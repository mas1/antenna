"""Offline tests for the tranco_rank collector.

No network. Parsing runs against the saved fixtures (the per-domain history
for skild.ai, the list metadata, and the 69-domain panel of real ranks on
three dates). The streaming scan and the end-to-end run use small synthetic
lists written to a temporary directory, with antenna.http swapped for a stub
that fails the test if any unexpected URL is asked for.
"""

from __future__ import annotations

import json
import tempfile
import unittest
import zlib
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from antenna import http
from antenna.collectors import tranco_rank as tr
from antenna.collectors.base import Context
from antenna.db import connect, fingerprint, insert_signals
from antenna.models import FAMILIES
from antenna.score import score_entity
from antenna.thesis import classify

FIX = Path(__file__).resolve().parents[1] / "fixtures"
TODAY = date(2026, 10, 1)

BUNDLE = json.loads((FIX / "traffic" / "domain_probe_bundle.json").read_text())
SKILD = json.loads((FIX / "traffic" / "tranco_ranks_domain_skild.ai.json").read_text())["response"]
KNOWN = json.loads((FIX / "known_sample.json").read_text())
PANEL = {row["domain"]: row["tranco_full_rank"] for row in BUNDLE["panel_69_domains"]}

# The three lists the panel was measured on (ids from the source card).
P_NEW = tr.TrancoList("Q2K34", date(2026, 9, 30))
P_30 = tr.TrancoList("K9QPW", date(2026, 9, 1))
P_90 = tr.TrancoList("JZ2VY", date(2026, 7, 1))
P_ROWS = 4_566_870


def ctx(**kw) -> Context:
    c = Context(today=kw.pop("today", TODAY), **kw)
    c.warn = lambda msg: c.warnings.append(msg)  # type: ignore[method-assign]
    c.log = lambda msg: None  # type: ignore[method-assign]
    return c


def panel_signal(domain: str, history=None, name: str | None = None):
    """The signal the collector builds for a panel domain, on the card's static norms."""
    r = PANEL[domain]
    now = r["2026-09-30"]
    assert now is not None, domain
    w30 = tr.make_window(30, P_NEW, P_30, now, r["2026-09-01"], None)
    w90 = tr.make_window(90, P_NEW, P_90, now, r["2026-07-01"], None)
    ent = {"name": name or domain, "kind": "company", "domain": domain}
    return tr.build_signal(ctx(), domain, ent, now, P_NEW, P_ROWS, w30, w90, None, history)


class Module(unittest.TestCase):
    def test_contract_constants(self):
        self.assertEqual(tr.SLUG, "tranco_rank")
        self.assertEqual(Path(tr.__file__).stem, tr.SLUG)
        self.assertIn(tr.FAMILY, FAMILIES)
        self.assertEqual(tr.STAGE, "enrich")
        self.assertTrue(tr.__doc__ and "Tranco" in tr.__doc__ and "early" in tr.__doc__)


class Domains(unittest.TestCase):
    def test_lookup_is_exact_and_normalised(self):
        self.assertEqual(tr.lookup_domain("skild.ai"), "skild.ai")
        self.assertEqual(tr.lookup_domain("https://www.Skild.AI/careers"), "skild.ai")
        self.assertEqual(tr.lookup_domain("physicalintelligence.company."), "physicalintelligence.company")
        # A subdomain is looked up as it is, never as its parent.
        self.assertEqual(tr.lookup_domain("docs.hadrian.co"), "docs.hadrian.co")

    def test_non_company_hosts_are_refused(self):
        for raw in [None, "", 42, "not a domain", "github.com", "acme.github.io", "pages.dev",
                    "hf.space", "mit.edu", "ox.ac.uk", "navy.mil", "nasa.gov", "bad_host.com",
                    "localhost",
                    # Shared hosts base.clean_domain knows, and their tenants.
                    "acme.pages.dev", "itch.io", "acme.itch.io", "kickstarter.com", "railway.app",
                    "jobs.ashbyhq.com", "wellfound.com",
                    # Shared hosts and directories only this collector knows.
                    "myshopify.com", "blogspot.com", "appspot.com", "amazonaws.com", "wix.com",
                    "pitchbook.com", "researchgate.net",
                    # Institutions beyond the suffixes clean_domain lists.
                    "who.int", "iitb.ac.in", "jaxa.go.jp", "nus.edu.sg", "service.gov.uk"]:
            self.assertIsNone(tr.lookup_domain(raw), raw)
        # A tenant of a host on this collector's own list is looked up as it is. Tranco
        # ranks registrable domains only, so it is never found and never reads the host's rank.
        self.assertEqual(tr.lookup_domain("acme.myshopify.com"), "acme.myshopify.com")

    def test_idn_goes_to_punycode(self):
        self.assertEqual(tr.lookup_domain("bücher.de"), "xn--bcher-kva.de")

    def test_name_carried_by_domain(self):
        for name, domain in [
            ("Anduril", "anduril.com"), ("Skild AI", "skild.ai"), ("1X", "1x.tech"),
            ("Physical Intelligence", "physicalintelligence.company"), ("Figure", "figure.ai"),
            ("Base Power", "basepowercompany.com"), ("Firestorm Labs", "launchfirestorm.com"),
            ("Space Exploration Technologies", "spacex.com"), ("Allen Control Systems", "acs.com"),
            ("robocurve", "robocurve.org"), ("Aalo Atomics", "aalo.com"), ("K2 Space", "k2space.com"),
        ]:
            self.assertTrue(tr.looks_like_own_domain(name, domain), (name, domain))
        for name, domain in [
            ("Acme Robotics", "calendly.com"), ("Tiny Drone Labs", "typeform.com"),
            ("The Fusion Company", "gumroad.com"), ("", "anduril.com"), ("Vernius Systems", "box.com"),
            # Short labels are inside almost any name; they must match a whole word or the initials.
            ("Acme Robotics", "a.co"), ("Blue Laser Fusion", "lu.ma"), ("Swarm Aero", "wa.me"),
            ("Scalable Robotics", "cal.com"), ("Isomorphic Labs", "is.gd"), ("Goodyear", "goo.gl"),
            ("Apex Space", "x.ai"), ("Form Energy", "typeform.com"), ("Charge Robotics", "ashbyhq.com"),
            ("Bloom Energy", "loom.com"),
        ]:
            self.assertFalse(tr.looks_like_own_domain(name, domain), (name, domain))
        for name, domain in [("Apex", "apexspace.com"), ("Zipline", "flyzipline.com"), ("RobCo", "rob.co"),
                             ("Commonwealth Fusion Systems", "cfs.energy"), ("X-energy", "x-energy.com"),
                             ("Rune Technologies", "runetech.co"), ("The Boring Company", "boringcompany.com"),
                             ("Avalanche Energy", "avalanchefusion.com"), ("Helion", "helionenergy.com")]:
            self.assertTrue(tr.looks_like_own_domain(name, domain), (name, domain))
        # Every named entity in the shared sample passes on its own domain.
        for dom, ent in tr.known_targets(KNOWN):
            self.assertTrue(tr.looks_like_own_domain(ent["name"], dom), (ent["name"], dom))

    def test_known_targets_from_fixture(self):
        targets = tr.known_targets(KNOWN)
        with_domain = [k for k in KNOWN if k.get("domain")]
        self.assertEqual(len(targets), len(with_domain))
        self.assertEqual(len(targets), 19)
        self.assertEqual(targets[0][0], "skild.ai")
        self.assertEqual(targets[0][1]["name"], "Skild AI")
        self.assertNotIn("Salem Robotics", [e["name"] for _, e in targets])  # no domain
        self.assertEqual(len(tr.known_targets(KNOWN, limit=5)), 5)

    def test_hint_carries_the_known_entitys_own_keys(self):
        # The resolver merges on domain, then GitHub login. The hint must carry the
        # known entity's keys as every other collector writes them.
        h = tr.entity_hint({"name": " robocurve ", "kind": "project", "domain": "robocurve.org",
                            "github": "robocurve"}, "robocurve.org")
        self.assertEqual((h.name, h.kind, h.domain, h.github), ("robocurve", "project", "robocurve.org", "robocurve"))
        # Looked up in punycode, reported under the domain the entity is known by.
        self.assertEqual(tr.entity_hint({"name": "Bücher", "domain": "bücher.de"}, "xn--bcher-kva.de").domain,
                         "bücher.de")
        self.assertEqual(tr.entity_hint({"name": "Skild AI", "domain": "https://www.Skild.AI/careers"},
                                        "skild.ai").domain, "skild.ai")
        # A login, never a URL; anything else is dropped rather than guessed at.
        for bad in [None, "", 7, "https://github.com/skild-ai", "skild-ai/repo", "skild ai", "-x"]:
            self.assertIsNone(tr.entity_hint({"name": "Skild AI", "domain": "skild.ai", "github": bad},
                                             "skild.ai").github, bad)
        self.assertEqual(tr.github_login(" hebbian-robotics "), "hebbian-robotics")

    def test_known_targets_dedupes_and_skips_junk(self):
        known = [
            {"name": "A", "domain": "a.example"},
            {"name": "A again", "domain": "https://www.a.example/"},
            {"name": "", "domain": "nameless.example"},
            {"name": "No domain", "domain": None},
            "not a dict",
            {"name": "Jane Researcher", "kind": "person", "domain": "janeresearcher.example"},
            {"name": "B", "domain": "b.example"},
        ]
        self.assertEqual([(d, e["name"]) for d, e in tr.known_targets(known)],
                         [("a.example", "A"), ("b.example", "B")])
        self.assertEqual(tr.known_targets([]), [])


