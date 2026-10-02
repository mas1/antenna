"""USPTO trademark applications whose goods and services text is on thesis.

A hardware startup files its brand as a trademark around the time it names
its first product, usually on an "intent to use" basis (section 1b), which
means the product has not shipped yet. The application is public the next
day through the search API behind tmsearch.uspto.gov (no key), with the
legal owner, its state of incorporation, the filing basis and the goods and
services wording, often months before a launch or a funding announcement.

One signal is emitted per US corporate owner, covering every on-thesis
application that owner filed inside the lookback window. The evidence link
is the public TSDR status page for the lead application's serial number.

What the title may claim, and what it may not:
- The goods named after "for" come from the head of an item the applicant
  wrote, never from an exclusion clause ("none of the foregoing relating to
  robotics") and never from what the item is merely aimed at or used with.
- "Its first N applications" is said only when the owner has no other
  application on file; otherwise the batch is "N of its first M".
- Applications are counted in the title; brands are counted for strength.

The thesis gate is antenna.thesis.classify on the goods wording, 0.3 or
more. The classifier wants two terms unless the one it finds is unmistakable
("Radar apparatus", "Space vehicles" and "Vertical take-off and landing (VTOL)
aircraft" each pass on the applicant's wording alone). When the wording gives
it one term that is not, and the applicant lists the thing itself as its
goods ("Self-driving cars" in class 012), the collector's reading of the item
("an application for autonomous vehicles", the words the title uses, also
written into Signal.text) is put beside the wording and the two are
classified together. That reading never counts alone: wording the classifier
finds nothing in is dropped, and a reading that repeats the wording's one
term ("machine tools" for "Machine tools, namely, rotary dies") adds nothing.

Radar is named as the goods only when the item is a radar. A gauge, an app or
a sports gadget that measures by radar (tank level sensors, weather radar
imagery, speed guns, golf monitors) is off thesis, like a radar detector.

Not emitted: contractors and installers (IC 037), surveying, insurance and
training services, US arms of other companies, holding vehicles, filings
that rest only on a foreign application, and established companies.
"""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable

from .. import http
from ..models import EntityHint, Signal
from ..thesis import classify
from .base import Context, iso, parse_date, squash, strip_legal

SLUG = "uspto_trademarks"
FAMILY = "regulatory"
STAGE = "discover"
DESCRIPTION = "New USPTO trademark applications on thesis by young US companies, intent-to-use first"

# Undocumented API behind the public Trademark Search site. The version is in
# the path, so expect this to move.
SEARCH_URL = "https://tmsearch.uspto.gov/prod-stage-v1-0-0/tmsearch"
TSDR_URL = (
    "https://tsdr.uspto.gov/#caseNumber={serial}&caseSearchType=US_APPLICATION"
    "&caseType=DEFAULT&searchType=statusSearch"
)

SEARCH_TTL = 6 * 3600  # new applications load once a day
HISTORY_TTL = 24 * 3600

# CloudFront answers 429 with Retry-After: 300 after a burst (about 170
# requests in 70 seconds did it). A full run makes about 15 requests; they
# are spaced out here (this host is left out of http.HOST_INTERVAL on
# purpose) and the run stops cleanly on the first 429.
PAUSE_SECONDS = 1.5
PAGE_SIZE = 250
MAX_PAGES = 16
HISTORY_BATCH = 40

# An owner with this many marks on file under its legal name, or whose first
# mark is this old, is an established company and is dropped.
INCUMBENT_MARKS = 25
INCUMBENT_YEARS = 8
# A use-based (1a) mark first used this long before filing is an old product.
STALE_USE_YEARS = 3
# Thesis items as a share of all items in the application, and of the items
# in the class where they are most concentrated.
MIN_SHARE = 0.1
MIN_CLASS_SHARE = 0.3
# When every thesis item is a service (no goods class at all), the thesis
# must be a real part of what the applicant does, not one field in a list.
MIN_SERVICE_SHARE = 0.25

MAX_TITLE = 108
EXCERPT_CHARS = 520

_SOURCE_FIELDS = [
    "id", "wordmark", "ownerName", "ownerCity", "ownerStateCountryAddress", "filedDate",
    "goodsAndServices", "internationalClass", "currentBasis", "attorney", "firstUseAnyDate",
    "alive", "statusDescription",
]

# What is sent to the goodsAndServices field (Elasticsearch query_string
# syntax). Deliberately wide: precision comes from the item-level matching
# below, not from the query.
SEARCH_TERMS = [
    # autonomy
    '"unmanned aerial vehicles"', '"unmanned aerial systems"', '"unmanned aircraft"',
    '"military drones"', '"autonomous underwater vehicles"', '"unmanned underwater vehicles"',
    '"unmanned ground vehicles"', '"unmanned surface vessels"', '"autonomous vehicles"',
    '"vertical take-off"', "evtol", "lidar",
    "((drone OR drones) AND (interceptor OR counter OR jamming OR autonomous OR military OR defense))",
    "((autonomous OR unmanned) AND (vessels OR boats OR submersibles))",
    # robotics
    '"humanoid robots"', '"industrial robots"', '"robotic arms"', "exoskeleton", "exoskeletons",
    '"legged robots"', "quadruped", '"mobile robots"', '"robotic manipulators"',
    # defense
    "munitions", "missiles", '"electronic warfare"', "radar", '"directed energy"', "hypersonic",
    '"counter-drone"', '"counter-unmanned"',
    # energy
    "(nuclear AND (reactor OR reactors OR power OR fuel OR fission OR energy))",
    "(fusion AND (reactor OR reactors OR energy OR power))", "microreactors",
    '"small modular reactors"', '"battery cells"', '"solid state batteries"', '"energy storage"',
    "geothermal", '"power plants"', "electrolyzers", '"grid-scale"',
    # manufacturing
    '"additive manufacturing"', '"machine tools"', '"rare earth"', '"critical minerals"',
    '"metal casting"', '"factory automation"', '"industrial automation"',
    # semiconductors
    '"semiconductor manufacturing"', '"semiconductor chips"', '"semiconductor wafers"',
    "(semiconductor AND (lithography OR wafers OR chips))", "photonic", "photonics",
    # space
    "satellites", "spacecraft", '"launch vehicles"', '"rocket engines"', '"space vehicles"',
    "rockets",
]

# Owner entity types that are a company. Individuals, non-profits, trusts,
# universities and government bodies are dropped.
COMPANY_ENTITIES = {
    "CORPORATION", "LIMITED LIABILITY COMPANY", "PUBLIC BENEFIT CORPORATION",
    "BENEFIT CORPORATION",
}

# International classes where a thesis phrase means the applicant builds or
# engineers the thing. A phrase that only appears under retail (035),
# construction, installation and repair (037: contractors and installers),
# education and entertainment (041), security guarding (045), toys (028) or
# clothing (025) does not count.
QUALIFYING_CLASSES = {
    "001", "004", "006", "007", "009", "011", "012", "013", "017",
    "038", "039", "040", "042",
}
GOODS_CLASSES = {"001", "004", "006", "007", "009", "011", "012", "013", "017"}

