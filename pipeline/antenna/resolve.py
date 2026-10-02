"""Entity resolution: many hints, one company.

A Form D names "Acme Robotics, Inc.", a GitHub org is `acme-robotics` with
blog acme.bot, a job board lives at jobs.ashbyhq.com/acme. This module
decides those are one entity. Domain and GitHub login are hard keys; a
normalized name is a soft key that merges only when nothing contradicts it.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter, defaultdict

from . import review
from .collectors.base import clean_domain, display_name, normalize_name

# Names too generic to merge on by themselves.
_WEAK_NAMES = {
    "", "ai", "labs", "lab", "robotics", "systems", "the", "app", "data", "demo", "test",
    "core", "atlas", "apex", "nova", "orbit", "alpha", "beta", "delta", "sigma", "omega",
    "vector", "matrix", "pilot", "titan", "aurora", "horizon", "summit", "vertex", "nexus",
    "stealth", "stealth startup", "unknown", "n a", "na", "none", "self", "independent",
}

# Which source's name and description to trust first when hints disagree.
_NAME_PRIORITY = [
    "yc_directory", "accelerators", "hn_launch", "ats_jobs", "hn_hiring",
    "github_velocity", "research_affil", "nrc_adams", "fcc_els", "faa_uas",
    "uspto_trademarks", "energy_grants", "sbir_awards", "fpds_ot", "sec_form_d",
]


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[int, int] = {}

    def find(self, x: int) -> int:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s[:60] or "entity"


_REGISTRY_SOURCES = {"sec_form_d", "fpds_ot", "sbir_awards", "energy_grants", "fcc_els",
                     "faa_uas", "uspto_trademarks", "nrc_adams"}

_LINER_PRIORITY = [
    "yc_directory", "accelerators", "hn_launch", "web_presence", "github_velocity",
    "hn_hiring", "ats_jobs", "energy_grants", "sbir_awards", "research_affil",
]


def _liner_priority(source: str) -> int:
    try:
        return _LINER_PRIORITY.index(source)
    except ValueError:
        return len(_LINER_PRIORITY)


def _priority(source: str) -> int:
    try:
        return _NAME_PRIORITY.index(source)
    except ValueError:
        return len(_NAME_PRIORITY)


def resolve(conn: sqlite3.Connection) -> int:
    """Rebuild the entities table from all stored signals. Returns entity count."""
    rows = conn.execute("SELECT id, source, hint, occurred_at, metrics FROM signals").fetchall()
    hints = {}
    for r in rows:
        h = json.loads(r["hint"])
        h["domain"] = clean_domain(h.get("domain"))
        h["github"] = (h.get("github") or "").strip().lower() or None
        h["_norm"] = normalize_name(h.get("name") or "")
        h["_source"] = r["source"]
        h["_occurred"] = r["occurred_at"]
        # Government identifiers are exact: one UEI or SEC CIK is one company.
        m = json.loads(r["metrics"] or "{}")
        h["_ids"] = [f"{k}:{str(m[k]).strip().upper()}" for k in ("uei", "cik") if m.get(k)]
        hints[r["id"]] = h

    uf = _UnionFind()
    by_hard: dict[str, int] = {}
    for sid, h in hints.items():
        uf.find(sid)
        for key in (h["domain"] and f"d:{h['domain']}", h["github"] and f"g:{h['github']}", *h["_ids"]):
            if not key:
                continue
            if key in by_hard:
                uf.union(sid, by_hard[key])
            else:
                by_hard[key] = sid

    # Soft merge on name. Group the hard clusters by every normalized name
    # they carry, then merge clusters under one name unless two of them hold
    # different domains (two real companies that share a name).
    def clusters() -> dict[int, list[int]]:
        out: dict[int, list[int]] = defaultdict(list)
        for sid in hints:
            out[uf.find(sid)].append(sid)
        return out

    name_to_roots: dict[str, set[int]] = defaultdict(set)
    for root, members in clusters().items():
        for sid in members:
            h = hints[sid]
            names = {h["_norm"]} | {normalize_name(a) for a in h.get("aliases") or []}
            for n in names:
                # Three characters is enough when one is a digit ("cx2").
                long_enough = len(n) >= 4 or (len(n) == 3 and any(c.isdigit() for c in n))
                if n and n not in _WEAK_NAMES and long_enough:
                    name_to_roots[n].add(root)

    for name, roots in name_to_roots.items():
        live = {uf.find(r) for r in roots}
        if len(live) < 2:
            continue
        cl = clusters()
        domains_of = {r: {hints[s]["domain"] for s in cl[r] if hints[s]["domain"]} for r in live}
        all_domains = set().union(*domains_of.values())
        if len(all_domains) <= 1:
            first, *rest = sorted(live)
            for r in rest:
                uf.union(first, r)
        else:
            # Ambiguous: only fold together the clusters with no domain.
            bare = sorted(r for r in live if not domains_of[r])
            for r in bare[1:]:
                uf.union(bare[0], r)

    final = clusters()
    conn.execute("UPDATE signals SET entity_id = NULL")
    conn.execute("DELETE FROM scores")
    conn.execute("DELETE FROM entities")
    used_slugs: Counter[str] = Counter()

    for root in sorted(final):
        members = sorted(final[root], key=lambda s: (_priority(hints[s]["_source"]), s))
        hs = [hints[s] for s in members]
        kinds = {h.get("kind") or "company" for h in hs}
        kind = "company" if "company" in kinds else ("project" if "project" in kinds else "person")
        # Name: the most trusted source's name; ties broken by frequency.
        names = Counter(h["name"].strip() for h in hs if h.get("name"))
        best = hs[0]["name"].strip()
        if kind == "company":
            company_names = [h["name"].strip() for h in hs if (h.get("kind") or "company") == "company"]
            if company_names:
                best = company_names[0]
        # Registries shout; companies that style themselves in capitals
        # ("NODA AI") do so on purpose. Only recase names a registry gave us.
        named_by = next((h["_source"] for h in hs if h["name"].strip() == best), hs[0]["_source"])
        best = display_name(best, recase=named_by in _REGISTRY_SOURCES) or best
        domain = next((h["domain"] for h in hs if h["domain"]), None)
        github = next((h["github"] for h in hs if h["github"]), None)
        # A company's own words beat a registry's: prefer launch posts and the
        # company site, and fall back to goods lists and filings last.
        liners = sorted((h for h in hs if (h.get("one_liner") or "").strip()),
                        key=lambda h: _liner_priority(h["_source"]))
        one_liner = liners[0]["one_liner"].strip() if liners else None
        description = max((h.get("description") or "" for h in hs), key=len) or None
        location = next((h["location"] for h in hs if h.get("location")), None)
        founded = min((str(h["founded"]) for h in hs if h.get("founded")), default=None)
        links: dict[str, str] = {}
        for h in reversed(hs):
            links.update({k: v for k, v in (h.get("links") or {}).items() if v})
        if domain and "website" not in links:
            links["website"] = f"https://{domain}"
        if github and "github" not in links:
            links["github"] = f"https://github.com/{github}"
        aliases = sorted({n for n in names if n != best} | {a for h in hs for a in (h.get("aliases") or [])})
        first_seen = min(h["_occurred"] for h in hs)

        slug = slugify(best)
        used_slugs[slug] += 1
        if used_slugs[slug] > 1:
            slug = f"{slug}-{used_slugs[slug]}"

        cur = conn.execute(
            "INSERT INTO entities (slug, name, kind, domain, github, one_liner, description,"
            " location, founded, links, aliases, first_seen) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (slug, best, kind, domain, github, one_liner, description, location, founded,
             json.dumps(links), json.dumps(aliases[:12]), first_seen),
        )
        eid = cur.lastrowid
        conn.executemany("UPDATE signals SET entity_id=? WHERE id=?", [(eid, s) for s in members])

    conn.commit()
    return len(final) - _apply_review(conn)


def _apply_review(conn: sqlite3.Connection) -> int:
    """Apply identity corrections from the review file. Returns merges made.

    A reviewer can say that two rows are one company (a legal name and a
    brand name), or supply a website or a location the filings got wrong.
    Scoring corrections are applied later, in score.py.
    """
    merged = 0
    by_slug = {r["slug"]: r["id"] for r in conn.execute("SELECT id, slug FROM entities")}
    for slug, rev in review.load().items():
        eid = by_slug.get(slug)
        if eid is None:
            continue
        target = by_slug.get(rev.get("merge_into") or "")
        if target and target != eid:
            conn.execute("UPDATE signals SET entity_id=? WHERE entity_id=?", (target, eid))
            conn.execute("DELETE FROM entities WHERE id=?", (eid,))
            merged += 1
            continue
        if rev.get("domain") and clean_domain(rev["domain"]):
            dom = clean_domain(rev["domain"])
            links = json.loads(conn.execute("SELECT links FROM entities WHERE id=?", (eid,)).fetchone()[0] or "{}")
            links["website"] = f"https://{dom}"
            conn.execute("UPDATE entities SET domain=?, links=? WHERE id=? AND domain IS NULL",
                         (dom, json.dumps(links), eid))
        if rev.get("location"):
            conn.execute("UPDATE entities SET location=? WHERE id=?", (rev["location"], eid))
    conn.commit()
    return merged
