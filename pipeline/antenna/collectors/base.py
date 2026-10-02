"""What a collector is, and the helpers every collector shares.

A collector is a module in this package exposing:

    SLUG: str                        # unique, snake_case
    FAMILY: str                      # one of models.FAMILIES
    DESCRIPTION: str                 # one line for the run report
    def collect(ctx: Context) -> Iterable[Signal]

`collect` yields Signals and never writes to the database itself. It should
survive partial failure: catch per-item errors, log them with ctx.warn, and
keep going. An exception that escapes fails only that collector.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse

from ..config import LOOKBACK_DAYS


@dataclass
class Context:
    today: date
    lookback_days: int = LOOKBACK_DAYS
    limit: int | None = None  # cap on entities per collector, for quick runs
    warnings: list[str] = field(default_factory=list)
    # Entities already discovered this run, for collectors that enrich rather
    # than discover (hiring, traffic, social). Filled by the orchestrator.
    known: list[dict] = field(default_factory=list)

    @property
    def since(self) -> date:
        return self.today - timedelta(days=self.lookback_days)

    def days_ago(self, n: int) -> date:
        return self.today - timedelta(days=n)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)
        print(f"  ! {msg}", file=sys.stderr)

    def log(self, msg: str) -> None:
        print(f"  {msg}", file=sys.stderr)


# Hosts that are never a company's own domain: code hosts, social sites,
# site builders, shared app platforms and job boards.
_GENERIC_HOSTS = {
    "github.com", "github.io", "gitlab.com", "gitlab.io", "codeberg.org", "bitbucket.org",
    "linkedin.com", "twitter.com", "x.com", "youtube.com", "youtu.be", "medium.com",
    "substack.com", "notion.site", "notion.so", "google.com", "docs.google.com",
    "sites.google.com", "arxiv.org", "huggingface.co", "hf.space", "facebook.com",
    "instagram.com", "tiktok.com", "linktr.ee", "bit.ly", "t.co", "discord.gg", "discord.com",
    "readthedocs.io", "vercel.app", "netlify.app", "herokuapp.com", "railway.app",
    "pages.dev", "workers.dev", "fly.dev", "onrender.com", "web.app", "firebaseapp.com",
    "streamlit.app", "replit.app", "wordpress.com", "wixsite.com", "squarespace.com",
    "webflow.io", "framer.website", "carrd.co", "gmail.com", "outlook.com", "yahoo.com",
    "proton.me", "pypi.org", "npmjs.com", "crates.io", "apple.com", "ycombinator.com",
    "news.ycombinator.com", "producthunt.com", "t.me", "bsky.app", "scholar.google.com",
    "itch.io", "arduino.cc", "hackaday.io", "hackaday.com", "hackster.io", "kickstarter.com",
    "indiegogo.com", "crunchbase.com", "wellfound.com", "angel.co", "workatastartup.com",
    "ashbyhq.com", "greenhouse.io", "lever.co", "workable.com", "rippling.com", "gem.com",
    "dover.com", "breezy.hr", "bamboohr.com", "applytojob.com", "recruitee.com",
    "teamtailor.com", "pinpointhq.com", "personio.de", "personio.com", "smartrecruiters.com",
    "a16z.com", "sosv.com", "hax.co", "sec.gov", "fcc.gov", "faa.gov", "nrc.gov", "uspto.gov",
    "wikipedia.org", "reddit.com", "amazon.com", "amzn.to", "calendly.com", "typeform.com",
    "airtable.com", "loom.com", "vimeo.com", "figma.com", "dropbox.com", "zoom.us",
}

# Not companies: schools, governments, militaries.
_NON_COMPANY_SUFFIXES = (".edu", ".gov", ".mil", ".ac.uk", ".edu.cn", ".ac.jp", ".ac.kr", ".edu.au",
                         ".gov.sg", ".edu.sg", ".ac.nz", ".gov.uk", ".gc.ca", ".ac.in", ".edu.hk", ".ac.cn")


def clean_domain(url_or_host: str | None) -> str | None:
    """Reduce a URL or host to a registrable-looking company domain.

    Returns None for blanks and for generic hosts (github.com, linkedin.com,
    ...) that say nothing about which company this is.
    """
    if not url_or_host:
        return None
    s = url_or_host.strip().lower()
    if not s:
        return None
    if "://" not in s:
        s = "http://" + s
    try:
        host = urlparse(s).netloc.split("@")[-1].split(":")[0]
    except ValueError:
        return None
    host = host.removeprefix("www.")
    if not host or "." not in host or " " in host:
        return None
    if host.endswith(_NON_COMPANY_SUFFIXES):
        return None
    for generic in _GENERIC_HOSTS:
        if host == generic or host.endswith("." + generic):
            return None
    return host


# Legal forms only. Descriptive words ("Robotics", "AI", "Labs") stay in the
# key on purpose: "Genesis AI" and "Genesis Robotics" are different
# companies, and a false merge is worse than a duplicate row. "Company" is
# not stripped either: it is part of the name in "The Boring Company".
_LEGAL = re.compile(
    r"[\s,]+(inc|incorporated|llc|l\.l\.c|corp|corporation|ltd|limited|gmbh|plc|pbc|"
    r"lp|llp|s\.r\.l|srl|b\.v|co\.?,?\s+ltd)\.?$",
    re.I,
)
# Two-letter forms only count when written as a legal form: capitals, at the end.
_LEGAL_CAPS = re.compile(r"[\s,]+(SA|S\.A\.|AB|AG|BV|OY|KK|SAS|PTE|PTY|USA|OÜ|NV|SE)\.?$")
_DESCRIPTIVE = re.compile(
    r"\s+(technologies|technology|labs|lab|systems|ai|robotics|industries|aerospace|"
    r"dynamics|energy|defense|space|group|holdings)$",
    re.I,
)


def strip_legal(name: str) -> str:
    """Drop trailing legal forms: 'Acme Robotics, Inc.' becomes 'Acme Robotics'."""
    s = re.sub(r"\s*\((?:d/?b/?a|formerly)[^)]*\)", "", name.strip(), flags=re.I)
    for _ in range(3):
        new = _LEGAL_CAPS.sub("", _LEGAL.sub("", s)).rstrip(" ,")
        if not new or new == s:
            break
        s = new
    return s


def normalize_name(name: str) -> str:
    """The soft key for matching names across sources.

    Lowercase, legal forms and punctuation removed. 'Salem Robotics, Inc.'
    and 'SALEM ROBOTICS INC' both give 'salem robotics'.
    """
    s = re.sub(r"[\u2019'`]", "", strip_legal(name).lower())
    s = re.sub(r"\(.*?\)", " ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def loose_name(name: str) -> str:
    """A looser key that also drops one descriptive word: 'anduril industries'
    becomes 'anduril'. Only for checks where a miss is worse than a false hit."""
    s = normalize_name(name)
    new = _DESCRIPTIVE.sub("", s).strip()
    return new or s


_SMALL = {"of", "and", "the", "for", "de", "la", "van", "von", "at", "in", "on"}
_KEEP_UPPER = {"AI", "USA", "US", "UAV", "UAS", "RF", "LLC", "LP", "PBC", "II", "III", "IV", "3D", "IO", "XR", "EV", "HQ"}


def display_name(name: str, recase: bool = True) -> str:
    """A name fit to show: no legal suffix, and registry ALL CAPS made readable.

    Mixed-case names are trusted as written, and so is a single all-caps
    word, which is as likely a styled brand as a registry artefact. Pass
    recase=False for names the company wrote itself.
    """
    s = strip_legal(name)
    letters = [c for c in s if c.isalpha()]
    if not recase or not letters or not all(c.isupper() for c in letters):
        return s
    words = s.split()
    if len(words) == 1:
        # One all-caps word may be a styled brand (DRONENX): do not guess.
        return s
    out = []
    for i, w in enumerate(words):
        core = w.strip(".,")
        if core in _KEEP_UPPER or (any(ch.isdigit() for ch in core) and len(core) <= 5):
            out.append(w)
        elif i and core.lower() in _SMALL:
            out.append(w.lower())
        else:
            out.append("-".join(p.capitalize() for p in w.split("-")))
    return " ".join(out)


def parse_date(s: str | None) -> date | None:
    """Best-effort parse of the date formats public APIs actually return."""
    if not s:
        return None
    s = s.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%d", "%m/%d/%Y", "%Y%m%d", "%B %d, %Y", "%b %d, %Y", "%b %d %Y", "%B %d %Y", "%m-%d-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def iso(d: date | datetime | None) -> str | None:
    if d is None:
        return None
    if isinstance(d, datetime):
        return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return d.isoformat()


def squash(x: float, half: float) -> float:
    """Map a non-negative count to 0..1 with diminishing returns.

    `half` is the value that should land at 0.5.
    """
    if x <= 0:
        return 0.0
    return x / (x + half)
