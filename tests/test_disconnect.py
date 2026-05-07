import json

import pytest

from clinkedin.disconnect import DisconnectError, remove_connection


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


URN = "ACoAACX1hoMBvWqTY21JGe0z91mnmjmLy9Wen4w"


class _FakeClient:
    """Minimal stand-in for linkedin_api.Linkedin.

    `_fetch` powers `resolve_profile_urn` (the URN-resolution probe).
    `_post` powers the disconnect call itself.
    """

    def __init__(self, post_response: _FakeResponse):
        self._post_response = post_response
        self.fetch_calls: list[str] = []
        self.post_calls: list[dict] = []

    def _fetch(self, uri, **kwargs):
        self.fetch_calls.append(uri)
        return _FakeResponse(
            200,
            payload={"elements": [{"entityUrn": f"urn:li:fsd_profile:{URN}"}]},
        )

    def _post(self, uri, **kwargs):
        self.post_calls.append({"uri": uri, **kwargs})
        return self._post_response


def test_remove_connection_success():
    client = _FakeClient(_FakeResponse(200))
    remove_connection(client, "simon-foo")

    assert len(client.post_calls) == 1
    call = client.post_calls[0]
    assert call["uri"] == "/relationships/dash/memberRelationships"
    assert call["params"]["action"] == "removeFromMyConnections"
    assert "MemberRelationship" in call["params"]["decorationId"]
    assert json.loads(call["data"]) == {
        "connectionUrn": f"urn:li:fsd_connection:{URN}"
    }


def test_remove_connection_surfaces_status_and_body():
    client = _FakeClient(_FakeResponse(404, '{"message":"profile not found"}'))
    with pytest.raises(DisconnectError) as exc:
        remove_connection(client, "ghost-user")
    msg = str(exc.value)
    assert "ghost-user" in msg
    assert "HTTP 404" in msg
    assert "profile not found" in msg


def test_remove_connection_empty_body():
    client = _FakeClient(_FakeResponse(401, ""))
    with pytest.raises(DisconnectError, match="HTTP 401"):
        remove_connection(client, "simon-foo")
