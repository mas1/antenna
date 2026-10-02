"""Federal SBIR/STTR Phase I awards on thesis, with a premium for a firm's first federal award on record.

The source is the SBIR.gov bulk award file (regenerated daily, every award
since 1983) plus the small purchase orders the Air Force SBIR/STTR contracting
office (the AFWERX office, award IDs starting FA8649) posts to USAspending. A
Phase I is often the first outside money a deep-tech company ever takes,
months or years before a priced round, and the record names the firm, its
website, headcount and the principal investigator. A firm's history is
computed from the whole SBIR.gov file and from its USAspending record, not
from the recent window.

How it works

1. The bulk CSV (about 91 MB, no abstracts) is downloaded to
   config.DOWNLOADS_DIR and streamed twice with the csv module. Pass one keeps
   Phase I rows dated inside [ctx.since, ctx.today] whose award title
   classifies on thesis (see "Thesis fit" below). Pass two collects, for
   those firms only, every other award in the file under the same UEI, the
   same DUNS, or the identical name. Nothing else is held in memory.
2. Each kept award is looked up on www.sbir.gov (search by contract number,
   then the award page). The award page is the evidence link, it supplies the
   abstract the bulk file lacks, and its contract number, UEI, firm, amount
   and start date must agree with the CSV row or the signal is dropped. No
   page, no signal.
3. USAspending gets a hard budget of waiting time per run. It is asked for
   new DoD contracts matching FA8649 of at most $200K, and then for the award
   record behind each order that is about to be emitted. The record must
   exist, name the FA8649 SBIR/STTR office, and agree on recipient, amount
   and date signed. Whatever the budget does not reach is skipped with a
   warning and picked up from cache on the next run.
4. No "first award" claim rests on SBIR.gov alone. The bulk file runs months
   behind for some agencies and misses awards outright: on 2026-10-01 it held
   no FA8649 row after 2025-09, and four firms it showed as new had earlier
   SBIR contracts on USAspending (a $1.6M order from the same office four
   months before, an Army Phase I five months before). So a firm with no
   earlier SBIR.gov row is also looked up on USAspending by UEI: any earlier
   federal contract or grant there and the title states that count instead.
   If USAspending cannot be asked, the signal goes out with no claim either
   way and a later run fills it in (same kind, so the stored row is updated).

Things a reader should know

- The window is the pipeline's (ctx.since), per the collector contract. With
  the default 120 days that is a few NSF awards a month plus whichever Air
  Force orders have cleared the 90 day DoD embargo. SBIR.gov lists no NASA or
  HHS row after 2025-09 and SBIR/STTR volume collapsed when the authorisation
  lapsed that month, so a longer --lookback mostly adds the 2025-09 cohort.
- "First federal award on record" means all of: no earlier row in the
  SBIR.gov file under the same UEI, DUNS or identical firm name; SBIR.gov's
  own firm profile does not date a first award to an earlier year; and
  USAspending lists no earlier prime contract and no earlier grant for the
  UEI. It says nothing about loans or subawards. DoD contracts appear on
  USAspending 90 days late, so for a civilian award from the last 90 days an
  earlier DoD contract can still surface later.
- "N prior awards on record" counts earlier SBIR.gov rows under the same UEI
  or DUNS (plus legacy rows under the identical name with no UEI). It equals
  SBIR.gov's own firm profile. For an Air Force order, earlier FA8649 awards
  that USAspending lists and SBIR.gov does not are added.
- Firms with 50 or more prior awards (or earlier federal contracts) are not
  emitted. Creare, Physical Sciences or a federal IT contractor taking one
  more Phase I is not an early company.
- USAspending's search index lists an order a few days before its award page
  exists (on 2026-10-01 every order dated 2026-07-01 returned 404), and DoD
  rows are embargoed 90 days, so the freshest Air Force orders visible are
  about three months old.
- USAspending rows carry no phase field. An FA8649 purchase order of at most
  $200K is Phase I sized (in the 400 days to 2026-10-01 the office's orders
  were either under $120K or over $1.0M, and all 80 of the large ones that
  also appear in the SBIR.gov file are Phase II there). That is an inference,
  so titles say "Air Force SBIR/STTR order" and never "Phase I".
- For 2026 NSF rows the date SBIR.gov calls "Award Start Date" is the day NSF
  made the award (checked against the NSF API for all ten in the window). In
  2025 rows it is sometimes the later project start date instead.
- Thesis fit is judged on the award title (program prefix removed) by the
  shared classifier. The classifier scrubs its own false friends ("hydrogen
  peroxide", "sensor fusion", "solar wind"); a few more that only turn up in
  award titles are scrubbed here ("radiological, nuclear", "solar flare",
  "solar eruptions", "nuclear magnetic", "knowledge enrichment"), and so is
  a keyword the rest of the title shows to be something else ("PCB" beside
  "pigments" or "polychlorinated" is the pollutant, a "microgrid" in an
  organic light emitting diode is a wire mesh). Titles about medicine or
  biology are dropped whatever else they say (a "pill for biological
  defense" is not defense tech, a "microprocessor for cell reprogramming"
  is not a chip). When the Department of Defense is paying (Air Force
  orders, and DoD rows in the bulk file), words that only name the customer
  ("military", "warfighter", "USAF", "Navy", "dual-use", "industrial base")
  earn no fit, and an order description that is the solicitation's own name
  is skipped. HHS and Department of Education awards are skipped: 9 of 11
  HHS title matches were biomedical ("CAR-T cell manufacturing", "nuclear
  delivery of DNA") and the other two were medical devices.
- The classifier takes one unmistakable keyword on its own ("radar",
  "microgrid", "lunar", "molten salt") and does not take one ambiguous
  keyword ("solar", "battery", "manufacturing", "turbine", "propulsion").
  A title that holds exactly one such keyword is shown to the classifier
  again together with what the source records about the award besides its
  title, in plain words: who is paying ("Air Force SBIR/STTR order", "Navy
  SBIR Phase I award", "NASA SBIR Phase I award") and, for NSF, the topic
  area NSF filed the award under ("NSF topic area: Energy Technologies").
  If the two together clear the bar the award is kept and counted as a
  one-keyword title; the same sentence is written into Signal.text. A title
  with no keyword of its own, or only a weak one ("tactical",
  "autonomous"), is never kept this way, and neither is a lone keyword
  under a funder or topic that adds nothing ("manufacturing" under Advanced
  Manufacturing is still one keyword; DOE and the Defense Logistics Agency
  add none, so "nuclear component repair" for DOE stays out). In the 120
  days to 2026-10-02 this kept 6 of 13 NSF awards (perovskite solar cells,
  flow batteries, 3D printing) and 8 of 66 Air Force orders (a turbine
  engine, solar power arrays, an electronic warfare payload) that the
  title alone would have lost. Of NASA's 294 Phase I rows from March to
  September 2025 the classifier takes 77 titles as they stand and 19 more
  on NASA's name ("propulsion", "composites", "solar array"); 31 that hold
  only a weak word ("autonomous", "lidar", "star tracker") stay out.
  SBIR.gov has listed no NASA row since 2025-09.
- NSF topic areas are named from the bulk file's "Topic Code" column using
  the list at seedfund.nsf.gov/what-we-fund (read 2026-10-01). The code is
  chosen by the applicant and is loose (an award on hospital pressure
  injuries sits under Semiconductors), which is why it only ever backs up a
  keyword that is already in the title.
- metrics["uei"] is the firm's UEI. An NSF award also carries
  metrics["award_key"] = "nsf:<7-digit NSF award id>" (SBIR.gov's "Agency
  Tracking Number" for NSF rows), so the scorer counts the award once if a
  collector reading NSF's own feed reports it under the same key.
- Air Force orders carry metrics["public_at"]: the date signed plus the 90
  days DoD contract actions are withheld from public release, and never
  later than the run date. Strength decays from that day, not from a
  signing date nobody outside the government could see.
- The CSV's website field is typed by hand and is sometimes someone else's
  site (one firm lists its fellowship programme's). A domain is emitted only
  if it resembles the firm's name or is also the PI's email domain; failing
  that the PI's email domain is used if it resembles the firm's name;
  failing that the domain is left empty.
- The CSV's "Contact" columns are the government program officer for DoD and
  NASA rows, so only the PI is emitted as a person, and no emails or phones
  are stored.
- One award is one stored row. `kind` never depends on the firm's history
  (db.fingerprint includes it), so when SBIR.gov or USAspending catch up and
  a "first" turns out not to be, the next run corrects the row in place.
"""

from __future__ import annotations

import csv
import html
import re
import threading
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from .. import config, http
from ..models import EntityHint, Person, Signal
from ..thesis import classify
from .base import Context, clean_domain, parse_date

SLUG = "sbir_awards"
FAMILY = "capital"
STAGE = "discover"
DESCRIPTION = "On-thesis SBIR/STTR Phase I awards and Air Force SBIR/STTR orders, first federal award weighted up"

CSV_URL = "https://data.www.sbir.gov/mod_awarddatapublic_no_abstract/award_data_no_abstract.csv"
CSV_FILE = "sbir_award_data_no_abstract.csv"
SITE = "https://www.sbir.gov"
USASPENDING_SEARCH = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
USASPENDING_AWARD = "https://www.usaspending.gov/award/"
USASPENDING_AWARD_API = "https://api.usaspending.gov/api/v2/awards/"

