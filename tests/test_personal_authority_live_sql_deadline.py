"""Real RW SQLite coexistence and finite new-lease consumer failures.

No old consumer/global is patched. Rejection projections and new scalar faults
are synthetic; fixture recovery of known CRT resources is independent of lease
normal close. Root alone performs the admitted isolated test execution.
"""
from collections import Counter
from contextlib import contextmanager
import ctypes
import errno
import hashlib
import io
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from reno import personal_authority_directory_deadline as directories
from reno import personal_authority_live_sql_deadline as subject
from reno import personal_authority_namespace3_deadline as deadline
from reno import storage_source as source
from reno import sync_media as safe


pytestmark = pytest.mark.skipif(os.name != "nt", reason="real Windows live-SQL HANDLE/CRT lease")
NAME = "live.sqlite3"
PLAIN = "绍兴\x00\r\n独立来源".encode() + b"EOF-sentinel"


def native(path):
    text = os.path.abspath(os.fspath(path))
    if text.startswith("\\\\?\\"):
        return text
    return "\\\\?\\UNC\\" + text[2:] if text.startswith("\\\\") else "\\\\?\\" + text


def note(first):
    try:
        BaseException.add_note(first, "R825_FIXTURE_CLEANUP_FAILED")
    except BaseException:
        pass


@contextmanager
def sqlite_owner(path):
    """Protect acquisition immediately and actually close the connection."""
    connection, first = None, None
    try:
        connection = sqlite3.connect(native(path))
        yield connection
    except BaseException as failure:
        first = failure
        raise
    finally:
        if connection is not None:
            try:
                connection.close()
            except BaseException:
                if first is None:
                    raise
                note(first)


@pytest.fixture
def env(tmp_path):
    count = 0

    @contextmanager
    def opened(*, plain=False, missing=False):
        nonlocal count
        count += 1
        root = tmp_path / ("浙江绍兴-live-" + str(count))
        root.mkdir()
        path = root / NAME
        with sqlite_owner(path) if not (plain or missing) else empty_owner() as connection:
            if connection is not None:
                assert connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0].lower() == "delete"
                connection.execute("CREATE TABLE samples(id INTEGER PRIMARY KEY, payload BLOB)")
                connection.execute("INSERT INTO samples(payload) VALUES (?)", (PLAIN,))
                connection.commit()
            elif not missing:
                with open(native(path), "wb") as sink:
                    sink.write(PLAIN)
            expected = None if missing else Path(native(path)).read_bytes()
            budget = deadline._RestrictedBudget(deadline.Namespace3Deadline(1.0e15))
            with directories.DeadlineDirectory(root, base=root,
                    deadline_token=budget.effective_deadline) as directory:
                yield SimpleNamespace(root=root, file=path, connection=connection,
                                      expected=expected, budget=budget, directory=directory)

    return opened


@contextmanager
def empty_owner():
    yield None


def lease(item, **kwargs):
    return subject.source_reader(item.directory, NAME,
        deadline_token=item.budget.effective_deadline, **kwargs)


def error(call, suffix):
    with pytest.raises(subject.LiveSqlDeadlineError) as raised:
        call()
    assert raised.value.code == "LIVE_SQL_DEADLINE_" + suffix
    return raised.value


def same_error(call, first):
    with pytest.raises(type(first)) as raised:
        call()
    assert raised.value is first


def no_action(*_args, **_kwargs):
    pytest.fail("refused lease operation reached a forbidden new consumer")


class Scalar:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


