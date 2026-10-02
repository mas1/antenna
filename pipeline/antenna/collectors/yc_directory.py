"""Y Combinator's company directory and launch board, for the three newest batches.

The directory, read through the open yc-oss JSON mirror that is rebuilt daily
from YC's own index, shows a company within a day of its YC page going up,
when it is usually a team of two to five (median three in the window measured
on 2026-10-01) and about ten days, at the median, before its own launch post.
The launch board (ycombinator.com/launches, which answers in JSON) then adds a
dated post with a vote count, and the YC company page names the founders.

What is emitted, and where each field comes from:

  yc_batch   one per on-thesis company whose YC page was published inside the
             lookback. occurred_at is the mirror's `launched_at`, the time the
             YC page was published, and metrics.occurred_at_basis says so. It
             is not the batch start. The daily changes feed cannot date more
             than its own day; companies it lists as added carry
             listed_in_changes_feed = true, and for those the two agree.
             The team size in the title is today's ("now a team of 3"): the
             mirror's history shows it moving after the listing date.
  yc_launch  one per launch post by those companies inside the lookback.
             occurred_at is the post's `created_at`; value is its vote count.
             A company's second and later posts say "Follow-up" and are
             discounted, so a first launch outranks a repeat at equal votes.

"On thesis" needs two things at once: the shared classifier scores the
record's text at 0.3 or more, and YC's own structured fields say physical
world (a thesis subindustry, or a sector or hard-tech tag). The text the
classifier reads, and the text every signal carries, states YC's tags and
industry in plain words next to the company's own, so a record YC files
under "Industrials -> Energy" or tags "Robotics" shows the classifier that
evidence. YC's labels never count twice, though. Outside a thesis subindustry
the company's own words must also name the thesis unless YC marks it Hard
Tech or Hardware; inside one, a record that presents itself as AI or software
and whose own words name nothing on thesis is left out. Text alone is trusted
only for records YC left untagged, and there the classifier's lone-term cap
applies in full: "factory", "defense" and "robotic" are everyday words for
software companies in these batches. Trading, sourcing and logistics
companies that merely buy from factories are left out.

Entity hints: the name drops a trailing legal form, as the shared helper
defines one ("DeepReach Inc." becomes "DeepReach", the original kept as an
alias; "Agency Tool Company" stays whole). YC's former_names are aliases
only when they are a spelling of the current name; a pivot's old name
("Front", "Telos") would merge another company's filings into this one, so
those stay in metrics.yc_former_names.

Founders, their bios and LinkedIn links come from the YC company page
(`data-page` JSON), fetched only for companies that are emitted, at most
MAX_FOUNDER_PAGES per run and about one request every 1.5 seconds. When the
page was fetched its team size is used, since the page is the evidence link;
otherwise the mirror's.
"""

from __future__ import annotations

import html
import json
import math
import re
import time
from bisect import bisect_left
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from .. import http
from ..models import EntityHint, Person, Signal
from ..thesis import classify
from .base import Context, clean_domain, iso, normalize_name, squash
from .base import display_name as shared_display_name

SLUG = "yc_directory"
FAMILY = "launch"
STAGE = "discover"
DESCRIPTION = "On-thesis companies in the three newest YC batches, their launch posts and founders"

META_URL = "https://yc-oss.github.io/api/meta.json"
CHANGES_URL = "https://yc-oss.github.io/api/changes/latest.json"
LAUNCHES_URL = "https://www.ycombinator.com/launches"
COMPANY_PAGE_PREFIX = "https://www.ycombinator.com/companies/"

N_BATCHES = 3
LAUNCH_PAGE_SIZE = 100
MAX_LAUNCH_PAGES = 10  # the endpoint stops at 1,000 hits whatever you ask
MAX_FOUNDER_PAGES = 160  # company pages fetched per run, newest companies first
YC_SPACING = 1.2  # extra seconds after a real ycombinator.com request (card: 1 per 1.5 s)
BIO_CHARS = 1500

_SEASON_ORDER = {"winter": 0, "spring": 1, "summer": 2, "fall": 3}
_SEASON_MONTH = {"winter": 1, "spring": 4, "summer": 7, "fall": 10}
# The directory carries placeholder batches a year out with one company each.
# A batch with fewer companies than this only counts once its season began.
MIN_REAL_BATCH = 5

