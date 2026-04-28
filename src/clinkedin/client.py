"""Factory for a Voyager-backed linkedin_api.Linkedin client."""

from __future__ import annotations

from .auth import (
    NotAuthenticatedError,
    cookies_from_chrome,
    load_session,
    save_session,
)


def make_client():
    jar = cookies_from_chrome()
    if jar is not None:
        save_session(jar)
    else:
        jar = load_session()

    if jar is None:
        raise NotAuthenticatedError(
            "No LinkedIn session found. Sign in to linkedin.com in Chrome, or "
            "run 'clinkedin login --cookie <li_at>' to paste a cookie manually."
        )

    from linkedin_api import Linkedin

    return Linkedin("", "", cookies=jar)
