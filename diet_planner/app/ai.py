"""Wspólny klient LLM dla agentów aplikacji: Anthropic (Claude), Google (Gemini), xAI (Grok) i OpenAI (GPT).

Każda funkcja aplikacji (zadanie, np. „agent diety”) ma przypisany model główny i opcjonalnie zapasowy
(library/ai_models.json, edycja w Ustawieniach). Model zapisujemy jako „dostawca:nazwa”, np.
„google:gemini-3.8-flash”. Gdy główny dostawca nie odpowiada albo nie ma klucza – zapytanie idzie do zapasowego.

Klucze: opcje dodatku HA (/data/options.json: anthropic_api_key / api_key, google_api_key, xai_api_key,
openai_api_key) albo zmienne środowiskowe (lokalnie plik .env: ANTHROPIC_API_KEY, GOOGLE_API_KEY / GEMINI_API_KEY,
XAI_API_KEY, OPENAI_API_KEY).
Klucze nie trafiają do przeglądarki ani do plików danych.

Odpowiedź zawsze jest JSON-em zgodnym ze schematem; i tak sprawdza ją kod aplikacji (kalorie, alergie itd.).
"""
import base64
import datetime
import importlib.util
import json
import os
import threading
import time
from pathlib import Path

import storage

OPTIONS_FILE = Path("/data/options.json")  # opcje dodatku Home Assistant
DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_IMPORT_MODEL = "claude-haiku-5-5"
PROVIDERS = {"anthropic": "Anthropic (Claude)", "google": "Google (Gemini)", "xai": "xAI (Grok)",
             "openai": "OpenAI (GPT)"}
XAI_URL = "https://api.x.ai/v1/responses"
OPENAI_URL = "https://api.openai.com/v1/responses"

# Modele podpowiadane w Ustawieniach: (nazwa, cena $/1M wejście, cena $/1M wyjście, obsługuje zdjęcia).
# Ceny wg cenników dostawców z 25.11.2026 (Gemini 4 Carbon – cena od 1.12.2026). Można wpisać też inny model.
MODELS = {
    "anthropic:claude-opus-5-5": ("Claude Opus 5.5", 4.0, 20.0, True),
    "anthropic:claude-sonnet-5-5": ("Claude Sonnet 5.5", 2.0, 10.0, True),
    "anthropic:claude-haiku-5-5": ("Claude Haiku 5.5", 0.10, 0.50, True),
    "anthropic:claude-fable-5-2": ("Claude Fable 5.2", 8.0, 40.0, True),
    "google:gemini-4-carbon": ("Gemini 4 Carbon", 4.0, 20.0, True),
    "google:gemini-4-neon": ("Gemini 4 Neon", 3.0, 15.0, True),
    "google:gemini-4-hydrogen": ("Gemini 4 Hydrogen", 1.5, 7.5, True),
    "google:gemini-3.8-flash": ("Gemini 3.8 Flash", 0.75, 3.75, True),
    "google:gemini-3.5-flash-lite": ("Gemini 3.5 Flash-Lite", 0.30, 2.50, True),
    "xai:grok-4.7": ("Grok 4.7", 2.0, 6.0, True),
    "xai:grok-4.6": ("Grok 4.6", 2.0, 6.0, True),
    "xai:grok-4.3": ("Grok 4.3", 1.25, 2.50, False),
    "openai:gpt-6.2-astra": ("GPT-6.2 Astra", 8.0, 40.0, True),
    "openai:gpt-6.2-sol": ("GPT-6.2 Sol", 2.0, 10.0, True),
    "openai:gpt-6.1-terra": ("GPT-6.1 Terra", 1.0, 5.0, True),
    "openai:gpt-6-luna": ("GPT-6 Luna", 0.10, 0.50, True),
}
# Starsze nazwy z cennikiem (zgodność ze statystykami i dawnymi ustawieniami).
EXTRA_PRICES = {"anthropic:claude-opus-5": (5.0, 25.0), "anthropic:claude-sonnet-5": (2.0, 10.0),
                "anthropic:claude-haiku-4-5": (1.0, 5.0)}
# Modele Claude z serwerowym „fallbackiem” przy odmowie (refusal).
ANTHROPIC_FALLBACK = ("claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-1", "claude-fable-5-2")
XAI_REASONING = ("grok-4.5", "grok-4.6", "grok-4.7")   # modele z parametrem reasoning effort

