"""Scale the quantities on an ingredient line.

The README used to say this was not worth attempting, and that was right for
*merging* a shopping list -- adding "a splash" to "2 tbsp, plus extra" has no
answer. Scaling one line is a different and much smaller problem: multiply the
leading number, leave every word alone. "400g beef mince" at 0.5 is "200g beef
mince" and nothing else about the line has to be understood.

So the rule here is that anything not confidently parsed is returned untouched
and *reported* as untouched, rather than guessed at. A line the caller knows was
not scaled can be shown with a note; a line silently halved wrongly cannot be
recovered from.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from fractions import Fraction

# Vulgar fractions, which recipe sites use constantly and str() cannot read.
VULGAR = {
    "½": Fraction(1, 2), "⅓": Fraction(1, 3), "⅔": Fraction(2, 3),
    "¼": Fraction(1, 4), "¾": Fraction(3, 4), "⅕": Fraction(1, 5),
    "⅖": Fraction(2, 5), "⅗": Fraction(3, 5), "⅘": Fraction(4, 5),
    "⅙": Fraction(1, 6), "⅚": Fraction(5, 6), "⅛": Fraction(1, 8),
    "⅜": Fraction(3, 8), "⅝": Fraction(5, 8), "⅞": Fraction(7, 8),
}

# Words used as counts. Only up to twelve: past that recipes use digits, and
# "a hundred" is not a phrase that appears in an ingredient list.
WORD_NUMBERS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "half": Fraction(1, 2), "quarter": Fraction(1, 4),
}

# Units that are a mass or a volume, so a *fraction* is meaningless -- nobody
# weighs out "3/4 of 250g". Split by size, because the sensible rounding differs
# by three orders of magnitude: 187.5g rounds to 190g and is still a weight,
# while 0.75kg rounded the same way is 1kg, which is a third more lamb.
FINE_METRIC = {"g", "gram", "grams", "ml", "millilitre", "millilitres", "oz"}
BULK_METRIC = {"kg", "kilo", "kilos", "kilogram", "kilograms",
               "l", "litre", "litres", "liter", "liters", "lb", "lbs"}
METRIC_UNITS = FINE_METRIC | BULK_METRIC

_NUM = r"\d+(?:\.\d+)?"
_FRAC = r"\d+\s*/\s*\d+"
_VULGAR = "".join(VULGAR)

# A leading quantity: an optional whole number, an optional fraction (written or
# vulgar), and optionally a range ("2-3", "2 to 3"). Anchored at the start,
# because a number in the middle of a line is part of the description ("1 x 400g
# tin") or a temperature, and neither is the amount.
QUANTITY_RE = re.compile(
    rf"""^\s*
    (?P<low>
        (?:{_NUM}\s*)?(?:{_FRAC}|[{_VULGAR}])   # 1 1/2, 1/2, 1 1/2 as a vulgar
        |{_NUM}                                  # or a plain 400 / 1.5
    )
    (?:\s*(?:-|–|to)\s*
       (?P<high>(?:{_NUM}\s*)?(?:{_FRAC}|[{_VULGAR}])|{_NUM}))?
    (?P<rest>.*)$""",
    re.X,
)

WORD_RE = re.compile(
    rf"^\s*(?P<word>{'|'.join(WORD_NUMBERS)})\b(?P<rest>.*)$", re.I
)


def _parse_number(text: str) -> Fraction | None:
    """'1 1/2' / '1½' / '½' / '400' / '1.5' -> Fraction, or None."""
    text = (text or "").strip()
    if not text:
        return None
    total = Fraction(0)
    seen = False

    # Split off any vulgar fraction characters first; they can be glued to a
    # whole number with no space ("1½"), which nothing else here would split.
    for ch in list(text):
        if ch in VULGAR:
            total += VULGAR[ch]
            seen = True
            text = text.replace(ch, " ", 1)

    for part in text.split():
        part = part.strip()
        if not part:
            continue
        try:
            if "/" in part:
                num, _, den = part.partition("/")
                total += Fraction(int(num), int(den))
            elif "." in part:
                total += Fraction(part)
            else:
                total += Fraction(int(part))
            seen = True
        except (ValueError, ZeroDivisionError):
            return None
    return total if seen else None


def _first_unit(rest: str) -> str:
    m = re.match(r"\s*([a-zA-Z]+)", rest or "")
    return m.group(1).lower() if m else ""


def format_amount(value: Fraction, unit: str = "") -> str:
    """Render a scaled amount the way a person would write it.

    Fine metric (g, ml) goes to whole numbers, because 187.5g is not a quantity
    anybody measures. Bulk metric (kg, l) keeps two decimals, because rounding
    0.75kg the same way gives 1kg -- a third more lamb. Everything else is a
    count, rounds to a quarter, and comes back as a mixed fraction, because half
    an onion is a real instruction.
    """
    if value <= 0:
        return "0"

    if unit in FINE_METRIC:
        f = float(value)
        if f >= 100:
            return str(int(round(f / 5.0) * 5))       # nearest 5g/5ml
        if f >= 10:
            return str(int(round(f)))
        return f"{f:.1f}".rstrip("0").rstrip(".")

    if unit in BULK_METRIC:
        return f"{float(value):.2f}".rstrip("0").rstrip(".")

    quarters = Fraction(round(value * 4), 4)
    if quarters == 0:
        quarters = Fraction(1, 4)
    whole, frac = divmod(quarters, 1)
    if frac == 0:
        return str(int(whole))
    frac_text = f"{frac.numerator}/{frac.denominator}"
    return f"{int(whole)} {frac_text}" if whole else frac_text


@dataclass
class ScaledLine:
    text: str
    scaled: bool          # did we actually change a number
    original: str


def scale_line(line: str, factor: float) -> ScaledLine:
    """Scale the leading quantity on one ingredient line."""
    raw = line.rstrip()
    if not raw.strip() or factor == 1.0:
        return ScaledLine(raw, False, raw)

    # Section headings ("For the sauce:") carry no quantity by definition.
    if raw.rstrip().endswith(":"):
        return ScaledLine(raw, False, raw)

    m = QUANTITY_RE.match(raw)
    if m:
        low = _parse_number(m.group("low"))
        high = _parse_number(m.group("high")) if m.group("high") else None
        if low is not None:
            rest = m.group("rest")
            unit = _first_unit(rest)
            f = Fraction(factor).limit_denominator(1000)
            new_low = format_amount(low * f, unit)
            if high is not None:
                new = f"{new_low}-{format_amount(high * f, unit)}{rest}"
            else:
                new = f"{new_low}{rest}"
            return ScaledLine(new, True, raw)

    wm = WORD_RE.match(raw)
    if wm:
        word = wm.group("word").lower()
        value = Fraction(WORD_NUMBERS[word])
        rest = wm.group("rest")
        # "a pinch of salt" is not a count, however much it parses like one --
        # scaling it produces "0.5 pinch", which is noise pretending to be
        # precision. Only scale a word number when it counts something.
        if word in ("a", "an") and _first_unit(rest) in {
            "pinch", "splash", "drizzle", "handful", "little", "few", "dash",
            "good", "generous", "squeeze", "knob", "sprig",
        }:
            return ScaledLine(raw, False, raw)
        f = Fraction(factor).limit_denominator(1000)
        return ScaledLine(f"{format_amount(value * f)}{rest}", True, raw)

    return ScaledLine(raw, False, raw)


def scale_ingredients(text: str, factor: float) -> tuple[list[ScaledLine], int]:
    """Scale every line. Returns (lines, how many could not be scaled)."""
    out: list[ScaledLine] = []
    unscaled = 0
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        result = scale_line(line, factor)
        # A heading was never going to have a quantity, so it is not a failure.
        is_heading = line.rstrip().endswith(":")
        if not result.scaled and not is_heading and factor != 1.0:
            unscaled += 1
        out.append(result)
    return out, unscaled


def servings_factor(servings: str, people: int) -> tuple[float, int]:
    """(factor, servings the recipe makes). Factor is 1.0 when unknown."""
    if not people or not servings:
        return 1.0, 0
    m = re.search(r"\d+", servings)
    if not m:
        return 1.0, 0
    made = int(m.group())
    if made <= 0:
        return 1.0, 0
    return people / made, made
