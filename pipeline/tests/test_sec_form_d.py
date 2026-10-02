"""Offline tests for the Form D collector: index parsing, XML parsing, funnel,
filing history, titles, strength and the collect() wiring. No network: the one
test that runs collect() swaps antenna.http for functions that serve fixtures.
"""

from __future__ import annotations

import copy
import json
import re
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from antenna import db, resolve, score
from antenna.collectors import sec_form_d as fd
from antenna.collectors.base import Context
from antenna.thesis import classify

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "capital"
IDX = (FIX / "edgar_daily_form_idx_20260930_excerpt.idx").read_text()
XML = (FIX / "edgar_formD_primary_doc.xml").read_text()
THESIS_SAMPLE = json.loads((FIX / "edgar_formD_parsed_thesis_sample.json").read_text())["records"]

FILED = date(2026, 9, 8)
ROW = {"form": "D", "company": "Salem Robotics, Inc.", "cik": "2134746",
       "filed": "2026-09-08", "accession": "0002134746-26-000002"}


def salem() -> dict:
    return fd.parse_primary_doc(XML)


def submissions(*filings: tuple) -> dict:
    """A data.sec.gov submissions document from (form, date, accession, accepted[, file number])."""
    return {"filings": {"files": [], "recent": {
        "form": [f[0] for f in filings],
        "filingDate": [f[1] for f in filings],
        "accessionNumber": [f[2] for f in filings],
        "acceptanceDateTime": [f[3] for f in filings],
        "fileNumber": [f[4] if len(f) > 4 else "" for f in filings],
    }}}


FIRST = submissions(("D", "2026-09-08", "0002134746-26-000002", "2026-09-08T16:01:00.000Z"))


class DailyIndex(unittest.TestCase):
    def test_parses_every_form_d_row(self):
        rows = fd.parse_form_idx(IDX)
        self.assertEqual(len(rows), 60)
        self.assertEqual(rows[0], {
            "form": "D", "company": "1World Online, Inc.", "cik": "1595734",
            "filed": "2026-09-30", "accession": "0001595734-26-000005",
        })
        self.assertTrue(all(r["form"] == "D" and r["filed"] == "2026-09-30" for r in rows))
        self.assertEqual(len({r["accession"] for r in rows}), 60)

    def test_form_type_is_matched_exactly(self):
        extra = (
            "D/A              Acme Robotics, Inc.                                           1234567     20260930    edgar/data/1234567/0001234567-26-000002.txt\n"
            "DEF 14A          Big Public Co                                                 7654321     20260930    edgar/data/7654321/0007654321-26-000009.txt\n"
            "DFAN14A          Activist Holder                                               7654322     20260930    edgar/data/7654322/0007654322-26-000001.txt\n"
        )
        rows = fd.parse_form_idx(IDX + extra)
        self.assertEqual(len(rows), 61)
        self.assertEqual(rows[-1]["form"], "D/A")
        self.assertEqual(rows[-1]["company"], "Acme Robotics, Inc.")
        self.assertEqual(rows[-1]["accession"], "0001234567-26-000002")

    def test_nothing_before_the_separator_is_a_row(self):
        self.assertEqual(fd.parse_form_idx("D   Fake Co   1   20260930   edgar/data/1/0000000001-26-000001.txt"), [])

    def test_name_prefilter_skips_vehicles_only(self):
        kept = [r["company"] for r in fd.parse_form_idx(IDX) if not fd.name_is_vehicle(r["company"])]
        self.assertEqual(kept, [
            "1World Online, Inc.", "ARTBnk Inc.",
            "Apogee Semiconductor, Inc.", "Autology, Inc.", "BZ Kitchens, PBC",
            "ClearOffice Inc.", "CuriousBox AI Inc.", "Dynagentic Inc.",
        ])
        for name in ("AI All Star Fund 2, L.P.", "ACRETRADER 298 LLC", "Centana CID SPV II, L.P.",
                     "AM-0901 Fund I, a series of Mute Ventures, LP", "Northwind DST",
                     "Jellicle Cast Album Ltd Liability Co", "Solar Investments Inc",
                     # seen live: financing shells and private-equity holding vehicles
                     "Alchemist Capital Offshore Ltd.", "Bash Capital1, Inc.", "MSE Acquisitions, Inc.",
                     "Kite Topco Inc.", "Quilt Holdco, Inc.", "Grit Parent, Inc."):
            self.assertTrue(fd.name_is_vehicle(name), name)
        # Words an operating company can carry must not trip the filter.
        for name in ("Offshore Robotics Inc", "Trust Machines Inc", "Master Dynamics Corp",
                     "LPG Power Inc", "ReitTech Inc", "Apex Partners Robotics, Inc.",
                     "ParentConnect Inc.", "Gasentec Holdings, Inc.", "Capitalize Robotics Inc"):
            self.assertFalse(fd.name_is_vehicle(name), name)

    def test_off_thesis_names(self):
        # Every one of these was emitted by the first version of this collector.
        for name in ("Penelope Health Inc.", "Real American Wrestling, Inc.", "Saltbox Tinned Fish Co., PBC",
                     "Happy Camper Distilling Co.", "Axiom Therapeutics Inc.", "Wilson & Pack Relocation Inc.",
                     "Alacriti Payments Inc.", "W4 Games US Inc.", "BZ Kitchens, PBC"):
            self.assertTrue(fd.name_is_off_thesis(name), name)
        # A name that is on thesis by its own words outranks the stop word, and
        # look-alikes do not trip it.
        for name in ("Apex Medical Robotics, Inc.", "Salem Robotics, Inc.", "Petra Systems Inc",
                     "ClinicFlow Inc.", "Healthy Grid Corp", "Gamesmith Robotics", "Mediate Inc",
                     "Orbital Satellite Media Inc"):
            self.assertFalse(fd.name_is_off_thesis(name), name)
        self.assertEqual([r["name"] for r in THESIS_SAMPLE if fd.name_is_off_thesis(r["name"])], [])

    def test_one_ambiguous_thesis_word_does_not_rescue_an_off_thesis_name(self):
        for name in ("Industrial Hemp Corp", "Fusion Foods Inc", "Volt Energy Drinks Inc",
                     # seen live; its legal form is not a thesis word
                     "DAVION HEALTHCARE PLC"):
            self.assertTrue(fd.name_is_off_thesis(name), name)
        # No stop word, no drop: these wait under the gate for another source.
        for name in ("Blue Laser Fusion, Inc.", "Antares Nuclear, Inc.", "Quaise Energy, Inc."):
            self.assertFalse(fd.name_is_off_thesis(name), name)

    def test_initials_alone_do_not_rescue_an_off_thesis_name(self):
        # Seen live in the 60 days to 2026-10-01: a bank holding company whose
        # initials are a thesis term. The classifier passes a lone "pcb"
        # (in running text it is a circuit board), so the name filter has to
        # hold the line itself.
        self.assertGreaterEqual(classify("PCB Financial, Inc")["fit"], 0.3)
        for name in ("PCB Financial, Inc", "CNC Brands Inc", "UAS Wellness Corp"):
            self.assertTrue(fd.name_is_off_thesis(name), name)
        # Initials beside a thesis word are the company's own words again, and
        # without a stop word the filter has no say at all.
        for name in ("PCB Robotics Medical Inc", "CNC Machining Sports Inc", "PCB Assembly Corp"):
            self.assertFalse(fd.name_is_off_thesis(name), name)

    def test_clean_name(self):
        cases = {
            "Cicero, Inc /DE/": ("Cicero, Inc", []),
            "Oncologic, Inc. / DE": ("Oncologic, Inc.", []),
            "Primis Inc. /DE": ("Primis Inc.", []),
            "Loamy Technologies, Inc,": ("Loamy Technologies, Inc", []),
            "Argo Energy Solutions, Inc., a Texas Corp": ("Argo Energy Solutions, Inc.", []),
            "VoiceIt Technologies, Inc dba EnQuanta": ("VoiceIt Technologies, Inc", ["EnQuanta"]),
            # left alone
            "Salem Robotics, Inc.": ("Salem Robotics, Inc.", []),
            "Novo Nordisk A/S": ("Novo Nordisk A/S", []),
            "Input/Output Inc": ("Input/Output Inc", []),
            "Build a Better Company": ("Build a Better Company", []),
            "Mary & Pip Co.": ("Mary & Pip Co.", []),
        }
        for raw, want in cases.items():
            self.assertEqual(fd.clean_name(raw), want, raw)
        self.assertEqual(fd.clean_name(None), (None, []))

    def test_name_prefilter_keeps_every_thesis_company_in_the_recon_sample(self):
        self.assertEqual(len(THESIS_SAMPLE), 23)
        self.assertEqual([r["name"] for r in THESIS_SAMPLE if fd.name_is_vehicle(r["name"])], [])

    def test_filing_urls(self):
        u = fd.filing_urls("2134746", "0002134746-26-000002")
        self.assertEqual(u["xml"], "https://www.sec.gov/Archives/edgar/data/2134746/000213474626000002/primary_doc.xml")
        self.assertEqual(u["page"], "https://www.sec.gov/Archives/edgar/data/2134746/000213474626000002/xslFormDX01/primary_doc.xml")
        self.assertEqual(u["index"], "https://www.sec.gov/Archives/edgar/data/2134746/000213474626000002/0002134746-26-000002-index.htm")

    def test_quarters_span_a_year_boundary(self):
        self.assertEqual(fd._quarters(date(2026, 11, 20), date(2027, 1, 5)), [(2026, 4), (2027, 1)])
        self.assertEqual(fd._quarters(date(2026, 8, 2), date(2026, 10, 1)), [(2026, 3), (2026, 4)])


