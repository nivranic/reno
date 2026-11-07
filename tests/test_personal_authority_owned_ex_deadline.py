"""r859 actual lock-stream owner and finite controlled return/lineage faults.

Prepared without importing or collecting subject. Root alone admits and runs.
The actual legacy directory remains borrowed; no Guard, complete outer EX,
source_closed, writer authority or expanded deadline is manufactured here.
"""
from collections import Counter
from dataclasses import FrozenInstanceError, fields
import ctypes
import io
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace

import pytest

from reno import personal_authority_namespace3_deadline as deadline
from reno import personal_authority_owned_ex_deadline as owned
from reno import storage_lifecycle as lifecycle
from reno import sync_media as safe


pytestmark = pytest.mark.skipif(os.name != "nt", reason="actual Windows lock stream")
OPERATION = ("00000000-0000-4000-8000-000000000859",
             "00000000-0000-4000-8000-000000000860",
             "00000000-0000-4000-8000-000000000861")
PREFIX = "OWNED_EX_DEADLINE_"


def forbidden(*_args, **_kwargs):
    pytest.fail("retired/cache-only owner reached a forbidden consumer")


def same_error(call, first):
    with pytest.raises(type(first)) as caught:
        call()
    assert caught.value is first


def owned_error(call, suffix):
    with pytest.raises(owned.OwnedExDeadlineError) as caught:
        call()
    assert caught.value.code == PREFIX + suffix
    return caught.value


def legal_handle_return(actual):
    # Validate real CloseHandle before fake results or fixture recovery counts.
    assert type(actual) is int and -(1 << 31) <= actual < (1 << 31) and actual != 0
    return actual


def facts(value, *, phase="retired", entered=True, exited=True, failed=False, normal=False):
    assert type(value) is owned.LockStreamCloseReceiptV1
    assert type(value.format_version) is int and value.format_version == 1
    assert value.kind == "lock_stream" and value.scope == "native_owner_close_facts_only"
    assert value.phase == phase
    for key, expected in (("enter_returned", entered), ("exit_attempted", exited),
                          ("failed", failed), ("normal_exit", normal), ("directory_borrowed", True)):
        assert type(getattr(value, key)) is bool and getattr(value, key) is expected
    assert type(value.root_ordinal) is int and value.root_ordinal == 0
    with pytest.raises(FrozenInstanceError):
        value.phase = "changed"
    assert not hasattr(value, "__dict__")
    assert not ({item.name for item in fields(value)} &
                {"path", "handle", "fd", "directory", "stream", "callback", "exception",
                 "source_closed", "owned_outer_ex_closed", "writer_authority"})


def fully_closed(value, *, normal=True, failed=False):
    facts(value, normal=normal, failed=failed)
    assert value.handle_state == value.transfer_state == "transferred"
    assert value.fd_state == value.raw_state == value.buffer_state == "closed"
    assert value.fd_close_owner == "numeric_owner"
    assert type(value.native_file_identity) is tuple and len(value.native_file_identity) == 3
    assert all(type(item) is int and 0 <= item < (1 << 32) for item in value.native_file_identity)


class Scalar:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


@pytest.fixture
def actual_lock():
    """Short new private roots, actual initializer and exact borrowed Directory."""
    qa = Path(os.environ["RENO_QA_ROOT"]).resolve(strict=True)
    selected = Path(tempfile.gettempdir()).resolve(strict=True)
    selected.relative_to(qa)
    assert selected.is_dir() and lifecycle.current_guard() is None
    with tempfile.TemporaryDirectory(prefix="x", dir=selected) as text:
        root = Path(text)
        profile = lifecycle.StorageProfile(root / "d/r.db", root / "m", root / "r", root / "c")
        result = lifecycle.initialize(profile)
        assert result["created_locks"] == len(profile.roots)
        limited = deadline._RestrictedBudget(deadline.Namespace3Deadline(1.0e15))
        parent, token = limited.requested_deadline, limited.effective_deadline
        assert parent is not token and token.absolute_monotonic < parent.absolute_monotonic
        assert 0.0 < token.absolute_monotonic - deadline._now() <= 30.0
        scope = object()
        binding = owned._new_owned_ex_binding(OPERATION, parent_token=parent,
                                              effective_token=token, scope_identity=scope)
        with safe._Directory(profile.roots[0], create=False, base=profile.roots[0]) as directory:
            assert type(directory) is safe._Directory and directory.handles and not directory._known_closed
            yield SimpleNamespace(profile=profile, directory=directory, parent=parent, token=token,
                                  scope=scope, binding=binding)
            # Fixture still owns this directory after every stream test.
            assert directory._known_closed is False and directory.handles
            directory.verify()
            assert directory.entry_stat(lifecycle.LOCK_NAME).st_size == len(lifecycle._MARKER)
        limited.clock_only()
    assert lifecycle.current_guard() is None


