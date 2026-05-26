"""Post a comment on a LinkedIn post (activity).

LinkedIn migrated comment posting from the legacy `/voyager/api/feed/comments`
REST endpoint to their SDUI framework (`/flagship-web/rsc-action/.../com.linkedin.sdui.comments.createComment`).
The SDUI request needs both:
  - shareId (post identifier; in URLs as `posts/...-share-N-X`)
  - activityId (comment-thread identifier; different number, embedded in the
    post detail page's HTML)

The activity ID and a per-page `trackingId` are extracted by GETting the post
HTML page once. This is fragile to LinkedIn UI changes — if the create-comment
POST starts 400ing, the most likely cause is the SDUI payload shape changing.
Re-capture from DevTools and update `_build_sdui_create_comment_payload`.
"""

from __future__ import annotations

import base64
import json
import re
import secrets
import uuid
from typing import Any
from urllib.parse import unquote, urlparse


class CommentError(Exception):
    pass


_ACTIVITY_URN_RE = re.compile(r"urn:li:activity:(\d+)")
_SHARE_URN_RE = re.compile(r"urn:li:share:(\d+)")
_POSTS_NUMERIC_RE = re.compile(r"-(\d{15,})(?:-[A-Za-z0-9_]+)?$")


def parse_post_url(url: str) -> dict[str, str | None]:
    """Parse a LinkedIn post URL or URN into the IDs we can extract.

    Returns {"activity_id": str | None, "share_id": str | None, "page_path": str | None}.
    `page_path` is the linkedin.com path to fetch the post HTML for missing IDs;
    None when the input is a bare URN (no associated public page).

    Raises ValueError for inputs that aren't recognisable LinkedIn post forms.
    """
    raw = url.strip()
    if not raw:
        raise ValueError("Empty URL.")

    # Bare URN forms.
    if raw.startswith("urn:li:activity:"):
        m = re.fullmatch(r"urn:li:activity:(\d+)", raw)
        if not m:
            raise ValueError(f"Malformed activity URN: {url}")
        return {"activity_id": m.group(1), "share_id": None, "page_path": None}
    if raw.startswith("urn:li:share:"):
        m = re.fullmatch(r"urn:li:share:(\d+)", raw)
        if not m:
            raise ValueError(f"Malformed share URN: {url}")
        return {"activity_id": None, "share_id": m.group(1), "page_path": None}
    if raw.startswith("urn:li:ugcPost:"):
        # ugcPost: keep as activity-equivalent; LinkedIn resolves at runtime.
        m = re.fullmatch(r"urn:li:ugcPost:(\d+)", raw)
        if not m:
            raise ValueError(f"Malformed ugcPost URN: {url}")
        return {"activity_id": m.group(1), "share_id": None, "page_path": None}

    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if not (host == "linkedin.com" or host.endswith(".linkedin.com")):
        raise ValueError(f"Not a LinkedIn URL: {url}")
    path = unquote(parsed.path)

    # /feed/update/urn:li:activity:N/
    m = _ACTIVITY_URN_RE.search(path)
    if m:
        return {
            "activity_id": m.group(1),
            "share_id": None,
            "page_path": parsed.path,
        }

    # /posts/<vanity>_<slug>-share-N-X  or  /posts/<vanity>_<slug>-activity-N-X
    if path.startswith("/posts/") or path.startswith("/mwlite/posts/"):
        m_p = _POSTS_NUMERIC_RE.search(path.rstrip("/"))
        if m_p:
            num_id = m_p.group(1)
            # The connector word disambiguates which kind of ID this is.
            if "-share-" in path:
                return {
                    "activity_id": None,
                    "share_id": num_id,
                    "page_path": parsed.path,
                }
            # Default / "-activity-" form: numeric ID is the activity URN.
            return {
                "activity_id": num_id,
                "share_id": None,
                "page_path": parsed.path,
            }

    raise ValueError(
        f"Not a LinkedIn post URL (expected /feed/update/urn:li:activity:N/, "
        f"/posts/...-(activity|share)-N-XYZ, or a bare urn:li:(activity|share):N): {url}"
    )


