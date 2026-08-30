# Meal planner

Picks seven dinners from your Paprika recipe library, one per night, using the
local weather forecast and where the day falls in the week.

Cold or wet nights get heartier food; warm dry ones get lighter food. Office
days get things you can actually put together after a commute; days at home can
take longer; the weekend gets the recipes worth spending an afternoon on.
Recipes from sources you like are favoured, produce in season is favoured,
and nothing you will not eat gets through. Runs entirely on your own machine.

```
python run.py            # web app on http://127.0.0.1:5173
python run.py --sync     # pull recipes from Paprika into the local cache
python run.py --plan     # print this week's plan to the terminal
python run.py --scores   # every recipe with its heartiness and effort scores
python run.py --stale    # which prices are real and which are estimates
python selftest.py       # 578 checks over scoring, planning, costing and the forecast
```

## Setup

1. **Install the two dependencies.**

   ```bash
   python -m pip install -r requirements.txt
   ```

2. **Check your location.** `config.json` ships pointed at Auckland
   (central Auckland) through explicit `latitude` / `longitude`.

   Coordinates rather than a name because **Open-Meteo's geocoder cannot
   resolve postcodes and does not say so** — asked for an Auckland postcode it confidently
   returns *Amsterdam*. `geocode()` now refuses a bare number outright rather
   than caching a forecast for the wrong hemisphere. To use a place name
   instead, delete `latitude` and `longitude` and set `location` to a suburb or
   city (`"Auckland, NZ"`); it is geocoded once and cached. No API key.

3. **Set your Paprika credentials.** Copy `.env.example` to `.env` and fill in
   the email and password you use for Paprika's cloud sync.

   ```bash
   cp .env.example .env
   ```

   `.env` is gitignored. Those details are read locally and sent only to
   `paprikaapp.com`, over the same sync endpoint the phone app uses. Cloud sync
   has to be switched on in Paprika on your phone or there is nothing to fetch.

4. **Pull your recipes**, then start it.

   ```bash
   python run.py --sync
   python run.py
   ```

Not ready to hand over credentials yet? `python seed_demo.py` fills the cache
with fourteen invented recipes so you can see the thing work first. A real sync
overwrites them.

## How a night gets chosen

Every recipe is scored on two axes, both 0 to 1:

- **Heartiness** — keyword tables over the name, categories, ingredients and
  method. Stews, braises, pastry, potatoes and cream push it up; salads,
  grilling, raw and citrus push it down. Long cooks push it up a little on
  their own.
- **Effort** — hands-on prep time, step count, ingredient count, and Paprika's
  own difficulty field when you have set it. Things that mean planning ahead
  (proving, marinating overnight) add to it; one-pan and slow-cooker dishes
  discount it.

Every day gets two wants:

- **Wanted heartiness**, from the forecast. A curve on the day's high (30°C
  wants 0.05, 3°C wants 0.90), pushed up by rain and by strong wind.
- **Wanted effort**, from what kind of evening it is.

### The three kinds of evening

Not weekday versus weekend — a work-from-home Wednesday has an afternoon in it
that an office Wednesday does not, and lumping the two together is what makes a
planner suggest a braise on a day you are on a train.

| | ceiling | target | wall clock |
| --- | --- | --- | --- |
| **office** | 0.30 | 0.12 | 50 min |
| **home** — a weekday not in the office | 0.62 | 0.32 | no cap |
| **weekend** | 1.0 | 0.78 | no cap |

Going *over* a day's ceiling is expensive and quadratic. Coming in *under* its
target costs `weight × gap^1.5`, and the weight is the whole priority ordering:
weekend 2.6, home 1.2, office 0.45. All three share one curve shape deliberately
— with different shapes they cross, and a crossing means home days start winning
the ambitious recipes away from the weekend at exactly the gaps that matter. The
self-test pins the ordering across the whole range for that reason.

The **wall clock** is a separate rule from effort. A three-hour braise is barely
any work and still cannot happen on an office day, because it has to go in the
oven at half past three and nobody is there. Dishes that can be started in the
morning and left — slow cooker, made the night before — are exempt however long
they run, which is what makes a slow cooker the ideal office-day dinner rather
than a borderline one. The cap lifts entirely at home: the braise that is
impossible on an office Wednesday is fine on a work-from-home one, and that is
the whole gain from splitting the two apart.

