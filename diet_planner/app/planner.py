"""Generator jadłospisu, przeliczanie porcji i listy zakupów."""
import datetime
import itertools
import math
import random
import re

import mdfields
import storage
from nutrition import amount_text, instructions, nutrition, prep_plan_of, rnd, serving_of  # noqa: F401 (używane też przez inne moduły)

DAY_NAMES = ["Poniedziałek", "Wtorek", "Środa", "Czwartek", "Piątek", "Sobota", "Niedziela"]
DAY_SHORT = ["Pn", "Wt", "Śr", "Cz", "Pt", "So", "Nd"]
SLOT_LABEL = {"sniadanie": "Śniadanie", "przekaska": "Przekąska", "obiad": "Obiad", "kolacja": "Kolacja"}

K = {
    "sex": "Dane podstawowe / Płeć", "age": "Dane podstawowe / Wiek",
    "height": "Dane podstawowe / Wzrost (cm)", "weight": "Dane podstawowe / Aktualna waga (kg)",
    "activity": "Dane podstawowe / Poziom aktywności", "goal": "Cel / Cel główny",
    "kcal": "Zapotrzebowanie / Zapotrzebowanie kaloryczne (kcal/dzień)",
    "allergy": "Alergie i nietolerancje (TWARDE OGRANICZENIE) / Alergie",
    "intol": "Alergie i nietolerancje (TWARDE OGRANICZENIE) / Nietolerancje (np. laktoza, gluten)",
    "diet": "Styl żywienia / Rodzaj diety",
    "dislike": "Preferencje żywnościowe / Nie lubi / unika",
}
S = {
    "weekday": "Organizacja gotowania / Czas na gotowanie w dni robocze",
    "weekend": "Organizacja gotowania / Czas na gotowanie w weekend",
    "prep": "Organizacja gotowania / Meal prep / gotowanie na zapas",
}
ACTIVITY = [("bardzo", 1.9), ("wysok", 1.725), ("umiark", 1.55), ("lekk", 1.375), ("siedz", 1.2)]
ALLERGEN_WORDS = {
    "gluten": ["gluten", "pszen", "celiak"], "laktoza": ["laktoz", "nabiał", "nabial", "mlek", "mleko"],
    "jaja": ["jaj"], "ryby": ["ryb"], "orzechy": ["orzech"],
}
IGNORE_WORDS = {"", "brak", "nie", "nie ma", "todo", "żadne", "zadne", "-", "nic"}


def num(v):
    m = re.search(r"\d+(?:[.,]\d+)?", str(v or ""))
    return float(m.group().replace(",", ".")) if m else None


# --- Dane wejściowe ------------------------------------------------------------

def people():
    out = []
    for pid, p in storage.profile_files().items():
        parsed = mdfields.parse(p.read_text(encoding="utf-8"))
        name = parsed["title"].split(":", 1)[-1].strip() or pid.capitalize()
        out.append({"id": pid, "name": name,
                    "fields": {k: f["value"] for k, f in parsed["fields"].items()}})
    return out


def settings():
    parsed = mdfields.parse(storage.settings_file().read_text(encoding="utf-8"))
    return {"fields": {k: f["value"] for k, f in parsed["fields"].items()},
            "meals": (parsed["meals"] or {}).get("rows") or []}


def slot_for(name, share):
    n = name.lower()
    if "obiad" in n:
        return "obiad"
    if "kolac" in n:
        return "kolacja"
    if "śniad" in n and not n.startswith("ii") and "drugie" not in n:
        return "sniadanie"
    if any(w in n for w in ("ii ", "podwiecz", "przek", "lunch", "drugie")):
        return "przekaska"
    return "obiad" if share >= 20 else "przekaska"


def meal_slots(st):
    rows = st["meals"] or [
        {"name": "Śniadanie", "time": "07:30", "share": "25"}, {"name": "II śniadanie", "time": "10:30", "share": "10"},
        {"name": "Obiad", "time": "13:30", "share": "30"}, {"name": "Podwieczorek", "time": "16:30", "share": "10"},
        {"name": "Kolacja", "time": "19:30", "share": "25"}]
    slots = []
    for r in rows:
        share = num(r.get("share")) or 0
        time = r.get("time") if re.match(r"^\d{1,2}:\d{2}$", r.get("time") or "") else ""
        slots.append({"name": r["name"], "time": time, "share": share, "slot": slot_for(r["name"], share)})
    total = sum(s["share"] for s in slots) or 1
    for s in slots:
        s["share"] = s["share"] / total
    return slots


def sex_of(fields):
    s = (fields.get(K["sex"]) or "").strip().lower()
    if re.match(r"^(m|mężczyzna|facet)", s):
        return "M"
    if re.match(r"^(k|kobieta)", s):
        return "K"
    return None


DEFAULT_KCAL = {}  # opcjonalnie: {id_profilu: kcal} – domyślny cel, gdy profil nie ma danych


def target(person):
    f = person["fields"]
    sex = sex_of(f)
    minimum = 1500 if sex == "M" else 1200
    kcal = num(f.get(K["kcal"]))
    if kcal:
        return {"kcal": round(kcal), "source": "z profilu", "estimate": False, "min": minimum}
    age, h, w = num(f.get(K["age"])), num(f.get(K["height"])), num(f.get(K["weight"]))
    act = next((m for p, m in ACTIVITY if p in (f.get(K["activity"]) or "").lower()), None)
    goal = (f.get(K["goal"]) or "").lower()
    adj = -0.15 if "reduk" in goal else 0.075 if "mas" in goal else 0
    if sex and age and h and w and act:
        bmr = 10 * w + 6.25 * h - 5 * age + (5 if sex == "M" else -161)
        kcal = round(bmr * act * (1 + adj) / 10) * 10
        return {"kcal": kcal, "source": "szacunek (Mifflin–St Jeor)", "estimate": True, "min": minimum}
    base = DEFAULT_KCAL.get(person["id"]) or {"M": 2500, "K": 1900}.get(sex, 2200)
    return {"kcal": base, "source": "założenie domyślne – uzupełnij profil", "estimate": True, "min": minimum}


