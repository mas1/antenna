"""Young companies putting their own name on a research paper, found through OpenAlex.

OpenAlex keeps each author's affiliation line as written and tries to match it
to a registered research organisation; a line it cannot match that reads like a
company name, on a robotics, energy, chip or space paper, is usually a company
too new for any registry. This collector reports the first paper under such a
name (after checking OpenAlex for earlier ones) and repeat papers from young
companies, with the authors and where they published from before, plus Hugging
Face daily papers on robotics claimed by a company. It is early because a paper
is often the first public, dated thing a deep-tech team ships, months before a
launch, a job board or a funding filing.

OpenAlex reads arXiv affiliations out of the PDF and gets who-is-where wrong
often enough to matter (one affiliation given to every author, a footnote list
attached to the wrong name). So on an arXiv paper nobody is named, and no
author is counted, unless the paper's own HTML page on arxiv.org puts that
person with the company. Journal papers carry the publisher's per-author
affiliations and are taken as OpenAlex gives them; their dates are checked
against Crossref, because OpenAlex sometimes dates a paper by its acceptance.
"""

from __future__ import annotations

import html as htmllib
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from html.parser import HTMLParser
from typing import Any, Iterable

from .. import http
from ..models import EntityHint, Person, Signal
from ..thesis import LONE_TERM_FIT, classify
from .base import Context, clean_domain, iso, parse_date, squash

SLUG = "research_affil"
FAMILY = "research"
STAGE = "discover"
DESCRIPTION = "First and repeat papers under a new company name (OpenAlex unmatched affiliations), plus HF daily papers claimed by an org"

OPENALEX = "https://api.openalex.org"
ROR = "https://api.ror.org/v2/organizations"
HF = "https://huggingface.co/api/daily_papers"
# The paper as an HTML page. export.arxiv.org is the host arXiv keeps for
# programs, and antenna.http paces it at one request every three seconds;
# arxiv.org itself answers a faster crawl with HTTP 406.
ARXIV_HTML = "https://export.arxiv.org/html/"
CROSSREF = "https://api.crossref.org/works"
TTL = 20 * 3600  # OpenAlex anonymous budget is 1000 credits a day: never refetch within a day
TTL_SLOW = 7 * 24 * 3600  # ROR registry entries, closed HF weeks and arXiv pages do not change
CROSSREF_CHUNK = 40  # DOIs per Crossref lookup

# OpenAlex topic ids for the thesis (source card, section 2.2). Order is part
# of the cache key, so do not reorder casually.
TOPICS = (
    "T10653 T10462 T10879 T10571 T10191 T10586 T12784 T10868 "  # robotics
    "T11133 T12158 T11099 "  # drones, guidance, autonomous vehicles
    "T10346 T10384 T10597 T11242 "  # fusion, fission
    "T10663 T10018 T10281 "  # batteries
    "T10223 T10603 T10305 T10228 "  # grid, power electronics
    "T10558 T10361 T10472 "  # semiconductors
    "T11701 T12449 T12513 T13200 "  # space
    "T10783 T10705 T11741"  # manufacturing
).split()
ARXIV_SOURCE = "S4306400194"
WORK_SELECT = "id,doi,title,publication_date,type,primary_topic,primary_location,authorships,abstract_inverted_index"
HISTORY_SELECT = "id,doi,title,publication_date,authorships"
AUTHOR_SELECT = "id,display_name,works_count,cited_by_count,summary_stats,affiliations,topics,created_date"

# Credit plan for one full run (measured costs: filter page 1, search page 10).
MAX_CREDITS = 150
ARXIV_PAGES = 20  # 1 credit each, 200 works a page
SEARCH_PAGES = 4  # 10 credits each
AUTHOR_BATCHES = 12  # 1 credit per 100 authors
AUTHOR_RESERVE = 6  # credits the history searches must leave for the author lookups
HISTORY_CHUNK = 100  # company names per 10-credit history search
ESTABLISHED_YEARS = 5  # a name with ESTABLISHED_OLD_WORKS papers older than this is not a discovery
ESTABLISHED_OLD_WORKS = 3
MAX_PEOPLE = 8

# Self-upload repositories: single-author "LLC" uploads dominate them (card gotcha 14).
_SKIP_DOI_PREFIXES = ("10.5281/", "10.6084/", "10.17605/", "10.13140/", "10.31219/", "10.31224/")
_SKIP_SOURCES = ("zenodo", "figshare", "researchgate", "osf preprints", "ssrn")

# ---------------------------------------------------------------------------
# Affiliation strings -> company names
# ---------------------------------------------------------------------------

# Anywhere in the raw string: this is a school, a public lab, a person's
# status, or parser debris. Checked on an accent-stripped lowercase copy.
_HARD_NEG = re.compile(
    r"universi|\buniv\b|institut|istitut|college|school|schule|hospital|clinic|academ|akadem|"
    r"laborator|polytech|politec|\becole\b|escuela|escola|facult|\bminist|\bnational\b|federal|"
    r"government|state key|key lab|council|commission|\bagency\b|authority|bureau|society|"
    r"association|foundation|consorti|museum|observator|\barmy\b|\bnavy\b|air force|\bnasa\b|"
    r"\bcnrs\b|\binria\b|\bcea\b|fraunhofer|max planck|helmholtz|eurofusion|\biter\b|\bcern\b|"
    r"\bieee\b|science park|independent|unaffiliated|freelance|retired|student|professor|"
    r"researcher|candidate|alumni|\bfellow\b|\bmember\b|work done|\bintern(?:s|ship|ing)?\b|equal|contribut|"
    r"advis|project page|\bblog\b|https?:|www\.|github|is with|are with|also with|^with\b|"
    r"affiliated|\bauthor|founder|\d{4}-\d{4}|[{}@\[\]*♡♣♢♦⋆†‡§¶]"
)
# In the chosen name segment, when no legal form vouches for it.
_SOFT_NEG = re.compile(
    r"\b(department|dept|division|div|group|center|centre|lab|team|unit|office|program|"
    r"programme|project|chair|section|branch|campus|joint|hub|initiative|network|alliance|"
    r"club|community|conference|workshop|journal|studio|consulting)\b"
)

# In the name itself, legal form or not: firms that sell advice or hold other
# firms ("TRIZ Consulting Group GmbH", "K2 Holding L.L.C.", "punctum pr-agentur GmbH").
_NOT_A_BUILDER = re.compile(
    r"\b(consult\w*|agentur|holdings?|associates|management|capital|ventures|kompetenzzentrum|"
    r"energieagentur|ingenieurburo)\b|innovation cent(er|re)"
)

_NON_LATIN = re.compile("[\u0400-\u04ff\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")

# A leading segment that names a unit inside a company, not the company.
_UNIT = re.compile(r"\b(department|dept|division|div|group|team|unit|center|centre|r&d|office|section|business)\b")

# Legal forms. Three letters and up match case-insensitively; two-letter
# forms only in their exact case, because "se", "ab", "sa" are also words.
_LEGAL_CI = {
    "inc", "incorporated", "llc", "l.l.c", "ltd", "limited", "gmbh", "corp", "corporation",
    "oyj", "plc", "pty", "pte", "pvt", "llp", "jsc", "doo", "aps", "sas", "srl", "s.r.l",
    "s.p.a", "s.a", "b.v", "k.k", "pbc", "s.a.s",
}
_LEGAL_EXACT = {"SA", "AB", "AG", "SE", "BV", "KG", "Oy", "OÜ", "KK", "NV", "AS"}

# Last word of a name with no legal form. Deliberately excludes "Research",
# "Lab", "Software", "Solutions", "Group": too many academic units end that way.
_HINT_LAST = {
    "robotics", "robots", "technologies", "technology", "tech", "labs", "dynamics", "fusion",
    "energy", "aerospace", "space", "systems", "intelligence", "motors", "semiconductor",
    "semiconductors", "photonics", "microsystems", "propulsion", "power", "nuclear", "atomics",
    "autonomy", "aero", "aviation", "industries", "enterprises", "instruments", "materials",
    "devices", "machines", "electric", "electronics", "bionics", "silicon", "mobility",
    "automation", "aeronautics", "sensors", "drones", "orbital", "energetics", "composites",
    "manufacturing", "motion",
}
_HINT_CAMEL = re.compile(r"[a-z](Robotics|Labs|AI|Dynamics|Energy|Fusion|Space|Aerospace)$")
_HINT_DOMAIN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]+\.(ai|io|bot|tech)$")

# Words that do not make a name distinctive on their own.
_GENERIC = _HINT_LAST | {
    "ai", "a.i", "intelligent", "autonomous", "cognitive", "mobile", "field", "medical", "soft",
    "advanced", "applied", "embodied", "general", "artificial", "machine", "learning", "computer",
    "vision", "perception", "control", "human", "humanoid", "robot", "robotic", "system",
    "engineering", "science", "sciences", "research", "data", "digital", "information",
    "computing", "electrical", "electronic", "mechanical", "industrial", "plasma", "physics",
    "chemistry", "new", "smart", "future", "innovation", "international", "global", "the", "of",
    "and", "for", "in", "at", "de", "platform", "development", "design", "solutions", "services",
    "software", "hardware", "lab", "group", "company", "co", "neurosymbolic", "quantum",
    "computational", "theoretical", "experimental", "integrated", "circuits", "vehicle",
    "vehicles", "driving", "transportation", "storage", "battery", "batteries", "laser",
    "optical", "optics", "cloud", "edge", "web", "world", "models", "model", "foundation",
    "multimodal", "language", "speech", "audio", "video", "image", "3d", "safety", "security",
    # "Tech and Business Solutions LLC": a name made only of such words identifies nobody.
    "business", "trade", "trading", "technical", "service", "commercial", "enterprise",
    "products", "production", "project", "projects", "works",
}

# Trailing words to drop from a name ("Knowin AI Shenzhen", "Bauplan Labs USA")
# and segments that are only a place.
_PLACES = {
    "usa", "us", "u.s.a", "u.s", "uk", "u.k", "china", "prc", "p.r.china", "japan", "korea",
    "germany", "france", "italy", "spain", "portugal", "netherlands", "belgium", "switzerland",
    "austria", "sweden", "norway", "denmark", "finland", "estonia", "poland", "ireland",
    "greece", "turkey", "türkiye", "israel", "india", "singapore", "vietnam", "taiwan",
    "australia", "canada", "brazil", "mexico", "uae", "russia", "serbia", "croatia", "europe",
    "america", "asia", "deutschland", "österreich",
    "beijing", "shanghai", "shenzhen", "hangzhou", "guangzhou", "suzhou", "nanjing", "wuhan",
    "chengdu", "xi'an", "xian", "hefei", "tianjin", "chongqing", "qingdao", "wuxi", "hong", "kong",
    "tokyo", "osaka", "kyoto", "yokohama", "seoul", "daejeon", "daegu", "hanoi", "taipei",
    "bangalore", "bengaluru", "mumbai", "delhi", "hyderabad", "london", "cambridge", "oxford",
    "paris", "grenoble", "toulouse", "lyon", "berlin", "munich", "münchen", "stuttgart",
    "hamburg", "dresden", "garching", "zurich", "zürich", "lausanne", "geneva", "vienna", "wien",
    "graz", "madrid", "barcelona", "lisbon", "porto", "rome", "milan", "genoa", "padova",
    "amsterdam", "rotterdam", "delft", "eindhoven", "brussels", "stockholm", "gothenburg",
    "copenhagen", "oslo", "helsinki", "espoo", "tallinn", "belgrade", "moscow", "dubai",
    "abu", "dhabi", "tel", "aviv", "toronto", "montreal", "vancouver", "waterloo", "ottawa",
    "boston", "york", "francisco", "san", "los", "angeles", "seattle", "austin", "pittsburgh",
    "palo", "alto", "sunnyvale", "mountain", "view", "diego", "jose", "berkeley", "pasadena",
    "houston", "dallas", "denver", "boulder", "chicago", "atlanta", "detroit", "philadelphia",
    "california", "texas", "massachusetts", "washington", "colorado", "michigan", "ohio",
    "ca", "ny", "ma", "tx", "wa", "pa", "nj", "co", "il", "mi", "az", "nc", "va", "md", "tn",
    "sydney", "melbourne", "canberra", "auckland", "republic", "people's", "south", "north",
}

