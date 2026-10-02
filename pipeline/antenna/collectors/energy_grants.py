"""NSF SBIR/STTR Phase I awards and ARPA-E project awards to private companies.

Two keyless federal grant feeds: the NSF Awards API (small-business Phase I and
Fast-Track awards, with the full abstract and the principal investigator) and
the JSON:API behind arpa-e.energy.gov (projects led by private companies).
Both are early because a first non-dilutive federal award usually lands before
a priced round or any press, and the ARPA-E site lists a selection one to four
months before the same award shows up in USAspending.

One award, one signal: the sbir_awards collector already reports the NSF
awards that SBIR.gov lists, so this one reports only the NSF awards SBIR.gov
does not list yet (the newest, and most STTR ones) or whose title alone is
under the thesis bar. See covered_by_sbir_awards(). Every NSF signal also
carries metrics["award_key"] = "nsf:<award id>", so if both collectors do
report one award the scorer counts it once. Today that happens for a title
with a single keyword ("solar", "batteries", "3D printing"): sbir_awards keeps
it when the topic code in the SBIR.gov file backs the keyword, and this
collector does not read that code (6 of the 13 NSF awards emitted here on
2026-10-01).
"""

from __future__ import annotations

import html
import re
from collections import Counter
from datetime import date
from typing import Any, Iterable

from .. import http
from ..models import EntityHint, Person, Signal
from ..thesis import classify
from .base import Context, clean_domain, iso, loose_name, normalize_name, parse_date, squash

SLUG = "energy_grants"
FAMILY = "capital"
STAGE = "discover"
DESCRIPTION = "NSF SBIR/STTR Phase I awards and ARPA-E private-company projects on thesis"

NSF_API = "https://api.nsf.gov/services/v1/awards.json"
NSF_PAGE = "https://www.nsf.gov/awardsearch/show-award/?AWD_ID={id}"
# One call per phrase: the API returns nothing for an OR of quoted phrases.
NSF_PHRASES = ('"SBIR Phase I"', '"STTR Phase I"', '"SBIR Fast-Track"', '"STTR Fast-Track"')
NSF_PAGE_SIZE = 100
NSF_MAX_PAGES = 30  # 3,000 awards per phrase; a 120-day window holds under 100

# The sbir_awards collector reports every Phase I that SBIR.gov lists and whose
# title is on thesis. The same award must not reach the board twice, so an NSF
# award that SBIR.gov already lists is left to that collector. Same URL, same
# parameters and same ttl as its lookup, so the two share one cached page.
SBIR_GOV_SEARCH = "https://www.sbir.gov/awards"
SBIR_GOV_TTL = 7 * 86400

ARPAE_SITE = "https://arpa-e.energy.gov"
# Upper-case path on purpose: the lower-case one answers with a 301.
ARPAE_INDEX = ARPAE_SITE + "/JSONAPI/custom/index/project"
ARPAE_NODE = ARPAE_SITE + "/JSONAPI/custom/node"
ARPAE_PRIVATE_COMPANY = 29  # filter[custom_reference_organization_type]
ARPAE_ACCEPT = {"Accept": "application/vnd.api+json"}
ARPAE_PAGE_SIZE = 50  # fixed by the server; page[limit] is ignored
ARPAE_MAX_OFFSET = 5000  # the index holds 681 projects; stop if `count` ever misreports

TITLE_MAX = 109  # "under 110 characters"

# An abstract runs to about 4 KB of prose, so single thesis words turn up by
# accident ("immune defense", "protein fusion", "scalable manufacturing" of a
# drug). Measured on the 93 awards dated June to September 2026, fit >= 0.3 on
# the stored text let through 54: the 20 the gate below keeps (two of them
# surgical robots under a biomedical program), 10 more that NSF files under a
# biomedical or biotech program, and 24 about pulp making, plastics recycling,
# lab instruments or software. So the award title has to carry the thesis
# itself, or the abstract has to be overwhelmingly about it.
#
# The classifier caps a lone ambiguous term ("solar", "battery", "3D
# printing") just under 0.3, and a title is short enough to hold only one. The
# second term then comes from NSF's own program label for the award ("Other
# Energy Research", "Advanced Manufacturing"), which is structured evidence of
# the subject. The label only ever seconds a term the title already has: a
# title with no thesis term of its own is not carried by its label.
NSF_TITLE_FIT = 0.3
NSF_ABSTRACT_FIT = 0.85
MIN_FIT = 0.3
# What classify() gives one unambiguous strong term (0.465). A lone ambiguous
# one is capped at 0.28, so a software title needs "robot", "drone" or the
# like, or two terms, to clear this.
STRONG_TERM_FIT = 0.46

# NSF's own topic label for the award (the `program` field: "BIOMEDICAL
# ENGINEERING", "Synthetic biology", "AgTech"). An abstract from one of these
# programs still says "manufacturing", "industrial" and "semiconductor", but
# the company is a medical-device or biotech one. Only an award whose title is
# itself about a robot (a surgical robot, say) stays in.
_NSF_LIFE_SCIENCE = re.compile(
    r"biomed|biotech|biolog|synthetic bio|diagnostic|health|agtech|agricultur|pharma|disabilit", re.I)
