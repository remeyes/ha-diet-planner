"""Import przepisów z książek kucharskich (PDF z tekstem, EPUB, MOBI/AZW3).

Pliki leżą w katalogu danych `books/`. Tekst jest dzielony na fragmenty, a każdy
fragment trafia do agenta przepisów. Postęp zapisuje się w library/books.json,
więc przerwany import można wznowić.
"""
import hashlib
import posixpath
import re
import shutil
import threading
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET

import ai
import recipe_agent
import storage

EXTS = {".pdf", ".epub", ".mobi", ".azw3", ".azw"}
CHUNK = 12000
OVERLAP = 800
_lock = threading.RLock()
_job = {"thread": None, "cancel": False, "book": None}


class BookError(ValueError):
    pass


# --- Stan -------------------------------------------------------------------------

def books_dir():
    d = storage.path("books")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _state():
    return storage.read_json(storage.path("library", "books.json"), {})


def _save_state(s):
    storage.write_json(storage.path("library", "books.json"), s)


def _update(name, **kw):
    with _lock:
        s = _state()
        s.setdefault(name, {}).update(kw)
        _save_state(s)
        return s[name]


def _hash(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()[:16]


def list_books():
    s = _state()
    out = []
    for f in sorted(books_dir().iterdir()):
        if f.suffix.lower() not in EXTS or not f.is_file():
            continue
        st = s.get(f.name, {})
        if st.get("size") != f.stat().st_size:  # nowy albo podmieniony plik
            st = {"status": "new"}
        out.append({"file": f.name, "size": f.stat().st_size, "title": st.get("title") or f.stem,
                    "status": st.get("status", "new"), "chunks_total": st.get("chunks_total", 0),
                    "chunks_done": st.get("chunks_done", 0), "drafts": st.get("drafts", 0),
                    "errors": st.get("errors", []), "estimate": st.get("estimate"),
                    "running": _job["book"] == f.name})
    return out


def recover():
    """Po restarcie serwera przerwane importy oznacza jako wstrzymane."""
    with _lock:
        s = _state()
        for st in s.values():
            if st.get("status") == "importing":
                st["status"] = "paused"
        _save_state(s)


def save_upload(filename, stream, length):
    name = Path(filename).name
    if Path(name).suffix.lower() not in EXTS:
        raise BookError("Obsługiwane formaty: PDF, EPUB, MOBI, AZW3.")
    if length > 300 * 1024 * 1024:
        raise BookError("Plik jest za duży (limit 300 MB).")
    dst = books_dir() / name
    tmp = dst.with_name(dst.name + ".part")
    left = length
    with open(tmp, "wb") as f:
        while left > 0:
            block = stream.read(min(1 << 20, left))
            if not block:
                break
            f.write(block)
            left -= len(block)
    if left:
        tmp.unlink()
        raise BookError("Przesyłanie przerwane.")
    tmp.replace(dst)
    return name


def delete_book(name):
    f = books_dir() / Path(name).name
    if f.exists():
        f.unlink()
    with _lock:
        s = _state()
        s.pop(f.name, None)
        _save_state(s)


# --- Odczyt tekstu ------------------------------------------------------------------

class _Text(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "table", "ul", "ol"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.title, self._in_title, self._skip = [], "", False, 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag in ("h1", "h2", "title"):
            self._in_title = True

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag in ("h1", "h2", "title"):
            self._in_title = False

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_title and not self.title.strip():
            self.title = data.strip()
        self.parts.append(data)

    def text(self):
        t = "".join(self.parts)
        t = re.sub(r"[ \t\xa0]+", " ", t)
        return re.sub(r"\n\s*\n+", "\n\n", t).strip()


def html_to_text(data):
    p = _Text()
    p.feed(data)
    return p.title, p.text()


def read_epub(path):
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise BookError("Plik EPUB jest uszkodzony.")
    names = set(z.namelist())
    if "META-INF/encryption.xml" in names:
        enc = z.read("META-INF/encryption.xml").decode("utf-8", "ignore")
        # Zaszyfrowane fonty są dozwolone, zaszyfrowana treść (DRM) – nie.
        if re.search(r'URI="[^"]+\.(x?html?|xml)"', enc):
            raise BookError("Ten EPUB jest zabezpieczony DRM – nie da się odczytać treści.")
    ns = {"c": "urn:oasis:names:tc:opendocument:xmlns:container", "o": "http://www.idpf.org/2007/opf",
          "dc": "http://purl.org/dc/elements/1.1/"}
    opf_path = ET.fromstring(z.read("META-INF/container.xml")).find(".//c:rootfile", ns).get("full-path")
    opf = ET.fromstring(z.read(opf_path))
    base = posixpath.dirname(opf_path)
    title_el = opf.find(".//dc:title", ns)
    manifest = {i.get("id"): i.get("href") for i in opf.findall(".//o:manifest/o:item", ns)}
    sections = []
    for ref in opf.findall(".//o:spine/o:itemref", ns):
        href = manifest.get(ref.get("idref"))
        if not href:
            continue
        full = posixpath.normpath(posixpath.join(base, urllib_unquote(href)))
        if full not in names:
            continue
        t, txt = html_to_text(z.read(full).decode("utf-8", "ignore"))
        if txt.strip():
            sections.append((t, txt))
    return (title_el.text.strip() if title_el is not None and title_el.text else None), sections


def urllib_unquote(s):
    from urllib.parse import unquote
    return unquote(s)


def read_pdf(path):
    try:
        from pypdf import PdfReader
    except ImportError:
        raise BookError("Brak biblioteki pypdf (pip install -r requirements.txt).")
    try:
        r = PdfReader(str(path))
        if r.is_encrypted and not r.decrypt(""):
            raise BookError("Ten PDF jest zabezpieczony hasłem/DRM.")
    except BookError:
        raise
    except Exception as e:  # noqa: BLE001
        raise BookError(f"Nie udało się otworzyć PDF: {e}")
    title = None
    try:
        title = (r.metadata or {}).get("/Title")
    except Exception:  # noqa: BLE001
        pass
    sections, buf, first = [], [], 1
    for i, page in enumerate(r.pages, 1):
        try:
            buf.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001
            buf.append("")
        if sum(len(x) for x in buf) >= CHUNK or i == len(r.pages):
            sections.append((f"s. {first}–{i}", "\n".join(buf)))
            buf, first = [], i + 1
    text_len = sum(len(t) for _, t in sections)
    if text_len < 200 * max(1, len(r.pages)) * 0.1:
        raise BookError("PDF prawie nie zawiera tekstu (to chyba skan) – obsługiwane są PDF-y z tekstem.")
    return title, sections


def read_mobi(path):
    try:
        import mobi
    except ImportError:
        raise BookError("Brak biblioteki mobi (pip install -r requirements.txt).")
    try:
        tmpdir, out = mobi.extract(str(path))
    except Exception as e:  # noqa: BLE001
        raise BookError(f"Nie udało się otworzyć pliku (możliwy DRM): {e}")
    try:
        out = Path(out)
        if out.suffix.lower() == ".epub":
            return read_epub(out)
        if out.suffix.lower() == ".pdf":
            return read_pdf(out)
        t, txt = html_to_text(out.read_text(encoding="utf-8", errors="ignore"))
        return t, [(t, txt)]
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def read_book(path):
    ext = Path(path).suffix.lower()
    if ext == ".epub":
        return read_epub(path)
    if ext == ".pdf":
        return read_pdf(path)
    if ext in (".mobi", ".azw3", ".azw"):
        return read_mobi(path)
    raise BookError("Nieobsługiwany format.")


def chunk_sections(sections, size=CHUNK, overlap=OVERLAP):
    """Łączy sekcje w fragmenty ~size znaków, dzieląc po akapitach, z zakładką."""
    chunks, cur, cur_title = [], "", None
    for title, text in sections:
        for para in re.split(r"\n\s*\n", text):
            para = para.strip()
            if not para:
                continue
            while len(para) > size:  # bardzo długi akapit
                chunks.append((title, (cur + "\n\n" + para[:size]).strip()))
                cur, para = "", para[size - overlap:]
            if len(cur) + len(para) + 2 > size and cur:
                chunks.append((cur_title, cur))
                cur = cur[-overlap:]
            if not cur.strip():
                cur_title = title
            cur = (cur + "\n\n" + para).strip()
    if cur.strip():
        chunks.append((cur_title, cur))
    return chunks


# --- Szacowanie i import -----------------------------------------------------------------

def prepare(name):
    path = books_dir() / Path(name).name
    if not path.exists():
        raise BookError("Nie ma takiego pliku.")
    size = path.stat().st_size
    try:
        title, sections = read_book(path)
    except BookError as e:
        _update(path.name, status="error", size=size, errors=[str(e)])
        raise
    chunks = chunk_sections(sections)
    if not chunks:
        _update(path.name, status="error", size=size, errors=["Nie znaleziono tekstu w pliku."])
        raise BookError("Nie znaleziono tekstu w pliku.")
    return path, (title or path.stem), chunks, size


def estimate(name):
    path, title, chunks, size = prepare(name)
    model = ai.config()["import_model"]
    chars = sum(len(t) for _, t in chunks)
    prods = len(storage.products())
    tin = chars / 3.2 + len(chunks) * (1500 + prods * 6)
    tout = chars / 3.2 * 0.35 + len(chunks) * 300
    p_in, p_out = ai.price(model)
    est = {"chunks": len(chunks), "chars": chars, "model": model,
           "tokens_in": round(tin), "tokens_out": round(tout),
           "usd": round(tin / 1e6 * p_in + tout / 1e6 * p_out, 3)}
    prev = _state().get(path.name, {})
    keep = prev.get("hash") == _hash(path) and prev.get("chunks_total") == len(chunks)
    _update(path.name, title=title, size=size, hash=_hash(path), estimate=est, chunks_total=len(chunks),
            status=prev.get("status") if keep and prev.get("status") not in (None, "error") else "ready",
            chunks_done=prev.get("chunks_done", 0) if keep else 0,
            drafts=prev.get("drafts", 0) if keep else 0, errors=prev.get("errors", []) if keep else [])
    return est


def start_import(name):
    with _lock:
        if _job["thread"] and _job["thread"].is_alive():
            raise BookError(f"Trwa już import książki „{_job['book']}”. Poczekaj albo go zatrzymaj.")
        if not ai.ai_status()["available"]:
            raise BookError(ai.ai_status()["reason"])
        path, title, chunks, size = prepare(name)
        st = _state().get(path.name, {})
        if st.get("hash") != _hash(path) or st.get("chunks_total") != len(chunks):
            estimate(path.name)
            st = _state()[path.name]
        _job.update(cancel=False, book=path.name)
        _update(path.name, status="importing")
        t = threading.Thread(target=_run, args=(path.name, title, chunks, st.get("chunks_done", 0)), daemon=True)
        _job["thread"] = t
        t.start()


def cancel():
    _job["cancel"] = True


def _run(name, title, chunks, start):
    model = ai.config()["import_model"]
    errors = list(_state().get(name, {}).get("errors", []))
    try:
        for i in range(start, len(chunks)):
            if _job["cancel"]:
                _update(name, status="paused")
                return
            chapter, text = chunks[i]
            try:
                drafts = recipe_agent.from_text(text, {"book": title, "file": name, "chapter": chapter}, model=model)
                if drafts:
                    recipe_agent.add_to_inbox(drafts)
                st = _state().get(name, {})
                _update(name, chunks_done=i + 1, drafts=st.get("drafts", 0) + len(drafts))
            except ai.TransientError as e:
                # Fragment NIE jest pomijany: import się wstrzymuje i po wznowieniu zacznie od niego.
                errors.append(f"Fragment {i + 1}: {e} Import wstrzymany – kliknij „Wznów import”.")
                _update(name, status="paused", errors=errors[-20:])
                return
            except ValueError as e:
                msg = str(e)
                errors.append(f"Fragment {i + 1}: {msg}")
                _update(name, errors=errors[-20:], chunks_done=i + 1)
                if any(w in msg for w in ("klucz", "Brak biblioteki", "limit")):
                    _update(name, status="paused")
                    return
        _update(name, status="done")
    except Exception as e:  # noqa: BLE001
        errors.append(f"Przerwano: {e} – kliknij „Wznów import”.")
        _update(name, status="paused", errors=errors[-20:])
    finally:
        _job["book"] = None

