"""Tłumaczenie przepisów na polski (biblioteka i „Do przejrzenia”).

Dwa etapy, w tle (wątek), paczkami:
1. wykrywanie języka – tanio: nazwa, pierwszy krok i składniki; przy pierwszej paczce także nazwy
   produktów z bazy (obcojęzyczne → propozycje polskiej nazwy);
2. tłumaczenie – nazwa, uwagi, kroki, przechowywanie, porcjowanie i etapy meal prepu.
Nazw własnych potraw (Pad thai, Bulgogi, Gazpacho…) nie tłumaczymy.

W przepisie zostaje `lang`: brak = nie sprawdzony, "pl" = po polsku, inny kod = do przetłumaczenia
(`title_foreign` = obcy tytuł). Po tłumaczeniu: `lang` = "pl", `translated_from` i `original_name`.
Gdy model odda obcy tytuł bez zmian, przepis dostaje `title_kept` – widać go w Ustawieniach i można go
przetłumaczyć ponownie (np. dokładniejszym modelem) albo zostawić.
Obcojęzyczne nazwy produktów nie są zmieniane same – trafiają do propozycji (`product_suggestions`)
do zatwierdzenia w zakładce Produkty.
Postęp: library/translate_job.json. Przy zerwanym połączeniu (ai.TransientError) zadanie się wstrzymuje.
"""
import datetime
import threading

import ai
import nutrition
import recipe_agent
import storage

TASK = "recipe_translate"
DETECT_BATCH = 40
TRANSLATE_BATCH = 8

_lock = threading.RLock()
_job = {"thread": None, "cancel": False}

PROPER_NAMES = ("Nie tłumacz nazw własnych potraw i słów, które po polsku funkcjonują w oryginale "
                "(np. Pad thai, Bulgogi, Gazpacho, Mango lassi, Bunbao, Donburi, Huajuan, Banoffee, curry, miso, "
                "tofu, mozzarella, feta, skyr, tortilla, wrap, passata, hummus, risotto, pesto, bowl, smoothie).")
KEEP_PRODUCTS = ("tortilla", "passata", "wrap", "pita", "tofu", "tempeh", "skyr", "feta", "mozzarella", "ricotta",
                 "mascarpone", "parmezan", "hummus", "pesto", "curry", "miso", "tahini", "granola", "musli", "noodle")

DETECT_SYSTEM = f"""Jesteś redaktorem polskiej aplikacji z przepisami. Dla każdego przepisu ustal, w jakim języku jest
nazwa i treść. Treść po polsku z pojedynczymi zapożyczeniami to nadal polski.
- `lang`: kod ISO 639-1 głównego języka treści (np. pl, en, de, it); gdy treść po polsku, a tylko nazwa w innym
  języku – podaj język nazwy.
- `title_foreign`: true, gdy nazwa (poza nazwami własnymi) jest w innym języku niż polski,
  np. „Sticky Chinese boczniak belly”, „Zero butter no chicken”, „Mango sticky rice”.
- `body_foreign`: true, gdy kroki lub składniki są w innym języku niż polski.
{PROPER_NAMES}
Zwróć wynik dla KAŻDEGO przepisu, z jego `id` bez zmian.
Jeśli podano listę produktów: w `products` zwróć tylko nazwy napisane w CAŁOŚCI w obcym języku, które w polskim
sklepie mają zwykłą polską nazwę (np. „chicken breast” → „pierś z kurczaka”, „baking powder” → „proszek do pieczenia”).
NIE zwracaj nazw polskich ani zapożyczeń używanych w Polsce (np. tortilla pełnoziarnista, passata pomidorowa,
tofu naturalne, mozzarella, skyr, pasta miso). Polską nazwę pisz małymi literami, krótko i ogólnie.
W razie wątpliwości – pomiń produkt."""

