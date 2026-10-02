"""Offline tests for the research_affil collector: parsing against saved responses, no network."""

from __future__ import annotations

import copy
import json
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from antenna.collectors import research_affil as ra
from antenna.collectors.base import Context, normalize_name
from antenna.db import fingerprint
from antenna.thesis import classify

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "research"
TODAY = date(2026, 10, 1)
SINCE = date(2026, 6, 3)


def _openalex() -> dict:
    return json.loads((FIXTURES / "openalex_affiliation_signals.json").read_text())


def _hf_items() -> list[dict]:
    return json.loads((FIXTURES / "paper_code_linkage_hf_s2.json").read_text())["hf_daily_papers"]["results"]


def _papers() -> list[ra.Paper]:
    works = _openalex()["works_arxiv_robotics_with_raw_affiliations"]["results"]
    return [p for p in (ra.parse_work(w) for w in works) if p]


class CleanAffiliationTest(unittest.TestCase):
    def test_company_names_are_kept_and_addresses_dropped(self):
        cases = {
            "Genesis AI": "Genesis AI",
            "Genesis AI, San Carlos, USA": "Genesis AI",
            ") Proxima Fusion GmbH , Munich , Germany": "Proxima Fusion GmbH",
            "Beijing VeloAlpha Technology Co., Ltd. , Beijing , 100080 , China": "Beijing VeloAlpha Technology Co. Ltd",
            "Tsing-AI(Shanghai) Technology Co. , Ltd": "Tsing-AI (Shanghai) Technology Co. Ltd",
            "Blue Laser Fusion Inc., 6950 Hollister Ave., Goleta, CA, 93117, USA": "Blue Laser Fusion Inc",
            "Propulsion Division, Perigee Aerospace Inc., 96 Gajeongbukro, Daejeon, 34111, Korea": "Perigee Aerospace Inc",
            "Flowbotic Mobile Systems , S.A. Lugar do Pombal , 3530-259 Viseu , Portugal": "Flowbotic Mobile Systems S.A.",
            "Knowin AI Shenzhen , China": "Knowin AI",
            "Dense - AI": "Dense-AI",
            "Avalanche Energy , 9100 E Marginal Way S , Tukwila , WA 98108 , USA": "Avalanche Energy",
            "Inertia Enterprises": "Inertia Enterprises",
            "Alternative Machine Inc.  Tokyo Japan": "Alternative Machine Inc",
        }
        for raw, want in cases.items():
            got = ra.clean_affiliation(raw)
            self.assertIsNotNone(got, raw)
            self.assertEqual(got.name, want, raw)
            self.assertTrue(got.strong, raw)

    def test_schools_labs_people_and_debris_are_rejected(self):
        for raw in [
            "Independent Researcher",
            "Carnegie Mellon University",
            "Zhejiang Key Laboratory of Additive Manufacturing Technology and Equipment , Hangzhou , China",
            "Institute for AI Industry Research (AIR) , Tsinghua University ;",
            "Robotics , Artificial Intelligence and Real-time Systems , Technical University of Munich",
            "X Robotics Lab",
            "Equal contribution",
            "https:// protracer-failure",
            "Wei Du is with PNNL , Richland , WA 99352 USA ,",
            "Intelligent Field Robotic Systems",
            "Guangzhou , China",
            "IMU Rata 关键帧冻结",
            "hessian. AI",
            "",
            None,
        ]:
            self.assertIsNone(ra.clean_affiliation(raw), raw)

    def test_a_name_that_lost_its_start_is_rejected(self):
        # The paper says "e:fs TechHub GmbH"; OpenAlex holds ": fs TechHub GmbH".
        self.assertIsNone(ra.clean_affiliation("Space Applications Group , : fs TechHub GmbH , Gaimersheim , Germany"))
        self.assertIsNone(ra.clean_affiliation(": fs TechHub GmbH , Gaimersheim , Germany"))
        self.assertIsNone(ra.clean_affiliation("{luke0911 ,"))
        self.assertIsNone(ra.clean_affiliation("/ Forestry-Robotics-UC/ig_lio/tree /ros2- jazzy"))
        # Footnote marks in front are not damage.
        self.assertEqual(ra.clean_affiliation("* Acme Robotics Inc").name, "Acme Robotics Inc")

    def test_html_entities_and_typographic_hyphens(self):
        got = ra.clean_affiliation("Acadian Research &amp; Development LLC Laramie Wyoming USA")
        self.assertEqual(got.name, "Acadian Research & Development LLC")
        self.assertEqual(ra.clean_affiliation("e-SOFT Corp.,R&#x0026;D Dept.,Taiwan").name, "e-SOFT Corp")
        self.assertEqual(ra.clean_affiliation("LCP Laser\u2010Cut\u2010Processing GmbH  Hermsdorf Germany").name,
                         "LCP Laser-Cut-Processing GmbH")

    def test_advisers_holdings_and_generic_names_are_rejected(self):
        for raw in ["TRIZ Consulting Group GmbH", "K2 Holding, L.L.C, Abu Dhabi, United Arab Emirates",
                    "punctum pr-agentur GmbH ; Neuer Zollhof 3 , Düsseldorf , Germany",
                    "Tech and Business Solutions LLC, 1AK-GROUP",
                    "Hubei Humanoid Robot Innova- tion Center Co. , Ltd",
                    "Chantier Davie Canada Inc, Levis, Canada"]:
            self.assertIsNone(ra.clean_affiliation(raw), raw)

    def test_incumbents_are_not_discoveries(self):
        for raw in ["Samsung Robotics eXperience", "Huawei Technologies Co. , Ltd", "Li Auto Inc. Beijing , China",
                    "Advanced Production Technology Div., DENSO CORPORATION"]:
            self.assertIsNone(ra.clean_affiliation(raw), raw)

    def test_bare_names_are_weak(self):
        # No legal form and no company-like last word: kept only next to a marked spelling.
        for raw in ["XGRIDS", "Sophelio , Austin , TX USA", "X-Humanoid , Humanoid Innovation Department"]:
            got = ra.clean_affiliation(raw)
            self.assertIsNotNone(got, raw)
            self.assertFalse(got.strong, raw)

    def test_a_name_split_by_a_comma_is_not_halved(self):
        got = ra.clean_affiliation("Mühlbauer+partner, Technische Dokumentation GmbH&Co. KG")
        self.assertFalse(got is not None and got.strong)

    def test_location_and_key(self):
        got = ra.clean_affiliation("Gauss Fusion GmbH , Parkring 29 , 85748 Garching bei München , Germany")
        self.assertEqual(got.location, "Garching bei München, Germany")
        self.assertEqual(got.key, "gauss fusion")
        self.assertTrue(got.legal)
        # Legal forms do not split a company; descriptive words do.
        self.assertEqual(ra.company_key("Sophelio LLC"), ra.company_key("Sophelio"))
        self.assertNotEqual(ra.company_key("Shadow AI"), ra.company_key("Shadow Robotics"))

    def test_key_agrees_with_the_shared_one_except_on_accents_and_european_forms(self):
        for name in ["Genesis AI", "Gauss Fusion GmbH", "Blue Laser Fusion Inc", "Flowbotic Mobile Systems S.A.",
                     "Tsing-AI (Shanghai) Technology Co. Ltd", "Beijing VeloAlpha Technology Co. Ltd"]:
            self.assertEqual(ra.company_key(name), normalize_name(name), name)
        # What the shared key does not do, and why this one is kept.
        self.assertEqual(ra.company_key("Grünweg GmbH"), "grunweg")
        self.assertNotEqual(normalize_name("Grünweg GmbH"), "grunweg")
        self.assertEqual(ra.company_key("VinRobotics JSC"), ra.company_key("VinRobotics"))
        self.assertNotEqual(normalize_name("VinRobotics JSC"), normalize_name("VinRobotics"))
        self.assertEqual(ra.company_key("ProLog Automation GmbH & Co. KG"), "prolog automation")


