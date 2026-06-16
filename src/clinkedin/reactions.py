"""List the people who reacted to (liked) or commented on a LinkedIn post.

Both the reaction list and the comment list come back from LinkedIn's modern
"social" Dash endpoints, and each element carries the actor's profile (name,
headline, and a profile URL in ACoA-URN form — the same convention as
`clinkedin following`, which LinkedIn redirects to the canonical vanity profile
in a browser).

The post URL is parsed with `comment.parse_post_url` to recover the
`urn:li:activity:<id>` thread URN that both endpoints key on.

ENDPOINT STATUS — pending DevTools capture.
  The exact request paths, the `decorationId`/`queryId` values, and the
  response field paths below are placeholders modelled on the documented modern
  Voyager shape. LinkedIn rotates these on bundle releases, so per the repo's
  convention (see post.py) the live fetch is GATED behind `_ENDPOINT_CONFIRMED`
  until a real capture lands — blind-probing Voyager risks 401-locking the
  session for hours. To unblock: open the post in Chrome, DevTools -> Network,
  expand the reaction list and the comment list, "Copy as cURL" each request,
  and paste them back. One edit then locks in the real path + field paths and
  flips `_ENDPOINT_CONFIRMED` to True.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

from .comment import parse_post_url


class ReactionsError(Exception):
    pass


# Flip to True once the request paths and response field paths below have been
# verified against a real DevTools capture. While False, fetch_* raise a clear
# "pending capture" error instead of probing Voyager (which could 401-lock the
# session). See the module docstring.
_ENDPOINT_CONFIRMED = False

# Voyager caps a single page; we page until exhausted or the caller's limit.
_PAGE_SIZE = 50
# Hard safety cap so a viral post with 50k reactions can't fan out into
# hundreds of Voyager calls and burst-throttle the session.
_MAX_TOTAL = 1000


def _activity_urn(url: str) -> str:
    """Resolve a post URL/URN to its urn:li:activity:<id> thread URN.

    Raises ReactionsError if the URL only yields a share id (we'd need an extra
    HTML fetch to resolve share -> activity; out of scope for v1)."""
    try:
        parsed = parse_post_url(url)
    except ValueError as e:
        raise ReactionsError(str(e)) from e
    activity_id = parsed.get("activity_id")
    if not activity_id:
        raise ReactionsError(
            f"Could not resolve an activity id from {url!r}. Pass a "
            "/feed/update/urn:li:activity:N/ URL or a bare urn:li:activity:N "
            "(a bare share id isn't enough to list engagement)."
        )
    return f"urn:li:activity:{activity_id}"


def _guard() -> None:
    if not _ENDPOINT_CONFIRMED:
        raise ReactionsError(
            "`clinkedin reactions` is pending a DevTools capture of LinkedIn's "
            "reactions/comments endpoints — the live fetch is disabled to avoid "
            "401-locking your session on an unverified request. See "
            "src/clinkedin/reactions.py for capture instructions. (--dry-run "
            "works now.)"
        )


def _extract_actor(node: dict[str, Any]) -> dict[str, Any] | None:
    """Pull name/headline/url/urn out of a reactor- or commenter-shaped node.

    Field paths below are best-effort against the documented Dash shape and are
    confirmed/adjusted from the capture. Returns None if no actor is present."""
    actor = (
        node.get("actor")
        or node.get("reactor")
        or node.get("commenter")
        or node.get("commenterProfile")
    )
    if not isinstance(actor, dict):
        return None

    def _txt(d: Any, *path: str) -> str:
        cur = d
        for p in path:
            if not isinstance(cur, dict) or p not in cur:
                return ""
            cur = cur[p]
        return cur if isinstance(cur, str) else (cur.get("text", "") if isinstance(cur, dict) else "")

    name = _txt(actor, "title", "text") or _txt(actor, "name", "text") or actor.get("name") or ""
    headline = _txt(actor, "subtitle", "text") or _txt(actor, "headline") or ""
    urn = actor.get("urn") or actor.get("entityUrn") or actor.get("profileUrn") or ""
    nav = actor.get("navigationUrl") or actor.get("url") or ""
    if not nav and urn:
        # urn:li:fsd_profile:ACoAxxxx -> /in/ACoAxxxx/ (redirects to vanity).
        member = urn.rsplit(":", 1)[-1]
        nav = f"https://www.linkedin.com/in/{member}/"
    return {
        "name": (name or "").strip(),
        "headline": (headline or "").strip(),
        "url": nav,
        "urn": urn,
    }


def fetch_reactions(client, url: str, *, limit: int, offset: int) -> list[dict[str, Any]]:
    """Return [{name, headline, url, urn, reaction_type}] for a post's reactors."""
    _guard()
    thread = _activity_urn(url)
    out: list[dict[str, Any]] = []
    start = max(0, offset)
    cap = min(limit, _MAX_TOTAL) if limit else _MAX_TOTAL
    while len(out) < cap:
        count = min(_PAGE_SIZE, cap - len(out))
        # PLACEHOLDER PATH — confirm from capture.
        path = (
            f"/voyagerSocialDashReactions?q=reactionType&start={start}"
            f"&count={count}&threadUrn={quote(thread, safe='')}"
        )
        res = client._fetch(path)
        if res.status_code != 200:
            raise ReactionsError(
                f"Reactions fetch failed (HTTP {res.status_code}). The endpoint "
                "shape may have rotated — re-capture from DevTools."
            )
        elements = (res.json() or {}).get("elements") or []
        if not elements:
            break
        for el in elements:
            actor = _extract_actor(el)
            if actor is None:
                continue
            actor["reaction_type"] = el.get("reactionType") or "LIKE"
            out.append(actor)
        start += len(elements)
        if len(elements) < count:
            break
    return out[:cap]


