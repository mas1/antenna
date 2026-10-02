"""Offline tests for the fpds_ot collector: parsing, screening, wording, strength.

Everything runs against fixtures/capital/fpds_atom_dod_ot_new_page0.xml, a raw
FPDS ATOM page saved from the live feed. No network: http.get is replaced.
"""

from __future__ import annotations

import re
import unittest
from datetime import date, timedelta
from unittest import mock

from antenna.collectors import fpds_ot as F
from antenna.collectors.base import Context
from antenna.config import FIXTURES_DIR
from antenna.thesis import classify

FIXTURE = FIXTURES_DIR / "capital" / "fpds_atom_dod_ot_new_page0.xml"
TODAY = date(2026, 10, 1)
SINCE = TODAY - timedelta(days=F.MIN_LOOKBACK_DAYS)

RAW = FIXTURE.read_text()
ENTRIES = re.findall(r"<entry>.*?</entry>", RAW, flags=re.S)
EMPTY_FEED = '<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom"><title>none</title></feed>'


def feed_of(*entries: str, last: int | None = None) -> str:
    """A feed page holding the given raw <entry> blocks."""
    link = (f'<link rel="last" type="text/html" href="https://www.fpds.gov/ezsearch/FEEDS/ATOM?'
            f'q=x&amp;start={last}"></link>') if last is not None else ""
    return ('<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom">'
            f"<title>test</title>{link}{''.join(entries)}</feed>")


def entry_for(piid: str) -> str:
    return next(e for e in ENTRIES if f"<ns1:PIID>{piid}</ns1:PIID>" in e)


def load() -> dict[str, dict]:
    awards, _, _ = F.parse_feed(RAW)
    return {a["piid"]: a for a in awards}


CAILABS = "FA2401269B018"  # registered 2023-08-24, signed 2026-05-27, $449,000
HARPOON = "FA8307269B003"  # registered 2019-03-23, signed 2026-04-09, $208,000


class ParseTest(unittest.TestCase):
    def test_page_shape(self):
        awards, last, errors = F.parse_feed(RAW)
        self.assertEqual(len(awards), 10)
        self.assertEqual(last, 310)
        self.assertEqual(errors, [])
        self.assertEqual(len({a["piid"] for a in awards}), 10)

    def test_fields_of_first_entry(self):
        a = load()["FA24012690023"]
        self.assertEqual(a["vendor"], "BLUE ORIGIN, LLC")
        self.assertEqual(a["record_type"], "OtherTransactionAward")
        self.assertFalse(a["is_idv"])
        self.assertEqual(a["mod"], "0")
        self.assertEqual(a["signed"], date(2026, 6, 2))
        self.assertEqual(a["obligated"], 10000.0)
        self.assertEqual(a["ceiling"], 10000.0)
        self.assertEqual(a["uei"], "SFKWJAYSTL95")
        self.assertEqual(a["cage"], "89ZW9")
        self.assertEqual(a["sam_registered"], date(2019, 2, 5))
        self.assertEqual(a["office_id"], "FA2401")
        self.assertEqual(a["office_name"], "FA2401 SPACE DEVELOPMENT AGENCY SDA")
        self.assertEqual(a["agency_name"], "DEPT OF THE AIR FORCE")
        self.assertEqual(a["agreement_type"], "PROTOTYPE")
        self.assertEqual(a["description"], "SB-AMTI")
        self.assertEqual((a["city"], a["state"], a["country"]), ("MERRITT ISLAND", "FL", "USA"))
        self.assertFalse(a["consortium"])
        self.assertIn("isForProfitOrganization", a["flags"])

    def test_startup_entry(self):
        a = load()[CAILABS]
        self.assertEqual(a["vendor"], "CAILABS US INC.")
        self.assertEqual(a["signed"], date(2026, 5, 27))
        self.assertEqual(a["obligated"], 449000.0)
        self.assertEqual(a["sam_registered"], date(2023, 8, 24))
        self.assertEqual(a["created"], date(2026, 5, 28))  # typed into FPDS the day after signing
        self.assertEqual(a["uei"], "CR9PWV5FXR24")
        self.assertEqual(a["parent_uei"], "CR9PWV5FXR24")

    def test_ceiling_differs_from_obligation(self):
        a = load()["FA2553269B001"]  # Slingshot Aerospace
        self.assertEqual(a["obligated"], 7407604.33)
        self.assertEqual(a["ceiling"], 69226950.0)
        self.assertEqual(a["agreement_type"], "PRODUCTION")

    def test_bad_entry_does_not_lose_the_page(self):
        broken = "<entry><title>no content</title></entry>"
        awards, _, errors = F.parse_feed(feed_of(ENTRIES[0], broken, ENTRIES[1]))
        self.assertEqual(len(awards), 2)
        self.assertEqual(len(errors), 1)