**Office days are set in the app, not the config file**, because they move week
to week. Each card has a chip — `💼 Office` / `🏡 Home` / `🏠 Weekend` — and
clicking it flips that day and re-plans. `office_days` in `config.json` only
seeds a *fresh* week; edits stick to the plan and survive a re-plan. The
re-plan is deliberately whole-week: marking Wednesday an office day changes what
every other night can hold too.

### Source preference

`preferred_sources` favours recipes from sites you actually cook from —
recipetineats.com out of the box. Matched on the domain from `source_url`, on a
bare domain in `source`, and on a human-typed site name with no URL at all
("RecipeTin Eats"), because Paprika stores all three and a preference that only
caught one of them would look broken at random.

It is a preference, not a filter: at the default weight of 0.45 it wins any
close call and beats a modestly better fit, while a real mismatch — a stew on a
hot night, a two-hour cook on an office day — still outweighs it. Raise
`weights.source` to lean harder; empty `preferred_sources` to switch it off.

### Seasonality

Produce at its peak is favoured, out-of-season produce is mildly penalised, and
a recipe of flour, stock and chicken is neither — it is genuinely not a seasonal
question, and scoring it either way would rank every pasta dish below every
salad in February for having no opinion.

**The hemisphere is the whole trick.** Every published seasonality calendar is
northern, and applying one unshifted in New Zealand recommends asparagus and
strawberries in the depths of winter — exactly wrong, twice a year, and
confidently. The table is authored in northern months and shifted six months
when `latitude` is negative. That is the entire correction and it happens in one
place, so nothing else has to know which hemisphere it is in.

In season is worth more than out of season is worth avoiding — a recipe built on
what is good right now is a real win, while one out-of-season lemon is just
slightly worse shopping. The month used is the month the *plan* opens in, not
the machine's today, because a plan generated on the 30th runs into the next one.

### Aversions, and cooking you would rather avoid

Two kinds of dislike, handled two ways, because they are two different
statements:

- **Ingredient aversions** are "we do not eat this", so the recipe leaves the
  library entirely. A weighted dislike would still put the thing on the table on
  a week when nothing else fits — exactly the outcome the setting exists to
  prevent. Matched over ingredients and method, not just the title: nothing in
  "Beef Wellington" says mushroom, and it is built on a duxelles.
- **Cooking methods** — deep and shallow frying, off by default — are a strong
  preference. Deep frying is not poison, it is a Tuesday you would rather not
  have, so it is a heavy penalty and an otherwise-perfect recipe can still win.
  Shallow frying almost never says so in the title, so detection reads the method
  for an oil depth, a frying temperature, or a pan being filled.

Saving shows what changed and offers **Undo** — an aversion removes recipes
outright, and the only previous sign of that was a count in the header moving.

Both are set on **the preferences page**, which lists every candidate with the
number of recipes it would affect. That count is the point: "exclude mushrooms"
is a different decision when it costs four recipes than when it costs forty, and
before you click is the only honest place to say so. A free-text field covers
anything the curated list misses. Saving re-plans the week, since the
preferences change which recipes exist.

On top of all that: no recipe twice in a week, a penalty on anything eaten in
the last few weeks, and a penalty on a week that leans on one protein.

### What it costs

Every night shows a **cost per portion**, and the week shows an average plus
what it costs to feed the household. Those are two different numbers on purpose:
cost per portion is what compares two recipes, and it is divided by the recipe's
own yield rather than by how many people are eating, so the same dish does not
look cheaper for a smaller family. The household figure is the one you budget
with.

> **The prices are estimates.** `prices.json` ships with hand-written, plausible
> Auckland supermarket prices — including the "specials" — so the costing engine
> has something to work on. Nothing here fetches live prices. Replace them with
> what you actually pay before trusting a single figure; the page says so too.

**Pantry staples are assumed to be in the cupboard.** Salt, oil, butter, flour,
dried herbs and ground spices each carry a free allowance, and only the *excess*
above it is charged. The excess and not the whole amount, so cost stays
continuous: charging the full 250g the moment butter crosses 40g would make a
recipe using 41g cost four dollars more than one using 39g, which is a cliff
nobody could explain from the page. A staple with no amount written at all
("olive oil" on its own line) cannot be excessive, so it is free.

**Anything that cannot be priced is said out loud.** A line the parser cannot
read, an ingredient not in the catalogue, a pack size it has no weight for —
each is listed, and the night's cost is marked *approx* with its coverage. A
cost estimate quietly missing its most expensive line is the one failure that
looks exactly like a correct answer.

