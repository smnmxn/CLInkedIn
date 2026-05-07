---
name: clinkedin
description: Operate on the user's own LinkedIn account from the terminal. Use when the user wants to list/search their connections, send or remove connection invites, follow/unfollow people or companies, view a profile (bio or recent posts), or search LinkedIn for people or posts. Authenticated via the user's Chrome session — no API key. Includes destructive operations (invite, disconnect) which require explicit confirmation.
---

# clinkedin

A personal CLI for the user's own LinkedIn account, exposed through the `clinkedin` shell command. All operations reuse the user's Chrome session (or a cached `li_at` cookie) — there is no API key and no separate sign-in flow.

This skill covers every clinkedin subcommand. Invoke them by shelling out; there is no in-conversation MCP equivalent for most.

## Setup (once per machine)

```sh
uv tool install --editable '/path/to/clinkedin'
```

The user must be signed in to linkedin.com in Chrome **once**. The CLI reads the Chrome cookie store automatically. macOS will prompt for Keychain access on first use — the user must click **Always Allow**.

Fallback if Chrome isn't available:

```sh
clinkedin login --cookie AQEDAR...   # paste li_at from DevTools → Cookies → linkedin.com
```

A session cookie is cached at `~/.config/clinkedin/session.json` (mode `0600`).

## Read-only commands

### List connections

```sh
clinkedin connections                                # Name · Headline · Location · Company · URL
clinkedin connections --limit 20 --offset 20         # paginated
clinkedin connections --json --output conns.json     # export JSON
```

JSON fields: `name`, `jobtitle`, `location`, `company`, `url`. 1st-degree only. Cap is roughly 1000 server-side; without `--limit` you get whatever the API returns.

### List who the user follows

```sh
clinkedin following                                       # everyone they follow (paginated, ~25 calls for 1000)
clinkedin following --limit 50 --json --output following.json
clinkedin following --limit 50 --offset 50                # next page (items 50–99)
```

Per-row JSON: `name`, `kind` (`member` | `company`), `public_id`, `headline`, `url`, `urn`. The `url` is URN-form (`https://www.linkedin.com/in/ACoA…/`); LinkedIn redirects it to the canonical vanity profile in a browser. Same convention as `clinkedin search people` results.

**LinkedIn caps `following` at the first 1000 results** even when the user follows more (LinkedIn UI shows a `totalResultCount` higher than the actual paging total). Nothing we can do about that; if the user has 2000 follows you'll only see the most recent 1000.

Implemented via the Curation Hub GraphQL endpoint (`/voyager/api/graphql?queryId=voyagerSearchDashClusters...`); the legacy REST `/feed/dash/followingStates` endpoint LinkedIn used to expose now returns HTTP 400 — we re-captured the modern queryId from a real LinkedIn web session.

### View a single profile

```sh
clinkedin view https://www.linkedin.com/in/<slug>/             # name, headline, summary
clinkedin view https://www.linkedin.com/in/<slug>/ --json      # raw Voyager Dash response
```

**Known regression:** Experience and education are not currently shown — LinkedIn deprecated the legacy `/profileView` endpoint and the dash endpoint we use doesn't include those sections. The output includes a hint pointing this out. The raw JSON (`--json`) does include nested URNs (`experienceCardUrn`) that could be fetched separately.

### View a profile's recent posts

```sh
clinkedin view https://www.linkedin.com/in/<slug>/ --posts                  # 10 posts, single-line each
clinkedin view https://www.linkedin.com/in/<slug>/ --posts --limit 25       # more
clinkedin view https://www.linkedin.com/in/<slug>/ --posts --full           # full untruncated text, multi-line per post
clinkedin view https://www.linkedin.com/in/<slug>/ --posts --since 7d       # only posts from last 7 days
clinkedin view https://www.linkedin.com/in/<slug>/ --posts --json           # raw post elements
```

Default text format: `date · "text (≤120 chars)" · N likes · M comments · url`. With `--full`: a header line followed by the full body, separated by `---`. Dates are relative ("1d", "2w", "3mo") since modern Voyager responses don't expose absolute timestamps at the top level.

`--since` accepts forms like `30m`, `5h`, `1d`, `7d`, `2w`, `3mo`, `1y`, or longer phrases like `"6 months ago"`. Filtering is approximate at the day level (months treated as 30 days, years as 365) because LinkedIn only exposes relative dates. When `--since` is set the CLI fetches up to 5× `--limit` from Voyager so that filtering doesn't silently empty the result. Posts whose age can't be parsed are dropped.

Common patterns:

```sh
# "What has X posted this week?"
clinkedin view <url> --posts --since 7d --full

# "Recap of someone's last month"
clinkedin view <url> --posts --since 1mo --limit 20 --full

# JSON for downstream filtering / summarization
clinkedin view <url> --posts --since 1mo --limit 50 --json --output posts.json
```

### Search people

```sh
clinkedin search people "product manager fintech"
clinkedin search people "designer" --network F,S --limit 50         # 1st + 2nd-degree
clinkedin search people "founder" --limit 25 --offset 25 --json
```

`--network` accepts a comma-separated subset of `F` (1st), `S` (2nd), `O` (3rd+). Default `--limit` is 25. Each result has a URN-form URL (`https://www.linkedin.com/in/ACoA…/`) that LinkedIn redirects to the canonical profile when opened in a browser.

### Search posts (web-scrape, fragile)

```sh
clinkedin search posts "AI agents"
clinkedin search posts "RAG" --limit 5 --json
```

