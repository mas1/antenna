"""Offline tests for the github_velocity collector.

Everything runs against the saved GitHub responses in fixtures/github/. No
network: the test that drives collect() swaps the module's two network entry
points (http.post_json for GraphQL, http.get_json for REST) for fakes that
serve those fixtures.
"""

from __future__ import annotations

import dataclasses
import json
import re
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from antenna import http
from antenna.collectors import github_velocity as gv
from antenna.collectors.base import Context
from antenna.db import fingerprint

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "github"
BUNDLE = json.loads((FIXTURES / "rest_repo_bundle.json").read_text())["repos"]
SEARCH = json.loads((FIXTURES / "graphql_search_with_owner.json").read_text())["data"]
NODES = {n["nameWithOwner"]: n for alias in ("a", "b") for n in SEARCH[alias]["nodes"]}

K_REPO = "GET /repos/{owner}/{repo}"
K_ORG = "GET /orgs/{org}"
K_USER = "GET /users/{username}"
K_HISTORY = "GET /repos/{owner}/{repo}/stargazers/history?per_page=30"
K_EVENTS = "GET /repos/{owner}/{repo}/events?per_page=30"
K_CONTRIB = "GET /repos/{owner}/{repo}/contributors?per_page=10"

QYM = BUNDLE["QymIs-Tech/QymCAD"]
MJ = BUNDLE["kevinzakka/mjbatch"]
TODAY = date(2026, 10, 1)  # the day the fixtures were captured


def graphql_node_from_rest(rest: dict) -> dict:
    """The REST repo object from the fixture, reshaped as a GraphQL search node."""
    return {
        "nameWithOwner": rest["full_name"],
        "url": rest["html_url"],
        "description": rest["description"],
        "stargazerCount": rest["stargazers_count"],
        "forkCount": rest["forks_count"],
        "createdAt": rest["created_at"],
        "pushedAt": rest["pushed_at"],
        "homepageUrl": rest["homepage"],
        "isFork": rest["fork"],
        "isArchived": rest["archived"],
        "repositoryTopics": {"nodes": [{"topic": {"name": t}} for t in rest["topics"]]},
        "owner": {"__typename": rest["owner"]["type"], "login": rest["owner"]["login"]},
    }


def graphql_owner_from_rest(rest: dict) -> dict:
    """The REST org or user object from the fixture, reshaped as a GraphQL owner node."""
    node = {
        "__typename": rest["type"],
        "login": rest["login"],
        "name": rest.get("name"),
        "websiteUrl": rest.get("blog"),
        "createdAt": rest["created_at"],
        "location": rest.get("location"),
        "twitterUsername": rest.get("twitter_username"),
        "repositories": {"totalCount": rest.get("public_repos")},
    }
    if rest["type"] == "Organization":
        node.update(description=rest.get("description"), email=rest.get("email"),
                    isVerified=rest.get("is_verified"))
    else:
        node.update(company=rest.get("company"), bio=rest.get("bio"),
                    followers={"totalCount": rest.get("followers")})
    return node


QYM_REPO = gv.parse_repo_node(graphql_node_from_rest(QYM[K_REPO]))
MJ_REPO = gv.parse_repo_node(graphql_node_from_rest(MJ[K_REPO]))
QYM_OWNER = gv.parse_owner(graphql_owner_from_rest(QYM[K_ORG]))
MJ_OWNER = gv.parse_owner(graphql_owner_from_rest(MJ[K_USER]))


def repo(full_name: str, description: str = "", topics: tuple[str, ...] = (), *,
         owner_type: str = "User", fork: bool = False) -> gv.Repo:
    return gv.parse_repo_node({
        "nameWithOwner": full_name, "description": description, "stargazerCount": 100,
        "forkCount": 5, "createdAt": "2026-08-01T00:00:00Z", "pushedAt": "2026-09-30T00:00:00Z",
        "isFork": fork, "isArchived": False,
        "repositoryTopics": {"nodes": [{"topic": {"name": t}} for t in topics]},
        "owner": {"__typename": owner_type, "login": full_name.split("/")[0]},
    })


class StarHistory(unittest.TestCase):
    def test_daily_counts_flattens_weeks_from_sunday(self):
        daily = gv.daily_counts(QYM[K_HISTORY])
        self.assertEqual(daily[date(2026, 9, 27)], 0)   # Sunday of the newest week
        self.assertEqual(daily[date(2026, 9, 30)], 9)
        self.assertEqual(daily[date(2026, 10, 1)], 132)  # Thursday
        self.assertEqual(sum(daily.values()), QYM[K_REPO]["stargazers_count"])  # 182

    def test_history_sums_to_star_count_for_a_young_repo(self):
        self.assertEqual(sum(gv.daily_counts(MJ[K_HISTORY]).values()), 581)

    def test_garbage_is_ignored(self):
        self.assertEqual(gv.daily_counts(None), {})
        self.assertEqual(gv.daily_counts({"message": "Not Found"}), {})
        self.assertEqual(gv.daily_counts([{"week": "x"}, {"days": [1]}, None]), {})

    def test_velocity_matches_the_source_card(self):
        # docs/sources/github.md: "QymIs-Tech/QymCAD 144 vs 8 -> 16.1x"
        v = gv.velocity(gv.daily_counts(QYM[K_HISTORY]), TODAY, 182)
        self.assertEqual((v["stars_7d"], v["stars_prev_7d"]), (144, 8))
        self.assertEqual((v["stars_30d"], v["stars_prev_30d"]), (156, 26))
        self.assertEqual(v["accel_7d"], 16.11)
        self.assertEqual(v["stars_total"], 182)
        self.assertEqual((v["peak_day"], v["peak_date"]), (132, date(2026, 10, 1)))
        self.assertEqual(v["first_star"], date(2026, 8, 26))
        self.assertEqual(v["sustained_weeks"], 2)
        self.assertAlmostEqual(v["velocity_share_30d"], 156 / 182, places=3)

    def test_velocity_as_of_an_earlier_date_ignores_later_days(self):
        v = gv.velocity(gv.daily_counts(QYM[K_HISTORY]), date(2026, 9, 24), 182)
        self.assertEqual(v["stars_total"], 182 - 144)
        self.assertEqual((v["stars_7d"], v["stars_prev_7d"]), (8, 3))

    def test_post_launch_decay(self):
        v = gv.velocity(gv.daily_counts(MJ[K_HISTORY]), TODAY, 581)
        self.assertEqual((v["stars_7d"], v["stars_prev_7d"]), (17, 26))
        self.assertEqual((v["stars_30d"], v["stars_prev_30d"]), (581, 0))
        self.assertEqual(v["velocity_share_30d"], 1.0)
        self.assertEqual(v["peak_day"], 197)

    def test_equal_peaks_resolve_to_the_earliest_day(self):
        daily = {date(2026, 9, 20): 5, date(2026, 9, 25): 5, date(2026, 9, 28): 2}
        self.assertEqual(gv.velocity(daily, TODAY, 12)["peak_date"], date(2026, 9, 20))

    def test_a_weekly_title_is_dated_inside_its_week(self):
        # a bigger spike three weeks ago must not date a signal about this week
        daily = {date(2026, 9, 8): 90, date(2026, 9, 27): 4, date(2026, 9, 29): 21,
                 date(2026, 9, 30): 12}
        v = gv.velocity(daily, TODAY, 127)
        self.assertEqual(v["peak_date"], date(2026, 9, 8))
        self.assertEqual(v["peak_date_7d"], date(2026, 9, 29))
        title, _, unit = gv.velocity_title(v, date(2026, 7, 1), TODAY)
        self.assertEqual(title, "Stars up 37 in 7 days against 0 the week before")
        self.assertEqual(gv.velocity_date(v, unit, date(2026, 7, 1), TODAY), date(2026, 9, 29))
        self.assertEqual(gv.velocity_date(v, "stars/30d", date(2026, 7, 1), TODAY), date(2026, 9, 8))

    def test_date_is_clamped_to_the_repo_creation_day_and_today(self):
        # History days are Pacific calendar days: a star at 01:00 UTC on the day a
        # repo was created is bucketed on the Pacific day before.
        daily = {date(2026, 9, 25): 30, date(2026, 9, 26): 5}
        v = gv.velocity(daily, TODAY, 35)
        self.assertEqual(gv.velocity_date(v, "stars", date(2026, 9, 26), TODAY), date(2026, 9, 26))
        self.assertEqual(gv.velocity_date(v, "stars", date(2026, 9, 1), date(2026, 9, 24)),
                         date(2026, 9, 24))
        empty = gv.velocity({}, TODAY, 0)
        self.assertIsNone(gv.velocity_date(empty, "stars/30d", date(2026, 9, 1), TODAY))


