"""FCC Experimental Licensing System: who is asking to transmit, before they ship.

Anything that radiates (a drone datalink, a radar, a satellite, a counter-UAS
sensor) needs an FCC experimental licence or a special temporary authority
(STA) before it can be tested, so a hardware company shows up here months
before it has a product page. This collector reads the rolling 30-day feed of
grants and also walks the sequential file numbers back from the newest one,
which exposes applications that are still pending: on the record, with a
contact, a test site and a radio bill of materials, weeks before any grant.

What it cannot do: say whether an applicant has ever filed before. The only
search by applicant name (GenericSearchResult.cfm) returns 503, so novelty is
reported as "filings seen in this run's window" and never as "first ever".
"""

from __future__ import annotations

import gzip
import html
import json
import re
import shutil
import subprocess
import time
from datetime import date, timedelta
from typing import Any, Callable, Iterable, Iterator

from .. import http
from ..models import EntityHint, Person, Signal
from ..thesis import LONE_TERM_FIT, classify
from .base import Context, clean_domain, iso, loose_name, normalize_name, parse_date, squash

SLUG = "fcc_els"
FAMILY = "regulatory"
STAGE = "discover"
DESCRIPTION = "FCC experimental licence and STA applications, pending and granted, with contact and test site"

HOST = "apps.fcc.gov"
BASE = f"https://{HOST}/oetcf/els"
RSS_URL = f"{BASE}/rss/NewGrants.cfm"
INFO_URL = f"{BASE}/reports/GetApplicationInfo.cfm?id_file_num={{}}"

# An old government site. One request a second was measured clean; stay there.
# antenna.http carries the same figure for this host; this is only the floor
# should that entry ever go, and it is not a second wait on top of it.
MIN_INTERVAL = 1.0
# File numbers requested per application type per run, walking back from the
# newest one. New licences arrive at about 5 a business day and STAs at about
# 13, so this reaches back roughly five weeks and two weeks respectively.
WALK_CAP = 120
WALK_TYPES = ("CN", "ST")  # CN new conventional licence, ST special temporary authority
# The frontier is the last file number that exists. It is declared found
# after this many consecutive absent numbers.
MISS_STOP = 10
# Detail pages fetched per applicant and kind. A company with nine STAs in a
# month is described well enough by its three newest.
MAX_PER_APPLICANT = 3

# Added when the applicant has no other filing in the window. Most applicants
# qualify, so it is kept small: at 0.08 one signal in six landed in the band
# the contract reserves for the top decile.
FIRST_SEEN_PREMIUM = 0.05

RSS_TTL = 3 * 3600
MISSING_TTL = 3 * 3600  # an absent number will exist tomorrow
PENDING_TTL = 20 * 3600  # re-poll daily for a status change
FINAL_TTL = 60 * 86400  # a granted or dismissed application does not change

_FINAL_STATUS = re.compile(r"grant|dismiss|denied|withdraw|cancel|expired|terminat|return", re.I)
_PENDING_STATUS = re.compile(r"^pending$", re.I)

# Experiment types (the FCC's own category, present on grants) that sit on the
# thesis, with the phrase used for them in a title. The phrase names the
# category and nothing more: "Rocket Launch" is also the category of a radio
# test at a factory, so it is never rendered as "a rocket launch".
THESIS_TYPES = {
    "Unmanned Aerial Vehicle": "UAV testing",
    "C-UAS": "counter-UAS testing",
    "Robotics": "robotics testing",
    "Rocket Launch": "launch vehicle testing",
    "Cubesats": "cubesat operations",
    "Space (other than cubesats)": "space operations",
    "satellite, general": "satellite testing",
    "big LEO (Low Earth Orbit)": "low Earth orbit satellite testing",
    "Little LEO (Low Earth Orbit)": "low Earth orbit satellite testing",
    "Fixed Radar": "fixed radar testing",
    # Applicants pick this FCC category for any radar, on a vehicle or not.
    "Vehicle Radar": "radar testing",
    "RF Sensor": "RF sensor testing",
    "Autonomous Ground Vehicle": "autonomous ground vehicle testing",
    "Autonomous Underwater Vehicle": "autonomous underwater vehicle testing",
    "Electronic Countermeasures": "electronic countermeasures testing",
    "Identification Friend or Foe": "identification friend or foe testing",
    "Military": "military radio testing",
    "High-Altitude Platform System": "high-altitude platform testing",
    "Telemetry, Tracking and Command": "telemetry, tracking and command",
}

# Categories whose grants are not read at all: event and broadcast crews, and
# GPS repeaters in hangars. A name that is itself on the thesis still gets
# its form read.
UNREAD_TYPES = {"Remote audio and video", "Remote Video Production", "Special Event", "Broadcast", "GPS Reradiator"}

# How a category is written into Signal.text, where the FCC's label alone
# would leave the thesis classifier with one stray word or with none. Each
# rendering says what the label means and nothing more: a rocket is a launch
# vehicle, a "big LEO" system is a satellite system in low Earth orbit.
# "Space (other than cubesats)" keeps the FCC's words and gains one: the
# classifier no longer reads the cubesat it is told to exclude, and "Space"
# by itself is not a thesis term. The label itself, verbatim, is in
# metrics["els_experiment_type"]. The radar categories need no rendering:
# "radar" is read as written. RF sensor and high-altitude categories have
# none either: what the sensor or the platform is for has to come from the
# applicant's own words.
TYPE_IN_WORDS = {
    "Unmanned Aerial Vehicle": "Unmanned Aerial Vehicle (UAV)",
    "Rocket Launch": "Rocket Launch (launch vehicle)",
    "Space (other than cubesats)": "Space (spacecraft other than cubesats)",
    "big LEO (Low Earth Orbit)": "big LEO (low Earth orbit satellite)",
    "Little LEO (Low Earth Orbit)": "Little LEO (low Earth orbit satellite)",
    "Autonomous Ground Vehicle": "Autonomous Ground Vehicle (an autonomous vehicle)",
    "Electronic Countermeasures": "Electronic Countermeasures (electronic warfare)",
}