class PrimaryDoc(unittest.TestCase):
    def test_parse_fixture(self):
        r = salem()
        self.assertEqual(r["form"], "D")
        self.assertEqual(r["cik"], "2134746")
        self.assertEqual(r["name"], "Salem Robotics, Inc.")
        self.assertEqual(r["entity_type"], "Corporation")
        self.assertEqual(r["year_inc"], 2024)
        self.assertEqual(r["industry"], "Other Technology")
        self.assertEqual(r["exemptions"], ["06b"])
        self.assertEqual(r["first_sale"], "2026-05-13")
        self.assertFalse(r["is_amendment"])
        self.assertEqual((r["total_offering"], r["amount_sold"], r["investors"]), (5_100_000, 1_819_500, 5))
        self.assertFalse(r["offering_indefinite"])
        self.assertEqual(r["minimum_investment"], 0)
        self.assertEqual(r["securities"]["other"], True)
        self.assertEqual(r["securities"]["equity"], False)
        self.assertEqual(r["other_security"], "Simple Agreement for Future Equity")
        self.assertEqual(r["previous_names"], [])  # the form says "None"
        self.assertEqual([p["name"] for p in r["people"]], ["Caleb Horan", "Janak Panthi"])
        self.assertEqual(r["people"][0]["relationships"], ["Executive Officer", "Director"])
        self.assertEqual((r["signer"], r["signer_title"]), ("Caleb Horan", "CEO"))

    def test_indefinite_offering_and_missing_year(self):
        x = XML.replace("<totalOfferingAmount>5100000</totalOfferingAmount>",
                        "<totalOfferingAmount>Indefinite</totalOfferingAmount>")
        x = re.sub(r"<yearOfInc>.*?</yearOfInc>",
                   "<yearOfInc><overFiveYears>true</overFiveYears></yearOfInc>", x, flags=re.S)
        r = fd.parse_primary_doc(x)
        self.assertTrue(r["offering_indefinite"])
        self.assertIsNone(r["total_offering"])
        self.assertIsNone(r["year_inc"])
        self.assertTrue(r["over_five_years"])

    def test_amendment_fields_and_previous_names(self):
        x = XML.replace("<submissionType>D</submissionType>", "<submissionType>D/A</submissionType>")
        x = x.replace("<isAmendment>false</isAmendment>",
                      "<isAmendment>true</isAmendment>"
                      "<previousAccessionNumber>0002134746-26-000001</previousAccessionNumber>")
        x = x.replace("<issuerPreviousNameList>\n            <value>None</value>",
                      "<issuerPreviousNameList>\n            <previousName>Salem Automation, Inc.</previousName>"
                      "<previousName>SALEM ROBOTICS, INC.</previousName>")
        r = fd.parse_primary_doc(x)
        self.assertEqual(r["form"], "D/A")
        self.assertTrue(r["is_amendment"])
        self.assertEqual(r["previous_accession"], "0002134746-26-000001")
        self.assertEqual(r["previous_names"], ["Salem Automation, Inc.", "SALEM ROBOTICS, INC."])
        self.assertEqual(r["edgar_previous_names"], [])
        # A former name that differs only in case is not an alias.
        self.assertEqual(fd.build_signal(r, {**ROW, "form": "D/A"}, None).entity.aliases,
                         ["Salem Automation, Inc."])

    def test_not_a_form_d(self):
        with self.assertRaises(ValueError):
            fd.parse_primary_doc("<edgarSubmission><x/></edgarSubmission>")


