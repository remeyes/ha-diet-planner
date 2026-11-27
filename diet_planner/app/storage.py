"""Katalog danych, dane startowe i bezpieczny zapis plików."""
import json
import os
import re
import shutil
import sqlite3
import threading
from contextlib import closing
from pathlib import Path

import db

APP = Path(__file__).resolve().parent
# Lokalnie dane leżą w katalogu projektu (te same pliki, których używa skill).
DATA = Path(os.environ.get("DATA_DIR", APP.parent)).resolve()
SEED = Path(os.environ.get("SEED_DIR", APP / "seed")).resolve()

LOCK = threading.RLock()
WEEK_RE = re.compile(r"^\d{4}-W\d{2}$")


def path(*parts):
    return DATA.joinpath(*parts)


def ensure_seed():
    """Kopiuje brakujące pliki z katalogu startowego (nie nadpisuje istniejących).
    Szablony profili trafiają tylko do pustej instalacji – nie dokładamy „Osoba 1/2” do istniejących osób."""
    if not SEED.exists():
        return
    has_profiles = any(path("profiles").glob("*.md")) if path("profiles").exists() else False
    for src in SEED.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(SEED)
        if rel.parts[0] == "profiles" and has_profiles:
            continue
        if rel.parts[0] == "library" and rel.name in LIB_JSON:
            continue  # produkty i przepisy startowe trafiają prosto do bazy (migrate)
        dst = DATA / rel
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
    path("plans").mkdir(parents=True, exist_ok=True)
    migrate()


def write_text(p, text):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)


def read_json(p, default=None):
    p = Path(p)
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


def write_json(p, obj):
    write_text(p, json.dumps(obj, ensure_ascii=False, indent=1))


# --- Biblioteka (SQLite: library/diet.db) ---------------------------------------------

LIB_JSON = ("products.json", "recipes.json")  # dawny format – tylko do migracji


def db_path():
    return path("library", "diet.db")


def _con():
    if not db_path().exists():
        migrate()
    con = db.connect(db_path())
    db.init(con)
    return con


def migrate():
    """Tworzy bazę przy pierwszym starcie: importuje library/*.json (albo dane startowe z seed),
    po czym zmienia nazwy plików JSON na *.json.migrated (kopia bezpieczeństwa)."""
    with LOCK:
        target = db_path()
        if target.exists():
            return False
        src = {}
        for name in LIB_JSON:
            for cand in (path("library", name), SEED / "library" / name):
                if cand.exists():
                    src[name] = cand
                    break
        prods = read_json(src["products.json"], []) if "products.json" in src else []
        recs = read_json(src["recipes.json"], []) if "recipes.json" in src else []
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name("diet.db.tmp")
        if tmp.exists():
            tmp.unlink()
        con = db.connect(tmp)
        try:
            db.init(con)
            known = {p["name"].strip().lower() for p in prods if p.get("name")}
            extra = []
            for r in recs:  # składnik bez produktu w bazie → produkt-zaślepka do uzupełnienia
                for i in r.get("ingredients", []):
                    n = (i.get("product") or "").strip().lower()
                    if n and n not in known:
                        known.add(n)
                        extra.append({"name": n, "kcal": 0, "protein": 0, "carbs": 0, "fat": 0, "dept": "Inne",
                                      "tags": [], "estimated": True, "custom": True, "placeholder": True})
            _write_products(con, prods + extra)
            _write_recipes(con, recs)
        finally:
            con.close()
        for suffix in ("-wal", "-shm"):
            side = tmp.with_name(tmp.name + suffix)
            if side.exists():
                side.unlink()
        os.replace(tmp, target)
        for name in LIB_JSON:
            p = path("library", name)
            if p.exists():
                os.replace(p, p.with_name(name + ".migrated"))
        return True


def _product_data(p):
    return {k: v for k, v in p.items() if k not in ("name", "id")}


