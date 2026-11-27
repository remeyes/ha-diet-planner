"""Agent przepisów: zamienia tekst (wklejony przepis, fragment książki) na przepisy w bazie.

Model wyciąga przepisy i dopasowuje składniki do bazy produktów (gramy na porcję).
Kalorie liczy wyłącznie nutrition.py. Wyniki trafiają do kolejki „Do przejrzenia”.
"""
import datetime
import re
import uuid

import ai
import nutrition
import planner
import storage

DEPTS = ["Warzywa i owoce", "Mięso i ryby", "Nabiał i jaja", "Pieczywo i produkty zbożowe",
         "Produkty suche, mrożonki i konserwy", "Tłuszcze i przyprawy", "Inne"]
TAGS = ["gluten", "laktoza", "jaja", "ryby", "orzechy", "mięso"]
SLOTS = list(planner.SLOT_LABEL)
MIN_KCAL, MAX_KCAL = 150, 1500

SERVING_RULES = """Porcjowanie (dwie osoby jedzą ten sam posiłek, ale w różnych ilościach – podział musi być prosty):
- `serving_mode` = "shared" – danie gotowane w całości i dzielone na równe części: zupa, gulasz, curry, chili,
  zapiekanka, makaron z sosem, kasza/ryż z sosem, risotto, leczo, sałatka robiona w misce dla wszystkich.
  Ostatni krok: „Podziel na równe części według planu (np. na 5 części)”.
- `serving_mode` = "pieces" – danie w sztukach, które łatwo policzyć: placki, pancakes, naleśniki, pulpety,
  kotlety, mufinki, wrapy/tortille, tosty, szaszłyki, gołąbki, kanapki (sztuka = kromka/kanapka).
  Podaj `unit` (rzeczownik w liczbie pojedynczej, np. „placek”, „pulpet”, „kanapka”) i `units_per_portion` –
  ile sztuk przypada na JEDNĄ porcję bazową, czyli na porcję, dla której podajesz gramatury składników.
- `serving_mode` = "individual" – każdy składa swoją porcję z produktów (owsianka, jogurt z dodatkami, smoothie,
  koktajl, jajecznica/omlet na osobę, twarożek, owoc). Wtedy `unit` = "" i `units_per_portion` = 0.
- Gdy wahasz się między "shared" a "individual" przy daniu gotowanym – wybierz "shared"."""


PREP_RULES = """Meal prep (`prep_plan`) – rozbij danie na etapy do zrobienia wcześniej (w niedzielę na cały tydzień)
i wykończenie na świeżo w dniu posiłku:
- `components` – etapy, które można zrobić z wyprzedzeniem i przechować: bazy (ryż, kasza, makaron, ziemniaki),
  sosy, pieczone/duszone mięso i warzywa, zupy, gulasze, pasty, dressingi, pokrojone twarde warzywa, owsianka nocna,
  wymieszane suche składniki. Każdy etap: `name` (krótko, np. „Ryż”, „Sos pomidorowy”), `products` – DOKŁADNE nazwy
  składników przepisu z listy (każdy składnik najwyżej w jednym etapie), `steps` (krótko, bez gramatur), `minutes`,
  `equipment` (piekarnik, garnek, patelnia, blender, bez obróbki), `fridge_days` – ile dni etap bezpiecznie
  wytrzyma w lodówce (ugotowane mięso, drób, ryż, kasze: 3–4; ryba i owoce morza: 1–2; zupy, sosy, gulasze: 3–4;
  pokrojone twarde warzywa: 3–4; sałaty i miękkie owoce pokrojone: 1), `freezes` – czy dobrze znosi mrożenie
  (sosy, zupy, gulasze, mięso duszone, kasze, ryż: tak; sałaty, surowe warzywa, ziemniaki gotowane, jajka, sosy
  jogurtowe i majonezowe, makaron al dente: nie).
- Na świeżo zostaw to, co traci jakość: smażenie mięsa/ryb na chrupko, jajka sadzone/jajecznica, omlety, tosty,
  krojenie sałat i miękkich owoców, sosy jogurtowe, składanie kanapek i wrapów. Składniki użyte tylko na świeżo nie
  trafiają do żadnego etapu.
- `on_day` – wykończenie w dniu posiłku: `minutes` i `steps` (np. „Odgrzej ryż i sos”, „Usmaż kurczaka 6–7 min”).
- Danie robione w całości na świeżo (np. jajecznica, owoc, jogurt) – pusta lista `components`, kroki w `on_day`."""


