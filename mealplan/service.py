"""The operations the web app performs, kept out of the request handlers.

Nothing here knows about Flask, so the same calls work from a REPL when the
scoring needs poking at.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from . import (paprika, preferences, pricesrc, pricing, quantity, seasons,
               store, weather)
from .planner import Slot, build_plan, classify, is_eligible, is_side
from .scoring import Scored, score
from .weather import DayWeather, describe, wanted_heartiness


class NoRecipes(RuntimeError):
    pass


# What a harvested recipe says it is. `scoring.source_domain` reads this when
# there is no source_url, and `preferred_sources` matches on it -- which is the
# right answer here, since the whole reason these are worth having is that this
# household already cooks from that site.
SOURCE_LABEL = "recipetineats.com"


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

    # Recipes harvested from the web join the same list and are scored by the
    # same function, which is the whole reason they are shaped like Paprika
    # recipes rather than given a parallel pipeline. Everything downstream --
    # aversions, sides, seasonality, costing, the shopping list -- then works on
    # them without knowing they came from anywhere unusual.
    web = web_recipes(config)
    if not raw and not web:
        raise NoRecipes(
            "No recipes cached yet. Hit Sync (or run: python run.py --sync) to "
            "pull them from Paprika."
        )
    # Their categories are already names, not Paprika's uids, so they map to
    # themselves. `score` looks every category up in this dict either way.
    categories = dict(cache.get("categories") or {})
    categories.update({n: n for r in web for n in (r.get("categories") or [])})
    raw = raw + web
    lat = float(config.get("latitude") or (config.get("_geo") or {}).get("lat") or 0.0)
    table = seasons.seasons_for(lat)
    month = month or seasons.month_for()
    return [score(paprika.Recipe.from_json(d), categories, config, table, month)
            for d in raw]


def web_recipes(config: dict) -> list[dict[str, Any]]:
    """Harvested recipes, in the shape `paprika.Recipe.from_json` reads.

    Switched off by `"web_recipes": false` in config.json rather than by
    deleting the file, because the file took an hour of somebody else's
    bandwidth to build and "I want this week planned from my own library" is a
    thing you might want on Tuesday and not on Wednesday.
    """
    if not config.get("web_recipes", True):
        return []
    data = store.load_rte()
    skip_leftovers = bool(config.get("skip_leftover_recipes", True))
    out = []
    for r in (data.get("recipes") or []):
        if skip_leftovers and wants_leftovers(r.get("ingredients", "")):
            continue
        cats = list(r.get("categories") or [])
        # Only applied to recipes the site itself did not already file as
        # something other than dinner: its own word beats a guess made here.
        if not (set(cats) & set(config.get("exclude_categories") or [])
                | set(cats) & set(config.get("not_a_meal_categories") or [])):
            verdict = web_verdict(cats, r.get("name", ""))
            if not verdict:
                continue
            if verdict == "side" and "Side" not in cats:
                cats = cats + ["Side"]
        out.append({
            "uid": r.get("uid", ""),
            "name": r.get("name", ""),
            "ingredients": r.get("ingredients", ""),
            # No directions, on purpose. `method_terms` is what was kept of
            # them; see rte.py.
            "directions": "",
            "description": r.get("description", ""),
            "categories": cats,
            "prep_time": r.get("prep_time", ""),
            "cook_time": r.get("cook_time", ""),
            "total_time": r.get("total_time", ""),
            "servings": r.get("servings", ""),
            "source": SOURCE_LABEL,
            "source_url": r.get("url", ""),
            "steps": int(r.get("steps") or 0),
            "method_terms": list(r.get("method_terms") or []),
        })
    return out


_LEFTOVER_RE = re.compile(r"\bleft[- ]?overs?\b", re.I)


def wants_leftovers(ingredients: str) -> bool:
    """Whether a recipe's shopping list starts with yesterday's dinner.

    RecipeTin Eats runs a "cook once eat thrice" series, and about one recipe
    in twenty-five lists something like "2 cups leftover beef from Slow Cooked
    Beef Cheeks in Red Wine". Those are real recipes and good ones; they are
    just not things a planner can book for Thursday, because the shopping list
    it produces has a line on it that no shop sells and that only exists if you
    already cooked something else.

    Filtered here rather than at harvest time so it stays a decision this
    project makes -- `"skip_leftover_recipes": false` puts them back without
    re-reading two thousand pages to change our mind.
    """
    return any(_LEFTOVER_RE.search(line)
               for line in (ingredients or "").splitlines())


# What makes a harvested recipe plausibly a dinner. `is_dinner` keeps a recipe
# with no categories at all, which is right for a Paprika library -- an
# uncategorised library should still plan -- and exactly wrong for this one.
# Here, no tags does not mean "uncurated", it means the classifier looked at the
# page and could not place it, which is a reason to doubt the recipe rather than
# to trust it. Left alone, the planner served "Gingerbread Boxes & Mason Jars"
# on a Saturday.
PROTEIN_TAGS = {"Beef", "Chicken", "Fish", "Lamb", "Pork", "Vege"}
_HOW_TO_RE = re.compile(r"^how to\b|^how do you\b|\bguide\b", re.I)
# Facets that carry a dinner on their own, for the meals that are a bowl of
# something rather than a piece of protein.
MAIN_TAGS = {"Pasta", "Rice", "Noodles", "Curry", "Soup and stew", "Stir fry"}


def web_verdict(categories: list[str], name: str = "") -> str:
    """"dinner", "side" or "" for a harvested recipe, from its tags alone.

    Three failures in one plan drove this, and they are three shapes of the
    same mistake -- reading silence as approval:

      Gingerbread Boxes & Mason Jars   []                  a craft post
      Freezer Friendly Mexican Meals   ["Mexican"]         a round-up
      Lemon Potato Salad               ["Potato", "Salad"] a side

    So a harvested recipe has to *earn* dinner with a protein or a facet that
    is a meal on its own. A salad without a protein is a side, because a Thai
    beef salad is dinner and a potato salad is what goes next to one. Anything
    with nothing recognisable at all is not offered: it is a Christmas craft
    or a list of links, and there is no tag for that.

    Computed here rather than burned in at harvest time, so a better rule is a
    re-read of one local file instead of two thousand of somebody else's pages.
    """
    # A technique post is not a dinner however much ham is in it. Only the
    # "how to" opening and a "guide" are matched, and the narrowness is the
    # point: the obvious wider net -- a leading number, the word "meals",
    # "recipes" -- catches "12 Minute Baked Salmon Fajitas", which is just how
    # that site names a perfectly good Tuesday.
    if _HOW_TO_RE.search(name or ""):
        return "side"

    tags = set(categories or [])
    protein = tags & PROTEIN_TAGS
    if protein:
        return "dinner"
    if "Salad" in tags:
        return "side"
    if tags & MAIN_TAGS:
        return "dinner"
    return "side" if tags else ""


def recipes_by_uid(config: dict | None = None) -> dict[str, dict[str, Any]]:
    """Every recipe this app knows about, Paprika's and the web's, by uid.

    It exists because two other places built this dict from the Paprika cache
    alone, and the day harvested recipes joined the pool that turned into a
    silent hole: the planner would happily put one on Thursday, and then the
    money block could not price Thursday and the shopping list had nothing to
    buy for it. Neither failed loudly -- a recipe the lookup misses is simply
    skipped -- so the fix is to stop having two answers to "what recipes are
    there".
    """
    if config is None:
        try:
            config = store.load_config()
        except (FileNotFoundError, ValueError):
            config = {}
    out = {r["uid"]: r for r in (store.load_cache().get("recipes") or [])
           if r.get("uid")}
    out.update({r["uid"]: r for r in web_recipes(config) if r.get("uid")})
    return out


def load_library(config: dict, month: int | None = None) -> list[Scored]:
    """Scored recipes that are dinner and that the household will eat."""
    return eligible_and_held(config, month)[0]


def eligible_and_held(config: dict, month: int | None = None
                      ) -> tuple[list[Scored], list[dict[str, Any]],
                                 list[dict[str, Any]]]:
    """The library, what was held back, and what is a side dish.

    Three lists out of one scoring pass, because a recipe leaves the pool for
    three different reasons and only one of them is "no".

    Held back is temporary. Beef cheeks and picanha are not things the
    household will not eat -- they are things the butcher does not always have
    -- so a recipe that needs one is out this week and back the week the cut
    returns. Saying which is the point: a library that quietly shrinks by four
    recipes looks like a bug, and "beef cheeks are not in the shops right now"
    is a sentence somebody can act on, by marking the cut available again when
    they see it.

    A side is permanent and is not a rejection at all. Roast potatoes are not a
    dinner and are also not a mistake in the library: they are what you want on
    the night something else is roasting. So they come back named rather than
    dropped, and the page can offer them when a main asks for one.
    """
    scored = score_all(config, month)

    cat = catalogue()
    library, held, sides = [], [], []
    for s in scored:
        # The aversion is checked first and applies to sides as well: a dish
        # nobody will eat is not worth offering alongside the roast either.
        if s.excluded_by:
            continue
        if is_side(s, config):
            sides.append(_side_payload(s, config))
            continue
        if not is_eligible(s, config):
            continue
        missing = cat.unavailable_in(s.recipe.ingredients)
        if missing:
            held.append({"name": s.name, "uid": s.uid, "needs": missing})
        else:
            library.append(s)

    if not library:
        raise NoRecipes(
            "Every recipe was filtered out. Check the ingredient aversions on "
            "the preferences page, and exclude_categories in config.json."
        )
    return library, held, sorted(sides, key=lambda d: d["name"].lower())


def _side_payload(s: Scored, config: dict) -> dict[str, Any]:
    """A side, as the page needs it: what it is, and who said so.

    `by_hand` is the whole reason this is a dict and not a name. A side that
    the library tagged Side needs no explaining; one that is only a side
    because config.json says so is a decision somebody made, and the page that
    shows it is the one place they will ever be reminded it is there to undo.
    """
    return {
        "name": s.name,
        "uid": s.uid,
        "url": s.recipe.source_url or "",
        "by_hand": s.name in (config.get("side_overrides") or {}),
    }


# --- recipes from the web -----------------------------------------------------

def web_refresh_state(config: dict) -> dict[str, Any]:
    """Whether it is worth asking RecipeTin Eats what is new, without asking.

    Reads nothing but the local file, deliberately: this is called on every
    render of the plan page, and five sitemap requests per page view is not a
    thing to do to somebody because a browser tab was left open.

    `pending` is what the last run said was still unread. It is a floor, not a
    total -- the site has almost certainly published since -- and it is the
    number that matters, because it is the difference between "nothing to do"
    and "you stopped halfway through a big one".
    """
    from . import rte

    data = store.load_rte()
    recipes = data.get("recipes") or []
    fetched = str(data.get("fetched") or "")
    age = _hours_since(fetched)
    due = age >= rte.MIN_CHECK_HOURS
    return {
        "have": len(recipes),
        "in_pool": len(web_recipes(config)) if recipes else 0,
        "enabled": bool(config.get("web_recipes", True)),
        "fetched": fetched,
        "hours": round(age) if fetched else None,
        "pending": int(data.get("pending") or 0),
        # Something left over from an interrupted run is worth offering
        # whatever the clock says: the interval is about not pestering the
        # site for news, and this is work already known about.
        "ready": bool(due or data.get("pending")),
        "wait_hours": 0 if due else max(1, round(rte.MIN_CHECK_HOURS - age)),
        "source": rte.SOURCE_NAME,
        "attribution": rte.ATTRIBUTION,
    }


def _hours_since(iso: str) -> float:
    if not iso:
        return 1e9
    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        return 1e9
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - then).total_seconds() / 3600.0


def harvest_web_recipes(config: dict, limit: int = 0, refresh: bool = False,
                        progress: Callable[[int, int, str], None] | None = None
                        ) -> dict[str, Any]:
    """Add RecipeTin Eats recipes this library does not already have.

    Two skips do the real work here and both are about not wasting somebody
    else's bandwidth. Sixty-three of the ninety-nine recipes in Paprika already
    came from that site, so those pages are never fetched at all; and every page
    a previous run read is remembered against the `lastmod` it was read at, so
    an interrupted harvest resumes and an unchanged recipe is never read twice.

    `refresh` drops the second of those, which is how you pick up edits the site
    has made to recipes already on file.
    """
    from . import rte

    data = store.load_rte()
    have = {r.get("url", ""): r for r in (data.get("recipes") or []) if r.get("url")}
    seen: dict[str, str] = {} if refresh else dict(data.get("seen") or {})

    cached = [d for d in (store.load_cache().get("recipes") or [])
              if not d.get("in_trash")]
    known = rte.known_urls(cached)

    def save(partial: "rte.Harvest") -> None:
        for record in partial.added:
            have[record["url"]] = record
        seen.update(partial.seen)
        store.save_rte({
            "fetched": rte.stamp(),
            "source": {"name": rte.SOURCE_NAME, "url": rte.SOURCE_URL,
                       "attribution": rte.ATTRIBUTION},
            "recipes": sorted(have.values(), key=lambda r: r["name"].lower()),
            "seen": seen,
            # What this run knows is still unread, so the page can offer to
            # continue rather than making somebody remember they stopped.
            "pending": max(0, partial.remaining),
        })

    result = rte.harvest(rte.catalogue(), known, seen, limit=limit,
                         progress=progress, checkpoint=save)
    save(result)
    recipes = sorted(have.values(), key=lambda r: r["name"].lower())

    # How many of them the planner can actually use, which is the number worth
    # reporting: a harvest of four hundred desserts has added nothing.
    dinners = sum(1 for r in result.added
                  if not (set(r.get("categories") or [])
                          & set(config.get("exclude_categories") or [])
                          | set(r.get("categories") or [])
                          & set(config.get("not_a_meal_categories") or [])))
    return {
        "total": len(recipes),
        "harvest": {
            "added": len(result.added),
            "dinners": dinners,
            "skipped_known": result.skipped_known,
            "skipped_seen": result.skipped_seen,
            "not_recipes": len(result.not_recipes),
            "failed": result.failed,
            "remaining": result.remaining,
        },
    }


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
        # Whether this is one of yours or one nobody here has cooked. Worth
        # saying on the card: a night you have made twenty times and a night
        # off a website you have never opened are different propositions on a
        # Wednesday, and without the marker the only tell is not recognising
        # your own dinner.
        "from_web": bool(s.uid.startswith("rte:")),
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
    library, held_back, sides = eligible_and_held(config, month=start.month)

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
        "held_back": held_back,
        "sides": sides,
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
    library, held_back, sides = eligible_and_held(config, month=start.month)
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
        "held_back": held_back,
        "sides": sides,
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
    by_uid = recipes_by_uid(config)
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


# --- prices ------------------------------------------------------------------

def price_refresh_state(config: dict) -> dict[str, Any]:
    """What a refresh button needs to know before anybody presses it.

    The field that matters is `ready`. grocer.nz publishes one file per store,
    roughly daily, and `grocer.MIN_REFRESH_HOURS` refuses to re-fetch inside
    twenty hours of the last one. That guard already existed and already worked
    -- but on the command line it answers after you typed the command, which is
    fine, and on a page it would answer after you pressed a button and watched
    the page come back looking identical, which is not. So the state comes out
    here and the control renders itself from it: a button that cannot do
    anything should not look like one that can.

    Refusing rather than forcing is the honest default for a button. The
    interval is not only politeness -- inside it there is no new file to fetch,
    so forcing spends somebody else's bandwidth writing down numbers already on
    screen. `--force` stays on the command line, where whoever typed it has a
    reason and had to mean it.

    `moved` is the one exception, and it is a real one. A different store is a
    different file, so an interval that exists to stop the *same* file being
    downloaded twice has nothing to say about it. Change your shop on the
    Preferences page an hour after a refresh and every price on the page is
    still the old shop's; being told to come back tomorrow would be the app
    refusing to answer the question you just asked it.
    """
    from . import grocer

    data = store.load_prices()
    st = data.get("grocer") or {}
    pins = grocer.pinned(data)
    age = grocer.hours_since_refresh(data)
    fetched = str(st.get("fetched") or "")
    store_id = int((config.get("grocer") or {}).get("store_id") or 0)
    moved = bool(fetched) and int(st.get("store_id") or 0) != store_id
    due = age >= grocer.MIN_REFRESH_HOURS or moved
    return {
        "pinned": len(pins),
        "store_id": store_id,
        "store": str(config.get("primary_store") or ""),
        "store_name": str((config.get("grocer") or {}).get("store_name") or ""),
        # The shop the prices in the file were actually read at, which is only
        # interesting when it is not the one you now shop at.
        "was": str(st.get("store") or ""),
        "fetched": fetched,
        "hours": round(age) if fetched else None,
        "moved": moved,
        "ready": bool(pins) and bool(store_id) and due,
        "wait_hours": 0 if due else max(1, round(grocer.MIN_REFRESH_HOURS - age)),
        "interval": grocer.MIN_REFRESH_HOURS,
    }


def refresh_prices(config: dict, force: bool = False) -> dict[str, Any]:
    """Pull every pinned price from grocer.nz. One request, about 60 KB.

    The single place that knows how to do this, so the button on the page and
    `--grocer-refresh` cannot drift apart on which store they read, whether the
    interval applies, or whether the file gets written.

    Everything ordinary is reported rather than raised -- nothing pinned, too
    soon, unchanged since last week -- because each of those is a sentence to
    put on a page, not a failure. What comes back is what `grocer.refresh`
    returns plus `counts`, `store` and a one-line `headline`: the terminal wants
    the lists, and the page wants a sentence.
    """
    from . import grocer

    state = price_refresh_state(config)
    if not state["store_id"]:
        raise ValueError("No shop is set to fetch prices from. Pick one on the "
                         "Preferences page.")

    data = store.load_prices()
    before = dict(data.get("grocer") or {})
    result = grocer.refresh(data, state["store_id"], state["store"],
                            force=bool(force or state["moved"]))
    # Only written if the fetch actually got as far as happening. The refusals
    # -- nothing pinned, too soon -- return before touching anything, and
    # writing anyway rewrites prices.json from scratch: it is hand-edited and
    # hand-formatted, so a no-op press produced a four-hundred-line diff in
    # which not one price had changed. Every path that does anything, the 304
    # included, stamps `fetched`, so the grocer block is the tell.
    if (data.get("grocer") or {}) != before:
        store._write_json(store.PRICES_PATH, data)

    result["counts"] = {k: len(result.get(k) or [])
                        for k in ("updated", "missing", "skipped")}
    result["store"] = state["store_name"] or state["store"]
    result["headline"] = _refresh_headline(result)
    result["attribution"] = grocer.ATTRIBUTION
    return result


def _refresh_headline(result: dict[str, Any]) -> str:
    """One sentence about what just happened, for the top of the page.

    Nothing updated is three different things -- nothing pinned, too soon, or
    the file has not changed since last time -- and `status` already says which
    in words somebody can act on, so it is passed through rather than flattened
    into a zero.
    """
    n = result["counts"]["updated"]
    if not n:
        return str(result.get("status") or "nothing to update")
    out = f"{n} price{'' if n == 1 else 's'} updated"
    tail = [f"{result['counts'][k]} {word}"
            for k, word in (("missing", "no longer stocked"),
                            ("skipped", "left alone"))
            if result["counts"][k]]
    return out + (", " + " and ".join(tail) if tail else "")


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

    Grouped rather than merged: quantities are free text and adding "a splash"
    to "2 tbsp, plus extra" has no answer. `shopping_by_item` takes the other
    view and merges what it can, which is what the toggle on the page picks
    between -- and the reason the merge is a separate function rather than a
    flag is that it answers a different question and can only answer it partly.

    Anything the parser could not read is passed through untouched and counted,
    so the page can say so. A line silently halved wrongly cannot be recovered
    from; one flagged as unscaled can be checked in two seconds.
    """
    out = []
    for day in _shopping_days(plan, people):
        shaped = day.pop("lines")
        out.append(day | {
            # Perishables sit with the purchases, not in the cupboard block:
            # they are pre-ticked instead, which says "you probably have this"
            # without hiding it from someone standing in the vegetable aisle.
            "ingredients": [x for x in shaped if x["kind"] != "pantry"],
            "pantry": [x for x in shaped if x["kind"] == "pantry"],
        })
    return out