def lease(item, *, binding=None, ordinal=0):
    return owned.LockStreamDeadlineLease(item.directory,
        binding=item.binding if binding is None else binding, root_ordinal=ordinal)


def run(selected, first=None):
    with selected:
        if first is not None:
            raise first


def traps(patch):
    for name in ("_monotonic", "_create_file", "_get_info", "_open_osfhandle", "_close_handle",
                 "_file_io", "_buffered_reader", "_retire_buffer", "_retire_raw", "_raw_fileno",
                 "_raw_closefd", "_raw_closed", "_buffer_raw", "_close_fd"):
        patch.setattr(owned, name, forbidden)


def test_actual_exact_original_lock_dispatch_acquire_release_and_sole_fd(actual_lock, monkeypatch):
    item, calls, order, flags, identities = actual_lock, Counter(), [], [], []
    selected = lease(item)
    initial = selected.close_receipt
    facts(initial, phase="new", entered=False, exited=False)
    real_create, real_info = owned._create_file, owned._get_info
    real_buffer, real_raw = owned._retire_buffer, owned._retire_raw
    real_fd, real_handle = owned._close_fd, owned._close_handle

    def create(*args):
        flags.append(args)
        return real_create(*args)

    def info(handle, pointer):
        result = legal_handle_return(real_info(handle, pointer))
        value = ctypes.cast(pointer, ctypes.POINTER(safe._HandleInfo)).contents
        identities.append((value.volume, value.index_high, value.index_low))
        return result

    def buffer_close(value):
        order.append("buffer")
        return real_buffer(value)

    def raw_close(value):
        order.append("raw")
        return real_raw(value)

    def fd_close(value):
        calls["fd"] += 1
        order.append("fd")
        assert real_fd(value) is None

    def handle_close(value):
        calls["handle"] += 1
        return legal_handle_return(real_handle(value))

    for name, value in (("_create_file", create), ("_get_info", info),
                        ("_retire_buffer", buffer_close), ("_retire_raw", raw_close),
                        ("_close_fd", fd_close), ("_close_handle", handle_close)):
        monkeypatch.setattr(owned, name, value)
    with selected as stream:
        assert type(stream) is io.BufferedReader and type(stream.raw) is io.FileIO
        raw = stream.raw
        assert raw.closefd is False and raw.fileno() == stream.fileno()
        facts(selected.close_receipt, phase="active", exited=False)
        lock = lifecycle._FileLock(item.directory, stream)
        assert lifecycle._original_lock_dispatch((lock,)) is True
        assert lock.verify() is None
        assert lifecycle._verify_lock_group((lock,)) is None
        assert lock.attempt(True) is True and lock.locked is True
        try:
            assert lock.verify() is None and lifecycle._original_lock_dispatch((lock,)) is True
        finally:
            assert lock.release() is None
        assert lock.locked is False and lifecycle._original_lock_dispatch((lock,)) is True
        assert os.lseek(stream.fileno(), 0, os.SEEK_SET) == 0
        assert stream.read() == lifecycle._MARKER
    receipt = selected.close_receipt
    fully_closed(receipt)
    assert stream.closed is raw.closed is True
    assert calls == {"fd": 1} and order[0] == "buffer" and order[-1] == "fd"
    assert order.count("raw") <= 1 and order.count("buffer") == order.count("fd") == 1
    assert len(flags) == len(identities) == 1
    args = flags[0]
    assert args[0] == safe._native_path(item.directory.path / lifecycle.LOCK_NAME)
    assert args[1:] == (0x80000000, 3, None, 3, 0x00200000, None)
    assert receipt.native_file_identity == identities[0] and initial.phase == "new"
    owned_error(lambda: selected.__exit__(None, None, None), "CLOSED")
    assert selected.close_receipt == receipt
    assert calls == {"fd": 1}


