"""Offline tests for the NRC ADAMS collector: parsing and signal building, no network.

The fixture is a real response from adams-search.nrc.gov (first 30 results of
the 99902 docket query, plus facets). Docket histories that the collector
would fetch live are written out here with the values the API returned on
2026-10-01.
"""

from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path

from antenna.collectors import nrc_adams as m
from antenna.collectors.base import Context
from antenna.thesis import classify

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "regulatory" / "nrc_adams_search_preapp_dockets.json"


def load() -> dict:
    return json.loads(FIXTURE.read_text())


def ctx() -> Context:
    return Context(today=date(2026, 10, 1), lookback_days=120)


def docs_on(docket: str) -> list[dict]:
    return [d for d in m.parse_results(load()) if docket in m.preapp_dockets(d)]


def by_accession() -> dict[str, dict]:
    return {d["AccessionNumber"]: d for d in m.parse_results(load())}


def history_of(docs: list[dict]) -> dict:
    """A whole-docket history response, oldest first, built from documents."""
    ordered = sorted(docs, key=lambda d: d["DateAddedTimestamp"])
    return m.parse_history({"count": len(ordered), "results": [{"document": d} for d in ordered], "facets": {}})


BLUE_ENERGY_HISTORY = {
    "count": 125, "first_added": date(2025, 2, 4), "first_docs": [],
    "authors": {"Blue Energy": 51, "Blue Energy Global, Inc": 44}, "addressees": {"Blue Energy Global, Inc": 2},
}


class ParsingTests(unittest.TestCase):
    def test_results_and_dockets(self):
        docs = m.parse_results(load())
        self.assertEqual(len(docs), 30)
        acc = by_accession()
        # Extra non-project dockets on the same document are ignored.
        self.assertEqual(m.preapp_dockets(acc["ML26271A258"]), ["99902106"])
        self.assertEqual(m.preapp_dockets(acc["ML26265A067"]), ["99902121"])
        self.assertEqual(m.preapp_dockets({"DocketNumber": ["05200055", "PROJ0728"]}), [])
        self.assertEqual(m.preapp_dockets({"DocketNumber": None}), [])

    def test_nrc_affiliations_are_not_companies(self):
        self.assertTrue(m.is_nrc("NRC"))
        self.assertTrue(m.is_nrc("NRC/NRR/DDLP/DLP2/ORTRB"))
        self.assertFalse(m.is_nrc("NRCO Holdings"))
        self.assertFalse(m.is_nrc("Valar Atomics"))

    def test_history_from_facets_drops_the_regulator(self):
        hist = m.parse_history(load())
        self.assertEqual(hist["count"], 291)
        self.assertIn("Framatome, Inc", hist["authors"])
        self.assertFalse(any(m.is_nrc(a) for a in list(hist["authors"]) + list(hist["addressees"])))
        self.assertFalse(any("no known affiliation" in a.lower() for a in hist["authors"]))

    def test_history_from_rows(self):
        hist = history_of(docs_on("99902185"))
        self.assertEqual(hist["count"], 2)
        self.assertEqual(hist["first_added"], date(2026, 10, 1))
        self.assertEqual(hist["authors"], {"Valar Atomics": 2})
        self.assertEqual(len(hist["first_docs"]), 2)

    def test_meeting_date_from_title(self):
        acc = by_accession()
        self.assertEqual(m._meeting_date(acc["ML26274A408"]), date(2026, 10, 8))
        self.assertEqual(m._meeting_date({"DocumentTitle": "10/28/2022 Notice of Meeting with Radiant Industries"}),
                         date(2022, 10, 28))
        self.assertIsNone(m._meeting_date({"DocumentTitle": "Notice of meeting, date to be set"}))
        self.assertIsNone(m._meeting_date({"DocumentTitle": "Summary of 13/45/2026 meeting"}))


