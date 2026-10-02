"""Infrastructure footprint of a company's own domain: registry record, DNS, certificates.

The source is the public plumbing a company has to stand up before it can
operate: the registry record for its domain (RDAP), its mail and verification
DNS records (Google DNS over HTTPS) and the TLS certificates issued for its
hostnames (Certificate Transparency, read through Cert Spotter). It is early
because none of it waits for traffic, press or a rank list: a company moves
its mail to Microsoft's government cloud, verifies a CRM, or gets a
certificate for pay., docs. or a factory hostname as part of doing the work,
and companies too small for any traffic list still leave all three trails.

What each part can and cannot say
---------------------------------
RDAP gives the registration date of the domain as the registry holds it today.
A dropped and re-registered domain shows the re-registration date, a bought
premium domain shows a date older than the company, so the title only ever
says "was registered on". domain_registered is emitted only when that date is
inside the lookback window (ctx.since); an older registration is not an event
of this window and only travels as metrics.domain_age_days on other signals.

DNS has no dates. A TXT verification token shows a tool was verified on the
domain at some point, not that it is in use today. Those signals carry
occurred_at = the day of the scan and metrics.observed_only = true, their
strength stays at 0.45 or below, and they are not emitted at all when the run
is dated to a day other than the real one (--today), because a record read
now says nothing about that day. The same goes for the certificate signals:
the listing shows what is unexpired now, not what was on the run's date.
The store keeps an observed_only signal as one row per entity and kind that
each run refreshes. A domain gives at most one dns_govcloud and one
dns_tooling, so neither needs metrics.subject to tell rows apart. The
certificate inventory (ct_subdomains) is kept the same way through
metrics.rolling: its date moves forward as older certificates expire, so a
row per date would stack one inventory beside the next.

The titles feed the thesis classifier along with everything else written
about the entity. Mail in a government cloud says who the company sells to,
not what it builds, so those titles name the cloud ("GCC High or DoD", an
agency name the thesis weighs as context) and add no sector word of their
own. Hostnames are the company's own words and are shown as issued.

Cert Spotter returns only certificates that have not expired, and allows
about 10 requests an hour per IP without a key. So the collector looks up
only the top CT_BUDGET entities by prelim score, with at most CT_BUDGET
network requests a run, and it cannot prove a hostname is new: a 90-day
certificate that is renewed looks the same as a first issuance once the older
one expires. Two kinds come out of it, one per entity at most:

  ct_new_subdomains  at least one notable hostname whose every unexpired
                     certificate is younger than the overlap a normal renewal
                     leaves (30 days, or a third of the certificate's life if
                     that is shorter, less two days of margin). A name on a renewal cycle would still
                     show its previous certificate, so this is the nearest the
                     source gets to "first issued". The title says exactly
                     that ("no unexpired certificate older than ...") and
                     never "new" or "first"; occurred_at is the newest such
                     certificate's not_before.
  ct_subdomains      notable hostnames with certificates issued in the window
                     but none that young. Most of these are renewals, so it is
                     an inventory at routine strength (0.15 to 0.3). It is
                     dated to the day the last of the counted names got its
                     earliest unexpired certificate, and is emitted only when
                     at least one name says something about the business.
                     That date moves forward as certificates expire, so the
                     signal carries metrics.rolling and the store keeps one
                     row per entity that each run refreshes.

Every signal is about the domain, so a wrong domain would put another
organisation's records under the entity's name. A domain is only probed when
it is a registrable name of its own, is not a shared host, a job board, a link
shortener, an investor or a university, and carries the entity's name (or its
GitHub login) in some form.

Certificate names often contain people's names (per-engineer dev clusters).
Only labels built entirely from a fixed vocabulary of functional words (app,
api, docs, pay, f3, cnc-cell1 ...) are ever shown or stored. Everything else
is counted and dropped.
"""

from __future__ import annotations

import html
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Iterable

from .. import http
from ..models import EntityHint, Signal
from .base import Context, clean_domain, iso, parse_date, squash

SLUG = "domain_footprint"
FAMILY = "traffic"
STAGE = "enrich"
DESCRIPTION = "Domain age, gov-cloud and tooling fingerprints in DNS, hostnames on recently issued certificates"

# Cert Spotter allows about 10 requests an hour per IP without a key. Only the
# top CT_BUDGET entities by prelim score are looked up, and one run makes at
# most CT_BUDGET network requests to it. A response already in the HTTP cache
# is free, so a second run the same day costs nothing and returns the same.
CT_BUDGET = 8
# Cert Spotter returns 100 issuances a page, oldest first. A company with more
# unexpired certificates than this many pages is reported as cut off.
CT_PAGE_SIZE = 100
CT_MAX_PAGES = 3
# Cert Spotter lookups that fail back to back before the run gives up on it.
# One failed lookup can cost two 75-second timeouts, so this bounds the wait.
CT_MAX_FAILURES = 2

# A domain registered inside the lookback window: solid, never more. It costs
# ten dollars, and a re-registered or renamed domain looks the same. For an
# open-source project with no company behind it yet it is the low end of solid.
REGISTERED_STRENGTH = 0.45
REGISTERED_STRENGTH_PROJECT = 0.35
# Government-cloud mail on a domain younger than this reads as a young
# company preparing for defense work rather than an incumbent's normal setup.
YOUNG_DOMAIN_DAYS = 5 * 365

RDAP_BOOTSTRAP = "https://data.iana.org/rdap/dns.json"
# ccTLDs missing from the IANA bootstrap whose registries do answer RDAP.
RDAP_OVERRIDES = {
    "io": "https://rdap.identitydigital.services/rdap/",
    "co": "https://rdap.registry.co/co/",
    "us": "https://rdap.nic.us/",
}
DOH = "https://dns.google/resolve"
CERTSPOTTER = "https://api.certspotter.com/v1/issuances"

RDAP_TTL = 3 * 24 * 3600
DNS_TTL = 20 * 3600
CT_TTL = 20 * 3600
WORKERS = 6

# An unexpired certificate this young with no older unexpired one for the
# same name: a name on a normal renewal cycle would still show its previous
# certificate, so this is the nearest thing to "first issued" the source has.
# Renewal normally happens with a third of the lifetime left (day 60 of 90),
# so for a short-lived certificate the bar is a third of its own lifetime.
# The margin covers a renewal that ran a day or two early, whose predecessor
# expires just before the replacement turns 30 days old.
CT_FRESH_DAYS = 30
CT_RENEWAL_MARGIN_DAYS = 2

