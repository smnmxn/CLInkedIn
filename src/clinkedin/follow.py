"""Follow / unfollow members or companies, and list who you follow.

LinkedIn's "follow" relationship is one-way and works for both members and
companies. This module mirrors the disconnect.py pattern: thin functions that
talk to Voyager directly via `client._post()` / `client._fetch()`, and a single
domain exception (`FollowError`) for surface-level errors.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal
from urllib.parse import urlparse


class FollowError(Exception):
    pass


_MEMBER_RE = re.compile(r"^/(?:mwlite/)?in/([^/]+)/?$")
_COMPANY_RE = re.compile(r"^/(?:mwlite/)?company/([^/]+)/?$")
_COMPANY_URN_RE = re.compile(r"urn:li:(?:fs_normalized_company|fsd_company):(\d+)")
_PROFILE_URN_RE = re.compile(r"urn:li:fsd_profile:(ACoA[A-Za-z0-9_-]+)")

EntityKind = Literal["member", "company"]


def parse_follow_url(url: str) -> tuple[EntityKind, str]:
    """Extract (kind, slug) from a LinkedIn member or company URL."""
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if not (host == "linkedin.com" or host.endswith(".linkedin.com")):
        raise ValueError(f"Not a LinkedIn URL: {url}")
    path = parsed.path.rstrip()
    m = _MEMBER_RE.match(path)
    if m:
        return "member", m.group(1)
    m = _COMPANY_RE.match(path)
    if m:
        return "company", m.group(1)
    raise ValueError(
        f"Not a LinkedIn member or company URL "
        f"(expected /in/<slug> or /company/<slug>): {url}"
    )


def resolve_company_urn(client, slug: str) -> str:
    """Look up the numeric company ID for a /company/<slug> universal name."""
    res = client._fetch(
        "/organization/companies",
        params={"q": "universalName", "universalName": slug},
    )
    if res.status_code != 200:
        raise FollowError(
            f"Company lookup failed for {slug} (HTTP {res.status_code})."
        )
    try:
        data = res.json()
    except ValueError:
        raise FollowError(f"Company lookup returned non-JSON for {slug}.")
    elements = data.get("elements") or []
    if not elements:
        raise FollowError(f"No company found for {slug}.")
    m = _COMPANY_URN_RE.search(json.dumps(elements[0]))
    if not m:
        raise FollowError(f"Could not extract company URN for {slug}.")
    return m.group(1)


def _post_follow_action(client, action: str, urn_id: str, label: str) -> None:
    """POST to /feed/follows?action=<action> with a followingInfo URN payload."""
    payload = {"urn": f"urn:li:fs_followingInfo:{urn_id}"}
    res = client._post(
        f"/feed/follows?action={action}",
        data=json.dumps(payload),
        headers={"accept": "application/vnd.linkedin.normalized+json+2.1"},
    )
    if res.status_code != 200:
        body = (res.text or "").strip().replace("\n", " ")[:200]
        raise FollowError(
            f"{label} failed for {urn_id} (HTTP {res.status_code}): "
            f"{body or '<empty body>'}"
        )


def follow_member(client, public_id: str) -> None:
    """Follow a person by their /in/<slug> public_id."""
    from .invite import resolve_profile_urn

    member_urn = resolve_profile_urn(client, public_id)
    _post_follow_action(client, "followByEntityUrn", member_urn, "Follow")


def unfollow_member(client, public_id: str) -> None:
    """Unfollow a person by their /in/<slug> public_id."""
    from .invite import resolve_profile_urn

    member_urn = resolve_profile_urn(client, public_id)
    _post_follow_action(client, "unfollowByEntityUrn", member_urn, "Unfollow")


def follow_company(client, slug: str) -> None:
    """Follow a company by its /company/<slug> universal name."""
    company_id = resolve_company_urn(client, slug)
    _post_follow_action(client, "followByEntityUrn", company_id, "Follow")


def unfollow_company(client, slug: str) -> None:
    """Unfollow a company by its /company/<slug> universal name."""
    company_id = resolve_company_urn(client, slug)
    _post_follow_action(client, "unfollowByEntityUrn", company_id, "Unfollow")


# WHY this endpoint: the Voyager web client's "Network → Following" page reads
# from `/feed/dash/followingStates` with `q=followingStates`, which returns the
# entities the authenticated viewer follows (members + companies + hashtags).
# The endpoint identifies the viewer from the session cookies, so no viewer URN
# parameter is needed. If LinkedIn moves this, the failure surfaces as a
# FollowError with the HTTP status, which is enough to debug.
_FOLLOWING_LIST_PATH = "/feed/dash/followingStates"


def _classify_entity(urn: str) -> tuple[EntityKind | str, str | None]:
    """Return (kind, public_id) inferred from a followed-entity URN."""
    if not urn:
        return "unknown", None
    if "fsd_profile" in urn or "fs_miniProfile" in urn or "ACoA" in urn:
        m = _PROFILE_URN_RE.search(urn) or re.search(r"(ACoA[A-Za-z0-9_-]+)", urn)
        return "member", (m.group(1) if m else None)
    m = _COMPANY_URN_RE.search(urn)
    if m:
        return "company", m.group(1)
    if "topic" in urn.lower() or "hashtag" in urn.lower():
        return "hashtag", None
    return "unknown", None


def _normalize_following_item(item: dict[str, Any]) -> dict[str, Any]:
    """Flatten a Voyager followingState element into a stable shape."""
    followee = item.get("followeeUrn") or item.get("entityUrn") or ""
    kind, public_id = _classify_entity(followee)
    name = (
        item.get("name")
        or (item.get("profile") or {}).get("firstName", "")
        + " "
        + (item.get("profile") or {}).get("lastName", "")
    ).strip() or None
    headline = item.get("headline") or item.get("subtitle") or None
    return {
        "name": name,
        "kind": kind,
        "public_id": public_id,
        "headline": headline,
        "urn": followee,
    }


def list_following(
    client,
    limit: int | None = None,
    offset: int = 0,
    page_size: int = 100,
) -> list[dict[str, Any]]:
    """Return entities the authenticated user follows, paginated sequentially."""
    if limit is not None and limit <= 0:
        return []
    out: list[dict[str, Any]] = []
    start = max(0, offset)
    while True:
        want = page_size if limit is None else min(page_size, limit - len(out))
        if want <= 0:
            break
        res = client._fetch(
            _FOLLOWING_LIST_PATH,
            params={"q": "followingStates", "start": start, "count": want},
        )
        if res.status_code != 200:
            body = (res.text or "").strip().replace("\n", " ")[:200]
            raise FollowError(
                f"List following failed (HTTP {res.status_code}): "
                f"{body or '<empty body>'}"
            )
        try:
            data = res.json()
        except ValueError:
            raise FollowError("List following returned non-JSON.")
        elements = data.get("elements") or []
        if not elements:
            break
        # Voyager sometimes ignores `count` and returns more — truncate so the
        # caller's `limit` is honoured.
        if limit is not None:
            elements = elements[: limit - len(out)]
        for elem in elements:
            out.append(_normalize_following_item(elem))
        if limit is not None and len(out) >= limit:
            break
        if len(elements) < want:
            break
        start += len(elements)
    return out


def format_json(items: list[dict[str, Any]]) -> str:
    return json.dumps(items, indent=2, ensure_ascii=False)


def format_table(items: list[dict[str, Any]]) -> str:
    if not items:
        return "Not following anyone."
    lines = []
    for it in items:
        name = it.get("name") or "(unknown)"
        kind = it.get("kind") or ""
        headline = it.get("headline") or ""
        public_id = it.get("public_id") or ""
        url = ""
        if public_id and kind == "member":
            url = f"https://www.linkedin.com/in/{public_id}/"
        elif public_id and kind == "company":
            url = f"https://www.linkedin.com/company/{public_id}/"
        parts = [p for p in (name, f"[{kind}]" if kind else "", headline, url) if p]
        lines.append(" · ".join(parts))
    return "\n".join(lines)