class Ledger:
    """Only actual alias inputs/returns and attempts; never subject internals."""
    def __init__(self, monkeypatch):
        names = ("_create_file", "_get_info", "_open_osfhandle", "_get_osfhandle",
                 "_file_io", "_raw_fileno", "_fstat", "_retire_raw", "_close_fd", "_close_handle")
        self.original = {name: getattr(subject, name) for name in names}
        self.calls, self.events = Counter(), []
        self.handles, self.fds, self.raws, self.flags, self.borrowed = [], [], [], [], []
        self.fd_attempts, self.handle_attempts = [], []

        def create(*args):
            self.calls["create"] += 1
            value = self.original["_create_file"](*args)
            self.flags.append(args[1:])
            self.handles.append(value)
            self.events.append(("create",))
            return value

        def info(handle, address):
            self.calls["info"] += 1
            value = self.original["_get_info"](handle, address)
            self.events.append(("info", self.calls["info"]))
            return value

        def transfer(handle, flags):
            self.calls["transfer"] += 1
            fd = self.original["_open_osfhandle"](handle, flags)
            self.fds.append(fd)
            self.events.append(("transfer",))
            return fd

        def query(fd):
            self.calls["query"] += 1
            value = self.original["_get_osfhandle"](fd)
            self.borrowed.append(value)
            self.events.append(("query",))
            return value

        def make_raw(*args, **kwargs):
            self.calls["raw"] += 1
            value = self.original["_file_io"](*args, **kwargs)
            self.raws.append(value)
            self.events.append(("raw",))
            return value

        def fileno(raw):
            self.calls["fileno"] += 1
            value = self.original["_raw_fileno"](raw)
            self.events.append(("fileno", self.calls["fileno"]))
            return value

        def fstat(fd):
            self.calls["fstat"] += 1
            value = self.original["_fstat"](fd)
            self.events.append(("fstat", self.calls["fstat"]))
            return value

        def retire(raw):
            self.calls["retire"] += 1
            self.events.append(("raw-attempt",))
            value = self.original["_retire_raw"](raw)
            self.events.append(("raw-return",))
            return value

        def close_fd(fd):
            self.calls["close-fd"] += 1
            self.fd_attempts.append(fd)
            self.events.append(("fd-attempt",))
            value = self.original["_close_fd"](fd)
            self.events.append(("fd-return",))
            return value

        def close_handle(handle):
            self.calls["close-handle"] += 1
            self.handle_attempts.append(handle)
            self.events.append(("handle-attempt",))
            value = self.original["_close_handle"](handle)
            self.events.append(("handle-return",))
            return value

        actions = (create, info, transfer, query, make_raw, fileno, fstat, retire, close_fd, close_handle)
        for name, action in zip(names, actions):
            monkeypatch.setattr(subject, name, action)


def assert_known_closed(fd):
    with pytest.raises(OSError) as raised:
        os.fstat(fd)
    assert raised.value.errno == errno.EBADF


def write_growth(item):
    before = os.stat(native(item.file)).st_size
    item.connection.execute("INSERT INTO samples(payload) VALUES (zeroblob(131072))")
    item.connection.commit()
    after = os.stat(native(item.file)).st_size
    assert after > before
    assert item.connection.execute("SELECT count(*) FROM samples").fetchone()[0] == 2


def stat_projection(info, field):
    values = {name: getattr(info, name) for name in (
        "st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode", "st_nlink")}
    values["st_file_attributes"] = getattr(info, "st_file_attributes", 0)
    values[field] += 1
    return SimpleNamespace(**values)


# L01: exact live binding, construction-only factory, generic plain names.
def test_binding_input_and_single_use_refuse_before_file_acquisition(env, monkeypatch):
    with env(plain=True) as item:
        ledger = Ledger(monkeypatch)
        token = item.budget.effective_deadline
        error(lambda: subject.source_reader(object(), NAME, deadline_token=token), "INVALID_ARGUMENT")
        other = deadline.Namespace3Deadline(token.absolute_monotonic)
        assert other == token and other is not token
        error(lambda: subject.source_reader(item.directory, NAME, deadline_token=other), "INVALID_ARGUMENT")
        class NameChild(str):
            pass
        error(lambda: subject.source_reader(item.directory, NameChild(NAME), deadline_token=token), "INVALID_ARGUMENT")
        for name in ("", "../outside", "bad\\name"):
            with pytest.raises(safe.SyncError) as raised:
                subject.source_reader(item.directory, name, deadline_token=token)
            assert raised.value.code == "SYNC_MEDIA_UNSAFE_PATH"
        fresh_dir = directories.DeadlineDirectory(item.root, base=item.root, deadline_token=token)
        with pytest.raises(directories.DirectoryDeadlineError) as closed:
            subject.source_reader(fresh_dir, NAME, deadline_token=token)
        assert closed.value.code == "DIRECTORY_DEADLINE_CLOSED"
        for option in ("write", "create", "lock", "share"):
            with pytest.raises(TypeError):
                lease(item, **{option: True})
        selected = lease(item)
        assert type(selected) is subject.LiveSqlDeadlineLease and ledger.calls["create"] == 0
        with selected as raw:
            assert raw.read() == PLAIN
        closes = list(ledger.fd_attempts)
        error(selected.__enter__, "CLOSED")
        error(lambda: selected.__exit__(None, None, None), "CLOSED")
        assert ledger.fd_attempts == closes and len(closes) == 1
        item.directory.verify()
    with pytest.raises(directories.DirectoryDeadlineError):
        subject.source_reader(item.directory, NAME, deadline_token=token)


