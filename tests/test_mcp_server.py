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