# (label used in the title, pattern). The first pattern that matches an item
# names it; the order runs from specific to generic. A label goes into the
# title after "for", so it must be a phrase the matched wording literally
# supports: "semiconductors" for semiconductor wafers, not "semiconductor chips".
_THESIS_PHRASES: list[tuple[str, re.Pattern[str]]] = [
    (label, re.compile(pat, re.I))
    for label, pat in [
        ("counter-drone systems",
         r"counter[\s-]*(?:drone|uas|uav|unmanned)|anti-drone|drone (?:detection|interceptors?|jamm\w+)"
         r"|interceptor drones?"),
        ("military drones", r"military drones?"),
        ("unmanned aerial vehicles",
         r"unmanned aerial (?:vehicle|system)s?|unmanned aircraft|\buavs?\b|\buas\b"
         r"|autonomous air(?:craft| vehicles?)"),
        ("VTOL aircraft", r"\be?vtols?\b|vertical[\s-]+take[\s-]?off"),
        ("autonomous vessels",
         r"(?:autonomous|unmanned|uncrewed)[^;]{0,60}(?:vessels?|boats?|submersibles?|watercraft"
         r"|underwater vehicles?|undersea vehicles?|surface vehicles?)|\b(?:usvs?|uuvs?|auvs?)\b"),
        ("unmanned ground vehicles", r"unmanned ground vehicles?|autonomous (?:land|ground) vehicles?"),
        ("autonomous vehicles",
         r"autonomous vehicles?|(?:self-driving|driverless) (?:cars?|vehicles?|trucks?|automobiles?)"),
        ("humanoid robots", r"humanoid"),
        ("legged robots", r"legged robots?|quadruped"),
        ("robotic arms", r"robot(?:ic)? arms?"),
        ("robotic hands", r"robot(?:ic)? (?:manipulator )?(?:hands?|grippers?)"),
        ("robotic manipulators", r"robot(?:ic)? (?:manipulators?|end effectors?)"),
        ("exoskeletons", r"exoskeleton"),
        ("industrial robots", r"industrial robots?|robots? for industrial"),
        ("hypersonics", r"hypersonic"),
        ("missiles", r"\bmissiles?\b"),
        ("munitions", r"\bmunitions?\b|\bwarheads?\b"),
        ("electronic warfare systems", r"electronic warfare"),
        ("directed energy systems", r"directed energy(?! deposition)"),
        # Not "infusion devices", and not the fusion machines that weld pipe.
        ("fusion energy",
         r"\bfusion (?:reactors?|energy|power)\b|\b(?:nuclear|thermonuclear|plasma) fusion\b"
         r"|\btokamaks?\b|stellarators?"),
        ("nuclear reactors",
         r"nuclear (?:fission )?reactors?|micro-?reactors?|small modular (?:nuclear )?reactors?|fission reactors?"),
        ("nuclear power", r"nuclear (?:power|fuels?|generators?|fission|energy|plants?)|\bfission\b"),
        ("geothermal energy", r"geothermal"),
        ("solid-state batteries", r"solid[\s-]state batter\w+"),
        ("battery cells", r"battery cells?"),
        ("lithium batteries", r"lithium[\s-](?:ion|metal) (?:batter\w+|cells?)"),
        ("energy storage systems",
         r"energy storage|battery storage(?! (?:cases?|box(?:es)?|bags?|containers?|organi[sz]ers?|racks?))"),
        ("electrolyzers", r"electroly[sz]ers?"),
        ("power plants", r"power plants?"),
        ("power grids", r"power grids?|electric(?:al|ity)? grids?|grid[\s-]scale"),
        ("semiconductor manufacturing",
         r"semiconductor[^;]{0,40}(?:manufactur\w+|processing|fabricat\w+|equipment|machines?|foundr\w+)"
         r"|lithograph\w+|wafer (?:processing|fabrication)|(?:wafer|chip|silicon) foundr\w+"),
        ("photonics", r"photonics?\b"),
        ("semiconductors",
         r"semiconductor (?:chips?|devices?|wafers?|components?|circuits?)|semiconductors\b|microchips?"),
        ("launch vehicles", r"launch vehicles?|space launch"),
        ("rocket engines", r"rocket (?:engines?|motors?)"),
        ("rockets", r"\brockets?\b"),
        # Not "aerospace vehicles", which are as often aircraft.
        ("spacecraft", r"spacecraft|\bspace vehicles?"),
        ("satellite communications", r"satellite communications?|\bsatcom\b"),
        ("satellite imagery", r"satellite (?:imagery|images)"),
        ("satellites", r"\bsatellites?\b"),
        ("additive manufacturing", r"additive manufactur\w+"),
        ("rare earths", r"rare earths?"),
        ("critical minerals", r"critical minerals?"),
        ("machine tools", r"machine tools?|\bcnc\b|metalworking machines?"),
        ("metal casting", r"metal casting|metal foundr(?:y|ies)|\bfoundry (?:machines?|equipment)"),
        ("factory automation", r"(?:factory|industrial) automation"),
        ("radar", r"\bradars?\b"),
        ("lidar", r"\blidars?\b"),
        ("military systems", r"\bmilitary\b|weapons? systems?"),
        ("drones", r"\bdrones?\b"),
        ("robots", r"\brobots?\b|\brobotics?\b"),
    ]
]
_LABEL_ORDER = [label for label, _ in _THESIS_PHRASES]
_GENERIC_LABELS = {"drones", "robots", "military systems", "radar", "lidar"}
# (named, loose): items with the loose label vote for the named one when the
# list states the named one outright.
_FOLD = [("unmanned aerial vehicles", "drones"), ("nuclear reactors", "nuclear power")]