def test_real_missing_file_is_not_created_and_entry_error_remains_first(env, monkeypatch):
    with env(missing=True) as item:
        ledger = Ledger(monkeypatch)
        selected = lease(item)
        with pytest.raises(FileNotFoundError) as raised:
            selected.__enter__()
        first = raised.value
        same_error(selected.__enter__, first)
        same_error(lambda: selected.__exit__(None, None, None), first)
        assert ledger.calls["create"] == ledger.calls["close-fd"] == 0
        assert not os.path.exists(native(item.file))


# L02: real existing RW connection, exact raw reads and no share-delete.
def test_actual_rw_sqlite_connection_coexists_with_unbuffered_same_fd_lease(env, monkeypatch):
    with env() as item:
        ledger = Ledger(monkeypatch)
        with lease(item) as raw:
            assert type(raw) is io.FileIO and raw.closefd is False
            fd = raw.fileno()
            assert fd == ledger.fds[0]
            pieces = [raw.read(1)]
            assert pieces[0] == item.expected[:1]
            while True:
                chunk = raw.read(4096)
                assert raw.fileno() == fd
                if not chunk:
                    break
                pieces.append(chunk)
            full = b"".join(pieces)
            assert full == item.expected and hashlib.sha256(full).digest() == hashlib.sha256(item.expected).digest()
            assert raw.seek(0) == 0 and raw.read() == item.expected and raw.read(1) == b""
            assert item.budget.control_bytes == 0
            assert item.connection.execute("SELECT count(*) FROM samples").fetchone()[0] == 1
            assert ledger.fd_attempts == [] and ledger.handle_attempts == []
        assert raw.closed and ledger.fd_attempts == [fd] and ledger.handle_attempts == []
        assert ledger.flags == [(0x80000000, 3, None, 3, 0x00200000, None)]
        assert ledger.calls["info"] == ledger.calls["fstat"] == 2 and ledger.calls["query"] == 1
        ordered = [("fstat", 2), ("query",), ("info", 2), ("raw-attempt",), ("fd-attempt",), ("fd-return",)]
        assert [ledger.events.index(event) for event in ordered] == sorted(ledger.events.index(event) for event in ordered)
        assert_known_closed(fd)
        assert item.connection.execute("SELECT count(*) FROM samples").fetchone()[0] == 1
        item.directory.verify()


def test_lease_alone_denies_actual_delete_of_its_private_regular_file(env, monkeypatch):
    with env(plain=True) as item:
        ledger = Ledger(monkeypatch)
        target = item.file.resolve(strict=True)
        assert target.parent == item.root.resolve(strict=True)
        with lease(item):
            with pytest.raises(PermissionError) as blocked:
                os.unlink(native(target))
            assert blocked.value.winerror in (5, 32)
            assert Path(native(target)).read_bytes() == PLAIN
        assert ledger.flags[0][1] == 3 and ledger.calls["close-fd"] == 1
        assert Path(native(target)).read_bytes() == PLAIN


# L03: a real writer changes size during yield; the terminal fstat rejects it.
def test_real_rw_commit_size_change_is_rejected_at_terminal_fstat(env, monkeypatch):
    with env() as item:
        ledger = Ledger(monkeypatch)
        with pytest.raises(source.SourceReadError) as raised:
            with lease(item):
                write_growth(item)
        assert raised.value.code == "SOURCE_CHANGED"
        assert ledger.calls["fstat"] == 2 and ledger.calls["info"] == 1 and ledger.calls["query"] == 0
        assert ledger.calls["close-fd"] == 1 and ledger.handle_attempts == []
        item.directory.verify()


