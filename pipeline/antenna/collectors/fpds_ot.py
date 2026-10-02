"""FPDS Other Transaction awards: the Pentagon's first prototype money to new vendors.

Other Transaction (OT) agreements are how DIU, DARPA, CDAO, SOCOM and the
services buy prototypes from companies that are not traditional contractors,
and they appear only in the FPDS ATOM feed (USAspending has no OT award type).
Each record carries the vendor's SAM registration date, the dollars obligated
and the signing date, so a first funded OT to a company that registered months
earlier shows up here before any announcement, and often there never is one.

DoD rows are embargoed for 90 days in FPDS, so the newest award this source
can show was signed about three months ago. The collector therefore looks
back at least 240 days whatever ctx.lookback_days says. occurred_at is always
the real signing date, and metrics["public_at"] is the day the record came out
of embargo, which is the first day anyone outside the Pentagon could act on it.
"""

from __future__ import annotations

import heapq
import re
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from typing import Any, Iterable, Iterator

from .. import http
from ..config import THESIS
from ..models import EntityHint, Signal
from ..thesis import classify
from .base import Context, iso, normalize_name, parse_date, squash

SLUG = "fpds_ot"
FAMILY = "capital"
STAGE = "discover"
DESCRIPTION = "New DoD Other Transaction awards to recently SAM-registered vendors, first awards flagged"

FEED = "https://www.fpds.gov/ezsearch/FEEDS/ATOM"
ATOM = "{http://www.w3.org/2005/Atom}"
NS = "{https://www.fpds.gov/FPDS}"

# The 90-day DoD embargo leaves the newest three months empty, so a shorter
# window than this would return nothing at all.
MIN_LOOKBACK_DAYS = 240
EMBARGO_DAYS = 90  # FPDS holds every DoD action back this long after signing
PAGE_SIZE = 10  # fixed by the feed
AWARD_TYPES = ("OTHER TRANSACTION AGREEMENT", "OTHER TRANSACTION IDV")
DOD = "9700"

PAGE_TTL = 12 * 3600  # embargoed rows are released every day
HISTORY_TTL = 7 * 86400  # actions signed before a past date no longer change
HISTORY_START = "1980/01/01"
MAX_PAGE_FAILURES = 3

# Screening. A vendor that has held a SAM registration longer than this is an
# established contractor, not a new entrant.
MAX_SAM_AGE_YEARS = 8
# A single obligation above this is not a startup-scale prototype award.
MAX_OBLIGATED_USD = 50_000_000
RECENT_SAM_DAYS = 548  # 18 months: registered, then funded, inside one cycle

