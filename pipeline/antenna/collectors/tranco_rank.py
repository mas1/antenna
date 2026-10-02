"""Domain rank momentum on the Tranco full list, for entities found elsewhere.

Tranco publishes a free daily ranking of about 4.6 million registrable domains
(a 30-day blend of Chrome usage, Cloudflare Radar, Cisco Umbrella DNS, Farsight
passive DNS and Majestic backlinks) and keeps every past day's list
downloadable. For each known entity with a domain this reads its rank on the
newest list and on the lists about 30 and 90 days before it. It is early
because most young hard-tech companies rank between 1,000,000 and 3,000,000,
below the cut-off of every top-1M list, so a first listing or a climb there is
on record while the usual traffic tools still show nothing. It corroborates
entities found elsewhere; it does not discover them.

What is emitted, one signal per entity found on the newest list:

  tranco_new_entrant   on the newest list, absent from both older lists. The
                       day it appeared comes from Tranco's per-domain history
                       (39 daily points); occurred_at is that day. If the
                       history shows the domain was listed shortly before the
                       30-day list it is a re-listing and is demoted to
                       tranco_rank_present. "New" means new against those
                       three checks, not first ever: measured on 2026-10-01,
                       12% of the domains absent from both older lists were
                       on the list of 2026-08-02 or 2026-04-04. So the title
                       says "appeared", and the strength stays below the
                       first-ever tier unless the entry is into ranks where
                       almost nothing is new. When the history gives no day
                       (budget spent, or the call failed) the entry is known
                       only as a difference between lists: occurred_at is the
                       newest list's date and metrics.rolling is set.
  tranco_rank_move     the rank improved by more than 90% of the sites that
                       held a similar rank on the older list did, over 30 or
                       90 days. occurred_at is the newest list's date, and
                       metrics.rolling is set.
  tranco_rank_present  ranked, no material move. Strength 0.0 and
                       metrics.observed_only: it is a reading, not an event,
                       and exists only to carry metrics.tranco_rank to the
                       scorer's consensus term. It adds nothing to momentum
                       and is stored as one row per entity that each run
                       refreshes.

A move and an undated entry are both read off "the newest list against older
ones", so their date moves forward with every daily run while the same climb
or the same entry is still what is being described. metrics.rolling tells the
store to keep one row per entity and kind and refresh it, so a domain that
climbs for three weeks is one move, not twenty-one. An entry dated from the
history keeps its own day and stays a point event.

The per-domain history page is the evidence link. It shows the newest rank,
the 30-day rank and the day a domain appeared. A 90-day rank is not on that
page (it only goes back 39 days); it comes from the dated list, whose page is
in metrics.list_url_90d.

"Similar rank" is measured, not assumed. Every list is read once as a stream;
alongside the wanted domains a fixed 1-in-64 hash sample of all domains is
kept (about 70,000 per list, the same domains in every list), which gives the
rank-ratio distribution of each rank band for that exact pair of lists. The
source card's table (30-day p50 0.99, p90 1.25 to 1.54) is the fallback when
a band has too few sampled sites, and the live numbers reproduce it.

The ratio is old rank / new rank, so above 1 is an improvement. A domain is
looked up exactly as known; a subdomain is never reduced to its parent, so a
project hosted on someone else's domain cannot inherit that domain's rank.
For the same reason a top-100,000 domain is used only when it carries the
entity's name, and shared hosts and institutional domains are never looked up.

Requests per run: three metadata calls, up to three list downloads of about
105 MB each (none when the files are on disk), and one per-domain history
call, 2.2 seconds apart, for each new entrant up to MAX_HISTORY_LOOKUPS.
"""

from __future__ import annotations

import math
import re
import time
import urllib.parse
import zlib
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import accumulate
from pathlib import Path
from typing import Any, Iterable

from .. import http
from ..config import DOWNLOADS_DIR
from ..models import EntityHint, Signal
from .base import Context, clean_domain, parse_date

SLUG = "tranco_rank"
FAMILY = "traffic"
STAGE = "enrich"
DESCRIPTION = "Tranco full-list rank of known domains: 30 and 90 day moves against same-depth sites, new entrants"

LIST_META_URL = "https://tranco-list.eu/api/lists/date/{}"
LIST_DOWNLOAD_URL = "https://tranco-list.eu/download/{}/full"
LIST_PAGE_URL = "https://tranco-list.eu/list/{}/full"
DOMAIN_API_URL = "https://tranco-list.eu/api/ranks/domain/{}"

WINDOWS = (30, 90)                    # days before the newest list
DATE_SLACK = (0, -1, 1, -2, 2, -3, 3)  # tried in order when a day has no list
STALE_AFTER_DAYS = 7                  # warn when the newest list is older than this

LATEST_TTL = 3600                     # the "latest" pointer moves once a day
DATED_TTL = 7 * 86400                 # a dated list's metadata never changes
HISTORY_TTL = 6 * 3600
# A list file is immutable once published, the newest one included: the file
# name carries the list id, and a new day is a new id. Never fetched twice.
FOREVER = 50 * 365 * 86400

# Full lists measured between 4.06M and 4.60M rows over the past year. Fewer
# rows than this means a truncated download or the top-1M variant, and absence
# from such a file would not mean absence from the ranking.
MIN_FULL_ROWS = 3_000_000

SAMPLE_MASK = 63      # keep domains whose crc32 has its low six bits clear: 1 in 64
COHORT_BAND = 1.5     # "similar rank" = within a factor of 1.5 either way
COHORT_MAX = 4000     # then the nearest this many sampled sites
COHORT_MIN = 200      # fewer than this and the band is not trusted