def test_actual_cached_receipt_and_binding_getters_do_not_reach_native_or_directory(actual_lock, monkeypatch):
    item, selected = actual_lock, lease(actual_lock)
    run(selected)
    original = selected.close_receipt
    fully_closed(original)
    with monkeypatch.context() as patch:
        traps(patch)
        patch.setattr(item.directory, "verify", forbidden)
        patch.setattr(item.directory, "entry_stat", forbidden)
        for _ in range(2):
            assert selected.close_receipt == original
            assert item.binding.operation == OPERATION
            assert item.binding.parent_token is item.parent
            assert item.binding.effective_token is item.token
            assert item.binding.scope_identity is item.scope
        owned_error(lambda: selected.__exit__(None, None, None), "CLOSED")
    assert item.directory._known_closed is False


@pytest.mark.parametrize("api, reported", [
    ("_retire_buffer", False), ("_retire_buffer", 0), ("_retire_buffer", True),
    ("_close_fd", False), ("_close_fd", 0), ("_close_fd", True),
], ids=["buffer-false", "buffer-zero", "buffer-true", "fd-false", "fd-zero", "fd-true"])
def test_controlled_non_none_cleanup_return_remains_unknown_and_no_retry(actual_lock, monkeypatch, api, reported):
    selected = lease(actual_lock)
    original, attempts = getattr(owned, api), []

    def close(value):
        attempts.append(value)
        assert original(value) is None
        return reported

    monkeypatch.setattr(owned, api, close)
    first = owned_error(lambda: run(selected), "INVALID_RESULT")
    receipt = selected.close_receipt
    facts(receipt, failed=True)
    assert receipt.handle_state == receipt.transfer_state == "transferred"
    assert receipt.raw_state == "closed"
    assert receipt.buffer_state == ("unknown" if api == "_retire_buffer" else "closed")
    assert receipt.fd_state == ("unknown" if api == "_close_fd" else "closed")
    same_error(lambda: selected.__exit__(None, None, None), first)
    assert len(attempts) == 1 and selected.close_receipt == receipt


@pytest.mark.parametrize("reported", [False, 0, True], ids=["false", "zero", "true"])
def test_controlled_buffer_first_failure_still_retires_raw_and_fd_once(actual_lock, monkeypatch, reported):
    selected, first, calls, returned_stream = lease(actual_lock), OSError("buffer before close"), Counter(), []
    real_buffered, real_raw, real_fd = owned._buffered_reader, owned._retire_raw, owned._close_fd

    def buffered(*args, **kwargs):
        result = real_buffered(*args, **kwargs)
        returned_stream.append(result)
        return result

    def buffer_close(_value):
        calls["buffer"] += 1
        raise first

    def raw_close(value):
        calls["raw"] += 1
        assert real_raw(value) is None
        return reported

    def fd_close(value):
        calls["fd"] += 1
        return real_fd(value)

    monkeypatch.setattr(owned, "_buffered_reader", buffered)
    monkeypatch.setattr(owned, "_retire_buffer", buffer_close)
    monkeypatch.setattr(owned, "_retire_raw", raw_close)
    monkeypatch.setattr(owned, "_close_fd", fd_close)
    try:
        same_error(lambda: run(selected), first)
        receipt = selected.close_receipt
        facts(receipt, failed=True)
        assert receipt.buffer_state == receipt.raw_state == "unknown" and receipt.fd_state == "closed"
        assert calls == {"buffer": 1, "raw": 1, "fd": 1}
        same_error(lambda: selected.__exit__(None, None, None), first)
        assert selected.close_receipt == receipt and calls == {"buffer": 1, "raw": 1, "fd": 1}
    finally:
        # Known closefd=False wrapper; no native/numeric recovery or UNKNOWN probe.
        for stream in returned_stream:
            assert io.BufferedReader.close(stream) is None


@pytest.mark.parametrize("reported", [None, False, True, 0, 1.0, 1 << 31],
                         ids=["none", "false", "true", "zero", "float", "out-of-signed32"])