# A software product described in thesis words ("a cloud API for materials
# simulation ... semiconductors, energy, defense") is not a hardware company.
_NSF_SOFTWARE_TITLE = re.compile(
    r"\b(cloud|software|application programming interface|api|saas|web[- ]based|apps?|"
    r"courseware|chatbot|large language models?|llms?)\b", re.I)

# ARPA-E files these under "Private Company" too. They are real awards but
# not sourcing targets, so they are skipped rather than ranked. Every name
# here appears in the ARPA-E private-company index.
_ESTABLISHED = re.compile(
    r"\b(halliburton|baker hughes|ge vernova|general electric|boeing|rtx|raytheon|"
    r"united technologies|toyota|saint-gobain|nokia|prysmian|siemens|ibm|eaton|"
    r"northrop grumman|lockheed|honeywell|mahle|innio|general motors|ford motor|"
    r"cummins|caterpillar|dupont|schlumberger|chevron|exxon|corning|xerox|"
    r"palo alto research center|hrl laboratories|general atomics|westinghouse|"
    r"blue origin|3m|abb|abengoa|alcoa|alliant techsystems|american superconductor|"
    r"applied materials|aurora flight sciences|basf|baldor|cree|delphi|"
    r"det norske veritas|eaglepicher|framatome|fuelcell energy|ginkgo bioworks|hp|"
    r"hewlett packard|infineon|intel|johnson matthey|linde|nalco|nvidia|oklo|orano|"
    r"panasonic|pratt & whitney|ricardo|rio tinto|robert bosch|sharp laboratories|"
    r"sunpower|teledyne|titanium metals|ws atkins)\b",
    re.I,
)

_FREEMAIL = {
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com",
    "yahoo.com", "ymail.com", "icloud.com", "me.com", "mac.com", "aol.com",
    "comcast.net", "att.net", "verizon.net", "sbcglobal.net", "cox.net", "charter.net",
    "protonmail.com", "proton.me", "pm.me", "qq.com", "163.com", "126.com",
}


# ---------------------------------------------------------------- text helpers

def _money(n: float) -> str:
    """Headline dollars: $305K, $1.55M, $2.2M."""
    if n >= 999_500:
        return "$" + f"{n / 1_000_000:.2f}".rstrip("0").rstrip(".") + "M"
    if n >= 1_000:
        return f"${n / 1_000:.0f}K"
    return f"${n:.0f}"


def _amount(raw: Any) -> int | None:
    """Parse '2,000,000', '$3,949,109 ' or '304985'. None when absent or not a number."""
    if raw is None:
        return None
    s = re.sub(r"[$,\s]", "", str(raw))
    if not re.fullmatch(r"\d+(\.\d+)?", s):
        return None
    n = int(float(s))
    return n if n > 0 else None


_SMALL = {"A", "An", "The", "Of", "On", "In", "To", "By", "At", "As", "Or", "For", "And",
          "Via", "With", "From", "Into", "Onto", "Using", "Its"}
# Short words that are words, not acronyms, when a whole title is in capitals.
_SHOUT_WORDS = {"A", "AN", "THE", "OF", "ON", "IN", "TO", "BY", "AT", "AS", "OR", "FOR", "AND",
                "VIA", "ITS", "NEW", "USE", "ION", "LOW", "AIR", "GAS", "OIL", "SEA", "ALL",
                "ONE", "TWO", "WET", "DRY", "HOT", "RAW", "NON", "PRE", "IS", "BE"}


def _sentence_case(text: str) -> str:
    """Lower-case Title Case words, leaving acronyms and formulas alone.

    'AI-Enhanced Robotic Instruments' -> 'AI-enhanced robotic instruments';
    'MXene', 'SnO2', 'Si-based', '3D' and 'UAV' are untouched. A title typed
    entirely in capitals ('DEVELOPMENT AND DEMONSTRATION OF A ...') carries
    no case information, so it is lowered whole, keeping only tokens of up to
    three letters that are not ordinary words (REE, DC, EV).
    """
    text = " ".join(text.split())
    letters = [c for c in text if c.isalpha()]
    if len(letters) >= 12 and sum(c.isupper() for c in letters) / len(letters) > 0.8:
        def unshout(m: re.Match[str]) -> str:
            w = m.group(0)
            return w if len(w) <= 3 and w.isupper() and w not in _SHOUT_WORDS else w.lower()
        return re.sub(r"[A-Za-z]+", unshout, text)

    def fix(m: re.Match[str]) -> str:
        w = m.group(0)
        if re.fullmatch(r"[A-Z][a-z]+", w) and (len(w) >= 3 or w in _SMALL):
            return w.lower()
        return w
    out = re.sub(r"[A-Za-z]+", fix, text)
    return "a " + out[2:] if out.startswith("A ") else out


def _article(following: str) -> str:
    """'a' or 'an' for the words that follow: 'an $8.5M', 'an $11M', 'an ARPA-E', 'a $2.2M'."""
    w = following.lstrip()
    m = re.match(r"\$(\d+)", w)
    if m:
        n = m.group(1)
        return "an" if n[0] == "8" or n in ("11", "18") else "a"
    return "an" if w[:1].lower() in ("a", "e", "i", "o", "u") else "a"


# Places a long project title can be cut and still read as a phrase.
_BREAKS = (": ", "; ", " - ", " for ", " with ", " using ", " via ", " through ", " to ",
           " by ", " in ", " on ", " at ", " from ", " that ", " toward ", " towards ",
           " enabled by ", " enabling ", " powering ", " leveraging ", " based on ")
