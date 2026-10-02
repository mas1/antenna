"""Offline tests for the yc_directory collector: parsing against saved fixtures.

No network. The end-to-end test swaps antenna.http for a stub that serves the
fixtures and fails the test if any other URL is asked for.
"""

from __future__ import annotations

import html
import json
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from antenna import http
from antenna.collectors import yc_directory as yc
from antenna.collectors.base import Context

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "launches"
TODAY = date(2026, 10, 1)


def load(name: str) -> dict:
    return json.loads((FIX / name).read_text())


COMPANIES = load("yc_oss_recent_batches_thesis.json")["companies"]
CHANGES = load("yc_oss_changes_latest.json")
LAUNCHES = load("yc_launches_query_robot.json")
PAGE = load("yc_company_page_noril1.json")
BY_NAME = {c["name"]: c for c in COMPANIES}
ADDED = frozenset(a["id"] for a in CHANGES["added"])


def record(**over) -> dict:
    """A directory record with every field the collector reads, for edge cases."""
    base = {
        "id": 1, "name": "Testco", "slug": "testco", "former_names": [], "website": "https://testco.example",
        "all_locations": "", "long_description": "", "one_liner": "", "team_size": 2,
        "industry": "B2B", "subindustry": "B2B", "launched_at": 1790785673, "tags": [],
        "batch": "Fall 2026", "status": "Active", "stage": "Early", "isHiring": False,
        "url": "https://www.ycombinator.com/companies/testco",
    }
    base.update(over)
    return base


def page_html(company: dict) -> str:
    """Wrap a company dict the way ycombinator.com embeds it."""
    blob = json.dumps({"component": "CompaniesShowPage", "props": {"company": company, "launches": []}})
    return f'<html><body><div id="x" data-page="{html.escape(blob, quote=True)}"></div></body></html>'


class Batches(unittest.TestCase):
    META = {"batches": {
        "summer-2026": {"name": "Summer 2026", "count": 231, "api": "u/summer-2026.json"},
        "winter-2026": {"name": "Winter 2026", "count": 198, "api": "u/winter-2026.json"},
        "spring-2026": {"name": "Spring 2026", "count": 194, "api": "u/spring-2026.json"},
        "fall-2026": {"name": "Fall 2026", "count": 108, "api": "u/fall-2026.json"},
        "winter-2027": {"name": "Winter 2027", "count": 1, "api": "u/winter-2027.json"},
        "summer-2027": {"name": "Summer 2027", "count": 1, "api": "u/summer-2027.json"},
        "unspecified": {"name": "Unspecified", "count": 1, "api": "u/unspecified.json"},
        "fall-2025": {"name": "Fall 2025", "count": 146, "api": "u/fall-2025.json"},
    }}

    def test_batch_key_orders_seasons(self):
        self.assertEqual(yc.batch_key("Summer 2026"), (2026, 2))
        self.assertLess(yc.batch_key("Winter 2026"), yc.batch_key("Spring 2026"))
        self.assertLess(yc.batch_key("Fall 2025"), yc.batch_key("Winter 2026"))
        self.assertIsNone(yc.batch_key("Unspecified"))
        self.assertIsNone(yc.batch_key(None))

    def test_three_newest_skip_placeholders(self):
        names = [b["name"] for b in yc.recent_batches(self.META, TODAY)]
        self.assertEqual(names, ["Fall 2026", "Summer 2026", "Spring 2026"])

    def test_small_batch_counts_once_its_season_began(self):
        names = [b["name"] for b in yc.recent_batches(self.META, date(2027, 1, 5))]
        self.assertEqual(names, ["Winter 2027", "Fall 2026", "Summer 2026"])


class SmallParsers(unittest.TestCase):
    def test_team_size_only_positive_counts(self):
        for unknown in (None, 0, -1, "3", True):
            self.assertIsNone(yc.team_size_of(unknown))
        self.assertEqual(yc.team_size_of(3), 3)

    def test_parse_ts(self):
        self.assertEqual(yc.iso(yc.parse_ts(1790785673)), "2026-09-30T16:27:53Z")
        self.assertEqual(yc.iso(yc.parse_ts("2026-09-28T07:00:00.116Z")), "2026-09-28T07:00:00Z")
        for bad in (None, "", 0, "not a date"):
            self.assertIsNone(yc.parse_ts(bad))

    def test_vote_ranks(self):
        votes = sorted([1, 5, 5, 10, 100])
        self.assertEqual(yc.percentile_of(100, votes), 80)
        self.assertEqual(yc.percentile_of(1, votes), 0)
        self.assertEqual(yc.top_share(100, votes), 20)
        self.assertEqual(yc.top_share(5, votes), 80)
        self.assertIsNone(yc.top_share(5, []))
        self.assertIsNone(yc.percentile_of(5, []))


