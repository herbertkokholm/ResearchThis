# ResearchThis Portal

Idea: Turns a curated literature-surveillance feed into a filterable, shareable
card gallery. Whatever produces findings uploads a versioned JSON contract
to S3; the running portal fetches and renders it. See [`docs/SPEC.md`](docs/SPEC.md)
for background — this README covers running, deploying, and refreshing it.

A public landing page lives at
[herbertkokholm.github.io/ResearchThis](https://herbertkokholm.github.io/ResearchThis/)
(source: [`docs/index.html`](docs/index.html), served via GitHub Pages).

Current status: **MVP**. Multi-feed, SEO/SSR, RSS, and Zotero
write-automation are not built (V2). A read-only Zotero reconciliation
*report* is included — see [`app/reconcile_zotero.py`](app/reconcile_zotero.py).

All Python source lives under `app/` (a single package — run everything as
`python3 -m app.<module>` from the repo root). Only config/deploy files
(`render.yaml`, `requirements.txt`, `.env`, `README.md`) and non-code
directories (`data/`, `templates/`, `tests/`, `dist/`) sit at repo root.

**The `data/example_*` files are a small illustrative dataset, not real
content.** A deployment's actual findings, theme taxonomy, and feed
branding live in S3, keyed by `S3_FINDINGS_KEY`/`S3_TRACKS_KEY`/`S3_FEED_KEY`
— never in the repo. This keeps the repository safe to make public: cloning
it gets you the code and a working example, not anyone's actual research
data.

## How it works

```
S3 (source of truth)
  ├─ findings.json  (the data contract — app/contract.py)
  ├─ tracks.json    (theme taxonomy)
  └─ feed.json      (branding / manifest)
        │
        ▼
running portal re-fetches each (TTL, or GET /refresh)
        │
        ▼
gallery + freshness stamp update — no redeploy needed
```

- **Data contract (the wire format)**: a versioned JSON payload —
  `{"contract_version", "last_updated", "records"}` — see
  [`app/contract.py`](app/contract.py) for the full schema and
  `validate_and_normalize()`. This is what's uploaded to S3 and what the
  running portal fetches for findings. The version field lets whatever
  produces this JSON and the portal evolve independently: an unsupported
  major version, or any malformed payload, is rejected with a clear error
  and the portal falls back to the last known-good data instead of
  breaking. Each record's core metadata (title, authors, ids, tracks,
  section) can be paired with an optional `summary` — a short plain-text
  description of the finding, used by the Reels view (see below); a
  record without one just renders without a summary rather than leaving a
  gap. `data/example_findings.json` is a small illustration of the shape.
- **Theme taxonomy**: `S3_TRACKS_KEY` (fallback: `data/example_tracks.json`)
  — id → full/short label + color. Never hardcoded in the UI, and
  deliberately not part of the findings contract — whatever produces
  findings shouldn't need to know the portal's color scheme.
- **Feed branding**: `S3_FEED_KEY` (fallback: `data/example_feed.json`) —
  page heading/subtitle/owner, plus two opt-in feature flags,
  `chat_enabled` and `reels_enabled` (both default `false`), that toggle
  the in-app chat and Reels tabs on/off (see "Reels" and "Chat" below).
  The MVP precursor to the full multi-feed manifest planned for V2.
- **Local fallbacks**: Render's free tier wipes local disk on spin-down, so
  nothing written at runtime persists. Each of the three S3 objects above
  falls back to its bundled `data/example_*` file when its S3 key is
  unconfigured, unreachable, or (for findings) fails contract validation —
  the server never crashes for lack of S3 access or a bad upload.

## Local run

