"""Company posts in Hacker News' monthly "Ask HN: Who is hiring?" threads, on thesis.

The thread is where a team of two to twenty people recruits before it has a
careers page or an applicant-tracking system, so a first post there is often
the earliest public, dated evidence that a hardware company exists and is
growing. Every top-level comment in the threads inside the lookback is read
from the public Algolia index, the conventional "Company | Role | Location"
first line is parsed, and a post is kept when the company's own description
is about robotics, autonomy, defense, energy, manufacturing, chips or space.

What is checked before a post is called a first. Three histories are read
for every kept post: every comment its author has ever made, and exact-phrase
searches of all HN comments for the company's name and for its domain. An
earlier top-level comment in a hiring thread counts as a prior post when its
own site is the company's domain, its header names the company, it opens with
the company's name, or it is by the same author, contains the name and
carries no other company's header. A name match that something contradicts
(the same name over a different site by a different poster, or the name with
another capitalised word after it: "Cable Labs" is not "Cable") is doubt: it
is never counted, but it withholds the claim. "First" is claimed only when
all three histories were read to the end and no earlier top-level hiring
comment mentions the name at all. When the name is too common for the phrase
search to be exhaustive (more than 1,000 hits: "Cable", "etc."), the 24
previous monthly threads are scanned header by header instead and the claim
is worded "in at least 24 months". A count of earlier threads that may be
incomplete is worded "at least N" and gives no first date.

Dates. occurred_at is the UTC date of the comment, which is what the HN page
shows. The run date is the UTC date too, so the two agree; when a run is given
an earlier date on purpose (a replay), comments dated after it are left out
rather than emitted with a date in that run's future.

Why the thesis filter is stricter than classify() alone. A hiring post lists
customers, industries served and desired backgrounds, so one thesis word is
weak evidence: in the five threads to 2026-10-02, 143 of 1,186 posts clear
fit >= 0.45 and four in ten of those fail the rules below, most of them
software companies with "defense" in a customer list or "robotics" in a wish
list. A post is kept only when

  1. the fit is >= 0.45 on the full text, with links removed and software
     idioms ("software factory", "on autopilot", "sensor fusion", "data
     enrichment", "Navy Yard") blanked, and the shared classify() gives at
     least 0.3 on the text the signal carries. The fit here uses the shared
     vocabulary, term patterns, weights, false-friend phrases and lone-term
     rule; it is computed in this module only because the rules below need
     to know where each term sits, a longer term consumes the words inside
     it ("missile defense" is one term, not three), and a few terms mean
     something else in a hiring post ("ISR" is a way of rendering web pages,
     "energy" is a mood unless the post has a strong energy term);
  2. the evidence adds up to two units: a strong term is one unit, repeats
     add half, context terms add half up to one, a term that appears only
     as one item in a comma-separated list of three or more counts half,
     and a strong term in the sentence where the company describes itself
     adds half; and
  3. the header or the first 600 characters carry a strong term (or two
     context terms backed by a strong term elsewhere).

That keeps 84 of the 1,186 and trades recall for precision on purpose. Posts
that name no company ("Robotics startup (stealth)", or a job title where the
name should be), organisations that are not companies (universities,
institutes, laboratories, government and military units, by name or by their
own description) and household-name incumbents (Boeing, SpaceX, Apple) are
skipped.

Entity fields come only from the post: the name is the first header field as
written (a three-letter short form gives way to the full name in the same
field), the domain is the registrable domain of a linked or written host that
matches the name (else none; the host the post uses most when two match), and
the person is the poster's HN username with the role they state for
themselves at this company, if any. A job-board link is never the domain; its
board and slug go into metrics as `ats_provider` / `ats_slug` for the ats_jobs
collector. Within one run a company keeps one domain across its posts.
"""

from __future__ import annotations

import calendar
import html
import math
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from .. import http
from ..config import THESIS, THESIS_FALSE_FRIENDS, THESIS_UNAMBIGUOUS
from ..models import EntityHint, Person, Signal
from ..thesis import CONTEXT_W, LONE_TERM_FIT, STRONG_W, classify, term_pattern
from .base import Context, clean_domain

SLUG = "hn_hiring"
FAMILY = "hiring"
STAGE = "discover"
DESCRIPTION = "On-thesis company posts in the monthly Ask HN: Who is hiring? threads, with first-post detection"

SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"
ITEM_PAGE = "https://news.ycombinator.com/item?id={id}"
USER_PAGE = "https://news.ycombinator.com/user?id={name}"

THREAD_AUTHOR = "whoishiring"
THREAD_TITLE = re.compile(r"^\s*Ask HN:\s*Who is hiring\?", re.I)
HIRING_STORY = re.compile(r"who\s+is\s+hiring", re.I)

PAGE_SIZE = 1000  # Algolia's hard cap per query
THREAD_OPEN_DAYS = 14  # HN locks a thread to new comments after about two weeks
HISTORY_THREADS = 24  # previous monthly threads scanned when a name is too common to search
AUTHOR_PAGES = 3  # 3,000 comments of author history; beyond that the history is marked incomplete
LIVE_TTL = 3600  # the current thread grows by the hour
CLOSED_TTL = 30 * 24 * 3600  # a locked thread does not change
HISTORY_TTL = 12 * 3600
MIN_FIT = 0.45
SHARED_FLOOR = 0.3  # contract rule 7, on the shared classifier
MIN_UNITS = 2.0
LEAD_CHARS = 600  # how much of the body counts as the company describing itself
TEXT_CHARS = 4000
TITLE_MAX = 108
ROLE_FIELD_MAX = 110  # a longer "role" field is a paragraph, not a role

_COMMENT_FIELDS = "author,comment_text,created_at,created_at_i,objectID,parent_id,story_id,story_title"

# ---------------------------------------------------------------------------
# Strength. Calibrated 2026-10-02 on the 68 signals the five threads June to
# October 2026 produce: 40 land in 0.15-0.30, 18 in 0.30-0.50, 3 in 0.50-0.60,
# 6 in 0.60-0.80 (the top decile) and 1 above 0.85.
#   routine  0.15-0.30  a company that posts most months, posting again; any
#                       company that says it is large or late-stage, or whose
#                       first hiring post is more than five years old
#   solid    0.35-0.55  a second or third post (never higher, however small
#                       the team), or a first post that says nothing about
#                       how young or small the company is
#   notable  0.60-0.80  a first-ever post by a team that says it is early
#   rare     0.85+      a first-ever post by a team of a handful, pre-seed
# ---------------------------------------------------------------------------
BASE = 0.15
FIRST_EVER = 0.36
FIRST_RECENT = 0.20  # no earlier post in 24 months, all-time history not verifiable
EARLY_REPEAT = 0.12  # second or third thread
TINY_CAP = 0.30
FIT_SPAN = 0.08
HARDWARE_ROLE = 0.04
ESTABLISHED_CAP = 0.25
REPEAT_CAP = 0.55  # a post that is not a first stays out of the notable band
STRENGTH_CAP = 0.97


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------