class ScreenTest(unittest.TestCase):
    def test_fixture_reasons(self):
        got = {piid: F.screen(a, SINCE, TODAY) for piid, a in load().items()}
        self.assertEqual(got, {
            "FA24012690023": "prime_or_widely_known",      # Blue Origin
            "FA24012690024": "prime_or_widely_known",      # Boeing
            "FA24012690025": "sam_registration_too_old",   # Umbra Lab, registered 2016
            "FA24012690026": "prime_or_widely_known",      # Anduril
            "FA2401269B015": "prime_or_widely_known",      # L3Harris
            "FA2401269B018": None,                         # Cailabs US
            "FA2553269B001": "sam_registration_too_old",   # Slingshot, registered 2016
            "FA6643269B002": "prime_or_widely_known",      # Carahsoft
            "FA8003269B001": "sam_registration_too_old",   # IT Cadre, registered 2002
            "FA8307269B003": None,                         # Harpoon Corp
        })

    def variant(self, **changes) -> dict:
        return {**load()[CAILABS], **changes}

    def test_window(self):
        self.assertEqual(F.screen(self.variant(), date(2026, 6, 1), TODAY), "out_of_window")
        self.assertEqual(F.screen(self.variant(mod="P00001"), SINCE, TODAY), "modification")

    def test_consortium(self):
        self.assertEqual(F.screen(self.variant(consortium=True), SINCE, TODAY), "consortium")
        self.assertEqual(F.screen(self.variant(vendor="ONE NATION INNOVATION"), SINCE, TODAY), "consortium")

    def test_not_a_company(self):
        self.assertEqual(F.screen(self.variant(vendor="UNIVERSITY OF DAYTON"), SINCE, TODAY), "not_a_company")
        flagged = self.variant(flags=["isNonprofitOrganization"])
        self.assertEqual(F.screen(flagged, SINCE, TODAY), "not_a_company")
        person = self.variant(vendor="JANE Q PUBLIC", flags=["isSolePropreitorship"])
        self.assertEqual(F.screen(person, SINCE, TODAY), "not_a_company")
        single_member_llc = self.variant(flags=["isSolePropreitorship"])
        self.assertIsNone(F.screen(single_member_llc, SINCE, TODAY))

    def test_parent_on_stoplist(self):
        sub = self.variant(parent_name="THE BOEING COMPANY", parent_uei="NU2UC8MX6NK1")
        self.assertEqual(F.screen(sub, SINCE, TODAY), "prime_or_widely_known")

    def test_stoplist_matches_whole_words_only(self):
        self.assertIsNone(F.screen(self.variant(vendor="CUBICLE DYNAMICS INC"), SINCE, TODAY))
        self.assertEqual(F.screen(self.variant(vendor="CUBIC DEFENSE INC"), SINCE, TODAY), "prime_or_widely_known")

    def test_award_too_large(self):
        self.assertEqual(F.screen(self.variant(obligated=60_000_000.0), SINCE, TODAY), "award_too_large")

    def test_not_an_award(self):
        a = self.variant(description="AEDC VELOCITY ALLIANCE CONSORTIUM BASE MEMBERSHIP AGREEMENT - X")
        self.assertEqual(F.screen(a, SINCE, TODAY), "not_an_award")

    def test_off_thesis(self):
        bio = self.variant(description="ANALGESIC KETAMINE PRODUCT DEVELOPMENT")
        self.assertEqual(F.screen(bio, SINCE, TODAY), "off_thesis")
        # An off-thesis word does not sink a requirement that is firmly on thesis.
        medevac = self.variant(description="UNMANNED AIRCRAFT DRONE FOR MEDICAL RESUPPLY")
        self.assertIsNone(F.screen(medevac, SINCE, TODAY))

    def test_off_thesis_by_product_service_code(self):
        # Live cases: a motor repair shop and a natural-rubber seed supplier.
        repair = self.variant(description="REFURBISH FAN MOTORS", psc="J041",
                              psc_description="MAINT/REPAIR/REBUILD OF EQUIPMENT- REFRIGERATION, AIR CONDITIONING")
        self.assertEqual(F.screen(repair, SINCE, TODAY), "off_thesis")
        seeds = self.variant(description="NATURAL RUBBER", psc="8730", psc_description="SEEDS AND NURSERY STOCK")
        self.assertEqual(F.screen(seeds, SINCE, TODAY), "off_thesis")
        # Hardware and R&D codes are untouched.
        self.assertIsNone(F.screen(self.variant(psc="1550", psc_description="UNMANNED AIRCRAFT"), SINCE, TODAY))
        self.assertIsNone(F.screen(self.variant(psc="AC13"), SINCE, TODAY))
        self.assertIsNone(F.screen(self.variant(psc=None, psc_description=None), SINCE, TODAY))
        # A repair code does not sink work that is firmly on thesis.
        drones = self.variant(description="REPAIR OF UNMANNED AIRCRAFT DRONE AUTOPILOT", psc="J015")
        self.assertIsNone(F.screen(drones, SINCE, TODAY))

    def test_hired_experts_are_not_a_prototype(self):
        smes = self.variant(description="PROCUREMENT OF SMES FOR GAUNTLET EVENTS.")
        self.assertEqual(F.screen(smes, SINCE, TODAY), "off_thesis")
        sme = self.variant(description="ELECTRONIC WARFARE SUBJECT MATTER EXPERT TRANSLATION AND TRAINING")
        self.assertEqual(F.screen(sme, SINCE, TODAY), "off_thesis")

    def test_investment_and_advisory_firms(self):
        self.assertEqual(F.screen(self.variant(vendor="RED CELL PARTNERS, LLC"), SINCE, TODAY), "not_a_company")
        self.assertEqual(F.screen(self.variant(vendor="NORTHSTAR CAPITAL LLC"), SINCE, TODAY), "not_a_company")
        self.assertIsNone(F.screen(self.variant(vendor="TESSERACT VENTURES LLC"), SINCE, TODAY))

    def test_late_stage_names_are_on_the_stoplist(self):
        for name in ("SARONIC TECHNOLOGIES, INC", "DEFENSE UNICORNS, INC.", "SHIELD AI INC."):
            self.assertEqual(F.screen(self.variant(vendor=name), SINCE, TODAY), "prime_or_widely_known", name)
        # 'GECKO ROBOTICS' is listed; a namesake arm is left to the history check.
        self.assertIsNone(F.screen(self.variant(vendor="GECKO USG, LLC"), SINCE, TODAY))

    def test_dates_must_be_real_and_inside_the_window(self):
        self.assertEqual(F.screen(self.variant(signed=None), SINCE, TODAY), "out_of_window")
        self.assertEqual(F.screen(self.variant(signed=TODAY + timedelta(days=1)), SINCE, TODAY), "out_of_window")
        self.assertEqual(F.screen(self.variant(signed=SINCE - timedelta(days=1)), SINCE, TODAY), "out_of_window")
        self.assertEqual(F.screen(self.variant(sam_registered=None), SINCE, TODAY), "no_sam_date")
        after = self.variant(sam_registered=date(2026, 6, 1))  # registered after signing: not usable
        self.assertEqual(F.screen(after, SINCE, TODAY), "no_sam_date")
        self.assertEqual(F.screen(self.variant(uei=None), SINCE, TODAY), "incomplete")

    def test_unfunded_needs_its_own_thesis_words(self):
        paper = self.variant(obligated=0.0, ceiling=0.0, description="VENDOR LICENSE AGREEMENT")
        self.assertEqual(F.screen(paper, SINCE, TODAY), "unfunded_and_off_thesis")
        drones = self.variant(obligated=0.0, ceiling=0.0, description="GROUND DRONES PURCHASED FOR R&D")
        self.assertIsNone(F.screen(drones, SINCE, TODAY))

    def test_unfunded_is_kept_when_the_requirement_names_a_product(self):
        # Live: two drone makers' agreements after an Army prize competition. The
        # only thesis word is "unmanned", in the product-service code, which the
        # classifier alone holds under the gate.
        mta = self.variant(obligated=0.0, ceiling=0.0, psc="1550", psc_description="UNMANNED AIRCRAFT",
                           description="MATERIAL TRANSFER AGREEMENT PROTOTYPE PROJECT OT FOLLOWING XTECH EDGE STRIKE "
                                       "GROUND PRIZE COMPETITION. KESTREL SYSTEM")
        self.assertLess(classify(" ".join([mta["vendor"], F.requirement_text(mta)]))["fit"], 0.3)
        self.assertIsNone(F.screen(mta, SINCE, TODAY))
        self.assertGreaterEqual(classify(F.build_signal(mta, None, TODAY).text)["fit"], 0.3)
        # A context word is not a product, and a vendor's name is not the requirement.
        vague = self.variant(obligated=0.0, ceiling=0.0, description="TACTICAL VENDOR LICENSE AGREEMENT")
        self.assertEqual(F.screen(vague, SINCE, TODAY), "unfunded_and_off_thesis")
        named = self.variant(obligated=0.0, ceiling=0.0, vendor="FUSION WORKS LLC", vendor_alt=None,
                             description="VENDOR LICENSE AGREEMENT")
        self.assertEqual(F.screen(named, SINCE, TODAY), "unfunded_and_off_thesis")


