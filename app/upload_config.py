#!/usr/bin/env python3
"""Pushes a deployment's theme-track taxonomy or feed manifest to S3.

Keeps a deployment's actual taxonomy/branding out of the repository —
data/example_tracks.json and data/example_feed.json are illustrative
sample content, served locally only when the corresponding S3 key isn't
configured or isn't reachable (see app/s3sync.py).

Usage:
    python3 -m app.upload_config tracks path/to/tracks.json
    python3 -m app.upload_config feed path/to/feed.json

Reads S3_BUCKET, S3_ROOT_FOLDER, S3_TRACKS_KEY/S3_FEED_KEY, AWS_REGION
from the environment (or a local .env file, loaded the same way
server.py does).
"""

from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

KIND_TO_KEY_ENV = {
    "tracks": "S3_TRACKS_KEY",
    "feed": "S3_FEED_KEY",
}


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


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    _load_dotenv(os.path.join(ROOT, ".env"))

    if len(argv) != 2 or argv[0] not in KIND_TO_KEY_ENV:
        print(
            f"usage: python3 -m app.upload_config {{{'|'.join(KIND_TO_KEY_ENV)}}} <path/to/file.json>",
            file=sys.stderr,
        )
        return 1

    kind, path = argv
    if not os.path.exists(path):
        print(f"error: {path} does not exist", file=sys.stderr)
        return 1

    with open(path, encoding="utf-8") as f:
        data = json.load(
            f
        )  # just needs to be valid JSON -- this config isn't contract-versioned
    payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")

    bucket = os.environ.get("S3_BUCKET")
    key_env = KIND_TO_KEY_ENV[kind]
    object_key = os.environ.get(key_env)
    if not bucket or not object_key:
        print(
            f"error: S3_BUCKET and {key_env} must be set (env or .env)", file=sys.stderr
        )
        return 1

    root_folder = os.environ.get("S3_ROOT_FOLDER", "").strip("/")
    key = f"{root_folder}/{object_key}" if root_folder else object_key
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
    print(f"Uploaded {path} -> s3://{bucket}/{key}")
    print(
        f"S3 LastModified: {head['LastModified'].isoformat()} ({head['ContentLength']} bytes)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
