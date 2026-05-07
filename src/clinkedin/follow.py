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
    """POST to /feed/follows?action=<action> with a followingInfo URN payload.

    Used by company follow/unfollow. Member follow/unfollow now use the SDUI
    endpoint via _post_sdui_follow_state — the legacy /feed/follows path
    400's for members as of 2026-05.
    """
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


# Captured from the LinkedIn web app 2026-05-07 — see the in-tree
# follow.py SDUI doc-comment above for the captured cURL details.
_SDUI_FOLLOW_PATH = (
    "/flagship-web/rsc-action/actions/server-request"
    "?sduiid=com.linkedin.sdui.requests.mynetwork.addaUpdateFollowState"
)
_SDUI_REQUEST_ID = "com.linkedin.sdui.requests.mynetwork.addaUpdateFollowState"


def _build_sdui_follow_payload(
    *, follow_state_type: str, numeric_id: str, acoa: str, vanity: str
) -> dict[str, Any]:
    requested_arguments = {
        "$type": "proto.sdui.actions.requests.RequestedArguments",
        "payload": {
            "followStateType": follow_state_type,
            "memberUrn": {"memberId": numeric_id},
            "postActionSentConfigs": [
                {
                    "type": "ProfileReplaceableSectionArgs",
                    "value": {"data": {"profileId": acoa, "vanityName": vanity}},
                },
                {
                    "type": "ProfileDiscoveryDrawerArgs",
                    "value": {
                        "data": {
                            "vanityName": vanity,
                            "nonIterableProfileId": acoa,
                        }
                    },
                },
            ],
            "followStateBinding": {
                "key": f"urn:li:fsd_followingState:urn:li:member:{numeric_id}",
                "namespace": None,
            },
        },
        "requestedStateKeys": [],
        "requestMetadata": {"$type": "proto.sdui.common.RequestMetadata"},
    }
    return {
        "requestId": _SDUI_REQUEST_ID,
        "serverRequest": {
            "requestId": _SDUI_REQUEST_ID,
            "requestedArguments": requested_arguments,
        },
        "states": [],
        "requestedArguments": {**requested_arguments, "states": [], "screenId": ""},
    }


def _post_sdui_follow_state(
    client,
    *,
    follow_state_type: str,
    numeric_id: str,
    acoa: str,
    vanity: str,
    label: str,
    identifier: str,
) -> None:
    """POST to LinkedIn's modern SDUI follow-state endpoint.

    `follow_state_type` is "FollowStateType_FOLLOW" or "FollowStateType_UNFOLLOW".
    Routed via base_request=True since the path is /flagship-web/... not /voyager/api/...
    """
    payload = _build_sdui_follow_payload(
        follow_state_type=follow_state_type,
        numeric_id=numeric_id,
        acoa=acoa,
        vanity=vanity,
    )
    res = client._post(
        _SDUI_FOLLOW_PATH,
        data=json.dumps(payload),
        headers={
            "accept": "*/*",
            "content-type": "application/json",
            "x-li-rsc-stream": "true",
        },
        base_request=True,
    )
    if res.status_code != 200:
        body = (res.text or "").strip().replace("\n", " ")[:300]
        raise FollowError(
            f"{label} failed for {identifier} (HTTP {res.status_code}): "
            f"{body or '<empty body>'}"
        )


def _do_member_follow_state(
    client, public_id: str, *, follow_state_type: str, label: str
) -> None:
    """Resolve a member identity then POST the SDUI follow-state mutation.

    Always does one Voyager Dash lookup (we need the numeric member ID, which
    only the dash response exposes — the ACoA URN alone isn't enough for the
    SDUI endpoint).
    """
    from .invite import resolve_profile_identity

    identity = resolve_profile_identity(client, public_id)
    _post_sdui_follow_state(
        client,
        follow_state_type=follow_state_type,
        numeric_id=identity["numeric_id"],
        acoa=identity["acoa"],
        vanity=identity["vanity"] or public_id,
        label=label,
        identifier=public_id,
    )


def follow_member(client, public_id: str) -> None:
    """Follow a person by their /in/<slug> public_id (vanity slug or ACoA URN)."""
    _do_member_follow_state(
        client, public_id, follow_state_type="FollowStateType_FOLLOW", label="Follow"
    )


def unfollow_member(client, public_id: str) -> None:
    """Unfollow a person by their /in/<slug> public_id (vanity slug or ACoA URN)."""
    _do_member_follow_state(
        client, public_id, follow_state_type="FollowStateType_UNFOLLOW", label="Unfollow"
    )


def follow_company(client, slug: str) -> None:
    """Follow a company by its /company/<slug> universal name."""
    company_id = resolve_company_urn(client, slug)
    _post_follow_action(client, "followByEntityUrn", company_id, "Follow")


def unfollow_company(client, slug: str) -> None:
    """Unfollow a company by its /company/<slug> universal name."""
    company_id = resolve_company_urn(client, slug)
    _post_follow_action(client, "unfollowByEntityUrn", company_id, "Unfollow")


