"""Offline tests for the faa_uas collector: parsing and judgement, no network.

Fixtures are real responses saved on 2026-10-01:
  fixtures/regulatory/faa_uasdoc_publicDOCRev.json        50 newest declarations of 506
  fixtures/regulatory/faa_part107_waivers_issued_p0.html  page 0 of the waiver table
"""

from __future__ import annotations

import json
import unittest
from datetime import date
from unittest import mock

from antenna.collectors import faa_uas
from antenna.collectors.base import Context
from antenna.config import FIXTURES_DIR
from antenna.thesis import classify

REG = FIXTURES_DIR / "regulatory"
SINCE = date(2026, 6, 3)
TODAY = date(2026, 10, 1)


def doc_payload() -> dict:
    return json.loads((REG / "faa_uasdoc_publicDOCRev.json").read_text())


def waiver_html() -> str:
    return (REG / "faa_part107_waivers_issued_p0.html").read_text()


# Filing dates as returned by the detail endpoint on 2026-10-01 for the
# fixture's in-window declarations that are past revision 0, plus three at
# revision 0.
CREATED = {
    "RID000000047": date(2022, 11, 9), "RID000000048": date(2022, 11, 9),
    "RID000002365": date(2025, 5, 9), "RID000002190": date(2025, 1, 2),
    "RID000000300": date(2023, 6, 8), "RID000002629": date(2025, 12, 19),
    "RID000002344": date(2025, 4, 30), "RID000001086": date(2024, 2, 2),
    "RID000000283": date(2023, 5, 30), "RID000002673": date(2026, 4, 21),
    "RID000000428": date(2023, 8, 8), "RID000000570": date(2023, 9, 12),
    "RID000001038": date(2024, 1, 19), "RID000002128": date(2024, 12, 17),
    "RID000002731": date(2026, 9, 24), "RID000002674": date(2026, 4, 23),
    "RID000002688": date(2026, 6, 4),
}

# Real rows from deeper in the full list than the 50-row fixture reaches.
# They are what stops Hylio, Aurelia, Matternet and Vision Aerial from
# looking like first-time filers.
OLDER = [
    {"tracking": "RID000002231", "make": "Hylio", "model": "HYL-150", "series": "", "status": "accepted",
     "doc_type": "rid", "rev": 0, "updated": date(2025, 2, 14), "declared_for": "Unmanned Aircraft"},
    {"tracking": "RID000000331", "make": "Aurelia", "model": "X4", "series": "", "status": "accepted",
     "doc_type": "rid", "rev": 0, "updated": date(2023, 7, 12), "declared_for": "Broadcast Module"},
    {"tracking": "RID000000337", "make": "Matternet", "model": "M2 Block 2", "series": "", "status": "accepted",
     "doc_type": "rid", "rev": 0, "updated": date(2023, 7, 12), "declared_for": "Unmanned Aircraft"},
    {"tracking": "RID000000113", "make": "Vision Aerial", "model": "Vector", "series": "", "status": "accepted",
     "doc_type": "rid", "rev": 1, "updated": date(2026, 1, 7), "declared_for": "Unmanned Aircraft"},
]

# The detail response for RID000002674, trimmed to the fields that are read.
DETAIL_2674 = {
    "apiVersion": "1.0.0", "error": {"code": "", "message": ""},
    "data": {"items": [{
        "trackingNumber": "RID000002674",
        "createdAt": "2026-04-23T22:57:58.960Z",
        "updatedAt": "2026-04-23T22:57:58.960Z",
        "publicRevision": {
            "docType": "rid", "updatedAt": "2026-09-15T22:38:57.965Z",
            "makeName": "Amor Fati Industries (dba Seneca)", "modelName": "Argo-3",
            "status": "accepted", "revNumber": "0",
            "complianceMeans": [{"id": 6, "name": "ASTM-F3586-22 with corrections"}],
        },
        "revisions": [{"docRevId": 6184, "status": "accepted"}],
    }]},
}


def summaries(extra_rows=()):
    rows, _ = faa_uas.parse_doc_list(doc_payload())
    out = faa_uas.summarize_makers(rows + list(extra_rows), SINCE, TODAY, CREATED)
    return {s["key"]: s for s in out}


def check_title(test: unittest.TestCase, title: str) -> None:
    test.assertLessEqual(len(title), 110, title)
    test.assertFalse(title.endswith("."), title)
    test.assertTrue(title[0].isupper(), title)
    test.assertNotIn("  ", title)


