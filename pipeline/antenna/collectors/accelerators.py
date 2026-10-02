"""Company pages of two accelerators: a16z speedrun and SOSV, whose HAX program is hard tech.

Each gives every company in its program a public page, read here through the
JSON feeds behind their own sites: the speedrun companies API and the
WordPress REST `company` and `founder` post types on sosv.com and hax.co.
The page goes up early in a company's life: in the speedrun cohort measured on
2026-10-01, 78 of 79 companies were founded in 2025 or 2026 with a median team
of three, and the page already names the founders and links their LinkedIn.

What is emitted, and where each date comes from (metrics.date_basis says which):

  speedrun_cohort   one per on-thesis company in a cohort whose kickoff fell
                    inside the lookback. The speedrun API carries no dates at
                    all, so occurred_at is the day speedrun's own newsletter
                    published its post on that cohort's kickoff week
                    ("cohort_kickoff_post_published", the post URL is in
                    metrics.date_source_url). The post describes the week
                    before it, so the date trails the kickoff by a few days;
                    it is given as a date, not a time. Every company in a
                    cohort shares it. A cohort with no kickoff post is not
                    emitted.
  hax_company       one per on-thesis company whose SOSV portfolio page was
  sosv_company      first published inside the lookback, HAX program or any
                    other SOSV program. occurred_at is the page's `date_gmt`
                    ("post_published"), the earliest across duplicate records
                    and across the two sites. It is when the page went up, not
                    the investment date; the cohort label carries the vintage.
                    Pages trail the cohort (most HAX Seed 2025 pages went up in
                    March 2026); one that goes up two or more calendar years
                    after its cohort is a back-fill and stays in the routine
                    strength band.

SOSV records also carry `acf.now_raising`. It is passed through in metrics as
the record states it and is never a signal of its own: nothing says when the
flag was set. The only date near it is `modified_gmt`, which on 2026-10-01 was
one site-wide re-save (2026-07-30, 616 records) for all five flagged sosv.com
records, three of them companies founded in 2016 or 2017.

"On thesis" always needs the company's own words, never a label alone: both
sites tag loosely ("Robotics" on an AGI lab, "Physical AI" on a company that
grows human cells). What the source states about a company beyond its own
words (speedrun's industry labels, SOSV's categories, membership of the HAX
program) is written into Signal.text in plain words, so the shared classifier
reads the same evidence this module does.

Both lists also hold older, well-known companies, so a company with a stated
founding year more than three calendar years back is skipped (before 2023
when run in 2026); where SOSV states no founding year, a cohort labelled more
than two years back is skipped instead, and a company with neither a founding
year nor a year in its cohort label is skipped too (those are the legacy
numbered cohorts, "IndieBio NY 01", "HAX Accelerator 09").

People come from the speedrun detail record (`founder_set`) and from SOSV
`founder` posts joined on the company term. SOSV's founder post type also
holds finance leads and chiefs of staff; the role is passed through as written.
"""

from __future__ import annotations

import html
import json
import re
from datetime import date, datetime, timezone
from typing import Any, Iterable, Iterator
from urllib.parse import urlparse

from .. import http
from ..models import EntityHint, Person, Signal
from ..thesis import classify
from .base import Context, clean_domain, iso, loose_name, normalize_name

SLUG = "accelerators"
FAMILY = "launch"
STAGE = "discover"
DESCRIPTION = "On-thesis companies newly listed by a16z speedrun and SOSV/HAX, with founders"

SPEEDRUN_API = "https://speedrun-api.a16z.com/api/companies/companies/"
SPEEDRUN_PAGE = "https://speedrun.a16z.com/companies/"
SPEEDRUN_NEWS = "https://speedrun.substack.com/api/v1/archive"
SPEEDRUN_PAGE_SIZE = 400  # one call returned all 321 on 2026-10-01

# sosv.com is the fresher of the two and comes first; hax.co is the cross-check.
SOSV_SITES = (
    ("sosv.com", "https://sosv.com/wp-json/wp/v2"),
    ("hax.co", "https://hax.co/wp-json/wp/v2"),
)
SITE_LABEL = {"sosv.com": "SOSV", "hax.co": "HAX"}
WP_PAGE = 100  # the REST maximum; 101 is a 400
MAX_WP_PAGES = 20
SCAN_FIELDS = (
    "id,date_gmt,modified_gmt,slug,link,title,class_list,tx_company,"
    "acf.tagline,acf.founded_year,acf.website,acf.linked_in,acf.twitter,acf.crunchbase,"
    "acf.now_raising,acf.total_capital_raised,acf.employee_count_range,acf.portal_id"
)
FOUNDER_FIELDS = "id,title,acf.position,acf.linked_in,acf.twitter"

MIN_FIT = 0.3  # the contract's pre-filter
# For records with no usable label: one unmistakable thesis term ("drone",
# "satellite") or two terms. The classifier holds any other lone term to 0.28.
MIN_ONE_LINER_FIT = 0.45
FOUNDED_YEARS_BACK = 3  # founded before today.year - 3 is skipped (2023 when run in 2026)
COHORT_YEARS_BACK = 2  # no founding year stated: a cohort labelled before today.year - 2 is skipped
BACKFILL_YEARS = 2  # a page that goes up this many calendar years after its cohort is a back-fill
BACKFILL_MAX_STRENGTH = 0.3  # and a back-fill stays at the top of the routine band
TEXT_CHARS = 4000
DESCRIPTION_CHARS = 2000
BIO_CHARS = 600

# speedrun industry labels that name a thesis sector, mapped to that sector.
SECTOR_INDUSTRIES = {
    "Robotics": "robotics",
    "Gov Tech / Defense": "defense",
    "Space Tech": "space",
    "Climate / Energy": "energy",
    "Manufacturing / Industrials": "manufacturing",
}
# Labels that say "physical product" without naming a sector.
HARD_INDUSTRIES = {"Hardware", "Deep Tech"}
# A bank or an insurer for defense companies is not a defense company, and a
# sales tool for industrial suppliers is not an industrial company.
OFF_INDUSTRIES = {"Fintech", "Insurtech", "Sales / GTM"}

