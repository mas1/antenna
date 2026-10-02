"""Offline tests for the accelerators collector: parsing against saved fixtures.

No network. The end-to-end tests swap antenna.http for a stub that serves the
fixtures and fails the test if any other URL is asked for.

The fixtures hold a speedrun list response and ten hax.co company records.
What they lack is inlined below, copied from live responses on 2026-10-01:
three speedrun detail records (image fields dropped), the speedrun newsletter's
kickoff posts (title, subtitle, date, URL only), SOSV taxonomy names, the
sosv.com twin of one hax.co record, and one sosv.com record whose page is a
tagline and nothing else. Two more sosv.com records were copied on 2026-10-02:
a HAX page that passes on SOSV's categories and an IndieBio page that names
PCBs, the pollutant.
"""

from __future__ import annotations

import copy
import json
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

from antenna import http
from antenna.collectors import accelerators as acc
from antenna.collectors.base import Context
from antenna.thesis import classify

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "launches"
TODAY = date(2026, 10, 1)

SPEEDRUN_LIST = json.loads((FIX / "a16z_speedrun_companies_robotics.json").read_text())["response"]
ROWS = {r["name"]: r for r in SPEEDRUN_LIST["results"]}
HAX = json.loads((FIX / "hax_wp_company_founder.json").read_text())

# GET https://speedrun.substack.com/api/v1/archive?sort=new&search=kickoff&limit=50&offset=0
POSTS = [
    {"title": "Scenes from SR007 Kickoff Week",
     "subtitle": "Founders from 80+ startups convened last week for the official kickoff of SR007",
     "post_date": "2026-08-05T16:00:07.711Z",
     "canonical_url": "https://speedrun.substack.com/p/scenes-from-sr007-kickoff-week"},
    {"title": "Scenes from SR006 Kickoff Week",
     "subtitle": "Founders from 70+ startups convened in San Francisco last week for the official kickoff of SR006. "
                 "Here's an inside look at what speedrun looks like from the inside. ",
     "post_date": "2026-02-05T14:47:51.746Z",
     "canonical_url": "https://speedrun.substack.com/p/scenes-from-sr006-kickoff-week"},
    {"title": "Scenes from SR Kickoff Week",
     "subtitle": "Founders from 60+ startups convened in Los Angeles last week for the official kickoff of SR005. "
                 "Here's an inside look at our startup program.",
     "post_date": "2025-08-06T14:16:08.349Z",
     "canonical_url": "https://speedrun.substack.com/p/scenes-from-sr-kickoff-week"},
    {"title": "Takeaways from New York Tech Week",
     "subtitle": "The things we saw and heard when 50,000 people descended on NYC for Tech Week",
     "post_date": "2026-06-09T14:00:00.000Z",
     "canonical_url": "https://speedrun.substack.com/p/takeaways-from-new-york-tech-week"},
]
KICKOFF_SR007 = {"at": datetime(2026, 8, 5, 16, 0, 7, 711000, tzinfo=timezone.utc),
                 "url": "https://speedrun.substack.com/p/scenes-from-sr007-kickoff-week",
                 "title": "Scenes from SR007 Kickoff Week"}

# GET https://speedrun-api.a16z.com/api/companies/companies/{id}/
DETAILS = {d["id"]: d for d in [
    {
        "id": "7ecf20af-0f3e-41d6-be53-01d375645e92", "slug": "cybernetic-physics", "name": "Cybernetic Physics",
        "cohort": "SR007", "preamble": "Autonomous robots to operate data centers.",
        "description": "Cybernetic Physics deploys autonomous robots inside data centers. We deploy third-party "
                       "humanoids and mobile manipulators to handle rack and stack, cabling, and maintenance 24/7. \n\n"
                       "Since launching 8 weeks ago, we've scaled to $828k live ARR and won a contract from DeepAI to "
                       "deploy more than 10 robots to build and operate a lights-out data center in San Francisco.  \n\n"
                       "Co-founder and CEO Natalia was previously at Stanford's Scaling Intelligence Lab and met her "
                       "co-founder and CTO Luc while working on confidential compute at Lucid Computing. The team "
                       "includes Stanford roboticists and ML researchers published at NeurIPS and ICML, experienced "
                       "hardware engineers, and technicians who have worked inside Microsoft's and Apple's data "
                       "centers for decades.",
        "industries": ["Robotics", "Deep Tech", "AI Infra"], "founded_year": 2026, "team_size": 4,
        "country": "United States of America", "region": "America/Canada", "state": "California", "city": "Stanford",
        "website_url": "https://cyberneticphysics.com", "github_url": "https://github.com/cybernetic-physics",
        "x_url": "https://x.com/cyberneticphysx",
        "linkedin_url": "https://www.linkedin.com/company/cybernetic-physics/", "demo_day_video_url": "",
        "founder_set": [
            {"id": "985d75a0-5979-4192-8b6f-6afb1d8a5a69", "first_name": "Natalia", "last_name": "Kokoromyti",
             "slug": "natalia-kokoromyti", "title": "CEO", "introduction": "",
             "linkedin_url": "https://www.linkedin.com/in/natalia-kokoromyti-650893257/"},
            {"id": "17c5701b-47c2-4d2a-9ca8-f967a2f8c321", "first_name": "Luc", "last_name": "Chartier",
             "slug": "luc-chartier", "title": "CTO", "introduction": "", "linkedin_url": ""},
        ],
    },
    {
        "id": "fef1f4c3-11b7-443d-b6db-1902fda9aa37", "slug": "munari", "name": "Munari", "cohort": "SR007",
        "preamble": "Improving data collection for robotics.",
        "description": "Munari is quality scoring for robotics data. Our platform processes, annotates, and scores "
                       "every episode and evaluates operator performance. We then turn those evaluations into "
                       "actionable feedback, helping teams improve data quality and get more value from every hour of "
                       "collection.\n\nBoth cofounders were previously at Hedra (a16z-backed, Series A video model "
                       "startup). Ramin was also the CTO at StockX (scaling from $0 to $1B+ GMV run rate) and a 3x "
                       "founder.",
        "industries": ["Robotics", "AI Infra"], "founded_year": 2026, "team_size": 3,
        "country": "United States of America", "region": "America/Canada", "state": "California",
        "city": "San Francisco", "website_url": "https://www.munari.ai",
        "github_url": "https://github.com/munari-labs", "x_url": "https://x.com/MunariAI",
        "linkedin_url": "https://www.linkedin.com/company/munari-ai", "demo_day_video_url": "",
        "founder_set": [
            {"id": "9382fe0f-71c3-47ce-8d60-bdcf61b74088", "first_name": "Alan", "last_name": "Guo",
             "slug": "alan-guo", "title": "Co-Founder",
             "introduction": "Previously the Head of Business & Chief of Staff at Hedra where he helped scale the "
                             "company to 8-figure ARR within 6 months. He led GTM, operations, hiring, and strategy. "
                             "Harvard Business School MBA.",
             "linkedin_url": "https://www.linkedin.com/in/alanyguo/"},
            {"id": "4773ea38-58c5-4b73-88e3-32679eccfaed", "first_name": "Ramin", "last_name": "Keene",
             "slug": "ramin-keene", "title": "Cofounder", "introduction": "",
             "linkedin_url": "https://www.linkedin.com/in/raminkeene/"},
        ],
    },
    {
        "id": "a16b67aa-3f27-474e-8fe1-777fa620c001", "slug": "superintelligence-foundry",
        "name": "Superintelligence Foundry", "cohort": "SR007", "preamble": "Moonshots for Every Company.",
        "description": "We are building a creative AGI: a scientist, engineer, and entrepreneur that learns by acting "
                       "in the world, keeps improving, and is intrinsically motivated to make the world better.\n\n"
                       "Our breakthroughs include intrinsic alignment that scales, open-ended self-improvement, and "
                       "connecting AGI into the physical world.\n\nOur business model is to turn capital into "
                       "invention, discovery, and the creation of autonomous companies.",
        "industries": ["Robotics", "Deep Tech", "AI Models / Research"], "founded_year": 2026, "team_size": 2,
        "country": "United States of America", "region": "America/Canada", "state": "California",
        "city": "San Francisco", "website_url": "https://superfoundry.dev/", "github_url": "",
        "x_url": "https://x.com/superfoundry",
        "linkedin_url": "https://www.linkedin.com/company/superintelligence-foundry/", "demo_day_video_url": "",
        "founder_set": [
            {"id": "60479ee9-b3fb-4750-9230-c6492579fa9d", "first_name": "Nima", "last_name": "Asgharbeygi",
             "slug": "nima-asgharbeygi", "title": "CTO", "introduction": "",
             "linkedin_url": "https://www.linkedin.com/in/nimaa/"},
        ],
    },
]}
CYBERNETIC = DETAILS["7ecf20af-0f3e-41d6-be53-01d375645e92"]
MUNARI = DETAILS["fef1f4c3-11b7-443d-b6db-1902fda9aa37"]
FOUNDRY = DETAILS["a16b67aa-3f27-474e-8fe1-777fa620c001"]

