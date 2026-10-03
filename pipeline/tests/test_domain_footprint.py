"""Offline tests for the domain_footprint collector.

Parsing runs against the saved responses in fixtures/traffic/: the RDAP,
MX and TXT answers for skild.ai, the Cert Spotter issuances for neros.tech,
and the 69-domain panel a colleague derived from live lookups. A few DNS
answers that have no fixture file (the neros.tech MX and SPF, the aalo.com and
allencontrolsystems.com nameservers) are pinned below exactly as dns.google
returned them on 2026-10-01, with verification token values redacted.

No network: the tests that drive collect() swap http.get_json and
http.request for fakes that serve those same responses.
"""

from __future__ import annotations

import copy
import json
import unittest
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from unittest import mock

from antenna import http
from antenna.collectors import domain_footprint as df
from antenna.collectors.base import Context, clean_domain
from antenna.db import fingerprint
from antenna.models import FAMILIES
from antenna.thesis import classify

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "traffic"
BUNDLE = json.loads((FIXTURES / "domain_probe_bundle.json").read_text())
NEROS_CT = json.loads((FIXTURES / "certspotter_issuances_neros.tech.json").read_text())["response"]
PANEL = {row["domain"]: row for row in BUNDLE["panel_69_domains"]}

RDAP_SKILD = BUNDLE["rdap"]["response"]
MX_SKILD = BUNDLE["doh_mx"]["response"]
TXT_SKILD = BUNDLE["doh_txt"]["response"]

TODAY = date(2026, 10, 1)
SINCE = TODAY - timedelta(days=120)  # 2026-06-03


def _answer(name: str, rtype: int, records: list[str]) -> dict:
    return {"Status": 0, "Question": [{"name": name + ".", "type": rtype}],
            "Answer": [{"name": name + ".", "type": rtype, "TTL": 3600, "data": r} for r in records]}


# dns.google answers read live on 2026-10-01.
MX_NEROS = _answer("neros.tech", 15, ["0 neros.mail.protection.office365.us."])
MX_HADRIAN = _answer("hadrian.co", 15, [
    "10 mxa-00adba01.gslb.gpphosted.com.",
    "300 hadrianautomation.mail.protection.office365.us.",
    "10 mxb-00adba01.gslb.gpphosted.com.",
])
NS_AALO = _answer("aalo.com", 2, [
    "ns1-36.azuregov-dns.us.", "ns2-36.azuregov-dns.us.",
    "ns3-36.azuregov-dns.us.", "ns4-36.azuregov-dns.us.",
])
NS_ALLEN = _answer("allencontrolsystems.com", 2, [
    "ns-118.awsdns-us-gov-14.org.", "ns-1865.awsdns-us-gov-41.net.",
    "ns-1452.awsdns-us-gov-53.com.", "ns-524.awsdns-us-gov-01.us.",
])
NS_NEROS = _answer("neros.tech", 2, ["ns18.domaincontrol.com.", "ns17.domaincontrol.com."])
TXT_NEROS = _answer("neros.tech", 16, [
    "slack-domain-verification=<redacted>",
    "airtable-verification=<redacted>",
    "google-site-verification=<redacted>",
    "MS=<redacted>",
    "atlassian-domain-verification=<redacted>",
    "v=spf1 include:spf.protection.office365.us include:_spf.smtp.com include:_spf.salesforce.com "
    "include:dc-aa8e722993._spfm.neros.tech include:mail.zendesk.com -all",
    "docusign=<redacted>",
    "box-domain-verification=<redacted>",
])
TXT_BASE_POWER = [
    "google-site-verification=<redacted>",
    "stripe-verification=<redacted>",
    "v=spf1 include:_spf.google.com include:hubspotemail.net include:mg-spf.greenhouse.io "
    "include:sendgrid.net ~all",
    "autodesk-domain-verification=<redacted>",
    "hubspot-domain-verification=<redacted>",
]
NXDOMAIN = {"Status": 3, "Question": [{"name": "nope.", "type": 16}]}

NEROS = {"slug": "neros-technologies", "name": "Neros Technologies", "kind": "company",
         "domain": "neros.tech", "github": None, "prelim": 1.2}
SKILD = {"slug": "skild-ai", "name": "Skild AI", "kind": "company", "domain": "skild.ai",
         "github": None, "prelim": 1.5}


def txt(doc: dict) -> list[str]:
    return df.doh_answers(doc, "TXT")


def mx(doc: dict) -> list[str]:
    return df.mx_hosts(df.doh_answers(doc, "MX"))


def ns(doc: dict) -> list[str]:
    return df.ns_hosts(df.doh_answers(doc, "NS"))


def check_title(test: unittest.TestCase, title: str) -> None:
    test.assertLess(len(title), 110, title)
    test.assertFalse(title.endswith("."), title)
    test.assertTrue(title[0].isupper(), title)
    test.assertNotIn("  ", title)


class Registrable(unittest.TestCase):
    def test_apex_domains_pass(self):
        for d in ("skild.ai", "neros.tech", "physicalintelligence.company", "hadrian.co"):
            self.assertEqual(df.registrable(d), d)

    def test_www_and_url_forms_reduce_to_the_apex(self):
        self.assertEqual(df.registrable("https://www.Skild.ai/careers"), "skild.ai")

    def test_country_second_level_is_kept_whole(self):
        self.assertEqual(df.registrable("cambridgeaerospace.co.uk"), "cambridgeaerospace.co.uk")

    def test_subdomains_are_refused_not_trimmed(self):
        # The parent could be a university or a hosting platform.
        self.assertIsNone(df.registrable("robotics.example.com"))
        self.assertIsNone(df.registrable("team.pages.dev"))

    def test_shared_and_institutional_hosts_are_refused(self):
        for d in ("mit.edu", "nasa.gov", "army.mil", "itch.io", "ox.ac.uk", "ethz.ch", "github.com",
                  "", None):
            self.assertIsNone(df.registrable(d), d)

    def test_job_boards_shorteners_investors_and_universities_are_refused(self):
        for d in ("workatastartup.com", "calendly.com", "tinyurl.com", "lnkd.in", "techstars.com",
                  "a16z.com", "uni-freiburg.de", "tu-berlin.de", "univ-lyon1.fr",
                  "stanforduniversity.org", "keio.go.jp", "https://bit.ly/3xyz", "ycombinator.com"):
            self.assertIsNone(df.registrable(d), d)
        # Names that merely start with the same letters are still companies.
        for d in ("unitree.com", "tulip.co", "universalrobots.com"):
            self.assertEqual(df.registrable(d), d)

    def test_hosts_the_shared_helper_refuses_stay_refused_without_a_local_copy(self):
        # Dropped from the local list once base.clean_domain covered them.
        for d in ("pages.dev", "web.app", "fly.dev", "kickstarter.com", "ashbyhq.com", "greenhouse.io",
                  "lever.co", "wellfound.com", "crunchbase.com", "sosv.com", "hax.co", "reddit.com",
                  "sec.gov", "sam.gov", "kit.edu", "u-tokyo.ac.jp", "railway.app", "outlook.com",
                  "nus.edu.sg"):
            self.assertIsNone(df.registrable(d), d)
            self.assertNotIn(d, df._SHARED_APEX)
        # National institution hosts the shared helper refuses by suffix.
        for d in ("ntu.edu.sg", "dsta.gov.sg", "dstl.gov.uk", "auckland.ac.nz", "nrc-cnrc.gc.ca",
                  "iitb.ac.in", "hku.edu.hk", "ustc.ac.cn"):
            self.assertIsNone(clean_domain(d), d)
            self.assertIsNone(df.registrable(d), d)
        # The local list holds only what the shared helper lets through.
        self.assertEqual(sorted(h for h in df._SHARED_APEX if clean_domain(h) is None), [])
        for d in sorted(df._SHARED_APEX):
            self.assertIsNone(df.registrable(d), d)
        # Treaty organisations are this collector's own rule.
        self.assertIsNone(df.registrable("nato.int"))


