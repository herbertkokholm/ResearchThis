"""Server-side logic for POST /api/v1/chat -- a small Q&A chat over the
findings feed (and, optionally, the connected Zotero library), backed by
OpenAI.

Findings retrieval deliberately reuses server.filter_records instead of a
separate search implementation: the question is split into significant
keywords, each is run through filter_records as its own `q` filter
(title/authors substring match -- the same check the search box uses), and
records are ranked by how many keywords they matched. This keeps "what the
chat can find" identical to "what the search box can find" and avoids a
second retrieval implementation to keep in sync. Zotero library items have
a different shape (title/creators/doi, no tracks/section/warn), so they get
their own small matcher (_select_zotero_context) that applies the same
keyword extraction and generic-word safeguard.

Only findings/Zotero metadata (title, authors/creators, date, identifiers)
is ever sent to OpenAI -- deliberately not a finding's optional `summary`
field (see app/contract.py), even when one is present, and there is no
equivalent in a Zotero library listing either -- so the prompt tells the
model to answer from that metadata alone and say so when it isn't enough,
rather than inventing findings.

The two sources are kept distinguishable end to end: each item in the
returned "sources" list carries a "type" ("finding" or "zotero") so the UI
can render them differently (app/chat.py's caller, templates/gallery.html),
and the model is told to say which source an answer draws on.

A surname search once answered "there are N" while listing N+1 matching
sources -- not a counting bug, it turned out: two different Zotero library
items matched on that surname but belonged to two different real people,
and the model quietly dropped the unrelated one instead of saying so.
(An earlier fix tried stating the section's exact item count in its
header instead, e.g. "Zotero library (4 item(s)):" -- that backfired: the
model started parroting that literal fragment into its prose instead of
actually accounting for every item.) The system prompt now requires
addressing every matched item explicitly, calling out same-surname/
different-person cases by name instead of silently excluding them, since
telling the model to match a count doesn't help when the "missing" item
was excluded for a legitimate reason it never disclosed.

Stateless by design, like the rest of this portal: each question is one
request/response, no server-side conversation history.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
REQUEST_TIMEOUT_SECONDS = 20
MAX_QUESTION_LENGTH = 500
MAX_CONTEXT_RECORDS = 12
MAX_ANSWER_TOKENS = 500
# A keyword that matches more than this share of all records is treated as
# too generic to be a relevance signal, even if it isn't in _STOPWORDS --
# a belt-and-suspenders catch for common words we didn't think to list (the
# stopword list caught "any paper from X?"'s "paper"/"from"; this catches
# whatever the next one turns out to be).
MAX_KEYWORD_MATCH_RATIO = 0.15
MIN_GENERIC_CAP = 3

# Danish-specific characters/words used only to pick a reply language (see
# _target_language below)
_DANISH_CHARS = set("æøåÆØÅ")
_DANISH_WORDS = {
    "der",
    "det",
    "den",
    "de",
    "og",
    "er",
    "om",
    "med",
    "hvad",
    "hvilke",
    "hvordan",
    "hvilken",
    "på",
    "af",
    "til",
    "har",
    "kan",
    "findes",
    "noget",
    "nogen",
    "som",
    "fra",
    "ikke",
    "hvorfor",
    "hvem",
    "jeg",
    "du",
    "vores",
    "hvilket",
    "hvornår",
}

# Small DA/EN stopword list -- just enough to keep short, common words from
# turning into noise keywords; not meant to be linguistically complete.
_STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "of",
    "in",
    "on",
    "for",
    "to",
    "is",
    "are",
    "was",
    "were",
    "what",
    "which",
    "who",
    "how",
    "about",
    "with",
    "that",
    "this",
    "does",
    "do",
    "did",
    "any",
    "there",
    "have",
    "has",
    "from",
    "know",
    "tell",
    "please",
    "want",
    "you",
    "your",
    # meta/framing words about "a finding" rather than its actual topic --
    # left in a query these just re-match everything, they don't narrow it
    # (e.g. "any paper from <author>?" matching unrelated titles on "from")
    "paper",
    "papers",
    "article",
    "articles",
    "study",
    "studies",
    "research",
    "der",
    "den",
    "det",
    "de",
    "og",
    "en",
    "et",
    "er",
    "om",
    "med",
    "hvad",
    "hvilke",
    "hvordan",
    "hvilken",
    "på",
    "af",
    "til",
    "har",
    "kan",
    "findes",
    "noget",
    "nogen",
    "som",
    "fra",
    "vil",
    "gerne",
    "kender",
    "fortæl",
    "venligst",
    "papir",
    "papirer",
    "artikel",
    "artikler",
}


class ChatError(Exception):
    """Carries the HTTP status the server.py handler should respond with."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _target_language(question: str) -> str:
    if any(ch in _DANISH_CHARS for ch in question):
        return "Danish"
    words = re.findall(r"[a-zA-ZæøåÆØÅ]+", question.lower())
    if any(w in _DANISH_WORDS for w in words):
        return "Danish"
    return "English"