def exclusions(ppl):
    tags, words, notes = set(), set(), []
    for p in ppl:
        f = p["fields"]
        for key in ("allergy", "intol", "dislike"):
            for tok in re.split(r"[,;/\n]| i | oraz ", (f.get(K[key]) or "").lower()):
                tok = tok.strip(" .()")
                if tok in IGNORE_WORDS or len(tok) < 3:
                    continue
                hit = [t for t, ws in ALLERGEN_WORDS.items() if any(w in tok for w in ws)]
                tags.update(hit)
                words.add(tok[:-1] if len(tok) > 5 else tok)
                notes.append(f"{p['name']}: {tok}")
        diet = (f.get(K["diet"]) or "").lower()
        if "wegan" in diet:
            tags.update({"mięso", "ryby", "laktoza", "jaja"})
        elif "wegetaria" in diet:
            tags.update({"mięso", "ryby"})
        elif "peskat" in diet:
            tags.add("mięso")
        if "bez nabiał" in diet or "bez laktoz" in diet:
            tags.add("laktoza")
        if "bez gluten" in diet:
            tags.add("gluten")
        if diet and any(x in diet for x in ("wegan", "wegetaria", "peskat", "bez ")):
            notes.append(f"{p['name']}: dieta {diet}")
    return {"tags": sorted(tags), "words": sorted(words), "notes": notes}


def recipe_ok(r, excl, prods):
    for ing in r["ingredients"]:
        prod = prods.get(ing["product"], {})
        if set(prod.get("tags", [])) & set(excl["tags"]):
            return False
        name = ing["product"].lower()
        if any(w in name for w in excl["words"]):
            return False
    return True


# --- Porcje i wartości -----------------------------------------------------------

def portion(recipe, kcal, prods):
    base = {i["product"]: i["grams"] for i in recipe["ingredients"]}
    bk = nutrition(base, prods)[0] or 1
    f = kcal / bk
    return {k: rnd(prods.get(k, {}), g * f) for k, g in base.items()}


# --- Podział posiłku między osoby ----------------------------------------------------

MAX_PARTS = 5          # garnek dzielimy najwyżej na tyle równych części
COMP_MIN, COMP_MAX = 0.75, 1.3   # granice przeskalowania posiłków „każdy osobno” przy wyrównaniu dnia


def split_options(targets, limit=3):
    """Najlepsze podziały garnka na równe części (suma ≤ MAX_PARTS), od najbliższego proporcji kcal.
    Każda opcja to {osoba: liczba części}; podziały nieskrócone (2:2) pomijamy."""
    pids = list(targets)
    total = sum(targets.values())
    if not pids or len(pids) > MAX_PARTS or total <= 0:
        return []
    opts = []
    for combo in itertools.product(range(1, MAX_PARTS + 1), repeat=len(pids)):
        n = sum(combo)
        if n > MAX_PARTS or math.gcd(*combo) > 1:
            continue
        err = sum(abs(c / n - targets[p] / total) for c, p in zip(combo, pids))
        opts.append((round(err, 6), n, dict(zip(pids, combo))))
    return [o[2] for o in sorted(opts, key=lambda o: (o[0], o[1]))[:limit]]


def split_parts(targets):
    """Podział garnka najbliższy proporcji kcal (albo None, gdy się nie da)."""
    opts = split_options(targets, 1)
    return opts[0] if opts else None


def split_pieces(targets, unit_kcal):
    """Całkowita liczba sztuk dla osób (metoda największych reszt), co najmniej 1 na osobę."""
    total = sum(targets.values())
    units = max(len(targets), round(total / unit_kcal)) if unit_kcal > 0 else len(targets)
    ideal = {p: t / total * units for p, t in targets.items()}
    counts = {p: max(1, int(v)) for p, v in ideal.items()}
    while sum(counts.values()) < units:
        p = max(ideal, key=lambda x: ideal[x] - counts[x])
        counts[p] += 1
    while sum(counts.values()) > units:
        p = max((x for x in counts if counts[x] > 1), key=lambda x: counts[x] - ideal[x], default=None)
        if p is None:
            break
        counts[p] -= 1
    return counts


def _scaled(recipe, factor, prods):
    return {i["product"]: rnd(prods.get(i["product"], {}), i["grams"] * factor) for i in recipe["ingredients"]}


def _share_of(total, part):
    return {k: round(g * part, 1) for k, g in total.items()}


def assign_meal(m, recipe, kcal_targets, prods, parts=None):
    """Ustala porcje posiłku: wspólny garnek dzielony na części, sztuki albo każdy osobno.
    `parts` – wymuszony podział garnka (dobierany dla całego dnia w assign_day)."""
    m.pop("split", None)
    sv = serving_of(recipe)
    base_kcal = nutrition({i["product"]: i["grams"] for i in recipe["ingredients"]}, prods)[0] or 1
    total_kcal = sum(kcal_targets.values())
    if sv["mode"] == "pieces":
        unit_kcal = base_kcal / sv["units_per_portion"]
        counts = split_pieces(kcal_targets, unit_kcal)
        units = sum(counts.values())
        total = _scaled(recipe, units / sv["units_per_portion"], prods)
        m["portions"] = {p: _share_of(total, c / units) for p, c in counts.items()}
        m["split"] = {"mode": "pieces", "unit": sv["unit"], "counts": counts, "total_units": units,
                      "total": total, "auto": sv["auto"]}
        return
    parts = (parts or split_parts(kcal_targets)) if sv["mode"] == "shared" else None
    if parts:
        n = sum(parts.values())
        total = _scaled(recipe, total_kcal / base_kcal, prods)
        m["portions"] = {p: _share_of(total, c / n) for p, c in parts.items()}
        m["split"] = {"mode": "shared", "parts": parts, "total_parts": n, "total": total, "auto": sv["auto"],
                      "scale": total_kcal / base_kcal}
        return
    m["portions"] = {p: portion(recipe, k, prods) for p, k in kcal_targets.items()}


