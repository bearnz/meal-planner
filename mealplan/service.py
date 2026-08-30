"""The operations the web app performs, kept out of the request handlers.

Nothing here knows about Flask, so the same calls work from a REPL when the
scoring needs poking at.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any, Callable

from . import (paprika, preferences, pricesrc, pricing, quantity, seasons,
               store, weather)
from .planner import Slot, build_plan, classify, is_eligible
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


def score_all(config: dict, month: int | None = None) -> list[Scored]:
    """Every cached recipe, scored. No filtering -- the preferences page needs
    to see what it is about to exclude, not only what survived."""
    cache = store.load_cache()
    raw = cache.get("recipes") or []
    if not raw:
        raise NoRecipes(
            "No recipes cached yet. Hit Sync (or run: python run.py --sync) to "
            "pull them from Paprika."
        )
    categories = cache.get("categories") or {}
    lat = float(config.get("latitude") or (config.get("_geo") or {}).get("lat") or 0.0)
    table = seasons.seasons_for(lat)
    month = month or seasons.month_for()
    return [score(paprika.Recipe.from_json(d), categories, config, table, month)
            for d in raw]


def load_library(config: dict, month: int | None = None) -> list[Scored]:
    """Scored recipes that are dinner and that the household will eat."""
    scored = score_all(config, month)
    library = [s for s in scored if is_eligible(s, config)]
    if not library:
        raise NoRecipes(
            "Every recipe was filtered out. Check the ingredient aversions on "
            "the preferences page, and exclude_categories in config.json."
        )
    return library


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


_CATALOGUE: pricing.Catalogue | None = None


def catalogue() -> pricing.Catalogue:
    """The price list, read once. Restart to pick up an edit to prices.json."""
    global _CATALOGUE
    if _CATALOGUE is None:
        _CATALOGUE = pricing.Catalogue(store.load_prices())
    return _CATALOGUE


def cost_of(recipe_json: dict, servings: int, store_name: str = "") -> dict:
    """Cost one recipe, as a payload the page can render.

    Servings is the recipe's own yield, not the household size: cost *per
    portion* is the comparable number, and dividing by how many people happen
    to be eating would make the same recipe look cheaper for a smaller family.
    """
    cat = catalogue()
    if not cat.items:
        return {}
    made = quantity.servings_factor(recipe_json.get("servings", ""), 1)[1]
    c = pricing.cost_recipe(recipe_json.get("ingredients", ""),
                            made or servings or 4, cat, store_name)
    return {
        "total": round(c.total, 2),
        "per_portion": round(c.per_portion, 2),
        "servings": c.servings,
        "coverage": round(c.coverage, 2),
        "confident": c.confident,
        "unpriced": [{"text": l.raw, "why": l.why_unpriced} for l in c.unpriced],
        "free_staples": sorted(set(c.staples_free)),
        "currency": cat.currency,
        "estimated": bool(cat.data.get("estimated")),
        "store": store_name or cat.primary,
    }


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


def _day_payload(choice, forecast_known: bool, household: int = 0,
                 store_name: str = "") -> dict[str, Any]:
    s, slot = choice.scored, choice.slot
    r = s.recipe
    return {
        "cost": cost_of(r.to_json(), 0, store_name),
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
        "in_season": s.in_season,
        "out_of_season": s.out_of_season,
        "season_score": round(s.season_score, 3),
        "avoid_methods": s.avoid_methods,
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
    lat, lon, pretty = resolve_location(config)
    forecast = weather.forecast(lat, lon, days=10)
    horizon = {w.day for w in forecast}
    start = week_start(forecast, config)
    # The month of the week being planned, not of the machine's today: a plan
    # generated on the 30th runs into the next month, and seasonality is the one
    # score where that boundary means something.
    library = load_library(config, month=start.month)

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
        "money": None,
        "days": [_day_payload(c, c.slot.day in horizon,
                              int(config.get("household_size", 0) or 0),
                              str(config.get("primary_store", "")))
                 for c in choices],
    }
    plan["money"] = money_summary(plan, config)
    store.save_plan(plan)
    store.record_history(plan)
    return plan


def reroll(config: dict, iso_day: str) -> dict:
    """Replace one day, leaving the rest of the week where it is."""
    plan = store.load_plan()
    if not plan.get("days"):
        return generate(config)

    lat, lon, pretty = resolve_location(config)
    forecast = weather.forecast(lat, lon, days=10)
    horizon = {w.day for w in forecast}

    start = date.fromisoformat(plan["days"][0]["date"])
    library = load_library(config, month=start.month)
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
        "money": None,
        "days": [_day_payload(c, c.slot.day in horizon,
                              int(config.get("household_size", 0) or 0),
                              str(config.get("primary_store", "")))
                 for c in choices],
    }
    plan["money"] = money_summary(plan, config)
    store.save_plan(plan)
    store.record_history(plan)
    return plan


def money_summary(plan: dict, config: dict) -> dict[str, Any]:
    """What the week costs at the usual shop, and what is worth leaving it for.

    An exception list, not a cheapest-basket optimiser. The ask is to shop at
    one supermarket and be told when something is worth breaking that for; a
    solver that splits a list across three stores to save four dollars answers
    a question nobody asked and costs two car trips to act on.
    """
    cat = catalogue()
    if not cat.items or not plan.get("days"):
        return {}

    primary = str(config.get("primary_store") or cat.primary)
    cache = store.load_cache()
    by_uid = {r["uid"]: r for r in (cache.get("recipes") or [])}
    ingredients = [by_uid[d["uid"]].get("ingredients", "")
                   for d in plan["days"] if d.get("uid") in by_uid]

    costs = [d.get("cost") or {} for d in plan["days"]]
    priced = [c for c in costs if c.get("per_portion")]
    people = int(config.get("household_size", 2) or 2)

    savings = pricing.find_savings(
        ingredients, cat, primary, float(config.get("min_saving", 0.50)))

    return {
        "currency": cat.currency,
        "estimated": bool(cat.data.get("estimated")),
        "updated": cat.data.get("updated", ""),
        "store": primary,
        "per_portion_avg": round(
            sum(float(c.get("per_portion", 0)) for c in priced) / len(priced), 2)
        if priced else 0.0,
        # What the week costs to feed this household, which is the number people
        # budget with -- distinct from cost per portion, which is what compares
        # two recipes to each other.
        "week_for_household": round(
            sum(float(c.get("per_portion", 0)) for c in priced) * people, 2),
        "people": people,
        "priced_days": len(priced),
        "low_confidence": sum(1 for c in costs if c and not c.get("confident")),
        "savings": [
            {"item": s.item, "store": s.store, "normal": round(s.normal, 2),
             "special": round(s.special, 2), "saved": round(s.saved, 2),
             "note": s.note, "until": s.until}
            for s in savings
        ],
        "total_saving": round(sum(s.saved for s in savings), 2),
        "freshness": pricesrc.freshness(cat.data),
    }


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


def shopping_list(plan: dict, people: int = 0) -> list[dict[str, Any]]:
    """Ingredients for the week, grouped by the night they are for, scaled.

    Still grouped rather than merged into one deduplicated list: quantities are
    free text and adding "a splash" to "2 tbsp, plus extra" has no answer.
    Scaling a single line is a different and much smaller problem -- multiply
    the leading number, leave every word alone -- which is why that half is now
    done and the merge still is not.

    Anything the parser could not read is passed through untouched and counted,
    so the page can say so. A line silently halved wrongly cannot be recovered
    from; one flagged as unscaled can be checked in two seconds.
    """
    cache = store.load_cache()
    by_uid = {r["uid"]: r for r in (cache.get("recipes") or [])}
    out = []
    for d in plan.get("days", []):
        r = by_uid.get(d.get("uid"))
        if not r:
            continue
        factor, made = quantity.servings_factor(r.get("servings", ""), people)
        lines, unscaled = quantity.scale_ingredients(r.get("ingredients", ""), factor)
        out.append({
            "day_name": d["day_name"],
            "name": d["name"],
            "factor": round(factor, 3),
            "makes": made,
            "people": people,
            "unscaled": unscaled,
            "ingredients": [
                {"text": ln.text, "scaled": ln.scaled, "original": ln.original,
                 "heading": ln.text.rstrip().endswith(":")}
                for ln in lines
            ],
        })
    return out


def preference_options(config: dict) -> dict[str, Any]:
    """What the preferences page shows: every candidate with its real cost.

    The counts are the point. "Exclude mushrooms" is a different decision when it
    removes four recipes than when it removes forty, and the only honest place to
    tell someone that is before they click rather than after.
    """
    scored = score_all(config)
    hays = [f"{r.name} {r.ingredients} {r.directions}".lower()
            for r in (s.recipe for s in scored)]

    chosen = [t.lower() for t in (config.get("avoid_ingredients") or [])]
    # Anything already chosen stays listed even at zero matches, or unticking a
    # term that has since left the library becomes impossible from the page.
    terms = list(dict.fromkeys(preferences.COMMON_INGREDIENTS + chosen))
    counts = preferences.library_counts(hays, terms)

    eligible = [x for x in scored if is_eligible(x, config)]
    return {
        "total": len(scored),
        "eligible": len(eligible),
        "ingredients": [{"term": t, "count": n, "on": t in chosen}
                        for t, n in counts],
        "methods": [
            {"key": k, "label": v["label"], "hint": v["hint"],
             "count": preferences.method_counts(hays).get(k, 0),
             "on": k in (config.get("avoid_methods") or [])}
            for k, v in preferences.METHOD_GROUPS.items()
        ],
        "people": int(config.get("household_size", 0) or 0),
    }


PREF_KEYS = ("avoid_ingredients", "avoid_methods", "household_size")


def _snapshot(config: dict) -> dict:
    return {k: config.get(k) for k in PREF_KEYS}


def save_preferences(config: dict, avoid_ingredients: list[str],
                     avoid_methods: list[str], people: int) -> dict:
    """Save preferences, recording enough to reverse it and to report it.

    An aversion removes recipes from the library outright, which is the right
    behaviour and a dangerous one to apply silently: the only sign a save had
    done anything was a count in the page header changing. A stray click during
    testing excluded an ingredient and quietly took a recipe out of the week.
    So every save now leaves a snapshot behind, and the page says what changed
    and offers to put it back.
    """
    before = _snapshot(config)
    before_size = len(load_library(config))

    config["avoid_ingredients"] = sorted({t.strip().lower()
                                          for t in avoid_ingredients if t.strip()})
    config["avoid_methods"] = [m for m in avoid_methods
                               if m in preferences.METHOD_GROUPS]
    config["household_size"] = max(1, min(int(people or 1), 50))
    store.save_config(config)

    after = _snapshot(config)
    if before == after:
        # Nothing moved, so there is nothing to announce and nothing to undo.
        # Offering "undo" for a no-op is how a banner stops being read.
        store.clear_undo()
        return config

    try:
        after_size = len(load_library(config))
    except NoRecipes:
        after_size = 0

    old_av = set(before.get("avoid_ingredients") or [])
    new_av = set(after.get("avoid_ingredients") or [])
    old_me = set(before.get("avoid_methods") or [])
    new_me = set(after.get("avoid_methods") or [])

    store.save_undo({
        "saved": datetime.now().isoformat(timespec="seconds"),
        "before": before,
        "library_before": before_size,
        "library_after": after_size,
        "added_ingredients": sorted(new_av - old_av),
        "removed_ingredients": sorted(old_av - new_av),
        "added_methods": sorted(new_me - old_me),
        "removed_methods": sorted(old_me - new_me),
        "people_before": before.get("household_size"),
        "people_after": after.get("household_size"),
    })
    return config


def undo_preferences(config: dict) -> dict:
    """Put the last preferences change back, and re-plan."""
    snap = store.load_undo()
    before = snap.get("before")
    if not before:
        raise ValueError("There is nothing to undo.")
    for key in PREF_KEYS:
        if key in before:
            config[key] = before[key]
    store.save_config(config)
    store.clear_undo()
    generate(config)
    return config