def _keywords(question: str) -> list[str]:
    words = re.findall(r"[\w\-]+", question.lower(), flags=re.UNICODE)
    seen: list[str] = []
    for w in words:
        if len(w) > 2 and w not in _STOPWORDS and w not in seen:
            seen.append(w)
    return seen


def _select_context(question: str, records: list) -> tuple[list, bool]:
    """Ranks records by keyword hits via server.filter_records; falls back
    to the most recent records if no keyword matches anything.

    Returns (records, matched) -- matched is False for the fallback case, so
    callers can tell "these are the relevant findings" apart from "nothing
    matched, here's some context anyway" and avoid presenting the latter as
    if it were relevant (see the "sources" handling in answer_question).
    """
    from app.server import (
        filter_records,
    )  # local import: avoids a module cycle with server.py

    generic_cap = max(MIN_GENERIC_CAP, int(len(records) * MAX_KEYWORD_MATCH_RATIO))
    scores: dict[str, int] = {}
    for kw in _keywords(question):
        hits = filter_records(records, {"q": [kw]})
        if len(hits) > generic_cap:
            continue  # too generic to signal relevance -- see MAX_KEYWORD_MATCH_RATIO
        for r in hits:
            scores[r["raw_id"]] = scores.get(r["raw_id"], 0) + 1

    if scores:
        matched = [r for r in records if scores.get(r["raw_id"], 0) > 0]
        matched.sort(key=lambda r: scores[r["raw_id"]], reverse=True)
        return matched[:MAX_CONTEXT_RECORDS], True

    ranked = sorted(records, key=lambda r: r["date_found"], reverse=True)
    return ranked[:MAX_CONTEXT_RECORDS], False


def _select_zotero_context(question: str, library: list) -> list:
    """Keyword-matches the connected Zotero library (title/creators
    substring, same generic-word safeguard as _select_context).

    Unlike _select_context there's no "most recent items" fallback: an
    unmatched personal library isn't useful filler context the way recent
    findings are, so it's simply left out of the prompt (and therefore
    never shown as a source) rather than padded with irrelevant items.
    """
    if not library:
        return []

    generic_cap = max(MIN_GENERIC_CAP, int(len(library) * MAX_KEYWORD_MATCH_RATIO))
    scores: dict[str, int] = {}
    for kw in _keywords(question):
        hits = [
            item
            for item in library
            if kw in item["title"].lower() or kw in item["creators"].lower()
        ]
        if len(hits) > generic_cap:
            continue  # too generic to signal relevance -- see MAX_KEYWORD_MATCH_RATIO
        for item in hits:
            scores[item["key"]] = scores.get(item["key"], 0) + 1

    if not scores:
        return []

    matched = [item for item in library if scores.get(item["key"], 0) > 0]
    matched.sort(key=lambda item: scores[item["key"]], reverse=True)
    return matched[:MAX_CONTEXT_RECORDS]


def _format_record(r: dict) -> str:
    ids = ", ".join(f"{i['kind']} {i['label']}" for i in r["ids"]) or "no link"
    warn = ", ⚠ unverified" if r["warn"] else ""
    return (
        f'- "{r["title"]}" — {r["authors"]} '
        f"({r['date_found']}, {r['section']}, tracks {r['tracks']}{warn}) [{ids}]"
    )


def _format_zotero_item(item: dict) -> str:
    doi = f"DOI {item['doi']}" if item.get("doi") else "no DOI"
    return (
        f'- "{item["title"]}" — {item["creators"]} '
        f"({item.get('date', '')}, {item.get('item_type', '')}) [{doi}]"
    )


