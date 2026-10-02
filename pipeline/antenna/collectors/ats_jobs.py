"""Open roles on the public Greenhouse, Lever and Ashby job boards of companies other collectors found.

A job board is a keyless public record in which every open role carries a
title and the date it was posted. A small hardware company that posts its
first Head of Manufacturing, or opens eleven roles in a fortnight after months
of one or two, shows up here as soon as the roles go live, which is typically
well before a round is announced or a product ships, because hiring starts
when the money lands and not when the press release does.

This is an enrich collector. For each entity in ctx.known it looks for a board
in three places, in order: the {provider, slug} pairs in the entity's "ats"
list, the seed registry of identity-checked boards in
fixtures/hiring/ats_registry_seed.json, and slug guesses built from the name
and domain. A 200 from a job-board API does not mean the right company (the
source card found 18 guessed slugs answering for someone else), so nothing is
emitted until the board passes an identity check:

  Ashby             the organisation's public website, from the unauthenticated
                    GraphQL identity call, must be the entity's domain. If
                    both exist and differ the board is rejected outright.
  Greenhouse/Lever  the board's job links or job content must carry the
                    entity's own domain, or the board's company name must
                    equal the entity's name exactly (legal suffixes aside) AND
                    the role titles themselves must classify on thesis in the
                    entity's sector, with a higher bar for one-word names.
                    When the entity has a one-liner or description of its
                    own, the board must also share a thesis sector with it.

The "ats" list is rebuilt each run from every stored signal that names a
board, and this collector's signals name theirs, so each pair says which
collector recorded it ("source"). A pair this collector wrote on an earlier
run is looked up first, which saves the guessing, but it is checked as
strictly as a guess. Only a pair that hn_hiring read off the company's own
hiring post, or web_presence off the company's own homepage, gets the lighter
name check, and a pair that names no source is checked as a guess too.

Run blind against the 134 registry companies with the registry hidden (last
on 2026-10-02, from name and website alone), this found 108 of them and
attached no company to a board that was not its own; the looser first version
attached REGENT to a private-equity firm's board.
A board that cannot be verified is dropped and counted in the run log.

Signals, one company at a time:

  hiring_velocity    N of M open roles posted in the last 14 and 30 days,
                     against the 30 days before, with the function mix read
                     from titles, executive openings by name, and a weekly
                     series of roles posted per week.
  ats_board_created  every open role, or an outsized share of them, was first
                     published within three days of the board's earliest
                     date. That is what a brand-new board looks like, and
                     also what a board looks like when a company moves ATS and
                     every date resets, so those roles never count as
                     velocity and the title says "new or republished". When an
                     older board for the same company is also verified and
                     carries the same role titles, the event is labelled a
                     move and scored below the scale; when it does not, it is
                     a second board, scored as routine. A big board is scored
                     down as well: 94 roles appearing at once on a 134-role
                     board is an established company republishing.

What the numbers are and are not. These APIs are snapshots of roles open
right now. "Posted in the last 30 days" counts roles still open; a role that
was opened and filled inside the window is invisible, so the prior-30-day
figure and the older points of the weekly series are floors, not totals.
Posted dates are taken as the source writes them. ctx.today is the UTC date,
and Greenhouse writes dates in the board's own offset, so a board east of UTC
can be a day ahead of it: a date one day ahead of ctx.today is read as today.
Lever's date is when
the posting was created, which can be hours or more before it went live (seen
2026-10-01: a role created at 13:56 UTC first appeared after 01:29 UTC the
next day), so Lever counts of recent roles are floors as well.

Every number in a title is a literal count of the postings on the board,
catch-all postings ("General Application") included, so that anyone who
recounts the source gets the same figure. Strength is the part that leaves
catch-all postings, duplicate requisitions and first-batch roles out.
"""

from __future__ import annotations

import html
import json
import re
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable
from urllib.parse import quote

from .. import http
from ..config import FIXTURES_DIR
from ..models import EntityHint, Signal
from ..thesis import classify
from .base import Context, clean_domain, display_name, iso, loose_name, normalize_name, squash

SLUG = "ats_jobs"
FAMILY = "hiring"
STAGE = "enrich"
DESCRIPTION = "Open roles and posting velocity on identity-checked Greenhouse, Lever and Ashby boards"

REGISTRY_PATH = FIXTURES_DIR / "hiring" / "ats_registry_seed.json"

GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
GREENHOUSE_JOB_API = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{job_id}"
ASHBY_API = "https://api.ashbyhq.com/posting-api/job-board/{slug}"
ASHBY_GRAPHQL = "https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiOrganizationFromHostedJobsPageName"
LEVER_API = "https://api.lever.co/v0/postings/{slug}"
# The page a person opens. For Greenhouse this is the hosted board in its
# embed form: job-boards.greenhouse.io/{slug} redirects to the company's own
# careers page when the board is embedded there (15 of 56 boards checked
# 2026-10-01, one of them behind a bot wall, and a 16th redirected to a 404),
# while the embed form answered 200 for all 56 with the job total in the page.
BOARD_PAGE = {
    "greenhouse": "https://job-boards.greenhouse.io/embed/job_board?for={slug}",
    "ashby": "https://jobs.ashbyhq.com/{slug}",
    "lever": "https://jobs.lever.co/{slug}",
}
PROVIDER_LABEL = {"greenhouse": "Greenhouse", "ashby": "Ashby", "lever": "Lever"}

_ASHBY_ORG_QUERY = (
    "query ApiOrganizationFromHostedJobsPageName($organizationHostedJobsPageName: String!) "
    "{ organization: organizationFromHostedJobsPageName(organizationHostedJobsPageName: "
    "$organizationHostedJobsPageName) { name publicWebsite customJobsPageUrl hostedJobsPageSlug } }"
)

# Every request to an ATS host counts against this, cached or not. Measured on
# 141 YC companies: 650 lookups, 4.6 per entity (most are 404s on guesses, which
# antenna.http counts as not_found, not as errors); the 385 known entities of
# 2026-10-02 took 2,048.
MAX_BOARD_LOOKUPS = 4000
# Slug guesses tried per provider for an entity with no recorded or registry board.
MAX_GUESSES = {"ashby": 4, "greenhouse": 3, "lever": 2}
WORKERS = 6  # entities in flight; antenna.http still paces each host

JOBS_TTL = 6 * 3600  # open roles move by the day
IDENTITY_TTL = 72 * 3600  # who owns a slug changes rarely
LEVER_TIMEOUT = 25  # the card measured 25 s stalls on api.lever.co; one retry
# Ashby's identity GraphQL answered 429 "Rate limit" on 14 of about 350 calls at
# antenna.http's default 0.35 s spacing (2026-10-01). It is now asked only for
# slugs whose board exists, one call at a time, this many seconds apart.
ASHBY_IDENTITY_INTERVAL = 1.0

NEW_SHORT_DAYS = 14
NEW_DAYS = 30
STALE_DAYS = 90  # a second board this far behind the freshest one is a leftover
MAX_SERIES_WEEKS = 18

# Board-birth detection: the "first batch" is every role dated within
# BIRTH_WINDOW_DAYS of the board's earliest posted date (a move rarely lands
# in one day: Mach Industries' Greenhouse board took 94 of its 134 roles
# between 2026-08-31 and 2026-09-02). It is a birth when the batch is the
# whole board, or a large enough part of it that organic posting cannot
# explain it. Measured 2026-10-01 on the 138 registry boards with roles: 7
# qualify (Rainmaker, General Galactic, Fervo Energy, Substrate, Mach
# Industries, Valstad, 1X). Five boards with a first-batch share of 0.3 to 0.8
# stay out because the batch is only 2 to 7 roles; every other board is under 0.3.
BIRTH_WINDOW_DAYS = 3
BIRTH_MIN_ROLES = 3  # whole board
PARTIAL_BIRTH_RULES = ((5, 0.5), (10, 0.3))  # (minimum roles in the batch, minimum share of the board)
# A 3-day window holding at least this share of the 30-day total is a bulk publish.
BULK_WINDOW_DAYS = 3
BULK_MIN_ROLES = 4
BULK_SHARE = 0.5

