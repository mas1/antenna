"""Offline tests for the ats_jobs collector: parsing and counting against saved fixtures.

No network. The end-to-end tests swap the three antenna.http calls the
collector uses for a stub that serves the fixtures, answers 404 for every
other slug, and fails the test if the real client is reached.
"""

from __future__ import annotations

import json
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from antenna import http
from antenna.collectors import ats_jobs as a
from antenna.collectors.base import Context

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "hiring"
TODAY = date(2026, 10, 1)

GREENHOUSE = json.loads((FIX / "greenhouse_valaratomics_jobs_content.json").read_text())
ASHBY = json.loads((FIX / "ashby_charge-robotics.json").read_text())
LEVER = json.loads((FIX / "lever_cx2.json").read_text())
SEED = json.loads((FIX / "ats_registry_seed.json").read_text())


def role(title: str, posted: date | None, location: str = "Austin, TX", url: str | None = None) -> a.Role:
    r = a.make_role(title, posted, location, None, url)
    assert r is not None
    return r


def board(provider: str, slug: str, name: str | None, roles: list[a.Role], website: str | None = None,
          text: str = "", origin: str = "guess") -> a.Board:
    return a.Board(provider, slug, name, website, roles, text=text, origin=origin)


def charge_board(**over) -> a.Board:
    kw = dict(provider="ashby", slug="charge-robotics", name="Charge Robotics", roles=a.parse_ashby(ASHBY),
              website="https://chargerobotics.com/", text=a.ashby_text(ASHBY), origin="registry")
    kw.update(over)
    b = board(**kw)
    b.verified_by = "website"
    return b


class Parsing(unittest.TestCase):
    def test_greenhouse(self):
        name, roles = a.parse_greenhouse(GREENHOUSE)
        self.assertEqual(name, "Valar Atomics")
        self.assertEqual(len(roles), GREENHOUSE["meta"]["total"])
        self.assertEqual(len(roles), 94)
        first = roles[0]
        self.assertEqual(first.title, "Automation and Controls Engineer")  # trailing space in the source
        self.assertEqual(first.posted, date(2026, 9, 17))  # 2026-09-17T15:40:12-04:00, date as written
        self.assertEqual(first.location, "Torrance, California, United States")
        self.assertEqual(first.department, "Instrumentation & Controls")
        self.assertEqual(first.url, "https://job-boards.greenhouse.io/valaratomics/jobs/4410316009")
        self.assertEqual(first.function, "controls_embedded")

    def test_ashby(self):
        roles = a.parse_ashby(ASHBY)
        self.assertEqual(len(roles), 14)
        self.assertEqual(roles[0].title, "R&D Technician (electrical assembly)")
        self.assertEqual(roles[0].posted, date(2026, 6, 9))
        self.assertEqual(roles[0].url, "https://jobs.ashbyhq.com/charge-robotics/067fef83-0fe8-4f2f-81c4-f72115256782")
        heads = [r.title for r in roles if r.notable]
        self.assertEqual(heads, ["Head of Manufacturing", "Head of Engineering"])

    def test_ashby_skips_unlisted(self):
        payload = {"jobs": [{"title": "Hidden", "publishedAt": "2026-09-01T00:00:00+00:00", "isListed": False},
                            {"title": "Shown", "publishedAt": "2026-09-01T00:00:00+00:00", "isListed": True}]}
        self.assertEqual([r.title for r in a.parse_ashby(payload)], ["Shown"])

    def test_lever(self):
        roles = a.parse_lever(LEVER)
        self.assertEqual(len(roles), 5)
        by_title = {r.title: r for r in roles}
        self.assertEqual(by_title["Electrical Engineer"].posted, date(2026, 9, 24))  # createdAt 1790267124957 ms
        self.assertEqual(by_title["Head of Manufacturing"].posted, date(2026, 8, 27))
        self.assertTrue(by_title["Head of Manufacturing"].notable)
        self.assertEqual(by_title["Electrical Engineer"].location, "El Segundo, CA")
        self.assertTrue(by_title["Electrical Engineer"].url.startswith("https://jobs.lever.co/cx2/"))

    def test_malformed_payloads_do_not_raise(self):
        self.assertEqual(a.parse_greenhouse(None), (None, []))
        self.assertEqual(a.parse_greenhouse({"jobs": [None, {"title": ""}, {"title": "X", "first_published": "nope"}]})[1][0].posted, None)
        self.assertEqual(a.parse_ashby({"jobs": None}), [])
        self.assertEqual(a.parse_lever({"ok": False}), [])
        self.assertEqual(a.parse_lever([{"text": "X", "createdAt": "soon"}])[0].posted, None)

    def test_odd_field_shapes_do_not_lose_the_board(self):
        # One row with a string where an object is expected must not raise and take the other rows with it.
        gh = {"jobs": [{"title": "Welder", "first_published": "2026-09-30T10:00:00-04:00", "location": "Austin, TX",
                        "departments": ["Production", {"name": "Shop"}, None], "company_name": "Acme"},
                       {"title": {"oops": 1}, "location": {"name": "Nowhere"}},
                       {"title": "GNC Engineer", "location": {"name": "El Segundo"}, "departments": "Flight",
                        "absolute_url": 42}],
              "meta": None}
        name, roles = a.parse_greenhouse(gh)
        self.assertEqual(name, "Acme")
        self.assertEqual([(r.title, r.location, r.department, r.url) for r in roles],
                         [("Welder", "Austin, TX", "Production", None), ("GNC Engineer", "El Segundo", None, None)])
        self.assertEqual(a.parse_greenhouse([1, 2])[1], [])
        ashby = {"jobs": [{"title": "Pilot", "publishedAt": "2026-09-30T10:00:00+00:00", "location": {"name": "Austin"},
                           "department": {"name": "Flight"}}, "junk", {"title": ["x"]}]}
        self.assertEqual([(r.title, r.location, r.department) for r in a.parse_ashby(ashby)], [("Pilot", "Austin", "Flight")])
        self.assertEqual(a.ashby_text({"jobs": "none"}), "")
        lever = [{"text": "Machinist", "createdAt": 1790267124957, "categories": "Shop"}, 7]
        self.assertEqual([(r.title, r.location) for r in a.parse_lever(lever)], [("Machinist", None)])

    def test_source_date(self):
        self.assertEqual(a.source_date("2026-09-30T22:15:00-04:00"), date(2026, 9, 30))  # not shifted to UTC
        self.assertEqual(a.source_date("2026-06-09T20:25:19.473+00:00"), date(2026, 6, 9))
        self.assertEqual(a.source_date(1790267124957), date(2026, 9, 24))
        self.assertIsNone(a.source_date(None))
        self.assertIsNone(a.source_date(""))
        self.assertIsNone(a.source_date(True))

    def test_lever_page_name(self):
        self.assertEqual(a.lever_page_name("<html><head><title>Sila Services</title></head></html>"), "Sila Services")
        self.assertEqual(a.lever_page_name("<title>R&amp;D Co</title>"), "R&D Co")
        self.assertIsNone(a.lever_page_name("<html></html>"))


class Titles(unittest.TestCase):
    def test_functions(self):
        cases = {
            "Head of Manufacturing": "manufacturing",
            "Structural Welder / Fabricator": "manufacturing",
            "Wire Harness Technician": "manufacturing",
            "Senior Mechanical Engineer": "hardware",
            "RF Hardware Engineer": "hardware",
            "ASIC Design Verification Engineer": "hardware",
            "Embedded Software Engineer": "controls_embedded",
            "GNC Engineer": "controls_embedded",
            "Senior Firmware Engineer": "controls_embedded",
            "Machine Learning Engineer, Perception": "ml_ai",
            "Principal AI Research Engineer - RL": "ml_ai",
            "Senior Software Engineer, Backend": "software",
            "Account Executive, Federal": "gtm",
            "Head of Sales": "gtm",
            "Director, Government Relations - Defense": "gtm",
            "Senior Technical Recruiter": "g_and_a",
            "Commercial Counsel": "g_and_a",
            "Export Controls Manager": "g_and_a",
            "Communications Systems Engineer": "hardware",  # not marketing
            "Operations Lead": "other",
        }
        for title, want in cases.items():
            self.assertEqual(a.classify_function(title), want, title)

    def test_department_is_the_fallback(self):
        self.assertEqual(a.classify_function("Engineer II", "Avionics"), "controls_embedded")
        self.assertEqual(a.classify_function("Sales Engineer", "Avionics"), "gtm")

    def test_notable(self):
        for t in ("Head of Manufacturing", "VP of Enterprise Sales", "Vice President, Software",
                  "Chief Financial Officer", "General Counsel", "CTO"):
            self.assertTrue(a.is_notable(t), t)
        for t in ("Chief Engineer", "Chief of Staff", "Executive Assistant to the CEO", "Deputy Head of Production",
                  "Senior Mechanical Engineer", "Director of Avionics",
                  "Associate General Counsel, Government Contracts"):
            self.assertFalse(a.is_notable(t), t)

    def test_evergreen(self):
        for t in ("General Application", "Your Dream Job", "Join our Talent Network", "Future Opportunities",
                  "Rockstar", "TEST JOB 2", "Don't see your role?", "<insert job you excel at>"):
            self.assertTrue(a.is_evergreen(t), t)
        for t in ("Test Engineer", "General Manager", "Flight Test Lead"):
            self.assertFalse(a.is_evergreen(t), t)

    def test_display_title_drops_requisition_ids(self):
        self.assertEqual(a.display_title("Flight Software Engineer (R5156)"), "Flight Software Engineer")
        self.assertEqual(a.display_title("Mechanical Engineer (all levels)"), "Mechanical Engineer (all levels)")
        self.assertEqual(a.display_title("  Head   of  Sales "), "Head of Sales")

    def test_github_is_a_login_never_a_url(self):
        self.assertEqual(a.github_login("charge-robotics"), "charge-robotics")
        self.assertEqual(a.github_login("https://github.com/Charge-Robotics/"), "Charge-Robotics")
        self.assertEqual(a.github_login("github.com/acme/repo"), "acme")
        for junk in (None, "", "https://gitlab.com/acme", "not a login", {"login": "x"}):
            self.assertIsNone(a.github_login(junk), junk)