Needs Python **3.10+** (`app/mcp_server.py`'s `mcp` dependency requires
it). If your system Python is older, install one with
[`uv`](https://docs.astral.sh/uv/) instead of upgrading system-wide —
`uv python install 3.12`, then run the commands below as `uv run
--python 3.12 python3 -m app.server` etc.

```bash
pip install -r requirements.txt   # boto3 + pyzotero + mcp; everything else is stdlib
python3 -m app.server --port 8000
```

Then open http://localhost:8000. `.env` (gitignored) is loaded automatically
if present, so local dev picks up S3 credentials without exporting them by
hand. Without any S3 env vars set, the server serves the bundled
`data/example_*` files and still works fully.

### Static-only preview (no server)

```bash
python3 -m app.build
open dist/index.html
```

Reads `data/example_findings.json` (or `--contract path/to/findings.json`)
and writes `dist/findings.json` (the full enriched feed — contract records
plus a tracks taxonomy/branding/counts merged in, [spec §7](docs/SPEC.md#7-feed-schema)
schema) plus a self-contained `dist/index.html` gallery — useful for a
quick visual check or for hosting a fully static snapshot somewhere.

### Tests

```bash
python3 -m unittest discover -s tests -v
```

## Routes

| Route | Response |
|---|---|
| `GET /` | Rendered gallery (server-side data injection) |
| `GET /api/v1/findings?section=&track=&q=&warn=&linkedin=` | JSON: filtered records |
| `GET /api/v1/meta` | JSON: counts, freshness (`source_last_modified`), taxonomy |
| `GET /refresh` | Forces an immediate S3 re-fetch (bypasses the TTL cache) |
| `GET /api/v1/zotero` | JSON: cached read-only Zotero reconciliation report — `{"configured": false}` if no Zotero credentials are set |
| `GET /zotero/refresh` | Forces an immediate Zotero re-fetch (bypasses its own TTL cache) |
| `GET /upload` | Drag-and-drop / fetch-by-URL upload page |
| `POST /upload` | Body = findings JSON (drag-and-drop), or `?url=<encoded-url>` with no body (server fetches it) — validated against the contract and written to S3, same as `python3 -m app.upload_findings` |
| `POST /api/v1/chat` | Body `{"question": str}` → `{"answer": str, "sources": [...]}`. Answers from the current findings feed via OpenAI (`app/chat.py`) — `404` if the feed's `chat_enabled` isn't `true`, `503` if `OPENAI_API_KEY` is unset |
| `GET /healthz` | `200 OK`, never PIN-gated (Render health check) |

(`/api/findings`, `/api/meta`, and `/api/chat` also work, unversioned, as aliases.)

## Refreshing the data (no redeploy)

```bash
python3 -m app.upload_findings path/to/findings.json   # validates, then uploads
python3 -m app.upload_config tracks path/to/tracks.json
python3 -m app.upload_config feed path/to/feed.json
```

`upload_findings` validates its input against the data contract before
uploading. There's no default input path — pass one explicitly, so the
bundled example data is never one missing argument away from overwriting
real S3 content. `upload_config` pushes the taxonomy or branding config
the same way.

There's also a browser path for the same findings upload: open `/upload`
on the running portal (PIN-gated, same as every other route) to drag a
`.json` file in directly, or paste a URL for the server to fetch and
upload itself. Both share `app/upload_findings.py`'s
`upload_contract_to_s3()`, so a browser upload gets exactly the same
contract validation and lands at the same S3 key as the CLI path — it
just can't accidentally target the wrong bucket/key, since there's no
argument to get wrong. Fetch-by-URL is restricted to `http(s)://` and
capped at 5&nbsp;MB.

Each upload lands at `s3://$S3_BUCKET/$S3_ROOT_FOLDER<key>`. The running
portal picks up findings automatically within `REFRESH_TTL_SECONDS`
(default 60s), or immediately via `GET /refresh` (tracks/feed refresh on
the same cycle). The "Data sidst opdateret" stamp in the UI reflects the
contract's own `last_updated` field; `source_last_modified` in
`/api/v1/meta` reflects the S3 object's actual `LastModified` timestamp.

If a findings upload fails contract validation, it's rejected client-side
before anything reaches S3 — the previous good data keeps serving. If a bad
payload somehow does reach S3, the portal detects that on fetch and falls
back to its bundled local copy instead of breaking; see `app/contract.py` /
`app/s3sync.py`.

## Deploying to Render

`render.yaml` defines two services:

- **researchthis-portal** — the portal itself (`app/server.py`).
- **researchthis-mcp** — the remote MCP server (`app/mcp_server.py`, see
  "MCP access" below); a thin HTTP client of researchthis-portal's own
  routes, needing no AWS/Zotero secrets of its own.

1. Push this repo to GitHub.
2. In Render: New → Blueprint → point at the repo (`render.yaml` picks up
   both services automatically), or New → Web Service twice using the
   values in `render.yaml` directly.
3. Set the `sync: false` env vars in the Render dashboard for
   **researchthis-portal** (never commit these): `AWS_ACCESS_KEY_ID`,
   `AWS_SECRET_ACCESS_KEY`, and optionally `PAGE_PIN` if you want the
   portal password-gated.
4. Deploy `researchthis-portal` first. `/healthz` should return `200`.
   Note its actual URL (shown at the top of its Render dashboard page —
   normally `https://<the name you gave it>.onrender.com`).
5. Deploy `researchthis-mcp`, then set its `sync: false` env var in the
   Render dashboard (not a secret, just deliberately not committed — see
   `app/mcp_server.py`'s module docstring for why there's no default):
   **`RESEARCHTHIS_PORTAL_URL`** — the URL from step 4. `/wake` should
   return `200`.
6. Upload your real findings/tracks/feed JSON to S3 (see above) — until
   you do, the portal serves the bundled example data.

## Zotero reconciliation (read-only)

Matches this feed's findings against a live Zotero library by DOI/arXiv and
reports what's already there, what's missing, and any DOI duplicates.
**It never writes to Zotero** — see [spec §10](docs/SPEC.md#10-zotero-integration)
for why (a changed item key breaks Word documents that cite it via the
Zotero Word plugin's live field codes). Tagging in place and selectively
adding missing items are deliberately separate, not-yet-built follow-up
steps ([§10.1](docs/SPEC.md#101-reconciliation-steps)).

Runs two ways:

- **On the portal**: `GET /api/v1/zotero` — `app/zotero_store.py` caches
  the report (TTL `ZOTERO_REFRESH_TTL_SECONDS`, default 900s) so the live
  Zotero API isn't hit on every request. The gallery header shows a small
  "X/N i Zotero" badge (fetched client-side; stays hidden if
  unconfigured, and degrades silently on the static `dist/index.html`
  build, which has no server behind it to answer the fetch).
- **A second "Zotero" tab** in the gallery shows a plain card view of the
  library itself (title/creators/date/item type/DOI), independent of the
  findings feed — deliberately no track filters, just a search box and a
  "missing DOI only" toggle (`app/reconcile_zotero.py:library_cards`). No
  config flag needed: it's driven entirely by whether Zotero is reachable.
  If credentials aren't set, or the last live fetch failed, or the library
  has zero items, the tab shows a plain-language reason instead of an
  empty grid.
- **From the CLI**, for a one-off full report (matches, the full missing
  list, duplicate details):
  ```bash
  python3 -m app.build          # ensure dist/findings.json exists
  python3 -m app.reconcile_zotero --out zotero_report.json
  ```

Both need `ZOTERO_LIBRARY_ID`, `ZOTERO_LIBRARY_TYPE` (`user`/`group`,
default `user`), and `ZOTERO_API_KEY` (Zotero → Settings → Security →
Applications; `ZOTERO_APP_KEY` also works, matching the name shown on that
settings page) in the environment or `.env`. Tip:
`curl https://api.zotero.org/keys/<key>` returns the key's `userID`
directly, so you don't have to dig it out of Zotero's UI by hand. Without
these set, the portal simply serves `{"configured": false}` and the badge
never appears — Zotero is fully optional.

## Reels (optional, off by default)

A "Reels" tab lets visitors swipe vertically through findings one at a
time — TikTok-style, but scientific papers instead of videos. It's plain
CSS scroll-snap client-side, no scroll library: each card shows the
finding's usual metadata plus its `summary` field (see "Data contract"
above), and a "🔀 Shuffle" button re-randomizes the order for a fresh pass
through the same feed. A finding without a `summary` yet just renders
without one rather than leaving a blank card.

Hidden unless explicitly turned on, the same opt-in pattern as `chat_enabled`
below: set `"reels_enabled": true` in the feed manifest (`S3_FEED_KEY`,
fallback `data/example_feed.json` — see `app/feed.py`), so an existing
deployment's manifest doesn't suddenly surface a new tab. Together with
the default grid, Zotero tab, in-app chat, and MCP access below, it's one
more way to read the same underlying feed — nothing about the data
contract or S3 layout changes to support it.

## Chat (optional, off by default)

A small "Chat" tab in the gallery lets visitors ask free-text questions
about the findings feed, answered by OpenAI (`app/chat.py`, `POST
/api/v1/chat`). It's read-only, and hidden unless explicitly turned on:
set `"chat_enabled": true` in the feed manifest (`S3_FEED_KEY`, fallback
`data/example_feed.json` — see `app/feed.py`). Off by default because the
quality bar isn't there yet for a public-facing widget (see the retrieval
caveats below); the code stays in the tree and the route stays reachable
for anyone who wants to keep improving or self-hosting it, it's just not
surfaced to visitors until the flag is flipped. With the flag off, the
tab doesn't render *and* `POST /api/v1/chat` itself returns `404` (not
just a hidden button — the route is actually off, so a request to it
directly doesn't spend any OpenAI budget). Without `OPENAI_API_KEY` set,
an enabled chat still renders but every question gets back a "chat isn't
set up" message instead of a 500.

If you'd rather point your own Claude Desktop/Claude Code at these two
sources instead of using this in-app widget, see "MCP access" below —
letting an MCP client's own model handle retrieval/reasoning sidesteps
most of the fragility described next.

Retrieval reuses `filter_records` (the same title/authors substring match
the search box uses) rather than a separate search implementation: the
question is split into keywords, each is run through `filter_records` as
its own `q` filter, and matches are ranked by how many keywords hit,
capped at 12 records. A keyword matching more than ~15% of all records
(e.g. "paper", "from") is treated as too generic to be a relevance signal
and ignored, on top of a small DA/EN stopword list — otherwise a query
like "any paper from X?" would return unrelated records as if they
mattered. 

If nothing matches, the most recent findings are sent to OpenAI
as fallback context so there's always something to reason about, but
they're never reported back as "sources" — only genuine keyword matches
are, so the source list never misrepresents filler as relevant. Only
record metadata (title, authors, date, tracks, section, ids) is ever sent
to OpenAI — deliberately not a record's `summary` field (see "Reels"
above) even when one is present, so answer quality doesn't quietly depend
on which findings happen to have a summary written — and the system
prompt tells the model to say so rather than invent findings when the
metadata isn't enough to answer. Each question is a single, stateless
request/response; there's no server-side conversation history.

If Zotero is configured (see above), the chat also searches the connected
library by the same title/creators keyword match, as a second, optional
source alongside the findings feed — with its own generic-word guard, but
no "recent items" fallback (an unmatched personal library isn't useful
filler the way recent findings are, so it's simply left out rather than
padded). 

The system prompt tells the model to say explicitly when an
answer draws on a Zotero item rather than the findings feed, and the UI
renders Zotero-sourced citations in a visually distinct color (the same
purple used for the "related" pill) so the two are never confused at a
glance.

Needs `OPENAI_API_KEY` and, optionally, `OPENAI_MODEL` (default
`gpt-4o-mini`) in the environment or `.env`.

## MCP access

`app/mcp_server.py` exposes the findings feed and (if configured)
connected Zotero library as read-only MCP tools, so an MCP client you
already use — Claude Desktop, Claude Code — can query them directly with
its own model doing the reasoning, instead of going through the in-app
chat above. It's a thin HTTP client of researchthis-portal's existing
public routes (`GET /api/v1/findings`, `/api/v1/meta`, `/api/v1/zotero` —
the same ones the browser gallery itself calls), *not* a separate path
that imports `app.server` and hits S3/Zotero directly: that would need
AWS/Zotero secrets wherever it ran. It's also deliberately thin in a
second sense: no keyword-matching heuristics, generic-word guards, or
reply-language handling live here (contrast with `app/chat.py`) — that's
the whole appeal of this path for anyone who already has an MCP client
capable of that reasoning natively.

**Primary path: it's hosted directly on Render** as its own service
(`researchthis-mcp` in `render.yaml`, streamable-HTTP transport), so an
MCP client connects straight to that service's URL as a *remote* server —
nothing to install, run, or clone on the machine using it, on any Mac.
See "Deploying to Render" above for standing this service up.

**Claude Desktop**: Settings → Connectors → Add → "Add custom connector" →
paste in the `researchthis-mcp` service's URL with `/mcp` appended (e.g.
`https://<your-service-name>.onrender.com/mcp`) → Add.

**Claude Code**:

```bash
claude mcp add --transport http researchthis-portal https://<your-service-name>.onrender.com/mcp
```

**Secondary path: run it locally instead**, e.g. to test against a
different deployment without touching Render. `app/mcp_server.py`
carries its own [PEP 723](https://peps.python.org/pep-0723/) inline
metadata (a `# /// script` header declaring `requires-python` and
`dependencies = ["mcp"]`), so a copy of just this one file can be run
directly with [`uv`](https://docs.astral.sh/uv/) — no project clone, no
`pip install`:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # if uv isn't already installed
uv run /path/to/mcp_server.py
```

With no `$PORT` env var set, it runs stdio instead of streamable-http
(see the module docstring) — the same transport a locally-launched
Claude Desktop/Code MCP entry expects. `RESEARCHTHIS_PORTAL_URL` must be
set explicitly (no default — see "Requirements" below):

```json
{
  "mcpServers": {
    "researchthis-portal-local": {
      "command": "/Users/you/.local/bin/uv",
      "args": ["run", "/path/to/mcp_server.py"],
      "env": {
        "RESEARCHTHIS_PORTAL_URL": "https://<your-service-name>.onrender.com"
      }
    }
  }
}
```

Tools (identical on both paths):

| Tool | Purpose |
|---|---|
| `search_findings(query, section, track, warn, linkedin)` | Same filtering as `GET /api/v1/findings` |
| `get_findings_meta()` | Counts, freshness, theme-track taxonomy |
| `search_zotero_library(query)` | Title/creators substring search over the connected Zotero library |
| `get_zotero_reconciliation()` | The exact DOI/arXiv match between the findings feed and Zotero (`app/reconcile_zotero.py`) — answers "what's in both sources" or "what's missing from Zotero" directly, rather than needing the client to cross-reference the other two tools' results by hand |

Env vars (for the deployed `researchthis-mcp` service, set via the Render
dashboard — see "Deploying to Render" above; running your own copy needs
`RESEARCHTHIS_PORTAL_URL` set explicitly too — no built-in default,
deliberately, see the module docstring):

| Var | Required? | Purpose |
|---|---|---|
| `RESEARCHTHIS_PORTAL_URL` | yes | Base URL of the portal to query |
| `RESEARCHTHIS_PORTAL_PIN` | no | Only needed if the portal's own `PAGE_PIN` is set — sent as HTTP Basic auth |
| `PORT` | no | If set, runs streamable-HTTP bound to `0.0.0.0:$PORT` instead of stdio |

In streamable-HTTP mode it also exposes `GET /wake` — `200 OK`, unauthenticated,
mirroring the portal's `/healthz` — set as this service's `healthCheckPath`
in `render.yaml`, and also usable as an external uptime-pinger target to
keep it from spinning down on Render's free tier.

### Requirements

Needs Python **3.10+**, same as the rest of this project (see "Local
run" above) — `mcp` is a plain entry in the shared `requirements.txt`,
no special-casing. `render.yaml`'s `researchthis-mcp` service pins
`PYTHON_VERSION` explicitly rather than relying on Render's default;
running your own copy locally via `uv run` doesn't need `requirements.txt`
at all — `uv` reads `mcp_server.py`'s own PEP 723 header instead.

## Environment variables

| Var | Required | Purpose |
|---|---|---|
| `S3_BUCKET` | for S3 mode | Bucket holding findings/tracks/feed JSON |
| `S3_ROOT_FOLDER` | no | Key prefix, e.g. `ResearchThis/` |
| `S3_FINDINGS_KEY` | for S3 mode | Object key (under the prefix) for the findings contract `.json` |
| `S3_TRACKS_KEY` | no | Object key for the theme-track taxonomy `.json` |
| `S3_FEED_KEY` | no | Object key for the feed manifest/branding `.json` |
| `AWS_REGION` | for S3 mode | e.g. `eu-north-1` |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | for S3 mode | IAM credentials scoped to the bucket |
| `REFRESH_TTL_SECONDS` | no | Re-fetch throttle, default `60` |
| `ZOTERO_LIBRARY_ID` | for Zotero | Numeric user or group library ID |
| `ZOTERO_LIBRARY_TYPE` | no | `user` (default) or `group` |
| `ZOTERO_API_KEY` / `ZOTERO_APP_KEY` | for Zotero | Zotero → Settings → Security → Applications |
| `ZOTERO_REFRESH_TTL_SECONDS` | no | Zotero re-fetch throttle, default `900` |
| `OPENAI_API_KEY` | for chat | Enables `POST /api/v1/chat`; without it, the endpoint returns `503` |
| `OPENAI_MODEL` | no | Chat completion model, default `gpt-4o-mini` |
| `PAGE_PIN` | no | If set, gates all routes except `/healthz` behind HTTP Basic auth (any username, this as password) |
| `PORT` | no | Listen port (Render sets this itself) |

Each of `S3_FINDINGS_KEY`/`S3_TRACKS_KEY`/`S3_FEED_KEY` is independently
optional: whichever aren't set (or aren't reachable) just serve their
bundled `data/example_*` fallback — this is intentional so local dev and CI
never need AWS credentials.

## Security notes

- No generic static-file route exists (only the fixed routes above), so
  there's no path-traversal surface to guard against.
- `POST /upload` is behind the same PIN gate as every other route once
  `PAGE_PIN` is set — it's a write path to S3, not a read, so it should
  never be left open on a public deployment without one. Its `?url=` fetch
  mode only follows `http(s)://` and caps the response at 5&nbsp;MB before
  it's even parsed as JSON, let alone validated against the contract.
- All record text is HTML-escaped when injected into the gallery template,
  and JSON embedded in `<script>` tags has `</` escaped — defensive given
  [§3.4/§5.4](docs/SPEC.md#34-future-multi-feed-manifest)
  plan to eventually accept feeds from third parties.
- Secrets only ever come from environment variables / `.env` (gitignored),
  never committed.
- `data/example_*` are placeholder content, safe to publish; a
  deployment's real data lives only in S3.

## Repo layout

All source is one package (`app/`), run as `python3 -m app.<module>`.
Everything else at root is config, deploy, data, or generated output —
never Python source.

```
app/                     the only place .py source lives
  contract.py                the versioned findings JSON contract + validate_and_normalize()
  feed.py                    assembles the full feed dict (docs/SPEC.md §7) from a contract + tracks + manifest
  render.py                  injects a feed dict into templates/gallery.html
  s3sync.py                  fetches findings/tracks/feed from S3, local-file fallback for each
  build.py                   CLI: contract .json -> dist/findings.json + dist/index.html
  server.py                  stdlib http.server + boto3 dynamic server
  upload_findings.py         uploads a findings JSON contract to S3 (the refresh workflow)
  upload_config.py           uploads tracks.json / feed.json to S3
  upload_endpoint.py         validate+upload logic behind POST /upload (drag-and-drop / fetch-by-URL)
  reconcile_zotero.py        read-only Zotero reconciliation logic + CLI (docs/SPEC.md §10.1 step 1)
  zotero_store.py            TTL cache around reconcile_zotero, backs GET /api/v1/zotero
  chat.py                    OpenAI Q&A over the findings feed, backs POST /api/v1/chat (off by default, see chat_enabled)
  mcp_server.py              remote MCP server (researchthis-mcp on Render) over the portal's HTTP API; dual stdio/streamable-http, own PEP 723 deps for local `uv run` too
data/                     illustrative example content only — see note above
  example_findings.json       sample findings contract (local S3 fallback for findings)
  example_tracks.json         sample theme taxonomy (local S3 fallback for tracks)
  example_feed.json           sample branding/manifest (local S3 fallback for feed)
templates/                the UI — single source of truth for markup/CSS/JS
  gallery.html
  upload.html                 GET /upload page (drag-and-drop + fetch-by-URL)
tests/
  test_contract.py
  test_feed.py
  test_s3sync.py
  test_reconcile_zotero.py
  test_zotero_store.py
  test_upload_endpoint.py
  test_chat.py
dist/                     generated (gitignored): findings.json + index.html
render.yaml, requirements.txt   Render deploy config (two services)
```
