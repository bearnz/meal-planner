"""Checks for the grocer.nz price source.

Nothing here touches the network. Every response is a stub, because what these
checks are about is the arithmetic and the refusals -- and because a test suite
that fetches a third party's data every time it runs is exactly the kind of
guest this module's docstring says not to be.

The two things worth knowing they hold:

  A blank pack size means "sold loose, priced per kilogram", and getting that
  backwards prices a $29 chuck steak at $29 for the whole plan.

  A 304 is only safe when there is nothing new to price.
"""

from __future__ import annotations


def run(check):
    _sizes(check)
    _best_price(check)
    _pinning(check)
    _refresh(check)
    _politeness(check)
    _stores(check)
    _store_change(check)
    _store_move_refetches(check)
    _verified(check)
    _live_money(check)


def _sizes(check):
    print("\ngrocer.nz pack sizes")
    from mealplan.grocer import parse_size, unit_price

    for size, want in [("", (1000.0, "g")), ("500g", (500.0, "g")),
                       ("1kg", (1000.0, "g")), ("2l", (2000.0, "ml")),
                       ("330ml", (330.0, "ml")), ("12 x 70g", (840.0, "g")),
                       ("6 x 330ml", (1980.0, "ml")), ("1ea", (1.0, "ea"))]:
        check(f"{size or '(blank)'!r} is {want[0]:g}{want[1]}",
              parse_size(size) == want, str(parse_size(size)))

    # The one that is a judgement rather than a conversion. grocer.nz leaves the
    # size blank for anything sold by weight and quotes those per kilo, so a
    # blank is 1000g and not "one unknown thing".
    check("a loose $28.99 cut is $28.99 per kilo",
          unit_price(2899, "") == (28.99, 1000.0, "g"),
          str(unit_price(2899, "")))
    check("a 2 litre bottle at $5.22 is per 2000ml",
          unit_price(522, "2l") == (5.22, 2000.0, "ml"),
          str(unit_price(522, "2l")))

    # An unreadable size must not become a free item.
    check("an unparseable size prices nothing",
          unit_price(499, "punnet") == (0.0, 0.0, ""),
          str(unit_price(499, "punnet")))


def _best_price(check):
    print("\ngrocer.nz price selection")
    from mealplan.grocer import Price

    plain = Price(1, 2899, 0, 0, "")
    check("no special means the shelf price", plain.best_cent == 2899)

    onsale = Price(1, 2899, 1999, 0, "")
    check("a special is what you would pay", onsale.best_cent == 1999)

    # A club price needs a card this project knows nothing about. Quoting one
    # makes the week look cheaper than the till will.
    club = Price(1, 2899, 0, 1500, "")
    check("a club price is not taken as the price", club.best_cent == 2899,
          "a card this project cannot know about is not a price")

    silly = Price(1, 2899, 3500, 0, "")
    check("a 'special' dearer than the shelf price is ignored",
          silly.best_cent == 2899)


def _pinning(check):
    print("\ngrocer.nz pinning")
    from mealplan.grocer import Product, pin, pinned

    p = Product(id=1672, name="NZ Beef Chuck Steak", brand="", size="",
                category="Meat & Seafood > Beef")
    data: dict = {"items": {}}

    ok, created = pin(data, "beef chuck", p)
    check("pinning an unknown ingredient creates it", ok and created)
    check("...with the name as its own alias",
          data["items"]["beef chuck"]["aliases"] == ["beef chuck"])
    check("...and the unit the pack size implies",
          data["items"]["beef chuck"]["unit"] == "g")
    check("...and it reads back as pinned",
          pinned(data).get("beef chuck", {}).get("id") == 1672)

    ok, created = pin(data, "beef chuck", p)
    check("pinning it again is not a second creation", ok and not created)

    litre = Product(id=5449, name="Standard Milk", brand="", size="2l",
                    category="Dairy")
    pin(data, "milk", litre)
    check("a 2 litre product creates a millilitre item",
          data["items"]["milk"]["unit"] == "ml")

    ok, created = pin(data, "nope", litre, create=False)
    check("create=False refuses to invent an item", not ok and not created)