**A stated weight beats a cup of a solid.** RecipeTin Eats writes "3 1/2 cups
(350g) shredded cheese" and "1 1/2 cups / 150g mozzarella", and where a line
gives both, the weight is what gets costed. A cup of a solid is not a quantity,
it is a quantity plus how hard you pressed — shredded cheese runs 90 to 115g a
cup depending on the grater, packed leaves are worse — and the gram figure has
none of that in it. It also spares the catalogue a density for every shredded
and leafy thing in it, a number that only ever existed to undo the conversion.

Only that direction. "600g / 1.4lb beef chuck" is the same amount said twice for
two audiences; the metric half is already in hand and there is nothing to
prefer. And both halves get scaled together — see below.

**An ingredient is its name, not its footnote.** Recipe writers put substitutes
in brackets, and a substitution note is made of real ingredient names, so a
matcher reading the whole line finds the wrong one. "3 1/2 tbsp (50g) unsalted
butter (or 2 1/2 tbsp olive oil)" was priced as olive oil, "10-12 eggs (reserve
one yolk for glazing pastry)" as puff pastry, and "cumin powder (sub coriander)"
as a bunch of fresh herbs. The name comes first now and the notes are the
fallback, which they have to remain: the prep-stripper can eat a name down to
nothing, and then the notes are all there is.

Between them these two, plus a round of aliases and densities, moved the library
from 49 recipes costed plainly to 74, and from 24 with no figure at all to 11.

### Getting real prices in

The shipped prices are estimates. Two ways to replace them with what you were
actually charged, both stamping the date so the page can show how much of a cost
is real:

```bash
python run.py --price "beef mince" 15.49 --per 1000    # one item, at the checkout
python run.py --receipt receipt.txt                    # preview a whole shop
python run.py --receipt receipt.txt --apply            # write it
python run.py --stale                                  # what still needs checking
```

The receipt reader handles the shapes NZ receipts come in — a pack size in the
description (`Beef Mince 500g  $7.49`), a unit price column
(`Chicken Thighs  $8.99 /kg  $8.99`), and loose weighed goods. It previews by
default and writes only on `--apply`, because a parser that silently rewrote the
price list from a misread line would corrupt the one file every cost depends on.
Anything it cannot resolve is skipped and named rather than guessed.

One conversion in there is worth knowing about, because getting it wrong is both
easy and enormous: a receipt line priced **per kilo** against an item the
catalogue counts **each** has to be converted through the unit weight.
`Chicken Thighs $8.99/kg` read naively becomes $8.99 *per thigh* — nine times
the real figure, written straight into the price list. It converts, or it
refuses; it never assumes.

**There is no scraper, deliberately.** Foodstuffs (PAK'nSAVE and New World, one
platform) publish a robots.txt disallowing exactly the paths this would need:

```
Disallow: /search          Disallow: /shop/search
Disallow: /shop/offer      Disallow: /product-promotions
```

Product lookup by name and the current promotions — both named, in a
machine-readable instruction from the operator about automated access. So this
project does not request them. `pricesrc.robots_allows()` is the gate any future
fetcher has to pass, and it **fails closed**: an unreachable robots.txt returns
False, because a site that is timing out is the worst possible one to hammer and
"I could not read the rules" is not a reason to assume there are none.

### One shop, and when to leave it

Everything is costed at `primary_store`. Separately, the planner checks each
ingredient for an **advertised special** at another store and reports the ones
worth the detour — biggest first, with what it saves and when it ends.

This is an exception list, not a cheapest-basket optimiser, and that is the
design rather than a limitation. A solver that splits a week's shopping across
three supermarkets to save four dollars answers a question nobody asked: it
costs two more car trips than it saves. `min_saving` (default $0.50) is the
floor, so the report never becomes a list of ten-cent errands.

Only advertised specials count. A rival being permanently a few cents cheaper is
not news — it is just a different shop — and reporting it would bury the real
specials in noise.

### Scaling to the household

`household_size` — also on the preferences page — scales every recipe on the
shopping list. The leading quantity is multiplied and every word is left alone,
which is a far smaller problem than *merging* a list and is why this half is now
done and the merge still is not: "400g beef mince" at 0.5 is "200g beef mince",
and nothing else about the line has to be understood.

Rounding is per unit, because the sensible answer differs by three orders of
magnitude. Grams and millilitres go to whole numbers — nobody weighs out 187.5g.
Kilos and litres keep two decimals — rounding 0.75kg the same way gives 1kg, a
third more lamb. Everything else is a count, rounds to a quarter and comes back
as a fraction, because half an onion is a real instruction.

