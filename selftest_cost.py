"""Checks for ingredient parsing, unit conversion, costing and specials."""

from __future__ import annotations

import re
from datetime import date

from mealplan import pricing, store


def run(check):
    cat = pricing.Catalogue(store.load_prices())
    if not cat.items:
        check("prices.json loads", False, "no items found")
        return

    _parsing(check)
    _stated_weight(check, cat)
    _name_before_notes(check, cat)
    _units(check, cat)
    _staples(check, cat)
    _costing(check, cat)
    _savings(check, cat)
    run_sources(check)


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


def _stated_weight(check, cat):
    """A cup of a solid, with the weight written beside it. Take the weight."""
    print("\na weight beats a cup of a solid")

    # Both spellings RecipeTin Eats uses, and it uses one or the other on
    # nearly every American measure it gives.
    for raw, want in [
            ("3 1/2 cups (350g) shredded Colby, Monterey Jack or Oaxaca cheese", 350.0),
            ("1 1/2 cups / 150g mozzarella cheese , shredded", 150.0),
            ("2 tbsp (30g) butter", 30.0),
            ("1 cup (8 oz) sour cream", 226.8)]:
        L = pricing.parse_line(raw)
        check(f"{raw[:34]!r} is {want:g} g",
              L.unit == "g" and abs(L.amount - want) < 0.5,
              f"{L.amount} {L.unit}")

    # A cup of a solid is a quantity plus how hard you pressed; the gram figure
    # has none of that in it. So this is not tidying, it is the difference
    # between a measurement and a guess, and it is why none of the four above
    # needs the catalogue to know a density.
    L = pricing.parse_line("3 1/2 cups (350g) shredded cheese")
    amt, why = cat.to_item_units(L, "cheddar")
    check("...so the line costs without a density being consulted",
          amt == 350.0, f"{amt} {why}")

    # The rule is one-way on purpose. Metric-then-imperial is the same amount
    # said twice for two audiences, the metric half is already in hand, and
    # there is nothing to prefer.
    for raw, want, unit in [("600g / 1.4lb beef chuck", 600.0, "g"),
                            ("1kg/2lb chicken thighs", 1.0, "kg"),
                            ("500ml / 2 cups chicken stock", 500.0, "ml")]:
        L = pricing.parse_line(raw)
        check(f"{raw!r} keeps its metric half", L.amount == want and L.unit == unit,
              f"{L.amount} {L.unit}")

    # A second *volume* is not worth swapping to either -- it says the same
    # thing -- and a count is left alone, or "1 onion (150g)" stops being an
    # onion and the shopping list starts asking for 150 g of onion.
    L = pricing.parse_line("1 cup (250ml) milk")
    check("a volume restated as a volume is left alone",
          L.amount == 1.0 and L.unit == "cup", f"{L.amount} {L.unit}")
    L = pricing.parse_line("1 onion (150g) , finely diced")
    check("a count with a weight beside it is still a count",
          L.amount == 1.0 and L.unit == "", f"{L.amount} {L.unit}")

    # Bracketed numbers that are not amounts must not be mistaken for one.
    L = pricing.parse_line("5 cups (tightly packed) kale leaves (1 small bunch, Note 3)")
    check("a bracketed note is not an amount",
          L.amount == 5.0 and L.unit == "cups", f"{L.amount} {L.unit}")

    # The invariant that ties this to the scaler, and the one that broke:
    # preferring the weight is only safe while the scaler is also scaling it.
    # Whatever a halved line *says* has to be what the costing *reads*.
    from mealplan import quantity
    for raw in ("3 1/2 cups (350g) shredded cheese",
                "1 1/2 cups / 150g mozzarella cheese",
                "1 tbsp / 15 g butter"):
        for factor in (0.5, 0.25, 2.0):
            half = quantity.scale_line(raw, factor).text
            whole = pricing.parse_line(raw).amount
            part = pricing.parse_line(half).amount
            check(f"{raw[:26]!r} x{factor} reads what it says",
                  abs(part - whole * factor) <= max(2.5, whole * factor * 0.05),
                  f"line says {half!r}, costing read {part}")


