"""Send a LinkedIn connection request."""

from __future__ import annotations

import json
import re
from urllib.parse import urlparse


class InviteError(Exception):
    pass


_SLUG_RE = re.compile(r"^/(?:mwlite/)?in/([^/]+)/?$")
_URN_RE = re.compile(r"ACoA[A-Za-z0-9_-]+")


def parse_profile_url(url: str) -> str:
    """Extract the public_id slug from a LinkedIn profile URL."""
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if not (host == "linkedin.com" or host.endswith(".linkedin.com")):
        raise ValueError(f"Not a LinkedIn URL: {url}")
    m = _SLUG_RE.match(parsed.path.rstrip())
    if not m:
        raise ValueError(f"Not a LinkedIn profile URL (expected /in/<slug>): {url}")
    return m.group(1)


def resolve_profile_urn(client, public_id: str) -> str:
    """Look up the ACoA... profile URN for a public_id via the Dash profiles endpoint."""
    res = client._fetch(
        f"/voyagerIdentityDashProfiles?q=memberIdentity&memberIdentity={public_id}"
    )
    if res.status_code != 200:
        raise InviteError(
            f"Profile lookup failed for {public_id} (HTTP {res.status_code})."
        )
    try:
        data = res.json()
    except ValueError:
        raise InviteError(f"Profile lookup returned non-JSON for {public_id}.")
    elements = data.get("elements") or []
    if not elements:
        raise InviteError(f"No profile found for {public_id}.")
    m = _URN_RE.search(json.dumps(elements[0]))
    if not m:
        raise InviteError(f"Could not extract profile URN for {public_id}.")
    return m.group(0)


def send_invite(client, public_id: str, message: str = "") -> None:
    """Send a connection request. Raises InviteError on failure."""
    urn = resolve_profile_urn(client, public_id)
    errored = client.add_connection(public_id, message=message, profile_urn=urn)
    if errored:
        raise InviteError(
            "Invite failed — you may already be connected, have a pending invite, "
            "or be rate-limited by LinkedIn."
        )