class DeclarationParsing(unittest.TestCase):
    def test_list(self):
        rows, total = faa_uas.parse_doc_list(doc_payload())
        self.assertEqual(total, 506)
        self.assertEqual(len(rows), 50)
        first = rows[0]
        self.assertEqual(first["tracking"], "RID000000047")
        self.assertEqual(first["make"], "Autel Robotics")
        self.assertEqual(first["model"], "Dragonfish Pro")
        self.assertEqual(first["rev"], 8)
        self.assertEqual(first["updated"], date(2026, 9, 28))
        self.assertEqual(first["status"], "accepted")
        self.assertEqual(sum(1 for r in rows if r["doc_type"] == "oop"), 2)

    def test_list_tolerates_junk(self):
        self.assertEqual(faa_uas.parse_doc_list({}), ([], 0))
        self.assertEqual(faa_uas.parse_doc_list({"data": {"items": [{"makeName": "x"}], "totalItems": "n/a"}}),
                         ([], 0))
        for junk in (None, [], "error", {"data": None}, {"data": []}, {"data": {"items": "none"}},
                     {"data": {"items": [None, "x", 3]}}):
            self.assertEqual(faa_uas.parse_doc_list(junk), ([], 0), junk)
            self.assertEqual(faa_uas.parse_doc_detail(junk), {}, junk)
        odd = {"data": {"items": [{"trackingNumber": 7, "makeName": None, "updatedAt": 0,
                                   "publicRevision": "n/a", "createdAt": None}]}}
        self.assertEqual(faa_uas.parse_doc_list(odd), ([], 0))
        self.assertEqual(faa_uas.parse_doc_detail(odd), {"created": None, "means": []})
        rows, _ = faa_uas.parse_doc_list({"data": {"items": [None, {
            "trackingNumber": "RID1", "makeName": " Acme  Air ", "updatedAt": "2026-09-01T15:00:00.000Z",
            "revNumber": 2, "status": None, "docType": "rid"}]}})
        self.assertEqual([(r["tracking"], r["make"], r["rev"], r["status"]) for r in rows],
                         [("RID1", "Acme Air", 2, "")])

    def test_dates_are_the_us_day(self):
        # Arcsky's Xplorer was updated at 01:25 UTC on 10 February 2026, which
        # the FAA's own page shows to a reader in the US as 9 February.
        self.assertEqual(faa_uas.faa_day("2026-02-10T01:25:46.914Z"), date(2026, 2, 9))
        self.assertEqual(faa_uas.faa_day("2026-09-24T15:53:37.520Z"), date(2026, 9, 24))
        self.assertEqual(faa_uas.faa_day("2026-07-22T23:57:50.535Z"), date(2026, 7, 22))
        self.assertEqual(faa_uas.faa_day("2026-09-24"), date(2026, 9, 24))
        for junk in (None, "", "soon", 20260924, "0000-00-00T00:00:00Z"):
            self.assertIsNone(faa_uas.faa_day(junk), junk)
        rows, _ = faa_uas.parse_doc_list({"data": {"items": [{
            "trackingNumber": "RID000002648", "makeName": "Arcsky", "modelName": "Xplorer",
            "updatedAt": "2026-02-10T01:25:46.914Z", "revNumber": "0", "status": "accepted", "docType": "rid"}]}})
        self.assertEqual(rows[0]["updated"], date(2026, 2, 9))

    def test_detail(self):
        d = faa_uas.parse_doc_detail(DETAIL_2674)
        self.assertEqual(d["created"], date(2026, 4, 23))
        self.assertEqual(d["means"], ["ASTM-F3586-22 with corrections"])
        self.assertEqual(faa_uas.parse_doc_detail({"data": {"items": []}}), {})

    def test_names(self):
        self.assertEqual(faa_uas.split_make("Amor Fati Industries (dba Seneca)"),
                         ("Amor Fati Industries", ["Seneca"]))
        self.assertEqual(faa_uas.split_make("AgEagle (senseFly)"), ("AgEagle (senseFly)", []))
        self.assertEqual(faa_uas.make_key("Hylio, Inc."), faa_uas.make_key("Hylio"))
        self.assertEqual(faa_uas.family_key("Draganfly Innovations"), faa_uas.family_key("Draganfly"))
        self.assertNotEqual(faa_uas.family_key("Drone Volt"), faa_uas.family_key("Drone Defence"))