def shopping_by_item(plan: dict, people: int = 0) -> dict[str, Any]:
    """The same week merged into one list, an entry per ingredient.

    The other way to read the list, and the way you read it in the shop: you do
    not walk to the vegetables three times because three nights want an onion.
    So every night's lines are pooled and the ones naming the same ingredient
    are added up.

    Three things about the merge, all of them about what it refuses to do.

    Two lines merge when they resolve to the same **catalogue key**, not when
    their text is similar -- so "2 garlic cloves" and "1 tbsp minced garlic"
    land together because the price list already knows both are garlic, and the
    alias table is the one place that knowledge is written down. An ingredient
    the catalogue has never heard of is grouped by its own name and merges only
    with an identical one, which is the honest answer: guessing that "chuck
    beef" and "beef chuck" are one thing is a guess, and a wrong one puts a
    kilo of the wrong cut in the trolley.

    Amounts are added only when **every** contributing line converts into the
    item's own unit. One "a handful of parsley" in among three weighed ones
    makes the total a lie, so the total is dropped and the lines are listed as
    written. That is the same rule the costing follows for the same reason.

    Weights add; **counts are rounded up a recipe at a time and then added**,
    because two dinners each wanting half an onion want two onions. See
    `_week_total`.

    And the parts are kept whatever happens. A merged entry always says which
    nights wanted it and what each of them asked for, because a single number
    with no working is not checkable while you are holding the bag.
    """
    cat = catalogue()
    entries: dict[str, dict[str, Any]] = {}
    for day in _shopping_days(plan, people):
        for ln in day["lines"]:
            if ln["heading"]:
                continue                      # recipe structure; means nothing merged
            e = entries.setdefault(ln["group"], {
                "key": ln["group"], "name": ln["name"], "kind": ln["kind"],
                "unit": ln["unit"], "amount": 0.0, "total": True,
                "buy": ln["buy"], "parts": [], "per_night": {},
                "spec": (cat.items.get(ln["group"]) or {}),
            })
            e["parts"].append({"day_name": day["day_name"], "recipe": day["name"],
                               "text": ln["text"], "buy": ln["buy"]})
            # Deliberately the strictest kind any single night gave it, not a
            # fresh verdict on the weekly total. Seven nights of a teaspoon of
            # olive oil is not a bottle of olive oil to buy, and re-testing the
            # sum against a per-recipe allowance would put every staple in the
            # cupboard back on the list -- which is the noise the split exists
            # to remove. One night wanting 200g of butter still buys butter.
            if _KIND_ORDER[ln["kind"]] < _KIND_ORDER[e["kind"]]:
                e["kind"] = ln["kind"]
            e["buy"] = e["buy"] or ln["buy"]
            if ln["amount"] is None or ln["unit"] != e["unit"]:
                e["total"] = False
            else:
                # Banked per night rather than straight into a running total,
                # because a countable thing is rounded up a recipe at a time.
                # See `_week_total`.
                e["per_night"][day["day_name"]] = (
                    e["per_night"].get(day["day_name"], 0.0) + ln["amount"])

    items = sorted(entries.values(), key=_shelf_order)
    for e in items:
        e["amount"] = _week_total(e["per_night"], e["unit"]) if e["total"] else None
        e["qty"] = _quantity_label(e["amount"], e["unit"], e["spec"])
        e["pack"] = _pack_hint(e["amount"], e["spec"])
        e["name"] = _plural(e["name"], e["amount"], e["unit"], e["spec"])
        e["nights"] = len({p["day_name"] for p in e["parts"]})
        del e["per_night"], e["spec"]
    return {
        "ingredients": [e for e in items if e["kind"] != "pantry"],
        "pantry": [e for e in items if e["kind"] == "pantry"],
    }