_A_TAG = re.compile(r"<a\s[^>]*?href=\"([^\"]*)\"[^>]*>.*?</a>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_EMAIL = re.compile(r"\b[\w.+-]+@((?:[\w-]+\.)+[a-z]{2,})\b", re.I)
_TLDS = (
    "com|co|io|ai|dev|org|net|tech|app|inc|energy|aero|space|bot|xyz|us|uk|de|so|sh|systems|company|"
    "health|run|ca|eu|fr|nl|se|ch|in|me|gg|vc|earth|bio|build|works|work|cloud|studio|industries|"
    "engineering|one|team|science|technology|tools|software|solutions|design|digital|network|au|jp|"
    "es|it|pl|dk|no|fi|ie|at|be|nz|sg|br|mx|il|ae|land|global|world|group|ventures|capital|finance|eco"
)
_BARE_DOMAIN = re.compile(
    rf"(?<![\w@/.-])((?:[a-z0-9][a-z0-9-]*\.)+(?:{_TLDS}))(?![\w-])(/[^\s)\],;|]*)?", re.I
)
_URL = re.compile(r"https?://[^\s<>\"')\]|]+", re.I)


def links_in(comment_html: str | None) -> list[str]:
    """hrefs of every anchor in a comment, in order, unescaped."""
    return [html.unescape(m.group(1)).strip() for m in _A_TAG.finditer(comment_html or "")]


def render(comment_html: str | None, links: str = "drop") -> str:
    """HN comment HTML as plain text. Paragraphs are separated by a blank line.

    links="drop" removes anchors entirely (their text is the URL, and a host
    such as factory.ai would otherwise read as a thesis word); links="href"
    replaces each anchor with its full target, for header parsing.
    """
    t = comment_html or ""
    if links == "href":
        t = _A_TAG.sub(lambda m: " " + html.unescape(m.group(1)) + " ", t)
    else:
        t = _A_TAG.sub(" ", t)
    t = re.sub(r"<\s*/?\s*p\s*>|<\s*br\s*/?\s*>", "\n\n", t, flags=re.I)
    t = _TAG.sub("", t)
    t = html.unescape(t).replace("\r", "\n").replace("\xa0", " ")
    t = re.sub(r"[ \t]+", " ", t)
    t = "\n".join(line.strip() for line in t.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def prose(comment_html: str | None) -> str:
    """The comment as text with every link, written URL, host and e-mail removed."""
    t = render(comment_html, "drop")
    t = _URL.sub(" ", t)
    t = _EMAIL.sub(" ", t)
    t = _BARE_DOMAIN.sub(_keep_dotted_tech, t)
    t = re.sub(r"\(\s*\)|\[\s*\]", " ", t)  # what a removed link leaves behind
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r" ([.,;:!?])", r"\1", t)
    return re.sub(r" ?\n ?", "\n", t).strip()


_NOT_A_HOST = {"asp.net", "vb.net", "ado.net", "ml.net", "node.io", "socket.io", "e.g.co"}


def _keep_dotted_tech(m: re.Match[str]) -> str:
    return m.group(0) if m.group(1).lower() in _NOT_A_HOST else " "


def _key(s: str | None) -> str:
    """Lowercase letters and digits only: the form names and hosts are compared in."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


# ---------------------------------------------------------------------------
# Thesis: positioned term matching, idioms, enumerations, evidence
# ---------------------------------------------------------------------------

# Terms that the shared list carries but that mislead in a hiring post.
_SKIP_TERMS = {
    "cam",
    "isr",  # incremental static regeneration, in every Next.js stack line
    "rideshare",
    "chip",  # "chip in", "blue chip"
    "energy",  # "a high energy team". Counted only beside a strong energy term: see term_hits
    "usv",  # Union Square Ventures
    "contested", "tactical", "swarm", "swarming",
    "dod",  # kept as context below only in upper case
    "enrichment",  # handled separately: thesis only next to nuclear words
}

# Spellings and synonyms of one thing count as one piece of evidence. (Plurals need no entry: the shared
# term pattern accepts them, "factories" and "foundries" included.)
_STEMS = {
    "robotic": "robot", "uas": "uav", "suas": "uav", "unmanned aerial": "uav", "quadrotor": "quadcopter",
    "defence": "defense", "department of defense": "defense", "department of war": "defense",
    "electric grid": "power grid", "critical metal": "critical mineral",
    "tape-out": "tapeout", "systemverilog": "verilog", "circuit board": "pcb", "printed circuit": "pcb",
    "ros 2": "ros2", "teleop": "teleoperation", "sim2real": "sim-to-real",
    "autonomy": "autonomous", "counter-uas": "c-uas", "cuas": "c-uas", "counter unmanned": "c-uas",
    "physical intelligence": "physical ai", "embodied intelligence": "embodied ai",
    "vertical take-off": "vtol",
}


def _build_terms() -> list[tuple[re.Pattern[str], str, str, str, float]]:
    """[(pattern, term, stem, sector, weight)], longest term first.

    The shared vocabulary, patterns and weights. What this module adds is
    where each hit sits (for the enumeration and lead rules) and that a
    longer term consumes the words inside it.
    """
    out = []
    for sector, groups in THESIS.items():
        for group, weight in (("strong", STRONG_W), ("context", CONTEXT_W)):
            for term in groups.get(group, []):
                if term in _SKIP_TERMS:
                    continue
                out.append((term_pattern(term), term, _STEMS.get(term, term), sector, weight))
    # "DoD" only in that casing: lower-case "dod" is "definition of done" in half the stack lines.
    out.append((re.compile(r"(?<![A-Za-z0-9])DoD(?![A-Za-z0-9])"), "dod", "dod", "defense", CONTEXT_W))
    out.sort(key=lambda r: -len(r[1]))
    return out


_TERMS = _build_terms()
_EVERY_TERM = [term_pattern(t) for groups in THESIS.values() for terms in groups.values() for t in terms]

# The phrases the shared classifier blanks before matching ("sensor fusion", "army of", "fusion 360").
_FALSE_FRIENDS = re.compile("|".join(term_pattern(p).pattern for p in THESIS_FALSE_FRIENDS), re.I)

# Software and workplace idioms that borrow a thesis word, beyond the shared list. Blanked before matching.
_IDIOMS = re.compile(
    r"""
      \b(?:software|code|coding|ai|agent|agentic|content|ticket|feature|dark|token|prompt|data|model|
           startup|company|venture|verticali[sz]ed|idea)[\s-]+factor(?:y|ies)\b
    | \bfactor(?:y|ies)[\s-]+(?:for|of)[\s-]+(?:building[\s-]+)?(?:software|claims|agents?|code|content|apps?)
      (?:[\s-]+factor(?:y|ies))?\b
    | \bfactory[\s-]+(?:pattern|method|function|reset|settings)\b
    | \bon[\s-]+autopilot\b | \bautopilot[\s-]+for\b
    | \bself[\s-]driving\b(?!\s+(?:cars?|vehicles?|trucks?|taxis?|tractors?|fleets?|tech\w*|industry|stack|software|systems?))
    | \b(?:llm|rank|score)[\s-]+fusion\b
    | \b(?:cyber|fraud|bot|legal|ddos|threat|malware|criminal|tower|self|prompt[\s-]+injection)[\s-]*defen[cs]e\b
    | \bdefen[cs]e[\s-]+in[\s-]+depth\b | \bdefen[cs]e\s+(?:attorneys?|lawyers?|counsel)\b
    | \bswiss[\s-]+army\b | \bsalvation\s+army\b
    | \bnavy\s+yard\b | \bold\s+navy\b | \bnavy\s+(?:blue|federal)\b
    | \bsecret\s+weapons?\b | \bweapon\s+of\s+choice\b
    | \bmilitary[\s-]grade\b
    | \b(?:software|open[\s-]source|package|dependency|npm)\s+supply\s+chain\b | \bsupply\s+chain\s+(?:security|attacks?)\b
    | \b(?:data|dom|string|image|bit|market|text|array)\s+manipulation\b
    | \brocket[\s-]?ships?\b | \brocket[\s-]+fuel\b
    | \b(?:css|data|ag|layout)\s+grid\b | \bgrid\s+(?:layout|view|system)\b
    | \b(?:high|real|more|full|total|significant|lots\s+of|a\s+lot\s+of|deep|complete|strong|genuine|true|
           your|much|enormous|tons\s+of|degree\s+of|level\s+of|values?|with|and)\s+autonomy\b
      (?=\s+(?:and|&|over|to|in|on|for|not)\b|\s*[.,;:!)]|\s*$)
    | \bautonomy\s+(?:and|&)\s+(?:ownership|transparency|trust|responsibility|accountability|freedom|flexibility|impact|mastery)\b
    | \bautonomy\s+over\b
    | \bautonomous(?:ly)?\s+(?:agents?|ai|coding|software\s+(?:engineer|develop)\w*|development|underwriting|workflows?|
           quote|teams?|squads?|organi[sz]ations?|compan(?:y|ies)|enterprises?|business(?:es)?|testing|qa|pentest\w*|
           security|research|models?|finance|accounting|sales|support|decision\w*|engineers?|individuals?|people)\b
    | (?:[$€£]\s?)?\d[\d.,]*\s?[kKmM]?\+?\s+CAD\b | \bCAD\s?\$
    | \bsolid[\s-]state\s+(?:drives?|storage|disks?)\b
    | \b(?:under|on|off|onto|to)\s+(?:the|your|our|my|their)\s+radar\b
      (?!\s+(?:systems?|sensors?|hardware|firmware|software|products?|platform|units?|arrays?|data|signals?|stack)\b)
    | \bin\s+spaces\b
    | \bgood\s+manufacturing\s+practices?\b
    | \b(?:palantir|ai|azure|cloud|model)\s+foundry\b
    | \bsatellite\s+(?:offices?|campus(?:es)?|teams?)\b
    | \bsolar\s+system\b
    | \bplasma\s+(?:desktop|donation|tv)\b | \bkde\s+plasma\b
    | \breactor\s+(?:pattern|netty|core)\b | \bproject\s+reactor\b
    | \b(?:brand|sales|product|growth)\s+constellation\b
    | \b(?:type|down|up)[\s-]?casting\b | \bcasting\s+(?:a|an|the|call|director|vote)\b
    | \bforging\s+(?:ahead|a|an|new|partnerships?|relationships?)\b
    | \bhigh[\s-]+energy\s+(?:teams?|people|person|folks|individuals?|engineers?|environment|culture|start-?up|pace)\b
    | \b(?:positive|good|great|infectious|contagious)\s+energy\b
    """,
    re.I | re.X,
)
_NUCLEAR_CONTEXT = re.compile(r"\b(?:uranium|nuclear|isotop\w+|haleu|centrifuges?|fuel\s+cycle)\b", re.I)
_ENRICHMENT = re.compile(r"\benrichment\b", re.I)
_ENERGY = term_pattern("energy")


def blank_idioms(text: str, own_name: str | None = None) -> str:
    """Replace idioms, and a company name that is itself a thesis word, with spaces.

    "Factory", "Factory AI" and "Radar Labs" as proper nouns say nothing about
    the business; "Charge Robotics" does and is left alone. Lengths are
    preserved so match offsets still index the original text.
    """
    out = _FALSE_FRIENDS.sub(lambda m: " " * len(m.group(0)), text)
    out = _IDIOMS.sub(lambda m: " " * len(m.group(0)), out)
    tokens = (own_name or "").split()
    if tokens and _is_thesis_word(tokens[0]) and all(t.lower().strip(".,") in _GENERIC_NAME_TOKENS for t in tokens[1:]):
        pat = re.compile(rf"(?<![A-Za-z0-9]){re.escape(tokens[0])}(?:'s|’s)?(?![A-Za-z0-9])", re.I)
        out = pat.sub(lambda m: " " * len(m.group(0)) if m.group(0)[:1].isupper() else m.group(0), out)
    return out


def _is_thesis_word(word: str) -> bool:
    """Is the word, on its own, a thesis term (in the singular or the plural)?"""
    w = word.strip()
    return any(pat.fullmatch(w) for pat in _EVERY_TERM)


def term_hits(text: str) -> list[dict[str, Any]]:
    """Every thesis term occurrence in text, longest term first, no overlapping spans."""
    taken: list[tuple[int, int]] = []
    hits: list[dict[str, Any]] = []
    nuclear = bool(_NUCLEAR_CONTEXT.search(text))
    for pat, term, stem, sector, weight in _TERMS:
        for m in pat.finditer(text):
            if any(m.start() < e and s < m.end() for s, e in taken):
                continue
            taken.append((m.start(), m.end()))
            hits.append({"term": term, "stem": stem, "sector": sector, "weight": weight,
                         "start": m.start(), "end": m.end()})
    if nuclear:
        for m in _ENRICHMENT.finditer(text):
            hits.append({"term": "enrichment", "stem": "enrichment", "sector": "energy", "weight": STRONG_W,
                         "start": m.start(), "end": m.end()})
    # "energy" alone is as often a mood as a sector ("a high energy team"). In a post that already has a
    # strong energy term (a battery, a reactor, a solar farm) it is the context term the shared list makes
    # it: "energy specialists", "wholesale energy markets".
    if any(h["sector"] == "energy" and h["weight"] >= STRONG_W for h in hits):
        for m in _ENERGY.finditer(text):
            if any(m.start() < e and s < m.end() for s, e in taken):
                continue  # inside "energy storage" or "directed energy"
            hits.append({"term": "energy", "stem": "energy", "sector": "energy", "weight": CONTEXT_W,
                         "start": m.start(), "end": m.end()})
    hits.sort(key=lambda h: h["start"])
    return hits


def corrected_fit(hits: list[dict[str, Any]]) -> dict[str, Any]:
    """thesis.classify's arithmetic over this module's term hits (distinct terms, once each).

    The shared rule for a lone term applies here too: one matched term that
    is not unmistakably physical stays under the shared 0.3 gate.
    """
    scores: dict[str, float] = {}
    seen: set[str] = set()
    for h in hits:
        if h["term"] in seen:
            continue
        seen.add(h["term"])
        scores[h["sector"]] = scores.get(h["sector"], 0.0) + h["weight"]
    if not scores:
        return {"sector": "other", "fit": 0.0, "terms": []}
    best = max(scores, key=scores.get)  # type: ignore[arg-type]
    mass = scores[best] + 0.35 * sum(v for k, v in scores.items() if k != best)
    fit = 1.0 - math.exp(-mass / 1.6)
    if len(seen) == 1 and not seen & THESIS_UNAMBIGUOUS:
        fit = min(fit, LONE_TERM_FIT)
    return {"sector": best, "fit": round(fit, 3), "terms": sorted(seen)}


_CLAUSE_BREAK = re.compile(r"[.!?](?:\s|$)|[\n|;:()\[\]]")
_OWN_PRODUCTS = re.compile(
    r"^\W*(?:(?:hi|hello|hey)\b[^,.!]{0,12}[,.!]\s*)?(?:we|we(?:'|’)re|we\s+are|[A-Z][\w&.'’-]*(?:\s+[A-Z][\w&.'’-]*){0,3})\s+"
    r"(?:also\s+|now\s+|currently\s+)?(?:(?:is|are)\s+)?(?:build|make|design|develop|manufactur|fus|creat|deploy|operat|produc)\w*\b",
)
_LEAD_IN = re.compile(r"^\s*(?:and|or|&)\s+", re.I)
# Between "we build" and the first comma: the list that follows names markets or examples, not products.
_LIST_OF_OTHERS = re.compile(r"\b(?:for|like|such\s+as|including|across|in|to|with|from)\b", re.I)


def _clause(text: str, start: int, end: int) -> tuple[int, int]:
    lo = 0
    for m in _CLAUSE_BREAK.finditer(text, 0, start):
        lo = m.end()
    m = _CLAUSE_BREAK.search(text, end)
    return lo, (m.start() if m else len(text))


def is_enumerated(text: str, start: int, end: int) -> bool:
    """True when the span is one item of a comma-separated list of three or more.

    "customers in defense, intelligence, security and critical infrastructure"
    and "(consumer, wearable, medical or robotics)" name the thesis in passing.
    "We make robots that lay bricks" is not a list, and "We build drones,
    radios and ground stations" is a list of the company's own products, so
    neither counts as enumerated. "We build simulators for robotics, science
    and storytelling" and "we speed up permitting for projects like power
    plants, transmission lines and data centers" list markets and examples:
    the verb's object came before the list, so those are enumerated.
    """
    lo, hi = _clause(text, start, end)
    clause = text[lo:hi]
    if clause.count(",") < 2:
        return False
    own = _OWN_PRODUCTS.match(clause)
    if own and not _LIST_OF_OTHERS.search(clause[own.end():clause.index(",")]):
        return False
    pos = lo
    items: list[tuple[int, int]] = []
    for piece in clause.split(","):
        items.append((pos, pos + len(piece)))
        pos += len(piece) + 1
    for i, (a, b) in enumerate(items):
        if not (a <= start < b + 1):
            continue
        item = text[a:b]
        closes = len(item[end - a:].split()) <= 1
        if i == 0:
            # The first item carries the lead-in ("customers are in defense"): the term must close it.
            return closes
        head = _LEAD_IN.sub("", item)
        offset = len(item) - len(head)
        if len(item[offset:start - a].split()) <= 2:
            return True
        # A second list in the same clause ("the networks, relationships and ties behind companies to
        # support national security, compliance and oversight"): this item carries its lead-in.
        return closes and len(items) - i >= 3
    return False


_SELF = re.compile(r"\b(?:we|we(?:'|’)re|we(?:'|’)ve|our)\b", re.I)
_NOT_SELF = re.compile(
    r"\b(?:customers?|clients?|serv(?:e|es|ing)|industries|sectors|markets|such\s+as|including|experience|"
    r"background|worked|previously|formerly|prev\.?|backed\s+by|funded\s+by|investors?|looking\s+for|hiring|"
    r"you(?:'|’)ll|you\s+will|you\s+have|ideal|candidates?)\b", re.I)


def is_self_description(text: str, start: int, name: str | None) -> bool:
    """Is the term inside a sentence where the company says what it is or does?"""
    lo = max(text.rfind("\n", 0, start), text.rfind(". ", 0, start), text.rfind("! ", 0, start)) + 1
    before = text[lo:start]
    if _NOT_SELF.search(before):
        return False
    if _SELF.search(before):
        return True
    first = (name or "").split()[0] if name else ""
    return bool(first) and len(first) >= 3 and first.lower() in before.lower()


def evidence(header_roles: str, name: str | None, body: str) -> dict[str, Any]:
    """Weigh the thesis evidence in one post.

    Returns units (see module docstring), whether the header or the opening
    of the post carries it, the corrected fit and sector, and the terms.
    """
    multiword_name = name if name and len(name.split()) > 1 else ""
    core_text = " | ".join(filter(None, [multiword_name, header_roles]))
    lead = body[:LEAD_CHARS]
    full = core_text + "\n\n" + body
    blanked = blank_idioms(full, name)
    hits = term_hits(blanked)
    core_end = len(core_text)
    lead_end = core_end + 2 + len(lead)

    strong: dict[str, dict[str, Any]] = {}
    context: dict[str, dict[str, Any]] = {}
    for h in hits:
        in_header = h["end"] <= core_end
        listed = False if in_header else is_enumerated(blanked, h["start"], h["end"])
        bucket = strong if h["weight"] >= STRONG_W else context
        cur = bucket.setdefault(h["stem"], {"plain": 0, "listed": 0, "core_plain": 0, "core_listed": 0})
        cur["listed" if listed else "plain"] += 1
        if h["start"] < lead_end:
            cur["core_listed" if listed else "core_plain"] += 1

    # One strong term in the sentence where the company describes itself is worth more than a mention.
    described = any(
        h["weight"] >= STRONG_W and core_end < h["start"] < lead_end
        and not is_enumerated(blanked, h["start"], h["end"])
        and is_self_description(blanked, h["start"], name)
        for h in hits
    )
    units = 0.5 if described else 0.0
    extra = 0.0
    core_strong = 0.0
    for cur in strong.values():
        if cur["plain"]:
            # Used outside a list at least once: every further mention is a repeat, in a list or not.
            units += 1.0
            extra += 0.5 * (cur["plain"] + cur["listed"] - 1)
        else:
            units += 0.5
        if cur["core_plain"]:
            core_strong += 1.0
        elif cur["core_listed"]:
            core_strong += 0.5
    units += min(extra, 1.0)
    ctx_units = sum(0.5 if cur["plain"] else 0.25 for cur in context.values())
    units += min(ctx_units, 1.0)
    core_context = sum(1 for cur in context.values() if cur["core_plain"])
    any_plain_strong = any(cur["plain"] for cur in strong.values())
    core_ok = core_strong >= 1.0 or (core_context >= 2 and any_plain_strong)

    fit = corrected_fit(hits)
    return {
        "units": round(units, 2),
        "core_ok": core_ok,
        "fit": fit["fit"],
        "sector": fit["sector"],
        "terms": fit["terms"],
        "strong_stems": sorted(strong),
    }


# ---------------------------------------------------------------------------
# Header parsing
# ---------------------------------------------------------------------------

_REMOTE = re.compile(
    r"\b(?:remote(?:[- ]first)?|on-?site|hybrid|in[- ]person|in[- ]office|wfh|relocat\w+|distributed|"
    r"anywhere|worldwide|global(?:ly)?|office)\b", re.I)
_WORKTYPE = re.compile(
    r"\b(?:full[- ]?time|part[- ]?time|contract(?:or)?|intern(?:ship)?s?|freelance|permanent|perm|"
    r"temporary|co-?ops?|ft|pt|fte)\b", re.I)
_SALARY = re.compile(r"[$€£¥]|\b\d{2,3}\s?k\b|\bequity\b|\bsalary\b|\bcompetitive\b|\b(?:USD|EUR|GBP|CHF)\b", re.I)
_VISA = re.compile(r"\bvisa\b|\bcitizens?(?:hip)?\b|\bclearance\b|\bsponsor\w*|\bus persons?\b|\bwork authori[sz]ation\b|\bitar\b", re.I)
_ROLE = re.compile(
    r"\b(?:engineers?|engineering|developers?|programmers?|scientists?|researchers?|designers?|managers?|"
    r"leads?|head of|founding|architects?|analysts?|technicians?|interns?|swe|sre|devops|roles?|positions?|"
    r"openings?|multiple|various|several|sales|marketing|operations|recruiters?|directors?|vp|cto|"
    r"staff|principal|senior|junior|sr|jr|full[- ]?stack|back[- ]?end|front[- ]?end|firmware|"
    r"roboticists?|writers?|consultants?|specialists?|experts?|associates?|mts|member of technical staff|"
    r"gtm|teleoperators?|operators?|machinists?|welders?|mechanics?|pilots?)\b", re.I)
_GENERIC_ROLE = re.compile(r"^\s*(?:multiple|various|several|many|all|lots of)\b|^\s*(?:engineering|hiring)\s*$", re.I)
_STAGE_FIELD = re.compile(r"^\s*(?:pre-?seed|seed|series\s+[a-h]|yc\s+[a-z]\d{2}|yc[- ]funded|vc[- ]backed|bootstrapped)\s*$", re.I)

_PLACES = (
    "san francisco|sf|bay area|sf bay|silicon valley|palo alto|menlo park|mountain view|sunnyvale|san jose|"
    "san mateo|san carlos|santa clara|redwood city|oakland|berkeley|south bay|peninsula|los angeles|la|el segundo|"
    "hawthorne|long beach|torrance|irvine|san diego|seattle|kirkland|bellevue|redmond|portland|denver|"
    "boulder|golden|austin|dallas|houston|chicago|boston|cambridge|somerville|new york|new york city|nyc|"
    "brooklyn|manhattan|washington|dc|washington dc|arlington|mclean|tysons|durham|raleigh|atlanta|miami|"
    "pittsburgh|philadelphia|detroit|ann arbor|minneapolis|salt lake city|phoenix|mesa|tucson|albuquerque|"
    "huntsville|chattanooga|nashville|charleston|tuscaloosa|dayton|columbus|toronto|ontario|vancouver|"
    "montreal|waterloo|london|cambridge uk|oxford|sheffield|bristol|manchester|edinburgh|knutsford|dublin|"
    "paris|berlin|munich|münchen|hamburg|cologne|köln|frankfurt|freiburg|bremen|stuttgart|amsterdam|utrecht|"
    "delft|eindhoven|zurich|zürich|geneva|lausanne|vienna|stockholm|copenhagen|oslo|helsinki|tallinn|"
    "warsaw|prague|madrid|barcelona|lisbon|milan|rome|tel aviv|dubai|abu dhabi|cairo|bangalore|bengaluru|"
    "noida|delhi|mumbai|hyderabad|singapore|tokyo|seoul|sydney|melbourne|auckland|sao paulo|mexico city|"
    "usa|us|u\\.s\\.|u\\.s\\.a\\.|united states|america|americas|north america|canada|uk|u\\.k\\.|"
    "united kingdom|england|scotland|ireland|europe|eu|emea|apac|asia|germany|france|netherlands|"
    "the netherlands|switzerland|sweden|denmark|norway|finland|estonia|poland|spain|portugal|italy|"
    "austria|belgium|israel|uae|egypt|india|japan|south korea|korea|australia|new zealand|brazil|mexico|"
    "latam|earth|"
    "california|texas|colorado|massachusetts|virginia|maryland|florida|georgia|arizona|new mexico|ohio|"
    "michigan|illinois|tennessee|alabama|north carolina|south carolina|oregon|utah|pennsylvania|new jersey|"
    "ca|tx|co|ma|va|md|fl|ga|az|nm|oh|mi|il|tn|al|nc|sc|or|ut|pa|nj|ny|wa|mn|ct"
)
_PLACE = re.compile(rf"(?<![A-Za-z])(?:{_PLACES})(?![A-Za-z])", re.I)
_CITY_STATE = re.compile(r"\b[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){0,2},\s*[A-Z]{2}\b")
_LOCATION_FILLER = re.compile(
    r"\b(?:or|and|in|only|preferred|based|area|metro|greater|time ?zones?|overlap|days?|per|week|wk|a|the|"
    r"of|to|from|with|w|est|pst|cst|et|pt|cet|gmt|utc|hq|downtown|north|south|east|west|city|county|"
    r"anywhere|flexible|required|optional|some|most|roles?|role-dependent|\d+)\b", re.I)


def is_pure_location(field: str) -> bool:
    """True when a header field is nothing but places and remote/on-site words."""
    if not (_PLACE.search(field) or _REMOTE.search(field) or _CITY_STATE.search(field)):
        return False
    rest = _CITY_STATE.sub(" ", field)
    rest = _PLACE.sub(" ", rest)
    rest = _REMOTE.sub(" ", rest)
    rest = _LOCATION_FILLER.sub(" ", rest)
    return len(re.sub(r"[^A-Za-z]", "", rest)) <= 2


def has_place(field: str) -> bool:
    return bool(_CITY_STATE.search(field) or _PLACE.search(_REMOTE.sub(" ", field)))


_REGION_ONLY = re.compile(
    r"\b(?:us|usa|u\.s\.a?\.?|united states|america|americas|north america|canada|uk|eu|europe|emea|apac|asia|"
    r"latam|earth|worldwide|global|or|and|only)\b", re.I)
_ONSITE = re.compile(r"\b(?:on-?site|hybrid|in[- ]person|in[- ]office|office)\b", re.I)
_REMOTE_WORD = re.compile(r"\bremote\b", re.I)


def clean_location(field: str | None) -> str | None:
    """Where the company works from, as the header gives it, without the remote/on-site wording.

    "ONSITE San Francisco, CA" gives "San Francisco, CA"; "Remote (US)" gives
    None, because a remote-only field says where candidates may be, not
    where the company is.
    """
    if not field:
        return None
    s = field.strip()
    if _REMOTE_WORD.search(s) and not _ONSITE.search(s):
        if not re.search(r"\bor\b|/|\+|,", _REMOTE_WORD.sub("", re.sub(r"\([^)]*\)", "", s))):
            return None
    s = re.sub(r"\b(?:US|USA|EU|UK|Europe|global|worldwide)\s+remote\b", " ", s, flags=re.I)
    s = re.sub(r"\b(?:fully\s+)?remote(?:[- ]first)?\s*(?:\([^)]*\)|(?:US|USA|EU|UK|EMEA|APAC|Europe|worldwide|global)\b)?", " ", s, flags=re.I)
    s = _ONSITE.sub(" ", s)
    s = re.sub(r"\b\d+\s*(?:-\s*\d+\s*)?days?(?:\s*(?:/|per|a)\s*(?:week|wk))?\b", " ", s, flags=re.I)
    s = re.sub(r"\((?:(?!\)).)*\)", lambda m: m.group(0) if has_place(m.group(0)) else " ", s)
    s = re.sub(r"\s+", " ", s)
    for _ in range(4):
        s = re.sub(r"^(?:[\s,;:/+&|–—-]+|(?:in|or|and|only|preferred|flexible)\b)+", "", s, flags=re.I).strip()
        s = re.sub(r"(?:[\s,;:/+&|(–—-]+|\b(?:in|or|and|only|preferred|flexible))+$", "", s, flags=re.I).strip()
    whole = re.fullmatch(r"\(([^()]*)\)", s)
    if whole:
        s = whole.group(1).strip()
    s = re.sub(r"\s+([,)])", r"\1", s)
    if not s or len(s) > 80 or not has_place(s) or s.count("(") != s.count(")"):
        return None
    # Nothing but places, or "Town, Country" with a town this module has no list entry for.
    town_country = re.fullmatch(rf"[^,;/|()]{{2,40}},\s*(?:the\s+)?(?:{_PLACES})", s, re.I)
    if not (is_pure_location(s) or town_country):
        return None
    if not re.sub(r"[^A-Za-z]", "", _REGION_ONLY.sub(" ", s)):
        return None  # "US", "EU / APAC": a hiring region, not a place
    return s


_YC = re.compile(r"\(?\bYC\s+([WSFXP]\d{2})\b\)?", re.I)
_PAREN_TAIL = re.compile(r"\s*\(([^()]*)\)\s*$")
_NOT_NAME = re.compile(
    r"^(?:hiring|we|we're|we are|i|i'm|hi|hello|hey|location|remote|seeking|looking|join|about|role|"
    r"position|company|stealth|confidential|undisclosed)\b|\bis hiring\b|\bstealth\b|\blooking for\b|"
    r"\bwe are\b|\bwe're\b|\bstart-?up\b|\bwanted\b|^\W*$", re.I)  # "Drone startup | ..." names nobody
# A first field that is a job title ("Software Engineer -- Infrastructure | Oslo | Onsite") is a post whose
# company is named only in the body. The title must never become the entity.
_JOB_TITLE = re.compile(
    r"\b(?:engineers?|developers?|programmers?|scientists?|designers?|managers?|architects?|analysts?|"
    r"technicians?|interns?|recruiters?|roboticists?|swe|sre)\b", re.I)
_NOT_COMPANY = re.compile(
    r"\b(?:universit(?:y|ies|ät)|college|school of|institute|laborator(?:y|ies)|national lab|"
    r"policy institute|foundation|non-?profit|government|agency|ministry|department of|"
    r"(?:research|engineering|technology|innovation)\s+cent(?:er|re)|air\s+force|police\s+department|"
    r"sheriff(?:'s|’s)?\s+(?:office|department)|city\s+of|digital\s+service)\b", re.I)
# The post says of itself that it is an institute, a public body or part of the armed forces:
# "CESMII is the United States' Manufacturing USA Institute", "a civilian software engineering organization
# operating under the United States Air Force", "a team of technologists hired into the Department of Defense".
# The subject must be the company ("we are", "<name> is"), and the head noun must follow without a
# preposition in between, so that "our first customer is the Allen Institute" and "a spin-out from the
# Max Planck Institute" stay companies.
_INSTITUTION_NOUN = (
    r"\s+(?:a|an|the)\s+(?:(?!(?:of|from|at|by|with|out|in|for|to)\b)[\w'’&-]+(?:\.[\w'’&-]+)*\s+){0,7}?"
    r"(?:institute|university|non-?profit|government\s+agency|federal\s+agency|public\s+agency)\b"
    r"(?![\s-]+(?:spin|start-?up|backed|affiliated|founded|born))")
_PUBLIC_BODY = re.compile(
    r"\boperating\s+under\s+the\s+(?:united\s+states|u\.?s\.?)\s+(?:air\s+force|army|navy|government|department)"
    r"|\bhired\s+into\s+the\s+department\s+of\b", re.I)


def says_institution(text: str, name: str) -> bool:
    """Does the opening of the post describe its author as an institute, a non-profit or a public body?"""
    lead = text[:LEAD_CHARS + 200]
    if _PUBLIC_BODY.search(lead):
        return True
    first = re.escape(name.split()[0]) if name.split() else r"\0"
    subject = rf"(?:\bwe\s+are|\bwe(?:'|’)re|(?<![\w-]){first}(?![\w-])[^.\n|]{{0,40}}?\s+is)"
    return bool(re.search(subject + _INSTITUTION_NOUN, lead, re.I))


# Household-name incumbents. A sourcing list that "discovers" Boeing is noise, whatever the post says.
_INCUMBENT = re.compile(
    r"^(?:the\s+)?(?:apple|amazon|google|alphabet|microsoft|nvidia|intel|qualcomm|samsung|ibm|oracle|cisco|tesla|"
    r"spacex|starlink|blue\s+origin|boeing|airbus|lockheed(?:\s+martin)?|northrop(?:\s+grumman)?|raytheon|rtx|"
    r"general\s+dynamics|general\s+motors|general\s+electric|bae\s+systems|l3harris|honeywell|siemens|bosch|"
    r"toyota|ford|volkswagen|bmw|jaguar)\b", re.I)
_DASH_SEP = re.compile(r"\s+[—–-]{1,2}\s+")


def split_header(text_with_links: str) -> tuple[str, list[str]]:
    """First line of the post and its fields. Fields are empty when it follows no convention."""
    first_para = text_with_links.split("\n\n", 1)[0]
    line = first_para.split("\n", 1)[0].strip()
    if "|" in line:
        fields = [f.strip() for f in re.split(r"\s*[|｜]\s*", line)]
    elif len(_DASH_SEP.findall(line)) >= 2 and len(line) <= 240:
        fields = [f.strip() for f in _DASH_SEP.split(line)]
    else:
        return line, []
    return line, [f for f in fields if f]


def clean_company_field(field: str) -> dict[str, Any]:
    """Split "etc. (Exploration Technology Corp.)" style fields into name, alias and YC batch."""
    out: dict[str, Any] = {"name": None, "alias": None, "yc_batch": None}
    s = field.strip().strip("*_ ").strip()
    s = _URL.sub(" ", s)
    m = _YC.search(s)
    if m:
        out["yc_batch"] = m.group(1).upper()
        s = (s[:m.start()] + " " + s[m.end():])
    # Peel trailing parentheticals: aliases, bare hosts, stage notes.
    for _ in range(3):
        pm = _PAREN_TAIL.search(s)
        if not pm:
            break
        inner = pm.group(1).strip(" ,;")
        s = s[:pm.start()]
        if not inner:
            continue
        if _BARE_DOMAIN.fullmatch(inner):
            continue  # "(withcargo.com)": a host, picked up with the other links
        if re.search(r"\bseries\s+[a-h]\b|\bseed\b|non-?profit|\byc\b|stealth|remote|hybrid|on-?site", inner, re.I):
            continue  # "(Series B)", "(Remote US)": a note, not a second name
        if out["alias"] is None and 2 <= len(inner) <= 60:
            out["alias"] = inner
    s = re.sub(r"\(\s*\)", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" ,;:-–—*_")
    out["name"] = s or None
    return out


def valid_name(name: str | None) -> bool:
    if not name:
        return False
    n = name.strip()
    if not (2 <= len(n) <= 60) or len(n.split()) > 6:
        return False
    if not re.search(r"[A-Za-z0-9]", n) or _NOT_NAME.search(n):
        return False
    if n.endswith((".", "!", "?")) and len(n.split()) > 2:
        return False
    return True


def parse_header(comment_html: str | None) -> dict[str, Any]:
    """Company, role, location and stage from the conventional first line.

    `name` is None when the first line follows no convention this parser
    trusts; the caller may still recover a name that a link corroborates.
    """
    text = render(comment_html, "href")
    line, fields = split_header(text)
    out: dict[str, Any] = {
        "line": line, "fields": fields, "name": None, "alias": None, "yc_batch": None,
        "role": None, "location": None, "name_field_index": None,
    }
    labelled = re.match(r"^\s*(?:company|employer)\s*:\s*(.{2,80})$", line, re.I)
    if not fields and labelled:
        # The other convention: "COMPANY: Tangram Vision" then "TYPE:", "DESCRIPTION:" on their own lines.
        first = clean_company_field(labelled.group(1))
        if valid_name(first["name"]):
            out.update(name=first["name"], alias=first["alias"], yc_batch=first["yc_batch"], name_field_index=0)
            out["fields"] = [labelled.group(1).strip()]
        return out
    if not fields:
        return out

    def kind(f: str) -> str:
        bare = _URL.sub(" ", f).strip(" -:")
        if not bare or _BARE_DOMAIN.fullmatch(bare):
            return "url"
        if _STAGE_FIELD.match(bare):
            return "stage"
        if is_pure_location(bare):
            return "location"
        # A role wins over place words: "Robotics Engineer, Austin" is a role field.
        if _ROLE.search(bare) and not _SALARY.search(bare.split(",")[0]):
            return "role" if len(bare) <= ROLE_FIELD_MAX else "other"
        if _SALARY.search(bare):
            return "salary"
        if _VISA.search(bare):
            return "visa"
        if _WORKTYPE.search(bare) or _REMOTE.search(bare):
            return "terms"
        if has_place(bare):
            return "location"
        return "other"

    kinds = [kind(f) for f in fields]
    first = clean_company_field(fields[0])
    idx = 0
    # The first field is judged by the name inside it, not by a "(Remote US)" beside it.
    if first["name"]:
        kinds[0] = kind(first["name"])
    # "Puma.tech | ..." and "Spore.Bio | ...": a name styled as its own host, with no scheme or path.
    styled = re.fullmatch(r"[A-Za-z0-9][\w-]*\.[A-Za-z]{2,10}", fields[0].strip())
    if styled and kinds[0] == "url":
        kinds[0] = "other"
    if first["name"] and _JOB_TITLE.search(first["name"]) and not styled:
        return out
    if not valid_name(first["name"]) or kinds[0] in {"location", "url", "salary", "terms", "visa", "stage"}:
        # "Cologne, Germany | UMH | Product Engineer": a place leads, the company follows.
        if len(fields) > 1 and kinds[0] == "location" and kinds[1] == "other":
            second = clean_company_field(fields[1])
            if valid_name(second["name"]):
                first, idx = second, 1
            else:
                return out
        else:
            return out
    out.update(name=first["name"], alias=first["alias"], yc_batch=first["yc_batch"], name_field_index=idx)
    for i, (f, k) in enumerate(zip(fields, kinds)):
        if i == idx:
            continue
        bare = re.sub(r"\s+", " ", _URL.sub(" ", f)).strip(" -:")
        if k == "role" and out["role"] is None:
            out["role"] = re.sub(r"^(?:roles?|positions?|hiring)\s*:\s*", "", bare, flags=re.I).strip("[] ")
        elif k in ("location", "terms") and out["location"] is None:
            out["location"] = clean_location(bare)
        if out["yc_batch"] is None:
            m = _YC.search(f)
            if m:
                out["yc_batch"] = m.group(1).upper()
    return out


# ---------------------------------------------------------------------------
# Links: company domain and job-board slug
# ---------------------------------------------------------------------------

# Never a company's own site, beyond what clean_domain already rejects (code hosts, social sites, the
# common job boards and applicant-tracking systems, .gov, .mil and .edu): the other boards and trackers
# hiring posts link, form and video hosts, the press, mail providers and link shorteners.
_NOT_COMPANY_HOSTS = (
    "myworkdayjobs.com", "jobvite.com", "icims.com", "workday.com", "paylocity.com", "adp.com",
    "indeed.com", "glassdoor.com", "otta.com", "welcometothejungle.com", "join.com", "homerun.co",
    "polymer.co", "trinethire.com", "zohorecruit.com", "freshteam.com", "comeet.com", "hiring.cafe",
    "remoteok.com", "weworkremotely.com", "hnhiring.com", "getro.com", "pallet.com", "kula.ai",
    "wistia.com", "tally.so", "forms.gle", "cal.com", "docsend.com", "canva.com", "techcrunch.com",
    "bloomberg.com", "forbes.com", "wsj.com", "nytimes.com", "reuters.com", "businesswire.com",
    "prnewswire.com", "venturebeat.com", "wired.com", "theverge.com", "axios.com", "fortune.com",
    "cnbc.com", "pitchbook.com", "sifted.eu", "theinformation.com", "businessinsider.com", "ft.com",
    "spacenews.com", "defensenews.com", "breakingdefense.com", "ieee.org", "nature.com", "science.org",
    "hotmail.com", "icloud.com", "protonmail.com", "pm.me", "hey.com", "fastmail.com", "googlemail.com",
    "goo.gl", "g.co", "share.google", "maps.app.goo.gl", "tinyurl.com", "lnkd.in", "ted.com", "spotify.com",
)
_TWO_LEVEL_SUFFIX = {"co.uk", "com.au", "co.nz", "co.jp", "com.br", "co.in", "com.mx", "co.il", "com.sg", "org.uk", "ac.uk"}
# Words in a company name that are too generic to tie a host to it.
_GENERIC_NAME_TOKENS = {
    "the", "and", "inc", "llc", "ltd", "gmbh", "corp", "corporation", "company", "labs", "lab",
    "technologies", "technology", "systems", "group", "studio", "energy", "space", "robotics", "defense",
    "aero", "power", "tech", "data", "cloud", "health", "bio", "ai", "design", "research", "computing",
    "industries", "dynamics", "motors", "works", "software", "security", "science", "sciences", "global",
    "solutions", "partners", "capital", "digital", "network", "networks", "medical", "analytics",
}


def company_host(url_or_host: str | None) -> str | None:
    """A company's own registrable-looking host, or None for boards, forms, press and mail hosts."""
    host = clean_domain(url_or_host)
    if not host:
        return None
    for bad in _NOT_COMPANY_HOSTS:
        if host == bad or host.endswith("." + bad):
            return None
    if host.endswith((".careers", ".jobs")):
        return None  # flix.careers is a careers microsite, not the domain the company is known by
    labels = host.split(".")
    # The registrable domain: jobs.cable.energy and people.blackshark.ai are cable.energy and blackshark.ai.
    # The resolver keys on the domain exactly, so a subdomain would split one company in two.
    keep = 3 if _two_level(labels) else 2
    return ".".join(labels[-keep:])


