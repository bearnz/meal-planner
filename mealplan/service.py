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
    """Pull the library from Paprika and cache it. Returns the cache payload.

    The previous cache is handed to the client so unchanged recipes are not
    re-fetched.

    The cache is a faithful mirror of the library, trash included, and the
    trash is filtered on the way *out* in `score_all`. Filtering it on the way
    in is the obvious version and it quietly defeats the incremental sync: a
    trashed recipe left out of the cache has no hash to compare against, so all
    twelve of them were re-downloaded on every single sync, forever.
    """
    store.load_env()
    old = {r["uid"]: r for r in (store.load_cache().get("recipes") or [])
           if r.get("uid")}
    recipes, categories, stats = paprika.fetch_all(progress=progress, cached=old)
    payload = {
        "fetched": datetime.now().isoformat(timespec="seconds"),
        "categories": categories,
        "recipes": [r.to_json() for r in recipes],
        "sync": dict(stats, trashed=sum(1 for r in recipes if r.in_trash)),
    }
    store.save_cache(payload)
    return payload


def score_all(config: dict, month: int | None = None) -> list[Scored]:
    """Every cached recipe, scored.

    No filtering beyond the trash -- the preferences page needs to see what it
    is about to exclude, not only what survived. The trash is not a preference:
    it is a recipe the library says has been thrown away, and the cache holds it
    only so the sync can compare hashes against it.
    """
    cache = store.load_cache()
    raw = [d for d in (cache.get("recipes") or []) if not d.get("in_trash")]
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
        shop = ""
        try:
            shop = str(store.load_config().get("primary_store") or "")
        except (FileNotFoundError, ValueError):
            pass          # no config yet; the first listed store will do
        _CATALOGUE = pricing.Catalogue(store.load_prices(), shop)
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
        "usable": c.usable,
        # Two different doubts, kept apart on purpose. `coverage` is how much of
        # the recipe got a price at all; `verified` is how much of that price
        # was real rather than a shipped guess. A night can be fully covered and
        # entirely invented, and the page has to be able to say so.
        "verified": round(c.verified_share, 2),
        # Named, so a night with no figure can say what is missing rather than
        # only that something is. "the beef" is actionable; "3 ingredients" is
        # a shrug.
        "major_unpriced": c.major_unpriced,
        "unpriced": [{"text": l.raw, "why": l.why_unpriced} for l in c.unpriced],
        "free_staples": sorted(set(c.staples_free)),
        "currency": cat.currency,
        "estimated": bool(cat.data.get("estimated")),
        "store": store_name or cat.primary,
    }


def _servings_label(servings: str) -> str:
    """Paprika's servings text, with its own label taken off the front.

    The page already writes "serves" before this, and a real library has
    "Servings 4", "Serves 4" and "6 to 8 servings" in it -- so printed raw it
    reads "serves Servings 4". Only the wrapper words go; anything the recipe
    says beyond a number is kept, because "6 to 8" and "4 generously" are worth
    seeing and are not ours to round.
    """
    text = re.sub(r"^\s*(?:servings?|serves)\b[:\s]*", "", servings or "",
                  flags=re.I)
    text = re.sub(r"\s*\bservings?\b\s*$", "", text, flags=re.I)
    return text.strip() or servings.strip()


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
        "servings": _servings_label(r.servings or ""),
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

    # Re-costed here rather than read off the saved day, and the plan's own days
    # are updated in place so the cards and the week's total cannot disagree.
    # A saved cost is a photograph of prices.json at plan time: refresh prices
    # and every night still showed the old figure, with the header confidently
    # reporting how little had been verified. Same bug as the money block above
    # it, one level down, and the cheapest fix is to stop storing the answer.
    for d in plan["days"]:
        r = by_uid.get(d.get("uid"))
        if r:
            d["cost"] = cost_of(r, 0, primary)

    costs = [d.get("cost") or {} for d in plan["days"]]
    # Only days with a number worth showing feed the week's figures. A day we
    # could price a third of is not a cheap day, and averaging it in makes the
    # whole week look cheaper than it is -- which is the one direction a food
    # budget must not be wrong in.
    priced = [c for c in costs if c.get("usable") and c.get("per_portion")]
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
        "days": len(costs),
        "unpriced_days": sum(1 for c in costs if c and not c.get("usable")),
        "low_confidence": sum(1 for c in costs if c and c.get("usable")
                              and not c.get("confident")),
        "savings": [
            {"item": s.item, "store": s.store, "normal": round(s.normal, 2),
             "special": round(s.special, 2), "saved": round(s.saved, 2),
             "note": s.note, "until": s.until}
            for s in savings
        ],
        "total_saving": round(sum(s.saved for s in savings), 2),
        "freshness": pricesrc.freshness(cat.data, primary),
        # Weighted by what each night costs, not averaged over nights: a cheap
        # night priced from receipts and an expensive one guessed do not each
        # contribute half of "how real is this number".
        "verified": (
            round(sum(float(c.get("total") or 0) * float(c.get("verified") or 0)
                      for c in costs if c and c.get("usable"))
                  / max(1e-9, sum(float(c.get("total") or 0)
                                  for c in costs if c and c.get("usable"))), 2)
            if any(c and c.get("usable") for c in costs) else 0.0),
        # Where the shelf prices came from, if anywhere. The page shows this as
        # attribution, which is the condition the data comes on rather than a
        # nicety -- so it is carried in the payload and not left to the template
        # to know about.
        "source": cat.data.get("grocer") or {},
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
        shaped = [{"text": ln.text, "scaled": ln.scaled, "original": ln.original,
                   "heading": ln.text.rstrip().endswith(":"),
                   "buy": buy_note(ln.text), "pantry": is_pantry(ln.text)}
                  for ln in lines]
        out.append({
            "day_name": d["day_name"],
            "name": d["name"],
            "factor": round(factor, 3),
            "makes": made,
            "people": people,
            "unscaled": unscaled,
            "ingredients": [x for x in shaped if not x["pantry"]],
            "pantry": [x for x in shaped if x["pantry"]],
        })
    return out