class Identity(unittest.TestCase):
    def test_sample_domains_carry_their_entity_name(self):
        sample = json.loads((FIXTURES.parent / "known_sample.json").read_text())
        for ent in sample:
            if ent.get("domain"):
                self.assertTrue(df.carries_name(ent["name"], ent["domain"], ent.get("github")), ent["name"])

    def test_panel_style_names(self):
        for name, domain in (("Aalo Atomics", "aalo.com"), ("Foundation", "foundation.bot"),
                             ("Physical Intelligence", "pi.website"), ("Zipline", "flyzipline.com"),
                             ("Radiant Industries", "radiantnuclear.com"), ("1X Technologies", "1x.tech")):
            self.assertTrue(df.carries_name(name, domain), name)

    def test_someone_elses_domain_is_refused(self):
        self.assertFalse(df.carries_name("Acme Robotics", "bostondynamics.com"))
        self.assertFalse(df.carries_name("spot-sdk-demo", "bostondynamics.com", "jdoe"))
        self.assertFalse(df.carries_name("Acme Robotics", "notion.site"))
        self.assertFalse(df.carries_name("", "acme.com"))

    def test_github_login_can_vouch_for_the_domain(self):
        self.assertTrue(df.carries_name("openpilot", "comma.ai", "commaai"))
        self.assertFalse(df.carries_name("openpilot", "comma.ai", None))

    def test_github_hint_is_a_login_never_a_url(self):
        self.assertEqual(df.github_login("hebbian-robotics"), "hebbian-robotics")
        self.assertEqual(df.github_login("https://github.com/Hebbian-Robotics/"), "Hebbian-Robotics")
        self.assertEqual(df.github_login("github.com/commaai/openpilot"), "commaai")
        self.assertEqual(df.github_login("@commaai"), "commaai")
        for bad in (None, "", "not a login", 42, "https://example.com/x", "a" * 60):
            self.assertIsNone(df.github_login(bad), bad)
        hint = df._hint({"name": "X", "github": "https://github.com/x-org"}, "x.com")
        self.assertEqual(hint.github, "x-org")

    def test_names_are_unescaped_and_single_spaced(self):
        self.assertEqual(df.clean_name("  Ball &amp; Chain   Robotics\n"), "Ball & Chain Robotics")
        self.assertEqual(df.clean_name(None), "")
        self.assertEqual(df._hint({"name": "R&amp;D  Co"}, "rd.co").name, "R&D Co")


class Rdap(unittest.TestCase):
    BOOT = df.parse_bootstrap({"services": [
        [["ai", "energy"], ["https://rdap.identitydigital.services/rdap/"]],
        [["com"], ["https://rdap.verisign.com/com/v1/"]],
    ]})

    def test_bootstrap_routes_to_the_registry(self):
        self.assertEqual(df.rdap_url("skild.ai", self.BOOT),
                         "https://rdap.identitydigital.services/rdap/domain/skild.ai")
        self.assertEqual(df.rdap_url("castelion.com", self.BOOT),
                         "https://rdap.verisign.com/com/v1/domain/castelion.com")

    def test_io_co_us_use_the_hard_coded_registries(self):
        self.assertEqual(df.rdap_url("hadrian.co", {}), "https://rdap.registry.co/co/domain/hadrian.co")
        self.assertEqual(df.rdap_url("batear.io", {}),
                         "https://rdap.identitydigital.services/rdap/domain/batear.io")
        self.assertEqual(df.rdap_url("theseus.us", {}), "https://rdap.nic.us/domain/theseus.us")

    def test_tld_without_rdap_gives_no_url(self):
        self.assertIsNone(df.rdap_url("copperhead.sh", self.BOOT))

    def test_parse_fixture(self):
        got = df.parse_rdap(RDAP_SKILD)
        self.assertEqual(got["registered"], date(2023, 2, 7))
        self.assertEqual(got["expires"], date(2033, 2, 7))
        self.assertEqual(got["registrar"], "NameCheap, Inc.")
        self.assertEqual(got["nameservers"], ["garrett.ns.cloudflare.com", "journey.ns.cloudflare.com"])

    def test_parse_survives_an_empty_or_odd_response(self):
        self.assertEqual(df.parse_rdap({}),
                         {"registered": None, "expires": None, "registrar": None, "nameservers": []})
        got = df.parse_rdap({"events": [{"eventAction": "registration"}], "entities": [{"roles": None}]})
        self.assertIsNone(got["registered"])

    def test_registry_date_formats_from_the_panel_all_parse(self):
        # Verisign, Identity Digital, Radix and Nominet each format the date differently.
        for domain, row in PANEL.items():
            doc = {"events": [{"eventAction": "registration", "eventDate": row["rdap"]["registration"]}]}
            got = df.parse_rdap(doc)["registered"]
            self.assertEqual(got.isoformat(), row["rdap"]["registration"][:10], domain)

    def test_ai_migration_date_is_not_trusted(self):
        self.assertIsNone(df.trusted_registration("shield.ai", date(2017, 12, 15)))
        self.assertEqual(df.trusted_registration("skild.ai", date(2023, 2, 7)), date(2023, 2, 7))
        self.assertEqual(df.trusted_registration("old.com", date(2017, 12, 15)), date(2017, 12, 15))


class RegisteredSignal(unittest.TestCase):
    URL = "https://rdap.identitydigital.services/rdap/domain/skild.ai"

    def test_registration_inside_the_window_emits_with_the_registry_date(self):
        today = date(2023, 6, 1)
        sig = df.registered_signal(SKILD, "skild.ai", df.parse_rdap(RDAP_SKILD), self.URL, today,
                                   today - timedelta(days=120))
        sig.validate()
        self.assertEqual(sig.kind, "domain_registered")
        self.assertEqual(sig.occurred_at, "2023-02-07")
        self.assertEqual(sig.title, "Domain skild.ai was registered on 2023-02-07")
        self.assertEqual(sig.value, 114)
        self.assertEqual(sig.metrics["domain_age_days"], 114)
        self.assertEqual(sig.metrics["registrar"], "NameCheap, Inc.")
        self.assertEqual(sig.url, self.URL)
        self.assertEqual(sig.entity.name, "Skild AI")
        self.assertEqual(sig.entity.domain, "skild.ai")
        self.assertEqual(sig.strength, df.REGISTERED_STRENGTH)
        check_title(self, sig.title)

    def test_a_project_registering_a_domain_is_the_low_end_of_solid(self):
        # robocurve.org, registered 2026-06-24: a repo with no company behind it yet.
        rdap = {"registered": date(2026, 6, 24), "expires": date(2027, 6, 24), "registrar": "Cloudflare, Inc.",
                "nameservers": []}
        ent = {"name": "robocurve", "kind": "project", "github": "robocurve"}
        sig = df.registered_signal(ent, "robocurve.org", rdap, self.URL, TODAY, SINCE)
        self.assertEqual(sig.title, "Domain robocurve.org was registered on 2026-06-24")
        self.assertEqual(sig.entity.kind, "project")
        self.assertEqual(sig.strength, df.REGISTERED_STRENGTH_PROJECT)
        self.assertEqual(sig.metrics["expiry_horizon_days"], 266)

    def test_registration_before_the_window_is_silent(self):
        self.assertIsNone(df.registered_signal(SKILD, "skild.ai", df.parse_rdap(RDAP_SKILD), self.URL,
                                               TODAY, SINCE))
        # axisrobotics.ai, registered 2025-12-09: young, but 296 days is outside 120.
        rdap = {"registered": date(2025, 12, 9), "expires": None, "registrar": None, "nameservers": []}
        self.assertIsNone(df.registered_signal(SKILD, "axisrobotics.ai", rdap, self.URL, TODAY, SINCE))

    def test_window_edges(self):
        rdap = {"registered": SINCE, "expires": None, "registrar": None, "nameservers": []}
        edge = df.registered_signal(SKILD, "x.com", rdap, self.URL, TODAY, SINCE)
        self.assertEqual(edge.occurred_at, SINCE.isoformat())
        self.assertEqual(edge.value, 120)
        rdap["registered"] = SINCE - timedelta(days=1)
        self.assertIsNone(df.registered_signal(SKILD, "x.com", rdap, self.URL, TODAY, SINCE))
        rdap["registered"] = TODAY
        new = df.registered_signal(SKILD, "x.com", rdap, self.URL, TODAY, SINCE)
        self.assertEqual(new.value, 0)
        self.assertNotIn("ago", new.title)
        # Flat: the scorer decays it, the collector does not decay it a second time.
        self.assertEqual(new.strength, edge.strength)
        rdap["registered"] = TODAY - timedelta(days=1)
        self.assertEqual(df.registered_signal(SKILD, "x.com", rdap, self.URL, TODAY, SINCE).value, 1)

    def test_missing_or_future_date_is_silent(self):
        rdap = {"registered": None, "expires": None, "registrar": None, "nameservers": []}
        self.assertIsNone(df.registered_signal(SKILD, "x.com", rdap, self.URL, TODAY, SINCE))
        rdap["registered"] = TODAY + timedelta(days=2)
        self.assertIsNone(df.registered_signal(SKILD, "x.com", rdap, self.URL, TODAY, SINCE))

    def test_no_panel_domain_is_young_on_the_scan_day(self):
        # The colleague's panel: the youngest registration is 2024-02-25.
        for domain, row in PANEL.items():
            rdap = df.parse_rdap({"events": [{"eventAction": "registration",
                                              "eventDate": row["rdap"]["registration"]}]})
            self.assertIsNone(df.registered_signal({"name": domain}, domain, rdap, self.URL, TODAY, SINCE),
                              domain)


