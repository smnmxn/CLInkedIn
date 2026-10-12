import argparse
import json
from pathlib import Path

import pytest

from clinkedin.cli import _cmd_feed, main
from clinkedin.feed import FeedError, fetch_feed, format_json, format_text


FIXTURES = Path(__file__).parent / "fixtures"


def _load_feed_response():
    return json.loads((FIXTURES / "feed_response.json").read_text())


class _FakeResponse:
    def __init__(self, status_code=200, body=None, body_text=None):
        self.status_code = status_code
        self._body = body
        self._text = body_text

    def json(self):
        if self._text is not None and self._body is None:
            raise ValueError("not json")
        return self._body


class _FakeClient:
    """Serves one queued response per /feed/updatesV2 call, in order."""

    def __init__(self, *pages):
        self._pages = list(pages)
        self.calls = []

    def _fetch(self, uri, **kwargs):
        self.calls.append({"uri": uri, "params": kwargs.get("params")})
        if not self._pages:
            raise AssertionError(f"unexpected extra fetch: {uri}")
        page = self._pages.pop(0)
        if isinstance(page, _FakeResponse):
            return page
        return _FakeResponse(200, page)


def _post(n: int, *, sponsored: bool = False) -> dict:
    urn = f"urn:li:activity:{n}"
    wrapped = f"urn:li:sponsoredContentV2:({urn},urn:li:sponsoredCreative:1)" if sponsored else urn
    return {
        "entityUrn": f"urn:li:fs_updateV2:({wrapped},MAIN_FEED,EMPTY,DEFAULT,false)",
        "actor": {"name": {"text": f"Author {n}"}, "subDescription": {"text": "1d •  "}},
        "commentary": {"text": {"text": f"post {n}"}},
        "updateMetadata": {"urn": urn},
    }


# ---------- fetch_feed ----------


def test_fetch_feed_drops_promoted_posts():
    client = _FakeClient(_load_feed_response(), {"elements": []})
    result = fetch_feed(client, limit=10)
    # The fixture has 3 elements, one of which is a promoted post.
    assert [e["updateMetadata"]["urn"] for e in result] == [
        "urn:li:activity:7200000000000000001",
        "urn:li:activity:7200000000000000003",
    ]
    assert client.calls[0]["uri"] == "/feed/updatesV2"
    params = client.calls[0]["params"]
    assert params["q"] == "chronFeed"
    assert params["start"] == 0


def test_fetch_feed_single_request_when_first_page_fills_limit():
    client = _FakeClient({"elements": [_post(n) for n in range(1, 8)]})
    result = fetch_feed(client, limit=5)
    assert len(result) == 5
    assert len(client.calls) == 1
    # Over-asks to absorb promoted posts, which are dropped after the fetch.
    assert client.calls[0]["params"]["count"] > 5


def test_fetch_feed_caps_count_at_100():
    client = _FakeClient({"elements": []})
    fetch_feed(client, limit=500)
    assert client.calls[0]["params"]["count"] == 100


def test_fetch_feed_pages_until_limit_and_dedupes():
    page1 = {"elements": [_post(1), _post(2, sponsored=True), _post(3)]}
    # A live feed shifts between requests, so the next page can repeat an update.
    page2 = {"elements": [_post(3), _post(4), _post(5)]}
    client = _FakeClient(page1, page2)
    result = fetch_feed(client, limit=4)
    assert [e["updateMetadata"]["urn"] for e in result] == [
        "urn:li:activity:1",
        "urn:li:activity:3",
        "urn:li:activity:4",
        "urn:li:activity:5",
    ]
    # The second request resumes after everything the first returned, ads included.
    assert client.calls[1]["params"]["start"] == 3


def test_fetch_feed_empty_is_ok():
    client = _FakeClient({"elements": [], "metadata": {}, "paging": {}})
    assert fetch_feed(client) == []
    assert len(client.calls) == 1


def test_fetch_feed_stops_after_max_pages():
    # Every page is all ads: give up rather than hammering the session.
    pages = [{"elements": [_post(n, sponsored=True)]} for n in range(1, 6)]
    client = _FakeClient(*pages)
    assert fetch_feed(client, limit=10) == []
    assert len(client.calls) == 5


def test_fetch_feed_non_200_raises():
    client = _FakeClient(_FakeResponse(401, {"status": 401}))
    with pytest.raises(FeedError, match="HTTP 401"):
        fetch_feed(client)


def test_fetch_feed_non_json_raises():
    client = _FakeClient(_FakeResponse(200, body_text="<html>"))
    with pytest.raises(FeedError, match="non-JSON"):
        fetch_feed(client)


# ---------- formatting ----------


def _feed_posts():
    return fetch_feed(_FakeClient(_load_feed_response(), {"elements": []}), limit=10)