INSTRUCTION_RULES = """Instrukcje (piszesz je własnymi słowami, jasno, w trybie rozkazującym, dla osoby gotującej z telefonem):
- `prep_notes` – „Przed gotowaniem”: czynności, które trzeba zrobić NAJPIERW albo z wyprzedzeniem, np.
  „Rozgrzej piekarnik do 200°C (góra–dół)”, „Namocz ciecierzycę na noc”, „Wyjmij mięso z lodówki 30 min wcześniej”,
  „Wyłóż blachę papierem do pieczenia”, „Nastaw wodę na makaron”. 0–4 pozycje; pusta lista, gdy nic takiego nie trzeba.
  Tylko rzeczy, które trzeba zacząć z wyprzedzeniem albo które „idą w tle” (piekarnik, moczenie, marynowanie,
  rozmrażanie, woda na gotowanie). Mycie, krojenie i samo gotowanie NIE są uwagami – to zwykłe kroki.
  Jeśli danie idzie do piekarnika, rozgrzanie z temperaturą i trybem jest obowiązkowe.
- `steps` – sposób przygotowania krok po kroku: 4–12 kroków, jedna czynność w kroku (1–2 zdania).
  Podawaj czas, temperaturę, moc ognia (mały/średni/duży), sposób krojenia i oznakę gotowości
  (np. „aż cebula się zeszkli”, „aż patyczek wyjdzie suchy”). NIE podawaj gramatur ani liczby sztuk – ilości
  są w tabeli składników i aplikacja przelicza je dla każdej osoby. Nie numeruj kroków w tekście.
- `storage` – tylko gdy danie nadaje się na zapas (`meal_prep` = true): jak przechowywać (pojemnik, lodówka,
  ile dni, czy można mrozić) i jak odgrzać (patelnia/mikrofala/piekarnik, ewentualnie z odrobiną wody).
  Gdy danie nie jest na zapas – pusty tekst.
""" + SERVING_RULES


SYSTEM = """Jesteś agentem przepisów w domowej aplikacji do planowania diety (kuchnia polska, dwie osoby).
Twoje zadanie: z podanego tekstu wyciągnąć kompletne przepisy kulinarne i opisać je w ustalonym formacie.

Zasady:
- Zwracaj tylko przepisy, które naprawdę są w tekście i mają listę składników. Pomijaj wstępy, porady, spisy treści i przepisy urwane na granicy fragmentu (bez składników albo bez sposobu przygotowania).
- Język: nazwę, kroki, uwagi, przechowywanie i nazwy produktów pisz ZAWSZE po polsku. Jeśli tekst jest w innym
  języku (także tylko tytuł, np. „Sticky Chinese boczniak belly”), przetłumacz go. Nie tłumacz nazw własnych potraw
  i nazw, które po polsku funkcjonują w oryginale (np. Pad thai, Bulgogi, Gazpacho, curry, tofu, mozzarella).
- Nazwa: bez numeracji; jeśli tekst jest po polsku – tak jak w tekście.
- Składniki podaj w gramach NA JEDNĄ PORCJĘ: zsumuj ilość z przepisu i podziel przez liczbę porcji. Gdy liczba porcji nie jest podana, oszacuj ją rozsądnie i wpisz w `servings`.
- Przeliczenia: łyżka oleju/oliwy 10 g, łyżka mąki 10 g, łyżka cukru 12 g, łyżeczka 5 g, szklanka płynu 250 g, szklanka mąki 150 g, jajko 55 g, ząbek czosnku 5 g, średnia cebula 100 g, średni ziemniak 150 g. Produkty suche i mięso podawaj w wadze surowej/suchej.
- Pomijaj sól, pieprz, przyprawy suszone, wodę i składniki poniżej 3 g na porcję.
- `product`: jeśli składnik odpowiada pozycji z listy bazy produktów, wpisz DOKŁADNIE tę nazwę i ustaw `is_new` = false. Jeśli nie ma go w bazie, wpisz krótką, ogólną nazwę małymi literami (np. „ser ricotta”, „dynia hokkaido”), ustaw `is_new` = true i podaj wartości odżywcze na 100 g produktu surowego (z błonnikiem) (polskie tabele wartości odżywczych), dział sklepu i tagi alergenów. Dla produktów z bazy wartości odżywcze wpisz 0.
- `slots`: do jakich posiłków danie pasuje (sniadanie, przekaska, obiad, kolacja).
- `meal_prep`: true, jeśli danie dobrze znosi przechowywanie 1–2 dni i odgrzanie.
- `time`: łączny czas przygotowania w minutach.

""" + PREP_RULES + """

""" + INSTRUCTION_RULES