# A host that turns up in a "website" field and is not the company's own.
# clean_domain already rejects sosv.com, hax.co, a16z.com and the directory
# sites; this is the one SOSV program site it does not know.
NOT_COMPANY_HOSTS = ("indiebio.co",)

# Membership of the HAX program, in plain words. hax.co titles itself
# "Hands-on Venture Capital for Hard Tech" and describes "a program built for
# pre-seed startups solving hard problems with hard tech" (read 2026-10-01).
# It goes into the text of every HAX company so a reader of the signal sees
# which program the page belongs to. It carries no thesis term and is not
# evidence for the classifier: a HAX page passes on its own words and SOSV's
# categories. (The note once ended in a clause with the word "industrial", to
# lift tagline-only pages such as "Building microprocessors/supercomputers for
# real-time edge compute." over the 0.3 gate. The classifier now takes
# "microprocessor" on its own, and across the 38 HAX pages whose bodies were
# read on 2026-10-02 no verdict depended on that clause.)
HAX_PROGRAM_NOTE = "SOSV program: HAX, SOSV's pre-seed program for hard tech"

# SOSV category and trend slugs that mean medicine. HAX funds medical devices
# too, and one stray "army" or "military" in a clinical description is not
# the thesis.
_HEALTH_SLUG = re.compile(r"health|diagnos|therap|medtech|medical|medicine|pharma|neuro|remote-care|drug|allergy")


# ---------------------------------------------------------------- helpers

def _ws(s: Any) -> str:
    return " ".join(str(s or "").split())


def strip_html(s: Any) -> str:
    """Rendered WordPress HTML to one line of plain text."""
    return _ws(html.unescape(re.sub(r"<[^>]+>", " ", str(s or ""))))


def year_of(value: Any) -> int | None:
    """'2023', '2020.0', 2026 -> int; blanks and nonsense -> None."""
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        year = int(float(str(value).strip()))
    except ValueError:
        return None
    return year if 1900 < year < 2100 else None


def _http_url(value: Any) -> str | None:
    s = str(value or "").strip()
    return s if s.startswith("http") else None


def profile_url(value: Any, company: bool = False) -> str | None:
    """A social link that points at a profile, or None.

    Both sources hold free text here: a bare "https://x.com/" with no handle,
    and, in a company's LinkedIn field, a founder's personal /in/ profile.
    Neither is the company's page, so neither is passed on as one.
    """
    s = _http_url(value)
    if not s:
        return None
    path = urlparse(s).path.strip("/")
    if not path:
        return None
    if company and re.match(r"(?:in|pub)/", path + "/", re.I) and "linkedin.com" in s.lower():
        return None
    return s


def company_domain(value: Any) -> str | None:
    """clean_domain, minus an SOSV program site left in a website field."""
    host = clean_domain(str(value or ""))
    if host and any(host == h or host.endswith("." + h) for h in NOT_COMPANY_HOSTS):
        return None
    return host


def _rendered(value: Any) -> Any:
    """WordPress `title` / `content`: {"rendered": ...}, or the bare string in some contexts."""
    return value.get("rendered") if isinstance(value, dict) else value


_PAREN_TAIL = re.compile(r"\s*\(([^()]*)\)\s*$")
_FORMERLY = re.compile(r"^(?:f/?k/?a|formerly(?: known as)?)\s+(.+)$", re.I)


def split_name(title: str) -> tuple[str, list[str]]:
    """SOSV's page title as (name, former names).

    "Novoloop (fka BioCellection)" -> ("Novoloop", ["BioCellection"]). Any
    other trailing note ("(Acq'd by Lexis Nexis)", "(CPTI)") is dropped from
    the name and not turned into anything.
    """
    m = _PAREN_TAIL.search(title)
    if not m or not title[:m.start()].strip():
        return title, []
    was = _FORMERLY.match(m.group(1).strip())
    return title[:m.start()].strip(), [was.group(1).strip()] if was else []


def own_spellings(name: str, text: str) -> list[str]:
    """Other spellings of the name in the company's own words.

    SOSV titles one page "Zetta Joule"; the company's tagline says
    "ZettaJoule", and that is the spelling a filing or a licence will carry.
    Only a word that equals the name once spaces and punctuation are removed
    counts, so nothing is guessed.
    """
    key = re.sub(r"[^a-z0-9]", "", name.lower())
    if len(key) < 6:
        return []
    out: list[str] = []
    for word in re.findall(r"[A-Za-z0-9][A-Za-z0-9.\-]*", text or ""):
        word = word.rstrip(".-")
        if word != name and word not in out and re.sub(r"[^a-z0-9]", "", word.lower()) == key:
            out.append(word)
    return out


def github_login(url: Any) -> str | None:
    """Org or user login from a github.com URL; None for anything else.

    speedrun's github_url field is free text: one company put an Instagram
    link there, another the /organizations/{org} settings URL.
    """
    m = re.match(
        r"https?://(?:www\.)?github\.com/(?:orgs/|organizations/)?([A-Za-z0-9][A-Za-z0-9\-]*)(?:[/?#]|$)",
        str(url or "").strip(), re.I,
    )
    return m.group(1) if m else None


def parse_stamp(value: Any) -> datetime | None:
    """An ISO timestamp, with or without a zone (WordPress *_gmt has none), as aware UTC."""
    s = str(value or "").strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value) if value >= 1 else None


def too_old(founded: int | None, today: date) -> bool:
    return founded is not None and founded < today.year - FOUNDED_YEARS_BACK


def _same_thing(a: str, b: str) -> bool:
    if a in b or b in a:
        return True
    n = max(5, min(len(a), len(b)) - 2)
    return a[:n] == b[:n]


