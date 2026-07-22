#!/usr/bin/env python3
"""Read-only Zotero reconciliation report (docs/SPEC.md §10.1, step 1).

Matches this feed's findings against a live Zotero library by DOI/arXiv ID
and reports:
  (a) findings already present in Zotero
  (b) findings missing from Zotero
  (c) DOI duplicates within Zotero

This script performs NO WRITES to Zotero — it only calls read endpoints.
That's deliberate, not a missing feature: any Zotero item whose *key*
changes (deleted, moved to another library, "clean library" reset) breaks
Word documents that cite it via the Zotero Word plugin's live field codes.
Tagging existing items in place and selectively adding missing ones are
separate, explicit follow-up steps (docs/SPEC.md §10.1 steps 2-3) — not implemented
here on purpose, so this can be run freely to see what *would* happen.

Requires:
    pip install pyzotero

Env vars (or .env):
    ZOTERO_LIBRARY_ID    numeric library ID (user or group)
    ZOTERO_LIBRARY_TYPE  "user" or "group" (default: user)
    ZOTERO_API_KEY       from Zotero -> Settings -> Security -> Applications

Usage:
    python3 -m app.build                       # ensure dist/findings.json exists
    python3 -m app.reconcile_zotero [--findings dist/findings.json] [--out report.json]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict

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


def normalize_doi(doi: str) -> str:
    doi = doi.strip().lower()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi)
    return doi.rstrip(".,;")


def _finding_doi(ident: dict) -> str:
    """Extracts the bare DOI for a finding's DOI-kind id.

    Some producer records append a citation/verification annotation to
    `label`, which breaks an exact match even though the DOI itself is correct.
    `url` is always a clean doi.org link, so it's preferred; `label` is
    only a fallback for the (so-far unseen) case where `url` isn't one.
    """
    url = ident.get("url") or ""
    if re.match(r"^https?://(dx\.)?doi\.org/", url, re.IGNORECASE):
        return normalize_doi(url)
    return normalize_doi(ident["label"])


def extract_zotero_identifiers(item: dict) -> dict:
    """Best-effort {'doi': ..., 'arxiv': ...} extraction from a Zotero item."""
    data = item.get("data", {})
    ids = {}
    doi = data.get("DOI") or ""
    if doi:
        ids["doi"] = normalize_doi(doi)
    # arXiv ids don't have a dedicated Zotero field; they turn up in
    # archiveID, extra, or url depending on how the item was imported.
    for field in ("archiveID", "extra", "url", "callNumber"):
        val = data.get(field) or ""
        m = re.search(r"arXiv:\s*(\d{4}\.\d{4,5})", val, re.IGNORECASE)
        if m:
            ids["arxiv"] = m.group(1)
            break
    return ids


def fetch_zotero_items(library_id: str, library_type: str, api_key: str) -> list:
    from pyzotero import zotero

    zot = zotero.Zotero(library_id, library_type, api_key)
    return zot.everything(zot.items())


# Child items that never carry their own bibliographic metadata -- a plain
# library view (title/creators/date/DOI) is meaningless for these, so they're
# left out of library_cards().
_NON_BIBLIOGRAPHIC_ITEM_TYPES = {"attachment", "note", "annotation"}


def _creator_name(creator: dict) -> str:
    if creator.get("name"):
        return creator["name"]
    return " ".join(p for p in (creator.get("firstName"), creator.get("lastName")) if p)


def format_creators(creators: list) -> str:
    """Renders a Zotero item's creators list as a single display string."""
    return ", ".join(n for n in (_creator_name(c) for c in creators or []) if n)


def zotero_item_to_card(item: dict) -> dict | None:
    """Reduces one raw Zotero item to the fields the library tab card needs.

    Returns None for non-bibliographic child items (attachments, notes).
    """
    data = item.get("data", {})
    item_type = data.get("itemType", "")
    if item_type in _NON_BIBLIOGRAPHIC_ITEM_TYPES:
        return None
    doi = (data.get("DOI") or "").strip()
    return {
        "key": data.get("key") or item.get("key"),
        "title": data.get("title") or "",
        "creators": format_creators(data.get("creators")),
        "date": data.get("date", ""),
        "item_type": item_type,
        "doi": doi or None,
        "missing_doi": not bool(doi),
    }


def library_cards(zotero_items: list) -> list:
    """A plain card view of the library itself, independent of `findings`."""
    return [
        card
        for card in (zotero_item_to_card(item) for item in zotero_items)
        if card is not None
    ]


