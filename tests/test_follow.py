import json

import pytest

from clinkedin.follow import (
    FollowError,
    follow_company,
    follow_member,
    format_json,
    format_table,
    list_following,
    parse_follow_url,
    unfollow_company,
    unfollow_member,
)


URN = "ACoAACX1hoMBvWqTY21JGe0z91mnmjmLy9Wen4w"
COMPANY_ID = "1337420"


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


class _FakeClient:
    """Stand-in for linkedin_api.Linkedin, recording fetch and post calls.

    `fetch_responses` and `post_responses` are queues. If empty, `_default_*`
    is returned so simple tests don't need to wire every probe.
    """

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
        self._default_post = default_post or _FakeResponse(200)
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


# ---------- parse_follow_url ----------


@pytest.mark.parametrize(
    "url,kind,slug",
    [
        ("https://www.linkedin.com/in/simon-foo/", "member", "simon-foo"),
        ("https://www.linkedin.com/in/simon-foo", "member", "simon-foo"),
        ("https://uk.linkedin.com/in/simon-foo/", "member", "simon-foo"),
        ("https://www.linkedin.com/mwlite/in/simon-foo", "member", "simon-foo"),
        ("https://www.linkedin.com/company/acme/", "company", "acme"),
        ("https://www.linkedin.com/company/together-ai", "company", "together-ai"),
        ("  https://www.linkedin.com/company/acme/  ", "company", "acme"),
    ],
)
def test_parse_follow_url_accepts(url, kind, slug):
    assert parse_follow_url(url) == (kind, slug)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/in/foo",
        "https://www.linkedin.com/feed/",
        "https://www.linkedin.com/school/mit/",
        "not-a-url",
    ],
)
def test_parse_follow_url_rejects(url):
    with pytest.raises(ValueError):
        parse_follow_url(url)


# ---------- follow_member / unfollow_member ----------


def test_follow_member_posts_followByEntityUrn():
    client = _FakeClient()
    follow_member(client, "simon-foo")

    assert len(client.post_calls) == 1
    call = client.post_calls[0]
    assert call["uri"] == "/feed/follows?action=followByEntityUrn"
    assert call["headers"]["accept"].startswith(
        "application/vnd.linkedin.normalized+json"
    )
    assert json.loads(call["data"]) == {"urn": f"urn:li:fs_followingInfo:{URN}"}


def test_unfollow_member_posts_unfollowByEntityUrn():
    client = _FakeClient()
    unfollow_member(client, "simon-foo")

    assert client.post_calls[0]["uri"] == "/feed/follows?action=unfollowByEntityUrn"
    assert json.loads(client.post_calls[0]["data"]) == {
        "urn": f"urn:li:fs_followingInfo:{URN}"
    }


def test_follow_member_surfaces_error():
    client = _FakeClient(default_post=_FakeResponse(429, '{"message":"rate limited"}'))
    with pytest.raises(FollowError) as exc:
        follow_member(client, "simon-foo")
    assert "HTTP 429" in str(exc.value)
    assert "rate limited" in str(exc.value)


# ---------- follow_company / unfollow_company ----------


def _company_lookup_response():
    return _FakeResponse(
        200,
        payload={
            "elements": [
                {"entityUrn": f"urn:li:fs_normalized_company:{COMPANY_ID}"}
            ]
        },
    )


def test_follow_company_resolves_then_posts():
    client = _FakeClient(fetch_responses=[_company_lookup_response()])
    follow_company(client, "acme")

    assert len(client.fetch_calls) == 1
    fetch = client.fetch_calls[0]
    assert fetch["uri"] == "/organization/companies"
    assert fetch["params"] == {"q": "universalName", "universalName": "acme"}

    assert len(client.post_calls) == 1
    post = client.post_calls[0]
    assert post["uri"] == "/feed/follows?action=followByEntityUrn"
    assert json.loads(post["data"]) == {
        "urn": f"urn:li:fs_followingInfo:{COMPANY_ID}"
    }