def distinct_terms(terms: Iterable[str]) -> int:
    """How many different things a list of matched thesis terms names.

    The classifier lists "manufacturer" and "manufacturing", "robot" and
    "robotic", "energy" and "energy storage" as separate terms, so the length
    of its list overstates how much a text says: "helps brands and
    manufacturers ... works with existing manufacturing workflows" is one
    passing subject, not two. Terms count once when one contains the other
    or they share a stem.
    """
    groups: list[list[str]] = []
    for term in terms:
        hits = [g for g in groups if any(_same_thing(term, t) for t in g)]
        merged = [term] + [t for g in hits for t in g]
        groups = [g for g in groups if not any(g is h for h in hits)] + [merged]
    return len(groups)


# ---------------------------------------------------------------- speedrun

def kickoff_posts(posts: Iterable[dict]) -> dict[str, dict]:
    """{cohort: {"at": datetime, "url": ..., "title": ...}} from newsletter posts.

    A post counts when its title says "kickoff" and it names exactly one
    cohort right beside that word: "SR007 Kickoff" in the title ("Scenes from
    SR007 Kickoff Week") or, when the title names none, "kickoff of SR005" in
    the subtitle. A cohort code elsewhere in a title that also mentions a
    kickoff does not date that cohort. The earliest such post per cohort wins.
    """
    out: dict[str, dict] = {}
    for p in posts:
        if not isinstance(p, dict):
            continue
        title = _ws(p.get("title"))
        if not re.search(r"\bkick-?off\b", title, re.I):
            continue
        codes = {c.upper() for c in re.findall(r"\b(SR\d{3})\s+kick-?off\b", title, re.I)}
        if not codes and not re.search(r"\bSR\d{3}\b", title, re.I):
            blurb = _ws(p.get("subtitle")) + " " + _ws(p.get("description"))
            codes = {c.upper() for c in re.findall(r"\bkick-?off of (SR\d{3})\b", blurb, re.I)}
        at = parse_stamp(p.get("post_date"))
        url = _http_url(p.get("canonical_url"))
        if len(codes) != 1 or at is None or url is None:
            continue
        cohort = codes.pop()
        if cohort not in out or at < out[cohort]["at"]:
            out[cohort] = {"at": at, "url": url, "title": title}
    return out


def speedrun_own_text(company: dict) -> str:
    """The company's own words: the one-liner and, once fetched, the description."""
    return "\n".join(p for p in (_ws(company.get("preamble")), _ws(company.get("description"))) if p)


def _own_then_labels(own: str, labels: list[str]) -> str:
    """The company's own words, then what the source states about it, inside TEXT_CHARS.

    Long own words are cut, never the label lines after them.
    """
    tail = "\n".join(p for p in labels if p)
    own = own[:max(TEXT_CHARS - len(tail) - 1, 0)].rstrip()
    return "\n".join(p for p in (own, tail) if p)[:TEXT_CHARS]


def speedrun_text(company: dict) -> str:
    inds = [i for i in company.get("industries") or [] if isinstance(i, str)]
    return _own_then_labels(speedrun_own_text(company), ["speedrun industries: " + ", ".join(inds) if inds else ""])


def speedrun_worth_detail(row: dict) -> bool:
    """From the list row alone: could this pass speedrun_verdict once its detail is read?"""
    inds = {i for i in row.get("industries") or [] if isinstance(i, str)}
    if inds & OFF_INDUSTRIES:
        return False
    if inds & (set(SECTOR_INDUSTRIES) | HARD_INDUSTRIES):
        return True
    return classify(_ws(row.get("preamble")))["fit"] >= MIN_ONE_LINER_FIT


def speedrun_verdict(company: dict) -> dict:
    """Is this speedrun company on thesis, and on what evidence.

    The shared classifier must score the full text (own words plus speedrun's
    industry labels) at MIN_FIT or more, and the company's own words must back
    the label. basis names how:

      "industry+text"  a sector label (Robotics, Gov Tech / Defense, ...) and own
                       words that either classify at MIN_FIT or hit that same
                       sector; or a Hardware / Deep Tech label and own words
                       that classify at MIN_FIT
      "one_liner"      no thesis label at all, but the one-liner classifies at
                       MIN_ONE_LINER_FIT: an unmistakable thesis term, or two
      None             not on thesis (including anything labelled Fintech,
                       Insurtech or Sales / GTM)

    When the own words name a single thesis subject (distinct_terms), it has
    to be in the one-liner, where a company says what it is. One word deep in
    a description is a customer list: an LLM API "for red teams, trust &
    safety ... and defense/government workflows" is not a defense company.
    """
    inds = [i for i in company.get("industries") or [] if isinstance(i, str)]
    labels = [i for i in inds if i in SECTOR_INDUSTRIES]
    hard = any(i in HARD_INDUSTRIES for i in inds)
    own = classify(speedrun_own_text(company))
    full = classify(speedrun_text(company))
    headline = classify(_ws(company.get("preamble")))
    passing_mention = distinct_terms(own["terms"]) < 2 and headline["fit"] == 0
    basis = None
    if set(inds) & OFF_INDUSTRIES or full["fit"] < MIN_FIT or passing_mention:
        pass
    elif labels and (own["fit"] >= MIN_FIT or any(SECTOR_INDUSTRIES[i] in own["sectors"] for i in labels)):
        basis = "industry+text"
    elif hard and own["fit"] >= MIN_FIT:
        basis = "industry+text"
    elif not labels and not hard and headline["fit"] >= MIN_ONE_LINER_FIT:
        basis = "one_liner"
    return {
        "on": basis is not None,
        "basis": basis,
        "fit": full["fit"],
        "own_fit": own["fit"],
        "sector": full["sector"],
        "labels": labels,
        "hard": hard,
    }


