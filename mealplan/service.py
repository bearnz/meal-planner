"""The operations the web app performs, kept out of the request handlers.

Nothing here knows about Flask, so the same calls work from a REPL when the
scoring needs poking at.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any, Callable

from . import paprika, store, weather
from .planner import Slot, build_plan, classify, is_dinner
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
    """Where to ask for weather: explicit coordinates, else geocode once.

    Coordinates in config win outright and are never second-guessed. That is the
    escape hatch for a postcode, which Open-Meteo's geocoder cannot resolve and,
    worse, answers anyway with an unrelated town. When they are set, "location"
    is only a label for the page.
    """
    lat_cfg, lon_cfg = config.get("latitude"), config.get("longitude")
    if lat_cfg is not None and lon_cfg is not None:
        label = str(config.get("location") or f"{lat_cfg}, {lon_cfg}")
        return float(lat_cfg), float(lon_cfg), label

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


def default_office_days(config: dict, start: date) -> list[str]:
    """Seed a fresh week's office days from config, as ISO dates.

    Config holds weekday *names* -- the usual pattern -- but a plan holds actual
    dates, because which days you are in the office changes week to week and the
    whole point is that it can be changed on the page without editing a file.
    Names would make Tuesday-the-office-day a permanent fact.
    """
    names = {str(n).strip().lower() for n in (config.get("office_days") or [])}
    return [(start + timedelta(days=i)).isoformat()
            for i in range(7)
            if store.DAY_NAMES[(start + timedelta(days=i)).weekday()] in names]


def week_slots(forecast: list[DayWeather], start: date | None = None,
               office_days: set[str] | None = None) -> list[Slot]:
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
        slots.append(Slot(day=day, weather=w,
                          day_type=classify(day, office_days or set())))
    return slots


def _servings_scale(servings: str, household: int) -> str:
    """How much of the recipe two people want, as a display string.

    A number, not a rewrite of the quantities. Paprika servings are free text
    ("4", "4-6 servings", "serves 4 generously"), so the leading number is the
    only part worth trusting -- and scaling the ingredient lines themselves runs
    straight into the same free-text problem the shopping list refuses to guess
    at. Telling you it is a half batch is useful; telling you "0.5 splashes of
    oil" is not.
    """
    if not household or not servings:
        return ""
    m = re.search(r"\d+", servings)
    if not m:
        return ""
    made = int(m.group())
    if made <= 0 or made == household:
        return ""
    ratio = household / made
    return f"{ratio:.2f}".rstrip("0").rstrip(".")


def _day_payload(choice, forecast_known: bool, household: int = 0) -> dict[str, Any]:
    s, slot = choice.scored, choice.slot
    r = s.recipe
    return {
        "servings_scale": _servings_scale(r.servings or "", household),
        "date": slot.day.isoformat(),
        "day_name": slot.day.strftime("%A"),
        # Built by hand rather than with strftime: "%-d" is a glibc extension
        # that raises on Windows, and "%d" gives a leading zero.
        "short_date": f"{slot.day.day} {slot.day.strftime('%b')}",
        "is_weekend": slot.is_weekend,
        "day_type": slot.day_type,
        "uid": s.uid,
        "name": r.name,
        "source": r.source or "",
        "source_url": r.source_url or "",
        "servings": r.servings or "",
        "total_time": r.total_time or "",
        "minutes": s.minutes,
        "hands_on": s.hands_on,
        "unattended": s.unattended,
        "domain": s.domain,
        "preferred_source": s.preferred_source,
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


def generate(config: dict, seed: int | None = None,
             office_days: list[str] | None = None) -> dict:
    library = load_library(config)
    lat, lon, pretty = resolve_location(config)
    forecast = weather.forecast(lat, lon, days=10)
    horizon = {w.day for w in forecast}
    start = week_start(forecast, config)

    # Carry the current week's office days over a re-plan. Editing them is a
    # deliberate act on the page; re-planning is not, and silently resetting
    # them to the config default would undo an edit the user can see on screen.
    if office_days is None:
        existing = store.load_plan()
        same_week = (existing.get("days") or [{}])[0].get("date") == start.isoformat()
        office_days = (existing.get("office_days") if same_week
                       else None) or default_office_days(config, start)

    slots = week_slots(forecast, start=start, office_days=set(office_days))
    recent = store.recent_uids(int(config.get("repeat_memory_weeks", 4)), start)

    choices = build_plan(library, slots, config, recent, seed=seed)
    plan = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "location": pretty,
        "household_size": int(config.get("household_size", 0) or 0),
        "office_days": sorted(office_days),
        "library_size": len(library),
        "days": [_day_payload(c, c.slot.day in horizon,
                              int(config.get("household_size", 0) or 0))
                 for c in choices],
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
    office_days = plan.get("office_days") or default_office_days(config, start)
    slots = week_slots(forecast, start=start, office_days=set(office_days))
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
        "household_size": int(config.get("household_size", 0) or 0),
        "office_days": sorted(office_days),
        "library_size": len(library),
        "days": [_day_payload(c, c.slot.day in horizon,
                              int(config.get("household_size", 0) or 0))
                 for c in choices],
    }
    store.save_plan(plan)
    store.record_history(plan)
    return plan


def toggle_office_day(config: dict, iso_day: str) -> dict:
    """Flip one day between office and not, then re-plan the week.

    A full re-plan rather than a re-roll of that one day, and deliberately so:
    marking Wednesday an office day changes what Wednesday can hold, which frees
    or blocks recipes for every other night too. Re-rolling only the toggled day
    would leave the braise sitting on Thursday that only made sense while
    Wednesday was taking the quick meal.
    """
    plan = store.load_plan()
    days = [d["date"] for d in plan.get("days", [])]
    if iso_day not in days:
        raise ValueError(f"{iso_day} is not in the current plan.")

    office = set(plan.get("office_days") or [])
    office.symmetric_difference_update({iso_day})
    return generate(config, office_days=sorted(office))


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
