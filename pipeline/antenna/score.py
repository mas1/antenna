"""The edge score.

    edge = 100 * momentum * thesis_fit * earliness * team

    momentum   independent signal families firing now, combined by noisy-OR
               so corroboration across families compounds
    thesis_fit how squarely the company sits in robotics, autonomy, defense,
               energy, manufacturing, semiconductors or space
    earliness  1 - consensus: stars, press, traffic rank, headcount and
               round size all say "everyone already knows"
    team       pedigree and research record of the people attached

Every term is 0..1 and every term is explainable from the stored signals,
so the score can be recomputed "as of" any past date. That is how the rank
history is built without waiting weeks for archived runs.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from datetime import date, timedelta
from functools import lru_cache

from .collectors.base import loose_name, normalize_name, parse_date
from .config import (
    FAMILY_CAP,
    FAMILY_GAIN,
    FAMILY_WEIGHTS,
    FIXTURES_DIR,
    HALF_LIFE_DAYS,
    PORTFOLIO,
)
from . import review
from .models import FAMILIES
from .thesis import classify

HISTORY_WEEKS = 12
FIRING = 0.12  # a family counts as "firing" above this


def noisy_or(values: list[float]) -> float:
    p = 1.0
    for v in values:
        p *= 1.0 - max(0.0, min(1.0, v))
    return 1.0 - p


def squash(x: float | None, half: float) -> float:
    if not x or x <= 0:
        return 0.0
    return x / (x + half)


def strength_asof(sig: dict, asof: date) -> float:
    """A signal's strength on a given date.

    Point events decay from the day the world could first see them: the
    event date, or `metrics.public_at` for records released after an
    embargo (Department of Defense awards appear ninety days late, and
    nobody could have acted on them sooner). Signals that carry a series
    with per-point strength (`s`) are continuous measurements: use the
    latest point on or before `asof`, decayed from that point.
    """
    half = HALF_LIFE_DAYS.get(sig["family"], 21.0)
    # Sources stamp UTC dates, so "today" there can be a day ahead of here.
    horizon = asof + timedelta(days=1)
    pts = [p for p in sig["series"] if "s" in p and parse_date(str(p.get("t")))]
    if pts:
        past = [p for p in pts if parse_date(str(p["t"])) <= horizon]
        if not past:
            return 0.0
        last = max(past, key=lambda p: parse_date(str(p["t"])))
        age = (asof - parse_date(str(last["t"]))).days
        return float(last["s"]) * 0.5 ** (max(age, 0) / half)
    when = parse_date(str(sig.get("metrics", {}).get("public_at") or "")) or parse_date(sig["occurred_at"][:10])
    if when is None or when > horizon:
        return 0.0
    age = max((asof - when).days, 0)
    return sig["strength"] * 0.5 ** (age / half)


@lru_cache(maxsize=1)
def pedigree_table() -> list[tuple[re.Pattern[str], int, str]]:
    """[(alias pattern, tier, canonical org)] from the pedigree fixture.

    Aliases are proper nouns and are matched with their capitals intact, so
    "six figure" never reads as Figure and "at scale" never reads as Scale.
    """
    path = FIXTURES_DIR / "positioning" / "pedigree_orgs.json"
    if not path.exists():
        return []
    out = []
    for row in json.loads(path.read_text()):
        for alias in sorted({row["org"], *row.get("aliases", [])}, key=len, reverse=True):
            alias = " ".join(alias.split())
            if len(alias) >= 3:
                pat = re.compile(rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])")
                out.append((pat, int(row.get("tier", 2)), row["org"]))
    return out


# In a sentence-length bio the org name has to be attached to the person:
# "ex-SpaceX", "previously at Tesla", "Anduril engineer". A bare mention
# ("$17M in DARPA sponsorship", "won the MIT hackathon") does not count.
_CUE_BEFORE = re.compile(
    r"(?:\b(?:at|from|ex|former|formerly|previously|joined|left|alum of)\b|ex-)"
    r"[^.;]{0,40}$", re.I)
_CUE_AFTER = re.compile(
    r"^[^.;]{0,6}\b(?:alum|alumn\w+|engineer|researcher|scientist|veteran|dropout|phd|grad\w*|"
    r"postdoc|fellow|intern|lead|director|founder|co-founder)\b", re.I)
BIO_LENGTH = 90


def pedigree_hits(text: str) -> list[tuple[str, int]]:
    """Canonical pedigree orgs a person is stated to come from."""
    seen: dict[str, int] = {}
    for part in text.split(" ; "):
        part = " ".join(part.split())
        is_bio = len(part) > BIO_LENGTH
        for pat, tier, org in pedigree_table():
            if org in seen:
                continue
            for m in pat.finditer(part):
                if is_bio and not (_CUE_BEFORE.search(part[: m.start()]) or _CUE_AFTER.search(part[m.end():])):
                    continue
                seen[org] = tier
                break
    return sorted(seen.items(), key=lambda kv: kv[1])


def team_score(people: list[dict]) -> tuple[float, list[dict]]:
    parts: list[float] = []
    enriched = []
    for p in people:
        facts = p.get("facts") or {}
        blob = " ; ".join([*(p.get("affiliations") or []), p.get("role") or "", str(facts.get("bio") or "")])
        hits = pedigree_hits(blob)
        for _, tier in hits[:3]:
            parts.append(0.55 if tier == 1 else 0.3)
        parts.append(0.6 * squash(facts.get("h_index"), 25))
        parts.append(0.3 * squash(facts.get("followers"), 3000))
        if facts.get("repeat_founder"):
            parts.append(0.45)
        enriched.append({**p, "pedigree": [org for org, _ in hits]})
    return round(noisy_or(parts), 4), enriched


# Footprint measurements. If none of these was taken, we do not know how
# visible the company is, and say so instead of calling it undiscovered.
_FOOTPRINT_KEYS = ("hn_mentions_total", "tranco_rank", "stars_total", "open_roles", "team_size")
UNMEASURED_CONSENSUS = 0.2


def _over(x: float | None, floor: float, half: float) -> float:
    """Squash the part of x above a floor: small values count for nothing."""
    return squash(max((x or 0) - floor, 0), half)


# What a reviewer's stage call is worth as consensus when no number backs it.
STAGE_CONSENSUS = {"early": 0.2, "growth": 0.6, "incumbent": 0.9}


def consensus_score(metrics: dict, founded: str | None, asof: date,
                    has_handle: bool = True, stage: str | None = None) -> tuple[float, dict]:
    """How widely known a company already is, 0..1, with the components.

    Every component has a floor, because a seed round, a handful of open
    roles and a year of existence are what an early company looks like.
    Only scale reads as consensus.
    """
    age_years = None
    d = parse_date(founded) if founded and len(str(founded)) > 4 else None
    if d is None and founded and str(founded)[:4].isdigit():
        d = date(int(str(founded)[:4]), 7, 1)
    if d:
        age_years = max((asof - d).days / 365.25, 0)
    # A domain registered years ago is rarely a company formed this year. It
    # stands in for a founding date when no filing gives one.
    if metrics.get("domain_age_days"):
        age_years = max(age_years or 0, float(metrics["domain_age_days"]) / 365.25)
    rank = metrics.get("tranco_rank")
    # Depth matters: a domain two million deep is not "known".
    traffic = 0.0
    if rank:
        traffic = 0.85 if rank <= 100_000 else 0.5 if rank <= 400_000 else 0.25 if rank <= 1_000_000 else 0.08 if rank <= 2_000_000 else 0.0
    # Money actually raised says more than the size of the offering on file.
    raised = metrics.get("amount_sold") if metrics.get("amount_sold") is not None else metrics.get("amount_usd")
    parts = {
        "stars": _over(metrics.get("stars_total"), 500, 8000),
        "press": _over(metrics.get("hn_mentions_total"), 5, 120),
        "traffic": traffic,
        "headcount": max(_over(metrics.get("open_roles"), 10, 80), _over(metrics.get("team_size"), 25, 200)),
        "capital": _over(raised, 15_000_000, 100_000_000),
        "age": 0.7 * _over(age_years, 3.0, 4.0),
        "stage": STAGE_CONSENSUS.get(stage or "", 0.0),
        # A long history of small federal awards is a grant shop, not a find.
        "awards": 0.7 * _over(metrics.get("sbir_prior_awards"), 3, 20),
    }
    # A count of zero Hacker News mentions is not a measurement: the search
    # is by domain or exact legal name, and a company can be in every
    # newspaper under a brand name the search never tried.
    def taken(k: str) -> bool:
        v = metrics.get(k)
        return v is not None and (bool(v) or k != "hn_mentions_total")

    if not stage and not any(taken(k) for k in _FOOTPRINT_KEYS):
        parts["unmeasured"] = UNMEASURED_CONSENSUS
    return round(noisy_or(list(parts.values())), 4), {k: round(v, 3) for k, v in parts.items() if v}


def score_asof(signals: list[dict], asof: date) -> dict:
    by_family: dict[str, list[float]] = defaultdict(list)
    # One real-world award can reach us through two collectors (an NSF grant
    # is in both the NSF feed and the SBIR file). Count it once.
    by_award: dict[tuple[str, str], float] = {}
    for s in signals:
        v = strength_asof(s, asof)
        if v <= 0.005:
            continue
        key = s.get("metrics", {}).get("award_key")
        if key:
            k = (s["family"], str(key))
            by_award[k] = max(by_award.get(k, 0.0), v)
        else:
            by_family[s["family"]].append(v)
    for (family, _), v in by_award.items():
        by_family[family].append(v)
    families = {f: round(noisy_or(by_family.get(f, [])), 4) for f in FAMILIES}
    lifted = [min(FAMILY_CAP, FAMILY_WEIGHTS[f] * FAMILY_GAIN) * families[f] for f in FAMILIES]
    momentum = noisy_or(lifted)
    return {
        "families": families,
        "momentum": round(momentum, 4),
        "convergence": sum(1 for v in families.values() if v >= FIRING),
    }


# One family firing alone is a lead, not a finding: anyone can file a
# trademark or write the regulator a letter. Uncorroborated rows are marked
# down so that a second independent source is what moves a company up.
UNCORROBORATED = 0.85


def edge_of(momentum: float, fit: float, earliness: float, team: float, families: int = 2) -> float:
    lone = UNCORROBORATED if families < 2 else 1.0
    return round(
        100.0 * momentum * (0.25 + 0.75 * fit) * (0.4 + 0.6 * earliness) * (0.85 + 0.15 * team) * lone, 2
    )


# Portfolio names distinctive enough to match loosely ("Anduril Industries").
# The rest ("Archive", "Natural", "Orbital") are common words and match only
# on the exact name.
_PORTFOLIO_LOOSE = {"anduril", "saronic", "helion", "spacex", "openai", "elevenlabs", "polymarket",
                    "efference", "erebor", "etched", "cognition", "physical intelligence",
                    "general galactic", "general matter", "kela", "eight sleep"}


def is_portfolio(name: str, aliases: list[str]) -> bool:
    held = {normalize_name(p) for p in PORTFOLIO}
    for n in [name, *aliases]:
        if normalize_name(n) in held or loose_name(n) in _PORTFOLIO_LOOSE:
            return True
    return False


def load_signals(conn: sqlite3.Connection, entity_id: int) -> list[dict]:
    out = []
    for r in conn.execute("SELECT * FROM signals WHERE entity_id=?", (entity_id,)):
        d = dict(r)
        d["metrics"] = json.loads(d["metrics"] or "{}")
        d["series"] = json.loads(d["series"] or "[]")
        d["people"] = json.loads(d["people"] or "[]")
        out.append(d)
    return out


def merge_people(signals: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for s in signals:
        for p in s["people"]:
            key = normalize_name(p.get("name") or "")
            if not key:
                continue
            cur = merged.setdefault(key, {"name": p["name"], "role": None, "github": None,
                                          "openalex_id": None, "affiliations": [], "links": {},
                                          "facts": {}, "sources": []})
            cur["role"] = cur["role"] or p.get("role")
            cur["github"] = cur["github"] or p.get("github")
            cur["openalex_id"] = cur["openalex_id"] or p.get("openalex_id")
            for a in p.get("affiliations") or []:
                if a and a not in cur["affiliations"]:
                    cur["affiliations"].append(a)
            cur["links"].update(p.get("links") or {})
            for k, v in (p.get("facts") or {}).items():
                if v is not None and (k not in cur["facts"] or isinstance(v, (int, float))
                                      and isinstance(cur["facts"][k], (int, float)) and v > cur["facts"][k]):
                    cur["facts"][k] = v
            if s["source"] not in cur["sources"]:
                cur["sources"].append(s["source"])
    return list(merged.values())


def thesis_of(entity: dict, signals: list[dict]) -> dict:
    """Classify a company from what is written about it.

    Not from the boilerplate of its job posts: a reactor company hiring
    welders is still an energy company. Hiring text is a fallback for
    companies that have nothing else.
    """
    def is_hiring(s: dict) -> bool:
        return (s.get("family") or "") == "hiring"

    identity = [entity.get("name"), entity.get("one_liner"), entity.get("description"),
                *(s["title"] for s in signals if not is_hiring(s)),
                *(s.get("text") or "" for s in signals if not is_hiring(s))]
    thesis = classify(" \n ".join(filter(None, identity)))
    if thesis["fit"] < 0.3:
        everything = [*identity, *(s["title"] for s in signals if is_hiring(s)),
                      *(s.get("text") or "" for s in signals if is_hiring(s))]
        thesis = classify(" \n ".join(filter(None, everything)))
    return thesis


def score_entity(entity: dict, signals: list[dict], today: date) -> dict:
    rev = review.for_slug(entity.get("slug") or "")
    dropped = set(rev.get("drop_signals") or [])
    if dropped:
        signals = [s for s in signals if s.get("url") not in dropped]
    thesis = thesis_of(entity, signals)
    if rev.get("sector"):
        thesis = {**thesis, "sector": rev["sector"]}
    if rev.get("founded"):
        entity = {**entity, "founded": str(rev["founded"])}

    # Consensus uses the largest value any signal reported for each metric.
    metrics: dict[str, float] = {}
    for s in signals:
        for k, v in s["metrics"].items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                if k == "tranco_rank":
                    metrics[k] = min(metrics.get(k, v), v)
                else:
                    metrics[k] = max(metrics.get(k, v), v)
    if rev.get("raised_usd"):
        metrics["amount_sold"] = max(metrics.get("amount_sold") or 0, float(rev["raised_usd"]))
    consensus, consensus_parts = consensus_score(
        metrics, entity.get("founded"), today,
        has_handle=bool(entity.get("domain") or entity.get("github")), stage=rev.get("stage"))
    earliness = round(1.0 - consensus, 4)

    drop_people = {normalize_name(n) for n in rev.get("drop_people") or []}
    people = [p for p in merge_people(signals) if normalize_name(p["name"]) not in drop_people]
    team, people = team_score(people)

    now = score_asof(signals, today)
    edge = edge_of(now["momentum"], thesis["fit"], earliness, team, now["convergence"])

    history = []
    for w in range(HISTORY_WEEKS, -1, -1):
        d = today - timedelta(days=7 * w)
        m = score_asof(signals, d)
        history.append({"t": d.isoformat(),
                        "edge": edge_of(m["momentum"], thesis["fit"], earliness, team, m["convergence"]),
                        "momentum": m["momentum"]})

    return {
        "edge": edge,
        "momentum": now["momentum"],
        "fit": thesis["fit"],
        "earliness": earliness,
        "team": team,
        "sector": thesis["sector"],
        "sectors": thesis["sectors"],
        "terms": thesis["terms"],
        "families": now["families"],
        "convergence": now["convergence"],
        "consensus_parts": consensus_parts,
        "metrics": metrics,
        "history": history,
        "people": people,
    }


def score_all(conn: sqlite3.Connection, run_id: int, today: date) -> int:
    conn.execute("DELETE FROM scores WHERE run_id=?", (run_id,))
    n = 0
    for e in conn.execute("SELECT * FROM entities").fetchall():
        ent = dict(e)
        sigs = load_signals(conn, ent["id"])
        if not sigs:
            continue
        sc = score_entity(ent, sigs, today)
        conn.execute(
            "INSERT INTO scores (entity_id, run_id, edge, momentum, fit, earliness, team,"
            " sector, breakdown) VALUES (?,?,?,?,?,?,?,?,?)",
            (ent["id"], run_id, sc["edge"], sc["momentum"], sc["fit"], sc["earliness"],
             sc["team"], sc["sector"], json.dumps(sc)),
        )
        n += 1
    conn.commit()
    return n