# Not who this is for: schools, public bodies, research centres run for the
# government, the large primes, carriers, big tech, tier-one auto suppliers
# and incumbent satellite operators.
_STOP = re.compile(
    r"""
      \buniversit | \bcollege\b | \bschool\b | \binstitute\b | \bpolytechnic | \bacademy\b
    | \bregents\b | \bboard\s+of\b | \bfoundation\b | \bpenn\s+state\b | \bvirginia\s+tech\b | \bgeorgia\s+tech\b
    | \bUCAR\b | \bresearch\s+range\b | \bnational\s+lab | \blaborator(?:y|ies)\b
    | \bcounty\b | \bcity\s+of\b | \bstate\s+of\b | \btown\s+of\b | \bdepartment\b | \bdept\b | \bpolice\b
    | \bsheriff | \bfire\s+(?:dept|department|district|rescue)\b | \bauthority\b | \bcommission\b
    | \bNASA\b | \bNOAA\b | \bU\.?S\.?\s+(?:army|navy|air\s+force|coast\s+guard|government)\b | \bnaval\b
    | \bair\s+force\b | \bnational\s+guard\b | \bMITRE\b | \bSRI\s+International\b | \bBattelle\b
    | \bDraper\b | \bAerospace\s+Corporation\b | \bSouthwest\s+Research\b | \bJHU\b | \bAPL\b
    | \bLockheed\b | \bSikorsky\b | \bRaytheon\b | \bRTX\b | \bCollins\s+Aerospace\b | \bPratt\b
    | \bNorthrop\b | \bBoeing\b | \bInsitu\b | \bAurora\s+Flight\b | \bGeneral\s+Dynamics\b | \bGulfstream\b
    | \bGeneral\s+Atomics\b | \bL3\s*Harris\b | \bL-?3\b | \bHarris\s+Corp | \bBAE\s+Systems\b | \bLeidos\b
    | \bSAIC\b | \bScience\s+Applications\b | \bHuntington\s+Ingalls\b | \bHoneywell\b | \bTextron\b
    | \bBell\s+(?:Textron|Helicopter)\b | \bLeonardo\b | \bDRS\b | \bThales\b | \bAirbus\b | \bSaab\b
    | \bElbit\b | \bRheinmetall\b | \bKratos\b | \bAeroVironment\b | \bCACI\b | \bBooz\s+Allen\b
    | \bParsons\b | \bPeraton\b | \bMantech\b | \bSierra\s+Nevada\b | \bGeneral\s+Electric\b | \bGE\s+Aerospace\b
    | \bCaterpillar\b | \bJohn\s+Deere\b | \bDeere\b | \bSiemens\b | \bRolls-?Royce\b
    | \bSpace\s+Exploration\b | \bSpaceX\b | \bBlue\s+Origin\b | \bUnited\s+Launch\b | \bRocket\s+Lab\b
    | \bAmazon\b | \bKuiper\b | \bGoogle\b | \bAlphabet\b | \bWaymo\b | \bApple\b | \bMicrosoft\b
    | \bMeta\s+Platforms\b | \bFacebook\b | \bTesla\b | \bNvidia\b | \bIntel\b | \bQualcomm\b | \bBroadcom\b
    | \bSamsung\b | \bSony\b | \bLG\s+Electronics\b | \bHuawei\b | \bNokia\b | \bEricsson\b | \bCisco\b
    | \bMotorola\b | \bGarmin\b | \bHP\s+Inc\b | \bHewlett | \bDell\b | \bIBM\b | \bTexas\s+Instruments\b
    | \bAnalog\s+Devices\b | \bNXP\b | \bInfineon\b | \bKeysight\b | \bRohde\b | \bPanasonic\b
    | \bAT&T\b | \bVerizon\b | \bT-Mobile\b | \bSprint\b | \bComcast\b | \bCharter\s+Comm | \bCox\s+Comm
    | \bDISH\b | \bEchoStar\b | \bHughes\s+Network\b | \bViasat\b | \bIntelsat\b | \bSES\b | \bIridium\b
    | \bGlobalstar\b | \bInmarsat\b | \bEutelsat\b | \bOneWeb\b | \bLigado\b | \bU\.?S\.?\s+Cellular\b
    | \bFord\s+Motor\b | \bGeneral\s+Motors\b | \bToyota\b | \bHonda\b | \bHyundai\b | \bNissan\b | \bBMW\b
    | \bMercedes\b | \bVolkswagen\b | \bStellantis\b | \bBosch\b | \bContinental\b | \bDenso\b | \bAptiv\b
    | \bMagna\b | \bValeo\b | \bHELLA\b | \bZF\b | \bVeoneer\b | \bGoodyear\b | \bBNSF\b | \bUnion\s+Pacific\b
    | \bMoog\b | \bAccenture\b | \bHII\b | \bElta\b | \bRockwell\b | \bVertex\s+Aerospace\b | \bV2X\b
    | \bAxon\b | \bNordic\s+Semiconductor\b | \bMitsubishi\b | \bHitachi\b | \bToshiba\b | \bFormula\s+One\b
    | \bbroadcast | \btelevision\b | \bESPN\b | \bNBC\b | \bCBS\b | \bFox\s+(?:Sports|Corp)
    # Listed or decacorn-scale: on thesis, but nobody needs to be told.
    | \bAnduril\b | \bShield\s+AI\b | \bPalantir\b | \bJoby\b | \bArcher\s+Aviation\b | \bFirefly\s+Aerospace\b
    | \bAST\s*(?:SpaceMobile|&\s*Science)\b | \bPlanet\s+Labs\b | \bSpire\s+Global\b | \bBlackSky\b | \bRedwire\b
    | \bIntuitive\s+Machines\b | \bVoyager\s+(?:Space|Technologies)\b | \bDroneShield\b | \bRed\s+Cat\b
    | \bTeal\s+Drones\b | \bOndas\b | \bZoox\b | \bAurora\s+Innovation\b | \bRivian\b | \bLucid\s+Motors\b
    # Contractors that have filed here for decades.
    | \bTCOM\b | \bToyon\b | \bTechnology\s+Service\s+Corp | \bWeibel\b
    # A process-instrument maker of long standing: its radar gauges the level in a tank.
    | \bVEGA\s+Grieshaber\b
    """,
    re.I | re.X,
)
_TEST_ROW = re.compile(r"^\s*testz?\b|^\s*testing\b|fcc\s*test", re.I)
# School, agency and military hosts in their national forms: every suffix the
# shared clean_domain refuses, and the same shapes for other countries.
_STOP_TLD = re.compile(r"\.(?:edu|gov|mil)(?:\.[a-z]{2})?$|\.ac\.[a-z]{2}$|\.gc\.ca$")
# The filer writes from a parent's mailbox: a subsidiary of a prime or of a
# large public company under its own name (Wisk and Liquid Robotics file from
# boeing.com, Dedrone from axon.com).
_STOP_DOMAINS = {
    "boeing.com", "rtx.com", "collins.com", "lmco.com", "ngc.com", "gd.com", "gdit.com",
    "gd-ms.com", "gdls.com", "l3harris.com", "baesystems.com", "baesystems.us", "ga.com",
    "ga-asi.com", "hii.com", "hii-tsd.com", "honeywell.com", "textron.com", "leidos.com",
    "saic.com", "axon.com", "moog.com", "accenture.com", "gov2x.com", "kratosdefense.com",
    "avinc.com", "mitre.org", "sri.com", "aero.org", "swri.org", "amazon.com", "google.com",
    "apple.com", "microsoft.com", "meta.com", "intel.com", "qualcomm.com", "att.com",
    "verizon.com", "t-mobile.com", "spacex.com", "blueorigin.com", "airbus.com", "thalesgroup.com",
    "ge.com", "geaerospace.com", "siemens.com", "bosch.com", "ford.com", "gm.com", "toyota.com",
    "anduril.com", "shield.ai", "palantir.com", "jobyaviation.com", "archer.com", "fireflyspace.com",
    "droneshield.com", "droneshield.us", "ast-science.com", "planet.com", "spire.com", "blacksky.com",
    "redwirespace.com", "intuitivemachines.com", "zoox.com", "aurora.tech", "rivian.com",
    "tcomlp.com", "toyon.com", "tsc.com",
}
# Carriers, ISPs and network vendors: on a new-licence form, which carries no
# description, they look exactly like a hardware startup. Applied only when
# nothing else places the filing on the thesis.
_TELECOM_NAME = re.compile(
    r"\bwireless\b|\btelecom|\bbroadband\b|\bcellular\b|\binternet\b|\bfiber\b|\bcable\b|\btelephone\b"
    r"|\bnet\b|(?-i:[a-z]Net\b)", re.I)
# The other regular filer of STAs: crews that cover races, concerts and award
# shows. The stoplist catches the ones named for it; the rest say so in the
# form's free text. Most of them now fail the thesis gate without help
# ("satellite broadcast" is no longer read as a satellite), so this is for
# the crew whose words do carry a thesis term, such as a drone camera or a
# drone race being covered. The bare word "broadcast" is not used: radio
# engineers write it as a verb.
_PRODUCTION_TEXT = re.compile(
    r"\bcablecast|\bwebcast|\btelecast|\b(?:television|tv|film|video|event|broadcast|live[- ]event)\s+production\b"
    r"|\bproduction\s+compan(?:y|ies)\b|\bwireless\s+microphones?\b|\bRF\s+cameras?\b|\bPart\s+74\b", re.I)