# Funkcje aplikacji: (nazwa w interfejsie, która dawna opcja jest domyślna, potrzebuje zdjęć)
TASKS = {
    "diet": ("Agent diety – układanie planu", "model", False),
    "recipe_text": ("Przepis z tekstu", "model", False),
    "book_import": ("Import książek kucharskich", "import_model", False),
    "recipe_enrich": ("Instrukcje, porcjowanie i meal prep", "import_model", False),
    "quick_invent": ("Szybki zamiennik – nowe danie", "model", False),
    "quick_photo": ("Zdjęcie lodówki", "model", True),
    "nutrition": ("Błonnik i wartości produktów", "import_model", False),
    "recipe_translate": ("Tłumaczenie przepisów na polski", "import_model", False),
}

_usage_lock = threading.Lock()
_local = threading.local()
RETRY_WAIT = (3, 10, 30)  # sekundy między kolejnymi próbami przy przerwanym połączeniu


class TransientError(ValueError):
    """Chwilowy problem z połączeniem/API – zapytanie można ponowić później."""


class TruncatedError(ValueError):
    """Odpowiedź modelu ucięta limitem max_tokens – trzeba zmniejszyć porcję danych wejściowych."""


class KeyProblem(ValueError):
    """Brak albo nieprawidłowy klucz API dostawcy (wtedy próbujemy modelu zapasowego)."""


# --- Konfiguracja ------------------------------------------------------------------

def _options():
    if OPTIONS_FILE.exists():
        try:
            return json.loads(OPTIONS_FILE.read_text(encoding="utf-8"))
        except ValueError:
            return {}
    return {}


def keys():
    o = _options()
    env = os.environ.get
    return {
        "anthropic": (o.get("anthropic_api_key") or o.get("api_key") or env("ANTHROPIC_API_KEY") or "").strip(),
        "google": (o.get("google_api_key") or env("GOOGLE_API_KEY") or env("GEMINI_API_KEY") or "").strip(),
        "xai": (o.get("xai_api_key") or env("XAI_API_KEY") or "").strip(),
        "openai": (o.get("openai_api_key") or env("OPENAI_API_KEY") or "").strip(),
    }


def _sdk(provider):
    mod = {"anthropic": "anthropic", "google": "google.genai", "xai": "httpx", "openai": "httpx"}[provider]
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


def provider_status():
    k = keys()
    out = {}
    for p, label in PROVIDERS.items():
        if not _sdk(p):
            out[p] = {"label": label, "ready": False, "reason": "brak biblioteki (pip install -r requirements.txt)"}
        elif not k[p]:
            out[p] = {"label": label, "ready": False, "reason": "brak klucza API"}
        else:
            out[p] = {"label": label, "ready": True, "reason": ""}
    return out


def normalize(model):
    """„dostawca:nazwa”. Bez przedrostka zgadujemy po nazwie (claude…, gemini…, grok…)."""
    m = (model or "").strip()
    if not m:
        return ""
    if ":" in m and m.split(":", 1)[0] in PROVIDERS:
        return m
    low = m.lower()
    p = ("google" if low.startswith("gemini") else "xai" if low.startswith("grok")
         else "openai" if low.startswith(("gpt", "o3", "o4")) else "anthropic")
    return f"{p}:{m}"


def split(model):
    m = normalize(model)
    return m.split(":", 1) if m else ("", "")


def label(model):
    m = normalize(model)
    return MODELS.get(m, (m.split(":", 1)[-1],))[0] if m else "—"


def config():
    """Zgodność wstecz: dawne opcje „model” i „import_model” (bez przedrostka dostawcy)."""
    o = _options()
    model = (o.get("model") or os.environ.get("DIET_PLANNER_MODEL") or DEFAULT_MODEL).strip()
    imodel = (o.get("import_model") or os.environ.get("DIET_PLANNER_IMPORT_MODEL") or DEFAULT_IMPORT_MODEL).strip()
    return {"key": keys()["anthropic"], "model": model, "import_model": imodel, "sdk": _sdk("anthropic")}


def _models_file():
    return storage.path("library", "ai_models.json")


def assignments():
    """{zadanie: {"model": "dostawca:nazwa", "fallback": "…" albo ""}} – zapisane albo domyślne."""
    c = config()
    saved = storage.read_json(_models_file(), {})
    out = {}
    for t, (_, legacy, _) in TASKS.items():
        s = saved.get(t) or {}
        out[t] = {"model": normalize(s.get("model") or c[legacy]), "fallback": normalize(s.get("fallback") or "")}
    return out


