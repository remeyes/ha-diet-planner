"""Serwis kaloryczności – jedyne miejsce, w którym liczone są kcal i makroskładniki.

Korzystają z niego: generator planu, agent przepisów, agent diety, interfejs,
eksport Markdown i skill Claude (przez plik library/nutrition.md).
"""
import datetime
import re

import storage

KEYS = ("kcal", "protein", "carbs", "fat")


def products_map():
    return {p["name"]: p for p in storage.products()}


def nutrition(ingr, prods):
    """[kcal, białko, węglowodany, tłuszcze] dla słownika {produkt: gramy}."""
    t = [0.0, 0.0, 0.0, 0.0]
    for name, g in ingr.items():
        p = prods.get(name)
        if not p:
            continue
        for i, k in enumerate(KEYS):
            t[i] += float(p.get(k) or 0) * g / 100
    return [round(x, 1) for x in t]


def fiber(ingr, prods):
    """(błonnik w g, produkty bez danych o błonniku) dla słownika {produkt: gramy}."""
    total, missing = 0.0, []
    for name, g in ingr.items():
        p = prods.get(name) or {}
        if p.get("fiber") is None:
            missing.append(name)
        else:
            total += float(p["fiber"]) * g / 100
    return round(total, 1), sorted(missing)


def rnd(prod, g):
    piece = prod.get("piece")
    if piece:
        return max(1, round(g / piece)) * piece
    if g < 25:
        return max(1, round(g))
    return int(5 * round(g / 5))


def amount_text(prod, g):
    if prod.get("piece"):
        return f"{round(g / prod['piece'])} {prod.get('piece_name', 'szt.')}"
    unit = prod.get("unit", "g")
    if g >= 1000:
        return f"{g / 1000:.2f}".rstrip("0").rstrip(".").replace(".", ",") + (" l" if unit == "ml" else " kg")
    return f"{round(g)} {unit}"


def ingredients_map(recipe):
    out = {}
    for i in recipe.get("ingredients", []):
        out[i["product"]] = out.get(i["product"], 0) + float(i["grams"])
    return out


def text_list(v, limit=20, size=400):
    """Lista tekstów z listy albo z tekstu (jedna pozycja w linii); puste pozycje odpadają."""
    items = v if isinstance(v, list) else str(v or "").splitlines()
    out = []
    for x in items:
        x = re.sub(r"^\s*(?:\d+[.)]|[-•*])\s*", "", str(x or "")).strip()
        if x:
            out.append(x[:size])
    return out[:limit]


def steps_list(recipe):
    """Kroki przygotowania jako lista. Stary format (jeden tekst) dzielimy na zdania."""
    v = recipe.get("steps")
    if isinstance(v, list):
        return text_list(v, 30)
    text = str(v or "").strip()
    if "\n" in text:
        return text_list(text, 30)
    parts = re.split(r"(?<=[.!?])\s+(?=[A-ZĄĆĘŁŃÓŚŹŻ0-9])", text)
    return [x.strip() for x in parts if x.strip()]


def instructions(recipe):
    """Ujednolicone instrukcje przepisu: przed gotowaniem, kroki, przechowywanie."""
    return {"prep_notes": text_list(recipe.get("prep_notes"), 6), "steps": steps_list(recipe),
            "storage": str(recipe.get("storage") or "").strip()[:600] if recipe.get("meal_prep") else ""}


def needs_rewrite(recipe):
    """Przepis ma jeszcze stary, skrótowy opis (bez rozpisanych kroków)."""
    return not isinstance(recipe.get("steps"), list) or "prep_notes" not in recipe


SERVING_MODES = ("shared", "pieces", "individual")
_SHARED_WORDS = ("zupa", "krem z", "gulasz", "zapiekank", "curry", "chili", "makaron", "spaghetti", "pęczotto",
                 "risotto", "leczo", "potrawk", "gołąbk", "bigos", "fasolka po", "duszon", "w sosie")


def serving_of(recipe):
    """Sposób porcjowania: shared (garnek dzielony na części), pieces (sztuki), individual (każdy osobno).
    Gdy przepis nie ma pola `serving`, zgadujemy po nazwie i oznaczamy `auto`."""
    sv = recipe.get("serving") or {}
    mode = sv.get("mode")
    if mode in SERVING_MODES:
        try:
            upp = float(sv.get("units_per_portion") or 0)
        except (TypeError, ValueError):
            upp = 0
        if mode == "pieces" and (upp <= 0 or not (sv.get("unit") or "").strip()):
            mode = "shared"
        out = {"mode": mode, "auto": False}
        if mode == "pieces":
            out.update(unit=sv["unit"].strip()[:30], units_per_portion=upp)
        return out
    name = (recipe.get("name") or "").lower()
    slots = set(recipe.get("slots") or [])
    if any(w in name for w in _SHARED_WORDS) or (recipe.get("meal_prep") and slots & {"obiad", "kolacja"}):
        return {"mode": "shared", "auto": True}
    return {"mode": "individual", "auto": True}