# Patterns to grep IDs out of the post detail page HTML / embedded JSON islands.
# Some patterns appear as `"foo":"..."` and others as `\"foo\":\"...\"` because
# LinkedIn embeds SDUI templates as JSON inside JS string literals. We match
# `\\?"` to accept either form.
_HTML_ACTIVITY_RE = re.compile(r'"activityId"\s*:\s*"(\d+)"')
_HTML_SHARE_RE = re.compile(r'"shareId"\s*:\s*"(\d+)"')
# Broader JSON forms LinkedIn embeds depending on post type (reposts, video,
# articles, etc.): "shareUrn":"urn:li:share:N", "backendShareUrn":"...", and
# "fsd_share:N" tokens. Captures the trailing numeric ID.
_HTML_SHARE_URN_JSON_RE = re.compile(
    r'"(?:share|backendShareUrn|fsd_share|preDashEntityUrn)"\s*:\s*"urn:li:(?:fsd_)?share:(\d+)"'
)
_HTML_TRACKING_RE = re.compile(r'"trackingId"\s*:\s*"([A-Za-z0-9+/=_-]{16,})"')
# The top-level commentbox state key prefix is the FIRST occurrence of
# `commentBoxText-<prefix>FeedType_FEED_DETAIL`. The reply boxes (per-comment)
# render later in the document, so the first hit is the page's own composer.
# LinkedIn parses this prefix as a base64-protobuf — synthesizing one causes
# the create-comment handler to 500. Match both escaped and unescaped forms.
_HTML_COMMENTBOX_KEY_RE = re.compile(
    r'commentBoxText-([A-Za-z0-9+/=_\-]+?)FeedType_FEED_DETAIL'
)
# Per-update tracking ID lives next to feedUpdateUrn in each rendered template.
# `.{1,800}?` keeps the regex local to a single feedUpdateUrn block.
_HTML_FEED_TRACKING_RE = re.compile(
    r'\\?"feedUpdateUrn\\?"\s*:\s*\{.{1,800}?\\?"trackingId\\?"\s*:\s*\\?"([A-Za-z0-9+/=_\-]+?)\\?"',
    re.DOTALL,
)
# Which feedUpdateUrn variant — Activity vs Share — does LinkedIn render for
# this post? Posts with no separate share URN (most original text posts now)
# use updateUrnActivityUrn. Reposts/legacy use updateUrnShareUrn.
_HTML_FEED_KIND_ACTIVITY_RE = re.compile(r'\\?"updateUrnActivityUrn\\?"')
_HTML_FEED_KIND_SHARE_RE = re.compile(r'\\?"updateUrnShareUrn\\?"')


