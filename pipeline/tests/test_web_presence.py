"""Offline tests for the web_presence collector.

No network. The pure functions (candidate hosts, name matching, page parsing,
parked-page detection, description and link extraction, verification) run on
small inline HTML pages modelled on sites seen on the 2026-10-01 test set,
including the wrong matches that shaped the rules. The end-to-end run swaps
the three network functions for stubs.

The collector reads the system word list to tell a made-up name from a
plain-word one. The tests pin a small list of their own so they give the same
answer on every machine.
"""

from __future__ import annotations

import unittest
from datetime import date
from unittest import mock

from antenna import http
from antenna.collectors import web_presence as wp
from antenna.collectors.base import Context
from antenna.models import FAMILIES

TODAY = date(2026, 10, 1)

WORDS = frozenset("""
    swarm parallel aura world american drone prometheus mantle riven chaos lion power mission
    control terminal missile ocean magnet arch aerial exclaim gauss process specialty mantis
    autonomous advanced first nuclear on demand angel falcon unmanned maxwell tera stealth
    genesis flying robot energy defense trinary minerva ground floor allen one alt spec last
""".split())

_SAVED: tuple = ()


def setUpModule() -> None:
    global _SAVED
    _SAVED = (wp._COMMON_WORDS, wp._COMMON_WORDS_LOADED)
    wp._COMMON_WORDS, wp._COMMON_WORDS_LOADED = WORDS, True


def tearDownModule() -> None:
    wp._COMMON_WORDS, wp._COMMON_WORDS_LOADED = _SAVED


def ctx(**kw) -> Context:
    c = Context(today=kw.pop("today", TODAY), **kw)
    c.logged = []  # type: ignore[attr-defined]
    c.warn = lambda msg: c.warnings.append(msg)  # type: ignore[method-assign]
    c.log = lambda msg: c.logged.append(msg)  # type: ignore[method-assign,attr-defined]
    return c


def entity(name: str, sector: str = "autonomy", one_liner: str | None = None, **kw) -> dict:
    return {"slug": name.lower().replace(" ", "-"), "name": name, "kind": "company", "domain": None,
            "github": None, "sector": sector, "fit": 0.9, "one_liner": one_liner, "description": None,
            "links": {}, "sources": [], "ats": [], "prelim": 1.0, **kw}


def html(title: str = "", description: str = "", body: str = "", head: str = "") -> str:
    meta = f'<meta name="description" content="{description}">' if description else ""
    return f"<html><head><title>{title}</title>{meta}{head}</head><body>{body}</body></html>"


def verify(e: dict, host: str, markup: str, final_url: str | None = None, source: str = "guess") -> wp.Verdict:
    return wp.verify_page(e, host, source, final_url or f"https://{host}/", wp.parse_page(markup), markup)


SWARM_US = html(
    "Swarm Defense Technologies | U.S.-Built Military Drone Swarms at Scale",
    "Swarm Defense Technologies designs and manufactures scalable drone swarm systems.",
    "<h1>Drone swarms at scale</h1><p>Attack drones, autonomous swarm coordination software and "
    "unmanned aircraft for the military.</p><footer>© 2026 Swarm Defense Technologies, Inc. Auburn Hills, "
    "Michigan</footer>",
)
SWARM_FR = html(
    "SWARM Defense | Drones et robotique à fins militaires",
    "SWARM Defense propose des solutions innovantes dans le domaine de la robotique et des drones.",
    "<p>Drones, autonomous systems, unmanned aircraft and military robots.</p>",
)
ORKORA = html(
    "Orkora", "",
    "<h1>The Nuclear Company With A Global Market</h1><p>Nuclear reactors, fuel recycling and "
    "energy for the grid. Power plants at sea and on land.</p>",
)


class Module(unittest.TestCase):
    def test_contract_constants(self):
        self.assertEqual(wp.SLUG, "web_presence")
        self.assertIn(wp.FAMILY, FAMILIES)
        self.assertEqual(wp.FAMILY, "traffic")
        self.assertEqual(wp.STAGE, "identify")
        self.assertEqual(wp.MAX_LOOKUPS, 260)
        self.assertEqual(wp.MAX_DESCRIBE, 300)
        self.assertTrue(wp.DESCRIPTION)

    def test_requests_use_the_antenna_user_agent_only(self):
        # Nothing in the module sets a User-Agent of its own.
        import inspect
        self.assertNotIn("User-Agent", inspect.getsource(wp))


class Names(unittest.TestCase):
    def test_tokens_drop_legal_form_and_the(self):
        self.assertEqual(wp.name_tokens("Swarm Defense Technologies, Inc."), ["swarm", "defense", "technologies"])
        self.assertEqual(wp.name_tokens("The American Drone"), ["american", "drone"])
        self.assertEqual(wp.name_tokens("Marcum & Swerling Defense"), ["marcum", "swerling", "defense"])
        self.assertEqual(wp.name_tokens("Tsing-AI (Shanghai) Technology"), ["tsing", "ai", "technology"])
        self.assertEqual(wp.name_tokens("1st American Nuclear"), ["first", "american", "nuclear"])
        self.assertEqual(wp.name_tokens("Blykälla Reactor"), ["blykalla", "reactor"])

    def test_forms_keep_the_full_distinctive_name(self):
        forms = wp.name_forms("Swarm Defense Technologies")
        self.assertEqual([f.tokens for f in forms], [("swarm", "defense", "technologies"), ("swarm", "defense")])
        self.assertEqual(forms[1].dropped, ("technologies",))
        # A sector word is never dropped and a name is never cut to one word.
        self.assertEqual([f.tokens for f in wp.name_forms("Parallel Robotics")], [("parallel", "robotics")])
        self.assertEqual([f.tokens for f in wp.name_forms("Aeglos Systems")], [("aeglos", "systems")])
        self.assertEqual([f.tokens for f in wp.name_forms("Arc Aerospace and Defense Systems")],
                         [("arc", "aerospace", "defense", "systems"), ("arc", "aerospace", "defense")])

    def test_weak_names(self):
        self.assertTrue(wp.name_forms("Mantle")[0].weak)       # one common word
        self.assertTrue(wp.name_forms("Avol")[0].weak)         # under five letters
        self.assertTrue(wp.name_forms("CX2")[0].weak)
        self.assertFalse(wp.name_forms("Orkora")[0].weak)      # one made-up word
        self.assertFalse(wp.name_forms("Parallel Robotics")[0].weak)
        self.assertEqual(wp.name_forms("X"), [])

    def test_coined_and_plain(self):
        self.assertTrue(wp.is_coined(["orkora"]))
        self.assertTrue(wp.is_coined(["veridis", "defense"]))
        self.assertTrue(wp.is_coined(["dronedeploy"]))
        self.assertFalse(wp.is_coined(["swarm", "defense"]))
        self.assertFalse(wp.is_coined(["process", "specialties"]))   # plural of a common word
        self.assertFalse(wp.is_coined(["kyoto", "energy"]))          # a place is not a coinage
        self.assertFalse(wp.is_coined(["arc", "defense"]))           # too short to count

    def test_without_a_word_list_every_name_is_treated_as_common(self):
        with mock.patch.object(wp, "_COMMON_WORDS", None):
            self.assertTrue(wp.is_common_word("orkora"))
            self.assertFalse(wp.is_coined(["orkora"]))
            self.assertTrue(wp.name_forms("Orkora")[0].weak)

    def test_match_in_a_title(self):
        forms = wp.name_forms("Swarm Defense Technologies")
        form, exact = wp.match_name("Swarm Defense Technologies | Drone Swarms at Scale", forms)
        self.assertEqual((form.label, exact), ("full", True))
        form, exact = wp.match_name("Home - Swarm Defense, Inc.", forms)
        self.assertEqual((form.label, exact), ("short", True))
        form, exact = wp.match_name("Why Swarm Defense matters for schools", forms)
        self.assertEqual((form.label, exact), ("short", False))
        self.assertIsNone(wp.match_name("Swarm | Decentralised storage", forms))
        self.assertIsNone(wp.match_name("", forms))

    def test_a_different_filler_word_is_a_different_company(self):
        forms = wp.name_forms("Autonomous Defense Technologies")
        self.assertIsNone(wp.match_name("ADS · Autonomous Defense Systems", forms))
        self.assertIsNone(wp.match_name("SwarmDefense.Systems", wp.name_forms("Swarm Defense Technologies")))
        self.assertIsNotNone(wp.match_name("Autonomous Defense Tech", forms))
        self.assertIsNotNone(wp.match_name("Autonomous Defense Technologies Systems Overview", forms))

    def test_match_joins_and_splits_words(self):
        self.assertIsNotNone(wp.match_name("SkildAI — robots", wp.name_forms("Skild AI")))
        self.assertIsNotNone(wp.match_name("Managed drones | LandSky AI", wp.name_forms("LandSkyAI")))
        self.assertIsNotNone(wp.match_name("Advanced Atomics™ | Nuclear", wp.name_forms("Advanced Atomics")))
        self.assertIsNotNone(wp.match_name("First American Nuclear Co.", wp.name_forms("1st American Nuclear")))
        self.assertIsNone(wp.match_name("Proception", wp.name_forms("Perception")))
        # Word boundaries hold: "Asylon" is not inside "Asylonia".
        self.assertIsNone(wp.match_name("Asylonia Travel", wp.name_forms("Asylon")))


