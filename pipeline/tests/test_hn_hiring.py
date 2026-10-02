"""Offline tests for the hn_hiring collector: header parsing, the thesis gate,
links, team cues, prior-post detection, strength and titles.

Everything runs against the saved Algolia response for the October 2026
thread in fixtures/hiring/ (105 top-level comments, as fetched nine hours
after the thread opened). No network: the tests that drive collect() swap
http.get_json for a fake that serves that fixture and synthetic histories.
"""

from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from antenna.collectors import hn_hiring as hh
from antenna.collectors.base import Context
from antenna.config import THESIS
from antenna.thesis import LONE_TERM_FIT

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "hiring" / "hn_algolia_whoishiring_2026-10_toplevel.json"
HITS = json.loads(FIXTURE.read_text())["hits"]
BY_ID = {h["objectID"]: h for h in HITS}
THREAD_ID = 49922569
THREAD = {"id": THREAD_ID, "title": "Ask HN: Who is hiring? (October 2026)", "ts": 1790866927,
          "created": date(2026, 10, 1)}

# Posts in the fixture, by what they are.
CHARGE = "49926244"      # Charge Robotics | Multiple Roles | ... ashby link, e-mail at chargerobotics.com
ETC = "49925468"         # etc. (Exploration Technology Corp.) | Vision Systems Engineer | ...
ETC_NAME = "Exploration Technology Corp."
KOBR = "49925283"        # ko-br | Engineering, sales, operations | ... | https://ko-br.com
SKYDIO = "49923847"
BEACON = "49923640"
MONUMENTAL = "49927073"
TANGRAM = "49927351"
FACTORY = "49926793"     # "Factory" the coding-agent company
SHEPHERD = "49922593"    # insurance; "semiconductor fabs" in a list of insured assets
ARTIFICIAL = "49928396"  # AI benchmarking; "robotics" in a list of roles
BATON = "49924087"       # "supply chain on autopilot"
DIFFUSION = "49925260"   # "software factory"
DEVIN = "49922921"       # consumer device; "robotics" in a list of backgrounds
BRAIN = "49927389"       # Brain Corp: one mention of robots, nothing else


ACME = ('Acme Robotics | Robotics Engineer | Austin, TX | <a href="https://acmerobotics.com">https://acmerobotics.com</a>'
        "<p>We build robots that weld ship hulls with industrial robots.")


def cand(oid: str) -> dict:
    c = hh.candidate(BY_ID[oid])
    assert c is not None, oid
    return c


def post(oid: str, author: str, text: str, ts: int, story: int = 111, title: str = "Ask HN: Who is hiring? (May 2026)",
         parent: int | None = None) -> dict:
    """A synthetic Algolia comment hit."""
    return {"objectID": oid, "author": author, "comment_text": text, "created_at_i": ts,
            "created_at": "2026-05-01T15:00:00Z", "story_id": story,
            "parent_id": story if parent is None else parent, "story_title": title}


class Text(unittest.TestCase):
    def test_render_unescapes_and_separates_paragraphs(self):
        raw = 'Acme | Engineer<p>We&#x27;re hiring. <a href="https:&#x2F;&#x2F;acme.io&#x2F;jobs" rel="nofollow">https:&#x2F;&#x2F;acme.io&#x2F;jobs</a>'
        self.assertEqual(hh.render(raw, "drop"), "Acme | Engineer\n\nWe're hiring.")
        self.assertEqual(hh.render(raw, "href"), "Acme | Engineer\n\nWe're hiring. https://acme.io/jobs")
        self.assertEqual(hh.links_in(raw), ["https://acme.io/jobs"])

    def test_prose_drops_links_hosts_and_emails(self):
        raw = 'Factory | Security Engineer | <a href="https://factory.com/">https://factory.com/</a><p>See factory.ai or mail jobs@factory.ai (<a href="https://x.y">here</a>). We use ASP.NET.'
        text = hh.prose(raw)
        self.assertNotIn("factory.com", text)
        self.assertNotIn("factory.ai", text)
        self.assertNotIn("@", text)
        self.assertNotIn("( )", text)
        self.assertIn("ASP.NET", text)

    def test_sentences_do_not_break_on_abbreviations(self):
        s = hh.sentences("Skydio is the leading U.S. drone company. Our drones respond to 911 calls.")
        self.assertEqual(s, ["Skydio is the leading U.S. drone company.", "Our drones respond to 911 calls."])


class Header(unittest.TestCase):
    def test_conventional_line(self):
        h = hh.parse_header(BY_ID[CHARGE]["comment_text"])
        self.assertEqual(h["name"], "Charge Robotics")
        self.assertEqual(h["role"], "Multiple Roles")
        self.assertEqual(h["location"], "SF Bay Area")

    def test_alias_in_parentheses(self):
        h = hh.parse_header(BY_ID[ETC]["comment_text"])
        self.assertEqual(h["name"], "etc.")
        self.assertEqual(h["alias"], "Exploration Technology Corp.")
        self.assertEqual(h["role"], "Vision Systems Engineer")
        self.assertEqual(h["location"], "San Francisco, CA")

    def test_batch_url_and_stage_notes_are_not_part_of_the_name(self):
        cases = {
            "VOYGR (YC W26) | Founding Product Engineer | San Francisco": ("VOYGR", "W26"),
            "Column (https://column.com) | ONSITE San Francisco, CA | Full Time": ("Column", None),
            "Shepherd (Series B) | ONSITE | San Francisco, CA": ("Shepherd", None),
            "Cargo Robotics (withcargo.com) | Menlo Park, CA | ONSITE": ("Cargo Robotics", None),
            "9 Mothers YC P26 | Robotics | Austin, ONSITE": ("9 Mothers", "P26"),
            "Snout https://snout.com/ | Multiple Engineering + Product Roles | Remote US": ("Snout", None),
        }
        for line, (name, batch) in cases.items():
            h = hh.parse_header(line)
            self.assertEqual((h["name"], h["yc_batch"]), (name, batch), line)
            self.assertIsNone(h["alias"], line)

    def test_place_first_then_company(self):
        h = hh.parse_header("Cologne, Germany | UMH | Product Engineer | Full-time | 55 - 85k EUR | ONSITE (part remote)")
        self.assertEqual(h["name"], "UMH")
        self.assertEqual(h["role"], "Product Engineer")
        self.assertEqual(h["location"], "Cologne, Germany")

    def test_name_styled_as_a_host(self):
        self.assertEqual(hh.parse_header("Spore.Bio | Senior Software Engineer | Paris, France")["name"], "Spore.Bio")

    def test_dash_separated_and_labelled_conventions(self):
        h = hh.parse_header("PrairieLearn (Remote US) — Full-Stack Software Engineer — TypeScript / Postgres")
        self.assertEqual(h["name"], "PrairieLearn")
        h = hh.parse_header("COMPANY: Tangram Vision<p>TYPE: Full-time<p>DESCRIPTION: Careers page here")
        self.assertEqual(h["name"], "Tangram Vision")

    def test_lines_that_name_no_company(self):
        for line in [
            "there used to be 800+ posts here :(",
            "Hiring: AI, Data, DevOps & Full Stack Engineers | Remote — Americas",
            "Robotics startup (STEALTH + YC S26) | Principal Mechanical + Firmware + SWE | SF Bay Area",
            "DuckDuckGo - we are looking for candidates that are excited to join us",
            "Location: Charleston SC\nRemote: Yes\nWilling to relocate: No",
            "Remote | Full-time | Senior Engineer",
        ]:
            self.assertIsNone(hh.parse_header(line)["name"], line)

    def test_a_job_title_or_a_description_is_never_the_company(self):
        for line in ["Software Engineer -- Infrastructure | Oslo, Norway | Onsite",
                     "Founding Engineer Wanted | Defense Startup | Munich/Remote",
                     "Drone startup | Product Engineer (Typescript) | London, UK",
                     "Senior Backend Engineer | Remote (EU) | Full-time",
                     "Stealth startup in drones | Embedded Engineer | Austin"]:
            self.assertIsNone(hh.parse_header(line)["name"], line)
        # Words that only look like seniority are still fine in a name.
        self.assertEqual(hh.parse_header("Lead Robotics | Controls Engineer | Austin, TX")["name"], "Lead Robotics")

    def test_a_paragraph_is_not_a_role(self):
        long_field = "Want to join a lean, ambitious startup? " * 4
        h = hh.parse_header(f"Trace Labs | Remote (USA) | {long_field}")
        self.assertEqual(h["name"], "Trace Labs")
        self.assertIsNone(h["role"])

    def test_fixture_parse_rate(self):
        named = [h for h in HITS if hh.parse_header(h["comment_text"])["name"]]
        # The source card measured 97 of 105 by a looser rule; this parser names 95.
        self.assertGreaterEqual(len(named), 93)
        self.assertLessEqual(len(named), 99)


