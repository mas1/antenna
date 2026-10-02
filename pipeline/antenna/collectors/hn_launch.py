"""Show HN and Launch HN posts about the physical world, with their traction.

Hacker News is where a builder outside any accelerator first shows a robot,
a drone, a board or a chip tool to strangers, often months before there is a
company page, a round or a job post; Launch HN is where a YC company does the
same on a dated post. Every Show HN and Launch HN story in the lookback is
read from the public Algolia index, kept only when its own title names the
thesis, and emitted with its points and comments plus the author's karma,
account age and earlier-story count from the public HN user record.

Why the thesis filter is strict. In a 120-day window there are about 15,000
Show HN posts and the shared classifier passes about 280 of them at
fit >= 0.45, but roughly four in five of those are software wearing a
hardware word: "software factory", "tower defense", "solar system", "on
autopilot", "data enrichment", "weather radar". So a post is kept only if

  1. classify(title + post text) >= 0.45 once those idioms are blanked out.
     The shared classifier holds a single matched term at 0.28 unless it is
     unmistakably physical ("drone", "FPGA", "circuit board", "Verilog",
     "ROS 2"), so one word is enough only when it is one of those; any other
     needs a second term beside it,
  2. the title is not a game, a sky toy, a course, a directory or database, a
     calculator, back-office software (CRM, ERP) or a phone or desktop app, and
  3. the title (for Launch HN: title plus opening paragraph) carries a thesis
     term that is unambiguous on HN ("drone", "FPGA", "lidar", "PCB",
     "robotics"), or an ambiguous one ("robot", "solar", "defense", "battery",
     "rover", an agency name) that is backed by an unambiguous term from the
     same sector in the post text or by a second ambiguous one in the title
     ("humanoid robot"). The shared pattern reads "robotics" as the plural of
     "robotic"; here the noun, which names the field, is told apart from the
     adjective, which is as often a metaphor. Being on the shared
     unmistakable list does not make a word unambiguous here: alone in a
     title in this window, "radar" was mostly weather radar, "TLS Radar" and
     a Reddit monitor, "lunar" was two Lua projects, and "RISC-V" was a
     program that runs on one.

That trades recall for precision on purpose: a hardware post with a generic
title ("We built an 8-bit CPU as EE students") is skipped, and so is a link
post whose title has one thesis word that is not on the shared unmistakable
list and no text to add a second ("A solar-powered, privacy-preserving
vehicle counter (Pi 5 and YOLOv8)", "AI agents design an open chip, the best
one gets fabricated"). In the 120 days to 2026-10-02 the filter kept 58
posts out of 15,038, which became 55 signals once a repost, a text-only
discussion and one third-party link share were dropped.

A Show HN is supposed to be posted by whoever made the thing, but nothing
enforces that. The submitter is attached as a person only when the post
itself supports it: a Launch HN, a username that matches the repository
owner or the site, or a title or post text written in the first person. A
post with none of those from an account with 100 or more earlier stories is
a link share and is not emitted at all.

Entity fields come only from the post: the name is the product name before
the dash or colon (for Launch HN, the company name before "(YC S26)"), else
the repository name, else the site's own host name, else the HN username. No
real names are inferred; the author is recorded by HN username only. The
author's own profile text is attached only on a Launch HN, where the poster
is by convention a founder; a Show HN can be submitted by anyone. A domain is
kept only when it is the builder's own: code hosts, app stores, job boards,
link shorteners, publishers, universities and governments are never one.

Dates are the UTC date HN itself shows for the post, and the run date is the
UTC date too. A story dated after ctx.today (a run pinned to an earlier day,
or one that crosses midnight UTC) is held back until a run whose window
contains it, rather than emitted with a date after ctx.today.
"""

from __future__ import annotations

import calendar
import html
import re
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import quote, urlparse

from .. import http
from ..config import THESIS
from ..models import EntityHint, Person, Signal
from ..thesis import classify
from .base import Context, clean_domain, squash

SLUG = "hn_launch"
FAMILY = "launch"
STAGE = "discover"
DESCRIPTION = "Show HN and Launch HN posts on thesis, with points, comments and author history"

SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"
USER_URL = "https://hacker-news.firebaseio.com/v0/user/{name}.json"
ITEM_PAGE = "https://news.ycombinator.com/item?id={id}"
USER_PAGE = "https://news.ycombinator.com/user?id={name}"

PAGE_SIZE = 1000  # Algolia's hard cap per query; the window is walked by created_at_i
MAX_PAGES = 40  # 120 days is about 16 pages; this is a runaway guard, not a budget
LIST_TTL = 3600  # points move by the hour
USER_TTL = 24 * 3600
HISTORY_TTL = 7 * 24 * 3600  # stories before a fixed timestamp do not change
MIN_FIT = 0.45
LEAD_CHARS = 600  # how much of a Launch HN post counts as its "opening"
BIO_CHARS = 500
TEXT_CHARS = 5000

_FIELDS = "objectID,title,url,story_text,author,points,num_comments,created_at,created_at_i,_tags"

