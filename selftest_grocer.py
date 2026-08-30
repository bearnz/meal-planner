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
    _freshness_is_about_your_shop(check)
    _yield(check)
    _buy_note(check)
    _cli_is_wired(check)
    _pantry(check)
    _perishable_staples(check)
    _the_same_thing_written_differently(check)
    _counted_things(check)
    _shopping_split(check)
    _by_item(check)
    _shopping_page(check)
    _availability(check)
    _mapped_by_hand(check)
    _sides(check)


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
        "currency": "NZD", "stores": ["S"],
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


def _freshness_is_about_your_shop(check):
    """The progress bar has to be a number you can actually move."""
    print("\nfreshness counts the shop you use")
    from mealplan import pricesrc

    data = {
        "items": {
            "one": {"prices": {"A": {"price": 1, "checked": "2026-08-30"},
                               "B": {"price": 1}, "C": {"price": 1}}},
            "two": {"prices": {"A": {"price": 1}, "B": {"price": 1}}},
            "three": {"prices": {"B": {"price": 1}}},
        },
    }
    f = pricesrc.freshness(data, "A")
    check("only the chosen store is counted", f["total"] == 2, str(f["total"]))
    check("...and its checked prices", f["checked"] == 1, str(f["checked"]))
    check("...giving a share that can reach 1.0", f["share"] == 0.5, str(f["share"]))
    check("the whole-file count is still reported", f["every"] == 6, str(f["every"]))
    check("an item with no price at that store is absent, not unchecked",
          f["absent"] == 1, str(f["absent"]))

    # The bug: counting every chain gave a ceiling nothing could move past.
    # Here 4 of 6 entries are at shops nobody will ever check, so the old
    # denominator capped an entirely finished job at 33%.
    check("counting all chains would cap a finished job well under 100%",
          f["every"] == 6 and f["total"] == 2,
          f"{f['every']} entries across chains, {f['total']} at the one shop: "
          f"checking every price you could would have read as 33% done")

    # No fallback to the price file any more: where you shop is config.json's
    # to say. Asking with no store means the whole file, not somebody's guess
    # at which shop you meant.
    allof = pricesrc.freshness(data)
    check("no store named counts the whole file rather than guessing one",
          allof["store"] == "" and allof["total"] == 6,
          f"{allof['store']!r}, {allof['total']}")

    # One source of truth for where you shop. The shipped price file used to
    # carry its own primary_store saying New World while config.json said
    # PAK'nSAVE, and everything that asked the file got a confident answer
    # about a shop nobody uses. Re-adding the field puts that back.
    from mealplan import pricing, store as _store

    shipped = _store.load_prices()
    check("the price file does not say where you shop",
          "primary_store" not in shipped,
          "that belongs in config.json; two copies is how they disagree")

    cat = pricing.Catalogue(shipped, "Woolworths")
    check("the catalogue takes its store from the caller",
          cat.primary == "Woolworths", cat.primary)
    check("...falling back to the first listed store, not to the file",
          pricing.Catalogue({"stores": ["X", "Y"]}).primary == "X")


