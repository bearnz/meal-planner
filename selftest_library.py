"""Checks for everything a real Paprika library broke.

The synthetic library in selftest.py is clean: flat ingredient lists, one unit
per line, meal-type categories, a difficulty on every recipe. A real one is
none of those things, and every check in here exists because something that
passed every other test in the project produced a visibly wrong answer against
99 real recipes.

Nothing here talks to Paprika. The inputs are the exact shapes the real library
turned out to contain, written out by hand.
"""

from __future__ import annotations


def run(check):
    _headers(check)
    _dual_units(check)
    _size_words(check)
    _cost_confidence(check)
    _faceted_categories(check)
    _category_anchors(check)
    _servings_and_names(check)


# --- section headings --------------------------------------------------------

def _headers(check):
    print("\nsection headings in ingredient and direction lists")
    from mealplan.quantity import is_section_header
    from mealplan.scoring import count_ingredients, count_steps

    for line in ("SAUCE", "TO SERVE:", "SATAY SEASONING", "GARNISHES",
                 "KALIMERA'S CHICKEN GYROS MARINADE", "For the sauce:",
                 "GARNISH (CHOICES)", "FLAVOUR INFUSION - CHOOSE ONE:"):
        check(f"{line[:34]!r} reads as a heading", is_section_header(line))

    for line in ("1.25 kg chicken thighs", "2 tomatoes , sliced",
                 "Peanuts , chopped", "Fresh chilli, finely chopped",
                 "1/3 cup extra virgin olive oil"):
        check(f"{line[:34]!r} is not a heading", not is_section_header(line))

    # The exact shape that scored 0.97: a short recipe carved into sections.
    ingredients = "\n".join([
        "MARINADE", "1 tbsp paprika", "1 tbsp mustard powder",
        "HOT CHIPS", "400g chips",
        "TO SERVE:", "2 tomatoes", "5 pita breads",
    ])
    check("headings are not counted as ingredients",
          count_ingredients(ingredients) == 5, str(count_ingredients(ingredients)))

    directions = "\n".join([
        "ABBREVIATED RECIPE", "Marinate, cook, serve.",
        "FULL RECIPE", "Mix the marinade.", "Cook the chicken.",
    ])
    check("headings are not counted as steps",
          count_steps(directions) == 3, str(count_steps(directions)))
    check("a recipe of nothing but headings still counts as one step",
          count_steps("SAUCE\nGARNISH") == 1)


# --- dual metric/imperial amounts --------------------------------------------

def _dual_units(check):
    print("\namounts written twice, metric and imperial")
    from mealplan.pricing import parse_line

    cases = [
        ("600g / 1.4lb beef chuck , cut into 4cm cubes", 600.0, "g", "beef chuck"),
        ("1 kg/ 2 lb pork shoulder, skinless", 1.0, "kg", "pork shoulder"),
        ("500ml / 2 cups beef broth", 500.0, "ml", "beef broth"),
        ("1.3 kg/2.6 lb chuck beef , cut into 6 pieces", 1.3, "kg", "chuck beef"),
        ("400g/14oz thick-cut chips", 400.0, "g", "thick-cut chips"),
    ]
    for raw, amount, unit, item in cases:
        line = parse_line(raw)
        check(f"{raw[:32]!r} -> {item!r}", line.item == item, repr(line.item))
        check(f"...keeping the metric amount ({amount}{unit})",
              line.amount == amount and line.unit == unit,
              f"{line.amount}{line.unit}")

    # The failure this guards: the imperial half glued to the ingredient name
    # means nothing matches, so the meat silently leaves the estimate.
    check("a slash that is not an alternate amount survives",
          parse_line("2 tbsp salt / pepper mix").item == "salt / pepper mix",
          repr(parse_line("2 tbsp salt / pepper mix").item))


# --- size adjectives ---------------------------------------------------------

def _size_words(check):
    print("\nsize adjectives on countable things")
    from mealplan.pricing import parse_line

    for raw, amount, item in (("1 large onion , chopped", 1.3, "onion"),
                              ("1 small onion , sliced", 0.75, "onion"),
                              ("1 medium tomato , sliced (180g)", 1.0, "tomato")):
        line = parse_line(raw)
        check(f"{raw[:30]!r} counts as {amount} {item}",
              line.item == item and abs((line.amount or 0) - amount) < 1e-9,
              f"{line.amount} {line.unit!r} {line.item!r}")
        check("...and leaves the unit empty so it prices by the each",
              line.unit == "", repr(line.unit))

    # Genuinely vague amounts must still refuse, or the distinction is lost.
    check("a pinch is still not a measurement",
          parse_line("a pinch of salt").unit == "pinch")


