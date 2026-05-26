import argparse
import json

import pytest

from clinkedin.cli import _cmd_comment
from clinkedin.comment import CommentError, parse_post_url, post_comment
from clinkedin.invite import InviteError, resolve_self_urn


URN = "ACoAACX1hoMBvWqTY21JGe0z91mnmjmLy9Wen4w"
ACTIVITY_ID = "7457689385040470016"
SHARE_ID = "7452350369906552832"
ACTIVITY_URN = f"urn:li:activity:{ACTIVITY_ID}"


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


# A minimal HTML stub that contains the IDs our scraper looks for.
def _post_page_html(activity_id: str, share_id: str, tracking_id: str = "Test1234567890==") -> str:
    return (
        "<html><body><script>"
        '"feedUpdateUrn":{"shareUrn":{"shareId":"' + share_id + '"}},'
        '"trackingId":"' + tracking_id + '",'
        '"threadUrn":{"activityUrn":{"activityId":"' + activity_id + '"}}'
        "</script></body></html>"
    )


class _FakeClient:
    """Records _fetch and _post calls. Default fetch returns a self-profile
    payload so `resolve_self_urn` works without explicit wiring; HTML page
    fetches need to be set up explicitly via `fetch_responses`."""

    def __init__(
        self,
        fetch_responses: list | None = None,
        post_responses: list | None = None,
        default_fetch: _FakeResponse | None = None,
        default_post: _FakeResponse | None = None,
    ):
        self._fetch_q = list(fetch_responses or [])
        self._post_q = list(post_responses or [])
        self._default_fetch = default_fetch or _FakeResponse(
            200,
            payload={"elements": [{"entityUrn": f"urn:li:fsd_profile:{URN}"}]},
        )
        self._default_post = default_post or _FakeResponse(200, payload={})
        self.fetch_calls: list[dict] = []
        self.post_calls: list[dict] = []

    def _fetch(self, uri, **kwargs):
        self.fetch_calls.append({"uri": uri, **kwargs})
        if self._fetch_q:
            return self._fetch_q.pop(0)
        return self._default_fetch

    def _post(self, uri, **kwargs):
        self.post_calls.append({"uri": uri, **kwargs})
        if self._post_q:
            return self._post_q.pop(0)
        return self._default_post


# ---------- parse_post_url ----------


def _ids(activity_id=None, share_id=None, page_path=None):
    return {"activity_id": activity_id, "share_id": share_id, "page_path": page_path}


@pytest.mark.parametrize(
    "url,expected",
    [
        # Bare URN forms — no page to scrape, only one ID present.
        (ACTIVITY_URN, _ids(activity_id=ACTIVITY_ID)),
        (f"  {ACTIVITY_URN}  ", _ids(activity_id=ACTIVITY_ID)),
        (f"urn:li:share:{SHARE_ID}", _ids(share_id=SHARE_ID)),
        (
            "urn:li:ugcPost:7000000000000000000",
            _ids(activity_id="7000000000000000000"),
        ),
        # /feed/update/urn:li:activity:N/ — gives activity, page is fetchable.
        (
            f"https://www.linkedin.com/feed/update/{ACTIVITY_URN}/",
            _ids(activity_id=ACTIVITY_ID, page_path=f"/feed/update/{ACTIVITY_URN}/"),
        ),
        # /posts/...-share-N-X — gives share, page is fetchable.
        (
            f"https://www.linkedin.com/posts/smoxon_some-text-share-{SHARE_ID}-Ih3F/",
            _ids(
                share_id=SHARE_ID,
                page_path=f"/posts/smoxon_some-text-share-{SHARE_ID}-Ih3F/",
            ),
        ),
        # /posts/...-activity-N-X — gives activity.
        (
            f"https://www.linkedin.com/posts/alex_text-activity-{ACTIVITY_ID}-AbCd",
            _ids(
                activity_id=ACTIVITY_ID,
                page_path=f"/posts/alex_text-activity-{ACTIVITY_ID}-AbCd",
            ),
        ),
        # uk subdomain still works.
        (
            "https://uk.linkedin.com/feed/update/urn:li:activity:1234/",
            _ids(activity_id="1234", page_path="/feed/update/urn:li:activity:1234/"),
        ),
    ],
)
def test_parse_post_url_accepts(url, expected):
    assert parse_post_url(url) == expected


def test_parse_post_url_strips_query_string_from_share_url():
    """Share URLs commonly come with utm_* tracking params; we only care about path."""
    url = (
        f"https://www.linkedin.com/posts/smoxon_some-text-share-{SHARE_ID}-Ih3F/"
        "?utm_source=share&utm_medium=member_desktop"
    )
    out = parse_post_url(url)
    assert out["share_id"] == SHARE_ID
    assert out["page_path"].endswith(f"-share-{SHARE_ID}-Ih3F/")