class WorkParsingTest(unittest.TestCase):
    def test_evidence_url_prefers_public_pages(self):
        self.assertEqual(ra.evidence_url({"doi": "https://doi.org/10.48550/arxiv.2609.28766"}),
                         "https://arxiv.org/abs/2609.28766")
        self.assertEqual(ra.evidence_url({"doi": "https://doi.org/10.1038/s42005-026-02829-8"}),
                         "https://doi.org/10.1038/s42005-026-02829-8")
        self.assertEqual(
            ra.evidence_url({"doi": None, "primary_location": {"landing_page_url": "https://example.org/paper/1"}}),
            "https://example.org/paper/1")
        self.assertIsNone(ra.evidence_url({"doi": None, "primary_location": None}))

    def test_abstract_is_rebuilt_in_order(self):
        self.assertEqual(ra.abstract_text({"robot": [1, 4], "A": [0], "meets": [2], "a": [3]}), "A robot meets a robot")
        self.assertEqual(ra.abstract_text(None), "")

    def test_self_upload_repositories_are_skipped(self):
        work = copy.deepcopy(_openalex()["works_arxiv_robotics_with_raw_affiliations"]["results"][0])
        self.assertIsNotNone(ra.parse_work(work))
        work["doi"] = "https://doi.org/10.5281/zenodo.1234567"
        self.assertIsNone(ra.parse_work(work))

    def test_tapesim_carries_genesis_ai(self):
        paper = next(p for p in _papers() if p.url == "https://arxiv.org/abs/2609.28766")
        self.assertEqual(paper.date, date(2026, 9, 23))
        self.assertEqual(paper.n_authors, 13)
        self.assertFalse(paper.uniform)
        mentions = paper.mentions["genesis ai"]
        self.assertEqual(len(mentions), 13)
        by_name = {m.author_name: m for m in mentions}
        # Dual affiliation, and the co-affiliation is borne out by the raw line.
        self.assertEqual(by_name["Minchen Li"].others, ["Carnegie Mellon University"])
        self.assertFalse(by_name["Minchen Li"].company_only)
        self.assertEqual(by_name["Minchen Li"].position, "last")
        self.assertTrue(by_name["Yi-Ling Qiao"].company_only)
        self.assertEqual(by_name["Yi-Ling Qiao"].author_id, "A5010354503")
        self.assertGreaterEqual(paper.fit, 0.3)

    def test_names_are_printed_names(self):
        self.assertEqual(ra.natural_name("Lee, Jiyul"), "Jiyul Lee")
        self.assertEqual(ra.natural_name("Kritcher, A. L."), "A. L. Kritcher")
        self.assertEqual(ra.natural_name("Matvey V. NASANOVICH"), "Matvey V. Nasanovich")
        self.assertEqual(ra.natural_name("Anthony Y. Ku"), "Anthony Y. Ku")
        self.assertEqual(ra.natural_name(None), "")

    def test_collaborations_repeats_and_missing_ids(self):
        def authorship(raw_name, display, aid, lines):
            return {"author": {"id": aid and f"https://openalex.org/{aid}", "display_name": display},
                    "raw_author_name": raw_name, "author_position": "middle", "institutions": [],
                    "affiliations": [{"raw_affiliation_string": x, "institution_ids": []} for x in lines]}
        work = {"id": "https://openalex.org/W9", "doi": "https://doi.org/10.1016/j.cryogenics.2026.104472",
                "title": "A stellarator magnet system for a fusion power plant", "publication_date": "2026-09-12",
                "authorships": [
                    authorship("Shin Hasegawa", "Shin Hasegawa", "A1", ["Gauss Fusion GmbH, Garching, Germany"]),
                    authorship("Shin Hasegawa", "Shin Hasegawa", "A1", ["Gauss Fusion GmbH, Garching, Germany"]),
                    # OpenAlex gave this author no id at all.
                    authorship("Neil Mitchell", "Neil Mitchell", None, ["Gauss Fusion GmbH, Garching, Germany"]),
                    authorship("Collaboration, Gauss", "Gauss Collaboration", "A3", []),
                    {"author": None, "raw_author_name": "Peter Stadler", "affiliations": [
                        {"raw_affiliation_string": None, "institution_ids": []}]},
                ]}
        paper = ra.parse_work(work)
        self.assertEqual(paper.n_authors, 3)  # Hasegawa once, Mitchell, Stadler; not the collaboration
        self.assertEqual(paper.doi, "10.1016/j.cryogenics.2026.104472")
        self.assertIsNone(paper.arxiv_id)
        mentions = paper.mentions["gauss fusion"]
        self.assertEqual([m.author_name for m in mentions], ["Shin Hasegawa", "Neil Mitchell"])
        self.assertEqual(mentions[1].author_id, "name:mitchell neil")
        self.assertEqual(len(ra.group_companies([paper], SINCE, TODAY)[0].authors), 2)

    def test_vague_institution_matches_are_dropped(self):
        work = {"id": "https://openalex.org/W8", "doi": "https://doi.org/10.48550/arxiv.2609.23296",
                "title": "A tokamak equilibrium database for fusion", "publication_date": "2026-09-20",
                "authorships": [{
                    "author": {"id": "https://openalex.org/A7", "display_name": "Ruohan Zhang"},
                    "raw_author_name": "Zhang, Ruohan", "author_position": "middle",
                    "institutions": [{"id": "I1", "display_name": "Dalian University of Technology"},
                                     {"id": "I2", "display_name": "Ministry of Education"}],
                    "affiliations": [
                        {"raw_affiliation_string": "Beijing VeloAlpha Technology Co., Ltd. , Beijing , China",
                         "institution_ids": []},
                        {"raw_affiliation_string": "Key Laboratory of Materials Modification by Beams of the Ministry "
                                                   "of Education , Dalian University of Technology , Dalian , China",
                         "institution_ids": ["I1", "I2"]}]}]}
        paper = ra.parse_work(work)
        self.assertEqual(paper.arxiv_id, "2609.23296")
        mention = paper.mentions["beijing veloalpha technology"][0]
        self.assertEqual(mention.author_name, "Ruohan Zhang")
        self.assertEqual(mention.others, ["Dalian University of Technology"])

    def test_names_found_across_the_fixture(self):
        strong = {c.name for p in _papers() for cs in p.affils.values() for c in cs if c.strong}
        for want in ["Genesis AI", "Sudo AI GmbH", "Sapient Intelligence",
                     "Tsing-AI (Shanghai) Technology Co. Ltd", "Yunshenchu Technology Co. Ltd",
                     "Flowbotic Mobile Systems S.A."]:
            self.assertIn(want, strong)
        every = {c.name for p in _papers() for cs in p.affils.values() for c in cs}
        for junk in ["Independent Researcher", "Samsung Robotics eXperience", "Amazon FAR", "LandSpace"]:
            self.assertFalse(any(junk in n for n in every), junk)

    def test_false_institution_matches_are_not_reported(self):
        # OpenAlex maps "DexRobot Co. Ltd" to Medrobotics (source card, gotcha 11).
        self.assertFalse(ra._inst_verified("Medrobotics (United States)", ["DexRobot Co. Ltd"]))
        self.assertFalse(ra._inst_verified("Berkeley College", ["UC Berkeley"]))
        self.assertTrue(ra._inst_verified("Carnegie Mellon University", ["Carnegie Mellon University"]))
        self.assertTrue(ra._inst_verified("Massachusetts Institute of Technology", ["CSAIL, MIT, Cambridge MA"]))

    def test_everything_to_everyone_is_flagged(self):
        def authorship(i):
            return {"author": {"id": f"https://openalex.org/A{i}", "display_name": f"Author {i}"},
                    "author_position": "middle", "institutions": [],
                    "affiliations": [{"raw_affiliation_string": "Harbin Institute of Technology , Shenzhen",
                                      "institution_ids": ["https://openalex.org/I1"]},
                                     {"raw_affiliation_string": "PHANES AI", "institution_ids": []}]}
        work = {"id": "https://openalex.org/W1", "doi": "https://doi.org/10.48550/arxiv.2607.07287",
                "title": "A tactile foundation model for dexterous robot manipulation",
                "publication_date": "2026-07-08", "authorships": [authorship(i) for i in range(4)]}
        paper = ra.parse_work(work)
        self.assertTrue(paper.uniform)
        self.assertEqual(paper.n_affiliations, 2)
        company = ra.group_companies([paper], SINCE, TODAY)[0]
        self.assertEqual(company.authors, {})  # the paper cannot say who is at the company