MATERIAL_PCT = 90.0   # the move must beat this share of its cohort
MIN_RATIO = {30: 1.2, 90: 1.3}  # and be at least this large in absolute terms
SUSTAINED_BONUS = 0.06          # both windows material
STALE_WINDOW_FACTOR = 0.9       # a move that only shows over 90 days is less fresh
MAX_MOVE_STRENGTH = 0.9
# tranco_rank_present is a reading, not an event: it carries metrics only.
PRESENT_STRENGTH = 0.0

MAX_HISTORY_LOOKUPS = 60  # per-domain API calls per run, for new entrants only
API_SPACING = 2.2         # seconds between per-domain calls: the card saw 429s at 1.2 s, none at 2.2 s

# The source card's cohort table (survivors only), measured 2026-10-01 from
# four full lists: (old rank from, to, p50, p90, p99) of old rank / new rank.
CARD_NORMS: dict[int, list[tuple[int, int, float, float, float]]] = {
    30: [
        (300_000, 1_000_000, 0.99, 1.25, 2.63),
        (1_000_000, 2_000_000, 0.99, 1.29, 3.09),
        (2_000_000, 3_500_000, 0.97, 1.54, 4.55),
    ],
    90: [
        (1_000_000, 2_000_000, 0.94, 1.40, 3.88),
        (2_000_000, 3_500_000, 0.96, 1.83, 6.59),
    ],
}

# Share of sites at a given depth on the newest list that were on neither
# older list, measured 2026-10-01 from lists Y83YG, K9QPW and N2Y7W with the
# same 1-in-64 sample. Used only when the live sample for a band is too thin.
STATIC_ENTRANT_RATE = [
    (100_000, 0.006), (1_000_000, 0.019), (2_000_000, 0.071), (3_500_000, 0.26),
]
STATIC_ENTRANT_RATE_DEEP = 0.50

# -log10(share of the cohort that did at least as well) -> strength.
# 1.0 is the 90th percentile, 2.0 the 99th, 3.0 the 99.9th.
_MOVE_ANCHORS = [(1.0, 0.30), (1.3, 0.42), (2.0, 0.60), (3.0, 0.78), (4.0, 0.88)]
# log10(share of same-depth sites that are also new) -> strength. Entering in
# the top million is rare (about 2% of sites there are new); past rank 2.5M a
# fifth to a half of the list is new and an entry says little. The share is
# measured around the domain's own rank because it is not smooth: on
# 2026-10-01 it was 43% at rank 2.0M and 6% at 2.2M.
#
# The scale sits 0.05 to 0.2 above a rank move of the same rarity (a 1-in-50
# move scores about 0.5, a 1-in-50 entry 0.7) and stops at 0.85. It is not
# the contract's first-ever tier: absence is only checked 30 and 90 days back
# and over the 39-day history, and one in eight such "entries" was listed
# earlier in the year (zenopower.com and diracinc.com were both ranked on
# 2026-04-04).
_ENTRANT_ANCHORS = [
    (math.log10(0.005), 0.85), (math.log10(0.02), 0.70), (math.log10(0.07), 0.42),
    (math.log10(0.26), 0.22), (math.log10(0.50), 0.15),
]
MAX_ENTRANT_STRENGTH = 0.85
# Days listed since entry -> multiplier. A listing a day or two old has not yet
# shown it will stay; after a week it has.
_YOUTH_ANCHORS = [(1.0, 0.85), (7.0, 1.0)]
UNDATED_ENTRY_FACTOR = 0.9  # entry seen only as a difference between lists
# Final rank -> multiplier. Deep ranks are thin evidence however large the ratio.
_DEPTH_ANCHORS = [(1_000_000, 1.0), (2_000_000, 0.9), (3_000_000, 0.8), (4_000_000, 0.7)]

# Registrable domains that host many unrelated tenants, or list companies
# rather than belong to one. A known entity carrying one of these as its
# "domain" must not inherit the host's rank. base.clean_domain drops the
# common ones (pages.dev, web.app, itch.io, kickstarter.com, ...) and their
# tenants; these are the ones it does not know. All of them rank in the top
# 120,000 on the list of 2026-10-01.
_SHARED_HOSTS = frozenset({
    "appspot.com", "repl.co", "glitch.me", "surge.sh", "bitbucket.io", "gitbook.io",
    "framer.app", "super.site", "wix.com", "weebly.com", "godaddysites.com",
    "myshopify.com", "blogspot.com", "tumblr.com", "azurewebsites.net", "cloudfront.net",
    "amazonaws.com", "pitchbook.com", "devpost.com", "patreon.com", "researchgate.net",
    "openreview.net",
})
# A top-100k domain is checked against the entity's name before it is used.
# At that rank the scorer treats the company as widely known, and the usual
# cause of a tiny company "owning" such a domain is a website field that
# pointed at a scheduling, form or storefront platform.
OWN_DOMAIN_CHECK_RANK = 100_000
_NAME_STOPWORDS = frozenset({
    "the", "inc", "llc", "ltd", "corp", "company", "labs", "lab", "technologies", "technology",
    "tech", "systems", "group", "holdings", "and", "for", "com", "org", "net", "app", "www",
})
# Universities, governments and militaries: the domain is the institution's.
# Wider than base.clean_domain's suffix list (which stops at .edu, .gov, .mil
# and five national academic suffixes): this also covers .int and every
# country's ac/edu/gov/go/mil second level.
_INSTITUTION = re.compile(r"\.(?:edu|gov|mil|int)$|\.(?:ac|edu|gov|go|mil)\.[a-z]{2}$")
_DOMAIN_SHAPE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}$")
_LIST_ID = re.compile(r"^[A-Z0-9]{3,8}$")
_GITHUB_LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_FIRST_ROW = re.compile(rb"^1,[^,\s]+\s*$")