class Counting(unittest.TestCase):
    def stats(self, roles, asof=TODAY, after=None):
        return a.board_stats(a.normalize_dates(roles, TODAY), asof, after)

    def test_valar_matches_the_source_card(self):
        # Card section 2.1, measured 2026-10-01: 94 open, 9 new in 14 d, 32 in 30 d, 33 in the prior 30.
        st = self.stats(a.parse_greenhouse(GREENHOUSE)[1])
        self.assertEqual((st.open_roles, st.new_14d, st.new_30d, st.prev_30d), (94, 9, 32, 33))
        self.assertEqual(st.newest, date(2026, 10, 1))
        self.assertEqual(st.n_locations, 3)
        self.assertEqual(sum(st.function_mix.values()), 94)
        self.assertIsNone(st.bulk_day)

    def test_charge_matches_the_source_card(self):
        st = self.stats(a.parse_ashby(ASHBY))
        self.assertEqual((st.open_roles, st.new_14d, st.new_30d, st.prev_30d), (14, 11, 11, 0))
        self.assertEqual(st.hardware_share, 0.71)
        self.assertEqual([r.title for r in st.notable_new], ["Head of Manufacturing", "Head of Engineering"])
        self.assertEqual((st.bulk_day, st.bulk_share), (date(2026, 9, 28), 1.0))  # all 11 on one day
        self.assertEqual(st.newest, date(2026, 9, 28))

    def test_cx2_matches_the_source_card(self):
        st = self.stats(a.parse_lever(LEVER))
        self.assertEqual((st.open_roles, st.new_14d, st.new_30d, st.prev_30d), (5, 3, 3, 1))
        self.assertEqual(st.notable_new, [])  # Head of Manufacturing was posted 35 days ago

    def test_window_edges(self):
        roles = [role("A", TODAY), role("B", TODAY - timedelta(days=13)), role("C", TODAY - timedelta(days=14)),
                 role("D", TODAY - timedelta(days=29)), role("E", TODAY - timedelta(days=30)),
                 role("F", TODAY - timedelta(days=59)), role("G", TODAY - timedelta(days=60))]
        st = a.board_stats(roles, TODAY)
        self.assertEqual((st.new_14d, st.new_30d, st.prev_30d), (2, 4, 2))

    def test_evergreen_counts_literally_but_never_toward_strength(self):
        # Periodic Labs, 2026-10-01: "Don't See Your Role? Apply Here!" was dated inside 30 days. A recount of
        # the source gives 8, so the title's number is 8; strength is built on the 7 real roles.
        roles = [role("General Application", TODAY), role("Controls Engineer", TODAY),
                 role("Talent Network", TODAY - timedelta(days=40)), role("Welder", TODAY - timedelta(days=41))]
        st = a.board_stats(roles, TODAY)
        self.assertEqual((st.open_roles, st.evergreen_roles), (4, 2))
        self.assertEqual((st.new_14d, st.new_30d, st.prev_30d), (2, 2, 2))
        self.assertEqual((st.distinct_new_30d, st.distinct_prev_30d), (1, 1))
        self.assertEqual(a.velocity_title(st), "2 of 4 open roles posted in the last 30 days, against 2 in the 30 days before")
        real_only = a.board_stats([r for r in roles if not r.evergreen], TODAY)
        self.assertEqual((real_only.distinct_new_30d, real_only.distinct_prev_30d), (1, 1))
        only_catch_all = a.board_stats([role("General Application", TODAY), role("Welder", TODAY - timedelta(days=90))], TODAY)
        self.assertEqual(a.velocity_strength(only_catch_all), a.STRENGTH_FLOOR)
        self.assertEqual(only_catch_all.notable_new, [])

    def test_requisition_per_seat_counts_once_for_strength(self):
        roles = [role(f"Flight Test Engineer (R{5100 + i})", TODAY) for i in range(6)]
        st = a.board_stats(roles, TODAY)
        self.assertEqual((st.new_30d, st.distinct_new_30d), (6, 1))

    def test_sole_gtm_role(self):
        roles = [role("Head of Sales", TODAY - timedelta(days=3)), role("Mechanical Engineer", TODAY - timedelta(days=70)),
                 role("Controls Engineer", TODAY - timedelta(days=80))]
        st = a.board_stats(roles, TODAY)
        self.assertEqual(st.sole_gtm.title, "Head of Sales")
        roles.append(role("Account Executive", TODAY - timedelta(days=90)))
        self.assertIsNone(a.board_stats(roles, TODAY).sole_gtm)

    def test_normalize_dates(self):
        roles = [role("Tomorrow UTC", TODAY + timedelta(days=1)), role("Next week", TODAY + timedelta(days=7)),
                 role("Undated", None), role("Old", TODAY - timedelta(days=5))]
        out = a.normalize_dates(roles, TODAY)
        self.assertEqual([(r.title, r.posted) for r in out],
                         [("Tomorrow UTC", TODAY), ("Undated", None), ("Old", TODAY - timedelta(days=5))])
        self.assertEqual(roles[0].posted, TODAY + timedelta(days=1))  # input left alone

    def test_past_asof_ignores_later_and_undated_roles(self):
        roles = [role("A", TODAY), role("B", TODAY - timedelta(days=40)), role("C", None)]
        st = a.board_stats(roles, TODAY - timedelta(days=30))
        self.assertEqual((st.open_roles, st.new_30d), (1, 1))


class BoardBirth(unittest.TestCase):
    def test_ordinary_boards(self):
        self.assertIsNone(a.detect_board_birth(a.parse_ashby(ASHBY)))
        self.assertIsNone(a.detect_board_birth(a.parse_greenhouse(GREENHOUSE)[1]))
        self.assertIsNone(a.detect_board_birth(a.parse_lever(LEVER)))

    def test_every_role_on_one_day(self):
        # General Galactic on 2026-10-01: 17 roles, one date.
        birth = a.detect_board_birth([role(f"Role {i}", TODAY) for i in range(17)])
        self.assertEqual((birth.first_day, birth.last_day, birth.batch, birth.dated, birth.full),
                         (TODAY, TODAY, 17, 17, True))
        self.assertEqual(birth.span, "on 2026-10-01")

    def test_too_few_roles_to_call(self):
        self.assertIsNone(a.detect_board_birth([role("A", TODAY), role("B", TODAY)]))
        self.assertIsNone(a.detect_board_birth([role("A", None)] * 5))

    def test_batch_spread_over_three_days_then_later_roles(self):
        # Mach Industries shape: most of the board lands over three days, then organic posting.
        d0 = date(2026, 8, 31)
        roles = ([role(f"A{i}", d0) for i in range(15)] + [role(f"B{i}", d0 + timedelta(days=1)) for i in range(43)]
                 + [role(f"C{i}", d0 + timedelta(days=2)) for i in range(36)]
                 + [role(f"D{i}", d0 + timedelta(days=10 + i % 20)) for i in range(40)])
        birth = a.detect_board_birth(roles)
        self.assertEqual((birth.first_day, birth.last_day, birth.batch, birth.dated, birth.full),
                         (d0, d0 + timedelta(days=2), 94, 134, False))
        self.assertEqual(birth.span, "from 2026-08-31 to 2026-09-02")
        st = a.board_stats(roles, TODAY, birth.last_day)
        self.assertEqual(st.prev_30d, 0)
        self.assertFalse(st.prior_known)  # the prior window overlaps the batch
        self.assertEqual(st.new_30d + sum(1 for r in roles if d0 + timedelta(days=2) < r.posted <= TODAY - timedelta(days=30)), 40)

    def test_small_cluster_on_an_old_board_is_not_a_birth(self):
        d0 = date(2026, 3, 2)
        roles = [role(f"A{i}", d0) for i in range(4)] + [role(f"B{i}", d0 + timedelta(days=30 * (i + 1))) for i in range(6)]
        self.assertIsNone(a.detect_board_birth(roles))  # 4 of 10: below the 5-role minimum

    def test_first_batch_roles_never_count_as_velocity(self):
        roles = [role(f"A{i}", TODAY - timedelta(days=5)) for i in range(25)] + [role("Later", TODAY)]
        birth = a.detect_board_birth(roles)
        st = a.board_stats(roles, TODAY, birth.last_day)
        self.assertEqual((st.open_roles, st.new_14d, st.new_30d), (26, 1, 1))
        loud = a.board_stats(roles, TODAY)
        self.assertGreater(a.velocity_strength(loud), a.velocity_strength(st) + 0.3)


class Strength(unittest.TestCase):
    def make(self, new, prev, old=0, title="Mechanical Engineer"):
        roles = ([role(f"{title} {i}", TODAY - timedelta(days=i % 25), f"Site {i}") for i in range(new)]
                 + [role(f"{title} p{i}", TODAY - timedelta(days=31 + i % 25), f"Site {i}") for i in range(prev)]
                 + [role(f"{title} o{i}", TODAY - timedelta(days=200 + i), f"Site {i}") for i in range(old)])
        return a.board_stats(roles, TODAY)

    def test_bounds(self):
        self.assertEqual(a.velocity_strength(a.Stats(asof=TODAY)), 0.0)
        self.assertEqual(a.velocity_strength(self.make(0, 0, old=12)), a.STRENGTH_FLOOR)
        for new, prev, old in ((1, 0, 0), (3, 3, 10), (30, 5, 2), (300, 0, 0), (900, 400, 1200)):
            s = a.velocity_strength(self.make(new, prev, old))
            self.assertGreaterEqual(s, a.STRENGTH_FLOOR)
            self.assertLessEqual(s, a.STRENGTH_CAP)

    def test_more_new_roles_is_stronger(self):
        vals = [a.velocity_strength(self.make(n, 4, old=6)) for n in (1, 4, 10, 25, 60)]
        self.assertEqual(vals, sorted(vals))
        self.assertLess(vals[0], 0.3)
        self.assertGreater(vals[-1], 0.6)

    def test_acceleration_beats_steady_state(self):
        self.assertGreater(a.velocity_strength(self.make(20, 2)), a.velocity_strength(self.make(20, 20)) + 0.1)

    def test_tiny_counts_do_not_read_as_acceleration(self):
        self.assertLess(a.velocity_strength(self.make(2, 0, old=5)), 0.3)

    def test_big_board_is_discounted(self):
        small = a.velocity_strength(self.make(40, 20, old=20))
        huge = a.velocity_strength(self.make(40, 20, old=2000))
        self.assertLess(huge, small * 0.6)

    def test_bulk_publish_is_discounted(self):
        spread = [role(f"Mechanical Engineer {i}", TODAY - timedelta(days=i * 2), f"S{i}") for i in range(11)]
        lump = [role(f"Mechanical Engineer {i}", TODAY - timedelta(days=3), f"S{i}") for i in range(11)]
        old = [role(f"Old {i}", TODAY - timedelta(days=100 + i)) for i in range(3)]
        s_spread, s_lump = a.board_stats(spread + old, TODAY), a.board_stats(lump + old, TODAY)
        self.assertIsNone(s_spread.bulk_day)
        self.assertEqual(s_lump.bulk_day, TODAY - timedelta(days=3))
        self.assertAlmostEqual(a.velocity_strength(s_lump), a.velocity_strength(s_spread) * a.BULK_DISCOUNT, places=2)

    def test_fixture_strengths_are_ordered_as_a_partner_would(self):
        charge = a.velocity_strength(a.board_stats(a.parse_ashby(ASHBY), TODAY))
        valar = a.velocity_strength(a.board_stats(a.parse_greenhouse(GREENHOUSE)[1], TODAY))
        cx2 = a.velocity_strength(a.board_stats(a.parse_lever(LEVER), TODAY))
        self.assertGreater(charge, valar)  # 11 of 14 new with two heads beats 32 vs 33 steady
        self.assertGreater(valar, cx2)
        self.assertTrue(0.6 <= charge <= 0.8 and 0.35 <= valar <= 0.55 and 0.15 <= cx2 <= 0.3, (charge, valar, cx2))

    def test_big_boards_sit_in_the_routine_band(self):
        # Shaped like Rocket Lab on 2026-10-01: 263 of 559 new against 132. A partner already knows it is hiring.
        self.assertLessEqual(a.velocity_strength(self.make(263, 132, old=164)), 0.3)
        self.assertLessEqual(a.velocity_strength(self.make(137, 84, old=128)), 0.35)  # Crusoe, 349 open roles
        # Just over the line is barely touched.
        self.assertGreater(a.velocity_strength(self.make(60, 20, old=75)), 0.5)

    def test_birth_strength_rewards_a_small_new_board_over_a_big_republished_one(self):
        self.assertTrue(0.35 <= a.birth_strength(3, 0) < a.birth_strength(17, 0) < a.birth_strength(28, 2) <= 0.70)
        general_galactic = a.birth_strength(17, 0, 17)
        mach = a.birth_strength(94, 0, 134)  # 94 roles at once on a 134-role board: a republish
        one_x = a.birth_strength(30, 0, 92)
        self.assertGreater(general_galactic, mach + 0.15)
        self.assertGreater(general_galactic, one_x + 0.15)
        self.assertLessEqual(mach, 0.35)
        self.assertGreaterEqual(a.birth_strength(500, 3, 2400), a.STRENGTH_FLOOR)
        self.assertEqual(a.birth_strength(17, 0), general_galactic)  # board size defaults to the batch


