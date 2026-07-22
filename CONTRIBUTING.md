# Contributing

Contributions are welcome — bug fixes, new routes/features, and improvements
to the data contract or Zotero reconciliation logic are all useful. See
[`README.md`](README.md) for how the pieces fit together before diving in.

## Project layout

All Python source lives under `app/` (a single package — run everything as
`python3 -m app.<module>` from the repo root). Only config/deploy files
(`render.yaml`, `requirements.txt`, `.env`), `templates/` (the UI),
`data/` (example fixtures), and `tests/` sit outside it. `dist/` is
generated (gitignored) — never edit or commit into it.

## Running locally

Needs Python **3.10+** (`app/mcp_server.py`'s `mcp` dependency requires
it; if your system Python is older, install one via
[`uv`](https://docs.astral.sh/uv/) — `uv python install 3.12` — and run
commands below as `uv run --python 3.12 python3 -m app.server` etc.
instead):

```bash
pip install -r requirements.txt
python3 -m app.server --port 8000
```

Without any `S3_*` env vars set, the server serves the bundled
`data/example_*` fixtures and works fully — no AWS credentials needed for
local dev. See the README's "Local run" and "Environment variables"
sections for `.env` and the full variable list.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Add or update a test under `tests/` alongside any change to `app/contract.py`,
`app/feed.py`, `app/s3sync.py`, `app/reconcile_zotero.py`, or
`app/zotero_store.py` — these carry most of the project's actual logic.

## Code style

CI (`.github/workflows/lint.yml`) runs `ruff check` and `ruff format --check`
on every push and PR; run `python3 -m ruff check .` and
`python3 -m ruff format .` locally before pushing. Otherwise match the style
of the file you are editing — the portal itself is intentionally minimal,
with no web framework beyond `http.server` + `boto3` (the `mcp` package's
own streamable-HTTP transport, used only by `app/mcp_server.py`, is the
one exception, and pulls in Starlette/uvicorn transitively).

## Changing the data contract

`app/contract.py` defines the versioned JSON contract between whatever
produces findings and this portal (`validate_and_normalize()`). It's meant
to let the two sides evolve independently, so:

- A breaking shape change needs a version bump and a compatibility note in
  `contract.py`'s docstring — an unrecognized version should fail loudly
  (clear error, fall back to last known-good data), not silently corrupt
  the gallery.
- If you add a field, update `data/example_findings.json` to demonstrate it
  and extend `tests/test_contract.py`.
- Theme-track taxonomy and feed branding are deliberately *not* part of the
  contract (see `app/contract.py`'s docstring for why) — changes to those
  belong in `app/feed.py` / `data/example_tracks.json` /
  `data/example_feed.json` instead.

For anything that changes the contract shape, the S3 fallback behavior, or
the Zotero reconciliation matching logic, opening an issue first is helpful
so the tradeoffs can be discussed before you invest in an implementation.

## Data and secrets

- `data/example_*` are small illustrative fixtures (not real content) and
  are committed on purpose — they're the local/CI fallback when no S3 keys
  are configured. Never replace them with real findings or credentials.
- `dist/` and `.env` are gitignored; don't force-add either into a PR.
- Never commit AWS or Zotero credentials. Local secrets go in `.env`
  (loaded automatically, gitignored); Render secrets are set via the
  dashboard as described in the README's "Deploying to Render" section.

## Bug reports and questions

Open an issue. Include the Python version, the command you ran (server,
build, upload, or reconcile), and any relevant error output.

## Code changes

Small fixes (typos, broken links, edge cases in analysis/rendering) can go
straight to a PR. For larger changes — new routes, changes to the S3 sync
or caching behavior, or anything touching the Zotero write-safety guarantee
([§10 in `docs/SPEC.md`](docs/SPEC.md#10-zotero-integration): this tool
never writes to Zotero) — open an issue first.