def assign_day(plan, d, lib, prods):
    """Porcje wszystkich posiłków dnia + wyrównanie kcal w posiłkach „każdy osobno”."""
    day = plan["days"][d]
    targets = {p: t["kcal"] for p, t in plan["targets"].items()}
    indiv = []
    for m in day["meals"]:
        r = lib.get(m["recipe"])
        if not r:
            m["portions"] = {}
            m.pop("split", None)
            continue
        share = plan["slots"][m["slot"]]["share"]
        assign_meal(m, r, {p: k * share for p, k in targets.items()}, prods)
        if "split" not in m:
            indiv.append((m, r, share))
    _balance_shared(plan, day, targets, lib, prods)
    _balance_pieces(plan, day, targets, lib, prods)
    _balance_shared(plan, day, targets, lib, prods)  # drugi raz – już z faktyczną liczbą sztuk
    if not indiv:
        return
    # Ile każdej osobie brakuje (albo zostaje) do celu dnia po posiłkach wspólnych? Wyrównujemy w posiłkach
    # „każdy osobno”. Zaokrąglenia (sztuki, 5 g) są skokowe, więc szukamy dla każdego posiłku najlepszego
    # mnożnika z siatki (dwa przejścia), zamiast jednego wspólnego.
    grid = [round(COMP_MIN + i * 0.025, 3) for i in range(int((COMP_MAX - COMP_MIN) / 0.025) + 1)]
    for p, k in targets.items():
        planned = sum(plan["slots"][m["slot"]]["share"] for m in day["meals"] if m["recipe"] in lib) * k
        fixed = sum(nutrition(m["portions"].get(p, {}), prods)[0] for m in day["meals"]
                    if m["recipe"] in lib and "split" in m)
        cache = {}

        def kcal_of(i, f):
            if (i, f) not in cache:
                m, r, share = indiv[i]
                por = portion(r, k * share * f, prods)
                cache[(i, f)] = (nutrition(por, prods)[0], por)
            return cache[(i, f)]
        chosen = [1.0] * len(indiv)
        for _ in range(2):
            for i in range(len(indiv)):
                rest = fixed + sum(kcal_of(j, chosen[j])[0] for j in range(len(indiv)) if j != i)
                chosen[i] = min(grid, key=lambda f: (abs(rest + kcal_of(i, f)[0] - planned), abs(f - 1)))
        for i, (m, _, _) in enumerate(indiv):
            m["portions"][p] = kcal_of(i, chosen[i])[1]


def _balance_shared(plan, day, targets, lib, prods):
    """Przy wspólnych garnkach w dniu dobiera ich podziały razem (np. obiad 3:2, kolacja 1:1) oraz – gdy trzeba –
    lekko zmienia wielkość garnków (±15%), żeby suma dnia każdej osoby była jak najbliżej celu.
    Resztę wyrównują posiłki „każdy osobno”."""
    shared = [m for m in day["meals"] if (m.get("split") or {}).get("mode") == "shared"]
    if not shared:
        return
    share = {id(m): plan["slots"][m["slot"]]["share"] for m in day["meals"]}
    opts, kcal_tot = [], {}
    for m in shared:
        opts.append(split_options({p: k * share[id(m)] for p, k in targets.items()}))
        kcal_tot[id(m)] = nutrition(m["split"]["total"], prods)[0]
    fixed = {p: 0.0 for p in targets}  # sztuki i posiłki „każdy osobno” (wg celu)
    for m in day["meals"]:
        if m["recipe"] not in lib or m in shared:
            continue
        for p, k in targets.items():
            fixed[p] += (nutrition(m["portions"].get(p, {}), prods)[0] if m.get("split")
                         else k * share[id(m)])
    planned = {p: k * sum(share[id(m)] for m in day["meals"] if m["recipe"] in lib) for p, k in targets.items()}
    grid = [round(0.85 + i * 0.025, 3) for i in range(13)]
    totals = {}  # (posiłek, mnożnik) -> (składniki całego garnka, kcal) – po zaokrągleniu do sztuk/5 g

    def pot(i, g):
        if (i, g) not in totals:
            m = shared[i]
            tot = _scaled(lib[m["recipe"]], m["split"]["scale"] * g, prods) if g != 1 else m["split"]["total"]
            totals[(i, g)] = (tot, nutrition(tot, prods)[0])
        return totals[(i, g)]

    def error(combo, gs):
        err = sum(abs(g - 1) * 0.04 for g in gs)  # bez potrzeby nie zmieniamy wielkości garnka
        for p in targets:
            got = fixed[p] + sum(pot(i, gs[i])[1] * c[p] / sum(c.values()) for i, c in enumerate(combo))
            err += abs(got - planned[p]) / planned[p]
        return err

    best = None
    for g in grid:  # wspólny mnożnik + wszystkie kombinacje podziałów
        gs = [g] * len(shared)
        for combo in itertools.product(*opts):
            rank = sum(opts[i].index(c) for i, c in enumerate(combo))  # przy remisie – podziały najbliższe proporcji
            key = (round(error(combo, gs), 4), rank)
            if best is None or key < best[0]:
                best = (key, combo, gs)
    _, combo, gs = best
    gs = list(gs)
    for _ in range(2):  # dopracowanie: osobny mnożnik dla każdego garnka (zaokrąglenia sztuk są skokowe)
        for i in range(len(shared)):
            gs[i] = min(grid, key=lambda g: (round(error(combo, gs[:i] + [g] + gs[i + 1:]), 4), abs(g - 1)))
    for i, (m, parts) in enumerate(zip(shared, combo)):
        tot, _ = pot(i, gs[i])
        if gs[i] != 1:
            m["split"]["scale"] *= gs[i]
            m["split"]["total"] = tot
        n = sum(parts.values())
        m["portions"] = {p: _share_of(tot, c / n) for p, c in parts.items()}
        m["split"].update(parts=parts, total_parts=n)


# --- Generowanie -------------------------------------------------------------------

def week_dates(week):
    y, w = int(week[:4]), int(week[-2:])
    start = datetime.date.fromisocalendar(y, w, 1)
    return [start + datetime.timedelta(days=i) for i in range(7)]


def current_week(date=None):
    y, w, _ = (date or datetime.date.today()).isocalendar()
    return f"{y}-W{w:02d}"


PREP_MODES = ("none", "every2", "weekly")


def prep_mode_default(st):
    v = (st["fields"].get(S["prep"]) or "tak").lower()
    if any(w in v for w in ("tydzie", "tydzień", "niedziel", "cały", "caly")):
        return "weekly"
    return "none" if v[:3].strip().startswith("nie") else "every2"


def fresh_minutes(r):
    """Ile czasu zostaje na dzień posiłku przy meal prepie tygodniowym."""
    return prep_plan_of(r)["on_day"]["minutes"]


def prep_minutes(r):
    """Łączny czas etapów robionych z wyprzedzeniem (niedzielna sesja)."""
    return sum(c["minutes"] for c in prep_plan_of(r)["components"])


WEEKLY_PREP_LIMIT = 60   # w trybie tygodniowym unikamy dań, których etapy zajmują więcej


