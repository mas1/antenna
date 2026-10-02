"""Offline tests for the energy_grants collector: parsing against saved fixtures."""

from __future__ import annotations

import copy
import json
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from antenna.collectors import energy_grants as eg
from antenna.collectors.base import Context
from antenna.thesis import classify

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "capital"
NSF = json.loads((FIXTURES / "nsf_awards_sbir_phase1_thesis.json").read_text())
ARPAE = json.loads((FIXTURES / "arpae_projects_private_company.json").read_text())

TODAY = date(2026, 10, 1)
SINCE = date(2026, 6, 3)


def nsf_record(award_id: str) -> dict:
    return next(r for r in NSF["response"]["award"] if r["id"] == award_id)


class Helpers(unittest.TestCase):
    def test_money(self):
        self.assertEqual(eg._money(304985), "$305K")
        self.assertEqual(eg._money(303518), "$304K")
        self.assertEqual(eg._money(1554959), "$1.55M")
        self.assertEqual(eg._money(2200000), "$2.2M")
        self.assertEqual(eg._money(2000000), "$2M")
        self.assertEqual(eg._money(999873), "$1M")

    def test_amount_handles_every_format_the_feeds_use(self):
        self.assertEqual(eg._amount("2,000,000"), 2000000)
        self.assertEqual(eg._amount("$3,949,109"), 3949109)
        self.assertEqual(eg._amount("$4,097,637 "), 4097637)
        self.assertEqual(eg._amount("304985"), 304985)
        self.assertIsNone(eg._amount(None))
        self.assertIsNone(eg._amount(""))
        self.assertIsNone(eg._amount("TBD"))
        self.assertIsNone(eg._amount("0"))

    def test_sentence_case_keeps_acronyms_and_formulas(self):
        self.assertEqual(
            eg._sentence_case("AI-Enhanced Robotic Continuum Robotic Instruments for use in Endoscopic Surgery"),
            "AI-enhanced robotic continuum robotic instruments for use in endoscopic surgery")
        self.assertEqual(eg._sentence_case("SnO2 Layers for Si-based MXene 3D Cells"),
                         "SnO2 layers for Si-based MXene 3D cells")
        self.assertEqual(eg._sentence_case("A Novel UAV-Based Method"), "a novel UAV-based method")

    def test_sentence_case_lowers_a_shouted_title(self):
        # ARPA-E carries a few project titles typed entirely in capitals.
        self.assertEqual(
            eg._sentence_case("DEVELOPMENT AND DEMONSTRATION OF A HIGHLY SELECTIVE REE RECOVERY SYSTEM USING NOVEL ION IMPRINTED MEDIA"),
            "development and demonstration of a highly selective REE recovery system using novel ion imprinted media")
        # A short all-caps token inside a normal title is still an acronym.
        self.assertEqual(eg._sentence_case("UAV Radio"), "UAV radio")

    def test_article(self):
        self.assertEqual(eg._article("$2.2M ROCKS award"), "a")
        self.assertEqual(eg._article("$8.5M OPEN award"), "an")
        self.assertEqual(eg._article("$11M award"), "an")
        self.assertEqual(eg._article("$18M award"), "an")
        self.assertEqual(eg._article("$110K award"), "a")
        self.assertEqual(eg._article("$1.1M award"), "a")
        self.assertEqual(eg._article("ARPA-E ROCKS project"), "an")
        self.assertEqual(eg._article("OPEN award"), "an")
        self.assertEqual(eg._article("ROCKS award"), "a")
        self.assertEqual(eg._article("award"), "an")

    def test_shorten_cuts_at_a_break_and_never_overruns(self):
        t = "fast non-destructive inspection of confined-space industrial assets with a novel vine robot"
        self.assertEqual(eg._shorten(t, 77), "fast non-destructive inspection of confined-space industrial assets")
        self.assertEqual(eg._shorten("short phrase.", 77), "short phrase")
        t2 = "AI-enhanced robotic continuum robotic instruments for use in endoscopic surgery"
        self.assertEqual(eg._shorten(t2, 77), "AI-enhanced robotic continuum robotic instruments")
        for budget in (30, 50, 70):
            self.assertLessEqual(len(eg._shorten(t, budget)), budget)

    def test_display_name(self):
        self.assertEqual(eg.display_name("TRELLIS ROBOTICS INC"), "Trellis Robotics Inc")
        self.assertEqual(eg.display_name("AI SCOPE INC"), "AI Scope Inc")
        self.assertEqual(eg.display_name("RADIATION DETECTION SOLUTIONS LLC"), "Radiation Detection Solutions LLC")
        self.assertEqual(eg.display_name("HEAT2POWER INC."), "Heat2Power Inc.")
        self.assertEqual(eg.display_name("CNC CONCRETE HOMES LLC"), "CNC Concrete Homes LLC")
        # Mixed case from the source is never touched.
        self.assertEqual(eg.display_name("Takachar Limited"), "Takachar Limited")
        # Inner capitals come from the award's own text, never from a guess.
        self.assertEqual(eg.display_name("MXENE INC", "Industrial-Grade MXene Production"), "MXene Inc")
        self.assertEqual(eg.display_name("MXENE INC"), "Mxene Inc")
        self.assertEqual(eg.display_name("TRELLIS ROBOTICS INC", "soft robotics"), "Trellis Robotics Inc")

    def test_company_domain_from_email(self):
        f = eg.company_domain_from_email
        self.assertEqual(f("TRELLIS ROBOTICS INC", "elvy@trellisrobotics.com"), "trellisrobotics.com")
        self.assertEqual(f("IZOTE BIOSCIENCES INC.", "victor@izote.bio"), "izote.bio")
        self.assertIsNone(f("AI SCOPE INC", "zhangjoshua360@gmail.com"))
        self.assertIsNone(f("SOME STARTUP INC", "pi@stanford.edu"))
        self.assertIsNone(f("AK INNOVATIONS LLC", "someone@philowave.tech"))
        self.assertIsNone(f("ACME INC", None))

    def test_arpae_dates(self):
        # 'Jun 18 2026', no comma: read by base.parse_date.
        def dates(**fields):
            rec = copy.deepcopy(ARPAE["data"][2])
            rec["attributes"]["fields"].update(fields)
            p = eg.parse_arpae_project(rec)
            return p["released"], p["term_start"], p["term_end"]

        self.assertEqual(dates(release_date="Jun 18 2026", term_start="Jul 01 2026", term_end="June 30 2029"),
                         (date(2026, 6, 18), date(2026, 7, 1), date(2029, 6, 30)))
        self.assertEqual(dates(release_date="Jun  8 2026", term_start=None, term_end="soon"),
                         (date(2026, 6, 8), None, None))
        # Not text: no date, and the record still parses.
        self.assertEqual(dates(term_start=20260701, term_end={"value": "Jul 01 2026"})[1:], (None, None))