class Series(unittest.TestCase):
    def test_weekly_series(self):
        roles = a.parse_ashby(ASHBY)
        series = a.weekly_series(roles, TODAY, 17)
        self.assertEqual([p["t"] for p in series], sorted(p["t"] for p in series))  # oldest first
        self.assertEqual(series[-1]["t"], "2026-10-01")
        self.assertEqual(series[-1]["v"], 11)  # the 2026-09-28 batch
        self.assertEqual(series[0]["t"], "2026-06-11")  # first week with a dated role, not padded with zeros
        self.assertEqual(sum(p["v"] for p in series), 14)
        self.assertEqual(series[-1]["s"], a.velocity_strength(a.board_stats(roles, TODAY)))
        self.assertLess(series[-2]["s"], 0.3)  # before the batch the board was quiet
        self.assertTrue(all(0.0 <= p["s"] <= 1.0 for p in series))

    def test_empty(self):
        self.assertEqual(a.weekly_series([role("A", None)], TODAY, 12), [])


class Identity(unittest.TestCase):
    """Every wrong-company case here is a real collision from the source card (section 2.2)."""

    ENG = ["Mechanical Engineer", "Propulsion Engineer", "Avionics Engineer", "Manufacturing Engineer"]

    def roles(self, titles):
        return [role(t, TODAY) for t in titles]

    def test_strict_name(self):
        self.assertEqual(a.strict_name("Dirac, Inc."), "dirac")
        self.assertEqual(a.strict_name("Rainmaker Technology Corporation"), "rainmaker technology")
        self.assertEqual(a.strict_name("Reflex Robotics"), "reflex robotics")  # descriptive words kept
        # base keeps "Company": it is the name in "The Boring Company", and "Nuclear" is some other company.
        self.assertEqual(a.strict_name("The Nuclear Company"), "nuclear company")
        self.assertEqual(a.strict_name("The Boring Company"), "boring company")
        self.assertEqual(a.strict_name("Rocket Co"), "rocket co")
        # Behind a name of two words or more it is a form, as "Corporation" is (registry: lever/picklerobot, ashby/base-power).
        self.assertEqual(a.strict_name("Pickle Robot Company"), "pickle robot")
        self.assertEqual(a.strict_name("Base Power Company"), "base power")
        self.assertEqual(a.strict_name("Pickle Robot Co"), "pickle robot")
        self.assertEqual(a.strict_name("Théa"), "th a")
        self.assertEqual(a.strict_name(None), "")
        # Legal forms come from base.normalize_name, so the ones registries write are covered too.
        for raw, want in (("GITAI USA INC", "gitai"), ("Newtwen S.r.l.", "newtwen"), ("RYENT L.L.C.", "ryent"),
                          ("CHIRONIX PTY LTD", "chironix"), ("Acme Inc. (YC S24)", "acme"),
                          ("Acme Holdings, Inc.", "acme"), ("Genesis AI", "genesis ai")):
            self.assertEqual(a.strict_name(raw), want, raw)

    def test_same_site_and_domain_in_text(self):
        self.assertTrue(a.same_site("swarm.aero", "www.swarm.aero"))
        self.assertTrue(a.same_site("careers.hadrian.co", "hadrian.co"))
        self.assertFalse(a.same_site("radiant.co", "radiantnuclear.com"))
        self.assertFalse(a.same_site(None, "x.com"))
        self.assertTrue(a.domain_in_text("valaratomics.com", "Write to careers@valaratomics.com today"))
        self.assertTrue(a.domain_in_text("valaratomics.com", '<a href="https://www.valaratomics.com/privacy">'))
        self.assertFalse(a.domain_in_text("radiant.co", "see https://radiant.com/jobs and radiant.co.uk"))
        self.assertFalse(a.domain_in_text("sift.com", "visit makesift.com"))

    def test_ashby_website_decides(self):
        ok = board("ashby", "radiant-industries", "Radiant", self.roles(self.ENG), "https://www.radiantnuclear.com/")
        self.assertEqual(a.verify_identity("Radiant", "radiantnuclear.com", "energy", ok), "website")
        wrong = board("ashby", "radiant", "Radiant", self.roles(self.ENG), "https://radiant.co/")
        self.assertIsNone(a.verify_identity("Radiant", "radiantnuclear.com", "energy", wrong))
        # A registry row does not override a website that says otherwise.
        wrong.origin = "registry"
        self.assertIsNone(a.verify_identity("Radiant", "radiantnuclear.com", "energy", wrong))

    def test_other_card_collisions_on_ashby(self):
        for name, domain, slug, site in (("Sift", "siftstack.com", "sift", "https://sift.com"),
                                         ("Boom Supersonic", "boomsupersonic.com", "boom", "https://boompay.app"),
                                         ("Arbor Energy", "arbor.co", "arbor", "https://joinarbor.com"),
                                         ("Foundation", "foundation.bot", "foundation", "https://buildwithfoundation.com")):
            b = board("ashby", slug, name.split()[0], self.roles(self.ENG), site)
            self.assertIsNone(a.verify_identity(name, domain, None, b), slug)

    def test_greenhouse_wrong_names(self):
        lending = board("greenhouse", "figure", "Figure Lending", self.roles(["Loan Officer", "Software Engineer"]))
        self.assertIsNone(a.verify_identity("Figure", "figure.ai", "robotics", lending))
        vet = board("greenhouse", "archer", "Archer Veterinary Clinic", self.roles(["Veterinary Technician"]))
        self.assertIsNone(a.verify_identity("Archer", "archer.com", "autonomy", vet))
        # normalize_name would call these equal; the strict comparison does not.
        reflex = board("greenhouse", "reflex", "Reflex", self.roles(["Robotics Engineer", "Head of Engineering"]))
        self.assertIsNone(a.verify_identity("Reflex Robotics", "reflexrobotics.com", "robotics", reflex))

    def test_a_project_is_never_matched_by_name(self):
        # A hobby repo called "dexterity" is not Dexterity the company, however the board got found.
        named = board("greenhouse", "dexterity", "Dexterity", self.roles(["Robotics Engineer", "Mechanical Engineer", "Controls Engineer"]))
        self.assertEqual(a.verify_identity("Dexterity", None, "robotics", board("greenhouse", "dexterity", "Dexterity", named.roles, origin="registry")), "registry")
        for origin in ("registry", "recorded", "guess"):
            named.origin = origin
            self.assertIsNone(a.verify_identity("dexterity", None, "robotics", named, "project"), origin)
        # Its own domain on the board is still proof.
        site = board("ashby", "dexterity", "Dexterity", named.roles, "https://dexterity.ai/")
        self.assertEqual(a.verify_identity("dexterity", "dexterity.ai", "robotics", site, "project"), "website")

    def test_same_name_wrong_company_needs_thesis_titles(self):
        # greenhouse/regent: a private-equity firm whose job text mentions manufacturing.
        pe = board("greenhouse", "regent", "Regent",
                   self.roles(["Director, Tax", "Legal Counsel, Litigation", "Senior Associate, M&A and Portfolio Improvement"]),
                   text="Our investments span retail, eCommerce, media, and manufacturing. Autonomy in decision making.")
        self.assertIsNone(a.verify_identity("REGENT", "regentcraft.com", "autonomy", pe))
        self.assertIsNone(a.verify_identity("REGENT", None, None, pe))

    def test_name_and_thesis(self):
        gh = board("greenhouse", "valaratomics", "Valar Atomics", a.parse_greenhouse(GREENHOUSE)[1])
        self.assertEqual(a.verify_identity("Valar Atomics", None, "energy", gh), "name_and_thesis")
        self.assertEqual(a.verify_identity("Valar Atomics, Inc.", "valaratomics.com", "energy", gh), "name_and_thesis")
        self.assertIsNone(a.verify_identity("Valar Atomics", None, "semiconductors", gh))  # wrong sector
        self.assertIsNone(a.verify_identity("Valar", None, "energy", gh))  # not the same name

    def test_company_in_a_name(self):
        titles = ["Robotics Software Engineer", "Mechanical Engineer", "Field Service Technician"]
        pickle = board("lever", "picklerobot", "Pickle Robot Company", self.roles(titles))
        self.assertEqual(a.verify_identity("Pickle Robot", None, "robotics", pickle), "name_and_thesis")
        nuclear = board("greenhouse", "thenuclearcompany", "The Nuclear Company",
                        self.roles(["Nuclear Engineer", "Reactor Operator", "Welding Engineer"]))
        self.assertEqual(a.verify_identity("The Nuclear Company", None, "energy", nuclear), "name_and_thesis")
        self.assertIsNone(a.verify_identity("Nuclear", None, "energy", nuclear))
        # The guesses carry the whole name, which is where its board is (greenhouse/thenuclearcompany).
        self.assertEqual(a.slug_candidates("The Nuclear Company", None)["greenhouse"], ["nuclearcompany", "thenuclearcompany"])
        self.assertEqual(a.slug_candidates("Base Power Company", None)["ashby"][:2], ["basepower", "base-power"])

    def test_title_words_the_thesis_no_longer_counts(self):
        # A same-named board whose titles only look like hardware: "SDR" is a sales role, "cyber defense" and
        # "robotic process automation" are software, a defense attorney is a lawyer.
        for titles in (["SDR", "Enterprise SDR Manager", "Account Executive"],
                       ["Cyber Defense Analyst", "Threat Defense Engineer", "Account Executive"],
                       ["RPA Developer, Robotic Process Automation", "Account Executive"],
                       ["Defense Attorney", "Paralegal"], ["Strike Team Lead", "PLC Programmer"]):
            b = board("lever", "prontosystems", "Pronto Systems", self.roles(titles))
            self.assertIsNone(a.verify_identity("Pronto Systems", None, None, b), titles)
        # One title word that can only mean hardware is enough for a name of two words, in the entity's own sector.
        radar = board("lever", "prontosystems", "Pronto Systems", self.roles(["Radar Systems Engineer", "Account Executive"]))
        self.assertEqual(a.verify_identity("Pronto Systems", None, "defense", radar), "name_and_thesis")
        self.assertIsNone(a.verify_identity("Pronto Systems", None, "energy", radar))
        self.assertIsNone(a.verify_identity("Pronto", None, "defense", board("lever", "pronto", "Pronto", radar.roles)))

    def test_one_word_name_needs_more(self):
        thin = board("lever", "pronto", "Pronto", self.roles(["Manufacturing Associate", "Account Executive"]))
        self.assertIsNone(a.verify_identity("Pronto", None, None, thin))
        rich = board("lever", "pronto", "Pronto", self.roles(["Autonomy Engineer, Autonomous Trucks", "Lidar Perception Engineer",
                                                              "Manufacturing Engineer", "Robotics Technician"]))
        self.assertEqual(a.verify_identity("Pronto", None, None, rich), "name_and_thesis")

    def test_one_ambiguous_title_word_needs_a_second_term(self):
        # The classifier holds a lone ambiguous term under its gate: "manufacturing" in one title of a
        # same-named board, and nothing else anywhere, proves little.
        titles = ["Manufacturing Associate", "Account Executive"]
        thin = board("lever", "prontosystems", "Pronto Systems", self.roles(titles))
        self.assertIsNone(a.verify_identity("Pronto Systems", None, None, thin))
        # A second title term does it, and so does one that can only mean hardware.
        more = board("lever", "prontosystems", "Pronto Systems", self.roles(titles + ["Solar Installer"]))
        self.assertEqual(a.verify_identity("Pronto Systems", None, None, more), "name_and_thesis")
        cnc = board("lever", "prontosystems", "Pronto Systems", self.roles(["CNC Machinist", "Account Executive"]))
        self.assertEqual(a.verify_identity("Pronto Systems", None, None, cnc), "name_and_thesis")
        # The second term may come from the job text (Arbor Energy and KoBold Metals, 2026-10-02: one
        # "manufacturing" title each, and a first job that says what the company builds).
        told = board("lever", "prontosystems", "Pronto Systems", self.roles(titles),
                     text="Pronto retrofits haul trucks so they drive themselves: an autonomy kit for mines.")
        self.assertEqual(a.verify_identity("Pronto Systems", None, None, told), "name_and_thesis")
        # The text never stands in for the titles: a lone context word in them is still no thesis title.
        soft = board("lever", "prontosystems", "Pronto Systems", self.roles(["Supply Chain Specialist", "Account Executive"]),
                     text=told.text + " Manufacturing and robotics at scale.")
        self.assertIsNone(a.verify_identity("Pronto Systems", None, None, soft))

    def test_description_is_a_second_text_for_a_name_only_match(self):
        shop = board("greenhouse", "pronto", "Pronto", self.roles(["Manufacturing Engineer", "CNC Machinist", "Welding Technician",
                                                                    "Quality Inspector, Machining"]))
        trucks = board("lever", "pronto", "Pronto", self.roles(["Autonomy Engineer, Autonomous Trucks", "Lidar Perception Engineer",
                                                                "Manufacturing Engineer", "Robotics Technician"]))
        about = "Autonomous haulage for mines: a self-driving kit retrofitted to off-road trucks."
        # The sector label alone can be this collector's own doing: score.thesis_of falls back to hiring text,
        # this collector's signals included, for an entity that has nothing else on thesis.
        self.assertEqual(a.verify_identity("Pronto", None, "manufacturing", shop), "name_and_thesis")
        # The company's own description shares no sector with a machine shop's board.
        self.assertIsNone(a.verify_identity("Pronto", None, "manufacturing", shop, about=about))
        self.assertEqual(a.verify_identity("Pronto", None, "autonomy", trucks, about=about), "name_and_thesis")
        # It never replaces the label: both must hold.
        self.assertIsNone(a.verify_identity("Pronto", None, "energy", trucks, about=about))
        # A description that is not on thesis, or rests on one ambiguous word, says too little to overrule.
        for weak in ("A marketplace for vintage furniture.", "Trademark goods: transceivers", "", None):
            self.assertEqual(a.verify_identity("Pronto", None, "manufacturing", shop, about=weak), "name_and_thesis", weak)
        # Only name-only matches consult it: a domain, a registry row or a recorded link is stronger evidence.
        site = board("ashby", "pronto", "Pronto", shop.roles, "https://pronto.ai/")
        self.assertEqual(a.verify_identity("Pronto", "pronto.ai", "autonomy", site, about=about), "website")
        shop.origin = "registry"
        self.assertEqual(a.verify_identity("Pronto", None, "autonomy", shop, about=about), "registry")
        shop.origin = "recorded"
        self.assertEqual(a.verify_identity("Pronto", None, "autonomy", shop, about=about), "recorded_name")

    def test_job_links_on_the_entity_domain(self):
        roles = [role("Technician", TODAY, url="https://www.agilityrobotics.com/careers?gh_jid=1")]
        b = board("greenhouse", "agilityrobotics", "Agility", roles)
        self.assertEqual(a.job_hosts(b), {"agilityrobotics.com"})
        self.assertEqual(a.verify_identity("Agility Robotics", "agilityrobotics.com", None, b), "job_url")
        # An ATS, or any other host clean_domain knows to be shared, is nobody's own.
        hosted = board("greenhouse", "x", "X", [role("T", TODAY, url="https://job-boards.greenhouse.io/x/jobs/1"),
                                                role("U", TODAY, url="https://jobs.eu.lever.co/x/2"),
                                                role("V", TODAY, url="https://jobs.ashbyhq.com/x/3"),
                                                role("W", TODAY, url="https://apply.workable.com/x/j/4")])
        self.assertEqual(a.job_hosts(hosted), set())

    def test_domain_in_job_content(self):
        b = board("lever", "sila", "Sila Services", self.roles(["HVAC Technician"]), text="apply at sila.com/careers")
        self.assertIsNone(a.verify_identity("Sila", "silanano.com", "energy", b))
        b2 = board("greenhouse", "pacificfusion", "PF", self.roles(["Engineer"]), text='<a href="https://www.pacificfusion.com/">us</a>')
        self.assertEqual(a.verify_identity("Pacific Fusion", "pacificfusion.com", "energy", b2), "content_url")

    def test_registry_and_recorded_origins(self):
        b = board("greenhouse", "nerostechnologies", "Neros Technologies", self.roles(["Recruiter"]), origin="registry")
        self.assertEqual(a.verify_identity("Neros Technologies", "neros.tech", "autonomy", b), "registry")
        # A slug read off the company's own post: the name only has to be compatible.
        rec = board("ashby", "valstad", "Valstad", self.roles(["Welder"]), origin="recorded")
        self.assertEqual(a.verify_identity("Valstad Shipworks", None, None, rec), "recorded_name")
        self.assertIsNone(a.verify_identity("Valar Atomics", None, None, rec))
        self.assertIsNone(a.verify_identity("Valstadt", None, None, rec))  # whole words only
        rec.website = "https://valstad.example"
        self.assertIsNone(a.verify_identity("Valstad Shipworks", "valstad.com", None, rec))  # website still decides
        rec2 = board("lever", "neros", "Neros", self.roles(["Recruiter"]), origin="recorded")
        self.assertEqual(a.verify_identity("Neros Technologies", None, None, rec2), "recorded_name")
        # One descriptive word apart is still compatible for a recorded link; two different ones are not the same name.
        rec3 = board("ashby", "skild", "Skild Robotics", self.roles(["Recruiter"]), origin="recorded")
        self.assertEqual(a.verify_identity("Skild AI", None, None, rec3), "recorded_name")
        self.assertIsNone(a.verify_identity("Skild AI", None, None, board("ashby", "skild", "Skild Robotics", rec3.roles)))
        # A pair this collector wrote on an earlier run is not a recorded link, and neither is one that names
        # no source: both verify as a guess does.
        for origin in ("remembered", "unsourced"):
            for b in (rec, rec2, rec3):
                b.origin, b.website = origin, None
            self.assertIsNone(a.verify_identity("Valstad Shipworks", None, None, rec), origin)
            self.assertIsNone(a.verify_identity("Neros Technologies", None, None, rec2), origin)
            self.assertIsNone(a.verify_identity("Skild AI", None, None, rec3), origin)
        anon = board("ashby", "helsing", "282fac21-5bf2-4821-b2e2-c3b80daa375d", self.roles(self.ENG))
        self.assertIsNone(a.verify_identity("Helsing", None, "defense", anon))  # a UUID is not a name

    def test_card_collision_rows_never_verify_by_name(self):
        # Every collision row in the seed file that recorded a board name: the
        # name the board gave must not be accepted for the company that was asked for.
        checked = 0
        for row in SEED["collisions"]:
            if not row.get("board_name"):
                continue
            b = board(row["ats"], row["slug"], row["board_name"], self.roles(["Office Manager", "Account Executive"]))
            self.assertIsNone(a.verify_identity(row["query_company"], None, None, b), row["slug"])
            checked += 1
        self.assertGreaterEqual(checked, 5)


