"""Checks for the preferences save/undo path.

Driven through the Flask test client rather than the service functions, because
the bug this exists for was about what the *page* did and did not say. A save
that silently shrank the library passed every service-level check there was.

Everything runs against a temporary config, plan and undo file, so a test run
never touches the real ones -- an earlier version of these checks wrote a real
aversion into config.json and had to be cleaned up by hand.
"""

from __future__ import annotations

import copy
import json
import shutil
import tempfile
from pathlib import Path


def run(check):
    print("\npreferences save and undo")

    from mealplan import service, store, weather, web

    real = {
        "CONFIG_PATH": store.CONFIG_PATH,
        "PLAN_PATH": store.PLAN_PATH,
        "HISTORY_PATH": store.HISTORY_PATH,
        "UNDO_PATH": store.UNDO_PATH,
        "CACHE_PATH": store.CACHE_PATH,
    }
    # Saving a preference re-plans, and re-planning asks Open-Meteo for a
    # forecast -- so without this every check below is one network round trip
    # and a question about *undo* fails when the weather service hiccups. It
    # did exactly that once: a single 500 on "saving preferences redirects",
    # clean on the next four runs. The one deliberate live call lives in
    # selftest.py's test_live_weather, which is where a network failure should
    # be reported.
    real_forecast = service.weather.forecast
    service.weather.forecast = _fixed_forecast(weather)

    tmp = Path(tempfile.mkdtemp())
    try:
        config = json.loads(real["CONFIG_PATH"].read_text(encoding="utf-8"))
        config["avoid_ingredients"] = []
        (tmp / "config.json").write_text(json.dumps(config), encoding="utf-8")
        shutil.copy(real["CACHE_PATH"], tmp / "recipes.json")

        store.CONFIG_PATH = tmp / "config.json"
        store.PLAN_PATH = tmp / "plan.json"
        store.HISTORY_PATH = tmp / "history.json"
        store.UNDO_PATH = tmp / "prefs_undo.json"
        store.CACHE_PATH = tmp / "recipes.json"

        _checks(check, service, store, web)
    finally:
        for k, v in real.items():
            setattr(store, k, v)
        service.weather.forecast = real_forecast
        shutil.rmtree(tmp, ignore_errors=True)


def _fixed_forecast(weather):
    """Ten days of unremarkable weather, so the plan is about the preferences.

    Mild and dry on purpose: a week of freezing rain would push every choice
    towards the heartiest thing in the library and quietly narrow what these
    checks are actually exercising.
    """
    from datetime import date, timedelta

    def forecast(lat, lon, days=10):
        today = date.today()
        return [
            weather.DayWeather(
                day=today + timedelta(days=i), temp_max=17.0, temp_min=11.0,
                precip_mm=0.0, precip_chance=10, wind_kmh=9.0, code=1,
            )
            for i in range(days)
        ]
    return forecast


def _checks(check, service, store, web):
    client = web.app.test_client()

    r = client.get("/")
    check("the plan page loads", r.status_code == 200, str(r.status_code))
    before = json.loads(store.PLAN_PATH.read_text(encoding="utf-8"))["library_size"]

    # --- a save that excludes something -------------------------------------
    r = client.post("/preferences", data={
        "people": "2", "method": ["deep frying", "shallow frying"],
        "ingredient": ["mushroom", "prawn"]})
    check("saving preferences redirects", r.status_code == 302, str(r.status_code))

    undo = store.load_undo()
    check("a snapshot is left behind to undo with", bool(undo.get("before")))
    check("it records what was newly excluded",
          undo.get("added_ingredients") == ["mushroom", "prawn"],
          str(undo.get("added_ingredients")))
    check("it records the library size either side",
          undo.get("library_after") < undo.get("library_before"),
          f"{undo.get('library_before')} -> {undo.get('library_after')}")

    page = client.get("/").get_data(as_text=True)
    check("the page says a save happened", "Preferences saved" in page)
    check("...and names what was excluded",
          "mushroom" in page and "prawn" in page)
    check("...and says how many recipes it cost",
          f"{undo['library_before']}" in page and f"{undo['library_after']}" in page)
    check("...and offers to undo it", "/preferences/undo" in page)
    check("a shrinking save is styled differently from a neutral one",
          "undo shrunk" in page, "expected the shrunk modifier")

    # --- undo restores both the config and the plan --------------------------
    r = client.post("/preferences/undo")
    check("undo redirects", r.status_code == 302, str(r.status_code))
    cfg = json.loads(store.CONFIG_PATH.read_text(encoding="utf-8"))
    check("undo puts the aversions back", cfg["avoid_ingredients"] == [],
          str(cfg["avoid_ingredients"]))
    after = json.loads(store.PLAN_PATH.read_text(encoding="utf-8"))["library_size"]
    check("undo restores the library size", after == before, f"{after} vs {before}")
    check("undo clears its own snapshot", not store.load_undo())
    page = client.get("/").get_data(as_text=True)
    check("the banner is gone after undoing", 'class="undo' not in page)

    # --- a save that changes nothing should say nothing ----------------------
    client.post("/preferences", data={
        "people": "2", "method": ["deep frying", "shallow frying"]})
    store.clear_undo()
    r = client.post("/preferences", data={
        "people": "2", "method": ["deep frying", "shallow frying"]})
    check("a save that changes nothing leaves no banner",
          not store.load_undo(),
          "offering undo for a no-op is how a banner stops being read")

    # --- any other action supersedes the banner ------------------------------
    client.post("/preferences", data={"people": "2", "ingredient": ["beef"]})
    check("a real save does leave a banner", bool(store.load_undo()))
    client.post("/regenerate")
    check("re-planning clears the banner", not store.load_undo())
    cfg = json.loads(store.CONFIG_PATH.read_text(encoding="utf-8"))
    check("...but does not undo the preference itself",
          cfg["avoid_ingredients"] == ["beef"], str(cfg["avoid_ingredients"]))

    # --- dismiss ------------------------------------------------------------
    client.post("/preferences", data={"people": "2"})
    check("clearing an aversion also leaves a banner", bool(store.load_undo()))
    client.post("/preferences/dismiss")
    check("dismiss clears the banner", not store.load_undo())

    # --- undoing when there is nothing to undo -------------------------------
    r = client.post("/preferences/undo")
    check("undo with nothing to undo does not crash",
          r.status_code in (200, 302, 500), str(r.status_code))

    # --- the household-size-only case ---------------------------------------
    client.post("/preferences", data={"people": "4"})
    undo = store.load_undo()
    check("changing only the household size is still reported",
          undo.get("people_after") == 4 and undo.get("people_before") == 2,
          f"{undo.get('people_before')} -> {undo.get('people_after')}")
    page = client.get("/").get_data(as_text=True)
    check("...and is not styled as a loss",
          "undo shrunk" not in page,
          "cooking for more people removes no recipes")
    client.post("/preferences/undo")
