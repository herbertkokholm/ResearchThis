"""Tests for app.server.filter_records (query-string record filtering)."""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.server import filter_records


def rec(**overrides):
    base = {
        "date_found": "2026-07-05",
        "title": "T",
        "authors": "A",
        "ids": [],
        "tracks": [1],
        "section": "relevant",
        "warn": False,
        "linkedin": False,
        "raw_id": "",
    }
    base.update(overrides)
    return base


class FilterRecordsLinkedinTests(unittest.TestCase):
    def setUp(self):
        self.records = [rec(raw_id="a", linkedin=True), rec(raw_id="b", linkedin=False)]

    def test_linkedin_filter_applies_when_enabled(self):
        kept = filter_records(
            self.records, {"linkedin": ["true"]}, linkedin_enabled=True
        )
        self.assertEqual([r["raw_id"] for r in kept], ["a"])

    def test_linkedin_filter_is_noop_when_disabled(self):
        kept = filter_records(
            self.records, {"linkedin": ["true"]}, linkedin_enabled=False
        )
        self.assertEqual([r["raw_id"] for r in kept], ["a", "b"])

    def test_linkedin_enabled_defaults_to_true_for_existing_callers(self):
        kept = filter_records(self.records, {"linkedin": ["true"]})
        self.assertEqual([r["raw_id"] for r in kept], ["a"])

    def test_linkedin_field_stays_on_records_regardless_of_flag(self):
        kept = filter_records(self.records, {}, linkedin_enabled=False)
        self.assertTrue(all("linkedin" in r for r in kept))


if __name__ == "__main__":
    unittest.main()