class Meta(unittest.TestCase):
    def test_fixture_meta(self):
        lst = tr.parse_meta(BUNDLE["tranco_list_meta"]["response"])
        self.assertEqual(lst, tr.TrancoList("Q2K34", date(2026, 9, 30)))

    def test_unusable_meta(self):
        good = BUNDLE["tranco_list_meta"]["response"]
        self.assertIsNone(tr.parse_meta({"available": False}))
        self.assertIsNone(tr.parse_meta({**good, "failed": True}))
        self.assertIsNone(tr.parse_meta({**good, "list_id": "../etc"}))
        self.assertIsNone(tr.parse_meta({**good, "list_id": None}))
        self.assertIsNone(tr.parse_meta([good]))
        self.assertIsNone(tr.parse_meta({**good, "configuration": {}, "created_on": None}))

    def test_date_falls_back_to_created_on(self):
        good = BUNDLE["tranco_list_meta"]["response"]
        lst = tr.parse_meta({**good, "configuration": {}})
        self.assertEqual(lst.day, date(2026, 9, 30))

    def test_file_name_carries_date_and_id(self):
        with mock.patch.object(tr, "DOWNLOADS_DIR", Path("/x")):
            self.assertEqual(tr.list_path(P_NEW), Path("/x/tranco_full_2026-09-30_Q2K34.csv"))


class History(unittest.TestCase):
    def test_fixture_history(self):
        pts = tr.parse_history(SKILD, "skild.ai")
        self.assertEqual(len(pts), 39)
        self.assertEqual(pts[0], (date(2026, 8, 24), 1166698))
        self.assertEqual(pts[-1], (date(2026, 10, 1), 442601))
        self.assertEqual(dict(pts)[date(2026, 9, 30)], 449531)  # the list-file value in the card
        self.assertEqual(pts, sorted(pts))

    def test_wrong_domain_or_junk(self):
        self.assertEqual(tr.parse_history(SKILD, "other.example"), [])
        self.assertEqual(tr.parse_history({"domain": "x.example", "ranks": []}, "x.example"), [])
        self.assertEqual(tr.parse_history(None), [])
        body = {"ranks": [{"date": "2026-09-30", "rank": 10}, {"date": "nope", "rank": 5},
                          {"date": "2026-09-29", "rank": "7"}, {"date": "2026-09-28", "rank": 0},
                          {"date": "2026-09-27", "rank": True}, "x", {"date": "2026-09-26", "rank": 12}]}
        self.assertEqual(tr.parse_history(body), [(date(2026, 9, 26), 12), (date(2026, 9, 30), 10)])

    def test_clean_entry(self):
        pts = [(date(2026, 9, 28), 900), (date(2026, 9, 29), 800), (date(2026, 9, 30), 700),
               (date(2026, 10, 1), 600), (date(2026, 10, 2), 500)]  # the API can run a day ahead
        e = tr.entry_from_history(pts, after=date(2026, 9, 1), upto=date(2026, 10, 1))
        self.assertEqual((e.day, e.rank, e.days_listed, e.span, e.relisted),
                         (date(2026, 9, 28), 900, 4, 4, False))

    def test_entry_with_gaps(self):
        pts = [(date(2026, 9, 22), 900), (date(2026, 9, 30), 700), (date(2026, 10, 1), 600)]
        e = tr.entry_from_history(pts, after=date(2026, 9, 1), upto=date(2026, 10, 1))
        self.assertEqual((e.day, e.days_listed, e.span, e.relisted), (date(2026, 9, 22), 3, 10, False))

    def test_listed_before_the_30_day_list_is_a_relisting(self):
        pts = [(date(2026, 8, 26), 900), (date(2026, 9, 20), 800), (date(2026, 10, 1), 600)]
        e = tr.entry_from_history(pts, after=date(2026, 9, 1), upto=date(2026, 10, 1))
        self.assertTrue(e.relisted)
        # Two listed days since the 30-day list, but not in a row: "since" is the last one.
        self.assertEqual((e.day, e.rank, e.days_listed), (date(2026, 10, 1), 600, 2))
        run = [(date(2026, 8, 26), 900), (date(2026, 9, 5), 850)] + \
              [(date(2026, 9, 20) + timedelta(days=k), 800 - k) for k in range(12)]
        e = tr.entry_from_history(run, after=date(2026, 9, 1), upto=date(2026, 10, 1))
        self.assertTrue(e.relisted)
        self.assertEqual((e.day, e.rank, e.days_listed), (date(2026, 9, 20), 800, 13))
        # skild.ai was listed all along: if the lists had missed it, the history would say so.
        e = tr.entry_from_history(tr.parse_history(SKILD), after=date(2026, 9, 1), upto=date(2026, 10, 1))
        self.assertTrue(e.relisted)

    def test_no_usable_points(self):
        self.assertIsNone(tr.entry_from_history([], date(2026, 9, 1), date(2026, 10, 1)))
        self.assertIsNone(tr.entry_from_history([(date(2026, 10, 2), 5)], date(2026, 9, 1), date(2026, 10, 1)))
        self.assertIsNone(tr.entry_from_history([(date(2026, 8, 30), 5)], date(2026, 9, 1), date(2026, 10, 1)))


class HistoryFetch(unittest.TestCase):
    def fetch(self, seconds: float, requests_after: int):
        slept: list[float] = []
        urls: list[str] = []
        clock = [100.0, 100.0 + seconds]
        counts = [{"requests": 5}, {"requests": requests_after}]

        def get_json(url: str, **kw):
            urls.append(url)
            return SKILD

        with mock.patch.object(tr.http, "get_json", get_json), \
                mock.patch.object(tr.http, "stats", lambda: counts.pop(0) if len(counts) > 1 else counts[0]), \
                mock.patch.object(tr.time, "monotonic", lambda: clock.pop(0) if len(clock) > 1 else clock[0]), \
                mock.patch.object(tr.time, "sleep", slept.append):
            pts = tr.fetch_history("skild.ai")
        self.assertEqual(urls, ["https://tranco-list.eu/api/ranks/domain/skild.ai"])
        self.assertEqual(len(pts), 39)
        return slept

    def test_real_request_is_spaced_to_the_cards_limit(self):
        slept = self.fetch(seconds=0.4, requests_after=6)
        self.assertEqual(len(slept), 1)
        self.assertAlmostEqual(slept[0], tr.API_SPACING - 0.4)
        self.assertGreaterEqual(tr.API_SPACING, 2.0)  # card: 429s at 1.2 s spacing, none at 2.2 s

    def test_cache_hit_does_not_wait(self):
        self.assertEqual(self.fetch(seconds=0.002, requests_after=5), [])
        # Another collector's request moved the shared counter; ours was still a cache hit.
        self.assertEqual(self.fetch(seconds=0.002, requests_after=9), [])

    def test_slow_request_needs_no_extra_wait(self):
        self.assertEqual(self.fetch(seconds=9.0, requests_after=8), [0.0])


def write_list(path: Path, domains: list[str], eol: bytes = b"\r\n") -> dict[str, int]:
    """Write `rank,domain` rows the way Tranco does; return domain -> rank."""
    with open(path, "wb") as fh:
        for i, d in enumerate(domains, 1):
            fh.write(f"{i},{d}".encode() + eol)
    return {d: i for i, d in enumerate(domains, 1)}