class NsfParsing(unittest.TestCase):
    def test_fixture_parses_completely(self):
        awards = [eg.parse_nsf_award(r) for r in NSF["response"]["award"]]
        self.assertEqual(len(awards), 12)
        self.assertTrue(all(a is not None for a in awards))

    def test_trellis_fields(self):
        a = eg.parse_nsf_award(nsf_record("2538085"))
        self.assertEqual(a["raw_name"], "TRELLIS ROBOTICS INC")
        self.assertEqual(a["name"], "Trellis Robotics Inc")
        self.assertEqual(a["program"], "STTR Phase I")
        self.assertEqual(a["date"], date(2026, 8, 11))
        self.assertEqual(a["total"], 304985)
        self.assertEqual(a["obligated"], 304985)
        self.assertEqual(a["pi"], "Elvymond Yao")
        self.assertEqual(a["location"], "Mountain View, CA")
        self.assertEqual(a["pi_email_domain"], "trellisrobotics.com")
        self.assertTrue(a["topic"].startswith("Fast non-destructive inspection"))
        self.assertNotIn("statutory mission", a["abstract"])
        self.assertNotIn("<", a["abstract"])
        self.assertEqual(a["nsf_title"], nsf_record("2538085")["title"])
        self.assertEqual(a["area"], "Instr Rsrch,Metro&Std NanTech")

    def test_abstract_keeps_the_program_name_and_drops_the_closing_sentence(self):
        # The program's name is not a thesis term, so the abstract is stored as
        # NSF wrote it. If the name ever earned thesis fit again, every award
        # would carry it and this would fail.
        for text in (
            "this Small Business Innovation Research (SBIR) Phase I project is",
            "this Small Business Technology Transfer (STTR) Phase I project is",
            "this Small Business Technology Transfer Research (STTR) Phase I project is",
            "this Small Business Innovation Research Phase I (Fast-Track) project",
            "this SBIR Phase I project",
        ):
            self.assertEqual(eg.clean_abstract(text), text)
            self.assertEqual(classify(text)["terms"], [])
        self.assertEqual(
            eg.clean_abstract("<p>A vine&nbsp;robot.</p> This award reflects NSF's statutory mission and has "
                              "been deemed worthy of support."),
            "A vine robot.")
        for rec in NSF["response"]["award"]:
            self.assertNotIn("statutory mission", eg.clean_abstract(rec.get("abstractText")))

    def test_phase_two_and_unrelated_awards_are_rejected(self):
        rec = copy.deepcopy(nsf_record("2538085"))
        rec["title"] = "SBIR Phase II: Scalable Manufacturing Technology for Windows"
        self.assertIsNone(eg.parse_nsf_award(rec))
        rec["title"] = "Collaborative Research: Soft Robots"
        self.assertIsNone(eg.parse_nsf_award(rec))
        rec = copy.deepcopy(nsf_record("2538085"))
        rec["date"] = ""
        self.assertIsNone(eg.parse_nsf_award(rec))

    def test_odd_field_shapes_do_not_invent_people(self):
        rec = copy.deepcopy(nsf_record("2538085"))
        rec["coPDPI"] = "Allison M Okamura aokamura@stanford.edu"  # a bare string, not a list
        rec["piEmail"] = None
        rec["estimatedTotalAmt"] = "n/a"
        a = eg.parse_nsf_award(rec)
        self.assertEqual(a["co_pis"], [{"name": "Allison M Okamura", "edu": "stanford.edu"}])
        self.assertIsNone(a["pi_email_domain"])
        self.assertIsNone(a["total"])
        s = eg.nsf_signal(a, None)
        self.assertEqual(s.value, 304985.0)  # falls back to the obligated amount
        rec["fundsObligatedAmt"] = None
        s = eg.nsf_signal(eg.parse_nsf_award(rec), None)
        self.assertIsNone(s.value)
        self.assertTrue(s.title.startswith("Won NSF Phase I award for "), s.title)
        self.assertNotIn("amount_usd", s.metrics)

    def test_thesis_gate(self):
        on = {r["id"]: eg.nsf_on_thesis(a["topic"], a["abstract"])
              for r in NSF["response"]["award"] for a in [eg.parse_nsf_award(r)]}
        self.assertTrue(on["2538085"])   # vine-robot inspection
        self.assertTrue(on["2537982"])   # robotic skill learning
        self.assertTrue(on["2554320"])   # semiconductor packaging
        self.assertFalse(on["2537614"])  # lung-cancer "bio-chip": one stray keyword
        # Flow-battery sensors. "Battery" and "batteries" are one term, so the
        # title holds a single ambiguous one and the abstract (battery,
        # electrolyte, energy, energy storage) stops short of the bar an
        # abstract has to clear by itself. Without NSF's label the award is
        # out; the collector always passes the label, and with it the award
        # is in (test_nsf_program_label_seconds_a_lone_title_term).
        flow = eg.parse_nsf_award(nsf_record("2604898"))
        self.assertEqual(classify(flow["topic"])["terms"], ["battery"])
        self.assertEqual(classify("a battery among batteries")["terms"], ["battery"])
        self.assertLess(classify(eg.nsf_text(flow["topic"], flow["abstract"]))["fit"], eg.NSF_ABSTRACT_FIT)
        self.assertFalse(on["2604898"])

    def test_thesis_gate_uses_nsf_program_area(self):
        def gate(award_id):
            a = eg.parse_nsf_award(nsf_record(award_id))
            return eg.nsf_on_thesis(a["topic"], a["abstract"], a["area"])

        # Filed by NSF under a life-science program: the abstract says
        # "manufacturing", "industrial", "semiconductor", the company is not one.
        self.assertFalse(gate("2605185"))  # fermentation platform, "Synthetic biology"
        self.assertFalse(gate("2537899"))  # radiotherapy dosimeter, "BIOMEDICAL ENGINEERING"
        # A robot in the title survives a biomedical program.
        self.assertTrue(gate("2527887"))   # continuum robotic instruments for endoscopy
        # The same awards pass when NSF's label is not consulted, so it is the label doing the work.
        a = eg.parse_nsf_award(nsf_record("2605185"))
        self.assertTrue(eg.nsf_on_thesis(a["topic"], a["abstract"]))
        # Other programs are untouched.
        for award_id in ("2538085", "2537982", "2554320", "2604898", "2537894", "2538045"):
            self.assertTrue(gate(award_id), award_id)

    def test_nsf_program_label_seconds_a_lone_title_term(self):
        # The classifier caps a lone ambiguous term under the gate. NSF's own
        # label for the award is the second term, said in plain words.
        solar = "SnO2 Layers for Scalable Fullerene-Free Perovskite Solar Cells"
        abstract = "liquid-processable tin oxide nanoparticles compatible with coating methods"
        self.assertLess(classify(solar)["fit"], eg.NSF_TITLE_FIT)
        self.assertTrue(eg.nsf_on_thesis(solar, abstract, "Other Energy Research, EXP PROG TO STIM COMP RES"))
        self.assertFalse(eg.nsf_on_thesis(solar, abstract))
        self.assertFalse(eg.nsf_on_thesis(solar, abstract, "NETWORKING RESEARCH"))
        # The same word twice is still one term.
        marine = "Empowering Oceanic Intelligence with Unlimited Marine Energy"
        self.assertFalse(eg.nsf_on_thesis(marine, abstract, "Other Energy Research"))
        # A label never carries a title that has no thesis term of its own ...
        lasers = "AI-Accelerated Simulation Platform for High-Power Fiber Lasers"
        self.assertGreaterEqual(classify(eg.nsf_text(lasers, "", "Optics and Photonics"))["fit"], eg.NSF_TITLE_FIT)
        self.assertFalse(eg.nsf_on_thesis(lasers, "simulation of fiber lasers for defense and manufacturing",
                                          "Optics and Photonics"))
        # ... and never rescues an award NSF also files under a life-science program.
        bio = "Solar-Driven Bioreactor for Protein Production"
        self.assertTrue(eg.nsf_on_thesis(bio, abstract, "Other Energy Research"))
        self.assertFalse(eg.nsf_on_thesis(bio, abstract, "Other Energy Research, Biotechnology"))

        def gate(award_id, with_label=True):
            a = eg.parse_nsf_award(nsf_record(award_id))
            return eg.nsf_on_thesis(a["topic"], a["abstract"], a["area"] if with_label else None)

        # Multi-polymer 3D printing, filed under "Advanced Manufacturing".
        self.assertTrue(gate("2537440"))
        self.assertFalse(gate("2537440", with_label=False))
        # Flow-battery sensors, filed under "Energy Storage or Transmission".
        self.assertTrue(gate("2604898"))
        self.assertFalse(gate("2604898", with_label=False))
        # "Reactor" under an environment program, "chip" under a diagnostics one: still out.
        self.assertFalse(gate("2604364"))
        self.assertFalse(gate("2537614"))

    def test_thesis_gate_rejects_software_described_in_thesis_words(self):
        abstract = ("accelerates discovery of advanced materials for semiconductors, energy storage, "
                    "advanced manufacturing, defense systems, national security and autonomous design of "
                    "semiconductor chips for industrial innovation")
        topic = "AI-Driven Cloud Application Programming Interface for Quantum-Accurate Materials Simulation"
        self.assertGreaterEqual(classify(f"{topic} \n {abstract}")["fit"], eg.NSF_ABSTRACT_FIT)
        self.assertFalse(eg.nsf_on_thesis(topic, abstract, "Materials Engineering"))
        # The same abstract under a hardware title passes ...
        self.assertTrue(eg.nsf_on_thesis("Purification of Electrochemical Graphite", abstract, "Advanced Manufacturing"))
        # ... and software for a robot keeps its place because the title carries a strong term.
        self.assertTrue(eg.nsf_on_thesis("Cloud Software for Robot Fleet Teleoperation", abstract, "ROBOTICS"))
        # One ambiguous word in a software title is not enough, whatever the label says.
        self.assertFalse(eg.nsf_on_thesis("Cloud Software for Battery Fleet Analytics", abstract,
                                          "Energy Storage or Transmission"))

    def test_signal_for_phase_one(self):
        a = eg.parse_nsf_award(nsf_record("2538085"))
        s = eg.nsf_signal(a, prior_awards=0)
        s.validate()
        self.assertEqual((s.source, s.family, s.kind), ("energy_grants", "capital", "nsf_sttr_phase1"))
        self.assertEqual(s.entity.name, "Trellis Robotics Inc")
        self.assertEqual(s.entity.aliases, ["TRELLIS ROBOTICS INC"])
        self.assertIsNone(s.entity.domain)  # NSF publishes no company website
        self.assertEqual(s.occurred_at, "2026-08-11")
        self.assertEqual(s.url, "https://www.nsf.gov/awardsearch/show-award/?AWD_ID=2538085")
        self.assertEqual(s.value, 304985.0)
        self.assertEqual(s.unit, "USD")
        self.assertEqual(s.metrics["amount_usd"], 304985)
        self.assertEqual(s.metrics["program"], "STTR Phase I")
        self.assertEqual(s.metrics["nsf_program_area"], "Instr Rsrch,Metro&Std NanTech")
        self.assertEqual(s.metrics["nsf_prior_awards"], 0)
        self.assertEqual(s.metrics["pi_email_domain"], "trellisrobotics.com")
        self.assertEqual(s.metrics["uei"], nsf_record("2538085")["ueiNumber"])
        # The key sbir_awards shares for the same award, so the scorer counts it once.
        self.assertEqual(s.metrics["award_key"], "nsf:2538085")
        self.assertEqual(
            s.title,
            "Won $305K NSF Phase I award for fast non-destructive inspection of confined-space industrial assets")
        # The text is the title, NSF's label in plain words, then the abstract.
        self.assertTrue(s.text.startswith(
            "Fast non-destructive inspection of confined-space industrial assets with a novel vine robot. "
            "NSF program area: Instr Rsrch,Metro&Std NanTech. The broader"), s.text[:200])
        self.assertEqual(s.people[0].name, "Elvymond Yao")
        self.assertEqual(s.people[0].role, "Principal Investigator")
        self.assertEqual(s.people[0].affiliations, ["Trellis Robotics Inc"])
        self.assertEqual(s.strength, 0.45)
        self.assertGreaterEqual(classify(s.text)["fit"], 0.3)
        # No email address is stored anywhere on the signal.
        self.assertNotIn("@", json.dumps(s.to_row()))

    def test_fast_track_says_up_to_when_partly_obligated(self):
        a = eg.parse_nsf_award(nsf_record("2537982"))
        self.assertEqual(a["program"], "SBIR Fast-Track")
        self.assertEqual((a["total"], a["obligated"]), (1550000, 400000))
        s = eg.nsf_signal(a, prior_awards=0)
        self.assertEqual(s.title, "Won up to $1.55M NSF Fast-Track award for scalable robotic skill learning")
        self.assertEqual(s.value, 1550000.0)
        self.assertEqual(s.metrics["funds_obligated_usd"], 400000)
        self.assertEqual(s.strength, 0.6)
        self.assertEqual(s.entity.name, "AI Scope Inc")
        self.assertEqual(s.kind, "nsf_sbir_fast_track")

    def test_award_key_is_the_seven_digit_nsf_id(self):
        for rec in NSF["response"]["award"]:
            s = eg.nsf_signal(eg.parse_nsf_award(rec), None)
            self.assertEqual(s.metrics["award_key"], "nsf:" + rec["id"])
            self.assertRegex(s.metrics["award_key"], r"^nsf:\d{7}$")
        # An id in any other shape would not match the other collector's key: no key.
        odd = eg.parse_nsf_award(dict(nsf_record("2538085"), id="A-17"))
        self.assertNotIn("award_key", eg.nsf_signal(odd, None).metrics)

    def test_text_is_what_the_gate_classified(self):
        a = eg.parse_nsf_award(nsf_record("2604898"))  # flow-battery sensors
        s = eg.nsf_signal(a, None)
        self.assertEqual(s.text, eg.nsf_text(a["topic"], a["abstract"], a["area"]))
        self.assertIn(". NSF program area: Energy Storage or Transmission. ", s.text)
        self.assertGreaterEqual(classify(s.text)["fit"], 0.3)
        # No label, no sentence about one.
        self.assertNotIn("NSF program area", eg.nsf_signal(dict(a, area=None), None).text)
        self.assertEqual(eg.nsf_text("A robot.", "", None), "A robot")

    def test_kind_names_the_program_the_award_is_under(self):
        kinds = {eg.nsf_signal(eg.parse_nsf_award(r), None).kind for r in NSF["response"]["award"]}
        self.assertEqual(kinds, {"nsf_sbir_phase1", "nsf_sttr_phase1", "nsf_sbir_fast_track"})

    def test_title_rules_hold_for_every_fixture_award(self):
        for rec in NSF["response"]["award"]:
            s = eg.nsf_signal(eg.parse_nsf_award(rec), prior_awards=None)
            s.validate()
            self.assertLess(len(s.title), 110, s.title)
            self.assertFalse(s.title.endswith("."), s.title)
            self.assertTrue(s.title.startswith("Won "), s.title)
            self.assertNotEqual(s.title, s.title.upper())
            self.assertRegex(s.title, r"\$\d")  # the number is in it
            self.assertNotIn("SBIR", s.title)
            self.assertNotIn("STTR", s.title)

    def test_strength_scale(self):
        self.assertEqual(eg.nsf_strength("SBIR Phase I", None), 0.38)
        self.assertEqual(eg.nsf_strength("SBIR Phase I", 0), 0.45)
        self.assertEqual(eg.nsf_strength("SBIR Phase I", 1), 0.38)
        self.assertEqual(eg.nsf_strength("STTR Phase I", 5), 0.28)
        self.assertEqual(eg.nsf_strength("SBIR Fast-Track", 0), 0.6)

    def test_co_pi_keeps_university_domain_but_no_address(self):
        rec = copy.deepcopy(nsf_record("2538085"))
        rec["coPDPI"] = ["Allison M Okamura aokamura@stanford.edu"]
        s = eg.nsf_signal(eg.parse_nsf_award(rec), prior_awards=0)
        co = s.people[1]
        self.assertEqual((co.name, co.role, co.affiliations),
                         ("Allison M Okamura", "Co-Principal Investigator on the NSF award", ["stanford.edu"]))
        # The co-PI sits at the partner institution: never listed under the company.
        self.assertNotIn("Trellis Robotics Inc", co.affiliations)
        self.assertNotIn("@", json.dumps(s.to_row()))