class WordingTest(unittest.TestCase):
    def test_money(self):
        self.assertEqual(F.fmt_usd(498700), "$499K")
        self.assertEqual(F.fmt_usd(999_900), "$1M")
        self.assertEqual(F.fmt_usd(1_229_415), "$1.2M")
        self.assertEqual(F.fmt_usd(10_000_000), "$10M")
        self.assertEqual(F.fmt_usd(28_536_386), "$28.5M")
        self.assertEqual(F.fmt_usd(5000), "$5K")
        self.assertEqual(F.fmt_usd(750), "$750")

    def test_age(self):
        self.assertEqual(F.fmt_age(43), "43 days")
        self.assertEqual(F.fmt_age(320), "11 months")
        self.assertEqual(F.fmt_age(1248), "3.4 years")

    def test_buyer(self):
        a = load()[CAILABS]
        self.assertEqual(F.buyer_label(a), "the Space Development Agency")
        self.assertEqual(F.buyer_label(load()[HARPOON]), "the Air Force")
        self.assertEqual(F.buyer_label({**a, "office_id": "HQ0845", "office_name": "DIRECTOR"}), "DIU")
        self.assertEqual(F.buyer_label({**a, "office_id": "HR0011", "office_name": "DEF ADVANCED RESEARCH PROJECTS AGCY",
                                        "agency_name": "DEFENSE ADVANCED RESEARCH PROJECTS AGENCY  (DARPA)"}), "DARPA")
        self.assertEqual(F.buyer_label({**a, "office_id": "X", "office_name": "X", "agency_name": "SOMETHING NEW"}),
                         "the Department of Defense")

    def test_titles(self):
        a = load()[HARPOON]
        self.assertEqual(F.make_title(a, first=False),
                         "Won $208K prototype OT from the Air Force, 7.0 years after SAM registration")
        diu = {**a, "office_id": "HQ0845", "obligated": 498700.0, "ceiling": 498700.0,
               "signed": date(2026, 6, 1), "sam_registered": date(2025, 7, 16)}
        self.assertEqual(F.make_title(diu, first=True),
                         "First federal contract on record: $499K prototype OT from DIU, 11 months after SAM registration")
        capped = {**diu, "obligated": 5000.0, "ceiling": 30_000_000.0}
        self.assertEqual(F.make_title(capped, first=False),
                         "Won $5K prototype OT from DIU, $30M ceiling, 11 months after SAM registration")
        unfunded = {**diu, "obligated": 0.0, "ceiling": 0.0}
        self.assertEqual(F.make_title(unfunded, first=False),
                         "Signed an unfunded prototype OT with DIU, 11 months after SAM registration")
        ceiling_only = {**diu, "obligated": 0.0, "ceiling": 30_300_000.0, "agreement_type": "PRODUCTION"}
        self.assertEqual(F.make_title(ceiling_only, first=False),
                         "Signed a production OT with DIU, $0 obligated against a $30.3M ceiling, 11 months after SAM registration")
        self.assertEqual(F.make_title(ceiling_only, first=True),
                         "First federal contract on record: production OT with DIU, $0 obligated against a $30.3M ceiling")
        self.assertEqual(F.make_title(unfunded, first=True),
                         "First federal contract on record: unfunded prototype OT with DIU, 11 months after SAM registration")

    def test_agreement_type_is_never_guessed(self):
        # Only PROTOTYPE and PRODUCTION occur today. Anything else must not be
        # called a prototype.
        a = {**load()[HARPOON], "agreement_type": "RESEARCH"}
        self.assertEqual(F.make_title(a, first=False),
                         "Won $208K OT from the Air Force, 7.0 years after SAM registration")
        self.assertEqual(F.make_title({**a, "agreement_type": None}, first=True),
                         "First federal contract on record: $208K OT from the Air Force, 7.0 years after SAM registration")
        ceiling_only = {**a, "obligated": 0.0, "ceiling": 2_000_000.0}
        self.assertTrue(F.make_title(ceiling_only, first=False).startswith("Signed an OT with the Air Force"))

    def test_titles_obey_the_rules(self):
        base = load()[CAILABS]
        buyers = [("HQ0845", "DIRECTOR", "IMMEDIATE OFFICE OF THE SECRETARY OF DEFENSE"),
                  ("HY0233", "DIRECTOR SCO", "IMMEDIATE OFFICE OF THE SECRETARY OF DEFENSE"),
                  ("HC1084", "X", "DEFENSE INFORMATION SYSTEMS AGENCY (DISA)"),
                  ("W519TC", "W6QK ACC-RI", "DEPT OF THE ARMY")]
        for office_id, office_name, agency in buyers:
            for obligated, ceiling in [(0.0, 0.0), (0.0, 123_456_789.0), (17_444_750.05, 19_744_499.0),
                                       (575_000.0, 49_000_000.0), (999.0, 999.0)]:
                for first in (True, False):
                    for kind in ("PROTOTYPE", "PRODUCTION", None):
                        a = {**base, "office_id": office_id, "office_name": office_name, "agency_name": agency,
                             "obligated": obligated, "ceiling": ceiling, "agreement_type": kind}
                        t = F.make_title(a, first)
                        self.assertLess(len(t), 110, t)
                        self.assertFalse(t.endswith("."), t)
                        self.assertTrue(t[0].isupper(), t)
                        self.assertTrue(any(ch.isdigit() for ch in t), t)
                        self.assertNotIn("fpds", t.lower())
                        self.assertNotIn("  ", t)
                        # Nothing is cut mid-word to fit.
                        self.assertRegex(t, r"(registration|ceiling|OT (from|with) .+)$")