class Funnel(unittest.TestCase):
    def test_young_operating_corporation_passes(self):
        self.assertIsNone(fd.funnel(salem(), FILED))

    def test_drops(self):
        cases = {
            "industry": {"industry": "Pooled Investment Fund"},
            "real estate": {"industry": "Commercial"},
            "health": {"industry": "Biotechnology"},
            "llc": {"entity_type": "Limited Liability Company"},
            "lp": {"entity_type": "Limited Partnership"},
            "old": {"year_inc": None},
            "too old": {"year_inc": 2020},
            "future year": {"year_inc": 2027},
            "fund exemption": {"exemptions": ["06b", "3C", "3C.1"]},
            "tiny": {"total_offering": 110_000},
            "no amount": {"total_offering": None},
            "vehicle name": {"name": "Salem Robotics SPV Inc."},
            "off-thesis name": {"name": "Salem Therapeutics Inc."},
            "no name": {"name": None},
        }
        for label, change in cases.items():
            rec = {**salem(), **change}
            self.assertIsNotNone(fd.funnel(rec, FILED), label)

    def test_pooled_security_type_is_a_fund(self):
        rec = salem()
        rec["securities"]["pooled"] = True
        self.assertEqual(fd.funnel(rec, FILED), "pooled fund")

    def test_indefinite_offering_is_kept(self):
        rec = {**salem(), "offering_indefinite": True, "total_offering": None}
        self.assertIsNone(fd.funnel(rec, FILED))

    def test_five_year_window_follows_the_filing_year(self):
        self.assertIsNone(fd.funnel({**salem(), "year_inc": 2021}, FILED))
        self.assertIsNotNone(fd.funnel({**salem(), "year_inc": 2021}, date(2027, 1, 4)))


class History(unittest.TestCase):
    def test_first_filing(self):
        h = fd.summarize_history(FIRST, ROW["accession"], ROW["filed"])
        self.assertTrue(h["listed"])
        self.assertEqual(h["prior_form_d"], 0)
        self.assertIs(h["first_form_d"], True)
        self.assertIsNone(h["previous"])
        self.assertFalse(h["reporting"])
        self.assertFalse(h["crowdfunding"])

    def test_prior_filings_and_previous(self):
        sub = submissions(
            ("D", "2026-09-18", "0001213900-26-101361", "2026-09-18T15:20:27.000Z"),
            ("D/A", "2025-12-22", "0002074867-25-000005", "2025-12-22T10:00:00.000Z"),
            ("D", "2025-06-30", "0002074867-25-000001", "2025-06-30T10:00:00.000Z"),
        )
        h = fd.summarize_history(sub, "0001213900-26-101361", "2026-09-18")
        self.assertEqual(h["prior_form_d"], 2)
        self.assertIs(h["first_form_d"], False)
        self.assertEqual(h["previous"]["accession"], "0002074867-25-000005")
        self.assertEqual(h["previous"]["date"], "2025-12-22")

    def test_two_filings_the_same_day_are_ordered_by_acceptance_time(self):
        sub = submissions(
            ("D", "2026-09-16", "0002100000-26-000002", "2026-09-16T15:00:00.000Z"),
            ("D", "2026-09-16", "0002100000-26-000001", "2026-09-16T14:00:00.000Z"),
        )
        self.assertIs(fd.summarize_history(sub, "0002100000-26-000001", "2026-09-16")["first_form_d"], True)
        later = fd.summarize_history(sub, "0002100000-26-000002", "2026-09-16")
        self.assertIs(later["first_form_d"], False)
        self.assertEqual(later["prior_form_d"], 1)

    def test_a_list_without_the_filing_never_claims_a_first(self):
        stale = submissions()
        h = fd.summarize_history(stale, ROW["accession"], ROW["filed"])
        self.assertFalse(h["listed"])
        self.assertIsNone(h["first_form_d"])
        older = submissions(("D", "2025-01-10", "0002134746-25-000001", "2025-01-10T12:00:00.000Z"))
        self.assertIs(fd.summarize_history(older, ROW["accession"], ROW["filed"])["first_form_d"], False)

    def test_older_pages_mean_the_list_is_incomplete(self):
        sub = copy.deepcopy(FIRST)
        sub["filings"]["files"] = [{"name": "CIK0002134746-submissions-001.json"}]
        self.assertIsNone(fd.summarize_history(sub, ROW["accession"], ROW["filed"])["first_form_d"])

    def test_public_registrant_and_crowdfunding_flags(self):
        public = submissions(
            ("D", "2026-09-25", "0001193125-26-401614", "2026-09-25T07:44:52.000Z"),
            ("10-Q", "2026-08-14", "0001193125-26-300000", "2026-08-14T12:00:00.000Z"),
            ("4", "2026-09-09", "0001213900-26-098437", "2026-09-09T12:00:00.000Z"),
        )
        h = fd.summarize_history(public, "0001193125-26-401614", "2026-09-25")
        self.assertTrue(h["reporting"])
        self.assertFalse(h["crowdfunding"])
        self.assertIs(h["first_form_d"], True)
        crowd = submissions(
            ("D", "2026-09-28", "0002149393-26-000003", "2026-09-28T12:00:00.000Z"),
            ("C/A", "2026-03-01", "0002149393-26-000002", "2026-03-01T12:00:00.000Z"),
            ("C", "2026-02-01", "0002149393-26-000001", "2026-02-01T12:00:00.000Z"),
        )
        h = fd.summarize_history(crowd, "0002149393-26-000003", "2026-09-28")
        self.assertTrue(h["crowdfunding"])
        self.assertFalse(h["reporting"])


    def test_filer_since_is_the_oldest_filing_on_edgar(self):
        self.assertEqual(fd.summarize_history(FIRST, ROW["accession"], ROW["filed"])["filer_since"], 2026)
        old = submissions(
            ("D", "2026-09-10", "0001737630-26-000003", "2026-09-10T12:00:00.000Z"),
            ("D", "2018-04-16", "0001737630-18-000001", "2018-04-16T12:00:00.000Z"),
        )
        self.assertEqual(fd.summarize_history(old, "0001737630-26-000003", "2026-09-10")["filer_since"], 2018)
        paged = copy.deepcopy(FIRST)
        paged["filings"]["files"] = [{"name": "CIK-001.json", "filingFrom": "2009-03-02", "filingTo": "2019-01-01"}]
        self.assertEqual(fd.summarize_history(paged, ROW["accession"], ROW["filed"])["filer_since"], 2009)
        self.assertIsNone(fd.summarize_history(submissions(), ROW["accession"], ROW["filed"])["filer_since"])

    def test_history_drop(self):
        first = fd.summarize_history(FIRST, ROW["accession"], ROW["filed"])
        self.assertIsNone(fd.history_drop(first, FILED))
        self.assertIsNone(fd.history_drop(None, FILED))  # no history: nothing to hold against it
        self.assertEqual(fd.history_drop({**first, "reporting": True}, FILED), "public registrant")
        # Incorporated 2022 on the form, on EDGAR since 2018: a reincorporated incumbent.
        self.assertEqual(fd.history_drop({**first, "filer_since": 2018}, FILED), "on EDGAR since 2018")
        self.assertIsNone(fd.history_drop({**first, "filer_since": 2021}, FILED))

    def test_malformed_submissions_do_not_raise(self):
        for sub in ({}, {"filings": None}, {"filings": {"recent": None}}, [], {"filings": {"recent": {"form": ["D"]}}}):
            h = fd.summarize_history(sub, ROW["accession"], ROW["filed"])
            self.assertFalse(h["listed"])
            self.assertIsNone(h["first_form_d"])


