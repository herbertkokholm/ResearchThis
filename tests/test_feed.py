"""Tests for app.feed.build_feed (contract dict -> full portal feed)."""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.feed import build_feed

TRACKS = {
    "1": {"full": "Track One", "short": "T1", "color": "#111111"},
    "2": {"full": "Track Two", "short": "T2", "color": "#222222"},
}
MANIFEST = {
    "feed_id": "test-feed",
    "heading": "Test Heading",
    "subtitle": "Test Subtitle",
    "owner": "Test Owner",
    "language": "en",
}


def make_contract(records):
    return {"contract_version": "1.0", "last_updated": "2026-07-22", "records": records}


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


class BuildFeedTests(unittest.TestCase):
    def test_assembles_manifest_and_tracks(self):
        feed = build_feed(make_contract([]), TRACKS, MANIFEST)
        self.assertEqual(feed["feed_id"], "test-feed")
        self.assertEqual(feed["heading"], "Test Heading")
        self.assertEqual(feed["owner"], "Test Owner")
        self.assertEqual(feed["tracks"], TRACKS)
        self.assertEqual(feed["last_updated"], "2026-07-22")

    def test_counts_are_recomputed_not_trusted(self):
        records = [
            rec(section="relevant", warn=True, tracks=[1, 2]),
            rec(section="related", linkedin=True, tracks=[2]),
        ]
        feed = build_feed(make_contract(records), TRACKS, MANIFEST)
        self.assertEqual(feed["counts"]["total"], 2)
        self.assertEqual(feed["counts"]["relevant"], 1)
        self.assertEqual(feed["counts"]["related"], 1)
        self.assertEqual(feed["counts"]["warn"], 1)
        self.assertEqual(feed["counts"]["linkedin"], 1)
        self.assertEqual(feed["counts"]["by_track"], {"1": 1, "2": 2})

    def test_source_last_modified_passed_through(self):
        feed = build_feed(
            make_contract([]),
            TRACKS,
            MANIFEST,
            source_last_modified="2026-07-22T10:00:00Z",
        )
        self.assertEqual(feed["source_last_modified"], "2026-07-22T10:00:00Z")

    def test_source_last_modified_defaults_to_none(self):
        feed = build_feed(make_contract([]), TRACKS, MANIFEST)
        self.assertIsNone(feed["source_last_modified"])

    def test_chat_enabled_defaults_to_false(self):
        feed = build_feed(make_contract([]), TRACKS, MANIFEST)
        self.assertFalse(feed["chat_enabled"])

    def test_chat_enabled_true_opts_in(self):
        feed = build_feed(make_contract([]), TRACKS, {**MANIFEST, "chat_enabled": True})
        self.assertTrue(feed["chat_enabled"])

    def test_reels_enabled_defaults_to_false(self):
        feed = build_feed(make_contract([]), TRACKS, MANIFEST)
        self.assertFalse(feed["reels_enabled"])

    def test_reels_enabled_true_opts_in(self):
        feed = build_feed(
            make_contract([]), TRACKS, {**MANIFEST, "reels_enabled": True}
        )
        self.assertTrue(feed["reels_enabled"])


if __name__ == "__main__":
    unittest.main()