def _write_products(con, items):
    with db.tx(con):
        existing = {r["name"].lower(): r["id"] for r in con.execute("SELECT id, name FROM products")}
        seen = {}
        for p in items:
            name = (p.get("name") or "").strip()
            if name:
                seen[name.lower()] = {**p, "name": name}
        for key, p in seen.items():
            d = _product_data(p)
            cols = (p["name"], d.get("dept"), db.num(d.get("kcal")), db.num(d.get("protein")), db.num(d.get("carbs")),
                    db.num(d.get("fat")), db.num(d.get("fiber")), db.dumps(d))
            if key in existing:
                con.execute("UPDATE products SET name=?, dept=?, kcal=?, protein=?, carbs=?, fat=?, fiber=?, data=? "
                            "WHERE id=?", cols + (existing[key],))
            else:
                con.execute("INSERT INTO products(name, dept, kcal, protein, carbs, fat, fiber, data) "
                            "VALUES (?,?,?,?,?,?,?,?)", cols)
        gone = [pid for key, pid in existing.items() if key not in seen]
        for pid in gone:
            used = _used_in(con, pid)
            if used:
                name = con.execute("SELECT name FROM products WHERE id=?", (pid,)).fetchone()["name"]
                raise ValueError(f"Produkt „{name}” jest używany w przepisach: {', '.join(used[:5])}"
                                 f"{'…' if len(used) > 5 else ''}")
            con.execute("DELETE FROM products WHERE id=?", (pid,))


def _used_in(con, pid):
    return [r["name"] for r in con.execute(
        "SELECT DISTINCT r.name FROM recipe_ingredients ri JOIN recipes r ON r.id = ri.recipe_id "
        "WHERE ri.product_id=? ORDER BY r.name", (pid,))]


def _name_ids(con):
    """{nazwa lub alias (małe litery): id produktu}"""
    ids = {r["alias"].lower(): r["product_id"] for r in con.execute("SELECT alias, product_id FROM product_aliases")}
    ids.update({r["name"].lower(): r["id"] for r in con.execute("SELECT id, name FROM products")})
    return ids


def _write_recipes(con, items):
    with db.tx(con):
        ids = _name_ids(con)
        missing = sorted({(i.get("product") or "").strip() for r in items for i in r.get("ingredients", [])
                          if (i.get("product") or "").strip().lower() not in ids})
        if missing:
            raise ValueError("Nieznane produkty: " + ", ".join(missing) + " – dodaj je najpierw do bazy")
        keep = []
        for pos, r in enumerate(items):
            rid = r["id"]
            keep.append(rid)
            data = {k: v for k, v in r.items() if k not in ("id", "name", "ingredients")}
            con.execute("INSERT INTO recipes(id, pos, name, lang, data) VALUES (?,?,?,?,?) "
                        "ON CONFLICT(id) DO UPDATE SET pos=excluded.pos, name=excluded.name, lang=excluded.lang, "
                        "data=excluded.data", (rid, pos, r.get("name") or "", data.get("lang"), db.dumps(data)))
            con.execute("DELETE FROM recipe_ingredients WHERE recipe_id=?", (rid,))
            con.executemany("INSERT INTO recipe_ingredients(recipe_id, position, product_id, grams) VALUES (?,?,?,?)",
                            [(rid, k, ids[i["product"].strip().lower()], float(i.get("grams") or 0))
                             for k, i in enumerate(r.get("ingredients", []))])
        old = [x["id"] for x in con.execute("SELECT id FROM recipes")]
        drop = [x for x in old if x not in set(keep)]
        con.executemany("DELETE FROM recipes WHERE id=?", [(x,) for x in drop])


def _g(v):
    return int(v) if float(v).is_integer() else v


def products():
    with closing(_con()) as con:
        return [{"name": r["name"], **json.loads(r["data"])}
                for r in con.execute("SELECT name, data FROM products ORDER BY id")]


def recipes():
    with closing(_con()) as con:
        ings = {}
        for r in con.execute("SELECT ri.recipe_id, p.name, ri.grams FROM recipe_ingredients ri "
                             "JOIN products p ON p.id = ri.product_id ORDER BY ri.recipe_id, ri.position"):
            ings.setdefault(r["recipe_id"], []).append({"product": r["name"], "grams": _g(r["grams"])})
        out = []
        for r in con.execute("SELECT id, name, data FROM recipes ORDER BY pos, rowid"):
            d = json.loads(r["data"])
            out.append({"id": r["id"], "name": r["name"], **d, "ingredients": ings.get(r["id"], [])})
        return out