def _two_level(labels: list[str]) -> bool:
    """Does the host end in a two-level public suffix (co.uk, com.au, co.za)?"""
    if len(labels) < 3:
        return False
    tail = ".".join(labels[-2:])
    return tail in _TWO_LEVEL_SUFFIX or (len(labels[-1]) == 2 and labels[-2] in {"co", "com", "org", "net", "ac", "or", "ne"})


def _host_label(host: str) -> str:
    """The label that carries the brand: "cable" in jobs.cable.energy, "foo" in foo.co.uk."""
    labels = host.split(".")
    if _two_level(labels):
        return labels[-3]
    return labels[-2] if len(labels) >= 2 else labels[0]


# A host with one of these in it is a job board or a careers microsite unless the company's own name
# carries the word: nordictechjobs.com is not Nordic Semiconductor's site.
_BOARD_WORD = re.compile(r"jobs|careers?|talent|hiring|recruit|staffing")


def name_matches_host(name: str, host: str, alias: str | None = None) -> bool:
    """Does this host plausibly belong to a company of this name?

    The brand label of the host must be the name, contain the name or one of
    its distinctive words, or be the word the name starts with. A generic
    word ("labs", "space", "robotics") never ties a host to a name.
    """
    label = _key(_host_label(host))
    whole = _key(host)
    if len(label) < 2:
        return False
    if _BOARD_WORD.search(label) and not any(_BOARD_WORD.search(_key(c)) for c in (name, alias) if c):
        return False
    for candidate in filter(None, [name, alias]):
        n = _key(candidate)
        if not n:
            continue
        if n == label or n == whole:
            return True
        if len(n) >= 4 and n in label:
            return True
        tokens = [t for t in re.split(r"[^a-z0-9]+", candidate.lower()) if t]
        if label in _GENERIC_NAME_TOKENS:
            continue
        if len(label) >= 4 and n.startswith(label):
            return True  # "Brightcore Energy" at brightcore.com
        if any(len(t) >= 4 and t not in _GENERIC_NAME_TOKENS and t in label for t in tokens):
            return True  # "Cargo Robotics" at withcargo.com, "Lumen Labs" at lumenresearch.co
        if tokens and len(tokens[0]) >= 2 and label == tokens[0]:
            return True  # "UMH Systems" at umh.app
    return False