PREP_PROPS = {"prep_plan": {"type": "object", "additionalProperties": False, "required": ["components", "on_day"],
                            "properties": {
                                "components": {"type": "array", "items": {
                                    "type": "object", "additionalProperties": False,
                                    "required": ["name", "products", "steps", "minutes", "equipment", "fridge_days", "freezes"],
                                    "properties": {
                                        "name": {"type": "string"},
                                        "products": {"type": "array", "items": {"type": "string"}},
                                        "steps": {"type": "array", "items": {"type": "string"}},
                                        "minutes": {"type": "integer"},
                                        "equipment": {"type": "string", "enum": list(nutrition.EQUIPMENT)},
                                        "fridge_days": {"type": "integer"}, "freezes": {"type": "boolean"}}}},
                                "on_day": {"type": "object", "additionalProperties": False, "required": ["minutes", "steps"],
                                           "properties": {"minutes": {"type": "integer"},
                                                          "steps": {"type": "array", "items": {"type": "string"}}}}}}}

SERVING_PROPS = {"serving_mode": {"type": "string", "enum": ["shared", "pieces", "individual"]},
                 "unit": {"type": "string"}, "units_per_portion": {"type": "number"}}


def _schema():
    ing = {"type": "object", "additionalProperties": False,
           "required": ["text", "product", "grams_per_serving", "is_new", "kcal_100g", "protein_100g",
                        "carbs_100g", "fat_100g", "fiber_100g", "dept", "tags"],
           "properties": {
               "text": {"type": "string"}, "product": {"type": "string"},
               "grams_per_serving": {"type": "number"}, "is_new": {"type": "boolean"},
               "kcal_100g": {"type": "number"}, "protein_100g": {"type": "number"},
               "carbs_100g": {"type": "number"}, "fat_100g": {"type": "number"},
               "fiber_100g": {"type": "number"},
               "dept": {"type": "string", "enum": DEPTS},
               "tags": {"type": "array", "items": {"type": "string", "enum": TAGS}}}}
    rec = {"type": "object", "additionalProperties": False,
           "required": ["name", "servings", "time", "slots", "meal_prep", "ingredients",
                        "prep_notes", "steps", "storage", "serving_mode", "unit", "units_per_portion", "prep_plan",
                        "chapter"],
           "properties": {
               "name": {"type": "string"}, "servings": {"type": "integer"}, "time": {"type": "integer"},
               "slots": {"type": "array", "items": {"type": "string", "enum": SLOTS}},
               "meal_prep": {"type": "boolean"}, "ingredients": {"type": "array", "items": ing},
               "prep_notes": {"type": "array", "items": {"type": "string"}},
               "steps": {"type": "array", "items": {"type": "string"}},
               "storage": {"type": "string"}, "chapter": {"type": "string"},
               **SERVING_PROPS, **PREP_PROPS}}
    return {"type": "object", "additionalProperties": False, "required": ["recipes"],
            "properties": {"recipes": {"type": "array", "items": rec}}}


def norm_name(s):
    return re.sub(r"[^a-ząćęłńóśźż0-9]+", " ", (s or "").lower()).strip()


# --- Rozpisywanie instrukcji istniejących przepisów ------------------------------------