class StrengthTest(unittest.TestCase):
    def award(self, obligated, age_days, ceiling=None):
        signed = date(2026, 6, 1)
        return {"obligated": obligated, "ceiling": obligated if ceiling is None else ceiling,
                "signed": signed, "sam_registered": signed - timedelta(days=age_days)}

    def test_scale(self):
        first_new = F.strength_of(self.award(1_500_000, 300), first=True)
        first_small = F.strength_of(self.award(70_000, 120), first=True)
        repeat_new = F.strength_of(self.award(1_500_000, 300), first=False, prior_count=3)
        repeat_mid = F.strength_of(self.award(1_500_000, 4 * 365), first=False, prior_count=3)
        old_hand = F.strength_of(self.award(30_000, 7 * 365), first=False, prior_count=300)
        self.assertGreaterEqual(first_new, 0.85)          # rare
        self.assertTrue(0.6 <= first_small < 0.85)        # notable
        self.assertTrue(0.35 <= repeat_new <= 0.6)        # solid
        self.assertTrue(0.35 <= repeat_mid <= 0.55)
        self.assertLess(repeat_mid, repeat_new)
        self.assertTrue(0.15 <= old_hand <= 0.3)          # routine

    def test_unfunded_is_capped(self):
        self.assertLessEqual(F.strength_of(self.award(0.0, 20), first=True), 0.30)
        self.assertGreaterEqual(F.strength_of(self.award(0.0, 2900), first=False), 0.15)

    def test_ceiling_is_intent_not_money(self):
        placeholder = F.strength_of(self.award(5_000, 400, ceiling=30_000_000), first=False)
        real = F.strength_of(self.award(6_000_000, 400), first=False)
        self.assertLess(placeholder, real)

    def test_bounds(self):
        for obligated in (0.0, 1.0, 1e4, 1e6, 5e7):
            for age in (0, 100, 600, 1500, 2920):
                for first in (True, False):
                    for prior in (None, 0, 500):
                        s = F.strength_of(self.award(obligated, age), first, prior)
                        self.assertTrue(0.0 <= s <= 1.0)


class HistoryTest(unittest.TestCase):
    """The first-award flag must never come from an empty response."""

    def run_history(self, responder):
        a = load()[CAILABS]
        calls = []

        def fake_get(url, **kw):
            calls.append(kw["params"])
            return responder(kw["params"])

        with mock.patch.object(F.http, "get", fake_get):
            return F.vendor_history(a), calls

    def test_first_award(self):
        own = entry_for(CAILABS)

        def responder(params):
            through_signing_day = "2026/05/27]" in params["q"]
            return feed_of(own) if through_signing_day else EMPTY_FEED

        history, calls = self.run_history(responder)
        self.assertEqual(history, {"prior": (0, True), "first": True, "re_registered": False, "namesake": None})
        self.assertEqual([c["q"].split(":")[0] for c in calls],
                         ["VENDOR_UEI", "VENDOR_UEI", "VENDOR_FULL_NAME", "VENDOR_NAME"])
        self.assertIn('VENDOR_UEI:"CR9PWV5FXR24" SIGNED_DATE:[1980/01/01,2026/05/26]', calls[0]["q"])
        # The last question is about the brand, not the legal entity: "US INC." is left out.
        self.assertEqual(calls[3]["q"], 'VENDOR_NAME:"CAILABS" SIGNED_DATE:[1980/01/01,2026/05/27]')
        self.assertEqual((calls[3]["sortBy"], calls[3]["desc"]), ("SIGNED_DATE", "N"))

    def test_repeat_vendor(self):
        history, calls = self.run_history(lambda params: feed_of(*ENTRIES[:3]))
        self.assertEqual(history, {"prior": (3, True), "first": False, "re_registered": False, "namesake": None})
        self.assertEqual(len(calls), 1)
        history, _ = self.run_history(lambda params: feed_of(*ENTRIES, last=330))
        self.assertEqual(history["prior"], (331, False))

    def test_empty_response_is_not_a_first_award(self):
        with self.assertRaises(ValueError):
            self.run_history(lambda params: EMPTY_FEED)

    def test_empty_response_is_asked_again_past_the_cache(self):
        own = entry_for(CAILABS)
        ttls = []

        def fake_get(url, **kw):
            q = kw["params"]["q"]
            ttls.append((q.split(":")[0], kw["ttl"]))
            if "2026/05/27]" not in q:
                return EMPTY_FEED            # nothing before the signing day
            if q.startswith("VENDOR_NAME") and kw["ttl"] > 0:
                return EMPTY_FEED            # the hiccup, as it would sit in the cache
            return feed_of(own)

        with mock.patch.object(F.http, "get", fake_get):
            history = F.vendor_history(load()[CAILABS])
        self.assertTrue(history["first"])
        self.assertEqual(ttls[-2:], [("VENDOR_NAME", F.HISTORY_TTL), ("VENDOR_NAME", 0)])

    def test_brand_words(self):
        self.assertEqual(F.brand_words("GECKO USG, LLC"), ["GECKO"])
        self.assertEqual(F.brand_words("HDT ROBOTICS LLC"), ["HDT", "ROBOTICS"])
        self.assertEqual(F.brand_words("Q-CTRL INC"), ["Q", "CTRL"])
        self.assertEqual(F.brand_words("CAILABS US INC."), ["CAILABS"])
        self.assertEqual(F.brand_words("QUANDELA FEDERAL INC"), ["QUANDELA"])
        self.assertEqual(F.brand_words("HONDOQ NORTH AMERICA LLC"), ["HONDOQ"])
        self.assertEqual(F.brand_words("THE WHISKEY PROJECT GROUP USA LLC"), ["WHISKEY", "PROJECT"])
        self.assertEqual(F.brand_words("PRINCIPAL MINERAL CO. INC."), ["PRINCIPAL", "MINERAL"])
        self.assertEqual(F.brand_words("S1 INDUSTRIES PBC, INCORPORATED"), ["S1", "INDUSTRIES"])
        self.assertEqual(F.brand_words("COHERE TECHNOLOGIES, INC."), ["COHERE", "TECHNOLOGIES"])
        # Connectives are not in the FPDS index; a clause on one would match nothing.
        self.assertEqual(F.brand_words("TRUSTED SCIENCE AND TECHNOLOGY, INC."), ["TRUSTED", "SCIENCE", "TECHNOLOGY"])
        self.assertEqual(F.brand_words("INK TO THINK LLC"), ["INK", "THINK"])
        self.assertEqual(F.brand_words("GROUP LLC"), ["GROUP"])  # never empty
        self.assertEqual(F.brand_words(""), [])

    def namesake_history(self, older_name):
        """Cailabs US with an older award to `older_name` under another UEI."""
        own = entry_for(CAILABS)
        older = (own.replace("<ns1:signedDate>2026-05-27", "<ns1:signedDate>2020-09-29")
                    .replace("<ns1:UEI>CR9PWV5FXR24", "<ns1:UEI>KLNKULR7C373")
                    .replace("<ns1:vendorName>CAILABS US INC.", f"<ns1:vendorName>{older_name}")
                    .replace("FA2401269B018", "80NSSC20P2391"))

        def responder(params):
            if params["q"].startswith("VENDOR_NAME"):
                return feed_of(older, own)
            return feed_of(own) if "2026/05/27]" in params["q"] else EMPTY_FEED

        return self.run_history(responder)[0]

    def test_older_award_to_the_same_brand_is_not_a_first_award(self):
        # Live case: CAILABS (the French parent) won a NASA order in 2020,
        # under a different UEI and a different legal name.
        history = self.namesake_history("CAILABS")
        self.assertEqual(history, {"prior": (0, True), "first": False, "re_registered": False,
                                   "namesake": "CAILABS"})
        s = F.build_signal(load()[CAILABS], history, TODAY)
        self.assertEqual(s.kind, "ot_award")
        self.assertTrue(s.title.startswith("Won $449K prototype OT"), s.title)
        self.assertEqual(s.metrics["first_fpds_award"], 0)
        self.assertEqual(s.metrics["earlier_awards_to_similar_name"], "CAILABS")
        self.assertLess(s.strength, 0.6)

    def test_same_name_but_for_punctuation_is_a_re_registration(self):
        # Live case: 'FOSTECH, INC' in 2026 and 'FOSTECH, INC.' in 1996.
        history = self.namesake_history("CAILABS US, INC")
        self.assertFalse(history["first"])
        self.assertTrue(history["re_registered"])

    def test_same_name_under_another_uei_is_a_re_registration(self):
        own = entry_for(CAILABS)
        older = (own.replace("<ns1:signedDate>2026-05-27", "<ns1:signedDate>2019-03-01")
                    .replace("<ns1:UEI>CR9PWV5FXR24", "<ns1:UEI>OLDUEI000000")
                    .replace("FA2401269B018", "FA865019C0001"))

        def responder(params):
            if params["q"].startswith("VENDOR_FULL_NAME"):
                return feed_of(older, own)
            return feed_of(own) if "2026/05/27]" in params["q"] else EMPTY_FEED

        history, calls = self.run_history(responder)
        self.assertFalse(history["first"])
        self.assertTrue(history["re_registered"])
        self.assertEqual(calls[-1]["q"].split(":")[0], "VENDOR_FULL_NAME")  # settled, no brand search