def _pick(cands, used, avoid, limit, rng, prefer_prep=False, weekly=False):
    if not cands:
        return None
    def score(r):
        t = fresh_minutes(r) if weekly else r["time"]
        return ((t > limit) if limit else False,
                (prep_minutes(r) > WEEKLY_PREP_LIMIT) if weekly else False,
                (not r.get("meal_prep")) if prefer_prep else False,
                r["id"] in avoid, used.get(r["id"], 0), rng.random())
    return min(cands, key=score)


PREP_LIMITS = (0, 60, 90, 120, 180)   # limit niedzielnej sesji w minutach (0 = bez limitu)
DEFAULT_PREP_LIMIT = 120


def _sunday_cost(r):
    """Przybliżona praca przy kuchni w niedzielę dla jednego przepisu (etapy z wyprzedzeniem)."""
    return sum(c["minutes"] * ACTIVE_SHARE.get(c["equipment"], 1) for c in prep_plan_of(r)["components"])


def _sunday_minutes(plan, lib, prods):
    ses = next((s for s in weekly_prep(plan, lib, prods)["sessions"] if s["day"] == SUNDAY), None)
    return ses["minutes"] if ses else 0


def fit_prep_limit(plan, lib, prods, ok, lim_wd, rng, max_steps=25):
    """Zamienia najbardziej pracochłonne (w niedzielę) dania na prostsze, aż sesja zmieści się w limicie.
    Dotyczy całych partii meal prepu i wszystkich wystąpień danego dania w tygodniu."""
    limit = plan["prep_limit"]
    before = _sunday_minutes(plan, lib, prods)
    now, replaced, tried = before, [], set()
    for _ in range(max_steps):
        if now <= limit:
            break
        used = {}
        for day in plan["days"]:
            for m in day["meals"]:
                used[m["recipe"]] = used.get(m["recipe"], 0) + 1
        # danie o największej niedzielnej pracy, którego jeszcze nie próbowaliśmy zamienić
        rid = max((r for r in used if r in lib and r not in tried), key=lambda r: _sunday_cost(lib[r]), default=None)
        if rid is None or _sunday_cost(lib[rid]) <= 0:
            break
        tried.add(rid)
        cost = _sunday_cost(lib[rid])
        snapshot = [[(m["recipe"], dict(m["portions"]), m.get("split")) for m in day["meals"]] for day in plan["days"]]
        changed_days = set()
        for d, day in enumerate(plan["days"]):
            for m in day["meals"]:
                if m["recipe"] != rid:
                    continue
                s = plan["slots"][m["slot"]]
                days_of = m.get("batch") or [d]
                taken = {x["recipe"] for dd in days_of for x in plan["days"][dd]["meals"]}
                cands = [r for r in ok if s["slot"] in r["slots"] and r["id"] not in taken
                         and _sunday_cost(r) < cost * 0.6]
                if len(days_of) > 1:  # partia: danie musi się nadawać na zapas
                    cands = [r for r in cands if r.get("meal_prep") or prep_plan_of(r)["components"]]
                if not cands:
                    continue
                new = min(cands, key=lambda r: (fresh_minutes(r) > max(lim_wd, 20), _sunday_cost(r),
                                                used.get(r["id"], 0), rng.random()))
                for dd in days_of:
                    plan["days"][dd]["meals"][m["slot"]]["recipe"] = new["id"]
                    changed_days.add(dd)
                used[new["id"]] = used.get(new["id"], 0) + len(days_of)
        if not changed_days:
            continue
        for dd in changed_days:
            assign_day(plan, dd, lib, prods)
        after = _sunday_minutes(plan, lib, prods)
        if after < now:
            replaced.append(lib[rid]["name"])
            now = after
        else:  # nie pomogło – cofamy
            for d, day in enumerate(plan["days"]):
                for m, (rec, por, sp) in zip(day["meals"], snapshot[d]):
                    m["recipe"], m["portions"] = rec, por
                    if sp is None:
                        m.pop("split", None)
                    else:
                        m["split"] = sp
    if replaced:
        plan["assumptions"].append(
            f"Limit niedzielnej sesji {fmt_minutes(limit)}: zamieniono na prostsze {len(replaced)} "
            f"{plural(len(replaced), 'danie', 'dania', 'dań')} ({', '.join(replaced)}) – sesja z ~{fmt_minutes(before)} "
            f"do ~{fmt_minutes(now)}.")
    if now > limit:
        plan["warnings"].append(
            f"Niedzielna sesja zajmie ok. {fmt_minutes(now)} – więcej niż limit {fmt_minutes(limit)}. "
            "Zmień limit albo ułóż plan ponownie.")
    return replaced


def plural(n, one, few, many):
    if n == 1:
        return one
    return few if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else many


def fmt_minutes(m):
    h, mm = divmod(int(m), 60)
    return f"{h} h {mm} min" if h and mm else f"{h} h" if h else f"{mm} min"


