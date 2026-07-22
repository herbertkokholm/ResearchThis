"""Tests for app.s3sync.fetch_findings_contract (no live network calls)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.contract import ContractError
from app.s3sync import fetch_findings_contract

VALID_CONTRACT = {
    "contract_version": "1.0",
    "last_updated": "2026-07-22",
    "records": [],
}


def _write_json(tmpdir, name, data):
    path = os.path.join(tmpdir, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return path


class LocalFallbackTests(unittest.TestCase):
    def setUp(self):
        self._saved = dict(os.environ)
        os.environ.pop("S3_BUCKET", None)
        os.environ.pop("S3_FINDINGS_KEY", None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)

    def test_uses_local_fallback_when_s3_not_configured(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_json(tmp, "fallback.json", VALID_CONTRACT)
            contract, source_last_modified = fetch_findings_contract(path)
            self.assertEqual(contract["contract_version"], "1.0")
            self.assertIsNone(source_last_modified)

    def test_invalid_local_fallback_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_json(
                tmp, "bad.json", {"records": []}
            )  # missing contract_version
            with self.assertRaises(ContractError):
                fetch_findings_contract(path)


class S3PathTests(unittest.TestCase):
    def setUp(self):
        self._saved = dict(os.environ)
        os.environ["S3_BUCKET"] = "test-bucket"
        os.environ["S3_FINDINGS_KEY"] = "findings.json"
        os.environ["S3_ROOT_FOLDER"] = "ResearchThis/"
        os.environ["AWS_REGION"] = "eu-north-1"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)

    def _mock_s3_client(self, body_dict, last_modified="2026-07-22T10:00:00+00:00"):
        mock_client = mock.MagicMock()
        body_bytes = json.dumps(body_dict).encode("utf-8")
        mock_client.get_object.return_value = {
            "Body": mock.MagicMock(read=lambda: body_bytes),
            "LastModified": mock.MagicMock(isoformat=lambda: last_modified),
        }
        return mock_client

    def test_fetches_valid_contract_from_s3(self):
        with tempfile.TemporaryDirectory() as tmp:
            fallback = _write_json(tmp, "fallback.json", VALID_CONTRACT)
            mock_client = self._mock_s3_client(VALID_CONTRACT)
            with mock.patch("boto3.client", return_value=mock_client):
                _contract, source_last_modified = fetch_findings_contract(fallback)
            self.assertEqual(source_last_modified, "2026-07-22T10:00:00+00:00")
            mock_client.get_object.assert_called_once_with(
                Bucket="test-bucket", Key="ResearchThis/findings.json"
            )

    def test_falls_back_to_local_on_invalid_s3_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            fallback = _write_json(tmp, "fallback.json", VALID_CONTRACT)
            mock_client = self._mock_s3_client(
                {"records": []}
            )  # missing contract_version
            with mock.patch("boto3.client", return_value=mock_client):
                contract, source_last_modified = fetch_findings_contract(fallback)
            self.assertIsNone(source_last_modified)
            self.assertEqual(contract["contract_version"], "1.0")

    def test_falls_back_to_local_on_s3_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            fallback = _write_json(tmp, "fallback.json", VALID_CONTRACT)
            mock_client = mock.MagicMock()
            mock_client.get_object.side_effect = RuntimeError("network down")
            with mock.patch("boto3.client", return_value=mock_client):
                contract, source_last_modified = fetch_findings_contract(fallback)
            self.assertIsNone(source_last_modified)
            self.assertEqual(contract["contract_version"], "1.0")


if __name__ == "__main__":
    unittest.main()
