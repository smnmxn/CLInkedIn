---
name: linkedin_send_connection_request
description: Send a LinkedIn connection request to a specific profile URL via the user's own LinkedIn session. Use when the user says things like "connect with <person> on LinkedIn", "send a connection request to https://linkedin.com/in/...", "add <person> on LinkedIn", or provides a LinkedIn profile URL and asks you to invite them. Strictly single-target, personal-volume only — do NOT use for bulk outreach, lead generation, or scheduled sends. Requires a one-time sign-in to linkedin.com in Chrome (the skill reuses that cookie).
---

# linkedin_send_connection_request

Send one LinkedIn connection request at a time, optionally with a personal note. Two interfaces ship: an MCP tool (preferred when the host speaks MCP) and the `clinkedin connect` CLI command.

## Setup (once per machine)

```sh
# Install clinkedin system-wide (uv tool install is editable and puts shims in ~/.local/bin)
uv tool install --editable '/path/to/clinkedin[mcp]'
```

Then sign in to linkedin.com in Chrome — the CLI reads that session via cookie on first run (macOS will prompt for Keychain access; click **Always Allow**). If Chrome isn't available:

```sh
clinkedin login --cookie AQEDAR...   # paste li_at from DevTools → Cookies → linkedin.com
```

## Invoke via MCP (preferred)

Register `clinkedin-mcp` (stdio) with the host:

```json
{
  "mcpServers": {
    "clinkedin": { "command": "/Users/simon/.local/bin/clinkedin-mcp" }
  }
}
```

Tool:

- `linkedin_send_connection_request(profile_url: str, message: str = "") -> dict`
  - `profile_url` — full URL, e.g. `https://www.linkedin.com/in/<slug>/`
  - `message` — optional note, ≤300 characters
  - returns `{"ok": bool, "public_id": str, "message_sent": bool, "error": str?}`

## Invoke via CLI

```sh
clinkedin connect https://www.linkedin.com/in/<slug>/ --yes
clinkedin connect https://www.linkedin.com/in/<slug>/ --message "Hi, met at the conf" --yes
clinkedin connect https://www.linkedin.com/in/<slug>/ --dry-run
```

Exit codes: `0` sent, `1` runtime failure, `2` bad args. `--dry-run` previews without sending. Without `--yes` the CLI asks y/N on stdin — use `--yes` in non-interactive contexts.

## Rate limits — READ before sending more than one

LinkedIn's unofficial Voyager API is fragile under bursts:

- **Hard caps (free account)**: ~5 invites-with-notes per week, ~100–200 invites per week total.
- **Burst penalty**: rapid Voyager calls can 401 the whole session for 30 min to several hours. If a response says `rate-limited`, or you see `HTTP 401`, **stop** — do not retry. Wait ≥30 minutes and ask the user to re-auth if it persists.
- **Pacing**: send one profile at a time, confirm each target with the user, insert ≥30 seconds between calls.
- **Never**: bulk outreach, lead-gen, scheduled runs, or sending to lists without per-target user confirmation. This violates LinkedIn ToS and will get the user's account flagged.

When the user asks to connect with several people, push back and ask them to confirm each target individually.

## Error contract

| Return `error` substring | Meaning | Fix |
|---|---|---|
| `invalid URL: ...` | URL didn't parse as `linkedin.com/in/<slug>` | Re-check URL with user |
| `message too long (N/300)` | Note exceeds 300 chars | Shorten and retry |
| `not authenticated: ...` | No Chrome/session cookie available | Sign into linkedin.com in Chrome, or `clinkedin login --cookie <li_at>` |
| `Invite failed — you may already be connected...` | Generic Voyager reject | Surface verbatim; don't retry |
| `Profile lookup failed for <slug> (HTTP 401)` | Session expired or burst-banned | Re-auth; wait ≥30 min |

## Related

For reading the user's existing network (no invites), see the sibling skill `linkedin_list_connections`.