def _resolve_post_context(
    client, *, page_path: str, share_id: str | None, activity_id: str | None
) -> dict[str, str | None]:
    """Load the post HTML page and extract IDs + SDUI shape hints.

    Returns keys:
      - share_id, activity_id, tracking_id (as before)
      - update_kind: "activity" or "share" — which `feedUpdateUrn` variant
        LinkedIn renders for this post; controls payload shape below
      - state_key_prefix: the real base64-protobuf prefix LinkedIn uses for
        the top-level commentbox memory bindings, or None if not found.
        LinkedIn parses this prefix server-side — synthesized prefixes 500.

    Raises CommentError if the activity ID can't be located.

    Why two passes: the SDUI templates appear in the HTML as JSON inside JS
    string literals (so `"` becomes `\"`), while the initial-state islands use
    plain JSON. We use `\\?"` to match both forms in a single regex.
    """
    res = client._fetch(page_path, base_request=True)
    if res.status_code != 200:
        raise CommentError(
            f"Could not load post page {page_path} (HTTP {res.status_code}). "
            "The post may be deleted or restricted."
        )
    html = res.text or ""

    # Pull IDs from the final URL after redirects, if requests followed any.
    final_url = getattr(res, "url", "") or ""
    final_path = urlparse(final_url).path if final_url else ""
    if final_path.startswith("/posts/") or final_path.startswith("/mwlite/posts/"):
        m_p = _POSTS_NUMERIC_RE.search(final_path.rstrip("/"))
        if m_p:
            num_id = m_p.group(1)
            if "-share-" in final_path and not share_id:
                share_id = num_id
            elif "-activity-" in final_path and not activity_id:
                activity_id = num_id

    found_activity = activity_id
    if not found_activity:
        m = _HTML_ACTIVITY_RE.search(html) or _ACTIVITY_URN_RE.search(html)
        found_activity = m.group(1) if m else None
    if not found_activity:
        raise CommentError(
            "Could not extract activity ID from post page — LinkedIn may have "
            "changed the page shape, or the post is gated."
        )

    found_share = share_id
    if not found_share:
        m = (
            _HTML_SHARE_RE.search(html)
            or _HTML_SHARE_URN_JSON_RE.search(html)
            or _SHARE_URN_RE.search(html)
        )
        found_share = m.group(1) if m else None

    # Prefer the per-update trackingId embedded next to feedUpdateUrn; that's
    # the one LinkedIn actually validates. Fall back to any top-level
    # trackingId, then to a stub.
    update_tracking_m = _HTML_FEED_TRACKING_RE.search(html)
    if update_tracking_m:
        tracking_id = update_tracking_m.group(1)
    else:
        tracking_m = _HTML_TRACKING_RE.search(html)
        tracking_id = tracking_m.group(1) if tracking_m else "AAAAAAAAAAAAAAAAAA=="

    # Detect feedUpdateUrn variant. Share form only if it appears AND activity
    # form doesn't (the page schema can mention both as type definitions; we
    # care which one is in the actual rendered templates).
    has_activity_form = bool(_HTML_FEED_KIND_ACTIVITY_RE.search(html))
    has_share_form = bool(_HTML_FEED_KIND_SHARE_RE.search(html))
    if has_activity_form and not has_share_form:
        update_kind = "activity"
    elif has_share_form and not has_activity_form:
        update_kind = "share"
    elif has_activity_form and has_share_form:
        # Both rendered: pick whichever appears first in the document, since
        # the top-level update is rendered before any embedded reposts.
        first_act = html.find('updateUrnActivityUrn')
        first_shr = html.find('updateUrnShareUrn')
        update_kind = "activity" if 0 <= first_act < first_shr else "share"
    else:
        # Neither shape found — older posts where our share-default payload
        # was working. Keep that behaviour.
        update_kind = "share"

    # Extract the real commentbox state-key prefix. The first hit is the
    # top-level box; reply boxes render later.
    key_m = _HTML_COMMENTBOX_KEY_RE.search(html)
    state_key_prefix = key_m.group(1) if key_m else None

    # If we still don't have a share ID and this is a share-kind post,
    # fall back to the activity ID. The original payload had this behaviour;
    # we preserve it for the share branch only.
    if update_kind == "share" and not found_share:
        found_share = found_activity

    return {
        "share_id": found_share,
        "activity_id": found_activity,
        "tracking_id": tracking_id,
        "update_kind": update_kind,
        "state_key_prefix": state_key_prefix,
    }


