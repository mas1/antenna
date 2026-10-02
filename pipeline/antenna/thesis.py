"""Thesis fit: is this text about the physical world Anti Fund is underwriting?

A transparent keyword model on purpose. A partner can read exactly why a
company scored the way it did, and tuning it is editing a list in config.py.
"""

from __future__ import annotations

import math
import re
from functools import lru_cache

from .config import THESIS, THESIS_FALSE_FRIENDS, THESIS_UNAMBIGUOUS

STRONG_W = 1.0
CONTEXT_W = 0.45

# A lone keyword is weak evidence: "fusion" is a brand name, a data
# technique and a restaurant style. Unless the one matched term is
# unmistakably physical, fit stays just under the 0.3 gate the rest of the
# pipeline uses, so it takes a second term or a second source to get in.
LONE_TERM_FIT = 0.28


def term_pattern(term: str) -> re.Pattern[str]:
    """Compile one thesis term.

    Spaces and hyphens inside a term are interchangeable ("sim-to-real",
    "sim to real", "simtoreal" for hyphens), a plural is allowed
    ("cubesats", "foundries"), and the match must sit on word boundaries.
    """
    # "factory" also covers "factories"; everything else takes s or es.
    stem, plural = (term[:-1], r"(?:y|ys|ies)") if term.endswith("y") and len(term) > 3 else (term, r"(?:s|es)?")
    parts = []
    for ch in stem:
        if ch == " ":
            parts.append(r"[\s\-]+")
        elif ch == "-":
            parts.append(r"[\s\-]?")
        else:
            parts.append(re.escape(ch))
    return re.compile(rf"(?<![a-z0-9]){''.join(parts)}{plural}(?![a-z0-9])", re.I)


@lru_cache(maxsize=1)
def _patterns() -> dict[str, list[tuple[re.Pattern[str], float, str]]]:
    out: dict[str, list[tuple[re.Pattern[str], float, str]]] = {}
    for sector, groups in THESIS.items():
        pats = []
        for group, weight in (("strong", STRONG_W), ("context", CONTEXT_W)):
            for term in groups.get(group, []):
                pats.append((term_pattern(term), weight, term))
        out[sector] = pats
    return out


@lru_cache(maxsize=1)
def _false_friends() -> re.Pattern[str]:
    alts = "|".join(term_pattern(p).pattern for p in THESIS_FALSE_FRIENDS)
    return re.compile(alts, re.I)


def classify(text: str | None) -> dict:
    """Score a blob of text against every thesis sector.

    Returns {"sector": best sector or "other", "fit": 0..1,
             "sectors": {sector: score}, "terms": [matched terms]}.
    Fit saturates: one stray keyword is weak evidence, four distinct ones are
    near-certain.
    """
    empty = {"sector": "other", "fit": 0.0, "sectors": {}, "terms": []}
    if not text:
        return empty
    # Blank out phrases that contain a thesis word but mean something else
    # ("sensor fusion", "hydrogen peroxide", "solar wind").
    text = _false_friends().sub(" ", text)

    scores: dict[str, float] = {}
    matched: dict[str, list[str]] = {}
    for sector, pats in _patterns().items():
        total = 0.0
        terms = []
        for pat, weight, term in pats:
            if pat.search(text):
                total += weight
                terms.append(term)
        if total:
            scores[sector] = total
            matched[sector] = terms
    if not scores:
        return empty

    best = max(scores, key=scores.get)  # type: ignore[arg-type]
    # Evidence across sectors compounds a little: a defense drone company
    # hits two lists and that is a stronger fit, not a confused one.
    mass = scores[best] + 0.35 * sum(v for k, v in scores.items() if k != best)
    fit = 1.0 - math.exp(-mass / 1.6)
    all_terms = sorted({t for ts in matched.values() for t in ts})
    if len(all_terms) == 1 and all_terms[0] not in THESIS_UNAMBIGUOUS:
        fit = min(fit, LONE_TERM_FIT)
    norm = {k: round(1.0 - math.exp(-v / 1.6), 3) for k, v in scores.items()}
    return {
        "sector": best,
        "fit": round(min(fit, 1.0), 3),
        "sectors": norm,
        "terms": all_terms[:12],
    }


def is_on_thesis(text: str | None, threshold: float = 0.3) -> bool:
    return classify(text)["fit"] >= threshold