class AmendmentBaseline(unittest.TestCase):
    """Which earlier filing an amendment's numbers are compared with."""

    # One Dosh Inc., CIK 2113647: the second amendment points at the original
    # notice ($2.4M) although the first amendment had already reported $3.4M.
    ONE_DOSH = submissions(
        ("D/A", "2026-09-25", "0002113647-26-000003", "2026-09-25T15:24:45.000Z", "021-574929"),
        ("D/A", "2026-06-18", "0002113647-26-000002", "2026-06-18T20:13:36.000Z", "021-574929"),
        ("D", "2026-02-27", "0002113647-26-000001", "2026-02-27T15:13:14.000Z", "021-574929"),
    )
    # Gilpin Health, CIK 2151911: a second, separate offering sits between
    # the amendment and the notice it amends.
    GILPIN = submissions(
        ("D/A", "2026-09-22", "0002151911-26-000003", "2026-09-21T22:33:09.000Z", "021-595329"),
        ("D", "2026-09-08", "0002151911-26-000002", "2026-09-08T19:28:13.000Z", "021-596699"),
        ("D", "2026-08-25", "0002151911-26-000001", "2026-08-25T20:30:15.000Z", "021-595329"),
    )

    def base(self, sub, accession, filed, pointer):
        hist = fd.summarize_history(sub, accession, filed)
        return fd.baseline_accession({"previous_accession": pointer}, hist, accession)

    def test_latest_filing_of_the_same_offering_beats_the_forms_pointer(self):
        self.assertEqual(self.base(self.ONE_DOSH, "0002113647-26-000003", "2026-09-25", "0002113647-26-000001"),
                         "0002113647-26-000002")

    def test_another_offering_in_between_is_skipped(self):
        self.assertEqual(self.base(self.GILPIN, "0002151911-26-000003", "2026-09-22", "0002151911-26-000001"),
                         "0002151911-26-000001")
        hist = fd.summarize_history(self.GILPIN, "0002151911-26-000003", "2026-09-22")
        self.assertEqual(hist["previous"]["accession"], "0002151911-26-000002")  # latest of any offering
        self.assertEqual(hist["previous_in_offering"]["accession"], "0002151911-26-000001")

    def test_without_file_numbers_only_the_immediately_preceding_filing_is_trusted(self):
        bare = submissions(*[f[:4] for f in zip(*[self.ONE_DOSH["filings"]["recent"][k] for k in
                                                   ("form", "filingDate", "accessionNumber", "acceptanceDateTime")])])
        self.assertEqual(self.base(bare, "0002113647-26-000003", "2026-09-25", "0002113647-26-000002"),
                         "0002113647-26-000002")
        # The pointer skips a filing and nothing ties it to this offering: no comparison.
        self.assertIsNone(self.base(bare, "0002113647-26-000003", "2026-09-25", "0002113647-26-000001"))

    def test_no_history_means_no_comparison(self):
        self.assertIsNone(fd.baseline_accession({"previous_accession": "0002113647-26-000001"}, None, "x"))
        stale = fd.summarize_history(submissions(), "0002113647-26-000003", "2026-09-25")
        self.assertIsNone(fd.baseline_accession({"previous_accession": "0002113647-26-000001"}, stale,
                                                "0002113647-26-000003"))

    def test_pointer_to_a_different_offering_is_refused(self):
        sub = submissions(
            ("D/A", "2026-09-22", "0002151911-26-000003", "2026-09-21T22:33:09.000Z", "021-595329"),
            ("D", "2026-09-08", "0002151911-26-000002", "2026-09-08T19:28:13.000Z", "021-596699"),
        )
        self.assertIsNone(self.base(sub, "0002151911-26-000003", "2026-09-22", "0002151911-26-000002"))


class Titles(unittest.TestCase):
    def setUp(self):
        self.first = fd.summarize_history(FIRST, ROW["accession"], ROW["filed"])

    def test_money(self):
        self.assertEqual(fd.money(1_819_500), "$1.8M")
        self.assertEqual(fd.money(525_000), "$525K")
        self.assertEqual(fd.money(52_500), "$52.5K")
        self.assertEqual(fd.money(999_700), "$1.0M")
        self.assertEqual(fd.money(7_199_952), "$7.2M")
        self.assertEqual(fd.money(3_110_000_000), "$3.11B")
        self.assertEqual(fd.money(800), "$800")

    def test_instrument_only_when_unambiguous(self):
        r = salem()
        self.assertEqual(fd.instrument(r), "SAFEs")
        r["other_security"] = "Convertible Notes"
        self.assertEqual(fd.instrument(r), "convertible notes")
        r["other_security"] = "Membership tokens"
        self.assertIsNone(fd.instrument(r))
        r["securities"].update(equity=True)  # equity plus something else: say nothing
        self.assertIsNone(fd.instrument(r))
        r["securities"].update(other=False)
        self.assertEqual(fd.instrument(r), "equity")
        r["securities"].update(equity=False, debt=True)
        self.assertEqual(fd.instrument(r), "debt")

    def test_right_to_acquire_box_alone_is_not_called_options_or_warrants(self):
        # The box reads "Option, Warrant or Other Right to Acquire Another
        # Security" and is how many filers report a SAFE.
        r = salem()
        r["securities"].update(other=False, option=True, underlying=True)
        r["other_security"] = None
        self.assertIsNone(fd.instrument(r))
        self.assertTrue(fd.rights_only(r))
        self.assertEqual(fd.make_title(r, self.first, None), "Filed first Form D: $1.8M sold of $5.1M from 5 investors")
        self.assertEqual(fd.make_title({**r, "amount_sold": 0}, self.first, None),
                         "Filed first Form D: $5.1M offering, nothing sold yet")
        self.assertEqual(fd.build_signal(r, ROW, self.first).metrics["instrument"], "option, warrant or other right")
        self.assertFalse(fd.rights_only(salem()))

    def test_first_form_d(self):
        self.assertEqual(fd.make_title(salem(), self.first, None),
                         "Filed first Form D: $1.8M sold of $5.1M in SAFEs from 5 investors")

    def test_unknown_or_later_filing_does_not_say_first(self):
        self.assertEqual(fd.make_title(salem(), None, None),
                         "Filed Form D: $1.8M sold of $5.1M in SAFEs from 5 investors")
        later = {**self.first, "first_form_d": False, "prior_form_d": 2}
        self.assertTrue(fd.make_title(salem(), later, None).startswith("Filed Form D: "))
        registrant = {**self.first, "reporting": True}
        self.assertTrue(fd.make_title(salem(), registrant, None).startswith("Filed Form D: "))

    def test_variants(self):
        r = {**salem(), "amount_sold": 0, "investors": 0}
        self.assertEqual(fd.make_title(r, self.first, None),
                         "Filed first Form D: $5.1M offering of SAFEs, nothing sold yet")
        r = {**salem(), "offering_indefinite": True, "total_offering": None, "investors": 1}
        self.assertEqual(fd.make_title(r, self.first, None),
                         "Filed first Form D: $1.8M sold of an indefinite offering in SAFEs from 1 investor")
        r = {**salem(), "amount_sold": 5_100_000}
        self.assertEqual(fd.make_title(r, self.first, None),
                         "Filed first Form D: $5.1M sold, the full offering, in SAFEs from 5 investors")
        r = {**salem(), "total_offering": 3_999_999, "amount_sold": 3_999_998}
        self.assertEqual(fd.make_title(r, self.first, None),
                         "Filed first Form D: $4.0M sold, all but $1 of the offering, in SAFEs from 5 investors")

    def test_amendments(self):
        r = {**salem(), "form": "D/A"}
        self.assertEqual(fd.make_title(r, None, 1_000_000),
                         "Amended Form D: $1.8M sold, up from $1.0M, in SAFEs from 5 investors")
        self.assertEqual(fd.make_title(r, None, 1_819_500),
                         "Amended Form D: $1.8M sold, unchanged, in SAFEs from 5 investors")
        self.assertEqual(fd.make_title(r, None, 1_800_000),
                         "Amended Form D: $1.82M sold, up from $1.80M, in SAFEs from 5 investors")
        self.assertEqual(fd.make_title(r, None, None),
                         "Amended Form D: $1.8M sold of $5.1M in SAFEs from 5 investors")

    def test_title_rules(self):
        for r, h, prev in ((salem(), self.first, None), ({**salem(), "form": "D/A"}, None, 900_000),
                           ({**salem(), "amount_sold": 0}, None, None)):
            t = fd.make_title(r, h, prev)
            self.assertLess(len(t), 110)
            self.assertFalse(t.endswith("."))
            self.assertFalse(t.endswith(","))
            self.assertRegex(t, r"\$\d")


