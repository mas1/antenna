"""Offline tests for the hn_launch collector: parsing, the thesis gate, strength.

Everything runs against the saved Algolia and Firebase responses in
fixtures/launches/. No network: the one test that drives collect() swaps
http.get_json for a fake that serves those fixtures.
"""

from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from antenna.collectors import hn_launch as hn
from antenna.collectors.base import Context

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "launches"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


LAUNCH_HITS = _load("hn_algolia_launch_hn_365d.json")["hits"]
SHOW_HITS = _load("hn_algolia_show_hn_thesis_90d.json")["hits"]
FIREBASE_NORI = _load("hn_firebase_item_49525153.json")
BY_ID = {h["objectID"]: h for h in SHOW_HITS + LAUNCH_HITS}

# The Nori founder's HN user record as read live on 2026-10-01 (karma 101,
# created 2025-03-21, no earlier story). Not in a fixture file, so pinned here.
NORI_AUTHOR = {"karma": 101, "created": 1742590649, "prior_stories": 0}


def kept_ids(hits: list[dict]) -> set[str]:
    return {h["objectID"] for h in hits if hn.candidate(h) is not None}


class ParseTitle(unittest.TestCase):
    def test_launch_hn_with_dash(self):
        p = hn.parse_title("Launch HN: Nori Robotics (YC S26) – A low-cost humanoid robot for development")
        self.assertEqual(p["kind"], "launch_hn")
        self.assertEqual(p["name"], "Nori Robotics")
        self.assertEqual(p["tagline"], "A low-cost humanoid robot for development")
        self.assertEqual(p["yc_batch"], "S26")

    def test_launch_hn_without_dash(self):
        p = hn.parse_title("Launch HN: ProvenMetal (YC S26) delivers circuit boards in days instead of weeks")
        self.assertEqual(p["name"], "ProvenMetal")
        self.assertEqual(p["tagline"], "delivers circuit boards in days instead of weeks")
        self.assertEqual(p["yc_batch"], "S26")

    def test_show_hn_separators(self):
        cases = {
            "Show HN: JBR-001 – An open-source 3D printable desktop robot": "JBR-001",
            "Show HN: Cubic Doggo 06R: 12-DOF 4-Legged Robot with IMU": "Cubic Doggo 06R",
            "Show HN: Wirespan, a daily power grid optimization puzzle I made for my son": "Wirespan",
            "Show HN: Langy, an automated AI engineer (we gave it a robot body) [video]": "Langy",
            "SHOW HN: Orbit - AR satellite tracker": "Orbit",
            "Show HN: OpenC6 v2.0 – Bare-metal BIOS and RISC-V microkernel for ESP32-C6": "OpenC6",
            "Show HN: Reactor Atlas": "Reactor Atlas",
        }
        for title, name in cases.items():
            with self.subTest(title=title):
                p = hn.parse_title(title)
                self.assertEqual(p["kind"], "show_hn")
                self.assertEqual(p["name"], name)

    def test_sentences_are_not_names(self):
        for title in (
            "Show HN: I vibecoded a heavy lift drone",
            "Show HN: A tiny LLM running at 21,000 tok/s on a $250 FPGA (Live Demo)",
            "Show HN: My solar died for 6 months, so I built a watchdog",
            "Show HN: Building an Autonomous Drone with Codex – Hardware Phase",
            "Show HN: FPGA design acceleration – idiomatic Python to synthesizable Verilog",
            "Show HN: Robot or Meatbag?",
        ):
            with self.subTest(title=title):
                self.assertIsNone(hn.parse_title(title)["name"])

    def test_every_fixture_launch_title_yields_a_company_name(self):
        for h in LAUNCH_HITS:
            p = hn.parse_title(h["title"])
            self.assertEqual(p["kind"], "launch_hn", h["title"])
            self.assertTrue(p["name"], h["title"])
            self.assertIn(p["name"], h["title"])
            self.assertRegex(p["yc_batch"], r"^[A-Z]\d{2}$")


class Urls(unittest.TestCase):
    def test_github_repo(self):
        self.assertEqual(hn.github_repo("https://github.com/Hebbian-Robotics/hflow"),
                         ("Hebbian-Robotics", "hflow"))
        self.assertEqual(hn.github_repo("https://github.com/rochus-keller/OberonSystem/tree/op2-rv32"),
                         ("rochus-keller", "OberonSystem"))
        self.assertEqual(hn.github_repo("https://nagylukas.github.io/orbit.html"), ("nagylukas", None))
        self.assertEqual(hn.github_repo("https://edacation.github.io/nextpnr-viewer/"),
                         ("edacation", "nextpnr-viewer"))
        self.assertIsNone(hn.github_repo("https://github.com/orgs/foo/repositories"))
        self.assertIsNone(hn.github_repo("https://www.norirobotics.com/"))
        self.assertIsNone(hn.github_repo(None))

    def test_own_domain_drops_institutions_stores_job_boards_and_shorteners(self):
        for url in (
            "https://phillips.shef.ac.uk/robot-arm/",          # university host seen in the live window
            "https://www.ndstudio.gov/posts/drone",             # government host seen in the live window
            "https://www.academia.edu/12345/A_Quadruped",
            "https://robotics.stanford.edu/~someone/gripper",
            "https://www.unsw.edu.au/research/uav",
            "https://www.navy.mil/Press-Office/",
            "https://store.steampowered.com/app/1/Drone_Sim",
            "https://jobs.ashbyhq.com/acme-robotics",
            "https://boards.greenhouse.io/acmerobotics",
            "https://www.workatastartup.com/companies/acme",
            "https://tinyurl.com/acme-robot",
            "https://speedrun.a16z.com/companies/acme",
            "https://techcrunch.com/2026/09/01/acme-robotics-raises/",
            "https://claude.ai/public/artifacts/abc",
        ):
            with self.subTest(url=url):
                self.assertIsNone(hn.own_domain(url))
        # A company on an unusual TLD is still its own domain.
        self.assertEqual(hn.own_domain("https://holoso.digital"), "holoso.digital")
        self.assertEqual(hn.own_domain("https://general-instinct.com/"), "general-instinct.com")
        self.assertEqual(hn.own_domain("https://rover.ac/"), "rover.ac")  # .ac alone is a normal TLD

    def test_own_domain_drops_shared_hosts(self):
        self.assertEqual(hn.own_domain("https://www.norirobotics.com/"), "norirobotics.com")
        self.assertEqual(hn.own_domain("https://provenmetal.com"), "provenmetal.com")
        for shared in (
            "https://github.com/agamrossen/VolAnti",
            "https://projecthub.arduino.cc/syntheticaidata/jbr-001-a-desktop-companion-robot",
            "https://ai-eng-design-production.up.railway.app/login",
            "https://lukeiseman.substack.com/p/i-vibecoded-a-drone",
            "https://mini000.itch.io/beeper",
            None,
        ):
            with self.subTest(url=shared):
                self.assertIsNone(hn.own_domain(shared))

    def test_local_host_list_only_adds_to_the_shared_one(self):
        # base.clean_domain rejects code hosts, site builders, job boards and
        # crowdfunding itself. The local list must hold only what it lets through.
        from antenna.collectors.base import clean_domain
        self.assertEqual(len(hn._SHARED_HOSTS), len(set(hn._SHARED_HOSTS)))
        for host in hn._SHARED_HOSTS:
            with self.subTest(host=host):
                self.assertEqual(clean_domain("https://" + host + "/x"), host)
                self.assertIsNone(hn.own_domain("https://someone." + host + "/x"))
        # Hosts that left the local list are still dropped, by the shared one.
        for url in ("https://acme.pages.dev/", "https://acme.gitlab.io/robot", "https://hackaday.io/project/1",
                    "https://www.kickstarter.com/projects/acme/arm", "https://jobs.lever.co/acme"):
            with self.subTest(url=url):
                self.assertIsNone(clean_domain(url))
                self.assertIsNone(hn.own_domain(url))
        # National school and government hosts: the shared list now spells these out...
        for url in ("https://www.tech.gov.sg/drone", "https://robotics.auckland.ac.nz/arm",
                    "https://www.nus.edu.sg/uav"):
            with self.subTest(url=url):
                self.assertIsNone(clean_domain(url))
                self.assertIsNone(hn.own_domain(url))
        # ...and the local pattern still covers the same forms under any other country code.
        for url in ("https://www.uct.ac.za/drone", "https://www.defence.gov.au/uav",
                    "https://www.ntu.edu.tw/robot", "https://www.exercito.mil.br/arm"):
            with self.subTest(url=url):
                self.assertIsNotNone(clean_domain(url))
                self.assertIsNone(hn.own_domain(url))