# Large primes, big tech, consultancies, research institutes, and late-stage
# companies everyone already tracks. Matched as whole words against the
# vendor, its alternate name and its ultimate parent.
NOT_STARTUPS = (
    "LOCKHEED MARTIN", "NORTHROP GRUMMAN", "RAYTHEON", "RTX", "GENERAL DYNAMICS", "BOEING",
    "L3HARRIS", "L3 TECHNOLOGIES", "BAE SYSTEMS", "LEIDOS", "SCIENCE APPLICATIONS INTERNATIONAL",
    "SAIC", "BOOZ ALLEN", "CACI", "HONEYWELL", "GENERAL ELECTRIC", "GE AEROSPACE", "TEXTRON",
    "HUNTINGTON INGALLS", "COLLINS AEROSPACE", "PRATT & WHITNEY", "SIERRA NEVADA", "KRATOS",
    "AMENTUM", "JACOBS", "KBR", "PARSONS", "MANTECH", "PERATON", "ELBIT", "ELBITAMERICA",
    "RHEINMETALL", "THALES", "LEONARDO", "AIRBUS", "ROLLS-ROYCE", "OSHKOSH", "AEROVIRONMENT",
    "GENERAL ATOMICS", "BWXT", "BWX TECHNOLOGIES", "AEROJET", "MAXAR", "VIASAT", "MOOG",
    "CURTISS-WRIGHT", "TELEDYNE", "MERCURY SYSTEMS", "SAAB", "KONGSBERG", "DIEHL", "HENSOLDT",
    "MBDA", "QINETIQ", "CUBIC", "BATTELLE", "MITRE", "SRI INTERNATIONAL", "DRAPER",
    "AEROSPACE CORPORATION", "APPLIED RESEARCH ASSOCIATES", "SOUTHWEST RESEARCH INSTITUTE",
    "SYSTEMS & TECHNOLOGY RESEARCH", "OCEANEERING", "KENNAMETAL", "TURNER CONSTRUCTION",
    "BURNS & MCDONNELL", "MICROSOFT", "GOOGLE", "AMAZON", "ORACLE", "IBM",
    "INTERNATIONAL BUSINESS MACHINES", "CISCO", "DELL", "HEWLETT", "INTEL CORPORATION", "NVIDIA",
    "CADENCE DESIGN", "SYNOPSYS", "CARAHSOFT", "ACCENTURE", "DELOITTE", "MCKINSEY", "BCG",
    "BOSTON CONSULTING", "ALVAREZ & MARSAL", "ERNST & YOUNG", "KPMG", "PRICEWATERHOUSECOOPERS",
    "LMI CONSULTING", "PALANTIR", "C3.AI", "IONQ", "ANDURIL", "SPACE EXPLORATION TECHNOLOGIES",
    "BLUE ORIGIN", "ROCKET LAB", "CANADIAN COMMERCIAL CORPORATION",
    # Venture-backed, but at a billion-dollar valuation and in every defense deck.
    "SARONIC", "SHIELD AI", "EPIRUS", "SKYDIO", "APPLIED INTUITION", "SCALE AI", "HELSING",
    "CHAOS INDUSTRIES", "DEFENSE UNICORNS", "GECKO ROBOTICS",
)
# Consortium managers front for the real performer, who is not in the record.
CONSORTIUM_MANAGERS = (
    "ONE NATION INNOVATION", "ADVANCED TECHNOLOGY INTERNATIONAL", "SOSSEC",
    "NATIONAL SECURITY TECHNOLOGY ACCELERATOR", "NSTXL", "CONSORTIUM MANAGEMENT GROUP",
    "NATIONAL ARMAMENTS CONSORTIUM", "MEDICAL TECHNOLOGY ENTERPRISE CONSORTIUM",
    "NATIONAL CENTER FOR MANUFACTURING SCIENCES", "NATIONAL SPECTRUM CONSORTIUM", "CONSORTIUM",
)
# SAM self-certifications that mark a vendor as something other than a
# venture-backable company. (isCorporateEntityTaxExempt and
# isInternationalOrganization are left out: for-profit firms tick them.)
NON_COMPANY_FLAGS = {
    "isEducationalInstitution", "isNonprofitOrganization", "isOtherNotForProfitOrganization",
    "isFoundation", "isHospital", "isFederalGovernment", "isFederalGovernmentAgency",
    "isFederallyFundedResearchAndDevelopmentCorp", "isUSGovernmentEntity", "isStateGovernment",
    "isLocalGovernment", "isTribalGovernment", "isForeignGovernment",
    "isPrivateUniversityOrCollege", "isStateControlledInstitutionofHigherLearning",
}
# A sole proprietor (FPDS spells the flag this way) registered under a
# personal name is an individual, not a company. Single-member LLCs tick the
# same box and are kept.
SOLE_PROPRIETOR_FLAG = "isSolePropreitorship"
_LEGAL_FORM = re.compile(r"\b(LLC|L\.L\.C|INC|INCORPORATED|CORP|CORPORATION|CO|COMPANY|LTD|LIMITED|LP|PBC|GMBH|PTY|OU)\b")
_NON_COMPANY_NAME = re.compile(
    r"\b(UNIVERSITY|COLLEGE|REGENTS|TRUSTEES|INSTITUTE OF TECHNOLOGY|FOUNDATION|"
    r"RESEARCH INSTITUTE|SCHOOL OF|CONSULTING|CONSULTANTS|"
    # Investment firms, advisers and labour suppliers are not product companies.
    r"PARTNERS|CAPITAL|ADVISORS|ADVISORY|STAFFING)\b"
)
# Agreements that move no money and select no one: consortium membership
# paperwork and equipment loans.
_NOT_AN_AWARD = re.compile(r"\b(MEMBERSHIP AGREEMENT|BAILMENT)\b")

# The Pentagon also uses OTs for things the thesis does not cover. A record
# whose vendor or requirement names one of these is dropped unless its own
# words are firmly on thesis: life sciences and medicine, advertising and
# outreach, buildings, and hired experts rather than a product.
_OFF_THESIS = re.compile(
    r"\b(BIO|BIOLOG\w*|BIOTECH\w*|BIOMANUFACTUR\w*|BIOMEDIC\w*|BIOPHARMA\w*|BIOSCIENCES?|"
    r"BIOSYSTEMS?|THERAP\w*|VACCIN\w*|CLINICAL|MEDICAL|MEDICINES?|PHARMA\w*|DNA|RNA|GENES?|"
    r"GENETIC\w*|GENOM\w*|CRISPR|PROTEINS?|BLOOD|DISEASES?|PATHOGENS?|SUICIDE|KETAMINE|"
    r"ANALGESIC\w*|SCREWWORM|OPTOGENETICS?|NANOPORE|PHYSIOLOGICAL|PROPHYLA\w*|"
    r"ADVERTISING|MULTIMEDIA|MEDIA|MARKETING|FESTIVAL|RECRUIT\w*|"
    r"ARCHITECT AND ENGINEERING|TROOP HOUSING|BARRACKS|CAMPUS|"
    r"SUBJECT MATTER EXPERTS?|SMES?)\b"
)
# The same test on the product-service code: equipment repair, utilities and
# housekeeping, social and medical services, real property, medical supplies,
# farm supplies, live animals and food.
_OFF_THESIS_PSC = re.compile(r"^(J|S|G|Q|X|Y|Z|65|87|88|89)")
# Content fit that outweighs an off-thesis word: two strong thesis terms.
FIRM_FIT = 0.6
_STRONG_TERMS = frozenset(t for groups in THESIS.values() for t in groups.get("strong", ()))

