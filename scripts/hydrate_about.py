#!/usr/bin/env python3
"""Hydrate a clinkedin connections/search JSON with each profile's About + industry.

For every row (identified by urn_id) it fetches the full profile and records the
About (summary) text and the industryUrn code. Output is JSONL appended one line
at a time, so the run is resumable: re-running skips urn_ids already written.

Usage:
    python scripts/hydrate_about.py <input.json> [output.jsonl]
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

from clinkedin.client import make_client
from clinkedin.industries import INDUSTRIES
from clinkedin.profile import fetch_profile

BY_CODE = {code: name for name, code in INDUSTRIES.items()}
_IND_RE = re.compile(r"fsd_industry:(\d+)")
EXTRA_SLEEP = 1.0  # small buffer on top of linkedin_api's built-in evade() throttle


def main(argv: list[str]) -> int:
    src = Path(argv[1]) if len(argv) > 1 else Path("/tmp/charity_keyword_connections.json")
    out = Path(argv[2]) if len(argv) > 2 else Path("/tmp/charity_hydrated.jsonl")

    rows = json.loads(src.read_text())
    done: set[str] = set()
    if out.exists():
        for line in out.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                done.add(json.loads(line)["urn_id"])
            except (ValueError, KeyError):
                pass

    pending = [r for r in rows if r.get("urn_id") and r["urn_id"] not in done]
    print(f"{len(rows)} total, {len(done)} already hydrated, {len(pending)} to do", flush=True)

    client = make_client()
    with out.open("a") as fh:
        for i, r in enumerate(pending, 1):
            pid = r["urn_id"]
            rec = {
                "urn_id": pid,
                "name": r.get("name"),
                "url": r.get("url"),
                "jobtitle": r.get("jobtitle"),
                "matched_keywords": sorted(set(r.get("matched_keywords", []))),
            }
            try:
                p = fetch_profile(client, pid)
                raw = p.get("_dash_raw") or {}
                m = _IND_RE.search(raw.get("industryUrn") or "")
                code = m.group(1) if m else None
                rec["headline"] = p.get("headline") or ""
                rec["about"] = p.get("summary") or ""
                rec["industry_code"] = code
                rec["industry_name"] = BY_CODE.get(code) if code else None
                rec["industry_set"] = code is not None
            except Exception as e:  # private/deleted/rate-limited — record and continue
                rec["error"] = str(e)
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            if i % 20 == 0 or i == len(pending):
                print(f"  {i}/{len(pending)}", flush=True)
            time.sleep(EXTRA_SLEEP)

    print("DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
