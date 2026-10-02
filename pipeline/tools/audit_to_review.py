"""Turn audit findings into entries in review.json.

The audit is a set of structured findings per company (verdict, stage,
founding year, money raised, better name, people who do not belong). This
merges them into `pipeline/review.json`, which the scorer and the exporter
apply. Existing entries are kept; a field already set by hand is not
overwritten.

    python3 tools/audit_to_review.py ../docs/sources/_audit.json [more.json]
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVIEW = ROOT / "review.json"
SECTORS = {"robotics", "autonomy", "defense", "energy", "manufacturing", "semiconductors", "space"}
STAGES = {"formation", "early", "growth", "incumbent"}
URL = re.compile(r"https?://[^\s)\]'\"]+")


def person_name(text: str) -> str | None:
    """'Jane Doe - she is outside counsel' gives 'Jane Doe'."""
    head = re.split(r"\s+[-–—:(]\s*|\s*\(", text.strip(), maxsplit=1)[0].strip(" .,")
    words = head.split()
    return head if 1 < len(words) <= 5 and len(head) <= 60 else None


def sentence(text: str, limit: int = 320) -> str:
    """A note fit to show: URLs and their brackets removed, one tidy line."""
    s = re.sub(r"\s*\(\s*" + URL.pattern + r"\s*\)", "", text)
    s = URL.sub("", s)
    s = re.sub(r"\s+", " ", s).strip(" ;,")
    s = re.sub(r"\s+([.,;])", r"\1", s)
    if len(s) > limit:
        s = s[:limit].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"
    return s


def entry_from(e: dict) -> dict:
    out: dict = {}
    if e.get("verdict") == "exclude":
        out["exclude"] = sentence(e.get("excludeReason") or "Removed on review", 260)
    if e.get("stage") in STAGES:
        out["stage"] = e["stage"]
    if e.get("foundedYear"):
        out["founded"] = int(e["foundedYear"])
    if e.get("totalRaisedUsd"):
        out["raised_usd"] = float(e["totalRaisedUsd"])
    evidence = e.get("stageEvidence") or ""
    urls = URL.findall(evidence)
    src = (e.get("sourceUrl") or "").strip() or (urls[0].rstrip(".,;") if urls else "")
    if src:
        out["source"] = src
    note = sentence(e.get("note") or "") if "partner" in (e.get("_note_kind") or "") else sentence(evidence)
    if note:
        out["note"] = note
    if e.get("betterName"):
        out["name"] = e["betterName"].strip()
    if not e.get("sectorCorrect", True) and (e.get("betterSector") or "").strip().lower() in SECTORS:
        out["sector"] = e["betterSector"].strip().lower()
    people = [n for n in (person_name(p) for p in e.get("wrongPeople") or []) if n]
    if people:
        out["drop_people"] = people
    if e.get("wrongSignalUrls"):
        out["drop_signals"] = [u for u in e["wrongSignalUrls"] if u.startswith("http")]
    if (e.get("duplicateOf") or "").strip():
        out["merge_into"] = e["duplicateOf"].strip()
    if (e.get("domain") or "").strip():
        out["domain"] = e["domain"].strip().lower()
    if (e.get("location") or "").strip():
        out["location"] = e["location"].strip()
    return out


def main(paths: list[str]) -> int:
    review = json.loads(REVIEW.read_text()) if REVIEW.exists() else {}
    added = updated = 0
    for path in paths:
        data = json.loads(Path(path).read_text())
        partner_notes = "audit2" in path  # the second audit wrote notes for a partner to read
        for e in data["entities"]:
            if partner_notes:
                e["_note_kind"] = "partner"
            # Every audited company gets an entry, even when nothing needed
            # correcting: the export only ranks what has been reviewed.
            new = {"reviewed": True, **entry_from(e)}
            cur = review.get(e["slug"])
            if cur is None:
                review[e["slug"]] = new
                added += 1
            else:
                before = dict(cur)
                for k, v in new.items():
                    cur.setdefault(k, v)
                updated += cur != before
    REVIEW.write_text(json.dumps(dict(sorted(review.items())), indent=1, ensure_ascii=False) + "\n")
    stages = {}
    for v in review.values():
        stages[v.get("stage", "none")] = stages.get(v.get("stage", "none"), 0) + 1
    print(f"{len(review)} entries ({added} added, {updated} updated); "
          f"{sum(1 for v in review.values() if v.get('exclude'))} excluded; stages {stages}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