# L04: explicit same-representation rejection and a genuine late path window.
@pytest.mark.parametrize("phase", ["opening", "terminal"])
def test_signature_domains_preserve_cross_four_and_same_full_comparisons(env, monkeypatch, phase):
    with env() as item:
        ledger = Ledger(monkeypatch)
        fstat = subject._fstat
        def projected(fd):
            info = fstat(fd)
            expected = 1 if phase == "opening" else 2
            return stat_projection(info, "st_ino" if phase == "opening" else "st_ctime_ns") if ledger.calls["fstat"] == expected else info
        monkeypatch.setattr(subject, "_fstat", projected)
        selected = lease(item)
        with pytest.raises(source.SourceReadError) as raised:
            with selected:
                pass
        first = raised.value
        assert first.code == "SOURCE_CHANGED" and ledger.calls["query"] == 0
        same_error(lambda: selected.__exit__(None, None, None), first)
        assert ledger.calls["close-fd"] == 1


def test_real_writer_after_fstat_and_native_query_is_rejected_by_path_signature(env, monkeypatch):
    with env() as item:
        ledger = Ledger(monkeypatch)
        get_info = subject._get_info
        wrote = False
        def late_write(handle, address):
            nonlocal wrote
            result = get_info(handle, address)
            if ledger.calls["info"] == 2:
                assert result and ledger.calls["fstat"] == 2
                write_growth(item)
                wrote = True
            return result
        monkeypatch.setattr(subject, "_get_info", late_write)
        with pytest.raises(source.SourceReadError) as raised:
            with lease(item):
                pass
        assert raised.value.code == "SOURCE_CHANGED" and wrote is True
        assert ledger.calls["query"] == 1 and ledger.calls["info"] == 2 and ledger.calls["close-fd"] == 1


# L05: fresh known-FD native query, unsafe-before-identity and original failures.
@pytest.mark.parametrize("fault", ["identity", "reparse-and-identity", "directory", "links"])
def test_terminal_native_query_rejects_explicit_projection_without_new_owner(env, monkeypatch, fault):
    with env() as item:
        ledger = Ledger(monkeypatch)
        query = subject._get_info
        def projected(handle, address):
            result = query(handle, address)
            if ledger.calls["info"] == 2:
                assert result and ledger.calls["query"] == 1
                info = ctypes.cast(address, ctypes.POINTER(safe._HandleInfo)).contents
                if fault == "identity":
                    info.index_low ^= 1
                elif fault == "reparse-and-identity":
                    info.attributes |= safe._REPARSE
                    info.index_low ^= 1
                elif fault == "directory":
                    info.attributes |= 0x10
                else:
                    info.links = 2
            return result
        monkeypatch.setattr(subject, "_get_info", projected)
        with pytest.raises(source.SourceReadError) as raised:
            with lease(item):
                pass
        assert raised.value.code == ("SOURCE_CHANGED" if fault == "identity" else "SOURCE_UNSAFE")
        assert ledger.calls["info"] == 2 and ledger.calls["query"] == ledger.calls["close-fd"] == 1
        assert ledger.handle_attempts == []


def test_terminal_get_info_failure_captures_last_error_before_any_later_scalar(env, monkeypatch):
    with env() as item:
        ledger = Ledger(monkeypatch)
        get_info = subject._get_info
        def fail(handle, address):
            result = get_info(handle, address)
            if ledger.calls["info"] == 2:
                assert result
                monkeypatch.setattr(subject, "_monotonic", no_action)
                ctypes.set_last_error(5)
                return False
            return result
        monkeypatch.setattr(subject, "_get_info", fail)
        selected = lease(item)
        with pytest.raises(OSError) as raised:
            with selected:
                pass
        first = raised.value
        assert first.winerror == 5 and ledger.calls["close-fd"] == 1
        same_error(lambda: selected.__exit__(None, None, None), first)


@pytest.mark.parametrize("fault", ["original-error", "invalid-result"])
def test_fresh_handle_conversion_failure_never_falls_back_to_cached_handle(env, monkeypatch, fault):
    with env() as item:
        ledger = Ledger(monkeypatch)
        query = subject._get_osfhandle
        first = OSError("finite borrowed-handle query failure")
        def refused(fd):
            query(fd)
            if fault == "original-error":
                raise first
            return False
        monkeypatch.setattr(subject, "_get_osfhandle", refused)
        expected = OSError if fault == "original-error" else subject.LiveSqlDeadlineError
        with pytest.raises(expected) as raised:
            with lease(item):
                pass
        if fault == "original-error":
            assert raised.value is first
        else:
            assert raised.value.code == "LIVE_SQL_DEADLINE_INVALID_ARGUMENT"
        assert ledger.calls["query"] == 1 and ledger.calls["info"] == 1
        assert ledger.calls["close-fd"] == 1 and ledger.handle_attempts == []


