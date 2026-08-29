"""Entry point.

    python run.py            start the web app on http://127.0.0.1:5173
    python run.py --sync     pull recipes from Paprika into the local cache
    python run.py --plan     print this week's plan to the terminal
    python run.py --scores   list every recipe with its heartiness and effort
"""

from __future__ import annotations

import argparse
import sys

from mealplan import service, store


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
    args = ap.parse_args()

    store.load_env()

    try:
        if args.sync:
            return cmd_sync()
        if args.plan:
            return cmd_plan()
        if args.scores:
            return cmd_scores()
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