# An item with any of these is consumer or off-thesis wording, whatever else
# it says: a toy drone, a radar detector, satellite television.
_CONSUMER = re.compile(
    r"\btoys?\b|\bgames?\b|\bgaming\b|vacuum|lawn\s?mowers?|headphones|earphones|earbuds"
    r"|smart\s?watch|mobile phone|cell phone|smartphone|screen protectors|selfie|power banks?"
    r"|kitchen|companion|\bpets?\b|children|hobby|plush"
    r"|model (?:rockets?|aircraft|airplanes?|spacecraft|space vehicles?)|(?<![\w-])scale models?"
    r"|action figures|\bracing\b|camera drones?|photography drones?|radar detectors?"
    r"|fish\s?finders?|sonar|pyrotechnic|fireworks"
    r"|satellite (?:navigat\w+|television|tv|radio|dish\w*|receiv\w+|phones?|telephon\w+)"
    r"|(?:via|by) satellite|cosmetic|surgical|surgeons?|medical|dental|massage|window cleaning"
    r"|swimming pool|retail|wholesale|advertising|marketing|consumer electronics|astronomical"
    # Robot vacuums and mops under their ID Manual names, and other home
    # appliances called robots. A humanoid "for household chores" is not one,
    # and neither is a robot that cleans streets or factories.
    r"|household [\w ]{0,30}robots?|robot(?:s|ic)?(?: cleaners?)? for household (?:purposes?|use)\b"
    r"|housekeeping robots?|robot(?:ic)? (?:mops?|air purifiers?)|\bpool clean\w+|entertainment drones?"
    # Lithography as printing, not as chip making.
    r"|offset lithograph\w*|lithograph\w* (?:print\w*|inks?|press\w*|plates?)|printing (?:plates?|inks?|presses)"
    # A stock phrase of the USPTO ID Manual for hot-rod and boat parts.
    r"|for land vehicle propulsion"
    # Radar as the way a gadget measures, not as the goods: tank level gauges,
    # weather apps, speed guns. Sports training devices, whatever they sense with.
    r"|radar[\s-]level|weather radar (?:imagery|images?|maps?|data|information)"
    r"|radar (?:speed )?guns?|speed radars?|\bgolf\w*|baseball|\bsports (?:training|performance|equipment)",
    re.I,
)
# A service that only uses or sits beside the technology: a drone survey of
# a building site, space insurance, training for plant staff, a 3D-printed
# sofa. Checked on service classes only, where it stops the item counting.
_SIDE_SERVICE = re.compile(
    r"insurance|workforce|staffing|surveying|mapping services|photogrammetry|furniture|cabinetry"
    r"|engraving",
    re.I,
)
# In a service class, wording that says the service is software.
_HOSTED_SOFTWARE = re.compile(
    r"software as a service|\bsaas\b|platform as a service|\bpaas\b|non-downloadable"
    r"|application service provider|providing (?:temporary use of )?(?:on-?line )?[^;,]{0,40}software",
    re.I,
)
# "Software-defined radar apparatus" is apparatus.
_SOFTWARE = re.compile(
    r"software(?![\s-]defined)|downloadable|application programs|computer programs?|mobile applications?"
    r"|\bapps?\b|firmware",
    re.I,
)
# "none of the foregoing relating to robotics": everything after wording
# like this is what the mark does NOT cover, and is never matched.
_EXCLUSION = re.compile(
    r"\b(?:none of the (?:foregoing|aforesaid|aforementioned|above)"
    r"|not (?:including|for|being|related|relating|in the (?:field|nature))"
    r"|excluding|except(?:ing)?|other than|with the exception of|but not(?! limited))\b.*$",
    re.I | re.S,
)
# "satellite connectivity" on a power station is not a satellite.
_SATELLITE_MODIFIER = re.compile(
    r"(?:navigation )?satellites? (?:connectivity|internet|broadband|links?|signals?|data|positioning"
    r"|systems? \(gnss\))",
    re.I,
)
# A software item that lists what the software does ("software for planning
# missions; managing constellations; monitoring operations") arrives split at
# the semicolons. The later pieces are still software.
_SOFTWARE_LIST = re.compile(r"software\b(?![\s-]defined)[^;]*?\bfor\s+(?:[\w-]+\s+){0,2}?[a-z]+ing\b", re.I)
_CONTINUATION = re.compile(r"^(?:and\s+|or\s+)?[a-z]+ing\b")
# Words after which an item stops naming the goods and starts qualifying them:
# "cameras FOR unmanned aerial vehicles" are cameras.
_QUALIFIER = re.compile(
    r"\b(?:for|being|featuring|comprised|comprising|consisting|used in|in the field)\b",
    re.I,
)

# How the thesis goods relate to the applicant, best first.
SCOPES = ("goods", "related", "software", "services")

# Backstop for household names that file through many subsidiaries, where the
# count of marks under one legal name understates the group.
_INCUMBENT = re.compile(
    r"\b(lockheed|northrop|raytheon|rtx|boeing|general dynamics|l3harris|bae systems|honeywell"
    r"|general electric|ge vernova|ge aerospace|siemens|amazon|google|alphabet|apple|microsoft"
    r"|meta platforms|nvidia|intel|micron|qualcomm|tesla|ford motor|general motors|toyota|honda"
    r"|samsung|sony|westinghouse|caterpillar|deere|textron|leidos|kbr|nextera|exxon|chevron"
    r"|shell|halliburton|oceaneering|aerovironment|teledyne|3m|dupont|abb|fanuc|rockwell"
    r"|blue origin|milacron|boston dynamics"
    # Listed companies, late-stage private companies and corporate arms seen
    # in live output under 25 marks: Joby Aviation, Palladyne AI (ex Sarcos),
    # Nauticus Robotics, VisionWave, Redwood Materials, Commonwealth Fusion,
    # TAE Technologies, Dover Motion, Astrion, Kaishan, the Hyundai and LG
    # battery plant (HLBMA, 1 LG Road, Ellabell).
    r"|joby|palladyne|sarcos|nauticus|visionwave|redwood materials|commonwealth fusion|tae"
    r"|dover motion|astrion|kaishan|hlbma|hyundai|lg energy|softbank|spacex|anduril|rocket lab"
    r"|kratos|rheinmetall|hitachi|mitsubishi|panasonic|applied materials|lam research|asml|tsmc)\b",
    re.I,
)
# Acquisition and holding vehicles of private-equity roll-ups, and the
# "Co., Ltd." style of foreign sellers' US shells.
_VEHICLE = re.compile(
    r"\b(holdco|intermediate|parent|acquisitions?|bidco|topco|midco|merger sub)\b|\bco\.?,?\s*ltd\b"
    r"|\bholdings? (?:[ivx]{1,4}|\d+)\b",
    re.I,
)
# The US arm of a foreign or larger group: "Kaishan Compressor (USA), LLC",
# "HEO (USA), INC.", "Acme Robotics North America, Inc.".
_US_ARM = re.compile(
    r"\((?:usa?|u\.s\.a?\.?|america)\)"
    r"|\b(?:usa|u\.s\.a\.|north america|americas?)(?:,?\s+(?:inc|llc|l\.l\.c|corp|corporation|co|company)\.?)?$",
    re.I,
)
# A mark that names its parent ("SEMIDICE A MICROSS COMPANY") belongs to a
# subsidiary of an established group, however new the filing entity is.
_SUBSIDIARY_MARK = re.compile(r"\ban? [\w&'. -]+ company$", re.I)
# A filer who typed a personal name as the owner of a "corporation".
_COMPANY_WORD = re.compile(
    r"\b(inc|incorporated|llc|corp|corporation|co|company|ltd|limited|lp|pbc|plc|group|holdings?"
    r"|labs?|systems?|technolog(?:y|ies)|robotics|energy|aerospace|defense|industries|solutions"
    r"|services|partners|space|power|usa|ai|international|global|enterprises?|ventures?|dynamics"
    r"|motors|works|studios?|semiconductors?|semi|atomics|nuclear|aviation|electric|automation"
    r"|manufacturing|materials|sciences?|research|computing|networks?|software|engineering"
    r"|capital|associates|america|american|fusion|drones?|photonics|orbital|tech)\b",
    re.I,
)

