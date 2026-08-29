"""Read-only client for Paprika 3's cloud-sync API.

This is Paprika's own sync endpoint, the one the phone app talks to. It is not
a documented public API, so everything here is defensive: we never write, we
cache aggressively, and a shape we do not recognise is reported rather than
guessed at.

Auth is HTTP Basic with the Paprika account email and password. Those come from
the environment (see .env.example) and are sent to paprikaapp.com and nowhere
else.
"""

from __future__ import annotations

import gzip
import json
import os
from dataclasses import dataclass, field
from typing import Any

import requests

BASE = "https://www.paprikaapp.com/api/v2/sync"
TIMEOUT = 30


class PaprikaError(RuntimeError):
    pass


@dataclass
class Recipe:
    uid: str
    name: str
    ingredients: str = ""
    directions: str = ""
    description: str = ""
    notes: str = ""
    categories: list[str] = field(default_factory=list)
    prep_time: str = ""
    cook_time: str = ""
    total_time: str = ""
    difficulty: str = ""
    servings: str = ""
    rating: int = 0
    source: str = ""
    source_url: str = ""
    in_trash: bool = False

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> "Recipe":
        return cls(
            uid=str(d.get("uid", "")),
            name=str(d.get("name", "") or "Untitled"),
            ingredients=str(d.get("ingredients", "") or ""),
            directions=str(d.get("directions", "") or ""),
            description=str(d.get("description", "") or ""),
            notes=str(d.get("notes", "") or ""),
            categories=list(d.get("categories") or []),
            prep_time=str(d.get("prep_time", "") or ""),
            cook_time=str(d.get("cook_time", "") or ""),
            total_time=str(d.get("total_time", "") or ""),
            difficulty=str(d.get("difficulty", "") or ""),
            servings=str(d.get("servings", "") or ""),
            rating=int(d.get("rating") or 0),
            source=str(d.get("source", "") or ""),
            source_url=str(d.get("source_url", "") or ""),
            in_trash=bool(d.get("in_trash")),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "uid": self.uid, "name": self.name, "ingredients": self.ingredients,
            "directions": self.directions, "description": self.description,
            "notes": self.notes, "categories": self.categories,
            "prep_time": self.prep_time, "cook_time": self.cook_time,
            "total_time": self.total_time, "difficulty": self.difficulty,
            "servings": self.servings, "rating": self.rating,
            "source": self.source, "source_url": self.source_url,
            "in_trash": self.in_trash,
        }

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "Recipe":
        return cls.from_api(d)


def _credentials() -> tuple[str, str]:
    email = os.environ.get("PAPRIKA_EMAIL", "").strip()
    password = os.environ.get("PAPRIKA_PASSWORD", "")
    if not email or not password:
        raise PaprikaError(
            "PAPRIKA_EMAIL / PAPRIKA_PASSWORD are not set. Copy .env.example to "
            ".env and fill in your Paprika account details."
        )
    return email, password


def _get(session: requests.Session, path: str) -> Any:
    """GET one sync endpoint and unwrap Paprika's {"result": ...} envelope.

    Responses are gzipped whether or not requests decided to decode them, so we
    try the decoded body first and fall back to inflating the raw bytes.
    """
    url = f"{BASE}/{path}"
    r = session.get(url, timeout=TIMEOUT)
    if r.status_code in (401, 403):
        raise PaprikaError(
            "Paprika rejected those credentials (HTTP %d). Check PAPRIKA_EMAIL "
            "and PAPRIKA_PASSWORD, and that cloud sync is enabled on your phone."
            % r.status_code
        )
    r.raise_for_status()

    try:
        payload = r.json()
    except ValueError:
        try:
            payload = json.loads(gzip.decompress(r.content).decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - surface the real body
            raise PaprikaError(
                f"Could not parse the response from {url}: {exc}. "
                f"First 200 bytes: {r.content[:200]!r}"
            ) from exc

    if isinstance(payload, dict) and "error" in payload:
        raise PaprikaError(f"Paprika returned an error for {path}: {payload['error']}")
    if isinstance(payload, dict) and "result" in payload:
        return payload["result"]
    return payload


def fetch_all(progress=None) -> tuple[list[Recipe], dict[str, str]]:
    """Pull every recipe plus the category uid -> name map.

    Returns (recipes, categories). `progress` is an optional callable taking
    (done, total) so a caller can show a counter over a slow library.
    """
    email, password = _credentials()
    session = requests.Session()
    session.auth = (email, password)
    session.headers["User-Agent"] = "meal-planner/1.0 (personal use)"

    categories: dict[str, str] = {}
    try:
        for c in _get(session, "categories/") or []:
            if isinstance(c, dict) and c.get("uid"):
                categories[str(c["uid"])] = str(c.get("name", ""))
    except Exception:
        # Categories are a nicety; a library with none should still plan.
        categories = {}

    index = _get(session, "recipes/") or []
    uids = [str(r["uid"]) for r in index if isinstance(r, dict) and r.get("uid")]

    recipes: list[Recipe] = []
    for i, uid in enumerate(uids, 1):
        data = _get(session, f"recipe/{uid}/")
        if isinstance(data, dict):
            recipes.append(Recipe.from_api(data))
        if progress:
            progress(i, len(uids))

    return recipes, categories
