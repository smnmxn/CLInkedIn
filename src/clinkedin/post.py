"""Create a new LinkedIn feed post (text + optional single image).

v1 scope:
  - plain text body
  - optional single image (PNG/JPG, <= 10 MB)
  - visibility hardcoded to PUBLIC (ANYONE)

Live request shapes were captured 2026-05-26 from a real web session.
See ``memory/post_creation_endpoint.md`` for the capture date and queryId
so the next rotation is easy to spot. Blind-probing risks 401-locking the
session for hours (see ``memory/voyager_rate_limits.md``).

Three calls in sequence when an image is attached; only the third for text:
  1. POST /voyager/api/voyagerVideoDashMediaUploadMetadata?action=upload
     -> {data.value.singleUploadUrl, data.value.urn}
  2. PUT <singleUploadUrl>  (raw image bytes)
  3. POST /voyager/api/graphql?action=execute&queryId=
     voyagerContentcreationDashShares.<hash>
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
from pathlib import Path
from typing import Any

MAX_BODY_LEN = 3000  # LinkedIn web composer cap; refine if surfaced from API
MAX_IMAGE_BYTES = 10 * 1024 * 1024
ALLOWED_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg"})

VISIBILITY = "PUBLIC"

# Captured 2026-05-26 from a real session. If this rotates, the create-share
# POST will likely 4xx — re-capture and update.
_CREATE_SHARE_QUERY_ID = "voyagerContentcreationDashShares.279996efa5064c01775d5aff003d9377"
_UPLOAD_METADATA_PATH = "/voyagerVideoDashMediaUploadMetadata"
_GRAPHQL_PATH = "/graphql"


class PostError(Exception):
    pass


def validate_body(body: str) -> str:
    """Strip + validate post body text. Raises PostError on empty / too long."""
    text = (body or "").rstrip()
    if not text:
        raise PostError("Post body is empty.")
    if len(text) > MAX_BODY_LEN:
        raise PostError(f"Post body too long ({len(text)}/{MAX_BODY_LEN}).")
    return text


def validate_image_path(image_path: str | None) -> Path | None:
    """Validate an optional image path. Returns the resolved Path or None.

    Raises PostError on: missing file, wrong extension, oversize.
    """
    if image_path is None:
        return None
    p = Path(image_path).expanduser()
    if not p.is_file():
        raise PostError(f"Image not found: {image_path}")
    ext = p.suffix.lower()
    if ext not in ALLOWED_IMAGE_EXTS:
        allowed = ", ".join(sorted(ALLOWED_IMAGE_EXTS))
        raise PostError(
            f"Image extension {ext!r} not supported (allowed: {allowed})."
        )
    size = os.path.getsize(p)
    if size > MAX_IMAGE_BYTES:
        raise PostError(
            f"Image too large: {size} bytes (limit {MAX_IMAGE_BYTES})."
        )
    return p


def _extract_upload_metadata(resp_json: dict) -> tuple[str, str]:
    """Pull (singleUploadUrl, asset_urn) out of the register response.

    LinkedIn's normalized+json wraps the action result in a couple of places
    depending on the day; try the known shapes and fall back to deriving the
    URN from the URL path if the explicit field is missing.
    """
    candidates: list[dict] = []
    if isinstance(resp_json, dict):
        candidates.append(resp_json)
        data = resp_json.get("data")
        if isinstance(data, dict):
            candidates.append(data)
            value = data.get("value")
            if isinstance(value, dict):
                candidates.append(value)
        value_top = resp_json.get("value")
        if isinstance(value_top, dict):
            candidates.append(value_top)

    single_url = None
    asset_urn = None
    for c in candidates:
        if single_url is None:
            single_url = c.get("singleUploadUrl") or c.get("uploadUrl")
        if asset_urn is None:
            asset_urn = c.get("urn") or c.get("mediaArtifactUrn") or c.get("vectorAssetRealtimeUrn")
        if single_url and asset_urn:
            break

    if not single_url:
        raise PostError(
            "Image upload registration returned no singleUploadUrl. "
            "LinkedIn may have rotated the endpoint shape — re-capture HAR."
        )

    if not asset_urn:
        # The asset id is the first segment after /sp/sync/v2/ in the URL.
        m = re.search(r"/sp/sync/v\d+/([A-Za-z0-9_-]+)/", single_url)
        if not m:
            raise PostError(
                "Could not derive asset URN from upload URL; "
                f"unexpected shape: {single_url!r}"
            )
        asset_urn = f"urn:li:digitalmediaAsset:{m.group(1)}"

    return single_url, asset_urn


def _upload_image(client, image_path: Path) -> str:
    """Register an upload, PUT the bytes, return the asset URN.

    Two-step flow:
      1. POST /voyagerVideoDashMediaUploadMetadata?action=upload  →
         {singleUploadUrl, urn}
      2. PUT image bytes to singleUploadUrl with content-type image/* and
         a `media-type-family: STILLIMAGE` header.
    """
    size = os.path.getsize(image_path)
    payload = {
        "mediaUploadType": "IMAGE_SHARING",
        "fileSize": size,
        "filename": image_path.name,
    }
    res = client._post(
        _UPLOAD_METADATA_PATH,
        params={"action": "upload"},
        data=json.dumps(payload),
        headers={
            "accept": "application/vnd.linkedin.normalized+json+2.1",
            "content-type": "application/json; charset=UTF-8",
        },
    )
    if not (200 <= res.status_code < 300):
        body = (res.text or "").strip().replace("\n", " ")[:300]
        raise PostError(
            f"Image upload registration failed "
            f"(HTTP {res.status_code}): {body or '<empty body>'}"
        )
    try:
        meta = res.json()
    except ValueError:
        raise PostError(
            "Image upload registration returned non-JSON; "
            "LinkedIn may have rotated the endpoint."
        )

    single_url, asset_urn = _extract_upload_metadata(meta)

    # Step 2: PUT the bytes. Uses the same session so cookies + csrf-token
    # are reused. content-type is inferred from extension.
    mime, _ = mimetypes.guess_type(image_path.name)
    if not mime or not mime.startswith("image/"):
        mime = "image/png"
    with image_path.open("rb") as f:
        body_bytes = f.read()
    put_res = client.client.session.put(
        single_url,
        data=body_bytes,
        headers={
            "accept": "*/*",
            "content-type": mime,
            "media-type-family": "STILLIMAGE",
            "origin": "https://www.linkedin.com",
        },
    )
    if not (200 <= put_res.status_code < 300):
        body = (put_res.text or "").strip().replace("\n", " ")[:300]
        raise PostError(
            f"Image bytes upload failed "
            f"(HTTP {put_res.status_code}): {body or '<empty body>'}"
        )

    return asset_urn


