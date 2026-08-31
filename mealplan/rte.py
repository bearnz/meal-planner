"""Recipes from RecipeTin Eats, for one household's own planning.

**What this is for.** The Paprika library is 99 recipes and 63 of them already
come from this one site, which is a strong statement about what gets cooked
here. This finds the ones that were never saved, scores them on the same two
axes as everything else, and puts them in the pool the planner chooses from.

**Link-first, deliberately.** What is kept locally is what the planner has to
reason about -- the name, the ingredients, the times, a handful of tags, and
the two scores -- plus the URL. The method is read once, at fetch time, to work
out how much effort the dish is and whether it is deep-fried or full of liver;
then the conclusions are kept and the prose is thrown away. So the dataset can
say "this is a 25-minute one-pan chicken thing that nobody here objects to",
and the only way to actually cook it is to follow the link to their page. That
is the right shape for somebody else's writing, and it is also the shape that
pays them: the recipe still lives where it was published.

**On being a good guest.** RecipeTin Eats is one person and a small team, and
the recipes are the product rather than a by-product. Their robots.txt allows
this (`User-agent: *` / `Disallow:`) and points at the sitemap this reads. So:

  - `robots_allows()` gates every request and fails closed. It is the same gate
    the grocer.nz client uses.
  - The User-Agent says what this is, that it is one household and
    non-commercial, and where the source lives.
  - **The page is abandoned as soon as the recipe has been read.** Their
    articles are half a megabyte of HTML and the JSON-LD sits about 6% in, at
    the end of `<head>`, so streaming and stopping there costs them roughly
    15 KB a recipe instead of 140. That one detail is the difference between a
    31 MB harvest and a 240 MB one.
  - `DELAY` between requests, and the harvest is resumable, so an interrupted
    run costs them the pages it already has exactly once.
  - `Last-Modified` from the sitemap is kept, so a later run re-reads only what
    has actually changed.
  - `ATTRIBUTION` is written into the dataset and shown on the page.

**Why JSON-LD and not the HTML.** Every recipe here carries a schema.org
`Recipe` block, which is the machine-readable copy the site publishes on
purpose for search engines. Reading that instead of scraping the rendered page
means no CSS-selector guesswork, no breakage when they restyle, and no chance
of picking up a "you might also like" recipe by mistake.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterator

import requests

from . import preferences, scoring
from .pricesrc import robots_allows

SOURCE_NAME = "RecipeTin Eats"
SOURCE_URL = "https://www.recipetineats.com"
ATTRIBUTION = ("Recipe data from RecipeTin Eats (recipetineats.com), read for "
               "personal, non-commercial meal planning. Recipes belong to "
               "them; follow the link to cook one.")

SITEMAP_INDEX = SOURCE_URL + "/sitemap_index.xml"

USER_AGENT = ("meal-planner/1.0 (personal, non-commercial meal planner for one "
              "household; +https://github.com/bearnz/meal-planner)")
TIMEOUT = 30

# Between requests. Their pages are CDN-cached and cheap to serve, but a
# thousand of them in a row is still a thousand, and nothing here is urgent.
DELAY = 1.0

# How much of a page to read before giving up on finding the recipe in it. The
# block sits about 30 KB in; this is four times that, so a page that has moved
# its markup is reported rather than silently skipped, and a page that never
# had a recipe (a blog post, a tribute to the dog) costs this and stops.
MAX_READ = 140_000


class RteError(RuntimeError):
    pass


# --- fetching -----------------------------------------------------------------

def _headers() -> dict[str, str]:
    return {"User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml"}


def _check_allowed(url: str) -> None:
    if not robots_allows(url, USER_AGENT):
        raise RteError(f"robots.txt does not allow fetching {url}")


def get_text(url: str) -> str:
    """A whole document. Used for the sitemaps, which are small."""
    _check_allowed(url)
    r = requests.get(url, headers=_headers(), timeout=TIMEOUT)
    if r.status_code != 200:
        raise RteError(f"{url} answered {r.status_code}")
    return r.text


def read_until_recipe(url: str, max_read: int = MAX_READ) -> str:
    """Enough of a page to contain its recipe, and not a byte more.

    The whole politeness argument of this module lives in this function. A
    RecipeTin Eats article is around half a megabyte of HTML -- the recipe, the
    story above it, the comments, and every "you might also like" card -- and
    the schema.org block this actually wants is at the end of `<head>`, about
    six per cent in. Reading the response as a stream and closing it the moment
    that block is complete turns 140 KB of gzipped transfer into about 15.

    Returns the partial document. It is deliberately not valid HTML; only
    `find_recipe` reads it, and that works on the script blocks.
    """
    _check_allowed(url)
    got: list[str] = []
    size = 0
    with requests.get(url, headers=_headers(), timeout=TIMEOUT,
                      stream=True) as r:
        if r.status_code != 200:
            raise RteError(f"{url} answered {r.status_code}")
        r.encoding = r.encoding or "utf-8"
        for chunk in r.iter_content(chunk_size=16384, decode_unicode=True):
            if not chunk:
                continue
            got.append(chunk)
            size += len(chunk)
            # Only look once a block has closed since the last check, so this
            # is not re-parsing the whole buffer on every 16 KB.
            if "</script>" in chunk and find_recipe("".join(got)) is not None:
                break
            if size >= max_read:
                break
    return "".join(got)


# --- reading the recipe out ---------------------------------------------------

_LD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.S | re.I)


def _types(node: Any) -> list[str]:
    t = node.get("@type") if isinstance(node, dict) else None
    return [str(x) for x in (t if isinstance(t, list) else [t]) if x]


def find_recipe(html: str) -> dict[str, Any] | None:
    """The schema.org Recipe out of a page, or None if there is not one.

    Not every post is a recipe -- there are travel diaries, and there is a very
    good one about the dog -- and "no Recipe block" is the honest way to tell,
    rather than guessing from the URL.
    """
    for blob in _LD_RE.findall(html or ""):
        try:
            doc = json.loads(blob)
        except ValueError:
            continue        # a block cut in half by the early stop; keep looking
        nodes = doc.get("@graph") if isinstance(doc, dict) else None
        if nodes is None:
            nodes = doc if isinstance(doc, list) else [doc]
        for node in nodes:
            if isinstance(node, dict) and "Recipe" in _types(node):
                return node
    return None


def _flat(value: Any) -> list[str]:
    """schema.org fields are a string, a list, or a list of objects."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        for key in ("text", "name", "@value"):
            if value.get(key):
                return [str(value[key])]
        return []
    out: list[str] = []
    for item in value if isinstance(value, list) else []:
        out.extend(_flat(item))
    return out


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def clean(text: str) -> str:
    """Strip the markup and entities their fields sometimes carry."""
    t = _TAG_RE.sub(" ", text or "")
    t = (t.replace("&nbsp;", " ").replace("&amp;", "&").replace("&#8217;", "'")
         .replace("&quot;", '"').replace("&#039;", "'").replace("&#8211;", "-"))
    return _WS_RE.sub(" ", t).strip()


