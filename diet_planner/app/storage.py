"""Katalog danych, dane startowe i bezpieczny zapis plików."""
import json
import os
import re
import shutil
import threading
from pathlib import Path

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
        dst = DATA / rel
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
    path("plans").mkdir(parents=True, exist_ok=True)


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


# --- Biblioteka ---------------------------------------------------------------

def products():
    return read_json(path("library", "products.json"), [])


def recipes():
    return read_json(path("library", "recipes.json"), [])


def save_products(items):
    write_json(path("library", "products.json"), items)


def save_recipes(items):
    write_json(path("library", "recipes.json"), items)


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