class Scan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.domains = [f"site{i:05d}.example" for i in range(5000)]
        self.domains[1233] = "skild.ai"
        self.domains[4998] = "valaratomics.com"

    def test_wanted_sample_and_rows(self):
        path = self.dir / "list.csv"
        truth = write_list(path, self.domains)
        scan = tr.scan_list(path, frozenset({b"skild.ai", b"valaratomics.com", b"thea.energy"}), min_rows=1000)
        self.assertEqual(scan.rows, 5000)
        self.assertEqual(scan.ranks, {"skild.ai": 1234, "valaratomics.com": 4999})
        expected = {d.encode(): r for d, r in truth.items() if not zlib.crc32(d.encode()) & tr.SAMPLE_MASK}
        self.assertEqual(scan.sample, expected)
        self.assertTrue(40 < len(scan.sample) < 130)  # about 1 in 64

    def test_same_domains_sampled_in_every_list(self):
        a, b = self.dir / "a.csv", self.dir / "b.csv"
        write_list(a, self.domains)
        write_list(b, list(reversed(self.domains)), eol=b"\n")
        sa = tr.scan_list(a, frozenset(), min_rows=1000)
        sb = tr.scan_list(b, frozenset(), min_rows=1000)
        self.assertEqual(set(sa.sample), set(sb.sample))
        self.assertEqual(tr.scan_list(b, frozenset({b"skild.ai"}), min_rows=1000).ranks, {"skild.ai": 5000 - 1233})

    def test_exact_match_only(self):
        path = self.dir / "list.csv"
        write_list(path, self.domains)
        scan = tr.scan_list(path, frozenset({b"kild.ai", b"www.skild.ai", b"skild.a"}), min_rows=1000)
        self.assertEqual(scan.ranks, {})

    def test_short_list_is_refused(self):
        path = self.dir / "list.csv"
        write_list(path, self.domains)
        with self.assertRaises(tr.ListError):
            tr.scan_list(path, frozenset())  # default floor: a real full list has millions of rows
        with self.assertRaises(tr.ListError):
            tr.scan_list(path, frozenset(), min_rows=5001)
        self.assertGreaterEqual(tr.MIN_FULL_ROWS, 3_000_000)
        self.assertGreater(tr.MIN_FULL_ROWS, 1_000_000)  # the top-1M variant must never pass

    def test_not_a_list_is_refused(self):
        html = self.dir / "error.csv"
        html.write_bytes(b"<html><head><title>429 Too Many Requests</title></head></html>\n" * 3000)
        with self.assertRaises(tr.ListError):
            tr.scan_list(html, frozenset(), min_rows=1000)
        headless = self.dir / "headless.csv"
        headless.write_bytes(b"".join(f"{i},d{i}.example\r\n".encode() for i in range(2, 4000)))
        with self.assertRaises(tr.ListError):
            tr.scan_list(headless, frozenset(), min_rows=1000)
        empty = self.dir / "empty.csv"
        empty.write_bytes(b"")
        with self.assertRaises(tr.ListError):
            tr.scan_list(empty, frozenset(), min_rows=0)

    def test_ranks_must_run_to_the_row_count(self):
        holed = self.dir / "holed.csv"
        rows = [f"{i},d{i}.example\r\n".encode() for i in range(1, 4001)]
        holed.write_bytes(b"".join(rows[:1000] + rows[3000:]))  # 2,000 rows lost in the middle
        with self.assertRaises(tr.ListError):
            tr.scan_list(holed, frozenset(), min_rows=1000)

    def test_blank_last_line_is_not_a_row(self):
        path = self.dir / "list.csv"
        path.write_bytes(b"".join(f"{i},d{i}.example\r\n".encode() for i in range(1, 3001)) + b"\r\n\n")
        scan = tr.scan_list(path, frozenset({b"d3000.example"}), min_rows=1000)
        self.assertEqual((scan.rows, scan.ranks), (3000, {"d3000.example": 3000}))
        self.assertNotIn(b"", scan.sample)

    def test_bad_rows_do_not_stop_the_scan(self):
        path = self.dir / "list.csv"
        rows = [f"{i},d{i}.example\r\n".encode() for i in range(1, 3001)]
        rows[500] = b"garbage without a comma\r\n"
        rows[600] = b"x,skild.ai\r\n"
        rows[700] = b"701,skild.ai\r\n"
        path.write_bytes(b"".join(rows))
        scan = tr.scan_list(path, frozenset({b"skild.ai"}), min_rows=1000)
        self.assertEqual(scan.rows, 3000)
        self.assertEqual(scan.ranks, {"skild.ai": 701})


