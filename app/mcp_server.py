# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp"]
# ///
"""MCP server exposing the deployed ResearchThis Portal's findings feed and
(if configured) connected Zotero library as read-only tools for an MCP
client -- e.g. Claude Desktop or Claude Code -- to query directly, instead
of (or alongside) the in-app chat (app/chat.py, off by default -- see
app/feed.py's chat_enabled).

Deployed as its own Render web service (see render.yaml's
"researchthis-mcp" service) speaking the MCP protocol directly over HTTP
(streamable-http transport) -- an MCP client just points at this
service's URL as a *remote* server, with nothing to install or run
locally on any machine. It is a thin HTTP client of the sibling
researchthis-portal service's existing public routes (GET
/api/v1/findings, /api/v1/meta, /api/v1/zotero -- the same ones the
browser gallery itself calls), not a separate path that imports
app.server and hits S3/Zotero directly: that would need this repo's .env
(AWS + Zotero credentials) wherever it runs. Proxying the deployed
portal's own REST API instead means this service needs no AWS/Zotero
secrets of its own.

Transport is chosen by environment, mirroring app/server.py's own
--port/$PORT convention: Render's platform sets $PORT for every web
service, so its presence means "run as an HTTP server" (streamable-http,
bound to 0.0.0.0:$PORT); without it (e.g. testing locally), this falls
back to stdio -- which also still works as a local Claude Desktop/Claude
Code MCP entry via `uv run app/mcp_server.py` (or a copy of this single
file elsewhere -- the PEP 723 header above lets `uv` install `mcp` on its
own, no project clone needed for that path either) if you'd rather not
depend on the hosted service.

Deliberately thin in another sense too: no retrieval heuristics,
keyword-match generic-word caps, or reply-language handling live here
(contrast with app/chat.py) -- an MCP client's own model does all of that
reasoning natively, which is the whole reason to prefer this path over
the in-app chat for anyone who already has an MCP client.
get_zotero_reconciliation in particular exposes the already-computed
exact DOI/arXiv match between the two sources directly, rather than
leaving "what's in both" to fuzzy cross-referencing.

This file has no built-in default for which portal it talks to -- that's
a required env var, set explicitly by whatever's running it
(render.yaml for the deployed service; your own config if you run a
copy). A hardcoded fallback here would mean copies of this file silently
default to *this* project's specific deployment without whoever's
running it noticing or deciding that -- the whole point of pulling this
out of the code is that a deployment's identity belongs in its config,
not baked into the script.

DNS-rebinding protection (the mcp SDK's optional Host/Origin-header
allowlist for its HTTP transports) is deliberately left off here rather
than configured: that protection guards against a browser being tricked
into hitting a server trusted only because it's on localhost/the private
network -- not the threat model for a service like this one, which is
read-only, holds no secrets or session state, is meant to be called
directly over the public internet, and proxies data the portal already
serves to any browser that asks. Turning it on would just mean adding
yet another env var (this service's own public hostname) with nothing
real behind it to protect.

Also exposes GET /wake (streamable-http mode only), a plain 200 OK with
no auth, mirroring app/server.py's own /healthz -- for Render's health
check / an external uptime pinger wake up the service.

Env vars (see README's "MCP access" section):
    RESEARCHTHIS_PORTAL_URL   required -- base URL of the portal this proxies
    RESEARCHTHIS_PORTAL_PIN   optional -- only needed if that portal's own
                              PAGE_PIN is set, sent as HTTP Basic auth
"""

from __future__ import annotations

import base64
import json
import os
import urllib.parse
import urllib.request

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import PlainTextResponse

REQUEST_TIMEOUT_SECONDS = 20


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"{name} must be set -- this server has no built-in default "
            f"for it (see app/mcp_server.py's module docstring for why); "
            f'see README\'s "MCP access" section for what to set it to.'
        )
    return value


_PORT = os.environ.get("PORT")
_INSTRUCTIONS = (
    "Read-only access to the ResearchThis Portal's curated findings feed "
    "and, if configured, its connected Zotero library, via the portal's "
    "own deployed HTTP API."
)

