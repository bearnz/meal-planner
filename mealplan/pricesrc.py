"""Getting real prices into prices.json, and knowing which ones to trust.

**Where prices come from.** Three sources, in descending order of how much
they can be trusted:

- `--receipt` reads a receipt or order history you already have. This is what
  you were actually charged, at your store, in your quantities.
- `--price` sets one item by hand, in a second, at the checkout.
- `--grocer-refresh` pulls current shelf prices for one store from grocer.nz.
  See `grocer.py`; it is one 60 KB request a week and every item it touches was
  matched to a product by a person rather than by a fuzzy string match.

**On Foodstuffs directly.** PAK'nSAVE and New World are one platform, and their
robots.txt disallows `/search` and `/shop/search` -- product lookup by name,
which is exactly what a price fetcher needs. Product pages themselves are not
disallowed, but they answer a non-browser client with 403, and their prices are
per-store behind the `?store=` parameter that robots.txt also disallows. So
there is no honest route in that does not involve pretending to be a browser,
and this project does not. `robots_allows()` is the gate every fetcher passes
through, and it fails closed.

Both stamp `checked` with the date, and `stale_items()` finds what has drifted,
so the page can show which numbers were verified and which are still guesses.
"""

from __future__ import annotations

import re
import urllib.parse
import urllib.robotparser

import requests
from dataclasses import dataclass
from datetime import date, datetime

_ROBOTS: dict[str, urllib.robotparser.RobotFileParser] = {}

USER_AGENT = "meal-planner/1.0 (personal use)"


def robots_allows(url: str, agent: str = USER_AGENT) -> bool:
    """Whether robots.txt permits fetching this URL. Fails closed.

    Fetched with `requests` and our real User-Agent rather than through
    `RobotFileParser.read()`, which was the bug this function shipped with.
    urllib sends `Python-urllib/3.x`, a CDN in front of a site answers that with
    403, and urllib's parser reads a 403 as "everything is disallowed" -- so
    this returned False for every URL on any site behind Cloudflare, including
    ones whose robots.txt says `Allow: /`. A gate that refuses everything looks
    exactly like a gate that works until you check.

    The status codes are the conventional reading, and the two unusual ones are
    deliberate. 404 means the site published no rules, which is permission by
    convention rather than a refusal. 401 and 403 on robots.txt itself mean the
    operator is refusing to say, and that is treated as a no. Anything
    unreachable is also a no: a site that is timing out or rate-limiting is the
    one case where hammering it is worst, and "I could not read the rules" has
    never been a reason to assume there are none.
    """
    try:
        parts = urllib.parse.urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        rp = _ROBOTS.get(origin)
        if rp is None:
            rp = urllib.robotparser.RobotFileParser()
            r = requests.get(origin + "/robots.txt", timeout=15,
                             headers={"User-Agent": agent})
            if r.status_code in (401, 403):
                rp.disallow_all = True
            elif r.status_code >= 400:
                rp.allow_all = True
            else:
                rp.parse(r.text.splitlines())
            rp.modified()
            _ROBOTS[origin] = rp
        return bool(rp.can_fetch(agent, url))
    except Exception:
        return False


# --- receipts -----------------------------------------------------------------

# A receipt line: a description, then a price at the end. Supermarket receipts
# and order histories both put the money last; everything before it is the item,
# possibly with a size in it.
_RECEIPT_RE = re.compile(
    r"^\s*(?P<name>.+?)\s+"
    r"(?:\$?(?P<unit_price>\d+\.\d{2})\s*/\s*(?P<unit>kg|g|ea|each|l|ml)\s+)?"
    r"\$?(?P<price>\d+\.\d{2})\s*$",
    re.I,
)

# A size written inside the description: "Beef Mince 500g", "Milk 2L".
_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(kg|g|ml|l)\b", re.I)

_SIZE_FACTOR = {"g": 1.0, "kg": 1000.0, "ml": 1.0, "l": 1000.0}


@dataclass
class ReceiptLine:
    raw: str
    name: str
    price: float
    size: float | None = None      # pack size in g or ml, if stated
    unit_price: float | None = None
    unit: str = ""
    matched: str = ""              # catalogue key
    per: float = 1.0               # what `price` is per, in catalogue units
    resolved_price: float = 0.0
    note: str = ""