class StripHtml(unittest.TestCase):
    def test_entities_links_and_paragraphs(self):
        raw = FIREBASE_NORI["text"]
        plain = hn.strip_html(raw)
        self.assertTrue(plain.startswith("Hey HN, I’m Antonio from Nori Robotics (https://norirobotics.com)."))
        self.assertNotIn("&#x2F;", plain)
        self.assertNotIn("<a ", plain)
        self.assertIn("\n\n", plain)
        self.assertEqual(hn.strip_html(None), "")


class ThesisGate(unittest.TestCase):
    def test_launch_fixture_keeps_the_physical_world_companies(self):
        kept = kept_ids(LAUNCH_HITS)
        for sid, who in {
            "49525153": "Nori Robotics",
            "49510632": "Hebbian Robotics",
            "49466715": "Salem Robotics",
            "49269090": "Discovered Materials",
            "49198464": "ProvenMetal",
        }.items():
            self.assertIn(sid, kept, who)

    def test_launch_fixture_drops_software_and_lookalikes(self):
        kept = kept_ids(LAUNCH_HITS)
        for sid, who in {
            "49911995": "Magnitude, an inference engine",
            "49804931": "Coverage Cat, insurance",
            "49451495": "Risklytics, an insurance brokerage that lists robots and drones as customers",
            "49543530": "RonanRX, peptides, whose founder once ran a mask factory",
            "49552616": "Mireye, a location data API that says 'enrichment'",
            "49348136": "machine0, cloud VMs",
            "49246057": "Stoa Markets, a GPU marketplace",
        }.items():
            self.assertNotIn(sid, kept, who)

    def test_show_fixture_keeps_builders(self):
        kept = kept_ids(SHOW_HITS)
        for sid, what in {
            "49890707": "JBR-001 desktop robot (robot backed by servo in the text)",
            "49242475": "LLM on an FPGA",
            "49802789": "InstinctFlash robotics runtime",
            "49253378": "heavy lift drone",
            "49618955": "VolAnti drone detector",
            "49048024": "Cubic Doggo legged robot",
            "48923872": "Stillwind, PCB part selection",
            "49478426": "PCB business card",
            "49184900": "PCB badges",
        }.items():
            self.assertIn(sid, kept, what)

    def test_show_fixture_drops_lookalikes(self):
        kept = kept_ids(SHOW_HITS)
        for sid, what in {
            "49246804": "Needle2, an on-device LLM that lists 'robots' last",
            "49898778": "solar system visualisation",
            "48873501": "AR satellite tracker",
            "48826094": "Chiptune Radio",
            "49744174": "CHIP-8 assembler",
            "49449201": "robot football league",
            "49345706": "Robot or Meatbag?",
            "49433314": "power grid puzzle",
            "48919638": "robotics course",
            "49549148": "Reactor Atlas",
            "49824715": "manufacturing ERP with no post text",
            "49223383": "2D Grid IDE",
            "49287596": "UI component libraries",
            "48893550": "ride price comparison",
        }.items():
            self.assertNotIn(sid, kept, what)

    def test_idioms_do_not_count(self):
        for title in (
            "Eve Software Factory",
            "Raiders at the Gate – tower defense web game",
            "DynaJS – New JavaScript runtime with batteries included",
            "Shelf Protocol – Robots.txt for Commerce",
            "ReplyHey – Get Customers from Reddit on Autopilot",
            "Locus Self-hosted Google Maps lead scraper with AI email enrichment",
            "SignalFusionKit – a watchOS sensor-fusion library for risk detection",
            "Mdrone – a microtonal drone instrument that runs in the browser",
            "Lunar, a coding harness extensible with Lua",
        ):
            with self.subTest(title=title):
                self.assertFalse(hn.thesis_gate(title, "", False)["ok"])

    def test_a_picture_of_a_circuit_board_and_a_sound_array_are_not_the_thing(self):
        # The shared list takes "circuit board" and "phased array" alone as
        # proof. Both titles as read from the listing on 2026-10-02, no post
        # text: code drawn as a board, and an array of ultrasonic transducers.
        for title in (
            "TypeScript/JavaScript as interactive isometric circuit board",
            "Visualizing sound waves with a custom Pico 2W ultrasonic phased array",
        ):
            with self.subTest(title=title):
                g = hn.thesis_gate(title, "", False)
                self.assertEqual((g["ok"], g["why"]), (False, "no thesis term in title"))
        # The same simile in other words (title made up).
        self.assertFalse(hn.thesis_gate("Your repo's dependency graph, drawn like a circuit board", "", False)["ok"])
        # The same words used for the thing itself are kept (the last title is made up).
        for title in (
            "Copperhead – Cursor for circuit boards",
            "ProvenMetal (YC S26) delivers circuit boards in days instead of weeks",
            "Stillwind – PCB part selection as constraint solving",
            "An X-band phased array you can build for $900",
        ):
            with self.subTest(title=title):
                g = hn.thesis_gate(title, "", False)
                self.assertEqual((g["ok"], g["why"]), (True, "title names the thesis"))

    def test_robotics_the_field_is_title_evidence_but_robotic_the_adjective_is_not(self):
        # The shared pattern reads "robotics" as the term "robotic". Titles and
        # openings as read live on 2026-10-01.
        g = hn.thesis_gate("A Layer for robotics dataset quality", "", False)
        self.assertEqual((g["ok"], g["why"]), (True, "title names the thesis"))
        g = hn.thesis_gate(
            "General Instinct (YC P26) – Frontier models on edge devices",
            "Hey HN, Guanming and Bill here from General Instinct (https://general-instinct.com/).\n\n"
            "After years of working in robotics, we kept running into the same problem: the best"
            " models never fit the hardware we actually had available.", True)
        self.assertEqual((g["ok"], g["why"]), (True, "title names the thesis"))
        g = hn.thesis_gate("HexBOTs – a cellular automaton with robotic lawnmowers", "", False)
        self.assertEqual((g["ok"], g["why"]), (False, "ambiguous term only"))
        # The noun in the post text backs an ambiguous robotics word in the title...
        self.assertTrue(hn.thesis_gate("Gardener One – a robot for the garden",
                                       "We met in a robotics lab.", False)["ok"])
        # ...and the adjective twice in one title is still one ambiguous term.
        self.assertFalse(hn.thesis_gate("Robotic voices for robotic pets", "", False)["ok"])
        self.assertEqual(hn._terms("Robotics"), {"robotic", "robotics"})
        self.assertEqual(hn._terms("robotic"), {"robotic"})

    def test_a_lone_ambiguous_term_stays_under_the_shared_cap(self):
        # classify() holds a single term that is not unmistakably physical at
        # 0.28. HN gives nothing but the title to add a second, so these are
        # skipped. Titles as read from the listing on 2026-10-02, none with
        # post text.
        for title in (
            "A solar-powered, privacy-preserving vehicle counter (Pi 5 and YOLOv8)",
            "AI agents design an open chip, the best one gets fabricated",
            "URML – safety-eval harness for AI agents on lab and factory hardware",
            "A route planner that starts from battery range, not distance",
        ):
            with self.subTest(title=title):
                g = hn.thesis_gate(title, "", False)
                self.assertEqual((g["ok"], g["why"]), (False, "fit"))
                self.assertLess(g["fit"], 0.3)
        # One more term in the text and the same word is enough (the text here is made up).
        self.assertTrue(hn.thesis_gate("A solar-powered, privacy-preserving vehicle counter (Pi 5 and YOLOv8)",
                                       "A 20 W panel and a small inverter keep the Pi running.", False)["ok"])

    def test_words_new_to_the_shared_list_that_hn_uses_loosely(self):
        ok = lambda t, s="": hn.thesis_gate(t, s, False)["ok"]  # noqa: E731
        # "rover": a product name as often as a vehicle.
        self.assertFalse(ok("Rover – book a trusted dog sitter in minutes"))
        self.assertFalse(ok("Rover – a tiny browser", "It can drive a headless tab on autopilot mode."))
        self.assertTrue(ok("Rover One – a robot for the garden"))  # two robotics words in the title
        self.assertTrue(ok("An open-source rover you can build for $500",
                           "Six servo motors, a lidar and ROS2 on a Raspberry Pi."))
        # "strike" and "SDR" have left the shared list, so these titles, as
        # read live on 2026-10-01, no longer carry a thesis term at all.
        for title, story in (
            ("Bolted – Visualizing real-time lightning strikes with MetalKit", ""),
            ("WebRTC P2P networking based Counter-Strike 1.6 on the web", "A tactical shooter in the browser."),
            ("Web App Uses RTL-SDR to Align HDTV Antenna", "It reads signal strength from the chip."),
        ):
            with self.subTest(title=title):
                self.assertEqual(hn.thesis_gate(title, story, False)["why"], "no thesis term in title")
        # "kinematics" and agency names, as read live on 2026-10-01.
        self.assertFalse(ok("SwiftMo Power captures real-time exercise kinematics from a webcam",
                            "Motion analysis for strength training and manipulation of the bar path."))
        g = hn.thesis_gate("Air Force One flight history explorer",
                           "Tail numbers from military and DoD records.", False)
        self.assertGreaterEqual(g["fit"], hn.MIN_FIT)  # an agency name says who buys, not what was built
        self.assertEqual((g["ok"], g["why"]), (False, "ambiguous term only"))
        # NASA is an agency too (title and text made up; the window had no NASA post).
        g = hn.thesis_gate("NASA picture of the day in your terminal",
                           "Pulls the image and its caption. Today it is a satellite over the Sahara.", False)
        self.assertGreaterEqual(g["fit"], hn.MIN_FIT)
        self.assertEqual((g["ok"], g["why"]), (False, "ambiguous term only"))
        # A CAD program named in the text says what a tool is built on. It
        # does not make an architecture workspace a manufacturing product.
        g = hn.thesis_gate(
            "Cogram Studio – CAD and BIM workspace for humans and agents",
            "Cogram Studio (studio.cogram.com) is a CAD and BIM workspace for AI agents to create"
            " three-dimensional models and dimensioned drawings. Studio runs FreeCAD 1.1 headlessly,"
            " using the OpenCASCADE geometry kernel.", False)
        self.assertGreaterEqual(g["fit"], hn.MIN_FIT)
        self.assertEqual((g["ok"], g["why"]), (False, "ambiguous term only"))
        # The same family of tools is kept when the title says what it is for.
        self.assertTrue(ok("RapidCam – Browser-based, parametric 2D CAD/CAM app for CNC and laser"))
        self.assertTrue(ok("KiCad in the Browser", "KiCad, a PCB EDA suite is now working in a browser."))

    def test_words_the_shared_list_calls_unmistakable_that_hn_uses_loosely(self):
        # The shared classifier now passes a lone "radar", "lunar" or "RISC-V"
        # at 0.465. On HN they were nearly always something else (the one
        # real radar is in an eldercare monitor), so alone they stay out.
        # Titles as read from the listing on 2026-10-02; where a post had
        # text, it added no thesis term.
        for title in (
            "Omastorm – Live NEXRAD weather radar for Omarchy",
            "Radar, find Reddit threads where your software could help",
            "TLS Radar – Independent SSL/TLS Monitoring, Web and CLI and Claude",
            "Radar 50 – Real-time signal engine for 50 crypto markets, with accuracy",
            "OdeCare – radar-based monitoring for aging parents, no wearables",
            "Lunar, a \"fast\", memory-efficient Lua 5.1 VM written in Go",
            "OpenC6 v2.0 – Bare-metal BIOS and RISC-V microkernel for ESP32-C6",
            "A Project Oberon System version running on RISC-V instead of RISC-5",
        ):
            with self.subTest(title=title):
                g = hn.thesis_gate(title, "", False)
                self.assertGreaterEqual(g["fit"], hn.MIN_FIT)  # the classifier alone would keep it
                self.assertEqual((g["ok"], g["why"]), (False, "ambiguous term only"))
        # With a second word of the sector, or a term that is unmistakable on
        # HN too, the same words are kept (titles made up).
        ok = lambda t, s="": hn.thesis_gate(t, s, False)["ok"]  # noqa: E731
        self.assertTrue(ok("A phased array radar on a $40 board"))
        self.assertTrue(ok("A lunar lander guidance computer for our CubeSat"))
        self.assertTrue(ok("A RISC-V core in 600 lines of Verilog"))
        self.assertTrue(ok("Our RISC-V core is back from the fab", "Tapeout was in March on a 130nm shuttle."))

    def test_soft_terms_are_all_terms_of_the_shared_list(self):
        # A word the shared list drops ("strike", "SDR", "world model") must
        # leave the local list too, or it reads as if it still did something.
        from antenna.config import THESIS
        shared = {t for groups in THESIS.values() for terms in groups.values() for t in terms}
        self.assertEqual(sorted(hn._SOFT_TERMS - shared), [])

    def test_ambiguous_term_needs_backing_from_the_same_sector(self):
        title = "Gardener One – a robot for the garden"
        self.assertFalse(hn.thesis_gate(title, "", False)["ok"])
        self.assertFalse(hn.thesis_gate(title, "It runs on a solar inverter.", False)["ok"])
        backed = hn.thesis_gate(title, "Built on ROS2 with a lidar and two actuators.", False)
        self.assertTrue(backed["ok"])
        self.assertGreaterEqual(backed["fit"], hn.MIN_FIT)

    def test_two_ambiguous_title_terms_of_one_sector_back_each_other(self):
        self.assertTrue(hn.thesis_gate("An open-source humanoid robot you can build for $5k", "", False)["ok"])
        self.assertFalse(hn.thesis_gate("Robots and more robots", "", False)["ok"])  # a plural is not a second term
        # ...but only in the title proper: a Launch HN opening that mentions a
        # factory and manufacturing in passing does not make a pharma company on thesis.
        self.assertFalse(hn.thesis_gate(
            "RonanRX (YC S26) – Personalized Peptides and GLP-1s",
            "We are building a pharmaceutical company with software for compounding, manufacturing"
            " and delivery. I once built one of the largest mask factories in the US.", True)["ok"])

    def test_veto_is_narrow(self):
        ok = lambda t: hn.thesis_gate(t, "", False)["ok"]  # noqa: E731
        self.assertFalse(ok("Leet Robotics: Learn robotics and ROS2 with hands-on courses"))
        self.assertFalse(ok("Learn FPGA design online"))
        self.assertTrue(ok("A drone flight simulator for PX4 autonomy testing"))
        self.assertTrue(ok("How legged robots learn to walk with MuJoCo"))
        self.assertFalse(ok("I have created a browser FPS game with kamikaze drones"))
        self.assertFalse(ok("Drone flight over world cities and neighbourhoods"))

    def test_veto_drops_back_office_software_directories_and_sketches(self):
        # Titles and post text as read live on 2026-10-01; each one passed the
        # classifier and the title-term rule, and none is a builder's product.
        cases = (
            ("Self-hosted CRM for manufacturing (Django and Htmx)",
             "Hi HN, I built a lightweight self-hosted CRM/ERP for small manufacturing shops,"
             " CNC workshops and engineering teams."),
            ("An open-source manufacturing ERP for CNC machine shops", ""),
            ("If I had to design a nuclear fusion power plant(Idea Sketch)", ""),
            ("Simple Free Quadcopter Calculator",
             "Instant feedback on whether a quadcopter build is viable."),
            ("UAVs FYI – Drone database with supply chain data, API and CLI",
             "I want to share my UAV/drone database."),
        )
        for title, story in cases:
            with self.subTest(title=title):
                g = hn.thesis_gate(title, story, False)
                self.assertGreaterEqual(g["fit"], hn.MIN_FIT)  # the classifier alone would keep it
                self.assertEqual((g["ok"], g["why"]), (False, "veto"))
        # The new veto words do not reach real hardware titles.
        for title in (
            "Atlas Motion – motors for drones, robotics, and autonomous systems",
            "A DIY pure sine-wave solar inverter I built from scratch",
            "VolAnti – Open-source acoustic detector for fibre-optic FPV drones",
        ):
            with self.subTest(title=title):
                self.assertTrue(hn.thesis_gate(title, "", False)["ok"])

    def test_unambiguous_title_term_is_enough(self):
        g = hn.thesis_gate("Dual YOLOv8n UAV Detection on RK3588S at 42 FPS Using NPU", "", False)
        self.assertTrue(g["ok"])  # "FPS" here is frames per second, not a shooter
        self.assertTrue(hn.thesis_gate("Write, simulate and synthesize VHDL/Verilog in the browser", "", False)["ok"])
        # Posts with one thesis word and nothing to add a second: four are
        # link posts with no text, and the text of the ros2_utils_tool post
        # has no other term. "circuit board", "Verilog", "SystemVerilog",
        # "ROS2" and "ROS 2" are on the shared unmistakable list, so the one
        # word is enough. Titles as read from the listing on 2026-10-02;
        # before the shared list changed all five were skipped.
        for title in (
            "Copperhead – Cursor for circuit boards",
            "AutoGPU – AI designs a real 7nm GPU, from Verilog to GDSII",
            "Naja-scope – Let AI agents explore SystemVerilog netlists over MCP",
            "The ros2_utils_tool v1.0, a tool for ROS2 activies with full UI support",
            "Sonny OS – An async Rust microkernel replacing ROS 2/DDS with Zenoh",
        ):
            with self.subTest(title=title):
                g = hn.thesis_gate(title, "", False)
                self.assertEqual((g["ok"], g["why"]), (True, "title names the thesis"))
                self.assertGreaterEqual(g["fit"], hn.MIN_FIT)