class Slugs(unittest.TestCase):
    def test_candidates(self):
        self.assertEqual(a.slug_candidates("Charge Robotics", "chargerobotics.com"), {
            "ashby": ["chargerobotics", "charge-robotics", "charge"],
            "greenhouse": ["chargerobotics", "charge"],
            "lever": ["chargerobotics", "charge-robotics"],
        })
        c = a.slug_candidates("Neros Technologies", "neros.tech")
        self.assertEqual(c["greenhouse"], ["nerostechnologies", "neros"])  # the registry's Greenhouse slug is first
        self.assertEqual(a.slug_candidates("Dirac, Inc.", None)["ashby"], ["dirac"])
        self.assertEqual(a.slug_candidates("Skild AI", "skild.ai")["ashby"], ["skildai", "skild-ai", "skild"])
        self.assertEqual(a.slug_candidates("Acme", "jobs.acme.co.uk")["greenhouse"], ["acme"])
        # The short form drops one descriptive word (base.loose_name), which is how 'robin-radar' is found.
        self.assertEqual(a.slug_candidates("Robin Radar Systems", "robinradar.com")["ashby"],
                         ["robinradarsystems", "robin-radar-systems", "robinradar", "robin-radar"])

    def test_caps_and_junk(self):
        for provider, slugs in a.slug_candidates("A Very Long Company Name Robotics", "example.io").items():
            self.assertLessEqual(len(slugs), a.MAX_GUESSES[provider])
            self.assertEqual(len(slugs), len(set(slugs)))
        self.assertEqual(a.slug_candidates("", None), {"ashby": [], "greenhouse": [], "lever": []})
        self.assertEqual(a.slug_candidates("X", None)["ashby"], [])  # one character is not a slug


