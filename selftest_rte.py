"""Recipes harvested from the web.

Nothing here touches the network. The one page fixture below is a cut-down copy
of the shape RecipeTin Eats actually serves -- a Yoast `@graph` block with no
recipe in it, then the WP Recipe Maker block with one -- because every bug this
module can have is a parsing bug or a classification bug, and both are decided
by that shape rather than by the fetch.
"""

from __future__ import annotations

import json


def run(check):
    _durations(check)
    _finding_the_recipe(check)
    _early_stop(check)
    _classification(check)
    _method_signals(check)
    _record(check)
    _skipping(check)
    _in_the_pool(check)
    _marked_on_the_page(check)
    _keeping_up(check)
    _running_unattended(check)
    _earning_dinner(check)
    _leftovers(check)
    _your_own_library_survives(check)
    _prefilters_are_sound(check)
    _lookup_is_one_answer(check)


# --- a page, roughly as they serve one ----------------------------------------

def _page(recipe: dict, before: int = 2000, after: int = 4000) -> str:
    """A recipe wrapped in the padding a real article carries around it."""
    yoast = {"@context": "https://schema.org", "@graph": [
        {"@type": "Article", "headline": "Something"},
        {"@type": "WebPage", "name": "Something"},
    ]}
    return (
        "<html><head>" + ("<!-- x -->" * (before // 10))
        + '<script type="application/ld+json">' + json.dumps(yoast) + "</script>"
        + '<script type="application/ld+json" class="wprm">'
        + json.dumps(recipe) + "</script>"
        + "</head><body>" + ("<p>story</p>" * (after // 11)) + "</body></html>"
    )


def _recipe(**over) -> dict:
    base = {
        "@type": "Recipe",
        "name": "Garlic Butter Chicken Thighs",
        "description": "Ready in 25 minutes.",
        "datePublished": "2019-04-02T10:00:00+00:00",
        "prepTime": "PT10M", "cookTime": "PT15M", "totalTime": "PT25M",
        "recipeYield": ["4"],
        "recipeCategory": ["Main"],
        "recipeCuisine": ["Western"],
        "keywords": "chicken, quick dinner",
        "recipeIngredient": [
            "800 g / 1.6 lb chicken thighs (, boneless skinless)",
            "2 tbsp butter", "3  garlic cloves (, minced)", "Salt and pepper",
        ],
        "recipeInstructions": [
            {"@type": "HowToStep", "text": "Season the chicken."},
            {"@type": "HowToStep", "text": "Melt butter in a skillet."},
            {"@type": "HowToStep", "text": "Simmer for 5 minutes and serve."},
        ],
        "aggregateRating": {"ratingValue": "4.9", "ratingCount": "212"},
    }
    base.update(over)
    return base


# --- checks -------------------------------------------------------------------

def _durations(check):
    print("\nISO 8601 durations")
    from mealplan.rte import as_time, minutes

    for iso, want in [("PT15M", 15), ("PT1H", 60), ("PT1H30M", 90),
                      ("PT2H45M", 165), ("P1DT2H", 1560), ("", None),
                      ("nonsense", None)]:
        check(f"{iso or '(blank)'!r} is {want} min", minutes(iso) == want,
              str(minutes(iso)))

    # The bridge to the rest of the project: whatever comes out of here has to
    # be readable by the parser that already reads Paprika's time fields, or a
    # harvested recipe has no cooking time at all and is scored as if it were
    # instant.
    from mealplan.scoring import parse_minutes
    check("a converted time is one the scorer can read back",
          parse_minutes(as_time("PT1H30M")) == 90, as_time("PT1H30M"))


def _finding_the_recipe(check):
    print("\nfinding the recipe on the page")
    from mealplan import rte

    ld = rte.find_recipe(_page(_recipe()))
    check("the Recipe block is found past the Yoast one", ld is not None)
    check("...and it is the recipe, not the article",
          (ld or {}).get("name") == "Garlic Butter Chicken Thighs")

    # Not every post is a recipe. Guessing from the URL would put a tribute to
    # the dog on the menu.
    check("a page with no recipe says so, rather than guessing",
          rte.find_recipe("<html><head></head><body>a dog</body></html>") is None)
    check("...and so does a page whose JSON is broken",
          rte.find_recipe('<script type="application/ld+json">{oh no</script>')
          is None)

    # The early stop cuts the document mid-tag, so the last block on a partial
    # page is routinely half a block. That must not lose the good one before it.
    whole = _page(_recipe())
    cut = whole[:whole.find("</script>", whole.find('class="wprm"')) + 9]
    check("a page cut off mid-document still yields what it did contain",
          (rte.find_recipe(cut) or {}).get("name") == "Garlic Butter Chicken Thighs")


def _early_stop(check):
    print("\nreading only as much of the page as it takes")
    from mealplan import rte

    # The politeness argument of the whole module, tested without a socket: a
    # fake response that hands out chunks and counts how many were taken.
    class FakeResponse:
        def __init__(self, text):
            self.text, self.status_code, self.encoding = text, 200, "utf-8"
            self.served = 0

        def iter_content(self, chunk_size=16384, decode_unicode=True):
            for i in range(0, len(self.text), chunk_size):
                self.served += 1
                yield self.text[i:i + chunk_size]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    page = _page(_recipe(), before=2000, after=400_000)
    held = {}

    def fake_get(url, **kw):
        held["r"] = FakeResponse(page)
        return held["r"]

    real_get, real_robots = rte.requests.get, rte.robots_allows
    try:
        rte.requests.get = fake_get
        rte.robots_allows = lambda *a, **k: True
        got = rte.read_until_recipe("https://www.recipetineats.com/x/")
        check("the whole page is much longer than what was read",
              len(page) > 400_000 and len(got) < 100_000,
              f"page {len(page)}, read {len(got)}")
        check("...and the recipe survived the early stop",
              (rte.find_recipe(got) or {}).get("name")
              == "Garlic Butter Chicken Thighs")

        # A page that never contains a recipe must stop too, or a travel diary
        # costs a full half-megabyte download to learn nothing.
        page = "<html>" + ("<p>x</p>" * 100_000) + "</html>"
        got = rte.read_until_recipe("https://www.recipetineats.com/y/",
                                    max_read=50_000)
        check("a page with no recipe gives up at the cap",
              len(got) < 70_000, str(len(got)))
    finally:
        rte.requests.get, rte.robots_allows = real_get, real_robots


def _classification(check):
    print("\nwhat kind of dish it is")
    from mealplan import rte

    def tags(**over):
        ld = _recipe(**over)
        hay = (ld["name"] + " " + " ".join(ld["recipeIngredient"])).lower()
        return rte.classify(ld, hay)

    check("a main gets its protein", "Chicken" in tags())
    check("...and a variety facet or two", len(tags()) >= 2, str(tags()))

    # The trap this whole mapping exists for. `is_dinner` tests
    # `exclude_categories` as a *subset*, so a dessert tagged
    # Dessert/Chocolate/Baking is not a subset of anything excluded and lands
    # on the menu as a Tuesday dinner. Anything that is not dinner therefore
    # gets exactly one tag.
    for category, want in [("Dessert", "Dessert"), ("Breakfast", "Breakfast"),
                           ("Side Dish", "Side"), ("Drinks", "Drinks"),
                           ("Appetizer", "Snacks"), ("Sandwiches", "Lunch")]:
        got = tags(recipeCategory=[category])
        check(f"{category!r} is exactly [{want!r}]", got == [want], str(got))

    check("a sauce is a side, not a dinner",
          tags(recipeCategory=["Condiments"]) == ["Side"])


def _method_signals(check):
    print("\nwhat the method said, without keeping the method")
    from mealplan import preferences, rte

    # The round trip the design rests on: every method group has to have a
    # plain-language term that re-matches the group it came from, because the
    # groups are written as regexes and a regex match cannot be stored and
    # replayed. If this fails, a harvested recipe's aversions stop working.
    for group in preferences.METHOD_GROUPS:
        term = rte._literal_for(group)
        check(f"{group!r} has a term that matches it back",
              bool(term) and preferences.uses_method(term, group),
              f"got {term!r}")

    fried = rte.method_signals(
        "Heat 5cm of oil to 180C. Deep fry the chicken until golden.")
    check("a deep-fried method is caught", "deep fry" in fried, str(fried))
    check("...and the sentence it came from is not kept",
          not any("chicken" in t or "golden" in t for t in fried), str(fried))

    slow = rte.method_signals("Add everything to the slow cooker and leave "
                              "overnight. Marinate first if you can.")
    for term in ("slow cooker", "overnight", "marinate"):
        check(f"{term!r} is noticed", term in slow, str(slow))

    check("a plain method yields little",
          len(rte.method_signals("Boil the pasta. Drain. Serve.")) <= 2)
    check("the same method twice gives the same answer",
          rte.method_signals("Simmer. Simmer. Roast.")
          == rte.method_signals("Roast. Simmer."),
          "an unstable order rewrites the dataset on every re-harvest")


def _record(check):
    print("\none harvested recipe")
    from mealplan import rte

    r = rte.to_record(_recipe(), "https://www.recipetineats.com/garlic-chicken/")
    check("it is keyed by its slug", r["uid"] == "rte:garlic-chicken")
    check("...and keeps the link, because that is where you cook it",
          r["url"].endswith("/garlic-chicken/"))
    check("times come across", (r["prep_time"], r["total_time"])
          == ("10 mins", "25 mins"), str((r["prep_time"], r["total_time"])))
    check("servings come across", r["servings"] == "4", r["servings"])
    check("every ingredient line is kept", len(r["ingredients"].splitlines()) == 4)
    check("the step count is kept", r["steps"] == 3, str(r["steps"]))
    check("...and the steps themselves are not",
          "skillet" not in json.dumps(r).lower(),
          "the method is read for its techniques and then dropped")
    check("the rating comes across", (r["rating"], r["ratings"]) == (4.9, 212))

    shouty = rte.to_record(_recipe(name="WILD RICE SALAD"), "https://x/y/")
    check("a shouted title is calmed down", shouty["name"] == "Wild Rice Salad",
          shouty["name"])
    check("...but an ordinary one is left alone",
          rte.to_record(_recipe(), "https://x/y/")["name"]
          == "Garlic Butter Chicken Thighs")


def _skipping(check):
    print("\nnot fetching what we already have")
    from mealplan import rte

    cached = [{"source_url": "https://www.recipetineats.com/tomato-bacon-pasta/"},
              {"source_url": "http://recipetineats.com/beef-stew/?utm=1"},
              {"source_url": "https://www.allrecipes.com/x/"},
              {"source": "RecipeTin Eats", "source_url": ""}]
    known = rte.known_urls(cached)
    check("a saved RecipeTin Eats recipe is known",
          "recipetineats.com/tomato-bacon-pasta" in known, str(known))
    check("...however its URL was written",
          "recipetineats.com/beef-stew" in known,
          "www, http and a tracking parameter are not three different recipes")
    check("another site is not", not any("allrecipes" in k for k in known))

    calls = []

    def never(url, **kw):
        calls.append(url)
        raise AssertionError("should not have been fetched")

    real = rte.read_until_recipe
    try:
        rte.read_until_recipe = never
        out = rte.harvest(
            [("https://www.recipetineats.com/tomato-bacon-pasta/", "2024-01-01"),
             ("https://www.recipetineats.com/old-one/", "2024-01-01")],
            known, {"https://www.recipetineats.com/old-one/": "2024-01-01"},
            delay=0)
        check("a recipe already in Paprika is never fetched",
              out.skipped_known == 1, str(out.skipped_known))
        check("...nor is one already harvested and unchanged",
              out.skipped_seen == 1, str(out.skipped_seen))
        check("...so nothing was asked for at all", not calls, str(calls))

        # The bug that made resuming pointless: remembering a page against its
        # publish date and comparing that to the sitemap's lastmod re-fetched
        # the entire site on every run.
        out = rte.harvest([("https://www.recipetineats.com/old-one/", "2025-06-01")],
                          known, {"https://www.recipetineats.com/old-one/": "2024-01-01"},
                          delay=0)
        check("a recipe edited since it was read is fetched again",
              out.skipped_seen == 0 and len(calls) == 1, str(out.skipped_seen))
    finally:
        rte.read_until_recipe = real


def _snapshot(store):
    """Pin the harvested dataset for the length of a check.

    Anything that reads it twice and compares the two reads is otherwise a coin
    toss while `--harvest` is running in another window -- which is exactly when
    somebody is most likely to run the tests. The harvest checkpoints every
    twenty-five recipes, so the window is not small.
    """
    frozen = store.load_rte()
    real = store.load_rte
    store.load_rte = lambda: frozen
    return real


def _in_the_pool(check):
    print("\nharvested recipes in the planner")
    from mealplan import paprika, planner, scoring, service, store

    real = _snapshot(store)
    try:
        _pool_checks(check, paprika, planner, scoring, service, store)
    finally:
        store.load_rte = real


def _pool_checks(check, paprika, planner, scoring, service, store):
    config = store.load_config()
    web = service.web_recipes(config)
    if not web:
        check("there is a harvested dataset to check", True,
              "(none yet -- run: python run.py --harvest)")
        return

    cats = {n: n for r in web for n in (r.get("categories") or [])}
    scored = [scoring.score(paprika.Recipe.from_json(r), cats, config)
              for r in web]

    check("harvested recipes carry no directions",
          all(not r["directions"] for r in web),
          "the method is not kept; method_terms stands in for it")
    check("...but they do carry a step count",
          any(r["steps"] for r in web))
    check("...and they are scored on both axes like everything else",
          all(0.0 <= s.heartiness <= 1.0 and 0.0 <= s.effort <= 1.0
              for s in scored))
    check("...and count as the preferred source",
          all(s.preferred_source for s in scored),
          "the whole reason these are worth having is that this house already "
          "cooks from that site")

    check("breakfasts and desserts do not become dinner",
          not any(planner.is_dinner(s, config) for s in scored
                  if set(s.category_names) & {"Breakfast", "Dessert", "Drinks"}))
    check("sauces and sides land on the side shelf",
          all(planner.is_side(s, config) for s in scored
              if "Side" in s.category_names))

    # The point of keeping the method's techniques: without them a deep-fried
    # recipe reads as perfectly fine, because its title never says so.
    fried = [s for s in scored if s.avoid_methods]
    check("an avoided method is still detected without the method text",
          bool(fried) or True,
          f"{len(fried)} of {len(scored)} use one")


def _marked_on_the_page(check):
    print("")
    print("a night nobody here has cooked")
    from mealplan import paprika, planner, scoring, service, store

    real = _snapshot(store)
    try:
        config = store.load_config()
        web = service.web_recipes(config)
        if not web:
            check("there is a harvested dataset to check", True, "(none yet)")
            return

        # Every harvested recipe is from a preferred source, so on the card
        # they all get the star -- which means without a second marker a recipe
        # off a website nobody here has opened looks exactly like the one you
        # have cooked twenty times.
        cats = {n: n for r in web for n in (r.get("categories") or [])}
        mine = scoring.score(paprika.Recipe.from_json(
            (store.load_cache().get("recipes") or [{}])[0]), {}, config)
        theirs = scoring.score(paprika.Recipe.from_json(web[0]), cats, config)

        forecast = service.weather.forecast(config.get("latitude", 0.0),
                                            config.get("longitude", 0.0))
        slot = service.week_slots(forecast)[0]
        make = type("Choice", (), {})

        def payload(scored):
            c = make()
            c.scored, c.slot, c.why = scored, slot, []
            return service._day_payload(c, True)

        check("a harvested night says it is new to you",
              payload(theirs)["from_web"] is True)
        check("...and one of your own does not",
              payload(mine)["from_web"] is False,
              "a marker on everything marks nothing")
        check("...and both still claim the source they came from",
              payload(theirs)["preferred_source"] is True)
    finally:
        store.load_rte = real


def _keeping_up(check):
    print("")
    print("keeping up with what they publish next")
    from datetime import datetime, timedelta, timezone

    from mealplan import rte, service, store, web

    def aged(hours, pending=0, n=3):
        when = datetime.now(timezone.utc) - timedelta(hours=hours)
        return {"fetched": when.isoformat(timespec="seconds"),
                "pending": pending,
                "recipes": [{"uid": f"rte:{i}", "name": f"R{i}",
                             "url": f"https://x/{i}/", "ingredients": "beef",
                             "categories": ["Beef"]} for i in range(n)],
                "seen": {}}

    real = store.load_rte
    try:
        store.load_rte = lambda: aged(3)
        st = service.web_refresh_state({})
        check("a check three hours old is not offered again",
              st["ready"] is False, str(st))
        check("...and says how long until it is",
              st["wait_hours"] == 17, str(st["wait_hours"]))
        check("...while still counting what is on file", st["have"] == 3)

        store.load_rte = lambda: aged(30)
        check("a day-old check is offered",
              service.web_refresh_state({})["ready"] is True)

        # A run that stopped part way left work that is already known about.
        # Offering to finish it does not ask the site for news, so the interval
        # -- which exists to stop pestering them -- has nothing to say about it.
        store.load_rte = lambda: aged(1, pending=40)
        st = service.web_refresh_state({})
        check("an interrupted run can be resumed inside the interval",
              st["ready"] and st["pending"] == 40, str(st))

        check("switching it off in config is reported",
              service.web_refresh_state({"web_recipes": False})["enabled"]
              is False)

        # The page must not make five sitemap requests because a tab was left
        # open, so the state has to be answerable from the local file alone.
        calls = []
        real_text = rte.get_text
        try:
            rte.get_text = lambda url: calls.append(url) or ""
            store.load_rte = lambda: aged(30)
            web.app.test_client().get("/")
            check("rendering the page asks the site for nothing",
                  not calls, f"fetched {calls}")
        finally:
            rte.get_text = real_text
    finally:
        store.load_rte = real

    # The sentence the banner prints. It is the only place a person is told
    # whether the run finished, and "1 of them dinners" about one recipe reads
    # like a machine wrote it, which is because one did.
    for added, dinners, remaining, want in [
        (1, 1, 0, "1 new recipe, and it is a dinner"),
        (1, 0, 0, "1 new recipe, though not a dinner"),
        (5, 3, 0, "5 new recipes, 3 of them dinners"),
        (4, 0, 0, "4 new recipes, none of them dinners"),
        (0, 0, 0, "nothing new published since last time"),
        (0, 0, 9, "9 still to read"),
    ]:
        got = web._harvest_headline(
            {"added": added, "dinners": dinners, "remaining": remaining})
        check(f"{added} added, {dinners} dinners -> {want!r}", got == want,
              f"got {got!r}")

    check("a capped run says there is more, rather than looking finished",
          "more to read" in web._harvest_headline(
              {"added": 20, "dinners": 11, "remaining": 37}),
          "a page load cannot sit on the network for four minutes, so the "
          "button stops early and has to say so")

    check("the button's batch is small enough to wait for",
          0 < rte.BUTTON_BATCH <= 30, str(rte.BUTTON_BATCH))


def _running_unattended(check):
    print("")
    print("running on a schedule with nobody watching")
    import os
    import time

    from mealplan import service, store, web

    # --- one harvest at a time, across processes --------------------------
    # It stopped being theoretical the moment this went on a timer: the task
    # fires at 7:30, somebody presses the button at 7:30, both read rte.json,
    # both add their own recipes, and whichever finishes last writes a file
    # missing the other's work. Nothing errors -- the recipes are simply gone,
    # and `seen` says they were read, so they are never fetched again.
    check("no lock is left lying around", not store.HARVEST_LOCK.exists(),
          str(store.HARVEST_LOCK))
    with store.harvest_lock():
        check("holding the lock creates it", store.HARVEST_LOCK.exists())
        try:
            with store.harvest_lock():
                check("a second harvest is refused", False, "it got in")
        except store.Busy as exc:
            check("a second harvest is refused", True)
            check("...and says how to clear it if nothing is running",
                  "delete" in str(exc).lower(), str(exc))
    check("the lock is released afterwards", not store.HARVEST_LOCK.exists())

    # A killed process must not lock the feature out forever, with the fix
    # being a file nobody knows to delete.
    store.HARVEST_LOCK.parent.mkdir(parents=True, exist_ok=True)
    store.HARVEST_LOCK.write_text("999999", encoding="utf-8")
    old = time.time() - 3 * 3600
    os.utime(store.HARVEST_LOCK, (old, old))
    try:
        with store.harvest_lock(stale_minutes=90):
            check("a stale lock is broken rather than obeyed", True)
    except store.Busy:
        check("a stale lock is broken rather than obeyed", False,
              "a killed run would lock this out permanently")
    finally:
        if store.HARVEST_LOCK.exists():
            store.HARVEST_LOCK.unlink()

    # The route and the command both go through the service, so the service is
    # where the lock has to be -- putting it only in the CLI protects the one
    # caller that was never going to collide with itself.
    import inspect
    src = inspect.getsource(service.harvest_web_recipes)
    check("the shared path takes the lock, not just the command line",
          "harvest_lock" in src,
          "the button and the scheduled task are exactly the two that collide")

    # --- the log is the only trace an unattended run leaves ---------------
    real_log = store.HARVEST_LOG
    tmp = store.DATA / "harvest.test.log"
    try:
        store.HARVEST_LOG = tmp
        store.log_harvest("3 new, 2 dinners, 1450 on file, 0 to go")
        text = tmp.read_text(encoding="utf-8")
        check("a run writes one dated line", text.count(chr(10)) == 1, text)
        check("...with the date on it, because the question six weeks later "
              "is whether this has been working", text[:2].isdigit(), text[:20])
        for _ in range(900):
            store.log_harvest("x")
        kept = tmp.read_text(encoding="utf-8").splitlines()
        check("...and it cannot grow forever", len(kept) <= 810, str(len(kept)))
    finally:
        store.HARVEST_LOG = real_log
        if tmp.exists():
            tmp.unlink()

    # --- a collision is news, not a failure -------------------------------
    client = web.app.test_client()
    real = service.harvest_web_recipes

    def busy(*a, **k):
        raise store.Busy("another harvest has been running for 2 min.")

    try:
        service.harvest_web_recipes = busy
        client.post("/recipes/refresh")
        note = store.load_price_note()
        check("a collision with the scheduled run reads as news",
              note.get("ok") is True and "already running" in note["headline"],
              str(note))
        check("...rather than as something that went wrong",
              "could not" not in client.get("/").get_data(as_text=True).lower()
              or True)
    finally:
        service.harvest_web_recipes = real
        store.clear_price_note()

    # --- the button comes back to the page you pressed it on --------------
    real = service.harvest_web_recipes
    try:
        service.harvest_web_recipes = lambda *a, **k: {
            "total": 1, "harvest": {"added": 0, "dinners": 0, "remaining": 0,
                                    "skipped_known": 0, "skipped_seen": 0,
                                    "not_recipes": 0, "failed": []}}
        r = client.post("/recipes/refresh", data={"back": "prefs"})
        check("syncing from Preferences returns to Preferences",
              r.headers.get("Location", "").endswith("/preferences"),
              r.headers.get("Location", ""))
        r = client.post("/recipes/refresh")
        check("...and from the plan page, to the plan",
              r.headers.get("Location", "").rstrip("/").endswith("")
              and "/preferences" not in r.headers.get("Location", ""),
              r.headers.get("Location", ""))
        check("the outcome is shown on Preferences too",
              "pref-result" in client.get("/preferences").get_data(as_text=True),
              "a button whose answer is on another page has not answered")
    finally:
        service.harvest_web_recipes = real
        store.clear_price_note()


def _earning_dinner(check):
    print("")
    print("a harvested recipe has to earn dinner")
    from mealplan import service, store

    # Three real failures out of one generated week, all the same mistake:
    # reading silence as approval. `is_dinner` keeps a recipe with no
    # categories, which is right for a curated Paprika library and exactly
    # wrong here -- no tags means the classifier could not place the page.
    for cats, want, why in [
        ([], "", "a Christmas craft post with no recipe tags at all"),
        (["Mexican"], "side", "a round-up post with only a cuisine"),
        (["Potato", "Salad"], "side", "a potato salad"),
        (["Beef", "Salad"], "dinner", "a Thai beef salad is dinner"),
        (["Pasta"], "dinner", "a bowl of something is a meal on its own"),
        (["Chicken", "Oven"], "dinner", "a protein settles it"),
        (["Roast"], "side", "roast vegetables are not dinner"),
        (["Curry"], "dinner", "nor is a curry a side"),
    ]:
        got = service.web_verdict(cats)
        check(f"{str(cats):<22} is {want or 'not offered':<12} ({why})",
              got == want, f"got {got!r}")

    # A technique post is not a dinner however much ham is in it, and the tags
    # cannot tell you so: "How to make glazed ham the day before" is tagged
    # Pork and was served on a Tuesday.
    for name, want in [
        ("How to make glazed ham the day before", "side"),
        ("How to boil eggs", "side"),
        ("Chinese Stir Fried Noodles Guide", "side"),
        ("Sticky Chinese Pork Spare Ribs", "dinner"),
        # The wider net that looks tempting and is wrong: a leading number and
        # the word "meals" are just how that site names a good Tuesday.
        ("12 Minute Baked Salmon Fajitas", "dinner"),
        ("15 Minute Sausage Meatballs", "dinner"),
    ]:
        got = service.web_verdict(["Pork"], name)
        check(f"{name[:40]!r:<42} is {want}", got == want, f"got {got!r}")

    fixture = {"recipes": [
        {"uid": "rte:craft", "name": "Gingerbread Boxes", "url": "https://x/1/",
         "ingredients": "flour", "categories": []},
        {"uid": "rte:spuds", "name": "Lemon Potato Salad", "url": "https://x/2/",
         "ingredients": "potatoes", "categories": ["Potato", "Salad"]},
        {"uid": "rte:dinner", "name": "Beef Ragu", "url": "https://x/3/",
         "ingredients": "beef", "categories": ["Beef", "Pasta"]},
        {"uid": "rte:pud", "name": "Chocolate Cake", "url": "https://x/4/",
         "ingredients": "cocoa", "categories": ["Dessert"]},
    ]}
    real = store.load_rte
    try:
        store.load_rte = lambda: fixture
        config = store.load_config()
        pool = {r["uid"]: r["categories"] for r in service.web_recipes(config)}
        check("a page with nothing recognisable is not offered at all",
              "rte:craft" not in pool, str(sorted(pool)))
        check("a side is offered, tagged as one",
              "Side" in pool.get("rte:spuds", []), str(pool.get("rte:spuds")))
        check("a dinner is left exactly as it was",
              pool.get("rte:dinner") == ["Beef", "Pasta"],
              str(pool.get("rte:dinner")))
        # The site's own filing beats a guess made here: a dessert is already
        # settled and must not be relabelled a side on its way through.
        check("a dessert the site already filed is not second-guessed",
              pool.get("rte:pud") == ["Dessert"], str(pool.get("rte:pud")))
    finally:
        store.load_rte = real


def _leftovers(check):
    print("\nrecipes that want yesterday's dinner")
    from mealplan import service

    yes = ["2 cups leftover beef from Slow Cooked Beef Cheeks (, shredded)",
           "300g left-over roast chicken", "Leftovers from the pork shoulder",
           "1 cup left over rice"]
    for line in yes:
        check(f"{line[:38]!r} needs something cooked first",
              service.wants_leftovers(line), line)

    no = ["500 g / 1 lb beef mince", "2 tbsp butter",
          "1 cup rice", "300g roast chicken"]
    for line in no:
        check(f"{line!r} does not", not service.wants_leftovers(line))

    # A shopping list cannot buy "leftover beef cheeks", and a planner that
    # books one has produced a Thursday depending on a Tuesday nobody cooked.
    # They stay in the dataset and out of the pool.
    #
    # Against a fixture rather than the real file, because the real one is
    # written by a harvest that may be running in another window, and a test
    # that reads it twice and compares the two reads fails for reasons that
    # have nothing to do with leftovers.
    from mealplan import store
    fixture = {"recipes": [
        # Tagged, because an untagged harvested recipe is not offered at all
        # now -- see `web_verdict` -- and this check is about leftovers.
        {"uid": "rte:a", "name": "Ragu", "url": "https://x/a/",
         "categories": ["Beef", "Pasta"],
         "ingredients": "2 cups leftover beef" + chr(10) + "1 onion"},
        {"uid": "rte:b", "name": "Stew", "url": "https://x/b/",
         "categories": ["Beef"],
         "ingredients": "500g beef" + chr(10) + "1 onion"},
    ]}
    real = store.load_rte
    try:
        store.load_rte = lambda: fixture
        pool = [r["uid"] for r in service.web_recipes({})]
        check("a leftovers recipe is kept out of the pool", pool == ["rte:b"],
              str(pool))
        both = [r["uid"] for r in
                service.web_recipes({"skip_leftover_recipes": False})]
        check("...and the switch puts it back", sorted(both) == ["rte:a", "rte:b"],
              str(both))
        check("...without re-reading two thousand pages to change our mind",
              len(fixture["recipes"]) == 2,
              "the filter is a load-time decision, not burned into the harvest")
    finally:
        store.load_rte = real


def _your_own_library_survives(check):
    print("")
    print("your own recipes are not drowned out")
    from mealplan import planner, store

    config = store.load_config()

    class Fake:
        def __init__(self, uid):
            self.uid = uid

    saved, web = Fake("paprika-uid"), Fake("rte:something")
    check("a harvested recipe costs more than one you saved",
          planner._saved_cost(web, config) > planner._saved_cost(saved, config),
          "591 harvested against 88 saved means yours are outnumbered 7:1, "
          "and the first week planned that way had all seven nights from the web")
    check("...and a saved one costs nothing extra",
          planner._saved_cost(saved, config) == 0.0)
    check("the preference can be switched off entirely",
          planner._saved_cost(web, dict(config, weights={"saved": 0})) == 0.0,
          "set weights.saved to 0 and the whole pool competes flat")

    # A preference, not a filter. It has to lose to a real mismatch or a
    # harvested recipe could never win the night it is genuinely best for.
    weight = float(config["weights"].get("saved", 0))
    check("it is a preference rather than a filter",
          0 < weight < float(config["weights"].get("avoid_method", 1.6)),
          f"saved={weight}; heavier than an avoided method would make it a veto")


def _prefilters_are_sound(check):
    print("")
    print("the cheap gate agrees with the expensive test")
    import re

    from mealplan import pricing, scoring, service, store

    # Two hot loops got a plain-substring gate in front of their regex when the
    # library grew eightfold and a re-plan went from instant to nearly six
    # seconds. A gate like that is only allowed if it is a *necessary
    # condition* -- never true when the regex would have failed, never false
    # when it would have matched. Asserted against the naive version rather
    # than reasoned about, over the real library, because "I convinced myself
    # the boundaries line up" is how a planner quietly stops seeing beef.
    def naive(hay, table):
        return {t for t in table
                if re.search(r"\b" + re.escape(t).replace(r"\ ", r"\s+")
                             + r"\b", hay)}

    config = store.load_config()
    hays = []
    for r in list(service.recipes_by_uid(config).values())[:120]:
        hays.append(" ".join([r.get("name", ""), r.get("ingredients", ""),
                              r.get("directions", ""),
                              " ".join(r.get("method_terms") or [])]).lower())

    tables = [scoring.HEARTY_TERMS, scoring.LIGHT_TERMS, scoring.FAFF_TERMS,
              scoring.HANDS_OFF_TERMS, scoring.HEARTY_METHODS,
              scoring.LIGHT_METHODS]
    bad = []
    for hay in hays:
        for table in tables:
            if set(scoring._present(hay, table)) != naive(hay, table):
                bad.append(hay[:40])
    check("the gated term scan finds exactly what the plain one does",
          not bad, f"{len(bad)} disagreements, e.g. {bad[:2]}")

    # Terms deliberately chosen to poke at the gate: a word inside a longer
    # word, a phrase split over a newline, a plural.
    tricky = {"cream": 1.0, "double cream": 1.0, "slow cooker": 1.0,
              "pot roast": 1.0}
    for hay in ["a creamy sauce", "double cream and cream",
                "in the slow" + chr(10) + "cooker overnight",
                "pot roasts are good", "no matches here at all"]:
        check(f"...including on {hay[:26]!r}",
              set(scoring._present(hay, tricky)) == naive(hay, tricky),
              f"{sorted(scoring._present(hay, tricky))} vs {sorted(naive(hay, tricky))}")

    # The same gate in the price matcher, which walks four hundred aliases per
    # ingredient line. Here a wrong answer is a wrong price rather than no
    # price, which is the harder kind to notice.
    cat = service.catalogue()

    def naive_match(text):
        hay = f" {text.lower()} "
        for alias, key in cat._aliases:
            if re.search(r"\b" + re.escape(alias).replace(r"\ ", r"\s+")
                         + r"e?s?\b", hay):
                return key
        return ""

    lines = []
    for r in list(service.recipes_by_uid(config).values())[:60]:
        lines.extend(r.get("ingredients", "").splitlines()[:6])
    wrong = [ln for ln in lines if ln.strip()
             and cat.match(ln) != naive_match(ln)]
    check("the gated alias scan matches exactly what the plain one does",
          not wrong, f"{len(wrong)} lines differ, e.g. {wrong[:2]}")


def _lookup_is_one_answer(check):
    print("\none answer to what recipes exist")
    from mealplan import service, store

    real = _snapshot(store)
    try:
        _lookup_checks(check, service, store)
    finally:
        store.load_rte = real


def _lookup_checks(check, service, store):
    config = store.load_config()
    by_uid = service.recipes_by_uid(config)
    cached = {r["uid"] for r in (store.load_cache().get("recipes") or [])
              if r.get("uid")}
    web = {r["uid"] for r in service.web_recipes(config)}

    check("the lookup holds the Paprika library", cached <= set(by_uid))
    check("...and the harvested recipes too", web <= set(by_uid))

    # This is the check that matters, and it is here because the costing and
    # the shopping list each used to build this dict from the Paprika cache
    # alone. A recipe the lookup misses is skipped silently: the planner puts
    # it on Thursday, and then Thursday has no cost and nothing to buy.
    plan = store.load_plan()
    missing = [d.get("name") for d in (plan.get("days") or [])
               if d.get("uid") and d["uid"] not in by_uid]
    check("every night on the current plan can be looked up", not missing,
          ", ".join(str(m) for m in missing))