class DeclarationJudgement(unittest.TestCase):
    def test_first_declaration(self):
        s = summaries(OLDER)["manna drone delivery"]
        self.assertTrue(s["first"])
        sig = faa_uas.doc_signal(s, CREATED, {})
        sig.validate()
        self.assertEqual(sig.kind, "faa_remote_id_doc")
        self.assertEqual(sig.entity.name, "Manna Drone Delivery")
        # "First" is only ever claimed about the FAA's public list.
        self.assertEqual(sig.title, "FAA accepted a Remote ID declaration for aircraft model 3.8.3, its first on the FAA list")
        self.assertEqual(sig.occurred_at, "2026-09-24")
        self.assertEqual(sig.url, "https://uasdoc.faa.gov/listDocs/RID000002731")
        self.assertEqual((sig.value, sig.unit), (1.0, "declarations"))
        self.assertGreaterEqual(sig.strength, 0.85)
        self.assertEqual(sig.metrics["first_declaration"], 1)
        self.assertEqual(sig.metrics["days_filing_to_acceptance"], 0)
        self.assertIn("RID000002731", sig.text)
        self.assertTrue(sig.text.startswith(
            "FAA Remote ID declaration of compliance for an unmanned aircraft (drone) model. "
            "Listed in the FAA UAS Declaration of Compliance system (14 CFR Part 89). "
            "Manufacturer as declared: Manna Drone Delivery. 3.8.3; declaration RID000002731 revision 0; "), sig.text)

    def test_text_says_what_the_filing_is(self):
        # The list has no description, so the evidence text alone must tell a
        # reader, and the thesis classifier, that the filing is about a drone:
        # two terms, not one, and nothing about what the company does beyond it.
        got = summaries(OLDER)
        for s in got.values():
            sig = faa_uas.doc_signal(s, CREATED, {})
            c = classify(sig.text)
            self.assertEqual(c["sector"], "autonomy", sig.text)
            self.assertGreaterEqual(c["fit"], 0.3)
            self.assertLessEqual({"drone", "unmanned"}, set(c["terms"]), sig.text)
        module = faa_uas.doc_signal(got["aurelia"], CREATED, {})
        self.assertTrue(module.text.startswith(
            "FAA Remote ID declaration of compliance for a Remote ID broadcast module, "
            "a device that adds Remote ID to an unmanned aircraft (drone). Listed in "), module.text)
        several = faa_uas.doc_signal(got["hylio"], CREATED, {})
        self.assertTrue(several.text.startswith(
            "4 FAA Remote ID declarations of compliance, each for an unmanned aircraft (drone) model. "), several.text)
        aircraft = {"declared_for": "Unmanned Aircraft"}
        mod = {"declared_for": "Broadcast Module"}
        self.assertEqual(faa_uas._filing_words([aircraft, mod]),
                         "2 FAA Remote ID declarations of compliance, "
                         "for unmanned aircraft (drone) models and Remote ID broadcast modules")
        self.assertEqual(faa_uas._filing_words([mod, mod]),
                         "2 FAA Remote ID declarations of compliance, each for a Remote ID broadcast module, "
                         "a device that adds Remote ID to an unmanned aircraft (drone)")

    def test_dba_becomes_alias(self):
        sig = faa_uas.doc_signal(summaries(OLDER)["amor fati industries"], CREATED,
                                 {"RID000002674": ["ASTM-F3586-22 with corrections"]})
        self.assertEqual(sig.entity.name, "Amor Fati Industries")
        self.assertEqual(sig.entity.aliases, ["Seneca"])
        self.assertEqual(sig.entity.one_liner, "Maker of the Argo-3 unmanned aircraft")
        self.assertEqual(sig.metrics["days_filing_to_acceptance"], 145)
        self.assertIn("ASTM-F3586-22 with corrections", sig.text)
        self.assertIsNone(sig.entity.domain)

    def test_new_declarations_by_an_existing_maker(self):
        s = summaries(OLDER)["hylio"]
        self.assertFalse(s["first"])
        self.assertEqual((len(s["new"]), len(s["amended"]), s["rid_on_list"]), (4, 0, 5))
        self.assertEqual(s["spellings"], ["Hylio", "Hylio, Inc."])
        sig = faa_uas.doc_signal(s, CREATED, {})
        self.assertEqual(
            sig.title,
            "FAA accepted 4 new Remote ID declarations since Jun 2026: PEGASUS HYL-30 and 3 more (5 on the FAA list)")
        # The four were accepted on four days from 8 June; the signal carries the latest.
        self.assertEqual(sorted(r["updated"] for r in s["new"])[0], date(2026, 6, 8))
        self.assertEqual(sig.occurred_at, "2026-09-08")
        self.assertEqual(sig.entity.aliases, ["Hylio, Inc."])
        self.assertTrue(0.35 <= sig.strength <= 0.6, sig.strength)
        self.assertEqual(sig.metrics["first_declaration"], 0)

    def test_truncated_list_would_overclaim(self):
        # Without the older rows the same four declarations look like a debut.
        # This is why the collector refuses "first" unless it read the whole list.
        self.assertTrue(summaries()["hylio"]["first"])

    def test_broadcast_module_is_named_as_such(self):
        sig = faa_uas.doc_signal(summaries(OLDER)["aurelia"], CREATED, {})
        self.assertEqual(sig.title,
                         "FAA accepted a new Remote ID declaration, for broadcast module X8 PRO (2 on the FAA list)")
        self.assertEqual(sig.entity.one_liner, "Maker of the X8 PRO Remote ID broadcast module")
        self.assertLess(sig.strength, faa_uas.doc_signal(summaries(OLDER)["matternet"], CREATED, {}).strength)

    def test_very_large_makers_are_skipped(self):
        self.assertTrue(faa_uas.is_big_maker("Autel Robotics"))
        self.assertTrue(faa_uas.is_big_maker("DJI"))
        self.assertFalse(faa_uas.is_big_maker("Autelia"))
        self.assertFalse(faa_uas.is_big_maker("Skydio"))
        # Six amendments in the window, none of them news. The name key keeps
        # "Robotics", and the size filter matches on the first word all the same.
        self.assertEqual(faa_uas.make_key("Autel Robotics"), "autel robotics")
        self.assertEqual([k for k in summaries(OLDER) if k.startswith("autel")], [])

    def test_amendments_are_routine(self):
        # Autel's six amended declarations are the only multi-row case in the
        # fixture, so the size filter is lifted to exercise the wording.
        with mock.patch.object(faa_uas, "_BIG_MAKERS", ()):
            s = summaries(OLDER)["autel robotics"]
        self.assertEqual((len(s["new"]), len(s["amended"])), (0, 6))
        sig = faa_uas.doc_signal(s, CREATED, {})
        self.assertEqual(
            sig.title,
            "FAA accepted amendments to 6 existing Remote ID declarations since Jul 2026: Dragonfish Pro and 5 more")
        check_title(self, sig.title)
        self.assertLessEqual(sig.strength, 0.3)
        self.assertEqual(sig.metrics["latest_revision"], 8)
        one = faa_uas.doc_signal(summaries(OLDER)["skydio"], CREATED, {})
        self.assertEqual(one.title,
                         "FAA accepted revision 2 of the Remote ID declaration for aircraft model 2 SDRC2V1")
        self.assertEqual(one.metrics["over_people_declarations_on_list"], 1)
        self.assertLess(one.strength, sig.strength)

    def test_second_spelling_blocks_a_first_claim(self):
        # "Draganfly" (Apex, May 2026) is outside the window and its own key,
        # yet it must stop "Draganfly Innovations" from ever reading as new.
        s = summaries(OLDER)["draganfly innovations"]
        self.assertFalse(s["first"])
        self.assertEqual(len(s["amended"]), 1)

    def test_revised_declaration_filed_inside_the_window_is_new(self):
        # Hypothetical filing date, to exercise the rule: DeltaQuad's Evo is at
        # revision 1, so it is an amendment unless it was filed after `since`.
        self.assertEqual(len(summaries(OLDER)["deltaquad"]["amended"]), 1)
        rows, _ = faa_uas.parse_doc_list(doc_payload())
        late = dict(CREATED, RID000002365=date(2026, 7, 1))
        s = {x["key"]: x for x in faa_uas.summarize_makers(rows, SINCE, TODAY, late)}["deltaquad"]
        self.assertEqual((len(s["new"]), len(s["amended"])), (1, 0))

    def test_window_and_type_filters(self):
        got = summaries(OLDER)
        # Skydio's over-people declaration and anything before 2026-06-03 are not signals.
        self.assertNotIn("sbg", got)
        self.assertNotIn("avol", got)  # accepted 2026-05-27, one week before the window opens
        self.assertEqual(len(got), 17)
        for s in got.values():
            sig = faa_uas.doc_signal(s, CREATED, {})
            sig.validate()
            check_title(self, sig.title)
            self.assertTrue(SINCE.isoformat() <= sig.occurred_at <= TODAY.isoformat())
            self.assertTrue(0.15 <= sig.strength <= 0.96)

    def test_strength_order(self):
        got = summaries(OLDER)
        first = faa_uas.doc_strength(got["manna drone delivery"])
        new = faa_uas.doc_strength(got["matternet"])
        amended = faa_uas.doc_strength(got["brinc"])
        self.assertGreater(first, new)
        self.assertGreater(new, amended)

    def test_long_model_lists_stay_under_the_title_limit(self):
        names = [f"MODEL-NUMBER-{i:02d}-EXTENDED" for i in range(12)]
        title = faa_uas._fit_list("FAA accepted its first 12 Remote ID declarations: ", names)
        self.assertLessEqual(len(title), 110)
        self.assertTrue(title.endswith("more"))

    def test_counts_are_left_out_when_the_list_was_partly_read(self):
        s = dict(summaries(OLDER)["hylio"], complete=False)
        sig = faa_uas.doc_signal(s, CREATED, {})
        self.assertNotIn("on the FAA list", sig.title)
        self.assertNotIn("remote_id_declarations_on_list", sig.metrics)
        check_title(self, sig.title)

    def test_first_of_several(self):
        s = dict(summaries()["hylio"])  # without the older rows: four declarations, all new
        sig = faa_uas.doc_signal(s, CREATED, {})
        self.assertEqual(
            sig.title,
            "FAA accepted 4 Remote ID declarations since Jun 2026, its first on the FAA list: "
            "PEGASUS HYL-30 and 3 more")
        check_title(self, sig.title)