def test_controlled_real_handle_close_then_illegal_return_preserves_enter_first(actual_lock, monkeypatch, reported):
    selected, first, calls = lease(actual_lock), OSError("GetInfo actual postreturn"), Counter()
    real_info, real_close = owned._get_info, owned._close_handle

    def info(*args):
        legal_handle_return(real_info(*args))
        raise first

    def close(handle):
        legal_handle_return(real_close(handle))
        calls["handle"] += 1
        return reported

    monkeypatch.setattr(owned, "_get_info", info)
    monkeypatch.setattr(owned, "_close_handle", close)
    monkeypatch.setattr(owned, "_close_fd", forbidden)
    same_error(selected.__enter__, first)
    receipt = selected.close_receipt
    facts(receipt, entered=False, failed=True)
    assert receipt.handle_state == "unknown" and receipt.transfer_state == "not_started"
    assert receipt.fd_state == receipt.raw_state == receipt.buffer_state == "empty"
    same_error(lambda: selected.__exit__(None, None, None), first)
    assert calls == {"handle": 1} and selected.close_receipt == receipt


@pytest.mark.parametrize("reported", [1, -1], ids=["positive", "negative-signed32"])
def test_controlled_legal_handle_return_keeps_physical_closed_despite_enter_first(actual_lock, monkeypatch, reported):
    selected, first, calls = lease(actual_lock), OSError("query first"), Counter()
    real_info, real_close = owned._get_info, owned._close_handle

    def info(*args):
        legal_handle_return(real_info(*args))
        raise first

    def close(handle):
        legal_handle_return(real_close(handle))
        calls["handle"] += 1
        return reported

    monkeypatch.setattr(owned, "_get_info", info)
    monkeypatch.setattr(owned, "_close_handle", close)
    same_error(selected.__enter__, first)
    receipt = selected.close_receipt
    facts(receipt, entered=False, failed=True)
    assert receipt.handle_state == "closed" and receipt.fd_state == "empty"
    same_error(lambda: selected.__exit__(None, None, None), first)
    assert calls == {"handle": 1} and selected.close_receipt == receipt


@pytest.mark.parametrize("stage", ["before-crt", "after-crt", "none", "bool", "negative"])
def test_controlled_transfer_unknown_never_probed_and_only_fixture_witness_recovered(actual_lock, monkeypatch, stage):
    selected, first, witness, recovery = lease(actual_lock), OSError("opaque transfer"), {}, Counter()
    real_create, real_transfer = owned._create_file, owned._open_osfhandle
    real_handle, real_fd = owned._close_handle, owned._close_fd

    def create(*args):
        handle = real_create(*args)
        assert type(handle) is int and handle >= 0 and handle != ctypes.c_void_p(-1).value
        witness["handle"] = handle
        return handle

    def transfer(handle, flags):
        assert handle == witness["handle"]
        if stage != "before-crt":
            actual = real_transfer(handle, flags)
            assert type(actual) is int and actual >= 0
            witness["fd"] = actual
        if stage in {"before-crt", "after-crt"}:
            raise first
        return {"none": None, "bool": True, "negative": -1}[stage]

    monkeypatch.setattr(owned, "_create_file", create)
    monkeypatch.setattr(owned, "_open_osfhandle", transfer)
    monkeypatch.setattr(owned, "_close_handle", forbidden)
    monkeypatch.setattr(owned, "_close_fd", forbidden)
    monkeypatch.setattr(owned, "_file_io", forbidden)
    try:
        if stage in {"before-crt", "after-crt"}:
            same_error(selected.__enter__, first)
        else:
            first = owned_error(selected.__enter__, "INVALID_RESULT")
        receipt = selected.close_receipt
        facts(receipt, entered=False, failed=True)
        assert receipt.handle_state == receipt.transfer_state == "unknown"
        assert receipt.fd_state == receipt.raw_state == receipt.buffer_state == "empty"
        same_error(lambda: selected.__exit__(None, None, None), first)
        with monkeypatch.context() as patch:
            traps(patch)
            assert selected.close_receipt == receipt
        assert recovery == {}
    finally:
        # Separate known fixture call/return records choose the sole closer.
        # No fstat/get_osfhandle/closed/fileno probe and no subject receipt upgrade.
        if "fd" in witness:
            assert real_fd(witness["fd"]) is None
            recovery["fd"] += 1
        elif "handle" in witness:
            legal_handle_return(real_handle(witness["handle"]))
            recovery["handle"] += 1
    assert recovery == ({"handle": 1} if stage == "before-crt" else {"fd": 1})
    assert selected.close_receipt == receipt


