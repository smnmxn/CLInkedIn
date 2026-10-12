# Queue-driven cron-paced batch LinkedIn invites

When Simon needs to invite 30–80 people from a curated list (typically pre-conference outreach: SaaStr ⭐ founder list, Web Summit speaker shortlist) the right pattern is **not** to blast via clinkedin in a loop, and not to manually click each one either. The proven middle path is queue-driven cron-paced sends — one invite every 15 minutes via a single-target script firing on cron.

This is the **only sanctioned batch pattern**. It's still personal-volume (under LinkedIn's ~100/week soft cap) and human-paced (15 min ≈ a person glancing at LinkedIn between meetings), so it doesn't bot-pattern. Don't deviate.

## When this applies

- Pre-conference outreach: 30–80 verified ⭐ founders / 💰 investors from a Luma scrape or speaker list
- Re-engagement campaigns: tier-A contacts you owe a follow-up
- Migration: moving Simon's network from a stale list to LinkedIn

**Does NOT apply to:**
- Cold lead-gen (≠ personal-volume)
- More than ~80 in a single campaign (split into weeks)
- Anyone not pre-qualified by Simon (the human-in-the-loop is the qualification step, not the script)

## File layout

In the initiative folder (e.g. `~/RoboCorrin/projects/surfacelayer/saastr-launch/`):

```
linkedin-queue.json          # FIFO array of {name, first_name, url, is_speaker}
linkedin-failures.json       # {url: retry_count} — auto-skips after MAX_RETRIES
LINKEDIN-OUTREACH.md         # tracker table with ❌/✅/⏭️ status per row
send-one.py                  # the script
pop-queue.py                 # manual pop if you need to skip one
```

## The script (canonical version)

```python
#!/usr/bin/env python3
"""Send one LinkedIn connection request from a queue. Reads queue, sends via clinkedin, pops queue, updates tracker.
Exit 0 = sent, Exit 1 = error, Exit 2 = queue empty."""
import json, subprocess, sys

QUEUE = "/Users/simon/RoboCorrin/projects/<COMPANY>/<INITIATIVE>/linkedin-queue.json"
TRACKER = "/Users/simon/RoboCorrin/projects/<COMPANY>/<INITIATIVE>/LINKEDIN-OUTREACH.md"
FAILURES = "/Users/simon/RoboCorrin/projects/<COMPANY>/<INITIATIVE>/linkedin-failures.json"
CLI = "/Users/simon/.local/bin/clinkedin"
MAX_RETRIES = 3

with open(QUEUE) as f:
    queue = json.load(f)
if not queue:
    print("Queue empty — all done!")
    sys.exit(2)

try:
    with open(FAILURES) as f:
        fail_counts = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    fail_counts = {}

entry = queue[0]
name, first_name, url = entry["name"], entry["first_name"], entry["url"]
is_speaker = entry.get("is_speaker", False)

# Build a message that fits the moment (today/tomorrow/this week/next week).
# Update the verb tense to match when the campaign actually fires.
if is_speaker:
    msg = f"Hey {first_name}, I've just landed from the UK for <CONF>. Looking forward to your session — thought I'd connect ahead of it. Good to know a few faces. :)"
else:
    msg = f"Hey {first_name}, I've just landed from the UK for <CONF>. Saw you're attending too — thought I'd connect ahead of it. Good to know a few faces. :)"

print(f"Sending to {name}: {url}")
result = subprocess.run(
    [CLI, "connect", url, "--message", msg, "--yes"],
    capture_output=True, text=True, timeout=60
)
output = result.stdout + result.stderr
print(output)

failed = (result.returncode != 0
          or "rate" in output.lower()
          or "401" in output or "403" in output
          or ("error" in output.lower() and "already" not in output.lower()))

if failed:
    fail_counts[url] = fail_counts.get(url, 0) + 1
    with open(FAILURES, "w") as f:
        json.dump(fail_counts, f, indent=2)
    if fail_counts[url] >= MAX_RETRIES:
        print(f"SKIPPING {name} after {MAX_RETRIES} failures — removing from queue")
        queue.pop(0)
        with open(QUEUE, "w") as f:
            json.dump(queue, f, indent=2)
        # Mark ❌ → ⏭️ in tracker
        linkedin_slug = url.rstrip("/").split("/")[-1]
        with open(TRACKER) as f:
            content = f.read()
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if linkedin_slug in line and "❌" in line:
                lines[i] = line.replace("❌", "⏭️", 1)
                break
        with open(TRACKER, "w") as f:
            f.write("\n".join(lines))
        sys.exit(1)
    print(f"FAILED: {name} (attempt {fail_counts[url]}/{MAX_RETRIES})")
    sys.exit(1)

# Success: pop queue + flip ❌ → ✅ in tracker
queue.pop(0)
with open(QUEUE, "w") as f:
    json.dump(queue, f, indent=2)

linkedin_slug = url.rstrip("/").split("/")[-1]
with open(TRACKER) as f:
    content = f.read()
lines = content.split("\n")
for i, line in enumerate(lines):
    if linkedin_slug in line and "❌" in line:
        lines[i] = line.replace("❌", "✅", 1)
        break
with open(TRACKER, "w") as f:
    f.write("\n".join(lines))

print(f"✅ {name}. {len(queue)} remaining.")
```

## Wiring it into cron

Hermes cron requires scripts to live under `~/.hermes/scripts/`. Create a shim:

```bash
cat > ~/.hermes/scripts/<campaign>-linkedin-invite.sh <<'EOF'
#!/bin/bash
exec python3 /Users/simon/RoboCorrin/projects/<COMPANY>/<INITIATIVE>/send-one.py
EOF
chmod +x ~/.hermes/scripts/<campaign>-linkedin-invite.sh
```