class Thesis(unittest.TestCase):
    def test_fixture_companies(self):
        on = {"Hundred": "subindustry", "Pendulum Robotics": "subindustry",
              "Streamline Systems": "subindustry", "Micora": "subindustry",
              "Aerogen Systems": "tags+text", "DeepReach Inc.": "tags+text"}
        for name, basis in on.items():
            v = yc.thesis_verdict(BY_NAME[name])
            self.assertTrue(v["on"], name)
            self.assertEqual(v["basis"], basis, name)
            self.assertGreaterEqual(v["fit"], yc.MIN_FIT, name)

    def test_fixture_rejections(self):
        # A consumer bracelet with a Hardware tag, a fund tagged Semiconductors,
        # a care company tagged Robotics, an agent company filed under Industrials,
        # an operations-research lab filed under supply chain and tagged Manufacturing.
        for name in ("Vexo", "Spectre Intelligence", "Rhem Labs", "Lark", "OpEra"):
            self.assertFalse(yc.thesis_verdict(BY_NAME[name])["on"], name)

    def test_traders_and_brokers_are_not_manufacturers(self):
        donkey = record(one_liner="The AI-native trading company: factory prices, delivered, one number",
                        long_description="One company responsible between the factory floor and your door.",
                        tags=["Logistics", "Manufacturing", "Supply Chain", "AI"],
                        subindustry="B2B -> Supply Chain and Logistics")
        self.assertFalse(yc.thesis_verdict(donkey)["on"])
        broker = record(one_liner="AI Native Sourcing Broker For Overseas Manufacturing")
        self.assertFalse(yc.thesis_verdict(broker)["on"])
        # A hardware company in the same subindustry stays: YC marks it Hard Tech.
        robots = record(one_liner="Autonomous robots that unload trucks in the warehouse",
                        tags=["Hard Tech", "Robotics", "Logistics"],
                        subindustry="B2B -> Supply Chain and Logistics")
        self.assertEqual(yc.thesis_verdict(robots)["basis"], "tags+text")

    def test_autopilot_for_is_a_metaphor_unless_hardware(self):
        airline = record(one_liner="Autopilot for Flight Operations", industry="Industrials",
                         subindustry="Industrials -> Aviation and Space",
                         tags=["Artificial Intelligence", "Airlines", "Aerospace"],
                         long_description="One AI system for airline flight operations.")
        v = yc.thesis_verdict(airline)
        self.assertFalse(v["on"])
        # With the metaphor gone its own words say nothing on thesis. What is
        # left is YC's labels ("Industrials", "Aerospace"), which the shared
        # classifier now reads as two thesis words; they must not carry it.
        self.assertEqual(v["own_fit"], 0.0)
        self.assertNotEqual(v["sector"], "autonomy")
        avionics = record(one_liner="Autopilot for light aircraft", industry="Industrials",
                          subindustry="Industrials -> Aviation and Space",
                          tags=["Hard Tech", "Aerospace"])
        self.assertTrue(yc.thesis_verdict(avionics)["on"])
        self.assertFalse(yc.thesis_verdict(record(one_liner="Autopilot for sales teams"))["on"])
        self.assertFalse(yc.thesis_verdict(record(one_liner="Lead enrichment for sales teams"))["on"])
        self.assertFalse(yc.thesis_verdict(record(one_liner="The secret weapon for recruiters"))["on"])

    def test_yc_labels_alone_do_not_carry_a_software_record(self):
        energy = dict(industry="Industrials", subindustry="Industrials -> Energy")
        # An engineering-design firm: own words name nothing on thesis, tagged AI.
        firm = record(one_liner="AI-Native Engineering Firm designing Data Centers",
                      long_description="The AI-native engineering firm automating engineering design.",
                      tags=["Construction", "Design", "Energy", "AI"], **energy)
        v = yc.thesis_verdict(firm)
        self.assertGreaterEqual(v["fit"], yc.MIN_FIT)  # "Industrials" and "Energy" alone reach the bar
        self.assertEqual(v["own_fit"], 0.0)
        self.assertFalse(v["on"])
        # The same without a tag, when only the one-liner says AI.
        self.assertFalse(yc.thesis_verdict({**firm, "tags": []})["on"])
        # A hardware company with the same thin evidence is taken at YC's word...
        coolers = record(one_liner="Hyper-efficient coolers for data centers",
                         long_description="Builds cooling systems for data centers.", **energy)
        v = yc.thesis_verdict(coolers)
        self.assertEqual(v["basis"], "subindustry")
        self.assertEqual(v["sector"], "energy")
        self.assertLess(yc.batch_strength(v), 0.35)  # ...at the routine end
        # ...and so is a tagged one that YC does not present as software.
        self.assertEqual(yc.thesis_verdict(BY_NAME["Sinter"])["basis"], "subindustry")
        # Software whose own words do name the thesis stays, as does anything marked hardware.
        self.assertTrue(yc.thesis_verdict(BY_NAME["Invertix"])["on"])
        self.assertTrue(yc.thesis_verdict({**firm, "tags": [*firm["tags"], "Hard Tech"]})["on"])
        # Devices on transformers: "power infrastructure" plus YC's Energy filing.
        talos = yc.thesis_verdict(BY_NAME["Talos"])
        self.assertEqual((talos["basis"], talos["sector"]), ("subindustry", "energy"))
        # Nothing on thesis in the text at all: YC's subindustry is not enough.
        towers = record(one_liner="Fixing air traffic control with remote towers", industry="Industrials",
                        subindustry="Industrials -> Aviation and Space")
        self.assertFalse(yc.thesis_verdict(towers)["on"])

    def test_one_strong_own_word_is_enough_beside_yc_tags(self):
        # The classifier caps a lone ambiguous term below the gate until a
        # second source agrees. YC's sector tags are that second source.
        pods = record(one_liner="Mass-producing micro data centers", industry="Industrials",
                      subindustry="Industrials", tags=["Robotics", "Manufacturing", "AI"],
                      long_description="Our pods use diamond wafers in the cooling system.")
        v = yc.thesis_verdict(pods)
        self.assertLess(v["own_fit"], yc.MIN_FIT)  # one term, capped
        self.assertEqual(v["basis"], "tags+text")
        # One context word is still not enough (see test_tags_do_not_count_twice),
        # and without any tag the cap stands.
        self.assertFalse(yc.thesis_verdict({**pods, "tags": [], "subindustry": "B2B", "industry": "B2B"})["on"])

    def test_hard_tech_tag_without_a_sector_needs_strong_words(self):
        # A SaaS with a Hard Tech tag. The shared thesis no longer lists "world model" or
        # "strike", so these words now name nothing on thesis at all.
        freight = record(one_liner="World Model for Freight",
                         long_description="Predicts the wars, strikes and tariffs that break global shipping.",
                         tags=["Artificial Intelligence", "Hard Tech", "SaaS", "Supply Chain"],
                         subindustry="B2B -> Supply Chain and Logistics")
        v = yc.thesis_verdict(freight)
        self.assertEqual(v["own_fit"], 0.0)
        self.assertFalse(v["on"])
        # The rule still has work to do: two stray context words the thesis does list
        # ("grid", "supply chain") pass the gate and stop short of MIN_TEXT_ONLY_FIT.
        outages = {**freight, "one_liner": "Forecasting for Freight",
                   "long_description": "Predicts the wars, tariffs and grid outages that break supply chains."}
        v = yc.thesis_verdict(outages)
        self.assertGreaterEqual(v["fit"], yc.MIN_FIT)
        self.assertGreaterEqual(v["own_fit"], yc.MIN_FIT)
        self.assertLess(v["own_fit"], yc.MIN_TEXT_ONLY_FIT)
        self.assertFalse(v["on"])
        rovers = record(one_liner="Autonomous rovers that map underground utilities",
                        tags=["Hard Tech", "Computer Vision", "Construction"],
                        industry="Real Estate and Construction",
                        subindustry="Real Estate and Construction -> Construction")
        self.assertEqual(yc.thesis_verdict(rovers)["basis"], "tags+text")

    def test_tags_do_not_count_twice(self):
        # Filed under plain Industrials with sector tags, but its own words
        # say nothing on thesis and YC does not mark it as hardware.
        docs = record(one_liner="Legora for physical engineering firms", industry="Industrials",
                      subindustry="Industrials", tags=["Construction", "Energy", "Enterprise Software", "Industrial"],
                      long_description="Engineering firms build infrastructure faster, including energy installations.")
        v = yc.thesis_verdict(docs)
        self.assertGreaterEqual(v["fit"], yc.MIN_FIT)  # the tags alone reach the classifier bar
        self.assertLess(v["own_fit"], yc.MIN_FIT)
        self.assertFalse(v["on"])
        # The same record marked Hard Tech passes, as Dipole Labs does in the fixture.
        self.assertTrue(yc.thesis_verdict({**docs, "tags": [*docs["tags"], "Hard Tech"]})["on"])
        self.assertTrue(yc.thesis_verdict(BY_NAME["Dipole Labs"])["on"])
        vision = record(one_liner="AI that turns any camera to an autonomous worker",
                        tags=["Robotics", "Computer Vision", "Industrial"], subindustry="B2B -> Infrastructure")
        self.assertFalse(yc.thesis_verdict(vision)["on"])

    def test_software_metaphors_are_not_thesis(self):
        factory = record(one_liner="Software factory for product teams",
                         subindustry="B2B -> Engineering, Product and Design")
        self.assertFalse(yc.thesis_verdict(factory)["on"])
        cyber = record(one_liner="Autonomous AI defense system for cyber attack",
                       tags=["Security", "Cybersecurity", "AI"], subindustry="B2B -> Security")
        self.assertFalse(yc.thesis_verdict(cyber)["on"])
        rpa = record(one_liner="The AI BPO for financial services",
                     tags=["Robotic Process Automation", "Finance"])
        self.assertFalse(yc.thesis_verdict(rpa)["on"])
        # The phrase in a company's own words is blanked by the shared classifier.
        v = yc.thesis_verdict(record(one_liner="Robotic process automation for back offices"))
        self.assertEqual(v["fit"], 0.0)
        self.assertFalse(v["on"])

    def test_untagged_record_passes_on_its_one_liner(self):
        # An unmistakable term, or a strong term and a second one.
        for liner in ("Your robot chef", "CNC machining for hardware startups",
                      "AI operating system for mega-scale satellite networks",
                      "Batteries for grid-scale storage"):
            v = yc.thesis_verdict(record(one_liner=liner, subindustry="B2B -> Operations"))
            self.assertEqual(v["basis"], "one_liner", liner)
        # A lone everyday word has no second source on an untagged record.
        for liner in ("AI Employee for Manufacturing Operations",
                      "AI mobile app that helps factory technicians fix broken machines"):
            v = yc.thesis_verdict(record(one_liner=liner, subindustry="B2B -> Operations"))
            self.assertFalse(v["on"], liner)
            self.assertLess(v["fit"], yc.MIN_FIT, liner)
        # Nor does text alone count under an industry that is never the thesis...
        v = yc.thesis_verdict(record(one_liner="Robots for Drug Manufacturing",
                                     industry="Healthcare", subindustry="Healthcare -> Drug Discovery and Delivery"))
        self.assertGreaterEqual(v["fit"], yc.MIN_TEXT_ONLY_FIT)
        self.assertFalse(v["on"])
        # ...or where "defense" is a network's.
        v = yc.thesis_verdict(record(one_liner="Frontier AI Defenses for Social Engineering Attacks",
                                     subindustry="B2B -> Security"))
        self.assertGreaterEqual(v["fit"], yc.MIN_TEXT_ONLY_FIT)
        self.assertFalse(v["on"])

    def test_sector_tags_keep_yc_order(self):
        v = yc.thesis_verdict(BY_NAME["Pendulum Robotics"])
        self.assertEqual(v["sector_tags"], ["Robotics", "Drones", "Manufacturing", "Electronics"])
        self.assertTrue(v["hard"])

    def test_homonym_tag_left_out_of_text(self):
        text = yc.company_text(record(one_liner="x", tags=["Swarm AI", "Finance"]))
        self.assertNotIn("Swarm", text)
        self.assertIn("Finance", text)
        # "Robotic Process Automation" needs no local guard: the tag is stated as
        # YC wrote it and the shared classifier blanks the phrase.
        rpa = record(one_liner="x", tags=["Robotic Process Automation", "Finance"])
        self.assertIn("Robotic Process Automation", yc.company_text(rpa))
        self.assertEqual(yc.thesis_verdict(rpa)["fit"], 0.0)

    def test_radar_tag_counts_as_defense_like_the_word(self):
        # The shared thesis files "radar" under defense. A lone Radar tag outside
        # Industrials needs the company's own words to agree with it.
        modules = record(one_liner="Low-cost radar modules for perimeter monitoring", tags=["Radar", "IoT"])
        v = yc.thesis_verdict(modules)
        self.assertEqual((v["basis"], v["sector"]), ("tags+text", "defense"))
        self.assertFalse(yc.thesis_verdict({**modules, "one_liner": "Low-cost modules for perimeter monitoring"})["on"])