# Not a discovery. Any name carrying one of these words is skipped; the
# history check below removes the long tail of established publishers.
_INCUMBENTS = {
    "samsung", "huawei", "honor", "oppo", "vivo", "xiaomi", "bosch", "sony", "toyota", "nissan",
    "honda", "mercedes", "mercedes-benz", "porsche", "bmw", "volkswagen", "audi", "cariad", "ford",
    "hyundai", "kia", "geely", "dongfeng", "chery", "byd", "nio", "xpeng", "leapmotor", "amazon",
    "google", "deepmind", "meta", "microsoft", "nvidia", "apple", "intel", "ibm", "amd",
    "qualcomm", "broadcom", "micron", "tsmc", "asml", "alibaba", "ant", "tencent", "bytedance",
    "baidu", "jd", "jd.com", "didi", "midea", "meituan", "bilibili", "ericsson", "nokia", "cisco",
    "salesforce", "oracle", "unity", "adobe", "waymo", "aurora", "motional", "cruise", "tesla",
    "spacex", "openai", "anthropic", "denso", "astemo", "framatome", "westinghouse", "teledyne",
    "gkn", "kuka", "siemens", "abb", "ge", "infineon", "leonardo", "rheinmetall", "valeo",
    "criteo", "konecranes", "omnivision", "jacobs", "bechtel", "bechtelplant", "saft", "ptc",
    "avl", "emd", "omv", "lg", "sk", "panasonic", "hitachi", "mitsubishi", "toshiba", "fujitsu",
    "nec", "ntt", "canon", "nikon", "boeing", "airbus", "lockheed", "northrop", "raytheon",
    "bae", "thales", "safran", "rolls-royce", "shell", "bp", "exxon", "chevron", "aramco",
    "sinopec", "catl", "petroleum", "state grid", "dji", "ubtech", "horizon", "symbotic",
    "caterpillar", "komatsu", "lenovo", "dell", "hp", "zte", "sensetime", "iflytek", "xiaopeng",
    "stellantis", "renault", "volvo", "zenseact", "continental", "zf", "schaeffler", "thermo",
    "zeiss", "deloitte", "accenture", "larsen", "constellation", "powerchina", "varta", "mtu",
    "akkodis", "havelsan", "phinia", "synopsys", "cadence", "studsvik", "borax",
    "yandex", "naver", "kakao", "rakuten", "softbank", "foxconn", "li", "qwen", "wan", "damo",
    "inclusionai", "robbyant", "mistral", "minimax", "stepfun", "insta360", "moonshot", "zhipu",
    "deepseek", "cohere", "xai", "perplexity", "stability", "midjourney", "runway",
    # Old industrial firms and listed companies that publish too rarely for
    # the history check to catch them (a shipyard founded in 1825, a fan maker).
    "davie", "miba", "piller", "jauch", "zhongchai", "atomberg", "velo3d", "hoymiles", "landspace",
    "swarovski",
}
# "li" covers Li Auto.


def _fold(s: str) -> str:
    """Lowercase and strip accents, for matching only (never for display)."""
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).lower()


def _tokens(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", _fold(s))


def company_key(name: str) -> str:
    """Grouping key: drop legal forms and bracketed parts, keep everything else.

    "Shadow AI" and "Shadow Robotics" stay two companies, as they do under
    base.normalize_name. This key is kept beside the shared one because
    affiliation lines need two things it does not do: accents are folded
    ("Grünweg GmbH" is "grunweg", not "gr nweg"), and the European forms
    met here (SE, KG, OÜ, JSC, d.o.o.) are dropped wherever they stand, so
    "VinRobotics" and "VinRobotics JSC" are one company.
    """
    s = re.sub(r"\(.*?\)", " ", name)
    words = [w for w in s.split() if not _is_legal(w) and w.strip(".,") not in ("Co", "CO", "&")]
    return " ".join(_tokens(" ".join(words)))


def _is_legal(tok: str) -> bool:
    t = tok.strip(",.;:")
    if not t:
        return False
    return t in _LEGAL_EXACT or t.lower() in _LEGAL_CI


def _is_legal_at(words: list[str], i: int) -> bool:
    w = words[i]
    if _is_legal(w):
        return True
    bare = w.strip(",;:")
    # "Co." counts; a bare "Co" only when "Ltd" follows.
    if bare in ("Co.", "CO.", "Co.,"):
        return True
    if bare in ("Co", "CO") and i + 1 < len(words) and _is_legal(words[i + 1]):
        return True
    return False


@dataclass
class Affil:
    name: str  # display name, as written minus address and footnote debris
    key: str  # grouping key
    legal: bool  # carries a legal form (Inc, GmbH, Co. Ltd, ...)
    strong: bool  # legal form or a company-like last word; False = name only
    location: str | None = None


def _location(rest: list[str]) -> str | None:
    """Last two place-like parts of the address tail, digits removed."""
    out = []
    for seg in rest:
        seg = " ".join(w for w in seg.split() if not re.search(r"\d", w)).strip(" .,;:-")
        if seg and (len(seg) >= 3 or seg.isupper()) and len(seg) <= 40 and not _is_legal(seg):
            out.append(seg)
    out = out[-2:]
    return ", ".join(out) if out else None


def clean_affiliation(raw: str | None) -> Affil | None:
    """Turn one raw affiliation line into a company name, or None.

    Returns None for universities, public labs, addresses, parser debris and
    anything else that does not read like a company. `strong=False` results
    are bare names with no company marker ("Sophelio"); the caller keeps them
    only when the same name also appears with a marker ("Sophelio LLC").
    """
    if not raw:
        return None
    s = unicodedata.normalize("NFKC", htmllib.unescape(raw))  # "R&#x0026;D", "Research &amp; Development"
    s = re.sub("[‐‑]", "-", s)  # typographic hyphens: "Laser‐Cut‐Processing"
    s = re.sub(r"\s+", " ", s).strip()
    if not s or len(s) > 220:
        return None
    if _NON_LATIN.search(s):  # Cyrillic, kana, CJK, hangul: names we cannot normalise
        return None
    s = re.sub(r"(?<=[a-z])- (?=[a-z])", "", s)  # "Uni- versity" from PDF line breaks
    s = re.sub(r"\s+-\s+", "-", s)  # "Dense - AI"
    s = re.sub(r"\s+([,.;:)])", r"\1", s)
    s = re.sub(r"\(\s+", "(", s)
    s = re.sub(r"^(?:[)*†‡§¶⋆]\s*|\d{1,2}\s+)+", "", s)  # footnote marks: ") Proxima", "1 NY Creates"
    # Anything else in front of the first letter means the start of the string
    # was lost in parsing, and what is left may be half a name.
    if not s or not (s[0].isalnum() or s[0] == "("):
        return None
    if _HARD_NEG.search(_fold(s)):
        return None
    s = re.sub(r"(?<=\w)\(", " (", s)  # "Tsing-AI(Shanghai)"
    # A legal form after a comma belongs to the name: "Acme, Inc." "Co., Ltd."
    s = re.sub(
        r"\s*,\s*(?=(?:Inc|INC|LLC|L\.L\.C|Ltd|LTD|Limited|GmbH|Corp|Corporation|S\.A|S\.r\.l|S\.p\.A|B\.V)\b)",
        " ", s,
    )
    segs = [x.strip() for x in re.split(r"[,;]", s)]
    segs = [x for x in segs if x]
    if not segs:
        return None

    for idx, seg in enumerate(segs[:3]):
        words = seg.split()
        hit = next((i for i in range(1, len(words)) if _is_legal_at(words, i)), None)
        if hit is None:
            # "Propulsion Division, Perigee Aerospace Inc." is fine;
            # "Mühlbauer+partner, Technische Dokumentation GmbH" is one name
            # split by a comma, and its second half alone would be wrong.
            if not _UNIT.search(_fold(seg)):
                break
            continue
        if not (seg[0].isalnum() or seg[0] == "("):
            return None  # "Space Applications Group, : fs TechHub GmbH" is "e:fs TechHub GmbH" cut short
        end = hit
        while end + 1 < len(words) and (
            _is_legal_at(words, end + 1)
            or (words[end + 1] == "&" and end + 2 < len(words) and _is_legal_at(words, end + 2))
        ):
            end += 2 if words[end + 1] == "&" else 1
        name_words = words[: end + 1]
        tail = " ".join(words[end + 1:])
        rest = ([tail] if tail and not tail.startswith("(") else []) + segs[idx + 1:]
        return _finish(name_words, legal=True, rest=rest)

    # No legal form: only the first segment can be the name.
    words = segs[0].split()
    while len(words) > 1 and (words[-1].strip(".").lower() in _PLACES or re.fullmatch(r"[\d.]+", words[-1])):
        words.pop()
    if not words or _SOFT_NEG.search(_fold(" ".join(words))):
        return None
    last = words[-1].strip(".")
    tail = last.rsplit("-", 1)[-1]  # "Dense-AI"
    strong = (
        tail.lower() in _HINT_LAST
        or tail in ("AI", "A.I")
        or bool(_HINT_CAMEL.search(last))
        or bool(_HINT_DOMAIN.match(last))
    )
    return _finish(words, legal=False, rest=segs[1:], strong=strong)


def _finish(words: list[str], *, legal: bool, rest: list[str], strong: bool = True) -> Affil | None:
    name = " ".join(words).strip(" ,;:")
    name = name.rstrip(".")
    if re.search(r"\b[A-Za-z]\.[A-Za-z]$|\.[A-Za-z]\.[A-Za-z]$", name):
        name += "."  # "S.A." "S.r.l." keep their closing dot
    if name.count("(") != name.count(")") or '"' in name or "“" in name:
        return None
    if not legal and re.search(r"[.:] ", name):
        return None  # "hessian. AI", sentence debris
    core = [w for w in re.sub(r"\(.*?\)", " ", name).split() if not _is_legal(w) and w.strip(".,") not in ("Co", "CO", "&")]
    if not core or len(core) > 5 or len(name) > 70:
        return None
    low = [_fold(w).strip(".,") for w in core]
    joined = " ".join(low)
    if any(p in _INCUMBENTS for p in re.split(r"[\s\-]+", joined)) or any(" " in i and i in joined for i in _INCUMBENTS):
        return None
    if _NOT_A_BUILDER.search(joined):
        return None
    if all(w in _PLACES for w in low):
        return None
    distinctive = [w for w in low if w not in _GENERIC and w not in _PLACES and len(re.sub(r"[^a-z0-9]", "", w)) >= 2]
    if not distinctive:
        return None
    if not legal and len(core) == 1 and not strong and len(core[0]) < 4:
        return None
    key = company_key(name)
    if len(key) < 3:
        return None
    return Affil(name=name, key=key, legal=legal, strong=legal or strong, location=_location(rest))


# ---------------------------------------------------------------------------
# OpenAlex works -> mentions
# ---------------------------------------------------------------------------


def abstract_text(inv: dict[str, list[int]] | None) -> str:
    """Rebuild an abstract from OpenAlex's inverted index."""
    if not inv:
        return ""
    pos: dict[int, str] = {}
    for word, places in inv.items():
        for p in places:
            pos[p] = word
    return " ".join(pos[i] for i in sorted(pos))


def evidence_url(work: dict) -> str | None:
    """The public landing page: arXiv abs page or DOI, never the API URL."""
    doi = (work.get("doi") or "").strip()
    if doi:
        bare = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi, flags=re.I)
        m = re.match(r"10\.48550/arxiv\.(.+)$", bare, flags=re.I)
        if m:
            return f"https://arxiv.org/abs/{m.group(1)}"
        return f"https://doi.org/{bare}"
    landing = ((work.get("primary_location") or {}).get("landing_page_url") or "").strip()
    if landing.startswith("http") and "openalex.org" not in landing:
        return landing
    return None