# --- when a cost is worth showing -------------------------------------------

def _cost_confidence(check):
    from mealplan import pricing
    print("\nwhen a cost is worth showing at all")

    cat = pricing.Catalogue({
        "currency": "NZD",
        "items": {
            # g_per_ml matters: without it a teaspoon of a thing priced by
            # weight cannot be converted, and the line lands in `unpriced`.
            "salt": {"unit": "g", "g_per_ml": 1.2, "staple": True,
                     "free_allowance": 100,
                     "prices": {"PAK'nSAVE": {"per": 1000, "price": 2.5}}},
            "onion": {"unit": "each", "g_per_unit": 150,
                      "prices": {"PAK'nSAVE": {"per": 1, "price": 0.55}}},
        },
    })

    # The picanha: one priced line, one big unpriced one.
    roast = pricing.cost_recipe("2 kg beef rump cap\n1 tsp salt", 6, cat,
                                "PAK'nSAVE")
    check("a roast whose meat has no price is not costed",
          not roast.usable, f"coverage {roast.coverage:.2f}")
    check("...and it says which ingredient did it",
          any("rump" in m for m in roast.major_unpriced),
          str(roast.major_unpriced))

    # The birria: majority of *lines* priced, but the beef is missing. This is
    # the case a line-count coverage test passes and should not.
    spices = "\n".join(f"1 tsp salt" for _ in range(8))
    birria = pricing.cost_recipe("1.3 kg chuck beef\n" + spices, 6, cat,
                                 "PAK'nSAVE")
    check("most lines priced does not rescue a missing main ingredient",
          not birria.usable,
          f"coverage {birria.coverage:.2f}, major {birria.major_unpriced}")

    # And the control: nothing important missing.
    fine = pricing.cost_recipe("2 onion\n1 tsp salt", 2, cat, "PAK'nSAVE")
    check("a recipe with everything priced is usable", fine.usable)
    check("...and confident", fine.confident, f"coverage {fine.coverage:.2f}")
    check("...and costs something", fine.total > 0, f"${fine.total:.2f}")

    check("a heading is never counted against coverage",
          pricing.cost_recipe("SAUCE\n2 onion", 2, cat, "PAK'nSAVE").coverage
          == 1.0)


# --- faceted category libraries ---------------------------------------------

def _faceted_categories(check):
    from mealplan.planner import is_dinner
    print("\ntelling a dinner from a side in a faceted library")

    class FakeScored:
        def __init__(self, names):
            self.category_names = names

    config = {
        "not_a_meal_categories": ["Side"],
        "component_categories": ["Bread", "Pastry", "Baking"],
        "main_ingredient_categories": ["Beef", "Chicken", "Fish", "Lamb",
                                       "Pork", "Vege"],
        "exclude_categories": ["Dessert", "Breakfast"],
    }

    not_dinner = {
        "Tzatziki Sauce": ["Greek", "Sauce", "Side", "Vege"],
        "Garlic Rice": ["As-is", "Asian", "Comfort", "Easy", "Pot", "Rice",
                        "Side"],
        "Pizza Dough": ["Baking", "Bread", "Italian", "Oven"],
        "Focaccia": ["Bread", "Italian", "Oven"],
    }
    for name, cats in not_dinner.items():
        check(f"{name} is not a dinner", not is_dinner(FakeScored(cats), config))

    # Each of these was dropped by an earlier, simpler version of this rule.
    dinner = {
        "Butter Chicken": ["Chicken", "Curry", "Indian", "Pan/Wok", "Rice",
                           "Sauce"],
        "Smash Cheeseburger": ["Beef", "Bread", "Cast-iron", "North-American"],
        "Chicken Gyros": ["As-is", "BBQ", "Bread", "Chicken", "Comfort",
                          "Easy", "Kiwiana"],
        "Cottage Pie": ["As-is", "Baking", "Beef", "British", "Casserole"],
        "Chicken Pot Pie": ["Baking", "Chicken", "Comfort", "Effort",
                            "Pastry"],
    }
    for name, cats in dinner.items():
        check(f"{name} IS a dinner", is_dinner(FakeScored(cats), config))

    check("an uncategorised recipe is kept",
          is_dinner(FakeScored([]), config))
    check("the meal-type subset rule still works",
          not is_dinner(FakeScored(["Dessert"]), config))
    check("...and does not fire on a partial match",
          is_dinner(FakeScored(["Dessert", "Beef"]), config))
    check("all three rules off means everything is dinner",
          is_dinner(FakeScored(["Side", "Bread"]), {}))