class CompanyNameTests(unittest.TestCase):
    def test_prefers_legal_name_and_drops_bare_prefix_alias(self):
        name, aliases, variants = m.pick_company({"Blue Energy": 51, "Blue Energy Global, Inc": 46})
        self.assertEqual(name, "Blue Energy Global, Inc")
        self.assertEqual(aliases, [])  # 'Blue Energy' alone is too generic to merge on
        self.assertEqual(set(variants), {"Blue Energy", "Blue Energy Global, Inc"})

    def test_consultant_and_misindexed_names_lose(self):
        name, aliases, variants = m.pick_company({
            "Radiant": 28, "Radiant Industries, LLC": 14, "Radiant Industries, Inc": 10,
            "R-50, LLC (Radiant)": 2, "Haley & Aldrich, Inc": 4,
        })
        self.assertIn(name, ("Radiant Industries, LLC", "Radiant Industries, Inc"))
        self.assertNotIn("Radiant", aliases)
        self.assertNotIn("Haley & Aldrich, Inc", variants)
        name, _, variants = m.pick_company({"Oklo Inc": 123, "Wagner Electric Corporation": 1})
        self.assertEqual(name, "Oklo Inc")
        self.assertEqual(variants, ["Oklo Inc"])

    def test_alias_kept_when_it_adds_a_distinct_name(self):
        name, aliases, _ = m.pick_company({"Aalo Holdings, Inc": 15, "AALO Atomics": 9})
        self.assertEqual(name, "Aalo Holdings, Inc")
        self.assertEqual(aliases, ["AALO Atomics"])

    def test_name_is_always_a_string_adams_wrote(self):
        counts = {"newcleo Americas, LLC": 8, "newcleo Group": 1}
        name, aliases, _ = m.pick_company(counts)
        self.assertIn(name, counts)
        self.assertEqual(aliases, [])  # a single sighting is not enough for an alias
        self.assertEqual(m.pick_company({}), (None, [], []))

    def test_spellings_that_differ_by_a_descriptive_word_are_one_company(self):
        # The shared name key keeps 'Technology' and 'Energy', so the docket's two spellings no
        # longer collapse by themselves: they are grouped here, and the minor one becomes an alias.
        counts = {"ARC Clean Technology": 120, "ARC Clean Energy, LLC": 5}
        name, aliases, variants = m.pick_company(counts)
        self.assertEqual(name, "ARC Clean Technology")
        self.assertEqual(aliases, ["ARC Clean Energy, LLC"])
        self.assertEqual(set(variants), set(counts))
        self.assertEqual(set(m.pick_company({"Aalo Holdings, Inc": 1, "AALO Atomics": 1})[2]),
                         {"Aalo Holdings, Inc", "AALO Atomics"})
        # Sharing only a descriptive or generic word is not the same company.
        self.assertFalse(m._related("Hadron Energy, Inc", "Deployable Energy"))
        self.assertFalse(m._related("Blue Energy", "BlueCore Energy, Inc"))
        self.assertFalse(m._related("Advanced Float Co., Ltd", "Advanced Reactor Concepts, LLC"))
        self.assertFalse(m._related("Radiant Industries, Inc", "Haley & Aldrich, Inc"))

    def test_legal_form_is_read_with_the_shared_helper(self):
        for n in ("Blue Energy Global, Inc", "Radiant Industries, LLC", "Advanced Float Co., Ltd",
                  "Terra Innovatum s.r.l", "Oklo Inc.", "Wagner Electric Corporation"):
            self.assertTrue(m.has_legal_form(n), n)
        for n in ("Valar Atomics", "Last Energy", "ARC Clean Technology", "R-50, LLC (Radiant)", ""):
            self.assertFalse(m.has_legal_form(n), n)
        # The legal entity wins the name even when ADAMS writes it 'Co., Ltd'.
        self.assertEqual(m.pick_company({"Advanced Float": 3, "Advanced Float Co., Ltd": 3})[0],
                         "Advanced Float Co., Ltd")

    def test_stoplist(self):
        for n in ("Westinghouse Electric Co, LLC", "X-Energy, LLC", "Duke Energy Carolinas, LLC", "Framatome, Inc",
                  "Holtec International", "SMR, LLC", "NuScale Power, LLC", "TerraPower, LLC", "Kairos Power, LLC",
                  "Electric Power Research Institute (EPRI)", "Tennessee Valley Authority", "Appalachian Power Co",
                  "US Dept of the Navy, Naval Sea Systems Command", "Govt of Japan, Atomic Energy Agency"):
            self.assertTrue(m.is_stoplisted(n), n)
        # Exchange-listed developers are real companies a venture fund can no longer source.
        for n in ("Oklo Inc", "Oklo Power LLC", "Terrestrial Energy USA, Inc", "Terra Innovatum s.r.l"):
            self.assertTrue(m.is_stoplisted(n), n)
        for n in ("Valar Atomics", "Apollo Atomics, Inc", "AMPERA, Inc", "Blue Energy Global, Inc",
                  "Radiant Industries, Inc", "Elementl Power Inc", "Last Energy", "Deployable Energy"):
            self.assertFalse(m.is_stoplisted(n), n)

    def test_shared_docket_with_an_incumbent_is_stopped(self):
        # Long Mott Energy is X-energy's project vehicle; X-energy writes half the docket.
        self.assertGreaterEqual(m.stop_share({"Long Mott Energy, LLC": 61, "X-Energy, LLC": 49}), 0.4)
        self.assertLess(m.stop_share({"Hadron Energy, Inc": 112, "Idaho National Lab": 3}), 0.4)


