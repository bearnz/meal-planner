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