MIN_FIT = 0.3
ONE_KEYWORD_FIT = 0.465      # what the classifier gives a title holding one unmistakable keyword
DOD_EMBARGO_DAYS = 90        # DoD contract actions reach FPDS and USAspending 90 days after signing
DOD = "Department of Defense"
NSF = "National Science Foundation"
ORDER_CONTEXT = "Air Force SBIR/STTR order"
AFWERX_PREFIX = "FA8649"
AFWERX_MAX_USD = 200_000     # Phase I sized; the office's Phase II orders start above $1.0M
AFWERX_MAX_PAGES = 10        # 1,000 orders; the busiest 120 days on file hold about 500
AFWERX_BUDGET_S = 180.0      # hard budget for time spent waiting on USAspending
SITE_BUDGET_S = 480.0        # wall-clock budget for cold www.sbir.gov lookups
ESTABLISHED = 50             # prior awards (or federal contracts) at which a firm is no early company
MAX_TITLE = 109
KIND_CSV = "sbir_phase1"
KIND_ORDER = "afwerx_order"
_CSV_COLUMNS = ("Company", "Award Title", "Agency", "Phase", "Contract", "Proposal Award Date", "UEI")

# --------------------------------------------------------------------------
# Small text helpers
# --------------------------------------------------------------------------

_WS = re.compile(r"\s+")
_PROGRAM_PREFIX = re.compile(r"^\s*(SBIR|STTR)\s+(Phase\s+I{1,2}|Fast[\s-]?Track)\s*:\s*", re.I)
_FAST_TRACK = re.compile(r"^\s*(SBIR|STTR)\s+Fast[\s-]?Track\s*:", re.I)

# Phrases that trip a thesis keyword without being about the thesis. Only the
# ones config.THESIS_FALSE_FRIENDS does not already blank ("hydrogen peroxide",
# "hydrogen sulfide", "solar wind", "solar energetic particle", "nuclear
# delivery", "nuclear receptor", "nuclear localization", "sensor fusion",
# "information fusion").
_FALSE_FRIENDS = re.compile(
    r"hydrogen[\s-]+sulphide"
    # The sun as a subject of study (NASA funds forecasts of it), not solar power.
    r"|solar[\s-]+(?:flares?|storms?|system|physics|corona|eclipse|blind|eruptions?|particles?|"
    r"magnetic|and[\s-]+geomagnetic)"
    r"|nuclear[\s-]+(?:localisation|import|export|magnetic|medicine|imaging|envelope|pore|dna|"
    r"rna|transport)"
    r"|radiological,?\s+(?:and\s+|or\s+)?nuclear"   # CBRN detection is not nuclear energy
    r"|(?:knowledge|data|semantic|genome)[\s-]+enrichment",  # not uranium
    re.I,
)
# A keyword that is another thing altogether once the title says what it is about:
# PCB the pollutant (polychlorinated biphenyl, banned from pigments) is no circuit
# board, and the microgrid printed into an OLED is a mesh of wires, not a power grid.
_OTHER_MEANING = (
    (re.compile(r"(?<![a-z0-9])pcbs?(?![a-z0-9])", re.I),
     re.compile(r"polychlorinated|biphenyl|pigments?", re.I)),
    (re.compile(r"(?<![a-z0-9])microgrids?(?![a-z0-9])", re.I),
     re.compile(r"light[\s-]+emitting|(?<![a-z])oleds?(?![a-z])", re.I)),
)
# Medicine and biology: off thesis whatever else the title says. Kept narrow on
# purpose ("health monitoring" of an aircraft and material "fatigue" must pass,
# and so must a surgical robot).
_OFF_SUBJECT = re.compile(
    r"(?<![a-z])(?:therapeutics?|pills?|wounds?|grafts?|dural|opioids?|vaccines?|viral|bacterial|"
    r"fungal|pathogens?|dna|rna|drugs?|clinical|patients?|biomanufacturing|fermentation|"
    r"phyllosphere|spoilage|medical[\s-]+diagnostics?|biomedical|cell[\s-]+reprogramming)(?![a-z])",
    re.I,
)
# Words that name the buyer of a Department of Defense award, not its subject.
_CUSTOMER_WORDS = re.compile(
    r"(?<![a-z])(?:(?:defen[sc]e[\s-]+)?industrial[\s-]+base|military|warfighters?|air[\s-]+force|"
    r"usaf|ussf|space[\s-]+force|afwerx|spacewerx|army|navy|marine[\s-]+corps|darpa|socom|diu|"
    r"dod|department[\s-]+of[\s-]+(?:defense|war)|dual[\s-]+(?:use|purpose))(?![a-z])",
    re.I,
)
# Thesis terms that are a keyword in their own right (the "strong" lists).
_STRONG_TERMS = frozenset(t for groups in config.THESIS.values() for t in groups.get("strong", ()))
# An order described by its solicitation's name says nothing about the firm.
_BOILERPLATE = re.compile(r"(?<![a-z])open[\s-]+topic(?![a-z])|stakeholder[\s-]+need", re.I)
_FUSION = re.compile(r"(?<![a-z])fusion(?![a-z])", re.I)
_FUSION_ENERGY = re.compile(
    r"plasma|tokamak|stellarator|reactor|nuclear|deuterium|tritium|neutron|inertial|"
    r"magnetic confinement|fusion[\s-]+(?:energy|power|fuel|pilot|device|machine)|laser fusion",
    re.I,
)

_SKIP_AGENCIES = {"Department of Health and Human Services", "Department of Education"}

# NSF's topic areas by the code in the bulk file's "Topic Code" column, as listed
# at https://seedfund.nsf.gov/what-we-fund/ on 2026-10-01. A code that is not on
# that list (older rows carry "IT") is left unnamed.
_NSF_TOPICS = {
    "M": "Advanced Manufacturing", "AM": "Advanced Materials",
    "AA": "Advanced Systems for Scalable Analytics", "AG": "Agricultural Technologies",
    "AI": "Artificial Intelligence", "AV": "Augmented Virtual and Mixed Reality",
    "BT": "Biological Technologies", "BM": "Biomedical Technologies",
    "CT": "Chemical Technologies", "CH": "Cloud and High-Performance Computing",
    "CA": "Cybersecurity and Authentication", "DH": "Digital Health",
    "EM": "Emerging Technologies", "EN": "Energy Technologies",
    "ET": "Environmental Technologies", "HC": "Human-Computer Interaction",
    "IH": "Instrumentation and Hardware Systems", "I": "Internet of Things",
    "LC": "Learning and Cognition Technologies", "MD": "Medical Devices", "MO": "Mobility",
    "N": "Nanotechnology", "OT": "Other Topics", "PT": "Pharmaceutical Technologies",
    "PH": "Photonics", "PM": "Power Management", "QT": "Quantum Information Technologies",
    "R": "Robotics", "S": "Semiconductors", "SP": "Space", "W": "Wireless Technologies",
}

_LEGAL = {"inc", "incorporated", "llc", "lc", "corp", "corporation", "co", "company", "ltd",
          "limited", "lp", "llp", "pbc", "pllc"}

_AGENCY = {
    "Department of Defense": "DoD",
    "National Aeronautics and Space Administration": "NASA",
    "National Science Foundation": "NSF",
    "Department of Energy": "DOE",
    "Department of Health and Human Services": "HHS",
    "Department of Agriculture": "USDA",
    "Department of Commerce": "Commerce",
    "Department of Transportation": "DOT",
    "Department of Homeland Security": "DHS",
    "Department of Education": "Education",
    "Environmental Protection Agency": "EPA",
}
_BRANCH = {
    "Air Force": "Air Force",
    "Navy": "Navy",
    "Army": "Army",
    "Defense Advanced Research Projects Agency": "DARPA",
    "Missile Defense Agency": "MDA",
    "Special Operations Command": "SOCOM",
    "Space Development Agency": "SDA",
    "Defense Threat Reduction Agency": "DTRA",
    "Defense Logistics Agency": "DLA",
    "Defense Health Agency": "DHA",
    "National Geospatial-Intelligence Agency": "NGA",
    "ARPA-E": "ARPA-E",
    "National Institutes of Health": "NIH",
    "National Oceanic and Atmospheric Administration": "NOAA",
    "National Institute of Standards and Technology": "NIST",
}

# Tokens kept upper-case when an ALL-CAPS government description is made readable.
_ACRONYMS = {
    "UAS", "SUAS", "CUAS", "UAV", "UAVS", "UUV", "USV", "AUV", "UGV", "AI", "ML", "RF", "EW",
    "ISR", "GPS", "PNT", "GNSS", "IR", "EO", "LEO", "GEO", "MEO", "DOD", "USAF", "USSF", "SATCOM",
    "VTOL", "STOL", "UHF", "VHF", "CBRN", "LLM", "NLP", "API", "MRO", "ISAM", "OSAM", "MEMS",
    "ASIC", "FPGA", "LWIR", "SWIR", "MWIR", "ATAK", "ABMS", "CMMC", "ICBM", "NASA", "DARPA",
    "AFRL", "AFWERX", "USA", "SWAP", "LIDAR", "RADAR", "IOT", "GAN", "SIC", "TACFI", "STRATFI",
    "NIST", "BLOS", "ISAC", "EMI", "EMP", "AESA", "SIGINT", "ELINT", "OSINT", "COTS", "MOSA",
    "DDIL", "ACE", "CONUS", "OCONUS", "HALE", "MALE", "CAD", "CAM", "ITAR", "SOCOM",
}
_ACRONYM_CASE = {"IOT": "IoT", "GAN": "GaN", "SIC": "SiC", "LIDAR": "LiDAR", "RADAR": "radar",
                 "SUAS": "sUAS", "DOD": "DoD", "EVTOL": "eVTOL", "LI": "Li"}
