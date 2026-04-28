"""Search LinkedIn for people."""

from __future__ import annotations

import json
from typing import Any


def _profile_url(urn_id: str | None) -> str | None:
    # URN-form profile URL — LinkedIn redirects /in/<urn_id>/ to the canonical profile.
    return f"https://www.linkedin.com/in/{urn_id}/" if urn_id else None


def search_people(
    client,
    query: str,
    network_depths: list[str] | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Search LinkedIn people by keywords, optionally filtered by network depth."""
    kwargs: dict[str, Any] = {"keywords": query}
    if network_depths:
        kwargs["network_depths"] = network_depths
    if limit is not None:
        kwargs["limit"] = limit
    raw = client.search_people(**kwargs)
    return [{**r, "url": _profile_url(r.get("urn_id"))} for r in raw]


def format_json(results: list[dict[str, Any]]) -> str:
    return json.dumps(results, indent=2, ensure_ascii=False)


def format_table(results: list[dict[str, Any]]) -> str:
    if not results:
        return "No results found."
    lines = []
    for r in results:
        name = r.get("name") or "(unknown)"
        headline = r.get("jobtitle") or ""
        location = r.get("location") or ""
        url = r.get("url") or ""
        parts = [p for p in (name, headline, location, url) if p]
        lines.append(" · ".join(parts))
    return "\n".join(lines)
