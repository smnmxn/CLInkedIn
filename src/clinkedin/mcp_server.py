"""Stdio MCP server exposing clinkedin's write actions and listings as tools."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .auth import NotAuthenticatedError
from .client import make_client
from .follow import (
    FollowError,
    follow_company,
    follow_member,
    list_following,
    parse_follow_url,
    unfollow_company,
    unfollow_member,
)
from .invite import InviteError, parse_profile_url, send_invite

mcp = FastMCP("clinkedin")

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = make_client()
    return _client


@mcp.tool()
def linkedin_send_connection_request(profile_url: str, message: str = "") -> dict:
    """Send a LinkedIn connection request to the given profile.

    Args:
        profile_url: Full LinkedIn profile URL (e.g. https://www.linkedin.com/in/<slug>/).
        message: Optional personal note, max 300 chars. Free accounts are limited to
            ~5 notes/week and ~100-200 total invites/week.

    Returns:
        A dict with "ok" (bool), "public_id" (str, when resolvable), and "error" (str, on failure).
    """
    if len(message) > 300:
        return {"ok": False, "error": f"message too long ({len(message)}/300)"}
    try:
        public_id = parse_profile_url(profile_url)
    except ValueError as e:
        return {"ok": False, "error": f"invalid URL: {e}"}
    try:
        client = _get_client()
    except NotAuthenticatedError as e:
        return {"ok": False, "public_id": public_id, "error": f"not authenticated: {e}"}
    try:
        send_invite(client, public_id, message=message)
    except InviteError as e:
        return {"ok": False, "public_id": public_id, "error": str(e)}
    return {"ok": True, "public_id": public_id, "message_sent": bool(message)}


@mcp.tool()
def linkedin_follow(profile_url: str) -> dict:
    """Follow a LinkedIn member or company (no connection request needed).

    Args:
        profile_url: A /in/<slug> member URL or /company/<slug> company URL.

    Returns:
        {"ok": bool, "kind": "member"|"company", "slug": str, "error": str}.
    """
    try:
        kind, slug = parse_follow_url(profile_url)
    except ValueError as e:
        return {"ok": False, "error": f"invalid URL: {e}"}
    try:
        client = _get_client()
    except NotAuthenticatedError as e:
        return {"ok": False, "kind": kind, "slug": slug, "error": f"not authenticated: {e}"}
    try:
        (follow_member if kind == "member" else follow_company)(client, slug)
    except FollowError as e:
        return {"ok": False, "kind": kind, "slug": slug, "error": str(e)}
    return {"ok": True, "kind": kind, "slug": slug}


@mcp.tool()
def linkedin_unfollow(profile_url: str) -> dict:
    """Unfollow a LinkedIn member or company.

    Args:
        profile_url: A /in/<slug> member URL or /company/<slug> company URL.

    Returns:
        {"ok": bool, "kind": "member"|"company", "slug": str, "error": str}.
    """
    try:
        kind, slug = parse_follow_url(profile_url)
    except ValueError as e:
        return {"ok": False, "error": f"invalid URL: {e}"}
    try:
        client = _get_client()
    except NotAuthenticatedError as e:
        return {"ok": False, "kind": kind, "slug": slug, "error": f"not authenticated: {e}"}
    try:
        (unfollow_member if kind == "member" else unfollow_company)(client, slug)
    except FollowError as e:
        return {"ok": False, "kind": kind, "slug": slug, "error": str(e)}
    return {"ok": True, "kind": kind, "slug": slug}


@mcp.tool()
def linkedin_list_following(limit: int | None = None, offset: int = 0) -> dict:
    """List members and companies the authenticated user follows.

    Args:
        limit: Optional cap on the number of results.
        offset: Skip the first N results (for pagination).

    Returns:
        {"ok": bool, "results": [{"name", "kind", "public_id", "headline", "urn"}], "error": str}.
    """
    try:
        client = _get_client()
    except NotAuthenticatedError as e:
        return {"ok": False, "error": f"not authenticated: {e}"}
    try:
        results = list_following(client, limit=limit, offset=offset)
    except FollowError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "results": results}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
