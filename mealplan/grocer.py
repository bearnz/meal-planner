"""Prices from grocer.nz, for a single store, for personal use.

**What this fetches, and why it is this small.** grocer.nz publishes one Parquet
file per store holding the current price of everything that store stocks --
about 10,000 products in 60 KB. So a full refresh of every price this project
knows about is *one* HTTP request of 60 KB, once a week, and an unchanged file
is a 304 with no body at all. That is a smaller footprint than one person
opening their front page, and it is the reason this was worth doing properly
rather than scraping pages one product at a time.

**Why it is split into finding and refreshing.** Matching an ingredient name to
a supermarket product is where silent wrongness lives: searching their catalogue
for "beef chuck" returns NZ Beef Chuck Steak first and then four kinds of cat
food, and nothing about the second one announces itself as wrong once it is a
number on a meal plan. So the two halves are deliberately separated.

  find    an occasional, interactive lookup. Shows candidates and their live
          price; a person picks. A handful of requests, only when adding an
          ingredient the file has never priced.
  pin     records that decision as a product id in prices.json.
  refresh the weekly job. Reads prices *by pinned id*, so there is no matching
          in it at all and nothing can drift onto the wrong product later.

That is the same shape as `--receipt`, which previews before `--apply` for the
same reason: this project would rather withhold a number than show a confident
wrong one.

**On being a good guest.** grocer.nz is a small New Zealand operation and this
price database is their actual product, not a by-product like it is for a
supermarket. Their robots.txt allows this (`Allow: /`, one auth path disallowed)
and their terms prohibit only unauthorised access and disrupting the service,
neither of which this does. That makes it permitted, not free, so:

  - `robots_allows()` gates every request and fails closed.
  - The User-Agent says what this is, that it is personal and non-commercial,
    and where the source lives.
  - `If-None-Match` on the price file, so an unchanged week costs them a 304.
  - `MIN_REFRESH_HOURS` refuses to re-fetch more often than daily unless forced.
    Their own cache-control asks for 8 hours; this is more conservative.
  - `ATTRIBUTION` is written into prices.json and shown in the app.

The search key below is their public search-only key, shipped in the JavaScript
every visitor downloads. It is not a credential that was obtained -- it is
scoped to querying the product index and is rejected for anything else. It can
be rotated at any time, and if it is, search stops working and `find` says so;
prices keep refreshing, because the Parquet file needs no key.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests

from .pricesrc import robots_allows

SOURCE_NAME = "grocer.nz"
SOURCE_URL = "https://grocer.nz"
ATTRIBUTION = "Price data from grocer.nz, used for personal, non-commercial use."

ASSETS = "https://assets-prod.grocer.nz/public"
PRICES_URL = ASSETS + "/prices_per_store_v3/public_prices_{store}.parquet"
SEARCH_URL = "https://meilisearch.grocer.nz/indexes/products/search"
# Public, search-only. See the module docstring.
SEARCH_KEY = "7f58239330307ec585c86863f985ab83cbb9ce951a9601c66e158548fb632fd1"

USER_AGENT = ("meal-planner/1.0 (personal, non-commercial; "
              "+https://github.com/bearnz/meal-planner)")
TIMEOUT = 60

# Their cache-control asks for 8 hours. Weekly is the intended cadence; this is
# only the floor that stops a scripted loop, so it sits between the two.
MIN_REFRESH_HOURS = 20


class GrocerError(RuntimeError):
    pass


@dataclass
class Product:
    id: int
    name: str
    brand: str
    size: str          # "500g", "2l", "12 x 70g", or "" for loose/by-weight
    category: str

    @property
    def label(self) -> str:
        bits = [b for b in (self.brand, self.name) if b]
        return " ".join(bits)


@dataclass
class Price:
    """One product's price at one store, as the source states it."""
    product_id: int
    original_cent: int
    sale_cent: int          # 0 when not on special
    club_cent: int          # 0 when there is no club price
    updated_at: str

    @property
    def best_cent(self) -> int:
        """What you would actually pay, ignoring club and multibuy.

        Club prices are deliberately not taken: they need a card this project
        knows nothing about, and quoting one as the price makes a plan cheaper
        than the shop will be. A special is available to anyone, so it counts.
        """
        if self.sale_cent and self.sale_cent < self.original_cent:
            return self.sale_cent
        return self.original_cent


def _headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    h = {"User-Agent": USER_AGENT}
    h.update(extra or {})
    return h


def _check_allowed(url: str) -> None:
    if not robots_allows(url, USER_AGENT):
        raise GrocerError(
            f"robots.txt does not allow fetching {url} (or could not be read, "
            f"which is treated the same way). Nothing was requested.")