# In an owner's legal name "Company" and "Co." are the legal form, as "Inc."
# is. base.strip_legal keeps them (they are part of the name a company shows
# the world); the filing-history match drops them.
_COMPANY_FORM = re.compile(r"[\s,]+(?:company|co)\.?$", re.I)

_OWNER = re.compile(r"^(?P<name>.+?)\s*\((?P<entity>[^;()]+)(?:;\s*(?P<org>[^()]*))?\)\s*$")
_CLASS = re.compile(r"^\s*IC\s+(\d{3})\s*:\s*(.*)$", re.S)

BASIS_WORDS = {
    "1a": "use in commerce",
    "1b": "intent to use",
    "44d": "foreign priority claim",
    "44e": "foreign registration",
    "66a": "Madrid extension",
}


class RateLimited(Exception):
    """The API asked us to stop. Nothing more is requested this run."""


@dataclass
class Mark:
    serial: str
    wordmark: str | None
    owner: str  # legal name as filed
    entity: str  # CORPORATION, LIMITED LIABILITY COMPANY, ...
    state_of_org: str | None  # "Delaware"
    city: str | None
    state: str | None
    filed: date
    basis: list[str]
    first_use: date | None
    classes: list[str]
    goods: list[tuple[str, str]]  # (class, item) in filing order
    attorney: str | None
    status: str | None
    # Filled by assess():
    label: str = ""
    labels: list[str] = field(default_factory=list)
    scope: str = "services"  # the closest any thesis item comes to a product (SCOPES)
    thesis_items: list[tuple[str, str]] = field(default_factory=list)
    share: float = 0.0
    fit: float = 0.0
    excerpt: str = ""

    @property
    def intent_to_use(self) -> bool:
        """Any part of the application rests on intent to use."""
        return "1b" in self.basis

    @property
    def basis_kind(self) -> str:
        """itu (1b without 1a), use (1a without 1b), mixed, or other (44d, 44e, 66a only)."""
        a, b = "1a" in self.basis, "1b" in self.basis
        return "mixed" if a and b else "itu" if b else "use" if a else "other"

    @property
    def hardware(self) -> bool:
        return self.scope in ("goods", "related")

    @property
    def url(self) -> str:
        return TSDR_URL.format(serial=self.serial)


# ---------------------------------------------------------------- parsing


def _one(v: Any) -> Any:
    """The API returns a scalar for one owner and a list for joint owners."""
    if isinstance(v, list):
        return v[0] if len(v) == 1 else None
    return v


def _many(v: Any) -> list:
    """A field that is usually a list, as a list of its non-empty values."""
    if v is None:
        return []
    return [x for x in v if x] if isinstance(v, (list, tuple)) else [v]


def _text(v: Any) -> str | None:
    """A field that is usually a string, squashed; None when empty."""
    v = _one(v)
    return " ".join(v.split()) or None if isinstance(v, str) else None


def _tidy_place(s: str | None) -> str | None:
    """'OKLAHOMA CITY' and 'bronx' as filed become 'Oklahoma City' and
    'Bronx'. Mixed case ('McLean') is left as the applicant typed it."""
    if s and (s.isupper() or s.islower()):
        return s.title()
    return s


def parse_owner(raw: str) -> tuple[str, str, str | None, str | None] | None:
    """'Proception Inc. (CORPORATION; Delaware, USA)' ->
    ('Proception Inc.', 'CORPORATION', 'Delaware', 'USA')."""
    m = _OWNER.match(raw.strip())
    if not m:
        return None
    org = (m.group("org") or "").strip()
    state, country = None, None
    if org:
        parts = [p.strip() for p in org.split(",") if p.strip()]
        country = parts[-1]
        state = parts[0].title() if len(parts) > 1 else None
    return m.group("name").strip(), m.group("entity").strip().upper(), state, country


def looks_personal(name: str) -> bool:
    """'William Evert Traver IV': two to four plain words and nothing that
    says company. Such owners are people who ticked the wrong entity box."""
    tokens = name.replace(".", " ").replace(",", " ").split()
    if not 2 <= len(tokens) <= 4 or _COMPANY_WORD.search(name):
        return False
    return all(re.fullmatch(r"[A-Za-z][A-Za-z'\-]*", t) for t in tokens)


def split_goods(goods: list[str] | None) -> list[tuple[str, str]]:
    """['IC 012: Drones; Military drones.'] -> [('012', 'Drones'), ('012', 'Military drones')]."""
    out: list[tuple[str, str]] = []
    for block in goods or []:
        m = _CLASS.match(block)
        cls, body = (m.group(1), m.group(2)) if m else ("", block)
        for item in body.split(";"):
            item = " ".join(item.split()).strip(" .")
            if item:
                out.append((cls, item))
    return out


def parse_mark(src: dict) -> tuple[Mark | None, str]:
    """One API hit to a Mark, or (None, reason) when it is not a live
    application by a single US company."""
    if not src.get("alive"):
        return None, "dead"
    owners = _many(src.get("ownerName"))
    if len(owners) != 1 or not isinstance(owners[0], str):
        return None, "joint owners" if len(owners) > 1 else "unparsed owner"
    parsed = parse_owner(owners[0])
    if not parsed:
        return None, "unparsed owner"
    name, entity, state_of_org, country = parsed
    if entity not in COMPANY_ENTITIES:
        return None, "not a company"
    if country != "USA":
        return None, "foreign entity"
    address = _text(src.get("ownerStateCountryAddress")) or ""
    if not address.upper().endswith("UNITED STATES"):
        return None, "foreign address"
    if looks_personal(name):
        return None, "owner is a personal name"
    if _VEHICLE.search(name):
        return None, "holding vehicle or foreign-style shell"
    if _US_ARM.search(name):
        return None, "US arm of another company"
    filed = parse_date(str(_one(src.get("filedDate")) or "")[:10])
    serial = str(src.get("id") or "").strip()
    if not filed or not serial.isdigit():
        return None, "no serial or date"
    state = address.rsplit(",", 1)[0].strip() if "," in address else ""
    state = re.sub(r"\s+\d{5}(?:-\d{4})?$", "", state).strip() or None
    return Mark(
        serial=serial,
        wordmark=_text(src.get("wordmark")),
        owner=name,
        entity=entity,
        state_of_org=state_of_org,
        city=_tidy_place(_text(src.get("ownerCity"))),
        state=_tidy_place(state),
        filed=filed,
        basis=[str(b).lower() for b in _many(src.get("currentBasis"))],
        first_use=parse_date(str(_one(src.get("firstUseAnyDate")) or "")[:10]),
        classes=sorted({str(c).replace("IC", "").strip() for c in _many(src.get("internationalClass"))}),
        goods=split_goods(_many(src.get("goodsAndServices"))),
        attorney=_text(src.get("attorney")),
        status=_text(src.get("statusDescription")),
    ), ""


