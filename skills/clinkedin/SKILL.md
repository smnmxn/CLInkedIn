---
name: clinkedin
description: Operate on the user's own LinkedIn account from the terminal. Use when the user wants to list/search their connections, send or remove connection invites, follow/unfollow people or companies, view a profile (bio or recent posts), search LinkedIn for people or posts, publish a new feed post (text and optional single image), post a public comment on a LinkedIn post, or reply in an existing direct-message conversation. Authenticated via the user's Chrome session — no API key. Includes destructive operations (invite, disconnect, post, comment, message) which require explicit confirmation.
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
clinkedin search people "" --network F --industry "Financial Services"  # 1st-degree in an industry
clinkedin search people "founder" --limit 25 --offset 25 --json
```

`--network` accepts a comma-separated subset of `F` (1st), `S` (2nd), `O` (3rd+). Default `--limit` is 25. Each result has a URN-form URL (`https://www.linkedin.com/in/ACoA…/`) that LinkedIn redirects to the canonical profile when opened in a browser.

`--industry` filters by a LinkedIn industry name (e.g. `"Financial Services"`, resolved offline to a code) or a raw numeric code; repeat to match any. Filtering is server-side, so rows don't show the industry value. To list "my connections in industry X", combine `--network F --industry "X"` — but `search` reads the search index, which lags removals, so `clinkedin connections` remains the authoritative source for the current connection set.

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

### Post a comment on a post

```sh
clinkedin comment https://www.linkedin.com/feed/update/urn:li:activity:7…/ --message "Great point"
clinkedin comment <url> --message-file ./reply.md      # for longer text
clinkedin comment <url> --message "…" --dry-run        # preview, send nothing
clinkedin comment <url> --message "…" --yes            # skip Y/N prompt (use sparingly for comments)
```

Accepted URL forms (all parsed to the same `urn:li:activity:<id>`):

- `https://www.linkedin.com/feed/update/urn:li:activity:N/`
- `https://www.linkedin.com/feed/update/urn:li:ugcPost:N/`
- `https://www.linkedin.com/posts/<vanity>_<slug>-activity-N-XYZ`
- A bare `urn:li:activity:N`

Comments are **public, persistent, and notify the post author**. Always show the user the exact text you intend to post and confirm before invoking — even if `--yes` is in your toolbox, prefer the interactive prompt for comments. v1 is plain text only (no mentions, hashtags, links with previews, or attachments). LinkedIn caps comment length at ~1250 chars; this CLI doesn't expose comment delete — retractions need the web UI.

**Cost:** one HTML page fetch (to scrape the post's `activityId` + `trackingId`, which LinkedIn's modern SDUI endpoint requires) + one SDUI POST. ~2 round trips per comment. Implemented against `/flagship-web/rsc-action/.../com.linkedin.sdui.comments.createComment` — captured live; this code path is fragile to LinkedIn UI changes and may need re-capture from DevTools when it next 400s.

### Publish a new feed post

```sh
clinkedin post --message "Shipping v2 today — link below"
clinkedin post --message-file ./announcement.md                  # for longer text
clinkedin post --message "Launch day" --image ~/Pictures/cover.png
clinkedin post --message "…" --dry-run                            # validate, publish nothing
clinkedin post --message "…" --yes                                # skip Y/N (avoid for posts)
```

Visibility is **hardcoded to PUBLIC in v1** — there's no `--visibility` flag yet. Images are limited to one per post (PNG or JPG, ≤10 MB). Body text cap is ~3000 chars (LinkedIn web composer; refined once confirmed from HAR). No mentions, hashtags-with-autocomplete, polls, documents, video, or article-link previews in v1.

Posts are **public, persistent, notify followers, and have no API delete** — retraction needs the web UI. **Always show the user the exact body text and image filename and confirm before invoking.** Prefer the interactive prompt over `--yes`.

**Status: pending HAR capture.** The networking calls (image-asset registration, asset PUT, create-share POST) are gated behind a HAR capture from a real LinkedIn web session — until the capture lands, `clinkedin post` validates inputs and dry-runs but refuses to publish (it raises a clear "HAR capture" error rather than blind-probing Voyager, which would risk 401-locking the session for hours). Dry-run is fully usable now.

To unblock: open linkedin.com in Chrome → DevTools → Network → "Preserve log" → create a text post and a single-image post → "Save all as HAR" → share the HAR file. One commit then wires the SDUI request id and payload shape into `post.py`.

### Send a direct message (reply only)

```sh
clinkedin message https://www.linkedin.com/messaging/thread/2-…/ --message "On my way!"
clinkedin message urn:li:msg_conversation:(urn:li:fsd_profile:ACoA…,2-…) --message "…"
clinkedin message 2-…    --message-file ./reply.md       # bare thread id form
clinkedin message <target> --message "…" --dry-run        # preview, send nothing
clinkedin message <target> --message "…" --yes            # skip Y/N prompt
```

Accepted target forms (all resolve to the same conversation):