# YC subindustries (the part after "Industrials -> ") that are the thesis.
THESIS_SUBINDUSTRIES = {
    "Manufacturing and Robotics", "Defense", "Aviation and Space", "Energy", "Drones",
}
# YC tags that name a thesis sector, mapped to that sector.
SECTOR_TAGS = {
    "Robotics": "robotics", "Swarm Robotics": "robotics",
    "Food Service Robots & Machines": "robotics",
    "Drones": "autonomy", "Unmanned Vehicle": "autonomy", "Self-Driving Vehicles": "autonomy",
    "Autonomous Delivery": "autonomy", "Autonomous Shipping": "autonomy",
    "Autonomous Trucking": "autonomy", "Air Taxis": "autonomy", "Lidar": "autonomy",
    "Defense": "defense", "Radar": "defense",  # the shared thesis files "radar" under defense
    "Aerospace": "space", "Airplanes": "space", "Satellites": "space",
    "Space Exploration": "space", "Commercial Space Launch": "space", "Rocketry": "space",
    "Energy": "energy", "Energy Storage": "energy", "Alternative Battery Tech": "energy",
    "Fusion Energy": "energy", "Small Modular Reactors": "energy", "Hydrogen Energy": "energy",
    "Solar Power": "energy", "Renewable Energy": "energy",
    "Manufacturing": "manufacturing", "3D Printing": "manufacturing",
    "Industrial": "manufacturing", "Advanced Materials": "manufacturing",
    "Mining": "manufacturing",
    "Semiconductors": "semiconductors", "Edge Computing Semiconductors": "semiconductors",
    "Electronics": "semiconductors",
}
# Tags that say "physical product" without naming a sector.
HARD_TAGS = {"Hard Tech", "Hardware"}
# YC industries that are never the thesis, whatever a tag says (an insurer
# for robot makers, a fund trading chip stocks).
OFF_THESIS_INDUSTRIES = {"Fintech", "Healthcare", "Education"}
# B2B subindustries for companies that move or trade goods. The Manufacturing
# tag there means "buys from factories", so it is not sector evidence alone.
TRADE_SUBINDUSTRIES = {"Supply Chain and Logistics"}
# One-liner words for an intermediary rather than a builder.
_COMMERCE = re.compile(
    r"\b(?:brokers?|brokerage|sourcing|procurement|marketplace|trading\s+company)\b", re.I)
# Tags whose words fool a keyword classifier; left out of the classified text.
# ("Robotic Process Automation" is not here: the shared classifier blanks it.)
_HOMONYM_TAGS = {"Swarm AI"}
# Figures of speech seen in these batches' one-liners ("software factory").
# The shared classifier blanks "army of" itself; the plural is still ours.
_METAPHOR = re.compile(
    r"\b(?:software|model|agent|ai|content|code|data|app|token|startup)s?[\s\-]+factor(?:y|ies)\b"
    r"|\barmies\s+of\b|\bbattery\s+of\b"
    r"|\bsecret\s+weapons?\b"
    r"|\b(?:data|lead|contact|email|crm|profile|record)s?\s+enrichment\b",
    re.I,
)
# "Autopilot for flight operations" is software unless YC marks the company as
# hardware, so this one is removed only from records without a hard-tech tag.
_SOFT_METAPHOR = re.compile(r"\bautopilot\s+for\b", re.I)
# YC tags, and one-liner words, by which a record presents itself as AI or
# software. Read only when the company's own words name nothing on thesis and
# YC does not mark it as hardware: then YC's labels are all the evidence, and
# an AI engineering firm filed under Energy is not an energy hardware company.
SOFTWARE_TAGS = {
    "AI", "Artificial Intelligence", "Generative AI", "AI Assistant", "AIOps", "SaaS",
    "Enterprise Software", "Developer Tools",
}
_SOFTWARE_WORDS = re.compile(r"\b(?:ai|software|saas|llms?|copilot)\b", re.I)
# B2B subindustries where "defense" means defending a network. Text alone is
# not trusted there.
CYBER_SUBINDUSTRIES = {"Security"}
MIN_FIT = 0.3  # the contract's pre-filter
# Text with no YC tag behind it: one unmistakable strong thesis term, or a
# strong term plus a second one. A lone ambiguous word ("manufacturing",
# "factory") is capped below this by the shared classifier.
MIN_TEXT_ONLY_FIT = 0.45
REPEAT_LAUNCH_DISCOUNT = 0.85  # a company's second or later launch post
# The run date and the source's stamps are both UTC. One day of slack covers a
# run that crosses midnight UTC.
FUTURE_SLACK_DAYS = 1

# First path segments on github.com that are not an account.
_GITHUB_NOT_LOGIN = {
    "orgs", "users", "sponsors", "apps", "marketplace", "topics", "features", "about",
    "enterprise", "login", "search", "settings", "collections", "organizations", "explore",
    "pricing", "site", "contact", "join", "new",
}

# Launch votes to strength, interpolated on log(1 + votes). Anchored on the
# measured distribution of thesis launches (source card, 2026-10-01): median
# 11 votes, p75 28, p90 64, and three posts out of 88 above 350.
_VOTE_ANCHORS = [
    (0, 0.15), (5, 0.28), (11, 0.40), (28, 0.50), (64, 0.62),
    (150, 0.75), (400, 0.87), (800, 0.95),
]


# ---------------------------------------------------------------- parsing

def _ws(s: Any) -> str:
    return " ".join(str(s or "").split())


