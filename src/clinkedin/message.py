"""Send a LinkedIn direct message: reply in a thread, or message a profile.

WHY this doesn't call linkedin_api's `client.send_message`: that wrapper hits
the legacy `/voyager/api/messaging/conversations/<id>/events?action=create`
path, which now 500s for all keyVersions (probed 2026-05-10). LinkedIn has
moved messaging to `voyagerMessagingDashMessengerMessages?action=createMessage`,
captured live from the web app on 2026-05-11.

The same createMessage action starts a brand-new conversation when the payload
carries `hostRecipientUrns` instead of `message.conversationUrn` (captured
2026-10-12 from "Message" on a profile with no prior thread). Profile targets
mirror the web app: look up an existing thread with the recipient first and
reply there; only start a new conversation when none exists.
"""

from __future__ import annotations

import json
import random
import re
import uuid
from typing import Any
from urllib.parse import unquote, urlparse

from .invite import parse_profile_url, resolve_profile_urn, resolve_self_urn


class MessageError(Exception):
    pass


_FULL_CONV_URN_RE = re.compile(
    r"^urn:li:msg_conversation:"
    r"\(urn:li:fsd_profile:(ACoA[A-Za-z0-9_-]+),"
    r"(2-[A-Za-z0-9+/=_-]+)\)$"
)
_THREAD_ID_RE = re.compile(r"^2-[A-Za-z0-9+/=_-]+$")
_THREAD_URL_PATH_RE = re.compile(r"^/messaging/thread/(2-[A-Za-z0-9+/=_-]+)$")

# LinkedIn's web UI caps message text well above this; we pick a sane upper
# bound to surface obviously-pathological inputs early rather than after a POST.
MAX_MESSAGE_LEN = 8000


def parse_thread_target(client, target: str) -> tuple[str, str]:
    """Resolve the user's target to (mailbox_urn, thread_id).

    Accepts:
      - https://www.linkedin.com/messaging/thread/<thread-id>/
      - urn:li:msg_conversation:(urn:li:fsd_profile:ACoA...,2-...)
      - bare thread id of the form 2-XXX

    For URL and bare-id inputs the mailbox URN is the logged-in viewer
    (one extra Voyager call, cached on the client).
    """
    raw = (target or "").strip().rstrip("/")
    if not raw:
        raise ValueError("Empty target.")

    m = _FULL_CONV_URN_RE.match(raw)
    if m:
        return f"urn:li:fsd_profile:{m.group(1)}", m.group(2)

    if _THREAD_ID_RE.match(raw):
        return resolve_self_urn(client), raw

    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if host == "linkedin.com" or host.endswith(".linkedin.com"):
        path = unquote(parsed.path.rstrip("/"))
        m_p = _THREAD_URL_PATH_RE.match(path)
        if m_p:
            return resolve_self_urn(client), m_p.group(1)

    raise ValueError(
        f"Not a recognised conversation target: {target!r}. Pass a thread URL "
        "(https://www.linkedin.com/messaging/thread/2-…/), a full "
        "urn:li:msg_conversation:(…) URN, or a bare 2-… thread id."
    )


def build_conversation_urn(mailbox_urn: str, thread_id: str) -> str:
    return f"urn:li:msg_conversation:({mailbox_urn},{thread_id})"


def _tracking_id() -> str:
    # Mirror linkedin-api's `generate_trackingId_as_charString`: 16 raw bytes
    # interpreted as latin-1. JSON will encode high-bit bytes as \uXXXX which
    # the createMessage handler accepts (captured live).
    return "".join(chr(random.randrange(256)) for _ in range(16))