class ClassifyTests(unittest.TestCase):
    def kind(self, accession: str, variants: list[str]) -> str | None:
        k = m.classify_doc(by_accession()[accession], variants)
        return k["kind"] if k else None

    def test_company_documents(self):
        self.assertEqual(self.kind("ML26274A394", ["Valar Atomics"]), "rep")  # '260402-REP_Cover_Letter_signed'
        self.assertIsNone(self.kind("ML26274A395", ["Valar Atomics"]))  # affidavit
        self.assertEqual(self.kind("ML26266A278", ["AMPERA, Inc"]), "fee_waiver")
        self.assertEqual(self.kind("ML26261A084", ["Apollo Atomics, Inc"]), "qapd")
        blue = ["Blue Energy", "Blue Energy Global, Inc"]
        self.assertEqual(self.kind("ML26265A370", blue), "application")
        self.assertEqual(self.kind("ML26265A351", blue), "exemption")
        self.assertIsNone(self.kind("ML26266A259", blue))  # 'Enclosure 3: Affidavit'

    def test_updated_plan_is_not_called_new(self):
        k = m.classify_doc(by_accession()["ML26271A258"], ["Radiant Industries, Inc"])
        self.assertEqual(k["kind"], "rep")
        self.assertIn("updated", k["act"])
        self.assertLess(k["base"], 0.65)

    def test_nrc_documents(self):
        aalo = ["Aalo Holdings, Inc"]
        self.assertEqual(self.kind("ML26265A272", aalo), "readiness_plan")
        k = m.classify_doc(by_accession()["ML26274A408"], ["ARC Clean Technology"])
        self.assertEqual(k["kind"], "meeting_notice")
        self.assertEqual(k["meeting_date"], date(2026, 10, 8))
        self.assertEqual(k["act"], "NRC scheduled a meeting for 8 Oct 2026")
        # NRC e-mails can name a docket's first document but never become an activity signal.
        self.assertEqual(m.classify_doc(by_accession()["ML26265A273"], aalo)["base"], 0.0)

    def test_third_party_and_mixed_authorship_are_not_attributed(self):
        doc = {"AuthorAffiliation": ["Haley & Aldrich, Inc"], "DocumentTitle": "Enclosure 3: Environmental Report",
               "DocumentType": ["Environmental Report"]}
        self.assertIsNone(m.classify_doc(doc, ["Radiant Industries, Inc"]))
        doc = {"AuthorAffiliation": ["NRC/OAR", "Natura Resources, LLC"], "DocumentTitle": "Information Access Agreement",
               "DocumentType": ["Legal-Stipulation/Agreement"]}
        self.assertEqual(m.doc_side(doc, ["Natura Resources, LLC"]), "other")

    def test_titles_that_mention_two_things(self):
        def company(title: str, types: list[str]) -> dict | None:
            return m.classify_company_doc({"DocumentTitle": title, "DocumentType": types})

        # A readiness request about a construction permit application is not the application.
        k = company("Deployable Energy Limited - Request for Preapplication Readiness Assessment of the Deployable "
                    "Energy Construction Permit Application", ["Letter"])
        self.assertEqual(k["kind"], "readiness_request")
        # Slides about a topical report are slides.
        k = company("Oklo, Inc. - Transmittal of Meeting Materials for the July 30, 2026, Pre-Submittal Meeting "
                    "Regarding Fuel Qualification Topical Report", ["Letter"])
        self.assertEqual(k["kind"], "presentation")
        # A letter that only mentions the QAPD is not a QAPD submittal: plain letter, no signal.
        k = company("Deployable Energy - Confirmation of Comments for QAPD and Public Meeting Minutes", ["Letter"])
        self.assertEqual((k["kind"], k["base"]), ("letter", 0.0))
        k = company("RankShield Energy, Inc. Letter of Intent to Begin Pre-Application Engagement and Request for a "
                    "Project Number", ["Letter"])
        self.assertEqual(k["kind"], "letter_of_intent")
        self.assertIsNone(company("RE_ Question regarding -A versions", ["E-Mail"]))

    def test_vendor_inspection(self):
        self.assertTrue(m.is_vendor_inspection({"DocumentTitle": "Announcement Letter for the Vendor Inspection of X",
                                                "AuthorAffiliation": ["NRC/NRR/DRO/IQVB"]}))
        self.assertTrue(m.is_vendor_inspection({"DocumentTitle": "EPRIs Announcement Letter",
                                                "AuthorAffiliation": ["NRC/CNRI/DDII/DRIS/VQAB"]}))
        self.assertFalse(m.is_vendor_inspection(by_accession()["ML26274A394"]))