class Dns(unittest.TestCase):
    def test_mx_fixture_is_google_workspace(self):
        hosts = mx(MX_SKILD)
        self.assertEqual(len(hosts), 5)
        self.assertIn("aspmx.l.google.com", hosts)
        self.assertEqual(df.mail_provider(hosts), "google_workspace")

    def test_failed_lookup_gives_no_records(self):
        self.assertEqual(df.doh_answers(NXDOMAIN, "TXT"), [])
        self.assertEqual(df.doh_answers({}, "MX"), [])
        self.assertIsNone(df.mail_provider([]))

    def test_other_record_types_in_an_answer_are_ignored(self):
        doc = {"Status": 0, "Answer": [{"type": 5, "data": "alias.example.net."},
                                       {"type": 16, "data": "\"v=spf1 include:a.example\" \" -all\""}]}
        self.assertEqual(df.doh_answers(doc, "TXT"), ["v=spf1 include:a.example -all"])
        self.assertEqual(df.doh_answers(doc, "MX"), [])

    def test_gcc_high_mx(self):
        self.assertEqual(df.mail_provider(mx(MX_NEROS)), "microsoft_365_gcc_high_or_dod")
        self.assertEqual(df.mail_provider(mx(MX_HADRIAN)), "microsoft_365_gcc_high_or_dod,proofpoint")
        gov = df.gov_cloud(mx(MX_NEROS), ns(NS_NEROS), txt(TXT_NEROS))
        self.assertEqual(gov["mx_gcc_high"], ["neros.mail.protection.office365.us"])
        self.assertEqual(gov["spf_gcc_high"], ["spf.protection.office365.us"])
        self.assertEqual(gov["ns_azure_gov"], [])
        self.assertEqual(gov["ns_aws_gov"], [])

    def test_commercial_microsoft_365_is_not_government(self):
        hosts = ["valaratomics-com.mail.protection.outlook.com"]
        self.assertEqual(df.mail_provider(hosts), "microsoft_365")
        gov = df.gov_cloud(hosts, [], ["v=spf1 include:spf.protection.outlook.com -all"])
        self.assertFalse(any(gov.values()))

    def test_gov_nameservers(self):
        self.assertEqual(len(df.gov_cloud([], ns(NS_AALO), [])["ns_azure_gov"]), 4)
        self.assertEqual(len(df.gov_cloud([], ns(NS_ALLEN), [])["ns_aws_gov"]), 4)
        self.assertTrue(df.looks_gov_ns(["NS1-37.AZUREGOV-DNS.US".lower()]))
        # Ordinary Route 53 and Azure DNS must not match.
        self.assertFalse(df.looks_gov_ns(["ns-1457.awsdns-54.org", "ns1-01.azure-dns.com",
                                          "garrett.ns.cloudflare.com"]))

    def test_gov_nameserver_flag_agrees_with_the_panel(self):
        for domain, row in PANEL.items():
            hosts = df.ns_hosts(row["dns"]["ns"])
            self.assertEqual(df.looks_gov_ns(hosts), row["dns"]["ns_govcloud"], domain)

    def test_spf_includes_and_redirect(self):
        self.assertEqual(df.spf_includes(txt(TXT_SKILD)), ["skild.ai.hosted.spf-report.com"])
        self.assertEqual(df.spf_includes(txt(TXT_NEROS)), [
            "spf.protection.office365.us", "_spf.smtp.com", "_spf.salesforce.com",
            "dc-aa8e722993._spfm.neros.tech", "mail.zendesk.com"])


class Tooling(unittest.TestCase):
    def tools(self, records: list[str]) -> list[str]:
        return [t["tool"] for t in df.detect_tools(records)]

    def test_skild_fixture_names_only_atlassian(self):
        records = txt(TXT_SKILD)
        self.assertEqual(len(records), 22)
        self.assertEqual(self.tools(records), ["Atlassian"])

    def test_one_back_office_tool_is_not_a_signal(self):
        self.assertIsNone(df.tooling_signal(SKILD, "skild.ai", mx(MX_SKILD), txt(TXT_SKILD), TODAY))
        self.assertIsNone(df.tooling_signal(SKILD, "skild.ai", [], ["rippling-domain-verification=x"], TODAY))
        self.assertIsNone(df.tooling_signal(SKILD, "skild.ai", [], [], TODAY))

    def test_back_office_tools_together_are_still_not_a_signal(self):
        # thea.energy and foundation.bot on 2026-10-01: Rippling and Atlassian, nothing else.
        records = ["rippling-domain-verification=x", "atlassian-domain-verification=y", "docusign=z"]
        self.assertEqual(self.tools(records), ["Rippling", "DocuSign", "Atlassian"])
        self.assertIsNone(df.tooling_signal(SKILD, "skild.ai", [], records, TODAY))

    def test_one_commercial_tool_is_a_signal(self):
        sig = df.tooling_signal(SKILD, "skild.ai", [], ["stripe-verification=x"], TODAY)
        self.assertEqual(sig.title, "DNS records name 1 vendor tool: Stripe")
        self.assertEqual((sig.value, sig.unit), (1, "tool"))

    def test_neros_spf_and_txt(self):
        self.assertEqual(self.tools(txt(TXT_NEROS)), ["Salesforce", "Zendesk", "DocuSign", "Atlassian"])
        sig = df.tooling_signal(NEROS, "neros.tech", mx(MX_NEROS), txt(TXT_NEROS), TODAY, age=1132)
        sig.validate()
        self.assertEqual(sig.kind, "dns_tooling")
        self.assertEqual(sig.title, "DNS records name 4 vendor tools: Salesforce, Zendesk, DocuSign and Atlassian")
        self.assertEqual(sig.value, 4)
        self.assertEqual(sig.occurred_at, "2026-10-01")
        self.assertEqual(sig.url, "https://dns.google/resolve?name=neros.tech&type=TXT")
        self.assertIs(sig.metrics["observed_only"], True)
        self.assertEqual(sig.metrics["domain_age_days"], 1132)
        self.assertEqual(sig.metrics["tool_groups"]["go_to_market"], ["Salesforce", "Zendesk"])
        self.assertEqual(sig.metrics["mail_provider"], "microsoft_365_gcc_high_or_dod")
        check_title(self, sig.title)

    def test_go_to_market_and_hiring_stack(self):
        self.assertEqual(self.tools(TXT_BASE_POWER), ["HubSpot", "Stripe", "Greenhouse", "Autodesk"])

    def test_gov_and_commercial_smartsheet_are_told_apart(self):
        self.assertEqual(self.tools(["smartsheet-gov-site-validation=x"]), ["Smartsheet Gov"])
        self.assertEqual(self.tools(["smartsheet-site-validation=x"]), ["Smartsheet"])

    def test_lookalike_records_do_not_match(self):
        # A prefix must start the record, an SPF suffix must end a whole host label.
        self.assertEqual(self.tools(["note about stripe-verification=x",
                                     "v=spf1 include:notmail.zendesk.com.evil.example -all",
                                     "v=spf1 include:fakehubspotemail.net -all"]), [])
        self.assertEqual(self.tools([" rippling-domain-verification=f804444",
                                     "v=spf1 include:22822034.spf03.hubspotemail.net -all"]),
                         ["HubSpot", "Rippling"])

    def test_strength_stays_routine(self):
        every = [p + "x" for t in df.TOOLS for p in t[3]]
        every.append("v=spf1 " + " ".join("include:" + s for t in df.TOOLS for s in t[4]) + " -all")
        sig = df.tooling_signal(SKILD, "skild.ai", [], every, TODAY)
        self.assertEqual(sig.value, len(df.TOOLS))
        self.assertLessEqual(sig.strength, 0.3)
        self.assertGreaterEqual(df.tooling_signal(SKILD, "s.ai", [], ["stripe-verification=x"], TODAY).strength, 0.15)
        check_title(self, sig.title)
        self.assertIn("including", sig.title)