class Entity(unittest.TestCase):
    def _entity(self, sid: str):
        h = BY_ID[sid]
        p = hn.parse_title(h["title"])
        return hn.entity_for(h, p)

    def test_launch_with_site(self):
        e = self._entity("49525153")
        self.assertEqual((e.name, e.kind, e.domain, e.github),
                         ("Nori Robotics", "company", "norirobotics.com", None))
        self.assertEqual(e.one_liner, "A low-cost humanoid robot for development")
        self.assertEqual(e.links["hn"], "https://news.ycombinator.com/item?id=49525153")

    def test_launch_whose_url_is_a_repo(self):
        e = self._entity("49510632")
        self.assertEqual((e.name, e.kind, e.domain, e.github),
                         ("Hebbian Robotics", "company", None, "Hebbian-Robotics"))

    def test_launch_without_url_takes_site_from_opening_line(self):
        self.assertIsNone(BY_ID["49466715"].get("url"))  # Algolia omits the key on text posts
        e = self._entity("49466715")
        self.assertEqual((e.name, e.domain), ("Salem Robotics", "salemroboticsinc.com"))

    def test_show_hn_repo_is_a_project(self):
        e = self._entity("49618955")
        self.assertEqual((e.name, e.kind, e.domain, e.github), ("VolAnti", "project", None, "agamrossen"))
        e = self._entity("49230891")  # unsplit title: fall back to the repository name
        self.assertEqual((e.name, e.kind, e.github), ("OberonSystem", "project", "rochus-keller"))

    def test_show_hn_on_shared_host_has_no_domain(self):
        e = self._entity("49890707")
        self.assertEqual((e.name, e.kind, e.domain, e.github), ("JBR-001", "project", None, None))

    def test_no_product_name_falls_back_to_site_then_author(self):
        e = self._entity("49242475")
        self.assertEqual((e.name, e.kind, e.domain), ("mikeayles.com", "project", "mikeayles.com"))
        e = self._entity("49253378")  # substack post: only the HN username is known
        self.assertEqual((e.name, e.kind, e.domain, e.github), ("liseman", "person", None, None))
        self.assertEqual(e.links["hn_user"], "https://news.ycombinator.com/user?id=liseman")

    def test_repo_name_fallback_keeps_the_title_as_one_liner(self):
        hit = {"objectID": "48609112", "title": "Show HN: Web-Based FPGA Viewer", "author": "malmeloo",
               "url": "https://edacation.github.io/nextpnr-viewer/", "created_at_i": 1781962246}
        e = hn.entity_for(hit, hn.parse_title(hit["title"]))
        self.assertEqual((e.name, e.kind, e.domain, e.github), ("nextpnr-viewer", "project", None, "edacation"))
        self.assertEqual(e.one_liner, "Web-Based FPGA Viewer")
        self.assertNotEqual(e.one_liner, e.name)

    def test_repo_name_with_trailing_dash_does_not_read_as_cut_off(self):
        hit = {"objectID": "49606818", "author": "Ros_Sourav",
               "title": "Show HN: Hybrid Nav2 and PPO RL for indoor Lidar-only robot navigation",
               "url": "https://github.com/Sourav29-2/ppo-lidar-navigation-", "created_at_i": 1788852870}
        e = hn.entity_for(hit, hn.parse_title(hit["title"]))
        self.assertEqual((e.name, e.github), ("ppo-lidar-navigation", "Sourav29-2"))
        self.assertEqual(e.links["repo"], "https://github.com/Sourav29-2/ppo-lidar-navigation-")  # link untouched

    def test_html_escaped_title_gives_a_clean_name(self):
        p = hn.parse_title("Show HN: R&amp;D Motors – brushless motors for drones &amp; robots")
        self.assertEqual(p["name"], "R&D Motors")
        self.assertEqual(p["tagline"], "brushless motors for drones & robots")

    def test_product_show_hn_resolves_to_the_companys_launch_hn(self):
        # InstinctFlash (Show HN, 2026-09-22) is a product of General Instinct
        # (Launch HN, 2026-06-05). The repo owner is the only shared handle.
        from antenna.collectors.base import normalize_name
        show = self._entity("49802789")
        self.assertEqual((show.name, show.github), ("InstinctFlash", "General-Instinct"))
        self.assertEqual(show.aliases, ["General-Instinct"])
        self.assertIn(normalize_name("General Instinct"), {normalize_name(a) for a in show.aliases})
        # A personal repo adds no alias: the owner is the submitter, not another name.
        self.assertEqual(self._entity("49618955").aliases, [])
        # A github login stays a login.
        self.assertNotIn("/", show.github)

    def test_text_only_post_without_a_name_is_skipped(self):
        hit = {"objectID": "1", "title": "Show HN: I built a drone", "url": None, "author": "someone",
               "story_text": "words", "created_at_i": 1790000000}
        self.assertIsNone(hn.entity_for(hit, hn.parse_title(hit["title"])))