def speedrun_strength(verdict: dict, founded: int | None, today: date) -> float:
    """How much one speedrun listing should move a partner, 0.15 to 0.72.

    Own-words fit carries most of it; speedrun's sector labels, a Hardware or
    Deep Tech label, and a founding year of this year or last add to it. On
    the 12 on-thesis SR007 companies (2026-10-01) this ran 0.28 to 0.67 with a
    median of 0.49: a company whose own words name one thesis subject sits at
    the routine end, a hardware company squarely in two sectors reaches
    "notable". A listing alone never reaches the "rare" band.
    """
    if verdict["basis"] == "one_liner":
        return round(0.15 + 0.15 * verdict["own_fit"], 3)
    s = 0.02 + 0.46 * verdict["own_fit"] + 0.05 * min(len(verdict["labels"]), 2)
    if verdict["hard"]:
        s += 0.07
    if founded is not None and founded >= today.year - 1:
        s += 0.03
    return round(min(max(s, 0.15), 0.72), 3)


def speedrun_people(company: dict) -> list[Person]:
    """Founders exactly as the detail record lists them."""
    people = []
    founders = company.get("founder_set")
    for f in founders if isinstance(founders, list) else []:
        if not isinstance(f, dict):
            continue
        name = _ws(f"{f.get('first_name') or ''} {f.get('last_name') or ''}")
        if not name:
            continue
        links = {}
        if profile_url(f.get("linkedin_url")):
            links["linkedin"] = profile_url(f.get("linkedin_url"))
        bio = _ws(f.get("introduction"))
        people.append(Person(
            name=name,
            role=_ws(f.get("title")) or None,
            links=links,
            facts={"bio": bio[:BIO_CHARS]} if bio else {},
        ))
    return people


def speedrun_title(company: dict, verdict: dict, team: int | None) -> str:
    # "Joined", not "listed": the date is the cohort's kickoff, and nothing
    # says when the company's page went up.
    head = f"Joined a16z speedrun cohort {company['cohort']}"
    tail = f" with a team of {team}" if team else ""
    labels = verdict["labels"] or [i for i in company.get("industries") or []
                                   if isinstance(i, str) and i in HARD_INDUSTRIES]
    options = []
    if len(labels) > 1:
        options.append(f" under {labels[0]} and {labels[1]}")
    if labels:
        options.append(f" under {labels[0]}")
    for where in options:
        if len(head + where + tail) <= 105:
            return head + where + tail
    return head + tail


def speedrun_signal(company: dict, verdict: dict, kickoff: dict, today: date) -> Signal | None:
    """A speedrun detail record as a Signal, or None without a name, slug or cohort."""
    name, slug, cohort = _ws(company.get("name")), _ws(company.get("slug")), _ws(company.get("cohort"))
    if not name or not slug or not cohort:
        return None
    team = positive_int(company.get("team_size"))
    founded = year_of(company.get("founded_year"))
    people = speedrun_people(company)
    inds = [i for i in company.get("industries") or [] if isinstance(i, str)]
    url = SPEEDRUN_PAGE + slug
    links = {"speedrun": url}
    for key, field in (("linkedin", "linkedin_url"), ("twitter", "x_url")):
        if profile_url(company.get(field), company=True):
            links[key] = profile_url(company.get(field), company=True)
    place = ", ".join(p for p in (_ws(company.get(k)) for k in ("city", "state", "country")) if p)
    login = github_login(company.get("github_url"))
    metrics: dict[str, Any] = {
        "speedrun_cohort": cohort,
        "speedrun_id": company.get("id"),
        "speedrun_industries": inds,
        "date_basis": "cohort_kickoff_post_published",
        "date_source_url": kickoff["url"],
        "date_source_title": kickoff["title"],
        "thesis_basis": verdict["basis"],
        "thesis_fit": verdict["fit"],
        "own_words_fit": verdict["own_fit"],
        "founders_listed": len(people),
        "has_github_org": login is not None,
    }
    if team:
        metrics["team_size"] = team
    if founded:
        metrics["founded_year"] = founded
    return Signal(
        source=SLUG, family=FAMILY, kind="speedrun_cohort",
        entity=EntityHint(
            name=name,
            domain=company_domain(company.get("website_url")),
            github=login,
            one_liner=_ws(company.get("preamble")) or None,
            description=_ws(company.get("description"))[:DESCRIPTION_CHARS] or None,
            location=place or None,
            founded=str(founded) if founded else None,
            links=links,
        ),
        title=speedrun_title(company, verdict, team),
        # A date, not the post's publish second: the kickoff was not at 16:00:07.
        occurred_at=iso(kickoff["at"].date()),
        url=url,
        value=team, unit="people" if team else None,
        strength=speedrun_strength(verdict, founded, today),
        metrics=metrics,
        people=people,
        text=speedrun_text(company),
    )


# ---------------------------------------------------------------- SOSV / HAX

def classes_of(record: dict, taxonomy: str) -> list[str]:
    """Taxonomy slugs from class_list, e.g. classes_of(r, "tx_cohort") -> ["hax-seed-2026"]."""
    prefix = taxonomy + "-"
    return [c[len(prefix):] for c in record.get("class_list") or [] if isinstance(c, str) and c.startswith(prefix)]


def _acf(record: dict) -> dict:
    acf = record.get("acf")
    return acf if isinstance(acf, dict) else {}


