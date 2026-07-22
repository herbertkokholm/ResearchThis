#!/usr/bin/env python3
"""CLI: assemble a static gallery from a findings JSON contract + taxonomy + manifest.

Runnable without any S3/server dependency. Defaults operate on the bundled
data/example_* files — a small illustrative dataset, not any particular
deployment's real content (that lives in S3; see README).

Usage:
    python3 -m app.build [--contract PATH] [--tracks PATH] [--feed PATH]
                          [--out-json PATH] [--out-html PATH]
"""

from __future__ import annotations

import argparse
import json
import logging
import os

from app.contract import validate_and_normalize
from app.feed import build_feed
from app.render import render_gallery_html

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(argv=None):
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--contract", default=os.path.join(ROOT, "data", "example_findings.json")
    )
    ap.add_argument(
        "--tracks", default=os.path.join(ROOT, "data", "example_tracks.json")
    )
    ap.add_argument("--feed", default=os.path.join(ROOT, "data", "example_feed.json"))
    ap.add_argument("--out-json", default=os.path.join(ROOT, "dist", "findings.json"))
    ap.add_argument("--out-html", default=os.path.join(ROOT, "dist", "index.html"))
    args = ap.parse_args(argv)

    with open(args.tracks, encoding="utf-8") as f:
        tracks = json.load(f)
    with open(args.feed, encoding="utf-8") as f:
        manifest = json.load(f)
    with open(args.contract, encoding="utf-8") as f:
        contract = validate_and_normalize(json.load(f))

    feed = build_feed(contract, tracks, manifest)

    out_json_dir = os.path.dirname(args.out_json)
    if out_json_dir:
        os.makedirs(out_json_dir, exist_ok=True)
    with open(args.out_json, "w", encoding="utf-8") as f:
        json.dump(feed, f, ensure_ascii=False, indent=2)

    os.makedirs(os.path.dirname(args.out_html), exist_ok=True)
    html = render_gallery_html(feed)
    with open(args.out_html, "w", encoding="utf-8") as f:
        f.write(html)

    print(
        f"Wrote {args.out_json} ({feed['counts']['total']} records: "
        f"{feed['counts']['relevant']} relevant / {feed['counts']['related']} related, "
        f"{feed['counts']['warn']} warn, {feed['counts']['linkedin']} linkedin)"
    )
    print(f"Wrote {args.out_html}")


if __name__ == "__main__":
    main()
