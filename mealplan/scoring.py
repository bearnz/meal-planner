"""Turn a Paprika recipe into two numbers: how hearty it is, and how much work.

Both run 0..1. Everything here is a heuristic over text the recipe already
carries, so it is wrong sometimes; config.json has "heartiness_overrides" and
"effort_overrides" keyed by recipe name for when it is.

The keyword tables are meant to be edited. A weight is a nudge, not a verdict:
several small ones agreeing is what moves a score, so adding a word you care
about is safe and adding it at +0.9 is not.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from . import preferences, seasons
from .paprika import Recipe

# --- heartiness ---------------------------------------------------------------
# Positive pushes towards stew-in-a-bowl; negative towards salad-on-a-plate.
# Matched as whole words against the recipe's name, categories, ingredients and
# directions, so "cream" hits "double cream" but not "creamy" -- add both if you
# want both.

HEARTY_TERMS: dict[str, float] = {
    # dishes that are the answer on their own
    "stew": 0.45, "casserole": 0.40, "hotpot": 0.40, "hot pot": 0.40,
    "cassoulet": 0.40, "ragu": 0.35, "ragout": 0.35, "lasagne": 0.35,
    "lasagna": 0.35, "shepherds pie": 0.45, "cottage pie": 0.45,
    "pot roast": 0.45, "goulash": 0.40, "chilli": 0.30, "chili": 0.30,
    "curry": 0.25, "tagine": 0.35, "gratin": 0.30,
    "pie": 0.30, "cobbler": 0.30, "risotto": 0.25, "carbonara": 0.30,
    "mac and cheese": 0.40, "macaroni cheese": 0.40, "chowder": 0.30,
    "broth": 0.15, "soup": 0.10,
    "dumpling": 0.30, "dumplings": 0.30, "pudding": 0.25, "meatball": 0.20,
    "meatballs": 0.20, "pilaf": 0.15, "biryani": 0.20, "paella": 0.15,
    "stock": 0.10,
    # filling ingredients
    "potato": 0.20, "potatoes": 0.20, "mash": 0.22, "mashed": 0.20,
    "suet": 0.30, "pastry": 0.25, "shortcrust": 0.25, "puff pastry": 0.22,
    "cream": 0.15, "double cream": 0.18, "butter": 0.08, "cheese": 0.12,
    "cheddar": 0.12, "gruyere": 0.15, "bacon": 0.15, "lardons": 0.18,
    "sausage": 0.20, "sausages": 0.20, "chorizo": 0.15, "beef": 0.20,
    "brisket": 0.30, "shin": 0.30, "oxtail": 0.35, "lamb": 0.22,
    "shoulder": 0.20, "belly": 0.25, "pork": 0.18, "duck": 0.20,
    "lentil": 0.18, "lentils": 0.18, "barley": 0.22, "chickpea": 0.12,
    "chickpeas": 0.12, "butter bean": 0.18, "cannellini": 0.15,
    "polenta": 0.15, "gnocchi": 0.20,
    "rice": 0.05, "pasta": 0.08, "bread": 0.08, "noodle": 0.05,
}

LIGHT_TERMS: dict[str, float] = {
    "salad": -0.45, "slaw": -0.25, "ceviche": -0.45, "poke": -0.40,
    "gazpacho": -0.45, "carpaccio": -0.40, "tartare": -0.35,
    "wrap": -0.25, "wraps": -0.25, "taco": -0.25, "tacos": -0.25,
    "skewer": -0.25, "skewers": -0.25, "kebab": -0.20, "kebabs": -0.20,
    "bruschetta": -0.30, "crudites": -0.35, "summer roll": -0.35,
    "stir fry": -0.20, "stir-fry": -0.20, "sushi": -0.35,
    # light ingredients
    "cucumber": -0.20, "watermelon": -0.30, "lime": -0.10, "lemon": -0.08,
    "rocket": -0.20, "arugula": -0.20, "watercress": -0.20, "lettuce": -0.25,
    "mint": -0.12, "coriander": -0.08, "cilantro": -0.08, "basil": -0.08,
    "prawn": -0.15, "prawns": -0.15, "shrimp": -0.15, "squid": -0.15,
    "tuna": -0.12, "seabass": -0.15, "sea bass": -0.15, "cod": -0.10,
    "white fish": -0.12, "mackerel": -0.08, "courgette": -0.15,
    "zucchini": -0.15, "asparagus": -0.15, "tomato salad": -0.30,
    "yoghurt": -0.10, "yogurt": -0.10, "vinaigrette": -0.25,
    "feta": -0.12, "mozzarella": -0.08, "avocado": -0.12,
}

# Cooking methods, scored apart from the tables above because **only the
# strongest match counts**. A recipe that says "roast", "oven" and "bake" has
# stated one fact three times, and summing synonyms was enough to read a lemon
# chicken traybake as heartier than a beef stew. Ingredients genuinely stack --
# cream *and* potatoes *and* suet is three separate reasons a dish is heavy --
# but a dish only has one main method.
HEARTY_METHODS: dict[str, float] = {
    "braise": 0.40, "braised": 0.40, "casserole dish": 0.35,
    "slow cooker": 0.35, "slow-cooked": 0.35, "slow roast": 0.35,
    "pot roast": 0.40, "dutch oven": 0.30,
    "roast": 0.28, "roasting tin": 0.28, "simmer": 0.20,
    "bake": 0.18, "oven": 0.15, "slow": 0.10,
}

LIGHT_METHODS: dict[str, float] = {
    "no-cook": -0.40, "raw": -0.30, "chilled": -0.30,
    "grill": -0.22, "grilled": -0.22, "barbecue": -0.22, "bbq": -0.22,
    "griddle": -0.18, "steam": -0.14, "steamed": -0.14,
    "blanch": -0.10, "poach": -0.10,
}

# --- effort -------------------------------------------------------------------
# Effort blends four signals. Paprika's own difficulty field wins when it is set,
# because that is the one signal a human authored.

DIFFICULTY_MAP = {
    "easy": 0.12, "simple": 0.12, "quick": 0.10,
    "medium": 0.5, "moderate": 0.5, "average": 0.5,
    "hard": 0.88, "difficult": 0.88, "challenging": 0.9, "advanced": 0.9,
}

# Terms that mean "this needs planning ahead", which is effort a clock misses.
FAFF_TERMS: dict[str, float] = {
    "marinate": 0.12, "overnight": 0.18,
    "rest for": 0.06, "prove": 0.15, "proving": 0.15, "knead": 0.12,
    "chill for": 0.10, "refrigerate for": 0.10, "soak": 0.10,
    "reduce by half": 0.06, "confit": 0.18, "sous vide": 0.20,
    "deep fry": 0.12, "deep-fry": 0.12, "temper": 0.12, "clarify": 0.12,
    "food processor": 0.05, "stand mixer": 0.06, "piping bag": 0.10,
    "from scratch": 0.10,
}

# Terms that mean the opposite: it looks long, but the oven does the work.
HANDS_OFF_TERMS: dict[str, float] = {
    "slow cooker": -0.22, "slow-cooker": -0.22, "crock pot": -0.22,
    "instant pot": -0.15, "pressure cooker": -0.12,
    "one pot": -0.15, "one-pot": -0.15, "one pan": -0.15, "one-pan": -0.15,
    "traybake": -0.18, "tray bake": -0.18, "sheet pan": -0.18,
    "leave to simmer": -0.08, "cover and cook": -0.08, "no-cook": -0.20,
}

# Dishes that can be started long before you want to eat, and left. This is a
# different question from effort and has to be asked separately: a three-hour
# oven braise is barely any *work*, and it still cannot happen on a Tuesday,
# because nobody is home at 3pm to put it in. A slow cooker at the same total
# time is a fine Tuesday -- it goes on before work. Without this distinction the
# planner reads both as "30 minutes hands-on, easy weeknight" and books you a
# stew you would sit down to at half past ten.
UNATTENDED_TERMS = [
    "slow cooker", "slow-cooker", "crock pot", "crockpot", "slow cook",
    "overnight", "make ahead", "make-ahead", "day before", "night before",
    "freezer", "batch cook",
]

# Longest alternative first, and it is not cosmetic: "h|hr|hrs" matches the bare
# "h" of "hrs" and leaves "rs 30 mins" for the minutes group, which then matches
# nothing, so "3 hrs 30 mins" read as 180.
_TIME_RE = re.compile(
    r"(?:(\d+)\s*(?:hours|hour|hrs|hr|h)\b)?\s*"
    r"(?:(\d+)\s*(?:minutes|minute|mins|min|m)\b)?",
    re.I,
)


def parse_minutes(text: str) -> int | None:
    """'1 hr 20 mins' -> 80. Returns None when there is nothing to read."""
    if not text:
        return None
    t = text.strip().lower()
    m = _TIME_RE.search(t)
    if m and (m.group(1) or m.group(2)):
        return int(m.group(1) or 0) * 60 + int(m.group(2) or 0)
    bare = re.search(r"\d+", t)
    return int(bare.group()) if bare else None


def _norm(value: float, low: float, high: float) -> float:
    """Map value onto 0..1 across [low, high], clamped."""
    if high <= low:
        return 0.0
    return max(0.0, min(1.0, (value - low) / (high - low)))


def _haystack(r: Recipe, category_names: list[str]) -> str:
    parts = [r.name, " ".join(category_names), r.ingredients, r.directions,
             r.description, r.notes]
    return " ".join(p for p in parts if p).lower()


def _term_score(haystack: str, table: dict[str, float]) -> float:
    total = 0.0
    for term, weight in table.items():
        pattern = r"\b" + re.escape(term).replace(r"\ ", r"\s+") + r"\b"
        if re.search(pattern, haystack):
            total += weight
    return total


def _method_score(haystack: str, table: dict[str, float]) -> float:
    """The single strongest method match, not the sum of all of them."""
    hits = [w for term, w in table.items()
            if re.search(r"\b" + re.escape(term).replace(r"\ ", r"\s+") + r"\b",
                         haystack)]
    # By magnitude, so the same helper serves the positive and negative tables.
    return max(hits, key=abs) if hits else 0.0


def count_steps(directions: str) -> int:
    lines = [ln.strip() for ln in (directions or "").splitlines() if ln.strip()]
    return len(lines) or (1 if (directions or "").strip() else 0)


def count_ingredients(ingredients: str) -> int:
    lines = [ln.strip() for ln in (ingredients or "").splitlines() if ln.strip()]
    # Paprika lets you write bare section headings ending in a colon; those are
    # not ingredients, and a recipe organised into sections is not a longer shop.
    return len([ln for ln in lines if not ln.rstrip().endswith(":")])


@dataclass
class Scored:
    recipe: Recipe
    heartiness: float
    effort: float
    minutes: int | None          # wall clock, start to plate
    hands_on: int | None         # of which, actually standing at the hob
    unattended: bool             # can be started hours ahead and left
    preferred_source: bool       # from a site you like cooking from
    domain: str                  # where it came from, for display
    season_score: float          # -0.35..1.0, how in-season its produce is
    in_season: list[str]         # produce at its best right now
    out_of_season: list[str]     # produce that is not
    avoid_methods: list[str]     # avoided cooking methods it uses
    excluded_by: list[str]       # aversions that rule it out entirely
    category_names: list[str]
    reasons: list[str]

    @property
    def uid(self) -> str:
        return self.recipe.uid

    @property
    def name(self) -> str:
        return self.recipe.name


def score_heartiness(r: Recipe, category_names: list[str]) -> tuple[float, list[str]]:
    hay = _haystack(r, category_names)
    raw = (_term_score(hay, HEARTY_TERMS) + _term_score(hay, LIGHT_TERMS)
           + _method_score(hay, HEARTY_METHODS)
           + _method_score(hay, LIGHT_METHODS))

    # A long cook is itself evidence of a heavy dish.
    minutes = parse_minutes(r.total_time) or parse_minutes(r.cook_time)
    if minutes:
        raw += _norm(minutes, 30, 180) * 0.25

    # Squash the unbounded keyword sum onto 0..1 around a neutral 0.5. tanh
    # rather than a clamp, so a recipe hitting fifteen hearty words is not
    # pinned to the same 1.0 as one hitting six.
    #
    # The gain is deliberately low. At 1.1 the whole hearty half of a library
    # bunched between 0.93 and 1.00, and a planner that cannot tell a casserole
    # from a beef shin stew has nothing to choose between them on a mild day --
    # which is most days. Spreading the top matters more than reaching it.
    value = 0.5 + 0.5 * math.tanh(raw * 0.7)

    reasons: list[str] = []
    if raw > 0.45:
        reasons.append("rich, slow or filling ingredients")
    elif raw < -0.45:
        reasons.append("fresh, quick or raw ingredients")
    if minutes and minutes >= 90:
        reasons.append(f"{minutes} min cook")
    return max(0.0, min(1.0, value)), reasons


def score_effort(r: Recipe, category_names: list[str]) -> tuple[float, list[str]]:
    hay = _haystack(r, category_names)
    reasons: list[str] = []

    total_min = parse_minutes(r.total_time)
    prep_min = parse_minutes(r.prep_time)
    cook_min = parse_minutes(r.cook_time)
    if total_min is None and (prep_min or cook_min):
        total_min = (prep_min or 0) + (cook_min or 0)

    # Hands-on time is the honest measure of effort: a three-hour braise with
    # fifteen minutes of prep is an easy Tuesday, so prep is weighted over total.
    hands_on = prep_min if prep_min is not None else total_min
    time_score = _norm(hands_on, 15, 75) if hands_on is not None else 0.4
    steps = count_steps(r.directions)
    steps_score = _norm(steps, 3, 14)
    ingr_score = _norm(count_ingredients(r.ingredients), 6, 20)

    value = 0.42 * time_score + 0.33 * steps_score + 0.25 * ingr_score
    value += _term_score(hay, FAFF_TERMS)

    # Hands-off is applied as a discount rather than a subtraction, because the
    # prep time above has already counted most of it: a traybake's ten minutes
    # of prep is *why* it scores low, and taking another flat 0.18 off drove a
    # 55-minute traybake to exactly zero -- below a salad you only assemble.
    # As a discount it does nothing to something already trivial and a lot to a
    # long, involved-looking recipe that turns out to be one pan.
    discount = _term_score(hay, HANDS_OFF_TERMS)
    value *= max(0.35, 1.0 + discount)

    # An authored difficulty is worth more than anything we inferred, but not
    # everything -- it says nothing about a forty-ingredient "easy" recipe.
    diff = DIFFICULTY_MAP.get((r.difficulty or "").strip().lower())
    if diff is not None:
        value = 0.6 * diff + 0.4 * value
        reasons.append(f"marked {r.difficulty.lower()}")

    if hands_on is not None:
        reasons.append(f"{hands_on} min hands-on")
    if steps >= 12:
        reasons.append(f"{steps} steps")

    return max(0.0, min(1.0, value)), reasons


def source_domain(r: Recipe) -> str:
    """A comparable domain for the recipe's origin, or "".

    Paprika fills these inconsistently: `source_url` is a real URL when the
    recipe was imported from the web, while `source` is whatever the site called
    itself -- "RecipeTin Eats", "recipetineats.com", sometimes a book title. So
    both are tried and the answer is normalised to a bare domain, because
    "www.recipetineats.com" and "recipetineats.com" are not two websites.
    """
    for raw in (r.source_url, r.source):
        text = (raw or "").strip().lower()
        if not text:
            continue
        m = re.search(r"https?://([^/\s]+)", text)
        host = m.group(1) if m else text
        m2 = re.search(r"\b([a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)+)\b", host)
        if m2:
            return m2.group(1).removeprefix("www.")
    return ""


def matches_source(r: Recipe, preferred: list[str]) -> bool:
    """Is this recipe from one of the preferred sources?

    Matched on the domain when there is one, and otherwise on the source text --
    a recipe typed in by hand and credited to "RecipeTin Eats" with no URL is
    still from RecipeTin Eats, and the whole point of the preference is that it
    finds the ones you actually cook from.
    """
    if not preferred:
        return False
    domain = source_domain(r)
    blob = f"{r.source or ''} {r.source_url or ''}".lower()
    for want in preferred:
        w = want.strip().lower().removeprefix("www.")
        if not w:
            continue
        if domain and (domain == w or domain.endswith("." + w)):
            return True
        if w in blob:
            return True
        # "recipetineats.com" should also match a source written "RecipeTin
        # Eats", which is the same site with the spaces a human puts in.
        bare = re.sub(r"\.[a-z.]+$", "", w)
        if bare and re.sub(r"[^a-z0-9]", "", blob).find(bare) >= 0:
            return True
    return False


def is_unattended(r: Recipe, category_names: list[str]) -> bool:
    hay = _haystack(r, category_names)
    return any(term in hay for term in UNATTENDED_TERMS)


def score(r: Recipe, categories: dict[str, str], config: dict,
          season_table: dict[str, set[int]] | None = None,
          month: int | None = None) -> Scored:
    names = [categories.get(uid, "") for uid in r.categories]
    names = [n for n in names if n]

    heart, hr = score_heartiness(r, names)
    effort, er = score_effort(r, names)

    heart = float(config.get("heartiness_overrides", {}).get(r.name, heart))
    effort = float(config.get("effort_overrides", {}).get(r.name, effort))

    prep, cook = parse_minutes(r.prep_time), parse_minutes(r.cook_time)
    minutes = parse_minutes(r.total_time)
    if minutes is None and (prep or cook):
        minutes = (prep or 0) + (cook or 0)
    hands_on = prep if prep is not None else minutes

    preferred = list(config.get("preferred_sources") or [])

    # Seasonality reads the ingredients only. The directions mention lemon
    # juice and a bay leaf constantly, and neither is a reason to call a dish
    # seasonal -- what matters is what you have to go out and buy.
    season_score, fresh, stale = 0.0, [], []
    if season_table and month:
        season_score, fresh, stale = seasons.score_recipe(
            r.ingredients, month, season_table)

    verdict = preferences.assess(
        _haystack(r, names),
        list(config.get("avoid_ingredients") or []),
        list(config.get("avoid_methods") or []))

    return Scored(recipe=r, heartiness=heart, effort=effort, minutes=minutes,
                  hands_on=hands_on, unattended=is_unattended(r, names),
                  preferred_source=matches_source(r, preferred),
                  domain=source_domain(r),
                  season_score=season_score, in_season=fresh,
                  out_of_season=stale,
                  avoid_methods=verdict.methods,
                  excluded_by=verdict.excluded_by,
                  category_names=names, reasons=hr + er)


# --- variety ------------------------------------------------------------------
# A week of seven chicken dinners scores perfectly and eats badly, so the
# planner needs some notion of "these two are the same thing".

PROTEIN_TERMS = [
    "chicken", "beef", "lamb", "pork", "sausage", "bacon", "duck", "turkey",
    "salmon", "tuna", "cod", "prawn", "fish", "squid", "mussel",
    "tofu", "paneer", "halloumi", "lentil", "chickpea", "bean", "mushroom",
    "egg",
]

CUISINE_TERMS = [
    "italian", "chinese", "thai", "indian", "mexican", "japanese", "greek",
    "french", "spanish", "korean", "vietnamese", "moroccan", "turkish",
    "middle eastern", "american", "british",
]

STARCH_TERMS = ["pasta", "rice", "noodle", "potato", "bread", "tortilla",
                "couscous", "polenta", "gnocchi", "quinoa"]


def tags(s: Scored) -> set[str]:
    """The handful of labels used to keep a week from repeating itself."""
    hay = _haystack(s.recipe, s.category_names)
    found: set[str] = set()
    for term in PROTEIN_TERMS:
        if re.search(r"\b" + term + r"s?\b", hay):
            found.add("protein:" + term)
    for term in CUISINE_TERMS:
        if term in hay:
            found.add("cuisine:" + term)
    for term in STARCH_TERMS:
        if re.search(r"\b" + term + r"s?\b", hay):
            found.add("starch:" + term)
    return found