class SignalTests(unittest.TestCase):
    def test_new_docket(self):
        docs = docs_on("99902185")
        name, opened, activity = m.docket_signals("99902185", docs, history_of(docs), ctx(), prior_docket_docs=0)
        self.assertEqual(name, "Valar Atomics")
        self.assertEqual(len(opened), 1)
        self.assertEqual(activity, [])  # the affidavit and the cover letter are one event
        s = opened[0]
        s.validate()
        self.assertEqual((s.source, s.family, s.kind), ("nrc_adams", "regulatory", "nrc_preapp_docket_opened"))
        # 'appeared', not 'opened': the date is the first public document, not the NRC's assignment.
        # No 'N months earlier' clause: the indexed document date cannot be trusted for a number.
        self.assertEqual(s.title, "New NRC pre-application docket 99902185 appeared with a Regulatory Engagement Plan")
        self.assertEqual(s.occurred_at, "2026-10-01")  # the day it became public
        self.assertEqual(s.url, "https://www.nrc.gov/docs/ML2627/ML26274A394.pdf")
        self.assertEqual(s.strength, 0.9)
        self.assertEqual((s.value, s.unit), (2.0, "documents on docket"))
        self.assertEqual(s.metrics["docket"], "99902185")
        self.assertEqual(s.metrics["adams_document_date"], "2026-03-28")
        self.assertEqual(s.metrics["publication_lag_days"], 187)
        self.assertIsNone(s.entity.domain)  # the search index gives no domain; enrichment may add one
        self.assertEqual([p.name for p in s.people], ["M. Mitchell"])
        self.assertIn("Nuclear Regulatory Commission", s.text)
        # The docket's own documents say Regulatory Engagement Plan, so the text may say pre-application.
        self.assertIn("(NRC) pre-application project docket 99902185", s.text)
        self.assertIn("advanced nuclear reactor pre-application", s.text)
        # Dated by the release day already: nothing to tell the scorer about an earlier, hidden date.
        self.assertNotIn("public_at", s.metrics)

    def test_text_alone_puts_the_row_on_thesis_with_more_than_one_term(self):
        # 'Valar Atomics' and '260402-REP_Cover_Letter_signed' contain no thesis word at all: the
        # docket is the evidence, and the text says what the docket is in plain words.
        docs = docs_on("99902185")
        _, opened, _ = m.docket_signals("99902185", docs, history_of(docs), ctx(), prior_docket_docs=0)
        c = classify(opened[0].text)
        self.assertEqual(c["sector"], "energy")
        self.assertGreaterEqual(c["fit"], 0.3)
        self.assertTrue({"nuclear", "reactor"} <= set(c["terms"]), c["terms"])

    def test_text_says_pre_application_only_when_the_docket_documents_do(self):
        letter = doc("ML8", "Blykalla Reactor, Inc. - Assignment of Project Number and Explanation of Billing and Fees",
                     added="2026-06-17", written="2026-06-10", types=["Letter"], author=["NRC/NRR"],
                     addressee=["Blykalla Reactor, Inc"])
        name, opened, _ = m.docket_signals("99902999", [letter], history_of([letter]), ctx(), prior_docket_docs=0)
        self.assertEqual(name, "Blykalla Reactor, Inc")
        self.assertEqual(opened[0].title, "New NRC project docket 99902999 appeared with the NRC project number assignment")
        self.assertIn("(NRC) project docket 99902999", opened[0].text)
        self.assertNotIn("pre-application project docket", opened[0].text)
        # The sentence about the series describes the series, and names its other use too.
        self.assertIn(m.SERIES_NOTE, opened[0].text)
        self.assertIn("vendor topical reports", m.SERIES_NOTE)

    def test_second_docket_for_a_known_company_is_not_a_new_entrant(self):
        docs = docs_on("99902180")
        hist = {"count": 3, "first_added": date(2026, 9, 2), "first_docs": [],
                "authors": {}, "addressees": {"Aalo Holdings, Inc": 1, "AALO Atomics": 1}}
        ctx_ = ctx()
        # The docket's first document (an NRC e-mail of 2 Sep) is not in the 30-row fixture.
        first = {"AccessionNumber": "ML26245A061", "DateAdded": "2026-09-02", "DateAddedTimestamp": "2026-09-02 10:00",
                 "DocumentDate": "2026-09-02", "DocumentTitle": "E-mail - RELLIS ESP Project Number",
                 "DocumentType": ["E-Mail"], "DocketNumber": ["99902180"], "AuthorAffiliation": ["NRC/OAR/DDLP/DARL/ARLB4"],
                 "AddresseeAffiliation": [], "AuthorName": ["Munoz J H"], "PackagesFiledIn": [],
                 "Url": "https://www.nrc.gov/docs/ML2624/ML26245A061.pdf"}
        name, opened, activity = m.docket_signals("99902180", docs + [first], hist, ctx_, prior_docket_docs=30)
        self.assertEqual(len(opened), 1)
        self.assertEqual(opened[0].strength, 0.6)
        self.assertEqual(opened[0].title,
                         "Additional NRC project docket 99902180 appeared with an NRC e-mail about the project number")
        self.assertEqual(opened[0].occurred_at, "2026-09-02")
        self.assertEqual(opened[0].people, [])  # NRC staff are not the company's people
        self.assertEqual([s.metrics["doc_kind"] for s in activity], ["readiness_plan"])
        self.assertEqual(activity[0].title, "NRC issued a pre-application readiness assessment plan on docket 99902180")
        # Dated by the plan's own date (30 Sep), not the day ADAMS released it (1 Oct).
        self.assertEqual(activity[0].occurred_at, "2026-09-30")
        self.assertEqual(activity[0].metrics["date_added"], "2026-10-01")
        self.assertEqual(activity[0].metrics["occurred_at_basis"], "adams_document_date")
        self.assertEqual(activity[0].metrics["public_at"], "2026-10-01")  # the scorer decays from the release
        self.assertEqual(opened[0].metrics["occurred_at_basis"], "adams_date_added")

    def test_old_docket_emits_activity_only_and_collapses_enclosures(self):
        docs = docs_on("99902137")
        self.assertEqual(len(docs), 15)
        name, opened, activity = m.docket_signals("99902137", docs, BLUE_ENERGY_HISTORY, ctx())
        self.assertEqual(name, "Blue Energy Global, Inc")
        self.assertEqual(opened, [])
        kinds = sorted(s.metrics["doc_kind"] for s in activity)
        self.assertEqual(kinds, ["application", "exemption"])  # 15 documents, 2 events
        app = next(s for s in activity if s.metrics["doc_kind"] == "application")
        # The filing is 'Construction Permit Application Part 1', and the title says so.
        self.assertEqual(app.title,
                         "Submitted part 1 of a construction permit application to the NRC on docket 99902137")
        self.assertEqual(app.metrics["accession_number"], "ML26265A370")  # the cover letter, not an enclosure
        self.assertEqual(app.strength, 0.68)  # 0.8 for an application, x0.85 for a docket in its second year
        # The letter is dated 15 September; ADAMS released this copy on the 29th.
        self.assertEqual(app.occurred_at, "2026-09-15")
        self.assertEqual(app.metrics["date_added"], "2026-09-29")
        self.assertEqual(app.metrics["public_at"], "2026-09-29")
        self.assertEqual([p.name for p in app.people], ["C. J. Fong"])

    def test_incumbents_and_inspections_are_skipped(self):
        docs = docs_on("99902038")
        self.assertEqual(m.docket_signals("99902038", docs, history_of(docs), ctx()), (None, [], []))
        docs = docs_on("99902121")  # Duke Energy
        self.assertEqual(m.docket_signals("99902121", docs, history_of(docs), ctx()), (None, [], []))
        insp = [{"AccessionNumber": "ML26239A228", "DateAdded": "2026-09-24", "DateAddedTimestamp": "2026-09-24 09:00",
                 "DocumentDate": "2026-09-16", "DocketNumber": ["99902182"], "DocumentType": ["Letter"],
                 "DocumentTitle": "Letter Vendor Inspection Of Tampa Armature Works Quality Assurance Program",
                 "AuthorAffiliation": ["NRC/CNRI/DDII/DRIS/VQAB"], "AddresseeAffiliation": ["Tampa Armature Works"],
                 "Url": "https://www.nrc.gov/docs/ML2623/ML26239A228.pdf"}]
        self.assertEqual(m.docket_signals("99902182", insp, history_of(insp), ctx()), (None, [], []))

    def test_nothing_older_than_the_window(self):
        docs = docs_on("99902185")
        late = Context(today=date(2027, 6, 1), lookback_days=120)
        name, opened, activity = m.docket_signals("99902185", docs, history_of(docs), late)
        self.assertEqual((opened, activity), ([], []))

    def test_every_fixture_signal_is_valid_and_follows_title_rules(self):
        docs = m.parse_results(load())
        dockets = sorted({dk for d in docs for dk in m.preapp_dockets(d)})
        signals = []
        for dk in dockets:
            mine = [d for d in docs if dk in m.preapp_dockets(d)]
            # Treat every docket as years old so only per-document signals come out.
            hist = history_of(mine)
            hist["first_added"] = date(2024, 1, 1)
            name, opened, activity = m.docket_signals(dk, mine, hist, ctx())
            signals.extend(opened + m.cap_activity(activity))
        self.assertGreaterEqual(len(signals), 5)
        names = {s.entity.name for s in signals}
        self.assertFalse(any(m.is_stoplisted(n) for n in names), names)
        for s in signals:
            s.validate()
            self.assertEqual(s.kind, "nrc_preapp_activity")
            self.assertLess(len(s.title), 110, s.title)
            self.assertFalse(s.title.endswith("."), s.title)
            self.assertTrue(s.title[0].isupper(), s.title)
            self.assertIn(s.metrics["docket"], s.title)
            self.assertTrue(s.url.startswith("https://www.nrc.gov/docs/"), s.url)
            self.assertGreaterEqual(s.occurred_at, ctx().since.isoformat())
            self.assertTrue(0.15 <= s.strength <= 0.8, (s.strength, s.title))
            c = classify(s.text)
            self.assertEqual(c["sector"], "energy", s.text)
            self.assertGreaterEqual(len(c["terms"]), 2, s.text)  # never rests on one keyword
            self.assertGreaterEqual(c["fit"], 0.3, s.text)
            # public_at is the release day, and only present when it is later than the event.
            if "public_at" in s.metrics:
                self.assertEqual(s.metrics["public_at"], s.metrics["date_added"])
                self.assertGreater(s.metrics["public_at"], s.occurred_at)
            else:
                self.assertEqual(s.occurred_at, s.metrics["date_added"])

    def test_cap_keeps_strongest_and_limits_repeats(self):
        docs = docs_on("99902137")
        _, _, activity = m.docket_signals("99902137", docs, BLUE_ENERGY_HISTORY, ctx())
        many = activity * 4
        kept = m.cap_activity(many)
        self.assertLessEqual(len(kept), m.ACTIVITY_CAP)
        self.assertEqual(kept[0].metrics["doc_kind"], "application")
        self.assertLessEqual(sum(1 for s in kept if s.metrics["doc_kind"] == "application"), 2)

    def test_age_factor(self):
        self.assertEqual(m.age_factor(0), 1.0)
        self.assertEqual(m.age_factor(400), 0.85)
        self.assertEqual(m.age_factor(1400), 0.7)

    def test_docket_older_than_four_years_is_not_a_new_entrant(self):
        docs = docs_on("99902137")
        old = dict(BLUE_ENERGY_HISTORY, first_added=date(2022, 9, 1))
        self.assertEqual(m.docket_signals("99902137", docs, old, ctx()), (None, [], []))
        young = dict(BLUE_ENERGY_HISTORY, first_added=date(2022, 10, 4))  # 1,458 days before the run
        self.assertEqual(m.docket_signals("99902137", docs, young, ctx())[0], "Blue Energy Global, Inc")


