"""Caches the read-only Zotero reconciliation report so the running portal
can expose it via an API route without hitting Zotero on every request.

Zotero credentials are optional: if unset, `configured` is False and no
network calls happen. The underlying logic (matching, dedup, duplicate
detection) lives in app/reconcile_zotero.py — this module only adds
TTL-based caching and error-handling around it, and never writes to
Zotero (same guarantee as app/reconcile_zotero.py, see its docstring).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone

logger = logging.getLogger("researchthis.zotero_store")

ZOTERO_REFRESH_TTL_SECONDS = int(os.environ.get("ZOTERO_REFRESH_TTL_SECONDS", "900"))


def _zotero_credentials():
    library_id = os.environ.get("ZOTERO_LIBRARY_ID")
    library_type = os.environ.get("ZOTERO_LIBRARY_TYPE", "user")
    # ZOTERO_APP_KEY is an alias -- the name shown on Zotero's own
    # "Applications" settings page for a newly created key.
    api_key = os.environ.get("ZOTERO_API_KEY") or os.environ.get("ZOTERO_APP_KEY")
    return library_id, library_type, api_key


class ZoteroStore:
    """Holds the most recent reconciliation report; refreshes at most once per TTL.

    `get_report(findings)` never raises -- callers always get a dict
    describing either a cached report, an "unconfigured" state, or (if a
    Zotero fetch just failed) the last error alongside any previously
    cached good report.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._report: dict | None = None
        self._fetched_at: str | None = None
        self._last_error: str | None = None
        self._last_attempt = 0.0

    def get_report(self, findings: list, force: bool = False) -> dict:
        library_id, library_type, api_key = _zotero_credentials()
        if not library_id or not api_key:
            return self._snapshot(configured=False)

        with self._lock:
            now = time.time()
            if not force and (now - self._last_attempt) < ZOTERO_REFRESH_TTL_SECONDS:
                return self._snapshot(configured=True)
            self._last_attempt = now

            try:
                from app.reconcile_zotero import build_report, fetch_zotero_items
            except ImportError:
                self._last_error = "pyzotero not installed"
                logger.warning("Zotero is configured but pyzotero is not installed")
                return self._snapshot(configured=True)

            try:
                zotero_items = fetch_zotero_items(library_id, library_type, api_key)
                self._report = build_report(findings, zotero_items)
                self._fetched_at = datetime.now(timezone.utc).isoformat()
                self._last_error = None
                logger.info(
                    "Zotero reconciliation refreshed (%d Zotero items, %d findings)",
                    len(zotero_items),
                    len(findings),
                )
            except Exception as e:
                self._last_error = str(e)
                logger.exception("Zotero reconciliation refresh failed")

            return self._snapshot(configured=True)

    def _snapshot(self, configured: bool) -> dict:
        return {
            "configured": configured,
            "fetched_at": self._fetched_at,
            "last_error": self._last_error,
            "report": self._report,
        }