def buy_note(text: str) -> dict[str, Any]:
    """What to actually pick up for this line, when it is not what it says.

    The shopping list is the one screen where this matters, because it is the
    only one you read in the shop. A recipe asking for boneless thighs is a line
    that says "buy bone-in and bone them out" if that is how the price was
    worked out -- and if the list does not say so, the number on the plan page
    was quietly costed against a purchase nobody made.
    """
    cat = catalogue()
    if not cat.items:
        return {}
    line = pricing.parse_line(text)
    if line.why_unpriced == "heading":
        return {}
    key = cat.match(line.item_raw or line.item) or cat.match(line.item)
    pin = ((cat.items.get(key) or {}).get("grocer") or {}) if key else {}
    if not pin.get("note") and float(pin.get("yield", 1) or 1) >= 1.0:
        return {}
    return {"product": pin.get("name", ""), "note": pin.get("note", ""),
            "yield": float(pin.get("yield", 1) or 1)}


def is_pantry(text: str) -> bool:
    """Whether this line is something already in the cupboard.

    Split out of the shopping list rather than deleted from it. Oyster sauce,
    rice, herbs and spices are not weekly purchases -- listing them every week
    is how a list stops being read, which is the whole complaint. But they do
    run out, and a recipe wanting three tablespoons of oyster sauce when there
    is none is a failed dinner, so the page keeps them where they can be
    glanced at instead of dropping them on the floor.

    This is the same `staple` flag the costing already uses to charge only the
    excess above an allowance, so the two cannot drift apart: anything the
    price list treats as free is the same thing the shopping list treats as
    already owned.
    """
    cat = catalogue()
    if not cat.items:
        return False
    line = pricing.parse_line(text)
    if line.why_unpriced == "heading":
        return False
    key = cat.match(line.item_raw or line.item) or cat.match(line.item)
    if not key:
        return False
    spec = cat.items.get(key) or {}
    if not spec.get("staple"):
        return False
    # A staple bought in bulk for one recipe is a real purchase: two kilos of
    # rice for a paella is not "there is rice in the cupboard". The allowance
    # the costing already uses is exactly the right threshold.
    amount, _why = cat.to_item_units(line, key)
    if amount is None:
        return True
    return amount <= float(spec.get("free_allowance", 0) or 0)


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
        "store": _store_options(config),
    }


def _store_options(config: dict) -> dict[str, Any]:
    """The store picker: which shop prices are read from.

    Only chains prices.json has a column for are offered. A Fresh Choice store
    would fetch and price perfectly and then be costed against nothing, because
    nothing in the file has a Fresh Choice price -- a setting that appears to
    work and quietly does nothing is worse than one that is not there.
    """
    from . import grocer

    chosen = int((config.get("grocer") or {}).get("store_id") or 0)
    groups: dict[str, list[dict]] = {}
    for s in grocer.load_stores():
        column = grocer.store_name_for(s.get("vendor", ""))
        if not column:
            continue
        groups.setdefault(column, []).append(
            {"id": s["id"], "name": s["name"], "on": s["id"] == chosen})

    priced = catalogue().data.get("grocer") or {}
    return {
        "groups": [{"vendor": v, "stores": sorted(g, key=lambda x: x["name"])}
                   for v, g in sorted(groups.items())],
        "chosen": chosen,
        "chosen_name": (grocer.store_by_id(chosen) or {}).get("name", ""),
        # Which store the prices currently in the file actually came from, which
        # is not the same question as which one is selected.
        "priced_store_id": int(priced.get("store_id") or 0),
        "priced_store": priced.get("store") or "",
        "fetched": (priced.get("fetched") or "")[:10],
    }


PREF_KEYS = ("avoid_ingredients", "avoid_methods", "household_size")


def _snapshot(config: dict) -> dict:
    """What a save is compared against to decide whether anything changed.

    The store belongs in here even though it lives under a nested key: leave
    it out and changing only the shop compares equal, the save is treated as
    a no-op, and the page says nothing happened while the config on disk has
    quietly moved.
    """
    snap = {k: config.get(k) for k in PREF_KEYS}
    snap["store_id"] = int((config.get("grocer") or {}).get("store_id") or 0)
    return snap


def _set_store(config: dict, store_id: int) -> None:
    """Point the price fetcher at a shop, and the costing at its chain.

    Two settings move together here, and forgetting the second is the whole
    reason this is a function. `grocer.store_id` says which shelf to read;
    `primary_store` says which column of prices.json the week is costed
    against. Set the first alone and the app fetches Royal Oak's prices into
    the PAK'nSAVE column while costing the week against New World, which is a
    plan priced at a shop you did not choose.
    """
    from . import grocer

    g = config.setdefault("grocer", {})
    if not store_id:
        g["store_id"] = 0
        g["store_name"] = ""
        return
    row = grocer.store_by_id(store_id)
    if not row:
        return
    column = grocer.store_name_for(row.get("vendor", ""))
    if not column:
        # A chain with no price column would fetch happily and cost nothing.
        return
    g["store_id"] = int(store_id)
    g["store_name"] = row.get("name", "")
    config["primary_store"] = column


def save_preferences(config: dict, avoid_ingredients: list[str],
                     avoid_methods: list[str], people: int,
                     store_id: int | None = None) -> dict:
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
    if store_id is not None:
        _set_store(config, int(store_id))
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