def generate(week, seed=None, menu=None, prep_mode=None, prep_limit=None):
    ppl, st = people(), settings()
    slots = meal_slots(st)
    prods = {p["name"]: p for p in storage.products()}
    lib = {r["id"]: r for r in storage.recipes()}
    rng = random.Random(seed)
    excl = exclusions(ppl)
    sf = st["fields"]
    lim_wd = num(sf.get(S["weekday"])) or 30
    lim_we = num(sf.get(S["weekend"])) or 60
    prep_mode = prep_mode if prep_mode in PREP_MODES else prep_mode_default(st)
    prep = prep_mode != "none"
    weekly = prep_mode == "weekly"
    if weekly:  # w dni robocze liczy się tylko wykończenie na świeżo
        lim_wd = min(lim_wd, 20)
    warnings = []

    def disliked(r):  # oceny 👎 przeważają o 2 lub więcej
        rt = r.get("rating") or {}
        return int(rt.get("down", 0)) - int(rt.get("up", 0)) >= 2

    ok = [r for r in lib.values() if recipe_ok(r, excl, prods) and not disliked(r)]
    batches = [[0, 1], [2, 3], [4, 5], [6]] if prep else [[d] for d in range(7)]
    grid = [[None] * len(slots) for _ in range(7)]
    batch_of = {}

    if menu:
        for d in range(7):
            for si in range(len(slots)):
                grid[d][si] = menu[d][si] if si < len(menu[d]) else None
        for si, s in enumerate(slots):
            if s["slot"] == "obiad":
                for b in batches:
                    for d in b:
                        batch_of[(d, si)] = b
    else:
        used = {}
        for si, s in enumerate(slots):
            cands = [r for r in ok if s["slot"] in r["slots"]]
            if not cands:
                warnings.append(f"Brak przepisów dla „{s['name']}” po uwzględnieniu wykluczeń – dodaj przepis.")
                continue
            if s["slot"] == "obiad":
                taken = set()
                for b in batches:
                    cook_day = b[0] - 1 if len(b) > 1 else b[0]
                    limit = lim_we if cook_day in (-1, 5, 6) else lim_wd
                    r = _pick(cands, used, taken, limit, rng, prefer_prep=len(b) > 1, weekly=weekly)
                    taken.add(r["id"])
                    used[r["id"]] = used.get(r["id"], 0) + len(b)
                    for d in b:
                        grid[d][si] = r["id"]
                        batch_of[(d, si)] = b
            else:
                for d in range(7):
                    avoid = set(grid[d]) | ({grid[d - 1][si]} if d else set())
                    limit = lim_we if d >= 5 else lim_wd
                    r = _pick(cands, used, avoid, limit, rng, weekly=weekly)
                    grid[d][si] = r["id"]
                    used[r["id"]] = used.get(r["id"], 0) + 1

    targets = {p["id"]: target(p) for p in ppl}
    dates = week_dates(week)
    days = []
    for d in range(7):
        meals = []
        for si, s in enumerate(slots):
            rid = grid[d][si]
            m = {"slot": si, "recipe": rid, "portions": {}}
            b = batch_of.get((d, si))
            if b and len(b) > 1:
                m["batch"] = b
            meals.append(m)
        days.append({"date": dates[d].isoformat(), "name": DAY_NAMES[d], "short": DAY_SHORT[d], "meals": meals})

    assumptions = [f"{p['name']}: {targets[p['id']]['kcal']} kcal/dzień – {targets[p['id']]['source']}" for p in ppl]
    if excl["notes"]:
        assumptions.append("Wykluczenia: " + "; ".join(excl["notes"]))
    else:
        assumptions.append("Brak wpisanych alergii i nietolerancji – plan może zawierać gluten, laktozę, jaja i ryby.")
    for s in slots:
        if not s["time"]:
            warnings.append(f"Brak godziny posiłku „{s['name']}” w ustawieniach.")
    for p in ppl:
        t = targets[p["id"]]
        if t["kcal"] < t["min"]:
            warnings.append(f"{p['name']}: {t['kcal']} kcal to mniej niż bezpieczne minimum (~{t['min']}) – skonsultuj z lekarzem.")

    plan = {
        "week": week, "start": dates[0].isoformat(), "end": dates[-1].isoformat(),
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "persons": [{"id": p["id"], "name": p["name"]} for p in ppl],
        "targets": targets, "slots": slots, "days": days,
        "assumptions": assumptions, "warnings": warnings,
        "shopping": {"items": []}, "prep_mode": prep_mode,
    }
    for d in range(7):
        assign_day(plan, d, lib, prods)
    if weekly:
        plan["prep_limit"] = prep_limit if prep_limit in PREP_LIMITS else DEFAULT_PREP_LIMIT
        if plan["prep_limit"]:
            fit_prep_limit(plan, lib, prods, ok, lim_wd, rng)
    return rebuild(plan)


def _balance_pieces(plan, day, targets, lib, prods):
    """Liczba sztuk każdej osoby w daniach „w sztukach” dobierana dla całego dnia (np. 2 wrapy zamiast 1,
    a za to placek mniej), żeby suma dnia była jak najbliżej celu. Posiłki „każdy osobno” liczymy wg celu."""
    pieces = [m for m in day["meals"] if (m.get("split") or {}).get("mode") == "pieces"]
    if not pieces:
        return
    share = {id(m): plan["slots"][m["slot"]]["share"] for m in day["meals"]}
    unit = {}
    for m in pieces:
        sp = m["split"]
        unit[id(m)] = nutrition(sp["total"], prods)[0] / sp["total_units"]
    new_counts = {id(m): {} for m in pieces}
    for p, k in targets.items():
        planned = k * sum(share[id(m)] for m in day["meals"] if m["recipe"] in lib)
        fixed = sum(nutrition(m["portions"].get(p, {}), prods)[0] if m.get("split") else k * share[id(m)]
                    for m in day["meals"] if m["recipe"] in lib and m not in pieces)
        opts = []
        for m in pieces:
            ideal = k * share[id(m)] / unit[id(m)] if unit[id(m)] else 1
            lo = max(1, int(ideal) - 1)
            opts.append(list(range(lo, max(lo, int(ideal) + 2) + 1)))
        best = None
        for combo in itertools.product(*opts):
            got = fixed + sum(c * unit[id(m)] for c, m in zip(combo, pieces))
            # błąd dnia + lekka kara za odejście od „naturalnej” liczby sztuk danego posiłku
            dev = sum(abs(c * unit[id(m)] - k * share[id(m)]) for c, m in zip(combo, pieces)) / planned
            key = abs(got - planned) / planned + 0.05 * dev
            if best is None or key < best[0]:
                best = (key, combo)
        for c, m in zip(best[1], pieces):
            new_counts[id(m)][p] = c
    for m in pieces:
        sp, r = m["split"], lib[m["recipe"]]
        counts = new_counts[id(m)]
        units = sum(counts.values())
        upp = serving_of(r)["units_per_portion"]
        total = _scaled(r, units / upp, prods)
        m["portions"] = {p: _share_of(total, c / units) for p, c in counts.items()}
        sp.update(counts=counts, total_units=units, total=total)


# --- Meal prep na cały tydzień ---------------------------------------------------------

SUNDAY, MIDWEEK = -1, 2   # niedziela przed tygodniem, środa wieczorem
EQUIP_ORDER = {"piekarnik": 0, "garnek": 1, "patelnia": 2, "blender": 3, "bez obróbki": 4}


# Jaka część czasu etapu to praca przy kuchni (reszta dzieje się sama: pieczenie, gotowanie, duszenie).
ACTIVE_SHARE = {"piekarnik": 0.15, "garnek": 0.3, "patelnia": 0.8, "blender": 0.5, "bez obróbki": 1.0}
PACKING_MIN = 10
# Proste bazy, które gotuje się raz dla kilku dań („Ugotuj: ryż 450 g”).
BASE_WORDS = ("ryż", "kasza", "makaron", "ziemniak", "batat", "quinoa", "kuskus", "bulgur", "pęczak",
              "soczewica", "ciecierzyca sucha", "fasola sucha", "jajka")