class Candidates(unittest.TestCase):
    def test_guess_order(self):
        hosts = wp.guess_hosts("Swarm Defense Technologies", "autonomy")
        self.assertEqual(hosts[:3], ["swarmdefense.com", "swarmdefensetechnologies.com", "swarmdefense.tech"])
        self.assertIn("swarmdefensetech.com", hosts)
        self.assertIn("swarm-defense.com", hosts)
        self.assertLessEqual(len(hosts), wp.GUESS_LIMIT)

    def test_last_word_as_the_ending(self):
        self.assertEqual(wp.guess_hosts("Alva Energy", "energy")[:2], ["alvaenergy.com", "alva.energy"])
        self.assertEqual(wp.guess_hosts("Skild AI", "robotics")[:2], ["skildai.com", "skild.ai"])
        self.assertIn("mantis.space", wp.guess_hosts("Mantis Space", "space"))
        self.assertIn("auron.solutions", wp.guess_hosts("Auron Solutions", "autonomy"))

    def test_sector_ending_and_coined_stem(self):
        self.assertIn("sparkz.energy", wp.guess_hosts("Sparkz", "energy"))
        self.assertNotIn("sparkz.energy", wp.guess_hosts("Sparkz", "robotics"))
        self.assertIn("atfuta.com", wp.guess_hosts("Atfuta Robotics", "robotics"))
        # A plain first word alone is somebody else's domain: not guessed.
        self.assertNotIn("parallel.com", wp.guess_hosts("Parallel Robotics", "robotics", limit=0))

    def test_name_written_as_a_domain_goes_first(self):
        self.assertEqual(wp.guess_hosts("Trinary.ai", "semiconductors")[0], "trinary.ai")
        self.assertEqual(wp.guess_hosts("EverPower.AI", "energy")[0], "everpower.ai")

    def test_no_silly_or_invalid_hosts(self):
        for name, sector in [("Skild AI", "robotics"), ("Alva Energy", "energy"), ("Mantis Space", "space")]:
            for host in wp.guess_hosts(name, sector, limit=0):
                label, ending = host.split(".", 1)
                self.assertFalse(label.endswith(ending) and label != ending, host)
                self.assertRegex(host, r"^[a-z0-9-]+\.[a-z]+$")
        self.assertEqual(wp.guess_hosts("", None), [])
        self.assertEqual(len(set(wp.guess_hosts("Orkora", "energy"))), len(wp.guess_hosts("Orkora", "energy")))

    def test_registrable(self):
        self.assertEqual(wp.registrable("www.swarmdefense.com"), "swarmdefense.com")
        self.assertEqual(wp.registrable("shop.power-on-demand.co.uk"), "power-on-demand.co.uk")
        self.assertEqual(wp.registrable("hyl.io"), "hyl.io")

    def test_domain_match(self):
        self.assertEqual(wp.domain_match("swarmdefense.com", "Swarm Defense Technologies"), "exact")
        self.assertEqual(wp.domain_match("alva.energy", "Alva Energy"), "exact")
        self.assertEqual(wp.domain_match("hyl.io", "Hylio"), "exact")
        self.assertEqual(wp.domain_match("www.gauss-fusion.com", "Gauss Fusion"), "exact")
        self.assertEqual(wp.domain_match("asylonrobotics.com", "Asylon"), "partial")
        self.assertEqual(wp.domain_match("censystech.com", "Censys Technologies"), "partial")
        self.assertEqual(wp.domain_match("dyna.co", "Dyna Robotics"), "partial")
        self.assertIsNone(wp.domain_match("mxllabs.com", "Maxwell Labs"))
        self.assertIsNone(wp.domain_match("arcade.com", "Arc Defense"))   # a three-letter stem never counts

    def test_suggestions_are_filtered_to_the_name(self):
        rows = [
            {"name": "World Airport Codes", "domain": "world-airport-codes.com"},
            {"name": "Asylon", "domain": "asylonrobotics.com", "logo": None},
            {"name": "ASYLON Consulting", "domain": "asylon.net"},
            {"name": "Asylon Theatre", "domain": "asylontheatre.org"},
            {"name": "Other", "domain": "https://www.linkedin.com/company/asylon"},
            "junk", {"name": None, "domain": None},
        ]
        self.assertEqual(wp.suggestion_candidates(rows, "World AI"), [])
        got = wp.suggestion_candidates(rows, "Asylon")
        self.assertEqual([h for h, _ in got], ["asylonrobotics.com", "asylon.net", "asylontheatre.org"])
        self.assertEqual(wp.suggestion_candidates({"error": "x"}, "Asylon"), [])
        self.assertEqual(wp.suggestion_candidates([{"name": "Maxwell Labs", "domain": "mxllabs.com"}],
                                                  "Maxwell Labs"), [("mxllabs.com", "Maxwell Labs")])


PAGE = """<!doctype html><html lang="en"><head>
<title> Exowatt | Powering AI with
 24-hour Dispatchable Energy </title>
<meta name="description" content="Exowatt is redefining energy for the AI era. Delivering clean, modular power.">
<meta property="og:site_name" content="Exowatt">
<meta property="og:title" content="Exowatt &amp; friends">
<meta property="og:description" content="OG text">
<script type="application/ld+json">{"@context":"https://schema.org","@graph":[
 {"@type":"Organization","name":"Exowatt, Inc.","legalName":"Exowatt Incorporated"},
 {"@type":"WebPage","name":"Home"}]}</script>
<style>body{color:red}</style>
<script>var hidden = "do not read me";</script>
</head><body>
<nav><a href="/about">About</a> <a href="https://jobs.lever.co/exowatt/123">Careers</a></nav>
<svg><title>logo</title><text>svg words</text></svg>
<h1>Dispatchable <em>solar</em> power</h1>
<h1>Second heading</h1>
<p>Modular energy for data centers.<br/>Thermal storage &amp; solar collectors.</p>
<noscript>Please enable JavaScript</noscript>
<iframe src="https://boards.greenhouse.io/embed/job_board?for=exowatt"></iframe>
<a href="https://github.com/exowatt">GitHub</a> <a href="https://github.com/vercel/next.js">Next</a>
<a href="https://x.com/exowattenergy">X</a> <a href="https://twitter.com/intent/tweet?text=hi">Share</a>
<a href="https://www.linkedin.com/company/exowatt/">LinkedIn</a>
<footer>Copyright © 2026 Exowatt, Inc. All rights reserved. Miami, FL 33101</footer>
</body></html>"""


class Parsing(unittest.TestCase):
    def setUp(self):
        self.page = wp.parse_page(PAGE)

    def test_name_slots(self):
        p = self.page
        self.assertEqual(p.title, "Exowatt | Powering AI with 24-hour Dispatchable Energy")
        self.assertEqual(p.site_name, "Exowatt")
        self.assertEqual(p.og_title, "Exowatt & friends")
        self.assertEqual(p.h1, "Dispatchable solar power")
        self.assertEqual(p.description, "Exowatt is redefining energy for the AI era. Delivering clean, modular power.")
        self.assertEqual(p.og_description, "OG text")
        self.assertEqual(p.schema_names, ["Exowatt, Inc.", "Exowatt Incorporated"])

    def test_visible_text_only(self):
        t = self.page.text
        self.assertIn("Modular energy for data centers. Thermal storage & solar collectors.", t)
        self.assertIn("Second heading", t)
        for hidden in ("do not read me", "color:red", "svg words", "enable JavaScript", "schema.org", "logo"):
            self.assertNotIn(hidden, t)
        self.assertNotIn("Powering AI with", t)   # the title is not body text

    def test_links_collected(self):
        self.assertIn("https://jobs.lever.co/exowatt/123", self.page.hrefs)
        self.assertIn("https://boards.greenhouse.io/embed/job_board?for=exowatt", self.page.srcs)

    def test_copyright_lines(self):
        lines = wp.copyright_lines(self.page.text)
        self.assertTrue(lines)
        self.assertIn("Exowatt, Inc.", lines[0][1])
        self.assertEqual(wp.copyright_lines("items (a) one (b) two (c) three"), [])
        self.assertEqual(len(wp.copyright_lines("x (c) 2026 Acme Robotics")), 1)

    def test_copyright_owner_stands_at_the_mark(self):
        forms = wp.name_forms("Acme Robotics")
        for text in ("Privacy © 2026 Acme Robotics, Inc. All rights reserved.", "Copyright 2019-2026 Acme Robotics",
                     "2026 © Acme Robotics Inc. X LinkedIn", "Terms Acme Robotics © 2026", "© Acme-Robotics™",
                     "Careers Acme Robotics, Inc. Copyright 2026. All rights reserved", "(c) 2026 by The Acme Robotics Company"):
            self.assertIsNotNone(wp.match_copyright(text, forms), text)
        for text in ("Our clients include Acme Robotics and others. © 2026 Hale & Dorr LLP",
                     "© 2026 Hale LLP. Counsel to Acme Robotics", "© 2026 Acme", "No mark here: Acme Robotics",
                     "© 2026 Acme Robotics Systems"[:0] + "© 2026 Acme Aerospace"):
            self.assertIsNone(wp.match_copyright(text, forms), text)
        # A different filler word after the short form is somebody else, here too.
        forms = wp.name_forms("Autonomous Defense Technologies")
        self.assertIsNone(wp.match_copyright("© 2026 Autonomous Defense Systems", forms))
        self.assertIsNotNone(wp.match_copyright("© 2026 Autonomous Defense", forms))

    def test_broken_markup_does_not_raise(self):
        for bad in ("", "<html><title>Half", "<<<>>> <meta name=description content=", "<svg><title>x",
                    "<script type='application/ld+json'>{not json</script><title>T</title>", None):
            self.assertIsInstance(wp.parse_page(bad), wp.Page)  # type: ignore[arg-type]
        self.assertEqual(wp.parse_page("<script type='application/ld+json'>{bad</script><title>T</title>").title, "T")

    def test_meta_refresh(self):
        p = wp.parse_page('<meta http-equiv="refresh" content="0; url=https://example.org/home">')
        self.assertEqual(p.refresh, "https://example.org/home")
        self.assertIsNone(wp.parse_page('<meta http-equiv="refresh" content="600">').refresh)
        self.assertIsNone(wp.parse_page(
            '<noscript><meta http-equiv="refresh" content="0; url=/nojs"></noscript>').refresh)