def covered(item: str) -> str:
    """The part of an item that says what the mark covers: the text before
    any exclusion ('..., none of the foregoing relating to robotics')."""
    return _EXCLUSION.sub("", item)


def is_consumer(item: str) -> bool:
    return bool(_CONSUMER.search(covered(item)))


def thesis_label(item: str) -> str | None:
    """The thesis phrase an item of goods matches, or None."""
    text = covered(item)
    if _CONSUMER.search(text):
        return None
    for label, pat in _THESIS_PHRASES:
        if label == "satellites":
            if pat.search(_SATELLITE_MODIFIER.sub(" ", text)):
                return label
        elif pat.search(text):
            return label
    return None


def item_scope(cls: str, item: str, continues_software: bool = False) -> str:
    """What a thesis item says about the applicant: it makes the thing
    ('goods'), makes something for it ('related'), writes software for it,
    or sells a service around it. `continues_software` marks a piece of a
    software item that was split at a semicolon."""
    text = covered(item)
    if cls not in GOODS_CLASSES:
        return "software" if _HOSTED_SOFTWARE.search(text) else "services"
    head = _QUALIFIER.split(text, maxsplit=1)[0]
    # Software is what the item is (its head), not something it contains:
    # "battery systems comprised of batteries, inverters and software" is hardware.
    if continues_software or _SOFTWARE.search(head):
        return "software"
    return "goods" if thesis_label(head) else "related"


def label_and_scope(cls: str, item: str, continues_software: bool = False) -> tuple[str, str] | None:
    """(label, scope) for a thesis item, or None. When the item is the thing
    itself, the label is read from its head, so "interceptor missiles for
    defense against unmanned aerial vehicles" are missiles, not UAVs."""
    label = thesis_label(item)
    if not label:
        return None
    scope = item_scope(cls, item, continues_software)
    if scope == "goods":
        label = thesis_label(_QUALIFIER.split(covered(item), maxsplit=1)[0]) or label
    return label, scope


def pick_label(hits: list[tuple[str, str]]) -> tuple[str, str]:
    """(label, scope) that best names a mark from its (label, scope) hits.
    Only the items closest to a product vote (goods before software before
    services). Among them the most frequent specific label wins, ties going
    to the more specific pattern; a generic label ('robots', 'drones') wins
    only when it has more than twice the items of any specific one, so a
    passing mention does not name the mark. Plain 'drones' items count toward
    'unmanned aerial vehicles', and 'nuclear power' items toward 'nuclear
    reactors', when the list also says so outright."""
    scope = min((sc for _, sc in hits), key=SCOPES.index)
    counts: dict[str, int] = {}
    for label, sc in hits:
        if sc == scope:
            counts[label] = counts.get(label, 0) + 1
    for named, loose in _FOLD:
        if named in counts and loose in counts:
            counts[named] += counts.pop(loose)

    def best(pool: list[str]) -> str | None:
        return min(pool, key=lambda k: (-counts[k], _LABEL_ORDER.index(k))) if pool else None

    specific = best([k for k in counts if k not in _GENERIC_LABELS])
    generic = best([k for k in counts if k in _GENERIC_LABELS])
    if specific and generic and counts[generic] > 2 * counts[specific]:
        specific = None
    return specific or generic, scope


def assess(mark: Mark) -> str:
    """Decide whether a mark's goods are on thesis. Fills the mark's label,
    scope, share, excerpt and fit. Returns '' to keep it, or the reason to
    drop it."""
    if not mark.goods:
        return "no goods text"
    if mark.basis_kind == "other" and mark.basis:
        # Resting only on a foreign application or registration (44d, 44e,
        # 66a): the brand was born abroad, or a large company is filing
        # through a shell. Either way not a young US company's first step.
        return "foreign-basis filing"
    hits: list[tuple[str, str]] = []
    labelled: list[tuple[str, str]] = []  # (label, scope) per thesis item
    per_class: dict[str, list[int]] = {}  # class -> [items, thesis items]
    consumer = 0
    software_run: dict[str, bool] = {}  # class -> inside a split software item
    for cls, item in mark.goods:
        tally = per_class.setdefault(cls, [0, 0])
        tally[0] += 1
        continues = bool(software_run.get(cls) and _CONTINUATION.match(item))
        software_run[cls] = continues or bool(_SOFTWARE_LIST.search(covered(item)))
        if is_consumer(item) or cls == "028":
            consumer += 1
            continue
        if cls not in QUALIFYING_CLASSES:
            continue
        if cls not in GOODS_CLASSES and _SIDE_SERVICE.search(covered(item)):
            continue
        found = label_and_scope(cls, item, continues)
        if found:
            tally[1] += 1
            hits.append((cls, item))
            labelled.append(found)
    if not hits:
        return "no thesis goods in a qualifying class"
    n = len(mark.goods)
    mark.share = round(len(hits) / n, 3)
    # A phrase or two inside a long shopping list is boilerplate: the thesis
    # goods must be a real part of the whole list and of at least one class.
    best_class = max(t[1] / t[0] for t in per_class.values())
    if mark.share < MIN_SHARE or best_class < MIN_CLASS_SHARE:
        return "thesis goods are a small part of the list"
    if consumer > len(hits) or consumer / n > 0.25:
        return "mostly consumer goods"
    if 2 * per_class.get("037", [0, 0])[0] > n:
        return "mostly installation and repair services"
    if all(cls not in GOODS_CLASSES for cls, _ in hits) and mark.share < MIN_SERVICE_SHARE:
        return "thesis services are a small part of the list"
    if mark.basis_kind == "use" and mark.first_use:
        if (mark.filed - mark.first_use).days > STALE_USE_YEARS * 365:
            return "mark in use for years"
    mark.thesis_items = hits
    mark.labels = sorted({lab for lab, _ in labelled}, key=_LABEL_ORDER.index)
    mark.label, mark.scope = pick_label(labelled)
    # The whole list when it is short; otherwise only the thesis items, and
    # the text says so.
    body, prefix = excerpt(mark.goods), "Goods and services: "
    if body.endswith(" ..."):
        body, prefix = excerpt(hits), f"On-thesis goods and services ({len(hits)} of {n} items): "
    mark.excerpt = prefix + body
    seen = classify(body)
    mark.fit = seen["fit"]
    if mark.fit < 0.3 and seen["terms"] and mark.scope == "goods":
        # The wording gave the classifier a term, but not enough on its own,
        # and the applicant lists the thing itself as its goods. Classify the
        # wording together with what this application is read to cover.
        mark.fit = classify(f"{covers(mark)}. {body}")["fit"]
    if mark.fit < 0.3:
        return "below thesis fit"
    return ""


def covers(mark: Mark) -> str:
    """What an assessed application covers, in the words the title uses:
    'for spacecraft', 'for software related to satellites'."""
    return _GOODS_PHRASE[mark.scope].format(mark.label)