# Ordinary words of one to three letters; any other short all-caps token is read as an acronym.
_SHORT_WORDS = set(
    "a an and any are arm as at aid air all bay be big bit box bus but by can car cut day do dry "
    "due end eye far few fit fly for fog gas get go gun has hot how ice in ink is it its jet key "
    "kit lab law low map may men mix mud net new no non nor not now of off oil old on one or our "
    "out own pad pay per pin pre raw ray red rig rod run sea see set six sky sub sun tag tap tax "
    "ten the tip to too top two up use van via war was way web wet who why win yet you age add "
    "aim act bar bed bid cap cup dam die dig ear eat egg fan fat fee gap gel hub ion jam job lap "
    "leg let lid log lot man mat mid mod nut odd ore pan pen pit pod pot pro we us if so my he "
    "she his her had did got put saw say ago arc art ash bad bag ban bat bee bet bin bow boy bug "
    "buy cab cat cow cry cue dot dye era fix fun fur gel gum gut hat hit ill jar jaw joy lay led "
    "lie lip mad mob mop nap nod oar oat pet pie rat rib rim rip rob row rub rug sad sap saw shy "
    "sip sit ski spy tab tan tea tie tin toe ton toy tub tug vet vow wax wig zip zoo "
    "re gen tri bi un vs".split()   # prefixes and clippings: RE-TASKING, NEXT-GEN
)

# Mailbox providers and ISPs a PI may write from. clean_domain already refuses
# gmail.com, yahoo.com, outlook.com and proton.me, so they are not repeated.
_FREEMAIL = {
    "hotmail.com", "aol.com", "icloud.com", "me.com", "mac.com", "msn.com", "live.com",
    "protonmail.com", "pm.me", "comcast.net", "att.net", "verizon.net", "sbcglobal.net",
    "ymail.com", "mail.com", "gmx.com", "zoho.com", "qq.com", "163.com", "cox.net",
    "earthlink.net", "bellsouth.net", "charter.net",
}
# Words too common in firm names to prove a domain belongs to the firm.
_GENERIC_NAME_WORDS = set(
    "advanced applied american national international global united general technologies "
    "technology systems system solutions research group laboratories engineering sciences "
    "science associates industries innovations innovation enterprises services consulting design "
    "development materials dynamics aerospace space energy robotics photonics optics defense "
    "power medical health digital scientific analytics instruments devices products "
    "manufacturing industrial software security networks sensors sensing electric electronics "
    "mechanical quantum intelligent smart creative physical optical federal america corporation "
    "company strategies holdings partners ventures works concepts composites semiconductor "
    "semiconductors autonomous autonomy drone drones orbital lunar solar nuclear fusion battery "
    "marine ocean aviation aeronautics precision integrated dynamic micro nano".split()
)


def _squeeze(s: str | None) -> str:
    return _WS.sub(" ", s or "").strip()


def firm_key(name: str | None) -> str:
    """Name key for matching one firm across SBIR.gov and USAspending rows.

    Lower-case, legal forms off, a leading "The" off. Not base.normalize_name
    because the two registries punctuate differently: here dots vanish, so
    "T.G.V. Rockets Inc." and "TGV ROCKETS INC" are one firm (normalize_name
    gives "t g v rockets"), and the registry forms "LC" and "PLLC" count as
    legal forms. Of 34,521 names in the bulk file the two keys differ on 442.
    """
    s = (name or "").lower().replace(".", "").replace("’", "").replace("'", "")
    toks = re.sub(r"[^a-z0-9]+", " ", s).split()
    if toks and toks[0] == "the":
        toks = toks[1:]
    while len(toks) > 1 and toks[-1] in _LEGAL:
        toks.pop()
    return " ".join(toks)


def contract_key(s: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def strip_program_prefix(title: str | None) -> str:
    """'SBIR Phase I: Foo' -> 'Foo' (NSF prefixes every title this way)."""
    return _PROGRAM_PREFIX.sub("", _squeeze(title))


def subject_text(title: str | None, *, buyer: bool = False) -> str:
    """An award title as the classifier should read it.

    The program prefix and the local false friends are removed, and so is a
    keyword the rest of the title shows to mean something else ("PCB" beside
    "pigments"). `buyer=True` is for awards the Department of Defense pays
    for: the words that only name the customer are removed too.
    """
    t = strip_program_prefix(title)
    t = _FALSE_FRIENDS.sub(" ", t)
    for word, cue in _OTHER_MEANING:
        if cue.search(t):
            t = word.sub(" ", t)
    if buyer:
        t = _CUSTOMER_WORDS.sub(" ", t)
    if _FUSION.search(t) and not _FUSION_ENERGY.search(t):
        t = _FUSION.sub(" ", t)  # track fusion, image fusion: not the energy kind
    return t


def title_fit(title: str | None, *, order: bool = False) -> dict:
    """Thesis classification of an award title on its own words.

    `order=True` reads it as a Department of Defense purchase (customer
    words earn nothing).
    """
    return classify(subject_text(title, buyer=order))


def nsf_topic_area(row: dict) -> str | None:
    """NSF's name for the topic area a bulk-file row is filed under, if it has one."""
    if (row.get("Agency") or "").strip() != NSF:
        return None
    return _NSF_TOPICS.get((row.get("Topic Code") or "").strip().upper())


def award_context(row: dict) -> str:
    """What the bulk file records about an award besides its title, in plain words.

    'Navy SBIR Phase I award.' or 'NSF SBIR Phase I award. NSF topic area:
    Energy Technologies.' Every word comes from the row's own columns.
    """
    program = (row.get("Program") or "").strip() or "SBIR"
    phase = "Fast-Track" if _FAST_TRACK.match(row.get("Award Title") or "") else "Phase I"
    funder = agency_label((row.get("Agency") or "").strip(), (row.get("Branch") or "").strip())
    out = _squeeze(f"{funder} {program} {phase} award.")
    area = nsf_topic_area(row)
    return f"{out} NSF topic area: {area}." if area else out


def on_thesis(title: str | None, agency: str = "", *, order: bool = False,
              context: str = "") -> dict | None:
    """The classification if the award clears the bar, else None.

    The title has to carry the subject. If the classifier takes it as it
    stands, that is the answer. If the title holds exactly one keyword and
    the classifier wants a second term before it believes it ("solar",
    "battery", "turbine"), the classifier is asked again with `context`
    added: the plain-words statement of who is paying and, for NSF, under
    which topic area. The answer is then capped at a one-keyword fit, which
    is what the title holds. A title with no keyword, or one weak one, is
    never rescued by its context.
    """
    if agency in _SKIP_AGENCIES:
        return None
    text = strip_program_prefix(title)
    if _OFF_SUBJECT.search(text) or (order and _BOILERPLATE.search(text)):
        return None
    subject = subject_text(title, buyer=order or agency == DOD)
    c = classify(subject)
    if c["fit"] >= MIN_FIT:
        return c
    context = context or (ORDER_CONTEXT if order else "")
    if not context or len(c["terms"]) != 1 or c["terms"][0] not in _STRONG_TERMS:
        return None
    backed = classify(f"{subject}. {context}")
    if backed["fit"] < MIN_FIT:
        return None
    return {**backed, "fit": min(backed["fit"], ONE_KEYWORD_FIT), "context": context}


_DBA = re.compile(r"^(.{3,}?)[\s,]*\(?\s*(?<![a-z])(?:d/b/a|dba|doing business as)(?![a-z])[\s:]+(.{2,}?)\)?\s*$", re.I)


def split_dba(name: str | None) -> tuple[str, list[str]]:
    """('Padco Industries, LLC DBA DEM Manufacturing') -> ('Padco Industries, LLC', ['DEM Manufacturing']).

    The resolver matches on the name and on each alias, and neither half
    would match anything while the two are glued together.
    """
    s = _squeeze(name)
    m = _DBA.match(s)
    if not m:
        return s, []
    legal, trade = m.group(1).strip(" ,("), m.group(2).strip(" ,)")
    if not legal or not trade:
        return s, []
    return legal, [trade]


_PLACEHOLDER_PERSON = re.compile(r"(?<![a-z])(?:fnu|lnu|tbd|tba)(?![a-z])", re.I)


def person_name(raw: str | None) -> str | None:
    """A PI name fit to store, or None for blanks and placeholders ('FNU Vedant', 'TBD')."""
    s = _squeeze(raw)
    if len(s) < 3 or " " not in s or _PLACEHOLDER_PERSON.search(s) or "@" in s:
        return None
    return s


def fmt_usd(x: float) -> str:
    if x >= 999_500:
        return "$" + f"{x / 1e6:.2f}".rstrip("0").rstrip(".") + "M"
    if x >= 1_000:
        return f"${round(x / 1000):,}K"
    return f"${x:,.0f}"


def _is_acronym(part: str) -> bool:
    if not part.isalpha():
        return any(ch.isdigit() for ch in part)
    if part in _ACRONYMS:
        return True
    if part.lower() in _SHORT_WORDS:
        return False
    if not any(ch in "AEIOUY" for ch in part):
        return len(part) >= 2
    return len(part) <= 3


def readable(s: str) -> str:
    """Sentence-case an ALL-CAPS description; leave mixed-case text alone.

    Presentation only. Known acronyms, short non-words, tokens with digits,
    one-word parentheticals and a short code name before a colon or dash
    stay upper-case. The verbatim text always travels in Signal.text.
    """
    s = _squeeze(s)
    letters = [ch for ch in s if ch.isalpha()]
    if not letters or sum(ch.isupper() for ch in letters) / len(letters) < 0.8:
        return s
    lead = re.match(r"^([A-Z0-9][A-Z0-9/&.-]*(?: [A-Z0-9][A-Z0-9/&.-]*)?)(?=\s*:|\s+[-–]\s)", s)
    lead_end = lead.end() if lead else 0
    out = []
    for m in re.finditer(r"\S+", s):
        tok = m.group(0)
        if m.start() < lead_end or re.fullmatch(r"\([^()\s]+\)[,;:.]?", tok):
            out.append(tok)
            continue
        pieces = re.split(r"([A-Za-z0-9]+)", tok)
        for i in range(1, len(pieces), 2):
            part = pieces[i]
            if part in _ACRONYM_CASE:
                pieces[i] = _ACRONYM_CASE[part]
            elif len(part) == 1 and part.isalpha() and "-" in (pieces[i - 1][-1:], pieces[i + 1][:1]):
                continue  # X-band, Li-S, N-methyl, C-UAS: a lone letter on a hyphen is a designator
            elif not _is_acronym(part):
                pieces[i] = part.lower()
        out.append("".join(pieces))
    text = " ".join(out)
    for i, ch in enumerate(text):
        if ch.isdigit():
            break  # "100% silicon anodes": a sentence that opens on a number has no capital
        if ch.isalpha():
            return text[:i] + ch.upper() + text[i + 1:]
    return text


def clip(s: str, room: int) -> str:
    """Trim to `room` characters on a word boundary, marking the cut."""
    s = _squeeze(s).strip(" .\"“”'")
    if len(s) <= room:
        return s
    cut = s[: max(room - 1, 1)]
    if " " in cut[room // 2:]:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ,;:-–(/&") + "…"


def website_domain(raw: str | None) -> str | None:
    """Company domain from the CSV's free-typed website field, or None.

    The field holds things like 'Https://www.x.com', 'htpps://x.com',
    'www://x.com', 'x.com.', a company name, or an email. Only the scheme is
    repaired; anything with a space or an @ is rejected, not guessed at.
    """
    s = _squeeze(raw).lower().rstrip("./ ")
    if not s or " " in s or "@" in s:
        return None
    s = re.sub(r"^[a-z]{3,6}:/+", "", s)
    d = clean_domain(s)
    if not d or not re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}", d):
        return None
    return d


def email_domain(raw: str | None) -> str | None:
    s = _squeeze(raw).lower()
    if s.count("@") != 1:
        return None
    d = clean_domain(s.split("@")[1])  # also refuses .edu, .gov and .mil
    if not d or d in _FREEMAIL or not re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}", d):
        return None
    return d


