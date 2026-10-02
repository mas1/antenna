"""NRC ADAMS: reactor developers appearing on new pre-application dockets.

ADAMS is the U.S. Nuclear Regulatory Commission's public document library. A
company that wants to license a reactor gets a 99902xxx project docket when it
first engages the regulator, and its letters of intent, Regulatory Engagement
Plans, topical reports and meeting notices are filed there. A new docket shows
up months or years before a licence application or any press, so it is one of
the earliest dated public traces a nuclear startup leaves.

How it works: one search for everything added to a 99902xxx docket inside the
lookback window, one history lookup per docket (first-ever document, lifetime
count, who writes on it), then one signal for each docket whose first document
appeared inside the window and one per notable filing, capped per company.
Long-established vendors, exchange-listed developers, utilities, agencies,
supplier-inspection dockets and dockets more than four years old are skipped:
the point is new entrants.

Dates. ADAMS indexes two dates per document: the date on the document
(`DocumentDate`) and the day it was added to the public library (`DateAdded`),
usually a week or more later.

- A docket-appeared signal is dated by `DateAdded` of the docket's first
  document: that is the day the docket became visible, and the title says
  "appeared", not "opened", because the NRC assigns the number earlier.
- A filing signal ("Submitted ...", "NRC issued ...") is dated by
  `DocumentDate`, which is normally the date printed on the letter in the
  evidence PDF (checked on 49 dated documents: 46 matched, one was two days
  off, one two weeks off, one carried a stale template date). A filing whose
  document date is older than the window is skipped even if it was released
  inside it. Meeting summaries, which ADAMS indexes by meeting day, are titled
  "published" and dated by `DateAdded`.

Titles never state the gap between the two dates: the indexed document date is
occasionally stale (a July 2026 letter indexed under its April template date
was seen), so a "released N months later" claim cannot be made from the index.
Both dates are kept in `metrics`. When a filing became public after the date
on it, `metrics["public_at"]` carries the release day, so the scorer decays
the signal from the day anyone outside the NRC could first read it.

Names. ADAMS writes one company several ways on one docket ('ARC Clean
Technology', 'ARC Clean Energy, LLC'). They are grouped with the shared loose
name key, which ignores one trailing descriptive word; the strict key keeps
those words, so the other spelling is passed to the resolver as an alias.
"""

from __future__ import annotations

import re
import socket
from collections import Counter
from datetime import date, timedelta
from typing import Any, Iterable

from .. import http
from ..models import EntityHint, Person, Signal
from .base import Context, clean_domain, iso, loose_name, normalize_name, parse_date, strip_legal

SLUG = "nrc_adams"
FAMILY = "regulatory"
STAGE = "discover"
DESCRIPTION = "NRC pre-application dockets: newly opened dockets and new filings by reactor startups"

API = "https://adams-search.nrc.gov/api/search"
DETAIL_API = "https://adams-search.nrc.gov/api/search/docdetails/{id}"
RECORD_URL = "https://adams-search.nrc.gov/props/{id}"
PAGE_SIZE = 100  # fixed by the API
MAX_PAGES = 30  # safety stop: 3,000 documents is far beyond any real window
ACTIVITY_CAP = 4  # activity signals per company per run
MAX_DOCKET_AGE_DAYS = 4 * 365  # a company four years into pre-application is not a new entrant
WINDOW_TTL = 3 * 3600
HISTORY_TTL = 12 * 3600
DETAIL_TTL = 30 * 86400  # a filed document never changes

_DOCKET = re.compile(r"^99902\d{3}$")


# --------------------------------------------------------------------------
# Who is on the docket
# --------------------------------------------------------------------------

# Long-established vendors, fuel-cycle majors and their project vehicles.
_INCUMBENTS = [
    r"westinghouse", r"\bge[- ]?hitachi", r"\bge vernova", r"general electric", r"global nuclear fuel",
    r"holtec", r"^smr, llc$", r"nuscale", r"framatome", r"\bareva\b", r"terrapower", r"\bx[- ]?energy\b",
    r"long mott", r"kairos", r"mitsubishi", r"rolls[- ]?royce", r"general atomics", r"atkinsr", r"\bcandu\b",
    r"\bbwxt?\b", r"babcock", r"energy ?solutions", r"curtiss[- ]?wright", r"toshiba", r"hitachi",
    r"korea hydro", r"\bkepco\b", r"\bedf\b", r"orano", r"centrus", r"urenco", r"bechtel", r"\bfluor\b",
]
# Exchange-listed reactor developers: real, but past the point a venture fund can source them.
_LISTED = [r"\boklo\b", r"terrestrial energy", r"terra innovatum", r"nano nuclear", r"lightbridge"]
# Utilities and plant operators.
_UTILITIES = [
    r"duke energy", r"dominion", r"virginia electric", r"\bvepco\b", r"appalachian power",
    r"american electric power", r"\bpseg\b", r"tennessee valley authority", r"constellation", r"exelon",
    r"southern nuclear", r"southern co\b", r"entergy", r"xcel", r"energy northwest", r"ontario power",
    r"nextera", r"florida power", r"vistra", r"talen", r"energy harbor", r"pacific gas", r"\bdte\b",
    r"ameren", r"arizona public service", r"dairyland", r"utah associated municipal", r"pacificorp",
    r"santee cooper", r"nebraska public power", r"evergy", r"firstenergy", r"luminant", r"georgia power",
    r"alabama power", r"wolf creek", r"stp nuclear", r"portland general",
    r"\b(power|electric|light) (co|company|cooperative|authority|district)\b", r"public (power|service|utility)",
]
# Industry bodies, governments, labs and universities: real, but not companies to source.
_NOT_COMPANIES = [
    r"electric power research", r"\bepri\b", r"nuclear energy institute", r"\bnei\b", r"owners group",
    r"\bgovt of\b", r"government of", r"\bus dept\b", r"\bu\.s\. dep", r"department of", r"national lab",
    r"\buniv\b", r"university", r"atomic energy agency", r"\bjaea\b", r"^state of\b", r"\bnavy\b",
    r"\bnaval\b", r"\bair force\b", r"\barmy\b",
]
_STOP = re.compile("|".join(_INCUMBENTS + _LISTED + _UTILITIES + _NOT_COMPANIES), re.I)

# Words that name a corporate vehicle, not a business: 'Aalo Holdings' trades as Aalo.
_VEHICLE_WORDS = {"holdings", "group"}
# First words too common to tie two affiliation strings together on their own.
_GENERIC_FIRST = {"energy", "nuclear", "advanced", "american", "general", "united", "national", "power",
                  "global", "north", "new", "first", "atomic"}