class Cohorts(unittest.TestCase):
    def setUp(self):
        # 1,000 sampled survivors at old ranks 10,000..10,999: one in ten doubled
        # its rank ratio, the rest did not move.
        self.old = {f"d{i}".encode(): 10_000 + i for i in range(1000)}
        self.new = {d: (r // 2 if i % 10 == 0 else r) for i, (d, r) in enumerate(self.old.items())}
        self.new.pop(b"d7")  # a dropout is not part of the survivor cohort
        self.cohort = tr.Cohort(self.old, self.new)

    def test_percentile_among_survivors(self):
        pct, n = self.cohort.percentile(10_500, 1.5)
        self.assertEqual(n, 999)
        self.assertAlmostEqual(pct, 100.0 * 899 / 999, places=6)
        self.assertEqual(self.cohort.percentile(10_500, 1.0)[0], 0.0)  # strictly below
        self.assertEqual(self.cohort.percentile(10_500, 3.0)[0], 100.0)

    def test_thin_band_is_not_trusted(self):
        self.assertIsNone(self.cohort.percentile(1_000_000, 2.0))
        thin = tr.Cohort(dict(list(self.old.items())[:150]), self.new)
        self.assertIsNone(thin.percentile(10_050, 2.0))

    def test_cohort_is_capped_to_the_nearest(self):
        old = {f"d{i}".encode(): 100_000 + i for i in range(10_000)}
        c = tr.Cohort(old, dict(old))
        self.assertEqual(c.percentile(105_000, 1.5)[1], tr.COHORT_MAX)

    def test_entrant_rate(self):
        new = {f"d{i}".encode(): 10_000 + i for i in range(1000)}
        old30 = {d: r for i, (d, r) in enumerate(new.items()) if i % 4}       # a quarter absent
        old90 = {d: r for i, (d, r) in enumerate(new.items()) if i % 8 == 0}  # half of those were there before
        rate, n = tr.EntrantRate(new, [old30, old90]).rate(10_500)
        self.assertEqual(n, 1000)
        self.assertAlmostEqual(rate, 0.125)
        self.assertIsNone(tr.EntrantRate(new, [old30, old90]).rate(5_000_000))

    def test_card_percentile_hits_the_card_numbers(self):
        for days, bands in tr.CARD_NORMS.items():
            for lo, hi, p50, p90, p99 in bands:
                mid = (lo + hi) // 2
                self.assertAlmostEqual(tr.card_percentile(mid, p50, days), 50.0)
                self.assertAlmostEqual(tr.card_percentile(mid, p90, days), 90.0)
                self.assertAlmostEqual(tr.card_percentile(mid, p99, days), 99.0)
        self.assertEqual(tr.card_percentile(1_500_000, 0.0, 30), 0.0)
        self.assertAlmostEqual(tr.card_percentile(1_500_000, 1000.0, 30), 99.9)

    def test_card_percentile_is_monotonic_and_uses_nearest_band(self):
        ratios = [0.2, 0.9, 0.99, 1.1, 1.29, 1.5, 2.0, 3.09, 5.0, 20.0]
        pcts = [tr.card_percentile(1_500_000, r, 30) for r in ratios]
        self.assertEqual(pcts, sorted(pcts))
        self.assertEqual(tr.card_percentile(50_000, 1.25, 30), tr.card_percentile(500_000, 1.25, 30))
        self.assertEqual(tr.card_percentile(4_400_000, 1.83, 90), tr.card_percentile(3_000_000, 1.83, 90))
        self.assertAlmostEqual(tr.card_percentile(500_000, 1.40, 90), 90.0)  # no 90-day band under 1M

    def test_card_norms_are_the_card(self):
        # docs/sources/traffic.md, "Baseline drift".
        self.assertEqual(tr.CARD_NORMS[30][0], (300_000, 1_000_000, 0.99, 1.25, 2.63))
        self.assertEqual(tr.CARD_NORMS[30][2], (2_000_000, 3_500_000, 0.97, 1.54, 4.55))
        self.assertEqual(tr.CARD_NORMS[90][1], (2_000_000, 3_500_000, 0.96, 1.83, 6.59))


def win(days, rank_then, rank_now, pct, n=4000, basis="live", span=None):
    lst = P_30 if days == 30 else P_90
    ratio = rank_then / rank_now if rank_then else None
    return tr.Window(days=days, span=span or days, lst=lst, rank=rank_then, ratio=ratio,
                     pct=pct if rank_then else None, n=n if rank_then else None,
                     basis=basis if rank_then else None)


class Strength(unittest.TestCase):
    def test_material_needs_percentile_and_size(self):
        self.assertTrue(tr.is_material(win(30, 1_500_000, 1_000_000, 92.0)))
        self.assertFalse(tr.is_material(win(30, 1_500_000, 1_000_000, 89.9)))
        self.assertFalse(tr.is_material(win(30, 23_000, 20_000, 97.0)))   # ratio 1.15: top-band jitter
        self.assertFalse(tr.is_material(win(90, 1_250_000, 1_000_000, 95.0)))  # 1.25 under the 90-day floor
        self.assertFalse(tr.is_material(win(30, None, 1_000_000, None)))
        self.assertFalse(tr.is_material(None))

    def test_move_strength_rises_with_the_percentile(self):
        got = [tr.move_strength(500_000, win(30, 1_000_000, 500_000, p), None)[0]
               for p in (90.0, 95.0, 99.0, 99.9, 100.0)]
        self.assertEqual(got, sorted(got))
        self.assertAlmostEqual(got[0], 0.30, places=2)
        self.assertAlmostEqual(got[2], 0.60, places=2)
        self.assertAlmostEqual(got[3], 0.78, places=2)
        self.assertLessEqual(got[-1], tr.MAX_MOVE_STRENGTH)
        self.assertEqual(tr.move_strength(500_000, win(30, 1_000_000, 500_000, 89.0), None), (0.0, None))

    def test_deep_ranks_are_discounted(self):
        shallow, _ = tr.move_strength(500_000, win(30, 1_000_000, 500_000, 99.0), None)
        deep, _ = tr.move_strength(3_000_000, win(30, 6_000_000, 3_000_000, 99.0), None)
        self.assertAlmostEqual(deep, shallow * 0.8, places=3)

    def test_sustained_move_gets_a_bonus_and_headlines_the_stronger_window(self):
        w30, w90 = win(30, 1_000_000, 500_000, 97.0), win(90, 1_200_000, 500_000, 96.0)
        alone, head = tr.move_strength(500_000, w30, None)
        both, head2 = tr.move_strength(500_000, w30, w90)
        self.assertIs(head, w30)
        self.assertIs(head2, w30)
        self.assertAlmostEqual(both, alone + tr.SUSTAINED_BONUS, places=3)
        _, head3 = tr.move_strength(500_000, win(30, 700_000, 500_000, 91.0), win(90, 2_000_000, 500_000, 99.5))
        self.assertEqual(head3.days, 90)

    def test_90_day_gain_does_not_count_while_it_is_being_given_back(self):
        w90 = win(90, 2_000_000, 1_000_000, 97.0)
        self.assertGreater(tr.move_strength(1_000_000, win(30, 1_050_000, 1_000_000, 60.0), w90)[0], 0)
        self.assertEqual(tr.move_strength(1_000_000, win(30, 900_000, 1_000_000, 20.0), w90), (0.0, None))
        self.assertEqual(tr.move_strength(1_000_000, win(30, None, 1_000_000, None), w90), (0.0, None))
        # With no 30-day list at all there is nothing to contradict it.
        self.assertGreater(tr.move_strength(1_000_000, None, w90)[0], 0)

    def test_present_carries_metrics_only(self):
        # A rank with no material move is a reading, not an event: no momentum from it.
        self.assertEqual(tr.PRESENT_STRENGTH, 0.0)
        self.assertFalse(hasattr(tr, "present_strength"))

    def test_entrant_strength_falls_with_the_base_rate(self):
        week = tr.Entry(day=date(2026, 9, 20), rank=1, days_listed=12, span=12, relisted=False)
        got = [tr.entrant_strength(r, week) for r in (0.002, 0.005, 0.02, 0.07, 0.26, 0.5, 0.9)]
        self.assertEqual(got, sorted(got, reverse=True))
        self.assertAlmostEqual(got[2], 0.70, places=2)   # top-million entry: notable
        self.assertAlmostEqual(got[3], 0.42, places=2)   # 1 site in 14 there is new: solid at most
        self.assertAlmostEqual(got[4], 0.22, places=2)   # deep entry: a quarter of the band is new
        self.assertTrue(all(0.15 <= s <= tr.MAX_ENTRANT_STRENGTH for s in got))
        # Absence is checked at two dates and over 39 days, not for all time: an
        # "entry" never reaches further into the first-ever tier than its floor.
        self.assertEqual(tr.entrant_strength(0.0, week), tr.MAX_ENTRANT_STRENGTH)
        self.assertLessEqual(tr.MAX_ENTRANT_STRENGTH, 0.85)

    def test_entry_keeps_a_premium_over_an_equally_rare_move(self):
        week = tr.Entry(day=date(2026, 9, 20), rank=1, days_listed=12, span=12, relisted=False)
        for rate in (0.10, 0.05, 0.02, 0.01):
            move, _ = tr.move_strength(500_000, win(30, 1_000_000, 500_000, 100.0 * (1 - rate)), None)
            entry = tr.entrant_strength(rate, week)
            self.assertGreater(entry, move, rate)
            self.assertLess(entry - move, 0.25, rate)

    def test_young_patchy_or_undated_entries_are_discounted(self):
        solid = tr.Entry(day=date(2026, 9, 20), rank=1, days_listed=12, span=12, relisted=False)
        young = tr.Entry(day=date(2026, 9, 30), rank=1, days_listed=2, span=2, relisted=False)
        patchy = tr.Entry(day=date(2026, 9, 16), rank=1, days_listed=8, span=16, relisted=False)
        base = tr.entrant_strength(0.02, solid)
        self.assertLess(tr.entrant_strength(0.02, young), base)
        self.assertGreater(tr.entrant_strength(0.02, young), 0.8 * base)
        self.assertAlmostEqual(tr.entrant_strength(0.02, patchy), base * 0.8, places=2)
        self.assertAlmostEqual(tr.entrant_strength(0.02, None), base * tr.UNDATED_ENTRY_FACTOR, places=2)

    def test_static_entrant_rate(self):
        self.assertEqual(tr.static_entrant_rate(505_708), 0.019)
        self.assertEqual(tr.static_entrant_rate(2_500_000), 0.26)
        self.assertEqual(tr.static_entrant_rate(4_400_000), 0.50)


class Wording(unittest.TestCase):
    def test_percent_is_rounded_down(self):
        self.assertEqual(tr._pct_words(90.0), "90")
        self.assertEqual(tr._pct_words(97.96), "97")
        self.assertEqual(tr._pct_words(99.78), "99")
        self.assertEqual(tr._pct_words(100.0), "99")

    def test_percent_allows_for_the_sample(self):
        # Measured against the full lists on 2026-10-01: the 1-in-64 sample gave
        # weaverobotics.com 93.3 (whole list 92.86) and etched.com 91.4 (90.80).
        self.assertEqual(tr._pct_words(93.3, 4000), "92")
        self.assertEqual(tr._pct_words(91.4, 4000), "90")
        self.assertEqual(tr._pct_words(97.9, 4000), "97")   # skild.ai: whole list 98.28
        self.assertEqual(tr._pct_words(99.8, 3777), "99")   # axisrobotics.ai: whole list 99.77
        self.assertEqual(tr._pct_words(100.0, 4000), "99")
        self.assertEqual(tr._pct_words(94.8, 400), "92")    # a thin cohort earns a wider allowance
        self.assertEqual(tr._pct_words(0.4, 200), "0")

    def test_normal_range_is_only_claimed_when_the_cohort_says_so(self):
        # cerebras.ai-like: a 1.12x gain near the top is under the size floor, so it is
        # no "move", but it can still beat nine in ten of its cohort.
        self.assertEqual(tr.present_title(42_069, P_ROWS, win(30, 47_083, 42_069, 89.8), None),
                         "Domain rank improved from 47,083 to 42,069 in 30 days, within the normal range")
        self.assertEqual(tr.present_title(42_069, P_ROWS, win(30, 47_083, 42_069, 93.0), None),
                         "Domain rank improved from 47,083 to 42,069 in 30 days")

    def test_titles_at_their_longest_fit(self):
        entry = tr.Entry(day=date(2026, 9, 30), rank=4_444_444, days_listed=2, span=2, relisted=False)
        w30, w90 = win(30, 4_444_444, 3_333_333, 99.95, span=33), win(90, 4_444_444, 3_333_333, 99.95, span=93)
        gone30, gone90 = win(30, None, 3_333_333, None, span=33), win(90, None, 3_333_333, None, span=93)
        titles = [
            tr.move_title(3_333_333, w30), tr.move_title(3_333_333, w90),
            tr.move_title(3_333_333, w90, w30),
            tr.move_title(3_333_333, w90, win(30, 3_333_334, 3_333_333, 50.0, span=33)),
            tr.move_title(3_333_333, win(90, 4_444_444, 3_333_333, 95.0, basis="card", span=93), w30),
            tr.move_title(3_333_333, win(30, 4_444_444, 3_333_333, 95.0, basis="card", span=33)),
            tr.present_title(3_333_333, 4_595_460, w30, w90),
            tr.present_title(4_444_444, 4_595_460, win(30, 3_333_333, 4_444_444, 5.0, span=33), w90),
            tr.present_title(3_333_333, 4_595_460, win(30, 3_333_334, 3_333_333, 50.0, span=33), w90),
            tr.present_title(3_333_333, 4_595_460, gone30, w90),
            tr.present_title(3_333_333, 4_595_460, gone30, None),
            tr.present_title(3_333_333, 4_595_460, None, w90),
            tr.present_title(3_333_333, 4_595_460, None, None),
            tr.relisted_title(3_333_333, 4_595_460, entry),
            tr.entrant_title(3_333_333, gone30, gone90, entry),
            tr.entrant_title(3_333_333, gone30, gone90, None),
        ]
        for t in titles:
            self.assertLess(len(t), 110, t)
            self.assertFalse(t.endswith("."), t)
            self.assertTrue(t[0].isupper(), t)
            self.assertIn("3,333,333", t)
            # The scorer reads titles when it classifies an entity. A rank says nothing
            # about what a company makes, so no title may carry a thesis term.
            self.assertEqual(classify(t)["terms"], [], t)
        self.assertEqual(len(set(titles)), len(titles))

    def test_title_text(self):
        self.assertEqual(
            tr.move_title(449_531, win(30, 1_133_223, 449_531, 97.9, span=29)),
            "Domain rank rose from 1,133,223 to 449,531 in 29 days, a bigger gain than 97% of similarly ranked sites")
        # The static table supports "material", not a percentage a partner could quote.
        self.assertEqual(tr.move_title(449_531, win(30, 1_133_223, 449_531, 98.3, basis="card", span=29)),
                         "Domain rank rose from 1,133,223 to 449,531 in 29 days")
        # A 90-day headline quotes the ranks the evidence page shows (it goes back 39 days)
        # and puts the 90-day climb in words: aalo.com and helsing.ai on 2026-10-01.
        w90 = win(90, 2_227_400, 1_319_398, 93.4)
        self.assertEqual(tr.move_title(1_319_398, w90, win(30, 1_900_353, 1_319_398, 91.7)),
                         "Domain rank rose from 1,900,353 to 1,319,398 in 30 days, "
                         "in a 90-day climb that beat 92% of similar sites")
        self.assertNotIn("2,227,400", tr.move_title(1_319_398, w90, win(30, 1_900_353, 1_319_398, 91.7)))
        self.assertEqual(tr.move_title(1_319_398, w90, win(30, 1_330_000, 1_319_398, 55.0)),
                         "Domain rank 1,319,398 after a 90-day climb that beat 92% of similar sites, "
                         "flat over the last 30 days")
        self.assertEqual(tr.move_title(1_319_398, win(90, 2_227_400, 1_319_398, 93.4, basis="card"),
                                       win(30, 1_900_353, 1_319_398, 91.7)),
                         "Domain rank rose from 1,900,353 to 1,319,398 in 30 days, in a 90-day climb")
        # With no 30-day list there is nothing nearer to quote.
        self.assertEqual(tr.move_title(1_319_398, w90, None),
                         "Domain rank rose from 2,227,400 to 1,319,398 in 90 days, "
                         "a bigger gain than 92% of similarly ranked sites")
        self.assertEqual(tr.present_title(495_718, P_ROWS, win(30, 460_725, 495_718, 20.0, span=29), None),
                         "Domain rank slipped from 460,725 to 495,718 in 29 days")
        self.assertEqual(tr.present_title(1_350_558, P_ROWS, win(30, 1_358_449, 1_350_558, 55.0, span=29), None),
                         "Domain rank 1,350,558 of 4.6M ranked sites, flat against 1,358,449 on 2026-09-01")
        gone30 = win(30, None, 2_475_885, None, span=29)
        self.assertEqual(tr.present_title(2_475_885, P_ROWS, gone30, None),
                         "Domain rank 2,475,885 of 4.6M ranked sites, unranked on 2026-09-01")
        self.assertEqual(tr.present_title(2_475_885, P_ROWS, None, win(90, None, 2_475_885, None, span=91)),
                         "Domain rank 2,475,885 of 4.6M ranked sites, unranked on 2026-07-01")
        self.assertEqual(tr.present_title(2_475_885, P_ROWS, None, win(90, 1_443_547, 2_475_885, 10.0, span=91)),
                         "Domain rank 2,475,885 of 4.6M ranked sites, against 1,443,547 on 2026-07-01")


class PanelOnCardNorms(unittest.TestCase):
    """Real ranks for 69 hard-tech domains on three dates, scored with the card's table."""

    def test_the_cards_own_reading(self):
        # "skild.ai (30 d ratio 2.52), generalistai.com (2.22), picogrid.com (1.84),
        #  castelion.com (1.59) are at or beyond the p90 of their cohorts;
        #  radiantnuclear.com (1.06) is noise."
        for domain, ratio in [("skild.ai", 2.52), ("generalistai.com", 2.22), ("picogrid.com", 1.84),
                              ("castelion.com", 1.59)]:
            s = panel_signal(domain)
            self.assertEqual(s.kind, "tranco_rank_move", domain)
            self.assertAlmostEqual(s.metrics["rank_ratio_30d"], ratio, places=2)
            self.assertGreaterEqual(s.metrics["cohort_pct_30d"], 90.0)
        noise = panel_signal("radiantnuclear.com")
        self.assertEqual(noise.kind, "tranco_rank_present")
        self.assertAlmostEqual(noise.metrics["rank_ratio_30d"], 1.06, places=2)
        self.assertEqual(noise.strength, 0.0)
        self.assertIs(noise.metrics["observed_only"], True)
        self.assertEqual(noise.metrics["tranco_rank"], PANEL["radiantnuclear.com"]["2026-09-30"])

    def test_skild(self):
        s = panel_signal("skild.ai", name="Skild AI")
        self.assertEqual(s.title, "Domain rank rose from 1,133,223 to 449,531 in 29 days")
        self.assertEqual((s.value, s.unit), (449_531, "tranco rank"))
        self.assertEqual(s.occurred_at, "2026-09-30")
        self.assertEqual(s.url, "https://tranco-list.eu/api/ranks/domain/skild.ai")
        self.assertEqual((s.entity.name, s.entity.domain, s.entity.kind), ("Skild AI", "skild.ai", "company"))
        self.assertEqual(s.series, [{"t": "2026-07-01", "v": 1_155_116}, {"t": "2026-09-01", "v": 1_133_223},
                                    {"t": "2026-09-30", "v": 449_531}])
        m = s.metrics
        self.assertEqual(m["tranco_rank"], 449_531)
        self.assertEqual((m["tranco_rank_30d"], m["tranco_rank_90d"]), (1_133_223, 1_155_116))
        self.assertEqual((m["window_days_30d"], m["window_days_90d"]), (29, 91))
        self.assertEqual((m["tranco_list_id"], m["list_id_30d"], m["list_id_90d"]), ("Q2K34", "K9QPW", "JZ2VY"))
        self.assertEqual(m["tranco_list_url"], "https://tranco-list.eu/list/Q2K34/full")
        self.assertEqual(m["cohort_basis_30d"], "card")
        self.assertTrue(m["sustained"])
        self.assertTrue(0.55 <= s.strength <= 0.65, s.strength)
        # A move is an event (it counts as momentum), restated daily while the climb
        # lasts: stored as one refreshed row, not as a standing reading.
        self.assertNotIn("observed_only", m)
        self.assertIs(m["rolling"], True)
        self.assertIsNone(s.text)

    def test_weak_deep_move_stays_routine(self):
        s = panel_signal("castelion.com")
        self.assertTrue(0.2 <= s.strength <= 0.35, s.strength)
        self.assertFalse(s.metrics["sustained"])

    def test_new_entrant_dated_from_history(self):
        # Card: "northwoodspace.io first appears 2026-09-30 at 505,708".
        s = panel_signal("northwoodspace.io", history=[(date(2026, 9, 30), 505_708)], name="Northwood Space")
        self.assertEqual(s.kind, "tranco_new_entrant")
        self.assertEqual(s.occurred_at, "2026-09-30")
        self.assertEqual(s.title, "Appeared on the Tranco web ranking on 2026-09-30 and now ranks 505,708, "
                                  "unranked 29 and 91 days earlier")
        self.assertEqual(s.metrics["tranco_rank"], 505_708)
        self.assertEqual((s.metrics["entry_date"], s.metrics["entry_rank"]), ("2026-09-30", 505_708))
        self.assertEqual(s.metrics["entry_date_basis"], "domain_api")
        # An entry with its own day is a point event: neither a reading nor a rolling row.
        self.assertNotIn("observed_only", s.metrics)
        self.assertNotIn("rolling", s.metrics)
        self.assertEqual((s.metrics["listed_30d"], s.metrics["listed_90d"]), (False, False))
        self.assertNotIn("tranco_rank_30d", s.metrics)
        self.assertEqual(s.series, [{"t": "2026-09-30", "v": 505_708}])
        # Notable, not rare: one day on the list, and "new" only as far back as was checked.
        self.assertTrue(0.5 <= s.strength <= 0.7, s.strength)
        self.assertEqual((s.metrics["list_url_30d"], s.metrics["list_url_90d"]),
                         ("https://tranco-list.eu/list/K9QPW/full", "https://tranco-list.eu/list/JZ2VY/full"))

    def test_new_entrant_without_history_is_dated_to_the_list(self):
        s = panel_signal("periodic.com")  # 2,504,339 on 2026-09-30, on neither older list
        self.assertEqual(s.kind, "tranco_new_entrant")
        self.assertEqual(s.occurred_at, "2026-09-30")
        self.assertEqual(s.metrics["entry_date_basis"], "list_diff")
        self.assertNotIn("entry_date", s.metrics)
        # The only date it has is the newest list's, which moves with every run.
        self.assertIs(s.metrics["rolling"], True)
        self.assertNotIn("observed_only", s.metrics)
        self.assertEqual(s.title, "Appeared on the Tranco web ranking in the last 29 days and now ranks 2,504,339, "
                                  "unranked 91 days earlier too")
        deep = panel_signal("diracinc.com")  # 3,902,508: half the list down there is new
        self.assertLess(deep.strength, s.strength)
        self.assertLess(deep.strength, 0.3)

    def test_relisting_is_not_an_entry(self):
        hist = [(date(2026, 8, 25), 2_600_000)] + \
               [(date(2026, 9, 12) + timedelta(days=k), 2_550_000 - 2_000 * k) for k in range(18)] + \
               [(date(2026, 9, 30), 2_504_339)]
        s = panel_signal("periodic.com", history=hist)
        self.assertEqual(s.kind, "tranco_rank_present")
        self.assertEqual(s.strength, 0.0)
        self.assertIs(s.metrics["observed_only"], True)
        self.assertNotIn("rolling", s.metrics)
        self.assertTrue(s.metrics["relisted"])
        self.assertEqual(s.occurred_at, "2026-09-30")
        self.assertIn("back on the list since 2026-09-12", s.title)

    def test_history_disagreeing_with_the_list_keeps_the_list_and_warns(self):
        r = PANEL["northwoodspace.io"]
        c = ctx()
        w30 = tr.make_window(30, P_NEW, P_30, r["2026-09-30"], None, None)
        w90 = tr.make_window(90, P_NEW, P_90, r["2026-09-30"], None, None)
        s = tr.build_signal(c, "northwoodspace.io", {"name": "Northwood Space"}, r["2026-09-30"], P_NEW, P_ROWS,
                            w30, w90, None, [(date(2026, 9, 29), 600_000), (date(2026, 9, 30), 499_999)])
        self.assertEqual(s.series[-1], {"t": "2026-09-30", "v": 505_708})
        self.assertEqual(s.value, 505_708)
        self.assertEqual(len(c.warnings), 1)

    def test_partial_histories(self):
        s = panel_signal("diode.computer")  # ranked 90 days ago, off the list 30 days ago, back now
        self.assertEqual(s.kind, "tranco_rank_present")
        self.assertEqual(s.title, "Domain rank 2,475,885 of 4.6M ranked sites, unranked on 2026-09-01 "
                                  "and ranked on 2026-07-01")
        self.assertEqual(s.metrics["tranco_rank_90d"], 1_443_547)
        self.assertEqual(s.series, [{"t": "2026-07-01", "v": 1_443_547}, {"t": "2026-09-30", "v": 2_475_885}])
        s = panel_signal("foundation.bot")  # on the list 30 days ago, not 90
        self.assertEqual(s.kind, "tranco_rank_present")
        self.assertEqual(len(s.series), 2)
        s = panel_signal("reflectorbital.com")
        self.assertEqual(s.title, "Domain rank slipped from 460,725 to 495,718 in 29 days")
        self.assertEqual(s.strength, 0.0)

    def test_whole_panel(self):
        ranked = [d for d, r in PANEL.items() if r["2026-09-30"] is not None]
        self.assertEqual(len(ranked), 50)  # the card's "50 / 69"
        kinds: dict[str, list[float]] = {}
        for d in ranked:
            s = panel_signal(d)
            s.validate()
            kinds.setdefault(s.kind, []).append(s.strength)
            # The standing reading is state (observed_only). A move, and an entry with no
            # day of its own, are events whose date rolls forward (rolling). Never both.
            self.assertEqual(s.metrics.get("observed_only", False), s.kind == "tranco_rank_present", d)
            undated = s.kind == "tranco_new_entrant" and s.metrics["entry_date_basis"] == "list_diff"
            self.assertEqual(s.metrics.get("rolling", False), s.kind == "tranco_rank_move" or undated, d)
            self.assertEqual(s.metrics["tranco_rank"], PANEL[d]["2026-09-30"])
            self.assertEqual(s.value, PANEL[d]["2026-09-30"])
            self.assertLess(len(s.title), 110, s.title)
            self.assertFalse(s.title.endswith("."))
            self.assertEqual(s.series, sorted(s.series, key=lambda p: p["t"]))
            self.assertEqual(s.series[-1]["v"], PANEL[d]["2026-09-30"])
            self.assertTrue(all(isinstance(p["v"], int) and "s" not in p for p in s.series))
            json.dumps(s.to_row())
        self.assertEqual(set(kinds), {"tranco_rank_present", "tranco_rank_move", "tranco_new_entrant"})
        self.assertTrue(all(x == 0.0 for x in kinds["tranco_rank_present"]))
        self.assertTrue(all(0.15 <= x <= tr.MAX_MOVE_STRENGTH for x in kinds["tranco_rank_move"]))
        self.assertTrue(all(0.15 <= x <= tr.MAX_ENTRANT_STRENGTH for x in kinds["tranco_new_entrant"]))
        # Most ranked domains did nothing unusual, and the moves use the scale.
        self.assertGreater(len(kinds["tranco_rank_present"]), len(kinds["tranco_rank_move"]))
        self.assertLess(min(kinds["tranco_rank_move"]), 0.35)
        self.assertGreater(max(kinds["tranco_rank_move"]), 0.55)

    NEXT = tr.TrancoList("NEXT1", P_NEW.day + timedelta(days=1))
    NEXT2 = tr.TrancoList("NEXT2", P_NEW.day + timedelta(days=2))

    def on_list(self, domain: str, newest: tr.TrancoList, bump: int = 0, history=None):
        """A panel domain as read off `newest`, its rank moved by `bump` since 2026-09-30."""
        r = PANEL[domain]
        rank = r["2026-09-30"] + bump
        w30 = tr.make_window(30, newest, P_30, rank, r["2026-09-01"], None)
        w90 = tr.make_window(90, newest, P_90, rank, r["2026-07-01"], None)
        return tr.build_signal(ctx(), domain, {"name": domain, "domain": domain}, rank, newest,
                               P_ROWS, w30, w90, None, history)

    def test_present_is_one_refreshed_row(self):
        # The same domain read off the next day's list: the standing reading keeps its
        # identity, so the stored row is refreshed.
        a, b = self.on_list("radiantnuclear.com", P_NEW), self.on_list("radiantnuclear.com", self.NEXT, 1_234)
        self.assertEqual((a.kind, b.kind), ("tranco_rank_present", "tranco_rank_present"))
        self.assertNotEqual((a.occurred_at, a.value), (b.occurred_at, b.value))
        self.assertEqual(fingerprint(a), fingerprint(b))
        # Different entities never share a row.
        self.assertNotEqual(fingerprint(panel_signal("radiantnuclear.com")),
                            fingerprint(panel_signal("reflectorbital.com")))

    def test_a_climb_restated_by_the_next_days_list_is_the_same_row(self):
        # skild.ai is still climbing on the next two lists. Date, rank and title all
        # change; it is the same move, and without `rolling` each day would be a new row.
        days = [self.on_list("skild.ai", P_NEW), self.on_list("skild.ai", self.NEXT, -6_930),
                self.on_list("skild.ai", self.NEXT2, -11_000)]
        self.assertEqual([s.kind for s in days], ["tranco_rank_move"] * 3)
        self.assertEqual([s.occurred_at for s in days], ["2026-09-30", "2026-10-01", "2026-10-02"])
        self.assertEqual(len({s.title for s in days}), 3)
        self.assertEqual(len({fingerprint(s) for s in days}), 1)
        for s in days:  # the flag is what does it: the same signals without it are three rows
            del s.metrics["rolling"]
        self.assertEqual(len({fingerprint(s) for s in days}), 3)
        # Two climbing domains are two rows.
        self.assertNotEqual(fingerprint(self.on_list("skild.ai", P_NEW)),
                            fingerprint(self.on_list("picogrid.com", P_NEW)))

    def test_an_undated_entry_is_one_row_and_a_dated_one_is_a_point_event(self):
        # No history: the entry is only a difference between lists and is dated to the
        # newest one, so the next run restates it under a later date. One row.
        a, b = self.on_list("periodic.com", P_NEW), self.on_list("periodic.com", self.NEXT, -9_000)
        self.assertEqual((a.kind, b.kind), ("tranco_new_entrant", "tranco_new_entrant"))
        self.assertEqual((a.occurred_at, b.occurred_at), ("2026-09-30", "2026-10-01"))
        self.assertEqual(fingerprint(a), fingerprint(b))
        # With history the entry has its own day, which no later run moves: the row is
        # found again by that day, and an entry on another day is another event.
        hist = [(date(2026, 9, 30), 505_708)]
        c = self.on_list("northwoodspace.io", P_NEW, history=hist)
        d = self.on_list("northwoodspace.io", self.NEXT, -10_805, history=hist + [(date(2026, 10, 1), 494_903)])
        self.assertEqual((c.occurred_at, d.occurred_at), ("2026-09-30", "2026-09-30"))
        self.assertNotEqual(c.value, d.value)
        self.assertNotIn("rolling", d.metrics)
        self.assertEqual(fingerprint(c), fingerprint(d))
        e = self.on_list("northwoodspace.io", self.NEXT, -10_805, history=[(date(2026, 10, 1), 494_903)])
        self.assertEqual((e.kind, e.occurred_at), ("tranco_new_entrant", "2026-10-01"))
        self.assertNotEqual(fingerprint(c), fingerprint(e))
        # The undated row and the dated event do not share a row: an entry first seen
        # undated (history call failed) and dated by a later run is stored twice.
        f = self.on_list("northwoodspace.io", P_NEW)
        self.assertEqual(f.metrics["entry_date_basis"], "list_diff")
        self.assertNotEqual(fingerprint(f), fingerprint(c))

    def test_kinds_never_share_a_row(self):
        # One domain can be an entrant, then present, then a move. Each kind is its own row.
        move, present = self.on_list("skild.ai", P_NEW), self.on_list("skild.ai", self.NEXT, 600_000)
        self.assertEqual((move.kind, present.kind), ("tranco_rank_move", "tranco_rank_present"))
        self.assertNotEqual(fingerprint(move), fingerprint(present))

    def test_a_sustained_climb_is_stored_once_and_refreshed(self):
        conn = connect(":memory:")
        self.addCleanup(conn.close)
        runs = [
            [self.on_list("skild.ai", P_NEW), self.on_list("periodic.com", P_NEW),
             self.on_list("radiantnuclear.com", P_NEW)],
            [self.on_list("skild.ai", self.NEXT, -6_930), self.on_list("periodic.com", self.NEXT, -9_000),
             self.on_list("radiantnuclear.com", self.NEXT, 1_234)],
            [self.on_list("skild.ai", self.NEXT2, -11_000), self.on_list("periodic.com", self.NEXT2, -15_000),
             self.on_list("radiantnuclear.com", self.NEXT2, 2_000)],
        ]
        self.assertEqual([insert_signals(conn, run_id, sigs) for run_id, sigs in enumerate(runs, 1)],
                         [(3, 3), (3, 0), (3, 0)])
        rows = {r["kind"]: r for r in conn.execute("SELECT * FROM signals")}
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0], 3)
        self.assertEqual(set(rows), {"tranco_rank_move", "tranco_new_entrant", "tranco_rank_present"})
        # Each row carries the last run's reading, not the first.
        move = rows["tranco_rank_move"]
        self.assertEqual((move["occurred_at"], move["value"], move["run_id"]),
                         ("2026-10-02", PANEL["skild.ai"]["2026-09-30"] - 11_000, 3))
        self.assertEqual(move["title"], runs[2][0].title)
        self.assertEqual(json.loads(move["metrics"])["tranco_list_id"], "NEXT2")
        self.assertEqual(rows["tranco_new_entrant"]["occurred_at"], "2026-10-02")

    def test_present_feeds_consensus_and_not_momentum(self):
        s = panel_signal("reflectorbital.com", name="Reflect Orbital")  # 495,718, slipped
        self.assertEqual(s.kind, "tranco_rank_present")
        scored = score_entity({"name": "Reflect Orbital"}, [s.to_row()], date(2026, 9, 30))
        self.assertEqual(scored["momentum"], 0.0)
        self.assertEqual(scored["families"]["traffic"], 0.0)
        self.assertEqual(scored["metrics"]["tranco_rank"], 495_718)
        self.assertGreater(scored["consensus_parts"]["traffic"], 0.0)
        self.assertNotIn("unmeasured", scored["consensus_parts"])
        # A move still counts as momentum.
        moved = score_entity({"name": "Skild AI"}, [panel_signal("skild.ai").to_row()], date(2026, 9, 30))
        self.assertGreater(moved["families"]["traffic"], 0.5)

    def test_outside_the_lookback_nothing_is_emitted(self):
        r = PANEL["skild.ai"]
        c = ctx(today=date(2027, 6, 1))
        w30 = tr.make_window(30, P_NEW, P_30, r["2026-09-30"], r["2026-09-01"], None)
        self.assertIsNone(tr.build_signal(c, "skild.ai", {"name": "Skild AI"}, r["2026-09-30"], P_NEW, P_ROWS,
                                          w30, None, None, None))