# L06: a closed wrapper or changed reported number cannot bypass the live raw gate.
def test_actual_raw_early_close_refuses_normal_terminal_but_lease_still_closes_fd(env, monkeypatch):
    with env() as item:
        ledger = Ledger(monkeypatch)
        selected = lease(item)
        with pytest.raises(ValueError) as raised:
            with selected as raw:
                fd = raw.fileno()
                raw.close()
                assert raw.closed and os.fstat(fd).st_size == len(item.expected)
                assert ledger.fd_attempts == []
        first = raised.value
        assert type(first) is ValueError
        assert ledger.calls["fstat"] == ledger.calls["info"] == 1 and ledger.calls["query"] == 0
        assert ledger.calls["retire"] == 0 and ledger.fd_attempts == [fd]
        assert_known_closed(fd)
        same_error(lambda: selected.__exit__(None, None, None), first)
        assert ledger.fd_attempts == [fd]


def test_terminal_changed_raw_number_is_refused_before_fstat_or_handle_query(env, monkeypatch):
    with env() as item:
        ledger = Ledger(monkeypatch)
        selected = lease(item)
        with pytest.raises(subject.LiveSqlDeadlineError) as raised:
            with selected as raw:
                expected = raw.fileno()
                fileno = subject._raw_fileno
                def changed(value):
                    assert fileno(value) == expected
                    return expected + 100000
                monkeypatch.setattr(subject, "_raw_fileno", changed)
        first = raised.value
        assert first.code == "LIVE_SQL_DEADLINE_INVALID_ARGUMENT"
        assert ledger.calls["fstat"] == 1 and ledger.calls["query"] == 0
        assert ledger.fd_attempts == [expected] and ledger.handle_attempts == []


# L07: finite own-scalar gates and acquired-owner registration, not hard timing.
def test_new_scalar_expired_before_enter_does_not_acquire_a_file(env, monkeypatch):
    with env() as item:
        ledger = Ledger(monkeypatch)
        selected = lease(item)
        monkeypatch.setattr(subject, "_monotonic", lambda: item.budget.effective_deadline.absolute_monotonic)
        error(selected.__enter__, "DEADLINE")
        assert ledger.calls["create"] == ledger.calls["close-fd"] == ledger.calls["close-handle"] == 0
        item.budget.clock_only()


@pytest.mark.parametrize("stage", ["create", "transfer", "raw", "terminal-info"])
def test_actual_consumer_return_is_registered_before_new_scalar_expiry(env, monkeypatch, stage):
    with env() as item:
        ledger = Ledger(monkeypatch)
        scalar = Scalar(subject._monotonic())
        monkeypatch.setattr(subject, "_monotonic", scalar)
        name = {"create": "_create_file", "transfer": "_open_osfhandle", "raw": "_file_io", "terminal-info": "_get_info"}[stage]
        consume = getattr(subject, name)
        def post(*args, **kwargs):
            value = consume(*args, **kwargs)
            if stage != "terminal-info" or ledger.calls["info"] == 2:
                scalar.value = item.budget.effective_deadline.absolute_monotonic
            return value
        monkeypatch.setattr(subject, name, post)
        selected = lease(item)
        with pytest.raises(subject.LiveSqlDeadlineError) as raised:
            with selected:
                pass
        first = raised.value
        assert first.code == "LIVE_SQL_DEADLINE_DEADLINE"
        if stage == "create":
            assert ledger.handle_attempts == ledger.handles and ledger.fd_attempts == []
        else:
            assert len(ledger.fd_attempts) == 1 and ledger.handle_attempts == []
        assert all(raw.closed for raw in ledger.raws)
        same_error(lambda: selected.__exit__(None, None, None), first)
        assert ledger.calls["close-fd"] + ledger.calls["close-handle"] == 1