# --- finding a product --------------------------------------------------------

def search(term: str, store_id: int, limit: int = 8,
           category: str = "") -> list[Product]:
    """Search the product catalogue, scoped to one store.

    Scoped because a product the store does not stock has no price in that
    store's file, so an unscoped hit is a candidate that can never be pinned.
    """
    _check_allowed(SEARCH_URL)
    parts = [f"stores = {int(store_id)}"]
    if category:
        parts.append(f'categories.level_1 = "{category}"')
    body: dict[str, Any] = {"q": term, "limit": int(limit),
                            "filter": " AND ".join(parts)}
    try:
        r = requests.post(SEARCH_URL, json=body, timeout=TIMEOUT,
                          headers=_headers({"Authorization": f"Bearer {SEARCH_KEY}"}))
    except requests.RequestException as exc:
        raise GrocerError(f"Could not reach {SOURCE_NAME} search: {exc}") from exc
    if r.status_code == 403:
        raise GrocerError(
            "grocer.nz rejected the search key. It is their public key and they "
            "have most likely rotated it; re-read it from their site, or pin "
            "product ids by hand. Refreshing prices still works -- it needs no key.")
    if r.status_code != 200:
        raise GrocerError(f"{SOURCE_NAME} search returned {r.status_code}.")

    out: list[Product] = []
    for h in (r.json() or {}).get("hits") or []:
        cats = h.get("categories") or {}
        out.append(Product(
            id=int(h["id"]), name=str(h.get("name") or ""),
            brand=str(h.get("brand") or ""), size=str(h.get("size") or ""),
            category=str(cats.get("level_2") or cats.get("level_1") or ""),
        ))
    return out


# --- fetching the store's prices ---------------------------------------------

def fetch_prices(store_id: int, etag: str = "") -> tuple[dict[int, Price], str, bool]:
    """Every current price at one store. Returns (by id, etag, unchanged).

    `unchanged` is True when the source answered 304, in which case the dict is
    empty and the caller should keep what it already has.
    """
    url = PRICES_URL.format(store=int(store_id))
    _check_allowed(url)
    extra = {"If-None-Match": etag} if etag else {}
    try:
        r = requests.get(url, timeout=TIMEOUT, headers=_headers(extra))
    except requests.RequestException as exc:
        raise GrocerError(f"Could not reach {SOURCE_NAME}: {exc}") from exc

    if r.status_code == 304:
        return {}, etag, True
    if r.status_code == 404:
        raise GrocerError(
            f"{SOURCE_NAME} has no price file for store {store_id}. Check the "
            f"store id with --grocer-stores.")
    if r.status_code != 200:
        raise GrocerError(f"{SOURCE_NAME} returned {r.status_code} for the price file.")

    return _read_parquet(r.content), r.headers.get("ETag", ""), False


def _read_parquet(blob: bytes) -> dict[int, Price]:
    """Decode the store's price table.

    pyarrow is imported here rather than at module scope so the web app, the
    planner and the whole test suite keep working without it. It is needed only
    by this one command, and it is a 40 MB dependency for a project whose entire
    runtime is Flask and requests.
    """
    try:
        import pyarrow.parquet as pq   # noqa: PLC0415 - deliberately local
    except ImportError as exc:
        raise GrocerError(
            "Reading grocer.nz prices needs pyarrow, which is not installed:\n"
            "    pip install pyarrow\n"
            "Nothing else in this project requires it.") from exc
    import io

    try:
        table = pq.read_table(io.BytesIO(blob))
    except Exception as exc:  # noqa: BLE001 - a bad download should say so
        raise GrocerError(f"Could not read the price file: {exc}") from exc

    out: dict[int, Price] = {}
    for row in table.to_pylist():
        when = row.get("updated_at")
        out[int(row["product_id"])] = Price(
            product_id=int(row["product_id"]),
            original_cent=int(row.get("original_price_cent") or 0),
            sale_cent=int(row.get("sale_price_cent") or 0),
            club_cent=int(row.get("club_price_cent") or 0),
            updated_at=when.isoformat() if isinstance(when, datetime) else "",
        )
    return out


# --- pack sizes ---------------------------------------------------------------

_SIZE_RE = re.compile(r"^\s*(?:(\d+(?:\.\d+)?)\s*x\s*)?(\d+(?:\.\d+)?)\s*"
                      r"(kg|g|l|ml|ea|pk)\s*$", re.I)

_TO_BASE = {"kg": (1000.0, "g"), "g": (1.0, "g"),
            "l": (1000.0, "ml"), "ml": (1.0, "ml"),
            "ea": (1.0, "ea"), "pk": (1.0, "ea")}