# Hosts that many unrelated parties share. Their DNS says nothing about the
# entity that happens to list them as a website. base.clean_domain already
# refuses code hosts, social sites, the common app platforms, job boards and
# .edu/.gov/.mil names; these are the ones it lets through.
_SHARED_APEX = {
    # App, site and storefront platforms.
    "gitbook.io", "framer.app", "framer.ai", "repl.co", "azurewebsites.net", "cloudfront.net",
    "amazonaws.com", "weebly.com", "godaddysites.com", "myshopify.com", "blogspot.com",
    "tumblr.com", "gumroad.com", "appspot.com", "glitch.me", "surge.sh", "bitbucket.io",
    "super.site", "wix.com", "pitchbook.com", "devpost.com", "patreon.com", "shopify.com",
    "etsy.com", "tindie.com", "crowdsupply.com",
    # Big vendors and mail providers.
    "microsoft.com", "nvidia.com", "hotmail.com", "icloud.com", "protonmail.com", "qq.com",
    "163.com",
    # Job boards, schedulers, forms and file shares.
    "jobvite.com", "indeed.com", "glassdoor.com", "builtin.com", "cal.com", "tally.so",
    "jotform.com", "docsend.com", "box.com", "luma.com", "lu.ma", "eventbrite.com",
    "meetup.com",
    # Link shorteners and social hosts.
    "tinyurl.com", "lnkd.in", "goo.gl", "forms.gle", "ow.ly", "buff.ly", "rebrand.ly",
    "is.gd", "cutt.ly", "shorturl.at", "tiny.cc", "rb.gy", "t.ly", "threads.net", "twitch.tv",
    "mastodon.social",
    # Accelerators and investors: a portfolio page is not the company's domain.
    "techstars.com", "joinef.com", "antler.co", "500.co", "sequoiacap.com", "foundersfund.com",
    "generalcatalyst.com", "luxcapital.com", "khoslaventures.com", "8vc.com", "nfx.com",
    "pioneer.app", "alchemistaccelerator.com", "plugandplaytechcenter.com", "masschallenge.org",
    "starburst.aero", "cyclotronroad.org", "activate.org", "theengine.com", "engine.xyz",
    "creativedestructionlab.com",
    # Code, package and paper hosts.
    "sourceforge.net", "kaggle.com", "paperswithcode.com", "openreview.net", "researchgate.net",
    "semanticscholar.org", "ssrn.com", "zenodo.org", "biorxiv.org", "doi.org", "nature.com",
    "ieee.org", "acm.org", "springer.com", "sciencedirect.com", "mdpi.com", "wiley.com",
    "europa.eu",
    # Research institutions outside .edu and .ac.*: a lab page there is not the lab's domain.
    "ethz.ch", "epfl.ch", "cern.ch", "tum.de", "mpg.de", "dlr.de", "fraunhofer.de",
    "rwth-aachen.de", "inria.fr", "cnrs.fr", "cea.fr", "tudelft.nl", "utwente.nl", "kth.se",
    "dtu.dk", "kuleuven.be", "utoronto.ca", "ubc.ca", "uwaterloo.ca", "mcgill.ca",
    "technion.ac.il", "csiro.au",
}
# Treaty organisations. clean_domain already refuses .edu, .gov and .mil.
_NON_COMPANY_SUFFIX = (".int",)
_CC_SECOND_LEVEL = {"co", "com", "org", "net", "ac", "gov", "go", "mil", "edu", "or", "ne"}
# University names outside .edu and .ac.*: uni-freiburg.de, tu-berlin.de, univ-lyon1.fr.
_UNIVERSITY_LABEL = re.compile(r"^(?:uni|univ|tu)-|universi|polytechni")
# Words that say nothing about which organisation a name belongs to.
_NAME_STOPWORDS = frozenset({
    "the", "inc", "llc", "ltd", "corp", "company", "labs", "lab", "technologies", "technology",
    "tech", "systems", "group", "holdings", "and", "for", "com", "org", "net", "app", "www",
})


# ---------------------------------------------------------------------------
# Domains and RDAP
# ---------------------------------------------------------------------------

def registrable(raw: str | None) -> str | None:
    """The entity's domain if it is itself a registrable name we may probe.

    Returns None for blanks, subdomains (the parent may be a shared platform
    or a university, and its DNS would be wrongly credited to the entity),
    shared hosts, and institutions. clean_domain refuses the hosts every
    collector has to refuse; the checks after it are the ones only this
    collector needs, because it reads the domain's own DNS.
    """
    host = clean_domain(raw)
    if not host or not re.fullmatch(r"[a-z0-9.-]+", host):
        return None
    labels = host.split(".")
    if any(not lab for lab in labels):
        return None
    cc_sld = len(labels) == 3 and len(labels[-1]) == 2 and labels[-2] in _CC_SECOND_LEVEL
    if len(labels) != 2 and not cc_sld:
        return None
    if host in _SHARED_APEX or host.endswith(_NON_COMPANY_SUFFIX):
        return None
    if cc_sld and labels[-2] in {"ac", "gov", "go", "mil", "edu"}:
        return None
    if _UNIVERSITY_LABEL.search(labels[0]):
        return None
    return host


def clean_name(raw: Any) -> str:
    """An entity name as one line of plain text: entities unescaped, spaces collapsed."""
    if not isinstance(raw, str):
        return ""
    return " ".join(html.unescape(raw).split())


def github_login(raw: Any) -> str | None:
    """A GitHub org or user login, never a URL. None when it is not one."""
    if not isinstance(raw, str):
        return None
    s = re.sub(r"^(?:https?://)?(?:www\.)?github\.com/", "", raw.strip(), flags=re.I)
    s = s.lstrip("@").strip("/").split("/")[0]
    return s if re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", s) else None


def carries_name(name: str, domain: str, github: str | None = None) -> bool:
    """Does the domain plausibly belong to this entity rather than to a host it linked?

    True when the domain's first label appears in the name (or the GitHub
    login), a distinctive word of the name appears in the domain, or the
    name's initials are the first label. Deliberately loose, the same test
    tranco_rank uses: it only has to tell "Aalo Atomics / aalo.com" from
    "Acme Robotics / bostondynamics.com".
    """
    host = re.sub(r"[^a-z0-9]", "", domain.lower())
    label = re.sub(r"[^a-z0-9]", "", domain.lower().split(".")[0])
    words = re.findall(r"[a-z0-9]+", name.lower())
    squashed = "".join(words)
    if not label or not squashed:
        return False
    if label in squashed or squashed in host:
        return True
    if any(len(w) >= 3 and w not in _NAME_STOPWORDS and w in host for w in words):
        return True
    initials = {"".join(w[0] for w in words), "".join(w[0] for w in words if w not in _NAME_STOPWORDS)}
    if len(label) >= 2 and label in initials:
        return True
    login = re.sub(r"[^a-z0-9]", "", (github or "").lower())
    return len(login) >= 3 and (label in login or login in host)


def parse_bootstrap(doc: dict) -> dict[str, str]:
    """IANA RDAP bootstrap file -> {tld: base url}."""
    out: dict[str, str] = {}
    for entry in doc.get("services") or []:
        if len(entry) < 2 or not entry[1]:
            continue
        https = [u for u in entry[1] if str(u).startswith("https://")]
        base = (https or entry[1])[0]
        for tld in entry[0]:
            out[str(tld).lower()] = base
    return out


def rdap_url(domain: str, bootstrap: dict[str, str]) -> str | None:
    """Direct registry RDAP URL for a domain, or None when no registry serves one."""
    tld = domain.rsplit(".", 1)[-1]
    base = RDAP_OVERRIDES.get(tld) or bootstrap.get(tld)
    if not base:
        return None
    return f"{base.rstrip('/')}/domain/{domain}"