SBIR_GOV_HIT = """
<table id="search-results-hits"><tbody><tr><td>
<h4 class="margin-top-2"><a href="/awards/{id}">{title}</a></h4>
<p><i><span><b>SBC:</b> {company} </span></i></p>
</td></tr></tbody></table>
"""


class NsfLookups(unittest.TestCase):
    def prior(self, award_id, rows):
        a = eg.parse_nsf_award(nsf_record(award_id))
        payload = {"response": {"metadata": {"totalCount": len(rows)}, "award": rows}}
        with mock.patch.object(eg.http, "get_json", return_value=payload):
            return eg._nsf_prior_awards(a)

    def test_prior_awards_counts_earlier_awards_to_the_same_uei(self):
        this = nsf_record("2537899")
        older = dict(this, id="1914013", date="07/01/2019")
        self.assertEqual(self.prior("2537899", [this]), 0)
        self.assertEqual(self.prior("2537899", [this, older]), 1)
        # An award dated after this one is not "prior".
        self.assertEqual(self.prior("2537899", [this, dict(this, id="2700001", date="09/30/2026")]), 0)

    def test_prior_awards_is_unknown_when_the_answer_is_not_about_this_uei(self):
        this = nsf_record("2537899")
        other = nsf_record("2538085")  # a different company's award
        self.assertIsNone(self.prior("2537899", [this, other]))   # filter ignored
        self.assertIsNone(self.prior("2537899", []))              # the award itself is missing
        self.assertIsNone(self.prior("2537899", [other]))
        a = eg.parse_nsf_award(nsf_record("2537899"))
        with mock.patch.object(eg.http, "get_json", return_value=[{"error": "x"}]):
            self.assertIsNone(eg._nsf_prior_awards(a))
        with mock.patch.object(eg.http, "get_json") as never:
            self.assertIsNone(eg._nsf_prior_awards(dict(a, uei=None)))
            never.assert_not_called()

    def test_sbir_gov_listing_is_matched_on_the_award_title(self):
        rec = nsf_record("2554320")
        page = SBIR_GOV_HIT.format(id="220814", title=rec["title"].replace("Boron", "Boron  "), company="HEXAspec Inc")
        self.assertTrue(eg.sbir_gov_lists(page, rec["title"]))
        self.assertFalse(eg.sbir_gov_lists(page, nsf_record("2538085")["title"]))
        self.assertFalse(eg.sbir_gov_lists("<html>No results</html>", rec["title"]))
        self.assertFalse(eg.sbir_gov_lists("", rec["title"]))
        self.assertFalse(eg.sbir_gov_lists(page, ""))

    def test_award_is_left_to_sbir_awards_only_when_that_collector_would_report_it(self):
        listed = lambda rec: SBIR_GOV_HIT.format(id="1", title=rec["title"], company=rec["awardeeName"])
        hexaspec = eg.parse_nsf_award(nsf_record("2554320"))
        with mock.patch.object(eg.http, "get", return_value=listed(nsf_record("2554320"))) as get:
            self.assertTrue(eg.covered_by_sbir_awards(hexaspec))
            (url,), kw = get.call_args
            # Byte-for-byte the request sbir_awards makes, so both share one cached page.
            self.assertEqual((url, kw["params"], kw["ttl"]),
                             ("https://www.sbir.gov/awards", {"keywords": "2554320"}, 7 * 86400))
        with mock.patch.object(eg.http, "get", return_value="<html>No results</html>"):
            self.assertFalse(eg.covered_by_sbir_awards(hexaspec))
        # sbir_awards judges the title alone. A title that is not on thesis is
        # never reported there, so it stays here and SBIR.gov is not even asked.
        off_title = dict(hexaspec, topic="Purification of Electrochemical Graphite")
        with mock.patch.object(eg.http, "get") as get:
            self.assertFalse(eg.covered_by_sbir_awards(off_title))
            get.assert_not_called()
        # A title with one ambiguous term ("batteries") is under the bar as
        # well, so it is not deferred and SBIR.gov is not asked. sbir_awards
        # may still keep such a title when the SBIR.gov topic code backs the
        # keyword; this collector cannot see that code, and the shared
        # award_key then has the scorer count the award once.
        flow = eg.parse_nsf_award(nsf_record("2604898"))
        with mock.patch.object(eg.http, "get") as get:
            self.assertFalse(eg.covered_by_sbir_awards(flow))
            get.assert_not_called()