# Display names as sosv.com's taxonomy endpoints return them (HTML-escaped).
TERMS = {
    "tx_cohort": {"hax-seed-2024": "HAX Seed 2024", "hax-seed-2025": "HAX Seed 2025",
                  "hax-seed-2026": "HAX Seed 2026"},
    "tx_category": {"manufacturing-energy": "Manufacturing &amp; Energy", "robotics": "Robotics",
                    "industrial-equipment": "Industrial Equipment", "healthcare": "Healthcare",
                    "climate-tech": "Climate Tech", "industrial-research-tools": "Industrial &amp; Research Tools",
                    "industrial-bio": "Industrial Bio", "sustainable-manufacturing": "Sustainable Manufacturing"},
    "tx_trend": {"critical-minerals": "Critical Minerals", "materials": "Materials", "physical-ai": "Physical AI",
                 "climate-tech": "Climate Tech", "energy": "Energy", "female-founders": "Female Founders"},
    "tx_program": {"sosv-hax": "SOSV HAX"},
    "tx_stage": {"pre-seed": "Pre-Seed"},
    "tx_location": {"houston-texas": "Houston, Texas", "united-states": "United States"},
}
NAMES = {tax: {slug: name.replace("&amp;", "&") for slug, name in terms.items()} for tax, terms in TERMS.items()}

# The sosv.com record of Zetta Joule: same portal_id as the hax.co one, four seconds older.
ZETTA_SOSV = {
    "id": 22484, "date_gmt": "2026-09-21T20:46:09", "modified_gmt": "2026-09-23T22:02:31", "slug": "zetta-joule",
    "link": "https://sosv.com/company/zetta-joule/", "title": {"rendered": "Zetta Joule"}, "tx_company": [2796],
    "class_list": ["post-22484", "company", "type-company", "status-publish", "hentry", "tx_company-zetta-joule",
                   "tx_cohort-hax-seed-2024", "tx_location-houston-texas", "tx_location-united-states",
                   "tx_program-sosv-hax"],
    "acf": {"now_raising": False, "tagline": "ZettaJoule delivers modernized HTGR through Energy-as-a-Service Solution",
            "founded_year": "2023", "employee_count_range": "", "total_capital_raised": "",
            "website": "http://zetta-joule.com", "linked_in": "https://www.linkedin.com/company/zettajoule",
            "twitter": "", "crunchbase": "", "portal_id": 10416},
    "_site": "sosv.com",
}


# GET https://sosv.com/wp-json/wp/v2/company?search=TopologiQ: a HAX Seed 2026 page with a tagline,
# an empty body and no categories. Its sosv.com cohort and program labels are in TOPOLOGIQ_NAMES.
TOPOLOGIQ = {
    "id": 22400, "date_gmt": "2026-08-31T13:49:05", "modified_gmt": "2026-08-31T15:03:01", "slug": "topologiq",
    "link": "https://sosv.com/company/topologiq/", "title": {"rendered": "TopologiQ"},
    "content": {"rendered": "", "protected": False}, "tx_company": [2794],
    "class_list": ["post-22400", "company", "type-company", "status-publish", "hentry", "tx_company-topologiq",
                   "tx_cohort-hax-seed-2026", "tx_region-north-america", "tx_location-canada", "tx_program-sosv-hax"],
    "acf": {"now_raising": False, "tagline": "Building microprocessors/supercomputers for real-time edge compute.",
            "founded_year": "", "employee_count_range": "", "total_capital_raised": "",
            "website": "https://www.topologiq.ai/", "linked_in": "", "twitter": "", "crunchbase": "",
            "portal_id": 10571},
    "_site": "sosv.com",
}

# sosv.com company 21089 with its page body (empty), 2026-10-02: a HAX page whose own words are one
# tagline with one thesis term that could mean anything.
NOCENERGY = {
    "id": 21089, "date_gmt": "2026-02-26T20:42:31", "modified_gmt": "2026-09-23T21:28:12", "slug": "nocenergy",
    "link": "https://sosv.com/company/nocenergy/", "title": {"rendered": "NOCEnergy"},
    "content": {"rendered": "", "protected": False}, "tx_company": [2599],
    "class_list": ["post-21089", "company", "type-company", "status-publish", "hentry", "tx_company-nocenergy",
                   "tx_cohort-hax-seed-2024", "tx_category-climate-tech", "tx_category-manufacturing-energy",
                   "tx_category-robotics", "tx_trend-climate-tech", "tx_trend-energy", "tx_stage-pre-seed",
                   "tx_location-france", "tx_location-united-states", "tx_program-sosv-hax"],
    "acf": {"now_raising": False, "tagline": "Electrifying High Temperature Industrial Heat",
            "founded_year": "2023", "employee_count_range": "", "total_capital_raised": "",
            "website": "https://www.nocenergy.com/", "linked_in": "https://www.linkedin.com/company/nocenergy",
            "twitter": "", "crunchbase": "", "portal_id": 10412},
    "_site": "sosv.com",
}

# sosv.com company 22185 with its page body, 2026-10-02: an IndieBio company from a legacy numbered
# cohort whose page is dated inside the lookback.
ALLIED_MICROBIOTA = {
    "id": 22185, "date_gmt": "2026-06-29T13:25:07", "modified_gmt": "2026-07-30T19:47:52",
    "slug": "allied-microbiota", "link": "https://sosv.com/company/allied-microbiota/",
    "title": {"rendered": "Allied Microbiota"},
    "content": {"rendered": "<p>Allied Microbiota develops microbes to degrade environmental contaminants. Our "
                            "technology can tackle the most recalcitrant organic pollutants such as "
                            "polychlorobiphenyls (PCBs), polyaromatic hydrocarbons (PAHs), and dioxins. Field tests "
                            "with tons of soil at a commercial facility demonstrated that our launch product, "
                            "ThermO+, can quickly and cost effectively breakdown PAHs and degrade the air "
                            "contaminant formaldehyde.</p>\n", "protected": False},
    "tx_company": [1730],
    "class_list": ["post-22185", "company", "type-company", "status-publish", "hentry",
                   "tx_company-allied-microbiota", "tx_cohort-indiebio-ny-01", "tx_category-climate-tech",
                   "tx_category-industrial-research-tools", "tx_category-industrial-bio",
                   "tx_category-sustainable-manufacturing", "tx_trend-female-founders", "tx_region-north-america",
                   "tx_location-united-states", "tx_program-sosv-ny"],
    "acf": {"now_raising": False, "tagline": "We use microbes to clean soil, turning brownfield into greenfield",
            "founded_year": "", "employee_count_range": "", "total_capital_raised": 0,
            "website": "http://alliedmicrobiota.com", "linked_in": "https://www.linkedin.com/company/allied-microbiota",
            "twitter": "", "crunchbase": "", "portal_id": 9125},
    "_site": "sosv.com",
}


def hax_records() -> list[dict]:
    rows = copy.deepcopy(HAX["company"])
    for r in rows:
        r["_site"] = "hax.co"
    return rows


def hax_view(slug: str, **acf_over) -> dict:
    record = next(r for r in hax_records() if r["slug"] == slug)
    record["acf"].update(acf_over)
    return acc.company_view([record])


def detail(**over) -> dict:
    """A speedrun detail record with every field the collector reads, for edge cases."""
    base = {"id": "00000000-0000-0000-0000-000000000001", "slug": "testco", "name": "Testco", "cohort": "SR007",
            "preamble": "", "description": "", "industries": [], "founded_year": 2026, "team_size": 3,
            "city": "", "state": "", "country": "", "website_url": "", "github_url": "", "x_url": "",
            "linkedin_url": "", "founder_set": []}
    base.update(over)
    return base