def _refresh(check):
    print("\ngrocer.nz refresh")
    from mealplan import grocer

    def stub(prices, seen):
        def fetch(store_id, etag=""):
            seen.append(etag)
            if etag == "STALE-OK":
                return {}, etag, True
            return prices, "STALE-OK", False
        return fetch

    real = grocer.fetch_prices
    try:
        prices = {1672: grocer.Price(1672, 2899, 0, 0, ""),
                  1314: grocer.Price(1314, 1399, 999, 0, "")}
        data: dict = {"items": {}}
        grocer.pin(data, "beef chuck",
                   grocer.Product(1672, "NZ Beef Chuck Steak", "", "", ""))
        grocer.pin(data, "pork mince",
                   grocer.Product(1314, "NZ Pork Mince", "", "", ""))

        seen: list[str] = []
        grocer.fetch_prices = stub(prices, seen)
        r = grocer.refresh(data, 241, "PAK'nSAVE", force=True)
        check("both pinned items are priced", len(r["updated"]) == 2,
              str(r["updated"]))
        check("a loose cut lands as a per-kilo price",
              data["items"]["beef chuck"]["prices"]["PAK'nSAVE"]["price"] == 28.99)
        check("...quoted per 1000",
              data["items"]["beef chuck"]["prices"]["PAK'nSAVE"]["per"] == 1000.0)
        check("an item on special takes the special price",
              data["items"]["pork mince"]["prices"]["PAK'nSAVE"]["price"] == 9.99)
        check("...and says what it was",
              data["items"]["pork mince"]["prices"]["PAK'nSAVE"]["special"]["was"]
              == 13.99)
        check("the first fetch sends no ETag", seen[0] == "")

        # Everything is priced now, so the next run may ask to be told nothing.
        seen.clear()
        r = grocer.refresh(data, 241, "PAK'nSAVE", force=True)
        check("a second run sends the ETag", seen[0] == "STALE-OK", str(seen))
        check("...and is told nothing changed", "304" in r["status"], r["status"])

        # The bug this exists for: pin something new, and the ETag would have
        # answered 304 and left it unpriced until grocer.nz happened to change
        # the file. A feature that silently does nothing on the run you cared
        # about is worse than one that is slow.
        grocer.pin(data, "pork loin", grocer.Product(1313, "NZ Pork Loin", "", "", ""))
        seen.clear()
        r = grocer.refresh(data, 241, "PAK'nSAVE", force=True)
        check("a newly pinned item forces a full fetch", seen[0] == "",
              f"sent {seen!r}; a 304 would leave it unpriced")
        check("...and it is reported as no longer stocked, not silently dropped",
              any("pork loin" in m for m in r["missing"]), str(r["missing"]))

        # Too soon, and no --force.
        r = grocer.refresh(data, 241, "PAK'nSAVE", force=False)
        check("a refresh inside the interval is refused",
              "force" in r["status"], r["status"])

        check("nothing pinned is reported, not crashed",
              grocer.refresh({"items": {}}, 241, "PAK'nSAVE",
                             force=True)["status"] == "nothing pinned")
    finally:
        grocer.fetch_prices = real


def _politeness(check):
    print("\ngrocer.nz is a guest")
    from mealplan import grocer

    check("the User-Agent says what this is",
          "meal-planner" in grocer.USER_AGENT
          and "personal" in grocer.USER_AGENT,
          grocer.USER_AGENT)
    check("...and where it came from", "github.com" in grocer.USER_AGENT)
    check("attribution names the source", "grocer.nz" in grocer.ATTRIBUTION)
    check("the refresh floor is at least their cache lifetime",
          grocer.MIN_REFRESH_HOURS >= 8, str(grocer.MIN_REFRESH_HOURS))

    # Every request goes through the robots gate, and the gate fails closed.
    real = grocer.robots_allows
    try:
        grocer.robots_allows = lambda url, agent=None: False
        for name, call in [("search", lambda: grocer.search("x", 241)),
                           ("price fetch", lambda: grocer.fetch_prices(241))]:
            try:
                call()
                ok = False
            except grocer.GrocerError as exc:
                ok = "robots" in str(exc)
            check(f"{name} refuses when robots.txt says no", ok)
    finally:
        grocer.robots_allows = real