_DUR_RE = re.compile(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?", re.I)


def minutes(iso: str) -> int | None:
    """'PT1H30M' -> 90. Their times are always ISO 8601 durations."""
    if not iso:
        return None
    m = _DUR_RE.fullmatch(str(iso).strip())
    if not m or not any(m.groups()):
        return None
    d, h, mi = (int(g or 0) for g in m.groups())
    total = d * 1440 + h * 60 + mi
    return total or None


def as_time(iso: str) -> str:
    """A duration in the shape `scoring.parse_minutes` already reads."""
    n = minutes(iso)
    return f"{n} mins" if n else ""


# --- what kind of thing is it -------------------------------------------------
#
# The categories written here are not RecipeTin Eats' vocabulary, they are the
# planner's, and the translation is the point. `is_dinner` tests
# `exclude_categories` as a *subset*, so a dessert tagged Dessert/Chocolate/
# Baking is not a subset of anything excluded and lands on the menu as Tuesday
# dinner. The Paprika library gets away with rich faceted tags because a person
# curated them; a harvested recipe gets a small, meal-type-shaped set instead,
# and anything that is not dinner gets exactly one tag so the subset test can
# do its job.

NOT_DINNER: dict[str, list[str]] = {
    "Dessert": ["dessert", "desserts", "sweet", "sweets", "cake", "cakes",
                "biscuit", "biscuits", "cookie", "cookies", "slice", "pudding",
                "ice cream", "icecream", "tart", "pie dessert", "baking sweet",
                "candy", "confectionery", "frosting", "icing"],
    "Breakfast": ["breakfast", "brunch", "breakfast and brunch"],
    "Drinks": ["drink", "drinks", "beverage", "beverages", "cocktail",
               "cocktails", "smoothie", "smoothies", "coffee", "tea"],
    "Snacks": ["snack", "snacks", "appetizer", "appetizers", "appetiser",
               "appetisers", "starter", "starters", "finger food", "nibbles",
               "party food", "canape", "canapes", "dip", "dips"],
    "Lunch": ["lunch", "lunchbox", "sandwich", "sandwiches", "toastie",
              "wrap", "wraps", "light lunch"],
    "Side": ["side", "sides", "side dish", "side dishes", "side salad",
             "condiment", "condiments", "sauce", "sauces", "dressing",
             "dressings", "marinade", "marinades", "spice mix", "spice blend",
             "seasoning", "how to", "basics", "stock", "pickles", "relish",
             "jam", "preserve", "preserves", "bread rolls", "dip sauce"],
}

# The protein facets `is_dinner` needs to tell a pizza dough from a cheeseburger.
# Read from the whole recipe rather than only its tags, because RecipeTin Eats
# files by cuisine and dish far more often than by what is in it.
PROTEINS: dict[str, list[str]] = {
    "Beef": ["beef", "steak", "mince", "brisket", "oxtail", "veal"],
    "Chicken": ["chicken", "turkey", "duck", "poultry"],
    "Fish": ["fish", "salmon", "tuna", "prawn", "shrimp", "cod", "snapper",
             "squid", "mussel", "seafood", "scallop", "crab"],
    "Lamb": ["lamb", "mutton"],
    "Pork": ["pork", "bacon", "ham", "sausage", "chorizo", "pancetta"],
    "Vege": ["vegetarian", "vegan", "tofu", "paneer", "halloumi", "chickpea",
             "lentil", "black bean", "meat free", "meat-free", "meatless"],
}

# Facets kept for variety only. `planner.tags` uses these to stop a week being
# seven bowls of noodles; nothing filters on them, so they can be generous.
FACETS: dict[str, list[str]] = {
    "Pasta": ["pasta", "spaghetti", "lasagne", "lasagna", "gnocchi", "risoni"],
    "Rice": ["rice", "risotto", "pilaf", "biryani", "paella"],
    "Noodles": ["noodle", "noodles", "ramen", "pad thai", "pho"],
    "Potato": ["potato", "potatoes", "kumara", "mash"],
    "Soup": ["soup", "chowder", "broth"],
    "Salad": ["salad"],
    "Curry": ["curry", "curries", "masala", "tikka"],
    "Stir fry": ["stir fry", "stir-fry", "stirfry"],
    "Slow cooker": ["slow cooker", "slow-cooker", "crockpot", "crock pot"],
    "Roast": ["roast", "roasted"],
    "Oven": ["oven", "baked", "traybake", "tray bake", "sheet pan"],
    "Soup and stew": ["stew", "casserole", "braise", "braised"],
}


def _has(hay: str, words: list[str]) -> bool:
    return any(re.search(r"\b" + re.escape(w).replace(r"\ ", r"\s+") + r"s?\b",
                         hay) for w in words)


def classify(ld: dict[str, Any], hay: str) -> list[str]:
    """The planner's tags for a harvested recipe.

    Anything that is not dinner comes back as exactly one tag, because that is
    what makes `exclude_categories`' subset test fire and what makes the `Side`
    veto unambiguous. Only once a recipe has survived that does it get the
    protein and variety facets, where more is better.
    """
    tagged = " ".join(_flat(ld.get("recipeCategory"))
                      + _flat(ld.get("keywords"))).lower()
    # The site's own filing wins for the not-dinner question: a chocolate cake
    # mentions "dessert" in its own category and a beef stew does not, whereas
    # both mention "cake" and "sweet" somewhere in three hundred words.
    for tag, words in NOT_DINNER.items():
        if _has(tagged, words):
            return [tag]

    out: list[str] = []
    for tag, words in PROTEINS.items():
        if _has(hay, words):
            out.append(tag)
    for tag, words in FACETS.items():
        if _has(hay, words):
            out.append(tag)
    for cuisine in _flat(ld.get("recipeCuisine"))[:2]:
        c = clean(cuisine).title()
        if c and c not in out:
            out.append(c)
    return out


# --- what the method says, without keeping the method -------------------------

def _literal_for(group: str) -> str:
    """A plain phrase that will re-match one of `preferences`' method groups.

    Those groups are written as small regexes -- `fill.{0,20}with oil` -- so the
    match cannot be stored and replayed verbatim. What is stored instead is the
    first *literal* term in the group, which by construction re-matches the
    group it came from. That keeps the whole aversion machinery working on a
    recipe whose method text was never kept, and the round trip is asserted in
    the tests rather than assumed.
    """
    for term in (preferences.METHOD_GROUPS.get(group, {}).get("terms") or []):
        if re.fullmatch(r"[a-z][a-z\- ]*", term):
            return term
    return ""


def method_signals(text: str) -> list[str]:
    """The techniques a method mentions, as words. Not the method.

    This is the whole of what survives from their instructions: a short list of
    plain cooking terms, each one a fact about the dish rather than a sentence
    anybody wrote. It is enough for the aversion check ("this one is deep
    fried"), for the effort score ("marinate overnight", "one pan"), and for
    the planner's unattended test ("slow cooker") -- which is everything the
    method was being read for.
    """
    hay = (text or "").lower()
    found: list[str] = []

    for group in preferences.METHOD_GROUPS:
        if preferences.uses_method(hay, group):
            term = _literal_for(group)
            if term:
                found.append(term)

    tables: list = [scoring.FAFF_TERMS, scoring.HANDS_OFF_TERMS,
                    scoring.HEARTY_METHODS, scoring.LIGHT_METHODS]
    for table in tables:
        for term in table:
            if re.search(r"\b" + re.escape(term).replace(r"\ ", r"\s+") + r"\b",
                         hay):
                found.append(term)
    for term in scoring.UNATTENDED_TERMS:
        if re.search(r"\b" + re.escape(term).replace(r"\ ", r"\s+") + r"\b", hay):
            found.append(term)

    # Stable order so a re-harvest of an unchanged recipe is an unchanged record.
    return sorted(set(found))


# --- a harvested recipe -------------------------------------------------------

def slug(url: str) -> str:
    return (url or "").rstrip("/").rsplit("/", 1)[-1]


def _title(name: str) -> str:
    """Their titles are sometimes shouted. The plan page is not the place.

    Only all-caps names are touched, and only the words that are themselves
    all-caps, so "WILD RICE SALAD" comes back readable while "Chicken Tikka
    Masala" and a genuine "BBQ" are left exactly as written.
    """
    if not name or name != name.upper():
        return name
    keep = {"BBQ", "AKA", "NZ", "US", "UK"}
    return " ".join(w if w in keep else w.capitalize() for w in name.split())


def to_record(ld: dict[str, Any], url: str) -> dict[str, Any]:
    """One recipe, as this project wants to keep it.

    The instructions go in and do not come out: they are read for their
    techniques and their step count, and then dropped on the floor.
    """
    ingredients = [clean(x) for x in _flat(ld.get("recipeIngredient"))]
    ingredients = [x for x in ingredients if x]
    steps = [clean(x) for x in _flat(ld.get("recipeInstructions"))]
    steps = [x for x in steps if x]

    name = _title(clean(" ".join(_flat(ld.get("name"))))
                  or slug(url).replace("-", " "))
    description = clean(" ".join(_flat(ld.get("description"))))[:400]

    # Read once, here, while the method is still in hand.
    hay = " ".join([name, description, " ".join(ingredients), " ".join(steps)])
    served = _flat(ld.get("recipeYield"))

    rating = (ld.get("aggregateRating") or {}) if isinstance(
        ld.get("aggregateRating"), dict) else {}

    return {
        "uid": "rte:" + slug(url),
        "name": name,
        "url": url,
        "description": description,
        "ingredients": "\n".join(ingredients),
        "prep_time": as_time(ld.get("prepTime", "")),
        "cook_time": as_time(ld.get("cookTime", "")),
        "total_time": as_time(ld.get("totalTime", "")),
        "servings": clean(served[0]) if served else "",
        "categories": classify(ld, hay.lower()),
        "steps": len(steps),
        "method_terms": method_signals(" ".join(steps)),
        "published": str(ld.get("datePublished", ""))[:10],
        "rating": round(float(rating.get("ratingValue") or 0), 2),
        "ratings": int(float(rating.get("ratingCount") or 0)),
    }


# --- the sitemap --------------------------------------------------------------

_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)
_URL_RE = re.compile(r"<url>(.*?)</url>", re.S | re.I)
_MOD_RE = re.compile(r"<lastmod>\s*([^<\s]+)\s*</lastmod>", re.I)


