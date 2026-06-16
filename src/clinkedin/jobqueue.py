"""On-disk job queue for deferred clinkedin write actions.

Write commands (connect, comment, …) can be *queued* instead of executed with
``--queue``. A one-shot runner (``clinkedin queue run``, driven by launchd/cron)
pops the oldest job and executes it, so a bulk batch drips out over time instead
of bursting into LinkedIn's rate limits.

Layout under ``~/.config/clinkedin/queue/`` (maildir-style, lock-free):

    pending/     jobs waiting, one JSON file each (lexical name sort == FIFO)
    processing/  a job is *claimed* by an atomic os.rename out of pending/
    done/        succeeded
    failed/      dead-lettered after MAX_ATTEMPTS
    payload/     copied image bytes for `post --image` jobs
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Computed at call time (not cached) so tests can monkeypatch QUEUE_ROOT.
QUEUE_ROOT = Path.home() / ".config" / "clinkedin" / "queue"

MAX_ATTEMPTS = 3
EMPTY = 100  # run_once() return code when there was nothing to do

# Commands that may be queued. The runner rebuilds an argparse Namespace from the
# stored args plus these defaults and calls the matching handler non-interactively.
QUEUEABLE = ("connect", "disconnect", "comment", "post", "message", "follow", "unfollow")
_RUN_DEFAULTS = {"dry_run": False, "yes": True, "queue": False, "message_file": None}

_STATES = ("pending", "processing", "done", "failed")

PLIST_LABEL = "com.clinkedin.queue"


# --- paths -----------------------------------------------------------------

def _dir(name: str) -> Path:
    return QUEUE_ROOT / name


def _payload_dir() -> Path:
    return QUEUE_ROOT / "payload"


def _run_state_file() -> Path:
    return QUEUE_ROOT / "run_state.json"


def runner_log() -> Path:
    return QUEUE_ROOT / "runner.log"


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{PLIST_LABEL}.plist"


def ensure_dirs() -> None:
    for name in _STATES:
        _dir(name).mkdir(parents=True, exist_ok=True)


# --- json helpers ----------------------------------------------------------

def _atomic_write(path: Path, obj: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2))
    os.replace(tmp, path)


def _read(path: Path) -> dict:
    return json.loads(path.read_text())


def _load_run_state() -> dict:
    try:
        return json.loads(_run_state_file().read_text())
    except (OSError, ValueError):
        return {}


def _save_run_state(state: dict) -> None:
    _atomic_write(_run_state_file(), state)


# --- enqueue ---------------------------------------------------------------

def enqueue(command: str, args: dict) -> str:
    """Write a pending job for `command` with resolved `args`. Returns its id."""
    if command not in QUEUEABLE:
        raise ValueError(f"command {command!r} is not queueable")
    ensure_dirs()
    now = datetime.now(timezone.utc)
    # Microsecond stamp keeps filenames lexically sortable == FIFO; the random
    # suffix avoids collisions when two jobs land in the same microsecond.
    job_id = f"{now.strftime('%Y%m%dT%H%M%S%f')}-{os.urandom(3).hex()}-{command}"

    args = dict(args)
    # Copy the image into the queue so the job survives the source file moving.
    if command == "post" and args.get("image"):
        src = Path(args["image"])
        _payload_dir().mkdir(parents=True, exist_ok=True)
        dest = _payload_dir() / f"{job_id}{src.suffix}"
        shutil.copyfile(src, dest)
        args["image"] = str(dest)

    job = {
        "id": job_id,
        "command": command,
        "args": args,
        "enqueued_at": now.isoformat().replace("+00:00", "Z"),
        "attempts": 0,
        "last_error": None,
    }
    _atomic_write(_dir("pending") / f"{job_id}.json", job)
    return job_id


# --- runner ----------------------------------------------------------------

def _claim_oldest() -> Path | None:
    """Atomically move the oldest pending job into processing/ and return it."""
    for src in sorted(_dir("pending").glob("*.json")):
        dest = _dir("processing") / src.name
        try:
            os.rename(src, dest)  # atomic claim; loses the race -> FileNotFoundError
        except FileNotFoundError:
            continue
        return dest
    return None


def _execute(job: dict) -> int:
    from .cli import _DISPATCH  # lazy import to avoid a circular import at module load

    command = job["command"]
    handler = _DISPATCH.get(command)
    if handler is None:
        print(f"queue: unknown command {command!r}", file=sys.stderr)
        return 1
    ns = argparse.Namespace(command=command, **_RUN_DEFAULTS, **job["args"])
    try:
        return handler(ns)
    except Exception as e:  # a crashing handler must not kill the runner
        print(f"queue: job {job['id']} crashed: {e}", file=sys.stderr)
        return 1


def _finish(path: Path, job: dict, state: str) -> None:
    dest = _dir(state) / f"{job['id']}.json"
    _atomic_write(dest, job)
    path.unlink(missing_ok=True)


def parse_active_hours(spec: str) -> tuple[int, int]:
    """Parse an active-hours window like '8-20' into (start, end) local hours."""
    try:
        start_s, end_s = spec.split("-", 1)
        start, end = int(start_s), int(end_s)
    except ValueError:
        raise ValueError(f"--active-hours must look like '8-20', got {spec!r}")
    for h in (start, end):
        if not 0 <= h <= 24:
            raise ValueError(f"--active-hours values must be 0-24, got {spec!r}")
    return start, end


def _within_hours(now: float, hours: tuple[int, int]) -> bool:
    start, end = hours
    h = datetime.fromtimestamp(now).hour
    if start <= end:
        return start <= h < end
    return h >= start or h < end  # overnight window, e.g. 20-6


def _today(now: float) -> str:
    return datetime.fromtimestamp(now).strftime("%Y-%m-%d")


def _sent_today(state: dict, now: float) -> int:
    if state.get("day") != _today(now):
        return 0
    return int(state.get("sent_today", 0))


def _record_send(state: dict, now: float, min_interval: int, max_interval: int) -> None:
    """After an execution, advance the spacing target and the daily counter."""
    state["last_send"] = now
    if max_interval > min_interval:
        # Randomize the next gap so actual sends land at irregular times.
        state["next_gap"] = random.uniform(min_interval, max_interval)
    else:
        state.pop("next_gap", None)
    today = _today(now)
    if state.get("day") != today:
        state["day"] = today
        state["sent_today"] = 0
    state["sent_today"] = int(state.get("sent_today", 0)) + 1
    _save_run_state(state)


def run_once(
    min_interval: int = 0,
    max_interval: int = 0,
    active_hours: tuple[int, int] | None = None,
    daily_cap: int = 0,
    _now: float | None = None,
) -> int:
    """Execute the oldest pending job, subject to anti-pattern gates. Returns the
    job's exit code, or EMPTY if nothing ran (empty queue, or a spacing /
    active-hours / daily-cap gate held the job back).

    Spacing: a job runs only once ``now - last_send`` exceeds the required gap.
    With ``max_interval > min_interval`` that gap is re-randomized after every
    send (so ticks land irregularly); otherwise it is a flat ``min_interval``.
    """
    ensure_dirs()
    now = time.time() if _now is None else _now
    state = _load_run_state()

    if active_hours and not _within_hours(now, active_hours):
        return EMPTY
    if daily_cap and _sent_today(state, now) >= daily_cap:
        return EMPTY

    if "last_send" in state:  # no prior send -> no spacing to honor yet
        required = state.get("next_gap", min_interval) if max_interval > min_interval else min_interval
        if required and (now - state["last_send"]) < required:
            return EMPTY

    path = _claim_oldest()
    if path is None:
        return EMPTY

    job = _read(path)
    rc = _execute(job)
    # A failed job still consumes a slot, so a permanently-broken job can't
    # hammer LinkedIn across its retry attempts.
    _record_send(state, now, min_interval, max_interval)

    if rc == 0:
        _finish(path, job, "done")
        return 0

    job["attempts"] += 1
    job["last_error"] = f"exit code {rc}"
    # Handlers return 1 for both transient and permanent errors, so retry is
    # blind: a permanent failure burns its attempts then dead-letters.
    _finish(path, job, "failed" if job["attempts"] >= MAX_ATTEMPTS else "pending")
    return rc


# --- management ------------------------------------------------------------

def list_jobs(state: str = "pending") -> list[dict]:
    d = _dir(state)
    if not d.exists():
        return []
    return [_read(p) for p in sorted(d.glob("*.json"))]


def counts() -> dict[str, int]:
    return {state: len(list(_dir(state).glob("*.json"))) if _dir(state).exists() else 0
            for state in _STATES}


def _find(job_id: str) -> tuple[str, Path] | None:
    for state in _STATES:
        path = _dir(state) / f"{job_id}.json"
        if path.exists():
            return state, path
    return None


def _drop_payload(job_id: str) -> None:
    if _payload_dir().exists():
        for p in _payload_dir().glob(f"{job_id}*"):
            p.unlink(missing_ok=True)


def remove(job_id: str) -> bool:
    found = _find(job_id)
    if found is None:
        return False
    found[1].unlink(missing_ok=True)
    _drop_payload(job_id)
    return True


def clear() -> int:
    n = 0
    for path in _dir("pending").glob("*.json"):
        job_id = path.stem
        path.unlink(missing_ok=True)
        _drop_payload(job_id)
        n += 1
    return n


def retry(job_id: str) -> bool:
    """Move a dead-lettered job back to pending and reset its attempt count."""
    path = _dir("failed") / f"{job_id}.json"
    if not path.exists():
        return False
    job = _read(path)
    job["attempts"] = 0
    job["last_error"] = None
    _atomic_write(_dir("pending") / f"{job_id}.json", job)
    path.unlink(missing_ok=True)
    return True


# --- scheduler (macOS launchd) ---------------------------------------------

_PLIST_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
{args}
    </array>
    <key>StartInterval</key>
    <integer>{interval}</integer>
    <key>StandardOutPath</key>
    <string>{log}</string>
    <key>StandardErrorPath</key>
    <string>{log}</string>
</dict>
</plist>
"""