TRANSLATE_SYSTEM = f"""Jesteś tłumaczem kulinarnym. Przetłumacz przepisy na naturalny, poprawny polski
(tryb rozkazujący w krokach, np. „Pokrój cebulę…”). {PROPER_NAMES}
Zasady:
- Nie zmieniaj dania, liczb, temperatur, czasów ani kolejności kroków. Liczba elementów każdej listy ma zostać taka sama.
- Teksty, które już są po polsku, przepisz bez zmian.
- Nazwa: krótka, po polsku, z wielkiej litery. Gdy przy przepisie jest „TYTUŁ DO PRZETŁUMACZENIA”, MUSISZ zwrócić
  polski tytuł – inny niż oryginał. Nazwę własną potrawy zostaw, ale słowa opisowe przetłumacz, np.
  „Peanut butter chicken” → „Kurczak w sosie z masła orzechowego”, „Sticky Chinese boczniak belly” → „Kleiste
  boczniaki po chińsku”, „Zero butter no chicken” → „Wegański butter chicken bez masła”, „Tea ceremony” →
  „Herbaciana ceremonia”, „Mango sticky rice” → „Kleisty ryż z mango”. Tylko gdy cały tytuł to nazwa własna
  (np. „Banoffee”, „Pad thai”), zostaw go bez zmian.
- `components` – w tej samej kolejności co w wejściu.
Zwróć wynik dla KAŻDEGO przepisu, z jego `id` bez zmian."""


def _path():
    return storage.path("library", "translate_job.json")


def _state():
    return storage.read_json(_path(), {"status": "idle"})


def _update(**kw):
    with _lock:
        s = _state()
        s.update(kw)
        storage.write_json(_path(), s)
        return s


def _model(choice):
    return ai.resolve("diet" if choice == "main" else TASK)[0]


def _items():
    """[(rodzaj, id, przepis z polem id)] – biblioteka i szkice."""
    out = [("recipe", r["id"], r) for r in storage.recipes()]
    out += [("draft", d["id"], {**d["recipe"], "id": d["id"]}) for d in recipe_agent.inbox()]
    return out


def pending(skipped=()):
    """(do wykrycia, do przetłumaczenia)"""
    skip = set(skipped)
    items = [x for x in _items() if x[1] not in skip]
    return ([x for x in items if not x[2].get("lang")],
            [x for x in items if x[2].get("lang") and x[2]["lang"] != "pl"])


def status():
    _mark_legacy()
    s = _state()
    running = bool(_job["thread"] and _job["thread"].is_alive())
    if s.get("status") == "running" and not running:
        s["status"] = "paused"
    det, tr = pending(s.get("skipped", []) if running else ())
    return {**s, "running": running, "pending": len(det) + len(tr), "to_detect": len(det), "to_translate": len(tr),
            "kept_titles": kept_titles(), "product_suggestions": suggestions(),
            "models": {"import": ai.label(_model("import")), "main": ai.label(_model("main"))}}


# --- Zapytania do modelu ---------------------------------------------------------------

def detect_prompt(recipes, products=None):
    parts = []
    for r in recipes:
        steps = nutrition.steps_list(r)
        parts.append(f"id: {r['id']}\nnazwa: {r['name']}\nskładniki: {', '.join(i['product'] for i in r.get('ingredients', []))}"
                     f"\npierwszy krok: {steps[0] if steps else '(brak)'}")
    out = "Przepisy:\n\n" + "\n\n---\n\n".join(parts)
    if products:
        out += "\n\nProdukty z bazy:\n" + "\n".join(f"- {n}" for n in products)
    return out


def detect(recipes, products=None, model=None):
    """({id: {lang, title_foreign, body_foreign}}, {obca nazwa produktu: polska nazwa})"""
    item = {"type": "object", "additionalProperties": False, "required": ["id", "lang", "title_foreign", "body_foreign"],
            "properties": {"id": {"type": "string"}, "lang": {"type": "string"},
                           "title_foreign": {"type": "boolean"}, "body_foreign": {"type": "boolean"}}}
    prod = {"type": "object", "additionalProperties": False, "required": ["name", "polish"],
            "properties": {"name": {"type": "string"}, "polish": {"type": "string"}}}
    schema = {"type": "object", "additionalProperties": False, "required": ["recipes", "products"],
              "properties": {"recipes": {"type": "array", "items": item}, "products": {"type": "array", "items": prod}}}
    out = ai.ask_json([{"type": "text", "text": detect_prompt(recipes, products)}], schema, purpose="wykrywanie języka",
                      task=TASK, model=model, effort="low", max_tokens=16000, system=DETECT_SYSTEM)
    ids = {r["id"] for r in recipes}
    found = {x["id"]: x for x in out.get("recipes", []) if x.get("id") in ids}
    known = set(products or [])
    renames = {}
    for p in out.get("products", []):
        old, new = (p.get("name") or "").strip(), (p.get("polish") or "").strip().lower()[:60]
        if old in known and new and new != old.lower():
            renames[old] = new
    return found, renames