class Strength(unittest.TestCase):
    @staticmethod
    def v(s7, p7, s30, p30, sustained=2):
        return {"stars_7d": s7, "stars_prev_7d": p7, "stars_30d": s30, "stars_prev_30d": p30,
                "sustained_weeks": sustained}

    def test_bands(self):
        routine = gv.velocity_strength(self.v(5, 4, 20, 15, 0))
        solid = gv.velocity_strength(self.v(40, 20, 150, 60, 4))
        notable = gv.velocity_strength(gv.velocity(gv.daily_counts(QYM[K_HISTORY]), TODAY, 182))
        rare = gv.velocity_strength(self.v(900, 100, 1500, 60, 3))
        self.assertTrue(0.1 <= routine <= 0.3, routine)
        self.assertTrue(0.35 <= solid <= 0.55, solid)
        self.assertTrue(0.6 <= notable <= 0.8, notable)
        self.assertTrue(0.85 <= rare <= 1.0, rare)

    def test_three_stars_against_zero_is_not_an_event(self):
        self.assertLess(gv.velocity_strength(self.v(3, 0, 3, 0, 0)), 0.2)

    def test_acceleration_beats_decay_at_the_same_level(self):
        up = gv.velocity_strength(self.v(100, 20, 150, 150))
        down = gv.velocity_strength(self.v(100, 500, 150, 150))
        self.assertGreater(up, down)

    def test_always_in_range(self):
        for args in [(0, 0, 0, 0, 0), (0, 5000, 0, 9000, 0), (10 ** 6, 0, 10 ** 6, 0, 8)]:
            self.assertTrue(0.0 <= gv.velocity_strength(self.v(*args)) <= 1.0)

    def test_quality_factor(self):
        q = lambda young, zero=0.2, median=2000: {  # noqa: E731
            "sg_pct_age_lt_90d": young, "sg_pct_zero_followers": zero,
            "sg_median_account_age_days": median}
        self.assertEqual(gv.quality_factor(None), 1.0)
        self.assertEqual(gv.quality_factor(q(0.04)), 1.0)       # QymCAD on the card: 4%
        self.assertEqual(gv.quality_factor(q(0.35)), 0.7)
        self.assertEqual(gv.quality_factor(q(0.5)), 0.4)
        self.assertEqual(gv.quality_factor(q(0.0, zero=0.85, median=200)), 0.6)

    def test_new_org_strength_scales_with_committers_and_stars(self):
        small = gv.new_org_strength(2, 30, 300, False, 1, False, True)
        big = gv.new_org_strength(18, 315, 55, True, 9, True, False)
        self.assertTrue(0.25 <= small <= 0.45, small)
        self.assertTrue(0.6 <= big <= 0.8, big)


class Series(unittest.TestCase):
    def test_weekly_points_are_oldest_first_and_end_today(self):
        daily = gv.daily_counts(QYM[K_HISTORY])
        series = gv.weekly_series(daily, TODAY, 182, QYM_REPO.created)
        self.assertEqual([p["t"] for p in series],
                         ["2026-08-27", "2026-09-03", "2026-09-10", "2026-09-17", "2026-09-24",
                          "2026-10-01"])
        self.assertEqual([p["v"] for p in series], [2, 25, 0, 3, 8, 144])
        now = gv.velocity_strength(gv.velocity(daily, TODAY, 182))
        self.assertEqual(series[-1]["s"], now)
        self.assertLess(max(p["s"] for p in series[:-1]), 0.35)  # nothing was happening before

    def test_no_points_before_the_repo_existed(self):
        series = gv.weekly_series(gv.daily_counts(MJ[K_HISTORY]), TODAY, 581, MJ_REPO.created)
        self.assertEqual(series[0]["t"], "2026-09-10")
        self.assertEqual(len(series), 4)
        # the launch weeks were stronger than today: the rank history can show the decay
        self.assertGreater(series[0]["s"], series[-1]["s"])

    def test_quality_factor_scales_every_point(self):
        daily = gv.daily_counts(QYM[K_HISTORY])
        full = gv.weekly_series(daily, TODAY, 182, QYM_REPO.created)
        half = gv.weekly_series(daily, TODAY, 182, QYM_REPO.created, factor=0.5)
        for a, b in zip(full, half):
            self.assertAlmostEqual(b["s"], a["s"] * 0.5, places=2)


class Titles(unittest.TestCase):
    def check(self, title: str):
        self.assertLess(len(title), 110)
        self.assertFalse(title.endswith("."))
        self.assertTrue(title[0].isupper())

    def test_accelerating_week(self):
        v = gv.velocity(gv.daily_counts(QYM[K_HISTORY]), TODAY, 182)
        title, value, unit = gv.velocity_title(v, QYM_REPO.created, TODAY)
        self.assertEqual(title, "Stars up 144 in 7 days against 8 the week before")
        self.assertEqual((value, unit), (144, "stars/7d"))
        self.check(title)

    def test_new_repo(self):
        v = gv.velocity(gv.daily_counts(MJ[K_HISTORY]), TODAY, 581)
        title, value, unit = gv.velocity_title(v, MJ_REPO.created, TODAY)
        self.assertEqual(title, "New repo gained 581 stars since it was created on 10 Sep 2026")
        self.assertEqual((value, unit), (581, "stars"))
        self.check(title)

    def test_month_over_month_and_decay_and_share(self):
        base = {"stars_7d": 60, "stars_prev_7d": 50, "stars_total": 2390}
        t, value, unit = gv.velocity_title({**base, "stars_30d": 1363, "stars_prev_30d": 784},
                                           date(2025, 3, 1), TODAY)
        self.assertEqual(t, "Stars up 1,363 in 30 days against 784 in the 30 days before")
        self.assertEqual((value, unit), (1363, "stars/30d"))
        t2, _, _ = gv.velocity_title({"stars_7d": 43, "stars_prev_7d": 468, "stars_30d": 1743,
                                      "stars_prev_30d": 1400, "stars_total": 2202},
                                     date(2026, 7, 1), TODAY)
        self.assertEqual(t2, "Added 1,743 stars in 30 days; 43 in the last 7 against 468 the week before")
        t3, _, _ = gv.velocity_title({"stars_7d": 15, "stars_prev_7d": 14, "stars_30d": 60,
                                      "stars_prev_30d": 55, "stars_total": 284},
                                     date(2026, 6, 20), TODAY)
        self.assertEqual(t3, "Added 60 stars in 30 days, 21% of its 284 total")
        for t in (t, t2, t3):
            self.check(t)

    def test_new_org_title_says_published_only_when_the_org_came_first(self):
        t = gv.new_org_title(date(2026, 8, 9), "QymCAD", date(2026, 8, 25), "2", 182)
        self.assertEqual(t, "Organization created 9 Aug 2026 published QymCAD with 2 contributors and 182 stars")
        # makerspet/oomwoo: the repo (10 Jun) is older than the organization (8 Jul)
        moved = gv.new_org_title(date(2026, 7, 8), "oomwoo", date(2026, 6, 10), "13", 11312)
        self.assertEqual(moved, "Organization created 8 Jul 2026 now hosts oomwoo with 13 contributors "
                                "and 11,312 stars")
        long = gv.new_org_title(date(2026, 7, 8), "a-very-long-repository-name-for-a-robot-arm-controller",
                                date(2026, 7, 9), "100+", 1234)
        self.assertEqual(long, "Organization created 8 Jul 2026 published a repo with 100+ contributors "
                               "and 1,234 stars")
        for t in (t, moved, long):
            self.check(t)


class Stargazers(unittest.TestCase):
    def test_watch_event_actors_newest_first(self):
        self.assertEqual(gv.stargazer_logins(QYM[K_EVENTS]), ["henk911", "hell-llex", "NSobolew"])
        self.assertEqual(gv.stargazer_logins(MJ[K_EVENTS]), ["engfelopater", "xplutoy", "kivejun"])

    def test_bots_duplicates_and_other_events_are_skipped(self):
        events = [
            {"type": "WatchEvent", "actor": {"login": "a"}, "created_at": "2026-10-01T02:00:00Z"},
            {"type": "WatchEvent", "actor": {"login": "a"}, "created_at": "2026-10-01T01:00:00Z"},
            {"type": "WatchEvent", "actor": {"login": "x[bot]"}, "created_at": "2026-10-01T03:00:00Z"},
            {"type": "ForkEvent", "actor": {"login": "b"}, "created_at": "2026-10-01T04:00:00Z"},
            {"type": "WatchEvent", "actor": {"login": "c"}, "created_at": "2026-10-01T05:00:00Z"},
        ]
        self.assertEqual(gv.stargazer_logins(events), ["c", "a"])
        self.assertEqual(gv.stargazer_logins({"message": "Not Found"}), [])

    @staticmethod
    def users(n_old: int, n_young: int, followers: int = 3) -> list[dict]:
        old = [{"createdAt": "2019-05-01T00:00:00Z", "followers": {"totalCount": followers}}] * n_old
        young = [{"createdAt": "2026-09-01T00:00:00Z", "followers": {"totalCount": 0}}] * n_young
        return old + young

    def test_small_sample_gives_no_judgement(self):
        self.assertIsNone(gv.stargazer_quality(self.users(5, 5), TODAY))

    def test_organic_sample(self):
        q = gv.stargazer_quality(self.users(29, 1), TODAY)
        self.assertEqual(q["sg_sample"], 30)
        self.assertAlmostEqual(q["sg_pct_age_lt_90d"], 0.033, places=3)
        self.assertAlmostEqual(q["stargazer_quality"], 0.967, places=3)
        self.assertGreater(q["sg_median_account_age_days"], 2000)
        self.assertEqual(gv.quality_factor(q), 1.0)

    def test_mostly_new_accounts_is_over_the_drop_line(self):
        q = gv.stargazer_quality(self.users(6, 24), TODAY)
        self.assertEqual(q["sg_pct_age_lt_90d"], 0.8)
        self.assertGreater(q["sg_pct_age_lt_90d"], gv._SG_DROP_ABOVE)
        self.assertEqual(q["sg_pct_zero_followers"], 0.8)
        self.assertTrue(gv.stars_look_bought(q))

    def test_three_agreeing_signs_drop_a_repo_under_the_half_line(self):
        # Phyzicalorg/Phyzical_org as sampled live: 44.8% under 90 days, 82.8%
        # with no followers, median account age 306 days
        q = {"sg_sample": 29, "sg_pct_age_lt_90d": 0.448, "sg_pct_zero_followers": 0.828,
             "sg_median_account_age_days": 306}
        self.assertTrue(gv.stars_look_bought(q))
        self.assertFalse(gv.stars_look_bought(None))
        # young-ish accounts that do have followers, or old accounts: discounted, not dropped
        self.assertFalse(gv.stars_look_bought({**q, "sg_pct_zero_followers": 0.3}))
        self.assertFalse(gv.stars_look_bought({**q, "sg_median_account_age_days": 1500}))
        self.assertFalse(gv.stars_look_bought({**q, "sg_pct_age_lt_90d": 0.1}))
        self.assertFalse(gv.stars_look_bought(gv.stargazer_quality(self.users(29, 1), TODAY)))


