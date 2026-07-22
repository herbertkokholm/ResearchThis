"""Tests for the read-only Zotero reconciliation logic (no live API calls)."""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.reconcile_zotero import (
    build_report,
    extract_zotero_identifiers,
    format_creators,
    library_cards,
    normalize_doi,
)


def zitem(key, doi=None, arxiv=None, title="Untitled"):
    data = {"key": key, "title": title}
    if doi:
        data["DOI"] = doi
    if arxiv:
        data["extra"] = f"arXiv:{arxiv}"
    return {"key": key, "data": data}


class NormalizeDoiTests(unittest.TestCase):
    def test_strips_url_prefix_and_lowercases(self):
        self.assertEqual(
            normalize_doi("https://doi.org/10.1234/ABC.5"), "10.1234/abc.5"
        )
        self.assertEqual(normalize_doi("10.1234/ABC.5"), "10.1234/abc.5")


class ExtractIdentifiersTests(unittest.TestCase):
    def test_extracts_doi_and_arxiv(self):
        item = zitem("K1", doi="10.1000/xyz", arxiv="2606.08076")
        ids = extract_zotero_identifiers(item)
        self.assertEqual(ids["doi"], "10.1000/xyz")
        self.assertEqual(ids["arxiv"], "2606.08076")

    def test_no_identifiers_returns_empty(self):
        self.assertEqual(extract_zotero_identifiers(zitem("K1")), {})