def test_controlled_foreign_closed_raw_has_no_getter_or_closer_and_fd_still_closes(actual_lock, monkeypatch):
    selected, calls = lease(actual_lock), Counter()
    original = owned._close_fd

    class ForeignClosed:
        @property
        def closed(self):
            calls["alien_closed"] += 1
            return True

        @property
        def closefd(self):
            calls["alien_closefd"] += 1
            return False

        def close(self):
            calls["alien_close"] += 1
            pytest.fail("alien raw must not be closed")

        def fileno(self):
            calls["alien_fileno"] += 1
            pytest.fail("alien raw must not be queried")

    def close(fd):
        assert original(fd) is None
        calls["fd"] += 1

    monkeypatch.setattr(owned, "_file_io", lambda *_args, **_kwargs: ForeignClosed())
    monkeypatch.setattr(owned, "_close_fd", close)
    first = owned_error(selected.__enter__, "INVALID_RESULT")
    receipt = selected.close_receipt
    facts(receipt, entered=False, failed=True)
    assert receipt.raw_state == "unknown" and receipt.fd_state == "closed"
    assert receipt.buffer_state == "empty" and receipt.handle_state == "transferred"
    same_error(lambda: selected.__exit__(None, None, None), first)
    assert calls == {"fd": 1} and selected.close_receipt == receipt


@pytest.mark.parametrize("kind", ["foreign-buffer", "wrong-buffer-raw"])
def test_controlled_wrong_buffer_never_closes_alien_and_known_raw_fd_cleanup_continues(actual_lock, monkeypatch, kind):
    selected, calls = lease(actual_lock), Counter()
    actual_raw, fixture_buffer, fixture_raw = [], [], []
    real_file, real_fd = owned._file_io, owned._close_fd

    class ForeignBuffer:
        @property
        def raw(self):
            calls["alien_raw"] += 1
            pytest.fail("alien buffer raw must not be queried")

        @property
        def closed(self):
            calls["alien_closed"] += 1
            return True

        def close(self):
            calls["alien_close"] += 1
            pytest.fail("alien buffer must not be closed")

    def file(*args, **kwargs):
        value = real_file(*args, **kwargs)
        actual_raw.append(value)
        return value

    def buffered(raw):
        if kind == "foreign-buffer":
            return ForeignBuffer()
        # A separate fixture-only buffer/raw over a separate actual descriptor.
        alternate = open(safe._native_path(actual_lock.directory.path / lifecycle.LOCK_NAME), "rb")
        fixture_buffer.append(alternate)
        fixture_raw.append(alternate.raw)
        assert alternate.raw is not raw
        return alternate

    def close(fd):
        assert real_fd(fd) is None
        calls["fd"] += 1

    monkeypatch.setattr(owned, "_file_io", file)
    monkeypatch.setattr(owned, "_buffered_reader", buffered)
    monkeypatch.setattr(owned, "_close_fd", close)
    try:
        first = owned_error(selected.__enter__, "INVALID_RESULT")
        receipt = selected.close_receipt
        facts(receipt, entered=False, failed=True)
        assert receipt.buffer_state == "unknown" and receipt.raw_state == receipt.fd_state == "closed"
        assert len(actual_raw) == 1 and actual_raw[0].closed is True
        for stream, raw in zip(fixture_buffer, fixture_raw):
            assert stream.closed is raw.closed is False
        same_error(lambda: selected.__exit__(None, None, None), first)
        assert calls == {"fd": 1} and selected.close_receipt == receipt
    finally:
        for stream in fixture_buffer:
            assert io.BufferedReader.close(stream) is None


def test_controlled_exact_raw_closefd_true_never_double_closes_numeric_fd(actual_lock, monkeypatch):
    selected, calls, returned_raw = lease(actual_lock), Counter(), []

    def file(fd, *_args, **_kwargs):
        value = io.FileIO(fd, "rb", closefd=True)
        returned_raw.append(value)
        return value

    real_raw = owned._retire_raw

    def close_raw(value):
        calls["raw"] += 1
        return real_raw(value)

    monkeypatch.setattr(owned, "_file_io", file)
    monkeypatch.setattr(owned, "_retire_raw", close_raw)
    monkeypatch.setattr(owned, "_close_fd", forbidden)
    monkeypatch.setattr(owned, "_close_handle", forbidden)
    first = owned_error(selected.__enter__, "INVALID_RESULT")
    receipt = selected.close_receipt
    facts(receipt, entered=False, failed=True)
    assert receipt.fd_close_owner == "unknown_wrapper" and receipt.fd_state == "unknown"
    assert receipt.raw_state == "closed" and receipt.buffer_state == "empty"
    assert returned_raw[0].closed is True and calls == {"raw": 1}
    same_error(lambda: selected.__exit__(None, None, None), first)
    assert calls == {"raw": 1} and selected.close_receipt == receipt