def group_records(records: Iterable[dict]) -> list[list[dict]]:
    """Group company records that are the same company.

    The same company appears on sosv.com and hax.co, and sometimes twice on
    one site (a re-import with a new post date). Records join when they share
    `acf.portal_id` or the company-term slug. Each group is sorted oldest first.
    """
    groups: list[list[dict]] = []
    index: dict[tuple, int] = {}
    for r in records:
        keys = []
        pid = _acf(r).get("portal_id")
        if isinstance(pid, (int, float)) and not isinstance(pid, bool) and pid > 0:
            keys.append(("portal", int(pid)))
        for slug in classes_of(r, "tx_company")[:1] or [str(r.get("slug") or "")]:
            if slug:
                keys.append(("slug", slug))
        hits = sorted({index[k] for k in keys if k in index})
        if hits:
            gi = hits[0]
            for other in hits[1:]:  # this record links two groups: fold them together
                groups[gi].extend(groups[other])
                for k, v in index.items():
                    if v == other:
                        index[k] = gi
                groups[other] = []
        else:
            gi = len(groups)
            groups.append([])
        groups[gi].append(r)
        for k in keys:
            index[k] = gi
    out = [g for g in groups if g]
    for g in out:
        g.sort(key=lambda r: str(r.get("date_gmt") or "9"))
    return out


def company_view(group: list[dict]) -> dict | None:
    """One company from its records: earliest publish date, newest non-blank fields."""
    dated = [(parse_stamp(r.get("date_gmt")), r) for r in group]
    dated = [(d, r) for d, r in dated if d is not None and _http_url(r.get("link"))]
    if not dated:
        return None
    first_at, first = min(dated, key=lambda x: x[0])
    name, former = split_name(strip_html(_rendered(first.get("title"))))
    if not name:
        return None
    newest_first = sorted(group, key=lambda r: str(r.get("modified_gmt") or ""), reverse=True)
    acf: dict[str, Any] = {}
    for r in newest_first:
        for k, v in _acf(r).items():
            if k not in acf and v not in ("", None):
                acf[k] = v

    def union(taxonomy: str) -> list[str]:
        seen: list[str] = []
        for r in group:
            for s in classes_of(r, taxonomy):
                if s not in seen:
                    seen.append(s)
        return seen

    content = max((strip_html(_rendered(r.get("content"))) for r in group), key=len, default="")
    tagline = _ws(acf.get("tagline"))
    aliases = list(dict.fromkeys(former + own_spellings(name, tagline + " " + content)))
    return {
        "name": name,
        "aliases": aliases,
        "records": group,
        "first": first,
        "first_at": first_at,
        "acf": acf,
        "tagline": tagline,
        "content": content,
        "cohorts": union("tx_cohort"),
        "programs": union("tx_program"),
        "categories": union("tx_category"),
        "trends": union("tx_trend"),
        "stages": union("tx_stage"),
        "locations": union("tx_location"),
        "sites": sorted({r.get("_site") for r in group if r.get("_site")}),
        # As the newest record states it today. Undated, so a metric and never a signal.
        "now_raising": acf.get("now_raising") is True,
        # The two sites sometimes disagree on the founding year; the earlier one
        # is kept, so a disputed company is skipped rather than passed as young.
        "founded": min((y for y in (year_of(_acf(r).get("founded_year")) for r in group) if y), default=None),
    }


def is_hax(view: dict) -> bool:
    return any(p in ("sosv-hax", "hax") for p in view["programs"]) or any(c.startswith("hax-") for c in view["cohorts"])


def _label(names: dict, taxonomy: str, slug: str) -> str:
    """The site's display name for a taxonomy slug; the slug itself if it was not returned."""
    return (names.get(taxonomy) or {}).get(slug) or slug


def sosv_own_text(view: dict) -> str:
    return "\n".join(p for p in (view["tagline"], view["content"]) if p)


def sosv_text(view: dict, names: dict) -> str:
    """Own words, then SOSV's categories and, for a HAX company, the program it is in."""
    cats = [_label(names, "tx_category", s) for s in view["categories"]]
    cats += [_label(names, "tx_trend", s) for s in view["trends"]]
    return _own_then_labels(sosv_own_text(view), [
        "SOSV categories: " + ", ".join(dict.fromkeys(cats)) if cats else "",
        HAX_PROGRAM_NOTE if is_hax(view) else "",
    ])


def sosv_verdict(view: dict, names: dict) -> dict:
    """Is this SOSV portfolio company on thesis, and on what evidence.

      "hax+text"  in the HAX program (hard tech by construction): the full
                  text, own words plus SOSV's categories, classifies at
                  MIN_FIT and the company's own words carry at least one
                  thesis term. One term that could mean anything needs a
                  category beside it ("Electrifying High Temperature
                  Industrial Heat", filed under Manufacturing & Energy); the
                  program is no evidence, and SOSV's categories never pass a
                  company whose own words carry no term.
      "text"      any other SOSV program (mostly biology): own words must
                  classify at MIN_ONE_LINER_FIT, and a single thesis subject
                  (distinct_terms) must be in the tagline, not a passing word
                  in the page body ("help brands and manufacturers ... work
                  with existing manufacturing workflows", or the pollutant in
                  "organic pollutants such as polychlorobiphenyls (PCBs)")
      None        not on thesis, including anything SOSV files under health

    "fit" is the classifier's score for the text the signal carries.
    """
    own = classify(sosv_own_text(view))
    full = classify(sosv_text(view, names))
    health = any(_HEALTH_SLUG.search(s) for s in view["categories"] + view["trends"])
    hax = is_hax(view)
    passing_mention = distinct_terms(own["terms"]) < 2 and classify(view["tagline"])["fit"] == 0
    basis = None
    if health or full["fit"] < MIN_FIT:
        pass
    elif hax and own["fit"] > 0:
        basis = "hax+text"
    elif not hax and own["fit"] >= MIN_ONE_LINER_FIT and not passing_mention:
        basis = "text"
    return {"on": basis is not None, "basis": basis, "fit": full["fit"],
            "own_fit": own["fit"], "sector": full["sector"], "hax": hax, "health": health}


def cohort_year(view: dict, names: dict) -> int | None:
    """The year in the first cohort label that has one ("HAX Seed 2026" -> 2026)."""
    for slug in view["cohorts"]:
        m = re.search(r"\b(20\d{2})\b", _label(names, "tx_cohort", slug).replace("-", " "))
        if m:
            return int(m.group(1))
    return None