class Location(unittest.TestCase):
    def test_clean_location(self):
        cases = {
            "ONSITE San Francisco, CA": "San Francisco, CA",
            "Onsite (London, UK)": "London, UK",
            "3 days hybrid in San Carlos, CA": "San Carlos, CA",
            "Brooklyn, NY (hybrid, 3 days/week onsite)": "Brooklyn, NY",
            "Remote (EU) / on-site in Bremen, Germany": "Bremen, Germany",
            "Sydney, Australia (ON-SITE)": "Sydney, Australia",
            "Isny im Allgäu, Germany": "Isny im Allgäu, Germany",
            "Hawthorne, CA (Los Angeles County)": "Hawthorne, CA (Los Angeles County)",
            "San Francisco, CA / London, UK / Cairo, Egypt": "San Francisco, CA / London, UK / Cairo, Egypt",
            "Remote (US preferred)": None,
            "REMOTE (US/Canada only)": None,
            "Remote": None,
            "Full-time": None,
            "ONSITE": None,
            "Remote (US: Atlanta, Austin, SF, Seattle) + APAC": None,
            "REMOTE (US/Canada), then ONSITE SF from fall 2026": None,
        }
        for field, want in cases.items():
            self.assertEqual(hh.clean_location(field), want, field)


class Links(unittest.TestCase):
    def test_company_host(self):
        self.assertEqual(hh.company_host("https://www.skydio.com/careers#job-listing"), "skydio.com")
        self.assertEqual(hh.company_host("https://jobs.cable.energy/roles/3e2c"), "cable.energy")
        self.assertEqual(hh.company_host("careers.foo.co.uk"), "foo.co.uk")
        # Always the registrable domain: the resolver keys on it exactly, so a subdomain would split a company.
        self.assertEqual(hh.company_host("https://people.blackshark.ai/jobs"), "blackshark.ai")
        self.assertEqual(hh.company_host("jobboerse.iabg.de"), "iabg.de")
        self.assertEqual(hh.company_host("https://shop.acme.co.za/x"), "acme.co.za")
        for url in ["https://jobs.ashbyhq.com/charge-robotics", "https://boards.greenhouse.io/oklo",
                    "https://jobs.lever.co/shieldai", "https://www.linkedin.com/company/x",
                    "https://www.ycombinator.com/companies/x/jobs", "https://youtu.be/ix0oMgqTfko",
                    "https://ats.rippling.com/etc/jobs/1", "https://app.dover.com/jobs/ko-br",
                    "https://docs.google.com/document/d/1", "https://sbir.nasa.gov/x", "gmail.com",
                    # Boards, press and mail hosts only this module's own list knows.
                    "https://acme.wd1.myworkdayjobs.com/careers", "https://www.indeed.com/cmp/acme",
                    "https://techcrunch.com/2026/09/01/acme-raises", "careers@hey.com", "https://lnkd.in/abc",
                    "https://robotics.mit.edu/lab",
                    # A careers microsite under a job-board ending.
                    "https://flix.careers/job/?jobid=8729129002"]:
            self.assertIsNone(hh.company_host(url), url)
        self.assertEqual(hh.company_host("https://mechanize.work/"), "mechanize.work")  # a company's real domain

    def test_name_matches_host(self):
        yes = [("Charge Robotics", "chargerobotics.com"), ("Lumen Labs", "lumenresearch.co"),
               ("Cargo Robotics", "withcargo.com"), ("UMH", "umh.app"), ("9 Mothers", "9mothers.com"),
               ("ko-br", "ko-br.com"), ("CABLE", "cable.energy"), ("Harmony AI", "tryharmony.ai"),
               ("NODA AI", "nodaintelligence.ai"), ("Spore.Bio", "spore.bio"), ("Brightcore Energy", "brightcore.com")]
        no = [("Apex Space", "spacenews.com"), ("Charge Robotics", "therobotreport.com"),
              ("Tangram Vision", "nasa.gov"), ("Silkline", "machinalabs.ai"), ("Lumen Labs", "labs.google")]
        for name, host in yes:
            self.assertTrue(hh.name_matches_host(name, host), (name, host))
        for name, host in no:
            self.assertFalse(hh.name_matches_host(name, host), (name, host))

    def test_a_job_board_that_shares_a_word_with_the_name_is_not_the_domain(self):
        self.assertFalse(hh.name_matches_host("Nordic Semiconductor", "nordictechjobs.com"))
        self.assertFalse(hh.name_matches_host("Pinterest", "pinterestcareers.com"))
        self.assertTrue(hh.name_matches_host("Talent Robotics", "talentrobotics.com"))  # the word is the company's own
        post_html = ('Nordic Semiconductor | Full Stack Developer | Oslo<p>Apply: '
                     '<a href="https://www.nordictechjobs.com/job/123">https://www.nordictechjobs.com/job/123</a>')
        self.assertIsNone(hh.pick_domain("Nordic Semiconductor", None, post_html))

    def test_domain_comes_only_from_a_host_that_matches_the_name(self):
        self.assertEqual(cand(CHARGE)["domain"], "chargerobotics.com")  # from max@chargerobotics.com
        self.assertEqual(cand(KOBR)["domain"], "ko-br.com")
        self.assertEqual(cand(SKYDIO)["domain"], "skydio.com")
        self.assertEqual(cand(BEACON)["domain"], "beaconai.co")
        self.assertEqual(cand(MONUMENTAL)["domain"], "monumental.co")
        self.assertEqual(cand(TANGRAM)["domain"], "tangramvision.com")
        self.assertIsNone(cand(ETC)["domain"])  # the only link is its Rippling job page

    def test_the_host_the_post_uses_most_is_the_domain(self):
        # One company, two hosts that both match its name: the one carrying the paper and the e-mail address wins,
        # so this post and a later one that only links lumenresearch.co come out under one domain.
        text = ('Lumen Labs | Simulation Engineer | REMOTE<p>Lumen ( <a href="https://lumenresearch.ai">'
                'https://lumenresearch.ai</a> ) is building the cognitive layer for physical AI. White paper: '
                '<a href="http://lumenresearch.co/paper">http://lumenresearch.co/paper</a><p>Email hi@lumenresearch.co')
        self.assertEqual(hh.host_mentions(text), {"lumenresearch.ai": 1, "lumenresearch.co": 2})
        self.assertEqual(hh.pick_domain("Lumen Labs", None, text), "lumenresearch.co")
        # A tie goes to the host that appears first.
        tie = 'Acme | Engineer | <a href="https://acme.io">https://acme.io</a><p>See https://acme.dev'
        self.assertEqual(hh.pick_domain("Acme", None, tie), "acme.io")

    def test_ats_patterns(self):
        cases = {
            "https://jobs.ashbyhq.com/Charge-Robotics": ("ashby", "charge-robotics"),
            "https://jobs.ashbyhq.com/9-mothers/abc": ("ashby", "9-mothers"),
            "https://boards.greenhouse.io/valaratomics/jobs/123": ("greenhouse", "valaratomics"),
            "https://job-boards.greenhouse.io/oklo": ("greenhouse", "oklo"),
            "https://boards.greenhouse.io/embed/job_board?for=apptronik": ("greenhouse", "apptronik"),
            "https://jobs.lever.co/ShieldAI/123": ("lever", "ShieldAI"),  # Lever slugs are case-sensitive
            "https://apply.workable.com/sumble-inc/": ("workable", "sumble-inc"),
            "https://ats.rippling.com/etc/jobs/3300a2a6": ("rippling", "etc"),
            "https://ats.rippling.com/en-US/boom-supersonic/jobs": ("rippling", "boom-supersonic"),
            "https://jobs.gem.com/silkline/abc": ("gem", "silkline"),
            "https://app.dover.com/jobs/ko-br": ("dover", "ko-br"),
            "https://valkyrie-aero.breezy.hr/p/1": ("breezy", "valkyrie-aero"),
            "https://greenzie.applytojob.com/apply": ("jazzhr", "greenzie"),
        }
        for url, want in cases.items():
            self.assertEqual(hh.ats_refs([url]), [want], url)
        for url in ["https://app.dover.com/apply/98147191-8319-43cd-911f-98f678be33be",
                    "https://boards.greenhouse.io/embed/job_board/js", "https://skydio.com/careers"]:
            self.assertEqual(hh.ats_refs([url]), [], url)

    def test_ats_from_fixture_posts(self):
        self.assertEqual(cand(CHARGE)["ats"], ("ashby", "charge-robotics"))
        self.assertEqual(cand(ETC)["ats"], ("rippling", "etc"))
        self.assertEqual(cand(KOBR)["ats"], ("dover", "ko-br"))
        self.assertIsNone(cand(SKYDIO)["ats"])

    def test_several_boards_in_one_post_need_a_name_match(self):
        urls = ["https://jobs.ashbyhq.com/gleanwork", "https://boards.greenhouse.io/esri"]
        self.assertIsNone(hh.pick_ats("CareerJumpShip", None, urls))
        self.assertEqual(hh.pick_ats("Esri", None, urls), ("greenhouse", "esri"))