class SignalTest(unittest.TestCase):
    def test_first_award_signal(self):
        a = load()[CAILABS]
        s = F.build_signal(a, {"prior": (0, True), "first": True, "re_registered": False}, TODAY)
        s.validate()
        self.assertEqual((s.source, s.family, s.kind), ("fpds_ot", "capital", "ot_first_award"))
        self.assertEqual(s.entity.name, "CAILABS US INC.")
        self.assertIsNone(s.entity.domain)
        self.assertEqual(s.entity.location, "Arlington, VA")
        self.assertEqual(s.occurred_at, "2026-05-27")
        self.assertEqual(s.value, 449000.0)
        self.assertEqual(s.unit, "USD")
        self.assertEqual(s.url, "https://www.fpds.gov/ezsearch/FEEDS/ATOM?FEEDNAME=PUBLIC"
                                "&q=PIID:%22FA2401269B018%22%20MODIFICATION_NUMBER:%220%22")
        self.assertTrue(s.title.startswith("First federal contract on record: $449K prototype OT from the Space"))
        self.assertNotIn("earlier_awards_to_similar_name", s.metrics)
        self.assertEqual(s.metrics["amount_usd"], 449000.0)
        self.assertEqual(s.metrics["first_fpds_award"], 1)
        self.assertEqual(s.metrics["prior_fpds_actions"], 0)
        self.assertEqual(s.metrics["sam_registered"], "2023-08-24")
        self.assertEqual(s.metrics["sam_registration_age_days"], 1007)
        self.assertEqual(s.metrics["uei"], "CR9PWV5FXR24")
        self.assertEqual(s.metrics["cage"], "9PXS5")
        self.assertEqual(s.metrics["public_at"], "2026-08-25")  # signed 2026-05-27, plus the 90-day embargo
        # The buyer as printed is kept; the text names it in plain words.
        self.assertEqual(s.metrics["contracting_office"], "FA2401")
        self.assertEqual(s.metrics["contracting_office_name"], "FA2401 SPACE DEVELOPMENT AGENCY SDA")
        self.assertEqual(s.metrics["contracting_agency"], "DEPT OF THE AIR FORCE")
        self.assertEqual(s.text, "THE OBJECTIVE OF THIS EFFORT IS TO PROVIDE A GROUND-BASED OPTICAL REFERENCE SOURCE "
                                 "MOON AS A SERVICE AND ASSOCIATED TRACKING SUPPORT. DoD prototype other transaction "
                                 "agreement signed by the Space Development Agency, part of the Air Force")

    def test_identifiers_are_uppercase(self):
        a = {**load()[CAILABS], "uei": "cr9pwv5fxr24", "cage": "9pxs5"}
        s = F.build_signal(a, None, TODAY)
        self.assertEqual((s.metrics["uei"], s.metrics["cage"]), ("CR9PWV5FXR24", "9PXS5"))
        self.assertNotIn("cage", F.build_signal({**a, "cage": None}, None, TODAY).metrics)

    def test_name_is_emitted_as_the_registry_gives_it(self):
        # Display casing and the legal suffix are the resolver's job now.
        for name in ("CAILABS US INC.", "THE WHISKEY PROJECT GROUP USA LLC", "Q-CTRL INC"):
            self.assertEqual(F.build_signal({**load()[CAILABS], "vendor": name}, None, TODAY).entity.name, name)

    def test_public_at_is_when_the_embargo_ended(self):
        a = load()[CAILABS]  # signed 2026-05-27, created 2026-05-28
        self.assertEqual(F.public_date(a, TODAY), date(2026, 8, 25))
        s = F.build_signal(a, None, TODAY)
        self.assertEqual((s.occurred_at, s.metrics["public_at"]), ("2026-05-27", "2026-08-25"))
        # The last day of the embargo and the first day out of it.
        self.assertEqual(F.public_date(a, date(2026, 8, 25)), date(2026, 8, 25))
        # Live case: Phalanx, signed 2026-03-12 and typed into FPDS on 2026-08-10.
        # Nobody could see it in June, when the embargo would have ended.
        late = {**a, "signed": date(2026, 3, 12), "created": date(2026, 8, 10)}
        self.assertEqual(F.public_date(late, TODAY), date(2026, 8, 10))
        # A record drafted before it was signed is still held from the signing date.
        self.assertEqual(F.public_date({**a, "created": date(2026, 5, 1)}, TODAY), date(2026, 8, 25))
        self.assertEqual(F.public_date({**a, "created": None}, TODAY), date(2026, 8, 25))

    def test_public_at_is_never_in_the_future(self):
        a = load()[CAILABS]
        # In the feed before the embargo would end: it was never held back, so
        # it has been public since it was entered, and not from a date still to come.
        inside = date(2026, 7, 1)
        self.assertEqual(F.public_date(a, inside), date(2026, 5, 28))
        self.assertEqual(F.public_date({**a, "created": None}, inside), date(2026, 5, 27))
        self.assertEqual(F.build_signal(a, None, inside).metrics["public_at"], "2026-05-28")
        for today in (date(2026, 5, 27), inside, date(2026, 8, 24), TODAY):
            for created in (None, date(2026, 5, 1), date(2026, 5, 28), date(2026, 9, 20), date(2027, 1, 1)):
                got = F.public_date({**a, "created": created}, today)
                self.assertLessEqual(got, today)
                self.assertGreaterEqual(got, a["signed"])

    def test_repeat_and_unknown_history(self):
        a = load()[HARPOON]
        repeat = F.build_signal(a, {"prior": (57, False), "first": False, "re_registered": False}, TODAY)
        repeat.validate()
        self.assertEqual(repeat.kind, "ot_award")
        self.assertEqual(repeat.metrics["prior_fpds_actions_at_least"], 57)
        self.assertEqual(repeat.metrics["first_fpds_award"], 0)
        unknown = F.build_signal(a, None, TODAY)
        self.assertEqual(unknown.kind, "ot_award")
        self.assertNotIn("first_fpds_award", unknown.metrics)
        self.assertFalse(unknown.title.startswith("First"))
        first = F.build_signal(a, {"prior": (0, True), "first": True, "re_registered": False}, TODAY)
        self.assertGreater(first.strength, repeat.strength)

    def test_location_is_not_shouted(self):
        a = load()[HARPOON]
        self.assertEqual(F.build_signal(a, None, TODAY).entity.location, "Menifee, CA")
        abroad = {**a, "city": "MALAGA", "state": None, "country": "AUS", "country_name": "AUSTRALIA"}
        self.assertEqual(F.build_signal(abroad, None, TODAY).entity.location, "Malaga, Australia")
        self.assertIsNone(F.build_signal({**a, "city": None}, None, TODAY).entity.location)
        self.assertEqual(F.build_signal({**a, "city": "MCLEAN", "state": "VA"}, None, TODAY).entity.location, "McLean, VA")
        self.assertEqual(F.build_signal({**a, "city": "Menifee"}, None, TODAY).entity.location, "Menifee, CA")

    def test_alias_only_when_it_differs(self):
        a = load()[HARPOON]
        self.assertEqual(F.build_signal(a, None, TODAY).entity.aliases, [])
        dba = F.build_signal({**a, "vendor": "CICERO TECHNOLOGIES, INC", "vendor_alt": "EUDIA"}, None, TODAY)
        self.assertEqual(dba.entity.aliases, ["EUDIA"])
        same = F.build_signal({**a, "vendor_alt": "HARPOON CORP"}, None, TODAY)
        self.assertEqual(same.entity.aliases, [])