def stale(view: dict, names: dict, today: date) -> bool:
    """Too old to be a find: by founding year when the page states one, else by cohort label.

    With neither a founding year nor a year in the cohort label there is
    nothing to show the company is young, and on these sites that combination
    is the legacy numbered cohorts ("HAX Accelerator 09", "IndieBio NY 01"),
    whose pages still get re-published. Those count as stale.
    """
    if view["founded"] is not None:
        return too_old(view["founded"], today)
    year = cohort_year(view, names)
    return year is None or year < today.year - COHORT_YEARS_BACK


def backfilled(view: dict, names: dict) -> bool:
    """The page went up BACKFILL_YEARS or more calendar years after the cohort it names."""
    year = cohort_year(view, names)
    return year is not None and year <= view["first_at"].year - BACKFILL_YEARS


def capital_raised(view: dict) -> float | None:
    """`acf.total_capital_raised` as dollars, when the page states a positive number."""
    raw = view["acf"].get("total_capital_raised")
    if isinstance(raw, bool):
        return None
    try:
        amount = float(raw)
    except (TypeError, ValueError):
        return None
    return amount if amount > 0 else None


def listing_strength(verdict: dict, view: dict, names: dict) -> float:
    """A new SOSV portfolio page, 0.12 to 0.7.

    A HAX page is a real, dated step by a real company: 0.22 for the program
    plus the fit of the page (own words and SOSV's categories), more when the
    cohort label is the year the page went up (a company's first appearance,
    in its own cohort year) and when the company was founded within two years
    of it. On the three HAX Seed 2026 pages that went up in August 2026 this
    ran 0.43 (a tagline with one unmistakable term) to 0.50.
    Other SOSV programs are mostly biology and start lower. A page that goes
    up BACKFILL_YEARS or more calendar years after its cohort is SOSV catching
    up on a company it already held, not a new entrant: routine, whatever the
    fit.
    """
    listed_year = view["first_at"].year
    cohort = cohort_year(view, names)
    if verdict["hax"]:
        s = 0.22 + 0.28 * verdict["fit"]
        if cohort == listed_year:
            s += 0.08
        if view["founded"] is not None and view["founded"] >= listed_year - 2:
            s += 0.04
        s = min(s, 0.7)
    else:
        s = min(0.12 + 0.3 * verdict["fit"], 0.45)
    if backfilled(view, names):
        s = min(s, BACKFILL_MAX_STRENGTH)
    return round(s, 3)


def sosv_people(founders: Iterable[dict], company_name: str) -> list[Person]:
    """People from `founder` posts, role as written (not all of them are founders)."""
    people: list[Person] = []
    seen: set[str] = set()
    for f in founders:
        if not isinstance(f, dict):
            continue
        name = strip_html(_rendered(f.get("title")))
        # Some titles carry the company: "Chris Wightman – Protogenix". The
        # loose key also takes the company's name without its descriptive
        # last word ("Labs", "Robotics"), so a short form still comes off.
        m = re.match(r"^(.*?)\s+[–—-]\s+(.+)$", name)
        if m and (normalize_name(m.group(2)) == normalize_name(company_name)
                  or loose_name(m.group(2)) == loose_name(company_name)):
            name = m.group(1).strip()
        key = normalize_name(name)
        if not key or key in seen:
            continue
        seen.add(key)
        acf = _acf(f)
        links = {}
        if profile_url(acf.get("linked_in")):
            links["linkedin"] = profile_url(acf.get("linked_in"))
        if profile_url(acf.get("twitter")):
            links["twitter"] = profile_url(acf.get("twitter"))
        # The founder post's own `link` is not passed on: on 2026-10-01 sosv.com
        # redirected every /founder/ URL to /portfolio/, and hax.co served a
        # stub with the name and nothing about the company.
        people.append(Person(name=name, role=_ws(acf.get("position")) or None, links=links))
    return people


def sosv_entity(view: dict, names: dict) -> EntityHint:
    acf = view["acf"]
    links = {SITE_LABEL.get(r.get("_site"), "sosv").lower(): r["link"]
             for r in reversed(view["records"]) if _http_url(r.get("link"))}
    for key, field in (("linkedin", "linked_in"), ("twitter", "twitter"), ("crunchbase", "crunchbase")):
        if profile_url(acf.get(field), company=True):
            links[key] = profile_url(acf.get(field), company=True)
    place = ", ".join(_label(names, "tx_location", s) for s in view["locations"])
    return EntityHint(
        name=view["name"],
        domain=company_domain(acf.get("website")),
        aliases=list(view.get("aliases") or []),
        one_liner=view["tagline"] or None,
        description=view["content"][:DESCRIPTION_CHARS] or None,
        location=place or None,
        founded=str(view["founded"]) if view["founded"] else None,
        links=links,
    )


def _sosv_metrics(view: dict, verdict: dict, names: dict, record: dict, people: list[Person]) -> dict[str, Any]:
    site = record.get("_site")
    api = dict(SOSV_SITES).get(site)
    metrics: dict[str, Any] = {
        "sosv_programs": [_label(names, "tx_program", s) for s in view["programs"]],
        "sosv_cohorts": [_label(names, "tx_cohort", s) for s in view["cohorts"]],
        "sosv_categories": [_label(names, "tx_category", s) for s in view["categories"]],
        "sosv_trends": [_label(names, "tx_trend", s) for s in view["trends"]],
        "sosv_stage": [_label(names, "tx_stage", s) for s in view["stages"]],
        "listed_on": view["sites"],
        "records": len(view["records"]),
        "first_listed": iso(view["first_at"]),
        "now_raising": view["now_raising"],
        "thesis_basis": verdict["basis"],
        "thesis_fit": verdict["fit"],
        "own_words_fit": verdict["own_fit"],
        "people_listed": len(people),
    }
    if api and record.get("id"):
        metrics["record_api_url"] = f"{api}/company/{record['id']}"
    if view["founded"]:
        metrics["founded_year"] = view["founded"]
    if _ws(view["acf"].get("employee_count_range")):
        metrics["employee_count_range"] = _ws(view["acf"].get("employee_count_range"))
    if capital_raised(view):
        metrics["total_capital_raised_usd"] = capital_raised(view)
    return metrics