def _name_before_notes(check, cat):
    """A recipe writer's aside says what to use *instead*. It is not the line."""
    print("\nan ingredient is its name, not its footnote")

    # Every one of these was priced as the substitute before `key_for` existed,
    # silently and plausibly, because a substitution note is made of real
    # ingredient names.
    for raw, want in [
            ("3 1/2 tbsp (50g) unsalted butter (or 2 1/2 tbsp olive oil)", "butter"),
            ("10-12 eggs (reserve one yolk for glazing pastry)", "egg"),
            ("3/4 tsp cumin powder (sub coriander)", "ground spices"),
            ("1 tsp onion powder (sub garlic powder)", "onion"),
            ("1/3 cup white wine (sub more chicken stock)", "white wine"),
            ("1 tbsp cooking/kosher salt (for cooking pasta)", "salt"),
            # Both of these used to swap places with each other, and the fix
            # for the second one exposed that the first was priced as a bag of
            # chopped peanuts. Oil is oil whichever nut it came out of.
            ("2 tbsp peanut oil (or vegetable or canola oil)", "vegetable oil"),
            ("1 1/2 tbsp cooking oil (I use peanut oil)", "vegetable oil"),
            ("3 tbsp peanuts , roughly chopped", "peanut"),
            ("1/4 cup sour cream (or yoghurt)", "cream"),
            ("2 tbsp lime juice (sub rice vinegar when limes are expensive)", "lime")]:
        got = cat.key_for(pricing.parse_line(raw))
        check(f"{raw[:38]!r} is {want}", got == want, repr(got))

    # The fallback still has to work: the stripper can eat a name to nothing,
    # and then the notes are all there is.
    L = pricing.parse_line("10 cloves (or 1/4 tsp ground cloves)")
    check("a line whose name strips to nothing falls back to the raw text",
          not L.item and cat.key_for(L) == "whole spices",
          f"{L.item!r} {cat.key_for(L)!r}")

    # Preparation written *before* the ingredient describes what follows, and
    # cutting from it to the end of the line leaves nothing -- which handed 78
    # lines in a real library to their footnote instead of their name.
    for raw, want in [
            ("3 tbsp roughly chopped coriander leaves (sub green onion)", "fresh herbs"),
            ("2 tbsp grated parmesan", "parmesan"),
            ("1 1/2 tbsp finely minced garlic , about 5 - 6 cloves", "garlic"),
            ("400g can chopped tomatoes", "chopped tomatoes"),
            ("1 cup diced tomato , onion, avocado", "tomato")]:
        L = pricing.parse_line(raw)
        got = cat.key_for(L)
        check(f"{raw[:38]!r} is {want}", got == want, f"{got!r} from item {L.item!r}")

    # ...and after it, it still means everything from there on is noise.
    L = pricing.parse_line("2 onions , finely chopped and set aside")
    check("preparation after the ingredient is still cut",
          L.item == "onions", repr(L.item))

    # Where two aliases are the same length the answer must not depend on what
    # order the price file happens to list its items in.
    for raw, want in [("25 g guajillo chillies, dried (~4 pieces)", "dried chilli"),
                      ("45 g ancho chillies, dried", "dried chilli"),
                      ("1/2 tsp fennel powder (sub more garam masala)", "ground spices"),
                      ("2 tsp fennel seeds", "whole spices"),
                      ("1/4 medium fennel , chopped", "fennel"),
                      ("2 red chillies , deseeded", "chilli")]:
        got = cat.key_for(pricing.parse_line(raw))
        check(f"{raw[:34]!r} is {want}", got == want, repr(got))


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

    # A vague amount of a staple is the same case wearing a number. Nobody
    # measures a pinch and nobody buys one, so the only honest thing to say
    # about it is that it is under any allowance worth the name.
    c = pricing.cost_recipe("1 pinch cooking / kosher salt\na knob of butter\n"
                            "a good glug of olive oil", 4, cat)
    check("a pinch of a staple is free rather than unpriced",
          c.total == 0.0 and not c.unpriced, str([l.raw for l in c.unpriced]))

    # ...but only for staples. A handful of prawns is a real purchase, and
    # quietly calling it free is how a number ends up lower than the shop.
    c = pricing.cost_recipe("a handful of prawns", 4, cat)
    check("a vague amount of something you buy is still unpriced",
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
    # Its own catalogue rather than the shipped one. This used to read
    # prices.json, and that worked only while every price in it was invented:
    # the moment real ones arrived, set_price correctly dropped the invented
    # specials sitting beside them and this went to zero -- a test failing
    # because the data got better. What it is about is find_savings, so it
    # brings its own prices and stops caring what the file happens to hold.
    cat = pricing.Catalogue({
        "currency": "NZD",
        "stores": ["New World", "PAK'nSAVE", "Woolworths"],
        "items": {
            "lamb shoulder": {"aliases": [], "unit": "g", "prices": {
                "New World": {"price": 20.0, "per": 1000},
                "PAK'nSAVE": {"price": 18.0, "per": 1000,
                              "special": {"price": 13.0, "until": "2099-01-01"}}}},
            "beef mince": {"aliases": [], "unit": "g", "prices": {
                "New World": {"price": 20.0, "per": 1000},
                "Woolworths": {"price": 19.5, "per": 1000,
                               "special": {"price": 14.0, "until": "2099-01-01"}}}},
            "chicken thigh": {"aliases": [], "unit": "each", "g_per_unit": 110,
                              "prices": {
                                  "New World": {"price": 3.0, "per": 1},
                                  "PAK'nSAVE": {"price": 2.90, "per": 1}}},
            "onion": {"aliases": ["onions"], "unit": "each", "g_per_unit": 150,
                      "prices": {"New World": {"price": 0.50, "per": 1}}},
            "lemon": {"aliases": ["lemons"], "unit": "each", "g_per_unit": 100,
                      "prices": {"New World": {"price": 0.80, "per": 1}}},
        }})
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


def run_sources(check):
    """Price maintenance: receipts, manual updates, and staleness."""
    import copy
    from mealplan import pricesrc

    print("\nprice sources")
    cat = pricing.Catalogue(store.load_prices())

    # robots.txt gate fails closed. A permissive default is conventional and
    # wrong here: an unreachable site is exactly the one it is worst to hammer.
    check("an unreachable host is not assumed to allow fetching",
          not pricesrc.robots_allows("https://nonexistent.invalid/search"))

    receipt = "\n".join([
        "Beef Mince 500g                          $7.49",
        "Chicken Thighs 1kg          $8.99 /kg    $8.99",
        "Onions Brown Loose          $2.49 /kg    $1.24",
        "Butter 500g                              $6.49",
        "Whittakers Chocolate 250g                $5.49",
        "SUBTOTAL                                $32.69",
        "EFTPOS                                  $32.69",
    ])
    lines = pricesrc.parse_receipt(receipt, cat)
    by = {l.matched: l for l in lines if l.matched}

    check("totals and payment lines are not read as groceries",
          not any(re.search(r"subtotal|eftpos", l.name, re.I) for l in lines),
          str([l.name for l in lines]))
    check("a pack size gives price per pack",
          by["beef mince"].per == 500 and by["beef mince"].resolved_price == 7.49,
          f"{by['beef mince'].per} {by['beef mince'].resolved_price}")

    # The bug worth a test: a per-kg receipt price against an each-priced item.
    # Read naively it becomes the price of one, which for chicken thighs is nine
    # times too much -- and it lands in the file the costs are built from.
    thigh = by["chicken thigh"]
    check("a per-kg price converts to per-each via the unit weight",
          abs(thigh.resolved_price - 8.99 * 110 / 1000) < 0.01 and thigh.per == 1.0,
          f"${thigh.resolved_price:.2f} per {thigh.per}")
    check("...and is nowhere near the raw per-kg figure",
          thigh.resolved_price < 8.99 / 2, f"${thigh.resolved_price:.2f}")
    onion = by["onion"]
    check("a loose per-kg vegetable converts too",
          abs(onion.resolved_price - 2.49 * 150 / 1000) < 0.01,
          f"${onion.resolved_price:.2f}")

    check("an unknown product is skipped, not guessed",
          any("chocolate" in l.name.lower() and not l.matched for l in lines))

    # Ambiguity is refused rather than resolved by assumption.
    amb = pricesrc.parse_receipt("Prosciutto            $12.00 /kg   $6.00", cat)
    check("an ambiguous line is refused when no unit weight is known",
          all(l.resolved_price or l.note for l in amb),
          str([(l.matched, l.note) for l in amb]))

    # Writing back, on a copy so the shipped file is untouched by the tests.
    data = copy.deepcopy(store.load_prices())
    # Per-store now: the file carries a price per chain and only one of
    # them is ever shopped at. Ask about the one being written to.
    before = pricesrc.freshness(data, "PAK'nSAVE")
    # A value deliberately unlike anything that could already be in the file:
    # the first cut asserted against a fixed price, and a real --price run on
    # that same item made the test fail for the one reason it should not care
    # about.
    # Whichever item the file has *not* checked yet, rather than a named
    # one. This said "beef mince", which was safe until beef mince became
    # one of the prices actually fetched from a shop -- re-checking an
    # already checked price cannot raise the count, so the test failed for
    # the one reason its own comment says it should not care about.
    item = next((k for k, v in data["items"].items()
                 if ((v.get("prices") or {}).get("PAK'nSAVE") or {}).get("price")
                 and not ((v.get("prices") or {}).get("PAK'nSAVE") or {}).get("checked")),
                "beef mince")
    entry = data["items"][item]["prices"]["PAK'nSAVE"]
    novel = round(float(entry.get("price", 0)) + 3.33, 2)
    changed = pricesrc.set_price(data, item, "PAK'nSAVE", novel, 1000)
    check("setting a price reports that it changed", changed)
    check("setting the same price again reports no change",
          not pricesrc.set_price(data, item, "PAK'nSAVE", novel, 1000))
    entry = data["items"][item]["prices"]["PAK'nSAVE"]
    check("the new price is stored", entry["price"] == novel and entry["per"] == 1000)
    check("it is stamped as checked today",
          entry.get("checked") == date.today().isoformat(), str(entry.get("checked")))
    check("a verified price drops the invented special beside it",
          "special" not in entry, str(entry))

    after = pricesrc.freshness(data, "PAK'nSAVE")
    check("freshness goes up when a price is checked",
          after["checked"] >= before["checked"] + 1,
          f"{before['checked']} -> {after['checked']}")

    stale = pricesrc.stale_items(data)
    check("a just-checked price is not stale",
          not any(i == item and s == "PAK'nSAVE" for i, s, _ in stale))
    # Like for like: freshness counts one store, so this counts that store's
    # share of stale_items rather than every chain's.
    here = [r for r in stale if r[1] == "PAK'nSAVE"]
    check("the shipped estimates are all reported unchecked",
          len(here) == after["total"] - after["checked"],
          f"{len(here)} vs {after['total'] - after['checked']}")
    check("...and the whole-file count is still available",
          after["every"] >= after["total"], f"{after['every']} vs {after['total']}")

    applied = pricesrc.apply_receipt(data, lines, "PAK'nSAVE")
    check("applying a receipt writes only the resolved lines",
          len(applied) == sum(1 for l in lines
                              if l.matched and l.resolved_price and not l.note),
          f"{len(applied)} written")