REWRITE_SYSTEM = """Jesteś agentem przepisów w domowej aplikacji do planowania diety (kuchnia polska).
Dostajesz gotowe przepisy (nazwa, składniki z gramaturą porcji, obecny skrótowy opis). Rozpisz dla każdego
instrukcje. Nie zmieniaj dania: trzymaj się składników i opisu, uzupełnij tylko brakujące oczywiste czynności
(np. rozgrzanie piekarnika, ugotowanie kaszy, umycie i pokrojenie warzyw). Zwróć wynik dla KAŻDEGO przepisu,
z jego `id` bez zmian.

""" + INSTRUCTION_RULES + "\n\n" + PREP_RULES

REWRITE_BATCH = 8
CLASSIFY_BATCH = 8


def _rewrite_schema():
    item = {"type": "object", "additionalProperties": False,
            "required": ["id", "prep_notes", "steps", "storage", "serving_mode", "unit", "units_per_portion",
                         "prep_plan"],
            "properties": {"id": {"type": "string"},
                           "prep_notes": {"type": "array", "items": {"type": "string"}},
                           "steps": {"type": "array", "items": {"type": "string"}},
                           "storage": {"type": "string"}, **SERVING_PROPS, **PREP_PROPS}}
    return {"type": "object", "additionalProperties": False, "required": ["recipes"],
            "properties": {"recipes": {"type": "array", "items": item}}}


def rewrite_prompt(recipes):
    parts = []
    for r in recipes:
        ings = ", ".join(f"{i['product']} {round(i['grams'])} g" for i in r.get("ingredients", []))
        steps = " ".join(nutrition.steps_list(r)) or "(brak opisu)"
        parts.append(f"id: {r['id']}\nnazwa: {r['name']}\nczas: {r.get('time', '?')} min · "
                     f"na zapas: {'tak' if r.get('meal_prep') else 'nie'}\nskładniki (1 porcja): {ings}\n"
                     f"obecny opis: {steps}")
    return "Przepisy do rozpisania:\n\n" + "\n\n---\n\n".join(parts)


def rewrite_instructions(recipes, model=None):
    """Zwraca {id: {prep_notes, steps, storage}} dla podanych przepisów (bez zapisu).
    Zmienia wyłącznie instrukcje – składniki, nazwy i kalorie zostają nietknięte."""
    by_id = {r["id"]: r for r in recipes}
    out = ai.ask_json([{"type": "text", "text": rewrite_prompt(recipes)}], _rewrite_schema(),
                      purpose="instrukcje przepisów", task="recipe_enrich", model=model, effort="low", max_tokens=64000,
                      system=REWRITE_SYSTEM)
    result = {}
    for item in out.get("recipes", []):
        r = by_id.get(item.get("id"))
        if r is None:
            continue
        ins = nutrition.instructions({**item, "meal_prep": r.get("meal_prep")})
        sv = nutrition.clean_serving(item)
        if sv:
            ins["serving"] = sv
        pp = nutrition.clean_prep_plan(item.get("prep_plan"), r)
        if pp:
            ins["prep_plan"] = pp
        if ins["steps"]:
            result[r["id"]] = ins
    return result


CLASSIFY_SYSTEM = """Jesteś agentem przepisów w domowej aplikacji do planowania diety. Dla każdego przepisu określ
sposób porcjowania i rozbicie na etapy meal prepu. Nie zmieniaj dania. W `products` używaj dokładnych nazw
składników podanych przy przepisie. Zwróć wynik dla KAŻDEGO przepisu, z jego `id` bez zmian.

""" + SERVING_RULES + "\n\n" + PREP_RULES


def classify_serving(recipes, model=None):
    """Zwraca {id: {"serving": {...}, "prep_plan": {...}}} – porcjowanie i etapy meal prepu,
    bez ruszania instrukcji."""
    by_id = {r["id"]: r for r in recipes}
    item = {"type": "object", "additionalProperties": False,
            "required": ["id", "serving_mode", "unit", "units_per_portion", "prep_plan"],
            "properties": {"id": {"type": "string"}, **SERVING_PROPS, **PREP_PROPS}}
    schema = {"type": "object", "additionalProperties": False, "required": ["recipes"],
              "properties": {"recipes": {"type": "array", "items": item}}}
    out = ai.ask_json([{"type": "text", "text": rewrite_prompt(recipes)}], schema, purpose="porcjowanie i meal prep",
                      task="recipe_enrich", model=model, effort="low", max_tokens=64000, system=CLASSIFY_SYSTEM)
    result = {}
    for it in out.get("recipes", []):
        r = by_id.get(it.get("id"))
        if not r:
            continue
        res = {}
        sv = nutrition.clean_serving(it)
        if sv:
            res["serving"] = sv
        pp = nutrition.clean_prep_plan(it.get("prep_plan"), r)
        if pp:
            res["prep_plan"] = pp
        elif isinstance(it.get("prep_plan"), dict):  # model uznał, że wszystko na świeżo – zapamiętaj to
            res["prep_plan"] = {"components": [], "on_day": {"minutes": int(r.get("time") or 15),
                                                             "steps": nutrition.steps_list(r)}}
        if res:
            result[r["id"]] = res
    return result