SYSTEM_PROMPT = (
    "You are the ResearchThis Portal's assistant. Answer the user's question "
    "using ONLY the items listed below -- each entry is a title, "
    "authors/creators, date and identifiers, not the paper's full text. "
    "There may be two sections: the curated findings feed, and (if present) "
    "the user's personal Zotero library -- when your answer draws on a "
    "Zotero library item, say so explicitly (e.g. 'in Zotero "
    "library'); items from the findings feed need no such qualifier. If "
    "neither section contains enough to answer, say so plainly instead of "
    "guessing or inventing details. Cite items by title. "
    "CRITICAL: every item in each section was matched by keyword and is "
    "shown to the user regardless of what you say, so you must explicitly "
    "account for every single one -- never silently omit an item from your "
    "answer while discussing that section. This matters especially for "
    "name matches: if two items share a surname but the full name, "
    "affiliation, or co-authors make clear they're different people, don't "
    "just drop the unrelated one -- say so explicitly (e.g. 'there's also "
    "a match under the same surname by a different, apparently unrelated "
    "author: ...') so the reader understands why it's listed but not "
    "counted with the others, instead of noticing an unexplained mismatch. "
    "IMPORTANT: you will be told which language to reply in -- use exactly "
    "that language for your entire answer, never a different one, even "
    "though the items' titles are in English."
)


def answer_question(
    question: str, records: list, zotero_library: list | None = None
) -> dict:
    """Answers a free-text question against the current findings feed and,
    if provided, the connected Zotero library.

    Returns {"answer": str, "sources": [...]} -- each source carries a
    "type" of "finding" or "zotero" so the caller/UI can tell them apart.
    "sources" omits the findings feed's fallback context (see
    _select_context) since showing unmatched filler as if it were relevant
    would be misleading; the Zotero library has no such fallback to begin
    with (see _select_zotero_context).
    Raises ChatError(status, message) for anything that should become an
    HTTP error response (bad input, missing config, upstream failure).
    """
    question = (question or "").strip()
    if not question:
        raise ChatError(400, "question must not be empty")
    if len(question) > MAX_QUESTION_LENGTH:
        raise ChatError(400, f"question exceeds {MAX_QUESTION_LENGTH} character limit")

    # Read lazily (not at module import time): app/server.py loads .env via
    # _load_dotenv() after its own imports run, so a module-level
    # os.environ.get() here could read before OPENAI_API_KEY is populated.
    api_key = os.environ.get("OPENAI_API_KEY")
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    if not api_key:
        raise ChatError(503, "chat is not configured -- set OPENAI_API_KEY")

    context, matched = _select_context(question, records)
    zotero_context = _select_zotero_context(question, zotero_library or [])

    context_block = (
        "\n".join(_format_record(r) for r in context) or "(no findings available)"
    )
    prompt_parts = [f"Findings feed:\n{context_block}"]
    if zotero_context:
        zotero_block = "\n".join(_format_zotero_item(i) for i in zotero_context)
        prompt_parts.append(f"Zotero library:\n{zotero_block}")
    prompt_parts.append(f"Question: {question}")
    prompt_parts.append(f"(Reply language: {_target_language(question)}.)")

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "\n\n".join(prompt_parts)},
        ],
        "temperature": 0,
        "max_tokens": MAX_ANSWER_TOKENS,
    }
    req = urllib.request.Request(
        OPENAI_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise ChatError(502, f"OpenAI request failed ({e.code}): {detail}") from e
    except Exception as e:
        raise ChatError(502, f"OpenAI request failed: {e}") from e

    try:
        answer = body["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError) as e:
        raise ChatError(502, "OpenAI response missing expected content") from e

    sources = []
    if matched:
        sources.extend(
            {
                "type": "finding",
                "title": r["title"],
                "raw_id": r["raw_id"],
                "url": r["ids"][0]["url"] if r["ids"] else None,
            }
            for r in context
        )
    sources.extend(
        {
            "type": "zotero",
            "title": i["title"],
            "key": i["key"],
            "url": f"https://doi.org/{i['doi']}" if i.get("doi") else None,
        }
        for i in zotero_context
    )
    return {"answer": answer, "sources": sources}