def excerpt(items: list[tuple[str, str]], limit: int = EXCERPT_CHARS) -> str:
    """Items in filing order with their class, cut at an item boundary."""
    out, last = "", None
    for cls, item in items:
        piece = (f"IC {cls}: " if cls and cls != last else "") + item
        sep = "" if not out else ("; " if cls == last else ". ")
        if len(out) + len(sep) + len(piece) > limit:
            if not out:
                out = piece[:limit].rsplit(" ", 1)[0]
            out += " ..."
            break
        out += sep + piece
        last = cls
    return out


def owner_key(name: str) -> str:
    """Groups 'AEROVIRONMENT, INC.' with 'AeroVironment, Inc.' and nothing else."""
    return re.sub(r"[^a-z0-9]+", " ", name.casefold()).strip()


def owner_core(name: str) -> str:
    """Legal name without its legal form, for a wider history match. "Company"
    and "Co." go as well, so the count of earlier marks under the name takes
    in Sierra Nevada Corporation for Sierra Nevada Company, LLC, and "first
    filing" is not said of an owner that only changed its legal form."""
    core = strip_legal(name)
    return _COMPANY_FORM.sub("", core).rstrip(" ,") or core


def lead_order(m: Mark) -> tuple:
    """Sort key, best first: newest filing, then the mark closest to a
    product, then a word mark over a design mark, then the higher serial."""
    return (-m.filed.toordinal(), SCOPES.index(m.scope), m.wordmark is None, -int(m.serial))


# ---------------------------------------------------------------- requests


def search_body(since: date, until: date, size: int, start: int,
                owners: list[str] | None = None) -> dict:
    must: list[dict] = [
        {"query_string": {"query": " OR ".join(SEARCH_TERMS), "default_operator": "AND",
                          "fields": ["goodsAndServices"]}},
        {"query_string": {"query": '"UNITED STATES"', "fields": ["ownerFullText"]}},
        {"query_string": {"query": 'CORPORATION OR "LIMITED LIABILITY COMPANY"',
                          "fields": ["ownerEntity"]}},
    ]
    if owners:
        must.append({"bool": {"should": [{"match_phrase": {"ownerName": o}} for o in owners],
                              "minimum_should_match": 1}})
    return {
        "query": {"bool": {"must": must, "filter": [
            {"range": {"filedDate": {"gte": since.isoformat(), "lte": until.isoformat()}}}]}},
        "size": size, "from": start, "track_total_hits": True,
        "sort": [{"filedDate": {"order": "desc"}}, {"id": {"order": "desc"}}],
        "_source": _SOURCE_FIELDS,
    }


def history_body(owners: list[tuple[str, date]]) -> dict:
    """One request for many owners. Three counts per owner (name, date):
    t: every mark on file under the legal name, with the earliest filing date
    l: marks under the legal name filed before the date
    c: marks under the name without its legal suffix filed before the date
    """
    filters: dict[str, dict] = {}
    should: list[dict] = []
    for i, (name, before) in enumerate(owners):
        core = owner_core(name)
        earlier = {"range": {"filedDate": {"lt": before.isoformat()}}}
        filters[f"t{i}"] = {"match_phrase": {"ownerName": name}}
        filters[f"l{i}"] = {"bool": {"must": [{"match_phrase": {"ownerName": name}}, earlier]}}
        filters[f"c{i}"] = {"bool": {"must": [{"match_phrase": {"ownerName": core}}, earlier]}}
        should.append({"match_phrase": {"ownerName": core}})
    return {
        "query": {"bool": {"should": should, "minimum_should_match": 1}},
        "size": 0, "track_total_hits": True,
        "aggs": {"owners": {"filters": {"filters": filters},
                            "aggs": {"first": {"min": {"field": "filedDate"}}}}},
    }


def parse_history(resp: dict, owners: list[tuple[str, date]]) -> dict[str, dict]:
    """{legal name: {"total", "first_filed", "before", "name_before",
    "name_first_filed"}}. `name_first_filed` is the earliest filing under the
    name without its legal suffix, or None when there is none before the
    batch. An owner whose buckets are missing is left out, and is then not
    emitted."""
    buckets = ((resp.get("aggregations") or {}).get("owners") or {}).get("buckets") or {}
    out: dict[str, dict] = {}
    for i, (name, _) in enumerate(owners):
        t, l, c = (buckets.get(f"{k}{i}") for k in "tlc")
        if not t or l is None or c is None or not t.get("doc_count"):
            continue
        first = parse_date(((t.get("first") or {}).get("value_as_string") or "")[:10])
        if not first:
            continue
        out[name] = {
            "total": int(t["doc_count"]),
            "first_filed": first,
            "before": int(l.get("doc_count") or 0),
            "name_before": int(c.get("doc_count") or 0),
            "name_first_filed": parse_date(((c.get("first") or {}).get("value_as_string") or "")[:10]),
        }
    return out


def _post(body: dict, ttl: float) -> dict:
    """POST to the search API. antenna.http retries once on a server error or
    a dropped connection, and gives up at once on a 429 whose Retry-After is
    long, which is what CloudFront sends. A 429 that reaches here becomes
    RateLimited and nothing more is requested. Pauses after a real request
    (not a cache hit)."""
    before = http.stats()["requests"]
    try:
        return http.post_json(SEARCH_URL, body, ttl=ttl, retries=1, timeout=40)
    except http.HttpError as e:
        if e.status == 429:
            raise RateLimited(str(e)) from e
        raise
    finally:
        if http.stats()["requests"] > before:
            time.sleep(PAUSE_SECONDS)


def _hits(resp: dict) -> tuple[list[dict], int | None]:
    """(records, total matches). The site's API flattens the Elasticsearch
    envelope (`totalValue`, `source`); the stock shape (`total.value`,
    `_source`) is read too. Total is None when the response does not say."""
    hits = resp.get("hits") if isinstance(resp, dict) else None
    if not isinstance(hits, dict):
        return [], None
    total = hits.get("totalValue")
    if total is None:
        total = hits.get("total")
        if isinstance(total, dict):
            total = total.get("value")
    rows = [h.get("source") or h.get("_source") for h in hits.get("hits") or [] if isinstance(h, dict)]
    return [r for r in rows if isinstance(r, dict)], int(total) if isinstance(total, (int, float)) else None


def _last_page(sources: list[dict], read: int, total: int | None) -> bool:
    """True when a page just read was the last one: nothing came back, the
    stated total has been reached, or (no total stated) the page was short."""
    if not sources:
        return True
    return read >= total if total is not None else len(sources) < PAGE_SIZE


# ---------------------------------------------------------------- signals


def is_incumbent(name: str, hist: dict, today: date) -> bool:
    if _INCUMBENT.search(name):
        return True
    if hist["total"] >= INCUMBENT_MARKS:
        return True
    # Earlier marks under the bare name (hist["name_first_filed"]) are not
    # used here: they are as often a namesake (Unigrid, CX2) as the same
    # company under an older legal name (Asylon Incorporated).
    return (today - hist["first_filed"]).days > INCUMBENT_YEARS * 365