class ArpaeParsing(unittest.TestCase):
    def setUp(self):
        self.projects = [eg.parse_arpae_project(r) for r in ARPAE["data"]]

    def test_fixture_parses(self):
        self.assertEqual([p["org"] for p in self.projects],
                         ["Fieldstone Bio, Inc.", "Deep Blue Geophysics, LLC", "Wetstone Exploration"])
        self.assertEqual([p["amount"] for p in self.projects], [2000000, 2200000, 3500000])
        self.assertTrue(all(p["released"] == date(2026, 6, 18) for p in self.projects))
        self.assertTrue(all(p["status"] == "Selected" for p in self.projects))
        self.assertEqual([p["has_detail"] for p in self.projects], [False, False, True])
        self.assertEqual(self.projects[1]["location"], "Los Angeles, CA")
        self.assertEqual(self.projects[1]["programs"][0][0], "ROCKS")
        self.assertIn("seafloor massive sulfide", self.projects[2]["description"])
        self.assertNotIn("<", self.projects[2]["description"])

    def test_url_is_the_page_that_opens_not_the_dead_redirect(self):
        p = self.projects[1]
        self.assertEqual(
            p["url"],
            "https://arpa-e.energy.gov/programs-and-initiatives/search-all-projects/"
            "v6em-uav-based-controlled-source-electromagnetics-rapid-ore-characterization")
        self.assertNotIn("/technologies/projects/", p["url"])

    def test_non_private_or_empty_records_are_rejected(self):
        rec = copy.deepcopy(ARPAE["data"][0])
        rec["attributes"]["fields"]["organization"][0]["fields"]["type"][0]["name"] = "University"
        self.assertIsNone(eg.parse_arpae_project(rec))
        rec["attributes"]["fields"]["organization"] = []
        self.assertIsNone(eg.parse_arpae_project(rec))

    def test_event_window(self):
        p = self.projects[1]
        self.assertEqual(eg.arpae_event(p, SINCE, TODAY), ("selected", date(2026, 6, 18)))
        self.assertIsNone(eg.arpae_event(p, date(2026, 7, 1), TODAY))
        started = dict(p, released=date(2024, 1, 31), term_start=date(2026, 7, 1))
        self.assertEqual(eg.arpae_event(started, SINCE, TODAY), ("started", date(2026, 7, 1)))
        future = dict(p, released=None, term_start=date(2026, 12, 1))
        self.assertIsNone(eg.arpae_event(future, SINCE, TODAY))

    def test_selection_signal(self):
        p = self.projects[1]
        s = eg.arpae_signal(p, "selected", p["released"], org_projects=1)
        s.validate()
        self.assertEqual((s.kind, s.family), ("arpa_e_project", "capital"))
        self.assertEqual(s.entity.name, "Deep Blue Geophysics, LLC")
        self.assertIsNone(s.entity.domain)
        self.assertEqual(s.occurred_at, "2026-06-18")
        self.assertEqual(s.value, 2200000.0)
        self.assertEqual(s.metrics["amount_usd"], 2200000)
        self.assertEqual(s.metrics["program"], "ROCKS")
        self.assertEqual(
            s.title, "Selected by ARPA-E for a $2.2M ROCKS award: UAV-based controlled-source electromagnetics")
        self.assertLess(len(s.title), 110)
        self.assertEqual(s.people, [])  # the feed names no project contact yet
        self.assertGreaterEqual(classify(s.text)["fit"], 0.3)
        self.assertEqual(s.strength, 0.76)

    def test_started_signal_carries_contact_and_website(self):
        rec = copy.deepcopy(ARPAE["data"][2])
        f = rec["attributes"]["fields"]
        f.update(status="Active", term_start="Jun 08 2026", term_end="Jun 07 2029", release_date=None,
                 contact="Dr. Margaret Lumley", website=[{"uri": "https://www.example-water.com/"}])
        rec["attributes"]["date"] = None
        p = eg.parse_arpae_project(rec)
        self.assertEqual(p["contact"], "Margaret Lumley")
        self.assertEqual(p["domain"], "example-water.com")
        event = eg.arpae_event(p, SINCE, TODAY)
        self.assertEqual(event, ("started", date(2026, 6, 8)))
        s = eg.arpae_signal(p, *event, org_projects=1)
        s.validate()
        self.assertTrue(s.title.startswith("Began a $3.5M ARPA-E ROCKS project: "), s.title)
        self.assertLess(len(s.title), 110)
        self.assertEqual(s.people[0].name, "Margaret Lumley")
        self.assertEqual(s.people[0].affiliations, ["Wetstone Exploration"])
        self.assertEqual(s.metrics["term_start"], "2026-06-08")
        # A term start with no selection date in the window is an earlier
        # selection becoming real: it scores below a fresh selection.
        self.assertEqual(s.strength, eg.arpae_strength("started", 3_500_000, 1, announced_earlier=True))
        fresh = eg.arpae_signal(p, *event, org_projects=1, selected_in_window=True)
        self.assertEqual(fresh.strength, eg.arpae_strength("started", 3_500_000, 1))
        self.assertLess(s.strength, fresh.strength)
        self.assertLess(s.strength, eg.arpae_strength("selected", 3_500_000, 1))

    def test_title_grammar_without_amount_or_with_an_eight(self):
        p = dict(self.projects[1], amount=None)
        self.assertTrue(eg.arpae_signal(p, "selected", p["released"], 1).title.startswith(
            "Selected by ARPA-E for a ROCKS award: "))
        self.assertTrue(eg.arpae_signal(p, "started", p["released"], 1).title.startswith(
            "Began an ARPA-E ROCKS project: "))
        p = dict(self.projects[1], amount=8_500_000, programs=[])
        self.assertTrue(eg.arpae_signal(p, "selected", p["released"], 1).title.startswith(
            "Selected by ARPA-E for an $8.5M award: "))
        self.assertTrue(eg.arpae_signal(p, "started", p["released"], 1).title.startswith(
            "Began an $8.5M ARPA-E project: "))

    def test_website_must_be_a_company_site(self):
        def domain(uri):
            rec = copy.deepcopy(ARPAE["data"][2])
            rec["attributes"]["fields"]["website"] = [{"uri": uri}]
            return eg.parse_arpae_project(rec)["domain"]

        self.assertEqual(domain("https://www.rocawater.com/"), "rocawater.com")
        self.assertIsNone(domain("https://arpa-e.energy.gov/some-project"))
        self.assertIsNone(domain("https://engineering.stanford.edu/lab"))
        self.assertIsNone(domain("https://www.nrl.navy.mil/"))
        self.assertIsNone(domain("https://www.linkedin.com/company/acme"))
        rec = copy.deepcopy(ARPAE["data"][2])
        rec["attributes"]["fields"]["website"] = None
        self.assertIsNone(eg.parse_arpae_project(rec)["domain"])

    def test_names_are_unescaped_and_trimmed(self):
        rec = copy.deepcopy(ARPAE["data"][1])
        rec["attributes"]["fields"]["organization"][0]["fields"]["title"] = " Smith &amp; Jones  Energy, Inc. "
        self.assertEqual(eg.parse_arpae_project(rec)["org"], "Smith & Jones Energy, Inc.")

    def test_detail_must_be_the_record_that_was_asked_for(self):
        full = ARPAE["data"][2]
        nid = str(full["attributes"]["drupal_internal__nid"])
        with mock.patch.object(eg.http, "get_json", return_value={"data": [full]}):
            self.assertEqual(eg._arpae_detail(nid)["nid"], nid)
            self.assertIsNone(eg._arpae_detail("999999"))  # filter ignored: some other node came back
        with mock.patch.object(eg.http, "get_json", return_value={"data": []}):
            self.assertIsNone(eg._arpae_detail(nid))

    def test_index_drops_a_row_the_server_repeats(self):
        page = {"data": ARPAE["data"] + [ARPAE["data"][0]], "meta": {"count": {"count": 4}}}
        with mock.patch.object(eg.http, "get_json", return_value=page):
            projects, complete = eg._arpae_index(Context(today=TODAY))
        self.assertTrue(complete)
        self.assertEqual(len(projects), 3)
        self.assertEqual(len({p["nid"] for p in projects}), 3)

    def test_one_company_spelled_two_ways_is_counted_once(self):
        # Both spellings are in the ARPA-E index. Counted apart, the second
        # project would be scored as a first.
        self.assertEqual(eg.arpae_org_key("AutoGrid Systems, Inc."), eg.arpae_org_key("AutoGrid"))
        self.assertEqual(eg.arpae_org_key("Zap Energy Inc."), eg.arpae_org_key("Zap Energy"))
        self.assertNotEqual(eg.arpae_org_key("Zap Energy"), eg.arpae_org_key("Zeno Power"))

        base = ARPAE["data"][1]

        def project(nid, org, title, release):
            rec = copy.deepcopy(base)
            rec["attributes"]["drupal_internal__nid"] = nid
            rec["attributes"]["title"] = title
            f = rec["attributes"]["fields"]
            f["title"] = title
            f["release_date"] = release
            f["organization"][0]["fields"]["title"] = org
            return rec

        page = {"data": [project(9001, "Example Grid Systems, Inc.", "UAV-Based Inspection of Transmission Lines", "Jun 18 2026"),
                         project(9002, "Example Grid", "Drone Survey of Substations", "Jan 31 2024")],
                "meta": {"count": {"count": 2}}}
        with mock.patch.object(eg.http, "get_json", return_value=page):
            signals = list(eg._collect_arpae(Context(today=TODAY), None))
        self.assertEqual([s.entity.name for s in signals], ["Example Grid Systems, Inc."])
        self.assertEqual(signals[0].metrics["arpa_e_projects_by_org"], 2)
        self.assertEqual(signals[0].strength, eg.arpae_strength("selected", 2_200_000, 2))

    def test_strength_scale(self):
        first = eg.arpae_strength("selected", 2_200_000, 1)
        repeat = eg.arpae_strength("selected", 2_200_000, 5)
        unknown = eg.arpae_strength("selected", None, None)
        old_news = eg.arpae_strength("started", 1_749_188, 1, announced_earlier=True)
        big = eg.arpae_strength("selected", 20_000_000, 1)
        self.assertEqual(unknown, 0.45)
        self.assertGreater(first, 0.7)
        self.assertLess(repeat, 0.5)
        self.assertLess(old_news, first)
        self.assertGreaterEqual(big, 0.85)
        for v in (first, repeat, unknown, old_news, big):
            self.assertTrue(0.0 <= v <= 1.0)

    def test_established_companies_are_skipped(self):
        self.assertTrue(eg._ESTABLISHED.search("Baker Hughes Energy Transition LLC"))
        self.assertTrue(eg._ESTABLISHED.search("Halliburton Technology Partners LLC"))
        self.assertTrue(eg._ESTABLISHED.search("GE Vernova Advanced Research"))
        for org in ("HP", "Nvidia", "Framatome", "Intel Federal", "3M", "ABB", "Robert Bosch",
                    "Pratt & Whitney Rocketdyne (PWR)", "Hewlett Packard Labs", "Rio Tinto Services"):
            self.assertTrue(eg._ESTABLISHED.search(org), org)
        for org in ("OnTo Technology", "Wetstone Exploration", "Phoenix Tailings", "Zap Energy",
                    "Intellicharge", "Lindell Energy", "Creek Power", "Abbot Fusion"):
            self.assertIsNone(eg._ESTABLISHED.search(org), org)
        for org in ("Deep Blue Geophysics, LLC", "Fieldstone Bio, Inc.", "Roca Water, Inc.", "Iontra"):
            self.assertIsNone(eg._ESTABLISHED.search(org))

    def test_topic_drops_codename(self):
        self.assertEqual(
            eg._arpae_topic("V6EM: UAV-Based Controlled-Source Electromagnetics for Rapid Ore Characterization"),
            "UAV-Based Controlled-Source Electromagnetics for Rapid Ore Characterization")
        self.assertEqual(eg._arpae_topic("DEEP - Deep-sea Exploration Efficiency Project"),
                         "Deep-sea Exploration Efficiency Project")
        self.assertEqual(eg._arpae_topic("Rapid In Situ Core Characterization"),
                         "Rapid In Situ Core Characterization")

    def recover_record(self, nid, org, title, description, term_start, award, website):
        """A RECOVER project as the node endpoint returned it on 2026-10-01
        (title, description, program and technology areas in ARPA-E's words),
        laid over the fixture's full record."""
        rec = copy.deepcopy(ARPAE["data"][2])
        at = rec["attributes"]
        at.update(drupal_internal__nid=nid, title=title, date=None, urls=[f"/node/{nid}"])
        f = at["fields"]
        f.update(title=title, project_description=description, status="Active", release_date=None,
                 term_start=term_start, award=award, website=[{"uri": website}],
                 related_technologies=[{"name": "Chemicals & Fuels"}, {"name": "Resources"}])
        f["related_programs"][0]["fields"].update(
            title="RECOVER",
            acronym="Realize Energy-rich Compound Opportunities Valorizing Extraction from Refuse waters")
        f["organization"][0]["fields"]["title"] = org
        return rec

    def test_critical_metals_are_on_thesis_and_battery_inspired_is_not_a_battery(self):
        chemfinity = self.recover_record(
            "5443", "ChemFinity Technologies",
            "Low-Cost, Selective Iridium and Platinum Recovery from Mining and Produced Wastewaters "
            "via Porous Polymer Sorbent Processes",
            "ChemFinity Technologies will develop porous polymer sorbent materials for the selective and "
            "cost-effective recovery of critical metals, including iridium and platinum, from mining and "
            "produced wastewaters. This innovative technology aims to fulfill a substantial portion of U.S. "
            "demand for these metals at significantly reduced costs and energy consumption compared to "
            "conventional methods.",
            "Aug 01 2026", "3,000,000", "https://chemfinitytech.com/")
        roca = self.recover_record(
            "5444", "Roca Water, Inc.",
            "Battery-Inspired, Electrified System for Precision Resource Recovery from Wastewater",
            "<p>Roca Water will engineer a new battery-inspired electrified system designed for the precise "
            "recovery of valuable resources such as ammonium and magnesium from wastewater streams. This "
            "process efficiently transforms pollutants into beneficial products like fertilizer and "
            "magnesium hydroxide. The technology has the potential to substantially decrease U.S. reliance "
            "on imported materials and lower energy consumption relative to existing resource recovery "
            "approaches.</p>",
            "Jun 08 2026", "3,705,637", "https://www.rocawater.com/")

        # Iridium and platinum recovery: "critical metals" is a manufacturing
        # term, seconded by "energy".
        text = eg.arpae_text(eg.parse_arpae_project(chemfinity))
        fit = classify(text)
        self.assertEqual((fit["sector"], fit["terms"]), ("manufacturing", ["critical metal", "energy"]))
        self.assertGreaterEqual(fit["fit"], eg.MIN_FIT)
        # Ammonium and magnesium from wastewater, for fertilizer. The system is
        # "battery-inspired"; it is not a battery, and the word earns nothing.
        text = eg.arpae_text(eg.parse_arpae_project(roca))
        fit = classify(text)
        self.assertEqual(fit["terms"], ["energy"])
        self.assertLess(fit["fit"], eg.MIN_FIT)
        # A project that is about a battery still passes on the same words.
        self.assertGreaterEqual(classify(text.replace("Battery-Inspired, Electrified System",
                                                      "Battery System"))["fit"], eg.MIN_FIT)

        page = {"data": [chemfinity, roca], "meta": {"count": {"count": 2}}}
        ctx = Context(today=TODAY)
        with mock.patch.object(eg.http, "get_json", return_value=page):
            signals = list(eg._collect_arpae(ctx, None))
        self.assertEqual(ctx.warnings, [])
        self.assertEqual([s.entity.name for s in signals], ["ChemFinity Technologies"])
        s = signals[0]
        s.validate()
        self.assertEqual(s.title, "Began a $3M ARPA-E RECOVER project: low-cost, selective iridium and platinum recovery")
        self.assertEqual((s.occurred_at, s.value, s.entity.domain), ("2026-08-01", 3000000.0, "chemfinitytech.com"))
        self.assertEqual(s.metrics["program"], "RECOVER")
        self.assertEqual(s.metrics["technology_areas"], ["Chemicals & Fuels", "Resources"])
        self.assertEqual(s.strength, eg.arpae_strength("started", 3_000_000, 1, announced_earlier=True))