def batch_key(name: str | None) -> tuple[int, int] | None:
    """("Summer 2026") -> (2026, 2), so batches sort in calendar order."""
    m = re.fullmatch(r"\s*(winter|spring|summer|fall)\s+(\d{4})\s*", name or "", re.I)
    if not m:
        return None
    return int(m.group(2)), _SEASON_ORDER[m.group(1).lower()]


def recent_batches(meta: dict, today: date, n: int = N_BATCHES) -> list[dict]:
    """The n newest real batches in meta.json, newest first.

    Placeholder batches (a season that has not begun and holds fewer than
    MIN_REAL_BATCH companies) are ignored.
    """
    rows = []
    for b in (meta.get("batches") or {}).values():
        key = batch_key(b.get("name"))
        if not key or not b.get("api"):
            continue
        season = b["name"].split()[0].lower()
        begun = date(key[0], _SEASON_MONTH[season], 1) <= today
        if (b.get("count") or 0) < MIN_REAL_BATCH and not begun:
            continue
        rows.append((key, {"name": b["name"].strip(), "api": b["api"], "count": b.get("count")}))
    rows.sort(key=lambda r: r[0], reverse=True)
    return [r[1] for r in rows[:n]]


def parse_ts(value: Any) -> datetime | None:
    """Unix seconds or an ISO string to an aware UTC datetime."""
    if value in (None, "", 0):
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def team_size_of(value: Any) -> int | None:
    """YC reports null, 0 and -1 for unknown; only a positive count is a fact."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value) if value >= 1 else None


def subindustry_leaf(company: dict) -> str | None:
    sub = company.get("subindustry") or ""
    if "->" in sub:
        return sub.split("->")[-1].strip() or None
    return None


def company_text(company: dict) -> str:
    """Everything the directory says about what the company does."""
    tags = [t for t in (company.get("tags") or []) if t not in _HOMONYM_TAGS]
    parts = [
        _ws(company.get("one_liner")),
        _ws(company.get("long_description")),
        "YC tags: " + ", ".join(tags) if tags else "",
        "YC industry: " + _ws(company.get("subindustry") or company.get("industry")),
    ]
    return "\n".join(p for p in parts if p)


def _scrub(text: str, hard: bool) -> str:
    """Remove figures of speech that would read as thesis terms."""
    text = _METAPHOR.sub(" ", text)
    return text if hard else _SOFT_METAPHOR.sub(" ", text)


def free_text(company: dict, hard: bool = False) -> str:
    """The company's own words only (no tags, no industry), metaphors removed."""
    return _scrub(_ws(company.get("one_liner")) + "\n" + _ws(company.get("long_description")), hard)


def own_words_say_thesis(own: dict) -> bool:
    """Do the company's own words name the thesis, given a YC tag beside them.

    `own` is the shared classifier's reading of the one-liner and description
    alone. The classifier holds a lone ambiguous term just under MIN_FIT
    because one keyword needs a second source. Where this is asked a YC sector
    or hard-tech tag is that second source, so one strong term is enough (a
    sector score of MIN_FIT or more). One context term still is not.
    """
    return own["fit"] >= MIN_FIT or max(own["sectors"].values(), default=0.0) >= MIN_FIT


