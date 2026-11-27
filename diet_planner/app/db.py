"""Baza produktów i przepisów (SQLite, library/diet.db).

Składnik przepisu wskazuje produkt po `id` (klucz obcy), więc zmiana nazwy lub scalenie produktu
przenosi się na wszystkie przepisy. Reszta aplikacji korzysta z fasady w storage.py, która zwraca
te same słowniki co dawne pliki JSON.

Tabele:
  products            – produkt (kolumny do zapytań + pełny słownik w `data`)
  product_aliases     – stare nazwy produktów po zmianie nazwy / scaleniu
  recipes             – przepis (nazwa, język + pozostałe pola w `data`)
  recipe_ingredients  – przepis ↔ produkt z gramaturą porcji bazowej
"""
import json
import sqlite3

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    dept TEXT, kcal REAL, protein REAL, carbs REAL, fat REAL, fiber REAL,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS product_aliases (
    alias TEXT PRIMARY KEY COLLATE NOCASE,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS recipes (
    id TEXT PRIMARY KEY,
    pos INTEGER NOT NULL DEFAULT 0,
    name TEXT NOT NULL,
    lang TEXT,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recipe_ingredients (
    recipe_id TEXT NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    grams REAL NOT NULL,
    PRIMARY KEY (recipe_id, position)
);
CREATE INDEX IF NOT EXISTS ix_ingredients_product ON recipe_ingredients(product_id);
"""


def connect(path):
    con = sqlite3.connect(str(path), timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    return con


def init(con):
    con.executescript(SCHEMA)
    con.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))


class tx:
    """`with tx(con):` – transakcja zapisu (BEGIN IMMEDIATE … COMMIT/ROLLBACK)."""

    def __init__(self, con):
        self.con = con

    def __enter__(self):
        self.con.execute("BEGIN IMMEDIATE")
        return self.con

    def __exit__(self, exc_type, *_):
        self.con.execute("ROLLBACK" if exc_type else "COMMIT")
        return False


def dumps(obj):
    return json.dumps(obj, ensure_ascii=False)


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