def parse_rdap(doc: dict) -> dict:
    """Pull the dated facts out of an RDAP domain response.

    Returns {"registered": date|None, "expires": date|None, "registrar": str|None,
             "nameservers": [lower-case hosts]}. Registrant identity is redacted
    everywhere, so nothing about people is read.
    """
    events: dict[str, str] = {}
    for ev in doc.get("events") or []:
        if isinstance(ev, dict) and ev.get("eventAction") and ev.get("eventDate"):
            events.setdefault(str(ev["eventAction"]).lower(), str(ev["eventDate"]))
    registrar = None
    for ent in doc.get("entities") or []:
        if not isinstance(ent, dict) or "registrar" not in (ent.get("roles") or []):
            continue
        card = ent.get("vcardArray")
        if isinstance(card, list) and len(card) > 1:
            for item in card[1]:
                if isinstance(item, list) and len(item) > 3 and item[0] == "fn" and item[3]:
                    registrar = str(item[3]).strip()
                    break
        if registrar:
            break
    nameservers = []
    for ns in doc.get("nameservers") or []:
        name = str((ns or {}).get("ldhName") or "").strip().rstrip(".").lower()
        if name and name not in nameservers:
            nameservers.append(name)
    return {
        "registered": parse_date((events.get("registration") or "")[:10]),
        "expires": parse_date((events.get("expiration") or "")[:10]),
        "registrar": registrar,
        "nameservers": nameservers,
    }


def trusted_registration(domain: str, registered: date | None) -> date | None:
    """Drop registration dates the registry itself got wrong.

    The .ai registry stamps 2017-12-15 on every name that predates its
    migration (figure.ai, shield.ai), so that date is a floor, not a fact.
    """
    if registered is None:
        return None
    if domain.endswith(".ai") and registered == date(2017, 12, 15):
        return None
    return registered


# ---------------------------------------------------------------------------
# DNS
# ---------------------------------------------------------------------------

_RR = {"MX": 15, "TXT": 16, "NS": 2}


def doh_url(name: str, rtype: str) -> str:
    return f"{DOH}?name={name}&type={rtype}"


def doh_answers(doc: dict, rtype: str) -> list[str]:
    """Record data for one type from a dns.google JSON answer, CNAMEs dropped."""
    if not isinstance(doc, dict) or doc.get("Status") != 0:
        return []
    want = _RR[rtype]
    out = []
    for ans in doc.get("Answer") or []:
        if ans.get("type") != want:
            continue
        data = str(ans.get("data") or "").strip()
        if rtype == "TXT":
            # Long TXT records can arrive as several quoted chunks.
            data = data.replace('" "', "").replace('""', "").strip('"').strip()
        if data:
            out.append(data)
    return out


def mx_hosts(records: list[str]) -> list[str]:
    hosts = []
    for rec in records:
        host = rec.split()[-1].rstrip(".").lower() if rec.split() else ""
        if host and host not in hosts:
            hosts.append(host)
    return hosts


def ns_hosts(records: list[str]) -> list[str]:
    out = []
    for rec in records:
        host = rec.strip().rstrip(".").lower()
        if host and host not in out:
            out.append(host)
    return out


def mail_provider(hosts: list[str]) -> str | None:
    """Coarse mail platform from MX hosts. None when there is no MX at all."""
    if not hosts:
        return None
    found = []
    for h in hosts:
        if h.endswith(".mail.protection.office365.us"):
            tag = "microsoft_365_gcc_high_or_dod"
        elif h.endswith(".mail.protection.outlook.com"):
            tag = "microsoft_365"
        elif h.endswith(("google.com", "googlemail.com")):
            tag = "google_workspace"
        elif h.endswith("pphosted.com"):
            tag = "proofpoint"
        elif h.endswith("mimecast.com"):
            tag = "mimecast"
        else:
            tag = "other"
        if tag not in found:
            found.append(tag)
    order = ["microsoft_365_gcc_high_or_dod", "microsoft_365", "google_workspace",
             "proofpoint", "mimecast", "other"]
    return ",".join(sorted(found, key=order.index))


def spf_includes(txt: list[str]) -> list[str]:
    out = []
    for rec in txt:
        if not rec.lower().startswith("v=spf1"):
            continue
        for m in re.findall(r"(?:include:|redirect=)(\S+)", rec, flags=re.I):
            host = m.strip().rstrip(".").lower()
            if host and host not in out:
                out.append(host)
    return out


_AWS_GOV_NS = re.compile(r"(^|\.)awsdns-us-gov-\d+\.")


def gov_cloud(mx: list[str], ns: list[str], txt: list[str]) -> dict:
    """Government-cloud tells that the records literally show.

    mx_gcc_high: an MX host under mail.protection.office365.us (Microsoft 365
    GCC High or DoD). spf_gcc_high: SPF includes spf.protection.office365.us.
    ns_azure_gov / ns_aws_gov: nameservers on azuregov-dns.us / awsdns-us-gov.
    """
    spf = spf_includes(txt)
    return {
        "mx_gcc_high": [h for h in mx if h.endswith(".mail.protection.office365.us")],
        "spf_gcc_high": [h for h in spf if h.endswith("protection.office365.us")],
        "ns_azure_gov": [h for h in ns if h.endswith(".azuregov-dns.us")],
        "ns_aws_gov": [h for h in ns if _AWS_GOV_NS.search(h)],
    }


def looks_gov_ns(hosts: list[str]) -> bool:
    return any(h.endswith(".azuregov-dns.us") or _AWS_GOV_NS.search(h) for h in hosts)


# Tool fingerprints. Every pattern here was seen in a live TXT answer for a
# hard-tech company domain on 2026-10-01; none is taken from memory. A TXT
# prefix matches the start of a record, an SPF suffix matches the end of an
# include/redirect target. Ashby and Lever left no TXT trace in 78 domains,
# so they are not here.
# (tool, group, weight, txt prefixes, spf suffixes)
TOOLS: list[tuple[str, str, float, tuple[str, ...], tuple[str, ...]]] = [
    ("HubSpot", "go_to_market", 1.0,
     ("hubspot-domain-verification=", "hubspot-developer-verification="), ("hubspotemail.net",)),
    ("Salesforce", "go_to_market", 1.0, (), ("_spf.salesforce.com",)),
    ("Stripe", "go_to_market", 1.0, ("stripe-verification=",), ()),
    ("Shopify", "go_to_market", 1.0, ("shopify-verification-code=",), ()),
    ("Zendesk", "go_to_market", 0.8, (), ("mail.zendesk.com",)),
    ("Marketo", "go_to_market", 0.8, (), ("mktomail.com",)),
    ("Mailchimp", "go_to_market", 0.6, (), ("servers.mcsv.net",)),
    ("Klaviyo", "go_to_market", 0.6, ("klaviyo-site-verification=",), ()),
    ("Greenhouse", "hiring", 1.0, (), ("mg-spf.greenhouse.io",)),
    ("Autodesk", "hardware_engineering", 0.8, ("autodesk-domain-verification=",), ()),
    ("Smartsheet Gov", "hardware_engineering", 0.8, ("smartsheet-gov-site-validation=",), ()),
    ("Smartsheet", "hardware_engineering", 0.6, ("smartsheet-site-validation=",), ()),
    ("Bluebeam", "hardware_engineering", 0.6, ("bluebeam-verification=",), ()),
    ("NetSuite", "operations", 0.8, (), ("sent-via.netsuite.com",)),
    ("Rippling", "operations", 0.5, ("rippling-domain-verification=",), ()),
    ("DocuSign", "operations", 0.4, ("docusign=",), ()),
    ("Atlassian", "collaboration", 0.3,
     ("atlassian-domain-verification=", "atlassian-sending-domain-verification="), ()),
]


