import pytest

from clinkedin.invite import parse_profile_url


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.linkedin.com/in/simon-foo/", "simon-foo"),
        ("https://www.linkedin.com/in/simon-foo", "simon-foo"),
        ("https://www.linkedin.com/in/simon-foo/?miniProfileUrn=xyz", "simon-foo"),
        ("https://uk.linkedin.com/in/simon-foo/", "simon-foo"),
        ("https://www.linkedin.com/mwlite/in/simon-foo", "simon-foo"),
        ("  https://www.linkedin.com/in/simon-foo/  ", "simon-foo"),
    ],
)
def test_parse_profile_url_accepts(url, expected):
    assert parse_profile_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/in/foo",
        "https://www.linkedin.com/company/acme",
        "https://www.linkedin.com/feed/",
        "not-a-url",
    ],
)
def test_parse_profile_url_rejects(url):
    with pytest.raises(ValueError):
        parse_profile_url(url)