class Parked(unittest.TestCase):
    def test_marketplace_hosts(self):
        p = wp.Page(title="swarmdefense.io")
        self.assertIn("hugedomains.com", wp.parked_reason("www.hugedomains.com", p))
        self.assertIn("sedo.com", wp.parked_reason("sedo.com", p))
        self.assertIn("dan.com", wp.parked_reason("dan.com", p))
        self.assertIsNone(wp.parked_reason("swarmdefense.com", p))
        self.assertIsNone(wp.parked_reason("jordan.com", p))     # ends in dan.com, is not dan.com

    def test_for_sale_phrases(self):
        for text in ("This domain is for sale", "The domain name inertia.co may be for sale",
                     "Buy this domain today", "Domain parked by the registrar", "Parked free, courtesy of GoDaddy",
                     "orkora.io is available for purchase - this domain is available"):
            self.assertIsNotNone(wp.parked_reason("x.com", wp.Page(title="x", text=text)), text)
        self.assertIsNotNone(wp.parked_reason("x.com", wp.Page(title="Inertia.co for sale on Atom")))
        self.assertIsNone(wp.parked_reason("x.com", wp.Page(title="Acme", text="We park drones in a domain of sky.")))

    def test_parking_markup(self):
        lander = '<html><head><script>window.location.href="/lander"</script></head></html>'
        self.assertIsNotNone(wp.parked_reason("fluxara.com", wp.parse_page(lander), lander))
        sedo = '<html><body><script src="https://img.sedoparking.com/x.js"></script></body></html>'
        self.assertIsNotNone(wp.parked_reason("x.com", wp.parse_page(sedo), sedo))


class Description(unittest.TestCase):
    def d(self, text: str, title: str = "Acme", og: str = "") -> str | None:
        return wp.clean_description(wp.Page(title=title, description=text, og_description=og), "Acme Robotics")

    def test_a_real_sentence_is_kept(self):
        s = "Acme Robotics builds autonomous mobile robots for rack maintenance in data centers."
        self.assertEqual(self.d(s), s)

    def test_boilerplate_is_skipped(self):
        for junk in ("Home", "Welcome", "Acme", "Just another WordPress site",
                     "We use cookies to improve your experience on this website and more.",
                     "Please enable JavaScript to view this website in your browser now.",
                     "Web site created using create-react-app with a few more words.",
                     "This domain is for sale, make an offer on the page today.",
                     "Coming soon: a new website for a new kind of company."):
            self.assertIsNone(self.d(junk), junk)
        self.assertIsNone(self.d("Acme Robotics", title="Acme Robotics"))
        self.assertIsNone(self.d(""))

    def test_domain_as_a_word_is_not_boilerplate(self):
        s = "Enabling cross-domain autonomy through software for robots and vehicles."
        self.assertEqual(self.d(s), s)

    def test_falls_back_to_og_description(self):
        og = "Acme builds humanoid robots that work in real factories every day."
        self.assertEqual(self.d("Home", og=og), og)

    def test_trimmed_at_a_sentence_end(self):
        s = ("Acme builds humanoid robots that work in real factories every single day of the year. "
             "The second sentence runs long enough to push the whole description well past the limit of "
             "two hundred characters, so it is dropped whole.")
        self.assertEqual(self.d(s), "Acme builds humanoid robots that work in real factories every single day of the year.")

    def test_trimmed_at_a_word(self):
        s = "Acme " + "builds reliable autonomous machines and " * 8 + "more"
        out = self.d(s)
        self.assertLessEqual(len(out), wp.ONE_LINER_CHARS)
        self.assertTrue(out.endswith("…"))
        self.assertTrue(s.startswith(out[:-1]))
        self.assertEqual(s[len(out) - 1], " ")       # the cut fell between words


class Links(unittest.TestCase):
    def test_all_four_kinds(self):
        got = wp.extract_links(wp.parse_page(PAGE), "Exowatt", "exowatt.com")
        self.assertEqual(got["github"], "exowatt")
        self.assertEqual(got["ats"], ("lever", "exowatt"))
        self.assertEqual(got["x"], "https://x.com/exowattenergy")
        self.assertEqual(got["linkedin"], "https://www.linkedin.com/company/exowatt")

    def links(self, *hrefs: str, name: str = "Dexmate", domain: str = "dexmate.com") -> dict:
        return wp.extract_links(wp.Page(hrefs=list(hrefs)), name, domain)

    def test_github_only_when_it_resembles_the_company(self):
        self.assertEqual(self.links("https://github.com/dexmate-ai/vega-sdk")["github"], "dexmate-ai")
        self.assertNotIn("github", self.links("https://github.com/PX4/PX4-Autopilot"))
        self.assertNotIn("github", self.links("https://github.com/sponsors/dexmate"))
        self.assertNotIn("github", self.links("https://github.com/"))

    def test_ats_boards(self):
        self.assertEqual(self.links("https://jobs.ashbyhq.com/dexmate")["ats"], ("ashby", "dexmate"))
        self.assertEqual(self.links("https://job-boards.greenhouse.io/dexmate/jobs/1")["ats"], ("greenhouse", "dexmate"))
        self.assertEqual(self.links("https://boards.greenhouse.io/embed/job_board/js?for=dexmate")["ats"],
                         ("greenhouse", "dexmate"))
        self.assertEqual(self.links("//jobs.lever.co/dexmate")["ats"], ("lever", "dexmate"))
        # One board that does not resemble the name is still the page's own board.
        self.assertEqual(self.links("https://jobs.ashbyhq.com/vega-robots")["ats"], ("ashby", "vega-robots"))
        # Two unrelated boards: no way to tell which is ours.
        self.assertNotIn("ats", self.links("https://jobs.lever.co/alpha", "https://jobs.lever.co/beta"))
        self.assertEqual(self.links("https://jobs.lever.co/alpha", "https://jobs.lever.co/dexmate")["ats"],
                         ("lever", "dexmate"))
        self.assertNotIn("ats", self.links("https://jobs.ashbyhq.com/Dex%20Mate%20Inc"))

    def test_social(self):
        self.assertEqual(self.links("https://twitter.com/DexmateAI")["x"], "https://x.com/DexmateAI")
        self.assertNotIn("x", self.links("https://x.com/intent/post?url=a", "https://x.com/share"))
        self.assertNotIn("x", self.links("https://x.com/wix"))
        self.assertNotIn("x", self.links("https://x.com/alice", "https://x.com/bob"))
        self.assertEqual(self.links("https://uk.linkedin.com/company/dexmate?trk=x")["linkedin"],
                         "https://www.linkedin.com/company/dexmate")
        self.assertNotIn("linkedin", self.links("https://www.linkedin.com/in/someone"))