def send_message(
    client,
    mailbox_urn: str,
    thread_id: str,
    text: str,
) -> dict[str, Any]:
    """POST a plain-text reply. Raises MessageError on non-2xx."""
    conv_urn = build_conversation_urn(mailbox_urn, thread_id)
    payload = {
        "message": {
            "body": {"attributes": [], "text": text},
            "renderContentUnions": [],
            "conversationUrn": conv_urn,
            "originToken": str(uuid.uuid4()),
        },
        "mailboxUrn": mailbox_urn,
        "trackingId": _tracking_id(),
        "dedupeByClientGeneratedToken": False,
    }
    res = client._post(
        "/voyagerMessagingDashMessengerMessages",
        params={"action": "createMessage"},
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "accept": "application/json",
            "content-type": "text/plain;charset=UTF-8",
        },
    )
    if not (200 <= res.status_code < 300):
        body = (res.text or "").strip().replace("\n", " ")[:300]
        raise MessageError(
            f"Send-message failed for thread {thread_id} "
            f"(HTTP {res.status_code}): {body or '<empty body>'}"
        )
    try:
        return res.json()
    except ValueError:
        return {}


_PROFILE_URN_RE = re.compile(r"^urn:li:fsd_profile:(ACoA[A-Za-z0-9_-]+)$")
_NEW_CONV_URN_RE = re.compile(
    r"urn:li:msg_conversation:\(urn:li:fsd_profile:ACoA[A-Za-z0-9_-]+,(2-[A-Za-z0-9+/=_-]+)\)"
)


def _profile_target(target: str) -> str | None:
    """Return a profile identifier (vanity slug or ACoA id) if `target` names a
    person rather than a thread, else None."""
    raw = (target or "").strip()
    m = _PROFILE_URN_RE.match(raw)
    if m:
        return m.group(1)
    try:
        return parse_profile_url(raw)
    except ValueError:
        return None


def resolve_message_target(client, target: str) -> dict[str, Any]:
    """Resolve any supported target to where a message should go.

    Returns {"mailbox_urn", "thread_id", "recipient_urn", "label"}:
      - thread targets: thread_id set, recipient_urn None.
      - profile targets: recipient_urn set; thread_id is the existing 1:1
        thread with that person, or None when a new conversation is needed.
    Raises ValueError for unrecognised targets.
    """
    profile_id = _profile_target(target)
    if profile_id is None:
        mailbox_urn, thread_id = parse_thread_target(client, target)
        return {
            "mailbox_urn": mailbox_urn,
            "thread_id": thread_id,
            "recipient_urn": None,
            "label": thread_id,
        }

    from .inbox import find_thread_with  # lazy: inbox imports this module

    acoa = profile_id if profile_id.startswith("ACoA") else resolve_profile_urn(client, profile_id)
    recipient_urn = f"urn:li:fsd_profile:{acoa}"
    mailbox_urn = resolve_self_urn(client)
    return {
        "mailbox_urn": mailbox_urn,
        "thread_id": find_thread_with(client, mailbox_urn, recipient_urn),
        "recipient_urn": recipient_urn,
        "label": profile_id,
    }


def start_conversation(
    client,
    mailbox_urn: str,
    recipient_urn: str,
    text: str,
) -> str:
    """POST a first message to `recipient_urn`, creating a new conversation.

    Returns the new thread id when the response carries it, else "".
    Raises MessageError on non-2xx (e.g. the recipient isn't a connection).
    """
    payload = {
        "message": {
            "body": {"attributes": [], "text": text},
            "renderContentUnions": [],
            "originToken": str(uuid.uuid4()),
        },
        "mailboxUrn": mailbox_urn,
        "trackingId": _tracking_id(),
        "dedupeByClientGeneratedToken": False,
        "hostRecipientUrns": [recipient_urn],
    }
    res = client._post(
        "/voyagerMessagingDashMessengerMessages",
        params={"action": "createMessage"},
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "accept": "application/json",
            "content-type": "text/plain;charset=UTF-8",
        },
    )
    if not (200 <= res.status_code < 300):
        body = (res.text or "").strip().replace("\n", " ")[:300]
        raise MessageError(
            f"New-conversation send to {recipient_urn} failed "
            f"(HTTP {res.status_code}): {body or '<empty body>'}. "
            "LinkedIn only allows free messages to 1st-degree connections."
        )
    m = _NEW_CONV_URN_RE.search(res.text or "")
    return m.group(1) if m else ""
