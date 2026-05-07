"""Remove an existing LinkedIn connection."""

from __future__ import annotations

import json

from .invite import resolve_profile_urn


class DisconnectError(Exception):
    pass


# WHY this doesn't call linkedin_api's `client.remove_connection`: that wrapper
# hits `/identity/profiles/<id>/profileActions?action=disconnect`, which LinkedIn
# retired (returns HTTP 410). The modern Voyager endpoint takes a connectionUrn
# payload and lives at `/relationships/dash/memberRelationships`.
def remove_connection(client, public_id: str) -> None:
    """Disconnect from a 1st-degree connection. Raises DisconnectError on failure."""
    member_urn = resolve_profile_urn(client, public_id)
    payload = {"connectionUrn": f"urn:li:fsd_connection:{member_urn}"}
    res = client._post(
        "/relationships/dash/memberRelationships",
        params={
            "action": "removeFromMyConnections",
            "decorationId": "com.linkedin.voyager.dash.deco.relationships.MemberRelationship-34",
        },
        data=json.dumps(payload),
        headers={"accept": "application/vnd.linkedin.normalized+json+2.1"},
    )
    if res.status_code != 200:
        body = (res.text or "").strip().replace("\n", " ")[:200]
        raise DisconnectError(
            f"Disconnect failed for {public_id} (HTTP {res.status_code}): {body or '<empty body>'}"
        )