class Verification(unittest.TestCase):
    def test_coined_name_on_its_own_domain(self):
        e = entity("Orkora", "energy", "Trademark goods: Nuclear fuel recycling; Nuclear reactors; power plants")
        v = verify(e, "orkora.com", ORKORA)
        self.assertTrue(v.accepted, v.reason)
        self.assertEqual(v.domain, "orkora.com")
        self.assertGreaterEqual(v.confidence, wp.ACCEPT_CONFIDENCE)
        for check in ("name_in_title", "name_full", "thesis_fit", "sector_agrees", "domain_exact", "name_coined"):
            self.assertIn(check, v.checks)

    def test_long_plain_name_that_is_the_site_name(self):
        e = entity("Swarm Defense Technologies", "autonomy")
        v = verify(e, "swarmdefense.com", SWARM_US, "https://www.swarmdefense.com/")
        self.assertTrue(v.accepted, v.reason)
        self.assertEqual(v.domain, "swarmdefense.com")
        self.assertIn("long_name_is_site_name", v.checks)
        self.assertIn("name_in_copyright", v.checks)

    def test_namesake_with_a_plain_name_is_refused(self):
        # The French SWARM Defense: same short name, same sector, another company.
        e = entity("Swarm Defense Technologies", "autonomy",
                   "Trademark goods: Software as a service (SAAS) services featuring software for the "
                   "operation and control of swarm technology for unmanned aerial vehicles (UAVs)")
        v = verify(e, "swarm-defense.com", SWARM_FR)
        self.assertFalse(v.accepted)
        self.assertIn("plain-word name", v.reason)
        self.assertTrue(v.reached_page)

    def test_plain_name_passes_with_a_second_fact(self):
        e = entity("Ocean Atomics", "energy",
                   "Trademark goods: Nuclear fuel recycling; treatment of water used in nuclear reactors; "
                   "production of energy by nuclear power plants")
        page = html("Ocean Atomics", "We are creating essential energy networks out at sea.",
                    "<p>Scale nuclear energy. Floating power plants with reactors cooled by sea water.</p>")
        v = verify(e, "oceanatomics.com", page)
        self.assertTrue(v.accepted, v.reason)
        self.assertIn("shared_terms", v.checks)
        self.assertIn("reactor", v.shared_terms)
        bare = dict(e, one_liner=None)
        self.assertFalse(verify(bare, "oceanatomics.com", page).accepted)

    def test_terms_that_echo_the_name_do_not_count(self):
        e = entity("Aura Robotics", "robotics", "Trademark goods: Humanoid robots; software for robots")
        page = html("AURA Robotics | Service Robots", "Commercial service robots for cleaning and delivery.",
                    "<p>Robots and robotic arms for hospitality.</p>")
        v = verify(e, "aurarobotics.io", page)
        self.assertFalse(v.accepted)
        self.assertEqual(v.shared_terms, [])

    def test_name_must_be_on_the_page_as_a_name(self):
        e = entity("Fluxara", "energy")
        v = verify(e, "fluxara.co", html("Amp3re — Power System Software", "", "<p>Battery energy storage.</p>"))
        self.assertFalse(v.accepted)
        self.assertIn("name not on the page", v.reason)
        # Only the first word of the name is there.
        e = entity("Swarm Defense Technologies")
        v = verify(e, "swarm.com", html("Swarm", "", "<p>Drone swarms and unmanned aircraft.</p>"))
        self.assertIn("name not on the page", v.reason)

    def test_page_must_be_on_thesis_without_the_names_help(self):
        e = entity("Parallel Robotics", "robotics")
        page = html("Parallel Robotics", "", "<p>Coming soon. Contact us.</p>")
        v = verify(e, "parallelrobotics.com", page)
        self.assertFalse(v.accepted)
        self.assertIn("not on thesis", v.reason)

    def test_sector_must_agree(self):
        e = entity("Umbra Lab", "energy", "Trademark goods: battery chargers")
        page = html("Umbra Lab", "", "<p>Radar satellites in orbit and spacecraft for earth observation.</p>")
        v = verify(e, "umbralab.com", page)
        self.assertFalse(v.accepted)
        self.assertIn("nothing on it about energy", v.reason)

    def test_coined_name_with_an_off_thesis_summary_needs_more(self):
        # fluxara.tech: an Indian biorefinery, matched to a US energy-software filer.
        e = entity("Fluxara", "energy",
                   "Trademark goods: Software as a service (SAAS) services featuring software for sizing "
                   "utility-scale battery energy storage systems (BESS)")
        page = html("Fluxara — Advanced Renewables & Applications",
                    "Fluxara is a recognised startup developing a circular biorefinery, converting "
                    "agricultural waste into specialty materials.",
                    "<h1>Turning agricultural waste into specialty materials</h1><p>500 kWp rooftop solar. "
                    "Silica for the battery separator mill. A manufacturer of precipitated silica.</p>")
        v = verify(e, "fluxara.tech", page)
        self.assertFalse(v.accepted)
        self.assertIn("summary is off thesis", v.reason)

    def test_parked_and_platform_pages(self):
        e = entity("Orkora", "energy")
        sale = html("orkora.io", "", "<p>This domain is for sale. Nuclear energy reactors power plants.</p>")
        self.assertIn("parked", verify(e, "orkora.io", sale).reason)
        self.assertIn("parked", verify(e, "orkora.io", ORKORA, "https://www.hugedomains.com/domain_profile.cfm?d=orkora.io").reason)
        self.assertIn("not a company domain", verify(e, "orkora.com", ORKORA, "https://www.linkedin.com/company/orkora").reason)
        v = verify(e, "orkora.com", ORKORA, "https://nuclear.mystrikingly.com/")
        self.assertIn("subdomain of an unrelated site", v.reason)
        self.assertFalse(v.reached_page)

    def test_redirect_final_host_is_the_domain(self):
        e = entity("Hylio", "autonomy")
        page = html("Hylio AgDrones | Autonomous, Precise, Swarm-Enabled",
                    "Hylio designs and manufactures drone systems for crop applications.",
                    "<p>Autonomous agricultural drones, unmanned aircraft and swarm software.</p>")
        v = verify(e, "hylio.com", page, "https://www.hyl.io/")
        self.assertTrue(v.accepted, v.reason)
        self.assertEqual(v.domain, "hyl.io")
        self.assertIn("redirect_followed", v.checks)
        self.assertIn("sector_not_in_name", v.checks)
        # A language subdomain of the company's own domain reduces to the domain.
        v = verify(entity("Orkora", "energy"), "orkora.com", ORKORA, "https://en.orkora.com/")
        self.assertEqual(v.domain, "orkora.com")

    def test_a_name_that_states_its_own_sector_needs_a_second_fact(self):
        # hadron-energy.com: a solar advisory firm, matched to a US reactor company of the same name.
        e = entity("Hadron Energy", "energy")
        solar = html("Hadron Energy", "", "<h1>PV consultancy experts</h1><p>Hadron Energy is a solar advisory "
                     "firm. Solar plants, energy storage and EV chargers.</p><footer>Copyright 2025 Hadron-Energy"
                     "</footer>")
        v = verify(e, "hadron-energy.com", solar)
        self.assertFalse(v.accepted)
        self.assertIn("name itself states the sector", v.reason)
        self.assertTrue(v.wants_fact)
        # "Veridis Defense" is known as an autonomy company: the name does not say that.
        e = entity("Veridis Defense", "autonomy", "Trademark goods: Unmanned aerial vehicles (UAVs)")
        page = html("Veridis Defense | Autonomous Fire Suppression", "Autonomous firefighting drone systems.",
                    "<p>Drones that detect and fight wildfire, one operator, one console.</p>")
        self.assertTrue(verify(e, "veridisdefense.com", page).accepted)

    def test_a_job_board_the_entity_is_known_by_is_a_second_fact(self):
        page_html = html("DYNA Robotics — Commercial-Grade Robots",
                         "DYNA Robotics builds commercial-grade robots for factory operations.",
                         '<p>Robots that fold, humanoid manipulation, embodied AI.</p><a href="/careers">Careers</a>')
        careers = wp.parse_page('<a href="https://jobs.ashbyhq.com/dyna-robotics/0733-aa35">Engineer</a>')
        bare = entity("Dyna Robotics", "robotics")
        known = dict(bare, ats=[{"provider": "ashby", "slug": "dyna-robotics"}])
        page = wp.parse_page(page_html)
        self.assertFalse(wp.verify_page(bare, "dyna.co", "guess", "https://www.dyna.co/", page, careers=careers).accepted)
        self.assertFalse(wp.verify_page(known, "dyna.co", "guess", "https://www.dyna.co/", page).accepted)
        v = wp.verify_page(known, "dyna.co", "guess", "https://www.dyna.co/", page, careers=careers)
        self.assertTrue(v.accepted, v.reason)
        self.assertIn("known_link_on_careers_page", v.checks)
        # Somebody else's board proves nothing.
        other = wp.parse_page('<a href="https://jobs.ashbyhq.com/dyna-robotics-europe">x</a>')
        self.assertFalse(wp.verify_page(known, "dyna.co", "guess", "https://www.dyna.co/", page, careers=other).accepted)
        # The same board linked from the homepage itself.
        direct = wp.parse_page(page_html.replace("/careers", "https://jobs.ashbyhq.com/dyna-robotics"))
        v = wp.verify_page(known, "dyna.co", "guess", "https://www.dyna.co/", direct)
        self.assertIn("known_link_on_page", v.checks)
        # The job board rides along on the signal.
        s = wp.to_signal(known, wp.verify_page(known, "dyna.co", "guess", "https://www.dyna.co/", page, careers=careers),
                         TODAY.isoformat())
        self.assertEqual((s.metrics["ats_provider"], s.metrics["ats_slug"]), ("ashby", "dyna-robotics"))

    def test_known_accounts(self):
        page = wp.Page(hrefs=["https://github.com/dexmate-ai/sdk", "https://x.com/DexmateAI",
                              "https://www.linkedin.com/company/dexmate/about"],
                       srcs=["https://boards.greenhouse.io/embed/job_board/js?for=dexmate"])
        self.assertTrue(wp.known_link_on_page({"github": "dexmate-ai"}, page))
        self.assertFalse(wp.known_link_on_page({"github": "dexmate"}, page))
        self.assertTrue(wp.known_link_on_page({"ats": [{"provider": "greenhouse", "slug": "dexmate"}]}, page))
        self.assertTrue(wp.known_link_on_page({"links": {"x": "https://x.com/dexmateai"}}, page))
        self.assertTrue(wp.known_link_on_page({"links": {"linkedin": "https://www.linkedin.com/company/dexmate"}}, page))
        self.assertFalse(wp.known_link_on_page({"links": {"website": "https://dexmate.com"}}, page))
        self.assertFalse(wp.known_link_on_page({"github": "dexmate-ai"}, None))
        self.assertTrue(wp.has_known_accounts({"ats": [{"provider": "lever", "slug": "x"}]}))
        self.assertFalse(wp.has_known_accounts({"links": {"website": "https://x.com"}, "ats": [], "github": None}))

    def test_careers_url(self):
        def url(*hrefs):
            return wp.careers_url(wp.Page(hrefs=list(hrefs)), "https://www.dyna.co/")
        self.assertEqual(url("/about", "/careers#open"), "https://www.dyna.co/careers")
        self.assertEqual(url("https://dyna.co/jobs/"), "https://dyna.co/jobs/")
        self.assertEqual(url("join-us"), "https://www.dyna.co/join-us")
        self.assertIsNone(url("mailto:careers@dyna.co", "https://jobs.lever.co/dyna", "https://other.com/careers",
                              "/career-stories-blog"))

    def test_one_word_name_needs_a_domain_that_carries_it(self):
        e = entity("Orkora", "energy")
        v = verify(e, "bigutility.com", ORKORA, source="clearbit")
        self.assertFalse(v.accepted)
        self.assertIn("does not carry it", v.reason)

    def test_weak_names(self):
        page = html("Mantle", "The pit crew for machines and robots.",
                    "<p>Robot maintenance, robotic fleet repair, humanoid service and industrial automation.</p>")
        bare = entity("Mantle", "robotics")
        self.assertIn("no second corroborating fact", verify(bare, "mantle.com", page).reason)
        told = entity("Mantle", "robotics", "Humanoid robot maintenance and industrial automation for fleets")
        v = verify(told, "mantle.com", page)
        self.assertTrue(v.accepted, v.reason)
        self.assertIn("name_weak", v.checks)
        self.assertGreaterEqual(v.confidence, wp.ACCEPT_CONFIDENCE_WEAK)
        self.assertIn("domain is not the name", verify(told, "getmantle.io", page).reason)

    def test_location_confirms_or_contradicts(self):
        e = entity("Tera AI", "robotics", location="San Francisco, California")
        page = html("Enabling cross-domain autonomy | Tera AI", "Zero-shot navigation for robots.",
                    "<p>Robots that navigate. Autonomy software. Built in San Francisco.</p>")
        self.assertFalse(verify(dict(e, location=None), "tera-ai.com", page).accepted)
        v = verify(e, "tera-ai.com", page)
        self.assertTrue(v.accepted, v.reason)
        self.assertIn("location_on_page", v.checks)
        # americandrone.us: a Wisconsin dealer, not the Atlanta filer.
        e = entity("The American Drone", "autonomy", "Trademark goods: Unmanned aerial vehicles (UAVs)",
                   location="Atlanta, Georgia")
        page = html("Home - American Drone, LLC", "", "<p>Agricultural spray drones, lidar and radar drone "
                    "sensors.</p><footer>American Drone, LLC | 213 Air Park Road | Marshfield, WI 54449</footer>")
        self.assertIn("US address in WI", verify(e, "americandrone.us", page).reason)

    def test_location_on_page(self):
        self.assertEqual(wp.location_on_page("Auburn Hills, Michigan", "HQ: Auburn Hills, MI 48326"), (True, ""))
        self.assertEqual(wp.location_on_page(None, "anything"), (False, ""))
        self.assertEqual(wp.location_on_page("Spain", "Madrid, Spain"), (False, ""))
        _, why = wp.location_on_page("Brooklyn, New York", "Founder D. K. Based in Astana · Kazakhstan")
        self.assertIn("Kazakhstan", why)
        self.assertEqual(wp.location_on_page("Dallas, Texas", "Based in the heart of Texas and shipping to India"),
                         (False, ""))
        self.assertEqual(wp.location_on_page("Munich, Germany", "Based in Garching, Germany"), (False, ""))

    def test_shared_words(self):
        e = entity("Angel Aerial Systems", one_liner="Maker of the Trio Scout unmanned aircraft")
        hit, total = wp.shared_words(e, {"trio", "scout", "drone"})
        self.assertEqual((hit, total), (["trio", "scout"], 2))
        hit, total = wp.shared_words(entity("X Co", one_liner=None), {"a"})
        self.assertEqual((hit, total), ([], 0))

    def test_choose(self):
        e = entity("Power-on-Demand", "energy", "battery energy storage load balancing software for the grid")
        a = verify(e, "powerondemand.in", html("Power On Demand", "", "<p>Battery energy storage for the grid, solar.</p>"))
        b = verify(e, "pod-llc.com", html("Power-on-Demand: Battery Energy Storage", "",
                                          "<p>Battery energy storage for oil and gas, grid power.</p>"))
        self.assertTrue(a.accepted and b.accepted, (a.reason, b.reason))
        chosen, why = wp.choose([a, b])
        self.assertIsNone(chosen)
        self.assertIn("ambiguous", why)
        # The same site under two addresses is one site.
        twin = verify(e, "powerondemand.co", html("Power On Demand", "", "<p>Battery energy storage for the grid, solar.</p>"))
        self.assertIs(wp.choose([a, twin])[0], a)
        same = verify(e, "powerondemand.io", html("Power On Demand", "", "<p>Battery energy storage, grid, solar.</p>"),
                      "https://powerondemand.in/")
        self.assertIs(wp.choose([a, same])[0], a)
        self.assertEqual(wp.choose([]), (None, ""))
        rejected = verify(entity("Fluxara", "energy"), "fluxara.co", html("Other", "", ""))
        self.assertEqual(wp.choose([rejected]), (None, ""))


