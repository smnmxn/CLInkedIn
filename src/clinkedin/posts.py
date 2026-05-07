"""Search LinkedIn posts.

LinkedIn's Voyager API does not expose a working post-search endpoint to public
clients. We scrape the rendered web search-results page instead -- one
authenticated GET to /search/results/content/?keywords=..., then parse the
embedded React tree to extract post bodies + URNs.

This is fragile by nature. Every parsing step has a sentinel: if any of them
fails we raise ``PageShapeError`` (with a saved-HTML dump path) so a silent
zero-results never masks a parser break.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

_BASE_URL = "https://www.linkedin.com/search/results/content/"

# Sentinels we expect on a real, authenticated, populated search-results page.
_MIN_BYTES = 100_000
_REQUIRED_URN_RE = re.compile(r"urn:li:(?:activity|ugcPost):\d+")
_FEED_COMMENTARY_MARKER = "feed-commentary"
_NO_RESULTS_MARKERS = (
    "No results found",
    "Try different keywords",
)
_LOGIN_PATH_MARKERS = ("/login", "/uas/login", "/checkpoint/")

# Strings that look like prose but are LinkedIn UI boilerplate -- drop them.
_UI_PREFIXES = (
    "Something went wrong",
    "Sorry, unable to",
    "Sorry, we",
    "Unable to ",         # e.g. "Unable to subscribe, please try again."
    "We have encountered",
    "We're sorry",
    "Are you sure you",
    "Stop seeing activity",
    "Create a new post",  # repost-with-thoughts menu copy
    "Repost with your",   # repost menu header
    "Instantly bring ",   # repost-as-is menu copy
)

# Parser regexes
# Post text leaf:  "children":["<actual text>"]  (escaped to \"children\":[\"...\"])
# Captured text can contain anything except an unescaped \"]; the lazy quantifier
# stops at the first such terminator. First captured char must not be $ (which
# marks a React component reference).
_TEXT_LEAF = re.compile(
    r'\\"children\\":\['
    r'\\"([^$"\\].+?)\\"\]',
    re.DOTALL,
)
# Multi-paragraph chunks split text across sub-arrays of the form:
#   [null,"<paragraph>"] or [<components>,"<paragraph>"]
# Match: any "<text>"] where <text> is prose (>= 15 chars, has space, starts
# with letter/digit so we skip component refs starting with $).
_PROSE_RUN = re.compile(
    r'\\"([A-Za-z0-9][^"\\]{15,}?)\\"\]',
    re.DOTALL,
)
# Reference inside feed-commentary subtree to a hoisted chunk: "children":"$L74"
_CHUNK_REF = re.compile(r'children\\":\\"\$L([0-9a-f]+)\\"')
# Hoisted-chunk definition. Either "\n74:[" (within a JS string) or "\",\"74:[" (start of new array element).
_URN_RE = re.compile(r"urn:li:(?:activity|ugcPost):\d+")

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


class PageShapeError(RuntimeError):
    """The fetched page didn't match the shape this parser expects.

    Carries a path to a saved HTML dump so the user can attach it when
    reporting a parser break.
    """

    def __init__(self, reason: str, dump_path: Path | None = None) -> None:
        self.reason = reason
        self.dump_path = dump_path
        msg = f"LinkedIn search-results page shape unrecognised: {reason}"
        if dump_path is not None:
            msg += f"\nFull response saved to: {dump_path}"
        super().__init__(msg)


class AuthExpiredError(RuntimeError):
    """Session cookie was rejected (redirect to login or 401/403)."""


def search_posts(client, query: str, limit: int | None = None) -> list[dict[str, Any]]:
    """Fetch LinkedIn's content search-results page and extract post bodies."""
    html = _fetch_page(client, query)
    posts = _parse_posts(html)
    if limit is not None and limit >= 0:
        posts = posts[:limit]
    return posts


def _fetch_page(client, query: str) -> str:
    # origin=CLUSTER_EXPANSION mirrors the URL LinkedIn navigates to when the
    # user clicks "Show More" on the initial results -- it returns a longer
    # list of posts than GLOBAL_SEARCH_HEADER (the default search-bar entry).
    url = f"{_BASE_URL}?keywords={quote(query)}&origin=CLUSTER_EXPANSION"
    session = client.client.session  # linkedin-api wraps a requests.Session here
    res = session.get(url, headers=_DEFAULT_HEADERS, allow_redirects=True)
    return _validate_response(res)


