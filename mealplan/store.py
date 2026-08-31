"""Where things live on disk: config, the recipe cache, the plan, the history.

Everything is plain JSON in data/, so any of it can be opened and edited by
hand when the planner gets something wrong.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CONFIG_PATH = ROOT / "config.json"
PRICES_PATH = ROOT / "prices.json"
CACHE_PATH = DATA / "recipes.json"
PLAN_PATH = DATA / "plan.json"
HISTORY_PATH = DATA / "history.json"
UNDO_PATH = DATA / "prefs_undo.json"
PRICE_NOTE_PATH = DATA / "price_note.json"
RTE_PATH = DATA / "rte.json"
HARVEST_LOG = DATA / "harvest.log"
HARVEST_LOCK = DATA / "harvest.lock"

DAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday",
             "saturday", "sunday"]


def load_env() -> None:
    """Read .env into os.environ without pulling in python-dotenv.

    Values already in the environment win, so a real env var can override the
    file. Blank lines and # comments are skipped; nothing is evaluated.
    """
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return default


def _write_json(path: Path, payload: Any) -> None:
    # Trailing newline, because config.json and prices.json are tracked in git
    # and edited by hand as well as by the app. Without it every save through
    # the page rewrites the last line and shows up as a diff that changed
    # nothing.
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def load_prices() -> dict:
    """The price catalogue. Missing is fine -- costing just switches off."""
    return _read_json(PRICES_PATH, {})


def load_config() -> dict:
    cfg = _read_json(CONFIG_PATH, None)
    if cfg is None:
        raise FileNotFoundError(
            f"No config.json at {CONFIG_PATH}. Copy the one that shipped with "
            f"the project, or run: python run.py --init"
        )
    if "PUT YOUR TOWN" in str(cfg.get("location", "")):
        raise ValueError(
            'config.json still says "PUT YOUR TOWN HERE". Set "location" to '
            'your town or city, e.g. "Bristol, GB".'
        )
    return cfg


def save_config(cfg: dict) -> None:
    _write_json(CONFIG_PATH, cfg)


def load_cache() -> dict:
    """{"fetched": iso8601, "categories": {...}, "recipes": [...]}"""
    return _read_json(CACHE_PATH, {})


def save_cache(payload: dict) -> None:
    _write_json(CACHE_PATH, payload)


def load_plan() -> dict:
    return _read_json(PLAN_PATH, {})


def save_plan(plan: dict) -> None:
    _write_json(PLAN_PATH, plan)


def load_history() -> list[dict]:
    """[{"date": iso, "uid": ..., "name": ...}, ...], newest last."""
    h = _read_json(HISTORY_PATH, [])
    return h if isinstance(h, list) else []


def record_history(plan: dict) -> None:
    """Fold a plan's choices into the history, replacing any same-date entries.

    Called when a plan is generated and again on every re-roll, so history
    reflects what the plan currently says rather than every candidate that was
    ever briefly on it.
    """
    history = load_history()
    dates = {d["date"] for d in plan.get("days", [])}
    history = [h for h in history if h.get("date") not in dates]
    for d in plan.get("days", []):
        if d.get("uid"):
            history.append({"date": d["date"], "uid": d["uid"],
                            "name": d.get("name", "")})
    history.sort(key=lambda h: h.get("date", ""))
    _write_json(HISTORY_PATH, history[-400:])


def load_undo() -> dict:
    """The last preferences change, so it can be reversed from the page.

    On disk rather than in a session, because the app has no sessions and does
    not want any: one user, one browser, one machine. A file also means an
    accidental save is still undoable after a restart, which is exactly when
    somebody would go looking for it.
    """
    u = _read_json(UNDO_PATH, {})
    return u if isinstance(u, dict) else {}


def save_undo(snapshot: dict) -> None:
    _write_json(UNDO_PATH, snapshot)


def clear_undo() -> None:
    """Called by any other action, so the banner never outlives its context."""
    try:
        UNDO_PATH.unlink()
    except (FileNotFoundError, OSError):
        pass


def load_rte() -> dict:
    """Recipes harvested from the web. Missing is fine -- the pool is Paprika's.

    Its own file rather than folded into the recipe cache, because the two have
    different lifetimes and different owners: `--sync` rewrites the cache from
    Paprika wholesale and would throw these away every time, and these are the
    only recipes here that nobody in this house has ever chosen.
    """
    d = _read_json(RTE_PATH, {})
    return d if isinstance(d, dict) else {}


def save_rte(payload: dict) -> None:
    _write_json(RTE_PATH, payload)


class Busy(RuntimeError):
    """Another harvest is already running."""


@contextlib.contextmanager
def harvest_lock(stale_minutes: int = 90):
    """One harvest at a time, across processes.

    It stopped being theoretical the moment the harvest went on a schedule: the
    task fires at seven, somebody presses the button at seven, and both read
    `rte.json`, both add their own recipes, and whichever finishes last writes a
    file missing the other's work. Nothing errors and nothing looks wrong -- the
    recipes are simply not there, and the `seen` map says they were read, so
    they are never fetched again.

    A file rather than a lock library, created with O_EXCL so the check and the
    claim are one operation. A stale lock is broken after `stale_minutes`,
    because the alternative is a killed process locking the feature out
    permanently and the fix being a file nobody knows to delete.
    """
    DATA.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(HARVEST_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        age_s = 1e9
        try:
            age_s = time.time() - HARVEST_LOCK.stat().st_mtime
        except OSError:
            pass
        if age_s < stale_minutes * 60:
            raise Busy(
                f"another harvest has been running for {age_s / 60:.0f} min. "
                f"If nothing is running, delete {HARVEST_LOCK}.")
        try:
            HARVEST_LOCK.unlink()
        except OSError:
            pass
        fd = os.open(HARVEST_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        try:
            HARVEST_LOCK.unlink()
        except OSError:
            pass


def log_harvest(line: str) -> None:
    """One line per run, appended. The only trace a scheduled run leaves.

    Kept small and dated rather than verbose: what a person wants six weeks
    later is "has this been working", and a progress bar written to a file
    answers that worse than one line a week does.
    """
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().isoformat(timespec="seconds")
        with open(HARVEST_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{stamp}  {line}" + chr(10))
        _trim_log()
    except OSError:
        pass          # a log that cannot be written is not a reason to fail


def _trim_log(keep: int = 400) -> None:
    """Keep the log to the last few hundred runs, so it cannot grow forever."""
    try:
        lines = HARVEST_LOG.read_text(encoding="utf-8").splitlines(keepends=True)
        if len(lines) > keep * 2:
            HARVEST_LOG.write_text("".join(lines[-keep:]), encoding="utf-8")
    except OSError:
        pass


def load_price_note() -> dict:
    """How the last price refresh went, so a redirect can still report it.

    Same reasoning as the undo snapshot above and the same shape. The app has
    no sessions and wants none, and a POST that redirects has nowhere to leave
    its answer. Rendering the outcome straight from the POST would work right
    up until somebody pressed F5 and re-fetched grocer.nz to redraw a banner,
    so the answer is written down and the redirect picks it up.
    """
    n = _read_json(PRICE_NOTE_PATH, {})
    return n if isinstance(n, dict) else {}


def save_price_note(note: dict) -> None:
    _write_json(PRICE_NOTE_PATH, note)


def clear_price_note() -> None:
    """Cleared by any other action, so the banner never outlives its context."""
    try:
        PRICE_NOTE_PATH.unlink()
    except (FileNotFoundError, OSError):
        pass


def recent_uids(weeks: int, today: date | None = None) -> dict[str, int]:
    """uid -> days since it was last eaten, for anything inside the window.

    `today` is the day the plan opens on, which is the location's date and not
    necessarily the machine's -- pass it, or a computer whose clock sits a day
    ahead of where you live counts the plan's own opening night as yesterday's
    dinner and refuses to serve it again.

    Only days that have actually been and gone count. History is written when a
    plan is generated, so it necessarily contains the coming week as well -- and
    those are intentions, not meals. Counting them meant re-planning a week made
    every recipe in the previous version of that same week look freshly eaten,
    at a penalty *above* the maximum (a date five days out gives a negative age,
    which ran the repeat cost past 1.0). The symptom was the showpiece recipe
    quietly disappearing on the second press of "re-plan", which reads as the
    planner having taken a dislike to it.
    """
    today = today or date.today()
    cutoff = today - timedelta(weeks=max(0, weeks))
    out: dict[str, int] = {}
    for h in load_history():
        try:
            when = date.fromisoformat(h["date"])
        except (KeyError, ValueError):
            continue
        if when < cutoff or when >= today:
            continue
        age = (today - when).days
        uid = h.get("uid")
        if uid and (uid not in out or age < out[uid]):
            out[uid] = age
    return out
