"""Wspólny klient Claude API dla agentów aplikacji.

Klucz i modele: opcje dodatku HA (/data/options.json) albo zmienne środowiskowe
(lokalnie plik .env: ANTHROPIC_API_KEY, DIET_PLANNER_MODEL, DIET_PLANNER_IMPORT_MODEL).
"""
import datetime
import json
import os
import threading
import time
from pathlib import Path

import storage

OPTIONS_FILE = Path("/data/options.json")  # opcje dodatku Home Assistant
DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_IMPORT_MODEL = "claude-haiku-5-5"
# Modele z obsługą serwerowego „fallbacku” przy odmowie (refusal).
FALLBACK_MODELS = ("claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-1", "claude-fable-5-2")
# Ceny $/1M tokenów (wejście, wyjście) – do szacowania kosztu importu.
PRICES = {
    "claude-opus-5-5": (4.0, 20.0), "claude-opus-5": (5.0, 25.0), "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0), "claude-haiku-5-5": (0.10, 0.50), "claude-haiku-4-5": (1.0, 5.0),
    "claude-fable-5-2": (8.0, 40.0),
}
_usage_lock = threading.Lock()
RETRY_WAIT = (3, 10, 30)  # sekundy między kolejnymi próbami przy przerwanym połączeniu


class TransientError(ValueError):
    """Chwilowy problem z połączeniem/API – zapytanie można ponowić później."""


class TruncatedError(ValueError):
    """Odpowiedź modelu ucięta limitem max_tokens – trzeba zmniejszyć porcję danych wejściowych."""


def config():
    opts = {}
    if OPTIONS_FILE.exists():
        try:
            opts = json.loads(OPTIONS_FILE.read_text(encoding="utf-8"))
        except ValueError:
            opts = {}
    key = (opts.get("api_key") or os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    model = (opts.get("model") or os.environ.get("DIET_PLANNER_MODEL") or DEFAULT_MODEL).strip()
    imodel = (opts.get("import_model") or os.environ.get("DIET_PLANNER_IMPORT_MODEL") or DEFAULT_IMPORT_MODEL).strip()
    try:
        import anthropic  # noqa: F401
        sdk = True
    except ImportError:
        sdk = False
    return {"key": key, "model": model, "import_model": imodel, "sdk": sdk}


def ai_status():
    c = config()
    if not c["sdk"]:
        return {"available": False, "reason": "Brak biblioteki anthropic (pip install -r requirements.txt)."}
    if not c["key"]:
        return {"available": False, "reason": "Brak klucza API – dodaj go w opcjach dodatku HA (lokalnie: plik .env)."}
    return {"available": True, "model": c["model"], "import_model": c["import_model"]}


def _client():
    c = config()
    if not c["sdk"] or not c["key"]:
        raise ValueError(ai_status()["reason"])
    import anthropic
    return anthropic.Anthropic(api_key=c["key"], timeout=600, max_retries=2), c


def price(model):
    return PRICES.get(model, PRICES[DEFAULT_MODEL])


def _record(purpose, model, usage):
    p_in, p_out = price(model)
    tin = getattr(usage, "input_tokens", 0) or 0
    tout = getattr(usage, "output_tokens", 0) or 0
    cost = tin / 1e6 * p_in + tout / 1e6 * p_out
    with _usage_lock:
        f = storage.path("library", "ai_usage.json")
        data = storage.read_json(f, {"total": {"input": 0, "output": 0, "usd": 0.0}, "by_purpose": {}, "last": None})
        for bucket in (data["total"], data["by_purpose"].setdefault(purpose, {"input": 0, "output": 0, "usd": 0.0})):
            bucket["input"] += tin
            bucket["output"] += tout
            bucket["usd"] = round(bucket["usd"] + cost, 4)
        data["last"] = datetime.datetime.now().isoformat(timespec="seconds")
        storage.write_json(f, data)


def _transient(e):
    import anthropic
    if isinstance(e, (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.InternalServerError,
                      TimeoutError, ConnectionError)):
        return True
    if isinstance(e, anthropic.APIStatusError):
        return e.status_code >= 500 or e.status_code == 429
    try:
        import httpx
        if isinstance(e, (httpx.TransportError, httpx.RemoteProtocolError)):
            return True
    except ImportError:
        pass
    return "timed out" in str(e).lower()


def _short(e):
    return "przekroczony czas oczekiwania" if "timed out" in str(e).lower() or isinstance(e, TimeoutError) \
        else type(e).__name__


def ask_json(content, schema, *, purpose="inne", model=None, effort="low", max_tokens=16000, system=None):
    """Jedno zapytanie ze strukturalną odpowiedzią JSON (zgodną ze schematem)."""
    import anthropic
    client, c = _client()
    model = model or c["model"]
    kwargs = dict(model=model, max_tokens=max_tokens,
                  output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
                  messages=[{"role": "user", "content": content}])
    if system:
        kwargs["system"] = system
    resp = None
    for attempt in range(len(RETRY_WAIT) + 1):
        try:
            if model in FALLBACK_MODELS:
                # Przy odmowie API samo ponawia zapytanie na modelu zapasowym.
                with client.beta.messages.stream(betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                                                 **kwargs) as stream:
                    resp = stream.get_final_message()
            else:
                with client.messages.stream(**kwargs) as stream:
                    resp = stream.get_final_message()
            break
        except anthropic.AuthenticationError:
            raise ValueError("Nieprawidłowy klucz API.")
        except anthropic.BadRequestError as e:
            raise ValueError(f"Błędne zapytanie do API: {getattr(e, 'message', e)}")
        except Exception as e:  # noqa: BLE001
            # Przerwany strumień (timeout, zerwane połączenie), przeciążenie (5xx/529), limit zapytań:
            # czekamy i ponawiamy. Inne błędy 4xx nie mają sensu przy ponowieniu.
            status = getattr(e, "status_code", None)
            if isinstance(e, anthropic.APIStatusError) and status and status < 500 and status != 429:
                raise ValueError(f"Błąd API ({status}).")
            if not _transient(e):
                raise
            if attempt == len(RETRY_WAIT):
                if isinstance(e, anthropic.RateLimitError):
                    raise TransientError("Przekroczony limit zapytań API – spróbuj za chwilę.")
                raise TransientError(f"Brak odpowiedzi z API po {attempt + 1} próbach ({_short(e)}).")
            time.sleep(RETRY_WAIT[attempt])
    _record(purpose, model, resp.usage)
    if resp.stop_reason == "refusal":
        raise ValueError("Model odmówił przetworzenia tego zapytania.")
    if resp.stop_reason == "max_tokens":
        raise TruncatedError("Odpowiedź modelu była za długa – spróbuj ponownie.")
    text = next((b.text for b in resp.content if b.type == "text"), "{}")
    return json.loads(text)


def usage():
    return storage.read_json(storage.path("library", "ai_usage.json"),
                             {"total": {"input": 0, "output": 0, "usd": 0.0}, "by_purpose": {}, "last": None})
