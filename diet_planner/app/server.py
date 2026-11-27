"""Serwer aplikacji Diet Planner.

Lokalnie:        python3 app/server.py --dev [--port 8099] [--open]
W dodatku HA:    DATA_DIR=/config python3 app/server.py --addon
"""
import argparse
import errno
import json
import mimetypes
import re
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP = Path(__file__).resolve().parent
sys.path.insert(0, str(APP))
import ai  # noqa: E402
import books  # noqa: E402
import diet_agent  # noqa: E402
import instructions_job  # noqa: E402
import mdfields  # noqa: E402
import nutrition  # noqa: E402
import planner  # noqa: E402
import quick  # noqa: E402
import recipe_agent  # noqa: E402
import storage  # noqa: E402
import translate_job  # noqa: E402

STATIC = APP / "static"
INGRESS_IPS = {"172.30.32.2", "127.0.0.1", "::1"}
ADDON = False
SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text):
    t = text.lower()
    for a, b in zip("ąćęłńóśźż", "acelnoszz"):
        t = t.replace(a, b)
    return SLUG_RE.sub("_", t).strip("_")[:40] or "przepis"


def ai_info():
    """Stan AI dla interfejsu: dostawcy, przypisania zadań, katalog modeli, zużycie (bez kluczy)."""
    return {**ai.ai_status(), "usage": ai.usage(),
            "task_names": {t: v[0] for t, v in ai.TASKS.items()},
            "task_vision": [t for t, v in ai.TASKS.items() if v[2]],
            "catalog": [{"id": k, "label": v[0], "price_in": v[1], "price_out": v[2], "vision": v[3]}
                        for k, v in ai.MODELS.items()]}


def state():
    profiles = {}
    for pid, p in storage.profile_files().items():
        profiles[pid] = mdfields.public(mdfields.parse(p.read_text(encoding="utf-8")))
    ppl = planner.people()
    return {
        "profiles": profiles,
        "persons": [{"id": p["id"], "name": p["name"], "target": planner.target(p)} for p in ppl],
        "settings": mdfields.public(mdfields.parse(storage.settings_file().read_text(encoding="utf-8"))),
        "products": storage.products(),
        "recipes": storage.recipes(),
        "weeks": storage.list_weeks(),
        "current_week": planner.current_week(),
        "prep_mode_default": planner.prep_mode_default(planner.settings()),
        "exclusions": planner.exclusions(ppl),
        "ai": ai_info(),
        "inbox_count": len(recipe_agent.inbox()),
    }


def save_plan(plan):
    storage.save_plan(plan, planner.to_markdown(plan))
    return plan