class Thesis(unittest.TestCase):
    def test_multi_word_terms_match(self):
        terms = {h["term"] for h in hh.term_hits(
            "the cognitive layer for physical AI; US Department of Defense missions; a launch vehicle; "
            "energy storage and a ground-station network")}
        for t in ("physical ai", "department of defense", "launch vehicle", "energy storage", "ground station"):
            self.assertIn(t, terms)

    def test_plurals_count_as_the_singular(self):
        terms = [h["term"] for h in hh.term_hits("Robots, drones and satellites. Robotics for power plants.")]
        self.assertEqual(terms, ["robot", "drone", "satellite", "robotic", "power plant"])
        # A plural in -ies too: the shared pattern reaches it, so this module keeps no list of its own.
        self.assertEqual([h["term"] for h in hh.term_hits("a network of robotic microfactories and foundries")],
                         ["robotic", "microfactory", "foundry"])
        self.assertEqual([h["term"] for h in hh.term_hits("factories that build batteries")], ["factory", "battery"])

    def test_tables_only_name_terms_the_shared_list_has(self):
        # A renamed or removed thesis term must not leave a dead entry behind.
        vocabulary = {t for groups in THESIS.values() for terms in groups.values() for t in terms}
        for name, table in (("_STEMS", hh._STEMS), ("_STEMS values", hh._STEMS.values()),
                            ("_SKIP_TERMS", hh._SKIP_TERMS)):
            self.assertEqual(set(table) - vocabulary, set(), name)

    def test_words_that_mean_something_else_in_a_hiring_post(self):
        # "SDR" and "PLC" are no longer thesis terms at all; the rest are skipped by this module.
        for text in ["Hiring: Founding Designer, SDR, Head of Sales", "3+ years of BDR/SDR experience",
                     "our PLC is registered in London", "a high energy team", "ISR and SSR with Next.js",
                     "meets our DoD (definition of done)", "we all chip in"]:
            self.assertEqual(hh.term_hits(hh.blank_idioms(text.replace("DoD", "dod"))), [], text)
        self.assertEqual([h["term"] for h in hh.term_hits("multiple DoD programs")], ["dod"])

    def test_energy_counts_only_beside_a_strong_energy_term(self):
        def terms(text: str) -> list[str]:
            return [h["term"] for h in hh.term_hits(hh.blank_idioms(text))]

        # On its own the word is as often a mood as a sector.
        self.assertEqual(terms("Energy experience is a plus. We sell to finance, energy and healthcare."), [])
        # Beside a battery it is the shared context term.
        self.assertEqual(terms("We operate batteries on site. Our energy specialists ship code."), ["battery", "energy"])
        # A longer term still consumes the word, and the mood is a mood even at a battery company.
        self.assertEqual(terms("energy storage against directed energy"), ["energy storage", "directed energy"])
        self.assertEqual(terms("We make batteries. A high-energy team with great energy."), ["battery"])
        # A context term in another sector does not unlock it.
        self.assertEqual(terms("energy for the grid and for the Navy"), ["grid", "navy"])

    def test_a_plural_is_not_a_second_term(self):
        # "batteries" beside "battery" is one term, so the lone-term rule applies to it ...
        lone = hh.evidence("Engineer", "Acme", "We sell batteries. Every battery is tested. Our batteries last.")
        self.assertEqual(lone["terms"], ["battery"])
        self.assertGreaterEqual(lone["units"], hh.MIN_UNITS)
        self.assertLessEqual(lone["fit"], LONE_TERM_FIT)
        # ... and a company that says what the batteries are for clears it.
        utility = hh.evidence("Software Engineer", "Acme",
                              "We're a battery-backed electricity company. We install and operate batteries at small "
                              "businesses and optimise those batteries against wholesale prices. Engineers and energy "
                              "specialists all ship code.")
        self.assertEqual(utility["terms"], ["battery", "energy"])
        self.assertGreaterEqual(utility["fit"], hh.MIN_FIT)
        self.assertGreaterEqual(utility["units"], hh.MIN_UNITS)
        self.assertTrue(utility["core_ok"])

    def test_longer_term_wins_over_the_word_inside_it(self):
        terms = [h["term"] for h in hh.term_hits("a counter-drone aircraft")]
        self.assertEqual(terms, ["counter-drone"])
        self.assertEqual([h["term"] for h in hh.term_hits("missile defense")], ["missile defense"])

    def test_a_lone_ambiguous_term_stays_under_the_shared_gate(self):
        # The shared rule: one matched term passes on its own only if it can hardly mean anything but hardware.
        factory = hh.evidence("Engineer", "Acme", "We run a factory in Ohio. The factory never stops. Our factory is hiring.")
        self.assertEqual(factory["terms"], ["factory"])
        self.assertGreaterEqual(factory["units"], hh.MIN_UNITS)
        self.assertLessEqual(factory["fit"], LONE_TERM_FIT)
        self.assertLess(factory["fit"], hh.SHARED_FLOOR)
        robots = hh.evidence("Engineer", "Acme", "We build robots in Ohio. The robots never stop. Our robots are hiring.")
        self.assertEqual(robots["terms"], ["robot"])
        self.assertGreaterEqual(robots["fit"], hh.MIN_FIT)
        # A second term lifts the cap.
        both = hh.evidence("Engineer", "Acme", "We run a factory in Ohio. The factory does CNC machining.")
        self.assertGreaterEqual(both["fit"], hh.MIN_FIT)

    def test_the_text_the_signal_carries_must_clear_the_shared_gate(self):
        lead = "Acme | Engineer | Dayton, OH<p>We run a factory in Ohio. The factory never stops. Our factory is hiring.<p>"
        tail = "<p>We also build robots and drones."
        short = hh.candidate(post("g1", "a", lead + "Benefits are good. " * 20 + tail, NOW))
        long = hh.candidate(post("g2", "a", lead + "Benefits are good. " * 300 + tail, NOW))
        self.assertTrue(hh.passes_gate(short))
        # Same evidence, but the second and third terms sit beyond what Signal.text keeps: downstream the
        # classifier would see a lone "factory".
        self.assertGreater(len(long["text"]), hh.TEXT_CHARS)
        self.assertGreaterEqual(long["evidence"]["fit"], hh.MIN_FIT)
        self.assertFalse(hh.passes_gate(long))

    def test_idioms_are_blanked(self):
        for text in [
            "a factory for building software factories", "enable supply chain on autopilot",
            "PostHog makes your product self-driving", "sensor fusion work", "at the Brooklyn Navy Yard",
            "$155K–$185K CAD", "high autonomy and ownership", "fully autonomous underwriting",
            "an army of agents", "not a ticket factory", "enrichment pipelines that turn raw data into alerts",
            "you're adding your profile to our radar", "keep us on your radar", "we work in spaces like fintech",
            # From the shared false-friend list.
            "hydrogen peroxide", "batteries included", "a satellite office in Austin", "robots.txt rules",
            "the solar wind", "self-defense classes", "model fusion and Fusion 360",
        ]:
            blanked = hh.blank_idioms(text)
            strong = [h["term"] for h in hh.term_hits(blanked) if h["weight"] >= 1.0]
            self.assertEqual(strong, [], text)
        # The same words in their own sense still count.
        self.assertTrue(hh.term_hits(hh.blank_idioms("our factory in El Segundo builds self-driving trucks")))
        for text, want in (("Our radar tracks small drones", ["radar", "drone"]),
                           ("firmware updates to our radar hardware", ["radar"]),
                           ("servicing satellites in space", ["satellite", "in-space"])):
            self.assertEqual([h["term"] for h in hh.term_hits(hh.blank_idioms(text)) if h["weight"] >= 1.0], want, text)
        self.assertIn("enrichment", {h["term"] for h in hh.term_hits("uranium enrichment plant")})

    def test_a_name_that_is_a_thesis_word_is_not_evidence(self):
        text = "Factory builds software development agents. I'm building out Factory's Security team."
        self.assertEqual(hh.term_hits(hh.blank_idioms(text, "Factory")), [])
        self.assertEqual(hh.term_hits(hh.blank_idioms(text, "Factory AI")), [])
        # A descriptive name is left alone.
        self.assertTrue(hh.term_hits(hh.blank_idioms("Charge Robotics is hiring", "Charge Robotics")))

    def test_enumerations(self):
        def listed(text: str, word: str) -> bool:
            i = text.index(word)
            return hh.is_enumerated(text, i, i + len(word))

        self.assertTrue(listed("Our customers are in defense, intelligence, security and critical infrastructure", "defense"))
        self.assertTrue(listed("The boom (data centers, semiconductor fabs, renewable energy assets) has to be insured", "semiconductor"))
        self.assertTrue(listed("hardware end to end (consumer, wearable, medical or robotics), have 8+ years", "robotics"))
        self.assertTrue(listed("serving sectors like pharmaceuticals, energy, and manufacturing", "manufacturing"))
        self.assertFalse(listed("We make robots that autonomously construct buildings, starting with bricklaying.", "robots"))
        self.assertFalse(listed("++ Lead Autonomy/Robotics Engineer [Tech Stack: C++, Python, iOT]", "Robotics"))
        self.assertFalse(listed("We connect the systems inside a factory (ERP, MES, PLCs, spreadsheets, etc) into one layer", "factory"))
        # A company listing its own products is describing itself.
        self.assertFalse(listed("We build drones, radios, and ground stations for the Navy", "drones"))
        self.assertFalse(listed("We build drones, radios, and ground stations for the Navy", "ground stations"))
        # A list of markets or examples after the verb's object is still a list.
        self.assertTrue(listed("Runway builds AI world models - general-purpose simulators for robotics, science, and storytelling", "robotics"))
        self.assertTrue(listed("We're building AI that speeds up permitting for projects like power plants, transmission lines, and data centers", "power plants"))
        self.assertTrue(listed("Seeq creates analytics software for industrial process data, serving sectors like pharmaceuticals, energy, and manufacturing", "manufacturing"))
        # Two lists in one clause: the second one starts where its lead-in ends.
        clause = ("surfacing the networks, relationships, and financial ties behind companies to support national security, "
                  "compliance, and regulatory oversight")
        self.assertTrue(listed(clause, "national security"))
        # Not a second list: the term does not close its item, or too few items follow it.
        self.assertFalse(listed("The platform ingests logs, metrics, and traces so that every one of our welding robots "
                                "stays up, safe and productive", "robots"))
        self.assertFalse(listed("We ship firmware, tooling, and the software that runs our robots, daily", "robots"))

    def test_a_term_only_ever_in_lists_counts_half_and_a_listed_repeat_counts_as_a_repeat(self):
        only_listed = hh.evidence("Trust & Safety Engineer", "Runway",
                                  "Runway builds AI world models - general-purpose simulators for robotics, science, and storytelling.")
        self.assertLess(only_listed["units"], hh.MIN_UNITS)
        self.assertFalse(only_listed["core_ok"])
        repeated = hh.evidence("Staff Engineer", "Viam",
                               "Viam makes robotics as programmable as software. We build a unified software layer for "
                               "robotics, connected machines, and devices.")
        self.assertGreaterEqual(repeated["units"], hh.MIN_UNITS)
        self.assertTrue(repeated["core_ok"])

    def test_fixture_keeps_the_physical_world_companies(self):
        kept = {c["name"] for c in filter(None, map(hh.candidate, HITS)) if hh.passes_gate(c)}
        self.assertEqual(kept, {"Tangram Vision", "MONUMENTAL", "Charge Robotics", "Exploration Technology Corp.",
                                "ko-br", "Skydio", "Beacon AI"})

    def test_fixture_drops_lookalikes(self):
        for oid in (FACTORY, SHEPHERD, ARTIFICIAL, BATON, DIFFUSION, DEVIN, BRAIN):
            c = cand(oid)
            self.assertFalse(hh.passes_gate(c), c["name"])
        # Each for its own reason.
        self.assertEqual(cand(FACTORY)["evidence"]["units"], 0.0)       # the name is the only "factory"
        self.assertLess(cand(SHEPHERD)["evidence"]["units"], 1.0)       # listed, and "autonomous underwriting"
        self.assertFalse(cand(ARTIFICIAL)["evidence"]["core_ok"])       # "robotics" only in a list of roles
        self.assertLess(cand(BRAIN)["evidence"]["units"], hh.MIN_UNITS)  # one mention, nothing else

    def test_incumbents_and_public_bodies_are_not_candidates(self):
        body = "<p>We build robots, drones and autonomous systems for the military. Robotics engineers wanted."
        for header in ["The Boeing Company | Robotics Engineer | Seattle, WA", "SpaceX/Starlink | Robotics Engineer | Redmond, WA",
                       "Apple | Robotics Engineer | Austin, TX", "National Robotics Engineering Center (NREC) | Robotics | Pittsburgh, PA"]:
            self.assertIsNone(hh.candidate(post("x", "a", header + body, NOW)), header)
        for header in ["Applied Intuition | Robotics Engineer | Mountain View, CA", "Fordham Robotics | Robotics Engineer | NYC"]:
            self.assertIsNotNone(hh.candidate(post("x", "a", header + body, NOW)), header)
        # What the post says of itself.
        air_force = ("76 Software Engineering Group | Oklahoma City, OK | ONSITE<p>76 SWEG is a civilian software engineering "
                     "organization operating under the United States Air Force. We support military platforms and robots.")
        institute = ("CESMII | Software Developer | Remote<p>CESMII is the United States' Manufacturing USA Institute "
                     "for smart manufacturing. We help manufacturers connect their factory data.")
        self.assertIsNone(hh.candidate(post("x", "a", air_force, NOW)))
        self.assertIsNone(hh.candidate(post("x", "a", institute, NOW)))
        # A company that came out of an institute is still a company, and "foundation models" is not a foundation.
        spinout = ("Proxima Fusion | Engineer | Munich<p>We are a spin-out from the Max Planck Institute for Plasma Physics "
                   "building a stellarator fusion power plant.")
        data = ("xdof | Robotics Engineers | SF<p>High-quality training data is the bottleneck. We're building the foundation "
                "behind the foundation models for general-purpose robots.")
        self.assertIsNotNone(hh.candidate(post("x", "a", spinout, NOW)))
        self.assertIsNotNone(hh.candidate(post("x", "a", data, NOW)))

    def test_a_blockchain_on_risc_v_is_not_a_chip_company(self):
        chain = ("Unto Labs | San Francisco, CA | ONSITE<p>Unto Labs is developing a Layer-1 blockchain. Our runtime conforms "
                 "to the RISC-V specification, moving away from domain-specific VMs. RISC-V toolchains, RISC-V everywhere.")
        c = hh.candidate(post("x", "a", chain, NOW))
        self.assertFalse(hh.passes_gate(c))

    def test_self_description_counts_for_more_than_a_mention(self):
        described = hh.evidence("Multiple Roles", "Apex Space",
                                "Apex Space is building standardized spacecraft platforms to enable mass-production of spacecraft.")
        mentioned = hh.evidence("Engineer", "Acme", "Great team.\n\n" + "x " * 400 + "Experience with spacecraft is a plus.")
        self.assertGreaterEqual(described["units"], hh.MIN_UNITS)
        self.assertTrue(described["core_ok"])
        self.assertLess(mentioned["units"], hh.MIN_UNITS)
        self.assertFalse(mentioned["core_ok"])

    def test_institutions_and_unnamed_posts_are_not_candidates(self):
        self.assertIsNone(hh.candidate(post("u1", "a", "Johns Hopkins Applied Physics Laboratory (JHU APL) | Graphics Engineer | Laurel, MD<p>missile defense simulation", 1)))
        self.assertIsNone(hh.candidate(post("u2", "a", "Robotics and AI Institute | Intern | Zürich<p>robotics systems", 1)))
        self.assertIsNone(hh.candidate(post("u3", "a", "Robotics startup (STEALTH + YC S26) | Firmware | SF<p>wearable robotic devices", 1)))
        self.assertIsNone(hh.candidate({"objectID": "u4", "author": "a", "comment_text": None}))


