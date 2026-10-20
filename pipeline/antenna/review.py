"""The review file: what a person corrected after reading the board.

A pipeline like this is wrong in small ways every day: a regulatory
consultant listed as a team member, a forty-year-old firm that filed its
first trademark, a sector label pulled sideways by one word. Those are
fixed here, in `pipeline/review.json`, not by special-casing collectors.
Each entry is keyed by slug and says why, so the corrections can be read
and argued with. In a fund this file is where partner feedback would land.

    {
      "acme-drones": {
        "exclude": "A DJI reseller, not a manufacturer",
        "sector": "autonomy",
        "name": "Acme",
        "stage": "growth",
        "founded": 2017,
        "raised_usd": 120000000,
        "source": "https://...",
        "drop_people": ["Jane Doe"],
        "drop_signals": ["https://evidence-url-of-a-wrong-signal"],
        "note": "Series C announced 2025"
      }
    }
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from functools import lru_cache

from .config import REVIEW_PATH

STAGES = ("formation", "early", "growth", "incumbent")


@lru_cache(maxsize=1)
def load() -> dict[str, dict]:
    if not REVIEW_PATH.exists():
        return {}
    try:
        data = json.loads(REVIEW_PATH.read_text())
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)}


_carried: dict[str, str] = {}


def carry_over(rows: Iterable[tuple[str, Iterable[str]]]) -> dict[str, str]:
    """Let an entry follow a company whose slug changed. Returns the moves.

    Entries are keyed by the slug a company had when it was reviewed. A
    rename gives it a new slug (a GitHub project moved to another account,
    a company that took a new name), and its old names stay among its
    aliases. An entry whose slug no row holds any more applies to the one
    row that lists the old name as an alias; with two claimants it applies
    to neither. A row's own entry always wins.

    `rows` is (slug, alias slugs) for every entity. Resolution calls this
    each run, before anything looks an entry up.
    """
    entries = load()
    rows = [(slug, set(aliases)) for slug, aliases in rows]
    current = {slug for slug, _ in rows}
    claims: dict[str, set[str]] = {}
    for slug, aliases in rows:
        if slug in entries:
            continue
        for key in sorted(aliases):
            if key in entries and key not in current:
                claims.setdefault(key, set()).add(slug)
    moves: dict[str, str] = {}
    for key, slugs in sorted(claims.items()):
        if len(slugs) == 1:
            moves.setdefault(next(iter(slugs)), key)
    _carried.clear()
    _carried.update(moves)
    return dict(moves)


def for_slug(slug: str) -> dict:
    entries = load()
    return entries.get(slug) or entries.get(_carried.get(slug, ""), {})