def parse_size(size: str) -> tuple[float, str]:
    """A pack size like '500g' or '12 x 70g' as (amount, unit).

    An empty size is not missing data: grocer.nz leaves it blank for anything
    sold loose, and those are quoted per kilogram -- which is how a $28.99 chuck
    steak is $28.99 and not the price of one unknowable piece of meat. So blank
    means (1000, "g"), and that is the single most important line in this file.
    """
    if not (size or "").strip():
        return 1000.0, "g"
    m = _SIZE_RE.match(size)
    if not m:
        return 0.0, ""
    packs = float(m.group(1) or 1.0)
    amount = float(m.group(2))
    scale, unit = _TO_BASE[m.group(3).lower()]
    return packs * amount * scale, unit


def unit_price(price_cent: int, size: str) -> tuple[float, float, str]:
    """(price, per, unit) in the shape prices.json stores.

    Returns per=0 when the size could not be parsed, which the caller must treat
    as "do not write this" rather than as a free item.
    """
    amount, unit = parse_size(size)
    if amount <= 0 or not unit:
        return 0.0, 0.0, ""
    return round(price_cent / 100.0, 2), amount, unit


# --- the weekly refresh -------------------------------------------------------

def pinned(data: dict) -> dict[str, dict]:
    """Every item in prices.json carrying a grocer.nz product id."""
    out = {}
    for name, spec in (data.get("items") or {}).items():
        pin = (spec or {}).get("grocer") or {}
        if pin.get("id"):
            out[name] = pin
    return out


def pin(data: dict, item: str, product: Product,
        create: bool = True) -> tuple[bool, bool]:
    """Record which grocer.nz product an item is. Returns (ok, created).

    Creates the entry when the file has never heard of the ingredient, which is
    the normal case rather than the exception: the ingredients worth pinning are
    exactly the ones `--missing` reports, and those are missing *because* they
    have no entry. Refusing to create one would mean hand-editing prices.json
    before every pin, which is the step this command exists to remove.

    The new entry gets the ingredient name as its own alias and takes its unit
    from the product's pack size, so a 2 litre bottle lands as ml and a loose
    cut of meat as g. No aliases beyond the name itself are invented -- guessing
    that "beef chuck" is also "chuck steak" is the matcher's job and it can be
    told so by hand.
    """
    items = data.setdefault("items", {})
    spec = items.get(item)
    created = False
    if spec is None:
        if not create:
            return False, False
        _amount, unit = parse_size(product.size)
        spec = {"aliases": [item], "unit": unit or "g", "prices": {}}
        items[item] = spec
        created = True
    spec["grocer"] = {"id": product.id, "name": product.label,
                      "size": product.size, "category": product.category}
    return True, created


def state(data: dict) -> dict:
    return data.setdefault("grocer", {})


def hours_since_refresh(data: dict) -> float:
    when = state(data).get("fetched")
    if not when:
        return 1e9
    try:
        then = datetime.fromisoformat(when)
    except ValueError:
        return 1e9
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - then).total_seconds() / 3600.0


# "ea" is what grocer.nz calls a countable thing and "each" is what prices.json
# calls it. Same unit, two spellings, and treating them as different silently
# refuses to price eggs.
_UNIT_ALIASES = {"ea": "each", "each": "each", "g": "g", "ml": "ml"}


def _reconcile_units(spec: dict, price: float, per: float,
                     unit: str) -> tuple[float, float, str, str]:
    """Get a fetched price into the units this item is costed in.

    Returns (price, per, unit, refusal) -- a non-empty refusal means do not
    write it. The interesting case is a shop that sells by weight something the
    recipes count: chicken thighs are $15.99/kg on the shelf and "4 chicken
    thighs" in a recipe. prices.json already carries `g_per_unit` for exactly
    these, so the conversion is *authored* rather than guessed -- which is the
    only reason it is done at all. Without that field the answer is a refusal,
    because inventing a weight for one thigh is inventing the price of the meal.
    """
    want = _UNIT_ALIASES.get(str(spec.get("unit") or unit).lower(), "")
    have = _UNIT_ALIASES.get(unit.lower(), "")
    if not want or not have:
        return price, per, unit, f"unit {spec.get('unit')!r} is not one this reads"
    if want == have:
        return price, per, want, ""

    if want == "each" and have in ("g", "ml"):
        size = float(spec.get(f"{have}_per_unit", 0) or 0)
        if size <= 0:
            return price, per, unit, (
                f"sold per {have}, counted per each, and no {have}_per_unit to "
                f"convert with")
        # price/per is the price of one gram; one item weighs `size` of them.
        return round(price / per * size, 4), 1.0, "each", ""

    return price, per, unit, f"source is per {have}, file says {want}"


