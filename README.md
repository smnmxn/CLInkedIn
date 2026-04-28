# CLInkedIn

A personal CLI for LinkedIn. Lists your connections and sends connection requests.

## Install

```sh
python -m venv .venv
.venv/bin/pip install -e .
```

## Use

Sign in to linkedin.com in Chrome once. That's your auth — the CLI reads your Chrome session automatically each run.

```sh
clinkedin connections                                          # Name · Headline · Location
clinkedin connections --limit 20
clinkedin connections --json --output connections.json

clinkedin connect https://www.linkedin.com/in/<slug>/          # prompts Y/N
clinkedin connect https://www.linkedin.com/in/<slug>/ --message "Hi, met at the conference"
clinkedin connect https://www.linkedin.com/in/<slug>/ --dry-run
clinkedin connect https://www.linkedin.com/in/<slug>/ --yes    # skip the prompt
```

The first run asks macOS Keychain for access to Chrome's cookie store — click **Always Allow**.

A session cookie is also cached at `~/.config/clinkedin/session.json` (mode `0600`) as a fallback for when Chrome is signed out. For users upgrading from the old `linkedin-cli` name, the old `~/.config/linkedin-cli/session.json` is still read as a fallback.

### Not using Chrome?

```sh
clinkedin login --cookie AQEDAR...    # paste li_at from your browser's DevTools
```

DevTools → Application → Cookies → `https://www.linkedin.com` → copy the `li_at` value.

## MCP server

An stdio MCP server exposing one tool, `linkedin_send_connection_request`, ships
under the `mcp` extra:

```sh
.venv/bin/pip install -e '.[mcp]'
```

Wire it into Claude Desktop (`~/Library/Application Support/Claude/claude_desktop_config.json`)
or any other MCP client:

```json
{
  "mcpServers": {
    "clinkedin": {
      "command": "/absolute/path/to/.venv/bin/clinkedin-mcp"
    }
  }
}
```

The tool takes `profile_url` (required) and `message` (optional, ≤300 chars). It
returns `{"ok": true|false, "public_id": ..., "error": ...}`. Auth is reused from
the same Chrome/`session.json` flow as the CLI.

## Agent use (OpenClaw / Claude skills)

The [`skills/`](skills/) directory ships two portable skills in the OpenClaw /
Claude-Code-Skills format (each is a directory with a `SKILL.md`):

- [`skills/linkedin_send_connection_request/`](skills/linkedin_send_connection_request/SKILL.md) — send a connection request to one profile (MCP tool + CLI).
- [`skills/linkedin_list_connections/`](skills/linkedin_list_connections/SKILL.md) — list / export the user's 1st-degree connections (CLI, read-only).

Point your agent framework at this `skills/` directory (or symlink the
individual skill dirs into its skills root) and the host will trigger them
automatically on matching LinkedIn requests.

## Warning

This tool uses LinkedIn's unofficial internal **Voyager** API (via the
[`linkedin-api`](https://pypi.org/project/linkedin-api/) library). That violates
LinkedIn's Terms of Service. Use it only for personal, low-volume access to your
own data. Do not run it on a schedule.

Invites-with-notes on a free account are limited to ~5/week; total invites to ~100–200/week.