def test_new_pre_crt_gate_keeps_handle_owned_and_never_invokes_transfer(env, monkeypatch):
    with env() as item:
        ledger = Ledger(monkeypatch)
        actual = subject._get_info
        live = subject._monotonic()
        armed, gates = False, 0
        def information(handle, address):
            nonlocal armed
            result = actual(handle, address)
            armed = True
            return result
        def clock():
            nonlocal gates
            if armed:
                gates += 1
                # Static helper contract: post-GetInfo gate, then pre-CRT gate.
                if gates >= 2:
                    return item.budget.effective_deadline.absolute_monotonic
            return live
        monkeypatch.setattr(subject, "_get_info", information)
        monkeypatch.setattr(subject, "_monotonic", clock)
        selected = lease(item)
        error(selected.__enter__, "DEADLINE")
        assert gates == 2 and ledger.calls["transfer"] == 0
        assert ledger.handle_attempts == ledger.handles and ledger.fd_attempts == []


def test_actual_numeric_close_returns_before_new_final_scalar_refuses_context(env, monkeypatch):
    with env() as item:
        ledger = Ledger(monkeypatch)
        scalar = Scalar(subject._monotonic())
        monkeypatch.setattr(subject, "_monotonic", scalar)
        selected = lease(item)
        with pytest.raises(subject.LiveSqlDeadlineError) as raised:
            with selected:
                close_fd = subject._close_fd
                def post(fd):
                    result = close_fd(fd)
                    scalar.value = item.budget.effective_deadline.absolute_monotonic
                    return result
                monkeypatch.setattr(subject, "_close_fd", post)
        first = raised.value
        assert first.code == "LIVE_SQL_DEADLINE_DEADLINE"
        assert ledger.events[-1] == ("fd-return",) and ledger.calls["close-fd"] == 1
        same_error(lambda: selected.__exit__(None, None, None), first)
        assert ledger.calls["close-fd"] == 1


# L08: unknown transfer recovery is fixture-owned and never inferred by subject.
@pytest.mark.parametrize("after_crt", [False, True], ids=["before-actual-crt", "after-actual-crt-return"])
def test_no_legal_fd_return_is_unknown_with_only_known_fixture_recovery(env, monkeypatch, after_crt):
    ledger = Ledger(monkeypatch)
    transfer = subject._open_osfhandle
    known, failed = {"handle": None, "fd": None}, None
    first = OSError("finite opaque CRT transfer failure")
    def opaque(handle, flags):
        known["handle"] = handle
        if after_crt:
            known["fd"] = transfer(handle, flags)
            assert type(known["fd"]) is int and known["fd"] >= 0
        raise first
    monkeypatch.setattr(subject, "_open_osfhandle", opaque)
    for name in ("_file_io", "_fstat", "_get_osfhandle", "_raw_fileno"):
        monkeypatch.setattr(subject, name, no_action)
    try:
        with env() as item:
            selected = lease(item)
            same_error(selected.__enter__, first)
            assert "LIVE_SQL_DEADLINE_TRANSFER_UNKNOWN" in getattr(first, "__notes__", [])
            same_error(selected.__enter__, first)
            same_error(lambda: selected.__exit__(None, None, None), first)
            assert known["handle"] is not None and ledger.fd_attempts == ledger.handle_attempts == []
    except BaseException as failure:
        failed = failure
        raise
    finally:
        untouched = known["handle"] is not None and known["handle"] not in ledger.handle_attempts and not ledger.fd_attempts
        if untouched:
            try:
                if known["fd"] is not None:
                    ledger.original["_close_fd"](known["fd"])
                elif not ledger.original["_close_handle"](known["handle"]):
                    raise ctypes.WinError(ctypes.get_last_error())
            except BaseException:
                if failed is None:
                    raise
                note(failed)


def test_raw_constructor_original_failure_still_closes_only_known_fd(env, monkeypatch):
    with env() as item:
        ledger = Ledger(monkeypatch)
        first = OSError("finite FileIO constructor failure before call")
        def refused(*_args, **_kwargs):
            raise first
        monkeypatch.setattr(subject, "_file_io", refused)
        selected = lease(item)
        same_error(selected.__enter__, first)
        assert ledger.calls["close-fd"] == 1 and ledger.handle_attempts == [] and ledger.raws == []
        same_error(lambda: selected.__exit__(None, None, None), first)
        assert ledger.calls["close-fd"] == 1