def host_mentions(comment_html: str | None) -> dict[str, int]:
    """Company-looking hosts and how often the post mentions each, in order of first appearance.

    Links, written hosts and e-mail addresses all count as a mention.
    """
    out: dict[str, int] = {}
    text = render(comment_html, "href")
    found: list[tuple[int, str]] = []
    for m in _URL.finditer(text):
        found.append((m.start(), m.group(0)))
    stripped = _URL.sub(lambda m: " " * len(m.group(0)), text)
    for m in _EMAIL.finditer(stripped):
        found.append((m.start(), m.group(1)))
    stripped = _EMAIL.sub(lambda m: " " * len(m.group(0)), stripped)
    for m in _BARE_DOMAIN.finditer(stripped):
        if m.group(1).lower() not in _NOT_A_HOST:
            found.append((m.start(), m.group(1)))
    for _, raw in sorted(found):
        host = company_host(raw)
        if host:
            out[host] = out.get(host, 0) + 1
    return out


def host_candidates(comment_html: str | None) -> list[str]:
    """Company-looking hosts from links, written hosts and e-mail addresses, in order of appearance."""
    return list(host_mentions(comment_html))


def pick_domain(name: str, alias: str | None, comment_html: str | None) -> str | None:
    """The host in the post that matches the company's name, else None.

    A link that does not match the name may be a customer, an investor, a
    press piece or an unfamiliar job board, so it is never used. When two
    hosts match (a post that links lumenresearch.ai once but puts its paper
    and its e-mail address on lumenresearch.co), the one the post uses most
    wins and the earlier one breaks a tie, so that two posts by one company
    do not come out under two domains.
    """
    counts = host_mentions(comment_html)
    matching = [h for h in counts if name_matches_host(name, h, alias)]
    if not matching:
        return None
    most = max(counts[h] for h in matching)
    return next(h for h in matching if counts[h] == most)


