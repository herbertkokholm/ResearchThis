"""Tests for app.chat.answer_question (no live OpenAI calls)."""

from __future__ import annotations

import json
import os
import sys
import unittest
import urllib.error
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.chat import (
    MAX_QUESTION_LENGTH,
    ChatError,
    _target_language,
    answer_question,
)

RECORDS = [
    {
        "date_found": "2026-01-05",
        "title": "Static Analysis Meets Generative AI: A Hybrid Approach to Bug Detection",
        "authors": "E. Researcher",
        "ids": [
            {"kind": "DOI", "label": "10.1234/x", "url": "https://doi.org/10.1234/x"}
        ],
        "tracks": [2],
        "section": "relevant",
        "warn": False,
        "linkedin": False,
        "raw_id": "static-analysis",
    },
    {
        "date_found": "2026-01-11",
        "title": "Multi-Agent Code Review Pipelines: Governance and Oversight Patterns",
        "authors": "I. Analyst",
        "ids": [],
        "tracks": [2, 4],
        "section": "relevant",
        "warn": False,
        "linkedin": False,
        "raw_id": "multi-agent-review",
    },
    {
        "date_found": "2026-01-14",
        "title": "Toward Trustworthy Developer Tooling: An Ethics Checklist",
        "authors": "P. Person",
        "ids": [],
        "tracks": [3],
        "section": "related",
        "warn": False,
        "linkedin": False,
        "raw_id": "ethics-checklist",
    },
]

ZOTERO_LIBRARY = [
    {
        "key": "ZKEY1",
        "title": "Deep Learning for Code Review Automation",
        "creators": "A. Zotero",
        "date": "2025",
        "item_type": "journalArticle",
        "doi": "10.9999/zot1",
        "missing_doi": False,
        "in_feed": False,
    },
    {
        "key": "ZKEY2",
        "title": "Unrelated Gardening Techniques",
        "creators": "B. Person",
        "date": "2024",
        "item_type": "book",
        "doi": None,
        "missing_doi": True,
        "in_feed": False,
    },
]


def _fake_openai_response(answer="Here's what I found."):
    payload = {"choices": [{"message": {"content": answer}}]}
    fake = mock.MagicMock()
    fake.read.return_value = json.dumps(payload).encode("utf-8")
    fake.__enter__.return_value = fake
    fake.__exit__.return_value = False
    return fake


class TargetLanguageTests(unittest.TestCase):
    """gpt-4o has been observed answering in a third language entirely when
    asked to detect-and-match the question's language itself (see app/chat.py
    module docstring) -- these lock in the da/en heuristic that replaced it."""

    def setUp(self):
        self._saved = dict(os.environ)
        os.environ["OPENAI_API_KEY"] = "sk-test"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)

    def test_english_question(self):
        self.assertEqual(
            _target_language("What paper is about LLM-as-a-judge?"), "English"
        )

    def test_danish_question_by_special_chars(self):
        self.assertEqual(_target_language("Hvad har I om kodegennemgang?"), "Danish")

    def test_danish_question_by_stopword_without_special_chars(self):
        self.assertEqual(
            _target_language("hvilket paper handler om LLM-as-a-judge?"), "Danish"
        )

    def test_sent_prompt_carries_the_detected_language(self):
        fake_resp = _fake_openai_response()
        with mock.patch(
            "urllib.request.urlopen", return_value=fake_resp
        ) as mock_urlopen:
            answer_question("Hvad har I om kodegennemgang?", RECORDS)
        sent_body = json.loads(mock_urlopen.call_args[0][0].data)
        self.assertIn("Reply language: Danish", sent_body["messages"][1]["content"])
        self.assertEqual(sent_body["temperature"], 0)


class AnswerQuestionValidationTests(unittest.TestCase):
    def setUp(self):
        self._saved = dict(os.environ)
        os.environ["OPENAI_API_KEY"] = "sk-test"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)

    def test_empty_question_is_rejected(self):
        with self.assertRaises(ChatError) as ctx:
            answer_question("   ", RECORDS)
        self.assertEqual(ctx.exception.status, 400)

    def test_oversized_question_is_rejected(self):
        with self.assertRaises(ChatError) as ctx:
            answer_question("x" * (MAX_QUESTION_LENGTH + 1), RECORDS)
        self.assertEqual(ctx.exception.status, 400)

    def test_missing_api_key_raises_503(self):
        os.environ.pop("OPENAI_API_KEY", None)
        with self.assertRaises(ChatError) as ctx:
            answer_question("what do you have on code review?", RECORDS)
        self.assertEqual(ctx.exception.status, 503)


