"""Entry point.

    python run.py            start the web app on http://127.0.0.1:5173
    python run.py --sync     pull recipes from Paprika into the local cache
    python run.py --plan     print this week's plan to the terminal
    python run.py --scores   list every recipe with its heartiness and effort

    python run.py --price "beef mince" 15.49 --per 1000 --store "PAK'nSAVE"
    python run.py --receipt receipt.txt --store "PAK'nSAVE"   (preview)
    python run.py --receipt receipt.txt --store "PAK'nSAVE" --apply
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
    print(f"\rCached {len(payload['recipes'])} recipes "
          f"({len(payload['categories'])} categories).      ")
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
    return data, pricing.Catalogue(data)


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


def cmd_stale(args) -> int:
    """Which prices are real and which are still the shipped estimates."""
    data, _ = _catalogue()
    fresh = pricesrc.freshness(data)
    rows = pricesrc.stale_items(data)
    never = [r for r in rows if not r[2]]
    old = [r for r in rows if r[2]]

    print(f"\n  {fresh['checked']}/{fresh['total']} prices have ever been "
          f"checked ({fresh['share']:.0%}).\n")
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