**Where a line says the amount twice, both halves scale.** Half of "3 1/2 cups
(350g) shredded cheese" is "1 3/4 cups (175g)", not "1 3/4 cups (350g)" — that
line contradicts itself on a page read standing in a shop. It stopped being
cosmetic when the costing started preferring the stated weight: preferring an
amount and then not scaling it puts a whole batch of cheese in a quarter batch
of tacos. The pattern is anchored to just after the unit, which is where recipe
writers put it and is what keeps "(Note 6)", "(1 small bunch)" and "(~5cm/2″
piece)" out of it. One definition, in `quantity.restatement`, used by the scaler
and the costing both — they have to agree about what a restatement is.

Anything not confidently parsed is **passed through untouched and flagged**:
"salt and pepper to taste" and "a pinch of saffron" are left alone, and the page
says how many lines it could not scale. A line silently halved wrongly cannot be
recovered from; one flagged as unscaled can be checked in two seconds.

The planner picks greedily, hardest day first, then improves by swapping days
and by substituting unused recipes until nothing helps.

## Tuning it

Everything worth changing is in `config.json`:

| Key | What it does |
| --- | --- |
| `latitude` / `longitude` | Exact coordinates. Set these and `location` is only a label. |
| `location` | Suburb or city, geocoded when no coordinates are set. Never a postcode. |
| `household_size` | Shows a scaling factor against each recipe's servings. |
| `office_days` | Weekday names seeding a *fresh* week. Day to day, use the chips on the page. |
| `day_types` | Ceiling, target and wall-clock cap for office / home / weekend. |
| `preferred_sources` | Domains or site names to favour. Empty to switch off. |
| `primary_store` | The one supermarket you shop at. Everything is costed here. |
| `min_saving` | How much a special must save before it is worth mentioning. |
| `avoid_ingredients` | Hard exclusions. Set these on the preferences page. |
| `avoid_methods` | Cooking to avoid (heavy penalty, not a ban). Same page. |
| `week_starts_on` | `"today"`, or a weekday name to plan the *next* one of those. |
| `weights` | Relative pull of heartiness, effort, repeats, variety, source, season and avoided methods. |
| `repeat_memory_weeks` | How long before something can come round again. |
| `exclude_categories` | Paprika categories that are not dinner. |
| `heartiness_overrides` | `{"Recipe name": 0.9}` when the scoring is wrong. |
| `effort_overrides` | Same, for effort. |

A recipe is only dropped by `exclude_categories` if *every* category it carries
is excluded, so something filed under both "Dinner" and "Baking" is still
dinner. Recipes with no categories at all are always kept.

The keyword tables live at the top of `mealplan/scoring.py` and are meant to be
edited — the weights are nudges, and several small ones agreeing is what moves a
score. `python run.py --scores` prints the whole library sorted by heartiness,
which is the fastest way to see whether a change did what you wanted.

## Prices from grocer.nz