# ------------------------------------------------------------ end to end

D = date(2026, 10, 1)
IDS = {D: "NEW01", D - timedelta(days=30): "OLD30", D - timedelta(days=91): "OLD91"}
N_FILLER = 40_000


def meta(list_id: str, day: date) -> dict:
    return {"list_id": list_id, "available": True, "failed": False,
            "download": f"https://tranco-list.eu/download/{list_id}/1000000",
            "created_on": f"{day.isoformat()}T22:00:02.000000",
            "configuration": {"endDate": day.isoformat(), "startDate": (day - timedelta(days=29)).isoformat()}}


def synthetic_lists() -> dict[str, list[str]]:
    """Three lists of 40,000 filler sites that barely move, plus the test entities."""
    filler = [f"site{i:05d}.example" for i in range(N_FILLER)]
    jitter = []  # the newest list: neighbours swap places, so cohort ratios sit at about 1
    for i in range(0, N_FILLER, 2):
        jitter += [filler[i + 1], filler[i]]

    def place(base: list[str], at: dict[str, int]) -> list[str]:
        out = list(base)
        for dom, rank in sorted(at.items(), key=lambda kv: kv[1]):
            out.insert(rank - 1, dom)
        return out

    return {
        "OLD91": place(filler, {"climber.example": 30_000, "steady.example": 20_000, "back.example": 25_000}),
        "OLD30": place(filler, {"climber.example": 24_000, "steady.example": 20_000}),
        "NEW01": place(jitter, {"climber.example": 12_000, "steady.example": 20_000, "back.example": 26_000,
                                "fresh.example": 22_000}),
    }


