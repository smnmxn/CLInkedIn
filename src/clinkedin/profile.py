"""Fetch and format a LinkedIn profile."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any


class ProfileError(Exception):
    pass


def _walk(d: Any, *path: str) -> Any:
    cur = d
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur


def _fetch_dash_profile_element(client, public_id: str) -> dict[str, Any]:
    """Hit /voyagerIdentityDashProfiles?q=memberIdentity and return elements[0].

    The legacy /identity/profiles/<id>/profileView endpoint that linkedin-api
    uses returns 410 Gone as of mid-2026, so we go direct to the modern Dash
    endpoint that invite.py already uses. Raises ProfileError on any failure.
    """
    res = client._fetch(
        f"/voyagerIdentityDashProfiles?q=memberIdentity&memberIdentity={public_id}"
    )
    if res.status_code != 200:
        raise ProfileError(
            f"Profile lookup failed for {public_id} (HTTP {res.status_code}). "
            "Your session may be expired or rate-limited; try 'clinkedin login'."
        )
    try:
        data = res.json()
    except ValueError as e:
        raise ProfileError(f"Profile lookup returned non-JSON for {public_id}.") from e
    elements = data.get("elements") if isinstance(data, dict) else None
    if not elements:
        raise ProfileError(
            f"No profile found for {public_id}. The profile may be private or deleted."
        )
    return elements[0]


def _normalize_dash_profile(element: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Dash profile element into the shape format_text consumes."""
    entity_urn = element.get("entityUrn") or ""
    object_urn = element.get("objectUrn") or ""
    profile_id = ""
    if entity_urn.startswith("urn:li:fsd_profile:"):
        profile_id = entity_urn[len("urn:li:fsd_profile:"):]
    headline = element.get("headline") or _walk(element, "multiLocaleHeadline", "en_US") or ""
    summary = element.get("summary") or _walk(element, "multiLocaleSummary", "en_US") or ""
    picture = _walk(element, "profilePicture", "displayImage", "vectorImage", "rootUrl") or ""
    return {
        "firstName": element.get("firstName") or _walk(element, "multiLocaleFirstName", "en_US") or "",
        "lastName": element.get("lastName") or _walk(element, "multiLocaleLastName", "en_US") or "",
        "headline": headline,
        "summary": summary,
        "public_id": element.get("publicIdentifier") or "",
        "profile_urn": entity_urn,
        "profile_id": profile_id,
        "member_urn": object_urn,
        "displayPictureUrl": picture,
    }


def fetch_profile(client, public_id: str) -> dict[str, Any]:
    """Fetch a LinkedIn profile by public_id slug.

    Bypasses linkedin-api (whose /profileView endpoint is now 410 Gone) and
    calls the modern /voyagerIdentityDashProfiles endpoint directly. Returns a
    normalized dict with firstName, lastName, headline, summary, public_id,
    profile_urn (the urn:li:fsd_profile:ACoA... form), profile_id (just ACoA...),
    member_urn, and displayPictureUrl. Experience and education are not
    populated by this endpoint — fetch separately if needed.
    """
    element = _fetch_dash_profile_element(client, public_id)
    profile = _normalize_dash_profile(element)
    profile["_dash_raw"] = element
    return profile


def _summarize_response(res) -> dict[str, Any]:
    body_raw = res.text
    body_parsed: Any
    try:
        body_parsed = res.json()
    except Exception:
        body_parsed = None
    return {
        "request_url": res.url,
        "status_code": res.status_code,
        "response_headers": dict(res.headers),
        "body_text": body_raw[:30000],
        "body_text_truncated": len(body_raw) > 30000,
        "body_json": body_parsed,
    }


def debug_profile_posts(client, public_id: str, limit: int = 10) -> dict[str, Any]:
    """Probe the Voyager endpoints we care about and dump raw HTTP responses.

    Probes three endpoints in order:
      1. legacy /identity/profiles/<id>/profileView (now 410-deprecated;
         kept for confirmation)
      2. modern /voyagerIdentityDashProfiles?q=memberIdentity (the URN lookup
         endpoint that invite.py already uses successfully)
      3. /identity/profileUpdatesV2 with the URN resolved from (2)

    Returns a dict with one section per probe. Bypasses linkedin-api parsing
    entirely so it survives KeyError bugs in the library.
    """
    out: dict[str, Any] = {"public_id": public_id}

    legacy_res = client._fetch(f"/identity/profiles/{public_id}/profileView")
    out["legacy_profileView"] = _summarize_response(legacy_res)

    dash_res = client._fetch(
        f"/voyagerIdentityDashProfiles?q=memberIdentity&memberIdentity={public_id}"
    )
    out["dash_profile"] = _summarize_response(dash_res)

    profile_urn = None
    dj = out["dash_profile"]["body_json"]
    if isinstance(dj, dict):
        elements = dj.get("elements") or []
        if elements and isinstance(elements[0], dict):
            raw_urn = elements[0].get("entityUrn")
            if isinstance(raw_urn, str) and "fsd_profile" in raw_urn:
                profile_urn = raw_urn
    out["resolved_profile_urn"] = profile_urn

    if profile_urn:
        params = {
            "count": min(limit, 100),
            "start": 0,
            "q": "memberShareFeed",
            "moduleKey": "member-shares:phone",
            "includeLongTermHistory": True,
            "profileUrn": profile_urn,
        }
        posts_res = client._fetch("/identity/profileUpdatesV2", params=params)
        out["posts_request_params"] = params
        out["posts_fetch"] = _summarize_response(posts_res)
    else:
        out["posts_fetch"] = "skipped: could not resolve profile_urn from dash_profile"
    return out