Then schedule:

```python
# via the mcp__hermes__Cronjob tool
action: "create"
name: "<campaign>-linkedin-invites"
schedule: "*/15 * * * *"
script: "<campaign>-linkedin-invite.sh"
no_agent: true   # critical — this is a script, not an agent run
deliver: "local" # don't ping Simon on success
```

Empty queue → script exits 2 → cron silently no-ops. No need to disable the job; just let it idle.

## Building the queue (input format)

From a Luma scrape or other source, transform to:

```json
[
  {"name": "Devang Jadav", "first_name": "Devang", "url": "https://www.linkedin.com/in/devangjadav", "is_speaker": false},
  ...
]
```

**Dedupe by LinkedIn URL** (people show up at multiple events with the same handle).

Sort by priority — founders first, investors next, "both events" tag first within each. The script pops index 0, so order = send order.

## Pace tuning

| Window | Rate | Use case |
|--------|------|----------|
| `*/11 * * * *` (every 11 min, 24/7) | ~131/day | Pre-conf overnight burst (proven SaaStr 2026) |
| `*/15 * * * *` (every 15 min, 24/7) | ~96/day | Overnight burst before tomorrow's event — invites land for morning inbox check |
| `*/30 * * * *` (every 30 min, 24/7) | ~48/day | Conservative steady-state |
| `*/15 8-22 * * *` (every 15 min, 8am–10pm PT) | ~56/day | Daytime-only, looks more human |
| ❌ `*/5 * * * *` (every 5 min) | ~12/hr, ~288/day | YELLOW — well over LinkedIn's ~20/day soft cap |
| ❌ `*/3 * * * *` (every 3 min) | ~20/hr, ~480/day | RED — pre-restriction territory. Do not run. |

In 2026-05 Simon ran 64 invites at `*/15 * * * *` overnight before SaaStr Mon mixer — all delivered before the 6pm event. Zero rate-limit hits. A second SaaStr batch ran at `*/11 * * * *` (~5.5/hr) successfully.

### Pushback on faster cadences (do this — Simon will sometimes ask)

When Simon asks to drop the cadence below ~10 min — e.g. "can we make it every 3 minutes" — push back with **specific numbers**, not vibes. He's not stupid; he wants to be talked out of it if there's a real reason.

The numbers to quote:

- **Hard caps (free account):** ~5 invites-with-notes per week, ~100–200 total invites per week, ~20/day soft ceiling for sustained sending.
- **Velocity threshold:** anything above ~10/hour from a single account starts pattern-matching as bot traffic. 11-min cadence ≈ 5.5/hr (safe). 5-min ≈ 12/hr (yellow). 3-min ≈ 20/hr (red — that's pre-restriction territory).
- **Consequence:** restrictions usually fire *right before* the event because that's when the burst pattern is most obvious. Worst possible timing for a conference push.

Counter-offers to have ready:
1. Keep current pace — give him the ETA at current cadence so he can decide whether the rush is real.
2. Step down by ~30% (e.g. 11→7 min) — meaningful speedup, still defensible.
3. Fast burst capped at ~15 sends, then resume normal — gets the urgency, limits exposure.

End the pushback with "what's the rush?" — sometimes the underlying problem (e.g. conference start time) is solvable a different way that doesn't need a faster pace. He responded to this with "leave it then" in the SaaStr 2026 session, which is the expected outcome — the pushback is the value, not the speed-up.

## Updating the tracker BEFORE the script runs

Before scheduling cron, append the queue targets to `LINKEDIN-OUTREACH.md` as a table with `❌` in the Sent column. The script flips `❌ → ✅` (sent) or `❌ → ⏭️` (3× failed). Without this prep step the script silently no-ops on the tracker update (still sends successfully though).

```markdown
## <Campaign Name> Targets (Batch N — <date>)

Brief context for the batch.

| Name | Cat | Events | Bio | LinkedIn | Sent? |
|------|-----|--------|-----|----------|-------|
| Devang Jadav | ⭐ | both | Founder, AI legaltech / home services | [LinkedIn](https://linkedin.com/in/devangjadav) | ❌ |
| ... |
```

## Pitfalls

- **Message tense matches the campaign timing.** "I'm flying over next week" reads wrong if cron fires the night before the event. The session that built the SaaStr 2026 batch had to patch the script mid-deploy because the canonical message said "next week" when the event was the next morning. Pick the verb tense for *when the invite will actually be received*, not when you're writing it.
- **Don't manually intervene mid-campaign without pausing cron.** If you want to skip one target, pause the cron job first, run `pop-queue.py`, resume. Editing `linkedin-queue.json` while the script could fire is a race condition.
- **Don't ramp up pace if it seems to be working.** 15-min cadence is fine; 5-min cadence pattern-matches as bot traffic to LinkedIn. The whole point of cron is to look human.
- **Don't reuse this pattern for second-degree lead-gen.** Personal-volume only. If Simon ever asks you to scrape a 500-person speaker list and queue them all, push back — that's not what this is.

## Related

- The class-level prohibition in the main `linkedin_send_connection_request` skill (no bulk / no scheduled) is *strict in the agent-loop sense* — an agent should never fire multiple invites in a single session. This cron pattern is the exception because it's **one script invocation per cron tick**, each invocation sends one invite, the human-in-the-loop is the queue-building step.
- See `conference-networking` skill + its `references/luma-guest-extraction.md` for how to build the input queue from a Luma scrape.
- See `simonizer` skill for refining the message wording.