def _round5(x):
    return max(5, int(5 * round(x / 5)))


def place(d, comp):
    """Gdzie przygotować etap jedzony w dniu d (0 = pn): (sesja, gdzie przechować) albo (None, 'fresh')."""
    if d + 1 <= comp["fridge_days"]:
        return SUNDAY, "fridge"
    if comp["freezes"]:
        return SUNDAY, "freezer"
    if d > MIDWEEK and d - MIDWEEK <= comp["fridge_days"]:
        return MIDWEEK, "fridge"
    return None, "fresh"


def _who(plan, m, prods_of):
    sp = m.get("split") or {}
    P = plan["persons"]
    if sp.get("mode") == "shared":
        return ", ".join(f"{p['name']} {sp['parts'][p['id']]}/{sp['total_parts']}" for p in P)
    if sp.get("mode") == "pieces":
        return ", ".join(f"{p['name']} {sp['counts'][p['id']]} szt." for p in P)
    return "osobno: " + ", ".join(
        p["name"] + " " + " + ".join(f"{round(m['portions'].get(p['id'], {}).get(k, 0))} g" for k in prods_of)
        for p in P)


def weekly_prep(plan, lib, prods):
    """Sesje przygotowań (niedziela, ewentualnie środa), rozmrażanie i wykończenia na świeżo."""
    old = {t["id"]: t.get("checked", False) for s in (plan.get("prep") or {}).get("sessions", []) for t in s["tasks"]}
    tasks, thaw, fresh = {}, {}, {}
    for d, day in enumerate(plan["days"]):
        for m in day["meals"]:
            r = lib.get(m["recipe"])
            if not r:
                continue
            pp = prep_plan_of(r)
            sp = m.get("split") or {}
            total = sp.get("total") or {}
            if not total:
                for por in m["portions"].values():
                    for k, g in por.items():
                        total[k] = total.get(k, 0) + g
            slot_name = plan["slots"][m["slot"]]["name"]
            fr = fresh.setdefault(str(d), {}).setdefault(str(m["slot"]), {
                "minutes": pp["on_day"]["minutes"], "steps": list(pp["on_day"]["steps"]),
                "from_fridge": [], "from_freezer": [], "auto": pp["auto"]})
            for c in pp["components"]:
                session, where = place(d, c)
                if session is None:
                    fr["steps"] = c["steps"] + fr["steps"]
                    fr["minutes"] += c["minutes"]
                    continue
                (fr["from_freezer"] if where == "freezer" else fr["from_fridge"]).append(c["name"])
                if where == "freezer":
                    thaw.setdefault(str(d - 1), []).append(f"{c['name']} – {DAY_SHORT[d]} {slot_name.lower()}")
                base = (len(c["products"]) == 1 and c["equipment"] == "garnek"
                        and any(w in c["products"][0] for w in BASE_WORDS))
                key = f"{session}:" + (f"base:{c['products'][0]}" if base else f"{r['id']}:{c['name']}")
                t = tasks.setdefault(key, {
                    "id": key, "session": session, "name": f"Ugotuj: {c['products'][0]}" if base else c["name"],
                    "recipes": [], "equipment": c["equipment"], "minutes": c["minutes"],
                    "steps": c["steps"], "amounts": {}, "containers": [], "checked": old.get(key, False)})
                t["minutes"] = max(t["minutes"], c["minutes"])
                if r["name"] not in t["recipes"]:
                    t["recipes"].append(r["name"])
                amounts = {k: round(total.get(k, 0), 1) for k in c["products"]}
                for k, g in amounts.items():
                    t["amounts"][k] = round(t["amounts"].get(k, 0) + g, 1)
                t["containers"].append({"day": d, "slot": m["slot"], "where": where,
                                        "label": f"{c['name']} · {DAY_SHORT[d]} · {slot_name.lower()}",
                                        "who": _who(plan, m, c["products"]),
                                        "amount": " + ".join(amount_text(prods.get(k, {}), g) for k, g in amounts.items())})
    sessions = []
    for sid, label in ((SUNDAY, "Niedziela przed tygodniem"), (MIDWEEK, "Środa wieczorem")):
        ts = sorted((t for t in tasks.values() if t["session"] == sid),
                    key=lambda t: (EQUIP_ORDER.get(t["equipment"], 9), -t["minutes"], t["name"]))
        if not ts:
            continue
        for t in ts:
            t["amount_text"] = ", ".join(f"{k} {amount_text(prods.get(k, {}), g)}" for k, g in t["amounts"].items())
            t["active"] = max(1, round(t["minutes"] * ACTIVE_SHARE.get(t["equipment"], 1)))
            t["phase"] = "start" if t["equipment"] in ("piekarnik", "garnek") else "meanwhile"
        # Realny czas: piekarnik i garnki pracują w tle, więc liczy się praca przy kuchni (aktywna)
        # albo najdłuższe zadanie, jeśli trwa dłużej. Do tego ~10 min na pakowanie do pojemników.
        active = sum(t["active"] for t in ts) + PACKING_MIN
        longest = max(t["minutes"] for t in ts)
        sessions.append({"day": sid, "label": label, "minutes": _round5(max(longest, active)),
                         "active": _round5(active), "tasks": ts})
    return {"sessions": sessions, "thaw": thaw, "fresh": fresh}


def refresh(plan):
    """Przelicza plan po zmianie ustawień lub profili, zachowując wybrane dania (po nazwie posiłku),
    odhaczenia zakupów i zadań meal prepu. Posiłki, których wcześniej nie było, uzupełnia generator."""
    st = settings()
    slots = meal_slots(st)
    by_name = {}
    for d, day in enumerate(plan["days"]):
        for m in day["meals"]:
            name = plan["slots"][m["slot"]]["name"].strip().lower()
            by_name[(d, name)] = m.get("recipe")
    base = generate(plan["week"], seed=7, prep_mode=plan.get("prep_mode"))
    menu = [[by_name.get((d, s["name"].strip().lower())) or base["days"][d]["meals"][si]["recipe"]
             for si, s in enumerate(slots)] for d in range(7)]
    new = generate(plan["week"], menu=menu, prep_mode=plan.get("prep_mode"), prep_limit=0)  # dania zostają
    if "prep_limit" in plan:
        new["prep_limit"] = plan["prep_limit"]
    new["shopping"] = plan.get("shopping", {"items": []})
    if plan.get("prep"):
        new["prep"] = plan["prep"]
    for k in ("agent", "generated"):
        if plan.get(k):
            new[k] = plan[k]
    new["refreshed"] = datetime.datetime.now().isoformat(timespec="seconds")
    return rebuild(new)