class Strength(unittest.TestCase):
    def test_launch_strength_anchors(self):
        self.assertEqual(yc.launch_strength(0), 0.15)
        self.assertEqual(yc.launch_strength(11), 0.40)   # measured median: solid
        self.assertEqual(yc.launch_strength(64), 0.62)   # measured p90: notable
        self.assertGreaterEqual(yc.launch_strength(374), 0.85)  # extreme outlier: rare
        self.assertLess(yc.launch_strength(5), 0.3)      # routine
        self.assertLess(yc.launch_strength(100000), 1.0)

    def test_first_launch_outranks_a_follow_up(self):
        for votes in (0, 6, 11, 64, 374, 5000):
            self.assertLess(yc.launch_strength(votes, repeat=True), yc.launch_strength(votes), votes)
            self.assertGreaterEqual(yc.launch_strength(votes, repeat=True), 0.0)

    def test_launch_strength_is_monotonic(self):
        prev = -1.0
        for v in range(0, 3000):
            s = yc.launch_strength(v)
            self.assertGreaterEqual(s, prev, v)
            self.assertTrue(0.0 <= s <= 1.0)
            prev = s

    def test_batch_strength_range_and_order(self):
        strengths = {}
        for c in COMPANIES:
            v = yc.thesis_verdict(c)
            if v["on"]:
                strengths[c["name"]] = yc.batch_strength(v)
                self.assertTrue(0.15 <= strengths[c["name"]] <= 0.64, c["name"])
        # Hardware under a thesis subindustry outranks a data business tagged Robotics.
        self.assertGreater(strengths["Pendulum Robotics"], strengths["DeepReach Inc."])
        self.assertGreaterEqual(strengths["Pendulum Robotics"], 0.6)
        self.assertLess(strengths["DeepReach Inc."], 0.45)
        self.assertNotIn("OpEra", strengths)
        one_liner = yc.thesis_verdict(record(one_liner="CNC machining for hardware startups"))
        self.assertEqual(one_liner["basis"], "one_liner")
        self.assertLess(yc.batch_strength(one_liner), 0.3)