def is_nrc(affiliation: str) -> bool:
    """ADAMS writes the regulator's own offices as 'NRC' or 'NRC/NRR/...'."""
    a = (affiliation or "").strip()
    return a == "NRC" or a.startswith("NRC/") or a.startswith("NRC ")


def _outside(affiliations: Iterable[str] | None) -> list[str]:
    """Affiliations that name someone other than the NRC, as ADAMS wrote them."""
    out = []
    for a in affiliations or []:
        a = " ".join((a or "").split())
        if a and not is_nrc(a) and "no known affiliation" not in a.lower():
            out.append(a)
    return out


def is_stoplisted(name: str) -> bool:
    return bool(_STOP.search(name or ""))


def preapp_dockets(doc: dict) -> list[str]:
    """The 99902xxx project dockets a document is filed on."""
    return [d for d in (doc.get("DocketNumber") or []) if _DOCKET.match(d or "")]


def _tokens(name: str) -> list[str]:
    return normalize_name(name).split()


def _loose_tokens(name: str) -> list[str]:
    """Name words with one trailing descriptive word dropped, for grouping spellings on one docket."""
    return loose_name(name).split()


def _brand_tokens(name: str) -> list[str]:
    """Name words without a trailing corporate-vehicle word: 'Aalo Holdings, Inc' gives ['aalo']."""
    t = _tokens(name)
    while len(t) > 1 and t[-1] in _VEHICLE_WORDS:
        t = t[:-1]
    return t


def has_legal_form(name: str) -> bool:
    """Does the string end in a legal form ('Inc', 'LLC', 'Co., Ltd', 's.r.l')?"""
    s = (name or "").strip(" ,.")
    return bool(s) and strip_legal(s) != s


def _related(a: str, b: str) -> bool:
    """Two affiliation strings on one docket that plausibly name the same company.

    'Blue Energy' and 'Blue Energy Global, Inc' (one is a word-prefix of the
    other), 'ARC Clean Technology' and 'ARC Clean Energy, LLC' or 'Aalo
    Holdings, Inc' and 'AALO Atomics' (the same once a trailing descriptive
    word is set aside), or 'newcleo Group' and 'newcleo Americas, LLC' (same
    distinctive first word). The loose key is right here: both strings come
    from one docket, and missing a spelling loses that company's filings.
    """
    ta, tb = _loose_tokens(a), _loose_tokens(b)
    if not ta or not tb:
        return False
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if long_[: len(short)] == short:
        return True
    return ta[0] == tb[0] and len(ta[0]) >= 5 and ta[0] not in _GENERIC_FIRST


def pick_company(counts: dict[str, int]) -> tuple[str | None, list[str], list[str]]:
    """Choose the docket's company from affiliation counts.

    `counts` maps each non-NRC affiliation string to how many documents carry
    it. Returns (name, aliases, all variants of that company). The name is
    always one of the strings ADAMS wrote; nothing is synthesised.
    """
    names = [n for n, c in counts.items() if c > 0]
    if not names:
        return None, [], []
    clusters: list[list[str]] = []
    for n in sorted(names, key=lambda n: (-counts[n], n)):
        for cl in clusters:
            if any(_related(n, m) for m in cl):
                cl.append(n)
                break
        else:
            clusters.append([n])
    best = max(clusters, key=lambda cl: (sum(counts[n] for n in cl), -clusters.index(cl)))
    top = max(counts[n] for n in best)
    solid = [n for n in best if counts[n] >= max(2, 0.2 * top)] or best
    legal = [n for n in solid if has_legal_form(n)]
    if legal:
        # The legal entity name is what other filings (Form D, trademarks) carry.
        name = max(legal, key=lambda n: (counts[n], len(_tokens(n)), n))
    else:
        name = max(solid, key=lambda n: (len(_tokens(n)), counts[n], n))
    name_tokens = _tokens(name)
    aliases = []
    for n in best:
        t = _tokens(n)
        if n == name or counts[n] < 2 or not t or t == name_tokens:
            continue
        # A bare prefix ('Radiant' for 'Radiant Industries') is too generic to
        # hand the resolver as a merge key.
        if len(t) < len(name_tokens) and name_tokens[: len(t)] == t:
            continue
        aliases.append(n)
    return name, aliases[:4], best


def stop_share(counts: dict[str, int]) -> float:
    """Share of non-NRC affiliation mentions that belong to stoplisted names."""
    total = sum(counts.values())
    if not total:
        return 0.0
    return sum(c for n, c in counts.items() if is_stoplisted(n)) / total


_INSPECTION = re.compile(r"vendor inspection|inspection report|inspection plan|non-?conformance", re.I)
_INSPECTION_BRANCH = re.compile(r"/(VQAB|IQVB)\b")


def is_vendor_inspection(doc: dict) -> bool:
    """99902xxx numbers are also used for supplier QA inspections, which are not pre-application."""
    if _INSPECTION.search(doc.get("DocumentTitle") or ""):
        return True
    if any(_INSPECTION.search(t or "") for t in doc.get("DocumentType") or []):
        return True
    return any(_INSPECTION_BRANCH.search(a or "") for a in doc.get("AuthorAffiliation") or [])


# --------------------------------------------------------------------------
# What a document is
# --------------------------------------------------------------------------

_APP_TYPES = [
    "construction permit", "combined license", "early site permit", "operating license",
    "limited work authorization", "manufacturing license",
]
# 'Rev 1' alone does not mean a second version: a first issue to the NRC is often
# numbered 1.0 after internal 0.x drafts (seen on a filing). Only explicit words,
# or a revision number of 2 or more, are read as an update.
_UPDATED = re.compile(r"\b(revised|updated?|amended|supersed\w+)\b|\b(rev\.?|revision|version)\s*([2-9]|[1-9]\d)\b",
                      re.I)
_PART = re.compile(r"\bapplication,? part (\d|one|two|three)\b")
_PART_WORDS = {"one": "1", "two": "2", "three": "3"}
_SLASH_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")


def _norm_title(doc: dict) -> str:
    """Lowercased title with underscores and odd spacing flattened, for matching."""
    return " ".join((doc.get("DocumentTitle") or "").replace("_", " ").lower().split())


def _types(doc: dict) -> set[str]:
    return {t for t in doc.get("DocumentType") or [] if t}