def domain_matches_firm(domain: str, company: str | None) -> bool:
    """Does this domain plausibly belong to this firm, judged by the name alone?"""
    label = re.sub(r"[^a-z0-9]", "", "".join(domain.split(".")[:-1]))
    key = firm_key(company)
    name = key.replace(" ", "")
    if not label or not name:
        return False
    if len(label) >= 4 and len(name) >= 4 and (label in name or name in label):
        return True
    toks = key.split()
    if any(len(t) >= 5 and t not in _GENERIC_NAME_WORDS and t in label for t in toks):
        return True
    initials = "".join(t[0] for t in toks)
    return len(initials) >= 3 and label.startswith(initials)


def firm_domain(website: str | None, pi_email: str | None, company: str | None) -> str | None:
    """The firm's own domain, or None when the source does not establish one."""
    site = website_domain(website)
    mail = email_domain(pi_email)
    if site:
        same = bool(mail) and (site == mail or site.endswith("." + mail) or mail.endswith("." + site))
        if same or domain_matches_firm(site, company):
            return site
    if mail and domain_matches_firm(mail, company):
        return mail
    return None


def _place(city: str | None, state: str | None) -> str | None:
    city, state = _squeeze(city), _squeeze(state)
    if city.isupper():
        city = city.title()
    return ", ".join(p for p in (city, state) if p) or None


def _int(s: Any) -> int | None:
    try:
        n = int(float(str(s).strip()))
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def agency_label(agency: str, branch: str = "") -> str:
    if branch in _BRANCH:
        return _BRANCH[branch]
    return _AGENCY.get(agency, agency)


# --------------------------------------------------------------------------
# The bulk CSV
# --------------------------------------------------------------------------

def iter_rows(path: Path | str) -> Iterator[dict]:
    """Stream the bulk CSV one row at a time."""
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        yield from csv.DictReader(fh)


def date_code(row: dict) -> int | None:
    """Sortable YYYYMMDD int for a row.

    About half the file (pre-2015, plus a few dozen recent rows) has no
    award date. Those rows are placed at the start of their fiscal award
    year (1 October of the year before), so they always count as earlier
    than a dated award from that year: the conservative side for a
    first-award claim.
    """
    d = (row.get("Proposal Award Date") or "").strip()
    if len(d) == 10 and d[4] == "-" and d[7] == "-":
        try:
            return int(d[:4]) * 10000 + int(d[5:7]) * 100 + int(d[8:10])
        except ValueError:
            pass
    year = _int(row.get("Award Year"))
    if year:
        return (year - 1) * 10000 + 1001
    return None


def row_keys(row: dict) -> tuple[str, str, str]:
    """(UEI, DUNS, strict name key); blanks stay blank."""
    return ((row.get("UEI") or "").strip().upper(),
            (row.get("Duns") or "").strip(),
            firm_key(row.get("Company")))


def select_candidates(rows: Iterable[dict], since: date, today: date) -> list[dict]:
    """Phase I rows dated inside [since, today] whose title is on thesis."""
    lo, hi = since.isoformat(), today.isoformat()
    out = []
    for i, row in enumerate(rows):
        d = (row.get("Proposal Award Date") or "").strip()
        if len(d) != 10 or not (lo <= d <= hi):
            continue
        if (row.get("Phase") or "").strip() != "Phase I":
            continue
        context = award_context(row)
        fit = on_thesis(row.get("Award Title"), (row.get("Agency") or "").strip(), context=context)
        if not fit:
            continue
        amount = None
        try:
            amount = float(row.get("Award Amount") or 0) or None
        except ValueError:
            pass
        uei, duns, name = row_keys(row)
        out.append({
            "src": "csv", "row_id": i, "row": row, "date": d, "code": date_code(row),
            "fit": fit, "amount": amount, "uei": uei, "duns": duns, "name_key": name,
            "context": context,
        })
    return out


def collect_history(rows: Iterable[dict], ueis: set[str], dunses: set[str],
                    names: set[str]) -> dict:
    """One pass over the file keeping only rows that touch a wanted firm.

    Returns {"uei": {key: [rec]}, "duns": {...}, "name": {...},
             "profile": {uei: newest row facts}, "afwerx_contracts": set}.
    A rec is (row_id, date_code, uei, is_phase2).
    """
    hist: dict[str, Any] = {"uei": {}, "duns": {}, "name": {}, "profile": {},
                            "afwerx_contracts": set()}
    name_cache: dict[str, str] = {}
    for i, row in enumerate(rows):
        contract = row.get("Contract") or ""
        if contract[:2].upper() == "FA":
            ck = contract_key(contract)
            if ck.startswith(AFWERX_PREFIX):
                hist["afwerx_contracts"].add(ck)
        uei = (row.get("UEI") or "").strip().upper()
        duns = (row.get("Duns") or "").strip()
        raw = row.get("Company") or ""
        name = name_cache.get(raw)
        if name is None:
            name = name_cache[raw] = firm_key(raw)
        hit_u, hit_d, hit_n = uei in ueis, duns in dunses, name in names
        if not (hit_u or hit_d or hit_n):
            continue
        rec = (i, date_code(row), uei, (row.get("Phase") or "").strip() == "Phase II")
        if hit_u and uei:
            hist["uei"].setdefault(uei, []).append(rec)
            # Rows arrive newest first, so the first one seen is the freshest profile.
            hist["profile"].setdefault(uei, {
                "website": row.get("Company Website"),
                "pi_email": row.get("PI Email"),
                "employees": _int(row.get("Number Employees")),
                "year": _int(row.get("Award Year")),
            })
        if hit_d and duns:
            hist["duns"].setdefault(duns, []).append(rec)
        if hit_n and name:
            hist["name"].setdefault(name, []).append(rec)
    return hist


def award_standing(hist: dict, uei: str, duns: str, name: str, code: int,
                   row_id: int | None = None) -> dict:
    """Where this award sits in the firm's SBIR/STTR history.

    status "first":   nothing earlier under the UEI, the DUNS or the name
    status "repeat":  earlier awards under the same UEI or DUNS
    status "unclear": the only earlier rows match by name alone
    `prior` counts earlier rows under the same UEI or DUNS plus earlier
    name-only rows that carry no UEI of their own (legacy rows). Name-only
    rows under a different UEI are reported separately and never counted.
    """
    hard: dict[int, tuple] = {}
    for rec in hist["uei"].get(uei, []) if uei else []:
        hard[rec[0]] = rec
    for rec in hist["duns"].get(duns, []) if duns else []:
        hard[rec[0]] = rec
    soft = {rec[0]: rec for rec in (hist["name"].get(name, []) if name else []) if rec[0] not in hard}

    def earlier(recs: Iterable[tuple]) -> list[tuple]:
        return [r for r in recs if r[0] != row_id and r[1] is not None and r[1] < code]

    prior_hard = earlier(hard.values())
    prior_soft = earlier(soft.values())
    legacy = [r for r in prior_soft if not r[2]]
    other_uei = [r for r in prior_soft if r[2]]
    if prior_hard:
        status = "repeat"
    elif prior_soft:
        status = "unclear"
    else:
        status = "first"
    counted = prior_hard + (legacy if status == "repeat" else [])
    lifetime = {r[0] for r in hard.values()} | {r[0] for r in soft.values() if not r[2]}
    if row_id is not None:
        lifetime.add(row_id)
    return {
        "status": status,
        "prior": len(counted),
        "prior_phase2": sum(1 for r in counted if r[3]),
        "name_only_prior": len(prior_soft),
        "name_only_other_uei": len(other_uei),
        "lifetime": len(lifetime),
    }