def _source_text(r):
    pp = r.get("prep_plan") or {}
    comps = pp.get("components") or []
    lines = [f"id: {r['id']}", f"nazwa: {r['name']}" + ("  ← TYTUŁ DO PRZETŁUMACZENIA" if r.get("title_foreign") else "")]
    if r.get("prep_notes"):
        lines.append("przed gotowaniem:\n" + "\n".join(f"- {x}" for x in r["prep_notes"]))
    lines.append("kroki:\n" + "\n".join(f"- {x}" for x in nutrition.steps_list(r)))
    if r.get("storage"):
        lines.append(f"przechowywanie: {r['storage']}")
    unit = (r.get("serving") or {}).get("unit")
    if unit:
        lines.append(f"jednostka porcji: {unit}")
    for k, c in enumerate(comps):
        lines.append(f"etap {k + 1}: {c.get('name', '')}\n" + "\n".join(f"- {x}" for x in c.get("steps", [])))
    if (pp.get("on_day") or {}).get("steps"):
        lines.append("w dniu podania:\n" + "\n".join(f"- {x}" for x in pp["on_day"]["steps"]))
    return "\n".join(lines)


def translate(recipes, model=None):
    """{id: pola do podmiany} – tylko teksty; składniki i kalorie bez zmian."""
    strs = {"type": "array", "items": {"type": "string"}}
    comp = {"type": "object", "additionalProperties": False, "required": ["name", "steps"],
            "properties": {"name": {"type": "string"}, "steps": strs}}
    item = {"type": "object", "additionalProperties": False,
            "required": ["id", "name", "prep_notes", "steps", "storage", "serving_unit", "components", "on_day_steps"],
            "properties": {"id": {"type": "string"}, "name": {"type": "string"}, "prep_notes": strs, "steps": strs,
                           "storage": {"type": "string"}, "serving_unit": {"type": "string"},
                           "components": {"type": "array", "items": comp}, "on_day_steps": strs}}
    schema = {"type": "object", "additionalProperties": False, "required": ["recipes"],
              "properties": {"recipes": {"type": "array", "items": item}}}
    text = "Przepisy do przetłumaczenia:\n\n" + "\n\n---\n\n".join(_source_text(r) for r in recipes)
    out = ai.ask_json([{"type": "text", "text": text}], schema, purpose="tłumaczenie przepisów", task=TASK,
                      model=model, effort="low", max_tokens=32000, system=TRANSLATE_SYSTEM)
    by_id = {r["id"]: r for r in recipes}
    result = {}
    for it in out.get("recipes", []):
        r = by_id.get(it.get("id"))
        if not r:
            continue
        name = (it.get("name") or "").strip()[:100]
        steps = [s.strip() for s in it.get("steps") or [] if s.strip()]
        if not name or (nutrition.steps_list(r) and not steps):
            continue
        res = {"name": name, "steps": steps, "lang": "pl", "translated_from": r.get("lang") or "?",
               "title_foreign": None, "title_kept": None}
        if name.casefold() != r["name"].casefold():
            res["original_name"] = r.get("original_name") or r["name"]
        elif r.get("title_foreign"):
            res["title_kept"] = True  # model zostawił obcy tytuł – do ponowienia albo ręcznej zmiany
        if "prep_notes" in r:
            res["prep_notes"] = [s.strip() for s in it.get("prep_notes") or [] if s.strip()]
        if r.get("storage"):
            res["storage"] = (it.get("storage") or "").strip() or r["storage"]
        sv = r.get("serving")
        if sv and sv.get("unit") and (it.get("serving_unit") or "").strip():
            res["serving"] = {**sv, "unit": it["serving_unit"].strip()}
        pp = r.get("prep_plan")
        if pp:
            pp = {**pp, "components": [dict(c) for c in pp.get("components") or []],
                  "on_day": dict(pp.get("on_day") or {})}
            comps = it.get("components") or []
            if len(comps) == len(pp["components"]):
                for c, t in zip(pp["components"], comps):
                    c["name"] = (t.get("name") or "").strip() or c.get("name")
                    if len(t.get("steps") or []) == len(c.get("steps") or []):
                        c["steps"] = t["steps"]
            ods = it.get("on_day_steps") or []
            if pp["on_day"].get("steps") and len(ods) == len(pp["on_day"]["steps"]):
                pp["on_day"]["steps"] = ods
            res["prep_plan"] = pp
        result[r["id"]] = res
    return result