class BuildReportTests(unittest.TestCase):
    def test_matches_by_doi(self):
        findings = [
            {
                "title": "Found via DOI",
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
        zotero_items = [zitem("K1", doi="10.1000/xyz", title="Found via DOI")]
        report = build_report(findings, zotero_items)
        self.assertEqual(report["summary"]["already_in_zotero"], 1)
        self.assertEqual(report["summary"]["missing_from_zotero"], 0)
        self.assertEqual(
            report["already_in_zotero"][0]["zotero_matches"][0]["key"], "K1"
        )

    def test_matches_by_doi_lowercase_kind(self):
        # The upstream feed sends lowercase "doi"/"arxiv" kinds; matching
        # must not be case-sensitive.
        findings = [
            {
                "title": "Found via lowercase doi",
                "raw_id": "10.1000/xyz",
                "ids": [
                    {
                        "kind": "doi",
                        "label": "10.1000/xyz",
                        "url": "https://doi.org/10.1000/xyz",
                    }
                ],
            }
        ]
        zotero_items = [zitem("K1", doi="10.1000/xyz", title="Found via lowercase doi")]
        report = build_report(findings, zotero_items)
        self.assertEqual(report["summary"]["already_in_zotero"], 1)
        self.assertEqual(report["summary"]["missing_from_zotero"], 0)

    def test_matches_by_doi_despite_annotated_label(self):
        # Some producer records append a citation/verification note to the
        # DOI label; `url` stays a clean doi.org link and must be preferred
        # for matching.
        findings = [
            {
                "title": "Found despite annotated label",
                "raw_id": "10.1000/xyz (Some Journal, 2026-01-01)",
                "ids": [
                    {
                        "kind": "DOI",
                        "label": "10.1000/xyz (Some Journal, 2026-01-01)",
                        "url": "https://doi.org/10.1000/xyz",
                    }
                ],
            }
        ]
        zotero_items = [
            zitem("K1", doi="10.1000/xyz", title="Found despite annotated label")
        ]
        report = build_report(findings, zotero_items)
        self.assertEqual(report["summary"]["already_in_zotero"], 1)
        self.assertEqual(report["summary"]["missing_from_zotero"], 0)

    def test_matches_by_arxiv(self):
        findings = [
            {
                "title": "Found via arXiv",
                "raw_id": "arXiv:2606.08076",
                "ids": [
                    {
                        "kind": "arXiv",
                        "label": "arXiv:2606.08076",
                        "url": "https://arxiv.org/abs/2606.08076",
                    }
                ],
            }
        ]
        zotero_items = [zitem("K2", arxiv="2606.08076", title="Found via arXiv")]
        report = build_report(findings, zotero_items)
        self.assertEqual(report["summary"]["already_in_zotero"], 1)

    def test_missing_when_no_match(self):
        findings = [
            {
                "title": "Not In Zotero",
                "raw_id": "10.9999/nope",
                "ids": [
                    {
                        "kind": "DOI",
                        "label": "10.9999/nope",
                        "url": "https://doi.org/10.9999/nope",
                    }
                ],
            }
        ]
        report = build_report(findings, [zitem("K1", doi="10.1000/other")])
        self.assertEqual(report["summary"]["missing_from_zotero"], 1)
        self.assertEqual(report["summary"]["already_in_zotero"], 0)

    def test_finding_with_no_ids_is_missing(self):
        findings = [{"title": "No IDs At All", "raw_id": "n/a", "ids": []}]
        report = build_report(findings, [zitem("K1", doi="10.1000/other")])
        self.assertEqual(report["summary"]["missing_from_zotero"], 1)

    def test_detects_doi_duplicates_in_zotero(self):
        zotero_items = [
            zitem("K1", doi="10.1000/dup", title="Copy A"),
            zitem("K2", doi="10.1000/dup", title="Copy B"),
            zitem("K3", doi="10.1000/unique", title="Solo"),
        ]
        report = build_report([], zotero_items)
        self.assertEqual(report["summary"]["doi_duplicates_in_zotero"], 1)
        self.assertIn("10.1000/dup", report["doi_duplicates_in_zotero"])
        self.assertEqual(len(report["doi_duplicates_in_zotero"]["10.1000/dup"]), 2)

    def test_multiple_matches_deduped_by_key(self):
        # A finding with both DOI and arXiv ids pointing at the same Zotero item
        # should report exactly one match, not two.
        findings = [
            {
                "title": "Dual ID",
                "raw_id": "arXiv:2606.08076 · 10.1000/xyz",
                "ids": [
                    {
                        "kind": "arXiv",
                        "label": "arXiv:2606.08076",
                        "url": "https://arxiv.org/abs/2606.08076",
                    },
                    {
                        "kind": "DOI",
                        "label": "10.1000/xyz",
                        "url": "https://doi.org/10.1000/xyz",
                    },
                ],
            }
        ]
        zotero_items = [
            zitem("K1", doi="10.1000/xyz", arxiv="2606.08076", title="Dual ID")
        ]
        report = build_report(findings, zotero_items)
        self.assertEqual(len(report["already_in_zotero"][0]["zotero_matches"]), 1)


class FormatCreatorsTests(unittest.TestCase):
    def test_joins_first_and_last_name(self):
        self.assertEqual(
            format_creators([{"firstName": "Ada", "lastName": "Lovelace"}]),
            "Ada Lovelace",
        )

    def test_uses_single_name_field_for_institutional_creators(self):
        self.assertEqual(format_creators([{"name": "OpenAI"}]), "OpenAI")

    def test_multiple_creators_joined_with_comma(self):
        creators = [
            {"firstName": "Ada", "lastName": "Lovelace"},
            {"name": "OpenAI"},
        ]
        self.assertEqual(format_creators(creators), "Ada Lovelace, OpenAI")

    def test_empty_or_missing_creators(self):
        self.assertEqual(format_creators([]), "")
        self.assertEqual(format_creators(None), "")


class LibraryCardsTests(unittest.TestCase):
    def test_flags_missing_doi(self):
        items = [
            {
                "data": {
                    "key": "K1",
                    "itemType": "journalArticle",
                    "title": "Has DOI",
                    "DOI": "10.1/x",
                }
            },
            {"data": {"key": "K2", "itemType": "journalArticle", "title": "No DOI"}},
        ]
        cards = library_cards(items)
        self.assertEqual(len(cards), 2)
        self.assertFalse(next(c for c in cards if c["key"] == "K1")["missing_doi"])
        self.assertTrue(next(c for c in cards if c["key"] == "K2")["missing_doi"])

    def test_filters_out_attachments_and_notes(self):
        items = [
            {"data": {"key": "K1", "itemType": "journalArticle", "title": "Article"}},
            {"data": {"key": "K2", "itemType": "attachment", "title": "some.pdf"}},
            {"data": {"key": "K3", "itemType": "note"}},
        ]
        cards = library_cards(items)
        self.assertEqual([c["key"] for c in cards], ["K1"])

    def test_build_report_includes_library(self):
        items = [
            {
                "data": {
                    "key": "K1",
                    "itemType": "journalArticle",
                    "title": "Has DOI",
                    "DOI": "10.1/x",
                }
            },
            {"data": {"key": "K2", "itemType": "journalArticle", "title": "No DOI"}},
        ]
        report = build_report([], items)
        self.assertEqual(report["summary"]["library_total"], 2)
        self.assertEqual(report["summary"]["library_missing_doi"], 1)
        self.assertEqual(len(report["library"]), 2)

    def test_marks_library_cards_matched_by_a_finding(self):
        findings = [
            {
                "title": "Found via DOI",
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
        items = [
            zitem("K1", doi="10.1000/xyz", title="Found via DOI"),
        ]
        items[0]["data"]["itemType"] = "journalArticle"
        items.append(
            {"data": {"key": "K2", "itemType": "journalArticle", "title": "Unrelated"}}
        )
        report = build_report(findings, items)
        by_key = {c["key"]: c for c in report["library"]}
        self.assertTrue(by_key["K1"]["in_feed"])
        self.assertFalse(by_key["K2"]["in_feed"])
        self.assertEqual(report["summary"]["library_in_feed"], 1)


if __name__ == "__main__":
    unittest.main()