class Maker(unittest.TestCase):
    """Who may be listed as a person on the entity."""

    def _ev(self, title: str, author: str, url: str | None, story: str = ""):
        hit = {"objectID": "1", "title": title, "author": author, "url": url}
        return hn.maker_evidence(hit, hn.parse_title(title), story)

    def test_launch_handle_and_first_person(self):
        self.assertEqual(self._ev("Launch HN: Nori Robotics (YC S26) – A robot", "AntonioLi", None), "launch")
        self.assertEqual(self._ev("Show HN: VolAnti – detector for FPV drones", "agamrossen",
                                  "https://github.com/agamrossen/VolAnti"), "handle")
        self.assertEqual(self._ev("Show HN: A tiny LLM on a $250 FPGA", "mikeayles",
                                  "https://www.mikeayles.com/blog/on-chip-llm-kv260/"), "handle")
        self.assertEqual(self._ev("Show HN: Virena, a minimal vision-language-action robot model",
                                  "georgia_bucea", "https://github.com/BuceaGeorgia/VIRENA"), "handle")
        self.assertEqual(self._ev("Show HN: WorldDiT – a world-action model for simulated robot tasks",
                                  "bageldotcom", "https://huggingface.co/bageldotcom/worlddit"), "handle")
        self.assertEqual(self._ev("Show HN: I vibecoded a heavy lift drone", "liseman",
                                  "https://lukeiseman.substack.com/p/i-vibecoded-a-drone"), "first person")
        self.assertEqual(self._ev("Show HN: JBR-001 – An open-source 3D printable desktop robot", "gvuksic",
                                  "https://projecthub.arduino.cc/syntheticaidata/jbr-001",
                                  "We built a desktop companion robot."), "first person")

    def test_no_evidence(self):
        # Read live: a 451-story account posting Ant Group's robotics repo.
        self.assertIsNone(self._ev(
            "Show HN: Open-weights VLA model for 20 robot embodiments (code and checkpoints)",
            "jinqueeny", "https://github.com/robbyant/lingbot-vla-v2"))
        # The platform's own name is not the submitter's name.
        self.assertIsNone(self._ev("Show HN: Drone swarm planner", "github_fan",
                                   "https://github.com/someorg/planner"))
        self.assertIsNone(self._ev("Show HN: Drone footage stabiliser", "watchman",
                                   "https://www.youtube.com/watch?v=abc"))
        # "US" and "IMU" are not first-person words.
        self.assertIsNone(self._ev("Show HN: US Navy drone tracker with IMU", "someone",
                                   "https://example.org/x", "Made in the US."))

    LINGBOT = {"objectID": "48830169", "author": "jinqueeny", "points": 1, "num_comments": 0,
               "title": "Show HN: Open-weights VLA model for 20 robot embodiments (code and checkpoints)",
               "url": "https://github.com/robbyant/lingbot-vla-v2", "created_at_i": 1783506774,
               "created_at": "2026-07-08T10:32:54Z", "_tags": ["story", "author_jinqueeny", "show_hn"]}

    def test_serial_submitter_sharing_someone_elses_repo_is_not_emitted(self):
        cand = hn.candidate(self.LINGBOT)
        self.assertIsNotNone(cand)  # on thesis; the problem is who posted it
        self.assertIsNone(hn.build_signal(cand, {"prior_stories": 451, "karma": 1705}))

    def test_unproven_submitter_is_kept_out_of_people_but_the_signal_stands(self):
        cand = hn.candidate(self.LINGBOT)
        sig = hn.build_signal(cand, {"prior_stories": 3, "karma": 12})
        sig.validate()
        self.assertEqual(sig.people, [])
        self.assertEqual(sig.metrics["author_prior_stories"], 3)
        self.assertEqual(sig.entity.github, "robbyant")
        # Unknown history is not treated as a serial submitter either.
        self.assertIsNotNone(hn.build_signal(cand, {}))

    def test_prolific_maker_is_kept(self):
        # ipunchghosts: 59 earlier stories, title in the first person. A
        # high count alone never drops a post.
        hit = {"objectID": "48711382", "author": "ipunchghosts", "points": 1, "num_comments": 0,
               "title": "Show HN: I reverse-engineered the RLF log format used by REMUS underwater drones",
               "url": "https://github.com/isaacgerg/remus-rlf-reader", "created_at_i": 1782679100,
               "_tags": ["story", "show_hn"]}
        sig = hn.build_signal(hn.candidate(hit), {"prior_stories": 400})
        self.assertEqual([p.name for p in sig.people], ["ipunchghosts"])