# ---------------------------------------------------------------------------
# Strength calibration. Measured 2026-10-02 over 2026-06-04..2026-10-02:
# 15,038 Show HN / Launch HN stories, points p50 2, p75 4, p90 8, p95 23,
# p99 156. Among the 55 this collector keeps: engagement (points + half the
# comments) p25 2, p50 4, p75 36, p90 138, max 315.
# ENGAGEMENT_HALF puts that p90 at 0.67 ("top decile for this source"), p75
# at 0.43 ("solid") and the median at 0.20 ("it exists").
# ---------------------------------------------------------------------------
ENGAGEMENT_HALF = 60.0
BASE_FLOOR = 0.15
BASE_SPAN = 0.75
LAUNCH_FLOOR = 0.40  # a YC company's dated public launch is a real step by itself
LAUNCH_BONUS = 0.05
FIRST_STORY_MIN_POINTS = 20  # "does well": above the p90 of all Show HN
FIRST_STORY_BONUS = 0.07
FRESH_ACCOUNT_DAYS = 30
FRESH_ACCOUNT_BONUS = 0.03
STRENGTH_CAP = 0.97
# An account with this many earlier stories that posts someone else's repo
# with no first-person word anywhere is sharing a link, not showing its work.
SERIAL_SUBMITTER_STORIES = 100

# ---------------------------------------------------------------------------
# Thesis filter
# ---------------------------------------------------------------------------

# Software idioms that borrow a thesis word. Blanked before classification.
# Only what HN adds: classify() already blanks config.THESIS_FALSE_FRIENDS
# ("sensor fusion", "batteries included", "robots.txt", "factory pattern",
# "solar wind", "army of", "satellite office", "drone music", "model fusion",
# "robotic process automation", "lunar new year", "satellite tv", "on the
# radar", "cyber defense", ...). The "defence" spellings are kept here
# because the shared list spells those phrases with an s only.
_IDIOMS = re.compile(
    r"""
      \b(?:software|code|coding|ai|agent|agentic|content|kernel|dark|prompt|token|app|startup|feature)
          [\s-]+factor(?:y|ies)\b
    | \bfactor(?:y|ies)[\s-]+(?:function|settings|template|simulator|game)\b
    | \b(?:tower|cyber|civil|self|ddos|zone|base|immune)[\s-]*defen[cs]e\b
    | \bdefen[cs]e[\s-]+(?:in[\s-]depth|layers?|mechanisms?)\b
    | \bcyber[\s-]*(?:weapons?|warfare)\b
    | \bbattery[\s-]+(?:life|drain|usage|level|saver|percentage|indicator|status|friendly|health)\b
    | \bnot\s+a\s+robot\b
    | \brobotic[\s-]+(?:voices?|sounding|speech)\b
    | \bsolar[\s-]+(?:system|eclipse|flares?|return)\b
    | \blunar[\s-]+(?:calendar|birthday|phases?|eclipse)\b
    | \bon[\s-]+autopilot\b | \b(?:marketing|sales|seo|ads?)[\s-]+autopilot\b
    | \b(?:data|lead|email|contact|transaction|cell|company|profile)[\s-]+enrichment\b
    | \b(?:llm|prompt|rank|score|funk|jazz)[\s-]+fusion\b
    | \bswiss[\s-]+army\b
    | \bself[\s-]driving[\s-]+(?:sales|marketing|company|lab|database|startup|agents?)\b
    | \bsatellite[\s-]+(?:imagery|images?|photos?|views?|maps?|internet)\b
    | \bdrone[\s-]+(?:synth\w*|instrument|sounds?|notes?|footage|shots?|videos?|views?|flyover|photography)\b
    | \bdrone[\s-]+flights?\s+over\b
    | \b(?:ambient|microtonal)\s+drones?\b
    | \bchip-?8\b | \bchiptune\w*\b | \brisc-?5\b
    | \bservo(?=\s+(?:browser|engine|web\s?view|layout))
      # Something drawn "as a circuit board" or that looks "like a circuit
      # board" is a picture of one: code rendered as an isometric board.
    | \b(?:as|like)\s+(?:an?\s+)?(?:[\w-]+\s+){0,2}circuit[\s-]+boards?\b
      # The shared list has "phased array" for radar. One made of ultrasonic
      # transducers or microphones steers sound, and is not defense hardware.
    | \b(?:ultrasonic|ultrasound|acoustic|microphone|speaker|audio)[\s-]+phased[\s-]+arrays?\b
    """,
    re.I | re.X,
)

# Title words that mark a post as something other than a builder's product.
_VETO = re.compile(
    r"""
      (?:^|[–—:|-]\s*)learn\b                      # "Learn robotics with ..." but not "how robots learn"
    | \b(?:
      games?|gaming|puzzles?|chess|shooter|multiplayer|roguelike|arena|battle|playable|red\s+alert
    | soccer|football|(?:landing|orbit\w*|gravity|war|city|life)\s+simulat\w+
    | (?:satellite|iss|flight|plane|aircraft|ship|asteroid|time|habit|period|price)\s+(?:tracker|spotter)
    | globe|planetarium|telescope|(?:solar|lunar|total)\s+eclipse|asteroids?
    | directory|registry|catalog(?:ue)?|databases?|wiki|atlas\s+of|map\s+of|job\s+board|jobs|hiring
    | newsletter|digest
    | courses?|textbook|tutorial|flashcards?
    | crm|erp|mrp                                  # back-office software sold to a factory
    | calculators?|idea\s+sketch|thought\s+experiment
    | ios|macos|mac\s+app|android|iphone|ipad|widgets?|(?:chrome|browser)\s+extension|menu\s+bar|new\s+tab
    | synths?|synthesi[sz]ers?
    | insurance|brokerage
    )\b""",
    re.I | re.X,
)