class ThesisTextTest(unittest.TestCase):
    """Signal.text says who bought in words light enough that the product names the sector."""

    BUYERS = {
        "the Army": ("W519TC", "W6QK ACC-RI", "DEPT OF THE ARMY"),
        "the Navy": ("N00014", "OFFICE OF NAVAL RESEARCH", "DEPT OF THE NAVY"),
        "the Air Force": ("FA8307", "FA8307 AFLCMC HNCK HNC CYBER & NTR", "DEPT OF THE AIR FORCE"),
        "DARPA": ("HR0011", "DEF ADVANCED RESEARCH PROJECTS AGCY", "DEFENSE ADVANCED RESEARCH PROJECTS AGENCY  (DARPA)"),
        "SOCOM": ("H92419", "USSOCOM", "U.S. SPECIAL OPERATIONS COMMAND (USSOCOM)"),
        "DIU": ("HQ0845", "DIRECTOR", "IMMEDIATE OFFICE OF THE SECRETARY OF DEFENSE"),
        "CDAO": ("HQ0883", "OFFICE, CHIEF DIGITAL & AI OFFICER", "IMMEDIATE OFFICE OF THE SECRETARY OF DEFENSE"),
        "the Space Development Agency": ("FA2401", "FA2401 SPACE DEVELOPMENT AGENCY SDA", "DEPT OF THE AIR FORCE"),
        "Washington HQ Services": ("HQ0034", "WASHINGTON HEADQUARTERS SERVICES", "WASHINGTON HEADQUARTERS SERVICES (WHS)"),
        "the Office of the Secretary of Defense": ("HY0233", "DIRECTOR SCO", "IMMEDIATE OFFICE OF THE SECRETARY OF DEFENSE"),
        "the Missile Defense Agency": ("HQ0857", "MISSILE DEFENSE AGENCY (MDA)", "MISSILE DEFENSE AGENCY (MDA)"),
        "the Defense Logistics Agency": ("SP4701", "DCSO PHILADELPHIA", "DEFENSE LOGISTICS AGENCY"),
        "the Department of Defense": ("X", "SOME NEW OFFICE", "SOMETHING NEW"),
    }

    def award(self, buyer="the Army", **changes) -> dict:
        office_id, office_name, agency = self.BUYERS[buyer]
        return {**load()[CAILABS], "office_id": office_id, "office_name": office_name, "agency_name": agency,
                "psc": "AC13", "psc_description": "NATIONAL DEFENSE R&D SERVICES; DEPARTMENT OF DEFENSE - MILITARY; "
                                                  "EXPERIMENTAL DEVELOPMENT", **changes}

    def thesis(self, a: dict) -> dict:
        """As the pipeline classifies one signal: name, title and text together."""
        s = F.build_signal(a, None, TODAY)
        return classify(" ".join([s.entity.name, s.title, s.text]))

    def test_wording(self):
        tail = lambda buyer, **kw: F.award_text(self.award(buyer, description="BASE AWARD", **kw))
        self.assertEqual(tail("the Army"), "BASE AWARD. DoD prototype other transaction agreement signed by the Army")
        self.assertEqual(tail("DARPA", is_idv=True), "BASE AWARD. DoD prototype other transaction IDV signed by DARPA")
        self.assertEqual(tail("DIU", agreement_type="PRODUCTION"),
                         "BASE AWARD. DoD production other transaction agreement signed by DIU")
        self.assertEqual(tail("SOCOM", agreement_type=None), "BASE AWARD. DoD other transaction agreement signed by SOCOM")
        # An office inside a service is named with the service the record puts it under.
        self.assertEqual(tail("the Space Development Agency"), "BASE AWARD. DoD prototype other transaction agreement "
                                                              "signed by the Space Development Agency, part of the Air Force")
        self.assertEqual(tail("CDAO"), "BASE AWARD. DoD prototype other transaction agreement signed by CDAO")
        # The title's short name is spelled out, the way the thesis vocabulary has it.
        whs = self.award("Washington HQ Services", description="BASE AWARD")
        self.assertEqual(F.award_text(whs), "BASE AWARD. DoD prototype other transaction agreement signed by "
                                            "Washington Headquarters Services")
        self.assertIn("from Washington HQ Services", F.make_title(whs, first=False))
        self.assertEqual(tail("the Department of Defense"), "BASE AWARD. Department of Defense prototype other transaction agreement")
        # A signer the thesis vocabulary does not know: the department is written out.
        self.assertEqual(tail("the Space Development Agency", agency_name="SOMETHING NEW"),
                         "BASE AWARD. Department of Defense prototype other transaction agreement signed by "
                         "the Space Development Agency")
        self.assertEqual(F.award_text(self.award(description=None)), "DoD prototype other transaction agreement signed by the Army")

    def test_names_as_printed_stay_out_of_the_text(self):
        # "DEFENSE ADVANCED RESEARCH PROJECTS AGENCY" and "... SECRETARY OF DEFENSE"
        # would put the word defense on every DARPA and DIU row.
        for buyer in ("DARPA", "DIU", "the Army", "SOCOM", "CDAO"):
            text = F.award_text(self.award(buyer, description="BASE AWARD"))
            self.assertNotIn("defense", text.lower(), buyer)
            self.assertEqual(sorted(classify(text)["terms"]), sorted({"dod", buyer.removeprefix("the ").lower()}), buyer)
        text = F.award_text(self.award("Washington HQ Services", description="BASE AWARD"))
        self.assertNotIn("defense", text.lower())
        self.assertEqual(classify(text)["terms"], ["dod", "washington headquarters services"])

    def test_every_buyer_passes_on_the_buyer_alone(self):
        # The requirement says nothing ("BASE AWARD"); that the Pentagon bought it is
        # the evidence, and the classifier has to see it in at least two terms.
        for buyer in self.BUYERS:
            a = self.award(buyer, description="BASE AWARD")
            got = self.thesis(a)
            self.assertEqual(got["sector"], "defense", buyer)
            self.assertGreaterEqual(got["fit"], 0.3, buyer)
            self.assertGreaterEqual(len(got["terms"]), 2, buyer)
            self.assertIsNone(F.screen(a, SINCE, TODAY), buyer)

    def test_the_product_names_the_sector(self):
        # Live requirements. One product word outweighs the buyer.
        cases = [
            ("DIU", "CONTAINERIZED AUTONOMOUS DRONE DELIVERY SYSTEM (CADDS)", "autonomy"),
            ("DARPA", "GROUND-AIR UNMANNED NETWORKED TEAMING AND LETHAL EFFECTOR TECHNOLOGY (GAUNTLET)", "autonomy"),
            ("DARPA", "MINIMIZING FLIGHT-QUALIFICATION TIME & COST THROUGH THE GENERATION A MULTI-ORGANIZATION TEST DATA "
                      "REPOSITORY USING A STANDARDIZED HALL EFFECT THRUSTER", "space"),
            ("DARPA", "LUNAR ASSAY VIA SMALL SATELLITE ORBITER (LASSO)", "space"),
            ("the Army", "SAMDIB DRAGON WINGS DISTRIBUTED SOLAR-GENERATOR", "energy"),
            ("the Navy", "UNMANNED WILDLAND FIREFIGHTING GROUND VEHICLE PROTOTYPE", "autonomy"),
            ("the Army", "COMPOSITE BASED ADDITIVE MANUFACTURING FOR SMALL UNMANNED AIR SYSTEMS IN SUPPORT OF THE "
                         "SKYFOUNDRY PROGRAM", "manufacturing"),
            ("SOCOM", "4023 TASK FORCE UNMANNED SYSTEM & ADVANCED WIRELESS NETWORKING", "autonomy"),
        ]
        for buyer, description, sector in cases:
            got = self.thesis(self.award(buyer, description=description))
            self.assertEqual(got["sector"], sector, (description, got))
        # A requirement that is itself about weapons is still defense.
        warhead = self.thesis(self.award(description="PROCUREMENT OF MMS WARHEAD FOR LUCAS", psc="1550",
                                         psc_description="UNMANNED AIRCRAFT"))
        self.assertEqual(warhead["sector"], "defense")


