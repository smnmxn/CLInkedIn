import json
from pathlib import Path

import pytest

from datetime import timedelta

from clinkedin.profile import (
    ProfileError,
    _extract_post_fields,
    _format_period,
    _normalize_dash_profile,
    fetch_profile,
    fetch_profile_posts,
    filter_posts_by_age,
    format_json,
    format_posts_json,
    format_posts_text,
    format_text,
    parse_age,
)


FIXTURES = Path(__file__).parent / "fixtures"


def _load_dash_response():
    return json.loads((FIXTURES / "dash_profile_response.json").read_text())


def _load_posts_response():
    return json.loads((FIXTURES / "profile_posts_response.json").read_text())


SAMPLE_PROFILE = {
    "firstName": "Ada",
    "lastName": "Lovelace",
    "headline": "Mathematician at Analytical Engine Co",
    "locationName": "London, UK",
    "industryName": "Computer Software",
    "summary": "I write algorithms.\nNote: this is a multi-line summary.",
    "experience": [
        {
            "title": "Senior Engineer",
            "companyName": "Analytical Engine Co",
            "locationName": "London",
            "timePeriod": {"startDate": {"year": 2020, "month": 5}},
        },
        {
            "title": "Engineer",
            "companyName": "Mechanical Co",
            "timePeriod": {
                "startDate": {"year": 2018},
                "endDate": {"year": 2020, "month": 4},
            },
        },
    ],
    "education": [
        {
            "schoolName": "Cambridge",
            "degreeName": "Diploma",
            "fieldOfStudy": "Mathematics",
            "timePeriod": {
                "startDate": {"year": 1830},
                "endDate": {"year": 1832},
            },
        },
    ],
    "public_id": "ada-lovelace",
}


def test_format_text_includes_all_sections():
    out = format_text(SAMPLE_PROFILE)
    assert "Ada Lovelace" in out
    assert "Mathematician at Analytical Engine Co" in out
    assert "London, UK · Computer Software" in out
    assert "About:" in out
    assert "I write algorithms." in out
    assert "Experience:" in out
    assert "- Senior Engineer · Analytical Engine Co · 5/2020 – Present · London" in out
    assert "- Engineer · Mechanical Co · 2018 – 4/2020" in out
    assert "Education:" in out
    assert "- Cambridge · Diploma · Mathematics · 1830 – 1832" in out
    assert "Profile: https://www.linkedin.com/in/ada-lovelace/" in out


def test_format_text_minimal_profile():
    minimal = {"firstName": "X", "lastName": "Y"}
    out = format_text(minimal)
    assert out == "X Y"


def test_format_text_empty_profile_falls_back_to_unknown():
    out = format_text({})
    assert out == "(unknown)"


def test_format_text_dash_profile_includes_hint():
    """Dash profiles have no experience/education — show a hint instead."""
    dash = _normalize_dash_profile(_load_dash_response()["elements"][0])
    out = format_text(dash)
    assert "Ada Lovelace" in out
    assert "Mathematician at Analytical Engine Co" in out
    assert "I write algorithms." in out
    assert "Profile: https://www.linkedin.com/in/ada-lovelace/" in out
    assert "Experience and education sections require a separate API call" in out


def test_format_json_roundtrip():
    out = format_json(SAMPLE_PROFILE)
    assert json.loads(out) == SAMPLE_PROFILE


@pytest.mark.parametrize(
    "period,expected",
    [
        (None, ""),
        ({}, ""),
        ({"startDate": {"year": 2020}}, "2020 – Present"),
        (
            {"startDate": {"year": 2020, "month": 5}, "endDate": {"year": 2022, "month": 8}},
            "5/2020 – 8/2022",
        ),
        ({"endDate": {"year": 2022}}, "2022"),
    ],
)
def test_format_period(period, expected):
    assert _format_period(period) == expected


class _FakeResponse:
    def __init__(self, status_code=200, body=None, body_text=None):
        self.status_code = status_code
        self._body = body
        self._text = body_text
        self.url = "https://www.linkedin.com/voyager/api/<test>"
        self.headers = {}

    @property
    def text(self):
        if self._text is not None:
            return self._text
        return json.dumps(self._body) if self._body is not None else ""

    def json(self):
        if self._text is not None and self._body is None:
            raise ValueError("not json")
        return self._body


class _FakeClient:
    """Mocks the bare-metal _fetch interface we now use directly.

    Routes by URI substring: '/voyagerIdentityDashProfiles' returns dash_response,
    '/identity/profileUpdatesV2' returns posts_response. Either can be a
    _FakeResponse to simulate non-200 / malformed responses.
    """

    def __init__(self, dash=None, posts=None):
        self._dash = dash
        self._posts = posts
        self.calls = []

    def _fetch(self, uri, **kwargs):
        self.calls.append({"uri": uri, "params": kwargs.get("params")})
        if "voyagerIdentityDashProfiles" in uri:
            target = self._dash
        elif "profileUpdatesV2" in uri:
            target = self._posts
        else:
            raise AssertionError(f"unexpected fetch: {uri}")
        if isinstance(target, _FakeResponse):
            return target
        return _FakeResponse(200, target)