# --------------------------------------------------------------------------
# www.sbir.gov award pages (evidence link, abstract, cross-check)
# --------------------------------------------------------------------------

_HIT = re.compile(r'<h4[^>]*>\s*<a href="/awards/(\d+)">(.*?)</a>\s*</h4>(.*?)(?=<h4[^>]*>\s*<a href="/awards/\d+">|</tbody>|\Z)', re.S)
_TAG = re.compile(r'<p class="margin-right-1[^"]*">\s*(.*?)\s*</p>', re.S)


def _text(fragment: str | None) -> str:
    return _squeeze(html.unescape(re.sub(r"<[^>]+>", " ", fragment or "")))


def parse_search_results(page: str) -> list[dict]:
    """Hits on a www.sbir.gov/awards?keywords=... page."""
    hits = []
    for m in _HIT.finditer(page):
        block = m.group(3)
        sbc = re.search(r"<b>SBC:</b>(.*?)</span>", block, re.S)
        hits.append({
            "id": m.group(1),
            "title": _text(m.group(2)),
            "company": _text(sbc.group(1)) if sbc else "",
            "tags": [_text(t) for t in _TAG.findall(block)],
        })
    return hits


def pick_hit(hits: list[dict], company: str, title: str) -> dict | None:
    """The one Phase I hit for this firm, or None if that is not unambiguous."""
    want = firm_key(company)
    same = [h for h in hits if "Phase I" in h["tags"] and firm_key(h["company"]) == want]
    if len(same) > 1:
        t = _squeeze(title).lower()
        same = [h for h in same if h["title"].lower() == t]
    return same[0] if len(same) == 1 else None


def parse_award_page(page: str) -> dict:
    """Facts from a www.sbir.gov/awards/<id> page."""

    def field(label: str) -> str:
        m = re.search(rf"<strong>{re.escape(label)}:</strong>(.*?)</p>", page, re.S)
        return _text(m.group(1)) if m else ""

    out: dict[str, Any] = {
        "uei": field("UEI").upper(),
        "contract": field("Contract Number"),
        "tracking": field("Agency Tracking Number"),
    }
    m = re.search(r"Back to Award Search</a></p>\s*<h2>(.*?)</h2>", page, re.S)
    out["title"] = _text(m.group(1)) if m else ""
    m = re.search(r'<a href="/portfolio/(\d+)">\s*<h4[^>]*>(.*?)</h4>', page, re.S)
    out["portfolio_id"] = m.group(1) if m else None
    out["company"] = _text(m.group(2)) if m else ""
    m = re.search(r"Total Award Amount:\s*<span[^>]*>\s*\$([\d,]+(?:\.\d+)?)", page)
    out["amount"] = float(m.group(1).replace(",", "")) if m else None
    m = re.search(r"<strong[^>]*>([^<]+)</strong>\s*<br>\s*Award Start Date", page)
    d = parse_date(_squeeze(m.group(1))) if m else None
    out["start_date"] = d.isoformat() if d else None
    m = re.search(r'Abstract</span>\s*</h2>.*?<p class="measure-none">(.*?)</p>', page, re.S)
    out["abstract"] = _text(m.group(1)) if m else ""
    out["tags"] = [_text(t) for t in _TAG.findall(page)]
    return out


def page_agrees(info: dict, cand: dict) -> str | None:
    """None when the award page matches the CSV row, else what differs."""
    row = cand["row"]
    same_contract = contract_key(info["contract"]) == contract_key(row.get("Contract"))
    same_tracking = contract_key(info["tracking"]) == contract_key(row.get("Agency Tracking Number"))
    if not (same_contract or same_tracking):
        return f"contract {info['contract']!r} vs {row.get('Contract')!r}"
    if info["uei"] and cand["uei"] and info["uei"] != cand["uei"]:
        return f"UEI {info['uei']} vs {cand['uei']}"
    if firm_key(info["company"]) != cand["name_key"]:
        return f"firm {info['company']!r} vs {row.get('Company')!r}"
    if info["amount"] is not None and cand["amount"] is not None and abs(info["amount"] - cand["amount"]) >= 1:
        return f"amount {info['amount']} vs {cand['amount']}"
    if info["start_date"] and info["start_date"] != cand["date"]:
        return f"date {info['start_date']} vs {cand['date']}"
    return None


def find_award_page(cand: dict) -> dict | None:
    """Locate and read the award's page on www.sbir.gov. None if not found."""
    row = cand["row"]
    hit = None
    for term in dict.fromkeys([_squeeze(row.get("Contract")), _squeeze(row.get("Agency Tracking Number"))]):
        if not term:
            continue
        page = http.get(f"{SITE}/awards", params={"keywords": term}, ttl=7 * 86400, timeout=40)
        hit = pick_hit(parse_search_results(page), row.get("Company") or "", row.get("Award Title") or "")
        if hit:
            break
    if not hit:
        return None
    info = parse_award_page(http.get(f"{SITE}/awards/{hit['id']}", ttl=30 * 86400, timeout=40))
    info["url"] = f"{SITE}/awards/{hit['id']}"
    return info


def parse_portfolio_page(page: str) -> dict:
    """SBIR.gov's own summary of a firm: year of first award and award counts."""

    def count(label: str) -> int | None:
        m = re.search(rf"<p[^>]*>\s*([\d,]+)\s*</p>\s*<p[^>]*>\s*{label}\s*</p>", page)
        return int(m.group(1).replace(",", "")) if m else None

    m = re.search(r"Year of first award:\s*<span[^>]*>\s*(\d{4})\s*</span>", page)
    return {"first_year": int(m.group(1)) if m else None,
            "phase1": count("Phase I Awards"), "phase2": count("Phase II Awards")}


def fetch_portfolio(portfolio_id: str) -> dict:
    return parse_portfolio_page(http.get(f"{SITE}/portfolio/{portfolio_id}", ttl=7 * 86400, timeout=40))


# --------------------------------------------------------------------------
# USAspending: the AFWERX purchase-order cohort
# --------------------------------------------------------------------------

_USA_FIELDS = ["Award ID", "Recipient Name", "Recipient UEI", "Start Date", "End Date",
               "Award Amount", "Awarding Sub Agency", "Contract Award Type", "Description",
               "generated_internal_id", "Base Obligation Date", "Recipient Location"]


def usaspending_body(since: date, today: date, page: int) -> dict:
    return {
        "filters": {
            "time_period": [{"start_date": since.isoformat(), "end_date": today.isoformat(),
                             "date_type": "new_awards_only"}],
            "award_type_codes": ["A", "B", "C", "D"],
            "agencies": [{"type": "awarding", "tier": "toptier", "name": "Department of Defense"}],
            "keywords": [AFWERX_PREFIX],
            "award_amounts": [{"lower_bound": 1, "upper_bound": AFWERX_MAX_USD}],
        },
        "fields": _USA_FIELDS, "page": page, "limit": 100,
        "sort": "Base Obligation Date", "order": "desc",
    }


def with_deadline(fn: Callable[[], Any], seconds: float) -> Any:
    """Run fn in a daemon thread and give up on it after `seconds`."""
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["value"] = fn()
        except Exception as e:  # handed back to the caller below
            box["error"] = e

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(max(seconds, 0.0))
    if t.is_alive():
        raise TimeoutError(f"no answer within {seconds:.0f}s")
    if "error" in box:
        raise box["error"]
    return box["value"]


class Budget:
    """A hard allowance of wall-clock seconds for one slow host.

    Time is charged only while a call is in flight, so cache hits are free
    and a warm run spends almost nothing. A call that outlives what is left
    is abandoned (its thread is a daemon) and counts as a failure.
    """

    def __init__(self, seconds: float):
        self.total = seconds
        self.spent = 0.0

    @property
    def left(self) -> float:
        return self.total - self.spent

    def call(self, fn: Callable[[], Any], cap: float | None = None) -> Any:
        allow = self.left if cap is None else min(self.left, cap)
        if allow <= 0.5:
            raise TimeoutError("time budget used up")
        t0 = time.monotonic()
        try:
            return with_deadline(fn, allow)
        finally:
            self.spent += time.monotonic() - t0


def parse_afwerx_row(r: dict, since: date, today: date) -> dict | None:
    """A candidate from one USAspending result row, or None if it does not qualify."""
    award_id = _squeeze(r.get("Award ID")).upper()
    if not award_id.startswith(AFWERX_PREFIX):
        return None  # the keyword also matches other offices' text mentions of FA8649
    try:
        amount = float(r.get("Award Amount") or 0)
    except (TypeError, ValueError):
        return None
    if not 0 < amount <= AFWERX_MAX_USD:
        return None
    # Start Date is period of performance and can be in the future: use the obligation date.
    d = parse_date(r.get("Base Obligation Date"))
    if d is None or not (since <= d <= today):
        return None
    internal = _squeeze(r.get("generated_internal_id"))
    name = _squeeze(r.get("Recipient Name"))
    desc = _squeeze(r.get("Description"))
    if not internal or not name or not desc:
        return None
    fit = on_thesis(desc, order=True)
    if not fit:
        return None
    loc = r.get("Recipient Location") or {}
    return {
        "src": "afwerx", "row_id": None, "date": d.isoformat(),
        "code": d.year * 10000 + d.month * 100 + d.day, "fit": fit, "amount": amount,
        "uei": _squeeze(r.get("Recipient UEI")).upper(), "duns": "", "name_key": firm_key(name),
        "award_id": award_id, "name": name, "description": desc, "internal_id": internal,
        "contract_type": _squeeze(r.get("Contract Award Type")),
        "location": _place(loc.get("city_name"), loc.get("state_name") or loc.get("state_code")),
        "end_date": _squeeze(r.get("End Date")) or None,
    }


