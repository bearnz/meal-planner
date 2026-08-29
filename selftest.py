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
from mealplan.planner import Slot, build_plan, is_dinner
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
           difficulty="", cats=None):
    return Recipe(uid=name.lower().replace(" ", "-"), name=name,
                  ingredients="\n".join(ingredients),
                  directions="\n".join(directions),
                  prep_time=prep, cook_time=cook, total_time=total,
                  difficulty=difficulty, categories=cats or ["dinner"])


LIBRARY = [
    recipe("Beef Shin Stew with Dumplings",
           ["1kg beef shin", "2 onions", "3 carrots", "500ml beef stock",
            "200g suet", "300g flour", "2 potatoes", "bay leaf"],
           ["Brown the beef shin.", "Soften the onions.", "Add stock and simmer.",
            "Braise in the oven for 3 hours.", "Make the dumplings.",
            "Add dumplings and cook 20 min."],
           prep="30 mins", cook="3 hrs 30 mins", total="4 hrs"),

    recipe("Sausage and Butter Bean Casserole",
           ["8 sausages", "2 tins butter beans", "1 onion", "chorizo",
            "chopped tomatoes", "chicken stock"],
           ["Brown the sausages in a one-pot.", "Add everything else.",
            "Cover and cook for 40 minutes."],
           prep="10 mins", cook="45 mins", total="55 mins", difficulty="Easy"),

    recipe("Watermelon and Feta Salad",
           ["watermelon", "feta", "mint", "lime", "olive oil", "rocket"],
           ["Chop the watermelon.", "Crumble over the feta.", "Dress and serve."],
           prep="10 mins", total="10 mins", difficulty="Easy"),

    recipe("Grilled Prawn Skewers",
           ["500g prawns", "lime", "garlic", "coriander", "chilli flakes"],
           ["Marinate the prawns.", "Thread onto skewers.", "Grill 3 min a side."],
           prep="15 mins", cook="8 mins", total="25 mins", difficulty="Easy"),

    recipe("Chicken Traybake with Lemon",
           ["6 chicken thighs", "2 lemons", "new potatoes", "olive oil",
            "oregano"],
           ["Heat the oven.", "Throw everything in a tray bake.",
            "Roast for 45 minutes."],
           prep="10 mins", cook="45 mins", total="55 mins", difficulty="Easy"),

    recipe("Cod with Courgette and Basil",
           ["4 cod fillets", "courgette", "basil", "lemon", "olive oil"],
           ["Steam the courgette.", "Pan fry the cod.", "Dress with basil."],
           prep="10 mins", cook="12 mins", total="22 mins", difficulty="Easy"),

    recipe("Slow Cooker Chilli",
           ["1kg beef mince", "kidney beans", "chopped tomatoes", "cumin",
            "chipotle", "onion", "rice"],
           ["Brown the mince.", "Everything in the slow cooker.",
            "Cook on low 8 hours."],
           prep="15 mins", cook="8 hrs", total="8 hrs 15 mins", difficulty="Easy"),

    recipe("Beef Wellington",
           ["1kg beef fillet", "puff pastry", "mushroom duxelles", "prosciutto",
            "egg yolk", "english mustard", "shallots", "thyme", "brandy",
            "double cream", "butter", "flour"],
           ["Sear the fillet.", "Make the duxelles.", "Chill for 2 hours.",
            "Lay out the prosciutto.", "Wrap the fillet.", "Chill again.",
            "Roll the pastry.", "Wrap and glaze.", "Rest for 20 minutes.",
            "Roast.", "Rest again.", "Carve carefully.", "Make the sauce.",
            "Reduce by half."],
           prep="1 hr 30 mins", cook="45 mins", total="4 hrs", difficulty="Hard"),

    recipe("Prawn Pad Thai",
           ["rice noodles", "prawns", "tamarind", "fish sauce", "beansprouts",
            "peanuts", "lime", "egg"],
           ["Soak the noodles.", "Stir fry the prawns.", "Add sauce and toss."],
           prep="15 mins", cook="10 mins", total="25 mins", difficulty="Easy"),

    recipe("Lamb Tagine with Apricots",
           ["1kg lamb shoulder", "apricots", "ras el hanout", "chickpeas",
            "onion", "couscous", "almonds", "coriander"],
           ["Brown the lamb.", "Add spices.", "Simmer for 2 hours.",
            "Add apricots.", "Serve with couscous."],
           prep="25 mins", cook="2 hrs 15 mins", total="2 hrs 40 mins"),

    recipe("Mushroom Risotto",
           ["arborio rice", "mushrooms", "white wine", "parmesan", "butter",
            "onion", "vegetable stock"],
           ["Soften the onion.", "Toast the rice.", "Add wine.",
            "Add stock a ladle at a time for 20 minutes.", "Stir in butter."],
           prep="10 mins", cook="30 mins", total="40 mins", difficulty="Medium"),

    recipe("Halloumi and Chickpea Wraps",
           ["halloumi", "chickpeas", "tortilla wraps", "cucumber", "yoghurt",
            "mint"],
           ["Griddle the halloumi.", "Warm the wraps.", "Assemble."],
           prep="10 mins", cook="8 mins", total="18 mins", difficulty="Easy"),

    recipe("Chocolate Fondant", ["chocolate", "butter", "eggs", "sugar"],
           ["Melt.", "Whisk.", "Bake for 12 minutes."],
           total="30 mins", cats=["dessert"]),

    recipe("Pork Belly with Mashed Potato",
           ["1kg pork belly", "potatoes", "double cream", "butter", "cider",
            "apples", "sage"],
           ["Score the belly.", "Slow roast for 3 hours.", "Crisp the crackling.",
            "Make the mash.", "Reduce the cider."],
           prep="20 mins", cook="3 hrs", total="3 hrs 20 mins", difficulty="Medium"),
]