def _stores(check):
    print("\ngrocer.nz store list")
    from mealplan import grocer

    rows = grocer.load_stores()
    check("the shipped store list is not empty", len(rows) > 100, str(len(rows)))
    check("Royal Oak is in it and is 222",
          (grocer.store_by_id(222) or {}).get("name") == "PAK'nSAVE Royal Oak",
          str(grocer.store_by_id(222)))
    check("an unknown id is empty rather than an error",
          grocer.store_by_id(99999999) == {})
    check("searching finds by name", any(
        "Royal Oak" in s["name"] for s in grocer.find_stores("royal oak")))

    # Only chains prices.json has a column for can be used. A Fresh Choice store
    # would fetch and price perfectly and then be costed against nothing.
    check("a priced chain maps to its price column",
          grocer.store_name_for("Pak'nSave") == "PAK'nSAVE",
          grocer.store_name_for("Pak'nSave"))
    check("...case and spelling from the catalogue, not ours",
          grocer.store_name_for("PAK'NSAVE") == "PAK'nSAVE")
    check("an unpriced chain maps to nothing",
          grocer.store_name_for("Fresh Choice") == "",
          "inventing a column makes a price that silently does nothing")


def _store_change(check):
    print("\nchanging shop")
    from mealplan import grocer, service

    config: dict = {"grocer": {"store_id": 241, "store_name": "PAK'nSAVE Mt Albert"},
                    "primary_store": "PAK'nSAVE"}

    service._set_store(config, 222)
    check("picking a store records it", config["grocer"]["store_id"] == 222)
    check("...with its name", "Royal Oak" in config["grocer"]["store_name"])

    # The one that is easy to forget: which shelf is read and which column the
    # week is costed against are two settings, and they have to move together.
    nw = next(s for s in grocer.load_stores() if s["vendor"] == "New World")
    service._set_store(config, nw["id"])
    check("changing chain also moves what the week is costed against",
          config["primary_store"] == "New World", config["primary_store"])

    fc = next((s for s in grocer.load_stores() if s["vendor"] == "Fresh Choice"),
              None)
    if fc:
        before = dict(config["grocer"])
        service._set_store(config, fc["id"])
        check("a chain with no price column is refused, not half-applied",
              config["grocer"] == before, str(config["grocer"]))

    service._set_store(config, 0)
    check("clearing the store is allowed", config["grocer"]["store_id"] == 0)

    # A change of shop has to count as a change, or the save is treated as a
    # no-op and the page reports nothing while config.json has moved.
    a = service._snapshot({"grocer": {"store_id": 241}})
    b = service._snapshot({"grocer": {"store_id": 222}})
    check("a different store is a different snapshot", a != b)


def _store_move_refetches(check):
    print("\nmoving shop re-fetches")
    from mealplan import grocer

    real = grocer.fetch_prices
    try:
        seen: list[str] = []

        def stub(store_id, etag=""):
            seen.append(etag)
            return {1672: grocer.Price(1672, 2999, 0, 0, "")}, "TAG", False

        grocer.fetch_prices = stub
        data: dict = {"items": {}}
        grocer.pin(data, "beef chuck",
                   grocer.Product(1672, "NZ Beef Chuck Steak", "", "", ""))
        grocer.refresh(data, 241, "PAK'nSAVE", force=True)
        seen.clear()

        # Same shop, everything priced: the ETag is worth sending.
        grocer.refresh(data, 241, "PAK'nSAVE", force=True)
        check("staying put may ask to be told nothing", seen[0] == "TAG", str(seen))

        # Different shop: the old ETag describes the old shop's price list, and
        # every price in the file was fetched somewhere else. A 304 here would
        # leave Royal Oak's plan costed at Mt Albert's prices.
        seen.clear()
        grocer.refresh(data, 222, "PAK'nSAVE", force=True)
        check("moving shop ignores the old ETag", seen[0] == "",
              f"sent {seen!r}; a 304 keeps the other shop's prices")
        check("...and the new price lands", data["items"]["beef chuck"]
              ["prices"]["PAK'nSAVE"]["price"] == 29.99)
    finally:
        grocer.fetch_prices = real