# Thesis terms that, on HN, are a metaphor or a consumer feature more often
# than the real thing. Alone in a title they prove nothing. The shared
# pattern accepts a trailing plural, so each entry covers its plural too.
# Some are on the shared unmistakable list (config.THESIS_UNAMBIGUOUS): that
# list serves every source, and HN titles use these words more loosely.
_SOFT_TERMS = frozenset({
    # robotics ("rover" is a product name as often as a vehicle; "kinematics"
    # is also animation and sports analysis)
    "robot", "robotic", "humanoid", "exoskeleton", "manipulator", "manipulation",
    "locomotion", "dexterous", "slam", "physical ai", "physical intelligence",
    "embodied ai", "embodied intelligence", "rover", "kinematics",
    # autonomy
    "autonomous", "autonomy", "autopilot", "self-driving", "swarm", "swarming",
    "unmanned", "path planning", "loitering",
    # defense ("radar" is weather radar, a market or Reddit monitor, a TLS
    # scanner; an agency name says who buys, not what was built)
    "defense", "defence", "military", "weapon", "missile", "radar",
    "battlefield", "tactical", "contested", "interceptor", "isr", "national security",
    "command and control", "kill chain",
    "army", "navy", "air force", "space force", "marine corps", "dod", "diu", "darpa", "socom",
    "afwerx", "spacewerx", "department of defense", "department of war", "cdao",
    "washington headquarters services",
    # energy
    "nuclear", "fusion", "enrichment", "battery", "solar", "hydrogen", "turbine",
    "energy", "grid", "reactor", "plasma", "smr", "nrc", "ferc", "solid-state", "megawatt",
    "gigawatt", "kwh", "mwh", "interconnection", "electrification", "heat pump", "power plant",
    "data center power", "transmission line",
    # manufacturing
    "manufacturing", "factory", "casting", "forging", "composites", "industrial",
    "cad", "cam", "foundry", "fabrication", "supply chain", "process control",
    "reshoring", "onshoring", "3d printing", "bill of materials", "manufacturer",
    # A geometry library a program is built on says what it is made of, not
    # what it is for: FreeCAD and OpenCASCADE sit under architecture tools too.
    "freecad", "opencascade",
    # semiconductors ("RISC-V" in a title has been a program that runs on
    # one: a BIOS, an operating system port)
    "chip", "silicon", "asic", "tsmc", "risc-v", "edge inference",
    # space ("lunar" is a calendar and a name for Lua projects; NASA is an
    # agency, like the ones under defense)
    "satellite", "lunar", "orbital", "in-space", "rocket", "constellation",
    "propulsion", "reentry", "aerospace", "rideshare", "nasa",
})

# The shared pattern folds "robotics" into the term "robotic". On HN the two
# are not alike: the adjective is a metaphor half the time ("robotic voice",
# "robotic lawnmowers" in a cellular automaton), the noun names the field.
# So the noun is kept apart as a term of its own, and it is not a soft one.
_ROBOTICS = re.compile(r"(?<![a-z0-9])robotics(?![a-z0-9])", re.I)

_TERM_SECTOR = {
    term: sector
    for sector, groups in THESIS.items()
    for group in groups.values()
    for term in group
}
_TERM_SECTOR["robotics"] = "robotics"


