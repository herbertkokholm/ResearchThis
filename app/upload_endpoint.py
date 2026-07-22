"""Server-side logic for POST /upload.

Same producer-uploads-a-contract workflow as `python3 -m app.upload_findings`
(docs/SPEC.md §9), but from a browser instead of the CLI, and with two ways
to supply the payload:

  - drag-and-drop: the browser reads the dropped file and POSTs its raw
    bytes as the request body.
  - fetch-by-URL: POST /upload?url=<encoded-url> with no body; the server
    fetches that URL itself.

Either way the payload is validated against the findings contract
(app/contract.py) before it ever reaches S3 -- a bad upload is refused, not
silently written over good data (same guarantee upload_findings.py gives
the CLI path). On success it's written to the exact S3_ROOT_FOLDER/
S3_FINDINGS_KEY the running portal reads from (app/upload_findings.py's
upload_contract_to_s3), regardless of what the source file/URL was named.
"""

from __future__ import annotations

import json
import urllib.request

from app.contract import ContractError, validate_and_normalize
from app.upload_findings import upload_contract_to_s3

MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # findings contracts are small JSON
FETCH_TIMEOUT_SECONDS = 10


class UploadError(Exception):
    """Carries the HTTP status the server.py handler should respond with."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _fetch_url(url: str) -> bytes:
    if not url.startswith(("http://", "https://")):
        raise UploadError(400, "url must be http:// or https://")
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "ResearchThisPortal-upload/1.0"}
        )
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_SECONDS) as resp:
            body = resp.read(MAX_UPLOAD_BYTES + 1)
    except Exception as e:
        raise UploadError(400, f"failed to fetch url: {e}") from e
    if len(body) > MAX_UPLOAD_BYTES:
        raise UploadError(400, f"fetched file exceeds {MAX_UPLOAD_BYTES} byte limit")
    return body


def process_upload(body: bytes | None, url: str | None) -> dict:
    """Validates + uploads a findings contract from a raw body or a URL.

    Returns the upload_contract_to_s3() result dict on success. Raises
    UploadError(status, message) for any failure -- missing/oversized
    input, invalid JSON, failed contract validation, or S3 not configured
    / unreachable -- so the caller can turn it straight into an HTTP
    response.
    """
    if url:
        body = _fetch_url(url)

    if not body or not body.strip():
        raise UploadError(400, "no JSON payload provided -- drag a file or pass ?url=")
    if len(body) > MAX_UPLOAD_BYTES:
        raise UploadError(400, f"upload exceeds {MAX_UPLOAD_BYTES} byte limit")

    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise UploadError(400, f"invalid JSON: {e}") from e

    try:
        contract = validate_and_normalize(data)
    except ContractError as e:
        raise UploadError(422, f"failed contract validation: {e}") from e

    try:
        return upload_contract_to_s3(contract)
    except RuntimeError as e:
        raise UploadError(503, str(e)) from e
    except UploadError:
        raise
    except Exception as e:
        raise UploadError(502, f"S3 upload failed: {e}") from e