def fetch_afwerx(ctx: Context, since: date, today: date, budget: Budget) -> list[dict]:
    """Thesis-matching FA8649 small orders, newest first, inside the time budget."""
    out: list[dict] = []
    page, retried = 1, False
    while True:
        if page > AFWERX_MAX_PAGES:
            ctx.warn(f"USAspending lists more than {AFWERX_MAX_PAGES * 100} FA8649 orders since {since}; "
                     "the oldest were not read")
            break
        if budget.left < 3:
            ctx.warn(f"USAspending: {budget.total:.0f}s budget used up before page {page}; "
                     "AFWERX cohort is partial")
            break
        body = usaspending_body(since, today, page)
        try:
            data = budget.call(lambda b=body: http.post_json(
                USASPENDING_SEARCH, b, ttl=12 * 3600, timeout=max(budget.left, 5), retries=0))
        except Exception as e:
            # The source card measured that an immediate retry of a 502/504 succeeds.
            if not retried and not isinstance(e, TimeoutError) and budget.left > 10:
                retried = True
                continue
            ctx.warn(f"USAspending page {page} failed ({type(e).__name__}: {str(e)[:80]}); "
                     f"AFWERX cohort {'skipped' if page == 1 else 'is partial'}")
            break
        retried = False
        if not isinstance(data, dict):
            ctx.warn(f"USAspending page {page} returned no JSON object; AFWERX cohort "
                     f"{'skipped' if page == 1 else 'is partial'}")
            break
        for r in data.get("results") or []:
            try:
                cand = parse_afwerx_row(r, since, today)
            except Exception as e:
                ctx.warn(f"USAspending row skipped: {e}")
                continue
            if cand:
                out.append(cand)
        if not (data.get("page_metadata") or {}).get("hasNext"):
            break
        page += 1
    return out


def fetch_award_detail(cand: dict, budget: Budget) -> dict | None:
    """The award's own USAspending record, or None when it has no page (HTTP 404).

    The search index lists orders a few days before their award page
    exists: on 2026-10-01 every order dated 2026-07-01 was in the search
    results and none had a page. Those must not be linked to.
    """
    url = USASPENDING_AWARD_API + cand["internal_id"] + "/"
    try:
        return budget.call(lambda: http.get_json(url, ttl=3 * 86400, timeout=25, retries=0), cap=25)
    except http.HttpError as e:
        if e.status == 404:
            return None
        raise


def detail_agrees(detail: dict, cand: dict) -> str | None:
    """None when the award record matches the search row, else what differs."""
    office = ((detail.get("awarding_agency") or {}).get("office_agency_name") or "")
    if AFWERX_PREFIX not in office or "SBIR" not in office.upper():
        return f"awarding office {office!r} is not the FA8649 SBIR/STTR office"
    name = ((detail.get("recipient") or {}).get("recipient_name") or "")
    if firm_key(name) != cand["name_key"]:
        return f"recipient {name!r} vs {cand['name']!r}"
    try:
        amount = float(detail.get("total_obligation"))
    except (TypeError, ValueError):
        return "no obligation amount"
    if abs(amount - cand["amount"]) >= 1:
        return f"amount {amount} vs {cand['amount']}"
    if (detail.get("date_signed") or "")[:10] != cand["date"]:
        return f"date signed {detail.get('date_signed')} vs {cand['date']}"
    return None


# --------------------------------------------------------------------------
# USAspending: the firm's own federal record (second source for "first")
# --------------------------------------------------------------------------

_USA_HISTORY_FIELDS = ["Award ID", "Recipient Name", "Recipient UEI", "Award Amount",
                       "Description", "Base Obligation Date"]
CONTRACT_CODES = ["A", "B", "C", "D"]
GRANT_CODES = ["02", "03", "04", "05"]


def history_body(uei: str, today: date, codes: list[str]) -> dict:
    """Every prime contract (or grant) to one UEI, oldest first.

    The end date is the end of the year, not today, so the request and its
    cache key stay the same from one day to the next.
    """
    return {
        "filters": {
            "time_period": [{"start_date": "2007-10-01", "end_date": f"{today.year}-12-31"}],
            "award_type_codes": list(codes),
            "recipient_search_text": [uei],
        },
        "fields": _USA_HISTORY_FIELDS, "page": 1, "limit": 100,
        "sort": "Base Obligation Date", "order": "asc",
    }


def earlier_awards(data: Any, uei: str, own: set[str], before: str, name_key: str = "") -> dict:
    """Awards to this UEI dated before `before`, from one history answer.

    `own` holds the contract keys of the award being reported, which is never
    its own predecessor. The search also matches on name and parent, so rows
    under any other UEI are ignored; a row with no UEI at all counts only if
    its recipient name is the firm's. An undated row counts as earlier: the
    safe side for a "first" claim. `complete` is False when the answer was
    cut at 100 rows before reaching `before`; the count is then a floor.
    """
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise ValueError("USAspending history answer has no result list")
    rows, reached = [], False
    for r in data["results"]:
        if not isinstance(r, dict):
            continue
        day = _squeeze(r.get("Base Obligation Date"))[:10]
        if day and day >= before:
            reached = True
            continue
        row_uei = _squeeze(r.get("Recipient UEI")).upper()
        if row_uei != uei and (row_uei or not name_key or firm_key(r.get("Recipient Name")) != name_key):
            continue
        if contract_key(r.get("Award ID")) in own:
            continue
        rows.append(r)
    more = bool((data.get("page_metadata") or {}).get("hasNext"))
    return {"n": len(rows), "rows": rows, "complete": reached or not more}


def fetch_history(uei: str, today: date, codes: list[str], budget: Budget) -> Any:
    body = history_body(uei, today, codes)
    return budget.call(lambda: http.post_json(USASPENDING_SEARCH, body, ttl=3 * 86400, timeout=25,
                                              retries=0), cap=25)


def own_keys(cand: dict) -> set[str]:
    """Contract keys under which USAspending may list the candidate's own award."""
    if cand["src"] == "afwerx":
        keys = {contract_key(cand["award_id"])}
    else:
        keys = {contract_key(cand["row"].get("Contract")),
                contract_key(cand["row"].get("Agency Tracking Number"))}
    return keys - {""}


def federal_standing(cand: dict, standing: dict, afwerx_on_file: set[str], today: date,
                     budget: Budget) -> dict:
    """The SBIR.gov standing, corrected by the firm's USAspending record.

    Adds to `standing`:
      federal_checked    True when USAspending answered
      federal_contracts  earlier prime contracts under the UEI (None if not asked)
      federal_grants     earlier grants under the UEI (asked only when there are no contracts)
      federal_complete   False when the contract list was cut at 100 rows
    and moves the status:
      "first"   stays only with no earlier contract and no earlier grant
      "federal" no SBIR.gov history, but earlier federal contracts or grants
      "repeat"  unchanged; earlier FA8649 awards missing from SBIR.gov are added to `prior`
      "unclear" a would-be "first" that USAspending could not confirm
    Never raises: a failed lookup only withdraws the claim.
    """
    out = {**standing, "federal_checked": False, "federal_contracts": None,
           "federal_grants": None, "federal_complete": True}
    uei = cand["uei"]
    try:
        if not uei:
            raise ValueError("no UEI to look up")
        own = own_keys(cand)
        contracts = earlier_awards(fetch_history(uei, today, CONTRACT_CODES, budget), uei, own,
                                   cand["date"], cand["name_key"])
        grants = None
        if standing["status"] != "repeat" and contracts["n"] == 0 and contracts["complete"]:
            grants = earlier_awards(fetch_history(uei, today, GRANT_CODES, budget), uei, own,
                                    cand["date"], cand["name_key"])
    except Exception as e:
        out["federal_error"] = f"{type(e).__name__}: {str(e)[:80]}"
        if standing["status"] == "first":
            out["status"] = "unclear"
        return out

    out.update(federal_checked=True, federal_contracts=contracts["n"],
               federal_complete=contracts["complete"],
               federal_grants=grants["n"] if grants else None)
    if standing["status"] == "repeat":
        missing = {contract_key(r.get("Award ID")) for r in contracts["rows"]}
        missing = {k for k in missing if k.startswith(AFWERX_PREFIX) and k not in afwerx_on_file}
        out["prior"] = standing["prior"] + len(missing)
        out["prior_not_on_sbir_gov"] = len(missing)
    elif contracts["n"] or not contracts["complete"] or (grants and (grants["n"] or not grants["complete"])):
        out["status"] = "federal"
    return out


def is_established(standing: dict) -> bool:
    """A firm with dozens of awards behind it: real, on thesis, and not early."""
    if standing["prior"] >= ESTABLISHED:
        return True
    if (standing.get("federal_contracts") or 0) >= ESTABLISHED:
        return True
    return standing.get("federal_complete") is False


# --------------------------------------------------------------------------
# Strength and titles
# --------------------------------------------------------------------------