def thesis_verdict(company: dict) -> dict:
    """Is this directory record on thesis, and on what evidence.

    Two things must both hold. The shared classifier scores the record's
    text (one-liner, description, tags, industry; metaphors removed) at
    MIN_FIT or more, and YC's own structured fields say physical world.
    basis names the second part:

      "subindustry"  filed under Industrials -> a thesis subindustry. YC's
                     labels alone can carry the text ("Industrials ->
                     Energy" is two thesis words), so one case is refused:
                     the company's own words name nothing on thesis, YC does
                     not mark it Hard Tech or Hardware, and the record
                     presents itself as AI or software
      "tags+text"    a sector or hard-tech tag, and either YC marks it Hard
                     Tech or Hardware or the company's own words name the
                     thesis (tags must not count twice). Under plain
                     Industrials that is enough; filed elsewhere it also
                     needs two sector tags, or one whose sector its own words
                     match. A hard-tech tag with no such sector tag says
                     "physical" but not which sector, so there the words
                     must reach MIN_TEXT_ONLY_FIT by themselves
      "one_liner"    YC left it untagged and the one-liner alone reaches
                     MIN_TEXT_ONLY_FIT (not under B2B -> Security, where
                     "defense" is a network's)
      None           not on thesis. That includes traders, brokers and
                     logistics companies whose only sector evidence is the
                     Manufacturing tag
    """
    tags = set(company.get("tags") or [])
    sector_tags = [t for t in (company.get("tags") or []) if t in SECTOR_TAGS]  # YC's order
    hard = bool(tags & HARD_TAGS)
    industry = company.get("industry") or ""
    industrials = industry == "Industrials"
    any_leaf = subindustry_leaf(company)
    leaf = any_leaf if industrials else None
    cls = classify(_scrub(company_text(company), hard))
    own = classify(free_text(company, hard))
    own_ok = own_words_say_thesis(own)
    raw_liner = _ws(company.get("one_liner"))
    liner = _scrub(raw_liner, hard)
    software = bool(tags & SOFTWARE_TAGS or _SOFTWARE_WORDS.search(raw_liner)
                    or _SOFT_METAPHOR.search(raw_liner))
    trade = (
        (any_leaf in TRADE_SUBINDUSTRIES or bool(_COMMERCE.search(liner)))
        and not hard
        and all(SECTOR_TAGS[t] == "manufacturing" for t in sector_tags)
    )
    basis = None
    if cls["fit"] < MIN_FIT or industry in OFF_THESIS_INDUSTRIES:
        pass
    elif leaf in THESIS_SUBINDUSTRIES:
        if hard or own["terms"] or not software:
            basis = "subindustry"
    elif trade:
        pass
    elif sector_tags or hard:
        if industrials and leaf is None:
            if hard or own_ok:
                basis = "tags+text"
        else:
            agree = any(SECTOR_TAGS[t] in own["sectors"] for t in sector_tags)
            tagged = len(sector_tags) >= 2 or agree
            if (tagged and (hard or own_ok)) or (hard and own["fit"] >= MIN_TEXT_ONLY_FIT):
                basis = "tags+text"
    elif not tags and any_leaf not in CYBER_SUBINDUSTRIES:
        if classify(liner)["fit"] >= MIN_TEXT_ONLY_FIT:
            basis = "one_liner"
    return {
        "on": basis is not None,
        "basis": basis,
        "fit": cls["fit"],
        "own_fit": own["fit"],
        "sector": cls["sector"],
        "sector_tags": sector_tags,
        "hard": hard,
        "industrials": industrials,
        "thesis_subindustry": leaf if leaf in THESIS_SUBINDUSTRIES else None,
    }


def batch_strength(verdict: dict) -> float:
    """How much a new directory listing should move a partner, 0.15 to 0.64.

    Evidence stacks: classifier fit, YC filing it under a thesis subindustry
    (or at least Industrials), a Hard Tech or Hardware tag (they build the
    thing rather than sell software to those who do), and each sector tag. A
    text-only match on an untagged record stays at the routine end. Measured
    on the 75 listings of 2026-06-03 to 2026-10-01: median 0.49, 90th
    percentile 0.60, lowest 0.32. A listing alone never reaches the "rare"
    band.
    """
    if verdict["basis"] == "one_liner":
        return round(0.15 + 0.1 * verdict["fit"], 3)
    s = 0.13 + 0.2 * verdict["fit"]
    if verdict["thesis_subindustry"]:
        s += 0.12
    elif verdict["industrials"]:
        s += 0.06
    if verdict["hard"]:
        s += 0.1
    s += 0.03 * min(len(verdict["sector_tags"]), 3)
    return round(min(s, 0.64), 3)


def launch_strength(votes: int, repeat: bool = False) -> float:
    """Votes to 0..1 against the measured launch distribution (see _VOTE_ANCHORS).

    A company's first launch post is the event; a second or later one
    (repeat=True) is routine and is discounted, so at equal votes the first
    always outranks the follow-up.
    """
    v = max(int(votes or 0), 0)
    x = math.log1p(v)
    xs = [math.log1p(a) for a, _ in _VOTE_ANCHORS]
    ys = [s for _, s in _VOTE_ANCHORS]
    if x >= xs[-1]:
        # Past the last anchor: creep toward 0.98, never 1.0.
        base = min(0.98, ys[-1] + 0.03 * squash(v - _VOTE_ANCHORS[-1][0], 800))
    else:
        i = max(bisect_left(xs, x), 1)
        x0, x1, y0, y1 = xs[i - 1], xs[i], ys[i - 1], ys[i]
        base = y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return round(base * REPEAT_LAUNCH_DISCOUNT if repeat else base, 3)


def percentile_of(value: int, sorted_values: list[int]) -> int | None:
    """Share of launches with strictly fewer votes, 0..100."""
    if not sorted_values:
        return None
    return round(100 * bisect_left(sorted_values, value) / len(sorted_values))


def parse_company_page(page_html: str) -> dict:
    """The `props` object embedded in a YC company page (`data-page` attribute)."""
    m = re.search(r'data-page="([^"]+)"', page_html)
    if not m:
        raise ValueError("no data-page attribute")
    data = json.loads(html.unescape(m.group(1)))
    props = data.get("props") or {}
    if not isinstance(props.get("company"), dict):
        raise ValueError("data-page has no props.company")
    return props