# ---------------------------------------------------------------------------
# Strength calibration. Measured 2026-10-01 over the seed registry (138
# boards, 127 companies with a dated role inside the lookback): distinct roles
# posted in 30 days p50 11, p75 30, p90 55; new / (prior + 3) p50 1.0, p75
# 1.7, p90 2.5. With the weights below, and the big-board taper, the 121
# velocity strengths the registry yields on thesis came out p10 0.17, p25
# 0.24, p50 0.37, p75 0.52, p90 0.64, max 0.84: the median board is "solid",
# the top decile "notable", and the single sharpest ramp (30 of 37 open roles
# new, against 5 the month before, an executive opening) sits just under
# "rare". Nothing reaches "rare": that band is for a first-ever event, and a
# job board's dates cannot prove one. On the population this is aimed at, 141
# hiring YC companies on thesis, 34 had a verifiable board and their velocity
# strengths were p50 0.20, p75 0.28, p90 0.61, max 0.66: most small boards
# are quiet, and the scale says so. See velocity_strength.
# ---------------------------------------------------------------------------
BASE = 0.10
W_VOLUME = 0.36  # distinct roles posted in 30 days
W_ACCEL = 0.34  # against the 30 days before
W_SHARE_NEW = 0.18  # how much of the whole board is new
W_PHYSICAL = 0.06  # hardware, controls and manufacturing share of the board
VOLUME_HALF = 12.0
ACCEL_HALF = 1.5
ACCEL_PRIOR_SMOOTHING = 3.0  # roles added to the prior window so 2 vs 0 is not "infinite"
STRENGTH_FLOOR = 0.15
STRENGTH_CAP = 0.95
# Above this many open roles the company is already consensus, and how fast it
# posts says little a partner does not know. Strength falls in proportion to
# the excess, to a floor: checked 2026-10-01, this puts Anduril (2,420 open
# roles), Rocket Lab (559), Shield AI (581), Zipline (350), Crusoe (349) and
# Relativity (345) at 0.20 to 0.31, the routine band, where a square-root
# taper had left four of them reading as "solid".
BIG_BOARD = 150
BIG_BOARD_FLOOR = 0.25
BULK_DISCOUNT = 0.85
# A move between ATS is context for a partner, not hiring. It sits below the
# bottom band of the scale on purpose so that it cannot fire the family alone.
MIGRATION_STRENGTH = 0.05
# A first batch on a second board whose roles are not the older board's roles:
# the company already had a board, so this is not a first, only routine.
SECOND_BOARD_STRENGTH = 0.15
# Share of a first batch's role titles that must also be open on the older
# board before the title may call it a move (Rainmaker: 25 of 25).
MOVE_TITLE_OVERLAP = 0.5
# A board this size did not just open for the first time: Mach Industries'
# first batch of 94 (134 open roles) and 1X's of 30 (92 open roles) are
# republished boards of established companies. Board-created strength peaks
# for boards of about this many roles and tapers above it.
BIRTH_BIG_BOARD = 30
BIRTH_BIG_FLOOR = 0.4

FUNCTIONS = ("hardware", "controls_embedded", "manufacturing", "ml_ai", "software", "gtm", "g_and_a", "other")
PHYSICAL_FUNCTIONS = ("hardware", "controls_embedded", "manufacturing")

# First match wins, in this order. Patterns follow section 6 of the source
# card, tightened where a bare word would misfire ("Communications Systems
# Engineer" is not marketing, "Motion Capture" is not a capture manager). It
# is a title regex, so the mix is approximate: read it as a share, not a census.
_FUNCTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("g_and_a", re.compile(
        r"\b(counsel|legal|paralegal|recruit\w*|talent|people|human resources|hr|finance|financial|"
        r"accountant|accounting|payroll|tax|executive assistant|office manager|workplace|compliance|"
        r"export controls?|document control\w*|it (support|administrator|manager|specialist)|fp&a|"
        r"facility security officer|administrative|receptionist)\b", re.I)),
    ("gtm", re.compile(
        r"\b(sales|account executive|account manager|business development|capture (manager|director|lead)|"
        r"growth|marketing|partnerships?|customer success|solutions? (engineer|architect)\w*|revenue|"
        r"commercial|government (relations|affairs)|proposals?|"
        r"communications (manager|director|lead|specialist)|head of communications|public relations|"
        r"go[- ]to[- ]market|gtm)\b", re.I)),
    ("manufacturing", re.compile(
        r"\b(manufactur\w*|production|assembl\w*|technicians?|machinists?|cnc|weld\w*|fabricat\w*|quality|"
        r"supply chain|procurement|buyers?|planners?|tooling|npi|harness\w*|operators?|inspectors?|"
        r"mechanics?|sourcing|warehouse|logistics|industriali[sz]ation)\b", re.I)),
    ("ml_ai", re.compile(
        r"\b(machine learning|ml|ai|deep learning|research (scientist|engineer)s?|perception|"
        r"computer vision|reinforcement|autonomy|slam|motion planning|path planning|robot learning|"
        r"foundation models?|applied scientists?|data scientists?)\b", re.I)),
    ("controls_embedded", re.compile(
        r"\b(controls?|gnc|guidance|navigation|embedded|firmware|fpga|rtos|avionics|flight software|"
        r"instrumentation|plc|automation|mechatronics?)\b", re.I)),
    ("hardware", re.compile(
        r"\b(mechanical|electrical|electronics?|hardware|rf|power electronics|battery|propulsion|thermal|"
        r"structur\w*|aero\w*|design engineers?|asic|rtl|analog|silicon|pcb|systems? engineer\w*|"
        r"test engineer\w*|nuclear|reactor|plasma|magnets?|materials?|process engineer\w*|optical|optics|"
        r"photonics?|laser|physicists?|chemical|chemists?|metallurg\w*|fluids?|cryogenic\w*|high voltage|"
        r"mechanisms?|verification|physical design|radar|antenna|turbomachinery|combustion)\b", re.I)),
    ("software", re.compile(
        r"\b(software|backend|back[- ]end|frontend|front[- ]end|full[- ]?stack|devops|sre|site reliability|"
        r"infrastructure|data engineer\w*|platform engineer\w*|developers?|security engineer\w*|cloud|"
        r"simulation)\b", re.I)),
]

# Openings a partner would want by name. "Chief Engineer" and "Chief of Staff"
# are ladder titles, not executive hires, and an assistant to the CEO is not one either.
_NOTABLE = re.compile(
    r"\b(head of|vp|svp|evp|vice president|chief \w+ officer|c[etofmr]o|general manager|general counsel|"
    r"president)\b", re.I)
_NOT_NOTABLE = re.compile(
    r"\b(assistant|associate|deputy|chief of staff|recruit\w*|business partner|intern(ship)?|office of the)\b", re.I)

# Evergreen and catch-all postings (card gotcha 7). They are postings, so the
# literal counts in a title include them; they never count toward strength,
# executive openings or the weekly series.
_EVERGREEN = re.compile(
    r"general (application|interest|submission|opportunit\w*)|dream job|talent (network|community|pool)|"
    r"future opportunit\w*|rockstar|open application|speculative application|don'?t see (a|your|the)|"
    r"expression of interest|join our team|unsolicited|insert job|\btest (job|role|posting|requisition)\b", re.I)

_REQ_ID = re.compile(r"\s*[\(\[]\s*(?:r|req|id)?[-#\s]*\d{2,}\s*[\)\]]\s*$", re.I)
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_SLUG_OK = re.compile(r"^[a-z0-9][a-z0-9-]{1,48}$")
# A slug another collector read off a link: anything that could not be one
# path segment of a board URL is refused before it reaches a request.
_RECORDED_SLUG_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_GITHUB_LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
# Collectors whose "ats" pairs are a link the company published itself:
# hn_hiring reads it off the company's own hiring post, web_presence off the
# company's own homepage. A pair from any other source is checked as a guess.
_RECORDS_BOARDS = {"hn_hiring", "web_presence"}
ABOUT_MAX_CHARS = 4000  # of an entity's one-liner and description, for the name-only identity check
_SECOND_LEVEL = {"co", "com", "org", "net", "ac", "gov"}


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------

@dataclass
class Role:
    title: str
    posted: date | None
    location: str | None = None
    department: str | None = None
    url: str | None = None
    function: str = "other"
    notable: bool = False
    evergreen: bool = False


@dataclass
class Board:
    provider: str
    slug: str
    name: str | None  # company name as the board itself states it
    website: str | None  # Ashby only
    roles: list[Role]
    text: str = ""  # sample of job content: identity evidence and thesis text
    origin: str = "guess"  # recorded | registry | remembered | unsourced | guess
    verified_by: str | None = None
    # Set when the ATS-hosted page shows nothing and the board itself names
    # the company's own careers page as where its jobs are listed.
    page_override: str | None = None

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.slug}"

    @property
    def page(self) -> str:
        return self.page_override or BOARD_PAGE[self.provider].format(slug=self.slug)