def listing_title(view: dict, names: dict) -> str:
    """"New SOSV portfolio page in the HAX Seed 2026 cohort".

    The cohort (or, failing that, the program) is named only by the display
    name the site returned for it; a raw slug never goes into a title.
    """
    site = SITE_LABEL.get(view["first"].get("_site"), "SOSV")
    title = f"New {site} portfolio page"
    cohort = next((n for n in ((names.get("tx_cohort") or {}).get(s) for s in view["cohorts"]) if n), None)
    program = next((n for n in ((names.get("tx_program") or {}).get(s) for s in view["programs"]) if n), None)
    for tail in (f" in the {cohort} cohort" if cohort else "", f" in the {program} program" if program else ""):
        if tail and len(title + tail) <= 105:
            return title + tail
    return title


def listing_signal(view: dict, verdict: dict, names: dict, people: list[Person]) -> Signal:
    """The first publication of a company's SOSV / HAX portfolio page."""
    first = view["first"]
    metrics = _sosv_metrics(view, verdict, names, first, people)
    metrics["date_basis"] = "post_published"
    cohort = cohort_year(view, names)
    if cohort is not None:
        metrics["cohort_year"] = cohort
        metrics["backfilled_page"] = backfilled(view, names)
    return Signal(
        source=SLUG, family=FAMILY, kind="hax_company" if verdict["hax"] else "sosv_company",
        entity=sosv_entity(view, names),
        title=listing_title(view, names),
        occurred_at=iso(view["first_at"]),
        url=first["link"],
        strength=listing_strength(verdict, view, names),
        metrics=metrics,
        people=people,
        text=sosv_text(view, names),
    )


# --------------------------------------------------------------- fetching

def wp_list(base: str, post_type: str, params: dict, ttl: float = 6 * 3600) -> list[dict]:
    """Every page of a WordPress REST collection."""
    out: list[dict] = []
    for page in range(1, MAX_WP_PAGES + 1):
        try:
            text, headers = http.request(
                f"{base}/{post_type}", params={**params, "per_page": WP_PAGE, "page": page},
                headers={"Accept": "application/json"}, ttl=ttl, return_headers=True,
            )
        except http.HttpError as e:
            # Asking one page past the end is a 400 (rest_post_invalid_page_number):
            # that is the end of the list, not a failed scan.
            if page > 1 and e.status == 400:
                break
            raise
        rows = json.loads(text)
        if not isinstance(rows, list):
            break
        out.extend(r for r in rows if isinstance(r, dict))
        try:
            total = int((headers or {}).get("x-wp-totalpages") or 0)
        except (TypeError, ValueError, AttributeError):
            total = 0
        if len(rows) < WP_PAGE or (total and page >= total):
            break
    return out


def term_names(base: str, taxonomy: str, slugs: Iterable[str]) -> dict[str, str]:
    """Display names for taxonomy slugs, as the site spells them."""
    want = sorted({s for s in slugs if s})
    out: dict[str, str] = {}
    for i in range(0, len(want), 50):
        rows = http.get_json(f"{base}/{taxonomy}", ttl=24 * 3600, params={
            "slug": ",".join(want[i:i + 50]), "per_page": WP_PAGE, "_fields": "slug,name"})
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict) and row.get("slug") and _ws(row.get("name")):
                out[row["slug"]] = html.unescape(_ws(row["name"]))
    return out


def _fill_content(ctx: Context, views: list[dict]) -> None:
    """Fetch page bodies for the records of the candidate companies (the scan skips them)."""
    for site, base in SOSV_SITES:
        need = {r["id"]: r for v in views for r in v["records"]
                if r.get("_site") == site and r.get("id") and "content" not in r}
        ids = sorted(need)
        for i in range(0, len(ids), WP_PAGE):
            try:
                rows = http.get_json(f"{base}/company", params={
                    "include": ",".join(str(x) for x in ids[i:i + WP_PAGE]),
                    "per_page": WP_PAGE, "_fields": "id,content"})
                for row in rows if isinstance(rows, list) else []:
                    if isinstance(row, dict) and row.get("id") in need:
                        need[row["id"]]["content"] = row.get("content") or {}
            except Exception as e:
                ctx.warn(f"accelerators: {site} page bodies failed: {type(e).__name__}: {e}")


def _fetch_people(ctx: Context, view: dict) -> list[Person]:
    bases = dict(SOSV_SITES)
    for r in view["records"]:
        terms = [t for t in r.get("tx_company") or [] if isinstance(t, int)]
        base = bases.get(r.get("_site"))
        if not terms or not base:
            continue
        try:
            rows = http.get_json(f"{base}/founder", ttl=24 * 3600, params={
                "tx_company": terms[0], "per_page": 20, "_fields": FOUNDER_FIELDS})
        except Exception as e:
            ctx.warn(f"accelerators: founders of {view['name']!r} on {r.get('_site')} failed: {type(e).__name__}: {e}")
            continue
        people = sosv_people(rows if isinstance(rows, list) else [], view["name"])
        if people:
            return people
    return []