def test_controlled_wrong_exact_raw_fd_retained_without_retiring_alien_file(actual_lock, monkeypatch):
    selected, calls, fixture_raw = lease(actual_lock), Counter(), []
    real_fd = owned._close_fd

    def file(fd, *_args, **_kwargs):
        # Fixture owns this separate actual file/FD and keeps a strong reference.
        value = io.FileIO(safe._native_path(actual_lock.directory.path / lifecycle.LOCK_NAME), "rb")
        assert value.fileno() != fd and value.closefd is True
        fixture_raw.append(value)
        return value

    def close(fd):
        assert real_fd(fd) is None
        calls["fd"] += 1

    monkeypatch.setattr(owned, "_file_io", file)
    monkeypatch.setattr(owned, "_retire_raw", forbidden)
    monkeypatch.setattr(owned, "_close_fd", close)
    try:
        first = owned_error(selected.__enter__, "INVALID_RESULT")
        receipt = selected.close_receipt
        facts(receipt, entered=False, failed=True)
        assert receipt.raw_state == "unknown" and receipt.fd_state == "closed"
        assert receipt.buffer_state == "empty" and receipt.handle_state == "transferred"
        assert len(fixture_raw) == 1 and fixture_raw[0].closed is False
        # Read only the independent actual fixture witness, never a subject UNKNOWN number.
        assert fixture_raw[0].read() == lifecycle._MARKER
        same_error(lambda: selected.__exit__(None, None, None), first)
        assert calls == {"fd": 1} and selected.close_receipt == receipt
    finally:
        for raw in fixture_raw:
            assert io.FileIO.close(raw) is None


@pytest.mark.parametrize("kind", ["ordinary", "control", "synthetic-memory"])
def test_controlled_first_body_or_control_survives_all_cleanup_and_fd_alias_fault(actual_lock, monkeypatch, kind):
    selected, calls = lease(actual_lock), Counter()
    first = {"ordinary": RuntimeError("body first"), "control": SystemExit(859),
             "synthetic-memory": MemoryError("synthetic body first")}[kind]
    real_fd = owned._close_fd

    def close(fd):
        assert real_fd(fd) is None
        calls["fd"] += 1
        raise OSError("actual FD close postreturn")

    monkeypatch.setattr(owned, "_close_fd", close)
    same_error(lambda: run(selected, first), first)
    receipt = selected.close_receipt
    facts(receipt, failed=True)
    assert receipt.buffer_state == receipt.raw_state == "closed" and receipt.fd_state == "unknown"
    same_error(lambda: selected.__exit__(None, None, None), first)
    assert calls == {"fd": 1} and selected.close_receipt == receipt


@pytest.mark.parametrize("stage", ["create-return", "last-fd-close"])
def test_controlled_postreturn_expiry_preserves_physical_facts_and_blocks_delivery(actual_lock, monkeypatch, stage):
    item, calls = actual_lock, Counter()
    selected = lease(item)
    scalar = Scalar(owned._monotonic())
    api = "_create_file" if stage == "create-return" else "_close_fd"
    original = getattr(owned, api)

    def returned(*args):
        result = original(*args)
        calls[api] += 1
        scalar.value = item.token.absolute_monotonic
        return result

    monkeypatch.setattr(owned, "_monotonic", scalar)
    monkeypatch.setattr(owned, api, returned)
    first = owned_error(selected.__enter__ if stage == "create-return" else lambda: run(selected), "DEADLINE")
    receipt = selected.close_receipt
    facts(receipt, entered=stage != "create-return", failed=True)
    if stage == "create-return":
        assert receipt.handle_state == "closed"
        assert receipt.fd_state == receipt.raw_state == receipt.buffer_state == "empty"
    else:
        fully_closed(receipt, normal=False, failed=True)
    same_error(lambda: selected.__exit__(None, None, None), first)
    assert calls == {api: 1} and selected.close_receipt == receipt