def test_format_json_emits_normalized_rows():
    rows = json.loads(format_json(_feed_posts()))
    assert rows[0] == {
        "date": "1h",
        "author": "Ada Lovelace",
        "author_headline": "Mathematician at Analytical Engine Co",
        "author_url": "https://www.linkedin.com/in/ada-lovelace",
        "reason": "Grace Hopper likes this",
        "text": "Notes on the Analytical Engine are finally published.\nSecond line of the post.",
        "like_count": 25,
        "comment_count": 2,
        "urn": "urn:li:activity:7200000000000000001",
        "url": "https://www.linkedin.com/feed/update/urn:li:activity:7200000000000000001/",
    }
    # A company post with no "X likes this" header.
    assert rows[1]["author"] == "Acme Corp"
    assert rows[1]["author_url"] == "https://www.linkedin.com/company/acme/"
    assert rows[1]["reason"] == ""
    assert rows[1]["date"] == "3d"


def test_format_text_one_line_per_post():
    lines = format_text(_feed_posts()).split("\n")
    assert len(lines) == 2
    assert lines[0] == (
        '1h · Ada Lovelace · "Notes on the Analytical Engine are finally published. '
        'Second line of the post." · 25 likes · 2 comments · '
        "https://www.linkedin.com/feed/update/urn:li:activity:7200000000000000001/"
    )


def test_format_text_truncates_long_text():
    post = _post(1)
    post["commentary"]["text"]["text"] = "x" * 500
    line = format_text([post])
    assert '"' + "x" * 119 + '…"' in line


def test_format_text_full_shows_reason_and_untruncated_body():
    out = format_text(_feed_posts(), full=True)
    first, second = out.split("\n\n---\n\n")
    assert first.split("\n") == [
        "1h · Ada Lovelace · 25 likes · 2 comments · "
        "https://www.linkedin.com/feed/update/urn:li:activity:7200000000000000001/",
        "(Grace Hopper likes this)",
        "Notes on the Analytical Engine are finally published.",
        "Second line of the post.",
    ]
    # No reason line when the post is in the feed on its own merits.
    assert second.split("\n")[1] == "Our v2 platform is now generally available."


def test_format_text_empty():
    assert format_text([]) == ""


# ---------- clinkedin feed ----------


def _ns(**kwargs) -> argparse.Namespace:
    defaults = {"limit": 10, "full": False, "since": None, "json": False, "output": None}
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _no_client():
    raise AssertionError("should not build a client before argument validation")


def test_cmd_feed_bad_since_returns_2(monkeypatch, capsys):
    monkeypatch.setattr("clinkedin.cli.make_client", _no_client)
    assert _cmd_feed(_ns(since="yesterday-ish")) == 2
    assert "Could not parse --since" in capsys.readouterr().err


def test_cmd_feed_bad_limit_returns_2(monkeypatch, capsys):
    monkeypatch.setattr("clinkedin.cli.make_client", _no_client)
    assert _cmd_feed(_ns(limit=0)) == 2


def test_cmd_feed_prints_posts(monkeypatch, capsys):
    monkeypatch.setattr("clinkedin.cli.make_client", lambda: object())
    seen = {}

    def _fake_fetch_feed(client, limit):
        seen["limit"] = limit
        return [_post(1), _post(2)]

    monkeypatch.setattr("clinkedin.cli.fetch_feed", _fake_fetch_feed)
    assert main(["feed", "--limit", "2"]) == 0
    assert seen == {"limit": 2}
    out = capsys.readouterr().out
    assert "Author 1" in out and "post 2" in out


def test_cmd_feed_since_overfetches_then_filters(monkeypatch, capsys):
    monkeypatch.setattr("clinkedin.cli.make_client", lambda: object())
    seen = {}
    old = _post(2)
    old["actor"]["subDescription"]["text"] = "2w •  "

    def _fake_fetch_feed(client, limit):
        seen["limit"] = limit
        return [_post(1), old, _post(3)]

    monkeypatch.setattr("clinkedin.cli.fetch_feed", _fake_fetch_feed)
    assert _cmd_feed(_ns(limit=10, since="7d", json=True)) == 0
    assert seen == {"limit": 50}
    rows = json.loads(capsys.readouterr().out)
    assert [r["urn"] for r in rows] == ["urn:li:activity:1", "urn:li:activity:3"]


def test_cmd_feed_error_returns_1(monkeypatch, capsys):
    monkeypatch.setattr("clinkedin.cli.make_client", lambda: object())

    def _boom(client, limit):
        raise FeedError("Feed fetch failed (HTTP 401).")

    monkeypatch.setattr("clinkedin.cli.fetch_feed", _boom)
    assert _cmd_feed(_ns()) == 1
    assert "HTTP 401" in capsys.readouterr().err