def _skip_work(work: dict) -> bool:
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", (work.get("doi") or ""), flags=re.I).lower()
    if doi.startswith(_SKIP_DOI_PREFIXES):
        return True
    src = ((work.get("primary_location") or {}).get("source") or {}).get("display_name") or ""
    return any(s in src.lower() for s in _SKIP_SOURCES)


def title_key(title: str) -> str:
    """Same paper, different record: compare titles without prefixes or punctuation."""
    t = re.sub(r"^(correction|erratum|corrigendum|publisher correction|author correction)\s*(to)?\s*:?\s*", "",
               _fold(title).strip())
    return re.sub(r"[^a-z0-9]+", "", t)


def _short_id(openalex_url: str | None) -> str:
    return (openalex_url or "").rsplit("/", 1)[-1]


def topic_note(topic: str | None) -> str:
    """OpenAlex's primary topic for a paper, as a sentence for Signal.text.

    Every work read here was picked by its primary topic (TOPICS), so the
    topic's name is evidence of what the paper is about, and it often
    holds the second thesis word a short title lacks: "inverse design of
    inertial fusion implosions", filed under "Laser-Plasma Interactions
    and Diagnostics".
    """
    return f"OpenAlex topic: {topic}." if topic else ""


def on_thesis(own: str, topic: str | None) -> bool:
    """Rule 7, with OpenAlex's topic as a second witness and never the only one.

    The paper's own words (with the company's name) pass alone at 0.3.
    Under that, the topic may supply the second term the classifier asks
    for, but only beside a strong thesis word in the paper itself, which
    is what a fit at the lone-term cap means. The topic alone proves
    nothing: OpenAlex assigns topics by model and files a study of
    GPS-tracked red kites under "Robotics and Sensor-Based Localization".
    A topic that only repeats the paper's one word adds no second term
    either: a paper that says nothing but "battery" and "batteries", filed
    under "Advanced Battery Technologies Research", stays under the gate,
    as it will when the pipeline classifies the same text.
    """
    fit = classify(own)["fit"]
    if fit >= 0.3:
        return True
    return bool(topic) and fit >= LONE_TERM_FIT and classify(f"{own} {topic_note(topic)}")["fit"] >= 0.3


def _inst_verified(display: str, raws: list[str]) -> bool:
    """Is an OpenAlex institution match borne out by the raw text it came from?

    OpenAlex mis-maps strings ("DexRobot Co. Ltd" -> Medrobotics, "UC
    Berkeley" -> Berkeley College). Accept a match only when every
    significant word of the institution's name, or its acronym, is in the
    raw affiliation line.
    """
    stop = {"of", "the", "and", "for", "at", "in", "de", "di", "la", "du", "des", "der"}
    words = [w for w in _tokens(re.sub(r"\(.*?\)", " ", display)) if w not in stop]
    if not words:
        return False
    acronym = "".join(w[0] for w in words)
    for raw in raws:
        toks = set(_tokens(raw))
        if all(w in toks for w in words):
            return True
        if len(acronym) >= 3 and acronym in toks:
            return True
    return False


# OpenAlex institution names that say nothing about a person when they are
# matched out of a longer line ("Key Laboratory ... of the Ministry of Education").
_VAGUE_INSTITUTION = re.compile(r"^(ministry|department|government|state council) of\b", re.I)

# An "author" that is a group of people: "Inertia Collaboration", "ATLAS Team".
_PSEUDO_AUTHOR = re.compile(r"\b(collaboration|consortium|team|group|committee|investigators|project)\b")


def _same_orcid(a: str | None, b: str | None) -> bool:
    x, y = ((v or "").rsplit("/", 1)[-1].strip().upper() for v in (a, b))
    return bool(x) and x == y


def natural_name(raw: str | None) -> str:
    """An author name as the paper prints it, given name first.

    arXiv records arrive as "Lee, Jiyul"; some journals shout the surname
    ("Matvey V. NASANOVICH"). Nothing is added and nothing is expanded.
    """
    s = re.sub(r"\s+", " ", raw or "").strip(" ,;")
    if s.count(",") == 1:
        last, first = (x.strip() for x in s.split(","))
        if last and first:
            s = f"{first} {last}"
    return " ".join(w.capitalize() if len(w) > 3 and w.isalpha() and w.isupper() else w for w in s.split())


@dataclass
class Mention:
    author_id: str  # OpenAlex author id, or "name:<key>" when OpenAlex assigned none
    author_name: str  # as printed on the paper, given name first
    position: str
    company_only: bool  # no other affiliation on this paper
    others: list[str] = field(default_factory=list)  # verified co-affiliations on this paper
    # True when the publisher printed an ORCID for this author and it is the
    # ORCID of the OpenAlex profile the authorship was matched to.
    orcid_match: bool = False


@dataclass
class Paper:
    id: str
    title: str
    date: date
    url: str
    fit: float
    text: str
    n_authors: int  # people on the paper: no collaborations, no repeated records
    topic: str | None
    doi: str | None = None  # bare DOI of a journal paper, for the Crossref date check
    arxiv_id: str | None = None  # set when the evidence page is an arXiv abstract page
    author_names: list[str] = field(default_factory=list)  # everyone on the paper, as printed
    # Company keys whose authors could not be checked against the arXiv page
    # (no HTML version, or a layout that does not say who is where).
    unverified: set[str] = field(default_factory=set)
    topic_field: str | None = None  # OpenAlex field of the primary topic, e.g. "Engineering"
    # True when every author carries the same two or more affiliation lines:
    # OpenAlex could not tell who belongs where and gave everything to
    # everyone, so nothing can be said about who is at the company.
    uniform: bool = False
    n_affiliations: int = 0  # distinct affiliation lines on the paper
    mentions: dict[str, list[Mention]] = field(default_factory=dict)  # company key -> authors
    affils: dict[str, list[Affil]] = field(default_factory=dict)


def parse_work(work: dict) -> Paper | None:
    """One OpenAlex work -> the unmatched company affiliations on it."""
    d = parse_date(work.get("publication_date"))
    title = re.sub(r"\s+", " ", work.get("title") or "").strip()
    url = evidence_url(work)
    if not d or not title or not url or _skip_work(work):
        return None
    text = f"{title}. {abstract_text(work.get('abstract_inverted_index'))}".strip()
    authorships = [a for a in work.get("authorships") or [] if isinstance(a, dict)]
    names = []
    for a in authorships:
        display = ((a.get("author") or {}).get("display_name") or "").strip()
        names.append(natural_name(a.get("raw_author_name")) or display)
    people = {_name_key(n) for n in names if n and not _PSEUDO_AUTHOR.search(_fold(n))}
    m = re.match(r"https://arxiv\.org/abs/(.+)$", url)
    paper = Paper(
        id=_short_id(work.get("id")), title=title, date=d, url=url,
        fit=classify(text)["fit"], text=text, n_authors=len(people),
        topic=(work.get("primary_topic") or {}).get("display_name"),
        topic_field=((work.get("primary_topic") or {}).get("field") or {}).get("display_name"),
        doi=None if m else (re.sub(r"^https?://(dx\.)?doi\.org/", "", work.get("doi") or "", flags=re.I) or None),
        arxiv_id=m.group(1) if m else None,
        author_names=[n for n in names if n],
    )
    line_sets = [
        frozenset((af.get("raw_affiliation_string") or "").strip() for af in a.get("affiliations") or []
                  if (af.get("raw_affiliation_string") or "").strip())
        for a in authorships
    ]
    filled = [x for x in line_sets if x]
    paper.uniform = len(filled) >= 3 and len(set(filled)) == 1 and len(filled[0]) >= 2
    paper.n_affiliations = len(set().union(*filled)) if filled else 0
    seen: set[str] = set()
    for a, aname in zip(authorships, names):
        author = a.get("author") or {}
        if not aname or _PSEUDO_AUTHOR.search(_fold(aname)) or _name_key(aname) in seen:
            continue  # a collaboration, or the same person recorded twice
        seen.add(_name_key(aname))
        # OpenAlex leaves some authors without an id (Neil Mitchell on a
        # Gauss Fusion paper): they still count, under their name.
        aid = _short_id(author.get("id")) or f"name:{_name_key(aname)}"
        inst_by_id = {i.get("id"): i for i in a.get("institutions") or []}
        found: dict[str, Affil] = {}
        others: list[str] = []
        n_strings = 0
        for af in a.get("affiliations") or []:
            raw = af.get("raw_affiliation_string") or ""
            if not raw.strip():
                continue
            n_strings += 1
            ids = af.get("institution_ids") or []
            if not ids:
                c = clean_affiliation(raw)
                if c:
                    found.setdefault(c.key, c)
                continue
            for iid in ids:
                disp = (inst_by_id.get(iid) or {}).get("display_name")
                if (disp and disp not in others and not _VAGUE_INSTITUTION.search(disp)
                        and _inst_verified(disp, [raw])):
                    others.append(disp)
        for key, c in found.items():
            paper.mentions.setdefault(key, []).append(Mention(
                author_id=aid, author_name=aname, position=a.get("author_position") or "",
                company_only=(n_strings == 1 and not a.get("institutions")), others=list(others),
                orcid_match=_same_orcid(a.get("raw_orcid"), author.get("orcid")),
            ))
            paper.affils.setdefault(key, []).append(c)
    return paper if paper.mentions else None


@dataclass
class Company:
    key: str
    name: str
    aliases: list[str]
    legal: bool
    location: str | None
    papers: list[Paper]  # oldest first, only papers carrying this name, one per title
    duplicate_ids: set[str] = field(default_factory=set)  # other records of the same papers

    @property
    def authors(self) -> dict[str, list[tuple[Paper, Mention]]]:
        """Author id -> the papers on which that author carries the company's name.

        Papers where OpenAlex gave every affiliation to every author are
        left out: they say the company is on the paper, not who is at it.
        """
        out: dict[str, list[tuple[Paper, Mention]]] = defaultdict(list)
        for p in self.papers:
            if p.uniform:
                continue
            for m in p.mentions.get(self.key, []):
                out[m.author_id].append((p, m))
        return out


