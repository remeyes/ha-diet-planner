"""Odczyt i zapis pól w plikach Markdown diet-plannera.

Edycja jest "chirurgiczna": podmieniane są tylko wartości w istniejących
liniach, reszta pliku (nagłówki, opisy, kolejność) zostaje bez zmian.
"""
import datetime
import re
import shutil

HISTORY = "Historia zmian"
NOTES = "Uwagi dla planisty"
MEALS = "Posiłki"
READONLY_PREFIXES = ("Wspólne ograniczenia",)

BULLET_RE = re.compile(r"^- ([^:]+?):\s?(.*)$")
TODO_RE = re.compile(r"^TODO\s*(?:\((.*)\))?\s*$")
COUNT_RE = re.compile(r"^(Liczba posiłków dziennie: \*\*)(\d+)(\*\*.*)$")


def split_todo(raw):
    """Zwraca (wartość, podpowiedź). Wartość pusta, gdy pole to TODO."""
    raw = raw.strip()
    m = TODO_RE.match(raw)
    if m:
        return "", (m.group(1) or "").strip()
    if "TODO" in raw:
        hint = raw.replace("TODO", "").strip(" –-—,")
        return "", hint
    return raw, ""


def cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def is_sep(line):
    return bool(re.match(r"^\|[\s\-|:]+\|$", line.strip()))


def parse(text):
    """Parsuje plik. Zwraca dict z sekcjami, polami i (opcjonalnie) posiłkami."""
    lines = text.split("\n")
    title = ""
    section = ""
    sections = []  # [{name, fields:[...]}]
    fields = {}  # key -> info (z numerem linii)
    meals = None
    notes = None
    table_row = 0  # numer wiersza w bieżącej tabeli (0 = poza tabelą)

    def sec():
        if not sections or sections[-1]["name"] != section:
            sections.append({"name": section, "fields": []})
        return sections[-1]

    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("# ") and not title:
            title = s[2:].strip()
            continue
        if s.startswith("## "):
            section = s[3:].strip()
            table_row = 0
            if section == NOTES:
                notes = {"key": NOTES, "label": NOTES, "lines": [], "values": [], "hint": ""}
            continue
        if s.startswith("|"):
            table_row += 1
            if table_row <= 2 or is_sep(s):  # nagłówek i separator
                continue
            c = cells(s)
            if section == MEALS and len(c) == 5:
                if meals is None:
                    meals = {"rows": [], "lines": []}
                meals["lines"].append(i)
                shared, hint = split_todo(c[4])
                meals["rows"].append({
                    "name": c[1], "time": c[2],
                    "share": c[3].rstrip("%").strip(),
                    "shared": shared, "shared_raw": c[4],
                })
                continue
            if len(c) == 2:
                value, hint = split_todo(c[1])
                key = f"{section} / {c[0]}"
                info = {"key": key, "label": c[0], "value": value, "hint": hint,
                        "raw": c[1], "line": i, "kind": "table"}
                fields[key] = info
                sec()["fields"].append(key)
            continue
        table_row = 0
        if section == HISTORY or not s.startswith("- "):
            continue
        if section == NOTES and notes is not None:
            value, hint = split_todo(s[2:])
            notes["lines"].append(i)
            if value:
                notes["values"].append(value)
            elif hint:
                notes["hint"] = hint
            continue
        m = BULLET_RE.match(s)
        if not m or m.group(1).startswith(READONLY_PREFIXES):
            continue
        label, raw = m.group(1).strip(), m.group(2)
        value, hint = split_todo(raw)
        key = f"{section} / {label}"
        fields[key] = {"key": key, "label": label, "value": value, "hint": hint,
                       "raw": raw, "line": i, "kind": "bullet"}
        sec()["fields"].append(key)

    if notes is not None:
        fields[NOTES] = {"key": NOTES, "label": NOTES, "value": "\n".join(notes["values"]),
                         "hint": notes["hint"], "raw": "", "lines": notes["lines"],
                         "kind": "notes"}
        sections.append({"name": NOTES, "fields": [NOTES]})

    return {"title": title, "sections": sections, "fields": fields, "meals": meals,
            "lines": lines}