class AnswerQuestionRetrievalTests(unittest.TestCase):
    """Retrieval reuses server.filter_records -- these tests exercise that
    without mocking, so a change to filter_records' matching semantics is
    caught here too."""

    def setUp(self):
        self._saved = dict(os.environ)
        os.environ["OPENAI_API_KEY"] = "sk-test"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)

    def test_keyword_match_ranks_matching_record_first(self):
        fake_resp = _fake_openai_response()
        with mock.patch(
            "urllib.request.urlopen", return_value=fake_resp
        ) as mock_urlopen:
            result = answer_question("What code review pipelines do you have?", RECORDS)

        # the sent prompt should be built only from records that matched a
        # keyword -- the unrelated ethics-checklist record is left out
        sent_body = json.loads(mock_urlopen.call_args[0][0].data)
        prompt = sent_body["messages"][1]["content"]
        self.assertIn("Multi-Agent Code Review Pipelines", prompt)
        self.assertNotIn("Toward Trustworthy Developer Tooling", prompt)
        self.assertEqual(result["answer"], "Here's what I found.")
        self.assertEqual(result["sources"][0]["raw_id"], "multi-agent-review")
        # multi-agent-review has no ids in the fixture -> no link
        self.assertIsNone(result["sources"][0]["url"])

    def test_source_url_comes_from_first_id(self):
        fake_resp = _fake_openai_response()
        with mock.patch("urllib.request.urlopen", return_value=fake_resp):
            result = answer_question("static analysis bug detection", RECORDS)
        source = next(s for s in result["sources"] if s["raw_id"] == "static-analysis")
        self.assertEqual(source["url"], "https://doi.org/10.1234/x")

    def test_no_keyword_match_still_answers_but_reports_no_sources(self):
        fake_resp = _fake_openai_response()
        with mock.patch(
            "urllib.request.urlopen", return_value=fake_resp
        ) as mock_urlopen:
            result = answer_question("xyzxyz nonsense query", RECORDS)
        # the model still gets the most recent findings as fallback context...
        sent_body = json.loads(mock_urlopen.call_args[0][0].data)
        self.assertIn(
            "Toward Trustworthy Developer Tooling", sent_body["messages"][1]["content"]
        )
        # ...but that fallback context isn't relevant, so it must not be
        # presented to the user as if it were the answer's sources
        self.assertEqual(result["sources"], [])


class AnswerQuestionZoteroTests(unittest.TestCase):
    """Zotero library retrieval -- a separate, optional source alongside the
    findings feed (see app/chat.py's _select_zotero_context)."""

    def setUp(self):
        self._saved = dict(os.environ)
        os.environ["OPENAI_API_KEY"] = "sk-test"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)

    def test_matching_zotero_item_is_included_as_a_zotero_source(self):
        fake_resp = _fake_openai_response()
        with mock.patch(
            "urllib.request.urlopen", return_value=fake_resp
        ) as mock_urlopen:
            result = answer_question(
                "What do you have on code review automation?",
                RECORDS,
                zotero_library=ZOTERO_LIBRARY,
            )

        sent_body = json.loads(mock_urlopen.call_args[0][0].data)
        prompt = sent_body["messages"][1]["content"]
        self.assertIn("Zotero library:", prompt)
        self.assertIn("Deep Learning for Code Review Automation", prompt)
        self.assertNotIn("Unrelated Gardening Techniques", prompt)

        zotero_sources = [s for s in result["sources"] if s["type"] == "zotero"]
        self.assertEqual(len(zotero_sources), 1)
        self.assertEqual(zotero_sources[0]["key"], "ZKEY1")
        self.assertEqual(zotero_sources[0]["url"], "https://doi.org/10.9999/zot1")

    def test_zotero_item_without_doi_has_no_url(self):
        fake_resp = _fake_openai_response()
        with mock.patch("urllib.request.urlopen", return_value=fake_resp):
            result = answer_question(
                "Anything on gardening techniques?",
                RECORDS,
                zotero_library=ZOTERO_LIBRARY,
            )
        zotero_sources = [s for s in result["sources"] if s["type"] == "zotero"]
        self.assertEqual(zotero_sources[0]["key"], "ZKEY2")
        self.assertIsNone(zotero_sources[0]["url"])

    def test_unmatched_zotero_library_contributes_no_block_or_sources(self):
        fake_resp = _fake_openai_response()
        with mock.patch(
            "urllib.request.urlopen", return_value=fake_resp
        ) as mock_urlopen:
            result = answer_question(
                "xyzxyz nonsense query", RECORDS, zotero_library=ZOTERO_LIBRARY
            )
        sent_body = json.loads(mock_urlopen.call_args[0][0].data)
        self.assertNotIn("Zotero library", sent_body["messages"][1]["content"])
        self.assertEqual(result["sources"], [])

    def test_no_zotero_library_passed_behaves_as_before(self):
        fake_resp = _fake_openai_response()
        with mock.patch("urllib.request.urlopen", return_value=fake_resp):
            result = answer_question("code review pipelines", RECORDS)
        self.assertTrue(all(s["type"] == "finding" for s in result["sources"]))


class AnswerQuestionUpstreamErrorTests(unittest.TestCase):
    def setUp(self):
        self._saved = dict(os.environ)
        os.environ["OPENAI_API_KEY"] = "sk-test"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)

    def test_http_error_is_reported_as_502(self):
        err = urllib.error.HTTPError(
            "https://api.openai.com/v1/chat/completions",
            401,
            "unauthorized",
            {},
            None,
        )
        err.read = lambda: b'{"error": "bad key"}'
        with (
            mock.patch("urllib.request.urlopen", side_effect=err),
            self.assertRaises(ChatError) as ctx,
        ):
            answer_question("code review", RECORDS)
        self.assertEqual(ctx.exception.status, 502)

    def test_malformed_upstream_response_is_reported_as_502(self):
        fake = mock.MagicMock()
        fake.read.return_value = b"{}"  # no "choices"
        fake.__enter__.return_value = fake
        fake.__exit__.return_value = False
        with (
            mock.patch("urllib.request.urlopen", return_value=fake),
            self.assertRaises(ChatError) as ctx,
        ):
            answer_question("code review", RECORDS)
        self.assertEqual(ctx.exception.status, 502)


if __name__ == "__main__":
    unittest.main()
