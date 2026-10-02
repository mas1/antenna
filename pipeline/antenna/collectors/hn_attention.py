"""Hacker News attention over time for entities other collectors already found.

For every known entity this reads, from the public Algolia index of Hacker
News, each story and comment that cites the entity's own domain (or links its
GitHub org or repo, or, when it has neither, writes its exact multi-word
name), counts them by week and keeps the lifetime total. It is early because
engineers link a company's site or repo in a thread long before the press
writes about it, and the first time a domain shows up on Hacker News at all
is a dated post anyone can open.

How an entity is looked up (exact keys only, strongest first):

  domain   "skild.ai" as a quoted phrase. The index keeps the dot as a token,
           so this is the literal host. Institutional and shared hosts
           (.edu, .gov, pages.dev, ...) are never used as a key: clean_domain
           rejects most, and a short list here adds the publishers and site
           builders that only matter as a search key.
  github   "github.com/<org>" for a company, "github.com/<owner>/<repo>" for a
           project (taken from the entity's own repo link; a project is one
           repo, not everything its author ever pushed).
  name     only when there is no domain and no GitHub key, and only for names
           of two or more words that are not all generic ("Blue Laser
           Fusion", not "Mantle", not "Advanced Materials Group").

Single-word names are never queried. Measured 2026-10-01 on 19 single-word,
name-only entities from live discovery output: 7 had no hits and most of the
rest were something else (Skytrax the airline rater, X-Star a 27-inch
monitor, Gridfire a weapon in science fiction, "audiance" a misspelling of
audience). A missing signal is cheaper than a wrong one.

Every hit is re-checked on our side before it counts. A domain or GitHub hit
must contain the literal string on a host boundary ("reliable.co" does not
match "reliable.com"). A name hit must contain the phrase with each word
capitalised: "9 Mothers" is the company, "9 mothers can't make a baby in a
month" is an idiom, and Algolia's phrase match also fires across punctuation
("Salem, Robotics student"). If more than a quarter of the raw hits for a
name are the lower-case phrase, the name is ordinary English and the entity
is skipped.

What is emitted. At most two real signals per entity, and only then:

  hn_first_mention  the first verified mention ever falls inside the lookback
                    window and was posted by someone other than the maker.
                    For a domain or GitHub key three more things are searched
                    before that date: the entity's name, the domain's own
                    label ("xgorobot" for xgorobot.com) and, for a company,
                    its GitHub login. Any earlier hit in any form cancels the
                    signal, so "first" means first. Checked live 2026-10-01:
                    xgorobot.com was first linked on 2026-08-27, but the same
                    company's Kickstarter (kickstarter.com/projects/xgorobot)
                    was a story in 2021, so no first mention is claimed.
  hn_attention      mentions in the last 4 weeks run at 1.5x or more the
                    weekly rate of the prior 8, with at least 2 mentions from
                    2 different users.

and, for every other entity that was searched:

  hn_baseline       strength 0.0, metrics only: "Mentioned 412 times on Hacker
                    News to date". It adds nothing to momentum. It is there
                    because the scorer reads metrics.hn_mentions_total as "how
                    widely known is this already", and a well-known company
                    whose attention is flat would otherwise carry no number at
                    all. It has no date of its own (observed_only), so it is
                    dated today, stored as one row per entity and refreshed by
                    each run. It links to the public Hacker News search page
                    for the query that found most of the posts; that page also
                    matches usernames and thread titles, so it can list more
                    than the verified count, and the newest ids that were
                    counted are in metrics.hn_item_ids_latest.
                    A total of 0 is a reading too: "No mentions on Hacker News
                    to date", value 0, linked to the search page for the query
                    that was made. The scorer has to tell "searched and found
                    nothing" from "never measured", and only a row can say the
                    first. Measured 2026-10-02 on the 24-entity sample: 9 of
                    the 23 entities that got a number had none.
                    A 0 says the searches that were made came back empty: the
                    exact key and, where the name is distinctive enough to
                    search, the name. For a single-word name that is the
                    domain or repo alone: its 0 says no post links it, not
                    that nobody ever wrote the word. A 0 is not stated when
                    the name search failed, or when the index holds hits for
                    the name that could not be counted (too many to read,
                    ordinary English, or the name only inside a link or as a
                    slug: as fetched 2026-10-02, "Sudo AI" has no verified
                    mention and two posts linking github.com/SUDO-AI-3D, the
                    company's own repo). Such an entity is skipped, like an
                    ambiguous name.
                    Nothing is emitted for an entity that was not searched (no
                    usable key), that was skipped as ambiguous, or whose search
                    failed: no number is better than a wrong one.

Attention means other people. Three kinds of post are part of the lifetime
total but are left out of the weekly counts and can never be a "first
mention": comments in the monthly "Who is hiring" threads (a job ad, and
hn_hiring already reads those threads), the entity's own Show HN or Launch
HN story (hn_launch already scores that post, points and all), and anything
else posted by the account that made that launch post or by an account whose
username carries the entity's GitHub login, domain label or name.

All three kinds carry metrics.hn_mentions_total, the same number computed the
same way. It has to cover posts that name the company without linking it, or
a well-known company looks obscure (measured 2026-10-01: flocksafety.com is
linked in 69 posts and "Flock Safety" is written in 188, 245 distinct posts
in all; chargerobotics.com 13 and 63, 64 in all). So for every entity keyed
by domain or repo whose name is distinctive enough to search (the same test
as a name key), the name is read too, verified hit by hit, and the total is
the union. Name mentions feed the total, hn_first_seen and the "first" check
only: the weekly counts, the rise and first-mention titles and their evidence
stay on the exact key (hn_citations_total).

A rise is also scaled down for a name Hacker News already knows well: 22
citations of tesla.com in 4 weeks is routine, 22 of a site with 10 lifetime
mentions is not. An entity with flat attention emits only its baseline.

Evidence. A rise or a first mention links to one Hacker News post and is
dated by that post: the first mention itself, or for a rise the best story of the 4 weeks
(else the latest comment). Before linking, the post is looked up in the
official Hacker News API and passed over if it is dead or deleted, so the
link always opens. The ids behind a count are in metrics (hn_item_ids,
hn_item_ids_4w) so the number can be checked post by post.

Calls. One search per key returns every hit there has ever been (Algolia
serves up to 1,000 per query), so the 26 weekly counts, the lifetime total
and the first mention all come from one response, about 1.1 requests per
entity. An entity keyed by domain or repo with a searchable name costs one
more search, for its name. Only a rise or a first mention costs more than
that: one to three counts to keep "first" honest, one item lookup for the
evidence page. Measured 2026-10-01 on a cold cache, when the name was read
only for entities with a rise or a first mention: 256 entities, 288
requests, 68 seconds. Reading the name for every entity adds at most one
request per entity at the same pace, so 400 entities is at most about 850
requests; seconds warm.
A key with more than 1,000 lifetime hits keeps the index's own
total (hn_total_basis = "index_count") and is bucketed from the newest 1,000
when they span 12 weeks; when they do not, it falls back to count-only
windows (hitsPerPage=0): weekly for the last 8 weeks plus two older buckets.
"""