if _PORT:
    # Render (or any $PORT-setting host): serve MCP over HTTP so a remote
    # client can connect directly, no local process needed.
    mcp = FastMCP(
        "researchthis-portal",
        instructions=_INSTRUCTIONS,
        host="0.0.0.0",
        port=int(_PORT),
        stateless_http=True,
    )
else:
    # No $PORT: local stdio use (Claude Desktop/Code launching this file
    # directly, or a copy of it, as a local process instead).
    mcp = FastMCP("researchthis-portal", instructions=_INSTRUCTIONS)


if _PORT:
    # Only meaningful under streamable-http (custom_route needs the
    # Starlette app that transport runs); stdio has no HTTP surface to
    # add a route to. Mirrors app/server.py's own /healthz: plain 200 OK,
    # no auth, for Render's health check / an external uptime pinger to
    # keep this free-tier service from spinning down.
    @mcp.custom_route("/wake", methods=["GET"])
    async def wake(request: Request) -> PlainTextResponse:
        return PlainTextResponse("OK")


def _portal_url() -> str:
    return _require_env("RESEARCHTHIS_PORTAL_URL").rstrip("/")


def _get_json(path: str, params: dict | None = None) -> dict:
    url = f"{_portal_url()}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url)
    pin = os.environ.get("RESEARCHTHIS_PORTAL_PIN")
    if pin:
        token = base64.b64encode(f"researchthis:{pin}".encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
        return json.loads(resp.read())


@mcp.tool()
def search_findings(
    query: str = "",
    section: str = "",
    track: int | None = None,
    warn: bool | None = None,
    linkedin: bool | None = None,
) -> list[dict]:
    """Search the curated findings feed (GET /api/v1/findings on the
    deployed portal).

    query: substring match against title or authors (case-insensitive).
    section: "relevant" or "related", or omit for both.
    track: a theme-track ID (see get_findings_meta for the taxonomy).
    warn: True to only include findings flagged unverified.
    linkedin: True to only include findings already used on LinkedIn.
    """
    params = {}
    if query:
        params["q"] = query
    if section:
        params["section"] = section
    if track is not None:
        params["track"] = str(track)
    if warn is not None:
        params["warn"] = str(warn)
    if linkedin is not None:
        params["linkedin"] = str(linkedin)
    return _get_json("/api/v1/findings", params)["records"]


@mcp.tool()
def get_findings_meta() -> dict:
    """Counts, freshness, and the theme-track taxonomy (id -> label/color)
    for the findings feed (GET /api/v1/meta on the deployed portal)."""
    return _get_json("/api/v1/meta")


@mcp.tool()
def search_zotero_library(query: str = "") -> list[dict]:
    """Search the connected Zotero library by title/creators substring
    (case-insensitive); an empty query lists the whole library. Returns
    an empty list if Zotero isn't configured on the deployed portal."""
    data = _get_json("/api/v1/zotero")
    if not data.get("configured") or not data.get("report"):
        return []
    library = data["report"]["library"]
    if not query:
        return library
    q = query.lower()
    return [
        item
        for item in library
        if q in item["title"].lower() or q in item["creators"].lower()
    ]


@mcp.tool()
def get_zotero_reconciliation() -> dict:
    """Findings already in Zotero, findings missing from Zotero, and DOI
    duplicates within Zotero -- an exact DOI/arXiv match between the two
    sources, computed by the deployed portal (app/reconcile_zotero.py), so
    "which findings are in both sources" or "what's missing from Zotero"
    can be answered directly instead of cross-referencing search_findings
    and search_zotero_library results by hand. Returns {"configured":
    False} if Zotero isn't configured on the deployed portal."""
    data = _get_json("/api/v1/zotero")
    if not data.get("configured"):
        return {"configured": False}
    return {"configured": True, **(data.get("report") or {})}


def main() -> None:
    _require_env("RESEARCHTHIS_PORTAL_URL")  # fail at startup, not on first tool call
    mcp.run(transport="streamable-http" if _PORT else "stdio")


if __name__ == "__main__":
    main()