SIGNAL_GROUPS = {"go_to_market", "hiring", "hardware_engineering"}


def detect_tools(txt: list[str]) -> list[dict]:
    """Catalog tools named by the TXT and SPF records, in catalog order."""
    lowered = [rec.strip().lower() for rec in txt]
    spf = spf_includes(txt)
    found = []
    for tool, group, weight, prefixes, suffixes in TOOLS:
        via = None
        if any(rec.startswith(p) for rec in lowered for p in prefixes):
            via = "txt"
        elif any(h == s or h.endswith("." + s) for h in spf for s in suffixes):
            via = "spf"
        if via:
            found.append({"tool": tool, "group": group, "weight": weight, "via": via})
    return found


# ---------------------------------------------------------------------------
# Certificate Transparency
# ---------------------------------------------------------------------------

# Functional vocabulary. A hostname label is shown only when every
# hyphen-separated token of it is in here, so a person's name can never leak
# into a title or the stored metrics.
_VOCAB: dict[str, tuple[str, ...]] = {
    "site": (
        "factory", "plant", "fab", "foundry", "site", "facility", "hq", "lab", "labs", "range",
        "hangar", "shipyard", "warehouse", "depot", "line", "cell", "cnc", "cmm", "mill", "lathe",
        "weld", "assembly", "teststand", "launchpad", "groundstation", "reactor", "mfg",
        "manufacturing", "scada", "plc", "hmi", "mes", "office", "datacenter",
        # US states and hard-tech sites that are not also common given names.
        "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut",
        "delaware", "florida", "hawaii", "idaho", "illinois", "iowa", "kansas", "kentucky",
        "louisiana", "maine", "maryland", "massachusetts", "michigan", "minnesota",
        "mississippi", "missouri", "nebraska", "nevada", "newhampshire", "newjersey",
        "newmexico", "newyork", "northcarolina", "northdakota", "ohio", "oklahoma", "oregon",
        "pennsylvania", "rhodeisland", "southcarolina", "southdakota", "tennessee", "texas",
        "utah", "vermont", "westvirginia", "wisconsin", "wyoming",
        "elsegundo", "longbeach", "costamesa", "alameda", "mojave", "huntsville",
        "capecanaveral", "seattle", "boston", "detroit", "pittsburgh", "tucson", "albuquerque",
        "oakridge", "losalamos", "idahofalls", "sandiego", "losangeles", "sanfrancisco",
        "sanjose", "chicago", "atlanta", "elpaso", "tulsa", "livermore",
    ),
    "government": (
        "gov", "usgov", "usg", "govcloud", "fedramp", "fedstart", "cui", "itar",
        "il2", "il4", "il5", "il6",
    ),
    "commerce": (
        "pay", "payments", "billing", "checkout", "shop", "store", "order", "orders", "buy",
        "quote", "quotes", "invoice", "invoices", "configurator", "sales", "pricing", "parts",
    ),
    "product": (
        "app", "apps", "console", "dashboard", "portal", "platform", "cloud", "web", "hub",
        "studio", "fleet", "control", "ops", "mission", "telemetry", "tracking", "customer",
        "customers", "client", "clients", "partner", "partners", "demo", "my", "account",
        "accounts", "login", "signin", "signup", "data", "live", "viewer", "map", "maps", "sim",
        "simulator", "teleop", "robot", "robots", "device", "devices", "edge", "ota", "firmware",
        "update", "updates", "product", "products", "catalog", "table", "tables", "services",
    ),
    "developer": (
        "api", "docs", "developer", "developers", "sdk", "graphql", "grpc", "reference",
        "changelog", "openapi",
    ),
    "people": (
        "careers", "jobs", "hiring", "recruiting", "talent", "apply", "join", "people", "hr",
        "learning", "training", "onboarding",
    ),
    "reliability": (
        "status", "uptime", "monitor", "monitoring", "grafana", "metrics", "health", "sentry",
    ),
    "support": ("support", "help", "helpdesk", "service", "kb", "community", "forum", "feedback"),
    "engineering": (
        "git", "gitlab", "github", "jira", "confluence", "atlassian", "wiki", "ci", "jenkins",
        "build", "builds", "artifactory", "artifacts", "registry", "harbor", "nexus",
        "windchill", "teamcenter", "plm", "erp", "cad", "pdm", "argocd", "k8s", "kube",
        "kubernetes", "cluster", "gpu", "hpc", "compute", "ml", "ai", "mlflow", "jupyter",
        "notebook", "airflow", "db", "postgres", "redis", "kafka", "mqtt", "s3", "storage",
        "cdn", "assets", "static", "media", "images", "img", "infra", "tunnel", "tunneling",
        "webrtc", "proxy", "gateway", "gw", "lb", "ingress", "backup", "logs", "logging",
        "kibana", "elastic", "prometheus", "packages", "repo", "code", "review", "inference",
        "azure", "aws", "gcp",
    ),
    "enterprise_it": (
        "sso", "auth", "okta", "vpn", "mdm", "corp", "intranet", "internal", "mail", "email",
        "remote", "id", "identity", "idp", "files", "share", "securetransfer", "vault",
        "security", "trust", "compliance", "wifi", "it", "ns", "dns", "ntp", "smtp", "imap", "mx",
        "admin", "users", "teams", "bitwarden",
    ),
    "environment": (
        "staging", "stage", "stg", "dev", "test", "qa", "uat", "prod", "production", "sandbox",
        "preview", "beta", "alpha", "canary", "preprod",
        # Region words, so "us-gov" and "us-west-1-test" read as vocabulary.
        "us", "usa", "eu", "west", "east", "north", "south", "central",
    ),
}
# Most telling first. Also the order names are listed in a title.
CLASS_ORDER = ["site", "government", "commerce", "product", "developer", "people",
               "reliability", "support", "engineering", "enterprise_it", "environment"]
CLASS_WEIGHT = {"site": 1.0, "government": 0.9, "commerce": 0.8, "product": 0.7,
                "developer": 0.6, "people": 0.5, "reliability": 0.5, "support": 0.4,
                "engineering": 0.4, "enterprise_it": 0.3, "environment": 0.2}
# A label nested under another shown label (media.github under github) adds
# little that the parent did not already say.
NESTED_WEIGHT = 0.3
# An inventory with none of these is IT plumbing (mail, vpn, sso) and is not emitted.
CT_TELLING_CLASSES = {"site", "government", "commerce", "product", "developer"}
_TOKEN_CLASS = {tok: cls for cls in CLASS_ORDER for tok in _VOCAB[cls]}
# Tokens that take a single trailing letter as a unit suffix (cmm-cella).
_LETTERED = {"cell", "line", "fab", "plant", "site"}
_SKIP_LABELS = {"www", "*"}


def token_class(token: str) -> str | None:
    """Class of one hyphen-separated token, or None when it is not vocabulary."""
    if not token:
        return None
    if token.isdigit():
        return "number"
    if re.fullmatch(r"f\d{1,2}", token):  # f2, f3: numbered facilities
        return "site"
    if token in _TOKEN_CLASS:
        return _TOKEN_CLASS[token]
    base = re.sub(r"\d+$", "", token)
    if base in _TOKEN_CLASS:
        return _TOKEN_CLASS[base]
    if len(token) > 1 and token[:-1] in _LETTERED and token[-1].isalpha():
        return _TOKEN_CLASS[token[:-1]]
    return None


