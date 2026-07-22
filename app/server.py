#!/usr/bin/env python3
"""ResearchThis Portal — stdlib http.server + boto3 dynamic server (docs/SPEC.md §6.1, §9).

Deliberately framework-free (ThreadingHTTPServer, no Flask/FastAPI) to
mirror the owner's existing eval-server deploy pattern on Render's free
tier. S3 holds a deployment's actual findings (app/contract.py), theme
taxonomy, and feed branding; the files bundled under data/ are generic
examples used as a local fallback when S3 is unconfigured, unreachable, or
returns something invalid, so the server never crashes for lack of AWS
access or a bad upload.

Routes:
    GET /                     rendered gallery (server-side data injection)
    GET /api/v1/findings      JSON: filterable records
                              (?section=&track=&q=&warn=&linkedin=)
    GET /api/v1/meta          JSON: counts, freshness, taxonomy
    GET /refresh              force an immediate S3 re-fetch
    GET /api/v1/zotero        JSON: cached read-only Zotero reconciliation
                              report (see app/zotero_store.py); {"configured":
                              false} if no Zotero credentials are set
    GET /zotero/refresh       force an immediate Zotero re-fetch
    GET /upload               drag-and-drop / fetch-by-URL upload page
    POST /upload              body = findings JSON (drag-and-drop), or
                              ?url=<encoded-url> with no body (server
                              fetches it) -- validated against the findings
                              contract (app/contract.py) and written to S3
                              (app/upload_endpoint.py), same as
                              `python3 -m app.upload_findings`
    POST /api/v1/chat         body = {"question": str} -- answers from the
                              current findings feed (and, if configured, the
                              connected Zotero library) via OpenAI, using
                              filter_records (below) for keyword retrieval
                              (app/chat.py); {"error": ...} 404 if the feed
                              manifest's chat_enabled is not true (see
                              app/feed.py), 503 if OPENAI_API_KEY is unset
    GET /healthz              200 OK (Render health check; never PIN-gated)

Env vars: S3_BUCKET, AWS_REGION, S3_ROOT_FOLDER, S3_FINDINGS_KEY,
S3_TRACKS_KEY, S3_FEED_KEY, REFRESH_TTL_SECONDS (default 60), PAGE_PIN
(optional HTTP Basic gate), ZOTERO_LIBRARY_ID, ZOTERO_LIBRARY_TYPE,
ZOTERO_API_KEY/ZOTERO_APP_KEY, ZOTERO_REFRESH_TTL_SECONDS (default 900),
OPENAI_API_KEY, OPENAI_MODEL (default gpt-4o-mini) -- all optional; each
falls back to its bundled local example without S3 configured for it,
Zotero reconciliation is simply unavailable ("configured": false) without
its credentials, and chat returns a 503 without OPENAI_API_KEY.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from app.chat import ChatError, answer_question
from app.feed import build_feed
from app.render import render_gallery_html
from app.s3sync import fetch_feed_manifest, fetch_findings_contract, fetch_tracks
from app.upload_endpoint import UploadError, process_upload
from app.zotero_store import ZoteroStore

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOAD_PAGE_PATH = os.path.join(ROOT, "templates", "upload.html")
MAX_UPLOAD_CONTENT_LENGTH = 5 * 1024 * 1024
MAX_CHAT_CONTENT_LENGTH = 4 * 1024  # {"question": "..."} only -- see app/chat.py


def _load_dotenv(path: str) -> None:
    """Minimal local-dev convenience: load KEY=VALUE lines from .env into
    os.environ (without overriding already-set vars). Render sets real env
    vars directly, so this is a no-op there if no .env is deployed."""
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


_load_dotenv(os.path.join(ROOT, ".env"))

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger("researchthis.server")

FINDINGS_FALLBACK = os.path.join(ROOT, "data", "example_findings.json")
TRACKS_FALLBACK = os.path.join(ROOT, "data", "example_tracks.json")
FEED_MANIFEST_FALLBACK = os.path.join(ROOT, "data", "example_feed.json")
REFRESH_TTL_SECONDS = int(os.environ.get("REFRESH_TTL_SECONDS", "60"))
PAGE_PIN = os.environ.get("PAGE_PIN")


class FeedStore:
    """Holds the current assembled feed; re-fetches from S3 at most once per TTL."""

    def __init__(self):
        self._lock = threading.Lock()
        self._feed = None
        self._last_fetch = 0.0
        self.refresh(force=True)

    def refresh(self, force: bool = False) -> dict:
        with self._lock:
            now = time.time()
            if (
                not force
                and self._feed is not None
                and (now - self._last_fetch) < REFRESH_TTL_SECONDS
            ):
                return self._feed
            contract, source_last_modified = fetch_findings_contract(FINDINGS_FALLBACK)
            tracks = fetch_tracks(TRACKS_FALLBACK)
            manifest = fetch_feed_manifest(FEED_MANIFEST_FALLBACK)
            try:
                feed = build_feed(contract, tracks, manifest, source_last_modified)
            except Exception:
                logger.exception(
                    "failed to build feed from refreshed contract — keeping previous feed"
                )
                if self._feed is not None:
                    return self._feed
                raise
            self._feed = feed
            self._last_fetch = now
            return self._feed

    @property
    def feed(self) -> dict:
        return self.refresh()


store = FeedStore()
zotero_store = ZoteroStore()


def _json_response(handler: Handler, status: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _truthy(v: str | None) -> bool:
    return (v or "").lower() in ("1", "true", "yes")


def filter_records(records: list, qs: dict) -> list:
    section = qs.get("section", [None])[0]
    track = qs.get("track", [None])[0]
    q = (qs.get("q", [None])[0] or "").lower()
    warn = qs.get("warn", [None])[0]
    linkedin = qs.get("linkedin", [None])[0]

    track_id = None
    if track:
        try:
            track_id = int(track)
        except ValueError:
            track_id = -1  # matches nothing

    def keep(r: dict) -> bool:
        if section and r["section"] != section:
            return False
        if track_id is not None and track_id not in r["tracks"]:
            return False
        if q and q not in r["title"].lower() and q not in r["authors"].lower():
            return False
        if warn is not None and _truthy(warn) and not r["warn"]:
            return False
        return not (linkedin is not None and _truthy(linkedin) and not r["linkedin"])

    return [r for r in records if keep(r)]


class Handler(BaseHTTPRequestHandler):
    server_version = "ResearchThisPortal/0.1"

    def log_message(self, fmt, *args):
        logger.info("%s - %s", self.address_string(), fmt % args)

    def _check_pin(self) -> bool:
        if not PAGE_PIN:
            return True
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Basic "):
            try:
                decoded = base64.b64decode(auth[len("Basic ") :]).decode("utf-8")
                _, _, password = decoded.partition(":")
                if password == PAGE_PIN:
                    return True
            except (binascii.Error, UnicodeDecodeError):
                pass
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="ResearchThis Portal"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/healthz":
            body = b"OK"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if not self._check_pin():
            return

        if path == "/":
            html = render_gallery_html(store.feed).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)
            return

        if path in ("/api/findings", "/api/v1/findings"):
            feed = store.feed
            records = filter_records(feed["records"], qs)
            _json_response(self, 200, {"records": records, "count": len(records)})
            return

        if path in ("/api/meta", "/api/v1/meta"):
            feed = store.feed
            meta = {
                "feed_id": feed["feed_id"],
                "title": feed["title"],
                "owner": feed["owner"],
                "language": feed["language"],
                "last_updated": feed["last_updated"],
                "source_last_modified": feed["source_last_modified"],
                "counts": feed["counts"],
                "tracks": feed["tracks"],
            }
            _json_response(self, 200, meta)
            return

        if path == "/refresh":
            feed = store.refresh(force=True)
            _json_response(
                self, 200, {"status": "refreshed", "last_updated": feed["last_updated"]}
            )
            return

        if path in ("/api/zotero", "/api/v1/zotero"):
            result = zotero_store.get_report(store.feed["records"])
            _json_response(self, 200, result)
            return

        if path == "/zotero/refresh":
            result = zotero_store.get_report(store.feed["records"], force=True)
            _json_response(self, 200, result)
            return

        if path == "/upload":
            with open(UPLOAD_PAGE_PATH, encoding="utf-8") as f:
                html = f.read().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)
            return

        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if not self._check_pin():
            return

        if path == "/upload":
            url = qs.get("url", [None])[0] or None
            content_length = int(self.headers.get("Content-Length", "0") or "0")
            if content_length > MAX_UPLOAD_CONTENT_LENGTH:
                _json_response(
                    self,
                    400,
                    {"error": f"upload exceeds {MAX_UPLOAD_CONTENT_LENGTH} byte limit"},
                )
                return
            body = self.rfile.read(content_length) if content_length else b""
            try:
                result = process_upload(body, url)
            except UploadError as e:
                _json_response(self, e.status, {"error": e.message})
                return
            store.refresh(force=True)
            _json_response(self, 200, {"status": "uploaded", **result})
            return

        if path in ("/api/chat", "/api/v1/chat"):
            if not store.feed.get("chat_enabled"):
                _json_response(self, 404, {"error": "chat is disabled for this feed"})
                return
            content_length = int(self.headers.get("Content-Length", "0") or "0")
            if content_length > MAX_CHAT_CONTENT_LENGTH:
                _json_response(
                    self,
                    400,
                    {"error": f"request exceeds {MAX_CHAT_CONTENT_LENGTH} byte limit"},
                )
                return
            body = self.rfile.read(content_length) if content_length else b""
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                _json_response(self, 400, {"error": "invalid JSON body"})
                return
            question = payload.get("question") if isinstance(payload, dict) else None
            zotero_report = zotero_store.get_report(store.feed["records"])
            zotero_library = (
                zotero_report["report"]["library"]
                if zotero_report.get("configured") and zotero_report.get("report")
                else []
            )
            try:
                result = answer_question(
                    question, store.feed["records"], zotero_library=zotero_library
                )
            except ChatError as e:
                _json_response(self, e.status, {"error": e.message})
                return
            _json_response(self, 200, result)
            return

        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    args = ap.parse_args()

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    logger.info(
        "ResearchThis Portal listening on %s:%d (PIN gate %s)",
        args.host,
        args.port,
        "on" if PAGE_PIN else "off",
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
