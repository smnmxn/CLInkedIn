import argparse
import json
from urllib.parse import unquote

import pytest

from clinkedin.cli import _cmd_inbox, _cmd_thread
from clinkedin.inbox import (
    InboxError,
    fetch_conversations,
    fetch_messages,
    format_conversations,
    format_json,
    format_messages,
)


SELF_ACOA = "ACoAAAA63I8BlgvjEFmtc5IYoErxz7GVcaSBfLg"
SELF_URN = f"urn:li:fsd_profile:{SELF_ACOA}"
OTHER_URN = "urn:li:fsd_profile:ACoAAB1234567890abcdefghijklmnopqrstuv"
THREAD_ID = "2-ZDlmNjE3YjUtZjU4ZS00NzZmLWIyOWMtMWFjMDU4OGY1ZjU1XzEwMA=="


class _FakeResponse:
    def __init__(self, status_code=200, body=None, text=None):
        self.status_code = status_code
        self._body = body
        self._text = text

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


class _FakeClient:
    """Serves one queued GraphQL response per call; self-URN is pre-cached."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls: list[str] = []
        self._self_urn = SELF_URN

    def _fetch(self, uri, **kwargs):
        assert kwargs.get("headers", {}).get("accept") == "application/graphql"
        self.calls.append(unquote(uri))
        if not self._responses:
            raise AssertionError(f"unexpected extra fetch: {uri}")
        r = self._responses.pop(0)
        return r if isinstance(r, _FakeResponse) else _FakeResponse(200, r)


def _name(first, last):
    return {"firstName": {"text": first}, "lastName": {"text": last}}


def _participant(urn, first, last):
    return {
        "hostIdentityUrn": urn,
        "participantType": {
            "member": {
                **_name(first, last),
                "headline": {"text": f"{first}'s headline"},
                "profileUrl": f"https://www.linkedin.com/in/{urn.rsplit(':', 1)[-1]}",
            },
            "agent": None,
            "custom": None,
            "organization": None,
        },
    }


def _conversation(n, *, last_from_me=True, unread=0, ts=None):
    thread = f"2-thread{n}"
    sender = SELF_URN if last_from_me else OTHER_URN
    return {
        "entityUrn": f"urn:li:msg_conversation:({SELF_URN},{thread})",
        "conversationUrl": f"https://www.linkedin.com/messaging/thread/{thread}/",
        "title": None,
        "groupChat": False,
        "unreadCount": unread,
        "read": unread == 0,
        "lastActivityAt": ts or 1791824274029 - n * 1000,
        "conversationParticipants": [
            _participant(OTHER_URN, "Ada", f"Lovelace{n}"),
            _participant(SELF_URN, "Simon", "Moxon"),
        ],
        # The inbox listing's embedded message has no participantType on sender.
        "messages": {
            "elements": [
                {
                    "entityUrn": f"urn:li:msg_message:{n}",
                    "deliveredAt": 1791824274029 - n * 1000,
                    "body": {"text": f"last message {n}"},
                    "sender": {"hostIdentityUrn": sender},
                }
            ]
        },
    }


def _conv_page(elements, cursor=None):
    return {
        "data": {
            "messengerConversationsByCategoryQuery": {
                "elements": elements,
                "metadata": {"nextCursor": cursor},
            }
        }
    }


def _message(n, *, from_me=False, text=None, fallback=None):
    urn, first, last = (SELF_URN, "Simon", "Moxon") if from_me else (OTHER_URN, "Ada", "Lovelace")
    return {
        "entityUrn": f"urn:li:msg_message:m{n}",
        "deliveredAt": 1791800000000 + n * 1000,
        "body": {"text": text if text is not None else f"message {n}"},
        "renderContentFallbackText": fallback,
        "sender": _participant(urn, first, last),
    }


def _msg_page(elements, key="messengerMessagesBySyncToken"):
    return {"data": {key: {"elements": elements, "metadata": {}}}}


# ---------- fetch_conversations ----------


def test_conversations_normalizes_rows():
    client = _FakeClient(_conv_page([_conversation(1), _conversation(2, last_from_me=False, unread=2)]))
    rows = fetch_conversations(client, limit=10)

    assert len(rows) == 2
    r0, r1 = rows
    assert r0["thread_id"] == "2-thread1"
    assert r0["title"] == "Ada Lovelace1"
    assert [p["name"] for p in r0["participants"]] == ["Ada Lovelace1"]  # self excluded
    assert r0["last_sender_is_me"] is True
    assert r0["last_message"] == "last message 1"
    # Sender name resolved from participants when the embedded message lacks it.
    assert r1["last_sender"] == "Ada Lovelace2"
    assert r1["last_sender_is_me"] is False
    assert r1["unread_count"] == 2 and r1["read"] is False

    assert "category:INBOX" in client.calls[0]
    assert f"mailboxUrn:{SELF_URN}" in client.calls[0]
    assert "nextCursor" not in client.calls[0]


def test_conversations_follows_cursor_until_limit():
    client = _FakeClient(
        _conv_page([_conversation(i) for i in range(20)], cursor="CUR1"),
        _conv_page([_conversation(i) for i in range(20, 40)], cursor="CUR2"),
    )
    rows = fetch_conversations(client, limit=25)
    assert len(rows) == 25
    assert len(client.calls) == 2
    assert "nextCursor:CUR1" in client.calls[1]


def test_conversations_stops_without_cursor():
    client = _FakeClient(_conv_page([_conversation(1)], cursor=None))
    rows = fetch_conversations(client, limit=50)
    assert len(rows) == 1
    assert len(client.calls) == 1


def test_conversations_page_cap():
    pages = [_conv_page([_conversation(i)], cursor=f"C{i}") for i in range(10)]
    client = _FakeClient(*pages)
    rows = fetch_conversations(client, limit=100)
    assert len(client.calls) == 5
    assert len(rows) == 5


def test_conversations_unread_only_filters_and_pages():
    client = _FakeClient(
        _conv_page([_conversation(1), _conversation(2, unread=1)], cursor="C"),
        _conv_page([_conversation(3, unread=4), _conversation(4)], cursor=None),
    )
    rows = fetch_conversations(client, limit=5, unread_only=True)
    assert [r["thread_id"] for r in rows] == ["2-thread2", "2-thread3"]


def test_conversations_empty_inbox():
    assert fetch_conversations(_FakeClient(_conv_page([])), limit=5) == []


def test_conversations_http_error_raises():
    with pytest.raises(InboxError, match="HTTP 500"):
        fetch_conversations(_FakeClient(_FakeResponse(500)), limit=5)


def test_conversations_non_json_raises():
    with pytest.raises(InboxError, match="non-JSON"):
        fetch_conversations(_FakeClient(_FakeResponse(200, None)), limit=5)


def test_conversations_unexpected_shape_raises():
    with pytest.raises(InboxError):
        fetch_conversations(_FakeClient({"data": {"somethingElse": {}}}), limit=5)


# ---------- fetch_messages ----------


def test_messages_single_page_oldest_first():
    client = _FakeClient(
        _msg_page([_message(3), _message(1, from_me=True), _message(2)]),
        _msg_page([], key="messengerMessagesByAnchorTimestamp"),
    )
    rows = fetch_messages(client, THREAD_ID, limit=20)

    assert [r["text"] for r in rows] == ["message 1", "message 2", "message 3"]
    assert rows[0]["sender"] == "Simon Moxon"
    assert rows[1]["sender"] == "Ada Lovelace"
    assert f"conversationUrn:urn:li:msg_conversation:({SELF_URN},{THREAD_ID})" in client.calls[0]
    # Older-page request anchors on the oldest message seen.
    assert "deliveredAt:1791800001000" in client.calls[1]
    assert "countBefore:20,countAfter:0" in client.calls[1]


def test_messages_pages_backwards_until_limit():
    client = _FakeClient(
        _msg_page([_message(i) for i in range(40, 60)]),
        _msg_page([_message(i) for i in range(20, 40)], key="messengerMessagesByAnchorTimestamp"),
    )
    rows = fetch_messages(client, THREAD_ID, limit=30)
    assert len(rows) == 30
    # Most recent 30, oldest first.
    assert rows[0]["text"] == "message 30"
    assert rows[-1]["text"] == "message 59"
    assert len(client.calls) == 2


def test_messages_stops_when_older_page_only_repeats():
    client = _FakeClient(
        _msg_page([_message(1), _message(2)]),
        _msg_page([_message(1)], key="messengerMessagesByAnchorTimestamp"),
    )
    rows = fetch_messages(client, THREAD_ID, limit=20)
    assert len(rows) == 2
    assert len(client.calls) == 2


def test_messages_attachment_fallback_text():
    client = _FakeClient(
        _msg_page([_message(1, text="", fallback="Sent an attachment")]),
        _msg_page([], key="messengerMessagesByAnchorTimestamp"),
    )
    rows = fetch_messages(client, THREAD_ID, limit=5)
    assert rows[0]["text"] == "[Sent an attachment]"


def test_messages_accepts_thread_url():
    client = _FakeClient(_msg_page([]))
    fetch_messages(client, f"https://www.linkedin.com/messaging/thread/{THREAD_ID}/", limit=5)
    assert THREAD_ID in client.calls[0]


def test_messages_bad_target_raises_value_error():
    with pytest.raises(ValueError):
        fetch_messages(_FakeClient(), "https://example.com/nope", limit=5)


# ---------- formatters ----------


def test_format_conversations():
    rows = fetch_conversations(
        _FakeClient(_conv_page([_conversation(1), _conversation(2, last_from_me=False, unread=3)])),
        limit=5,
    )
    out = format_conversations(rows)
    assert "Ada Lovelace1" in out
    assert "You: last message 1" in out
    assert "[3 unread] Ada Lovelace2" in out
    assert "Ada Lovelace2: last message 2" in out
    assert "https://www.linkedin.com/messaging/thread/2-thread1/" in out


def test_format_conversations_empty():
    assert format_conversations([]) == "No conversations."


def test_format_messages_and_json():
    rows = fetch_messages(
        _FakeClient(_msg_page([_message(1)]), _msg_page([], key="messengerMessagesByAnchorTimestamp")),
        THREAD_ID,
        limit=5,
    )
    assert "· Ada Lovelace\nmessage 1" in format_messages(rows)
    assert json.loads(format_json(rows))[0]["text"] == "message 1"
    assert format_messages([]) == "No messages."


# ---------- CLI ----------


def _ns(**kw):
    defaults = {"limit": 20, "unread": False, "json": False, "output": None, "target": THREAD_ID}
    defaults.update(kw)
    return argparse.Namespace(**defaults)


def test_cmd_inbox_rejects_bad_limit(capsys):
    assert _cmd_inbox(_ns(limit=0)) == 2


def test_cmd_inbox_prints(monkeypatch, capsys):
    monkeypatch.setattr("clinkedin.cli.make_client", lambda: _FakeClient(_conv_page([_conversation(1)])))
    assert _cmd_inbox(_ns()) == 0
    assert "Ada Lovelace1" in capsys.readouterr().out


def test_cmd_inbox_error_returns_1(monkeypatch, capsys):
    monkeypatch.setattr("clinkedin.cli.make_client", lambda: _FakeClient(_FakeResponse(403)))
    assert _cmd_inbox(_ns()) == 1
    assert "HTTP 403" in capsys.readouterr().err


def test_cmd_thread_bad_target_returns_2(monkeypatch, capsys):
    monkeypatch.setattr("clinkedin.cli.make_client", lambda: _FakeClient())
    assert _cmd_thread(_ns(target="nope")) == 2


def test_cmd_thread_json(monkeypatch, capsys):
    monkeypatch.setattr(
        "clinkedin.cli.make_client",
        lambda: _FakeClient(_msg_page([_message(1)]), _msg_page([], key="messengerMessagesByAnchorTimestamp")),
    )
    assert _cmd_thread(_ns(json=True)) == 0
    assert json.loads(capsys.readouterr().out)[0]["sender"] == "Ada Lovelace"