@dataclass
class Stats:
    asof: date
    open_roles: int = 0
    dated_roles: int = 0
    evergreen_roles: int = 0
    new_14d: int = 0
    new_30d: int = 0
    prev_30d: int = 0
    distinct_new_30d: int = 0
    distinct_prev_30d: int = 0
    newest: date | None = None
    oldest: date | None = None
    function_mix: dict[str, int] = field(default_factory=dict)
    hardware_share: float = 0.0
    gtm_roles: int = 0
    notable_new: list[Role] = field(default_factory=list)
    sole_gtm: Role | None = None
    bulk_day: date | None = None  # first day of the busiest 3-day window
    bulk_share: float = 0.0
    prior_known: bool = True  # False when the prior 30 days overlap a board birth
    n_locations: int = 0
    founding_roles: int = 0


@dataclass
class Target:
    """One company to look up: who it is, and where its board might be."""

    name: str
    domain: str | None = None
    github: str | None = None
    kind: str = "company"
    sector: str | None = None
    about: str = ""  # the entity's own one-liner and description
    candidates: list[tuple[str, str, str]] = field(default_factory=list)  # provider, slug, origin
    seed: bool = False  # no known entity: name and domain come from the board itself
    registry_names: list[str] = field(default_factory=list)


@dataclass
class Outcome:
    signals: list[Signal] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    boards: list[Board] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    out_of_budget: bool = False


class _BudgetSpent(Exception):
    pass


class _Budget:
    def __init__(self, cap: int) -> None:
        self.cap = cap
        self.used = 0
        self._lock = threading.Lock()

    def take(self) -> None:
        with self._lock:
            if self.used >= self.cap:
                raise _BudgetSpent()
            self.used += 1


# ---------------------------------------------------------------------------
# Titles and functions
# ---------------------------------------------------------------------------

def clean_title(title: Any) -> str:
    """Whitespace collapsed. Anything that is not text (an object, a list) is no title."""
    if isinstance(title, bool) or not isinstance(title, (str, int, float)):
        return ""
    return " ".join(str(title).split())


def _named(value: Any) -> Any:
    """A field that is a string on one ATS and an object with a name on another."""
    return value.get("name") if isinstance(value, dict) else value


def github_login(value: Any) -> str | None:
    """A login, never a URL: 'https://github.com/acme/' -> 'acme'; junk -> None."""
    v = clean_title(value)
    if not v:
        return None
    v = re.sub(r"^(https?://)?(www\.)?github\.com/", "", v, flags=re.I).strip("/@ ").split("/")[0]
    return v if _GITHUB_LOGIN.match(v) else None


def display_title(title: str) -> str:
    """A role title as it reads in a sentence: no trailing requisition id."""
    return _REQ_ID.sub("", clean_title(title)).strip(" -|,")


def classify_function(title: str, department: str | None = None) -> str:
    """Bucket a role by its title, falling back to the department name."""
    for text in (title, department or ""):
        for name, pat in _FUNCTION_PATTERNS:
            if text and pat.search(text):
                return name
    return "other"


def is_notable(title: str) -> bool:
    return bool(_NOTABLE.search(title)) and not _NOT_NOTABLE.search(title)


def is_evergreen(title: str) -> bool:
    return bool(_EVERGREEN.search(title))


def make_role(title: Any, posted: date | None, location: Any = None, department: Any = None,
              url: Any = None) -> Role | None:
    t = clean_title(title)
    if not t:
        return None
    dept = clean_title(department) or None
    return Role(
        title=t, posted=posted, location=clean_title(location) or None, department=dept,
        url=url if isinstance(url, str) and url else None, function=classify_function(t, dept),
        notable=is_notable(t), evergreen=is_evergreen(t),
    )


def source_date(value: Any) -> date | None:
    """The calendar date a posting timestamp names, as the source wrote it.

    Greenhouse gives an ISO string with the board's own offset, Ashby an ISO
    string in UTC, Lever epoch milliseconds (read as UTC).
    """
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc).date()
        except (OverflowError, OSError, ValueError):
            return None
    try:
        return datetime.fromisoformat(str(value).strip().replace("Z", "+00:00")).date()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Parsing the three payloads
# ---------------------------------------------------------------------------

def parse_greenhouse(payload: Any) -> tuple[str | None, list[Role]]:
    """Roles and the board's company name from /v1/boards/{slug}/jobs."""
    roles: list[Role] = []
    names: Counter[str] = Counter()
    for j in _jobs(payload):
        if clean_title(j.get("company_name")):
            names[clean_title(j["company_name"])] += 1
        depts = j.get("departments")
        depts = [_named(d) for d in depts if _named(d)] if isinstance(depts, list) else []
        role = make_role(j.get("title"), source_date(j.get("first_published")), _named(j.get("location")),
                         depts[0] if depts else None, j.get("absolute_url"))
        if role:
            roles.append(role)
    return (names.most_common(1)[0][0] if names else None), roles


def _jobs(payload: Any) -> list[dict]:
    """The job objects of a Greenhouse or Ashby payload; anything else is skipped, not raised on."""
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    return [j for j in jobs if isinstance(j, dict)] if isinstance(jobs, list) else []


def parse_ashby(payload: Any) -> list[Role]:
    """Listed roles from the Ashby posting API."""
    roles: list[Role] = []
    for j in _jobs(payload):
        if j.get("isListed") is False:
            continue
        role = make_role(j.get("title"), source_date(j.get("publishedAt")), _named(j.get("location")),
                         _named(j.get("department")) or _named(j.get("team")), j.get("jobUrl"))
        if role:
            roles.append(role)
    return roles


def parse_lever(payload: Any) -> list[Role]:
    """Roles from the Lever postings array."""
    roles: list[Role] = []
    for j in payload if isinstance(payload, list) else []:
        if not isinstance(j, dict):
            continue
        cats = j.get("categories") if isinstance(j.get("categories"), dict) else {}
        role = make_role(j.get("text"), source_date(j.get("createdAt")), cats.get("location"),
                         cats.get("team") or cats.get("department"), j.get("hostedUrl"))
        if role:
            roles.append(role)
    return roles


def ashby_text(payload: Any, n: int = 3, chars: int = 6000) -> str:
    return "\n".join(str(j.get("descriptionPlain") or "")[:chars] for j in _jobs(payload)[:n])


def lever_text(payload: Any, n: int = 3, chars: int = 6000) -> str:
    jobs = [j for j in payload if isinstance(j, dict)] if isinstance(payload, list) else []
    return "\n".join(
        (str(j.get("descriptionPlain") or "") + "\n" + str(j.get("additionalPlain") or ""))[:chars]
        for j in jobs[:n]
    )


def lever_page_name(page_html: str) -> str | None:
    """Lever's JSON has no company name; the hosted page's <title> does."""
    m = re.search(r"<title[^>]*>(.*?)</title>", page_html or "", re.S | re.I)
    if not m:
        return None
    name = clean_title(html.unescape(m.group(1)))
    return name or None


def plain_text(markup: str) -> str:
    """Tags out, entities decoded, whitespace collapsed."""
    return " ".join(re.sub(r"<[^>]+>", " ", html.unescape(markup or "")).split())


# ---------------------------------------------------------------------------
# Counting
# ---------------------------------------------------------------------------

def normalize_dates(roles: list[Role], today: date) -> list[Role]:
    """Drop roles posted after the run date; read a one-day lead as today.

    The run date is the UTC date. Ashby and Lever stamp in UTC and cannot
    lead it; Greenhouse writes the board's own offset, so a board east of UTC
    can. Anything further ahead only occurs with --today set in the past, and
    such a role did not exist as of that date.
    """
    out = []
    for r in roles:
        if r.posted and r.posted > today:
            if (r.posted - today).days > 1:
                continue
            r = Role(**{**r.__dict__, "posted": today})
        out.append(r)
    return out


def _distinct_key(r: Role) -> tuple[str, str]:
    return (display_title(r.title).lower(), (r.location or "").lower())


@dataclass
class Birth:
    first_day: date
    last_day: date  # last day of the first batch; velocity counts start after it
    batch: int  # dated roles in the first batch
    dated: int  # all dated roles on the board

    @property
    def full(self) -> bool:
        return self.batch == self.dated

    @property
    def span(self) -> str:
        if self.first_day == self.last_day:
            return f"on {self.first_day.isoformat()}"
        return f"from {self.first_day.isoformat()} to {self.last_day.isoformat()}"


def detect_board_birth(roles: list[Role]) -> Birth | None:
    """The board's first batch, when it is all or an outsized part of the board.

    None for an ordinary board, whose oldest open role is a lone straggler.
    """
    dated = sorted(r.posted for r in roles if r.posted)
    n = len(dated)
    if n < BIRTH_MIN_ROLES:
        return None
    first = dated[0]
    batch = [d for d in dated if (d - first).days < BIRTH_WINDOW_DAYS]
    n0 = len(batch)
    whole = n0 == n
    partial = any(n0 >= least and n0 / n >= share for least, share in PARTIAL_BIRTH_RULES)
    if whole or partial:
        return Birth(first, batch[-1], n0, n)
    return None