def save_assignments(data):
    cur = assignments()
    for t, v in (data or {}).items():
        if t not in TASKS or not isinstance(v, dict):
            continue
        for k in ("model", "fallback"):
            if k not in v:
                continue
            m = normalize(v.get(k))
            if k == "model" and not m:
                raise ValueError(f"{TASKS[t][0]}: wybierz model.")
            if m and (split(m)[0] not in PROVIDERS or not split(m)[1]):
                raise ValueError(f"{TASKS[t][0]}: nieznany model „{v.get(k)}”.")
            if m and TASKS[t][2] and MODELS.get(m, (0, 0, 0, True))[3] is False:
                raise ValueError(f"{TASKS[t][0]}: model {label(m)} nie obsługuje zdjęć.")
            cur[t][k] = m
        if cur[t]["fallback"] == cur[t]["model"]:
            cur[t]["fallback"] = ""
    storage.write_json(_models_file(), cur)
    return cur


def resolve(task):
    """(model główny, zapasowy albo "") dla zadania."""
    a = assignments().get(task) or {"model": normalize(config()["model"]), "fallback": ""}
    return a["model"], a["fallback"]


def ready(model):
    p, _ = split(model)
    return bool(p) and provider_status()[p]["ready"]


def ai_status(task=None):
    """Czy AI działa (dla wszystkich zadań albo jednego). Zwraca też dostawców i przypisania."""
    prov = provider_status()
    tasks = {}
    for t in TASKS:
        m, fb = resolve(t)
        tasks[t] = {"model": m, "fallback": fb, "label": label(m), "fallback_label": label(fb) if fb else "",
                    "ready": ready(m) or (bool(fb) and ready(fb))}
    if task:
        t = tasks.get(task) or {"ready": False, "model": ""}
        if t["ready"]:
            return {"available": True, "model": t["model"], "label": t["label"]}
        p = split(t["model"])[0]
        why = prov.get(p, {}).get("reason") or "brak konfiguracji"
        return {"available": False, "reason": f"{label(t['model'])}: {why} – dodaj klucz w opcjach dodatku HA "
                                              "(lokalnie: plik .env) albo zmień model w Ustawieniach."}
    any_ready = any(t["ready"] for t in tasks.values())
    reason = "" if any_ready else ("Brak klucza API – dodaj klucz Anthropic, Google, xAI albo OpenAI w opcjach dodatku HA "
                                   "(lokalnie: plik .env).")
    return {"available": any_ready, "reason": reason, "providers": prov, "tasks": tasks,
            "model": tasks["diet"]["label"], "import_model": tasks["book_import"]["label"]}


def price(model):
    """(cena wejścia, cena wyjścia) za 1M tokenów albo None, gdy nie znamy cennika."""
    m = normalize(model)
    if m in MODELS:
        return MODELS[m][1], MODELS[m][2]
    return EXTRA_PRICES.get(m)


def used_model():
    """Model, który faktycznie odpowiedział na ostatnie zapytanie w tym wątku."""
    return getattr(_local, "model", "")


# --- Zużycie -------------------------------------------------------------------------

def _record(purpose, model, tin, tout):
    pr = price(model)
    cost = (tin / 1e6 * pr[0] + tout / 1e6 * pr[1]) if pr else 0.0
    prov = split(model)[0] or "anthropic"
    with _usage_lock:
        f = storage.path("library", "ai_usage.json")
        data = storage.read_json(f, {"total": {"input": 0, "output": 0, "usd": 0.0}, "by_purpose": {}, "last": None})
        data.setdefault("by_provider", {})
        for bucket in (data["total"], data["by_purpose"].setdefault(purpose, {"input": 0, "output": 0, "usd": 0.0}),
                       data["by_provider"].setdefault(prov, {"input": 0, "output": 0, "usd": 0.0})):
            bucket["input"] += tin
            bucket["output"] += tout
            bucket["usd"] = round(bucket["usd"] + cost, 4)
            if not pr:
                bucket["unpriced"] = True
        data["last"] = datetime.datetime.now().isoformat(timespec="seconds")
        data["last_model"] = normalize(model)
        storage.write_json(f, data)