class Registry(unittest.TestCase):
    REG = a.load_registry()

    def test_loads_only_supported_boards(self):
        self.assertEqual(len(self.REG), len(SEED["verified"]))
        self.assertTrue(all(r["ats"] in a.BOARD_PAGE for r in self.REG))
        self.assertFalse(any(r["slug"] in {"radiant", "sila", "figure"} for r in self.REG))  # collisions stay out

    def test_match_by_domain(self):
        rows = a.registry_rows_for("Swarm", "swarm.aero", self.REG)
        self.assertEqual([(r["ats"], r["slug"]) for r in rows], [("ashby", "swarmaero")])

    def test_match_by_name(self):
        rows = a.registry_rows_for("Valar Atomics", "valaratomics.com", self.REG)
        self.assertEqual([(r["ats"], r["slug"]) for r in rows], [("greenhouse", "valaratomics")])
        rows = a.registry_rows_for("Valstad Shipworks", None, self.REG)
        self.assertEqual([(r["ats"], r["slug"]) for r in rows], [("ashby", "valstad")])
        both = {(r["ats"], r["slug"]) for r in a.registry_rows_for("Rainmaker", None, self.REG)}
        self.assertEqual(both, {("ashby", "rainmaker"), ("lever", "make-rain")})

    def test_same_name_other_domain_is_refused(self):
        # A different company called Radiant must not inherit Radiant Industries... nor Regent's board.
        self.assertEqual(a.registry_rows_for("Regent", "regent.example", self.REG), [])
        self.assertEqual(a.registry_rows_for("Picogrid", "notpicogrid.example", self.REG), [])

    def test_target_from_known(self):
        t = a.target_from_known({"name": "Charge Robotics", "domain": "https://www.chargerobotics.com/", "github": None,
                                 "kind": "company", "sector": "robotics",
                                 "ats": [{"provider": "Ashby", "slug": "Charge-Robotics", "source": "hn_hiring"}]}, self.REG)
        self.assertEqual((t.name, t.domain, t.sector), ("Charge Robotics", "chargerobotics.com", "robotics"))
        self.assertEqual(t.candidates, [("ashby", "charge-robotics", "registry")])  # recorded and in the registry
        hn = a.target_from_known({"name": "Valstad Shipworks",
                                  "ats": [{"provider": "ashby", "slug": "valstad", "source": "hn_hiring"},
                                          {"provider": "lever", "slug": "valstad-old", "source": "hn_hiring"}]}, self.REG)
        self.assertEqual(hn.candidates, [("ashby", "valstad", "registry"), ("lever", "valstad-old", "recorded")])
        self.assertIsNone(a.target_from_known({"name": "Jane Doe", "kind": "person"}, self.REG))
        self.assertIsNone(a.target_from_known({"name": " "}, self.REG))
        lever = a.target_from_known({"name": "X Co", "ats": [{"provider": "lever", "slug": "MixedCase", "source": "hn_hiring"},
                                                             {"provider": "workable", "slug": "x", "source": "hn_hiring"}]}, [])
        self.assertEqual(lever.candidates, [("lever", "MixedCase", "recorded")])  # Lever slugs are case-sensitive

    def test_the_source_of_a_pair_decides_how_it_is_checked(self):
        # cli rebuilds "ats" from every stored signal that names a board, this collector's own included, and
        # says on each pair which collector recorded it.
        def origin(source, **entity):
            pair = {"provider": "lever", "slug": "eliyan"}
            if source is not ...:
                pair["source"] = source
            (cand,) = a.target_from_known({"name": "Eliyan", "ats": [pair], **entity}, self.REG).candidates
            self.assertEqual(cand[:2], ("lever", "eliyan"))
            return cand[2]

        self.assertEqual(origin("ats_jobs"), "remembered")
        # hn_hiring reads the board off the company's own hiring post, web_presence off its own homepage.
        self.assertEqual(origin("hn_hiring"), "recorded")
        self.assertEqual(origin("web_presence"), "recorded")
        # The pair's own source decides, not the list of collectors that have a signal on the entity.
        self.assertEqual(origin("ats_jobs", sources=["ats_jobs", "hn_hiring"]), "remembered")
        self.assertEqual(origin("hn_hiring", sources=["ats_jobs", "uspto_trademarks"]), "recorded")
        # No source, or one that is not known to read boards off the company's own pages, proves nothing.
        for source in (..., None, "", "yc_directory", 7, ["hn_hiring"], {"hn_hiring": 1}):
            self.assertEqual(origin(source, sources=["hn_hiring"]), "unsourced", source)
        # Two collectors naming one board: the company's own link counts, in either order.
        for pairs in ([("Complement", "hn_hiring"), ("complement", "ats_jobs")], [("complement", "ats_jobs"), ("Complement", "hn_hiring")]):
            t = a.target_from_known({"name": "Complement AI", "ats": [{"provider": "ashby", "slug": s, "source": src}
                                                                      for s, src in pairs]}, self.REG)
            self.assertEqual(t.candidates, [("ashby", "complement", "recorded")], pairs)
        # A registry row stays the registry's whoever wrote the pair down.
        charge = a.target_from_known({"name": "Charge Robotics", "domain": "chargerobotics.com", "sources": ["ats_jobs"],
                                      "ats": [{"provider": "ashby", "slug": "charge-robotics", "source": "ats_jobs"}]}, self.REG)
        self.assertEqual(charge.candidates, [("ashby", "charge-robotics", "registry")])

    def test_target_carries_the_entitys_own_description(self):
        t = a.target_from_known({"name": "X Co", "one_liner": "  Drones for\n pipelines ", "description": "X Co builds UAVs."}, [])
        self.assertEqual(t.about, "Drones for pipelines X Co builds UAVs.")
        self.assertEqual(a.target_from_known({"name": "X Co", "one_liner": None, "description": ["x"]}, []).about, "")
        self.assertEqual(a.target_from_known({"name": "X Co"}, []).about, "")
        long = a.target_from_known({"name": "X Co", "description": "robot " * 5000}, [])
        self.assertEqual(len(long.about), a.ABOUT_MAX_CHARS)

    def test_target_from_known_survives_odd_entities(self):
        odd = a.target_from_known({"name": "X Co", "domain": 7, "github": "https://github.com/x-co", "kind": "startup",
                                   "sector": ["robotics"],
                                   "ats": [None, "lever/x", {"provider": "lever"}, {"provider": "ashby", "slug": "x/../y"},
                                           {"provider": "greenhouse", "slug": "x?for=y"}, {"provider": "ashby", "slug": "x-co"}]}, [])
        self.assertEqual((odd.domain, odd.github, odd.kind, odd.sector), (None, "x-co", "company", None))
        self.assertEqual(odd.candidates, [("ashby", "x-co", "unsourced")])  # nothing that is not one path segment
        self.assertEqual(a.target_from_known({"name": "X Co", "ats": "ashby:x"}, []).candidates, [])
        self.assertIsNone(a.target_from_known("X Co", []))
        # A repository with no site of its own has nothing a board could be checked against.
        self.assertIsNone(a.target_from_known({"name": "Charge Robotics", "kind": "project", "github": "someone"}, self.REG))
        with_site = a.target_from_known({"name": "charge", "kind": "project", "domain": "chargerobotics.com"}, self.REG)
        self.assertEqual((with_site.kind, with_site.candidates), ("project", [("ashby", "charge-robotics", "registry")]))

    def test_seed_targets_group_boards_and_put_small_first(self):
        targets = a.targets_from_registry(self.REG)
        self.assertTrue(all(t.seed for t in targets))
        rain = next(t for t in targets if t.name == "Rainmaker")
        self.assertEqual({(p, s) for p, s, _ in rain.candidates}, {("ashby", "rainmaker"), ("lever", "make-rain")})
        self.assertEqual(rain.domain, "rainmaker.com")
        names = [t.name for t in targets]
        self.assertLess(names.index("Turion Space"), names.index("Anduril"))


