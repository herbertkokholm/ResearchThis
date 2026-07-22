"""Assembles a full feed dict (docs/SPEC.md §7) from a validated JSON contract + taxonomy + manifest."""

from __future__ import annotations


def compute_stats(records: list) -> dict:
    """Tallies section/warn/linkedin/track counts for a list of records.

    Counts are always recomputed from records here rather than trusted
    from upstream, so a stale/wrong count can never leak into the UI.
    """
    stats = {
        "total": len(records),
        "relevant": sum(1 for r in records if r["section"] == "relevant"),
        "related": sum(1 for r in records if r["section"] == "related"),
        "warn": sum(1 for r in records if r["warn"]),
        "linkedin": sum(1 for r in records if r["linkedin"]),
        "by_track": {},
    }
    for r in records:
        for t in r["tracks"]:
            stats["by_track"][str(t)] = stats["by_track"].get(str(t), 0) + 1
    return stats


def build_feed(
    contract: dict,
    tracks: dict,
    manifest: dict,
    source_last_modified: str | None = None,
) -> dict:
    """Build the feed-level structure described in docs/SPEC.md §7.

    `contract` is a validated data-contract payload (see app/contract.py) —
    i.e. `{"contract_version", "last_updated", "records"}`.

    `manifest` is the lightweight feed metadata (see data/example_feed.json)
    (feed_id, heading, subtitle, owner, language, chat_enabled,
    reels_enabled) — the MVP precursor to the full multi-feed manifest
    described in docs/SPEC.md §3.4. `chat_enabled`/`reels_enabled` each
    default to False so existing feed manifests don't suddenly surface the
    chat tab (app/chat.py) or the Reels tab (templates/gallery.html)
    without an explicit opt-in.
    """
    records = contract["records"]
    counts = compute_stats(records)
    return {
        "feed_id": manifest.get("feed_id"),
        "title": manifest.get("subtitle") or manifest.get("heading"),
        "heading": manifest.get("heading"),
        "subtitle": manifest.get("subtitle"),
        "owner": manifest.get("owner"),
        "language": manifest.get("language", "en"),
        "flag": manifest.get("flag", ""),
        "chat_enabled": bool(manifest.get("chat_enabled", False)),
        "reels_enabled": bool(manifest.get("reels_enabled", False)),
        "last_updated": contract["last_updated"],
        "tracks": tracks,
        "records": records,
        "counts": counts,
        "source_last_modified": source_last_modified,
    }