def _collect_sosv(ctx: Context) -> Iterator[Signal]:
    records: list[dict] = []
    for site, base in SOSV_SITES:
        try:
            rows = wp_list(base, "company", {"orderby": "date", "order": "desc", "_fields": SCAN_FIELDS})
        except Exception as e:
            ctx.warn(f"accelerators: {site} company scan failed: {type(e).__name__}: {e}")
            continue
        for r in rows:
            r["_site"] = site
        records.extend(rows)
    if not records:
        return

    since, today = ctx.since, ctx.today
    fresh: list[dict] = []
    for group in group_records(records):
        try:
            view = company_view(group)
        except Exception as e:
            ctx.warn(f"accelerators: SOSV record group {group[0].get('slug')!r} skipped: {type(e).__name__}: {e}")
            continue
        if view is None or too_old(view["founded"], today):
            continue  # cohort-label staleness needs the label names, checked below
        if since <= view["first_at"].date() <= today:
            fresh.append(view)
    fresh.sort(key=lambda v: v["first_at"], reverse=True)
    _fill_content(ctx, fresh)
    # Rebuild the views now that the bodies are in.
    fresh = [company_view(v["records"]) or v for v in fresh]

    names: dict[str, dict[str, str]] = {}
    base = SOSV_SITES[0][1]
    for taxonomy, key in (("tx_cohort", "cohorts"), ("tx_category", "categories"), ("tx_trend", "trends"),
                          ("tx_program", "programs"), ("tx_stage", "stages"), ("tx_location", "locations")):
        try:
            names[taxonomy] = term_names(base, taxonomy, (s for v in fresh for s in v[key]))
        except Exception as e:
            ctx.warn(f"accelerators: {taxonomy} names failed, using slugs: {type(e).__name__}: {e}")

    dropped: list[str] = []
    undated: list[str] = []
    n = 0
    for view in fresh:
        try:
            verdict = sosv_verdict(view, names)
            if not verdict["on"]:
                if verdict["hax"] and not verdict["health"]:
                    dropped.append(view["name"])
                continue
            if stale(view, names, today):
                if view["founded"] is None and cohort_year(view, names) is None:
                    undated.append(view["name"])
                continue
            yield listing_signal(view, verdict, names, _fetch_people(ctx, view))
            n += 1
        except Exception as e:
            ctx.warn(f"accelerators: SOSV company {view.get('name')!r} skipped: {type(e).__name__}: {e}")
    ctx.log(f"accelerators: SOSV/HAX {len(records)} records, {len(fresh)} new pages since {since}, "
            f"{n} on thesis")
    if dropped:
        ctx.log(f"accelerators: new HAX pages below thesis fit on their own words, not emitted: "
                f"{', '.join(sorted(dropped))}")
    if undated:
        ctx.log(f"accelerators: on-thesis pages with no founding year and no cohort year, not emitted: "
                f"{', '.join(sorted(undated))}")


def _speedrun_list() -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        page = http.get_json(SPEEDRUN_API, params={"limit": SPEEDRUN_PAGE_SIZE, "offset": offset, "ordering": "name"})
        if not isinstance(page, dict):
            return rows
        results = page.get("results")
        got = [r for r in results if isinstance(r, dict)] if isinstance(results, list) else []
        rows.extend(got)
        offset += SPEEDRUN_PAGE_SIZE
        if not page.get("next") or not got or offset >= 5000:
            return rows


def _collect_speedrun(ctx: Context) -> Iterator[Signal]:
    try:
        posts = http.get_json(SPEEDRUN_NEWS, ttl=12 * 3600,
                              params={"sort": "new", "search": "kickoff", "limit": 50, "offset": 0})
        kickoffs = kickoff_posts(posts if isinstance(posts, list) else [])
    except Exception as e:
        ctx.warn(f"accelerators: speedrun kickoff posts failed, cohorts cannot be dated: {type(e).__name__}: {e}")
        return
    live = {c: k for c, k in kickoffs.items() if ctx.since <= k["at"].date() <= ctx.today}
    if not live:
        ctx.log(f"accelerators: no speedrun cohort kicked off since {ctx.since} "
                f"(kickoff posts found for {', '.join(sorted(kickoffs)) or 'none'})")
        return
    rows = [r for r in _speedrun_list()
            if isinstance(r.get("cohort"), str) and r["cohort"] in live and isinstance(r.get("id"), str)]
    worth = [r for r in rows if speedrun_worth_detail(r)]
    # Newest cohort first, then labelled sectors, then the one-liner's fit, so
    # a limited probe reads the likeliest detail records first.
    worth.sort(key=lambda r: (
        -live[r["cohort"]]["at"].timestamp(),
        not {i for i in r.get("industries") or [] if isinstance(i, str)} & set(SECTOR_INDUSTRIES),
        -classify(_ws(r.get("preamble")))["fit"],
        _ws(r.get("name")).lower(),
    ))
    ctx.log(f"accelerators: speedrun cohorts in the lookback: {', '.join(sorted(live))} "
            f"({len(rows)} companies, {len(worth)} worth a detail call)")
    for row in worth:
        try:
            company = http.get_json(f"{SPEEDRUN_API}{row['id']}/", ttl=24 * 3600)
            if not isinstance(company, dict) or company.get("id") != row["id"] or company.get("cohort") != row["cohort"]:
                ctx.warn(f"accelerators: speedrun detail for {row.get('name')!r} does not match its list row, skipped")
                continue
            if too_old(year_of(company.get("founded_year")), ctx.today):
                continue
            verdict = speedrun_verdict(company)
            if not verdict["on"]:
                continue
            sig = speedrun_signal(company, verdict, live[row["cohort"]], ctx.today)
            if sig:
                yield sig
        except Exception as e:
            ctx.warn(f"accelerators: speedrun company {row.get('name')!r} skipped: {type(e).__name__}: {e}")


def collect(ctx: Context) -> Iterable[Signal]:
    seen: set[str] = set()
    for source in (_collect_sosv, _collect_speedrun):
        try:
            for sig in source(ctx):
                key = sig.entity.domain or normalize_name(sig.entity.name)
                if key not in seen:
                    if ctx.limit and len(seen) >= ctx.limit:
                        return
                    seen.add(key)
                yield sig
        except Exception as e:
            ctx.warn(f"accelerators: {source.__name__} failed: {type(e).__name__}: {e}")