class ListError(ValueError):
    """A downloaded file is not a complete Tranco full list."""


@dataclass(frozen=True)
class TrancoList:
    list_id: str
    day: date  # the last day of the 30-day window the list covers


@dataclass
class ListScan:
    ranks: dict[str, int]     # wanted domain -> rank
    sample: dict[bytes, int]  # hash-sampled domain -> rank
    rows: int


@dataclass
class Window:
    """One older list, as it bears on one domain."""
    days: int                 # nominal: 30 or 90
    span: int                 # actual days between the two lists
    lst: TrancoList
    rank: int | None          # None: the domain is absent from that list
    ratio: float | None = None  # old rank / new rank
    pct: float | None = None    # share of the cohort this ratio beats, 0..100
    n: int | None = None        # cohort size
    basis: str | None = None    # "live" (measured this run) or "card" (static table)


@dataclass
class Entry:
    """When a new entrant first shows in the per-domain history."""
    day: date
    rank: int
    days_listed: int  # daily points from the entry day to the newest list
    span: int         # calendar days over the same stretch
    relisted: bool    # it was listed on or before the 30-day list's date


# ---------------------------------------------------------------- parsing

def lookup_domain(raw: Any) -> str | None:
    """The exact string to look for in the list, or None if there is none.

    Tranco ranks registrable domains only. A subdomain is deliberately not
    reduced to its parent: `lab.github.io` must not read github.io's rank.
    """
    host = clean_domain(raw if isinstance(raw, str) else None)
    if not host:
        return None
    host = host.strip(".")
    try:
        host = host.encode("idna").decode("ascii")
    except (UnicodeError, ValueError):
        return None
    if not _DOMAIN_SHAPE.match(host):
        return None
    if host in _SHARED_HOSTS or _INSTITUTION.search(host):
        return None
    return host


def looks_like_own_domain(name: str, domain: str) -> bool:
    """Does the domain plausibly carry this entity's name?

    True when the first label of the domain is the name, one of its words or
    its initials; when the whole name sits inside the domain; or when a
    distinctive part is shared (a label of four letters or more that starts
    or ends the name, a word of five or more inside the label). A fragment in
    the middle does not count: "lu" is inside "Blue Laser Fusion", "loom"
    inside "Bloom Energy" and "a" inside everything, but lu.ma, loom.com and
    a.co are nobody's company site. It only has to tell "Anduril /
    anduril.com" from "Acme Robotics / calendly.com".
    """
    host = re.sub(r"[^a-z0-9]", "", domain.lower())
    label = re.sub(r"[^a-z0-9]", "", domain.lower().split(".")[0])
    words = re.findall(r"[a-z0-9]+", name.lower())
    squashed = "".join(words)
    if not label or not squashed:
        return False
    if label == squashed or (len(label) >= 2 and label in words and label not in _NAME_STOPWORDS):
        return True
    if len(squashed) >= 4 and squashed in host:
        return True
    if len(label) >= 4 and (squashed.startswith(label) or squashed.endswith(label)):
        return True
    if any(len(w) >= 5 and w not in _NAME_STOPWORDS and w in label for w in words):
        return True
    initials = {"".join(w[0] for w in words), "".join(w[0] for w in words if w not in _NAME_STOPWORDS)}
    return len(label) >= 2 and label in initials


def github_login(raw: Any) -> str | None:
    """The known entity's GitHub login, passed through so the hint resolves; never a URL."""
    s = raw.strip() if isinstance(raw, str) else ""
    return s if _GITHUB_LOGIN.match(s) else None


def entity_hint(ent: dict, domain: str) -> EntityHint:
    """The known entity's own identifiers, as the resolver will see them elsewhere.

    The domain is the known entity's (cleaned the way every other collector
    cleans it), not the punycode or dot-stripped form used for the lookup, so
    this signal lands on the entity it was asked about.
    """
    raw = ent.get("domain")
    return EntityHint(name=str(ent["name"]).strip(), kind=str(ent.get("kind") or "company"),
                      domain=clean_domain(raw if isinstance(raw, str) else None) or domain,
                      github=github_login(ent.get("github")))


def known_targets(known: Iterable[dict], limit: int | None = None) -> list[tuple[str, dict]]:
    """(lookup domain, known entity) pairs, in the order given, one per domain.

    People are left out: a researcher's personal or lab site has a rank, but
    it is not a company's traffic and must not read as one.
    """
    out: list[tuple[str, dict]] = []
    seen: set[str] = set()
    for ent in known or []:
        if not isinstance(ent, dict) or not str(ent.get("name") or "").strip():
            continue
        if str(ent.get("kind") or "").strip().lower() == "person":
            continue
        dom = lookup_domain(ent.get("domain"))
        if not dom or dom in seen:
            continue
        seen.add(dom)
        out.append((dom, ent))
        if limit and len(out) >= limit:
            break
    return out