class RecoveredName(unittest.TestCase):
    def test_name_is_recovered_only_when_a_link_spells_it(self):
        raw = ('Beacon AI builds intelligent systems that make aviation safer and more autonomous. We’ve completed '
               'multiple DoD programs.<p>++ Lead Autonomy/Robotics Engineer<p>Join us at beaconai.co/careers')
        c = hh.candidate(post("r1", "beaconai", raw, 1))
        self.assertEqual((c["name"], c["domain"]), ("Beacon AI", "beaconai.co"))
        self.assertIsNone(hh.candidate(post("r2", "x", "We build great robots. Apply at https://jobs.ashbyhq.com/acme", 1)))


class Cues(unittest.TestCase):
    def test_team_size_and_stage(self):
        c = hh.team_cues("Team of two ex-founders — you'd be #3. Pre-seed, runway well into 2027.")
        self.assertEqual((c["team_size"], c["employee_number"], c["stage"]), (2, 3, "pre-seed"))
        c = hh.team_cues("VC-backed by General Catalyst, ~12 engineers and ~18 employees total. See all 20 open roles")
        self.assertEqual((c["team_size"], c["open_roles"]), (18, 20))
        self.assertEqual(hh.team_cues("We're live with real customers. Team of six, backed by Systemiq.")["team_size"], 6)
        self.assertEqual(hh.team_cues("We are a 12-person team in Austin.")["team_size"], 12)
        self.assertEqual(hh.team_cues("still a small team of fewer than 20 people")["team_size_upper"], 20)

    def test_sub_teams_and_ranks_are_not_the_headcount(self):
        self.assertNotIn("team_size", hh.team_cues("You will join a team of 5 engineers inside Ryder."))
        self.assertNotIn("team_size", hh.team_cues("a core member of a 300+ person world-class engineering and research team"))
        self.assertNotIn("team_size", hh.team_cues("mentoring and growing a small team of talented engineers (typically 1–2 to start)"))
        c = hh.team_cues("You'd be one of the first ~10 employees, owning transmissions.")
        self.assertNotIn("team_size", c)
        self.assertEqual(c["among_first_employees"], 10)

    def test_roles_and_funding(self):
        c = hh.team_cues("This is employee #1. We've raised a $1.5M pre-seed. Founding Hardware Lead; firmware in C.")
        self.assertEqual((c["employee_number"], c["stage"], c["funding_stated"], c["funding_currency"]),
                         (1, "pre-seed", 1.5e6, "$"))
        self.assertEqual(hh.team_cues("Return recently raised €300M to support its growth")["funding_currency"], "€")
        self.assertTrue(c["founding_role"] and c["hardware_roles"] and c["says_early"])
        self.assertTrue(hh.team_cues("You'd be our first engineering hire.")["first_hire"])
        self.assertEqual(hh.team_cues("I'm Gio, CTO at Whistle Robotics.")["poster_role"], "CTO")
        self.assertEqual(hh.team_cues("I’m the Co-Founder and CEO of Sonibel Instruments.")["poster_role"], "Co-Founder and CEO")
        self.assertEqual(hh.team_cues("Hi HN! We’re a YC-backed, Series-A startup building robots")["stage"], "series a")
        self.assertNotIn("stage", hh.team_cues("We seed the database with random data."))

    def test_a_role_is_the_posters_only_at_this_company(self):
        self.assertEqual(hh.team_cues("I'm Gio, CTO at Whistle Robotics.", "Whistle Robotics")["poster_role"], "CTO")
        self.assertEqual(hh.team_cues("I'm Daniel, co-founder. Lumen Labs is building", "Lumen Labs")["poster_role"], "co-founder")
        self.assertEqual(hh.team_cues("I'm Roman, co-founder of 9 Mothers. We", "9 Mothers")["poster_role"], "co-founder")
        # A recruiter who founded the agency is not the founder of the client.
        self.assertNotIn("poster_role", hh.team_cues("I'm the founder of Acme Recruiting, hiring for Globex.", "Globex"))
        self.assertNotIn("poster_role", hh.team_cues("I am the CEO's chief of staff.", "Globex"))
        self.assertNotIn("poster_role", hh.team_cues("I'm a founder-friendly recruiter.", "Globex"))

    def test_seed_is_a_stage_only_as_a_stage(self):
        self.assertNotIn("says_early", hh.team_cues("We seed the database with random data."))
        self.assertTrue(hh.team_cues("We closed a seed round last month.")["says_early"])

    def test_stated_headcount_floor(self):
        self.assertEqual(hh.team_cues("While we employ over 130 people, our teams are deliberately small.")["team_size"], 130)
        big = hh.team_cues("a core member of a 300+ person world-class engineering and research team")
        self.assertNotIn("team_size", big)  # one department, not the company
        self.assertTrue(hh.is_established(big))  # but a company with a 300-person department is not early
        self.assertTrue(hh.is_established({"long_history": True}))

    def test_established(self):
        self.assertTrue(hh.is_established(hh.team_cues("First spin-out of Max Planck, €650M+ raised, teams in Munich")))
        self.assertTrue(hh.is_established(hh.team_cues("Ryder acquired Baton in 2022.")))
        self.assertTrue(hh.is_established({"team_size": 300}))
        self.assertTrue(hh.is_established({"stage": "series c"}))
        self.assertFalse(hh.is_established(hh.team_cues("Team of six. Seed round led by Example Capital.")))


