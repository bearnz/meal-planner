"""Checks for ingredient parsing, unit conversion, costing and specials."""

from __future__ import annotations

import json

from mealplan import pricing, store


def run(check):
    cat = pricing.Catalogue(store.load_prices())
    if not cat.items:
        check("prices.json loads", False, "no items found")
        return

    _parsing(check)
    _units(check, cat)
    _staples(check, cat)
    _costing(check, cat)
    _savings(check, cat)


def _parsing(check):
    print("\ningredient parsing")
    cases = [
        ("400g beef mince", 400.0, "g", "beef mince"),
        ("1kg lamb shoulder", 1.0, "kg", "lamb shoulder"),
        ("2 onions", 2.0, "", "onions"),
        ("2 tbsp olive oil", 2.0, "tbsp", "olive oil"),
        ("3 cloves garlic, finely chopped", 3.0, "cloves", "garlic"),
        ("1 x 400g tin chopped tomatoes", 400.0, "g", "chopped tomatoes"),
        ("2 x 400g tins kidney beans", 800.0, "g", "kidney beans"),
        ("1/2 cup cream", 0.5, "cup", "cream"),
    ]
    for raw, amount, unit, item in cases:
        L = pricing.parse_line(raw)
        got_item = L.item_raw or L.item
        check(f"{raw!r} -> {amount} {unit or 'each'} of {item!r}",
              L.amount == amount and L.unit == unit and item in got_item.lower(),
              f"amount={L.amount} unit={L.unit!r} item={got_item!r}")

    # The bug that made this worth testing at all: a character-class lstrip
    # ate the first letter of any ingredient starting with o, f or x.
    for raw, want in [("2 onions", "onion"), ("2 tbsp olive oil", "olive oil"),
                      ("300g flour", "flour")]:
        L = pricing.parse_line(raw)
        check(f"{raw!r} keeps its first letter",
              (L.item_raw or L.item).lower().startswith(want[:3]),
              f"got {(L.item_raw or L.item)!r}")

    # A range costs the midpoint, not either end.
    L = pricing.parse_line("2-3 tbsp soy sauce")
    check("'2-3 tbsp' is costed at 2.5", L.amount == 2.5, str(L.amount))

    for raw in ("Salt and pepper to taste", "a good glug of oil",
                "a pinch of saffron"):
        L = pricing.parse_line(raw)
        check(f"{raw!r} yields no usable amount",
              L.amount is None or L.unit in pricing.VAGUE_WORDS,
              f"amount={L.amount} unit={L.unit!r}")


def _units(check, cat):
    print("\nunit conversion")
    def conv(raw):
        L = pricing.parse_line(raw)
        key = cat.match(L.item_raw or L.item) or cat.match(L.item)
        return key, cat.to_item_units(L, key) if key else (None, "no match")

    key, (amt, why) = conv("1kg beef shin")
    check("1kg -> 1000g", key == "beef shin" and amt == 1000.0, f"{key} {amt} {why}")
    key, (amt, why) = conv("2 onions")
    check("a count stays a count for an each-priced item",
          key == "onion" and amt == 2.0, f"{key} {amt} {why}")
    key, (amt, why) = conv("600g potatoes")
    check("mass stays mass for a per-kg item", amt == 600.0, f"{amt} {why}")
    key, (amt, why) = conv("2 potatoes")
    check("a count converts to mass via g_per_unit", amt == 300.0, f"{amt} {why}")
    key, (amt, why) = conv("2 cups flour")
    check("cups convert to grams via density (NZ cup = 250ml)",
          amt == 300.0, f"{amt} {why}")
    key, (amt, why) = conv("2 tbsp olive oil")
    check("tbsp -> 30ml", key == "olive oil" and amt == 30.0, f"{key} {amt} {why}")

    # Ambiguity that is both plausible and expensive if it goes the wrong way.
    check("'pepper' means black pepper, not capsicum",
          cat.match("black pepper") == "black pepper"
          and cat.match("1 tsp pepper") != "capsicum",
          str(cat.match("1 tsp pepper")))
    # Longest alias wins, or every specific ingredient collapses to a cheap one.
    check("'spring onion' beats 'onion'", cat.match("2 spring onions") == "spring onion")
    check("'sweet potato' beats 'potato'", cat.match("500g sweet potato") == "kumara")
    check("'arborio rice' beats 'rice'", cat.match("350g arborio rice") == "arborio rice")

    L = pricing.parse_line("2 sprigs rosemary")
    key = cat.match(L.item_raw or L.item)
    amt, why = cat.to_item_units(L, key)
    check("an unknown pack size is refused rather than guessed",
          amt is not None or "no unit size" in why, f"{amt} {why}")