def distinct_marks(marks: list[Mark]) -> int:
    """Brands in a batch. One word mark filed in seven classes is seven
    applications and one brand; the design marks together count as one."""
    names = {m.wordmark.casefold() for m in marks if m.wordmark}
    return len(names) + (1 if any(not m.wordmark for m in marks) else 0)


def foreign_priority(marks: list[Mark]) -> bool:
    """Any application leans on a foreign filing (44d, 44e, 66a): the brand
    started abroad, which for a US entity usually means a foreign parent."""
    return any(b in ("44d", "44e", "66a") for m in marks for b in m.basis)


def strength_for(marks: list[Mark], hist: dict, first_ever: bool, today: date) -> float:
    """0..1. Lifted by: intent to use, the owner's first filing, a family of
    brands at once, goods that are hardware and squarely on thesis. Lowered by
    the marks the owner already held and by a foreign priority claim. A first
    filer is the norm here (about half of all owners), so on its own it is
    worth a solid score, not a rare one; the top of the range needs a new
    owner filing a family of hardware brands on intent to use. Brands are
    counted, not applications: one name filed in many classes is one brand."""
    lead = marks[0]
    brands = distinct_marks(marks)
    scope = min((m.scope for m in marks), key=SCOPES.index)
    itu = any(m.intent_to_use for m in marks)
    mostly_itu = 2 * sum(1 for m in marks if m.intent_to_use) > len(marks)
    foreign = foreign_priority(marks)
    s = 0.12
    if itu:
        s += 0.07
    elif lead.first_use and (lead.filed - lead.first_use).days <= 365:
        s += 0.03
    if first_ever:
        s += 0.09
    elif (today - hist["first_filed"]).days <= 3 * 365 and hist["before"] <= 5:
        s += 0.04
    s += {"goods": 0.15, "related": 0.09, "software": 0.05, "services": 0.0}[scope]
    s += 0.24 * squash(brands - 1, 2.0)
    s += 0.10 * (max(m.fit for m in marks) - 0.3) / 0.7
    s += 0.04 * max(m.share for m in marks)
    if lead.attorney:
        s += 0.02
    if first_ever and mostly_itu and scope == "goods" and brands >= 3 and not foreign:
        s += 0.14
    if foreign:
        s -= 0.08
    s -= 0.20 * squash(hist["before"], 8)
    return round(min(max(s, 0.15), 0.97), 3)


_BASIS_ADJECTIVE = {"itu": "intent-to-use ", "use": "in-use ", "mixed": "", "other": ""}
_GOODS_PHRASE = {
    "goods": "for {}",
    "related": "for goods related to {}",
    "software": "for software related to {}",
    "services": "for services related to {}",
}


def title_for(marks: list[Mark], first_ever: bool, total: int | None = None) -> str:
    """One sentence for the owner's batch. `total` is every application the
    owner has on file. When the owner filed others beside these (off thesis,
    the same day or later), these are "N of its first M", never "its first N".
    The goods are said of the whole batch only when every application in it
    covers them; otherwise they are said of the lead mark, or counted."""
    lead = marks[0]
    goods = covers(lead)
    n = len(marks)
    name = lead.wordmark
    others = total is not None and total > n
    if n == 1:
        adj = _BASIS_ADJECTIVE[lead.basis_kind]
        what = f"trademark {name}" if name else "design mark"
        first = ""
        if first_ever:
            first = f", one of its first {total} filings" if others else ", its first trademark filing"
        options = [
            f"Filed {adj}{what} {goods}{first}",
            f"Filed {adj}{what} {goods}",
            f"Filed {adj}trademark {goods}{first}",
            f"Filed {adj}trademark {goods}",
        ]
    else:
        span = (marks[0].filed - min(m.filed for m in marks)).days
        when = "in one day" if span == 0 else f"in {span + 1} days"
        itu = sum(1 for m in marks if m.basis_kind == "itu")
        use = sum(1 for m in marks if m.basis_kind == "use")
        basis = (", all intent to use" if itu == n else ", all in use" if use == n
                 else f", {itu} intent to use" if itu else "")
        head = f"Filed {n} trademark applications"
        if first_ever:
            head = (f"Filed {n} of its first {total} trademark applications" if others
                    else f"Filed its first {n} trademark applications")
        alike = sum(1 for m in marks if (m.label, m.scope) == (lead.label, lead.scope))
        if alike == n:
            # A slogan mark that ends in a full stop would end the title with one.
            incl = f", including {name}" if name and not name.endswith(".") else ""
            options = [
                f"{head} {when} {goods}{basis}{incl}",
                f"{head} {goods}{basis}{incl}",
                f"{head} {when} {goods}{basis}",
                f"{head} {goods}{basis}",
                f"{head} {goods}",
            ]
        else:
            named = [f"{head} {when}{basis}, including {name} {goods}",
                     f"{head}{basis}, including {name} {goods}"] if name else []
            options = named + [
                f"{head} {when}{basis}, {alike} of them {goods}",
                f"{head}{basis}, {alike} of them {goods}",
                f"{head}, {alike} of them {goods}",
            ]
    for t in options:
        if len(t) <= MAX_TITLE:
            return t
    return options[-1][:MAX_TITLE].rsplit(" ", 1)[0].rstrip(" ,.")


def display_owner(marks: list[Mark]) -> str:
    """The owner's name as filed on the lead application, unless that one is
    in capitals and another application spells it in mixed case."""
    names = [m.owner for m in marks]
    return ([x for x in names if not x.isupper()] or names)[0]