def sitemaps() -> list[str]:
    """The post sitemaps. Pages, categories and authors hold no recipes."""
    return [u for u in _LOC_RE.findall(get_text(SITEMAP_INDEX))
            if "post-sitemap" in u]


def catalogue() -> list[tuple[str, str]]:
    """Every post URL and when it last changed, newest sitemap first.

    `lastmod` is the reason this is a pair rather than a list of links: it is
    what lets a second run skip everything it already has and re-read only what
    has actually been edited, which is most of the politeness of this module
    after the early stop.
    """
    out: list[tuple[str, str]] = []
    for sm in sitemaps():
        body = get_text(sm)
        for block in _URL_RE.findall(body):
            loc = _LOC_RE.search(block)
            if not loc:
                continue
            mod = _MOD_RE.search(block)
            out.append((loc.group(1), mod.group(1)[:10] if mod else ""))
        time.sleep(DELAY)
    return out


# --- the harvest --------------------------------------------------------------

@dataclass
class Harvest:
    """What one run did, in the shape a page and a terminal both want."""
    added: list[dict[str, Any]] = field(default_factory=list)
    skipped_known: int = 0          # already in Paprika
    skipped_seen: int = 0           # already harvested and unchanged
    not_recipes: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    remaining: int = 0
    # url -> the sitemap `lastmod` it was read at, for every page this run
    # actually opened. Built here rather than by the caller because this is the
    # only place that holds the url and its lastmod at the same time, and
    # guessing it afterwards from the recipe's publish date compares a 2017 date
    # against a 2026 lastmod and re-fetches the whole site on every run.
    seen: dict[str, str] = field(default_factory=dict)

    @property
    def fetched(self) -> int:
        return len(self.added) + len(self.not_recipes) + len(self.failed)


