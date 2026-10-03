"""Write the JSON the web app reads. The app has no backend: this is its API."""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from .collectors.base import parse_date
from .config import (
    BRIEFS_DIR,
    DATA_DIR,
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
WIRE_FLOOR = 0.05   # strength at or under this is not news


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


def _last_generated(out_dir: Path) -> str:
    """When data was last collected, from the export already on disk."""
    try:
        return json.loads((out_dir / "meta.json").read_text())["generatedAt"]
    except (OSError, ValueError, KeyError):
        return now_iso()


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
_ABBREVIATIONS = {"inc", "co", "corp", "ltd", "llc", "no", "dr", "mr", "ms", "st", "vs", "approx", "est",
                  "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec"}
# What can follow a company's name at the start of its own description:
# "(TMD), LLC", "Co. Ltd.", then "is", a dash or colon, or a verb.
_LEGAL_TAIL = re.compile(r"(?:\s*\([^)]{1,24}\))?(?:,?\s+(?:Inc|LLC|Ltd|Co|Corp|Corporation|Company|Limited|GmbH|PBC)\b\.?)*", re.I)
_AFTER_NAME = re.compile(r"\s*(?:[—–:|-]\s+|(?:is|are)\s+(?=\w)|(?=[a-z]+s\b))")
_PITCH = re.compile(r"\b(?:learn (?:why|how|more)|click here|contact us|welcome to|announces|digital hub|official site|will)\b", re.I)
# A line that speaks as the company ("We're building X", "We make X") is
# turned into the third person; one that addresses the reader ("Discover
# our...", "Your partner in...") is a slogan and is not shown.
_WE_ARE = re.compile(r"^(?:At [\w.]+, )?we(?:'re|’re| are)\s+(?:actively\s+)?", re.I)
_WE_DO = re.compile(r"^(?:At [\w.]+, )?we\s+([a-z]+)\b", re.I)
_NOT_A_VERB = {"can", "will", "would", "could", "may", "might", "must", "should", "have", "do", "believe",
               "think", "know", "want", "love", "help", "plan", "aim", "strive", "exist", "also", "just", "all"}
_NOT_WHAT_IT_IS = re.compile(r"^(?:dedicated|committed|proud|excited|thrilled|passionate|hiring|here|on a)\b", re.I)
_SLOGAN = re.compile(r"^(?:our|your|meet|discover|explore|experience|introducing|see|enable|automate|reduce|"
                     r"standardize|democratize|build your|design, build)\b", re.I)
_WE_CLAUSE = re.compile(r",?\s+(?:because|so|and|as|since)\s+we\b.*$", re.I)


def _third_person(line: str) -> str | None:
    """'We're building arms' gives 'Building arms'; 'We make arms' gives
    'Makes arms'. None when the line is not a description at all."""
    m = _WE_ARE.match(line)
    if m:
        line = line[m.end():]
        if _NOT_WHAT_IT_IS.match(line):
            return None
    else:
        m = _WE_DO.match(line)
        if m:
            verb = m.group(1).lower()
            if verb in _NOT_A_VERB:
                return None
            verb += "es" if verb.endswith(("s", "x", "ch", "sh", "o")) else "s"
            line = verb + line[m.end():]
    if _SLOGAN.match(line):
        return None
    line = _WE_CLAUSE.sub("", line)
    return line[:1].upper() + line[1:]


def _first_sentence(text: str) -> str:
    for m in re.finditer(r"[.!?](?=\s+\S)", text):
        word = text[:m.start()].rsplit(None, 1)[-1].strip("'\"()")
        if len(word) > 1 and "." not in word and word.lower() not in _ABBREVIATIONS:
            return text[:m.start()]
    return text.rstrip(".!")


def _without_name(text: str, name: str) -> str:
    """'Acme Robotics is building arms' gives 'Building arms': the name is
    already printed beside the line, in the company's own or a shorter form."""
    words = name.split()
    for n in range(len(words), 0, -1):
        head = re.match(r"[\s-]".join(re.escape(w) for w in words[:n]) + r"(?![\w.])", text, re.I)
        if not head:
            continue
        rest = text[head.end():]
        rest = rest[_LEGAL_TAIL.match(rest).end():]
        m = _AFTER_NAME.match(rest)
        if m and (rest[:1].isspace() or m.end()):
            rest = rest[m.end():].lstrip()
            return rest[:1].upper() + rest[1:] if len(rest) > 20 else text
    return text


def _one_liner(text: str | None, name: str = "") -> str | None:
    """A one-liner fit to show, or nothing.

    A list of trademark goods or a regulator's model field is not a
    description of a company, and showing one as if it were misleads. The
    text stays on the signal it came from. What is shown is the first
    sentence of the company's own description, without its name in front
    and without the invitation that tends to follow.
    """
    if not text or text.startswith(_NOT_A_DESCRIPTION):
        return None
    whole = " ".join(text.split())
    line = _first_sentence(_without_name(whole, name)).rstrip(" …,;:—–-")
    line = _third_person(line)
    if not line or len(line) < 12 or _PITCH.search(line):
        return None
    # A description the collector cut off mid-sentence (it marks the cut with
    # an ellipsis): drop a short trailing fragment, or else keep the mark.
    if whole.endswith("…") and whole.rstrip(" …").endswith(line[1:]):
        clause = max(line.rfind(", "), line.rfind(": "), line.rfind(" - "), line.rfind(" — "))
        line = line[:clause] if 0 < len(line) - clause < 60 and clause >= 60 else line + "…"
    return line


_SENTENCE_END = re.compile(r"([A-Za-z0-9%)'\"]+)[.!?]['\")]?(?=\s+[A-Z0-9'\"(])")
_DANGLING = re.compile(r"[\s,;:]+(?:per|and|or|via|from|see|with)?[\s,;:]*$")
_SHORTEST_NOTE = 60


def _whole_part(body: str) -> str | None:
    """The longest opening of a cut-off note that ends cleanly: at a full
    stop, at a semicolon, or before a bracket that never closes."""
    points = [m.end() for m in _SENTENCE_END.finditer(body)
              if len(m.group(1)) > 1 and m.group(1).lower().strip("'\"()") not in _ABBREVIATIONS]
    points += [m.start() for m in re.finditer(r";\s", body)]
    depth, opened = 0, -1
    for i, ch in enumerate(body):
        if ch == "(":
            opened = i if depth == 0 else opened
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
    # Before an unclosed bracket only if a whole clause leads up to it:
    # "The VESPID trademark (serial" would leave half a sentence.
    if depth and opened - max([p for p in points if p <= opened] + [0]) >= _SHORTEST_NOTE:
        points.append(opened)
    points = [p for p in points if p >= _SHORTEST_NOTE and body[:p].count("(") == body[:p].count(")")]
    return body[:max(points)] if points else None


def _tidy_note(text: str | None) -> str | None:
    """A reviewer's note fit to show.

    Notes are stored with their links taken out and cut to a length, which
    leaves empty brackets, a trailing "per" where a link was, and a last
    sentence that stops half way. This closes those up and ends the note
    where it last ends cleanly. A note with no such place keeps its ellipsis.
    """
    if not text:
        return None
    s = re.sub(r"\(\s*[,;]?\s*\)", "", text)            # "( )" where a link was
    s = re.sub(r"\(\s*[,;]\s*", "(", s)
    s = re.sub(r"\s*[,;]\s*\)", ")", s)
    s = re.sub(r":\s*(?=[;)])", "", s)                    # "2 Feb 2026:;" with the link gone
    s = re.sub(r"[,;]?\s+per(?=\s*[;,.:]|\s*$)", "", s)    # "... $15M, per" likewise
    s = re.sub(r"\s+([.,;:)])", r"\1", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    if s.endswith("…"):
        body = s[:-1].rstrip()
        s = body if body.endswith((".", "!", "?")) else (_whole_part(body) or s)
    if not s.endswith("…"):
        s = _DANGLING.sub("", s)
        if s and (s[-1] not in ".!?'\")" or (s[-1] in "'\")" and s[-2:-1] not in (".", "!", "?"))):
            s += "."
    return s or None


_ISO_DATE = re.compile(r"\b(20\d\d)-(\d\d)-(\d\d)\b")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
# "7 Oct 2026" and "7 October 2026".
_DAY_FIRST_DATE = re.compile(r"\b(\d{1,2}) ((?:" + "|".join(_MONTHS) + r")[a-z]*)\.? (20\d\d)\b")


def _house_style(text: str, today: date) -> str:
    """'registered on 2026-09-19' gives 'registered on Sep 19', and so does
    '19 Sep 2026': dates inside a title or a note read the way the site
    prints every other date, with the year only when it is not this one.
    Spelling follows the site too, which is American: 'licence' becomes
    'license'."""
    def short(m: re.Match) -> str:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if not (1 <= month <= 12 and 1 <= day <= 31):
            return m.group(0)
        return f"{_MONTHS[month - 1]} {day}" + ("" if year == today.year else f", {year}")
    def day_first(m: re.Match) -> str:
        day, month, year = int(m.group(1)), m.group(2)[:3], int(m.group(3))
        return f"{month} {day}" + ("" if year == today.year else f", {year}")

    text = _DAY_FIRST_DATE.sub(day_first, _ISO_DATE.sub(short, text))
    return re.sub(r"\b([Ll])icence", r"\1icense", text)


def _signal_out(s: dict, today: date) -> dict:
    out = {
        "id": s["fingerprint"][:12],
        "family": s["family"],
        "kind": s["kind"],
        "source": s["source"],
        "title": _house_style(s["title"], today),
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


def export(conn: sqlite3.Connection, run_id: int, today: date, out_dir: Path = EXPORT_DIR,
           collected: bool = True) -> dict:
    """Write the site's JSON. `collected` is False for a re-score of stored
    signals: the site's run stamp then keeps the time data was last gathered."""
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
                item = {"title": s["title"], "occurredAt": s["occurredAt"]}
                if s.get("state"):
                    item["state"] = True
                f["items"].append(item)
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
            "oneLiner": _one_liner(e["one_liner"], e["name"]),
            "domain": e["domain"],
            "location": e["location"],
            "sector": sc["sector"],
            "rank": rank,
            # A company whose first dated signal is this week had no rank a
            # week ago, whatever its undated readings add up to in the backcast.
            "rankPrev": None if "new" in flags else prev_rank.get(e["id"]),
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
            note = _tidy_note(rev.get("note"))
            shown = {**rev, "note": _house_style(note, today) if note else None}
            dossiers[e["slug"]]["review"] = {k: shown[k] for k in ("note", "source", "founded", "raised_usd") if shown.get(k)}
        brief = _brief(e["slug"])
        if brief:
            dossiers[e["slug"]]["people"] = _with_brief_team(dossiers[e["slug"]]["people"], brief)[:14]
            ann = brief.pop("announcement", None)
            # The brief's own line says what the company makes, checked
            # against its sources; the website's line is the company's pitch.
            line = (brief.pop("oneLiner", None) or "").strip()
            if line:
                row["oneLiner"] = dossiers[e["slug"]]["oneLiner"] = line
            dossiers[e["slug"]]["brief"] = brief
            row["hasBrief"] = True
            lead = _lead(sigs, dropped, ann)
            if lead:
                lead["firstSignal"] = _house_style(lead["firstSignal"], today)
                dossiers[e["slug"]]["lead"] = lead
                leads.append({"slug": e["slug"], "name": e["name"], "rank": rank, **lead})
            elif ann is None and brief.get("searchedAnnouncement"):
                # Looked for news of the company or its round and found none.
                dossiers[e["slug"]]["unannounced"] = True
                unannounced.append({"slug": e["slug"], "name": e["name"], "rank": rank,
                                    "firstSignalAt": first_at.isoformat()})
        for s in outs:
            # The wire is for news: no undated readings, and nothing a
            # collector itself rates as next to nothing (a job board moved
            # to a new provider, say).
            if s.get("state") or s["strength"] <= WIRE_FLOOR:
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
        "generatedAt": now_iso() if collected else _last_generated(out_dir),
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
    # The review queue stays on this machine: names nobody has looked at yet
    # do not belong on the site. `antenna report` prints the top of it.
    _write(DATA_DIR / "awaiting.json", sorted(awaiting, key=lambda a: -a["edge"]))
    # The search palette loads this on first use, so it is not inlined into
    # every page of the static export.
    _write(out_dir.parents[1] / "public" / "palette.json",
           [{"slug": b["slug"], "name": b["name"], "sector": b["sector"], "rank": b["rank"],
             "oneLiner": b["oneLiner"]} for b in board])
    return meta
