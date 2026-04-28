"""Session save/load for the Voyager client."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from requests.cookies import RequestsCookieJar

SESSION_PATH = Path.home() / ".config" / "clinkedin" / "session.json"
_LEGACY_SESSION_PATH = Path.home() / ".config" / "linkedin-cli" / "session.json"


class NotAuthenticatedError(Exception):
    pass


def _serialize_jar(jar: RequestsCookieJar) -> list[dict[str, Any]]:
    return [
        {
            "name": c.name,
            "value": c.value,
            "domain": c.domain,
            "path": c.path,
            "expires": c.expires,
        }
        for c in jar
    ]


def _deserialize_jar(data: list[dict[str, Any]]) -> RequestsCookieJar:
    jar = RequestsCookieJar()
    for c in data:
        jar.set(
            c["name"],
            c["value"],
            domain=c.get("domain", ""),
            path=c.get("path", "/"),
            expires=c.get("expires"),
        )
    return jar


def save_session(jar: RequestsCookieJar) -> None:
    SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"cookies": _serialize_jar(jar)}
    SESSION_PATH.write_text(json.dumps(payload))
    os.chmod(SESSION_PATH, 0o600)


def load_session() -> RequestsCookieJar | None:
    path = SESSION_PATH if SESSION_PATH.exists() else _LEGACY_SESSION_PATH
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return _deserialize_jar(data["cookies"])
    except (json.JSONDecodeError, KeyError, OSError):
        return None


def login(email: str, password: str) -> dict[str, Any]:
    """Authenticate against Voyager, persist cookies, return the viewer profile."""
    from linkedin_api import Linkedin

    api = Linkedin(email, password)
    save_session(api.client.session.cookies)
    return api.get_user_profile()


def cookies_from_chrome() -> RequestsCookieJar | None:
    """Return a RequestsCookieJar of LinkedIn cookies pulled from Chrome, or None."""
    try:
        import browser_cookie3
    except ImportError:
        return None
    try:
        raw = browser_cookie3.chrome(domain_name="linkedin.com")
    except Exception:
        return None
    if not any(c.name == "li_at" for c in raw):
        return None
    jar = RequestsCookieJar()
    for c in raw:
        jar.set(c.name, c.value, domain=c.domain, path=c.path, expires=c.expires)
    return jar


def login_from_chrome() -> dict[str, Any]:
    """Pull LinkedIn cookies from Chrome, persist them, return the viewer profile."""
    from linkedin_api import Linkedin

    jar = cookies_from_chrome()
    if jar is None:
        raise RuntimeError(
            "No li_at cookie found in Chrome. Sign in to linkedin.com in Chrome first."
        )
    save_session(jar)
    api = Linkedin("", "", cookies=jar)
    return api.get_user_profile()


def login_with_cookie(li_at: str) -> dict[str, Any]:
    """Build a session from a raw li_at value, persist, return the viewer profile."""
    from linkedin_api import Linkedin

    jar = RequestsCookieJar()
    jar.set("li_at", li_at, domain=".linkedin.com", path="/")
    save_session(jar)
    api = Linkedin("", "", cookies=jar)
    return api.get_user_profile()
