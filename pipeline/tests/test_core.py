"""Offline tests for the shared core: thesis fit, resolution, scoring."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from antenna.collectors.base import (
    clean_domain,
    display_name,
    loose_name,
    normalize_name,
    parse_date,
    squash,
)
from antenna.db import connect, insert_signals
from antenna.models import EntityHint, Person, Signal
from antenna.resolve import resolve
from antenna.score import (
    edge_of,
    noisy_or,
    pedigree_hits,
    score_all,
    score_entity,
    strength_asof,
)
from antenna.thesis import classify

TODAY = date(2026, 10, 1)


def sig(name, *, family="capital", source="sec_form_d", kind="form_d", when="2026-09-20",
        strength=0.6, url=None, domain=None, github=None, text=None, metrics=None, people=None,
        series=None, one_liner=None):
    return Signal(
        source=source, family=family, kind=kind,
        entity=EntityHint(name=name, domain=domain, github=github, one_liner=one_liner),
        title=f"{kind} for {name}", occurred_at=when,
        url=url or f"https://example.org/{source}/{name.replace(' ', '-')}/{when}",
        strength=strength, text=text, metrics=metrics or {}, people=people or [], series=series or [],
    )


class Helpers(unittest.TestCase):
    def test_clean_domain(self):
        self.assertEqual(clean_domain("https://www.Skydio.com/careers"), "skydio.com")
        self.assertIsNone(clean_domain("https://github.com/acme"))
        self.assertIsNone(clean_domain("acme.github.io"))
        self.assertIsNone(clean_domain(""))
        self.assertIsNone(clean_domain("not a domain"))
        self.assertIsNone(clean_domain("robotics.stanford.edu"))
        self.assertIsNone(clean_domain("https://jobs.ashbyhq.com/acme"))
        self.assertIsNone(clean_domain("demo.pages.dev"))

    def test_normalize_name(self):
        # Legal forms go; descriptive words stay, so look-alikes do not merge.
        self.assertEqual(normalize_name("Saronic Technologies, Inc."), "saronic technologies")
        self.assertEqual(normalize_name("SALEM ROBOTICS INC"), normalize_name("Salem Robotics, Inc."))
        self.assertNotEqual(normalize_name("Genesis AI"), normalize_name("Genesis Robotics"))
        self.assertEqual(normalize_name("Terra Innovatum s.r.l"), "terra innovatum")
        self.assertEqual(normalize_name("Advanced Float Co., Ltd"), "advanced float")
        self.assertEqual(loose_name("Anduril Industries, Inc."), "anduril")

    def test_display_name(self):
        self.assertEqual(display_name("VALINOR DISPATCH, INC."), "Valinor Dispatch")
        self.assertEqual(display_name("Parallel Robotics, Inc."), "Parallel Robotics")
        self.assertEqual(display_name("DRONENX LLC"), "DRONENX")  # one caps word: do not guess
        self.assertEqual(display_name("3D GLASS SOLUTIONS, INC."), "3D Glass Solutions")

    def test_parse_date(self):
        self.assertEqual(parse_date("2026-09-18T12:00:00Z"), date(2026, 9, 18))
        self.assertEqual(parse_date("09/18/2026"), date(2026, 9, 18))
        self.assertEqual(parse_date("Jun 18 2026"), date(2026, 6, 18))
        self.assertIsNone(parse_date("soon"))

    def test_squash(self):
        self.assertEqual(squash(0, 10), 0)
        self.assertAlmostEqual(squash(10, 10), 0.5)
        self.assertLess(squash(1000, 10), 1)


class Thesis(unittest.TestCase):
    def test_on_thesis(self):
        c = classify("Open-source autopilot for BVLOS drone swarms, built on PX4")
        self.assertEqual(c["sector"], "autonomy")
        self.assertGreater(c["fit"], 0.8)
        self.assertEqual(classify("Compact stellarator fusion power plant")["sector"], "energy")

    def test_multi_word_terms_and_plurals(self):
        for text in ["physical ai", "power electronics", "launch vehicles", "cubesats",
                     "sim to real transfer for a legged robot", "counter UAS radar"]:
            self.assertGreaterEqual(classify(text)["fit"], 0.3, text)

    def test_lone_ambiguous_word_stays_under_the_gate(self):
        for text in ["Fusion Data Centers Inc.", "Scoring Factory AI Inc."]:
            self.assertLess(classify(text)["fit"], 0.3, text)
        self.assertGreaterEqual(classify("Compact fusion power plant using a stellarator")["fit"], 0.5)

    def test_false_friends(self):
        for text in ["sensor fusion library for home assistant", "Hydrogen peroxide vapor sterilization",
                     "CSS grid layout helper", "solar wind forecasting"]:
            self.assertEqual(classify(text)["fit"], 0.0, text)

    def test_software_lookalikes_stay_out(self):
        for text in ["A CRM for dentists", "LLM agents for sales teams",
                     "Exploratory data analysis (EDA) tooling for RTL text",
                     "A transformer model with a utility payload"]:
            self.assertLess(classify(text)["fit"], 0.3, text)


class Scoring(unittest.TestCase):
    def test_noisy_or(self):
        self.assertEqual(noisy_or([]), 0)
        self.assertAlmostEqual(noisy_or([0.5, 0.5]), 0.75)

    def test_decay_and_future(self):
        s = {"family": "capital", "series": [], "occurred_at": "2026-09-01", "strength": 0.8}
        self.assertAlmostEqual(strength_asof(s, date(2026, 9, 1)), 0.8)
        self.assertAlmostEqual(strength_asof(s, date(2026, 10, 16)), 0.4, places=2)  # one half-life
        self.assertEqual(strength_asof(s, date(2026, 8, 1)), 0.0)  # had not happened yet

    def test_series_strength(self):
        s = {"family": "github", "occurred_at": "2026-10-01", "strength": 0.9,
             "series": [{"t": "2026-09-01", "v": 5, "s": 0.2}, {"t": "2026-09-29", "v": 90, "s": 0.9}]}
        self.assertAlmostEqual(strength_asof(s, date(2026, 9, 1)), 0.2)
        self.assertGreater(strength_asof(s, TODAY), 0.8)
        self.assertEqual(strength_asof(s, date(2026, 8, 1)), 0.0)

    def test_embargoed_record_decays_from_release(self):
        s = {"family": "capital", "series": [], "occurred_at": "2026-06-01", "strength": 0.8,
             "metrics": {"public_at": "2026-08-30"}}
        self.assertEqual(strength_asof(s, date(2026, 8, 1)), 0.0)  # signed, but not yet visible
        self.assertAlmostEqual(strength_asof(s, date(2026, 8, 30)), 0.8)

    def test_pedigree_needs_a_real_affiliation(self):
        self.assertEqual([o for o, _ in pedigree_hits("SpaceX")], ["SpaceX"])
        self.assertEqual([o for o, _ in pedigree_hits("Previously led avionics at SpaceX and built two companies before that, one of which was acquired in 2021.")], ["SpaceX"])
        self.assertEqual(pedigree_hits("Built a six figure content business and won a hackathon with $17M in DARPA sponsorship behind the lab's research programme."), [])

    def test_edge_terms(self):
        self.assertGreater(edge_of(0.8, 0.9, 0.9, 0.5), edge_of(0.8, 0.9, 0.3, 0.5))  # earlier is better
        self.assertGreater(edge_of(0.8, 0.9, 0.9, 0.5), edge_of(0.8, 0.2, 0.9, 0.5))  # on thesis is better
        self.assertLessEqual(edge_of(1, 1, 1, 1), 100)
        # One family alone is marked down against the same numbers corroborated.
        self.assertLess(edge_of(0.6, 0.9, 0.9, 0.2, families=1), edge_of(0.6, 0.9, 0.9, 0.2, families=2))

    def test_convergence_beats_one_loud_family(self):
        ent = {"name": "Acme Drones", "one_liner": "Autonomous drone interceptors for defense", "description": None, "founded": None}
        one = [dict(family="capital", source="a", kind="k", occurred_at="2026-09-28", strength=1.0,
                    series=[], metrics={}, people=[], title="t", text=None)]
        three = [dict(family=f, source="a", kind="k", occurred_at="2026-09-28", strength=0.6,
                      series=[], metrics={}, people=[], title="t", text=None)
                 for f in ("capital", "regulatory", "hiring")]
        self.assertGreater(score_entity(ent, three, TODAY)["momentum"], score_entity(ent, one, TODAY)["momentum"])
        self.assertEqual(score_entity(ent, three, TODAY)["convergence"], 3)

    def test_consensus_discounts_known_companies(self):
        ent = {"name": "Acme Robotics", "one_liner": "Humanoid robots", "description": None, "founded": "2016"}
        base = dict(family="github", source="a", kind="k", occurred_at="2026-09-28", strength=0.7,
                    series=[], people=[], title="t", text=None)
        quiet = score_entity(ent | {"founded": "2026"}, [base | {"metrics": {"hn_mentions_total": 1, "open_roles": 4}}], TODAY)
        unmeasured = score_entity(ent | {"founded": None}, [base | {"metrics": {}}], TODAY)
        loud = score_entity(ent, [base | {"metrics": {"stars_total": 40000, "hn_mentions_total": 500, "tranco_rank": 20000}}], TODAY)
        self.assertGreater(quiet["earliness"], 0.9)
        self.assertLess(loud["earliness"], 0.2)
        self.assertGreater(quiet["edge"], loud["edge"])
        # Nothing measured is not the same as nothing there.
        self.assertAlmostEqual(unmeasured["earliness"], 0.8)
        self.assertIn("unmeasured", unmeasured["consensus_parts"])

    def test_seed_stage_facts_are_not_consensus(self):
        ent = {"name": "Acme Atomics", "one_liner": "Nuclear microreactor", "description": None, "founded": "2025"}
        s = dict(family="capital", source="a", kind="k", occurred_at="2026-09-28", strength=0.7, series=[],
                 people=[], title="t", text=None,
                 metrics={"amount_sold": 12_000_000, "open_roles": 9, "tranco_rank": 2_400_000, "hn_mentions_total": 3})
        self.assertGreater(score_entity(ent, [s], TODAY)["earliness"], 0.95)


class Resolution(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.tmp.name) / "t.db")
        self.run = self.conn.execute("INSERT INTO runs (started_at) VALUES ('x')").lastrowid

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def names(self):
        return sorted(r["name"] for r in self.conn.execute("SELECT name FROM entities"))

    def test_merges_on_domain_github_and_name(self):
        insert_signals(self.conn, self.run, [
            sig("Salem Robotics, Inc.", text="industrial inspection robots"),
            sig("Salem Robotics", source="hn_launch", family="launch", kind="launch_hn", domain="salemrobotics.com"),
            sig("salem", source="github_velocity", family="github", kind="star_velocity",
                domain="https://www.salemrobotics.com/", github="Salem-Robotics"),
            sig("Other Works", domain="other.co"),
        ])
        self.assertEqual(resolve(self.conn), 2)
        self.assertEqual(self.names(), ["Other Works", "Salem Robotics"])
        row = self.conn.execute("SELECT * FROM entities WHERE domain='salemrobotics.com'").fetchone()
        self.assertEqual(row["github"], "salem-robotics")
        n = self.conn.execute("SELECT COUNT(*) FROM signals WHERE entity_id=?", (row["id"],)).fetchone()[0]
        self.assertEqual(n, 3)

    def test_same_name_different_domains_stay_apart(self):
        insert_signals(self.conn, self.run, [
            sig("Atlas Fusion", domain="atlasfusion.com"),
            sig("Atlas Fusion", domain="atlas-fusion.de", source="research_affil", family="research"),
        ])
        self.assertEqual(resolve(self.conn), 2)

    def test_weak_names_never_merge(self):
        insert_signals(self.conn, self.run, [
            sig("Robotics Inc", url="https://example.org/a"),
            sig("Robotics LLC", source="fcc_els", family="regulatory", url="https://example.org/b"),
        ])
        self.assertEqual(resolve(self.conn), 2)

    def test_rerun_is_idempotent_and_scores(self):
        batch = [
            sig("Acme Drones", text="autonomous drone interceptor for counter-UAS defense",
                people=[Person(name="Jo Doe", role="Executive Officer", affiliations=["SpaceX"])]),
            sig("Acme Drones", source="fcc_els", family="regulatory", kind="fcc_sta", when="2026-09-25"),
        ]
        self.assertEqual(insert_signals(self.conn, self.run, batch), (2, 2))
        self.assertEqual(insert_signals(self.conn, self.run, batch), (2, 0))
        self.assertEqual(resolve(self.conn), 1)
        self.assertEqual(score_all(self.conn, self.run, TODAY), 1)
        row = self.conn.execute("SELECT * FROM scores").fetchone()
        self.assertGreater(row["edge"], 20)
        self.assertEqual(row["sector"], "autonomy")


if __name__ == "__main__":
    unittest.main()
