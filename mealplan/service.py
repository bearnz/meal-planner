"""The operations the web app performs, kept out of the request handlers.

Nothing here knows about Flask, so the same calls work from a REPL when the
scoring needs poking at.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Callable

from . import paprika, store, weather
from .planner import Slot, build_plan, is_dinner
from .scoring import Scored, score
from .weather import DayWeather, describe, wanted_heartiness


class NoRecipes(RuntimeError):
    pass


# --- recipes -----------------------------------------------------------------

def sync_recipes(progress: Callable[[int, int], None] | None = None) -> dict:
    """Pull the library from Paprika and cache it. Returns the cache payload."""
    store.load_env()
    recipes, categories = paprika.fetch_all(progress=progress)
    payload = {
        "fetched": datetime.now().isoformat(timespec="seconds"),
        "categories": categories,
        "recipes": [r.to_json() for r in recipes if not r.in_trash],
    }
    store.save_cache(payload)
    return payload


def load_library(config: dict) -> list[Scored]:
    """Scored, dinner-only recipes from the cache."""
    cache = store.load_cache()
    raw = cache.get("recipes") or []
    if not raw:
        raise NoRecipes(
            "No recipes cached yet. Hit Sync (or run: python run.py --sync) to "
            "pull them from Paprika."
        )
    categories = cache.get("categories") or {}
    scored = [score(paprika.Recipe.from_json(d), categories, config) for d in raw]
    return [s for s in scored if is_dinner(s, config)]


# --- weather -----------------------------------------------------------------

def resolve_location(config: dict) -> tuple[float, float, str]:
    """Geocode once, then remember. Editing "location" re-resolves it."""
    place = config["location"]
    cached = config.get("_geo") or {}
    if cached.get("query") == place:
        return float(cached["lat"]), float(cached["lon"]), cached["name"]

    lat, lon, pretty = weather.geocode(place)
    config["_geo"] = {"query": place, "lat": lat, "lon": lon, "name": pretty}
    store.save_config(config)
    return lat, lon, pretty


# --- the week ----------------------------------------------------------------

def week_start(forecast: list[DayWeather], config: dict) -> date:
    """Which day the plan opens on.

    "today" is the first day Open-Meteo returned, not date.today(): the forecast
    comes back in the location's own timezone, and a machine whose clock is in a
    different one is a day out either side of midnight. The week you are cooking
    is the week where you are, not where the computer thinks it is.

    A weekday name instead means "start at the next one of those", which is for
    planning ahead on a Saturday for the week beginning Monday. It leaves you
    without a dinner tonight, which is why "today" is the default.
    """
    today = forecast[0].day if forecast else date.today()
    setting = str(config.get("week_starts_on", "today")).strip().lower()
    if setting in ("", "today", "now"):
        return today
    if setting not in store.DAY_NAMES:
        return today
    wanted = store.DAY_NAMES.index(setting)
    return today + timedelta(days=(wanted - today.weekday()) % 7)


def week_slots(forecast: list[DayWeather], start: date | None = None) -> list[Slot]:
    """Seven consecutive days from `start`, matched to their forecast.

    Open-Meteo gives ten days for free, so a plan generated on a Saturday still
    reaches the following Friday with real numbers rather than an average.
    """
    if start is None:
        start = forecast[0].day if forecast else date.today()
    by_day = {w.day: w for w in forecast}
    slots: list[Slot] = []
    for i in range(7):
        day = start + timedelta(days=i)
        w = by_day.get(day)
        if w is None:
            # Past the forecast horizon: fall back to the last day we have, so
            # a long week degrades to "same as the end of the week" rather than
            # to an exception.
            w = forecast[-1] if forecast else DayWeather(
                day, 15.0, 8.0, 0.0, 0, 0.0, 3)
            w = DayWeather(day, w.temp_max, w.temp_min, w.precip_mm,
                           w.precip_chance, w.wind_kmh, w.code)
        slots.append(Slot(day=day, weather=w))
    return slots


def _day_payload(choice, forecast_known: bool) -> dict[str, Any]:
    s, slot = choice.scored, choice.slot
    r = s.recipe
    return {
        "date": slot.day.isoformat(),
        "day_name": slot.day.strftime("%A"),
        # Built by hand rather than with strftime: "%-d" is a glibc extension
        # that raises on Windows, and "%d" gives a leading zero.
        "short_date": f"{slot.day.day} {slot.day.strftime('%b')}",
        "is_weekend": slot.is_weekend,
        "uid": s.uid,
        "name": r.name,
        "source": r.source or "",
        "source_url": r.source_url or "",
        "servings": r.servings or "",
        "total_time": r.total_time or "",
        "minutes": s.minutes,
        "hands_on": s.hands_on,
        "unattended": s.unattended,
        "categories": s.category_names,
        "heartiness": round(s.heartiness, 3),
        "effort": round(s.effort, 3),
        "wanted_heartiness": round(wanted_heartiness(slot.weather), 3),
        "why": choice.why,
        "reasons": s.reasons,
        "forecast_known": forecast_known,
        "weather": {
            **slot.weather.to_json(),
            "label": slot.weather.label,
            "icon": slot.weather.icon,
            "is_wet": slot.weather.is_wet,
            "summary": describe(slot.weather),
        },
    }


def generate(config: dict, seed: int | None = None) -> dict:
    library = load_library(config)
    lat, lon, pretty = resolve_location(config)
    forecast = weather.forecast(lat, lon, days=10)
    horizon = {w.day for w in forecast}
    start = week_start(forecast, config)
    slots = week_slots(forecast, start=start)
    recent = store.recent_uids(int(config.get("repeat_memory_weeks", 4)), start)

    choices = build_plan(library, slots, config, recent, seed=seed)
    plan = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "location": pretty,
        "library_size": len(library),
        "days": [_day_payload(c, c.slot.day in horizon) for c in choices],
    }
    store.save_plan(plan)
    store.record_history(plan)
    return plan


def reroll(config: dict, iso_day: str) -> dict:
    """Replace one day, leaving the rest of the week where it is."""
    plan = store.load_plan()
    if not plan.get("days"):
        return generate(config)

    library = load_library(config)
    lat, lon, pretty = resolve_location(config)
    forecast = weather.forecast(lat, lon, days=10)
    horizon = {w.day for w in forecast}

    start = date.fromisoformat(plan["days"][0]["date"])
    slots = week_slots(forecast, start=start)
    recent = store.recent_uids(int(config.get("repeat_memory_weeks", 4)), start)

    pinned = {d["date"]: d["uid"] for d in plan["days"] if d["date"] != iso_day}
    current = next((d["uid"] for d in plan["days"] if d["date"] == iso_day), None)
    exclude = {current} if current else set()

    if len(library) - len(pinned) < 1:
        raise NoRecipes("Nothing left to swap in - the library is too small.")

    choices = build_plan(library, slots, config, recent,
                         pinned=pinned, exclude=exclude)
    plan = {
        "generated": plan.get("generated"),
        "rerolled": datetime.now().isoformat(timespec="seconds"),
        "location": pretty,
        "library_size": len(library),
        "days": [_day_payload(c, c.slot.day in horizon) for c in choices],
    }
    store.save_plan(plan)
    store.record_history(plan)
    return plan


def shopping_list(plan: dict) -> list[dict[str, Any]]:
    """Ingredients for the week, grouped by the night they are for.

    Deliberately not merged into a single deduplicated list: quantities in
    Paprika are free text ("a splash", "2 tbsp, plus extra"), so adding them up
    means parsing them, and parsing them wrongly is worse than not trying.
    """
    cache = store.load_cache()
    by_uid = {r["uid"]: r for r in (cache.get("recipes") or [])}
    out = []
    for d in plan.get("days", []):
        r = by_uid.get(d.get("uid"))
        if not r:
            continue
        lines = [ln.strip() for ln in (r.get("ingredients") or "").splitlines()
                 if ln.strip()]
        out.append({"day_name": d["day_name"], "name": d["name"],
                    "ingredients": lines})
    return out