class CollectTest(unittest.TestCase):
    """collect() end to end with the saved page standing in for the feed."""

    def fake_get(self, fail_history=False, idv_feed=None):
        # Newest first, as the live feed returns it with sortBy=SIGNED_DATE.
        ordered = sorted(ENTRIES, key=lambda e: re.search(r"signedDate>([\d-]+)", e).group(1), reverse=True)
        # The IDV feed repeats one record the award feed already has, so it adds nothing.
        idv_feed = feed_of(ordered[0]) if idv_feed is None else idv_feed

        def get(url, **kw):
            q, start = kw["params"]["q"], kw["params"]["start"]
            if q.startswith("AWARD_TYPE"):
                if "OTHER TRANSACTION IDV" in q:
                    return idv_feed
                return feed_of(*ordered, last=0) if start == 0 else EMPTY_FEED
            if fail_history:
                raise OSError("connection reset")
            if "CR9PWV5FXR24" in q or "CAILABS" in q:  # no history before the award itself
                return feed_of(entry_for(CAILABS)) if "2026/05/27]" in q else EMPTY_FEED
            return feed_of(*ENTRIES, last=50)  # Harpoon: an established vendor
        return get

    def collect(self, ctx, **kw):
        with mock.patch.object(F.http, "get", self.fake_get(**kw)):
            return list(F.collect(ctx))

    def test_emits_the_two_startups(self):
        # A 30-day lookback is widened to 240 days, or the embargo would leave nothing.
        # (Offline, Cailabs US has no namesake. Live, FPDS shows a 2020 NASA order
        # to CAILABS, so the real run does not call this one a first award.)
        ctx = Context(today=TODAY, lookback_days=30)
        signals = self.collect(ctx)
        self.assertEqual([s.entity.name for s in signals], ["CAILABS US INC.", "HARPOON CORP."])
        self.assertEqual([s.kind for s in signals], ["ot_first_award", "ot_award"])
        self.assertEqual([s.occurred_at for s in signals], ["2026-05-27", "2026-04-09"])
        for s in signals:
            s.validate()
            self.assertGreaterEqual(s.occurred_at, (TODAY - timedelta(days=240)).isoformat())
            self.assertLessEqual(s.occurred_at, TODAY.isoformat())
        self.assertEqual(ctx.warnings, [])

    def test_limit(self):
        signals = self.collect(Context(today=TODAY, limit=1))
        self.assertEqual(len(signals), 1)

    def test_history_failure_keeps_the_award_without_the_claim(self):
        ctx = Context(today=TODAY)
        signals = self.collect(ctx, fail_history=True)
        self.assertEqual(len(signals), 2)
        self.assertEqual({s.kind for s in signals}, {"ot_award"})
        self.assertEqual(len(ctx.warnings), 2)
        for s in signals:
            self.assertFalse(s.title.startswith("First"), s.title)
            self.assertNotIn("first_fpds_award", s.metrics)

    def test_public_at_on_every_signal(self):
        for s in self.collect(Context(today=TODAY)):
            self.assertEqual(s.metrics["public_at"],
                             (date.fromisoformat(s.occurred_at) + timedelta(days=F.EMBARGO_DAYS)).isoformat())
            self.assertLessEqual(s.metrics["public_at"], TODAY.isoformat())

    def test_a_record_inside_the_embargo_is_reported(self):
        # On 2026-08-01 the fixture's awards (signed April to June) should all
        # still be under embargo. If the feed shows them anyway, say so.
        ctx = Context(today=date(2026, 8, 1))
        signals = self.collect(ctx)
        self.assertEqual([s.entity.name for s in signals], ["CAILABS US INC.", "HARPOON CORP."])
        self.assertEqual([s.metrics["public_at"] for s in signals], ["2026-05-28", "2026-07-08"])
        self.assertEqual(len(ctx.warnings), 1)
        self.assertIn("1 kept records were in the feed less than 90 days after signing", ctx.warnings[0])

    def test_a_feed_with_nothing_at_all_is_reported(self):
        ctx = Context(today=TODAY)
        signals = self.collect(ctx, idv_feed=EMPTY_FEED)
        self.assertEqual(len(signals), 2)
        self.assertEqual(len(ctx.warnings), 1)
        self.assertIn("OTHER TRANSACTION IDV feed returned no records at all", ctx.warnings[0])


