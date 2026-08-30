"""Entry point.

    python run.py            start the web app on http://127.0.0.1:5173
    python run.py --sync     pull recipes from Paprika into the local cache
    python run.py --plan     print this week's plan to the terminal
    python run.py --scores   list every recipe with its heartiness and effort

    python run.py --price "beef mince" 15.49 --per 1000 --store "PAK'nSAVE"
    python run.py --receipt receipt.txt --store "PAK'nSAVE"   (preview)
    python run.py --receipt receipt.txt --store "PAK'nSAVE" --apply
    python run.py --missing  which unpriced ingredients block the most recipes

    python run.py --grocer-stores "royal oak"     find your store id
    python run.py --grocer-find "beef chuck"      candidates at your store
    python run.py --grocer-pin "beef chuck" 1672  remember which one it is
    python run.py --grocer-refresh                update every pinned price
    python run.py --stale    prices that have never been checked, or are old
"""

from __future__ import annotations

import argparse
import sys

import json

from mealplan import pricesrc, pricing, service, store


def cmd_sync() -> int:
    def progress(done: int, total: int) -> None:
        print(f"\r  {done}/{total} recipes", end="", flush=True)

    print("Fetching from Paprika...")
    payload = service.sync_recipes(progress=progress)
    s = payload.get("sync", {})
    detail = (f"{s.get('fetched', 0)} fetched, {s.get('reused', 0)} unchanged, "
              f"{s.get('trashed', 0)} in the trash")
    print(f"\rCached {len(payload['recipes'])} recipes "
          f"({len(payload['categories'])} categories) - {detail}.      ")
    return 0


def cmd_plan() -> int:
    config = store.load_config()
    plan = service.generate(config)
    print(f"\n  {plan['location']} - {plan['library_size']} recipes in play\n")
    for d in plan["days"]:
        w = d["weather"]
        print(f"  {d['day_name']:<10} {w['icon']} {w['temp_max']:>4.0f}C  "
              f"{d['name']}")
        print(f"  {'':<10}   {' - '.join(d['why'])}")
    print()
    return 0


def cmd_scores() -> int:
    config = store.load_config()
    library = service.load_library(config)
    library.sort(key=lambda s: s.heartiness)
    print(f"\n  {'hearty':>6}  {'effort':>6}  recipe\n")
    for s in library:
        print(f"  {s.heartiness:>6.2f}  {s.effort:>6.2f}  {s.name}")
    print(f"\n  {len(library)} dinners.\n")
    return 0


def _catalogue():
    data = store.load_prices()
    shop = str(store.load_config().get("primary_store") or "")
    return data, pricing.Catalogue(data, shop)


def cmd_price(args) -> int:
    """Set one price by hand. The fast path: do it at the checkout."""
    data, cat = _catalogue()
    item = args.price[0]
    key = item if item in cat.items else cat.match(item)
    if not key:
        print(f"\n  {item!r} is not in prices.json. Add it there first, or "
              f"try one of its aliases.\n", file=sys.stderr)
        return 1
    shop = args.store or cat.primary
    changed = pricesrc.set_price(data, key, shop, float(args.price[1]),
                                 float(args.per))
    store._write_json(store.PRICES_PATH, data)
    verb = "updated" if changed else "unchanged"
    print(f"\n  {key} @ {shop}: ${float(args.price[1]):.2f} "
          f"per {float(args.per):g} ({verb}), checked today.\n")
    return 0


def cmd_receipt(args) -> int:
    """Read prices off a receipt. Previews by default; --apply writes."""
    data, cat = _catalogue()
    with open(args.receipt, encoding="utf-8", errors="replace") as fh:
        lines = pricesrc.parse_receipt(fh.read(), cat)
    if not lines:
        print("\n  Nothing on that receipt looked like a priced line.\n")
        return 1

    shop = args.store or cat.primary
    print(f"\n  Read {len(lines)} priced lines from {args.receipt}\n")
    good = 0
    for line in lines:
        if line.matched and line.resolved_price and not line.note:
            good += 1
            print(f"    ok    {line.name[:32]:<32} -> {line.matched:<18} "
                  f"${line.resolved_price:.2f} per {line.per:g}")
        else:
            print(f"    skip  {line.name[:32]:<32} -- "
                  f"{line.note or 'unresolved'}")

    if not args.apply:
        print(f"\n  {good} would be written to prices.json. "
              f"Re-run with --apply to write them.\n")
        return 0

    done = pricesrc.apply_receipt(data, lines, shop)
    store._write_json(store.PRICES_PATH, data)
    print(f"\n  Updated {len(done)} prices at {shop}.\n")
    return 0