class Strength(unittest.TestCase):
    def test_range_and_monotonic(self):
        ladder = [hn.strength_for(p, c, False, 5, 900)
                  for p, c in ((1, 0), (4, 0), (16, 0), (27, 4), (79, 33), (130, 31), (537, 185))]
        self.assertEqual(ladder, sorted(ladder))
        self.assertLess(ladder[0], 0.2)      # one point: it exists, little more
        self.assertLess(ladder[2], 0.35)
        self.assertGreaterEqual(ladder[4], 0.6)  # top decile of what the collector keeps
        self.assertLess(ladder[-1], 0.9)     # points alone never reach the first-event tier
        for s in ladder:
            self.assertTrue(0.0 <= s <= 1.0)

    def test_launch_floor_and_first_story_premium(self):
        self.assertGreaterEqual(hn.strength_for(3, 0, True, 12, 900), hn.LAUNCH_FLOOR)
        base = hn.strength_for(201, 66, True, 4, 528)
        first = hn.strength_for(201, 66, True, 0, 528)
        fresh = hn.strength_for(201, 66, True, 0, 3)
        self.assertAlmostEqual(first - base, hn.FIRST_STORY_BONUS, places=3)
        self.assertGreater(fresh, first)
        self.assertLessEqual(fresh, hn.STRENGTH_CAP)
        # A first story that nobody noticed earns nothing extra, and unknown history is not "first".
        self.assertEqual(hn.strength_for(2, 0, False, 0, 1), hn.strength_for(2, 0, False, 9, 900))
        self.assertEqual(hn.strength_for(201, 66, True, None, None), base)