class ThesisGateTest(unittest.TestCase):
    """OpenAlex's topic is a second witness for a paper with one strong thesis word, never the only one."""

    FUSION = "High-dimensional inverse design of inertial fusion implosions via differentiable simulation"
    KITES = "High human-caused mortality in GPS-tracked red kites across Europe"

    def _work(self, title: str, topic: str | None, line: str) -> dict:
        return {"id": "https://openalex.org/W7", "doi": "https://doi.org/10.48550/arxiv.2609.11111",
                "title": title, "publication_date": "2026-09-10",
                "primary_topic": topic and {"id": "https://openalex.org/T10384", "display_name": topic,
                                            "field": {"display_name": "Physics and Astronomy"}},
                "authorships": [{"author": {"id": "https://openalex.org/A1", "display_name": "Ann Lee"},
                                 "raw_author_name": "Ann Lee", "author_position": "first", "institutions": [],
                                 "affiliations": [{"raw_affiliation_string": line, "institution_ids": []}]}]}

    def test_topic_is_stated_in_plain_words(self):
        self.assertEqual(ra.topic_note("Laser-Plasma Interactions and Diagnostics"),
                         "OpenAlex topic: Laser-Plasma Interactions and Diagnostics.")
        self.assertEqual(ra.topic_note(None), "")

    def test_one_strong_word_and_the_topic_pass(self):
        own = f"Ergodic LLC. {self.FUSION}"
        # "fusion" alone sits under the gate: it is also a brand name and a data technique.
        self.assertLess(classify(own)["fit"], 0.3)
        self.assertFalse(ra.on_thesis(own, None))
        self.assertTrue(ra.on_thesis(own, "Laser-Plasma Interactions and Diagnostics"))
        # Words that pass alone need no topic.
        self.assertTrue(ra.on_thesis("A stellarator magnet system for a fusion power plant", None))

    def test_the_topic_alone_proves_nothing(self):
        topic = "Robotics and Sensor-Based Localization"  # where OpenAlex filed the red kites
        self.assertGreaterEqual(classify(ra.topic_note(topic))["fit"], 0.3)
        self.assertFalse(ra.on_thesis(f"Amprion GmbH. {self.KITES}", topic))
        # A weak word from the company's name is not a strong word in the paper.
        self.assertFalse(ra.on_thesis(f"Qualitas Energy Service GmbH. {self.KITES}", topic))

    def test_a_false_friend_is_not_a_strong_word_for_the_topic_to_second(self):
        # Each of these used to read as "fusion" or "nuclear" in the paper, and the topic then let it through.
        for own, topic in [
            ("Knowin AI. Sparsity Curse: Understanding RLVR Model Parameter Space from Model Merging. Merging "
             "fine-tuned checkpoints promises capability fusion without retraining.", "Reinforcement Learning in Robotics"),
            ("Acme Mapping Inc. BEV fusion of camera features for lane topology", "Robotics and Sensor-Based Localization"),
            ("Few-Cycle Inc. Narrowband and wavelength-tuneable bright EUV harmonics for resonant imaging and "
             "nuclear clock spectroscopy", "Laser-Plasma Interactions and Diagnostics"),
            ("LCP Laser-Cut-Processing GmbH. Controlling Grain Growth in Powder Bed Fusion of Yttria-Stabilized "
             "Zirconia Using Femtosecond Lasers", "Additive Manufacturing Materials and Processes"),
        ]:
            self.assertEqual(classify(own)["terms"], [], own)
            self.assertFalse(ra.on_thesis(own, topic), own)
        # A printing paper that says so in its own words is a manufacturing paper, not an energy one.
        own = ("Fehrmann Materials X GmbH. Identifying process windows of aluminum alloys in laser powder bed fusion, "
               "an additive manufacturing process")
        self.assertTrue(ra.on_thesis(own, None))
        self.assertEqual(classify(own)["sector"], "manufacturing")

    def test_a_topic_that_repeats_the_papers_one_word_adds_no_second_term(self):
        topic = "Advanced Battery Technologies Research"
        own = ("Ionworks Technologies Inc. Incorporating multiscale mechanics in lithium-ion battery models. "
               "Swelling in lithium-ion batteries generates stresses.")
        # Singular and plural are one term, and "battery" is not one that passes alone.
        self.assertEqual(classify(own)["terms"], ["battery"])
        self.assertEqual(classify(f"{own} {ra.topic_note(topic)}")["terms"], ["battery"])
        self.assertFalse(ra.on_thesis(own, topic))
        # A second word about the cell itself is enough, with or without a topic.
        self.assertTrue(ra.on_thesis(own.replace("stresses", "stresses in the cathode"), None))

    def test_grouping_uses_the_topic_as_second_witness(self):
        line = "Ergodic LLC, Seattle, WA, USA"
        with_topic = ra.parse_work(self._work(self.FUSION, "Laser-Plasma Interactions and Diagnostics", line))
        self.assertEqual(with_topic.topic, "Laser-Plasma Interactions and Diagnostics")
        self.assertLess(with_topic.fit, 0.3)  # the paper's fit is still its own words only
        self.assertEqual([c.name for c in ra.group_companies([with_topic], SINCE, TODAY)], ["Ergodic LLC"])
        without = ra.parse_work(self._work(self.FUSION, None, line))
        self.assertEqual(ra.group_companies([without], SINCE, TODAY), [])
        kites = ra.parse_work(self._work(self.KITES, "Robotics and Sensor-Based Localization",
                                         "Qualitas Energy Service GmbH, Berlin, Germany"))
        self.assertEqual(ra.group_companies([kites], SINCE, TODAY), [])

    def test_signal_text_carries_the_topic_so_the_pipeline_sees_two_terms(self):
        paper = ra.parse_work(self._work(self.FUSION, "Laser-Plasma Interactions and Diagnostics",
                                         "Ergodic LLC, Seattle, WA, USA"))
        company = ra.group_companies([paper], SINCE, TODAY)[0]
        signals = ra.company_signals(company, ra.History(checked=True, complete=True), False, {}, Context(today=TODAY))
        self.assertEqual([s.kind for s in signals], ["company_first_paper"])
        s = signals[0]
        s.validate()
        self.assertEqual(s.text, f"{self.FUSION}. OpenAlex topic: Laser-Plasma Interactions and Diagnostics.")
        seen = classify(f"{s.entity.name} {s.title} {s.text}")
        self.assertGreaterEqual(seen["fit"], 0.3)
        self.assertEqual(seen["sector"], "energy")
        # Without a topic the same paper is held back, as the lone-term rule intends.
        paper.topic = None
        self.assertEqual(
            ra.company_signals(company, ra.History(checked=True, complete=True), False, {}, Context(today=TODAY)), [])


class GroupingTest(unittest.TestCase):
    def test_groups_on_thesis_companies(self):
        companies = {c.name: c for c in ra.group_companies(_papers(), SINCE, TODAY)}
        self.assertIn("Genesis AI", companies)
        self.assertIn("Yunshenchu Technology Co. Ltd", companies)
        genesis = companies["Genesis AI"]
        self.assertEqual(len(genesis.papers), 1)
        self.assertEqual(len(genesis.authors), 13)
        self.assertFalse(genesis.legal)
        self.assertEqual(companies["Yunshenchu Technology Co. Ltd"].location, "Hangzhou, China")
        # Weak-only names never become companies.
        self.assertNotIn("XGRIDS", companies)
        self.assertNotIn("X-Humanoid", companies)

    def test_window_is_respected(self):
        self.assertEqual(ra.group_companies(_papers(), date(2026, 9, 24), TODAY), [])
        self.assertEqual(ra.group_companies(_papers(), SINCE, date(2026, 9, 1)), [])

    def test_preprint_and_correction_count_once(self):
        self.assertEqual(ra.title_key("Correction: A Paper, on Robots"), ra.title_key("A paper on robots"))
        papers = [p for p in _papers() if "genesis ai" in p.mentions]
        twin = copy.deepcopy(papers[0])
        twin.id, twin.date = "W999", date(2026, 9, 30)
        company = next(c for c in ra.group_companies(papers + [twin], SINCE, TODAY) if c.key == "genesis ai")
        self.assertEqual(len(company.papers), 1)
        self.assertEqual(company.papers[0].date, date(2026, 9, 23))
        self.assertEqual(company.duplicate_ids, {"W999"})


# Author blocks in the shapes LaTeXML gives arxiv.org/html pages, cut down from real ones.
PAGE_MARKERS = """<html><body><div class="ltx_authors">
<span class="ltx_creator ltx_role_author"><span class="ltx_personname">Zhaofeng Luo<sup class="ltx_sup"><span
class="ltx_text ltx_font_italic">1,2</span></sup>, Xinyu Lu<sup class="ltx_sup">2</sup>, Siyuan Huang<sup
class="ltx_sup">1&#8224;</sup>, <br class="ltx_break">Yi-Ling Qiao<sup class="ltx_sup">2</sup>,
Minchen Li<sup class="ltx_sup">1,2</sup></span><span class="ltx_author_notes"><span class="ltx_author_notes_content">
<span class="ltx_contact ltx_role_affiliation"><span class="ltx_contact_name">Affiliation: </span><sup
class="ltx_sup">1</sup>Carnegie Mellon University <sup class="ltx_sup">2</sup>Genesis AI <br class="ltx_break"><sup
class="ltx_sup">*</sup>Corresponding author: Minchen Li</span></span></span></span></div>
<div class="ltx_abstract"><p>Genesis AI builds simulators.</p></div></body></html>"""