def save_products(items):
    with LOCK, closing(_con()) as con:
        _write_products(con, items)


def save_recipes(items):
    with LOCK, closing(_con()) as con:
        _write_recipes(con, items)


def aliases():
    """{stara nazwa: obecna nazwa produktu}"""
    with closing(_con()) as con:
        return {r["alias"]: r["name"] for r in con.execute(
            "SELECT a.alias, p.name FROM product_aliases a JOIN products p ON p.id = a.product_id")}


def canonical(name):
    """Nazwa produktu w bazie dla nazwy lub aliasu (albo None)."""
    n = (name or "").strip()
    with closing(_con()) as con:
        r = con.execute("SELECT name FROM products WHERE name=?", (n,)).fetchone() or con.execute(
            "SELECT p.name FROM product_aliases a JOIN products p ON p.id = a.product_id WHERE a.alias=?",
            (n,)).fetchone()
        return r["name"] if r else None


def known_names():
    """Nazwy produktów i ich aliasy (małymi literami)."""
    with closing(_con()) as con:
        return set(_name_ids(con))


def product_usage():
    """{nazwa produktu: [{"id": przepis, "grams": g}]} – także produkty nieużywane (pusta lista)."""
    with closing(_con()) as con:
        out = {r["name"]: [] for r in con.execute("SELECT name FROM products ORDER BY id")}
        for r in con.execute("SELECT p.name, ri.recipe_id, SUM(ri.grams) AS g FROM recipe_ingredients ri "
                             "JOIN products p ON p.id = ri.product_id GROUP BY p.id, ri.recipe_id"):
            out[r["name"]].append({"id": r["recipe_id"], "grams": _g(r["g"])})
        return out


def recipes_with_products(names, mode="all"):
    """Id przepisów zawierających wszystkie (`all`) lub którykolwiek (`any`) z produktów;
    najpierw te z największą liczbą trafień."""
    with closing(_con()) as con:
        ids = _name_ids(con)
        pids = sorted({ids[n.strip().lower()] for n in names if n.strip().lower() in ids})
        if not pids:
            return []
        need = len(pids) if mode == "all" else 1
        q = ",".join("?" * len(pids))
        return [r["recipe_id"] for r in con.execute(
            f"SELECT recipe_id, COUNT(DISTINCT product_id) AS n FROM recipe_ingredients WHERE product_id IN ({q}) "
            "GROUP BY recipe_id HAVING n >= ? ORDER BY n DESC, recipe_id", (*pids, need))]


def _pid(con, name):
    r = con.execute("SELECT id, name FROM products WHERE name=?", ((name or "").strip(),)).fetchone()
    if not r:
        raise ValueError(f"Nie ma produktu „{name}”")
    return r["id"], r["name"]


def _rename_in_prep_plans(con, rids, old, new):
    """Nazwy produktów w etapach meal prepu (`prep_plan.components[].products`) to zwykłe teksty."""
    for rid in rids:
        row = con.execute("SELECT data FROM recipes WHERE id=?", (rid,)).fetchone()
        d = json.loads(row["data"])
        changed = False
        for c in (d.get("prep_plan") or {}).get("components") or []:
            prods = c.get("products") or []
            if old in prods:
                c["products"] = list(dict.fromkeys(new if x == old else x for x in prods))
                changed = True
        if changed:
            con.execute("UPDATE recipes SET data=? WHERE id=?", (db.dumps(d), rid))