class WaiverParsing(unittest.TestCase):
    def setUp(self):
        self.rows = faa_uas.parse_waiver_page(waiver_html())

    def test_rows(self):
        self.assertEqual(len(self.rows), 25)
        r = self.rows[0]
        self.assertEqual(r["issued"], date(2026, 11, 2))
        self.assertEqual(r["expires"], date(2026, 11, 9))
        self.assertEqual(r["company"], "Skyworx Drone Shows, LLC")
        self.assertEqual(r["person"], "Taylor Woodall")
        self.assertEqual(r["pdf"], "https://www.faa.gov/sites/faa.gov/files/107W-2026-00079-Taylor-Woodall-CoW.pdf")
        self.assertEqual(r["number"], "107W-2026-00079")
        self.assertEqual(r["regs"], ["107.51"])

    def test_percepto_row(self):
        r = next(x for x in self.rows if x["company"].startswith("Percepto"))
        self.assertEqual(r["company"], "Percepto Robotics Inc.")
        self.assertEqual(r["person"], "Raviv Raz")
        self.assertEqual(r["issued"], date(2026, 9, 30))
        self.assertEqual(r["expires"], date(2028, 8, 31))
        self.assertEqual(r["regs"], ["107.31", "107.33", "107.35", "107.39", "107.145"])
        # An encoded space in the link must survive as written, not be encoded twice.
        self.assertEqual(r["pdf"], "https://www.faa.gov/sites/faa.gov/files/107W-2026-02228-A-Raviv%20Raz-CoW.pdf")
        self.assertEqual(r["number"], "107W-2026-02228")

    def test_regulation_typos(self):
        self.assertEqual(faa_uas.parse_regulations("107.31, 107..33, 107.39, 107.51(b0, 107.145"),
                         ["107.31", "107.33", "107.39", "107.51", "107.145"])
        self.assertEqual(faa_uas.parse_regulations("107.31, 107.33(b) & (c)(2), 107.51(c),107.51(d). 107.145"),
                         ["107.31", "107.33", "107.51", "107.145"])
        self.assertEqual(faa_uas.parse_regulations("107.39 107.145"), ["107.39", "107.145"])
        self.assertEqual(faa_uas.parse_regulations(""), [])

    def test_no_table(self):
        self.assertEqual(faa_uas.parse_waiver_page("<html><body>Access Denied</body></html>"), [])
        self.assertEqual(faa_uas.parse_waiver_page(""), [])

    def test_glued_person_name_is_removed(self):
        page = (
            '<table><tr>'
            '<td class="views-field views-field-field-issue-date"><time datetime="2026-07-09T12:00:00Z">2026-07-09</time></td>'
            '<td class="views-field views-field-field-expiration-date"><time datetime="2028-04-30T12:00:00Z">2028-04-30</time></td>'
            '<td class="views-field views-field-field-company-name">Amazon.com Services LLCWilson Ragle </td>'
            '<td class="views-field views-field-field-responsible-person">'
            '<a href="/sites/faa.gov/files/107W-2026-01882-Wilson-Ragle-CoW.pdf">Wilson Ragle</a> (pdf)</td>'
            '<td class="views-field views-field-field-waivered-regulation">107.51(c), 107.51(d)</td>'
            '</tr></table>'
        )
        (row,) = faa_uas.parse_waiver_page(page)
        self.assertEqual(row["company"], "Amazon.com Services LLC")
        self.assertEqual(row["person"], "Wilson Ragle")
        # A company that is really named after the person keeps its whole name.
        named = page.replace("Amazon.com Services LLCWilson Ragle", "Aerial Surveys by Wilson Ragle")
        self.assertEqual(faa_uas.parse_waiver_page(named)[0]["company"], "Aerial Surveys by Wilson Ragle")
        # Stray punctuation at the end would stop the name matching other sources.
        comma = page.replace("Amazon.com Services LLCWilson Ragle", "Hoverfly Technologies Inc,")
        self.assertEqual(faa_uas.parse_waiver_page(comma)[0]["company"], "Hoverfly Technologies Inc")
        # The name key drops the legal form and keeps the descriptive word.
        self.assertEqual(faa_uas.make_key(faa_uas.parse_waiver_page(comma)[0]["company"]), "hoverfly technologies")

    def test_merge_recovers_rows_the_paging_skipped(self):
        # Live on 2026-10-01 the table served one row on both page 0 and page 1
        # and left DroneDeploy's waiver of the same date off every page; the
        # regulation search still returned it.
        skipped = next(r for r in self.rows if r["company"] == "DroneDeploy")
        main = [r for r in self.rows if r is not skipped]
        main.append(dict(main[-1]))  # the row served twice
        merged, stats = faa_uas.merge_listings(main, [skipped, dict(main[0])])
        self.assertEqual(stats, {"repeated": 1, "recovered": 1})
        self.assertEqual(len(merged), 25)
        self.assertIn("DroneDeploy", [r["company"] for r in merged])
        self.assertEqual([r["issued"] for r in merged], sorted((r["issued"] for r in merged), reverse=True))


