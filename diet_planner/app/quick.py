"""Szybki zamiennik: dobór dań z tego, co jest w domu (tekst i/lub zdjęcie).

Dopasowanie do bazy przepisów działa bez AI. Rozpoznawanie produktów ze
zdjęcia i proponowanie nowych dań używa Claude API (wymaga klucza).
"""
import json
import os
import re
from pathlib import Path

import ai
import nutrition
import planner
import recipe_agent
import storage

STAPLES = {"oliwa", "olej rzepakowy", "miód"}  # zakładamy, że są w domu


# --- Konfiguracja (wspólna z agentami – patrz ai.py) ------------------------------

from ai import ai_status, config  # noqa: E402,F401


# --- Dopasowanie tekstu do produktów ------------------------------------------

def _stem(word):
    return word[:max(4, len(word) - 2)]


def normalize(items, products):
    """Zamienia wpisane nazwy na produkty z bazy. Zwraca (znalezione, nierozpoznane)."""
    found, unknown = [], []
    names = [p["name"] for p in products]
    for raw in items:
        tok = raw.strip().lower()
        if not tok:
            continue
        if tok in names:
            found.append(tok)
            continue
        words = [w for w in re.findall(r"[a-ząćęłńóśźż]+", tok) if len(w) >= 4]
        hits = [n for n in names
                if any(any(pw.startswith(_stem(w)) for pw in re.findall(r"[a-ząćęłńóśźż]+", n)) for w in words)]
        if hits:
            found.extend(h for h in hits if h not in found)
        else:
            unknown.append(raw.strip())
    return list(dict.fromkeys(found)), unknown


def split_text(text):
    return [t for t in re.split(r"[,;\n]| i | oraz ", text or "") if t.strip()]


def suggest(plan, slot, owned, limit=6):
    """Ranking przepisów dla danego posiłku wg pokrycia składników."""
    s = plan["slots"][slot]
    prods = {p["name"]: p for p in storage.products()}
    excl = planner.exclusions(planner.people())
    owned = set(owned)
    out = []
    for r in storage.recipes():
        if s["slot"] not in r["slots"] or not planner.recipe_ok(r, excl, prods):
            continue
        need = [i["product"] for i in r["ingredients"] if i["product"] not in STAPLES]
        have = [n for n in need if n in owned]
        if not have:
            continue
        missing = [n for n in need if n not in owned]
        out.append({"recipe": r["id"], "name": r["name"], "time": r["time"],
                    "have": have, "missing": missing, "coverage": len(have) / len(need)})
    out.sort(key=lambda x: (-x["coverage"], len(x["missing"]), x["time"]))
    return out[:limit]


# --- Claude API --------------------------------------------------------------------

def _ask_json(content, schema, max_tokens=4000):
    return ai.ask_json(content, schema, purpose="szybki zamiennik", max_tokens=max_tokens)


def recognize(image_data_url, text, products):
    """Rozpoznaje produkty na zdjęciu (i w tekście), mapując je na nazwy z bazy."""
    m = re.match(r"^data:(image/(?:jpeg|png|webp|gif));base64,(.+)$", image_data_url or "", re.S)
    if not m:
        raise ValueError("Nieobsługiwany format zdjęcia.")
    data = m.group(2)
    if len(data) > 7_000_000:
        raise ValueError("Zdjęcie jest za duże.")
    names = [p["name"] for p in products]
    prompt = (
        "Na zdjęciu jest zawartość lodówki lub szafki kuchennej. Wypisz produkty spożywcze, które widać "
        "na tyle wyraźnie, że da się je rozpoznać. Gdy produkt odpowiada pozycji z listy bazy, użyj "
        "dokładnie nazwy z listy w polu `known`. Produkty spoza listy wpisz w `other` po polsku. "
        "Nie zgaduj zawartości nieprzezroczystych pojemników.\n\n"
        f"Lista bazy: {', '.join(names)}\n\n"
        f"Użytkownik dopisał: {text or '(nic)'}"
    )
    schema = {"type": "object", "additionalProperties": False, "required": ["known", "other"],
              "properties": {"known": {"type": "array", "items": {"type": "string"}},
                             "other": {"type": "array", "items": {"type": "string"}}}}
    out = _ask_json([{"type": "image", "source": {"type": "base64", "media_type": m.group(1), "data": data}},
                     {"type": "text", "text": prompt}], schema)
    known = [k for k in out.get("known", []) if k in names]
    return known, [o for o in out.get("other", []) if o.strip()]


def invent(plan, slot, owned, extra):
    """Proponuje nowe danie z posiadanych produktów (tylko produkty z bazy)."""
    s = plan["slots"][slot]
    products = storage.products()
    excl = planner.exclusions(planner.people())
    allowed = [p["name"] for p in products
               if not set(p.get("tags", [])) & set(excl["tags"])
               and not any(w in p["name"].lower() for w in excl["words"])]
    prompt = (
        f"Zaproponuj jedno proste danie na posiłek „{s['name']}” ({planner.SLOT_LABEL[s['slot']]}) "
        "dla dwóch osób, polska kuchnia domowa. Używaj WYŁĄCZNIE produktów z listy dozwolonych, "
        "z dokładnie takimi nazwami. Najlepiej z produktów, które użytkownik ma; dodatkowe produkty "
        "ogranicz do minimum. Gramatury podaj dla jednej porcji ok. 500 kcal, w gramach "
        "(dla jajek: 55 g na sztukę).\n\n" + recipe_agent.INSTRUCTION_RULES + "\n\n"
        f"Użytkownik ma: {', '.join(owned + extra) or '(nic)'}\n"
        f"Dozwolone produkty: {', '.join(allowed)}"
    )
    schema = {"type": "object", "additionalProperties": False,
              "required": ["name", "time", "meal_prep", "ingredients", "prep_notes", "steps", "storage",
                           "serving_mode", "unit", "units_per_portion"],
              "properties": {
                  "name": {"type": "string"}, "time": {"type": "integer"}, "meal_prep": {"type": "boolean"},
                  "prep_notes": {"type": "array", "items": {"type": "string"}},
                  "steps": {"type": "array", "items": {"type": "string"}}, "storage": {"type": "string"},
                  **recipe_agent.SERVING_PROPS,
                  "ingredients": {"type": "array", "items": {
                      "type": "object", "additionalProperties": False, "required": ["product", "grams"],
                      "properties": {"product": {"type": "string"}, "grams": {"type": "number"}}}}}}
    out = _ask_json([{"type": "text", "text": prompt}], schema)
    ings = [{"product": i["product"], "grams": round(float(i["grams"]))}
            for i in out.get("ingredients", []) if i.get("product") in allowed and float(i.get("grams") or 0) > 0]
    if not ings:
        raise ValueError("Model nie zaproponował dania z dostępnych produktów – spróbuj ponownie.")
    return {"name": out["name"].strip()[:80], "slots": [s["slot"]], "time": max(5, min(180, int(out["time"]))),
            "meal_prep": bool(out.get("meal_prep")), "ingredients": ings,
            **nutrition.instructions({**out, "meal_prep": bool(out.get("meal_prep"))}),
            **({"serving": nutrition.clean_serving(out)} if nutrition.clean_serving(out) else {})}
