import argparse

from clinkedin.cli import _cmd_view


def _ns(**kwargs) -> argparse.Namespace:
    defaults = {
        "url": "https://www.linkedin.com/in/ada-lovelace/",
        "json": False,
        "posts": False,
        "limit": 10,
        "full": False,
        "since": None,
        "debug": False,
        "output": None,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _no_client():
    raise AssertionError("should not build a client before URL validation")


def test_cmd_view_company_without_posts_returns_2(monkeypatch, capsys):
    monkeypatch.setattr("clinkedin.cli.make_client", _no_client)
    rc = _cmd_view(_ns(url="https://www.linkedin.com/company/acme/", posts=False))
    assert rc == 2
    err = capsys.readouterr().err
    assert "only support --posts" in err


def test_cmd_view_bad_url_returns_2(monkeypatch, capsys):
    monkeypatch.setattr("clinkedin.cli.make_client", _no_client)
    rc = _cmd_view(_ns(url="https://example.com/foo", posts=True))
    assert rc == 2


def test_cmd_view_company_posts_routes_to_company_fetch(monkeypatch, capsys):
    monkeypatch.setattr("clinkedin.cli.make_client", lambda: object())

    seen = {}

    def _fake_company_posts(client, slug, limit):
        seen["slug"] = slug
        seen["limit"] = limit
        return [
            {
                "updateMetadata": {"urn": "urn:li:activity:9001"},
                "commentary": {"text": {"text": "hello from acme"}},
                "socialDetail": {
                    "totalSocialActivityCounts": {"numLikes": 3, "numComments": 1}
                },
                "actor": {"subDescription": {"text": "1d •  "}},
            }
        ]

    def _fail_profile_posts(*a, **k):
        raise AssertionError("company URL must not hit the member posts path")

    monkeypatch.setattr("clinkedin.cli.fetch_company_posts", _fake_company_posts)
    monkeypatch.setattr("clinkedin.cli.fetch_profile_posts", _fail_profile_posts)

    rc = _cmd_view(
        _ns(url="https://www.linkedin.com/company/acme/", posts=True, limit=5)
    )
    assert rc == 0
    assert seen == {"slug": "acme", "limit": 5}
    assert "hello from acme" in capsys.readouterr().out


def test_cmd_view_member_posts_routes_to_member_fetch(monkeypatch, capsys):
    monkeypatch.setattr("clinkedin.cli.make_client", lambda: object())

    seen = {}

    def _fake_profile_posts(client, public_id, limit):
        seen["public_id"] = public_id
        return [
            {
                "updateMetadata": {"urn": "urn:li:activity:42"},
                "commentary": {"text": {"text": "hello from ada"}},
                "socialDetail": {
                    "totalSocialActivityCounts": {"numLikes": 0, "numComments": 0}
                },
                "actor": {"subDescription": {"text": "2d •  "}},
            }
        ]

    def _fail_company_posts(*a, **k):
        raise AssertionError("member URL must not hit the company posts path")

    monkeypatch.setattr("clinkedin.cli.fetch_profile_posts", _fake_profile_posts)
    monkeypatch.setattr("clinkedin.cli.fetch_company_posts", _fail_company_posts)

    rc = _cmd_view(_ns(url="https://www.linkedin.com/in/ada-lovelace/", posts=True))
    assert rc == 0
    assert seen["public_id"] == "ada-lovelace"
    assert "hello from ada" in capsys.readouterr().out