def test_controlled_create_first_failure_is_sticky_empty_and_getter_cached(actual_lock, monkeypatch):
    selected, first, attempts = lease(actual_lock), SystemExit(859), []

    def create(*args):
        attempts.append(args)
        raise first

    monkeypatch.setattr(owned, "_create_file", create)
    monkeypatch.setattr(owned, "_close_fd", forbidden)
    monkeypatch.setattr(owned, "_close_handle", forbidden)
    same_error(selected.__enter__, first)
    receipt = selected.close_receipt
    facts(receipt, entered=False, failed=True)
    assert receipt.handle_state == "unknown"
    assert receipt.fd_state == receipt.raw_state == receipt.buffer_state == "empty"
    same_error(selected.__enter__, first)
    same_error(lambda: selected.__exit__(None, None, None), first)
    with monkeypatch.context() as patch:
        traps(patch)
        assert selected.close_receipt == receipt
    assert len(attempts) == 1


@pytest.mark.parametrize("stage, kind", [("before-borrow", "ordinary"), ("before-borrow", "synthetic-memory"),
                                      ("after-borrow", "ordinary"), ("after-borrow", "synthetic-memory")],
                         ids=["before-oserror", "before-memory", "after-oserror", "after-memory"])
def test_controlled_fileio_borrow_constructor_failure_keeps_known_fd_obligation(actual_lock, monkeypatch, stage, kind):
    selected, calls, fixture_raw = lease(actual_lock), Counter(), []
    first = OSError("borrow constructor first") if kind == "ordinary" else MemoryError("synthetic borrow first")
    real_file, real_fd = owned._file_io, owned._close_fd

    def file(fd, *args, **kwargs):
        calls["borrow"] += 1
        assert kwargs.get("closefd") is False
        if stage == "after-borrow":
            # This is the actual explicitly borrowing constructor, not a transfer.
            value = real_file(fd, *args, **kwargs)
            assert type(value) is io.FileIO and value.closefd is False
            fixture_raw.append(value)
        raise first

    def close(fd):
        assert real_fd(fd) is None
        calls["fd"] += 1

    monkeypatch.setattr(owned, "_file_io", file)
    monkeypatch.setattr(owned, "_close_fd", close)
    monkeypatch.setattr(owned, "_close_handle", forbidden)
    monkeypatch.setattr(owned, "_retire_raw", forbidden)
    monkeypatch.setattr(owned, "_buffered_reader", forbidden)
    try:
        same_error(selected.__enter__, first)
        receipt = selected.close_receipt
        facts(receipt, entered=False, failed=True)
        assert receipt.handle_state == receipt.transfer_state == "transferred"
        assert receipt.raw_state == "unknown" and receipt.buffer_state == "empty"
        assert receipt.fd_state == "closed" and receipt.fd_close_owner == "numeric_owner"
        assert calls == {"borrow": 1, "fd": 1}
        same_error(selected.__enter__, first)
        same_error(lambda: selected.__exit__(None, None, None), first)
        with monkeypatch.context() as patch:
            traps(patch)
            assert selected.close_receipt == receipt
        assert calls == {"borrow": 1, "fd": 1}
        assert len(fixture_raw) == (1 if stage == "after-borrow" else 0)
    finally:
        # Fixture has a known actual closefd=False wrapper only. Its own wrapper
        # close never retries/probes a subject UNKNOWN native or numeric value.
        for raw in fixture_raw:
            assert io.FileIO.close(raw) is None


@pytest.mark.parametrize("kind", ["new", "retired", "failed-enter"])
def test_duplicate_root_ordinal_stays_consumed_before_native_creation(actual_lock, monkeypatch, kind):
    item, selected = actual_lock, lease(actual_lock)
    if kind == "retired":
        run(selected)
    elif kind == "failed-enter":
        first = OSError("fixture CreateFile boundary")

        def create(*_args):
            raise first

        with monkeypatch.context() as patch:
            patch.setattr(owned, "_create_file", create)
            same_error(selected.__enter__, first)
    prior = selected.close_receipt
    with monkeypatch.context() as patch:
        patch.setattr(owned, "_create_file", forbidden)
        owned_error(lambda: lease(item), "CLOSED")
    assert selected.close_receipt == prior