# Capture group 1 is the slug. Patterns from the source card, section 3.
_ATS_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("greenhouse", re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board(?:/js)?\?for=)?([A-Za-z0-9_-]+)", re.I)),
    ("greenhouse", re.compile(r"boards-api\.greenhouse\.io/v1/boards/([A-Za-z0-9_-]+)", re.I)),
    ("lever", re.compile(r"jobs(?:\.eu)?\.lever\.co/([A-Za-z0-9_.-]+)", re.I)),
    ("lever", re.compile(r"api(?:\.eu)?\.lever\.co/v0/postings/([A-Za-z0-9_.-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)", re.I)),
    ("ashby", re.compile(r"api\.ashbyhq\.com/posting-api/job-board/([A-Za-z0-9_.%-]+)", re.I)),
    ("workable", re.compile(r"apply\.workable\.com/([A-Za-z0-9_-]+)", re.I)),
    ("workable", re.compile(r"https?://([A-Za-z0-9-]+)\.workable\.com", re.I)),
    ("rippling", re.compile(r"ats\.rippling\.com/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)/jobs", re.I)),
    ("gem", re.compile(r"jobs\.gem\.com/([A-Za-z0-9_-]+)", re.I)),
    ("dover", re.compile(r"app\.dover\.com/(?:jobs/|apply/)?([A-Za-z0-9_-]+)", re.I)),
    ("breezy", re.compile(r"https?://([A-Za-z0-9-]+)\.breezy\.hr", re.I)),
    ("bamboohr", re.compile(r"https?://([A-Za-z0-9-]+)\.bamboohr\.com", re.I)),
    ("jazzhr", re.compile(r"https?://([A-Za-z0-9-]+)\.applytojob\.com", re.I)),
    ("recruitee", re.compile(r"https?://([A-Za-z0-9-]+)\.recruitee\.com", re.I)),
    ("teamtailor", re.compile(r"https?://([A-Za-z0-9-]+)(?:\.[a-z]{2})?\.teamtailor\.com", re.I)),
    ("pinpoint", re.compile(r"https?://([A-Za-z0-9-]+)\.pinpointhq\.com", re.I)),
    ("personio", re.compile(r"https?://([A-Za-z0-9-]+)\.jobs\.personio\.(?:de|com)", re.I)),
]
_ATS_STOP = {"embed", "js", "api", "www", "apply", "jobs", "job_board", "v1", "assets", "static", "cdn",
             "app", "careers", "job", "j", "o", "p"}
_ATS_CASE_SENSITIVE = {"lever"}
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def ats_refs(urls: list[str]) -> list[tuple[str, str]]:
    """Distinct (provider, slug) pairs among the URLs, in order."""
    out: list[tuple[str, str]] = []
    for url in urls:
        for provider, pat in _ATS_PATTERNS:
            m = pat.search(url)
            if not m:
                continue
            slug = m.group(1).strip(".")
            if not slug or slug.lower() in _ATS_STOP or _UUID.fullmatch(slug):
                continue  # a posting id, not the board's slug
            if provider not in _ATS_CASE_SENSITIVE:
                slug = slug.lower()
            if (provider, slug) not in out:
                out.append((provider, slug))
            break
    return out


def pick_ats(name: str, alias: str | None, urls: list[str]) -> tuple[str, str] | None:
    """The post's own job board. With several boards in one post, only one that matches the name."""
    refs = ats_refs(urls)
    if not refs:
        return None
    if len(refs) == 1:
        return refs[0]
    names = [_key(n) for n in (name, alias) if n]
    for provider, slug in refs:
        s = _key(slug)
        if s and any(n and (s == n or (len(s) >= 4 and (s in n or n in s))) for n in names):
            return provider, slug
    return None


# ---------------------------------------------------------------------------
# Cues about team size and stage, stated in the post
# ---------------------------------------------------------------------------

_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
              "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
              "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20}
_NUM = r"(\d{1,3}(?:,\d{3})+|\d{1,5}|" + "|".join(_NUM_WORDS) + r")"
_APPROX = r"(?:about\s+|around\s+|roughly\s+|approximately\s+|approx\.?\s+|just\s+|only\s+|~\s*|c\.\s*)?"
_TEAM_PATTERNS = [
    re.compile(rf"\bteam\s+of\s+{_APPROX}{_NUM}\b(?!\s*(?:%|x\b|-\s*\d|–\s*\d|\s+to\s+\d))", re.I),
    # "300+ person engineering and research team" is a department, so nothing may sit between.
    re.compile(rf"\b{_NUM}\+?[- ]person\s+(?:team|company|startup)\b", re.I),
    re.compile(rf"\b(?:we(?:'|’)re|we\s+are)\s+(?:currently\s+|now\s+)?{_APPROX}{_NUM}\s+(?:people|employees|folks|of\s+us)\b", re.I),
    re.compile(rf"\b{_APPROX}{_NUM}\+?\s+employees\b", re.I),
]
_TEAM_NOT_COMPANY = re.compile(
    r"\b(?:join(?:ing)?|lead(?:ing)?|manag(?:e|ing)|mentor(?:ing)?|grow(?:ing)?|build(?:ing)?|run(?:ning)?|"
    r"hir(?:e|ing)|on|into|within|in|with|alongside)\s+(?:a|an|our|the|this)\s+(?:[\w-]+\s+){0,3}$", re.I)
_TEAM_UPPER = re.compile(rf"\b(?:fewer|less)\s+than\s+{_NUM}\s+(?:people|employees|engineers)\b|\bunder\s+{_NUM}\s+(?:people|employees)\b", re.I)
_OPEN_ROLES = re.compile(rf"\b{_NUM}\s+(?:open\s+)?(?:openings|open\s+roles|open\s+positions|roles\s+open|positions\s+open)\b", re.I)
_EMPLOYEE_NO = re.compile(
    r"\b(?:employee|hire|engineer|teammate|team\s+member)\s*(?:#\s*|no\.?\s*|number\s+)(\d{1,3})\b"
    r"|\byou(?:'|’)d\s+be\s+(?:employee\s+|hire\s+|engineer\s+|number\s+)?#\s?(\d{1,3})\b", re.I)
_FIRST_HIRE = re.compile(
    r"\b(?:our|the|as|a)\s+(?:very\s+)?first\s+(?:[A-Za-z/&-]+\s+){0,3}?(?:hires?|engineers?|employees?|scientists?|designers?|roboticists?)\b"
    r"|\bfirst\s+(?:\d+|five|ten|twenty)\s+(?:employees|hires|engineers)\b", re.I)
_FIRST_N = re.compile(rf"\bfirst\s+{_APPROX}{_NUM}\s+(?:employees|hires|engineers|people)\b", re.I)
# The post itself says the company is young. Without this a first post earns half the premium:
# a seventy-year-old machine builder can post for the first time too.
_YOUNG = re.compile(
    r"\b(?:start-?up|early[- ]stage|early\s+(?:team|engineer|employee|hire)s?|stealth|pre-?seed|"
    r"seed[- ](?:stage|round|funded|funding|backed|financing|extension)|series\s+a|"
    r"yc[- ]backed|yc[- ]funded|vc[- ]backed|venture[- ]backed|recently\s+raised|just\s+raised|we\s+raised|"
    r"spin-?out|founded\s+in\s+20(?:2[2-9])|started\s+in\s+20(?:2[2-9])|founding)\b|\bYC\s+[WSFXP]2[3-9]\b", re.I)
_FOUNDING = re.compile(
    r"\bfounding\s+(?:[A-Za-z/&.+-]+\s+){0,4}?(?:engineers?|scientists?|designers?|members?|team|hires?|"
    r"leads?|roboticists?|researchers?|developers?|staff|ae|gtm|sales|operators?|architects?)\b", re.I)
_PRE_SEED = re.compile(r"\bpre[- ]?seed\b", re.I)
_SEED = re.compile(
    r"\bseed[- ](?:stage|round|funded|funding|backed|financing|extension|startup|company)\b"
    r"|\b(?:raised|closed|announced)\b[^.\n]{0,60}?\bseed\b"
    r"|[$€£]\s?\d[\d.]*\s?(?:m|mm|million|k)\b\s+seed\b|\bseed\s+(?:from|led\s+by)\b", re.I)
_SERIES = re.compile(r"\bseries[- ]([A-H])\b", re.I)
_BOOTSTRAPPED = re.compile(r"\bbootstrapped\b", re.I)
_PUBLIC = re.compile(r"\b(?:publicly[- ]traded|public company|nyse|nasdaq|fortune\s?500|ipo'?d)\b", re.I)
_ACQUIRED = re.compile(r"\b(?:acquired\s+(?:by|\w+\s+in\s+20\d\d)|was\s+acquired|subsidiary\s+of|a\s+division\s+of)\b", re.I)
_RAISED = re.compile(
    r"(?:\b(?:raised|raising|backed\s+by|funding\s+of|funded\s+with|closed)\b[^.\n]{0,40}?([$€£])\s?(\d+(?:\.\d+)?)\s?(k|m|mm|million|b|bn|billion)\b\+?)"
    r"|(?:([$€£])\s?(\d+(?:\.\d+)?)\s?(k|m|mm|million|b|bn|billion)\b\+?\s+(?:raised|in\s+funding|in\s+total\s+funding|series\s+[a-h]|seed|pre-?seed))",
    re.I)
_ROLE_END = r"\b(?![-'’]\w)"  # not "the CEO's assistant", not "founder-friendly"
_FOUNDER_SELF = re.compile(
    r"\bI(?:'|’)?\s?a?m\s+(?:[A-Z][\w'’-]+(?:\s+[A-Z][\w'’-]+)?\s*,\s+)?(?:the\s+|a\s+|an\s+|one\s+of\s+the\s+)?"
    r"((?:co-?)?founders?(?:\s*(?:&|and|/|,)\s*(?:CEO|CTO))?|CEO|CTO|chief\s+technology\s+officer|chief\s+executive)" + _ROLE_END +
    r"|\bmy\s+name\s+is\s+[A-Z][\w'’-]+(?:\s+[A-Z][\w'’-]+)?\s*(?:,|and)\s+I(?:'|’)?\s?a?m\s+(?:the\s+|a\s+|one\s+of\s+the\s+)?"
    r"((?:co-?)?founders?|CEO|CTO)" + _ROLE_END +
    r"|\b((?:co-?)?founder|CEO|CTO)\s+here\b", re.I)