def _week_total(per_night: dict[str, float], unit: str) -> float:
    """The week's amount of one ingredient, from what each night wanted.

    Weights and volumes simply add. **Counts are rounded up one recipe at a
    time and only then added**, which is the whole of the difference: two
    dinners each wanting half an onion are two onions, not one. You cut an
    onion for a recipe and the other half goes soft in the fridge -- the half
    Tuesday did not use is not the half Thursday gets.

    Rounding the week's total instead is the version that looks tidier and
    sends you home one onion short, which is the only direction of error that
    matters in a shop. The cost of being wrong the other way is a spare onion.

    It is a recipe at a time rather than a *line* at a time, so a dish calling
    for half an onion in the marinade and half in the pan still buys one.
    """
    if unit != "each":
        return round(sum(per_night.values()), 2)
    return float(sum(math.ceil(v - 1e-9) for v in per_night.values()))


def _quantity_label(amount: float | None, unit: str,
                    spec: dict | None = None) -> str:
    """How much of something to buy, written the way you would say it.

    Three things here are not formatting. A count is rounded **up**: nobody
    sells a fifth of a broccoli, so a week needing 0.2 of one needs one, and
    1.4 chicken breasts is a packet of two. Rounding to nearest would send you
    home without dinner about half the time, which is the only direction of
    error that matters in a shop. (By the time a merged entry gets here the
    rounding has usually already happened a recipe at a time -- see
    `_week_total` -- but this is also called on things that never went through
    a merge.)

    "each" is never printed. It is the catalogue's word for "this is counted,
    not weighed", which is worth knowing inside the costing and reads as
    nonsense on a list -- "0.2 each broccoli".

    And where the catalogue names the thing being counted, that noun is used
    instead: `unit_label` turns "10 garlic" into "10 cloves garlic", which is
    the only form anybody actually says. Nothing derives it, because there is
    no rule -- garlic comes in cloves, celery in stalks, broccoli in heads.
    """
    if amount is None:
        return ""
    if unit == "each":
        n = math.ceil(amount - 1e-9)
        label = str((spec or {}).get("unit_label") or "")
        if not label:
            return str(n)
        return f"{n} {label}" if n == 1 else f"{n} {label}s"
    # Grams and millilitres to the nearest whole one: a shopping list quoting
    # 60.29 g of butter is precision about an estimate.
    if amount >= 10:
        return f"{round(amount):g} {unit}".strip()
    return f"{round(amount, 1):g} {unit}".strip()