# Short buyer names for titles and text, keyed on the contracting agency as printed.
_AGENCY_LABEL = {
    "DEPT OF THE ARMY": "the Army",
    "DEPT OF THE NAVY": "the Navy",
    "DEPT OF THE AIR FORCE": "the Air Force",
    "DEFENSE ADVANCED RESEARCH PROJECTS AGENCY (DARPA)": "DARPA",
    "U.S. SPECIAL OPERATIONS COMMAND (USSOCOM)": "SOCOM",
    "MISSILE DEFENSE AGENCY (MDA)": "the Missile Defense Agency",
    "WASHINGTON HEADQUARTERS SERVICES (WHS)": "Washington HQ Services",
    "IMMEDIATE OFFICE OF THE SECRETARY OF DEFENSE": "the Office of the Secretary of Defense",
    "DEFENSE HEALTH AGENCY (DHA)": "the Defense Health Agency",
    "DEFENSE LOGISTICS AGENCY": "the Defense Logistics Agency",
    "DEFENSE THREAT REDUCTION AGENCY (DTRA)": "the Defense Threat Reduction Agency",
    "DEFENSE INFORMATION SYSTEMS AGENCY (DISA)": "the Defense Information Systems Agency",
}
DEPARTMENT_LABEL = "the Department of Defense"  # when the agency is one not listed above
MILITARY_SERVICES = ("the Army", "the Navy", "the Air Force")
# Titles shorten this name to save room. The text spells it out, which is how
# the thesis vocabulary knows it.
_SIGNER_IN_FULL = {"Washington HQ Services": "Washington Headquarters Services"}
# Office HQ0845 is the Defense Innovation Unit; its rows print the office
# name only as "DIRECTOR".
DIU_OFFICE = "HQ0845"
TITLE_MAX = 109


# ---------------------------------------------------------------- parsing

def _find(root: ET.Element, tag: str) -> ET.Element | None:
    return root.find(f".//{NS}{tag}")


def _text(root: ET.Element, tag: str) -> str | None:
    el = _find(root, tag)
    if el is None or el.text is None:
        return None
    return " ".join(el.text.split()) or None


def _attr(root: ET.Element, tag: str, attr: str) -> str | None:
    el = _find(root, tag)
    if el is None:
        return None
    val = el.get(attr)
    return " ".join(val.split()) if val else None


def _money(root: ET.Element, tag: str) -> float | None:
    raw = _text(root, tag)
    if raw is None:
        return None
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


def _day(root: ET.Element, tag: str) -> date | None:
    raw = _text(root, tag)
    return parse_date(raw[:10]) if raw else None


def parse_award(root: ET.Element) -> dict[str, Any]:
    """Flatten one FPDS record (OtherTransactionAward or OtherTransactionIDV)."""
    record_type = root.tag.replace(NS, "")
    # The record's own id block is the first child; a referenced IDV, when
    # present, sits in a later sibling and must not be mistaken for it.
    ident = next((c for c in root if c.tag.endswith("ID")), root)
    where = _find(root, "vendorLocation")
    where = root if where is None else where
    country = _find(where, "countryCode")
    state = _find(where, "state")
    return {
        "record_type": record_type,
        "is_idv": record_type.endswith("IDV"),
        "piid": _text(ident, "PIID"),
        "mod": _text(ident, "modNumber"),
        "signed": _day(root, "signedDate"),
        "effective": _day(root, "effectiveDate"),
        "completion": _day(root, "ultimateCompletionDate"),
        "created": _day(root, "createdDate"),
        "obligated": _money(root, "obligatedAmount"),
        "ceiling": _money(root, "baseAndAllOptionsValue"),
        "agency_name": _attr(root, "contractingOfficeAgencyID", "name"),
        "office_id": _text(root, "contractingOfficeID"),
        "office_name": _attr(root, "contractingOfficeID", "name"),
        "funding_office_name": _attr(root, "fundingRequestingOfficeID", "name"),
        "agreement_type": _text(root, "typeOfAgreement"),
        "description": _text(root, "descriptionOfContractRequirement"),
        "nontraditional": _attr(root, "nonTraditionalGovernmentContractorParticipation", "description"),
        "psc": _text(root, "PSCCode"),
        "psc_description": _attr(root, "PSCCode", "description"),
        "vendor": _text(root, "vendorName"),
        "vendor_alt": _text(root, "vendorAlternateName"),
        "uei": _text(root, "UEI"),
        "parent_uei": _text(root, "ultimateParentUEI"),
        "parent_name": _text(root, "ultimateParentUEIName"),
        "cage": _text(root, "cageCode"),
        "city": _text(where, "city"),
        "state": (state.text or "").strip() or None if state is not None else None,
        "country": (country.text or "").strip() or None if country is not None else None,
        "country_name": country.get("name") if country is not None else None,
        "sam_registered": _day(root, "registrationDate"),
        "consortium": (_text(root, "consortiaFlag") or "").upper() == "Y",
        "solicitation": _attr(root, "solicitationProcedures", "description"),
        "flags": sorted(
            el.tag.replace(NS, "") for el in root.iter()
            if el.text == "true" and el.tag.replace(NS, "").startswith("is")
        ),
    }