_DANGLING = re.compile(r"\s+(for use|for|to|of|in|on|with|and|or|the|an|a)$", re.I)


def _shorten(text: str, budget: int) -> str:
    """Fit a phrase into `budget` characters, cutting at a natural break."""
    text = " ".join(text.split()).rstrip(" .")
    if len(text) <= budget:
        return text
    best = 0
    for b in _BREAKS:
        i = text.rfind(b, 0, budget + len(b))
        if 20 <= i <= budget and i > best:
            best = i
    if best:
        out = text[:best]
    else:
        cut = text.rfind(" ", 0, budget)
        out = (text[:cut] if cut > 0 else text[: budget - 1]) + "…"
    tail = "…" if out.endswith("…") else ""
    out = out.rstrip("… ,;:-")
    while _DANGLING.search(out):
        out = _DANGLING.sub("", out).rstrip(" ,;:-")
    return out + tail


def _strip_html(s: str | None) -> str:
    if not s:
        return ""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s)).replace("\xa0", " ").split())


_KEEP_UPPER = {"LLC", "PLLC", "LLP", "LP", "PBC", "USA", "US", "AI", "II", "III", "IV"}
_NAME_MAP = {"CO": "Co", "LTD": "Ltd"}
_NAME_SMALL = {"OF", "AND", "THE", "FOR", "IN", "AT", "BY", "TO"}


def display_name(raw: str, context: str = "") -> str:
    """De-shout an ALL-CAPS registry name; anything already mixed-case is kept.

    'TRELLIS ROBOTICS INC' -> 'Trellis Robotics Inc', 'AI SCOPE INC' ->
    'AI Scope Inc', 'HEAT2POWER INC.' -> 'Heat2Power Inc.'. Two-letter and
    vowel-less runs (AI, LLC, CNC) stay as they are. When `context` (the award's own
    text) spells a word with inner capitals, that spelling wins: 'MXENE INC'
    -> 'MXene Inc'. The raw name is kept as an alias by the caller.

    Kept beside base.display_name because that one has no award text to read
    inner capitals from. The legal suffix stays on here (the hint name is part
    of a stored signal's identity); the resolver drops it for display.
    """
    name = " ".join((raw or "").split())
    letters = [c for c in name if c.isalpha()]
    if not letters or not all(c.isupper() for c in letters):
        return name

    def from_context(w: str) -> str | None:
        if len(w) < 4 or not context:
            return None
        for m in re.finditer(rf"(?<![A-Za-z0-9]){re.escape(w)}(?![A-Za-z0-9])", context, re.I):
            seen = m.group(0)
            if seen not in (seen.lower(), seen.upper(), seen.capitalize()):
                return seen
        return None

    out = []
    for i, tok in enumerate(name.split(" ")):
        def fix(m: re.Match[str], first: bool = i == 0) -> str:
            w = m.group(0)
            if w in _KEEP_UPPER:
                return w
            if w in _NAME_MAP:
                return _NAME_MAP[w]
            if w in _NAME_SMALL:
                return w.capitalize() if first else w.lower()
            if len(w) <= 2 or not re.search(r"[AEIOUY]", w):
                return w
            return from_context(w) or w.capitalize()

        out.append(re.sub(r"[A-Z]+", fix, tok))
    return " ".join(out)


def company_domain_from_email(company: str, email: str | None) -> str | None:
    """The PI's email domain, only when it visibly belongs to the awardee.

    Free-mail, university and government addresses say nothing about the
    company, so the domain must contain the first word of the company name
    (or be contained in the squashed name). Otherwise None.

    This is a lead, not the company's website: it is stored as the
    `pi_email_domain` metric and never as `EntityHint.domain`. Checked
    against the SBIR.gov website column it agreed 23 times out of 24, and
    the miss (parallelbot.com for a company whose site is parallel-robot.com)
    would have stopped the resolver from merging that company's Form D with
    its award, because domain is a hard key.
    """
    if not isinstance(email, str) or "@" not in email:
        return None
    host = email.rsplit("@", 1)[-1].strip().lower()
    if host in _FREEMAIL or re.search(r"\.(edu|gov|mil)(\.[a-z]{2})?$", host) or ".ac." in host:
        return None
    dom = clean_domain(host)
    if not dom:
        return None
    label = re.sub(r"[^a-z0-9]", "", dom.rsplit(".", 1)[0])
    tokens = re.findall(r"[a-z0-9]+", normalize_name(company))
    if not tokens or not label:
        return None
    if len(tokens[0]) >= 3 and tokens[0] in label:
        return dom
    if len(label) >= 4 and label in "".join(tokens):
        return dom
    return None


def _person_name(raw: str | None) -> str:
    """'Dr. Yong Jiang' -> 'Yong Jiang', 'VIVEK BABU' -> 'Vivek Babu'."""
    name = " ".join((raw or "").split())
    name = re.sub(r"^(dr|mr|mrs|ms|prof|professor)\.?\s+", "", name, flags=re.I)
    return name.title() if name.isupper() else name


def _edu_domain(email: str | None) -> str | None:
    if not isinstance(email, str) or "@" not in email:
        return None
    host = email.rsplit("@", 1)[-1].strip().lower()
    return host if re.search(r"\.edu(\.[a-z]{2})?$", host) or ".ac." in host else None


