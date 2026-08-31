"""What a recipe costs per portion, and where it is cheaper this week.

Three jobs, in order of how much can go wrong:

1. **Read an ingredient line into an amount, a unit and a thing.** "400g beef
   mince" is easy; "a good glug of olive oil" has no amount at all. Anything
   that cannot be read is reported as unpriced rather than assumed to be free,
   because a cost per portion quietly missing its most expensive line is worse
   than no number at all.

2. **Convert to the unit the price is quoted in.** Mass to mass is arithmetic.
   Volume to mass needs a density, and counts to mass need a typical size, so
   both live on the catalogue entry. Where neither is known the line is
   unpriced -- again, said out loud.

3. **Decide what to charge for.** Pantry staples are assumed to be in the
   cupboard already. A knob of butter is free; the 250g a pastry wants is not.
   So each staple carries a free allowance and only the *excess* is charged --
   the excess rather than the whole amount, so cost is continuous. Charging the
   full 250g the moment it crosses 30g would make a recipe using 31g of butter
   cost forty cents more than one using 29g, which is a cliff nobody could
   explain from the page.

Prices come from prices.json. Nothing here fetches anything: see the notes in
that file about where the numbers are from and how much to trust them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import quantity
from .quantity import (QUANTITY_RE, VULGAR, WORD_NUMBERS, _parse_number,
                       is_section_header)

# unit -> (kind, how many base units). Base units are grams and millilitres.
# Below this share of a recipe's lines priced, no figure is reported at all.
# See RecipeCost.usable for why this is a third state rather than a caveat.
SHOW_MIN_COVERAGE = 0.5

# An unpriced line carrying at least this much of something is on its own
# enough to withhold the figure, whatever the line count says. Coverage counts
# lines, and a line is a terrible unit of money: a beef birria with 1.3 kg of
# unpriced chuck in it and eleven priced spices scores 55% covered and comes out
# at 59 cents a portion. Half a kilo is about where an ingredient stops being a
# seasoning and starts being the thing you are actually buying.
MAJOR_UNPRICED_GRAMS = 400.0

UNITS: dict[str, tuple[str, float]] = {
    "g": ("mass", 1.0), "gram": ("mass", 1.0), "grams": ("mass", 1.0),
    "gm": ("mass", 1.0), "gms": ("mass", 1.0),
    "kg": ("mass", 1000.0), "kilo": ("mass", 1000.0), "kilos": ("mass", 1000.0),
    "kilogram": ("mass", 1000.0), "kilograms": ("mass", 1000.0),
    "oz": ("mass", 28.35), "ounce": ("mass", 28.35), "ounces": ("mass", 28.35),
    "lb": ("mass", 453.6), "lbs": ("mass", 453.6), "pound": ("mass", 453.6),

    "ml": ("volume", 1.0), "millilitre": ("volume", 1.0),
    "millilitres": ("volume", 1.0), "mls": ("volume", 1.0),
    "l": ("volume", 1000.0), "litre": ("volume", 1000.0),
    "litres": ("volume", 1000.0), "liter": ("volume", 1000.0),
    # NZ/AU metric cup is 250ml, not the US 240. The planner is in Auckland.
    "cup": ("volume", 250.0), "cups": ("volume", 250.0),
    "tbsp": ("volume", 15.0), "tablespoon": ("volume", 15.0),
    "tablespoons": ("volume", 15.0), "tbs": ("volume", 15.0),
    "tsp": ("volume", 5.0), "teaspoon": ("volume", 5.0),
    "teaspoons": ("volume", 5.0),
}

# A second amount in the other measurement system, written after a slash:
# "600g / 1.4lb", "1 kg/ 2 lb", "500ml / 2 cups". Longest unit names first so
# "litre" is tried before "l"; the trailing \b is what keeps "l" off "large".
_ALT_AMOUNT_RE = re.compile(
    r"^\s*/\s*[\d][\d.,\s/]*\s*(?:"
    + "|".join(re.escape(u) for u in sorted(UNITS, key=len, reverse=True))
    + r")\b\.?",
    re.I,
)


# Words that look like units and are really shapes of packaging or vagueness.
# They stop a line being priced by amount; some are rescued by the catalogue
# (a "tin" of tomatoes has a known size), the rest are honestly unpriceable.
PACK_WORDS = {"tin", "tins", "can", "cans", "jar", "jars", "packet", "packets",
              "pack", "packs", "punnet", "punnets", "bunch", "bunches",
              "clove", "cloves", "sprig", "sprigs", "stick", "sticks",
              "head", "heads", "slice", "slices", "rasher", "rashers",
              "fillet", "fillets", "breast", "breasts", "thigh", "thighs",
              "leg", "legs", "block", "blocks", "bag", "bags"}

VAGUE_WORDS = {"pinch", "splash", "drizzle", "handful", "dash", "knob",
               "glug", "squeeze", "few", "little", "good", "generous",
               "extra"}

# Size adjectives are not vague, they are *descriptions of a thing you can
# count*. "1 large onion" is one onion, and an onion has a known weight in the
# catalogue -- so refusing to price it, which is what filing these under
# VAGUE_WORDS did, threw away a line it could have costed to within a third.
# That is the wrong trade: a third out on one onion is noise beside dropping
# the onion entirely, and "onion" was the single most common unpriced
# ingredient in a real library, blocking nineteen recipes.
#
# The factors are what the words mean rather than a hedge. Left at a flat 1.0
# they would still beat refusing, and there is no reason to be that coarse when
# a greengrocer's small and large differ by about this much.
SIZE_WORDS: dict[str, float] = {
    "small": 0.75, "medium": 1.0, "large": 1.3, "extra large": 1.6,
    "baby": 0.5, "jumbo": 1.6,
}

_PREP_ADVERBS = "finely|roughly|thinly|coarsely|freshly|lightly|well"
_PREP_VERBS = ("chopped|diced|sliced|minced|grated|crushed|peeled|trimmed|"
               "shredded|julienned|cubed|halved|quartered|beaten|melted|"
               "softened|drained|rinsed")

# Trailing noise on an ingredient line. Everything after a comma is nearly
# always preparation ("finely diced", "plus extra to serve") rather than a
# different ingredient, so it is cut before matching.
_PREP_RE = re.compile(
    rf"\b(?:{_PREP_ADVERBS})?\s*"
    rf"(?:{_PREP_VERBS}|to serve|to taste|to garnish|plus extra|optional)\b.*",
    re.I,
)

# ...but only when it is not the first thing on the line. Position is the whole
# distinction: preparation written *after* the ingredient describes what came
# before it, and everything from there on is noise. Written *before*, it
# describes what follows, and cutting from it to the end of the line leaves
# nothing at all -- which is what happened to 78 of the 83 lines in a real
# library whose ingredient name came out empty ("grated parmesan", "minced
# ginger", "roughly chopped coriander leaves"). Each was then matched on its
# footnote instead, so "coriander (sub green onion)" was a spring onion.
#
# Leaving the leading words in place costs nothing: "chopped" and "grated" are
# not ingredient names, the matcher looks for aliases anywhere in the text, and
# the one place a prep word *is* part of the name -- a tin of chopped tomatoes
# -- keeps working precisely because it was left alone.


@dataclass
class Line:
    """One ingredient line, read as far as it can be."""
    raw: str
    amount: float | None = None
    unit: str = ""
    item: str = ""
    item_raw: str = ""         # before preparation words are stripped
    matched: str = ""          # catalogue key it resolved to, if any
    base: float | None = None  # amount in the catalogue item's own unit
    cost: float = 0.0
    staple: bool = False
    free: bool = False         # a staple under its allowance
    priced: bool = False
    verified: bool = False     # priced from a real price, not a shipped guess
    why_unpriced: str = ""


def _short_name(line: "Line") -> str:
    """A few words naming what a line is, for showing to a person.

    `item` is what the matcher works on and is usually already short. When it
    came out empty the fallback is the raw line, and a raw line can be most of a
    sentence -- so it is cut at the first comma or bracket and capped, which is
    where recipe writers put the notes rather than the ingredient.
    """
    # item, then item_raw, then the whole line. `item` is empty more often than
    # it looks -- the prep-word stripper can eat a name down to nothing -- and
    # `item_raw` is the same line one step earlier, which is where the words
    # still are.
    text = (line.item or "").strip() or (line.item_raw or "").strip() \
        or (line.raw or "")

    # Brackets first, then the comma. Cutting at the first bracket instead
    # looks equivalent and throws the name away on "3 1/2 cups (350g) shredded
    # cheese", where the bracket comes first and holds only the amount -- which
    # is what left the page saying "no price for chuck beef," with a trailing
    # comma and nothing after it.
    text = re.sub(r"\([^)]*\)", " ", text)
    # ...and an opening bracket with no partner, which is what an upstream
    # truncation leaves behind: "beef rump cap / picanha (more names: sirloin".
    text = re.sub(r"\([^)]*$", " ", text).split(",")[0]
    text = re.sub(r"\s+", " ", text).strip(" .-")
    if len(text) > 28:
        text = text[:28].rstrip(" ,-/")
    return text


# Public name for the same thing: the shopping list needs to show an
# ingredient it could not price, and "a few words naming what this line is"
# is exactly that question asked from outside this module.
short_name = _short_name


def _strip_prep(text: str) -> str:
    text = text.split(",")[0]
    m = _PREP_RE.search(text)
    if m and m.start() > 0:                       # see _PREP_RE on why not 0
        text = text[:m.start()]
    text = re.sub(r"\(.*?\)", " ", text)          # "(about 2 medium)"
    return re.sub(r"\s+", " ", text).strip(" .-")


def parse_line(raw: str) -> Line:
    """Split an ingredient line into amount, unit and ingredient name."""
    line = Line(raw=raw)
    text = (raw or "").strip()
    if is_section_header(text):
        line.why_unpriced = "heading"
        return line

    rest = text
    amount: float | None = None

    m = QUANTITY_RE.match(text)
    if m and _parse_number(m.group("low")) is not None:
        low = float(_parse_number(m.group("low")))
        high = m.group("high") and _parse_number(m.group("high"))
        # A range costs the midpoint. Taking the low end understates every
        # recipe that says "2-3 tbsp" and the high end overstates it; the
        # midpoint is wrong by half as much either way.
        amount = (low + float(high)) / 2 if high else low
        rest = m.group("rest")
    else:
        wm = re.match(rf"^\s*({'|'.join(WORD_NUMBERS)})\b(.*)$", text, re.I)
        if wm and wm.group(1).lower() not in ("a", "an"):
            amount = float(WORD_NUMBERS[wm.group(1).lower()])
            rest = wm.group(2)
        elif wm:
            amount, rest = 1.0, wm.group(2)

    rest = rest.strip()

    # "400 g" and "400g" both leave the unit at the front of the remainder.
    unit = ""
    um = re.match(r"^([a-zA-Z]+)\.?\b(.*)$", rest)
    if um:
        word = um.group(1).lower()
        if word in UNITS:
            unit, rest = word, um.group(2)
        elif word in SIZE_WORDS:
            # Not a unit at all -- a size on the thing being counted. Consume
            # it, scale the count, and leave the unit empty so the line reads
            # as "1.3 onions", which the catalogue can price by the each.
            if amount is not None:
                amount *= SIZE_WORDS[word]
            rest = um.group(2)
        elif word in PACK_WORDS or word in VAGUE_WORDS:
            unit, rest = word, um.group(2)

    # "2 x 400g tin" -- the pack size is the real amount.
    xm = re.match(r"^x?\s*(\d+(?:\.\d+)?)\s*(g|kg|ml|l)\b(.*)$", rest, re.I)
    if xm:
        packs = amount or 1.0
        amount = packs * float(xm.group(1))
        unit, rest = xm.group(2).lower(), xm.group(3)

    # "3 1/2 cups (350g) shredded cheese", "1 1/2 cups / 150g mozzarella" --
    # a cup measure with the weight the author actually put on the scales
    # beside it. Take the weight.
    #
    # Only when the leading amount is a *volume* and the second one is a mass,
    # which is the whole of the rule. A cup of a solid is not a quantity, it is
    # a quantity plus how hard you pressed: shredded cheese is anywhere from 90
    # to 115 g a cup depending on the grater, and packed leaves are worse. The
    # gram figure has none of that in it. Going the other way -- "600g / 1.4lb
    # beef chuck" -- there is nothing to gain, the two say the same thing, and
    # the metric half is already in hand; that pairing is stripped as noise a
    # few lines below.
    #
    # It also spares the catalogue a density for every shredded and leafy thing
    # in it, which is a number that only ever existed to undo this conversion.
    if UNITS.get(unit, ("", 0.0))[0] == "volume":
        # Asked with the unit put back on the front, because that is the shape
        # `restatement` anchors to -- and sharing it with the scaler is the
        # point. The two have to agree about what a restatement is: one finds
        # them to scale, the other to price, and a line scaled by one and read
        # by the other is how a quarter batch of tacos gets a whole batch of
        # cheese.
        whole = unit + rest
        m2 = quantity.restatement(whole)
        second = UNITS.get(m2.group("unit").lower(), ("", 0.0)) if m2 else ("", 0.0)
        if m2 and second[0] == "mass":
            value = quantity._parse_number(m2.group("amount"))
            if value is not None:
                amount = float(value) * second[1]
                unit, rest = "g", whole[m2.end():]

    line.amount = amount
    line.unit = unit

    # A *word*, not a character class. `lstrip(" ofx")` is the obvious version
    # and it is silently catastrophic: it strips any leading o, f or x, so
    # "onions" became "nions", "olive oil" became "live oil" and "flour" became
    # "lour" -- three ingredients that then matched nothing and were quietly
    # dropped from the cost.
    rest = re.sub(r"^\s*(?:of|x)\s+", "", rest, flags=re.I).strip()

    # "600g / 1.4lb beef chuck" -- the same amount said twice for two
    # audiences. The metric half is already in `amount`; what is left is the
    # imperial half glued to the front of the ingredient, and it made the item
    # name "/ 1.4lb beef chuck", which matches nothing in any catalogue.
    #
    # This is most of a real library rather than an edge case: RecipeTin Eats
    # writes every quantity in both units, and it is two thirds of the one this
    # was tested against. The visible symptom was every beef, pork and chicken
    # cut in the library coming back "not in the price list" while the spices
    # beside them priced perfectly -- which is exactly the shape that makes a
    # recipe look cheap rather than unpriced.
    rest = _ALT_AMOUNT_RE.sub("", rest, count=1).strip()

    # Pack words at the front describe the container, not the contents:
    # "tin chopped tomatoes" is tomatoes.
    rest = re.sub(r"^\s*(?:" + "|".join(PACK_WORDS) + r")\s+", "", rest, flags=re.I)

    line.item_raw = rest.strip()
    line.item = _strip_prep(rest)
    if not line.item and not line.item_raw:
        line.why_unpriced = "no ingredient name"
    return line


class Catalogue:
    """The price list, indexed for matching against messy ingredient text."""

    def __init__(self, data: dict, primary: str = ""):
        """`primary` is which shop this catalogue is read against.

        Passed in rather than read out of the price file, because where you
        shop is a preference and belongs in config.json with the rest of them.
        The file used to carry its own `primary_store` and the two disagreed --
        the file said New World, the config said PAK'nSAVE -- so anything that
        asked the file got a confident answer about a shop nobody uses. That is
        not a bug you find by reading either file; you find it when a freshness
        figure says 0 checked with thirteen real prices in the next column.

        Falling back to the first listed store keeps a bare Catalogue({...})
        usable in tests and one-off scripts. It is a default, not a setting.
        """
        self.data = data or {}
        self.currency = self.data.get("currency", "NZD")
        self.stores: list[str] = list(self.data.get("stores") or [])
        self.primary: str = primary or (self.stores[0] if self.stores else "")
        self.items: dict[str, dict] = self.data.get("items") or {}

        # Longest alias first, so "spring onion" wins over "onion" and
        # "sweet potato" over "potato". Matching the short one would be
        # silently, plausibly wrong -- and cheaper, which hides it.
        #
        # Ties broken alphabetically rather than left to whatever order the
        # file happens to list its items in. Two aliases the same length on
        # different items is not hypothetical -- "guajillo" and "chillies" are
        # both eight -- and resolving that by dict order means the answer moves
        # when someone reorders the price file, which is not a change anybody
        # would connect to a chilli being priced fresh instead of dried. Where
        # it matters the fix is a longer alias, not a luckier order.
        self._aliases: list[tuple[str, str]] = []
        for key, spec in self.items.items():
            for alias in [key] + list(spec.get("aliases") or []):
                self._aliases.append((alias.lower(), key))
        self._aliases.sort(key=lambda kv: (-len(kv[0]), kv[0]))
        # Built once, here, rather than per call. `match` walks every alias in
        # the file -- around four hundred -- for every ingredient line, and it
        # was escaping and recompiling each one every time: a re-plan spent two
        # and a half million calls to `re.escape` on strings that had not
        # changed since the file was read. The gate is the same idea as the one
        # in `scoring._prepare`: the pattern only adds word boundaries and an
        # optional plural to a literal, so an alias whose longest word is not
        # in the text literally cannot match, and `in` settles that at C speed.
        self._matchers = [
            (max(alias.split(), key=len) if alias.split() else alias,
             re.compile(r"\b" + re.escape(alias).replace(r"\ ", r"\s+")
                        + r"e?s?\b"),
             key)
            for alias, key in self._aliases
        ]

    def match(self, text: str) -> str:
        hay = f" {text.lower()} "
        for gate, pattern, key in self._matchers:
            if gate in hay and pattern.search(hay):
                return key
        return ""

    @property
    def unavailable(self) -> set[str]:
        """Catalogue items marked as not currently in the shops.

        Absent means available, so nothing has to be marked to keep working.
        This is a standing fact about a cut rather than a live stock check --
        nothing here fetches anything -- so it is maintained by hand in
        prices.json alongside the price, and it is the one field in that file
        that changes what gets *cooked* rather than what a night costs.
        """
        return {k for k, v in self.items.items() if v.get("available") is False}

    def unavailable_in(self, ingredients: str) -> list[str]:
        """Which unavailable items a recipe needs, if any."""
        if not self.unavailable:
            return []
        out = []
        for raw in (ingredients or "").splitlines():
            if not raw.strip():
                continue
            line = parse_line(raw)
            if line.why_unpriced == "heading":
                continue
            key = self.key_for(line)
            if key in self.unavailable and key not in out:
                out.append(key)
        return sorted(out)

    def key_for(self, line: "Line") -> str:
        """Which catalogue item a parsed line is: the name first, notes last.

        `item` is the ingredient with the recipe writer's asides taken off;
        `item_raw` is the same text with them still attached. Asking the
        stripped name first matters because those asides are overwhelmingly
        about what to use *instead*, and matching them picks the substitute
        over the ingredient. Read the wrong way round, "3 1/2 tbsp (50g)
        unsalted butter (or 2 1/2 tbsp olive oil)" is a line about olive oil,
        "10-12 eggs (reserve one yolk for glazing pastry)" is a line about puff
        pastry, and "cumin powder (sub coriander)" is a bunch of fresh herbs.

        Each of those was priced as the wrong thing, silently and plausibly,
        because a substitution note is made of real ingredient names. Falling
        back to the raw text still matters: the stripper can eat a name down to
        nothing ("3 1/2 cups (350g) shredded Colby..." starts with a bracket),
        and then the notes are all there is.
        """
        return self.match(line.item) or self.match(line.item_raw)

    # --- unit handling --------------------------------------------------------

    def to_item_units(self, line: Line, key: str) -> tuple[float | None, str]:
        """Convert a parsed line into the units the catalogue prices `key` in.

        Returns (amount, reason-if-None).
        """
        spec = self.items.get(key) or {}
        want = spec.get("unit", "g")

        if line.amount is None:
            return None, "no amount given"

        kind, factor = UNITS.get(line.unit, ("", 0.0))

        # A bare count: "2 onions", "4 chicken thighs".
        if not kind:
            if line.unit in VAGUE_WORDS:
                return None, f"'{line.unit}' is not a measurable amount"
            per = spec.get("g_per_unit")
            if want == "each":
                # Pack words that describe a portion of the thing are counted
                # as units of it: 4 chicken thighs is 4 thighs, not 4 chickens,
                # so the catalogue prices thighs and not birds.
                return line.amount, ""
            if per:
                return line.amount * float(per), ""
            return None, f"no unit size known for {key}"

        base = line.amount * factor          # grams or millilitres

        if kind == "mass":
            if want == "g":
                return base, ""
            if want == "each" and spec.get("g_per_unit"):
                return base / float(spec["g_per_unit"]), ""
            if want == "ml":
                density = float(spec.get("g_per_ml", 1.0))
                return base / density, ""
            return None, f"cannot convert grams to {want}"

        # volume
        if want == "ml":
            return base, ""
        if want == "g":
            density = spec.get("g_per_ml")
            if density is None:
                return None, f"no density known for {key}"
            return base * float(density), ""
        if want == "each" and spec.get("ml_per_unit"):
            return base / float(spec["ml_per_unit"]), ""
        return None, f"cannot convert millilitres to {want}"

    # --- prices ---------------------------------------------------------------

    def unit_price(self, key: str, store: str, on_special: bool = True
                   ) -> tuple[float | None, dict | None]:
        """(price per single catalogue unit at this store, the special if any)."""
        spec = self.items.get(key) or {}
        entry = (spec.get("prices") or {}).get(store)
        if not entry:
            return None, None
        per = float(entry.get("per", 1) or 1)
        special = entry.get("special") if on_special else None
        price = float(special["price"]) if special else float(entry.get("price", 0))
        return price / per, special

    def is_verified(self, key: str, store: str) -> bool:
        """Whether this price was ever actually checked against a shop.

        `checked` is stamped by --price, --receipt and --grocer-refresh, and is
        absent on everything the file shipped with. It is the difference between
        a number and a guess, and until now nothing downstream could tell them
        apart -- so a week costed entirely from invented prices looked exactly
        like one costed from receipts.
        """
        entry = ((self.items.get(key) or {}).get("prices") or {}).get(store) or {}
        return bool(entry.get("checked"))

    def stores_with(self, key: str) -> list[str]:
        return list(((self.items.get(key) or {}).get("prices") or {}).keys())


@dataclass
class RecipeCost:
    total: float = 0.0
    per_portion: float = 0.0
    servings: int = 0
    lines: list[Line] = field(default_factory=list)
    unpriced: list[Line] = field(default_factory=list)
    staples_free: list[str] = field(default_factory=list)
    coverage: float = 0.0        # share of priceable lines we actually priced
    verified_total: float = 0.0  # of `total`, how much came from real prices

    @property
    def verified_share(self) -> float:
        """How much of this number is real, by money rather than by line count.

        By money on purpose. A recipe can have nineteen guessed spices and one
        checked cut of beef and still be almost entirely right, because the beef
        is almost all of the cost -- counting lines would call that 5% real and
        be badly wrong about how much to trust the total.
        """
        return (self.verified_total / self.total) if self.total else 0.0

    @property
    def confident(self) -> bool:
        """Whether the number is worth showing without a caveat.

        Coverage rather than a count, because a recipe of four ingredients with
        one unpriced is a much worse estimate than one of twenty with the same.
        """
        return self.coverage >= 0.8

    @property
    def major_unpriced(self) -> list[str]:
        """Unpriced lines big enough to decide the answer on their own.

        Named short enough to print. These end up on the page as the reason a
        night has no price, and a recipe line is not a name -- "3 1/2 cups
        (350g) shredded Colby, Monterey Jack or Oaxaca cheese (if you can get
        it) (Note 6)" is what one of them says in full.
        """
        out = []
        for line in self.unpriced:
            kind_factor = UNITS.get((line.unit or "").lower())
            if not kind_factor or line.amount is None:
                continue
            if line.amount * kind_factor[1] >= MAJOR_UNPRICED_GRAMS:
                name = _short_name(line)
                if name:
                    out.append(name)
        return out

    @property
    def usable(self) -> bool:
        """Whether there is a number here at all, as opposed to a caveat.

        There are three states, not two, and the third one arrived with a real
        library. A roast whose only priceable line was the salt came out at
        **$0.00 a portion** for two kilos of picanha, and the page dutifully
        printed it with "approx" beside it. But "approx" claims the number is
        roughly right, and zero is not roughly the price of a beef roast -- it
        is the price of the things we happened to know, which is a different
        quantity wearing the same units.

        So: at or above `confident` it is shown plainly, between the two it is
        shown as approximate, and below `SHOW_MIN_COVERAGE` there is no number,
        because the honest answer is that we cannot say. The threshold is a half
        deliberately: at exactly half the lines priced the figure is already
        more missing than the reader would ever guess from looking at it.

        Coverage alone is not enough, which is why `major_unpriced` also has a
        veto. Coverage counts lines and money does not live in lines -- a recipe
        can be 55% covered because eleven spices priced and the 1.3 kg of beef
        did not, and there is no share of *lines* that makes that figure mean
        anything.
        """
        return self.coverage >= SHOW_MIN_COVERAGE and not self.major_unpriced


def cost_recipe(ingredients: str, servings: int, cat: Catalogue,
                store: str = "", specials: bool = True) -> RecipeCost:
    """What one recipe costs at one store."""
    store = store or cat.primary
    out = RecipeCost(servings=max(1, servings or 1))
    priceable = 0

    for raw in (ingredients or "").splitlines():
        if not raw.strip():
            continue
        line = parse_line(raw)
        if line.why_unpriced == "heading":
            continue
        priceable += 1
        out.lines.append(line)

        key = cat.key_for(line)
        if not key:
            line.why_unpriced = line.why_unpriced or "not in the price list"
            out.unpriced.append(line)
            continue
        line.matched = key
        spec = cat.items[key]

        amount, reason = cat.to_item_units(line, key)
        if amount is None:
            # A staple written with no amount at all -- "olive oil", "salt",
            # "basil" on its own line -- is a cupboard item by definition. It
            # cannot be excessive if the recipe never said how much, so it is
            # free rather than unpriced. Counting it as unpriced instead was
            # dragging coverage down over exactly the lines that were never
            # going to cost anything.
            #
            # A *vague* amount of one is the same case wearing a number. "1
            # pinch cooking salt" and "a knob of butter" have an amount and no
            # size, and the only thing that could ever be said about them is
            # that they are under any allowance worth the name -- nobody
            # measures a pinch and nobody buys one. Left unpriced it counted
            # against the recipe exactly as a missing kilo of beef would.
            vague = line.unit in VAGUE_WORDS
            if spec.get("staple") and (line.amount is None or vague):
                line.staple = line.free = line.priced = True
                out.staples_free.append(key)
                continue
            line.why_unpriced = reason
            out.unpriced.append(line)
            continue
        line.base = amount

        # Pantry staples: the cupboard covers an ordinary amount, and only the
        # excess above that allowance is charged. See the module docstring for
        # why the excess and not the whole amount.
        chargeable = amount
        if spec.get("staple"):
            line.staple = True
            allowance = float(spec.get("free_allowance", 0) or 0)
            if amount <= allowance:
                line.free = True
                line.priced = True
                out.staples_free.append(key)
                continue
            chargeable = amount - allowance

        unit_price, _ = cat.unit_price(key, store, specials)
        if unit_price is None:
            line.why_unpriced = f"no price at {store}"
            out.unpriced.append(line)
            continue

        line.cost = chargeable * unit_price
        line.priced = True
        line.verified = cat.is_verified(key, store)
        out.total += line.cost
        if line.verified:
            out.verified_total += line.cost

    out.coverage = (1.0 - len(out.unpriced) / priceable) if priceable else 0.0
    out.per_portion = out.total / out.servings
    return out


@dataclass
class Saving:
    item: str
    store: str
    normal: float
    special: float
    saved: float
    note: str = ""
    until: str = ""


def find_savings(ingredients_by_recipe: list[str], cat: Catalogue,
                 primary: str = "", min_saving: float = 0.50
                 ) -> list[Saving]:
    """Items worth buying somewhere other than the usual shop this week.

    Deliberately an exception list rather than a cheapest-basket optimiser. The
    ask is to shop at one supermarket and be told when something is worth
    breaking that for, and a solver that splits a list across three stores to
    save four dollars answers a question nobody asked -- it costs two more car
    trips than it saves.

    Only *advertised specials* count. A rival being permanently a few cents
    cheaper is not news, it is just a different shop.
    """
    primary = primary or cat.primary
    wanted: dict[str, float] = {}

    for ingredients in ingredients_by_recipe:
        for raw in (ingredients or "").splitlines():
            if not raw.strip() or raw.strip().endswith(":"):
                continue
            line = parse_line(raw)
            key = cat.key_for(line)
            if not key:
                continue
            amount, reason = cat.to_item_units(line, key)
            if amount is None:
                continue
            spec = cat.items[key]
            if spec.get("staple"):
                allowance = float(spec.get("free_allowance", 0) or 0)
                amount = max(0.0, amount - allowance)
            if amount > 0:
                wanted[key] = wanted.get(key, 0.0) + amount

    savings: list[Saving] = []
    for key, amount in wanted.items():
        here, _ = cat.unit_price(key, primary, True)
        if here is None:
            here, _ = cat.unit_price(key, primary, False)
        if here is None:
            continue

        best: Saving | None = None
        for store in cat.stores_with(key):
            if store == primary:
                continue
            price, special = cat.unit_price(key, store, True)
            if price is None or not special:
                continue
            saved = (here - price) * amount
            if saved >= min_saving and (best is None or saved > best.saved):
                best = Saving(item=key, store=store,
                              normal=here * amount, special=price * amount,
                              saved=saved, note=str(special.get("note", "")),
                              until=str(special.get("until", "")))
        if best:
            savings.append(best)

    savings.sort(key=lambda s: -s.saved)
    return savings
