"""Fetch and format 1st-degree connections via the authoritative dash endpoint.

WHY this doesn't use linkedin_api's `client.search(filters=…network:F…)`: that
hits LinkedIn's people-search index, which is denormalized and refreshed
asynchronously. After `clinkedin disconnect` writes to the relationship store,
the search index can keep returning the disconnected member as 1st-degree for
hours-to-days. This module reads from `/relationships/dash/connections` — the
same service the disconnect endpoint writes to — so removals reflect immediately.

Trade-off: the `ConnectionListWithProfile-15` decoration only inlines name,
headline, publicIdentifier, and profile picture — `geoLocation` is *not*
included. `location` therefore reports `None` from this endpoint; users who
need location can `clinkedin view <url>` for full profile data.
"""

from __future__ import annotations

import json
import re
from typing import Any


class ConnectionsError(Exception):
    pass


# Heuristic parse of "Title at Company" / "Title @ Company" out of a self-set
# headline. Stops at common separators (|, ·, •, " - ") so multi-role headlines
# like "CEO at A | Advisor at B" pick the first/current company.
_COMPANY_RE = re.compile(
    r"\s+(?:at|@)\s+(.+?)(?:\s*[|·•]|\s+-\s+|$)",
    re.IGNORECASE,
)

_PROFILE_URN_RE = re.compile(r"urn:li:fsd_profile:(ACoA[A-Za-z0-9_-]+)")

_DASH_PATH = "/relationships/dash/connections"
_DECORATION_ID = (
    "com.linkedin.voyager.dash.deco.web.mynetwork.ConnectionListWithProfile-15"
)


def _parse_company(jobtitle: str | None) -> str | None:
    if not jobtitle:
        return None
    m = _COMPANY_RE.search(jobtitle)
    if not m:
        return None
    company = m.group(1).strip()
    return company or None


def _extract_location(member: dict[str, Any]) -> str | None:
    """Best-effort location extraction from a resolved profile sub-object."""
    geo = member.get("geoLocation")
    if isinstance(geo, dict):
        inner = geo.get("geo")
        if isinstance(inner, dict):
            name = inner.get("defaultLocalizedName")
            if isinstance(name, dict) and name.get("value"):
                return name["value"]
            if isinstance(name, str):
                return name
    return member.get("locationName") or member.get("location") or None


def _normalize(elem: dict[str, Any]) -> dict[str, Any] | None:
    """Flatten a dash connection element into the public connection schema."""
    member = elem.get("connectedMemberResolutionResult") or {}
    if not isinstance(member, dict) or not member:
        return None

    first = (member.get("firstName") or "").strip()
    last = (member.get("lastName") or "").strip()
    name = (first + " " + last).strip() or None

    headline = member.get("headline") or None
    public_id = member.get("publicIdentifier")

    m = _PROFILE_URN_RE.search(member.get("entityUrn") or "")
    urn_id = m.group(1) if m else None

    if public_id:
        url = f"https://www.linkedin.com/in/{public_id}/"
    elif urn_id:
        url = f"https://www.linkedin.com/in/{urn_id}/"
    else:
        url = None

    return {
        "name": name,
        "jobtitle": headline,
        "location": _extract_location(member),
        "distance": "DISTANCE_1",
        "urn_id": urn_id,
        "url": url,
        "company": _parse_company(headline),
    }


def fetch_connections(
    client,
    limit: int | None = None,
    offset: int = 0,
    page_size: int = 40,
) -> list[dict[str, Any]]:
    """Return the authenticated user's 1st-degree connections."""
    if limit is not None and limit <= 0:
        return []
    out: list[dict[str, Any]] = []
    start = max(0, offset)
    while True:
        want = page_size if limit is None else min(page_size, limit - len(out))
        if want <= 0:
            break
        res = client._fetch(
            _DASH_PATH,
            params={
                "q": "search",
                "start": start,
                "count": want,
                "decorationId": _DECORATION_ID,
            },
        )
        if res.status_code != 200:
            body = (res.text or "").strip().replace("\n", " ")[:200]
            raise ConnectionsError(
                f"List connections failed (HTTP {res.status_code}): "
                f"{body or '<empty body>'}"
            )
        try:
            data = res.json()
        except ValueError:
            raise ConnectionsError("List connections returned non-JSON.")
        elements = data.get("elements") or []
        if not elements:
            break
        if limit is not None:
            elements = elements[: limit - len(out)]
        for elem in elements:
            row = _normalize(elem)
            if row is not None:
                out.append(row)
        if limit is not None and len(out) >= limit:
            break
        if len(elements) < want:
            break
        start += len(elements)
    return out


def format_json(conns: list[dict[str, Any]]) -> str:
    return json.dumps(conns, indent=2, ensure_ascii=False)


def format_table(conns: list[dict[str, Any]]) -> str:
    if not conns:
        return "No connections found."
    lines = []
    for c in conns:
        name = c.get("name") or "(unknown)"
        headline = c.get("jobtitle") or ""
        location = c.get("location") or ""
        company = c.get("company") or ""
        url = c.get("url") or ""
        parts = [p for p in (name, headline, location, company, url) if p]
        lines.append(" · ".join(parts))
    return "\n".join(lines)
