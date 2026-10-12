import json

import pytest

from clinkedin.message import (
    MAX_MESSAGE_LEN,
    MessageError,
    build_conversation_urn,
    parse_thread_target,
    send_message,
)


SELF_URN_ACOA = "ACoAAAA63I8BlgvjEFmtc5IYoErxz7GVcaSBfLg"
SELF_FSD_URN = f"urn:li:fsd_profile:{SELF_URN_ACOA}"
THREAD_ID = "2-ZDlmNjE3YjUtZjU4ZS00NzZmLWIyOWMtMWFjMDU4OGY1ZjU1XzEwMA=="
FULL_CONV_URN = f"urn:li:msg_conversation:({SELF_FSD_URN},{THREAD_ID})"


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


class _FakeClient:
    """Minimal stand-in. `_fetch` only used for self-URN lookups."""

    def __init__(self, post_response: _FakeResponse | None = None):
        self._post_response = post_response or _FakeResponse(200)
        self.fetch_calls: list[str] = []
        self.post_calls: list[dict] = []

    def _fetch(self, uri, **kwargs):
        self.fetch_calls.append(uri)
        return _FakeResponse(
            200,
            payload={"elements": [{"entityUrn": SELF_FSD_URN}]},
        )

    def _post(self, uri, **kwargs):
        self.post_calls.append({"uri": uri, **kwargs})
        return self._post_response


def test_parse_full_urn():
    client = _FakeClient()
    mailbox, thread = parse_thread_target(client, FULL_CONV_URN)
    assert mailbox == SELF_FSD_URN
    assert thread == THREAD_ID
    # No /me lookup needed when caller supplies the full URN.
    assert client.fetch_calls == []


def test_parse_thread_url_uses_self_urn():
    client = _FakeClient()
    url = f"https://www.linkedin.com/messaging/thread/{THREAD_ID}/"
    mailbox, thread = parse_thread_target(client, url)
    assert mailbox == SELF_FSD_URN
    assert thread == THREAD_ID
    assert any("memberIdentity=me" in c for c in client.fetch_calls)


def test_parse_bare_thread_id():
    client = _FakeClient()
    mailbox, thread = parse_thread_target(client, THREAD_ID)
    assert mailbox == SELF_FSD_URN
    assert thread == THREAD_ID


def test_parse_strips_trailing_slash_and_whitespace():
    client = _FakeClient()
    url = f"  https://www.linkedin.com/messaging/thread/{THREAD_ID}/  "
    _, thread = parse_thread_target(client, url)
    assert thread == THREAD_ID


def test_parse_rejects_non_linkedin_url():
    with pytest.raises(ValueError, match="Not a recognised conversation target"):
        parse_thread_target(_FakeClient(), "https://example.com/messaging/thread/2-foo/")


def test_parse_rejects_empty():
    with pytest.raises(ValueError, match="Empty"):
        parse_thread_target(_FakeClient(), "")


def test_parse_rejects_profile_url():
    with pytest.raises(ValueError, match="Not a recognised conversation target"):
        parse_thread_target(_FakeClient(), "https://www.linkedin.com/in/somebody/")


def test_build_conversation_urn_roundtrips_har_value():
    assert build_conversation_urn(SELF_FSD_URN, THREAD_ID) == FULL_CONV_URN


def test_send_message_posts_expected_shape():
    client = _FakeClient(_FakeResponse(200, payload={"value": {"urn": "urn:li:msg:1"}}))
    result = send_message(client, SELF_FSD_URN, THREAD_ID, "Hi there")

    assert len(client.post_calls) == 1
    call = client.post_calls[0]
    assert call["uri"] == "/voyagerMessagingDashMessengerMessages"
    assert call["params"] == {"action": "createMessage"}
    body = json.loads(call["data"])
    assert body["message"]["body"]["text"] == "Hi there"
    assert body["message"]["body"]["attributes"] == []
    assert body["message"]["renderContentUnions"] == []
    assert body["message"]["conversationUrn"] == FULL_CONV_URN
    # originToken must look like a UUID (LinkedIn dedupes on it).
    assert len(body["message"]["originToken"]) == 36
    assert body["mailboxUrn"] == SELF_FSD_URN
    assert body["dedupeByClientGeneratedToken"] is False
    assert len(body["trackingId"]) == 16
    # Headers must match the captured live request.
    headers = call["headers"]
    assert headers["accept"] == "application/json"
    assert headers["content-type"] == "text/plain;charset=UTF-8"
    assert result == {"value": {"urn": "urn:li:msg:1"}}