def _article(phrase: str) -> str:
    return ("an " if phrase[:1] in "aeiou" else "a ") + phrase


def _kind(kind: str, base: float, act: str, noun: str, **extra: Any) -> dict:
    return {"kind": kind, "base": base, "act": act, "noun": noun, **extra}


def classify_company_doc(doc: dict) -> dict | None:
    """What a document written by the company is. None means not worth a signal.

    Rules run in order and the first match wins; the order encodes which
    reading is safer when a title mentions two things. Anything that does not
    match a specific rule falls through to a plain 'letter' so a title is
    never over-described.
    """
    t = _norm_title(doc)
    types = _types(doc)
    submits = bool(re.search(r"submit|submission|transmit", t))
    if re.search(r"affidavit|unsworn declaration|withholding", t) and not submits:
        return None
    if types == {"Legal-Affidavit"}:
        return None
    presentation = bool(
        types & {"Slides and Viewgraphs", "Meeting Briefing Package/Handouts"}
        or re.search(r"\bslides?\b|presentation|meeting materials", t)
    )
    if "withdraw" in t:  # taking something back is not a submission
        return _kind("letter", 0.0, "", "a letter")
    if "readiness assessment" in t and re.search(r"\brequest", t):
        what = "pre-application readiness assessment" if re.search(r"pre-?application", t) else "readiness assessment"
        return _kind("readiness_request", 0.65, f"Requested an NRC {what}", "a request for a readiness assessment")
    if submits and not presentation and "readiness" not in t:
        m = re.search(r"\b(" + "|".join(_APP_TYPES) + r")\b[^.]*\bapplication\b", t)
        if m and not re.search(r"pre-?application|pre-?submittal|response to|\brai\b|white ?paper", t):
            part = _PART.search(t)
            if part:  # applications may be filed in parts; say which, not 'the application'
                n = _PART_WORDS.get(part.group(1), part.group(1))
                phrase = f"part {n} of {_article(m.group(1) + ' application')}"
            else:
                phrase = _article(f"{m.group(1)} application filing")
            return _kind("application", 0.8, f"Submitted {phrase} to the NRC", phrase)
    if re.search(r"letter of intent|notice of intent", t):
        if "early site permit" in t:
            return _kind("letter_of_intent", 0.6, "Sent the NRC a letter of intent to pursue an early site permit",
                         "a letter of intent to pursue an early site permit")
        if re.search(r"pre-?application", t):
            return _kind("letter_of_intent", 0.6, "Sent the NRC a letter of intent to begin pre-application engagement",
                         "a letter of intent")
        return _kind("letter_of_intent", 0.6, "Sent the NRC a letter of intent", "a letter of intent")
    if re.search(r"request (for|to initiate) pre-?application|request for (a |new |a new )?project number"
                 r"|project number request", t):
        return _kind("preapp_request", 0.6, "Requested pre-application engagement with the NRC",
                     "a pre-application engagement request")
    # 'REP' alone also abbreviates Radiological Emergency Preparedness at the NRC.
    rep_abbrev = re.search(r"\brep\b", t) and not re.search(r"emergency|radiological|exercise|drill", t)
    if (re.search(r"regulatory engagement plan|plan for regulatory engagement", t) or rep_abbrev) and not presentation:
        if "outline" in t:
            return _kind("rep", 0.5, "Submitted a Regulatory Engagement Plan outline to the NRC",
                         "a Regulatory Engagement Plan outline", updated=False)
        if _UPDATED.search(t):
            return _kind("rep", 0.5, "Submitted an updated Regulatory Engagement Plan to the NRC",
                         "an updated Regulatory Engagement Plan", updated=True)
        return _kind("rep", 0.65, "Submitted a Regulatory Engagement Plan to the NRC",
                     "a Regulatory Engagement Plan", updated=False)
    if re.search(r"fee waiver|fee exemption|exemption from fees", t):
        return _kind("fee_waiver", 0.3, "Requested a fee waiver from the NRC", "a fee waiver request")
    if re.search(r"exemption request|request for exemption", t):
        return _kind("exemption", 0.45, "Submitted an exemption request to the NRC", "an exemption request")
    if re.search(r"request for additional information|\brai\b", t) and "response" in t:
        return _kind("rai_response", 0.35, "Responded to an NRC request for additional information",
                     "a response to an NRC request for additional information")
    if re.search(r"\bresponse to\b", t):
        return _kind("response", 0.3, "Sent a written response to NRC staff", "a written response to NRC staff")
    if presentation:
        return _kind("presentation", 0.35, "Submitted meeting presentation materials to the NRC",
                     "meeting presentation materials")
    qa_noise = re.search(r"comment|meeting|confirmation|feedback|audit", t)
    if re.search(r"quality assurance program description|\bqapd\b", t) and not qa_noise and (
            submits or "Quality Assurance Program" in types or "Topical Report" in types):
        return _kind("qapd", 0.5, "Submitted a Quality Assurance Program Description to the NRC",
                     "a Quality Assurance Program Description")
    if ("Quality Assurance Program" in types or "quality assurance program" in t) and not qa_noise:
        what = "topical report" if "Topical Report" in types or "topical report" in t else "document"
        return _kind("qapd", 0.5, f"Submitted a quality assurance program {what} to the NRC",
                     f"a quality assurance program {what}")
    if "Topical Report" in types or "topical report" in t:
        return _kind("topical_report", 0.5, "Submitted a topical report to the NRC", "a topical report")
    if re.search(r"white ?paper", t) or "Technical Paper" in types:
        return _kind("white_paper", 0.45, "Submitted a white paper to the NRC", "a white paper")
    if types == {"E-Mail"}:
        return None
    # Generic correspondence says nothing a partner can act on: no activity
    # signal (base 0), but it can still be named as a docket's first document.
    if "Letter" in types:
        return _kind("letter", 0.0, "", "a letter")
    return _kind("other", 0.0, "", "a filing")