# --- Kolejka „Do przejrzenia” -------------------------------------------------------

# Blokują zatwierdzenie tylko braki, bez których przepisu nie da się użyć. Nietypowa kaloryczność
# (np. pasta kanapkowa, sałatka, dodatek) to tylko uwaga do sprawdzenia.
BLOCKING = ("Brak rozpoznanych", "Brak sposobu")


def is_blocking(warnings):
    return any(w.startswith(BLOCKING) for w in warnings or [])


def inbox():
    items = storage.read_json(storage.path("library", "inbox.json"), [])
    for d in items:  # starsze szkice miały ostrzejszą regułę – liczymy na nowo
        d["blocking"] = is_blocking(d.get("warnings"))
    return items


def save_inbox(items):
    storage.write_json(storage.path("library", "inbox.json"), items)


def known_names():
    return {norm_name(r["name"]) for r in storage.recipes()} | {norm_name(d["recipe"]["name"]) for d in inbox()}


# --- Budowanie szkicu (walidacja w kodzie) ---------------------------------------------

def _num(v, lo, hi):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if lo <= x <= hi else None


def build_draft(raw, source=None, prods=None):
    """Zamienia odpowiedź modelu na szkic przepisu z ostrzeżeniami. Nie zapisuje niczego."""
    prods = prods if prods is not None else nutrition.products_map()
    alias = storage.aliases()  # stare nazwy scalonych / przemianowanych produktów
    warnings, new_products, ingr = [], {}, {}
    for i in raw.get("ingredients", []):
        name = (i.get("product") or "").strip().lower()[:60]
        name = alias.get(name, name)
        grams = _num(i.get("grams_per_serving"), 0, 100000)
        if not name or grams is None:
            warnings.append(f"Pominięto składnik bez nazwy lub ilości: „{i.get('text', '')}”.")
            continue
        if grams < 3:
            continue
        if grams > 1500:
            warnings.append(f"{name}: {round(grams)} g na porcję wygląda na błąd przeliczenia.")
        if name not in prods:
            vals = {k: _num(i.get(f"{k}_100g"), 0, 900) for k in ("kcal", "protein", "carbs", "fat")}
            if not i.get("is_new") or None in vals.values() or vals["kcal"] == 0:
                warnings.append(f"Nie rozpoznano produktu „{name}” ({i.get('text', '')}) – pominięty, dodaj ręcznie.")
                continue
            macros = vals["protein"] + vals["carbs"] + vals["fat"]
            calc = vals["protein"] * 4 + vals["carbs"] * 4 + vals["fat"] * 9
            if macros > 100 or (vals["kcal"] > 20 and abs(calc - vals["kcal"]) / vals["kcal"] > 0.3):
                warnings.append(f"Wartości odżywcze „{name}” są niespójne – sprawdź je.")
            fib = _num(i.get("fiber_100g"), 0, 90)
            new_products[name] = {"name": name, **{k: round(v, 1) for k, v in vals.items()},
                                  **({"fiber": round(fib, 1)} if fib is not None else {}),
                                  "dept": i.get("dept") if i.get("dept") in DEPTS else "Inne",
                                  "tags": [t for t in i.get("tags", []) if t in TAGS],
                                  "estimated": True, "custom": True}
        ingr[name] = ingr.get(name, 0) + grams
    recipe = {
        "name": (raw.get("name") or "").strip()[:100],
        "slots": [s for s in raw.get("slots", []) if s in SLOTS] or ["obiad"],
        "time": int(max(5, min(300, raw.get("time") or 30))),
        "meal_prep": bool(raw.get("meal_prep")),
        "ingredients": [{"product": k, "grams": round(g)} for k, g in ingr.items()],
        "lang": "pl",  # agent przepisów zawsze zwraca polski tekst (SYSTEM)
    }
    recipe.update(nutrition.instructions({**raw, "meal_prep": recipe["meal_prep"]}))
    pp = nutrition.clean_prep_plan(raw.get("prep_plan"), recipe)
    if pp:
        recipe["prep_plan"] = pp
    sv = nutrition.clean_serving(raw.get("serving") or {"mode": raw.get("serving_mode"), "unit": raw.get("unit"),
                                                        "units_per_portion": raw.get("units_per_portion")})
    if sv:
        recipe["serving"] = sv
        if raw.get("serving_mode") == "pieces" and sv["mode"] != "pieces":
            warnings.append("Danie w sztukach bez liczby sztuk na porcję – ustawiono podział garnka na części.")
    if source:
        recipe["source"] = {k: v for k, v in source.items() if v}
        if raw.get("chapter"):
            recipe["source"]["chapter"] = raw["chapter"][:100]
    if not recipe["ingredients"]:
        warnings.append("Brak rozpoznanych składników.")
    if not recipe["steps"]:
        warnings.append("Brak sposobu przygotowania – dopisz kroki.")
    elif len(recipe["steps"]) < 3:
        warnings.append("Sposób przygotowania ma mniej niż 3 kroki – sprawdź, czy jest kompletny.")
    facts = nutrition.recipe_facts(recipe, {**prods, **new_products})
    kcal = facts["per_portion"]["kcal"]
    if recipe["ingredients"] and not MIN_KCAL <= kcal <= MAX_KCAL:
        warnings.append(f"Porcja ma {kcal} kcal – to nietypowo {'mało' if kcal < MIN_KCAL else 'dużo'}; "
                        "sprawdź liczbę porcji i gramatury (przy dodatkach, np. pastach, to normalne).")
    if new_products:
        warnings.append("Nowe produkty z szacowanymi wartościami: " + ", ".join(new_products) + ".")
    return {"id": uuid.uuid4().hex[:12], "created": datetime.datetime.now().isoformat(timespec="seconds"),
            "recipe": recipe, "new_products": list(new_products.values()), "nutrition": facts,
            "servings_source": raw.get("servings"), "warnings": warnings,
            "blocking": is_blocking(warnings)}