class PagingTest(unittest.TestCase):
    """_pages must reach the last page, and must stop."""

    def pages(self, get):
        ctx = Context(today=TODAY)
        with mock.patch.object(F.http, "get", get):
            return [a["piid"] for a in F._pages(ctx, F.AWARD_TYPES[0], SINCE)], ctx

    def test_walks_to_the_last_page(self):
        starts = []

        def get(url, **kw):
            start = kw["params"]["start"]
            starts.append(start)
            return feed_of(*ENTRIES[start // 10 * 3:start // 10 * 3 + 3], last=20)

        piids, ctx = self.pages(get)
        self.assertEqual(starts, [0, 10, 20])
        self.assertEqual(len(piids), 9)
        self.assertEqual(ctx.warnings, [])

    def test_single_page_without_a_last_link(self):
        calls = []

        def get(url, **kw):
            calls.append(kw["params"]["start"])
            return feed_of(*ENTRIES[:4])

        piids, _ = self.pages(get)
        self.assertEqual((calls, len(piids)), ([0], 4))

    def test_cached_empty_page_is_fetched_again(self):
        seen = []

        def get(url, **kw):
            start, ttl = kw["params"]["start"], kw["ttl"]
            seen.append((start, ttl))
            if start == 10 and ttl > 0:
                return EMPTY_FEED  # the hiccup; before this fix the walk ended here
            return feed_of(*ENTRIES[start // 10 * 3:start // 10 * 3 + 3], last=20)

        piids, ctx = self.pages(get)
        self.assertEqual(seen, [(0, F.PAGE_TTL), (10, F.PAGE_TTL), (10, 0), (20, F.PAGE_TTL)])
        self.assertEqual(len(piids), 9)
        self.assertEqual(ctx.warnings, [])

    def test_empty_page_is_skipped_with_a_warning(self):
        def get(url, **kw):
            start = kw["params"]["start"]
            return EMPTY_FEED if start == 10 else feed_of(*ENTRIES[start // 10 * 3:start // 10 * 3 + 3], last=20)

        piids, ctx = self.pages(get)
        self.assertEqual(len(piids), 6)
        self.assertEqual(len(ctx.warnings), 1)

    def test_failures_are_bounded(self):
        calls = []

        def get(url, **kw):
            calls.append(kw["params"]["start"])
            if kw["params"]["start"] == 0:
                return feed_of(*ENTRIES[:3], last=5000)
            raise OSError("connection reset")

        piids, ctx = self.pages(get)
        self.assertEqual(len(piids), 3)
        self.assertEqual(len(calls), 1 + F.MAX_PAGE_FAILURES)
        self.assertEqual(len(ctx.warnings), F.MAX_PAGE_FAILURES)

    def test_dead_feed_gives_up(self):
        calls = []

        def get(url, **kw):
            calls.append(kw["params"]["start"])
            raise OSError("connection reset")

        piids, ctx = self.pages(get)
        self.assertEqual((piids, len(calls)), ([], F.MAX_PAGE_FAILURES))


if __name__ == "__main__":
    unittest.main()