class WaiverJudgement(unittest.TestCase):
    def test_who_is_dropped(self):
        cases = {
            ("Phillipsburg Police Department", "Brad Kisselbach"): "public safety or government",
            ("Eastern Green Fire Territory", "Jeremy Inman"): "public safety or government",
            ("CITY OF MESA", "Reynaldo Morga Jr"): "public safety or government",
            ("NCDOT –NC Division of Aviation", "John Riley Beaman, III"): "public safety or government",
            ("Tennessee Valley Authority", "Zackary Whittle"): "public safety or government",
            ("Brad Johnston", "McMinn County Sheriff's Department"): "public safety or government",
            ("The University of Alabama", "Jonathan Norris"): "education or research institution",
            ("Johns Hopkins Applied Physics Lab", "Garrett Krol"): "education or research institution",
            ("St Joseph's Health", "Kevin Kan"): "hospital or health system",
            ("The Aspen Utility Company LLC", "Ernest W Spicer Jr"): "utility, resource or property operator",
            ("TD Bank", "Jeremy Russ"): "utility, resource or property operator",
            ("Cache Creek Casino Resort", "Donald Nolen"): "utility, resource or property operator",
            ("Kevin Johnson Visuals LLC", "Kevin Johnson"): "film, photo or drone show",
            ("Firefly Drone Shows", "Jonathan Hearl"): "film, photo or drone show",
            ("Walmart, Inc.", "Patricia Morgan"): "large corporate drone user",
            ("Amazon.com Services LLC", "Wilson Ragle"): "large corporate drone user",
            ("ExxonMobil", "Jonathan Cain"): "large corporate drone user",
            ("BRYAN Keith STANCIL", "BRYAN Keith STANCIL"): "individual",
            ("THEODORE G IGLEHART", "THEODORE G IGLEHART III"): "individual",
            ("ERIC FITZSIMONS", "RYAN FITZSIMONS"): "individual",
            ("Pburke", "Peter Burke"): "individual",
            ("James Michael", "James Richard"): "individual",
            ("", "Andrew Todd Rush"): "individual",
            # The table lists "Physics A"; waiver 107W-2026-00423 itself is issued to "Physics AI".
            ("Physics A", "John M. Pierre"): "name cut off in the FAA table",
            ("Axon Enterprises", "William Sallee"): "large corporate drone user",
            ("Acuren Inspection", "Eileen Lockhart"): "engineering, inspection, consulting or security firm",
            ("Energix US, Inc.", "James Pioli"): "large corporate drone user",
            ("Tesla Inc", "Someone Else"): "large corporate drone user",
            ("AUI Partners", "Javier Cantu"): "drone user in another trade",
            ("Merjent, Inc.", "Michael Brack"): "drone user in another trade",
            ("MJ Inspection Consultants", "Joel Manfred"): "engineering, inspection, consulting or security firm",
            ("Meridian Land Services", "Patrick Livernois"): "engineering, inspection, consulting or security firm",
            ("Enhanced Patrol", "Jonathan Chase Frith"): "engineering, inspection, consulting or security firm",
            ("W&A Engineering", "Someone Else"): "engineering, inspection, consulting or security firm",
            ("FAA Flight Program Operations", "Someone Else"): "public safety or government",
            ("UMD UAS Research and Operations Center", "Someone Else"): "education or research institution",
            ("Alaska Remote Imaging", "Someone Else"): "film, photo or drone show",
        }
        for (company, person), want in cases.items():
            self.assertEqual(faa_uas.drop_reason(company, person), want, company)

    def test_who_is_kept(self):
        for company, person in [
            ("Percepto Robotics Inc.", "Raviv Raz"), ("Neros Technologies", "LEVI JOHNSON"),
            ("Avol", "Nathan Poon"), ("blueflite", "Joshua Greenway"), ("Kettering Industries", "Ansel Kim"),
            ("Manna Drone Delivery, LLC", "Jenel Davis Sanders"), ("Flytrex", "Brent Eliason"),
            ("DroneDeploy", "Matthew Lyon"), ("FireBot Labs Inc.", "Someone Else"),
            ("Mansfield Computing Technologies", "William Mansfield"), ("Blue Vigil", "Carl Miller"),
            ("Birdstop", "Keith Miao"), ("Rainmaker Technology Corporation", "Samuel Kim"),
            ("Falcon Unmanned Systems LLc", "Eric Michael Ebert"), ("Nightingale Security", "Kyle Roh"),
            ("Skydio, Inc.", "Jeff Horne"), ("Censys Technologies Corporation", "Peter Frank"),
        ]:
            self.assertIsNone(faa_uas.drop_reason(company, person), company)

    def test_name_cue(self):
        self.assertEqual(faa_uas.name_cue("Percepto Robotics Inc."), 2)
        self.assertEqual(faa_uas.name_cue("Overwatch Aero"), 2)
        self.assertEqual(faa_uas.name_cue("LandSkyAI, LLC"), 2)
        self.assertEqual(faa_uas.name_cue("Neros Technologies"), 1)
        self.assertEqual(faa_uas.name_cue("BrightAI Corporation"), 1)
        self.assertEqual(faa_uas.name_cue("Avol"), 0)
        self.assertEqual(faa_uas.name_cue("Fairfield Paint"), 0)  # 'air' inside a word is not a cue

    def test_which_waivers_qualify(self):
        self.assertTrue(faa_uas.qualifies(["107.31"]))
        self.assertTrue(faa_uas.qualifies(["107.39", "107.145"]))
        self.assertTrue(faa_uas.qualifies(["107.35"]))
        self.assertTrue(faa_uas.qualifies(["107.29", "107.31", "107.35"]))
        self.assertFalse(faa_uas.qualifies(["107.51"]))
        self.assertFalse(faa_uas.qualifies(["107.25"]))
        self.assertFalse(faa_uas.qualifies(["107.29", "107.35"]))  # the drone light show template
        self.assertFalse(faa_uas.qualifies([]))

    def test_selection_on_the_fixture_page(self):
        rows = faa_uas.dedupe_waivers(faa_uas.parse_waiver_page(waiver_html()))
        keep, dropped = faa_uas.select_waivers(rows, SINCE, TODAY)
        self.assertEqual([r["company"] for r in keep], ["Percepto Robotics Inc.", "DroneDeploy"])
        self.assertEqual(dropped, {
            "dated in the future": 5,
            "other waiver type": 8,
            "public safety or government": 3,
            "utility, resource or property operator": 4,
            "individual": 3,
        })
        # Nothing older than the window comes through.
        self.assertEqual(faa_uas.select_waivers(rows, date(2026, 10, 1), date(2026, 10, 1))[0], [])

    def test_over_people_alone_needs_a_drone_company(self):
        def row(company, regs):
            return {"issued": date(2026, 8, 31), "expires": date(2028, 8, 31), "company": company,
                    "person": "Parker Moseley", "pdf": "https://www.faa.gov/sites/faa.gov/files/x.pdf",
                    "number": "107W-2026-01470", "regs_raw": ", ".join(regs), "regs": regs}

        people = ["107.39", "107.145"]
        why = "over people only, by a drone user or pilot for hire"
        # Gradex is an earthworks contractor; Avol holds a Remote ID declaration as a maker.
        for company in ("Gradex Inc", "MI Technical Solutions", "Lucky Drone Services, LLC", "Avol"):
            keep, dropped = faa_uas.select_waivers([row(company, people)], SINCE, TODAY)
            self.assertEqual((keep, dropped), ([], {why: 1}), company)
        for company, regs, makers in [("Gradex Inc", ["107.31", "107.39"], set()), ("Skydio, Inc.", people, set()),
                                      ("Avol", people, {"avol"})]:
            keep, dropped = faa_uas.select_waivers([row(company, regs)], SINCE, TODAY, makers)
            self.assertEqual((len(keep), dropped), (1, {}), company)

    def test_holder_is_matched_to_the_declaration_list(self):
        # Live on 2026-10-01 the declaration list had "Percepto" and "Rainmaker"
        # and the waiver table "Percepto Robotics Inc." and "Rainmaker Technology
        # Corporation": one descriptive word apart, the same company.
        makers = {"percepto", "percepto airmax", "rainmaker", "avol", "flyby robotics", "genesis ai",
                  "drone robotics", "censys technologies"}
        bare = faa_uas.bare_maker_keys(makers)
        for company in ("Percepto Robotics Inc.", "Rainmaker Technology Corporation", "Avol", "AVOL, LLC",
                        "Flyby Robotics", "Flyby", "Censys Technologies Corporation", "Censys"):
            self.assertTrue(faa_uas.is_listed_maker(company, makers, bare), company)
        # A different descriptive word is a different company; so is a longer
        # name, and a generic word alone ties nothing together.
        for company in ("Genesis Robotics", "Percepto Air", "Rainmaker Drone Shows", "Avolon", "Drone",
                        "Drone Labs", "Censys Aerospace"):
            self.assertFalse(faa_uas.is_listed_maker(company, makers, bare), company)
        self.assertFalse(faa_uas.is_listed_maker("Percepto Robotics Inc.", set()))

        def row(company):
            return {"issued": date(2026, 8, 31), "expires": date(2028, 8, 31), "company": company,
                    "person": "Samuel Kim", "pdf": "https://www.faa.gov/sites/faa.gov/files/x.pdf",
                    "number": "107W-2026-01470", "regs_raw": "107.39, 107.145", "regs": ["107.39", "107.145"]}

        # "Rajant" says nothing about drones, so flight over people alone is
        # dropped unless the declaration list names it as a maker.
        why = "over people only, by a drone user or pilot for hire"
        self.assertEqual(faa_uas.select_waivers([row("Rajant Corporation")], SINCE, TODAY, makers), ([], {why: 1}))
        keep, dropped = faa_uas.select_waivers([row("Rajant Corporation")], SINCE, TODAY, makers | {"rajant"})
        self.assertEqual((len(keep), dropped), (1, {}))
        keep, dropped = faa_uas.select_waivers([row("Rajant Technologies, Inc.")], SINCE, TODAY, makers | {"rajant"})
        self.assertEqual((len(keep), dropped), (1, {}))

    def test_titles(self):
        exp = date(2028, 8, 31)
        self.assertEqual(faa_uas.waiver_title(["107.31"], date(2030, 9, 30)),
                         "FAA waiver to fly drones beyond visual line of sight, valid to Sep 2030")
        self.assertEqual(faa_uas.waiver_title(["107.31", "107.33", "107.39", "107.145"], date(2027, 8, 31)),
                         "FAA waiver to fly drones beyond visual line of sight, over people and over moving vehicles, "
                         "valid to Aug 2027")
        self.assertEqual(faa_uas.waiver_title(["107.31", "107.33", "107.35", "107.39", "107.145"], exp),
                         "FAA waiver to fly multiple drones per pilot beyond visual line of sight and over people, "
                         "valid to Aug 2028")
        self.assertEqual(faa_uas.waiver_title(["107.35"], date(2028, 10, 31)),
                         "FAA waiver to fly multiple drones per pilot, valid to Oct 2028")
        self.assertEqual(faa_uas.waiver_title(["107.39"], None), "FAA waiver to fly drones over people")
        for regs in (["107.31"], ["107.31", "107.35", "107.39", "107.145"], ["107.39", "107.145"], ["107.35"]):
            check_title(self, faa_uas.waiver_title(regs, exp))

    def test_strength(self):
        s = faa_uas.waiver_strength
        both = s(["107.31", "107.35"], cue=2, term_days=1400, others_on_list=None)
        bvlos = s(["107.31"], cue=2, term_days=1400, others_on_list=None)
        people = s(["107.39", "107.145"], cue=2, term_days=1400, others_on_list=None)
        self.assertGreater(both, bvlos)
        self.assertGreater(bvlos, people)
        self.assertLessEqual(people, 0.3)
        # A name that says nothing about drones stays in the routine band, whatever was waived.
        self.assertEqual(s(["107.31", "107.35", "107.39"], cue=0, term_days=1400, others_on_list=14), 0.3)
        self.assertLessEqual(s(["107.31", "107.35", "107.39"], cue=1, term_days=1400, others_on_list=14), 0.5)
        # A six-week waiver is a campaign, not a standing operation.
        self.assertLess(s(["107.31"], cue=2, term_days=46, others_on_list=None), bvlos)
        # A lone waiver earns nothing extra: it may be a reissue.
        self.assertEqual(s(["107.31"], cue=2, term_days=1400, others_on_list=0), bvlos)
        # The fourteenth waiver of a company (Skydio has 13 others listed) is
        # routine for it and must not outrank the same waiver held alone.
        self.assertLess(s(["107.31"], cue=2, term_days=1400, others_on_list=13), bvlos)
        self.assertLess(s(["107.31"], cue=2, term_days=1400, others_on_list=13),
                        s(["107.31"], cue=2, term_days=1400, others_on_list=1))
        top = s(["107.31", "107.35", "107.39"], cue=2, term_days=1400, others_on_list=None)
        self.assertTrue(0.6 <= top <= 0.8, top)
        self.assertGreaterEqual(s(["107.39"], cue=0, term_days=20, others_on_list=50), 0.15)
        # A maker with an aircraft of its own on the FAA list outranks an operator with the same waiver.
        self.assertGreater(s(["107.31"], cue=2, term_days=1400, others_on_list=None, maker=True), bvlos)
        # A pilot-for-hire name stays in the routine band whatever was waived.
        self.assertEqual(s(["107.31", "107.35"], cue=2, term_days=1400, others_on_list=None, service=True), 0.3)

    def test_signal(self):
        rows = faa_uas.parse_waiver_page(waiver_html())
        row = next(r for r in rows if r["company"].startswith("Percepto"))
        sig = faa_uas.waiver_signal(row, on_list=None, in_window=1)
        sig.validate()
        self.assertEqual(sig.kind, "faa_part107_waiver")
        self.assertEqual(sig.family, "regulatory")
        self.assertEqual(sig.entity.name, "Percepto Robotics Inc.")
        self.assertIsNone(sig.entity.domain)
        self.assertEqual(sig.occurred_at, "2026-09-30")
        self.assertEqual(sig.url, row["pdf"])
        self.assertEqual((sig.value, sig.unit), (701.0, "days valid"))
        self.assertEqual(sig.strength, 0.61)
        self.assertEqual(sig.people[0].name, "Raviv Raz")
        self.assertEqual(sig.metrics["bvlos"], 1)
        self.assertEqual(sig.metrics["multiple_aircraft_per_pilot"], 1)
        self.assertEqual(sig.metrics["validity_days"], 701)
        self.assertNotIn("other_waivers_on_list", sig.metrics)  # the whole table was not read
        self.assertIn("107W-2026-02228", sig.text)
        # The table describes nobody, so the text says what the paper is, and
        # that alone puts the row on thesis with two terms.
        self.assertTrue(sig.text.startswith(
            "FAA Part 107 certificate of waiver for small unmanned aircraft (drone) operations, "
            "number 107W-2026-02228, issued 2026-09-30 to Percepto Robotics Inc., responsible person Raviv Raz, "
            "expires 2028-08-31. Waived regulations as listed: "), sig.text)
        c = classify(sig.text)
        self.assertEqual(c["sector"], "autonomy")
        self.assertLessEqual({"drone", "unmanned"}, set(c["terms"]))
        self.assertNotIn("first", sig.title.lower())
        self.assertEqual(sig.entity.aliases, [])
        self.assertEqual(sig.metrics["remote_id_maker"], 0)
        with_history = faa_uas.waiver_signal(row, on_list=3, in_window=1)
        self.assertEqual(with_history.metrics["other_waivers_on_list"], 2)
        self.assertLess(with_history.strength, sig.strength)

    def test_signal_details(self):
        base = {"issued": date(2026, 9, 14), "expires": date(2027, 8, 31), "company": "Avol",
                "person": "MARCELL HAYWOOD", "pdf": "https://www.faa.gov/sites/faa.gov/files/107W-2026-01754-x.pdf",
                "number": "107W-2026-01754", "regs_raw": "107.31, 107.33, 107.39, 107.145",
                "regs": ["107.31", "107.33", "107.39", "107.145"]}
        plain = faa_uas.waiver_signal(base, on_list=None, in_window=1)
        maker = faa_uas.waiver_signal(base, on_list=None, in_window=1, maker=True)
        # "Avol" says nothing about drones, but a maker on the declaration list is a drone company.
        self.assertEqual((plain.strength, plain.metrics["name_cue"]), (0.3, 0))
        self.assertEqual((maker.strength, maker.metrics["name_cue"], maker.metrics["remote_id_maker"]), (0.55, 2, 1))
        # Shouted names are set in ordinary case; the words are not changed.
        self.assertEqual(plain.people[0].name, "Marcell Haywood")
        self.assertEqual(faa_uas._as_name("EARNEST MCCOY"), "Earnest McCoy")
        self.assertEqual(faa_uas._as_name("THEODORE G IGLEHART III"), "Theodore G Iglehart III")
        self.assertEqual(faa_uas._as_name("Kyle VanNote"), "Kyle VanNote")
        # A trading name becomes an alias, as it does for declarations.
        dba = faa_uas.waiver_signal(dict(base, company="Remnant Technology Inc. /d/b/a Drone Crop Services"),
                                    on_list=None, in_window=1)
        self.assertEqual((dba.entity.name, dba.entity.aliases), ("Remnant Technology Inc.", ["Drone Crop Services"]))
        service = faa_uas.waiver_signal(dict(base, company="Alt Spec UAS Services LLC"), on_list=None, in_window=1)
        self.assertEqual(service.strength, 0.3)
        # An expiry before the issue date is a typo in the table, not a validity.
        typo = faa_uas.waiver_signal(dict(base, expires=date(2025, 8, 31)), on_list=None, in_window=1)
        self.assertEqual((typo.value, typo.unit), (None, None))
        self.assertNotIn("valid to", typo.title)
        self.assertNotIn("validity_days", typo.metrics)
        typo.validate()
        unnumbered = faa_uas.waiver_signal(dict(base, number=None), on_list=None, in_window=1)
        self.assertTrue(unnumbered.text.startswith(
            "FAA Part 107 certificate of waiver for small unmanned aircraft (drone) operations "
            "issued 2026-09-14 to Avol, responsible person MARCELL HAYWOOD, expires 2027-08-31."), unnumbered.text)
        self.assertGreaterEqual(classify(unnumbered.text)["fit"], 0.3)