def fetch_comments(client, url: str, *, limit: int, offset: int) -> list[dict[str, Any]]:
    """Return [{name, headline, url, urn, comment}] for a post's commenters."""
    _guard()
    thread = _activity_urn(url)
    out: list[dict[str, Any]] = []
    start = max(0, offset)
    cap = min(limit, _MAX_TOTAL) if limit else _MAX_TOTAL
    while len(out) < cap:
        count = min(_PAGE_SIZE, cap - len(out))
        # PLACEHOLDER PATH — confirm from capture.
        path = (
            f"/voyagerSocialDashComments?q=comments&sortOrder=RELEVANCE"
            f"&start={start}&count={count}&socialDetailUrn={quote(thread, safe='')}"
        )
        res = client._fetch(path)
        if res.status_code != 200:
            raise ReactionsError(
                f"Comments fetch failed (HTTP {res.status_code}). The endpoint "
                "shape may have rotated — re-capture from DevTools."
            )
        elements = (res.json() or {}).get("elements") or []
        if not elements:
            break
        for el in elements:
            actor = _extract_actor(el)
            if actor is None:
                continue
            body = el.get("commentary", {}) if isinstance(el.get("commentary"), dict) else {}
            actor["comment"] = (body.get("text") or el.get("comment") or "").strip()
            out.append(actor)
        start += len(elements)
        if len(elements) < count:
            break
    return out[:cap]


def format_text(reactors: list[dict], commenters: list[dict]) -> str:
    lines: list[str] = []
    if reactors:
        lines.append(f"Reactions ({len(reactors)}):")
        for r in reactors:
            lines.append(
                " · ".join(
                    [
                        r.get("name") or "(unknown)",
                        r.get("reaction_type") or "LIKE",
                        (r.get("headline") or "").replace("\n", " "),
                        r.get("url") or "",
                    ]
                )
            )
    if commenters:
        if lines:
            lines.append("")
        lines.append(f"Comments ({len(commenters)}):")
        for c in commenters:
            text = (c.get("comment") or "").replace("\n", " ")
            if len(text) > 80:
                text = text[:79].rstrip() + "…"
            lines.append(
                " · ".join(
                    [
                        c.get("name") or "(unknown)",
                        f'"{text}"' if text else "(no text)",
                        c.get("url") or "",
                    ]
                )
            )
    return "\n".join(lines)


def format_json(reactors: list[dict], commenters: list[dict]) -> str:
    return json.dumps(
        {"reactions": reactors, "comments": commenters},
        indent=2,
        ensure_ascii=False,
    )
