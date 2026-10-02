"""Finds the own website of a company that so far exists only on government paper, and proves it.

Most entities come from filings that name a company and nothing else: a Form D,
an FCC or FAA record, a trademark application, a federal award. With no domain,
the collectors that key on one (job boards, Tranco rank, DNS and certificate
footprint, Hacker News mentions) cannot run, so the company shows one signal
family when the truth may be three or four. This collector runs between
discovery and enrichment, takes the known companies that have no domain, and
tries to attach one. A wrong website is worse than no website, so every step
is built to miss rather than guess.

Where candidates come from, in the order they are tried:

  clearbit  Clearbit's company autocomplete (autocomplete.clearbit.com), which
            is served without a key. Only rows whose listed name is the
            company's name, or whose domain is exactly the name, are kept. On
            the 177 test companies of 2026-10-01 it gave a candidate for 55,
            and 5 of the verified sites could be reached no other way
            (lionpowerusa.com, mxllabs.com, arc-cleantech.com).
  guess     Hosts built from the name: the legal-stripped name with spaces
            removed, on the endings a hardware startup picks, plus the forms
            without a trailing generic word ("Swarm Defense Technologies" gives
            swarmdefense.com) and the ending that completes the name ("Alva
            Energy" gives alva.energy, "Skild AI" gives skild.ai). Each guess
            is resolved over DNS-over-HTTPS first, and only hosts with an
            address are fetched.

Brandfetch's search endpoint also answers without a key and finds more, but its
published usage guidelines require a client id on every request and limit the
data to a live autocomplete box in a browser, never stored. It is not used.
Web search engines are not scraped.

A candidate becomes the company's domain only when all of this holds:

  1. The homepage fetches, redirects followed, and the host it ends on is the
     one recorded. A parked, for-sale or registrar page is rejected, and so is
     a page on somebody else's platform (a subdomain of a site builder).
  2. The company's name is on the page as a name: in <title>, og:site_name,
     og:title, the first <h1>, a schema.org Organization block or the
     copyright line. Legal forms are dropped and case is ignored, but the
     full distinctive name is required: "Swarm Defense" passes for "Swarm
     Defense Technologies", "Swarm" does not, and neither does "Swarm Defense
     Systems", which is somebody else.
  3. The page is about the same kind of business. The thesis classifier is
     run on the page with the company's own name blanked out (a name like
     "Parallel Robotics" would otherwise vouch for itself); fit must reach
     0.3 and the sector the entity already has must score above zero.
  4. A second fact ties the page to this entity rather than a namesake. The
     facts that count: a link, on the homepage or its careers page, to a job
     board, GitHub login or social page the entity is already known by; the
     entity's city on the page (when the known entity carries a location);
     two thesis terms shared by the entity's description and the page that
     the name itself does not supply; most of the description's own words on
     the page. How much is asked depends on the name:
       made-up name ("Orkora", "Veridis Defense")   nothing more, as long as
           the name does not itself state the sector the entity is known for
           and the page's own title and description are on thesis. "Hadron
           Energy" on an energy page proves only the name, so it needs a fact.
       plain words ("Aura Robotics", "Lion Power")  one fact, or a name of
           three words or more that is exactly the site's name on a matching
           domain.
       one common word, or under five letters       the domain must be the
           name, one fact is required, and the confidence bar is higher.
     Which is which is decided with the system word list. On a machine with
     no word list every name is treated as plain, which costs matches and
     never adds a wrong one.
  5. The confidence built from these checks reaches ACCEPT_CONFIDENCE.
  6. No second, different site passes for the same name. Every candidate is
     looked at even after one verifies; if two pass, the name is shared and
     neither is recorded.

Step 4 exists because name, fit and sector together were not enough. On the
test set they accepted a site for 78 companies. Reading them found 7 that
were another company (an Indian silica plant for an Illinois energy-software
filer, a Wisconsin drone dealer for an Atlanta one, a Kazakh autonomy stack
for a Brooklyn launcher maker) and 2 names under which several unrelated
sites all passed (three Swarm Defenses, three Power on Demands). A second
check, against 84 board companies whose domain was already known and was
hidden from the collector, found one more: a Pakistani solar firm for a US
reactor company, both named Hadron Energy.

One signal per verified company: kind "website_verified", strength 0.0 (it
carries identity and text, not momentum), the checks that passed and the
confidence in metrics, and the page's title, description and opening text as
`text`, which gives the thesis classifier a real description for companies
whose filings carry none. GitHub, job-board, X and LinkedIn links found on
the homepage ride along. Every rejected candidate that got as far as a page
is logged with the reason, so a run can be audited.

A second mode reads the site of a company whose domain is already known
but which has no description of its own (no one-liner, or only the goods
list of a trademark filing). The domain came from another source, a contact
e-mail or a registry's website field, so it may be a parent company, a law
firm or a consultant who filed on the applicant's behalf. The homepage is
therefore used only when the company's name stands on it as a name, by the
same test as above; on a domain that itself starts with the company's name,
the name without its filler word is enough ("Radiant" on radiantnuclear.com
for Radiant Industries). No second fact and no thesis
fit are asked: the domain is the corroboration, and an off-thesis
description is still the company's own words. The signal is
"website_described", with the known domain kept as given. Its one-liner is
the meta description, then og:description, then the first real sentence of
the page's opening text, preferring a paragraph that opens with the
company's own name; when none of those reads as a sentence it is left empty.
On the 82 board companies with a domain and no description (2026-10-01), 79
were described, 76 of them with a one-liner, in 14 seconds: one request each.

antenna.http returns a page but not the URL it was finally served from, and
that URL is the one thing this collector cannot do without. While a homepage
is being fetched, a urllib response handler copies the final URL into a
response header, on this collector's thread only; nothing else in the process
sees a difference. It can go once http.request reports the final URL itself.

Requests per run: one autocomplete call and at most GUESS_LIMIT DNS lookups per
company, split between two public resolvers, then one homepage per host that
exists and, rarely, one careers page. Measured on 2026-10-01 on the 177
companies of the first run that had no domain: 2,287 requests and 4 min 25 s
with an empty cache, 76 s with a warm one (hosts that do not answer are not
cached, so they are asked again each run). 57 were verified, 59 once the job
boards already recorded for them were passed in, and all were read by hand.
On the 84 companies with a known domain, 26 were verified (31 with their job
boards passed in) and every one matched the known domain.
"""

from __future__ import annotations

import json
import queue
import re
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

from .. import http
from ..models import EntityHint, Signal
from ..thesis import classify, term_pattern
from .base import Context, clean_domain, normalize_name, strip_legal

SLUG = "web_presence"
FAMILY = "traffic"
STAGE = "identify"
DESCRIPTION = "Finds and verifies the website of known companies with no domain, and reads the site of those with no description"

MAX_LOOKUPS = 260          # companies with no domain attempted per run
MAX_DESCRIBE = 300         # companies with a domain and no description read per run
WORKERS = 16               # companies in flight; pacing is per host inside antenna.http
RUN_BUDGET_SECONDS = 480   # stop starting new companies after this long
ACCEPT_CONFIDENCE = 0.75
ACCEPT_CONFIDENCE_WEAK = 0.85
GUESS_LIMIT = 8            # DNS lookups per company at most

SUGGEST_URL = "https://autocomplete.clearbit.com/v1/companies/suggest"
SUGGEST_TTL = 3 * 86400
SUGGEST_KEEP = 3
# Two public resolvers with the same JSON answer format. A host always goes
# to the same one, so a warm cache answers every repeat lookup.
RESOLVERS = ("https://dns.google/resolve", "https://cloudflare-dns.com/dns-query")
DNS_TTL = 7 * 86400
HOMEPAGE_TTL = 24 * 3600
HOMEPAGE_TIMEOUT = 12
RETRY_PAUSE = 2.0          # seconds before the one retry of a 5xx or 429
# Our own Accept header keeps these cache rows apart from any other
# collector's fetch of the same URL, which would lack the final-URL header.
HOMEPAGE_ACCEPT = "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5"
FINAL_URL_HEADER = "x-antenna-final-url"

MAX_HTML = 1_500_000       # characters of a homepage that are parsed
MAX_TEXT = 200_000         # characters of visible text kept (the footer matters)
CLASSIFY_CHARS = 6000
WORDS_CHARS = 20_000       # visible text searched for the entity's own words
SIGNAL_TEXT_CHARS = 1500
ONE_LINER_CHARS = 200
HERO_BLOCKS = 40           # opening paragraphs and headings searched for a first sentence
# A one-liner that is a registry's words, not the company's: the goods list
# of a trademark filing. An entity that has only this is still described.
REGISTRY_LINER_PREFIXES = ("Trademark goods:",)

# Words that pad a company name without naming it. One may be dropped from the
# end when at least two words remain: "Surtr Defense Systems" is still itself
# as "Surtr Defense".
FILLER_WORDS = frozenset({
    "technologies", "technology", "tech", "systems", "industries", "labs", "lab", "holdings",
    "holding", "group", "solutions", "enterprises", "services", "partners", "international",
    "global", "ventures", "works",
})
# Generic last words that stay part of the name for matching, but that a
# company often leaves out of, or turns into, its domain.
SECTOR_WORDS = frozenset({
    "defense", "energy", "robotics", "aerospace", "space", "dynamics", "ai", "atomics", "nuclear",
    "fusion", "power", "aero", "aerial", "drones", "drone", "motors", "manufacturing", "materials",
    "propulsion", "maritime", "robot", "humanoids", "uas", "semiconductor", "semiconductors",
    "autonomy", "design", "storage",
})
# A last word that is also a domain ending: "Alva Energy" may live at alva.energy.
ENDING_FOR_WORD = {
    "ai": "ai", "energy": "energy", "space": "space", "aero": "aero", "aerospace": "aero",
    "systems": "systems", "industries": "industries", "tech": "tech", "technologies": "tech",
    "technology": "tech", "solutions": "solutions",
}
# One extra ending worth a lookup for a sector: sparkz.energy, mantis.space.
SECTOR_ENDINGS = {"energy": ("energy",), "space": ("space",)}
_DOMAIN_SHAPED = re.compile(r"^[a-z0-9][a-z0-9-]*\.(?:ai|io|co|com|tech|dev|us|energy|space|aero|bot)$")