PAGE_OWN = """<div class="ltx_authors">
<span class="ltx_creator ltx_role_author"><span class="ltx_personname">Alexander Meinert</span><span
class="ltx_author_notes"><span class="ltx_author_notes_content"><span class="ltx_contact ltx_role_affiliation"><span
class="ltx_contact_name">Affiliation: </span>Alexander Meinert and Peter Stadler are with the Space Applications
Group, Acme Orbital GmbH, Gaimersheim, Germany. Alen Turnwald is with Beta Space GmbH, Ingolstadt.
<span class="ltx_text ltx_font_typewriter">{a.meinert, p.stadler}@acme-orbital.com</span></span></span></span></span>
<span class="ltx_author_before"> </span>
<span class="ltx_creator ltx_role_author"><span class="ltx_personname">Peter Stadler</span><span
class="ltx_author_notes"><span class="ltx_contact ltx_role_affiliation"><span class="ltx_contact_name">Affiliation:
</span>Alexander Meinert and Peter Stadler are with the Space Applications Group, Acme Orbital GmbH, Gaimersheim,
Germany. Alen Turnwald is with Beta Space GmbH, Ingolstadt.</span></span></span>
<span class="ltx_creator ltx_role_author"><span class="ltx_personname">Alen Turnwald</span><span
class="ltx_note ltx_role_thanks"><sup class="ltx_note_mark">&#8224;</sup><span class="ltx_note_outer">thanks: funded
by Acme Orbital GmbH.</span></span><span class="ltx_author_notes"><span class="ltx_contact ltx_role_affiliation"><span
class="ltx_contact_name">Affiliation: </span>Alexander Meinert and Peter Stadler are with the Space Applications
Group, Acme Orbital GmbH, Gaimersheim, Germany. Alen Turnwald is with Beta Space GmbH, Ingolstadt.</span></span></span>
<span class="ltx_creator ltx_role_author"><span class="ltx_personname">Hua Chen<sup class="ltx_sup">*</sup></span><span
class="ltx_author_notes"><span class="ltx_contact ltx_role_affiliation"><span class="ltx_contact_name">Affiliation:
</span>Acme Orbital GmbH, Gaimersheim, Germany.</span><span class="ltx_contact ltx_role_address"><span
class="ltx_contact_name">Address: </span>Zhejiang University, Hangzhou, China</span><span
class="ltx_contact ltx_role_email"><span class="ltx_contact_name">Email: </span>hua@acme-orbital.com</span></span></span>
<span class="ltx_creator ltx_role_author"><span class="ltx_personname">Yang Liu</span><span
class="ltx_author_notes"><span class="ltx_contact ltx_role_affiliation"><span class="ltx_contact_name">Affiliation:
</span>Acme Orbital GmbH, Gaimersheim, Germany Zhejiang University, Hangzhou, China</span></span></span>
</div><div class="ltx_abstract">Abstract</div>"""

PAGE_LINES = """<div class="ltx_authors"><span class="ltx_creator ltx_role_author"><span class="ltx_personname">
<span class="ltx_p">C. R. Weber<sup class="ltx_sup">*</sup>, S. Bhandarkar, T. Briggs,</span>
<span class="ltx_p">A. Wray</span>
<span class="ltx_p"><span class="ltx_text ltx_font_italic">Lawrence Livermore National Laboratory <br
class="ltx_break"></span>A. L. Kritcher<sup class="ltx_sup">&#8224;</sup>, N. Alexander,</span>
<span class="ltx_p">R. Toro, and the Inertia Collaboration<sup class="ltx_sup">&#8225;</sup></span>
<span class="ltx_p"><span class="ltx_text ltx_font_italic">Inertia Enterprises <br class="ltx_break"></span>
<span class="ltx_text">Corresponding authors: weber30@llnl.gov and annie@inertia.com</span></span>
</span></span></div><div class="ltx_abstract">Abstract</div>"""

# A block shared by a group hangs on its last author; the company's person is Chao Li, not Jun Wu.
PAGE_SHARED = """<div class="ltx_authors">
<span class="ltx_creator ltx_role_author"><span class="ltx_personname">Sicen Li</span></span>
<span class="ltx_creator ltx_role_author"><span class="ltx_personname">Chao Li</span></span>
<span class="ltx_creator ltx_role_author"><span class="ltx_personname">Jun Wu</span><span class="ltx_author_notes"><span
class="ltx_contact ltx_role_affiliation"><span class="ltx_contact_name">Affiliation: </span>Yunshenchu Technology Co.,
Ltd., Hangzhou, China (e-mail: lichao@deeprobotics.cn).</span></span></span>
</div><div class="ltx_abstract">Abstract</div>"""

# Names over an unnumbered list of affiliations: nobody can say who is where.
PAGE_UNLABELLED = """<div class="ltx_authors"><span class="ltx_creator ltx_role_author"><span class="ltx_personname">
<span class="ltx_text ltx_font_bold">Hongyi Lin</span> <span class="ltx_text ltx_font_bold">Song Zhang</span>
<span class="ltx_text ltx_font_bold">Haiquan Liu</span></span><span class="ltx_author_notes"><span
class="ltx_contact ltx_role_affiliation"><span class="ltx_contact_name">Affiliation: </span>Tsinghua University
Tsing-AI(Shanghai) Technology Co., Ltd</span></span></span></div><div class="ltx_abstract">Abstract</div>"""

# Affiliations only in footnotes; OpenAlex gave the company to the wrong author.
PAGE_FOOTNOTES = """<div class="ltx_authors">
<span class="ltx_creator ltx_role_author"><span class="ltx_personname">Bhavnashri A Sobia Shafi</span></span>
<span class="ltx_creator ltx_role_author"><span class="ltx_personname">Anuj Tiwari</span><span
class="ltx_note ltx_role_thanks"><sup class="ltx_note_mark">&#8224;</sup><span class="ltx_note_outer"><span
class="ltx_note_content">thanks: $^1$Atomberg Technologies Limited, India bhavnashri.a@gmail.com</span></span></span>
</span></div><div class="ltx_abstract">Abstract</div>"""


def _company(name: str, aliases: list[str] | None = None) -> ra.Company:
    return ra.Company(key=ra.company_key(name), name=name, aliases=aliases or [], legal=True, location=None, papers=[])


def _paper(names: list[str], key: str, at_company: dict[str, bool], arxiv_id: str = "2609.00001") -> ra.Paper:
    """A paper on which OpenAlex puts `at_company` (name -> company_only) at the company `key`."""
    paper = ra.Paper(id="W1", title="A robot paper", date=date(2026, 9, 20), url=f"https://arxiv.org/abs/{arxiv_id}",
                     fit=0.8, text="robot", n_authors=len(names), topic=None, arxiv_id=arxiv_id, author_names=names)
    paper.mentions[key] = [ra.Mention(author_id=f"A{i}", author_name=n, position="middle", company_only=only,
                                      others=["Carnegie Mellon University", "Zhejiang University"])
                           for i, (n, only) in enumerate(at_company.items())]
    return paper