def board_stats(roles: list[Role], asof: date, after: date | None = None) -> Stats:
    """Counts as of a date, from the posted dates of the roles open now.

    `after` is the last day of a board's first batch. Roles dated on or before
    it are left out of every velocity count: their dates say when the board
    appeared, not when the company decided to hire. While the prior 30 days
    still overlap that batch, the prior count is unknown rather than zero.

    new_14d, new_30d and prev_30d are literal: every dated posting in the
    window, catch-all postings included, which is what a recount of the source
    gives. The distinct counts, executive openings and bulk-publish check that
    feed strength are taken from real roles only.
    """
    st = Stats(asof=asof)
    st.prior_known = after is None or (asof - after).days >= 2 * NEW_DAYS
    live = [r for r in roles if r.posted is None or r.posted <= asof]
    if asof < max((r.posted for r in roles if r.posted), default=asof):
        # A past date: undated roles cannot be placed, leave them out.
        live = [r for r in live if r.posted is not None]
    st.open_roles = len(live)
    st.evergreen_roles = sum(1 for r in live if r.evergreen)
    real = [r for r in live if not r.evergreen]
    dated = [r for r in real if r.posted]
    st.dated_roles = len(dated)
    if dated:
        st.newest = max(r.posted for r in dated)
        st.oldest = min(r.posted for r in dated)

    mix = Counter(r.function for r in real)
    st.function_mix = {f: mix[f] for f in FUNCTIONS if mix.get(f)}
    if real:
        st.hardware_share = round(sum(mix.get(f, 0) for f in PHYSICAL_FUNCTIONS) / len(real), 2)
    st.gtm_roles = mix.get("gtm", 0)
    st.n_locations = len({r.location for r in real if r.location})
    st.founding_roles = sum(1 for r in real if re.search(r"\bfounding\b", r.title, re.I))

    pool = [r for r in live if r.posted and (after is None or r.posted > after)]
    new_30, prev_30 = [], []  # real roles only
    # "The last N days" is N calendar days ending on asof (age 0..N-1), the
    # convention that reproduces the source card's counts for Valar Atomics.
    for r in pool:
        age = (asof - r.posted).days
        if age < NEW_SHORT_DAYS:
            st.new_14d += 1
        if age < NEW_DAYS:
            st.new_30d += 1
            if not r.evergreen:
                new_30.append(r)
        elif age < 2 * NEW_DAYS:
            st.prev_30d += 1
            if not r.evergreen:
                prev_30.append(r)
    st.distinct_new_30d = len({_distinct_key(r) for r in new_30})
    st.distinct_prev_30d = len({_distinct_key(r) for r in prev_30})
    st.notable_new = sorted((r for r in new_30 if r.notable), key=lambda r: r.posted, reverse=True)

    gtm = [r for r in real if r.function == "gtm"]
    if len(gtm) == 1 and len(real) >= 3 and gtm[0] in new_30:
        st.sole_gtm = gtm[0]

    if len(new_30) >= BULK_MIN_ROLES:
        per_day = Counter(r.posted for r in new_30)
        day, n = max(
            ((d, sum(v for k, v in per_day.items() if 0 <= (k - d).days < BULK_WINDOW_DAYS)) for d in per_day),
            key=lambda dn: (dn[1], dn[0]),
        )
        if n >= BULK_MIN_ROLES and n / len(new_30) >= BULK_SHARE:
            st.bulk_day, st.bulk_share = day, round(n / len(new_30), 2)
    return st


def velocity_strength(st: Stats) -> float:
    """0..1 read of one company's posting pace, from counts alone.

    Volume (distinct roles posted in 30 days) and acceleration over the prior
    30 days carry most of it; the share of the board that is new, executive
    openings and a hardware-heavy mix add to it; a bulk publish and a board
    already past BIG_BOARD open roles take away.
    Distinct title-and-location pairs are used so that one requisition per seat
    (Shield AI lists near-identical titles under separate ids) does not read
    as breadth.
    """
    if st.open_roles <= 0:
        return 0.0
    new, prev = st.distinct_new_30d, st.distinct_prev_30d
    vol = squash(new, VOLUME_HALF)
    gate = min(1.0, new / 5.0)  # two roles against none is not an acceleration
    ratio = new / (prev + ACCEL_PRIOR_SMOOTHING)
    acc = squash(max(ratio - 1.0, 0.0), ACCEL_HALF) * gate if st.prior_known else 0.0
    named = min(0.10, 0.06 * len(st.notable_new)) + (0.04 if st.sole_gtm else 0.0)
    share_new = min(1.0, new / st.open_roles)
    s = (BASE + W_VOLUME * vol + W_ACCEL * acc + W_SHARE_NEW * share_new * gate + named
         + W_PHYSICAL * st.hardware_share * gate)
    if st.bulk_day:
        s *= BULK_DISCOUNT
    if st.open_roles > BIG_BOARD:
        s *= max(BIG_BOARD_FLOOR, BIG_BOARD / st.open_roles)
    return round(min(max(s, STRENGTH_FLOOR), STRENGTH_CAP), 3)


def birth_strength(n_roles: int, n_notable: int, board_roles: int | None = None) -> float:
    """A board whose first batch is n_roles, on a board of board_roles dated roles.

    Capped below 'rare': the dates cannot tell a first-ever board from a
    republished one. More roles in the batch read stronger up to a point, but a
    board past BIRTH_BIG_BOARD roles belongs to a company that has been hiring
    for a while, so the larger the board the more likely this is a republish
    and the less it is worth: 17 roles on a 17-role board outranks 94 on a
    134-role board.
    """
    s = min(0.30 + 0.40 * squash(n_roles, 12.0) + min(0.08, 0.04 * n_notable), 0.70)
    size = max(board_roles or 0, n_roles)
    if size > BIRTH_BIG_BOARD:
        s *= max(BIRTH_BIG_FLOOR, (BIRTH_BIG_BOARD / size) ** 0.5)
    return round(max(s, STRENGTH_FLOOR), 3)


def weekly_series(roles: list[Role], today: date, weeks: int, after: date | None = None) -> list[dict]:
    """Roles posted per week, oldest first, with the strength as of each week end.

    Built from roles still open, so older weeks are floors (see module note).
    Weeks before the first dated role are left out rather than shown as zero.
    """
    pool = [r for r in roles if r.posted and not r.evergreen and (after is None or r.posted > after)]
    if not pool:
        return []
    first = min(r.posted for r in pool)
    out = []
    for w in range(weeks, -1, -1):
        end = today - timedelta(days=7 * w)
        if end < first:
            continue
        start = end - timedelta(days=7)
        v = sum(1 for r in pool if start < r.posted <= end)
        out.append({"t": end.isoformat(), "v": v, "s": velocity_strength(board_stats(roles, end, after))})
    return out


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

def strict_name(name: str | None) -> str:
    """The name two records must share exactly to be called one company.

    base.normalize_name (legal forms off, 'Robotics', 'AI' and 'Labs' kept:
    the Greenhouse board 'Reflex' is not Reflex Robotics), read without a
    leading 'The' or a trailing 'Holdings'.

    base keeps 'Company' because it is the name in 'The Boring Company'. Here
    a trailing 'Company' or 'Co' is dropped only when two or more words are
    left without it: the Lever board 'Pickle Robot Company' is Pickle Robot,
    and 'The Nuclear Company' stays 'nuclear company', which is not a company
    called Nuclear.
    """
    toks = normalize_name(re.sub(r"\(.*?\)", " ", name or "")).split()
    if len(toks) > 1 and toks[0] == "the":
        toks = toks[1:]
    while len(toks) > 1 and toks[-1] == "holdings":
        toks.pop()
    if len(toks) > 2 and toks[-1] in ("company", "co"):
        toks.pop()
    return " ".join(toks)


def same_site(a: str | None, b: str | None) -> bool:
    """Two hosts on one registrable domain: equal, or one a subdomain of the other."""
    if not a or not b:
        return False
    a, b = a.lower(), b.lower()
    return a == b or a.endswith("." + b) or b.endswith("." + a)


def domain_in_text(domain: str | None, text: str | None) -> bool:
    """The domain appears as a host or an email domain, not as a prefix of a longer one."""
    if not domain or not text:
        return False
    pat = rf"(?<![a-z0-9-]){re.escape(domain.lower())}(?![a-z0-9-])(?!\.[a-z])"
    return re.search(pat, text.lower()) is not None