def prep_toggle(plan, task_id, checked):
    for s in (plan.get("prep") or {}).get("sessions", []):
        for t in s["tasks"]:
            if t["id"] == task_id:
                t["checked"] = bool(checked)
    return plan


def schedule(plan, lib):
    out = []
    seen = set()
    for d, day in enumerate(plan["days"]):
        for m in day["meals"]:
            b = m.get("batch")
            if not b or tuple(b) + (m["slot"],) in seen or m["recipe"] not in lib:
                continue
            seen.add(tuple(b) + (m["slot"],))
            cook = b[0] - 1
            label = ("Niedziela przed tygodniem (albo rano w poniedziałek)" if cook < 0
                     else f"{DAY_NAMES[cook]} wieczór")
            out.append({"cook_day": cook, "label": label, "recipe": m["recipe"],
                        "name": lib[m["recipe"]]["name"], "time": lib[m["recipe"]]["time"],
                        "for": " i ".join(DAY_SHORT[x] for x in b)})
    for d, day in enumerate(plan["days"]):
        for m in day["meals"]:
            r = lib.get(m["recipe"])
            if r and not m.get("batch") and plan["slots"][m["slot"]]["slot"] == "obiad" and r["time"] >= 30:
                out.append({"cook_day": d, "label": f"{DAY_NAMES[d]}, przed obiadem", "recipe": r["id"],
                            "name": r["name"], "time": r["time"], "for": DAY_SHORT[d]})
    return sorted(out, key=lambda x: x["cook_day"])


def rebuild(plan):
    """Przelicza sumy dni, harmonogram i listę zakupów (zachowując odhaczenia)."""
    prods = {p["name"]: p for p in storage.products()}
    lib = {r["id"]: r for r in storage.recipes()}
    totals_needed = {}
    for day in plan["days"]:
        tot = {}
        for m in day["meals"]:
            m["name"] = lib.get(m["recipe"], {}).get("name", "— brak dania —")
            m["nutrition"] = {}
            for pid, ingr in m["portions"].items():
                n = nutrition(ingr, prods)
                m["nutrition"][pid] = n
                t = tot.setdefault(pid, [0, 0, 0, 0])
                for i in range(4):
                    t[i] += n[i]
                for k, g in ingr.items():
                    totals_needed[k] = totals_needed.get(k, 0) + g
        day["totals"] = {pid: [round(x) for x in t] for pid, t in tot.items()}
    if plan.get("prep_mode") == "weekly":
        plan["schedule"] = []
        plan["prep"] = weekly_prep(plan, lib, prods)
    else:
        plan["schedule"] = schedule(plan, lib)
        plan.pop("prep", None)

    old = {i["id"]: i for i in plan.get("shopping", {}).get("items", [])}
    items = []
    for name, g in totals_needed.items():
        prod = prods.get(name, {})
        prev = old.get(name, {})
        item = {"id": name, "name": name, "amount": g, "amount_text": amount_text(prod, g),
                "dept": prod.get("dept", "Inne"), "checked": prev.get("checked", False),
                "checked_at": prev.get("checked_at"), "checked_by": prev.get("checked_by")}
        # Ile już kupiono: przy odhaczeniu zapamiętujemy ilość. Gdy po zmianie planu trzeba więcej,
        # pozycja wraca na listę jako „dokup +X” (a gdy znów wystarczy – sama się odhacza).
        bought = prev.get("bought", prev.get("amount") if prev.get("checked") else None)
        if bought is not None:
            tol = max(5.0, 0.05 * bought, 0.5 * (prod.get("piece") or 0))
            if g > bought + tol:
                item.update(checked=False, checked_at=None, bought=bought, extra=round(g - bought, 1),
                            extra_text=amount_text(prod, g - bought), bought_text=amount_text(prod, bought))
            else:
                item.update(checked=True, checked_at=prev.get("checked_at"), checked_by=prev.get("checked_by"),
                            bought=bought)
        items.append(item)
    items += [i for i in old.values() if i.get("custom")]
    plan["shopping"] = {"items": items}
    return plan


def swap(plan, day, slot, recipe_id=None, seed=None):
    prods = {p["name"]: p for p in storage.products()}
    lib = {r["id"]: r for r in storage.recipes()}
    meal = plan["days"][day]["meals"][slot]
    s = plan["slots"][slot]
    if recipe_id is None:
        excl = exclusions(people())
        cur = {m["recipe"] for m in plan["days"][day]["meals"]}
        cands = [r for r in lib.values() if s["slot"] in r["slots"] and r["id"] not in cur
                 and recipe_ok(r, excl, prods)]
        if not cands:
            raise ValueError("Brak innych pasujących przepisów")
        recipe_id = random.Random(seed).choice(cands)["id"]
    if recipe_id not in lib:
        raise ValueError("Nieznany przepis")
    for d in meal.get("batch") or [day]:
        plan["days"][d]["meals"][slot]["recipe"] = recipe_id
        assign_day(plan, d, lib, prods)
    return rebuild(plan)


# --- Lista zakupów ---------------------------------------------------------------

def shopping_op(plan, op, data, user=None):
    items = plan["shopping"]["items"]
    if op == "toggle":
        for i in items:
            if i["id"] == data["id"]:
                i["checked"] = bool(data.get("checked"))
                i["checked_at"] = datetime.datetime.now().isoformat(timespec="seconds") if i["checked"] else None
                i["checked_by"] = user if i["checked"] else None
                for k in ("extra", "extra_text", "bought_text"):
                    i.pop(k, None)
                if i["checked"] and not i.get("custom"):
                    i["bought"] = i.get("amount")  # kupione w całości (także po „dokup”)
                else:
                    i.pop("bought", None)
    elif op == "add":
        text = (data.get("text") or "").strip()[:120]
        if text:
            items.append({"id": f"custom-{datetime.datetime.now().timestamp():.0f}-{len(items)}", "name": text,
                          "amount_text": "", "dept": "Inne", "custom": True, "checked": False})
    elif op == "remove":
        plan["shopping"]["items"] = [i for i in items if not (i["id"] == data["id"] and i.get("custom"))]
    elif op == "clear":
        plan["shopping"]["items"] = [i for i in items if not (i.get("custom") and i["checked"])]
        for i in plan["shopping"]["items"]:
            i["checked"], i["checked_at"], i["checked_by"] = False, None, None
    else:
        raise ValueError("Nieznana operacja")
    return plan