def _github_login(url: str | None) -> str | None:
    """The org or user login from a github.com URL, never the URL itself."""
    m = re.match(r"(?:https?://)?(?:www\.)?github\.com/([^?#]*)", (url or "").strip(), re.I)
    if not m:
        return None
    parts = [p for p in m.group(1).split("/") if p]
    if len(parts) >= 2 and parts[0].lower() in ("orgs", "users"):
        parts = parts[1:]  # github.com/orgs/acme/repositories
    if not parts or parts[0].lower() in _GITHUB_NOT_LOGIN:
        return None
    return parts[0] if re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9\-]{0,38})", parts[0]) else None


def display_name(name: Any) -> str:
    """The name as YC writes it, minus a trailing legal form ("Acme, Inc." -> "Acme").

    The shared helper decides what a legal form is, so the hint carries the
    name the resolver will show. The company wrote this name itself, so its
    capitals are kept as written (recase=False).
    """
    raw = _ws(name)
    short = shared_display_name(raw, recase=False)
    return short if len(short) >= 2 else raw


def _name_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", normalize_name(name))


def safe_aliases(name: Any, former_names: Any) -> list[str]:
    """Other spellings of this company's name that are safe to merge on.

    The resolver treats an alias as a merge key. YC's former_names are mostly
    pivots ("Front", "Telos", "Flow AI"), and an unrelated company's filing
    under such a name would be folded into this one. Only a former name that
    is a spelling of the current name is kept (one normalized name starts
    with the other, four characters or more), plus the name as YC writes it
    when display_name() shortened it.
    """
    raw, shown = _ws(name), display_name(name)
    key = _name_key(raw)
    out = [raw] if raw != shown else []
    seen = {shown.casefold(), raw.casefold()}
    for former in former_names if isinstance(former_names, list) else []:
        n = _ws(former)
        k = _name_key(n)
        if not n or n.casefold() in seen or not k or not key:
            continue
        short, long_ = sorted((k, key), key=len)
        if len(short) >= 4 and long_.startswith(short):
            out.append(n)
            seen.add(n.casefold())
    return out


def people_from_page(page_company: dict) -> list[Person]:
    """Founders exactly as the YC page lists them.

    The bio is free text, not a list of employer names, so it goes in
    facts["bio"] (where the scorer reads it for pedigree) and affiliations
    stays empty rather than holding a paragraph.
    """
    people = []
    for f in page_company.get("founders") or []:
        if not isinstance(f, dict):
            continue
        name = _ws(f.get("full_name"))
        if not name or f.get("is_active") is False:
            continue
        bio = _ws(f.get("founder_bio"))
        links = {}
        if str(f.get("linkedin_url") or "").startswith("http"):
            links["linkedin"] = f["linkedin_url"].strip()
        if str(f.get("twitter_url") or "").startswith("http"):
            links["twitter"] = f["twitter_url"].strip()
        people.append(Person(
            name=name,
            role=_ws(f.get("title")) or None,
            links=links,
            facts={"bio": bio[:BIO_CHARS]} if bio else {},
        ))
    return people


def entity_hint(company: dict, page_company: dict | None = None) -> EntityHint:
    """EntityHint from a directory record, plus links from the YC page if fetched."""
    links = {"yc": company["url"], "yc_batch": company["batch"]}
    github = None
    founded = None
    if page_company:
        for key, field in (("linkedin", "linkedin_url"), ("twitter", "twitter_url"),
                           ("crunchbase", "cb_url")):
            if str(page_company.get(field) or "").startswith("http"):
                links[key] = page_company[field].strip()
        github = _github_login(page_company.get("github_url"))
        year = page_company.get("year_founded")
        if isinstance(year, int) and 1990 < year < 2100:
            founded = str(year)
    return EntityHint(
        name=display_name(company["name"]),
        domain=clean_domain(company.get("website")),
        github=github,
        aliases=safe_aliases(company["name"], company.get("former_names")),
        one_liner=_ws(company.get("one_liner")) or None,
        description=_ws(company.get("long_description")) or None,
        location=_ws(company.get("all_locations")) or None,
        founded=founded,
        links=links,
    )


def _former_names(company: dict) -> list[str]:
    """YC's former_names as written, de-duplicated, for the record (not for merging)."""
    names = company.get("former_names")
    return list(dict.fromkeys(_ws(n) for n in names if _ws(n))) if isinstance(names, list) else []


def _team_size(company: dict, page_company: dict | None) -> int | None:
    # The page is first-party and is the evidence link, so its count wins.
    if page_company and team_size_of(page_company.get("team_size")):
        return team_size_of(page_company.get("team_size"))
    return team_size_of(company.get("team_size"))