def test_unfollow_company_uses_unfollow_action():
    client = _FakeClient(fetch_responses=[_company_lookup_response()])
    unfollow_company(client, "acme")

    assert (
        client.post_calls[0]["uri"]
        == "/feed/follows?action=unfollowByEntityUrn"
    )


def test_follow_company_missing_company():
    client = _FakeClient(
        fetch_responses=[_FakeResponse(200, payload={"elements": []})]
    )
    with pytest.raises(FollowError, match="No company found for acme"):
        follow_company(client, "acme")


def test_follow_company_lookup_http_error():
    client = _FakeClient(fetch_responses=[_FakeResponse(404, "")])
    with pytest.raises(FollowError, match="Company lookup failed"):
        follow_company(client, "acme")


# ---------- list_following ----------


def _member_elem(urn=URN, name="Test User", headline="Eng @ Acme"):
    return {
        "followeeUrn": f"urn:li:fsd_profile:{urn}",
        "name": name,
        "headline": headline,
    }


def _company_elem(cid=COMPANY_ID, name="Acme Corp", headline="Software"):
    return {
        "followeeUrn": f"urn:li:fsd_company:{cid}",
        "name": name,
        "headline": headline,
    }


def test_list_following_paginates_until_short_page():
    page1 = _FakeResponse(
        200,
        payload={"elements": [_member_elem(), _company_elem()]},
    )
    page2 = _FakeResponse(200, payload={"elements": []})
    client = _FakeClient(fetch_responses=[page1, page2])

    results = list_following(client, page_size=2)

    assert len(results) == 2
    assert results[0]["kind"] == "member"
    assert results[0]["public_id"] == URN
    assert results[1]["kind"] == "company"
    assert results[1]["public_id"] == COMPANY_ID

    # First call asks for start=0; either we stop because the 2-element page
    # filled the request and the next page is empty, OR we stop because the
    # page was short. Either way we should hit the endpoint at least once.
    assert client.fetch_calls[0]["uri"] == "/feed/dash/followingStates"
    assert client.fetch_calls[0]["params"]["q"] == "followingStates"
    assert client.fetch_calls[0]["params"]["start"] == 0
    assert client.fetch_calls[0]["params"]["count"] == 2


def test_list_following_respects_limit():
    page1 = _FakeResponse(
        200,
        payload={
            "elements": [_member_elem(name=f"User {i}") for i in range(5)]
        },
    )
    client = _FakeClient(fetch_responses=[page1])

    results = list_following(client, limit=3, page_size=10)

    assert len(results) == 3
    # We asked for count=3 (min of page_size and limit) — single fetch.
    assert len(client.fetch_calls) == 1
    assert client.fetch_calls[0]["params"]["count"] == 3


def test_list_following_offset_passed_through():
    client = _FakeClient(
        fetch_responses=[_FakeResponse(200, payload={"elements": []})]
    )
    list_following(client, offset=42, page_size=10)
    assert client.fetch_calls[0]["params"]["start"] == 42


def test_list_following_http_error():
    client = _FakeClient(
        fetch_responses=[_FakeResponse(401, '{"message":"session expired"}')]
    )
    with pytest.raises(FollowError, match="HTTP 401"):
        list_following(client)


def test_list_following_zero_limit_returns_empty():
    client = _FakeClient()
    assert list_following(client, limit=0) == []
    assert client.fetch_calls == []


# ---------- formatters ----------


def test_format_json_round_trips():
    items = [{"name": "X", "kind": "member", "public_id": URN, "headline": "H"}]
    rendered = format_json(items)
    assert json.loads(rendered) == items


def test_format_table_renders_member_and_company():
    items = [
        {"name": "Alice", "kind": "member", "public_id": URN, "headline": "Eng"},
        {"name": "Acme", "kind": "company", "public_id": COMPANY_ID, "headline": "SaaS"},
    ]
    out = format_table(items)
    assert "Alice" in out
    assert "[member]" in out
    assert f"https://www.linkedin.com/in/{URN}/" in out
    assert "Acme" in out
    assert "[company]" in out
    assert f"https://www.linkedin.com/company/{COMPANY_ID}/" in out


def test_format_table_empty():
    assert format_table([]) == "Not following anyone."
