"""Renders a feed dict (app.feed.build_feed output) into the gallery HTML page.

The template (templates/gallery.html) is the single source of truth for the
UI — this module only injects data into it. Keep markup/CSS/JS changes in
the template, not here.
"""

from __future__ import annotations

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_PATH = os.path.join(ROOT, "templates", "gallery.html")
LOCALES_DIR = os.path.join(ROOT, "app", "locales")
DEFAULT_LANGUAGE = "en"


def _load_locale(language: str) -> dict:
    """Loads the UI-strings JSON for `language`, falling back to English.

    Adding a new UI language is just dropping app/locales/<code>.json —
    no template or render.py changes needed.
    """
    path = os.path.join(LOCALES_DIR, f"{language}.json")
    if not os.path.isfile(path):
        path = os.path.join(LOCALES_DIR, f"{DEFAULT_LANGUAGE}.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _safe_json(obj) -> str:
    """JSON-encode for embedding inside a <script> tag.

    Escapes '</' so a title/author containing "</script>" can't break out
    of the script block (the data is human-curated but may eventually come
    from third-party feeds — see docs/SPEC.md §3.4/§5.4).
    """
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


def render_gallery_html(feed: dict, template_path: str = TEMPLATE_PATH) -> str:
    with open(template_path, encoding="utf-8") as f:
        template = f.read()

    language = feed.get("language") or DEFAULT_LANGUAGE
    i18n = _load_locale(language)

    data_blob = {
        "last_updated": feed["last_updated"],
        "records": feed["records"],
        "owner": feed.get("owner", ""),
        "chat_enabled": bool(feed.get("chat_enabled", False)),
        "reels_enabled": bool(feed.get("reels_enabled", False)),
    }
    title = f"{feed.get('heading', feed.get('title', 'ResearchThis Portal'))}"

    json_replacements = {
        "__RESEARCHTHIS_TRACKS_JSON__": _safe_json(feed["tracks"]),
        "__RESEARCHTHIS_DATA_JSON__": _safe_json(data_blob),
        "__RESEARCHTHIS_I18N_JSON__": _safe_json(i18n),
    }
    text_replacements = {
        "__RESEARCHTHIS_LANG__": language,
        "__RESEARCHTHIS_TITLE__": title,
        "__RESEARCHTHIS_HEADING__": feed.get(
            "heading", feed.get("title", "ResearchThis Portal")
        ),
        "__RESEARCHTHIS_SUBTITLE__": feed.get("subtitle", ""),
        "__RESEARCHTHIS_FLAG__": feed.get("flag", ""),
    }
    html = template
    for token, value in json_replacements.items():
        html = html.replace(token, value)
    for token, value in text_replacements.items():
        html = html.replace(token, _escape_html_text(value))
    return html


def _escape_html_text(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