def parse_feed(xml_text: str) -> tuple[list[dict[str, Any]], int | None, list[str]]:
    """One ATOM page -> (awards, `start` of the last page or None, entry errors)."""
    feed = ET.fromstring(xml_text)
    last = None
    for link in feed.findall(f"{ATOM}link"):
        if link.get("rel") == "last":
            m = re.search(r"[?&]start=(\d+)", link.get("href") or "")
            if m:
                last = int(m.group(1))
    awards, errors = [], []
    for entry in feed.findall(f"{ATOM}entry"):
        try:
            content = entry.find(f"{ATOM}content")
            root = next(iter(content)) if content is not None else None
            if root is None:
                raise ValueError("entry has no FPDS record")
            awards.append(parse_award(root))
        except Exception as e:  # one malformed entry must not lose the page
            errors.append(f"{type(e).__name__}: {e}")
    return awards, last, errors


# -------------------------------------------------------------- screening

def _word_match(names: Iterable[str | None], needles: Iterable[str]) -> str | None:
    blob = " | ".join(" ".join(re.sub(r"[,]", " ", n.upper()).split()) for n in names if n)
    for needle in needles:
        if re.search(rf"(?<![A-Z0-9]){re.escape(needle)}(?![A-Z0-9])", blob):
            return needle
    return None


def requirement_text(a: dict[str, Any]) -> str:
    """What the record says is being bought."""
    parts = [a.get("description")]
    # R&D product-service codes ("NATIONAL DEFENSE R&D SERVICES; ...") are the
    # same on every row. Hardware and software codes say something.
    if a.get("psc") and not a["psc"].upper().startswith("A"):
        parts.append(a.get("psc_description"))
    return ". ".join(p.rstrip(". ") for p in parts if p)


def award_text(a: dict[str, Any]) -> str:
    """The requirement as printed, then who bought it in plain words, for thesis classification.

    Every record here is a Department of Defense buy (department 9700). That
    is structured evidence the row is on thesis, and often the only evidence:
    requirements read "CORNERSTONE OTA", "BASE AWARD", "PSP 7". So the text
    says it. The buyer is context, though, not the product, and is written
    the way the thesis vocabulary weighs it: "DoD" and the signer's short name
    ("the Army", "DARPA", "DIU") are two context terms, enough to pass on
    their own and light enough that one product word in the requirement
    (drone, thruster, solar) names the sector instead. The names as FPDS
    prints them ("DEFENSE ADVANCED RESEARCH PROJECTS AGENCY", "IMMEDIATE OFFICE
    OF THE SECRETARY OF DEFENSE") would label every row defense; they are kept
    in metrics.

    Every signer named today is thesis vocabulary. One that is not (an office
    label with no service to place it under) would leave "DoD" as the only
    term, which the classifier does not accept alone, so for that one the
    department is written out in full.
    """
    kind = {"PROTOTYPE": "prototype ", "PRODUCTION": "production "}.get((a.get("agreement_type") or "").upper(), "")
    what = f"{kind}other transaction {'IDV' if a.get('is_idv') else 'agreement'}"
    signer = signer_text(a)
    if signer is None:
        tail = f"Department of Defense {what}"
    else:
        department = "DoD" if classify(signer)["terms"] else "Department of Defense"
        tail = f"{department} {what} signed by {signer}"
    return ". ".join(p for p in (requirement_text(a), tail) if p)


def names_a_product(requirement: str) -> bool:
    """True when what is being bought carries a strong thesis term ("UNMANNED AIRCRAFT").

    The classifier holds a lone word such as "unmanned" just under the gate,
    because with nothing around it the word can mean anything. Here the
    surroundings are known: the words are a Pentagon requirement, so one
    strong term does name a product on thesis. This reads the requirement
    only. A vendor's name ("FUSION PARTNERS") is not what is being bought.
    """
    return any(t in _STRONG_TERMS for t in classify(requirement)["terms"])


def screen(a: dict[str, Any], since: date, today: date) -> str | None:
    """Reason to drop this award, or None to keep it."""
    if not a.get("piid") or not a.get("vendor") or not a.get("uei"):
        return "incomplete"
    if a.get("mod") not in (None, "0"):
        return "modification"
    signed = a.get("signed")
    if signed is None or signed < since or signed > today:
        return "out_of_window"
    names = [a["vendor"], a.get("vendor_alt"), a.get("parent_name")]
    if a.get("consortium") or _word_match(names, CONSORTIUM_MANAGERS):
        return "consortium"
    if _NOT_AN_AWARD.search((a.get("description") or "").upper()):
        return "not_an_award"
    flags = set(a.get("flags") or ())
    if NON_COMPANY_FLAGS & flags or _NON_COMPANY_NAME.search(a["vendor"].upper()):
        return "not_a_company"
    if SOLE_PROPRIETOR_FLAG in flags and not _LEGAL_FORM.search(a["vendor"].upper()):
        return "not_a_company"
    if _word_match(names, NOT_STARTUPS):
        return "prime_or_widely_known"
    registered = a.get("sam_registered")
    if registered is None or registered > signed:
        return "no_sam_date"
    if (signed - registered).days > MAX_SAM_AGE_YEARS * 365.25:
        return "sam_registration_too_old"
    if (a.get("obligated") or 0) > MAX_OBLIGATED_USD:
        return "award_too_large"
    # Thesis. Every row here is a Department of Defense prototype or production
    # buy, so the buyer alone puts it in defense. What is left to remove is the
    # work the thesis does not cover, and paper agreements with no money in
    # them unless their own words are on thesis.
    own_words = " ".join(filter(None, [a["vendor"], a.get("vendor_alt"), requirement_text(a)]))
    fit = classify(own_words)["fit"]
    off = _OFF_THESIS.search(own_words.upper()) or _OFF_THESIS_PSC.match((a.get("psc") or "").upper())
    if off and fit < FIRM_FIT:
        return "off_thesis"
    unfunded = (a.get("obligated") or 0) <= 0 and (a.get("ceiling") or 0) <= 0
    if unfunded and fit < 0.3 and not names_a_product(requirement_text(a)):
        return "unfunded_and_off_thesis"
    # The contract's fit test, on the text the signal will carry. award_text
    # states the Department of Defense buyer in two thesis terms, so every row
    # passes it, which is why the two screens above do the real work.
    if classify(" ".join([a["vendor"], award_text(a)]))["fit"] < 0.3:
        return "off_thesis"
    return None