def job_hosts(board: Board) -> set[str]:
    """Company-owned hosts the board's job links point at (boards embedded on
    the company's own careers page link there, not to the ATS, and
    clean_domain gives nothing for an ATS or any other shared host)."""
    return {h for h in (clean_domain(r.url) for r in board.roles if r.url) if h}


def board_on_thesis(board: Board, name: str, sector: str | None, about: str | None = None) -> bool:
    """Corroboration for a name-only match: do the roles read like this company?

    The titles themselves must carry the thesis: one strong term or two
    context terms. Job descriptions alone are not enough: a private-equity
    firm named Regent mentions "manufacturing" in its boilerplate and has no
    such role. The classifier holds a lone ambiguous term ("manufacturing",
    "battery") under its gate until a second term turns up, so when one such
    title word is all the titles have, the second must be in the job text.
    Titles and text together must then cover the entity's own sector, and a
    one-word name needs the stronger fit.

    `about` is the entity's own one-liner and description. The sector label
    comes from score.thesis_of, which reads the entity's name, its description
    and its non-hiring signals, and falls back to hiring text, this
    collector's earlier signals included, when nothing else is on thesis. In
    that fallback a board attached once names the sector it is then checked
    against. The description is written by nobody but the company or a
    registry, so when it is on thesis the board must share a sector with it.
    """
    titles = " ; ".join(r.title for r in board.roles[:400] if not r.evergreen)
    ct = classify(titles)
    # Sector scores are not capped, so they still tell a lone strong term
    # (0.465) from a lone context term (0.245) when the fit reads 0.28 for both.
    if ct["fit"] < 0.3 and max(ct["sectors"].values(), default=0.0) < 0.3:
        return False
    c = classify(titles + " \n " + plain_text(board.text)[:4000])
    if c["fit"] < 0.3:
        return False
    if sector and sector != "other" and sector not in c["sectors"]:
        return False
    said = classify(about) if about else None
    if said and said["fit"] >= 0.3 and not set(said["sectors"]) & set(c["sectors"]):
        return False
    if len(strict_name(name).split()) < 2 and c["fit"] < 0.6:
        return False
    return True


def verify_identity(name: str, domain: str | None, sector: str | None, board: Board,
                    kind: str = "company", about: str | None = None) -> str | None:
    """How we know this board belongs to this entity, or None if we do not.

    Order of evidence: Ashby's stated website against the entity domain (a
    mismatch is disqualifying), job links on the entity's own domain, the
    entity domain inside job content, then the name. A name alone is only
    enough for a registry row, which a person already checked, or loosely for
    a slug another collector read off the company's own post or homepage; a
    guess, a board this collector attached on an earlier run, or a pair that
    names no such source, needs the exact name and roles that are on thesis
    and agree with the entity's own description (`about`) where it has one.

    An entity of kind "project" is a repository with no company behind it yet.
    A name is never enough for one: a hobby repo called "dexterity" is not
    Dexterity the company, so only the entity's own domain can tie it to a board.
    """
    board_site = clean_domain(board.website)
    if domain and board_site:
        return "website" if same_site(domain, board_site) else None
    if domain and any(same_site(domain, h) for h in job_hosts(board)):
        return "job_url"
    if domain and domain_in_text(domain, board.text):
        return "content_url"
    if kind == "project":
        return None
    if board.origin == "registry":
        return "registry"
    if not board.name or _UUID.match(board.name):
        return None
    want, says = strict_name(name), strict_name(board.name)
    exact = want == says != ""
    if board.origin == "recorded":
        # The link came from the company's own post or homepage, so the name is a
        # sanity check, not the proof: 'Valstad' on the board of Valstad Shipworks passes.
        loose = loose_name(name) == loose_name(board.name) != ""
        prefix = bool(want and says) and (want.startswith(says + " ") or says.startswith(want + " "))
        return "recorded_name" if exact or loose or prefix else None
    if exact and board_on_thesis(board, name, sector, about):
        return "name_and_thesis"
    return None


def slug_candidates(name: str, domain: str | None) -> dict[str, list[str]]:
    """Guesses in the order the card's sweep found them to hit.

    Name with spaces removed (49 of 58 Greenhouse, 36 of 59 Ashby, 12 of 15
    Lever), hyphenated (17 Ashby, 2 Lever), then the domain label and the name
    without its descriptive word ('antora', 'cobot', 'sunday'). Short forms
    collide most, which is what verify_identity is for.
    """
    toks = strict_name(name).split()
    short = loose_name(name).split()
    label = None
    if domain:
        parts = domain.lower().split(".")
        label = parts[-2] if len(parts) >= 2 else None
        if label in _SECOND_LEVEL and len(parts) >= 3:
            label = parts[-3]
    forms = {
        "joined": "".join(toks), "hyphen": "-".join(toks), "label": label or "",
        "short": "".join(short), "short_hyphen": "-".join(short),
    }
    order = {
        "ashby": ["joined", "hyphen", "label", "short", "short_hyphen"],
        "greenhouse": ["joined", "label", "short"],
        "lever": ["joined", "hyphen"],
    }
    out: dict[str, list[str]] = {}
    for provider, keys in order.items():
        seen: list[str] = []
        for k in keys:
            s = forms[k]
            if s and _SLUG_OK.match(s) and s not in seen:
                seen.append(s)
        out[provider] = seen[: MAX_GUESSES[provider]]
    return out


# ---------------------------------------------------------------------------
# Seed registry
# ---------------------------------------------------------------------------

def load_registry(path=REGISTRY_PATH) -> list[dict]:
    try:
        rows = json.loads(path.read_text()).get("verified") or []
    except (OSError, ValueError):
        return []
    return [r for r in rows if r.get("ats") in BOARD_PAGE and r.get("slug")]


def registry_rows_for(name: str, domain: str | None, registry: list[dict]) -> list[dict]:
    """Registry boards for an entity: by website domain, else by exact name.

    A name match is refused when the row carries a website on a different
    domain from the entity's: two companies, one name.
    """
    want = strict_name(name)
    out = []
    for row in registry:
        site = clean_domain(row.get("website"))
        if domain and site:
            if same_site(domain, site):
                out.append(row)
            continue
        names = {strict_name(row.get("query_company")), strict_name(row.get("board_name"))} - {""}
        if want and want in names:
            out.append(row)
    return out


def target_from_known(entity: dict, registry: list[dict]) -> Target | None:
    if not isinstance(entity, dict):
        return None
    name = clean_title(entity.get("name"))
    if not name or entity.get("kind") == "person":
        return None
    domain = entity.get("domain")
    domain = clean_domain(domain) if isinstance(domain, str) else None
    kind = entity.get("kind") if entity.get("kind") in ("company", "project") else "company"
    if kind == "project" and not domain:
        # A repository with no company behind it and no site of its own: a name
        # is all there is, and verify_identity never accepts a name for a
        # project, so there is nothing to look up.
        return None
    sector = entity.get("sector") if isinstance(entity.get("sector"), str) else None
    about = " ".join(filter(None, (clean_title(entity.get(k)) for k in ("one_liner", "description"))))
    t = Target(name=name, domain=domain, github=github_login(entity.get("github")), kind=kind, sector=sector,
               about=about[:ABOUT_MAX_CHARS])
    in_registry = [(row["ats"], row["slug"]) for row in registry_rows_for(name, domain, registry)]
    # Each pair names the collector that recorded it. A link the company
    # published itself is "recorded". This collector's own pair from an earlier
    # run is "remembered": worth trying first, but "we matched it last time" is
    # not evidence, so it verifies as a guess does. So does a pair that names
    # no source, or one this collector does not know to read boards off the
    # company's own pages.
    origins: dict[tuple[str, str], str] = {}
    recorded = entity.get("ats")
    for pair in recorded if isinstance(recorded, list) else []:
        if not isinstance(pair, dict):
            continue
        provider = clean_title(pair.get("provider")).lower()
        slug = clean_title(pair.get("slug"))
        if provider not in BOARD_PAGE or not _RECORDED_SLUG_OK.match(slug):
            continue
        source = pair.get("source") if isinstance(pair.get("source"), str) else None
        origin = "recorded" if source in _RECORDS_BOARDS else "remembered" if source == SLUG else "unsourced"
        key = (provider, slug if provider == "lever" else slug.lower())
        # Two collectors can name one board: the company's own link is the stronger account.
        if key not in origins or origin == "recorded":
            origins[key] = origin
    for pair in in_registry:
        origins[pair] = "registry"  # recorded and in the registry counts as the registry's
    t.candidates = [(*pair, origin) for pair, origin in origins.items()]
    return t