def group_companies(papers: Iterable[Paper], since: date, today: date) -> list[Company]:
    """Group papers by company key; keep names with a company marker and an on-thesis paper."""
    by_key: dict[str, list[Paper]] = defaultdict(list)
    for p in papers:
        if p.date < since or p.date > today:
            continue
        for key in p.mentions:
            by_key[key].append(p)
    out = []
    for key, plist in by_key.items():
        affils = [c for p in plist for c in p.affils[key]]
        if not any(c.strong for c in affils):
            continue
        names = Counter(c.name for c in affils if c.strong)
        best = sorted(names.items(), key=lambda kv: (-kv[1], len(kv[0]), kv[0]))[0][0]
        # Rule 7. The name counts as text: "Realta Fusion Inc" on a plasma
        # paper is on thesis even when the abstract never says "fusion".
        if not any(p.fit >= 0.3 or on_thesis(f"{best}. {p.text}", p.topic) for p in plist):
            continue
        locs = Counter(c.location for c in affils if c.location)
        # One paper, one count: OpenAlex holds the preprint, the journal
        # version and the correction notice as separate works.
        seen: dict[str, Paper] = {}
        for p in sorted(plist, key=lambda p: (p.date, "arxiv.org" not in p.url and "doi.org" not in p.url, p.id)):
            seen.setdefault(title_key(p.title), p)
        out.append(Company(
            key=key, name=best,
            aliases=sorted({c.name for c in affils} - {best}),
            legal=any(c.legal for c in affils),
            location=locs.most_common(1)[0][0] if locs else None,
            papers=list(seen.values()),
            duplicate_ids={p.id for p in plist} - {p.id for p in seen.values()},
        ))
    out.sort(key=lambda c: (-len(c.authors), -len(c.papers), c.name))
    return out


# ---------------------------------------------------------------------------
# OpenAlex and ROR access, with a client-side credit budget
# ---------------------------------------------------------------------------


class Budget:
    """Planned OpenAlex credits for this run. Counts cached calls too, so a
    run makes the same requests whether or not the cache is warm."""

    def __init__(self, cap: int = MAX_CREDITS) -> None:
        self.cap = cap
        self.spent = 0

    def take(self, cost: int, keep: int = 0) -> bool:
        """Spend `cost` credits unless that would leave fewer than `keep` under the cap."""
        if self.spent + cost > self.cap - keep:
            return False
        self.spent += cost
        return True


def _works_pages(ctx: Context, budget: Budget, filt: str, cost: int, max_pages: int, label: str) -> list[dict]:
    out: list[dict] = []
    cursor = "*"
    for page in range(max_pages):
        if not budget.take(cost):
            ctx.warn(f"{label}: credit budget reached after {page} pages")
            break
        try:
            data = http.get_json(
                f"{OPENALEX}/works",
                params={"filter": filt, "per-page": 200, "cursor": cursor,
                        "sort": "publication_date:desc", "select": WORK_SELECT},
                ttl=TTL, timeout=60,
            )
        except Exception as e:  # noqa: BLE001 - keep what we have
            ctx.warn(f"{label}: page {page + 1} failed: {type(e).__name__}: {str(e)[:120]}")
            break
        results = data.get("results") or []
        out.extend(results)
        cursor = (data.get("meta") or {}).get("next_cursor")
        if not cursor or len(results) < 200:
            break
    else:
        ctx.log(f"{label}: stopped at the page cap ({max_pages}), older works not read")
    return out


def fetch_works(ctx: Context, budget: Budget) -> list[dict]:
    topics = "|".join(TOPICS)
    since = iso(ctx.since)
    quick = ctx.limit is not None and ctx.limit <= 50
    arxiv = _works_pages(
        ctx, budget,
        f"primary_topic.id:{topics},from_publication_date:{since},has_raw_affiliation_strings:true,"
        f"primary_location.source.id:{ARXIV_SOURCE}",
        cost=1, max_pages=2 if quick else ARXIV_PAGES, label="openalex arxiv works",
    )
    journals = _works_pages(
        ctx, budget,
        f"primary_topic.id:{topics},from_publication_date:{since},"
        f"primary_location.source.id:!{ARXIV_SOURCE},raw_affiliation_strings.search:Inc|LLC|GmbH|Corp",
        cost=10, max_pages=1 if quick else SEARCH_PAGES, label="openalex company-suffix works",
    )
    ctx.log(f"openalex: {len(arxiv)} arXiv works, {len(journals)} journal works, {budget.spent} credits planned so far")
    return arxiv + journals


def _phrase(name: str) -> str:
    """The searchable core of a name: text before any bracket, no legal form."""
    head = name.split("(")[0]
    words = [w for w in head.split() if not _is_legal(w) and w.strip(".,") not in ("Co", "CO", "&")]
    s = re.sub(r"[^\w .&\-']", " ", " ".join(words), flags=re.UNICODE)
    return re.sub(r"\s+", " ", s).strip(" .-&'")


def _contains(tokens: list[str], phrase: list[str]) -> bool:
    n = len(phrase)
    return any(tokens[i:i + n] == phrase for i in range(len(tokens) - n + 1))


def ror_match(items: list[dict], phrase: str) -> dict | None:
    """The ROR record whose name equals the phrase, if any."""
    want = _tokens(phrase)
    for it in items or []:
        for n in it.get("names") or []:
            have = [w for w in _tokens(re.sub(r"\(.*?\)", " ", n.get("value") or ""))]
            if have == want or [w for w in have if w not in _LEGAL_CI] == want:
                return it
    return None


def check_ror(ctx: Context, companies: list[Company]) -> dict[str, dict | bool | None]:
    """key -> the ROR record (registered), False (not in ROR), None (could not check)."""
    out: dict[str, dict | bool | None] = {}
    broken = False
    for c in companies:
        phrase = _phrase(c.name)
        if broken or not phrase:
            out[c.key] = None
            continue
        try:
            data = http.get_json(ROR, params={"query": f'"{phrase}"'}, ttl=TTL_SLOW, timeout=20, retries=1)
            out[c.key] = ror_match(data.get("items") or [], phrase) or False
        except Exception as e:  # noqa: BLE001
            out[c.key] = None
            if getattr(e, "status", None) in (429, 403) or not isinstance(e, http.HttpError):
                broken = True
                ctx.warn(f"ROR check stopped: {type(e).__name__}: {str(e)[:100]}")
    return out


def ror_is_young(record: dict, today: date) -> bool:
    """A registered organisation still counts when ROR dates it inside the last five years."""
    est = record.get("established")
    return isinstance(est, int) and est >= today.year - ESTABLISHED_YEARS


def ror_website(record: dict) -> str | None:
    for link in record.get("links") or []:
        if link.get("type") == "website" and link.get("value"):
            return link["value"]
    return None


def ror_same_place(record: dict, location: str | None) -> bool:
    """Does the registry place the organisation where the paper's address line does?

    A name match alone could be a namesake. The website and founding year
    are taken from ROR only when city or country agree as well.
    """
    if not location:
        return False
    places: set[str] = set()
    for loc in record.get("locations") or []:
        details = loc.get("geonames_details") or {}
        for k in ("name", "country_name", "country_subdivision_name"):
            places.update(t for t in _tokens(details.get(k) or "") if len(t) >= 4)
    return bool(places & {t for t in _tokens(location) if len(t) >= 4})


@dataclass
class History:
    """What OpenAlex holds under one company name before the first paper we parsed."""
    checked: bool = False  # False: could not be checked, make no claim either way
    complete: bool = False  # True: every earlier work under the name was read
    earlier: list[tuple[str, str]] = field(default_factory=list)  # (date, work id)
    old: list[str] = field(default_factory=list)  # distinct dates of works over ESTABLISHED_YEARS old
    old_known: bool = False  # True: `old` is the full list, or already past the cut-off

    @property
    def first(self) -> bool:
        return self.checked and self.complete and not self.earlier

    def is_old(self) -> bool:
        """Old works on three or more dates, in at least two different years.

        One year is not enough: OpenAlex holds clusters of mis-dated
        records (five "Proxima Fusion" works all dated 2012-02-24, eleven
        years before the company existed).
        """
        return len(self.old) >= ESTABLISHED_OLD_WORKS and len({d[:4] for d in self.old}) >= 2

    def established(self) -> bool | None:
        """True: publishing for years. False: young. None: not determined."""
        if self.is_old() or len(self.earlier) >= 100:
            return True
        if self.checked and (self.complete or self.old_known):
            return False
        return None


def search_phrases(c: Company) -> list[str]:
    """Phrases that identify the company in an affiliation search.

    A one-word name ("Turing Inc", "Conception Inc") is searched with its
    legal form, otherwise every institute with that word in its name floods
    the page. The bare word is added back when the company's own authors
    wrote it bare in the window ("Sophelio" next to "Sophelio LLC").
    """
    core = _phrase(c.name)
    if not core:
        return []
    out = [core]
    if c.legal and len(core.split()) == 1:
        tail = c.name.split("(")[0].split()
        legal = next((w.strip(".,") for w in tail[1:] if _is_legal(w) or w.strip(".,") in ("Co", "CO")), None)
        if legal:
            out = [f"{core} {legal}"]
            if any(_tokens(a) == _tokens(core) for a in c.aliases):
                out.append(core)
    return out


def history_matches(works: list[dict], phrases: dict[str, list[list[str]]]) -> dict[str, list[tuple[str, str]]]:
    """key -> [(publication_date, work id)] for works whose raw affiliations contain the name.

    Matching is loose on purpose (the phrase as consecutive words anywhere in
    the line): a false hit only costs a "first paper" claim, never makes one.
    """
    out: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for w in works:
        d, wid = w.get("publication_date") or "", _short_id(w.get("id"))
        if not d or not wid:
            continue
        hit: set[str] = set()
        for a in w.get("authorships") or []:
            for raw in a.get("raw_affiliation_strings") or []:
                toks = _tokens(raw)
                for key, phs in phrases.items():
                    if key not in hit and any(_contains(toks, ph) for ph in phs):
                        hit.add(key)
        for key in hit:
            out[key].append((d, wid))
    return out


def _history_page(phrases: list[str], upper: date) -> tuple[list[dict], bool]:
    """Works whose affiliations contain any phrase, dated up to `upper`, newest first."""
    filt = ("raw_affiliation_strings.search:" + "|".join(f'"{p}"' for p in phrases)
            + f",to_publication_date:{iso(upper)}")
    data = http.get_json(
        f"{OPENALEX}/works",
        params={"filter": filt, "per-page": 200, "sort": "publication_date:desc", "select": HISTORY_SELECT},
        ttl=TTL, timeout=90,
    )
    works = data.get("results") or []
    return works, ((data.get("meta") or {}).get("count") or 0) > len(works)


