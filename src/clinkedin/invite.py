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


_MEMBER_URN_NUMERIC_RE = re.compile(r"urn:li:member:(\d+)")


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


def resolve_profile_identity(client, identifier: str) -> dict:
    """Resolve a profile to all three identity forms in one Voyager call.

    `identifier` may be a vanity slug ("alex-fairweather-68778760") OR an ACoA
    URN ("ACoA..."). Returns {"acoa": ACoA, "numeric_id": NNN, "vanity": str}.

    The numeric member ID comes from `objectUrn` (urn:li:member:NNN) — the
    SDUI follow/unfollow endpoint requires it (the legacy /feed/follows path
    accepted ACoA but is now 400-deprecated).
    """
    res = client._fetch(
        f"/voyagerIdentityDashProfiles?q=memberIdentity&memberIdentity={identifier}"
    )
    if res.status_code != 200:
        raise InviteError(
            f"Profile lookup failed for {identifier} (HTTP {res.status_code})."
        )
    try:
        data = res.json()
    except ValueError as e:
        raise InviteError(f"Profile lookup returned non-JSON for {identifier}.") from e
    elements = data.get("elements") or []
    if not elements:
        raise InviteError(f"No profile found for {identifier}.")
    elem = elements[0]
    blob = json.dumps(elem)
    acoa_m = _URN_RE.search(blob)
    numeric_m = _MEMBER_URN_NUMERIC_RE.search(blob)
    if not acoa_m or not numeric_m:
        raise InviteError(
            f"Could not extract identity for {identifier} "
            f"(acoa={bool(acoa_m)}, numeric={bool(numeric_m)})."
        )
    return {
        "acoa": acoa_m.group(0),
        "numeric_id": numeric_m.group(1),
        "vanity": elem.get("publicIdentifier") or "",
    }


def send_invite(client, public_id: str, message: str = "") -> None:
    """Send a connection request. Raises InviteError on failure."""
    urn = resolve_profile_urn(client, public_id)
    errored = client.add_connection(public_id, message=message, profile_urn=urn)
    if errored:
        raise InviteError(
            "Invite failed — you may already be connected, have a pending invite, "
            "or be rate-limited by LinkedIn."
        )