def _yield(check):
    """Buying the whole thing and breaking it down yourself."""
    print("\nbuying whole and preparing it yourself")
    from mealplan import grocer

    check("no yield stated means none is lost", grocer._yield_of({}) == 1.0)
    check("a stated yield is used", grocer._yield_of({"yield": 0.68}) == 0.68)
    # A typo must not multiply food or divide by nothing.
    for bad in (0, -1, 1.5, "nonsense", None):
        check(f"a yield of {bad!r} is refused and read as no loss",
              grocer._yield_of({"yield": bad}) == 1.0)

    real = grocer.fetch_prices
    try:
        # Bone-in thigh at $8.89/kg. Boneless fillet on the same shelf is
        # $15.99. Buying bone-in is only worth it because of the yield, and
        # ignoring the yield is what makes it look like a free lunch.
        grocer.fetch_prices = lambda s, etag="": (
            {26814: grocer.Price(26814, 889, 0, 0, "")}, "T", False)

        plain: dict = {"items": {}}
        grocer.pin(plain, "chicken thigh",
                   grocer.Product(26814, "Bone In Thighs", "", "", ""))
        grocer.refresh(plain, 222, "S", force=True)
        shelf = plain["items"]["chicken thigh"]["prices"]["S"]["price"]
        check("with no yield the shelf price is taken as is", shelf == 8.89,
              str(shelf))

        boned: dict = {"items": {}}
        grocer.pin(boned, "chicken thigh",
                   grocer.Product(26814, "Bone In Thighs", "", "", ""),
                   yield_=0.68, note="bought bone-in and boned out")
        r = grocer.refresh(boned, 222, "S", force=True)
        got = boned["items"]["chicken thigh"]["prices"]["S"]["price"]
        check("a yield divides the shelf price", abs(got - 8.89 / 0.68) < 0.01,
              f"${got} -- a kilo of bone-in is 680g of meat")
        check("...which is dearer than the label, not cheaper", got > 8.89,
              "the whole point is that some of what you buy is bone")
        check("...and still beats the boneless fillet at $15.99", got < 15.99,
              "otherwise nobody would bother boning them out")
        check("the refresh says a yield was applied",
              any("68% yield" in line for line in r["updated"]), str(r["updated"]))

        # 44% is the size of the mistake this exists to stop.
        check("ignoring the yield would understate it badly",
              abs(shelf / got - 0.68) < 0.01,
              "the shelf price is 68% of the true cost of the meat")
    finally:
        grocer.fetch_prices = real

    # A pin with no yield and no note stays as small as it was.
    clean: dict = {"items": {}}
    grocer.pin(clean, "beef mince", grocer.Product(1, "Mince", "", "", ""))
    check("a plain pin gains no yield field",
          "yield" not in clean["items"]["beef mince"]["grocer"])


def _buy_note(check):
    """The shopping list has to say what to put in the trolley."""
    print("\nwhat to actually buy")
    from mealplan import service

    note = service.buy_note("500g shredded Colby cheese")
    check("a line priced from a block says to buy the block",
          "Colby" in (note.get("product") or ""), str(note))
    check("...and why", "grate" in (note.get("note") or "").lower(), str(note))

    check("a line priced from the thing itself says nothing",
          not service.buy_note("500g beef mince"),
          "a note on every line is a note nobody reads")
    check("a heading gets no note", not service.buy_note("SAUCE:"))
    check("an unknown ingredient gets no note",
          not service.buy_note("2 tbsp powdered unobtainium"))


def _cli_is_wired(check):
    """Every command main() dispatches to actually exists.

    Cheap, and it exists because an edit that replaced one command in run.py
    silently deleted the four defined after it. Everything still imported,
    every test still passed, and the failure only showed up by running a
    command by hand -- the suite drives the service layer, and nothing was
    checking that the CLI could still reach it.
    """
    print("\nthe command line is wired up")
    import ast

    tree = ast.parse(pathlib.Path("run.py").read_text(encoding="utf-8"))
    defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    called = {n.func.id for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
              and n.func.id.startswith(("cmd_", "_grocer"))}
    missing = sorted(called - defined)
    check("every command main() dispatches to is defined", not missing,
          ", ".join(missing))

    expected = {"cmd_sync", "cmd_plan", "cmd_scores", "cmd_price", "cmd_receipt",
                "cmd_missing", "cmd_stale", "cmd_grocer_stores",
                "cmd_grocer_find", "cmd_grocer_pin", "cmd_grocer_refresh"}
    gone = sorted(expected - defined)
    check("no command has gone missing", not gone, ", ".join(gone))


import pathlib  # noqa: E402 - used by the check above