def cmd_missing(args) -> int:
    """Which missing prices would buy the most, over the whole library.

    Ranked by how many *recipes* each one blocks rather than how many lines it
    appears on, because the thing being bought here is priceable dinners. One
    entry for beef chuck unlocks a whole night; ten entries for spices that
    appear once each unlock nothing, since they were never what the estimate
    was missing.
    """
    from collections import Counter

    config = store.load_config()
    cat = service.catalogue()
    if not cat.items:
        print("\n  No prices.json to compare against.\n")
        return 1

    library = service.load_library(config)
    blocking: Counter[str] = Counter()
    major: Counter[str] = Counter()
    unusable = 0

    for s in library:
        made = service.quantity.servings_factor(s.recipe.servings, 1)[1]
        c = pricing.cost_recipe(s.recipe.ingredients, made or 4, cat,
                                str(config.get("primary_store") or ""))
        if not c.usable:
            unusable += 1
        seen = set()
        for line in c.unpriced:
            item = (line.item or line.raw).strip().lower()[:40]
            if item and item not in seen:
                seen.add(item)
                blocking[item] += 1
        for item in c.major_unpriced:
            major[item.strip().lower()[:40]] += 1

    print(f"\n  {unusable} of {len(library)} dinners cannot be costed at all.\n")
    if major:
        print("  Missing a MAIN ingredient (these block a whole recipe each):\n")
        for item, n in major.most_common(20):
            print(f"    {n:3} recipe(s)   {item}")
    print("\n  Most common unpriced ingredients overall:\n")
    for item, n in blocking.most_common(30):
        print(f"    {n:3} recipe(s)   {item}")
    print('\n  Add one with:  python run.py --price "beef chuck" 18.99 '
          '--per 1000\n')
    return 0


def _grocer_store(config) -> tuple[int, str]:
    g = config.get("grocer") or {}
    store_id = int(g.get("store_id") or 0)
    if not store_id:
        raise SystemExit(
            '\n  No grocer.nz store set. Put one in config.json under '
            '"grocer": {"store_id": ...}.\n')
    return store_id, str(config.get("primary_store") or "")


def _grocer_prices(grocer, store_id):
    """Live prices, or an empty dict and a printed reason. Never raises."""
    try:
        prices, _etag, _same = grocer.fetch_prices(store_id)
        return prices
    except grocer.GrocerError as exc:
        print(f"\n  (no live prices: {exc})")
        return {}


def cmd_grocer_stores(args) -> int:
    """List the stores prices can be fetched for, or re-download the list."""
    from mealplan import grocer

    if args.refresh:
        rows = grocer.fetch_stores()
        print(f"\n  Re-read {len(rows)} stores from grocer.nz.")
    query = args.grocer_stores if isinstance(args.grocer_stores, str) else ""
    rows = grocer.find_stores(query, limit=int(args.limit) if query else 400)
    if not rows:
        print(f"\n  No store matched {query!r}.\n")
        return 1

    config = store.load_config()
    chosen = int((config.get("grocer") or {}).get("store_id") or 0)
    print(f"\n  {len(rows)} store(s)\n")
    for s in rows:
        column = grocer.store_name_for(s.get("vendor", ""))
        mark = "*" if s["id"] == chosen else " "
        note = "" if column else "   (no price column in prices.json)"
        print(f"  {mark} {s['id']:>6}  {s['name']}{note}")
    print("\n  * is the store in use. Change it on the Preferences page, or "
          'set "grocer": {"store_id": ...} in config.json.\n')
    return 0