def _build_create_post_payload(
    body: str,
    asset_urn: str | None,
    alt_text: str = "",
) -> dict[str, Any]:
    """Build the GraphQL variables.post payload for createShare."""
    post: dict[str, Any] = {
        "allowedCommentersScope": "ALL",
        "intendedShareLifeCycleState": "PUBLISHED",
        "origin": "FEED",
        "visibilityDataUnion": {"visibilityType": "ANYONE"},
        "commentary": {"text": body, "attributesV2": []},
    }
    if asset_urn:
        post["media"] = {
            "category": "IMAGE",
            "mediaUrn": asset_urn,
            "tapTargets": [],
            "altText": alt_text,
        }
    return {
        "variables": {"post": post},
        "queryId": _CREATE_SHARE_QUERY_ID,
        "includeWebMetadata": True,
    }


def _extract_share_result(resp_json: Any) -> dict[str, str | None]:
    """Best-effort extraction of activity / share URNs from createShare.

    The response is a normalized+json blob; URNs can hide in multiple keys
    or as substring matches in `data.value.entityUrn`. We pull what we can
    and let the caller fall back to "Post published." if nothing matches.
    """
    out: dict[str, str | None] = {"activity_urn": None, "share_urn": None}

    def _walk(node: Any):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, str):
                    if out["activity_urn"] is None and v.startswith("urn:li:activity:"):
                        out["activity_urn"] = v
                    if out["share_urn"] is None and v.startswith("urn:li:share:"):
                        out["share_urn"] = v
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(resp_json)
    return out


def create_post(
    client,
    body: str,
    image_path: str | None = None,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create a feed post on the authenticated user's profile.

    Returns a dict with `activity_urn` / `share_url` (best-effort) on success.
    Raises PostError on validation or HTTP failure.

    When `dry_run` is True the function validates inputs and returns a
    preview dict without making any network calls.
    """
    text = validate_body(body)
    image = validate_image_path(image_path)

    if dry_run:
        return {
            "dry_run": True,
            "body": text,
            "body_chars": len(text),
            "image": str(image) if image else None,
            "visibility": VISIBILITY,
        }

    asset_urn: str | None = None
    if image is not None:
        asset_urn = _upload_image(client, image)

    payload = _build_create_post_payload(text, asset_urn)
    res = client._post(
        _GRAPHQL_PATH,
        params={"action": "execute", "queryId": _CREATE_SHARE_QUERY_ID},
        data=json.dumps(payload),
        headers={
            "accept": "application/vnd.linkedin.normalized+json+2.1",
            "content-type": "application/json; charset=UTF-8",
        },
    )
    if not (200 <= res.status_code < 300):
        body_snip = (res.text or "").strip().replace("\n", " ")[:300]
        raise PostError(
            f"Create-share failed (HTTP {res.status_code}): "
            f"{body_snip or '<empty body>'}"
        )

    try:
        resp_json = res.json()
    except ValueError:
        resp_json = None

    urns = _extract_share_result(resp_json) if resp_json is not None else {}
    activity_urn = urns.get("activity_urn")
    share_url = (
        f"https://www.linkedin.com/feed/update/{activity_urn}/"
        if activity_urn
        else None
    )
    return {
        "activity_urn": activity_urn,
        "share_urn": urns.get("share_urn"),
        "share_url": share_url,
        "asset_urn": asset_urn,
    }