class ArxivPageTest(unittest.TestCase):
    def _kept(self, page: str, company: ra.Company, paper: ra.Paper) -> list[str]:
        return [m.author_name for m in ra.check_page(page, company, paper)]

    def test_numbered_affiliations(self):
        names = ["Zhaofeng Luo", "Xinyu Lu", "Siyuan Huang", "Yi-Ling Qiao", "Minchen Li"]
        where = ra.page_affiliations(ra.read_author_block(PAGE_MARKERS), names)
        self.assertEqual(where["luo zhaofeng"], [("Carnegie Mellon University", "marker"), ("Genesis AI", "marker")])
        self.assertEqual(where["lu xinyu"], [("Genesis AI", "marker")])
        self.assertEqual(where["huang siyuan"], [("Carnegie Mellon University", "marker")])  # "1†" is label 1
        company = _company("Genesis AI")
        # OpenAlex also put Siyuan Huang at the company; the page does not.
        paper = _paper(names, company.key, {"Zhaofeng Luo": False, "Xinyu Lu": True, "Siyuan Huang": True,
                                            "Minchen Li": False})
        kept = ra.check_page(PAGE_MARKERS, company, paper)
        self.assertEqual([m.author_name for m in kept], ["Zhaofeng Luo", "Xinyu Lu", "Minchen Li"])
        # A co-affiliation survives only when the page shows it for that author.
        self.assertEqual(kept[0].others, ["Carnegie Mellon University"])
        self.assertEqual(kept[1].others, [])

    def test_own_affiliation_lines_and_footnote_sentences(self):
        names = ["Alexander Meinert", "Peter Stadler", "Alen Turnwald", "Hua Chen", "Yang Liu"]
        company = _company("Acme Orbital GmbH")
        # OpenAlex gave the one line it found to every author.
        paper = _paper(names, company.key, {n: True for n in names})
        kept = ra.check_page(PAGE_OWN, company, paper)
        # Turnwald's sentence puts him elsewhere (and the funding footnote does not count);
        # Yang Liu's single line names a university beside the company.
        self.assertEqual([m.author_name for m in kept], ["Alexander Meinert", "Peter Stadler", "Hua Chen"])
        self.assertEqual(kept[2].others, ["Zhejiang University"])  # his own second line
        self.assertEqual(kept[0].others, [])

    def test_runs_of_names_over_one_line(self):
        names = ["C. R. Weber", "S. Bhandarkar", "T. Briggs", "A. Wray", "A. L. Kritcher", "N. Alexander", "R. Toro",
                 "Inertia Collaboration"]
        where = ra.page_affiliations(ra.read_author_block(PAGE_LINES), names)
        self.assertEqual(where["a wray"], [("lawrence livermore national laboratory", "line")])
        self.assertEqual(where["a kritcher l"], [("inertia enterprises", "line")])
        company = _company("Inertia Enterprises")
        paper = _paper(names, company.key, {"A. L. Kritcher": True, "N. Alexander": True, "R. Toro": True,
                                            "A. Wray": True})
        self.assertEqual(self._kept(PAGE_LINES, company, paper), ["A. L. Kritcher", "N. Alexander", "R. Toro"])
        # The line shape is not enough when OpenAlex shows the author elsewhere too.
        paper = _paper(names, company.key, {"R. Toro": False})
        self.assertEqual(self._kept(PAGE_LINES, company, paper), [])

    def test_layouts_that_do_not_say_who_is_where_verify_nobody(self):
        company = _company("Yunshenchu Technology Co. Ltd")
        paper = _paper(["Sicen Li", "Chao Li", "Jun Wu"], company.key, {"Jun Wu": True, "Chao Li": True})
        self.assertEqual(self._kept(PAGE_SHARED, company, paper), [])
        company = _company("Tsing-AI (Shanghai) Technology Co. Ltd")
        paper = _paper(["Hongyi Lin", "Song Zhang", "Haiquan Liu"], company.key, {"Song Zhang": True})
        self.assertEqual(self._kept(PAGE_UNLABELLED, company, paper), [])
        company = _company("Atomberg Technologies Limited")
        paper = _paper(["Bhavnashri A", "Sobia Shafi", "Anuj Tiwari"], company.key, {"Sobia Shafi": False,
                                                                                    "Anuj Tiwari": True})
        self.assertEqual(self._kept(PAGE_FOOTNOTES, company, paper), [])
        self.assertEqual(ra.read_author_block("<html><body>no author block</body></html>"), [])
        self.assertEqual(ra.page_affiliations([], ["Ann Lee"]), {})

    def test_verify_arxiv_keeps_only_what_the_page_bears_out(self):
        company = _company("Genesis AI")
        names = ["Zhaofeng Luo", "Xinyu Lu", "Siyuan Huang", "Yi-Ling Qiao", "Minchen Li"]
        checked = _paper(names, company.key, {"Xinyu Lu": True, "Siyuan Huang": True}, "2609.00001")
        no_html = _paper(names, company.key, {"Xinyu Lu": True}, "2609.00002")
        no_html.id = "W2"
        broken = _paper(names, company.key, {"Xinyu Lu": True}, "2609.00003")
        broken.id = "W3"
        journal = _paper(names, company.key, {"Yi-Ling Qiao": True}, "x")
        journal.id, journal.arxiv_id, journal.url = "W4", None, "https://doi.org/10.1000/j"
        company.papers = [checked, no_html, broken, journal]

        def fake_get(url, **kw):
            self.assertTrue(url.startswith("https://export.arxiv.org/html/"))
            if url.endswith("2609.00001"):
                return PAGE_MARKERS
            if url.endswith("2609.00002"):
                raise ra.http.HttpError(404, url)
            raise OSError("timed out")

        ctx = Context(today=TODAY)
        with mock.patch.object(ra.http, "get", side_effect=fake_get):
            ra.verify_arxiv(ctx, [company])
        self.assertEqual([m.author_name for m in checked.mentions[company.key]], ["Xinyu Lu"])
        self.assertEqual(checked.unverified, set())
        self.assertEqual(no_html.mentions[company.key], [])
        self.assertEqual(no_html.unverified, {company.key})
        self.assertEqual(broken.unverified, {company.key})
        # Journal papers carry the publisher's own affiliations and are left alone.
        self.assertEqual([m.author_name for m in journal.mentions[company.key]], ["Yi-Ling Qiao"])
        self.assertEqual(len(ctx.warnings), 1)  # the time-out, not the missing page
        self.assertEqual(sorted(company.authors), ["A0"])  # Xinyu Lu on the checked paper, Qiao on the journal one


class DateTest(unittest.TestCase):
    def test_crossref_online_date(self):
        self.assertEqual(ra.crossref_online_date({"published-online": {"date-parts": [[2026, 9, 23]]},
                                                  "published": {"date-parts": [[2026, 9, 1]]}}), date(2026, 9, 23))
        self.assertEqual(ra.crossref_online_date({"published": {"date-parts": [[2026, 8, 27]]}}), date(2026, 8, 27))
        # An issue month says less than OpenAlex does.
        self.assertIsNone(ra.crossref_online_date({"published": {"date-parts": [[2026, 12]]}}))
        self.assertIsNone(ra.crossref_online_date({"published-online": {"date-parts": [[2026, 2, 31]]}}))
        self.assertIsNone(ra.crossref_online_date({"published-online": {"date-parts": [[None]]}}))
        self.assertIsNone(ra.crossref_online_date({}))

    def _journal_paper(self, pid: str, doi: str, day: date) -> ra.Paper:
        return ra.Paper(id=pid, title=f"Paper {pid}", date=day, url=f"https://doi.org/{doi}", fit=0.8, text="fusion",
                        n_authors=3, topic=None, doi=doi)

    def test_dates_follow_the_publisher_and_the_window_is_reapplied(self):
        accepted = self._journal_paper("W1", "10.1103/bpms-63ml", date(2026, 9, 1))  # registered at acceptance
        old = self._journal_paper("W2", "10.1000/old", date(2026, 6, 10))  # really online before the window
        future = self._journal_paper("W3", "10.1000/future", date(2026, 9, 28))
        plain = self._journal_paper("W4", "10.1016/j.x.2026.1", date(2026, 9, 18))  # Crossref has an issue month only
        preprint = _paper(["Ann Lee"], "ergodic", {"Ann Lee": True})  # dated 2026-09-20 by arXiv itself
        preprint.id = "W5"
        ergodic = _company("Ergodic LLC")
        ergodic.papers = [accepted, plain, preprint]
        gone = _company("Gone Inc")
        gone.papers = [old, future]
        seen = {}

        def fake_get_json(url, **kw):
            seen["url"], seen["filter"] = url, kw["params"]["filter"]
            return {"message": {"items": [
                {"DOI": "10.1103/BPMS-63ML", "published-online": {"date-parts": [[2026, 9, 23]]}},
                {"DOI": "10.1000/old", "published-online": {"date-parts": [[2026, 5, 2]]}},
                {"DOI": "10.1000/future", "published-online": {"date-parts": [[2026, 10, 9]]}},
                {"DOI": "10.1016/j.x.2026.1", "published": {"date-parts": [[2026, 12]]}},
            ]}}

        ctx = Context(today=TODAY)
        with mock.patch.object(ra.http, "get_json", side_effect=fake_get_json):
            kept = ra.fix_dates(ctx, [ergodic, gone])
        self.assertEqual(seen["url"], "https://api.crossref.org/works")
        self.assertEqual(seen["filter"].count("doi:"), 4)  # one call, and the arXiv paper is not asked about
        self.assertEqual(accepted.date, date(2026, 9, 23))
        self.assertEqual(plain.date, date(2026, 9, 18))
        self.assertEqual([c.name for c in kept], ["Ergodic LLC"])
        self.assertEqual([p.id for p in ergodic.papers], ["W4", "W5", "W1"])  # oldest first, by the new dates

    def test_dates_stand_when_crossref_is_down(self):
        paper = self._journal_paper("W1", "10.1103/bpms-63ml", date(2026, 9, 1))
        company = _company("Ergodic LLC")
        company.papers = [paper]
        ctx = Context(today=TODAY)
        with mock.patch.object(ra.http, "get_json", side_effect=OSError("down")):
            kept = ra.fix_dates(ctx, [company])
        self.assertEqual(kept, [company])
        self.assertEqual(paper.date, date(2026, 9, 1))
        self.assertEqual(len(ctx.warnings), 1)