class Helpers(unittest.TestCase):
    def test_year_of(self):
        self.assertEqual(acc.year_of("2023"), 2023)
        self.assertEqual(acc.year_of("2020.0"), 2020)  # seen on sosv.com
        self.assertEqual(acc.year_of(2026), 2026)
        for bad in ("", None, "n/a", True, "20", 0):
            self.assertIsNone(acc.year_of(bad))

    def test_github_login(self):
        self.assertEqual(acc.github_login("https://github.com/cybernetic-physics"), "cybernetic-physics")
        self.assertEqual(acc.github_login("https://github.com/organizations/Oruk-AI"), "Oruk-AI")
        self.assertEqual(acc.github_login("https://github.com/acme/repo"), "acme")
        for junk in ("https://www.instagram.com/tryblueprint.io/", "", None, "github.com"):
            self.assertIsNone(acc.github_login(junk))

    def test_parse_stamp(self):
        self.assertEqual(acc.iso(acc.parse_stamp("2026-09-21T20:46:13")), "2026-09-21T20:46:13Z")  # WordPress *_gmt
        self.assertEqual(acc.iso(acc.parse_stamp("2026-08-05T16:00:07.711Z")), "2026-08-05T16:00:07Z")
        for bad in (None, "", "yesterday"):
            self.assertIsNone(acc.parse_stamp(bad))

    def test_strip_html(self):
        self.assertEqual(acc.strip_html("<p>Our aim: <b>fission</b> &amp; heat.</p>\n<p>Next</p>"),
                         "Our aim: fission & heat. Next")
        self.assertEqual(acc.strip_html(None), "")

    def test_profile_url_drops_links_that_are_not_a_profile(self):
        self.assertEqual(acc.profile_url("https://x.com/cyberneticphysx", company=True), "https://x.com/cyberneticphysx")
        self.assertIsNone(acc.profile_url("https://x.com/", company=True))          # Acaysia's x_url, live 2026-10-01
        self.assertIsNone(acc.profile_url("", company=True))
        self.assertIsNone(acc.profile_url("n/a"))
        # Galileo Space's company linkedin_url is its CEO's personal profile.
        personal = "https://www.linkedin.com/in/kazi-farabi-40962313a/?isSelfProfile=true"
        self.assertIsNone(acc.profile_url(personal, company=True))
        self.assertEqual(acc.profile_url(personal), personal)                       # fine as a person's link
        self.assertEqual(acc.profile_url("https://www.linkedin.com/company/kiraeco/", company=True),
                         "https://www.linkedin.com/company/kiraeco/")

    def test_company_domain(self):
        self.assertEqual(acc.company_domain("https://www.photonspear.space"), "photonspear.space")
        for not_own in ("https://sosv.com/company/x/", "https://hax.co/company/x/", "https://speedrun.a16z.com/companies/x",
                        "https://www.crunchbase.com/organization/x", "https://www.linkedin.com/company/x",
                        "https://play.google.com/store/apps/details?id=com.woovly.bucketlist",
                        "https://indiebio.co/companies/x", "http://angel.co", "http://hackster.io", "", None, False):
            self.assertIsNone(acc.company_domain(not_own), not_own)

    def test_distinct_terms_counts_subjects_not_spellings(self):
        # Lists as the classifier returns them. Variants and nested phrases are one subject.
        self.assertEqual(acc.distinct_terms(["manufacturer", "manufacturing"]), 1)
        self.assertEqual(acc.distinct_terms(["additive manufacturing", "manufacturer", "manufacturing"]), 1)
        self.assertEqual(acc.distinct_terms(["robot", "robotic"]), 1)
        self.assertEqual(acc.distinct_terms(["defence", "defense"]), 1)
        self.assertEqual(acc.distinct_terms(["autonomous", "autonomy"]), 1)
        self.assertEqual(acc.distinct_terms(["defense", "missile", "missile defense"]), 1)   # one phrase
        self.assertEqual(acc.distinct_terms(["energy", "energy storage"]), 1)
        self.assertEqual(acc.distinct_terms(["defense"]), 1)
        self.assertEqual(acc.distinct_terms([]), 0)
        # Different subjects stay apart, also when they start alike.
        self.assertEqual(acc.distinct_terms(["autonomous", "robot"]), 2)
        self.assertEqual(acc.distinct_terms(["microgrid", "microprocessor", "microreactor"]), 3)
        self.assertEqual(acc.distinct_terms(["fission", "fusion"]), 2)
        self.assertEqual(acc.distinct_terms(["critical mineral", "energy", "industrial"]), 3)
        self.assertEqual(acc.distinct_terms(["photonic", "photovoltaic"]), 2)

    def test_split_name(self):
        self.assertEqual(acc.split_name("Zetta Joule"), ("Zetta Joule", []))
        self.assertEqual(acc.split_name("Novoloop (fka BioCellection)"), ("Novoloop", ["BioCellection"]))
        self.assertEqual(acc.split_name("Ao Air (f/k/a O2 O2)"), ("Ao Air", ["O2 O2"]))
        self.assertEqual(acc.split_name("Mapflow (Acq’d by Lexis Nexis)"), ("Mapflow", []))  # a note, not a name
        self.assertEqual(acc.split_name("(stealth)"), ("(stealth)", []))

    def test_own_spellings(self):
        tagline = "ZettaJoule delivers modernized HTGR through Energy-as-a-Service Solution"
        self.assertEqual(acc.own_spellings("Zetta Joule", tagline + " ZettaJoule. Zetta Joule"), ["ZettaJoule"])
        self.assertEqual(acc.own_spellings("RB-Ware", "No-code robotic solutions for metalworking"), [])
        self.assertEqual(acc.own_spellings("X Co", "XCo builds"), [])  # too short to be safe

    def test_too_old(self):
        self.assertTrue(acc.too_old(2022, TODAY))
        self.assertFalse(acc.too_old(2023, TODAY))
        self.assertFalse(acc.too_old(None, TODAY))  # no founding year stated: not skipped on that ground


class Kickoffs(unittest.TestCase):
    def test_cohort_from_title_or_subtitle(self):
        k = acc.kickoff_posts(POSTS)
        self.assertEqual(sorted(k), ["SR005", "SR006", "SR007"])  # SR005 is named only in the subtitle
        self.assertEqual(acc.iso(k["SR007"]["at"]), "2026-08-05T16:00:07Z")
        self.assertEqual(k["SR007"]["url"], "https://speedrun.substack.com/p/scenes-from-sr007-kickoff-week")
        self.assertEqual(acc.iso(k["SR005"]["at"]), "2025-08-06T14:16:08Z")

    def test_posts_that_do_not_date_a_cohort(self):
        base = {"post_date": "2026-08-05T16:00:07.711Z", "canonical_url": "https://speedrun.substack.com/p/x"}
        self.assertEqual(acc.kickoff_posts([
            {**base, "title": "Takeaways from New York Tech Week", "subtitle": "kickoff of SR007"},  # not a kickoff post
            {**base, "title": "From SR006 Kickoff to SR007 Kickoff", "subtitle": ""},                # two cohorts
            {**base, "title": "Scenes from Kickoff Week", "subtitle": "no cohort named"},
            # A cohort code that is not beside "kickoff" does not date that cohort.
            {**base, "title": "SR008 applications are open: kickoff in January", "subtitle": ""},
            {**base, "title": "What SR008 founders can expect", "subtitle": "after the kickoff of SR007"},
            {"title": "Scenes from SR008 Kickoff Week", "post_date": None, "canonical_url": base["canonical_url"]},
            {"title": "Scenes from SR008 Kickoff Week", "post_date": base["post_date"], "canonical_url": ""},
        ]), {})

    def test_earliest_post_wins(self):
        later = {**POSTS[0], "title": "More from SR007 Kickoff Week", "post_date": "2026-08-12T16:00:00.000Z",
                 "canonical_url": "https://speedrun.substack.com/p/more"}
        k = acc.kickoff_posts([later, POSTS[0]])
        self.assertEqual(k["SR007"]["url"], POSTS[0]["canonical_url"])