def fetch_profile_posts(client, public_id: str, limit: int = 10) -> list[dict[str, Any]]:
    """Fetch a profile's recent posts via Voyager.

    Bypasses linkedin-api entirely (its get_profile_posts internally calls the
    410-deprecated /profileView endpoint when only public_id is given). We
    resolve the profile URN via the Dash endpoint, then hit
    /identity/profileUpdatesV2 directly.

    An empty list is a valid result (the profile has no posts).
    """
    element = _fetch_dash_profile_element(client, public_id)
    profile_urn = element.get("entityUrn") or ""
    if not profile_urn.startswith("urn:li:fsd_profile:"):
        raise ProfileError(
            f"Could not resolve profile URN for {public_id} (got {profile_urn!r})."
        )
    params = {
        "count": min(limit, 100),
        "start": 0,
        "q": "memberShareFeed",
        "moduleKey": "member-shares:phone",
        "includeLongTermHistory": "true",
        "profileUrn": profile_urn,
    }
    res = client._fetch("/identity/profileUpdatesV2", params=params)
    if res.status_code != 200:
        raise ProfileError(
            f"Posts fetch failed for {public_id} (HTTP {res.status_code}). "
            "Posts may be restricted to connections, or your session may be "
            "rate-limited."
        )
    try:
        data = res.json()
    except ValueError as e:
        raise ProfileError(f"Posts fetch returned non-JSON for {public_id}.") from e
    if not isinstance(data, dict):
        raise ProfileError(f"Posts fetch returned unexpected shape for {public_id}.")
    elements = data.get("elements") or []
    if not isinstance(elements, list):
        raise ProfileError(f"Posts fetch returned unexpected elements for {public_id}.")
    return elements


def _format_date(d: dict | None) -> str:
    if not d:
        return ""
    parts = []
    if d.get("month"):
        parts.append(str(d["month"]))
    if d.get("year"):
        parts.append(str(d["year"]))
    return "/".join(parts)


def _format_period(time_period: dict | None) -> str:
    if not time_period:
        return ""
    start = _format_date(time_period.get("startDate"))
    end = _format_date(time_period.get("endDate")) or "Present"
    if start and end:
        return f"{start} – {end}"
    return start or end


def format_text(profile: dict[str, Any]) -> str:
    sections: list[str] = []

    name = " ".join(
        x for x in (profile.get("firstName"), profile.get("lastName")) if x
    ) or "(unknown)"
    head = [name]
    if profile.get("headline"):
        head.append(profile["headline"])
    loc_parts = [profile.get("locationName"), profile.get("industryName")]
    loc = " · ".join(p for p in loc_parts if p)
    if loc:
        head.append(loc)
    sections.append("\n".join(head))

    if profile.get("summary"):
        sections.append("About:\n" + profile["summary"].strip())

    experience = profile.get("experience") or []
    if experience:
        lines = ["Experience:"]
        for exp in experience:
            parts = [
                exp.get("title") or "",
                exp.get("companyName") or "",
                _format_period(exp.get("timePeriod")),
                exp.get("locationName") or "",
            ]
            lines.append("- " + " · ".join(p for p in parts if p))
        sections.append("\n".join(lines))

    education = profile.get("education") or []
    if education:
        lines = ["Education:"]
        for edu in education:
            parts = [
                edu.get("schoolName") or "",
                edu.get("degreeName") or "",
                edu.get("fieldOfStudy") or "",
                _format_period(edu.get("timePeriod")),
            ]
            lines.append("- " + " · ".join(p for p in parts if p))
        sections.append("\n".join(lines))

    public_id = profile.get("public_id")
    if public_id:
        sections.append(f"Profile: https://www.linkedin.com/in/{public_id}/")

    if not experience and not education and profile.get("profile_urn"):
        sections.append(
            "(Experience and education sections require a separate API call "
            "and are not yet wired up — pass --posts to view recent activity, "
            "or --json to see the raw Dash response.)"
        )

    return "\n\n".join(sections)


def format_json(profile: dict[str, Any]) -> str:
    return json.dumps(profile, indent=2, ensure_ascii=False, default=str)


