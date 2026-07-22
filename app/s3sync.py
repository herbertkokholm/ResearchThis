"""Fetches the portal's data from S3, with fallback to bundled local example files.

S3 holds three JSON objects for a deployment:
  - the findings data contract (see app/contract.py), keyed by S3_FINDINGS_KEY
  - the theme-track taxonomy (data/example_tracks.json shape), keyed by S3_TRACKS_KEY
  - the feed manifest / branding (data/example_feed.json shape), keyed by S3_FEED_KEY

This keeps deployment-specific content (a research feed's actual findings,
taxonomy, and branding) out of the repository entirely -- the files
bundled under data/ are illustrative examples, not what any given
deployment actually serves. Render's free tier also wipes local disk on
spin-down, so nothing written at runtime can be trusted to persist anyway.

For each of the three, if S3 isn't configured for it, is unreachable, or
returns something invalid, the corresponding local example file is served
instead -- the server never crashes for lack of S3 access or a bad upload.
"""

from __future__ import annotations

import json
import logging
import os

from app.contract import validate_and_normalize

logger = logging.getLogger("researchthis.s3sync")


def _s3_key(root_folder_env: str, key_env: str) -> str | None:
    bucket = os.environ.get("S3_BUCKET")
    object_key = os.environ.get(key_env)
    if not bucket or not object_key:
        return None
    root_folder = os.environ.get(root_folder_env, "").strip("/")
    return f"{root_folder}/{object_key}" if root_folder else object_key


def _get_s3_object(bucket: str, key: str, region: str | None) -> tuple[str, str]:
    import boto3

    client = boto3.client("s3", region_name=region)
    obj = client.get_object(Bucket=bucket, Key=key)
    body = obj["Body"].read().decode("utf-8")
    last_modified = obj["LastModified"].isoformat()
    return body, last_modified


def _read_local_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def fetch_findings_contract(local_fallback_path: str) -> tuple[dict, str | None]:
    """Returns (contract_dict, source_last_modified_iso8601_or_None).

    `source_last_modified` is the S3 object's LastModified, used for the
    freshness stamp (docs/SPEC.md §5.2/§9). It's None when serving the local
    fallback, since local file mtime isn't meaningful in the Render
    ephemeral-disk deployment model.

    Falls back to the local file for any reason the S3 payload can't be
    trusted — unreachable, malformed JSON, or a contract that fails
    validation (e.g. a version this portal doesn't understand yet, or a
    bad upload). Raises only if the local fallback is itself invalid — at
    that point there's genuinely nothing safe left to serve.
    """
    bucket = os.environ.get("S3_BUCKET")
    key = _s3_key("S3_ROOT_FOLDER", "S3_FINDINGS_KEY")

    if not bucket or not key:
        logger.info(
            "S3 not configured for findings (S3_BUCKET/S3_FINDINGS_KEY missing) — using local fallback %s",
            local_fallback_path,
        )
        return validate_and_normalize(_read_local_json(local_fallback_path)), None

    region = os.environ.get("AWS_REGION")
    try:
        body, last_modified = _get_s3_object(bucket, key, region)
        data = validate_and_normalize(json.loads(body))
        logger.info(
            "fetched s3://%s/%s (last_modified=%s, %d records)",
            bucket,
            key,
            last_modified,
            len(data["records"]),
        )
        return data, last_modified
    except Exception:
        logger.exception(
            "S3 fetch or contract validation failed for s3://%s/%s — falling back to local file %s",
            bucket,
            key,
            local_fallback_path,
        )
        return validate_and_normalize(_read_local_json(local_fallback_path)), None


def fetch_json_config(key_env: str, local_fallback_path: str) -> dict:
    """Generic S3-with-local-fallback fetch for portal config (tracks/feed
    manifest) that isn't part of the findings data contract and so isn't
    contract-versioned — just needs to be a JSON object.
    """
    bucket = os.environ.get("S3_BUCKET")
    key = _s3_key("S3_ROOT_FOLDER", key_env)

    if not bucket or not key:
        return _read_local_json(local_fallback_path)

    region = os.environ.get("AWS_REGION")
    try:
        body, _ = _get_s3_object(bucket, key, region)
        return json.loads(body)
    except Exception:
        logger.exception(
            "S3 fetch failed for s3://%s/%s — falling back to local file %s",
            bucket,
            key,
            local_fallback_path,
        )
        return _read_local_json(local_fallback_path)


def fetch_tracks(local_fallback_path: str) -> dict:
    """Fetches the theme-track taxonomy (S3_TRACKS_KEY), local fallback otherwise."""
    return fetch_json_config("S3_TRACKS_KEY", local_fallback_path)


def fetch_feed_manifest(local_fallback_path: str) -> dict:
    """Fetches the feed manifest / branding (S3_FEED_KEY), local fallback otherwise."""
    return fetch_json_config("S3_FEED_KEY", local_fallback_path)