class Targets(unittest.TestCase):
    def test_only_companies_without_a_domain(self):
        known = [
            entity("Orkora"), dict(entity("Skild AI"), domain="skild.ai"),
            dict(entity("px4-nav"), kind="project"), dict(entity("Jane Doe"), kind="person"),
            dict(entity(""), name=""), dict(entity("x"), name=None), "junk", entity("Exowatt"),
        ]
        self.assertEqual([e["name"] for e in wp.targets(known, None)], ["Orkora", "Exowatt"])
        self.assertEqual([e["name"] for e in wp.targets(known, 1)], ["Orkora"])

    def test_module_cap(self):
        known = [entity(f"Company{i}") for i in range(wp.MAX_LOOKUPS + 40)]
        self.assertEqual(len(wp.targets(known, None)), wp.MAX_LOOKUPS)
        self.assertEqual(len(wp.targets(known, 10_000)), wp.MAX_LOOKUPS)


class SignalShape(unittest.TestCase):
    def test_signal(self):
        e = entity("Exowatt", "energy", "Trademark goods: solar thermal-based power plants; thermal energy storage")
        v = verify(e, "exowatt.com", PAGE, "https://www.exowatt.com/")
        self.assertTrue(v.accepted, v.reason)
        s = wp.to_signal(e, v, TODAY.isoformat())
        s.validate()
        self.assertEqual((s.source, s.family, s.kind), ("web_presence", "traffic", "website_verified"))
        self.assertEqual(s.strength, 0.0)
        self.assertEqual(s.title, "Website verified at exowatt.com")
        self.assertEqual(s.occurred_at, "2026-10-01")
        self.assertEqual(s.url, "https://www.exowatt.com/")
        self.assertEqual(s.entity.name, "Exowatt")          # exactly as known, so it resolves to the same entity
        self.assertEqual(s.entity.domain, "exowatt.com")
        self.assertEqual(s.entity.kind, "company")
        self.assertEqual(s.entity.github, "exowatt")
        self.assertEqual(s.entity.links, {"x": "https://x.com/exowattenergy",
                                          "linkedin": "https://www.linkedin.com/company/exowatt"})
        self.assertEqual(s.entity.one_liner,
                         "Exowatt is redefining energy for the AI era. Delivering clean, modular power.")
        m = s.metrics
        self.assertIs(m["observed_only"], True)
        self.assertEqual(m["final_url"], "https://www.exowatt.com/")
        self.assertEqual((m["ats_provider"], m["ats_slug"]), ("lever", "exowatt"))
        self.assertTrue(0.0 <= m["confidence"] <= 1.0)
        self.assertIn("thesis_fit", m["checks"])
        self.assertTrue(s.text.startswith("Exowatt | Powering AI"))
        self.assertIn("Modular energy for data centers", s.text)
        self.assertLessEqual(len(s.text), wp.SIGNAL_TEXT_CHARS + 400)


class EndToEnd(unittest.TestCase):
    """collect() with the three network functions replaced."""

    PAGES = {
        "orkora.com": ("https://orkora.com/", ORKORA),
        "swarmdefense.com": ("https://www.swarmdefense.com/", SWARM_US),
        "swarm-defense.com": ("https://swarm-defense.com/", SWARM_FR),
        "orkora.io": ("https://www.hugedomains.com/domain_profile.cfm?d=orkora.io", html("orkora.io is for sale")),
    }

    def run_collect(self, known, **kw):
        asked: list[str] = []

        def fake_exists(host):
            asked.append(host)
            return host in self.PAGES or host == "brokenco.com" or host == "refused.com"

        def fake_fetch(host):
            if host == "brokenco.com":
                raise RuntimeError("boom")
            if host == "refused.com":
                raise http.HttpError(403, f"https://{host}/")
            return self.PAGES[host]

        def fake_suggest(name):
            if name == "Refused":
                raise OSError("autocomplete down")
            return []

        c = ctx(known=known, **kw)
        with mock.patch.object(wp, "host_exists", fake_exists), mock.patch.object(wp, "fetch_homepage", fake_fetch), \
                mock.patch.object(wp, "suggest", fake_suggest), mock.patch.object(wp, "_install_final_url_hook", lambda: None), \
                mock.patch.object(http, "request", side_effect=AssertionError("network in a test")):
            signals = list(wp.collect(c))
        return signals, c, asked

    def test_run(self):
        known = [
            entity("Orkora", "energy", "Trademark goods: Nuclear reactors"),
            dict(entity("Skild AI", "robotics", "General purpose robotic intelligence"), domain="skild.ai"),
            entity("Swarm Defense Technologies", "autonomy"),
            dict(entity("px4-nav"), kind="project"),
            entity("Brokenco", "energy"),
            entity("Refused", "energy"),
            entity("CX2", "autonomy"),
        ]
        signals, c, asked = self.run_collect(known)
        self.assertEqual([(s.entity.name, s.entity.domain) for s in signals],
                         [("Orkora", "orkora.com"), ("Swarm Defense Technologies", "swarmdefense.com")])
        for s in signals:
            s.validate()
        # Every guess is looked at, even after one verifies: that is how a namesake is found.
        self.assertEqual([h for h in asked if h.startswith("orkora")], wp.guess_hosts("Orkora", "energy"))
        self.assertIn("swarm-defense.com", asked)
        # Entities with a domain, projects and unverifiable names cost no lookups.
        self.assertFalse([h for h in asked if h.startswith(("skild", "px4", "cx2"))])
        log = "\n".join(c.logged)
        self.assertIn("+ Orkora -> orkora.com", log)
        self.assertIn("+ Swarm Defense Technologies -> swarmdefense.com", log)
        self.assertIn("Refused ~ refused.com: homepage answered HTTP 403", log)
        self.assertIn("CX2: weak name and nothing to corroborate it with", log)
        self.assertIn("2 verified of 5 companies", log)
        self.assertEqual(c.warnings, [])

    def test_rejected_candidates_are_logged_with_the_reason(self):
        # Only the namesake exists: nothing is emitted and the log says why.
        french_only = {"swarm-defense.com": self.PAGES["swarm-defense.com"], "orkora.io": self.PAGES["orkora.io"]}
        with mock.patch.object(self, "PAGES", french_only):
            signals, c, _ = self.run_collect([entity("Swarm Defense Technologies", "autonomy"),
                                              entity("Orkora", "energy")])
        self.assertEqual(signals, [])
        log = "\n".join(c.logged)
        self.assertIn("- Swarm Defense Technologies ~ swarm-defense.com: plain-word name", log)
        self.assertNotIn("orkora.io", log)       # a parked page is not a near miss
        self.assertIn("0 verified of 2 companies", log)

    def test_limit_and_empty(self):
        known = [entity("Orkora", "energy"), entity("Swarm Defense Technologies", "autonomy")]
        signals, c, asked = self.run_collect(known, limit=1)
        self.assertEqual([s.entity.name for s in signals], ["Orkora"])
        self.assertFalse([h for h in asked if h.startswith("swarm")])
        signals, c, asked = self.run_collect([dict(entity("Skild AI", one_liner="Robot brains"), domain="skild.ai")])
        self.assertEqual((signals, asked), ([], []))
        self.assertIn("no known companies without a domain or without a description", "\n".join(c.logged))

    def test_a_crash_in_one_company_is_a_warning(self):
        known = [entity("Orkora", "energy"), entity("Exowatt", "energy")]
        real = wp.identify

        def flaky(e):
            if e["name"] == "Orkora":
                raise ValueError("bad page")
            return real(e)

        with mock.patch.object(wp, "identify", flaky):
            signals, c, _ = self.run_collect(known)
        self.assertEqual(signals, [])
        self.assertEqual(len(c.warnings), 1)
        self.assertIn("Orkora: ValueError: bad page", c.warnings[0])