def _verified(check):
    print("\nreal prices versus shipped guesses")
    from mealplan import pricing

    data = {
        "currency": "NZD", "primary_store": "S", "stores": ["S"],
        "items": {
            "beef mince": {"aliases": [], "unit": "g",
                           "prices": {"S": {"price": 20.0, "per": 1000,
                                            "checked": "2026-08-30"}}},
            "paprika": {"aliases": [], "unit": "g",
                        "prices": {"S": {"price": 100.0, "per": 1000}}},
        },
    }
    cat = pricing.Catalogue(data)
    check("a price with a checked date is verified", cat.is_verified("beef mince", "S"))
    check("...and one without is not", not cat.is_verified("paprika", "S"))
    check("an item with no price at that store is not verified",
          not cat.is_verified("beef mince", "elsewhere"))

    c = pricing.cost_recipe("500g beef mince\n10g paprika", 1, cat, "S")
    check("both lines priced", c.coverage == 1.0, str(c.coverage))
    # $10 of real mince, $1 of guessed paprika.
    check("the verified share is by money, not by line count",
          abs(c.verified_share - (10.0 / 11.0)) < 0.01,
          f"{c.verified_share:.3f} -- half the lines, but most of the money")

    none = pricing.cost_recipe("10g paprika", 1, cat, "S")
    check("nothing verified is a zero share", none.verified_share == 0.0)

    free = pricing.cost_recipe("", 1, cat, "S")
    check("a recipe costing nothing does not divide by zero",
          free.verified_share == 0.0)


def _live_money(check):
    """The money block must not outlive the prices it was built from."""
    print("\nthe page reflects the prices as they are now")
    import json
    import shutil
    import tempfile
    from pathlib import Path

    from mealplan import service, store, web

    real = {k: getattr(store, k) for k in
            ("CONFIG_PATH", "PLAN_PATH", "HISTORY_PATH", "UNDO_PATH",
             "CACHE_PATH", "PRICES_PATH")}
    tmp = Path(tempfile.mkdtemp())
    try:
        for name, src in [("config.json", real["CONFIG_PATH"]),
                          ("recipes.json", real["CACHE_PATH"]),
                          ("prices.json", real["PRICES_PATH"])]:
            shutil.copy(src, tmp / name)
        store.CONFIG_PATH = tmp / "config.json"
        store.CACHE_PATH = tmp / "recipes.json"
        store.PRICES_PATH = tmp / "prices.json"
        store.PLAN_PATH = tmp / "plan.json"
        store.HISTORY_PATH = tmp / "history.json"
        store.UNDO_PATH = tmp / "undo.json"
        service.catalogue.cache_clear() if hasattr(service.catalogue, "cache_clear") else None

        client = web.app.test_client()
        client.get("/")
        plan = json.loads(store.PLAN_PATH.read_text(encoding="utf-8"))
        check("a plan gets saved", bool(plan.get("days")))

        # Freeze a wrong answer into the saved plan, the way a real one goes
        # stale the moment prices change under it.
        plan["money"] = {"per_portion_avg": 999.99, "freshness": {"total": 1,
                                                                  "checked": 0}}
        for d in plan["days"]:
            d["cost"] = {"per_portion": 999.99, "usable": True,
                         "confident": True, "verified": 0}
        store.PLAN_PATH.write_text(json.dumps(plan), encoding="utf-8")

        page = client.get("/").get_data(as_text=True)
        check("a stale saved money block is not shown", "999.99" not in page,
              "prices change without the week changing; a saved figure is a "
              "photograph of prices.json at plan time")
    finally:
        for k, v in real.items():
            setattr(store, k, v)
        shutil.rmtree(tmp, ignore_errors=True)