from __future__ import annotations

import calendar
import html
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable
from urllib.parse import quote, urlparse

from .. import http
from ..models import EntityHint, Signal
from .base import Context, clean_domain, squash

SLUG = "hn_attention"
FAMILY = "social"
STAGE = "enrich"
DESCRIPTION = "Hacker News mentions per week for known entities: first mention ever, rises, lifetime total"

SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"
ITEM_URL = "https://hacker-news.firebaseio.com/v0/item/{id}.json"
ITEM_PAGE = "https://news.ycombinator.com/item?id={id}"
# The public search page (stories and comments, all time, newest first, no prefix matching).
SEARCH_PAGE = "https://hn.algolia.com/?dateRange=all&page=0&prefix=false&query={query}&sort=byDate&type=all"

MAX_ENTITIES = 400  # entities queried per run; the orchestrator passes at most 400
PAGE_SIZE = 1000  # Algolia's hard cap on retrievable hits per query
SERIES_WEEKS = 26
RECENT_WEEKS = 4
PRIOR_WEEKS = 8
DETAIL_WEEKS = 8  # count-only fallback: weekly for this many weeks, then two buckets
SEARCH_TTL = 6 * 3600
ITEM_TTL = 3600
WEEK = 7 * 86400

# Rise rule.
MIN_RECENT = 2  # mentions in the last 4 weeks
MIN_AUTHORS = 2  # from different users: one person posting twice is not attention
LIFT_MIN = 1.5  # weekly rate of the last 4 weeks over the weekly rate of the prior 8

# Name keys.
MIN_NAME_WORDS = 2
MIN_NAME_CHARS = 5
MAX_GENERIC_SHARE = 0.25  # lower-case uses of the phrase among raw hits
MIN_SAMPLE_PRECISION = 0.95  # for keys too big to verify hit by hit

# ---------------------------------------------------------------------------
# Strength.
#   breadth  distinct users beyond the first, half at AUTHORS_HALF more
#   reach    points of the best story in the window, half at POINTS_HALF
# The two combine by noisy-OR. A rise is scaled by how far the weekly rate
# moved (full credit at LIFT_FULL, or when the prior 8 weeks were silent).
#
# Calibrated 2026-10-01 on 387 queried entities (296 from live discovery
# output, 91 better-known thesis companies). 14 signals came out:
#   0.2-0.3  ####   2 users and stories with a handful of points; a lone first comment
#   0.3-0.4  ###    2 users after 8 silent weeks; a first mention with a 42-point story
#   0.4-0.5  ###    4 users in 4 weeks
#   0.5-0.7         (8 users, or 3 users and a 100-point story, would land here)
#   0.7-0.8  ###    a front-page story (234 to 618 points) and several users
#   0.8-0.9  #      first mention ever, 4 users, a 480-point story
# The smallest rise that can fire (2 users, no points, lift 1.5) scores 0.23;
# a lone first mention scores 0.25; a debut with 10 users and a 500-point
# story scores 0.86.
#
# Familiarity. Breadth is counted in users, and a household name collects
# users every week, so a rise is scaled down by how much Hacker News already
# talks about the entity: nothing below FAMILIAR_FREE lifetime mentions, then
# towards 1 - FAMILIAR_DAMP. Measured 2026-10-01, before and after:
#   tesla.com   2,536 lifetime, 22 in 4 weeks by 17 users   0.764 -> 0.447
#   spacex.com    666 lifetime, 17 in 4 weeks by 16 users   0.563 -> 0.413
#   openai.com 14,153 lifetime, 344 in 4 weeks              0.630 -> 0.350
#   isaraerospace.com 10 lifetime, a 618-point story        0.787 unchanged
# A first mention is never damped: there is nothing before it.
# ---------------------------------------------------------------------------
AUTHORS_HALF = 5.0
POINTS_HALF = 200.0
LIFT_FULL = 4.0
LIFT_FLOOR = 0.6
RISE_FLOOR = 0.15
RISE_SPAN = 0.80
FIRST_FLOOR = 0.25  # the premium: a lone first mention is a signal, a lone nth mention is not
FIRST_SPAN = 0.68
STRENGTH_CAP = 0.95
FAMILIAR_FREE = 50  # lifetime mentions an entity can have and still count as new to the site
FAMILIAR_HALF = 400.0  # further mentions at which half the damping applies
FAMILIAR_DAMP = 0.6

_FIELDS = ("objectID,created_at,created_at_i,author,title,url,points,num_comments,"
           "story_id,story_title,story_text,comment_text,_tags")
_SEARCHABLE = "url,title,story_text,comment_text"  # never author, never the parent story

_HIRING_THREAD = re.compile(
    r"^\s*(?:Ask|Tell)\s+HN:\s*(?:Who\s+is\s+hiring|Who\s+wants\s+to\s+be\s+hired|"
    r"Freelancer\?\s*Seeking\s+freelancer)", re.I)
_LAUNCH_TITLE = re.compile(r"^\s*(?:Show|Launch)\s+HN\b", re.I)  # for a hit that lost its tags
_TAG = re.compile(r"<[^>]+>")

# Hosts that are not one company's own site, beyond the ones clean_domain
# already rejects (pages.dev, itch.io, kickstarter.com, ... and their
# subdomains). For these a subdomain is fine as a key ("acme.blogspot.com"
# is Acme's); the bare host is not: it would count every site on it.
_SHARED_HOSTS = frozenset({
    "blogspot.com", "framer.app", "weebly.com", "godaddysites.com", "myshopify.com",
    "gitbook.io", "repl.co", "azurewebsites.net", "amazonaws.com", "cloudfront.net",
    "appspot.com", "sourceforge.net", "gitee.com", "openreview.net", "researchgate.net",
    "techcrunch.com", "wix.com", "wordpress.org", "tumblr.com", "ieee.org", "acm.org",
    "nature.com", "crowdsupply.com", "devpost.com", "tindie.com", "patreon.com",
    "gumroad.com", "europa.eu",
})
# clean_domain rejects .edu, .gov, .mil and the national forms it lists
# (.ac.uk, .gov.uk, .ac.in, .edu.sg, .gc.ca, ...). These are the rest: treaty
# bodies and every other country's schools, ministries and forces
# ("nato.int", "unam.edu.mx", "tau.ac.il"), and a bare national root ("gov.uk").
_INSTITUTIONAL = re.compile(r"(?:^|\.)(?:int|(?:edu|gov|mil|ac)\.[a-z]{2})$")

# Trailing legal forms. Abbreviations only: "Company" and "Corporation" can be
# part of what people actually call the firm ("The Boring Company").
_LEGAL_TAIL = re.compile(
    r"[\s,]+(?:inc|incorporated|llc|l\.l\.c|corp|ltd|limited|gmbh|plc|pbc|llp|lp|oy|oü|ou|"
    r"s\.a|b\.v|pty|pte|co\.)\.?,?$", re.I)