class Network(unittest.TestCase):
    """The thin wrappers, with antenna.http stubbed."""

    def test_host_exists(self):
        answers = {
            "orkora.com": {"Status": 0, "Answer": [{"name": "orkora.com.", "type": 1, "data": "1.2.3.4"}]},
            "nope-zz.com": {"Status": 3},
            "wwwonly.com": {"Status": 0},
            "www.wwwonly.com": {"Status": 0, "Answer": [{"type": 5, "data": "x."}, {"type": 1, "data": "1.2.3.4"}]},
            "nodata.com": {"Status": 0}, "www.nodata.com": {"Status": 0},
        }
        calls = []

        def fake_get_json(url, **kw):
            calls.append((url, kw))
            name = kw["params"]["name"]
            if name == "down.com":
                raise OSError("resolver down")
            return answers[name]

        with mock.patch.object(http, "get_json", fake_get_json):
            self.assertIs(wp.host_exists("orkora.com"), True)
            self.assertIs(wp.host_exists("nope-zz.com"), False)
            self.assertIs(wp.host_exists("wwwonly.com"), True)
            self.assertIs(wp.host_exists("nodata.com"), False)
            self.assertIsNone(wp.host_exists("down.com"))
        for url, kw in calls:
            self.assertIn(url, wp.RESOLVERS)
            self.assertEqual(kw["params"]["type"], "A")
            self.assertEqual(kw["ttl"], wp.DNS_TTL)
        # A host always goes to the same resolver, so the cache can answer it next time.
        urls = {kw["params"]["name"]: url for url, kw in calls}
        with mock.patch.object(http, "get_json", fake_get_json):
            calls.clear()
            wp.host_exists("orkora.com")
        self.assertEqual(calls[0][0], urls["orkora.com"])

    def stub_request(self, script):
        seen = []

        def fake_request(url, **kw):
            seen.append((url, kw))
            result = script[url]
            if isinstance(result, list):
                result = result.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        return seen, fake_request

    def test_fetch_uses_the_final_url_and_house_settings(self):
        seen, fake = self.stub_request({
            "https://dynarobotics.ai/": ("<title>DYNA</title>", {wp.FINAL_URL_HEADER: "https://www.dyna.co/"}),
        })
        with mock.patch.object(http, "request", fake):
            self.assertEqual(wp.fetch_homepage("dynarobotics.ai"), ("https://www.dyna.co/", "<title>DYNA</title>"))
        url, kw = seen[0]
        self.assertEqual(kw["timeout"], 12)
        self.assertEqual(kw["ttl"], 24 * 3600)
        self.assertNotIn("User-Agent", kw["headers"])
        self.assertTrue(kw["return_headers"])

    def test_fetch_falls_back_and_respects_refusals(self):
        seen, fake = self.stub_request({
            "https://a.com/": OSError("certificate verify failed"),
            "https://www.a.com/": ("<title>A</title>", {}),
        })
        with mock.patch.object(http, "request", fake):
            self.assertEqual(wp.fetch_homepage("a.com"), ("https://www.a.com/", "<title>A</title>"))
        seen, fake = self.stub_request({"https://b.com/": http.HttpError(403, "https://b.com/")})
        with mock.patch.object(http, "request", fake):
            with self.assertRaises(http.HttpError):
                wp.fetch_homepage("b.com")
        self.assertEqual(len(seen), 1)      # a refusal is not worked around
        seen, fake = self.stub_request({"https://c.com/": TimeoutError("timed out")})
        with mock.patch.object(http, "request", fake):
            with self.assertRaises(TimeoutError):
                wp.fetch_homepage("c.com")
        self.assertEqual(len(seen), 1)      # nothing is listening: stop

    def test_fetch_retries_a_server_error_once(self):
        seen, fake = self.stub_request({
            "https://d.com/": [http.HttpError(503, "https://d.com/"), ("<title>D</title>", {})],
        })
        with mock.patch.object(http, "request", fake), mock.patch.object(wp.time, "sleep") as nap:
            self.assertEqual(wp.fetch_homepage("d.com")[1], "<title>D</title>")
        nap.assert_called_once_with(wp.RETRY_PAUSE)
        seen, fake = self.stub_request({
            "https://e.com/": [http.HttpError(503, "https://e.com/"), http.HttpError(503, "https://e.com/")],
        })
        with mock.patch.object(http, "request", fake), mock.patch.object(wp.time, "sleep"):
            with self.assertRaises(http.HttpError):
                wp.fetch_homepage("e.com")
        self.assertEqual(len(seen), 2)

    def test_fetch_follows_one_meta_refresh(self):
        seen, fake = self.stub_request({
            "https://f.com/": ('<meta http-equiv="refresh" content="0; url=https://g.com/home">', {}),
            "https://g.com/home": ("<title>G</title>", {wp.FINAL_URL_HEADER: "https://g.com/home"}),
        })
        with mock.patch.object(http, "request", fake):
            self.assertEqual(wp.fetch_homepage("f.com"), ("https://g.com/home", "<title>G</title>"))

    def test_careers_page_is_read_only_when_it_can_settle_the_match(self):
        home = html("DYNA Robotics — Commercial-Grade Robots", "DYNA Robotics builds commercial-grade robots.",
                    '<p>Robots that fold, humanoid manipulation, embodied AI.</p><a href="/careers">Careers</a>')
        script = {
            "https://dyna.co/": (home, {wp.FINAL_URL_HEADER: "https://www.dyna.co/"}),
            "https://www.dyna.co/careers": ('<a href="https://jobs.ashbyhq.com/dyna-robotics/1">Role</a>', {}),
        }
        known = dict(entity("Dyna Robotics", "robotics"), ats=[{"provider": "ashby", "slug": "dyna-robotics"}])
        seen, fake = self.stub_request(script)
        with mock.patch.object(http, "request", fake):
            v = wp.verify_host(known, "dyna.co", "guess")
        self.assertTrue(v.accepted, v.reason)
        self.assertEqual([u for u, _ in seen], ["https://dyna.co/", "https://www.dyna.co/careers"])
        # No recorded account: nothing the careers page could confirm, so it is not fetched.
        seen, fake = self.stub_request(script)
        with mock.patch.object(http, "request", fake):
            v = wp.verify_host(entity("Dyna Robotics", "robotics"), "dyna.co", "guess")
        self.assertFalse(v.accepted)
        self.assertEqual([u for u, _ in seen], ["https://dyna.co/"])
        # A careers page that fails to load leaves the verdict as it was.
        seen, fake = self.stub_request({**script, "https://www.dyna.co/careers": OSError("reset")})
        with mock.patch.object(http, "request", fake):
            self.assertFalse(wp.verify_host(known, "dyna.co", "guess").accepted)

    def test_final_url_tag_only_on_our_own_thread(self):
        class Resp:
            def __init__(self):
                self.headers = {}

            def geturl(self):
                return "https://final.example/"

        tagger = wp._FinalUrlTagger()
        wp._tls.tag = False
        self.assertEqual(tagger.http_response(None, Resp()).headers, {})
        wp._tls.tag = True
        try:
            r = Resp()
            r.headers[wp.FINAL_URL_HEADER] = "stale"
            self.assertEqual(tagger.https_response(None, r).headers, {wp.FINAL_URL_HEADER: "https://final.example/"})
        finally:
            wp._tls.tag = False

    def test_suggest_query(self):
        with mock.patch.object(http, "get_json", return_value=[{"name": "Asylon", "domain": "asylonrobotics.com"}]) as g:
            self.assertEqual(wp.suggest("Asylon, Inc."), [("asylonrobotics.com", "Asylon")])
        self.assertEqual(g.call_args.args[0], wp.SUGGEST_URL)
        self.assertEqual(g.call_args.kwargs["params"], {"query": "Asylon"})


RADIANT = html(
    "Radiant", "Radiant builds Kaleidos, a portable, factory-built nuclear microreactor that delivers a megawatt "
    "of clean power anywhere it is needed.", "<h1>Portable nuclear power</h1><p>Kaleidos microreactor.</p>")
LAW_FIRM = html(
    "Hale & Dorr LLP | Communications Counsel",
    "Hale & Dorr advises wireless carriers and satellite operators on FCC licensing matters.",
    "<h1>Regulatory counsel</h1><p>Our clients include BlueCore Energy, Acme Wireless and others.</p>"
    "<footer>© 2026 Hale & Dorr LLP</footer>")
BLUECORE = html(
    "Bluecore Energy | Floating Nuclear Power for Ports & Cities",
    "A compact 10 MWe modular reactor on a barge — zero-emission power that goes where it's needed.",
    '<h1>Power that floats</h1><p>Built at the Port of Long Beach.</p><a href="https://jobs.lever.co/bluecore-energy">'
    'Careers</a><a href="https://x.com/bluecore">X</a><a href="https://github.com/PX4/PX4-Autopilot">PX4</a>'
    "<footer>© 2026 BlueCore Energy, Inc.</footer>")


def known(name: str, domain: str, one_liner: str | None = None, **kw) -> dict:
    return dict(entity(name, kw.pop("sector", "energy"), one_liner, **kw), domain=domain)