# ------------------------------------------------------------------------ NSF

_NSF_TITLE = re.compile(r"^\s*(SBIR|STTR)\s+(Phase\s+I(?!I)|Fast-Track)\s*:\s*(.+)$", re.I | re.S)
# Every abstract ends with this sentence. The program's own name ("Small
# Business Innovation Research (SBIR)") is left as NSF wrote it: it is not a
# thesis term.
_NSF_BOILERPLATE = re.compile(r"This award reflects NSF'?s statutory mission.*$", re.S)


def clean_abstract(text: str | None) -> str:
    return " ".join(_NSF_BOILERPLATE.sub(" ", _strip_html(text)).split())


_NSF_KIND = {"SBIR Phase I": "nsf_sbir_phase1", "STTR Phase I": "nsf_sttr_phase1",
             "SBIR Fast-Track": "nsf_sbir_fast_track", "STTR Fast-Track": "nsf_sttr_fast_track"}


def parse_nsf_award(rec: dict) -> dict | None:
    """Normalise one NSF award record. None unless it is a Phase I or Fast-Track."""
    m = _NSF_TITLE.match(rec.get("title") or "")
    if not m:
        return None
    program = f"{m.group(1).upper()} {'Fast-Track' if 'fast' in m.group(2).lower() else 'Phase I'}"
    # `date` is the award date (NSF's "initial amendment date"); a later
    # amendment moves `latestAmendmentDate`, never this field.
    when = parse_date(rec.get("date"))
    raw_name = " ".join(html.unescape(str(rec.get("awardeeName") or rec.get("awardee") or "")).split())
    if not when or not raw_name or not rec.get("id"):
        return None
    pi = " ".join(filter(None, [(rec.get("piFirstName") or "").strip(), (rec.get("piLastName") or "").strip()]))
    pi = _person_name(pi or rec.get("pdPIName"))
    co_pis = []
    entries = rec.get("coPDPI") or []
    for entry in [entries] if isinstance(entries, str) else entries:
        # "Allison M Okamura aokamura@stanford.edu"
        parts = str(entry).split()
        email = parts[-1] if parts and "@" in parts[-1] else None
        name = _person_name(" ".join(parts[:-1] if email else parts))
        if name:
            co_pis.append({"name": name, "edu": _edu_domain(email)})
    city = (rec.get("awardeeCity") or "").strip().title()
    state = (rec.get("awardeeStateCode") or "").strip().upper()
    return {
        "id": str(rec["id"]),
        "program": program,
        "nsf_title": " ".join(html.unescape(rec.get("title") or "").split()),
        "topic": " ".join(html.unescape(m.group(3)).split()),
        "area": " ".join(str(rec.get("program") or "").split()) or None,
        "abstract": clean_abstract(rec.get("abstractText")),
        "date": when,
        "raw_name": raw_name,
        "name": display_name(raw_name, f"{rec.get('title') or ''} {rec.get('abstractText') or ''}"),
        "location": ", ".join(p for p in (city, state) if p) or None,
        "total": _amount(rec.get("estimatedTotalAmt")),
        "obligated": _amount(rec.get("fundsObligatedAmt")),
        "pi": pi or None,
        "pi_edu": _edu_domain(rec.get("piEmail")),
        "co_pis": co_pis,
        "pi_email_domain": company_domain_from_email(raw_name, rec.get("piEmail")),
        "uei": (rec.get("ueiNumber") or "").strip() or None,
        "start": parse_date(rec.get("startDate")),
        "end": parse_date(rec.get("expDate")),
    }


def nsf_text(topic: str, abstract: str, area: str | None = None) -> str:
    """What is classified and stored: the award title, NSF's program label for
    the award in NSF's own words, then the abstract."""
    parts = (topic.rstrip(". "), f"NSF program area: {area}" if area else "", abstract)
    return ". ".join(p for p in parts if p)


def nsf_on_thesis(topic: str, abstract: str, area: str | None = None) -> bool:
    """Thesis gate for one award: its title, its abstract and NSF's program label."""
    title = classify(topic)
    labelled = classify(nsf_text(topic, "", area))["fit"]
    full = classify(nsf_text(topic, abstract, area))["fit"]
    if full < MIN_FIT:
        return False
    title_carries_it = bool(title["terms"]) and labelled >= NSF_TITLE_FIT
    if not (title_carries_it or full >= NSF_ABSTRACT_FIT):
        return False
    if _NSF_SOFTWARE_TITLE.search(topic) and title["fit"] < STRONG_TERM_FIT:
        return False
    if area and _NSF_LIFE_SCIENCE.search(area):
        sectors = title.get("sectors") or {}
        return max(sectors.get("robotics", 0.0), sectors.get("autonomy", 0.0)) >= STRONG_TERM_FIT
    return True


def nsf_strength(program: str, prior_awards: int | None) -> float:
    """A Phase I is a solid, dated step. Fast-Track (about one award in ten,
    with Phase II money committed up front) and a first NSF award score
    higher; a repeat awardee scores lower."""
    s = 0.38
    if "Fast-Track" in program:
        s += 0.15
    if prior_awards == 0:
        s += 0.07
    elif prior_awards is not None and prior_awards >= 3:
        s -= 0.10
    return round(s, 2)