# Mailbox providers: an address here says nothing about the company.
_FREEMAIL = {
    "gmail.com", "googlemail.com", "yahoo.com", "ymail.com", "outlook.com", "hotmail.com",
    "live.com", "msn.com", "icloud.com", "me.com", "mac.com", "aol.com", "proton.me",
    "protonmail.com", "pm.me", "gmx.com", "mail.com", "comcast.net", "att.net",
    "verizon.net", "sbcglobal.net", "bellsouth.net", "cox.net", "charter.net", "earthlink.net",
}
# Words too common in company names to tie an e-mail domain to the applicant.
_GENERIC_WORDS = {
    "the", "and", "inc", "llc", "corp", "corporation", "company", "ltd", "limited", "group",
    "holdings", "technologies", "technology", "tech", "systems", "system", "labs", "lab",
    "space", "aero", "aerospace", "aviation", "robotics", "robotic", "dynamics", "industries",
    "defense", "defence", "research", "sciences", "science", "solutions", "services",
    "networks", "network", "wireless", "communications", "international", "global", "usa",
    "america", "american", "national", "advanced", "applied", "engineering", "enterprises",
    "radar", "sensing", "energy", "power", "air", "law", "consulting", "associates",
}
# A contact with one of these titles is the filer's agent, not the team.
_AGENT_TITLE = re.compile(r"counsel|attorney|lawyer|legal|consultant|agent|paralegal|esq", re.I)
# A founder or chief filing in person is the mark of a very small company.
_SENIOR_TITLE = re.compile(
    r"\b(?:co-?founder|founder|ceo|cto|coo|owner|managing\s+director|chief\s+\w+\s+officer"
    r"|chief\s+(?:engineer|scientist|technologist))\b|(?<!vice )(?<!vice-)\bpresident\b", re.I
)
_ORBIT = re.compile(r"non-?geostationary|low\s+earth\s+orbit|\bLEO\b|\bMEO\b", re.I)
_ORG_WORD = re.compile(
    r"\b(?:inc|llc|l\.l\.c|corp|corporation|co|company|ltd|limited|lp|l\.p|gmbh|plc|group|systems|technologies"
    r"|technology|networks|labs|laboratories|solutions|industries|aerospace|aviation|space|robotics|dynamics"
    r"|defense|partners|associates|enterprises|services|research|international)\b", re.I)
_HONORIFIC = re.compile(r"^(?:dr|mr|mrs|ms|prof|professor)\.?\s+", re.I)
# An "Attention" line that names a desk, not a person ("Spectrum Management",
# "Legal", "Regulatory Affairs"). Its mailbox is often spectrum@ or legal@,
# which would otherwise pass for the person's own.
_NOT_A_PERSON = re.compile(
    r"\b(?:spectrum|regulatory|legal|licens\w*|compliance|management|department|dept|team|office|affairs|operations"
    r"|engineering|admin\w*|accounts?|payable|program|attn|c/o|inc|llc|corp|ltd|group|company)\b", re.I)
_DBA = re.compile(r"\s+(?:d/b/a|d\.b\.a\.?|dba|doing\s+business\s+as)\s+", re.I)
# The station table's State cell is a dropdown; anything outside this list
# ("Space Station", "United States (All 50)", "Other") is not a place.
_US_STATES = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA", "Colorado": "CO",
    "Connecticut": "CT", "Delaware": "DE", "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID",
    "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA",
    "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN",
    "Mississippi": "MS", "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY", "North Carolina": "NC",
    "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR", "Pennsylvania": "PA",
    "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX",
    "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA", "West Virginia": "WV",
    "Wisconsin": "WI", "Wyoming": "WY", "District of Columbia": "DC", "Puerto Rico": "PR", "Guam": "GU",
}
_STATE_ALIASES = {"dist of columbia": "District of Columbia", "district of columbia": "District of Columbia"}
_NOT_A_CITY = re.compile(
    r"[\d,]|^(?:us|usa|conus|n/?a|none|tbd|various|multiple|mobile|airborne|nationwide|statewide|unknown|see\b.*|all|any)$",
    re.I)

# Free text on the form often ends with a "stop buzzer" contact. The number
# and the address are dropped; frequencies and file numbers do not have
# either shape.
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(
    r"\+\d{1,3}(?:[\s.-]?\(?\d{2,5}\)?){2,4}(?!\d)"  # +44 7733 933630, +1 914.319.2848
    r"|(?<![\d.-])(?:\(\d{3}\)\s?|\d{3}[\s.-])\d{3}[\s.-]\d{4}(?![\d-])"  # (415) 321-0934, 650-680-6000
)

_FILE_NUM = re.compile(r"\b(\d{4})-EX-([A-Z]{2})-(\d{4})\b")
_RSS_DESC = re.compile(r"A grant was issued on (\S+) to (.*), experiment type: (.*)$", re.S)
_TAG = re.compile(r"</?[A-Za-z!][^>]*>")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_TO_MHZ = {"hz": 1e-6, "khz": 1e-3, "mhz": 1.0, "ghz": 1e3}


# --------------------------------------------------------------------------
# Transport. apps.fcc.gov refuses Python's urllib outright (HTTP 403 from the
# CDN, measured, same User-Agent) and answers curl normally, so requests go
# out through curl while keeping antenna.http's disk cache, pacing, honest
# User-Agent and counters.
# --------------------------------------------------------------------------

def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", "replace")


def _curl(url: str, timeout: float) -> tuple[int, str]:
    proc = subprocess.run(
        ["curl", "-sS", "-g", "--proto", "=https", "--compressed", "--max-time", str(int(timeout)),
         "-A", http.USER_AGENT, "-w", "\n%{http_code}", url],
        capture_output=True, timeout=timeout + 10,
    )
    if proc.returncode != 0:
        raise OSError(f"curl exit {proc.returncode}: {proc.stderr.decode('utf-8', 'replace').strip()[:200]}")
    body, _, code = proc.stdout.rpartition(b"\n")
    return int(code or 0), _decode(body)


def _fetch(url: str, *, ttl: float | Callable[[str], float], timeout: float = 30, retries: int = 2,
           expect: tuple[str, ...] = ()) -> str:
    """GET a page through curl with the shared cache.

    `ttl` is seconds, or a function of the cached page that returns seconds,
    so a page can be kept for months once it shows a final state and
    re-polled daily while it does not. `expect` lists markers of which one
    must be in the page: the CDN and the application server both answer 200
    with an error page at times, and such a page must be neither parsed as
    "no such file number" nor kept in the cache.
    """
    key = http._key("GET", url, None, "curl")
    conn = http._cache()
    try:
        row = conn.execute("SELECT fetched_at, status, body FROM http_cache WHERE key=?", (key,)).fetchone()
    finally:
        conn.close()
    if row and row[1] == 200:
        text = gzip.decompress(row[2]).decode("utf-8", "replace")
        if time.time() - row[0] < (ttl(text) if callable(ttl) else ttl):
            http._stats["cache_hits"] += 1
            return text

    http.HOST_INTERVAL.setdefault(HOST, MIN_INTERVAL)
    last: Exception | None = None
    for attempt in range(retries + 1):
        http._pace(HOST)
        http._stats["requests"] += 1
        try:
            status, text = _curl(url, timeout)
        except (OSError, subprocess.SubprocessError, ValueError) as e:
            last = e
            if attempt < retries:
                time.sleep(2.0 * (2 ** attempt))
            continue
        if status == 200 and expect and not any(marker in text for marker in expect):
            last = http.HttpError(status, url, "not the expected page: " + _clean(text)[:120])
            if attempt < retries:
                time.sleep(3.0 * (2 ** attempt))
            continue
        if status == 200:
            conn = http._cache()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO http_cache VALUES (?,?,?,?,?,?)",
                    (key, url, time.time(), status, json.dumps({"via": "curl"}), gzip.compress(text.encode())),
                )
                conn.commit()
            finally:
                conn.close()
            return text
        last = http.HttpError(status, url, text)
        if status in (404, 410):  # counted as antenna.http counts them: absent, not an error
            http._stats["not_found"] += 1
            raise last
        if status in (400, 401) or attempt == retries:
            break
        time.sleep(3.0 * (2 ** attempt))
    http._stats["errors"] += 1
    assert last is not None
    raise last


# --------------------------------------------------------------------------
# Parsing. Pure functions over page text, tested offline against fixtures.
# --------------------------------------------------------------------------

def _clean(fragment: str) -> str:
    """Tags out, entities decoded, whitespace collapsed."""
    return " ".join(html.unescape(_TAG.sub(" ", fragment)).replace("\xa0", " ").split())


def _scrub(text: str | None) -> str | None:
    """Free text from the form with phone numbers and e-mail addresses removed."""
    if not text:
        return None
    return " ".join(_PHONE.sub(" ", _EMAIL.sub(" ", text)).split()) or None


def _strip_noise(page: str) -> str:
    page = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", page)
    return re.sub(r"(?s)<!--.*?-->", " ", page)


