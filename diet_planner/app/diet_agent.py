"""Agent diety: dobiera dania na tydzień (AI wybiera, kod liczy).

Alergie i „nie lubi” filtruje kod (twarde ograniczenie). Model dostaje tylko
dozwolonych kandydatów, a jego wybór jest sprawdzany komórka po komórce;
błędne pozycje uzupełnia zwykły generator. Porcje, kcal i zakupy liczy planner.
"""
import datetime

import ai
import nutrition
import planner
import storage

PREF_SECTIONS = ("Cel", "Styl żywienia", "Preferencje żywnościowe", "Zwyczaje", "Uwagi dla planisty")
SEASON = {12: "zima", 1: "zima", 2: "zima", 3: "wiosna", 4: "wiosna", 5: "wiosna",
          6: "lato", 7: "lato", 8: "lato", 9: "jesień", 10: "jesień", 11: "jesień"}

SYSTEM = """Jesteś agentem diety w domowej aplikacji. Układasz jadłospis na tydzień dla domowników,
wybierając dania WYŁĄCZNIE z podanej listy kandydatów (po identyfikatorach).
Porcje i kalorie przeliczy aplikacja – Ty dbasz o dobór dań:
- urozmaicenie (różne źródła białka, warzywa, kasze/ryż/makaron/ziemniaki; to samo danie najwyżej 2 razy w tygodniu poza meal prepem),
- preferencje i życzenia domowników, oceny dań (dodatnie częściej, ujemne rzadko), sezon,
- czas gotowania: w dni robocze krótsze dania, dłuższe w weekend,
- dania z ostatnich tygodni rzadziej, przepisy z książek użytkownika chętnie, gdy pasują,
- białko: przy celu „masa” lub treningach wybieraj dania z większą ilością białka.
- obiady na zapas (meal prep): najlepiej dania „wspólny garnek” albo „w sztukach” – łatwo je podzielić między osoby.
Pisz uzasadnienia krótko, po polsku, konkretnie (bez ogólników)."""


def _history(week, n=3):
    counts = {}
    for w in [x for x in storage.list_weeks() if x < week][-n:]:
        p = storage.load_plan(w) or {}
        for d in p.get("days", []):
            for m in d.get("meals", []):
                if m.get("recipe"):
                    counts[m["recipe"]] = counts.get(m["recipe"], 0) + 1
    return counts


def rating_score(r):
    rt = r.get("rating") or {}
    return int(rt.get("up", 0)) - int(rt.get("down", 0))


def candidates(slots, ppl, prods, recipes):
    excl = planner.exclusions(ppl)
    out = {}
    for s in {x["slot"] for x in slots}:
        out[s] = [r for r in recipes if s in r["slots"] and planner.recipe_ok(r, excl, prods)
                  and rating_score(r) > -2]
    return out, excl


def _describe(r, hist, prods):
    f = r.get("nutrition") or nutrition.recipe_facts(r, prods)
    pp = f["per_portion"]
    bits = [r["id"], r["name"], f"{r['time']} min", f"{pp['kcal']} kcal/B{round(pp['protein'])}"]
    if r.get("meal_prep"):
        bits.append("na zapas")
    pp = nutrition.prep_plan_of(r)
    if pp["components"]:
        bits.append(f"wykończenie {pp['on_day']['minutes']} min, etapy na zapas: "
                    + ", ".join(c["name"] + (" (mrozi się)" if c["freezes"] else "") for c in pp["components"]))
    sv = nutrition.serving_of(r)
    bits.append({"shared": "wspólny garnek", "individual": "każdy osobno"}.get(sv["mode"])
                or f"w sztukach ({sv['unit']})")
    if (r.get("source") or {}).get("book"):
        bits.append(f"książka: {r['source']['book']}")
    sc = rating_score(r)
    if sc:
        bits.append(f"ocena {sc:+d}")
    if hist.get(r["id"]):
        bits.append(f"ostatnio {hist[r['id']]}×")
    return " | ".join(bits)