# --- Zapis -------------------------------------------------------------------------------

def _merge(r, ch):
    """Jak dict.update, ale None usuwa klucz."""
    for k, v in ch.items():
        if v is None:
            r.pop(k, None)
        else:
            r[k] = v


def _apply(changes):
    """{id: pola} → biblioteka i szkice."""
    if not changes:
        return
    with storage.LOCK:
        recipes = storage.recipes()
        hit = [r for r in recipes if r["id"] in changes]
        for r in hit:
            _merge(r, changes[r["id"]])
        if hit:
            storage.save_recipes(recipes)
            nutrition.write_markdown(recipes)
        items = recipe_agent.inbox()
        dhit = [d for d in items if d["id"] in changes]
        for d in dhit:
            _merge(d["recipe"], changes[d["id"]])
        if dhit:
            recipe_agent.save_inbox(items)


def suggestions():
    """[{name, polish}] – propozycje polskich nazw produktów (tylko dla produktów, które nadal są w bazie)."""
    names = {p["name"] for p in storage.products()}
    return [x for x in _state().get("product_suggestions", []) if x["name"] in names]


def _suggest(renames):
    s = _state()
    cur = {x["name"]: x for x in s.get("product_suggestions", [])}
    for old, new in renames.items():
        if old.casefold() in KEEP_PRODUCTS or any(w in old.casefold().split() for w in KEEP_PRODUCTS):
            continue  # zapożyczenia używane po polsku – nie proponujemy zmiany
        if old not in set(s.get("dismissed_suggestions", [])):
            cur[old] = {"name": old, "polish": new}
    _update(product_suggestions=list(cur.values()))


def accept_suggestion(name, new=None):
    """Zmienia nazwę produktu na proponowaną (albo podaną) – przepisy idą za produktem."""
    sug = next((x for x in suggestions() if x["name"] == name), None)
    target = (new or (sug or {}).get("polish") or "").strip()
    if not target:
        raise ValueError("Brak propozycji dla tego produktu")
    result = storage.rename_product(name, target)
    s = _state()
    _update(product_suggestions=[x for x in s.get("product_suggestions", []) if x["name"] != name],
            renamed=(s.get("renamed", []) + [f"{name} → {result}"])[-200:])
    nutrition.refresh_all()
    return result


def reject_suggestion(name):
    s = _state()
    _update(product_suggestions=[x for x in s.get("product_suggestions", []) if x["name"] != name],
            dismissed_suggestions=list(dict.fromkeys(s.get("dismissed_suggestions", []) + [name])))


def kept_titles():
    return [{"id": r["id"], "name": r["name"]} for r in storage.recipes() if r.get("title_kept")]


def _mark_legacy():
    """Wersja 1.12.0 oznaczała przepis jako przetłumaczony, nawet gdy model zostawił obcy tytuł.
    Takie przepisy (przetłumaczone, bez `original_name`) dostają jednorazowo `title_kept`."""
    if _state().get("legacy_checked"):
        return
    changes = {r["id"]: {"title_kept": True} for r in storage.recipes()
               if r.get("translated_from") and not r.get("original_name") and not r.get("title_kept")}
    _apply(changes)
    _update(legacy_checked=True)


def retry_kept(ids=None):
    """Ponownie kolejkuje przepisy z pozostawionym obcym tytułem."""
    with storage.LOCK:
        want = set(ids or [])
        changes = {r["id"]: {"lang": r.get("translated_from") if r.get("translated_from") not in (None, "?") else "xx",
                             "title_foreign": True, "title_kept": None}
                   for r in storage.recipes() if r.get("title_kept") and (not want or r["id"] in want)}
        _apply(changes)
    return status()


# --- Sterowanie zadaniem -----------------------------------------------------------------