def rename_product(old, new):
    """Zmienia nazwę produktu (przepisy wskazują go po id). Gdy `new` już istnieje – scala."""
    new = (new or "").strip().lower()
    if not new:
        raise ValueError("Podaj nową nazwę")
    with LOCK, closing(_con()) as con:
        pid, old_name = _pid(con, old)
        other = con.execute("SELECT id FROM products WHERE name=? AND id<>?", (new, pid)).fetchone()
    if other:
        return merge_products(old_name, new)
    with LOCK, closing(_con()) as con, db.tx(con):
        con.execute("UPDATE products SET name=? WHERE id=?", (new, pid))
        con.execute("DELETE FROM product_aliases WHERE alias=?", (new,))
        if old_name.lower() != new:
            con.execute("INSERT OR REPLACE INTO product_aliases(alias, product_id) VALUES (?,?)", (old_name, pid))
        rids = [r["recipe_id"] for r in con.execute(
            "SELECT DISTINCT recipe_id FROM recipe_ingredients WHERE product_id=?", (pid,))]
        _rename_in_prep_plans(con, rids, old_name, new)
    return new


def merge_products(src, dst):
    """Scala produkt `src` w `dst`: przepina składniki (sumując gramy, gdy przepis ma oba),
    zostawia starą nazwę jako alias i usuwa `src`. Przelicz potem kaloryczność (nutrition.refresh_all)."""
    with LOCK, closing(_con()) as con, db.tx(con):
        sid, sname = _pid(con, src)
        did, dname = _pid(con, dst)
        if sid == did:
            raise ValueError("Nie można scalić produktu z samym sobą")
        rows = con.execute("SELECT recipe_id, position, grams FROM recipe_ingredients WHERE product_id=?",
                           (sid,)).fetchall()
        for r in rows:
            both = con.execute("SELECT position FROM recipe_ingredients WHERE recipe_id=? AND product_id=? "
                               "ORDER BY position LIMIT 1", (r["recipe_id"], did)).fetchone()
            if both:
                con.execute("UPDATE recipe_ingredients SET grams = grams + ? WHERE recipe_id=? AND position=?",
                            (r["grams"], r["recipe_id"], both["position"]))
                con.execute("DELETE FROM recipe_ingredients WHERE recipe_id=? AND position=?",
                            (r["recipe_id"], r["position"]))
            else:
                con.execute("UPDATE recipe_ingredients SET product_id=? WHERE recipe_id=? AND position=?",
                            (did, r["recipe_id"], r["position"]))
        con.execute("UPDATE product_aliases SET product_id=? WHERE product_id=?", (did, sid))
        con.execute("DELETE FROM products WHERE id=?", (sid,))
        con.execute("INSERT OR REPLACE INTO product_aliases(alias, product_id) VALUES (?,?)", (sname, did))
        _rename_in_prep_plans(con, sorted({r["recipe_id"] for r in rows}), sname, dname)
    return dname


def delete_product(name):
    with LOCK, closing(_con()) as con, db.tx(con):
        pid, pname = _pid(con, name)
        used = _used_in(con, pid)
        if used:
            raise ValueError(f"Produkt „{pname}” jest używany w przepisach: {', '.join(used[:5])}"
                             f"{'…' if len(used) > 5 else ''} – usuń go z nich albo scal z innym produktem.")
        con.execute("DELETE FROM products WHERE id=?", (pid,))


def backup(name):
    """Kopia bazy obok niej (library/<name>), jeśli jeszcze nie istnieje."""
    dest = path("library", name)
    if dest.exists():
        return False
    with LOCK, closing(_con()) as con, closing(sqlite3.connect(str(dest))) as out:
        con.backup(out)
    return True


# --- Profile i ustawienia (Markdown) -------------------------------------------

def profile_files():
    """{id: ścieżka} dla wszystkich profili, w stałej kolejności."""
    d = path("profiles")
    return {p.stem: p for p in sorted(d.glob("*.md"))} if d.exists() else {}


def settings_file():
    return path("config", "diet-settings.md")


# --- Plany ---------------------------------------------------------------------

def plan_path(week):
    if not WEEK_RE.match(week):
        raise ValueError("Nieprawidłowy tydzień")
    return path("plans", f"{week}.json")


def list_weeks():
    d = path("plans")
    return sorted(p.stem for p in d.glob("*.json") if WEEK_RE.match(p.stem)) if d.exists() else []


def load_plan(week):
    return read_json(plan_path(week))


def save_plan(plan, markdown=None):
    write_json(plan_path(plan["week"]), plan)
    if markdown is not None:
        write_text(path("plans", f"{plan['week']}.md"), markdown)