class Handler(BaseHTTPRequestHandler):
    server_version = "DietPlanner/1.0"

    # --- pomocnicze ---
    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length > 2_000_000:
            raise ValueError("Za duże zapytanie")
        return json.loads(self.rfile.read(length) or b"{}")

    def _user(self):
        for h in ("X-Remote-User-Display-Name", "X-Remote-User-Name"):
            v = self.headers.get(h)
            if v:
                return v[:60]
        return None

    def _allowed(self):
        if ADDON and self.client_address[0] not in INGRESS_IPS:
            self._send(403, {"error": "Dostęp tylko przez Home Assistant"})
            return False
        return True

    def _path(self):
        return urllib.parse.urlparse(self.path).path.rstrip("/") or "/"

    # --- GET ---
    def do_GET(self):
        if not self._allowed():
            return
        p = self._path()
        try:
            if p in ("/", "/index.html"):
                return self._static("index.html")
            if p.startswith("/static/"):
                return self._static(p[len("/static/"):])
            if p == "/api/state":
                return self._send(200, state())
            if p == "/api/inbox":
                return self._send(200, {"items": recipe_agent.inbox()})
            if p == "/api/ai":
                return self._send(200, ai_info())
            if p == "/api/instructions":
                return self._send(200, instructions_job.status())
            if p == "/api/translate":
                return self._send(200, translate_job.status())
            if p == "/api/books":
                return self._send(200, {"books": books.list_books(), "ai": ai.ai_status()})
            m = re.match(r"^/api/plans/(\d{4}-W\d{2})(/export\.md)?$", p)
            if m:
                with storage.LOCK:
                    plan = storage.load_plan(m.group(1))
                if plan is None:
                    return self._send(404, {"error": "Brak planu na ten tydzień"})
                if m.group(2):
                    return self._send(200, planner.to_markdown(plan).encode("utf-8"), "text/markdown; charset=utf-8",
                                      {"Content-Disposition": f'attachment; filename="{m.group(1)}.md"'})
                return self._send(200, plan)
            self._send(404, {"error": "Nie znaleziono"})
        except Exception as e:  # noqa: BLE001
            self._send(500, {"error": str(e)})

    def _static(self, name):
        f = (STATIC / name).resolve()
        if STATIC not in f.parents or not f.is_file():
            return self._send(404, {"error": "Nie znaleziono"})
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        self._send(200, f.read_bytes(), ctype)

    # --- POST ---
    def do_POST(self):
        if not self._allowed():
            return
        p = self._path()
        try:
            if p == "/api/books/upload":
                name = urllib.parse.unquote(self.headers.get("X-Filename", ""))
                saved = books.save_upload(name, self.rfile, int(self.headers.get("Content-Length", 0)))
                return self._send(200, {"file": saved, "books": books.list_books()})
            body = self._body()
            # Zapytania do AI trwają długo – wykonujemy je poza blokadą zapisu.
            if p == "/api/recipes/draft":
                drafts = recipe_agent.from_text(body.get("text"), {"book": (body.get("source") or "").strip()[:100]})
                recipe_agent.add_to_inbox(drafts)
                return self._send(200, {"drafts": drafts, "items": recipe_agent.inbox()})
            m = re.match(r"^/api/plans/(\d{4}-W\d{2})/generate$", p)
            if m and body.get("mode") == "agent":
                plan = diet_agent.generate(m.group(1), body.get("wishes") or "", body.get("seed"), body.get("prep_mode"),
                                           _limit(body))
                with storage.LOCK:
                    return self._send(200, save_plan(plan))
            m = re.match(r"^/api/books/(estimate|import|cancel|delete)$", p)
            if m:
                return self._send(200, self._books(m.group(1), body))
            if p == "/api/ai/test":  # krótkie zapytanie do modelu (poza blokadą zapisu)
                return self._send(200, ai.test_model(body.get("model") or ""))
            if p == "/api/ai/models":
                ai.save_assignments(body.get("tasks") or {})
                return self._send(200, ai_info())
            if p == "/api/products/fiber":
                n = nutrition.fill_fiber()
                return self._send(200, {"filled": n, "products": storage.products(), "recipes": storage.recipes()})
            m = re.match(r"^/api/translate/(estimate|start|cancel)$", p)
            if m:
                choice = "main" if body.get("model") == "main" else "import"
                fn = {"estimate": translate_job.estimate, "start": translate_job.start}.get(m.group(1))
                return self._send(200, fn(choice) if fn else translate_job.cancel())
            m = re.match(r"^/api/instructions/(estimate|start|cancel)$", p)
            if m:
                choice = "main" if body.get("model") == "main" else "import"
                fn = {"estimate": instructions_job.estimate, "start": instructions_job.start}.get(m.group(1))
                return self._send(200, fn(choice) if fn else instructions_job.cancel())
            with storage.LOCK:
                m = re.match(r"^/api/inbox/(accept|accept_clean|reject|update)$", p)
                if m:
                    return self._send(200, self._inbox(m.group(1), body))
                if p == "/api/recipes/rate":
                    r = diet_agent.rate(body.get("id"), int(body.get("delta") or 1), self._user())
                    return self._send(200, {"recipe": r, "recipes": storage.recipes()})
                m = re.match(r"^/api/profiles/([a-z0-9_-]+)$", p)
                if m:
                    f = storage.profile_files().get(m.group(1))
                    if not f:
                        return self._send(404, {"error": "Nieznany profil"})
                    changed = mdfields.save_file(f, body.get("fields") or {})
                    return self._send(200, {"changed": changed, "state": state()})
                if p == "/api/settings":
                    changed = mdfields.save_file(storage.settings_file(), body.get("fields") or {}, body.get("meals"))
                    return self._send(200, {"changed": changed, "state": state()})
                if p == "/api/recipes":
                    return self._send(200, self._save_recipe(body))
                if p == "/api/recipes/delete":
                    items = [r for r in storage.recipes() if r["id"] != body.get("id")]
                    storage.save_recipes(items)
                    nutrition.write_markdown(items)
                    return self._send(200, {"recipes": items})
                if p == "/api/products":
                    return self._send(200, self._save_product(body))
                m = re.match(r"^/api/products/(rename|merge|delete)$", p)
                if m:
                    return self._send(200, self._product_action(m.group(1), body))
                m = re.match(r"^/api/plans/(\d{4}-W\d{2})/(generate|swap|shopping|delete|quick|invent|refresh)$", p)
                if m:
                    return self._plan_action(m.group(1), m.group(2), body)
            self._send(404, {"error": "Nie znaleziono"})
        except ValueError as e:
            self._send(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            self._send(500, {"error": str(e)})

    def _books(self, action, body):
        name = body.get("file") or ""
        if action == "estimate":
            return {"estimate": books.estimate(name), "books": books.list_books()}
        if action == "import":
            books.start_import(name)
        elif action == "cancel":
            books.cancel()
        else:
            books.delete_book(name)
        return {"books": books.list_books()}

    def _inbox(self, action, body):
        if action == "accept":
            rec = recipe_agent.accept(body.get("id"), body.get("recipe"))
            return {"recipe": rec, "items": recipe_agent.inbox(), "recipes": storage.recipes(),
                    "products": storage.products()}
        if action == "accept_clean":
            n = len(recipe_agent.accept_clean())
            return {"accepted": n, "items": recipe_agent.inbox(), "recipes": storage.recipes(),
                    "products": storage.products()}
        if action == "reject":
            recipe_agent.reject(body.get("id"))
        else:
            recipe_agent.update(body.get("id"), body.get("recipe") or {})
        return {"items": recipe_agent.inbox()}

    def _plan_action(self, week, action, body):
        if action == "generate":
            plan = planner.generate(week, seed=body.get("seed"), prep_mode=body.get("prep_mode"),
                                    prep_limit=_limit(body))
            return self._send(200, save_plan(plan))
        if action == "delete":
            for ext in (".json", ".md"):
                f = storage.path("plans", week + ext)
                if f.exists():
                    f.unlink()
            return self._send(200, {"ok": True})
        plan = storage.load_plan(week)
        if plan is None:
            return self._send(404, {"error": "Brak planu na ten tydzień"})
        if action in ("quick", "invent"):
            return self._send(200, self._quick(plan, action, body))
        if action == "refresh":
            return self._send(200, save_plan(planner.refresh(plan)))
        if action == "swap":
            plan = planner.swap(plan, int(body["day"]), int(body["slot"]), body.get("recipe"), body.get("seed"))
        else:
            if body.get("op") == "prep_toggle":
                plan = planner.prep_toggle(plan, body.get("id"), body.get("checked"))
            else:
                plan = planner.shopping_op(plan, body.get("op"), body, self._user())
        return self._send(200, save_plan(plan))

    def _quick(self, plan, action, body):
        slot = int(body["slot"])
        if not 0 <= slot < len(plan["slots"]):
            raise ValueError("Nieprawidłowy posiłek")
        products = storage.products()
        items = body.get("items")
        if items is None:
            items = quick.split_text(body.get("text"))
        found, unknown = quick.normalize([str(i) for i in items][:100], products)
        if action == "invent":
            return {"draft": quick.invent(plan, slot, found, unknown)}
        if body.get("image"):
            known, other = quick.recognize(body["image"], body.get("text"), products)
            found = list(dict.fromkeys(found + known))
            unknown = list(dict.fromkeys(unknown + other))
        return {"products": found, "unknown": unknown,
                "suggestions": quick.suggest(plan, slot, found), "ai": quick.ai_status()}

    def _save_recipe(self, r):
        name = (r.get("name") or "").strip()
        ings = [{"product": i["product"], "grams": float(i["grams"])}
                for i in r.get("ingredients", []) if i.get("product") and float(i.get("grams") or 0) > 0]
        if not name or not ings:
            raise ValueError("Przepis musi mieć nazwę i co najmniej jeden składnik")
        known = storage.known_names()
        missing = [i["product"] for i in ings if i["product"].strip().lower() not in known]
        if missing:
            raise ValueError("Nieznane produkty: " + ", ".join(missing) + " – dodaj je najpierw do bazy")
        slots = [s for s in r.get("slots", []) if s in planner.SLOT_LABEL] or ["obiad"]
        items = storage.recipes()
        rid = r.get("id") or slugify(name)
        if not r.get("id"):
            base, n = rid, 2
            while any(x["id"] == rid for x in items):
                rid, n = f"{base}_{n}", n + 1
        rec = {"id": rid, "name": name, "slots": slots, "time": int(float(r.get("time") or 15)),
               "meal_prep": bool(r.get("meal_prep")), "ingredients": ings, "custom": True}
        rec.update(nutrition.instructions(r))
        sv = nutrition.clean_serving(r.get("serving") or {})
        if sv:
            rec["serving"] = sv
        prev = next((x for x in items if x["id"] == rid), {})
        if "prep_notes" not in r and "prep_notes" not in prev and not rec["prep_notes"]:
            del rec["prep_notes"]  # stary przepis bez rozpisanych instrukcji – zostaje do przepisania przez AI
        if "serving" not in rec and prev.get("serving"):
            rec["serving"] = prev["serving"]
        for k in ("source", "rating", "lang", "translated_from", "original_name"):
            if prev.get(k):
                rec[k] = prev[k]
        rec["nutrition"] = nutrition.recipe_facts(rec)
        items = [x for x in items if x["id"] != rid] + [rec]
        storage.save_recipes(items)
        nutrition.write_markdown(items)
        return {"recipe": rec, "recipes": items}

    def _save_product(self, p):
        name = (p.get("name") or "").strip().lower()
        if not name:
            raise ValueError("Podaj nazwę produktu")
        try:
            vals = {k: float(str(p.get(k, "")).replace(",", ".")) for k in ("kcal", "protein", "carbs", "fat")}
            fib = str(p.get("fiber", "") or "").replace(",", ".").strip()
            if fib:
                vals["fiber"] = float(fib)
        except ValueError:
            raise ValueError("Wartości odżywcze muszą być liczbami")
        old = (p.get("old_name") or "").strip()
        if old and old != name:  # edycja z nową nazwą – przepisy idą za produktem
            name = storage.rename_product(old, name)
        prev = next((x for x in storage.products() if x["name"] == name), {})
        prod = {**{k: prev[k] for k in ("unit", "piece", "piece_name") if k in prev},
                "name": name, **vals, "dept": p.get("dept") or "Inne",
                "tags": [t for t in p.get("tags", []) if t in ("gluten", "laktoza", "jaja", "ryby", "orzechy", "mięso")],
                "custom": True}
        items = [x if x["name"] != name else prod for x in storage.products()]
        if not prev:
            items.append(prod)
        storage.save_products(items)
        recipes = nutrition.refresh_all()  # zmiana produktu zmienia kaloryczność przepisów
        return {"product": prod, "products": items, "recipes": recipes}

    def _product_action(self, action, body):
        if action == "rename":
            name = storage.rename_product(body.get("old"), body.get("new"))
        elif action == "merge":
            name = storage.merge_products(body.get("src"), body.get("dst"))
        else:
            storage.delete_product(body.get("name"))
            name = None
        recipes = nutrition.refresh_all()  # scalenie zmienia kaloryczność przepisów
        return {"product": name, "products": storage.products(), "recipes": recipes}

    def log_message(self, fmt, *args):
        sys.stderr.write("[diet-planner] " + fmt % args + "\n")


def _limit(body):
    try:
        return int(body.get("prep_limit"))
    except (TypeError, ValueError):
        return None


def already_running(url):
    try:
        with urllib.request.urlopen(url + "/api/state", timeout=2) as r:
            return "profiles" in json.loads(r.read())
    except Exception:  # noqa: BLE001
        return False


def main():
    global ADDON
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--open", action="store_true", help="otwórz przeglądarkę")
    ap.add_argument("--dev", action="store_true", help="tryb lokalny (127.0.0.1)")
    ap.add_argument("--addon", action="store_true", help="tryb dodatku HA (tylko Ingress)")
    args = ap.parse_args()
    ADDON = args.addon
    storage.ensure_seed()
    books.recover()
    nutrition.refresh_all()
    host = "0.0.0.0" if args.addon else "127.0.0.1"
    url = f"http://127.0.0.1:{args.port}"
    try:
        server = ThreadingHTTPServer((host, args.port), Handler)
    except OSError as e:
        if e.errno != errno.EADDRINUSE:
            raise
        if already_running(url):
            print(f"Diet Planner już działa: {url}", flush=True)
            if args.open:
                webbrowser.open(url)
            return
        sys.exit(f"Port {args.port} jest zajęty przez inny program. Użyj np. --port 8100")
    print(f"Diet Planner: {url}  (dane: {storage.DATA})", flush=True)
    if args.open:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
