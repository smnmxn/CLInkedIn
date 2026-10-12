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
from .inbox import InboxError, fetch_conversations, fetch_messages
from .invite import InviteError, parse_profile_url, send_invite
from .message import (
    MAX_MESSAGE_LEN,
    MessageError,
    resolve_message_target,
    send_message,
    start_conversation,
)

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


@mcp.tool()
def linkedin_list_conversations(limit: int = 20, unread_only: bool = False) -> dict:
    """List the authenticated user's LinkedIn message conversations, most recent first.

    Args:
        limit: Max conversations to return (default 20; at most ~100 per call).
        unread_only: Only return conversations with unread messages.

    Returns:
        {"ok": bool, "results": [{"thread_id", "url", "title", "participants",
        "unread_count", "read", "last_activity_at", "last_sender",
        "last_sender_is_me", "last_message"}], "error": str}.
    """
    try:
        client = _get_client()
    except NotAuthenticatedError as e:
        return {"ok": False, "error": f"not authenticated: {e}"}
    try:
        results = fetch_conversations(client, limit=max(1, limit), unread_only=unread_only)
    except (InboxError, InviteError) as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "results": results}


@mcp.tool()
def linkedin_read_conversation(target: str, limit: int = 20) -> dict:
    """Read the most recent messages in a LinkedIn conversation, oldest first.

    Args:
        target: Thread URL (https://www.linkedin.com/messaging/thread/2-.../),
            full urn:li:msg_conversation:(...) URN, or bare 2-... thread id.
        limit: Max most-recent messages to return (default 20).

    Returns:
        {"ok": bool, "results": [{"sent_at", "sender", "sender_url", "text"}], "error": str}.
    """
    try:
        client = _get_client()
    except NotAuthenticatedError as e:
        return {"ok": False, "error": f"not authenticated: {e}"}
    try:
        results = fetch_messages(client, target, limit=max(1, limit))
    except ValueError as e:
        return {"ok": False, "error": f"invalid target: {e}"}
    except (InboxError, InviteError) as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "results": results}


@mcp.tool()
def linkedin_send_message(target: str, message: str) -> dict:
    """Send a LinkedIn direct message.

    Args:
        target: A profile URL (https://www.linkedin.com/in/<slug>/) or
            urn:li:fsd_profile:ACoA... to message a person — replies in the existing
            thread with them, or starts a new conversation (1st-degree connections
            only). Or a thread URL / urn:li:msg_conversation:(...) / bare 2-... id
            to reply in that conversation.
        message: Message text (max 8000 chars).

    Returns:
        {"ok": bool, "thread_id": str, "new_conversation": bool, "error": str}.
    """
    text = (message or "").rstrip()
    if not text:
        return {"ok": False, "error": "message is empty"}
    if len(text) > MAX_MESSAGE_LEN:
        return {"ok": False, "error": f"message too long ({len(text)}/{MAX_MESSAGE_LEN})"}
    try:
        client = _get_client()
    except NotAuthenticatedError as e:
        return {"ok": False, "error": f"not authenticated: {e}"}
    try:
        dest = resolve_message_target(client, target)
    except ValueError as e:
        return {"ok": False, "error": f"invalid target: {e}"}
    except (InboxError, InviteError) as e:
        return {"ok": False, "error": str(e)}
    try:
        if dest["thread_id"]:
            send_message(client, dest["mailbox_urn"], dest["thread_id"], text)
            return {"ok": True, "thread_id": dest["thread_id"], "new_conversation": False}
        thread_id = start_conversation(client, dest["mailbox_urn"], dest["recipient_urn"], text)
    except MessageError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "thread_id": thread_id, "new_conversation": True}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