- A LinkedIn messaging URL: `https://www.linkedin.com/messaging/thread/2-…/`
- A full conversation URN: `urn:li:msg_conversation:(urn:li:fsd_profile:ACoA…,2-…)`
- A bare thread id starting with `2-` (combined with the logged-in user's mailbox)

**Scope: reply-only.** This command sends into an **existing** conversation; it cannot start a brand-new thread with a recipient who has no prior conversation with the user. (The "first message to a stranger" payload shape is different and wasn't in the captured HAR — needs a fresh capture before it can be wired up.) If the user asks to "DM someone we've never messaged before," tell them this and suggest opening the thread once in the LinkedIn web UI first, then reusing the URL here.

Messages are **private, persistent, and notify the recipient** (push + email). Always show the user the exact text and confirm before sending — prefer the interactive prompt over `--yes` unless the user explicitly authorized that exact text. Plain text only; no attachments, mentions, or rich content in v1.

**Cost:** one POST to `/voyager/api/voyagerMessagingDashMessengerMessages?action=createMessage` (plus one `/me` lookup the first time per session, cached after). Captured live 2026-05-11; this bypasses `linkedin-api`'s dead `client.send_message` wrapper (the legacy `/messaging/conversations/<id>/events` path 500s).

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
- **Don't burst.** No tight loops, no fanning out many writes in seconds. The one sanctioned way to do volume is the **queue** (below), which runs at most one job per scheduled tick.
- **Note:** `clinkedin search posts` is web-scraping (not Voyager) so it doesn't share the same rate limit, but it can break when LinkedIn ships UI changes.

## Queue (bulk / deferred writes)

To do many writes (e.g. send a batch of invites) without bursting, **queue** them instead of sending immediately. Every destructive command accepts `--queue`:

```sh
clinkedin connect https://www.linkedin.com/in/<slug>/ --message "Hi" --queue   # records the job, no network call
clinkedin queue list                                                           # pending + dead-lettered jobs
clinkedin queue run                                                            # execute the single oldest job (FIFO), then exit
clinkedin queue remove <id> | clinkedin queue clear | clinkedin queue retry <id>
```

- `--queue` validates inputs and writes the job to `~/.config/clinkedin/queue/` **without contacting LinkedIn** — so queueing a batch is safe and never confirmation-gated (nothing is sent yet). Get the user's confirmation on the *content* as usual before they (or the scheduler) drain it.
- **Sends are jittered to look less like a bot.** `clinkedin queue install` writes a launchd agent that wakes every 60s but the runner gates each send: a randomized gap (default 5–20 min), an active-hours window (default 08:00–20:00 local), and an optional `--daily-cap`. `launchctl load -w ~/Library/LaunchAgents/com.clinkedin.queue.plist` to start, `clinkedin queue uninstall` to stop. The same gates are flags on `queue run` (`--min-interval`/`--max-interval`/`--active-hours`/`--daily-cap`); the cron equivalent line is printed by `queue install`.
- Failed jobs retry on the next few ticks, then dead-letter after 3 attempts (`queue list` shows the last error).
- **Jitter is hardening, not a cloak; volume matters more.** The bigger detection risks are the unofficial API and sheer volume. The gates space sends out but don't impose a weekly ceiling, so keep volume human-plausible: recommend the user set `--daily-cap` and enqueue at most a week's worth of invites (LinkedIn allows ~100–200/week) at a time.

## Choosing output format

- For human-readable summaries: omit `--json`. Use `--full` on `view --posts` if the user wants to read post bodies.
- For programmatic use (filtering, exporting, feeding into other tools): always pass `--json`. Pair with `--output <path>` to keep large results out of stdout.
- Exit codes: `0` success, `1` runtime/auth failure, `2` argument error.

## Confirmation policy

| Operation | Default behavior |
|---|---|
| `connections`, `following`, `view`, `search` | Run without confirmation — read-only. |
| `connect`, `disconnect`, `follow`, `unfollow` | **Ask the user first.** Show what will happen. Use `--dry-run` to preview when the user is unsure. |
| `comment` | **Show the exact comment text and confirm.** Comments are public, notify the author, and have no API delete. Default to interactive (don't auto-pass `--yes`) unless the user explicitly authorized that exact text. |
| `post` | **Show the exact body and image filename and confirm.** Posts are public, persistent, notify followers, and have no API delete. Default to interactive — strongly prefer the prompt over `--yes`. Visibility is hardcoded PUBLIC in v1. |
| `message` | **Show the exact message text and confirm.** Messages are private but notify the recipient (push + email) and can't be unsent by the API. Default to interactive (don't auto-pass `--yes`) unless the user explicitly authorized that exact text. |
| Bulk operations (multiple targets) | Prefer `--queue` so jobs drip out one per tick instead of bursting. Confirm the full list before enqueueing. |
| `queue list/run/remove/clear/retry/install` | Queue management — run without confirmation. `--queue` on a write command only records the job (no send), so it's not confirmation-gated; confirm the content before it's drained. |

## Troubleshooting

- **`HTTP 410` errors** — LinkedIn deprecated the legacy `/profileView` endpoint. The CLI has been migrated to the modern Dash endpoint; if you still see 410s, the install is stale (`uv tool install --editable` again).
- **`HTTP 400` on `following`, `follow`, `unfollow`, `comment`, or `message`** — usually means LinkedIn rotated the GraphQL `queryId` hash baked into the bundle (for `following`) or moved the SDUI / Voyager endpoint shape (for the others). All have hardcoded values that need re-capturing from a real LinkedIn web session via DevTools (Network tab → trigger the action → Copy as cURL → share). One commit usually fixes it.
- **`HTTP 401` / "session may be rate-limited"** — stop, do not retry. The session is locked for 30 minutes to several hours. Tell the user to pause and try again later.
- **`view --posts` returns empty after `--since`** — the user's posts may genuinely all be older than the window. Try a wider `--since` or omit it. If `--since 1y` is also empty, the user may have no posts visible to your session (1st-degree only / privacy settings).
- **Debug flags** — `view --posts --debug` dumps the raw `/identity/profileUpdatesV2` response; `following --debug` dumps the raw GraphQL Curation Hub response. Use only when investigating an unexpected error; each invocation is one more Voyager call against a fragile session.

## Help

```sh
clinkedin --help
clinkedin <command> --help
```