class Titles(unittest.TestCase):
    def test_wording(self):
        self.assertEqual(hn.title_for("launch_hn", 201, 66, "S26", False),
                         "Launch HN (YC S26) reached 201 points and 66 comments")
        self.assertEqual(hn.title_for("show_hn", 1, 1, None, False),
                         "Show HN reached 1 point and 1 comment")
        self.assertEqual(hn.title_for("show_hn", 72, 9, None, True),
                         "Show HN reached 72 points and 9 comments, the author's first HN story")
        self.assertEqual(hn.title_for("show_hn", 4, 0, None, True),
                         "Show HN reached 4 points and 0 comments")
        long = hn.title_for("launch_hn", 12345, 12345, "S26", True)
        self.assertLess(len(long), 110)
        self.assertFalse(long.endswith("."))


class BuildSignal(unittest.TestCase):
    def test_nori_matches_the_post(self):
        hit = BY_ID["49525153"]
        sig = hn.build_signal(hn.candidate(hit), NORI_AUTHOR)
        sig.validate()
        # The same story as HN's own Firebase API reports it.
        self.assertEqual(hit["points"], FIREBASE_NORI["score"])
        self.assertEqual(hit["num_comments"], FIREBASE_NORI["descendants"])
        self.assertEqual(hit["author"], FIREBASE_NORI["by"])
        self.assertEqual(hit["created_at_i"], FIREBASE_NORI["time"])

        self.assertEqual((sig.source, sig.family, sig.kind), ("hn_launch", "launch", "launch_hn"))
        self.assertEqual(sig.url, "https://news.ycombinator.com/item?id=49525153")
        self.assertEqual(sig.occurred_at, "2026-09-01")
        self.assertEqual(sig.title,
                         "Launch HN (YC S26) reached 201 points and 66 comments, the author's first HN story")
        self.assertEqual((sig.value, sig.unit), (201, "points"))
        self.assertEqual(sig.metrics["hn_points"], 201)
        self.assertEqual(sig.metrics["hn_comments"], 66)
        self.assertEqual(sig.metrics["author_karma"], 101)
        self.assertEqual(sig.metrics["author_prior_stories"], 0)
        self.assertEqual(sig.metrics["author_account_age_days"], 528)
        self.assertTrue(0.85 <= sig.strength <= hn.STRENGTH_CAP)
        self.assertEqual(sig.entity.name, "Nori Robotics")
        self.assertEqual(sig.entity.domain, "norirobotics.com")
        self.assertTrue(sig.entity.description.startswith("Hey HN, I’m Antonio from Nori Robotics"))
        self.assertIn("bimanual mobile robot", sig.text)

        self.assertEqual(len(sig.people), 1)
        person = sig.people[0]
        self.assertEqual(person.name, "AntonioLi")  # the HN username, never a guessed real name
        self.assertEqual(person.links, {"hn": "https://news.ycombinator.com/user?id=AntonioLi"})
        self.assertEqual(person.facts["hn_karma"], 101)

    def test_unknown_author_facts_leave_metrics_out(self):
        sig = hn.build_signal(hn.candidate(BY_ID["49618955"]), {})
        sig.validate()
        self.assertEqual(sig.title, "Show HN reached 16 points and 0 comments")
        self.assertNotIn("author_karma", sig.metrics)
        self.assertNotIn("author_prior_stories", sig.metrics)
        self.assertEqual(sig.people[0].facts, {})

    def test_repost_count_is_recorded_and_an_earlier_repost_is_a_prior_story(self):
        cand = hn.candidate(BY_ID["49618955"])
        sig = hn.build_signal(cand, {"prior_stories": 1, "karma": 7}, reposts=2)
        self.assertEqual(sig.metrics["author_prior_stories"], 1)
        self.assertEqual(sig.metrics["hn_posts_same_url"], 2)
        self.assertNotIn("first HN story", sig.title)

    def test_every_kept_fixture_story_builds_a_valid_signal(self):
        seen = 0
        for h in SHOW_HITS + LAUNCH_HITS:
            cand = hn.candidate(h)
            if cand is None:
                continue
            sig = hn.build_signal(cand, {})
            self.assertIsNotNone(sig, h["title"])
            sig.validate()
            seen += 1
            self.assertLess(len(sig.title), 110)
            self.assertEqual(sig.value, h["points"])
            self.assertIn(str(h["points"]), sig.title.replace(",", ""))
            self.assertEqual(sig.occurred_at, h["created_at"][:10])
            self.assertTrue(sig.url.endswith(h["objectID"]))
            # The submitter is listed only when the post ties them to the thing.
            self.assertEqual([p.name for p in sig.people],
                             [h["author"]] if hn.maker_evidence(h, cand["parsed"], cand["story"]) else [])
            self.assertFalse(sig.title.endswith("."))
            self.assertTrue(sig.title[0].isupper())
            self.assertNotIn("&amp;", sig.entity.name)
            if sig.entity.github:
                self.assertNotIn("/", sig.entity.github)  # a login, never a URL
        self.assertGreaterEqual(seen, 15)