def _staples(check, cat):
    print("\npantry staples")
    c = pricing.cost_recipe("1 tsp salt\n2 tbsp olive oil\n1 bay leaf", 4, cat)
    check("a teaspoon of salt is free", c.total == 0.0, f"${c.total:.2f}")
    check("...and is still counted as priced, not unpriced",
          not c.unpriced, str([l.raw for l in c.unpriced]))

    # The whole point of the allowance: a knob of butter is free and a pastry's
    # worth is not.
    small = pricing.cost_recipe("20g butter", 1, cat).total
    large = pricing.cost_recipe("250g butter", 1, cat).total
    check("20g of butter is free", small == 0.0, f"${small:.2f}")
    check("250g of butter is not", large > 0.5, f"${large:.2f}")

    # Only the excess is charged, so cost is continuous across the threshold --
    # no cliff where 41g suddenly costs what 41g costs.
    just_over = pricing.cost_recipe("41g butter", 1, cat).total
    check("only the excess above the allowance is charged",
          0 < just_over < 0.10, f"${just_over:.3f}")
    check("cost is continuous across the allowance",
          abs(just_over - 0.0) < 0.10, f"${just_over:.3f}")

    # An unquantified staple cannot be excessive, so it is free rather than
    # unpriced -- otherwise coverage collapses over lines that cost nothing.
    c = pricing.cost_recipe("olive oil\nsalt\nblack pepper", 4, cat)
    check("staples with no amount are free, not unpriced",
          c.total == 0.0 and not c.unpriced, str([l.raw for l in c.unpriced]))

    # And a non-staple with no amount is still honestly unpriced.
    c = pricing.cost_recipe("beef mince", 4, cat)
    check("a non-staple with no amount is reported unpriced",
          len(c.unpriced) == 1, str([l.raw for l in c.unpriced]))


def _costing(check, cat):
    print("\ncost per portion")
    stew = ("1kg beef shin\n2 onions\n3 carrots\n500ml beef stock\n"
            "200g suet\n300g flour\n600g potatoes\n1 tsp salt")
    c = pricing.cost_recipe(stew, 4, cat)
    check("everything in a well-written recipe is priced",
          c.coverage == 1.0, f"{c.coverage:.0%}, missing "
          f"{[l.raw for l in c.unpriced]}")
    check("the total is plausible for a beef stew", 20 < c.total < 40,
          f"${c.total:.2f}")
    check("per portion is the total over the servings",
          abs(c.per_portion - c.total / 4) < 0.01)
    check("a fully-priced recipe is confident", c.confident)

    # Servings is the recipe's yield, so cost per portion is comparable between
    # recipes rather than moving with how many people are eating.
    four = pricing.cost_recipe(stew, 4, cat).per_portion
    eight = pricing.cost_recipe(stew, 8, cat).per_portion
    check("doubling the servings halves the cost per portion",
          abs(four - 2 * eight) < 0.01, f"{four:.2f} vs {eight:.2f}")

    # An expensive recipe must cost more than a cheap one, or the number is
    # decoration.
    posh = pricing.cost_recipe("1kg beef fillet\n500g puff pastry", 4, cat)
    plain = pricing.cost_recipe("500g pasta\n2 onions", 4, cat)
    check("beef fillet costs more than pasta and onions",
          posh.per_portion > plain.per_portion * 3,
          f"${posh.per_portion:.2f} vs ${plain.per_portion:.2f}")

    # Coverage has to fall when something cannot be priced, and the reason has
    # to be reported -- a cost silently missing its priciest line is the one
    # failure that looks exactly like a correct answer.
    c = pricing.cost_recipe("1kg beef shin\n3 widgets of unobtainium", 4, cat)
    check("an unknown ingredient lowers coverage", c.coverage < 1.0,
          f"{c.coverage:.0%}")
    check("...and is named", any("unobtainium" in l.raw for l in c.unpriced))
    check("...and the recipe is marked not confident", not c.confident)

    # Cheaper store, cheaper basket -- an obvious property that would break
    # silently if the store argument were ever ignored.
    nw = pricing.cost_recipe(stew, 4, cat, "New World", specials=False).total
    pns = pricing.cost_recipe(stew, 4, cat, "PAK'nSAVE", specials=False).total
    check("PAK'nSAVE is cheaper than New World for the same basket",
          pns < nw, f"${pns:.2f} vs ${nw:.2f}")


def _savings(check, cat):
    print("\nspecials elsewhere")
    week = ["1kg lamb shoulder\n2 onions", "500g beef mince\n1 onion",
            "6 chicken thighs\n2 lemons"]
    sav = pricing.find_savings(week, cat, "New World")
    check("specials at other stores are found", len(sav) >= 2, str(len(sav)))
    check("every saving is worth having",
          all(s.saved >= 0.50 for s in sav), str([s.saved for s in sav]))
    check("savings are sorted biggest first",
          sav == sorted(sav, key=lambda s: -s.saved))
    check("each names a store that is not the usual one",
          all(s.store != "New World" for s in sav))

    # Only advertised specials count. A rival being permanently a few cents
    # cheaper is not news, it is just a different shop -- and reporting it would
    # bury the real specials in noise.
    everyday = [s for s in sav
                if not (cat.items[s.item]["prices"][s.store].get("special"))]
    check("a permanently cheaper rival is not reported as a saving",
          not everyday, str([s.item for s in everyday]))

    # The threshold has to bite, or the report is a list of ten-cent errands.
    stingy = pricing.find_savings(week, cat, "New World", min_saving=100.0)
    check("a high threshold suppresses everything", not stingy, str(len(stingy)))

    # Staple allowances apply here too: the shop only needs what is chargeable.
    only_staples = pricing.find_savings(["1 tsp salt\n1 tbsp olive oil"], cat,
                                        "New World")
    check("a recipe of nothing but free staples has nothing to save",
          not only_staples, str([s.item for s in only_staples]))