class Strength(unittest.TestCase):
    def setUp(self):
        self.first = fd.summarize_history(FIRST, ROW["accession"], ROW["filed"])
        self.later = {**self.first, "first_form_d": False, "prior_form_d": 2}

    def s(self, hist=None, prev=None, **change) -> float:
        rec = {**salem(), **change}
        return fd.strength_of(rec, FILED, hist if hist is not None else self.first, prev)

    def test_always_in_range(self):
        for sold in (0, 1, 5_000, 250_000, 3_000_000, 15_000_000, 90_000_000, 3_000_000_000):
            for year in (2021, 2024, 2026):
                v = self.s(amount_sold=sold, year_inc=year)
                self.assertGreaterEqual(v, 0.15)
                self.assertLessEqual(v, 0.97)

    def test_seed_and_series_a_sizes_beat_tiny_and_huge(self):
        tiny, seed, series_a, growth, mega = (
            self.s(amount_sold=x) for x in (20_000, 1_500_000, 8_000_000, 60_000_000, 300_000_000))
        self.assertLess(tiny, 0.3)
        self.assertGreater(seed, tiny)
        self.assertGreater(series_a, seed)
        self.assertLess(growth, series_a)
        self.assertLess(mega, 0.3)

    def test_first_filing_and_youth_raise_it(self):
        self.assertGreater(self.s(), self.s(hist=self.later))
        self.assertGreater(self.s(year_inc=2026), self.s(year_inc=2022))

    def test_top_of_scale_is_a_new_company_with_an_institutional_round(self):
        top = self.s(amount_sold=6_000_000, total_offering=6_000_000, investors=2, year_inc=2026,
                     securities={**salem()["securities"], "other": False, "equity": True},
                     other_security=None, first_sale="2026-08-28")
        self.assertGreaterEqual(top, 0.85)
        # The same round as a second filing by a five-year-old company is not rare.
        ordinary = self.s(hist=self.later, amount_sold=6_000_000, investors=2, year_inc=2021,
                          first_sale="2026-08-28")
        self.assertLess(ordinary, 0.6)

    def test_fixture_is_solid_not_rare(self):
        # $1.8M of SAFEs, incorporated two years before, filed 118 days after the first sale.
        self.assertTrue(0.35 <= self.s() <= 0.7, self.s())

    def test_nothing_sold_is_routine(self):
        self.assertLess(self.s(amount_sold=0), 0.2)

    def test_discounts(self):
        base = self.s()
        debt = {**salem()["securities"], "other": False, "debt": True}
        self.assertLess(self.s(securities=debt, other_security=None), base)
        self.assertLess(self.s(brokers=1), base)
        self.assertLess(self.s(business_combination=True), base)
        self.assertLess(self.s(first_sale="2024-01-15"), base)
        self.assertLess(self.s(hist={**self.first, "reporting": True}), base)
        self.assertLess(self.s(hist={**self.first, "crowdfunding": True}), base)

    def test_amendment_counts_only_new_money(self):
        self.assertEqual(self.s(form="D/A", prev=1_819_500), 0.15)
        self.assertEqual(self.s(form="D/A", prev=2_500_000), 0.15)
        added = self.s(form="D/A", prev=300_000)
        self.assertGreater(added, 0.25)
        self.assertLess(added, 0.6)
        self.assertEqual(self.s(form="D/A", prev=None), 0.2)