def _pantry(check):
    """Cupboard items stay off the shopping list proper."""
    print("\npantry staples")
    from mealplan import service

    for text in ["2 tbsp oyster sauce", "1 cup jasmine rice", "1 tsp star anise",
                 "2 tsp fennel seeds", "1/2 tsp tumeric", "1 tbsp cornflour",
                 "2 tbsp mirin", "1 tbsp worcestershire sauce",
                 "1 tsp baking soda", "3 tbsp soy sauce",
                 "1/4 tsp dried oregano", "1 tbsp olive oil"]:
        check(f"{text!r} is a cupboard item", service.is_pantry(text))

    for text in ["500g beef mince", "6 chicken thighs", "2 onions",
                 "300g baby spinach", "1kg potatoes"]:
        check(f"{text!r} is not", not service.is_pantry(text))

    check("a heading is not a pantry item", not service.is_pantry("SAUCE:"))
    check("an unknown ingredient is not assumed to be in the cupboard",
          not service.is_pantry("2 tbsp powdered unobtainium"),
          "guessing yes would quietly drop a real purchase off the list")

    # The allowance is the threshold, and it has to be: two kilos of rice for
    # a paella is a purchase, not "there is rice in the cupboard".
    check("a staple bought in bulk is still a purchase",
          not service.is_pantry("2 kg rice"),
          "the free_allowance the costing uses is the same line here")
    check("...while a normal amount is not", service.is_pantry("150g rice"))

    # One flag, two consumers. If these ever drift, a line is free in the
    # costing and still on the shopping list, or the reverse.
    cat = service.catalogue()
    staples = [k for k, v in cat.items.items() if v.get("staple")]
    check("every staple has an allowance",
          all(float((cat.items[k].get("free_allowance") or 0)) > 0 for k in staples),
          str([k for k in staples
               if not float(cat.items[k].get("free_allowance") or 0)]))
    check("the named pantry items are all staples",
          all((cat.items.get(cat.match(t)) or {}).get("staple")
              for t in ["oyster sauce", "rice", "star anise", "cinnamon",
                        "dried oregano", "sesame seeds"]))


def _perishable_staples(check):
    """A staple that goes off is shown, not folded into the cupboard."""
    print("\nperishable staples stay on the list")
    from mealplan import service

    # Owned in the same sense as oyster sauce, and gone by Thursday. Hiding
    # these in the cupboard block is what made the list wrong rather than
    # merely long -- nobody has a fortnight-old bunch of coriander.
    for text in ["2 garlic cloves", "1 tbsp butter", "1 tsp grated ginger",
                 "1 red chilli", "2 tbsp chopped parsley"]:
        check(f"{text!r} is a fridge staple, not a cupboard one",
              service.is_perishable(text), _kind(service, text))
        check(f"...so {text!r} is not folded away", not service.is_pantry(text))

    # The two are alternatives, not a hierarchy: nothing may be both, or the
    # line is drawn twice and bought twice.
    cat = service.catalogue()
    perish = [k for k, v in cat.items.items() if v.get("perishable")]
    check("something is marked perishable", bool(perish), str(perish))
    check("every perishable is also a staple",
          all((cat.items[k] or {}).get("staple") for k in perish),
          "perishable is a split within staple, not a third kind of item")
    check("every perishable has an allowance",
          all(float(cat.items[k].get("free_allowance") or 0) > 0 for k in perish))

    # ...and the allowance still decides. A stick of butter is shopping.
    check("a perishable bought in bulk is a purchase like any other",
          _kind(service, "250g butter") == "",
          _kind(service, "250g butter"))
    check("...while a knob of it is not", _kind(service, "1 tbsp butter")
          == "perishable")

    # Dried is not fresh, and putting perishables on the list is what made that
    # visible: three lines of dried chillies had always matched the fresh entry
    # and nobody saw it while they were folded into the cupboard block.
    for text in ["2.3 g guajillo chillies, dried (~4 pieces)",
                 "1/4 cup dried Chinese chillis , whole",
                 "2 tsp chilli flakes"]:
        check(f"{text[:34]!r} is a cupboard chilli",
              _kind(service, text) == "pantry", _kind(service, text))
    check("a fresh chilli still is not",
          _kind(service, "1 red chilli, finely sliced") == "perishable")
    check("water you made yourself is not shopping",
          _kind(service, "1/4 cup chilli soaking water (reserve)") == "pantry",
          "soaking liquid is a step in the recipe, not a line on the list")

    # The shopping list must show them where they will be read.
    from mealplan import store as _store
    groups = service.shopping_list(_store.load_plan(), 2)
    shown = [x for g in groups for x in g["ingredients"] if x["kind"] == "perishable"]
    hidden = [x for g in groups for x in g["pantry"] if x["kind"] == "perishable"]
    check("perishables appear on the list proper", bool(shown),
          "a week of dinners with no butter, garlic or herbs is suspicious")
    check("...and never in the cupboard block", not hidden,
          str([x["text"] for x in hidden]))


def _kind(service, text):
    return service._shape_line(text, True, text, service.catalogue())["kind"]