NOW = 1_790_000_000  # late September 2026
DAY = 86400


class PriorPosts(unittest.TestCase):
    def setUp(self):
        hh._FACTS.clear()
        self.me = post("p-now", "founder1", "Acme Robotics | Robotics Engineer | Austin, TX | https://acmerobotics.com"
                       "<p>We build robots that weld ship hulls with industrial robots.", NOW, story=999,
                       title="Ask HN: Who is hiring? (October 2026)")
        self.cand = hh.candidate(self.me)
        self.assertEqual(self.cand["domain"], "acmerobotics.com")

    def prior(self, author=(), phrase=(), scan=(), domain=(), distinctive=True):
        return hh.prior_posts(self.cand, 999, list(author), list(phrase), list(scan),
                              distinctive=distinctive, domain_hits=list(domain))

    def test_nothing_before(self):
        out = self.prior(author=[self.me], phrase=[self.me])
        self.assertEqual((out["prior_threads"], out["loose_mentions"], out["ambiguous_author_posts"]), (0, 0, 0))
        self.assertIsNone(out["first_seen"])

    def test_header_match_by_anyone_counts_once_per_thread(self):
        a = post("p1", "someone", "Acme Robotics | Welder | Austin", 1_782_900_000, story=101)  # 2026-07-01
        b = post("p2", "someone", "ACME ROBOTICS | Engineer | Austin", 1_782_900_500, story=101)
        c = post("p3", "other", "Acme Robotics (YC S25) | Engineer", NOW - 40 * DAY, story=102)
        out = self.prior(phrase=[a, b, c])
        self.assertEqual(out["prior_threads"], 2)
        self.assertEqual(out["first_seen"], date(2026, 7, 1))

    def test_same_site_under_a_shorter_name_counts(self):
        earlier = post("p4", "cofounder", 'Acme | Engineer | <a href="https://acmerobotics.com">https://acmerobotics.com</a>', NOW - 60 * DAY, story=101)
        self.assertEqual(self.prior(domain=[earlier])["prior_threads"], 1)
        # Another company that merely links the site is not a prior post.
        other = post("p5", "x", 'Globex | Engineer | https://globex.io<p>We integrate with https://acmerobotics.com', NOW - 60 * DAY, story=103)
        self.assertEqual(self.prior(domain=[other])["prior_threads"], 0)

    def test_same_author_mention_counts_only_for_a_distinctive_name(self):
        thanks = post("p6", "founder1", "Thank you HN! Acme Robotics is still hiring mechanical engineers.", NOW - 60 * DAY, story=101)
        self.assertEqual(self.prior(author=[thanks])["prior_threads"], 1)
        hh._FACTS.clear()
        self.assertEqual(self.prior(author=[thanks], distinctive=False)["prior_threads"], 0)

    def test_other_peoples_mentions_block_a_first_claim_without_being_counted(self):
        drop = post("p7", "rival", "Globex | Engineer | Remote<p>Our founders come from Acme Robotics.", NOW - 60 * DAY, story=101)
        out = self.prior(phrase=[drop])
        self.assertEqual((out["prior_threads"], out["loose_mentions"]), (0, 1))

    def test_a_longer_name_by_someone_else_is_doubt_not_a_prior_post(self):
        # "Acme Robotics Labs" may be another company. It is not counted, and it blocks a "first" claim,
        # whether or not the name was distinctive enough to search.
        longer = post("p20", "stranger", "Acme Robotics Labs | Engineer | Boston", NOW - 60 * DAY, story=101)
        for distinctive, source in ((True, {"phrase": [longer]}), (False, {"scan": [longer]})):
            hh._FACTS.clear()
            out = self.prior(distinctive=distinctive, **source)
            self.assertEqual((out["prior_threads"], out["loose_mentions"]), (0, 1), distinctive)
        # The same poster extending the name ("Acme Robotics Defense | ...") is the same company.
        hh._FACTS.clear()
        own = post("p21", "founder1", "Acme Robotics Defense | Engineer | Austin", NOW - 60 * DAY, story=101)
        self.assertEqual(self.prior(author=[own])["prior_threads"], 1)
        # A separator or a lower-case word after the name is not a longer name.
        self.assertEqual(hh.opens_with("Acme Robotics", "Acme Robotics - Autonomy Team | Austin"), "exact")
        self.assertEqual(hh.opens_with("Acme Robotics", "Acme Robotics is hiring a welder"), "exact")
        self.assertEqual(hh.opens_with("Acme Robotics", "Acme Robotics YC S25 | Engineer"), "exact")
        self.assertEqual(hh.opens_with("Acme Robotics", "Acme Robotics Labs | Engineer"), "extended")
        self.assertIsNone(hh.opens_with("Acme", "Acmecorp | Engineer"))

    def test_same_name_on_another_site_by_someone_else_is_doubt(self):
        twin = post("p22", "stranger", 'Acme Robotics | Engineer | <a href="https://acme-robotics.de">https://acme-robotics.de</a>',
                    NOW - 60 * DAY, story=101)
        out = self.prior(phrase=[twin])
        self.assertEqual((out["prior_threads"], out["loose_mentions"]), (0, 1))
        # The same header with no site of its own, or on our site, is a prior post.
        hh._FACTS.clear()
        plain = post("p23", "stranger", "Acme Robotics | Engineer | Austin", NOW - 60 * DAY, story=101)
        self.assertEqual(self.prior(phrase=[plain])["prior_threads"], 1)
        # A second host the current post uses for itself is not "another site".
        hh._FACTS.clear()
        self.cand["own_hosts"] = {"acmerobotics.com", "acme-robotics.de"}
        self.assertEqual(self.prior(phrase=[twin])["prior_threads"], 1)

    def test_same_author_mention_under_another_companys_header_does_not_count(self):
        old_job = post("p24", "founder1", "Globex | Engineer | Remote<p>We supply welding cells to Acme Robotics.",
                       NOW - 60 * DAY, story=101)
        out = self.prior(author=[old_job])
        self.assertEqual((out["prior_threads"], out["loose_mentions"]), (0, 1))

    def test_posts_and_threads_are_counted_apart(self):
        a = post("p25", "someone", "Acme Robotics | Welder | Austin", NOW - 60 * DAY, story=101)
        b = post("p26", "someone", "Acme Robotics | Engineer | Austin", NOW - 59 * DAY, story=101)
        out = self.prior(phrase=[a, b], author=[a])
        self.assertEqual((out["prior_threads"], out["prior_post_count"]), (1, 2))

    def test_other_employers_of_the_same_author_do_not_count(self):
        old = post("p8", "founder1", "Newton | Finance Coordinator | Toronto, Canada", NOW - 90 * DAY, story=50)
        out = self.prior(author=[old])
        self.assertEqual((out["prior_threads"], out["author_prior_hiring_posts"], out["ambiguous_author_posts"]), (0, 1, 0))

    def test_recent_unnamed_post_by_the_author_is_ambiguous_an_old_one_is_not(self):
        recent = post("p9", "founder1", "We're still hiring, see my profile.", NOW - 30 * DAY, story=101)
        old = post("p10", "founder1", "Toronto - web developers wanted", NOW - 15 * 365 * DAY, story=60)
        out = self.prior(author=[recent, old])
        self.assertEqual((out["prior_threads"], out["ambiguous_author_posts"]), (0, 1))

    def test_replies_other_stories_same_thread_and_later_posts_are_ignored(self):
        reply = post("p11", "x", "Acme Robotics | Engineer", NOW - 60 * DAY, story=101, parent=555)
        elsewhere = post("p12", "x", "Acme Robotics | Engineer", NOW - 60 * DAY, story=102, title="Ask HN: Who wants to be hired?")
        same_thread = post("p13", "x", "Acme Robotics | Engineer", NOW - 3600, story=999)
        later = post("p14", "x", "Acme Robotics | Engineer", NOW + 30 * DAY, story=104)
        self.assertEqual(self.prior(phrase=[reply, elsewhere, same_thread, later])["prior_threads"], 0)