def needs_serving(recipe):
    return (recipe.get("serving") or {}).get("mode") not in SERVING_MODES


def clean_serving(raw):
    """Normalizuje pole `serving` z odpowiedzi modelu albo edytora."""
    mode = (raw or {}).get("mode") or (raw or {}).get("serving_mode")
    if mode not in SERVING_MODES:
        return None
    out = {"mode": mode}
    if mode == "pieces":
        try:
            upp = round(float((raw or {}).get("units_per_portion") or 0), 2)
        except (TypeError, ValueError):
            upp = 0
        unit = str((raw or {}).get("unit") or "").strip()[:30]
        if upp <= 0 or not unit:
            return {"mode": "shared"}
        out.update(unit=unit, units_per_portion=upp)
    return out


EQUIPMENT = ("piekarnik", "garnek", "patelnia", "blender", "bez obróbki")


def _int(v, lo, hi, default):
    try:
        return max(lo, min(hi, int(round(float(v)))))
    except (TypeError, ValueError):
        return default


def clean_prep_plan(raw, recipe):
    """Normalizuje rozbicie przepisu na etapy meal prepu (z modelu albo edytora). None, gdy nic sensownego."""
    if not isinstance(raw, dict):
        return None
    names = {i["product"] for i in recipe.get("ingredients", [])}
    used, comps = set(), []
    for c in raw.get("components") or []:
        prods = []
        for p in c.get("products") or []:
            if p in names and p not in used and p not in prods:
                prods.append(p)
        steps = text_list(c.get("steps"), 12)
        if not prods or not steps:
            continue
        used.update(prods)
        comps.append({"name": str(c.get("name") or prods[0]).strip()[:60], "products": prods, "steps": steps,
                      "minutes": _int(c.get("minutes"), 1, 240, 15),
                      "equipment": c.get("equipment") if c.get("equipment") in EQUIPMENT else "bez obróbki",
                      "fridge_days": _int(c.get("fridge_days"), 1, 5, 3), "freezes": bool(c.get("freezes"))})
    od = raw.get("on_day") or {}
    on_day = {"minutes": _int(od.get("minutes"), 0, 120, 10), "steps": text_list(od.get("steps"), 12)}
    if not comps and not on_day["steps"]:
        return None
    return {"components": comps, "on_day": on_day}


def prep_plan_of(recipe):
    """Etapy meal prepu przepisu. Bez rozbicia od agenta: danie „na zapas” = jeden etap (lodówka 3 dni),
    inne – w całości na świeżo. Bez wiedzy o składnikach zakładamy ostrożnie 2 dni w lodówce. Klucz `auto` = zgadnięte."""
    pp = clean_prep_plan(recipe.get("prep_plan"), recipe)
    if pp:
        return {**pp, "auto": False}
    prods = [i["product"] for i in recipe.get("ingredients", [])]
    steps = steps_list(recipe)
    if recipe.get("meal_prep"):
        return {"components": [{"name": recipe.get("name", "Danie")[:60], "products": prods, "steps": steps,
                                 "minutes": int(recipe.get("time") or 30), "equipment": "garnek",
                                 "fridge_days": 2, "freezes": False}],
                "on_day": {"minutes": 5, "steps": ["Odgrzej porcję i podawaj."]}, "auto": True}
    return {"components": [], "on_day": {"minutes": int(recipe.get("time") or 15), "steps": steps}, "auto": True}


def needs_prep_plan(recipe):
    return clean_prep_plan(recipe.get("prep_plan"), recipe) is None


def recipe_facts(recipe, prods=None):
    """Opis kaloryczności przepisu (porcja bazowa) – zapisywany w polu `nutrition` przepisu."""
    prods = prods or products_map()
    ingr = ingredients_map(recipe)
    kcal, b, w, t = nutrition(ingr, prods)
    grams = round(sum(ingr.values()))
    fib, fib_missing = fiber(ingr, prods)
    per100 = [round(x * 100 / grams, 1) if grams else 0 for x in (kcal, b, w, t, fib)]
    energy = (b * 4 + w * 4 + t * 9) or 1
    return {
        "per_portion": {"kcal": round(kcal), "protein": round(b, 1), "carbs": round(w, 1), "fat": round(t, 1),
                        "fiber": fib},
        "per_100g": dict(zip(KEYS + ("fiber",), per100)),
        "fiber_missing": fib_missing,
        "grams": grams,
        "energy_split": {"protein": round(b * 4 / energy * 100), "carbs": round(w * 4 / energy * 100),
                         "fat": round(t * 9 / energy * 100)},
        "estimated_products": sorted(k for k in ingr if prods.get(k, {}).get("estimated")),
        "unknown_products": sorted(k for k in ingr if k not in prods),
    }