def parse_meta(meta: Any) -> TrancoList | None:
    """A usable list from /api/lists/date/..., or None."""
    if not isinstance(meta, dict) or meta.get("available") is not True or meta.get("failed"):
        return None
    list_id = str(meta.get("list_id") or "")
    if not _LIST_ID.match(list_id):
        return None
    conf = meta.get("configuration") or {}
    # endDate is the day the list is filed under (verified: /lists/date/D
    # returns the list whose endDate is D, and the per-domain API uses it too).
    day = parse_date(str(conf.get("endDate") or "")) or parse_date(str(meta.get("created_on") or "")[:10])
    if day is None:
        return None
    return TrancoList(list_id=list_id, day=day)


def parse_history(data: Any, domain: str | None = None) -> list[tuple[date, int]]:
    """Per-domain API body -> [(day, rank)] oldest first. Empty if unlisted."""
    if not isinstance(data, dict):
        return []
    if domain and data.get("domain") and str(data["domain"]).lower() != domain.lower():
        return []
    out: dict[date, int] = {}
    for p in data.get("ranks") or []:
        if not isinstance(p, dict):
            continue
        d = parse_date(str(p.get("date") or ""))
        r = p.get("rank")
        if d is None or isinstance(r, bool) or not isinstance(r, int) or r <= 0:
            continue
        out[d] = r
    return sorted(out.items())