def _the_same_thing_written_differently(check):
    """Names a person supplied for things the catalogue was missing.

    Every line here is a fact about ingredients rather than about code, and the
    alias table is where this project writes such facts down. They are checked
    because that is also the easiest kind of knowledge to lose: an alias list
    tidied, a key renamed, and an ingredient quietly stops being priced with no
    error anywhere -- which is how three of these came to be missing.
    """
    print("\ningredients under a second name")
    from mealplan import pricing, service

    cat = service.catalogue()

    def key_for(text):
        line = pricing.parse_line(text)
        return cat.key_for(line)

    for text, want in [
            # The same cut, written either way round.
            ("0.12 kg chuck beef , cut into 6 large pieces", "beef chuck"),
            ("500g beef chuck", "beef chuck"),
            ("1kg chuck steak, diced", "beef chuck"),
            # Allspice, as one word and as two, and the blend recipes offer
            # in its place.
            ("1/4 tsp all spice powder (sub mixed spice)", "ground spices"),
            ("1 tsp allspice", "ground spices"),
            # Stems, stalks and sticks are all one vegetable.
            ("1/2 celery stems , roughly chopped", "celery"),
            ("2 celery stalks, diced", "celery"),
            ("3 sticks celery", "celery"),
            # Already right, and worth pinning: the reason parmesan looked
            # unmapped was a line with no quantity in it, not a missing entry.
            ("Parmesan cheese , finely grated, for serving", "parmesan"),
            ("3/4 cup stale bread like sourdough, ciabatta", "bread"),
    ]:
        check(f"{text[:38]!r} is {want}", key_for(text) == want,
              repr(key_for(text)))

    # Parmesan is not cheddar. "cheese" is an alias of cheddar and the matcher
    # takes the longest alias first, which is the only thing keeping these
    # apart -- so it is worth an assertion rather than a comment.
    check("parmesan is not swallowed by the generic cheese alias",
          key_for("50g grated parmesan cheese") == "parmesan",
          key_for("50g grated parmesan cheese"))
    check("...and plain cheese still is cheddar",
          key_for("1 cup grated cheese") == "cheddar")

    # Bread is assumed to be in the house. A loaf's worth is not.
    check("a handful of breadcrumbs is a cupboard item",
          service.is_pantry("3/4 cup stale bread, torn"))
    check("...and most of a loaf is shopping",
          not service.is_pantry("400g sourdough, cubed"))


