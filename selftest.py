"""Offline check of the whole chain except Paprika.

Seeds the cache with a synthetic library, drives the planner against invented
forecasts, and asserts the properties the plan is supposed to have rather than
any particular menu -- the choices are meant to change with the weather, so a
fixed expected answer would be a test of the seed and nothing else.

    python selftest.py
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta

from mealplan import store
from mealplan.planner import Slot, build_plan, is_dinner, is_eligible
from mealplan.scoring import parse_minutes, score, tags
from mealplan.paprika import Recipe
from mealplan.weather import DayWeather, wanted_heartiness

PASS, FAIL = "ok  ", "FAIL"
failures = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global failures
    print(f"  {PASS if condition else FAIL}  {label}" + (f"  [{detail}]" if detail and not condition else ""))
    if not condition:
        failures += 1


# --- a synthetic library ------------------------------------------------------

def recipe(name, ingredients, directions, prep="", cook="", total="",
           difficulty="", cats=None, source="", source_url="", servings="4"):
    return Recipe(uid=name.lower().replace(" ", "-"), name=name,
                  ingredients="\n".join(ingredients),
                  directions="\n".join(directions),
                  prep_time=prep, cook_time=cook, total_time=total,
                  difficulty=difficulty, categories=cats or ["dinner"],
                  source=source, source_url=source_url, servings=servings)


# A few of the library below come from the preferred source, written the three
# different ways Paprika actually stores it: a full URL, a bare domain, and a
# human-typed site name with no URL at all.
RTE_URL = "https://www.recipetineats.com/some-recipe/"
RTE_NAME = "RecipeTin Eats"


LIBRARY = [
    recipe("Beef Shin Stew with Dumplings",
           ["1kg beef shin", "2 onions", "3 carrots", "500ml beef stock",
            "200g suet", "300g flour", "600g potatoes", "1 bay leaf"],
           ["Brown the beef shin.", "Soften the onions.", "Add stock and simmer.",
            "Braise in the oven for 3 hours.", "Make the dumplings.",
            "Add dumplings and cook 20 min."],
           prep="30 mins", cook="3 hrs 30 mins", total="4 hrs"),

    recipe("Sausage and Butter Bean Casserole",
           ["8 sausages", "2 x 400g tins butter beans", "1 onion", "150g chorizo",
            "1 x 400g tin chopped tomatoes", "300ml chicken stock"],
           ["Brown the sausages in a one-pot.", "Add everything else.",
            "Cover and cook for 40 minutes."],
           prep="10 mins", cook="45 mins", total="55 mins", difficulty="Easy"),

    recipe("Watermelon and Feta Salad",
           ["800g watermelon", "200g feta", "20g mint", "2 limes", "3 tbsp olive oil",
            "100g rocket"],
           ["Chop the watermelon.", "Crumble over the feta.", "Dress and serve."],
           prep="10 mins", total="10 mins", difficulty="Easy"),

    recipe("Grilled Prawn Skewers",
           ["500g prawns", "2 limes", "3 cloves garlic", "20g coriander",
            "1 tsp chilli flakes"],
           ["Marinate the prawns.", "Thread onto skewers.", "Grill 3 min a side."],
           prep="15 mins", cook="8 mins", total="25 mins", difficulty="Easy"),

    recipe("Chicken Traybake with Lemon",
           ["6 chicken thighs", "2 lemons", "800g new potatoes", "3 tbsp olive oil",
            "1 tsp oregano"],
           ["Heat the oven.", "Throw everything in a tray bake.",
            "Roast for 45 minutes."],
           prep="10 mins", cook="45 mins", total="55 mins", difficulty="Easy",
           source_url=RTE_URL),

    recipe("Cod with Courgette and Basil",
           ["4 cod fillets", "2 courgettes", "20g basil", "1 lemon", "2 tbsp olive oil"],
           ["Steam the courgette.", "Pan fry the cod.", "Dress with basil."],
           prep="10 mins", cook="12 mins", total="22 mins", difficulty="Easy"),

    recipe("Slow Cooker Chilli",
           ["1kg beef mince", "2 x 400g tins kidney beans",
            "2 x 400g tins chopped tomatoes", "2 tsp cumin", "2 tsp chipotle",
            "2 onions", "400g rice"],
           ["Brown the mince.", "Everything in the slow cooker.",
            "Cook on low 8 hours."],
           prep="15 mins", cook="8 hrs", total="8 hrs 15 mins", difficulty="Easy",
           source_url=RTE_URL),

    recipe("Beef Wellington",
           ["1kg beef fillet", "500g puff pastry", "500g mushrooms", "12 slices prosciutto",
            "2 egg yolks", "2 tbsp english mustard", "3 shallots", "1 tsp thyme",
            "50ml brandy", "100ml double cream", "100g butter", "50g flour"],
           ["Sear the fillet.", "Make the duxelles.", "Chill for 2 hours.",
            "Lay out the prosciutto.", "Wrap the fillet.", "Chill again.",
            "Roll the pastry.", "Wrap and glaze.", "Rest for 20 minutes.",
            "Roast.", "Rest again.", "Carve carefully.", "Make the sauce.",
            "Reduce by half."],
           prep="1 hr 30 mins", cook="45 mins", total="4 hrs", difficulty="Hard"),

    recipe("Prawn Pad Thai",
           ["400g rice noodles", "400g prawns", "2 tbsp tamarind", "3 tbsp fish sauce",
            "200g beansprouts", "60g peanuts", "2 limes", "2 eggs"],
           ["Soak the noodles.", "Stir fry the prawns.", "Add sauce and toss."],
           prep="15 mins", cook="10 mins", total="25 mins", difficulty="Easy",
           source="recipetineats.com"),

    recipe("Lamb Tagine with Apricots",
           ["1kg lamb shoulder", "150g dried apricots", "2 tbsp ras el hanout",
            "1 x 400g tin chickpeas", "2 onions", "300g couscous", "60g almonds",
            "20g coriander"],
           ["Brown the lamb.", "Add spices.", "Simmer for 2 hours.",
            "Add apricots.", "Serve with couscous."],
           prep="25 mins", cook="2 hrs 15 mins", total="2 hrs 40 mins",
           source=RTE_NAME),

    recipe("Mushroom Risotto",
           ["350g arborio rice", "400g mushrooms", "150ml white wine", "80g parmesan",
            "60g butter", "1 onion", "1 litre vegetable stock"],
           ["Soften the onion.", "Toast the rice.", "Add wine.",
            "Add stock a ladle at a time for 20 minutes.", "Stir in butter."],
           prep="10 mins", cook="30 mins", total="40 mins", difficulty="Medium"),

    recipe("Halloumi and Chickpea Wraps",
           ["400g halloumi", "1 x 400g tin chickpeas", "8 tortilla wraps", "1 cucumber",
            "200g yoghurt", "20g mint"],
           ["Griddle the halloumi.", "Warm the wraps.", "Assemble."],
           prep="10 mins", cook="8 mins", total="18 mins", difficulty="Easy"),

    recipe("Chocolate Fondant", ["chocolate", "butter", "eggs", "sugar"],
           ["Melt.", "Whisk.", "Bake for 12 minutes."],
           total="30 mins", cats=["dessert"]),

    recipe("Pork Belly with Mashed Potato",
           ["1kg pork belly", "800g potatoes", "100ml double cream", "60g butter",
            "330ml cider", "3 apples", "1 tsp sage"],
           ["Score the belly.", "Slow roast for 3 hours.", "Crisp the crackling.",
            "Make the mash.", "Reduce the cider."],
           prep="20 mins", cook="3 hrs", total="3 hrs 20 mins", difficulty="Medium"),
]

CONFIG = {
    "location": "Auckland, NZ",
    "avoid_ingredients": [],
    "avoid_methods": ["deep frying", "shallow frying"],
    "latitude": -36.848, "longitude": 174.762,
    "household_size": 2,
    "office_days": ["tuesday", "wednesday", "thursday"],
    "day_types": {
        "office": {"effort_ceiling": 0.30, "effort_target": 0.12,
                   "max_minutes": 50},
        "home": {"effort_ceiling": 0.62, "effort_target": 0.32,
                 "max_minutes": None},
        "weekend": {"effort_ceiling": 1.0, "effort_target": 0.78,
                    "max_minutes": None},
    },
    "preferred_sources": ["recipetineats.com"],
    "weights": {"heartiness": 1.0, "effort": 1.2, "repeat": 0.8,
                "variety": 0.6, "source": 0.45,
                "avoid_method": 1.6, "season": 0.7},
    "repeat_memory_weeks": 4,
    "exclude_categories": ["dessert", "breakfast"],
    "heartiness_overrides": {}, "effort_overrides": {},
}

OFFICE = {"tuesday", "wednesday", "thursday"}


def week_of(monday, tmax=16.0, precip=0.0, chance=0, office=OFFICE):
    """Seven slots from a Monday, with day types applied."""
    from mealplan.planner import classify
    out = []
    for i in range(7):
        d = monday + timedelta(days=i)
        out.append(Slot(d, weather_day(d, tmax, precip, chance),
                        day_type=classify(d, office)))
    return out


NZ_SEASONS = None       # built lazily; seasons_for is cheap but not free


def scored_library(month: int | None = None, config: dict | None = None):
    global NZ_SEASONS
    from mealplan.seasons import seasons_for
    if NZ_SEASONS is None:
        NZ_SEASONS = seasons_for(-36.848)
    cats = {"dinner": "Dinner", "dessert": "Dessert"}
    return [score(r, cats, config or CONFIG, NZ_SEASONS, month or 8)
            for r in LIBRARY]


def weather_day(day, tmax, precip=0.0, chance=0, wind=10.0, code=0):
    return DayWeather(day, tmax, tmax - 6, precip, chance, wind, code)


def next_monday() -> date:
    d = date.today()
    return d + timedelta(days=(7 - d.weekday()) % 7 or 7)


# --- checks -------------------------------------------------------------------

def test_time_parsing():
    print("\ntime parsing")
    check("'3 hrs 30 mins' -> 210", parse_minutes("3 hrs 30 mins") == 210,
          str(parse_minutes("3 hrs 30 mins")))
    check("'45 mins' -> 45", parse_minutes("45 mins") == 45)
    check("'1 hr' -> 60", parse_minutes("1 hr") == 60)
    check("'' -> None", parse_minutes("") is None)
    check("'20' -> 20", parse_minutes("20") == 20)


def test_scoring():
    print("\nscoring")
    lib = {s.name: s for s in scored_library()}
    stew = lib["Beef Shin Stew with Dumplings"]
    salad = lib["Watermelon and Feta Salad"]
    welly = lib["Beef Wellington"]
    traybake = lib["Chicken Traybake with Lemon"]
    slowchilli = lib["Slow Cooker Chilli"]

    check("stew is hearty (>0.7)", stew.heartiness > 0.7, f"{stew.heartiness:.2f}")
    check("salad is light (<0.3)", salad.heartiness < 0.3, f"{salad.heartiness:.2f}")
    check("stew beats salad on heartiness", stew.heartiness > salad.heartiness)

    check("wellington is high effort (>0.7)", welly.effort > 0.7, f"{welly.effort:.2f}")
    check("traybake is low effort (<0.35)", traybake.effort < 0.35, f"{traybake.effort:.2f}")
    check("8hr slow cooker still counts as easy (<0.35)",
          slowchilli.effort < 0.35, f"{slowchilli.effort:.2f}")
    check("slow cooker chilli is hearty despite being easy",
          slowchilli.heartiness > 0.6, f"{slowchilli.heartiness:.2f}")

    check("dessert is filtered out",
          not is_dinner(lib["Chocolate Fondant"], CONFIG))
    check("dinners survive the filter", is_dinner(stew, CONFIG))


def test_weather_curve():
    print("\nweather -> wanted heartiness")
    hot = wanted_heartiness(weather_day(date.today(), 29.0))
    mild = wanted_heartiness(weather_day(date.today(), 18.0))
    cold = wanted_heartiness(weather_day(date.today(), 3.0))
    dry = wanted_heartiness(weather_day(date.today(), 14.0))
    wet = wanted_heartiness(weather_day(date.today(), 14.0, precip=8.0, chance=90))

    check("29C wants light (<0.25)", hot < 0.25, f"{hot:.2f}")
    check("18C is middling", 0.3 < mild < 0.6, f"{mild:.2f}")
    check("3C wants hearty (>0.85)", cold > 0.85, f"{cold:.2f}")
    check("colder is monotonically heartier", hot < mild < cold)
    check("rain raises heartiness at the same temperature", wet > dry,
          f"dry {dry:.2f} vs wet {wet:.2f}")


def test_weekday_effort():
    print("\nweekday vs weekend effort")
    lib = [s for s in scored_library() if is_eligible(s, CONFIG)]
    monday = next_monday()
    # A uniformly mild week, so effort is the only thing deciding anything.
    slots = week_of(monday, 16.0)
    plan = build_plan(lib, slots, CONFIG, {}, seed=1)

    office = [c for c in plan if c.slot.day_type == "office"]
    weekend = [c for c in plan if c.slot.day_type == "weekend"]

    from mealplan.planner import day_rules
    over = [c for c in office
            if c.scored.effort > day_rules(c.slot, CONFIG)["effort_ceiling"]]
    check("no office day is over its effort ceiling", not over,
          ", ".join(f"{c.slot.day_name}={c.scored.name}({c.scored.effort:.2f})" for c in over))

    avg_of = sum(c.scored.effort for c in office) / len(office)
    avg_we = sum(c.scored.effort for c in weekend) / len(weekend)
    check("weekend averages more effort than office days", avg_we > avg_of,
          f"office {avg_of:.2f} vs weekend {avg_we:.2f}")

    hardest = max(plan, key=lambda c: c.scored.effort)
    check("the hardest recipe of the week lands on a weekend",
          hardest.slot.is_weekend,
          f"{hardest.scored.name} on {hardest.slot.day_name}")

    # The weekend slots have to hold up against weather that wants light food,
    # or a warm Saturday quietly becomes another weeknight.
    monday = next_monday()
    hot_plan = build_plan(lib, week_of(monday, 28.0), CONFIG, {}, seed=8)
    lazy = [c for c in hot_plan
            if c.slot.day_type == "weekend" and c.scored.effort < 0.3]
    check("a hot weekend still gets a real cook, not a salad", not lazy,
          ", ".join(f"{c.slot.day_name}={c.scored.name}"
                    f"({c.scored.effort:.2f})" for c in lazy))


def test_weeknight_clock():
    print("\nweeknight wall clock")
    lib = {s.name: s for s in scored_library()}
    stew = lib["Beef Shin Stew with Dumplings"]
    chilli = lib["Slow Cooker Chilli"]

    check("a 4hr braise is low hands-on effort", stew.effort < 0.4,
          f"{stew.effort:.2f}")
    check("...but is not unattended", not stew.unattended)
    check("a slow cooker dish is unattended", chilli.unattended)
    check("hands-on is separated from total time",
          stew.hands_on == 30 and stew.minutes == 240,
          f"hands_on={stew.hands_on} total={stew.minutes}")

    monday = next_monday()
    plan = build_plan([s for s in scored_library() if is_eligible(s, CONFIG)],
                      week_of(monday, 5.0), CONFIG, {}, seed=7)

    cap = CONFIG["day_types"]["office"]["max_minutes"]
    late = [c for c in plan
            if c.slot.day_type == "office" and not c.scored.unattended
            and (c.scored.minutes or 0) > cap]
    check("no attended long cook lands on an office day", not late,
          ", ".join(f"{c.slot.day_name}={c.scored.name}"
                    f"({c.scored.minutes}min)" for c in late))

    # The exemption has to actually work, or "unattended" is just a stricter cap.
    # Asserted against the rule itself rather than against a finished plan: a
    # plan can drop the chilli for perfectly good reasons of its own -- variety
    # will, if something else that week is already beef -- and a check that
    # cannot tell that apart from a broken exemption is a check that will one
    # day be "fixed" by breaking the exemption.
    from mealplan.planner import _clock_cost
    tuesday = Slot(monday + timedelta(days=1),
                   weather_day(monday + timedelta(days=1), 5.0),
                   day_type="office")
    saturday = Slot(monday + timedelta(days=5),
                    weather_day(monday + timedelta(days=5), 5.0),
                    day_type="weekend")
    check("a 4hr braise is charged for the clock on a weeknight",
          _clock_cost(stew, tuesday, CONFIG) > 0.5,
          f"{_clock_cost(stew, tuesday, CONFIG):.2f}")
    check("an 8hr slow cooker dish is not",
          _clock_cost(chilli, tuesday, CONFIG) == 0.0,
          f"{_clock_cost(chilli, tuesday, CONFIG):.2f}")
    check("the clock is not charged at all at the weekend",
          _clock_cost(stew, saturday, CONFIG) == 0.0)


def test_weather_drives_choice():
    print("\nweather drives the choice")
    lib = [s for s in scored_library() if is_eligible(s, CONFIG)]
    monday = next_monday()

    hot_plan = build_plan(lib, week_of(monday, 30.0), CONFIG, {}, seed=2)
    cold_plan = build_plan(lib, week_of(monday, 2.0), CONFIG, {}, seed=2)

    hot_avg = sum(c.scored.heartiness for c in hot_plan) / len(hot_plan)
    cold_avg = sum(c.scored.heartiness for c in cold_plan) / len(cold_plan)
    check("a cold week is heartier than a hot one", cold_avg > hot_avg,
          f"hot {hot_avg:.2f} vs cold {cold_avg:.2f}")

    hot_names = {c.scored.name for c in hot_plan}
    cold_names = {c.scored.name for c in cold_plan}
    check("the two weeks are not the same menu", hot_names != cold_names)

    # Within one mixed week, the hot day should be lighter than the cold day.
    from mealplan.planner import classify
    mixed = [Slot(monday + timedelta(days=i),
                  weather_day(monday + timedelta(days=i),
                              30.0 if i in (0, 1) else 2.0),
                  day_type=classify(monday + timedelta(days=i), OFFICE))
             for i in range(7)]
    mixed_plan = build_plan(lib, mixed, CONFIG, {}, seed=3)
    warm_days = [c.scored.heartiness for c in mixed_plan[:2]]
    cold_days = [c.scored.heartiness for c in mixed_plan[2:5]]
    check("inside one week, the hot days get the lighter food",
          max(warm_days) < max(cold_days),
          f"hot {warm_days} vs cold {cold_days}")


def test_no_repeats_and_variety():
    print("\nrepeats and variety")
    lib = [s for s in scored_library() if is_eligible(s, CONFIG)]
    monday = next_monday()
    slots = week_of(monday, 12.0)
    plan = build_plan(lib, slots, CONFIG, {}, seed=4)

    uids = [c.scored.uid for c in plan]
    check("no recipe appears twice in a week", len(set(uids)) == len(uids))
    check("every day got something", len(plan) == 7)

    # Variety has to bite on the main protein, or a library heavy in one thing
    # produces a week of it and every check above still passes.
    proteins: dict[str, int] = {}
    for c in plan:
        for t in tags(c.scored):
            if t.startswith("protein:"):
                proteins[t] = proteins.get(t, 0) + 1
    worst = max(proteins.values()) if proteins else 0
    check("no single protein carries more than three nights of seven",
          worst <= 3, f"{proteins}")

    # Feed history saying the stew was eaten yesterday; it should be displaced.
    recent = {"beef-shin-stew-with-dumplings": 1}
    penalised = build_plan(lib, slots, CONFIG, recent, seed=4)
    was_in = "beef-shin-stew-with-dumplings" in uids
    now_in = "beef-shin-stew-with-dumplings" in {c.scored.uid for c in penalised}
    check("a recipe eaten yesterday is pushed out (or was never in)",
          not (was_in and now_in), "still present despite the repeat penalty")


def test_day_types():
    print("\noffice / home / weekend")
    from mealplan.planner import classify, _clock_cost, day_rules
    monday = next_monday()

    check("a configured office weekday classifies as office",
          classify(monday + timedelta(days=1), OFFICE) == "office")
    check("a weekday not in the office is a home day",
          classify(monday, OFFICE) == "home")
    check("Saturday is a weekend",
          classify(monday + timedelta(days=5), OFFICE) == "weekend")
    check("an explicit ISO date also marks an office day",
          classify(monday, {(monday).isoformat()}) == "office")
    check("office beats weekend if you are in on a Saturday",
          classify(monday + timedelta(days=5),
                   {(monday + timedelta(days=5)).isoformat()}) == "office")

    lib = {s.name: s for s in scored_library()}
    stew = lib["Beef Shin Stew with Dumplings"]

    def slot(offset, kind):
        d = monday + timedelta(days=offset)
        return Slot(d, weather_day(d, 12.0), day_type=kind)

    # The whole point of splitting office days out of "weekday": the braise that
    # cannot happen on an office Wednesday is fine on a work-from-home one.
    check("a 4hr braise is blocked on an office day",
          _clock_cost(stew, slot(2, "office"), CONFIG) > 0.5)
    check("...and is allowed on a day at home",
          _clock_cost(stew, slot(2, "home"), CONFIG) == 0.0)

    check("a home day tolerates more effort than an office day",
          day_rules(slot(0, "home"), CONFIG)["effort_ceiling"]
          > day_rules(slot(0, "office"), CONFIG)["effort_ceiling"])
    check("a weekend aims higher than a home day",
          day_rules(slot(0, "weekend"), CONFIG)["effort_target"]
          > day_rules(slot(0, "home"), CONFIG)["effort_target"])

    # The three under-target penalties must stay in priority order at *every*
    # gap, not just on average. The first version used a different exponent per
    # day type, and the weekend and home curves crossed around gap 0.5 -- home
    # days then won the ambitious recipes away from the nights reserved for
    # them, which no average-based check could see.
    from mealplan.planner import _effort_cost
    from dataclasses import replace as _replace
    inversions = []
    for gap in (0.05, 0.15, 0.3, 0.45, 0.6, 0.75, 0.9):
        costs = {}
        for kind in ("office", "home", "weekend"):
            rules = day_rules(slot(0, kind), CONFIG)
            effort = max(0.0, rules["effort_target"] - gap)
            probe = _replace(stew, effort=effort, minutes=30, unattended=True)
            costs[kind] = _effort_cost(probe, slot(0, kind), CONFIG)
        if not costs["weekend"] > costs["home"] > costs["office"]:
            inversions.append((gap, {k: round(v, 3) for k, v in costs.items()}))
    check("weekend > home > office penalty at every shortfall",
          not inversions, str(inversions))

    # A week where every weekday is an office day should be measurably easier
    # than one where none are -- that is the whole feature, end to end.
    all_lib = [s for s in scored_library() if is_eligible(s, CONFIG)]
    busy = build_plan(all_lib, week_of(monday, 12.0,
                                       office={"monday", "tuesday", "wednesday",
                                               "thursday", "friday"}),
                      CONFIG, {}, seed=11)
    wfh = build_plan(all_lib, week_of(monday, 12.0, office=set()),
                     CONFIG, {}, seed=11)
    busy_wd = [c.scored.effort for c in busy if not c.slot.is_weekend]
    wfh_wd = [c.scored.effort for c in wfh if not c.slot.is_weekend]
    check("a week of office days is easier than a week working from home",
          sum(busy_wd) / len(busy_wd) < sum(wfh_wd) / len(wfh_wd),
          f"office {sum(busy_wd)/len(busy_wd):.2f} vs home {sum(wfh_wd)/len(wfh_wd):.2f}")

    # And within a single ordinary week, not only between two extreme ones.
    # This is the check that catches a home day quietly taking the ten-minute
    # salad because its under-target penalty was too soft to notice.
    mixed = build_plan(all_lib, week_of(monday, 12.0), CONFIG, {}, seed=13)
    home = [c.scored.effort for c in mixed if c.slot.day_type == "home"]
    work = [c.scored.effort for c in mixed if c.slot.day_type == "office"]
    check("in one week, home nights out-effort office nights",
          sum(home) / len(home) > sum(work) / len(work),
          f"home {sum(home)/len(home):.2f} vs office {sum(work)/len(work):.2f}")


def test_source_preference():
    print("\npreferred source")
    from mealplan.scoring import matches_source, source_domain
    from mealplan.planner import _source_cost

    lib = {s.name: s for s in scored_library()}
    check("a full URL matches", lib["Chicken Traybake with Lemon"].preferred_source)
    check("a bare domain matches", lib["Prawn Pad Thai"].preferred_source)
    check("a human-typed site name matches", lib["Lamb Tagine with Apricots"].preferred_source)
    check("an unsourced recipe does not", not lib["Beef Wellington"].preferred_source)

    check("www. is stripped from the domain",
          source_domain(Recipe(uid="x", name="x",
                               source_url="https://www.recipetineats.com/a/"))
          == "recipetineats.com")
    check("a subdomain of a preferred host still matches",
          matches_source(Recipe(uid="x", name="x",
                                source_url="https://blog.recipetineats.com/a/"),
                         ["recipetineats.com"]))
    check("an unrelated site does not match",
          not matches_source(Recipe(uid="x", name="x",
                                    source_url="https://example.com/a/"),
                             ["recipetineats.com"]))

    check("a preferred recipe costs less than an equivalent one",
          _source_cost(lib["Prawn Pad Thai"], CONFIG)
          < _source_cost(lib["Beef Wellington"], CONFIG))
    check("the preference is off when no sources are configured",
          _source_cost(lib["Beef Wellington"], {**CONFIG,
                                                "preferred_sources": []}) == 0.0)

    # It has to actually change a plan, and it has to remain a preference: a
    # weight that filters is a different feature and a much worse one.
    monday = next_monday()
    all_lib = [s for s in scored_library() if is_eligible(s, CONFIG)]
    with_pref = build_plan(all_lib, week_of(monday, 14.0), CONFIG, {}, seed=12)
    without = build_plan(all_lib, week_of(monday, 14.0),
                         {**CONFIG, "preferred_sources": []}, {}, seed=12)
    n_with = sum(1 for c in with_pref if c.scored.preferred_source)
    n_without = sum(1 for c in without if c.scored.preferred_source)
    check("preferring a source puts more of it in the week",
          n_with >= n_without, f"{n_with} with vs {n_without} without")
    check("...but does not take the week over",
          n_with < 7, f"{n_with}/7 from the preferred source")


def test_history_ignores_the_future():
    print("\nhistory counts meals eaten, not meals planned")
    import tempfile, pathlib
    from mealplan import store as st

    original = st.HISTORY_PATH
    tmp = pathlib.Path(tempfile.mkdtemp()) / "history.json"
    st.HISTORY_PATH = tmp
    try:
        today = date.today()
        tmp.write_text(json.dumps([
            {"date": (today - timedelta(days=2)).isoformat(),
             "uid": "eaten-recently", "name": "Eaten Recently"},
            {"date": (today + timedelta(days=4)).isoformat(),
             "uid": "merely-planned", "name": "Merely Planned"},
            {"date": today.isoformat(),
             "uid": "tonight", "name": "Tonight"},
        ]), encoding="utf-8")

        recent = st.recent_uids(4)
        check("a meal eaten two days ago counts",
              recent.get("eaten-recently") == 2, str(recent))
        check("a meal planned for Friday does not",
              "merely-planned" not in recent, str(recent))
        check("tonight's dinner does not either (it is not eaten yet)",
              "tonight" not in recent, str(recent))
        check("no negative ages escape", all(v >= 0 for v in recent.values()),
              str(recent))

        # The machine's clock and the location's can disagree by a day. When
        # they do, the plan's own opening night is "yesterday" by the machine's
        # reckoning, and re-planning threw away whatever was on it -- which
        # showed up as the week's showpiece vanishing on the second press of
        # "re-plan the week". History has to be read on the plan's calendar.
        tmp.write_text(json.dumps([
            {"date": today.isoformat(), "uid": "opening-night",
             "name": "Opening Night"},
        ]), encoding="utf-8")
        as_machine = st.recent_uids(4, today + timedelta(days=1))
        as_plan = st.recent_uids(4, today)
        check("a machine clocked a day ahead would have counted it",
              "opening-night" in as_machine, str(as_machine))
        check("...but on the plan's own calendar it does not",
              "opening-night" not in as_plan, str(as_plan))
    finally:
        st.HISTORY_PATH = original


def test_pinning():
    print("\nre-roll keeps the rest of the week")
    lib = [s for s in scored_library() if is_eligible(s, CONFIG)]
    monday = next_monday()
    slots = week_of(monday, 14.0)
    plan = build_plan(lib, slots, CONFIG, {}, seed=5)

    target = plan[2]
    pinned = {c.slot.day.isoformat(): c.scored.uid for c in plan if c is not target}
    rerolled = build_plan(lib, slots, CONFIG, {}, pinned=pinned,
                          exclude={target.scored.uid})

    by_day = {c.slot.day.isoformat(): c.scored.uid for c in rerolled}
    kept = all(by_day[iso] == uid for iso, uid in pinned.items())
    check("the six pinned days are untouched", kept)
    check("the re-rolled day changed",
          by_day[target.slot.day.isoformat()] != target.scored.uid)


def test_small_library():
    print("\nedge cases")
    lib = [s for s in scored_library() if is_eligible(s, CONFIG)][:7]
    monday = next_monday()
    slots = week_of(monday, 14.0)
    plan = build_plan(lib, slots, CONFIG, {}, seed=6)
    check("exactly seven recipes fills exactly seven days", len(plan) == 7)

    too_small = lib[:3]
    try:
        build_plan(too_small, slots, CONFIG, {}, seed=6)
        check("a too-small library raises rather than silently repeating", False)
    except ValueError:
        check("a too-small library raises rather than silently repeating", True)


def test_live_weather():
    print("\nlive Open-Meteo call")
    try:
        from mealplan import weather as wx
        lat, lon, pretty = wx.geocode("Auckland, NZ")
        check("geocoded Auckland", -90 <= lat <= 90 and -180 <= lon <= 180,
              f"{lat},{lon}")
        fc = wx.forecast(lat, lon, days=7, max_age=0)
        check("got 7 days of forecast", len(fc) == 7, str(len(fc)))
        # Within a day of the machine's date rather than equal to it: the
        # forecast is in the location's timezone, so a machine clocked
        # elsewhere legitimately disagrees either side of midnight.
        check("forecast starts within a day of today",
              abs((fc[0].day - date.today()).days) <= 1, str(fc[0].day))
        check("temperatures are plausible",
              all(-50 < d.temp_max < 60 for d in fc))
        print(f"        ({pretty}: " +
              ", ".join(f"{d.temp_max:.0f}C" for d in fc) + ")")
    except Exception as exc:  # noqa: BLE001
        check(f"live weather reachable", False, str(exc))


if __name__ == "__main__":
    test_time_parsing()
    test_scoring()
    test_weather_curve()
    test_day_types()
    test_weekday_effort()
    test_source_preference()
    test_weeknight_clock()
    test_weather_drives_choice()
    test_no_repeats_and_variety()
    test_history_ignores_the_future()
    test_pinning()
    test_small_library()
    test_live_weather()

    import selftest_library
    selftest_library.run(check)

    import selftest_grocer
    selftest_grocer.run(check)

    import selftest_rte
    selftest_rte.run(check)

    import selftest_speed
    selftest_speed.run(check, scored_library())

    import selftest_undo
    selftest_undo.run(check)

    import selftest_cost
    selftest_cost.run(check)

    import selftest_extra
    selftest_extra.run(check, {
        "LIBRARY": LIBRARY, "CONFIG": CONFIG, "week_of": week_of,
        "next_monday": next_monday, "scored_library": scored_library,
    })

    print(f"\n{'All checks passed.' if not failures else str(failures) + ' FAILED.'}\n")
    sys.exit(1 if failures else 0)
