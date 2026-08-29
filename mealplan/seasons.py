"""Which produce is in season, and how much a recipe leans on it.

Two things make this worth a module rather than another keyword table.

**The hemisphere.** The planner is being used in New Zealand, where August is
late winter. Every published seasonality calendar is northern, so applying one
unshifted would recommend asparagus and strawberries in the depths of a
Wellington winter -- exactly wrong, twice a year, and confidently. The table
below is authored in northern months and shifted six months when the latitude is
negative, which is the whole correction and is applied in one place.

**In season is worth more than out of season is worth avoiding.** A recipe built
on what is good right now is a real win; a recipe using one out-of-season
ingredient is just slightly worse shopping. So the score runs from a small
penalty to a decent bonus rather than symmetrically about zero. Anything not in
the table -- flour, stock, chicken, tinned tomatoes -- has no season and is
correctly ignored, which is most of most recipes.
"""

from __future__ import annotations

import re
from datetime import date

# Northern-hemisphere months (1 = January) in which each item is at its best.
# Peak season rather than "available", because a supermarket has everything all
# year and that would make the whole idea vacuous.
NORTHERN_SEASONS: dict[str, set[int]] = {
    # spring
    "asparagus": {4, 5, 6},
    "rhubarb": {3, 4, 5, 6},
    "spring onion": {3, 4, 5, 6},
    "radish": {4, 5, 6, 7},
    "pea": {5, 6, 7},
    "broad bean": {6, 7, 8},
    "artichoke": {6, 7, 8},
    "new potato": {5, 6, 7},
    "watercress": {4, 5, 6},
    "spinach": {3, 4, 5, 9, 10},
    "lettuce": {5, 6, 7, 8, 9},
    "rocket": {5, 6, 7, 8, 9},
    # summer
    "tomato": {7, 8, 9},
    "courgette": {6, 7, 8, 9}, "zucchini": {6, 7, 8, 9},
    "aubergine": {7, 8, 9}, "eggplant": {7, 8, 9},
    "cucumber": {6, 7, 8, 9},
    "pepper": {7, 8, 9, 10}, "capsicum": {7, 8, 9, 10},
    "sweetcorn": {7, 8, 9}, "corn": {7, 8, 9},
    "green bean": {6, 7, 8, 9},
    "basil": {6, 7, 8, 9},
    "strawberry": {5, 6, 7},
    "raspberry": {6, 7, 8},
    "peach": {7, 8},
    "apricot": {6, 7, 8},
    "nectarine": {7, 8},
    "cherry": {6, 7},
    "watermelon": {7, 8, 9},
    "melon": {7, 8, 9},
    "fennel": {6, 7, 8, 9},
    "mackerel": {7, 8, 9, 10},
    # autumn
    "pumpkin": {9, 10, 11}, "squash": {9, 10, 11, 12},
    "butternut": {9, 10, 11, 12},
    "mushroom": {9, 10, 11},
    "apple": {9, 10, 11},
    "pear": {9, 10, 11},
    "plum": {8, 9},
    "fig": {8, 9},
    "blackberry": {8, 9},
    "beetroot": {9, 10, 11, 12},
    "cauliflower": {9, 10, 11, 12},
    "broccoli": {8, 9, 10, 11},
    "chestnut": {10, 11, 12},
    "venison": {10, 11, 12, 1},
    # winter
    "leek": {10, 11, 12, 1, 2, 3},
    "parsnip": {10, 11, 12, 1, 2},
    "swede": {10, 11, 12, 1, 2},
    "turnip": {10, 11, 12, 1, 2},
    "celeriac": {10, 11, 12, 1, 2},
    "kale": {11, 12, 1, 2, 3},
    "cabbage": {10, 11, 12, 1, 2, 3},
    "brussels sprout": {11, 12, 1},
    "carrot": {9, 10, 11, 12, 1},
    "potato": {9, 10, 11, 12, 1, 2},
    "onion": {8, 9, 10, 11, 12},
    "orange": {12, 1, 2, 3},
    "blood orange": {1, 2, 3},
    "lemon": {12, 1, 2, 3},
    "chicory": {11, 12, 1, 2},
    "kumara": {9, 10, 11, 12},   # NZ staple; northern equivalent is sweet potato
    "sweet potato": {9, 10, 11, 12},
    "mussel": {10, 11, 12, 1, 2},
    "oyster": {10, 11, 12, 1, 2},
}


def shift_months(months: set[int], southern: bool) -> set[int]:
    """Move a northern season half a year for the southern hemisphere."""
    if not southern:
        return months
    return {((m + 5) % 12) + 1 for m in months}


def seasons_for(latitude: float) -> dict[str, set[int]]:
    """The seasonality table as it applies at this latitude."""
    southern = latitude < 0
    return {name: shift_months(months, southern)
            for name, months in NORTHERN_SEASONS.items()}


def in_season(name: str, month: int, table: dict[str, set[int]]) -> bool:
    return month in table.get(name, set())


_WORD: dict[str, re.Pattern] = {}


def _pattern(term: str) -> re.Pattern:
    """Match a produce name and its plural, whichever plural English uses.

    Handling the plurals here rather than as extra table entries is what keeps
    the labels readable: with "tomatoes" as its own key, reporting it meant
    stripping the trailing s and telling the user their recipe contained a
    "tomatoe".
    """
    if term not in _WORD:
        stem = re.escape(term).replace(r"\ ", r"\s+")
        forms = [stem + r"(?:e?s)?"]          # tomato / tomatos / tomatoes
        if term.endswith("y"):
            forms.append(re.escape(term[:-1]).replace(r"\ ", r"\s+") + "ies")
        _WORD[term] = re.compile(r"\b(?:" + "|".join(forms) + r")\b")
    return _WORD[term]


def score_recipe(ingredients: str, month: int, table: dict[str, set[int]]
                 ) -> tuple[float, list[str], list[str]]:
    """(score, in-season hits, out-of-season hits) for one recipe.

    Score runs about -0.35 to +1.0. Only ingredients that *have* a season count
    towards it at all, so a recipe of flour, stock and chicken scores 0 and is
    neither rewarded nor punished -- it is genuinely not a seasonal question.
    """
    text = (ingredients or "").lower()
    hits_in: list[str] = []
    hits_out: list[str] = []

    for name, months in table.items():
        if not _pattern(name).search(text):
            continue
        if month in months:
            hits_in.append(name)
        else:
            hits_out.append(name)

    # Synonyms are separate keys ("courgette" / "zucchini"), and only one of a
    # pair can ever match a given recipe, so nothing needs de-duplicating here.
    if not hits_in and not hits_out:
        return 0.0, [], []

    total = len(hits_in) + len(hits_out)
    share = len(hits_in) / total

    # Asymmetric on purpose: cooking what is good right now is worth chasing,
    # while one out-of-season ingredient is only slightly worse shopping. A
    # symmetric score would make a single out-of-season lemon outweigh three
    # things at their peak.
    score = share - 0.35 * (1.0 - share)

    # A recipe with several seasonal ingredients is more of a seasonal choice
    # than one that happens to contain a carrot, so weight by how much of the
    # recipe is actually in play, saturating at four.
    weight = min(1.0, total / 4.0)
    return score * weight, hits_in, hits_out


def month_for(day: date | None = None) -> int:
    return (day or date.today()).month