def build_report(findings: list, zotero_items: list) -> dict:
    by_doi = defaultdict(list)
    by_arxiv = defaultdict(list)
    for item in zotero_items:
        ids = extract_zotero_identifiers(item)
        key = item.get("data", {}).get("key") or item.get("key")
        title = item.get("data", {}).get("title", "")
        if "doi" in ids:
            by_doi[ids["doi"]].append({"key": key, "title": title})
        if "arxiv" in ids:
            by_arxiv[ids["arxiv"]].append({"key": key, "title": title})

    doi_duplicates = {
        doi: entries for doi, entries in by_doi.items() if len(entries) > 1
    }

    already_in_zotero = []
    missing = []
    matched_zotero_keys = set()
    for rec in findings:
        matches = []
        for ident in rec.get("ids", []):
            kind = ident["kind"].lower()
            if kind == "doi":
                matches.extend(by_doi.get(_finding_doi(ident), []))
            elif kind == "arxiv":
                arxiv_id = ident["label"].split(":", 1)[-1]
                matches.extend(by_arxiv.get(arxiv_id, []))

        seen_keys = set()
        uniq_matches = []
        for m in matches:
            if m["key"] not in seen_keys:
                seen_keys.add(m["key"])
                uniq_matches.append(m)

        if uniq_matches:
            already_in_zotero.append(
                {
                    "title": rec["title"],
                    "raw_id": rec["raw_id"],
                    "zotero_matches": uniq_matches,
                }
            )
            matched_zotero_keys.update(m["key"] for m in uniq_matches)
        else:
            missing.append(
                {
                    "title": rec["title"],
                    "raw_id": rec["raw_id"],
                    "ids": rec.get("ids", []),
                }
            )

    library = library_cards(zotero_items)
    for card in library:
        card["in_feed"] = card["key"] in matched_zotero_keys

    return {
        "summary": {
            "total_findings": len(findings),
            "already_in_zotero": len(already_in_zotero),
            "missing_from_zotero": len(missing),
            "doi_duplicates_in_zotero": len(doi_duplicates),
            "library_total": len(library),
            "library_missing_doi": sum(1 for c in library if c["missing_doi"]),
            "library_in_feed": sum(1 for c in library if c["in_feed"]),
        },
        "already_in_zotero": already_in_zotero,
        "missing_from_zotero": missing,
        "doi_duplicates_in_zotero": doi_duplicates,
        "library": library,
    }


def main(argv: list | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    _load_dotenv(os.path.join(ROOT, ".env"))

    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--findings",
        default=os.path.join(ROOT, "dist", "findings.json"),
        help="Path to dist/findings.json (run `python3 -m app.build` first if missing)",
    )
    ap.add_argument(
        "--out", default=None, help="Optional path to write the full JSON report to"
    )
    args = ap.parse_args(argv)

    library_id = os.environ.get("ZOTERO_LIBRARY_ID")
    library_type = os.environ.get("ZOTERO_LIBRARY_TYPE", "user")
    # ZOTERO_APP_KEY is accepted as an alias -- that's the name shown on
    # Zotero's "Applications" settings page for a newly created key.
    api_key = os.environ.get("ZOTERO_API_KEY") or os.environ.get("ZOTERO_APP_KEY")
    if not library_id or not api_key:
        print(
            "error: ZOTERO_LIBRARY_ID and ZOTERO_API_KEY (or ZOTERO_APP_KEY) must be set (env or .env)",
            file=sys.stderr,
        )
        print(
            "       get an API key at Zotero -> Settings -> Security -> Applications",
            file=sys.stderr,
        )
        return 1

    if not os.path.exists(args.findings):
        print(
            f"error: {args.findings} not found -- run `python3 -m app.build` first",
            file=sys.stderr,
        )
        return 1
    with open(args.findings, encoding="utf-8") as f:
        feed = json.load(f)

    try:
        import pyzotero  # noqa: F401
    except ImportError:
        print(
            "error: pyzotero not installed -- run `pip install pyzotero`",
            file=sys.stderr,
        )
        return 1

    print(
        f"Fetching items from Zotero library {library_id} ({library_type})... READ-ONLY, no writes.",
        file=sys.stderr,
    )
    zotero_items = fetch_zotero_items(library_id, library_type, api_key)
    print(f"Fetched {len(zotero_items)} Zotero items.", file=sys.stderr)

    report = build_report(feed["records"], zotero_items)

    print(json.dumps(report["summary"], indent=2, ensure_ascii=False))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"Full report written to {args.out}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