class Contributors(unittest.TestCase):
    def test_fixture(self):
        self.assertEqual(gv.human_contributors(QYM[K_CONTRIB]),
                         [{"login": "basson", "contributions": 19}])
        self.assertEqual(gv.human_contributors(MJ[K_CONTRIB]),
                         [{"login": "kevinzakka", "contributions": 11}])

    def test_automation_is_removed_and_order_is_by_commits(self):
        rows = [
            {"login": "dependabot[bot]", "type": "Bot", "contributions": 90},
            {"login": "claude", "type": "User", "contributions": 80},
            {"login": "Copilot", "type": "User", "contributions": 70},
            {"login": "acme-release-bot", "type": "User", "contributions": 60},
            {"login": "blb3d-automation", "type": "User", "contributions": 55},
            {"login": "talbot", "type": "User", "contributions": 2},
            {"login": "bob", "type": "User", "contributions": 5},
            {"login": "alice", "type": "User", "contributions": 40},
        ]
        self.assertEqual([c["login"] for c in gv.human_contributors(rows)], ["alice", "bob", "talbot"])
        self.assertEqual(gv.human_contributors({"message": "Not Found"}), [])


    def test_only_people_who_did_a_real_share_are_attached(self):
        # Shpigford/nurb as seen live: 204 commits by the owner, then 7 and 6
        nurb = [{"login": "Shpigford", "contributions": 204}, {"login": "dimfeld", "contributions": 7},
                {"login": "talic", "contributions": 6}]
        self.assertEqual([c["login"] for c in gv.attached_contributors(nurb)], ["Shpigford"])
        # robocurve/inspect-robots: 365, 71, 25, 20
        team = [{"login": "jeqcho", "contributions": 365}, {"login": "aris-zhu", "contributions": 71},
                {"login": "Adityakk9031", "contributions": 25}, {"login": "sravanthi6m", "contributions": 20}]
        self.assertEqual([c["login"] for c in gv.attached_contributors(team)],
                         ["jeqcho", "aris-zhu", "Adityakk9031"])
        # dexmal/opendm: a two-commit contributor is not named
        self.assertEqual([c["login"] for c in gv.attached_contributors(
            [{"login": "hbzfeng", "contributions": 28}, {"login": "cybercat521", "contributions": 2}])],
            ["hbzfeng"])
        # copperheadhq/copperhead: 16 commits is real work even next to 321
        self.assertEqual([c["login"] for c in gv.attached_contributors(
            [{"login": "animesh-chouhan", "contributions": 321}, {"login": "Chirag6722", "contributions": 27},
             {"login": "AniketR10", "contributions": 16}, {"login": "Devesh36", "contributions": 8}])],
            ["animesh-chouhan", "Chirag6722", "AniketR10"])
        self.assertEqual(gv.attached_contributors([]), [])


class ParseNodes(unittest.TestCase):
    def test_repo_node(self):
        r = gv.parse_repo_node(NODES["kenchangh/kensat"])
        self.assertEqual((r.owner, r.name, r.stars, r.forks), ("kenchangh", "kensat", 112, 18))
        self.assertEqual(r.created, date(2026, 6, 17))
        self.assertEqual(r.url, "https://github.com/kenchangh/kensat")
        self.assertEqual(r.owner_type, "User")
        self.assertIn("cubesat", r.topics)
        self.assertEqual(r.homepage, "https://kensat.com")

    def test_language_and_all_twenty_topics(self):
        topics = [f"topic-{i}" for i in range(19)] + ["rtl-sdr"]
        node = {"nameWithOwner": "u/x", "createdAt": "2026-08-01T00:00:00Z",
                "owner": {"__typename": "User", "login": "u"},
                "primaryLanguage": {"name": "SystemVerilog"},
                "repositoryTopics": {"nodes": [{"topic": {"name": t}} for t in topics]}}
        r = gv.parse_repo_node(node)
        self.assertEqual(r.language, "SystemVerilog")
        self.assertEqual(r.topics, topics)
        self.assertTrue(gv.repo_text(r).endswith("topic 18, rtl sdr"))
        # the search asks for every topic a repo can carry, and for the language
        self.assertIn("repositoryTopics(first:20)", gv._REPO_FRAGMENT)
        self.assertIn("primaryLanguage{name}", gv._REPO_FRAGMENT)
        # as GitHub returned them in the saved search response
        self.assertEqual(gv.parse_repo_node(NODES["kenchangh/kensat"]).language, "Python")
        self.assertEqual(gv.parse_repo_node(NODES["skmp/polly2-rtl"]).language, "SystemVerilog")
        # a repo with no code GitHub recognises has no primary language
        self.assertIsNone(gv.parse_repo_node(NODES["olmanqj/awesome-flight-software"]).language)
        self.assertIsNone(gv.parse_repo_node({**node, "primaryLanguage": None}).language)
        del node["primaryLanguage"]
        self.assertIsNone(gv.parse_repo_node(node).language)

    def test_unusable_nodes(self):
        self.assertIsNone(gv.parse_repo_node(None))
        self.assertIsNone(gv.parse_repo_node({}))
        self.assertIsNone(gv.parse_repo_node({"nameWithOwner": "a/b", "owner": {"login": "a"}}))

    def test_counts_accept_either_shape(self):
        self.assertEqual(gv._count({"totalCount": 7}), 7)
        self.assertEqual(gv._count(7), 7)
        for odd in (None, {}, "7", True, [7]):
            self.assertIsNone(gv._count(odd))
        rest_shaped = {"__typename": "User", "login": "u", "followers": 12, "repositories": 3}
        o = gv.parse_owner(rest_shaped)
        self.assertEqual((o.followers, o.public_repos), (12, 3))
        self.assertEqual(gv.person_from_user(rest_shaped, "Repository owner").facts["followers"], 12)
        sample = [{"createdAt": "2019-05-01T00:00:00Z", "followers": 4}] * 20
        self.assertEqual(gv.stargazer_quality(sample, TODAY)["sg_pct_zero_followers"], 0.0)

    def test_every_fixture_node_parses(self):
        for name, node in NODES.items():
            r = gv.parse_repo_node(node)
            self.assertEqual(r.full_name, name)
            self.assertIsNotNone(gv.parse_owner(node["owner"]))

    def test_user_owner(self):
        o = gv.parse_owner(NODES["kenchangh/kensat"]["owner"])
        self.assertFalse(o.is_org)
        self.assertEqual((o.name, o.location, o.followers), ("Ken Chan", "San Francisco", 140))
        self.assertEqual(o.description, "Working on space & hardware")  # the bio
        self.assertIsNone(o.domain)  # a user's own site is never the entity's domain

    def test_org_owner_from_fixture(self):
        self.assertTrue(QYM_OWNER.is_org)
        self.assertEqual(QYM_OWNER.name, "QymIs.Tech")
        self.assertEqual(QYM_OWNER.domain, "qymis.tech")
        self.assertEqual(QYM_OWNER.created, date(2026, 8, 9))
        self.assertEqual(QYM_OWNER.location, "Kazakhstan")
        self.assertFalse(QYM_OWNER.academic)

    def test_website_without_scheme_and_junk_hosts(self):
        self.assertEqual(MJ_OWNER.website, "https://kzakka.com")  # profile says "kzakka.com"
        o = gv.parse_owner(NODES["giancarloerra/Degauss"]["owner"])
        self.assertEqual(o.company, "Altaire Limited")
        org = gv.parse_owner({"__typename": "Organization", "login": "x",
                              "websiteUrl": "https://farotech.github.io/orbitfabric/"})
        self.assertIsNone(org.domain)

    def test_academic_owners(self):
        edu = gv.parse_owner({"__typename": "Organization", "login": "x",
                              "websiteUrl": "https://rpl.cs.utexas.edu"})
        lab = gv.parse_owner({"__typename": "Organization", "login": "hku-sail", "name": "SAIL@HKU",
                              "description": "HKU Super Artificial Intelligence Lab (SAIL)"})
        co = gv.parse_owner({"__typename": "Organization", "login": "menloresearch",
                             "name": "Menlo Research", "websiteUrl": "https://menlo.ai",
                             "description": "Menlo Research builds Asimov, a humanoid."})
        self.assertTrue(edu.academic)
        self.assertTrue(lab.academic)
        self.assertFalse(co.academic)
        # clean_domain answers None for schools, so the academic check reads
        # the raw host: a site or a contact address on any academic suffix
        self.assertIsNone(edu.domain)
        for node in ({"websiteUrl": "rpl.cs.utexas.edu"},
                     {"websiteUrl": "https://www.ee.cuhk.edu.hk/~lab/"},
                     {"websiteUrl": "http://robotics.sjtu.edu.cn"},
                     {"websiteUrl": "https://www.cl.cam.ac.uk"},
                     {"email": "contact@cs.stanford.edu"},
                     {"websiteUrl": "https://example-robotics.com", "email": "pi@iis.ac.cn"}):
            o = gv.parse_owner({"__typename": "Organization", "login": "x", **node})
            self.assertTrue(o.academic, node)
        for node in ({"websiteUrl": "https://education-robots.com"},
                     {"websiteUrl": "https://edu.acme.io", "email": "hi@acme.io"},
                     {"websiteUrl": "not a url"}, {}):
            o = gv.parse_owner({"__typename": "Organization", "login": "x", **node})
            self.assertFalse(o.academic, node)

    def test_established_org(self):
        old = gv.parse_owner({"__typename": "Organization", "login": "bigco",
                              "createdAt": "2012-01-01T00:00:00Z",
                              "repositories": {"totalCount": 150}})
        self.assertTrue(gv.is_established(old, TODAY))
        self.assertFalse(gv.is_established(QYM_OWNER, TODAY))
        self.assertFalse(gv.is_established(MJ_OWNER, TODAY))