class BuildSignal(unittest.TestCase):
    def setUp(self):
        self.hist = fd.summarize_history(FIRST, ROW["accession"], ROW["filed"])
        self.sig = fd.build_signal(salem(), ROW, self.hist)

    def test_signal_is_valid_and_matches_the_filing(self):
        s = self.sig
        s.validate()
        self.assertEqual((s.source, s.family, s.kind), ("sec_form_d", "capital", "form_d"))
        self.assertEqual(s.entity.name, "Salem Robotics, Inc.")
        self.assertEqual(s.entity.kind, "company")
        self.assertIsNone(s.entity.domain)  # Form D gives no website
        self.assertIsNone(s.entity.one_liner)  # and no description of the business
        self.assertEqual(s.entity.founded, "2024")
        self.assertEqual(s.entity.location, "Austin, TX")
        self.assertEqual(s.title, "Filed first Form D: $1.8M sold of $5.1M in SAFEs from 5 investors")
        self.assertEqual(s.occurred_at, "2026-09-08")
        self.assertEqual(s.url, "https://www.sec.gov/Archives/edgar/data/2134746/000213474626000002/xslFormDX01/primary_doc.xml")
        self.assertEqual((s.value, s.unit), (1_819_500.0, "USD"))
        self.assertTrue(0.0 <= s.strength <= 1.0)

    def test_metrics(self):
        m = self.sig.metrics
        # amount_usd is the total offering, amount_sold the money actually raised.
        self.assertEqual(m["amount_usd"], 5_100_000)
        self.assertEqual(m["offering_usd"], 5_100_000)
        self.assertEqual(m["amount_sold"], 1_819_500)
        self.assertEqual(m["investors"], 5)
        self.assertEqual(m["minimum_investment"], 0)
        self.assertEqual(m["year_incorporated"], 2024)
        self.assertEqual(m["fill_ratio"], 0.357)
        self.assertEqual(m["first_sale_date"], "2026-05-13")
        self.assertEqual(m["days_from_first_sale_to_filing"], 118)
        self.assertEqual(m["first_form_d"], 1)
        self.assertEqual(m["prior_form_d_filings"], 0)
        self.assertEqual(m["is_safe"], 1)
        self.assertEqual(m["cik"], "2134746")  # digits, no leading zeros
        self.assertEqual(m["accession"], "0002134746-26-000002")
        self.assertEqual(m["edgar_filer_since"], 2026)

    def test_cik_has_one_form_whatever_the_source_printed(self):
        # The XML pads the CIK to ten digits and the daily index does not.
        self.assertIn("<cik>0002134746</cik>", XML)
        padded = fd.build_signal({**salem(), "cik": "0002134746"}, ROW, None).metrics["cik"]
        from_row = fd.build_signal({**salem(), "cik": ""}, ROW, None).metrics["cik"]
        self.assertEqual((padded, from_row), ("2134746", "2134746"))

    def test_nothing_sold_claims_no_round_size(self):
        m = fd.build_signal({**salem(), "amount_sold": 0, "investors": 0}, ROW, self.hist).metrics
        self.assertNotIn("amount_usd", m)
        self.assertEqual(m["amount_sold"], 0)
        self.assertEqual(m["offering_usd"], 5_100_000)
        # So the scorer sees no capital at all, not the target.
        self.assertNotIn("capital", score.consensus_score(m, None, FILED)[1])

    def test_scorer_reads_the_money_raised_not_the_target(self):
        # Seen live: $285K sold of a $40M offering is a $285K round so far.
        rec = {**salem(), "amount_sold": 285_000, "total_offering": 40_000_000}
        m = fd.build_signal(rec, ROW, self.hist).metrics
        self.assertEqual((m["amount_sold"], m["amount_usd"]), (285_000, 40_000_000))
        # A $285K round is under the scorer's floor for "capital the market
        # has noticed"; the $40M target would not be.
        self.assertNotIn("capital", score.consensus_score(m, None, FILED)[1])
        self.assertIn("capital", score.consensus_score({**m, "amount_sold": 40_000_000}, None, FILED)[1])

    def test_people_carry_the_stated_relationship(self):
        people = self.sig.people
        self.assertEqual([p.name for p in people], ["Caleb Horan", "Janak Panthi"])
        # Horan signed the form as CEO; the form says nothing more about Panthi.
        self.assertEqual(people[0].role, "Executive Officer, Director (CEO)")
        self.assertEqual(people[1].role, "Executive Officer, Director")
        self.assertEqual(people[0].affiliations, [])

    def test_no_phone_or_street_address_leaves_the_collector(self):
        blob = json.dumps(self.sig.to_row())
        self.assertNotIn("505-699-5384", blob)
        self.assertNotIn("COLORADO ST", blob.upper())

    def test_text_does_not_carry_the_industry_group(self):
        rec = {**salem(), "industry": "Manufacturing"}
        s = fd.build_signal(rec, ROW, self.hist)
        self.assertNotIn("anufactur", s.text)
        self.assertEqual(s.metrics["industry_group"], "Manufacturing")

    def test_indefinite_offering_states_no_total(self):
        rec = {**salem(), "offering_indefinite": True, "total_offering": None}
        m = fd.build_signal(rec, ROW, self.hist).metrics
        self.assertEqual(m["amount_sold"], 1_819_500)
        self.assertNotIn("amount_usd", m)  # the form gives no total, so none is claimed
        self.assertNotIn("offering_usd", m)
        self.assertEqual(m["offering_indefinite"], 1)
        self.assertNotIn("fill_ratio", m)

    def test_amendment(self):
        rec = {**salem(), "form": "D/A", "previous_names": ["Salem Automation, Inc."]}
        prev = {**salem(), "amount_sold": 1_000_000, "people": salem()["people"][:1]}
        s = fd.build_signal(rec, {**ROW, "form": "D/A"}, None, prev)
        s.validate()
        self.assertEqual(s.kind, "form_d_amendment")
        self.assertEqual(s.title, "Amended Form D: $1.8M sold, up from $1.0M, in SAFEs from 5 investors")
        self.assertEqual(s.metrics["amount_sold_previous"], 1_000_000)
        self.assertEqual(s.metrics["amount_sold_added"], 819_500)
        self.assertEqual(s.metrics["new_related_persons"], 1)
        self.assertEqual(s.entity.aliases, ["Salem Automation, Inc."])
        self.assertEqual(s.text.splitlines()[:2], ["Salem Robotics, Inc.", "Formerly Salem Automation, Inc."])
        self.assertNotIn("first_form_d", s.metrics)  # no history was available

    def test_foreign_location_uses_the_country_name(self):
        rec = {**salem(), "city": "Almonte", "state": "A6", "state_name": "ONTARIO, CANADA"}
        self.assertEqual(fd.location_of(rec), "Almonte, Ontario, Canada")
        rec = {**salem(), "city": "SINGAPORE", "state": "U0", "state_name": "SINGAPORE"}
        self.assertEqual(fd.location_of(rec), "Singapore")
        rec = {**salem(), "city": "Santa Monica", "state": "CA", "state_name": "CALIFORNIA"}
        self.assertEqual(fd.location_of(rec), "Santa Monica, CA")  # "monica" ends in "ca"

    def test_a_firm_listed_as_a_related_person_is_not_a_person(self):
        rec = salem()
        rec["people"] = rec["people"] + [
            {"name": "Marble Arch Partners", "first": "Marble Arch", "last": "Partners",
             "relationships": ["Promoter"], "clarification": None},
            {"name": "Quilt Holdco GP, LLC", "first": None, "last": "Quilt Holdco GP, LLC",
             "relationships": ["Director"], "clarification": None},
        ]
        self.assertEqual([p.name for p in fd.people_of(rec)], ["Caleb Horan", "Janak Panthi"])

    def test_role_detail_is_trimmed_to_whole_words_and_never_repeats_the_relationship(self):
        rec = salem()
        rec["signer"] = None
        rec["people"][0]["clarification"] = (
            "President, sole director and founder. Sole member of Freedom Capital Partners LLC, the majority holder")
        rec["people"][1]["relationships"] = ["Director"]
        rec["people"][1]["clarification"] = "Director"
        horan, panthi = fd.people_of(rec)
        self.assertEqual(horan.role, "Executive Officer, Director (President, sole director and founder. "
                                     "Sole member of Freedom Capital Partners...)")
        self.assertEqual(panthi.role, "Director")
        self.assertEqual(fd._short("Chief Executive Officer of the Issuer."), "Chief Executive Officer of the Issuer")
        self.assertIsNone(fd._short(" "))

    def test_founded_is_withheld_when_the_company_had_an_earlier_life(self):
        self.assertEqual(self.sig.entity.founded, "2024")
        renamed = fd.build_signal({**salem(), "previous_names": ["Salem Automation LLC"]}, ROW, self.hist)
        self.assertIsNone(renamed.entity.founded)
        self.assertEqual(renamed.metrics["year_incorporated"], 2024)  # the form's own statement stays
        older = fd.build_signal(salem(), ROW, {**self.hist, "filer_since": 2022})
        self.assertIsNone(older.entity.founded)
        # A former name that differs only in punctuation is the same name.
        same = fd.build_signal({**salem(), "previous_names": ["Salem Robotics Inc"]}, ROW, self.hist)
        self.assertEqual((same.entity.founded, same.entity.aliases), ("2024", []))

    def test_edgar_state_tag_and_trade_name(self):
        x = XML.replace("<entityName>Salem Robotics, Inc.</entityName>",
                        "<entityName>Salem Robotics, Inc. /DE/ dba Salem Arms</entityName>", 1)
        self.assertIn("/DE/ dba", x)
        s = fd.build_signal(fd.parse_primary_doc(x), ROW, self.hist)
        self.assertEqual(s.entity.name, "Salem Robotics, Inc.")
        self.assertEqual(s.entity.aliases, ["Salem Arms"])
        self.assertEqual(s.entity.founded, "2024")  # a trade name is not a former name
        self.assertIn("Trading as Salem Arms", s.text)

    def test_text_carries_the_names_as_filed(self):
        # No legal form is a thesis term ("PLC" was one, as a programmable
        # logic controller), so the names need no trimming before they are
        # classified.
        rec = {**salem(), "name": "Fusion Fuel Green PLC", "previous_names": ["Fusion Fuel Ltd."],
               "other_security": None}
        s = fd.build_signal(rec, ROW, self.hist)
        self.assertEqual(s.entity.name, "Fusion Fuel Green PLC")
        self.assertEqual(s.text, "Fusion Fuel Green PLC\nFormerly Fusion Fuel Ltd.")
        self.assertEqual(classify(s.text)["terms"], ["fusion"])
        self.assertLess(classify(s.text)["fit"], 0.3)  # one ambiguous word: under the gate
        # A name that says what the company makes is still on thesis by itself.
        self.assertGreaterEqual(classify(self.sig.text)["fit"], 0.3)

    def test_filings_under_one_cik_resolve_to_one_company(self):
        # The resolver will not merge on a name as short as "Hub Corp" ("hub"),
        # and a renamed issuer shares no name with its earlier filings. The CIK
        # in metrics ties each pair; no made-up alias is needed. The same short
        # name under another CIK stays another company.
        notice = fd.build_signal({**salem(), "name": "Hub Corp"}, ROW, self.hist)
        amended = fd.build_signal({**salem(), "name": "Hub Corp", "form": "D/A"},
                                  {**ROW, "form": "D/A", "filed": "2026-09-20",
                                   "accession": "0002134746-26-000003"}, None)
        renamed = fd.build_signal({**salem(), "name": "Zaps Worldwide, Inc.", "cik": "2157757"},
                                  {**ROW, "cik": "2157757", "accession": "0002157757-26-000001"}, None)
        before = fd.build_signal({**salem(), "name": "Quantum Charge Group, Inc.", "cik": "0002157757"},
                                 {**ROW, "cik": "2157757", "filed": "2026-08-11",
                                  "accession": "0002157757-26-000002"}, None)
        other = fd.build_signal({**salem(), "name": "Hub Corp", "cik": "2999999"},
                                {**ROW, "cik": "2999999", "accession": "0002999999-26-000001"}, None)
        self.assertEqual(notice.entity.aliases, [])
        self.assertEqual(notice.metrics["cik"], amended.metrics["cik"])
        conn = db.connect(":memory:")
        self.addCleanup(conn.close)
        self.assertEqual(db.insert_signals(conn, 1, [notice, amended, renamed, before, other]), (5, 5))
        self.assertEqual(resolve.resolve(conn), 3)
        groups = {}
        for r in conn.execute("SELECT entity_id, title, metrics FROM signals"):
            groups.setdefault(r["entity_id"], set()).add(json.loads(r["metrics"])["cik"])
        self.assertEqual(sorted(sorted(g) for g in groups.values()), [["2134746"], ["2157757"], ["2999999"]])