# "CTO at Acme", "co-founder of Acme": the organisation the poster names right after the role.
_ROLE_ORG = re.compile(r"\s*(?:,\s*)?(?:of|at|@)\s+((?-i:[A-Z0-9])[\w&.'’-]*(?:\s+(?-i:[A-Z0-9])[\w&.'’-]*){0,3})", re.I)
# A stated headcount for the whole company, or for one department of it, that no early company has.
_EMPLOYS = re.compile(
    rf"\b(?:we\s+|\w+\s+)?employs?\s+(?:over\s+|more\s+than\s+|about\s+|around\s+|nearly\s+|roughly\s+|~\s*)?{_NUM}\+?\s+"
    r"(?:people|employees|staff)\b", re.I)
_BIG_GROUP = re.compile(r"\b(\d{3,5})\+?[- ]person\b", re.I)
_HARDWARE_ROLE = re.compile(
    r"\bfirmware\b|\bembedded\s+(?:systems?|software|linux|engineer\w*)|\bmechanical\s+(?:design\s+)?engineer\w*|"
    r"\belectrical\s+engineer\w*|\bcontrols\s+engineer\w*|\bgnc\b|\bpcbs?\b|\bmechatronic\w*|\bhardware\s+engineer\w*|"
    r"\brf\s+(?:engineer\w*|hardware)|\bmanufacturing\s+engineer\w*|\brobotics\s+(?:software\s+)?engineer\w*|"
    r"\bavionics\b|\btechnicians?\b|\bmachinists?\b|\bwelders?\b", re.I)


def _to_int(tok: str) -> int | None:
    t = tok.lower().replace(",", "")
    if t.isdigit():
        return int(t)
    return _NUM_WORDS.get(t)


def team_cues(text: str, name: str | None = None) -> dict[str, Any]:
    """What the post itself says about how small and how early the team is.

    `name` is the company the post is for. With it, a self-stated role is
    kept only when the poster does not attach it to some other organisation
    ("I'm the founder of Acme Recruiting, hiring for Globex").
    """
    out: dict[str, Any] = {}
    sizes: list[int] = []
    for m in _EMPLOYS.finditer(text):
        n = _to_int(m.group(1))
        if n:
            sizes.append(n)  # "we employ over 130 people": the floor the post states
    groups = [int(g) for g in _BIG_GROUP.findall(text)]
    if groups:
        out["largest_group_stated"] = max(groups)  # "a 300+ person engineering team": at least that many
    for pat in _TEAM_PATTERNS:
        for m in pat.finditer(text):
            n = _to_int(m.group(1))
            if n is None or n < 1:
                continue
            before = text[max(0, m.start() - 40):m.start()]
            if _TEAM_NOT_COMPANY.search(before):
                continue  # "join a team of 5" is a sub-team, not the company
            if re.search(r"\bfirst\s*~?\s*$", before, re.I):
                continue  # "one of the first ~10 employees" is a rank, handled below
            sizes.append(n)
    if sizes:
        out["team_size"] = max(sizes)
    m = _TEAM_UPPER.search(text)
    if m:
        n = _to_int(m.group(1) or m.group(2))
        if n:
            out["team_size_upper"] = n
    m = _OPEN_ROLES.search(text)
    if m:
        n = _to_int(m.group(1))
        if n:
            out["open_roles"] = n
    m = _EMPLOYEE_NO.search(text)
    if m:
        out["employee_number"] = int(m.group(1) or m.group(2))
    if _FIRST_HIRE.search(text):
        out["first_hire"] = True
    m = _FIRST_N.search(text)
    if m:
        n = _to_int(m.group(1))
        if n:
            out["among_first_employees"] = n
    if _YOUNG.search(text):
        out["says_early"] = True
    if _FOUNDING.search(text):
        out["founding_role"] = True
    series = sorted({s.upper() for s in _SERIES.findall(text)})
    if _PRE_SEED.search(text):
        out["stage"] = "pre-seed"
    elif series:
        out["stage"] = "series " + series[-1].lower()
    elif _SEED.search(text):
        out["stage"] = "seed"
    elif _BOOTSTRAPPED.search(text):
        out["stage"] = "bootstrapped"
    amounts: list[tuple[float, str]] = []
    for m in _RAISED.finditer(text):
        sym, num, unit = m.group(1, 2, 3) if m.group(1) else m.group(4, 5, 6)
        mult = {"k": 1e3, "m": 1e6, "mm": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9}[unit.lower()]
        amounts.append((float(num) * mult, sym))
    if amounts:
        # The largest figure the post states, in the currency it was written in (not converted).
        out["funding_stated"], out["funding_currency"] = max(amounts)
    if _PUBLIC.search(text):
        out["public_company"] = True
    if _ACQUIRED.search(text):
        out["acquired"] = True
    m = _FOUNDER_SELF.search(text)
    if m:
        role = next(g for g in m.groups() if g)
        org = _ROLE_ORG.match(text, m.end())
        if not (name and org and not _same_org(name, org.group(1))):
            out["poster_role"] = re.sub(r"\s+", " ", role).strip()
    if _HARDWARE_ROLE.search(text):
        out["hardware_roles"] = True
    return out


def _same_org(name: str, org: str) -> bool:
    """Is the organisation named after a role the company the post is for? Compared on the first word."""
    a, b = _key(name.split()[0]) if name.split() else "", _key(org.split()[0]) if org.split() else ""
    return bool(a and b) and (a == b or (len(a) >= 4 and b.startswith(a)) or (len(b) >= 4 and a.startswith(b)))


ESTABLISHED_TEAM = 150
LONG_HISTORY_DAYS = 5 * 365  # first hiring post this long ago: not an early company any more


def is_established(cues: dict[str, Any]) -> bool:
    """Says of itself that it is large, late-stage, public or acquired, or has been hiring here for years."""
    if cues.get("public_company") or cues.get("acquired") or cues.get("long_history"):
        return True
    if (cues.get("team_size") or 0) >= ESTABLISHED_TEAM or (cues.get("largest_group_stated") or 0) >= ESTABLISHED_TEAM:
        return True
    if cues.get("stage") in {"series c", "series d", "series e", "series f", "series g", "series h"}:
        return True
    if (cues.get("funding_stated") or 0) >= 100e6:
        return True
    return False


# ---------------------------------------------------------------------------
# Strength and title
# ---------------------------------------------------------------------------

def strength_for(novelty: str, prior_threads: int, cues: dict[str, Any], fit: float) -> float:
    """novelty is one of first_ever, first_recent, repeat, unknown (history could not be settled)."""
    s = BASE
    tiny = 0.0
    if cues.get("founding_role"):
        tiny += 0.10
    emp = cues.get("employee_number")
    among = cues.get("among_first_employees")
    if cues.get("first_hire") or (emp is not None and emp <= 10) or (among is not None and among <= 10):
        tiny += 0.10
    size = cues.get("team_size")
    upper = cues.get("team_size_upper")
    if size is not None and size <= 10:
        tiny += 0.12
    elif (size is not None and size <= 25) or (upper is not None and upper <= 25):
        tiny += 0.06
    stage = cues.get("stage")
    if stage == "pre-seed":
        tiny += 0.10
    elif stage == "seed":
        tiny += 0.07
    elif stage == "series a":
        tiny += 0.02
    if cues.get("poster_role"):
        tiny += 0.05
    young = tiny > 0 or bool(cues.get("says_early"))
    if novelty == "first_ever":
        s += FIRST_EVER if young else FIRST_EVER / 2
    elif novelty == "first_recent":
        s += FIRST_RECENT if young else FIRST_RECENT / 2
    elif novelty == "repeat" and prior_threads <= 2:
        s += EARLY_REPEAT if young else EARLY_REPEAT / 2
    s += min(tiny, TINY_CAP)
    s += FIT_SPAN * max(0.0, min(1.0, (fit - MIN_FIT) / (1.0 - MIN_FIT)))
    if cues.get("hardware_roles"):
        s += HARDWARE_ROLE
    if novelty == "repeat" and prior_threads >= 12:
        s -= 0.03
    if novelty not in ("first_ever", "first_recent"):
        s = min(s, REPEAT_CAP)  # however small the team, the nth post is not the event the first one was
    if is_established(cues):
        s = min(s, ESTABLISHED_CAP)
    return round(max(0.05, min(s, STRENGTH_CAP)), 3)


_ORDINALS = {2: "Second", 3: "Third"}
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _month(d: date) -> str:
    return f"{_MONTHS[d.month - 1]} {d.year}"


def _role_phrase(role: str | None, budget: int) -> str | None:
    """The header's role field, whole or not at all: a cut list of roles would misstate the post."""
    if not role:
        return None
    r = re.sub(r"\s+", " ", role).strip(" .;,:-")
    if _GENERIC_ROLE.match(r):
        r = "multiple roles"
    return r if 3 <= len(r) <= budget else None


def title_for(novelty: str, prior_threads: int, first_seen: date | None, role: str | None,
              cues: dict[str, Any], complete: bool, prior_post_count: int | None = None) -> str:
    """One sentence with the number in it.

    `complete` says the count of earlier threads is exhaustive. Without it
    the count is worded "at least N" and no first date is given, because an
    earlier post may exist that the histories read did not reach.
    `prior_post_count` is the number of earlier posts behind the thread
    count; "Second" and "Third" are used only when it equals the number of
    threads, so the ordinal is true of posts as well as of months.
    """
    one_post_per_thread = prior_post_count is None or prior_post_count == prior_threads
    early = novelty == "repeat" and prior_threads in (1, 2) and complete and one_post_per_thread
    if novelty == "first_ever":
        head = "First Who is hiring post on Hacker News"
    elif novelty == "first_recent":
        head = f"First Who is hiring post on Hacker News in at least {HISTORY_THREADS} months"
    elif early:
        head = f"{_ORDINALS[prior_threads + 1]} Who is hiring post on Hacker News"
    else:
        head = "Who is hiring post on Hacker News"
    bits: list[str] = []
    plural = "s" if prior_threads != 1 else ""
    if early and first_seen:
        bits.append(f"first seen {_month(first_seen)}")
    elif novelty == "repeat" and complete:
        since = f" since {_month(first_seen)}" if first_seen else ""
        bits.append(f"{prior_threads} earlier thread{plural}{since}")
    elif novelty == "repeat":
        bits.append(f"at least {prior_threads} earlier thread{plural}")
    size = cues.get("team_size")
    emp = cues.get("employee_number")
    if size is not None and size <= 25:
        bits.append(f"team of {size}")
    elif emp is not None and emp <= 25:
        bits.append(f"hiring employee #{emp}")
    elif cues.get("stage") in {"pre-seed", "seed"}:
        bits.append(f"{cues['stage']} stage")
    elif cues.get("poster_role") and novelty != "repeat":
        bits.append(f"posted by its {cues['poster_role']}")
    tail = "; " + ", ".join(bits) if bits else ""
    role_txt = _role_phrase(role, TITLE_MAX - len(head) - len(tail) - len(", for "))
    title = f"{head}{', for ' + role_txt if role_txt else ''}{tail}"
    if len(title) > TITLE_MAX:
        title = f"{head}{tail}"
    if len(title) > TITLE_MAX:
        title = head
    return title.rstrip(". ")


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------

def _recover_name(comment_html: str | None, text: str) -> tuple[str | None, str | None]:
    """For a post with no conventional header: a name that one of its own links spells out.

    "Beacon AI builds ..." plus a link to beaconai.co gives "Beacon AI". The
    words must appear in the post exactly; nothing is taken from the host.
    """
    for host in host_candidates(comment_html):
        label = _key(_host_label(host))
        if len(label) < 4:
            continue
        for m in re.finditer(r"(?<![\w.])([A-Z0-9][\w&'’-]*(?:\s+[A-Z0-9][\w&'’-]*){0,3})", text):
            words = m.group(1).split()
            for n in range(len(words), 0, -1):
                cand = " ".join(words[:n])
                if _key(cand) == label and valid_name(cand):
                    return cand, host
    return None, None


_ABBREV = re.compile(r"(?:\b[A-Z]|\bU\.S|\be\.g|\bi\.e|\betc|\bInc|\bCorp|\bCo|\bLtd|\bvs|\bapprox|\bNo|\bSr|\bJr|\bSt|\bPrev)\.$")
_VERBS = (
    r"(?:build|builds|building|make|makes|making|develop\w*|design|designs|designing|create|creates|creating|"
    r"provide\w*|turn|turns|help|helps|give|gives|deliver\w*|operate\w*|manufactur\w+|fuse|automat\w+|work\s+with)"
)
_HIRING_TALK = re.compile(r"\b(?:hiring|hire|looking\s+for|seeking|apply|join\s+us|openings?|open\s+roles)\b", re.I)
_INTRO = re.compile(r"^\W*(?:my\s+name|i(?:'|’)?m\b|i\s+am\b|hi\b|hello\b|hey\b)", re.I)