def classify_nrc_doc(doc: dict) -> dict | None:
    """What a document written by NRC staff about the company is. None means skip."""
    t = _norm_title(doc)
    types = _types(doc)
    if "assignment of project number" in t:
        return _kind("project_number", 0.55, "NRC assigned a project number", "the NRC project number assignment")
    # Proprietary-information determinations are paperwork; '(Non-Proprietary)' in a title is only a label.
    if re.search(r"withholding|(?<!non-)(?<!non )proprietary|\bform\s*89[67]\b|prop det", t) \
            or "Proprietary Information Review" in types:
        return None
    if types == {"E-Mail"}:  # a transmittal note; the thing it transmits is filed separately
        if "project number" in t:
            return _kind("nrc_email", 0.0, "", "an NRC e-mail about the project number")
        return _kind("nrc_email", 0.0, "", "an NRC e-mail")
    if re.search(r"accept(ance|ed) for docketing", t):
        what = "the construction permit application" if "construction permit" in t else "an application"
        return _kind("docketing", 0.8, f"NRC accepted {what} for review", "an NRC docketing acceptance")
    if "readiness assessment" in t:
        what = "pre-application readiness assessment" if re.search(r"pre-?application", t) else "readiness assessment"
        what += " plan" if re.search(r"\bplan\b", t) else " document"
        return _kind("readiness_plan", 0.6, f"NRC issued a {what}", f"an NRC {what}")
    if "Meeting Notice" in types:
        when = _meeting_date(doc)
        posted = _added(doc)
        # 'Scheduled for' must be a day still ahead when the notice was posted, and not a typo years out.
        if when and posted and not (posted <= when <= posted + timedelta(days=400)):
            when = None
        label = ("pre-application meeting" if re.search(r"pre-?application", t)
                 else "pre-submittal meeting" if re.search(r"pre-?submittal", t) else "meeting")
        act = f"NRC scheduled a {label} for {fmt_day(when)}" if when else "NRC posted a meeting notice"
        return _kind("meeting_notice", 0.4, act, "an NRC meeting notice", meeting_date=when)
    if "Meeting Summary" in types or re.search(r"\bsummary of\b.*\bmeeting\b|meeting summary", t):
        return _kind("meeting_summary", 0.3, "NRC published a meeting summary", "an NRC meeting summary")
    if types & {"Safety Evaluation", "Final Safety Evaluation Report (FSER)"} and not re.search(r"draft|schedule", t):
        return _kind("safety_evaluation", 0.6, "NRC issued a safety evaluation", "an NRC safety evaluation")
    if "Audit Plan" in types or "audit plan" in t:
        return _kind("audit", 0.4, "NRC issued a regulatory audit plan", "an NRC audit plan")
    if "Audit Report" in types or re.search(r"audit (summary|report)", t):
        return _kind("audit", 0.4, "NRC issued a regulatory audit report", "an NRC audit report")
    if re.search(r"\bform\s*898\b|completeness determination", t):
        return _kind("completeness", 0.35, "NRC issued a topical report completeness determination",
                     "an NRC completeness determination")
    if re.search(r"staff (feedback|observations)|nrc feedback|feedback (regarding|on|for)\b|response to .*white ?paper", t):
        if re.search(r"white ?paper", t):
            return _kind("feedback", 0.45, "NRC staff issued feedback on a white paper", "NRC staff feedback")
        return _kind("feedback", 0.45, "NRC staff issued written feedback", "NRC staff feedback")
    if "Letter" in types:
        return _kind("nrc_letter", 0.0, "", "an NRC letter")
    return None


def _meeting_date(doc: dict) -> date | None:
    """Meeting notices carry the meeting day as MM/DD/YYYY at the start or end of the title."""
    title = (doc.get("DocumentTitle") or "").strip()
    hits = list(_SLASH_DATE.finditer(title))
    if not hits:
        return None
    m = hits[-1] if hits[-1].end() >= len(title) - 1 else hits[0] if hits[0].start() <= 1 else None
    if m is None:
        return None
    try:
        return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None


def doc_side(doc: dict, variants: list[str]) -> str:
    """'company', 'nrc', or 'other' (third parties and mixed authorship are not attributed)."""
    authors = [" ".join((a or "").split()) for a in doc.get("AuthorAffiliation") or [] if a]
    if not authors:
        return "other"
    if all(is_nrc(a) for a in authors):
        return "nrc"
    if any(is_nrc(a) for a in authors):
        return "other"
    mine = set(variants)
    return "company" if any(a in mine for a in authors) else "other"


def classify_doc(doc: dict, variants: list[str]) -> dict | None:
    side = doc_side(doc, variants)
    if side == "company":
        k = classify_company_doc(doc)
    elif side == "nrc":
        k = classify_nrc_doc(doc)
    else:
        return None
    if k:
        k["side"] = side
    return k


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def fmt_day(d: date | None) -> str:
    return f"{d.day} {d:%b %Y}" if d else "an unknown date"


def age_factor(days: int) -> float:
    """New entrants are the point: a filing on a years-old docket counts for less.

    Dockets older than MAX_DOCKET_AGE_DAYS are not emitted at all.
    """
    if days <= 365:
        return 1.0
    if days <= 730:
        return 0.85
    return 0.7


def format_author(raw: str) -> str:
    """ADAMS indexes people as 'Surname Initials'; show them as 'I. Surname'."""
    s = " ".join((raw or "").split())
    m = re.match(r"^(.+?)((?:\s[A-Z])+)$", s)
    if not m:
        return s
    initials = " ".join(f"{c}." for c in m.group(2).split())
    return f"{initials} {m.group(1)}"


_NOT_FIRST_NAMES = {"Mrs", "Miss", "Messrs", "Dear", "Doctor", "Director", "Manager", "Officer", "President"}
_INDEXED_NAME = re.compile(r"^(.+?)((?:\s[A-Z])+)$")  # 'Surname I' or 'Surname I J'


def author_in_text(raw: str, content: str | None) -> bool:
    """Is the indexed author's surname actually written in the filing?

    The ADAMS index is typed by hand and has been seen to get a surname wrong
    ('Lotti R' on a letter signed Robert Iotti). A name the document itself
    does not contain is not attached to the company.
    """
    s = " ".join((raw or "").split())
    m = _INDEXED_NAME.match(s)
    surname = m.group(1) if m else s
    if not surname or not content:
        return False
    return bool(re.search(r"(?<![A-Za-z])" + re.escape(surname) + r"(?![A-Za-z])", content, re.I))