def check_history(ctx: Context, budget: Budget, companies: list[Company]) -> dict[str, History]:
    """Has OpenAlex any paper under each name older than the first one we hold?

    Each search call covers up to HISTORY_CHUNK names and costs 10 credits.
    Three passes, all newest first:
      1. before the window: names seen have earlier papers; names not seen
         are asked again for the period before the page's oldest date, and
         are "first" only once a page comes back complete with nothing;
      2. for the first-paper candidates only, everything before their first
         paper including the window itself (a paper in another field a month
         earlier would make ours the second), and for one-word names the
         bare word too ("Solivis Co., Ltd." before "Solivis Inc.");
      3. older than five years, for names with earlier papers whose history
         was cut short: three or more old works means an established firm.
    A candidate that cannot be verified is withdrawn, never assumed first.
    """
    hist = {c.key: History() for c in companies}
    first = {c.key: c.papers[0].date for c in companies}
    known = {c.key: {p.id for p in c.papers} | c.duplicate_ids for c in companies}
    by_key = {c.key: c for c in companies}
    text = {c.key: search_phrases(c) for c in companies}
    toks = {k: [_tokens(p) for p in v] for k, v in text.items()}
    old_before = ctx.today - timedelta(days=ESTABLISHED_YEARS * 365)
    todo = sorted(k for k, v in text.items() if v)

    def page(keys: list[str], upper: date, what: str, extra: dict[str, str] | None = None):
        extra = extra or {}
        if not budget.take(10, keep=AUTHOR_RESERVE):
            ctx.warn(f"history check ({what}): credit budget reached, {len(keys)} names left")
            return None
        try:
            works, truncated = _history_page(
                [p for k in keys for p in text[k] + ([extra[k]] if k in extra else [])], upper)
        except Exception as e:  # noqa: BLE001
            ctx.warn(f"history check ({what}) failed for {len(keys)} names: {type(e).__name__}: {str(e)[:120]}")
            return None
        return works, truncated, history_matches(
            works, {k: toks[k] + ([_tokens(extra[k])] if k in extra else []) for k in keys})

    def is_old(d: str) -> bool:
        return (parse_date(d) or ctx.today) < old_before

    def oldest(works: list[dict]) -> date | None:
        return parse_date(min((w.get("publication_date") or "9999") for w in works)) if works else None

    # Pass 1: anything before the window?
    for start in range(0, len(todo), HISTORY_CHUNK):
        unresolved = todo[start:start + HISTORY_CHUNK]
        upper = ctx.since - timedelta(days=1)
        for _round in range(3):
            got = page(unresolved, upper, "earlier papers") if unresolved else None
            if not got:
                break
            works, truncated, found = got
            still = []
            for k in unresolved:
                h = hist[k]
                h.earlier = list(found.get(k, []))
                h.old = sorted({d for d, _ in h.earlier if is_old(d)})
                if h.earlier or not truncated:
                    # Pages run newest to oldest without gaps, so a page that
                    # is not cut short means every earlier work was read.
                    h.checked, h.complete = True, not truncated
                else:
                    still.append(k)
            unresolved = still
            upper = oldest(works) or upper
            if not truncated:
                break

    # Pass 2: first-paper candidates, everything before their first paper.
    bare = {k: _phrase(by_key[k].name) for k in todo
            if hist[k].first and _phrase(by_key[k].name) not in text[k]}
    cands = [k for k in todo if hist[k].first and (first[k] > ctx.since or k in bare)]
    for start in range(0, len(cands), HISTORY_CHUNK):
        ask = cands[start:start + HISTORY_CHUNK]
        upper = max(first[k] for k in ask) - timedelta(days=1)
        for _round in range(2):
            got = page(ask, upper, "first papers", extra=bare) if ask else None
            if not got:
                break
            works, truncated, found = got
            reach = oldest(works)
            again = []
            for k in ask:
                h = hist[k]
                h.earlier = [(d, wid) for d, wid in found.get(k, [])
                             if wid not in known[k] and (parse_date(d) or date.max) < first[k]]
                if h.earlier:
                    h.complete = not truncated
                    h.old = sorted({d for d, _ in h.earlier if is_old(d)})
                elif truncated and (k in bare or not reach or reach > ctx.since):
                    again.append(k)  # the page did not reach far enough back to clear this name
            ask = again
            if not truncated or not reach:
                break
            upper = reach
        for k in ask:  # unverified: withdraw the claim rather than guess
            hist[k].checked = False

    # Pass 3: young or established? Only where it changes what we emit.
    pending = [k for k in todo
               if hist[k].checked and not hist[k].complete and hist[k].established() is None
               and (len(by_key[k].papers) >= 2 or len(by_key[k].authors) >= 2)]
    for start in range(0, len(pending), HISTORY_CHUNK):
        ask = pending[start:start + HISTORY_CHUNK]
        for _round in range(2):
            got = page(ask, old_before - timedelta(days=1), "age") if ask else None
            if not got:
                break
            works, truncated, found = got
            again = []
            for k in ask:
                h = hist[k]
                h.old = sorted({*h.old, *(d for d, _ in found.get(k, []))})
                if h.is_old() or not truncated:
                    h.old_known = True
                else:
                    again.append(k)
            ask = again
            if not truncated:
                break
    return hist


