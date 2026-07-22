"""Tests for app.upload_endpoint.process_upload (no live network/S3 calls)."""

from __future__ import annotations

import json
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.upload_endpoint import (
    MAX_UPLOAD_BYTES,
    UploadError,
    process_upload,
)

VALID_CONTRACT = {
    "contract_version": "1.0",
    "last_updated": "2026-07-22",
    "records": [
        {
            "date_found": "2026-07-22",
            "title": "A paper",
            "authors": "Someone",
            "ids": [],
            "tracks": [],
            "section": "relevant",
            "warn": False,
            "linkedin": False,
            "raw_id": "abc123",
        }
    ],
}


def _mock_s3_client(last_modified="2026-07-22T10:00:00+00:00", size=123):
    client = mock.MagicMock()
    client.head_object.return_value = {
        "LastModified": mock.MagicMock(isoformat=lambda: last_modified),
        "ContentLength": size,
    }
    return client


class ProcessUploadDragDropTests(unittest.TestCase):
    def setUp(self):
        self._saved = dict(os.environ)
        os.environ["S3_BUCKET"] = "test-bucket"
        os.environ["S3_FINDINGS_KEY"] = "findings.json"
        os.environ["S3_ROOT_FOLDER"] = "ResearchThis/"
        os.environ["AWS_REGION"] = "eu-north-1"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)

    def test_valid_contract_is_uploaded_to_configured_key(self):
        body = json.dumps(VALID_CONTRACT).encode("utf-8")
        mock_client = _mock_s3_client()
        with mock.patch("boto3.client", return_value=mock_client):
            result = process_upload(body, None)

        mock_client.put_object.assert_called_once()
        _, kwargs = mock_client.put_object.call_args
        self.assertEqual(kwargs["Bucket"], "test-bucket")
        self.assertEqual(kwargs["Key"], "ResearchThis/findings.json")
        uploaded = json.loads(kwargs["Body"])
        self.assertEqual(uploaded["contract_version"], "1.0")

        self.assertEqual(result["bucket"], "test-bucket")
        self.assertEqual(result["key"], "ResearchThis/findings.json")
        self.assertEqual(result["records"], 1)

    def test_invalid_json_is_rejected_before_touching_s3(self):
        with (
            mock.patch("boto3.client") as mock_boto,
            self.assertRaises(UploadError) as ctx,
        ):
            process_upload(b"{not json", None)
        self.assertEqual(ctx.exception.status, 400)
        mock_boto.assert_not_called()

    def test_contract_missing_fields_is_rejected_before_touching_s3(self):
        bad = {"records": []}  # missing contract_version, last_updated
        with (
            mock.patch("boto3.client") as mock_boto,
            self.assertRaises(UploadError) as ctx,
        ):
            process_upload(json.dumps(bad).encode("utf-8"), None)
        self.assertEqual(ctx.exception.status, 422)
        mock_boto.assert_not_called()

    def test_empty_body_is_rejected(self):
        with self.assertRaises(UploadError) as ctx:
            process_upload(b"", None)
        self.assertEqual(ctx.exception.status, 400)

    def test_oversized_body_is_rejected_before_touching_s3(self):
        oversized = b"x" * (MAX_UPLOAD_BYTES + 1)
        with (
            mock.patch("boto3.client") as mock_boto,
            self.assertRaises(UploadError) as ctx,
        ):
            process_upload(oversized, None)
        self.assertEqual(ctx.exception.status, 400)
        mock_boto.assert_not_called()

    def test_s3_not_configured_raises_503(self):
        os.environ.pop("S3_BUCKET", None)
        body = json.dumps(VALID_CONTRACT).encode("utf-8")
        with self.assertRaises(UploadError) as ctx:
            process_upload(body, None)
        self.assertEqual(ctx.exception.status, 503)


class ProcessUploadUrlFetchTests(unittest.TestCase):
    def setUp(self):
        self._saved = dict(os.environ)
        os.environ["S3_BUCKET"] = "test-bucket"
        os.environ["S3_FINDINGS_KEY"] = "findings.json"
        os.environ.pop("S3_ROOT_FOLDER", None)
        os.environ["AWS_REGION"] = "eu-north-1"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)

    def test_fetches_url_then_validates_and_uploads(self):
        body = json.dumps(VALID_CONTRACT).encode("utf-8")
        fake_resp = mock.MagicMock()
        fake_resp.read.return_value = body
        fake_resp.__enter__.return_value = fake_resp
        fake_resp.__exit__.return_value = False
        mock_client = _mock_s3_client()
        with (
            mock.patch("urllib.request.urlopen", return_value=fake_resp),
            mock.patch("boto3.client", return_value=mock_client),
        ):
            result = process_upload(None, "https://example.com/findings.json")
        self.assertEqual(result["key"], "findings.json")
        mock_client.put_object.assert_called_once()

    def test_disallowed_url_scheme_is_rejected(self):
        with self.assertRaises(UploadError) as ctx:
            process_upload(None, "file:///etc/passwd")
        self.assertEqual(ctx.exception.status, 400)

    def test_fetch_failure_is_reported_as_400(self):
        with (
            mock.patch("urllib.request.urlopen", side_effect=OSError("boom")),
            self.assertRaises(UploadError) as ctx,
        ):
            process_upload(None, "https://example.com/findings.json")
        self.assertEqual(ctx.exception.status, 400)


if __name__ == "__main__":
    unittest.main()
