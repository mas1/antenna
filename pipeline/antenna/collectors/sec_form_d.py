"""SEC EDGAR Form D: private financing rounds, disclosed by the issuer itself.

A company that sells stock, SAFEs or notes privately must file a Form D with
the SEC within 15 days of the first sale. That puts the round, its size, the
investor count and the names of the officers and directors on the public
record weeks before any announcement, and many of these rounds are never
announced at all. Form D carries no description of the business, so this
collector emits every young operating corporation whose own name does not
place it outside the thesis, and leaves thesis fit to the join with other
sources. Public registrants and issuers EDGAR has known for more than five
years are dropped: they are not early, whatever year the form gives.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import Any, Iterable, Iterator

from .. import http
from ..models import EntityHint, Person, Signal
from ..thesis import classify
from .base import Context, iso, parse_date

SLUG = "sec_form_d"
FAMILY = "capital"
STAGE = "discover"
DESCRIPTION = "Form D private rounds by young operating corporations, with officers and directors"

# How many days of the EDGAR daily form index to walk.
# Form D gets its own, longer window. A notice is filed within fifteen days
# of first sale and companies often announce months later, so the filing
# that matters for lead time can be half a year old. Old filings carry
# almost no momentum (the capital family halves every 45 days); they are
# kept for the timeline.
FORM_D_DAYS = 200

ARCHIVES = "https://www.sec.gov/Archives"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"

LISTING_TTL = 3600  # the quarter directory gains a file every evening
IDX_TTL = 7 * 86400  # a posted daily index does not change
XML_TTL = 30 * 86400  # a filed form never changes
HISTORY_TTL = 30 * 86400  # refreshed sooner when it lacks the filing in hand

# http paces www.sec.gov at 0.15 s between requests whatever the thread count,
# so four workers stay under 7 requests a second (about 5 measured). The
# filing-history calls to data.sec.gov run between batches, never alongside.
FETCH_THREADS = 4
PROGRESS_EVERY = 500
MAX_PEOPLE = 12

# The filing year minus this is the oldest year of incorporation kept. The
# form only prints a year for issuers under five years old, so this is also
# the most the source can say.
MAX_AGE_YEARS = 5
# A definite offering below this is friends and family, not a venture round.
# Indefinite offerings are kept whatever has been sold so far.
MIN_OFFERING_USD = 250_000

# Self-reported industry groups that can hold a thesis company. Everything
# else (pooled funds, real estate, banking, health care, retail, restaurants,
# travel, agriculture) is dropped.
KEEP_INDUSTRIES = {
    "Other Technology",
    "Computers",
    "Telecommunications",
    "Manufacturing",
    "Other Energy",
    "Energy Conservation",
    "Electric Utilities",
    "Oil and Gas",
    "Business Services",
    "Other",
}

# Exemptions that mark an investment company (a fund), whatever it calls itself.
_FUND_EXEMPTION = re.compile(r"^3c", re.I)

# Issuer names that are plainly funds, SPVs, partnerships or property vehicles.
# Applied to the daily index row so their XML is never fetched (about 80% of
# all Form D filings). Only unambiguous markers are listed: a word that could
# sit in an operating company's name (offshore, master, partners, trust, bank)
# is left to the industry filter. Corporations are the only entity type kept
# later, so skipping LLC and LP names loses nothing.
_NAME_STOP = re.compile(
    r"""
      \bfunds?\b | \bLLC\b | \bL\.L\.C\b | \bLLLP\b | \bLP\b | \bL\.P\.?(?=\W|$)
    | \blimited\s+partnership\b | \bl(?:imi)?te?d\.?\s+liability\b
    | \ba\s+series\s+of\b | \bseries\s+llc\b | \bSPV\b | \bSCSp\b | \bSICAV\b | \bfeeder\b
    | \bco-?invest(?:ment|ors?)?\b | \binvestors\b | \binvestments?\b | \bcapital\s+partners\b
    | \bREIT\b | \bDST$ | \btrust$ | \breal\s+estate\b | \brealty\b | \bapartments?\b
    | \bmultifamily\b | \bopportunity\s+zone\b | \bQOZB?\b
    | \bbancorp\b | \bbancshares\b | \bacquisitions?\b | \bcapital\d*\b
    | \b(?:top|hold|mid|bid)co\b | \bparent,?\s+(?:inc|corp|ltd|llc)\b
    """,
    re.I | re.X,
)

# Issuer names that state a line of business the thesis does not cover. The
# funnel already drops the health care, banking, retail, restaurant and travel
# industry groups; these are the same businesses filed under "Other" or
# "Other Technology". A name that is on thesis by its own words is kept.
_OFF_THESIS_NAME = re.compile(
    r"""
      \btherapeutics?\b | \bpharma(?:ceuticals?)?\b | \bbio(?:sciences?|pharma|tech|logics?)?\b
    | \bgenomics\b | \blife\s?sciences?\b | \boncolog\w* | \bhealth(?:care)?\b | \bmedical\b
    | \bmed\s?tech\b | \bdental\b | \bclinical\b | \bveterinary\b | \bwellness\b | \bhearing\b
    | \bnutrition\b | \bfoods?\b | \bkitchens?\b | \brestaurants?\b | \bdistill(?:ing|ery|ers)\b
    | \bspirits\b | \bbrew(?:ing|ery)\b | \bseltzer\b | \bdrinks\b | \bbeverages?\b | \bwine(?:s|ry)?\b
    | \bcoffee\b | \bcannabis\b | \bhemp\b | \btinned\b | \bbeauty\b | \bcosmetics\b | \bskin(?:care)?\b
    | \bapparel\b | \bfashion\b | \bpets?\b | \bwrestling\b | \bfighting\b | \bsports?\b | \bracing\b
    | \bmusic\b | \bcinema\b | \bfilms?\b | \bgames\b | \bgaming\b | \bmedia\b | \banimation\b
    | \bfinancial\b | \bcredit\b | \bpayments?\b | \blending\b | \bmortgage\b | \binsurance\b | \bwealth\b
    | \blegal\b | \brelocation\b | \btravel\b | \bbrands\b
    """,
    re.I | re.X,
)

# Thesis terms this short are initialisms ("pcb", "cnc", "uas"). In a name that
# states an off-thesis business they do not count as the company's own words.
_INITIALS_MAX = 3

# What EDGAR and filers hang on the end of a legal name: a state tag ("/DE/"),
# a stray comma, "a Texas Corp". None of it is part of the company's name.
_STATE_TAG = re.compile(r"\s+[/\\]\s*[A-Z]{2,3}\s*[/\\]?\s*$")
_US_STATES = (
    "Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|Florida|Georgia|Hawaii|Idaho|"
    "Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|"
    "Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|New York|"
    "North Carolina|North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|"
    "South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|West Virginia|Wisconsin|Wyoming"
)
_STATE_TAIL = re.compile(
    rf",?\s+an?\s+(?:{_US_STATES})\s+(?:corp(?:oration)?|company|public\s+benefit\s+corporation)\.?$", re.I
)
_DBA = re.compile(r"\s+(?:d/b/a|dba)\s+", re.I)

# A related person that is a firm, not a human being.
_ENTITY_PERSON = re.compile(
    r"\b(?:LLC|L\.L\.C|LLP|LP|L\.P|Inc|Corp|Ltd|GmbH|Fund|Trust|Partners|Capital|Ventures|Holdings|Holdco|"
    r"Management|Advisors|Associates|Company|Group)\b\.?",
    re.I,
)

_IDX_ROW = re.compile(r"^(\S+(?: \S+)*?)\s{2,}(.+?)\s{2,}(\d+)\s+(\d{8})\s+(edgar/\S+)")
_ACCESSION = re.compile(r"(\d{10}-\d{2}-\d{6})")

_SAFE = re.compile(r"\bsafes?\b|simple\s+agreements?\s+for\s+future\s+equity", re.I)
_CONVERTIBLE = re.compile(r"convertible|\bnotes?\b", re.I)

# Forms only a public registrant files. One of these in the filing history
# means the issuer already reports to the SEC, so it is not undiscovered.
_REPORTING_FORMS = re.compile(r"^(10-K|10-Q|8-K|20-F|40-F|6-K|S-1|S-4|F-1|F-4|424B|10-12)")
# Regulation Crowdfunding and Regulation A: the issuer has raised from retail.
_CROWDFUNDING_FORMS = re.compile(r"^(C$|C/A|C-U|C-AR|C-TR|1-A|1-K|1-SA|1-U|253G)")


# --------------------------------------------------------------------------
# Daily index
# --------------------------------------------------------------------------

def parse_form_idx(text: str) -> list[dict[str, str]]:
    """Form D and D/A rows of one `form.YYYYMMDD.idx`, in file order.

    The form type is matched exactly: a prefix match would also catch
    DEF 14A, DFAN14A and friends.
    """
    rows = []
    started = False
    for line in text.splitlines():
        if not started:
            started = line.startswith("-----")
            continue
        m = _IDX_ROW.match(line)
        if not m:
            continue
        form, company, cik, filed, path = m.groups()
        if form not in ("D", "D/A"):
            continue
        acc = _ACCESSION.search(path)
        if not acc:
            continue
        rows.append({
            "form": form,
            "company": company.strip(),
            "cik": cik,
            "filed": f"{filed[:4]}-{filed[4:6]}-{filed[6:]}",
            "accession": acc.group(1),
        })
    return rows


def name_is_vehicle(name: str) -> bool:
    """True when the index row's company name is plainly a fund, SPV, LP or LLC."""
    return bool(_NAME_STOP.search(name.strip()))