class SpeedrunThesis(unittest.TestCase):
    def test_worth_detail_from_list_rows(self):
        self.assertTrue(acc.speedrun_worth_detail(ROWS["Cybernetic Physics"]))
        self.assertTrue(acc.speedrun_worth_detail(ROWS["Exia Labs"]))
        # "DeepMind for Finance", labelled Robotics and Fintech: never fetched.
        self.assertFalse(acc.speedrun_worth_detail(ROWS["Axon"]))
        # No thesis label: only a one-liner with an unmistakable thesis term, or two terms, earns a detail call.
        self.assertFalse(acc.speedrun_worth_detail({"industries": ["AI Agents"], "preamble": "The AI notetaker"}))
        self.assertTrue(acc.speedrun_worth_detail({"industries": ["AI Agents"], "preamble": "Autopilot for drones"}))
        self.assertTrue(acc.speedrun_worth_detail({"industries": ["AI Agents"], "preamble": "Data for drones"}))
        # LFG (SR003), live 2026-10-01: one thesis word that could mean anything is not enough.
        games = {"industries": ["Gaming", "Media / Entertainment / Creator Economy"],
                 "preamble": "The Hit Factory Building the Biggest Games for Gen Alpha"}
        self.assertFalse(acc.speedrun_worth_detail(games))
        self.assertFalse(acc.speedrun_verdict(detail(**games))["on"])

    def test_label_backed_by_own_words(self):
        v = acc.speedrun_verdict(CYBERNETIC)
        self.assertEqual(v["basis"], "industry+text")
        self.assertEqual(v["labels"], ["Robotics"])
        self.assertTrue(v["hard"])
        self.assertGreaterEqual(v["own_fit"], 0.6)

    def test_label_without_own_words_is_not_thesis(self):
        # Labelled Robotics and Deep Tech; the text is about AGI and matches no robotics term.
        v = acc.speedrun_verdict(FOUNDRY)
        self.assertFalse(v["on"])
        self.assertGreaterEqual(v["fit"], acc.MIN_FIT)  # the labels alone would have passed the classifier
        self.assertLess(v["own_fit"], acc.MIN_FIT)

    def test_low_own_fit_passes_when_it_agrees_with_the_label(self):
        # KIRA (SR007), live 2026-10-01: one context word in the one-liner, in the sector speedrun filed it under.
        v = acc.speedrun_verdict(detail(industries=["Manufacturing / Industrials", "Hardware", "Infra"],
                                        preamble="We transform industrial wastewater into clean water for data "
                                                 "centers: we enable critical infrastructure."))
        self.assertEqual(v["basis"], "industry+text")
        self.assertLess(v["own_fit"], acc.MIN_FIT)
        self.assertGreaterEqual(v["fit"], acc.MIN_FIT)   # the label line in the text is the second piece of evidence
        # The same word under a label for a different sector does not agree with anything.
        other = acc.speedrun_verdict(detail(industries=["Robotics"],
                                            preamble="We transform industrial wastewater into clean water."))
        self.assertFalse(other["on"])
        # Acaysia (SR007): "industrial" and "reactors" are two context words, enough on their own.
        v = acc.speedrun_verdict(detail(industries=["Manufacturing / Industrials", "Hardware", "Deep Tech"],
                                        preamble="Real-time AI control layer to optimize industrial reactors."))
        self.assertEqual(v["basis"], "industry+text")
        self.assertGreaterEqual(v["own_fit"], acc.MIN_FIT)

    def test_sales_tooling_for_a_thesis_sector_is_off(self):
        # Emanate (SR006), live 2026-10-01.
        row = {"industries": ["Manufacturing / Industrials", "Sales / GTM", "AI Agents"],
               "preamble": "The First AI Revenue Engine Built for the Physical Economy."}
        self.assertFalse(acc.speedrun_worth_detail(row))
        self.assertFalse(acc.speedrun_verdict(detail(
            **row, description="We're building the AI Revenue Engine for industrial materials companies: "
                               "autonomous AI agents that run and grow revenue operations end-to-end."))["on"])

    def test_finance_for_a_thesis_sector_is_off(self):
        v = acc.speedrun_verdict(detail(industries=["Fintech", "Deep Tech", "Gov Tech / Defense"],
                                        preamble="Banking for Europe's frontier industries across defence, "
                                                 "security, and resilience."))
        self.assertFalse(v["on"])

    def test_unlabelled_record_needs_a_strong_one_liner(self):
        speech = detail(industries=["AI Models / Research", "AI Voice"],
                        preamble="Speech foundation models that understand people",
                        description="As robots enter the workforce, every robot command needs the full context.")
        self.assertFalse(acc.speedrun_verdict(speech)["on"])  # robots only in the long description
        drones = detail(industries=["AI Agents"], preamble="Autopilot software for cargo drones")
        self.assertEqual(acc.speedrun_verdict(drones)["basis"], "one_liner")

    def test_one_thesis_word_deep_in_the_description_is_not_thesis(self):
        # Abliteration.ai, live 2026-10-01: an LLM API labelled Gov Tech / Defense whose only thesis
        # word is "defense" in a list of customer segments.
        llm = detail(industries=["Gov Tech / Defense", "Dev Tools & DevOps", "AI Models / Research"],
                     preamble="Unrestricted AI provider for high-risk industries.",
                     description="OpenAI- and Anthropic-compatible unrestricted AI for red teams, trust & safety, "
                                 "synthetic data, ML research, and defense/government workflows. Governed by your "
                                 "policy. Crossed $7 million in annual run rate.")
        v = acc.speedrun_verdict(llm)
        self.assertFalse(v["on"])
        self.assertGreaterEqual(v["own_fit"], acc.MIN_FIT)  # the classifier alone would have passed it
        # The same single word in the one-liner is the company saying what it is.
        self.assertTrue(acc.speedrun_verdict(MUNARI)["on"])  # "Improving data collection for robotics."
        said = detail(industries=["Gov Tech / Defense"], preamble="Secure AI for defense", description="")
        self.assertTrue(acc.speedrun_verdict(said)["on"])
        # One phrase is one mention, however many terms the classifier lists for it.
        phrase = detail(industries=["Gov Tech / Defense"], preamble="Unrestricted AI provider.",
                        description="Used by red teams, trust & safety, and missile defense programs.")
        self.assertGreaterEqual(len(classify(phrase["description"])["terms"]), 3)
        self.assertFalse(acc.speedrun_verdict(phrase)["on"])

    def test_strength_bands(self):
        strong = acc.speedrun_strength(acc.speedrun_verdict(CYBERNETIC), 2026, TODAY)
        weak = acc.speedrun_strength(acc.speedrun_verdict(MUNARI), 2026, TODAY)
        self.assertTrue(0.45 <= strong <= 0.6, strong)   # solid
        self.assertTrue(0.25 <= weak <= 0.4, weak)       # one robotics term: near routine
        self.assertGreater(strong, weak)
        # Older founding year gives up the recency bonus.
        self.assertLess(acc.speedrun_strength(acc.speedrun_verdict(CYBERNETIC), 2024, TODAY), strong)
        top = {"basis": "industry+text", "own_fit": 1.0, "labels": ["a", "b", "c"], "hard": True}
        self.assertLessEqual(acc.speedrun_strength(top, 2026, TODAY), 0.72)  # a listing is never "rare"
        one = {"basis": "one_liner", "own_fit": 0.6, "labels": [], "hard": False}
        self.assertLess(acc.speedrun_strength(one, 2026, TODAY), 0.3)


class SpeedrunSignal(unittest.TestCase):
    def make(self, company):
        return acc.speedrun_signal(company, acc.speedrun_verdict(company), KICKOFF_SR007, TODAY)

    def test_cybernetic_physics(self):
        s = self.make(CYBERNETIC)
        s.validate()
        self.assertEqual((s.source, s.family, s.kind), ("accelerators", "launch", "speedrun_cohort"))
        self.assertEqual(s.entity.name, "Cybernetic Physics")
        self.assertEqual(s.entity.domain, "cyberneticphysics.com")
        self.assertEqual(s.entity.github, "cybernetic-physics")
        self.assertEqual(s.entity.one_liner, "Autonomous robots to operate data centers.")
        self.assertEqual(s.entity.location, "Stanford, California, United States of America")
        self.assertEqual(s.entity.founded, "2026")
        self.assertEqual(s.entity.links["speedrun"], "https://speedrun.a16z.com/companies/cybernetic-physics")
        self.assertEqual(s.entity.links["twitter"], "https://x.com/cyberneticphysx")
        self.assertEqual(s.title, "Joined a16z speedrun cohort SR007 under Robotics with a team of 4")
        self.assertEqual(s.url, "https://speedrun.a16z.com/companies/cybernetic-physics")
        self.assertEqual((s.value, s.unit), (4, "people"))
        # The API has no dates: the cohort's kickoff post dates it, to the day, and says so.
        self.assertEqual(s.occurred_at, "2026-08-05")
        self.assertEqual(s.metrics["date_basis"], "cohort_kickoff_post_published")
        self.assertEqual(s.metrics["date_source_url"], KICKOFF_SR007["url"])
        self.assertEqual(s.metrics["speedrun_cohort"], "SR007")
        self.assertEqual(s.metrics["team_size"], 4)
        self.assertEqual(s.metrics["founded_year"], 2026)
        self.assertEqual(s.metrics["founders_listed"], 2)
        self.assertIn("speedrun industries: Robotics, Deep Tech, AI Infra", s.text)
        self.assertNotIn("\n\n", s.entity.description)

    def test_founders(self):
        people = self.make(CYBERNETIC).people
        self.assertEqual([(p.name, p.role) for p in people], [("Natalia Kokoromyti", "CEO"), ("Luc Chartier", "CTO")])
        self.assertEqual(people[0].links, {"linkedin": "https://www.linkedin.com/in/natalia-kokoromyti-650893257/"})
        self.assertEqual(people[1].links, {})   # blank linkedin_url stays blank
        self.assertEqual(people[0].facts, {})   # blank introduction: no bio invented
        alan = self.make(MUNARI).people[0]
        self.assertEqual(alan.name, "Alan Guo")
        self.assertTrue(alan.facts["bio"].startswith("Previously the Head of Business"))
        self.assertIn("Harvard Business School", alan.facts["bio"])

    def test_unknown_team_size_and_junk_links_are_left_out(self):
        c = detail(industries=["Robotics"], preamble="Humanoid robots for warehouses", team_size=0,
                   founded_year=None, github_url="https://www.instagram.com/x/", website_url="",
                   founder_set=[{"first_name": "", "last_name": "", "title": "CEO"}])
        s = self.make(c)
        self.assertIsNone(s.value)
        self.assertIsNone(s.unit)
        self.assertNotIn("team_size", s.metrics)
        self.assertNotIn("founded_year", s.metrics)
        self.assertIsNone(s.entity.github)
        self.assertIsNone(s.entity.domain)
        self.assertIsNone(s.entity.founded)
        self.assertIsNone(s.entity.location)
        self.assertEqual(s.people, [])
        self.assertEqual(s.title, "Joined a16z speedrun cohort SR007 under Robotics")

    def test_company_links_keep_only_company_profiles(self):
        c = detail(industries=["Robotics"], preamble="Humanoid robots for warehouses", x_url="https://x.com/",
                   linkedin_url="https://www.linkedin.com/in/some-founder/?isSelfProfile=true")
        self.assertEqual(self.make(c).entity.links, {"speedrun": "https://speedrun.a16z.com/companies/testco"})

    def test_odd_shapes_do_not_raise(self):
        c = detail(industries=["Robotics", None, {"name": "Hardware"}], preamble="Humanoid robots for warehouses",
                   founder_set=["Jane Roe", None, {"first_name": "Jane", "last_name": "Roe", "title": None}],
                   website_url=None, team_size="4")
        self.assertTrue(acc.speedrun_worth_detail(c))
        s = self.make(c)
        self.assertEqual([p.name for p in s.people], ["Jane Roe"])
        self.assertIsNone(s.value)  # "4" as text is not taken as a count
        self.assertEqual(acc.speedrun_people({"founder_set": "Jane Roe"}), [])

    def test_no_slug_no_signal(self):
        self.assertIsNone(self.make(detail(industries=["Robotics"], preamble="Humanoid robots", slug="")))

    def test_titles_follow_the_rules(self):
        # List rows carry the fields the title needs; run every fixture row through it.
        n = 0
        for row in SPEEDRUN_LIST["results"]:
            v = acc.speedrun_verdict(row)
            t = acc.speedrun_title(row, v, acc.positive_int(row.get("team_size")))
            n += 1
            self.assertLessEqual(len(t), 110, t)
            self.assertFalse(t.endswith("."), t)
            self.assertTrue(t.startswith(f"Joined a16z speedrun cohort {row['cohort']}"), t)
        self.assertEqual(n, 16)
        self.assertEqual(
            acc.speedrun_title(ROWS["URSA Mining"], acc.speedrun_verdict(ROWS["URSA Mining"]), 5),
            "Joined a16z speedrun cohort SR005 under Manufacturing / Industrials and Robotics with a team of 5")

    def test_long_label_pair_falls_back_to_one(self):
        c = detail(industries=["Manufacturing / Industrials", "Gov Tech / Defense"], team_size=1200)
        v = {"labels": c["industries"], "basis": "industry+text"}
        t = acc.speedrun_title(c, v, 1200)
        self.assertEqual(t, "Joined a16z speedrun cohort SR007 under Manufacturing / Industrials with a team of 1200")


