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


def for_slug(slug: str) -> dict:
    return load().get(slug, {})
