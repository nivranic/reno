"""Video locks use real OS byte locks/processes; no downloads or model calls."""
import errno
import multiprocessing
import os
from pathlib import Path
import time
from types import SimpleNamespace

import pytest

from reno import config, ingest


def _hold_lock(root, ready, inside, release):
    config.ORIG = Path(root)
    with ingest._vid_lock("long-download"):
        inside.set()
        ready.set()
        try:
            if not release.wait(25):
                raise RuntimeError("test holder was not released")
        finally:
            inside.clear()


def _wait_for_lock(root, attempting, inside, acquired, result):
    config.ORIG = Path(root)
    started = None
    original_locking = ingest.msvcrt.locking

    def signal_first_attempt(fd, mode, length):
        nonlocal started
        if mode != ingest.msvcrt.LK_UNLCK and started is None:
            started = time.monotonic()
            attempting.set()
        return original_locking(fd, mode, length)

    ingest.msvcrt.locking = signal_first_attempt
    try:
        with ingest._vid_lock("long-download"):
            elapsed = time.monotonic() - started
            overlap = inside.is_set()
            acquired.set()
        result.send({"acquired": True, "overlap": overlap, "elapsed": elapsed})
    except BaseException as exc:
        result.send({"acquired": False, "error": type(exc).__name__, "errno": getattr(exc, "errno", None)})
    finally:
        result.close()


def _stop(process):
    if process.pid is None:
        return
    process.join(timeout=3)
    if process.is_alive():
        process.terminate()
        process.join(timeout=3)
    if process.is_alive():
        process.kill()
        process.join(timeout=3)