def scan_list(path: Path | str, wanted: frozenset[bytes], *, min_rows: int | None = None) -> ListScan:
    """One streaming pass over a `rank,domain` CSV.

    Keeps the wanted domains and the 1-in-64 hash sample, nothing else. Raises
    ListError unless the file looks like a complete full list: first row is
    rank 1, ranks run to the row count, and there are at least `min_rows`.
    """
    if min_rows is None:
        min_rows = MIN_FULL_ROWS
    hits: list[tuple[bytes, bytes]] = []
    samp: list[tuple[bytes, bytes]] = []
    rows = 0
    rank = b""
    crc, mask = zlib.crc32, SAMPLE_MASK
    with open(path, "rb") as fh:
        first = fh.readline()
        if not _FIRST_ROW.match(first):
            raise ListError(f"{Path(path).name}: first row is not rank 1: {first[:60]!r}")
        fh.seek(0)
        for line in fh:
            line = line.rstrip()
            if not line:  # a blank last line is not a row and must not hide the last rank
                continue
            rank, _, dom = line.partition(b",")
            rows += 1
            if dom in wanted:
                hits.append((dom, rank))
            if not crc(dom) & mask:
                samp.append((dom, rank))
    if rows < min_rows:
        raise ListError(f"{Path(path).name}: {rows:,} rows, a full list has over {min_rows:,}")
    try:
        last = int(rank)
    except ValueError:
        raise ListError(f"{Path(path).name}: last row has no rank") from None
    if abs(last - rows) > max(5, rows // 200):
        raise ListError(f"{Path(path).name}: last rank {last:,} does not match {rows:,} rows")

    ranks: dict[str, int] = {}
    for dom, r in hits:
        try:
            ranks.setdefault(dom.decode("ascii"), int(r))
        except (ValueError, UnicodeDecodeError):
            continue
    sample: dict[bytes, int] = {}
    for dom, r in samp:
        if not dom:
            continue
        try:
            sample.setdefault(dom, int(r))
        except ValueError:
            continue
    return ListScan(ranks=ranks, sample=sample, rows=rows)


# ---------------------------------------------------------------- cohorts

class Cohort:
    """Rank ratios of the sampled sites present on both an old and a new list."""

    def __init__(self, old: dict[bytes, int], new: dict[bytes, int]):
        pairs = sorted((r, r / new[d]) for d, r in old.items() if d in new)
        self.ranks = [p[0] for p in pairs]
        self.ratios = [p[1] for p in pairs]

    def _band(self, rank: int) -> tuple[int, int]:
        lo = bisect_left(self.ranks, rank / COHORT_BAND)
        hi = bisect_right(self.ranks, rank * COHORT_BAND)
        mid = bisect_left(self.ranks, rank)
        return max(lo, mid - COHORT_MAX // 2), min(hi, mid + COHORT_MAX // 2)

    def percentile(self, old_rank: int, ratio: float) -> tuple[float, int] | None:
        """(share of similarly ranked survivors with a smaller ratio, cohort size)."""
        lo, hi = self._band(old_rank)
        n = hi - lo
        if n < COHORT_MIN:
            return None
        below = sum(1 for x in self.ratios[lo:hi] if x < ratio)
        return 100.0 * below / n, n


class EntrantRate:
    """Share of sampled sites near a rank on the newest list that are on no older list."""

    def __init__(self, new: dict[bytes, int], olds: list[dict[bytes, int]]):
        rows = sorted((r, 0 if any(d in o for o in olds) else 1) for d, r in new.items())
        self.ranks = [r for r, _ in rows]
        self.cum = [0, *accumulate(flag for _, flag in rows)]

    def rate(self, rank: int) -> tuple[float, int] | None:
        lo = bisect_left(self.ranks, rank / COHORT_BAND)
        hi = bisect_right(self.ranks, rank * COHORT_BAND)
        mid = bisect_left(self.ranks, rank)
        lo, hi = max(lo, mid - COHORT_MAX // 2), min(hi, mid + COHORT_MAX // 2)
        n = hi - lo
        if n < COHORT_MIN:
            return None
        return (self.cum[hi] - self.cum[lo]) / n, n


def card_percentile(old_rank: int, ratio: float, days: int) -> float:
    """Approximate cohort percentile from the source card's static table.

    The table gives p50, p90 and p99 per rank band. Between them the
    percentile is read off a straight line in log(ratio); past p99 the same
    log-linear tail continues and stops at 99.9, the most three numbers can
    support. A rank outside the measured bands uses the nearest band.
    """
    bands = CARD_NORMS[days]
    lo, hi, p50, p90, p99 = min(
        bands, key=lambda b: 0 if b[0] <= old_rank < b[1] else min(abs(old_rank - b[0]), abs(old_rank - b[1]))
    )
    if ratio <= 0:
        return 0.0
    if ratio <= p50:
        return 50.0 * ratio / p50
    if ratio <= p90:
        return 50.0 + 40.0 * math.log(ratio / p50) / math.log(p90 / p50)
    tail = 1.0 + math.log(ratio / p90) / math.log(p99 / p90)  # 1 at p90, 2 at p99
    return 100.0 - 100.0 * 10 ** -min(tail, 3.0)


def static_entrant_rate(rank: int) -> float:
    for ceiling, rate in STATIC_ENTRANT_RATE:
        if rank <= ceiling:
            return rate
    return STATIC_ENTRANT_RATE_DEEP


# ---------------------------------------------------------------- strength

def _interp(x: float, anchors: list[tuple[float, float]]) -> float:
    """Piecewise-linear through the anchors, flat beyond either end."""
    if x <= anchors[0][0]:
        return anchors[0][1]
    for (x0, y0), (x1, y1) in zip(anchors, anchors[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return anchors[-1][1]


def is_material(w: Window | None) -> bool:
    return bool(
        w and w.rank is not None and w.ratio is not None and w.pct is not None
        and w.pct >= MATERIAL_PCT and w.ratio >= MIN_RATIO[w.days]
    )


def window_strength(w: Window) -> float:
    """Strength of one material window, before the depth discount."""
    n = w.n or 1000  # the card table resolves to about one in a thousand
    tail = max(1.0 - (w.pct or 0.0) / 100.0, 0.5 / n)
    return _interp(-math.log10(tail), _MOVE_ANCHORS)


def move_strength(rank_now: int, w30: Window | None, w90: Window | None) -> tuple[float, Window | None]:
    """(strength, the window to headline) for a ranked domain; (0, None) if no material move.

    The 90-day window counts only while the last 30 days are not giving the
    gain back: a domain absent or better ranked 30 days ago is not climbing now.
    """
    s30 = window_strength(w30) if w30 is not None and is_material(w30) else 0.0
    s90 = 0.0
    if w90 is not None and is_material(w90) and (
            w30 is None or (w30.ratio is not None and w30.ratio >= 1.0)):
        s90 = window_strength(w90) * STALE_WINDOW_FACTOR
    if not s30 and not s90:
        return 0.0, None
    head = w30 if s30 >= s90 else w90
    s = max(s30, s90) + (SUSTAINED_BONUS if s30 and s90 else 0.0)
    s *= _interp(rank_now, _DEPTH_ANCHORS)
    return round(min(s, MAX_MOVE_STRENGTH), 3), head


def entrant_strength(rate: float, entry: Entry | None) -> float:
    """Rarer at that depth is stronger; a young or on-and-off listing is discounted."""
    s = _interp(math.log10(max(rate, 1e-4)), _ENTRANT_ANCHORS)
    if entry is None:
        s *= UNDATED_ENTRY_FACTOR
    else:
        if entry.span > 0:
            s *= 0.6 + 0.4 * min(1.0, entry.days_listed / entry.span)
        s *= _interp(entry.days_listed, _YOUTH_ANCHORS)
    return round(min(max(s, 0.15), MAX_ENTRANT_STRENGTH), 3)


def entry_from_history(points: list[tuple[date, int]], after: date, upto: date) -> Entry | None:
    """The first listing in the per-domain history, read against the 30-day list's date.

    `after` is the date of the 30-day list, on which the domain is known to be
    absent. A first point on or before it means the domain was listed, dropped
    and came back.
    """
    pts = [(d, r) for d, r in points if d <= upto]
    if not pts:
        return None
    day, rank = pts[0]
    recent = [p for p in pts if p[0] > after]
    if day <= after:
        if not recent:
            return None
        # "Back on the list since" must hold for every day since: take the
        # start of the unbroken daily run that reaches the latest point.
        start = len(recent) - 1
        while start > 0 and (recent[start][0] - recent[start - 1][0]).days == 1:
            start -= 1
        return Entry(day=recent[start][0], rank=recent[start][1], days_listed=len(recent),
                     span=(upto - recent[0][0]).days + 1, relisted=True)
    return Entry(day=day, rank=rank, days_listed=len(pts), span=(upto - day).days + 1, relisted=False)


# ---------------------------------------------------------------- wording

def _sites(rows: int) -> str:
    return f"{rows / 1e6:.1f}M" if rows >= 1_000_000 else f"{rows:,}"


def _pct_words(pct: float, n: int | None = None) -> str:
    """A whole percent a title can stand behind: rounded down, never above 99.

    The cohort is a sample of at most 4,000 sites, so the measured share is
    itself uncertain (about half a point at the 90th percentile). Checked
    against the full lists on 2026-10-01, plain rounding down overstated 2 of
    15 titles by one point. Two standard errors come off first, so the figure
    in the title is one the whole list supports.
    """
    if n:
        p = min(max(pct / 100.0, 0.0), 1.0)
        pct -= 200.0 * math.sqrt(p * (1.0 - p) / n)
    return str(max(0, min(99, math.floor(pct))))


def move_title(rank_now: int, head: Window, w30: Window | None = None) -> str:
    """The move in ranks a reader can find on the evidence page.

    That page (the per-domain history) goes back 39 days, so it shows the
    newest rank and the 30-day rank but not the 90-day one. When the 90-day
    window is the headline, the title still quotes the 30-day ranks and puts
    the 90-day climb in words; the 90-day rank itself is in the metrics, with
    the list it came from. Only with no 30-day list at all is it quoted.
    """
    pct = _pct_words(head.pct, head.n) if head.basis == "live" and head.pct is not None else None
    if head.days == 30 or w30 is None or w30.rank is None or w30.ratio is None:
        base = f"Domain rank rose from {head.rank:,} to {rank_now:,} in {head.span} days"
        return f"{base}, a bigger gain than {pct}% of similarly ranked sites" if pct else base
    climb = f"a {head.span}-day climb" + (f" that beat {pct}% of similar sites" if pct else "")
    if w30.ratio > 1.03:
        return f"Domain rank rose from {w30.rank:,} to {rank_now:,} in {w30.span} days, in {climb}"
    return f"Domain rank {rank_now:,} after {climb}, flat over the last {w30.span} days"


def present_title(rank_now: int, rows: int, w30: Window | None, w90: Window | None) -> str:
    here = f"Domain rank {rank_now:,} of {_sites(rows)} ranked sites"
    if w30 and w30.rank is not None and w30.ratio is not None:
        if w30.ratio > 1.03:
            base = f"Domain rank improved from {w30.rank:,} to {rank_now:,} in {w30.span} days"
            # "Normal" is a claim about the cohort: only made when the cohort bears it out.
            # (A small gain near the top can beat 90% of its cohort and still be under MIN_RATIO.)
            if w30.pct is not None and w30.pct < MATERIAL_PCT:
                return f"{base}, within the normal range"
            return base
        if w30.ratio < 0.97:
            return f"Domain rank slipped from {w30.rank:,} to {rank_now:,} in {w30.span} days"
        return f"{here}, flat against {w30.rank:,} on {w30.lst.day.isoformat()}"
    if w30 and w90 and w90.rank is not None:
        # The 90-day rank is in the metrics; the evidence page does not go back that far.
        return f"{here}, unranked on {w30.lst.day.isoformat()} and ranked on {w90.lst.day.isoformat()}"
    if w30:
        return f"{here}, unranked on {w30.lst.day.isoformat()}"
    if w90 and w90.rank is not None:
        return f"{here}, against {w90.rank:,} on {w90.lst.day.isoformat()}"
    if w90:
        return f"{here}, unranked on {w90.lst.day.isoformat()}"
    return here


def relisted_title(rank_now: int, rows: int, entry: Entry) -> str:
    return (f"Domain rank {rank_now:,} of {_sites(rows)} ranked sites, back on the list since "
            f"{entry.day.isoformat()} after dropping off")


def entrant_title(rank_now: int, w30: Window, w90: Window, entry: Entry | None) -> str:
    # "Appeared", not "entered": absence is known for the two older lists and
    # the days of history in between, not for all time.
    if entry:
        return (f"Appeared on the Tranco web ranking on {entry.day.isoformat()} and now ranks "
                f"{rank_now:,}, unranked {w30.span} and {w90.span} days earlier")
    return (f"Appeared on the Tranco web ranking in the last {w30.span} days and now ranks "
            f"{rank_now:,}, unranked {w90.span} days earlier too")


# ---------------------------------------------------------------- fetching

def fetch_meta(ctx: Context, key: str, ttl: float) -> TrancoList | None:
    """List metadata for "latest" or a YYYY-MM-DD day. None when there is no such list."""
    try:
        return parse_meta(http.get_json(LIST_META_URL.format(key), ttl=ttl))
    except http.HttpError as e:
        if e.status != 404:  # 404 {"available": false} is simply "no list that day"
            ctx.warn(f"tranco list metadata for {key}: HTTP {e.status}")
    except Exception as e:  # noqa: BLE001 - network or JSON trouble, carry on without it
        ctx.warn(f"tranco list metadata for {key}: {type(e).__name__}: {e}")
    return None


def list_near(ctx: Context, target: date, not_after: date | None = None) -> TrancoList | None:
    """The list filed under `target`, or the nearest day within three that has one."""
    for off in DATE_SLACK:
        day = target + timedelta(days=off)
        if not_after and day > not_after:
            continue
        lst = fetch_meta(ctx, day.isoformat(), DATED_TTL)
        if lst:
            return lst
    return None


def resolve_lists(ctx: Context) -> tuple[TrancoList, dict[int, TrancoList]] | None:
    """The newest list on or before ctx.today, and the lists about 30 and 90 days before it."""
    newest = fetch_meta(ctx, "latest", LATEST_TTL)
    if newest is None:
        ctx.warn("tranco: no latest list available, nothing emitted")
        return None
    if ctx.today < newest.day:  # a run "as of" an earlier date
        newest = list_near(ctx, ctx.today, not_after=ctx.today)
        if newest is None:
            ctx.warn(f"tranco: no list on or near {ctx.today.isoformat()}, nothing emitted")
            return None
    if newest.day < ctx.since:
        ctx.warn(f"tranco: newest list is dated {newest.day.isoformat()}, older than the lookback")
        return None
    if (ctx.today - newest.day).days > STALE_AFTER_DAYS:
        ctx.warn(f"tranco: newest list is dated {newest.day.isoformat()}, "
                 f"{(ctx.today - newest.day).days} days before today")
    older: dict[int, TrancoList] = {}
    for days in WINDOWS:
        lst = list_near(ctx, newest.day - timedelta(days=days))
        if lst is None or lst.day >= newest.day or lst.list_id == newest.list_id:
            ctx.warn(f"tranco: no list about {days} days before {newest.day.isoformat()}")
            continue
        older[days] = lst
    return newest, older


def list_path(lst: TrancoList) -> Path:
    return Path(DOWNLOADS_DIR) / f"tranco_full_{lst.day.isoformat()}_{lst.list_id}.csv"


def load_list(ctx: Context, lst: TrancoList, wanted: frozenset[bytes]) -> ListScan | None:
    """Download (once) and scan one full list. A bad file is fetched again, once."""
    url = LIST_DOWNLOAD_URL.format(lst.list_id)
    for attempt in (0, 1):
        try:
            path = http.download(url, list_path(lst), max_age=FOREVER if attempt == 0 else 0)
            return scan_list(path, wanted)
        except Exception as e:  # noqa: BLE001 - a list we cannot read is a list we do not use
            ctx.warn(f"tranco list {lst.list_id} ({lst.day.isoformat()}), attempt {attempt + 1}: "
                     f"{type(e).__name__}: {e}")
    return None


def fetch_history(domain: str) -> list[tuple[date, int]]:
    """Per-domain daily ranks, oldest first. Spaced to the card's measured limit."""
    before = http.stats()["requests"]
    t0 = time.monotonic()
    try:
        data = http.get_json(DOMAIN_API_URL.format(urllib.parse.quote(domain, safe="")), ttl=HISTORY_TTL)
    finally:
        # A cache hit returns at once and needs no pause. (The request counter
        # alone would not tell: other collectors share it.)
        elapsed = time.monotonic() - t0
        if http.stats()["requests"] > before and elapsed >= 0.05:
            time.sleep(max(0.0, API_SPACING - elapsed))
    return parse_history(data, domain)


# ---------------------------------------------------------------- signals

def make_window(days: int, newest: TrancoList, lst: TrancoList, rank_now: int, rank_then: int | None,
                cohort: Cohort | None) -> Window:
    w = Window(days=days, span=(newest.day - lst.day).days, lst=lst, rank=rank_then)
    if rank_then is None:
        return w
    w.ratio = rank_then / rank_now
    live = cohort.percentile(rank_then, w.ratio) if cohort else None
    if live:
        w.pct, w.n, w.basis = live[0], live[1], "live"
    else:
        w.pct, w.basis = card_percentile(rank_then, w.ratio, days), "card"
    return w


def base_metrics(rank_now: int, newest: TrancoList, rows: int, windows: list[Window]) -> dict[str, Any]:
    m: dict[str, Any] = {
        "tranco_rank": rank_now,
        "tranco_list_id": newest.list_id,
        "tranco_list_date": newest.day.isoformat(),
        "tranco_list_size": rows,
        "tranco_list_url": LIST_PAGE_URL.format(newest.list_id),
    }
    for w in windows:
        tag = f"{w.days}d"
        m[f"list_id_{tag}"] = w.lst.list_id
        m[f"list_date_{tag}"] = w.lst.day.isoformat()
        m[f"list_url_{tag}"] = LIST_PAGE_URL.format(w.lst.list_id)
        m[f"window_days_{tag}"] = w.span
        m[f"listed_{tag}"] = w.rank is not None
        if w.rank is not None and w.ratio is not None:
            m[f"tranco_rank_{tag}"] = w.rank
            m[f"rank_ratio_{tag}"] = round(w.ratio, 3)
        if w.pct is not None:
            m[f"cohort_pct_{tag}"] = round(w.pct, 1)
            m[f"cohort_basis_{tag}"] = w.basis
            if w.n:
                m[f"cohort_n_{tag}"] = w.n
    return m


def list_series(rank_now: int, newest: TrancoList, windows: list[Window]) -> list[dict[str, Any]]:
    pts = [{"t": w.lst.day.isoformat(), "v": w.rank} for w in windows if w.rank is not None]
    pts.append({"t": newest.day.isoformat(), "v": rank_now})
    return sorted(pts, key=lambda p: p["t"])


def build_signal(ctx: Context, domain: str, ent: dict, rank_now: int, newest: TrancoList, rows: int,
                 w30: Window | None, w90: Window | None, entrants: EntrantRate | None,
                 history: list[tuple[date, int]] | None) -> Signal | None:
    """One signal for one ranked domain. `history` is the per-domain API result, if fetched."""
    windows = [w for w in (w90, w30) if w is not None]
    metrics = base_metrics(rank_now, newest, rows, windows)
    series = list_series(rank_now, newest, windows)
    occurred = newest.day
    is_new = bool(w30 and w90 and w30.rank is None and w90.rank is None)

    if is_new:
        assert w30 is not None and w90 is not None
        entry = entry_from_history(history or [], w30.lst.day, newest.day)
        live = entrants.rate(rank_now) if entrants else None
        rate = live[0] if live else static_entrant_rate(rank_now)
        metrics["entrant_base_rate"] = round(rate, 4)
        metrics["entrant_rate_basis"] = "live" if live else "static"
        if entry:
            metrics.update({"entry_date": entry.day.isoformat(), "entry_rank": entry.rank,
                            "days_listed": entry.days_listed, "entry_date_basis": "domain_api"})
            pts = {d.isoformat(): r for d, r in (history or []) if entry.day <= d <= newest.day}
            if pts.get(newest.day.isoformat(), rank_now) != rank_now:
                ctx.warn(f"{domain}: per-domain API gives {pts[newest.day.isoformat()]:,} for "
                         f"{newest.day.isoformat()}, list {newest.list_id} gives {rank_now:,}")
            pts[newest.day.isoformat()] = rank_now  # the list file is what the title quotes
            series = [{"t": t, "v": v} for t, v in sorted(pts.items())]
        else:
            metrics["entry_date_basis"] = "list_diff"
        if entry and entry.relisted:
            kind, strength = "tranco_rank_present", PRESENT_STRENGTH
            title = relisted_title(rank_now, rows, entry)
            metrics["relisted"] = True
        else:
            kind = "tranco_new_entrant"
            strength = entrant_strength(rate, entry)
            title = entrant_title(rank_now, w30, w90, entry)
            if entry:
                occurred = entry.day
            else:
                # No entry day: the date is the newest list's and moves with
                # every run. One refreshed row, not one per daily list.
                metrics["rolling"] = True
    else:
        strength, head = move_strength(rank_now, w30, w90)
        if head is not None:
            kind = "tranco_rank_move"
            title = move_title(rank_now, head, w30)
            metrics["headline_window_days"] = head.span
            metrics["sustained"] = is_material(w30) and is_material(w90)
            # "Rose over the 30 days to the newest list" is restated by every
            # daily run while the climb lasts. One refreshed row, so a
            # sustained climb is counted once.
            metrics["rolling"] = True
        else:
            kind = "tranco_rank_present"
            strength = PRESENT_STRENGTH
            title = present_title(rank_now, rows, w30, w90)

    if kind == "tranco_rank_present":
        # A standing reading: one row per entity, refreshed by every run
        # instead of a new row for every day's list.
        metrics["observed_only"] = True
    if occurred < ctx.since:
        return None
    return Signal(
        source=SLUG, family=FAMILY, kind=kind,
        entity=entity_hint(ent, domain),
        title=title,
        occurred_at=occurred.isoformat(),
        url=DOMAIN_API_URL.format(urllib.parse.quote(domain, safe="")),
        value=rank_now, unit="tranco rank", strength=strength,
        metrics=metrics, series=series,
    )


def collect(ctx: Context) -> Iterable[Signal]:
    targets = known_targets(ctx.known, ctx.limit)
    if not targets:
        ctx.log("tranco_rank: no known entities with a usable domain")
        return
    resolved = resolve_lists(ctx)
    if resolved is None:
        return
    newest, older = resolved
    wanted = frozenset(d.encode("ascii") for d, _ in targets)

    now = load_list(ctx, newest, wanted)
    if now is None:
        ctx.warn("tranco: newest list unusable, nothing emitted")
        return
    scans: dict[int, ListScan] = {}
    for days, lst in older.items():
        scan = load_list(ctx, lst, wanted)
        if scan is not None:
            scans[days] = scan
    cohorts = {days: Cohort(scan.sample, now.sample) for days, scan in scans.items()}
    entrants = EntrantRate(now.sample, [s.sample for s in scans.values()]) if len(scans) == len(WINDOWS) else None
    ctx.log(f"tranco_rank: list {newest.list_id} ({newest.day.isoformat()}, {now.rows:,} sites) against "
            + ", ".join(f"{older[d].list_id} ({older[d].day.isoformat()})" for d in sorted(scans))
            + f"; {len(now.ranks)} of {len(targets)} known domains ranked")

    # Windows per ranked domain, then the few per-domain history calls new entrants need.
    rows: list[tuple[str, dict, int, Window | None, Window | None]] = []
    for domain, ent in targets:
        rank_now = now.ranks.get(domain)
        if rank_now is None:
            continue
        if rank_now <= OWN_DOMAIN_CHECK_RANK and not looks_like_own_domain(str(ent["name"]), domain):
            ctx.warn(f"tranco_rank: {domain} ranks {rank_now:,} but does not carry the name "
                     f"{str(ent['name']).strip()!r}; skipped as a likely platform link")
            continue
        try:
            w = {days: make_window(days, newest, older[days], rank_now, scans[days].ranks.get(domain),
                                   cohorts[days]) for days in scans}
            rows.append((domain, ent, rank_now, w.get(30), w.get(90)))
        except Exception as e:  # noqa: BLE001 - one bad row must not lose the run
            ctx.warn(f"tranco_rank failed for {domain}: {type(e).__name__}: {e}")

    histories: dict[str, list[tuple[date, int]]] = {}
    new_rows = sorted((r for r in rows if r[3] and r[4] and r[3].rank is None and r[4].rank is None),
                      key=lambda r: r[2])
    for domain, *_ in new_rows[:MAX_HISTORY_LOOKUPS]:
        try:
            histories[domain] = fetch_history(domain)
        except Exception as e:  # noqa: BLE001 - fall back to the list diff alone
            ctx.warn(f"tranco history for {domain}: {type(e).__name__}: {e}")
    if len(new_rows) > MAX_HISTORY_LOOKUPS:
        ctx.warn(f"tranco_rank: {len(new_rows) - MAX_HISTORY_LOOKUPS} new entrants left undated "
                 f"(history budget {MAX_HISTORY_LOOKUPS})")

    out: list[Signal] = []
    for domain, ent, rank_now, w30, w90 in rows:
        try:
            sig = build_signal(ctx, domain, ent, rank_now, newest, now.rows, w30, w90, entrants,
                               histories.get(domain))
            if sig is not None:
                out.append(sig)
        except Exception as e:  # noqa: BLE001
            ctx.warn(f"tranco_rank failed for {domain}: {type(e).__name__}: {e}")
    out.sort(key=lambda s: (-s.strength, s.entity.name.lower()))
    yield from out
