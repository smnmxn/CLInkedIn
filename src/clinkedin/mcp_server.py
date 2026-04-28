"""Stdio MCP server exposing a single tool: linkedin_send_connection_request."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .auth import NotAuthenticatedError
from .client import make_client
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


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