class GovCloud(unittest.TestCase):
    def test_gcc_high_mail_says_so_plainly(self):
        sig = df.govcloud_signal(NEROS, "neros.tech", mx(MX_NEROS), ns(NS_NEROS), txt(TXT_NEROS), TODAY, 1132)
        sig.validate()
        self.assertEqual(sig.kind, "dns_govcloud")
        self.assertEqual(sig.title, "Email runs on Microsoft 365 GCC High or DoD (MX at office365.us), "
                                    "a US government cloud for controlled data")
        self.assertEqual(sig.url, "https://dns.google/resolve?name=neros.tech&type=MX")
        self.assertEqual(sig.occurred_at, "2026-10-01")
        self.assertIs(sig.metrics["observed_only"], True)
        self.assertIs(sig.metrics["gcc_high_mx"], True)
        self.assertIsNone(sig.metrics["govcloud_ns"])
        self.assertEqual(sig.metrics["mx_hosts"], ["neros.mail.protection.office365.us"])
        self.assertEqual(sig.strength, 0.4)  # 0.35 plus 0.05 for a domain under five years old
        check_title(self, sig.title)

    def test_old_domain_gets_no_youth_premium(self):
        sig = df.govcloud_signal(NEROS, "neros.tech", mx(MX_NEROS), [], [], TODAY, 8161)
        self.assertEqual(sig.strength, 0.35)
        self.assertEqual(df.govcloud_signal(NEROS, "neros.tech", mx(MX_NEROS), [], [], TODAY).strength, 0.35)

    def test_mail_and_nameservers_together(self):
        castelion_ns = df.ns_hosts(PANEL["castelion.com"]["dns"]["ns"])
        sig = df.govcloud_signal({"name": "Castelion"}, "castelion.com",
                                 ["castelion.mail.protection.office365.us"], castelion_ns, [], TODAY, 1183)
        self.assertEqual(sig.title, "Email on Microsoft 365 GCC High or DoD and DNS on Azure Government, "
                                    "both US government clouds")
        self.assertEqual(sig.metrics["govcloud_ns"], "Azure Government")
        self.assertEqual(set(sig.metrics["evidence"]), {"mx", "ns"})
        self.assertEqual(sig.strength, 0.45)
        check_title(self, sig.title)

    def test_nameservers_only(self):
        azure = df.govcloud_signal({"name": "Aalo Atomics"}, "aalo.com",
                                   ["mx1-us.emailsecurity.app"], ns(NS_AALO), [], TODAY, 8953)
        self.assertEqual(azure.title, "DNS is hosted on Azure Government (azuregov-dns.us nameservers), "
                                      "Microsoft's US government cloud")
        self.assertEqual(azure.url, "https://dns.google/resolve?name=aalo.com&type=NS")
        self.assertEqual(azure.strength, 0.3)
        aws = df.govcloud_signal({"name": "Allen Control Systems"}, "allencontrolsystems.com",
                                 [], ns(NS_ALLEN), [], TODAY)
        self.assertEqual(aws.title, "DNS is hosted on AWS GovCloud (awsdns-us-gov nameservers), "
                                    "Amazon's US government cloud")
        for sig in (azure, aws):
            check_title(self, sig.title)

    def test_spf_only_is_the_weakest_form(self):
        sig = df.govcloud_signal(NEROS, "neros.tech", ["mxa.gslb.gpphosted.com"], [],
                                 ["v=spf1 include:spf.protection.office365.us -all"], TODAY)
        self.assertIn("SPF record authorises Microsoft 365 GCC High or DoD", sig.title)
        self.assertEqual(sig.url, "https://dns.google/resolve?name=neros.tech&type=TXT")
        self.assertEqual(sig.strength, 0.25)
        check_title(self, sig.title)

    def test_undated_state_never_outranks_a_dated_step(self):
        castelion_ns = df.ns_hosts(PANEL["castelion.com"]["dns"]["ns"])
        strongest = df.govcloud_signal({"name": "Castelion"}, "castelion.com",
                                       ["castelion.mail.protection.office365.us"], castelion_ns,
                                       ["v=spf1 include:spf.protection.office365.us -all"], TODAY, 10)
        self.assertLessEqual(strongest.strength, df.REGISTERED_STRENGTH)

    def test_commercial_setup_is_silent(self):
        self.assertIsNone(df.govcloud_signal(SKILD, "skild.ai", mx(MX_SKILD),
                                             ["garrett.ns.cloudflare.com"], txt(TXT_SKILD), TODAY))

    def all_forms(self) -> list:
        castelion_ns = df.ns_hosts(PANEL["castelion.com"]["dns"]["ns"])
        gcc_mx = ["x.mail.protection.office365.us"]
        return [
            df.govcloud_signal(NEROS, "neros.tech", gcc_mx, castelion_ns, [], TODAY),   # mail and DNS
            df.govcloud_signal(NEROS, "neros.tech", gcc_mx, [], [], TODAY),             # mail
            df.govcloud_signal(NEROS, "neros.tech", [], ns(NS_AALO), [], TODAY),        # Azure Government
            df.govcloud_signal(NEROS, "neros.tech", [], ns(NS_ALLEN), [], TODAY),       # AWS GovCloud
            df.govcloud_signal(NEROS, "neros.tech", [], [],
                               ["v=spf1 include:spf.protection.office365.us -all"], TODAY),  # SPF only
        ]

    def test_title_says_who_the_customer_is_not_what_the_company_builds(self):
        # The title is classified with the rest of the entity's text. Radiant Industries
        # (a reactor company) and Apex Space (satellite buses) were both being filed under
        # defense because the title called GCC High "the cloud for controlled defense data".
        for sig in self.all_forms():
            got = classify(sig.title)
            # "DoD" is part of the cloud's name and counts as context. Nothing stronger.
            self.assertIn(got["terms"], ([], ["dod"]), sig.title)
            self.assertLess(got["fit"], 0.3, sig.title)
            for about, sector in (("Portable nuclear microreactor", "energy"),
                                  ("Satellite buses for low earth orbit", "space"),
                                  ("Humanoid robot for warehouses", "robotics")):
                self.assertEqual(classify(f"{about} \n {sig.title}")["sector"],
                                 classify(about)["sector"], sig.title)
                self.assertEqual(classify(about)["sector"], sector)

    def test_tooling_titles_carry_no_thesis_term(self):
        every = [p + "x" for t in df.TOOLS for p in t[3]]
        every.append("v=spf1 " + " ".join("include:" + s for t in df.TOOLS for s in t[4]) + " -all")
        sig = df.tooling_signal(SKILD, "skild.ai", [], every, TODAY)
        self.assertEqual(classify(" ".join(sig.metrics["tools"]) + " " + sig.title)["terms"], [])

    def test_panel_gcc_high_set(self):
        # The 22 domains the source card lists as GCC High, from the panel's MX providers.
        want = {d for d, row in PANEL.items()
                if "microsoft_365_gcc_high_or_dod" in row["dns"]["mx_provider"]}
        self.assertEqual(len(want), 22)
        self.assertIn("neros.tech", want)
        self.assertNotIn("skild.ai", want)


class Labels(unittest.TestCase):
    def test_vocabulary_tokens(self):
        self.assertEqual(df.token_class("api"), "developer")
        self.assertEqual(df.token_class("cell1"), "site")
        self.assertEqual(df.token_class("cella"), "site")
        self.assertEqual(df.token_class("f3"), "site")
        self.assertEqual(df.token_class("il5"), "government")
        self.assertEqual(df.token_class("01"), "number")
        for unknown in ("pike", "lavos", "nguyen", "f", "fx", "apia", "deva", ""):
            self.assertIsNone(df.token_class(unknown), unknown)

    def test_label_needs_every_token_known(self):
        self.assertEqual(df.label_class("web-console"), "product")
        self.assertEqual(df.label_class("staging-configurator"), "commerce")
        self.assertEqual(df.label_class("cmm-cell1"), "site")
        self.assertEqual(df.label_class("us-gov"), "government")
        self.assertIsNone(df.label_class("jane-doe"))
        self.assertIsNone(df.label_class("atlas-api"))
        self.assertIsNone(df.label_class("01"))

    def test_shown_label_keeps_only_the_safe_suffix(self):
        d = "hadrian.co"
        self.assertEqual(df.shown_label("lavos.cmm-cell1.f3.hadrian.co", d), ("cmm-cell1.f3", "site"))
        self.assertEqual(df.shown_label("*.f2.hadrian.co", d), ("f2", "site"))
        self.assertEqual(df.shown_label("docs.hadrian.co", d), ("docs", "developer"))
        self.assertEqual(df.shown_label("www.pay.neros.tech", "neros.tech"), ("pay", "commerce"))

    def test_person_like_and_unknown_names_are_never_shown(self):
        d = "example.co"
        for name in ("*.jane-doe.vcluster.example.co", "jane.example.co", "pike.example.co",
                     "example.co", "www.example.co", "*.example.co", "docs.other.com",
                     "docs.notexample.co"):
            self.assertIsNone(df.shown_label(name, d), name)
        # A vocabulary label left of an unknown one is not promoted past it.
        self.assertIsNone(df.shown_label("api.jane-doe.example.co", d))


