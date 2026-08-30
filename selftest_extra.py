"""Checks for scaling, aversions, avoided methods and seasonality.

Split out of selftest.py only because that file was getting long; run by it, and
sharing its synthetic library and config.
"""

from __future__ import annotations

from datetime import date, timedelta

from mealplan import preferences, quantity, seasons
from mealplan.paprika import Recipe
from mealplan.planner import build_plan, is_dinner, is_eligible
from mealplan.scoring import score


def run(check, ctx):
    LIBRARY = ctx["LIBRARY"]
    CONFIG = ctx["CONFIG"]
    week_of = ctx["week_of"]
    next_monday = ctx["next_monday"]
    scored_library = ctx["scored_library"]

    _quantities(check)
    _seasons(check)
    _aversions(check, LIBRARY, CONFIG, scored_library)
    _methods(check, LIBRARY, CONFIG, scored_library, week_of, next_monday)
    _season_planning(check, CONFIG, week_of, next_monday, scored_library)


# --- quantity scaling ---------------------------------------------------------

def _quantities(check):
    print("\nquantity scaling")
    cases = [
        ("400g beef mince", 0.5, "200g beef mince"),
        ("1 1/2 cups flour", 0.5, "3/4 cups flour"),
        ("½ onion, finely diced", 2.0, "1 onion, finely diced"),
        ("1½ tsp salt", 2.0, "3 tsp salt"),
        ("2-3 tbsp olive oil", 0.5, "1-1 1/2 tbsp olive oil"),
        ("4 chicken thighs", 0.5, "2 chicken thighs"),
        ("250ml double cream", 1.5, "375ml double cream"),
        ("1.5 kg lamb shoulder", 0.5, "0.75 kg lamb shoulder"),
    ]
    for text, factor, want in cases:
        got = quantity.scale_line(text, factor).text
        check(f"{text!r} x{factor} -> {want!r}", got == want, f"got {got!r}")

    # The lines that must NOT be touched. Each is a way a wrong guess would be
    # invisible in the output and wrong in the kitchen.
    for text in ("Salt and pepper to taste", "a pinch of saffron",
                 "For the sauce:", "Juice of a lemon"):
        r = quantity.scale_line(text, 0.5)
        check(f"{text!r} is left alone", not r.scaled and r.text == text,
              f"became {r.text!r}")

    # Where a line says the amount twice, both halves scale. Half of "3 1/2
    # cups (350g)" is not "1 3/4 cups (350g)" -- that line contradicts itself,
    # on a page read in a shop, and the costing now *prefers* the weight, so a
    # stale one puts a whole batch of cheese in a quarter batch of tacos.
    for text, factor, want in [
            ("3 1/2 cups (350g) shredded cheese", 0.5,
             "1 3/4 cups (175g) shredded cheese"),
            ("1 1/2 cups / 150g mozzarella", 0.5, "3/4 cups / 75g mozzarella"),
            ("600g / 1.4lb beef chuck", 0.5, "300g / 0.7lb beef chuck"),
            ("1 cup (250ml) milk", 0.5, "1/2 cup (125ml) milk")]:
        got = quantity.scale_line(text, factor).text
        check(f"{text[:30]!r} scales both halves", got == want, f"got {got!r}")

    # ...and a bracketed number that is not a restatement is left where it is.
    # Each of these reads as one if the pattern is allowed to roam.
    for text in ("5 cups (tightly packed) kale leaves (1 small bunch, Note 3)",
                 "2 tbsp minced ginger (~5cm/2 inch piece)",
                 "1 onion (150g) , finely diced",
                 "3 1/2 cups shredded cheese (Note 6)"):
        got = quantity.scale_line(text, 0.5).text
        tail = text[text.index("("):]
        check(f"{text[:34]!r} keeps its note", got.endswith(tail),
              f"got {got!r}")

    # A whole-number metric result, because 187.5g is not a weight.
    got = quantity.scale_line("375g flour", 0.5).text
    check("fine metric rounds to a whole number", "." not in got, got)

    # And bulk metric keeps its decimals, because rounding 0.75kg the same way
    # gives 1kg -- a third more meat.
    got = quantity.scale_line("1.5kg beef", 0.5).text
    check("bulk metric keeps decimals", got.startswith("0.75"), got)

    lines, unscaled = quantity.scale_ingredients(
        "400g mince\nSalt and pepper\nFor the sauce:\n2 onions", 0.5)
    check("headings are not counted as unscaled failures", unscaled == 1,
          f"{unscaled} unscaled")
    check("every line comes back", len(lines) == 4, str(len(lines)))

    f, made = quantity.servings_factor("Serves 4", 2)
    check("servings_factor reads 'Serves 4' for 2", f == 0.5 and made == 4,
          f"{f}, {made}")
    f, made = quantity.servings_factor("", 2)
    check("no serving count means no scaling", f == 1.0 and made == 0)


# --- seasonality --------------------------------------------------------------

