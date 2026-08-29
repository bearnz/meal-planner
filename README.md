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
python selftest.py       # 184 checks over scoring, planning, costing and the forecast
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

## Layout

```
run.py            entry point: web app, --sync, --plan, --scores
selftest.py       offline checks; also the synthetic library
selftest_extra.py checks for scaling, aversions, methods and seasons
selftest_cost.py  checks for parsing, units, staples, costing and specials
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
  planner.py      day/recipe costs and the assignment search
  service.py      the operations, with no Flask in them
  store.py        config, recipe cache, plan and history on disk
  web.py          Flask routes
templates/        plan, shopping list, preferences
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
- The shopping list groups ingredients by night rather than merging them.
  Paprika quantities are free text ("a splash", "2 tbsp, plus extra"), and
  adding those up means parsing them, which is worse done badly than not at all.
- History records what the plan says, so it doubles as a log of what you ate.
  Only days that have actually passed count towards the repeat penalty.