_TRAILING_PAREN = re.compile(r"\s*\([^()]*\)\s*$")

# Words that do not make a name distinctive by themselves.
_GENERIC_WORDS = frozenset("""
a an and the of for in on at to & robot robots robotic robotics lab labs system systems
technology technologies tech ai energy power space aerospace aero defense defence dynamics
industries industrial industry group solutions solution research engineering services service
materials fusion nuclear atomics atomic global international general advanced applied new open
deep digital smart autonomous autonomy machine machines intelligence intelligent works company
corporation enterprises holdings partners ventures capital studio studios software hardware
data cloud network networks mobility aviation marine motors electric electronics semiconductor
semiconductors devices instruments computing compute science sciences bio health medical
national american united us usa world innovations innovation development design control
controls products manufacturing sensors sensor drone drones uav uas quantum photonics
""".split())


class Skip(Exception):
    """This entity cannot be measured cleanly; say why and move on."""


# ---------------------------------------------------------------------------
# Keys: what to search for, and how to re-check a hit
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Key:
    kind: str  # "domain" | "github" | "name"
    value: str  # "skild.ai" | "github.com/hebbian-robotics" | "Blue Laser Fusion"

    @property
    def query(self) -> str:
        return f'"{self.value}"'

    @property
    def pattern(self) -> re.Pattern[str]:
        return _pattern(self.kind, self.value)


def _name_word(word: str) -> str:
    """Regex for one word of a name: the capital letter is not negotiable."""
    first, rest = word[0], word[1:]
    if first.isalpha() and first.isupper():
        return re.escape(first) + (f"(?i:{re.escape(rest)})" if rest else "")
    if first.isdigit():
        return f"(?i:{re.escape(word)})"
    return re.escape(word)


_PATTERNS: dict[tuple[str, str], re.Pattern[str]] = {}


def _pattern(kind: str, value: str) -> re.Pattern[str]:
    got = _PATTERNS.get((kind, value))
    if got is None:
        if kind == "domain":
            # Subdomains count ("blog.skild.ai"); a longer host does not
            # ("notskild.ai", "skild.ai.evil.com", "reliable.co" in "reliable.com").
            got = re.compile(rf"(?<![a-z0-9-]){re.escape(value)}(?![a-z0-9-])(?!\.[a-z0-9])", re.I)
        elif kind == "github":
            got = re.compile(rf"(?<![a-z0-9.-]){re.escape(value)}(?![a-z0-9_-])", re.I)
        else:
            body = r"[ \t ]+".join(_name_word(w) for w in value.split())
            got = re.compile(rf"(?<![A-Za-z0-9]){body}(?![A-Za-z0-9])")
        _PATTERNS[(kind, value)] = got
    return got


def _loose_name(value: str) -> re.Pattern[str]:
    """The same phrase in any capitalisation: how ordinary prose would write it."""
    body = r"[ \t ]+".join(re.escape(w) for w in value.split())
    return re.compile(rf"(?<![A-Za-z0-9]){body}(?![A-Za-z0-9])", re.I)


def _other_form(value: str) -> re.Pattern[str]:
    """The same words as a slug, a handle, a host or run together, in any
    case: "SUDO-AI-3D", "Salem_robotics", "skild.ai", "GenesisAI". A sentence
    break between them ("Salem, Robotics student", "Sudo – AI monetization")
    is not the name in any form."""
    body = r"(?:\s+|[-_+./]|%20)?".join(re.escape(w) for w in value.split())
    return re.compile(rf"(?<![A-Za-z0-9]){body}(?![A-Za-z0-9])", re.I)


def usable_domain(raw: str | None) -> str | None:
    """A domain that is one entity's own, fit to search as an exact key."""
    d = clean_domain(raw)
    if not d or d in _SHARED_HOSTS or _INSTITUTIONAL.search(d):
        return None
    return d


def clean_name(name: str | None) -> str:
    """The name as people write it: no trailing "(YC S26)", no "Inc" or "LLC"."""
    s = " ".join((name or "").split())
    for _ in range(4):
        new = _LEGAL_TAIL.sub("", _TRAILING_PAREN.sub("", s)).strip(" ,;")
        if new == s:
            break
        s = new
    return s


def name_key(name: str | None, kind: str = "company") -> Key | None:
    """A name worth searching as an exact phrase, or None."""
    if kind == "person":
        return None  # a personal name or handle says nothing about which person
    core = clean_name(name)
    words = core.split()
    if len(words) < MIN_NAME_WORDS or any(c in core for c in '"()'):
        return None
    if sum(ch.isalnum() for ch in core) < MIN_NAME_CHARS:
        return None
    if all(w.lower().strip(".,") in _GENERIC_WORDS for w in words):
        return None
    if not all(any(ch.isalnum() for ch in w) or w == "&" for w in words):
        return None
    return Key("name", core)


_GH_LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_GH_REPO = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


def github_key(login: str | None, kind: str, links: dict | None) -> Key | None:
    login = (login or "").strip()
    if not _GH_LOGIN.match(login):
        return None
    if kind == "company":
        return Key("github", f"github.com/{login.lower()}")
    # A project is one repository. Take it from the entity's own repo link.
    repo_url = str((links or {}).get("repo") or "")
    try:
        parsed = urlparse(repo_url if "://" in repo_url else "https://" + repo_url)
    except ValueError:
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if parsed.netloc.lower().removeprefix("www.") != "github.com" or len(parts) < 2:
        return None
    owner, repo = parts[0], parts[1].removesuffix(".git")
    if owner.lower() != login.lower() or not _GH_REPO.match(repo):
        return None
    return Key("github", f"github.com/{owner.lower()}/{repo.lower()}")


def keys_for(known: dict) -> list[Key]:
    """Exact keys for one known entity; a name only when nothing better exists."""
    kind = known.get("kind") or "company"
    keys: list[Key] = []
    domain = usable_domain(known.get("domain"))
    if domain:
        keys.append(Key("domain", domain))
    gh = github_key(known.get("github"), kind, known.get("links"))
    if gh:
        keys.append(gh)
    if not keys:
        nk = name_key(known.get("name"), kind)
        if nk:
            keys.append(nk)
    return keys


# ---------------------------------------------------------------------------
# Hits -> mentions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Mention:
    id: str
    ts: int
    created_at: str
    kind: str  # "story" | "comment"
    author: str
    points: int
    comments: int
    title: str  # the story's title, or for a comment the title of its thread
    hiring: bool  # a comment in a monthly hiring thread
    launch: bool  # a Show HN or Launch HN story
    in_headline: bool  # the key is in the story's own title or URL


def _plain(text: str | None) -> str:
    return html.unescape(_TAG.sub(" ", text or ""))