def usage():
    return storage.read_json(storage.path("library", "ai_usage.json"),
                             {"total": {"input": 0, "output": 0, "usd": 0.0}, "by_purpose": {}, "last": None})


# --- Dostawcy -------------------------------------------------------------------------
# Każdy zwraca (tekst JSON, tokeny wejścia, tokeny wyjścia) albo rzuca:
# TransientError (ponów / zapasowy), KeyProblem (zapasowy), TruncatedError, ValueError.

def _transient_net(e):
    if isinstance(e, (TimeoutError, ConnectionError)):
        return True
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


_clients = {}


def _call_anthropic(name, key, content, schema, system, effort, max_tokens):
    import anthropic
    client = _clients.get(("anthropic", key)) or anthropic.Anthropic(api_key=key, timeout=600, max_retries=2)
    _clients[("anthropic", key)] = client
    kwargs = dict(model=name, max_tokens=max_tokens,
                  output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
                  messages=[{"role": "user", "content": content}])
    if system:
        kwargs["system"] = system
    try:
        if name in ANTHROPIC_FALLBACK:
            # Przy odmowie API samo ponawia zapytanie na modelu zapasowym.
            with client.beta.messages.stream(betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                                             **kwargs) as stream:
                resp = stream.get_final_message()
        else:
            with client.messages.stream(**kwargs) as stream:
                resp = stream.get_final_message()
    except anthropic.AuthenticationError:
        raise KeyProblem("Nieprawidłowy klucz API Anthropic.")
    except anthropic.PermissionDeniedError:
        raise KeyProblem("Klucz Anthropic nie ma dostępu do tego modelu.")
    except anthropic.BadRequestError as e:
        raise ValueError(f"Błędne zapytanie do API Anthropic: {getattr(e, 'message', e)}")
    except anthropic.RateLimitError:
        raise TransientError("Przekroczony limit zapytań Anthropic – spróbuj za chwilę.")
    except anthropic.APIStatusError as e:
        if e.status_code >= 500 or e.status_code == 429:
            raise TransientError(f"Anthropic chwilowo nie odpowiada ({e.status_code}).")
        raise ValueError(f"Błąd API Anthropic ({e.status_code}).")
    except anthropic.APIConnectionError as e:
        raise TransientError(f"Brak połączenia z Anthropic ({_short(e)}).")
    except Exception as e:  # noqa: BLE001 – przerwany strumień
        if _transient_net(e):
            raise TransientError(f"Przerwane połączenie z Anthropic ({_short(e)}).")
        raise
    u = resp.usage
    tin, tout = getattr(u, "input_tokens", 0) or 0, getattr(u, "output_tokens", 0) or 0
    if resp.stop_reason == "refusal":
        raise ValueError("Model odmówił przetworzenia tego zapytania.")
    if resp.stop_reason == "max_tokens":
        raise TruncatedError("Odpowiedź modelu była za długa – spróbuj ponownie.", tin, tout)
    text = next((b.text for b in resp.content if b.type == "text"), "{}")
    return text, tin, tout


def _google_thinking(name, effort):
    """Konfiguracja „myślenia” Gemini (poziom dla Gemini 3+/4). Starsze SDK albo modele – bez ustawiania."""
    from google.genai import types
    if "thinking_level" not in types.ThinkingConfig.model_fields:
        return None
    if not (name.startswith("gemini-3") or name.startswith("gemini-4")):
        return None
    lvl = {"low": "low", "medium": "medium", "high": "high"}.get(effort, "low")
    return types.ThinkingConfig(thinking_level=lvl)