def _plural(name: str, amount: float | None, unit: str, spec: dict) -> str:
    """"4 onions", not "4 onion".

    Only for things counted singly: a weight is already plural-proof ("60 g
    butter") and an entry that names its own unit has said the countable noun
    already ("3 cloves garlic" -- the garlic is not what is being counted).

    "+s" with an escape hatch, because English plurals are not derivable and
    the alternative is either "2 cods" or a plural on every entry in the file.
    `plural` is written down only where the rule is wrong, which so far is cod
    and chillies.
    """
    if unit != "each" or amount is None or spec.get("unit_label"):
        return name
    if math.ceil(amount - 1e-9) == 1:
        return name
    return str(spec.get("plural") or (name + "s"))


def _pack_hint(amount: float | None, spec: dict) -> str:
    """What the counted thing is actually sold in, when it is not sold singly.

    Twelve cloves of garlic is the honest amount and not a thing you can pick
    up: garlic is sold in bulbs. So the count stays the headline -- it is what
    the recipes asked for -- and this says what to put in the trolley to cover
    it, rounded up for the same reason everything else here is.

    Only for the deliberately vague. Being a clove or two out on garlic does
    not matter, which is exactly why quoting it to the gram was worse than
    useless.
    """
    per = float(spec.get("pack_of") or 0)
    name = str(spec.get("pack_label") or "")
    if amount is None or not per or not name:
        return ""
    packs = math.ceil(amount / per - 1e-9)
    return f"{packs} {name}" if packs == 1 else f"{packs} {name}s"


