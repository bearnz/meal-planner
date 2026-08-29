"""What the household will not eat, and how it is not cooked.

Two different kinds of dislike, handled two different ways, because they are two
different statements:

- An **ingredient aversion** is "we do not eat this". It is a hard exclusion --
  the recipe leaves the library entirely. A penalty would be wrong: a weighted
  dislike still puts the thing on the table on a week when nothing else fits,
  which is exactly the outcome the setting exists to prevent.

- A **cooking method** you would rather avoid is a preference. Deep frying is not
  poison; it is a Tuesday you would rather not have. So it is a heavy weighted
  penalty, and a recipe that is otherwise perfect can still win.

Both are chosen on the preferences page rather than typed into a file, so the
vocabulary below exists to give that page something to list. It is a candidate
set, intersected with what the library actually contains -- offering to exclude
an ingredient nobody has a recipe for is noise, and the page is meant to be
scannable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Cooking methods worth being able to rule out. Each is a display label and the
# phrases that betray it. The frying pair is the reason this exists: "shallow
# fried" almost never says so in the title, and the giveaway is in the method --
# an oil depth, a temperature, or a pan being filled.
METHOD_GROUPS: dict[str, dict] = {
    "deep frying": {
        "label": "Deep frying",
        "hint": "oil to submerge in — fritters, tempura, chips",
        "terms": [
            "deep fry", "deep-fry", "deep fried", "deep-fried", "deep frying",
            "deep-frying", "deep fryer", "deep-fryer",
            "oil for frying", "oil for deep", "vegetable oil for",
            "submerge in oil", "fill.{0,20}with oil",
            r"oil to \d{2,3}\s*(?:c|f|degrees)", r"\d{2,3}\s*c.{0,12}oil",
            "enough oil to cover", "5cm of oil", "2 inches of oil",
            "until golden and crisp.{0,20}oil",
        ],
    },
    "shallow frying": {
        "label": "Shallow frying",
        "hint": "a layer of oil in the pan — schnitzel, crumbed cutlets",
        "terms": [
            "shallow fry", "shallow-fry", "shallow fried", "shallow-fried",
            "shallow frying", "pan fry.{0,30}oil", "fry.{0,20}in.{0,15}oil",
            r"\d+\s*(?:cm|mm|inch).{0,12}oil",
            "cover the base with oil", "generous.{0,12}oil.{0,20}pan",
            "breadcrumb.{0,60}fry", "crumbed.{0,40}fry",
        ],
    },
    "barbecue": {
        "label": "Barbecue / grilling outdoors",
        "hint": "needs the barbecue lit",
        "terms": ["barbecue", "bbq", "on the grill", "charcoal", "grill outdoors"],
    },
    "raw fish": {
        "label": "Raw fish",
        "hint": "sashimi, ceviche, tartare",
        "terms": ["sashimi", "ceviche", "raw fish", "tartare", "crudo",
                  "sushi grade", "sushi-grade"],
    },
    "offal": {
        "label": "Offal",
        "hint": "liver, kidney, tripe",
        "terms": ["liver", "kidneys", "tripe", "sweetbread", "offal",
                  "black pudding", "chicken livers"],
    },
}

# Candidates for the aversion list. Deliberately not derived from the library by
# parsing ingredient lines: "2 tbsp finely chopped flat-leaf parsley, plus extra
# to serve" has no reliable noun to extract, and a menu full of half-parsed
# fragments is worse than a curated list that covers what people actually
# refuse. Free text on the page covers whatever is missing.
COMMON_INGREDIENTS = [
    # proteins
    "beef", "pork", "lamb", "chicken", "turkey", "duck", "veal", "venison",
    "bacon", "sausage", "chorizo", "prosciutto", "ham",
    "salmon", "tuna", "cod", "prawn", "shrimp", "squid", "octopus", "crab",
    "lobster", "mussel", "oyster", "clam", "anchovy", "sardine", "mackerel",
    "tofu", "tempeh", "seitan", "paneer", "halloumi",
    # dairy and eggs
    "egg", "cream", "butter", "milk", "yoghurt", "blue cheese", "goat cheese",
    "feta", "parmesan", "ricotta", "mascarpone",
    # divisive vegetables
    "mushroom", "olive", "aubergine", "eggplant", "courgette", "zucchini",
    "beetroot", "fennel", "celery", "capsicum", "pepper", "cucumber",
    "brussels sprout", "cabbage", "kale", "spinach", "broccoli", "cauliflower",
    "pea", "sweetcorn", "artichoke", "asparagus", "okra", "radish", "turnip",
    "parsnip", "swede", "leek", "onion", "garlic", "pumpkin", "kumara",
    # divisive flavours
    "coriander", "cilantro", "dill", "mint", "rosemary", "sage", "tarragon",
    "chilli", "horseradish", "mustard", "vinegar", "capers", "gherkin",
    "coconut", "peanut", "cashew", "almond", "walnut", "pine nut", "sesame",
    "raisin", "sultana", "date", "apricot", "fig", "banana", "pineapple",
    # pantry / dietary
    "wheat", "gluten", "flour", "pasta", "bread", "rice", "noodle",
    "soy sauce", "fish sauce", "shellfish", "tomato", "lentil", "chickpea",
    "kidney bean", "quinoa", "couscous", "polenta", "tahini", "miso",
]

_CACHE: dict[str, re.Pattern] = {}


def term_pattern(term: str) -> re.Pattern:
    """Whole-word match, tolerant of plurals and of hyphen-or-space phrases."""
    if term not in _CACHE:
        stem = re.escape(term.strip().lower()).replace(r"\ ", r"[\s-]+")
        forms = [stem + r"(?:e?s)?"]
        if term.endswith("y"):
            forms.append(re.escape(term[:-1]).replace(r"\ ", r"[\s-]+") + "ies")
        _CACHE[term] = re.compile(r"\b(?:" + "|".join(forms) + r")\b")
    return _CACHE[term]


def _raw_pattern(term: str) -> re.Pattern:
    """For method terms, which are already written as small regexes."""
    key = "\x00" + term
    if key not in _CACHE:
        _CACHE[key] = re.compile(term, re.I)
    return _CACHE[key]


def contains_ingredient(haystack: str, term: str) -> bool:
    return bool(term_pattern(term).search(haystack))


def uses_method(haystack: str, group: str) -> bool:
    spec = METHOD_GROUPS.get(group)
    if not spec:
        return False
    return any(_raw_pattern(t).search(haystack) for t in spec["terms"])


@dataclass
class Verdict:
    excluded_by: list[str]      # aversions that rule the recipe out
    methods: list[str]          # avoided methods it uses


def assess(haystack: str, avoid_ingredients: list[str],
           avoid_methods: list[str]) -> Verdict:
    """What, if anything, is wrong with this recipe for this household."""
    hay = (haystack or "").lower()
    hit = [t for t in (avoid_ingredients or []) if t and contains_ingredient(hay, t)]
    used = [g for g in (avoid_methods or []) if uses_method(hay, g)]
    return Verdict(excluded_by=hit, methods=used)


def library_counts(haystacks: list[str], terms: list[str] | None = None
                   ) -> list[tuple[str, int]]:
    """How many recipes contain each candidate ingredient, commonest first.

    This is what makes the preferences page worth looking at: it lists what is
    actually in *your* library, with the cost of excluding it visible before you
    click rather than after.
    """
    out: list[tuple[str, int]] = []
    for term in (terms if terms is not None else COMMON_INGREDIENTS):
        pattern = term_pattern(term)
        n = sum(1 for h in haystacks if pattern.search(h))
        if n:
            out.append((term, n))
    out.sort(key=lambda kv: (-kv[1], kv[0]))
    return out


def method_counts(haystacks: list[str]) -> dict[str, int]:
    return {g: sum(1 for h in haystacks if uses_method(h, g))
            for g in METHOD_GROUPS}