def _counted_things(check):
    """Vegetables you buy whole, and the units people actually say."""
    print("\nthings that are counted rather than weighed")
    from mealplan import pricing, service

    total = service._week_total

    # The rule: round up a recipe at a time, then add. Two dinners each wanting
    # half an onion are two onions -- the half Tuesday did not use is not the
    # half Thursday gets, it is soft by then.
    check("two half onions on two nights are two onions",
          total({"Tue": 0.5, "Thu": 0.5}, "each") == 2.0,
          str(total({"Tue": 0.5, "Thu": 0.5}, "each")))
    check("...and rounding the week's total instead would say one",
          round(sum([0.5, 0.5])) == 1,
          "which is the version that sends you home an onion short")
    check("half an onion twice in ONE recipe is still one onion",
          total({"Tue": 1.0}, "each") == 1.0,
          "a recipe at a time, not a line at a time")
    check("a whole number is not nudged up",
          total({"Tue": 3.0, "Thu": 1.0}, "each") == 4.0,
          str(total({"Tue": 3.0, "Thu": 1.0}, "each")))
    check("1.2 on one night is two",
          total({"Tue": 1.2}, "each") == 2.0)

    # Weights are not touched: rounding those up would buy a kilo of mince to
    # cover 900g, which is a different and much worse habit.
    check("grams simply add", total({"Tue": 250.0, "Thu": 300.0}, "g") == 550.0)
    check("...and are not rounded up", total({"Tue": 0.4}, "g") == 0.4)

    # The noun a thing is counted in. Nothing derives these -- garlic comes in
    # cloves, celery in stalks, broccoli in heads, and there is no rule.
    cat = service.catalogue()
    label = service._quantity_label
    for key, want in [("garlic", "3 cloves"), ("celery", "3 stalks"),
                      ("broccoli", "3 heads"), ("fennel", "3 bulbs")]:
        got = label(3.0, "each", cat.items.get(key) or {})
        check(f"{key} is counted in {want.split()[1]}", got == want, got)
    check("one of something is singular",
          label(1.0, "each", cat.items["garlic"]) == "1 clove",
          label(1.0, "each", cat.items["garlic"]))
    check("something with no noun is just a number",
          label(2.0, "each", cat.items.get("onion") or {}) == "2")

    # Garlic is quoted in cloves and sold in bulbs, and both are worth saying.
    hint = service._pack_hint
    check("thirteen cloves is two bulbs",
          hint(13.0, cat.items["garlic"]) == "2 bulbs",
          hint(13.0, cat.items["garlic"]))
    check("...and ten is one", hint(10.0, cat.items["garlic"]) == "1 bulb")
    check("...and one clove still means buying a bulb",
          hint(1.0, cat.items["garlic"]) == "1 bulb")
    check("nothing sold in packs gets a pack hint",
          hint(3.0, cat.items["onion"]) == "",
          "an onion is sold as an onion")

    # Garlic moved from grams to cloves. Both spellings of an amount have to
    # arrive as cloves, or the allowance and the count are in different units.
    def amount(text):
        line = pricing.parse_line(text)
        key = cat.key_for(line)
        return cat.to_item_units(line, key)[0] if key else None

    check("'3 garlic cloves' is 3", amount("3 garlic cloves") == 3.0,
          str(amount("3 garlic cloves")))
    check("'15g garlic, minced' is 3 cloves", amount("15g garlic, minced") == 3.0,
          str(amount("15g garlic, minced")))
    check("'1 tbsp minced garlic' is 3 cloves",
          amount("1 tbsp minced garlic") == 3.0,
          str(amount("1 tbsp minced garlic")))
    check("garlic's allowance is quoted in cloves too",
          float(cat.items["garlic"]["free_allowance"]) < 20,
          "a 30-clove allowance is the gram figure left behind")

    # "4 onions", not "4 onion". +s with an escape hatch, since English plurals
    # are not derivable and the alternative is "2 cods".
    plural = service._plural
    check("more than one onion is onions",
          plural("onion", 4.0, "each", cat.items["onion"]) == "onions")
    check("one onion is an onion",
          plural("onion", 1.0, "each", cat.items["onion"]) == "onion")
    check("cod says its own plural", plural("cod", 2.0, "each",
          cat.items["cod"]) == "cod", "'2 cods' is what the rule would give")
    check("chillies too", plural("chilli", 3.0, "each",
          cat.items["chilli"]) == "chillies")
    check("a weight is left alone",
          plural("butter", 60.0, "g", cat.items["butter"]) == "butter")
    check("an entry that names its unit is left alone",
          plural("garlic", 13.0, "each", cat.items["garlic"]) == "garlic",
          "'13 cloves garlics' -- the garlic is not the thing being counted")

    # The bulb and the seed are two ingredients, kept apart by the matcher
    # taking the longest alias first and by nothing else.
    def key_for(text):
        line = pricing.parse_line(text)
        return cat.key_for(line)

    check("'1/4 medium fennel, chopped' is the vegetable",
          key_for("1/4 medium fennel , chopped (Note 2)") == "fennel",
          repr(key_for("1/4 medium fennel , chopped (Note 2)")))
    check("'2 tsp fennel seeds' is still a spice",
          key_for("2 tsp fennel seeds") == "whole spices",
          repr(key_for("2 tsp fennel seeds")))


def _shopping_split(check):
    print("\nthe shopping list splits pantry from purchases")
    from mealplan import service, store as _store

    groups = service.shopping_list(_store.load_plan(), 2)
    check("there is something to shop for", bool(groups))
    if not groups:
        return
    check("every group has both lists",
          all("ingredients" in g and "pantry" in g for g in groups))
    check("something was moved to the pantry",
          any(g["pantry"] for g in groups),
          "a week of dinners with no cupboard items at all is suspicious")
    check("nothing is in both",
          all(not ({i["text"] for i in g["ingredients"]}
                   & {i["text"] for i in g["pantry"]}) for g in groups))
    check("no line was lost in the split",
          all(all(x["kind"] != "pantry" for x in g["ingredients"]) for g in groups))
    check("headings stay on the list proper, not in the cupboard",
          all(not any(i["heading"] for i in g["pantry"]) for g in groups))