# WHY this endpoint: as of mid-2026 the legacy /feed/dash/followingStates
# endpoint returns HTTP 400 — the Voyager web client now reads "people you
# follow" from the same `voyagerSearchDashClusters` GraphQL endpoint that
# powers global search, but with `flagshipSearchIntent:MYNETWORK_CURATION_HUB`
# and `queryParameters` filtering by `resultType:PEOPLE_FOLLOW`.
#
# The queryId hash is rotated by LinkedIn on bundle releases; if list_following
# starts 400ing again, re-capture from DevTools (Network tab on
# https://www.linkedin.com/mynetwork/network-manager/people-follow/following/).
_CURATION_HUB_QUERY_ID = "voyagerSearchDashClusters.843215f2a3455f1bed85762a45d71be8"


def _build_curation_hub_url(*, start: int, count: int, result_type: str) -> str:
    """Build the /graphql URL for the MyNetwork Curation Hub view."""
    variables = (
        f"(start:{start},count:{count},origin:CurationHub,"
        f"query:(flagshipSearchIntent:MYNETWORK_CURATION_HUB,"
        f"includeFiltersInResponse:false,"
        f"queryParameters:List((key:resultType,value:List({result_type})))))"
    )
    return f"/graphql?variables={variables}&queryId={_CURATION_HUB_QUERY_ID}"


def _walk_curation_results(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract entityResult objects from the SearchDashClusters response shape.

    Real shape (verified live 2026-05-07):
      data.searchDashClustersByAll.elements[]    ← clusters (CollectionResponse)
        .items[]                                  ← flat list of SearchItem
          .item.entityResult                      ← the result we want
    """
    out: list[dict[str, Any]] = []
    data = payload.get("data") or {}
    clusters = data.get("searchDashClustersByAll") or {}
    for cluster in clusters.get("elements") or []:
        for it in cluster.get("items") or []:
            er = (it.get("item") or {}).get("entityResult")
            if isinstance(er, dict):
                out.append(er)
    return out


def _normalize_curation_item(er: dict[str, Any]) -> dict[str, Any]:
    """Flatten a SearchEntityResultViewModel into our list_following schema."""
    title = ((er.get("title") or {}).get("text") or "").strip() or None
    headline = ((er.get("primarySubtitle") or {}).get("text") or "").strip() or None
    entity_urn = er.get("entityUrn") or ""
    nav_url = er.get("navigationUrl") or ""

    member_m = _PROFILE_URN_RE.search(entity_urn) or _PROFILE_URN_RE.search(nav_url)
    company_m = _COMPANY_URN_RE.search(entity_urn) or _COMPANY_URN_RE.search(nav_url)

    if member_m:
        kind: EntityKind | str = "member"
        public_id = member_m.group(1)
        urn = f"urn:li:fsd_profile:{public_id}"
        url = f"https://www.linkedin.com/in/{public_id}/"
    elif company_m:
        kind = "company"
        public_id = company_m.group(1)
        urn = f"urn:li:fsd_company:{public_id}"
        url = f"https://www.linkedin.com/company/{public_id}/"
    else:
        kind = "unknown"
        public_id = None
        urn = entity_urn
        url = None

    return {
        "name": title,
        "kind": kind,
        "public_id": public_id,
        "headline": headline,
        "url": url,
        "urn": urn,
    }


def list_following(
    client,
    limit: int | None = None,
    offset: int = 0,
    page_size: int = 40,
    result_type: str = "PEOPLE_FOLLOW",
) -> list[dict[str, Any]]:
    """Return entities the authenticated user follows, paginated sequentially.

    `result_type` selects which curation hub bucket: "PEOPLE_FOLLOW" (members
    you follow) or "COMPANIES" (companies). Default matches the legacy
    behaviour of returning members.
    """
    if limit is not None and limit <= 0:
        return []
    out: list[dict[str, Any]] = []
    start = max(0, offset)
    while True:
        want = page_size if limit is None else min(page_size, limit - len(out))
        if want <= 0:
            break
        url = _build_curation_hub_url(start=start, count=want, result_type=result_type)
        res = client._fetch(url)
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
        items = _walk_curation_results(data)
        if not items:
            break
        for er in items:
            out.append(_normalize_curation_item(er))
            if limit is not None and len(out) >= limit:
                return out
        if len(items) < want:
            break
        start += len(items)
    return out


def _summarize(res) -> dict[str, Any]:
    body_raw = res.text or ""
    try:
        body_json = res.json()
    except Exception:
        body_json = None
    return {
        "request_url": res.url,
        "status_code": res.status_code,
        "response_headers": dict(res.headers),
        "body_text": body_raw[:30000],
        "body_text_truncated": len(body_raw) > 30000,
        "body_json": body_json,
    }


def debug_following(
    client, count: int = 5, result_type: str = "PEOPLE_FOLLOW"
) -> dict[str, Any]:
    """Hit the Curation Hub GraphQL endpoint and dump the raw response.

    Single probe — useful for verifying that list_following's response shape
    is what we expect, and for diagnosing if the queryId hash has rotated.
    """
    url = _build_curation_hub_url(start=0, count=count, result_type=result_type)
    res = client._fetch(url)
    return {"request_url_template": url, **_summarize(res)}


def format_json(items: list[dict[str, Any]]) -> str:
    return json.dumps(items, indent=2, ensure_ascii=False)


def format_table(items: list[dict[str, Any]]) -> str:
    if not items:
        return "Not following anyone."
    lines = []
    for it in items:
        name = it.get("name") or "(unknown)"
        headline = it.get("headline") or ""
        url = it.get("url") or ""
        parts = [p for p in (name, headline, url) if p]
        lines.append(" · ".join(parts))
    return "\n".join(lines)