def targets_from_registry(registry: list[dict]) -> list[Target]:
    """With no known entities, the registry's own companies, smallest boards first."""
    groups: dict[str, Target] = {}
    size: dict[str, int] = {}
    for row in registry:
        key = strict_name(row.get("query_company") or row.get("board_name"))
        if not key:
            continue
        t = groups.setdefault(key, Target(name=clean_title(row.get("query_company") or row.get("board_name")),
                                          seed=True))
        t.candidates.append((row["ats"], row["slug"], "registry"))
        t.registry_names.extend(n for n in (row.get("query_company"), row.get("board_name")) if n)
        t.domain = t.domain or clean_domain(row.get("website"))
        size[key] = max(size.get(key, 0), int(row.get("open_roles") or 0))
    return [groups[k] for k in sorted(groups, key=lambda k: (size[k] == 0, size[k], k))]


# ---------------------------------------------------------------------------
# Fetching (the only functions that touch the network)
# ---------------------------------------------------------------------------

def fetch_greenhouse(slug: str, budget: _Budget, bodies: int = 2) -> Board | None:
    """The board, with the text of its first `bodies` jobs.

    The light listing has no job text. Two job bodies are enough to look for
    the entity's own domain. A registry board needs no such proof and gets
    one, so that the signal's text carries the company's own account of
    itself and not role titles alone: titles are mostly function words, and
    the classifier holds a lone ambiguous term under its gate. Measured
    2026-10-02 over the 129 registry companies with a signal: 122 classified
    on thesis while Greenhouse registry boards had titles only, 127 with the
    opening of one job (Albedo, Fervo Energy, KoBold Metals, Orbital
    Operations and Vannevar Labs came in; Dirac and Lydian stay under).
    """
    budget.take()
    try:
        payload = http.get_json(GREENHOUSE_API.format(slug=quote(slug, safe="")), ttl=JOBS_TTL, retries=2)
    except http.HttpError as e:
        if e.status in (404, 410):
            return None
        raise
    name, roles = parse_greenhouse(payload)
    board = Board("greenhouse", slug.lower(), name, None, roles)
    if bodies > 0:
        chunks = []
        for j in _jobs(payload)[:bodies]:
            if not isinstance(j.get("id"), (int, str)) or isinstance(j.get("id"), bool) or not j.get("id"):
                continue
            budget.take()
            try:
                job = http.get_json(GREENHOUSE_JOB_API.format(slug=quote(slug, safe=""),
                                                              job_id=quote(str(j["id"]), safe="")),
                                    ttl=IDENTITY_TTL, retries=1)
                if isinstance(job, dict):
                    chunks.append(html.unescape(str(job.get("content") or ""))[:12000])
            except Exception:  # noqa: BLE001 - job text is extra evidence; the board itself is already in hand
                continue
        board.text = "\n".join(chunks)
    return board


_identity_lock = threading.Lock()
_identity_next = [0.0]  # monotonic time before which no identity request goes out


def _ashby_paced(call):
    """Run one request to jobs.ashbyhq.com, one at a time, ASHBY_IDENTITY_INTERVAL apart."""
    with _identity_lock:
        wait = _identity_next[0] - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        sent_before = http.stats()["requests"]
        try:
            return call()
        finally:
            if http.stats()["requests"] != sent_before:  # a cache hit costs Ashby nothing
                _identity_next[0] = time.monotonic() + ASHBY_IDENTITY_INTERVAL


def ashby_identity(slug: str, budget: _Budget) -> dict | None:
    """Name and public website of the organisation behind an Ashby slug."""
    budget.take()
    ident = _ashby_paced(lambda: http.post_json(
        ASHBY_GRAPHQL,
        {"operationName": "ApiOrganizationFromHostedJobsPageName",
         "variables": {"organizationHostedJobsPageName": slug}, "query": _ASHBY_ORG_QUERY},
        ttl=IDENTITY_TTL, retries=2,
    ))
    data = ident.get("data") if isinstance(ident, dict) else None
    org = data.get("organization") if isinstance(data, dict) else None
    return org if isinstance(org, dict) else None


def hosted_page_is_empty(page_html: str) -> bool:
    """An Ashby hosted page that lists nothing: its embedded state carries no organisation.

    Form Energy's did on 2026-10-01 (203 roles in the API, a blank page titled
    "Jobs" at jobs.ashbyhq.com/formenergy) because the company lists its jobs
    on its own site instead.
    """
    return re.search(r'"organization"\s*:\s*\{', page_html or "") is None


def ashby_evidence_page(slug: str, org: dict, budget: _Budget) -> str | None:
    """The company's own careers page, when Ashby names one and its hosted page is blank.

    17 of 66 boards checked 2026-10-01 name a custom careers page and still
    serve a working hosted page, so the hosted page is looked at (one cached
    request, only for boards that name a custom page) rather than assumed.
    The custom page is used only when it is on the organisation's own site.
    """
    custom = org.get("customJobsPageUrl")
    if not isinstance(custom, str) or not custom.lower().startswith(("http://", "https://")):
        return None
    if not same_site(clean_domain(custom), clean_domain(org.get("publicWebsite") if isinstance(org.get("publicWebsite"), str) else None)):
        return None
    try:
        budget.take()
        page = _ashby_paced(lambda: http.get(BOARD_PAGE["ashby"].format(slug=quote(slug, safe="")),
                                             ttl=IDENTITY_TTL, retries=1))
    except _BudgetSpent:
        raise
    except http.HttpError as e:
        return custom.strip() if e.status in (404, 410) else None
    except Exception:  # noqa: BLE001 - cannot tell: keep the hosted page
        return None
    return custom.strip() if hosted_page_is_empty(page) else None


def fetch_ashby(slug: str, budget: _Budget) -> Board | None:
    # The posting API is the existence check (it took 488 probes at 0.35 s
    # spacing without complaint); identity is asked only for boards that exist.
    budget.take()
    try:
        payload = http.get_json(ASHBY_API.format(slug=quote(slug, safe="")), ttl=JOBS_TTL, retries=2)
    except http.HttpError as e:
        if e.status in (404, 410):
            return None
        raise
    roles = parse_ashby(payload)
    if not roles:
        return Board("ashby", slug.lower(), None, None, [])  # parked or empty: nothing to verify or emit
    org = ashby_identity(slug, budget) or {}
    site = org.get("publicWebsite")
    board = Board("ashby", slug.lower(), clean_title(org.get("name")) or None,
                  site if isinstance(site, str) and site.strip() else None, roles, text=ashby_text(payload))
    board.page_override = ashby_evidence_page(slug, org, budget)
    return board


def fetch_lever(slug: str, budget: _Budget, want_name: bool) -> Board | None:
    budget.take()
    try:
        payload = http.get_json(LEVER_API.format(slug=quote(slug, safe="")), params={"mode": "json"}, ttl=JOBS_TTL,
                                timeout=LEVER_TIMEOUT, retries=1)
    except http.HttpError as e:
        if e.status in (404, 410):
            return None
        raise
    board = Board("lever", slug, None, None, parse_lever(payload), text=lever_text(payload))
    if want_name and board.roles:
        budget.take()
        try:
            board.name = lever_page_name(http.get(board.page, ttl=IDENTITY_TTL, timeout=LEVER_TIMEOUT, retries=1))
        except Exception:  # noqa: BLE001 - no page, no name: the board then has to verify some other way
            board.name = None
    return board


def fetch_board(provider: str, slug: str, origin: str, budget: _Budget, need_identity: bool) -> Board | None:
    if provider == "greenhouse":
        board = fetch_greenhouse(slug, budget, bodies=2 if need_identity else 1)
    elif provider == "ashby":
        board = fetch_ashby(slug, budget)
    else:
        board = fetch_lever(slug, budget, want_name=need_identity)
    if board:
        board.origin = origin
    return board


# ---------------------------------------------------------------------------
# From boards to signals
# ---------------------------------------------------------------------------

def _plural(n: int, word: str = "role") -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def pick_notable(st: Stats, within_days: int, max_chars: int = 48) -> Role | None:
    """The executive opening to name in the title: physical functions first."""
    rank = {"manufacturing": 0, "gtm": 1, "hardware": 2, "controls_embedded": 3, "ml_ai": 4, "software": 5}
    pool = [r for r in st.notable_new
            if (st.asof - r.posted).days < within_days and len(display_title(r.title)) <= max_chars]
    if not pool:
        return None
    return min(pool, key=lambda r: (rank.get(r.function, 9), (st.asof - r.posted).days, r.title))