class HistoryTest(unittest.TestCase):
    def _company(self, name: str, aliases: list[str] | None = None, legal: bool = False) -> ra.Company:
        return ra.Company(key=ra.company_key(name), name=name, aliases=aliases or [], legal=legal,
                          location=None, papers=[])

    def test_search_phrases(self):
        self.assertEqual(ra.search_phrases(self._company("Genesis AI")), ["Genesis AI"])
        self.assertEqual(ra.search_phrases(self._company("Gauss Fusion GmbH", legal=True)), ["Gauss Fusion"])
        # One-word names are searched with their legal form...
        self.assertEqual(ra.search_phrases(self._company("Turing Inc", legal=True)), ["Turing Inc"])
        # ...and bare as well when the authors themselves wrote it bare.
        self.assertEqual(ra.search_phrases(self._company("Sophelio LLC", ["Sophelio"], legal=True)),
                         ["Sophelio LLC", "Sophelio"])
        self.assertEqual(ra.search_phrases(self._company("Tsing-AI (Shanghai) Technology Co. Ltd", legal=True)),
                         ["Tsing-AI"])

    def test_history_matches_whole_phrases_only(self):
        works = [
            {"id": "https://openalex.org/W1", "publication_date": "2025-03-19",
             "authorships": [{"raw_affiliation_strings": ["Blue Laser Fusion Inc., Goleta, CA"]}]},
            {"id": "https://openalex.org/W2", "publication_date": "2024-01-02",
             "authorships": [{"raw_affiliation_strings": ["Blue Laser Institute", "Fusion Center"]}]},
            {"id": "https://openalex.org/W3", "publication_date": "2023-01-25",
             "authorships": [{"raw_affiliation_strings": ["Genesis AI Lab, Futong Technology, Chengdu, China"]}]},
        ]
        found = ra.history_matches(works, {"blue laser fusion": [["blue", "laser", "fusion"]],
                                           "genesis ai": [["genesis", "ai"]]})
        self.assertEqual(found["blue laser fusion"], [("2025-03-19", "W1")])
        # Loose on purpose: a namesake costs a "first paper" claim, it never makes one.
        self.assertEqual(found["genesis ai"], [("2023-01-25", "W3")])

    def test_verdicts(self):
        self.assertTrue(ra.History(checked=True, complete=True).first)
        self.assertFalse(ra.History(checked=False, complete=True).first)
        self.assertFalse(ra.History(checked=True, complete=True, earlier=[("2025-01-01", "W1")]).first)
        young = ra.History(checked=True, complete=True, earlier=[("2025-01-01", "W1")])
        self.assertIs(young.established(), False)
        old = ra.History(checked=True, old=["2012-05-01", "2015-06-01", "2019-07-01"])
        self.assertIs(old.established(), True)
        # Five works on one date eleven years before the company existed: mis-dated, not old.
        misdated = ra.History(checked=True, complete=True, old=["2012-02-24"])
        self.assertIs(misdated.established(), False)
        self.assertIsNone(ra.History(checked=True, earlier=[("2025-01-01", "W1")]).established())

    def test_ror_match_is_exact(self):
        items = [{"id": "https://ror.org/x", "established": 2009,
                  "names": [{"value": "Tokamak Energy (United Kingdom)"}],
                  "links": [{"type": "website", "value": "https://www.tokamakenergy.co.uk/"}]}]
        rec = ra.ror_match(items, "Tokamak Energy")
        self.assertIsNotNone(rec)
        self.assertFalse(ra.ror_is_young(rec, TODAY))
        self.assertEqual(ra.ror_website(rec), "https://www.tokamakenergy.co.uk/")
        self.assertIsNone(ra.ror_match(items, "Tokamak"))
        self.assertTrue(ra.ror_is_young({"established": 2024}, TODAY))
        self.assertFalse(ra.ror_is_young({"established": None}, TODAY))

    def test_budget_counts_planned_credits(self):
        b = ra.Budget(cap=25)
        self.assertTrue(b.take(10))
        self.assertTrue(b.take(10))
        self.assertFalse(b.take(10))
        self.assertEqual(b.spent, 20)


class PeopleTest(unittest.TestCase):
    def setUp(self):
        self.profiles = {a["id"].rsplit("/", 1)[-1]: a for a in _openalex()["author_singletons"]["results"]}

    def test_fragment_profiles_give_no_facts(self):
        # "Hao Su", minted 2026-09-25 with 2 works and h-index 0 (source card, gotcha 10).
        self.assertEqual(ra.author_facts(self.profiles["A5152653211"], TODAY, {"Engineering"}), ({}, []))
        self.assertEqual(ra.author_facts(None, TODAY, {"Engineering"}), ({}, []))

    def test_profile_without_a_matching_record_gives_no_facts(self):
        # The saved profile carries no topics, so nothing ties it to the paper's field.
        self.assertEqual(ra.author_facts(self.profiles["A5010354503"], TODAY, {"Engineering"}), ({}, []))
        other_field = copy.deepcopy(self.profiles["A5010354503"])
        other_field["topics"] = [{"count": 20, "field": {"display_name": "Medicine"}},
                                 {"count": 3, "field": {"display_name": "Computer Science"}}]
        self.assertEqual(ra.author_facts(other_field, TODAY, {"Engineering"}), ({}, []))

    def test_established_profile_gives_h_index_and_previous_homes(self):
        qiao = copy.deepcopy(self.profiles["A5010354503"])
        qiao["topics"] = [{"count": 9, "field": {"display_name": "Engineering"}},
                          {"count": 8, "field": {"display_name": "Computer Science"}}]
        facts, prev = ra.author_facts(qiao, TODAY, {"Engineering"})
        self.assertEqual(facts, {"h_index": 10, "cited_by_count": 364, "works_count": 37})
        self.assertEqual(prev[0], "University of Maryland, College Park")
        # Two publication years are not enough: this is OpenAlex mis-reading a UMD centre.
        self.assertNotIn("Machine Science", prev)

    def test_same_name_under_two_ids_is_one_person(self):
        self.assertEqual(ra._name_key("C. Day"), ra._name_key("Day C."))

    def test_two_careers_under_one_name_give_no_facts(self):
        # "Ziming Ding": a Zhejiang drone author merged with a Karlsruhe battery chemist.
        merged = [{"institution": {"display_name": "Karlsruhe Institute of Technology", "country_code": "DE"},
                   "years": [2026, 2025, 2024, 2023, 2022, 2021]},
                  {"institution": {"display_name": "Zhejiang University", "country_code": "CN"},
                   "years": [2026, 2025, 2022, 2021]}]
        self.assertTrue(ra.two_careers(merged))
        moved = [{"institution": {"display_name": "University of British Columbia", "country_code": "CA"},
                  "years": [2015, 2016, 2018]},
                 {"institution": {"display_name": "University of Pennsylvania", "country_code": "US"},
                  "years": [2018, 2019, 2020, 2021]}]
        self.assertFalse(ra.two_careers(moved))
        profile = {"works_count": 43, "summary_stats": {"h_index": 17}, "cited_by_count": 1116,
                   "created_date": "2020-11-23", "affiliations": merged,
                   "topics": [{"count": 30, "field": {"display_name": "Engineering"}}]}
        self.assertEqual(ra.author_facts(profile, TODAY, {"Engineering"}), ({}, []))
        profile["affiliations"] = merged[1:]
        facts, prev = ra.author_facts(profile, TODAY, {"Engineering"})
        self.assertEqual((facts["h_index"], prev), (17, ["Zhejiang University"]))

    def test_names_agree(self):
        self.assertTrue(ra.names_agree("J. Gaffney", "Jim Gaffney"))
        self.assertTrue(ra.names_agree("Vien Ngo", "Vien Anh Ngo"))
        self.assertTrue(ra.names_agree("Yi-Ling Qiao", "Yi-Ling Qiao"))
        self.assertFalse(ra.names_agree("Yi Zhu", "Zhu Yi"))
        self.assertFalse(ra.names_agree("Wei Wang", "Weiguo Wang"))
        self.assertFalse(ra.names_agree("Jiyul Lee", "Jinseok Lee"))
        self.assertFalse(ra.names_agree("Madonna", "Madonna"))

    def _team(self, mentions: list[ra.Mention]) -> ra.Company:
        paper = ra.Paper(id="W1", title="A robot paper", date=date(2026, 9, 20), url="https://doi.org/10.1000/x",
                         fit=0.8, text="robot", n_authors=len(mentions), topic=None, topic_field="Engineering")
        company = _company("Acme Robotics Inc")
        paper.mentions[company.key] = mentions
        company.papers = [paper]
        return company

    def _profile(self, name: str, homes: list[str]) -> dict:
        return {"display_name": name, "works_count": 40, "cited_by_count": 900, "created_date": "2019-01-01",
                "summary_stats": {"h_index": 21},
                "affiliations": [{"institution": {"display_name": h, "country_code": "US"},
                                  "years": [2024, 2023, 2022]} for h in homes],
                "topics": [{"count": 30, "field": {"display_name": "Engineering"}}]}

    def test_a_profile_is_used_only_when_the_paper_ties_it_to_the_author(self):
        mentions = [
            # Tied by a co-affiliation the paper shows and the profile lists.
            ra.Mention("A1", "A. Lee", "last", False, ["Carnegie Mellon University"]),
            # Tied by the ORCID the publisher printed.
            ra.Mention("A2", "Bo Li", "first", True, [], orcid_match=True),
            # Nothing ties this profile to the author: it may be a namesake's.
            ra.Mention("A3", "Peter Stadler", "middle", True, []),
            # Tied, but the profile carries another family name.
            ra.Mention("A4", "Yi Zhu", "middle", True, [], orcid_match=True),
            ra.Mention("name:c wu", "C. Wu", "middle", True, []),
        ]
        profiles = {"A1": self._profile("Ann Lee", ["Carnegie Mellon University", "Stanford University"]),
                    "A2": self._profile("Bo Li", ["University of Michigan"]),
                    "A3": self._profile("Peter F. Stadler", ["Leipzig University"]),
                    "A4": self._profile("Zhu Yi", ["Tsinghua University"])}
        people, team = ra.build_people(self._team(mentions), profiles, TODAY)
        by_name = {p.name: p for p in people}
        self.assertEqual(sorted(by_name), ["Ann Lee", "Bo Li", "C. Wu", "Peter Stadler", "Yi Zhu"])
        ann = by_name["Ann Lee"]  # printed "A. Lee": the profile spells the name out, and the profile is hers
        self.assertEqual(ann.facts["h_index"], 21)
        self.assertEqual(ann.affiliations, ["Acme Robotics Inc", "Carnegie Mellon University", "Stanford University"])
        self.assertEqual(ann.links, {"openalex": "https://openalex.org/A1"})
        self.assertEqual(by_name["Bo Li"].affiliations, ["Acme Robotics Inc", "University of Michigan"])
        for name in ["Peter Stadler", "Yi Zhu", "C. Wu"]:
            person = by_name[name]
            self.assertEqual((person.facts, person.links, person.openalex_id), ({}, {}, None), name)
            self.assertEqual(person.affiliations, ["Acme Robotics Inc"], name)
        self.assertEqual(team["company_authors"], 5)
        self.assertEqual(team["max_author_h_index"], 21)
        # A trusted profile never shortens the printed name or adds a middle initial to it.
        for printed, on_profile in [("An Thai Le", "An T. Le"), ("Jesse Leitner", "Jesse A. Leitner")]:
            mentions = [ra.Mention("A1", printed, "last", False, ["Carnegie Mellon University"])]
            profiles = {"A1": self._profile(on_profile, ["Carnegie Mellon University"])}
            people, _ = ra.build_people(self._team(mentions), profiles, TODAY)
            self.assertEqual((people[0].name, people[0].facts["h_index"]), (printed, 21))

    def test_spellings_of_one_person_are_counted_once(self):
        mentions = [ra.Mention("A1", "Albrecht Herrmann", "middle", True, []),
                    ra.Mention("A2", "A. Herrmann", "middle", True, []),
                    ra.Mention("A3", "Vien Ngo", "last", True, []),
                    ra.Mention("A4", "Vien Anh Ngo", "last", False, ["VinUniversity"]),
                    ra.Mention("A5", "Jiyul Lee", "first", True, []),
                    ra.Mention("A6", "Jinseok Lee", "middle", True, [])]
        people, team = ra.build_people(self._team(mentions), {}, TODAY)
        self.assertEqual(sorted(p.name for p in people), ["Albrecht Herrmann", "Jinseok Lee", "Jiyul Lee", "Vien Anh Ngo"])
        self.assertEqual(team["company_authors"], 4)
        ngo = next(p for p in people if p.name == "Vien Anh Ngo")
        self.assertEqual(ngo.affiliations, ["Acme Robotics Inc", "VinUniversity"])