def refresh_all():
    """Przelicza `nutrition` wszystkich przepisów i zapisuje library/nutrition.md."""
    prods = products_map()
    recipes = storage.recipes()
    for r in recipes:
        r["nutrition"] = recipe_facts(r, prods)
    storage.save_recipes(recipes)
    write_markdown(recipes, prods)
    return recipes


def write_markdown(recipes=None, prods=None):
    prods = prods or products_map()
    recipes = recipes if recipes is not None else storage.recipes()
    slot = {"sniadanie": "śniadanie", "przekaska": "przekąska", "obiad": "obiad", "kolacja": "kolacja"}
    o = ["# Kaloryczność przepisów i produktów",
         "",
         f"Plik generowany automatycznie przez aplikację ({datetime.date.today().isoformat()}). "
         "Nie edytuj ręcznie – zmiany wprowadzaj w aplikacji (zakładka Przepisy).",
         "",
         "Wartości dla **porcji bazowej** przepisu (w planie porcje są skalowane do kcal każdej osoby). "
         "„szac.” = przepis zawiera produkty z szacowanymi wartościami.",
         "",
         "## Przepisy",
         "",
         "| Przepis | Posiłek | Porcja (g) | kcal | B (g) | W (g) | T (g) | Bł (g) | Źródło |",
         "|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(recipes, key=lambda x: x["name"]):
        f = r.get("nutrition") or recipe_facts(r, prods)
        pp = f["per_portion"]
        src = (r.get("source") or {}).get("book", "")
        est = " (szac.)" if f["estimated_products"] else ""
        o.append(f"| {r['name']}{est} | {', '.join(slot.get(s, s) for s in r['slots'])} | {f['grams']} | "
                 f"{pp['kcal']} | {pp['protein']} | {pp['carbs']} | {pp['fat']} | "
                 f"{'' if f.get('fiber_missing') or pp.get('fiber') is None else pp['fiber']} | {src} |")
    o += ["", "## Produkty (na 100 g)", "",
          "| Produkt | kcal | B | W | T | Bł | Dział | Uwagi |", "|---|---|---|---|---|---|---|---|"]
    for p in sorted(prods.values(), key=lambda x: x["name"]):
        note = "szacunek AI" if p.get("estimated") else ("błonnik: szacunek AI" if p.get("fiber_estimated") else "")
        fib = "" if p.get("fiber") is None else p["fiber"]
        o.append(f"| {p['name']} | {p['kcal']} | {p['protein']} | {p['carbs']} | {p['fat']} | {fib} | "
                 f"{p.get('dept', '')} | {note} |")
    storage.write_text(storage.path("library", "nutrition.md"), "\n".join(o) + "\n")


# --- Uzupełnianie błonnika (AI) ----------------------------------------------------

FIBER_SYSTEM = """Podajesz zawartość błonnika pokarmowego (g na 100 g produktu) dla produktów spożywczych,
w stanie, w jakim są ważone w przepisie (surowe/suche, chyba że nazwa mówi inaczej), według polskich tabel
wartości odżywczych (IŻŻ) lub USDA. Zwróć wynik dla KAŻDEGO produktu z listy, z nazwą przepisaną dokładnie."""


def fill_fiber(model=None):
    """Uzupełnia `fiber` produktom, które go nie mają (szacunek AI). Zwraca liczbę uzupełnionych."""
    import ai
    missing = [p["name"] for p in storage.products() if p.get("fiber") is None]
    if not missing:
        return 0
    schema = {"type": "object", "additionalProperties": False, "required": ["products"],
              "properties": {"products": {"type": "array", "items": {
                  "type": "object", "additionalProperties": False, "required": ["name", "fiber_100g"],
                  "properties": {"name": {"type": "string"}, "fiber_100g": {"type": "number"}}}}}}
    out = ai.ask_json([{"type": "text", "text": "Produkty:\n" + "\n".join(f"- {n}" for n in missing)}], schema,
                      purpose="błonnik produktów", model=model or ai.config()["import_model"], effort="low",
                      max_tokens=8000, system=FIBER_SYSTEM)
    vals = {}
    for item in out.get("products", []):
        try:
            v = float(item.get("fiber_100g"))
        except (TypeError, ValueError):
            continue
        if 0 <= v <= 90:
            vals[item.get("name")] = round(v, 1)
    with storage.LOCK:
        products = storage.products()
        n = 0
        for p in products:
            if p.get("fiber") is None and p["name"] in vals:
                p["fiber"], p["fiber_estimated"] = vals[p["name"]], True
                n += 1
        storage.save_products(products)
        refresh_all()
    return n