def refresh(data: dict, store_id: int, storename: str,
            force: bool = False) -> dict[str, Any]:
    """Update every pinned price from one 60 KB download.

    Reports rather than raises for the cases that are normal: nothing pinned,
    too soon, unchanged since last time. Each of those is a thing to print, not
    a failure.
    """
    from . import pricesrc

    pins = pinned(data)
    if not pins:
        return {"status": "nothing pinned", "updated": [], "missing": [],
                "skipped": []}

    age = hours_since_refresh(data)
    if not force and age < MIN_REFRESH_HOURS:
        return {"status": f"last refreshed {age:.0f}h ago; --force to insist",
                "updated": [], "missing": [], "skipped": []}

    st = state(data)
    # If-None-Match is an optimisation for "there is nothing to do anyway", and
    # it is only true when every pin already has a price. Send it unconditionally
    # and a freshly pinned item is answered with 304 and never priced at all --
    # it stays unpriced until grocer.nz happens to change the file, which is a
    # feature that silently does nothing on exactly the run you cared about.
    # "Has no price" is the wrong test and cost this two goes to find. Almost
    # every pin already has a price -- the shipped estimate -- so asking whether
    # one exists says yes, the ETag goes out, and a freshly pinned item is
    # answered 304 and keeps the guess it was pinned to replace. What matters is
    # whether this store has ever actually been asked about it, which is exactly
    # what `checked` records.
    unpriced = [i for i in pins
                if not ((data["items"][i].get("prices") or {})
                        .get(storename, {}).get("checked"))]
    # A different store is a different price list, so the ETag from the old one
    # says nothing about this one -- and every price already in the file was
    # fetched somewhere else.
    moved = int(st.get("store_id") or 0) != int(store_id)
    etag_in = "" if (unpriced or moved) else str(st.get("etag") or "")
    prices, etag, unchanged = fetch_prices(store_id, etag_in)
    st.update({"source": SOURCE_NAME, "url": SOURCE_URL,
               "attribution": ATTRIBUTION, "store_id": int(store_id),
               "store": storename,
               "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    if unchanged:
        return {"status": "unchanged since last refresh (304, nothing downloaded)",
                "updated": [], "missing": [], "skipped": []}
    st["etag"] = etag

    updated: list[str] = []
    missing: list[str] = []
    skipped: list[str] = []
    for item, info in sorted(pins.items()):
        p = prices.get(int(info["id"]))
        if not p:
            missing.append(f"{item} (id {info['id']} no longer stocked here)")
            continue
        price, per, unit = unit_price(p.best_cent, str(info.get("size") or ""))
        if per <= 0:
            skipped.append(f"{item} (cannot read pack size "
                           f"{info.get('size')!r})")
            continue
        spec = data["items"][item]
        price, per, unit, why = _reconcile_units(spec, price, per, unit)
        if why:
            skipped.append(f"{item} ({why})")
            continue
        pricesrc.set_price(data, item, storename, price, per)
        note = f"{item}: ${price:.2f} per {per:g}{unit}"
        if p.sale_cent and p.sale_cent < p.original_cent:
            spec["prices"][storename]["special"] = {
                "price": round(p.sale_cent / 100.0, 2),
                "was": round(p.original_cent / 100.0, 2),
                "source": SOURCE_NAME,
            }
            note += f"  (on special, was ${p.original_cent / 100:.2f})"
        updated.append(note)

    return {"status": f"{len(prices)} prices read", "updated": updated,
            "missing": missing, "skipped": skipped}



# --- remembering what was offered --------------------------------------------
#
# grocer.nz's public search key is search-only: it cannot fetch a document by
# id, and `id` is not a filterable attribute, so there is no way to ask "what is
# product 1672" once the search that produced it has scrolled away. Rather than
# make the pin step guess, `find` writes down exactly what it showed and `pin`
# reads its answer back. That also means pinning costs no network call at all --
# it is a decision about something already on screen.

def remember(products: list[Product]) -> None:
    from . import store as _store

    seen = _recall_all()
    seen.update({str(p.id): {"id": p.id, "name": p.name, "brand": p.brand,
                             "size": p.size, "category": p.category}
                 for p in products})
    # Bounded, because this is a convenience cache and not a catalogue. Oldest
    # out first; dicts keep insertion order, so this is the last 500 offered.
    if len(seen) > 500:
        seen = dict(list(seen.items())[-500:])
    _store._write_json(_store.DATA / "grocer_seen.json", seen)


def _recall_all() -> dict:
    from . import store as _store

    return _store._read_json(_store.DATA / "grocer_seen.json", {}) or {}


def recall(product_id: int) -> Product | None:
    """A product previously shown by `find`, or None."""
    d = _recall_all().get(str(int(product_id)))
    if not d:
        return None
    return Product(id=int(d["id"]), name=str(d.get("name") or ""),
                   brand=str(d.get("brand") or ""), size=str(d.get("size") or ""),
                   category=str(d.get("category") or ""))



# --- the store list -----------------------------------------------------------
#
# grocer.nz's own store picker is fed from Firestore, which requires a signed-in
# user, so that door is shut and stays shut. The list is in the public catalogue
# instead -- `base_v3.duckdb.br`, the same static asset the site downloads to
# every visitor -- and it is 2.4 MB, which is forty times the price file. So it
# is fetched *rarely* and kept: stores open and close a handful of times a year,
# and the shipped copy is what the settings page reads. `--grocer-stores
# --refresh` is the only thing that ever downloads it.

STORES_URL = ASSETS + "/base_v3.duckdb.br"
STORES_PATH_NAME = "grocer_stores.json"


def stores_path():
    from . import store as _store

    return _store.DATA / STORES_PATH_NAME


def load_stores() -> list[dict]:
    """Every store grocer.nz knows about, as last fetched. Never network."""
    from . import store as _store

    data = _store._read_json(stores_path(), {}) or {}
    return list(data.get("stores") or [])


def find_stores(query: str, limit: int = 25) -> list[dict]:
    q = (query or "").strip().lower()
    rows = load_stores()
    if not q:
        return rows[:limit]
    hit = [s for s in rows
           if q in f"{s.get('vendor', '')} {s.get('name', '')}".lower()]
    return hit[:limit]


def store_by_id(store_id: int) -> dict:
    for s in load_stores():
        if int(s.get("id") or 0) == int(store_id):
            return s
    return {}


def fetch_stores() -> list[dict]:
    """Re-download the store list. 2.4 MB, so this is not the weekly job."""
    from datetime import date

    from . import store as _store

    _check_allowed(STORES_URL)
    try:
        import duckdb   # noqa: PLC0415 - deliberately local, see _read_parquet
    except ImportError as exc:
        raise GrocerError(
            "Refreshing the store list needs duckdb, which is not installed:\n"
            "    pip install duckdb\n"
            "The shipped list works without it; this is only for picking up "
            "stores that have opened since.") from exc

    import tempfile
    from pathlib import Path

    try:
        r = requests.get(STORES_URL, timeout=TIMEOUT, headers=_headers())
        r.raise_for_status()
    except requests.RequestException as exc:
        raise GrocerError(f"Could not fetch the store list: {exc}") from exc

    # requests decompresses Content-Encoding: br for us, so what arrives is the
    # database itself despite the .br on the name. Decompressing again fails.
    tmp = Path(tempfile.gettempdir()) / "grocer_base.duckdb"
    tmp.write_bytes(r.content)
    try:
        con = duckdb.connect(str(tmp), read_only=True)
        rows = con.execute(
            "SELECT s.id, v.name, s.name FROM public_stores s "
            "JOIN public_vendors v ON v.id = s.vendor_id "
            "WHERE s.is_enabled ORDER BY v.name, s.name").fetchall()
        con.close()
    except Exception as exc:  # noqa: BLE001
        raise GrocerError(f"Could not read the store list: {exc}") from exc
    finally:
        tmp.unlink(missing_ok=True)

    stores = [{"id": int(i), "vendor": str(v), "name": str(n)} for i, v, n in rows]
    _store._write_json(stores_path(), {
        "source": SOURCE_NAME, "url": SOURCE_URL,
        "note": "Store ids for --grocer-refresh. Prices are per store.",
        "fetched": date.today().isoformat(), "stores": stores})
    return stores


# The vendor names in the catalogue are the chain's own spelling; prices.json
# keys its columns by the names it was written with. Only the ones this project
# has price columns for need to agree.
VENDOR_TO_STORE = {
    "pak'nsave": "PAK'nSAVE",
    "new world": "New World",
    "woolworths": "Woolworths",
}


def store_name_for(vendor: str) -> str:
    """Which prices.json column a vendor's prices belong in.

    Returns "" for a chain the price file has no column for, which the caller
    must treat as "cannot use this store" rather than inventing a column --
    a price written under a store nobody costs against is a price that silently
    does nothing.
    """
    return VENDOR_TO_STORE.get((vendor or "").strip().lower(), "")
