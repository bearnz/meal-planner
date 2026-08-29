"""Getting real prices into prices.json, and knowing which ones to trust.

**Why there is no scraper here.** The obvious answer to "where do prices come
from" is to fetch them from the supermarket. Foodstuffs (PAK'nSAVE and New
World, one platform) publish a robots.txt that disallows exactly the two paths
this would need:

    Disallow: /search          Disallow: /shop/search
    Disallow: /shop/offer      Disallow: /product-promotions

Product lookup by name, and the current promotions. That is a machine-readable
instruction from the operator about automated access, naming the endpoints, so
this project does not request them. `robots_allows()` below is the gate: any
future fetcher has to pass through it, and it fails closed.

That leaves two ways in, and both are better data anyway because they are what
you were actually charged rather than a shelf price for a different store:

- `--receipt` reads a receipt or order history you already have.
- `--price` sets one item by hand, in a second, at the checkout.

Both stamp `checked` with the date, and `stale_items()` finds what has drifted,
so the page can show which numbers were verified and which are still guesses.
"""

from __future__ import annotations

import re
import urllib.parse
import urllib.robotparser
from dataclasses import dataclass
from datetime import date, datetime

_ROBOTS: dict[str, urllib.robotparser.RobotFileParser] = {}

USER_AGENT = "meal-planner/1.0 (personal use)"


def robots_allows(url: str, agent: str = USER_AGENT) -> bool:
    """Whether robots.txt permits fetching this URL. Fails closed.

    Unreachable robots.txt returns False rather than True. The permissive
    default is the conventional one and it is wrong here: a site that is timing
    out or rate-limiting is the one case where hammering it is worst, and
    "I could not read the rules" is not a reason to assume there are none.
    """
    try:
        parts = urllib.parse.urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        rp = _ROBOTS.get(origin)
        if rp is None:
            rp = urllib.robotparser.RobotFileParser()
            rp.set_url(origin + "/robots.txt")
            rp.read()
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


def freshness(data: dict) -> dict:
    """A summary for the page: how much of this price list is real."""
    total = sum(len(spec.get("prices") or {})
                for spec in (data.get("items") or {}).values())
    checked = total - len(stale_items(data, days=10 ** 6))
    return {
        "total": total,
        "checked": checked,
        "share": (checked / total) if total else 0.0,
        "stale": len(stale_items(data)),
    }