# ---------------------------------------------------------------- wording

def fmt_usd(x: float) -> str:
    """$499K, $1.2M, $10M, $28.5M: one decimal on millions, dropped when it is zero."""
    for unit, suffix in ((1e9, "B"), (1e6, "M")):
        if x >= unit * 0.9995:
            return f"${x / unit:.1f}".removesuffix(".0") + suffix
    if x >= 1_000:
        return f"${x / 1e3:.0f}K"
    return f"${x:,.0f}"


def fmt_age(days: int) -> str:
    if days < 60:
        return f"{days} day{'' if days == 1 else 's'}"
    if days < 730:
        return f"{round(days / 30.44)} months"
    return f"{days / 365.25:.1f} years"


def _agency_label(a: dict[str, Any]) -> str | None:
    return _AGENCY_LABEL.get(" ".join((a.get("agency_name") or "").upper().split()))


def buyer_label(a: dict[str, Any]) -> str:
    if (a.get("office_id") or "").upper() == DIU_OFFICE:
        return "DIU"
    office = (a.get("office_name") or "").upper()
    if "CHIEF DIGITAL & AI" in office:
        return "CDAO"
    if "SPACE DEVELOPMENT AGENCY" in office:
        return "the Space Development Agency"
    return _agency_label(a) or DEPARTMENT_LABEL


def signer_text(a: dict[str, Any]) -> str | None:
    """The signer as award_text names it, or None when only the department is known.

    An office that sits inside a military service is named with the service
    the record puts it under: "the Space Development Agency, part of the Air
    Force".
    """
    label = buyer_label(a)
    if label == DEPARTMENT_LABEL:
        return None
    service = _agency_label(a)
    if service in MILITARY_SERVICES and service != label:
        return f"{label}, part of {service}"
    return _SIGNER_IN_FULL.get(label, label)


def make_title(a: dict[str, Any], first: bool) -> str:
    """The most detailed sentence that fits; detail is dropped from the right.

    "First federal contract on record" is said only when `first` is set, which
    means FPDS, the government's record of contract actions, holds nothing
    earlier for the vendor (see vendor_history). The agreement is called a
    prototype or a production OT only when the record says so.
    """
    kind = {"PRODUCTION": "production OT", "PROTOTYPE": "prototype OT"}.get(
        (a.get("agreement_type") or "").upper(), "OT")
    buyer = buyer_label(a)
    age = f"{fmt_age((a['signed'] - a['sam_registered']).days)} after SAM registration"
    obligated = a.get("obligated") or 0.0
    ceiling = a.get("ceiling") or 0.0
    if obligated > 0:
        head = (f"First federal contract on record: {fmt_usd(obligated)} {kind} from {buyer}" if first
                else f"Won {fmt_usd(obligated)} {kind} from {buyer}")
        cap = f", {fmt_usd(ceiling)} ceiling" if ceiling >= 2 * obligated else ""
        options = [f"{head}{cap}, {age}", f"{head}, {age}", f"{head}{cap}", head]
    elif ceiling > 0:
        head = (f"First federal contract on record: {kind} with {buyer}" if first
                else f"Signed {'an' if kind == 'OT' else 'a'} {kind} with {buyer}")
        cap = f", $0 obligated against a {fmt_usd(ceiling)} ceiling"
        options = [f"{head}{cap}, {age}", f"{head}{cap}", f"{head}, {fmt_usd(ceiling)} ceiling"]
    else:
        head = (f"First federal contract on record: unfunded {kind} with {buyer}" if first
                else f"Signed an unfunded {kind} with {buyer}")
        options = [f"{head}, {age}", f"Unfunded {kind} with {buyer}, {age}"]
    for t in options:
        if len(t) <= TITLE_MAX:
            return t
    return options[-1][:TITLE_MAX]


# --------------------------------------------------------------- strength

def _lerp(x: float, pts: list[tuple[float, float]]) -> float:
    if x <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return pts[-1][1]


# SAM registration age at signing (years) -> how new the vendor is, 1 to 0.15.
_NEWNESS = [(RECENT_SAM_DAYS / 365.25, 1.0), (3.0, 0.7), (5.0, 0.4), (float(MAX_SAM_AGE_YEARS), 0.15)]
MONEY_HALF_USD = 750_000
CEILING_CREDIT, CEILING_CREDIT_MAX_USD = 0.10, 500_000
SEASONED_ACTIONS = 50