@pytest.mark.parametrize("kind", [RuntimeError, KeyboardInterrupt, SystemExit], ids=["ordinary", "interrupt", "exit"])
def test_body_first_skips_terminal_and_survives_expiry_and_real_close_post_fault(env, monkeypatch, kind):
    with env() as item:
        ledger = Ledger(monkeypatch)
        scalar = Scalar(subject._monotonic())
        monkeypatch.setattr(subject, "_monotonic", scalar)
        first, cleanup = kind("finite original body failure"), OSError("finite close postfailure")
        selected = lease(item)
        with pytest.raises(kind) as raised:
            with selected:
                scalar.value = item.budget.effective_deadline.absolute_monotonic
                close_fd = subject._close_fd
                def post(fd):
                    close_fd(fd)
                    raise cleanup
                monkeypatch.setattr(subject, "_close_fd", post)
                raise first
        assert raised.value is first and ledger.calls["fstat"] == ledger.calls["info"] == 1
        assert ledger.calls["query"] == 0 and ledger.calls["close-fd"] == 1
        assert "LIVE_SQL_DEADLINE_CLEANUP_FAILED" in getattr(first, "__notes__", [])
        same_error(lambda: selected.__exit__(None, None, None), first)
        assert ledger.calls["close-fd"] == 1


def test_terminal_original_query_first_survives_actual_close_postfailure(env, monkeypatch):
    with env() as item:
        ledger = Ledger(monkeypatch)
        first, cleanup = OSError("finite terminal query failure"), RuntimeError("finite cleanup failure")
        selected = lease(item)
        with pytest.raises(OSError) as raised:
            with selected:
                query, close_fd = subject._get_osfhandle, subject._close_fd
                def refused(fd):
                    query(fd)
                    raise first
                def post(fd):
                    close_fd(fd)
                    raise cleanup
                monkeypatch.setattr(subject, "_get_osfhandle", refused)
                monkeypatch.setattr(subject, "_close_fd", post)
        assert raised.value is first and ledger.calls["close-fd"] == 1
        assert "LIVE_SQL_DEADLINE_CLEANUP_FAILED" in getattr(first, "__notes__", [])
        same_error(lambda: selected.__exit__(None, None, None), first)
        assert ledger.calls["close-fd"] == 1


def test_raw_retirement_first_does_not_skip_numeric_close_or_yield_to_later_expiry(env, monkeypatch):
    with env() as item:
        ledger = Ledger(monkeypatch)
        scalar = Scalar(subject._monotonic())
        monkeypatch.setattr(subject, "_monotonic", scalar)
        first = OSError("finite wrapper retirement postfailure")
        selected = lease(item)
        with pytest.raises(OSError) as raised:
            with selected:
                retire, close_fd = subject._retire_raw, subject._close_fd
                def refused(raw):
                    retire(raw)
                    raise first
                def post(fd):
                    result = close_fd(fd)
                    scalar.value = item.budget.effective_deadline.absolute_monotonic
                    return result
                monkeypatch.setattr(subject, "_retire_raw", refused)
                monkeypatch.setattr(subject, "_close_fd", post)
        assert raised.value is first and ledger.calls["retire"] == ledger.calls["close-fd"] == 1
        assert all(raw.closed for raw in ledger.raws)
        same_error(lambda: selected.__exit__(None, None, None), first)
        assert ledger.calls["close-fd"] == 1


def test_opaque_numeric_close_result_is_first_and_never_retried_or_probed(env, monkeypatch):
    with env() as item:
        ledger = Ledger(monkeypatch)
        first = OSError("finite numeric closer no return")
        selected = lease(item)
        with pytest.raises(OSError) as raised:
            with selected:
                close_fd = subject._close_fd
                def opaque(fd):
                    close_fd(fd)
                    raise first
                monkeypatch.setattr(subject, "_close_fd", opaque)
        assert raised.value is first and ledger.calls["close-fd"] == 1
        same_error(selected.__enter__, first)
        same_error(lambda: selected.__exit__(None, None, None), first)
        assert ledger.calls["close-fd"] == 1
        # Subject attempted this numeric close: no fixture probe or retry.