@pytest.mark.parametrize(
    "url",
    [
        "",
        "https://www.linkedin.com/in/simon-foo/",  # profile URL
        "https://www.linkedin.com/school/mit/",
        "https://example.com/feed/update/urn:li:activity:1/",  # wrong host
        "not-a-url",
        "urn:li:member:1234",  # wrong URN type
        "urn:li:activity:!@#$",  # bad chars
    ],
)
def test_parse_post_url_rejects(url):
    with pytest.raises(ValueError):
        parse_post_url(url)


# ---------- resolve_self_urn ----------


def test_resolve_self_urn_caches_after_first_call():
    client = _FakeClient()
    first = resolve_self_urn(client)
    second = resolve_self_urn(client)
    assert first == second == f"urn:li:fsd_profile:{URN}"
    assert len(client.fetch_calls) == 1
    assert "memberIdentity=me" in client.fetch_calls[0]["uri"]


def test_resolve_self_urn_http_error():
    client = _FakeClient(fetch_responses=[_FakeResponse(401, "")])
    with pytest.raises(InviteError, match="HTTP 401"):
        resolve_self_urn(client)


# ---------- post_comment (SDUI endpoint) ----------


def test_post_comment_share_url_fetches_page_then_posts_sdui():
    """Share-URL inputs need an HTML page fetch to recover the activity ID."""
    page_html = _post_page_html(ACTIVITY_ID, SHARE_ID, tracking_id="Test1234567890ABCDEF==")
    client = _FakeClient(fetch_responses=[_FakeResponse(200, text=page_html)])

    url = f"https://www.linkedin.com/posts/smoxon_x-share-{SHARE_ID}-Ih3F/"
    post_comment(client, url, "Hello")

    # 1 fetch for the post page (HTML scrape). Note: no self-URN fetch — the
    # SDUI payload doesn't need it.
    assert len(client.fetch_calls) == 1
    assert client.fetch_calls[0]["uri"].endswith(f"-share-{SHARE_ID}-Ih3F/")
    assert client.fetch_calls[0]["base_request"] is True

    # 1 SDUI POST.
    assert len(client.post_calls) == 1
    call = client.post_calls[0]
    assert call["uri"].startswith(
        "/flagship-web/rsc-action/actions/server-request"
    )
    assert "createComment" in call["uri"]
    assert call["base_request"] is True

    body = json.loads(call["data"])
    payload = body["serverRequest"]["requestedArguments"]["payload"]
    update_item = payload["collection"]["updateKey"]["items"][0]
    assert update_item["feedUpdateUrn"]["updateUrnShareUrn"]["shareUrn"]["shareId"] == SHARE_ID
    assert update_item["trackingId"] == "Test1234567890ABCDEF=="
    assert (
        payload["collection"]["threadUrn"]["threadUrnActivityThreadUrn"]["activityUrn"]["activityId"]
        == ACTIVITY_ID
    )
    # Comment text appears in body["states"], not in the inner payload.
    state_values = {s["key"].split("-", 1)[0]: s["value"] for s in body["states"]}
    assert state_values["commentBoxText"] == "Hello"
    assert state_values["richCommentBoxText"]["text"] == "Hello"


def test_post_comment_activity_url_also_fetches_page_for_share_id():
    """`feed/update/urn:li:activity:N/` URLs give us the activity ID but not
    the share ID — so we still need to scrape the page."""
    page_html = _post_page_html(ACTIVITY_ID, SHARE_ID)
    client = _FakeClient(fetch_responses=[_FakeResponse(200, text=page_html)])

    url = f"https://www.linkedin.com/feed/update/{ACTIVITY_URN}/"
    post_comment(client, url, "Hi")

    assert len(client.fetch_calls) == 1
    body = json.loads(client.post_calls[0]["data"])
    payload = body["serverRequest"]["requestedArguments"]["payload"]
    assert (
        payload["collection"]["updateKey"]["items"][0]["feedUpdateUrn"]["updateUrnShareUrn"]["shareUrn"]["shareId"]
        == SHARE_ID
    )
    assert (
        payload["collection"]["threadUrn"]["threadUrnActivityThreadUrn"]["activityUrn"]["activityId"]
        == ACTIVITY_ID
    )


def test_post_comment_bare_urn_with_only_one_id_raises():
    """A bare URN gives only one of share/activity; we can't fetch a page to
    fill in the other, so we error out with a clear message."""
    client = _FakeClient()
    with pytest.raises(CommentError, match="Pass the public post URL"):
        post_comment(client, ACTIVITY_URN, "hi")
    # No POST should have fired.
    assert client.post_calls == []


def test_post_comment_page_fetch_404_raises_helpfully():
    client = _FakeClient(fetch_responses=[_FakeResponse(404, "")])
    url = f"https://www.linkedin.com/posts/smoxon_x-share-{SHARE_ID}-Ih3F/"
    with pytest.raises(CommentError, match="HTTP 404"):
        post_comment(client, url, "hi")


