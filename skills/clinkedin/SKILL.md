---
name: clinkedin
description: Operate on the user's own LinkedIn account from the terminal. Use when the user wants to list/search their connections, send or remove connection invites, follow/unfollow people or companies, view a profile (bio or recent posts), view a company page's recent posts, read their home feed, list their message inbox or read a conversation, search LinkedIn for people or posts, publish a new feed post (text and optional single image), post a public comment on a LinkedIn post, or send a direct message (to a connection by profile URL, or as a reply in an existing conversation). Also use when the user wants to queue or schedule write actions to drip out over time and avoid rate limits (bulk invites, etc.) via `--queue` and the `clinkedin queue` runner. Authenticated via the user's Chrome session — no API key. Includes destructive operations (invite, disconnect, post, comment, message) which require explicit confirmation.
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

### View a profile's or company's recent posts

```sh
clinkedin view https://www.linkedin.com/in/<slug>/ --posts                  # 10 posts, single-line each
clinkedin view https://www.linkedin.com/in/<slug>/ --posts --limit 25       # more
clinkedin view https://www.linkedin.com/in/<slug>/ --posts --full           # full untruncated text, multi-line per post
clinkedin view https://www.linkedin.com/in/<slug>/ --posts --since 7d       # only posts from last 7 days
clinkedin view https://www.linkedin.com/in/<slug>/ --posts --json           # raw post elements
clinkedin view https://www.linkedin.com/company/<slug>/ --posts             # a company page's recent posts
clinkedin view https://www.linkedin.com/company/<slug>/ --posts --since 7d --json
```

`view` accepts both member (`/in/<slug>`) and company (`/company/<slug>`) URLs. **Company URLs are `--posts`-only** — there's no bio/experience view for an organization, so `clinkedin view <company-url>` without `--posts` prints a hint and exits 2. All the same flags (`--full`, `--since`, `--limit`, `--json`) work for both. Sponsored/promoted ads that LinkedIn interleaves into the company feed are filtered out; `--json` returns the raw (decorated) feed elements.

Default text format: `date · "text (≤120 chars)" · N likes · M comments · url`. With `--full`: a header line followed by the full body, separated by `---`. Dates are relative ("1d", "2w", "3mo") since modern Voyager responses don't expose absolute timestamps at the top level.