def _seasons(check):
    print("\nseasonality")
    nz = seasons.seasons_for(-36.848)     # Auckland
    uk = seasons.seasons_for(51.5)        # London

    check("asparagus peaks Apr-Jun in the north", uk["asparagus"] == {4, 5, 6})
    check("...and Oct-Dec in the south", nz["asparagus"] == {10, 11, 12},
          str(sorted(nz["asparagus"])))
    check("pumpkin moves to autumn in NZ (Mar-May)", nz["pumpkin"] == {3, 4, 5},
          str(sorted(nz["pumpkin"])))
    check("the shift is exactly six months, not five or seven",
          all(seasons.shift_months({m}, True) == {((m + 5) % 12) + 1}
              for m in range(1, 13)))
    check("shifting twice returns the original",
          all(seasons.shift_months(seasons.shift_months({m}, True), True) == {m}
              for m in range(1, 13)))

    summer = "500g tomatoes\n2 courgettes\nbasil\n1 cucumber"
    winter = "1kg parsnips\n3 leeks\nkale\n500g potatoes"
    neutral = "500g chicken thighs\nplain flour\nchicken stock\nsoy sauce"

    feb_s, _, _ = seasons.score_recipe(summer, 2, nz)
    aug_s, _, _ = seasons.score_recipe(summer, 8, nz)
    feb_w, _, _ = seasons.score_recipe(winter, 2, nz)
    aug_w, _, _ = seasons.score_recipe(winter, 8, nz)

    check("a summer salad scores well in a NZ February", feb_s > 0.7, f"{feb_s:.2f}")
    check("...and badly in a NZ August", aug_s < 0, f"{aug_s:.2f}")
    check("winter roots score well in a NZ August", aug_w > 0.7, f"{aug_w:.2f}")
    check("...and badly in a NZ February", feb_w < 0, f"{feb_w:.2f}")

    n_score, n_in, n_out = seasons.score_recipe(neutral, 8, nz)
    check("a recipe with no seasonal produce scores exactly zero",
          n_score == 0.0 and not n_in and not n_out, f"{n_score}")

    # Plurals and irregular plurals, which is why the table has no plural keys.
    for text, term in [("500g tomatoes", "tomato"), ("2 potatoes", "potato"),
                       ("300g strawberries", "strawberry"),
                       ("brussels sprouts", "brussels sprout")]:
        _, hits, misses = seasons.score_recipe(text, 1, nz)
        check(f"{term!r} matches its plural in {text!r}",
              term in hits or term in misses, f"{hits} {misses}")

    # The label a human sees must be the word they would use.
    _, hits, _ = seasons.score_recipe("500g tomatoes", 2, nz)
    check("labels are readable, not stem-stripped", "tomatoe" not in hits,
          str(hits))

    # Out of season is worth less than in season is worth, deliberately.
    one_in = seasons.score_recipe("1kg parsnips\n2 leeks\nkale\n1 tomato", 8, nz)[0]
    all_in = seasons.score_recipe("1kg parsnips\n2 leeks\nkale\n1 potato", 8, nz)[0]
    check("one out-of-season item does not sink three in-season ones",
          one_in > 0.3, f"{one_in:.2f} vs all-in {all_in:.2f}")


# --- ingredient aversions -----------------------------------------------------

def _aversions(check, LIBRARY, CONFIG, scored_library):
    print("\ningredient aversions")
    averse = {**CONFIG, "avoid_ingredients": ["mushroom", "prawn"]}
    lib = scored_library(config=averse)
    by_name = {s.name: s for s in lib}

    check("a recipe with an avoided ingredient is flagged",
          by_name["Mushroom Risotto"].excluded_by == ["mushroom"],
          str(by_name["Mushroom Risotto"].excluded_by))
    check("and is dropped from the library entirely",
          not is_eligible(by_name["Mushroom Risotto"], averse))
    check("but is still recognised as a dinner",
          is_dinner(by_name["Mushroom Risotto"], averse))
    check("an unaffected recipe survives",
          is_eligible(by_name["Beef Shin Stew with Dumplings"], averse))
    # Beef Wellington is the useful case here: nothing in its *name* says
    # mushroom, and it is built on a mushroom duxelles. An aversion that only
    # read titles would serve it to someone who does not eat mushrooms.
    check("an aversion catches an ingredient the title never mentions",
          not is_eligible(by_name["Beef Wellington"], averse),
          str(by_name["Beef Wellington"].excluded_by))

    eligible = [s for s in lib if is_eligible(s, averse)]
    check("both prawn dishes go too",
          not any("Prawn" in s.name for s in eligible),
          str([s.name for s in eligible]))

    # A hard exclusion, not a penalty: it must hold even when the library is
    # squeezed and the planner would love to reach for it.
    from mealplan.planner import build_plan as bp
    check("an averse library is smaller", len(eligible) < len(
        [s for s in scored_library() if is_eligible(s, CONFIG)]))

    # Plural and hyphen tolerance, which is most of what goes wrong here.
    for text, term in [("500g mushrooms", "mushroom"),
                       ("spring-onions", "spring onion"),
                       ("2 anchovies", "anchovy")]:
        check(f"{term!r} matches {text!r}",
              preferences.contains_ingredient(text, term))
    check("'pea' does not match 'peanut'",
          not preferences.contains_ingredient("100g peanuts", "pea"))
    check("'corn' does not match 'cornflour'",
          not preferences.contains_ingredient("2 tbsp cornflour", "corn"))