def match_hit(hit: dict, key: Key) -> str | None:
    """"verified" if this hit itself cites the key, "generic" if it only uses
    a name as ordinary lower-case words, None if the index matched something
    else (the parent story, a phrase split by punctuation)."""
    is_comment = "comment" in (hit.get("_tags") or []) or hit.get("comment_text") is not None

    def text(field: str) -> str:
        return str(hit.get(field) or "")

    if key.kind == "name":
        blob = _plain(text("comment_text")) if is_comment else \
            "\n".join([text("title"), _plain(text("story_text"))])
        if key.pattern.search(blob):
            return "verified"
        return "generic" if _loose_name(key.value).search(blob) else None
    # Domains and repo paths: keep the markup, the full link lives in href.
    blob = html.unescape(text("comment_text")) if is_comment else \
        "\n".join([text("url"), text("title"), html.unescape(text("story_text"))])
    return "verified" if key.pattern.search(blob) else None


def _stamp(hit: Any) -> int | None:
    """The hit's Unix time, or None when it has none we can trust."""
    try:
        ts = int(hit["created_at_i"])
    except (KeyError, TypeError, ValueError):
        return None
    return ts if ts > 0 else None  # 0 would read as 1970 and pass for a "first mention"


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def to_mention(hit: dict, key: Key) -> Mention | None:
    ts = _stamp(hit)
    oid = str(hit.get("objectID") or "").strip()
    if ts is None or not oid.isdigit():  # the id becomes the evidence link
        return None
    tags = hit.get("_tags") or []
    is_comment = "comment" in tags or hit.get("comment_text") is not None
    title = str((hit.get("story_title") if is_comment else hit.get("title")) or "")
    headline = "\n".join([str(hit.get("title") or ""), str(hit.get("url") or "")])
    return Mention(
        id=oid, ts=ts, created_at=str(hit.get("created_at") or ""),
        kind="comment" if is_comment else "story",
        author=str(hit.get("author") or ""),
        points=_int(hit.get("points")) if not is_comment else 0,
        comments=_int(hit.get("num_comments")) if not is_comment else 0,
        title=html.unescape(title).strip(),
        hiring=bool(is_comment and _HIRING_THREAD.match(title)),
        launch=bool(not is_comment and ("show_hn" in tags or "launch_hn" in tags
                                        or _LAUNCH_TITLE.match(title))),
        in_headline=bool(not is_comment and key.pattern.search(headline)),
    )


def parse_hits(hits: list[dict], key: Key) -> tuple[list[Mention], int, int]:
    """(verified mentions, generic lower-case uses, hits that were neither)."""
    mentions: list[Mention] = []
    generic = other = 0
    for hit in hits or []:
        if not isinstance(hit, dict):
            other += 1
            continue
        verdict = match_hit(hit, key)
        m = to_mention(hit, key) if verdict == "verified" else None
        if m is not None:
            mentions.append(m)
        elif verdict == "generic":
            generic += 1
        else:
            other += 1
    return mentions, generic, other


def other_forms(hits: list[dict], key: Key) -> int:
    """Raw hits that carry a name in any form, including the ones match_hit
    does not count: inside a link, as a slug or handle, run together. Those
    are never added to a total. They are enough to say the name is not absent
    from Hacker News, so a total of 0 cannot be stated over them."""
    pattern = _other_form(key.value)
    n = 0
    for hit in hits or []:
        if not isinstance(hit, dict):
            continue
        blob = html.unescape("\n".join(str(hit.get(f) or "") for f in ("url", "title", "story_text",
                                                                        "comment_text")))
        n += bool(pattern.search(blob))
    return n


def _only_other_forms(key: Key, n: int) -> str:
    return f"\"{key.value}\" is on Hacker News only inside a link or as a slug ({_n(n, 'hit')})"


# ---------------------------------------------------------------------------
# Windows, the rise rule, strength
# ---------------------------------------------------------------------------

def day_ts(d: date) -> int:
    """Midnight UTC at the start of a calendar day."""
    return calendar.timegm(d.timetuple())


def window_stats(mentions: Iterable[Mention], end_ts: int) -> dict[str, Any]:
    """Counts for the 4 weeks ending at end_ts and the 8 weeks before them."""
    lo4, lo12 = end_ts - RECENT_WEEKS * WEEK, end_ts - (RECENT_WEEKS + PRIOR_WEEKS) * WEEK
    recent = [m for m in mentions if lo4 <= m.ts < end_ts]
    n8 = sum(1 for m in mentions if lo12 <= m.ts < lo4)
    return {
        "n4": len(recent),
        "n8": n8,
        "authors": len({m.author for m in recent if m.author}),
        "stories": sum(1 for m in recent if m.kind == "story"),
        "top_points": max((m.points for m in recent if m.kind == "story"), default=0),
    }


def lift(n4: int, n8: int) -> float | None:
    """Weekly rate of the last 4 weeks over that of the prior 8; None from zero."""
    return None if n8 <= 0 else (n4 / RECENT_WEEKS) / (n8 / PRIOR_WEEKS)


def is_rise(st: dict[str, Any]) -> bool:
    if st["n4"] < MIN_RECENT or st["authors"] < MIN_AUTHORS:
        return False
    lf = lift(st["n4"], st["n8"])
    return lf is None or lf >= LIFT_MIN


def _core(authors: int, top_points: int) -> float:
    breadth = squash(max(authors - 1, 0), AUTHORS_HALF)
    reach = squash(top_points, POINTS_HALF)
    return 1.0 - (1.0 - breadth) * (1.0 - reach)


def familiarity_damp(total: int) -> float:
    """What is left of a rise for an entity with this many lifetime mentions:
    1.0 up to FAMILIAR_FREE, falling towards 1 - FAMILIAR_DAMP for a name the
    site writes about every day."""
    return 1.0 - FAMILIAR_DAMP * squash(max(_int(total) - FAMILIAR_FREE, 0), FAMILIAR_HALF)


def rise_strength(st: dict[str, Any], damp: float = 1.0) -> float:
    """`damp` is familiarity_damp(lifetime total); 1.0 leaves the rise as measured."""
    lf = lift(st["n4"], st["n8"])
    if lf is None:
        scale = 1.0
    else:
        scale = LIFT_FLOOR + (1.0 - LIFT_FLOOR) * min(1.0, max(0.0, (lf - LIFT_MIN) / (LIFT_FULL - LIFT_MIN)))
    core = _core(st["authors"], st["top_points"]) * scale * min(1.0, max(0.0, damp))
    return round(min(STRENGTH_CAP, RISE_FLOOR + RISE_SPAN * core), 3)


def first_strength(authors: int, top_points: int) -> float:
    return round(min(STRENGTH_CAP, FIRST_FLOOR + FIRST_SPAN * _core(authors, top_points)), 3)