def cmd_grocer_find(args) -> int:
    """Show candidate products so a person can choose. Never picks for you.

    Choosing by hand is the whole reason this is a separate command from the
    refresh. Searching their catalogue for "beef chuck" returns the right steak
    first and then four kinds of cat food, and once a wrong pick has become a
    number on a meal plan there is nothing about it that looks wrong. So this
    prints and stops.
    """
    from mealplan import grocer

    config = store.load_config()
    store_id, _ = _grocer_store(config)
    term = args.grocer_find
    hits = grocer.search(term, store_id, limit=int(args.limit),
                         category=args.category)
    grocer.remember(hits)
    if not hits:
        print(f"\n  Nothing at that store matched {term!r}.\n")
        return 1

    data, _ = _catalogue()
    known = grocer.pinned(data)
    prices = _grocer_prices(grocer, store_id)
    where = (config.get("grocer") or {}).get("store_name") or store_id

    print(f"\n  {len(hits)} candidate(s) for {term!r} at {where}\n")
    for h in hits:
        p = prices.get(h.id)
        if p:
            amount, unit = grocer.parse_size(h.size)
            per = (f"${p.best_cent / 100:.2f} per {amount:g}{unit}"
                   if amount else f"${p.best_cent / 100:.2f} per ?")
            if p.sale_cent and p.sale_cent < p.original_cent:
                per += f"  SPECIAL was ${p.original_cent / 100:.2f}"
        else:
            per = "no price at this store"
        print(f"    {h.id:>8}  {h.label[:42]:<42} {(h.size or 'loose/kg'):<9} {per}")
        print(f"    {'':>8}  {h.category}")

    ids = {h.id for h in hits}
    already = [i for i, v in known.items() if v.get("id") in ids]
    if already:
        print(f"\n  Already pinned: {', '.join(already)}")
    print('\n  Pin one with:  python run.py --grocer-pin "<item>" <id>\n')
    print(f"  {grocer.ATTRIBUTION}\n")
    return 0


def cmd_grocer_pin(args) -> int:
    """Record that an item in prices.json is a particular grocer.nz product."""
    from mealplan import grocer

    config = store.load_config()
    store_id, _ = _grocer_store(config)
    item, pid = args.grocer_pin[0], int(args.grocer_pin[1])
    data, cat = _catalogue()
    # An exact entry wins, then an alias; anything else is a new ingredient,
    # which is the usual reason for pinning one at all -- the names worth
    # pinning are exactly the ones --missing reports as having no entry.
    key = item if item in (data.get("items") or {}) else (cat.match(item) or item)

    # The price file is the thing that actually has to contain it: an id that
    # is not in there would pin cleanly and then never refresh, which is a
    # silent no-op rather than an error.
    prices = _grocer_prices(grocer, store_id)
    if prices and pid not in prices:
        print(f"\n  Product {pid} has no price at that store, so pinning it "
              f"would never refresh. Nothing written.\n", file=sys.stderr)
        return 1

    # What --grocer-find last showed. Their search key cannot fetch a
    # document by id, so this is the only way to know the pack size, and the
    # pack size is what turns a shelf price into a price per gram.
    product = grocer.recall(pid)
    if product is None:
        print(f"\n  Product {pid} was not among the candidates that --grocer-find "
              f"last showed, so its pack size is unknown -- and guessing that "
              f"is how a 500g pack gets priced as a kilo. Run:\n\n"
              f'    python run.py --grocer-find "{item}"' + "\n\n",
              file=sys.stderr)
        return 1

    if not 0.0 < args.keep <= 1.0:
        print(f"\n  --yield must be above 0 and at most 1 (got {args.keep})." + "\n", file=sys.stderr)
        return 1
    _ok, created = grocer.pin(data, key, product, yield_=args.keep,
                              note=args.note)
    store._write_json(store.PRICES_PATH, data)
    verb = "is a new item, pinned to" if created else "is now"
    print(f"\n  {key!r} {verb} grocer.nz product {pid} "
          f"({product.label}, {product.size or 'loose/kg'}).")
    if args.keep < 1.0:
        print(f"  {args.keep:.0%} of what you buy ends up in the pot"
              f"{', ' + args.note if args.note else ''} - the shelf "
              f"price is divided by that.")
    print("  Run --grocer-refresh to price it.\n")
    return 0


def cmd_grocer_refresh(args) -> int:
    """The weekly job: one 60 KB download, every pinned price updated."""
    from mealplan import grocer

    config = store.load_config()
    store_id, storename = _grocer_store(config)
    data, _ = _catalogue()
    result = grocer.refresh(data, store_id, storename, force=bool(args.force))
    store._write_json(store.PRICES_PATH, data)

    print(f"\n  {result['status']}")
    for line in result["updated"]:
        print(f"    ok    {line}")
    for line in result["missing"]:
        print(f"    gone  {line}")
    for line in result["skipped"]:
        print(f"    skip  {line}")
    print(f"\n  {len(result['updated'])} price(s) updated at {storename}.")
    print(f"  {grocer.ATTRIBUTION}\n")
    return 0