def test_duplicate_actual_owner_init_cannot_reset_active_slots_or_consume_other_root(actual_lock, monkeypatch):
    item, selected = actual_lock, lease(actual_lock)
    with selected as stream:
        fd, prior = stream.fileno(), selected.close_receipt
        with monkeypatch.context() as patch:
            patch.setattr(owned, "_create_file", forbidden)
            owned_error(lambda: owned.LockStreamDeadlineLease.__init__(selected, item.directory,
                binding=item.binding, root_ordinal=1), "CLOSED")
        assert stream.fileno() == fd and selected.close_receipt == prior
        assert stream.raw.closefd is False
    fully_closed(selected.close_receipt)
    # Rejected re-init has no authority to take another declared ordinal.
    second = lease(item, ordinal=1)
    run(second)
    receipt = second.close_receipt
    assert type(receipt.root_ordinal) is int and receipt.root_ordinal == 1
    assert receipt.fd_state == receipt.raw_state == receipt.buffer_state == "closed"
    assert receipt.normal_exit is True


@pytest.mark.parametrize("kind", ["operation-list", "operation-upper", "operation-invalid", "parent-dto",
                                  "effective-dto", "widened", "unclipped", "scope-dto"])
def test_binding_rejects_noncanonical_or_invalid_lineage_before_any_native(actual_lock, monkeypatch, kind):
    item = actual_lock
    operation, parent, effective, scope = OPERATION, item.parent, item.token, object()
    if kind == "operation-list":
        operation = list(OPERATION)
    elif kind == "operation-upper":
        operation = ("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa".upper(), OPERATION[1], OPERATION[2])
    elif kind == "operation-invalid":
        operation = ("not-a-uuid", OPERATION[1], OPERATION[2])
    elif kind == "parent-dto":
        parent = SimpleNamespace(absolute_monotonic=item.parent.absolute_monotonic)
    elif kind == "effective-dto":
        effective = SimpleNamespace(absolute_monotonic=item.token.absolute_monotonic)
    elif kind == "widened":
        parent = deadline.Namespace3Deadline(item.token.absolute_monotonic - 1.0)
    elif kind == "unclipped":
        effective = parent
    elif kind == "scope-dto":
        scope = SimpleNamespace()
    with monkeypatch.context() as patch:
        patch.setattr(owned, "_create_file", forbidden)
        owned_error(lambda: owned._new_owned_ex_binding(operation, parent_token=parent,
                    effective_token=effective, scope_identity=scope), "INVALID_ARGUMENT")


def test_binding_retains_actual_token_references_and_independent_scope_root_registry(actual_lock):
    item = actual_lock
    equal_parent = deadline.Namespace3Deadline(item.parent.absolute_monotonic)
    equal_effective = deadline.Namespace3Deadline(item.token.absolute_monotonic)
    assert equal_parent == item.parent and equal_parent is not item.parent
    assert equal_effective == item.token and equal_effective is not item.token
    scope = object()
    second = owned._new_owned_ex_binding(OPERATION, parent_token=equal_parent,
        effective_token=equal_effective, scope_identity=scope)
    assert item.binding.parent_token is item.parent and item.binding.effective_token is item.token
    assert second.parent_token is equal_parent and second.effective_token is equal_effective
    assert second.scope_identity is scope and second.scope_identity is not item.scope
    first_lease, second_lease = lease(item), lease(item, binding=second)
    run(first_lease)
    run(second_lease)
    fully_closed(first_lease.close_receipt)
    fully_closed(second_lease.close_receipt)
    # Each factory has its own actual root registrations; neither is a DTO grant.
    owned_error(lambda: lease(item), "CLOSED")
    owned_error(lambda: lease(item, binding=second), "CLOSED")


@pytest.mark.parametrize("ordinal", [True, -1, 4, 0.0], ids=["bool", "negative", "beyond-four", "float"])
def test_illegal_root_ordinal_rejected_without_consuming_valid_root(actual_lock, monkeypatch, ordinal):
    with monkeypatch.context() as patch:
        patch.setattr(owned, "_create_file", forbidden)
        owned_error(lambda: lease(actual_lock, ordinal=ordinal), "INVALID_ARGUMENT")
    selected = lease(actual_lock)
    run(selected)
    fully_closed(selected.close_receipt)