class Strength(unittest.TestCase):
    def test_scale(self):
        tiny = {"team_size": 2, "employee_number": 3, "stage": "pre-seed"}
        rare = hh.strength_for("first_ever", 0, tiny, 0.9)
        notable = hh.strength_for("first_ever", 0, {"founding_role": True, "poster_role": "CTO"}, 0.75)
        plain_first = hh.strength_for("first_ever", 0, {}, 0.7)
        second = hh.strength_for("repeat", 1, {"says_early": True}, 0.7)
        routine = hh.strength_for("repeat", 30, {"hardware_roles": True}, 0.8)
        unknown = hh.strength_for("unknown", 0, {}, 0.7)
        self.assertGreaterEqual(rare, 0.85)
        self.assertTrue(0.6 <= notable <= 0.8, notable)
        self.assertTrue(0.35 <= plain_first <= 0.55, plain_first)
        self.assertTrue(0.25 <= second <= 0.55, second)
        self.assertTrue(0.15 <= routine <= 0.3, routine)
        self.assertLess(unknown, plain_first)
        self.assertGreater(rare, notable)
        self.assertGreater(notable, plain_first)
        self.assertGreater(plain_first, routine)

    def test_a_repeat_never_reaches_the_notable_band(self):
        tiny = {"team_size": 2, "employee_number": 3, "stage": "pre-seed", "poster_role": "co-founder", "hardware_roles": True}
        first = hh.strength_for("first_ever", 0, tiny, 0.99)
        second = hh.strength_for("repeat", 1, tiny, 0.99)
        self.assertLessEqual(second, hh.REPEAT_CAP)
        self.assertLessEqual(hh.strength_for("unknown", 0, tiny, 0.99), hh.REPEAT_CAP)
        self.assertGreater(first, second)
        # The weakest first-ever post by a company that says it is young already sits at the top of the solid band.
        self.assertGreater(hh.strength_for("first_ever", 0, {"says_early": True}, hh.MIN_FIT), hh.REPEAT_CAP - 0.05)

    def test_first_recent_is_worth_less_than_first_ever(self):
        cues = {"team_size": 6}
        self.assertLess(hh.strength_for("first_recent", 0, cues, 0.7), hh.strength_for("first_ever", 0, cues, 0.7))

    def test_established_companies_are_capped(self):
        big = {"team_size": 300, "founding_role": True}
        self.assertLessEqual(hh.strength_for("first_ever", 0, big, 0.95), hh.ESTABLISHED_CAP)
        self.assertLessEqual(hh.strength_for("first_ever", 0, {"funding_stated": 650e6}, 0.9), hh.ESTABLISHED_CAP)

    def test_always_in_range(self):
        for novelty in ("first_ever", "first_recent", "repeat", "unknown"):
            for prior in (0, 1, 2, 5, 50):
                for cues in ({}, {"team_size": 1, "employee_number": 1, "stage": "pre-seed", "founding_role": True,
                                  "first_hire": True, "poster_role": "founder", "hardware_roles": True}):
                    s = hh.strength_for(novelty, prior, cues, 1.0)
                    self.assertTrue(0.0 <= s <= 1.0)


class Titles(unittest.TestCase):
    def test_wording(self):
        self.assertEqual(
            hh.title_for("first_ever", 0, None, "Founding Geometry Engineer", {}, True),
            "First Who is hiring post on Hacker News, for Founding Geometry Engineer")
        self.assertEqual(
            hh.title_for("first_ever", 0, None, "Simulation/RL Integration Engineer", {"team_size": 2, "stage": "pre-seed"}, True),
            "First Who is hiring post on Hacker News, for Simulation/RL Integration Engineer; team of 2")
        self.assertEqual(
            hh.title_for("first_recent", 0, None, "Vision Systems Engineer", {}, False),
            "First Who is hiring post on Hacker News in at least 24 months, for Vision Systems Engineer")
        self.assertEqual(
            hh.title_for("repeat", 1, date(2026, 7, 13), "Robotics / Hardware Engineer", {"team_size": 2}, True),
            "Second Who is hiring post on Hacker News, for Robotics / Hardware Engineer; first seen Jul 2026, team of 2")
        self.assertEqual(
            hh.title_for("repeat", 49, date(2021, 6, 1), "Multiple Roles", {"stage": "series a"}, True),
            "Who is hiring post on Hacker News, for multiple roles; 49 earlier threads since Jun 2021")
        # An incomplete history gives a floor and no first date: an older post may exist beyond what was read.
        self.assertEqual(
            hh.title_for("repeat", 30, date(2024, 2, 1), None, {}, False),
            "Who is hiring post on Hacker News; at least 30 earlier threads")
        self.assertEqual(
            hh.title_for("repeat", 1, date(2019, 5, 1), None, {}, False),
            "Who is hiring post on Hacker News; at least 1 earlier thread")
        self.assertEqual(hh.title_for("unknown", 0, None, "Software Engineer", {}, False),
                         "Who is hiring post on Hacker News, for Software Engineer")
        self.assertEqual(
            hh.title_for("first_ever", 0, None, "Founding Geometry Engineer", {"poster_role": "CTO", "founding_role": True}, True),
            "First Who is hiring post on Hacker News, for Founding Geometry Engineer; posted by its CTO")
        self.assertEqual(
            hh.title_for("first_ever", 0, None, None, {"poster_role": "Co-Founder and CEO"}, True),
            "First Who is hiring post on Hacker News; posted by its Co-Founder and CEO")

    def test_ordinal_needs_one_post_per_earlier_thread(self):
        # Two earlier threads, two earlier posts: this is the third post.
        self.assertTrue(hh.title_for("repeat", 2, date(2026, 6, 1), None, {}, True, 2).startswith("Third Who is hiring post"))
        # Two earlier threads but three earlier posts: "Third" would be false of posts, so the threads are counted.
        self.assertEqual(hh.title_for("repeat", 2, date(2026, 6, 1), None, {}, True, 3),
                         "Who is hiring post on Hacker News; 2 earlier threads since Jun 2026")

    def test_a_role_list_is_quoted_whole_or_not_at_all(self):
        long_role = "Senior, Staff, Principal Software Engineer and Engineering Manager, Robotics Controls"
        t = hh.title_for("repeat", 12, date(2023, 8, 1), long_role, {}, True)
        self.assertEqual(t, "Who is hiring post on Hacker News; 12 earlier threads since Aug 2023")

    def test_rules(self):
        roles = [None, "Engineer", "x" * 300, "Senior Robotics Software Engineer (Early Career + Experienced)"]
        for novelty in ("first_ever", "first_recent", "repeat", "unknown"):
            for prior in (0, 1, 2, 3, 120):
                for role in roles:
                    for cues in ({}, {"team_size": 12}, {"employee_number": 4}, {"stage": "seed"},
                                 {"poster_role": "co-founder and CTO"}):
                        t = hh.title_for(novelty, prior, date(2021, 6, 1) if prior else None, role, cues, prior != 3)
                        self.assertNotIn("+ earlier", t)
                        if prior == 3:  # the incomplete case
                            self.assertNotIn("since", t)
                            self.assertNotIn("first seen", t)
                        self.assertLess(len(t), 110, t)
                        self.assertFalse(t.endswith("."), t)
                        self.assertFalse(t.lower().startswith(("hn", "hacker news")), t)
                        self.assertTrue(t[0].isupper(), t)