def _by_item(check):
    """The merged view: one entry per ingredient, added up across the week."""
    print("\nthe shopping list merged by item")
    from mealplan import service, store as _store

    plan = _store.load_plan()
    nights = service.shopping_list(plan, 2)
    merged = service.shopping_by_item(plan, 2)
    check("the merged list has both halves",
          "ingredients" in merged and "pantry" in merged)
    items = merged["ingredients"] + merged["pantry"]
    check("there is something in it", bool(items))
    if not items:
        return

    # The whole point: fewer entries than lines, because the repeats collapsed.
    lines = [x for g in nights for x in g["ingredients"] + g["pantry"]
             if not x["heading"]]
    check("merging shortens the list", len(items) < len(lines),
          f"{len(items)} entries from {len(lines)} lines")
    check("every entry knows which nights wanted it",
          all(e["parts"] and e["nights"] >= 1 for e in items))
    check("something actually merged", any(e["nights"] > 1 for e in items),
          "seven dinners sharing no ingredient at all would be remarkable")

    # Nothing may be dropped on the way in. A merged list that quietly loses a
    # line is worse than no merged list, because it looks complete.
    check("no line was lost in the merge",
          sum(len(e["parts"]) for e in items) == len(lines),
          f"{sum(len(e['parts']) for e in items)} parts from {len(lines)} lines")
    check("headings are dropped rather than merged",
          not any("SAUCE" in e["name"].upper() and e["name"].endswith(":")
                  for e in items))
    check("nothing is in both halves",
          not ({e["key"] for e in merged["ingredients"]}
               & {e["key"] for e in merged["pantry"]}))

    # A total is only offered when every contributing line converted. One
    # "handful of parsley" among three weighed ones makes the sum a lie.
    totalled = [e for e in items if e["amount"] is not None]
    check("some entries carry a total", bool(totalled))
    check("a total always has a unit to be in",
          all(e["unit"] for e in totalled),
          str([e["name"] for e in totalled if not e["unit"]]))
    check("a total is at least as big as any one night's share",
          all(e["amount"] > 0 for e in totalled),
          str([e["name"] for e in totalled if e["amount"] <= 0]))

    # A count is rounded up, because a shop does not sell a fifth of a broccoli
    # and the only direction of error that matters here sends you home without
    # dinner. The unit word "each" is the catalogue's, and never printed.
    label = service._quantity_label
    check("a fraction of a countable thing rounds up to one",
          label(0.2, "each") == "1", label(0.2, "each"))
    check("...and 1.4 of them is two", label(1.4, "each") == "2")
    check("...and a whole number is left alone", label(3.0, "each") == "3")
    check("a weight is not rounded up, only tidied",
          label(60.29, "g") == "60 g", label(60.29, "g"))
    check("a small weight keeps a decimal", label(2.24, "g") == "2.2 g",
          label(2.24, "g"))
    check("no amount means no label", label(None, "g") == "")
    check("'each' is never printed", "each" not in
          " ".join(e["qty"] for e in items))

    # Merging is by catalogue key, so the alias table does the synonym work.
    keys = [e["key"] for e in items]
    check("entries are unique", len(keys) == len(set(keys)))
    check("an unmatched ingredient is grouped under its own name only",
          all(k.startswith("?") or k in service.catalogue().items for k in keys),
          str([k for k in keys
               if not k.startswith("?") and k not in service.catalogue().items]))
    check("the list is ordered, not in dictionary insertion order",
          [e["name"].lower() for e in merged["ingredients"]]
          == sorted(e["name"].lower() for e in merged["ingredients"]),
          "shelf order replaces this later; it still has to be *an* order")

    # A staple is judged a night at a time, deliberately. Seven teaspoons of
    # olive oil is not a bottle of olive oil to buy.
    oil = [e for e in merged["pantry"] if e["key"] == "olive oil"]
    if oil:
        check("a staple used all week is still a cupboard item",
              oil[0]["kind"] == "pantry",
              "re-testing the weekly sum would put every staple back on the list")