**Cost (company posts):** two Voyager calls — one `/organization/companies` lookup to resolve the `/company/<slug>` universal name (fails loud if the company doesn't exist), then one `/feed/updates?q=companyFeedByUniversalName` read. Member posts use the `/identity/profileUpdatesV2` path instead.

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

### Read the home feed

```sh
clinkedin feed                                  # 10 posts, single-line each
clinkedin feed --limit 25 --full                # full untruncated text, multi-line per post
clinkedin feed --since 1d                       # only posts from the last day
clinkedin feed --since 1d --json --output feed.json
```

The user's own home feed in LinkedIn's "Recent" order (most recent *activity* first — a week-old post can sit at the top because a connection just liked it). Promoted posts are filtered out. `--since` takes the same forms as `view --posts --since` and filters on the post's own age.

Default text format: `date · author · "text (≤120 chars)" · N likes · M comments · url`. With `--full`: a header line, then the reason the post surfaced in parentheses (e.g. `(Jane Doe likes this)`, omitted for posts from people/companies the user follows directly), then the full body, separated by `---`.

Unlike `view --posts --json` (raw Voyager elements), `feed --json` emits **normalized rows** — a raw feed element is ~40 KB of rendering metadata per post. Fields: `date` (relative, e.g. `1h`), `author`, `author_headline`, `author_url`, `reason`, `text`, `like_count`, `comment_count`, `urn`, `url`.

**Cost:** one Voyager call (`/feed/updatesV2?q=chronFeed`) for up to ~95 posts; more only when `--limit` (or `--since`, which fetches 5× `--limit`) needs a further page, hard-capped at 5 calls per invocation.

### Read messages

```bash
clinkedin inbox                                 # 20 most recent conversations
clinkedin inbox --unread                        # only threads with unread messages
clinkedin inbox --limit 50 --json               # normalized rows for filtering/summarizing
clinkedin thread https://www.linkedin.com/messaging/thread/2-…/            # last 20 messages, oldest first
clinkedin thread 2-… --limit 60 --json
```

`inbox` text output: `time  [N unread] Name`, then `Sender: last message`, then the thread URL — pass that URL to `thread` to read it or to `message` to reply. `--json` fields: `thread_id`, `conversation_urn`, `url`, `title`, `participants` (`name`, `headline`, `url`, `urn`; excludes the user), `group`, `unread_count`, `read`, `last_activity_at`, `last_sender`, `last_sender_is_me`, `last_message`. `thread --json` fields: `sent_at`, `sender`, `sender_url`, `sender_urn`, `text`, `urn`. Attachments, GIFs and shared posts appear as `[description]`.

Reading does **not** mark messages as read. `--unread` filters client-side, so it may page further to fill `--limit`.

**Cost:** `inbox` is one Voyager messaging-GraphQL call per 20 conversations (hard cap 5); `thread` is one call per 20 messages (hard cap 5). If either starts failing with HTTP 400/500, LinkedIn has likely rotated the queryId — see `src/clinkedin/inbox.py` for how to re-capture.

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
clinkedin connect https://www.linkedin.com/in/<slug>/ --message "..." --queue        # ENQUEUE only, send nothing now
```

Free-account limits are tight: ~5 invites with notes per week, ~100–200 invites total per week. Hitting the limit silently fails — the API doesn't return a clear error.

### The send queue (`clinkedin queue ...`) — the safe way to batch invites

`--queue` on `connect` does NOT send. It appends the invite to a local persistent
job queue and exits immediately (zero Voyager calls, so it never touches the
rate-limited session). This is how you decouple ENQUEUE (cheap, batchable, safe to
loop) from SEND (rate-limited, must be slow and jittered). Enqueue a whole batch at
once; drain it one invite at a time on a throttled schedule.

Queue management subcommands:

```sh
clinkedin queue list                       # pending + dead-lettered jobs
clinkedin queue run                        # execute the OLDEST queued job and EXIT (one send per call)
clinkedin queue run --min-interval 1800 --max-interval 7200 \
    --active-hours 8-20 --daily-cap 1      # one send, only if throttle / hours / cap allow
clinkedin queue retry <id>                 # move a dead-lettered (failed) job back to pending
clinkedin queue remove <id>                # delete one job (any state)
clinkedin queue clear                      # delete ALL pending jobs
clinkedin queue install --interval 60 --min-interval 300 --max-interval 1200 \
    --active-hours 8-20 --daily-cap 5      # install a launchd agent that drains the queue on a schedule
clinkedin queue uninstall                  # remove that launchd agent
```

`queue run` flags enforce the safety model at the DRAIN, not the enqueue:
`--min-interval`/`--max-interval` jitter the gap between sends; `--active-hours
START-END` only sends in local business hours; `--daily-cap N` stops after N sends
that day (0 = uncapped). A single `queue run` sends at most one invite. Drive it
either with clinkedin's own launchd agent (`queue install`) or an external scheduler
calling `queue run` per tick. Because the noted-invite cap on a free account is ~5/week,
set `--daily-cap` LOW (e.g. 1) so a week's worth of enqueued invites bleeds out
slowly rather than burning the weekly allowance in a day.

**macOS caveat — prefer cron over the launchd agent for the drain.** On some Macs
the `StartInterval` timer in the agent that `queue install` writes never fires on its
own: launchd coalesces/defers it indefinitely, so the queue silently never drains
(symptom: `launchctl print gui/$(id -u)/com.clinkedin.queue` shows `runs = 0` and
`pended nondemand spawn = interval`, yet `launchctl kickstart` runs the job fine). If
you hit this, drive the drain from cron instead — `queue install` already prints the
equivalent line. Drop the launchd agent (`launchctl bootout gui/$(id -u)/com.clinkedin.queue`
then delete the plist) and add a per-minute crontab entry; the runner self-gates, so
waking every minute is correct:

```sh
* * * * * /Users/<you>/.local/bin/clinkedin queue run --min-interval 300 --max-interval 1200 --active-hours 8-20 --daily-cap 5 >> ~/.config/clinkedin/queue/runner.log 2>&1
```

The runner uses the cached session cookie (`~/.config/clinkedin/session.json`), so the
cron job needs no Chrome/Keychain access to send. Verify it fires by watching
`runner.log` grow on the next minute boundary.

Pattern: a producer enqueues `connect ... --queue` for a vetted list (safe, no
session calls); a slow drain (`queue run` on a tick, or the installed launchd agent)
does the actual sending under the caps. NEVER loop `connect` WITHOUT `--queue` — that
bursts Voyager and 401-locks the session. The queue is the sanctioned way to
"schedule" invites despite the don't-schedule-clinkedin rule below: that rule is about
bursting the READ/WRITE Voyager calls, and the queue's whole job is to throttle exactly
that.

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

### Send a direct message

```sh
clinkedin message https://www.linkedin.com/in/<slug>/ --message "Thanks for connecting!"   # message a person
clinkedin message https://www.linkedin.com/messaging/thread/2-…/ --message "On my way!"   # reply in a thread
clinkedin message urn:li:msg_conversation:(urn:li:fsd_profile:ACoA…,2-…) --message "…"
clinkedin message 2-…    --message-file ./reply.md       # bare thread id form
clinkedin message <target> --message "…" --dry-run        # preview (shows existing vs new conversation), send nothing
clinkedin message <target> --message "…" --yes            # skip Y/N prompt
clinkedin message <target> --message "…" --queue          # defer to the send queue
```

Accepted targets:

- **A person:** `https://www.linkedin.com/in/<slug>/`, `https://www.linkedin.com/in/ACoA…/` (as in `inbox --json` participant URLs), or `urn:li:fsd_profile:ACoA…`. Like the web app's Message button, this first looks up an existing 1:1 thread with that person and replies there; only if none exists does it start a new conversation. The preview line says which (`existing conversation 2-…` vs `new conversation`).
- **A conversation:** messaging URL `https://www.linkedin.com/messaging/thread/2-…/`, full URN `urn:li:msg_conversation:(…)`, or bare `2-…` thread id.

New conversations only work for **1st-degree connections** — messaging anyone else needs InMail, which isn't supported; LinkedIn rejects the send and the command exits 1 with the HTTP error. To find a thread to reply to, use `clinkedin inbox`.

Messages are **private, persistent, and notify the recipient** (push + email). Always show the user the exact text and confirm before sending — prefer the interactive prompt over `--yes` unless the user explicitly authorized that exact text. Run `--dry-run` first when messaging a person so the user can see whether it continues an old thread. Plain text only; no attachments, mentions, or rich content in v1.

**Cost:** one POST to `/voyager/api/voyagerMessagingDashMessengerMessages?action=createMessage` (plus one `/me` lookup the first time per session, cached after). Person targets add a profile lookup (skipped for ACoA forms) and one existing-thread GraphQL lookup. Reply payload captured live 2026-05-11; new-conversation payload (`hostRecipientUrns`) captured 2026-10-12.

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

- For human-readable summaries: omit `--json`. Use `--full` on `view --posts` or `feed` if the user wants to read post bodies.
- For programmatic use (filtering, exporting, feeding into other tools): always pass `--json`. Pair with `--output <path>` to keep large results out of stdout.
- Exit codes: `0` success, `1` runtime/auth failure, `2` argument error.

## Confirmation policy

| Operation | Default behavior |
|---|---|
| `connections`, `following`, `view`, `feed`, `search` | Run without confirmation — read-only. |
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
- **Debug flags** — `view --posts --debug` dumps the raw `/identity/profileUpdatesV2` response (member profiles only; ignored for company URLs); `following --debug` dumps the raw GraphQL Curation Hub response. Use only when investigating an unexpected error; each invocation is one more Voyager call against a fragile session.

## Help

```sh
clinkedin --help
clinkedin <command> --help
```
