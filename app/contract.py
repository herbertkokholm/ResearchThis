"""The versioned JSON data contract between the research-monitoring agent
and this portal.

Whatever produces findings (an agent, a script, a person) uploads a JSON
payload matching this contract to S3 (see app/upload_findings.py); the
running portal fetches and validates it (app/s3sync.py).

The agent and the portal are independently-evolving systems talking over
this JSON, so the contract carries an explicit version: an unrecognized
shape fails loudly (clear error, fall back to the last known-good data)
instead of silently corrupting the gallery.

Contract v1.1 shape:
    {
      "contract_version": "1.1",
      "last_updated": "YYYY-MM-DD",
      "records": [
        {
          "date_found": "YYYY-MM-DD",
          "title": str,
          "authors": str,
          "ids": [{"kind": str, "label": str, "url": str}, ...],
          "tracks": [int, ...],
          "section": "relevant" | "related",
          "warn": bool,
          "linkedin": bool,
          "raw_id": str,
          "summary": str (optional)
        },
        ...
      ]
    }

`summary` is optional (added in 1.1, a minor/backward-compatible bump --
existing 1.0 producers keep working unchanged) and, when present, is a
short producer-written plain-text summary of the finding. It powers the
"Reels" vertical-scroll view (templates/gallery.html); a record without
one just renders without a summary there. Unlike every other field, this
is prose the producer chooses to write, not something derived from the
finding's metadata -- the portal never generates it itself (see
app/chat.py for why: without real summary text to ground it, an LLM
asked to fill this in would be guessing at what the paper found).

Deliberately NOT part of the contract: theme-track taxonomy (colors,
labels) and feed branding (heading/owner/subtitle) -- those are the
portal's own config (see data/example_tracks.json, data/example_feed.json
for the shape), not something the
upstream agent should need to know about. Per-record `stats`/counts are
also not carried over the wire; the portal recomputes them from `records`
so a stale or lying count from upstream can never leak into the UI.
"""

from __future__ import annotations

CONTRACT_VERSION = "1.1"
SUPPORTED_MAJOR_VERSIONS = {"1"}

REQUIRED_RECORD_FIELDS = {
    "date_found",
    "title",
    "authors",
    "ids",
    "tracks",
    "section",
    "warn",
    "linkedin",
    "raw_id",
}


class ContractError(ValueError):
    """Raised when a payload doesn't conform to the findings data contract."""


def validate_and_normalize(data: dict) -> dict:
    """Validates a parsed JSON contract payload; returns it unchanged if valid.

    Raises ContractError with a specific, actionable message otherwise.
    Checks structure and required fields only -- it does not re-derive or
    correct anything; a payload either conforms or it's rejected.
    """
    if not isinstance(data, dict):
        raise ContractError(
            f"contract payload must be a JSON object, got {type(data).__name__}"
        )

    version = data.get("contract_version")
    if not version:
        raise ContractError("missing required 'contract_version' field")
    major = str(version).split(".")[0]
    if major not in SUPPORTED_MAJOR_VERSIONS:
        raise ContractError(
            f"unsupported contract_version {version!r} "
            f"(this portal supports major version(s): {sorted(SUPPORTED_MAJOR_VERSIONS)})"
        )

    if "last_updated" not in data or not data["last_updated"]:
        raise ContractError("missing required 'last_updated' field")

    records = data.get("records")
    if not isinstance(records, list):
        raise ContractError("missing or invalid 'records' field (must be a JSON array)")

    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            raise ContractError(f"record {i} is not a JSON object")
        missing = REQUIRED_RECORD_FIELDS - set(rec.keys())
        if missing:
            raise ContractError(
                f"record {i} ({rec.get('title', '?')!r}) missing field(s): {sorted(missing)}"
            )
        if rec["section"] not in ("relevant", "related"):
            raise ContractError(
                f"record {i} has invalid section {rec['section']!r} (must be 'relevant' or 'related')"
            )
        if not isinstance(rec["tracks"], list):
            raise ContractError(f"record {i} 'tracks' must be a list")
        if not isinstance(rec["ids"], list):
            raise ContractError(f"record {i} 'ids' must be a list")
        if "summary" in rec and not isinstance(rec["summary"], str):
            raise ContractError(f"record {i} 'summary' must be a string")

    return data