E2E_KNOWN = [
    {"name": "Climber Robotics", "kind": "company", "domain": "climber.example"},
    {"name": "Steady Fusion", "kind": "company", "domain": "https://www.steady.example/about"},
    {"name": "fresh-drones", "kind": "project", "domain": "fresh.example"},
    {"name": "Back Again", "kind": "company", "domain": "back.example"},
    {"name": "Ghost Atomics", "kind": "company", "domain": "ghost.example"},
    {"name": "No Domain Inc", "kind": "company", "domain": None},
    {"name": "Tenant Labs", "kind": "company", "domain": "lab.site00010.example"},
    {"name": "Hosted Project", "kind": "project", "domain": "pages.dev"},
]
FRESH_HISTORY = {"domain": "fresh.example", "ranks": [
    {"date": "2026-10-02", "rank": 21_500},  # the API can be a day ahead of the latest list
    {"date": "2026-10-01", "rank": 22_000},
    {"date": "2026-09-30", "rank": 23_000},
    {"date": "2026-09-29", "rank": 25_000},
    {"date": "2026-09-28", "rank": 28_000},
    {"date": "2026-09-27", "rank": 30_000},
]}


class FakeHttp:
    """Serves list metadata, list files and per-domain history; anything else fails the test."""

    def __init__(self, lists: dict[str, list[str]], *, latest: date = D, missing: tuple[date, ...] = (),
                 history=FRESH_HISTORY, corrupt_first: tuple[str, ...] = (), broken: tuple[str, ...] = ()):
        self.lists, self.latest, self.missing = lists, latest, set(missing)
        self.history, self.corrupt_first, self.broken = history, set(corrupt_first), set(broken)
        self.requests = 0
        self.json_urls: list[str] = []
        self.downloads: list[tuple[str, str, float]] = []

    def get_json(self, url: str, **kw):
        self.json_urls.append(url)
        self.requests += 1
        prefix = "https://tranco-list.eu/api/lists/date/"
        if url.startswith(prefix):
            key = url[len(prefix):]
            day = self.latest if key == "latest" else date.fromisoformat(key)
            if day in self.missing or day not in IDS:
                raise http.HttpError(404, url, '{"available": false}')
            return meta(IDS[day], day)
        if url == "https://tranco-list.eu/api/ranks/domain/fresh.example":
            if isinstance(self.history, Exception):
                raise self.history
            return self.history
        raise AssertionError(f"unexpected URL {url}")

    def download(self, url: str, dest, *, max_age: float = 0, **kw) -> Path:
        dest = Path(dest)
        list_id = url.split("/")[-2]
        if not url.startswith("https://tranco-list.eu/download/") or not url.endswith("/full") \
                or list_id not in self.lists:
            raise AssertionError(f"unexpected download {url}")
        self.downloads.append((list_id, dest.name, max_age))
        if dest.exists() and max_age > 0:
            return dest
        self.requests += 1
        if list_id in self.broken:
            raise http.HttpError(503, url)
        if list_id in self.corrupt_first:
            self.corrupt_first.discard(list_id)
            dest.write_bytes(b"<html>Bad Gateway</html>\n")
        else:
            write_list(dest, self.lists[list_id])
        return dest

    def stats(self) -> dict:
        return {"requests": self.requests, "cache_hits": 0, "errors": 0}