_LEGAL_TOKENS = frozenset({
    "inc", "incorporated", "llc", "corp", "corporation", "co", "company", "ltd", "limited", "gmbh",
    "plc", "pbc", "lp", "llp", "srl", "sa", "ab", "ag", "bv", "oy", "pty", "pte", "kk", "sas",
})
_SEGMENT_NOISE = frozenset({"home", "homepage", "welcome", "to", "official", "website", "site", "the"})
_TWO_LEVEL_SUFFIXES = frozenset({
    "co.uk", "org.uk", "com.au", "net.au", "co.nz", "co.jp", "co.kr", "co.in", "co.il", "co.za",
    "com.br", "com.cn", "com.mx", "com.sg", "com.tr", "com.tw", "com.hk", "com.my", "com.ar",
})

# Marketplaces, registrars and parking networks: a page that ends on one of
# these hosts is a domain for sale, not a company.
PARKED_HOSTS = (
    "sedo.com", "sedoparking.com", "dan.com", "godaddy.com", "hugedomains.com", "afternic.com",
    "squadhelp.com", "atom.com", "bodis.com", "parkingcrew.net", "above.com", "buydomains.com",
    "domainmarket.com", "brandbucket.com", "namebright.com", "uniregistry.com", "undeveloped.com",
    "epik.com", "dynadot.com", "namecheap.com", "porkbun.com", "name.com", "namesilo.com",
    "register.com", "networksolutions.com", "domain.com", "spaceship.com", "gname.com",
    "domains.squarespace.com", "saw.com", "brandpa.com", "efty.com", "godaddysites.com",
)
_PARKED_PHRASES = re.compile(
    r"domain(?: name)?(?: [\w.-]+)? (?:is|may be|might be|could be) (?:available )?for sale"
    r"|this domain is (?:available|for sale|parked|registered)"
    r"|(?:buy|purchase|acquire|get) this domain"
    r"|domain (?:is )?parked|parked domain|parked free|(?:web ?)?page is parked|is parked free"
    r"|domain for sale|domains? for sale|for sale on|make an offer (?:on|for) this domain"
    r"|inquire about (?:this|the) domain|interested in (?:this|the) domain"
    r"|domain is available for purchase|this domain has (?:recently )?been registered"
    r"|domain has expired|this domain name has expired|renew (?:this|your) domain"
    r"|sedo domain parking|courtesy of godaddy|related searches",
    re.I,
)
_PARKED_MARKUP = (
    "sedoparking.com", "parkingcrew.net", "bodis.com", "afternic.com/forsale", "dan.com/buy-domain",
    "hugedomains.com/domain_profile", "parklogic", "domainmarket.com", 'window.location.href="/lander"',
    "window.location.href='/lander'", "parking-lander", "/parking.php", "img.sedoparking",
)

_COPYRIGHT = re.compile(r"©|&copy;|\(c\)\s*(?=(?:19|20)\d\d)|\bcopyright\b", re.I)
_SEPARATORS = re.compile(r"\s+[|\-–—·•:»/]+\s+|\s*[|»•·]\s*|:\s+|\s+::\s+")
_WS = re.compile(r"\s+")

_BOILERPLATE_DESCRIPTION = re.compile(
    r"cookie|javascript|just another wordpress|my wordpress blog|wordpress site|lorem ipsum"
    r"|coming soon|under construction|page not found|website description|site description"
    r"|create-react-app|created using|this is the default|web site created|privacy policy"
    r"|this domain|domain name|is for sale|sign in|log in|access denied"
    r"|^(?:home|homepage|welcome|index)\b.{0,20}$",
    re.I,
)

# Words of a filing's goods list or a registry line that describe no business.
_WORD_STOP = frozenset("""
    trademark goods services service software saas featuring namely downloadable computer providing
    temporary online line recorded programs program being related field fields nature others order
    specification maker unmanned aircraft used using with from that this which their they have will
    into than also such based other including include includes configured purposes therefor thereof
    comprised primarily were what when where your more most over under about through between
""".split())

_US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "district of columbia": "DC",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID", "illinois": "IL",
    "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA",
    "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}

_GITHUB_RESERVED = frozenset({
    "about", "apps", "collections", "contact", "customer-stories", "enterprise", "events", "explore",
    "features", "login", "marketplace", "new", "notifications", "orgs", "pricing", "readme", "search",
    "security", "settings", "signup", "site", "sponsors", "team", "topics", "trending", "join",
})
_SOCIAL_RESERVED = frozenset({
    "intent", "share", "sharer", "home", "hashtag", "search", "i", "login", "signup", "explore",
    "privacy", "tos", "settings", "compose", "messages", "notifications", "twitter", "x",
    "wix", "squarespace", "webflow", "shopify", "wordpress", "godaddy", "framer", "elonmusk",
})
_GITHUB_LINK = re.compile(r"^https?://(?:www\.)?github\.com/([A-Za-z0-9][A-Za-z0-9-]{0,38})(?:[/?#]|$)", re.I)
_X_LINK = re.compile(r"^https?://(?:www\.|mobile\.)?(?:twitter|x)\.com/(?:#!/)?@?([A-Za-z0-9_]{1,15})(?:[/?#]|$)", re.I)
_LINKEDIN_LINK = re.compile(r"^https?://(?:[a-z]{2,3}\.)?linkedin\.com/company/([^/?#\s]+)", re.I)
_ATS_LINKS = (
    ("ashby", re.compile(r"^https?://jobs\.ashbyhq\.com/([^/?#\s]+)", re.I)),
    ("greenhouse", re.compile(
        r"^https?://(?:job-boards|boards)(?:\.eu)?\.greenhouse\.io/embed/job_board(?:/js)?\?(?:[^#\s]*&)?for=([^&#\s]+)", re.I)),
    ("greenhouse", re.compile(r"^https?://(?:job-boards|boards)(?:\.eu)?\.greenhouse\.io/([^/?#\s]+)", re.I)),
    ("lever", re.compile(r"^https?://jobs(?:\.eu)?\.lever\.co/([^/?#\s]+)", re.I)),
)
_ATS_SLUG_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


# ---------------------------------------------------------------- names

_MARKS = re.compile(r"[\u2122\u00ae\u00a9\u2120]")   # TM, (R), (C), SM glued to a name
_ORDINALS = {"1st": "first", "2nd": "second", "3rd": "third"}


def _fold(text: str) -> str:
    """Lowercase ASCII with accents removed: 'Blykalla' and 'Blyk\u00e4lla' compare equal."""
    text = _MARKS.sub(" ", text)
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def text_tokens(text: str) -> list[str]:
    """Words of a piece of page text, normalized the way names are."""
    s = re.sub(r"[\u2019'`]", "", _fold(text or ""))
    return [_ORDINALS.get(t, t) for t in re.sub(r"[^a-z0-9]+", " ", s).split() if t != "and"]


def name_tokens(name: str) -> list[str]:
    """Words of a company name with the legal form, 'and' and a leading 'the' dropped."""
    tokens = [_ORDINALS.get(t, t) for t in normalize_name(_fold(name)).split() if t != "and"]
    if len(tokens) > 1 and tokens[0] == "the":
        tokens = tokens[1:]
    return tokens


# Place names the word list lacks; a name built on one is not a coined name.
_PLACE_WORDS = frozenset({
    "kyoto", "tokyo", "beijing", "shanghai", "shenzhen", "europe", "asia", "africa", "america",
    "texas", "california", "florida", "pacific", "atlantic", "nordic", "boston", "austin", "london",
})
_COMMON_WORDS: frozenset[str] | None = None
_COMMON_WORDS_LOADED = False
_DICTIONARY_PATHS = ("/usr/share/dict/words", "/usr/dict/words")


def common_words() -> frozenset[str] | None:
    """The system word list, lowercased, or None when the machine has none.

    With no list every one-word name is treated as a common word, which costs
    matches and never adds a wrong one.
    """
    global _COMMON_WORDS, _COMMON_WORDS_LOADED
    if not _COMMON_WORDS_LOADED:
        for path in _DICTIONARY_PATHS:
            try:
                words = Path(path).read_text(encoding="utf-8", errors="ignore").split()
            except OSError:
                continue
            if len(words) > 20_000:
                _COMMON_WORDS = frozenset(w.lower() for w in words)
                break
        _COMMON_WORDS_LOADED = True
    return _COMMON_WORDS


def is_common_word(word: str, words: frozenset[str] | None = None) -> bool:
    """Whether a word is an ordinary dictionary word (plurals included)."""
    words = common_words() if words is None else words
    if words is None:
        return True
    w = word.lower()
    if w in words or w in _PLACE_WORDS:
        return True
    for suffix, repl in (("ies", "y"), ("es", ""), ("s", "")):
        if w.endswith(suffix) and len(w) > len(suffix) + 2 and w[: -len(suffix)] + repl in words:
            return True
    return False


def is_coined(tokens: Iterable[str], words: frozenset[str] | None = None) -> bool:
    """Whether a name carries a made-up word: "Orkora", "Veridis Defense",
    "DroneDeploy". Such a name rarely belongs to two companies in one sector.
    "Swarm Defense" and "Aura Robotics" are plain words, and several
    companies do share them."""
    for t in tokens:
        if t in FILLER_WORDS or t in SECTOR_WORDS or len(t) < 4 or t.isdigit():
            continue
        if not is_common_word(t, words):
            return True
    return False


@dataclass(frozen=True)
class NameForm:
    tokens: tuple[str, ...]
    label: str          # "full" or "short"
    weak: bool          # one common word, or under five letters
    dropped: tuple[str, ...] = ()   # filler words left out of a short form

    @property
    def compact(self) -> str:
        return "".join(self.tokens)


def name_forms(name: str, words: frozenset[str] | None = None) -> list[NameForm]:
    """The spellings of a name that count as the name when seen on a page.

    The full legal-stripped name always, and a shorter form made by dropping
    filler words from the end while two words remain. Sector words are never
    dropped, and a name is never cut to one word: "Swarm" is not "Swarm
    Defense", and "Aeglos" on a page did not prove "Aeglos Systems" (the one
    time that was allowed, it matched a different company).
    """
    tokens = tuple(name_tokens(name))
    if not tokens or len("".join(tokens)) < 3:
        return []

    def weak(form: tuple[str, ...]) -> bool:
        compact = "".join(form)
        if len(compact) < 5:
            return True
        return len(form) == 1 and is_common_word(form[0], words)

    forms = [NameForm(tokens, "full", weak(tokens))]
    short = tokens
    while len(short) > 2 and short[-1] in FILLER_WORDS:
        short = short[:-1]
    if short != tokens:
        forms.append(NameForm(short, "short", weak(short), tokens[len(short):]))
    return forms


