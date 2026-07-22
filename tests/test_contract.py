"""Tests for the versioned JSON data contract (app/contract.py)."""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.contract import ContractError, validate_and_normalize

GOOD_RECORD = {
    "date_found": "2026-07-05",
    "title": "Example",
    "authors": "A. Example",
    "ids": [
        {
            "kind": "arXiv",
            "label": "arXiv:2606.07441",
            "url": "https://arxiv.org/abs/2606.07441",
        }
    ],
    "tracks": [1, 8],
    "section": "relevant",
    "warn": False,
    "linkedin": True,
    "raw_id": "arXiv:2606.07441",
}


def make_payload(**overrides):
    payload = {
        "contract_version": "1.0",
        "last_updated": "2026-07-22",
        "records": [GOOD_RECORD],
    }
    payload.update(overrides)
    return payload


class ValidPayloadTests(unittest.TestCase):
    def test_valid_payload_passes_through_unchanged(self):
        payload = make_payload()
        result = validate_and_normalize(payload)
        self.assertEqual(result, payload)

    def test_minor_version_bump_is_accepted(self):
        # 1.1, 1.42 etc should all be fine -- only the major version gates.
        validate_and_normalize(make_payload(contract_version="1.7"))

    def test_empty_records_list_is_valid(self):
        validate_and_normalize(make_payload(records=[]))

    def test_record_with_summary_field_is_valid(self):
        record = dict(GOOD_RECORD, summary="A short summary of the finding.")
        validate_and_normalize(make_payload(records=[record]))

    def test_record_without_summary_field_is_valid(self):
        # summary is optional -- 1.0-era producers that never send it stay valid.
        validate_and_normalize(make_payload(records=[GOOD_RECORD]))


class InvalidPayloadTests(unittest.TestCase):
    def test_not_a_dict_raises(self):
        with self.assertRaises(ContractError):
            validate_and_normalize(["not", "a", "dict"])

    def test_missing_contract_version_raises(self):
        payload = make_payload()
        del payload["contract_version"]
        with self.assertRaises(ContractError):
            validate_and_normalize(payload)

    def test_unsupported_major_version_raises(self):
        with self.assertRaises(ContractError):
            validate_and_normalize(make_payload(contract_version="2.0"))

    def test_missing_last_updated_raises(self):
        payload = make_payload()
        del payload["last_updated"]
        with self.assertRaises(ContractError):
            validate_and_normalize(payload)

    def test_records_not_a_list_raises(self):
        with self.assertRaises(ContractError):
            validate_and_normalize(make_payload(records="not a list"))

    def test_record_missing_field_raises(self):
        bad_record = dict(GOOD_RECORD)
        del bad_record["tracks"]
        with self.assertRaises(ContractError):
            validate_and_normalize(make_payload(records=[bad_record]))

    def test_record_invalid_section_raises(self):
        bad_record = dict(GOOD_RECORD, section="maybe")
        with self.assertRaises(ContractError):
            validate_and_normalize(make_payload(records=[bad_record]))

    def test_record_tracks_not_a_list_raises(self):
        bad_record = dict(GOOD_RECORD, tracks="1, 8")
        with self.assertRaises(ContractError):
            validate_and_normalize(make_payload(records=[bad_record]))

    def test_record_ids_not_a_list_raises(self):
        bad_record = dict(GOOD_RECORD, ids="arXiv:2606.07441")
        with self.assertRaises(ContractError):
            validate_and_normalize(make_payload(records=[bad_record]))

    def test_record_summary_not_a_string_raises(self):
        bad_record = dict(GOOD_RECORD, summary=123)
        with self.assertRaises(ContractError):
            validate_and_normalize(make_payload(records=[bad_record]))


if __name__ == "__main__":
    unittest.main()