class SosvRecords(unittest.TestCase):
    def test_classes_of(self):
        zetta = HAX["company"][0]
        self.assertEqual(acc.classes_of(zetta, "tx_cohort"), ["hax-seed-2024"])
        self.assertEqual(acc.classes_of(zetta, "tx_location"), ["houston-texas", "united-states"])
        self.assertEqual(acc.classes_of(zetta, "tx_category"), [])

    def test_two_sites_one_company(self):
        groups = acc.group_records(hax_records() + [copy.deepcopy(ZETTA_SOSV)])
        self.assertEqual(len(groups), 10)  # ten fixture companies, the sosv.com twin joins Zetta Joule
        zetta = next(g for g in groups if g[0]["slug"] == "zetta-joule")
        self.assertEqual([r["_site"] for r in zetta], ["sosv.com", "hax.co"])  # oldest first
        view = acc.company_view(zetta)
        self.assertEqual(acc.iso(view["first_at"]), "2026-09-21T20:46:09Z")
        self.assertEqual(view["first"]["link"], "https://sosv.com/company/zetta-joule/")
        self.assertEqual(view["sites"], ["hax.co", "sosv.com"])
        self.assertTrue(view["content"].startswith("Our aim: Transform the energy ecosystem"))

    def test_reimported_record_keeps_its_first_date(self):
        # sosv.com held two records for one portal_id (first 2026-03-24, again 2026-04-29).
        old = {"id": 1, "slug": "halltech-bv", "date_gmt": "2026-03-24T15:00:00", "modified_gmt": "2026-06-05T10:00:00",
               "link": "https://sosv.com/company/halltech-bv/", "title": {"rendered": "Halltech B.V."},
               "class_list": ["tx_company-halltech-bv"], "acf": {"portal_id": 10553, "founded_year": "2024"},
               "_site": "sosv.com"}
        new = {**old, "id": 2, "slug": "halltech-bv-2", "date_gmt": "2026-04-29T15:50:17",
               "modified_gmt": "2026-04-29T15:54:04", "link": "https://sosv.com/company/halltech-bv-2/",
               "class_list": ["tx_company-halltech-bv-2"], "acf": {"portal_id": 10553, "founded_year": ""}}
        groups = acc.group_records([new, old])
        self.assertEqual(len(groups), 1)
        view = acc.company_view(groups[0])
        self.assertEqual(acc.iso(view["first_at"]), "2026-03-24T15:00:00Z")
        self.assertEqual(view["founded"], 2024)  # the blank on the newer record does not erase it
        self.assertEqual(view["first"]["link"], "https://sosv.com/company/halltech-bv/")

    def test_sites_that_disagree_on_founding_year_keep_the_earlier(self):
        a = {"id": 1, "slug": "x", "date_gmt": "2024-01-23T00:00:00", "modified_gmt": "2026-07-30T00:00:00",
             "link": "https://sosv.com/company/x/", "title": {"rendered": "X"}, "class_list": ["tx_company-x"],
             "acf": {"founded_year": "2021"}, "_site": "sosv.com"}
        b = {**a, "id": 2, "link": "https://hax.co/company/x/", "acf": {"founded_year": "2023"}, "_site": "hax.co"}
        self.assertEqual(acc.company_view(acc.group_records([a, b])[0])["founded"], 2021)

    def test_view_needs_a_date_a_link_and_a_name(self):
        ok = hax_records()[0]
        self.assertIsNone(acc.company_view([{**ok, "date_gmt": ""}]))
        self.assertIsNone(acc.company_view([{**ok, "link": ""}]))
        self.assertIsNone(acc.company_view([{**ok, "title": {"rendered": " "}}]))

    def test_html_entities_in_names_are_decoded(self):
        r = {**hax_records()[0], "title": {"rendered": "Volt &amp; Sons"}}
        self.assertEqual(acc.company_view([r])["name"], "Volt & Sons")

    def test_name_notes_and_own_spelling(self):
        r = {**hax_records()[0], "title": {"rendered": "Novoloop (fka BioCellection)"}}
        view = acc.company_view([r])
        self.assertEqual((view["name"], view["aliases"]), ("Novoloop", ["BioCellection"]))
        zetta = acc.company_view([hax_records()[0]])
        self.assertEqual((zetta["name"], zetta["aliases"]), ("Zetta Joule", ["ZettaJoule"]))  # from its own tagline

    def test_title_as_a_bare_string_does_not_raise(self):
        r = {**hax_records()[0], "title": "Zetta Joule", "content": "Small nuclear power plants"}
        view = acc.company_view([r])
        self.assertEqual((view["name"], view["content"]), ("Zetta Joule", "Small nuclear power plants"))