class CollectOffline(unittest.TestCase):
    """The whole collector against fixtures, with the HTTP layer replaced."""

    def run_collect(self, limit, total_items=506, repeat_a_row=False):
        calls = {"get": [], "get_json": []}
        page0 = waiver_html()

        def fake_get_json(url, params=None, **kw):
            calls["get_json"].append((url, params, kw.get("ttl")))
            if url == faa_uas.DOC_API:
                payload = doc_payload()
                payload["data"]["totalItems"] = total_items
                if repeat_a_row:  # the list shifted between pages: one row twice, one never served
                    payload["data"]["items"][-1] = dict(payload["data"]["items"][0])
                if params["pageIndex"] > 0:
                    payload["data"]["items"] = []
                return payload
            if url.endswith("/RID000002674"):
                return DETAIL_2674
            raise faa_uas.http.HttpError(404, url)

        def fake_get(url, params=None, headers=None, **kw):
            calls["get"].append((url, params, headers))
            body = page0 if params.get("page") == 0 else "<table></table>"
            return body, {"date": "Fri, 02 Oct 2026 00:00:00 GMT"}

        ctx = Context(today=TODAY, lookback_days=120, limit=limit)
        with mock.patch.object(faa_uas.http, "get_json", fake_get_json), \
                mock.patch.object(faa_uas.http, "get", fake_get), \
                mock.patch.object(faa_uas.time, "sleep", lambda s: None):
            signals = list(faa_uas.collect(ctx))
        return signals, ctx, calls

    def test_both_feeds_and_the_limit(self):
        signals, ctx, calls = self.run_collect(limit=10)
        kinds = [s.kind for s in signals]
        self.assertEqual(kinds.count("faa_remote_id_doc"), 5)  # half the limit
        self.assertEqual(kinds.count("faa_part107_waiver"), 2)
        for s in signals:
            s.validate()
            check_title(self, s.title)
            self.assertEqual(s.source, "faa_uas")
            self.assertTrue(SINCE.isoformat() <= s.occurred_at <= TODAY.isoformat())
        # A failed detail lookup is a warning, not a lost run.
        self.assertTrue(any("detail failed" in w for w in ctx.warnings))
        # www.faa.gov only answers a plain tool User-Agent.
        self.assertTrue(all(h == {"User-Agent": "curl/8.7.1"} for _, _, h in calls["get"]))
        searched = {p.get(faa_uas.WAIVER_SEARCH_PARAM) for _, p, _ in calls["get"]}
        self.assertEqual(searched, {None, "107.31", "107.35", "107.39"})
        # One detail request per in-window declaration of the five makers, kept for at least a day.
        details = [(u, ttl) for u, _, ttl in calls["get_json"] if u != faa_uas.DOC_API]
        self.assertTrue(details)
        self.assertEqual(len(details), len(set(details)))
        self.assertTrue(all(ttl >= 24 * 3600 for _, ttl in details))
        # No signal about a very large maker, and none left with a blank or padded name.
        self.assertNotIn("Autel Robotics", [s.entity.name for s in signals])
        for s in signals:
            self.assertEqual(s.entity.name, s.entity.name.strip(" ,;"))
            self.assertIsNone(s.entity.github)
            self.assertIsNone(s.entity.domain)

    def test_no_first_claim_from_a_partial_list(self):
        # The fake API says 506 declarations exist but serves 50.
        signals, ctx, _ = self.run_collect(limit=40)
        docs = [s for s in signals if s.kind == "faa_remote_id_doc"]
        self.assertTrue(docs)
        self.assertTrue(any("no 'first declaration' claims" in w for w in ctx.warnings))
        for s in docs:
            self.assertNotIn("first", s.title)
            self.assertEqual(s.metrics["first_declaration"], 0)
            self.assertLess(s.strength, 0.85)
            # A count over a partly read list would be too low, so none is given.
            self.assertNotIn("on the FAA list", s.title)
            self.assertNotIn("remote_id_declarations_on_list", s.metrics)

    def test_a_repeated_row_does_not_pass_for_the_whole_list(self):
        # 50 rows served against a total of 50, but only 49 distinct declarations.
        signals, ctx, _ = self.run_collect(limit=40, total_items=50, repeat_a_row=True)
        self.assertTrue(any("read 49 of 50" in w for w in ctx.warnings), ctx.warnings)
        docs = [s for s in signals if s.kind == "faa_remote_id_doc"]
        self.assertTrue(docs)
        self.assertTrue(all(s.metrics["first_declaration"] == 0 for s in docs))
        # The repeated row is counted once.
        self.assertEqual(len({s.entity.name for s in docs}), len(docs))

    def test_makers_are_handed_to_the_waiver_feed(self):
        makers: set[str] = set()
        ctx = Context(today=TODAY, lookback_days=120, limit=4)

        def fake_get_json(url, params=None, **kw):
            if url == faa_uas.DOC_API:
                payload = doc_payload()
                if params["pageIndex"] > 0:
                    payload["data"]["items"] = []
                return payload
            raise faa_uas.http.HttpError(404, url)

        with mock.patch.object(faa_uas.http, "get_json", fake_get_json):
            list(faa_uas._collect_docs(ctx, 2, makers))
        # Every maker with an accepted Remote ID declaration, in or out of the window.
        self.assertIn("avol", makers)
        self.assertIn("manna drone delivery", makers)
        self.assertIn(faa_uas.make_key("Skydio, Inc."), makers)

    def test_waiver_holder_named_with_one_more_word_than_the_maker(self):
        # The fixture's waiver is issued to "Percepto Robotics Inc."; the live
        # declaration list names the maker "Percepto".
        ctx = Context(today=TODAY, lookback_days=120, limit=10)
        page = lambda url, params=None, **kw: (  # noqa: E731
            waiver_html() if params.get("page") == 0 else "", {"date": "Fri, 02 Oct 2026 00:00:00 GMT"})
        with mock.patch.object(faa_uas.http, "get", page), mock.patch.object(faa_uas.time, "sleep", lambda s: None):
            plain = {s.entity.name: s for s in faa_uas._collect_waivers(ctx, 10, set())}
            listed = {s.entity.name: s for s in faa_uas._collect_waivers(ctx, 10, {"percepto"})}
        name = "Percepto Robotics Inc."
        self.assertEqual((plain[name].metrics["remote_id_maker"], plain[name].strength), (0, 0.61))
        self.assertEqual((listed[name].metrics["remote_id_maker"], listed[name].strength), (1, 0.66))
        self.assertEqual(listed["DroneDeploy"].metrics["remote_id_maker"], 0)

    def test_first_claim_when_the_whole_list_was_read(self):
        signals, _, _ = self.run_collect(limit=40, total_items=50)
        manna = next(s for s in signals if s.entity.name == "Manna Drone Delivery")
        self.assertEqual(manna.title, "FAA accepted a Remote ID declaration for aircraft model 3.8.3, its first on the FAA list")
        self.assertGreaterEqual(manna.strength, 0.85)

    def test_one_feed_failing_keeps_the_other(self):
        def boom(*a, **kw):
            raise OSError("network down")

        ctx = Context(today=TODAY, lookback_days=120, limit=10)
        with mock.patch.object(faa_uas.http, "get_json", boom), \
                mock.patch.object(faa_uas.http, "get", lambda url, params=None, **kw: (
                    waiver_html() if params.get("page") == 0 else "", {"date": "Fri, 02 Oct 2026 00:00:00 GMT"})), \
                mock.patch.object(faa_uas.time, "sleep", lambda s: None):
            signals = list(faa_uas.collect(ctx))
        self.assertEqual({s.kind for s in signals}, {"faa_part107_waiver"})
        self.assertTrue(ctx.warnings)


if __name__ == "__main__":
    unittest.main()