class CollectOffline(unittest.TestCase):
    """collect() end to end with the HTTP layer replaced by the fixtures."""

    def fake_get_json(self, url, params=None, **kw):
        params = params or {}
        self.calls.append((url, dict(params)))
        if url == eg.NSF_API:
            if "ueiNumber" in params:
                recs = [r for r in NSF["response"]["award"] if r.get("ueiNumber") == params["ueiNumber"]]
                return {"response": {"metadata": {"totalCount": len(recs)}, "award": recs}}
            if params.get("keyword") == '"SBIR Phase I"' and params.get("offset") == 0:
                return NSF
            return {"response": {"metadata": {"totalCount": 0}, "award": []}}
        if url == eg.ARPAE_INDEX:
            return ARPAE if params.get("page[offset]") == 0 else {"data": [], "meta": ARPAE["meta"]}
        if url == eg.ARPAE_NODE:
            return {"data": []}
        raise AssertionError(f"unexpected request {url}")

    sbir_gov_listed: tuple = ()   # NSF award ids that SBIR.gov shows
    sbir_gov_down = False

    def fake_get(self, url, params=None, **kw):
        """www.sbir.gov award search, the only plain-text request the collector makes."""
        params = params or {}
        self.calls.append((url, dict(params)))
        if url != eg.SBIR_GOV_SEARCH:
            raise AssertionError(f"unexpected request {url}")
        if self.sbir_gov_down:
            raise OSError("sbir.gov timed out")
        award_id = params["keywords"]
        if award_id in self.sbir_gov_listed:
            rec = nsf_record(award_id)
            return SBIR_GOV_HIT.format(id="220000", title=rec["title"], company=rec["awardeeName"])
        return "<html><p>No results</p></html>"

    def run_collect(self, **ctx_kw):
        self.calls = []
        ctx = Context(today=TODAY, **ctx_kw)
        with mock.patch.object(eg.http, "get_json", side_effect=self.fake_get_json), \
                mock.patch.object(eg.http, "get", side_effect=self.fake_get):
            signals = list(eg.collect(ctx))
        return ctx, signals

    def test_collect(self):
        ctx, signals = self.run_collect()
        self.assertEqual(ctx.warnings, [])
        for s in signals:
            s.validate()
            self.assertLess(len(s.title), 110)
            self.assertGreaterEqual(s.occurred_at, SINCE.isoformat())
            self.assertLessEqual(s.occurred_at, TODAY.isoformat())
        by_kind = {}
        for s in signals:
            by_kind.setdefault(s.kind, []).append(s.entity.name)
        self.assertIn("Trellis Robotics Inc", by_kind["nsf_sttr_phase1"])
        self.assertIn("AI Scope Inc", by_kind["nsf_sbir_fast_track"])
        nsf_names = [s.entity.name for s in signals if s.kind.startswith("nsf_")]
        self.assertEqual(sorted(nsf_names), sorted([
            "AI Scope Inc", "Materium Technologies Inc.", "Hexaspec Inc", "Trellis Robotics Inc",
            "Flowcellutions, Inc", "MXene Inc", "Channel Robotics, Inc.", "Neural Solid, Inc."]))
        for s in signals:
            if s.kind.startswith("nsf_"):
                self.assertEqual(s.metrics["award_key"], "nsf:" + s.metrics["nsf_award_id"])
            else:
                self.assertNotIn("award_key", s.metrics)
        # Off thesis: a lung-cancer chip, a fermentation strain, a radiotherapy dosimeter.
        for off in ("Rumedical Nova Limited Liability Co", "Izote Biosciences Inc.",
                    "Radiation Detection Solutions LLC"):
            self.assertNotIn(off, nsf_names)
        # Two of the three in the fixture clear the thesis gate: the UAV survey
        # and the subsea survey for critical minerals. The third comes without
        # its description here, and its title alone says nothing on thesis.
        self.assertEqual(by_kind["arpa_e_project"], ["Deep Blue Geophysics, LLC", "Wetstone Exploration"])
        # Newest first, and the dateStart filter was sent as MM/DD/YYYY.
        nsf_dates = [s.occurred_at for s in signals if s.kind.startswith("nsf_")]
        self.assertEqual(len(set(s.url for s in signals)), len(signals))  # one signal per award
        self.assertEqual(nsf_dates, sorted(nsf_dates, reverse=True))
        nsf_params = next(p for u, p in self.calls if u == eg.NSF_API and "keyword" in p)
        self.assertEqual(nsf_params["dateStart"], "06/03/2026")

    def test_old_awards_are_skipped(self):
        _, signals = self.run_collect(lookback_days=30)  # since 2026-09-01
        self.assertEqual([s.entity.name for s in signals], ["AI Scope Inc"])

    def test_awards_listed_on_sbir_gov_are_left_to_sbir_awards(self):
        self.sbir_gov_listed = ("2554320", "2604898", "2537894", "2527887", "2537440")
        ctx, signals = self.run_collect()
        self.assertEqual(ctx.warnings, [])
        nsf_names = sorted(s.entity.name for s in signals if s.kind.startswith("nsf_"))
        # Flowcellutions ("batteries") and Neural Solid ("3D printing") are
        # listed on SBIR.gov too, but their titles alone are under the thesis
        # bar, so they are not deferred and stay here.
        self.assertEqual(nsf_names, ["AI Scope Inc", "Flowcellutions, Inc", "Materium Technologies Inc.",
                                     "Neural Solid, Inc.", "Trellis Robotics Inc"])
        asked = {p["keywords"] for u, p in self.calls if u == eg.SBIR_GOV_SEARCH}
        self.assertFalse(asked & {"2604898", "2537440"})
        self.assertEqual([s.entity.name for s in signals if s.kind == "arpa_e_project"],
                         ["Deep Blue Geophysics, LLC", "Wetstone Exploration"])

    def test_sbir_gov_down_reports_the_award_here_and_stops_asking(self):
        self.sbir_gov_down = True
        ctx, signals = self.run_collect()
        self.assertEqual(len([s for s in signals if s.kind.startswith("nsf_")]), 8)
        self.assertEqual(len([w for w in ctx.warnings if "SBIR.gov check failed" in w]), 2)
        self.assertEqual(len([u for u, _ in self.calls if u == eg.SBIR_GOV_SEARCH]), 2)

    def test_nsf_paging_stops_without_a_total(self):
        def no_total(url, params=None, **kw):
            params = params or {}
            if url == eg.NSF_API and "keyword" in params:
                self.calls.append((url, dict(params)))
                first = params["keyword"] == '"SBIR Phase I"' and params["offset"] == 0
                return {"response": {"award": NSF["response"]["award"] if first else []}}
            return self.fake_get_json(url, params, **kw)

        self.calls = []
        ctx = Context(today=TODAY)
        with mock.patch.object(eg.http, "get_json", side_effect=no_total), \
                mock.patch.object(eg.http, "get", side_effect=self.fake_get):
            signals = list(eg.collect(ctx))
        self.assertEqual(len([s for s in signals if s.kind.startswith("nsf_")]), 8)
        searches = [p for u, p in self.calls if u == eg.NSF_API and "keyword" in p]
        self.assertEqual(len(searches), 4)  # one short page per phrase, then stop

    def test_limit_is_respected(self):
        _, signals = self.run_collect(limit=3)
        self.assertLessEqual(len(signals), 3)
        self.assertGreaterEqual(len(signals), 1)

    def test_one_feed_failing_does_not_lose_the_other(self):
        def flaky(url, params=None, **kw):
            if url == eg.NSF_API:
                raise OSError("connection reset")
            return self.fake_get_json(url, params, **kw)

        self.calls = []
        ctx = Context(today=TODAY)
        with mock.patch.object(eg.http, "get_json", side_effect=flaky), \
                mock.patch.object(eg.http, "get", side_effect=self.fake_get):
            signals = list(eg.collect(ctx))
        self.assertEqual([(s.kind, s.entity.name) for s in signals],
                         [("arpa_e_project", "Deep Blue Geophysics, LLC"), ("arpa_e_project", "Wetstone Exploration")])
        self.assertTrue(ctx.warnings)


if __name__ == "__main__":
    unittest.main()
