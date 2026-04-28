---
name: linkedin_search_people
description: Search LinkedIn for people matching keywords, optionally filtered by network depth (1st / 2nd / 3rd) and exporting to JSON. Use when the user says things like "find people on LinkedIn who…", "search LinkedIn for product managers at Stripe", "look up designers in London", or any open-ended people-finding query that goes beyond their existing 1st-degree connections. Read-only — this skill does NOT send invites or messages. Requires a one-time sign-in to linkedin.com in Chrome.
---

# linkedin_search_people

Search LinkedIn's people index via the `clinkedin search people` CLI. There is no MCP tool for this yet; the skill invokes the shell command directly.

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
clinkedin search people "product manager fintech"                    # human-readable
clinkedin search people "designer" --network F,S                     # 1st + 2nd-degree only
clinkedin search people "founder" --limit 10                         # cap results
clinkedin search people "recruiter" --json                           # JSON to stdout
clinkedin search people "vc partner" --json --output partners.json   # write JSON to file
```

`--network` takes a comma-separated subset of `F` (1st-degree), `S` (2nd-degree), `O` (3rd-degree and beyond). Omit it to search across all degrees.

`--limit` defaults to **25**. Don't raise it without reason — see Rate limits below.

JSON fields: `name`, `jobtitle`, `location`, `url`, `urn_id`, `distance`.

`url` is the URN-form profile URL (`https://www.linkedin.com/in/<urn_id>/`), which LinkedIn redirects to the canonical profile. It's fine for the user to click; passing it into `clinkedin connect` is **not** guaranteed to work today — the invite path expects a vanity slug. If the user wants to invite someone from a search result, open the URL, copy the canonical `/in/<slug>/` URL from the address bar, and pass that to the invite skill.

Exit codes: `0` success, `1` runtime failure (auth / API), `2` bad arguments (e.g. unknown `--network` value). Failures with session errors print a hint to re-run `clinkedin login`.

## How to use the result

- For "find me X people who…": call without `--json`; print results and offer to refine the query.
- For "give me a CSV / spreadsheet of X": call with `--json`, parse stdout, then transform — don't repeatedly query for sub-filters you can apply locally.
- For combining with invites: parse the JSON, pick a profile, then use the sibling `linkedin_send_connection_request` skill. **Don't loop blindly** — always confirm with the user before sending more than one invite.

## Rate limits — search is the most fragile call

This call shares LinkedIn's Voyager API with the invite path, and search burns more quota per call than listing connections. Bursts (multiple searches within seconds, or a search followed immediately by an invite) routinely 401 the whole session for 30 min to several hours.

- **One search per task.** Refine the query rather than re-running variants.
- On HTTP 401 or session errors, **stop** and ask the user to re-auth. Don't retry automatically.
- Don't run on a schedule.
- If you need many results, raise `--limit` once rather than paginating across multiple calls.

## Limits of the data

- LinkedIn's search ranking is opaque — the order is not stable across calls and is not pure relevance.
- 3rd-degree (`O`) results have less data on free accounts (e.g. `name` may be a partial match).
- `regions`, `industries`, etc. are deliberately not exposed by this CLI in v1; they take internal URN strings rather than human-readable names. If the user needs them, fall back to keyword filtering inside the query string (e.g. `"product manager Stripe London"`).

## Related

- `linkedin_list_connections` — list the user's existing 1st-degree connections (no search).
- `linkedin_send_connection_request` — send a connection request to one profile found via this skill.
