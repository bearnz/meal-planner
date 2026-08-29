# Meal planner

Picks seven dinners from your Paprika recipe library, one per night, using the
local weather forecast and where the day falls in the week.

Cold or wet nights get heartier food; warm dry ones get lighter food. Monday to
Thursday get things you can actually put together after work; the weekend gets
the recipes worth spending an afternoon on. Runs entirely on your own machine.

```
python run.py            # web app on http://127.0.0.1:5173
python run.py --sync     # pull recipes from Paprika into the local cache
python run.py --plan     # print this week's plan to the terminal
python run.py --scores   # every recipe with its heartiness and effort scores
python selftest.py       # 50 checks over scoring, planning and the forecast
```

## Setup

1. **Install the two dependencies.**

   ```bash
   python -m pip install -r requirements.txt
   ```

2. **Set your location.** Open `config.json` and replace the `location` value
   with your town or city — `"Bristol, GB"`, `"Wellington, NZ"`. It is geocoded
   once through Open-Meteo and the coordinates are cached back into the file.
   No API key, no account.

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
- **Wanted effort**, from `effort_target` and `effort_ceiling` in `config.json`.
  Weeknights have a low ceiling and going over it is expensive. Weekends have a
  high target and coming in *under* it is expensive — a trivial Saturday spends
  the one slot in the week that could have held the ambitious recipe.

There is also a **wall-clock rule**, which is separate from effort on purpose. A
three-hour braise is barely any work and still cannot happen on a Tuesday,
because it has to go in the oven at half past three. So weeknights cap total
elapsed time at `weeknight_max_minutes` (75 by default) — except for dishes that
can be started in the morning and left, which are exempt however long they run.
That is what lets an eight-hour slow cooker dish sit on a Wednesday while a
four-hour oven braise does not.

On top of that: no recipe twice in a week, a penalty on anything eaten in the
last few weeks, and a penalty on a week that leans on one protein.

The planner picks greedily, hardest day first, then improves by swapping days
and by substituting unused recipes until nothing helps.

## Tuning it

Everything worth changing is in `config.json`:

| Key | What it does |
| --- | --- |
| `location` | Town or city. Changing it re-geocodes on the next run. |
| `week_starts_on` | `"today"`, or a weekday name to plan the *next* one of those. |
| `weeknight_max_minutes` | Wall-clock cap for an attended weeknight cook. |
| `effort_ceiling` | Per day, the most effort that day will tolerate. |
| `effort_target` | Per day, the effort it is aiming for. |
| `weights` | Relative pull of heartiness, effort, repeats and variety. |
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
- The shopping list groups ingredients by night rather than merging them.
  Paprika quantities are free text ("a splash", "2 tbsp, plus extra"), and
  adding those up means parsing them, which is worse done badly than not at all.
- History records what the plan says, so it doubles as a log of what you ate.
  Only days that have actually passed count towards the repeat penalty.