def label_class(label: str) -> str | None:
    """Class of a DNS label if all its tokens are vocabulary, else None."""
    classes = [token_class(tok) for tok in label.split("-")]
    if not classes or any(c is None for c in classes):
        return None
    real = [c for c in classes if c != "number"]
    if not real:
        return None
    return min(real, key=CLASS_ORDER.index)


def shown_label(fqdn: str, domain: str) -> tuple[str, str] | None:
    """Reduce a certificate DNS name to the part that is safe to show.

    Takes the labels under `domain` and keeps the longest run of vocabulary
    labels nearest the domain: "lavos.cmm-cell1.f3.hadrian.co" becomes
    ("cmm-cell1.f3", "site"). Returns None for the apex, www, names outside
    the domain, and names whose nearest label is not vocabulary.
    """
    name = fqdn.strip().rstrip(".").lower()
    if name == domain or not name.endswith("." + domain):
        return None
    labels = [lab for lab in name[: -len(domain) - 1].split(".") if lab not in _SKIP_LABELS]
    kept: list[str] = []
    classes: list[str] = []
    for lab in reversed(labels):
        cls = label_class(lab)
        if cls is None:
            break
        kept.insert(0, lab)
        classes.append(cls)
    if not kept:
        return None
    return ".".join(kept), min(classes, key=CLASS_ORDER.index)


def relative_names(issuance: dict, domain: str) -> set[str]:
    """Distinct hostnames under `domain` on one certificate, apex and www dropped."""
    out = set()
    for raw in issuance.get("dns_names") or []:
        name = str(raw).strip().rstrip(".").lower()
        if not name.endswith("." + domain):
            continue
        rel = name[: -len(domain) - 1]
        rel = rel[2:] if rel.startswith("*.") else rel
        if rel and rel != "www" and not rel.startswith("www."):
            out.add(rel)
        elif rel.startswith("www.") and rel[4:]:
            out.add(rel[4:])
    return out