# --- avoided cooking methods --------------------------------------------------

def _methods(check, LIBRARY, CONFIG, scored_library, week_of, next_monday):
    print("\navoided cooking methods")
    fried = Recipe(
        uid="fried", name="Crispy Chicken",
        ingredients="4 chicken thighs\nbreadcrumbs\n1 litre vegetable oil",
        directions="Crumb the chicken.\nHeat the oil to 180C.\n"
                   "Deep fry until golden.",
        total_time="30 mins", categories=["dinner"], servings="4")
    shallow = Recipe(
        uid="shallow", name="Schnitzel",
        ingredients="4 pork steaks\nbreadcrumbs\noil",
        directions="Crumb the pork.\nShallow fry for 3 minutes a side.",
        total_time="25 mins", categories=["dinner"], servings="4")

    cats = {"dinner": "Dinner"}
    sf = score(fried, cats, CONFIG)
    ss = score(shallow, cats, CONFIG)

    check("a deep-fried recipe is detected", "deep frying" in sf.avoid_methods,
          str(sf.avoid_methods))
    check("a shallow-fried recipe is detected",
          "shallow frying" in ss.avoid_methods, str(ss.avoid_methods))
    check("a braise is not", not score(LIBRARY[0], cats, CONFIG).avoid_methods,
          str(score(LIBRARY[0], cats, CONFIG).avoid_methods))

    # A preference, not a ban -- this is the whole difference from an aversion.
    check("an avoided method does NOT remove the recipe",
          is_eligible(sf, CONFIG))

    from mealplan.planner import _method_cost
    check("but it costs a lot", _method_cost(sf, CONFIG) > 1.0,
          f"{_method_cost(sf, CONFIG):.2f}")
    check("a clean recipe costs nothing",
          _method_cost(score(LIBRARY[0], cats, CONFIG), CONFIG) == 0.0)
    check("the charge is off when nothing is avoided",
          _method_cost(sf, {**CONFIG, "weights": {**CONFIG["weights"],
                                                  "avoid_method": 0.0}}) == 0.0)

    # End to end: fried options lose to comparable clean ones.
    monday = next_monday()
    lib = [s for s in scored_library() if is_eligible(s, CONFIG)] + [sf, ss]
    plan = build_plan(lib, week_of(monday, 16.0), CONFIG, {}, seed=21)
    picked_fried = [c.scored.name for c in plan if c.scored.avoid_methods]
    check("fried recipes are passed over when alternatives exist",
          not picked_fried, str(picked_fried))


# --- seasonality inside a plan ------------------------------------------------

def _season_planning(check, CONFIG, week_of, next_monday, scored_library):
    print("\nseasonality changes the plan")
    monday = next_monday()

    # Same library, same weather, two different months. Only the season score
    # differs, so any change in the menu is seasonality doing its job.
    feb = [s for s in scored_library(month=2) if is_eligible(s, CONFIG)]
    aug = [s for s in scored_library(month=8) if is_eligible(s, CONFIG)]

    feb_plan = build_plan(feb, week_of(monday, 16.0), CONFIG, {}, seed=31)
    aug_plan = build_plan(aug, week_of(monday, 16.0), CONFIG, {}, seed=31)

    feb_season = sum(c.scored.season_score for c in feb_plan) / 7
    aug_season = sum(c.scored.season_score for c in aug_plan) / 7
    check("a February plan is in season for February", feb_season > 0,
          f"{feb_season:.2f}")
    check("an August plan is in season for August", aug_season > 0,
          f"{aug_season:.2f}")

    # And the weighting has to be doing something: turning it off should be
    # able to change the answer, or the whole feature is inert.
    off = {**CONFIG, "weights": {**CONFIG["weights"], "season": 0.0}}
    off_plan = build_plan(feb, week_of(monday, 16.0), off, {}, seed=31)
    off_season = sum(c.scored.season_score for c in off_plan) / 7
    check("switching seasonality on improves the season score",
          feb_season >= off_season,
          f"on {feb_season:.2f} vs off {off_season:.2f}")

    from mealplan.planner import _season_cost
    fresh = max(feb, key=lambda s: s.season_score)
    stale = min(feb, key=lambda s: s.season_score)
    check("in-season costs less than out-of-season",
          _season_cost(fresh, CONFIG) < _season_cost(stale, CONFIG),
          f"{_season_cost(fresh, CONFIG):.2f} vs {_season_cost(stale, CONFIG):.2f}")
