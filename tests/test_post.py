import argparse

import pytest

from clinkedin.cli import _cmd_post
from clinkedin.post import (
    MAX_BODY_LEN,
    MAX_IMAGE_BYTES,
    PostError,
    create_post,
    validate_body,
    validate_image_path,
)


# ---------- validate_body ----------


def test_validate_body_strips_trailing_whitespace():
    assert validate_body("hello   \n") == "hello"


def test_validate_body_empty_raises():
    with pytest.raises(PostError, match="empty"):
        validate_body("   \n  ")


def test_validate_body_too_long_raises():
    with pytest.raises(PostError, match=f"{MAX_BODY_LEN + 1}/{MAX_BODY_LEN}"):
        validate_body("x" * (MAX_BODY_LEN + 1))


def test_validate_body_at_limit_passes():
    body = "x" * MAX_BODY_LEN
    assert validate_body(body) == body


# ---------- validate_image_path ----------


def test_validate_image_path_none_returns_none():
    assert validate_image_path(None) is None


def test_validate_image_path_missing_file_raises(tmp_path):
    bad = tmp_path / "does-not-exist.png"
    with pytest.raises(PostError, match="not found"):
        validate_image_path(str(bad))


def test_validate_image_path_wrong_extension_raises(tmp_path):
    txt = tmp_path / "note.txt"
    txt.write_text("hi")
    with pytest.raises(PostError, match="not supported"):
        validate_image_path(str(txt))


def test_validate_image_path_oversize_raises(tmp_path):
    big = tmp_path / "big.png"
    big.write_bytes(b"\x00" * (MAX_IMAGE_BYTES + 1))
    with pytest.raises(PostError, match="too large"):
        validate_image_path(str(big))


def test_validate_image_path_png_jpg_jpeg_accepted(tmp_path):
    for name in ("a.png", "b.jpg", "c.jpeg", "MIXED.PNG"):
        p = tmp_path / name
        p.write_bytes(b"\x89PNG\r\n")
        assert validate_image_path(str(p)) == p


# ---------- create_post (dry-run) ----------


def test_create_post_dry_run_text_only_does_no_network():
    """Sentinel: passing None as client must be safe when dry_run=True."""
    out = create_post(client=None, body="hello world", dry_run=True)
    assert out["dry_run"] is True
    assert out["body"] == "hello world"
    assert out["body_chars"] == len("hello world")
    assert out["image"] is None
    assert out["visibility"] == "PUBLIC"


def test_create_post_dry_run_with_image(tmp_path):
    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG\r\n")
    out = create_post(client=None, body="x", image_path=str(img), dry_run=True)
    assert out["image"] == str(img)
    assert out["visibility"] == "PUBLIC"


def test_create_post_live_requires_a_client():
    """Live path is now wired; passing client=None blows up loudly on the
    first network call rather than silently no-oping. Anything besides a
    real ``Linkedin`` client will fail when we try to ``client._post``."""
    with pytest.raises(AttributeError):
        create_post(client=None, body="hello", dry_run=False)


# ---------- _cmd_post ----------


def _ns(**kwargs) -> argparse.Namespace:
    defaults = {
        "message": "hello",
        "message_file": None,
        "image": None,
        "dry_run": False,
        "yes": True,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def test_cmd_post_dry_run_prints_preview_no_client(monkeypatch, capsys):
    def _explode():
        raise AssertionError("should not build a client on --dry-run")

    monkeypatch.setattr("clinkedin.cli.make_client", _explode)
    rc = _cmd_post(_ns(dry_run=True, message="hello there"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "Visibility: PUBLIC" in out
    assert "hello there" in out


def test_cmd_post_requires_exactly_one_of_message_flags(capsys):
    rc = _cmd_post(_ns(message=None, message_file=None))
    assert rc == 2
    assert "exactly one of --message or --message-file" in capsys.readouterr().err


def test_cmd_post_both_message_flags_rejected(capsys, tmp_path):
    f = tmp_path / "body.txt"
    f.write_text("from file")
    rc = _cmd_post(_ns(message="inline", message_file=str(f)))
    assert rc == 2
    assert "exactly one of --message or --message-file" in capsys.readouterr().err


def test_cmd_post_message_file_read(tmp_path, monkeypatch, capsys):
    f = tmp_path / "body.txt"
    f.write_text("loaded from file")
    monkeypatch.setattr(
        "clinkedin.cli.make_client",
        lambda: (_ for _ in ()).throw(AssertionError("dry-run path")),
    )
    rc = _cmd_post(_ns(message=None, message_file=str(f), dry_run=True))
    assert rc == 0
    assert "loaded from file" in capsys.readouterr().out


def test_cmd_post_message_file_unreadable_returns_2(capsys, tmp_path):
    rc = _cmd_post(
        _ns(message=None, message_file=str(tmp_path / "missing.txt"))
    )
    assert rc == 2
    assert "Could not read --message-file" in capsys.readouterr().err


def test_cmd_post_empty_body_returns_2(capsys):
    rc = _cmd_post(_ns(message="   \n  "))
    assert rc == 2
    assert "empty" in capsys.readouterr().err


def test_cmd_post_body_too_long_returns_2(capsys):
    rc = _cmd_post(_ns(message="x" * (MAX_BODY_LEN + 1)))
    assert rc == 2
    assert "too long" in capsys.readouterr().err


def test_cmd_post_missing_image_returns_2(capsys, tmp_path):
    rc = _cmd_post(_ns(image=str(tmp_path / "nope.png")))
    assert rc == 2
    assert "not found" in capsys.readouterr().err


def test_cmd_post_bad_image_extension_returns_2(capsys, tmp_path):
    bad = tmp_path / "doc.pdf"
    bad.write_bytes(b"%PDF")
    rc = _cmd_post(_ns(image=str(bad)))
    assert rc == 2
    assert "not supported" in capsys.readouterr().err


def test_cmd_post_aborts_when_user_says_no(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    monkeypatch.setattr(
        "clinkedin.cli.make_client",
        lambda: (_ for _ in ()).throw(AssertionError("should not be called")),
    )
    rc = _cmd_post(_ns(yes=False))
    assert rc == 0
    assert "Aborted." in capsys.readouterr().out


def test_cmd_post_live_path_calls_create_share(monkeypatch, capsys):
    """End-to-end: confirm + live path posts the share and prints the URN.

    Stubs both ``make_client`` and ``create_post`` so the test stays offline.
    """
    called = {}

    def _fake_make_client():
        return object()

    def _fake_create_post(client, body, image_path=None, *, dry_run=False):
        if dry_run:
            return {
                "dry_run": True,
                "body": body.rstrip(),
                "body_chars": len(body.rstrip()),
                "image": image_path,
                "visibility": "PUBLIC",
            }
        called["body"] = body
        return {
            "activity_urn": "urn:li:activity:9999",
            "share_urn": None,
            "share_url": "https://www.linkedin.com/feed/update/urn:li:activity:9999/",
            "asset_urn": None,
        }

    monkeypatch.setattr("clinkedin.cli.make_client", _fake_make_client)
    monkeypatch.setattr("clinkedin.cli.create_post", _fake_create_post)
    rc = _cmd_post(_ns(yes=True, message="hello world"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "Post published." in out
    assert "urn:li:activity:9999" in out
    assert called["body"] == "hello world"