def strength_of(a: dict[str, Any], first: bool, prior_count: int | None = None) -> float:
    """0..1, built from who got the award as much as from how big it is.

    a funded award               0.18
    + newness     up to 0.25     SAM registration 18 months or less before signing
                                 counts in full, fading to 0.15 of that at 8 years
    + money       up to 0.17     dollars obligated, half credit at $750K; an
                                 unexercised ceiling adds a tenth of itself, at
                                 most $500K, because it is intent and not money
    + first award 0.10 to 0.40   nothing earlier in FPDS for this vendor; worth
                                 more the newer the registration and the larger
                                 the award
    - seasoned    0.05           50 or more earlier FPDS actions

    A first award of $1M or more to a company that registered within 18 months
    lands above 0.85. A repeat award to a three-year-old registrant sits near
    0.5, an old hand's small order near 0.2. An agreement with no dollars and
    no ceiling stays between 0.15 and 0.30.
    """
    obligated = a.get("obligated") or 0.0
    ceiling = max(a.get("ceiling") or 0.0, obligated)
    years = (a["signed"] - a["sam_registered"]).days / 365.25
    newness = _lerp(years, _NEWNESS)
    if ceiling <= 0:
        return round(0.15 + 0.10 * newness + (0.05 if first else 0.0), 3)
    committed = obligated + min(CEILING_CREDIT * (ceiling - obligated), CEILING_CREDIT_MAX_USD)
    money = squash(committed, MONEY_HALF_USD)
    s = 0.18 + 0.25 * newness + 0.17 * money
    if first:
        s += 0.10 + 0.15 * newness + 0.15 * money
    elif prior_count is not None and prior_count >= SEASONED_ACTIONS:
        s -= 0.05
    return round(min(max(s, 0.0), 1.0), 3)


# ------------------------------------------------------------------ fetch

def _fpds_date(d: date) -> str:
    return d.strftime("%Y/%m/%d")


def _feed_url(query: str) -> str:
    return f"{FEED}?FEEDNAME=PUBLIC&q={urllib.parse.quote(query, safe=':')}"


def evidence_url(piid: str) -> str:
    """The base award record itself, openable in a browser."""
    return _feed_url(f'PIID:"{piid}" MODIFICATION_NUMBER:"0"')


def vendor_history_url(uei: str) -> str:
    return _feed_url(f'VENDOR_UEI:"{uei}"')


def _pages(ctx: Context, award_type: str, since: date) -> Iterator[dict[str, Any]]:
    """New DoD awards of one type, newest signing date first."""
    query = (f'AWARD_TYPE:"{award_type}" DEPARTMENT_ID:"{DOD}" '
             f'SIGNED_DATE:[{_fpds_date(since)},{_fpds_date(ctx.today)}] MODIFICATION_NUMBER:"0"')
    start, failures = 0, 0
    final: int | None = None  # `start` of the last page, once a page has said so
    while True:
        params = {"FEEDNAME": "PUBLIC", "q": query, "start": start, "sortBy": "SIGNED_DATE", "desc": "Y"}
        try:
            awards, last, errors = parse_feed(http.get(FEED, params=params, ttl=PAGE_TTL, timeout=60))
            if not awards and not errors:
                # FPDS now and then answers a good query with an empty feed.
                # Page 0, and any page before the known last one, must hold
                # records, so ask again past the cache rather than stop early
                # and silently lose the rest of the window.
                awards, last, errors = parse_feed(http.get(FEED, params=params, ttl=0, timeout=60))
                if not awards and not errors and final is None:
                    ctx.warn(f"fpds_ot: {award_type} feed returned no records at all since {since}")
                    return
                if not awards and not errors:
                    raise ValueError("empty page before the last one")
        except Exception as e:
            failures += 1
            ctx.warn(f"fpds_ot: {award_type} page start={start} failed: {type(e).__name__}: {e}")
            start += PAGE_SIZE
            if failures >= MAX_PAGE_FAILURES or (final is not None and start > final):
                return
            continue
        if last is not None:
            final = last
        for err in errors:
            ctx.warn(f"fpds_ot: bad entry on {award_type} page start={start}: {err}")
        for a in awards:
            if a.get("signed"):
                yield a
        if final is None or start >= final:
            return
        start += PAGE_SIZE


def _clause(field: str, value: str) -> str:
    return '{}:"{}"'.format(field, value.replace('"', " "))


def _history(where: str, first_day: str, last_day: date, oldest_first: bool = False, fresh: bool = False):
    """Page 0 of every FPDS action, any agency, matching the `where` clauses.

    `fresh` asks past the cache, for a second opinion on an empty answer.
    """
    params = {"FEEDNAME": "PUBLIC", "start": 0,
              "q": f"{where} SIGNED_DATE:[{first_day},{_fpds_date(last_day)}]"}
    if oldest_first:
        params.update(sortBy="SIGNED_DATE", desc="N")
    awards, last, _ = parse_feed(http.get(FEED, params=params, ttl=0 if fresh else HISTORY_TTL, timeout=60))
    return awards, last


