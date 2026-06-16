#!/usr/bin/env python3
"""Bulk-disconnect from every connection listed in a clinkedin JSON export.

Reads a JSON file produced by e.g.
    clinkedin search people "" --network F --industry "<X>" --json --output conns.json
(a list of objects with a `url` and/or `urn_id` field) and removes each one.

SAFETY: this is irreversible — removed connections must re-accept a fresh
invite to come back. So the script is DRY-RUN by default; it only disconnects
when you pass --execute, and even then it prompts once for confirmation unless
you also pass --yes. Progress is recorded to a sidecar <input>.done file so a
re-run resumes where it left off rather than re-processing the whole list.

Usage:
    # preview (no changes):
    python scripts/bulk_disconnect.py /tmp/nonprofit_connections.json
    # actually disconnect, 5s between each:
    python scripts/bulk_disconnect.py /tmp/nonprofit_connections.json --execute
    # non-interactive (e.g. background run):
    python scripts/bulk_disconnect.py /tmp/nonprofit_connections.json --execute --yes
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from clinkedin.client import make_client
from clinkedin.disconnect import DisconnectError, remove_connection
from clinkedin.invite import parse_profile_url

SLEEP_SECONDS = 5


def _entry_id(entry: dict) -> str | None:
    """Pull the ACoA profile id from an export row (prefer urn_id, fall back to url)."""
    if entry.get("urn_id"):
        return entry["urn_id"]
    url = entry.get("url")
    if url:
        try:
            return parse_profile_url(url)
        except ValueError:
            return None
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Bulk-disconnect from a clinkedin JSON export.")
    ap.add_argument("json_file", help="Path to the JSON list of connections")
    ap.add_argument(
        "--execute",
        action="store_true",
        help="Actually disconnect. Without this, the script only previews (dry run).",
    )
    ap.add_argument(
        "--yes",
        action="store_true",
        help="Skip the confirmation prompt (for non-interactive runs).",
    )
    ap.add_argument(
        "--sleep",
        type=float,
        default=SLEEP_SECONDS,
        help=f"Seconds to sleep between disconnects (default {SLEEP_SECONDS}).",
    )
    args = ap.parse_args(argv)

    src = Path(args.json_file)
    if not src.is_file():
        print(f"File not found: {src}", file=sys.stderr)
        return 2
    try:
        entries = json.loads(src.read_text())
    except (ValueError, OSError) as e:
        print(f"Could not read {src}: {e}", file=sys.stderr)
        return 2
    if not isinstance(entries, list):
        print(f"Expected a JSON list in {src}, got {type(entries).__name__}.", file=sys.stderr)
        return 2

    # Build a de-duplicated work list of (id, name), skipping rows we can't identify.
    seen: set[str] = set()
    work: list[tuple[str, str]] = []
    skipped_unidentifiable = 0
    for entry in entries:
        pid = _entry_id(entry) if isinstance(entry, dict) else None
        if not pid:
            skipped_unidentifiable += 1
            continue
        if pid in seen:
            continue
        seen.add(pid)
        work.append((pid, (entry.get("name") or "(unknown)")))

    # Resume support: skip anyone already recorded as done.
    done_file = src.with_suffix(src.suffix + ".done")
    already_done: set[str] = set()
    if done_file.exists():
        already_done = {ln.strip() for ln in done_file.read_text().splitlines() if ln.strip()}
    pending = [(pid, name) for pid, name in work if pid not in already_done]

    print(f"Loaded {len(entries)} rows from {src}")
    if skipped_unidentifiable:
        print(f"  {skipped_unidentifiable} row(s) had no usable id — skipped")
    if already_done:
        print(f"  {len(already_done)} already disconnected on a prior run — skipping")
    print(f"  {len(pending)} connection(s) to disconnect")

    if not pending:
        print("Nothing to do.")
        return 0

    if not args.execute:
        print("\n--- DRY RUN (no changes). Re-run with --execute to disconnect. ---")
        for pid, name in pending:
            print(f"  would disconnect: {name}  [{pid}]")
        est = len(pending) * args.sleep
        print(f"\nEstimated wall-clock when executed: ~{est/60:.1f} min (plus API time).")
        return 0

    # Irreversible from here on. Confirm unless --yes.
    if not args.yes:
        print(
            f"\nAbout to PERMANENTLY disconnect from {len(pending)} connection(s). "
            "This cannot be undone."
        )
        reply = input("Type 'yes' to proceed: ").strip().lower()
        if reply != "yes":
            print("Aborted.")
            return 1

    client = make_client()
    ok = 0
    failures: list[tuple[str, str, str]] = []
    total = len(pending)
    # Open the done-log in append mode so each success is durably recorded immediately.
    with done_file.open("a") as done_log:
        for i, (pid, name) in enumerate(pending, 1):
            print(f"[{i}/{total}] disconnecting {name} [{pid}] ... ", end="", flush=True)
            try:
                remove_connection(client, pid)
            except (DisconnectError, Exception) as e:  # keep going on any single failure
                print(f"FAILED: {e}")
                failures.append((pid, name, str(e)))
            else:
                print("ok")
                done_log.write(pid + "\n")
                done_log.flush()
                ok += 1
            if i < total:
                time.sleep(args.sleep)

    print(f"\nDone. {ok} disconnected, {len(failures)} failed.")
    if failures:
        print("Failures (re-run the script to retry these — successes are skipped):")
        for pid, name, err in failures:
            print(f"  {name} [{pid}]: {err}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