def _build_sdui_create_comment_payload(
    *,
    share_id: str | None,
    activity_id: str,
    tracking_id: str,
    message: str,
    optimistic_key: str,
    state_id: str,
    update_kind: str = "share",
) -> dict[str, Any]:
    """Build the SDUI createComment payload mirroring the captured shape.

    Captured live 2026-05-07; see follow.py for the same SDUI pattern. Most of
    this is plumbing — the only fields that vary per-call are the IDs and the
    `states[*].value` entries that carry the comment text.

    `update_kind` selects the `feedUpdateUrn` / `threadUrn` variant:
      - "share":    legacy/repost shape with updateUrnShareUrn + shareUrn
      - "activity": original-post shape with updateUrnActivityUrn (no share ID)

    The `state_id` is used as the suffix for memory bindings. LinkedIn parses
    it as a base64-protobuf to identify the feed context, so when we have the
    real prefix from the page HTML we pass it through verbatim. Falling back
    to a synthesised prefix only works for the share branch on older posts.
    """
    if not state_id:
        # Fallback: synthesise a state ID with the required FeedType suffix.
        # Won't satisfy LinkedIn's proto-parse on newer posts, but matches the
        # legacy behaviour for the share branch on older ones.
        state_id = f"clinkedin{share_id or ''}{activity_id}FeedType_FEED_DETAIL"
    if not state_id.endswith("FeedType_FEED_DETAIL"):
        state_id = state_id + "FeedType_FEED_DETAIL"
    state_key_prefix = state_id

    def _binding(prefix: str) -> dict[str, str]:
        return {
            "key": f"{prefix}-{state_key_prefix}",
            "namespace": "MemoryNamespace",
        }

    field_bindings = {
        "commentFieldBinding": _binding("commentBoxText"),
        "richCommentFieldBinding": _binding("richCommentBoxText"),
        "linkPreviewIngestedContentId": _binding("commentBoxLinkPreviewIngestedContentId"),
        "externalImageUrl": _binding("commentBoxExternalImageUrl"),
    }

    state_keys = [
        {"key": {"value": {"$case": "id", "id": f"commentBoxText-{state_key_prefix}"}}},
        {"key": {"value": {"$case": "id", "id": f"richCommentBoxText-{state_key_prefix}"}}},
        {"key": {"value": {"$case": "id", "id": f"commentBoxLinkPreviewIngestedContentId-{state_key_prefix}"}}},
        {"key": {"value": {"$case": "id", "id": f"commentBoxExternalImageUrl-{state_key_prefix}"}}},
    ]

    states = [
        {
            "key": f"commentBoxText-{state_key_prefix}",
            "namespace": "MemoryNamespace",
            "value": message,
            "originalProtoCase": "stringValue",
        },
        {
            "key": f"richCommentBoxText-{state_key_prefix}",
            "namespace": "MemoryNamespace",
            "value": {
                "text": message,
                "attribute": [],
                "$type": "TextModel",
                "source": "local",
            },
            "originalProtoCase": "textModelForWrite",
        },
        {
            "key": f"commentBoxLinkPreviewIngestedContentId-{state_key_prefix}",
            "namespace": "MemoryNamespace",
            "value": "",
            "originalProtoCase": "stringValue",
        },
        {
            "key": f"commentBoxExternalImageUrl-{state_key_prefix}",
            "namespace": "MemoryNamespace",
            "value": "",
            "originalProtoCase": "stringValue",
        },
    ]

    if update_kind == "activity":
        feed_update_urn = {
            "updateUrnActivityUrn": {
                "__typename": "ActivityUrn",
                "activityUrn": {"activityId": activity_id},
            }
        }
    else:
        feed_update_urn = {
            "updateUrnShareUrn": {
                "__typename": "proto_com_linkedin_common_ShareUrn",
                "shareUrn": {"shareId": share_id or activity_id},
            }
        }

    request_payload = {
        "optimisticKey": optimistic_key,
        "collection": {
            "updateKey": {
                "feedType": 3,
                "items": [
                    {
                        "feedUpdateUrn": feed_update_urn,
                        "trackingId": tracking_id,
                    }
                ],
                "aggregationType": 0,
                "isVideoCarousel": False,
            },
            "threadUrn": {
                "threadUrnActivityThreadUrn": {
                    "__typename": "proto_com_linkedin_common_ActivityUrn",
                    "activityUrn": {"activityId": activity_id},
                }
            },
        },
        **field_bindings,
    }

    request_metadata = {
        "$type": "proto.sdui.common.RequestMetadata",
        "currentActor": {
            "$type": "proto.sdui.bindings.core.Bindable",
            "key": {
                "key": {
                    "value": {
                        "$case": "id",
                        "id": f"identitySwitcherActorContext-urn:li:activity:{activity_id}",
                    }
                }
            },
            "content": {"$case": "stringBinding", "stringBinding": {}},
        },
    }

    requested_arguments = {
        "$type": "proto.sdui.actions.requests.RequestedArguments",
        "payload": request_payload,
        "requestedStateKeys": state_keys,
        "requestMetadata": request_metadata,
    }

    on_failure = {
        "actions": [
            {
                "$type": "proto.sdui.actions.core.SetState",
                "value": {
                    "state": {
                        "key": {
                            "key": {
                                "value": {
                                    "$case": "id",
                                    "id": f"optimisticCommentShowSocialFooter-{optimistic_key}",
                                }
                            }
                        },
                        "value": {"$case": "booleanValue", "booleanValue": False},
                    }
                },
            },
            {
                "$type": "proto.sdui.actions.core.SetState",
                "value": {
                    "state": {
                        "key": {
                            "key": {
                                "value": {
                                    "$case": "id",
                                    "id": f"optimisticCommentShowRetry-{optimistic_key}",
                                }
                            }
                        },
                        "value": {"$case": "booleanValue", "booleanValue": True},
                    }
                },
            },
        ]
    }

    return {
        "requestId": "com.linkedin.sdui.comments.createComment",
        "serverRequest": {
            "requestId": "com.linkedin.sdui.comments.createComment",
            "requestedArguments": requested_arguments,
            "onClientRequestFailureAction": on_failure,
        },
        "states": states,
        "requestedArguments": {
            **requested_arguments,
            "states": states,
            "screenId": "com.linkedin.sdui.flagshipnav.feed.UpdateDetail",
        },
    }


