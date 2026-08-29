# Meal planner

Picks seven dinners from your Paprika recipe library, one per night, using the
local weather forecast and where the day falls in the week.

Cold or wet nights get heartier food; warm dry ones get lighter food. Office
days get things you can actually put together after a commute; days at home can
take longer; the weekend gets the recipes worth spending an afternoon on.
Recipes from sources you like are favoured. Runs entirely on your own machine.

```
python run.py            # web app on http://127.0.0.1:5173
python run.py --sync     # pull recipes from Paprika into the local cache
python run.py --plan     # print this week's plan to the terminal
python run.py --scores   # every recipe with its heartiness and effort scores
python selftest.py       # 75 checks over scoring, planning and the forecast
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

On top of all that: no recipe twice in a week, a penalty on anything eaten in
the last few weeks, and a penalty on a week that leans on one protein.

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
| `week_starts_on` | `"today"`, or a weekday name to plan the *next* one of those. |
| `weights` | Relative pull of heartiness, effort, repeats, variety and source. |
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
seed_demo.py      fill the cache with that library to try the app
config.json       everything tunable
mealplan/
  paprika.py      read-only client for Paprika's cloud sync API
  weather.py      Open-Meteo forecast + the wanted-heartiness curve
  scoring.py      the keyword tables and the two scores
  planner.py      day/recipe costs and the assignment search
  service.py      the operations, with no Flask in them
  store.py        config, recipe cache, plan and history on disk
  web.py          Flask routes
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