# Which classification wins when one ingredient arrives wearing two of them
# across a week: a real purchase beats a perishable beats a cupboard item.
# Lowest wins, and it has to be this way round -- a night that needs 200g of
# butter is a night you buy butter, whatever the other four nights thought.
_KIND_ORDER = {"": 0, "perishable": 1, "pantry": 2}


def _shelf_order(entry: dict[str, Any]) -> tuple:
    """The order a merged list is walked in.

    Alphabetical for now, which is a placeholder and is meant to be one. The
    order worth having is the order the shelves are in, so the list is walked
    once from the door to the checkout instead of doubling back -- and that
    order is a property of a *store*, not of the data, so it will need a layout
    per shop before it can be anything but this. Everything that sorts the
    merged list goes through here so there is one place to change.
    """
    return (entry["name"].lower(),)


def _shopping_days(plan: dict, people: int) -> list[dict[str, Any]]:
    """Each night in the plan with its ingredient lines scaled and classified.

    Both views are built from this, so the two cannot disagree about what a
    line says, what it is really buying, or whether it is already in the
    cupboard -- which they would within a week of being written twice.
    """
    cat = catalogue()
    by_uid = recipes_by_uid()
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
            "lines": [_shape_line(ln.text, ln.scaled, ln.original, cat)
                      for ln in lines],
        })
    return out