def strength_for(status: str, prior: int, fit: float, employees: int | None, *,
                 fast_track: bool = False, afwerx: bool = False) -> float:
    """0..1 per the scale in docs/COLLECTORS.md.

    A first federal award on record is notable (0.6 to 0.8) and reaches 0.85
    and up only for a team of ten or fewer whose title hits the thesis
    several times. A firm with a few earlier awards is a solid step (0.35 to
    0.55); `prior` is its earlier SBIR/STTR awards, or its earlier federal
    contracts when the status is "federal". A firm with dozens is doing what
    it always does (0.15 to 0.3).
    """
    if status == "first":
        s = 0.70
        if employees is not None:
            if employees <= 10:
                s += 0.08
            elif employees <= 50:
                s += 0.02
            elif employees <= 250:
                s -= 0.10
            else:
                s -= 0.22
    elif status == "unclear":
        s = 0.42
    else:
        s = 0.50 if prior <= 2 else 0.40 if prior <= 9 else 0.28 if prior <= 24 else 0.18
        if employees is not None and employees > 250:
            s -= 0.03
    # One thesis keyword in a title is fit 0.465. Reward titles that hit
    # several, and mark down the ones that only scraped past on context words.
    gap = fit - ONE_KEYWORD_FIT
    s += max(-0.10, min(0.10, gap * (0.33 if gap >= 0 else 0.6)))
    if fast_track:
        s += 0.04  # NSF Fast-Track commits Phase I and II money at once
    if afwerx:
        s -= 0.05  # open-topic $75K orders are the lowest bar in the program
    return round(max(0.15, min(0.97, s)), 2)


def prior_count(standing: dict) -> int:
    """The count behind the firm's standing: SBIR/STTR awards, else federal contracts or grants."""
    if standing["status"] == "federal":
        return standing.get("federal_contracts") or standing.get("federal_grants") or 0
    return standing["prior"]


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def history_clause(standing: dict) -> str:
    """What a title says about the firm's past, or '' when it claims nothing."""
    if standing["status"] == "repeat":
        return f"{_count(standing['prior'], 'prior award')} on record"
    if standing["status"] == "federal":
        if standing.get("federal_contracts"):
            return f"{_count(standing['federal_contracts'], 'earlier federal contract')} on record"
        if standing.get("federal_grants"):
            return f"{_count(standing['federal_grants'], 'earlier federal grant')} on record"
    return ""


def build_title(lead: str, topic: str) -> str:
    """'<lead>: <topic>', with the topic clipped so the whole stays under 110 characters."""
    return f"{lead}: {clip(topic, MAX_TITLE - len(lead) - 2)}"


def _lead(what: str, standing: dict) -> str:
    if standing["status"] == "first":
        return f"First federal award on record, {what}"
    clause = history_clause(standing)
    return f"{what}, {clause}" if clause else what


def csv_title(cand: dict, standing: dict) -> str:
    row = cand["row"]
    program = (row.get("Program") or "SBIR").strip() or "SBIR"
    phase = "Fast-Track" if _FAST_TRACK.match(row.get("Award Title") or "") else "Phase I"
    agency = agency_label(row.get("Agency") or "", row.get("Branch") or "")
    amount = f"{fmt_usd(cand['amount'])} " if cand["amount"] else ""
    topic = readable(strip_program_prefix(row.get("Award Title")))
    return build_title(_lead(f"{amount}{agency} {program} {phase}", standing), topic)


def afwerx_title(cand: dict, standing: dict) -> str:
    # The award record names the office ("USAF SBIR STTR CNTRCTNG") and no phase.
    order = f"{fmt_usd(cand['amount'])} Air Force SBIR/STTR order"
    return build_title(_lead(order, standing), readable(cand["description"]))


# --------------------------------------------------------------------------
# Signals
# --------------------------------------------------------------------------

_NSF_AWARD_ID = re.compile(r"\d{7}")


def nsf_award_key(row: dict) -> str | None:
    """'nsf:2528317' for an NSF row: the award id NSF itself uses, else None."""
    if (row.get("Agency") or "").strip() != NSF:
        return None
    for col in ("Agency Tracking Number", "Contract"):
        v = _squeeze(row.get(col))
        if _NSF_AWARD_ID.fullmatch(v):
            return f"nsf:{v}"
    return None


def public_date(signed: str, today: date) -> str:
    """The first day a DoD contract action could be seen on USAspending.

    DoD actions are withheld for DOD_EMBARGO_DAYS after signing. A record
    that is visible today was public by today, whatever the arithmetic says.
    """
    return min(date.fromisoformat(signed) + timedelta(days=DOD_EMBARGO_DAYS), today).isoformat()


def _history_metrics(standing: dict) -> dict:
    first = standing["status"] == "first"
    m: dict[str, Any] = {
        "sbir_prior_awards": standing["prior"],
        "sbir_prior_phase2": standing["prior_phase2"],
        "first_award_on_record": int(first),
        "history_status": standing["status"],
    }
    if standing.get("name_only_prior"):
        m["sbir_name_only_prior_awards"] = standing["name_only_prior"]
    if standing.get("name_only_other_uei"):
        m["sbir_same_name_other_uei"] = standing["name_only_other_uei"]
    if standing.get("prior_not_on_sbir_gov"):
        m["sbir_prior_awards_not_on_sbir_gov"] = standing["prior_not_on_sbir_gov"]
    if standing.get("federal_checked"):
        m["federal_prior_contracts"] = standing["federal_contracts"]
        if standing.get("federal_grants") is not None:
            m["federal_prior_grants"] = standing["federal_grants"]
    elif "federal_checked" in standing:
        m["federal_history"] = "not checked: " + (standing.get("federal_error") or "unavailable")
    return m


def csv_signal(cand: dict, standing: dict, page: dict, profile: dict | None = None) -> Signal:
    """One SBIR.gov award. `page` is its parsed, cross-checked www.sbir.gov page."""
    row = cand["row"]
    name, aliases = split_dba(row.get("Company"))
    program = (row.get("Program") or "").strip() or "SBIR"
    fast_track = bool(_FAST_TRACK.match(row.get("Award Title") or ""))
    phase = "Fast-Track" if fast_track else "Phase I"
    agency = agency_label(row.get("Agency") or "", row.get("Branch") or "")
    award_title = _squeeze(row.get("Award Title"))
    employees = _int(row.get("Number Employees"))

    metrics: dict[str, Any] = {
        **_history_metrics(standing),
        "sbir_lifetime_awards": standing["lifetime"],
        "title_fit": cand["fit"]["fit"],
        "agency": _squeeze(row.get("Agency")),
        "program": program,
        "phase": phase,
        "contract": _squeeze(row.get("Contract")),
    }
    if cand["amount"]:
        metrics["amount_usd"] = cand["amount"]
    if employees:
        metrics["team_size"] = employees
    if cand["uei"]:
        metrics["uei"] = cand["uei"]   # upper-case: the resolver joins signals on it
    for key, col in (("branch", "Branch"), ("topic_code", "Topic Code"),
                     ("research_institution", "RI Name"), ("contract_end", "Contract End Date")):
        v = _squeeze(row.get(col))
        if v:
            metrics[key] = v
    area = nsf_topic_area(row)
    if area:
        metrics["nsf_topic_area"] = area
    award_key = nsf_award_key(row)
    if award_key:
        metrics["award_key"] = award_key
    if profile:
        for key, field in (("sbir_profile_first_year", "first_year"),
                           ("sbir_profile_phase1_awards", "phase1"),
                           ("sbir_profile_phase2_awards", "phase2")):
            if profile.get(field) is not None:
                metrics[key] = profile[field]

    people = []
    pi = person_name(row.get("PI Name"))
    if pi:
        # On an STTR the PI may sit at the partner institution, so no employer is asserted.
        people.append(Person(name=pi, role=f"Principal investigator, {agency} {program} {phase}",
                             affiliations=[name] if program == "SBIR" else []))

    links = {}
    if page.get("portfolio_id"):
        links["sbir_profile"] = f"{SITE}/portfolio/{page['portfolio_id']}"

    return Signal(
        source=SLUG, family=FAMILY, kind=KIND_CSV,
        entity=EntityHint(
            name=name, aliases=aliases,
            domain=firm_domain(row.get("Company Website"), row.get("PI Email"), row.get("Company")),
            description=f"{agency} {program} {phase} project: {strip_program_prefix(award_title)}",
            location=_place(row.get("City"), row.get("State")),
            links=links,
        ),
        title=csv_title(cand, standing),
        occurred_at=cand["date"],
        url=page["url"],
        value=cand["amount"], unit="USD" if cand["amount"] else None,
        strength=strength_for(standing["status"], prior_count(standing), cand["fit"]["fit"], employees,
                              fast_track=fast_track),
        metrics=metrics,
        people=people,
        # The funder and topic sentence first: it is what on_thesis showed the classifier.
        text="\n".join(p for p in (cand.get("context") or award_context(row), award_title,
                                   page.get("abstract") or "") if p),
    )