class Filters(unittest.TestCase):
    def test_lists_and_course_material(self):
        for name in ("olmanqj/awesome-flight-software", "ishandutta2007/Awesome-Satellite-Operations",
                     "andrea-cpu96/vhdl-cookbook", "Kampi/TinyMCU"):
            self.assertEqual(gv.exclusion_reason(gv.parse_repo_node(NODES[name])),
                             "list or course material", name)
        # seen live: no description, and "asic" in the name passes the shared model alone
        prep = repo("KareemElhafi/Cisco-ASIC-Verification-Internship-Preparation")
        self.assertGreaterEqual(gv.classify(gv.repo_text(prep))["fit"], 0.3)
        self.assertEqual(gv.exclusion_reason(prep), "list or course material")

    def test_forks_stoplist_and_labs(self):
        self.assertEqual(gv.exclusion_reason(repo("a/b", "A drone autopilot", fork=True)), "fork")
        for owner in ("facebookresearch", "NVlabs", "amazon-far", "baidu-baige", "eth-siplab", "Toyota"):
            self.assertEqual(gv.exclusion_reason(repo(f"{owner}/x", "Humanoid VLA")),
                             "stoplist owner", owner)
        self.assertEqual(gv.exclusion_reason(repo("real-stanford/x", "Humanoid VLA",
                                                  owner_type="Organization")), "stoplist owner")
        # a student's own account is a person, not a university org
        self.assertIsNone(gv.exclusion_reason(repo("Peter-cuhk/robot-data-studio",
                                                   "Workbench for robot datasets")))
        self.assertEqual(gv.exclusion_reason(repo("malik-group/x", "Dexterous hands",
                                                  owner_type="Organization")), "university")
        self.assertEqual(gv.exclusion_reason(repo("u/x", "ROS 2 stack, University of Bonn")),
                         "university")

    def test_keyword_collisions_seen_live_are_dropped(self):
        cases = [
            ("u/UAV-Downloader", "UAV Downloader for JableTV: unattended UAV Watcher"),
            ("u/kiosk-satellite", "Turn any Android device into a Home Assistant kiosk"),
            ("u/CnC-FPS-Unlocker", "Run Red Alert 3 above 30 fps"),
            ("u/b2b-sdr-agent-template", "Open-source AI SDR template for B2B export, sales pipeline"),
            ("u/SolidWorks-Premium-2026", "SolidWorks Premium 2026: 3D mechanical CAD on Windows"),
            ("u/Degauss", "The lightweight frontend for MiSTer FPGA"),
            ("u/kistack", "KiStack is a HUMAN WRITTEN bunch of skills for KiCad"),
        ]
        for name, desc in cases:
            self.assertEqual(gv.exclusion_reason(repo(name, desc)), "not a hardware project", name)

    def test_hobby_gadgets_and_contest_boards_seen_live_are_dropped(self):
        self.assertEqual(gv.exclusion_reason(repo(
            "Auroranchen/-PCB", "这是一个面向蓝桥杯单片机竞赛的 STM32G431RBT6 开发板")), "university")
        for name, desc in [
            ("ReconGrunt/FlipDeFlock", "Passive Flipper Zero surveillance detector for drones"),
            ("rkana-org/gfty", "A CLI tool to manage gridfinity configurations for baseplates"),
            ("SAM0-0/ATHER-OBD-READER", "A open-source OBD data reader for Ather electric scooters, "
                                        "reads the BMS (Battery Management System)"),
        ]:
            self.assertEqual(gv.exclusion_reason(repo(name, desc)), "not a hardware project", name)
        # seen once all twenty topics were read: "drone-detection" and "remote-id"
        # sat past the tenth topic and anchored an Android privacy app
        app = repo("CIS-C0/RFSentinel", "Passive BLE & WiFi scanner for Android that flags police and "
                   "surveillance equipment: body cams, license-plate cameras, drones, trackers and "
                   "camera glasses.", ("android", "bluetooth-low-energy", "kotlin", "privacy",
                                       "drone-detection", "remote-id"))
        self.assertEqual(gv.exclusion_reason(app), "not a hardware project")
        # acoustic drone detection hardware is not that
        self.assertIsNone(gv.exclusion_reason(repo("agamrossen/VolAnti", "Open-source acoustic drone "
                                                   "detection. It hears the propellers, not the radio",
                                                   ("3d-printing", "drone-detection", "pcb"))))

    def test_pcb_vocabulary_in_topics_alone_does_not_anchor(self):
        # made with KiCad is not EDA tooling
        ups = repo("aronreid/ups-to-esp32", "Put a USB-only UPS on the network: an ESP32-S3 board "
                   "and firmware that serves NUT", ("esp32", "home-assistant", "kicad", "ups"))
        laptop = repo("EwoudVV/ducktop2", "An open-source 16-inch laptop designed from scratch.",
                      ("cyberdeck", "hardware", "kicad", "laptop", "pcb"))
        self.assertIsNone(gv.thesis_gate(ups))
        self.assertIsNone(gv.thesis_gate(laptop))
        # the shared model holds "pcb" to be unambiguous: that topic alone passes it
        self.assertGreaterEqual(gv.classify("Topics: hardware, laptop, pcb")["fit"], 0.3)
        # EDA tooling says so in its own name or description
        self.assertIsNotNone(gv.thesis_gate(repo("drandyhaas/KiCadRoutingTools",
                                                 "A set of Python/Rust tools to aid routing in KiCad")))
        self.assertIsNotNone(gv.thesis_gate(repo("u/boardview", "Viewer for gerbers", ("kicad",))))
        # a robot that also ships a board is anchored by the robot, topics or not
        self.assertIsNotNone(gv.thesis_gate(repo("7757/fanduck", "Build resources for our replica",
                                                 ("bipedal-robot", "kicad", "robotics"))))
        # and other families still anchor from topics
        self.assertIsNotNone(gv.thesis_gate(repo("penberg/titania", "A complete large language model "
                                                 "system, from transformer to transistor", ("verilog",))))

    def test_personal_repos_need_twenty_stars_in_the_month(self):
        v = {"stars_30d": 13, "stars_7d": 13, "accel_30d": 14.0, "accel_7d": 14.0}
        personal = repo("wieslawsoltes/CadSpace", "Modular 2D/3D CAD workspace")
        org = repo("acme/arm", "Robot arm driver", owner_type="Organization")
        self.assertFalse(gv._wants_velocity(personal, v))
        self.assertTrue(gv._wants_velocity(org, v))
        self.assertTrue(gv._wants_velocity(personal, {**v, "stars_30d": 20}))

    def test_real_projects_are_kept(self):
        for r in (QYM_REPO, MJ_REPO, gv.parse_repo_node(NODES["kenchangh/kensat"]),
                  repo("makerspet/oomwoo", "Open-source vacuum robot cleaner",
                       ("3d-printing", "esp32", "home-assistant", "ros2")),
                  repo("u/synth-explorer", "Compiler Explorer for RTL: synthesize Verilog with Yosys")):
            self.assertIsNone(gv.exclusion_reason(r), r.full_name)

    def test_anchored(self):
        yes = [
            "A Python library for running thousands of MuJoCo simulations in parallel on CPU",
            "Parametric 3D CAD with a real B-rep kernel (OpenCASCADE)",
            "modular, client-server software-defined radio",
            "Hardware as fast as software. Cursor for circuit boards.",
            "Open-source acoustic drone detection",
            "Controls library for the Open Inverter-Based Resource",
            "ESP32无人机飞控固件",
            "The open-source operating system for robots. Building the full stack for physical AI",
        ]
        no = [
            "UAV Watcher for video sites",
            "Open-source AI SDR template",
            "A modern admin template called Robot Admin",
            "A spy satellite simulator in your browser",
            "A deep real-time brain. Topics: machine learning, embodied ai",
            "The front interface of the AI assistant. The Apex Humanoid is not included",
            "Quality-per-token model router: Model Inverter",
            "Data exploration in the terminal. Topics: eda",
        ]
        for t in yes:
            self.assertTrue(gv.anchored(t), t)
        for t in no:
            self.assertFalse(gv.anchored(t), t)

    def test_thesis_gate(self):
        self.assertIsNotNone(gv.thesis_gate(QYM_REPO))
        self.assertIsNotNone(gv.thesis_gate(MJ_REPO))
        fit, gate = gv.thesis_gate(gv.parse_repo_node(NODES["kenchangh/kensat"]))
        self.assertGreaterEqual(fit, 0.3)
        self.assertEqual(gate, "classify")
        self.assertIsNone(gv.thesis_gate(repo("u/datui", "Data Exploration in the Terminal", ("eda",))))

    def test_agent_tooling_must_be_anchored_by_its_own_text(self):
        r = repo("u/RoboRSI", "Robot-agent harness with a CLI and local Web console",
                 ("embodied-ai", "robotics", "agents", "llm"))
        self.assertIsNone(gv.thesis_gate(r))

    def test_org_description_can_carry_the_gate(self):
        r = repo("copperheadhq/copperhead", "Hardware as fast as software.",
                 ("agentic-ai", "cli", "eda"), owner_type="Organization")
        self.assertIsNone(gv.thesis_gate(r))
        self.assertIsNotNone(gv.thesis_gate(r, "Cursor for circuit boards."))

    def test_collisions_the_shared_model_passes_are_still_dropped_here(self):
        # each of these scores 0.3 or more in the shared keyword model on one
        # word it holds to be unambiguous; the lone-term cap does not catch them
        dropped = [
            repo("u/UAV-Downloader", "UAV Downloader for JableTV: unattended UAV Watcher"),
            repo("u/voice-satellite-card", "Voice Satellite turns any tablet into a voice assistant "
                 "for Home Assistant"),
            repo("u/CnC-FPS-Unlocker", "Run Red Alert 3 above 30 fps"),
            repo("u/mister-companion", "A simple companion for MiSTer-FPGA"),
        ]
        unanchored = [
            repo("u/gods-eye-view", "A spy satellite simulator in your browser"),
            repo("u/autopilot-jobhunt", "AI job agent: scans careers pages nightly and drafts cover letters"),
            repo("u/osmosis", "Third party Android client for DJI Osmo cameras and DJI drones"),
            # "radar" passes the shared model alone; on GitHub it is mostly not a sensor
            repo("u/meme-radar", "Local read-only multi-chain meme token scanner", ("crypto", "solana")),
            repo("u/reddit-radar", "Reddit Radar: an MCP server that scans thousands of Reddit threads"),
            repo("u/pubg-visual-toolkit", "Player Tracker, Radar Overlay, Aim Assistant"),
            repo("u/hookecho", "Live weather radar without the clutter: rain, warnings, lightning",
                 ("meteorology", "nexrad", "radar", "weather")),
        ]
        for r in dropped + unanchored:
            self.assertGreaterEqual(gv.classify(gv.repo_text(r))["fit"], 0.3, r.full_name)
        for r in dropped:
            self.assertEqual(gv.exclusion_reason(r), "not a hardware project", r.full_name)
        for r in unanchored:
            self.assertIsNone(gv.exclusion_reason(r), r.full_name)
            self.assertIsNone(gv.thesis_gate(r), r.full_name)

    def test_signal_text_is_what_github_says_and_thesis_metrics_score_it(self):
        text = gv.signal_text(QYM_REPO, QYM_OWNER)
        self.assertTrue(text.startswith(
            "QymIs-Tech/QymCAD: QymCAD. Parametric 3D CAD with a real B-rep kernel (OpenCASCADE). Topics: "))
        self.assertIn("parametric cad", text)                 # the "parametric-cad" topic
        self.assertTrue(text.endswith(QYM_OWNER.description.rstrip(".")))  # the organization's own words
        self.assertNotIn("..", text)
        self.assertNotIn("Mostly written in", text)            # the REST fixture has no language
        m = gv.thesis_metrics("QymIs.Tech", text)
        self.assertGreaterEqual(m["thesis_fit"], 0.3)
        self.assertEqual(m["thesis_anchor_only"], 0)
        # a user's bio is about the person, not the repo
        self.assertNotIn("Berkeley", gv.signal_text(MJ_REPO, MJ_OWNER))

    def test_one_ambiguous_term_stays_anchor_only(self):
        # "mujoco" alone: on thesis by this module's anchors, under the shared
        # model's lone-term cap. Nothing is added to the text to lift it.
        self.assertIsNotNone(gv.thesis_gate(MJ_REPO))
        m = gv.thesis_metrics("mjbatch", gv.signal_text(MJ_REPO, MJ_OWNER))
        self.assertLess(m["thesis_fit"], 0.3)
        self.assertEqual(m["thesis_anchor_only"], 1)
        # "cad" alone is capped the same way (seen live)
        dxf = repo("mlightcad/cad-viewer", "The world's first fully web-based DXF/DWG viewer and editor "
                   "that runs entirely in the browser", ("autocad", "cad", "dwg", "dxf"))
        self.assertEqual(gv.thesis_gate(dxf)[1], "anchor")
        m = gv.thesis_metrics("cad-viewer", gv.signal_text(dxf, None))
        self.assertEqual(gv.classify(gv.signal_text(dxf, None))["terms"], ["cad"])
        self.assertTrue(0 < m["thesis_fit"] < 0.3)
        self.assertEqual(m["thesis_anchor_only"], 1)

    def test_vocabulary_the_shared_model_does_not_list_is_anchor_only_at_zero(self):
        # seen live. "sdr" by itself is not a thesis term: an RTL-SDR scanner
        # that never writes out "software-defined radio" scores nothing there
        scanner = repo("MattCheramie/GopherTrunk", "Pure-Go, cross-platform RTL-SDR scanner and audio "
                       "processing toolkit", ("golang", "rf", "rtl-sdr", "sdr", "p25", "tetra"))
        scanner.language = "Go"
        self.assertEqual(gv.thesis_gate(scanner), (0.0, "anchor"))
        self.assertEqual(gv.thesis_metrics("GopherTrunk", gv.signal_text(scanner, None)),
                         {"thesis_fit": 0.0, "thesis_anchor_only": 1})
        # the same kind of repo with the phrase written out passes there on its own
        spelled = repo("Newspicel/sdrminusminus", "modular, client-server software-defined radio",
                       ("sdr", "hackrf", "rtl-sdr"))
        self.assertEqual(gv.thesis_metrics("sdrminusminus", gv.signal_text(spelled, None))["thesis_anchor_only"], 0)
        # "sdr" alone still does not anchor a repo here either
        self.assertIsNone(gv.thesis_gate(repo("u/sdr-console", "A console for my SDR")))
        # a PLC gateway: "plc" is not a shared term, "industrial" alone is capped
        plc = repo("u/edge-gateway", "Collects data from Modbus and S7 PLCs and forwards it over MQTT",
                   ("modbus", "plc", "mqtt"))
        self.assertEqual(gv.thesis_gate(plc), (0.0, "anchor"))

    def test_terms_that_now_pass_the_shared_model_alone_are_not_anchor_only(self):
        for name, desc, topics in [
            ("u/rtl-lint", "Deterministic structural sanity checker for Verilog RTL", ()),
            ("u/vector-core", "Custom 64 bit vector accelerator for a RISC-V CPU", ()),
            ("diodeinc/pcb", "PCB tooling by Diode Computers, Inc", ()),
            ("u/demod-analyzer", "Demodulate BPSK/QPSK/QAM16 signals from IQ recordings",
             ("gnuradio", "hackrf", "rtlsdr", "sdr")),
        ]:
            r = repo(name, desc, topics)
            self.assertEqual(gv.thesis_gate(r)[1], "classify", name)
            text = gv.signal_text(r, None)
            self.assertEqual(len(gv.classify(text)["terms"]), 1, name)
            self.assertEqual(gv.thesis_metrics(r.name, text)["thesis_anchor_only"], 0, name)

    def test_a_screen_called_a_radar_does_not_put_a_repo_on_thesis(self):
        # seen live: anchored by the RTL-SDR dongle, and the shared model's
        # only term is "radar", from "a radar GUI"
        tracker = repo("PrzemekWasinski/PlaneTracker", "Live aircraft tracker that receives radio "
                       "broadcasts from planes via a radio antenna, decodes them on an RPI 4 and shows "
                       "their position on a radar GUI.", ("1090mhz", "ads-b", "dump1090", "rtl-sdr", "sdr"))
        tracker.language = "Python"
        text = gv.signal_text(tracker, None)
        self.assertEqual(gv.classify(text)["terms"], ["radar"])
        self.assertGreaterEqual(gv.classify(text)["fit"], 0.3)
        self.assertTrue(gv.only_a_screen_radar(text))
        self.assertIsNone(gv.exclusion_reason(tracker))
        self.assertIsNone(gv.thesis_gate(tracker))
        for phrase in ("radar sweep, aircraft silhouettes", "Radar Overlay, Aim Assistant",
                       "radar-style minimap", "Live ADS-B flight radar for the Cheap Yellow Display"):
            self.assertTrue(gv.only_a_screen_radar(phrase), phrase)
        # a radar that also has a screen, and a repo with other thesis terms, are untouched
        for kept in ("Open-source 24 GHz FMCW radar with a web radar UI",
                     "Counter-UAS drone tracker with a radar GUI",
                     "Phased array radar signal processing"):
            self.assertFalse(gv.only_a_screen_radar(kept), kept)
        fmcw = repo("u/openfmcw", "Open-source 24 GHz FMCW radar: RF front-end, antenna and STM32 "
                    "firmware, with a web radar UI")
        self.assertEqual(gv.thesis_gate(fmcw)[1], "classify")
        # without the phrase the tracker is what it was: anchored here, nothing there
        plain = repo("u/planes", "Live aircraft tracker on an RPI 4", ("ads-b", "rtl-sdr"))
        self.assertEqual(gv.thesis_gate(plain), (0.0, "anchor"))

    def test_the_detected_language_is_a_thesis_term_for_hdl_repos(self):
        # from the saved search response: GitHub reports SystemVerilog
        polly = gv.parse_repo_node(NODES["skmp/polly2-rtl"])
        text = gv.signal_text(polly, None)
        self.assertTrue(text.endswith(". Mostly written in SystemVerilog"))
        self.assertEqual(gv.classify(text)["terms"], ["fpga", "systemverilog"])
        kensat = gv.signal_text(gv.parse_repo_node(NODES["kenchangh/kensat"]), None)
        self.assertIn(". Mostly written in Python", kensat)
        self.assertNotIn("python", gv.classify(kensat)["terms"])
        # a constructed case: anchored here by "rv32i", which the shared model
        # does not list, then the language as the term it does
        core = repo("u/vector-core", "Custom vector accelerator for an RV32I core")
        self.assertEqual(gv.thesis_gate(core), (0.0, "anchor"))
        self.assertEqual(gv.thesis_metrics("vector-core", gv.signal_text(core, None))["thesis_anchor_only"], 1)
        core.language = "SystemVerilog"
        text = gv.signal_text(core, None)
        self.assertTrue(text.endswith("for an RV32I core. Mostly written in SystemVerilog"))
        self.assertEqual(gv.classify(text)["terms"], ["systemverilog"])
        self.assertEqual(gv.thesis_metrics("vector-core", text)["thesis_anchor_only"], 0)
        # a language is never what puts a repo on thesis by itself
        tool = repo("u/logviewer", "Fast log viewer")
        tool.language = "Verilog"
        self.assertIsNone(gv.thesis_gate(tool))
        # and an ordinary language adds nothing
        mj = gv.parse_repo_node(graphql_node_from_rest(MJ[K_REPO]))
        mj.language = "Python"
        self.assertEqual(gv.thesis_metrics("mjbatch", gv.signal_text(mj, None))["thesis_anchor_only"], 1)

    def test_an_organization_name_counts_as_part_of_the_text(self):
        r = repo("Mondo-Robotics/PMT", "Official implementation of Perceptive Behavior Foundation Model",
                 owner_type="Organization")
        self.assertIsNotNone(gv.thesis_gate(r))
        self.assertEqual(gv.thesis_metrics("Mondo-Robotics", gv.signal_text(r, None))["thesis_anchor_only"], 0)

    def test_paper_drop(self):
        yes = ['[NeurIPS 2026] Official code of "StreamPI: Streaming Multimodal"',
               'Official repository for "RoboDojo: A Unified Sim-and-Real Benchmark"',
               'The official implementation of the paper "Scaling Behavior Foundation Model"',
               "HuRo: Robotizing Human Videos for Scalable VLA Pretraining (CoRL 2026)"]
        no = ["Official implementation of AstraBrain-WBC 0.5",
              "Parametric 3D CAD with a real B-rep kernel (OpenCASCADE)",
              "Open source evals for physical AI"]
        for d in yes:
            self.assertTrue(gv.is_paper_drop(repo("u/x", d)), d)
        for d in no:
            self.assertFalse(gv.is_paper_drop(repo("u/x", d)), d)