def _call_google(name, key, content, schema, system, effort, max_tokens):
    from google import genai
    from google.genai import errors, types
    client = _clients.get(("google", key)) or genai.Client(api_key=key)
    _clients[("google", key)] = client
    parts = []
    for b in content:
        if b.get("type") == "text":
            parts.append(b["text"])
        elif b.get("type") == "image":
            src = b["source"]
            parts.append(types.Part.from_bytes(data=base64.b64decode(src["data"]), mime_type=src["media_type"]))
    cfg = {"response_mime_type": "application/json", "response_json_schema": schema,
           "max_output_tokens": max_tokens}
    if system:
        cfg["system_instruction"] = system
    th = _google_thinking(name, effort)
    if th is not None:
        cfg["thinking_config"] = th
    try:
        resp = client.models.generate_content(model=name, contents=parts, config=types.GenerateContentConfig(**cfg))
    except errors.APIError as e:
        code = getattr(e, "code", None) or 0
        if code in (401, 403):
            raise KeyProblem(f"Nieprawidłowy klucz Google albo brak dostępu ({code}).")
        if code == 429 or code >= 500:
            raise TransientError(f"Google chwilowo nie odpowiada ({code}).")
        raise ValueError(f"Błędne zapytanie do API Google ({code}): {getattr(e, 'message', e)}")
    except Exception as e:  # noqa: BLE001
        if _transient_net(e):
            raise TransientError(f"Brak połączenia z Google ({_short(e)}).")
        raise
    um = resp.usage_metadata
    tin = (getattr(um, "prompt_token_count", 0) or 0) if um else 0
    tout = ((getattr(um, "candidates_token_count", 0) or 0) + (getattr(um, "thoughts_token_count", 0) or 0)) if um else 0
    cand = (resp.candidates or [None])[0]
    finish = getattr(getattr(cand, "finish_reason", None), "name", "") if cand else ""
    if finish == "MAX_TOKENS":
        raise TruncatedError("Odpowiedź modelu była za długa – spróbuj ponownie.", tin, tout)
    if finish in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION", "IMAGE_SAFETY"):
        raise ValueError(f"Model Google odmówił odpowiedzi ({finish}).", tin, tout)
    return resp.text or "{}", tin, tout


RESPONSES_API = {  # dostawcy z tym samym REST Responses API: (adres, nazwa, modele z reasoning.effort)
    "xai": (XAI_URL, "xAI", lambda n: n in XAI_REASONING),
    "openai": (OPENAI_URL, "OpenAI", lambda n: n.startswith(("gpt-5", "gpt-6", "o3", "o4"))),
}
# 429 z wyczerpanym budżetem/kredytami – ponawianie nic nie da, od razu model zapasowy.
_NO_CREDIT = ("credit_balance_exhausted", "spend_limit", "usage_limit", "insufficient_quota")


def _call_xai(name, key, content, schema, system, effort, max_tokens):
    return _call_responses("xai", name, key, content, schema, system, effort, max_tokens)


def _call_openai(name, key, content, schema, system, effort, max_tokens):
    return _call_responses("openai", name, key, content, schema, system, effort, max_tokens)


def _call_responses(provider, name, key, content, schema, system, effort, max_tokens):
    """xAI i OpenAI przez udokumentowane REST Responses API (structured outputs: text.format = json_schema)."""
    import httpx
    url, who, reasoning = RESPONSES_API[provider]
    parts = []
    for b in content:
        if b.get("type") == "text":
            parts.append({"type": "input_text", "text": b["text"]})
        elif b.get("type") == "image":
            src = b["source"]
            parts.append({"type": "input_image", "image_url": f"data:{src['media_type']};base64,{src['data']}"})
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": parts}]
    body = {"model": name, "input": msgs, "max_output_tokens": max_tokens,
            "text": {"format": {"type": "json_schema", "name": "odpowiedz", "schema": schema, "strict": True}}}
    if provider == "openai":
        body["store"] = False  # nie przechowujemy odpowiedzi po stronie OpenAI
    if reasoning(name):
        body["reasoning"] = {"effort": effort if effort in ("low", "medium", "high") else "low"}
    try:
        r = httpx.post(url, json=body, headers={"Authorization": f"Bearer {key}"}, timeout=600)
    except Exception as e:  # noqa: BLE001
        if _transient_net(e):
            raise TransientError(f"Brak połączenia z {who} ({_short(e)}).")
        raise
    if r.status_code in (401, 403):
        raise KeyProblem(f"Nieprawidłowy klucz {who} albo brak dostępu ({r.status_code}).")
    if r.status_code == 429 and any(w in r.text for w in _NO_CREDIT):
        raise KeyProblem(f"{who}: wyczerpany limit wydatków albo kredyty.")
    if r.status_code == 429 or r.status_code >= 500:
        raise TransientError(f"{who} chwilowo nie odpowiada ({r.status_code}).")
    if r.status_code >= 400:
        try:
            msg = r.json().get("error") or r.text
        except ValueError:
            msg = r.text
        raise ValueError(f"Błędne zapytanie do API {who} ({r.status_code}): {str(msg)[:300]}")
    j = r.json()
    u = j.get("usage") or {}
    tin, tout = int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0)
    if j.get("status") == "incomplete":
        reason = (j.get("incomplete_details") or {}).get("reason", "")
        if "token" in reason:
            raise TruncatedError("Odpowiedź modelu była za długa – spróbuj ponownie.", tin, tout)
        raise ValueError(f"{who} nie dokończył odpowiedzi ({reason or 'incomplete'}).")
    texts = [c.get("text", "") for item in j.get("output") or [] if item.get("type") == "message"
             for c in item.get("content") or [] if c.get("type") == "output_text"]
    if not texts and j.get("output_text"):
        texts = [j["output_text"]]
    if not texts:
        refusal = [c.get("refusal") for item in j.get("output") or [] for c in item.get("content") or []
                   if c.get("type") == "refusal"]
        raise ValueError("Model odmówił przetworzenia tego zapytania." if refusal else f"Pusta odpowiedź {who}.")
    return "".join(texts), tin, tout


