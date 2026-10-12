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

clinkedin comment https://www.linkedin.com/feed/update/urn:li:activity:N/ --message "Great point"
clinkedin comment <url> --message-file ./reply.txt             # for longer text
clinkedin comment <url> --message "..." --dry-run              # preview, send nothing

clinkedin message https://www.linkedin.com/in/<slug>/ --message "Thanks for connecting!"   # DM a connection (reuses existing thread)
clinkedin message https://www.linkedin.com/messaging/thread/2-.../ --message "On my way!"
clinkedin message 2-...   --message-file ./reply.md            # reply into existing thread by bare id

clinkedin post --message "Shipping v2 today"                   # publish a text-only feed post (PUBLIC)
clinkedin post --message "Launch day" --image ~/Pictures/cover.png   # single image (PNG/JPG, <=10MB)
clinkedin post --message-file ./announcement.md --dry-run      # preview only, publish nothing

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
clinkedin view https://www.linkedin.com/company/<slug>/ --posts                  # a company's recent posts (--posts only)
clinkedin view https://www.linkedin.com/company/<slug>/ --posts --since 7d --json

clinkedin feed                                                 # your home feed: Date · Author · Text · Likes · Comments · URL
clinkedin feed --limit 25 --full                               # full untruncated text
clinkedin feed --since 1d --json --output feed.json            # last day's posts as JSON

clinkedin inbox                                                # recent conversations: time · who · last message · thread URL
clinkedin inbox --unread --json                                # only threads with unread messages, as JSON
clinkedin thread https://www.linkedin.com/messaging/thread/2-.../ --limit 50   # read a conversation, oldest first

clinkedin search people "product manager fintech"              # Name · Headline · Location · URL
clinkedin search people "designer" --network F,S --limit 50    # 1st + 2nd-degree only
clinkedin search people "" --network F --industry "Financial Services"  # 1st-degree in an industry
clinkedin search people "founder" --limit 25 --offset 25       # next page (items 25-49)
clinkedin search people "founder" --json --output founders.json

clinkedin search posts "AI agents"                             # Text · URL
clinkedin search posts "RAG" --limit 5
clinkedin search posts "vector db" --json --output posts.json

clinkedin connect <url> --message "Hi" --queue                 # defer instead of sending now (any write command)
clinkedin queue list                                           # pending + dead-lettered jobs
clinkedin queue run                                            # execute the oldest queued job and exit
clinkedin queue install                                        # launchd agent: drains the queue on a jittered schedule
```

Any write command (`connect`, `disconnect`, `comment`, `post`, `message`,
`follow`, `unfollow`) takes `--queue` to defer it. See
[Queue](#queue-rate-limit-friendly-bulk-writes) below for the scheduler and the
anti-rate-limit strategy.

`--network` accepts a comma-separated subset of `F` (1st), `S` (2nd), `O` (3rd+); omit to search all. `--limit` defaults to 25 for people. Each result includes a `url` field (URN-form, e.g. `https://www.linkedin.com/in/ACoAA…/`) — LinkedIn redirects this to the canonical profile in the browser.

`--industry` filters by industry, given as a LinkedIn industry name (e.g. `"Financial Services"`, resolved offline) or a raw numeric code; repeat the flag to match any of several. LinkedIn applies the filter server-side, so result rows don't carry the industry value (it filters but isn't displayed). Industry filtering needs network depth `F` to behave like "my connections in X" — and note that `search` reads LinkedIn's search index, which lags the relationship store, so a freshly removed connection can briefly still appear; use `clinkedin connections` for the authoritative, up-to-date list.

`inbox` lists your message conversations, most recent activity first (`--limit` defaults to 20, up to 5 pages of 20). `--unread` keeps only threads with unread messages. `thread` takes the same targets as `message` and prints the most recent `--limit` messages oldest-first, paging back as needed; attachments and shared posts show as a bracketed description. Both accept `--json` for normalized rows.

`feed` reads your home feed in LinkedIn's "Recent" order (most recent activity first) with promoted posts removed; `--limit` defaults to 10. `--json` emits one normalized row per post (`date`, `author`, `author_headline`, `author_url`, `reason`, `text`, `like_count`, `comment_count`, `urn`, `url`), where `reason` is why the post surfaced (e.g. "Jane Doe likes this") and is empty for posts from people and companies you follow directly.

Post search is implemented by **scraping** LinkedIn's web search-results page (Voyager doesn't expose a usable content-search endpoint). It returns `text` / `url` / `urn` per result, defaults to a `--limit` of 10, and only fetches the first page (~3–10 posts). Author, post date, and engagement counts are not extracted in v1. Comment search is **not** supported — LinkedIn doesn't expose it.