def expand_author(raw: str, content: str | None) -> str:
    """Use the filings' own text to turn 'Mitchell M' into 'Mark Mitchell'.

    Only when the text has exactly one capitalised word that starts with the
    indexed initial and sits directly before the surname, and it appears at
    least twice. Some filings are scans and their text is OCR ('Sinon Irish'
    was seen once next to 'Simon Irish'), so a single sighting is not enough.
    Otherwise the initial is kept: a wrong first name is worse than none.
    """
    s = " ".join((raw or "").split())
    m = re.match(r"^(.+?)((?:\s[A-Z])+)$", s)
    if not m or not content:
        return format_author(raw)
    surname, initial = m.group(1), m.group(2).split()[0]
    pat = re.compile(r"\b(" + initial + r"[a-z]{2,})\s+(?:[A-Z]\.?\s+){0,2}" + re.escape(surname) + r"\b")
    firsts = Counter(f for f in pat.findall(content) if f not in _NOT_FIRST_NAMES)
    if len(firsts) == 1 and next(iter(firsts.values())) >= 2:
        return f"{next(iter(firsts))} {surname}"
    return format_author(raw)


_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})")
# OCR turns '.com' into '.corn' or '.c0m'; only accept endings that exist.
_TLDS = {"com", "co", "io", "energy", "tech", "ai", "org", "net", "us", "uk", "ca", "eu", "de", "fr", "it",
         "se", "jp", "kr", "nl", "ch", "inc", "earth", "power", "systems", "industries", "global", "group",
         "dev", "app", "xyz"}


def domain_matches(domain: str, variants: list[str]) -> bool:
    """Does this e-mail domain plainly belong to the docket's company?

    The first label must be the company's distinctive first word, or start
    with its first two words run together, or start with a long first word:
    'aalo.com' for Aalo Holdings, 'blueenergy.co' for Blue Energy Global,
    'radiantnuclear.com' for Radiant Industries. A consultant's or a law
    firm's address in the same letter fails this test. Descriptive words stay
    in the name here ('blue.com' is not Blue Energy's): a domain is the
    entity's strongest key, so a miss is better than a false hit.
    """
    labels = domain.lower().split(".")
    if len(labels) != 2 or labels[-1] not in _TLDS:
        return False
    label = labels[0].replace("-", "")
    for v in variants:
        t = [re.sub(r"[^a-z0-9]", "", w) for w in _brand_tokens(v)]
        t = [w for w in t if w]
        if not t:
            continue
        distinctive = t[0] not in _GENERIC_FIRST  # 'advanced...' or 'energy...' alone proves nothing
        if label == t[0] and (len(t) == 1 or (len(t[0]) >= 5 and distinctive)):
            return True
        if len(t) >= 2 and label.startswith(t[0] + t[1]):
            return True
        if len(t[0]) >= 6 and distinctive and label.startswith(t[0]):
            return True
    return False


def extract_domains(content: str | None, variants: list[str]) -> Counter:
    """Company e-mail domains found in a document's text, with how often each appears."""
    out: Counter[str] = Counter()
    for m in _EMAIL.finditer(content or ""):
        d = clean_domain(m.group(1))
        if d and domain_matches(d, variants):
            out[d] += 1
    return out


def _people(doc: dict, company: str, variants: list[str], content: str | None = None) -> list[Person]:
    """Authors of a document written by the company and nobody else.

    ADAMS lists author names and author affiliations separately, so when a
    consultant co-authors a filing there is no telling whose employee each
    name is. Those documents get no people.
    """
    affiliations = _outside(doc.get("AuthorAffiliation"))
    if not affiliations or any(a not in variants for a in affiliations):
        return []
    out = []
    for raw in (doc.get("AuthorName") or [])[:4]:
        name = expand_author(raw, content)
        if name:
            out.append(Person(name=name, role="Author of NRC submission", affiliations=[company],
                              facts={"adams_author_name": " ".join(raw.split())}))
    return out


def _added(doc: dict) -> date | None:
    return parse_date(doc.get("DateAdded"))


def lag_days(doc: dict) -> int | None:
    """Days between the date ADAMS indexes for the document and its public release."""
    added, written = _added(doc), parse_date(doc.get("DocumentDate"))
    if added and written and added >= written:
        return (added - written).days
    return None


# Kinds whose title describes the release itself, so the release day is the event.
_RELEASE_DATED = {"meeting_summary"}


def event_date(doc: dict, kind: str) -> date | None:
    """The day the thing a filing title describes happened.

    That is the date on the document, as ADAMS indexes it, not the later day
    the library released it. The release day is used only when the title is
    about the release, or when the document date is missing or impossible
    (after the release).
    """
    added, written = _added(doc), parse_date(doc.get("DocumentDate"))
    if kind in _RELEASE_DATED or not written or (added and written > added):
        return added
    return written


# What the docket series is, in plain words, so the thesis classifier reads the
# structured fact (a 99902xxx docket number) as text. It describes the series,
# not the company: a vendor's topical report on a 99902xxx docket is not a
# reactor design, and this sentence does not say it is.
SERIES_NOTE = ("The NRC uses 99902-series project dockets for new and advanced nuclear reactor "
               "pre-application engagement and for vendor topical reports")


def _doc_text(doc: dict, docket: str, preapp: bool = False) -> str:
    """Source fields joined for thesis classification.

    The lines that are ours say only where the document sits: an NRC project
    docket, called a pre-application docket when the docket's own documents
    use that word (`preapp`, the same test the title uses), and what the
    99902 series is for. That is how the source itself is defined.
    """
    where = "pre-application project docket" if preapp else "project docket"
    parts = [
        doc.get("DocumentTitle") or "",
        "Document type: " + ", ".join(sorted(_types(doc))) if _types(doc) else "",
        f"U.S. Nuclear Regulatory Commission (NRC) {where} {docket}",
        SERIES_NOTE,
        "Author affiliation: " + "; ".join(doc.get("AuthorAffiliation") or []) if doc.get("AuthorAffiliation") else "",
        "Addressee affiliation: " + "; ".join(doc.get("AddresseeAffiliation") or [])
        if doc.get("AddresseeAffiliation") else "",
    ]
    return ". ".join(p for p in parts if p)


def _doc_metrics(doc: dict, docket: str, hist: dict, n_window: int) -> dict[str, Any]:
    added, written = _added(doc), parse_date(doc.get("DocumentDate"))
    m: dict[str, Any] = {
        "docket": docket,
        "accession_number": doc.get("AccessionNumber"),
        "document_title": doc.get("DocumentTitle"),
        "adams_document_date": iso(written),  # as indexed; the printed date can differ by a few days
        "date_added": iso(added),
        "document_types": sorted(_types(doc)),
        "docket_first_public": iso(hist.get("first_added")),
        "docket_docs_total": hist.get("count"),
        "docket_docs_in_window": n_window,
    }
    if lag_days(doc) is not None:
        m["publication_lag_days"] = lag_days(doc)
    if added and hist.get("first_added"):
        m["docket_age_days"] = max((added - hist["first_added"]).days, 0)
    pages = str(doc.get("EstimatedPageCount") or "")
    if pages.isdigit() and int(pages) > 0:
        m["pages"] = int(pages)
    if doc.get("IdT"):
        m["adams_record_url"] = RECORD_URL.format(id=doc["IdT"])
    return {k: v for k, v in m.items() if v not in (None, "", [])}


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------

