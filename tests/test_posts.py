import json
from pathlib import Path

import pytest

from clinkedin.posts import (
    AuthExpiredError,
    PageShapeError,
    _parse_posts,
    _validate_response,
    format_json,
    format_table,
    search_posts,
)


FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text()


# === _parse_posts: pure-function tests against synthetic fixtures ===


def test_parse_extracts_three_posts_from_fixture():
    posts = _parse_posts(_read("search_posts_ai.html"))
    assert len(posts) == 3
    assert posts[0]["urn"] == "urn:li:activity:7000000000000000001"
    assert posts[0]["url"] == (
        "https://www.linkedin.com/feed/update/urn:li:activity:7000000000000000001/"
    )
    assert "foundation beneath your AI strategy" in posts[0]["text"]

    assert posts[1]["urn"] == "urn:li:ugcPost:7000000000000000002"
    assert "candidate use of AI" in posts[1]["text"]

    assert posts[2]["urn"] == "urn:li:activity:7000000000000000003"
    assert "Building companies with AI" in posts[2]["text"]


def test_parse_extracts_hoisted_posts():
    """Some LinkedIn responses hoist post bodies into separate streamed chunks
    referenced by $L<id>. Make sure the parser follows those references."""
    posts = _parse_posts(_read("search_posts_hoisted.html"))
    assert len(posts) == 2
    assert posts[0]["urn"] == "urn:li:activity:8000000000000000001"
    assert "AI smartphone" in posts[0]["text"]
    assert posts[1]["urn"] == "urn:li:activity:8000000000000000002"
    assert "polyamorous" in posts[1]["text"]


def test_parse_no_results_returns_empty_list():
    assert _parse_posts(_read("search_posts_no_results.html")) == []


def test_parse_recognised_page_with_zero_extracted_raises():
    with pytest.raises(PageShapeError) as exc_info:
        _parse_posts(_read("search_posts_broken.html"))
    assert "parser likely needs updating" in exc_info.value.reason


def test_parse_missing_urns_raises():
    html = "<html>" + "x" * 200_000 + "feed-commentary" + "</html>"
    with pytest.raises(PageShapeError) as exc_info:
        _parse_posts(html)
    assert "no post URNs" in exc_info.value.reason


def test_parse_missing_feed_commentary_raises():
    html = "<html>" + "urn:li:activity:1234567890" + "x" * 200_000 + "</html>"
    with pytest.raises(PageShapeError) as exc_info:
        _parse_posts(html)
    assert "feed-commentary" in exc_info.value.reason


# === _validate_response: HTTP-level sentinels ===


class _Resp:
    """Minimal stand-in for a requests.Response."""

    def __init__(self, status_code=200, text="", url=""):
        self.status_code = status_code
        self.text = text
        self.url = url
        self.content = text.encode()


def test_validate_passes_through_good_response():
    body = "x" * 200_000
    out = _validate_response(_Resp(200, body, "https://www.linkedin.com/search/results/content/"))
    assert out == body


def test_validate_raises_auth_expired_on_login_redirect():
    with pytest.raises(AuthExpiredError):
        _validate_response(_Resp(200, "x", "https://www.linkedin.com/login"))


def test_validate_raises_auth_expired_on_401():
    with pytest.raises(AuthExpiredError):
        _validate_response(_Resp(401, "x", "https://www.linkedin.com/foo"))


def test_validate_raises_page_shape_on_too_small():
    with pytest.raises(PageShapeError) as exc_info:
        _validate_response(_Resp(200, "tiny", "https://www.linkedin.com/search/results/content/"))
    assert "too small" in exc_info.value.reason


def test_validate_raises_page_shape_on_non_200():
    with pytest.raises(PageShapeError) as exc_info:
        _validate_response(_Resp(503, "x" * 200_000, "https://www.linkedin.com/foo"))
    assert "HTTP 503" in exc_info.value.reason


# === search_posts: end-to-end with a fake client ===


class _FakeSession:
    def __init__(self, body, status=200, url=None):
        self._body = body
        self._status = status
        self._url = url or "https://www.linkedin.com/search/results/content/?keywords=AI"

    def get(self, url, headers=None, allow_redirects=True):
        return _Resp(self._status, self._body, self._url)


class _FakeClient:
    def __init__(self, session):
        self.client = type("C", (), {"session": session})()


def test_search_posts_end_to_end():
    body = _read("search_posts_ai.html")
    client = _FakeClient(_FakeSession(body))
    results = search_posts(client, "AI")
    assert len(results) == 3
    assert results[0]["urn"] == "urn:li:activity:7000000000000000001"


def test_search_posts_applies_limit():
    body = _read("search_posts_ai.html")
    client = _FakeClient(_FakeSession(body))
    results = search_posts(client, "AI", limit=2)
    assert len(results) == 2


def test_search_posts_login_wall_raises_auth_expired():
    client = _FakeClient(
        _FakeSession(
            _read("search_posts_login_wall.html"),
            url="https://www.linkedin.com/login",
        )
    )
    with pytest.raises(AuthExpiredError):
        search_posts(client, "AI")


# === format helpers ===


def test_format_table_renders_rows_with_url():
    rows = [
        {"text": "First post body about AI", "url": "https://x/1", "urn": "urn:li:activity:1"},
        {"text": "Second post", "url": None, "urn": None},
    ]
    out = format_table(rows)
    lines = out.splitlines()
    assert lines[0] == "First post body about AI · https://x/1"
    assert lines[1] == "Second post"


def test_format_table_truncates_long_text():
    rows = [{"text": "a " * 200, "url": "https://x", "urn": None}]
    line = format_table(rows)
    assert "…" in line
    assert "a " * 200 not in line


def test_format_table_empty():
    assert format_table([]) == "No posts found."


def test_format_json_roundtrip():
    sample = [{"text": "hi", "url": "https://x", "urn": "urn:li:activity:1"}]
    assert json.loads(format_json(sample)) == sample