def name_is_off_thesis(name: str) -> bool:
    """True when the name itself states a business outside the thesis.

    "Penelope Health", "Real American Wrestling", "Saltbox Tinned Fish Co".
    A name that is on thesis by its own words ("Apex Medical Robotics") is
    kept. One ambiguous thesis word is not enough to outrank the stated
    business: "Industrial Hemp" grows hemp. Neither are initials alone,
    however unambiguous the term is in running text: three capitals in an
    issuer's name are as often somebody's initials, and "PCB Financial" is a
    bank holding company, not a circuit-board maker.
    """
    if not _OFF_THESIS_NAME.search(name):
        return False
    thesis = classify(name)
    initials_only = all(len(t) <= _INITIALS_MAX for t in thesis["terms"])
    return thesis["fit"] < 0.3 or initials_only


def clean_name(raw: str | None) -> tuple[str | None, list[str]]:
    """(issuer name, trade names) with EDGAR's and the filer's trailing noise removed.

    "Cicero, Inc /DE/" is "Cicero, Inc"; "VoiceIt Technologies, Inc dba
    EnQuanta" is "VoiceIt Technologies, Inc" trading as "EnQuanta". Nothing is
    added and the case is left as filed.
    """
    name = " ".join((raw or "").split())
    trade: list[str] = []
    parts = _DBA.split(name)
    if len(parts) > 1 and parts[0].strip():
        name, trade = parts[0], [t.strip(" ,") for t in parts[1:] if t.strip(" ,")]
    for _ in range(2):
        name = _STATE_TAG.sub("", name)
        name = _STATE_TAIL.sub("", name)
        name = name.rstrip(" ,;")
    return (name or None), trade


def filing_urls(cik: str | int, accession: str) -> dict[str, str]:
    folder = f"{ARCHIVES}/edgar/data/{int(cik)}/{accession.replace('-', '')}"
    return {
        "xml": f"{folder}/primary_doc.xml",
        "page": f"{folder}/xslFormDX01/primary_doc.xml",  # the form as a person reads it
        "index": f"{folder}/{accession}-index.htm",
    }