class Signals(unittest.TestCase):
    def ctx(self, **kw):
        return Context(today=TODAY, **kw)

    def check(self, s):
        s.validate()
        self.assertLessEqual(len(s.title), 110, s.title)
        self.assertFalse(s.title.endswith("."))
        self.assertTrue(s.title[0].isupper() or s.title[0].isdigit())
        self.assertGreaterEqual(date.fromisoformat(s.occurred_at), TODAY - timedelta(days=120))
        self.assertLessEqual(date.fromisoformat(s.occurred_at), TODAY)

    def test_velocity_signal_for_a_known_entity(self):
        target = a.Target(name="Charge Robotics", domain="chargerobotics.com", github="charge-robotics", sector="robotics")
        (s,) = a.build_signals(target, [charge_board()], self.ctx())
        self.check(s)
        self.assertEqual((s.source, s.family, s.kind), ("ats_jobs", "hiring", "hiring_velocity"))
        self.assertEqual(s.title, "11 of 14 open roles posted in the last 14 days, including Head of Manufacturing")
        # The known entity's identifiers, not the board's.
        self.assertEqual((s.entity.name, s.entity.domain, s.entity.github), ("Charge Robotics", "chargerobotics.com", "charge-robotics"))
        self.assertEqual(s.occurred_at, "2026-09-28")
        self.assertEqual(s.url, "https://jobs.ashbyhq.com/charge-robotics")
        self.assertEqual((s.value, s.unit), (11, "roles posted in 30 days"))
        m = s.metrics
        self.assertEqual((m["open_roles"], m["new_14d"], m["new_30d"], m["prev_30d"]), (14, 11, 11, 0))
        self.assertEqual((m["ats_provider"], m["ats_slug"], m["identity_basis"]), ("ashby", "charge-robotics", "website"))
        self.assertEqual(m["executive_roles_new_30d"], ["Head of Manufacturing", "Head of Engineering"])
        self.assertEqual(m["bulk_publish_day"], "2026-09-28")
        self.assertEqual(m["function_mix"]["manufacturing"], 6)
        self.assertEqual(s.series[-1], {"t": "2026-10-01", "v": 11, "s": s.strength})
        self.assertIn("Head of Manufacturing", s.text)
        self.assertEqual(s.people, [])

    def test_quiet_board(self):
        roles = [role("Mechanical Engineer", TODAY - timedelta(days=45)), role("Controls Engineer", TODAY - timedelta(days=80))]
        (s,) = a.build_signals(a.Target(name="Quiet Co"), [board("lever", "quiet", "Quiet Co", roles)], self.ctx())
        self.check(s)
        self.assertEqual(s.title, "2 open roles, none posted in the last 30 days")
        self.assertEqual(s.strength, a.STRENGTH_FLOOR)
        self.assertEqual(s.occurred_at, (TODAY - timedelta(days=45)).isoformat())

    def test_nothing_newer_than_the_lookback_is_skipped(self):
        roles = [role("Mechanical Engineer", TODAY - timedelta(days=150)), role("Controls Engineer", TODAY - timedelta(days=300))]
        self.assertEqual(a.build_signals(a.Target(name="Old Co"), [board("lever", "old", "Old Co", roles)], self.ctx()), [])
        self.assertEqual(a.build_signals(a.Target(name="Empty"), [board("ashby", "e", "Empty", [])], self.ctx()), [])
        self.assertEqual(a.build_signals(a.Target(name="None"), [], self.ctx()), [])

    def test_new_board_is_a_birth_not_velocity(self):
        roles = [role(t, TODAY) for t in ("Director of Avionics", "GNC Engineer", "Flight Software Engineer",
                                          "Electric Propulsion Engineer", "Head of Manufacturing")] \
            + [role(f"Propulsion Technician {i}", TODAY) for i in range(12)]
        (s,) = a.build_signals(a.Target(name="General Galactic"), [board("greenhouse", "generalgalactic", "General Galactic", roles)], self.ctx())
        self.check(s)
        self.assertEqual(s.kind, "ats_board_created")
        self.assertEqual(s.title, "All 17 open roles first published on 2026-10-01: a new or republished job board")
        self.assertEqual((s.value, s.unit, s.occurred_at), (17, "roles", "2026-10-01"))
        self.assertEqual(s.series, [])  # a point event carries no per-week strength
        self.assertEqual(s.metrics["open_roles"], 17)
        self.assertEqual(s.metrics["executive_roles"], ["Head of Manufacturing"])
        self.assertTrue(0.35 <= s.strength <= 0.70)

    def test_migration_uses_the_old_board_for_velocity(self):
        # Rainmaker: Lever shows real dates, Ashby shows every role on the day of the move.
        lever = board("lever", "make-rain", "Rainmaker Technology Corporation", a.parse_lever(LEVER))
        ashby = board("ashby", "rainmaker", "Rainmaker Technology Corporation",
                      [role(r.title, TODAY) for r in a.parse_lever(LEVER)], "https://www.rainmaker.com/")
        sigs = a.build_signals(a.Target(name="Rainmaker", domain="rainmaker.com"), [ashby, lever], self.ctx())
        self.assertEqual(sorted(s.kind for s in sigs), ["ats_board_created", "hiring_velocity"])
        for s in sigs:
            self.check(s)
            self.assertEqual((s.entity.name, s.entity.domain), ("Rainmaker", "rainmaker.com"))
        move = next(s for s in sigs if s.kind == "ats_board_created")
        vel = next(s for s in sigs if s.kind == "hiring_velocity")
        self.assertEqual(move.title, "All 5 open roles republished on 2026-10-01 in a move from Lever to Ashby, not new hiring")
        self.assertEqual(move.strength, a.MIGRATION_STRENGTH)
        self.assertEqual(move.metrics["ats_migration_from"], "lever:make-rain")
        self.assertEqual((vel.metrics["ats_provider"], vel.metrics["new_30d"], vel.metrics["prev_30d"]), ("lever", 3, 1))
        self.assertEqual(vel.metrics["other_boards"], ["ashby:rainmaker"])
        self.assertEqual(vel.url, "https://jobs.lever.co/make-rain")

    def test_stale_second_board_is_ignored(self):
        fresh = charge_board()
        stale = board("greenhouse", "chargeold", "Charge Robotics", [role(f"Old {i}", date(2025, 2, 6)) for i in range(25)])
        sigs = a.build_signals(a.Target(name="Charge Robotics", domain="chargerobotics.com"), [stale, fresh], self.ctx())
        self.assertEqual([(s.kind, s.metrics["ats_slug"], s.metrics["open_roles"]) for s in sigs],
                         [("hiring_velocity", "charge-robotics", 14)])

    def test_first_batch_then_organic_roles(self):
        d0 = date(2026, 8, 31)
        roles = [role(f"Welder {i}", d0) for i in range(6)] + [role("Controls Technician", date(2026, 9, 10)),
                                                               role("Head of Manufacturing", date(2026, 9, 22))]
        sigs = a.build_signals(a.Target(name="Valstad Shipworks"), [board("ashby", "valstad", "Valstad", roles, "https://valstad.com/", origin="registry")], self.ctx())
        birth = next(s for s in sigs if s.kind == "ats_board_created")
        vel = next(s for s in sigs if s.kind == "hiring_velocity")
        for s in sigs:
            self.check(s)
            self.assertIsNone(s.entity.domain)  # verified by name only: the site is a link, not a merge key
            self.assertEqual(s.entity.links["website"], "https://valstad.com")
        self.assertEqual(birth.title, "6 of 8 open roles first published on 2026-08-31: a new or republished job board")
        self.assertEqual(birth.occurred_at, "2026-08-31")
        # 2026-09-10 is 21 days back, so only the 2026-09-22 role is inside 14 days.
        self.assertEqual(vel.title, "1 of 8 open roles posted in the last 14 days, including Head of Manufacturing")
        self.assertEqual(vel.metrics["new_14d"], 1)
        self.assertEqual((vel.metrics["new_30d"], vel.metrics["excluded_first_batch_roles"]), (2, 6))
        self.assertIn("prev_30d_basis", vel.metrics)
        self.assertEqual(vel.series[0]["t"], "2026-09-10")  # nothing is claimed before the first organic role

    def test_first_batch_inside_the_window_is_named_as_left_out(self):
        # Mach Industries, 2026-10-01: 94 roles from 08-31 to 09-02, then 40. 09-02 is inside 30 days, so 76
        # roles are literally dated in the window; "40 posted in the last 30 days" alone would be false.
        batch = ([role(f"Technician {i}", date(2026, 8, 31)) for i in range(40)]
                 + [role(f"Machinist {i}", date(2026, 9, 1)) for i in range(18)]
                 + [role(f"Welder {i}", date(2026, 9, 2)) for i in range(36)])
        later = [role(f"GNC Engineer {i}", date(2026, 9, 5) + timedelta(days=i % 20)) for i in range(40)]
        mach = board("greenhouse", "machindustries", "Mach Industries", batch + later)
        sigs = a.build_signals(a.Target(name="Mach Industries"), [mach], self.ctx())
        birth = next(s for s in sigs if s.kind == "ats_board_created")
        vel = next(s for s in sigs if s.kind == "hiring_velocity")
        for s in sigs:
            self.check(s)
        self.assertEqual(birth.title, "94 of 134 open roles first published from 2026-08-31 to 2026-09-02: a new or republished job board")
        self.assertEqual(vel.title, "40 of 134 open roles posted in the last 30 days, not counting a first batch of 94 starting 2026-08-31")
        self.assertLessEqual(birth.strength, 0.35)  # a 134-role board is not a first board
        # An executive opening inside 14 days does not switch to the 14-day sentence while the batch is inside it.
        fresh = [role(f"Welder {i}", TODAY - timedelta(days=10)) for i in range(8)]
        after = [role("Head of Manufacturing", TODAY - timedelta(days=2)), role("Machinist", TODAY - timedelta(days=3))]
        (b2, v2) = sorted(a.build_signals(a.Target(name="Newco"), [board("ashby", "newco", "Newco", fresh + after)], self.ctx()),
                          key=lambda s: s.kind)
        self.assertEqual(v2.title, f"2 of 10 open roles posted in the last 30 days, not counting a first batch of 8 on {(TODAY - timedelta(days=10)).isoformat()}")
        self.assertEqual(v2.metrics["executive_roles_new_30d"], ["Head of Manufacturing"])

    def test_second_board_with_other_roles_is_not_called_a_move(self):
        lever = board("lever", "make-rain", "Rainmaker Technology Corporation", a.parse_lever(LEVER))
        other = board("ashby", "rainmaker", "Rainmaker Technology Corporation",
                      [role(t, TODAY) for t in ("Cloud Seeding Pilot", "Radar Meteorologist", "Field Technician", "Program Manager")],
                      "https://www.rainmaker.com/")
        sigs = a.build_signals(a.Target(name="Rainmaker", domain="rainmaker.com"), [other, lever], self.ctx())
        second = next(s for s in sigs if s.kind == "ats_board_created")
        self.check(second)
        self.assertEqual(second.title, "All 4 open roles first published on 2026-10-01 on a second board beside its Lever one")
        self.assertEqual(second.strength, a.SECOND_BOARD_STRENGTH)
        self.assertNotIn("ats_migration_from", second.metrics)
        self.assertEqual((second.metrics["older_board"], second.metrics["first_batch_titles_on_older_board"]), ("lever:make-rain", 0))

    def test_lever_dates_are_flagged_as_creation_dates(self):
        (lev,) = a.build_signals(a.Target(name="CX2"), [board("lever", "cx2", "CX2", a.parse_lever(LEVER))], self.ctx())
        self.assertIn("Lever dates are when a posting was created", lev.metrics["dates_basis"])
        (ash,) = a.build_signals(a.Target(name="Charge Robotics"), [charge_board()], self.ctx())
        self.assertNotIn("Lever", ash.metrics["dates_basis"])

    def test_title_never_ends_in_a_period(self):
        roles = [role("VP, Manufacturing Eng.", TODAY), role("Welder", TODAY - timedelta(days=100))]
        title = a.velocity_title(a.board_stats(roles, TODAY))
        self.assertEqual(title, "1 of 2 open roles posted in the last 14 days, including VP, Manufacturing Eng")

    def test_a_date_one_day_ahead_of_the_utc_run_date_is_today(self):
        # The run date is the UTC date; a Greenhouse board east of UTC writes its own, later, local date.
        roles = [role("Perception Engineer", TODAY + timedelta(days=1)), role("UAS Pilot", TODAY - timedelta(days=50)),
                 role("Field Engineer", TODAY - timedelta(days=90))]
        (s,) = a.build_signals(a.Target(name="9 Mothers"), [board("ashby", "9-mothers", "9 Mothers", roles)], self.ctx())
        self.check(s)
        self.assertEqual(s.occurred_at, "2026-10-01")
        self.assertEqual(s.metrics["new_14d"], 1)

    def test_seed_mode_names_the_company_from_the_board(self):
        t = a.Target(name="Charge", seed=True, registry_names=["Charge Robotics"])
        (s,) = a.build_signals(t, [charge_board()], self.ctx())
        self.assertEqual((s.entity.name, s.entity.domain, s.entity.kind), ("Charge Robotics", "chargerobotics.com", "company"))
        # The board's own name, without the legal form that would stop it matching and reads as noise.
        (s,) = a.build_signals(t, [charge_board(name="Charge Robotics, Inc.")], self.ctx())
        self.assertEqual(s.entity.name, "Charge Robotics")
        # base.display_name does it: "Company" is part of a name, and the company's own capitals are kept.
        for raw, want in (("Apex Technology, Inc.", "Apex Technology"), ("Reliable Robotics Corporation", "Reliable Robotics"),
                          ("The Nuclear Company", "The Nuclear Company"), ("Pickle Robot Company", "Pickle Robot Company"),
                          ("REGENT Craft Inc.", "REGENT Craft"), ("NODA AI", "NODA AI"), ("1X", "1X")):
            (s,) = a.build_signals(t, [charge_board(name=raw)], self.ctx())
            self.assertEqual(s.entity.name, want, raw)
        # A board that gives an id for a name leaves the registry's name in place.
        (s,) = a.build_signals(t, [charge_board(name="282fac21-5bf2-4821-b2e2-c3b80daa375d")], self.ctx())
        self.assertEqual(s.entity.name, "Charge")

    def test_title_shapes(self):
        st = a.board_stats(a.parse_greenhouse(GREENHOUSE)[1], TODAY)
        self.assertEqual(a.velocity_title(st), "32 of 94 open roles posted in the last 30 days, against 33 in the 30 days before")
        one = a.board_stats([role("Avionics Engineer", TODAY)], TODAY)
        self.assertEqual(a.velocity_title(one), "1 of 1 open role posted in the last 30 days, against 0 in the 30 days before")
        long_head = [role("Head of Something With A Remarkably Long And Winding Title, Global Programs", TODAY),
                     role("Mechanical Engineer", TODAY - timedelta(days=100))]
        title = a.velocity_title(a.board_stats(long_head, TODAY))
        self.assertEqual(title, "1 of 2 open roles posted in the last 30 days, against 0 in the 30 days before")