def velocity_title(st: Stats, birth: Birth | None = None) -> str:
    """One sentence whose every number is a literal count of the board.

    When a first batch was left out of the counts and any of it falls inside
    the window the sentence names, the sentence says "not counting" it: Mach
    Industries had 76 roles dated inside 30 days, 36 of them first-batch roles
    from 2026-09-02, and "40 posted in the last 30 days" alone would be false.
    """
    open_part = f"{st.open_roles} open role{'' if st.open_roles == 1 else 's'}"
    batch_age = (st.asof - birth.last_day).days if birth else None
    near = pick_notable(st, NEW_SHORT_DAYS)
    if near and st.new_14d and (batch_age is None or batch_age >= NEW_SHORT_DAYS):
        return _no_period(
            f"{st.new_14d} of {open_part} posted in the last 14 days, including {display_title(near.title)}")
    if birth and st.new_30d and not st.prior_known:
        when = "on" if birth.first_day == birth.last_day else "starting"
        link = "not counting" if batch_age < NEW_DAYS else "after"
        return (f"{st.new_30d} of {open_part} posted in the last 30 days, {link} a first batch of "
                f"{birth.batch} {when} {birth.first_day.isoformat()}")
    if st.new_30d:
        base = f"{st.new_30d} of {open_part} posted in the last 30 days, against {st.prev_30d} in the 30 days before"
        extra = pick_notable(st, NEW_DAYS)
        if extra:
            longer = _no_period(f"{base}, including {display_title(extra.title)}")
            if len(longer) <= 108:
                return longer
        elif st.sole_gtm:
            longer = f"{base}, including its only sales-side role"
            if len(longer) <= 108:
                return longer
        return base
    return f"{open_part}, none posted in the last 30 days"


def _no_period(title: str) -> str:
    """A role name can end in a full stop ('... Engineer, Sr.'); a signal title may not."""
    return title.rstrip(" .")


def choose_boards(boards: list[Board], today: date) -> tuple[Board | None, list[Board]]:
    """The board to measure, and the rest.

    A company can be live on two ATS at once, or leave a stale board behind
    after a move. Boards with roles are ranked: an ordinary board ahead of a
    one-day (birth or migration) board, then newest posting, then size. A
    board whose newest role trails the freshest board by STALE_DAYS is dropped.
    """
    live = [b for b in boards if b.roles]
    if not live:
        return None, []

    def newest(b: Board) -> date:
        return max((r.posted for r in b.roles if r.posted), default=date.min)

    freshest = max(newest(b) for b in live)
    live = [b for b in live if (freshest - newest(b)).days <= STALE_DAYS] or live

    def rank(b: Board):
        birth = detect_board_birth(b.roles)
        return (bool(birth and birth.full), -newest(b).toordinal(), -len(b.roles), b.key)

    live.sort(key=rank)
    return live[0], live[1:]


def _hint(target: Target, board: Board) -> EntityHint:
    """Known entities keep their own name, domain and github so the resolver
    joins this signal to them. In seed mode the board names the company."""
    links = {"jobs": board.page}
    if target.seed:
        # The board's own name without its legal form ('Sift Stack, Inc.' -> 'Sift Stack'),
        # in the company's own capitals.
        name = display_name(board.name, recase=False) if board.name and not _UUID.match(board.name) else target.name
        domain = clean_domain(board.website) or target.domain
        return EntityHint(name=name or target.name, kind="company", domain=domain, links=links)
    site = clean_domain(board.website)
    if site and not target.domain:
        # Stated by the board, but verified by name only: offered as a link,
        # not as the hard key the resolver merges on.
        links["website"] = f"https://{site}"
    return EntityHint(name=target.name, kind=target.kind, domain=target.domain, github=target.github, links=links)


def _signal_text(board: Board) -> str:
    titles: list[str] = []
    for r in board.roles:
        t = display_title(r.title)
        if not r.evergreen and t not in titles:
            titles.append(t)
        if len(titles) >= 60:
            break
    text = "Open roles: " + "; ".join(titles)
    snippet = plain_text(board.text)[:600]
    return f"{text}\n{snippet}" if snippet else text


def _base_metrics(board: Board, st: Stats, others: list[Board]) -> dict[str, Any]:
    m: dict[str, Any] = {
        "open_roles": st.open_roles,
        "ats_provider": board.provider,
        "ats_slug": board.slug,
        "identity_basis": board.verified_by,
        "function_mix": st.function_mix,
        "hardware_share": st.hardware_share,
        "gtm_roles": st.gtm_roles,
        "n_locations": st.n_locations,
    }
    if st.oldest:
        m["oldest_posting"] = st.oldest.isoformat()
    if st.newest:
        m["newest_posting"] = st.newest.isoformat()
    if st.evergreen_roles:
        m["evergreen_roles"] = st.evergreen_roles
    if st.founding_roles:
        m["founding_roles"] = st.founding_roles
    if others:
        m["other_boards"] = [b.key for b in others]
    return m