class SosvThesis(unittest.TestCase):
    def verdict(self, slug, **acf_over):
        return acc.sosv_verdict(hax_view(slug, **acf_over), NAMES)

    def test_hax_companies_on_thesis(self):
        for slug in ("zetta-joule", "5qxt", "robotic-actuators-company", "halltech-bv", "terabora", "cargo-robotics"):
            v = self.verdict(slug)
            self.assertEqual(v["basis"], "hax+text", slug)
            self.assertTrue(v["hax"], slug)
            self.assertGreaterEqual(v["fit"], acc.MIN_FIT, slug)

    def test_label_without_own_words_is_not_thesis(self):
        # Filed under Robotics and Physical AI; its own words are about affordable housing.
        v = self.verdict("verustruct")
        self.assertFalse(v["on"])
        self.assertEqual(v["own_fit"], 0.0)
        self.assertGreaterEqual(v["fit"], acc.MIN_FIT)

    def test_health_is_off_even_with_a_stray_keyword(self):
        v = self.verdict("sharper-sense")  # a nasal spray; the text says "army" once
        self.assertTrue(v["health"])
        self.assertFalse(v["on"])
        self.assertGreaterEqual(v["own_fit"], acc.MIN_FIT)

    def test_non_hax_program_needs_a_strong_term(self):
        r = hax_records()[0]
        r["class_list"] = ["tx_company-x", "tx_cohort-indiebio-ny-10", "tx_program-sosv-ny"]
        r["acf"]["tagline"] = "Rapidly reprogramming plants"
        r["content"] = {"rendered": "<p>Industrial pollution and energy shortages need plants.</p>"}
        v = acc.sosv_verdict(acc.company_view([r]), NAMES)
        self.assertFalse(v["hax"])
        self.assertFalse(v["on"])  # two context words (0.3 to 0.45) are not enough outside HAX
        r["content"] = {"rendered": "<p>Perovskite solar films for satellites.</p>"}
        self.assertEqual(acc.sosv_verdict(acc.company_view([r]), NAMES)["basis"], "text")
        # One thesis word that could mean anything, alone in the tagline: the classifier holds it under the gate.
        r["acf"]["tagline"] = "Plants as living batteries"
        r["content"] = {"rendered": "<p>Engineered endophytes for crops.</p>"}
        v = acc.sosv_verdict(acc.company_view([r]), NAMES)
        self.assertLess(v["own_fit"], acc.MIN_FIT)
        self.assertFalse(v["on"])
        # Materia Bioworks, live 2026-10-01: its customers and their workflows, in passing, in the body.
        # The classifier lists two terms for that one subject and alone would have passed it.
        r["acf"]["tagline"] = "Empowering Companies to Launch Sustainable Products"
        r["content"] = {"rendered": "<p>Materia Bioworks uses AI to help brands and manufacturers transition away "
                                    "from petroleum plastics. By combining computational chemistry and machine "
                                    "learning, Materia enables the development of sustainable products 3x faster and "
                                    "10x cheaper than traditional R&amp;D. As plastic bans and EPR laws tighten "
                                    "globally, Materia simplifies the transition by providing drop-in sustainable "
                                    "solutions that work with existing manufacturing workflows.</p>"}
        view = acc.company_view([r])
        self.assertEqual(classify(acc.sosv_own_text(view))["terms"], ["manufacturer", "manufacturing"])
        v = acc.sosv_verdict(view, NAMES)
        self.assertGreaterEqual(v["own_fit"], acc.MIN_ONE_LINER_FIT)
        self.assertFalse(v["on"])
        r["acf"]["tagline"] = "Bio-based resins for additive manufacturing"   # said in the tagline: on
        self.assertEqual(acc.sosv_verdict(acc.company_view([r]), NAMES)["basis"], "text")

    def test_hax_page_with_one_thesis_term_and_nothing_else(self):
        # TopologiQ, live 2026-10-01: a tagline, an empty body, no categories. "microprocessor" is a
        # term the classifier takes on its own, so the page passes on its own words.
        view = acc.company_view([copy.deepcopy(TOPOLOGIQ)])
        own = classify(acc.sosv_own_text(view))
        self.assertEqual(own["terms"], ["microprocessor"])
        self.assertGreaterEqual(own["fit"], acc.MIN_FIT)
        v = acc.sosv_verdict(view, NAMES)
        self.assertEqual((v["basis"], v["hax"]), ("hax+text", True))
        self.assertEqual(v["fit"], own["fit"])            # the program note adds nothing to it
        self.assertEqual(v["sector"], "semiconductors")
        text = acc.sosv_text(view, NAMES)
        self.assertEqual(text, "Building microprocessors/supercomputers for real-time edge compute.\n"
                               + acc.HAX_PROGRAM_NOTE)
        self.assertNotIn("SOSV categories", text)          # none on the page, none stated

    def test_hax_page_with_one_ambiguous_term_needs_a_category(self):
        # NOCEnergy: the tagline is all the page says, and "industrial" alone could mean anything.
        # SOSV's categories are the second piece of evidence.
        view = acc.company_view([copy.deepcopy(NOCENERGY)])
        self.assertEqual(classify(acc.sosv_own_text(view))["terms"], ["industrial"])
        v = acc.sosv_verdict(view, NAMES)
        self.assertLess(v["own_fit"], acc.MIN_FIT)
        self.assertGreaterEqual(v["fit"], acc.MIN_FIT)
        self.assertEqual(v["basis"], "hax+text")
        self.assertEqual(acc.sosv_text(view, NAMES),
                         "Electrifying High Temperature Industrial Heat\n"
                         "SOSV categories: Climate Tech, Manufacturing & Energy, Robotics, Energy\n"
                         + acc.HAX_PROGRAM_NOTE)
        # The same tagline with no category: one ambiguous term and the program. Under the gate.
        r = copy.deepcopy(NOCENERGY)
        r["class_list"] = [c for c in r["class_list"] if not c.startswith(("tx_category-", "tx_trend-"))]
        v = acc.sosv_verdict(acc.company_view([r]), NAMES)
        self.assertTrue(v["hax"])
        self.assertGreater(v["own_fit"], 0)
        self.assertLess(v["fit"], acc.MIN_FIT)
        self.assertFalse(v["on"])

    def test_the_hax_program_is_stated_and_is_no_evidence(self):
        # The program note carries no thesis term: it says where the page sits and lifts no score.
        self.assertEqual(classify(acc.HAX_PROGRAM_NOTE)["terms"], [])
        self.assertEqual(classify(acc.HAX_PROGRAM_NOTE)["fit"], 0.0)
        r = copy.deepcopy(TOPOLOGIQ)
        r["acf"]["tagline"] = "Building the engine of the imagination age"
        view = acc.company_view([r])
        self.assertIn(acc.HAX_PROGRAM_NOTE, acc.sosv_text(view, NAMES))
        v = acc.sosv_verdict(view, NAMES)
        self.assertTrue(v["hax"])
        self.assertEqual((v["own_fit"], v["fit"]), (0.0, 0.0))
        self.assertFalse(v["on"])
        # A program outside HAX gets no note. TopologiQ's tagline there passes as it does in HAX: an
        # unmistakable term, said in the tagline.
        r = copy.deepcopy(TOPOLOGIQ)
        r["class_list"] = ["tx_company-topologiq", "tx_cohort-indiebio-ny-2026", "tx_program-sosv-ny"]
        view = acc.company_view([r])
        self.assertNotIn("SOSV program", acc.sosv_text(view, NAMES))
        v = acc.sosv_verdict(view, NAMES)
        self.assertEqual((v["basis"], v["hax"]), ("text", False))

    def test_pollutant_pcbs_in_a_biology_page_are_not_circuit_boards(self):
        # Allied Microbiota: the classifier takes "pcb" on its own as a circuit board. Here it is a
        # pollutant named once in the body, and the tagline says what the company is.
        view = acc.company_view([copy.deepcopy(ALLIED_MICROBIOTA)])
        self.assertEqual(classify(acc.sosv_own_text(view))["terms"], ["pcb"])
        v = acc.sosv_verdict(view, NAMES)
        self.assertFalse(v["hax"])
        self.assertGreaterEqual(v["own_fit"], acc.MIN_ONE_LINER_FIT)   # the classifier alone would have passed it
        self.assertGreaterEqual(v["fit"], acc.MIN_FIT)
        self.assertFalse(v["on"])
        self.assertTrue(acc.stale(view, NAMES, TODAY))                 # and a legacy numbered cohort besides

    def test_label_lines_survive_a_long_page(self):
        r = hax_records()[0]
        r["content"] = {"rendered": "<p>" + "Small nuclear power plants on site. " * 200 + "</p>"}
        text = acc.sosv_text(acc.company_view([r]), NAMES)
        self.assertLessEqual(len(text), acc.TEXT_CHARS)
        self.assertTrue(text.endswith(acc.HAX_PROGRAM_NOTE))
        long = detail(industries=["Robotics"], preamble="Humanoid robots", description="word " * 2000)
        self.assertLessEqual(len(acc.speedrun_text(long)), acc.TEXT_CHARS)
        self.assertTrue(acc.speedrun_text(long).endswith("speedrun industries: Robotics"))

    def test_stale_uses_founding_year_then_cohort_label(self):
        self.assertFalse(acc.stale(hax_view("zetta-joule"), NAMES, TODAY))            # founded 2023
        self.assertTrue(acc.stale(hax_view("vision-iv"), NAMES, TODAY))               # founded 2021
        blank = hax_view("halltech-bv")                                               # no year, HAX Seed 2026
        self.assertIsNone(blank["founded"])
        self.assertFalse(acc.stale(blank, NAMES, TODAY))
        old = hax_view("halltech-bv")
        old["cohorts"] = ["hax-seed-2023"]
        self.assertTrue(acc.stale(old, {"tx_cohort": {"hax-seed-2023": "HAX Seed 2023"}}, TODAY))
        self.assertTrue(acc.stale(old, {}, TODAY))  # names missing: the slug still carries the year
        # No founding year and a numbered legacy cohort: nothing shows it is young, so it is not passed as new.
        legacy = hax_view("halltech-bv")
        legacy["cohorts"] = ["hax-accelerator-09"]
        self.assertTrue(acc.stale(legacy, {"tx_cohort": {"hax-accelerator-09": "HAX Accelerator 09"}}, TODAY))
        legacy["cohorts"] = []
        self.assertTrue(acc.stale(legacy, NAMES, TODAY))

    def test_capital_raised(self):
        self.assertIsNone(acc.capital_raised(hax_view("zetta-joule")))                # "" on the page
        self.assertEqual(acc.capital_raised(hax_view("zetta-joule", total_capital_raised=2000000)), 2000000.0)
        self.assertIsNone(acc.capital_raised(hax_view("zetta-joule", total_capital_raised=0)))