class EnrichmentTests(unittest.TestCase):
    LETTER = ("Valar Atomics Inc. 4857 W 147th Street Hawthorne, CA 2-Apr-2026 Subject: Submittal of Regulatory "
              "Engagement Plan ... Unsworn Declaration of Mark Mitchell ... questions to licensing@nrc.gov ... "
              "Sincerely,\nMark Mitchell\nValar Atomics Inc.\nmark@valaratomics.com")

    def signals(self):
        docs = docs_on("99902185")
        _, opened, _ = m.docket_signals("99902185", docs, history_of(docs), ctx(), prior_docket_docs=0)
        return opened

    def test_domain_and_first_name_from_the_filing_text(self):
        sigs = self.signals()
        m.enrich_from_text(sigs, ["Valar Atomics"], by_accession(), ctx(),
                           fetch=lambda _id: self.LETTER, is_live=lambda d: True)
        self.assertEqual(sigs[0].entity.domain, "valaratomics.com")
        self.assertEqual(sigs[0].people[0].name, "Mark Mitchell")
        self.assertEqual(sigs[0].people[0].facts["adams_author_name"], "Mitchell M")

    def test_dead_domain_is_left_out(self):
        sigs = self.signals()
        c = ctx()
        m.enrich_from_text(sigs, ["Valar Atomics"], by_accession(), c, fetch=lambda _id: self.LETTER,
                           is_live=lambda d: False)
        self.assertIsNone(sigs[0].entity.domain)
        self.assertEqual(len(c.warnings), 1)

    def test_failed_text_fetch_adds_no_domain_and_keeps_no_unchecked_names(self):
        sigs = self.signals()
        c = ctx()

        def boom(_id):
            raise OSError("down")

        m.enrich_from_text(sigs, ["Valar Atomics"], by_accession(), c, fetch=boom, is_live=lambda d: True)
        self.assertIsNone(sigs[0].entity.domain)
        self.assertEqual(sigs[0].people, [])  # a name nobody could check against the filing is not shipped
        self.assertEqual(len(c.warnings), 1)

    def test_domain_must_belong_to_the_company(self):
        v = ["Radiant Industries, Inc", "Radiant"]
        self.assertTrue(m.domain_matches("radiantnuclear.com", v))
        self.assertTrue(m.domain_matches("blueenergy.co", ["Blue Energy Global, Inc"]))
        self.assertTrue(m.domain_matches("aalo.com", ["Aalo Holdings, Inc", "AALO Atomics"]))
        self.assertTrue(m.domain_matches("advanced-float.com", ["Advanced Float Co., Ltd"]))
        self.assertTrue(m.domain_matches("bluecore.energy", ["BlueCore Energy, Inc"]))
        self.assertFalse(m.domain_matches("nrc.gov", v))
        self.assertFalse(m.domain_matches("haleyaldrich.com", v))
        self.assertFalse(m.domain_matches("radiantnuclear.corn", v))  # OCR for .com
        self.assertFalse(m.domain_matches("blue.com", ["Blue Energy Global, Inc"]))
        # Descriptive words stay in the name: only a corporate-vehicle word ('Holdings', 'Group') is set aside.
        self.assertFalse(m.domain_matches("blue.com", ["Blue Energy", "Blue Energy Global, Inc"]))
        self.assertFalse(m.domain_matches("last.com", ["Last Energy"]))
        self.assertTrue(m.domain_matches("lastenergy.com", ["Last Energy"]))
        self.assertTrue(m.domain_matches("deployable.energy", ["Deployable Energy"]))
        self.assertTrue(m.domain_matches("arc-cleantech.com", ["ARC Clean Technology", "ARC Clean Energy, LLC"]))
        self.assertTrue(m.domain_matches("aalo.com", ["Aalo Holdings, Inc"]))
        self.assertTrue(m.domain_matches("newcleo.com", ["newcleo Group"]))
        self.assertFalse(m.domain_matches("holdings.com", ["Aalo Holdings, Inc"]))
        self.assertFalse(m.domain_matches("terrapower.com", ["Terra Innovatum s.r.l"]))
        found = m.extract_domains("a@radiantnuclear.com b@nrc.gov c@gmail.com d@RadiantNuclear.com", v)
        self.assertEqual(dict(found), {"radiantnuclear.com": 2})

    def test_first_name_needs_two_matching_sightings(self):
        self.assertEqual(m.format_author("Fong C J"), "C. J. Fong")
        self.assertEqual(m.format_author("Cuadrado de Jesus S"), "S. Cuadrado de Jesus")
        self.assertEqual(m.format_author("Public Commenter"), "Public Commenter")
        self.assertEqual(m.expand_author("Irish S", "Simon Irish ... Sinon Irish"), "S. Irish")  # OCR disagreement
        self.assertEqual(m.expand_author("Irish S", "Simon Irish, CEO"), "S. Irish")  # seen once only
        self.assertEqual(m.expand_author("Irish S", "Simon Irish ... Sincerely, Simon Irish"), "Simon Irish")
        self.assertEqual(m.expand_author("Moor P O", "Philip O. Moor\nPhilip Moor"), "Philip Moor")
        self.assertEqual(m.expand_author("Mitchell M", "Mr Mitchell and Ms Mitchell"), "M. Mitchell")


