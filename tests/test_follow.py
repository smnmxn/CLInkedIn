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
NUMERIC_ID = "987654321"
VANITY = "alex-fairweather"
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
            payload={
                "elements": [
                    {
                        "entityUrn": f"urn:li:fsd_profile:{URN}",
                        "objectUrn": f"urn:li:member:{NUMERIC_ID}",
                        "publicIdentifier": VANITY,
                    }
                ]
            },
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


def test_follow_member_posts_sdui_follow_state():
    client = _FakeClient()
    follow_member(client, "simon-foo")

    # 1. Dash profile lookup to resolve numeric member ID.
    assert len(client.fetch_calls) == 1
    assert "voyagerIdentityDashProfiles" in client.fetch_calls[0]["uri"]
    assert "memberIdentity=simon-foo" in client.fetch_calls[0]["uri"]

    # 2. SDUI follow-state POST.
    assert len(client.post_calls) == 1
    call = client.post_calls[0]
    assert call["uri"].startswith("/flagship-web/rsc-action/actions/server-request")
    assert "addaUpdateFollowState" in call["uri"]
    assert call["base_request"] is True
    assert call["headers"]["content-type"] == "application/json"

    body = json.loads(call["data"])
    payload = body["serverRequest"]["requestedArguments"]["payload"]
    assert payload["followStateType"] == "FollowStateType_FOLLOW"
    assert payload["memberUrn"] == {"memberId": NUMERIC_ID}
    assert (
        payload["followStateBinding"]["key"]
        == f"urn:li:fsd_followingState:urn:li:member:{NUMERIC_ID}"
    )


def test_unfollow_member_posts_sdui_unfollow_state():
    client = _FakeClient()
    unfollow_member(client, "simon-foo")

    assert len(client.post_calls) == 1
    call = client.post_calls[0]
    assert "addaUpdateFollowState" in call["uri"]
    body = json.loads(call["data"])
    payload = body["serverRequest"]["requestedArguments"]["payload"]
    assert payload["followStateType"] == "FollowStateType_UNFOLLOW"
    assert payload["memberUrn"] == {"memberId": NUMERIC_ID}


def test_follow_member_surfaces_error():
    client = _FakeClient(default_post=_FakeResponse(429, '{"message":"rate limited"}'))
    with pytest.raises(FollowError) as exc:
        follow_member(client, "simon-foo")
    assert "HTTP 429" in str(exc.value)
    assert "rate limited" in str(exc.value)


def test_follow_member_acoa_urn_resolves_via_dash_lookup():
    """SDUI requires the numeric member ID, so even ACoA inputs must do a
    dash profile lookup — the old shortcut that skipped the lookup is gone."""
    client = _FakeClient()
    follow_member(client, URN)
    # Dash lookup happens regardless of input form (vanity vs ACoA).
    assert len(client.fetch_calls) == 1
    assert f"memberIdentity={URN}" in client.fetch_calls[0]["uri"]
    assert len(client.post_calls) == 1


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
#
# Modern path: /graphql?...voyagerSearchDashClusters via Curation Hub.
# The response wraps results in clusters > items > entityResult.


def _curation_member(urn=URN, name="Test User", headline="Eng @ Acme"):
    return {
        "entityUrn": f"urn:li:fsd_entityResultViewModel:(urn:li:fsd_profile:{urn},...)",
        "title": {"text": name},
        "primarySubtitle": {"text": headline},
        "navigationUrl": f"https://www.linkedin.com/in/{urn}/",
    }


def _curation_company(cid=COMPANY_ID, name="Acme Corp", headline="Software"):
    return {
        "entityUrn": f"urn:li:fsd_entityResultViewModel:(urn:li:fsd_company:{cid},...)",
        "title": {"text": name},
        "primarySubtitle": {"text": headline},
        "navigationUrl": f"https://www.linkedin.com/company/acme/",
    }


def _curation_payload(*entity_results) -> dict:
    """Wrap entity results in the SearchDashClustersByAll envelope.

    Mirrors the real response shape verified live 2026-05-07: cluster.items
    is a flat list of SearchItem objects, NOT {"elements": [...]}.
    """
    return {
        "data": {
            "searchDashClustersByAll": {
                "elements": [
                    {
                        "items": [
                            {"item": {"entityResult": er}} for er in entity_results
                        ]
                    }
                ]
            }
        }
    }