class SosvSignals(unittest.TestCase):
    def test_zetta_joule_listing(self):
        view = hax_view("zetta-joule")
        s = acc.listing_signal(view, acc.sosv_verdict(view, NAMES), NAMES, [])
        s.validate()
        self.assertEqual(s.kind, "hax_company")
        self.assertEqual(s.entity.name, "Zetta Joule")
        self.assertEqual(s.entity.domain, "zetta-joule.com")
        self.assertEqual(s.entity.founded, "2023")
        self.assertEqual(s.entity.location, "Houston, Texas, United States")
        self.assertEqual(s.entity.one_liner, "ZettaJoule delivers modernized HTGR through Energy-as-a-Service Solution")
        self.assertEqual(s.entity.links["hax"], "https://hax.co/company/zetta-joule/")
        self.assertEqual(s.entity.links["linkedin"], "https://www.linkedin.com/company/zettajoule")
        self.assertNotIn("twitter", s.entity.links)  # blank on the page
        self.assertEqual(s.entity.aliases, ["ZettaJoule"])
        self.assertEqual(s.title, "New HAX portfolio page in the HAX Seed 2024 cohort")
        self.assertEqual(s.url, "https://hax.co/company/zetta-joule/")
        self.assertEqual(s.occurred_at, "2026-09-21T20:46:13Z")
        self.assertEqual(s.metrics["date_basis"], "post_published")
        self.assertEqual(s.metrics["record_api_url"], "https://hax.co/wp-json/wp/v2/company/9551")
        self.assertEqual(s.metrics["sosv_cohorts"], ["HAX Seed 2024"])
        self.assertEqual(s.metrics["sosv_programs"], ["SOSV HAX"])
        self.assertEqual(s.metrics["founded_year"], 2023)
        self.assertFalse(s.metrics["now_raising"])
        self.assertNotIn("employee_count_range", s.metrics)
        self.assertNotIn("total_capital_raised_usd", s.metrics)
        self.assertIsNone(s.value)
        # A 2024-cohort company whose page went up in 2026: a back-fill, routine however good the fit.
        self.assertEqual(s.metrics["cohort_year"], 2024)
        self.assertTrue(s.metrics["backfilled_page"])
        self.assertTrue(0.15 <= s.strength <= 0.3, s.strength)
        self.assertIn("nuclear fission", s.text)
        self.assertNotIn("<p>", s.text)

    def test_topologiq_listing(self):
        view = acc.company_view([copy.deepcopy(TOPOLOGIQ)])
        names = NAMES
        s = acc.listing_signal(view, acc.sosv_verdict(view, names), names, [])
        s.validate()
        self.assertEqual((s.kind, s.entity.name, s.entity.domain), ("hax_company", "TopologiQ", "topologiq.ai"))
        self.assertEqual(s.title, "New SOSV portfolio page in the HAX Seed 2026 cohort")
        self.assertEqual(s.occurred_at, "2026-08-31T13:49:05Z")
        self.assertEqual(s.entity.one_liner, "Building microprocessors/supercomputers for real-time edge compute.")
        self.assertIsNone(s.entity.description)            # empty body: nothing made up
        self.assertIsNone(s.entity.founded)
        self.assertFalse(acc.stale(view, names, TODAY))    # no founding year; HAX Seed 2026 carries the vintage
        # A first page in its own cohort year is solid, in the lower half of the band: one thesis term
        # is all the page says. A page of the same cohort year that says more outranks it.
        self.assertEqual(s.strength, acc.listing_strength(acc.sosv_verdict(view, names), view, names))
        self.assertTrue(0.35 <= s.strength <= 0.45, s.strength)
        more = hax_view("halltech-bv")                      # HAX Seed 2026, no founding year either
        self.assertGreater(acc.listing_strength(acc.sosv_verdict(more, names), more, names), s.strength + 0.05)
        self.assertEqual(s.metrics["thesis_fit"], s.metrics["own_words_fit"])   # no categories, and the note is no evidence
        # What the pipeline classifies (name, one-liner, title, text) clears the gate, in the right sector.
        seen = classify(" ".join([s.entity.name, s.entity.one_liner, s.title, s.text]))
        self.assertGreaterEqual(seen["fit"], 0.3)
        self.assertEqual(seen["sector"], "semiconductors")

    def test_current_cohort_page_outranks_a_backfilled_one(self):
        fresh, back = hax_view("halltech-bv"), hax_view("zetta-joule")
        s_fresh = acc.listing_strength(acc.sosv_verdict(fresh, NAMES), fresh, NAMES)   # HAX Seed 2026, up in 2026
        s_back = acc.listing_strength(acc.sosv_verdict(back, NAMES), back, NAMES)      # HAX Seed 2024, up in 2026
        self.assertGreater(s_fresh, s_back)
        self.assertLessEqual(s_fresh, 0.7)
        self.assertGreaterEqual(s_fresh, 0.35)                      # a first appearance in its own cohort year: solid
        self.assertLessEqual(s_back, acc.BACKFILL_MAX_STRENGTH)     # routine
        fresh_signal = acc.listing_signal(fresh, acc.sosv_verdict(fresh, NAMES), NAMES, [])
        self.assertFalse(fresh_signal.metrics["backfilled_page"])
        # Pages trail the cohort: a 2025-cohort page that went up in March 2026 is not a back-fill,
        # it only gives up the same-year bonus.
        lag = hax_view("terabora")                                   # HAX Seed 2025, founded 2025, up 2026-03-17
        self.assertFalse(acc.backfilled(lag, NAMES))
        s_lag = acc.listing_strength(acc.sosv_verdict(lag, NAMES), lag, NAMES)
        self.assertGreater(s_lag, acc.BACKFILL_MAX_STRENGTH)
        self.assertTrue(acc.backfilled(back, NAMES))

    def test_title_never_carries_a_slug(self):
        view = hax_view("halltech-bv")
        self.assertEqual(acc.listing_title(view, NAMES), "New HAX portfolio page in the HAX Seed 2026 cohort")
        # Cohort name not returned: fall back to the program's name, then to nothing. Never the slug.
        self.assertEqual(acc.listing_title(view, {"tx_program": NAMES["tx_program"]}),
                         "New HAX portfolio page in the SOSV HAX program")
        self.assertEqual(acc.listing_title(view, {}), "New HAX portfolio page")

    def test_categories_feed_the_text_with_decoded_names(self):
        view = hax_view("5qxt")
        text = acc.sosv_text(view, NAMES)
        self.assertIn("SOSV categories: Manufacturing & Energy, Critical Minerals, Materials", text)
        # Without the names lookup the slugs are used as written, never guessed at.
        self.assertIn("SOSV categories: manufacturing-energy, critical-minerals, materials", acc.sosv_text(view, {}))

    def test_raising_flag_is_a_metric_and_never_a_signal(self):
        # The record says "now raising" but nothing says since when; modified_gmt is a re-save date.
        view = hax_view("zetta-joule", now_raising=True, total_capital_raised=2000000)
        self.assertTrue(view["now_raising"])
        s = acc.listing_signal(view, acc.sosv_verdict(view, NAMES), NAMES, [])
        self.assertEqual(s.kind, "hax_company")
        self.assertTrue(s.metrics["now_raising"])
        self.assertEqual(s.occurred_at, "2026-09-21T20:46:13Z")      # the publish date, not modified_gmt
        self.assertEqual(s.metrics["total_capital_raised_usd"], 2000000.0)
        self.assertNotIn("amount_usd", s.metrics)                    # total raised is not a round size
        self.assertNotIn("raising", s.title.lower())
        self.assertFalse(hasattr(acc, "raising_signal"))

    def test_people_roles_as_written(self):
        people = acc.sosv_people(HAX["founder"], "Juno Propulsion")
        self.assertEqual([p.name for p in people],
                         ["Mario Sanchez", "Arne Arens", "Johnathan Traudt", "Imad Agha", "Ariana Martinez"])
        ariana = people[-1]
        self.assertEqual(ariana.role, "Co-founder & CTO")
        # Only the LinkedIn profile: the founder post's own URL shows nothing about the company
        # (sosv.com redirects it to /portfolio/), so it is not offered as a link.
        self.assertEqual(ariana.links, {"linkedin": "https://www.linkedin.com/in/ari-martinez-ph-d-a36205a6/"})
        self.assertIsNone(people[2].role)                 # blank position stays blank
        self.assertNotIn("linkedin", people[0].links)

    def test_people_dedupe_and_company_suffix(self):
        rows = [
            {"title": {"rendered": "Chris Wightman &#8211; Protogenix"}, "acf": {"position": "CEO"},
             "link": "https://sosv.com/founder/chris-wightman-protogenix/"},
            {"title": {"rendered": "Kai Latham"}, "acf": {}, "link": "https://sosv.com/founder/kai-latham/"},
            {"title": {"rendered": "Kai Latham"}, "acf": {}, "link": "https://sosv.com/founder/kai-latham-2/"},
            {"title": {"rendered": ""}, "acf": {}},
        ]
        people = acc.sosv_people(rows, "Protogenix")
        self.assertEqual([p.name for p in people], ["Chris Wightman", "Kai Latham"])
        # A dash that is part of the name, not the company, is kept.
        other = acc.sosv_people(rows[:1], "Klona Biotech")
        self.assertEqual(other[0].name, "Chris Wightman – Protogenix")
        # The company's short form after the dash is still the company.
        short = acc.sosv_people(rows[:1], "Protogenix Labs")
        self.assertEqual(short[0].name, "Chris Wightman")


class Paging(unittest.TestCase):
    def pages(self, served, headers):
        calls = []

        def request(url, **kw):
            page = kw["params"]["page"]
            calls.append(page)
            if page > len(served):
                raise http.HttpError(400, url, '{"code":"rest_post_invalid_page_number"}')
            return json.dumps(served[page - 1]), headers

        with mock.patch.object(acc.http, "request", request):
            return acc.wp_list("https://sosv.com/wp-json/wp/v2", "company", {}), calls

    def test_stops_on_the_total_pages_header(self):
        full = [{"id": i} for i in range(100)]
        rows, calls = self.pages([full, full, [{"id": 1}]], {"x-wp-totalpages": "2"})
        self.assertEqual((len(rows), calls), (200, [1, 2]))

    def test_a_400_past_the_last_full_page_ends_the_list_and_keeps_the_rows(self):
        full = [{"id": i} for i in range(100)]
        rows, calls = self.pages([full, full], {})  # exactly 200 records and no paging header
        self.assertEqual((len(rows), calls), (200, [1, 2, 3]))

    def test_a_failure_on_the_first_page_is_raised(self):
        with self.assertRaises(http.HttpError):
            self.pages([], {})

    def test_non_list_and_non_dict_rows(self):
        rows, _ = self.pages([[{"id": 1}, "junk", None]], {"x-wp-totalpages": "1"})
        self.assertEqual(rows, [{"id": 1}])
        rows, _ = self.pages([{"code": "rest_error"}], None)
        self.assertEqual(rows, [])