class Collect(unittest.TestCase):
    """collect() end to end against fixtures, with antenna.http replaced."""

    LISTING = {"directory": {"item": [
        {"name": "form.20260930.idx", "type": "file"},
        {"name": "company.20260930.idx", "type": "file"},
        {"name": "form.20260701.idx", "type": "file"},  # outside the window
    ]}}

    # A multi-issuer filing is listed once per co-issuer: here one filing under
    # an LLC and a corporation, and the first fixture row repeated.
    CO_ISSUERS = (
        "D                Zeta Holdings LLC                                             2990001     20260930    edgar/data/2990001/0002990002-26-000001.txt\n"
        "D                Zeta Robotics Inc                                             2990002     20260930    edgar/data/2990002/0002990002-26-000001.txt\n"
    )

    def setUp(self):
        self.fetched: list[str] = []
        first_row = next(line for line in IDX.splitlines() if line.startswith("D  "))
        idx = IDX + first_row + "\n" + self.CO_ISSUERS
        names = {r["cik"]: r["company"] for r in fd.parse_form_idx(idx)}

        def get(url, **kw):
            self.fetched.append(url)
            if url.endswith("form.20260930.idx"):
                return idx
            m = re.search(r"/edgar/data/(\d+)/\d+/primary_doc\.xml$", url)
            if m:
                cik = m.group(1)
                if names[cik].startswith("Apogee"):
                    raise OSError("connection reset")
                return (XML.replace("0002134746", cik.zfill(10))
                           .replace("Salem Robotics, Inc.", names[cik]))
            raise AssertionError(f"unexpected GET {url}")

        def get_json(url, **kw):
            self.fetched.append(url)
            if url.endswith("/index.json"):
                return self.LISTING if "QTR3" in url else {"directory": {"item": []}}
            if "data.sec.gov/submissions" in url:
                return submissions()  # never lists the filing: history unknown
            raise AssertionError(f"unexpected GET {url}")

        self.patches = [mock.patch.object(fd.http, "get", get), mock.patch.object(fd.http, "get_json", get_json)]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])

    def test_collect_streams_valid_signals_and_survives_a_bad_filing(self):
        ctx = Context(today=date(2026, 10, 1), limit=None)
        signals = list(fd.collect(ctx))
        for s in signals:
            s.validate()
        # 9 filings pass the name prefilter; one fetch fails and is reported, one
        # (BZ Kitchens) is off thesis by its own name, 7 emit.
        self.assertEqual(sorted(s.entity.name for s in signals), sorted([
            "1World Online, Inc.", "ARTBnk Inc.", "Autology, Inc.",
            "ClearOffice Inc.", "CuriousBox AI Inc.", "Dynagentic Inc.",
            "Zeta Robotics Inc",
        ]))
        self.assertEqual(len([w for w in ctx.warnings if "Apogee" in w]), 1)
        self.assertTrue(all(s.occurred_at == "2026-09-30" for s in signals))
        self.assertTrue(all(s.title.startswith("Filed Form D: ") for s in signals))  # history unknown
        xml_gets = [u for u in self.fetched if u.endswith("/primary_doc.xml")]
        # Funds, LPs and LLCs were never fetched, and each filing only once.
        self.assertEqual(len(xml_gets), 9)
        self.assertEqual(len(set(xml_gets)), 9)
        self.assertIn("https://www.sec.gov/Archives/edgar/data/2990002/000299000226000001/primary_doc.xml", xml_gets)
        # The window is the collector's own (FORM_D_DAYS), not the run's.
        self.assertFalse(any("form.20260201" in u for u in self.fetched))

    def test_limit_stops_early(self):
        ctx = Context(today=date(2026, 10, 1), limit=3)
        self.assertEqual(len(list(fd.collect(ctx))), 3)

    def test_window_is_set_by_form_d_days(self):
        with mock.patch.object(fd, "FORM_D_DAYS", 0):
            self.assertEqual(list(fd.collect(Context(today=date(2026, 10, 1)))), [])
        # A short run-wide lookback does not shorten it: a Form D can be
        # months older than the news it predicts.
        ctx = Context(today=date(2026, 10, 1), lookback_days=0)
        self.assertEqual(len(list(fd.collect(ctx))), 7)