class CompanyPage(unittest.TestCase):
    def test_parse_data_page_round_trip(self):
        props = yc.parse_company_page(page_html(PAGE["company"]))
        self.assertEqual(props["company"]["id"], 32553)
        self.assertEqual(props["company"]["founders"][0]["full_name"], "Antonio Sitong Li")

    def test_page_without_data_raises(self):
        with self.assertRaises(ValueError):
            yc.parse_company_page("<html><body>Just a moment...</body></html>")
        with self.assertRaises(ValueError):
            yc.parse_company_page('<div data-page="{&quot;props&quot;:{}}"></div>')

    def test_founders(self):
        people = yc.people_from_page(PAGE["company"])
        self.assertEqual(len(people), 1)
        p = people[0]
        self.assertEqual(p.name, "Antonio Sitong Li")
        self.assertEqual(p.role, "Founder/CTO")
        self.assertEqual(p.links["linkedin"], "https://www.linkedin.com/in/antonio-sitong-li/")
        self.assertEqual(p.links["twitter"], "https://x.com/AntonioSitongLi")
        # The bio is a paragraph, not a list of employers: it goes in facts["bio"].
        self.assertEqual(p.affiliations, [])
        self.assertTrue(p.facts["bio"].startswith("Antonio is the founder of Nori Robotics."))
        self.assertIn("Columbia", p.facts["bio"])
        self.assertNotIn("\n", p.facts["bio"])
        self.assertLessEqual(len(p.facts["bio"]), yc.BIO_CHARS)
        self.assertNotIn("X-Amz", json.dumps(p.__dict__))

    def test_inactive_and_nameless_founders_are_dropped(self):
        page = {"founders": [
            {"full_name": "Gone Person", "is_active": False, "title": "Founder"},
            {"full_name": "  ", "is_active": True},
            {"full_name": "Here Person", "is_active": True, "title": "", "founder_bio": "",
             "linkedin_url": "", "twitter_url": ""},
        ]}
        people = yc.people_from_page(page)
        self.assertEqual([p.name for p in people], ["Here Person"])
        self.assertIsNone(people[0].role)
        self.assertEqual(people[0].affiliations, [])
        self.assertEqual(people[0].facts, {})
        self.assertEqual(people[0].links, {})
        # A malformed founders list must not raise.
        self.assertEqual(yc.people_from_page({"founders": ["x", None, {"full_name": None}]}), [])

    def test_entity_hint_with_page(self):
        nori = record(id=32553, name="Nori", slug="noril1", website="http://norirobotics.com",
                      batch="Summer 2026", url="https://www.ycombinator.com/companies/noril1",
                      all_locations="San Francisco, CA, USA")
        e = yc.entity_hint(nori, PAGE["company"])
        self.assertEqual(e.domain, "norirobotics.com")
        self.assertEqual(e.founded, "2026")
        self.assertIsNone(e.github)  # the page's github_url is null
        self.assertEqual(e.links["yc"], "https://www.ycombinator.com/companies/noril1")
        self.assertEqual(e.links["yc_batch"], "Summer 2026")
        self.assertEqual(e.links["linkedin"], "https://www.linkedin.com/company/norirobotics/")
        self.assertNotIn("crunchbase", e.links)  # cb_url is empty on the page

    def test_github_login_only(self):
        self.assertEqual(yc._github_login("https://github.com/Hebbian-Robotics"), "Hebbian-Robotics")
        self.assertEqual(yc._github_login("https://github.com/acme/repo"), "acme")
        self.assertEqual(yc._github_login("https://github.com/orgs/acme/repositories"), "acme")
        self.assertEqual(yc._github_login("github.com/acme/"), "acme")
        for not_a_login in ("", None, "https://github.com/", "https://github.com/orgs",
                            "https://github.com/sponsors/acme", "https://github.com/apps/acme-bot",
                            "https://gitlab.com/acme", "https://acme.github.io"):
            self.assertIsNone(yc._github_login(not_a_login), not_a_login)

    def test_display_name_drops_legal_form_only(self):
        # The shared helper decides what a legal form is. "Company" and "Co" are part of
        # the name, and capitals are the company's own styling.
        for raw, shown in (("DeepReach Inc.", "DeepReach"), ("Vernius Systems, Inc.", "Vernius Systems"),
                           ("Isengard Industries Inc", "Isengard Industries"), ("HEVN, inc", "HEVN"),
                           ("Robocurve PBC", "Robocurve"), ("Earendil Robotics Ltd", "Earendil Robotics"),
                           ("The Agentic Data Co.", "The Agentic Data Co."), ("Hickory & Company", "Hickory & Company"),
                           ("Agency Tool Company", "Agency Tool Company"),
                           ("The Robot Learning Company", "The Robot Learning Company"),
                           ("IMPACT Drones", "IMPACT Drones"), ("NODA AI", "NODA AI"),
                           ("Dreamscale Labs", "Dreamscale Labs"), ("  Hundred ", "Hundred"), ("Inc", "Inc"),
                           ("X, Inc.", "X, Inc.")):
            self.assertEqual(yc.display_name(raw), shown)
        e = yc.entity_hint(record(name="Agency Tool Company"))
        self.assertEqual((e.name, e.aliases), ("Agency Tool Company", []))

    def test_aliases_keep_spellings_and_drop_pivots(self):
        # A pivot's old name is a merge key for somebody else's filings.
        self.assertEqual(yc.safe_aliases("Baud", ["Neuralyze", "Zener", "Front", "Zener", "Ponder", "Baud Labs"]),
                         ["Baud Labs"])
        self.assertEqual(yc.safe_aliases("Robocurve", ["Telos", "Robocurve PBC"]), ["Robocurve PBC"])
        self.assertEqual(yc.safe_aliases("Datoric", ["Flow AI", "Arzule"]), [])
        self.assertEqual(yc.safe_aliases("Tensr", ["Core", "Tensr", "Core"]), [])
        self.assertEqual(yc.safe_aliases("Hub", ["Hub", "Hub.xyz"]), [])
        self.assertEqual(yc.safe_aliases("Faraday", ["Faraday Cage", "Faraday", "Faraday Cage"]), ["Faraday Cage"])
        self.assertEqual(yc.safe_aliases("Orca Aerospace", ["Otter Aerospace Inc.", "Orca Aerospace Inc."]),
                         ["Orca Aerospace Inc."])
        # The name as YC writes it stays findable when the legal form was dropped.
        self.assertEqual(yc.safe_aliases("Applied Electrodynamics, Inc.", ["Applied Electrodynamics"]),
                         ["Applied Electrodynamics, Inc."])
        self.assertEqual(yc.safe_aliases("Hundred", None), [])

    def test_entity_hint_name_and_aliases(self):
        c = BY_NAME["Vernius Systems, Inc."]
        e = yc.entity_hint(c)
        self.assertEqual(e.name, "Vernius Systems")
        self.assertEqual(e.aliases, ["Vernius Systems, Inc."])
        self.assertEqual(e.domain, "vernius.systems")
        atoms = yc.entity_hint(BY_NAME["Moving Atoms"])
        self.assertEqual(atoms.aliases, [])  # Run Lobster, Demon AI, Avicenna Care, Agent Insurance
        s = yc.batch_signal(BY_NAME["Moving Atoms"], yc.thesis_verdict(BY_NAME["Moving Atoms"]))
        self.assertIn("Avicenna Care", s.metrics["yc_former_names"])  # kept, but not as a merge key