def batch_title(company: dict, verdict: dict, team: int | None) -> str:
    """"Listed in YC Fall 2026 under Defense, now a team of 3", from YC's own labels.

    The team size is the one YC shows today, not the one on the listing date
    (Isengard Industries was listed at 20 and showed 30 three months later),
    so the title says "now" rather than tying the number to the dated event.
    """
    head = f"Listed in YC {company['batch']}"
    tail = f", now a team of {team}" if team else ""
    if verdict["thesis_subindustry"]:
        options = [f" under {verdict['thesis_subindustry']}"]
    elif verdict["sector_tags"]:
        st = verdict["sector_tags"]
        options = [f" tagged {st[0]} and {st[1]}"] if len(st) > 1 else []
        options.append(f" tagged {st[0]}")
    elif verdict["hard"]:
        options = [" tagged " + next(t for t in company.get("tags") or [] if t in HARD_TAGS)]
    else:
        options = []
    for where in options:
        if len(head + where + tail) <= 105:
            return head + where + tail
    return head + tail


def batch_signal(company: dict, verdict: dict, *, page_company: dict | None = None,
                 added_ids: frozenset[int] = frozenset()) -> Signal | None:
    """The directory listing as a Signal, or None if it has no publish time."""
    listed = parse_ts(company.get("launched_at"))
    if listed is None:
        return None
    team = _team_size(company, page_company)
    people = people_from_page(page_company) if page_company else []
    metrics: dict[str, Any] = {
        "yc_batch": company["batch"],
        "yc_id": company["id"],
        "yc_industry": company.get("industry"),
        "yc_subindustry": company.get("subindustry"),
        "yc_tags": list(company.get("tags") or []),
        "yc_stage": company.get("stage"),
        "yc_is_hiring": bool(company.get("isHiring")),
        "thesis_basis": verdict["basis"],
        "thesis_fit": verdict["fit"],
        "occurred_at_basis": "launched_at",  # YC page publish time, not batch start
        "listed_in_changes_feed": company["id"] in added_ids,
        "founders_listed": len(people),
    }
    if _former_names(company):
        metrics["yc_former_names"] = _former_names(company)
    if team:
        metrics["team_size"] = team
    return Signal(
        source=SLUG, family=FAMILY, kind="yc_batch",
        entity=entity_hint(company, page_company),
        title=batch_title(company, verdict, team),
        occurred_at=iso(listed),
        url=company["url"],
        value=team, unit="people" if team else None,
        strength=batch_strength(verdict),
        metrics=metrics,
        people=people,
        text=company_text(company),
    )


def top_share(value: int, sorted_values: list[int]) -> int | None:
    """Percent of launches with at least this many votes, rounded up (1 = top 1%)."""
    if not sorted_values:
        return None
    at_least = len(sorted_values) - bisect_left(sorted_values, value)
    return max(1, math.ceil(100 * at_least / len(sorted_values)))


def launch_title(votes: int, top: int | None = None, window_days: int | None = None,
                 repeat: bool = False) -> str:
    head = "Follow-up YC launch post" if repeat else "YC launch post"
    if votes <= 0:
        return f"{head} published, no votes yet"
    title = f"{head} reached {votes} vote{'' if votes == 1 else 's'}"
    if top is not None and top <= 25 and window_days:
        title += f", top {top}% of YC launches in the past {window_days} days"
    return title


def launch_number(created_at: str, known: Iterable[str]) -> int:
    """1 for a company's earliest known launch post, 2 for the next, and so on.

    `known` holds the created_at stamps of every launch post known for the
    company (the YC page lists all of them; the launches board only those in
    the lookback).
    """
    return 1 + len({k[:19] for k in known if k and k[:19] < created_at[:19]})


def launch_signal(hit: dict, company: dict, verdict: dict, *, page_company: dict | None = None,
                  window_votes: list[int] | None = None,
                  window_days: int | None = None, number: int | None = None) -> Signal | None:
    """A launches hit, joined to its directory record, as a Signal.

    `number` is the post's position among the company's launch posts when it
    is known (see launch_number); 2 or more marks a follow-up.
    """
    created = parse_ts(hit.get("created_at"))
    url = str(hit.get("search_path") or "")
    if created is None or not url.startswith("https://www.ycombinator.com/launches/"):
        return None
    try:
        votes = max(int(hit.get("total_vote_count") or 0), 0)
    except (TypeError, ValueError):
        return None  # a vote count we cannot read is not a number to print
    repeat = bool(number and number > 1)
    team = _team_size(company, page_company)
    metrics: dict[str, Any] = {
        "yc_batch": company["batch"],
        "yc_id": company["id"],
        "yc_launch_id": hit.get("id"),
        "yc_launch_votes": votes,
        "launch_title": _ws(hit.get("title")),
        "launch_tagline": _ws(hit.get("tagline")),
        "thesis_basis": verdict["basis"],
        "thesis_fit": verdict["fit"],
        "occurred_at_basis": "launch_post_created_at",
    }
    if number:
        metrics["yc_launch_number"] = number
    if _former_names(company):
        metrics["yc_former_names"] = _former_names(company)
    pct = percentile_of(votes, window_votes or [])
    if pct is not None:
        metrics["yc_launch_votes_percentile"] = pct
        metrics["yc_launches_in_window"] = len(window_votes or [])
    if team:
        metrics["team_size"] = team
    listed = parse_ts(company.get("launched_at"))
    if listed is not None:
        metrics["days_directory_to_launch"] = (created.date() - listed.date()).days
    return Signal(
        source=SLUG, family=FAMILY, kind="yc_launch",
        entity=entity_hint(company, page_company),
        title=launch_title(votes, top_share(votes, window_votes or []), window_days, repeat),
        occurred_at=iso(created),
        url=url,
        value=votes, unit="votes",
        strength=launch_strength(votes, repeat),
        metrics=metrics,
        people=people_from_page(page_company) if page_company else [],
        text="\n".join(p for p in (_ws(hit.get("title")), _ws(hit.get("tagline")),
                                   company_text(company)) if p),
    )


