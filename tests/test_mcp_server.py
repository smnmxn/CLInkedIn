import pytest

pytest.importorskip("mcp")

from clinkedin import mcp_server
from clinkedin.auth import NotAuthenticatedError
from clinkedin.invite import InviteError

VALID_URL = "https://www.linkedin.com/in/simon-foo/"

tool = mcp_server.linkedin_send_connection_request


@pytest.fixture(autouse=True)
def _reset_client():
    mcp_server._client = None
    yield
    mcp_server._client = None


def test_happy_path(monkeypatch):
    calls = {}

    def fake_make_client():
        return "sentinel-client"

    def fake_send_invite(client, public_id, message=""):
        calls["args"] = (client, public_id, message)

    monkeypatch.setattr(mcp_server, "make_client", fake_make_client)
    monkeypatch.setattr(mcp_server, "send_invite", fake_send_invite)

    result = tool(VALID_URL, message="hi there")

    assert result == {"ok": True, "public_id": "simon-foo", "message_sent": True}
    assert calls["args"] == ("sentinel-client", "simon-foo", "hi there")


def test_happy_path_no_message(monkeypatch):
    monkeypatch.setattr(mcp_server, "make_client", lambda: object())
    monkeypatch.setattr(mcp_server, "send_invite", lambda *a, **kw: None)

    result = tool(VALID_URL)

    assert result["ok"] is True
    assert result["message_sent"] is False


def test_bad_url():
    result = tool("https://example.com/not-linkedin")
    assert result["ok"] is False
    assert "invalid URL" in result["error"]


def test_message_too_long():
    result = tool(VALID_URL, message="x" * 301)
    assert result["ok"] is False
    assert "301/300" in result["error"]


def test_not_authenticated(monkeypatch):
    def boom():
        raise NotAuthenticatedError("no session")

    monkeypatch.setattr(mcp_server, "make_client", boom)

    result = tool(VALID_URL)

    assert result["ok"] is False
    assert result["public_id"] == "simon-foo"
    assert "not authenticated" in result["error"]


def test_invite_error(monkeypatch):
    def fake_send_invite(client, public_id, message=""):
        raise InviteError("already connected or rate-limited")

    monkeypatch.setattr(mcp_server, "make_client", lambda: object())
    monkeypatch.setattr(mcp_server, "send_invite", fake_send_invite)

    result = tool(VALID_URL)

    assert result["ok"] is False
    assert result["public_id"] == "simon-foo"
    assert result["error"] == "already connected or rate-limited"


def test_client_is_cached(monkeypatch):
    call_count = {"n": 0}

    def counting_make_client():
        call_count["n"] += 1
        return object()

    monkeypatch.setattr(mcp_server, "make_client", counting_make_client)
    monkeypatch.setattr(mcp_server, "send_invite", lambda *a, **kw: None)

    tool(VALID_URL)
    tool(VALID_URL)

    assert call_count["n"] == 1


def test_list_conversations(monkeypatch):
    monkeypatch.setattr(mcp_server, "_client", object())
    seen = {}

    def fake(client, limit, unread_only):
        seen.update(limit=limit, unread_only=unread_only)
        return [{"thread_id": "2-x"}]

    monkeypatch.setattr(mcp_server, "fetch_conversations", fake)
    out = mcp_server.linkedin_list_conversations(limit=5, unread_only=True)
    assert out == {"ok": True, "results": [{"thread_id": "2-x"}]}
    assert seen == {"limit": 5, "unread_only": True}


def test_read_conversation_bad_target(monkeypatch):
    monkeypatch.setattr(mcp_server, "_client", object())

    def fake(client, target, limit):
        raise ValueError("Not a recognised conversation target")

    monkeypatch.setattr(mcp_server, "fetch_messages", fake)
    out = mcp_server.linkedin_read_conversation("nope")
    assert out["ok"] is False
    assert "invalid target" in out["error"]


def test_send_message_new_conversation(monkeypatch):
    monkeypatch.setattr(mcp_server, "_client", object())
    monkeypatch.setattr(
        mcp_server,
        "resolve_message_target",
        lambda c, t: {"mailbox_urn": "me", "thread_id": None, "recipient_urn": "urn:r", "label": "x"},
    )
    monkeypatch.setattr(mcp_server, "start_conversation", lambda c, m, r, text: "2-new")
    out = mcp_server.linkedin_send_message("https://www.linkedin.com/in/x/", "hi")
    assert out == {"ok": True, "thread_id": "2-new", "new_conversation": True}


def test_send_message_existing_thread(monkeypatch):
    monkeypatch.setattr(mcp_server, "_client", object())
    monkeypatch.setattr(
        mcp_server,
        "resolve_message_target",
        lambda c, t: {"mailbox_urn": "me", "thread_id": "2-old", "recipient_urn": None, "label": "2-old"},
    )
    sent = []
    monkeypatch.setattr(mcp_server, "send_message", lambda c, m, t, text: sent.append(t))
    out = mcp_server.linkedin_send_message("2-old", "hi")
    assert out["ok"] and out["new_conversation"] is False and sent == ["2-old"]


def test_send_message_empty():
    assert mcp_server.linkedin_send_message("2-x", "  ")["ok"] is False