class BatchSignal(unittest.TestCase):
    def test_hundred(self):
        c = BY_NAME["Hundred"]
        s = yc.batch_signal(c, yc.thesis_verdict(c), added_ids=ADDED)
        s.validate()
        self.assertEqual((s.source, s.family, s.kind), ("yc_directory", "launch", "yc_batch"))
        self.assertEqual(s.entity.name, "Hundred")
        self.assertEqual(s.entity.domain, "hundred.systems")
        self.assertEqual(s.entity.one_liner, "100% automated inspection for consumer electronics parts")
        self.assertEqual(s.entity.location, "San Francisco, CA, USA")
        self.assertIsNone(s.entity.founded)  # only the company page states it
        self.assertEqual(s.title, "Listed in YC Fall 2026 under Manufacturing and Robotics, now a team of 3")
        self.assertEqual(s.occurred_at, "2026-09-30T16:27:53Z")
        self.assertEqual(s.url, "https://www.ycombinator.com/companies/hundred")
        self.assertEqual((s.value, s.unit), (3, "people"))
        self.assertEqual(s.metrics["yc_batch"], "Fall 2026")
        self.assertEqual(s.metrics["team_size"], 3)
        self.assertEqual(s.metrics["occurred_at_basis"], "launched_at")
        self.assertTrue(s.metrics["listed_in_changes_feed"])
        self.assertEqual(s.entity.links["yc_batch"], "Fall 2026")
        self.assertEqual(s.people, [])
        self.assertIn("Hard Tech", s.text)

    def test_not_in_changes_feed(self):
        c = BY_NAME["Micora"]
        s = yc.batch_signal(c, yc.thesis_verdict(c), added_ids=ADDED)
        self.assertFalse(s.metrics["listed_in_changes_feed"])
        self.assertEqual(s.title, "Listed in YC Fall 2026 under Energy, now a team of 4")

    def test_unknown_team_size_is_left_out(self):
        for name in ("Hilstart", "Viraj Aero"):  # team_size 0 and null in the directory
            c = BY_NAME[name]
            s = yc.batch_signal(c, yc.thesis_verdict(c))
            self.assertNotIn("team_size", s.metrics, name)
            self.assertIsNone(s.value)
            self.assertIsNone(s.unit)
            self.assertNotIn("team of", s.title)

    def test_empty_website_gives_no_domain(self):
        c = BY_NAME["Mantle"]
        self.assertIsNone(yc.batch_signal(c, yc.thesis_verdict(c)).entity.domain)

    def test_page_team_size_wins_and_founders_attach(self):
        c = record(id=32553, name="Nori", website="http://norirobotics.com", team_size=4,
                   industry="Industrials", subindustry="Industrials -> Manufacturing and Robotics",
                   tags=["Hard Tech", "Hardware", "Robotics"], batch="Summer 2026",
                   one_liner="The most capable American-made humanoid robot for $1,688",
                   url="https://www.ycombinator.com/companies/noril1")
        s = yc.batch_signal(c, yc.thesis_verdict(c), page_company=PAGE["company"])
        self.assertEqual(s.metrics["team_size"], 5)  # the page says 5
        self.assertEqual(s.title, "Listed in YC Summer 2026 under Manufacturing and Robotics, now a team of 5")
        self.assertEqual([p.name for p in s.people], ["Antonio Sitong Li"])
        self.assertEqual(s.metrics["founders_listed"], 1)

    def test_no_publish_time_no_signal(self):
        c = record(launched_at=None, industry="Industrials",
                   subindustry="Industrials -> Defense", one_liner="Counter-drone radar for the army")
        self.assertIsNone(yc.batch_signal(c, yc.thesis_verdict(c)))

    def test_titles_follow_the_rules(self):
        n = 0
        for c in COMPANIES:
            v = yc.thesis_verdict(c)
            if not v["on"]:
                continue
            t = yc.batch_signal(c, v).title
            n += 1
            self.assertLessEqual(len(t), 110, t)
            self.assertFalse(t.endswith("."), t)
            self.assertTrue(t.startswith("Listed in YC "), t)
            self.assertIn(c["batch"], t)
        self.assertGreater(n, 20)

    def test_long_tag_pair_falls_back_to_one_tag(self):
        c = record(industry="Industrials", subindustry="Industrials", team_size=12,
                   tags=["Edge Computing Semiconductors", "Food Service Robots & Machines"],
                   one_liner="Robots and chips for semiconductor manufacturing")
        t = yc.batch_title(c, yc.thesis_verdict(c), 12)
        self.assertEqual(t, "Listed in YC Fall 2026 tagged Edge Computing Semiconductors, now a team of 12")