# --------------------------------------------------------------- fetching

def _polite_get(url: str, **kw: Any) -> str:
    """http.get, plus extra spacing when the request really went to the network."""
    before = http.stats()["requests"]
    text = http.get(url, **kw)
    if http.stats()["requests"] > before:
        time.sleep(YC_SPACING)
    return text


def _in_window(day: str, ctx: Context) -> bool:
    """Is this UTC date (YYYY-MM-DD) inside the lookback and not in the future."""
    latest = (ctx.today + timedelta(days=FUTURE_SLACK_DAYS)).isoformat()
    return ctx.since.isoformat() <= day <= latest


def fetch_launches(ctx: Context) -> tuple[list[dict], bool]:
    """Every launch post created inside the lookback, newest first.

    Returns (hits, complete). complete is False when the endpoint's page
    ceiling was reached before the start of the lookback, in which case the
    hits do not cover the whole window and no "top N%" claim should be made.
    Pages are cached separately, so a post can slide from one page to the
    next between fetches: hits are de-duplicated on id.
    """
    hits: list[dict] = []
    seen: set[Any] = set()
    since = ctx.since.isoformat()
    complete = True
    for page in range(MAX_LAUNCH_PAGES):
        # The endpoint only answers in JSON when Accept says so.
        body = _polite_get(LAUNCHES_URL, params={"hitsPerPage": LAUNCH_PAGE_SIZE, "page": page},
                           headers={"Accept": "application/json"}, ttl=3 * 3600)
        data = json.loads(body)
        page_hits = [h for h in (data.get("hits") if isinstance(data, dict) else None) or []
                     if isinstance(h, dict)]
        if not page_hits:
            break
        for h in page_hits:
            key = h.get("id") or h.get("slug") or h.get("search_path")
            if key is None or key in seen:
                continue
            seen.add(key)
            hits.append(h)
        oldest = min(str(h.get("created_at") or "9") for h in page_hits)
        if oldest[:10] < since:
            break
    else:
        complete = False
        ctx.warn(f"yc launches: hit the {MAX_LAUNCH_PAGES}-page ceiling before reaching {since}")
    return [h for h in hits if _in_window(str(h.get("created_at") or "")[:10], ctx)], complete


def fetch_page(url: str) -> dict:
    """The `props` of a YC company page: `company`, and `launches` (all of its posts)."""
    body = _polite_get(url, headers={"Accept": "text/html"}, ttl=24 * 3600)
    return parse_company_page(body)


def with_page(company: dict, page_company: dict | None) -> dict:
    """The directory record carrying the name and batch its YC page shows.

    The page is the evidence link, and the mirror can be a day behind it. If
    the company was renamed or moved to another batch since the mirror was
    built, the signal must say what the page says. The mirror's name is kept
    as a former name.
    """
    if not page_company:
        return company
    out = dict(company)
    name = _ws(page_company.get("name"))
    if name and name != _ws(company["name"]):
        out["name"] = name
        out["former_names"] = [*_former_names(company), _ws(company["name"])]
    batch = _ws(page_company.get("batch_name"))
    if batch_key(batch) and batch != company["batch"]:
        out["batch"] = batch
    return out