Implemented by **scraping** LinkedIn's web search results page — Voyager doesn't expose a usable content-search endpoint. Returns `text` / `url` / `urn` per result. Default `--limit` is 10, only fetches one page (~3–10 posts). Author, post date, and engagement counts are **not** extracted. Comment search is **not** supported.

Failure modes are loud — the CLI prints a saved-HTML dump path on UI shape changes rather than silently returning zero results.

## Destructive commands — require confirmation

These mutate the user's network or messaging state. **Always** confirm with the user before invoking, and prefer `--dry-run` first.

### Send a connection request

```sh
clinkedin connect https://www.linkedin.com/in/<slug>/                                # interactive Y/N
clinkedin connect https://www.linkedin.com/in/<slug>/ --message "Hi, met at X"       # ≤300 chars
clinkedin connect https://www.linkedin.com/in/<slug>/ --dry-run                      # parse the URL, do nothing
clinkedin connect https://www.linkedin.com/in/<slug>/ --yes                          # skip the Y/N prompt
```

Free-account limits are tight: ~5 invites with notes per week, ~100–200 invites total per week. Hitting the limit silently fails — the API doesn't return a clear error.

### Remove an existing connection

```sh
clinkedin disconnect https://www.linkedin.com/in/<slug>/         # interactive Y/N — no undo
clinkedin disconnect https://www.linkedin.com/in/<slug>/ --yes
```

There is **no undo** — re-connecting requires a new invite. Always get explicit user confirmation. (This command bypasses `linkedin-api`'s dead `remove_connection` and calls `/relationships/dash/memberRelationships` directly.)

### Follow / unfollow

```sh
clinkedin follow https://www.linkedin.com/in/<slug>/             # follow a person (no connection request)
clinkedin follow https://www.linkedin.com/in/ACoA…/              # ACoA URN form also accepted (e.g. from `following` output)
clinkedin follow https://www.linkedin.com/company/<slug>/        # follow a company
clinkedin unfollow https://www.linkedin.com/in/<slug>/           # unfollow a person
clinkedin unfollow https://www.linkedin.com/company/<slug>/      # unfollow a company
clinkedin follow <url> --dry-run                                 # parse the URL, do nothing
clinkedin follow <url> --yes                                     # skip the Y/N prompt
```

Follow is non-destructive but visible to the followed person/company. Less risky than `connect` but still ask first.

**Cost:** member follow/unfollow now costs **two Voyager calls** (one Dash profile lookup to resolve the numeric member ID, then one POST to LinkedIn's modern SDUI follow-state endpoint). When batching across many profiles, space the calls out — bursts of >5/sec can throttle the session.

## Rate limits and session fragility

`clinkedin` uses LinkedIn's unofficial **Voyager** API. This is technically a TOS violation; use only for personal, low-volume access to the user's own data.

- **Bursts kill the session.** Multiple Voyager calls within seconds can 401-lock the entire session for 30 minutes to several hours. If a command returns an HTTP 401 or any "session may be rate-limited" error, **stop immediately** and tell the user to wait. Do not retry.
- **Fetch once, cache.** If you need to iterate over connections or search results, run the command once with `--json --output <path>`, then read the file.
- **Don't schedule it.** No cron, no loops, no "every N minutes" automations.
- **Note:** `clinkedin search posts` is web-scraping (not Voyager) so it doesn't share the same rate limit, but it can break when LinkedIn ships UI changes.

## Choosing output format

- For human-readable summaries: omit `--json`. Use `--full` on `view --posts` if the user wants to read post bodies.
- For programmatic use (filtering, exporting, feeding into other tools): always pass `--json`. Pair with `--output <path>` to keep large results out of stdout.
- Exit codes: `0` success, `1` runtime/auth failure, `2` argument error.

## Confirmation policy

| Operation | Default behavior |
|---|---|
| `connections`, `following`, `view`, `search` | Run without confirmation — read-only. |
| `connect`, `disconnect`, `follow`, `unfollow` | **Ask the user first.** Show what will happen. Use `--dry-run` to preview when the user is unsure. |
| Bulk operations (multiple targets) | Confirm the full list before sending the first request. Keep the burst small to protect the session. |

## Troubleshooting

- **`HTTP 410` errors** — LinkedIn deprecated the legacy `/profileView` endpoint. The CLI has been migrated to the modern Dash endpoint; if you still see 410s, the install is stale (`uv tool install --editable` again).
- **`HTTP 400` on `following`, `follow`, or `unfollow`** — usually means LinkedIn rotated the GraphQL `queryId` hash baked into the bundle (for `following`) or moved the SDUI endpoint shape (for `follow`/`unfollow`). Both have hardcoded values that need re-capturing from a real LinkedIn web session via DevTools (Network tab → trigger the action → Copy as cURL → share). One commit usually fixes it.
- **`HTTP 401` / "session may be rate-limited"** — stop, do not retry. The session is locked for 30 minutes to several hours. Tell the user to pause and try again later.
- **`view --posts` returns empty after `--since`** — the user's posts may genuinely all be older than the window. Try a wider `--since` or omit it. If `--since 1y` is also empty, the user may have no posts visible to your session (1st-degree only / privacy settings).
- **Debug flags** — `view --posts --debug` dumps the raw `/identity/profileUpdatesV2` response; `following --debug` dumps the raw GraphQL Curation Hub response. Use only when investigating an unexpected error; each invocation is one more Voyager call against a fragile session.

## Help

```sh
clinkedin --help
clinkedin <command> --help
```