class LaunchSignal(unittest.TestCase):
    HITS = {h["company"]["name"]: h for h in LAUNCHES["hits"]}
    VOTES = sorted(h["total_vote_count"] for h in LAUNCHES["hits"])

    def make(self, name: str, **kw):
        c = BY_NAME[name]
        return yc.launch_signal(self.HITS[name], c, yc.thesis_verdict(c), **kw)

    def test_invertix(self):
        s = self.make("Invertix")
        s.validate()
        self.assertEqual(s.kind, "yc_launch")
        self.assertEqual(s.entity.name, "Invertix")
        self.assertEqual(s.entity.domain, "invertix.ai")
        self.assertEqual(s.url, "https://www.ycombinator.com/launches/UC8-invertix-energy-superintelligence")
        self.assertEqual(s.occurred_at, "2026-09-28T07:00:00Z")
        self.assertEqual((s.value, s.unit), (6, "votes"))
        self.assertEqual(s.title, "YC launch post reached 6 votes")
        self.assertEqual(s.metrics["yc_batch"], "Fall 2026")
        self.assertEqual(s.metrics["yc_launch_votes"], 6)
        self.assertEqual(s.metrics["occurred_at_basis"], "launch_post_created_at")
        self.assertEqual(s.metrics["days_directory_to_launch"], 10)
        self.assertEqual(s.metrics["launch_title"], "Invertix: Energy Superintelligence")
        self.assertEqual(s.entity.links["yc_batch"], "Fall 2026")
        self.assertTrue(s.text.startswith("Invertix: Energy Superintelligence"))
        self.assertNotIn("bookface", json.dumps(s.to_row()))  # internal YC links never leak

    def test_breakout_launch(self):
        s = self.make("DeepReach Inc.", window_votes=self.VOTES, window_days=120)
        self.assertEqual(s.value, 470)
        self.assertGreaterEqual(s.strength, 0.85)
        self.assertEqual(s.metrics["yc_launch_votes_percentile"], 97)  # 29 of 30 below it
        self.assertEqual(s.metrics["yc_launches_in_window"], 30)
        self.assertEqual(s.title, "YC launch post reached 470 votes, top 4% of YC launches in the past 120 days")

    def test_median_launch_gets_plain_title(self):
        s = self.make("Enact", window_votes=self.VOTES, window_days=120)
        self.assertEqual(s.title, "YC launch post reached 16 votes")
        self.assertTrue(0.35 <= s.strength <= 0.55)

    def test_titles(self):
        self.assertEqual(yc.launch_title(0), "YC launch post published, no votes yet")
        self.assertEqual(yc.launch_title(1), "YC launch post reached 1 vote")
        self.assertEqual(yc.launch_title(30, 26, 120), "YC launch post reached 30 votes")
        for h in LAUNCHES["hits"]:
            t = yc.launch_title(h["total_vote_count"], yc.top_share(h["total_vote_count"], self.VOTES), 120)
            self.assertLessEqual(len(t), 110)
            self.assertFalse(t.endswith("."))

    def test_bad_hits_give_no_signal(self):
        c = BY_NAME["Invertix"]
        v = yc.thesis_verdict(c)
        good = self.HITS["Invertix"]
        self.assertIsNone(yc.launch_signal({**good, "created_at": None}, c, v))
        self.assertIsNone(yc.launch_signal({**good, "search_path": "https://bookface.ycombinator.com/x"}, c, v))
        self.assertIsNone(yc.launch_signal({**good, "total_vote_count": "many"}, c, v))

    def test_follow_up_launch_is_named_and_discounted(self):
        first = self.make("Invertix", number=1)
        again = self.make("Invertix", number=2)
        unknown = self.make("Invertix")
        self.assertEqual(first.title, "YC launch post reached 6 votes")
        self.assertEqual(again.title, "Follow-up YC launch post reached 6 votes")
        self.assertEqual(unknown.title, first.title)
        self.assertLess(again.strength, first.strength)
        self.assertEqual(unknown.strength, first.strength)
        self.assertEqual(again.metrics["yc_launch_number"], 2)
        self.assertNotIn("yc_launch_number", unknown.metrics)
        self.assertEqual(yc.launch_title(0, repeat=True), "Follow-up YC launch post published, no votes yet")

    def test_launch_number(self):
        known = ["2026-08-05T17:31:24.990Z", "2026-09-02T19:54:37.419Z", "2026-09-02T19:54:37.000Z", ""]
        self.assertEqual(yc.launch_number("2026-08-05T17:31:24.990Z", known), 1)
        self.assertEqual(yc.launch_number("2026-09-02T19:54:37.419Z", known), 2)  # page and board stamps agree
        self.assertEqual(yc.launch_number("2026-09-30T00:00:00.000Z", []), 1)