def _prefs(person):
    lines = []
    for k, v in person["fields"].items():
        if v and k.split(" / ")[0] in PREF_SECTIONS:
            lines.append(f"  - {k.split(' / ', 1)[-1]}: {v}")
    return "\n".join(lines) or "  - (brak wpisanych preferencji)"


def build_prompt(week, wishes, ppl, st, slots, cands, hist, prods, prep, batches, weekly=False, prep_limit=0):
    dates = planner.week_dates(week)
    sf = st["fields"]
    parts = [f"Tydzień {week}: {dates[0].isoformat()} – {dates[-1].isoformat()}, sezon: {SEASON[dates[0].month]}."]
    parts.append("Dni: " + ", ".join(f"{i}={planner.DAY_NAMES[i]}" for i in range(7)) + ".")
    parts.append("Posiłki (slot = indeks): " + ", ".join(
        f"{i}={s['name']} ({planner.SLOT_LABEL[s['slot']]}, {round(s['share'] * 100)}% kcal{', ' + s['time'] if s['time'] else ''})"
        for i, s in enumerate(slots)) + ".")
    parts.append(f"Gotowanie: dni robocze {sf.get(planner.S['weekday']) or '30 min'}, weekend {sf.get(planner.S['weekend']) or '60 min'}.")
    if prep:
        parts.append("Meal prep: obiady gotujemy na zapas w partiach dni " +
                     ", ".join("+".join(str(d) for d in b) for b in batches) +
                     ". W obiedzie wybierz TO SAMO danie dla wszystkich dni partii, najlepiej z oznaczeniem „na zapas”.")
    if weekly:
        parts.append("MEAL PREP NA CAŁY TYDZIEŃ: w niedzielę przygotowujemy z wyprzedzeniem wszystko, co się da "
                     "(bazy, sosy, pieczone mięso, pokrojone warzywa), a w dniu posiłku zostaje krótkie wykończenie. "
                     "Wybieraj dania z krótkim wykończeniem (zwłaszcza pn–pt), dania, których etapy dobrze się mrożą, "
                     "na czwartek–niedzielę, oraz wspólne bazy (np. ten sam ryż lub kasza w dwóch daniach). "
                     "Ryby najlepiej na początek tygodnia albo na świeżo. Niedzielna sesja ma być realna (ok. 2–3 h): "
                     "unikaj dań z bardzo długimi etapami (wyrastanie ciasta, pieczywo, dania powyżej 60 min przygotowań)."
                     + (f" Limit niedzielnej sesji: {planner.fmt_minutes(prep_limit)} – całe przygotowania z "
                        "wyprzedzeniem muszą się w nim zmieścić (garnki i piekarnik pracują w tle)." if prep_limit else ""))
    for p in ppl:
        t = planner.target(p)
        parts.append(f"{p['name']}: cel {t['kcal']} kcal/dzień.\n{_prefs(p)}")
    parts.append("Życzenia na ten tydzień: " + (wishes.strip() or "(brak)"))
    for s, lst in cands.items():
        parts.append(f"Kandydaci – {planner.SLOT_LABEL[s]} (id | nazwa | czas | kcal porcji bazowej/białko | uwagi):\n" +
                     "\n".join("- " + _describe(r, hist, prods) for r in lst))
    parts.append("Zwróć dla każdego dnia (0–6) wybór dania dla każdego slotu oraz jedno zdanie uzasadnienia dnia, "
                 "a także 2–4 zdania podsumowania tygodnia.")
    return "\n\n".join(parts)


def _schema(n_slots):
    return {"type": "object", "additionalProperties": False, "required": ["summary", "days"],
            "properties": {
                "summary": {"type": "string"},
                "days": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False, "required": ["day", "note", "meals"],
                    "properties": {
                        "day": {"type": "integer"}, "note": {"type": "string"},
                        "meals": {"type": "array", "items": {
                            "type": "object", "additionalProperties": False, "required": ["slot", "recipe"],
                            "properties": {"slot": {"type": "integer"}, "recipe": {"type": "string"}}}}}}}}}