def parse_receipt(text: str, cat) -> list[ReceiptLine]:
    """Read a pasted receipt into priced lines, matched to the catalogue.

    Nothing is written by this. It returns what it *thinks* each line is, for a
    human to look at first -- a receipt parser that silently rewrote the price
    list from a misread line would corrupt the one file the costs depend on.
    """
    out: list[ReceiptLine] = []
    for raw in (text or "").splitlines():
        if not raw.strip():
            continue
        m = _RECEIPT_RE.match(raw)
        if not m:
            continue
        name = m.group("name").strip()
        # Skip the totals and payment lines, which parse perfectly and are not
        # groceries.
        if re.search(r"\b(total|subtotal|eftpos|change|cash|balance|savings|"
                     r"gst|tender|card|loyalty|points)\b", name, re.I):
            continue

        line = ReceiptLine(raw=raw.strip(), name=name,
                           price=float(m.group("price")))
        if m.group("unit_price"):
            line.unit_price = float(m.group("unit_price"))
            line.unit = (m.group("unit") or "").lower()

        sm = _SIZE_RE.search(name)
        if sm:
            line.size = float(sm.group(1)) * _SIZE_FACTOR[sm.group(2).lower()]

        key = cat.match(name)
        if not key:
            line.note = "no catalogue match"
            out.append(line)
            continue
        line.matched = key
        spec = cat.items[key]
        want = spec.get("unit", "g")

        # Work out what the receipt price is *per*, in the units the catalogue
        # prices this item in. Anything ambiguous is left for the human.
        per_unit = spec.get("g_per_unit")
        if line.unit_price and line.unit in ("kg", "l") and want in ("g", "ml"):
            line.per, line.resolved_price = 1000.0, line.unit_price
        elif line.unit_price and line.unit in ("ea", "each") and want == "each":
            line.per, line.resolved_price = 1.0, line.unit_price
        elif line.unit_price and line.unit in ("kg", "l") and want == "each":
            # A per-kg receipt price against an item the catalogue counts one at
            # a time. Converting is the only correct answer and skipping is the
            # only safe alternative -- taking the line total as a unit price is
            # what a naive reading does, and it is out by however many are in a
            # kilo: "Chicken Thighs $8.99/kg" became $8.99 *per thigh*, nine
            # times the real figure, silently, in a file the costs depend on.
            if per_unit:
                line.per = 1.0
                line.resolved_price = line.unit_price * float(per_unit) / 1000.0
            else:
                line.note = "priced per kg, catalogue counts each - no unit weight"
        elif line.size and want in ("g", "ml"):
            line.per, line.resolved_price = line.size, line.price
        elif line.size and want == "each" and per_unit:
            # "Beef Mince 500g" against an each-priced item: the pack is the
            # thing bought, so the price is per pack-worth of units.
            line.per = float(line.size) / float(per_unit)
            line.resolved_price = line.price
        elif want == "each" and not line.size and not line.unit_price:
            line.per, line.resolved_price = 1.0, line.price
        else:
            line.note = "cannot tell what that price is per"
        out.append(line)
    return out


# --- writing back -------------------------------------------------------------

def set_price(data: dict, item: str, storename: str, price: float,
              per: float = 1.0, checked: str = "") -> bool:
    """Set one price and stamp it as checked. Returns whether it changed."""
    spec = (data.get("items") or {}).get(item)
    if not spec:
        return False
    prices = spec.setdefault("prices", {})
    entry = prices.setdefault(storename, {})
    before = (entry.get("price"), entry.get("per"))
    entry["price"] = round(float(price), 2)
    entry["per"] = float(per)
    entry["checked"] = checked or date.today().isoformat()
    # A verified price supersedes whatever special was guessed for it. Leaving a
    # stale invented special beside a real price is the worst of both.
    entry.pop("special", None)
    return before != (entry["price"], entry["per"])


def apply_receipt(data: dict, lines: list[ReceiptLine], storename: str,
                  checked: str = "") -> list[str]:
    """Write the lines that resolved cleanly. Returns what was updated."""
    done: list[str] = []
    for line in lines:
        if not line.matched or not line.resolved_price or line.note:
            continue
        if set_price(data, line.matched, storename, line.resolved_price,
                     line.per, checked):
            done.append(f"{line.matched} @ {storename}: "
                        f"${line.resolved_price:.2f} per {line.per:g}")
    return done


def stale_items(data: dict, days: int = 120) -> list[tuple[str, str, str]]:
    """(item, store, when) for prices never checked or checked long ago.

    An unchecked price is not a small problem here: the file ships full of
    estimates, so "never checked" is the default state and the page needs to be
    able to say how much of the week's cost rests on one.
    """
    today = date.today()
    out: list[tuple[str, str, str]] = []
    for key, spec in (data.get("items") or {}).items():
        for storename, entry in (spec.get("prices") or {}).items():
            checked = entry.get("checked")
            if not checked:
                out.append((key, storename, ""))
                continue
            try:
                when = datetime.fromisoformat(checked).date()
            except ValueError:
                out.append((key, storename, checked))
                continue
            if (today - when).days > days:
                out.append((key, storename, checked))
    return out


def freshness(data: dict, store: str = "") -> dict:
    """How much of this price list is real, at the shop you actually use.

    Counted at one store on purpose. The file holds a price per item *per
    chain* -- 209 entries across three of them -- and the whole point of the
    project is that you shop at one. Counting all three made the progress bar
    a number that could not be moved: --grocer-refresh fetches one store, so
    123 of those entries belonged to shops nobody was going to check, and the
    bar had a hard ceiling of 41% that had nothing to do with effort. It read
    as "you have barely started" after pricing every ingredient you own.

    The other chains' prices are not dead weight -- `find_savings` needs them
    to spot a special worth a detour -- they are simply not something anyone
    will ever verify, so they do not belong in a progress figure.

    `every` keeps the old whole-file count for anything that wants it.
    """
    items = (data.get("items") or {}).values()
    store = store or str(data.get("primary_store") or "")
    every = sum(len(spec.get("prices") or {}) for spec in items)

    if store:
        entries = [(spec.get("prices") or {}).get(store) for spec in items]
        here = [e for e in entries if e]
        checked = sum(1 for e in here if e.get("checked"))
        total = len(here)
    else:
        total = every
        checked = every - len(stale_items(data, days=10 ** 6))

    return {
        "store": store,
        "total": total,
        "checked": checked,
        "share": (checked / total) if total else 0.0,
        "stale": len(stale_items(data)),
        "every": every,
        # Items with no price at this store at all: not "unchecked" but
        # "absent", and the two want different actions from the reader.
        "absent": sum(1 for spec in items
                      if store and store not in (spec.get("prices") or {})),
    }