class FakeHttp:
    """Serves the fixtures in place of antenna.http; any other URL fails the test."""

    def __init__(self, *, sosv_down=False, news_down=False, raising_resaved=False):
        self.calls: list[tuple[str, dict]] = []
        self.sosv_down = sosv_down
        self.news_down = news_down
        self.raising_resaved = raising_resaved

    # WordPress collections go through http.request for the paging header.
    def request(self, url, **kw):
        params = kw.get("params") or {}
        self.calls.append((url, params))
        assert kw.get("return_headers") is True
        assert params.get("per_page") == 100 and params.get("page") == 1, params
        if url == "https://hax.co/wp-json/wp/v2/company":
            rows = copy.deepcopy(HAX["company"])
            for r in rows:
                r.pop("content")  # the real scan asks for no page bodies
                if self.raising_resaved and r["slug"] == "cargo-robotics":
                    # An on-thesis page from March, flag on, re-saved inside the lookback.
                    r["acf"]["now_raising"] = True
                    r["modified_gmt"] = "2026-09-30T10:00:00"
            return json.dumps(rows), {"x-wp-total": "10", "x-wp-totalpages": "1"}
        if url == "https://sosv.com/wp-json/wp/v2/company":
            if self.sosv_down:
                raise http.HttpError(503, url)
            twin = {k: v for k, v in ZETTA_SOSV.items() if k != "_site"}
            return json.dumps([twin]), {"x-wp-total": "1", "x-wp-totalpages": "1"}
        raise AssertionError(f"unexpected request {url}")

    def get_json(self, url, **kw):
        params = kw.get("params") or {}
        self.calls.append((url, params))
        if url == acc.SPEEDRUN_NEWS:
            if self.news_down:
                raise TimeoutError("handshake timed out")
            assert params.get("search") == "kickoff"
            return POSTS
        if url == acc.SPEEDRUN_API:
            return SPEEDRUN_LIST
        if url.startswith(acc.SPEEDRUN_API):
            cid = url[len(acc.SPEEDRUN_API):].strip("/")
            if cid in DETAILS:
                return DETAILS[cid]
            raise http.HttpError(404, url)  # one detail down must not lose the run
        if url == "https://hax.co/wp-json/wp/v2/company" and "include" in params:
            want = {int(x) for x in params["include"].split(",")}
            return [{"id": r["id"], "content": r["content"]} for r in HAX["company"] if r["id"] in want]
        if url == "https://sosv.com/wp-json/wp/v2/company" and "include" in params:
            assert params["include"] == "22484"
            return [{"id": 22484, "content": HAX["company"][0]["content"]}]
        for tax, terms in TERMS.items():
            if url == f"https://sosv.com/wp-json/wp/v2/{tax}":
                return [{"slug": s, "name": terms[s]} for s in params["slug"].split(",") if s in terms]
        if url.endswith("/wp-json/wp/v2/founder"):
            return [f for f in HAX["founder"] if params.get("tx_company") in f["tx_company"]]
        raise AssertionError(f"unexpected get_json {url} {params}")


class CollectOffline(unittest.TestCase):
    def run_collect(self, fake=None, **ctx_kw):
        fake = fake or FakeHttp()
        ctx = Context(today=TODAY, **ctx_kw)
        with mock.patch.object(acc.http, "get_json", fake.get_json), \
                mock.patch.object(acc.http, "request", fake.request), \
                mock.patch("sys.stderr"):
            signals = list(acc.collect(ctx))
        return signals, ctx, fake

    def test_end_to_end_default_lookback(self):
        signals, ctx, fake = self.run_collect()
        for s in signals:
            s.validate()
            self.assertIn(s.kind, ("speedrun_cohort", "hax_company", "sosv_company"))
            self.assertGreaterEqual(s.occurred_at[:10], ctx.since.isoformat())
            self.assertLessEqual(s.occurred_at[:10], TODAY.isoformat())
            self.assertLessEqual(len(s.title), 110)
            self.assertFalse(s.title.endswith("."))
            self.assertIn(s.metrics["date_basis"], ("cohort_kickoff_post_published", "post_published"))
        got = [(s.kind, s.entity.name) for s in signals]
        # Only Zetta Joule's page went up inside 120 days; only SR007 kicked off inside them.
        self.assertEqual(got, [("hax_company", "Zetta Joule"),
                               ("speedrun_cohort", "Cybernetic Physics"),
                               ("speedrun_cohort", "Munari")])

        zetta = signals[0]
        self.assertEqual(zetta.url, "https://sosv.com/company/zetta-joule/")       # the older of the two records
        self.assertEqual(zetta.occurred_at, "2026-09-21T20:46:09Z")
        self.assertEqual(zetta.title, "New SOSV portfolio page in the HAX Seed 2024 cohort")
        self.assertEqual(zetta.metrics["listed_on"], ["hax.co", "sosv.com"])
        self.assertEqual([s.occurred_at for s in signals[1:]], ["2026-08-05", "2026-08-05"])
        self.assertEqual(zetta.metrics["records"], 2)
        self.assertEqual(zetta.entity.location, "Houston, Texas, United States")
        self.assertIn("nuclear fission", zetta.text)                               # body fetched after the scan
        self.assertEqual(zetta.people, [])                                         # no founder post yet: none invented
        founder_calls = [p for u, p in fake.calls if u.endswith("/founder")]
        self.assertEqual([p["tx_company"] for p in founder_calls], [2796, 865])    # asked on both sites

        # SR001 to SR006 rows are in the list fixture; none is emitted, none is fetched.
        details = [u for u, _ in fake.calls if u.startswith(acc.SPEEDRUN_API) and u != acc.SPEEDRUN_API]
        self.assertEqual(len(details), 7)  # the seven SR007 rows
        self.assertNotIn(("speedrun_cohort", "Superintelligence Foundry"), got)
        # Four detail calls answered 404: warned about, run kept going.
        self.assertEqual(sum("HttpError" in w for w in ctx.warnings), 4)

    def test_longer_lookback_reaches_older_pages_and_cohorts(self):
        signals, ctx, _ = self.run_collect(lookback_days=250)   # since 2026-01-24
        got = {(s.kind, s.entity.name) for s in signals}
        for name in ("Zetta Joule", "5QXT", "Robotic Actuators Company", "Halltech B.V.", "Terabora", "Cargo Robotics"):
            self.assertIn(("hax_company", name), got)
        for name in ("VISION IV", "Navlive", "Sharper Sense"):    # founded 2021, 2022, 2020
            self.assertNotIn(("hax_company", name), got)
        self.assertNotIn(("hax_company", "VeruStruct"), got)      # labels only
        for s in signals:
            self.assertGreaterEqual(s.occurred_at[:10], ctx.since.isoformat())
        # SR006 kicked off 2026-02-05, inside 250 days, but its detail records are not served here.
        self.assertNotIn("SR006", {s.metrics.get("speedrun_cohort") for s in signals})
        self.assertEqual({s.kind for s in signals}, {"hax_company", "speedrun_cohort"})

    def test_limit_caps_entities(self):
        signals, _, fake = self.run_collect(limit=2)
        self.assertEqual([s.entity.name for s in signals], ["Zetta Joule", "Cybernetic Physics"])
        details = [u for u, _ in fake.calls if u.startswith(acc.SPEEDRUN_API) and u != acc.SPEEDRUN_API]
        self.assertLess(len(details), 7)  # stopped reading detail records once the cap was reached

    def test_undated_cohorts_are_not_emitted(self):
        signals, ctx, _ = self.run_collect(FakeHttp(news_down=True))
        self.assertEqual({s.kind for s in signals}, {"hax_company"})
        self.assertTrue(any("cannot be dated" in w for w in ctx.warnings))

    def test_one_site_down_keeps_the_rest(self):
        signals, ctx, _ = self.run_collect(FakeHttp(sosv_down=True))
        self.assertTrue(any("sosv.com company scan failed" in w for w in ctx.warnings))
        got = [(s.kind, s.entity.name) for s in signals]
        self.assertIn(("speedrun_cohort", "Cybernetic Physics"), got)
        zetta = next(s for s in signals if s.entity.name == "Zetta Joule")
        self.assertEqual(zetta.url, "https://hax.co/company/zetta-joule/")  # hax.co alone still carries it
        self.assertEqual(zetta.title, "New HAX portfolio page in the HAX Seed 2024 cohort")

    def test_a_resaved_record_with_the_raising_flag_emits_nothing(self):
        plain, _, _ = self.run_collect()
        signals, _, _ = self.run_collect(FakeHttp(raising_resaved=True))
        self.assertEqual([(s.kind, s.entity.name, s.occurred_at) for s in signals],
                         [(s.kind, s.entity.name, s.occurred_at) for s in plain])
        self.assertNotIn("Cargo Robotics", [s.entity.name for s in signals])

    def test_nothing_before_the_lookback(self):
        signals, _, _ = self.run_collect(lookback_days=5)  # since 2026-09-26
        self.assertEqual(signals, [])


if __name__ == "__main__":
    unittest.main()