class SignalTest(unittest.TestCase):
    def setUp(self):
        self.ctx = Context(today=TODAY)
        self.genesis = next(c for c in ra.group_companies(_papers(), SINCE, TODAY) if c.key == "genesis ai")

    def test_first_paper_signal(self):
        hist = ra.History(checked=True, complete=True)
        signals = ra.company_signals(self.genesis, hist, False, {}, self.ctx)
        self.assertEqual([s.kind for s in signals], ["company_first_paper"])
        s = signals[0]
        s.validate()
        self.assertEqual(s.entity.name, "Genesis AI")
        self.assertEqual(s.entity.kind, "company")
        self.assertIsNone(s.entity.domain)  # the source gives none, so none is claimed
        self.assertEqual(s.url, "https://arxiv.org/abs/2609.28766")
        self.assertEqual(s.occurred_at, "2026-09-23")
        self.assertEqual(s.title, "First indexed paper under the company name, with all 13 authors listing it")
        self.assertEqual(s.value, 13)
        self.assertEqual(s.metrics["company_authors"], 13)
        self.assertEqual(s.metrics["dual_affiliation_authors"], 2)
        self.assertEqual(s.metrics["company_only_authors"], 11)
        self.assertEqual(s.metrics["in_ror"], 0)
        self.assertGreaterEqual(s.strength, 0.6)
        self.assertLessEqual(s.strength, 1.0)
        self.assertLess(len(s.title), 110)
        self.assertTrue(s.text.startswith("TAPESIM: Efficient Simulation of Adhesive Tape Dispensing"))
        self.assertTrue(s.text.endswith(" OpenAlex topic: Robot Manipulation and Learning."))
        self.assertEqual(len(s.people), ra.MAX_PEOPLE)
        minchen = next(p for p in s.people if p.name == "Minchen Li")
        self.assertEqual(minchen.affiliations, ["Genesis AI", "Carnegie Mellon University"])
        self.assertEqual(minchen.role, "Last author")
        # No profile supplied: no h-index is invented and no profile is linked.
        self.assertEqual(minchen.facts, {})
        self.assertEqual(minchen.links, {})
        self.assertIsNone(minchen.openalex_id)

    def test_first_paper_titles_say_only_what_was_checked(self):
        hist = ra.History(checked=True, complete=True)
        first = self.genesis.papers[0]
        # Three of the thirteen authors borne out by the page.
        first.mentions["genesis ai"] = first.mentions["genesis ai"][:3]
        s = ra.company_signals(self.genesis, hist, False, {}, self.ctx)[0]
        self.assertEqual(s.title, "First indexed paper under the company name, with at least 3 of 13 authors listing it")
        self.assertEqual((s.value, s.unit), (3, "company authors"))
        self.assertEqual(len(s.people), 3)
        solid = s.strength
        # Nobody borne out: the paper still counts, nobody is named, and it is routine.
        first.mentions["genesis ai"] = []
        first.unverified.add("genesis ai")
        s = ra.company_signals(self.genesis, hist, False, {}, self.ctx)[0]
        self.assertEqual(s.title, "First indexed paper under the company name, a 13-author paper")
        self.assertEqual((s.value, s.unit), (13, "authors on paper"))
        self.assertEqual(s.people, [])
        self.assertEqual(s.metrics["papers_authors_unchecked"], 1)
        self.assertEqual(s.metrics["company_authors"], 0)
        self.assertGreaterEqual(s.strength, 0.15)
        self.assertLess(s.strength, 0.3)
        self.assertLess(s.strength, solid)

    def test_a_first_paper_outranks_a_repeat_paper(self):
        first = ra.company_signals(self.genesis, ra.History(checked=True, complete=True), False, {}, self.ctx)[0]
        again = ra.company_signals(
            self.genesis, ra.History(checked=True, complete=True, earlier=[("2026-03-01", "W1")]), False, {}, self.ctx)[0]
        self.assertEqual((first.kind, again.kind), ("company_first_paper", "company_papers"))
        self.assertGreater(first.strength, again.strength + 0.3)

    def test_series_has_one_point_per_day(self):
        second, third = copy.deepcopy(self.genesis.papers[0]), copy.deepcopy(self.genesis.papers[0])
        second.id, second.title = "W2", "Another paper on robot manipulation"
        third.id, third.title, third.date = "W3", "A third paper on robot manipulation", date(2026, 9, 29)
        self.genesis.papers += [second, third]
        hist = ra.History(checked=True, complete=True, earlier=[("2026-03-01", "W1")])
        s = ra.company_signals(self.genesis, hist, False, {}, self.ctx)[0]
        self.assertEqual(s.title, "At least 3 papers and 13 authors list the company as an affiliation in the last 120 days")
        self.assertEqual(s.series, [{"t": "2026-09-23", "v": 2}, {"t": "2026-09-29", "v": 3}])
        self.assertEqual(s.occurred_at, "2026-09-29")

    def test_the_paper_count_is_one_rolling_row_per_company(self):
        hist = ra.History(checked=True, complete=True, earlier=[("2026-03-01", "W1")])
        before = ra.company_signals(self.genesis, hist, False, {}, self.ctx)[0]
        self.assertEqual(before.kind, "company_papers")
        self.assertIs(before.metrics["rolling"], True)
        newer = copy.deepcopy(self.genesis.papers[0])
        newer.id, newer.title, newer.date = "W2", "A newer paper on robot manipulation", date(2026, 9, 29)
        newer.url = "https://arxiv.org/abs/2609.99999"
        self.genesis.papers.append(newer)
        after = ra.company_signals(self.genesis, hist, False, {}, self.ctx)[0]
        # The count is dated and linked by its newest paper, so both move when one appears...
        self.assertEqual((before.value, before.occurred_at, before.url), (1, "2026-09-23", "https://arxiv.org/abs/2609.28766"))
        self.assertEqual((after.value, after.occurred_at, after.url), (2, "2026-09-29", "https://arxiv.org/abs/2609.99999"))
        # ...and the stored row is refreshed rather than joined by a second one.
        self.assertEqual(fingerprint(before), fingerprint(after))
        # A first paper is a dated event, not a rolling count.
        both = ra.company_signals(self.genesis, ra.History(checked=True, complete=True), False, {}, self.ctx)
        self.assertEqual([s.kind for s in both], ["company_first_paper", "company_papers"])
        self.assertNotIn("rolling", both[0].metrics)
        self.assertIs(both[1].metrics["rolling"], True)
        self.assertNotEqual(fingerprint(both[0]), fingerprint(both[1]))

    def test_repeat_paper_signal_for_a_young_company(self):
        hist = ra.History(checked=True, complete=True, earlier=[("2026-03-01", "W1"), ("2026-04-20", "W2")])
        signals = ra.company_signals(self.genesis, hist, False, {}, self.ctx)
        self.assertEqual([s.kind for s in signals], ["company_papers"])
        s = signals[0]
        s.validate()
        self.assertEqual(s.title, "At least 1 paper and 13 authors list the company as an affiliation in the last 120 days")
        self.assertEqual(s.value, 1)
        self.assertEqual(s.series, [])
        # Nobody borne out on the page: the paper is still counted, without an author count.
        self.genesis.papers[0].mentions["genesis ai"] = []
        s = ra.company_signals(self.genesis, hist, False, {}, self.ctx)[0]
        self.assertEqual(s.title, "At least 1 paper lists the company as an affiliation in the last 120 days")
        self.assertEqual(s.people, [])
        self.assertGreaterEqual(s.strength, 0.15)
        self.assertEqual(s.metrics["earlier_papers_same_name"], 2)
        self.assertLess(s.strength, 0.4)

    def test_nothing_is_emitted_without_a_verdict(self):
        unchecked = ra.History()
        self.assertEqual(ra.company_signals(self.genesis, unchecked, False, {}, self.ctx), [])
        cut_short = ra.History(checked=True, earlier=[("2025-01-01", "W1")])
        self.assertEqual(ra.company_signals(self.genesis, cut_short, False, {}, self.ctx), [])
        established = ra.History(checked=True, complete=True, earlier=[("2012-05-01", "W1")],
                                 old=["2012-05-01", "2015-06-01", "2019-07-01"])
        self.assertEqual(ra.company_signals(self.genesis, established, False, {}, self.ctx), [])

    def test_ror_record_supplies_domain_or_drops_the_company(self):
        hist = ra.History(checked=True, complete=True)
        young = {"id": "https://ror.org/abc", "established": 2024,
                 "links": [{"type": "website", "value": "https://genesis.example"}],
                 "locations": [{"geonames_details": {"name": "San Carlos", "country_name": "United States"}}]}
        # A name match with no address to compare could be a namesake: no domain is taken.
        s = ra.company_signals(self.genesis, hist, young, {}, self.ctx)[0]
        self.assertIsNone(s.entity.domain)
        self.assertEqual(s.metrics["in_ror"], 1)
        self.genesis.location = "San Carlos, USA"
        s = ra.company_signals(self.genesis, hist, young, {}, self.ctx)[0]
        self.assertEqual(s.entity.domain, "genesis.example")
        self.assertEqual(s.entity.founded, "2024")
        self.assertEqual(s.entity.links["ror"], "https://ror.org/abc")
        self.genesis.location = "Chengdu, China"
        self.assertIsNone(ra.company_signals(self.genesis, hist, young, {}, self.ctx)[0].entity.domain)
        old = {"id": "https://ror.org/abc", "established": 2009, "links": []}
        self.assertEqual(ra.company_signals(self.genesis, hist, old, {}, self.ctx), [])


