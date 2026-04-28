"""Fetch and format 1st-degree connections."""

from __future__ import annotations

import json
from typing import Any


def fetch_connections(client, limit: int | None = None) -> list[dict[str, Any]]:
    """Return the authenticated user's 1st-degree connections via people search."""
    kwargs: dict[str, Any] = {"network_depths": ["F"]}
    if limit is not None:
        kwargs["limit"] = limit
    return client.search_people(**kwargs)


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
        parts = [p for p in (name, headline, location) if p]
        lines.append(" · ".join(parts))
    return "\n".join(lines)