def _shape_line(text: str, scaled: bool, original: str,
                cat: pricing.Catalogue) -> dict[str, Any]:
    """One ingredient line, read once, for whichever view is being drawn.

    Read *once* is the point. The two public callers used to ask three separate
    functions about the same line and each of them re-parsed it and re-fetched
    the catalogue; the by-item view would have made that four. Everything a
    line is asked about downstream comes off this one parse.
    """
    heading = text.rstrip().endswith(":")
    out = {"text": text, "scaled": scaled, "original": original,
           "heading": heading, "buy": {}, "kind": "", "group": "",
           "name": "", "amount": None, "unit": ""}
    if heading or not cat.items:
        out["group"] = out["name"] = text.strip()
        return out

    line = pricing.parse_line(text)
    if line.why_unpriced == "heading":
        out["heading"] = True
        out["group"] = out["name"] = text.strip()
        return out

    key = cat.key_for(line)
    spec = (cat.items.get(key) or {}) if key else {}
    amount, _why = cat.to_item_units(line, key) if key else (None, "")

    # An ingredient the price list has never heard of is grouped under its own
    # name, so it merges with an identical line and nothing else.
    out["name"] = key or pricing.short_name(line)
    out["group"] = key or f"?{out['name'].lower()}"
    out["unit"] = spec.get("unit", "") if key else ""
    out["amount"] = amount
    out["kind"] = _staple_kind(spec, amount)
    out["buy"] = _buy_from(spec)
    return out


