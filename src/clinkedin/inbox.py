"""List inbox conversations and read the messages in a thread.

WHY these endpoints: messaging reads moved off the legacy
`/messaging/conversations` REST resource to the Voyager messaging GraphQL
endpoint (`/voyagerMessagingGraphQL/graphql`), captured live from the web app
on 2026-10-12. Requests must send `accept: application/graphql`.

The queryId hashes are rotated by LinkedIn on bundle releases; if these start
returning 400/500, re-capture from DevTools (Network tab on
https://www.linkedin.com/messaging/, filter on `messenger`):
  - inbox page (scroll the conversation list to get the cursor form)
  - thread messages (open a thread; scroll up for the older-messages form)
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from urllib.parse import quote

from .invite import resolve_self_urn
from .message import build_conversation_urn, parse_thread_target


class InboxError(Exception):
    pass


_GRAPHQL_PATH = "/voyagerMessagingGraphQL/graphql"
_HEADERS = {"accept": "application/graphql"}

# Category-filtered inbox listing. Same query serves page 1 (no cursor) and
# later pages (`nextCursor` from the previous page's metadata).
_CONVERSATIONS_QUERY_ID = "messengerConversations.9501074288a12f3ae9e3c7ea243bccbf"
# Latest messages in a conversation (what the web app loads on opening a thread).
_MESSAGES_QUERY_ID = "messengerMessages.5846eeb71c981f11e0134cb6626cc314"
# Messages delivered before an anchor timestamp (scroll-up in a thread).
_MESSAGES_BEFORE_QUERY_ID = "messengerMessages.d8ea76885a52fd5dc5c317078ab7c977"

# Latest conversation with a given set of recipients (what the web app checks
# when "Message" is clicked on a profile). Empty elements = no prior thread.
_BY_RECIPIENTS_QUERY_ID = "messengerConversations.9c3ab648b616451570c715e4a184465e"

# The web app asks for 20 per page; keep to what it sends.
_PAGE_SIZE = 20
# Hard stop on round trips per invocation; bursts of Voyager calls can lock the
# session.
_MAX_PAGES = 5


def _enc(value: str) -> str:
    return quote(value, safe="")


def _graphql_get(client, query_id: str, variables: str, what: str) -> dict[str, Any]:
    """GET one messaging GraphQL page and return the single `data.messenger*` collection."""
    res = client._fetch(
        f"{_GRAPHQL_PATH}?queryId={query_id}&variables={variables}", headers=_HEADERS
    )
    if res.status_code != 200:
        raise InboxError(
            f"{what} fetch failed (HTTP {res.status_code}). Your session may be "
            "expired or rate-limited (try 'clinkedin login'), or LinkedIn rotated "
            "the queryId."
        )
    try:
        data = res.json()
    except ValueError as e:
        raise InboxError(f"{what} fetch returned non-JSON.") from e
    payload = (data or {}).get("data") if isinstance(data, dict) else None
    if not isinstance(payload, dict):
        raise InboxError(f"{what} fetch returned unexpected shape.")
    for key, value in payload.items():
        if key.startswith("messenger") and isinstance(value, dict):
            return value
    raise InboxError(f"{what} fetch returned no messenger collection.")


def _text(node: Any) -> str:
    """Unwrap an AttributedText ({"text": ...}) or return ''."""
    if isinstance(node, dict):
        return node.get("text") or ""
    return ""


def _participant(p: dict[str, Any]) -> dict[str, Any]:
    """Normalize a MessagingParticipant. participantType is a union keyed by kind."""
    ptype = p.get("participantType") or {}
    member = ptype.get("member")
    if isinstance(member, dict):
        name = " ".join(
            s for s in (_text(member.get("firstName")), _text(member.get("lastName"))) if s
        )
        return {
            "name": name,
            "headline": _text(member.get("headline")),
            "url": member.get("profileUrl") or "",
            "urn": p.get("hostIdentityUrn") or "",
        }
    for kind in ("organization", "custom", "agent"):
        node = ptype.get(kind)
        if isinstance(node, dict):
            return {
                "name": _text(node.get("name")) or kind,
                "headline": "",
                "url": node.get("pageUrl") or node.get("url") or "",
                "urn": p.get("hostIdentityUrn") or "",
            }
    return {"name": "", "headline": "", "url": "", "urn": p.get("hostIdentityUrn") or ""}


def _iso(ms: Any) -> str:
    if not isinstance(ms, (int, float)):
        return ""
    return datetime.fromtimestamp(ms / 1000).astimezone().isoformat(timespec="seconds")


def _message_fields(m: dict[str, Any]) -> dict[str, Any]:
    sender = _participant(m.get("sender") or {})
    text = _text(m.get("body"))
    # Attachments, shared posts, GIFs etc. have an empty body and a fallback
    # description instead.
    if not text and m.get("renderContentFallbackText"):
        text = f"[{m['renderContentFallbackText']}]"
    return {
        "sent_at": _iso(m.get("deliveredAt")),
        "delivered_at_ms": m.get("deliveredAt"),
        "sender": sender["name"],
        "sender_url": sender["url"],
        "sender_urn": sender["urn"],
        "text": text,
        "urn": m.get("entityUrn") or "",
    }


def _conversation_fields(c: dict[str, Any], self_urn: str) -> dict[str, Any]:
    participants = [_participant(p) for p in c.get("conversationParticipants") or []]
    others = [p for p in participants if p["urn"] != self_urn] or participants
    last_elements = (c.get("messages") or {}).get("elements") or []
    last = _message_fields(last_elements[0]) if last_elements else None
    if last and not last["sender"]:
        # The inbox listing's embedded message carries only the sender's URN;
        # names live on the conversation participants.
        by_urn = {p["urn"]: p for p in participants}
        match = by_urn.get(last["sender_urn"])
        if match:
            last["sender"] = match["name"]
            last["sender_url"] = match["url"]
    url = c.get("conversationUrl") or ""
    thread_id = url.rstrip("/").rsplit("/", 1)[-1] if url else ""
    return {
        "thread_id": thread_id,
        "conversation_urn": c.get("entityUrn") or "",
        "url": url,
        "title": c.get("title") or ", ".join(p["name"] for p in others if p["name"]),
        "participants": others,
        "group": bool(c.get("groupChat")),
        "unread_count": c.get("unreadCount") or 0,
        "read": bool(c.get("read")),
        "last_activity_at": _iso(c.get("lastActivityAt")),
        "last_sender": (last or {}).get("sender", ""),
        "last_sender_is_me": bool(last) and last["sender_urn"] == self_urn,
        "last_message": (last or {}).get("text", ""),
    }


def fetch_conversations(
    client, limit: int = 20, *, unread_only: bool = False
) -> list[dict[str, Any]]:
    """List inbox conversations, most recent activity first.

    `unread_only` filters client-side (the web app's Unread tab uses a separate
    query that we haven't captured), so it can page further to fill `limit`.
    """
    self_urn = resolve_self_urn(client)
    rows: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(_MAX_PAGES):
        variables = (
            "(query:(predicateUnions:List((conversationCategoryPredicate:(category:INBOX)))),"
            f"count:{_PAGE_SIZE},mailboxUrn:{_enc(self_urn)}"
            + (f",nextCursor:{_enc(cursor)}" if cursor else "")
            + ")"
        )
        page = _graphql_get(client, _CONVERSATIONS_QUERY_ID, variables, "Inbox")
        elements = page.get("elements") or []
        for c in elements:
            if not isinstance(c, dict):
                continue
            row = _conversation_fields(c, self_urn)
            if unread_only and not row["unread_count"] and row["read"]:
                continue
            rows.append(row)
        cursor = (page.get("metadata") or {}).get("nextCursor")
        if len(rows) >= limit or not elements or not cursor:
            break
    return rows[:limit]


def find_thread_with(client, mailbox_urn: str, recipient_urn: str) -> str | None:
    """Return the thread id of the existing 1:1 conversation with
    `recipient_urn`, or None if the viewer has never messaged them."""
    variables = f"(mailboxUrn:{_enc(mailbox_urn)},recipients:List({_enc(recipient_urn)}))"
    page = _graphql_get(client, _BY_RECIPIENTS_QUERY_ID, variables, "Conversation lookup")
    for c in page.get("elements") or []:
        if not isinstance(c, dict) or c.get("groupChat"):
            continue
        url = (c.get("conversationUrl") or "").rstrip("/")
        if url:
            return url.rsplit("/", 1)[-1]
    return None


def fetch_messages(client, target: str, limit: int = 20) -> list[dict[str, Any]]:
    """Return up to `limit` most recent messages in a thread, oldest first."""
    mailbox_urn, thread_id = parse_thread_target(client, target)
    conv = _enc(build_conversation_urn(mailbox_urn, thread_id))

    page = _graphql_get(
        client, _MESSAGES_QUERY_ID, f"(conversationUrn:{conv})", "Thread"
    )
    raw = [m for m in page.get("elements") or [] if isinstance(m, dict)]
    seen = {m.get("entityUrn") for m in raw}

    for _ in range(_MAX_PAGES - 1):
        if len(raw) >= limit or not raw:
            break
        oldest = min(m.get("deliveredAt") or 0 for m in raw)
        older = _graphql_get(
            client,
            _MESSAGES_BEFORE_QUERY_ID,
            f"(deliveredAt:{oldest},conversationUrn:{conv},countBefore:{_PAGE_SIZE},countAfter:0)",
            "Thread",
        )
        new = [
            m
            for m in older.get("elements") or []
            if isinstance(m, dict) and m.get("entityUrn") not in seen
        ]
        if not new:
            break
        seen.update(m.get("entityUrn") for m in new)
        raw.extend(new)

    raw.sort(key=lambda m: m.get("deliveredAt") or 0)
    return [_message_fields(m) for m in raw[-limit:]]


def format_json(rows: list[dict[str, Any]]) -> str:
    return json.dumps(rows, indent=2, ensure_ascii=False)


def _short_time(iso: str) -> str:
    # 2026-10-12T17:57:54+01:00 -> 2026-10-12 17:57
    return iso[:16].replace("T", " ") if iso else "?"


def format_conversations(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No conversations."
    lines = []
    for r in rows:
        marker = f"[{r['unread_count']} unread] " if r["unread_count"] else ""
        who = "You" if r["last_sender_is_me"] else (r["last_sender"] or "?")
        snippet = " ".join((r["last_message"] or "").split())
        if len(snippet) > 100:
            snippet = snippet[:99] + "…"
        lines.append(f"{_short_time(r['last_activity_at'])}  {marker}{r['title'] or '(untitled)'}")
        lines.append(f"    {who}: {snippet}")
        lines.append(f"    {r['url']}")
    return "\n".join(lines)


def format_messages(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No messages."
    blocks = []
    for r in rows:
        header = f"{_short_time(r['sent_at'])} · {r['sender'] or '?'}"
        blocks.append(header + "\n" + (r["text"] or ""))
    return "\n\n".join(blocks)