def parse_rss(xml_text: str) -> list[dict[str, Any]]:
    """Recent grants feed -> one dict per item, in feed order (newest first)."""
    out = []
    for item in re.findall(r"(?is)<item>(.*?)</item>", xml_text):
        title = re.search(r"(?is)<title>(.*?)</title>", item)
        desc = re.search(r"(?is)<description>(.*?)</description>", item)
        if not title or not desc:
            continue
        fm = _FILE_NUM.search(title.group(1))
        dm = _RSS_DESC.match(_clean(desc.group(1)))
        if not fm or not dm:
            continue
        granted = parse_date(dm.group(1))
        if granted is None:
            continue
        cs = re.search(r"Callsign:\s*([A-Z0-9]+)", title.group(1))
        out.append({
            "file_num": fm.group(0),
            "seq": int(fm.group(1)),
            "type": fm.group(2),
            "year": int(fm.group(3)),
            "callsign": cs.group(1) if cs else None,
            "granted": granted,
            "applicant": dm.group(2).strip(),
            "experiment_type": dm.group(3).strip(),
            "url": INFO_URL.format(fm.group(0)),
        })
    return out


def parse_application_info(page: str) -> dict[str, Any] | None:
    """Status page for one file number. None when the number does not exist.

    A missing number returns the same 200 page with an empty results table,
    so existence is the presence of the file-number cell, not the status code.
    """
    m = re.search(r'(?is)<tbody[^>]*id="?offTblBdy"?[^>]*>(.*?)</tbody>', page)
    if not m:
        return None
    body = m.group(1)
    cells = [_clean(c) for c in re.split(r"(?i)<td\b[^>]*>", body)[1:]]
    at = next((i for i, c in enumerate(cells) if _FILE_NUM.fullmatch(c)), None)
    if at is None or len(cells) < at + 6:
        return None
    file_num, callsign, applicant, received, status, status_date = cells[at:at + 6]
    fm = _FILE_NUM.fullmatch(file_num)
    assert fm is not None

    def link(pattern: str) -> str | None:
        hit = re.search(pattern, body, re.I)
        return f"https://{HOST}{html.unescape(hit.group(1))}" if hit else None

    return {
        "file_num": file_num,
        "seq": int(fm.group(1)),
        "type": fm.group(2),
        "year": int(fm.group(3)),
        "callsign": None if callsign.upper() in ("", "N/A") else callsign,
        "applicant": applicant,
        "received": parse_date(received),
        "status": status,
        "status_date": parse_date(status_date),
        "form_url": link(r'"(/oetcf/els/reports/(?:442|STA)_Print\.cfm\?mode=current[^"]*)"'),
        "exhibits_url": link(r'"(/oetcf/els/reports/ViewExhibitReport\.cfm\?[^"]*)"'),
    }


def _labelled(page: str) -> list[tuple[str, str]]:
    """(label, value) pairs from a print view.

    Labels carry class small-bold-content and values small-content, as cells
    or spans. An empty value stays empty rather than swallowing the next
    label, which is what goes wrong when splitting on text nodes alone.
    """
    marked = re.sub(
        r'(?is)<(?:td|th|span)\b[^>]*class="?(small-bold-content|small-content)\b[^>]*>',
        lambda m: "\x00L" if m.group(1) == "small-bold-content" else "\x00V",
        _strip_noise(page),
    )
    # Each chunk runs to the next marker; the element itself ends at its
    # first closing cell or span tag (the site also emits a broken "</spa>").
    chunks = [(c[0], _clean(re.split(r"(?i)</(?:td|th|span|spa)\b", c[1:], maxsplit=1)[0]))
              for c in marked.split("\x00")[1:]]
    pairs = []
    for i, (kind, label) in enumerate(chunks):
        if kind != "L" or not label:
            continue
        nxt = chunks[i + 1] if i + 1 < len(chunks) else ("", "")
        pairs.append((label, nxt[1] if nxt[0] == "V" else ""))
    return pairs


def _table_rows(page: str) -> list[tuple[str, list[str]]]:
    """Every table row as ("th" | "td", [cell text, ...]), in page order."""
    rows = []
    for chunk in re.split(r"(?i)<tr\b[^>]*>", _strip_noise(page))[1:]:
        chunk = re.split(r"(?i)</tr>|</table>", chunk)[0]
        parts = re.split(r"(?i)<(th|td)\b[^>]*>", chunk)
        kinds = [k.lower() for k in parts[1::2]]
        cells = [_clean(c) for c in parts[2::2]]
        if cells:
            rows.append(("th" if all(k == "th" for k in kinds) else "td", cells))
    return rows


def _dms(cell: str) -> float | None:
    """'North 34 42 13' -> 34.7036. None when any part is blank."""
    m = re.fullmatch(r"(North|South|East|West)\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)", cell.strip(), re.I)
    if not m:
        return None
    deg = float(m.group(2)) + float(m.group(3)) / 60 + float(m.group(4)) / 3600
    if (m.group(1).lower() in ("north", "south") and deg > 90) or deg > 180:
        return None
    return round(-deg if m.group(1).lower() in ("south", "west") else deg, 4)


def _num(x: float) -> str:
    return f"{x:.5f}".rstrip("0").rstrip(".")


def _frequency(cell: str) -> dict[str, Any] | None:
    """'1435.00000000-1525.00000000 MHz' -> {low_mhz, high_mhz, label}."""
    m = re.match(r"\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)?\s*([kMG]?Hz)\b", cell, re.I)
    if not m:
        return None
    unit = m.group(3)[:-2].upper().replace("K", "k") + "Hz"
    low = float(m.group(1))
    high = float(m.group(2)) if m.group(2) else low
    if high < low:
        low, high = high, low
    label = f"{_num(low)} {unit}" if high == low else f"{_num(low)}-{_num(high)} {unit}"
    k = _TO_MHZ[unit.lower()]
    return {"low_mhz": round(low * k, 6), "high_mhz": round(high * k, 6), "label": label}


def _person_tokens(name: str | None) -> list[str]:
    return re.findall(r"[a-z]+", (name or "").lower())


def _owns_mailbox(person: str | None, local: str) -> bool:
    """The mailbox name carries the person's name: 'sdoly' or 'severin.staehly'.

    A name of four letters or more may sit anywhere in it; a three-letter
    one ('Ann', 'Lee') only as a whole part or at either end, so that
    'planning@' is not taken for Ann's.
    """
    parts = set(re.findall(r"[a-z]+", local))
    for t in _person_tokens(person):
        if len(t) >= 4 and t in local:
            return True
        if len(t) == 3 and (t in parts or local.startswith(t) or local.endswith(t)):
            return True
    return False


def parse_form(page: str) -> dict[str, Any]:
    """Form 442 or STA print view -> the fields this collector uses.

    Phone numbers and e-mail local parts are read past, never kept, and
    that includes the ones applicants type into the free-text answers.
    """
    pairs = _labelled(page)

    def first(*labels: str) -> str | None:
        for want in labels:
            for label, value in pairs:
                if label.lower().startswith(want.lower()):
                    if value:
                        return value
                    break
        return None

    def yes_no(prefix: str) -> bool | None:
        v = (first(prefix) or "").lower()
        return True if v == "yes" else False if v == "no" else None

    email = first("E-Mail Address:")
    local, _, email_domain = (email or "").strip().lower().rpartition("@")
    attention = first("Attention:")
    contact = " ".join(filter(None, [first("First Name:"), first("Last Name:")])) or None
    months = re.match(r"(\d+)\s*months?\b", first("Give an estimate of the length") or "", re.I)

    equipment: list[dict[str, Any]] = []
    stations: list[dict[str, Any]] = []
    freqs: list[dict[str, Any]] = []
    mode: str | None = None
    header: list[str] = []
    for kind, cells in _table_rows(page):
        low = [c.lower() for c in cells]
        if kind == "th":
            header = low
            if "model number" in low and "manufacturer" in low:
                mode = "equipment"
            elif "latitude" in low and "city" in low:
                mode = "station"
            elif "frequency" in low and "action" in low:
                mode = "frequency"
            else:
                mode = None
            continue
        if mode is None or len(cells) != len(header):
            mode = None
            continue
        row = dict(zip(header, cells))
        if mode == "equipment":
            units = row.get("no. of units", "")
            if row.get("manufacturer") or row.get("model number"):
                equipment.append({
                    "manufacturer": row.get("manufacturer") or None,
                    "model": row.get("model number") or None,
                    "units": int(units) if units.isdigit() else None,
                    "experimental": row.get("experimental", "").lower() == "yes",
                })
        elif mode == "station":
            where = next((v for k, v in row.items() if k.startswith("street")), "")
            stations.append({
                "city": row.get("city") or None,
                "state": row.get("state") or None,
                "lat": _dms(row.get("latitude", "")),
                "lon": _dms(row.get("longitude", "")),
                "mobile": _scrub(row.get("mobile")) if re.search(r"[A-Za-z]", row.get("mobile", "")) else None,
                "where": _scrub(where),
                "radius": row.get("radius of operation") or None,
            })
        elif mode == "frequency":
            f = _frequency(row.get("frequency", ""))
            if f:
                f["station_class"] = row.get("station class") or None
                freqs.append(f)

    return {
        "is_sta": bool(re.search(r"APPLICATION FOR SPECIAL TEMPORARY AUTHORITY", page)),
        "applicant": first("Applicant's Name (company):", "Name of Applicant:"),
        "attention": attention,
        # Whether the mailing address is the Attention person's own mailbox:
        # the one fact needed from the local part, which is not kept.
        "attention_owns_email": _owns_mailbox(attention, local),
        "mail_city": first("City:"),
        "mail_state": first("State:"),
        "email_domain": email_domain if local and "." in email_domain else None,
        "contact_name": contact,
        "contact_title": first("Title:"),
        "application_for": first("Application is for:"),
        "applicant_type": first("Applicant is:"),
        "gov_contract": yes_no("Is this authorization to be used for fulfilling the requirement of a government contract"),
        "duration_months": int(months.group(1)) if months else None,
        # The two free-text answers on an STA form. Form 442 has neither: its
        # narrative is an attached exhibit.
        "purpose": _scrub(first("Please explain the purpose of operation:")),
        "sta_reason": _scrub(first("Please explain in the area below why an STA is necessary:")),
        "op_start": parse_date(first("Operation Start Date:")),
        "op_end": parse_date(first("Operation End Date:")),
        "equipment": equipment,
        "stations": stations,
        "frequencies": freqs,
    }