def _shopping_page(check):
    """Both views render, and the ticks have something stable to hang on."""
    print("\nthe shopping page")
    from mealplan import web

    client = web.app.test_client()
    for query, want in [("", "By night"), ("?by=item", "By item")]:
        r = client.get("/shopping" + query)
        check(f"/shopping{query} loads", r.status_code == 200, str(r.status_code))
        page = r.get_data(as_text=True)
        check(f"...and offers the other view", 'class="seg"' in page)
        check(f"...and every box has something to be remembered by",
              page.count("data-tick=") == page.count('type="checkbox"'),
              "a checkbox with no key is a tick that vanishes on the next reload")

    page = client.get("/shopping?by=item").get_data(as_text=True)
    check("the merged view is one column, not a grid of cards",
          'class="shop one"' in page)
    check("the tick store is scoped to a plan",
          "data-plan=" in page,
          "unscoped ticks show last week's shopping crossed off this week's")


def _availability(check):
    """A cut the butcher does not always have keeps its recipe out of the week."""
    print("\ningredients the shop does not have")
    from mealplan import pricing, service, store

    cat = service.catalogue()
    gone = cat.unavailable
    check("something is marked unavailable", bool(gone), str(sorted(gone)))
    check("...and absent means available",
          not any(v.get("available") is False
                  for k, v in cat.items.items() if k not in gone),
          "nothing should have to be marked to keep working")
    check("an unavailable item still has a price",
          all(cat.items[k].get("prices") for k in gone),
          "it comes back; the price is what it costs when it does")

    # It is the recipe that is held, not the line. A dinner built on beef
    # cheeks is not a dinner with beef cheeks left out of it.
    check("a recipe needing one is spotted",
          cat.unavailable_in("1.5 kg/3lb beef cheeks , (4 large or 6 small)")
          == ["beef cheeks"])
    check("...and one that does not, is not",
          cat.unavailable_in("500g beef mince\n2 onions") == [])
    check("a heading is not an ingredient",
          cat.unavailable_in("BEEF CHEEKS:") == [],
          "a section title naming the cut is not the cut")
    check("each unavailable item is named once",
          cat.unavailable_in("1kg beef cheeks\n500g more beef cheek")
          == ["beef cheeks"])

    # Held back rather than filtered out: the difference is that somebody is
    # told, and can act on it by marking the cut available again.
    config = store.load_config()
    library, held, _ = service.eligible_and_held(config)
    check("the held-back list is populated", bool(held), str(held))
    check("nothing is both in play and held back",
          not ({s.uid for s in library} & {h["uid"] for h in held}))
    check("every held-back recipe says what it is waiting for",
          all(h["needs"] and h["name"] for h in held))
    check("held-back recipes are the ones that need an unavailable item",
          all(set(h["needs"]) <= gone for h in held),
          str([h["needs"] for h in held]))
    check("load_library returns just the playable ones",
          len(service.load_library(config)) == len(library))

    # An aversion and an absence are different statements and must not be
    # conflated: one is permanent and about the household, the other is about
    # this week and about the shop.
    check("an unavailable ingredient is not an aversion",
          not any(h["name"] in str(config.get("avoid_ingredients") or [])
                  for h in held),
          "nothing here was excluded for being disliked")

    # And where one does reach the shopping list -- a plan made before the cut
    # was marked -- it says so on the page read in front of the cabinet.
    note = service.buy_note("1.5 kg beef cheeks")
    check("the shopping list would flag it", note.get("unavailable") is True,
          str(note))
    check("...and an ordinary line carries no such flag",
          not service.buy_note("500g beef mince").get("unavailable"))