class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lists = synthetic_lists()

    def run_collect(self, fake: FakeHttp, known=E2E_KNOWN, **ctx_kw):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        c = ctx(known=known, **ctx_kw)
        with mock.patch.object(tr.http, "get_json", fake.get_json), \
                mock.patch.object(tr.http, "download", fake.download), \
                mock.patch.object(tr.http, "stats", fake.stats), \
                mock.patch.object(tr, "DOWNLOADS_DIR", Path(tmp.name)), \
                mock.patch.object(tr, "MIN_FULL_ROWS", 30_000), \
                mock.patch.object(tr, "API_SPACING", 0):
            signals = list(tr.collect(c))
        for s in signals:
            s.validate()
        return {s.entity.domain: s for s in signals}, signals, c

    def test_full_run(self):
        fake = FakeHttp(self.lists, missing=(D - timedelta(days=90),))
        by, signals, c = self.run_collect(fake)
        self.assertEqual(c.warnings, [])
        self.assertEqual(set(by), {"climber.example", "steady.example", "fresh.example", "back.example"})
        self.assertEqual([s.strength for s in signals], sorted((s.strength for s in signals), reverse=True))

        # Each list fetched once and kept: a list id never changes its contents.
        self.assertEqual(sorted(fake.downloads), sorted([
            ("NEW01", "tranco_full_2026-10-01_NEW01.csv", tr.FOREVER),
            ("OLD30", "tranco_full_2026-09-01_OLD30.csv", tr.FOREVER),
            ("OLD91", "tranco_full_2026-07-02_OLD91.csv", tr.FOREVER),
        ]))
        # Only the new entrant costs a per-domain call.
        self.assertEqual([u for u in fake.json_urls if "/ranks/domain/" in u],
                         ["https://tranco-list.eu/api/ranks/domain/fresh.example"])

        move = by["climber.example"]
        self.assertEqual(move.kind, "tranco_rank_move")
        self.assertEqual((move.entity.name, move.entity.kind), ("Climber Robotics", "company"))
        self.assertEqual(move.occurred_at, "2026-10-01")
        self.assertEqual(move.url, "https://tranco-list.eu/api/ranks/domain/climber.example")
        self.assertEqual(move.series, [{"t": "2026-07-02", "v": 30_000}, {"t": "2026-09-01", "v": 24_000},
                                       {"t": "2026-10-01", "v": 12_000}])
        self.assertEqual(move.metrics["tranco_rank"], 12_000)
        self.assertEqual(move.metrics["rank_ratio_30d"], 2.0)
        self.assertEqual(move.metrics["rank_ratio_90d"], 2.5)
        self.assertEqual((move.metrics["window_days_30d"], move.metrics["window_days_90d"]), (30, 91))
        self.assertEqual(move.metrics["cohort_basis_30d"], "live")
        self.assertGreaterEqual(move.metrics["cohort_n_30d"], tr.COHORT_MIN)
        self.assertEqual(move.metrics["cohort_pct_30d"], 100.0)
        self.assertTrue(move.metrics["sustained"])
        self.assertEqual(move.title, "Domain rank rose from 24,000 to 12,000 in 30 days, "
                                     "a bigger gain than 99% of similarly ranked sites")
        self.assertTrue(0.7 <= move.strength <= 0.9, move.strength)

        flat = by["steady.example"]
        self.assertEqual(flat.kind, "tranco_rank_present")
        self.assertEqual(flat.entity.name, "Steady Fusion")
        self.assertEqual(flat.metrics["tranco_rank"], 20_000)
        self.assertEqual(flat.title, "Domain rank 20,000 of 40,004 ranked sites, flat against 20,000 on 2026-09-01")
        self.assertEqual(flat.strength, 0.0)
        self.assertIs(flat.metrics["observed_only"], True)
        self.assertNotIn("rolling", flat.metrics)
        self.assertNotIn("observed_only", move.metrics)
        self.assertIs(move.metrics["rolling"], True)

        new = by["fresh.example"]
        self.assertEqual(new.kind, "tranco_new_entrant")
        self.assertEqual((new.entity.name, new.entity.kind), ("fresh-drones", "project"))
        self.assertEqual(new.occurred_at, "2026-09-27")
        self.assertEqual(new.value, 22_000)
        self.assertEqual(new.title, "Appeared on the Tranco web ranking on 2026-09-27 and now ranks 22,000, "
                                    "unranked 30 and 91 days earlier")
        self.assertIsNone(new.entity.github)
        self.assertEqual([p["t"] for p in new.series],
                         ["2026-09-27", "2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01"])
        self.assertEqual(new.series[-1]["v"], 22_000)
        self.assertEqual((new.metrics["entry_rank"], new.metrics["days_listed"]), (30_000, 5))
        self.assertNotIn("rolling", new.metrics)  # dated from the history: a point event
        self.assertEqual(new.metrics["entrant_rate_basis"], "live")
        self.assertLess(new.metrics["entrant_base_rate"], 0.01)  # no filler site is new
        self.assertTrue(0.75 <= new.strength <= tr.MAX_ENTRANT_STRENGTH, new.strength)

        back = by["back.example"]
        self.assertEqual(back.kind, "tranco_rank_present")
        self.assertEqual(back.strength, 0.0)
        self.assertIn("unranked on 2026-09-01 and ranked on 2026-07-02", back.title)
        self.assertEqual(back.metrics["tranco_rank_90d"], 25_000)

    def test_top_ranked_domain_that_is_not_the_entitys_is_skipped(self):
        # site00042.example sits at rank ~43: a platform, not "Tiny Drone Labs".
        known = [{"name": "Tiny Drone Labs", "kind": "company", "domain": "site00042.example"},
                 {"name": "Site00044 Example", "kind": "company", "domain": "site00044.example"},
                 *E2E_KNOWN]
        by, _, c = self.run_collect(FakeHttp(self.lists, missing=(D - timedelta(days=90),)), known=known)
        self.assertNotIn("site00042.example", by)
        self.assertIn("site00044.example", by)  # the name is in the domain: a big site that is the entity
        self.assertLessEqual(by["site00044.example"].metrics["tranco_rank"], 50)
        self.assertEqual(len(c.warnings), 1)
        self.assertIn("site00042.example", c.warnings[0])
        with mock.patch.object(tr, "OWN_DOMAIN_CHECK_RANK", 10):
            by, _, c = self.run_collect(FakeHttp(self.lists, missing=(D - timedelta(days=90),)), known=known)
        self.assertIn("site00042.example", by)  # below the checked ranks nothing is second-guessed

    def test_limit_counts_entities_with_a_domain(self):
        by, _, _ = self.run_collect(FakeHttp(self.lists, missing=(D - timedelta(days=90),)), limit=2)
        self.assertEqual(set(by), {"climber.example", "steady.example"})

    def test_no_known_entities_means_no_requests(self):
        fake = FakeHttp(self.lists)
        by, _, c = self.run_collect(fake, known=[{"name": "No Domain Inc", "domain": None}])
        self.assertEqual((by, fake.requests, c.warnings), ({}, 0, []))

    def test_corrupt_download_is_fetched_again(self):
        fake = FakeHttp(self.lists, missing=(D - timedelta(days=90),), corrupt_first=("OLD30",))
        by, _, c = self.run_collect(fake)
        self.assertEqual(by["climber.example"].kind, "tranco_rank_move")
        self.assertEqual(len(c.warnings), 1)
        self.assertEqual([m for i, _, m in fake.downloads if i == "OLD30"], [tr.FOREVER, 0])

    def test_without_the_newest_list_nothing_is_emitted(self):
        by, _, c = self.run_collect(FakeHttp(self.lists, missing=(D - timedelta(days=90),), broken=("NEW01",)))
        self.assertEqual(by, {})
        self.assertTrue(any("nothing emitted" in w for w in c.warnings))

    def test_without_an_older_list_no_entry_is_claimed(self):
        fake = FakeHttp(self.lists, missing=(D - timedelta(days=90),), broken=("OLD91",))
        by, _, c = self.run_collect(fake)
        self.assertEqual(by["fresh.example"].kind, "tranco_rank_present")
        self.assertEqual(by["fresh.example"].title, "Domain rank 22,000 of 40,004 ranked sites, unranked on 2026-09-01")
        self.assertEqual(by["climber.example"].kind, "tranco_rank_move")
        self.assertNotIn("tranco_rank_90d", by["climber.example"].metrics)
        self.assertEqual(len(by["climber.example"].series), 2)
        self.assertFalse(any("/ranks/domain/" in u for u in fake.json_urls))
        self.assertTrue(c.warnings)

    def test_history_failure_falls_back_to_the_list_diff(self):
        fake = FakeHttp(self.lists, missing=(D - timedelta(days=90),), history=http.HttpError(429, "x"))
        by, _, c = self.run_collect(fake)
        new = by["fresh.example"]
        self.assertEqual(new.kind, "tranco_new_entrant")
        self.assertEqual(new.occurred_at, "2026-10-01")
        self.assertEqual(new.metrics["entry_date_basis"], "list_diff")
        self.assertIs(new.metrics["rolling"], True)  # dated to the list, which moves every run
        self.assertEqual(new.series, [{"t": "2026-10-01", "v": 22_000}])
        self.assertEqual(len(c.warnings), 1)

    def test_stale_latest_list_is_outside_the_lookback(self):
        fake = FakeHttp(self.lists)
        by, _, c = self.run_collect(fake, today=D + timedelta(days=200))
        self.assertEqual(by, {})
        self.assertEqual(fake.downloads, [])
        self.assertTrue(any("older than the lookback" in w for w in c.warnings))