# --- the library's own tags --------------------------------------------------

def _category_anchors(check):
    from mealplan.scoring import ANCHOR_WEIGHT, apply_category_anchor
    print("\nthe library's own tags outrank what we inferred")

    anchors = {"Effort": 0.82, "Easy": 0.18, "Quick": 0.08}

    # The gyros: we said 0.85, the cook said Easy.
    got, why = apply_category_anchor(0.85, ["Chicken", "Easy"], anchors)
    expect = ANCHOR_WEIGHT * 0.18 + (1 - ANCHOR_WEIGHT) * 0.85
    check("an 'Easy' tag pulls a high score down",
          abs(got - expect) < 1e-9, f"{got:.3f} vs {expect:.3f}")
    check("...and says so in the reasons", why and "Easy" in why[0], str(why))
    check("...without going all the way to the tag's own value",
          got > 0.18, f"{got:.3f}")

    got, _ = apply_category_anchor(0.20, ["Beef", "Effort"], anchors)
    check("an 'Effort' tag pulls a low score up", got > 0.5, f"{got:.3f}")

    check("no tags means no change",
          apply_category_anchor(0.5, ["Beef", "Asian"], anchors)[0] == 0.5)
    check("no anchor map means no change",
          apply_category_anchor(0.5, ["Easy"], None)[0] == 0.5)
    check("matching is case-insensitive",
          apply_category_anchor(0.9, ["easy"], anchors)[0] < 0.9)

    # Contradictory tags: the strongest claim should win rather than the mean.
    both, _ = apply_category_anchor(0.80, ["Easy", "Effort"], anchors)
    check("contradictory tags take the one that says most",
          abs(both - (ANCHOR_WEIGHT * 0.18 + (1 - ANCHOR_WEIGHT) * 0.80)) < 1e-9,
          f"{both:.3f}")


def _servings_and_names(check):
    print("\nlabels a person has to read")
    from mealplan.pricing import Line, _short_name
    from mealplan.service import _servings_label

    for raw, want in (("Servings 4", "4"), ("Serves 4", "4"),
                      ("6 to 8 servings", "6 to 8"), ("4", "4"),
                      ("Servings: 6", "6"), ("", "")):
        got = _servings_label(raw)
        check(f"{raw!r} shows as {want!r}", got == want, repr(got))
    check("a serving note beyond the number is kept",
          _servings_label("serves 4 generously") == "4 generously")

    # Built through parse_line, because that is the only way one of these is
    # ever made and `item` is empty far more often than it looks.
    from mealplan.pricing import parse_line
    for raw, want in (
        ("3 1/2 cups (350g) shredded Colby, Monterey Jack or Oaxaca cheese "
         "(if you can get it) (Note 6)", "shredded Colby"),
        ("1.3 kg/2.6 lb chuck beef , cut into 6 large pieces", "chuck beef"),
        ("500g elbow macaroni", "elbow macaroni"),
    ):
        got = _short_name(parse_line(raw))
        check(f"{raw[:30]!r} names itself {want!r}", got == want, repr(got))

    picanha = _short_name(parse_line(
        "2 kg/ 4 lb beef rump cap / picanha (more names: sirloin cap, "
        "coulette), silver skin trimmed"))
    check("an unclosed bracket is not left dangling in a name",
          "(" not in picanha and len(picanha) <= 28, repr(picanha))
    check("...and the name still says what it is",
          "rump" in picanha, repr(picanha))
    check("a name is never empty when there is a line to name",
          all(_short_name(parse_line(r)) for r in
              ("500g beef", "2 kg/4 lb pork shoulder, diced")))