def merge_menu(answer, slots, cands, fallback, batches, prep):
    """Łączy odpowiedź modelu z menu zapasowym. Zwraca (menu, liczba poprawek, notatki dni)."""
    allowed = {s: {r["id"] for r in lst} for s, lst in cands.items()}
    menu = [list(row) for row in fallback]
    fixed, notes = 0, {}
    chosen = {}
    for d in answer.get("days", []):
        di = d.get("day")
        if not isinstance(di, int) or not 0 <= di < 7:
            continue
        notes[di] = (d.get("note") or "").strip()
        for m in d.get("meals", []):
            si = m.get("slot")
            if isinstance(si, int) and 0 <= si < len(slots):
                chosen[(di, si)] = m.get("recipe")
    for di in range(7):
        for si, s in enumerate(slots):
            rid = chosen.get((di, si))
            if rid in allowed.get(s["slot"], set()):
                menu[di][si] = rid
            else:
                fixed += 1
    if prep:  # obiad: to samo danie w całej partii (bierzemy wybór z pierwszego dnia)
        for si, s in enumerate(slots):
            if s["slot"] != "obiad":
                continue
            for b in batches:
                for d in b[1:]:
                    menu[d][si] = menu[b[0]][si]
    return menu, fixed, notes


def generate(week, wishes="", seed=None, prep_mode=None, prep_limit=None):
    ppl, st = planner.people(), planner.settings()
    slots = planner.meal_slots(st)
    prods = nutrition.products_map()
    recipes = storage.recipes()
    cands, excl = candidates(slots, ppl, prods, recipes)
    hist = _history(week)
    prep_mode = prep_mode if prep_mode in planner.PREP_MODES else planner.prep_mode_default(st)
    prep = prep_mode != "none"
    batches = [[0, 1], [2, 3], [4, 5], [6]] if prep else [[d] for d in range(7)]

    limit = prep_limit if prep_limit in planner.PREP_LIMITS else planner.DEFAULT_PREP_LIMIT
    base = planner.generate(week, seed=seed, prep_mode=prep_mode, prep_limit=limit)  # zapasowe menu z kodu
    fallback = [[m["recipe"] for m in d["meals"]] for d in base["days"]]

    model = ai.config()["model"]
    prompt = build_prompt(week, wishes or "", ppl, st, slots, cands, hist, prods, prep, batches,
                          weekly=prep_mode == "weekly", prep_limit=limit)
    answer = ai.ask_json([{"type": "text", "text": prompt}], _schema(len(slots)), purpose="agent diety",
                         model=model, effort="medium", max_tokens=16000, system=SYSTEM)
    menu, fixed, notes = merge_menu(answer, slots, cands, fallback, batches, prep)
    plan = planner.generate(week, menu=menu, prep_mode=prep_mode, prep_limit=limit)  # kod pilnuje limitu
    plan["agent"] = {"model": model, "summary": (answer.get("summary") or "").strip(),
                     "notes": {str(k): v for k, v in notes.items() if v}, "wishes": wishes or "",
                     "replaced": fixed, "at": datetime.datetime.now().isoformat(timespec="seconds")}
    if fixed:
        plan["warnings"].append(f"Agent diety podał {fixed} niepasujących pozycji – uzupełnił je zwykły generator.")
    return plan


def rate(recipe_id, delta, user=None):
    with storage.LOCK:
        recipes = storage.recipes()
        r = next((x for x in recipes if x["id"] == recipe_id), None)
        if r is None:
            raise ValueError("Nie ma takiego przepisu")
        rt = r.setdefault("rating", {"up": 0, "down": 0})
        rt["up" if delta > 0 else "down"] += 1
        rt["last"] = {"by": user, "at": datetime.datetime.now().isoformat(timespec="seconds"), "delta": 1 if delta > 0 else -1}
        storage.save_recipes(recipes)
        return r