class HuggingFaceTest(unittest.TestCase):
    def test_company_org_on_a_robotics_paper(self):
        signals = [s for s in (ra.hf_signal(it, SINCE, TODAY) for it in _hf_items()) if s]
        self.assertEqual([s.entity.name for s in signals], ["Knowin AI"])
        s = signals[0]
        s.validate()
        self.assertEqual(s.kind, "hf_paper_with_org")
        self.assertEqual(s.url, "https://huggingface.co/papers/2609.36012")
        self.assertEqual(s.occurred_at, "2026-09-28")
        self.assertEqual(s.value, 358)
        self.assertEqual(s.metrics, {"hf_upvotes": 358, "hf_comments": 2, "paper_repo_stars": 14})
        self.assertEqual(s.title, "Paper reached 358 upvotes on Hugging Face daily papers: "
                                  "In-Context Learning for Robots: Methods and…")
        self.assertLess(len(s.title), 110)
        self.assertEqual(s.entity.links["huggingface"], "https://huggingface.co/Knowin")
        # The repo belongs to a personal account, so the company gets no GitHub login and no links from it.
        self.assertIsNone(s.entity.github)
        self.assertEqual(s.entity.links, {"huggingface": "https://huggingface.co/Knowin",
                                          "paper": "https://arxiv.org/abs/2609.36012"})
        self.assertEqual(s.people, [])

    def test_a_repo_under_the_org_name_gives_the_login(self):
        item = copy.deepcopy(next(it for it in _hf_items()
                                  if (it.get("organization") or {}).get("fullname") == "Knowin AI"))
        item["paper"]["githubRepo"] = "https://github.com/Knowin/robot-icl"
        item["paper"]["projectPage"] = "https://knowin.example/icl"
        s = ra.hf_signal(item, SINCE, TODAY)
        self.assertEqual(s.entity.github, "Knowin")  # a login, not a URL
        self.assertEqual(s.entity.links["repo"], "https://github.com/Knowin/robot-icl")
        self.assertEqual(s.entity.links["project_page"], "https://knowin.example/icl")

    def test_universities_incumbents_and_orgless_papers_are_skipped(self):
        by_org = {(it.get("organization") or {}).get("fullname"): ra.hf_signal(it, SINCE, TODAY) for it in _hf_items()}
        for name in ["NVIDIA", "DAMO Academy", "The Hong Kong University of Science and Technology",
                     "Harbin Institute of Techonology, Shenzhen", "Yandex Research", None]:
            self.assertIn(name, by_org)
            self.assertIsNone(by_org[name], name)

    def test_one_borrowed_phrase_does_not_make_a_robotics_paper(self):
        item = copy.deepcopy(next(it for it in _hf_items()
                                  if (it.get("organization") or {}).get("fullname") == "Knowin AI"))
        # A paper about economies. "World model" is not a thesis term, so the one phrase it
        # borrows from robotics stands alone and the paper sits under the shared gate.
        item["paper"]["title"] = "From Economic Agents to Agentic Economies: A Systems Blueprint for Economic World Models"
        item["paper"]["summary"] = ("Economic World Models are generative economic models. We organize systems into a "
                                    "capability ladder, up to sim-to-real economic twins aligned with real observations.")
        seen = classify(f"{item['paper']['title']}. {item['paper']['summary']}")
        self.assertEqual(seen["terms"], ["sim-to-real"])
        self.assertLess(seen["fit"], 0.3)
        self.assertIsNone(ra.hf_signal(item, SINCE, TODAY))
        # A word from another sector lifts it over the shared gate; this lens still wants
        # more than one robotics or autonomy word, having no affiliations to lean on.
        item["paper"]["summary"] += " The twins forecast energy demand."
        self.assertGreaterEqual(classify(f"{item['paper']['title']}. {item['paper']['summary']}")["fit"], 0.3)
        self.assertIsNone(ra.hf_signal(item, SINCE, TODAY))
        # A robot's world model is a robotics paper by its other words.
        item["paper"]["title"] = "A World Model for Robot Manipulation"
        item["paper"]["summary"] = "We train a world model on robot manipulation data and test it on a real gripper."
        self.assertIsNotNone(ra.hf_signal(item, SINCE, TODAY))

    def test_a_university_called_tech_is_not_a_company(self):
        item = copy.deepcopy(next(it for it in _hf_items()
                                  if (it.get("organization") or {}).get("fullname") == "Knowin AI"))
        self.assertIsNotNone(ra.hf_signal(item, SINCE, TODAY))
        item["organization"]["fullname"] = "Virginia Tech"
        self.assertIsNone(ra.hf_signal(item, SINCE, TODAY))

    def test_window(self):
        item = next(it for it in _hf_items() if (it.get("organization") or {}).get("fullname") == "Knowin AI")
        self.assertIsNone(ra.hf_signal(item, date(2026, 9, 29), TODAY))

    def test_the_date_is_the_papers_own(self):
        item = copy.deepcopy(next(it for it in _hf_items()
                                  if (it.get("organization") or {}).get("fullname") == "Knowin AI"))
        # The listing's own timestamp is 20:00 UTC the evening before: never used.
        self.assertTrue(item["publishedAt"].startswith("2026-09-27T20"))
        self.assertEqual(ra.hf_signal(item, SINCE, TODAY).occurred_at, "2026-09-28")
        del item["paper"]["publishedAt"]
        self.assertIsNone(ra.hf_signal(item, SINCE, TODAY))

    def test_iso_weeks_newest_first(self):
        weeks = ra._iso_weeks(date(2026, 9, 14), date(2026, 10, 1))
        self.assertEqual(weeks, ["2026-W40", "2026-W39", "2026-W38"])


class NoNetworkTest(unittest.TestCase):
    def test_collect_survives_a_dead_network(self):
        ctx = Context(today=TODAY, limit=5)
        with mock.patch.object(ra.http, "get_json", side_effect=OSError("network down")), \
                mock.patch.object(ra.http, "get", side_effect=OSError("network down")):
            signals = list(ra.collect(ctx))
        self.assertEqual(signals, [])
        self.assertTrue(ctx.warnings)


if __name__ == "__main__":
    unittest.main()