_FILLER_FAMILY = {
    "technologies": "tech", "technology": "tech", "tech": "tech", "labs": "lab", "lab": "lab",
    "holdings": "holding", "holding": "holding", "systems": "system", "industries": "industry",
    "solutions": "solution", "services": "service", "enterprises": "enterprise",
}


def _find_run(tokens: list[str], compact: str, not_before: frozenset[str] = frozenset()) -> int:
    """Index where consecutive tokens spell `compact`, or -1.

    Joining tokens lets "Skild AI" match "SkildAI" and "LandSkyAI" match
    "LandSky AI", while still ending on word boundaries. An occurrence that
    is followed by a word in `not_before` does not count.
    """
    n = len(compact)
    for i in range(len(tokens)):
        acc = ""
        j = i
        for j in range(i, len(tokens)):
            acc += tokens[j]
            if len(acc) >= n:
                break
        if acc == compact:
            if j + 1 < len(tokens) and tokens[j + 1] in not_before:
                continue
            return i
    return -1


def _other_fillers(form: "NameForm") -> frozenset[str]:
    """Filler words that would make a short form somebody else's name.

    "Autonomous Defense" stands for "Autonomous Defense Technologies", so
    "Autonomous Defense Systems" on a page is a different company.
    """
    if form.label != "short" or not form.dropped:
        return frozenset()
    own = {_FILLER_FAMILY.get(d, d) for d in form.dropped}
    return frozenset(f for f in FILLER_WORDS if _FILLER_FAMILY.get(f, f) not in own)


def _segments(text: str) -> list[list[str]]:
    """A title split at its separators, each part as tokens with noise words
    ("Home", "Welcome to", "Inc") removed from the ends."""
    out = []
    for part in _SEPARATORS.split(text or ""):
        toks = text_tokens(part)
        while toks and toks[0] in _SEGMENT_NOISE:
            toks = toks[1:]
        while toks and (toks[-1] in _LEGAL_TOKENS or toks[-1] in _SEGMENT_NOISE):
            toks = toks[:-1]
        if toks:
            out.append(toks)
    return out


def match_name(text: str, forms: list[NameForm]) -> tuple[NameForm, bool] | None:
    """Find the company's name in one slot of a page.

    Returns the best form present (the full one when it is there) and whether
    a whole segment of the slot is the name, so the slot names the site
    rather than merely mentioning it.
    """
    if not text or not forms:
        return None
    tokens = text_tokens(text)
    present = [f for f in forms if _find_run(tokens, f.compact, _other_fillers(f)) >= 0]
    if not present:
        return None
    wanted = {f.compact for f in present}
    exact = False
    for seg in _segments(text):
        core = list(seg)
        # "Swarm Defense Technologies" as a segment still names the site when
        # the accepted form is "Swarm Defense".
        while core:
            if "".join(core) in wanted:
                exact = True
                break
            if core[-1] not in FILLER_WORDS:
                break
            core = core[:-1]
        if exact:
            break
    return present[0], exact


# ---------------------------------------------------------------- candidates

def _split_name(tokens: list[str]) -> tuple[list[str], list[str]]:
    """(short, stem): the name without trailing filler words, and without its
    last generic word of any kind. "Swarm Defense Technologies" gives
    (swarm defense, swarm)."""
    short = list(tokens)
    while len(short) > 1 and short[-1] in FILLER_WORDS:
        short = short[:-1]
    stem = short[:-1] if len(short) > 1 and short[-1] in SECTOR_WORDS else short
    return short, stem


def guess_hosts(name: str, sector: str | None = None, limit: int = GUESS_LIMIT) -> list[str]:
    """Hosts a company with this name would plausibly own, most likely first.

    "Swarm Defense Technologies" gives swarmdefense.com,
    swarmdefensetechnologies.com, swarmdefense.tech, swarmdefense.ai, ... A
    last word that is itself a domain ending is tried as one: "Alva Energy"
    gives alva.energy, "Skild AI" gives skild.ai. A name already written as a
    domain ("Trinary.ai") is tried as written, first. The order is by how
    often each shape was the verified one on the 2026-10-01 test set.
    """
    tokens = name_tokens(name)
    if not tokens:
        return []
    out: list[str] = []

    def add(label: str, ending: str) -> None:
        label = label.strip("-")
        if len(label) < 3 or len(label) > 40 or not re.fullmatch(r"[a-z0-9-]+", label):
            return
        if label.endswith(ending) and len(label) > len(ending):
            return  # "skildai.ai", "alvaenergy.energy": nobody registers these
        host = f"{label}.{ending}"
        if host not in out:
            out.append(host)

    written = re.sub(r"\s+", "", strip_legal(_fold(name)))
    if _DOMAIN_SHAPED.match(written):
        out.append(written)

    short_tokens, stem_tokens = _split_name(tokens)
    full, short, stem = "".join(tokens), "".join(short_tokens), "".join(stem_tokens)
    last = short_tokens[-1] if len(tokens) == len(short_tokens) else tokens[-1]
    before_last = "".join(tokens[:-1] if len(tokens) == len(short_tokens) else short_tokens)

    add(short, "com")
    if full != short:
        add(full, "com")
    # The ending that completes the name: alva.energy, skild.ai, abraxas.systems.
    if len(tokens) > 1 and last in ENDING_FOR_WORD:
        add(before_last, ENDING_FOR_WORD[last])
    add(short, "ai")
    add(short, "io")
    # A made-up first word often stands alone: "Atfuta Robotics" is at atfuta.com.
    if stem != short and len(stem) >= 5 and is_coined(stem_tokens):
        add(stem, "com")
    if len(tokens) > len(short_tokens) and tokens[len(short_tokens)] in ("technologies", "technology"):
        add(short + "tech", "com")
    if len(short_tokens) > 1:
        add("-".join(short_tokens), "com")
    add(short, "co")
    for ending in SECTOR_ENDINGS.get(sector or "", ()):
        if not short.endswith(ending):
            add(short, ending)
    add(short, "us")
    add(short, "dev")
    add(short, "tech")
    if stem != short and len(stem) >= 5 and is_coined(stem_tokens):
        add(stem, "ai")
        add(stem, "io")
        add(stem, "co")
    return out[:limit] if limit else out


def registrable(host: str) -> str:
    """The registrable domain of a host, without a public-suffix list: the
    last two labels, or three under the common two-level country suffixes."""
    parts = host.lower().strip(".").split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in _TWO_LEVEL_SUFFIXES:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def domain_match(host: str, name: str) -> str | None:
    """How a host relates to a company name: "exact", "partial" or None.

    exact:   the registrable label is the name ("swarmdefense.com"), or label
             plus ending spell it ("alva.energy", "hyl.io").
    partial: the label contains the name or its stem ("flyasylon.com" for
             Asylon, "censystech.com" for Censys Technologies). Stems under
             four letters never count.
    """
    tokens = name_tokens(name)
    if not tokens or not host:
        return None
    reg = registrable(host.lower().removeprefix("www."))
    label = reg.split(".")[0].replace("-", "")
    joined = reg.replace(".", "").replace("-", "")
    short_tokens, stem_tokens = _split_name(tokens)
    full, short, stem = "".join(tokens), "".join(short_tokens), "".join(stem_tokens)
    if label in (full, short) or joined in (full, short):
        return "exact"
    for piece in (short, stem):
        if len(piece) >= 4 and piece in label:
            return "partial"
    return None


