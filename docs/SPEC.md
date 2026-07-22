# ResearchThis Portal — Spec

## §1 Overview

ResearchThis Portal turns a curated literature-surveillance feed into a
filterable, shareable card gallery. A producer — an agent, a script, or a
person — uploads a versioned JSON contract of findings to S3. The portal
fetches that contract, combines it with a theme taxonomy and feed
branding, and serves it as a gallery page and a small JSON API. Producer
and portal are two independently-evolving systems that agree only on the
wire contract (§3); neither needs to know the other's internals.

## §2 Scope

### §2.1 Current (MVP)

- A single feed: one set of findings, one taxonomy, one branding config.
- Server-rendered gallery with client-side filtering, plus a JSON API for
  the same data (§6, §7).
- A read-only Zotero reconciliation report (§10) — no write path.

### §2.2 Non-goals (deferred to V2)

- Multi-feed serving/aggregation (§3.4).
- Full SEO-oriented SSR beyond the MVP's server-side data injection.
- RSS output.
- Zotero write automation (tagging, adding items) — see §10.1 steps 2–3.

## §3 Data contract

The data contract is the sole interface between a producer and the
portal: a versioned JSON payload uploaded to S3 (§9) and fetched by the
running portal (§5).

### §3.1 Versioning

Every payload carries `contract_version`. The portal tracks the set of
major versions it understands; an unsupported major version, or any
malformed payload, is rejected outright with a specific error — the
producer's upload is refused before it reaches S3 (§9), and if a bad
payload somehow reaches S3 anyway, the portal falls back to the last
known-good data on fetch rather than serving a broken gallery. A breaking
shape change requires a version bump, not a silent field change.

### §3.2 Shape

```
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
```

`last_updated` is set by the producer and reflects when the findings
themselves were last generated — distinct from the S3 object's own
modification time (§5.2). `tracks` on a record is a list of taxonomy IDs
(§4); `section` splits records into two audiences ("relevant" vs.
"related"); `ids` carries the record's external identifiers (DOI, arXiv,
etc.), used for Zotero matching (§10). `summary` (added in 1.1, optional —
a 1.0 producer that never sends it stays valid) is a short producer-
written plain-text summary of the finding; it's the only field that's
prose rather than derived metadata, and it powers the Reels view
(templates/gallery.html), gated behind the feed manifest's
`reels_enabled` flag (§4).

### §3.3 Exclusions

Theme taxonomy and feed branding are deliberately **not** part of the
contract (§4) — a producer of findings shouldn't need to know the
portal's color scheme or page heading. Per-record counts/stats are also
never carried over the wire: the portal always recomputes them from
`records`, so a stale or incorrect count from upstream can never leak into
the UI.

### §3.4 Future: multi-feed manifest

The MVP's feed manifest (§4) — a flat `feed_id`/`heading`/`subtitle`/
`owner`/`language` object — is the precursor to a planned multi-feed
manifest that lets the portal serve or aggregate feeds from third
parties, not just a single curated source. Until this ships, all findings
data is treated as human-curated, trusted input; §5.4 establishes the
defensive posture in anticipation of that changing.

## §4 Theme taxonomy and feed branding

Two small, portal-owned config objects, each independently optional and
independently fetched (§5.1):

- **Theme taxonomy**: track ID → full label, short label, color. Never
  hardcoded in the UI.
- **Feed branding**: page heading, subtitle, owner, language, flag —
  the display identity of the feed — plus two opt-in feature flags,
  `chat_enabled` and `reels_enabled`, both defaulting to false so an
  existing manifest doesn't suddenly surface a new tab.

Both are plain JSON objects, not contract-versioned like findings (§3.1)
— they're portal configuration, not a producer/portal interface.

## §5 Serving and freshness

### §5.1 Sources and fallback

The portal holds three JSON objects per deployment: the findings contract
(§3), the theme taxonomy, and the feed manifest (§4), each keyed by its
own S3 object key. For each of the three, independently: if S3 isn't
configured for it, is unreachable, or returns something invalid, the
portal serves a bundled local example instead. The portal never fails to
serve because of an S3 outage or a bad upload — it degrades to the last
trustworthy data it has.

### §5.2 Freshness stamp

The portal exposes two distinct timestamps: `last_updated`, the
producer-set field from the contract (§3.2), and `source_last_modified`,
the S3 object's actual last-write time. The former answers "how current
is this data, according to whoever generated it"; the latter answers "when
did this portal last actually receive new data." `source_last_modified`
is absent when serving a local fallback file, since local file time
carries no meaning once the underlying disk is ephemeral.

### §5.3 Caching

The portal re-fetches each of the three S3 objects on a time-to-live, not
on every request, to keep steady-state load on S3 low. An explicit refresh
action bypasses the TTL and re-fetches immediately (§9).

### §5.4 Handling of untrusted content

All record text is escaped before being placed into the page — both in
HTML markup and in JSON embedded inside `<script>` blocks. This guarantee
holds even though the MVP's single curated source doesn't strictly require
it: it is deliberate groundwork for §3.4, where record content will no
longer come from one trusted producer.

