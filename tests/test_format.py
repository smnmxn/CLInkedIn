import json

import pytest

from clinkedin.connections import (
    ConnectionsError,
    _parse_company,
    fetch_connections,
    format_json,
    format_table,
)


SAMPLE = [
    {"name": "Ada Lovelace", "jobtitle": "Analyst at Analytical Engine Co", "location": "London"},
    {"name": "Grace Hopper", "jobtitle": "Rear Admiral", "location": "Arlington, VA"},
    {"name": "No Headline", "jobtitle": "", "location": ""},
]


SAMPLE_WITH_URL = [
    {
        "name": "Ada Lovelace",
        "jobtitle": "Analyst at Analytical Engine Co",
        "location": "London",
        "url": "https://www.linkedin.com/in/ada-lovelace/",
        "company": "Analytical Engine Co",
    },
    {
        "name": "Bare Minimum",
        "jobtitle": "",
        "location": "",
        "url": "https://www.linkedin.com/in/ACoAAB/",
    },
]


def test_format_table_renders_rows():
    out = format_table(SAMPLE)
    lines = out.splitlines()
    assert lines[0] == "Ada Lovelace · Analyst at Analytical Engine Co · London"
    assert lines[1] == "Grace Hopper · Rear Admiral · Arlington, VA"
    assert lines[2] == "No Headline"


def test_format_table_includes_company_and_url_when_present():
    out = format_table(SAMPLE_WITH_URL)
    lines = out.splitlines()
    assert (
        lines[0]
        == "Ada Lovelace · Analyst at Analytical Engine Co · London · Analytical Engine Co · https://www.linkedin.com/in/ada-lovelace/"
    )
    assert lines[1] == "Bare Minimum · https://www.linkedin.com/in/ACoAAB/"


def test_format_table_empty():
    assert format_table([]) == "No connections found."


def test_format_json_roundtrip():
    out = format_json(SAMPLE)
    assert json.loads(out) == SAMPLE


@pytest.mark.parametrize(
    "headline,expected",
    [
        ("Senior Engineer at Acme Corp", "Acme Corp"),
        ("CEO @ Acme", "Acme"),
        ("Engineer At Google", "Google"),
        ("Founder & CEO at Foo Bar Inc · Mentor at Y Combinator", "Foo Bar Inc"),
        ("CEO at A | Advisor at B", "A"),
        ("Engineer at Google - 5y", "Google"),
        ("Building the future of AI", None),
        ("", None),
        (None, None),
        ("Engineer at ", None),
        ("at Tesla", None),
    ],
)
def test_parse_company(headline, expected):
    assert _parse_company(headline) == expected


# ---------- fetch_connections (dash endpoint) ----------


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, responses: list[_FakeResponse]):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def _fetch(self, uri, **kwargs):
        self.calls.append({"uri": uri, **kwargs})
        if self._responses:
            return self._responses.pop(0)
        return _FakeResponse(200, payload={"elements": []})


def _conn_elem(
    urn_id="ACoAAA",
    public_id="ada-lovelace",
    first="Ada",
    last="Lovelace",
    headline="Analyst at Analytical Engine Co",
    location="London",
):
    return {
        "entityUrn": (
            f"urn:li:fsd_connection:(urn:li:fsd_profile:VIEWER,"
            f"urn:li:fsd_profile:{urn_id})"
        ),
        "connectedMemberResolutionResult": {
            "entityUrn": f"urn:li:fsd_profile:{urn_id}",
            "publicIdentifier": public_id,
            "firstName": first,
            "lastName": last,
            "headline": headline,
            "geoLocation": {
                "geo": {"defaultLocalizedName": {"value": location}}
            }
            if location
            else None,
        },
    }


def test_fetch_connections_hits_dash_endpoint_with_decoration():
    client = _FakeClient([_FakeResponse(200, payload={"elements": []})])
    fetch_connections(client, limit=5, offset=10)

    call = client.calls[0]
    assert call["uri"] == "/relationships/dash/connections"
    assert call["params"]["q"] == "search"
    assert call["params"]["start"] == 10
    assert call["params"]["count"] == 5
    assert "ConnectionListWithProfile" in call["params"]["decorationId"]


def test_fetch_connections_normalizes_resolved_member():
    client = _FakeClient(
        [_FakeResponse(200, payload={"elements": [_conn_elem()]})]
    )
    results = fetch_connections(client, limit=1)
    assert len(results) == 1
    ada = results[0]
    assert ada["name"] == "Ada Lovelace"
    assert ada["jobtitle"] == "Analyst at Analytical Engine Co"
    assert ada["location"] == "London"
    assert ada["urn_id"] == "ACoAAA"
    assert ada["url"] == "https://www.linkedin.com/in/ada-lovelace/"
    assert ada["company"] == "Analytical Engine Co"
    assert ada["distance"] == "DISTANCE_1"


def test_fetch_connections_falls_back_to_urn_url_when_no_public_id():
    elem = _conn_elem(
        urn_id="ACoABB", public_id=None, first="No", last="Slug", location=None
    )
    client = _FakeClient([_FakeResponse(200, payload={"elements": [elem]})])
    results = fetch_connections(client, limit=1)
    assert results[0]["url"] == "https://www.linkedin.com/in/ACoABB/"
    assert results[0]["location"] is None


def test_fetch_connections_paginates():
    page1 = _FakeResponse(
        200,
        payload={
            "elements": [
                _conn_elem(urn_id=f"ACoA{i}", public_id=f"user-{i}", first=f"U{i}")
                for i in range(40)
            ]
        },
    )
    page2 = _FakeResponse(
        200,
        payload={
            "elements": [
                _conn_elem(urn_id=f"ACoA{40 + i}", public_id=f"user-{40 + i}", first=f"U{40 + i}")
                for i in range(10)
            ]
        },
    )
    client = _FakeClient([page1, page2])
    results = fetch_connections(client)
    assert len(results) == 50
    # Two pages fetched: start=0 then start=40.
    assert client.calls[0]["params"]["start"] == 0
    assert client.calls[1]["params"]["start"] == 40


def test_fetch_connections_respects_limit_across_pages():
    page1 = _FakeResponse(
        200,
        payload={
            "elements": [
                _conn_elem(urn_id=f"ACoA{i}", public_id=f"user-{i}", first=f"U{i}")
                for i in range(40)
            ]
        },
    )
    client = _FakeClient([page1])
    results = fetch_connections(client, limit=5, page_size=40)
    assert len(results) == 5
    # Single fetch — count clamped to 5.
    assert len(client.calls) == 1
    assert client.calls[0]["params"]["count"] == 5


def test_fetch_connections_zero_limit_returns_empty_no_calls():
    client = _FakeClient([])
    assert fetch_connections(client, limit=0) == []
    assert client.calls == []


def test_fetch_connections_http_error_raises():
    client = _FakeClient(
        [_FakeResponse(401, '{"message":"session expired"}')]
    )
    with pytest.raises(ConnectionsError, match="HTTP 401"):
        fetch_connections(client)


def test_fetch_connections_skips_elements_without_resolved_member():
    elements = [
        {"entityUrn": "urn:li:fsd_connection:..."},  # no resolution result
        _conn_elem(),
    ]
    client = _FakeClient(
        [_FakeResponse(200, payload={"elements": elements})]
    )
    results = fetch_connections(client, limit=10)
    assert len(results) == 1
    assert results[0]["name"] == "Ada Lovelace"