SPLIT_OVERLAP = 800   # zakładka przy dzieleniu tekstu, żeby przepis na granicy trafił w całości do jednej połówki
MAX_SPLIT_DEPTH = 3   # maks. 8 części z jednego fragmentu


def split_text(text, overlap=SPLIT_OVERLAP):
    """Dzieli tekst na dwie połówki po granicy akapitu najbliższej środka (z zakładką)."""
    mid = len(text) // 2
    cuts = [m.start() for m in re.finditer(r"\n\s*\n", text)] or [m.start() for m in re.finditer(r"\n", text)]
    cut = min(cuts, key=lambda c: abs(c - mid)) if cuts else mid
    if not len(text) // 4 <= cut <= len(text) * 3 // 4:
        cut = mid  # brak sensownej granicy akapitu – tniemy w połowie
    return text[:cut].strip(), text[max(0, cut - overlap):].strip()


def _extract(text, prods, model, depth=0, task="recipe_text"):
    """Surowe przepisy z modelu. Gdy odpowiedź nie mieści się w limicie tokenów
    (dużo przepisów w jednym fragmencie), dzieli tekst na połówki i przetwarza osobno."""
    content = (f"Baza produktów (dokładne nazwy): {', '.join(sorted(prods))}\n\n"
               f"Tekst do przetworzenia:\n<tekst>\n{text}\n</tekst>")
    try:
        out = ai.ask_json([{"type": "text", "text": content}], _schema(), purpose="agent przepisów",
                          task=task, model=model, effort="low", max_tokens=64000, system=SYSTEM)
    except ai.TruncatedError:
        if depth >= MAX_SPLIT_DEPTH or len(text) < 2 * SPLIT_OVERLAP:
            raise
        a, b = split_text(text)
        return _extract(a, prods, model, depth + 1, task) + _extract(b, prods, model, depth + 1, task)
    return out.get("recipes", [])