class BuildSignal(unittest.TestCase):
    def setUp(self):
        hh._FACTS.clear()

    def test_charge_robotics_matches_the_post(self):
        history = {"prior_threads": 49, "first_seen": date(2021, 6, 1), "author_prior_hiring_posts": 47,
                   "loose_mentions": 0, "ambiguous_author_posts": 0, "all_time_complete": True, "recent_complete": True}
        s = hh.build_signal(cand(CHARGE), THREAD, history)
        s.validate()
        self.assertEqual((s.source, s.family, s.kind), ("hn_hiring", "hiring", "hn_hiring_post"))
        self.assertEqual(s.entity.name, "Charge Robotics")
        self.assertEqual(s.entity.domain, "chargerobotics.com")
        self.assertEqual(s.entity.location, "SF Bay Area")
        self.assertEqual(s.entity.one_liner,
                         "We’re a YC-backed, Series-A startup building robots that build large-scale solar farms.")
        self.assertEqual(s.url, "https://news.ycombinator.com/item?id=49926244")
        self.assertEqual(s.occurred_at, "2026-10-01")
        self.assertEqual(s.title, "Who is hiring post on Hacker News, for multiple roles; 49 earlier threads since Jun 2021")
        self.assertEqual((s.value, s.unit), (50.0, "hiring threads"))
        self.assertEqual((s.metrics["ats_provider"], s.metrics["ats_slug"]), ("ashby", "charge-robotics"))
        self.assertEqual(s.metrics["stage"], "series a")
        self.assertFalse(s.metrics["first_hiring_post"])
        self.assertNotIn("team_size", s.metrics)
        self.assertLessEqual(s.strength, 0.3)
        self.assertEqual([(p.name, p.role, p.links) for p in s.people],
                         [("justicz", "HN hiring post author", {"hn": "https://news.ycombinator.com/user?id=justicz"})])
        self.assertTrue(s.text.startswith("Charge Robotics | Multiple Roles"))
        self.assertNotIn("http", s.text)

    def test_first_post_with_a_common_name_is_worded_as_recent(self):
        history = {"prior_threads": 0, "first_seen": None, "author_prior_hiring_posts": 0, "loose_mentions": 0,
                   "ambiguous_author_posts": 0, "all_time_complete": False, "recent_complete": True}
        s = hh.build_signal(cand(ETC), THREAD, history)
        self.assertEqual(s.title, "First Who is hiring post on Hacker News in at least 24 months, for Vision Systems Engineer")
        # A three-letter short form cannot lead: the full name from the header does, the short form is the alias.
        self.assertEqual(s.entity.name, "Exploration Technology Corp.")
        self.assertEqual(s.entity.aliases, ["etc."])
        self.assertTrue(s.entity.one_liner.startswith("At etc, we are actively developing"))
        self.assertIsNone(s.entity.domain)
        self.assertEqual((s.metrics["ats_provider"], s.metrics["ats_slug"]), ("rippling", "etc"))
        self.assertFalse(s.metrics["first_hiring_post"])
        self.assertEqual(s.metrics["first_hiring_post_in_months"], 24)

    def test_incomplete_history_gives_no_first_thread(self):
        history = {"prior_threads": 30, "prior_post_count": 30, "first_seen": date(2024, 2, 1),
                   "author_prior_hiring_posts": 29, "loose_mentions": 0, "ambiguous_author_posts": 0,
                   "all_time_complete": False, "recent_complete": True}
        s = hh.build_signal(cand(MONUMENTAL), THREAD, history)
        self.assertEqual(s.title, "Who is hiring post on Hacker News; at least 30 earlier threads")
        self.assertNotIn("first_hiring_thread", s.metrics)
        self.assertEqual(s.metrics["earliest_hiring_thread_found"], "2024-02")
        self.assertFalse(s.metrics["prior_history_complete"])

    def test_years_of_hiring_posts_cap_the_strength(self):
        base = {"prior_threads": 2, "prior_post_count": 2, "author_prior_hiring_posts": 1, "loose_mentions": 0,
                "ambiguous_author_posts": 0, "all_time_complete": True, "recent_complete": True}
        old = hh.build_signal(cand(KOBR), THREAD, {**base, "first_seen": date(2019, 1, 2)})
        new = hh.build_signal(cand(KOBR), THREAD, {**base, "first_seen": date(2026, 6, 2)})
        self.assertLessEqual(old.strength, hh.ESTABLISHED_CAP)
        self.assertGreater(new.strength, old.strength)
        self.assertTrue(new.title.startswith("Third Who is hiring post"))

    def test_first_ever(self):
        history = {"prior_threads": 0, "first_seen": None, "author_prior_hiring_posts": 0, "loose_mentions": 0,
                   "ambiguous_author_posts": 0, "all_time_complete": True, "recent_complete": True}
        s = hh.build_signal(cand(KOBR), THREAD, history)
        self.assertTrue(s.title.startswith("First Who is hiring post on Hacker News"))
        self.assertTrue(s.metrics["first_hiring_post"])
        self.assertEqual((s.value, s.unit), (1.0, "hiring threads"))
        self.assertTrue(s.metrics["founding_role"])
        self.assertGreaterEqual(s.strength, 0.6)

    def test_unsettled_history_makes_no_claim(self):
        for extra in ({"loose_mentions": 2}, {"ambiguous_author_posts": 1},
                      {"all_time_complete": False, "recent_complete": False}):
            history = {"prior_threads": 0, "first_seen": None, "author_prior_hiring_posts": 0, "loose_mentions": 0,
                       "ambiguous_author_posts": 0, "all_time_complete": True, "recent_complete": True, **extra}
            s = hh.build_signal(cand(KOBR), THREAD, history)
            self.assertFalse(s.title.startswith("First"), extra)
            self.assertFalse(s.metrics["first_hiring_post"])
            self.assertIsNone(s.value)
            self.assertLess(s.strength, 0.6)

    def test_every_kept_fixture_post_builds_a_valid_signal(self):
        history = {"prior_threads": 3, "first_seen": date(2025, 1, 2), "author_prior_hiring_posts": 3,
                   "loose_mentions": 0, "ambiguous_author_posts": 0, "all_time_complete": True, "recent_complete": True}
        for c in filter(None, map(hh.candidate, HITS)):
            if not hh.passes_gate(c):
                continue
            s = hh.build_signal(c, THREAD, history)
            s.validate()
            self.assertLess(len(s.title), 110)
            self.assertEqual(s.url, f"https://news.ycombinator.com/item?id={c['hit']['objectID']}")
            self.assertTrue(s.occurred_at.startswith("2026-10-0"))