def cmd_stale(args) -> int:
    """Which prices are real and which are still the shipped estimates."""
    data, _ = _catalogue()
    # The config's store, not the price file's own default. Those disagree:
    # prices.json still ships with New World as its primary while the config
    # says PAK'nSAVE, and asking the file gave a freshness figure for a shop
    # nobody here uses -- 0 checked, while every real price sat next door.
    shop = str(store.load_config().get("primary_store") or "")
    fresh = pricesrc.freshness(data, shop)
    rows = pricesrc.stale_items(data)
    never = [r for r in rows if not r[2]]
    old = [r for r in rows if r[2]]
    print(f"\n  {fresh['checked']}/{fresh['total']} prices at "
          f"{fresh['store'] or 'your store'} have ever been checked "
          f"({fresh['share']:.0%}). {fresh['every']} across all chains." + "\n")
    for item, shop, when in old:
        print(f"    {when}  {item} @ {shop}")
    if old:
        print()
    print(f"  {len(never)} price(s) have never been checked, so they are the "
          f"shipped estimates.")
    print('  Fix one with:  python run.py --price "beef mince" 15.49 '
          '--per 1000\n')
    return 0


def main() -> int:
    # The plan prints weather emoji, and a Windows console defaults to cp1252,
    # which cannot encode them -- so the whole command dies on a decoration.
    # errors="replace" rather than a strict switch: a console that genuinely
    # cannot draw the glyph should show a box, not refuse to print the menu.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sync", action="store_true", help="pull recipes from Paprika")
    ap.add_argument("--plan", action="store_true", help="print this week's plan")
    ap.add_argument("--scores", action="store_true", help="list scored recipes")
    ap.add_argument("--port", type=int, default=5173)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--price", nargs=2, metavar=("ITEM", "PRICE"),
                    help="set one price, e.g. --price \"beef mince\" 15.49")
    ap.add_argument("--per", default=1.0,
                    help="what that price is per (1000 for a per-kg price)")
    ap.add_argument("--receipt", metavar="FILE",
                    help="read prices from a receipt or order history")
    ap.add_argument("--apply", action="store_true",
                    help="with --receipt, actually write the prices")
    ap.add_argument("--store", default="", help="which supermarket")
    ap.add_argument("--missing", action="store_true",
                    help="which unpriced ingredients block the most recipes")
    ap.add_argument("--grocer-stores", nargs="?", const=True, metavar="QUERY",
                    help="list stores prices can be fetched for")
    ap.add_argument("--refresh", action="store_true",
                    help="with --grocer-stores, re-download the list")
    ap.add_argument("--grocer-find", metavar="ITEM",
                    help="search grocer.nz for a product to price ITEM with")
    ap.add_argument("--grocer-pin", nargs=2, metavar=("ITEM", "PRODUCT_ID"),
                    help="remember which grocer.nz product ITEM is")
    ap.add_argument("--yield", dest="keep", type=float, default=1.0,
                    help="with --grocer-pin: the share of what you buy that "
                         "ends up in the pot, e.g. 0.68 for bone-in chicken")
    ap.add_argument("--note", default="",
                    help="with --grocer-pin: why this product, e.g. "
                         "\"bought bone-in and boned out\"")
    ap.add_argument("--grocer-refresh", action="store_true",
                    help="update every pinned price from grocer.nz")
    ap.add_argument("--force", action="store_true",
                    help="with --grocer-refresh, ignore the refresh interval")
    ap.add_argument("--limit", default=8, help="how many candidates to show")
    ap.add_argument("--category", default="",
                    help='narrow --grocer-find, e.g. "Meat & Seafood"')
    ap.add_argument("--stale", action="store_true",
                    help="list prices that are unchecked or old")
    args = ap.parse_args()

    store.load_env()

    try:
        if args.sync:
            return cmd_sync()
        if args.plan:
            return cmd_plan()
        if args.scores:
            return cmd_scores()
        if args.price:
            return cmd_price(args)
        if args.receipt:
            return cmd_receipt(args)
        if args.missing:
            return cmd_missing(args)
        if args.grocer_stores:
            return cmd_grocer_stores(args)
        if args.grocer_find:
            return cmd_grocer_find(args)
        if args.grocer_pin:
            return cmd_grocer_pin(args)
        if args.grocer_refresh:
            return cmd_grocer_refresh(args)
        if args.stale:
            return cmd_stale(args)
    except Exception as exc:  # noqa: BLE001 - a CLI should say what broke
        print(f"\nError: {exc}\n", file=sys.stderr)
        return 1

    from mealplan.web import run
    url = f"http://{args.host}:{args.port}"
    print(f"\n  Meal planner running at {url}\n  Ctrl-C to stop.\n")
    run(host=args.host, port=args.port, debug=args.debug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