def _mapped_by_hand(check):
    """The gaps that were closed one at a time, and must stay closed."""
    print("\ningredients that were once unpriceable")
    from mealplan import pricing, service

    cat = service.catalogue()

    def key(text):
        return cat.key_for(pricing.parse_line(text))

    # Products rather than ingredients. Every one of these was "not in the
    # price list" and blocked a whole recipe's figure.
    for raw, want in [
            ("1 cup Pams Smokey BBQ Sauce", "bbq sauce"),
            ("500g bag Pams Superfoods Super Slaw", "coleslaw"),
            ("12 Pams Finest Brioche Burger Buns, halved", "burger bun"),
            ("1 soft hamburger roll, buttered and toasted", "burger bun"),
            ("1 cup Soy Vay Veri Veri Teriyaki sauce", "teriyaki sauce"),
            ("400g / 14oz thick-cut chips , frozen", "frozen chips"),
            ("2 cups frozen diced carrots, peas, corn", "frozen mixed vegetables"),
            ("5 x 20cm / 8\" pita breads", "pita bread"),
            ("3 cups vegetables grated or finely chopped", "mirepoix")]:
        check(f"{raw[:36]!r} is {want}", key(raw) == want, repr(key(raw)))

    # Two that were matching the wrong thing rather than nothing, which is the
    # worse failure: a number came out, and it was for another ingredient.
    check("'whole-egg mayonnaise' is mayonnaise, not eggs",
          key("3 tbsp whole-egg mayonnaise") == "mayonnaise",
          repr(key("3 tbsp whole-egg mayonnaise")))
    check("'apple juice' is juice, not apples",
          key("1 cup apple juice") == "apple juice",
          repr(key("1 cup apple juice")))
    check("'green onions' are spring onions, not onions",
          key("2 green onions , finely sliced") == "spring onion",
          repr(key("2 green onions , finely sliced")))

    # Cuts the household would swap for braising steak. Not chuck by any
    # butcher's definition -- chuck is what gets bought instead, which is the
    # question this file answers.
    for raw in ("2 lbs. flank steak, cut into thin strips",
                "500g/1 lb thinly sliced tenderloin, flank, sirloin/Porterhouse"):
        check(f"{raw[:34]!r} is priced as braising steak",
              key(raw) == "beef chuck", repr(key(raw)))

    # A cup of a vegetable the catalogue counts whole. One cup of diced onion
    # is one medium onion, which is the conversion everybody already uses.
    def amount(text):
        line = pricing.parse_line(text)
        return cat.to_item_units(line, cat.key_for(line))[0]

    got = amount("1 cup diced white onion")
    check("a cup of diced onion is one onion", abs(got - 1.0) < 0.05, str(got))
    got = amount("2 cups shredded carrots")
    check("two cups of shredded carrot is two and a half carrots",
          2.0 < got < 3.0, str(got))
    got = amount("1/2 cup finely sliced green onions (1-2 stems)")
    check("half a cup of sliced green onion is a stem or two",
          1.0 <= got <= 3.0, str(got))
    got = amount("1 slice good melting cheese")
    check("a slice of cheese is about twenty grams",
          15 <= got <= 30, str(got))

    # The mirepoix is priced as the blend it is, and says what to buy -- one
    # thing the shelves do not stock is a mirepoix.
    note = service.buy_note("3 cups vegetables grated or finely chopped")
    check("the mirepoix says what to actually put in the trolley",
          "celery" in note.get("note", "").lower()
          or "celery" in note.get("product", "").lower(), str(note))


def _sides(check):
    """Side dishes: out of the week, and still on the shelf."""
    print("\nside dishes against the real library")
    from mealplan import service, store
    from mealplan.planner import is_side

    config = store.load_config()
    library, _held, sides = service.eligible_and_held(config)
    names = {d["name"] for d in sides}

    check("the library has side dishes in it", bool(sides), str(sorted(names)))
    check("no side dish is also in the pool",
          not (names & {s.name for s in library}))
    check("every side is named and can be looked up",
          all(d["name"] and d["uid"] for d in sides))
    check("the shelf is in a stable order",
          [d["name"] for d in sides]
          == sorted(names, key=str.lower),
          "the page re-renders on every read; the list must not shuffle")

    # The one the tags could not catch. A tray of potatoes, oil and salt was
    # served as a Monday dinner because Vege is both "vegetarian main" and
    # "vegetables", and that rescued it from every rule there was.
    spuds = "Truly Crunchy Roast Potatoes"
    check(f"{spuds} is a side", spuds in names)
    check("...and says it was set by hand",
          any(d["name"] == spuds and d["by_hand"] for d in sides),
          "the page is the only place anyone is reminded the override exists")
    check("...and a tagged side does not claim to be",
          not any(d["by_hand"] for d in sides if d["name"] != spuds))

    # Kept out is not the same as thrown away, which is the whole point of
    # handing them back rather than dropping them on the floor.
    scored = {s.name: s for s in service.score_all(config)}
    check("the sides are real recipes still in the library",
          all(d["name"] in scored for d in sides))
    check("every one of them answers to is_side",
          all(is_side(scored[d["name"]], config) for d in sides))