# --- Eksport Markdown ----------------------------------------------------------

def to_markdown(plan):
    prods = {p["name"]: p for p in storage.products()}
    lib = {r["id"]: r for r in storage.recipes()}
    P = plan["persons"]
    d0, d1 = plan["start"], plan["end"]
    o = [f"# Plan diety {plan['week']} ({d0[8:10]}.{d0[5:7]} – {d1[8:10]}.{d1[5:7]}.{d1[:4]})\n"]
    o.append("Cele dzienne: " + " · ".join(f"{p['name']} **{plan['targets'][p['id']]['kcal']} kcal**" for p in P) + "\n")
    o.append("## Założenia\n")
    o += [f"- {a}" for a in plan["assumptions"] + plan["warnings"]]
    for ses in (plan.get("prep") or {}).get("sessions", []):
        o.append(f"\n## Meal prep – {ses['label'].lower()} (ok. {ses['minutes']} min)\n")
        for t in ses["tasks"]:
            o.append(f"- [{'x' if t.get('checked') else ' '}] **{t['name']}** ({t['equipment']}, ~{t['minutes']} min): "
                     f"{t['amount_text']}")
            o += [f"  - {x}" for x in t["steps"]]
            o += [f"  - 📦 {c['label']}{' · zamrażarka' if c['where'] == 'freezer' else ''} – {c['amount']} ({c['who']})"
                  for c in t["containers"]]
    if plan["schedule"]:
        o.append("\n## Harmonogram gotowania\n\n| Kiedy | Co | Na | Czas |\n|---|---|---|---|")
        o += [f"| {s['label']} | {s['name']} | {s['for']} | ~{s['time']} min |" for s in plan["schedule"]]
    for day in plan["days"]:
        o.append(f"\n## {day['name']} {day['date'][8:10]}.{day['date'][5:7]}\n")
        o.append("| Godz. | Posiłek | Danie | " + " | ".join(f"{p['name']} (porcja, kcal, B/W/T)" for p in P) + " |")
        o.append("|---|---|---|" + "---|" * len(P))
        for m in day["meals"]:
            s = plan["slots"][m["slot"]]
            cells = []
            for p in P:
                ing = m["portions"].get(p["id"], {})
                n = m["nutrition"].get(p["id"], [0, 0, 0, 0])
                sp = m.get("split") or {}
                how = (f"{sp['parts'][p['id']]}/{sp['total_parts']} garnka" if sp.get("mode") == "shared"
                       else f"{sp['counts'][p['id']]} szt." if sp.get("mode") == "pieces"
                       else f"{round(sum(ing.values()))} g")
                cells.append(f"{how} · {n[0]:.0f} kcal · {n[1]:.0f}/{n[2]:.0f}/{n[3]:.0f}")
            o.append(f"| {s['time']} | {s['name']} | {m['name']} | " + " | ".join(cells) + " |")
        di = str(plan["days"].index(day))
        prep = plan.get("prep") or {}
        if prep.get("thaw", {}).get(di):
            o.append("\n🧊 **Wieczorem przenieś z zamrażarki do lodówki:** " + "; ".join(prep["thaw"][di]))
        for si, fr in sorted((prep.get("fresh") or {}).get(di, {}).items(), key=lambda x: int(x[0])):
            if fr["from_fridge"] or fr["from_freezer"]:
                o.append(f"\n**{plan['slots'][int(si)]['name']} – na świeżo (~{fr['minutes']} min):** "
                         f"z lodówki: {', '.join(fr['from_fridge'] + fr['from_freezer'])}. " + " ".join(fr["steps"]))
        o.append("\n**Suma dnia** – " + " · ".join(
            f"{p['name']}: {day['totals'][p['id']][0]} kcal (cel {plan['targets'][p['id']]['kcal']})"
            for p in P if p["id"] in day["totals"]))
    o.append("\n## Przepisy\n")
    seen = set()
    for day in plan["days"]:
        for m in day["meals"]:
            key = (m["recipe"], m["slot"])
            if key in seen or m["recipe"] not in lib:
                continue
            seen.add(key)
            r = lib[m["recipe"]]
            o.append(f"### {r['name']}\n*{plan['slots'][m['slot']]['name']} · ok. {r['time']} min*\n")
            sp = m.get("split") or {}
            if sp.get("mode") == "shared":
                o.append(f"**Podział:** ugotuj całość i podziel na {sp['total_parts']} równe części – " +
                         ", ".join(f"{p['name']} {sp['parts'][p['id']]}" for p in P) + ".\n")
            elif sp.get("mode") == "pieces":
                o.append(f"**Podział:** {sp['total_units']} szt. ({sp['unit']}) – " +
                         ", ".join(f"{p['name']} {sp['counts'][p['id']]}" for p in P) + ".\n")
            o.append("| Składnik | " + " | ".join(p["name"] for p in P) + " |")
            o.append("|---|" + "---|" * len(P))
            for i in r["ingredients"]:
                k = i["product"]
                o.append(f"| {k} | " + " | ".join(amount_text(prods.get(k, {}), m["portions"].get(p["id"], {}).get(k, 0)) for p in P) + " |")
            ins = instructions(r)
            if ins["prep_notes"]:
                o.append("\n**Przed gotowaniem:**")
                o += [f"- {x}" for x in ins["prep_notes"]]
            if ins["steps"]:
                o.append("\n**Przygotowanie:**")
                o += [f"{n}. {x}" for n, x in enumerate(ins["steps"], 1)]
            if ins["storage"]:
                o.append(f"\n**Przechowywanie i odgrzewanie:** {ins['storage']}")
            o.append("")
    o.append("## Lista zakupów\n")
    depts = {}
    for i in plan["shopping"]["items"]:
        depts.setdefault(i["dept"], []).append(i)
    for dept, items in depts.items():
        o.append(f"**{dept}**")
        o += [f"- [{'x' if i['checked'] else ' '}] {i['name']}" + (f" – {i['amount_text']}" if i.get("amount_text") else "") for i in items]
        o.append("")
    return "\n".join(o) + "\n"