def _validate_response(res) -> str:
    """Run sentinels on a response. Return body text if all pass, else raise."""
    final_url = getattr(res, "url", "")
    status = res.status_code

    if status in (401, 403):
        raise AuthExpiredError(
            f"LinkedIn rejected the session (HTTP {status}). "
            "Run 'clinkedin login' to re-authenticate."
        )
    if any(marker in final_url for marker in _LOGIN_PATH_MARKERS):
        raise AuthExpiredError(
            "LinkedIn redirected the request to a login/checkpoint page. "
            "Run 'clinkedin login' to re-authenticate."
        )
    if status != 200:
        dump = _dump_response(res)
        raise PageShapeError(f"HTTP {status}", dump)

    text = res.text
    if len(text) < _MIN_BYTES:
        dump = _dump_response(res)
        raise PageShapeError(
            f"response too small ({len(text)} bytes < {_MIN_BYTES}) "
            "-- likely an interstitial / login wall",
            dump,
        )
    return text


def _dump_response(res) -> Path:
    """Save the response body so the user can attach it when reporting."""
    path = Path(f"/tmp/clinkedin_search_failed_{int(time.time())}.html")
    try:
        path.write_text(getattr(res, "text", ""))
    except Exception:
        pass
    return path


def _parse_posts(html: str) -> list[dict[str, Any]]:
    """Parse a known-shape response. Raise PageShapeError if shape is foreign."""
    if not _URN_RE.search(html):
        raise PageShapeError(
            "no post URNs found in response -- page shape may have changed",
            _dump_text(html),
        )
    if _FEED_COMMENTARY_MARKER not in html:
        raise PageShapeError(
            "no 'feed-commentary' markers in response -- page structure may have changed",
            _dump_text(html),
        )

    posts = _extract_post_bodies(html)
    if not posts:
        # Distinguish "genuine zero" from "parser broken on a recognised page".
        if any(marker in html for marker in _NO_RESULTS_MARKERS):
            return []
        raise PageShapeError(
            "page structure recognised but parser extracted zero posts "
            "-- parser likely needs updating",
            _dump_text(html),
        )
    return posts


def _dump_text(html: str) -> Path:
    path = Path(f"/tmp/clinkedin_search_failed_{int(time.time())}.html")
    try:
        path.write_text(html)
    except Exception:
        pass
    return path


# How far past a feed-commentary marker to look for an inline post body.
_INLINE_LOOKAHEAD = 20_000
# How far into a hoisted chunk to look for the post body text.
_CHUNK_SCAN = 8_000


def _extract_post_bodies(html: str) -> list[dict[str, Any]]:
    """Each post is anchored at a feed-commentary marker. From there the body
    is either inline (within ~20KB after) or hoisted into a separate streamed
    chunk referenced as ``children":"$L<id>"``.

    For each marker we pick the best body text from whichever shape applies.
    """
    seen_keys: set[str] = set()
    out: list[dict[str, Any]] = []
    for m in re.finditer(_FEED_COMMENTARY_MARKER, html):
        marker_off = m.start()
        # 1. Hoisted shape: look in the next ~500 bytes for $L<id>
        marker_window = html[marker_off : marker_off + 500]
        ref_m = _CHUNK_REF.search(marker_window)
        if ref_m:
            text = _extract_from_chunk(html, ref_m.group(1))
            anchor_off = marker_off  # URN association still uses the marker
        else:
            # 2. Inline shape: scan up to _INLINE_LOOKAHEAD bytes for body
            text, anchor_off = _extract_inline_body(html, marker_off)

        if not text:
            continue
        key = text[:80]
        if key in seen_keys:
            continue
        seen_keys.add(key)
        urn = _nearest_urn(html, anchor_off)
        out.append(
            {
                "text": text,
                "url": (
                    f"https://www.linkedin.com/feed/update/{urn}/" if urn else None
                ),
                "urn": urn,
            }
        )
    return out


def _extract_inline_body(html: str, marker_off: int) -> tuple[str | None, int]:
    """Find the best post body in a 20KB window after a feed-commentary marker.

    Returns (decoded_text, offset) -- offset is where the body was found, used
    for URN association.
    """
    end = min(len(html), marker_off + _INLINE_LOOKAHEAD)
    best_text: str | None = None
    best_off = marker_off
    for tm in _TEXT_LEAF.finditer(html, marker_off, end):
        text = _decode(tm.group(1))
        if not _looks_like_body(text):
            continue
        if best_text is None or len(text) > len(best_text):
            best_text = text
            best_off = tm.start()
    return best_text, best_off