CONFIG = {
    "location": "Bristol, GB",
    "effort_ceiling": {"monday": 0.35, "tuesday": 0.35, "wednesday": 0.35,
                       "thursday": 0.35, "friday": 0.55, "saturday": 1.0,
                       "sunday": 1.0},
    "effort_target": {"monday": 0.15, "tuesday": 0.15, "wednesday": 0.15,
                      "thursday": 0.20, "friday": 0.40, "saturday": 0.80,
                      "sunday": 0.70},
    "weights": {"heartiness": 1.0, "effort": 1.2, "repeat": 0.8, "variety": 0.6},
    "repeat_memory_weeks": 4,
    "weeknight_max_minutes": 75,
    "exclude_categories": ["dessert", "breakfast"],
    "heartiness_overrides": {}, "effort_overrides": {},
}


def scored_library():
    cats = {"dinner": "Dinner", "dessert": "Dessert"}
    return [score(r, cats, CONFIG) for r in LIBRARY]


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
    lib = [s for s in scored_library() if is_dinner(s, CONFIG)]
    monday = next_monday()
    # A uniformly mild week, so effort is the only thing deciding anything.
    slots = [Slot(monday + timedelta(days=i),
                  weather_day(monday + timedelta(days=i), 16.0)) for i in range(7)]
    plan = build_plan(lib, slots, CONFIG, {}, seed=1)

    weekday = [c for c in plan if not c.slot.is_weekend]
    weekend = [c for c in plan if c.slot.is_weekend]

    over = [c for c in weekday if c.scored.effort > CONFIG["effort_ceiling"][c.slot.day_name]]
    check("no weeknight is over its effort ceiling", not over,
          ", ".join(f"{c.slot.day_name}={c.scored.name}({c.scored.effort:.2f})" for c in over))

    avg_wd = sum(c.scored.effort for c in weekday) / len(weekday)
    avg_we = sum(c.scored.effort for c in weekend) / len(weekend)
    check("weekend averages more effort than weekdays", avg_we > avg_wd,
          f"weekday {avg_wd:.2f} vs weekend {avg_we:.2f}")

    hardest = max(plan, key=lambda c: c.scored.effort)
    check("the hardest recipe of the week lands on a weekend",
          hardest.slot.is_weekend,
          f"{hardest.scored.name} on {hardest.slot.day_name}")

    # The weekend slots have to hold up against weather that wants light food,
    # or a warm Saturday quietly becomes another weeknight.
    monday = next_monday()
    hot = [Slot(monday + timedelta(days=i),
                weather_day(monday + timedelta(days=i), 28.0)) for i in range(7)]
    hot_plan = build_plan(lib, hot, CONFIG, {}, seed=8)
    lazy = [c for c in hot_plan if c.slot.is_weekend and c.scored.effort < 0.3]
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
    slots = [Slot(monday + timedelta(days=i),
                  weather_day(monday + timedelta(days=i), 5.0)) for i in range(7)]
    plan = build_plan([s for s in scored_library() if is_dinner(s, CONFIG)],
                      slots, CONFIG, {}, seed=7)

    cap = CONFIG["weeknight_max_minutes"]
    late = [c for c in plan
            if not c.slot.is_weekend and not c.scored.unattended
            and (c.scored.minutes or 0) > cap]
    check("no attended long cook lands on a weeknight", not late,
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
                   weather_day(monday + timedelta(days=1), 5.0))
    saturday = Slot(monday + timedelta(days=5),
                    weather_day(monday + timedelta(days=5), 5.0))
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
    lib = [s for s in scored_library() if is_dinner(s, CONFIG)]
    monday = next_monday()

    def week(tmax, precip=0.0, chance=0):
        return [Slot(monday + timedelta(days=i),
                     weather_day(monday + timedelta(days=i), tmax, precip, chance))
                for i in range(7)]

    hot_plan = build_plan(lib, week(30.0), CONFIG, {}, seed=2)
    cold_plan = build_plan(lib, week(2.0), CONFIG, {}, seed=2)

    hot_avg = sum(c.scored.heartiness for c in hot_plan) / len(hot_plan)
    cold_avg = sum(c.scored.heartiness for c in cold_plan) / len(cold_plan)
    check("a cold week is heartier than a hot one", cold_avg > hot_avg,
          f"hot {hot_avg:.2f} vs cold {cold_avg:.2f}")

    hot_names = {c.scored.name for c in hot_plan}
    cold_names = {c.scored.name for c in cold_plan}
    check("the two weeks are not the same menu", hot_names != cold_names)

    # Within one mixed week, the hot day should be lighter than the cold day.
    mixed = [Slot(monday + timedelta(days=i),
                  weather_day(monday + timedelta(days=i),
                              30.0 if i in (0, 1) else 2.0)) for i in range(7)]
    mixed_plan = build_plan(lib, mixed, CONFIG, {}, seed=3)
    warm_days = [c.scored.heartiness for c in mixed_plan[:2]]
    cold_days = [c.scored.heartiness for c in mixed_plan[2:5]]
    check("inside one week, the hot days get the lighter food",
          max(warm_days) < max(cold_days),
          f"hot {warm_days} vs cold {cold_days}")