def _describes(sentence: str, name: str) -> bool:
    """Does the sentence have the company (or "we") as subject of a verb that says what it does?"""
    first = re.escape(name.split()[0]) if name.split() else r"\0"
    subject = rf"(?:\bwe\b|\b{first}\b(?:\s+[\w&.-]+){{0,3}}?)"
    does = rf"{subject}(?:\s+\w+){{0,2}}?\s+{_VERBS}\b"
    is_a = (rf"{subject}(?:\s+(?:is|are)|(?:'|’)re|(?:'|’)s\s+(?:main\s+)?(?:mission|activity)\s+is)\s+"
            rf"(?:(?:actively|currently|now)\s+)?(?:a|an|the|on\s+a\s+mission|to|building|developing|making|creating|"
            rf"(?-i:[A-Z]))")
    return bool(re.search(does, sentence, re.I) or re.search(is_a, sentence, re.I))


def sentences(paragraph: str) -> list[str]:
    """Split on sentence ends without breaking at "U.S." or a middle initial."""
    out: list[str] = []
    for piece in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"“(])", paragraph.strip()):
        if out and _ABBREV.search(out[-1]):
            out[-1] = out[-1] + " " + piece
        else:
            out.append(piece)
    return [re.sub(r"\s+", " ", x).strip() for x in out if x.strip()]


def _lead_sentence(body: str, name: str) -> str | None:
    """The first sentence in which the company says what it is or does, verbatim."""
    seen = 0
    for para in body.split("\n\n"):
        for sent in sentences(para):
            seen += len(sent)
            if seen > 900:
                return None
            if not (30 <= len(sent) <= 240) or sent.count(" ") < 4:
                continue
            if _HIRING_TALK.search(sent) or _INTRO.match(sent):
                continue
            if _describes(sent, name):
                return sent
    return None


def _description(body: str) -> str | None:
    """Opening of the post, skipping one-line leftovers such as "Apply here:"."""
    paras = [p.strip() for p in body.split("\n\n") if p.strip()]
    while paras and (len(paras[0]) < 40 or paras[0].count(" ") < 5):
        paras = paras[1:]
    text = "\n\n".join(paras)[:500].strip()
    return text or None


def candidate(hit: dict) -> dict[str, Any] | None:
    """Parse one top-level comment. None when it is not a company post this collector can name."""
    raw = hit.get("comment_text")
    if not raw or not hit.get("objectID") or not hit.get("author"):
        return None
    header = parse_header(raw)
    text = prose(raw)
    name, alias = header["name"], header["alias"]
    recovered_host = None
    if not name:
        name, recovered_host = _recover_name(raw, text)
        if not name:
            return None
    if _NOT_COMPANY.search(name) or (alias and _NOT_COMPANY.search(alias)):
        return None
    if _INCUMBENT.match(name.strip()) or says_institution(text, name):
        return None
    # Body: everything after the header line (or the whole text when there was no header).
    if header["fields"]:
        parts = text.split("\n", 1)
        body = parts[1].strip() if len(parts) > 1 else ""
        header_roles = " | ".join(
            re.sub(r"\s+", " ", _BARE_DOMAIN.sub(" ", _URL.sub(" ", f))).strip()
            for i, f in enumerate(header["fields"]) if i != header["name_field_index"]
        )
    else:
        body, header_roles = text, ""
    ev = evidence(header_roles, name, body)
    urls = links_in(raw)
    domain = recovered_host or pick_domain(name, alias, raw)
    # Prefer the casing the company uses in its own sentences over an all-caps header.
    display = name
    if name.isupper() and len(name) > 4:
        m = re.search(rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])", body, re.I)
        if m and not m.group(0).isupper():
            display = m.group(0)
    # "etc. (Exploration Technology Corp.)": a three-letter short form cannot be matched or read cold,
    # so the full name the header gives beside it leads and the short form becomes the alias.
    shown_alias = alias
    if alias and len(_key(display)) < 4 and len(_key(alias)) >= 6 and valid_name(alias.rstrip(".")):
        display, shown_alias = alias, display
    return {
        "hit": hit, "name": display, "alias": alias, "shown_alias": shown_alias, "header": header, "text": text,
        "body": body, "evidence": ev, "domain": domain,
        "own_hosts": {h for h in host_candidates(raw) if name_matches_host(name, h, alias)},
        "ats": pick_ats(name, alias, urls + _URL.findall(render(raw, "href"))),
        "cues": team_cues(text, name),
    }


_OFF_THESIS_LEAD = re.compile(r"\b(?:blockchains?|smart\s+contracts?|web3|defi|crypto(?:currenc(?:y|ies))?|layer[- ][12])\b", re.I)


def passes_gate(cand: dict[str, Any]) -> bool:
    ev = cand["evidence"]
    if ev["fit"] < MIN_FIT or ev["units"] < MIN_UNITS or not ev["core_ok"]:
        return False
    if _OFF_THESIS_LEAD.search(cand["body"][:LEAD_CHARS]):
        return False  # "our runtime conforms to the RISC-V specification" is a blockchain, not a chip
    return classify(cand["text"][:TEXT_CHARS])["fit"] >= SHARED_FLOOR  # the text the signal will carry


def company_key(cand: dict[str, Any]) -> str:
    return cand["domain"] or _key(cand["name"])


_FACTS: dict[str, dict[str, Any]] = {}


def _post_facts(hit: dict) -> dict[str, Any]:
    """Parsed header names, hosts and prose of one comment, memoised by id."""
    oid = str(hit.get("objectID") or id(hit))
    facts = _FACTS.get(oid)
    if facts is None:
        raw = hit.get("comment_text")
        header = parse_header(raw)
        hosts = host_candidates(raw)
        facts = {
            "names": {_key(n) for n in (header["name"], header["alias"]) if n and _key(n)},
            "hosts": set(hosts),
            # The post's own site: the host that matches the name in its own header.
            "own_domain": next((h for h in hosts if header["name"]
                                and name_matches_host(header["name"], h, header["alias"])), None),
            "plain": prose(raw),
        }
        if len(_FACTS) > 60000:
            _FACTS.clear()
        _FACTS[oid] = facts
    return facts


# Words that may follow a name without making it a different name: "Acme Inc. | ...", "9 Mothers YC P26 | ...".
_NAME_FOLLOWERS = {"inc", "inc.", "llc", "ltd", "ltd.", "gmbh", "corp", "corp.", "co.", "yc", "is", "are"}


def opens_with(name: str, plain: str) -> str | None:
    """How a post's text opens with a company name, if it does.

    "exact" when the name is followed by a separator or a lower-case word
    ("Greenzie is hiring", "Skydio - Autonomy | ..."). "extended" when a
    capitalised word follows ("Cable" before "Cable Labs | ..."), which may
    be a longer form of the same name or a different company altogether.
    """
    p = plain.lstrip(" *_#>-")
    if p[: len(name)].lower() != name.lower():
        return None
    rest = p[len(name):]
    if rest[:1].isalnum():
        return None
    m = re.match(r"[ \t]+([A-Za-z0-9][\w&'’.-]*)", rest)
    if m and not m.group(1)[0].islower() and m.group(1).lower() not in _NAME_FOLLOWERS:
        return "extended"
    return "exact"


def company_match(name: str, alias: str | None, domain: str | None, other: dict,
                  same_author: bool = False, own_hosts: Iterable[str] = ()) -> str | None:
    """Is this earlier top-level hiring comment a post for the same company? "same", "doubt" or None.

    "same" takes positive evidence: the post's own site is the company's
    domain (so "Strobe | ..." linking strobepower.com is Strobe Power), its
    header names the company, or it opens with the company's name. A mention
    or a link further down is nothing: other companies name-drop customers
    and former employers.

    "doubt" is a name match that something contradicts, which is enough to
    withhold a "first" claim and to stop the count being called exact, but
    is never counted as a prior post: the same name over a different own
    site by a different author (two companies can share a name), or the
    name followed by another capitalised word by a different author
    ("Cable Labs" is not "Cable"). `own_hosts` are the other hosts the
    current post uses for itself (boom.aero beside boomsupersonic.com): an
    earlier post on one of those is on the company's own site too.
    """
    facts = _post_facts(other)
    ours = {h for h in (domain, *own_hosts) if h}
    if ours and (facts["own_domain"] in ours or (not facts["names"] and ours & facts["hosts"])):
        return "same"
    names = {_key(n) for n in (name, alias) if n and _key(n)}
    opens = opens_with(name, facts["plain"])
    if names & facts["names"] or opens == "exact":
        other_site = bool(ours and facts["own_domain"])  # not one of ours, or it would have matched above
        return "doubt" if other_site and not same_author else "same"
    if opens == "extended":
        return "same" if same_author else "doubt"
    return None


AMBIGUOUS_DAYS = 400  # an unnamed post by the same author this recently may be the same employer


def prior_posts(cand: dict[str, Any], thread_id: int, author_hits: list[dict], phrase_hits: list[dict],
                scan_hits: list[dict], distinctive: bool = True,
                domain_hits: list[dict] | None = None) -> dict[str, Any]:
    """Earlier monthly threads in which this company posted, from the histories read.

    prior_threads counts positive matches (see company_match) and, when the
    name is distinctive (the exact-phrase search over all of HN was
    exhaustive), earlier posts by the same author that contain the name and
    carry no other company's header. prior_post_count is the number of
    comments behind that count: a company that posted twice in one thread
    has more posts than threads. loose_mentions counts earlier top-level
    hiring comments that could not be attributed: a doubtful name match, or
    a distinctive name inside somebody else's post. ambiguous_author_posts
    counts recent top-level comments by the same author that name no
    company. Either is enough to withhold a "first" claim, but neither is
    counted as a prior post.
    """
    me = cand["hit"]
    ts = int(me.get("created_at_i") or 0)
    author = me.get("author")
    name = cand["header"]["name"] or cand["name"]
    threads: dict[int, int] = {}  # story_id -> earliest created_at_i
    counted: set[str] = set()
    seen: set[str] = set()
    author_posts = 0
    loose = 0
    ambiguous = 0
    named = re.compile(rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])", re.I)
    sources = (("author", author_hits), ("phrase", phrase_hits), ("domain", domain_hits or []), ("scan", scan_hits))
    for source, hits in sources:
        for h in hits:
            sid = h.get("story_id")
            if not sid or h.get("parent_id") != sid or sid == thread_id:
                continue
            if not HIRING_STORY.search(h.get("story_title") or ""):
                continue
            try:
                hts = int(h.get("created_at_i") or 0)
            except (TypeError, ValueError):
                continue
            if not hts or hts >= ts:
                continue
            oid = str(h.get("objectID"))
            first_look = oid not in seen
            seen.add(oid)
            facts = _post_facts(h)
            if source == "author" and facts["names"] and first_look:
                author_posts += 1
            mine = h.get("author") == author
            mentions = bool(named.search(facts["plain"]))
            match = company_match(name, cand["alias"], cand["domain"], h, same_author=mine,
                                  own_hosts=cand.get("own_hosts") or ())
            if match == "same" or (distinctive and mine and mentions and not facts["names"]):
                threads[sid] = min(threads.get(sid, hts), hts)
                counted.add(oid)
            elif not first_look:
                continue
            elif match == "doubt" or (distinctive and mentions and source in ("author", "phrase")):
                loose += 1
            elif mine and not facts["names"] and (ts - hts) <= AMBIGUOUS_DAYS * 86400:
                ambiguous += 1
    first = min(threads.values()) if threads else None
    return {
        "prior_threads": len(threads),
        "prior_post_count": len(counted),
        "first_seen": datetime.fromtimestamp(first, tz=timezone.utc).date() if first else None,
        "author_prior_hiring_posts": author_posts,
        "loose_mentions": loose,
        "ambiguous_author_posts": ambiguous,
    }


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------

def find_threads(ctx: Context) -> list[dict[str, Any]]:
    """Every "Who is hiring?" thread, newest first: [{id, title, created (date), ts}]."""
    data = http.get_json(SEARCH_URL, ttl=LIVE_TTL, params={
        "tags": f"story,author_{THREAD_AUTHOR}",
        "hitsPerPage": PAGE_SIZE,
        "attributesToRetrieve": "objectID,title,created_at,created_at_i",
        "attributesToHighlight": "[]",
    })
    out = []
    for h in data.get("hits") or []:
        if not THREAD_TITLE.match(h.get("title") or "") or not h.get("created_at_i"):
            continue
        try:
            out.append({
                "id": int(h["objectID"]), "title": h["title"].strip(), "ts": int(h["created_at_i"]),
                "created": datetime.fromtimestamp(int(h["created_at_i"]), tz=timezone.utc).date(),
            })
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda t: -t["ts"])
    return out