class FakeHttp:
    """Serves the three fixtures. Everything else is a 404. Records what was asked."""

    ORGS = {
        "charge-robotics": {"name": "Charge Robotics", "publicWebsite": "https://chargerobotics.com/"},
        # card 2.2: a UK cloud company answers on the slug a nuclear startup's name suggests
        "radiant": {"name": "Radiant", "publicWebsite": "https://radiant.co/"},
    }

    def __init__(self):
        self.calls: list[str] = []

    def get_json(self, url, **kw):
        if kw.get("params"):
            self.assert_lever_params(kw["params"])
        self.calls.append(url)
        if url == a.GREENHOUSE_API.format(slug="valaratomics"):
            return GREENHOUSE
        if url in (a.ASHBY_API.format(slug="charge-robotics"), a.ASHBY_API.format(slug="radiant")):
            return ASHBY
        if url == a.LEVER_API.format(slug="cx2"):
            return LEVER
        raise http.HttpError(404, url)

    @staticmethod
    def assert_lever_params(params):
        assert params == {"mode": "json"}, params

    def post_json(self, url, payload, **kw):
        assert url == a.ASHBY_GRAPHQL, url
        slug = payload["variables"]["organizationHostedJobsPageName"]
        self.calls.append(f"graphql:{slug}")
        return {"data": {"organization": self.ORGS.get(slug)}}

    def get(self, url, **kw):
        self.calls.append(url)
        if url == "https://jobs.lever.co/cx2":
            return "<html><head><title>CX2</title></head><body></body></html>"
        raise http.HttpError(404, url)


MINI_REGISTRY = [
    {"query_company": "Valar Atomics", "ats": "greenhouse", "slug": "valaratomics", "board_name": "Valar Atomics",
     "website": None, "open_roles": 94},
    {"query_company": "Charge Robotics", "ats": "ashby", "slug": "charge-robotics", "board_name": "Charge Robotics",
     "website": "https://chargerobotics.com/", "open_roles": 14},
    {"query_company": "CX2", "ats": "lever", "slug": "cx2", "board_name": "CX2", "website": None, "open_roles": 5},
]