def test_send_message_surfaces_status_and_body():
    client = _FakeClient(_FakeResponse(429, '{"message":"too many"}'))
    with pytest.raises(MessageError) as exc:
        send_message(client, SELF_FSD_URN, THREAD_ID, "hi")
    msg = str(exc.value)
    assert "HTTP 429" in msg
    assert "too many" in msg
    assert THREAD_ID in msg


def test_send_message_handles_non_json_response():
    client = _FakeClient(_FakeResponse(200, text=""))

    class _BadJson(_FakeResponse):
        def json(self):
            raise ValueError("no body")

    client._post_response = _BadJson(200, "")
    result = send_message(client, SELF_FSD_URN, THREAD_ID, "hi")
    assert result == {}


def test_message_length_bound_is_generous():
    # Sanity check on the constant — guards against an accidental tightening
    # that would reject realistic long messages.
    assert MAX_MESSAGE_LEN >= 4000


# ---------- profile targets / new conversations ----------

from clinkedin.inbox import InboxError  # noqa: E402
from clinkedin.message import resolve_message_target, start_conversation  # noqa: E402

RECIPIENT_ACOA = "ACoAAB1234567890abcdefghijklmnopqrstuv"
RECIPIENT_URN = f"urn:li:fsd_profile:{RECIPIENT_ACOA}"


class _RoutingClient:
    """Routes self/profile lookups and the by-recipients GraphQL query."""

    def __init__(self, existing_thread: str | None = None, post_response=None, lookup_status=200):
        self.existing_thread = existing_thread
        self.lookup_status = lookup_status
        self._post_response = post_response or _FakeResponse(200)
        self.fetch_calls: list[str] = []
        self.post_calls: list[dict] = []

    def _fetch(self, uri, **kwargs):
        self.fetch_calls.append(uri)
        if "memberIdentity=me" in uri:
            return _FakeResponse(200, payload={"elements": [{"entityUrn": SELF_FSD_URN}]})
        if "memberIdentity=" in uri:
            return _FakeResponse(200, payload={"elements": [{"entityUrn": RECIPIENT_URN}]})
        assert "messengerConversations.9c3ab" in uri
        elements = []
        if self.existing_thread:
            elements = [{
                "groupChat": False,
                "conversationUrl": f"https://www.linkedin.com/messaging/thread/{self.existing_thread}/",
            }]
        return _FakeResponse(
            self.lookup_status,
            payload={"data": {"messengerConversationsByRecipients": {"elements": elements}}},
        )

    def _post(self, uri, **kwargs):
        self.post_calls.append({"uri": uri, **kwargs})
        return self._post_response


def test_resolve_thread_target_unchanged():
    dest = resolve_message_target(_RoutingClient(), FULL_CONV_URN)
    assert dest == {
        "mailbox_urn": SELF_FSD_URN,
        "thread_id": THREAD_ID,
        "recipient_urn": None,
        "label": THREAD_ID,
    }


def test_resolve_profile_url_with_existing_thread():
    client = _RoutingClient(existing_thread=THREAD_ID)
    dest = resolve_message_target(client, "https://www.linkedin.com/in/ada-lovelace/")
    assert dest["recipient_urn"] == RECIPIENT_URN
    assert dest["thread_id"] == THREAD_ID
    assert dest["mailbox_urn"] == SELF_FSD_URN
    assert dest["label"] == "ada-lovelace"
    assert any("memberIdentity=ada-lovelace" in u for u in client.fetch_calls)


def test_resolve_profile_url_without_thread():
    dest = resolve_message_target(_RoutingClient(), "https://www.linkedin.com/in/ada-lovelace/")
    assert dest["thread_id"] is None
    assert dest["recipient_urn"] == RECIPIENT_URN