def nsf_signal(a: dict, prior_awards: int | None) -> Signal:
    # The headline number is NSF's "total intended award amount". On a
    # Fast-Track only the Phase I slice is obligated at first, hence "up to".
    amount = a["total"] or a["obligated"]
    partly = bool(amount and a["obligated"] and a["obligated"] < amount)
    money = f"{'up to ' if partly else ''}{_money(amount)} " if amount else ""
    # The title says "NSF Phase I award" and leaves "SBIR"/"STTR" out: the
    # shorter head leaves more room for what the award is for. The exact
    # program is in `kind` and metrics["program"].
    stage = "Fast-Track" if "Fast-Track" in a["program"] else "Phase I"
    head = f"Won {money}NSF {stage} award for "
    title = head + _shorten(_sentence_case(a["topic"]), TITLE_MAX - len(head))

    metrics: dict[str, Any] = {"nsf_award_id": a["id"], "program": a["program"]}
    if re.fullmatch(r"\d{7}", a["id"]):
        # Shared with sbir_awards, which reports the same award from SBIR.gov:
        # the scorer counts signals with one award_key once.
        metrics["award_key"] = "nsf:" + a["id"]
    if a.get("area"):
        metrics["nsf_program_area"] = a["area"]
    if amount:
        metrics["amount_usd"] = amount
    if a["obligated"]:
        metrics["funds_obligated_usd"] = a["obligated"]
    if prior_awards is not None:
        metrics["nsf_prior_awards"] = prior_awards
    if a["uei"]:
        metrics["uei"] = a["uei"]
    if a["pi_email_domain"]:
        metrics["pi_email_domain"] = a["pi_email_domain"]
    if a["start"]:
        metrics["project_start"] = iso(a["start"])
    if a["end"]:
        metrics["project_end"] = iso(a["end"])

    people = []
    if a["pi"]:
        people.append(Person(name=a["pi"], role="Principal Investigator",
                             affiliations=[a["name"]] + ([a["pi_edu"]] if a["pi_edu"] else [])))
    for co in a["co_pis"]:
        # A co-PI is named on the award, usually from the partner research
        # institution. NSF does not say they work for the company, so the role
        # says "on the NSF award" and the company is not listed as an affiliation.
        people.append(Person(name=co["name"], role="Co-Principal Investigator on the NSF award",
                             affiliations=[co["edu"]] if co["edu"] else []))

    url = NSF_PAGE.format(id=a["id"])
    return Signal(
        source=SLUG, family=FAMILY, kind=_NSF_KIND.get(a["program"], "nsf_sbir_phase1"),
        entity=EntityHint(
            name=a["name"],  # no domain: NSF publishes none (see company_domain_from_email)
            aliases=[a["raw_name"]] if a["raw_name"] != a["name"] else [],
            one_liner=a["topic"], location=a["location"],
            links={"nsf_award": url},
        ),
        title=title, occurred_at=iso(a["date"]), url=url,
        value=float(amount) if amount else None, unit="USD" if amount else None,
        strength=nsf_strength(a["program"], prior_awards),
        metrics=metrics, people=people,
        text=nsf_text(a["topic"], a["abstract"], a.get("area")),
    )


def _nsf_prior_awards(a: dict) -> int | None:
    """NSF awards to the same UEI dated before this one. None when unknown."""
    if not a["uei"]:
        return None
    data = http.get_json(NSF_API, params={"ueiNumber": a["uei"], "rpp": NSF_PAGE_SIZE}, ttl=24 * 3600)
    resp = (data.get("response") if isinstance(data, dict) else None) or {}
    rows = resp.get("award")
    if resp.get("serviceNotification") or not isinstance(rows, list):
        return None
    # Trust the answer only if it is about this UEI: every row carries it and
    # the award in hand is among them. If the API ever stopped honouring the
    # filter it would hand back unrelated awards.
    if any((rec.get("ueiNumber") or "").strip().upper() != a["uei"].upper() for rec in rows):
        return None
    if len(rows) < NSF_PAGE_SIZE and a["id"] not in {str(rec.get("id")) for rec in rows}:
        return None
    prior = 0
    for rec in rows:
        d = parse_date(rec.get("date"))
        if str(rec.get("id")) != a["id"] and d and d <= a["date"]:
            prior += 1
    return prior


_SBIR_GOV_HIT = re.compile(r'<a href="/awards/\d+">(.*?)</a>', re.S)