class EndToEnd(unittest.TestCase):
    def run_collect(self, known, registry=MINI_REGISTRY, limit=None, today=TODAY):
        fake = FakeHttp()
        ctx = Context(today=today, limit=limit, known=known)
        with mock.patch.object(http, "get_json", fake.get_json), mock.patch.object(http, "post_json", fake.post_json), \
                mock.patch.object(http, "get", fake.get), \
                mock.patch.object(http, "request", side_effect=AssertionError("network reached")), \
                mock.patch.object(a, "load_registry", return_value=registry), \
                mock.patch("sys.stderr"):
            signals = list(a.collect(ctx))
        for s in signals:
            s.validate()
            self.assertLessEqual(len(s.title), 110)
        return signals, ctx, fake

    KNOWN = [
        {"slug": "charge-robotics", "name": "Charge Robotics", "kind": "company", "domain": "chargerobotics.com",
         "github": None, "sector": "robotics", "fit": 0.8},
        {"slug": "valar-atomics", "name": "Valar Atomics", "kind": "company", "domain": "valaratomics.com",
         "github": None, "sector": "energy", "fit": 0.8},
        {"slug": "cx2", "name": "CX2", "kind": "company", "domain": None, "github": "cx2-labs", "sector": "defense",
         "fit": 0.8, "ats": [{"provider": "lever", "slug": "cx2", "source": "hn_hiring"}]},
        {"slug": "radiant", "name": "Radiant", "kind": "company", "domain": "radiantnuclear.com", "github": None,
         "sector": "energy", "fit": 0.8},
        {"slug": "no-board", "name": "Salem Robotics", "kind": "company", "domain": None, "github": None,
         "sector": "robotics", "fit": 0.8},
        {"slug": "jane", "name": "Jane Doe", "kind": "person", "domain": None, "github": "jane"},
    ]

    def test_known_entities(self):
        # Only Charge is in the registry: Valar must be found by guessing, CX2 through its recorded slug.
        signals, ctx, fake = self.run_collect(self.KNOWN, registry=MINI_REGISTRY[1:2])
        by_name = {s.entity.name: s for s in signals}
        self.assertEqual(set(by_name), {"Charge Robotics", "Valar Atomics", "CX2"})
        self.assertTrue(all(s.kind == "hiring_velocity" for s in signals))
        self.assertEqual([s.strength for s in signals], sorted((s.strength for s in signals), reverse=True))
        # Each hint reuses the known entity's own identifiers.
        self.assertEqual(by_name["Charge Robotics"].entity.domain, "chargerobotics.com")
        self.assertEqual(by_name["Valar Atomics"].entity.domain, "valaratomics.com")
        self.assertEqual((by_name["CX2"].entity.domain, by_name["CX2"].entity.github), (None, "cx2-labs"))
        self.assertEqual(by_name["Charge Robotics"].metrics["identity_basis"], "website")
        self.assertEqual(by_name["Valar Atomics"].metrics["identity_basis"], "name_and_thesis")
        self.assertEqual(by_name["CX2"].metrics["identity_basis"], "recorded_name")
        self.assertEqual(by_name["Valar Atomics"].url, "https://job-boards.greenhouse.io/embed/job_board?for=valaratomics")
        self.assertEqual(by_name["Valar Atomics"].metrics["open_roles"], 94)
        self.assertEqual(by_name["CX2"].title, "3 of 5 open roles posted in the last 30 days, against 1 in the 30 days before")
        # Radiant's obvious slug exists and belongs to someone else: asked, rejected, nothing emitted.
        self.assertIn("graphql:radiant", fake.calls)
        self.assertNotIn("Radiant", by_name)
        self.assertEqual(ctx.warnings, [])

    def test_guesses_stop_at_the_first_live_verified_board(self):
        _, _, fake = self.run_collect([self.KNOWN[0]])
        # Existence first (the posting API), then identity, then nothing more.
        self.assertEqual(fake.calls, [a.ASHBY_API.format(slug="charge-robotics"), "graphql:charge-robotics"])

    def test_limit_caps_entities_looked_up(self):
        signals, _, fake = self.run_collect(self.KNOWN, limit=1)
        self.assertEqual([s.entity.name for s in signals], ["Charge Robotics"])
        self.assertFalse(any("valaratomics" in c or "cx2" in c for c in fake.calls))

    def test_seed_registry_only_when_nothing_is_known(self):
        signals, ctx, _ = self.run_collect([])
        self.assertEqual({s.entity.name for s in signals}, {"Charge Robotics", "Valar Atomics", "CX2"})
        self.assertEqual({s.entity.name: s.entity.domain for s in signals},
                         {"Charge Robotics": "chargerobotics.com", "Valar Atomics": None, "CX2": None})
        self.assertEqual(ctx.warnings, [])
        one, _, _ = self.run_collect([], limit=1)
        self.assertEqual([s.entity.name for s in one], ["CX2"])  # smallest board first
        # With known entities the registry's other companies are left alone.
        known_only, _, _ = self.run_collect([self.KNOWN[0]])
        self.assertEqual([s.entity.name for s in known_only], ["Charge Robotics"])

    def test_seed_row_that_no_longer_matches_its_board_is_dropped(self):
        changed = [dict(MINI_REGISTRY[0], query_company="Somebody Else", board_name="Somebody Else")]
        signals, _, _ = self.run_collect([], registry=changed)
        self.assertEqual(signals, [])

    def test_old_boards_emit_nothing(self):
        signals, _, _ = self.run_collect(self.KNOWN, today=date(2027, 6, 1))
        self.assertEqual(signals, [])

    def test_identity_is_only_asked_for_boards_that_exist(self):
        _, _, fake = self.run_collect([self.KNOWN[4]])  # Salem Robotics: every guess is a 404
        self.assertTrue(fake.calls)
        self.assertFalse(any(c.startswith("graphql:") for c in fake.calls))

    def test_board_without_an_identity_answer_is_not_emitted(self):
        with mock.patch.dict(FakeHttp.ORGS, {"charge-robotics": None}):
            signals, _, _ = self.run_collect([self.KNOWN[0]], registry=[])
        self.assertEqual(signals, [])

    def test_lookup_budget(self):
        with mock.patch.object(a, "MAX_BOARD_LOOKUPS", 2):
            signals, ctx, fake = self.run_collect(self.KNOWN)
        self.assertLessEqual(len(fake.calls), 2)
        self.assertTrue(any("MAX_BOARD_LOOKUPS" in w for w in ctx.warnings))
        self.assertLessEqual(len(signals), 1)

    def test_one_board_is_attributed_once(self):
        twins = [dict(self.KNOWN[2]), dict(self.KNOWN[2], slug="cx2-b", name="CX2 Inc", github=None)]
        signals, ctx, _ = self.run_collect(twins)
        self.assertEqual([s.entity.name for s in signals], ["CX2"])
        self.assertTrue(any("already attributed" in w for w in ctx.warnings))

    def test_blank_hosted_page_gives_way_to_the_companys_own_careers_page(self):
        # Form Energy, 2026-10-01: 203 roles in the API, a blank hosted page, its jobs listed on its own site.
        hosted = "https://jobs.ashbyhq.com/charge-robotics"
        own = {"name": "Charge Robotics", "publicWebsite": "https://chargerobotics.com/",
               "customJobsPageUrl": "https://www.chargerobotics.com/careers"}
        blank = '<script>window.__appData = {"organization":null,"jobBoard":null};\n</script>'
        listed = '<script>window.__appData = {"organization":{"name":"Charge Robotics"},"jobBoard":{}};\n</script>'
        self.assertTrue(a.hosted_page_is_empty(blank))
        self.assertFalse(a.hosted_page_is_empty(listed))

        def run(org, page):
            def get(self_, url, **kw):
                self_.calls.append(url)
                if url == hosted and page is not None:
                    return page
                raise http.HttpError(404, url)
            with mock.patch.dict(FakeHttp.ORGS, {"charge-robotics": org}), mock.patch.object(FakeHttp, "get", get):
                (sig,), _, fake = self.run_collect([self.KNOWN[0]])
            return sig, fake

        sig, fake = run(own, blank)
        self.assertEqual(sig.url, "https://www.chargerobotics.com/careers")
        self.assertEqual(sig.entity.links["jobs"], "https://www.chargerobotics.com/careers")
        self.assertEqual(run(own, None)[0].url, "https://www.chargerobotics.com/careers")  # hosted page is a 404
        # 17 of 66 boards name a careers page and still serve a working hosted page: that one stays the evidence.
        self.assertEqual(run(own, listed)[0].url, hosted)
        # A careers page that is not on the organisation's own site is never used, and nothing is fetched to decide.
        sig, fake = run(dict(own, customJobsPageUrl="https://example-jobs.io/charge"), blank)
        self.assertEqual(sig.url, hosted)
        self.assertNotIn(hosted, fake.calls)
        sig, fake = run(dict(own, customJobsPageUrl="javascript:alert(1)"), blank)
        self.assertEqual(sig.url, hosted)

    def test_a_board_this_collector_attached_before_is_tried_first_and_checked_again(self):
        valar = dict(self.KNOWN[1], domain=None, sources=["ats_jobs", "nrc_adams"],
                     ats=[{"provider": "greenhouse", "slug": "valaratomics", "source": "ats_jobs"}],
                     one_liner="High-temperature gas reactors for industrial heat and hydrogen")
        (sig,), ctx, fake = self.run_collect([valar], registry=[])
        self.assertEqual(fake.calls[0], a.GREENHOUSE_API.format(slug="valaratomics"))  # before any guess
        self.assertFalse(any("ashby" in c or "lever" in c for c in fake.calls))  # and no guess after it
        self.assertEqual(sig.metrics["identity_basis"], "name_and_thesis")  # not "recorded_name"
        self.assertEqual(ctx.warnings, [])
        # If what the company says it does no longer fits the board, last run's match is not carried over.
        other = dict(valar, one_liner="Counter-drone radar and interceptor drones for air defense.")
        signals, _, _ = self.run_collect([other], registry=[])
        self.assertEqual(signals, [])
        # A pair with no source is no better, whatever other collectors have a signal on the entity.
        unsourced = dict(other, sources=["ats_jobs", "hn_hiring"], ats=[{"provider": "greenhouse", "slug": "valaratomics"}])
        self.assertEqual(self.run_collect([unsourced], registry=[])[0], [])
        # The same pair read off the company's own hiring post needs only the name.
        posted = dict(other, ats=[{"provider": "greenhouse", "slug": "valaratomics", "source": "hn_hiring"}])
        (sig,), _, _ = self.run_collect([posted], registry=[])
        self.assertEqual(sig.metrics["identity_basis"], "recorded_name")

    def test_a_registry_greenhouse_board_still_carries_the_companys_own_words(self):
        # The light listing has titles only. One job body gives the signal text the company's account of itself.
        prefix = a.GREENHOUSE_JOB_API.format(slug="valaratomics", job_id="")
        plain = FakeHttp.get_json

        def get_json(self_, url, **kw):
            if url.startswith(prefix):
                self_.calls.append(url)
                return next(j for j in GREENHOUSE["jobs"] if url == prefix + str(j["id"]))
            return plain(self_, url, **kw)

        with mock.patch.object(FakeHttp, "get_json", get_json):
            (sig,), _, fake = self.run_collect([self.KNOWN[1]], registry=MINI_REGISTRY[:1])
            self.assertEqual(fake.calls, [a.GREENHOUSE_API.format(slug="valaratomics"), prefix + str(GREENHOUSE["jobs"][0]["id"])])
            self.assertEqual(sig.metrics["identity_basis"], "registry")
            self.assertTrue(sig.text.startswith("Open roles: Automation and Controls Engineer; "))
            self.assertIn("high-temperature nuclear power", sig.text)
            # A guess reads two, to look for the entity's own domain in them.
            (sig,), _, fake = self.run_collect([self.KNOWN[1]], registry=[])
            self.assertEqual(sum(1 for c in fake.calls if c.startswith(prefix)), 2)
            self.assertEqual(sig.metrics["identity_basis"], "name_and_thesis")

    def test_a_gone_slug_is_no_board_and_no_warning(self):
        for status in (404, 410):
            with mock.patch.object(http, "get_json", side_effect=http.HttpError(status, "u")):
                self.assertIsNone(a.fetch_greenhouse("x", a._Budget(9)))
                self.assertIsNone(a.fetch_ashby("x", a._Budget(9)))
                self.assertIsNone(a.fetch_lever("x", a._Budget(9), want_name=True))
        with mock.patch.object(http, "get_json", side_effect=http.HttpError(500, "u")):
            for fetch in (a.fetch_greenhouse, a.fetch_ashby):
                with self.assertRaises(http.HttpError):
                    fetch("x", a._Budget(9))

    def test_two_entities_with_one_name_do_not_both_get_the_board(self):
        twins = [dict(self.KNOWN[0]), dict(self.KNOWN[0], slug="charge-robotics-2", domain=None)]
        signals, ctx, _ = self.run_collect(twins)
        self.assertEqual([(s.entity.name, s.entity.domain) for s in signals], [("Charge Robotics", "chargerobotics.com")])
        self.assertTrue(any("already attributed" in w for w in ctx.warnings))

    def test_a_project_never_inherits_a_company_board(self):
        repo = [{"slug": "cx2", "name": "CX2", "kind": "project", "domain": None, "github": "someone", "sector": "defense",
                 "ats": [{"provider": "lever", "slug": "cx2", "source": "hn_hiring"}]},
                {"slug": "valar", "name": "Valar Atomics", "kind": "project", "domain": None, "github": "fan"}]
        signals, ctx, fake = self.run_collect(repo)
        self.assertEqual(signals, [])
        self.assertEqual(fake.calls, [])  # nothing to check a board against, so nothing is asked

    def test_seed_mode_applies_the_thesis_filter(self):
        with mock.patch.object(a, "_on_thesis", side_effect=lambda s: s.entity.name != "CX2"):
            signals, ctx, _ = self.run_collect([])
        self.assertEqual({s.entity.name for s in signals}, {"Charge Robotics", "Valar Atomics"})
        # Known entities were filtered upstream: nothing is dropped for them here.
        with mock.patch.object(a, "_on_thesis", return_value=False):
            known, _, _ = self.run_collect([self.KNOWN[0]])
        self.assertEqual([s.entity.name for s in known], ["Charge Robotics"])

    def test_a_malformed_known_entity_does_not_lose_the_run(self):
        signals, ctx, _ = self.run_collect([None, "Charge Robotics", {"name": None}, {"name": "Charge Robotics", "domain": ["x"],
                                            "ats": "ashby:charge-robotics"}, self.KNOWN[0]])
        self.assertEqual([(s.entity.name, s.entity.domain) for s in signals], [("Charge Robotics", None)])

    def test_a_failing_board_does_not_lose_the_run(self):
        fake = FakeHttp()

        def flaky(url, **kw):
            if "valaratomics" in url:
                raise TimeoutError("stalled")
            return fake.get_json(url, **kw)

        ctx = Context(today=TODAY, known=self.KNOWN[:2])
        with mock.patch.object(http, "get_json", flaky), mock.patch.object(http, "post_json", fake.post_json), \
                mock.patch.object(http, "get", fake.get), mock.patch.object(a, "load_registry", return_value=MINI_REGISTRY), \
                mock.patch("sys.stderr"):
            signals = list(a.collect(ctx))
        self.assertEqual([s.entity.name for s in signals], ["Charge Robotics"])
        self.assertTrue(any("Valar Atomics" in w and "TimeoutError" in w for w in ctx.warnings))


if __name__ == "__main__":
    unittest.main()
