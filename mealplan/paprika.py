"""Read-only client for Paprika's cloud-sync API.

This is Paprika's own sync endpoint, the one the phone app talks to. It is not
a documented public API, so everything here is defensive: we never write, we
cache aggressively, and a shape we do not recognise is reported rather than
guessed at.

Auth is HTTP Basic with the Paprika account email and password. Those come from
the environment (see .env.example) and are sent to paprikaapp.com and nowhere
else.

**Why v1 and not v2.** v2 does not accept HTTP Basic at all -- it answers a
correct email and password with `{"error": "Invalid or expired credentials."}`
and sends no `WWW-Authenticate` header, which is the tell that it never offered
Basic in the first place. Its login endpoint (`/api/v2/account/login/`) takes
the same credentials and answers `{"error": "Unrecognized client."}`: it
validated the account, then refused *the client*. That is Paprika gating v2 to
their own apps, and the only way past it is to forge a client identity, which is
not ours to do on someone else's server. v1 serves the same library to an honest
client using the account's own credentials, so that is what this uses. If v1 is
ever retired the answer is Paprika's own export -- File > Export writes a
`.paprikarecipes` file, which is a zip of gzipped JSON -- and an importer for
it, not a spoofed User-Agent.
"""

from __future__ import annotations

import gzip
import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable

import requests

BASE = "https://www.paprikaapp.com/api/v1/sync"
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
    # Paprika's own content hash, from the recipe index. Kept so a re-sync can
    # skip anything unchanged; nothing else reads it.
    hash: str = ""

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
            hash=str(d.get("hash", "") or ""),
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
            "in_trash": self.in_trash, "hash": self.hash,
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


def _decode(content: bytes) -> Any:
    """Last-resort decode for a body requests did not decompress itself.

    Paprika serves these compressed and negotiates the codec, so what arrives
    depends on what the installed urllib3 advertised -- brotli in practice, gzip
    on a build without it. requests unwraps either on its own, and this only
    runs when it has not.
    """
    def _brotli(raw: bytes) -> bytes:
        import brotli  # noqa: PLC0415 - optional, and only needed here
        return brotli.decompress(raw)

    for inflate in (_brotli, gzip.decompress):
        try:
            return json.loads(inflate(content).decode("utf-8"))
        except Exception:  # noqa: BLE001 - try the next codec
            continue
    return None


def _get(session: requests.Session, path: str) -> Any:
    """GET one sync endpoint and unwrap Paprika's {"result": ...} envelope."""
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
        payload = _decode(r.content)
        if payload is None:
            raise PaprikaError(
                f"Could not parse the response from {url}. Content-Encoding: "
                f"{r.headers.get('Content-Encoding', '(none)')}. "
                f"First 200 bytes: {r.content[:200]!r}"
            ) from None

    if isinstance(payload, dict) and "error" in payload:
        err = payload["error"]
        msg = err.get("message", err) if isinstance(err, dict) else err
        raise PaprikaError(f"Paprika returned an error for {path}: {msg}")
    if isinstance(payload, dict) and "result" in payload:
        return payload["result"]
    return payload


def fetch_all(
    progress: Callable[[int, int], None] | None = None,
    cached: dict[str, dict] | None = None,
) -> tuple[list[Recipe], dict[str, str], dict[str, int]]:
    """Pull every recipe plus the category uid -> name map.

    Returns (recipes, categories, stats).

    The index is one request and carries a uid and a content hash per recipe;
    the recipes themselves are one request each. So a library of a hundred is a
    hundred round trips on a first sync, and `cached` -- the previous cache
    keyed by uid -- is what stops it being a hundred on every sync after that.
    Anything whose hash is unchanged is reused untouched.
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
    except PaprikaError:
        # Categories are a nicety; a library with none should still plan.
        categories = {}

    index = _get(session, "recipes/") or []
    wanted: list[tuple[str, str]] = [
        (str(e["uid"]), str(e.get("hash", "")))
        for e in index if isinstance(e, dict) and e.get("uid")
    ]

    cached = cached or {}
    recipes: list[Recipe] = []
    stats = {"total": len(wanted), "fetched": 0, "reused": 0}

    for i, (uid, digest) in enumerate(wanted, 1):
        old = cached.get(uid)
        if old and digest and old.get("hash") == digest:
            recipes.append(Recipe.from_json(old))
            stats["reused"] += 1
        else:
            data = _get(session, f"recipe/{uid}/")
            if isinstance(data, dict):
                data.setdefault("hash", digest)
                recipes.append(Recipe.from_api(data))
                stats["fetched"] += 1
        if progress:
            progress(i, len(wanted))

    live = {u for u, _ in wanted}
    stats["removed"] = len([u for u in cached if u not in live])
    return recipes, categories, stats