class Domains(unittest.TestCase):
    def test_site_domain(self):
        self.assertEqual(gv._site_domain("https://qymis.tech/"), "qymis.tech")
        self.assertEqual(gv._site_domain("https://developer.intrinsic.ai/docs"), "intrinsic.ai")
        self.assertEqual(gv._site_domain("https://sail.ai.hku.hk"), "sail.ai.hku.hk")
        for junk in ("https://github.com/DynamiX-Labs/x", "https://dynamix-labs.github.io/x",
                     "https://www.linkedin.com/in/x/", "--disable-wiki", "", None,
                     "https://x.gitbook.io/docs", "https://arxiv.org/abs/2609.00001",
                     # rejected by the shared clean_domain, no longer listed here
                     "https://myproject.pages.dev", "https://someone.itch.io/sim",
                     "https://demo.hf.space", "https://hackaday.io/project/1",
                     "https://ai.stanford.edu/~x", "https://www.nasa.gov/x",
                     # still this module's own list
                     "https://docs.px4.io/main", "https://www.bilibili.com/video/x",
                     "https://x.mintlify.app", "someone@foxmail.com"):
            self.assertIsNone(gv._site_domain(junk), junk)
        # every host on the local list is one the shared helper lets through
        for host in gv._JUNK_HOSTS:
            self.assertIsNotNone(gv.clean_domain(host), host)

    def test_domain_related(self):
        self.assertTrue(gv.domain_related("kensat.com", "kenchangh", "kensat"))
        self.assertTrue(gv.domain_related("misterdegauss.com", "giancarloerra", "Degauss"))
        self.assertTrue(gv.domain_related("cad.qymis.tech", "QymIs-Tech", "QymCAD"))
        # a project subdomain on somebody's personal site is not the project's domain
        self.assertFalse(gv.domain_related("skyfall-gs.jayinnn.dev", "jayin92", "Skyfall-GS"))
        self.assertFalse(gv.domain_related("ardupilot.org", "someone", "my-plugin"))

    def test_entity_domain(self):
        self.assertEqual(gv.entity_domain(QYM_REPO, QYM_OWNER), "qymis.tech")  # org site wins
        self.assertIsNone(gv.entity_domain(MJ_REPO, MJ_OWNER))  # kzakka.com is a personal blog
        kensat = NODES["kenchangh/kensat"]
        self.assertEqual(gv.entity_domain(gv.parse_repo_node(kensat), gv.parse_owner(kensat["owner"])),
                         "kensat.com")
        dyn = NODES["DynamiX-Labs/SDR-Hardware-Benchmark"]  # homepage points back at github.com
        self.assertIsNone(gv.entity_domain(gv.parse_repo_node(dyn), gv.parse_owner(dyn["owner"])))

    def test_a_project_page_on_the_authors_own_site_is_not_the_projects_domain(self):
        def with_home(full_name: str, home: str, owner_type: str = "User") -> gv.Repo:
            r = repo(full_name, "Dexterous manipulation recipe", owner_type=owner_type)
            r.homepage = home
            return r
        # seen live: the domain matched the owner's login, not the repo
        self.assertIsNone(gv.entity_domain(with_home("yunhaif/regrind", "https://www.yunhaifeng.com/REGRIND/"), None))
        self.assertIsNone(gv.entity_domain(
            with_home("mohammadrezwankhan/matlab-simulink-energy-lab",
                      "https://rezwankhan.tech/models/matlab-simulink-energy-lab/"), None))
        # the repo's own name on the domain is the project's site
        self.assertEqual(gv.entity_domain(with_home("Shpigford/nurb", "https://nurb.dev"), None), "nurb.dev")
        # an organization's name on the domain still counts
        org = gv.parse_owner({"__typename": "Organization", "login": "ESPARGOS", "name": "ESPARGOS"})
        self.assertEqual(gv.entity_domain(with_home("ESPARGOS/esp-sdr", "https://espargos.net/espsdr/",
                                                    "Organization"), org), "espargos.net")