def fetch_authors(ctx: Context, budget: Budget, author_ids: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    ids = [a for a in dict.fromkeys(author_ids) if re.fullmatch(r"A\d+", a)]  # not the "name:..." stand-ins
    for n, start in enumerate(range(0, len(ids), 100)):
        if n >= AUTHOR_BATCHES or not budget.take(1):
            ctx.warn(f"author lookup: stopped after {n} batches, {len(ids) - start} authors without a profile")
            break
        batch = ids[start:start + 100]
        try:
            data = http.get_json(
                f"{OPENALEX}/authors",
                params={"filter": "openalex:" + "|".join(batch), "per-page": 100, "select": AUTHOR_SELECT},
                ttl=TTL, timeout=60,
            )
        except Exception as e:  # noqa: BLE001
            ctx.warn(f"author lookup failed for {len(batch)} authors: {type(e).__name__}: {str(e)[:120]}")
            continue
        for a in data.get("results") or []:
            out[_short_id(a.get("id"))] = a
    return out


# ---------------------------------------------------------------------------
# Crossref: the day a journal paper really went online
# ---------------------------------------------------------------------------


def crossref_online_date(record: dict) -> date | None:
    """The publisher's own online date, when Crossref holds it to the day.

    OpenAlex dates some papers by the day the DOI was registered: Physical
    Review registers at acceptance, three weeks before the paper appears.
    A month-only date (an issue date) says less than OpenAlex and is ignored.
    """
    for k in ("published-online", "published"):
        parts = ((record.get(k) or {}).get("date-parts") or [[]])[0] or []
        if len(parts) == 3 and all(isinstance(x, int) for x in parts):
            try:
                return date(*parts)
            except ValueError:
                return None
    return None


def fix_dates(ctx: Context, companies: list[Company]) -> list[Company]:
    """Replace OpenAlex's date by Crossref's online date on journal papers, then re-apply the window."""
    papers = {p.id: p for c in companies for p in c.papers if p.doi and "," not in p.doi}  # "," splits the filter
    dois = sorted({p.doi.lower() for p in papers.values()})
    online: dict[str, date] = {}
    for start in range(0, len(dois), CROSSREF_CHUNK):
        chunk = dois[start:start + CROSSREF_CHUNK]
        try:
            data = http.get_json(
                CROSSREF,
                params={"filter": ",".join(f"doi:{d}" for d in chunk), "rows": len(chunk),
                        "select": "DOI,published-online,published"},
                ttl=TTL, timeout=40, retries=1,  # a fresh paper's record still changes, so not longer
            )
        except Exception as e:  # noqa: BLE001 - OpenAlex's dates stand
            ctx.warn(f"crossref date check failed for {len(chunk)} papers: {type(e).__name__}: {str(e)[:100]}")
            continue
        for item in ((data.get("message") or {}).get("items") or []):
            d = crossref_online_date(item)
            if d and item.get("DOI"):
                online[item["DOI"].lower()] = d
    moved = 0
    for p in papers.values():
        d = online.get(p.doi.lower())
        if d and d != p.date:
            p.date, moved = d, moved + 1
    if moved:
        ctx.log(f"crossref: {moved} of {len(papers)} journal papers re-dated to the publisher's online date")
    out = []
    for c in companies:
        c.papers = sorted((p for p in c.papers if ctx.since <= p.date <= ctx.today), key=lambda p: (p.date, p.id))
        if c.papers:
            out.append(c)
    return out


# ---------------------------------------------------------------------------
# arXiv pages: who the paper itself puts at the company
# ---------------------------------------------------------------------------

# LaTeXML, which renders arxiv.org/html pages, marks up the author block in a
# few shapes. Three of them say who is where, and only those are trusted:
#   own     one author per block, with that author's own affiliation lines
#   marker  "Ann Lee^1,2, Bo Li^2" with "^1 Tsinghua University ^2 Acme AI"
#   line    a run of names followed by one affiliation line (physics style)
# A list of names over an unnumbered list of affiliations says nothing, and
# neither does a block that follows authors who have none: LaTeXML hangs a
# block shared by a group on the group's last author ("Sicen Li, Zhen Chu,
# Chao Li, Qiuguo Zhu and Jun Wu" over "Zhejiang University ... Yunshenchu
# Technology Co., Ltd. ..." puts all of it under Jun Wu, who is not the one
# at the company).


@dataclass
class Creator:
    """One author block: the name line and the affiliation lines, as (kind, text) tokens."""
    name: list[tuple[str, str]] = field(default_factory=list)  # kind: "text", "sup" or "br"
    contacts: list[list[tuple[str, str]]] = field(default_factory=list)


class _AuthorBlock(HTMLParser):
    """Reads <div class="ltx_authors"> into Creators. Footnotes, emails and labels are left out."""

    _VOID = {"br", "img", "hr", "meta", "link", "input", "wbr"}
    _AFFIL = {"ltx_role_affiliation", "ltx_role_address", "ltx_role_institution", "ltx_role_institutetext"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.creators: list[Creator] = []
        self._stack: list[tuple[str, tuple[str, list | None]]] = []  # (tag, mode before it opened)
        self._mode: tuple[str, list | None] = ("out", None)  # (mode, token list being filled)
        self._done = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._done:
            return
        classes = set((dict(attrs).get("class") or "").split())
        mode, target = self._mode
        if tag in self._VOID:
            if tag == "br" and mode in ("name", "contact") and target is not None:
                target.append(("br", ""))
            return
        if mode == "out":
            if "ltx_authors" not in classes:
                return
            self._stack.append((tag, self._mode))
            self._mode = ("block", None)
            return
        self._stack.append((tag, self._mode))
        if mode == "skip":
            return
        if "ltx_creator" in classes:
            self.creators.append(Creator())
            self._mode = ("creator", None)
        elif "ltx_note" in classes or "ltx_contact_name" in classes or tag.startswith("annotation"):
            self._mode = ("skip", None)  # footnotes, "Affiliation:" labels, TeX source of formulas
        elif "ltx_personname" in classes and self.creators:
            self._mode = ("name", self.creators[-1].name)
        elif "ltx_author_notes" in classes:
            self._mode = ("notes", None)
        elif "ltx_contact" in classes:
            if classes & self._AFFIL and self.creators:
                self.creators[-1].contacts.append([])
                self._mode = ("contact", self.creators[-1].contacts[-1])
            else:
                self._mode = ("skip", None)
        elif tag == "sup" and mode in ("name", "contact"):
            self._mode = ("sup", target)

    def handle_endtag(self, tag: str) -> None:
        if self._done or tag in self._VOID or not any(t == tag for t, _ in self._stack):
            return
        while self._stack:
            t, before = self._stack.pop()
            self._mode = before
            if t == tag:
                break
        if not self._stack:
            self._done = True

    def handle_data(self, data: str) -> None:
        mode, target = self._mode
        if self._done or target is None or not data.strip():
            return
        if mode == "sup":
            target.append(("sup", data.strip()))
        elif mode in ("name", "contact"):
            target.append(("text", data))


def read_author_block(page: str) -> list[Creator]:
    parser = _AuthorBlock()
    try:
        parser.feed(page)
    except Exception:  # noqa: BLE001 - a page that does not parse verifies nobody
        return []
    return parser.creators


def _markers(sup: str) -> list[str]:
    """Affiliation labels in a superscript: "1,2" -> ["1", "2"], "1†" -> ["1"]. Symbols are not labels."""
    low = _fold(sup)
    if re.search(r"[a-z]{3}", low):  # a word, not a label
        return []
    return re.findall(r"\d{1,2}|[a-z]", low)


def _items(tokens: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Token list -> words ("w", folded), punctuation ("p"), superscripts ("sup") and breaks ("br")."""
    out: list[tuple[str, str]] = []
    for kind, text in tokens:
        if kind != "text":
            out.append((kind, text))
            continue
        for piece in re.findall(r"\w+|[^\w\s]", text):
            toks = _tokens(piece)
            out.extend(("w", t) for t in toks) if toks else out.append(("p", piece))
    return out


def _plain(tokens: list[tuple[str, str]]) -> str:
    return re.sub(r"\s+", " ", " ".join(t for k, t in tokens if k == "text")).strip()


_NAME_GLUE = {".", "-", "'", "’", "‐"}  # punctuation allowed inside a printed name
_FILLER = {"and", "the", "by"}  # words allowed between two names in a run
_OTHER_ORG = re.compile(r"universi|institut|college|school|schule|laborator|academ|hospital|polytech|politec")


def _find_names(items: list[tuple[str, str]], names: dict[str, list[str]]) -> list[tuple[int, int, str]]:
    """(first item, last item, name key) for each author name printed in the item list."""
    words = [(i, v) for i, (k, v) in enumerate(items) if k == "w"]
    taken: set[int] = set()
    found = []
    for key, toks in sorted(names.items(), key=lambda kv: -len(kv[1])):
        n = len(toks)
        for j in range(len(words) - n + 1):
            span = words[j:j + n]
            if [v for _, v in span] != toks or any(i in taken for i, _ in span):
                continue
            between = [items[i] for a, b in zip(span, span[1:]) for i in range(a[0] + 1, b[0])]
            if any(k != "p" or v not in _NAME_GLUE for k, v in between):
                continue
            taken.update(range(span[0][0], span[-1][0] + 1))
            found.append((span[0][0], span[-1][0], key))
    return sorted(found)


def _label_map(token_lists: list[list[tuple[str, str]]]) -> dict[str, str]:
    """Affiliation label -> the text printed after it, up to the next label or line break."""
    out: dict[str, str] = {}
    clash: set[str] = set()
    for tokens in token_lists:
        label, text = None, []
        for kind, value in [*tokens, ("br", "")]:
            if kind == "text" and label:
                text.append(value)
                continue
            if label and text:
                line = re.sub(r"\s+", " ", " ".join(text)).strip(" ,;")
                if out.get(label, line) != line:
                    clash.add(label)
                out[label] = line
            marks = _markers(value) if kind == "sup" else []
            label, text = (marks[0] if len(marks) == 1 else None), []
    return {k: v for k, v in out.items() if k not in clash and v}


def _after_names(tokens: list[tuple[str, str]], n_words: int) -> list[tuple[str, str]]:
    """What a name line prints after its first `n_words` words: a label list may sit there."""
    out: list[tuple[str, str]] = []
    count = 0
    for kind, text in tokens:
        if count >= n_words:
            out.append((kind, text))
        elif kind == "text":
            count += len(_tokens(text))
    return out


def page_affiliations(creators: list[Creator], author_names: list[str]) -> dict[str, list[tuple[str, str]]]:
    """Author name key -> [(affiliation text, shape)] as the page attaches them.

    `shape` is "own", "marker" or "line" (see the note above). An author the
    page does not clearly place anywhere is simply absent from the result.
    """
    names = {_name_key(n): _tokens(n) for n in author_names if len(_tokens(n)) >= 2}
    parsed = []
    label_sources: list[list[tuple[str, str]]] = []
    for c in creators:
        items = _items(c.name)
        found = _find_names(items, names)
        parsed.append((c, items, found))
        label_sources.extend(c.contacts)
        if found:
            label_sources.append(_after_names(c.name, sum(1 for k, _ in items[:found[-1][1] + 1] if k == "w")))
    labels = _label_map(label_sources)  # a label may be defined under any author block

    out: dict[str, list[tuple[str, str]]] = defaultdict(list)
    after_bare = False  # the block before this one had no affiliation lines of its own
    for c, items, found in parsed:
        shared, after_bare = after_bare, not any(_plain(contact) for contact in c.contacts)
        if not found:
            continue
        marks: dict[int, list[str]] = {}
        for n, (_, last, _key) in enumerate(found):
            j = last + 1
            while j < len(items) and (items[j][0] == "sup" or (items[j][0] == "p" and items[j][1] in ",;")):
                if items[j][0] == "sup" and _markers(items[j][1]):
                    marks.setdefault(n, []).extend(_markers(items[j][1]))
                j += 1
        if marks:  # numbered affiliations
            for n, ms in marks.items():
                for mk in ms:
                    if mk in labels:
                        out[found[n][2]].append((labels[mk], "marker"))
            continue
        covered = {i for a, b, _ in found for i in range(a, b + 1)}
        loose = [v for i, (k, v) in enumerate(items) if k == "w" and i not in covered and v not in _FILLER]
        if len(found) == 1 and not loose:  # one author, own affiliation lines
            for contact in c.contacts:
                text = _plain(contact)
                if text and not shared:
                    out[found[0][2]].append((text, "own"))
            continue
        # Runs of names, each followed by one line of text.
        run: list[str] = []
        for n, (_, last, key) in enumerate(found):
            run.append(key)
            stop = found[n + 1][0] if n + 1 < len(found) else len(items)
            gap = items[last + 1:stop]
            if not any(k == "w" and v not in _FILLER for k, v in gap):
                continue  # the next name follows directly: same run
            line: list[str] = []
            for k, v in gap:
                if k == "br" and line:
                    break
                if k == "w" and (line or v not in _FILLER):
                    line.append(v)
            for who in run:
                out[who].append((" ".join(line), "line"))
            run = []
    return dict(out)


def attached(contexts: list[tuple[str, str]], phrases: list[list[str]], company_only: bool,
             who: list[str] | None = None) -> bool:
    """Does the page put this author (name tokens `who`) at the company?

    The affiliation text must name the company and no school or institute
    beside it: "Differential Robotics, Hangzhou; Zhejiang University,
    Hangzhou" under one name does not say which of the two the person is at.
    A footnote sentence ("A. Lee and B. Li are with Acme AI. C. Wu is with
    ...") counts only for the people its own sentence names. For the "line"
    shape the company must also open the line, and OpenAlex must show the
    author with the company alone.
    """
    for text, shape in contexts:
        if _OTHER_ORG.search(_fold(text)):
            continue
        parts = [text]
        if re.search(r"\b(is|are|was|were) with\b", text):
            parts = [x for x in re.split(r"(?<=\w{3})[.;]\s+(?=[A-Z])", text) if who and who[-1] in _tokens(x)]
        for part in parts:
            toks = _tokens(part)
            for ph in phrases:
                if shape == "line":
                    if toks[:len(ph)] == ph and company_only:
                        return True
                elif _contains(toks, ph):
                    return True
    return False


def company_phrases(c: Company) -> list[list[str]]:
    out = []
    for name in [c.name, *c.aliases]:
        toks = _tokens(_phrase(name))
        if toks and toks not in out:
            out.append(toks)
    return out


def check_page(page: str, c: Company, paper: Paper) -> list[Mention]:
    """The company's authors on one arXiv paper that the paper's HTML page bears out."""
    phrases = company_phrases(c)
    if len(paper.author_names) > 200:
        return []  # a collaboration paper: not worth matching hundreds of names
    where = page_affiliations(read_author_block(page), paper.author_names)
    kept = []
    for m in paper.mentions.get(c.key, []):
        mine = where.get(_name_key(m.author_name), [])
        if attached(mine, phrases, m.company_only, _tokens(m.author_name)):
            m.others = [o for o in m.others if _inst_verified(o, [t for t, _ in mine])]
            kept.append(m)
    return kept


def verify_arxiv(ctx: Context, companies: list[Company]) -> None:
    """Keep, on arXiv papers, only the authors the paper's own page places at the company.

    A paper with no HTML version, or with an author block that does not say
    who is where, keeps its place in the company's count but names nobody
    (LaTeXML drops the affiliations of some templates altogether, so a page
    that is silent proves nothing against the paper).
    """
    pages: dict[str, str | None] = {}
    checked = borne_out = failed = 0
    for c in companies:
        for p in c.papers:
            if not p.arxiv_id or c.key not in p.mentions:
                continue
            if p.arxiv_id not in pages:
                try:
                    pages[p.arxiv_id] = http.get(ARXIV_HTML + p.arxiv_id, ttl=TTL_SLOW, timeout=60, retries=1)
                except Exception as e:  # noqa: BLE001 - many papers have no HTML version (404)
                    pages[p.arxiv_id] = None
                    if getattr(e, "status", None) not in (404, 410):
                        failed += 1
                        if failed <= 3:
                            ctx.warn(f"arxiv page {p.arxiv_id}: {type(e).__name__}: {str(e)[:100]}")
            page = pages[p.arxiv_id]
            checked += 1
            try:
                kept = check_page(page, c, p) if page and "ltx_authors" in page else []
            except Exception as e:  # noqa: BLE001
                ctx.warn(f"arxiv page {p.arxiv_id} not understood: {type(e).__name__}: {e}")
                kept = []
            p.mentions[c.key] = kept
            # Who is where now comes from the page, so OpenAlex giving every
            # line to every author no longer matters for this paper.
            p.uniform = False
            if kept:
                borne_out += 1
            else:
                p.unverified.add(c.key)
    if failed > 3:
        ctx.warn(f"arxiv pages: {failed} could not be fetched, their authors are left out")
    if checked:
        ctx.log(f"arxiv pages: authors borne out on {borne_out} of {checked} company papers")


# ---------------------------------------------------------------------------
# Authors -> people
# ---------------------------------------------------------------------------


# Fields a researcher in each paper field plausibly also publishes in.
_FIELD_KIN = {
    "Computer Science": {"Computer Science", "Engineering", "Mathematics"},
    "Mathematics": {"Computer Science", "Engineering", "Mathematics"},
    "Engineering": {"Engineering", "Computer Science", "Mathematics", "Materials Science", "Energy"},
    "Physics and Astronomy": {"Physics and Astronomy", "Engineering", "Energy", "Materials Science"},
    "Energy": {"Energy", "Engineering", "Materials Science", "Chemistry", "Chemical Engineering", "Physics and Astronomy"},
    "Materials Science": {"Materials Science", "Engineering", "Energy", "Chemistry", "Chemical Engineering", "Physics and Astronomy"},
    "Chemistry": {"Chemistry", "Materials Science", "Energy", "Chemical Engineering"},
    "Chemical Engineering": {"Chemical Engineering", "Chemistry", "Materials Science", "Energy", "Engineering"},
}


def profile_fits(profile: dict, fields: set[str]) -> bool:
    """Does the profile's publication record sit in the field of the company's papers?

    OpenAlex merges namesakes: the "Shang Su" on a Tsinghua robotics paper
    resolves to a prostate-cancer researcher's profile with h-index 8. At
    least half of the profile's topic counts must be in the paper's field
    or a neighbouring one, otherwise the record belongs to someone else.
    """
    topics = profile.get("topics") or []
    total = sum(t.get("count") or 0 for t in topics)
    if not total or not fields:
        return False
    ok = set().union(*(_FIELD_KIN.get(f, {f}) for f in fields))
    hit = sum(t.get("count") or 0 for t in topics if ((t.get("field") or {}).get("display_name")) in ok)
    return hit / total >= 0.5


def two_careers(solid: list[dict]) -> bool:
    """Do two long affiliations in different countries overlap for two years or more?

    That is what a merged profile looks like: the "Ziming Ding" on a
    Zhejiang University drone paper resolves to a profile that also sits at
    Karlsruhe and Darmstadt from 2021 to 2026, with a battery chemist's
    h-index. A real move abroad overlaps for a year at most. Genuine double
    appointments across borders are lost too; that is the cheaper error.
    """
    for i, a in enumerate(solid):
        for b in solid[i + 1:]:
            ca, cb = (a.get("institution") or {}).get("country_code"), (b.get("institution") or {}).get("country_code")
            if ca and cb and ca != cb and len(set(a.get("years") or []) & set(b.get("years") or [])) >= 2:
                return True
    return False


def names_agree(printed: str, profile_name: str) -> bool:
    """Is the profile's name the printed name, allowing initials for given names?

    "J. Gaffney" and "Jim Gaffney" agree; "Shang Su" and "Su Shang" do not
    (family names differ), and neither do "Wei Wang" and "Weiguo Wang".
    """
    a, b = _tokens(printed), _tokens(profile_name)
    if not a or not b or a[-1] != b[-1]:
        return False
    ga, gb = a[:-1], b[:-1]
    if not ga or not gb:
        return False
    x, y = ga[0], gb[0]
    return x == y or (len(x) == 1 and y.startswith(x)) or (len(y) == 1 and x.startswith(y))


def author_facts(profile: dict | None, today: date, fields: set[str]) -> tuple[dict[str, Any], list[str]]:
    """(facts, previous institutions) from an OpenAlex author profile.

    Returns ({}, []) unless the profile is clearly this person's own record:
    not a fragment (minted in the last 60 days, under five works or an
    h-index under 3: 48% of recent robotics authorships point at one), not
    several careers merged under a common name, and in the right field.
    Previous institutions need three distinct publication years, the last
    within eight years, which drops one-off mis-matches such as "Machine
    Science" for a University of Maryland centre.
    """
    if not profile:
        return {}, []
    works = profile.get("works_count") or 0
    created = parse_date((profile.get("created_date") or "")[:10])
    h = (profile.get("summary_stats") or {}).get("h_index") or 0
    if works < 5 or h < 3 or (created and (today - created).days <= 60):
        return {}, []
    solid = [
        a for a in profile.get("affiliations") or []
        if len(set(a.get("years") or [])) >= 3 and (a.get("institution") or {}).get("display_name")
    ]
    if works > 600 or len(solid) > 6 or two_careers(solid) or not profile_fits(profile, fields):
        return {}, []
    facts = {"h_index": h, "cited_by_count": profile.get("cited_by_count") or 0, "works_count": works}
    solid.sort(key=lambda a: -max(a["years"]))
    prev = [a["institution"]["display_name"] for a in solid if max(a["years"]) >= today.year - 8][:3]
    return facts, prev


def _name_key(name: str) -> str:
    """"C. Day" and "Day C." are one person; OpenAlex often gives them two ids."""
    return " ".join(sorted(_tokens(name)))


def _one_person(a: dict, b: dict) -> dict:
    """Two records of one person: keep the one with a trusted profile, else the first (fuller) name."""
    keep, drop = (b, a) if b["facts"] and not a["facts"] else (a, b)
    keep["others"] += [o for o in drop["others"] if o not in keep["others"]]
    keep["positions"] |= drop["positions"]
    keep["papers"] |= drop["papers"]
    keep["only"] = keep["only"] and drop["only"]
    return keep


def build_people(c: Company, profiles: dict[str, dict], today: date) -> tuple[list[Person], dict[str, Any]]:
    """The company's authors as people, and a summary of the team.

    A person's name is the one printed on the paper. The OpenAlex profile
    (its fuller name, its link, h-index and earlier institutions) is used
    only when it passes author_facts, carries the same name and is tied to
    this author by something the paper itself shows: the ORCID the
    publisher printed, or a co-affiliation that the profile also lists.
    Otherwise nothing from it is shown, not even the link, because it may
    be somebody else's record ("Peter Stadler" resolves to a
    bioinformatician called Peter F. Stadler).
    """
    by_name: dict[str, dict] = {}
    for aid, hits in c.authors.items():
        fields = {p.topic_field for p, _ in hits if p.topic_field}
        printed = hits[0][1].author_name
        profile = profiles.get(aid)
        facts, prev = author_facts(profile, today, fields)
        others: list[str] = []
        for _, m in hits:
            for o in m.others:
                if o not in others:
                    others.append(o)
        homes = {(a.get("institution") or {}).get("display_name") for a in (profile or {}).get("affiliations") or []}
        tied = any(m.orcid_match for _, m in hits) or any(o in homes for o in others)
        trusted = bool(facts) and tied and names_agree(printed, (profile or {}).get("display_name") or "")
        if not trusted:
            facts, prev = {}, []
        # The printed name stands unless it gives only an initial and the
        # trusted profile spells the name out ("J. Gaffney" -> "Jim Gaffney").
        # A middle initial the paper does not print is not added.
        full = ((profile or {}).get("display_name") or "").strip()
        initial_only = len((_tokens(printed) or ["xx"])[0]) == 1 and len((_tokens(full) or ["x"])[0]) > 1
        shown = full if trusted and initial_only else printed
        positions = {m.position for _, m in hits}
        row = {
            "aid": aid if trusted else None, "facts": facts, "prev": prev, "others": others,
            "name": shown, "printed": printed,
            "positions": positions, "papers": {p.id for p, _ in hits},
            "only": all(m.company_only for _, m in hits),
        }
        key = _name_key(printed) or aid
        cur = by_name.get(key)
        by_name[key] = row if cur is None else _one_person(cur, row)
    # "A. Herrmann" on one paper and "Albrecht Herrmann" on the next are one
    # person at one company. Merging can only lower the count, which is a floor.
    rows: list[dict] = []
    for row in sorted(by_name.values(), key=lambda r: (-len(_tokens(r["printed"])), -len(r["printed"]))):
        twin = next((r for r in rows if names_agree(row["printed"], r["printed"])), None)
        if twin is None:
            rows.append(row)
        else:
            rows[rows.index(twin)] = _one_person(twin, row)
    for r in rows:
        r["h"] = r["facts"].get("h_index", 0)
        r["rank"] = 0 if "last" in r["positions"] else 1 if "first" in r["positions"] else 2
    rows.sort(key=lambda r: (-r["h"], -len(r["papers"]), r["rank"], r["name"]))
    people = [
        Person(
            name=r["name"],
            role="Last author" if r["rank"] == 0 else "First author" if r["rank"] == 1 else "Author",
            openalex_id=r["aid"],
            affiliations=[c.name] + r["others"] + [p for p in r["prev"] if p not in r["others"]],
            links={"openalex": f"https://openalex.org/{r['aid']}"} if r["aid"] else {},
            facts=dict(r["facts"]),
        )
        for r in rows[:MAX_PEOPLE]
    ]
    summary = {
        "company_authors": len(rows),
        "company_only_authors": sum(1 for r in rows if r["only"]),
        "dual_affiliation_authors": sum(1 for r in rows if r["others"]),
        "max_author_h_index": max((r["h"] for r in rows), default=0),
    }
    return people, summary


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------


def _short(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1].rsplit(" ", 1)[0].rstrip(" ,;:-") + "…"


def _signal_text(main: Paper, papers: list[Paper], topic: bool = True) -> str:
    """Title and abstract of one paper, OpenAlex's topic for it, then the titles of the company's other papers."""
    others = " | ".join(p.title for p in papers if p.id != main.id)
    note = f" {topic_note(main.topic)}" if topic and main.topic else ""
    return _short(main.text, 1400) + note + (f" Other papers: {_short(others, 500)}" if others else "")


def company_signals(c: Company, hist: History, ror: dict | bool | None,
                    profiles: dict[str, dict], ctx: Context) -> list[Signal]:
    """Signals for one company.

    Emits nothing for names ROR dates more than five years back, names
    publishing for more than five years, and names whose history could not
    be checked: the point of this source is the young company, and an
    unverified one is a guess.
    """
    today = ctx.today
    record = ror if isinstance(ror, dict) else None
    if (record and not ror_is_young(record, today)) or not hist.checked:
        return []
    is_first = hist.first
    if not is_first and hist.established() is not False:
        return []
    earlier_dates = sorted(d for d, _ in hist.earlier)
    # Author counts are floors, hence "at least" in every title: OpenAlex
    # leaves some authors without any affiliation line, and on arXiv papers
    # only authors the paper's own page places at the company are counted
    # (see verify_arxiv). A paper whose authors could not be checked still
    # counts as a paper, with nobody named.
    people, team = build_people(c, profiles, today)
    n_auth = team["company_authors"]
    senior = team["max_author_h_index"]
    # How sure are we that the string is a company: a legal form, or
    # several people writing the same name, or one person and a hint word.
    conf = 1.0 if c.legal or n_auth >= 3 else 0.85 if n_auth == 2 else 0.55
    entity = EntityHint(
        name=c.name, kind="company", aliases=c.aliases[:6], location=c.location,
        links={"paper": c.papers[-1].url},
    )
    if record and ror_same_place(record, c.location):
        # The registry, not a guess, supplies the website and founding year.
        entity.domain = clean_domain(ror_website(record))
        entity.founded = str(record["established"])
        if record.get("id"):
            entity.links["ror"] = record["id"]
    base_metrics: dict[str, Any] = {
        "papers_window": len(c.papers),
        "company_authors": n_auth,
        "company_only_authors": team["company_only_authors"],
        "dual_affiliation_authors": team["dual_affiliation_authors"],
        "has_legal_suffix": int(c.legal),
        "papers_authors_unchecked": sum(1 for p in c.papers if c.key in p.unverified),
    }
    if senior:
        base_metrics["max_author_h_index"] = senior
    if ror is not None:
        base_metrics["in_ror"] = int(bool(record))
    if hist.complete:
        # Works whose affiliation lines contain the same name. A namesake
        # would be counted too, so this stays a metric and out of the title.
        base_metrics["earlier_papers_same_name"] = len(earlier_dates)
    made: list[tuple[Signal, Paper]] = []  # each signal with the paper its text is taken from

    first = c.papers[0]
    if is_first:
        on_paper = 0 if first.uniform else len({_name_key(m.author_name) for m in first.mentions.get(c.key, [])})
        # "First" is as far as OpenAlex's index goes, and the title says so.
        # One checked author on somebody else's paper is routine: it is how
        # a decades-old supplier shows up too. Two or more people, a senior
        # author or a second paper make it a solid step; a large team with
        # all three is rare.
        team_part = 1 - (1 - 0.9 * squash(max(n_auth - 1, 0), 3)) * (1 - 0.5 * squash(senior, 20)) * (
            1 - (0.25 if len(c.papers) >= 2 else 0.0))
        base = 0.38 if n_auth >= 2 else 0.28 if n_auth == 1 else 0.22
        strength = round(max(0.15, min(0.97, conf * (base + 0.62 * team_part))), 3)
        lead = "First indexed paper under the company name"
        if first.n_authors == 1:
            title = f"{lead}, by a single author"
        elif on_paper and on_paper >= first.n_authors:
            title = f"{lead}, with all {first.n_authors} authors listing it"
        elif on_paper:
            title = f"{lead}, with at least {on_paper} of {first.n_authors} authors listing it"
        elif first.n_authors:
            title = f"{lead}, a {first.n_authors}-author paper"
        else:
            title = lead
        made.append((Signal(
            source=SLUG, family=FAMILY, kind="company_first_paper", entity=entity, title=title,
            occurred_at=iso(first.date), url=first.url,
            value=on_paper or first.n_authors, unit="company authors" if on_paper else "authors on paper",
            strength=strength,
            metrics={**base_metrics, "authors_on_paper": first.n_authors, "company_authors_on_paper": on_paper},
            people=people, text=_signal_text(first, c.papers),
        ), first))

    n = len(c.papers)
    if n >= 2 or not is_first:
        last = c.papers[-1]
        days = ctx.lookback_days
        novelty = 1.0 / (1.0 + len(earlier_dates) / 15.0)
        # Several papers from one person count for less than from a team.
        team_factor = 0.5 + 0.5 * squash(max(n_auth - 1, 0), 2)
        strength = round(max(0.15, min(0.8, conf * (0.2 + 0.5 * squash(n - 1, 3) * team_factor
                                                    + 0.15 * squash(n_auth, 6)) * (0.6 + 0.4 * novelty))), 3)
        # "At least": only thesis-topic sources were read, and see the note
        # on author counts above.
        papers = "1 paper" if n == 1 else f"{n} papers"
        if n_auth:
            people_part = "1 author" if n_auth == 1 else f"{n_auth} authors"
            title = f"At least {papers} and {people_part} list the company as an affiliation in the last {days} days"
        else:
            title = (f"At least {papers} list{'s' if n == 1 else ''} the company as an affiliation "
                     f"in the last {days} days")
        # Cumulative count, one point per day: three papers on one day are one step to 3.
        by_day: dict[str, int] = {}
        for i, p in enumerate(c.papers):
            by_day[iso(p.date)] = i + 1
        series = [{"t": t, "v": v} for t, v in by_day.items()]
        best = max(c.papers, key=lambda p: (p.fit, p.date))  # the abstract that shows why it is on thesis
        made.append((Signal(
            source=SLUG, family=FAMILY, kind="company_papers", entity=entity, title=title,
            occurred_at=iso(last.date), url=last.url, value=n, unit=f"papers/{days}d",
            # A count over a moving window, dated by its newest paper: one
            # stored row per company that each run refreshes, not a new row
            # every time another paper appears.
            strength=strength, metrics={**base_metrics, "rolling": True}, people=people,
            series=series if len(series) >= 2 else [], text=_signal_text(best, c.papers),
        ), best))
    # Rule 7, on the text the pipeline will classify. The topic sentence in
    # that text counts only the way on_thesis lets it.
    return [s for s, main in made
            if on_thesis(f"{s.entity.name} {s.title} {_signal_text(main, c.papers, topic=False)}", main.topic)]


# ---------------------------------------------------------------------------
# Hugging Face daily papers: on-thesis papers claimed by an organisation
# ---------------------------------------------------------------------------

# University handles that the affiliation rules do not know as words, and
# universities whose short name ends like a company's ("Virginia Tech").
_HF_ACADEMIC = re.compile(
    r"\b(mit|cmu|eth|epfl|kaist|hkust|hku|cuhk|ntu|nus|snu|tum|dtu|fau|kit|rwth|zju|hust|sjtu|"
    r"ustc|thu|pku|nju|hit|fdu|ucb|ucla|ucsd|uiuc|umd|nyu|mila|ai2|stanford|berkeley|harvard|"
    r"princeton|caltech|oxford|cambridge|tsinghua|peking|fudan|zhejiang|bench|benchmark|"
    r"dataset|leaderboard|challenge|club|community|open[- ]source|"
    r"(?:virginia|georgia|texas|michigan|illinois|louisiana) tech)\b|^(zju|thu|pku|hku|ntu|tum|mit|cmu)"
)

def hf_signal(item: dict, since: date, today: date) -> Signal | None:
    """One daily_papers item -> a signal when a company claimed an on-thesis paper.

    A Hugging Face organisation can be a company, a university lab, a
    benchmark or a fan club. Only names that pass the same test as an
    affiliation line (a legal form or a company-like last word, no school
    or lab words) are kept, so "Knowin AI" and "Bagel Labs" pass and
    "RoboDojo-Benchmark" and "TUM - Professorship ..." do not.
    """
    paper = item.get("paper") or {}
    org = item.get("organization") or paper.get("organization") or {}
    pid = (paper.get("id") or "").strip()
    fullname = re.sub(r"\s+", " ", org.get("fullname") or "").strip()
    handle = (org.get("name") or "").strip()
    if not pid or not fullname or not handle:
        return None
    # The paper's own date (midnight UTC, the day the paper page prints). The
    # item-level publishedAt is four hours earlier and falls on the day before.
    d = parse_date((paper.get("publishedAt") or "")[:10])
    if not d or d < since or d > today:
        return None
    affil = clean_affiliation(fullname)
    if not affil or not affil.strong or affil.name != fullname.rstrip("."):
        return None
    if _HF_ACADEMIC.search(_fold(fullname)) or _HF_ACADEMIC.search(_fold(handle)):
        return None
    title = re.sub(r"\s+", " ", paper.get("title") or item.get("title") or "").strip().rstrip(". ")
    summary = re.sub(r"\s+", " ", paper.get("summary") or item.get("summary") or "").strip()
    if not title:
        return None
    upvotes = int(paper.get("upvotes") or 0)
    stars = paper.get("githubStars")
    repo = (paper.get("githubRepo") or "").strip()
    links = {"huggingface": f"https://huggingface.co/{handle}", "paper": f"https://arxiv.org/abs/{pid}"}
    github = None
    m = re.match(r"https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", repo)
    if m:
        # The repo and project page are the paper's and often an author's own
        # ("JethroJames/awesome-robots-icl"). They become the company's links,
        # and the owner its GitHub login, only when the owner carries its name.
        squashed = lambda x: re.sub(r"[^a-z0-9]", "", x.lower())  # noqa: E731
        if squashed(m.group(1)) in (squashed(handle), squashed(fullname)):
            github = m.group(1)
            links["repo"] = repo
            if (paper.get("projectPage") or "").startswith("http"):
                links["project_page"] = paper["projectPage"]
    else:
        stars = None
    metrics: dict[str, Any] = {"hf_upvotes": upvotes, "hf_comments": int(item.get("numComments") or 0)}
    if isinstance(stars, int):
        metrics["paper_repo_stars"] = stars
    bits = f"{upvotes} upvote" + ("" if upvotes == 1 else "s")
    strength = round(min(0.8, 0.18 + 0.5 * squash(upvotes, 60) + 0.2 * squash(stars or 0, 400)), 3)
    sig = Signal(
        source=SLUG, family=FAMILY, kind="hf_paper_with_org",
        entity=EntityHint(name=fullname, kind="company", github=github, links=links),
        # The date is the paper's, as the linked page prints it ("Published on Sep 28");
        # the repo is the paper's and may be an author's own, so its stars stay a metric.
        title=_short(f"Paper reached {bits} on Hugging Face daily papers: {title}", 109),
        occurred_at=iso(d), url=f"https://huggingface.co/papers/{pid}",
        value=upvotes, unit="upvotes", strength=strength, metrics=metrics,
        text=_short(f"{title}. {summary}", 1500),
    )
    # This lens is for robotics and autonomy papers, and it has no author
    # affiliations to lean on, so it wants more than one stray keyword.
    sectors = classify(f"{sig.entity.name} {sig.title} {sig.text}")["sectors"]
    if max(sectors.get("robotics", 0.0), sectors.get("autonomy", 0.0)) < 0.55:
        return None
    return sig


def _iso_weeks(since: date, today: date) -> list[str]:
    out, d = [], since
    while d <= today:
        y, w, _ = d.isocalendar()
        tag = f"{y}-W{w:02d}"
        if tag not in out:
            out.append(tag)
        d += timedelta(days=1)
    return list(reversed(out))  # newest first


def collect_hf(ctx: Context, cap: int | None) -> Iterable[Signal]:
    weeks = _iso_weeks(ctx.since, ctx.today)
    if cap is not None and cap <= 20:
        weeks = weeks[:3]  # a quick probe reads the last three weeks only
    fresh = set(weeks[:2])  # upvotes still move on the last two weeks
    best: dict[str, Signal] = {}
    for week in weeks:
        for page in range(6):
            try:
                items = http.get_json(
                    HF, params={"week": week, "limit": 100, "p": page},
                    ttl=TTL if week in fresh else TTL_SLOW, timeout=40,
                )
            except Exception as e:  # noqa: BLE001
                ctx.warn(f"hf daily_papers {week} p{page}: {type(e).__name__}: {str(e)[:100]}")
                break
            if not isinstance(items, list) or not items:
                break
            for item in items:
                try:
                    sig = hf_signal(item, ctx.since, ctx.today)
                except Exception as e:  # noqa: BLE001
                    ctx.warn(f"hf item skipped: {type(e).__name__}: {e}")
                    continue
                if sig:
                    best.setdefault(sig.url, sig)
            if len(items) < 100:
                break
    out = sorted(best.values(), key=lambda s: -s.strength)
    # Several papers by one org in the window: keep each paper, strongest first.
    return out[:cap] if cap is not None else out


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def collect(ctx: Context) -> Iterable[Signal]:
    budget = Budget()
    signals: list[Signal] = []
    try:
        works = fetch_works(ctx, budget)
        papers: dict[str, Paper] = {}
        for w in works:
            try:
                p = parse_work(w)
            except Exception as e:  # noqa: BLE001 - one bad row must not lose the run
                ctx.warn(f"work {w.get('id')} skipped: {type(e).__name__}: {e}")
                continue
            if p:
                papers.setdefault(p.id, p)
        companies = group_companies(papers.values(), ctx.since, ctx.today)
        ctx.log(f"openalex: {len(papers)} papers carry an unmatched company-like affiliation, {len(companies)} companies")
        if ctx.limit is not None:
            companies = companies[: ctx.limit]
        ror = check_ror(ctx, companies)
        companies = [c for c in companies
                     if not isinstance(ror.get(c.key), dict) or ror_is_young(ror[c.key], ctx.today)]
        companies = fix_dates(ctx, companies)
        hist = check_history(ctx, budget, companies)
        live = [c for c in companies if hist[c.key].first
                or (hist[c.key].checked and hist[c.key].established() is False)]
        ctx.log(f"openalex: {len(companies)} names checked, {len(live)} are young companies "
                f"({sum(1 for c in live if hist[c.key].first)} with no earlier paper)")
        verify_arxiv(ctx, live)
        profiles = fetch_authors(ctx, budget, [aid for c in live for aid in c.authors])
        for c in live:
            try:
                signals.extend(company_signals(c, hist[c.key], ror.get(c.key), profiles, ctx))
            except Exception as e:  # noqa: BLE001
                ctx.warn(f"{c.name}: {type(e).__name__}: {e}")
        ctx.log(f"openalex: {budget.spent} credits planned this run (cap {budget.cap}), {len(signals)} signals")
    except Exception as e:  # noqa: BLE001 - the HF lens should still run
        ctx.warn(f"openalex lens failed: {type(e).__name__}: {e}")
    try:
        signals.extend(collect_hf(ctx, None if ctx.limit is None else max(5, ctx.limit // 3)))
    except Exception as e:  # noqa: BLE001
        ctx.warn(f"hf lens failed: {type(e).__name__}: {e}")
    return signals