## §6 Server

### §6.1 Architecture

The server is deliberately framework-free — a threaded stdlib HTTP server,
no web framework — favoring a small, auditable dependency surface over
framework convenience. It holds an in-memory, TTL-refreshed copy of the
assembled feed (§7) and serves every request from that copy rather than
re-fetching per request.

### §6.2 Routes

| Route | Purpose |
|---|---|
| `GET /` | Rendered gallery (server-side data injection) |
| `GET /api/v1/findings` | JSON: filtered records (`section`, `track`, `q`, `warn`, `linkedin`) |
| `GET /api/v1/meta` | JSON: counts, freshness (§5.2), taxonomy |
| `GET /refresh` | Forces an immediate re-fetch of findings/taxonomy/branding (§5.3) |
| `GET /api/v1/zotero` | JSON: cached read-only Zotero reconciliation report (§10) |
| `GET /zotero/refresh` | Forces an immediate Zotero re-fetch |
| `GET /upload` | Drag-and-drop / fetch-by-URL upload page |
| `POST /upload` | Body = findings JSON (drag-and-drop), or `?url=` with no body (server fetches it) — validated against the contract (§3) and written to S3, same as step 1 of §9 |
| `GET /healthz` | Liveness check; always reachable, never behind an access gate |

An optional access gate may sit in front of every route except
`/healthz`, so uptime monitoring never depends on a shared secret.

## §7 Feed schema

The feed is the single structure the server renders and serves, assembled
from a validated data contract (§3) plus the theme taxonomy and feed
manifest (§4):

```
{
  "feed_id": str | None,
  "title": str | None,          # subtitle, falling back to heading
  "heading": str | None,
  "subtitle": str | None,
  "owner": str | None,
  "language": str,              # default "en"
  "flag": str,                  # default ""
  "last_updated": str,          # from the contract, see §5.2
  "tracks": dict,                # theme taxonomy, keyed by track id
  "records": list,               # contract records, verbatim
  "counts": dict,                # recomputed, see §3.3
  "source_last_modified": str | None,  # see §5.2
}
```

This is the shape a static build snapshot also carries, for offline
preview or as an input to Zotero reconciliation (§10).

## §8 Deployment

The portal is a single stateless process: it holds no durable local
state, so it can run on infrastructure that wipes local disk between
restarts. All persistent state lives in S3 (§5.1); credentials are
supplied via environment variables, never committed. A liveness route
(§6.2) exists independent of any access gate so uptime checks are never
credentialed.

## §9 Refresh workflow

Getting new data live requires no redeploy:

1. A producer validates a findings contract (§3) and uploads it to the
   findings S3 key. Validation happens before upload — a payload that
   fails contract checks never reaches S3, so a bad upload can't take
   down the live gallery. There is deliberately no default input path for
   this step, so a producer is never one missing argument away from
   overwriting real data with example data. This can happen from the CLI
   (`python3 -m app.upload_findings`) or from a browser via `POST /upload`
   (§6.2) — a user dragging in a file or pointing at a URL instead of a
   script; both paths share the same validate-then-write logic and land at
   the same S3 key.
2. Taxonomy/branding config (§4) is pushed to S3 the same way, without
   contract validation (§3.1 doesn't apply — it isn't versioned).
3. The running portal picks up the change within its TTL (§5.3), or
   immediately via the refresh route (§6.2).
4. If an invalid payload somehow reaches S3 despite step 1, the portal
   detects that on its next fetch and falls back to the last known-good
   data (§5.1) instead of breaking.

The freshness stamp (§5.2) makes a successful refresh visible immediately,
without a redeploy.

## §10 Zotero integration

The portal's Zotero integration **never writes to Zotero** — it only
calls read endpoints. This is a hard constraint, not a missing feature:
any Zotero item whose *key* changes (deleted, moved to another library, a
"clean library" reset) breaks Word documents that cite it via the Zotero
Word plugin's live field codes, since those citations reference items by
key. A write path that could ever change an existing item's key is out of
scope, permanently, unless that guarantee is revisited explicitly.

### §10.1 Reconciliation steps

1. **Read-only reconciliation report** (built): matches feed findings
   against a live Zotero library by DOI/arXiv ID, and reports what's
   already present, what's missing, and any DOI duplicates within the
   library. Exposed via the API, a library-card view in the gallery, and
   a CLI report. Safe to run at any time, on any schedule, with zero write
   risk.
2. **Tagging existing items in place** (not built): a future step that
   would tag Zotero items already matched to findings, without ever
   touching an item's key.
3. **Selectively adding missing items** (not built): a future step that
   would let a user choose which "missing from Zotero" findings to add,
   rather than adding all of them automatically.

Steps 2–3 are deliberately separate from step 1 and from each other, so
step 1 can be run freely to see what *would* happen before any write
capability exists at all.

### §10.2 Configuration

Zotero integration is entirely optional: without its credentials
configured, the portal simply reports itself as unconfigured wherever the
integration would otherwise appear, and the rest of the portal is
unaffected.
