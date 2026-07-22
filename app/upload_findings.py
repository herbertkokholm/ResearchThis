#!/usr/bin/env python3
"""Pushes a findings JSON contract to S3 so the portal picks it up.

This is the "refresh" workflow (docs/SPEC.md §9): whatever produces new findings
hands this a `.json` contract payload (app/contract.py); it's validated
and uploaded to S3. The running portal re-fetches it (on next `/refresh`
call or at its TTL) with no redeploy needed. The freshness stamp shown in
the UI is the S3 object's LastModified, so a successful upload is
immediately visible.

Usage:
    python3 -m app.upload_findings path/to/findings.json

There is no default input path on purpose: data/example_findings.json in
this repo is illustrative sample content, not any particular deployment's
real data, so accidentally uploading it over real data in S3 shouldn't be
one missing argument away.

Reads S3_BUCKET, S3_ROOT_FOLDER, S3_FINDINGS_KEY, AWS_REGION from the
environment (or a local .env file, loaded the same way server.py does).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from app.contract import ContractError, validate_and_normalize

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_dotenv(path: str) -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if key and key not in os.environ:
                os.environ[key] = value


def upload_contract_to_s3(contract: dict) -> dict:
    """Uploads an already-validated findings contract to S3.

    Shared by the CLI (`python3 -m app.upload_findings`) and the browser
    /upload endpoint (app/upload_endpoint.py) so both paths write the
    contract to the exact same S3_ROOT_FOLDER/S3_FINDINGS_KEY the running
    portal reads from (app/s3sync.py) -- an upload can't accidentally land
    under some other name and be silently ignored.

    Returns {"bucket", "key", "last_modified" (iso8601), "size" (bytes),
    "records" (count)}. Raises RuntimeError if S3_BUCKET/S3_FINDINGS_KEY
    aren't configured.
    """
    payload = json.dumps(contract, ensure_ascii=False, indent=2).encode("utf-8")

    bucket = os.environ.get("S3_BUCKET")
    findings_key = os.environ.get("S3_FINDINGS_KEY")
    if not bucket or not findings_key:
        raise RuntimeError("S3_BUCKET and S3_FINDINGS_KEY must be set (env or .env)")

    root_folder = os.environ.get("S3_ROOT_FOLDER", "").strip("/")
    key = f"{root_folder}/{findings_key}" if root_folder else findings_key
    region = os.environ.get("AWS_REGION")

    import boto3

    client = boto3.client("s3", region_name=region)
    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=payload,
        ContentType="application/json; charset=utf-8",
    )

    head = client.head_object(Bucket=bucket, Key=key)
    return {
        "bucket": bucket,
        "key": key,
        "last_modified": head["LastModified"].isoformat(),
        "size": head["ContentLength"],
        "records": len(contract.get("records", [])),
    }


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    _load_dotenv(os.path.join(ROOT, ".env"))

    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("input_path", help="path to a findings contract .json file")
    args = ap.parse_args(argv)

    if not os.path.exists(args.input_path):
        print(f"error: {args.input_path} does not exist", file=sys.stderr)
        return 1

    with open(args.input_path, encoding="utf-8") as f:
        raw = json.load(f)
    try:
        contract = validate_and_normalize(raw)
    except ContractError as e:
        print(
            f"error: {args.input_path} failed contract validation: {e}", file=sys.stderr
        )
        return 1
    try:
        result = upload_contract_to_s3(contract)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(f"Uploaded {args.input_path} -> s3://{result['bucket']}/{result['key']}")
    print(f"S3 LastModified: {result['last_modified']} ({result['size']} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