def suggestion_candidates(rows: Any, name: str) -> list[tuple[str, str]]:
    """(host, listed name) pairs from an autocomplete answer that could be this company.

    A row is kept when its listed name contains the company's name as whole
    words, or its domain is exactly the name. Everything else the
    autocomplete returns is a lookalike ("World Airport Codes" for World AI).
    """
    forms = name_forms(name)
    out: list[tuple[str, str]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        host = clean_domain(row.get("domain") if isinstance(row.get("domain"), str) else None)
        listed = row.get("name") if isinstance(row.get("name"), str) else ""
        if not host or any(host == h for h, _ in out):
            continue
        if match_name(listed, forms) or domain_match(host, name) == "exact":
            out.append((host, listed))
        if len(out) >= SUGGEST_KEEP:
            break
    return out


# ---------------------------------------------------------------- the page

@dataclass
class Page:
    title: str = ""
    site_name: str = ""
    og_title: str = ""
    h1: str = ""
    description: str = ""
    og_description: str = ""
    text: str = ""
    hrefs: list[str] = field(default_factory=list)
    srcs: list[str] = field(default_factory=list)
    schema_names: list[str] = field(default_factory=list)
    refresh: str | None = None
    blocks: list[str] = field(default_factory=list)   # text of each <p>, <h1>, <h2>, <h3>, in order


_SCHEMA_ORG_TYPES = {"organization", "corporation", "localbusiness", "website", "brand"}


def _schema_names(node: Any, out: list[str], depth: int = 0) -> None:
    if depth > 6:
        return
    if isinstance(node, list):
        for item in node[:50]:
            _schema_names(item, out, depth + 1)
    elif isinstance(node, dict):
        kinds = node.get("@type")
        kinds = kinds if isinstance(kinds, list) else [kinds]
        if any(isinstance(k, str) and k.lower() in _SCHEMA_ORG_TYPES for k in kinds):
            for key in ("name", "legalName", "alternateName"):
                value = node.get(key)
                if isinstance(value, str) and value.strip() and value.strip() not in out:
                    out.append(value.strip())
        for key in ("@graph", "publisher", "author", "mainEntity"):
            if key in node:
                _schema_names(node[key], out, depth + 1)


class _PageParser(HTMLParser):
    """One pass over a homepage: the slots a name can sit in, the visible
    text, and every link."""

    _SKIP = {"script", "style", "noscript", "template", "svg", "iframe"}
    _BLOCK = {"p", "h1", "h2", "h3"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.page = Page()
        self._skip = 0
        self._in_title = False
        self._title_done = False
        self._in_h1 = False
        self._h1_done = False
        self._in_ld = False
        self._ld: list[str] = []
        self._title: list[str] = []
        self._h1: list[str] = []
        self._text: list[str] = []
        self._text_len = 0
        self._block: list[str] | None = None

    def _close_block(self) -> None:
        if self._block is not None:
            text = _WS.sub(" ", "".join(self._block)).strip()
            if text and len(self.page.blocks) < 400:
                self.page.blocks.append(text)
            self._block = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        if tag == "meta":
            self._meta(a)
        elif tag == "title" and not self._skip and not self._title_done:
            self._in_title = True
        elif tag == "h1" and not self._skip and not self._h1_done:
            self._in_h1 = True
        elif tag == "a" and a.get("href"):
            self.page.hrefs.append(a["href"].strip())
        elif tag in ("iframe", "script") and a.get("src"):
            self.page.srcs.append(a["src"].strip())
        if tag == "script" and "ld+json" in a.get("type", "").lower():
            self._in_ld = True
            self._ld = []
        if tag in self._SKIP:
            self._skip += 1
        elif not self._skip:
            self._text.append(" ")
            if tag in self._BLOCK:
                self._close_block()      # an unclosed <p> ends where the next block starts
                self._block = []
            elif self._block is not None:
                self._block.append(" ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # A self-closed tag opens nothing: do not let "<svg/>" start a skip.
        if tag == "meta":
            self._meta({k: (v or "") for k, v in attrs})
        elif tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.page.hrefs.append(href.strip())
        if not self._skip:
            self._text.append(" ")

    def _meta(self, a: dict[str, str]) -> None:
        key = (a.get("property") or a.get("name") or a.get("itemprop") or "").strip().lower()
        content = _WS.sub(" ", a.get("content", "")).strip()
        p = self.page
        if a.get("http-equiv", "").lower() == "refresh" and not self._skip and not p.refresh:
            m = re.match(r"\s*(\d+)\s*[;,]\s*(?:url\s*=\s*)?['\"]?([^'\"]+)", a.get("content", ""), re.I)
            if m and int(m.group(1)) <= 5:
                p.refresh = m.group(2).strip()
        if not content:
            return
        if key == "description" and not p.description:
            p.description = content
        elif key in ("og:description", "twitter:description") and not p.og_description:
            p.og_description = content
        elif key in ("og:site_name", "application-name") and not p.site_name:
            p.site_name = content
        elif key in ("og:title", "twitter:title") and not p.og_title:
            p.og_title = content

    def handle_endtag(self, tag: str) -> None:
        if tag == "title" and self._in_title:
            self._in_title = False
            self._title_done = True
        elif tag == "h1" and self._in_h1:
            self._in_h1 = False
            self._h1_done = bool("".join(self._h1).strip())
        elif tag == "script" and self._in_ld:
            self._in_ld = False
            try:
                _schema_names(json.loads("".join(self._ld)), self.page.schema_names)
            except (ValueError, RecursionError):
                pass
        if tag in self._SKIP:
            if self._skip:
                self._skip -= 1
        elif not self._skip:
            self._text.append(" ")
            if tag in self._BLOCK:
                self._close_block()
            elif self._block is not None:
                self._block.append(" ")

    def handle_data(self, data: str) -> None:
        if self._in_ld:
            self._ld.append(data)
            return
        if self._in_title:
            self._title.append(data)
            return
        if self._skip:
            return
        if self._in_h1:
            self._h1.append(data)
        if self._block is not None:
            self._block.append(data)
        if self._text_len < MAX_TEXT:
            self._text.append(data)
            self._text_len += len(data)

    def finish(self) -> Page:
        self._close_block()
        p = self.page
        p.title = _WS.sub(" ", "".join(self._title)).strip()
        p.h1 = _WS.sub(" ", "".join(self._h1)).strip()
        p.text = _WS.sub(" ", "".join(self._text)).strip()
        return p


def parse_page(html: str) -> Page:
    """Parse a homepage. Broken markup yields whatever was read before it broke."""
    parser = _PageParser()
    try:
        parser.feed((html or "")[:MAX_HTML])
        parser.close()
    except Exception:  # noqa: BLE001 - html.parser can raise on hostile input
        pass
    return parser.finish()


def copyright_lines(text: str) -> list[tuple[str, str]]:
    """The words just before and just after each copyright mark, where a
    site names its owner: ("Acme Robotics", "2026 Acme Robotics, Inc. All rights")."""
    out = []
    for m in _COPYRIGHT.finditer(text or ""):
        before = " ".join(text[max(0, m.start() - 80): m.start()].split()[-6:])
        after = " ".join(text[m.end(): m.end() + 110].split()[:10])
        out.append((before, after))
        if len(out) >= 6:
            break
    return out


_COPYRIGHT_NOISE = frozenset({"copyright", "c", "by", "the", "all", "rights", "reserved"})


def _is_year(token: str) -> bool:
    return token.isdigit() and len(token) == 4


def match_copyright(text: str, forms: list[NameForm]) -> NameForm | None:
    """The form of the company's name that a copyright line gives as the owner.

    The name has to stand right at the mark: "(c) 2026 Acme Robotics, Inc."
    or "Acme Robotics (c) 2026". A name that merely sits near the footer
    ("Our clients include Acme Robotics and others. (c) 2026 Hale LLP") is
    not the owner.
    """
    for before, after in copyright_lines(text):
        tokens = text_tokens(after)
        while tokens and (_is_year(tokens[0]) or tokens[0] in _COPYRIGHT_NOISE):
            tokens = tokens[1:]
        head = text_tokens(before)
        while head and (_is_year(head[-1]) or head[-1] in _LEGAL_TOKENS or head[-1] in _COPYRIGHT_NOISE):
            head = head[:-1]
        for form in forms:
            n = form.compact
            acc = ""
            for j, t in enumerate(tokens):
                acc += t
                if len(acc) >= len(n):
                    if acc == n and not (j + 1 < len(tokens) and tokens[j + 1] in _other_fillers(form)):
                        return form
                    break
            if any("".join(head[i:]) == n for i in range(len(head))):
                return form
    return None


def parked_reason(final_host: str, page: Page, html: str = "") -> str | None:
    """Why this page is a parked or for-sale domain, or None when it is not."""
    host = (final_host or "").lower().removeprefix("www.")
    for parked in PARKED_HOSTS:
        if host == parked or host.endswith("." + parked):
            return f"ends on {parked}"
    head = " ".join([page.title, page.description, page.og_description, page.text[:3000]])
    m = _PARKED_PHRASES.search(head)
    if m:
        return f'page says "{m.group(0).lower()}"'
    low = (html or "")[:60_000].lower()
    for marker in _PARKED_MARKUP:
        if marker in low:
            return f"parking markup ({marker})"
    return None


_ABBREVIATIONS = frozenset({
    "inc", "co", "corp", "ltd", "llc", "dr", "mr", "mrs", "ms", "st", "no", "vs", "jr", "sr", "gen",
    "lt", "col", "fig", "approx", "dept", "est",
})


def sentence_ends(s: str) -> list[int]:
    """Offsets just past each sentence end in a text.

    A full stop ends a sentence only when what follows starts a new one and
    what precedes is not an abbreviation: "to the U.S. Government" and
    "Acme, Inc. builds" are one sentence each.
    """
    out = []
    for m in re.finditer(r"[.!?]+(?=\s|$)", s):
        before = s[: m.start()].rsplit(None, 1)[-1] if s[: m.start()].strip() else ""
        rest = s[m.end():].lstrip()
        if s[m.start()] == ".":
            word = before.strip("(\"'\u201c").lower()
            if word in _ABBREVIATIONS or re.fullmatch(r"(?:[a-z]\.)*[a-z]", word):
                continue        # "Inc.", "U.S.", a lone initial
        if rest and rest[0].islower():
            continue
        out.append(m.end())
    return out


def _trim_liner(s: str) -> str:
    """Bring a description to one-liner size.

    A dangling tail of a few words with no full stop (a site's own truncated
    excerpt) is dropped. A text over ONE_LINER_CHARS is cut at a sentence end
    when there is one, otherwise at a word, with an ellipsis.
    """
    s = re.sub(r"\s+(?=[\u00ae\u2122])", "", s)       # "blueflite (R) offers" as the page spaces it
    ends = sentence_ends(s)
    if ends and ends[-1] < len(s) and ends[-1] >= 60:
        # Text after the last full stop that never reaches one of its own. A
        # few words, or a total length where site builders cut their
        # automatic excerpts, means the sentence was cut off, not written so.
        if len(s[ends[-1]:].split()) <= 3 or 150 <= len(s) <= 165:
            s = s[: ends[-1]].strip()
    if len(s) <= ONE_LINER_CHARS:
        return s
    within = [e for e in ends if e <= ONE_LINER_CHARS]
    if within and within[-1] >= 60:
        return s[: within[-1]].strip()
    cut = s[: ONE_LINER_CHARS - 1]
    if " " in cut:
        cut = cut[: cut.rindex(" ")]
    return cut.rstrip(" ,;:-\u2013\u2014") + "\u2026"


def clean_description(page: Page, name: str = "") -> str | None:
    """The site's own one-sentence description, or None when it has none.

    Takes the meta description (og:description as the fallback) only when it
    reads as a sentence about the company: at least five words, not a cookie
    notice, a placeholder or a bare "Home". Trimmed to 200 characters at a
    sentence end when there is one, otherwise at a word, with an ellipsis.
    """
    for raw in (page.description, page.og_description):
        s = _WS.sub(" ", raw or "").strip().strip('"')
        if len(s) < 30 or sum(1 for w in s.split() if any(c.isalpha() for c in w)) < 5:
            continue
        if _BOILERPLATE_DESCRIPTION.search(s):
            continue
        if text_tokens(s) == text_tokens(page.title) or text_tokens(s) == text_tokens(name):
            continue
        return _trim_liner(s)
    return None


_NOT_PROSE = re.compile(
    r"©|\bcopyright\b|all rights reserved|subscribe|newsletter|sign up|skip to|read more|learn more"
    r"|click here|terms of|\bmenu\b|@|https?://|\bloading\b|\d{4}-\d{2}-\d{2}|your (?:request|details|email)"
    r"|press inquiries|get in touch|contact us"
    r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.? \d{1,2}, \d{4}", re.I)


def _prose(s: str, page: Page, name: str, min_lower: float = 0.5) -> bool:
    """Whether a block of page text reads as a sentence and not as a menu, a
    slogan in fragments, a footer, a form or a news list."""
    words = s.split()
    if len(words) < 8 or len(s) < 50 or len(s) > 1200:
        return False
    if _BOILERPLATE_DESCRIPTION.search(s) or _NOT_PROSE.search(s):
        return False
    if sum(1 for w in words if w[:1].islower()) < max(2, min_lower * len(words)):
        return False
    ends = sentence_ends(s)
    if not ends and len(words) < 12:
        return False
    if ends and len(s[: ends[0]].split()) < 5 and len(words) < 14:
        return False                    # "Make rain. Make snow."
    return text_tokens(s) != text_tokens(page.title) and text_tokens(s) != text_tokens(name)


def _self_names(name: str) -> list[str]:
    """Compact spellings by which a company refers to itself in running text:
    its name, the name without filler words and, when that word is made up
    or more than one word, the name without its sector word ("Fortem" for
    Fortem Technologies, never "Last" for Last Energy)."""
    tokens = name_tokens(name)
    if not tokens:
        return []
    short, stem = _split_name(tokens)
    out = ["".join(tokens), "".join(short)]
    if len("".join(stem)) >= 4 and (len(stem) > 1 or is_coined(stem)):
        out.append("".join(stem))
    return list(dict.fromkeys(out))


_LEAD_IN = frozenset({"founded", "in", "since", "at", "the", "today"})
_SPEAKER = re.compile(r"\b(?:we|our|us|my|i)\b", re.I)


def _opens_with_name(sentence: str, names: list[str]) -> bool:
    """Whether a sentence starts with the company's name, allowing a short
    lead-in ("Founded in 2018, blueflite offers ...")."""
    tokens = text_tokens(" ".join(sentence.split()[:8]))
    for skip in range(0, 4):
        if skip and (tokens[skip - 1] not in _LEAD_IN and not tokens[skip - 1].isdigit()):
            break
        for n in names:
            acc = ""
            for t in tokens[skip:]:
                acc += t
                if len(acc) >= len(n):
                    break
            if acc == n:
                return True
        if skip >= len(tokens):
            break
    return False


def hero_sentence(page: Page, name: str = "") -> str | None:
    """The first real sentence of the page's opening text, for a site with no
    usable meta description.

    Looks only at the first paragraphs and headings. A paragraph that opens
    with the company's own name in the third person ("Aerovec is developing
    wind technology ...") is taken first, because the first prose on a page
    is often the problem the company solves rather than what it is. Without
    one, the first paragraph that reads as prose is taken. A customer's
    praise ("Acme built us a ...") is the customer's words and is passed over.
    """
    blocks = [_WS.sub(" ", b).strip().strip('"\u201c\u201d') for b in page.blocks[:HERO_BLOCKS]]
    names = _self_names(name)
    for block in blocks:
        ends = sentence_ends(block)
        first = block[: ends[0]] if ends else block
        if (len(first.split()) >= 6 and _opens_with_name(first, names) and not _SPEAKER.search(first)
                and _prose(block, page, name, min_lower=0.2)):
            return _trim_liner(block)
    for block in blocks:
        if _prose(block, page, name):
            return _trim_liner(block)
    return None


def page_one_liner(page: Page, name: str = "") -> str | None:
    """Meta description, then og:description, then the first real sentence."""
    return clean_description(page, name) or hero_sentence(page, name)


def _resembles(handle: str, name: str, domain: str | None) -> bool:
    """Whether a login or slug plausibly belongs to this company."""
    h = re.sub(r"[^a-z0-9]", "", urllib.parse.unquote(handle).lower())
    if len(h) < 3:
        return False
    tokens = name_tokens(name)
    full = "".join(tokens)
    short_tokens, stem_tokens = _split_name(tokens)
    label = registrable(domain).split(".")[0].replace("-", "") if domain else ""
    for piece in {full, "".join(short_tokens), "".join(stem_tokens), label}:
        if len(piece) >= 4 and (piece in h or (len(h) >= 4 and h in piece)):
            return True
    return False


def extract_links(page: Page, name: str, domain: str | None = None) -> dict[str, Any]:
    """Identity links on a homepage: {"github", "ats": (provider, slug), "x", "linkedin"}.

    A GitHub login is a hard key for the resolver, so it is only taken when it
    resembles the company's name or domain: a homepage also links to the
    libraries it uses. The other three are taken when the page links to
    exactly one, or to one that resembles the name.
    """
    out: dict[str, Any] = {}

    def pick(found: list[str]) -> str | None:
        distinct = list(dict.fromkeys(found))
        alike = [f for f in distinct if _resembles(f, name, domain)]
        if alike:
            return alike[0]
        return distinct[0] if len(distinct) == 1 else None

    githubs, xs, linkedins = [], [], []
    ats: list[tuple[str, str]] = []
    for href in page.hrefs:
        if href.startswith("//"):
            href = "https:" + href
        m = _GITHUB_LINK.match(href)
        if m and m.group(1).lower() not in _GITHUB_RESERVED:
            githubs.append(m.group(1))
        m = _X_LINK.match(href)
        if m and m.group(1).lower() not in _SOCIAL_RESERVED:
            xs.append(m.group(1))
        m = _LINKEDIN_LINK.match(href)
        if m and m.group(1).lower() not in _SOCIAL_RESERVED:
            linkedins.append(urllib.parse.unquote(m.group(1)).strip("/"))
    for link in [*page.hrefs, *page.srcs]:
        if link.startswith("//"):
            link = "https:" + link
        for provider, pattern in _ATS_LINKS:
            m = pattern.match(link)
            if m:
                slug = urllib.parse.unquote(m.group(1))
                if _ATS_SLUG_OK.match(slug) and slug.lower() not in ("embed", "jobs", "careers"):
                    ats.append((provider, slug))
                break

    github = next((g for g in dict.fromkeys(githubs) if _resembles(g, name, domain)), None)
    if github:
        out["github"] = github.lower()
    distinct_ats = list(dict.fromkeys(ats))
    alike_ats = [p for p in distinct_ats if _resembles(p[1], name, domain)]
    if alike_ats:
        out["ats"] = alike_ats[0]
    elif len(distinct_ats) == 1:
        out["ats"] = distinct_ats[0]
    x = pick(xs)
    if x:
        out["x"] = f"https://x.com/{x}"
    linkedin = pick(linkedins)
    if linkedin:
        out["linkedin"] = f"https://www.linkedin.com/company/{linkedin}"
    return out


# ---------------------------------------------------------------- verification

@dataclass
class Verdict:
    host: str                      # the candidate as tried
    source: str                    # "clearbit" or "guess"
    accepted: bool = False
    reason: str = ""               # why not, when not accepted
    refused: bool = False          # the site answered with an HTTP error status
    domain: str | None = None      # the verified domain, from the final URL
    final_url: str | None = None
    confidence: float = 0.0
    checks: list[str] = field(default_factory=list)
    page: Page | None = None
    page_fit: float = 0.0
    page_sector: str = "other"
    shared_terms: list[str] = field(default_factory=list)
    shared_words: list[str] = field(default_factory=list)
    reached_page: bool = False     # a real page was read (worth logging as a near miss)
    wants_fact: bool = False       # everything held except the second fact
    careers: Page | None = None    # the careers page, when it was read for that fact


def _blank_name(text: str, forms: list[NameForm]) -> str:
    """Remove the company's own name from text before classifying it."""
    for form in forms:
        pattern = r"[\W_]*".join(re.escape(t) for t in form.tokens)
        text = re.sub(rf"(?<![a-z0-9]){pattern}(?![a-z0-9])", " ", text, flags=re.I)
    return text


def _stem(word: str) -> str:
    return word[:5]


def _echoes_name(term: str, tokens: Iterable[str]) -> bool:
    """Whether a thesis term only repeats the company's name: "robot" for
    "Aura Robotics", "energy" for "Alva Energy". "energy storage" does not."""
    stems = {_stem(t) for t in tokens if len(t) >= 4}
    words = text_tokens(term)
    return bool(words) and all(_stem(w) in stems for w in words)


def entity_text(entity: dict) -> str:
    return " ".join(str(entity.get(k) or "") for k in ("one_liner", "description")).strip()


def shared_terms(entity: dict, page_blob: str) -> list[str]:
    """Thesis terms in both the entity's own description and the page that
    the company's name does not supply. One per word stem, so "battery" and
    "batteries" count once."""
    own = entity_text(entity)
    if not own:
        return []
    tokens = name_tokens(str(entity.get("name") or ""))
    out: dict[tuple[str, ...], str] = {}
    for term in classify(own)["terms"]:
        if _echoes_name(term, tokens) or not term_pattern(term).search(page_blob):
            continue
        out.setdefault(tuple(_stem(w) for w in text_tokens(term)), term)
    return list(out.values())


def shared_words(entity: dict, page_tokens: set[str]) -> tuple[list[str], int]:
    """Content words of the entity's description that the page also uses.

    Returns (shared words, how many content words the description has).
    Filing boilerplate, generic sector words and the name itself are left out.
    """
    name_stems = {_stem(t) for t in name_tokens(str(entity.get("name") or ""))}
    own = []
    for w in text_tokens(entity_text(entity)):
        if len(w) < 4 or w in _WORD_STOP or w in FILLER_WORDS or w in SECTOR_WORDS:
            continue
        if _stem(w) in name_stems or w in own:
            continue
        own.append(w)
    hit = [w for w in own if w in page_tokens or w + "s" in page_tokens
           or (w.endswith("s") and w[:-1] in page_tokens)]
    return hit, len(own)


_BASED_IN = re.compile(
    r"\b(?:based|headquartered|located|registered|incorporated)\s+in\s+([^.;:!?|]{3,60})", re.I)
_FOREIGN_PLACES = re.compile(
    r"\b(?:kazakhstan|india|china|canada|germany|france|israel|japan|korea|united kingdom|england|"
    r"scotland|australia|netherlands|sweden|norway|finland|denmark|switzerland|austria|italy|spain|"
    r"poland|ukraine|turkey|estonia|latvia|lithuania|singapore|taiwan|brazil|mexico|ireland|"
    r"belgium|portugal|czech|hungary|romania|greece|pakistan|vietnam|indonesia|nigeria|"
    r"south africa|new zealand|uae|united arab emirates|saudi arabia|hong kong)\b", re.I)


def has_known_accounts(entity: dict) -> bool:
    """Whether the entity is already known by a GitHub login, a job board or a social page."""
    links = entity.get("links") if isinstance(entity.get("links"), dict) else {}
    return bool(entity.get("github") or entity.get("ats") or any(links.get(k) for k in ("x", "twitter", "linkedin")))


def known_link_on_page(entity: dict, page: Page | None) -> bool:
    """Whether a page links to an account the entity is already known by:
    its GitHub login, a job board recorded for it, or its X or LinkedIn page."""
    if page is None:
        return False
    hrefs = " ".join([*page.hrefs, *page.srcs]).lower()
    login = entity.get("github")
    if isinstance(login, str) and login.strip():
        if re.search(rf"github\.com/{re.escape(login.strip().lower())}(?![a-z0-9-])", hrefs):
            return True
    for pair in entity.get("ats") if isinstance(entity.get("ats"), list) else []:
        if isinstance(pair, dict) and pair.get("slug"):
            slug = re.escape(urllib.parse.quote(str(pair["slug"]).lower()))
            if re.search(rf"(?:ashbyhq\.com|greenhouse\.io|lever\.co)/(?:\S*for=)?{slug}(?![a-z0-9._-])", hrefs):
                return True
    links = entity.get("links") if isinstance(entity.get("links"), dict) else {}
    for key in ("x", "twitter", "linkedin"):
        url = links.get(key)
        if isinstance(url, str) and "/" in url:
            tail = re.sub(r"^https?://(?:www\.)?(?:twitter\.com|x\.com|linkedin\.com/company)/", "", url.lower()).strip("/")
            if len(tail) >= 3 and re.search(
                    rf"(?:twitter\.com|x\.com|linkedin\.com/company)/{re.escape(tail)}(?![a-z0-9_-])", hrefs):
                return True
    return False


_CAREERS_PATH = re.compile(r"/(?:careers?|jobs|join(?:-us)?|work-with-us|open-roles|open-positions|hiring)(?:[/?#.]|$)", re.I)


def careers_url(page: Page, final_url: str) -> str | None:
    """The site's own careers page, if the homepage links to one."""
    home = registrable(urllib.parse.urlparse(final_url).netloc.lower().removeprefix("www."))
    for href in page.hrefs:
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        url = urllib.parse.urljoin(final_url, href)
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https") or not _CAREERS_PATH.search(parsed.path):
            continue
        if registrable(parsed.netloc.lower().removeprefix("www.")) == home:
            return url.split("#")[0]
    return None


def location_on_page(location: Any, text: str) -> tuple[bool, str]:
    """(the entity's city is on the page, why the page contradicts the entity's location).

    Only used when the known entity carries a location. A filer in Atlanta,
    Georgia is not the company whose footer reads "Marshfield, WI 54449", and
    a filer in Brooklyn is not the one "based in Astana, Kazakhstan".
    """
    if not isinstance(location, str) or not location.strip():
        return False, ""
    parts = [p.strip() for p in location.split(",") if p.strip()]
    city = parts[0] if len(parts) >= 2 else ""
    region = parts[-1]
    city_seen = bool(len(city) >= 4 and re.search(rf"(?<![A-Za-z]){re.escape(city)}(?![A-Za-z])", text, re.I))
    state = _US_STATES.get(region.lower()) or (region.upper() if region.upper() in _US_STATES.values() else None)
    if not state or city_seen:
        return city_seen, ""
    seen = {s for s in re.findall(r",\s*([A-Z]{2})\.?\s+\d{5}\b", text) if s in _US_STATES.values()}
    if seen and state not in seen:
        return False, f"page gives a US address in {', '.join(sorted(seen))}, the entity is in {state}"
    for m in _BASED_IN.finditer(text):
        # Only the place itself: stop at the first word that starts a new clause.
        place = re.split(r"\s+(?:and|to|with|for|where|while|but|we|our)\s+", m.group(1), maxsplit=1)[0][:40]
        abroad = _FOREIGN_PLACES.search(place)
        if abroad and not any(re.search(rf"\b{re.escape(us)}\b", place, re.I) for us in _US_STATES):
            return False, f"page says it is based in {abroad.group(0).title()}, the entity is in {state}"
    return False, ""


def name_slots(page: Page, forms: list[NameForm]) -> dict[str, tuple[NameForm, bool]]:
    """Where on a page the company's name stands as a name.

    Keys are the slots (title, site_name, og_title, h1, schema_org,
    copyright); values are the form found there and whether the slot, or a
    segment of it, is exactly the name.
    """
    slots = [("title", page.title), ("site_name", page.site_name), ("og_title", page.og_title), ("h1", page.h1)]
    slots += [("schema_org", s) for s in page.schema_names[:4]]
    found: dict[str, tuple[NameForm, bool]] = {}
    for slot, text in slots:
        hit = match_name(text, forms)
        if hit and (slot not in found or (hit[1] and not found[slot][1])
                    or (hit[0].label == "full" and found[slot][0].label != "full")):
            found[slot] = hit
    owner = match_copyright(page.text, forms)
    if owner:
        found["copyright"] = (owner, False)
    return found


def verify_page(entity: dict, host: str, source: str, final_url: str, page: Page,
                html: str = "", words: frozenset[str] | None = None, careers: Page | None = None) -> Verdict:
    """Decide whether a fetched homepage is this entity's own website.

    Pure: everything it needs is passed in. See the module docstring for the
    rules; `checks` records each one that passed and `reason` the first that
    did not. `careers` is the site's careers page, when it was read to look
    for a job board the entity is already known by.
    """
    name = str(entity.get("name") or "")
    v = Verdict(host=host, source=source, final_url=final_url, page=page, careers=careers)
    forms = name_forms(name, words)
    if not forms:
        v.reason = "name too short to verify"
        return v

    # 1. Its own domain, not parked.
    final_host = urllib.parse.urlparse(final_url).netloc.split("@")[-1].split(":")[0].lower()
    parked = parked_reason(final_host, page, html)
    if parked:
        v.reason = f"parked: {parked}"
        return v
    own = clean_domain(final_host)
    if not own:
        v.reason = f"ends on {final_host or 'no host'}, which is not a company domain"
        return v
    relation = domain_match(own, name)
    if own != registrable(own):
        # A subdomain. Ours only when the parent carries the company's name;
        # otherwise it is a page on someone else's platform.
        if not relation:
            v.reason = f"ends on {own}, a subdomain of an unrelated site"
            return v
        own = registrable(own)
    v.domain = own
    v.reached_page = True
    if final_host.removeprefix("www.") != host.lower().removeprefix("www."):
        v.checks.append("redirect_followed")

    # 2. The name, as a name.
    found = name_slots(page, forms)
    if not found:
        v.reason = f"name not on the page as a name (title {page.title[:60]!r})"
        return v
    identity_slots = ("title", "site_name", "schema_org")
    exact_identity = [found[s][0] for s in identity_slots if s in found and found[s][1]]
    in_identity = any(s in found for s in (*identity_slots, "og_title", "h1"))
    full_seen = any(f.label == "full" for f, _ in found.values())
    best_form = forms[0] if full_seen else next(iter(found.values()))[0]
    v.checks += [f"name_in_{s}" for s in found]
    if full_seen:
        v.checks.append("name_full")

    # 3. The same kind of business, judged with the name blanked out.
    description = page.description or page.og_description
    blob = _blank_name(" \n ".join([page.title, description, page.text[:CLASSIFY_CHARS]]), forms)
    c = classify(blob)
    v.page_fit, v.page_sector = c["fit"], c["sector"]
    if c["fit"] < 0.3:
        v.reason = f"page is not on thesis (fit {c['fit']})"
        return v
    v.checks.append("thesis_fit")
    sector = entity.get("sector")
    try:
        entity_fit = float(entity.get("fit") or 0.0)
    except (TypeError, ValueError):
        entity_fit = 0.0
    if isinstance(sector, str) and sector not in ("", "other") and entity_fit >= 0.3:
        if not c["sectors"].get(sector):
            v.reason = f"page reads as {c['sector']}, nothing on it about {sector}"
            return v
        v.checks.append("sector_agrees")

    # 4. A second fact that ties the page to this entity and not a namesake.
    v.shared_terms = shared_terms(entity, blob)
    page_tokens = set(text_tokens(" ".join([page.title, description, page.text[:WORDS_CHARS]])))
    v.shared_words, own_words = shared_words(entity, page_tokens)
    words_agree = len(v.shared_words) >= 2 and len(v.shared_words) >= 0.6 * own_words
    city_seen, elsewhere = location_on_page(entity.get("location"), page.text)
    if elsewhere:
        v.reason = elsewhere
        return v
    second: list[str] = []
    if city_seen:
        second.append("location_on_page")
    if known_link_on_page(entity, page):
        second.append("known_link_on_page")
    elif known_link_on_page(entity, careers):
        second.append("known_link_on_careers_page")
    if len(v.shared_terms) >= 2:
        second.append("shared_terms")
    if words_agree:
        second.append("shared_words")
    v.checks += second
    if relation:
        v.checks.append(f"domain_{relation}")

    # 5. Confidence.
    conf = 0.40
    conf += 0.20 if exact_identity else (0.10 if in_identity else 0.0)
    conf += 0.10 if len(found) >= 2 else 0.0
    conf += 0.10 if "copyright" in found else 0.0
    conf += {"exact": 0.20, "partial": 0.10}.get(relation or "", 0.0)
    conf += 0.05 if full_seen else 0.0
    conf += min(0.20, 0.15 * bool(city_seen) + 0.15 * (len(v.shared_terms) >= 2)
                + 0.08 * (len(v.shared_terms) == 1) + 0.10 * words_agree)
    v.confidence = round(min(conf, 1.0), 2)

    # 6. How much the name itself proves decides how much more is asked.
    tokens = best_form.tokens
    if best_form.weak:
        v.checks.append("name_weak")
        if relation != "exact":
            v.reason = "weak name and the domain is not the name"
        elif not second:
            v.reason, v.wants_fact = "weak name with no second corroborating fact", True
        elif v.confidence < ACCEPT_CONFIDENCE_WEAK:
            v.reason = f"weak name, confidence {v.confidence} under {ACCEPT_CONFIDENCE_WEAK}"
    elif is_coined(tokens, words):
        v.checks.append("name_coined")
        summary = _blank_name(" \n ".join([page.title, page.og_title, description, page.h1]), forms)
        # "Hadron Energy" says energy by itself, so an energy page proves
        # nothing beyond the name. "Orkora" on a nuclear page does.
        told_by_name = "sector_agrees" not in v.checks or bool(classify(name)["sectors"].get(sector))
        if not told_by_name:
            v.checks.append("sector_not_in_name")
        if len(tokens) == 1 and not relation:
            v.reason = "one-word name on a domain that does not carry it"
        elif told_by_name and not second:
            v.reason = "the name itself states the sector, and no second fact ties the page to this entity"
            v.wants_fact = True
        elif len(description.split()) >= 8 and classify(summary)["fit"] == 0 and not second:
            v.reason = "the page's own summary is off thesis and nothing else ties it to the entity"
            v.wants_fact = True
    else:
        v.checks.append("name_plain")
        long_identity = relation and any(len(f.tokens) >= 3 for f in exact_identity)
        if long_identity:
            v.checks.append("long_name_is_site_name")
        elif not second:
            v.reason = "plain-word name that other companies share, and no second fact ties the page to this one"
            v.wants_fact = True
    if not v.reason and v.confidence < ACCEPT_CONFIDENCE:
        v.reason = f"confidence {v.confidence} under {ACCEPT_CONFIDENCE}"
    v.accepted = not v.reason
    return v


def choose(verdicts: list[Verdict]) -> tuple[Verdict | None, str]:
    """Pick the one verified site, or none when two different sites both pass.

    Two hosts that end on the same domain are one site, and two domains
    showing the same title are one company with two addresses: the first
    tried wins. Two different sites that both pass mean the name is shared,
    and neither is recorded.
    """
    accepted: list[Verdict] = []
    for v in verdicts:
        if v.accepted and all(v.domain != a.domain for a in accepted):
            accepted.append(v)
    if not accepted:
        return None, ""
    first = accepted[0]
    title = text_tokens(first.page.title if first.page else "")
    rivals = [a for a in accepted[1:] if text_tokens(a.page.title if a.page else "") != title]
    if not rivals:
        return first, ""
    return None, "ambiguous: " + " and ".join(a.domain or "?" for a in [first, *rivals]) + " all pass"


# ---------------------------------------------------------------- network

_tls = threading.local()
_hook_lock = threading.Lock()
_hook_installed = False


class _FinalUrlTagger(urllib.request.BaseHandler):
    """Copies the URL a response was finally served from into a header.

    antenna.http follows redirects and returns body and headers but not the
    final URL, and this collector must record the host a homepage ends on.
    The tag is added only on the thread that is fetching a homepage for this
    collector, so every other request in the process is left exactly as it was.
    """

    def http_response(self, request: Any, response: Any) -> Any:
        if getattr(_tls, "tag", False):
            try:
                del response.headers[FINAL_URL_HEADER]
                response.headers[FINAL_URL_HEADER] = response.geturl()
            except Exception:  # noqa: BLE001 - never break a response over a tag
                pass
        return response

    https_response = http_response


def _install_final_url_hook() -> None:
    global _hook_installed
    with _hook_lock:
        if _hook_installed:
            return
        existing = getattr(urllib.request, "_opener", None)
        if existing is not None:
            existing.add_handler(_FinalUrlTagger())
        else:
            urllib.request.install_opener(urllib.request.build_opener(_FinalUrlTagger()))
        _hook_installed = True


def _get_homepage(url: str) -> tuple[str, str]:
    """(final URL, html) for one URL, through antenna.http."""
    _tls.tag = True
    try:
        html, headers = http.request(
            url, headers={"Accept": HOMEPAGE_ACCEPT}, ttl=HOMEPAGE_TTL,
            timeout=HOMEPAGE_TIMEOUT, retries=0, return_headers=True,
        )
    finally:
        _tls.tag = False
    return headers.get(FINAL_URL_HEADER) or url, html


def _is_timeout(e: Exception) -> bool:
    return isinstance(e, TimeoutError) or "timed out" in str(e).lower()


def fetch_homepage(host: str) -> tuple[str, str]:
    """Fetch a host's homepage, trying https, then www, then plain http.

    One retry at most, and only where a retry can help: after a 5xx or 429.
    Any other HTTP status is the site's answer (a 403 is a refusal we
    respect) and is raised at once. A timeout means nothing is listening, so
    the other spellings are not tried: measured on the test set, a dead host
    otherwise held a worker for 50 seconds. A TLS or connection error moves
    on to the next spelling. One meta refresh is followed.
    """
    last: Exception | None = None
    urls = [f"https://{host}/"]
    if not host.startswith("www."):
        urls.append(f"https://www.{host}/")
    urls.append(f"http://{host}/")
    retried = False
    i = 0
    while i < len(urls):
        url = urls[i]
        try:
            final_url, html = _get_homepage(url)
        except http.HttpError as e:
            if (e.status >= 500 or e.status == 429) and not retried:
                retried = True
                time.sleep(RETRY_PAUSE)
                continue
            raise
        except Exception as e:  # noqa: BLE001 - DNS, TLS, reset, timeout
            last = e
            if _is_timeout(e):
                break
            i += 1
            continue
        page = parse_page(html[:200_000]) if "http-equiv" in html[:200_000].lower() else Page()
        if page.refresh and len(page.text) < 400:
            target = urllib.parse.urljoin(final_url, page.refresh)
            if target.startswith("http") and target.rstrip("/") != final_url.rstrip("/"):
                try:
                    return _get_homepage(target)
                except Exception:  # noqa: BLE001 - keep the page we have
                    pass
        return final_url, html
    assert last is not None
    raise last


def host_exists(host: str) -> bool | None:
    """Whether a host has an address, by DNS over HTTPS. None when the resolver failed."""
    for name in (host, f"www.{host}"):
        base = RESOLVERS[zlib.crc32(name.encode()) % len(RESOLVERS)]
        try:
            data = http.get_json(base, params={"name": name, "type": "A"},
                                 headers={"Accept": "application/dns-json"},
                                 ttl=DNS_TTL, timeout=10, retries=1)
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(data, dict):
            return None
        if any(isinstance(a, dict) and a.get("type") == 1 for a in data.get("Answer") or []):
            return True
        if data.get("Status") != 0:
            return False
        # Status 0 with no address: the domain exists but the bare name has no
        # A record. Some sites only answer on www.
    return False


def suggest(name: str) -> list[tuple[str, str]]:
    rows = http.get_json(SUGGEST_URL, params={"query": strip_legal(name)},
                         ttl=SUGGEST_TTL, timeout=10, retries=1)
    return suggestion_candidates(rows, name)


def verify_host(entity: dict, host: str, source: str) -> Verdict:
    try:
        final_url, html = fetch_homepage(host)
    except http.HttpError as e:
        return Verdict(host=host, source=source, reason=f"homepage answered HTTP {e.status}", refused=True)
    except Exception as e:  # noqa: BLE001
        return Verdict(host=host, source=source, reason=f"homepage did not load ({type(e).__name__})")
    page = parse_page(html)
    v = verify_page(entity, host, source, final_url, page, html)
    if v.wants_fact and has_known_accounts(entity):
        # Everything held but the second fact, and the entity is known by a
        # job board or an account. The careers page is where a site links to
        # its board, so read that one page more.
        url = careers_url(page, final_url)
        if url:
            try:
                careers = parse_page(_get_homepage(url)[1])
            except Exception:  # noqa: BLE001 - the verdict stands as it was
                return v
            again = verify_page(entity, host, source, final_url, page, html, careers=careers)
            if again.accepted:
                return again
    return v


@dataclass
class Outcome:
    entity: dict
    chosen: Verdict | None = None
    verdicts: list[Verdict] = field(default_factory=list)
    note: str = ""
    lookups: int = 0
    dns_failures: int = 0
    error: str | None = None


def identify(entity: dict) -> Outcome:
    """Everything for one company: candidates, DNS, fetch, verify, choose."""
    out = Outcome(entity=entity)
    name = str(entity.get("name") or "")
    forms = name_forms(name)
    if not forms:
        out.note = "name too short to verify"
        return out
    if all(f.weak for f in forms) and not entity_text(entity) and not entity.get("location"):
        out.note = "weak name and nothing to corroborate it with"
        return out

    tried: set[str] = set()
    try:
        listed = suggest(name)
    except Exception as e:  # noqa: BLE001 - the autocomplete is optional
        listed = []
        out.note = f"autocomplete failed ({type(e).__name__})"
    for host, _listed_name in listed:
        if host not in tried:
            tried.add(host)
            out.verdicts.append(verify_host(entity, host, "clearbit"))

    sector = entity.get("sector") if isinstance(entity.get("sector"), str) else None
    # Every guess is looked at even after one verifies: the only way to learn
    # that a name is shared is to find the second site.
    for host in guess_hosts(name, sector):
        if host in tried or any(v.domain == host for v in out.verdicts):
            continue
        tried.add(host)
        out.lookups += 1
        exists = host_exists(host)
        if exists is None:
            out.dns_failures += 1
        if not exists:
            continue
        out.verdicts.append(verify_host(entity, host, "guess"))

    out.chosen, why = choose(out.verdicts)
    if why:
        out.note = why
    return out


def _links_and_text(page: Page, careers: Page | None, name: str, domain: str | None) -> tuple[dict[str, Any], str]:
    """What both kinds of signal carry from a page: its identity links and
    the text handed to the thesis classifier."""
    linked = page
    if careers is not None:
        linked = Page(hrefs=[*page.hrefs, *careers.hrefs], srcs=[*page.srcs, *careers.srcs])
    description = page.description or page.og_description
    text = " \n ".join(filter(None, [page.title, description, page.text[:SIGNAL_TEXT_CHARS]]))
    return extract_links(linked, name, domain), text


def to_signal(entity: dict, v: Verdict, today_iso: str) -> Signal:
    page = v.page or Page()
    name = str(entity["name"])
    links_found, text = _links_and_text(page, v.careers, name, v.domain)
    metrics: dict[str, Any] = {
        "observed_only": True,
        "confidence": v.confidence,
        "checks": list(v.checks),
        "final_url": v.final_url,
        "candidate_source": v.source,
        "page_fit": v.page_fit,
        "page_sector": v.page_sector,
    }
    if v.shared_terms:
        metrics["shared_terms"] = v.shared_terms[:8]
    if v.shared_words:
        metrics["shared_words"] = v.shared_words[:8]
    if "ats" in links_found:
        metrics["ats_provider"], metrics["ats_slug"] = links_found["ats"]
    return Signal(
        source=SLUG, family=FAMILY, kind="website_verified",
        entity=EntityHint(
            name=name, kind="company", domain=v.domain,
            github=links_found.get("github"),
            one_liner=clean_description(page, name),
            links={k: links_found[k] for k in ("x", "linkedin") if k in links_found},
        ),
        title=f"Website verified at {v.domain}",
        occurred_at=today_iso,
        url=v.final_url or f"https://{v.domain}/",
        strength=0.0,
        metrics=metrics,
        text=text,
    )


def targets(known: list[dict], limit: int | None) -> list[dict]:
    """Known companies with no domain, strongest first, capped."""
    cap = min(MAX_LOOKUPS, limit) if limit else MAX_LOOKUPS
    out = []
    for e in known:
        if not isinstance(e, dict) or e.get("domain") or e.get("kind") != "company":
            continue
        if not isinstance(e.get("name"), str) or not e["name"].strip():
            continue
        out.append(e)
        if len(out) >= cap:
            break
    return out


# ---------------------------------------------------------------- describing a known domain

def lacks_description(entity: dict) -> bool:
    """No one-liner, or only a registry's goods list in place of one."""
    liner = entity.get("one_liner")
    if not isinstance(liner, str) or not liner.strip():
        return True
    return liner.strip().startswith(REGISTRY_LINER_PREFIXES)


def describe_targets(known: list[dict], limit: int | None) -> list[dict]:
    """Known companies that have a domain and no description of their own, capped."""
    cap = min(MAX_DESCRIBE, limit) if limit else MAX_DESCRIBE
    out = []
    for e in known:
        if not isinstance(e, dict) or e.get("kind") != "company":
            continue
        if not isinstance(e.get("domain"), str) or not e["domain"].strip() or not lacks_description(e):
            continue
        if not isinstance(e.get("name"), str) or not e["name"].strip():
            continue
        out.append(e)
        if len(out) >= cap:
            break
    return out


def describe_page(entity: dict, final_url: str, page: Page, html: str = "") -> Verdict:
    """Decide whether the homepage of an entity's known domain may describe it.

    The domain came from another source (a contact e-mail, a registry's
    website field, a job board), so it may belong to a parent company, a law
    firm or a consultant who filed on the applicant's behalf. The page is
    used only when the company's name stands on it as a name. Nothing else
    is asked: the domain is the corroboration, and a real description that is
    off thesis is still the company's own words. Pure.
    """
    name = str(entity.get("name") or "")
    domain = str(entity.get("domain") or "").strip()
    v = Verdict(host=domain, source="known", domain=domain, final_url=final_url, page=page)
    forms = name_forms(name)
    if not forms:
        v.reason = "name too short to check against the page"
        return v
    final_host = urllib.parse.urlparse(final_url).netloc.split("@")[-1].split(":")[0].lower()
    parked = parked_reason(final_host, page, html)
    if parked:
        v.reason = f"parked: {parked}"
        return v
    if not clean_domain(final_host):
        v.reason = f"ends on {final_host or 'no host'}, which is not a company domain"
        return v
    v.reached_page = True
    if registrable(final_host.removeprefix("www.")) != registrable(domain.lower().removeprefix("www.")):
        v.checks.append("redirect_followed")
    found = name_slots(page, forms)
    if not found:
        # "Radiant" on radiantnuclear.com is Radiant Industries: when the
        # known domain itself starts with the company's name, the name
        # without its filler word counts as the name, even when one word is
        # left. On any other domain it does not, and that is the law firm or
        # the parent company. A sector word is never dropped: "Swarm" is not
        # Swarm Defense.
        stem_tokens, _ = _split_name(name_tokens(name))
        stem = "".join(stem_tokens)
        label = registrable(domain.lower().removeprefix("www.")).split(".")[0].replace("-", "")
        if domain_match(domain, name) == "exact" or (len(stem) >= 5 and label.startswith(stem)):
            if len(stem) >= 4 and name_slots(page, [NameForm(tuple(stem_tokens), "stem", False)]):
                v.checks.append("name_stem_on_own_domain")
                v.accepted = True
                return v
        v.reason = f"name not on the page as a name (title {page.title[:60]!r})"
        return v
    v.checks += [f"name_in_{s}" for s in found]
    if any(f.label == "full" for f, _ in found.values()):
        v.checks.append("name_full")
    v.accepted = True
    return v


def describe(entity: dict) -> Verdict:
    """Fetch the homepage of an entity's known domain and check it."""
    domain = str(entity.get("domain") or "").strip()
    try:
        final_url, html = fetch_homepage(domain.lower().removeprefix("www."))
    except http.HttpError as e:
        return Verdict(host=domain, source="known", domain=domain,
                       reason=f"homepage answered HTTP {e.status}", refused=True)
    except Exception as e:  # noqa: BLE001
        return Verdict(host=domain, source="known", domain=domain,
                       reason=f"homepage did not load ({type(e).__name__})")
    return describe_page(entity, final_url, parse_page(html), html)


def to_description_signal(entity: dict, v: Verdict, today_iso: str) -> Signal:
    page = v.page or Page()
    name = str(entity["name"])
    domain = str(entity["domain"]).strip()       # as known: never replaced by the final host
    links_found, text = _links_and_text(page, None, name, domain)
    metrics: dict[str, Any] = {
        "observed_only": True,
        "final_url": v.final_url,
        "domain_source": "known",
        "checks": list(v.checks),
    }
    if "ats" in links_found:
        metrics["ats_provider"], metrics["ats_slug"] = links_found["ats"]
    return Signal(
        source=SLUG, family=FAMILY, kind="website_described",
        entity=EntityHint(
            name=name, kind="company", domain=domain,
            github=links_found.get("github"),
            one_liner=page_one_liner(page, name),
            links={k: links_found[k] for k in ("x", "linkedin") if k in links_found},
        ),
        title=f"Website on file at {domain}",
        occurred_at=today_iso,
        url=v.final_url or f"https://{domain}/",
        strength=0.0,
        metrics=metrics,
        text=text,
    )


def collect(ctx: Context) -> Iterable[Signal]:
    todo = targets(ctx.known, ctx.limit)
    to_describe = describe_targets(ctx.known, ctx.limit)
    if not todo and not to_describe:
        ctx.log(f"{SLUG}: no known companies without a domain or without a description")
        return
    _install_final_url_hook()
    deadline = time.monotonic() + RUN_BUDGET_SECONDS
    # Describing is one request per company, so it goes first: the slower
    # search for missing domains cannot use up its share of the run budget.
    work: list[tuple[str, dict]] = [("describe", e) for e in to_describe] + [("identify", e) for e in todo]
    jobs: queue.Queue[tuple[int, str, dict]] = queue.Queue()
    done: queue.Queue[tuple[int, Any]] = queue.Queue()
    for i, (mode, e) in enumerate(work):
        jobs.put((i, mode, e))

    def worker() -> None:
        while True:
            try:
                i, mode, e = jobs.get_nowait()
            except queue.Empty:
                return
            if time.monotonic() > deadline:
                done.put((i, Outcome(entity=e, error="not attempted: run budget spent")))
                continue
            try:
                done.put((i, describe(e) if mode == "describe" else identify(e)))
            except Exception as ex:  # noqa: BLE001 - one company never stops the run
                done.put((i, Outcome(entity=e, error=f"{type(ex).__name__}: {ex}")))

    # Daemon threads: a site that drips bytes forever cannot hold the run open.
    for _ in range(min(WORKERS, len(work))):
        threading.Thread(target=worker, daemon=True).start()

    results: dict[int, Any] = {}
    hard_stop = deadline + 60
    while len(results) < len(work):
        try:
            i, outcome = done.get(timeout=max(0.1, hard_stop - time.monotonic()))
        except queue.Empty:
            break
        results[i] = outcome

    today_iso = ctx.today.isoformat()
    verified = described = lookups = dns_failures = not_attempted = 0
    # Verified sites first, in the order the entities were given.
    for i, (mode, e) in sorted(enumerate(work), key=lambda item: (item[1][0] != "identify", item[0])):
        outcome = results.get(i)
        if outcome is None:
            ctx.warn(f"{SLUG}: {e['name']}: abandoned, a site did not finish responding")
            continue
        if isinstance(outcome, Outcome) and outcome.error:
            if outcome.error.startswith("not attempted"):
                not_attempted += 1
            else:
                ctx.warn(f"{SLUG}: {e['name']}: {outcome.error}")
            continue
        if mode == "describe":
            v = outcome
            if not v.accepted:
                # Every miss is logged here, a failed fetch included: the
                # domain is on file, so a reader will want to know why its
                # description is not.
                ctx.log(f"{SLUG}: - {e['name']} ~ {e['domain']}: not described: {v.reason}")
                continue
            try:
                signal = to_description_signal(e, v, today_iso)
            except Exception as ex:  # noqa: BLE001
                ctx.warn(f"{SLUG}: {e['name']}: could not build signal: {type(ex).__name__}: {ex}")
                continue
            described += 1
            words = "with a one-liner" if signal.entity.one_liner else "no usable description on the page"
            ctx.log(f"{SLUG}: + {e['name']} described from {e['domain']} ({words})")
            yield signal
            continue
        lookups += outcome.lookups
        dns_failures += outcome.dns_failures
        if outcome.chosen:
            v = outcome.chosen
            try:
                signal = to_signal(e, v, today_iso)
            except Exception as ex:  # noqa: BLE001
                ctx.warn(f"{SLUG}: {e['name']}: could not build signal: {type(ex).__name__}: {ex}")
                continue
            verified += 1
            ctx.log(f"{SLUG}: + {e['name']} -> {v.domain} ({v.confidence}, {v.source})")
            yield signal
            continue
        if outcome.note:
            ctx.log(f"{SLUG}: - {e['name']}: {outcome.note}")
        logged: set[tuple[str, str]] = set()
        for v in outcome.verdicts:
            line = (v.domain or v.host, v.reason or "passed, not chosen")
            if (v.reached_page or v.accepted or v.refused) and line not in logged:
                logged.add(line)
                ctx.log(f"{SLUG}: - {e['name']} ~ {line[0]}: {line[1]}")
    if not_attempted:
        ctx.warn(f"{SLUG}: {not_attempted} companies not attempted, run budget of {RUN_BUDGET_SECONDS}s spent")
    if dns_failures:
        ctx.warn(f"{SLUG}: {dns_failures} of {lookups} DNS lookups got no answer from the resolver; those hosts were skipped")
    if todo:
        ctx.log(f"{SLUG}: {verified} verified of {len(todo)} companies without a domain, {lookups} DNS lookups")
    if to_describe:
        ctx.log(f"{SLUG}: {described} described of {len(to_describe)} companies with a domain and no description")