class Certificates(unittest.TestCase):
    def summary(self, today: date = TODAY) -> dict:
        return df.parse_issuances(copy.deepcopy(NEROS_CT), "neros.tech", today - timedelta(days=120), today)

    def test_fixture_summary(self):
        s = self.summary()
        self.assertEqual(s["issuances_visible"], 14)
        self.assertEqual(s["names_visible"], 7)      # air, web-console, staging-configurator, git, pay, pike, product-tables
        self.assertEqual(s["names_in_window"], 6)    # air was issued in January
        got = {v["label"]: (v["class"], v["issued"].isoformat(), v["fresh"]) for v in s["notable"]}
        self.assertEqual(got, {
            "pay": ("commerce", "2026-07-28", False),
            "staging-configurator": ("commerce", "2026-07-10", False),
            "web-console": ("product", "2026-07-09", False),
            "product-tables": ("product", "2026-10-01", True),
            "git": ("engineering", "2026-07-18", False),
        })
        self.assertEqual([v["label"] for v in s["notable"]][0], "pay")
        self.assertEqual(s["newest"], date(2026, 10, 1))

    def test_signal_matches_the_fixture(self):
        # Only product-tables has no certificate older than 30 days, so only it is claimed.
        sig = df.ct_signal(NEROS, "neros.tech", self.summary(), 120, TODAY, age=1132)
        sig.validate()
        self.assertEqual(sig.kind, "ct_new_subdomains")
        self.assertEqual(sig.title, "Hostname product-tables.neros.tech got a certificate on 2026-10-01 "
                                    "and has no older unexpired one")
        self.assertEqual((sig.value, sig.unit), (1, "subdomain"))
        self.assertEqual(sig.occurred_at, "2026-10-01")  # product-tables, issued 2026-10-01T21:13:28Z
        self.assertEqual(sig.url, "https://api.certspotter.com/v1/issuances?domain=neros.tech"
                                  "&include_subdomains=true&expand=dns_names&expand=issuer")
        self.assertEqual(sig.metrics["ct_notable_names"], 5)
        self.assertEqual(sig.metrics["ct_fresh_names"], 1)
        self.assertEqual(sig.metrics["ct_names_in_window"], 6)
        self.assertEqual(sig.metrics["domain_age_days"], 1132)
        self.assertEqual(sig.metrics["names"][0], {"label": "product-tables", "class": "product",
                                                   "issued": "2026-10-01", "issuer": "Let's Encrypt",
                                                   "fresh": True})
        self.assertEqual(len(sig.metrics["names"]), 5)
        self.assertNotIn("observed_only", sig.metrics)
        self.assertNotIn("rolling", sig.metrics)  # a dated event, one row per event
        self.assertNotIn("ct_listing_incomplete", sig.metrics)
        self.assertEqual(sig.strength, 0.46)
        check_title(self, sig.title)

    def test_without_a_fresh_name_it_is_an_inventory_at_routine_strength(self):
        day = date(2026, 9, 30)  # the day before product-tables was issued
        sig = df.ct_signal(NEROS, "neros.tech", self.summary(today=day), 120, day, age=1131)
        sig.validate()
        self.assertEqual(sig.kind, "ct_subdomains")
        self.assertEqual(sig.title, "Certificates issued in the last 120 days name 4 notable subdomains "
                                    "including pay and staging-configurator")
        self.assertEqual((sig.value, sig.unit), (4, "subdomains"))
        self.assertEqual(sig.metrics["ct_fresh_names"], 0)
        self.assertTrue(0.15 <= sig.strength <= 0.3)
        # An inventory whose date moves as certificates expire: one stored row per entity.
        self.assertIs(sig.metrics["rolling"], True)
        self.assertNotIn("subject", sig.metrics)
        self.assertNotIn("repo", sig.metrics)
        self.assertNotIn("observed_only", sig.metrics)  # it does have a date of its own
        self.assertEqual(sig.occurred_at, "2026-07-28")
        check_title(self, sig.title)

    def test_title_never_claims_new_or_first(self):
        for day in (TODAY, date(2026, 9, 30)):
            title = df.ct_signal(NEROS, "neros.tech", self.summary(today=day), 120, day).title.lower()
            for word in ("new", "first", "launched", "opened"):
                self.assertNotIn(word, title)

    def test_fresh_needs_no_older_certificate_and_a_known_lifetime(self):
        def cert(name, issued, days=90):
            nb = date.fromisoformat(issued)
            out = {"dns_names": [name], "not_before": issued + "T00:00:00Z"}
            if days:
                out["not_after"] = (nb + timedelta(days=days)).isoformat() + "T00:00:00Z"
            return out

        certs = [
            cert("api.x.com", "2026-09-11"),                 # 20 days old, alone: fresh
            cert("app.x.com", "2026-07-21"), cert("app.x.com", "2026-09-19"),  # a renewal pair
            cert("docs.x.com", "2026-08-25"),                # alone but 37 days old
            cert("shop.x.com", "2026-09-11", days=45),       # 45-day certificate: the bar is 13 days
            cert("pay.x.com", "2026-09-20", days=45),        # 11 days old: fresh
            cert("status.x.com", "2026-09-25", days=0),      # lifetime unknown
            cert("portal.x.com", "2026-09-01"),              # 30 days: a renewal's predecessor has just expired
            cert("hub.x.com", "2026-09-03"),                 # 28 days: the oldest that still counts
        ]
        s = df.parse_issuances(certs, "x.com", SINCE, TODAY)
        self.assertEqual({v["label"] for v in s["notable"] if v["fresh"]}, {"api", "pay", "hub"})
        sig = df.ct_signal({"name": "X"}, "x.com", s, 120, TODAY)
        self.assertEqual(sig.kind, "ct_new_subdomains")
        self.assertEqual(sig.title, "No unexpired certificate older than 30 days for 3 subdomains: "
                                    "pay, hub and api")
        self.assertEqual(sig.occurred_at, "2026-09-20")
        self.assertEqual((sig.value, sig.unit), (3, "subdomains"))
        self.assertEqual(sig.metrics["ct_notable_names"], 8)
        self.assertEqual([n["label"] for n in sig.metrics["names"][:3]], ["pay", "hub", "api"])
        check_title(self, sig.title)

    def test_host_beneath_a_shown_label_is_not_called_that_hostname(self):
        # ad.corp.x.com is shown as "corp" (a name stays hidden), but no certificate names corp.x.com.
        certs = [{"dns_names": ["ad.corp.x.com", "*.ad.corp.x.com"], "not_before": "2026-09-20T00:00:00Z",
                  "not_after": "2026-12-19T00:00:00Z"}]
        s = df.parse_issuances(certs, "x.com", SINCE, TODAY)
        self.assertFalse(s["notable"][0]["exact"])
        sig = df.ct_signal({"name": "X"}, "x.com", s, 120, TODAY)
        self.assertEqual(sig.title, "A host under corp.x.com got a certificate on 2026-09-20, "
                                    "the oldest unexpired one there")
        check_title(self, sig.title)
        certs.append({"dns_names": ["corp.x.com"], "not_before": "2026-09-20T09:00:00Z",
                      "not_after": "2026-12-19T09:00:00Z"})
        s = df.parse_issuances(certs, "x.com", SINCE, TODAY)
        self.assertTrue(s["notable"][0]["exact"])

    def test_fresh_factory_build_out_is_the_strong_case(self):
        names = ["*.f3.x.co", "f3.x.co", "a.cmm-cell1.f3.x.co", "b.cnc-cell1.f3.x.co", "c.cnc-cell2.f3.x.co",
                 "*.f2.x.co", "gov.x.co"]
        certs = [{"dns_names": names, "not_before": "2026-09-15T00:00:00Z",
                  "not_after": "2026-12-14T00:00:00Z"}]
        sig = df.ct_signal({"name": "X"}, "x.co", df.parse_issuances(certs, "x.co", SINCE, TODAY), 120, TODAY)
        self.assertEqual(sig.kind, "ct_new_subdomains")
        self.assertEqual(sig.title, "No unexpired certificate older than 30 days for 6 subdomains "
                                    "including f2, f3, gov and cmm-cell1.f3")
        self.assertTrue(0.6 <= sig.strength <= 0.8, sig.strength)
        check_title(self, sig.title)

    def test_it_plumbing_alone_is_not_an_inventory(self):
        certs = [{"dns_names": ["mail.x.com", "vpn.x.com", "sso.x.com", "git.x.com"],
                  "not_before": "2026-07-10T00:00:00Z", "not_after": "2026-10-08T00:00:00Z"}]
        s = df.parse_issuances(certs, "x.com", SINCE, TODAY)
        self.assertEqual(len(s["notable"]), 4)
        self.assertIsNone(df.ct_signal({"name": "X"}, "x.com", s, 120, TODAY))

    def test_odd_certificate_rows_are_skipped_not_fatal(self):
        certs = [None, "x", {"dns_names": "api.x.com", "not_before": "2026-09-10T00:00:00Z"},
                 {"dns_names": ["api.x.com", 7], "issuer": "LE", "_page": "0",
                  "not_before": "2026-09-10T00:00:00Z", "not_after": "garbage"},
                 {"dns_names": ["docs.x.com"], "not_before": "1970-01-01T00:00:00Z"},
                 {"dns_names": ["app.x.com"], "not_before": "2031-01-01T00:00:00Z"}]
        s = df.parse_issuances(certs, "x.com", SINCE, TODAY)
        self.assertEqual([v["label"] for v in s["notable"]], ["api"])
        self.assertFalse(s["notable"][0]["fresh"])

    def test_unlisted_labels_never_reach_the_signal(self):
        # pike and air are real names on the certificates but not vocabulary.
        blob = json.dumps(df.ct_signal(NEROS, "neros.tech", self.summary(), 120, TODAY).to_row())
        self.assertNotIn("pike", blob)
        self.assertNotIn("air.", blob)

    def test_certificates_dated_after_the_scan_day_wait(self):
        s = self.summary(today=date(2026, 9, 30))
        self.assertNotIn("product-tables", [v["label"] for v in s["notable"]])
        self.assertEqual(s["issuances_visible"], 13)
        sig = df.ct_signal(NEROS, "neros.tech", s, 120, date(2026, 9, 30))
        self.assertEqual(sig.occurred_at, "2026-07-28")
        self.assertEqual(sig.value, 4)

    def test_occurred_at_is_always_inside_the_window(self):
        for day in (TODAY, date(2026, 9, 30), date(2026, 8, 1)):
            since = day - timedelta(days=120)
            sig = df.ct_signal(NEROS, "neros.tech", self.summary(today=day), 120, day)
            self.assertTrue(since.isoformat() <= sig.occurred_at <= day.isoformat(), sig.occurred_at)

    def test_names_whose_earliest_certificate_predates_the_window_are_left_out(self):
        certs = [
            {"dns_names": ["app.x.com"], "not_before": "2026-01-10T00:00:00Z", "revoked": False},
            {"dns_names": ["app.x.com"], "not_before": "2026-09-10T00:00:00Z", "revoked": False},
            {"dns_names": ["docs.x.com"], "not_before": "2026-09-12T00:00:00Z", "revoked": False},
            {"dns_names": ["status.x.com"], "not_before": "2026-09-20T00:00:00Z", "revoked": True},
            {"dns_names": ["api.x.com"], "not_before": None},
        ]
        s = df.parse_issuances(certs, "x.com", SINCE, TODAY)
        self.assertEqual([v["label"] for v in s["notable"]], ["docs"])
        self.assertEqual(s["issuances_visible"], 3)

    def test_nothing_notable_is_silent(self):
        certs = [{"dns_names": ["x.com", "www.x.com", "pike.x.com"], "not_before": "2026-09-10T00:00:00Z"}]
        s = df.parse_issuances(certs, "x.com", SINCE, TODAY)
        self.assertEqual(s["names_in_window"], 1)
        self.assertIsNone(df.ct_signal(NEROS, "x.com", s, 120, TODAY))
        self.assertIsNone(df.ct_signal(NEROS, "x.com", df.parse_issuances([], "x.com", SINCE, TODAY), 120, TODAY))

    def test_cut_off_listing_says_at_least(self):
        day = date(2026, 9, 30)
        sig = df.ct_signal(NEROS, "neros.tech", self.summary(today=day), 120, day, complete=False)
        self.assertIn("name at least 4 notable subdomains", sig.title)
        self.assertIs(sig.metrics["ct_listing_incomplete"], True)
        check_title(self, sig.title)
        certs = [{"dns_names": ["api.x.com", "docs.x.com"], "not_before": "2026-09-20T00:00:00Z",
                  "not_after": "2026-12-19T00:00:00Z"}]
        s = df.parse_issuances(certs, "x.com", SINCE, TODAY)
        sig = df.ct_signal({"name": "X"}, "x.com", s, 120, TODAY, complete=False)
        self.assertEqual(sig.title, "No unexpired certificate older than 30 days for at least 2 subdomains: "
                                    "api and docs")
        check_title(self, sig.title)

    def test_factory_hostnames_rank_first_and_nested_ones_count_less(self):
        certs = [
            {"dns_names": ["*.f2.x.co", "*.f3.x.co"], "not_before": "2026-08-01T00:00:00Z", "_page": 0},
            {"dns_names": ["lavos.cmm-cell1.f3.x.co", "mother-brain.cnc-cell1.f3.x.co"],
             "not_before": "2026-08-03T00:00:00Z", "_page": 0},
            {"dns_names": ["docs.x.co", "*.jane-doe.vcluster.x.co"], "not_before": "2026-08-05T00:00:00Z",
             "_page": 0},
        ]
        s = df.parse_issuances(certs, "x.co", SINCE, TODAY)
        labels = [v["label"] for v in s["notable"]]
        self.assertEqual(labels, ["f2", "f3", "docs", "cmm-cell1.f3", "cnc-cell1.f3"])
        self.assertEqual([v["nested"] for v in s["notable"]], [False, False, False, True, True])
        sig = df.ct_signal({"name": "X"}, "x.co", s, 120, TODAY)
        self.assertEqual(sig.title, "Certificates issued in the last 120 days name 5 notable subdomains "
                                    "including f2, f3, docs and cmm-cell1.f3")
        self.assertNotIn("jane", json.dumps(sig.to_row()))
        self.assertEqual(sig.metrics["ct_classes"], {"site": 4, "developer": 1})

    def test_evidence_link_is_the_page_holding_the_newest_certificate(self):
        certs = [
            {"dns_names": ["app.x.com"], "not_before": "2026-07-01T00:00:00Z", "_page": 0},
            {"dns_names": ["api.x.com"], "not_before": "2026-09-01T00:00:00Z", "_page": 1},
        ]
        s = df.parse_issuances(certs, "x.com", SINCE, TODAY)
        self.assertEqual(s["evidence_page"], 1)
        pages = [df.certspotter_url("x.com"), df.certspotter_url("x.com", after="123")]
        sig = df.ct_signal({"name": "X"}, "x.com", s, 120, TODAY, page_urls=pages)
        self.assertEqual(sig.url, pages[1])
        self.assertEqual(sig.occurred_at, "2026-09-01")
        self.assertEqual(sig.metrics["evidence_pages"], pages)
        # The name a reader will find on the linked page is listed first.
        self.assertTrue(sig.title.endswith("api and app"))