def test_list_following_paginates_until_short_page():
    page1 = _FakeResponse(200, payload=_curation_payload(_curation_member(), _curation_company()))
    page2 = _FakeResponse(200, payload=_curation_payload())  # empty cluster items
    client = _FakeClient(fetch_responses=[page1, page2])

    results = list_following(client, page_size=2)

    assert len(results) == 2
    assert results[0]["kind"] == "member"
    assert results[0]["public_id"] == URN
    assert results[1]["kind"] == "company"
    assert results[1]["public_id"] == COMPANY_ID

    # First call hits the GraphQL endpoint with start=0, count=2.
    first_uri = client.fetch_calls[0]["uri"]
    assert first_uri.startswith("/graphql?variables=")
    assert "start:0" in first_uri
    assert "count:2" in first_uri
    assert "MYNETWORK_CURATION_HUB" in first_uri
    assert "PEOPLE_FOLLOW" in first_uri


def test_list_following_respects_limit():
    page1 = _FakeResponse(
        200,
        payload=_curation_payload(*[_curation_member(name=f"User {i}") for i in range(5)]),
    )
    client = _FakeClient(fetch_responses=[page1])

    results = list_following(client, limit=3, page_size=10)

    assert len(results) == 3
    # Single fetch — page returned more than enough to satisfy limit.
    assert len(client.fetch_calls) == 1
    assert "count:3" in client.fetch_calls[0]["uri"]


def test_list_following_offset_passed_through():
    client = _FakeClient(fetch_responses=[_FakeResponse(200, payload=_curation_payload())])
    list_following(client, offset=42, page_size=10)
    assert "start:42" in client.fetch_calls[0]["uri"]


def test_list_following_offset_advances_across_pages():
    """When iterating past --limit, subsequent fetches start where the last
    fetch left off (start = offset + sum of returned items). Mirrors the
    pagination shape of fetch_connections."""
    page1 = _FakeResponse(
        200,
        payload=_curation_payload(*[_curation_member(name=f"Page1 User {i}") for i in range(2)]),
    )
    page2 = _FakeResponse(
        200,
        payload=_curation_payload(*[_curation_member(name=f"Page2 User {i}") for i in range(2)]),
    )
    page3 = _FakeResponse(200, payload=_curation_payload())  # short page → stop
    client = _FakeClient(fetch_responses=[page1, page2, page3])

    results = list_following(client, offset=10, page_size=2)

    assert len(results) == 4
    # Three URLs: start=10, start=12, start=14 — proves we add page-length
    # to the previous start, not just `offset`.
    starts = [
        int(call["uri"].split("start:")[1].split(",")[0])
        for call in client.fetch_calls
    ]
    assert starts == [10, 12, 14]


def test_list_following_default_page_size_matches_connections():
    """Default page_size should match fetch_connections (40) so iterating a
    full follow list doesn't make 4× as many Voyager calls."""
    client = _FakeClient(fetch_responses=[_FakeResponse(200, payload=_curation_payload())])
    list_following(client)  # no explicit page_size
    assert "count:40" in client.fetch_calls[0]["uri"]


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


def test_list_following_company_result_type():
    """result_type='COMPANIES' is reflected in the URL filter."""
    client = _FakeClient(fetch_responses=[_FakeResponse(200, payload=_curation_payload())])
    list_following(client, result_type="COMPANIES", page_size=5)
    assert "COMPANIES" in client.fetch_calls[0]["uri"]


def test_list_following_includes_canonical_url():
    """Each result has a `url` field shaped like the connections output."""
    client = _FakeClient(
        fetch_responses=[
            _FakeResponse(200, payload=_curation_payload(_curation_member(), _curation_company()))
        ]
    )
    results = list_following(client, page_size=2)
    assert results[0]["url"] == f"https://www.linkedin.com/in/{URN}/"
    assert results[1]["url"] == f"https://www.linkedin.com/company/{COMPANY_ID}/"


# ---------- formatters ----------


def test_format_json_round_trips():
    items = [{"name": "X", "kind": "member", "public_id": URN, "headline": "H"}]
    rendered = format_json(items)
    assert json.loads(rendered) == items


def test_format_table_renders_member_and_company():
    items = [
        {
            "name": "Alice",
            "kind": "member",
            "public_id": URN,
            "headline": "Eng",
            "url": f"https://www.linkedin.com/in/{URN}/",
        },
        {
            "name": "Acme",
            "kind": "company",
            "public_id": COMPANY_ID,
            "headline": "SaaS",
            "url": f"https://www.linkedin.com/company/{COMPANY_ID}/",
        },
    ]
    out = format_table(items)
    assert "Alice" in out
    assert "Eng" in out
    assert f"https://www.linkedin.com/in/{URN}/" in out
    assert "Acme" in out
    assert "SaaS" in out
    assert f"https://www.linkedin.com/company/{COMPANY_ID}/" in out
    # No more [member] / [company] tags — match connections format style.
    assert "[member]" not in out
    assert "[company]" not in out


def test_format_table_empty():
    assert format_table([]) == "Not following anyone."