# Words that say what kind of legal entity a vendor is, and words that mark a
# US or government-sales arm of a business that trades under the same name.
_LEGAL_WORDS = {
    "INC", "INCORPORATED", "LLC", "LC", "LLP", "LP", "CORP", "CORPORATION", "CO", "COMPANY", "LTD",
    "LIMITED", "PBC", "PLC", "PC", "GMBH", "PTY", "OU", "SA", "SAS", "AG", "BV", "AB", "OY", "SRL",
}
_ARM_WORDS = {
    "US", "USA", "USG", "FEDERAL", "GOVERNMENT", "GOV", "NORTH", "AMERICA", "AMERICAS", "HOLDINGS",
    "HOLDING", "GROUP", "INTERNATIONAL", "GLOBAL",
}


# The FPDS index does not hold these, and a search clause on one matches nothing.
_UNINDEXED_WORDS = {"A", "AN", "AND", "AT", "BY", "FOR", "IN", "NOT", "OF", "ON", "OR", "THE", "TO", "WITH"}


def brand_words(name: str) -> list[str]:
    """The words a business trades under: 'GECKO USG, LLC' -> ['GECKO'],
    'HDT ROBOTICS LLC' -> ['HDT', 'ROBOTICS'], 'Q-CTRL INC' -> ['Q', 'CTRL']."""
    words = re.findall(r"[A-Z0-9]+", name.upper())
    while len(words) > 1 and words[-1] in _LEGAL_WORDS | _ARM_WORDS:
        words.pop()
    return [w for w in words if w not in _UNINDEXED_WORDS] or words


def _bare(name: str | None) -> str:
    """A vendor name with case, spacing and punctuation removed."""
    return re.sub(r"[^A-Z0-9]+", "", (name or "").upper())


def prior_actions(uei: str, before: date) -> tuple[int, bool]:
    """(count, exact) of FPDS actions for this UEI signed before `before`.

    The feed gives no total, so past one page the count is the floor implied
    by the last-page link.
    """
    awards, last = _history(_clause("VENDOR_UEI", uei), HISTORY_START, before - timedelta(days=1))
    if last:
        return last + 1, False
    return len(awards), True


def older_records(where: str, a: dict[str, Any]) -> list[dict[str, Any]]:
    """The oldest matching actions that predate this award, at most a page.

    The window runs through the signing day, so a healthy response always
    holds at least the award itself. An empty one is an error, never "no
    history": that is what keeps a hiccup at FPDS from minting a first award.
    An empty answer is asked again past the cache before it is given up on,
    so one hiccup does not stay cached for a week.
    """
    awards, _ = _history(where, HISTORY_START, a["signed"], oldest_first=True)
    if not awards:
        awards, _ = _history(where, HISTORY_START, a["signed"], oldest_first=True, fresh=True)
    if not awards:
        raise ValueError(f"{where} lookup returned nothing, not even {a['piid']}")
    return [x for x in awards if x.get("signed") and x["signed"] < a["signed"]]


def vendor_history(a: dict[str, Any]) -> dict[str, Any]:
    """{"prior": (count, exact), "first": bool, "re_registered": bool, "namesake": str | None}.

    A first award needs four clean answers. Nothing older under the vendor's
    own UEI. Nothing older anywhere under its ultimate parent. Nothing older
    under its exact name (an old contractor on a new registration, which is
    dropped). And nothing older under any vendor name that carries all of its
    brand words: 'GECKO USG, LLC' is not a first-time winner while Gecko
    Robotics has years of awards, nor 'HDT ROBOTICS LLC' after 'HDT ROBOTICS,
    INC.', nor 'Q-CTRL INC' after 'Q-CTRL PTY LTD'. That last test also demotes
    a few genuine newcomers that share a one-word name with an unrelated
    vendor. Their award is still emitted, without the claim: missing a first
    costs a little strength, a wrong one costs the reader's trust.
    """
    prior = prior_actions(a["uei"], a["signed"])
    if prior[0] > 0:
        return {"prior": prior, "first": False, "re_registered": False, "namesake": None}
    n = len(older_records(_clause("VENDOR_UEI", a["uei"]), a))
    parent = a.get("parent_uei")
    if n == 0 and parent and parent != a["uei"]:
        n = len(older_records(_clause("ULTIMATE_UEI", parent), a))
    if n > 0:
        return {"prior": (n, False), "first": False, "re_registered": False, "namesake": None}
    if older_records(_clause("VENDOR_FULL_NAME", a["vendor"]), a):
        return {"prior": (0, True), "first": False, "re_registered": True, "namesake": a["vendor"]}
    words = brand_words(a["vendor"])
    related = older_records(" ".join(_clause("VENDOR_NAME", w) for w in words), a) if words else []
    if not related:
        return {"prior": (0, True), "first": True, "re_registered": False, "namesake": None}
    # The same name but for punctuation ('FOSTECH, INC.' and 'FOSTECH, INC') is
    # the exact-name case again, which the exact-name search cannot see.
    same = next((x for x in related if _bare(x.get("vendor")) == _bare(a["vendor"])), None)
    return {"prior": (0, True), "first": False, "re_registered": same is not None,
            "namesake": (same or related[0]).get("vendor")}


# ---------------------------------------------------------------- collect

def _unshout(s: str | None) -> str | None:
    """'SOUTH SAN FRANCISCO' -> 'South San Francisco', 'MCLEAN' -> 'McLean'; mixed case is left alone."""
    if not s or not s.isupper() or len(s) <= 3:
        return s
    return re.sub(r"\bMc([a-z])", lambda m: "Mc" + m.group(1).upper(), s.title())