def afwerx_signal(cand: dict, standing: dict, profile: dict | None, today: date,
                  detail: dict) -> Signal:
    """One FA8649 order. `detail` is its cross-checked USAspending award record."""
    office = _squeeze((detail.get("awarding_agency") or {}).get("office_agency_name"))
    name, aliases = split_dba(cand["name"])
    metrics: dict[str, Any] = {
        "amount_usd": cand["amount"],
        **_history_metrics(standing),
        "title_fit": cand["fit"]["fit"],
        "agency": "Department of Defense",
        "branch": "Air Force",
        "office": office,
        "phase": "not stated; Phase I size",
        "contract": cand["award_id"],
        "contract_type": cand["contract_type"],
    }
    if cand["uei"]:
        metrics["uei"] = cand["uei"]
    if cand["end_date"]:
        metrics["contract_end"] = cand["end_date"]
    # Nobody outside the government could see the order before the embargo ran out.
    metrics["public_at"] = public_date(cand["date"], today)
    domain = None
    employees = None
    if profile:
        # Same UEI in the SBIR.gov file: borrow the website it lists there, and
        # the headcount only if that row is recent enough to still mean something.
        domain = firm_domain(profile.get("website"), profile.get("pi_email"), cand["name"])
        if profile.get("employees") and (profile.get("year") or 0) >= today.year - 2:
            employees = profile["employees"]
            metrics["team_size"] = employees
    return Signal(
        source=SLUG, family=FAMILY, kind=KIND_ORDER,
        entity=EntityHint(
            name=name, aliases=aliases, domain=domain, location=cand["location"],
            description=f"Air Force SBIR/STTR office order: {readable(cand['description'])}",
        ),
        title=afwerx_title(cand, standing),
        occurred_at=cand["date"],
        url=USASPENDING_AWARD + cand["internal_id"],
        value=cand["amount"], unit="USD",
        strength=strength_for(standing["status"], prior_count(standing), cand["fit"]["fit"], employees,
                              afwerx=True),
        metrics=metrics,
        # The buyer is named once, in plain words, so the classifier weighs the
        # product and not who bought it.
        text=f"{cand['description']}\n{ORDER_CONTEXT} {cand['award_id']}, "
             f"{cand['contract_type'].lower() or 'contract'}. Awarding office: {office}.",
    )


# --------------------------------------------------------------------------
# collect
# --------------------------------------------------------------------------

class _Skip(Exception):
    """An award that cannot be emitted; `reason` keys the end-of-run tally.

    `by_design` marks a filter doing its job (logged) as opposed to a lookup
    that failed (warned).
    """

    def __init__(self, reason: str, by_design: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.by_design = by_design


def _skip_established() -> _Skip:
    return _Skip(f"firm has {ESTABLISHED} or more prior awards or federal contracts, so it is no "
                 "early company", by_design=True)


def _confirm(ctx: Context, cand: dict, standing: dict, hist: dict, usa: Budget, today: date,
             state: dict) -> dict:
    """Check the firm's USAspending record and drop established contractors."""
    standing = federal_standing(cand, standing, hist["afwerx_contracts"], today, usa)
    if not standing["federal_checked"]:
        state["unchecked"] += 1
    if is_established(standing):
        raise _skip_established()
    return standing


def _csv_step(ctx: Context, cand: dict, standing: dict, state: dict, hist: dict, usa: Budget,
              today: date) -> Signal:
    """Find, cross-check and emit one SBIR.gov award, or raise _Skip."""
    company = _squeeze(cand["row"].get("Company"))
    if state["site_down"]:
        raise _Skip("www.sbir.gov unreachable, so no evidence page")
    if state["site_started"] is None:
        state["site_started"] = time.monotonic()
    if time.monotonic() - state["site_started"] > SITE_BUDGET_S:
        raise _Skip(f"www.sbir.gov lookup budget ({SITE_BUDGET_S:.0f}s) ran out; "
                    "fetched pages are cached, so the next run picks these up")
    try:
        page = find_award_page(cand)
    except Exception as e:
        state["site_failures"] += 1
        if state["site_failures"] >= 5:
            state["site_down"] = True
            ctx.warn(f"www.sbir.gov unreachable ({type(e).__name__}: {str(e)[:80]}); its award page "
                     "is the evidence link, so the remaining SBIR.gov awards are skipped this run")
        raise _Skip("www.sbir.gov lookup failed") from e
    state["site_failures"] = 0
    if not page:
        raise _Skip("no unambiguous award page on www.sbir.gov")
    problem = page_agrees(page, cand)
    if problem:
        ctx.warn(f"dropped {company!r}: award page disagrees with the bulk file ({problem})")
        raise _Skip("award page disagrees with the bulk file")

    profile = None
    if standing["status"] == "first" and page.get("portfolio_id"):
        # Second opinion on the headline claim: SBIR.gov's own firm summary.
        try:
            profile = fetch_portfolio(page["portfolio_id"])
        except Exception:
            profile = None  # USAspending still has to agree below
        if profile and profile["first_year"] and profile["first_year"] < int(cand["date"][:4]):
            ctx.warn(f"{company!r}: SBIR.gov profile dates its first award to {profile['first_year']}, "
                     "before this one, so it is not called a first award")
            standing = {**standing, "status": "unclear"}
    if standing["status"] == "first":
        # Third opinion, and the one that catches what SBIR.gov has not listed yet.
        standing = _confirm(ctx, cand, standing, hist, usa, today, state)
    return csv_signal(cand, standing, page, profile)


def _afwerx_step(ctx: Context, cand: dict, standing: dict, hist: dict, usa: Budget, today: date,
                 state: dict) -> Signal:
    """Cross-check and emit one FA8649 order, or raise _Skip."""
    try:
        detail = fetch_award_detail(cand, usa)
    except TimeoutError as e:
        if usa.left <= 0.5:
            raise _Skip(f"USAspending {usa.total:.0f}s budget ran out before the award record was "
                        "checked; answers are cached, so the next run picks these up") from e
        raise _Skip("USAspending award record timed out") from e
    except Exception as e:
        raise _Skip("USAspending award record did not load") from e
    if detail is None:
        raise _Skip("USAspending search lists the order but its award page does not exist yet (404)")
    problem = detail_agrees(detail, cand)
    if problem:
        ctx.warn(f"dropped {cand['name']!r} {cand['award_id']}: {problem}")
        raise _Skip("USAspending award record disagrees with the search row")
    # SBIR.gov lists no FA8649 award after 2025-09, so the firm's USAspending
    # record is the only place a recent earlier award can show.
    standing = _confirm(ctx, cand, standing, hist, usa, today, state)
    return afwerx_signal(cand, standing, hist["profile"].get(cand["uei"]), today, detail)


def missing_columns(path: Path | str) -> list[str]:
    """Columns the collector reads that the bulk file's header no longer has."""
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        header = next(csv.reader(fh), [])
    return [c for c in _CSV_COLUMNS if c not in header]


def collect(ctx: Context) -> Iterable[Signal]:
    today = ctx.today
    since = ctx.since

    dest = Path(config.DOWNLOADS_DIR) / CSV_FILE
    try:
        path = http.download(CSV_URL, dest)
    except Exception as e:
        if not (dest.exists() and dest.stat().st_size > 0):
            raise
        ctx.warn(f"SBIR.gov bulk file could not be refreshed ({type(e).__name__}: {str(e)[:80]}); "
                 f"using the copy saved {date.fromtimestamp(dest.stat().st_mtime)}")
        path = dest
    gone = missing_columns(path)
    if gone:
        ctx.warn(f"SBIR.gov bulk file has no column {', '.join(gone)}; its layout changed and "
                 "awards may be missed")
    cands = select_candidates(iter_rows(path), since, today)
    ctx.log(f"sbir_awards: {len(cands)} on-thesis Phase I rows since {since} in the bulk file")

    usa = Budget(AFWERX_BUDGET_S)
    try:
        afwerx = fetch_afwerx(ctx, since, today, usa)
    except Exception as e:  # never let the optional half sink the collector
        ctx.warn(f"USAspending AFWERX step failed: {type(e).__name__}: {e}")
        afwerx = []
    ctx.log(f"sbir_awards: {len(afwerx)} on-thesis FA8649 small orders from USAspending "
            f"({usa.spent:.0f}s of {usa.total:.0f}s used)")

    everyone = cands + afwerx
    hist = collect_history(
        iter_rows(path),
        {c["uei"] for c in everyone if c["uei"]},
        {c["duns"] for c in everyone if c["duns"]},
        {c["name_key"] for c in everyone if c["name_key"]},
    )

    # An FA8649 order already in the SBIR.gov file is that file's row to report
    # (it knows the phase).
    afwerx = [c for c in afwerx if contract_key(c["award_id"]) not in hist["afwerx_contracts"]]

    ordered = sorted(cands + afwerx, key=lambda c: c["date"], reverse=True)
    seen_entities: set[str] = set()
    state = {"site_started": None, "site_failures": 0, "site_down": False, "unchecked": 0}
    skipped: dict[str, list] = {}

    for cand in ordered:
        ident = cand["uei"] or cand["name_key"]
        if ctx.limit and ident not in seen_entities and len(seen_entities) >= ctx.limit:
            break
        try:
            standing = award_standing(hist, cand["uei"], cand["duns"], cand["name_key"],
                                      cand["code"], cand["row_id"])
            if is_established(standing):
                raise _skip_established()  # before any lookup is spent on it
            if cand["src"] == "afwerx":
                sig = _afwerx_step(ctx, cand, standing, hist, usa, today, state)
            else:
                sig = _csv_step(ctx, cand, standing, state, hist, usa, today)
        except _Skip as sk:
            tally = skipped.setdefault(sk.reason, [0, sk.by_design])
            tally[0] += 1
            continue
        except Exception as e:
            ctx.warn(f"award skipped ({type(e).__name__}: {e})")
            continue
        seen_entities.add(ident)
        yield sig

    for reason, (n, by_design) in sorted(skipped.items()):
        (ctx.log if by_design else ctx.warn)(f"{n} award{'s' if n != 1 else ''} not emitted: {reason}")
    if state["unchecked"]:
        n = state["unchecked"]
        ctx.warn(f"{n} award{'s' if n != 1 else ''} emitted without a USAspending history check "
                 "(budget or outage), so no first-award claim is made for them this run")
