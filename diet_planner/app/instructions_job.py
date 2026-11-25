"""Jednorazowe rozpisanie instrukcji (przed gotowaniem, kroki, przechowywanie) i sposobu porcjowania
dla przepisów w bibliotece i szkiców w „Do przejrzenia”, które mają jeszcze stary, skrótowy opis.

Działa w tle (wątek), paczkami po kilka przepisów. Postęp: library/instructions_job.json.
Przy zerwanym połączeniu (ai.TransientError) zadanie się wstrzymuje i po wznowieniu
zaczyna od tej samej paczki – nic nie jest pomijane.
"""
import datetime
import shutil
import threading

import ai
import nutrition
import recipe_agent
import storage

_lock = threading.RLock()
_job = {"thread": None, "cancel": False}
TOKENS_OUT_PER_RECIPE = 450


def _path():
    return storage.path("library", "instructions_job.json")


def _state():
    return storage.read_json(_path(), {"status": "idle"})


def _update(**kw):
    with _lock:
        s = _state()
        s.update(kw)
        storage.write_json(_path(), s)
        return s


def _model(choice):
    c = ai.config()
    return c["model"] if choice == "main" else c["import_model"]


def pending(skipped=()):
    """Przepisy do rozpisania: (rodzaj, id, przepis z polem id)."""
    def todo(r):
        return nutrition.needs_rewrite(r) or nutrition.needs_serving(r) or nutrition.needs_prep_plan(r)
    out = [("recipe", r["id"], r) for r in storage.recipes() if todo(r)]
    out += [("draft", d["id"], {**d["recipe"], "id": d["id"]}) for d in recipe_agent.inbox() if todo(d["recipe"])]
    return [x for x in out if x[1] not in set(skipped)]


def status():
    s = _state()
    running = bool(_job["thread"] and _job["thread"].is_alive())
    if s.get("status") == "running" and not running:
        s["status"] = "paused"  # serwer został zrestartowany w trakcie
    p = pending(s.get("skipped", []) if running else ())
    return {**s, "running": running, "pending": len(p),
            "pending_recipes": sum(1 for k, _, _ in p if k == "recipe"),
            "pending_drafts": sum(1 for k, _, _ in p if k == "draft"),
            "models": {"import": ai.config()["import_model"], "main": ai.config()["model"]}}


def estimate(choice="import"):
    items = pending()
    model = _model(choice)
    rw = [x for x in items if nutrition.needs_rewrite(x[2])]
    cl = [x for x in items if not nutrition.needs_rewrite(x[2])]
    batches = ([rw[i:i + recipe_agent.REWRITE_BATCH] for i in range(0, len(rw), recipe_agent.REWRITE_BATCH)] +
               [cl[i:i + recipe_agent.CLASSIFY_BATCH] for i in range(0, len(cl), recipe_agent.CLASSIFY_BATCH)])
    tin = sum(len(recipe_agent.rewrite_prompt([r for _, _, r in b])) / 3.2 + 1500 for b in batches)
    tout = len(rw) * (TOKENS_OUT_PER_RECIPE + 350) + len(cl) * 380
    p_in, p_out = ai.price(model)
    est = {"recipes": len(items), "batches": len(batches), "model": model, "choice": choice,
           "tokens_in": round(tin), "tokens_out": round(tout),
           "usd": round(tin / 1e6 * p_in + tout / 1e6 * p_out, 3)}
    _update(estimate=est)
    return status()


def start(choice="import"):
    with _lock:
        if _job["thread"] and _job["thread"].is_alive():
            raise ValueError("Rozpisywanie instrukcji już trwa.")
        if not ai.ai_status()["available"]:
            raise ValueError(ai.ai_status()["reason"])
        if not pending():
            raise ValueError("Wszystkie przepisy mają już rozpisane instrukcje.")
        src = storage.path("library", "recipes.json")
        bak = storage.path("library", "recipes.before-instructions.json")
        if src.exists() and not bak.exists():
            shutil.copy2(src, bak)  # kopia sprzed pierwszego przepisania
        s = _state()
        resumed = s.get("status") in ("paused", "running")
        _update(status="running", model=_model(choice), choice=choice, skipped=[],
                done=s.get("done", 0) if resumed else 0,
                errors=s.get("errors", []) if resumed else [],
                started=datetime.datetime.now().isoformat(timespec="seconds"))
        _job["cancel"] = False
        t = threading.Thread(target=_run, args=(_model(choice),), daemon=True)
        _job["thread"] = t
        t.start()
    return status()


def cancel():
    _job["cancel"] = True
    return status()


def _apply(results):
    """Zapisuje instrukcje (tylko trzy pola) do biblioteki i kolejki."""
    with storage.LOCK:
        recipes = storage.recipes()
        changed = False
        for r in recipes:
            if r["id"] in results:
                r.update(results[r["id"]])
                changed = True
        if changed:
            storage.save_recipes(recipes)
            nutrition.write_markdown(recipes)
        items = recipe_agent.inbox()
        dchanged = False
        for d in items:
            if d["id"] in results:
                d["recipe"].update(results[d["id"]])
                d["warnings"] = [w for w in d.get("warnings", []) if "sposobu przygotowania" not in w
                                 and "Sposób przygotowania" not in w]
                dchanged = True
        if dchanged:
            recipe_agent.save_inbox(items)


def _run(model):
    skipped = []
    try:
        while True:
            if _job["cancel"]:
                _update(status="paused")
                return
            todo = pending(skipped)
            rw = [x for x in todo if nutrition.needs_rewrite(x[2])]
            batch = rw[:recipe_agent.REWRITE_BATCH] or todo[:recipe_agent.CLASSIFY_BATCH]
            if not batch:
                _update(status="done", finished=datetime.datetime.now().isoformat(timespec="seconds"))
                return
            try:
                fn = recipe_agent.rewrite_instructions if rw else recipe_agent.classify_serving
                results = fn([r for _, _, r in batch], model=model)
            except ai.TransientError as e:
                s = _state()
                _update(status="paused", errors=(s.get("errors", []) + [f"{e} Wstrzymano – kliknij „Wznów”."])[-20:])
                return
            except ValueError as e:  # np. odmowa lub za długa odpowiedź – pomijamy tę paczkę w tym przebiegu
                results = {}
                s = _state()
                _update(errors=(s.get("errors", []) + [f"{', '.join(r['name'] for _, _, r in batch)}: {e}"])[-20:])
            _apply(results)
            missing = [i for _, i, _ in batch if i not in results]
            skipped += missing
            s = _state()
            _update(done=s.get("done", 0) + len(results), skipped=skipped)
    except Exception as e:  # noqa: BLE001
        s = _state()
        _update(status="paused", errors=(s.get("errors", []) + [f"Przerwano: {e} – kliknij „Wznów”."])[-20:])
    finally:
        _job["thread"] = None