class Sentences(unittest.TestCase):
    def test_abbreviations_do_not_end_a_sentence(self):
        s = "Stucan Solutions provides testing to the U.S. Government and its allies. More follows here."
        self.assertEqual([s[:e] for e in wp.sentence_ends(s)],
                         ["Stucan Solutions provides testing to the U.S. Government and its allies.", s])
        self.assertEqual(wp.sentence_ends("Acme, Inc. builds robots"), [])
        self.assertEqual(len(wp.sentence_ends("It costs $2.5 trillion a year. Really! Is it so?")), 3)

    def test_trim_keeps_whole_sentences(self):
        long = ("Stucan Solutions specialize in providing threat understanding, security evaluation, prototyping, "
                "and testing capabilities to the U.S. DoD, Federal civilian agencies, international and commercial "
                "customers across a wide range of mission areas and domains.")
        out = wp._trim_liner(long)
        self.assertLessEqual(len(out), wp.ONE_LINER_CHARS)
        self.assertTrue(out.endswith("…"))
        self.assertIn("to the U.S. DoD", out)          # not cut at "U.S."

    def test_a_cut_off_tail_is_dropped(self):
        excerpt = ("darkflite is offering innovative, dual-use drone solutions to enhance tactical capabilities. "
                   "Based on blueflite’s unique, patent-pending design, it features")
        self.assertEqual(wp._trim_liner(excerpt),
                         "darkflite is offering innovative, dual-use drone solutions to enhance tactical capabilities.")
        whole = ("Enabling profitable LEO satellite constellations with standardized satellites. "
                 "100+ satellites deployed to orbit")
        self.assertEqual(wp._trim_liner(whole), whole)
        self.assertEqual(wp._trim_liner("Founded in 2018, blueflite ® offers a drone-based logistics platform."),
                         "Founded in 2018, blueflite® offers a drone-based logistics platform.")

    def test_a_bare_tagline_is_not_a_description(self):
        page = wp.Page(title="Fortem Technologies", description="Airspace Awareness Safety & Security")
        self.assertIsNone(wp.clean_description(page, "Fortem Technologies"))


class Hero(unittest.TestCase):
    def hero(self, body: str, name: str) -> str | None:
        return wp.hero_sentence(wp.parse_page(html("T", "", body)), name)

    def test_blocks_are_paragraphs_and_headings(self):
        page = wp.parse_page("<body><nav><a>Home</a><a>About</a></nav><h1>Big <em>claim</em></h1><p>First para."
                             "<p>Unclosed second<h2>Head</h2><div>loose text</div><script>x</script></body>")
        self.assertEqual(page.blocks, ["Big claim", "First para.", "Unclosed second", "Head"])

    def test_a_paragraph_that_opens_with_the_name_wins(self):
        body = ("<h1>A $2.5 Trillion Problem</h1><p>Corrosion silently eats away at infrastructure around the world, "
                "costing trillions annually.</p><p>FibrX redefines infrastructure monitoring with a fusion of fiber "
                "optic sensors, AI analytics and cloud reporting.</p>")
        self.assertTrue(self.hero(body, "FIBRX").startswith("FibrX redefines infrastructure monitoring"))
        body = "<p>Why is every shipper looking up?</p><p>Founded in 2018, blueflite ® offers a drone-based " \
               "logistics platform for faster deliveries.</p>"
        self.assertEqual(self.hero(body, "blueflite"),
                         "Founded in 2018, blueflite® offers a drone-based logistics platform for faster deliveries.")
        # The name without its generic word, when that word is made up.
        body = "<p>Protecting the world, one city at a time, with radar and drones.</p><p>Fortem delivers advanced " \
               "radar systems and autonomous drones that safeguard the airspace.</p>"
        self.assertTrue(self.hero(body, "Fortem Technologies").startswith("Fortem delivers"))

    def test_plain_first_word_alone_is_not_the_name(self):
        body = "<p>Last year the grid failed in three states and nobody was ready for it.</p>"
        self.assertEqual(wp._self_names("Last Energy"), ["lastenergy"])
        self.assertTrue(self.hero(body, "Last Energy").startswith("Last year"))    # as plain prose, not as a self-description
        body = "<p>A problem statement that comes first on the page, as usual.</p>" + body
        self.assertTrue(self.hero(body, "Last Energy").startswith("A problem statement"))

    def test_a_customers_praise_is_passed_over(self):
        body = ("<p>Fast, affordable software design and development for entrepreneurs and small business.</p>"
                "<p>Smarter Reality developed a terrific design that fit both our vision and our budget.</p>")
        self.assertEqual(self.hero(body, "Smarter Reality"),
                         "Fast, affordable software design and development for entrepreneurs and small business.")
        # A mention of the name that does not open the paragraph is not a self-description.
        body = ("<p>Bringing the production of MXenes, a new class of nanomaterial, to industrial scale.</p>"
                "<p>Ti3C2 MXene has shown an IR emissivity as low as polished gold in recent tests.</p>")
        self.assertTrue(self.hero(body, "MXENE").startswith("Bringing the production"))

    def test_first_prose_when_nothing_names_the_company(self):
        body = ("<h1>Make rain. Make snow.</h1><p>Home About Products Careers Contact Press Investors Login</p>"
                "<p>The Unity Nuclear Battery is a compact, modular, transportable microreactor system.</p>")
        self.assertEqual(self.hero(body, "Deployable Energy"),
                         "The Unity Nuclear Battery is a compact, modular, transportable microreactor system.")

    def test_nothing_usable(self):
        for body in ("", "<h1>Orkora</h1>", "<p>Make rain. Make snow. Make Earth habitable again.</p>",
                     "<p>We use cookies to improve your experience on this website and for analytics.</p>",
                     "<p>© 2026 Acme Robotics, Inc. All rights reserved worldwide, including all subsidiaries.</p>",
                     "<p>Subscribe to our newsletter to hear about new products and company events first.</p>",
                     "<p>PLATFORMS CAPABILITIES TECHNOLOGY CAREERS NEWS CONTACT INVESTORS PARTNERS</p>"):
            self.assertIsNone(self.hero(body, "Acme Robotics"), body)

    def test_one_liner_order(self):
        body = "<p>Acme Robotics builds autonomous mobile robots for rack maintenance in data centers.</p>"
        meta = "Autonomous mobile robots that keep data center racks running around the clock."
        page = wp.parse_page(html("Acme", meta, body, head='<meta property="og:description" content="OG words here '
                                  'describing the company in one sentence.">'))
        self.assertEqual(wp.page_one_liner(page, "Acme Robotics"), meta)
        page = wp.parse_page(html("Acme", "Home", body, head='<meta property="og:description" content="OG words '
                                  'here describing the company in one sentence.">'))
        self.assertEqual(wp.page_one_liner(page, "Acme Robotics"), "OG words here describing the company in one sentence.")
        page = wp.parse_page(html("Acme", "", body))
        self.assertTrue(wp.page_one_liner(page, "Acme Robotics").startswith("Acme Robotics builds"))
        self.assertIsNone(wp.page_one_liner(wp.parse_page(html("Acme", "", "<h1>Acme</h1>")), "Acme Robotics"))