class FakeHttp:
    """Serves the fixtures in place of antenna.http; any other URL fails the test."""

    def __init__(self):
        self.calls: list[str] = []

    def get_json(self, url, **kw):
        self.calls.append(url)
        if url == yc.META_URL:
            return {"last_updated": "2026-10-01T03:12:16.858Z", "batches": {
                "fall-2026": {"name": "Fall 2026", "count": 108, "api": "fixture://fall-2026"},
                "summer-2026": {"name": "Summer 2026", "count": 231, "api": "fixture://summer-2026"},
                "spring-2026": {"name": "Spring 2026", "count": 194, "api": "fixture://spring-2026"},
                "winter-2027": {"name": "Winter 2027", "count": 1, "api": "fixture://winter-2027"},
            }}
        if url == yc.CHANGES_URL:
            return CHANGES
        if url.startswith("fixture://"):
            if url == "fixture://spring-2026":
                raise http.HttpError(503, url)  # one batch file down must not lose the run
            batch = {"fixture://fall-2026": "Fall 2026", "fixture://summer-2026": "Summer 2026"}[url]
            return [c for c in COMPANIES if c["batch"] == batch]
        raise AssertionError(f"unexpected get_json {url}")

    def get(self, url, **kw):
        self.calls.append(url)
        if url == yc.LAUNCHES_URL:
            self_headers = kw.get("headers") or {}
            assert self_headers.get("Accept") == "application/json", "launches needs Accept: application/json"
            page = (kw.get("params") or {}).get("page")
            return json.dumps({"hits": LAUNCHES["hits"] if page == 0 else []})
        if url == "https://www.ycombinator.com/companies/hundred":
            hundred = {"id": 37732, "name": "Hundred", "team_size": 3, "year_founded": 2026,
                       "linkedin_url": "https://www.linkedin.com/company/hundred-systems-inc/",
                       "twitter_url": "", "github_url": "", "cb_url": "",
                       "founders": [{"full_name": "Erica Diaz", "title": "Founder/CEO", "is_active": True,
                                     "founder_bio": "Previously at Meta, Google, and Magic Leap.",
                                     "linkedin_url": "https://www.linkedin.com/in/erica-diaz1/",
                                     "twitter_url": ""}]}
            return page_html(hundred)
        if url == "https://www.ycombinator.com/companies/micora":
            return page_html({**PAGE["company"]})  # wrong company id: must be ignored
        if url.startswith(yc.COMPANY_PAGE_PREFIX):
            raise http.HttpError(404, url)
        raise AssertionError(f"unexpected get {url}")