def doc(accession: str, title: str, *, added: str, written: str | None, types: list[str], author: list[str],
        docket: str = "99902999", addressee: list[str] | None = None, names: list[str] | None = None,
        package: list[str] | None = None, dockets: list[str] | None = None) -> dict:
    """A search-result row with the fields the collector reads, shaped like the fixture rows."""
    return {"AccessionNumber": accession, "IdT": accession, "DocumentTitle": title, "DateAdded": added,
            "DateAddedTimestamp": added + " 08:00", "DocumentDate": written, "DocumentType": types,
            "DocketNumber": dockets or [docket], "AuthorAffiliation": author, "AddresseeAffiliation": addressee or [],
            "AuthorName": names or [], "PackagesFiledIn": package or [],
            "Url": f"https://www.nrc.gov/docs/ML0000/{accession}.pdf"}


class VerifierRegressionTests(unittest.TestCase):
    """Each test pins a defect found by checking emitted signals against their evidence PDFs."""

    ARC_HISTORY = {"count": 157, "first_added": date(2023, 3, 1), "first_docs": [],
                   "authors": {"ARC Clean Technology": 120, "ARC Clean Energy, LLC": 5}, "addressees": {}}

    def test_revision_one_is_not_called_an_update(self):
        def rep(title: str) -> dict:
            return m.classify_company_doc({"DocumentTitle": title, "DocumentType": ["Letter"]})

        # Last Energy's 'Rev 1.0' was its first issue to the NRC (0.x were internal drafts).
        k = rep("Last Energy, PWR-20 Regulatory Engagement Plan, Rev 1.0")
        self.assertEqual((k["kind"], k["act"]), ("rep", "Submitted a Regulatory Engagement Plan to the NRC"))
        self.assertNotIn("updated", rep("Submittal of Regulatory Engagement Plan, Revision 0")["act"])
        self.assertNotIn("updated",
                         rep('Submittal of "Regulatory Engagement Plan for the Aalo R-COLA," Revision A')["act"])
        for title in ("Ampera, Inc., Revised Regulatory Engagement Plan (REP), Version 1",
                      "Update to Radiant's Regulatory Engagement Plan",
                      "Fourth Amended Hadron Energy Microreactor Pre-Application Regulatory Engagement Plan",
                      "ARC Clean Technology, Regulatory Engagement Plan, Rev. 2",
                      "Regulatory Engagement Plan, Revision 3"):
            self.assertIn("updated", rep(title)["act"], title)

    def test_one_regulatory_engagement_plan_per_docket_and_the_safer_wording_wins(self):
        # ADAMS posted the revised plan and its cover letter as two records a week apart; only the
        # letter's title says 'revised'. One event, called an update, dated by the letter.
        docs = [
            doc("ML26225A376", "ARC Clean Technology, Inc. - ARC 20 Regulatory Engagement Plan", added="2026-08-21",
                written="2026-08-13", types=["Report, Technical"], author=["ARC Clean Technology"], names=["Burski R"]),
            doc("ML26222A132", "ARC Clean Energy LLC - Submittal of a revised ARC-100 Regulatory Engagement Plan",
                added="2026-08-14", written="2026-07-27", types=["Letter"], author=["ARC Clean Energy, LLC"],
                names=["Lotti R"], package=["ML26222A084"]),
        ]
        _, opened, activity = m.docket_signals("99902999", docs, self.ARC_HISTORY, ctx())
        self.assertEqual(opened, [])
        self.assertEqual(len(activity), 1)
        self.assertEqual(activity[0].title,
                         "Submitted an updated Regulatory Engagement Plan to the NRC on docket 99902999")
        self.assertEqual(activity[0].metrics["accession_number"], "ML26222A132")
        self.assertEqual(activity[0].occurred_at, "2026-07-27")

    def test_filing_is_dated_by_the_document_not_by_its_release(self):
        written = doc("ML1", "Submittal of a white paper", added="2026-09-23", written="2026-09-15", types=["Letter"],
                      author=["X"])
        self.assertEqual(m.event_date(written, "white_paper"), date(2026, 9, 15))
        # Meeting summaries are indexed by meeting day and titled 'published': the release day is the event.
        self.assertEqual(m.event_date(written, "meeting_summary"), date(2026, 9, 23))
        # A missing or impossible document date falls back to the release day.
        self.assertEqual(m.event_date(dict(written, DocumentDate=None), "white_paper"), date(2026, 9, 23))
        self.assertEqual(m.event_date(dict(written, DocumentDate="2027-01-01"), "white_paper"), date(2026, 9, 23))

    def test_filing_written_before_the_window_is_skipped_and_no_title_claims_a_delay(self):
        # Seen live: a July 2026 letter indexed under its April template date. The old title said
        # 'released 3 months after it was written', which the PDF contradicts.
        stale = doc("ML26196A401", "Deployable Energy - Submission of the Unity Fuel Qualification White Paper",
                    added="2026-07-23", written="2026-04-19", types=["Letter"], author=["ARC Clean Technology"])
        fresh = doc("ML26244A259", "Request for Preapplication Readiness Assessment of the Construction Permit "
                    "Application", added="2026-09-10", written="2026-09-01", types=["Letter"],
                    author=["ARC Clean Technology"])
        _, _, activity = m.docket_signals("99902999", [stale, fresh], self.ARC_HISTORY, ctx())
        self.assertEqual([s.metrics["accession_number"] for s in activity], ["ML26244A259"])
        self.assertEqual(activity[0].occurred_at, "2026-09-01")
        for s in activity:
            self.assertNotRegex(s.title, r"months|released|earlier")
            self.assertGreaterEqual(s.occurred_at, ctx().since.isoformat())

    def test_author_must_be_written_in_the_filing(self):
        letter = "Sincerely,\nRobert Iotti\nARC 100 Project Manager\nRaymond Burski, ARC-100 Licensing Director"
        self.assertFalse(m.author_in_text("Lotti R", letter))  # the index misread 'Iotti'
        self.assertTrue(m.author_in_text("Iotti R", letter))
        self.assertTrue(m.author_in_text("Hernandez  J H", "From: Jorge Hernandez Munoz"))
        self.assertFalse(m.author_in_text("Lee J", "Leeds, United Kingdom"))
        self.assertFalse(m.author_in_text("Burski R", ""))

        docs = [doc("ML26222A132", "ARC Clean Energy LLC - Submittal of a revised ARC-100 Regulatory Engagement Plan",
                    added="2026-08-14", written="2026-07-27", types=["Letter"], author=["ARC Clean Energy, LLC"],
                    names=["Lotti R", "Burski R"])]
        name, _, activity = m.docket_signals("99902999", docs, self.ARC_HISTORY, ctx())
        self.assertEqual([p.name for p in activity[0].people], ["R. Lotti", "R. Burski"])  # as indexed, unchecked
        m.enrich_from_text(activity, ["ARC Clean Technology", "ARC Clean Energy, LLC"],
                           {d["AccessionNumber"]: d for d in docs}, ctx(),
                           fetch=lambda _id: letter + " riotti@arc-cleantech.com", is_live=lambda d: True)
        self.assertEqual([p.name for p in activity[0].people], ["R. Burski"])
        self.assertEqual(activity[0].entity.domain, "arc-cleantech.com")

    def test_people_only_when_the_company_is_the_sole_author(self):
        shared = doc("ML2", "Enclosure 3: Environmental Report", added="2026-09-01", written="2026-09-01",
                     types=["Environmental Report"], author=["Radiant", "Haley & Aldrich, Inc"],
                     names=["Baranwal R", "Smith J"])
        self.assertEqual(m._people(shared, "Radiant Industries, Inc", ["Radiant", "Radiant Industries, Inc"]), [])
        own = dict(shared, AuthorAffiliation=["Radiant"])
        self.assertEqual([p.name for p in m._people(own, "Radiant Industries, Inc", ["Radiant"])],
                         ["R. Baranwal", "J. Smith"])

    def test_meeting_notice_only_says_scheduled_for_a_plausible_future_day(self):
        def notice(title: str) -> dict:
            return m.classify_nrc_doc(doc("ML3", title, added="2026-09-22", written="2026-09-22",
                                          types=["Meeting Notice"], author=["NRC"]))

        self.assertEqual(notice("Pre-Application Meeting with Advanced Float 11/05/2026")["act"],
                         "NRC scheduled a pre-application meeting for 5 Nov 2026")
        self.assertEqual(notice("Notice of Meeting with X 08/25/2026")["act"], "NRC posted a meeting notice")
        self.assertEqual(notice("Notice of Meeting with X 10/08/2206")["act"], "NRC posted a meeting notice")
        self.assertIsNone(notice("Notice of Meeting with X 10/08/2206")["meeting_date"])

    def test_nrc_document_on_several_dockets_goes_to_its_addressee_only(self):
        both = ["99902999", "99902998"]
        feedback = "NRC Staff Feedback Regarding White Paper on Fuel Qualification"
        for_us = doc("ML4", feedback, added="2026-09-11", written="2026-09-01", types=["Letter"],
                     author=["NRC/OAR"], addressee=["ARC Clean Technology"], dockets=both)
        for_them = doc("ML5", feedback, added="2026-09-12", written="2026-09-02", types=["Letter"],
                       author=["NRC/OAR"], addressee=["Somebody Else, Inc"], dockets=both)
        _, _, activity = m.docket_signals("99902999", [for_us, for_them], self.ARC_HISTORY, ctx())
        self.assertEqual([s.metrics["accession_number"] for s in activity], ["ML4"])

    def test_application_wording(self):
        k = m.classify_company_doc({"DocumentTitle": "Submittal of Construction Permit Application for the Unity "
                                    "Facility", "DocumentType": ["Letter"]})
        self.assertEqual(k["act"], "Submitted a construction permit application filing to the NRC")
        k = m.classify_company_doc({"DocumentTitle": "Submittal of Construction Permit Application Part 1: Limited "
                                    "Work Authorization Application", "DocumentType": ["Letter"]})
        self.assertEqual(k["act"], "Submitted part 1 of a construction permit application to the NRC")

    def test_non_proprietary_label_does_not_hide_an_nrc_document(self):
        plan = doc("ML6", "Regulatory Audit Plan for Alva Energy's Topical Report (Non-Proprietary, Cover Letter only)",
                   added="2026-08-20", written="2026-08-17", types=["Letter"], author=["NRC/NRR"])
        self.assertEqual(m.classify_nrc_doc(plan)["kind"], "audit")
        withheld = dict(plan, DocumentTitle="Request for Withholding Proprietary Information from Public Disclosure")
        self.assertIsNone(m.classify_nrc_doc(withheld))

    def test_generic_first_word_cannot_claim_a_domain(self):
        self.assertFalse(m.domain_matches("advancedenergy.com", ["Advanced Float Co., Ltd"]))
        self.assertFalse(m.domain_matches("energy.com", ["Energy Vault, Inc"]))
        self.assertTrue(m.domain_matches("advanced-float.com", ["Advanced Float Co., Ltd"]))
        self.assertTrue(m.domain_matches("amperaglobal.com", ["AMPERA, Inc"]))

    def test_window_paging_dedupes_and_does_not_need_a_count(self):
        rows = [{"document": {"AccessionNumber": f"ML{i:05d}"}} for i in range(204)]
        pages = [rows[:100], rows[99:199], rows[199:]]  # a row repeats when a document lands mid-paging
        calls = []

        def fake_search(filters, *, skip=0, **kw):
            calls.append(skip)
            return {"results": pages[skip // 100]}  # no 'count' key at all

        real, m._search = m._search, fake_search
        try:
            c = ctx()
            got = m.fetch_window(c)
        finally:
            m._search = real
        self.assertEqual(calls, [0, 100, 200])
        self.assertEqual(len(got), 204)
        self.assertEqual(len({d["AccessionNumber"] for d in got}), 204)
        self.assertEqual(c.warnings, [])

    def test_window_keeps_earlier_pages_when_a_later_one_fails(self):
        def fake_search(filters, *, skip=0, **kw):
            if skip:
                raise OSError("timeout")
            return {"count": 250, "results": [{"document": {"AccessionNumber": f"ML{i:05d}"}} for i in range(100)]}

        real, m._search = m._search, fake_search
        try:
            c = ctx()
            got = m.fetch_window(c)
        finally:
            m._search = real
        self.assertEqual(len(got), 100)
        self.assertEqual(len(c.warnings), 1)

    def test_domain_is_dead_only_when_dns_says_so(self):
        import socket
        import urllib.error

        def raising(exc):
            def request(url, **kw):
                raise exc
            return request

        real = m.http.request
        try:
            m.http.request = raising(urllib.error.URLError(socket.gaierror(8, "nodename nor servname provided")))
            self.assertFalse(m.domain_is_live("radiantnuclear.corn"))
            # A slow or misconfigured site still exists; the domain must not flicker between runs.
            m.http.request = raising(TimeoutError("timed out"))
            self.assertTrue(m.domain_is_live("example.com"))
            m.http.request = raising(m.http.HttpError(403, "https://example.com/"))
            self.assertTrue(m.domain_is_live("example.com"))
            m.http.request = lambda url, **kw: "<html>"
            self.assertTrue(m.domain_is_live("example.com"))
        finally:
            m.http.request = real

    def test_malformed_rows_do_not_raise(self):
        junk = [{}, {"DocketNumber": ["99902999"]}, {"DocketNumber": ["99902999"], "DateAdded": "not a date",
                                                      "DocumentTitle": None, "AuthorAffiliation": None},
                doc("ML7", "", added="2026-09-01", written="garbage", types=[], author=["ARC Clean Technology"])]
        name, opened, activity = m.docket_signals("99902999", junk, self.ARC_HISTORY, ctx())
        self.assertEqual(name, "ARC Clean Technology")
        for s in opened + activity:
            s.validate()
        self.assertEqual(m.parse_results({}), [])
        self.assertEqual(m.parse_history({})["count"], 0)
        self.assertEqual(m.docket_signals("99902999", [], {}, ctx()), (None, [], []))


if __name__ == "__main__":
    unittest.main()