class Collect(unittest.TestCase):
    def _fake_get_json(self, calls: list):
        def fake(url, **kw):
            params = kw.get("params") or {}
            calls.append((url, params))
            if url == hn.SEARCH_URL and params.get("tags") == "(show_hn,launch_hn)":
                return {"hits": SHOW_HITS + LAUNCH_HITS}  # under PAGE_SIZE, so one page
            if url == hn.SEARCH_URL and str(params.get("tags", "")).startswith("story,author_"):
                return {"nbHits": 0 if params["tags"] == "story,author_AntonioLi" else 3}
            if url.startswith("https://hacker-news.firebaseio.com/v0/user/"):
                if url.endswith("/AntonioLi.json"):
                    return {"id": "AntonioLi", "karma": 101, "created": 1742590649,
                            "about": "Antonio is the founder of Nori Robotics.<p>Second line."}
                if url.endswith("/gvuksic.json"):
                    return {"id": "gvuksic", "karma": 64, "created": 1496275200, "about": "Robotics at MIT"}
                return None  # Firebase answers null for a user it will not show
            raise AssertionError(f"unexpected request {url}")
        return fake

    def test_collect_offline(self):
        calls: list = []
        ctx = Context(today=date(2026, 10, 1), lookback_days=120, limit=None)
        with mock.patch.object(hn.http, "get_json", self._fake_get_json(calls)):
            sigs = list(hn.collect(ctx))
        self.assertEqual(ctx.warnings, [])
        for s in sigs:
            s.validate()
        urls = [s.url for s in sigs]
        self.assertEqual(len(urls), len(set(urls)), "a story fetched twice must be emitted once")
        by_name = {s.entity.name: s for s in sigs}
        for name in ("Nori Robotics", "Hebbian Robotics", "Salem Robotics", "ProvenMetal",
                     "Discovered Materials", "JBR-001", "VolAnti"):
            self.assertIn(name, by_name)
        nori = by_name["Nori Robotics"]
        self.assertEqual(nori.metrics["author_karma"], 101)
        self.assertEqual(nori.people[0].facts["bio"], "Antonio is the founder of Nori Robotics. Second line.")
        self.assertIn("first HN story", nori.title)
        # A Show HN can be submitted by anyone, so the submitter's profile text is not attached.
        jbr = by_name["JBR-001"].people[0]
        self.assertEqual((jbr.name, jbr.role, jbr.facts["hn_karma"]), ("gvuksic", "Show HN author", 64))
        self.assertNotIn("bio", jbr.facts)
        self.assertEqual(nori.people[0].role, "Launch HN author")
        # A user record that comes back null leaves karma out rather than guessing.
        self.assertNotIn("author_karma", by_name["VolAnti"].metrics)
        self.assertEqual(by_name["VolAnti"].metrics["author_prior_stories"], 3)
        self.assertNotIn("hn_posts_same_url", nori.metrics)  # in both fixtures, still one post
        # The listing asked for the window only.
        listing = [p for u, p in calls if p.get("tags") == "(show_hn,launch_hn)"]
        self.assertEqual(len(listing), 1)
        self.assertEqual(listing[0]["numericFilters"], "created_at_i>=1780444800")  # 2026-06-03 UTC

    def test_limit_stops_early_and_old_stories_are_skipped(self):
        calls: list = []
        ctx = Context(today=date(2026, 10, 1), lookback_days=120, limit=3)
        with mock.patch.object(hn.http, "get_json", self._fake_get_json(calls)):
            sigs = list(hn.collect(ctx))
        self.assertEqual(len(sigs), 3)

        # With a 20-day lookback nothing before 2026-09-11 may come out, even
        # though the fake listing ignores the date filter.
        ctx = Context(today=date(2026, 10, 1), lookback_days=20, limit=None)
        with mock.patch.object(hn.http, "get_json", self._fake_get_json([])):
            sigs = list(hn.collect(ctx))
        self.assertTrue(sigs)
        for s in sigs:
            self.assertGreaterEqual(s.occurred_at, "2026-09-11")

    def test_story_dated_after_today_is_held_for_the_next_run(self):
        # A run dated 2026-10-01 meets a story HN dates 2026-10-02 01:03 UTC.
        # Emitting it would date a signal after ctx.today.
        late = dict(BY_ID["49890707"], objectID="49999999", created_at_i=1790903036,
                    created_at="2026-10-02T01:03:56Z", url="https://example-robot.dev/")
        far = dict(late, objectID="49999998", created_at_i=4102444800, url="https://example-robot2.dev/")
        epoch = dict(late, objectID="49999997", created_at_i=0, url="https://example-robot3.dev/")

        def fake(url, **kw):
            params = kw.get("params") or {}
            if params.get("tags") == "(show_hn,launch_hn)":
                return {"hits": [far, late, epoch, BY_ID["49890707"]]}
            if str(params.get("tags", "")).startswith("story,author_"):
                return {"nbHits": 2}
            return None

        ctx = Context(today=date(2026, 10, 1), lookback_days=120)
        with mock.patch.object(hn.http, "get_json", fake):
            sigs = list(hn.collect(ctx))
        self.assertEqual([s.url for s in sigs], ["https://news.ycombinator.com/item?id=49890707"])
        self.assertEqual(sigs[0].occurred_at, "2026-09-29")
        # The next day the held story is inside the window and comes out with its real date.
        ctx = Context(today=date(2026, 10, 2), lookback_days=120)
        with mock.patch.object(hn.http, "get_json", fake):
            dates = sorted(s.occurred_at for s in hn.collect(ctx))
        self.assertEqual(dates, ["2026-09-29", "2026-10-02"])

    def test_listing_pages_by_timestamp_until_the_window_is_read(self):
        def story(i: int) -> dict:
            return {"objectID": str(i), "title": "Show HN: nothing", "created_at_i": 1790000000 - i,
                    "_tags": ["story", "show_hn"]}

        everything = [story(i) for i in range(2500)]  # newest first
        asked: list[str] = []

        def fake(url, **kw):
            nf = kw["params"]["numericFilters"]
            asked.append(nf)
            top = int(nf.split("created_at_i<=")[1]) if "created_at_i<=" in nf else 10**12
            return {"hits": [h for h in everything if h["created_at_i"] <= top][:hn.PAGE_SIZE]}

        ctx = Context(today=date(2026, 10, 1), lookback_days=120)
        with mock.patch.object(hn.http, "get_json", fake):
            got = [h["objectID"] for h in hn._walk(ctx)]
        self.assertEqual(len(got), 2500)
        self.assertEqual(len(set(got)), 2500)  # the boundary story of each page is not repeated
        self.assertEqual(len(asked), 3)
        self.assertEqual(ctx.warnings, [])

    def test_short_page_is_not_the_end_when_algolia_says_more_matched(self):
        pages = [
            {"nbHits": 3, "hits": [{"objectID": "a", "created_at_i": 30}, {"objectID": "b", "created_at_i": 20}]},
            {"nbHits": 2, "hits": [{"objectID": "b", "created_at_i": 20}, {"objectID": "c", "created_at_i": 10}]},
        ]
        served = iter(pages)
        ctx = Context(today=date(2026, 10, 1))
        with mock.patch.object(hn.http, "get_json", lambda url, **kw: next(served)):
            self.assertEqual([h["objectID"] for h in hn._walk(ctx)], ["a", "b", "c"])

    def test_walk_is_bounded_when_the_listing_never_ends(self):
        n = iter(range(10**9))

        def endless(url, **kw):
            base = next(n) * hn.PAGE_SIZE
            return {"hits": [{"objectID": str(base + i), "created_at_i": 10**9 - base - i}
                             for i in range(hn.PAGE_SIZE)]}

        ctx = Context(today=date(2026, 10, 1))
        with mock.patch.object(hn.http, "get_json", endless):
            got = sum(1 for _ in hn._walk(ctx))
        self.assertEqual(got, hn.MAX_PAGES * hn.PAGE_SIZE)
        self.assertEqual(len(ctx.warnings), 1)  # said so, did not hang

    def test_repost_key(self):
        key = lambda url: hn._post_key({"url": url, "author": "a"}, {"rest": "t"})  # noqa: E731
        self.assertEqual(key("https://www.atlasmotion.com/"), key("http://atlasmotion.com"))
        self.assertEqual(key("https://atlasmotion.com/?utm_source=hn&ref=show"), key("https://atlasmotion.com"))
        # Two videos on one platform are two posts, not a repost.
        self.assertNotEqual(key("https://www.youtube.com/watch?v=6xXRh7PC7nI"),
                            key("https://www.youtube.com/watch?v=iESOr7EGWqk"))

    def test_malformed_hits_do_not_lose_the_run(self):
        good = BY_ID["49890707"]
        junk = [
            {"objectID": "x1"},                                             # nothing but an id
            {"objectID": "x2", "title": None, "created_at_i": 1790000000},
            {"objectID": "x3", "title": "Show HN: A drone", "created_at_i": "not a number"},
            {"title": "Show HN: A drone with no id", "created_at_i": 1790000000},
            "not a dict",
            dict(good, objectID="x4", points=None, num_comments=None, author=None,
                 url="https://example-robot.dev/"),
        ]

        def fake(url, **kw):
            params = kw.get("params") or {}
            if params.get("tags") == "(show_hn,launch_hn)":
                return {"hits": junk + [good]}
            if str(params.get("tags", "")).startswith("story,author_"):
                return {"nbHits": "many"}  # wrong type: history stays unknown
            return ["unexpected"]

        ctx = Context(today=date(2026, 10, 1), lookback_days=120)
        with mock.patch.object(hn.http, "get_json", fake):
            sigs = list(hn.collect(ctx))
        for s in sigs:
            s.validate()
        by_id = {s.url.rsplit("=", 1)[1]: s for s in sigs}
        # x4 has no points or comment count: a title must not state a number
        # the source did not give, so it is not emitted.
        self.assertEqual(set(by_id), {"49890707"})
        self.assertNotIn("author_prior_stories", by_id["49890707"].metrics)
        self.assertNotIn("first HN story", by_id["49890707"].title)

    def test_listing_failure_is_a_warning_not_a_crash(self):
        def boom(url, **kw):
            raise OSError("network down")
        ctx = Context(today=date(2026, 10, 1))
        with mock.patch.object(hn.http, "get_json", boom):
            self.assertEqual(list(hn.collect(ctx)), [])
        self.assertEqual(len(ctx.warnings), 1)


if __name__ == "__main__":
    unittest.main()