# --------------------------------------------------------------------------
# Judgement: who to skip, which domain to trust, how strong, what to say.
# --------------------------------------------------------------------------

def is_stoplisted(name: str) -> bool:
    return bool(_STOP.search(name) or _TEST_ROW.search(name))


def _stop_domain(edom: str | None) -> bool:
    """A school, an agency, or the mailbox of a prime or large public company."""
    if not edom:
        return False
    edom = edom.lower()
    return bool(_STOP_TLD.search(edom)) or any(edom == d or edom.endswith("." + d) for d in _STOP_DOMAINS)


def split_dba(applicant: str) -> tuple[str, list[str]]:
    """'ACME AtronOmatic LLC d/b/a MyRadar' -> ('MyRadar', ['ACME AtronOmatic LLC']).

    The trading name is the one every other source uses, so it is the name;
    the legal name is kept as an alias.
    """
    parts = _DBA.split(applicant.strip(), maxsplit=1)
    if len(parts) == 2 and parts[0].strip(" ,") and parts[1].strip(" ,"):
        return parts[1].strip(" ,"), [parts[0].strip(" ,")]
    return applicant.strip(), []


def _name_tokens(name: str) -> list[str]:
    return [t for t in normalize_name(name).split() if t not in _GENERIC_WORDS]


def _only_generic(stem: str) -> bool:
    """The label is nothing but words any company might use: 'defensesystems', 'spacetech'."""
    reach = [True] + [False] * len(stem)
    for i in range(len(stem)):
        if reach[i]:
            for w in _GENERIC_WORDS:
                if stem.startswith(w, i):
                    reach[i + len(w)] = True
    return reach[-1]


def company_domain(email_domain: str | None, applicant: str) -> str | None:
    """The form's e-mail domain, kept only when it plainly belongs to the applicant.

    Applications are often filed by outside counsel or a frequency
    consultant, whose domain on the company's row would be a wrong fact. The
    domain is kept when its first label contains a distinctive word of the
    applicant's name, is contained in the name, or is the name's initials.
    Short or generic labels are held to a stricter test, so that a law
    firm at arc.com is not handed to Starcloud nor space.com to anyone.
    """
    host = clean_domain(email_domain)
    if not host or host in _FREEMAIL or _STOP_TLD.search(host):
        return None
    labels = host.split(".")
    stem = labels[-3] if len(labels) >= 3 and len(labels[-2]) <= 3 and len(labels[-1]) == 2 else labels[-2]
    stem = re.sub(r"[^a-z0-9]", "", stem)
    if len(stem) < 3 or stem in _GENERIC_WORDS:
        return None
    words = normalize_name(applicant).split()
    joined = "".join(words)
    # The label inside the name: anywhere when it is long, as a whole word
    # or the start of the name when it is three or four letters. The name
    # key keeps its descriptive words, so a label made only of those
    # ("defensesystems" inside "Surtr Defense Systems") has to be the whole
    # name or its start, as advancedspace.com is for Advanced Space.
    if stem in joined and (len(stem) >= 5 or stem in words or joined.startswith(stem)):
        if joined.startswith(stem) or not _only_generic(stem):
            return host
    # A distinctive word of the name inside the label ("firestorm" in
    # launchfirestorm, "joby" at the head of jobyaviation).
    for t in _name_tokens(applicant):
        if (len(t) >= 5 and t in stem) or (len(t) == 4 and stem.startswith(t)):
            return host
    raw_words = re.findall(r"[a-z0-9]+", applicant.lower())
    if stem in ("".join(w[0] for w in words), "".join(w[0] for w in raw_words)):
        return host
    return None


def _place(st: dict[str, Any]) -> str | None:
    """'at City, State', 'in State' or an orbit phrase; None when the row is free text.

    The form's cells are typed by the filer: a city cell can hold 'CONUS' or
    'NONGEOSTATIONARY', and for mobile stations the whole location sits in
    the Mobile cell. Only shapes that read as a place are used.
    """
    city, state, mobile = st.get("city") or "", st.get("state") or "", st.get("mobile") or ""
    if _ORBIT.search(f"{city} {mobile}"):
        return "for a non-geostationary satellite"  # LEO and MEO stations included
    state = _STATE_ALIASES.get(state.lower(), state)
    state_ok = state in _US_STATES
    city_ok = 3 <= len(city) <= 30 and not _NOT_A_CITY.search(city)
    if city_ok and state_ok:
        return f"at {city.title() if city.isupper() or city.islower() else city}, {state}"
    # "Rockaway Township, New Jersey" typed into the Mobile cell: taken only
    # when what follows the comma is a state, so "Airborne, CONUS" is not.
    m = re.fullmatch(r"([A-Za-z .'-]{2,28}), ([A-Za-z ]{4,20})", mobile)
    if not city and not state and m and _STATE_ALIASES.get(m.group(2).lower(), m.group(2)) in _US_STATES:
        return f"at {m.group(1)}, {_STATE_ALIASES.get(m.group(2).lower(), m.group(2))}"
    if state_ok:
        return f"in {state}"
    return None


def _where(form: dict[str, Any]) -> tuple[str | None, str | None]:
    """(first place with a count of further sites, first place alone)."""
    places = [p for p in dict.fromkeys(_place(s) for s in form["stations"]) if p]
    if not places:
        return None, None
    more = len(places) - 1
    full = places[0] if not more or places[0].startswith("for ") else f"{places[0]} and {more} more site{'s' if more > 1 else ''}"
    return full, places[0]


def _bands(form: dict[str, Any], n: int = 2) -> str | None:
    labels: list[str] = []
    for f in form["frequencies"]:
        if f["label"] not in labels:
            labels.append(f["label"])
    if not labels:
        return None
    if len(labels) > n:
        return f"{labels[0]} and {len(labels) - 1} other bands"
    if len(labels) == 2 and len(labels[0]) + len(labels[1]) > 34:
        return f"{labels[0]} and 1 other band"
    if len(labels) == 2 and labels[0].split()[-1] == labels[1].split()[-1]:
        return f"{labels[0].rsplit(' ', 1)[0]} and {labels[1]}"
    return " and ".join(labels)


def _day(d: date) -> str:
    return f"{d.day} {_MONTHS[d.month - 1]} {d.year}"


def _units(form: dict[str, Any]) -> int:
    return sum(e["units"] or 0 for e in form["equipment"])