class CollectWithHistory(unittest.TestCase):
    """collect() when EDGAR's filing list is available: amendments and history drops."""

    IDX_HEAD = "Form Type   Company Name   CIK   Date Filed   File Name\n" + "-" * 120 + "\n"
    ROWS = (
        "D/A              One Dosh Inc.                                                 2113647     20260925    edgar/data/2113647/0002113647-26-000003.txt\n"
        "D                Old Shell Robotics Inc                                        1737630     20260925    edgar/data/1737630/0001737630-26-000003.txt\n"
        "D                Listed Robotics Inc                                           1800000     20260925    edgar/data/1800000/0001800000-26-000007.txt\n"
    )
    SOLD = {"000211364726000003": 4_150_000, "000211364726000002": 3_400_000, "000211364726000001": 2_400_000}
    HISTORY = {
        "2113647": AmendmentBaseline.ONE_DOSH,
        "1737630": submissions(
            ("D", "2026-09-25", "0001737630-26-000003", "2026-09-25T12:00:00.000Z", "021-600001"),
            ("D", "2018-04-16", "0001737630-18-000001", "2018-04-16T12:00:00.000Z", "021-300001"),
        ),
        "1800000": submissions(
            ("D", "2026-09-25", "0001800000-26-000007", "2026-09-25T12:00:00.000Z", "021-600002"),
            ("10-K", "2026-03-01", "0001800000-26-000001", "2026-03-01T12:00:00.000Z", "001-40000"),
        ),
    }

    def setUp(self):
        self.fetched: list[str] = []

        def get(url, **kw):
            self.fetched.append(url)
            if url.endswith("form.20260925.idx"):
                return self.IDX_HEAD + self.ROWS
            m = re.search(r"/edgar/data/(\d+)/(\d+)/primary_doc\.xml$", url)
            if not m:
                raise AssertionError(f"unexpected GET {url}")
            cik, folder = m.groups()
            x = XML.replace("0002134746", cik.zfill(10))
            if cik == "2113647":
                x = x.replace("Salem Robotics, Inc.", "One Dosh Inc.")
                x = x.replace("<totalAmountSold>1819500</totalAmountSold>",
                              f"<totalAmountSold>{self.SOLD[folder]}</totalAmountSold>")
                if folder != "000211364726000001":
                    # Both amendments point at the original notice, as the real ones do.
                    x = x.replace("<submissionType>D</submissionType>", "<submissionType>D/A</submissionType>")
                    x = x.replace("<isAmendment>false</isAmendment>",
                                  "<isAmendment>true</isAmendment>"
                                  "<previousAccessionNumber>0002113647-26-000001</previousAccessionNumber>")
            return x

        def get_json(url, **kw):
            self.fetched.append(url)
            if url.endswith("/index.json"):
                return {"directory": {"item": {"name": "form.20260925.idx"}}} if "QTR3" in url else ["not a listing"]
            m = re.search(r"submissions/CIK0*(\d+)\.json$", url)
            return self.HISTORY[m.group(1)]

        self.patches = [mock.patch.object(fd.http, "get", get), mock.patch.object(fd.http, "get_json", get_json)]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])

    def test_amendment_is_compared_with_the_previous_amendment_not_the_original(self):
        ctx = Context(today=date(2026, 10, 1))
        signals = list(fd.collect(ctx))
        # The reincorporated incumbent and the public registrant are dropped.
        self.assertEqual([s.entity.name for s in signals], ["One Dosh Inc."])
        s = signals[0]
        s.validate()
        self.assertEqual(s.kind, "form_d_amendment")
        self.assertEqual(s.title, "Amended Form D: $4.2M sold, up from $3.4M, in SAFEs from 5 investors")
        self.assertEqual(s.metrics["amount_sold_previous"], 3_400_000)
        self.assertEqual(s.metrics["amount_sold_added"], 750_000)
        self.assertEqual(s.occurred_at, "2026-09-25")
        # The original notice was never read: it is not the baseline.
        self.assertFalse(any("000211364726000001/primary_doc.xml" in u for u in self.fetched))
        # A malformed listing for the empty quarter is a warning, not a crash.
        self.assertEqual(len([w for w in ctx.warnings if "QTR4" in w]), 1)


if __name__ == "__main__":
    unittest.main()
