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
clinkedin connections                                          # Name · Headline · Location · Company · URL
clinkedin connections --limit 20
clinkedin connections --limit 20 --offset 20                   # next page (items 20-39)
clinkedin connections --json --output connections.json

clinkedin connect https://www.linkedin.com/in/<slug>/          # prompts Y/N
clinkedin connect https://www.linkedin.com/in/<slug>/ --message "Hi, met at the conference"
clinkedin connect https://www.linkedin.com/in/<slug>/ --dry-run
clinkedin connect https://www.linkedin.com/in/<slug>/ --yes    # skip the prompt

clinkedin disconnect https://www.linkedin.com/in/<slug>/       # prompts Y/N — no undo
clinkedin disconnect https://www.linkedin.com/in/<slug>/ --yes

clinkedin follow https://www.linkedin.com/in/<slug>/           # follow a person (no connection request)
clinkedin follow https://www.linkedin.com/company/<slug>/      # follow a company
clinkedin unfollow https://www.linkedin.com/company/<slug>/    # stop following
clinkedin following                                            # list everyone you follow
clinkedin following --limit 50 --json --output following.json

clinkedin view https://www.linkedin.com/in/<slug>/             # name, headline, experience, education
clinkedin view https://www.linkedin.com/in/<slug>/ --json      # full raw profile JSON
clinkedin view https://www.linkedin.com/in/<slug>/ --posts                      # recent posts (default 10)
clinkedin view https://www.linkedin.com/in/<slug>/ --posts --full               # full untruncated text
clinkedin view https://www.linkedin.com/in/<slug>/ --posts --since 7d           # only posts from last 7 days
clinkedin view https://www.linkedin.com/in/<slug>/ --posts --limit 25 --json    # raw Voyager post JSON

clinkedin search people "product manager fintech"              # Name · Headline · Location · URL
clinkedin search people "designer" --network F,S --limit 50    # 1st + 2nd-degree only
clinkedin search people "founder" --limit 25 --offset 25       # next page (items 25-49)
clinkedin search people "founder" --json --output founders.json

clinkedin search posts "AI agents"                             # Text · URL
clinkedin search posts "RAG" --limit 5
clinkedin search posts "vector db" --json --output posts.json
```

`--network` accepts a comma-separated subset of `F` (1st), `S` (2nd), `O` (3rd+); omit to search all. `--limit` defaults to 25 for people. Each result includes a `url` field (URN-form, e.g. `https://www.linkedin.com/in/ACoAA…/`) — LinkedIn redirects this to the canonical profile in the browser.

Post search is implemented by **scraping** LinkedIn's web search-results page (Voyager doesn't expose a usable content-search endpoint). It returns `text` / `url` / `urn` per result, defaults to a `--limit` of 10, and only fetches the first page (~3–10 posts). Author, post date, and engagement counts are not extracted in v1. Comment search is **not** supported — LinkedIn doesn't expose it.

Because post search depends on the rendered web UI, it can break when LinkedIn ships UI changes. The CLI fails loud (with a saved-HTML dump path) rather than silently returning zero results — see `skills/linkedin_search_posts/SKILL.md` for failure modes.

The first run asks macOS Keychain for access to Chrome's cookie store — click **Always Allow**.

A session cookie is also cached at `~/.config/clinkedin/session.json` (mode `0600`) as a fallback for when Chrome is signed out. For users upgrading from the old `linkedin-cli` name, the old `~/.config/linkedin-cli/session.json` is still read as a fallback.

### Not using Chrome?

```sh
clinkedin login --cookie AQEDAR...    # paste li_at from your browser's DevTools
```

DevTools → Application → Cookies → `https://www.linkedin.com` → copy the `li_at` value.

## MCP server

An stdio MCP server exposing `linkedin_send_connection_request`,
`linkedin_follow`, `linkedin_unfollow`, and `linkedin_list_following` ships
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

`linkedin_send_connection_request` takes `profile_url` (required) and `message`
(optional, ≤300 chars) and returns `{"ok", "public_id", "error"}`.
`linkedin_follow` and `linkedin_unfollow` take `profile_url` (member or company)
and return `{"ok", "kind", "slug", "error"}`. `linkedin_list_following` takes
optional `limit` / `offset` and returns `{"ok", "results", "error"}` where each
result is `{"name", "kind", "public_id", "headline", "urn"}`. Auth is reused
from the same Chrome/`session.json` flow as the CLI.

## Agent use (OpenClaw / Claude skills)

A single portable skill at [`skills/clinkedin/`](skills/clinkedin/SKILL.md)
covers the whole CLI — list/search connections, send/remove invites,
follow/unfollow, view profiles and recent posts, and search people/posts.

Point your agent framework at this `skills/` directory (or symlink
`skills/clinkedin/` into its skills root) and the host will trigger it
automatically on matching LinkedIn requests.

## Warning

This tool uses LinkedIn's unofficial internal **Voyager** API (via the
[`linkedin-api`](https://pypi.org/project/linkedin-api/) library). That violates
LinkedIn's Terms of Service. Use it only for personal, low-volume access to your
own data. Do not run it on a schedule.

Invites-with-notes on a free account are limited to ~5/week; total invites to ~100–200/week.