def fetch_top_level(thread: dict[str, Any], today: date) -> list[dict]:
    """All top-level comments of one thread, newest first."""
    closed = (today - thread["created"]).days > THREAD_OPEN_DAYS + 2
    hits: list[dict] = []
    seen: set[str] = set()
    cursor: int | None = None
    for _ in range(6):
        nf = f"parent_id={thread['id']}" + (f",created_at_i<={cursor}" if cursor is not None else "")
        data = http.get_json(SEARCH_URL, ttl=CLOSED_TTL if closed else LIVE_TTL, params={
            "tags": f"comment,story_{thread['id']}",
            "numericFilters": nf,
            "hitsPerPage": PAGE_SIZE,
            "attributesToRetrieve": _COMMENT_FIELDS,
            "attributesToHighlight": "[]",
        })
        page = data.get("hits") or []
        fresh = 0
        for h in page:
            if h.get("objectID") and h["objectID"] not in seen:
                seen.add(h["objectID"])
                hits.append(h)
                fresh += 1
        if len(page) < PAGE_SIZE or not fresh:
            break
        cursor = min(int(h["created_at_i"]) for h in page if h.get("created_at_i"))
    return hits


def author_history(author: str) -> tuple[list[dict], bool]:
    """(the author's comments in hiring threads, whether the whole history was read)."""
    out: list[dict] = []
    cursor: int | None = None
    complete = False
    for _ in range(AUTHOR_PAGES):
        params: dict[str, Any] = {
            "tags": f"comment,author_{author}",
            "hitsPerPage": PAGE_SIZE,
            "attributesToRetrieve": _COMMENT_FIELDS,
            "attributesToHighlight": "[]",
        }
        if cursor is not None:
            params["numericFilters"] = f"created_at_i<{cursor}"
        data = http.get_json(SEARCH_URL, ttl=HISTORY_TTL, params=params)
        page = data.get("hits") or []
        out.extend(h for h in page if HIRING_STORY.search(h.get("story_title") or ""))
        if len(page) < PAGE_SIZE:
            complete = True
            break
        cursor = min(int(h["created_at_i"]) for h in page if h.get("created_at_i"))
    return out, complete


def phrase_history(phrase: str) -> tuple[list[dict], bool]:
    """(comments in hiring threads containing the exact phrase, whether the search was exhaustive).

    Used with the company's name and, separately, with its domain: Algolia
    indexes "strobepower.com" inside a link as searchable text.
    """
    data = http.get_json(SEARCH_URL, ttl=HISTORY_TTL, params={
        "query": '"' + phrase.replace('"', " ") + '"',
        "tags": "comment",
        "hitsPerPage": PAGE_SIZE,
        "typoTolerance": "false",
        "restrictSearchableAttributes": "comment_text",
        "attributesToRetrieve": _COMMENT_FIELDS,
        "attributesToHighlight": "[]",
    })
    hits = data.get("hits") or []
    total = data.get("nbHits")
    complete = isinstance(total, int) and total <= len(hits)
    return [h for h in hits if HIRING_STORY.search(h.get("story_title") or "")], complete


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------

def build_signal(cand: dict[str, Any], thread: dict[str, Any], history: dict[str, Any],
                 posts_in_thread: int = 1) -> Signal | None:
    """One Signal for one company in one thread. `history` comes from prior_posts plus completeness flags."""
    hit = cand["hit"]
    try:
        when = datetime.fromtimestamp(int(hit["created_at_i"]), tz=timezone.utc)
    except (KeyError, TypeError, ValueError):
        return None
    ev, cues, header = cand["evidence"], cand["cues"], cand["header"]
    prior = int(history.get("prior_threads") or 0)
    first_seen = history.get("first_seen")
    if first_seen and (when.date() - first_seen).days > LONG_HISTORY_DAYS:
        cues = {**cues, "long_history": True}
    all_time = bool(history.get("all_time_complete"))
    recent = bool(history.get("recent_complete"))
    doubt = bool(history.get("loose_mentions")) or bool(history.get("ambiguous_author_posts"))
    if prior > 0:
        novelty = "repeat"
    elif doubt:
        novelty = "unknown"  # the name shows up in earlier hiring posts that could not be attributed
    elif all_time:
        novelty = "first_ever"
    elif recent:
        novelty = "first_recent"
    else:
        novelty = "unknown"
    strength = strength_for(novelty, prior, cues, ev["fit"])
    # The count of earlier threads is exact only when every history was read and nothing was left unattributed.
    exact = all_time and not history.get("loose_mentions")
    title = title_for(novelty, prior, first_seen, header["role"], cues, exact, history.get("prior_post_count"))
    unknown = novelty == "unknown"

    metrics: dict[str, Any] = {
        "hn_item_id": int(hit["objectID"]),
        "hn_thread_id": thread["id"],
        "thread_month": thread["created"].strftime("%Y-%m"),
        "thesis_fit": ev["fit"],
        "thesis_units": ev["units"],
        "prior_hiring_threads": prior,
        "prior_history_complete": exact,
        "unattributed_name_mentions": int(history.get("loose_mentions") or 0),
        "author_prior_hiring_posts": int(history.get("author_prior_hiring_posts") or 0),
        "first_hiring_post": novelty == "first_ever",
        "posts_in_thread": posts_in_thread,
    }
    if novelty == "first_recent":
        metrics["first_hiring_post_in_months"] = HISTORY_THREADS
    if first_seen and exact:
        metrics["first_hiring_thread"] = first_seen.strftime("%Y-%m")
    elif first_seen:
        metrics["earliest_hiring_thread_found"] = first_seen.strftime("%Y-%m")
    if cand["ats"]:
        metrics["ats_provider"], metrics["ats_slug"] = cand["ats"]
    for k in ("team_size", "team_size_upper", "open_roles", "employee_number", "among_first_employees",
              "stage", "funding_stated", "funding_currency"):
        if cues.get(k) is not None:
            metrics[k] = cues[k]
    for k in ("founding_role", "first_hire", "hardware_roles", "public_company", "acquired"):
        if cues.get(k):
            metrics[k] = True
    if header["yc_batch"]:
        metrics["yc_batch"] = header["yc_batch"]
    if header["role"]:
        metrics["role"] = header["role"][:160]
    if cues.get("poster_role"):
        metrics["poster_role"] = cues["poster_role"]

    links = {"hn_item": ITEM_PAGE.format(id=hit["objectID"])}
    if cand["domain"]:
        links["website"] = "https://" + cand["domain"]
    entity = EntityHint(
        name=cand["name"],
        domain=cand["domain"],
        aliases=[cand["shown_alias"]] if cand.get("shown_alias") else [],
        one_liner=_lead_sentence(cand["body"], header["name"] or cand["name"]),
        description=_description(cand["body"]),
        location=header["location"],
        links=links,
    )
    author = hit["author"]
    role = "HN hiring post author"
    if cues.get("poster_role"):
        role = f"{cues['poster_role']} (as stated in own HN hiring post)"
    people = [Person(name=author, role=role, links={"hn": USER_PAGE.format(name=author)})]

    return Signal(
        source=SLUG,
        family=FAMILY,
        kind="hn_hiring_post",
        entity=entity,
        title=title,
        occurred_at=when.strftime("%Y-%m-%d"),
        url=ITEM_PAGE.format(id=hit["objectID"]),
        value=None if unknown else float(prior + 1),
        unit=None if unknown else "hiring threads",
        strength=strength,
        metrics=metrics,
        people=people,
        text=cand["text"][:TEXT_CHARS],
    )


def collect(ctx: Context) -> Iterable[Signal]:
    """One signal per on-thesis company per monthly thread, newest thread first."""
    try:
        threads = find_threads(ctx)
    except Exception as e:  # noqa: BLE001 - without the thread list there is nothing to read
        ctx.warn(f"hn_hiring: thread list: {type(e).__name__}: {e}")
        return
    if not threads:
        ctx.warn("hn_hiring: no Who is hiring threads found")
        return
    since_ts = calendar.timegm(ctx.since.timetuple())
    # occurred_at is the UTC date HN shows for the comment, and ctx.today is the UTC run date, so in a
    # normal run nothing is dated after today. A run given an earlier date (a replay) must not emit
    # comments from its own future: the scorer gives them no weight and a row dated ahead of its run
    # reads as an error, so they wait for the first run on or after their date. (Re-dating them to
    # today would store the same post twice: the date is part of a signal's identity.)
    until_ts = calendar.timegm((ctx.today + timedelta(days=1)).timetuple())
    window = [t for t in threads
              if (ctx.since - t["created"]).days <= THREAD_OPEN_DAYS and t["ts"] < until_ts]
    ctx.log(f"hn_hiring: {len(window)} threads in window, {len(threads)} known")

    scan_cache: dict[int, list[dict]] = {}
    scan_failed: set[int] = set()

    def scan(before: dict[str, Any]) -> tuple[list[dict], bool]:
        """(top-level comments of the HISTORY_THREADS threads before this one, whether all were read)."""
        older = [t for t in threads if t["ts"] < before["ts"]][:HISTORY_THREADS]
        out: list[dict] = []
        for t in older:
            if t["id"] not in scan_cache:
                try:
                    scan_cache[t["id"]] = fetch_top_level(t, ctx.today)
                except Exception as e:  # noqa: BLE001
                    ctx.warn(f"hn_hiring: history thread {t['id']}: {type(e).__name__}: {e}")
                    scan_cache[t["id"]] = []
                    scan_failed.add(t["id"])
            out.extend(scan_cache[t["id"]])
        whole = len(older) == HISTORY_THREADS and not any(t["id"] in scan_failed for t in older)
        return out, whole

    emitted_entities: set[str] = set()
    domain_of: dict[str, str] = {}  # company name -> the domain its newest kept post gave it
    deferred = 0
    for thread in window:
        if ctx.limit and len(emitted_entities) >= ctx.limit:
            break
        try:
            hits = fetch_top_level(thread, ctx.today)
        except Exception as e:  # noqa: BLE001 - lose one month, keep the rest
            ctx.warn(f"hn_hiring: thread {thread['id']}: {type(e).__name__}: {e}")
            continue
        scan_cache.setdefault(thread["id"], hits)
        kept: dict[str, list[dict[str, Any]]] = {}
        for hit in hits:
            try:
                posted = int(hit.get("created_at_i") or 0)
                if posted < since_ts:
                    continue
                cand = candidate(hit)
                if cand is None or not passes_gate(cand):
                    continue
                if posted >= until_ts:
                    deferred += 1
                    continue
                # One company, one domain within a run: an older post that also mentions the domain its
                # newer post settled on, or uses the same name under another ending (gambitrobotics.com
                # before gambitrobotics.ai), is filed under the newer one.
                name_key = _key(cand["header"]["name"] or cand["name"])
                settled = domain_of.get(name_key)
                if settled and cand["domain"] and cand["domain"] != settled and (
                        settled in host_candidates(hit.get("comment_text"))
                        or _host_label(settled) == _host_label(cand["domain"])):
                    cand["domain"] = settled
                elif cand["domain"]:
                    domain_of.setdefault(name_key, cand["domain"])
                kept.setdefault(company_key(cand), []).append(cand)
            except Exception as e:  # noqa: BLE001 - one bad comment must not lose the thread
                ctx.warn(f"hn_hiring: comment {hit.get('objectID')}: {type(e).__name__}: {e}")
        # One signal per company per thread: the earliest post speaks for it.
        for key, cands in sorted(kept.items(), key=lambda kv: -max(int(c["hit"]["created_at_i"]) for c in kv[1])):
            if ctx.limit and len(emitted_entities) >= ctx.limit and key not in emitted_entities:
                continue
            cand = min(cands, key=lambda c: int(c["hit"]["created_at_i"]))
            try:
                author_hits, author_complete = author_history(cand["hit"]["author"])
                search_name = cand["header"]["name"] or cand["name"]
                phrase_hits, phrase_complete = phrase_history(search_name)
                if cand["alias"]:
                    phrase_hits = phrase_hits + phrase_history(cand["alias"])[0]
                # Every host the post uses for itself, and the domain settled on for the run.
                domain_hits: list[dict] = []
                domain_complete = True
                for host in sorted({h for h in (cand["domain"], *cand.get("own_hosts", ())) if h}):
                    found, whole = phrase_history(host)
                    domain_hits += found
                    domain_complete = domain_complete and whole
                scan_hits: list[dict] = []
                recent_complete = False
                if not phrase_complete:
                    scan_hits, recent_complete = scan(thread)
                history = prior_posts(cand, thread["id"], author_hits, phrase_hits, scan_hits,
                                      distinctive=phrase_complete, domain_hits=domain_hits)
                history["all_time_complete"] = author_complete and phrase_complete and domain_complete
                history["recent_complete"] = recent_complete or phrase_complete
                sig = build_signal(cand, thread, history, posts_in_thread=len(cands))
            except Exception as e:  # noqa: BLE001
                ctx.warn(f"hn_hiring: {cand['name']} ({cand['hit'].get('objectID')}): {type(e).__name__}: {e}")
                continue
            if sig is not None:
                emitted_entities.add(key)
                yield sig
    if deferred:
        ctx.log(f"hn_hiring: {deferred} on-thesis post(s) dated after {ctx.today} in UTC left for the next run")