def test_resolve_bare_profile_urn_skips_profile_lookup():
    client = _RoutingClient()
    dest = resolve_message_target(client, RECIPIENT_URN)
    assert dest["recipient_urn"] == RECIPIENT_URN
    assert not any(f"memberIdentity={RECIPIENT_ACOA}" in u for u in client.fetch_calls)


def test_resolve_lookup_failure_raises():
    with pytest.raises(InboxError):
        resolve_message_target(
            _RoutingClient(lookup_status=500), "https://www.linkedin.com/in/ada-lovelace/"
        )


def test_start_conversation_posts_recipient_payload():
    new_thread = "2-MzdkY2FhNmItMTA2OS00ZjhhLWE1ODQtNDQzMzZkOWU1NzBmXzEwMA=="
    resp = _FakeResponse(
        200,
        text=json.dumps({"value": {"conversationUrn": f"urn:li:msg_conversation:({SELF_FSD_URN},{new_thread})"}}),
    )
    client = _RoutingClient(post_response=resp)
    thread = start_conversation(client, SELF_FSD_URN, RECIPIENT_URN, "Hi, thanks for the add.")

    assert thread == new_thread
    call = client.post_calls[0]
    assert call["uri"] == "/voyagerMessagingDashMessengerMessages"
    assert call["params"] == {"action": "createMessage"}
    body = json.loads(call["data"])
    assert body["hostRecipientUrns"] == [RECIPIENT_URN]
    assert "conversationUrn" not in body["message"]
    assert body["message"]["body"]["text"] == "Hi, thanks for the add."
    assert body["mailboxUrn"] == SELF_FSD_URN


def test_start_conversation_unknown_response_returns_empty():
    client = _RoutingClient(post_response=_FakeResponse(200, text="{}"))
    assert start_conversation(client, SELF_FSD_URN, RECIPIENT_URN, "hi") == ""


def test_start_conversation_error_mentions_connections():
    client = _RoutingClient(post_response=_FakeResponse(403, text="forbidden"))
    with pytest.raises(MessageError, match="HTTP 403.*1st-degree"):
        start_conversation(client, SELF_FSD_URN, RECIPIENT_URN, "hi")


# ---------- CLI: message command with profile targets ----------

import argparse  # noqa: E402

from clinkedin.cli import _cmd_message  # noqa: E402


def _msg_ns(target, **kw):
    d = {"target": target, "message": "Hi there", "message_file": None,
         "dry_run": False, "yes": True, "queue": False}
    d.update(kw)
    return argparse.Namespace(**d)


def test_cmd_message_profile_without_thread_starts_conversation(monkeypatch, capsys):
    client = _RoutingClient(post_response=_FakeResponse(200, text="{}"))
    monkeypatch.setattr("clinkedin.cli.make_client", lambda: client)
    assert _cmd_message(_msg_ns("https://www.linkedin.com/in/ada-lovelace/")) == 0
    out = capsys.readouterr().out
    assert "ada-lovelace (new conversation)" in out
    body = json.loads(client.post_calls[0]["data"])
    assert body["hostRecipientUrns"] == [RECIPIENT_URN]


def test_cmd_message_profile_with_thread_replies(monkeypatch, capsys):
    client = _RoutingClient(existing_thread=THREAD_ID)
    monkeypatch.setattr("clinkedin.cli.make_client", lambda: client)
    assert _cmd_message(_msg_ns("https://www.linkedin.com/in/ada-lovelace/")) == 0
    assert f"existing conversation {THREAD_ID}" in capsys.readouterr().out
    body = json.loads(client.post_calls[0]["data"])
    assert body["message"]["conversationUrn"] == FULL_CONV_URN
    assert "hostRecipientUrns" not in body


def test_cmd_message_profile_dry_run_sends_nothing(monkeypatch, capsys):
    client = _RoutingClient()
    monkeypatch.setattr("clinkedin.cli.make_client", lambda: client)
    assert _cmd_message(_msg_ns("https://www.linkedin.com/in/ada-lovelace/", dry_run=True)) == 0
    assert client.post_calls == []


def test_cmd_message_bad_target_returns_2(monkeypatch, capsys):
    monkeypatch.setattr("clinkedin.cli.make_client", lambda: _RoutingClient())
    assert _cmd_message(_msg_ns("https://example.com/nope")) == 2