def strip_html(text: str | None) -> str:
    """HN story text to plain text, paragraphs kept as blank lines."""
    if not text:
        return ""
    t = re.sub(r"(?i)</?(?:p|pre)>", "\n\n", text)
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t)
    t = re.sub(r"[ \t\r\f\v]+", " ", t)
    t = re.sub(r" ?\n ?", "\n", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def _blank_idioms(text: str) -> str:
    return _IDIOMS.sub(" ", text)


def _terms(text: str) -> set[str]:
    """Thesis terms in a text, plus "robotics" where the noun itself is written.

    classify() caps its list at 12, so go by paragraph.
    """
    out: set[str] = set()
    for para in text.split("\n\n"):
        if para.strip():
            found = classify(para)["terms"]
            out.update(found)
            if "robotic" in found and _ROBOTICS.search(para):
                out.add("robotics")
    return out


def thesis_gate(title: str, story: str, is_launch: bool) -> dict[str, Any]:
    """Decide whether a post is about the thesis. Returns {"ok", "why", "fit", "sector", "terms"}.

    `title` is the title without its "Show HN:" prefix, `story` the plain
    post text (may be empty). For a Launch HN the opening of the post counts
    as part of the title, because that is where YC companies say what they do.
    """
    out: dict[str, Any] = {"ok": False, "why": "", "fit": 0.0, "sector": "other", "terms": []}
    zone_raw = title + ("\n\n" + story[:LEAD_CHARS] if is_launch else "")
    zone_terms = _terms(_blank_idioms(zone_raw))
    if not zone_terms:
        # Cheap exit for 98% of posts: nothing in the title to classify.
        out["why"] = "no thesis term in title"
        return out

    story_c = _blank_idioms(story)
    c = classify(_blank_idioms(title) + " \n " + story_c)
    out.update(fit=c["fit"], sector=c["sector"], terms=c["terms"])
    if c["fit"] < MIN_FIT:
        out["why"] = "fit"
        return out
    if _VETO.search(title):
        out["why"] = "veto"
        return out
    if any(t not in _SOFT_TERMS for t in zone_terms):
        out["ok"] = True
        out["why"] = "title names the thesis"
        return out
    hard_sectors = {_TERM_SECTOR.get(t) for t in _terms(story_c) if t not in _SOFT_TERMS}
    if any(_TERM_SECTOR.get(t) in hard_sectors for t in zone_terms):
        out["ok"] = True
        out["why"] = "ambiguous title term backed by the post text"
        return out
    # "humanoid robot", "nuclear fusion": two different ambiguous terms from
    # one sector, both in the title proper, back each other. The shared
    # pattern reads a plural as its term, so "robots and more robots" is one.
    per_sector: dict[str | None, set[str]] = {}
    for t in _terms(_blank_idioms(title)):
        per_sector.setdefault(_TERM_SECTOR.get(t), set()).add(t)
    if any(len(v) >= 2 for v in per_sector.values()):
        out["ok"] = True
        out["why"] = "two title terms from one sector"
        return out
    out["why"] = "ambiguous term only"
    return out


# ---------------------------------------------------------------------------
# Title and URL parsing
# ---------------------------------------------------------------------------

_PREFIX = re.compile(r"^\s*(show|launch)\s+hn\s*[:;]\s*", re.I)
_YC_BATCH = re.compile(r"\(\s*YC\s+([A-Z]{1,2}\d{2})\s*\)", re.I)
# First separator between a product name and its description.
_SEP = re.compile(r"\s*[–—]\s*|\s+-{1,2}\s+|:\s+|\s+\|\s+|,\s+(?=(?:a|an|the)\s)")
_VERSION_TAIL = re.compile(r"\s+v\d+(?:\.\d+)*$", re.I)
_TRAILING_TAG = re.compile(r"\s*\[(?:video|pdf|audio)\]\s*$", re.I)
# A head that opens with one of these is a sentence, not a product name.
_NOT_A_NAME = {
    "i", "i've", "i’ve", "i'm", "i’m", "i'd", "we", "we've", "we’ve", "we're", "we’re", "my", "our",
    "a", "an", "the", "how", "why", "what", "when", "if", "this", "these", "there", "it", "its",
    "building", "built", "build", "making", "made", "make", "using", "running", "run",
    "introducing", "announcing", "new", "open", "open-source", "opensource", "free", "live",
    "real-time", "realtime", "simple", "tiny", "fast", "yet", "show", "ask", "tell", "hey",
}

# Hosts where many unrelated builders publish, on top of the ones
# base.clean_domain already rejects (code hosts, site builders, job boards,
# crowdfunding, .edu/.gov/.mil). A URL there says nothing about whose domain
# it is. Every entry was checked against clean_domain: none is rejected there.
_SHARED_HOSTS = (
    # App hosting, tunnels and previews.
    "repl.co", "replit.com", "lovable.app", "appspot.com", "azurewebsites.net", "cloudfront.net",
    "amazonaws.com", "modal.run", "deno.dev", "val.run", "surge.sh", "glitch.me", "glitch.com",
    "neocities.org", "pythonanywhere.com", "ngrok.io", "ngrok-free.app", "trycloudflare.com",
    "tiiny.site", "framer.app", "super.site", "softr.app", "bubbleapps.io", "bolt.new", "v0.app",
    "v0.dev", "gradio.live", "puter.com", "codeberg.page",
    # Docs, code and package hosts.
    "gitbook.io", "mintlify.app", "readme.io", "github.dev", "githubusercontent.com", "sr.ht",
    "sourceforge.net", "gitee.com", "tangled.org", "pkg.go.dev", "docs.rs", "rubygems.org",
    "docker.com", "codepen.io", "codesandbox.io", "stackblitz.com", "jsfiddle.net",
    "observablehq.com", "kaggle.com", "ollama.com", "replicate.com", "devpost.com",
    # Maker platforms and hardware marketplaces.
    "instructables.com", "printables.com", "thingiverse.com", "makerworld.com", "cults3d.com",
    "crowdsupply.com", "tindie.com", "wokwi.com", "tinkercad.com", "oshwlab.com", "easyeda.com",
    "grabcad.com", "onshape.com",
    # Blogs, social sites and media.
    "dev.to", "hashnode.dev", "bearblog.dev", "blogspot.com", "tumblr.com", "ghost.io",
    "mataroa.blog", "write.as", "telegra.ph", "twitch.tv", "threads.net", "mastodon.social",
    "imgur.com", "soundcloud.com", "bandcamp.com", "spotify.com", "telegram.org",
    "stackoverflow.com", "stackexchange.com", "quora.com", "archive.org",
    # Stores, plugin stores, payment pages, forms and AI chat shares.
    "steampowered.com", "claude.ai", "chatgpt.com", "wordpress.org", "obsidian.md", "shopify.com",
    "myshopify.com", "apify.com", "jetbrains.com", "raycast.com", "open-vsx.org", "flathub.org",
    "f-droid.org", "snapcraft.io", "greasyfork.org", "microsoft.com", "visualstudio.com",
    "mozilla.org", "etsy.com", "canva.com", "gumroad.com", "patreon.com", "ko-fi.com",
    "buymeacoffee.com", "tally.so",
    # Job boards, link shorteners, accelerators and launch directories.
    "indeed.com", "tinyurl.com", "goo.gl", "ow.ly", "buff.ly", "rebrand.ly", "lnkd.in", "cutt.ly",
    "is.gd", "tiny.cc", "dub.sh", "techstars.com", "antler.co", "joinef.com", "peerlist.io",
    "indiehackers.com", "betalist.com", "uneed.best", "devhunt.org", "alternativeto.net",
    "terminaltrove.com",
    # Publishers and the press: a Show HN that links to coverage is not on its own site.
    "zenodo.org", "doi.org", "researchgate.net", "ssrn.com", "openreview.net", "biorxiv.org",
    "osf.io", "nature.com", "science.org", "ieee.org", "acm.org", "sciencedirect.com",
    "springer.com", "mdpi.com", "techcrunch.com", "theverge.com", "wired.com", "arstechnica.com",
    "nytimes.com", "bloomberg.com", "reuters.com", "wsj.com", "ft.com", "forbes.com",
    "businessinsider.com",
)
# base.clean_domain drops .edu, .gov, .mil and the national forms it spells
# out (.ac.uk, .edu.au, .gov.sg, .ac.nz, ...). This covers the rest of the
# pattern for any country code: foo.edu.br, foo.gov.au, foo.ac.za. A bare
# .ac (rover.ac) is an ordinary TLD and stays.
_INSTITUTION_HOST = re.compile(r"\.(?:edu|gov|mil)\.[a-z]{2}$|\.ac\.[a-z]{2}$")
# github.com/<these>/... are site sections, not accounts.
_GITHUB_RESERVED = {
    "orgs", "sponsors", "topics", "marketplace", "features", "collections", "trending",
    "about", "pricing", "explore", "settings", "apps", "login", "search", "enterprise",
}


def own_domain(url: str | None) -> str | None:
    """The builder's own host from a URL, or None for code hosts and shared platforms."""
    d = clean_domain(url)
    if not d or _INSTITUTION_HOST.search(d):
        return None
    for shared in _SHARED_HOSTS:
        if d == shared or d.endswith("." + shared):
            return None
    return d


def github_repo(url: str | None) -> tuple[str, str | None] | None:
    """(owner login, repo or None) when a URL points into GitHub, else None.

    github.com/<owner>/<repo> gives both. <owner>.github.io gives the owner
    (GitHub Pages hosts are the account login, lowercased) and, for project
    pages, the repo from the first path segment.
    """
    if not url:
        return None
    try:
        p = urlparse(url if "://" in url else "http://" + url)
    except ValueError:
        return None
    host = (p.netloc or "").lower().split("@")[-1].split(":")[0].removeprefix("www.")
    parts = [s for s in (p.path or "").split("/") if s]
    if host == "github.com":
        if not parts or parts[0].lower() in _GITHUB_RESERVED:
            return None
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", parts[0]):
            return None
        repo = parts[1].removesuffix(".git") if len(parts) > 1 else None
        return parts[0], repo
    if host.endswith(".github.io") and host.count(".") == 2:
        owner = host.split(".")[0]
        repo = parts[0] if parts and "." not in parts[0] else None
        return owner, repo
    return None


def parse_title(title: str) -> dict[str, Any]:
    """Split an HN title into its parts.

    Returns {"kind": "launch_hn" | "show_hn" | None, "rest": title without the
    prefix, "name": product or company name or None, "tagline": text after the
    name or None, "yc_batch": "S26" or None, "split": True when the name was
    set off from a description by a dash, colon or "(YC ...)"}.
    """
    # Algolia titles are plain text today; unescape anyway so a stray "&amp;"
    # can never end up inside a company name.
    raw = " ".join(html.unescape(title or "").split())
    m = _PREFIX.match(raw)
    kind = None
    rest = raw
    if m:
        kind = "launch_hn" if m.group(1).lower() == "launch" else "show_hn"
        rest = raw[m.end():].strip()
    rest = _TRAILING_TAG.sub("", rest).strip()
    out: dict[str, Any] = {"kind": kind, "rest": rest, "name": None, "tagline": None,
                           "yc_batch": None, "split": False}

    yc = _YC_BATCH.search(rest)
    if yc:
        out["yc_batch"] = yc.group(1).upper()

    if kind == "launch_hn" and yc:
        # "Name (YC S26) – tagline" and the rarer "Name (YC S26) does something".
        name = rest[: yc.start()].strip(" -–—:")
        tail = rest[yc.end():].strip()
        tail = re.sub(r"^[–—:-]+\s*", "", tail).strip()
        if name:
            out["name"] = name
            out["tagline"] = tail or None
            out["split"] = True
            return out

    sep = _SEP.search(rest)
    if sep and sep.start() > 0:
        head, tail = rest[: sep.start()].strip(), rest[sep.end():].strip()
    else:
        head, tail = rest, ""
    head = _YC_BATCH.sub("", head).strip()
    head = _VERSION_TAIL.sub("", head).strip()
    words = head.split()
    looks_like_name = (
        bool(words)
        and len(words) <= (4 if tail else 3)
        and len(head) <= 40
        and words[0].lower().strip(",.") not in _NOT_A_NAME
        and not head.endswith("?")
        and any(ch.isalpha() for ch in head)
        # "Atlas Motion" is a name; "FPGA design acceleration" is a description.
        and (len(words) == 1 or all(w[0].isupper() or w[0].isdigit() for w in words))
    )
    if looks_like_name:
        out["name"] = head
        out["tagline"] = tail or None
        out["split"] = bool(tail)
    return out


def _first_paragraph_link(story_html: str | None) -> str | None:
    """First link in the opening paragraph of a post (where Launch HN names its site)."""
    if not story_html:
        return None
    first = re.split(r"(?i)<p>", story_html, maxsplit=1)[0]
    m = re.search(r'<a\s+href="([^"]+)"', first)
    return html.unescape(m.group(1)) if m else None


def entity_for(hit: dict, parsed: dict) -> EntityHint | None:
    """Build the entity hint from what the post itself states. None if it names nothing."""
    url = (hit.get("url") or "").strip() or None
    is_launch = parsed["kind"] == "launch_hn"
    author = (hit.get("author") or "").strip()
    item_url = ITEM_PAGE.format(id=hit["objectID"])

    gh = github_repo(url)
    domain = own_domain(url)
    lead_link = _first_paragraph_link(hit.get("story_text")) if is_launch else None
    if is_launch:
        # A Launch HN names the company site in its opening line; the story
        # URL is often a repo or a docs page.
        domain = domain or own_domain(lead_link)
        gh = gh or github_repo(lead_link)

    links = {"hn": item_url}
    if url and re.match(r"(?i)https?://(?:www\.)?github\.com/", url) and github_repo(url):
        links["repo"] = url
    elif url and (own_domain(url) or github_repo(url)):
        # Only a root URL is "the website"; a deep link is the page that was shown.
        links["website" if own_domain(url) and urlparse(url).path in ("", "/") else "page"] = url
    if lead_link and own_domain(lead_link) and "website" not in links:
        links["website"] = lead_link

    name = parsed["name"]
    if is_launch:
        kind = "company"
    elif gh:
        kind = "project"
    elif name and domain:
        kind = "company"
    else:
        kind = "project"

    if gh and gh[1] and not (name and parsed["split"]):
        # A short unsplit title ("Web-Based FPGA Viewer") is a description;
        # the repository's own name is the better handle. A repository can
        # be named with a trailing dash; that reads as a cut-off name.
        repo = gh[1].rstrip("-_.") or gh[1]
        name = repo if len(repo) >= 4 else f"{gh[0]}/{repo}"
    # Whatever part of the title is not the name describes the thing.
    one_liner = parsed["tagline"] or (None if name == parsed["rest"] else parsed["rest"])
    # A repository owner that is not the submitter's own handle is another
    # name for whoever is behind the thing, usually an organisation. It lets
    # a product's Show HN ("InstinctFlash", github General-Instinct) resolve
    # to the company's own Launch HN ("General Instinct").
    aliases = []
    if gh and gh[0].lower() != (name or "").lower() and not _same_handle(author, gh[0]):
        aliases.append(gh[0])
    if not name and domain:
        name = domain
    if not name:
        # Nothing names a product or a site. If the post links to something
        # (a video, a write-up on a shared platform) the builder is the
        # entity; a text-only post with no product name is a discussion.
        if not author or not url:
            return None
        name, kind = author, "person"
        links["hn_user"] = USER_PAGE.format(name=author)

    return EntityHint(
        name=name,
        kind=kind,
        domain=domain,
        github=gh[0] if gh else None,
        aliases=aliases,
        one_liner=one_liner,
        links=links,
    )


_FIRST_PERSON = re.compile(
    r"(?<![\w'’])(?:i|we|my|our|i['’](?:m|ve|d|ll)|we['’](?:re|ve|d|ll))(?![\w'’])", re.I)


def _squash_handle(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _handle_words(s: str) -> frozenset[str]:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s)
    return frozenset(w for w in re.split(r"[^a-z0-9]+", s.lower()) if w)


def _same_handle(a: str, b: str) -> bool:
    """Two account or site names that are plainly one name: "MCH170" and
    "mch170.com", "georgia_bucea" and "BuceaGeorgia"."""
    sa, sb = _squash_handle(a), _squash_handle(b)
    if len(sa) >= 4 and len(sb) >= 4 and (sa in sb or sb in sa):
        return True
    wa, wb = _handle_words(a), _handle_words(b)
    return len(wa) >= 2 and wa == wb


def maker_evidence(hit: dict, parsed: dict, story: str) -> str | None:
    """Why the submitter can be taken as the maker, or None when nothing says so.

    "launch": a Launch HN is posted by a founder. "handle": the HN username
    matches the repository owner, the site or a name in the URL. "first
    person": the title or the post text says I, we, my or our.
    """
    if parsed["kind"] == "launch_hn":
        return "launch"
    author = (hit.get("author") or "").strip()
    url = (hit.get("url") or "").strip()
    if author and url:
        gh = github_repo(url)
        if gh and _same_handle(author, gh[0]):
            return "handle"
        try:
            p = urlparse(url if "://" in url else "http://" + url)
        except ValueError:
            p = None
        if p is not None:
            host = (p.netloc or "").lower().split("@")[-1].split(":")[0].removeprefix("www.")
            own = own_domain(url)
            # On the builder's own site, the name before the TLD; on a shared
            # platform, only the account part ("lukeiseman" in
            # lukeiseman.substack.com), never the platform's own name.
            names = [own.rsplit(".", 1)[0]] if own else host.split(".")[:-2]
            if any(_same_handle(author, n) for n in names if n):
                return "handle"
            first = next((seg for seg in (p.path or "").split("/") if seg), "")
            if len(_squash_handle(first)) >= 4 and _squash_handle(first) == _squash_handle(author):
                return "handle"  # huggingface.co/<account>/..., <platform>/<account>/...
    if _FIRST_PERSON.search(parsed["rest"]) or _FIRST_PERSON.search(story):
        return "first person"
    return None


# ---------------------------------------------------------------------------
# Strength and wording
# ---------------------------------------------------------------------------

def strength_for(points: int, comments: int, is_launch: bool,
                 prior_stories: int | None, account_age_days: int | None) -> float:
    """0..1 read of one post. See the calibration block at the top of the module."""
    s = BASE_FLOOR + BASE_SPAN * squash(points + 0.5 * comments, ENGAGEMENT_HALF)
    if is_launch:
        s = max(s + LAUNCH_BONUS, LAUNCH_FLOOR)
    if prior_stories == 0 and points >= FIRST_STORY_MIN_POINTS:
        # A first-ever submission that lands is a formation signal.
        s += FIRST_STORY_BONUS
        if account_age_days is not None and account_age_days <= FRESH_ACCOUNT_DAYS:
            s += FRESH_ACCOUNT_BONUS
    return round(min(s, STRENGTH_CAP), 3)


def _plural(n: int, word: str) -> str:
    return f"{n:,} {word}" if n == 1 else f"{n:,} {word}s"


def title_for(kind: str, points: int, comments: int, yc_batch: str | None,
              first_story: bool) -> str:
    label = "Launch HN" if kind == "launch_hn" else "Show HN"
    if kind == "launch_hn" and yc_batch:
        label += f" (YC {yc_batch})"
    t = f"{label} reached {_plural(points, 'point')} and {_plural(comments, 'comment')}"
    if first_story and points >= FIRST_STORY_MIN_POINTS:
        t += ", the author's first HN story"
    return t


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def _since_ts(ctx: Context) -> int:
    return calendar.timegm(ctx.since.timetuple())


def _walk(ctx: Context) -> Iterable[dict]:
    """Every Show HN / Launch HN story since ctx.since, newest first.

    Algolia returns at most 1,000 hits per query, so page by timestamp.
    """
    since = _since_ts(ctx)
    cursor: int | None = None
    seen: set[str] = set()
    for _ in range(MAX_PAGES):
        nf = f"created_at_i>={since}" + (f",created_at_i<={cursor}" if cursor is not None else "")
        data = http.get_json(SEARCH_URL, ttl=LIST_TTL, params={
            "tags": "(show_hn,launch_hn)",
            "numericFilters": nf,
            "hitsPerPage": PAGE_SIZE,
            "attributesToRetrieve": _FIELDS,
            "attributesToHighlight": "[]",
        })
        hits = [h for h in (data.get("hits") or []) if isinstance(h, dict)]
        fresh = 0
        for h in hits:
            if not h.get("objectID") or h["objectID"] in seen:
                continue
            seen.add(h["objectID"])
            fresh += 1
            yield h
        if not fresh:
            return
        # A short page is the end of the window, unless Algolia says more
        # hits matched than it returned (a lowered page cap must not be
        # mistaken for the end; MAX_PAGES then stops the walk with a warning).
        total = data.get("nbHits")
        if len(hits) < PAGE_SIZE and not (isinstance(total, int) and total > len(hits)):
            return
        stamps = [int(h["created_at_i"]) for h in hits if h.get("created_at_i")]
        if not stamps:
            return
        cursor = min(stamps)
    ctx.warn(f"hn_launch: stopped after {MAX_PAGES} pages, window not fully read")


_TRACKING_PARAM = re.compile(r"(?:utm_[a-z]+|ref|ref_src|source|fbclid|gclid)=")


def _post_key(hit: dict, parsed: dict) -> str:
    """Reposts of one thing share a key: the URL, or author + title when there is none."""
    url = (hit.get("url") or "").strip().lower()
    if url:
        url = re.sub(r"^https?://(www\.)?", "", url).split("#")[0]
        base, _, query = url.partition("?")
        # Keep the query: youtube.com/watch?v=A and ?v=B are different posts.
        # Only campaign tags are noise.
        kept = sorted(q for q in query.split("&") if q and not _TRACKING_PARAM.match(q))
        return "u:" + base.rstrip("/") + ("?" + "&".join(kept) if kept else "")
    return f"t:{(hit.get('author') or '').lower()}:{parsed['rest'].lower()}"


def _author_facts(ctx: Context, author: str, before_ts: int) -> dict[str, Any]:
    """Karma, account age and earlier-story count for an HN user. Missing keys mean unknown."""
    out: dict[str, Any] = {}
    if not author:
        return out
    try:
        u = http.get_json(USER_URL.format(name=quote(author, safe="")), ttl=USER_TTL)
        if isinstance(u, dict):
            if isinstance(u.get("karma"), int):
                out["karma"] = u["karma"]
            if isinstance(u.get("created"), int):
                out["created"] = u["created"]
            about = strip_html(u.get("about"))
            if about:
                out["about"] = " ".join(about.split())[:BIO_CHARS]
    except Exception as e:  # noqa: BLE001 - one author must not lose the post
        ctx.warn(f"hn_launch: user {author}: {type(e).__name__}: {e}")
    try:
        prior = http.get_json(SEARCH_URL, ttl=HISTORY_TTL, params={
            "tags": f"story,author_{author}",
            "numericFilters": f"created_at_i<{before_ts}",
            "hitsPerPage": 0,
            "attributesToHighlight": "[]",
        })
        if isinstance(prior.get("nbHits"), int):
            out["prior_stories"] = prior["nbHits"]
    except Exception as e:  # noqa: BLE001
        ctx.warn(f"hn_launch: story history for {author}: {type(e).__name__}: {e}")
    return out


def candidate(hit: dict) -> dict[str, Any] | None:
    """Parse and gate one Algolia hit. None when it is not a keepable on-thesis post."""
    parsed = parse_title(hit.get("title") or "")
    tags = hit.get("_tags") or []
    if "launch_hn" in tags:
        parsed["kind"] = "launch_hn"
    elif parsed["kind"] is None and "show_hn" in tags:
        parsed["kind"] = "show_hn"
    if parsed["kind"] is None or not parsed["rest"]:
        return None
    story = strip_html(hit.get("story_text"))
    gate = thesis_gate(parsed["rest"], story, parsed["kind"] == "launch_hn")
    if not gate["ok"]:
        return None
    return {"hit": hit, "parsed": parsed, "story": story, "gate": gate}


def _description(story: str) -> str | None:
    """First paragraph of the post that says something ("Hi HN," does not)."""
    for para in story.split("\n\n"):
        para = " ".join(para.split())
        if len(para) >= 60:
            return para if len(para) <= 400 else para[:400].rsplit(" ", 1)[0] + "…"
    return None


def build_signal(cand: dict[str, Any], facts: dict[str, Any], reposts: int = 1) -> Signal | None:
    """Turn a gated post plus author facts into a Signal. Pure: no network.

    `reposts` is how many times this URL was posted in the window; only the
    highest-scoring post is emitted.
    """
    hit, parsed, story = cand["hit"], cand["parsed"], cand["story"]
    created = int(hit["created_at_i"])
    when = datetime.fromtimestamp(created, tz=timezone.utc)
    entity = entity_for(hit, parsed)
    if entity is None:
        return None
    if hit.get("points") is None or hit.get("num_comments") is None:
        # The title states both numbers. Algolia has them for every story in
        # the window today; if one is ever missing, say nothing rather than 0.
        return None
    points = int(hit["points"])
    comments = int(hit["num_comments"])
    is_launch = parsed["kind"] == "launch_hn"
    author = (hit.get("author") or "").strip()

    prior = facts.get("prior_stories")
    maker = maker_evidence(hit, parsed, story)
    if maker is None and isinstance(prior, int) and prior >= SERIAL_SUBMITTER_STORIES:
        # A prolific account sharing someone else's repo under "Show HN". It
        # says nothing about the builder and must not be credited to them.
        return None
    age_days = None
    if isinstance(facts.get("created"), int):
        age_days = max((created - facts["created"]) // 86400, 0)

    metrics: dict[str, Any] = {
        "hn_points": points,
        "hn_comments": comments,
        "hn_comment_ratio": round(comments / max(points, 1), 2),
        "hn_thesis_fit": cand["gate"]["fit"],
    }
    if reposts > 1:
        metrics["hn_posts_same_url"] = reposts
    if "karma" in facts:
        metrics["author_karma"] = facts["karma"]
    if age_days is not None:
        metrics["author_account_age_days"] = age_days
    if prior is not None:
        metrics["author_prior_stories"] = prior

    people = []
    if author and maker:
        # Only a submitter the post itself ties to the thing is listed as a
        # person on the entity; the author metrics above are kept either way.
        pfacts: dict[str, Any] = {}
        if "karma" in facts:
            pfacts["hn_karma"] = facts["karma"]
        if age_days is not None:
            pfacts["hn_account_age_days"] = age_days
        if prior is not None:
            pfacts["hn_prior_stories"] = prior
        if is_launch and facts.get("about"):
            # The scorer reads `bio` for pedigree. Only a Launch HN poster is
            # known to belong to the company; a Show HN submitter may not.
            pfacts["bio"] = facts["about"]
        people.append(Person(name=author, role="Launch HN author" if is_launch else "Show HN author",
                             links={"hn": USER_PAGE.format(name=author)}, facts=pfacts))

    entity.description = _description(story)

    return Signal(
        source=SLUG,
        family=FAMILY,
        kind=parsed["kind"],
        entity=entity,
        title=title_for(parsed["kind"], points, comments, parsed["yc_batch"], prior == 0),
        occurred_at=when.strftime("%Y-%m-%d"),
        url=ITEM_PAGE.format(id=hit["objectID"]),
        value=points,
        unit="points",
        strength=strength_for(points, comments, is_launch, prior, age_days),
        metrics=metrics,
        people=people,
        text=(parsed["rest"] + "\n\n" + story).strip()[:TEXT_CHARS],
    )


def collect(ctx: Context) -> Iterable[Signal]:
    since = _since_ts(ctx)
    # occurred_at is the UTC date, as HN shows it, and ctx.today is the UTC
    # run date. A story dated after ctx.today (the run was pinned to an
    # earlier day, or midnight UTC passed mid-run) is outside the window and
    # is left for the run that contains it.
    until = calendar.timegm(ctx.today.timetuple()) + 86400
    best: dict[str, dict[str, Any]] = {}
    counts: dict[str, int] = {}
    read = 0
    try:
        for hit in _walk(ctx):
            read += 1
            try:
                if not since <= int(hit.get("created_at_i") or 0) < until:
                    continue
                cand = candidate(hit)
                if cand is None:
                    continue
                key = _post_key(hit, cand["parsed"])
                counts[key] = counts.get(key, 0) + 1
                cur = best.get(key)
                if cur is None or (hit.get("points") or 0) > (cur["hit"].get("points") or 0):
                    best[key] = cand
            except Exception as e:  # noqa: BLE001 - one bad row must not lose the run
                ctx.warn(f"hn_launch: story {hit.get('objectID')}: {type(e).__name__}: {e}")
            if ctx.limit and len(best) >= ctx.limit:
                break
    except Exception as e:  # noqa: BLE001 - keep what was read before the failure
        ctx.warn(f"hn_launch: listing failed after {read} stories: {type(e).__name__}: {e}")
    ctx.log(f"hn_launch: read {read} stories, {len(best)} on thesis")

    for key, cand in best.items():
        hit = cand["hit"]
        try:
            facts = _author_facts(ctx, (hit.get("author") or "").strip(), int(hit["created_at_i"]))
            sig = build_signal(cand, facts, counts.get(key, 1))
            if sig is not None:
                yield sig
        except Exception as e:  # noqa: BLE001
            ctx.warn(f"hn_launch: story {hit.get('objectID')}: {type(e).__name__}: {e}")