Shelf prices for one store, pulled from [grocer.nz](https://grocer.nz). Needs
`pip install pyarrow`, which nothing else here does.

```bash
python run.py --grocer-stores "royal oak"                  # find a store id
python run.py --grocer-find "beef chuck" --category "Meat & Seafood"
python run.py --grocer-pin "beef chuck" 1672
python run.py --grocer-refresh                             # the weekly job
```

**Where you shop is config.json's to say, and only config.json's.**
`primary_store` there picks which chain's prices the week is costed against;
`prices.json` holds a price per chain and no opinion about which is yours. It
used to carry its own copy, the two drifted apart, and anything that asked the
file got a confident answer about a shop nobody used.

**Which shop.** Prices are per store — Royal Oak and Mt Albert are both
PAK'nSAVE and neither charges the same. Pick one on the **Preferences page**,
or set `grocer.store_id` in config.json. Changing it moves two settings
together: which shelf is read, and which column of `prices.json` the week is
costed against, so picking a New World store re-costs the week against New
World. Only chains the price file has a column for are offered — a Fresh Choice
store would fetch perfectly and then be costed against nothing.

Changing store does **not** re-fetch; the page says so and points at
`--grocer-refresh`. The refresh knows it has moved and ignores the old ETag,
because a 304 there would leave the new shop's plan costed at the old shop's
prices.

The store list ships in `data/grocer_stores.json` (465 stores) so the picker
works on a fresh clone. `--grocer-stores --refresh` re-downloads it, needs
`duckdb`, and is worth running about as often as supermarkets open.

**Find, then pin, then refresh** — and the split is the point. Searching their
catalogue for "beef chuck" returns the right steak first and then four kinds of
cat food; searching for "beef cheeks" returns beef *rump*, confidently. So a
person picks, the choice is stored as a product id, and the weekly refresh reads
prices **by id** with no matching in it at all. Nothing can quietly drift onto
the wrong product three months later.

**The weekly cost to them is one request of 60 KB.** grocer.nz publishes one
Parquet file per store holding every current price — about 10,000 products —
so a full refresh is a single download, and an unchanged one is a 304 with no
body. `--grocer-find` is the only thing that makes extra requests, and only when
you are adding an ingredient.

**What the page shows.** Two different doubts, kept apart, because one mark
cannot say both:

- **approx** &mdash; part of the recipe has no price at all (low coverage).
- **guess** / **real** &mdash; the prices it *did* use were shipped estimates,
  or were actually fetched from a shop.

A night can be fully covered and entirely invented, which is the case the second
mark exists for. The week's header leads with **how much of this week's cost is
real**, weighted by money rather than by line or by night: one checked cut of
beef is most of a dinner, and nineteen guessed spices are not. The secondary count is
per **store**, not per file: prices.json holds a price per item per chain, and
you shop at one. Counting all three made the bar a number that could not be
moved &mdash; 123 of 209 entries belong to shops `--grocer-refresh` will never
fetch, so it had a hard ceiling of 41% that had nothing to do with effort.

**On being a good guest.** Their robots.txt allows this (`Allow: /`) and their
terms prohibit only unauthorised access and disrupting the service. That makes
it permitted, not free: this is a small New Zealand operation and the price
database *is* their product, where for a supermarket it is a by-product. So the
User-Agent says what this is and that it is personal and non-commercial,
`robots_allows()` gates every request and fails closed, `MIN_REFRESH_HOURS`
refuses to re-fetch more often than daily, and the attribution is written into
`prices.json` and shown on the plan page. If you are going to use this beyond
one household, ask them first: `info@grocer.nz`.

**The cupboard is not the shopping list.** Oyster sauce, rice, herbs, spices
and the rest of the pantry are marked `staple` in prices.json, and the shopping
list folds them into a *"from the pantry &mdash; check you have these"* block
rather than putting them among the things to buy. Listing soy sauce every week
is how a list stops being read.

Folded away rather than deleted, deliberately: staples do run out, and a recipe
wanting three tablespoons of a bottle you do not have is a failed dinner. One
glance, one line, no scrolling past it every week.

It is the **same flag the costing already uses** to charge only the excess above
`free_allowance`, so the two cannot drift apart &mdash; anything the price list
treats as free is the same thing the list treats as already owned. The allowance
is the threshold in both: two kilos of rice for a paella is a purchase, 150g is
"there is rice in the cupboard".

**Except the ones that go off.** A second flag, `perishable`, splits that
cupboard in half. Star anise keeps for a year and butter, garlic, ginger, fresh
chillies and a bunch of coriander are gone by Thursday &mdash; they are owned in
exactly the same sense, and folding them away is what would make the list wrong
rather than merely long. So a perishable staple stays on the list proper,
marked with a dot, and you tick it off if you already have it. `perishable` is a
split *within* `staple` rather than a third kind of item: everything marked
perishable is also a staple, is still free to the costing under its allowance,
and is still a purchase above it. A stick of butter is shopping; a knob of it is
not.

Putting them where they can be seen is also what found three lines of dried
chillies matching the *fresh* chilli entry, and a cup of chilli soaking water
being scored as an ingredient to buy. Both had been wrong since the catalogue
was written and both were invisible while they sat in a collapsed block.

**Two ways to read the list.** *By night* is the default and is for cooking: one
card per dinner, in recipe order, quantities untouched. *By item* is for
shopping &mdash; one entry per ingredient with the week added up, because you do
not walk to the vegetables three times because three nights want an onion.

The merge is careful about what it refuses to do. Two lines merge when they
resolve to the same **catalogue key**, so "2 garlic cloves" and "1 tbsp minced
garlic" land together on the alias table's authority rather than on a guess; an
ingredient the catalogue has never heard of merges only with an identical one.
Amounts are added only when **every** contributing line converts to the same
unit &mdash; one "handful of parsley" among three weighed ones makes the sum a
lie, so the total is dropped and the lines are shown as written instead. And
every entry keeps its parts, naming which nights wanted it and what each asked
for, because a number with no working behind it is not checkable while you are
holding the bag.

**Counts round up one recipe at a time, and only then add.** Two dinners each
wanting half an onion are two onions: you cut an onion for a recipe and the
other half goes soft, so the half Tuesday did not use is not the half Thursday
gets. Rounding the week's total instead is the version that looks tidier and
sends you home an onion short. It is a *recipe* at a time rather than a line at
a time, so a dish wanting half an onion in the marinade and half in the pan
still buys one. Weights are untouched -- rounding those up buys a kilo of mince
to cover 900g, which is a different and worse habit.

**Counted things are named in the units people say.** `unit_label` in
prices.json turns "13 garlic" into "13 cloves garlic", and celery into stalks,
broccoli into heads, fennel into bulbs. Nothing derives these, because there is
no rule. `pack_of` and `pack_label` add what the thing is *sold* in where that
differs -- garlic reads "13 cloves garlic &mdash; 2 bulbs", since thirteen
cloves is the honest amount and is not a thing you can pick up. Garlic is
deliberately vague for the same reason it is worth doing: being a clove out
does not matter, which is exactly why quoting it to the gram was worse than
useless. And `plural` is written down only where "+s" is wrong, which so far is
cod and chillies.

A staple is judged one night at a time, deliberately: seven teaspoons of olive
oil is not a bottle of olive oil to buy, and re-testing the weekly sum against a
per-recipe allowance would put the whole cupboard back on the list.

The by-item order is **alphabetical, and that is a placeholder**. The order worth
having is the order of the shelves, so the list is walked once from the door to
the checkout &mdash; and that is a property of a store rather than of the data, so
it needs a layout per shop before it can be anything else. Everything that sorts
the merged list goes through `service._shelf_order`, so there is one place to
change.

**Ticks are remembered.** The list is read standing in a shop on a phone whose
screen locks every thirty seconds, so a tick that does not survive a reload is a
decoration. They live in `localStorage`, scoped to the plan they were made
against, so re-planning starts a clean list rather than showing last week's
shopping crossed off this week's dinners.

**What is left to map.** `python run.py --missing` splits the job three ways,
because the three piles need different actions:

- **Already in the list, still a guess** &mdash; one `--grocer-find` and one
  `--grocer-pin` each, ranked by how much of the library's cost each one makes
  real. Work top-down: bacon is $34 across 91 dinners, a lone spice is cents.
- **Not in the list at all** &mdash; the same two commands, except the pin
  creates the entry. Ranked by how many recipes mention it, with a `*` on the
  ones that block a whole recipe on their own.
- **Everything else** &mdash; lines with no amount on them, or a unit with no
  known density. Those want a `g_per_ml` or a clearer recipe line, not a price,
  and counting them with the others makes the job look bigger than it is.

**Buy it whole and prepare it yourself.** A recipe asking for shredded Colby
or boneless thighs is asking for a *prepared* form, and the shop mostly sells
the whole thing. So a pin can say what you really buy, and how much of it
survives the preparation:

```bash
python run.py --grocer-pin "cheddar" 8911 --note "block, grated at home"
python run.py --grocer-pin "chicken thigh" 26814 --yield 0.68     --note "bought bone-in and boned out"
```

`--yield` is the share of what you buy that ends up in the pot, and leaving it
out is not a rounding error. Bone-in thigh is **$8.89/kg** against **$15.99**
for fillets, but a kilo of it is only ~680 g of meat: the real cost is
**$13.07/kg**. Priced without the yield it reads $8.89 &mdash; 44% cheap, and
buying bone-in looks like a free lunch instead of the modest 18% saving it is.
Grating your own cheese has a yield of 1.0 and needs only the note.

The **shopping list** shows what to put in the trolley under any line it
applies to &mdash; *"buy Pams Colby Cheese &mdash; block, grated at home"* &mdash;
because that is the only page anyone reads while standing in the shop, and a
price worked out from a block has to say so or the plan was costed against a
purchase nobody made.

Still to come: showing both options side by side, so the saving from breaking
it down yourself is visible rather than assumed.

**The one number to be suspicious of** is the pack size. A blank size means the
product is sold loose and priced *per kilogram*; anything else is the price of
that pack. Get it backwards and a $28.99/kg chuck steak becomes $28.99 for the
whole dinner. `--grocer-pin` refuses to pin an id it has not seen the pack size
for, rather than assuming.

**Club prices are deliberately not taken.** They need a card this project knows
nothing about, and quoting one makes the week look cheaper than the till will be.
Specials are taken, because anyone can have them.

## Where this is up to

Running against a real 111-recipe Paprika library. To pick it up:

```bash
python run.py --sync                      # pull your library (needs .env)
python run.py                             # or seed_demo.py first, to try it dry
python selftest.py                        # 578 checks, all passing
```

**What the first real library changed.** Every one of these passed the whole
test suite and still produced a visibly wrong answer, which is the failure mode
worth remembering: the synthetic library is clean, and a real one is not.
`selftest_library.py` now holds a check for each.

- **The sync client was pointed at the wrong API version.** v2 refuses HTTP
  Basic outright and its login endpoint answers `Unrecognized client.` — it
  validates the account, then rejects the client, which is Paprika gating v2 to
  their own apps. v1 serves the same library to an honest client. The one thing
  not done about this is the obvious one: no spoofed User-Agent. If v1 is
  retired, the route is Paprika's own File > Export.
- **A re-sync re-downloaded everything.** The recipe index carries a per-recipe
  hash, so it now fetches only what changed — 111 requests became 0, and about
  two minutes became one second. Trashed recipes stay *in* the cache for the
  same reason: dropped, they had no hash to compare and were re-fetched forever.
- **Section headings were counted as ingredients and steps.** RecipeTin Eats
  writes `SAUCE`, `TO SERVE:`, `SATAY SEASONING` — 125 such lines here, all
  headings, none an ingredient. A 30-minute gyros scored 0.97 of 1.0 for effort
  and was being served on Saturdays.
- **Dual metric/imperial amounts hid the meat.** `600g / 1.4lb beef chuck` left
  the ingredient named `/ 1.4lb beef chuck`, so every cut of meat in the library
  matched nothing while the spices beside it priced perfectly — which makes a
  recipe look *cheap* rather than unpriced.
- **A substitution note was priced instead of the ingredient.** Brackets on a
  RecipeTin Eats line hold what to use *instead*, and a note is made of real
  ingredient names, so a matcher reading the whole line finds the wrong one:
  butter priced as olive oil, eggs as puff pastry, cumin powder as fresh herbs,
  salt as pasta. All plausible, all cheaper or dearer by a little, none of it
  visible from the page. See `Catalogue.key_for`.
- **A cup of a solid was being converted with a density nobody had.** Half the
  American measures in the library carry the weight beside them --
  `3 1/2 cups (350g)` -- and the parser was reading the cups and throwing the
  grams away, then refusing the line for want of a density. Two of the seven
  nights in the current plan had no price for exactly this reason. The weight
  is taken now, and the scaler had to learn to scale it: preferring an amount
  and then leaving it at full batch is a worse bug than the one it fixed.
- **`1 large onion` was refused as vague.** Size words are descriptions of
  countable things, not vague amounts, and onion was the single most common
  unpriced ingredient — 19 recipes.
- **The category rules were built for the wrong shape of library.** This one
  files a recipe under everything it is *like* (cuisine, protein, method, vibe)
  rather than what it *is*, so the old subset test never fired and the planner
  served Pizza Dough and Tzatziki Sauce as dinners. See `is_dinner` for the
  three settings and why a veto is needed alongside the subset rule.
- **The library already knew what this file spends 400 lines inferring.** Its
  `Vibe` tree — Easy, Effort, Heavy, Light — is the owner stating both scoring
  axes by hand, and `difficulty` is empty on all 99 recipes. Those tags now
  anchor the scores. They disagreed: a gyros tagged `Easy` was the highest-effort
  recipe in the library.
- **A 2 kg picanha costed $0.00 a portion** and the page printed it with
  "approx" beside it. There are now three states, not two — see `RecipeCost.usable`.
- **Re-planning took fifteen seconds.** Not the library's doing, but only a
  real one made it visible. The planner's local search asks each candidate for
  its variety tags once per swap it tries — about 15,000 asks for one week —
  and each ask re-ran fifty regexes over the whole text of a recipe, directions
  included. Half a million full-text scans per re-plan. Tags are a pure function
  of a recipe that cannot change mid-plan, so they are now derived once. The
  other second was a live Open-Meteo call on every button press, for a daily
  forecast that does not change between two clicks; it is cached in process for
  fifteen minutes. A re-plan is 0.7 s, and the first one of a session is 1.9 s.
  `selftest_speed.py` counts the work rather than timing it — a stopwatch in a
  test suite is a coin toss.

**Not yet done, in the order it probably matters:**

1. **Every price is an estimate.** 16 of 108 verified at PAK'nSAVE, and 11 of
   111 recipes still cannot be costed at all -- down from 24, after a pass that
   taught the parser to prefer a stated weight and the matcher to read a name
   before its footnote. `python run.py --missing` ranks what to add by how
   many recipes each unlocks — it is the fastest way to spend an hour on this.
   `--price` and `--receipt` are the way in; `--stale` lists what is still a
   shipped guess. Until that share climbs the money figures compare recipes
   against each other; they do not budget.

2. **`day_types` has still only been judged against the demo library.** The
   effort ceilings and targets are now being applied to a real spread of 91
   recipes and have not been retuned since. `python run.py --scores` prints the
   distribution.

3. **Live price fetching is deliberately not built.** Foodstuffs' robots.txt
   disallows the search and promotions paths this would need — see the note at
   the top of `mealplan/pricesrc.py`. If a legitimate source turns up (a
   commercial API, or your own order-history export), `robots_allows()` is the
   gate to put it behind. An order-history importer is the obvious next move
   and would reuse most of `parse_receipt`.

4. ~~The preferences checkboxes save with no confirmation.~~ **Done.** A save
   now leaves a banner saying what changed and what it cost, with Undo and
   Dismiss. The snapshot is on disk, so an accidental save is still reversible
   after a restart. Any other action supersedes the banner, and a save that
   changes nothing produces none.

5. ~~`prices.json` reformats itself on the first write.~~ Done — it was
   expanded once, on its own commit, and is stable now. Worth a compact
   serialiser only if the file goes back to being edited by hand.

6. **Smaller things:** the seasonality table is ~65 items and a starting point
   rather than an authority; some recipes state servings in units of their own
   ("Servings 22" for a taco recipe), which divides that recipe's cost per
   portion by 22 — the number is shown rather than corrected, because a party
   recipe that genuinely serves 22 looks identical; a dozen recipes carry their
   directions twice (`ABBREVIATED RECIPE` then `FULL RECIPE`), which inflates
   the step count; and the shopping list merges quantities across recipes only
   where the units agree, which leaves a tail of entries shown as written.

## Layout

```
run.py            entry point: web app, --sync, --plan, --scores
selftest.py       offline checks; also the synthetic library
selftest_extra.py checks for scaling, aversions, methods and seasons
selftest_cost.py  checks for parsing, units, staples, costing and specials
selftest_undo.py  checks for the preferences save/undo path, via the routes
selftest_library.py checks for the shapes a real Paprika library turned out to have
selftest_speed.py checks that a re-plan does not redo work it already did
selftest_grocer.py checks the grocer.nz price source and the store picker
prices.json       the price catalogue - ESTIMATES, see the notes inside
seed_demo.py      fill the cache with that library to try the app
config.json       everything tunable
mealplan/
  paprika.py      read-only client for Paprika's cloud sync API
  weather.py      Open-Meteo forecast + the wanted-heartiness curve
  scoring.py      the keyword tables and the two scores
  seasons.py      produce calendar + the hemisphere shift
  preferences.py  aversions, avoided methods, and the page's candidate lists
  quantity.py     parse and scale an ingredient line
  pricing.py      ingredient -> item -> cost, staples, specials
  pricesrc.py     receipts, manual price updates, robots.txt gate
  planner.py      day/recipe costs and the assignment search
  service.py      the operations, with no Flask in them
  store.py        config, recipe cache, plan and history on disk
  web.py          Flask routes
templates/        plan, shopping list, preferences
static/           style.css, shop.js (remembers which lines are ticked off)
data/             cache, current plan, history (all gitignored)
```

## Notes

- Paprika's sync API is not documented or supported. Nothing here writes, so the
  worst case is a fetch that stops working. If the response shape ever changes,
  `paprika.py` reports what it got rather than guessing.
- The plan opens on the *location's* today, not the machine's. If your computer's
  clock is in another timezone, the week is still the week where you live.
- Serving sizes show as a scaling factor (`serves 4 (x0.5)`) rather than being
  rewritten. Paprika quantities are free text, and "0.5 splashes of oil" is
  worse than doing the arithmetic yourself.
- The shopping list groups ingredients by night by default. The *by item* view
  merges them where it honestly can and says so where it cannot: Paprika
  quantities are free text ("a splash", "2 tbsp, plus extra"), so a total is
  offered only when every line behind it converted to the same unit.
- History records what the plan says, so it doubles as a log of what you ate.
  Only days that have actually passed count towards the repeat penalty.