def _extract_from_chunk(html: str, chunk_id: str) -> str | None:
    """Find a hoisted chunk's definition and pull its post-body text.

    Chunks are separated either by `\\n<id>:[` (within a JS string element) or
    by `","<id>:[` (start of a new element of the rehydration array).

    Two text shapes inside chunks:
    - Single-paragraph: ``"children":["<text>"]`` -- one big text leaf.
    - Multi-paragraph: ``[null,"<para>"]`` repeated, with <br> components
      between paragraphs. We concat all prose runs in the chunk.
    """
    sep = re.compile(rf'(?:\\n|"\s*,\s*"){re.escape(chunk_id)}:\[')
    m = sep.search(html)
    if not m:
        return None
    start = m.end()
    end = min(len(html), start + _CHUNK_SCAN)
    chunk = html[start:end]

    # Try single-paragraph first -- if we find a long, prose-y leaf, prefer it.
    best_leaf: str | None = None
    for tm in _TEXT_LEAF.finditer(chunk):
        text = _decode(tm.group(1))
        if _looks_like_body(text) and (best_leaf is None or len(text) > len(best_leaf)):
            best_leaf = text
    if best_leaf is not None:
        return best_leaf

    # Multi-paragraph fallback: gather all prose runs.
    parts: list[str] = []
    seen: set[str] = set()
    for tm in _PROSE_RUN.finditer(chunk):
        text = _decode(tm.group(1)).strip()
        if not _is_prose_run(text):
            continue
        key = text[:80]
        if key in seen:
            continue
        seen.add(key)
        parts.append(text)
    if not parts:
        return None
    combined = "\n".join(parts)
    return combined if _looks_like_body(combined) else None


def _is_prose_run(text: str) -> bool:
    """Looser filter for individual paragraphs in multi-paragraph chunks."""
    if len(text) < 15 or " " not in text:
        return False
    if any(text.startswith(p) for p in _UI_PREFIXES):
        return False
    # Reject obvious tracking IDs / class names: contain only alphanumerics
    # and dashes/underscores with no sentence punctuation.
    if re.fullmatch(r"[A-Za-z0-9_\-]+", text):
        return False
    return True


def _decode(raw: str) -> str:
    """Unescape JSON-in-JS escapes. Run twice -- response is double-escaped."""
    for _ in range(2):
        out: list[str] = []
        i = 0
        while i < len(raw):
            ch = raw[i]
            if ch == "\\" and i + 1 < len(raw):
                nxt = raw[i + 1]
                if nxt == "n":
                    out.append("\n"); i += 2; continue
                if nxt == "t":
                    out.append("\t"); i += 2; continue
                if nxt == "r":
                    out.append("\r"); i += 2; continue
                if nxt == '"':
                    out.append('"'); i += 2; continue
                if nxt == "\\":
                    out.append("\\"); i += 2; continue
            out.append(ch)
            i += 1
        raw = "".join(out)
    return raw


def _looks_like_body(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < 30:
        return False
    # Spaces are a weak signal for "this is prose not a label", but short post
    # titles ("OpenAI is officially polyamorous") have only 3 spaces. Be lax.
    if stripped.count(" ") < 2:
        return False
    if any(stripped.startswith(p) for p in _UI_PREFIXES):
        return False
    return True


def _nearest_urn(html: str, offset: int, before: int = 30_000, after: int = 5_000) -> str | None:
    fwd = _URN_RE.search(html, offset, min(len(html), offset + after))
    if fwd:
        return fwd.group(0)
    start = max(0, offset - before)
    bwd = list(_URN_RE.finditer(html, start, offset))
    if bwd:
        return bwd[-1].group(0)
    return None


def format_json(results: list[dict[str, Any]]) -> str:
    return json.dumps(results, indent=2, ensure_ascii=False)


def format_table(results: list[dict[str, Any]]) -> str:
    if not results:
        return "No posts found."
    lines = []
    for r in results:
        text = (r.get("text") or "").replace("\n", " ").strip()
        if len(text) > 120:
            text = text[:119].rstrip() + "…"
        url = r.get("url") or ""
        parts = [p for p in (text, url) if p]
        lines.append(" · ".join(parts))
    return "\n".join(lines)