class ResolveLists(unittest.TestCase):
    def resolve(self, today: date, latest: date, missing: set[date] = frozenset()):
        asked: list[str] = []

        def get_json(url: str, **kw):
            key = url.rsplit("/", 1)[1]
            asked.append(key)
            day = latest if key == "latest" else date.fromisoformat(key)
            if day in missing or day > latest:
                raise http.HttpError(404, url)
            return meta(f"L{day:%m%d}", day)

        c = ctx(today=today)
        with mock.patch.object(tr.http, "get_json", get_json):
            return tr.resolve_lists(c), asked, c

    def test_latest_and_exact_offsets(self):
        (newest, older), asked, c = self.resolve(date(2026, 10, 1), date(2026, 10, 1))
        self.assertEqual(newest, tr.TrancoList("L1001", date(2026, 10, 1)))
        self.assertEqual(older, {30: tr.TrancoList("L0901", date(2026, 9, 1)),
                                 90: tr.TrancoList("L0703", date(2026, 7, 3))})
        self.assertEqual(asked, ["latest", "2026-09-01", "2026-07-03"])
        self.assertEqual(c.warnings, [])

    def test_latest_pointer_lagging_today_is_fine(self):
        # Card gotcha 5: "latest" can trail the calendar by a day. Dates come from the list.
        (newest, older), _, c = self.resolve(date(2026, 10, 2), date(2026, 9, 30))
        self.assertEqual(newest.day, date(2026, 9, 30))
        self.assertEqual(older[30].day, date(2026, 8, 31))
        self.assertEqual(c.warnings, [])

    def test_missing_day_takes_the_nearest(self):
        gone = {date(2026, 9, 1), date(2026, 8, 31)}
        (newest, older), asked, _ = self.resolve(date(2026, 10, 1), date(2026, 10, 1), gone)
        self.assertEqual(older[30].day, date(2026, 9, 2))
        self.assertEqual(asked[1:4], ["2026-09-01", "2026-08-31", "2026-09-02"])

    def test_no_list_within_three_days_drops_the_window(self):
        gone = {date(2026, 9, 1) + timedelta(days=k) for k in range(-3, 4)}
        (newest, older), _, c = self.resolve(date(2026, 10, 1), date(2026, 10, 1), gone)
        self.assertEqual(set(older), {90})
        self.assertEqual(len(c.warnings), 1)

    def test_as_of_an_earlier_date(self):
        (newest, older), asked, _ = self.resolve(date(2026, 9, 10), date(2026, 10, 1))
        self.assertEqual(newest.day, date(2026, 9, 10))
        self.assertEqual(older[30].day, date(2026, 8, 11))
        self.assertEqual(asked[:2], ["latest", "2026-09-10"])

    def test_no_latest(self):
        resolved, _, c = self.resolve(date(2026, 10, 1), date(2026, 10, 1), {date(2026, 10, 1)})
        self.assertIsNone(resolved)
        self.assertTrue(c.warnings)

    def test_very_old_latest_warns_but_runs(self):
        (newest, _), _, c = self.resolve(date(2026, 10, 1), date(2026, 9, 15))
        self.assertEqual(newest.day, date(2026, 9, 15))
        self.assertTrue(any("days before today" in w for w in c.warnings))


if __name__ == "__main__":
    unittest.main()