def _ct_headers(remaining: int, age_seconds: int = 0) -> dict:
    sent = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    return {"date": format_datetime(sent, usegmt=True), "x-ratelimit-limit": "10",
            "x-ratelimit-remaining": str(remaining)}


class FakeHttp:
    """Serves the fixtures by URL and records what was asked for."""

    def __init__(self, ct_status: int = 200, ct_remaining: int = 9, ct_age: int = 0,
                 ct_error: Exception | None = None):
        self.calls: list[str] = []
        self.ct_status = ct_status
        self.ct_remaining = ct_remaining
        self.ct_age = ct_age
        self.ct_error = ct_error

    def get_json(self, url: str, **kw):
        self.calls.append(url)
        if url == df.RDAP_BOOTSTRAP:
            return {"services": [[["ai"], ["https://rdap.identitydigital.services/rdap/"]],
                                 [["tech"], ["https://rdap.radix.host/rdap/"]]]}
        if url.endswith("/domain/skild.ai"):
            return RDAP_SKILD
        if url.endswith("/domain/neros.tech"):
            return {"events": [{"eventAction": "registration",
                                "eventDate": PANEL["neros.tech"]["rdap"]["registration"]}],
                    "nameservers": [{"ldhName": "ns17.domaincontrol.com"}]}
        if "/domain/" in url:
            raise http.HttpError(404, url)
        table = {
            df.doh_url("skild.ai", "TXT"): TXT_SKILD, df.doh_url("skild.ai", "MX"): MX_SKILD,
            df.doh_url("neros.tech", "TXT"): TXT_NEROS, df.doh_url("neros.tech", "MX"): MX_NEROS,
            df.doh_url("neros.tech", "NS"): NS_NEROS,
        }
        if url in table:
            return table[url]
        if url.startswith(df.DOH):
            return NXDOMAIN
        raise AssertionError(f"unexpected url {url}")

    def request(self, url: str, **kw):
        self.calls.append(url)
        if not url.startswith(df.CERTSPOTTER):
            raise AssertionError(f"unexpected url {url}")
        if self.ct_error is not None:
            raise self.ct_error
        if self.ct_status != 200:
            raise http.HttpError(self.ct_status, url)
        body = NEROS_CT if "domain=neros.tech" in url else []
        return json.dumps(body), _ct_headers(self.ct_remaining, self.ct_age)

    def ct_calls(self) -> list[str]:
        return [u for u in self.calls if u.startswith(df.CERTSPOTTER)]


def run_collect(fake: FakeHttp, known: list[dict], limit: int | None = None,
                clock: date = TODAY) -> tuple[list, Context]:
    """collect() on the fixtures. `clock` is the real calendar day the run happens on."""
    ctx = Context(today=TODAY, lookback_days=120, limit=limit, known=known)
    with mock.patch.object(df.http, "get_json", fake.get_json), \
            mock.patch.object(df.http, "request", fake.request), \
            mock.patch.object(df, "_clock_today", lambda: clock):
        signals = list(df.collect(ctx))
    return signals, ctx