def from_text(text, source=None, model=None, task="recipe_text"):
    """Wywołuje model i zwraca listę szkiców (bez duplikatów z biblioteką i kolejką)."""
    text = (text or "").strip()
    if len(text) < 40:
        raise ValueError("Za mało tekstu – wklej cały przepis ze składnikami.")
    prods = nutrition.products_map()
    seen = known_names()
    drafts = []
    for raw in _extract(text[:60000], prods, model, task=task):
        d = build_draft(raw, source, prods)
        key = norm_name(d["recipe"]["name"])
        if not key or key in seen:
            continue
        seen.add(key)
        drafts.append(d)
    return drafts


def add_to_inbox(drafts):
    with storage.LOCK:
        items = inbox() + drafts
        save_inbox(items)
    return items


# --- Zatwierdzanie -------------------------------------------------------------------------

def _unique_id(name, items):
    t = name.lower()
    for a, b in zip("ąćęłńóśźż", "acelnoszz"):
        t = t.replace(a, b)
    base = re.sub(r"[^a-z0-9]+", "_", t).strip("_")[:40] or "przepis"
    rid, n = base, 2
    ids = {x["id"] for x in items}
    while rid in ids:
        rid, n = f"{base}_{n}", n + 1
    return rid


def accept(draft_id, edited=None):
    """Przenosi szkic do biblioteki (z nowymi produktami). Zwraca zapisany przepis."""
    with storage.LOCK:
        items = inbox()
        d = next((x for x in items if x["id"] == draft_id), None)
        if d is None:
            raise ValueError("Nie ma takiego szkicu")
        if edited is None and is_blocking(d.get("warnings")):
            raise ValueError("Szkic wymaga poprawek – otwórz go w edycji.")
        rec = dict(edited or d["recipe"])
        sv = nutrition.clean_serving(rec.get("serving") or {})
        if sv:
            rec["serving"] = sv
        else:
            rec.pop("serving", None)
        had_prep = "prep_notes" in rec
        rec.update(nutrition.instructions(rec))
        if not had_prep and not rec["prep_notes"]:
            del rec["prep_notes"]  # stary szkic – zostaje do rozpisania przez AI
        products = storage.products()
        names = storage.known_names()
        used = {i["product"] for i in rec["ingredients"]}
        for p in d.get("new_products", []):
            if p["name"].lower() not in names and p["name"] in used:
                products.append(p)
                names.add(p["name"].lower())
        missing = sorted(u for u in used if u.lower() not in names)
        if missing:
            raise ValueError("Nieznane produkty: " + ", ".join(missing))
        storage.save_products(products)
        recipes = storage.recipes()
        rec["id"] = _unique_id(rec["name"], recipes)
        rec["custom"] = True
        rec["nutrition"] = nutrition.recipe_facts(rec)
        recipes.append(rec)
        storage.save_recipes(recipes)
        save_inbox([x for x in items if x["id"] != draft_id])
        nutrition.write_markdown(recipes)
        return rec


def accept_clean():
    accepted = []
    for d in [x for x in inbox() if not x["warnings"]]:
        accepted.append(accept(d["id"]))
    return accepted


def reject(draft_id):
    with storage.LOCK:
        save_inbox([x for x in inbox() if x["id"] != draft_id])


def update(draft_id, recipe):
    with storage.LOCK:
        items = inbox()
        for d in items:
            if d["id"] == draft_id:
                rebuilt = build_draft({**recipe, "ingredients": [
                    {"product": i["product"], "grams_per_serving": i["grams"], "is_new": False}
                    for i in recipe.get("ingredients", [])]}, recipe.get("source"),
                    {**nutrition.products_map(), **{p["name"]: p for p in d.get("new_products", [])}})
                d["recipe"] = {**rebuilt["recipe"], "source": recipe.get("source") or d["recipe"].get("source")}
                if "prep_notes" not in recipe and not d["recipe"]["prep_notes"]:
                    del d["recipe"]["prep_notes"]
                d["nutrition"], d["warnings"] = rebuilt["nutrition"], rebuilt["warnings"]
                d["blocking"] = rebuilt["blocking"]
        save_inbox(items)
        return items