def test_normalize_dash_profile_extracts_top_level_fields():
    elem = _load_dash_response()["elements"][0]
    norm = _normalize_dash_profile(elem)
    assert norm["firstName"] == "Ada"
    assert norm["lastName"] == "Lovelace"
    assert norm["headline"].startswith("Mathematician")
    assert norm["public_id"] == "ada-lovelace"
    assert norm["profile_urn"].startswith("urn:li:fsd_profile:ACoA")
    assert norm["profile_id"].startswith("ACoA")
    assert norm["member_urn"] == "urn:li:member:99999999"
    assert "I write algorithms." in norm["summary"]


def test_normalize_dash_profile_falls_back_to_multilocale():
    """If top-level firstName/headline absent, multiLocale.en_US is used."""
    elem = {
        "entityUrn": "urn:li:fsd_profile:ACoAFOO",
        "publicIdentifier": "x",
        "multiLocaleFirstName": {"en_US": "Charles"},
        "multiLocaleLastName": {"en_US": "Babbage"},
        "multiLocaleHeadline": {"en_US": "Inventor"},
    }
    norm = _normalize_dash_profile(elem)
    assert norm["firstName"] == "Charles"
    assert norm["lastName"] == "Babbage"
    assert norm["headline"] == "Inventor"


def test_fetch_profile_success_uses_dash_endpoint():
    dash = _load_dash_response()
    client = _FakeClient(dash=dash)
    result = fetch_profile(client, "ada-lovelace")
    assert result["firstName"] == "Ada"
    assert result["public_id"] == "ada-lovelace"
    assert result["profile_urn"].startswith("urn:li:fsd_profile:")
    assert "_dash_raw" in result
    assert client.calls[0]["uri"].startswith(
        "/voyagerIdentityDashProfiles?q=memberIdentity"
    )


def test_fetch_profile_410_raises():
    client = _FakeClient(dash=_FakeResponse(410, {"status": 410}))
    with pytest.raises(ProfileError, match="HTTP 410"):
        fetch_profile(client, "ada-lovelace")


def test_fetch_profile_no_elements_raises():
    client = _FakeClient(dash={"elements": [], "paging": {}})
    with pytest.raises(ProfileError, match="No profile found"):
        fetch_profile(client, "ada-lovelace")


def test_fetch_profile_non_json_raises():
    client = _FakeClient(dash=_FakeResponse(200, body=None, body_text="<html>"))
    with pytest.raises(ProfileError, match="non-JSON"):
        fetch_profile(client, "ada-lovelace")


def test_fetch_profile_posts_success():
    client = _FakeClient(
        dash=_load_dash_response(),
        posts=_load_posts_response(),
    )
    result = fetch_profile_posts(client, "ada-lovelace", limit=25)
    assert isinstance(result, list)
    assert len(result) == 3
    # Two calls: one to resolve URN, one to fetch posts
    assert len(client.calls) == 2
    assert "voyagerIdentityDashProfiles" in client.calls[0]["uri"]
    assert "profileUpdatesV2" in client.calls[1]["uri"]
    params = client.calls[1]["params"]
    assert params["count"] == 25
    assert params["profileUrn"].startswith("urn:li:fsd_profile:")


def test_fetch_profile_posts_empty_is_ok():
    client = _FakeClient(
        dash=_load_dash_response(),
        posts={"elements": [], "metadata": {}, "paging": {}},
    )
    assert fetch_profile_posts(client, "ada-lovelace") == []


def test_fetch_profile_posts_410_on_dash_propagates():
    client = _FakeClient(dash=_FakeResponse(410, {"status": 410}))
    with pytest.raises(ProfileError, match="HTTP 410"):
        fetch_profile_posts(client, "ada-lovelace")


def test_fetch_profile_posts_non_200_on_posts_endpoint_raises():
    client = _FakeClient(
        dash=_load_dash_response(),
        posts=_FakeResponse(429, {"status": 429}),
    )
    with pytest.raises(ProfileError, match="HTTP 429"):
        fetch_profile_posts(client, "ada-lovelace")


def test_extract_post_fields_uses_relative_date_from_subdescription():
    posts = _load_posts_response()["elements"]
    f = _extract_post_fields(posts[0])
    assert f["urn"].startswith("urn:li:activity:")
    assert f["url"].startswith("https://www.linkedin.com/feed/update/")
    # Posts response has no top-level createdAt; date comes from subDescription
    assert f["date"]  # e.g. "1d", "2w"
    assert "•" not in f["date"]  # bullet stripped


def test_extract_post_fields_missing_commentary():
    posts = _load_posts_response()["elements"]
    f = _extract_post_fields(posts[1])  # forced-empty commentary in fixture
    assert f["text"] == ""