class Collect(unittest.TestCase):
    KNOWN = [
        SKILD,
        NEROS,
        {"name": "Salem Robotics", "kind": "company", "domain": None, "prelim": 1.6},
        {"name": "Uni Lab", "kind": "project", "domain": "lab.stanford.edu", "prelim": 1.0},
        {"name": "Ghost", "kind": "company", "domain": "ghost-domain.tech", "prelim": 0.2},
        {"name": "A Researcher", "kind": "person", "domain": "employer.tech", "prelim": 2.0},
    ]

    def test_end_to_end_on_fixtures(self):
        fake = FakeHttp()
        signals, ctx = run_collect(fake, self.KNOWN)
        for s in signals:
            s.validate()
            self.assertEqual(s.source, "domain_footprint")
            self.assertEqual(s.family, "traffic")
            self.assertIn(s.family, FAMILIES)
            self.assertTrue(0.15 <= s.strength <= 0.8)
            check_title(self, s.title)
        got = [(s.entity.name, s.kind) for s in signals]
        self.assertEqual(got, [
            ("Neros Technologies", "dns_govcloud"),
            ("Neros Technologies", "dns_tooling"),
            ("Neros Technologies", "ct_new_subdomains"),
        ])
        # Every signal carries the domain age RDAP gave (2023-08-26 to 2026-10-01).
        self.assertEqual({s.metrics.get("domain_age_days") for s in signals}, {1132})
        self.assertEqual({s.entity.domain for s in signals}, {"neros.tech"})
        self.assertEqual(ctx.warnings, [])

    def test_entities_without_a_usable_domain_cost_no_requests(self):
        fake = FakeHttp()
        run_collect(fake, self.KNOWN)
        asked = " ".join(fake.calls)
        self.assertNotIn("stanford", asked)
        self.assertNotIn("salem", asked.lower())
        self.assertNotIn("employer.tech", asked)  # a person's domain is not their own

    def test_nameservers_are_only_asked_when_the_registry_cannot_say(self):
        fake = FakeHttp()
        run_collect(fake, self.KNOWN)
        self.assertNotIn(df.doh_url("skild.ai", "NS"), fake.calls)   # RDAP listed Cloudflare
        self.assertNotIn(df.doh_url("neros.tech", "NS"), fake.calls)
        # A domain that does not resolve is not asked for MX or NS at all.
        self.assertIn(df.doh_url("ghost-domain.tech", "TXT"), fake.calls)
        self.assertNotIn(df.doh_url("ghost-domain.tech", "MX"), fake.calls)

    def test_ct_goes_only_to_the_top_entities_by_prelim(self):
        fake = FakeHttp()
        with mock.patch.object(df, "CT_BUDGET", 1):
            signals, _ = run_collect(fake, self.KNOWN)
        self.assertEqual(fake.ct_calls(), [df.certspotter_url("skild.ai")])
        self.assertNotIn("ct_new_subdomains", [s.kind for s in signals])
        # KNOWN lists Skild before Neros, but with Neros ranked higher it gets the one lookup.
        known = [dict(SKILD, prelim=0.5), NEROS]
        fake = FakeHttp()
        with mock.patch.object(df, "CT_BUDGET", 1):
            signals, _ = run_collect(fake, known)
        self.assertEqual(fake.ct_calls(), [df.certspotter_url("neros.tech")])
        self.assertIn("ct_new_subdomains", [s.kind for s in signals])

    def test_stops_asking_when_the_quota_header_reaches_zero(self):
        fake = FakeHttp(ct_remaining=0)
        run_collect(fake, self.KNOWN)
        self.assertEqual(len(fake.ct_calls()), 1)

    def test_quota_error_stops_ct_but_keeps_everything_else(self):
        fake = FakeHttp(ct_status=429)
        signals, ctx = run_collect(fake, self.KNOWN)
        self.assertEqual([s.kind for s in signals], ["dns_govcloud", "dns_tooling"])
        self.assertEqual(len(fake.ct_calls()), 1)
        self.assertEqual(len(ctx.warnings), 1)
        self.assertIn("hourly quota spent", ctx.warnings[0])

    def test_default_budget_matches_the_brief(self):
        self.assertEqual(df.CT_BUDGET, 8)

    def test_limit_caps_the_entities_probed(self):
        fake = FakeHttp()
        signals, _ = run_collect(fake, self.KNOWN, limit=1)
        self.assertEqual(signals, [])  # only skild.ai, which has nothing to report
        self.assertFalse(any("neros.tech" in u for u in fake.calls))

    def test_a_failing_registry_does_not_lose_the_dns_signals(self):
        fake = FakeHttp()
        real = fake.get_json

        def flaky(url, **kw):
            if "/domain/neros.tech" in url:
                raise TimeoutError("registry timed out")
            return real(url, **kw)

        fake.get_json = flaky
        signals, ctx = run_collect(fake, self.KNOWN)
        self.assertEqual([s.kind for s in signals], ["dns_govcloud", "dns_tooling", "ct_new_subdomains"])
        self.assertTrue(all("domain_age_days" not in s.metrics for s in signals))
        self.assertTrue(any("RDAP TimeoutError" in w for w in ctx.warnings))

    def test_every_signal_is_dated_inside_the_window(self):
        signals, _ = run_collect(FakeHttp(), self.KNOWN)
        for s in signals:
            self.assertTrue(SINCE.isoformat() <= s.occurred_at <= TODAY.isoformat(), (s.kind, s.occurred_at))

    def test_a_run_dated_to_another_day_emits_no_present_state(self):
        # --today 2026-10-01 used three weeks later: the DNS and the unexpired certificates
        # read then are not those of 2026-10-01. Only a registry date survives the move.
        fake = FakeHttp()
        young = {"name": "Ghost", "kind": "company", "domain": "ghost.ai", "prelim": 0.2}
        real = fake.get_json

        def get_json(url, **kw):
            if url.endswith("/domain/ghost.ai"):
                return {"events": [{"eventAction": "registration", "eventDate": "2026-09-01T10:00:00Z"}]}
            return real(url, **kw)

        fake.get_json = get_json
        signals, ctx = run_collect(fake, self.KNOWN + [young], clock=TODAY + timedelta(days=21))
        self.assertEqual([(s.kind, s.occurred_at) for s in signals], [("domain_registered", "2026-09-01")])
        self.assertEqual(fake.ct_calls(), [])
        self.assertEqual(len(ctx.warnings), 1)
        self.assertIn("have no history", ctx.warnings[0])
        # A run that started before midnight UTC and is still going after it is the scan day.
        signals, ctx = run_collect(FakeHttp(), self.KNOWN, clock=TODAY + timedelta(days=1))
        self.assertIn("dns_govcloud", [s.kind for s in signals])
        self.assertEqual(ctx.warnings, [])

    def test_a_run_dated_ahead_of_the_clock_emits_no_present_state(self):
        # --today set to tomorrow: a record read now cannot be dated to a day that has not come.
        fake = FakeHttp()
        signals, ctx = run_collect(fake, self.KNOWN, clock=TODAY - timedelta(days=1))
        self.assertEqual(signals, [])
        self.assertEqual(fake.ct_calls(), [])
        self.assertIn("have no history", ctx.warnings[0])

    def test_the_scan_day_is_the_utc_day_like_the_run_date(self):
        before = datetime.now(timezone.utc).date()
        got = df._clock_today()
        self.assertIn(got, (before, datetime.now(timezone.utc).date()))

    def test_undated_readings_are_one_stored_row_that_each_run_refreshes(self):
        signals, _ = run_collect(FakeHttp(), self.KNOWN)
        by_kind = {s.kind: s for s in signals}
        self.assertEqual(len(by_kind), len(signals))  # never two of a kind for one domain
        tomorrow = TODAY + timedelta(days=1)
        for kind, later in (
            ("dns_govcloud", df.govcloud_signal(NEROS, "neros.tech", mx(MX_NEROS), ns(NS_AALO), [], tomorrow)),
            ("dns_tooling", df.tooling_signal(NEROS, "neros.tech", [], ["stripe-verification=x"], tomorrow)),
        ):
            # Another day, and a different record set behind it: still the same row.
            self.assertNotEqual((later.occurred_at, later.title),
                                (by_kind[kind].occurred_at, by_kind[kind].title))
            self.assertEqual(fingerprint(later), fingerprint(by_kind[kind]), kind)
        self.assertNotEqual(fingerprint(by_kind["dns_govcloud"]), fingerprint(by_kind["dns_tooling"]))
        # Another company's reading is another row.
        other = df.tooling_signal(SKILD, "skild.ai", [], ["stripe-verification=x"], TODAY)
        self.assertNotEqual(fingerprint(other), fingerprint(by_kind["dns_tooling"]))

    def test_certificate_inventory_is_one_stored_row_as_its_date_moves_forward(self):
        def cert(name, issued, days=90):
            nb = date.fromisoformat(issued)
            return {"dns_names": [name], "not_before": issued + "T00:00:00Z",
                    "not_after": (nb + timedelta(days=days)).isoformat() + "T00:00:00Z"}

        def inventory(certs, day):
            s = df.parse_issuances(certs, "x.com", day - timedelta(days=120), day)
            return df.ct_signal({"name": "X"}, "x.com", s, 120, day)

        old, renewal = cert("app.x.com", "2026-07-10"), cert("app.x.com", "2026-09-08")
        first = inventory([old, renewal], TODAY)
        # Eight days on the July certificate has expired and left the listing.
        later_day = date(2026, 10, 9)
        later = inventory([renewal], later_day)
        for sig in (first, later):
            sig.validate()
            self.assertEqual(sig.kind, "ct_subdomains")
            self.assertIs(sig.metrics["rolling"], True)
        self.assertEqual((first.occurred_at, later.occurred_at), ("2026-07-10", "2026-09-08"))
        self.assertEqual(fingerprint(first), fingerprint(later))
        # A second notable name does not make a second row either.
        grown = inventory([renewal, cert("docs.x.com", "2026-08-30")], later_day)
        self.assertEqual((grown.kind, grown.value), ("ct_subdomains", 2))
        self.assertEqual(fingerprint(grown), fingerprint(first))
        # Another company's inventory is another row, and so is another kind on the same domain.
        s = df.parse_issuances([renewal], "x.com", SINCE, TODAY)
        self.assertNotEqual(fingerprint(df.ct_signal({"name": "Y"}, "y.com", s, 120, TODAY)),
                            fingerprint(first))
        fresh = inventory([cert("app.x.com", "2026-09-20")], TODAY)
        self.assertEqual(fresh.kind, "ct_new_subdomains")
        self.assertNotEqual(fingerprint(fresh), fingerprint(first))

    def test_dated_events_stay_one_row_per_event(self):
        # A first-looking certificate or a registration has a date of its own: a later one is a new row.
        def ct(certs, d=TODAY):
            return df.ct_signal(NEROS, "neros.tech",
                                df.parse_issuances(copy.deepcopy(certs), "neros.tech",
                                                   d - timedelta(days=120), d), 120, d)

        def cert(name, issued):
            nb = date.fromisoformat(issued)
            return {"dns_names": [name], "not_before": issued + "T00:00:00Z",
                    "not_after": (nb + timedelta(days=90)).isoformat() + "T00:00:00Z"}

        self.assertEqual(ct(NEROS_CT).kind, "ct_new_subdomains")
        self.assertNotIn("observed_only", ct(NEROS_CT).metrics)
        self.assertNotIn("rolling", ct(NEROS_CT).metrics)
        self.assertEqual(ct(NEROS_CT).series, [])
        self.assertEqual(fingerprint(ct(NEROS_CT)), fingerprint(ct(NEROS_CT)))
        # The same event read again the next day is the same row.
        self.assertEqual(fingerprint(ct(NEROS_CT, TODAY + timedelta(days=1))), fingerprint(ct(NEROS_CT)))
        one, two = ct([cert("pay.neros.tech", "2026-09-12")]), ct([cert("docs.neros.tech", "2026-09-25")])
        self.assertEqual((one.kind, two.kind), ("ct_new_subdomains", "ct_new_subdomains"))
        self.assertEqual(one.url, two.url)
        self.assertNotEqual(fingerprint(one), fingerprint(two))  # a later fresh name is a new event
        rdap = {"registered": date(2026, 9, 1), "expires": None, "registrar": None, "nameservers": []}
        url = "https://rdap.radix.host/rdap/domain/neros.tech"
        first = df.registered_signal(NEROS, "neros.tech", rdap, url, TODAY, SINCE)
        again = df.registered_signal(NEROS, "neros.tech", rdap, url, TODAY + timedelta(days=1), SINCE)
        self.assertNotIn("observed_only", first.metrics)
        self.assertEqual(fingerprint(first), fingerprint(again))  # same event seen on a later run

    def test_someone_elses_domain_is_never_probed(self):
        known = [{"name": "Spot Demo", "kind": "project", "domain": "neros.tech", "github": "jdoe",
                  "prelim": 3.0}]
        fake = FakeHttp()
        signals, _ = run_collect(fake, known)
        self.assertEqual(signals, [])
        self.assertEqual(fake.calls, [])

    def test_odd_known_rows_do_not_break_the_run(self):
        known = [None, "neros.tech", {"name": None, "domain": "neros.tech"},
                 {"name": "Neros", "domain": 42}, {"name": "Neros", "domain": ["neros.tech"]},
                 {"name": "Neros &amp; Co", "domain": "https://www.neros.tech/about", "prelim": "high",
                  "github": "https://github.com/neros-tech", "kind": "startup"}]
        signals, ctx = run_collect(FakeHttp(), known)
        self.assertEqual([s.kind for s in signals], ["dns_govcloud", "dns_tooling", "ct_new_subdomains"])
        for s in signals:
            self.assertEqual((s.entity.name, s.entity.domain, s.entity.github, s.entity.kind),
                             ("Neros & Co", "neros.tech", "neros-tech", "company"))
        self.assertEqual(ctx.warnings, [])

    def test_cert_spotter_down_is_given_up_on_after_two_lookups(self):
        known = [dict(NEROS, name=f"Neros {i}", domain=f"neros{i}.tech", prelim=5 - i) for i in range(5)]
        fake = FakeHttp(ct_error=TimeoutError("timed out"))
        signals, ctx = run_collect(fake, known)
        # Each lookup is tried twice (the API often answers the second time), then no more.
        self.assertEqual(len(fake.ct_calls()), 2 * df.CT_MAX_FAILURES)
        self.assertEqual(sum("Cert Spotter TimeoutError" in w for w in ctx.warnings), df.CT_MAX_FAILURES)
        self.assertTrue(any("no more certificate lookups" in w for w in ctx.warnings))

    def test_no_known_entities_is_a_clean_empty_run(self):
        fake = FakeHttp()
        signals, ctx = run_collect(fake, [])
        self.assertEqual(signals, [])
        self.assertEqual(fake.calls, [])