_AGE_RE = re.compile(
    r"(?P<num>\d+)\s*(?P<unit>seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h|days?|d|weeks?|wks?|w|months?|mos?|mo|years?|yrs?|y)\b",
    re.IGNORECASE,
)
_AGE_UNIT_TO_DAYS = {
    "s": 1 / 86400, "sec": 1 / 86400, "secs": 1 / 86400, "second": 1 / 86400, "seconds": 1 / 86400,
    "m": 1 / 1440, "min": 1 / 1440, "mins": 1 / 1440, "minute": 1 / 1440, "minutes": 1 / 1440,
    "h": 1 / 24, "hr": 1 / 24, "hrs": 1 / 24, "hour": 1 / 24, "hours": 1 / 24,
    "d": 1, "day": 1, "days": 1,
    "w": 7, "wk": 7, "wks": 7, "week": 7, "weeks": 7,
    "mo": 30, "mos": 30, "month": 30, "months": 30,
    "y": 365, "yr": 365, "yrs": 365, "year": 365, "years": 365,
}


def parse_age(s: str) -> timedelta | None:
    """Parse a relative-age string into a timedelta.

    Accepts forms like '1d', '2w', '3 months', '1 year ago', '5 hours'.
    Returns None for inputs we can't parse (caller decides what to do).
    Month/year are approximate (30 days / 365 days).
    """
    if not s:
        return None
    s = s.strip().lower()
    if s in {"now", "just now", "moments ago"}:
        return timedelta(0)
    m = _AGE_RE.search(s)
    if not m:
        return None
    n = int(m.group("num"))
    unit = m.group("unit").lower()
    days = _AGE_UNIT_TO_DAYS.get(unit)
    if days is None:
        return None
    return timedelta(days=n * days)


def _post_age(post: dict[str, Any]) -> timedelta | None:
    """Best-effort age of a post. Tries absolute createdAt first, then the
    relative string in actor.subDescription (which is what LinkedIn renders).
    """
    created = post.get("createdAt") or post.get("firstPublishedAt")
    if isinstance(created, (int, float)):
        try:
            ts = datetime.fromtimestamp(created / 1000, tz=timezone.utc)
            return datetime.now(tz=timezone.utc) - ts
        except (ValueError, OverflowError, OSError):
            pass
    sub = (
        _walk(post, "actor", "subDescription", "accessibilityText")
        or _walk(post, "actor", "subDescription", "text")
        or ""
    )
    return parse_age(sub)


def filter_posts_by_age(
    posts: list[dict[str, Any]], max_age: timedelta
) -> list[dict[str, Any]]:
    """Return posts whose age is <= max_age. Drops posts whose age can't be
    parsed — better to under-include than to silently misclassify."""
    out = []
    for p in posts:
        age = _post_age(p)
        if age is not None and age <= max_age:
            out.append(p)
    return out


def _extract_post_fields(post: dict[str, Any]) -> dict[str, Any]:
    text = (
        _walk(post, "commentary", "text", "text")
        or _walk(post, "value", "com.linkedin.voyager.feed.render.UpdateV2", "commentary", "text", "text")
        or ""
    )
    urn = _walk(post, "updateMetadata", "urn") or post.get("urn") or ""
    counts = _walk(post, "socialDetail", "totalSocialActivityCounts") or {}
    likes = counts.get("numLikes") or 0
    comments = counts.get("numComments") or 0

    date = ""
    created = post.get("createdAt") or post.get("firstPublishedAt")
    if isinstance(created, (int, float)):
        try:
            date = datetime.fromtimestamp(created / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        except (ValueError, OverflowError, OSError):
            date = ""
    if not date:
        # Modern Voyager feed responses don't expose absolute timestamps at the
        # top level; the relative time ("1d", "2w") is in actor.subDescription.
        sub = _walk(post, "actor", "subDescription", "text") or ""
        date = sub.split("•")[0].strip() or sub.strip()

    return {
        "date": date,
        "text": text,
        "like_count": likes,
        "comment_count": comments,
        "urn": urn,
        "url": f"https://www.linkedin.com/feed/update/{urn}/" if urn else "",
    }


def format_posts_text(posts: list[dict[str, Any]], *, full: bool = False) -> str:
    """Render a list of profile posts as text.

    full=False (default): one line per post, text truncated to 120 chars.
    full=True: multi-line block per post — metadata header, full text below,
    blank line between posts.
    """
    if not posts:
        return ""
    if full:
        blocks = []
        for post in posts:
            f = _extract_post_fields(post)
            header = " · ".join(
                [
                    f["date"] or "?",
                    f"{f['like_count']} likes",
                    f"{f['comment_count']} comments",
                    f["url"] or "(no url)",
                ]
            )
            body = (f["text"] or "").strip() or "(no text)"
            blocks.append(f"{header}\n{body}")
        return "\n\n---\n\n".join(blocks)

    lines = []
    for post in posts:
        f = _extract_post_fields(post)
        text = (f["text"] or "").replace("\n", " ").strip() or "(no text)"
        if len(text) > 120:
            text = text[:119].rstrip() + "…"
        parts = [
            f["date"] or "?",
            f'"{text}"',
            f"{f['like_count']} likes",
            f"{f['comment_count']} comments",
            f["url"] or "(no url)",
        ]
        lines.append(" · ".join(parts))
    return "\n".join(lines)


def format_posts_json(posts: list[dict[str, Any]]) -> str:
    return json.dumps(posts, indent=2, ensure_ascii=False, default=str)