def estimate(choice="import"):
    det, tr = pending()
    model = _model(choice)
    tin = (sum(len(detect_prompt([r for _, _, r in det[i:i + DETECT_BATCH]])) / 3.2 + 800
               for i in range(0, len(det), DETECT_BATCH)) +
           sum(len(_source_text(r)) / 3.2 + 600 for _, _, r in tr))
    tout = len(det) * 30 + len(tr) * 500
    # wykryte obcojęzyczne przepisy też trzeba będzie przetłumaczyć – zakładamy ok. 1/3
    tin += len(det) / 3 * 900
    tout += len(det) / 3 * 500
    if not _state().get("products_checked"):
        n = len(storage.products())
        tin += n * 6
        tout += 200
    p_in, p_out = ai.price(model) or (0, 0)
    est = {"recipes": len(det) + len(tr), "model": model, "choice": choice,
           "tokens_in": round(tin), "tokens_out": round(tout),
           "usd": round(tin / 1e6 * p_in + tout / 1e6 * p_out, 3)}
    _update(estimate=est)
    return status()


def start(choice="import"):
    with _lock:
        if _job["thread"] and _job["thread"].is_alive():
            raise ValueError("Tłumaczenie już trwa.")
        st = ai.ai_status(TASK)
        if not st["available"]:
            raise ValueError(st["reason"])
        det, tr = pending()
        if not det and not tr and _state().get("products_checked"):
            raise ValueError("Wszystkie przepisy są już po polsku.")
        storage.backup("diet.before-translation.db")
        s = _state()
        resumed = s.get("status") in ("paused", "running")
        _update(status="running", model=_model(choice), choice=choice, skipped=[],
                done=s.get("done", 0) if resumed else 0, checked=s.get("checked", 0) if resumed else 0,
                renamed=s.get("renamed", []),
                errors=s.get("errors", []) if resumed else [],
                started=datetime.datetime.now().isoformat(timespec="seconds"))
        _job["cancel"] = False
        t = threading.Thread(target=_run, args=(_model(choice),), daemon=True)
        _job["thread"] = t
        t.start()
    return status()


def cancel():
    _job["cancel"] = True
    return status()


def _error(msg):
    s = _state()
    _update(errors=(s.get("errors", []) + [msg])[-20:])


def step(model, skipped):
    """Jedna paczka. Zwraca listę id, których nie udało się obsłużyć, albo None, gdy nie ma już nic do zrobienia."""
    det, tr = pending(skipped)
    s = _state()
    if det or not s.get("products_checked"):
        batch = det[:DETECT_BATCH]
        names = None if s.get("products_checked") else [p["name"] for p in storage.products()]
        found, renames = detect([r for _, _, r in batch], names, model=model)
        if names is not None:
            _suggest(renames)
            _update(products_checked=True)
        changes = {}
        for _, rid, _r in batch:
            f = found.get(rid)
            if f:
                foreign = f.get("title_foreign") or f.get("body_foreign")
                lang = (f.get("lang") or "").strip().lower()[:5]
                changes[rid] = {"lang": (lang if lang and lang != "pl" else "xx") if foreign else "pl",
                                "title_foreign": True if f.get("title_foreign") else None}
        _apply(changes)
        _update(checked=_state().get("checked", 0) + len(changes))
        return [rid for _, rid, _ in batch if rid not in changes]
    if not tr:
        return None
    batch = tr[:TRANSLATE_BATCH]
    res = translate([r for _, _, r in batch], model=model)
    _apply(res)
    _update(done=_state().get("done", 0) + len(res))
    return [rid for _, rid, _ in batch if rid not in res]


def _run(model):
    skipped = []
    try:
        while True:
            if _job["cancel"]:
                _update(status="paused")
                return
            try:
                missing = step(model, skipped)
            except ai.TransientError as e:
                _update(status="paused")
                _error(f"{e} Wstrzymano – kliknij „Wznów”.")
                return
            except ValueError as e:  # odmowa / za długa odpowiedź – pomijamy paczkę w tym przebiegu
                det, tr = pending(skipped)
                if det:
                    batch = det[:DETECT_BATCH]
                elif not _state().get("products_checked"):
                    batch = []
                    _update(products_checked=True)  # błąd przy samych nazwach produktów – nie ponawiamy
                else:
                    batch = tr[:TRANSLATE_BATCH]
                missing = [rid for _, rid, _ in batch]
                _error(f"{', '.join(r['name'] for _, _, r in batch)[:300] or 'Nazwy produktów'}: {e}")
            if missing is None:
                _update(status="done", skipped=skipped, finished=datetime.datetime.now().isoformat(timespec="seconds"))
                return
            skipped += missing
            _update(skipped=skipped)
    except Exception as e:  # noqa: BLE001
        _update(status="paused")
        _error(f"Przerwano: {e} – kliknij „Wznów”.")
    finally:
        _job["thread"] = None