def _search(filters: list[dict], *, skip: int = 0, direction: int = -1, any_filters: list[dict] | None = None,
            ttl: float = WINDOW_TTL) -> dict:
    body = {
        "q": "", "filters": filters, "anyFilters": any_filters or [], "legacyLibFilter": False,
        "mainLibFilter": True, "content": False, "sort": "DateAddedTimestamp", "sortDirection": direction,
        "skip": skip,
    }
    return http.post_json(API, body, ttl=ttl, timeout=45)


def parse_results(resp: dict) -> list[dict]:
    rows = (resp or {}).get("results") or []
    return [r["document"] for r in rows if isinstance(r, dict) and isinstance(r.get("document"), dict)]


def _facet_counts(resp: dict, field: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for f in (resp.get("facets") or {}).get(field) or []:
        for name in _outside([f.get("value")]):
            out[name] = out.get(name, 0) + int(f.get("count") or 0)
    return out


def parse_history(resp: dict) -> dict:
    """Reduce an oldest-first, whole-docket search response to what we need."""
    docs = parse_results(resp)
    authors = _facet_counts(resp, "AuthorAffiliation")
    addressees = _facet_counts(resp, "AddresseeAffiliation")
    if not authors and not addressees:  # no facets: count from the rows we have
        for d in docs:
            for a in _outside(d.get("AuthorAffiliation")):
                authors[a] = authors.get(a, 0) + 1
            for a in _outside(d.get("AddresseeAffiliation")):
                addressees[a] = addressees.get(a, 0) + 1
    first_added = _added(docs[0]) if docs else None
    return {
        "count": int(resp.get("count") or len(docs)),
        "first_added": first_added,
        "first_docs": [d for d in docs if _added(d) == first_added],
        "authors": authors,
        "addressees": addressees,
    }


def fetch_window(ctx: Context) -> list[dict]:
    filters = [
        {"field": "DateAdded", "value": f"(DateAdded ge '{ctx.since.isoformat()}')"},
        {"field": "DocketNumber", "value": "99902", "operator": "contains"},
    ]
    docs: list[dict] = []
    seen: set[str] = set()
    fetched = 0
    for page in range(MAX_PAGES):
        try:
            resp = _search(filters, skip=page * PAGE_SIZE)
        except Exception as e:  # keep what we already have
            ctx.warn(f"nrc_adams: window page {page} failed: {e}")
            break
        batch = parse_results(resp)
        fetched += len(batch)
        for d in batch:
            # A document added while we page shifts the rows and repeats one.
            key = d.get("AccessionNumber") or d.get("IdT") or ""
            if key and key in seen:
                continue
            seen.add(key)
            docs.append(d)
        total = resp.get("count") if isinstance(resp, dict) else None
        if len(batch) < PAGE_SIZE or (isinstance(total, int) and fetched >= total):
            break
    else:
        ctx.warn(f"nrc_adams: window has more than {MAX_PAGES * PAGE_SIZE} documents, the oldest were not read")
    return docs


def fetch_history(docket: str) -> dict:
    resp = _search([{"field": "DocketNumber", "value": docket, "operator": "equals"}], direction=1, ttl=HISTORY_TTL)
    return parse_history(resp)


def fetch_content(record_id: str) -> str:
    """Extracted text of one document, as the search site's own detail view loads it."""
    resp = http.get_json(DETAIL_API.format(id=record_id), ttl=DETAIL_TTL, timeout=60)
    return (resp.get("document") or {}).get("content") or ""


def domain_is_live(domain: str) -> bool:
    """False only if the domain does not exist in DNS.

    A domain mangled by OCR does not resolve, so this is the last guard before
    a domain becomes the entity's strongest key. Any answer, a refused
    connection, a certificate error or a timeout all mean the name exists: a
    slow company site must not make the domain come and go between runs,
    because the stored signal's identity includes it.
    """
    try:
        http.request(f"https://{domain}/", ttl=DETAIL_TTL, timeout=8, retries=0)
        return True
    except http.HttpError:
        return True
    except Exception as e:
        return not isinstance(getattr(e, "reason", e), socket.gaierror)


def fetch_prior_docket_docs(variants: list[str], docket: str, before: date) -> int:
    """Documents this company wrote or received on OTHER 99902xxx dockets before `before`."""
    any_filters = []
    for v in variants[:6]:
        any_filters.append({"field": "AuthorAffiliation", "value": v, "operator": "equals"})
        any_filters.append({"field": "AddresseeAffiliation", "value": v, "operator": "equals"})
    filters = [
        {"field": "DateAdded", "value": f"(DateAdded lt '{before.isoformat()}')"},
        {"field": "DocketNumber", "value": "99902", "operator": "contains"},
        {"field": "DocketNumber", "value": docket, "operator": "notequals"},
    ]
    resp = _search(filters, direction=1, any_filters=any_filters, ttl=HISTORY_TTL)
    return int(resp.get("count") or 0)


# --------------------------------------------------------------------------
# Signals
# --------------------------------------------------------------------------

def _merge_counts(*maps: dict[str, int]) -> dict[str, int]:
    out: Counter[str] = Counter()
    for m in maps:
        out.update(m)
    return dict(out)


def _rank(doc: dict, k: dict) -> tuple:
    """Order for picking one document to stand for a package or a docket opening."""
    types = _types(doc)
    return (k["base"], k["side"] == "company", "Letter" in types, "E-Mail" not in types,
            doc.get("AccessionNumber") or "")


def docket_signals(docket: str, window_docs: list[dict], hist: dict, ctx: Context,
                   prior_docket_docs: int | None = None) -> tuple[str | None, list[Signal], list[Signal]]:
    """Signals for one docket.

    Returns (company name or None if skipped, [docket-appeared signal], [uncapped activity signals]).
    Pure: no network. `window_docs` are the docket's documents added inside the
    lookback window, `hist` is parse_history() of the whole docket.
    """
    counts = _merge_counts(hist.get("authors") or {}, hist.get("addressees") or {})
    name, aliases, variants = pick_company(counts)
    if not name or is_stoplisted(name) or stop_share(counts) >= 0.4:
        return None, [], []
    first_added: date | None = hist.get("first_added")
    if first_added and (ctx.today - first_added).days > MAX_DOCKET_AGE_DAYS:
        return None, [], []  # years into pre-application: not a new entrant, often a vendor's report docket
    known = window_docs + [d for d in hist.get("first_docs") or [] if d not in window_docs]
    if known and sum(is_vendor_inspection(d) for d in known) * 2 >= len(known):
        return None, [], []

    entity = EntityHint(name=name, aliases=aliases)
    preapp = any(re.search(r"pre-?application|regulatory engagement|\brep\b", _norm_title(d)) for d in known)

    classified: list[tuple[dict, dict]] = []
    for d in window_docs:
        added = _added(d)
        if not added or added < ctx.since or added > ctx.today or not (d.get("Url") or "").startswith("http"):
            continue
        shared = len(preapp_dockets(d))
        if shared > 3:  # a circular to many vendors, not this company's filing
            continue
        k = classify_doc(d, variants)
        if not k:
            continue
        if shared > 1 and k["side"] == "nrc" and not set(_outside(d.get("AddresseeAffiliation"))) & set(variants):
            continue  # an NRC document on several dockets belongs to whoever it is addressed to
        classified.append((d, k))

    opened: list[Signal] = []
    anchor_keys: set[str] = set()
    anchor_kind = None
    if first_added and ctx.since <= first_added <= ctx.today:
        firsts = [(d, k) for d, k in classified if _added(d) == first_added]
        if not firsts:  # first-day documents were all of a kind we do not describe
            for d in window_docs:
                if _added(d) == first_added and (d.get("Url") or "").startswith("http"):
                    side = doc_side(d, variants)
                    noun = {"company": "a company filing", "nrc": "an NRC document"}.get(side, "a document")
                    firsts.append((d, {**_kind("other", 0.0, "", noun), "side": side}))
        if firsts:
            d, k = max(firsts, key=lambda dk: _rank(*dk))
            kinds = {kk["kind"] for _, kk in classified}
            strength = 0.85
            if kinds & {"rep", "letter_of_intent", "preapp_request"}:
                strength += 0.05  # the company has put its plan or intent in writing
            if kinds & {"meeting_notice", "meeting_summary", "readiness_plan", "feedback"}:
                strength += 0.05  # and NRC staff are already spending time on it
            additional = bool(prior_docket_docs)
            if additional:  # the company already had a docket: a new project, not a new entrant
                strength = 0.6
            label = ("Additional NRC project docket" if additional
                     else "New NRC pre-application docket" if preapp else "New NRC project docket")
            # 'appeared', not 'opened': the date is the docket's first public document, and the
            # NRC assigns the number some time before that.
            title = f"{label} {docket} appeared with {k['noun']}"
            if len(title) >= 110:
                title = f"{label} {docket} appeared"
            metrics = _doc_metrics(d, docket, hist, len(window_docs))
            metrics["doc_kind"] = k["kind"]
            metrics["author_side"] = k["side"]
            metrics["occurred_at_basis"] = "adams_date_added"  # the day the docket's first document became public
            if prior_docket_docs is not None:
                metrics["company_docs_on_earlier_dockets"] = prior_docket_docs
            opened.append(Signal(
                source=SLUG, family=FAMILY, kind="nrc_preapp_docket_opened", entity=entity, title=title,
                occurred_at=iso(first_added), url=d["Url"], value=float(hist.get("count") or len(window_docs)),
                unit="documents on docket", strength=round(strength, 2), metrics=metrics,
                people=_people(d, name, variants) if k["side"] == "company" else [], text=_doc_text(d, docket, preapp),
            ))
            anchor_kind = k["kind"]
            anchor_keys.add(d.get("AccessionNumber") or "")
            anchor_keys.update(d.get("PackagesFiledIn") or [])

    # One document per package, one per (kind, date): enclosures and re-posted
    # copies of the same submission must not count twice.
    best: dict[tuple, tuple[dict, dict]] = {}
    for d, k in classified:
        if k["base"] <= 0 or (d.get("AccessionNumber") or "") in anchor_keys:
            continue
        if anchor_keys & set(d.get("PackagesFiledIn") or []):
            continue
        if opened and k["kind"] == "project_number":  # same event as the docket appearing
            continue
        if k["kind"] == "rep":
            # A company has one Regulatory Engagement Plan. The plan and its cover letter are often
            # posted as separate records days apart, and only one of the two titles may say
            # 'revised': keep a single record per docket, preferring the one that says so.
            if anchor_kind == "rep":
                continue
            key: tuple = ("rep", docket)
            if key in best and (bool(k.get("updated")), _rank(d, k)) <= (bool(best[key][1].get("updated")),
                                                                         _rank(*best[key])):
                continue
            best[key] = (d, k)
            continue
        pkg = (d.get("PackagesFiledIn") or [None])[0]
        key = ("pkg", pkg) if pkg else ("doc", d.get("AccessionNumber"))
        if key not in best or _rank(d, k) > _rank(*best[key]):
            best[key] = (d, k)
    seen: set[tuple] = set()
    activity: list[Signal] = []
    # Strongest kind first; among re-posted copies the earliest public one wins.
    for d, k in sorted(best.values(), key=lambda dk: (-dk[1]["base"], dk[0].get("DateAddedTimestamp") or "")):
        dedupe = (k["kind"], k.get("meeting_date") or d.get("DocumentDate"))
        if dedupe in seen:
            continue
        seen.add(dedupe)
        when = event_date(d, k["kind"])
        if not when or when < ctx.since or when > ctx.today:
            continue  # written before the window (or misdated in the index): not news, even if just released
        age = max((when - first_added).days, 0) if first_added else 0
        strength = max(0.15, round(k["base"] * age_factor(age), 2))
        if k["kind"] == "project_number":
            title = f"NRC assigned project number {docket}"
        else:
            title = f"{k['act']} on docket {docket}"
        metrics = _doc_metrics(d, docket, hist, len(window_docs))
        metrics["doc_kind"] = k["kind"]
        metrics["author_side"] = k["side"]
        metrics["occurred_at_basis"] = "adams_date_added" if when == _added(d) and when != parse_date(
            d.get("DocumentDate")) else "adams_document_date"
        if k.get("meeting_date"):
            metrics["meeting_date"] = iso(k["meeting_date"])
        released = _added(d)
        if released and released > when:
            # Nobody outside the NRC could read it before ADAMS released it: decay from that day.
            metrics["public_at"] = iso(released)
        activity.append(Signal(
            source=SLUG, family=FAMILY, kind="nrc_preapp_activity", entity=entity, title=title,
            occurred_at=iso(when), url=d["Url"], strength=strength, metrics=metrics,
            people=_people(d, name, variants) if k["side"] == "company" else [], text=_doc_text(d, docket, preapp),
        ))
    return name, opened, activity


def cap_activity(signals: list[Signal], cap: int = ACTIVITY_CAP) -> list[Signal]:
    """Keep a company's strongest few filings so one busy docket cannot flood the board.

    Strongest first, cover documents before their enclosures, newest first
    among equals; at most two of any one kind of company filing or meeting
    notice and one of any other kind of NRC document.
    """
    def is_enclosure(s: Signal) -> bool:
        return bool(re.match(r"\s*(public )?(encl(osure)?|attachment)\b", s.metrics.get("document_title") or "", re.I))

    ordered = sorted(signals, key=lambda s: s.occurred_at, reverse=True)
    ordered = sorted(ordered, key=lambda s: (-s.strength, is_enclosure(s)))
    kept: list[Signal] = []
    per_kind: Counter[str] = Counter()
    for s in ordered:
        kind = s.metrics.get("doc_kind") or ""
        allowed = 2 if s.metrics.get("author_side") == "company" or kind == "meeting_notice" else 1
        if per_kind[kind] >= allowed:
            continue
        per_kind[kind] += 1
        kept.append(s)
        if len(kept) >= cap:
            break
    return kept


def enrich_from_text(signals: list[Signal], variants: list[str], docs_by_accession: dict[str, dict],
                     ctx: Context, fetch=None, is_live=None) -> None:
    """Fill the company domain, and confirm and complete author names, from the filings' own text.

    One detail request per company-written document we are about to emit, and
    one request to the company's own site to confirm the domain exists. Only
    the e-mail domain and the author's name are kept; letters also carry phone
    numbers and street addresses, which we do not store.

    People are kept only when the filing itself contains the indexed surname.
    A signal whose text could not be read ends up with no people rather than
    with a name nobody checked.
    """
    fetch = fetch or fetch_content
    is_live = is_live or domain_is_live
    texts: dict[str, str] = {}
    for s in signals:
        if s.metrics.get("author_side") != "company":
            continue
        accession = s.metrics.get("accession_number") or ""
        doc = docs_by_accession.get(accession)
        if not doc or not doc.get("IdT") or accession in texts:
            continue
        try:
            texts[accession] = fetch(doc["IdT"]) or ""
        except Exception as e:
            ctx.warn(f"nrc_adams: no text for {doc.get('AccessionNumber')}: {type(e).__name__}: {e}")
    everything = "\n".join(t for t in texts.values() if t)
    for s in signals:
        if not s.people:
            continue
        accession = s.metrics.get("accession_number") or ""
        doc, own = docs_by_accession.get(accession), texts.get(accession) or ""
        if not doc or not own:
            s.people = []
            continue
        confirmed = {**doc, "AuthorName": [a for a in doc.get("AuthorName") or [] if author_in_text(a, own)]}
        s.people = _people(confirmed, s.entity.name, variants, everything)
    if not everything:
        return
    ranked = extract_domains(everything, variants).most_common(2)
    if ranked and (len(ranked) == 1 or ranked[0][1] > ranked[1][1]):  # skip a tie rather than guess
        domain = ranked[0][0]
        if is_live(domain):
            for s in signals:
                s.entity.domain = domain
        else:
            ctx.warn(f"nrc_adams: domain {domain} for {signals[0].entity.name} did not resolve, left out")


def collect(ctx: Context) -> Iterable[Signal]:
    docs = fetch_window(ctx)
    by_docket: dict[str, list[dict]] = {}
    for d in docs:
        for dk in preapp_dockets(d):
            by_docket.setdefault(dk, []).append(d)
    docs_by_accession = {d.get("AccessionNumber") or "": d for d in docs}
    ctx.log(f"nrc_adams: {len(docs)} documents on {len(by_docket)} project dockets since {ctx.since}")

    # Most recently active dockets first, so a limited probe sees today's news.
    order = sorted(by_docket, key=lambda dk: max(d.get("DateAddedTimestamp") or "" for d in by_docket[dk]),
                   reverse=True)
    companies: dict[str, dict] = {}  # normalized name -> {"opened": [], "activity": [], "variants": []}
    examined = skipped = 0
    for docket in order:
        if ctx.limit and examined >= ctx.limit:
            break
        window_docs = by_docket[docket]
        # Cheap pre-check on what the window itself shows, to save a history call.
        seen_counts: dict[str, int] = {}
        for d in window_docs:
            for a in _outside(d.get("AuthorAffiliation")) + _outside(d.get("AddresseeAffiliation")):
                seen_counts[a] = seen_counts.get(a, 0) + 1
        if seen_counts and stop_share(seen_counts) >= 0.5:
            skipped += 1
            continue
        try:
            hist = fetch_history(docket)
            _, _, variants = pick_company(_merge_counts(hist["authors"], hist["addressees"]))
            prior = None
            first_added = hist.get("first_added")
            if variants and first_added and first_added >= ctx.since:
                prior = fetch_prior_docket_docs(variants, docket, first_added)
            name, opened, activity = docket_signals(docket, window_docs, hist, ctx, prior)
        except Exception as e:
            ctx.warn(f"nrc_adams: docket {docket} failed: {type(e).__name__}: {e}")
            continue
        if not name:
            skipped += 1
            continue
        examined += 1
        co = companies.setdefault(normalize_name(name) or name, {"opened": [], "activity": [], "variants": []})
        co["opened"].extend(opened)
        co["activity"].extend(activity)
        co["variants"].extend(v for v in variants if v not in co["variants"])

    ctx.log(f"nrc_adams: {examined} dockets kept, {skipped} skipped "
            "(incumbents, listed companies, utilities, agencies, inspections, dockets over four years old)")
    for co in companies.values():
        signals = co["opened"] + cap_activity(co["activity"])
        if not signals:
            continue
        try:
            enrich_from_text(signals, co["variants"], docs_by_accession, ctx)
        except Exception as e:
            ctx.warn(f"nrc_adams: enrichment failed for {signals[0].entity.name}: {type(e).__name__}: {e}")
            for s in signals:  # unconfirmed names are not shipped
                s.people = []
        yield from signals
