"""Fetch and format the logged-in user's home feed."""

from __future__ import annotations

import json
from typing import Any

from .profile import _extract_post_fields, _walk


class FeedError(Exception):
    pass


# LinkedIn returns HTTP 400 for count > 100.
_PAGE_MAX = 100
# Promoted posts are dropped after the fetch, so over-ask a little to usually
# fill --limit in a single request.
_AD_PAD = 5
# Hard stop on round trips per invocation; bursts of Voyager calls can lock the
# session.
_MAX_PAGES = 5


def _is_sponsored(element: dict[str, Any]) -> bool:
    if "sponsoredContent" in (element.get("entityUrn") or ""):
        return True
    return bool(_walk(element, "updateMetadata", "trackingData", "sponsoredTracking"))


def fetch_feed(client, limit: int = 10) -> list[dict[str, Any]]:
    """Fetch the home feed via Voyager, newest activity first.

    Hits /feed/updatesV2 with q=chronFeed (the "Recent" sort; the same query
    linkedin-api uses) and returns the raw UpdateV2 elements, which have the
    same top-level shape as member posts so profile's age filter applies
    unchanged. Promoted posts are dropped.

    An empty list is a valid result (nothing in the feed).
    """
    posts: list[dict[str, Any]] = []
    seen: set[str] = set()
    start = 0
    for _ in range(_MAX_PAGES):
        if len(posts) >= limit:
            break
        params = {
            "q": "chronFeed",
            "count": min(limit - len(posts) + _AD_PAD, _PAGE_MAX),
            "start": start,
        }
        res = client._fetch("/feed/updatesV2", params=params)
        if res.status_code != 200:
            raise FeedError(
                f"Feed fetch failed (HTTP {res.status_code}). "
                "Your session may be expired or rate-limited; try 'clinkedin login'."
            )
        try:
            data = res.json()
        except ValueError as e:
            raise FeedError("Feed fetch returned non-JSON.") from e
        if not isinstance(data, dict):
            raise FeedError("Feed fetch returned unexpected shape.")
        elements = data.get("elements") or []
        if not isinstance(elements, list):
            raise FeedError("Feed fetch returned unexpected elements.")
        if not elements:
            break
        start += len(elements)
        for e in elements:
            if not isinstance(e, dict) or _is_sponsored(e):
                continue
            # Paging by offset over a live feed can repeat an update.
            urn = _walk(e, "updateMetadata", "urn") or e.get("entityUrn") or ""
            if urn and urn in seen:
                continue
            seen.add(urn)
            posts.append(e)
    return posts[:limit]


def _feed_fields(post: dict[str, Any]) -> dict[str, Any]:
    """Normalize a raw feed element into the row the formatters emit."""
    f = _extract_post_fields(post)
    target = _walk(post, "actor", "navigationContext", "actionTarget") or ""
    return {
        "date": f["date"],
        "author": _walk(post, "actor", "name", "text") or "",
        "author_headline": _walk(post, "actor", "description", "text") or "",
        # The nav target carries tracking params (miniProfileUrn=...); drop them.
        "author_url": target.split("?")[0],
        # Why it's in the feed, e.g. "Jane Doe likes this"; empty for posts from
        # people/companies the user follows directly.
        "reason": _walk(post, "header", "text", "text") or "",
        "text": f["text"],
        "like_count": f["like_count"],
        "comment_count": f["comment_count"],
        "urn": f["urn"],
        "url": f["url"],
    }


def format_text(posts: list[dict[str, Any]], *, full: bool = False) -> str:
    """Render feed posts as text.

    full=False (default): one line per post, text truncated to 120 chars.
    full=True: multi-line block per post — metadata header, the reason it's in
    the feed (if any), full text below, blank line between posts.
    """
    if not posts:
        return ""
    if full:
        blocks = []
        for post in posts:
            f = _feed_fields(post)
            header = " · ".join(
                [
                    f["date"] or "?",
                    f["author"] or "(unknown)",
                    f"{f['like_count']} likes",
                    f"{f['comment_count']} comments",
                    f["url"] or "(no url)",
                ]
            )
            lines = [header]
            if f["reason"]:
                lines.append(f"({f['reason']})")
            lines.append((f["text"] or "").strip() or "(no text)")
            blocks.append("\n".join(lines))
        return "\n\n---\n\n".join(blocks)

    lines = []
    for post in posts:
        f = _feed_fields(post)
        text = (f["text"] or "").replace("\n", " ").strip() or "(no text)"
        if len(text) > 120:
            text = text[:119].rstrip() + "…"
        parts = [
            f["date"] or "?",
            f["author"] or "(unknown)",
            f'"{text}"',
            f"{f['like_count']} likes",
            f"{f['comment_count']} comments",
            f["url"] or "(no url)",
        ]
        lines.append(" · ".join(parts))
    return "\n".join(lines)


def format_json(posts: list[dict[str, Any]]) -> str:
    # Normalized rows rather than raw Voyager elements: a raw feed element runs
    # to ~40KB of rendering/tracking metadata per post.
    return json.dumps([_feed_fields(p) for p in posts], indent=2, ensure_ascii=False)