def _days_to_grant(info: dict[str, Any]) -> int | None:
    if not info["status"].lower().startswith("grant") or not info.get("status_date") or not info.get("received"):
        return None
    days = (info["status_date"] - info["received"]).days
    return days if days >= 0 else None


def _first_fitting(options: list[str], limit: int = 109) -> str:
    """First candidate title short enough; the last one is the floor."""
    for t in options:
        if len(t) <= limit:
            return t
    return options[-1][:limit]


def build_title(info: dict[str, Any], form: dict[str, Any], experiment_type: str | None) -> str:
    """One sentence with the company as the implied subject and a number in it."""
    sta = info["type"] == "ST"
    what = "FCC special temporary authority" if sta else "a new FCC experimental licence"
    phrase = THESIS_TYPES.get(experiment_type or "")
    full, short = _where(form)
    bands = _bands(form)
    units = _units(form)
    opts: list[str] = []

    if info["status"].lower().startswith("grant"):
        days = _days_to_grant(info)
        tail = f", {days} day{'' if days == 1 else 's'} after filing" if days is not None else ""
        head = f"Received {what}" + (f" for {phrase}" if phrase else "")
        # The dates are typed by the filer; one that ends before the grant
        # (a wrong year) is left out rather than repeated.
        end_ok = sta and form.get("op_end") and info.get("status_date") and form["op_end"] >= info["status_date"]
        end = f", valid to {_day(form['op_end'])}" if end_ok else ""
        for where in dict.fromkeys(w for w in (full, short) if w and not (phrase and w.startswith("for "))):
            link = " to test " if not phrase and not where.startswith("for ") else " "
            opts += [f"{head}{link}{where}{tail}{end}", f"{head}{link}{where}{tail}"]
        if bands:
            opts.append(f"{head} on {bands}{tail}")
        opts.append(f"{head}{tail}")
        return _first_fitting(opts)

    head = f"Applied for {what}"
    count = f"{units} transmitter{'s' if units != 1 else ''}" if units else None
    start_ok = (sta and form.get("op_start") and info.get("received")
                and 0 <= (form["op_start"] - info["received"]).days <= 730)
    start = f", starting {_day(form['op_start'])}" if start_ok else ""
    for where in dict.fromkeys(w for w in (full, short) if w):
        link = " " if where.startswith("for ") else " to test "
        if bands and count:
            opts += [f"{head}{link}{where}: {count} on {bands}{start}", f"{head}{link}{where}: {count} on {bands}"]
        if bands:
            opts += [f"{head}{link}{where} on {bands}{start}", f"{head}{link}{where} on {bands}"]
        if count:
            opts.append(f"{head}{link}{where} with {count}{start}")
        opts += [f"{head}{link}{where}{start}", f"{head}{link}{where}"]
    if bands and count:
        opts.append(f"{head}: {count} on {bands}")
    if bands:
        opts.append(f"{head} to test on {bands}")
    opts.append(f"{head}, file number {info['file_num']}")
    return _first_fitting(opts)


def _own_make(maker: str, applicant: str) -> bool:
    """The manufacturer cell names the applicant itself (either side of a d/b/a)."""
    def key(name: str) -> set[str]:
        return set(_name_tokens(name)) or set(normalize_name(name).split())

    made_by = key(maker)
    name, aliases = split_dba(applicant)
    return any(made_by and key(n) and (made_by <= key(n) or key(n) <= made_by) for n in (name, *aliases))


def _gear(form: dict[str, Any], applicant: str) -> list[str]:
    """The transmitter list as text: '4 x Echodyne EchoGuard (experimental)'.

    Another company's name is left out when it carries a thesis word of
    its own. "2 x Nordic Semiconductor NRF9151" on a power-tool maker's form
    says what the vendor is, and a keyword model would read it as what the
    applicant is. The model number stays; the applicant's own make stays
    whole, because that is what the applicant builds.
    """
    out = []
    for e in form["equipment"]:
        maker = e["manufacturer"] or ""
        if maker and not _own_make(maker, applicant) and classify(maker)["terms"]:
            maker = ""
        label = " ".join(f"{maker} {e['model'] or ''}".split())
        if label:
            out.append((f"{e['units']} x " if e["units"] else "") + label + (" (experimental)" if e["experimental"] else ""))
    return out


def build_text(info: dict[str, Any], form: dict[str, Any], experiment_type: str | None) -> str:
    """Plain description of the filing, from the form only, for thesis fit.

    Everything the applicant wrote about the experiment is carried: both
    free-text answers of an STA form, the station notes and the radio list.
    The FCC's category is put in plain words where the label alone would
    not be understood (TYPE_IN_WORDS).
    """
    parts = [f"{info['applicant'].rstrip('. ')}. FCC {'special temporary authority' if info['type'] == 'ST' else 'experimental licence'} "
             f"application {info['file_num']}, status {info['status']}."]
    if experiment_type and experiment_type.lower() != "not specified":
        parts.append(f"Experiment type: {TYPE_IN_WORDS.get(experiment_type, experiment_type)}.")
    purpose, reason = form.get("purpose"), form.get("sta_reason")
    if purpose:
        parts.append(f"Purpose of operation: {purpose}")
    # Applicants often answer both questions with the same sentence.
    if reason and reason.lower().rstrip(". ") not in (purpose or "").lower():
        parts.append(f"Why the authority is needed: {reason}")
    sites = []
    for s in form["stations"]:
        bits = [", ".join(filter(None, [s.get("city"), s.get("state")]))]
        if s.get("mobile"):
            bits.append(s["mobile"])
        if _ORBIT.search(f"{s.get('city') or ''} {s.get('mobile') or ''}"):
            bits.append("satellite station")  # what the FCC's NONGEOSTATIONARY / LEO entry denotes
        # The free-text location is kept when it describes a range or an
        # operation; a plain street address adds nothing and is left out.
        if s.get("where") and not re.match(r"\s*\d", s["where"]):
            bits.append(s["where"])
        if s.get("radius"):
            bits.append(f"radius {s['radius']}")
        line = ", ".join(b for b in bits if b)
        if line and line not in sites:
            sites.append(line)
    if sites:
        parts.append("Stations: " + "; ".join(sites[:6]) + ".")
    labels = list(dict.fromkeys(f["label"] for f in form["frequencies"]))
    if labels:
        parts.append("Frequencies: " + ", ".join(labels[:8]) + ".")
    gear = _gear(form, info["applicant"])
    if gear:
        parts.append("Transmitters: " + "; ".join(gear[:10]) + ".")
    if form.get("gov_contract"):
        parts.append("Filed to fulfil a US government contract.")
    return " ".join(parts)


def _gps_only(form: dict[str, Any]) -> bool:
    """Every frequency is a GPS carrier: a hangar re-radiator, not a product radio.

    Either a narrow channel on L1, L2 or L5, or one wide entry that covers
    them and stays inside the satellite-navigation band (1164-1610 MHz), which
    is how the wideband repeaters are filed.
    """
    gps = (1575.42, 1227.6, 1176.45)
    fs = form["frequencies"]
    return bool(fs) and all(
        any(f["low_mhz"] <= g <= f["high_mhz"] for g in gps)
        and (f["high_mhz"] - f["low_mhz"] < 40 or (f["low_mhz"] >= 1150 and f["high_mhz"] <= 1620))
        for f in fs)


def strength_of(info: dict[str, Any], form: dict[str, Any], experiment_type: str | None,
                filings_seen: int, fit: float, own_title: str | None = None) -> float:
    """0..1 per the shared scale.

    Base by event: an application for a new licence that is still pending is
    the early one (0.55); a granted new licence is a solid dated step (0.42);
    an STA is a short test or a customer demo (0.40 pending, 0.30 granted).
    A founder or chief filing in person, fleet-scale unit counts, own-design
    transmitters and a government contract lift it. Several filings by the
    same applicant in the window mark an established programme and lower it;
    the only filing seen raises it a little (this is not "first ever": the
    source cannot answer that). A pending STA that already carries a call
    sign is an extension or change of one granted before, so it gets no
    such premium and sits with the granted ones. A GPS re-radiator is held
    in the routine band, and a filing with no thesis evidence at all is
    scaled down.
    """
    sta = info["type"] == "ST"
    granted = info["status"].lower().startswith("grant")
    extension = sta and not granted and bool(info.get("callsign"))
    s = (0.30 if granted or extension else 0.40) if sta else (0.42 if granted else 0.55)
    if _SENIOR_TITLE.search(own_title or ""):
        s += 0.12
    s += 0.14 * squash(_units(form), 40)
    if any(e["experimental"] for e in form["equipment"]):
        s += 0.05
    if form.get("gov_contract"):
        s += 0.05
    if experiment_type in THESIS_TYPES:
        s += 0.05
    if filings_seen <= 1 and not extension:
        s += FIRST_SEEN_PREMIUM
    elif filings_seen >= 4:
        s -= 0.12
    elif filings_seen >= 2:
        s -= 0.04
    if _gps_only(form) or experiment_type == "GPS Reradiator":
        s = min(s, 0.22)
    elif fit < 0.3 and experiment_type not in THESIS_TYPES:
        s *= 0.7
    return round(max(0.15, min(0.95, s)), 2)


