"""Review entries follow a company through a rename."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from antenna import review
from antenna.db import connect, insert_signals
from antenna.models import EntityHint, Signal
from antenna.resolve import resolve


class ReviewFile(unittest.TestCase):
    """Points the review module at a temporary file for one test."""

    entries: dict = {}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        path = Path(self.tmp.name) / "review.json"
        path.write_text(json.dumps(self.entries))
        self.patch = mock.patch.object(review, "REVIEW_PATH", path)
        self.patch.start()
        review.load.cache_clear()
        review.carry_over([])

    def tearDown(self):
        self.patch.stop()
        review.load.cache_clear()
        review.carry_over([])
        self.tmp.cleanup()


class CarryOver(ReviewFile):
    entries = {
        "solvecraft": {"reviewed": True, "exclude": "One person's repository"},
        "acme": {"reviewed": True, "stage": "early"},
        "shared": {"reviewed": True, "stage": "growth"},
    }

    def test_entry_follows_the_old_name(self):
        moves = review.carry_over([("tangency-labs", {"solvecraft", "solidcraft"})])
        self.assertEqual(moves, {"tangency-labs": "solvecraft"})
        self.assertEqual(review.for_slug("tangency-labs")["exclude"], "One person's repository")

    def test_not_while_a_row_still_holds_the_slug(self):
        review.carry_over([("acme", set()), ("acme-robotics", {"acme"})])
        self.assertEqual(review.for_slug("acme-robotics"), {})
        self.assertEqual(review.for_slug("acme")["stage"], "early")

    def test_two_claimants_get_nothing(self):
        review.carry_over([("one", {"shared"}), ("two", {"shared"})])
        self.assertEqual(review.for_slug("one"), {})
        self.assertEqual(review.for_slug("two"), {})

    def test_own_entry_wins(self):
        review.carry_over([("acme", {"solvecraft"})])
        self.assertEqual(review.for_slug("acme")["stage"], "early")

    def test_each_run_starts_clean(self):
        review.carry_over([("tangency-labs", {"solvecraft"})])
        review.carry_over([("solvecraft", set())])
        self.assertEqual(review.for_slug("tangency-labs"), {})


class RenamedInResolution(ReviewFile):
    entries = {"old-name-robotics": {"reviewed": True, "location": "Austin, TX"}}

    def test_resolution_applies_the_entry_to_the_renamed_row(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        conn = connect(Path(tmp.name) / "t.db")
        self.addCleanup(conn.close)
        run = conn.execute("INSERT INTO runs (started_at) VALUES ('x')").lastrowid
        insert_signals(conn, run, [Signal(
            source="github_velocity", family="github", kind="star_velocity",
            entity=EntityHint(name="New Name Robotics", github="newname",
                              aliases=["Old Name Robotics"]),
            title="New repo", occurred_at="2026-10-18",
            url="https://github.com/newname/robot", strength=0.5,
        )])
        resolve(conn)
        row = conn.execute("SELECT slug, location FROM entities").fetchone()
        self.assertEqual(row["slug"], "new-name-robotics")
        self.assertEqual(row["location"], "Austin, TX")
        self.assertEqual(review.for_slug("new-name-robotics")["location"], "Austin, TX")


if __name__ == "__main__":
    unittest.main()
