"""Write the JSON the web app reads. The app has no backend: this is its API."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from .collectors.base import parse_date
from .config import (
    BRIEFS_DIR,
    EXPORT_DIR,
    FAMILY_WEIGHTS,
    HALF_LIFE_DAYS,
    LOOKBACK_DAYS,
    THESIS,
)
from . import review
from .db import now_iso
from .score import is_portfolio, strength_asof

BOARD_SIZE = 300
MIN_FIT = 0.3
MIN_MOMENTUM = 0.04
SIGNALS_PER_ENTITY = 40
FEED_SIZE = 600


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=False, default=str))


def _brief(slug: str) -> dict | None:
    """A research brief for this company, if one has been written.

    Briefs live in pipeline/briefs/<slug>.json. They are written by a
    research agent and fact-checked by a second one, and are kept apart from
    the signals because they are prose about the evidence, not evidence.
    """
    path = BRIEFS_DIR / f"{slug}.json"
    if not path.exists():
        return None
    try:
        b = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return b if b.get("summary") and b.get("sources") else None


def _lead(sigs: list[dict], dropped: set, announcement: dict | None) -> dict | None:
    """How long before the news did the first signal become visible?

    `announcement` is the first public announcement of the company's launch
    or round, found and dated by the research agent and checked by a second.
    The first signal is dated by when it could first be seen: an embargoed
    award counts from its release, not from its signing. Days are positive
    when the signal came first and negative when the news did.
    """
    if not announcement or not parse_date(str(announcement.get("date") or "")):
        return None
    first = None
    for s in sigs:
        if s["url"] in dropped or s["metrics"].get("observed_only") or s["strength"] <= 0:
            continue
        seen = parse_date(str(s["metrics"].get("public_at") or "")) or parse_date(s["occurred_at"][:10])
        if seen and (first is None or seen < first[0]):
            first = (seen, s)
    if first is None:
        return None
    announced = parse_date(str(announcement["date"]))
    return {
        "firstSignalAt": first[0].isoformat(),
        "firstSignal": first[1]["title"],
        "firstSignalUrl": first[1]["url"],
        "announcedAt": announced.isoformat(),
        "announcement": announcement.get("what") or "",
        "announcementUrl": announcement.get("url") or "",
        "days": (announced - first[0]).days,
    }


def _year(value) -> int | None:
    """A founding year from whatever a source or a reviewer gave: 2024, "2024", "2024-03-01"."""
    text = str(value or "")[:4]
    return int(text) if text.isdigit() else None


def _with_brief_team(people: list[dict], brief: dict | None) -> list[dict]:
    """Add the people a brief names to the list from filings and code.

    A filing lists whoever signed it; the brief names the founders. Someone
    already present keeps their filing role and gains the brief's
    background where they had none.
    """
    if not brief:
        return people
    out = [dict(p) for p in people]
    index = {" ".join(sorted(p["name"].lower().replace(".", "").split())): p for p in out}
    added = []
    for t in brief.get("team") or []:
        key = " ".join(sorted(t["name"].lower().replace(".", "").split()))
        cur = index.get(key)
        if cur is None:
            added.append({"name": t["name"], "role": "Named in the brief",
                          "facts": {"bio": t["background"]}, "sources": ["brief"]})
        elif not (cur.get("facts") or {}).get("bio"):
            cur.setdefault("facts", {})["bio"] = t["background"]
    # The brief's people are the founders and officers: they lead the list.
    return added + out


_NOT_A_DESCRIPTION = ("Trademark goods:", "Maker of the ")


def _one_liner(text: str | None) -> str | None:
    """A one-liner fit to show, or nothing.

    A list of trademark goods or a regulator's model field is not a
    description of a company, and showing one as if it were misleads. The
    text stays on the signal it came from.
    """
    if not text or text.startswith(_NOT_A_DESCRIPTION):
        return None
    return text


def _signal_out(s: dict, today: date) -> dict:
    out = {
        "id": s["fingerprint"][:12],
        "family": s["family"],
        "kind": s["kind"],
        "source": s["source"],
        "title": s["title"],
        "occurredAt": s["occurred_at"][:10],
        "url": s["url"],
        "strength": round(s["strength"], 3),
        "now": round(strength_asof(s, today), 3),
    }
    if s["value"] is not None:
        out["value"] = s["value"]
        out["unit"] = s["unit"]
    # A reading with no date of its own (a DNS record, a lifetime count) is
    # state, not news: it belongs on the dossier but not on the wire.
    if s["metrics"].get("observed_only") or s["strength"] <= 0:
        out["state"] = True
    series = [{"t": str(p["t"])[:10], "v": p.get("v")} for p in s["series"] if p.get("v") is not None]
    if len(series) >= 3:
        out["series"] = series[-26:]
    return out


def export(conn: sqlite3.Connection, run_id: int, today: date, out_dir: Path = EXPORT_DIR) -> dict:
    rows = conn.execute(
        "SELECT e.*, s.breakdown FROM entities e JOIN scores s ON s.entity_id = e.id"
        " WHERE s.run_id = ?", (run_id,)
    ).fetchall()

    candidates = []
    portfolio_seen = []
    excluded = []
    awaiting = []
    gated = bool(review.load())
    for r in rows:
        e = dict(r)
        sc = json.loads(e.pop("breakdown"))
        e["aliases"] = json.loads(e["aliases"] or "[]")
        e["links"] = json.loads(e["links"] or "{}")
        rev = review.for_slug(e["slug"])
        if rev.get("exclude"):
            excluded.append({"name": e["name"], "reason": rev["exclude"]})
            continue
        if rev.get("name"):
            e["name"] = rev["name"]
        e["_review"] = rev
        if is_portfolio(e["name"], e["aliases"]):
            portfolio_seen.append({"name": e["name"], "edge": sc["edge"], "momentum": sc["momentum"]})
            continue
        if sc["fit"] < MIN_FIT or sc["momentum"] < MIN_MOMENTUM:
            continue
        # Once a review file exists, nothing is ranked until someone has
        # looked at it. New arrivals wait in a queue, counted on the method
        # page, instead of appearing on the board unchecked.
        if gated and not rev:
            awaiting.append({"slug": e["slug"], "name": e["name"], "edge": sc["edge"]})
            continue
        candidates.append((e, sc))

    candidates.sort(key=lambda x: -x[1]["edge"])
    # Rank a week ago, among the same set, from the backcast.
    prev_order = sorted(candidates, key=lambda x: -x[1]["history"][-2]["edge"])
    prev_rank = {e["id"]: i + 1 for i, (e, sc) in enumerate(prev_order) if sc["history"][-2]["edge"] > 0}

    board, dossiers, feed, leads, unannounced = [], {}, [], [], []
    week_ago = today - timedelta(days=7)
    for rank, (e, sc) in enumerate(candidates[:BOARD_SIZE], start=1):
        sigs = [dict(s) for s in conn.execute("SELECT * FROM signals WHERE entity_id=?", (e["id"],))]
        for s in sigs:
            s["series"] = json.loads(s["series"] or "[]")
            s["metrics"] = json.loads(s["metrics"] or "{}")
        dropped = set(e["_review"].get("drop_signals") or [])
        # A search that found nothing is kept for the scorer's information
        # and not shown: "no mentions" under a legal name proves little.
        shown = [s for s in sigs if s["url"] not in dropped
                 and not (s["kind"] == "hn_baseline" and not s["value"])
                 and s["kind"] not in ("website_verified", "website_described")]
        # Dated events newest first, then undated readings. Two collectors can
        # report the same fact in the same words: show it once.
        seen_out: set[tuple] = set()
        outs = []
        for o in sorted((_signal_out(s, today) for s in shown),
                        key=lambda s: (not s.get("state"), s["occurredAt"], s["now"]), reverse=True):
            k = (o["family"], o["title"], o["occurredAt"])
            if k not in seen_out:
                seen_out.add(k)
                outs.append(o)
        if not outs:
            continue
        # Why now: the strongest live signals, one per family, and dated
        # events ahead of undated readings.
        by_now = sorted(outs, key=lambda s: (bool(s.get("state")), -s["now"]))
        why, seen_fam = [], set()
        for s in by_now:
            if s["family"] in seen_fam or s["now"] <= 0.02:
                continue
            item = {"family": s["family"], "title": s["title"], "url": s["url"], "occurredAt": s["occurredAt"]}
            if s.get("state"):
                item["state"] = True
            why.append(item)
            seen_fam.add(s["family"])
            if len(why) == 3:
                break
        # What sits behind each cell of the family glyph: a count and the
        # two strongest signals, for the hover on the board.
        by_family: dict[str, dict] = {}
        for s in by_now:
            f = by_family.setdefault(s["family"], {"count": 0, "items": []})
            f["count"] += 1
            if len(f["items"]) < 2:
                f["items"].append({"title": s["title"], "occurredAt": s["occurredAt"]})
        # A reading taken today (a DNS record, a lifetime count) is stamped
        # with today's date. It is not news: first and last signal dates
        # come from dated events, so "latest signal" means something happened.
        dated = [s for s in outs if not s.get("state") and parse_date(s["occurredAt"])]
        basis = dated or [s for s in outs if parse_date(s["occurredAt"])]
        dates = [parse_date(s["occurredAt"]) for s in basis]
        first_at, last_at = min(dates), max(dates)
        latest = max(dated, key=lambda s: (s["occurredAt"], s["now"])) if dated else None
        flags = []
        if first_at >= week_ago:
            flags.append("new")
        if sc["convergence"] >= 2:
            flags.append("convergent")
        if sc["earliness"] >= 0.85:
            flags.append("pre-consensus")
        if sc["team"] >= 0.45:
            flags.append("pedigree")

        row = {
            "slug": e["slug"],
            "name": e["name"],
            "kind": e["kind"],
            "oneLiner": _one_liner(e["one_liner"]),
            "domain": e["domain"],
            "location": e["location"],
            "sector": sc["sector"],
            "rank": rank,
            "rankPrev": prev_rank.get(e["id"]),
            "edge": sc["edge"],
            "momentum": sc["momentum"],
            "fit": sc["fit"],
            "earliness": sc["earliness"],
            "team": sc["team"],
            "families": sc["families"],
            "convergence": sc["convergence"],
            "history": [h["edge"] for h in sc["history"]],
            "why": why,
            "latest": ({"family": latest["family"], "title": latest["title"], "url": latest["url"],
                        "occurredAt": latest["occurredAt"]} if latest else None),
            "founded": _year(e["_review"].get("founded") or e["founded"]),
            "raisedUsd": e["_review"].get("raised_usd"),
            "bySignalFamily": by_family,
            "signalCount": len(outs),
            "firstSignalAt": first_at.isoformat(),
            "lastSignalAt": last_at.isoformat(),
            "flags": flags,
        }
        board.append(row)
        dossiers[e["slug"]] = {
            **row,
            "description": e["description"],
            "github": e["github"],
            "links": e["links"],
            "aliases": e["aliases"],
            "sectors": sc["sectors"],
            "terms": sc["terms"],
            "consensus": sc["consensus_parts"],
            "metrics": sc["metrics"],
            "historyDates": [h["t"] for h in sc["history"]],
            "momentumHistory": [h["momentum"] for h in sc["history"]],
            "people": [
                {k: v for k, v in p.items() if v not in (None, [], {})} for p in sc["people"][:12]
            ],
            "signals": outs[:SIGNALS_PER_ENTITY],
        }
        rev = e["_review"]
        if rev.get("stage") in review.STAGES:
            row["stage"] = rev["stage"]
            dossiers[e["slug"]]["stage"] = rev["stage"]
        if rev:
            dossiers[e["slug"]]["review"] = {k: rev[k] for k in ("note", "source", "founded", "raised_usd") if rev.get(k)}
        brief = _brief(e["slug"])
        if brief:
            dossiers[e["slug"]]["people"] = _with_brief_team(dossiers[e["slug"]]["people"], brief)[:14]
            ann = brief.pop("announcement", None)
            dossiers[e["slug"]]["brief"] = brief
            row["hasBrief"] = True
            lead = _lead(sigs, dropped, ann)
            if lead:
                dossiers[e["slug"]]["lead"] = lead
                leads.append({"slug": e["slug"], "name": e["name"], "rank": rank, **lead})
            elif ann is None and brief.get("searchedAnnouncement"):
                # Looked for news of the company or its round and found none.
                dossiers[e["slug"]]["unannounced"] = True
                unannounced.append({"slug": e["slug"], "name": e["name"], "rank": rank,
                                    "firstSignalAt": first_at.isoformat()})
        for s in outs:
            if s.get("state"):
                continue
            feed.append({**{k: s[k] for k in ("id", "family", "kind", "source", "title", "occurredAt", "url", "strength")},
                         "slug": e["slug"], "name": e["name"], "sector": sc["sector"], "rank": rank,
                         "stage": e["_review"].get("stage") if e["_review"].get("stage") in review.STAGES else None})

    feed.sort(key=lambda s: (s["occurredAt"], s["strength"]), reverse=True)
    feed = feed[:FEED_SIZE]

    collectors = [dict(r) for r in conn.execute(
        "SELECT source, seconds, emitted, inserted, error FROM collector_runs WHERE run_id=?", (run_id,))]
    totals = conn.execute(
        "SELECT COUNT(*) n, COUNT(DISTINCT entity_id) e, COUNT(DISTINCT source) s FROM signals").fetchone()
    by_family = {r["family"]: r["n"] for r in conn.execute(
        "SELECT family, COUNT(*) n FROM signals GROUP BY family")}
    by_source = {r["source"]: r["n"] for r in conn.execute(
        "SELECT source, COUNT(*) n FROM signals GROUP BY source")}
    meta = {
        "generatedAt": now_iso(),
        "asOf": today.isoformat(),
        "lookbackDays": LOOKBACK_DAYS,
        "totals": {
            "signals": totals["n"],
            "entities": totals["e"],
            "sources": totals["s"],
            "scored": len(rows),
            "onThesis": len(candidates) + len(awaiting),
            "board": len(board),
            "newThisWeek": sum(1 for b in board if "new" in b["flags"]),
            "convergent": sum(1 for b in board if "convergent" in b["flags"]),
        },
        "bySector": dict(Counter(b["sector"] for b in board).most_common()),
        "byFamily": by_family,
        "bySource": by_source,
        "collectors": collectors,
        "weights": FAMILY_WEIGHTS,
        "halfLives": HALF_LIFE_DAYS,
        "thesis": {k: v["strong"] for k, v in THESIS.items()},
        "portfolioSeen": sorted(portfolio_seen, key=lambda p: -p["momentum"])[:20],
        "reviewed": {"entries": len(review.load()), "excluded": len(excluded),
                     "awaiting": len(awaiting)},
        "leads": sorted(leads, key=lambda x: -x["days"]),
        "unannounced": unannounced,
        "briefs": sum(1 for b in board if b.get("hasBrief")),
    }

    _write(out_dir / "board.json", board)
    _write(out_dir / "entities.json", dossiers)
    _write(out_dir / "feed.json", feed)
    _write(out_dir / "meta.json", meta)
    # The search palette loads this on first use, so it is not inlined into
    # every page of the static export.
    _write(out_dir.parents[1] / "public" / "palette.json",
           [{"slug": b["slug"], "name": b["name"], "sector": b["sector"], "rank": b["rank"],
             "oneLiner": b["oneLiner"]} for b in board])
    return meta