class Entities(unittest.TestCase):
    def test_org_with_own_domain_is_a_company(self):
        e = gv.build_entity(QYM_REPO, QYM_OWNER, TODAY)
        self.assertEqual((e.name, e.kind, e.domain, e.github),
                         ("QymIs.Tech", "company", "qymis.tech", "QymIs-Tech"))
        # the org account was opened on 2026-08-09; that is not a founding date
        self.assertIsNone(e.founded)
        self.assertEqual(e.location, "Kazakhstan")
        self.assertEqual(e.one_liner, "Parametric 3D CAD with a real B-rep kernel (OpenCASCADE)")
        # qymis.tech does not carry "QymCAD", so the repo name is not offered as a company name
        self.assertEqual(e.aliases, ["QymIs-Tech"])
        self.assertEqual(e.links["repo"], "https://github.com/QymIs-Tech/QymCAD")
        self.assertEqual(e.links["website"], "https://qymis.tech/")

    def test_user_owned_repo_is_a_project(self):
        e = gv.build_entity(MJ_REPO, MJ_OWNER, TODAY)
        self.assertEqual((e.name, e.kind, e.domain, e.github),
                         ("mjbatch", "project", None, "kevinzakka"))
        self.assertIsNone(e.founded)
        self.assertNotIn("website", e.links)

    def test_founded_is_never_the_org_account_date(self):
        # Galbot's own description says it was founded in May 2023; its GitHub
        # organization was created on 2024-05-10.
        galbot = gv.parse_owner({"__typename": "Organization", "login": "GalaxyGeneralRobotics",
                                 "name": "Galbot", "websiteUrl": "https://www.galbot.com/",
                                 "createdAt": "2024-05-10T07:14:52Z"})
        e = gv.build_entity(repo("GalaxyGeneralRobotics/Humanoid-GPT", "Whole-body humanoid control",
                                 owner_type="Organization"), galbot, TODAY)
        self.assertEqual((e.name, e.kind, e.domain), ("Galbot", "company", "galbot.com"))
        self.assertIsNone(e.founded)

    def test_repo_name_is_an_alias_only_when_the_domain_carries_it(self):
        fluid = gv.parse_owner({"__typename": "Organization", "login": "Fluid-CAD",
                                "websiteUrl": "https://fluidcad.io"})
        e = gv.build_entity(repo("Fluid-CAD/FluidCAD", "Parametric cad modeling",
                                 owner_type="Organization"), fluid, TODAY)
        self.assertEqual((e.name, e.aliases), ("Fluid-CAD", ["FluidCAD"]))
        menlo = gv.parse_owner({"__typename": "Organization", "login": "menloresearch",
                                "name": "Menlo Research", "websiteUrl": "https://menlo.ai"})
        e = gv.build_entity(repo("menloresearch/cyclotron", "RL framework for humanoid robots",
                                 owner_type="Organization"), menlo, TODAY)
        # "cyclotron" would let the resolver fold any company of that name into Menlo
        self.assertEqual(e.aliases, ["menloresearch"])

    def test_names_are_cleaned(self):
        self.assertEqual(gv.clean_org_name("Hebbian Robotics (YC S26)"), "Hebbian Robotics")
        self.assertEqual(gv.clean_org_name("Acme Robotics [hiring]"), "Acme Robotics")
        self.assertEqual(gv.clean_org_name("Maker's Pet"), "Maker's Pet")
        self.assertEqual(gv.clean_org_name("AI (x)"), "AI (x)")  # nothing usable would be left
        hebbian = gv.parse_owner({"__typename": "Organization", "login": "Hebbian-Robotics",
                                  "name": "Hebbian Robotics (YC S26)",
                                  "websiteUrl": "https://hebbianrobotics.com/"})
        e = gv.build_entity(repo("Hebbian-Robotics/hflow", "SDK for robotics teams",
                                 owner_type="Organization"), hebbian, TODAY)
        self.assertEqual(e.name, "Hebbian Robotics")
        self.assertEqual(e.aliases, ["Hebbian Robotics (YC S26)", "Hebbian-Robotics"])
        self.assertEqual(e.github, "Hebbian-Robotics")
        # a personal repo whose bare name identifies nothing is named owner/repo
        self.assertEqual(gv.project_name(repo("Auroranchen/-PCB")), "Auroranchen/-PCB")
        self.assertEqual(gv.project_name(repo("someone/firmware")), "someone/firmware")
        self.assertEqual(gv.project_name(repo("u/sim")), "u/sim")
        self.assertEqual(gv.project_name(repo("Shpigford/nurb")), "nurb")
        self.assertEqual(gv.project_name(MJ_REPO), "mjbatch")

    def test_old_org_has_no_founded_date_and_no_domain_means_project(self):
        old = gv.parse_owner({"__typename": "Organization", "login": "oldorg", "name": "Old Org",
                              "createdAt": "2019-01-01T00:00:00Z"})
        e = gv.build_entity(repo("oldorg/arm-driver", "ROS 2 driver", owner_type="Organization"),
                            old, TODAY)
        self.assertIsNone(e.founded)
        self.assertEqual(e.kind, "project")
        self.assertIsNone(e.domain)

    def test_paper_drop_on_a_project_site_is_a_project(self):
        org = gv.parse_owner({"__typename": "Organization", "login": "RoboDojo",
                              "websiteUrl": "https://robodojo.com",
                              "createdAt": "2026-07-05T00:00:00Z"})
        r = repo("RoboDojo/RoboDojo", 'Official repository for "RoboDojo: A Unified Sim-and-Real '
                 'Benchmark for Generalist Robot Policies"', owner_type="Organization")
        self.assertEqual(gv.build_entity(r, org, TODAY).kind, "project")

    def test_person_from_user_states_only_what_the_profile_says(self):
        p = gv.person_from_user(graphql_owner_from_rest(MJ[K_USER]), "Repository owner",
                                {"commits": 11})
        self.assertEqual((p.name, p.github, p.role), ("Kevin Zakka", "kevinzakka", "Repository owner"))
        self.assertEqual(p.affiliations, [])  # company is null on the profile
        self.assertEqual(p.facts["bio"], "PhD @ UC Berkeley.")
        self.assertEqual(p.facts["followers"], 1815)
        self.assertEqual(p.facts["commits"], 11)
        self.assertEqual(p.links["twitter"], "https://x.com/kevin_zakka")
        self.assertEqual(p.links["website"], "https://kzakka.com")
        anon = gv.person_from_user({"login": "basson"}, "Top contributor (19 commits)")
        self.assertEqual((anon.name, anon.affiliations, anon.facts), ("basson", [], {}))