def _clinkedin_binary() -> str:
    return shutil.which("clinkedin") or os.path.abspath(sys.argv[0])


def runner_args(
    min_interval: int = 300,
    max_interval: int = 1200,
    active_hours: str | None = "8-20",
    daily_cap: int = 0,
) -> list[str]:
    """Build the `queue run …` argv the scheduler should invoke. Defaults to a
    jittered 5-20 min gap within local 08:00-20:00 (pass active_hours='none' or
    a falsy value to run around the clock, daily_cap=0 for no ceiling)."""
    args = [
        _clinkedin_binary(), "queue", "run",
        "--min-interval", str(min_interval),
        "--max-interval", str(max_interval),
    ]
    if active_hours and active_hours.lower() != "none":
        args += ["--active-hours", active_hours]
    if daily_cap:
        args += ["--daily-cap", str(daily_cap)]
    return args


def install_scheduler(
    interval: int = 60,
    min_interval: int = 300,
    max_interval: int = 1200,
    active_hours: str | None = "8-20",
    daily_cap: int = 0,
) -> Path:
    """Write a launchd agent that wakes every `interval` seconds and runs
    `queue run` with anti-pattern gates baked in. The agent waking often but the
    runner gating sends is what produces irregular, daytime-only send times."""
    ensure_dirs()
    args = runner_args(min_interval, max_interval, active_hours, daily_cap)
    arg_xml = "\n".join(f"        <string>{a}</string>" for a in args)
    path = plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_PLIST_TEMPLATE.format(
        label=PLIST_LABEL,
        args=arg_xml,
        interval=interval,
        log=runner_log(),
    ))
    return path


def uninstall_scheduler() -> bool:
    path = plist_path()
    if not path.exists():
        return False
    path.unlink()
    return True