@pytest.mark.skipif(os.name != "nt", reason="Regression for Windows CRT byte-lock retry limit")
def test_windows_waits_for_real_process_past_crt_retry_limit(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    ready, inside, release, attempting, acquired = [ctx.Event() for _ in range(5)]
    result, sender = ctx.Pipe(duplex=False)
    holder = ctx.Process(target=_hold_lock, args=(str(tmp_path), ready, inside, release))
    waiter = ctx.Process(target=_wait_for_lock, args=(str(tmp_path), attempting, inside, acquired, sender))
    try:
        holder.start()
        assert ready.wait(timeout=8), "holder never acquired the test lock"
        waiter.start()
        sender.close()
        assert attempting.wait(timeout=8), "waiter never attempted the OS lock"
        # The old LK_LOCK exhausts its ten one-second retries here. Start this
        # interval at the first actual OS call, not at unpredictable process boot.
        assert not acquired.wait(timeout=11.1), "waiter entered while holder still owned the lock"
        assert inside.is_set() and holder.is_alive()
        release.set()
        assert result.poll(8), "waiter did not finish after the lock was released"
        outcome = result.recv()
        assert outcome["acquired"], outcome
        assert not outcome["overlap"]
        assert outcome["elapsed"] >= 11.0
        waiter.join(timeout=3)
        holder.join(timeout=3)
        assert waiter.exitcode == 0 and holder.exitcode == 0
    finally:
        if holder.is_alive():
            release.set()
        _stop(holder)
        _stop(waiter)
        sender.close()
        result.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows process-death byte-lock behavior")
def test_windows_process_death_releases_lock_for_another_process(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    ready, inside, release, attempting, acquired = [ctx.Event() for _ in range(5)]
    result, sender = ctx.Pipe(duplex=False)
    holder = ctx.Process(target=_hold_lock, args=(str(tmp_path), ready, inside, release))
    waiter = ctx.Process(target=_wait_for_lock, args=(str(tmp_path), attempting, inside, acquired, sender))
    try:
        holder.start()
        assert ready.wait(timeout=8)
        holder.terminate()
        holder.join(timeout=3)
        assert not holder.is_alive()
        # Forced termination does not execute the holder's Python finally.
        inside.clear()
        waiter.start()
        sender.close()
        assert result.poll(8)
        assert result.recv()["acquired"]
        waiter.join(timeout=3)
        assert waiter.exitcode == 0
    finally:
        # A killed Event.wait participant cannot acknowledge Condition.notify.
        # Never notify its orphaned multiprocessing synchronization primitive.
        if holder.is_alive():
            release.set()
        _stop(holder)
        _stop(waiter)
        sender.close()
        result.close()


@pytest.mark.parametrize("error_number", sorted({errno.EACCES, errno.EAGAIN, errno.EDEADLK}))
def test_normal_windows_contention_retries_same_byte_without_fixed_attempt_limit(tmp_path, monkeypatch, error_number):
    calls, delays = [], []
    lock_path = tmp_path / "existing.lock"
    lock_path.write_bytes(b"keep-lock-content")
    with lock_path.open("a+b") as stream:
        def locking(fd, mode, length):
            calls.append((fd, mode, length, stream.tell()))
            if len(calls) <= 25:
                raise OSError(error_number, "synthetic contention")

        monkeypatch.setattr(ingest.time, "sleep", delays.append)
        ingest._wait_windows_lock(stream, SimpleNamespace(locking=locking, LK_NBLCK=2))
        assert len(calls) == 26 and len(delays) == 25
        assert all(call == (stream.fileno(), 2, 1, 0) for call in calls)
    assert lock_path.read_bytes() == b"keep-lock-content"


def test_winerror_lock_violation_is_retried(tmp_path, monkeypatch):
    calls = []
    error = OSError(errno.EIO, "synthetic lock violation")
    error.winerror = 33

    def locking(*args):
        calls.append(args)
        if len(calls) == 1:
            raise error

    monkeypatch.setattr(ingest.time, "sleep", lambda _: None)
    with (tmp_path / "test.lock").open("a+b") as stream:
        ingest._wait_windows_lock(stream, SimpleNamespace(locking=locking, LK_NBLCK=2))
    assert len(calls) == 2


@pytest.mark.parametrize("error_number", [errno.EBADF, errno.EINVAL, errno.EIO, errno.EPERM])
def test_non_contention_errors_escape_immediately(tmp_path, monkeypatch, error_number):
    calls = []
    error = OSError(error_number, "synthetic non-contention failure")

    def locking(*args):
        calls.append(args)
        raise error

    def unexpected_sleep(_):
        pytest.fail("non-contention errors must not enter the retry loop")

    monkeypatch.setattr(ingest.time, "sleep", unexpected_sleep)
    with (tmp_path / "test.lock").open("a+b") as stream:
        with pytest.raises(OSError) as caught:
            ingest._wait_windows_lock(stream, SimpleNamespace(locking=locking, LK_NBLCK=2))
    assert caught.value is error and len(calls) == 1


def test_lock_file_is_not_truncated_and_body_exception_releases(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ORIG", tmp_path)
    path = tmp_path / ".lock-same-video"
    path.write_bytes(b"preserve-existing-content")
    with pytest.raises(RuntimeError, match="synthetic body failure"):
        with ingest._vid_lock("same-video"):
            raise RuntimeError("synthetic body failure")
    with ingest._vid_lock("same-video"):
        assert path.stat().st_size == len(b"preserve-existing-content")
    assert path.read_bytes() == b"preserve-existing-content"


@pytest.mark.skipif(os.name != "nt", reason="Windows acquire/unlock API contract")
def test_acquisition_error_does_not_yield_or_attempt_unlock(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ORIG", tmp_path)
    calls = []

    def invalid_handle(fd, mode, length):
        calls.append(mode)
        raise OSError(errno.EBADF, "synthetic invalid descriptor")

    monkeypatch.setattr(ingest.msvcrt, "locking", invalid_handle)
    with pytest.raises(OSError):
        with ingest._vid_lock("failed-acquire"):
            pytest.fail("an unacquired lock must never enter its critical section")
    assert calls == [ingest.msvcrt.LK_NBLCK]