class SearchQueries(unittest.TestCase):
    def test_both_lenses_and_dates(self):
        ctx = Context(today=TODAY)
        qs = gv._search_queries(ctx)
        new = [q for _, lens, q, _ in qs if lens == "new"]
        older = [q for _, lens, q, _ in qs if lens == "older"]
        self.assertTrue(new and older)
        self.assertTrue(all("created:>=2026-06-03" in q or "created:>=2026-09-10" in q for q in new))
        self.assertTrue(all("created:2024-10-01..2026-06-02" in q and "pushed:>=2026-09-17" in q
                            for q in older))
        # OR may join bare keywords only; a qualifier next to OR is a 422
        for q in new + older:
            self.assertIsNone(re.search(r"\bOR (topic|stars|created|pushed):", q), q)
        self.assertTrue(all(first <= gv._NODES_PER_REQUEST for *_, first in qs))

    def test_full_run_budget_is_under_600_core_calls(self):
        self.assertLessEqual(sum(gv._BUDGET.values()), 600)


class FakeGitHub:
    """Serves the fixtures to collect(): GraphQL through post_json, REST through get_json."""

    def __init__(self, fail_graphql_over: int | None = None):
        self.search_nodes = [graphql_node_from_rest(QYM[K_REPO]), graphql_node_from_rest(MJ[K_REPO]),
                             NODES["kenchangh/kensat"], NODES["olmanqj/awesome-flight-software"]]
        self.owners = {"qymis-tech": graphql_owner_from_rest(QYM[K_ORG]),
                       "kevinzakka": graphql_owner_from_rest(MJ[K_USER])}
        self.rest_calls: list[str] = []
        self.graphql_sizes: list[int] = []
        self.fail_graphql_over = fail_graphql_over
        self.served_search = False

    def post_json(self, url, payload, **kw):
        query = payload["query"]
        aliases = re.findall(r"(\w+): (search|repositoryOwner|user)\((?:query|login):(\"(?:[^\"\\]|\\.)*\")",
                             query)
        self.graphql_sizes.append(len(aliases))
        if self.fail_graphql_over is not None and len(aliases) > self.fail_graphql_over:
            raise http.HttpError(502, url, "<html>502 Bad Gateway</html>")
        data = {}
        for alias, kind, arg in aliases:
            arg = json.loads(arg)
            if kind == "search":
                nodes = [] if self.served_search else self.search_nodes
                self.served_search = True
                data[alias] = {"repositoryCount": len(nodes), "nodes": nodes}
            else:
                node = self.owners.get(arg.lower())
                if kind == "user" and node and node["__typename"] != "User":
                    node = None
                data[alias] = node
        return {"data": data}

    def get_json(self, url, params=None, **kw):
        self.params = params
        m = re.match(r"https://api\.github\.com/repos/([^/]+/[^/]+)/(.+)$", url)
        full, what = m.group(1), m.group(2)
        self.rest_calls.append(f"{full} {what}")
        if full not in BUNDLE:
            raise http.HttpError(404, url, "Not Found")
        key = {"stargazers/history": K_HISTORY, "events": K_EVENTS, "contributors": K_CONTRIB}[what]
        return BUNDLE[full][key]