_CALLS = {"anthropic": _call_anthropic, "google": _call_google, "xai": _call_xai, "openai": _call_openai}


def _ask_one(model, content, schema, system, effort, max_tokens, purpose):
    """Jeden model z ponawianiem przy chwilowych błędach. Zwraca dict."""
    p, name = split(model)
    if p not in _CALLS:
        raise ValueError(f"Nieznany dostawca modelu „{model}”.")
    if not _sdk(p):
        raise KeyProblem(f"{PROVIDERS[p]}: brak biblioteki (pip install -r requirements.txt).")
    key = keys()[p]
    if not key:
        raise KeyProblem(f"{PROVIDERS[p]}: brak klucza API.")
    for attempt in range(len(RETRY_WAIT) + 1):
        try:
            text, tin, tout = _CALLS[p](name, key, content, schema, system, effort, max_tokens)
            break
        except TransientError as e:
            if attempt == len(RETRY_WAIT):
                raise TransientError(f"{e} Brak odpowiedzi po {attempt + 1} próbach.")
            time.sleep(RETRY_WAIT[attempt])
        except (TruncatedError, ValueError) as e:
            if len(e.args) >= 3:  # zużyte tokeny przy uciętej/odrzuconej odpowiedzi też liczymy
                _record(purpose, model, e.args[1], e.args[2])
                raise type(e)(e.args[0])
            raise
    _record(purpose, model, tin, tout)
    try:
        return json.loads(text)
    except ValueError:
        raise ValueError(f"{label(model)} zwrócił niepoprawny JSON.")


def ask_json(content, schema, *, purpose="inne", task=None, model=None, effort="low", max_tokens=16000, system=None):
    """Zapytanie ze strukturalną odpowiedzią JSON (zgodną ze schematem).

    `task` wybiera model główny i zapasowy z ustawień; `model` (opcjonalnie) wymusza konkretny model,
    a zapasowym zostaje wtedy zapasowy model zadania. Przy braku klucza albo chwilowej awarii
    dostawcy zapytanie przechodzi na model zapasowy."""
    primary, fallback = resolve(task) if task else (normalize(config()["model"]), "")
    if model:
        primary = normalize(model)
    order = [m for m in (primary, fallback) if m]
    order = list(dict.fromkeys(order))
    last = None
    for i, m in enumerate(order):
        try:
            out = _ask_one(m, content, schema, system, effort, max_tokens, purpose)
            _local.model = m
            return out
        except (TransientError, KeyProblem) as e:
            last = e
            if i + 1 < len(order):
                continue
            raise
    raise last or ValueError("Brak modelu do wykonania zapytania.")


def test_model(model):
    """Krótki test modelu: zwraca czas odpowiedzi i to, czy JSON zgadza się ze schematem."""
    schema = {"type": "object", "additionalProperties": False, "required": ["ok", "slowo"],
              "properties": {"ok": {"type": "boolean"}, "slowo": {"type": "string"}}}
    t0 = time.time()
    out = _ask_one(normalize(model), [{"type": "text", "text": "Zwróć ok=true i słowo „marchewka”."}], schema,
                   "Odpowiadasz wyłącznie JSON-em zgodnym ze schematem.", "low", 2000, "test modelu")
    return {"ok": out.get("ok") is True and isinstance(out.get("slowo"), str), "seconds": round(time.time() - t0, 1),
            "model": normalize(model), "label": label(model)}