def public(parsed):
    """Wersja do wysłania przeglądarce (bez numerów linii)."""
    return {
        "title": parsed["title"],
        "sections": [s for s in parsed["sections"] if s["fields"]],
        "fields": {k: {"label": f["label"], "value": f["value"], "hint": f["hint"]}
                   for k, f in parsed["fields"].items()},
        "meals": [{k: r[k] for k in ("name", "time", "share", "shared")}
                  for r in parsed["meals"]["rows"]] if parsed["meals"] else None,
    }


def clean(value, table):
    value = (value or "").strip()
    if table:
        value = value.replace("|", "/")
    return re.sub(r"\s*\n\s*", "; ", value)


def render_value(new, info):
    new = clean(new, info["kind"] == "table")
    if new:
        return new
    if not info["value"]:  # było TODO i dalej jest – zostaw oryginał z podpowiedzią
        return info["raw"]
    return "TODO"


def apply_changes(text, new_fields, new_meals=None, today=None):
    """Zwraca (nowy_tekst, lista_zmienionych_pól)."""
    p = parse(text)
    lines = list(p["lines"])
    changed = []
    removed = set()
    inserts = {}  # nr linii -> lista linii wstawianych PRZED nią

    for key, new in (new_fields or {}).items():
        info = p["fields"].get(key)
        if info is None:
            continue
        if clean(new, False) == clean(info["value"], False):
            continue
        changed.append(info["label"])
        if info["kind"] == "notes":
            vals = [v.strip(" -") for v in (new or "").split("\n") if v.strip(" -")]
            new_lines = [f"- {v}" for v in vals] or ["- TODO"]
            first = info["lines"][0]
            for ln in info["lines"]:
                removed.add(ln)
            inserts.setdefault(first, []).extend(new_lines)
            continue
        val = render_value(new, info)
        i = info["line"]
        if info["kind"] == "table":
            lines[i] = f"| {info['label']} | {val} |"
        else:
            lines[i] = f"- {info['label']}: {val}"

    if new_meals is not None and p["meals"]:
        old = [(r["name"], r["time"], r["share"], r["shared"]) for r in p["meals"]["rows"]]
        norm = [(clean(m.get("name"), True), clean(m.get("time"), True),
                 clean(str(m.get("share", "")), True).rstrip("%").strip(),
                 clean(m.get("shared"), True)) for m in new_meals
                if clean(m.get("name"), True)]
        if norm != old:
            changed.append("posiłki")
            mlines = p["meals"]["lines"]
            for ln in mlines:
                removed.add(ln)
            rows = []
            for n, (name, time, share, shared) in enumerate(norm, 1):
                share_txt = f"{share}%" if share else "TODO"
                rows.append(f"| {n} | {name} | {time or 'TODO'} | {share_txt} | {shared or 'TODO'} |")
            inserts.setdefault(mlines[0], []).extend(rows)
            for i, line in enumerate(lines):
                m = COUNT_RE.match(line)
                if m:
                    lines[i] = f"{m.group(1)}{len(norm)}{m.group(3)}"

    if not changed:
        return text, []

    # Dopisz wpis w "Historii zmian" (po ostatnim punkcie tej sekcji).
    today = today or datetime.date.today().isoformat()
    entry = f"- {today} – formularz: zmieniono {', '.join(changed)}."
    hist_end = None
    in_hist = False
    for i, line in enumerate(lines):
        if line.strip().startswith("## "):
            in_hist = line.strip()[3:].strip() == HISTORY
            if in_hist:
                hist_end = i
            continue
        if in_hist and line.strip().startswith("- "):
            hist_end = i
    if hist_end is None:
        lines += ["", f"## {HISTORY}"]
        hist_end = len(lines) - 1
    inserts.setdefault(hist_end + 1, []).append(entry)

    out = []
    for i, line in enumerate(lines):
        out.extend(inserts.get(i, []))
        if i not in removed:
            out.append(line)
    out.extend(inserts.get(len(lines), []))
    return "\n".join(out), changed


def save_file(path, new_fields, new_meals=None):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    new_text, changed = apply_changes(text, new_fields, new_meals)
    if changed:
        shutil.copyfile(path, str(path) + ".bak")
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_text)
    return changed