class Collect(unittest.TestCase):
    def run_collect(self, fake: FakeGitHub, limit=None):
        ctx = Context(today=TODAY, limit=limit)
        with mock.patch.object(gv.http, "post_json", fake.post_json), \
                mock.patch.object(gv.http, "get_json", fake.get_json), \
                mock.patch.object(gv, "github_token", lambda: "test-token"), \
                mock.patch.object(gv.time, "sleep", lambda s: None), \
                mock.patch("sys.stderr"):
            signals = list(gv.collect(ctx))
        return signals, ctx

    def test_end_to_end_on_fixtures(self):
        fake = FakeGitHub()
        signals, ctx = self.run_collect(fake)
        for s in signals:
            s.validate()
        by = {s.url: s for s in signals if s.kind == "star_velocity"}
        self.assertEqual(set(by), {"https://github.com/QymIs-Tech/QymCAD",
                                   "https://github.com/kevinzakka/mjbatch"})

        q = by["https://github.com/QymIs-Tech/QymCAD"]
        self.assertEqual((q.source, q.family), ("github_velocity", "github"))
        self.assertEqual(q.title, "Stars up 144 in 7 days against 8 the week before")
        self.assertEqual((q.value, q.unit), (144, "stars/7d"))
        self.assertEqual(q.occurred_at, "2026-10-01")  # the peak day
        self.assertEqual((q.entity.name, q.entity.kind, q.entity.domain),
                         ("QymIs.Tech", "company", "qymis.tech"))
        self.assertEqual(q.metrics["stars_total"], 182)
        self.assertEqual((q.metrics["stars_7d"], q.metrics["stars_prev_7d"]), (144, 8))
        self.assertEqual(q.metrics["owner_is_org"], 1)
        self.assertNotIn("stargazer_quality", q.metrics)  # 3 sampled stargazers: no judgement
        self.assertEqual(q.series[-1], {"t": "2026-10-01", "v": 144, "s": q.strength})
        self.assertTrue(0.6 <= q.strength <= 0.8)
        self.assertEqual([p.github for p in q.people], [])  # basson has no profile in the fixtures
        # the storage key is the one metric that is not a number
        self.assertEqual(q.metrics["repo"], "QymIs-Tech/QymCAD")
        self.assertTrue(all(isinstance(v, (int, float)) for k, v in q.metrics.items() if k != "repo"))
        self.assertEqual(q.metrics["thesis_anchor_only"], 0)
        self.assertGreaterEqual(q.metrics["thesis_fit"], 0.3)
        self.assertEqual(q.text, gv.signal_text(QYM_REPO, QYM_OWNER))

        m = by["https://github.com/kevinzakka/mjbatch"]
        self.assertEqual(m.title, "New repo gained 581 stars since it was created on 10 Sep 2026")
        self.assertEqual((m.entity.name, m.entity.kind, m.entity.github),
                         ("mjbatch", "project", "kevinzakka"))
        self.assertEqual(m.occurred_at, "2026-09-10")
        self.assertEqual(m.metrics["stars_total"], 581)
        self.assertEqual([(p.name, p.role) for p in m.people], [("Kevin Zakka", "Repository owner")])
        self.assertEqual(m.people[0].facts["commits"], 11)
        self.assertLess(len(m.title), 110)
        self.assertEqual(m.metrics["repo"], "kevinzakka/mjbatch")
        self.assertEqual(m.metrics["thesis_anchor_only"], 1)  # "mujoco" is its one thesis term

        # one committer on QymCAD: a new org with a domain, but not "several committers"
        self.assertEqual([s for s in signals if s.kind == "new_org_repo"], [])
        # the list repo never reached a REST call; kensat's 404 was survived and reported
        self.assertFalse(any("awesome" in c for c in fake.rest_calls))
        self.assertTrue(any("kenchangh/kensat" in w for w in ctx.warnings))

    def test_star_velocity_is_stored_as_one_row_per_repo(self):
        signals, _ = self.run_collect(FakeGitHub())
        q = next(s for s in signals if s.url.endswith("/QymCAD"))
        self.assertTrue(all("s" in p for p in q.series))  # what makes it a continuous signal
        base = fingerprint(q)
        # tomorrow's reading of the same repo lands on the same row
        later = dataclasses.replace(q, occurred_at="2026-10-02", title="Stars up 150 in 7 days",
                                    series=q.series + [{"t": "2026-10-02", "v": 150, "s": 0.7}])
        self.assertEqual(fingerprint(later), base)
        # another repo of the same owner is a row of its own
        other = dataclasses.replace(q, metrics={**q.metrics, "repo": "QymIs-Tech/QymCAM"},
                                    url="https://github.com/QymIs-Tech/QymCAM")
        self.assertEqual(other.entity.domain, q.entity.domain)
        self.assertNotEqual(fingerprint(other), base)

    def test_new_org_repo_needs_two_committers(self):
        fake = FakeGitHub()
        second = dict(QYM[K_CONTRIB][0], login="second-dev", contributions=7)
        with mock.patch.dict(QYM, {K_CONTRIB: QYM[K_CONTRIB] + [second]}):
            signals, _ = self.run_collect(fake)
        new_org = [s for s in signals if s.kind == "new_org_repo"]
        self.assertEqual(len(new_org), 1)
        s = new_org[0]
        s.validate()
        self.assertEqual(s.title,
                         "Organization created 9 Aug 2026 published QymCAD with 2 contributors and 182 stars")
        self.assertEqual(s.occurred_at, "2026-08-25")  # the repo's creation date
        self.assertEqual((s.value, s.unit), (2, "contributors"))
        self.assertEqual(s.entity.kind, "company")
        self.assertIsNone(s.entity.founded)
        self.assertEqual(s.metrics["stars_total"], 182)
        self.assertEqual(s.series, [])
        self.assertTrue(0.25 <= s.strength <= 0.6)
        self.assertEqual(s.metrics["thesis_anchor_only"], 0)
        self.assertNotIn("repo", s.metrics)  # a point event: the evidence url and day identify it

    def test_graphql_502_is_retried_in_halves(self):
        fake = FakeGitHub(fail_graphql_over=3)
        signals, ctx = self.run_collect(fake)
        self.assertEqual(len([s for s in signals if s.kind == "star_velocity"]), 2)
        self.assertTrue(any(n > 3 for n in fake.graphql_sizes))      # a batch was refused
        self.assertFalse([w for w in ctx.warnings if "GraphQL" in w or "batch failed" in w])

    def test_rate_limited_rest_stops_calling_instead_of_waiting_per_repo(self):
        fake = FakeGitHub()
        base = graphql_node_from_rest(MJ[K_REPO])
        fake.search_nodes = [dict(base, nameWithOwner=f"dev{i}/robot-arm-{i}",
                                  url=f"https://github.com/dev{i}/robot-arm-{i}",
                                  description="ROS 2 driver for a robot arm",
                                  owner={"__typename": "User", "login": f"dev{i}"})
                             for i in range(40)]

        def limited(url, params=None, **kw):
            fake.rest_calls.append(url)
            raise http.HttpError(403, url, '{"message": "API rate limit exceeded for user ID 1."}')

        fake.get_json = limited
        signals, ctx = self.run_collect(fake)
        self.assertEqual(signals, [])
        self.assertLess(len(fake.rest_calls), 10)  # not 40: the breaker opened
        self.assertEqual(len([w for w in ctx.warnings if "REST is rate limited" in w]), 1)
        # and a later run starts clean
        signals, _ = self.run_collect(FakeGitHub())
        self.assertEqual(len([s for s in signals if s.kind == "star_velocity"]), 2)

    def test_rate_limited_graphql_stops_after_two_refusals(self):
        fake = FakeGitHub()
        calls: list[int] = []

        def refused(url, payload, **kw):
            calls.append(1)
            raise http.HttpError(403, url, '{"message": "You have exceeded a secondary rate limit."}')

        fake.post_json = refused
        signals, ctx = self.run_collect(fake)
        self.assertEqual(signals, [])
        self.assertEqual(len(calls), 2)  # not three tries for each of thirty-odd search batches
        self.assertEqual(len([w for w in ctx.warnings if "GraphQL is rate limited" in w]), 1)
        self.assertEqual(fake.rest_calls, [])

    def test_a_403_that_is_not_a_rate_limit_does_not_open_the_breaker(self):
        fake = FakeGitHub()
        real = fake.get_json

        def blocked_contributors(url, params=None, **kw):
            if url.endswith("/contributors"):
                raise http.HttpError(403, url, '{"message": "The history or contributor list is too '
                                               'large to list contributors for this repository"}')
            return real(url, params=params, **kw)

        fake.get_json = blocked_contributors
        signals, ctx = self.run_collect(fake)
        self.assertEqual(len([s for s in signals if s.kind == "star_velocity"]), 2)
        self.assertFalse([w for w in ctx.warnings if "rate limited" in w])

    def test_high_velocity_repo_is_held_back_when_its_stargazers_cannot_be_checked(self):
        fake = FakeGitHub()
        real = fake.get_json

        def no_events(url, params=None, **kw):
            if url.endswith("/events"):
                raise http.HttpError(500, url, "Server Error")
            return real(url, params=params, **kw)

        fake.get_json = no_events
        signals, ctx = self.run_collect(fake)
        self.assertEqual(signals, [])  # both fixture repos are high-velocity
        self.assertTrue(any("held back" in w for w in ctx.warnings))

    def test_graphql_200_without_data_is_asked_again_under_a_new_cache_key(self):
        fake = FakeGitHub()
        real = fake.post_json
        queries: list[str] = []

        def flaky(url, payload, **kw):
            queries.append(payload["query"])
            if "#retry" not in payload["query"] and "search(" in payload["query"] and len(queries) == 1:
                return {"data": None, "errors": [{"type": "RATE_LIMITED", "message": "API rate limit"}]}
            return real(url, payload, **kw)

        fake.post_json = flaky
        signals, ctx = self.run_collect(fake)
        self.assertEqual(len([s for s in signals if s.kind == "star_velocity"]), 2)
        self.assertIn("#retry", queries[1])
        self.assertEqual(queries[1].split(" #retry")[0], queries[0])

    def test_partial_graphql_errors_are_reported_once(self):
        fake = FakeGitHub()
        real = fake.post_json

        def partial(url, payload, **kw):
            out = real(url, payload, **kw)
            if "search(" in payload["query"]:
                out["errors"] = [{"type": "TIMEOUT", "path": ["s3"]},
                                 {"type": "NOT_FOUND", "path": ["u1"]}]
            return out

        fake.post_json = partial
        signals, ctx = self.run_collect(fake)
        self.assertEqual(len([s for s in signals if s.kind == "star_velocity"]), 2)
        reported = [w for w in ctx.warnings if "partial errors" in w]
        self.assertEqual(len(reported), 1)
        self.assertIn("TIMEOUT", reported[0])
        self.assertNotIn("NOT_FOUND", reported[0])

    def test_one_malformed_search_node_does_not_lose_the_batch(self):
        fake = FakeGitHub()
        fake.search_nodes = [{"nameWithOwner": "x/y", "createdAt": 20260901, "owner": "x"},
                             "not a node", *fake.search_nodes]
        signals, ctx = self.run_collect(fake)
        self.assertEqual(len([s for s in signals if s.kind == "star_velocity"]), 2)
        self.assertTrue(any("search node skipped" in w for w in ctx.warnings))

    def test_limit_caps_rest_calls(self):
        fake = FakeGitHub()
        self.run_collect(fake, limit=1)
        history = [c for c in fake.rest_calls if c.endswith("stargazers/history")]
        self.assertLessEqual(len(history), 12)

    def test_no_token_collects_nothing(self):
        ctx = Context(today=TODAY)
        with mock.patch.object(gv, "github_token", lambda: None), mock.patch("sys.stderr"):
            self.assertEqual(list(gv.collect(ctx)), [])
        self.assertEqual(len(ctx.warnings), 1)


if __name__ == "__main__":
    unittest.main()