class CollectOffline(unittest.TestCase):
    def run_collect(self, limit=None):
        fake = FakeHttp()
        ctx = Context(today=TODAY, limit=limit)
        with mock.patch.object(yc.http, "get_json", fake.get_json), \
                mock.patch.object(yc.http, "get", fake.get), \
                mock.patch.object(yc.time, "sleep", lambda s: None), \
                mock.patch("sys.stderr"):
            signals = list(yc.collect(ctx))
        return signals, ctx, fake

    def test_end_to_end_on_fixtures(self):
        signals, ctx, fake = self.run_collect()
        self.assertGreater(len(signals), 30)
        for s in signals:
            s.validate()
            self.assertIn(s.kind, ("yc_batch", "yc_launch"))
            self.assertGreaterEqual(s.occurred_at[:10], ctx.since.isoformat())
            self.assertLessEqual(len(s.title), 110)
            self.assertEqual(s.metrics["yc_batch"], s.entity.links["yc_batch"])
            self.assertTrue(s.url.startswith("https://www.ycombinator.com/"))
        by = {(s.kind, s.entity.name): s for s in signals}

        hundred = by[("yc_batch", "Hundred")]
        self.assertEqual([p.name for p in hundred.people], ["Erica Diaz"])
        self.assertEqual(hundred.entity.founded, "2026")
        self.assertTrue(hundred.metrics["listed_in_changes_feed"])

        # Launches join the directory on company id; off-thesis launches are not emitted.
        deepreach = by[("yc_launch", "DeepReach")]  # "DeepReach Inc." in the directory
        self.assertEqual(deepreach.value, 470)
        self.assertEqual(deepreach.entity.aliases, ["DeepReach Inc."])
        self.assertEqual(by[("yc_launch", "Invertix")].value, 6)
        names = {s.entity.name for s in signals}
        self.assertNotIn("Risklytics", names)   # in the launches fixture, Fintech
        self.assertNotIn("sizeless", names)     # in the launches fixture, Summer 2023
        self.assertNotIn("Vexo", names)         # in the directory fixture, consumer hardware
        self.assertNotIn("OpEra", names)        # in the directory fixture, supply-chain software
        for s in signals:
            self.assertNotRegex(s.entity.name, r"(?i)\binc\.?$")
            self.assertIsNone(s.entity.github)  # no page in the fixtures states one
            for p in s.people:
                self.assertEqual(p.affiliations, [])
        # One signal per launch post and per listing, never two.
        keys = [(s.kind, s.url) for s in signals]
        self.assertEqual(len(keys), len(set(keys)))

        # A page for a different company id is ignored, a missing page only warns.
        self.assertEqual(by[("yc_batch", "Micora")].people, [])
        self.assertTrue(any("micora" in w and "is not" in w for w in ctx.warnings))
        self.assertTrue(any("HttpError" in w for w in ctx.warnings))
        self.assertTrue(any("Spring 2026" in w for w in ctx.warnings))

    def test_limit_caps_entities_newest_first(self):
        signals, _, fake = self.run_collect(limit=5)
        names = [s.entity.name for s in signals]
        self.assertEqual(len(set(names)), 5)
        self.assertEqual(names[0], "Hundred")  # listed 2026-09-30, the newest event
        pages = [u for u in fake.calls if u.startswith(yc.COMPANY_PAGE_PREFIX)]
        self.assertEqual(len(pages), 5)  # founder pages only for emitted companies

    def test_launch_failure_keeps_directory_listings(self):
        fake = FakeHttp()

        def broken_get(url, **kw):
            if url == yc.LAUNCHES_URL:
                raise TimeoutError("handshake timed out")
            return fake.get(url, **kw)

        ctx = Context(today=TODAY)
        with mock.patch.object(yc.http, "get_json", fake.get_json), \
                mock.patch.object(yc.http, "get", broken_get), \
                mock.patch.object(yc.time, "sleep", lambda s: None), \
                mock.patch("sys.stderr"):
            signals = list(yc.collect(ctx))
        self.assertTrue(signals)
        self.assertEqual({s.kind for s in signals}, {"yc_batch"})
        self.assertTrue(any("yc launches" in w for w in ctx.warnings))

    def collect_with(self, fake, today=TODAY, limit=None):
        ctx = Context(today=today, limit=limit)
        with mock.patch.object(yc.http, "get_json", fake.get_json), \
                mock.patch.object(yc.http, "get", fake.get), \
                mock.patch.object(yc.time, "sleep", lambda s: None), \
                mock.patch("sys.stderr"):
            return list(yc.collect(ctx)), ctx

    def test_meta_failure_warns_and_emits_nothing(self):
        fake = FakeHttp()

        def broken(url, **kw):
            raise TimeoutError("no route")

        fake.get_json = broken
        signals, ctx = self.collect_with(fake)
        self.assertEqual(signals, [])
        self.assertTrue(any("meta.json" in w for w in ctx.warnings))

    def test_a_post_on_two_pages_is_emitted_once(self):
        # Pages are cached separately; a new post pushes the last hit of page 0 onto page 1.
        fake = FakeHttp()
        plain = fake.get

        def shifted(url, **kw):
            if url == yc.LAUNCHES_URL:
                page = (kw.get("params") or {}).get("page")
                return json.dumps({"hits": LAUNCHES["hits"] if page in (0, 1) else []})
            return plain(url, **kw)

        fake.get = shifted
        signals, _ = self.collect_with(fake)
        once, _ = self.collect_with(FakeHttp())
        urls = [s.url for s in signals if s.kind == "yc_launch"]
        self.assertEqual(len(urls), len(set(urls)))
        self.assertEqual(sorted(urls), sorted(s.url for s in once if s.kind == "yc_launch"))
        deepreach = next(s for s in signals if s.kind == "yc_launch" and s.entity.name == "DeepReach")
        self.assertEqual(deepreach.metrics["yc_launches_in_window"], len(LAUNCHES["hits"]))

    def test_future_and_stale_dates_are_not_emitted(self):
        fake = FakeHttp()
        plain_json = fake.get_json

        def with_bad_dates(url, **kw):
            rows = plain_json(url, **kw)
            if url == "fixture://fall-2026":
                rows = [dict(c) for c in rows]
                for c in rows:
                    if c["name"] == "Hundred":
                        c["launched_at"] = 1893456000   # 2030-01-01
                    if c["name"] == "Pendulum Robotics":
                        c["launched_at"] = 86400        # 1970-01-02
            return rows

        fake.get_json = with_bad_dates
        signals, ctx = self.collect_with(fake)
        names = {s.entity.name for s in signals if s.kind == "yc_batch"}
        self.assertNotIn("Hundred", names)
        self.assertNotIn("Pendulum Robotics", names)
        self.assertIn("Micora", names)
        latest = TODAY.toordinal() + yc.FUTURE_SLACK_DAYS
        for sig in signals:
            day = date.fromisoformat(sig.occurred_at[:10])
            self.assertLessEqual(day.toordinal(), latest)
            self.assertGreaterEqual(day, ctx.since)

    def test_page_is_the_evidence_for_name_batch_and_status(self):
        fake = FakeHttp()
        plain = fake.get

        def renamed(url, **kw):
            if url == "https://www.ycombinator.com/companies/hundred":
                return page_html({"id": 37732, "name": "Hundred Systems, Inc.", "batch_name": "Summer 2026",
                                  "team_size": 4, "ycdc_status": "Active", "founders": [],
                                  "github_url": "https://github.com/orgs/hundred-systems/repositories"})
            if url == "https://www.ycombinator.com/companies/pendulum-robotics":
                return page_html({"id": BY_NAME["Pendulum Robotics"]["id"], "name": "Pendulum Robotics",
                                  "ycdc_status": "Inactive", "founders": []})
            return plain(url, **kw)

        fake.get = renamed
        signals, _ = self.collect_with(fake)
        by = {(s.kind, s.entity.name): s for s in signals}
        s = by[("yc_batch", "Hundred Systems")]
        self.assertEqual(s.title, "Listed in YC Summer 2026 under Manufacturing and Robotics, now a team of 4")
        self.assertEqual(s.metrics["yc_batch"], "Summer 2026")
        self.assertEqual(s.entity.links["yc_batch"], "Summer 2026")
        self.assertIn("Hundred", s.entity.aliases)                # the mirror's name still resolves
        self.assertIn("Hundred Systems, Inc.", s.entity.aliases)  # and so does the page's spelling
        self.assertEqual(s.entity.github, "hundred-systems")
        self.assertNotIn(("yc_batch", "Pendulum Robotics"), by)   # inactive on its own page

    def test_follow_up_launch_end_to_end(self):
        # The page lists an earlier post than the one on the board: the board's is a follow-up.
        fake = FakeHttp()
        plain = fake.get
        invertix = BY_NAME["Invertix"]

        def with_history(url, **kw):
            if url == invertix["url"]:
                blob = json.dumps({"props": {"company": {"id": invertix["id"], "name": "Invertix",
                                                         "founders": []},
                                             "launches": [{"created_at": "2026-07-01T16:00:00.000Z"},
                                                          {"created_at": "2026-09-28T07:00:00.116Z"}]}})
                return f'<div data-page="{html.escape(blob, quote=True)}"></div>'
            return plain(url, **kw)

        fake.get = with_history
        signals, _ = self.collect_with(fake)
        s = next(s for s in signals if s.kind == "yc_launch" and s.entity.name == "Invertix")
        self.assertEqual(s.title, "Follow-up YC launch post reached 6 votes")
        self.assertEqual(s.metrics["yc_launch_number"], 2)
        self.assertEqual(s.strength, yc.launch_strength(6, repeat=True))
        first = next(s for s in signals if s.kind == "yc_launch" and s.entity.name == "DeepReach")
        self.assertFalse(first.title.startswith("Follow-up"))


if __name__ == "__main__":
    unittest.main()