def _same_person(a: str | None, b: str | None) -> bool:
    """Two renderings of one name: 'Eli Lockwood' / 'Elijah Lockwood',
    'MATTHEW W. SMITH' / 'Matthew Smith', or first and last swapped."""
    ta = [t for t in _person_tokens(_HONORIFIC.sub("", a or "")) if len(t) >= 2]
    tb = [t for t in _person_tokens(_HONORIFIC.sub("", b or "")) if len(t) >= 2]
    if len(ta) < 2 or len(tb) < 2:
        return False
    if set(ta) == set(tb):
        return True
    return ta[-1] == tb[-1] and (ta[0].startswith(tb[0]) or tb[0].startswith(ta[0]))


def _as_name(raw: str) -> str:
    raw = _HONORIFIC.sub("", raw.strip())
    return raw.title() if raw.isupper() or raw.islower() else raw


def is_individual(applicant: str, form: dict[str, Any]) -> bool:
    """The applicant is a private person, not a company."""
    if _ORG_WORD.search(applicant):
        return False
    if _same_person(applicant, form.get("contact_name")) or _same_person(applicant, form.get("attention")):
        return True
    return (form.get("applicant_type") or "").lower() == "individual"


def filing_people(form: dict[str, Any], domain: str | None) -> list[Person]:
    """The applicant's own person on the form, if the form shows one.

    Two people can appear: "Attention" on the mailing address and the
    contact "who can best handle inquiries". The second is often outside
    counsel or a frequency consultant, so nobody is attached on a title
    alone. A person is kept when the mailing address is at the company's own
    domain and is their mailbox, or, with a personal mailbox or none, when
    Attention and the contact are the same person. The title is used only
    when it belongs to that person.
    """
    attention, contact, title = form.get("attention"), form.get("contact_name"), form.get("contact_title")
    edom = form.get("email_domain")
    same = _same_person(attention, contact)
    att_is_name = (bool(attention) and 2 <= len(_person_tokens(attention)) <= 4
                   and not re.search(r"\d", attention) and not _NOT_A_PERSON.search(attention))
    if not att_is_name:
        return []
    if domain:
        if not form.get("attention_owns_email"):
            return []
    elif not same or (edom and edom not in _FREEMAIL):
        return []
    role = title if same and title and not _HONORIFIC.match(title + " ") else None
    if role and _AGENT_TITLE.search(role):
        return []
    return [Person(name=_as_name(attention), role=role)]


def build_signal(info: dict[str, Any], form: dict[str, Any], experiment_type: str | None,
                 filings_seen: int) -> Signal | None:
    """One application and its form -> one Signal, or None when it is not for us."""
    applicant = info["applicant"].strip()
    edom = form.get("email_domain")
    if is_stoplisted(applicant) or _stop_domain(edom):
        return None
    sta = info["type"] == "ST"
    granted = info["status"].lower().startswith("grant")
    when = info["status_date"] if granted else info["received"]
    if when is None:
        return None
    text = build_text(info, form, experiment_type)
    fit = classify(text)["fit"]
    on_thesis = experiment_type in THESIS_TYPES or fit >= 0.3
    # Private individuals are left out: a hobbyist's licence is not a
    # company, and their station is usually their home.
    if is_individual(applicant, form):
        return None
    # An STA states its purpose, so it is held to the thesis. A new-licence
    # form carries no description of the experiment at all: it is emitted
    # anyway and the join with other sources supplies the fit, except for
    # GPS re-radiators, which are noise without it.
    if not on_thesis and (sta or _gps_only(form) or _TELECOM_NAME.search(applicant)):
        return None
    # Event and broadcast production, in the applicant's own words, unless
    # the FCC itself has filed the grant under a thesis category.
    said = f"{form.get('purpose') or ''} {form.get('sta_reason') or ''}"
    if experiment_type not in THESIS_TYPES and _PRODUCTION_TEXT.search(said):
        return None

    domain = company_domain(edom, applicant)
    people = filing_people(form, domain)
    title_ = people[0].role if people else None

    units = _units(form)
    days = _days_to_grant(info)
    st0 = next((s for s in form["stations"] if s["lat"] is not None and s["lon"] is not None), None)
    lows = [f["low_mhz"] for f in form["frequencies"]]
    highs = [f["high_mhz"] for f in form["frequencies"]]
    places = {p for p in (_place(s) for s in form["stations"]) if p}
    metrics: dict[str, Any] = {
        "els_file_number": info["file_num"],
        "els_status": info["status"],
        "els_received": iso(info["received"]),
        "els_experiment_type": experiment_type,
        "els_days_to_grant": days,
        "els_unit_count": units or None,
        "els_experimental_equipment": int(any(e["experimental"] for e in form["equipment"])) if form["equipment"] else None,
        "els_gov_contract": int(form["gov_contract"]) if form.get("gov_contract") is not None else None,
        "els_duration_months": form.get("duration_months"),
        "els_station_count": len(form["stations"]) or None,  # rows on the form
        "els_site_count": len(places) or None,  # distinct named places among them
        "els_station_lat": st0["lat"] if st0 else None,
        "els_station_lon": st0["lon"] if st0 else None,
        "els_freq_min_mhz": min(lows) if lows else None,
        "els_freq_max_mhz": max(highs) if highs else None,
        "els_contact_title": title_,
        "els_filings_seen": filings_seen,
        "els_operation_start": iso(form.get("op_start")),
        "els_operation_end": iso(form.get("op_end")),
        "els_callsign": info.get("callsign"),
    }
    metrics = {k: v for k, v in metrics.items() if v is not None}

    if days is not None:
        value, unit = float(days), "days to grant"
    elif units:
        value, unit = float(units), "transmitters"
    else:
        value, unit = None, None

    city = form.get("mail_city")
    if city and (city.isupper() or city.islower()):
        city = city.title()
    location = ", ".join(filter(None, [city, form.get("mail_state")])) or None
    name, aliases = split_dba(applicant)
    return Signal(
        source=SLUG, family=FAMILY, kind="fcc_sta" if sta else ("fcc_experimental_granted" if granted else "fcc_experimental_pending"),
        entity=EntityHint(name=name, domain=domain, aliases=aliases, location=location),
        title=build_title(info, form, experiment_type),
        occurred_at=iso(when),
        url=INFO_URL.format(info["file_num"]),
        value=value, unit=unit,
        strength=strength_of(info, form, experiment_type, filings_seen, fit, title_),
        metrics=metrics,
        people=people,
        text=text,
    )


# --------------------------------------------------------------------------
# Collection.
# --------------------------------------------------------------------------

def _info_ttl(page: str) -> float:
    info = parse_application_info(page)
    if info is None:
        return MISSING_TTL
    return FINAL_TTL if _FINAL_STATUS.search(info["status"]) else PENDING_TTL


# What the site's own pages say: the status report, the answer for a file
# number that does not exist ("... is not a valid confirmation number"),
# and the two print views. Anything else is an outage or a block page.
_INFO_MARKS = ("OET ELS Application Detail Report", "OET Validation Error Page", "offTblBdy")
_FORM_MARKS = ("FCC FORM 442", "APPLICATION FOR SPECIAL TEMPORARY AUTHORITY")


def _info(file_num: str) -> dict[str, Any] | None:
    return parse_application_info(_fetch(INFO_URL.format(file_num), ttl=_info_ttl, expect=_INFO_MARKS))