def build_signals(target: Target, boards: list[Board], ctx: Context) -> list[Signal]:
    """Turn a company's verified boards into at most one velocity signal, plus
    one board-created signal for each board that opens with a first batch."""
    today = ctx.today
    for b in boards:
        b.roles = normalize_dates(b.roles, today)
    primary, others = choose_boards(boards, today)
    if primary is None:
        return []
    out: list[Signal] = []

    # First batches: the primary's if it has one, and any other board that is
    # nothing but a first batch (a move, when an older board is also live).
    for b in [primary, *others]:
        birth = detect_board_birth(b.roles)
        if not birth or (b is not primary and not birth.full):
            continue
        if birth.first_day < ctx.since:
            continue
        older = [o for o in boards if o is not b
                 and any(r.posted and r.posted < birth.first_day for r in o.roles)]
        st = board_stats(b.roles, today)
        metrics = _base_metrics(b, st, [o for o in boards if o is not b and o.roles])
        metrics.update({
            "roles_in_first_batch": birth.batch, "dated_roles": birth.dated,
            "first_day": birth.first_day.isoformat(), "first_batch_last_day": birth.last_day.isoformat(),
            "basis": (f"every dated open role was first published within {BIRTH_WINDOW_DAYS} days of the "
                      "board's earliest posted date" if birth.full else
                      f"{round(100 * birth.batch / birth.dated)}% of dated open roles were first published "
                      f"within {BIRTH_WINDOW_DAYS} days of the board's earliest posted date"),
        })
        named = [r for r in b.roles if r.notable and not r.evergreen and r.posted
                 and birth.first_day <= r.posted <= birth.last_day]
        if named:
            metrics["executive_roles"] = [display_title(r.title) for r in named[:8]]
        count = (f"All {_plural(birth.batch, 'open role')}" if birth.full
                 else f"{birth.batch} of {_plural(birth.dated, 'open role')}")
        if older:
            # "A move" is only said when the source shows it: the first batch's
            # role titles are open on the older board too.
            batch_titles = {display_title(r.title).lower() for r in b.roles
                            if r.posted and birth.first_day <= r.posted <= birth.last_day}
            shared, old = max(((len(batch_titles & {display_title(r.title).lower() for r in o.roles}), o)
                               for o in older), key=lambda so: so[0])
            metrics["older_board"] = old.key
            metrics["first_batch_titles_on_older_board"] = shared
            if batch_titles and shared / len(batch_titles) >= MOVE_TITLE_OVERLAP:
                title = (f"{count} republished {birth.span} in a move from {PROVIDER_LABEL[old.provider]} "
                         f"to {PROVIDER_LABEL[b.provider]}, not new hiring")
                strength = MIGRATION_STRENGTH
                metrics["ats_migration_from"] = old.key
            else:
                title = (f"{count} first published {birth.span} on a second board beside its "
                         f"{PROVIDER_LABEL[old.provider]} one")
                if len(title) > 110:
                    title = f"{count} first published {birth.span} on a second job board"
                strength = SECOND_BOARD_STRENGTH
        else:
            title = f"{count} first published {birth.span}: a new or republished job board"
            strength = birth_strength(birth.batch, len(named), birth.dated)
        out.append(Signal(
            source=SLUG, family=FAMILY, kind="ats_board_created", entity=_hint(target, b),
            title=title, occurred_at=iso(birth.first_day), url=b.page, value=birth.batch, unit="roles",
            strength=strength, metrics=metrics, text=_signal_text(b),
        ))

    birth = detect_board_birth(primary.roles)
    after = birth.last_day if birth else None
    st = board_stats(primary.roles, today, after)
    pool_dates = [r.posted for r in primary.roles
                  if r.posted and not r.evergreen and (after is None or r.posted > after)]
    if not pool_dates:
        return out  # nothing but the one-day batch, or no dated roles: no velocity to report
    newest = max(pool_dates)
    if newest < ctx.since:
        return out
    strength = velocity_strength(st)
    metrics = _base_metrics(primary, st, [o for o in boards if o is not primary and o.roles])
    metrics.update({
        "new_14d": st.new_14d, "new_30d": st.new_30d, "prev_30d": st.prev_30d,
        "distinct_new_30d": st.distinct_new_30d, "distinct_prev_30d": st.distinct_prev_30d,
        "pct_new_30d": round(st.new_30d / st.open_roles, 2) if st.open_roles else 0.0,
        "dates_basis": "posted dates of roles still open; roles opened and closed inside a window are not visible"
                       + ("; Lever dates are when a posting was created, which can precede the day it went live"
                          if primary.provider == "lever" else ""),
    })
    if st.prior_known:
        metrics["hiring_accel_30d"] = round((st.new_30d - st.prev_30d) / max(st.prev_30d, 1), 2)
    if st.notable_new:
        metrics["executive_roles_new_30d"] = [display_title(r.title) for r in st.notable_new[:8]]
    if st.sole_gtm:
        metrics["sole_gtm_role"] = display_title(st.sole_gtm.title)
    if st.bulk_day:
        metrics["bulk_publish_day"] = st.bulk_day.isoformat()
        metrics["bulk_publish_share"] = st.bulk_share
    if birth:
        metrics["excluded_first_batch_roles"] = birth.batch
        metrics["first_day"] = birth.first_day.isoformat()
        if not st.prior_known:
            metrics["prev_30d_basis"] = "overlaps the board's first batch; not comparable"
    weeks = min(MAX_SERIES_WEEKS, max(ctx.lookback_days // 7, 1))
    series = weekly_series(primary.roles, today, weeks, after)
    out.append(Signal(
        source=SLUG, family=FAMILY, kind="hiring_velocity", entity=_hint(target, primary),
        title=velocity_title(st, birth), occurred_at=iso(newest), url=primary.page,
        value=st.new_30d, unit="roles posted in 30 days", strength=strength,
        metrics=metrics, series=series, text=_signal_text(primary),
    ))
    return out


# ---------------------------------------------------------------------------
# One company
# ---------------------------------------------------------------------------

def _is_good(board: Board, since: date) -> bool:
    """Enough to stop guessing: dated roles, something recent, not a one-day board."""
    dates = [r.posted for r in board.roles if r.posted]
    if not dates or max(dates) < since:
        return False
    birth = detect_board_birth(board.roles)
    return not (birth and birth.full)


def _seed_identity_ok(target: Target, board: Board) -> bool:
    """Seed mode re-checks the registry row against what the board says today."""
    if not board.name or _UUID.match(board.name):
        return bool(board.website and target.domain and same_site(clean_domain(board.website), target.domain))
    have = {strict_name(n) for n in target.registry_names} | {loose_name(n) for n in target.registry_names}
    says = strict_name(board.name)
    if says in have or loose_name(board.name) in have:
        return True
    # The registry's label can be longer or shorter than the board's own name
    # ('Valstad Shipworks' on a board named 'Valstad'): whole-word prefix either way.
    if says and any(h.startswith(says + " ") or says.startswith(h + " ") for h in have if h):
        return True
    return bool(board.website and target.domain and same_site(clean_domain(board.website), target.domain))


def process_target(target: Target, ctx: Context, budget: _Budget) -> Outcome:
    out = Outcome()
    tried: set[tuple[str, str]] = set()

    def attempt(provider: str, slug: str, origin: str) -> None:
        if (provider, slug) in tried:
            return
        tried.add((provider, slug))
        try:
            # Registry rows were identity-checked by hand; everything else, and
            # Lever in seed mode (its JSON carries no company name), gets the
            # extra identity calls. A Greenhouse registry board still reads one
            # job body, for the signal's text.
            need = origin != "registry" or (target.seed and provider == "lever")
            board = fetch_board(provider, slug, origin, budget, need_identity=need)
        except _BudgetSpent:
            raise
        except Exception as e:  # noqa: BLE001 - one bad board must not lose the company
            out.warnings.append(f"{target.name}: {provider}/{slug} failed: {type(e).__name__}: {str(e)[:120]}")
            return
        if board is None or not board.roles:
            return  # no such slug, or a parked account with nothing open: nothing to verify or emit
        try:
            if target.seed:
                ok = _seed_identity_ok(target, board)
                board.verified_by = "registry" if ok else None
            else:
                board.verified_by = verify_identity(target.name, target.domain, target.sector, board, target.kind,
                                                    target.about)
        except Exception as e:  # noqa: BLE001 - an unverifiable board is dropped, never emitted
            out.warnings.append(f"{target.name}: {provider}/{slug} identity check failed: {type(e).__name__}: {str(e)[:120]}")
            return
        if board.verified_by:
            out.boards.append(board)
        else:
            out.rejected.append(f"{target.name} is not {provider}/{slug} "
                                f"(board says {board.name!r}, site {clean_domain(board.website)})")

    try:
        for provider, slug, origin in target.candidates:
            attempt(provider, slug, origin)
        if not target.seed and not any(_is_good(b, ctx.since) for b in out.boards):
            guesses = slug_candidates(target.name, target.domain)
            depth = max(len(v) for v in guesses.values()) if guesses else 0
            for provider, slug in ((p, guesses[p][i]) for i in range(depth)
                                   for p in ("ashby", "greenhouse", "lever") if i < len(guesses[p])):
                attempt(provider, slug, "guess")
                if any(_is_good(b, ctx.since) for b in out.boards):
                    break  # one verified, live board is enough; stop spending lookups
    except _BudgetSpent:
        out.out_of_budget = True
    except Exception as e:  # noqa: BLE001 - one company must not lose the run
        out.warnings.append(f"{target.name}: lookup failed: {type(e).__name__}: {str(e)[:120]}")

    try:
        out.signals = build_signals(target, out.boards, ctx)
    except Exception as e:  # noqa: BLE001
        out.warnings.append(f"{target.name}: could not build signals: {type(e).__name__}: {str(e)[:120]}")
    return out


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------

def _on_thesis(s: Signal) -> bool:
    return classify(" ".join(filter(None, [s.entity.name, s.title, s.text])))["fit"] >= 0.3


def collect(ctx: Context) -> Iterable[Signal]:
    registry = load_registry()
    if ctx.known:
        targets = []
        for e in ctx.known:
            try:
                t = target_from_known(e, registry)
            except Exception as err:  # noqa: BLE001 - one malformed entity must not lose the run
                ctx.warn(f"skipped a known entity: {type(err).__name__}: {str(err)[:120]}")
                continue
            if t:
                targets.append(t)
    else:
        # A probe with no known entities still shows what the source yields.
        targets = targets_from_registry(registry)
        if not targets:
            ctx.warn(f"no known entities and no seed registry at {REGISTRY_PATH}")
    if ctx.limit:
        targets = targets[: ctx.limit]
    if not targets:
        return

    budget = _Budget(MAX_BOARD_LOOKUPS)
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        outcomes = list(pool.map(lambda t: process_target(t, ctx, budget), targets))

    signals: list[Signal] = []
    claimed: dict[str, int] = {}
    counts: Counter[str] = Counter()
    rejected: list[str] = []
    spent = False
    for i, (target, o) in enumerate(zip(targets, outcomes)):
        for w in o.warnings:
            ctx.warn(w)
        rejected.extend(o.rejected)
        spent = spent or o.out_of_budget
        if o.boards:
            counts["companies_with_board"] += 1
        for b in o.boards:
            counts[f"verified_by_{b.verified_by}"] += 1
        for s in o.signals:
            board_key = f"{s.metrics.get('ats_provider')}:{s.metrics.get('ats_slug')}"
            owner = claimed.setdefault(board_key, i)
            if owner != i:
                # Two known entities, one board (two that share a name included):
                # the stronger entity came first and keeps it.
                ctx.warn(f"{board_key} already attributed to {targets[owner].name}; skipped for {target.name}")
                continue
            if target.seed and not _on_thesis(s):
                # With no known entities this collector is the one naming the
                # company, so the thesis pre-filter every discover collector applies holds here.
                counts["seed_off_thesis"] += 1
                continue
            signals.append(s)
    if spent:
        ctx.warn(f"stopped at MAX_BOARD_LOOKUPS={MAX_BOARD_LOOKUPS}; later entities were not looked up")
    ctx.log(f"ats_jobs: {len(targets)} entities, {counts['companies_with_board']} with a verified board, "
            f"{budget.used} lookups; " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items()) if k.startswith("verified_by")))
    if counts["seed_off_thesis"]:
        ctx.log(f"ats_jobs: {counts['seed_off_thesis']} registry signals dropped, "
                "role titles and job text classify under 0.3 on thesis")
    if rejected:
        ctx.log(f"ats_jobs: {len(rejected)} boards answered but failed the identity check: " + "; ".join(rejected[:12]))

    signals.sort(key=lambda s: (-s.strength, s.entity.name))
    yield from signals