Because post search depends on the rendered web UI, it can break when LinkedIn ships UI changes. The CLI fails loud (with a saved-HTML dump path) rather than silently returning zero results — see `skills/linkedin_search_posts/SKILL.md` for failure modes.

The first run asks macOS Keychain for access to Chrome's cookie store — click **Always Allow**.

A session cookie is also cached at `~/.config/clinkedin/session.json` (mode `0600`) as a fallback for when Chrome is signed out. For users upgrading from the old `linkedin-cli` name, the old `~/.config/linkedin-cli/session.json` is still read as a fallback.

### Not using Chrome?

```sh
clinkedin login --cookie AQEDAR...    # paste li_at from your browser's DevTools
```

DevTools → Application → Cookies → `https://www.linkedin.com` → copy the `li_at` value.

## Queue (rate-limit-friendly bulk writes)

Firing many write actions back-to-back (especially `connect`) trips LinkedIn's
rate limits. Instead, **queue** them and let a scheduler drain the queue one job
at a time. Every write command — `connect`, `disconnect`, `comment`, `post`,
`message`, `follow`, `unfollow` — takes `--queue`:

```sh
clinkedin connect https://www.linkedin.com/in/<slug>/ --message "Hi" --queue
clinkedin follow https://www.linkedin.com/company/<slug>/ --queue
```

`--queue` validates inputs and records the job to disk under
`~/.config/clinkedin/queue/` **without touching LinkedIn**. Inspect and drain it:

```sh
clinkedin queue list                  # pending + dead-lettered jobs
clinkedin queue run                   # execute the single oldest job, then exit
clinkedin queue remove <id>           # drop a job
clinkedin queue clear                 # drop all pending jobs
clinkedin queue retry <id>            # move a failed job back to pending
```

`queue run` pops the **oldest** job (FIFO) and runs it non-interactively. A job
that fails is retried on the next few runs and dead-lettered after 3 attempts
(`queue list` shows the last error).

Schedule it so a batch drips out over time:

```sh
clinkedin queue install   # launchd agent with anti-pattern defaults (see below)
# then load it (printed for you):
launchctl load -w ~/Library/LaunchAgents/com.clinkedin.queue.plist
clinkedin queue uninstall # remove the agent
```

**Sends are jittered, not metronomic.** A fixed `*/5` tick firing 24/7 is an
obvious automation signature. So the agent wakes often (every 60s) but the runner
*gates* each send: a **randomized gap** between sends, an **active-hours** window,
and an optional **daily cap**. `queue install` bakes in sensible defaults —
5–20 min between sends, only 08:00–20:00 local, no daily cap — all overridable:

```sh
clinkedin queue install --min-interval 600 --max-interval 1800 \
    --active-hours 9-18 --daily-cap 20      # slower, office hours, ≤20/day
clinkedin queue install --active-hours none # run around the clock
```

The same gates are flags on `queue run`, so the cron equivalent (non-macOS) is
the line printed by `queue install`:

```cron
* * * * * /abs/path/to/clinkedin queue run --min-interval 300 --max-interval 1200 --active-hours 8-20
```

**Caveats.** Jitter is cheap hardening against *naive timing-pattern* detection,
not a cloak — the bigger risks are using the unofficial Voyager API at all and
sheer volume. Even with a daily cap, ~288 wake-ups/day could in principle send far
more than LinkedIn's ~100–200 invites/week, so keep volume human-plausible: set
`--daily-cap`, and enqueue at most a week's worth of invites at a time.

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
result is `{"name", "kind", "public_id", "headline", "urn"}`.
`linkedin_list_conversations` takes `limit` / `unread_only` and
`linkedin_read_conversation` takes `target` (thread URL, URN, or id) / `limit`;
both return `{"ok", "results", "error"}` with the same rows as `inbox --json` /
`thread --json`. `linkedin_send_message` takes `target` (profile URL/URN or
thread URL/URN/id) and `message`, and returns `{"ok", "thread_id",
"new_conversation", "error"}`. Auth is reused
from the same Chrome/`session.json` flow as the CLI.

## Agent use (OpenClaw / Claude skills)

A single portable skill at [`skills/clinkedin/`](skills/clinkedin/SKILL.md)
covers the whole CLI — list/search connections, send/remove invites,
follow/unfollow, view profiles and recent posts, read the home feed and messages, and search
people/posts.

Point your agent framework at this `skills/` directory (or symlink
`skills/clinkedin/` into its skills root) and the host will trigger it
automatically on matching LinkedIn requests.

## Warning

This tool uses LinkedIn's unofficial internal **Voyager** API (via the
[`linkedin-api`](https://pypi.org/project/linkedin-api/) library). That violates
LinkedIn's Terms of Service. Use it only for personal, low-volume access to your
own data. Do not run it on a schedule.

Invites-with-notes on a free account are limited to ~5/week; total invites to ~100–200/week.