def _location(a: dict[str, Any]) -> str | None:
    if not a.get("city"):
        return None
    city = _unshout(a["city"])
    if (a.get("country") or "USA") == "USA":
        return ", ".join(p for p in (city, a.get("state")) if p)
    return ", ".join(p for p in (city, _unshout(a.get("country_name")) or a.get("country")) if p)


def public_date(a: dict[str, Any], today: date) -> date:
    """The first day the record could be seen in the public feed.

    The embargo runs EMBARGO_DAYS from the signing date. About one record in a
    hundred is typed into FPDS later than that (signed in March, created in
    August) and shows up the day it is created, so that day is the floor. A
    record that is in the feed before its embargo would end was never held
    back, and has been public since it was entered. Never after `today`.
    """
    entered = max(a["signed"], a.get("created") or a["signed"])
    released = a["signed"] + timedelta(days=EMBARGO_DAYS)
    return min(max(released, entered) if released <= today else entered, today)


def build_signal(a: dict[str, Any], history: dict[str, Any] | None, today: date) -> Signal:
    """One kept award -> Signal. `history` is None when the lookup failed, and
    then nothing is claimed about whether this is a first award."""
    first = bool(history and history["first"])
    prior = history["prior"] if history else None
    obligated = a.get("obligated") or 0.0
    ceiling = a.get("ceiling") or 0.0
    metrics: dict[str, Any] = {
        "sam_registration_age_days": (a["signed"] - a["sam_registered"]).days,
        "ceiling_usd": ceiling,
        "is_idv": int(bool(a.get("is_idv"))),
        "is_production": int((a.get("agreement_type") or "").upper() == "PRODUCTION"),
        "piid": a["piid"],
        "uei": a["uei"].upper(),
        "sam_registered": iso(a["sam_registered"]),
        # Strength decays from here, not from the signing date nobody could see.
        "public_at": iso(public_date(a, today)),
    }
    if obligated > 0:
        metrics["amount_usd"] = obligated
    if a.get("cage"):
        metrics["cage"] = a["cage"].upper()
    # The buyer as FPDS prints it; title and text use plain names.
    for key, field in (("contracting_office", "office_id"), ("contracting_office_name", "office_name"),
                       ("contracting_agency", "agency_name")):
        if a.get(field):
            metrics[key] = a[field]
    if prior is not None:
        metrics["prior_fpds_actions" if prior[1] else "prior_fpds_actions_at_least"] = prior[0]
        metrics["first_fpds_award"] = int(first)
        if history.get("namesake"):
            # Why a vendor with no history of its own is not called a first-time winner.
            metrics["earlier_awards_to_similar_name"] = history["namesake"]

    aliases = []
    alt = a.get("vendor_alt")
    if alt and normalize_name(alt) != normalize_name(a["vendor"]):
        aliases.append(alt)
    return Signal(
        source=SLUG,
        family=FAMILY,
        kind="ot_first_award" if first else "ot_award",
        entity=EntityHint(
            name=a["vendor"],
            aliases=aliases,
            location=_location(a),
            links={"fpds_history": vendor_history_url(a["uei"])},
        ),
        title=make_title(a, first),
        occurred_at=iso(a["signed"]),
        url=evidence_url(a["piid"]),
        value=obligated,
        unit="USD",
        strength=strength_of(a, first, prior[0] if prior else None),
        metrics=metrics,
        text=award_text(a) or None,
    )


def collect(ctx: Context) -> Iterable[Signal]:
    since = ctx.today - timedelta(days=max(ctx.lookback_days, MIN_LOOKBACK_DAYS))
    feeds = [_pages(ctx, t, since) for t in AWARD_TYPES]
    newest_first = heapq.merge(*feeds, key=lambda a: a["signed"], reverse=True)

    seen: set[str] = set()
    entities: set[str] = set()
    dropped: dict[str, int] = {}
    emitted = early = 0
    for a in newest_first:
        if ctx.limit and len(entities) >= ctx.limit:
            break
        try:
            if a.get("piid") in seen:
                continue
            seen.add(a.get("piid") or "")
            reason = screen(a, since, ctx.today)
            history = None
            if not reason:
                try:
                    history = vendor_history(a)
                    if history["re_registered"]:
                        # Same legal name, older awards, different UEI: an
                        # established contractor under a fresh registration.
                        reason = "re_registered_contractor"
                except Exception as e:
                    ctx.warn(f"fpds_ot: history lookup failed for {a['vendor']} ({a['uei']}): "
                             f"{type(e).__name__}: {e}")
            if reason:
                dropped[reason] = dropped.get(reason, 0) + 1
                continue
            yield build_signal(a, history, ctx.today)
            entities.add(a["uei"])
            emitted += 1
            early += a["signed"] + timedelta(days=EMBARGO_DAYS) > ctx.today
        except Exception as e:
            ctx.warn(f"fpds_ot: skipped {a.get('piid')}: {type(e).__name__}: {e}")
    if early:
        # public_at rests on the embargo holding: nothing signed this recently should be visible.
        ctx.warn(f"fpds_ot: {early} kept records were in the feed less than {EMBARGO_DAYS} days after signing")
    ctx.log(f"fpds_ot: {len(seen)} new DoD OT records since {since}, {emitted} kept, dropped {dropped}")
