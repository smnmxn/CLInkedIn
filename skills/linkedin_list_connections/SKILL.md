---
name: linkedin_list_connections
description: List the user's 1st-degree LinkedIn connections, optionally exporting to JSON. Use when the user says things like "list my LinkedIn connections", "show me my LinkedIn network", "export my contacts", "who am I connected with on LinkedIn", or asks to search/filter people they're already connected to. Read-only — this skill does NOT send invites or messages. Requires a one-time sign-in to linkedin.com in Chrome.
---

# linkedin_list_connections

Fetch the user's 1st-degree LinkedIn connections via the `clinkedin connections` CLI. There is no MCP tool for this yet; the skill invokes the shell command directly.

## Setup (once per machine)

```sh
uv tool install --editable '/path/to/clinkedin'
```

Sign in to linkedin.com in Chrome, or paste a cookie:

```sh
clinkedin login --cookie AQEDAR...   # from DevTools → Cookies → linkedin.com → li_at
```

## Usage

```sh
clinkedin connections                               # human-readable: Name · Headline · Location
clinkedin connections --limit 20                    # first 20 only
clinkedin connections --json                        # JSON to stdout
clinkedin connections --json --output conns.json    # write JSON to file
```

JSON fields: `name`, `jobtitle`, `location` (all strings; may be empty).

Exit codes: `0` success, `1` runtime failure (auth / API). Failures with session errors print a hint to re-run `clinkedin login`.

## How to use the result

- For "show me my connections": call without `--json`; print the first ~20 lines and offer to list more.
- For "search/filter my network": call with `--json`, parse stdout, filter in your own code. Don't call twice for the same session — the list is stable within a day.
- For "export my network": call with `--json --output <path>` and confirm the file path with the user.

## Rate limits — lighter than invites, still fragile

This call shares LinkedIn's Voyager API with the invite path. Bursts (repeated fetches within seconds) can 401 the whole session for 30 min to several hours.

- **Fetch once, cache** — if you need to iterate over the list, call once and keep the result in memory or on disk.
- On HTTP 401 or session errors, **stop** and ask the user to re-auth. Don't retry automatically.
- Don't run on a schedule.

## Limits of the data

- 1st-degree connections only (no 2nd- or 3rd-degree).
- LinkedIn paginates server-side; `--limit` caps the fetch. Without `--limit`, the CLI pulls what the API returns (typically several hundred, hard cap around 1000).
- Fields are LinkedIn-provided; some profiles have empty `jobtitle` or `location`.

## Related

To send a connection request (not listed here), see the sibling skill `linkedin_send_connection_request`.