class CtPaging(unittest.TestCase):
    """_fetch_ct against a fake Cert Spotter that serves 100 issuances a page."""

    def serve(self, total: int, age: int = 0, remaining: int = 5):
        rows = [{"id": str(1000 + i), "dns_names": [f"app{i}.x.com"],
                 "not_before": "2026-09-01T00:00:00Z"} for i in range(total)]
        calls: list[str] = []

        def request(url, **kw):
            calls.append(url)
            start = 0
            if "&after=" in url:
                start = int(url.rsplit("&after=", 1)[1]) - 1000 + 1
            return json.dumps(rows[start:start + 100]), _ct_headers(remaining, age)

        return request, calls

    def fetch(self, request, budget: int):
        state = {"budget": budget, "quota_spent": False, "failures": 0}
        with mock.patch.object(df.http, "request", request):
            return df._fetch_ct("x.com", state), state

    def test_short_page_is_complete_in_one_request(self):
        request, calls = self.serve(47)
        (issuances, pages, complete, error), state = self.fetch(request, 8)
        self.assertEqual((len(issuances), len(pages), complete, error), (47, 1, True, None))
        self.assertEqual(state["budget"], 7)
        self.assertEqual(calls, [df.certspotter_url("x.com")])

    def test_full_page_is_followed_with_after(self):
        request, calls = self.serve(148)
        (issuances, pages, complete, _), state = self.fetch(request, 8)
        self.assertEqual((len(issuances), complete), (148, True))
        self.assertEqual(calls[1], df.certspotter_url("x.com") + "&after=1099")
        self.assertEqual([c["_page"] for c in issuances[98:102]], [0, 0, 1, 1])
        self.assertEqual(state["budget"], 6)

    def test_budget_running_out_mid_listing_reports_incomplete(self):
        request, calls = self.serve(148)
        (issuances, pages, complete, error), state = self.fetch(request, 1)
        self.assertEqual((len(issuances), len(pages), complete, error), (100, 1, False, None))
        self.assertEqual(len(calls), 1)
        self.assertEqual(state["budget"], 0)

    def test_page_cap_reports_incomplete(self):
        request, calls = self.serve(100 * df.CT_MAX_PAGES + 5)
        (issuances, _, complete, _), _ = self.fetch(request, 8)
        self.assertEqual(len(calls), df.CT_MAX_PAGES)
        self.assertFalse(complete)

    def test_cached_pages_cost_no_budget(self):
        request, _ = self.serve(148, age=3600, remaining=0)  # an hour-old Date header
        (issuances, _, complete, _), state = self.fetch(request, 8)
        self.assertEqual((len(issuances), complete), (148, True))
        self.assertEqual(state, {"budget": 8, "quota_spent": False, "failures": 0})

    def test_zero_quota_on_a_live_response_stops_the_run(self):
        request, calls = self.serve(148, remaining=0)
        (issuances, _, complete, _), state = self.fetch(request, 8)
        self.assertEqual((len(issuances), complete, len(calls)), (100, False, 1))
        self.assertTrue(state["quota_spent"])

    def test_failed_request_is_charged_to_the_budget(self):
        def request(url, **kw):
            raise http.HttpError(503, url)

        (issuances, pages, complete, error), state = self.fetch(request, 8)
        self.assertEqual((issuances, pages, complete, error), ([], [], False, "HTTP 503"))
        self.assertEqual((state["budget"], state["failures"], state["quota_spent"]), (7, 1, False))

    def test_unreadable_date_header_counts_as_a_live_request(self):
        self.assertTrue(df._from_network({}))
        self.assertTrue(df._from_network({"date": "not a date"}))
        self.assertTrue(df._from_network(_ct_headers(5)))
        self.assertFalse(df._from_network(_ct_headers(5, age_seconds=3600)))


if __name__ == "__main__":
    unittest.main()