def post_comment(client, post_url: str, message: str) -> dict[str, Any]:
    """Post a comment on a given LinkedIn post.

    `post_url` may be:
      - https://www.linkedin.com/feed/update/urn:li:activity:N/
      - https://www.linkedin.com/posts/<vanity>_<slug>-(activity|share)-N-XYZ
      - urn:li:activity:N  /  urn:li:share:N  /  urn:li:ugcPost:N

    Loads the post HTML page once (when the URL gives us only one of share/
    activity ID — both are required by the SDUI endpoint), then POSTs.
    """
    parsed = parse_post_url(post_url)
    share_id = parsed["share_id"]
    activity_id = parsed["activity_id"]
    page_path = parsed["page_path"]

    # We always need to load the post page now — it's the only reliable source
    # for the SDUI shape (activity vs share), the real state-key prefix, and
    # the per-update trackingId. Skipping it caused 500s on original posts.
    if not page_path:
        raise CommentError(
            f"Need a fetchable post URL to resolve LinkedIn's per-post SDUI "
            f"context; the bare URN {post_url!r} can't be loaded as HTML. "
            "Pass the public post URL (https://www.linkedin.com/posts/... or "
            "/feed/update/...) instead."
        )

    ctx = _resolve_post_context(
        client,
        page_path=page_path,
        share_id=share_id,
        activity_id=activity_id,
    )
    share_id = ctx["share_id"]
    activity_id = ctx["activity_id"]
    tracking_id = ctx["tracking_id"]
    update_kind = ctx.get("update_kind") or "share"
    state_key_prefix = ctx.get("state_key_prefix")

    optimistic_key = f"clinkedin-{uuid.uuid4()}"
    # Prefer LinkedIn's real state-key prefix from the page; fall back to a
    # synthesised one (legacy behaviour, works for share-kind posts).
    if state_key_prefix:
        state_id = f"{state_key_prefix}FeedType_FEED_DETAIL"
    else:
        state_id = f"clinkedin-{share_id or activity_id}-{activity_id}"

    payload = _build_sdui_create_comment_payload(
        share_id=share_id,
        activity_id=activity_id,
        tracking_id=tracking_id,
        message=message,
        optimistic_key=optimistic_key,
        state_id=state_id,
        update_kind=update_kind,
    )

    # Synthesize the page-context headers LinkedIn's web app sends. The
    # comment handler returned 500 when these were absent — it appears to
    # validate (or at least require the presence of) page-instance / forest /
    # trace headers. We generate plausible random values; LinkedIn doesn't
    # appear to cross-check these against real session state, only that they
    # exist and are well-formed.
    page_key = "d_flagship3_detail_base"
    page_tracking = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
    forest_id = secrets.token_hex(16)            # 32 hex chars
    span_id = secrets.token_hex(8)               # 16 hex chars
    parent_span_b64 = base64.b64encode(secrets.token_bytes(8)).decode("ascii")
    app_instance = base64.b64encode(secrets.token_bytes(16)).decode("ascii")

    headers = {
        "accept": "*/*",
        "content-type": "application/json",
        "origin": "https://www.linkedin.com",
        "referer": (
            f"https://www.linkedin.com{page_path}"
            if page_path
            else "https://www.linkedin.com/feed/"
        ),
        "x-li-rsc-stream": "true",
        "x-li-anchor-page-key": page_key,
        "x-li-application-instance": app_instance,
        "x-li-page-instance": f"urn:li:page:{page_key};{page_tracking}",
        "x-li-page-instance-tracking-id": page_tracking,
        "x-li-pageforestid": forest_id,
        "x-li-traceparent": f"00-{forest_id}-{span_id}-00",
        "x-li-tracestate": f"LinkedIn={span_id}",
    }
    res = client._post(
        "/flagship-web/rsc-action/actions/server-request"
        f"?sduiid=com.linkedin.sdui.comments.createComment&parentSpanId={parent_span_b64}",
        data=json.dumps(payload),
        headers=headers,
        base_request=True,
    )
    if not (200 <= res.status_code < 300):
        body = (res.text or "").strip().replace("\n", " ")[:300]
        raise CommentError(
            f"Comment failed for share:{share_id}/activity:{activity_id} "
            f"(HTTP {res.status_code}): {body or '<empty body>'}"
        )
    try:
        return res.json()
    except ValueError:
        return {}