def test_no_repeats_and_variety():
    print("\nrepeats and variety")
    lib = [s for s in scored_library() if is_dinner(s, CONFIG)]
    monday = next_monday()
    slots = [Slot(monday + timedelta(days=i),
                  weather_day(monday + timedelta(days=i), 12.0)) for i in range(7)]
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
    lib = [s for s in scored_library() if is_dinner(s, CONFIG)]
    monday = next_monday()
    slots = [Slot(monday + timedelta(days=i),
                  weather_day(monday + timedelta(days=i), 14.0)) for i in range(7)]
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
    lib = [s for s in scored_library() if is_dinner(s, CONFIG)][:7]
    monday = next_monday()
    slots = [Slot(monday + timedelta(days=i),
                  weather_day(monday + timedelta(days=i), 14.0)) for i in range(7)]
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
        lat, lon, pretty = wx.geocode("Bristol, GB")
        check("geocoded Bristol", -90 <= lat <= 90 and -180 <= lon <= 180,
              f"{lat},{lon}")
        fc = wx.forecast(lat, lon, days=7)
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
    test_weekday_effort()
    test_weeknight_clock()
    test_weather_drives_choice()
    test_no_repeats_and_variety()
    test_history_ignores_the_future()
    test_pinning()
    test_small_library()
    test_live_weather()

    print(f"\n{'All checks passed.' if not failures else str(failures) + ' FAILED.'}\n")
    sys.exit(1 if failures else 0)