class Describe(unittest.TestCase):
    def test_who_lacks_a_description(self):
        self.assertTrue(wp.lacks_description({"one_liner": None}))
        self.assertTrue(wp.lacks_description({"one_liner": "  "}))
        self.assertTrue(wp.lacks_description({"one_liner": "Trademark goods: Nuclear reactors; power plants"}))
        self.assertFalse(wp.lacks_description({"one_liner": "Maker of the F-11 unmanned aircraft"}))
        self.assertFalse(wp.lacks_description({"one_liner": "We build floating reactors."}))

    def test_targets(self):
        k = [
            known("BlueCore Energy", "bluecore.energy"),
            known("Orkora", "orkora.com", "Trademark goods: Nuclear reactors"),
            known("Skild AI", "skild.ai", "General purpose robotic intelligence"),
            entity("No Domain Co"),
            dict(known("kicad-mcp", "kicad-mcp.dev"), kind="project"),
            dict(known("x", "x.com"), name=""), "junk",
            known("AMPERA", "amperaglobal.com"),
        ]
        self.assertEqual([e["name"] for e in wp.describe_targets(k, None)], ["BlueCore Energy", "Orkora", "AMPERA"])
        self.assertEqual([e["name"] for e in wp.describe_targets(k, 2)], ["BlueCore Energy", "Orkora"])
        many = [known(f"Company{i}", f"company{i}.com") for i in range(wp.MAX_DESCRIBE + 30)]
        self.assertEqual(len(wp.describe_targets(many, None)), wp.MAX_DESCRIBE)
        # The two modes never take the same entity.
        self.assertEqual([e["name"] for e in wp.targets(k, None)], ["No Domain Co"])

    def page(self, e: dict, markup: str, final_url: str | None = None) -> wp.Verdict:
        return wp.describe_page(e, final_url or f"https://{e['domain']}/", wp.parse_page(markup), markup)

    def test_name_on_the_page_is_enough(self):
        v = self.page(known("BlueCore Energy", "bluecore.energy"), BLUECORE)
        self.assertTrue(v.accepted, v.reason)
        self.assertIn("name_in_title", v.checks)
        # No thesis fit and no second fact are asked: an off-thesis page of a plain-word name still describes it.
        weather = html("MyRadar | Keeping you ahead of the storm", "Mobile Weather and Radar App for iOS and Android",
                       "<p>Download the app.</p>")
        self.assertTrue(self.page(known("MyRadar", "myradar.com", sector="autonomy"), weather).accepted)
        plain = html("Aura Robotics | Service Robots", "Commercial service robots for cleaning.", "<p>Robots.</p>")
        self.assertTrue(self.page(known("Aura Robotics", "aurarobotics.io", sector="robotics"), plain).accepted)
        # The legal name in the copyright line of a site trading under another name.
        dba = html("Ixana | The second nervous system", "Ixana builds E-field communication silicon.",
                   "<p>Chips.</p><footer>© 2026 Quasistatics Inc. All Rights Reserved.</footer>")
        v = self.page(known("QUASISTATICS", "ixana.ai", sector="semiconductors"), dba)
        self.assertEqual(v.checks, ["name_in_copyright", "name_full"])

    def test_somebody_elses_domain_is_not_described(self):
        # The domain of the lawyer who filed: the client is named in the text, not as the site's name.
        v = self.page(known("BlueCore Energy", "haledorr.com"), LAW_FIRM)
        self.assertFalse(v.accepted)
        self.assertIn("name not on the page as a name", v.reason)
        self.assertTrue(v.reached_page)
        # A parent company's site.
        parent = html("newcleo | Futurable energy", "newcleo designs lead-cooled fast reactors.", "<p>Reactors.</p>")
        self.assertFalse(self.page(known("newcleo Americas", "newcleo.com"), parent).accepted)

    def test_stem_counts_only_on_a_domain_that_starts_with_the_name(self):
        v = self.page(known("Radiant Industries", "radiantnuclear.com"), RADIANT)
        self.assertTrue(v.accepted, v.reason)
        self.assertEqual(v.checks, ["name_stem_on_own_domain"])
        aalo = html("Aalo - Welcome to the dawn of a Second Atomic Age", "Creating abundant clean energy.", "")
        self.assertTrue(self.page(known("Aalo Holdings", "aalo.com"), aalo).accepted)
        self.assertFalse(self.page(known("Radiant Industries", "smithconsulting.com"), RADIANT).accepted)
        self.assertFalse(self.page(known("Radiant Industries", "getradiant.com"), RADIANT).accepted)
        # A sector word is still part of the name where the full name is needed: "Swarm" is not Swarm Defense.
        swarm = html("Swarm", "Decentralised storage and communication.", "<p>Bees.</p>")
        self.assertFalse(self.page(known("Swarm Defense Technologies", "swarmlabs.io"), swarm).accepted)

    def test_parked_and_generic_endings(self):
        e = known("BlueCore Energy", "bluecore.energy")
        self.assertIn("parked", self.page(e, html("bluecore.energy", "", "<p>This domain is for sale.</p>")).reason)
        self.assertIn("parked", self.page(e, BLUECORE, "https://www.hugedomains.com/domain_profile.cfm?d=x").reason)
        self.assertIn("not a company domain", self.page(e, BLUECORE, "https://www.linkedin.com/company/bluecore").reason)
        self.assertIn("too short", self.page(known("X", "x.co"), BLUECORE).reason)

    def test_signal(self):
        e = known("BlueCore Energy", "bluecore.energy", "Trademark goods: Nuclear reactors")
        v = self.page(e, BLUECORE, "https://www.bluecore.energy/")
        s = wp.to_description_signal(e, v, TODAY.isoformat())
        s.validate()
        self.assertEqual((s.source, s.family, s.kind, s.strength), ("web_presence", "traffic", "website_described", 0.0))
        self.assertEqual(s.title, "Website on file at bluecore.energy")
        self.assertEqual(s.occurred_at, "2026-10-01")
        self.assertEqual(s.url, "https://www.bluecore.energy/")
        self.assertEqual((s.entity.name, s.entity.domain, s.entity.kind), ("BlueCore Energy", "bluecore.energy", "company"))
        self.assertEqual(s.entity.one_liner,
                         "A compact 10 MWe modular reactor on a barge — zero-emission power that goes where it's needed.")
        self.assertIsNone(s.entity.github)                 # PX4 is a library the page links to, not the company
        self.assertEqual(s.entity.links, {"x": "https://x.com/bluecore"})
        m = s.metrics
        self.assertIs(m["observed_only"], True)
        self.assertEqual((m["final_url"], m["domain_source"]), ("https://www.bluecore.energy/", "known"))
        self.assertEqual((m["ats_provider"], m["ats_slug"]), ("lever", "bluecore-energy"))
        self.assertNotIn("confidence", m)
        self.assertTrue(s.text.startswith("Bluecore Energy | Floating Nuclear Power"))
        self.assertIn("Built at the Port of Long Beach", s.text)

    def test_known_domain_is_kept_when_the_site_redirects(self):
        e = known("TETAC", "tetacinc.com", sector="defense")
        page = html("Energetic Payload Systems | TETAC | USA", "TETAC develops energetic payload systems for unmanned "
                    "vehicles.", "<p>Payloads.</p>")
        v = self.page(e, page, "https://www.tetac.com/")
        self.assertTrue(v.accepted)
        self.assertIn("redirect_followed", v.checks)
        s = wp.to_description_signal(e, v, TODAY.isoformat())
        self.assertEqual(s.entity.domain, "tetacinc.com")
        self.assertEqual(s.title, "Website on file at tetacinc.com")
        self.assertEqual(s.metrics["final_url"], "https://www.tetac.com/")

    def test_no_usable_description_leaves_the_one_liner_empty(self):
        e = known("Orkora", "orkora.com")
        v = self.page(e, html("Orkora", "Home", "<h1>Orkora</h1><div>Sites Refs Timeline GDP Subs Cables</div>"))
        self.assertTrue(v.accepted)
        self.assertIsNone(wp.to_description_signal(e, v, TODAY.isoformat()).entity.one_liner)

    def test_fetch_failures_are_misses(self):
        e = known("Nosh Robotics", "www.NoshRobotics.co", sector="robotics")
        with mock.patch.object(wp, "fetch_homepage", side_effect=http.HttpError(403, "https://noshrobotics.co/")) as f:
            v = wp.describe(e)
        f.assert_called_once_with("noshrobotics.co")
        self.assertEqual((v.accepted, v.refused, v.reason), (False, True, "homepage answered HTTP 403"))
        with mock.patch.object(wp, "fetch_homepage", side_effect=TimeoutError("timed out")):
            self.assertEqual(wp.describe(e).reason, "homepage did not load (TimeoutError)")
        with mock.patch.object(wp, "fetch_homepage", return_value=("https://noshrobotics.co/", BLUECORE)):
            self.assertIn("name not on the page", wp.describe(e).reason)


class DescribeEndToEnd(unittest.TestCase):
    PAGES = {
        "bluecore.energy": ("https://www.bluecore.energy/", BLUECORE),
        "radiantnuclear.com": ("https://www.radiantnuclear.com/", RADIANT),
        "haledorr.com": ("https://www.haledorr.com/", LAW_FIRM),
        "orkora.com": ("https://orkora.com/", ORKORA),
    }

    def run_collect(self, known_list, **kw):
        asked: list[str] = []

        def fake_fetch(host):
            if host == "refused.com":
                raise http.HttpError(403, f"https://{host}/")
            return self.PAGES[host]

        def fake_exists(host):
            asked.append(host)
            return host in self.PAGES

        c = ctx(known=known_list, **kw)
        with mock.patch.object(wp, "host_exists", fake_exists), mock.patch.object(wp, "fetch_homepage", fake_fetch), \
                mock.patch.object(wp, "suggest", lambda name: []), \
                mock.patch.object(wp, "_install_final_url_hook", lambda: None), \
                mock.patch.object(http, "request", side_effect=AssertionError("network in a test")):
            signals = list(wp.collect(c))
        return signals, c, asked

    def test_both_modes_in_one_run(self):
        k = [
            known("BlueCore Energy", "bluecore.energy"),
            entity("Orkora", "energy", "Trademark goods: Nuclear reactors"),             # no domain: verified
            known("Radiant Industries", "radiantnuclear.com", "Trademark goods: Nuclear reactors"),
            known("Acme Wireless", "haledorr.com"),                                       # the lawyer's domain
            known("Refused", "refused.com"),
            known("Skild AI", "skild.ai", "General purpose robotic intelligence"),        # has both: skipped
            dict(known("kicad-mcp", "kicad-mcp.dev"), kind="project"),
        ]
        signals, c, asked = self.run_collect(k)
        self.assertEqual([(s.kind, s.entity.name, s.entity.domain) for s in signals], [
            ("website_verified", "Orkora", "orkora.com"),
            ("website_described", "BlueCore Energy", "bluecore.energy"),
            ("website_described", "Radiant Industries", "radiantnuclear.com"),
        ])
        for s in signals:
            s.validate()
        self.assertTrue(all(h.startswith("orkora") for h in asked))      # describing costs no DNS lookups
        log = "\n".join(c.logged)
        self.assertIn("+ BlueCore Energy described from bluecore.energy (with a one-liner)", log)
        self.assertIn("- Acme Wireless ~ haledorr.com: not described: name not on the page as a name", log)
        self.assertIn("- Refused ~ refused.com: not described: homepage answered HTTP 403", log)
        self.assertNotIn("Skild", log)
        self.assertNotIn("kicad", log)
        self.assertIn("1 verified of 1 companies without a domain", log)
        self.assertIn("2 described of 4 companies with a domain and no description", log)
        self.assertEqual(c.warnings, [])

    def test_limit_applies_to_each_mode(self):
        k = [known("BlueCore Energy", "bluecore.energy"), known("Radiant Industries", "radiantnuclear.com"),
             entity("Orkora", "energy"), entity("Exowatt", "energy")]
        signals, c, asked = self.run_collect(k, limit=1)
        self.assertEqual([s.entity.name for s in signals], ["Orkora", "BlueCore Energy"])
        self.assertFalse([h for h in asked if h.startswith("exowatt")])

    def test_a_crash_while_describing_is_a_warning(self):
        k = [known("BlueCore Energy", "bluecore.energy"), known("Radiant Industries", "radiantnuclear.com")]
        real = wp.describe

        def flaky(e):
            if e["name"] == "BlueCore Energy":
                raise ValueError("bad page")
            return real(e)

        with mock.patch.object(wp, "describe", flaky):
            signals, c, _ = self.run_collect(k)
        self.assertEqual([s.entity.name for s in signals], ["Radiant Industries"])
        self.assertEqual(len(c.warnings), 1)
        self.assertIn("BlueCore Energy: ValueError: bad page", c.warnings[0])


if __name__ == "__main__":
    unittest.main()
