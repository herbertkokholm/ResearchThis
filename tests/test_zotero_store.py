"""Tests for the ZoteroStore TTL cache (no live Zotero calls)."""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.zotero_store import ZoteroStore


def _clear_zotero_env():
    for key in (
        "ZOTERO_LIBRARY_ID",
        "ZOTERO_LIBRARY_TYPE",
        "ZOTERO_API_KEY",
        "ZOTERO_APP_KEY",
    ):
        os.environ.pop(key, None)


class ZoteroStoreUnconfiguredTests(unittest.TestCase):
    def setUp(self):
        self._saved = dict(os.environ)
        _clear_zotero_env()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)

    def test_unconfigured_without_credentials(self):
        store = ZoteroStore()
        result = store.get_report([])
        self.assertEqual(result["configured"], False)
        self.assertIsNone(result["report"])


class ZoteroStoreConfiguredTests(unittest.TestCase):
    def setUp(self):
        self._saved = dict(os.environ)
        _clear_zotero_env()
        os.environ["ZOTERO_LIBRARY_ID"] = "12345"
        os.environ["ZOTERO_LIBRARY_TYPE"] = "user"
        os.environ["ZOTERO_API_KEY"] = "fake-key"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)

    def test_fetches_and_caches_report(self):
        store = ZoteroStore()
        fake_items = [
            {"key": "K1", "data": {"key": "K1", "DOI": "10.1000/xyz", "title": "T"}}
        ]
        findings = [
            {
                "title": "T",
                "raw_id": "10.1000/xyz",
                "ids": [
                    {
                        "kind": "DOI",
                        "label": "10.1000/xyz",
                        "url": "https://doi.org/10.1000/xyz",
                    }
                ],
            }
        ]
        with mock.patch(
            "app.reconcile_zotero.fetch_zotero_items", return_value=fake_items
        ) as fetch_mock:
            result = store.get_report(findings)
            self.assertTrue(result["configured"])
            self.assertIsNotNone(result["report"])
            self.assertEqual(result["report"]["summary"]["already_in_zotero"], 1)
            self.assertIsNotNone(result["fetched_at"])
            self.assertEqual(fetch_mock.call_count, 1)

            # second call within TTL should NOT re-fetch
            store.get_report(findings)
            self.assertEqual(fetch_mock.call_count, 1)

            # forced refresh should re-fetch
            store.get_report(findings, force=True)
            self.assertEqual(fetch_mock.call_count, 2)

    def test_fetch_failure_keeps_previous_report_and_records_error(self):
        store = ZoteroStore()
        fake_items = [
            {"key": "K1", "data": {"key": "K1", "DOI": "10.1000/xyz", "title": "T"}}
        ]
        findings = [
            {
                "title": "T",
                "raw_id": "10.1000/xyz",
                "ids": [
                    {
                        "kind": "DOI",
                        "label": "10.1000/xyz",
                        "url": "https://doi.org/10.1000/xyz",
                    }
                ],
            }
        ]
        with mock.patch(
            "app.reconcile_zotero.fetch_zotero_items", return_value=fake_items
        ):
            first = store.get_report(findings)
            self.assertIsNotNone(first["report"])

        with mock.patch(
            "app.reconcile_zotero.fetch_zotero_items", side_effect=RuntimeError("boom")
        ):
            second = store.get_report(findings, force=True)
            self.assertTrue(second["configured"])
            self.assertEqual(second["last_error"], "boom")
            # previous good report is still served
            self.assertIsNotNone(second["report"])
            self.assertEqual(second["report"]["summary"]["already_in_zotero"], 1)


if __name__ == "__main__":
    unittest.main()
