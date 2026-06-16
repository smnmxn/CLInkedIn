import argparse
from datetime import datetime

import pytest

from clinkedin import cli, jobqueue


def _local_ts(year, month, day, hour) -> float:
    """A POSIX timestamp for the given civil time in the machine's local zone
    (matching how the runner reads the clock via datetime.fromtimestamp)."""
    return datetime(year, month, day, hour).timestamp()


@pytest.fixture(autouse=True)
def _queue_root(tmp_path, monkeypatch):
    """Point the on-disk queue at a temp dir for every test."""
    monkeypatch.setattr(jobqueue, "QUEUE_ROOT", tmp_path / "queue")
    jobqueue.ensure_dirs()
    yield


def _connect_ns(**kwargs) -> argparse.Namespace:
    defaults = {
        "command": "connect",
        "url": "https://www.linkedin.com/in/foo/",
        "message": "hi",
        "dry_run": False,
        "yes": False,
        "queue": True,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _no_client(monkeypatch):
    def _explode(*a, **k):
        raise AssertionError("should not build a client while queueing")

    monkeypatch.setattr("clinkedin.cli.make_client", _explode)


# ---------- enqueue via the CLI handler ----------


def test_connect_queue_enqueues_without_client(monkeypatch):
    _no_client(monkeypatch)
    rc = cli._cmd_connect(_connect_ns())
    assert rc == 0
    pending = jobqueue.list_jobs("pending")
    assert len(pending) == 1
    assert pending[0]["command"] == "connect"
    assert pending[0]["args"] == {"url": "https://www.linkedin.com/in/foo/", "message": "hi"}
    assert pending[0]["attempts"] == 0


def test_queue_plus_dry_run_queues_nothing(monkeypatch):
    _no_client(monkeypatch)
    rc = cli._cmd_connect(_connect_ns(dry_run=True))
    assert rc == 0
    assert jobqueue.list_jobs("pending") == []


def test_comment_message_file_is_inlined(monkeypatch, tmp_path):
    _no_client(monkeypatch)
    f = tmp_path / "msg.txt"
    f.write_text("from a file")
    ns = argparse.Namespace(
        command="comment",
        url="urn:li:activity:7",
        message=None,
        message_file=str(f),
        dry_run=False,
        yes=False,
        queue=True,
    )
    assert cli._cmd_comment(ns) == 0
    # The runner must not depend on the source file still existing.
    f.unlink()
    job = jobqueue.list_jobs("pending")[0]
    assert job["args"]["message"] == "from a file"


# ---------- run_once ----------


def test_run_once_empty_returns_sentinel():
    assert jobqueue.run_once() == jobqueue.EMPTY


def test_run_once_success_moves_to_done(monkeypatch):
    jobqueue.enqueue("connect", {"url": "https://www.linkedin.com/in/foo/", "message": "hi"})

    seen = {}

    def _fake_connect(ns):
        seen["url"] = ns.url
        seen["yes"] = ns.yes
        seen["queue"] = ns.queue
        return 0

    monkeypatch.setitem(cli._DISPATCH, "connect", _fake_connect)

    rc = jobqueue.run_once()
    assert rc == 0
    assert seen == {"url": "https://www.linkedin.com/in/foo/", "yes": True, "queue": False}
    assert jobqueue.list_jobs("pending") == []
    assert len(jobqueue.list_jobs("done")) == 1


def test_run_once_retries_then_dead_letters(monkeypatch):
    jobqueue.enqueue("connect", {"url": "https://www.linkedin.com/in/foo/", "message": "hi"})

    monkeypatch.setitem(cli._DISPATCH, "connect", lambda ns: 1)

    # First MAX_ATTEMPTS-1 runs re-queue the job...
    for _ in range(jobqueue.MAX_ATTEMPTS - 1):
        assert jobqueue.run_once() == 1
        assert len(jobqueue.list_jobs("pending")) == 1
        assert jobqueue.list_jobs("failed") == []

    # ...the final failure dead-letters it.
    assert jobqueue.run_once() == 1
    assert jobqueue.list_jobs("pending") == []
    failed = jobqueue.list_jobs("failed")
    assert len(failed) == 1
    assert failed[0]["attempts"] == jobqueue.MAX_ATTEMPTS


def test_run_once_executes_oldest_first(monkeypatch):
    first = jobqueue.enqueue("connect", {"url": "a", "message": ""})
    second = jobqueue.enqueue("connect", {"url": "b", "message": ""})
    assert first < second  # filenames sort FIFO

    order = []
    monkeypatch.setitem(cli._DISPATCH, "connect", lambda ns: order.append(ns.url) or 0)

    jobqueue.run_once()
    jobqueue.run_once()
    assert order == ["a", "b"]


# ---------- management ----------


def test_remove_and_clear():
    jid = jobqueue.enqueue("connect", {"url": "a", "message": ""})
    jobqueue.enqueue("connect", {"url": "b", "message": ""})
    assert jobqueue.remove(jid) is True
    assert jobqueue.remove("nope") is False
    assert len(jobqueue.list_jobs("pending")) == 1
    assert jobqueue.clear() == 1
    assert jobqueue.list_jobs("pending") == []


def test_retry_moves_failed_to_pending(monkeypatch):
    jobqueue.enqueue("connect", {"url": "a", "message": ""})
    monkeypatch.setitem(cli._DISPATCH, "connect", lambda ns: 1)
    for _ in range(jobqueue.MAX_ATTEMPTS):
        jobqueue.run_once()
    failed = jobqueue.list_jobs("failed")
    assert len(failed) == 1
    jid = failed[0]["id"]

    assert jobqueue.retry(jid) is True
    assert jobqueue.list_jobs("failed") == []
    pending = jobqueue.list_jobs("pending")
    assert len(pending) == 1
    assert pending[0]["attempts"] == 0


def test_min_interval_guard_skips(monkeypatch):
    jobqueue.enqueue("connect", {"url": "a", "message": ""})
    jobqueue.enqueue("connect", {"url": "b", "message": ""})
    called = []
    monkeypatch.setitem(cli._DISPATCH, "connect", lambda ns: called.append(1) or 0)

    assert jobqueue.run_once(min_interval=3600, _now=1000.0) == 0  # first send
    # A second run before the interval elapses is a no-op.
    assert jobqueue.run_once(min_interval=3600, _now=1001.0) == jobqueue.EMPTY
    # Once the interval has passed, it runs again.
    assert jobqueue.run_once(min_interval=3600, _now=1000.0 + 3601) == 0
    assert len(called) == 2
    assert jobqueue.list_jobs("pending") == []


# ---------- anti-pattern gates ----------


def test_randomized_gap_is_persisted(monkeypatch):
    jobqueue.enqueue("connect", {"url": "a", "message": ""})
    jobqueue.enqueue("connect", {"url": "b", "message": ""})
    monkeypatch.setitem(cli._DISPATCH, "connect", lambda ns: 0)
    monkeypatch.setattr(jobqueue.random, "uniform", lambda lo, hi: 500.0)

    assert jobqueue.run_once(min_interval=300, max_interval=900, _now=1000.0) == 0
    # next_gap (500) not yet elapsed at +400 -> skip; elapsed at +600 -> run.
    assert jobqueue.run_once(min_interval=300, max_interval=900, _now=1400.0) == jobqueue.EMPTY
    assert jobqueue.run_once(min_interval=300, max_interval=900, _now=1600.0) == 0


def test_active_hours_skips_outside_window(monkeypatch):
    jobqueue.enqueue("connect", {"url": "a", "message": ""})
    called = []
    monkeypatch.setitem(cli._DISPATCH, "connect", lambda ns: called.append(1) or 0)

    # 03:00 local -> outside 8-20 -> skip; 12:00 -> inside -> run.
    night = _local_ts(2026, 6, 16, 3)
    noon = _local_ts(2026, 6, 16, 12)
    assert jobqueue.run_once(active_hours=(8, 20), _now=night) == jobqueue.EMPTY
    assert called == []
    assert jobqueue.run_once(active_hours=(8, 20), _now=noon) == 0
    assert called == [1]


def test_overnight_active_hours_window():
    assert jobqueue._within_hours(_local_ts(2026, 6, 16, 23), (20, 6)) is True
    assert jobqueue._within_hours(_local_ts(2026, 6, 16, 3), (20, 6)) is True
    assert jobqueue._within_hours(_local_ts(2026, 6, 16, 12), (20, 6)) is False


def test_daily_cap_blocks_after_n(monkeypatch):
    for u in ("a", "b", "c"):
        jobqueue.enqueue("connect", {"url": u, "message": ""})
    sent = []
    monkeypatch.setitem(cli._DISPATCH, "connect", lambda ns: sent.append(ns.url) or 0)

    base = _local_ts(2026, 6, 16, 12)
    assert jobqueue.run_once(daily_cap=2, _now=base) == 0
    assert jobqueue.run_once(daily_cap=2, _now=base + 1) == 0
    # Cap reached for the day.
    assert jobqueue.run_once(daily_cap=2, _now=base + 2) == jobqueue.EMPTY
    assert sent == ["a", "b"]
    # New day resets the counter.
    next_day = _local_ts(2026, 6, 17, 12)
    assert jobqueue.run_once(daily_cap=2, _now=next_day) == 0
    assert sent == ["a", "b", "c"]


def test_parse_active_hours():
    assert jobqueue.parse_active_hours("8-20") == (8, 20)
    assert jobqueue.parse_active_hours("20-6") == (20, 6)
    with pytest.raises(ValueError):
        jobqueue.parse_active_hours("8")
    with pytest.raises(ValueError):
        jobqueue.parse_active_hours("8-25")


# ---------- scheduler ----------


def test_install_scheduler_writes_plist(monkeypatch, tmp_path):
    monkeypatch.setattr(jobqueue.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(jobqueue, "QUEUE_ROOT", tmp_path / "queue")
    path = jobqueue.install_scheduler(interval=600)
    assert path.exists()
    text = path.read_text()
    assert "<integer>600</integer>" in text
    assert "<string>queue</string>" in text
    assert jobqueue.uninstall_scheduler() is True
    assert not path.exists()
    assert jobqueue.uninstall_scheduler() is False