def test_post_comment_post_page_missing_activity_id_raises():
    """If the page HTML doesn't contain the activity URN we look for, error."""
    client = _FakeClient(fetch_responses=[_FakeResponse(200, text="<html>no IDs here</html>")])
    url = f"https://www.linkedin.com/posts/smoxon_x-share-{SHARE_ID}-Ih3F/"
    with pytest.raises(CommentError, match="activity ID"):
        post_comment(client, url, "hi")


def test_post_comment_http_error_includes_status_and_body():
    page_html = _post_page_html(ACTIVITY_ID, SHARE_ID)
    client = _FakeClient(
        fetch_responses=[_FakeResponse(200, text=page_html)],
        default_post=_FakeResponse(429, '{"message":"slow down"}'),
    )
    url = f"https://www.linkedin.com/posts/smoxon_x-share-{SHARE_ID}-Ih3F/"
    with pytest.raises(CommentError) as exc:
        post_comment(client, url, "hi")
    msg = str(exc.value)
    assert "HTTP 429" in msg
    assert "slow down" in msg
    assert SHARE_ID in msg
    assert ACTIVITY_ID in msg


# ---------- _cmd_comment ----------


def _ns(**kwargs) -> argparse.Namespace:
    """Build a Namespace with all comment-cmd defaults."""
    defaults = {
        "url": f"https://www.linkedin.com/posts/smoxon_x-share-{SHARE_ID}-Ih3F/",
        "message": "hello",
        "message_file": None,
        "dry_run": False,
        "yes": True,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def test_cmd_comment_dry_run_does_not_fetch_or_post(monkeypatch, capsys):
    called = {"client": False}

    def _make_client():
        called["client"] = True
        raise AssertionError("should not call make_client on --dry-run")

    monkeypatch.setattr("clinkedin.cli.make_client", _make_client)
    rc = _cmd_comment(_ns(dry_run=True))
    assert rc == 0
    assert not called["client"]
    out = capsys.readouterr().out
    assert "Comment target:" in out
    assert SHARE_ID in out
    assert "hello" in out


def test_cmd_comment_aborts_when_user_says_no(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    monkeypatch.setattr("clinkedin.cli.make_client", lambda: (_ for _ in ()).throw(AssertionError("should not call")))
    rc = _cmd_comment(_ns(yes=False))
    assert rc == 0
    assert "Aborted." in capsys.readouterr().out


def test_cmd_comment_message_too_long_returns_2(capsys):
    rc = _cmd_comment(_ns(message="x" * 1251))
    assert rc == 2
    assert "1251/1250" in capsys.readouterr().err


def test_cmd_comment_empty_message_returns_2(capsys):
    rc = _cmd_comment(_ns(message="   "))
    assert rc == 2
    assert "empty" in capsys.readouterr().err.lower()


def test_cmd_comment_requires_message_xor_file(capsys):
    rc = _cmd_comment(_ns(message="hi", message_file="/tmp/x.txt"))
    assert rc == 2
    err = capsys.readouterr().err
    assert "--message" in err and "--message-file" in err

    rc2 = _cmd_comment(_ns(message=None, message_file=None))
    assert rc2 == 2


def test_cmd_comment_message_file_reaches_post_payload(monkeypatch, tmp_path):
    f = tmp_path / "reply.txt"
    f.write_text("from a file\nwith newlines", encoding="utf-8")

    page_html = _post_page_html(ACTIVITY_ID, SHARE_ID)
    fake = _FakeClient(fetch_responses=[_FakeResponse(200, text=page_html)])
    monkeypatch.setattr("clinkedin.cli.make_client", lambda: fake)

    rc = _cmd_comment(_ns(message=None, message_file=str(f), yes=True))
    assert rc == 0
    body = json.loads(fake.post_calls[0]["data"])
    state_values = {s["key"].split("-", 1)[0]: s["value"] for s in body["states"]}
    assert state_values["commentBoxText"] == "from a file\nwith newlines"


def test_cmd_comment_invalid_url_returns_2(capsys):
    rc = _cmd_comment(_ns(url="https://example.com/feed/update/x/"))
    assert rc == 2
    assert "LinkedIn" in capsys.readouterr().err


def test_cmd_comment_success_prints_target(monkeypatch, capsys):
    page_html = _post_page_html(ACTIVITY_ID, SHARE_ID)
    fake = _FakeClient(fetch_responses=[_FakeResponse(200, text=page_html)])
    monkeypatch.setattr("clinkedin.cli.make_client", lambda: fake)

    rc = _cmd_comment(_ns(yes=True))
    assert rc == 0
    out = capsys.readouterr().out
    assert "Comment posted" in out
    assert SHARE_ID in out  # target_label is share-form for share URLs