def harvest(candidates: list[tuple[str, str]], known: set[str],
            seen: dict[str, str], limit: int = 0,
            progress: Callable[[int, int, str], None] | None = None,
            delay: float = DELAY,
            checkpoint: Callable[["Harvest"], None] | None = None,
            every: int = 25) -> Harvest:
    """Fetch the recipes this library does not have, politely and resumably.

    `known` is the set of URLs already in Paprika -- 63 of the 99 recipes here
    came from this site, and re-harvesting those would be work for both ends
    with nothing at the end of it. `seen` is what a previous run already read,
    keyed by URL and holding the `lastmod` it was read at, so an interrupted
    run resumes rather than restarts and an unchanged recipe is never fetched
    twice.
    """
    out = Harvest()
    todo = []
    for url, mod in candidates:
        if _normalise(url) in known:
            out.skipped_known += 1
        elif url in seen and seen[url] >= (mod or ""):
            out.skipped_seen += 1
        else:
            todo.append((url, mod))

    out.remaining = len(todo)
    if limit:
        todo = todo[:limit]

    for i, (url, _mod) in enumerate(todo, 1):
        if progress:
            progress(i, len(todo), url)
        try:
            ld = find_recipe(read_until_recipe(url))
        except Exception as exc:      # noqa: BLE001 - one bad page is not a run
            out.failed.append(f"{slug(url)}: {exc}")
            time.sleep(delay)
            continue
        if ld is None:
            out.not_recipes.append(url)
        else:
            out.added.append(to_record(ld, url))
        # Recorded whether or not there was a recipe on it. A travel diary is
        # never going to become a dinner, and remembering that is the
        # difference between reading it once and reading it every single run.
        out.seen[url] = _mod or "read"
        out.remaining -= 1
        # Written down as we go. A full pass is nearly two thousand pages and
        # half an hour, and saving only at the end means a Ctrl-C at page 1600
        # throws away 1600 fetches -- which is rude to them as well as annoying
        # here, because the only way to get them back is to ask again.
        if checkpoint and every and i % every == 0:
            checkpoint(out)
        time.sleep(delay)
    if checkpoint:
        checkpoint(out)
    return out


def _normalise(url: str) -> str:
    """Compare URLs the way two humans would: same page, same recipe."""
    u = (url or "").strip().lower().split("?")[0].split("#")[0]
    u = re.sub(r"^https?://", "", u).removeprefix("www.")
    return u.rstrip("/")


def known_urls(recipes: list[dict[str, Any]]) -> set[str]:
    """Which RecipeTin Eats pages the Paprika library already holds."""
    out = set()
    for r in recipes:
        for raw in (r.get("source_url") or "", r.get("source") or ""):
            if "recipetineats.com" in (raw or "").lower():
                out.add(_normalise(raw))
    return out


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
