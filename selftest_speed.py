"""Checks that a re-plan does not redo work it has already done.

These exist because replanning took fifteen seconds against a 99-recipe library
and every other check in the project passed the whole time. Nothing here is a
stopwatch -- a timing assertion on a shared machine is a coin toss. What is
asserted instead is the shape of the work: how many times a thing that only
needed doing once was actually done. That is what regressed, and it is the only
part of it a test can hold still.
"""

from __future__ import annotations


def run(check, library):
    _variety_tags(check, library)
    _forecast_cache(check)


def _variety_tags(check, library):
    print("\nreplanning does not rescan the library")
    from mealplan import planner, scoring

    lib = list(library)[:12]
    config = {"weights": {"variety": 0.6}}

    real = scoring._haystack
    calls = {"n": 0}

    def counted(recipe, names):
        calls["n"] += 1
        return real(recipe, names)

    scoring._haystack = counted
    try:
        for _ in range(40):
            planner._variety_cost(lib, config)
    finally:
        scoring._haystack = real

    # Forty passes over twelve recipes is 480 asks. Anything above one scan per
    # recipe means the tags are being rebuilt, which is what cost the fifteen
    # seconds -- the local search asks for them thousands of times per plan.
    check("a recipe's variety tags are derived once, not per ask",
          calls["n"] <= len(lib), f"{calls['n']} scans for {len(lib)} recipes")

    s = lib[0]
    check("...and the second ask hands back the same set",
          scoring.tags(s) is scoring.tags(s))

    # The cache must not have changed the answer. Cleared, it recomputes, and
    # what comes back has to be what was there before.
    before = set(scoring.tags(s))
    s._tags = None
    check("...and clearing it recomputes the same tags",
          scoring.tags(s) == before, f"{scoring.tags(s)} vs {before}")


def _forecast_cache(check):
    print("\nthe forecast is fetched once, not once per click")
    from datetime import date, timedelta

    from mealplan import weather

    real = weather._fetch
    calls = {"n": 0}

    def counted(lat, lon, days=7):
        calls["n"] += 1
        return [weather.DayWeather(day=date.today() + timedelta(days=i),
                                   temp_max=17.0, temp_min=11.0, precip_mm=0.0,
                                   precip_chance=10, wind_kmh=9.0, code=1)
                for i in range(days)]

    saved = dict(weather._CACHE)
    weather._fetch = counted
    weather._CACHE.clear()
    try:
        a = weather.forecast(-36.85, 174.76, days=10)
        b = weather.forecast(-36.85, 174.76, days=10)
        check("two re-plans in a row are one call to Open-Meteo",
              calls["n"] == 1, f"{calls['n']} calls")
        check("...and both get the same week", [d.day for d in a] == [d.day for d in b])
        check("...as a copy, so trimming one does not trim the cache",
              a is not b)

        a.clear()
        check("...which the next reader still gets in full",
              len(weather.forecast(-36.85, 174.76, days=10)) == 10)

        weather.forecast(-36.85, 174.76, days=10, max_age=0)
        check("max_age=0 insists on a live call", calls["n"] == 2,
              f"{calls['n']} calls")

        weather.forecast(-41.29, 174.78, days=10)
        check("a different place is a different forecast", calls["n"] == 3,
              f"{calls['n']} calls")
    finally:
        weather._fetch = real
        weather._CACHE.clear()
        weather._CACHE.update(saved)