class Collect(unittest.TestCase):
    """collect() against the fixture, with every other Algolia answer synthesised."""

    older_threads = 24  # with September: the 24 earlier threads the header scan needs

    def setUp(self):
        hh._FACTS.clear()

    def _fake(self, calls: list, charge_history: bool = True):
        threads = {"hits": [
            {"objectID": str(THREAD_ID), "title": "Ask HN: Who is hiring? (October 2026)", "created_at_i": 1790866927},
            {"objectID": "49922570", "title": "Ask HN: Who wants to be hired? (October 2026)", "created_at_i": 1790866928},
            {"objectID": "49522897", "title": "Ask HN: Who is hiring? (September 2026)", "created_at_i": 1788274877},
        ] + [
            {"objectID": str(40000000 + i), "title": f"Ask HN: Who is hiring? (older {i})",
             "created_at_i": 1788274877 - i * 30 * 86400}
            for i in range(1, self.older_threads)
        ]}
        earlier_charge = post("old-charge", "justicz", "Charge Robotics | Robotics Software Engineer | Onsite | SF Bay Area",
                              1788280000, story=49522897, title="Ask HN: Who is hiring? (September 2026)")

        def fake(url, *, params=None, ttl=None, **kw):
            calls.append(dict(params or {}))
            tags = (params or {}).get("tags", "")
            if tags == "story,author_whoishiring":
                return threads
            if tags == f"comment,story_{THREAD_ID}":
                return {"hits": HITS, "nbHits": len(HITS)}
            if tags.startswith("comment,story_"):
                return {"hits": [], "nbHits": 0}
            if tags == "comment,author_justicz" and charge_history:
                return {"hits": [earlier_charge, BY_ID[CHARGE]], "nbHits": 2}
            if tags.startswith("comment,author_"):
                return {"hits": [], "nbHits": 0}
            if tags == "comment" and "query" in params:
                if params["query"] == '"etc."':
                    return {"hits": [], "nbHits": 995987}  # far too common to be exhaustive
                return {"hits": [], "nbHits": 0}
            raise AssertionError(f"unexpected request {url} {params}")
        return fake

    def run_collect(self, **ctx_kw):
        calls: list = []
        ctx = Context(today=date(2026, 10, 1), **ctx_kw)
        with mock.patch.object(hh.http, "get_json", self._fake(calls)):
            signals = list(hh.collect(ctx))
        return signals, calls, ctx

    def test_one_valid_signal_per_company(self):
        signals, calls, ctx = self.run_collect()
        self.assertEqual(ctx.warnings, [])
        names = [s.entity.name for s in signals]
        self.assertEqual(sorted(names), sorted(["Tangram Vision", "MONUMENTAL", "Charge Robotics", ETC_NAME, "ko-br",
                                               "Skydio", "Beacon AI"]))
        for s in signals:
            s.validate()
            self.assertGreaterEqual(s.occurred_at, ctx.since.isoformat())
        by_name = {s.entity.name: s for s in signals}
        # Charge Robotics has one earlier thread in the synthetic author history.
        self.assertEqual(by_name["Charge Robotics"].metrics["prior_hiring_threads"], 1)
        self.assertTrue(by_name["Charge Robotics"].title.startswith("Second Who is hiring post"))
        # A name too common to search is only ever "first in 24 months", after the thread scan.
        self.assertTrue(by_name[ETC_NAME].title.startswith("First Who is hiring post on Hacker News in at least 24 months"))
        self.assertEqual(by_name[ETC_NAME].entity.aliases, ["etc."])
        # Everything else has empty histories here, so it reads as a first.
        self.assertTrue(by_name["ko-br"].metrics["first_hiring_post"])
        # The "Who wants to be hired?" story is never read as a hiring thread.
        self.assertNotIn("comment,story_49922570", [c.get("tags") for c in calls])

    def test_a_short_thread_history_cannot_vouch_for_24_months(self):
        self.older_threads = 5
        signals, _, _ = self.run_collect()
        etc = {s.entity.name: s for s in signals}[ETC_NAME]
        self.assertEqual(etc.title, "Who is hiring post on Hacker News, for Vision Systems Engineer")
        self.assertIsNone(etc.value)

    def _with_extra(self, calls, extra: dict[int, list[dict]]):
        """The usual fake, with extra synthetic top-level comments added to the given threads."""
        inner = self._fake(calls)

        def fake(url, *, params=None, **kw):
            out = inner(url, params=params, **kw)
            tags = (params or {}).get("tags", "")
            for story, hits in extra.items():
                if tags == f"comment,story_{story}":
                    out = {"hits": list(out["hits"]) + hits, "nbHits": len(out["hits"]) + len(hits)}
            return out
        return fake

    def test_a_comment_dated_tomorrow_in_utc_waits_for_the_next_run(self):
        # A run dated 1 October (a replay: the run date is the UTC date) must not emit a comment made at
        # 2026-10-02 00:03 UTC; the run dated 2 October does.
        late = post("49990001", "theo", ACME, 1790899435, story=THREAD_ID, title="Ask HN: Who is hiring? (October 2026)")
        on_time = post("49990002", "ana", ACME.replace("Acme", "Borealis").replace("acme", "borealis"), 1790899199,
                       story=THREAD_ID, title="Ask HN: Who is hiring? (October 2026)")
        for today, expect_late in ((date(2026, 10, 1), False), (date(2026, 10, 2), True)):
            hh._FACTS.clear()
            ctx = Context(today=today)
            with mock.patch.object(hh.http, "get_json", self._with_extra([], {THREAD_ID: [late, on_time]})):
                signals = list(hh.collect(ctx))
            dates = {s.entity.name: s.occurred_at for s in signals}
            self.assertEqual(dates.get("Borealis Robotics"), "2026-10-01")
            self.assertEqual(dates.get("Acme Robotics"), "2026-10-02" if expect_late else None, today)
            for s in signals:
                self.assertLessEqual(s.occurred_at, today.isoformat())
                self.assertGreaterEqual(s.occurred_at, ctx.since.isoformat())

    def test_one_company_keeps_one_domain_across_threads(self):
        # September links acme.ai twice and acme.co once; October only knows acme.co. Left alone the two
        # posts would resolve to two entities, because the resolver treats a domain as a hard key.
        september = post("49530001", "founder1",
                         'Acme Robotics | Engineer | <a href="https://acme.ai">https://acme.ai</a><p>We build robots that weld '
                         'ship hulls with industrial robots. Jobs: <a href="https://acme.ai/jobs">https://acme.ai/jobs</a> '
                         'or hello@acme.co', 1788280000, story=49522897, title="Ask HN: Who is hiring? (September 2026)")
        october = post("49990003", "founder1",
                       'Acme Robotics | Engineer | <a href="https://acme.co">https://acme.co</a><p>We build robots that weld '
                       'ship hulls with industrial robots.', 1790870000, story=THREAD_ID,
                       title="Ask HN: Who is hiring? (October 2026)")
        self.assertEqual(hh.candidate(september)["domain"], "acme.ai")
        ctx = Context(today=date(2026, 10, 1))
        with mock.patch.object(hh.http, "get_json", self._with_extra([], {THREAD_ID: [october], 49522897: [september]})):
            signals = [s for s in hh.collect(ctx) if s.entity.name == "Acme Robotics"]
        self.assertEqual([(s.occurred_at, s.entity.domain) for s in signals],
                         [("2026-10-01", "acme.co"), ("2026-09-01", "acme.co")])
        # The same name under another ending, with no mention of the newer one: still one company, one domain.
        hh._FACTS.clear()
        older = dict(september, comment_text=september["comment_text"].replace(" or hello@acme.co", ""))
        with mock.patch.object(hh.http, "get_json", self._with_extra([], {THREAD_ID: [october], 49522897: [older]})):
            signals = [s for s in hh.collect(ctx) if s.entity.name == "Acme Robotics"]
        self.assertEqual({s.entity.domain for s in signals}, {"acme.co"})
        self.assertEqual(len(signals), 2)

    def test_limit_caps_entities(self):
        signals, _, _ = self.run_collect(limit=3)
        self.assertEqual(len({s.entity.name for s in signals}), 3)

    def test_lookback_excludes_old_comments(self):
        calls: list = []
        ctx = Context(today=date(2027, 6, 1), lookback_days=30)
        with mock.patch.object(hh.http, "get_json", self._fake(calls)):
            self.assertEqual(list(hh.collect(ctx)), [])

    def test_one_failing_history_does_not_lose_the_run(self):
        calls: list = []
        inner = self._fake(calls)

        def flaky(url, *, params=None, **kw):
            if (params or {}).get("tags") == "comment,author_crubier":  # Skydio's poster
                raise OSError("timed out")
            return inner(url, params=params, **kw)

        ctx = Context(today=date(2026, 10, 1))
        with mock.patch.object(hh.http, "get_json", flaky):
            signals = list(hh.collect(ctx))
        self.assertEqual(len(signals), 6)
        self.assertNotIn("Skydio", [s.entity.name for s in signals])
        self.assertEqual(len(ctx.warnings), 1)

    def test_no_threads_is_a_warning_not_a_crash(self):
        ctx = Context(today=date(2026, 10, 1))
        with mock.patch.object(hh.http, "get_json", lambda url, **kw: {"hits": []}):
            self.assertEqual(list(hh.collect(ctx)), [])
        self.assertEqual(len(ctx.warnings), 1)


if __name__ == "__main__":
    unittest.main()