def parse_issuances(issuances: list[dict], domain: str, since: date, today: date) -> dict:
    """Summarise the Cert Spotter issuances for a domain.

    A name counts for the window when its earliest unexpired certificate has
    not_before between `since` and `today` inclusive. Certificates dated after
    `today` (a run that crossed midnight UTC, or one dated to yesterday) are
    left for the next run rather than given a date in the future. Each issuance may carry "_page", the index of
    the response page it came from; the summary names the page a reader should
    open to see the newest of the notable certificates.

    A notable name is "fresh" when that earliest certificate is younger than
    CT_FRESH_DAYS, or a third of its own lifetime if that is shorter, less a
    two-day margin: a name renewed on schedule would still show the
    certificate it replaced.
    """
    first_seen: dict[str, date] = {}       # every hostname under the domain
    shown: dict[str, dict] = {}            # display label -> facts
    visible = 0
    newest: date | None = None
    for cert in issuances or []:
        if not isinstance(cert, dict) or cert.get("revoked"):
            continue
        nb = parse_date(str(cert.get("not_before") or "")[:10])
        if nb is None or nb > today:
            continue
        visible += 1
        newest = nb if newest is None or nb > newest else newest
        page = cert.get("_page") if isinstance(cert.get("_page"), int) else 0
        issuer_doc = cert.get("issuer") if isinstance(cert.get("issuer"), dict) else {}
        issuer = str(issuer_doc.get("friendly_name") or "").strip() or None
        na = parse_date(str(cert.get("not_after") or "")[:10])
        lifetime = (na - nb).days if na is not None and na > nb else None
        names = cert.get("dns_names")
        names = [str(n) for n in names] if isinstance(names, list) else []
        for rel in relative_names({"dns_names": names}, domain):
            if rel not in first_seen or nb < first_seen[rel]:
                first_seen[rel] = nb
        # Display labels on this certificate. "exact": the certificate names
        # label.domain itself, not only a host or a wildcard beneath it
        # (ad.corp.x.com is shown as "corp", but corp.x.com has no certificate).
        on_cert: dict[str, tuple[str, bool]] = {}
        for raw in names:
            disp = shown_label(raw, domain)
            if disp is None:
                continue
            label, cls = disp
            exact = raw.strip().rstrip(".").lower() in (f"{label}.{domain}", f"www.{label}.{domain}")
            on_cert[label] = (cls, exact or on_cert.get(label, (cls, False))[1])
        for label, (cls, exact) in on_cert.items():
            cur = shown.get(label)
            if cur is None:
                cur = shown[label] = {"label": label, "class": cls, "issued": nb, "issuer": issuer,
                                      "cert_sha256": cert.get("cert_sha256"), "page": page,
                                      "lifetime": lifetime, "pages": set(), "exact": exact}
            elif nb < cur["issued"]:
                cur.update(issued=nb, issuer=issuer, cert_sha256=cert.get("cert_sha256"),
                           page=page, lifetime=lifetime, exact=exact)
            elif nb == cur["issued"] and exact:
                cur["exact"] = True
            if nb >= since:
                cur["pages"].add(page)

    notable = [v for v in shown.values() if v["issued"] >= since]
    labels = {v["label"] for v in notable}
    for v in notable:
        # Lifetime unknown: the renewal overlap cannot be judged, so not fresh.
        bar = min(CT_FRESH_DAYS, v["lifetime"] // 3) - CT_RENEWAL_MARGIN_DAYS if v["lifetime"] else -1
        v["fresh"] = (today - v["issued"]).days <= bar
        v["nested"] = "." in v["label"] and v["label"].split(".", 1)[1] in labels
    evidence_page = max(((v["issued"], v["page"]) for v in notable), default=(None, 0))[1]
    # Names a reader will find on the evidence page first, then the most
    # telling class, then the shortest name.
    notable.sort(key=lambda v: (evidence_page not in v["pages"], v["nested"],
                                CLASS_ORDER.index(v["class"]), v["label"].count("."),
                                len(v["label"]), v["label"]))
    return {
        "issuances_visible": visible,
        "names_visible": len(first_seen),
        "names_in_window": sum(1 for d in first_seen.values() if d >= since),
        "notable": notable,
        "newest": newest,
        "evidence_page": evidence_page,
    }


def certspotter_url(domain: str, after: str | None = None) -> str:
    url = f"{CERTSPOTTER}?domain={domain}&include_subdomains=true&expand=dns_names&expand=issuer"
    return f"{url}&after={after}" if after else url


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------

def _hint(ent: dict, domain: str) -> EntityHint:
    kind = ent.get("kind") if ent.get("kind") in ("company", "project", "person") else "company"
    return EntityHint(name=clean_name(ent.get("name")), kind=kind, domain=domain,
                      github=github_login(ent.get("github")))


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _listed(prefix_exact: str, prefix_more: str, items: list[str], limit: int = 110,
            most: int = 5) -> str:
    """'<prefix>: a, b and c', or '<prefix> including a, b and c' when some are left out."""
    if len(items) <= most:
        full = f"{prefix_exact}: {_join(items)}"
        if len(full) < limit:
            return full
    for n in range(min(most, len(items) - 1), 0, -1):
        title = f"{prefix_more} including {_join(items[:n])}"
        if len(title) < limit:
            return title
    return prefix_more


def _age(registered: date | None, today: date) -> int | None:
    if registered is None or registered > today:
        return None
    return (today - registered).days


def registered_signal(ent: dict, domain: str, rdap: dict, url: str, today: date,
                      since: date) -> Signal | None:
    """domain_registered, when the registry's registration date is inside the window.

    The date is the registry's own (UTC). A registration before `since` is not
    an event of this window and is left to metrics.domain_age_days.
    """
    registered = trusted_registration(domain, rdap.get("registered"))
    age = _age(registered, today)
    if age is None or registered < since:
        return None
    metrics: dict[str, Any] = {"domain_age_days": age}
    if rdap.get("registrar"):
        metrics["registrar"] = rdap["registrar"]
    if rdap.get("expires"):
        metrics["expires"] = iso(rdap["expires"])
        metrics["expiry_horizon_days"] = (rdap["expires"] - today).days
    day = "day" if age == 1 else "days"
    return Signal(
        source=SLUG, family=FAMILY, kind="domain_registered",
        entity=_hint(ent, domain),
        title=f"Domain {domain} was registered on {iso(registered)}, {age} {day} ago",
        occurred_at=iso(registered),
        url=url,
        value=age, unit="days since registration",
        # A real, dated step, and the earliest one a domain can show. Flat:
        # the scorer applies the decay, so recency is not counted twice here.
        strength=REGISTERED_STRENGTH_PROJECT if ent.get("kind") == "project" else REGISTERED_STRENGTH,
        metrics=metrics,
    )


def govcloud_signal(ent: dict, domain: str, mx: list[str], ns: list[str], txt: list[str],
                    today: date, age: int | None = None) -> Signal | None:
    """dns_govcloud, when mail or nameservers sit on a US government cloud."""
    gov = gov_cloud(mx, ns, txt)
    mail = bool(gov["mx_gcc_high"])
    spf = bool(gov["spf_gcc_high"])
    azure, aws = bool(gov["ns_azure_gov"]), bool(gov["ns_aws_gov"])
    if not (mail or spf or azure or aws):
        return None
    ns_cloud = "Azure Government" if azure else "AWS GovCloud" if aws else None
    ns_zone = "azuregov-dns.us" if azure else "awsdns-us-gov" if aws else None
    ns_owner = "Microsoft's" if azure else "Amazon's"
    # A state read on the scan day, not a dated step, so it stays under what a
    # dated event earns: 0.25 to 0.45. Mail in the government tenant is the
    # strongest tell, a nameserver or an SPF include alone the weakest, and a
    # domain under five years old is more telling than a long-established one.
    if mail and ns_cloud:
        title = (f"Email on Microsoft 365 GCC High or DoD and DNS on {ns_cloud}, "
                 "both US government clouds")
        url, strength = doh_url(domain, "MX"), 0.4
    elif mail:
        title = ("Email runs on Microsoft 365 GCC High or DoD (MX at office365.us), "
                 "a US government cloud for controlled data")
        url, strength = doh_url(domain, "MX"), 0.35
    elif ns_cloud:
        title = (f"DNS is hosted on {ns_cloud} ({ns_zone} nameservers), "
                 f"{ns_owner} US government cloud")
        url, strength = doh_url(domain, "NS"), 0.3
    else:
        title = ("SPF record authorises Microsoft 365 GCC High or DoD (office365.us) "
                 "to send the domain's email")
        url, strength = doh_url(domain, "TXT"), 0.25
    if age is not None and age <= YOUNG_DOMAIN_DAYS:
        strength += 0.05
    evidence = {}
    if mail:
        evidence["mx"] = doh_url(domain, "MX")
    if ns_cloud:
        evidence["ns"] = doh_url(domain, "NS")
    if spf:
        evidence["txt"] = doh_url(domain, "TXT")
    metrics: dict[str, Any] = {
        "observed_only": True,
        "gcc_high_mx": mail,
        "gcc_high_spf": spf,
        "govcloud_ns": ns_cloud,
        "mail_provider": mail_provider(mx),
        "mx_hosts": gov["mx_gcc_high"],
        "ns_hosts": gov["ns_azure_gov"] or gov["ns_aws_gov"],
        "evidence": evidence,
    }
    if age is not None:
        metrics["domain_age_days"] = age
    return Signal(
        source=SLUG, family=FAMILY, kind="dns_govcloud",
        entity=_hint(ent, domain), title=title, occurred_at=iso(today), url=url,
        strength=round(strength, 2), metrics=metrics,
    )


def tooling_signal(ent: dict, domain: str, mx: list[str], txt: list[str], today: date,
                   age: int | None = None) -> Signal | None:
    """dns_tooling: one signal listing the catalog tools the TXT records name."""
    tools = detect_tools(txt)
    # Back-office tools (Atlassian, Rippling, DocuSign) are on nearly every
    # company's domain and say nothing, alone or together. Ask for at least
    # one commercial, hiring or hardware-engineering tool.
    if not any(t["group"] in SIGNAL_GROUPS for t in tools):
        return None
    names = [t["tool"] for t in tools]
    n = len(names)
    noun = "vendor tool" if n == 1 else "vendor tools"
    title = _listed(f"DNS records name {n} {noun}", f"DNS records name {n} {noun}", names)
    groups: dict[str, list[str]] = {}
    for t in tools:
        groups.setdefault(t["group"], []).append(t["tool"])
    weight = sum(t["weight"] for t in tools)
    metrics: dict[str, Any] = {
        "observed_only": True,
        "txt_tool_count": n,
        "tools": names,
        "tool_groups": groups,
        "txt_records": len(txt),
        "mail_provider": mail_provider(mx),
    }
    if age is not None:
        metrics["domain_age_days"] = age
    return Signal(
        source=SLUG, family=FAMILY, kind="dns_tooling",
        entity=_hint(ent, domain), title=title, occurred_at=iso(today),
        url=doh_url(domain, "TXT"),
        value=n, unit="tool" if n == 1 else "tools",
        # Undated, and a token only proves the tool was verified once: routine,
        # 0.15 to 0.3.
        strength=round(0.15 + 0.15 * squash(weight, 2.0), 2),
        metrics=metrics,
    )


def _ct_order(names: list[dict], page: int) -> list[dict]:
    """Names a reader finds on the linked page first, then the most telling class."""
    return sorted(names, key=lambda v: (page not in v["pages"], v["nested"],
                                        CLASS_ORDER.index(v["class"]), v["label"].count("."),
                                        len(v["label"]), v["label"]))


def _ct_weight(names: list[dict]) -> float:
    return sum(CLASS_WEIGHT[v["class"]] * (NESTED_WEIGHT if v["nested"] else 1.0) for v in names)


def ct_signal(ent: dict, domain: str, summary: dict, lookback_days: int, today: date,
              age: int | None = None, page_urls: list[str] | None = None,
              complete: bool = True) -> Signal | None:
    """One certificate signal for a domain, or None.

    ct_new_subdomains when at least one notable hostname is fresh (see
    parse_issuances): the title names only those, and occurred_at is the
    newest of their certificates. Otherwise ct_subdomains, an inventory of
    notable hostnames with certificates issued in the window, which needs at
    least one name that says something about the business (a site, a
    government, commerce, product or developer hostname) and stays at routine
    strength because most of it is renewals. The inventory carries
    metrics.rolling, so the store keeps one row per entity for it.

    `page_urls` are the Cert Spotter pages the issuances came from, in order.
    `complete` is False when the listing was cut off before its last page; the
    title then says "at least", because the newest certificates are missing.
    """
    notable = summary.get("notable") or []
    if not notable:
        return None
    page_urls = page_urls or [certspotter_url(domain)]
    fresh = [v for v in notable if v["fresh"]]
    if not fresh and not any(v["class"] in CT_TELLING_CLASSES for v in notable):
        return None
    counted = fresh or notable
    anchor = max(counted, key=lambda v: (v["issued"], v["page"]))
    counted = _ct_order(counted, anchor["page"])
    n = len(counted)
    labels = [v["label"] for v in counted]
    if fresh:
        kind = "ct_new_subdomains"
        host = f"{labels[0]}.{domain}"
        if counted[0].get("exact"):
            title = (f"Hostname {host} got a certificate on {iso(anchor['issued'])} "
                     "and has no older unexpired one")
        else:
            title = (f"A host under {host} got a certificate on {iso(anchor['issued'])}, "
                     "the oldest unexpired one there")
        if n > 1 or len(title) >= 110:
            count = f"{n}" if complete else f"at least {n}"
            noun = "subdomain" if n == 1 and complete else "subdomains"
            head = f"No unexpired certificate older than {CT_FRESH_DAYS} days for {count} {noun}"
            title = _listed(head, head, labels, most=4)
        # 0.38 for one routine hostname, about 0.5 for a site or a storefront,
        # 0.6 and up for a factory build-out. Never above 0.8: a late renewal
        # can pass for a first issuance.
        strength = 0.3 + 0.5 * squash(_ct_weight(counted), 1.5)
    else:
        kind = "ct_subdomains"
        noun = "notable subdomain" if n == 1 else "notable subdomains"
        count = f"{n}" if complete else f"at least {n}"
        head = f"Certificates issued in the last {lookback_days} days name {count} {noun}"
        title = _listed(head, head, labels, most=4)
        # Mostly renewals, so an inventory and no more: 0.15 to 0.3.
        strength = 0.15 + 0.15 * squash(_ct_weight(counted), 3.0)
    classes: dict[str, int] = {}
    for v in notable:
        classes[v["class"]] = classes.get(v["class"], 0) + 1
    listed = counted + [v for v in _ct_order(notable, anchor["page"]) if v not in counted]
    metrics: dict[str, Any] = {
        "ct_notable_names": len(notable),
        "ct_fresh_names": len(fresh),
        "ct_names_in_window": summary["names_in_window"],
        "ct_names_visible": summary["names_visible"],
        "ct_issuances_visible": summary["issuances_visible"],
        "ct_classes": classes,
        "names": [{"label": v["label"], "class": v["class"], "issued": iso(v["issued"]),
                   "issuer": v["issuer"], "fresh": v["fresh"]} for v in listed[:40]],
        "window_days": lookback_days,
        "fresh_days": CT_FRESH_DAYS,
        "newness": "earliest unexpired certificate per name; expired ones are not visible, "
                   "so a renewal can look like a first issuance",
    }
    if len(page_urls) > 1:
        metrics["evidence_pages"] = page_urls
    if anchor.get("cert_sha256"):
        metrics["crtsh_url"] = f"https://crt.sh/?q={anchor['cert_sha256']}"
    if not fresh:
        # An inventory, not an event: its date is the newest of the earliest
        # unexpired certificates, which moves forward as older ones expire.
        # One stored row per entity that each run refreshes. No "subject":
        # a domain gives at most one inventory. ct_new_subdomains is a dated
        # event and stays one row per date.
        metrics["rolling"] = True
    if not complete:
        metrics["ct_listing_incomplete"] = True
    if age is not None:
        metrics["domain_age_days"] = age
    return Signal(
        source=SLUG, family=FAMILY, kind=kind,
        entity=_hint(ent, domain), title=title, occurred_at=iso(anchor["issued"]),
        url=page_urls[min(anchor["page"], len(page_urls) - 1)],
        value=n, unit="subdomain" if n == 1 else "subdomains",
        strength=round(strength, 2), metrics=metrics,
    )


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def _doh(domain: str, rtype: str) -> dict:
    return http.get_json(doh_url(domain, rtype), ttl=DNS_TTL, timeout=20, retries=2)


def _fetch(domain: str, bootstrap: dict[str, str]) -> dict:
    """RDAP plus DNS for one domain. Never raises: failures land in 'errors'."""
    out: dict[str, Any] = {"rdap": None, "rdap_url": rdap_url(domain, bootstrap), "mx": [],
                           "txt": [], "ns": [], "dns_ok": False, "errors": []}
    if out["rdap_url"]:
        try:
            doc = http.get_json(out["rdap_url"], headers={"Accept": "application/rdap+json"},
                                ttl=RDAP_TTL, timeout=20, retries=1)
            out["rdap"] = parse_rdap(doc)
        except http.HttpError as e:
            if e.status != 404:
                out["errors"].append(f"RDAP HTTP {e.status}")
        except Exception as e:  # noqa: BLE001 - one registry must not lose the run
            out["errors"].append(f"RDAP {type(e).__name__}")
    try:
        txt_doc = _doh(domain, "TXT")
        out["txt"] = doh_answers(txt_doc, "TXT")
        if txt_doc.get("Status") == 0:
            out["dns_ok"] = True
            out["mx"] = mx_hosts(doh_answers(_doh(domain, "MX"), "MX"))
            # The registry already lists the delegation. Ask DNS for NS only
            # when that list looks like a government cloud (so the claim rests
            # on a DNS answer a reader can open) or when there is no RDAP.
            registry_ns = (out["rdap"] or {}).get("nameservers") or []
            if not registry_ns or looks_gov_ns(registry_ns):
                out["ns"] = ns_hosts(doh_answers(_doh(domain, "NS"), "NS"))
    except Exception as e:  # noqa: BLE001
        out["errors"].append(f"DNS {type(e).__name__}")
    return out


def _from_network(headers: dict) -> bool:
    """True when a response came off the wire just now rather than the disk cache.

    The cache stores the original response headers, so an old Date header
    means a cache hit. Unreadable dates count as fresh, the cautious reading.
    """
    try:
        sent = parsedate_to_datetime(str(headers.get("date")))
        return abs((datetime.now(timezone.utc) - sent).total_seconds()) < 120
    except (TypeError, ValueError):
        return True


def _ct_page(url: str) -> tuple[list[dict], dict]:
    last: Exception | None = None
    for _ in range(2):  # the API is known to time out once and then answer
        try:
            text, headers = http.request(url, headers={"Accept": "application/json"},
                                         ttl=CT_TTL, timeout=75, retries=0, return_headers=True)
            data = json.loads(text)
            return (data if isinstance(data, list) else []), headers
        except http.HttpError:
            raise
        except Exception as e:  # noqa: BLE001
            last = e
    assert last is not None
    raise last


def _fetch_ct(domain: str, state: dict) -> tuple[list[dict], list[str], bool, str | None]:
    """Unexpired issuances for a domain, following pages while the budget lasts.

    `state` is {"budget": network requests left this run, "quota_spent": bool,
    "failures": lookups failed back to back}. Returns (issuances tagged with
    "_page", page urls, complete, error). Never raises. A 429, or a response
    saying no quota is left, sets quota_spent so the caller stops asking. A
    failed request went to the network too, so it is charged to the budget.
    """
    issuances: list[dict] = []
    page_urls: list[str] = []
    url = certspotter_url(domain)
    for page_no in range(CT_MAX_PAGES):
        if state["budget"] <= 0 or state["quota_spent"]:
            return issuances, page_urls, False, None
        try:
            data, headers = _ct_page(url)
        except http.HttpError as e:
            state["budget"] -= 1
            state["failures"] = state.get("failures", 0) + 1
            if e.status == 429:
                state["quota_spent"] = True
                return issuances, page_urls, False, "hourly quota spent"
            return issuances, page_urls, False, f"HTTP {e.status}"
        except Exception as e:  # noqa: BLE001
            state["budget"] -= 1
            state["failures"] = state.get("failures", 0) + 1
            return issuances, page_urls, False, type(e).__name__
        state["failures"] = 0
        if _from_network(headers):
            state["budget"] -= 1
            if str(headers.get("x-ratelimit-remaining")) == "0":
                state["quota_spent"] = True
        rows = [c for c in data if isinstance(c, dict)]
        for cert in rows:
            cert["_page"] = page_no
        issuances.extend(rows)
        page_urls.append(url)
        last_id = str(rows[-1].get("id") or "") if rows else ""
        if len(data) < CT_PAGE_SIZE or not last_id:
            return issuances, page_urls, True, None
        url = certspotter_url(domain, after=last_id)
    return issuances, page_urls, False, None


def _clock_today() -> date:
    """The real calendar day in UTC, as opposed to ctx.today, which --today can move.

    UTC because the run date is the UTC date, and so are the registry and
    certificate dates this collector reads.
    """
    return datetime.now(timezone.utc).date()


def _prelim(ent: dict) -> float:
    try:
        return float(ent.get("prelim") or 0)
    except (TypeError, ValueError):
        return 0.0


def collect(ctx: Context) -> Iterable[Signal]:
    targets: list[tuple[dict, str]] = []
    seen: set[str] = set()
    skipped = unnamed = 0
    for ent in ctx.known:
        if not isinstance(ent, dict):
            continue
        name = clean_name(ent.get("name"))
        raw = ent.get("domain")
        # A person's domain is an employer, a university or a personal site:
        # none of them is this entity's own infrastructure.
        if not name or not isinstance(raw, str) or not raw.strip() or ent.get("kind") == "person":
            continue
        domain = registrable(raw)
        if domain is None:
            skipped += 1
            continue
        # The domain has to carry the entity's name. A website field that
        # points at a customer, a parent or a platform must not put that
        # organisation's DNS and certificates under this entity.
        if not carries_name(name, domain, github_login(ent.get("github"))):
            unnamed += 1
            continue
        if domain in seen:
            continue
        seen.add(domain)
        targets.append((ent, domain))
        if ctx.limit and len(targets) >= ctx.limit:
            break
    if not targets:
        ctx.log(f"{SLUG}: no known entities with a registrable domain of their own")
        return

    try:
        bootstrap = parse_bootstrap(http.get_json(RDAP_BOOTSTRAP, ttl=7 * 24 * 3600))
    except Exception as e:  # noqa: BLE001
        ctx.warn(f"{SLUG}: RDAP bootstrap unavailable ({type(e).__name__}); "
                 "only .io, .co and .us dates this run")
        bootstrap = {}

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        fetched = list(pool.map(lambda t: _fetch(t[1], bootstrap), targets))

    # DNS answers carry no dates, and Cert Spotter lists only what is unexpired
    # now: both are the state today. When the run is dated to another day that
    # state cannot be given the run's date, and "no older unexpired
    # certificate" cannot be judged for it, so only the registry date is used.
    # One day of slack for a run that started before midnight UTC and is still
    # going after it. A run dated ahead of the clock is never the scan day.
    on_scan_day = 0 <= (_clock_today() - ctx.today).days <= 1
    if not on_scan_day:
        ctx.warn(f"{SLUG}: run is dated {iso(ctx.today)}, not today; DNS and the certificate "
                 "listing have no history, so only domain_registered is emitted")

    ages: dict[str, int | None] = {}
    no_rdap = rdap_ok = dns_ok = 0
    for (ent, domain), got in zip(targets, fetched):
        for err in got["errors"]:
            ctx.warn(f"{SLUG}: {domain}: {err}")
        try:
            rdap = got["rdap"]
            if got["rdap_url"] is None:
                no_rdap += 1
            if rdap:
                rdap_ok += 1
            age = _age(trusted_registration(domain, rdap["registered"]), ctx.today) if rdap else None
            ages[domain] = age
            if rdap:
                sig = registered_signal(ent, domain, rdap, got["rdap_url"], ctx.today, ctx.since)
                if sig:
                    yield sig
            if got["dns_ok"]:
                dns_ok += 1
            if got["dns_ok"] and on_scan_day:
                sig = govcloud_signal(ent, domain, got["mx"], got["ns"], got["txt"], ctx.today, age)
                if sig:
                    yield sig
                sig = tooling_signal(ent, domain, got["mx"], got["txt"], ctx.today, age)
                if sig:
                    yield sig
        except Exception as e:  # noqa: BLE001 - one bad row must not lose the run
            ctx.warn(f"{SLUG}: {domain}: {type(e).__name__}: {e}")

    # Certificate Transparency for the strongest few. `known` arrives sorted
    # by prelim; sort again so the budget never depends on that. A domain with
    # more than one page of certificates shares the same request budget, so
    # its later pages may have to wait for the next run.
    ranked = sorted(targets, key=lambda t: -_prelim(t[0]))[:CT_BUDGET] if on_scan_day else []
    state = {"budget": CT_BUDGET, "quota_spent": False, "failures": 0}
    ct_done = 0
    for ent, domain in ranked:
        if state["budget"] <= 0 or state["quota_spent"]:
            break
        if state["failures"] >= CT_MAX_FAILURES:
            ctx.warn(f"{SLUG}: Cert Spotter failed {CT_MAX_FAILURES} lookups in a row; "
                     "no more certificate lookups this run")
            break
        issuances, page_urls, complete, error = _fetch_ct(domain, state)
        if error:
            ctx.warn(f"{SLUG}: {domain}: Cert Spotter {error}")
        if not page_urls:
            continue
        ct_done += 1
        try:
            if not complete:
                ctx.warn(f"{SLUG}: {domain}: certificate listing cut off after "
                         f"{len(issuances)} issuances; newest ones not read")
            summary = parse_issuances(issuances, domain, ctx.since, ctx.today)
            sig = ct_signal(ent, domain, summary, ctx.lookback_days, ctx.today,
                            ages.get(domain), page_urls, complete)
            if sig:
                yield sig
        except Exception as e:  # noqa: BLE001
            ctx.warn(f"{SLUG}: {domain}: {type(e).__name__}: {e}")

    ctx.log(f"{SLUG}: {len(targets)} domains ({skipped} skipped as subdomain, shared host or "
            f"institution, {unnamed} as not carrying the entity's name), "
            f"RDAP {rdap_ok} ok / {no_rdap} with no registry service, DNS {dns_ok} ok, "
            f"CT read for {ct_done} domains with {CT_BUDGET - state['budget']} new requests")