def _quarters(start: date, end: date) -> list[tuple[int, int]]:
    out = []
    y, q = start.year, (start.month - 1) // 3 + 1
    while (y, q) <= (end.year, (end.month - 1) // 3 + 1):
        out.append((y, q))
        y, q = (y, q + 1) if q < 4 else (y + 1, 1)
    return out


def index_days(ctx: Context, start: date, end: date) -> list[tuple[date, str]]:
    """(day, url) for every daily form index posted in the window, newest first."""
    days = []
    for y, q in _quarters(start, end):
        base = f"{ARCHIVES}/edgar/daily-index/{y}/QTR{q}"
        try:
            listing = http.get_json(f"{base}/index.json", ttl=LISTING_TTL)
            items = (listing.get("directory") or {}).get("item") or []
        except Exception as e:  # a new quarter's folder can be missing, empty or malformed
            ctx.warn(f"sec_form_d: no daily index listing for {y} QTR{q}: {e}")
            continue
        for item in [items] if isinstance(items, dict) else items:
            m = re.fullmatch(r"form\.(\d{8})\.idx", (item.get("name") if isinstance(item, dict) else None) or "")
            if not m:
                continue
            d = parse_date(m.group(1))
            if d and start <= d <= end:
                days.append((d, f"{base}/{item['name']}"))
    return sorted(days, reverse=True)


# --------------------------------------------------------------------------
# primary_doc.xml
# --------------------------------------------------------------------------

def _txt(node: ET.Element | None, path: str) -> str | None:
    if node is None:
        return None
    v = node.findtext(path)
    v = " ".join(v.split()) if v else ""
    return v or None


def _int(s: str | None) -> int | None:
    if s is None:
        return None
    try:
        return int(float(s.replace(",", "")))
    except ValueError:
        return None


def _names(node: ET.Element | None, path: str) -> list[str]:
    if node is None:
        return []
    out = []
    for v in node.findall(path):
        s = " ".join((v.text or "").split())
        if s and s.lower() not in ("none", "n/a", "na") and s not in out:
            out.append(s)
    return out


def parse_primary_doc(xml_text: str) -> dict[str, Any]:
    """Flatten a Form D `primary_doc.xml` into the fields this collector uses.

    Only what the form states. Missing elements come back as None or empty.
    """
    root = ET.fromstring(xml_text.encode("utf-8"))
    issuer = root.find("primaryIssuer")
    offering = root.find("offeringData")
    if issuer is None or offering is None:
        raise ValueError("not a Form D document")

    year_node = issuer.find("yearOfInc")
    year_inc = _int(_txt(year_node, "value"))
    over_five = (_txt(year_node, "overFiveYears") or "").lower() == "true"
    yet_to_form = (_txt(year_node, "yetToBeFormed") or "").lower() == "true"

    people = []
    for rp in root.findall("relatedPersonsList/relatedPersonInfo"):
        parts = [_txt(rp, f"relatedPersonName/{k}") for k in ("firstName", "middleName", "lastName")]
        parts = [p for p in parts if p and p not in ("-", "--", ".", "N/A", "n/a", "NA", "None")]
        name = " ".join(parts)
        if not name:
            continue
        people.append({
            "name": name,
            "first": _txt(rp, "relatedPersonName/firstName"),
            "last": _txt(rp, "relatedPersonName/lastName"),
            "relationships": _names(rp, "relatedPersonRelationshipList/relationship"),
            "clarification": _txt(rp, "relationshipClarification"),
        })

    types = offering.find("typesOfSecuritiesOffered")
    flags = {
        key: (_txt(types, tag) or "").lower() == "true"
        for key, tag in (
            ("equity", "isEquityType"), ("debt", "isDebtType"),
            ("option", "isOptionToAcquireType"), ("underlying", "isSecurityToBeAcquiredType"),
            ("pooled", "isPooledInvestmentFundType"), ("tenant", "isTenantInCommonType"),
            ("mineral", "isMineralPropertyType"), ("other", "isOtherType"),
        )
    }
    other_desc = _txt(types, "descriptionOfOtherType")

    amounts = offering.find("offeringSalesAmounts")
    offering_raw = _txt(amounts, "totalOfferingAmount")
    indefinite = bool(offering_raw) and offering_raw.strip().lower() == "indefinite"

    filing = offering.find("typeOfFiling")
    sig = offering.find("signatureBlock/signature")
    addr = issuer.find("issuerAddress")

    return {
        "form": _txt(root, "submissionType"),
        "cik": str(_int(_txt(issuer, "cik")) or ""),
        "name": clean_name(_txt(issuer, "entityName"))[0],
        "name_as_filed": _txt(issuer, "entityName"),
        "trade_names": clean_name(_txt(issuer, "entityName"))[1],
        "city": _txt(addr, "city"),
        "state": _txt(addr, "stateOrCountry"),
        "state_name": _txt(addr, "stateOrCountryDescription"),
        "jurisdiction": _txt(issuer, "jurisdictionOfInc"),
        "entity_type": _txt(issuer, "entityType"),
        "year_inc": year_inc,
        "over_five_years": over_five,
        "yet_to_be_formed": yet_to_form,
        # Real names sit in <previousName>; <value>None</value> means there are none.
        "previous_names": _names(issuer, "issuerPreviousNameList/*"),
        "edgar_previous_names": _names(issuer, "edgarPreviousNameList/*"),
        "co_issuers": _names(root, "issuerList/issuer/entityName"),
        "people": people,
        "industry": _txt(offering, "industryGroup/industryGroupType"),
        "fund_type": _txt(offering, "industryGroup/investmentFundInfo/investmentFundType"),
        "revenue_range": _txt(offering, "issuerSize/revenueRange"),
        "exemptions": _names(offering, "federalExemptionsExclusions/item"),
        "is_amendment": (_txt(filing, "newOrAmendment/isAmendment") or "").lower() == "true",
        "previous_accession": _txt(filing, "newOrAmendment/previousAccessionNumber"),
        "first_sale": _txt(filing, "dateOfFirstSale/value"),
        "first_sale_yet_to_occur": (_txt(filing, "dateOfFirstSale/yetToOccur") or "").lower() == "true",
        "more_than_one_year": (_txt(offering, "durationOfOffering/moreThanOneYear") or "").lower() == "true",
        "securities": flags,
        "other_security": other_desc,
        "business_combination": (
            _txt(offering, "businessCombinationTransaction/isBusinessCombinationTransaction") or ""
        ).lower() == "true",
        "minimum_investment": _int(_txt(offering, "minimumInvestmentAccepted")),
        "brokers": len(offering.findall("salesCompensationList/recipient")),
        "offering_indefinite": indefinite,
        "total_offering": None if indefinite else _int(offering_raw),
        "amount_sold": _int(_txt(amounts, "totalAmountSold")),
        "investors": _int(_txt(offering, "investors/totalNumberAlreadyInvested")),
        "non_accredited": (_txt(offering, "investors/hasNonAccreditedInvestors") or "").lower() == "true",
        "signer": _txt(sig, "nameOfSigner"),
        "signer_title": _txt(sig, "signatureTitle"),
        "signed": _txt(sig, "signatureDate"),
    }


# --------------------------------------------------------------------------
# Funnel
# --------------------------------------------------------------------------

def funnel(rec: dict[str, Any], filed: date) -> str | None:
    """Why a filing is dropped, or None when it is a young operating corporation."""
    if not rec.get("name"):
        return "no issuer name"
    if rec.get("industry") not in KEEP_INDUSTRIES:
        return f"industry {rec.get('industry')}"
    if rec.get("fund_type") or rec["securities"].get("pooled"):
        return "pooled fund"
    if any(_FUND_EXEMPTION.match(e) for e in rec.get("exemptions") or []):
        return "investment company exemption"
    if rec["securities"].get("tenant") or rec["securities"].get("mineral"):
        return "property or mineral interest"
    if rec.get("entity_type") != "Corporation":
        return f"entity type {rec.get('entity_type')}"
    year = rec.get("year_inc")
    if year is None:
        return "no year of incorporation (over five years old, or not yet formed)"
    if year < filed.year - MAX_AGE_YEARS or year > filed.year:
        return f"incorporated {year}"
    if name_is_vehicle(rec["name"]):
        return "vehicle name"
    if name_is_off_thesis(rec["name"]):
        return "off-thesis name"
    if rec.get("offering_indefinite"):
        return None
    offering = rec.get("total_offering")
    if offering is None:
        return "no offering amount"
    if offering < MIN_OFFERING_USD:
        return "offering under $250k"
    return None


# --------------------------------------------------------------------------
# Filing history (data.sec.gov submissions)
# --------------------------------------------------------------------------

def summarize_history(sub: dict[str, Any], accession: str, filed: str) -> dict[str, Any]:
    """What the issuer's EDGAR filing list says about the filing in hand.

    `prior_form_d` counts Form D and D/A filings accepted before this one.
    `first_form_d` is True only when the list is complete and holds none.
    `previous` is the most recent earlier Form D or D/A, of any offering.
    `previous_in_offering` is the most recent earlier filing under the same
    SEC file number, which is the same offering: the filing an amendment
    should be compared with.
    `reporting` is True when the issuer also files as a public registrant,
    `crowdfunding` when it has filed under Regulation Crowdfunding or A.
    `filer_since` is the year of the oldest filing EDGAR holds for the issuer.
    """
    filings = sub.get("filings") if isinstance(sub, dict) else None
    filings = filings if isinstance(filings, dict) else {}
    recent = filings.get("recent") if isinstance(filings.get("recent"), dict) else {}
    forms = recent.get("form") or []
    accs = recent.get("accessionNumber") or []
    dates = recent.get("filingDate") or []
    accepted = recent.get("acceptanceDateTime") or []
    files = recent.get("fileNumber") or []
    rows = [
        {"form": forms[i] or "", "accession": accs[i] or "", "date": (dates[i] if i < len(dates) else "") or "",
         "accepted": (accepted[i] if i < len(accepted) else "") or "",
         "file": (files[i] if i < len(files) else "") or ""}
        for i in range(min(len(forms), len(accs)))
    ]
    me = next((r for r in rows if r["accession"] == accession), None)
    older = [f for f in (filings.get("files") or []) if isinstance(f, dict)]
    older_pages = bool(filings.get("files"))
    oldest = min([r["date"] for r in rows if r["date"]] + [f["filingFrom"] for f in older if f.get("filingFrom")],
                 default="")

    def before(r: dict[str, str]) -> bool:
        if r["accession"] == accession:
            return False
        if me and me["accepted"] and r["accepted"]:
            return r["accepted"] < me["accepted"]
        # No timestamp to compare: anything filed the same day counts as earlier,
        # so a tie can never be reported as a first filing.
        return bool(r["date"]) and r["date"] <= filed

    prior = [r for r in rows if r["form"] in ("D", "D/A") and before(r)]
    prior.sort(key=lambda r: (r["accepted"] or r["date"]), reverse=True)
    complete = me is not None and not older_pages
    same = next((r for r in prior if me and me["file"] and r["file"] == me["file"]), None)
    return {
        "listed": me is not None,
        "prior_form_d": len(prior),
        "first_form_d": (len(prior) == 0) if complete else (False if prior else None),
        "previous": prior[0] if prior else None,
        "previous_in_offering": same,
        "file": me["file"] if me else "",
        "reporting": any(_REPORTING_FORMS.match(r["form"]) for r in rows),
        "crowdfunding": any(_CROWDFUNDING_FORMS.match(r["form"]) for r in rows),
        "filer_since": int(oldest[:4]) if oldest[:4].isdigit() else None,
        "filings_total": len(rows),
    }


def history_drop(hist: dict[str, Any] | None, filed: date) -> str | None:
    """Why the issuer's filing history rules it out, or None.

    The form's year of incorporation is the year of the legal entity. A
    company that reincorporated or put a new holding company on top looks
    young on the form while EDGAR has filings from it going back years.
    """
    if not hist:
        return None
    if hist.get("reporting"):
        return "public registrant"
    since = hist.get("filer_since")
    if since is not None and since < filed.year - MAX_AGE_YEARS:
        return f"on EDGAR since {since}"
    return None


def baseline_accession(rec: dict[str, Any], hist: dict[str, Any] | None, accession: str) -> str | None:
    """The filing an amendment's numbers may be compared with, or None.

    Only the most recent earlier filing of the same offering will do. The
    form's own pointer usually names the original notice, so with two
    amendments on file it would credit the second with money the first had
    already reported. EDGAR's file number ties an offering's filings
    together; without it the form's pointer is used only when it is the
    issuer's immediately preceding Form D.
    """
    if not hist or not hist.get("listed"):
        return None
    same = hist.get("previous_in_offering")
    if same and same["accession"] != accession:
        return same["accession"]
    pointer = rec.get("previous_accession")
    latest = hist.get("previous")
    if not pointer or not latest or latest["accession"] != pointer or pointer == accession:
        return None
    if hist.get("file") and latest.get("file") and latest["file"] != hist["file"]:
        return None  # EDGAR says the pointer names a different offering
    return pointer


def filing_history(cik: str, accession: str, filed: str) -> dict[str, Any]:
    url = SUBMISSIONS.format(cik=int(cik))
    hist = summarize_history(http.get_json(url, ttl=HISTORY_TTL), accession, filed)
    if not hist["listed"]:
        # The cached list predates this filing. Ask again for a current one.
        hist = summarize_history(http.get_json(url, ttl=1800), accession, filed)
    return hist


# --------------------------------------------------------------------------
# Presentation
# --------------------------------------------------------------------------

def money(n: int | float, precise: bool = False) -> str:
    """$525K, $1.8M, $1.25B. Rounded for a headline; exact values go in metrics."""
    n = float(n)
    if n >= 999_950_000:
        return f"${n / 1e9:.2f}B"
    if n >= 999_500:
        return f"${n / 1e6:.2f}M" if precise else f"${n / 1e6:.1f}M"
    if n >= 1_000:
        k = round(n / 1e3, 1)
        return f"${k:.0f}K" if (k >= 100 and not precise) or k == int(k) else f"${k:.1f}K"
    return f"${n:.0f}"


def _pair(a: int | float, b: int | float) -> tuple[str, str]:
    """Two amounts for one sentence, with enough digits to tell them apart."""
    x, y = money(a), money(b)
    if x == y and a != b:
        x, y = money(a, precise=True), money(b, precise=True)
    if x == y and a != b:
        x, y = f"${a:,.0f}", f"${b:,.0f}"
    return x, y


def instrument(rec: dict[str, Any]) -> str | None:
    """The security sold, named only when the form is unambiguous about it."""
    s = rec["securities"]
    core = {k for k in ("equity", "debt", "other") if s.get(k)}
    desc = rec.get("other_security") or ""
    if core == {"other"}:
        if _SAFE.search(desc):
            return "SAFEs"
        if _CONVERTIBLE.search(desc):
            return "convertible notes"
        return None
    if core == {"equity"}:
        return "equity"
    if core == {"debt"}:
        return "debt"
    if core == {"debt", "other"} and _CONVERTIBLE.search(desc) and not _SAFE.search(desc):
        return "convertible notes"
    # The box "Option, Warrant or Other Right to Acquire Another Security"
    # ticked alone is how many filers report a SAFE or a note. It is not
    # evidence of options or warrants, so the headline does not name it.
    return None


def rights_only(rec: dict[str, Any]) -> bool:
    """Only the "option, warrant or other right to acquire" box is ticked."""
    s = rec["securities"]
    return bool(s.get("option")) and not any(s.get(k) for k in ("equity", "debt", "other"))


def make_title(rec: dict[str, Any], hist: dict[str, Any] | None, previous_sold: int | None) -> str:
    amendment = rec["form"] == "D/A"
    if amendment:
        lead = "Amended Form D"
    elif hist and hist.get("first_form_d") and not hist.get("reporting"):
        # "First" means first under this CIK. A public shell that took over a
        # private company can have an older history under another CIK, so the
        # word is withheld for registrants.
        lead = "Filed first Form D"
    else:
        lead = "Filed Form D"
    sold = rec.get("amount_sold") or 0
    offering = rec.get("total_offering")
    indefinite = rec.get("offering_indefinite") or not offering
    inst = instrument(rec)
    n = rec.get("investors") or 0

    if sold <= 0:
        size = "indefinite offering" if indefinite else f"{money(offering)} offering"
        return f"{lead}: {size}{f' of {inst}' if inst else ''}, nothing sold yet"

    if amendment and previous_sold is not None and previous_sold > 0:
        if sold == previous_sold:
            body = f"{money(sold)} sold, unchanged,"
        else:
            now, before = _pair(sold, previous_sold)
            body = f"{now} sold, {'up' if sold > previous_sold else 'down'} from {before},"
    elif indefinite:
        body = f"{money(sold)} sold of an indefinite offering"
    elif sold >= offering:
        body = f"{money(sold)} sold, the full offering,"
    elif money(sold) == money(offering):
        body = f"{money(sold)} sold, all but {money(offering - sold)} of the offering,"
    else:
        body = f"{money(sold)} sold of {money(offering)}"
    tail = ""
    if inst:
        tail += f" in {inst}"
    if n > 0:
        tail += f" from {n} investor{'' if n == 1 else 's'}"
    if not tail:
        body = body.rstrip(",")
    return f"{lead}: {body}{tail}"


def _interp(x: float, points: list[tuple[float, float]]) -> float:
    if x <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return points[-1][1]


# How much a round of this size should move a partner: (log10 of dollars
# actually sold, weight). It peaks across large seed and Series A rounds. A
# few thousand dollars is noise and nine figures is already consensus.
_SIZE_CURVE = [
    (4.0, 0.05),   # $10k
    (5.0, 0.20),   # $100k
    (5.7, 0.38),   # $500k
    (6.0, 0.46),   # $1M
    (6.3, 0.56),   # $2M
    (6.6, 0.76),   # $4M
    (6.9, 1.00),   # $8M
    (7.3, 1.00),   # $20M
    (7.7, 0.55),   # $50M
    (8.0, 0.25),   # $100M
    (8.5, 0.05),   # $300M
]
# Years since incorporation at filing -> weight.
_YOUTH = {0: 1.0, 1: 0.95, 2: 0.8, 3: 0.62, 4: 0.48, 5: 0.38}


def size_weight(usd: int | float) -> float:
    return _interp(math.log10(usd), _SIZE_CURVE) if usd and usd > 0 else 0.0


def strength_of(rec: dict[str, Any], filed: date, hist: dict[str, Any] | None,
                previous_sold: int | None = None) -> float:
    """0..1 read of one filing, on the scale in docs/COLLECTORS.md.

    0.85 and up is kept for a first Form D from a company incorporated this
    year or last that has actually sold about $6M to $30M of equity or SAFEs
    to five or fewer investors. The same round with a wider investor list, or
    a $3M to $6M first round, is 0.6 to 0.8. A first seed of $0.2M to $2M
    lands at 0.35 to 0.55. Nothing sold, a few thousand dollars, a nine-figure
    round, a loan, a late filing for an old sale and an amendment that adds
    no money all fall to 0.15 to 0.3. Measured over the 60 days to 2026-09-30
    (615 filings): 55% under 0.35, 30% from 0.35 to 0.6, 14% from 0.6 to 0.85
    and 1% above.
    """
    sold = rec.get("amount_sold") or 0
    age = max(filed.year - (rec.get("year_inc") or filed.year), 0)
    youth = _YOUTH.get(age, 0.3)
    first = None if hist is None else hist.get("first_form_d")
    if first and hist.get("reporting"):
        first = None  # see make_title: not provably a first for a registrant
    novelty = 1.0 if first else (0.7 if first is None else 0.45)
    sec = rec["securities"]
    inst = instrument(rec)
    loan = bool(sec.get("debt")) and not sec.get("equity") and inst != "convertible notes"

    if rec["form"] == "D/A":
        # An amendment matters only for the money it adds.
        if previous_sold is None:
            return 0.2 if sold > 0 else 0.15
        if sold <= previous_sold:
            return 0.15
        s = 0.15 + 0.45 * size_weight(sold - previous_sold) * (0.5 + 0.5 * youth)
    elif sold <= 0:
        return round(0.15 + 0.03 * youth, 3)  # on file, nothing raised yet
    else:
        size = size_weight(sold)
        s = 0.15 + 0.66 * size * (0.35 + 0.35 * youth + 0.30 * novelty)
        n = rec.get("investors") or 0
        if first and age <= 1 and 0 < n <= 5 and 2_000_000 <= sold <= 30_000_000 and not loan:
            s += 0.12 * size  # a few large cheques into a new company: an institutional lead

    mult = 1.0
    if loan:
        mult *= 0.5
    elif rights_only(rec):
        mult *= 0.85
    if rec.get("brokers"):
        mult *= 0.8  # a placement agent: later stage, or sold to retail
    if rec.get("non_accredited"):
        mult *= 0.85
    if rec.get("business_combination"):
        mult *= 0.6
    if (rec.get("investors") or 0) >= 50:
        mult *= 0.85  # crowd or syndicate round
    if hist and hist.get("reporting"):
        mult *= 0.5  # already a public registrant
    elif hist and hist.get("crowdfunding"):
        mult *= 0.75
    first_sale = parse_date(rec.get("first_sale"))
    if first_sale and rec["form"] == "D":
        lag = (filed - first_sale).days
        if lag > 365:
            mult *= 0.55  # a late filing for a sale that is old news
        elif lag > 120:
            mult *= 0.8
    return round(min(max(0.15 + (s - 0.15) * mult, 0.15), 0.97), 3)


def _titlecase(s: str | None) -> str | None:
    if not s:
        return None
    if s.isupper() or s.islower():
        s = " ".join(w if i and w in ("of", "and", "the") else w.capitalize()
                     for i, w in enumerate(s.lower().split()))
    return s


def location_of(rec: dict[str, Any]) -> str | None:
    """Issuer address as the form gives it. Often a registered agent, not an office."""
    code = rec.get("state") or ""
    # US states are two letters; foreign codes carry a digit (A6 Ontario, X0 UK).
    us_state = bool(re.fullmatch(r"[A-Z]{2}", code))
    region = code if us_state else _titlecase(rec.get("state_name"))
    city = _titlecase(rec.get("city"))
    if city and region and not us_state and f" {city.lower()}".endswith(f" {region.lower()}"):
        return city  # "Singapore", not "Singapore, Singapore"
    return ", ".join(p for p in (city, region) if p) or None


def _short(text: str | None, limit: int = 80) -> str | None:
    """A clarification trimmed to whole words, never cut mid-word."""
    text = (text or "").strip().rstrip(".").strip()
    if len(text) <= limit:
        return text or None
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:(")
    return f"{cut}..." if cut else None


def people_of(rec: dict[str, Any]) -> list[Person]:
    """Related persons with the relationship the form states for each.

    Executive officers first. The free-text clarification (usually a title)
    is appended, or the signer's title when the signer is that person. Phones
    and home addresses on the form are deliberately not carried.
    """
    signer = (rec.get("signer") or "").lower().replace(".", " ").replace(",", " ").split()
    ranked = sorted(
        rec.get("people") or [],
        key=lambda p: ("Executive Officer" not in p["relationships"], "Director" not in p["relationships"]),
    )
    out: list[Person] = []
    seen = set()
    for p in ranked:
        key = p["name"].lower()
        if key in seen:
            continue
        seen.add(key)
        if _ENTITY_PERSON.search(p["name"]) or not p.get("first") or not p.get("last"):
            continue  # a firm listed as promoter or director, not a person
        role = ", ".join(p["relationships"]) or None
        detail = p.get("clarification")
        if not detail and signer and rec.get("signer_title"):
            first, last = (p.get("first") or "").lower().split(), (p.get("last") or "").lower().split()
            if first and last and first[0] in signer and last[-1] in signer:
                detail = rec["signer_title"]
        detail = _short(detail)
        if detail and detail.lower() not in {r.lower() for r in p["relationships"]}:
            role = f"{role} ({detail})" if role else detail
        out.append(Person(name=p["name"], role=role))
        if len(out) >= MAX_PEOPLE:
            break
    return out


def build_signal(rec: dict[str, Any], row: dict[str, str], hist: dict[str, Any] | None,
                 previous: dict[str, Any] | None = None) -> Signal:
    """One Signal from a parsed filing, its index row and its filing history.

    `previous` is the parsed filing an amendment amends, when it could be read.
    """
    filed = parse_date(row["filed"])
    if filed is None:
        raise ValueError(f"bad filing date {row['filed']!r}")
    urls = filing_urls(row["cik"], row["accession"])
    cik = rec.get("cik") or row["cik"]
    amendment = rec["form"] == "D/A"
    sold = rec.get("amount_sold")
    offering = rec.get("total_offering")
    previous_sold = previous.get("amount_sold") if amendment and previous else None

    metrics: dict[str, Any] = {
        "year_incorporated": rec["year_inc"],
        # The SEC's identifier, as a string of digits without leading zeros
        # (the form EDGAR uses in its archive paths). The resolver joins every
        # signal that shares it, so a notice, its amendments and any other
        # SEC-keyed source land on one company whatever name each one prints.
        "cik": str(int(cik)),
        "accession": row["accession"],
        "industry_group": rec.get("industry"),
        "people_listed": len(rec.get("people") or []),
    }
    # amount_sold is the money actually raised and is what the scorer reads as
    # how well funded, and so how well known, the company already is.
    # amount_usd is the total offering, the size of the round the issuer set
    # out to raise ($285K sold of a $40M offering: amount_sold 285,000,
    # amount_usd 40,000,000). An indefinite offering states no total, so it
    # has no amount_usd. Neither does an offering with nothing sold yet, or
    # with no amount sold stated: a target nobody has paid into is not a
    # round, and the scorer reads amount_usd as money raised when it finds no
    # amount_sold. The target is always in offering_usd.
    if offering:
        metrics["offering_usd"] = offering
        if sold:
            metrics["amount_usd"] = offering
    if rec.get("offering_indefinite"):
        metrics["offering_indefinite"] = 1
    if sold is not None:
        metrics["amount_sold"] = sold
    if offering and sold is not None:
        metrics["fill_ratio"] = round(min(sold / offering, 1.0), 3)
    if rec.get("investors") is not None:
        metrics["investors"] = rec["investors"]
    if rec.get("minimum_investment") is not None:
        metrics["minimum_investment"] = rec["minimum_investment"]
    first_sale = parse_date(rec.get("first_sale"))
    if first_sale:
        metrics["first_sale_date"] = iso(first_sale)
        metrics["days_from_first_sale_to_filing"] = (filed - first_sale).days
    inst = instrument(rec)
    if inst:
        metrics["instrument"] = inst
    elif rights_only(rec):
        metrics["instrument"] = "option, warrant or other right"
    if _SAFE.search(rec.get("other_security") or ""):
        metrics["is_safe"] = 1
    if rec.get("brokers"):
        metrics["brokers"] = rec["brokers"]
    if rec.get("non_accredited"):
        metrics["non_accredited_investors"] = 1
    if rec.get("business_combination"):
        metrics["business_combination"] = 1
    if hist:
        metrics["prior_form_d_filings"] = hist["prior_form_d"]
        if hist.get("first_form_d") is not None:
            metrics["first_form_d"] = int(bool(hist["first_form_d"]))
        prev_date = parse_date((hist.get("previous") or {}).get("date"))
        if prev_date:
            metrics["days_since_previous_form_d"] = (filed - prev_date).days
        if hist.get("reporting"):
            metrics["public_registrant"] = 1
        if hist.get("crowdfunding"):
            metrics["crowdfunding_issuer"] = 1
        if hist.get("filer_since"):
            metrics["edgar_filer_since"] = hist["filer_since"]
    if amendment and previous:
        if previous_sold is not None and sold is not None:
            metrics["amount_sold_previous"] = previous_sold
            metrics["amount_sold_added"] = sold - previous_sold
        before = {p["name"].lower() for p in previous.get("people") or []}
        metrics["new_related_persons"] = sum(
            1 for p in rec.get("people") or [] if p["name"].lower() not in before
        )

    def bare(n: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", n.lower())

    # Former and trade names. One that differs from the name only in case or
    # punctuation ("Cube Security, Inc.") is not an alias.
    aliases: list[str] = []
    known = {bare(rec["name"])}
    for raw in [*rec.get("previous_names", []), *rec.get("edgar_previous_names", []),
                *rec.get("trade_names", [])]:
        n = clean_name(raw)[0]
        if n and bare(n) not in known:
            known.add(bare(n))
            aliases.append(n)
    former = [a for a in aliases if a not in (rec.get("trade_names") or [])]

    # The year on the form is the year this legal entity was incorporated.
    # That is the founding year unless the company had an earlier life: a
    # former name (an LLC that converted, a renamed shell) or filings on
    # EDGAR from before that year. Then the year stays in metrics only.
    since = (hist or {}).get("filer_since")
    founded = None if former or (since is not None and since < rec["year_inc"]) else str(rec["year_inc"])

    # Form D has no business description. The issuer's names and the wording
    # of the security are the only free text on it, and they go in as filed.
    # The industry group is left out on purpose: "Manufacturing" covers
    # distillers and canneries, and the thesis classifier would read it as a
    # fit.
    text_bits = [rec["name"]]
    if rec.get("trade_names"):
        text_bits.append("Trading as " + "; ".join(rec["trade_names"]))
    if former:
        text_bits.append("Formerly " + "; ".join(former))
    if rec.get("other_security"):
        text_bits.append(rec["other_security"])

    return Signal(
        source=SLUG,
        family=FAMILY,
        kind="form_d_amendment" if amendment else "form_d",
        entity=EntityHint(
            name=rec["name"],
            kind="company",
            aliases=aliases,
            location=location_of(rec),
            founded=founded,
            links={
                "sec_filing": urls["index"],
                "sec_filings": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={int(cik)}&type=D",
            },
        ),
        title=make_title(rec, hist, previous_sold),
        occurred_at=row["filed"],
        url=urls["page"],
        value=float(sold) if sold is not None else None,
        unit="USD" if sold is not None else None,
        strength=strength_of(rec, filed, hist, previous_sold),
        metrics=metrics,
        people=people_of(rec),
        text="\n".join(text_bits)[:600],
    )


# --------------------------------------------------------------------------
# Collect
# --------------------------------------------------------------------------

def _fetch(row: dict[str, str]) -> tuple[dict[str, str], dict[str, Any] | None, str | None]:
    try:
        xml_text = http.get(filing_urls(row["cik"], row["accession"])["xml"], ttl=XML_TTL)
        return row, parse_primary_doc(xml_text), None
    except Exception as e:  # one bad filing must not lose the run
        return row, None, f"{type(e).__name__}: {e}"[:200]


def _chunks(it: Iterable[dict[str, str]], n: int) -> Iterator[list[dict[str, str]]]:
    buf: list[dict[str, str]] = []
    for x in it:
        buf.append(x)
        if len(buf) >= n:
            yield buf
            buf = []
    if buf:
        yield buf


def collect(ctx: Context) -> Iterable[Signal]:
    start = ctx.today - timedelta(days=FORM_D_DAYS)
    days = index_days(ctx, start, ctx.today)
    if not days:
        ctx.warn("sec_form_d: no daily form index in the window")
        return
    ctx.log(f"sec_form_d: {len(days)} daily indexes, {days[-1][0]} to {days[0][0]}")

    stats = {"filings": 0, "skipped_by_name": 0, "fetched": 0, "dropped": 0, "emitted": 0, "errors": 0}
    counted: set[str] = set()  # every accession in the window, for the progress count
    queued: set[str] = set()  # accessions handed to the fetcher
    entities: set[str] = set()

    def rows() -> Iterator[dict[str, str]]:
        """Index rows worth an XML fetch, newest day first."""
        for day, url in days:
            try:
                parsed = parse_form_idx(http.get(url, ttl=IDX_TTL, timeout=60))
            except Exception as e:
                ctx.warn(f"sec_form_d: daily index {day} failed: {e}")
                continue
            for row in parsed:
                acc = row["accession"]
                if acc not in counted:
                    counted.add(acc)
                    stats["filings"] += 1
                    if stats["filings"] % PROGRESS_EVERY == 0:
                        stats["skipped_by_name"] = stats["filings"] - len(queued)
                        ctx.log(
                            f"sec_form_d: {stats['filings']} filings through {day}: "
                            f"{stats['skipped_by_name']} funds and vehicles skipped by name, "
                            f"{stats['fetched']} read, {stats['emitted']} emitted"
                        )
                # A multi-issuer filing is listed once per co-issuer. Fetch it
                # once, through the first co-issuer whose name is not a vehicle.
                if acc in queued:
                    continue
                filed = parse_date(row["filed"])
                if filed is None or filed < start or filed > ctx.today:
                    continue
                if name_is_vehicle(row["company"]):
                    continue
                queued.add(acc)
                yield row

    with ThreadPoolExecutor(max_workers=FETCH_THREADS) as pool:
        for chunk in _chunks(rows(), 8 * FETCH_THREADS):
            for row, rec, err in pool.map(_fetch, chunk):
                stats["fetched"] += 1
                if err or rec is None:
                    stats["errors"] += 1
                    ctx.warn(f"sec_form_d: {row['accession']} ({row['company']}): {err}")
                    continue
                try:
                    filed = parse_date(row["filed"])
                    if funnel(rec, filed):
                        stats["dropped"] += 1
                        continue
                    cik = rec.get("cik") or row["cik"]
                    hist = None
                    try:
                        hist = filing_history(cik, row["accession"], row["filed"])
                    except Exception as e:
                        ctx.warn(f"sec_form_d: no filing history for {rec['name']}: {e}")
                    if history_drop(hist, filed):
                        stats["dropped"] += 1
                        continue
                    previous = None
                    if rec["form"] == "D/A":
                        # What the amendment changed: read the last filing of the same offering.
                        prev_acc = baseline_accession(rec, hist, row["accession"])
                        if prev_acc:
                            try:
                                previous = parse_primary_doc(http.get(filing_urls(cik, prev_acc)["xml"], ttl=XML_TTL))
                            except Exception as e:
                                ctx.warn(f"sec_form_d: previous filing {prev_acc} of {rec['name']} unreadable: {e}")
                    sig = build_signal(rec, row, hist, previous)
                except Exception as e:
                    stats["errors"] += 1
                    ctx.warn(f"sec_form_d: {row['accession']} ({row['company']}): {type(e).__name__}: {e}")
                    continue
                stats["emitted"] += 1
                entities.add(str(rec.get("cik") or row["cik"]))
                yield sig
                if ctx.limit and len(entities) >= ctx.limit:
                    stats["skipped_by_name"] = stats["filings"] - len(queued)
                    ctx.log(f"sec_form_d: limit reached, {stats}")
                    return
    stats["skipped_by_name"] = stats["filings"] - len(queued)
    ctx.log(f"sec_form_d: done, {stats}")