def _title_key(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", html.unescape(re.sub(r"<[^>]+>", " ", s or "")).lower())


def sbir_gov_lists(page: str, nsf_title: str) -> bool:
    """True when a www.sbir.gov/awards?keywords=<award number> page shows this award."""
    want = _title_key(nsf_title)
    return bool(want) and any(_title_key(m.group(1)) == want for m in _SBIR_GOV_HIT.finditer(page or ""))


def covered_by_sbir_awards(a: dict) -> bool:
    """Will the sbir_awards collector report this same award?

    It does when SBIR.gov lists the award and the award title alone is on
    thesis. SBIR.gov runs behind NSF and carries few NSF STTR rows, so the
    awards left for this collector are the newest ones, the STTR ones, and
    the ones whose thesis fit only shows in the abstract or needs NSF's
    program label.

    Not every overlap is caught. sbir_awards also keeps a title that holds
    one strong keyword when the topic code in the SBIR.gov file backs it
    ("solar" under Energy Technologies). The NSF feed carries a different
    label and no such code, so that award is not deferred and both
    collectors report it: 6 of the 13 emitted here on 2026-10-01. The shared
    award_key keeps the score from counting it twice.
    """
    if classify(a["topic"])["fit"] < MIN_FIT:
        return False
    page = http.get(SBIR_GOV_SEARCH, params={"keywords": a["id"]}, ttl=SBIR_GOV_TTL, timeout=40, retries=1)
    return sbir_gov_lists(page, a["nsf_title"])


def _collect_nsf(ctx: Context, cap: int | None) -> Iterable[Signal]:
    records: dict[str, dict] = {}
    for phrase in NSF_PHRASES:
        offset = 0
        for _ in range(NSF_MAX_PAGES):
            try:
                data = http.get_json(NSF_API, params={
                    "keyword": phrase, "dateStart": ctx.since.strftime("%m/%d/%Y"),
                    "rpp": NSF_PAGE_SIZE, "offset": offset})
                resp = (data.get("response") if isinstance(data, dict) else None) or {}
                batch = [rec for rec in resp.get("award") or [] if isinstance(rec, dict)]
                total = int((resp.get("metadata") or {}).get("totalCount") or 0)
            except Exception as e:
                ctx.warn(f"nsf: {phrase} offset {offset} failed: {e}")
                break
            if resp.get("serviceNotification"):
                ctx.warn(f"nsf: {phrase}: {resp['serviceNotification']}")
                break
            for rec in batch:
                if rec.get("id"):
                    records.setdefault(str(rec["id"]), rec)
            offset += len(batch)
            # Stop on an empty page, at the stated total, or (when the API
            # states no total) on a short page.
            if not batch or (total and offset >= total) or (not total and len(batch) < NSF_PAGE_SIZE):
                break

    awards = []
    for rec in records.values():
        try:
            a = parse_nsf_award(rec)
            if a is None or not (ctx.since <= a["date"] <= ctx.today):
                continue
            if nsf_on_thesis(a["topic"], a["abstract"], a["area"]):
                awards.append(a)
        except Exception as e:
            ctx.warn(f"nsf: could not parse award {rec.get('id')}: {e}")
    awards.sort(key=lambda a: (a["date"], a["id"]), reverse=True)
    ctx.log(f"nsf: {len(records)} Phase I / Fast-Track awards since {ctx.since}, {len(awards)} on thesis")

    emitted = deferred = lookup_failures = 0
    for a in awards:
        if cap is not None and emitted >= cap:
            break
        # Two failures in a row and SBIR.gov is treated as down for this run.
        # The sbir_awards collector emits nothing without that site either,
        # so reporting the award here cannot double it.
        if lookup_failures < 2:
            try:
                if covered_by_sbir_awards(a):
                    deferred += 1
                    continue
                lookup_failures = 0
            except Exception as e:
                lookup_failures += 1
                ctx.warn(f"nsf: SBIR.gov check failed for award {a['id']}, reporting it here: {e}")
        try:
            prior = _nsf_prior_awards(a)
        except Exception as e:
            ctx.warn(f"nsf: prior-award lookup failed for {a['raw_name']}: {e}")
            prior = None
        try:
            yield nsf_signal(a, prior)
            emitted += 1
        except Exception as e:
            ctx.warn(f"nsf: could not build signal for award {a['id']}: {e}")
    ctx.log(f"nsf: {emitted} emitted, {deferred} left to sbir_awards (already listed on SBIR.gov)")


# --------------------------------------------------------------------- ARPA-E

def parse_arpae_project(rec: dict) -> dict | None:
    """Normalise one project record from the index or the node endpoint.

    The index hands back a mix of 'basic' and 'full' records and which is
    which changes between calls; `has_detail` says whether the description,
    term dates and contact came along. None unless a private company leads.
    """
    at = rec.get("attributes") or {}
    f = at.get("fields") or {}
    orgs = f.get("organization") or []
    if not orgs:
        return None
    of = orgs[0].get("fields") or {}
    org = " ".join(html.unescape(of.get("title") or "").split())
    types = [t.get("name") for t in of.get("type") or [] if isinstance(t, dict)]
    title = " ".join(html.unescape(at.get("title") or f.get("title") or "").split())
    nid = str(at.get("drupal_internal__nid") or "")
    if not org or not title or not nid or "Private Company" not in types:
        return None

    # `redirect_url` (/technologies/projects/<slug>) is a 404 on the live
    # site; the first entry of `urls` is the page that actually opens.
    paths = [u for u in at.get("urls") or [] if isinstance(u, str)]
    path = next((u for u in paths if u.startswith("/programs-and-initiatives/")), None) or f"/node/{nid}"

    programs = []
    for p in f.get("related_programs") or []:
        pf = p.get("fields") or {}
        if pf.get("title"):
            # Field names are swapped upstream: `title` holds the acronym
            # (ROCKS), `acronym` holds the long form.
            programs.append((" ".join(pf["title"].split()), " ".join((pf.get("acronym") or "").split())))

    # The project's "Web Link", then the organization's. Both are the
    # company's own site on every record checked (109 of 109); a government,
    # university or military host can only be someone else's page, and
    # clean_domain turns those away.
    website = None
    links = [x for src in (f.get("website"), of.get("website")) for x in (src if isinstance(src, list) else [])]
    for w in links:
        uri = w.get("uri") if isinstance(w, dict) else w
        website = clean_domain(uri) if isinstance(uri, str) else None
        if website:
            break

    def day(raw: Any) -> date | None:
        # 'Jun 18 2026' (no comma). Anything that is not text is no date.
        return parse_date(raw) if isinstance(raw, str) else None

    return {
        "nid": nid,
        "title": title,
        "org": org,
        "url": ARPAE_SITE + path,
        "released": day(f.get("release_date") or at.get("date")),
        "term_start": day(f.get("term_start")),
        "term_end": day(f.get("term_end")),
        "amount": _amount(f.get("award")),
        "status": (f.get("status") or "").strip(),
        "location": " ".join((f.get("location") or "").replace(" ,", ",").split()) or None,
        "programs": programs,
        "technologies": [t["name"] for t in f.get("related_technologies") or [] if t.get("name")],
        "description": _strip_html(f.get("project_description")),
        "contact": _person_name(f.get("contact")) or None,
        "partners": [p["value"] for p in f.get("partner_organizations") or []
                     if isinstance(p, dict) and p.get("value")],
        "domain": website,
        "has_detail": "project_description" in f,
    }


def arpae_text(p: dict) -> str:
    """What gets classified: ARPA-E's own words about the project.

    The program's long-form name and technology areas are included; the
    program summary is not, because it describes the program's national
    rationale ("energy, technology, and defense") rather than this company.
    """
    progs = "; ".join(f"{t} ({long})" if long else t for t, long in p["programs"])
    parts = [p["title"], p["description"], f"ARPA-E program: {progs}" if progs else "",
             "Technology areas: " + ", ".join(p["technologies"]) if p["technologies"] else ""]
    return ". ".join(x.rstrip(". ") for x in parts if x)


def _arpae_topic(title: str) -> str:
    """Drop a leading project codename ('V6EM: ', 'DEEP - ') when a real description follows."""
    for sep in (": ", " - "):
        head, found, rest = title.partition(sep)
        if found and len(head) <= 25 and len(rest) >= 20 and (head.upper() == head or head.startswith("Project ")):
            return rest
    return title


def arpae_event(p: dict, since: date, today: date) -> tuple[str, date] | None:
    """The dated step inside the window, if there is one.

    'started' is the award's term start (money begins to flow); 'selected'
    is the public selection announcement. When both fall in the window the
    later one wins so a project is reported once.
    """
    events = []
    if p["released"] and since <= p["released"] <= today:
        events.append(("selected", p["released"]))
    if p["term_start"] and since <= p["term_start"] <= today:
        events.append(("started", p["term_start"]))
    return max(events, key=lambda e: e[1]) if events else None


def arpae_strength(event: str, amount: int | None, org_projects: int | None,
                   announced_earlier: bool = False) -> float:
    """ARPA-E funds a few percent of applicants with seven-figure awards, so
    any selection is solid. Award size adds a little; the company's first
    ARPA-E project adds a lot; a repeat performer (3+) is marked down.

    `announced_earlier` is for a term start whose selection is not news
    inside the window: ARPA-E selects months before a term starts, so that
    start is the same award becoming real. It starts lower and earns half
    the first-project premium. The caller sets it for every term start
    unless the feed dates the selection inside the window as well."""
    s = 0.35 if announced_earlier else (0.45 if event == "selected" else 0.40)
    if amount:
        s += 0.25 * squash(amount, 3_000_000)
    if org_projects == 1:
        s += 0.10 if announced_earlier else 0.20
    elif org_projects is not None and org_projects >= 3:
        s -= 0.10
    return round(min(max(s, 0.15), 0.95), 2)


def arpae_signal(p: dict, event: str, when: date, org_projects: int | None,
                 selected_in_window: bool = False) -> Signal:
    program = p["programs"][0][0] if p["programs"] else None
    money = f"{_money(p['amount'])} " if p["amount"] else ""
    if event == "selected":
        what = f"{money}{program + ' ' if program else ''}award"
        head = f"Selected by ARPA-E for {_article(what)} {what}: "
    else:
        what = f"{money}ARPA-E {program + ' ' if program else ''}project"
        head = f"Began {_article(what)} {what}: "
    title = head + _shorten(_sentence_case(_arpae_topic(p["title"])), TITLE_MAX - len(head))

    metrics: dict[str, Any] = {"arpa_e_node": p["nid"], "event": event, "status": p["status"]}
    if p["amount"]:
        metrics["amount_usd"] = p["amount"]
    if program:
        metrics["program"] = program
    if org_projects is not None:
        metrics["arpa_e_projects_by_org"] = org_projects
    if p["technologies"]:
        metrics["technology_areas"] = p["technologies"]
    if p["partners"]:
        metrics["partners"] = p["partners"]
    for key in ("released", "term_start", "term_end"):
        if p[key]:
            metrics[key] = iso(p[key])

    people = []
    if p["contact"]:
        people.append(Person(name=p["contact"], role="ARPA-E project contact", affiliations=[p["org"]]))

    return Signal(
        source=SLUG, family=FAMILY, kind="arpa_e_project",
        entity=EntityHint(name=p["org"], domain=p["domain"], one_liner=p["title"],
                          location=p["location"], links={"arpa_e_project": p["url"]}),
        title=title, occurred_at=iso(when), url=p["url"],
        value=float(p["amount"]) if p["amount"] else None, unit="USD" if p["amount"] else None,
        strength=arpae_strength(event, p["amount"], org_projects,
                                announced_earlier=event == "started" and not selected_in_window),
        metrics=metrics, people=people, text=arpae_text(p),
    )


def arpae_org_key(org: str) -> str:
    """One key per company for counting its ARPA-E projects.

    The index spells some companies two ways ('AutoGrid' and 'AutoGrid
    Systems, Inc.'). Counting those apart would call a second project a first
    one, so the looser key is used: a company wrongly merged with a namesake
    only loses the first-project premium.
    """
    return loose_name(org)


def _arpae_index(ctx: Context) -> tuple[list[dict], bool]:
    """Every private-company project, newest release first. (projects, complete)."""
    projects: list[dict] = []
    seen: set[str] = set()
    offset, total = 0, None
    while (total is None or offset < total) and offset < ARPAE_MAX_OFFSET:
        try:
            data = http.get_json(ARPAE_INDEX, headers=ARPAE_ACCEPT, timeout=90, params={
                "filter[custom_reference_organization_type]": ARPAE_PRIVATE_COMPANY,
                "page[offset]": offset})
            batch = data.get("data") or []
            total = int(((data.get("meta") or {}).get("count") or {}).get("count") or 0)
        except Exception as e:
            ctx.warn(f"arpa-e: index page at offset {offset} failed: {e}")
            return projects, False
        for rec in batch:
            try:
                p = parse_arpae_project(rec)
            except Exception as e:
                ctx.warn(f"arpa-e: could not parse project {rec.get('id') if isinstance(rec, dict) else rec!r}: {e}")
                continue
            # The server repeats a row now and then when a page boundary
            # shifts (one of 681 on 2026-10-01); a repeat must not count as a
            # second project for that company.
            if p and p["nid"] not in seen:
                seen.add(p["nid"])
                projects.append(p)
        offset += ARPAE_PAGE_SIZE
        if len(batch) < ARPAE_PAGE_SIZE:
            break
    return projects, True


def _arpae_detail(nid: str) -> dict | None:
    data = http.get_json(ARPAE_NODE, headers=ARPAE_ACCEPT, timeout=90, ttl=24 * 3600,
                         params={"filter[nid]": nid})
    recs = data.get("data") or []
    p = parse_arpae_project(recs[0]) if recs else None
    # Only the record that was asked for: if the filter were ignored the
    # endpoint would answer with some other node.
    return p if p and p["nid"] == str(nid) else None


def _collect_arpae(ctx: Context, cap: int | None) -> Iterable[Signal]:
    projects, complete = _arpae_index(ctx)
    if not projects:
        return
    by_org = Counter(arpae_org_key(p["org"]) for p in projects)

    # Projects worth a closer look: anything released inside the window, and
    # every live project, because the term start (which is often the only
    # date an unannounced project has) is on the detail record only. Undated
    # projects go first: that is where the recent term starts are.
    live = [p for p in projects if p["status"] in ("Active", "Selected")]
    recent = [p for p in projects if p["released"] and ctx.since <= p["released"] <= ctx.today
              and p["status"] != "Cancelled"]
    queue, seen = [], set()
    for p in recent + [p for p in live if not p["released"]] + [p for p in live if p["released"]]:
        if p["nid"] not in seen:
            seen.add(p["nid"])
            queue.append(p)

    detail_budget = None if cap is None else 4 * cap  # keeps a limited probe quick
    emitted = fetched = 0
    for p in queue:
        if cap is not None and emitted >= cap:
            break
        if _ESTABLISHED.search(p["org"]):
            continue
        try:
            if not p["has_detail"]:
                if detail_budget is not None and fetched >= detail_budget:
                    continue
                fetched += 1
                p = _arpae_detail(p["nid"]) or p
            event = arpae_event(p, ctx.since, ctx.today)
            if not event or p["status"] == "Cancelled":
                continue
            if classify(arpae_text(p))["fit"] < MIN_FIT:
                continue
            org_projects = by_org.get(arpae_org_key(p["org"])) if complete else None
            selected_in_window = bool(p["released"] and ctx.since <= p["released"] <= ctx.today)
            yield arpae_signal(p, event[0], event[1], org_projects, selected_in_window)
            emitted += 1
        except Exception as e:
            ctx.warn(f"arpa-e: project {p['nid']} ({p['org']}) failed: {e}")
    ctx.log(f"arpa-e: {len(projects)} private-company projects, {len(queue)} checked, {emitted} emitted")


# ----------------------------------------------------------------------- main

def collect(ctx: Context) -> Iterable[Signal]:
    nsf_cap = arpae_cap = None
    if ctx.limit:
        nsf_cap = max(1, round(ctx.limit * 0.7))
        arpae_cap = max(1, ctx.limit - nsf_cap)
    for name, run, cap in (("nsf", _collect_nsf, nsf_cap), ("arpa-e", _collect_arpae, arpae_cap)):
        try:
            yield from run(ctx, cap)
        except Exception as e:  # one feed failing must not lose the other
            ctx.warn(f"{name}: feed failed: {type(e).__name__}: {e}")