def _squeeze(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def own_handles(known: dict) -> frozenset[str]:
    """Squeezed strings an HN username would contain if the account were the
    entity's own: its GitHub login, its domain label, its name run together
    ("Salem_robotics" for Salem Robotics, "rokbenko" for github.com/rokbenko)."""
    out = set()
    gh = _squeeze(known.get("github"))
    if len(gh) >= 4:
        out.add(gh)
    domain = usable_domain(known.get("domain"))
    if domain:
        label = _squeeze(domain.split(".")[0])
        if len(label) >= 5:
            out.add(label)
    name = _squeeze(clean_name(known.get("name")))
    if len(name) >= 6:
        out.add(name)
    return frozenset(out)


def split_attention(mentions: list[Mention],
                    handles: frozenset[str] = frozenset()) -> tuple[list[Mention], int, int]:
    """(what other people posted, hiring-thread comments, the maker's own posts).

    The maker is whoever posted a Show HN or Launch HN story that carries the
    key, plus any account whose username contains one of `handles`;
    everything from those accounts is the entity talking about itself.
    """
    makers = {m.author for m in mentions if m.launch and m.author}
    for m in mentions:
        squeezed = _squeeze(m.author)
        if squeezed and any(h in squeezed for h in handles):
            makers.add(m.author)
    hiring = sum(1 for m in mentions if m.hiring)
    own = sum(1 for m in mentions if not m.hiring and (m.launch or m.author in makers))
    others = [m for m in mentions if not m.hiring and not m.launch and m.author not in makers]
    return others, hiring, own


def weekly_series(mentions: list[Mention], end_ts: int, today: date, *, covered_since: int = 0,
                  with_strength: bool = True, weeks: int = SERIES_WEEKS,
                  damp: float = 1.0) -> list[dict[str, Any]]:
    """Weekly points, oldest first. v = mentions in the 7 days ending on t;
    s = the strength a rise signal would have had on t (0 when there was no
    rise), under the same familiarity `damp` as the signal itself so the last
    point equals the signal's strength. Weeks the fetched sample does not
    fully cover are left out."""
    out = []
    for w in range(weeks - 1, -1, -1):
        hi = end_ts - w * WEEK
        lo = hi - WEEK
        if lo < covered_since:
            continue
        point: dict[str, Any] = {"t": (today - timedelta(days=7 * w)).isoformat(),
                                 "v": sum(1 for m in mentions if lo <= m.ts < hi)}
        if with_strength and hi - (RECENT_WEEKS + PRIOR_WEEKS) * WEEK >= covered_since:
            st = window_stats(mentions, hi)
            point["s"] = rise_strength(st, damp) if is_rise(st) else 0.0
        out.append(point)
    return out


# ---------------------------------------------------------------------------
# Titles
# ---------------------------------------------------------------------------

def _n(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _verb(key_kind: str) -> str:
    return "Named" if key_kind == "name" else "Cited"


def rise_title(key_kind: str, st: dict[str, Any], users_known: bool = True) -> str:
    prior = "none" if st["n8"] == 0 else str(st["n8"])
    base = (f"{_verb(key_kind)} {_n(st['n4'], 'time')} on Hacker News in 4 weeks against {prior} "
            f"in the prior 8")
    if users_known:
        base += f", by {_n(st['authors'], 'user')}"
    tail = f", top story {st['top_points']} points" if st["top_points"] >= 10 else ""
    return base + tail if len(base + tail) < 110 else base


def first_title(key_kind: str, first: Mention, count: int, authors: int, top_points: int) -> str:
    lead = f"First {_verb(key_kind).lower()} on Hacker News"
    if count <= 1:
        if first.kind == "story":
            return f"{lead}, in a story with {_n(first.points, 'point')} and {_n(first.comments, 'comment')}"
        return f"{lead}, in 1 comment so far"
    noun = "mention" if key_kind == "name" else "citation"
    base = f"{lead}, now {_n(count, noun)} by {_n(authors, 'user')}"
    # The link opens the first post, which need not be the big one: say so.
    tail = f", one a {top_points}-point story" if top_points >= 10 else ""
    return base + tail if len(base + tail) < 110 else base


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------

def _search(query: str, *, hits: int, lo: int | None = None, hi: int | None = None) -> dict:
    bounds = [f"created_at_i>={lo}" if lo is not None else None,
              f"created_at_i<{hi}" if hi is not None else None]
    params = {
        "query": query,
        "tags": "(story,comment)",
        "typoTolerance": "false",  # on, it inflates counts about 5x (source card)
        "advancedSyntax": "true",  # quoted query = exact phrase
        "queryType": "prefixNone",
        "restrictSearchableAttributes": _SEARCHABLE,
        "hitsPerPage": hits,
        "attributesToRetrieve": _FIELDS if hits else "objectID",
        "attributesToHighlight": "[]",
        "numericFilters": ",".join(b for b in bounds if b) or None,
    }
    data = http.get_json(SEARCH_URL, params=params, ttl=SEARCH_TTL)
    if not isinstance(data, dict) or "nbHits" not in data:
        raise ValueError(f"unexpected Algolia response for {query}")
    return data


def _count(query: str, lo: int | None, hi: int | None) -> int:
    return int(_search(query, hits=0, lo=lo, hi=hi)["nbHits"])


def _is_public(item_id: str) -> bool:
    """The official item record says the post is still there to open."""
    item = http.get_json(ITEM_URL.format(id=item_id), ttl=ITEM_TTL)
    return isinstance(item, dict) and not item.get("dead") and not item.get("deleted")


def earlier_terms(known: dict) -> list[str] | None:
    """What must be absent from Hacker News before a date for a domain or repo
    citation on that date to be the entity's first appearance: its name, the
    domain's own label, and for a company its GitHub login. None when the name
    is too short to search, so "first" cannot be established at all."""
    core = clean_name(known.get("name"))
    if sum(ch.isalnum() for ch in core) < 4 or '"' in core:
        return None
    terms = [core]
    domain = usable_domain(known.get("domain"))
    if domain:
        terms.append(domain.split(".")[0])  # "xgorobot" also finds kickstarter.com/projects/xgorobot
    login = str(known.get("github") or "").strip()
    if (known.get("kind") or "company") == "company" and _GH_LOGIN.match(login):
        terms.append(login)  # a project's owner is a person with a history of their own
    out: list[str] = []
    for term in terms:
        if term and term.lower() not in {t.lower() for t in out}:
            out.append(term)
    return out


def seen_before(known: dict, before_ts: int) -> bool:
    """True if the entity was already on Hacker News before before_ts under
    its name, its domain label or its GitHub login, or if that cannot be ruled
    out. Used to keep "first" honest when the key was a domain or a repo. Any
    raw hit counts, verified or not: "GenesisAI raises $105M" (2025) is enough
    to say Genesis AI was not new to Hacker News in 2026, even though it fails
    the capitalised-phrase check.
    """
    terms = earlier_terms(known)
    if terms is None:
        return True
    return any(_count(f'"{term}"', None, before_ts) > 0 for term in terms)


def read_name(known: dict, end_ts: int) -> tuple[list[Mention], str | None]:
    """(verified posts that write the entity's name, why its hits could not be
    counted), for the lifetime total of an entity keyed by domain or repo.

    The posts are empty unless the name passes the same tests a name key has
    to pass: distinctive, multi-word, not ordinary lower-case English in more
    than a quarter of its hits, and few enough hits that every one was read.
    The reason is None when the name was not searched or the search was read
    in full; otherwise the index holds hits for the name that are in no
    total, and "no mentions" cannot be said of the entity."""
    key = name_key(known.get("name"), known.get("kind") or "company")
    if key is None:
        return [], None
    data = _search(key.query, hits=PAGE_SIZE, hi=end_ts)
    served = data.get("hits") or []
    nb = _int(data.get("nbHits"))
    if nb > len(served):
        return [], f"\"{key.value}\" has {nb} hits, more than can be read"
    hits = [h for h in served if isinstance(h, dict)]
    mentions, generic, _ = parse_hits(hits, key)
    if generic and generic / (generic + len(mentions)) > MAX_GENERIC_SHARE:
        return [], (f"\"{key.value}\" is ordinary lower-case English in {generic} of "
                    f"{generic + len(mentions)} hits")
    mentions = [m for m in mentions if m.ts < end_ts]
    forms = 0 if mentions else other_forms(hits, key)
    return mentions, _only_other_forms(key, forms) if forms else None


def name_mentions(known: dict, end_ts: int) -> list[Mention]:
    """The posts of read_name alone."""
    return read_name(known, end_ts)[0]


# ---------------------------------------------------------------------------
# One entity
# ---------------------------------------------------------------------------

@dataclass
class Observation:
    keys: list[Key]
    mentions: list[Mention]  # verified, deduplicated, oldest first, before end_ts
    total: int  # lifetime mentions: on the keys, plus by name where that was read
    total_basis: str  # "verified" (every hit re-checked) | "index_count" (Algolia nbHits)
    covered_since: int  # mentions are complete from this timestamp on (0 = all time)
    counts: dict[str, Any] | None = None  # count-only fallback windows, when used
    raw_first_ts: int | None = None  # earliest raw index hit, verified or not
    # Verified posts that write the name of an entity keyed by domain or repo.
    # They count towards `total` and the first-seen date and towards nothing else.
    named: list[Mention] = field(default_factory=list)
    name_read: bool = False  # the name search was made (whatever it found)
    name_failed: bool = False  # ... and did not answer: the total is citations only
    name_gap: str | None = None  # ... and has hits that are in no total: why
    key_counts: list[int] = field(default_factory=list)  # posts counted per key, in key order


def observe(known: dict, keys: list[Key], end_ts: int) -> Observation:
    """Fetch and verify every mention of one entity."""
    by_id: dict[str, Mention] = {}
    covered_since = 0
    index_total = 0
    biggest: Key | None = None
    raw_stamps: list[int] = []
    key_counts: list[int] = []
    for key in keys:
        data = _search(key.query, hits=PAGE_SIZE, hi=end_ts)
        served = data.get("hits") or []
        hits = [h for h in served if isinstance(h, dict)]
        nb = _int(data["nbHits"])
        mentions, generic, other = parse_hits(hits, key)
        stamps = [ts for ts in map(_stamp, hits) if ts is not None]
        raw_stamps += stamps
        if key.kind == "name" and generic and generic / (generic + len(mentions)) > MAX_GENERIC_SHARE:
            raise Skip(f"\"{key.value}\" is ordinary lower-case English in {generic} of "
                       f"{generic + len(mentions)} hits")
        forms = other_forms(hits, key) if key.kind == "name" and not mentions else 0
        if forms:
            raise Skip(_only_other_forms(key, forms))  # nothing to count, and not a zero either
        if nb > len(served):  # more than one response can carry: the sample is the newest 1,000
            if not stamps or len(mentions) / len(hits) < MIN_SAMPLE_PRECISION:
                raise Skip(f"{key.value}: {nb} hits and only {len(mentions)} of the newest "
                           f"{len(hits)} verify")
            covered_since = max(covered_since, min(stamps) + 1)
            if nb > index_total:
                index_total, biggest = nb, key
        mentions = [m for m in mentions if m.ts < end_ts]  # the request already says so; never trust a future date
        key_counts.append(nb if nb > len(served) else len(mentions))
        for m in mentions:
            by_id.setdefault(m.id, m)
    ordered = sorted(by_id.values(), key=lambda m: (m.ts, _int(m.id)))
    if not biggest:
        return Observation(keys, ordered, len(ordered), "verified", 0,
                           raw_first_ts=min(raw_stamps, default=None), key_counts=key_counts)
    obs = Observation(keys, [m for m in ordered if m.ts >= covered_since],
                      max(index_total, len(ordered)), "index_count", covered_since,
                      key_counts=key_counts)
    if covered_since > end_ts - (RECENT_WEEKS + PRIOR_WEEKS) * WEEK:
        obs.counts = bucket_counts(biggest, end_ts)
    return obs


def bucket_counts(key: Key, end_ts: int) -> dict[str, Any]:
    """Count-only windows for a key with too many hits to read: weekly for the
    last DETAIL_WEEKS weeks (newest first), then weeks 9-12 and weeks 13-26."""
    weekly = [_count(key.query, end_ts - (w + 1) * WEEK, end_ts - w * WEEK) for w in range(DETAIL_WEEKS)]
    mid = _count(key.query, end_ts - 12 * WEEK, end_ts - DETAIL_WEEKS * WEEK)
    old = _count(key.query, end_ts - SERIES_WEEKS * WEEK, end_ts - 12 * WEEK)
    return {"weekly": weekly, "weeks_9_12": mid, "weeks_13_26": old}


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _when(m: Mention) -> str:
    """The post's own time, UTC, from the same number the windows are cut on,
    so the date on a signal can never disagree with the week it was counted in."""
    return _iso(m.ts)


def _entity(known: dict) -> EntityHint:
    """The identifiers the entity came in with, unchanged, so the signal lands
    on the entity it was asked about. Nothing here is learned from Hacker News."""
    login = str(known.get("github") or "").strip()
    kind = known.get("kind")
    return EntityHint(
        name=" ".join(str(known.get("name") or "").split()),
        kind=kind if kind in ("company", "project", "person") else "company",
        domain=clean_domain(known.get("domain")),
        github=login if _GH_LOGIN.match(login) else None,  # a login, never a URL
    )


def _headline_text(mentions: list[Mention]) -> str | None:
    """Titles of stories that carry the key in their own headline or link."""
    seen: list[str] = []
    for m in sorted(mentions, key=lambda m: -m.points):
        if m.kind == "story" and m.in_headline and m.title and m.title not in seen:
            seen.append(m.title)
    return "\n".join(seen[:12]) or None


def _evidence(candidates: list[Mention], ctx: Context) -> Mention | None:
    """The first candidate whose page is still public, per the official API."""
    for m in candidates[:4]:
        try:
            if _is_public(m.id):
                return m
        except Exception as e:  # noqa: BLE001 - an unreadable item is just not evidence
            ctx.warn(f"hn_attention: item {m.id} not checked: {type(e).__name__}: {e}")
    return None


def _base_metrics(obs: Observation, known: dict) -> dict[str, Any]:
    key_kind = obs.keys[0].kind
    m: dict[str, Any] = {
        "hn_mentions_total": obs.total,
        "hn_total_basis": obs.total_basis,
        "hn_key": " + ".join(k.value for k in obs.keys),
        "hn_key_kind": key_kind,
    }
    if obs.total_basis == "verified" and (obs.mentions or obs.named):
        m["hn_first_seen"] = _iso(min(x.ts for x in [*obs.mentions, *obs.named]))[:10]
        if key_kind != "name":
            # The count behind "Cited": posts that carry the domain or repo itself.
            m["hn_citations_total"] = len(obs.mentions)
        if obs.named:
            m["hn_name_mentions_total"] = len(obs.named)
        _, hiring, own = split_attention(obs.mentions, own_handles(known))
        if hiring:
            m["hn_hiring_posts_total"] = hiring
        if own:
            m["hn_own_posts_total"] = own
    return m


def add_name_mentions(ctx: Context, known: dict, obs: Observation, end_ts: int) -> None:
    """Widen the lifetime total of an entity keyed by domain or repo with the
    posts that write its name. One search, made once per entity, just before
    whatever it emits: every kind of signal states the same total."""
    if obs.total_basis != "verified" or obs.keys[0].kind == "name" or obs.name_read:
        return
    obs.name_read = True
    try:
        named, obs.name_gap = read_name(known, end_ts)
    except Exception as e:  # noqa: BLE001 - the signal is still true; its total is the citations alone
        obs.name_failed = True
        ctx.warn(f"hn_attention: name of {known.get('name')!r} not read, total is citations only: "
                 f"{type(e).__name__}: {e}")
        return
    obs.named = named
    obs.total = len({m.id for m in obs.mentions} | {m.id for m in named})


def evidence_query(known: dict, obs: Observation) -> str:
    """The query that found the most of the posts in the total: its public
    search page is where a reader sees them. The first key wins a tie."""
    options = [(n, key.query) for key, n in zip(obs.keys, obs.key_counts)]
    by_name = name_key(known.get("name"), known.get("kind") or "company")
    if obs.named and by_name:
        options.append((len(obs.named), by_name.query))
    most = max(n for n, _ in options)
    return next(q for n, q in options if n == most)


def baseline_signal(ctx: Context, known: dict, obs: Observation, end_ts: int) -> Signal | None:
    """The lifetime total by itself, for an entity with no rise and no first
    mention: strength 0.0, so it moves nothing, and metrics the scorer reads
    to judge how well known the entity already is. A total of 0 is emitted
    too: it is what tells "searched and found nothing" from "never measured".
    A 0 is stated only when every search made for the entity was read and
    held nothing that could be it: None when the name search failed, Skip
    when the name has hits that could not be counted."""
    add_name_mentions(ctx, known, obs, end_ts)
    page = SEARCH_PAGE.format(query=quote(evidence_query(known, obs), safe=""))
    if obs.total <= 0:
        if obs.name_failed:
            return None
        if obs.name_gap:
            raise Skip(f"no post cites {obs.keys[0].value} and {obs.name_gap}")
        return Signal(
            source=SLUG, family=FAMILY, kind="hn_baseline",
            entity=_entity(known),
            title="No mentions on Hacker News to date",
            occurred_at=ctx.today.isoformat(),
            url=page,  # the search that came back empty
            value=0, unit="mentions",
            strength=0.0,
            metrics={"hn_mentions_total": 0, "observed_only": True},
        )
    metrics = _base_metrics(obs, known)
    metrics["observed_only"] = True  # a reading with no date of its own: one row, refreshed each run
    if obs.total_basis == "verified":
        counted = {m.id: m for m in [*obs.mentions, *obs.named]}.values()
        metrics["hn_item_ids_latest"] = [m.id for m in sorted(counted, key=lambda m: -m.ts)[:25]]
    # Above 1,000 hits the number is the index's own count, not ours hit by hit.
    about = "about " if obs.total_basis == "index_count" else ""
    return Signal(
        source=SLUG, family=FAMILY, kind="hn_baseline",
        entity=_entity(known),
        title=f"Mentioned {about}{_n(obs.total, 'time')} on Hacker News to date",
        occurred_at=ctx.today.isoformat(),
        url=page,
        value=obs.total, unit="mentions",
        strength=0.0,
        metrics=metrics,
    )


def signals_for(ctx: Context, known: dict, obs: Observation, end_ts: int) -> list[Signal]:
    """Turn one entity's verified mentions into at most two signals."""
    out: list[Signal] = []
    key_kind = obs.keys[0].kind
    attention, _, _ = split_attention(obs.mentions, own_handles(known))
    since_ts = day_ts(ctx.since)
    recent_lo = end_ts - RECENT_WEEKS * WEEK

    # --- first mention ever ------------------------------------------------
    first = obs.mentions[0] if (obs.total_basis == "verified" and obs.mentions) else None
    first_emitted = False
    first_candidate = bool(first and first.ts >= since_ts and attention and attention[0].id == first.id)
    if first_candidate or (not obs.counts and is_rise(window_stats(attention, end_ts))):
        add_name_mentions(ctx, known, obs, end_ts)
    # Only when the very first post was someone else's: a job ad or the maker's
    # own launch post coming first means the debut was not attention.
    if first_candidate:
        if key_kind == "name":
            # The phrase in any other form earlier on (a URL slug, run together,
            # lower case) means "first" cannot be claimed.
            is_first = obs.raw_first_ts is None or obs.raw_first_ts >= first.ts
        elif any(m.ts < first.ts for m in obs.named):
            is_first = False  # written about by name before anyone linked it
        else:
            is_first = not seen_before(known, first.ts)
        if is_first:
            if _evidence([first], ctx):
                authors = len({m.author for m in attention if m.author})
                top = max((m.points for m in attention if m.kind == "story"), default=0)
                metrics = _base_metrics(obs, known)
                metrics.update({
                    "hn_mentions_since_first": len(attention),
                    "hn_authors_since_first": authors,
                    "hn_top_story_points": top,
                    "hn_first_kind": first.kind,
                    "hn_item_ids": [m.id for m in attention[:25]],
                })
                out.append(Signal(
                    source=SLUG, family=FAMILY, kind="hn_first_mention",
                    entity=_entity(known),
                    title=first_title(key_kind, first, len(attention), authors, top),
                    occurred_at=_when(first),
                    url=ITEM_PAGE.format(id=first.id),
                    value=len(attention), unit="mentions",
                    strength=first_strength(authors, top),
                    metrics=metrics,
                    # A point event: no per-point strength, it decays from the day it happened.
                    series=weekly_series(attention, end_ts, ctx.today, with_strength=False),
                    text=_headline_text(attention),
                ))
                first_emitted = True

    # --- a rise ------------------------------------------------------------
    if first_emitted and first is not None and first.ts >= recent_lo:
        return out  # the debut is the rise; one signal says it
    if obs.counts:
        sig = _rise_from_counts(ctx, known, obs, end_ts)
    else:
        sig = _rise_from_mentions(ctx, known, obs, attention, end_ts)
    if sig:
        out.append(sig)
    return out


def _rise_from_mentions(ctx: Context, known: dict, obs: Observation, attention: list[Mention],
                        end_ts: int) -> Signal | None:
    st = window_stats(attention, end_ts)
    if not is_rise(st):
        return None
    recent = [m for m in attention if m.ts >= end_ts - RECENT_WEEKS * WEEK]
    # Evidence: the best story of the window (latest on a tie), else the latest
    # comment. The signal is dated by that post.
    stories = sorted((m for m in recent if m.kind == "story"), key=lambda m: (-m.points, -m.ts))
    comments = sorted((m for m in recent if m.kind == "comment"), key=lambda m: -m.ts)
    shown = _evidence(stories + comments, ctx)
    if shown is None:
        return None
    if shown.ts < day_ts(ctx.since):
        return None
    key_kind = obs.keys[0].kind
    metrics = _base_metrics(obs, known)
    metrics.update({
        "hn_mentions_4w": st["n4"],
        "hn_mentions_prior_8w": st["n8"],
        "hn_authors_4w": st["authors"],
        "hn_stories_4w": st["stories"],
        "hn_comments_4w": st["n4"] - st["stories"],
        "hn_top_story_points_4w": st["top_points"],
        "hn_item_ids_4w": [m.id for m in recent[:25]],
    })
    lf = lift(st["n4"], st["n8"])
    if lf is not None:
        metrics["hn_lift"] = round(lf, 2)
    damp = familiarity_damp(obs.total)
    if damp < 1.0:
        metrics["hn_familiarity_damp"] = round(damp, 3)
    return Signal(
        source=SLUG, family=FAMILY, kind="hn_attention",
        entity=_entity(known),
        title=rise_title(key_kind, st),
        occurred_at=_when(shown),  # the date on the page the link opens
        url=ITEM_PAGE.format(id=shown.id),
        value=st["n4"], unit="mentions/4w",
        strength=rise_strength(st, damp),
        metrics=metrics,
        series=weekly_series(attention, end_ts, ctx.today, covered_since=obs.covered_since, damp=damp),
        text=_headline_text(recent),
    )


def _rise_from_counts(ctx: Context, known: dict, obs: Observation, end_ts: int) -> Signal | None:
    """A key with more hits than can be read: counts come from the index
    (hitsPerPage=0), users and the top story from the newest 1,000 hits."""
    c = obs.counts or {}
    weekly = c["weekly"]  # newest first
    n4 = sum(weekly[:RECENT_WEEKS])
    n8 = sum(weekly[RECENT_WEEKS:DETAIL_WEEKS]) + c["weeks_9_12"]
    sample = [m for m in obs.mentions if m.ts >= end_ts - RECENT_WEEKS * WEEK]
    st = {
        "n4": n4, "n8": n8,
        "authors": len({m.author for m in sample if m.author}),
        "stories": sum(1 for m in sample if m.kind == "story"),
        "top_points": max((m.points for m in sample if m.kind == "story"), default=0),
    }
    if not sample or not is_rise(st):
        return None
    stories = sorted((m for m in sample if m.kind == "story"), key=lambda m: (-m.points, -m.ts))
    shown = _evidence(stories + sorted(sample, key=lambda m: -m.ts), ctx)
    if shown is None:
        return None
    damp = familiarity_damp(obs.total)
    strength = rise_strength(st, damp)
    series = [{"t": (ctx.today - timedelta(days=7 * w)).isoformat(), "v": weekly[w]}
              for w in range(DETAIL_WEEKS - 1, -1, -1)]
    series[-1]["s"] = strength
    metrics = _base_metrics(obs, known)
    metrics.update({
        "hn_mentions_4w": n4, "hn_mentions_prior_8w": n8,
        "hn_mentions_weeks_13_26": c["weeks_13_26"],
        "hn_authors_4w_sampled": st["authors"],
        "hn_top_story_points_4w": st["top_points"],
        "hn_counts_basis": "index_count",
        "hn_familiarity_damp": round(damp, 3),
    })
    lf = lift(n4, n8)
    if lf is not None:
        metrics["hn_lift"] = round(lf, 2)
    if shown.ts < day_ts(ctx.since):
        return None
    sample_spans_window = obs.covered_since <= end_ts - RECENT_WEEKS * WEEK
    return Signal(
        source=SLUG, family=FAMILY, kind="hn_attention",
        entity=_entity(known),
        title=rise_title(obs.keys[0].kind, st, users_known=sample_spans_window),
        occurred_at=_when(shown),  # the date on the page the link opens
        url=ITEM_PAGE.format(id=shown.id),
        value=n4, unit="mentions/4w",
        strength=strength, metrics=metrics, series=series,
        text=_headline_text(sample),
    )


# ---------------------------------------------------------------------------
# Collect
# ---------------------------------------------------------------------------

def collect(ctx: Context) -> Iterable[Signal]:
    cap = min(ctx.limit or MAX_ENTITIES, MAX_ENTITIES)
    end_ts = day_ts(ctx.today + timedelta(days=1))  # through the end of today, UTC
    seen: set[str] = set()
    queried = unkeyed = skipped = emitted = baselines = zeros = 0
    for known in ctx.known or []:
        if queried >= cap:
            break
        try:
            if not isinstance(known, dict) or not str(known.get("name") or "").strip():
                continue
            keys = keys_for(known)
            if not keys:
                unkeyed += 1
                continue
            ident = keys[0].value.lower()
            if ident in seen:
                continue
            seen.add(ident)
            queried += 1
            obs = observe(known, keys, end_ts)
            signals = signals_for(ctx, known, obs, end_ts) if obs.mentions or obs.counts else []
            if not signals:
                # Nothing to say today. The scorer still needs the lifetime
                # total, and a total of 0 is one: it was looked for.
                base = baseline_signal(ctx, known, obs, end_ts)
                if base is not None:
                    baselines += 1
                    zeros += base.value == 0
                    signals = [base]
            for sig in signals:
                emitted += 1
                yield sig
        except Skip as why:
            skipped += 1
            ctx.log(f"hn_attention: skipped {known.get('name')!r}: {why}")
        except Exception as e:  # noqa: BLE001 - one entity must not lose the run
            ctx.warn(f"hn_attention: {known.get('name') if isinstance(known, dict) else known!r}: "
                     f"{type(e).__name__}: {e}")
    ctx.log(f"hn_attention: {queried} entities queried, {unkeyed} without a usable key, "
            f"{skipped} skipped as ambiguous, {emitted} signals ({baselines} of them metrics-only "
            f"baselines, {zeros} with no mentions)")