def _staple_kind(spec: dict, amount: float | None) -> str:
    """"" to buy, "perishable" to check the fridge for, "pantry" for cupboard.

    Built on the same `staple` flag the costing uses to charge only the excess
    above an allowance, so the two cannot drift apart: anything the price list
    treats as free is the same thing the shopping list treats as already owned.

    `perishable` is the split within that. Oyster sauce and star anise sit in a
    cupboard for years and listing them weekly is how a list stops being read;
    butter, garlic and a bunch of coriander are owned in exactly the same sense
    and are still gone by Thursday. So the flag decides which of two ways an
    already-owned ingredient is shown, and neither of them is "not shown".

    A staple bought in bulk for one recipe is a real purchase either way: two
    kilos of rice for a paella is not "there is rice in the cupboard", and the
    allowance the costing already uses is exactly the right threshold. An
    amount that could not be converted is treated as small, since a line with
    no readable quantity is a pinch of something far more often than it is a
    catering pack.
    """
    if not spec.get("staple"):
        return ""
    allowance = float(spec.get("free_allowance", 0) or 0)
    if amount is not None and amount > allowance:
        return ""
    return "perishable" if spec.get("perishable") else "pantry"


def _buy_from(spec: dict) -> dict[str, Any]:
    """What to actually pick up, when it is not what the line says."""
    pin = spec.get("grocer") or {}
    out = {"product": pin.get("name", ""), "note": pin.get("note", ""),
           "yield": float(pin.get("yield", 1) or 1)}
    # A cut the shop does not always carry belongs on the list louder than
    # anywhere else: this is the page read while standing in front of the
    # cabinet it is not in. Recipes needing one are normally held out of the
    # plan entirely, so this is the case where one was pinned or planned before
    # the cut was marked -- exactly when a person needs telling.
    if spec.get("available") is False:
        out["unavailable"] = True
        return out
    if not pin.get("note") and out["yield"] >= 1.0:
        return {}
    return out


def buy_note(text: str) -> dict[str, Any]:
    """What to actually pick up for this line, when it is not what it says.

    The shopping list is the one screen where this matters, because it is the
    only one you read in the shop. A recipe asking for boneless thighs is a line
    that says "buy bone-in and bone them out" if that is how the price was
    worked out -- and if the list does not say so, the number on the plan page
    was quietly costed against a purchase nobody made.
    """
    return _shape_line(text, True, text, catalogue())["buy"]


def is_pantry(text: str) -> bool:
    """Whether this line is a cupboard item, kept off the list proper.

    Cupboard specifically, not "already owned": a perishable staple is owned in
    the same sense and still has to be looked at. See `_staple_kind`.
    """
    return _shape_line(text, True, text, catalogue())["kind"] == "pantry"


def is_perishable(text: str) -> bool:
    """Whether this line is a staple that goes off, so it stays on the list."""
    return _shape_line(text, True, text, catalogue())["kind"] == "perishable"


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