def build_signal(marks: list[Mark], hist: dict, today: date) -> Signal | None:
    """One owner's on-thesis applications to one Signal, or None for an
    established company."""
    marks = sorted(marks, key=lead_order)
    lead = marks[0]
    if is_incumbent(lead.owner, hist, today):
        return None
    if any(m.wordmark and _SUBSIDIARY_MARK.search(m.wordmark) for m in marks):
        return None
    # "First" means nothing on file before this batch under the owner's name,
    # even with the legal suffix ignored (Acme LLC that became Acme, Inc.).
    first_ever = hist["name_before"] == 0 and hist["before"] == 0
    # Never state a total smaller than the batch (the index can lag a day).
    total = max(int(hist["total"]), len(marks))
    itu = sum(1 for m in marks if m.intent_to_use)
    in_use = sum(1 for m in marks if m.basis_kind == "use")
    dates = sorted({m.filed for m in marks})
    burst = any(sum(1 for m in marks if 0 <= (m.filed - d).days <= 6) >= 3 for d in dates)
    kind = "trademark_intent_to_use" if itu else ("trademark_in_use" if in_use else "trademark_filing")

    lines = []
    for m in marks[:6]:
        basis = ", ".join(f"{b} {BASIS_WORDS.get(b, '')}".strip() for b in m.basis) or "basis not stated"
        # The date of first use the applicant claims (for one class; another
        # class of the same application may claim an earlier one).
        use = f", first use claimed {iso(m.first_use)}" if m.first_use and "1a" in m.basis else ""
        # What the application is read to cover, in plain words, then the
        # applicant's own wording.
        lines.append(f"{m.wordmark or 'Design mark'} (serial {m.serial}, filed {iso(m.filed)}, "
                     f"{basis}{use}), an application {covers(m)}. {m.excerpt}")
    if len(marks) > 6:
        lines.append(f"Plus {len(marks) - 6} more applications.")
    metrics: dict[str, Any] = {
        "tm_serial": lead.serial,
        "tm_serials": [m.serial for m in marks],
        "tm_wordmarks": sorted({m.wordmark for m in marks if m.wordmark}),
        "tm_applications": len(marks),
        "tm_distinct_marks": distinct_marks(marks),
        "tm_intent_to_use": itu,
        "tm_in_use": in_use,
        "tm_basis": lead.basis,
        "tm_foreign_priority": foreign_priority(marks),
        "tm_classes": sorted({c for m in marks for c in m.classes}),
        "tm_goods_labels": sorted({lab for m in marks for lab in m.labels}),
        "tm_goods_scope": lead.scope,
        "tm_thesis_share": max(m.share for m in marks),
        "tm_hardware_goods": any(m.hardware for m in marks),
        "tm_family_burst": burst,
        "tm_first_filing_by_owner": first_ever,
        "tm_owner_marks_total": total,
        "tm_owner_marks_before": hist["before"],
        "tm_similar_name_marks_before": hist["name_before"],
        "tm_owner_first_filed": iso(hist["first_filed"]),
        "tm_owner_entity": lead.entity,
        "tm_owner_state_of_org": lead.state_of_org,
        "tm_status": lead.status,
    }
    if hist.get("name_first_filed"):
        metrics["tm_similar_name_first_filed"] = iso(hist["name_first_filed"])
    if lead.first_use and "1a" in lead.basis:
        metrics["tm_first_use_date"] = iso(lead.first_use)
    if lead.attorney:
        metrics["tm_attorney"] = lead.attorney
    return Signal(
        source=SLUG, family=FAMILY, kind=kind,
        entity=EntityHint(
            name=display_owner(marks),
            one_liner="Trademark goods: " + excerpt([("", item) for _, item in lead.thesis_items], 150),
            location=", ".join(x for x in (lead.city, lead.state) if x) or None,
            links={"trademark": lead.url},
        ),
        title=title_for(marks, first_ever, total),
        occurred_at=iso(lead.filed),
        url=lead.url,
        value=len(marks), unit="trademark applications",
        strength=strength_for(marks, hist, first_ever, today),
        metrics=metrics,
        text="\n".join(lines),
    )


def collect(ctx: Context) -> Iterable[Signal]:
    since, until = ctx.since, ctx.today
    by_owner: dict[str, list[Mark]] = {}
    seen: set[str] = set()
    dropped: dict[str, int] = {}

    def take(sources: list[dict], only: set[str] | None = None) -> None:
        for src in sources:
            try:
                mark, why = parse_mark(src)
                if mark and (mark.filed < since or mark.filed > until):
                    mark, why = None, "outside window"
                if mark:
                    why = assess(mark)
                if not mark or why:
                    if only is None:
                        dropped[why] = dropped.get(why, 0) + 1
                    continue
                key = owner_key(mark.owner)
                if mark.serial in seen or (only is not None and key not in only):
                    continue
                seen.add(mark.serial)
                by_owner.setdefault(key, []).append(mark)
            except Exception as e:  # one malformed record must not lose the page
                sid = src.get("id") if isinstance(src, dict) else repr(src)[:40]
                ctx.warn(f"{SLUG}: skipped record {sid}: {type(e).__name__}: {e}")

    # 1. Page through on-thesis applications, newest first.
    # About four candidate owners in ten turn out to be established once
    # their filing history is read, so read twice the limit.
    want = math.ceil(ctx.limit * 2.0) if ctx.limit else None
    start, total, truncated = 0, None, False
    try:
        for _ in range(MAX_PAGES):
            sources, total = _hits(_post(search_body(since, until, PAGE_SIZE, start), SEARCH_TTL))
            take(sources)
            start += len(sources)
            if _last_page(sources, start, total):
                break
            if want and len(by_owner) >= want:
                truncated = True
                break
        else:
            # Older applications were not read, so an owner's batch may be
            # incomplete: fetch each emitted owner's applications by name.
            ctx.warn(f"{SLUG}: stopped at {start} of {total} applications (page cap)")
            truncated = True
    except RateLimited:
        ctx.warn(f"{SLUG}: rate limited after {start} applications; continuing with what was read")
        truncated = True
    except Exception as e:
        ctx.warn(f"{SLUG}: search failed after {start} applications: {type(e).__name__}: {e}")
        truncated = True
    ctx.log(f"{SLUG}: read {start} of {total} applications since {since}, "
            f"{len(by_owner)} candidate owners; dropped {dict(sorted(dropped.items()))}")

    # Newest filers first. With a limit, the loop below stops as soon as
    # enough owners have been emitted, one history request per batch.
    keys = sorted(by_owner, key=lambda k: max(m.filed for m in by_owner[k]), reverse=True)

    emitted = 0
    for i in range(0, len(keys), HISTORY_BATCH):
        batch = keys[i:i + HISTORY_BATCH]
        try:
            # 2. If paging stopped early, fetch the rest of these owners'
            #    applications in the window so counts and dates are complete.
            if truncated:
                names = sorted({m.owner for k in batch for m in by_owner[k]})
                read = 0
                for _ in range(MAX_PAGES):
                    sources, found = _hits(_post(search_body(since, until, PAGE_SIZE, read, names), SEARCH_TTL))
                    take(sources, only=set(batch))
                    read += len(sources)
                    if _last_page(sources, read, found):
                        break
            # 3. Each owner's filing history, in one request for the batch.
            owners = [(min(by_owner[k], key=lead_order).owner, min(m.filed for m in by_owner[k]))
                      for k in batch]
            history = parse_history(_post(history_body(owners), HISTORY_TTL), owners)
        except RateLimited:
            ctx.warn(f"{SLUG}: rate limited; {len(keys) - i} owners without a history lookup were not emitted")
            return
        except Exception as e:
            ctx.warn(f"{SLUG}: history lookup failed for {len(batch)} owners: {type(e).__name__}: {e}")
            continue
        for k, (name, _) in zip(batch, owners):
            hist = history.get(name)
            if not hist:
                ctx.warn(f"{SLUG}: no filing history returned for {name}; not emitted")
                continue
            try:
                sig = build_signal(by_owner[k], hist, ctx.today)
            except Exception as e:
                ctx.warn(f"{SLUG}: could not build signal for {name}: {type(e).__name__}: {e}")
                continue
            if sig is None:
                continue
            yield sig
            emitted += 1
            if ctx.limit and emitted >= ctx.limit:
                return