def test_extract_post_fields_handles_empty_dict():
    f = _extract_post_fields({})
    assert f == {
        "date": "",
        "text": "",
        "like_count": 0,
        "comment_count": 0,
        "urn": "",
        "url": "",
    }


def test_extract_post_fields_uses_createdAt_when_present():
    """Older fixtures (and possibly some Voyager responses) have createdAt."""
    post = {
        "updateMetadata": {"urn": "urn:li:activity:1"},
        "commentary": {"text": {"text": "hello"}},
        "createdAt": 1776729600000,  # 2026-04-21 UTC
    }
    f = _extract_post_fields(post)
    assert f["date"] == "2026-04-21"


def test_format_posts_text_renders_one_line_per_post():
    posts = _load_posts_response()["elements"]
    out = format_posts_text(posts)
    lines = out.split("\n")
    assert len(lines) == 3
    # First post has commentary text
    assert "likes" in lines[0]
    assert "comments" in lines[0]
    assert "urn:li:activity:" in lines[0]


def test_format_posts_text_uses_no_text_placeholder_for_image_only():
    posts = _load_posts_response()["elements"]
    out = format_posts_text(posts)
    image_line = out.split("\n")[1]
    assert '"(no text)"' in image_line


def test_format_posts_text_truncates_long_text():
    long = "x" * 200
    post = {
        "updateMetadata": {"urn": "urn:li:activity:1"},
        "commentary": {"text": {"text": long}},
    }
    out = format_posts_text([post])
    assert "…" in out
    quoted = out.split(" · ")[1]
    assert len(quoted) <= 122


def test_format_posts_text_collapses_newlines():
    post = {
        "updateMetadata": {"urn": "urn:li:activity:1"},
        "commentary": {"text": {"text": "line one\nline two"}},
    }
    out = format_posts_text([post])
    assert "line one line two" in out


def test_format_posts_text_empty_list():
    assert format_posts_text([]) == ""


def test_format_posts_text_full_does_not_truncate():
    long = "x" * 500 + "\nsecond paragraph"
    post = {
        "updateMetadata": {"urn": "urn:li:activity:1"},
        "commentary": {"text": {"text": long}},
        "actor": {"subDescription": {"text": "1d"}},
    }
    out = format_posts_text([post], full=True)
    assert "x" * 500 in out
    assert "second paragraph" in out
    assert "…" not in out


def test_format_posts_text_full_uses_separator_between_posts():
    posts = _load_posts_response()["elements"]
    out = format_posts_text(posts, full=True)
    # 3 posts → 2 separators
    assert out.count("\n---\n") == 2


def test_format_posts_json_roundtrip():
    posts = _load_posts_response()["elements"]
    out = format_posts_json(posts)
    assert json.loads(out) == posts


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1d", timedelta(days=1)),
        ("7d", timedelta(days=7)),
        ("2w", timedelta(days=14)),
        ("3mo", timedelta(days=90)),
        ("1y", timedelta(days=365)),
        ("5h", timedelta(hours=5)),
        ("30m", timedelta(minutes=30)),
        ("1 day ago", timedelta(days=1)),
        ("3 weeks", timedelta(days=21)),
        ("6 months ago", timedelta(days=180)),
        ("Now", timedelta(0)),
        ("just now", timedelta(0)),
    ],
)
def test_parse_age_known_forms(raw, expected):
    assert parse_age(raw) == expected


@pytest.mark.parametrize("raw", ["", "yesterday", "soon", "abc", None])
def test_parse_age_returns_none_for_unparseable(raw):
    assert parse_age(raw) is None


def test_filter_posts_by_age_keeps_recent_drops_old():
    posts = [
        {"actor": {"subDescription": {"text": "1d"}}, "id": "a"},
        {"actor": {"subDescription": {"text": "2w"}}, "id": "b"},
        {"actor": {"subDescription": {"text": "3mo"}}, "id": "c"},
    ]
    out = filter_posts_by_age(posts, timedelta(days=7))
    assert [p["id"] for p in out] == ["a"]


def test_filter_posts_by_age_drops_unparseable():
    """Posts whose age can't be parsed are dropped — better to under-include."""
    posts = [
        {"actor": {"subDescription": {"text": "1d"}}, "id": "a"},
        {"actor": {"subDescription": {"text": "garbage"}}, "id": "b"},
        {"id": "c"},  # no actor at all
    ]
    out = filter_posts_by_age(posts, timedelta(days=30))
    assert [p["id"] for p in out] == ["a"]


def test_filter_posts_by_age_uses_accessibility_text_when_present():
    posts = [
        {"actor": {"subDescription": {"accessibilityText": "5 days ago", "text": "5d"}}, "id": "a"},
        {"actor": {"subDescription": {"accessibilityText": "2 months ago", "text": "2mo"}}, "id": "b"},
    ]
    out = filter_posts_by_age(posts, timedelta(days=7))
    assert [p["id"] for p in out] == ["a"]
