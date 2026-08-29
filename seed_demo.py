"""Fill the recipe cache with the self-test's invented library.

Lets you see the app working before touching your Paprika credentials, and
gives you something to look at while tuning the scoring tables. A real sync
overwrites all of it.

    python seed_demo.py
"""

from __future__ import annotations

from datetime import datetime

from selftest import LIBRARY
from mealplan import store

payload = {
    "fetched": datetime.now().isoformat(timespec="seconds") + "  (demo data)",
    "categories": {"dinner": "Dinner", "dessert": "Dessert"},
    "recipes": [r.to_json() for r in LIBRARY],
}
store.save_cache(payload)
store.save_plan({})
print(f"Seeded {len(payload['recipes'])} demo recipes into {store.CACHE_PATH}.")
print("Run: python run.py")