def _form(info: dict[str, Any]) -> dict[str, Any] | None:
    if not info.get("form_url"):
        return None
    final = bool(_FINAL_STATUS.search(info["status"]))
    return parse_form(_fetch(info["form_url"], ttl=FINAL_TTL if final else PENDING_TTL, expect=_FORM_MARKS))


def _exists(kind: str, year: int, n: int) -> bool:
    return _info(f"{n:04d}-EX-{kind}-{year}") is not None


def find_frontier(kind: str, year: int, hint: int,
                  exists: Callable[[str, int, int], bool] = _exists) -> int:
    """Highest file number that exists for this type and year, 0 if none.

    Gallops forward from `hint` (the newest number seen in the grants feed),
    bisects to the edge, then confirms with MISS_STOP consecutive absences so
    a small gap in the sequence is not mistaken for the end.
    """
    def there(n: int) -> bool:
        return 1 <= n <= 9999 and exists(kind, year, n)

    lo = hint if there(hint) else (1 if there(1) else 0)
    if lo == 0:
        return 0
    step = 16
    while there(lo + step):
        lo, step = lo + step, step * 2
    hi = lo + step
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if there(mid):
            lo = mid
        else:
            hi = mid
    misses, n = 0, lo
    while misses < MISS_STOP and n < 9999:
        n += 1
        if there(n):
            lo, misses = n, 0
        else:
            misses += 1
    return lo


def _feed_on_thesis(applicant: str, experiment_type: str | None) -> bool:
    """Worth a detail fetch, judged from the feed row alone.

    A name is all there is to go on here, so one strong thesis word in it
    ("Orbital", "Hydrogen", "Propulsion") is enough to go and read the
    form, though the classifier holds a lone word of that kind just under
    its gate. The form then decides: build_signal applies the full gate to
    what the applicant wrote.
    """
    if experiment_type in THESIS_TYPES:
        return True
    return classify(f"{applicant}. {experiment_type or ''}")["fit"] >= LONE_TERM_FIT


def _worth_reading(grant: dict[str, Any]) -> bool:
    """Whether a granted feed row gets its form fetched.

    An STA form says what the experiment is, in the applicant's words, and
    a name such as EnduroSat or Hermeus says nothing: so every granted STA
    is read, bar the event-production categories, and build_signal holds
    what it says to the thesis. A new-licence form has no such answer, so a
    grant of one is read only when the feed row itself is on the thesis.
    """
    if _feed_on_thesis(grant["applicant"], grant["experiment_type"]):
        return True
    return grant["type"] == "ST" and grant["experiment_type"] not in UNREAD_TYPES


def _walk(ctx: Context, kind: str, year: int, hint: int, cap: int) -> Iterator[dict[str, Any]]:
    """Status rows for the newest `cap` file numbers of one type, newest first."""
    try:
        top = find_frontier(kind, year, hint)
    except Exception as e:
        ctx.warn(f"fcc_els: frontier search failed for {kind} {year}: {e}")
        return
    if top == 0:
        ctx.log(f"fcc_els: no {kind} file numbers for {year} yet")
        return
    ctx.log(f"fcc_els: newest {kind} file number for {year} is {top:04d}, walking back {min(cap, top)}")
    failures = 0
    for n in range(top, max(top - cap, 0), -1):
        file_num = f"{n:04d}-EX-{kind}-{year}"
        try:
            info = _info(file_num)
        except Exception as e:
            ctx.warn(f"fcc_els: {file_num}: {e}")
            failures += 1
            if failures >= 5:
                ctx.warn(f"fcc_els: stopping the {kind} walk after {failures} failures")
                return
            continue
        if info is not None and info["applicant"]:
            yield info


def collect(ctx: Context) -> Iterable[Signal]:
    if shutil.which("curl") is None:
        ctx.warn("fcc_els: curl is not installed and apps.fcc.gov refuses urllib; nothing collected")
        return
    try:
        grants = parse_rss(_fetch(RSS_URL, ttl=RSS_TTL, expect=("<rss",)))
    except Exception as e:  # the walk still works without the feed
        ctx.warn(f"fcc_els: grants feed failed: {e}")
        grants = []
    if not grants:
        ctx.warn("fcc_els: no grants parsed from the feed; granted applications will be missing from this run")
    ctx.log(f"fcc_els: {len(grants)} grants in the feed")
    in_feed = {g["file_num"] for g in grants}

    # Every filing seen this run, per applicant. It is the only novelty
    # measure available while the site's search by applicant name is down.
    # Keyed on the loose name: "Fortem Technologies, Inc." and "Fortem" are
    # one filer, and counting them apart would pay the first-seen premium twice.
    seen: dict[str, set[str]] = {}
    for g in grants:
        seen.setdefault(loose_name(g["applicant"]), set()).add(g["file_num"])

    # 1. Walk file numbers back from the newest: applications still pending.
    pending: list[tuple[dict[str, Any], str | None]] = []
    cap = min(WALK_CAP, max(20, ctx.limit * 2)) if ctx.limit else WALK_CAP
    years = [ctx.today.year] + ([ctx.today.year - 1] if ctx.today.month == 1 else [])
    for year in years:
        for kind in WALK_TYPES:
            hint = max((g["seq"] for g in grants if g["type"] == kind and g["year"] == year), default=0)
            for info in _walk(ctx, kind, year, hint, cap):
                seen.setdefault(loose_name(info["applicant"]), set()).add(info["file_num"])
                if info["file_num"] in in_feed:
                    continue  # granted and in the feed: taken below, with its experiment type
                granted = info["status"].lower().startswith("grant")
                if not (granted or _PENDING_STATUS.match(info["status"])):
                    continue  # drafts ("Incomplete Electronic Filing") and dismissals
                if is_stoplisted(info["applicant"]):
                    continue
                pending.append((info, None))

    # 2. Grants in the feed, new licences and STAs only, that are worth reading.
    granted_rows: list[tuple[dict[str, Any], str | None]] = []
    for g in grants:
        if g["type"] not in WALK_TYPES or g["granted"] < ctx.since:
            continue
        if is_stoplisted(g["applicant"]) or not _worth_reading(g):
            continue
        granted_rows.append((g, g["experiment_type"]))

    if ctx.limit:  # a probe should show both halves
        queue = [x for pair in zip(pending, granted_rows) for x in pair]
        queue += pending[len(granted_rows):] + granted_rows[len(pending):]
    else:
        queue = pending + granted_rows
    ctx.log(f"fcc_els: {len(pending)} open applications and {len(granted_rows)} grants to read")

    entities: set[str] = set()
    taken: dict[tuple[str, str], int] = {}
    for row, experiment_type in queue:
        if ctx.limit and len(entities) >= ctx.limit:
            break
        key = (loose_name(row["applicant"]), row["type"] + ("G" if "granted" in row else "P"))
        if taken.get(key, 0) >= MAX_PER_APPLICANT:
            continue
        try:
            info = _info(row["file_num"]) if "granted" in row else row
            if info is None:
                ctx.warn(f"fcc_els: {row['file_num']} is in the feed but has no status page")
                continue
            form = _form(info)
            if form is None:
                continue
            if form.get("applicant") and loose_name(form["applicant"]) != loose_name(info["applicant"]):
                ctx.warn(f"fcc_els: {info['file_num']}: form names {form['applicant']!r}, "
                         f"status page names {info['applicant']!r}; skipped")
                continue
            filings = len(seen.get(loose_name(info["applicant"]), ())) or 1
            sig = build_signal(info, form, experiment_type, filings)
            if sig is None or sig.occurred_at[:10] < iso(ctx.since):
                continue
            # The run date is UTC and the site's clock is Eastern, which is
            # behind it, so a genuine date is never after today. One day of
            # slack is kept for a run pinned to a local date; anything later
            # is a typing error.
            if sig.occurred_at[:10] > iso(ctx.today + timedelta(days=1)):
                ctx.warn(f"fcc_els: {info['file_num']}: dated {sig.occurred_at}, in the future; skipped")
                continue
            taken[key] = taken.get(key, 0) + 1
            entities.add(sig.entity.domain or sig.entity.name)
            yield sig
        except Exception as e:
            ctx.warn(f"fcc_els: {row.get('file_num')}: {type(e).__name__}: {e}")