def collect(ctx: Context) -> Iterable[Signal]:
    try:
        meta = http.get_json(META_URL, ttl=3 * 3600)
        batches = recent_batches(meta, ctx.today)
    except Exception as e:
        ctx.warn(f"yc directory: meta.json failed, nothing collected: {type(e).__name__}: {e}")
        return
    if not batches:
        ctx.warn("yc directory: no batches found in meta.json")
        return
    ctx.log(f"yc directory: mirror updated {meta.get('last_updated')}, batches "
            + ", ".join(f"{b['name']} ({b['count']})" for b in batches))

    companies: dict[int, dict] = {}
    for b in batches:
        try:
            for c in http.get_json(b["api"], ttl=3 * 3600):
                if (isinstance(c, dict) and isinstance(c.get("id"), int) and _ws(c.get("name"))
                        and isinstance(c.get("url"), str) and isinstance(c.get("batch"), str)
                        and batch_key(c["batch"])):
                    companies[c["id"]] = c
        except Exception as e:
            ctx.warn(f"yc directory: batch {b['name']} failed: {type(e).__name__}: {e}")

    added_ids: frozenset[int] = frozenset()
    try:
        changes = http.get_json(CHANGES_URL, ttl=3 * 3600)
        added_ids = frozenset(a["id"] for a in changes.get("added") or []
                              if isinstance(a, dict) and isinstance(a.get("id"), int))
    except Exception as e:
        ctx.warn(f"yc directory: changes feed failed: {type(e).__name__}: {e}")

    verdicts: dict[int, dict] = {}
    refused: list[str] = []  # YC says thesis subindustry, the record's text does not
    for cid, c in companies.items():
        if (c.get("status") or "Active") != "Active":
            continue
        try:
            v = thesis_verdict(c)
        except Exception as e:
            ctx.warn(f"yc directory: could not classify {c.get('name')!r}: {e}")
            continue
        if v["on"]:
            verdicts[cid] = v
        elif c.get("industry") == "Industrials" and subindustry_leaf(c) in THESIS_SUBINDUSTRIES:
            refused.append(_ws(c["name"]))
    if refused:
        ctx.log(f"yc directory: {len(refused)} under a YC thesis subindustry not emitted (text below fit "
                f"{MIN_FIT}, or software with only YC's labels on thesis): {', '.join(sorted(refused)[:12])}")

    launches_by_company: dict[int, list[dict]] = {}
    window_votes: list[int] = []
    window_days: int | None = None
    try:
        launches, complete = fetch_launches(ctx)
        window_votes = sorted(int(h.get("total_vote_count") or 0) for h in launches
                              if isinstance(h.get("total_vote_count"), (int, float, type(None))))
        window_days = ctx.lookback_days if complete else None
        for h in launches:
            company = h.get("company")
            cid = company.get("id") if isinstance(company, dict) else None
            if isinstance(cid, int) and cid in verdicts:
                launches_by_company.setdefault(cid, []).append(h)
    except Exception as e:
        ctx.warn(f"yc launches: failed, emitting directory listings only: {type(e).__name__}: {e}")

    # A company is in scope if its listing or one of its launches is inside
    # the lookback. Newest event first, so a limited probe sees fresh ones.
    scoped: list[tuple[str, int]] = []
    for cid in verdicts:
        listed = parse_ts(companies[cid].get("launched_at"))
        stamps = [str(h["created_at"]) for h in launches_by_company.get(cid, [])]
        if listed and _in_window(listed.date().isoformat(), ctx):
            stamps.append(iso(listed))
        if stamps:
            scoped.append((max(stamps), cid))
    scoped.sort(reverse=True)
    in_scope = len(scoped)
    if ctx.limit:
        scoped = scoped[: ctx.limit]
    ctx.log(f"yc directory: {len(companies)} companies, {len(verdicts)} on thesis, {in_scope} in scope "
            f"({len(verdicts) - in_scope} listed before {ctx.since} with no launch since), taking {len(scoped)}")

    for n, (_, cid) in enumerate(scoped):
        c, v = companies[cid], verdicts[cid]
        try:
            page_company = None
            page_launches: list[dict] = []
            if n < MAX_FOUNDER_PAGES and c["url"].startswith(COMPANY_PAGE_PREFIX):
                try:
                    props = fetch_page(c["url"])
                    if props["company"].get("id") != cid:
                        ctx.warn(f"yc page {c['url']}: id {props['company'].get('id')} is not {cid}, ignored")
                    else:
                        page_company = props["company"]
                        page_launches = [l for l in props.get("launches") or [] if isinstance(l, dict)]
                except Exception as e:
                    ctx.warn(f"yc page {c['url']}: {type(e).__name__}: {e}")
            if page_company and (page_company.get("ycdc_status") or "Active") != "Active":
                ctx.log(f"yc directory: {c['name']!r} is {page_company.get('ycdc_status')} on its page, skipped")
                continue
            c = with_page(c, page_company)
            listed = parse_ts(c.get("launched_at"))
            if listed and _in_window(listed.date().isoformat(), ctx):
                sig = batch_signal(c, v, page_company=page_company, added_ids=added_ids)
                if sig:
                    yield sig
            hits = launches_by_company.get(cid, [])
            # Every launch post known for this company: the page lists them
            # all, the board only those inside the lookback.
            known = [str(l.get("created_at") or "") for l in [*page_launches, *hits]]
            for h in hits:
                number: int | None = launch_number(str(h["created_at"]), known)
                if number == 1 and page_company is None:
                    number = None  # without the page an earlier post cannot be ruled out
                sig = launch_signal(h, c, v, page_company=page_company, window_votes=window_votes,
                                    window_days=window_days, number=number)
                if sig:
                    yield sig
        except Exception as e:
            ctx.warn(f"yc directory: {c.get('name')!r} skipped: {type(e).__name__}: {e}")
