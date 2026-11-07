"""Actual owners plus finite consumer-local faults for cached close facts.

Faults first release known fixture resources where possible, then substitute
an ABI result. This never turns subject UNKNOWN into a native-close proof.
No receipt here grants source_closed, SQL COMMIT, or writer permission.
"""
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, fields
import ctypes
import io
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace

import pytest

from reno import personal_authority_directory_deadline as directories
from reno import personal_authority_namespace3_deadline as deadline
from reno import personal_authority_source_deadline as source
from reno import personal_authority_committed_image_deadline as images
from reno import storage_lifecycle as lifecycle


pytestmark = pytest.mark.skipif(os.name != "nt", reason="actual Windows HANDLE/CRT owner fixtures")
NODE = "node-identity.json"
PAYLOAD = "绍兴\x00装修关闭回执".encode() + b"EOF-sentinel"
SCOPE = "native_owner_close_facts_only"
COMMON = {"format_version", "kind", "scope", "phase", "enter_returned", "exit_attempted", "failed", "normal_exit"}
SCHEMAS = {
    "directory": (directories.DirectoryCloseReceiptV1,
        {"parent_borrowed", "handle_states", "handle_native_identities"}),
    "readonly": (directories.ReadonlyCloseReceiptV1,
        {"directory_borrowed", "handle_state", "fd_state", "raw_state", "buffer_state", "transfer_state", "native_file_identity"}),
    "node_stream": (source.NodeStreamCloseReceiptV1, {"directory_borrowed", "readonly_close_receipt"}),
    "held_file": (source.HeldFileCloseReceiptV1, {"directory_borrowed", "stream_close_receipt"}),
}


def native(path):
    value = os.path.abspath(os.fspath(path))
    if value.startswith("\\\\?\\"):
        return value
    return "\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value


def budget():
    return deadline._RestrictedBudget(deadline.Namespace3Deadline(deadline.time.monotonic() + 30.0))


def qa_temp():
    qa = Path(os.environ["RENO_QA_ROOT"]).resolve(strict=True)
    selected = Path(tempfile.gettempdir()).resolve(strict=True)
    selected.relative_to(qa)
    assert selected.is_dir()


def forbidden(*args, **kwargs):
    raise AssertionError("cached receipt reached a clock, lower provider, or physical consumer")


def same_error(call, original):
    with pytest.raises(type(original)) as raised:
        call()
    assert raised.value is original


def directory_error(call, suffix):
    with pytest.raises(directories.DirectoryDeadlineError) as raised:
        call()
    assert raised.value.code == "DIRECTORY_DEADLINE_" + suffix
    return raised.value


def common(receipt, kind, *, phase="retired", normal=False, failed=False, entered=True, exited=True):
    cls, extra = SCHEMAS[kind]
    assert type(receipt) is cls and {field.name for field in fields(receipt)} == COMMON | extra
    assert type(receipt.format_version) is int and receipt.format_version == 1
    assert receipt.kind == kind and receipt.scope == SCOPE and receipt.phase == phase
    for name, expected in {"normal_exit": normal, "failed": failed,
                           "enter_returned": entered, "exit_attempted": exited}.items():
        assert type(getattr(receipt, name)) is bool and getattr(receipt, name) is expected
    borrowed = "parent_borrowed" if kind == "directory" else "directory_borrowed"
    assert type(getattr(receipt, borrowed)) is bool


def identity(value):
    assert type(value) is tuple and len(value) == 3
    assert all(type(item) is int and 0 <= item < (1 << 32) for item in value)


def directory_closed(receipt, *, normal=True, failed=False):
    common(receipt, "directory", normal=normal, failed=failed)
    assert type(receipt.handle_states) is type(receipt.handle_native_identities) is tuple
    assert receipt.handle_states and all(state == "closed" for state in receipt.handle_states)
    assert len(receipt.handle_states) == len(receipt.handle_native_identities)
    for value in receipt.handle_native_identities:
        identity(value)


def readonly_closed(receipt, *, normal=True, failed=False):
    common(receipt, "readonly", normal=normal, failed=failed)
    assert receipt.directory_borrowed is True
    assert receipt.handle_state == receipt.transfer_state == "transferred"
    assert receipt.fd_state == receipt.raw_state == receipt.buffer_state == "closed"
    identity(receipt.native_file_identity)


@pytest.fixture
def files_env(tmp_path):
    root, deep = tmp_path / "p", tmp_path / "p/c/d"
    os.makedirs(native(deep / "e"))
    for path in (root / NODE, deep / NODE):
        with open(native(path), "wb") as stream:
            stream.write(PAYLOAD)
    return SimpleNamespace(root=root, deep=deep, payload=PAYLOAD)


def owner(env, token=None, *, deep=False):
    return directories.DeadlineDirectory(env.deep if deep else env.root, base=env.root,
        deadline_token=token if token is not None else budget().effective_deadline)


def trap_directory_consumers(patch):
    for name in ("_clock", "_create_file", "_get_info", "_close_handle", "_close_fd",
                 "_open_osfhandle", "_stat", "_fstat", "_retire_raw", "_retire_buffer",
                 "_file_io", "_buffered_reader"):
        patch.setattr(directories, name, forbidden)


def test_actual_directory_readonly_and_borrowed_parent_retired_receipts_are_cached(files_env, monkeypatch):
    env = files_env
    counts = {"handle": 0, "fd": 0}
    real_handle, real_fd = directories._close_handle, directories._close_fd
    def close_handle(value):
        counts["handle"] += 1
        returned = real_handle(value)
        assert type(returned) is int and -(1 << 31) <= returned < (1 << 31) and returned != 0
        return returned
    def close_fd(value):
        counts["fd"] += 1
        returned = real_fd(value)
        assert returned is None
        return returned
    monkeypatch.setattr(directories, "_close_handle", close_handle)
    monkeypatch.setattr(directories, "_close_fd", close_fd)
    root = owner(env, deep=True)
    initial = root.close_receipt
    common(initial, "directory", phase="new", entered=False, exited=False)
    assert initial.handle_states == ("empty",) * 3 and initial.handle_native_identities == (None,) * 3
    with root:
        active = root.close_receipt
        common(active, "directory", phase="active", exited=False)
        assert active.handle_states == ("owned",) * 3
        child = directories.DeadlineDirectory(env.deep / "e", parent=root, deadline_token=root.deadline_token)
        with child:
            assert child.close_receipt.parent_borrowed is True
        directory_closed(child.close_receipt)
        assert child.close_receipt.handle_states == ("closed",)
        root.verify()
        leases = []
        for user_closes_wrapper in (False, True):
            lease = root.open_file(NODE)
            common(lease.close_receipt, "readonly", phase="new", entered=False, exited=False)
            assert lease.close_receipt.transfer_state == "not_started"
            with lease as stream:
                assert stream.read() == env.payload and stream.read(1) == b""
                in_use = lease.close_receipt
                common(in_use, "readonly", phase="active", exited=False)
                assert in_use.handle_state == in_use.transfer_state == "transferred" and in_use.fd_state == "owned"
                if user_closes_wrapper:
                    assert stream.close() is None
            readonly_closed(lease.close_receipt)
            assert in_use.fd_state == "owned"  # Detached earlier snapshot.
            leases.append(lease)
    receipt = root.close_receipt
    directory_closed(receipt)
    assert receipt.parent_borrowed is False and active.handle_states == ("owned",) * 3
    assert counts == {"handle": 4, "fd": 2}
    snapshots = [selected.close_receipt for selected in (root, child, *leases)]
    with monkeypatch.context() as patch:
        trap_directory_consumers(patch)
        assert [selected.close_receipt for selected in (root, child, *leases)] == snapshots
        assert [selected.close_receipt for selected in (root, child, *leases)] == snapshots
    with pytest.raises(FrozenInstanceError):
        receipt.normal_exit = False
    assert Path(native(env.deep / NODE)).read_bytes() == env.payload


@pytest.mark.parametrize("reported", [-1, -(1 << 31), (1 << 31) - 1], ids=["negative", "minimum", "maximum"])
def test_controlled_signed32_nonzero_close_return_domain(files_env, monkeypatch, reported):
    selected = owner(files_env)
    real = directories._close_handle
    def close(value):
        assert real(value)
        return reported  # Exact ABI shape substitution after actual fixture close.
    monkeypatch.setattr(directories, "_close_handle", close)
    with selected:
        pass
    directory_closed(selected.close_receipt)


@pytest.mark.parametrize("reported", [False, 0], ids=["false", "zero"])
def test_controlled_false_bool_close_has_native_error_and_unknown_receipt(files_env, monkeypatch, reported):
    selected = owner(files_env)
    real, calls = directories._close_handle, []
    def close(value):
        calls.append(value)
        assert real(value)
        ctypes.set_last_error(5)
        return reported
    monkeypatch.setattr(directories, "_close_handle", close)
    selected.__enter__()
    with pytest.raises(OSError) as raised:
        selected.__exit__(None, None, None)
    assert raised.value.winerror == 5
    common(selected.close_receipt, "directory", failed=True)
    assert selected.close_receipt.handle_states == ("unknown",)
    same_error(lambda: selected.__exit__(None, None, None), raised.value)
    assert len(calls) == 1


@pytest.mark.parametrize("case", ["bool", "float", "truthy-trap", "above", "below"])
def test_controlled_illegal_handle_returns_never_coerce_truthiness_or_report_closed(files_env, monkeypatch, case):
    class TruthyTrap:
        def __bool__(self):
            raise AssertionError("BOOL validation coerced a foreign result")
    result = {"bool": True, "float": 1.0, "truthy-trap": TruthyTrap(),
              "above": 1 << 31, "below": -(1 << 31) - 1}[case]
    selected, real, calls = owner(files_env), directories._close_handle, []
    def close(value):
        calls.append(value)
        assert real(value)
        return result
    monkeypatch.setattr(directories, "_close_handle", close)
    selected.__enter__()
    failure = directory_error(lambda: selected.__exit__(None, None, None), "INVALID_RESULT")
    common(selected.close_receipt, "directory", failed=True)
    assert selected.close_receipt.handle_states == ("unknown",)
    same_error(lambda: selected.__exit__(None, None, None), failure)
    assert len(calls) == 1


@pytest.mark.parametrize("kind", ["fd", "buffer", "raw"])
def test_controlled_non_none_retirement_return_does_not_become_closed(files_env, monkeypatch, kind):
    first = RuntimeError("synthetic buffer failure before raw retirement")
    alias = {"fd": "_close_fd", "buffer": "_retire_buffer", "raw": "_retire_raw"}[kind]
    real, calls = getattr(directories, alias), []
    with owner(files_env) as root:
        lease = root.open_file(NODE)
        lease.__enter__()
        def close(value):
            calls.append(value)
            assert real(value) is None
            return 0
        with monkeypatch.context() as patch:
            patch.setattr(directories, alias, close)
            if kind == "raw":
                def refused(_buffer):
                    raise first
                patch.setattr(directories, "_retire_buffer", refused)
                same_error(lambda: lease.__exit__(None, None, None), first)
            else:
                first = directory_error(lambda: lease.__exit__(None, None, None), "INVALID_RESULT")
            receipt = lease.close_receipt
            common(receipt, "readonly", failed=True)
            assert getattr(receipt, kind + "_state") == "unknown"
            assert receipt.fd_state == ("unknown" if kind == "fd" else "closed")
            same_error(lambda: lease.__exit__(None, None, None), first)
            assert len(calls) == 1


def test_controlled_first_handle_failure_captures_error_before_exhausting_other_pins(files_env, monkeypatch):
    selected = owner(files_env, deep=True)
    selected.__enter__()
    native_ids = selected.close_receipt.handle_native_identities
    real, calls, events = directories._close_handle, [], []
    later = RuntimeError("synthetic later native close result")
    actual_ctypes = directories.ctypes
    class CtypesFacade:
        def __getattr__(self, name):
            return getattr(actual_ctypes, name)
        def get_last_error(self):
            events.append("last-error")
            return actual_ctypes.get_last_error()
    def close(value):
        calls.append(value)
        events.append("close")
        assert real(value)
        if len(calls) == 1:
            ctypes.set_last_error(5)
            return 0
        if len(calls) == 2:
            raise later
        ctypes.set_last_error(4321)
        return 1
    monkeypatch.setattr(directories, "ctypes", CtypesFacade())
    monkeypatch.setattr(directories, "_close_handle", close)
    with pytest.raises(OSError) as raised:
        selected.__exit__(None, None, None)
    assert raised.value.winerror == 5 and events[:3] == ["close", "last-error", "close"]
    receipt = selected.close_receipt
    common(receipt, "directory", failed=True)
    assert receipt.handle_states == ("closed", "unknown", "unknown")
    assert receipt.handle_native_identities == native_ids and len(calls) == 3
    same_error(lambda: selected.__exit__(None, None, None), raised.value)
    assert len(calls) == 3


@pytest.mark.parametrize("primary", [RuntimeError("body"), SystemExit(852), MemoryError("synthetic memory")],
                         ids=["ordinary", "control", "synthetic-memory"])
def test_controlled_body_first_survives_close_fault_and_all_resources_attempt(files_env, monkeypatch, primary):
    root, real_handle, real_fd = owner(files_env), directories._close_handle, directories._close_fd
    counts = {"handle": 0, "fd": 0}
    def handle(value):
        counts["handle"] += 1
        assert real_handle(value)
        raise RuntimeError("synthetic handle post-close fault")
    def fd(value):
        counts["fd"] += 1
        assert real_fd(value) is None
        raise RuntimeError("synthetic fd post-close fault")
    monkeypatch.setattr(directories, "_close_handle", handle)
    monkeypatch.setattr(directories, "_close_fd", fd)
    with pytest.raises(type(primary)) as raised:
        with root:
            lease = root.open_file(NODE)
            with lease:
                raise primary
    assert raised.value is primary and counts == {"handle": 1, "fd": 1}
    common(root.close_receipt, "directory", failed=True)
    common(lease.close_receipt, "readonly", failed=True)
    assert root.close_receipt.handle_states == ("unknown",) and lease.close_receipt.fd_state == "unknown"


@pytest.mark.parametrize("stage", ["directory-handle", "file-handle", "fd-transfer"])
def test_controlled_acquisition_postreturn_expiry_records_failed_enter_and_cannot_upgrade(files_env, monkeypatch, stage):
    start = directories.time.monotonic()
    clock = SimpleNamespace(value=start)
    token = deadline.Namespace3Deadline(start + 30.0)
    monkeypatch.setattr(directories, "time", SimpleNamespace(monotonic=lambda: clock.value))
    root = owner(files_env, token)
    real_create, real_transfer = directories._create_file, directories._open_osfhandle
    closes = {"handle": 0, "fd": 0}
    real_handle, real_fd = directories._close_handle, directories._close_fd
    def handle(value):
        closes["handle"] += 1
        return real_handle(value)
    def fd(value):
        closes["fd"] += 1
        return real_fd(value)
    monkeypatch.setattr(directories, "_close_handle", handle)
    monkeypatch.setattr(directories, "_close_fd", fd)
    def create(*args):
        value = real_create(*args)
        clock.value = token.absolute_monotonic
        return value
    def transfer(*args):
        value = real_transfer(*args)
        clock.value = token.absolute_monotonic
        return value
    if stage == "directory-handle":
        monkeypatch.setattr(directories, "_create_file", create)
        directory_error(root.__enter__, "DEADLINE")
        selected = root
    else:
        root.__enter__()
        monkeypatch.setattr(directories, "_create_file" if stage == "file-handle" else "_open_osfhandle",
                            create if stage == "file-handle" else transfer)
        selected = root.open_file(NODE)
        directory_error(selected.__enter__, "DEADLINE")
    kind = "directory" if stage == "directory-handle" else "readonly"
    receipt = selected.close_receipt
    common(receipt, kind, entered=False, failed=True)
    if stage == "directory-handle":
        assert receipt.handle_states == ("closed",) and receipt.handle_native_identities == (None,)
    elif stage == "file-handle":
        assert receipt.handle_state == "closed" and receipt.fd_state == "empty"
    else:
        assert receipt.handle_state == "transferred" and receipt.fd_state == "closed"
        assert receipt.raw_state == receipt.buffer_state == "empty"
    clock.value = start
    prior = dict(closes)
    selected.__exit__(None, None, None)
    assert selected.close_receipt == receipt and closes == prior
    if selected is not root:
        root.__exit__(None, None, None)


@pytest.mark.parametrize("kind", ["directory", "readonly"])
def test_controlled_exact_final_deadline_keeps_physical_closed_without_normal_exit(files_env, monkeypatch, kind):
    start = directories.time.monotonic()
    clock = SimpleNamespace(value=start)
    token = deadline.Namespace3Deadline(start + 30.0)
    monkeypatch.setattr(directories, "time", SimpleNamespace(monotonic=lambda: clock.value))
    root = owner(files_env, token)
    root.__enter__()
    selected = root if kind == "directory" else root.open_file(NODE)
    if kind == "readonly":
        selected.__enter__()
    alias = "_close_handle" if kind == "directory" else "_close_fd"
    real, calls = getattr(directories, alias), []
    def close(value):
        calls.append(value)
        returned = real(value)
        clock.value = token.absolute_monotonic
        return returned
    monkeypatch.setattr(directories, alias, close)
    directory_error(lambda: selected.__exit__(None, None, None), "DEADLINE")
    receipt = selected.close_receipt
    if kind == "directory":
        directory_closed(receipt, normal=False, failed=True)
    else:
        readonly_closed(receipt, normal=False, failed=True)
    clock.value = start
    selected.__exit__(None, None, None)
    assert selected.close_receipt == receipt and len(calls) == 1
    if selected is not root:
        root.__exit__(None, None, None)


@pytest.mark.parametrize("after_real_transfer", [False, True], ids=["before-crt", "after-crt"])
def test_controlled_no_fd_return_keeps_transfer_unknown_and_only_fixture_recovers(files_env, monkeypatch, after_real_transfer):
    first = OSError("synthetic opaque CRT result")
    real_transfer, real_handle, real_fd = directories._open_osfhandle, directories._close_handle, directories._close_fd
    known, subject_calls = {"handle": None, "fd": None}, {"handle": [], "fd": []}
    def transfer(handle, flags):
        known["handle"] = handle
        if after_real_transfer:
            known["fd"] = real_transfer(handle, flags)
        raise first
    def handle(value):
        subject_calls["handle"].append(value)
        return real_handle(value)
    def fd(value):
        subject_calls["fd"].append(value)
        return real_fd(value)
    fixture_first = None
    try:
        with owner(files_env) as root:
            with monkeypatch.context() as patch:
                patch.setattr(directories, "_open_osfhandle", transfer)
                patch.setattr(directories, "_close_handle", handle)
                patch.setattr(directories, "_close_fd", fd)
                patch.setattr(directories, "_fstat", forbidden)
                patch.setattr(directories, "_file_io", forbidden)
                patch.setattr(directories, "_buffered_reader", forbidden)
                selected = root.open_file(NODE)
                same_error(selected.__enter__, first)
                receipt = selected.close_receipt
                common(receipt, "readonly", entered=False, failed=True)
                assert receipt.handle_state == receipt.transfer_state == "unknown"
                assert receipt.fd_state == receipt.raw_state == receipt.buffer_state == "empty"
                same_error(lambda: selected.__exit__(None, None, None), first)
                assert known["handle"] not in subject_calls["handle"] and subject_calls["fd"] == []
                with patch.context() as cache_patch:
                    trap_directory_consumers(cache_patch)
                    assert selected.close_receipt == receipt
    except BaseException as primary:
        fixture_first = primary
        raise
    finally:
        # Only this fixed injection boundary knows which untouched resource it owns.
        # Never retry if a regression already attempted an opaque numeric close.
        if known["handle"] is not None and known["handle"] not in subject_calls["handle"] and not subject_calls["fd"]:
            try:
                if known["fd"] is not None:
                    assert real_fd(known["fd"]) is None
                else:
                    assert real_handle(known["handle"])
            except BaseException:
                if fixture_first is None:
                    raise
                BaseException.add_note(fixture_first, "R852_FIXTURE_RECOVERY_FAILED")


@contextmanager
def node_env(env, *, missing=False):
    limited = budget()
    path = env.deep / "e" if missing else env.root
    with directories.DeadlineDirectory(path, base=path, deadline_token=limited.effective_deadline) as directory:
        node = source.DeadlineNodeDirectory(directory, limited)
        yield SimpleNamespace(node=node, directory=directory, budget=limited)


def source_context(item, kind):
    return item.node.open_file(NODE) if kind == "stream" else source.DeadlineHeldFile(item.node)


def lower_readonly(receipt):
    return (receipt.readonly_close_receipt if type(receipt) is source.NodeStreamCloseReceiptV1
            else receipt.stream_close_receipt.readonly_close_receipt)


def test_actual_source_stream_and_held_cache_exact_lower_facts_without_provider_reads(files_env, monkeypatch):
    with node_env(files_env) as item:
        stream = item.node.open_file(NODE)
        with stream:
            assert stream.read(len(PAYLOAD)) == PAYLOAD and stream.read(1) == b""
        common(stream.close_receipt, "node_stream", normal=True)
        readonly_closed(stream.close_receipt.readonly_close_receipt)
        held = source.DeadlineHeldFile(item.node)
        with held:
            assert held.bytes(4096) == PAYLOAD
        common(held.close_receipt, "held_file", normal=True)
        common(held.close_receipt.stream_close_receipt, "node_stream", normal=True)
        readonly_closed(lower_readonly(held.close_receipt))
        snapshots = stream.close_receipt, held.close_receipt
        with monkeypatch.context() as patch:
            for name in ("_clock", "_monotonic", "_stat", "_fstat", "_raw_read", "_raw_seek", "_raw_fileno", "_lease_exit"):
                patch.setattr(source, name, forbidden)
            trap_directory_consumers(patch)
            patch.setattr(directories.DeadlineReadonlyLease, "close_receipt", property(forbidden))
            assert (stream.close_receipt, held.close_receipt) == snapshots
            patch.setattr(source.DeadlineNodeStream, "close_receipt", property(forbidden))
            assert held.close_receipt == snapshots[1]
        item.directory.verify()
    assert Path(native(files_env.root / NODE)).read_bytes() == PAYLOAD


@pytest.mark.parametrize("kind", ["stream", "held"])
def test_controlled_source_lower_exit_then_throw_preserves_closed_receipt_and_first(files_env, monkeypatch, kind):
    first, calls = RuntimeError("synthetic lower exit postreturn fault"), []
    real = source._lease_exit
    def exit_lease(*args):
        calls.append("exit")
        assert real(*args) is None
        raise first
    with node_env(files_env) as item:
        selected = source_context(item, kind)
        selected.__enter__()
        monkeypatch.setattr(source, "_lease_exit", exit_lease)
        same_error(lambda: selected.__exit__(None, None, None), first)
        receipt = selected.close_receipt
        common(receipt, "node_stream" if kind == "stream" else "held_file", failed=True)
        readonly_closed(lower_readonly(receipt))
        same_error(lambda: selected.__exit__(None, None, None), first)
        assert calls == ["exit"] and selected.close_receipt == receipt


@pytest.mark.parametrize("kind", ["stream", "held"])
def test_controlled_source_final_clock_expiry_keeps_lower_native_close(files_env, monkeypatch, kind):
    real = source._lease_exit
    clock, calls = SimpleNamespace(value=source._monotonic()), []
    with node_env(files_env) as item:
        selected = source_context(item, kind)
        selected.__enter__()
        def exit_lease(*args):
            calls.append("exit")
            returned = real(*args)
            clock.value = item.budget.effective_deadline.absolute_monotonic
            return returned
        monkeypatch.setattr(source, "_monotonic", lambda: clock.value)
        monkeypatch.setattr(source, "_lease_exit", exit_lease)
        with pytest.raises(source.SourceDeadlineError) as raised:
            selected.__exit__(None, None, None)
        assert raised.value.code == "SOURCE_DEADLINE_DEADLINE"
        receipt = selected.close_receipt
        common(receipt, "node_stream" if kind == "stream" else "held_file", failed=True)
        readonly_closed(lower_readonly(receipt))
        same_error(lambda: selected.__exit__(None, None, None), raised.value)
        assert calls == ["exit"] and selected.close_receipt == receipt


def test_actual_missing_node_lower_enter_self_cleanup_is_cached_without_second_exit(files_env, monkeypatch):
    with node_env(files_env, missing=True) as item:
        selected = item.node.open_file(NODE)
        calls, real = [], source._lease_exit
        def exit_lease(*args):
            calls.append("exit")
            return real(*args)
        monkeypatch.setattr(source, "_lease_exit", exit_lease)
        with pytest.raises(FileNotFoundError) as raised:
            selected.__enter__()
        receipt = selected.close_receipt
        common(receipt, "node_stream", entered=False, exited=False, failed=True)
        common(receipt.readonly_close_receipt, "readonly", entered=False, failed=True)
        assert receipt.readonly_close_receipt.handle_state == "empty"
        same_error(lambda: selected.__exit__(None, None, None), raised.value)
        assert calls == [] and selected.close_receipt == receipt


@pytest.mark.parametrize("kind", ["stream", "held"])
def test_controlled_source_non_none_lower_exit_is_invalid_even_when_falsey(files_env, monkeypatch, kind):
    real, calls = source._lease_exit, []
    def exit_lease(*args):
        calls.append("exit")
        assert real(*args) is None
        return 0
    with node_env(files_env) as item:
        selected = source_context(item, kind)
        selected.__enter__()
        monkeypatch.setattr(source, "_lease_exit", exit_lease)
        with pytest.raises(source.SourceDeadlineError) as raised:
            selected.__exit__(None, None, None)
        assert raised.value.code == "SOURCE_DEADLINE_INVALID_RESULT"
        receipt = selected.close_receipt
        common(receipt, "node_stream" if kind == "stream" else "held_file", failed=True)
        readonly_closed(lower_readonly(receipt))
        same_error(lambda: selected.__exit__(None, None, None), raised.value)
        assert calls == ["exit"]


@pytest.mark.parametrize("kind", ["stream", "held"])
def test_controlled_source_enter_lower_receipt_getter_postreturn_expiry_blocks_delivery(files_env, monkeypatch, kind):
    with node_env(files_env) as item:
        selected = source_context(item, kind)
        lower_cls = directories.DeadlineReadonlyLease if kind == "stream" else source.DeadlineNodeStream
        real_getter = lower_cls.close_receipt.fget
        clock, reads = SimpleNamespace(value=source._monotonic()), []
        def getter(lower):
            receipt = real_getter(lower)
            reads.append(receipt.phase)
            if receipt.phase == "active":
                clock.value = item.budget.effective_deadline.absolute_monotonic
            return receipt
        monkeypatch.setattr(source, "_monotonic", lambda: clock.value)
        monkeypatch.setattr(lower_cls, "close_receipt", property(getter))
        with pytest.raises(source.SourceDeadlineError) as raised:
            selected.__enter__()
        assert raised.value.code == "SOURCE_DEADLINE_DEADLINE"
        receipt = selected.close_receipt
        common(receipt, "node_stream" if kind == "stream" else "held_file", entered=False, failed=True)
        readonly_closed(lower_readonly(receipt), normal=False, failed=True)
        previous = list(reads)
        same_error(selected.__enter__, raised.value)
        same_error(lambda: selected.__exit__(None, None, None), raised.value)
        assert reads == previous and selected.close_receipt == receipt


@pytest.mark.parametrize("kind", ["stream", "held"])
def test_controlled_source_receipt_getter_failure_never_replaces_body_primary(files_env, monkeypatch, kind):
    first = SystemExit(852)
    with node_env(files_env) as item:
        selected = source_context(item, kind)
        selected.__enter__()
        def getter(_lower):
            raise RuntimeError("synthetic lower receipt getter failure")
        monkeypatch.setattr(directories.DeadlineReadonlyLease, "close_receipt", property(getter))
        assert selected.__exit__(type(first), first, None) is None
        receipt = selected.close_receipt
        common(receipt, "node_stream" if kind == "stream" else "held_file", failed=True)
        assert lower_readonly(receipt) is None
        same_error(lambda: selected.__exit__(None, None, None), first)


@pytest.fixture
def image_env(tmp_path):
    """One short, independent real EX/WAL profile and one original token."""
    root = tmp_path / "p"
    profile = lifecycle.StorageProfile(root / "data/r.db", root / "m", root / "r", root / "c")
    os.makedirs(native(profile.database.parent))
    initial = sqlite3.connect(native(profile.database))
    try:
        initial.executescript("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT); "
                             "INSERT INTO meta VALUES('migration_version','0');")
        initial.commit()
    finally:
        initial.close()
    lifecycle.initialize(profile)
    with lifecycle.exclusive(profile) as guard:
        rw = sqlite3.connect(native(profile.database))
        try:
            assert rw.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal"
            rw.execute("PRAGMA wal_autocheckpoint=0")
            rw.execute("CREATE TABLE wal_probe(value TEXT)")
            rw.execute("INSERT INTO wal_probe VALUES('cached-close')")
            rw.commit()
            limited = budget()
            with directories.DeadlineDirectory(profile.database.parent, base=profile.database.parent,
                    deadline_token=limited.effective_deadline) as directory:
                expected = {suffix: Path(native(Path(str(profile.database) + suffix))).read_bytes()
                            for suffix in ("", "-wal")}
                yield SimpleNamespace(profile=profile, guard=guard, directory=directory,
                    token=limited.effective_deadline, expected=expected)
                assert {suffix: Path(native(Path(str(profile.database) + suffix))).read_bytes()
                        for suffix in expected} == expected
            limited.clock_only()
        finally:
            rw.close()  # Source checkpoint/close follows all subject observations.
    assert lifecycle.current_guard() is None


def test_actual_image_pin_cache_preserves_normal_body_and_postreturn_facts_without_old_evidence_change(image_env, monkeypatch):
    item = image_env
    qa_temp()  # Verify Root's isolated TEMP before the actual private image owner.
    old_fields = {"phase", "temp_state", "private_pin_state", "sql_state", "byte_evidence",
                  "bootstrap_evidence", "normal_exit", "source_directory_borrowed"}
    real_exit = images._directory_exit
    for mode in ("normal", "body", "post-return"):
        first = RuntimeError("synthetic image body") if mode == "body" else OSError("synthetic pin postreturn fault")
        selected = images.committed_image_locked(item.profile, item.directory, deadline_token=item.token)
        assert selected.private_pin_close_receipt is None
        with monkeypatch.context() as patch:
            if mode == "post-return":
                def pin_exit(*args):
                    assert real_exit(*args) is None
                    raise first
                patch.setattr(images, "_directory_exit", pin_exit)
            def run():
                with selected as image:
                    active = selected.private_pin_close_receipt
                    common(active, "directory", phase="active", exited=False)
                    cursor = image.connection.execute("SELECT value FROM wal_probe")
                    try:
                        assert cursor.fetchall() == [("cached-close",)]
                    finally:
                        cursor.close()
                    if mode == "body":
                        raise first
            if mode == "normal":
                run()
            else:
                same_error(run, first)
        receipt, evidence = selected.private_pin_close_receipt, selected.evidence
        directory_closed(receipt, normal=mode != "body", failed=mode == "body")
        assert set(evidence) == old_fields
        assert evidence["private_pin_state"] == ("closed" if mode == "normal" else "unknown")
        assert evidence["normal_exit"] is (mode == "normal")
        with monkeypatch.context() as patch:
            patch.setattr(directories.DeadlineDirectory, "close_receipt", property(forbidden))
            trap_directory_consumers(patch)
            for name in ("_clock", "_monotonic", "_directory_enter", "_directory_exit", "_source_state",
                         "_sql_enter", "_sql_exit", "_private_directory", "_temp_cleanup", "_image_verify_source"):
                patch.setattr(images, name, forbidden)
            assert selected.private_pin_close_receipt == receipt and selected.evidence == evidence
            assert selected.private_pin_close_receipt == receipt
        assert lifecycle.require_guard(item.profile, exclusive=True) is item.guard
        item.directory.verify()


def test_controlled_image_private_pin_enter_self_cleanup_is_cached_without_second_exit(image_env, monkeypatch):
    item = image_env
    qa_temp()
    first = OSError("synthetic private pin info postreturn fault")
    selected = images.committed_image_locked(item.profile, item.directory, deadline_token=item.token)
    real_enter, real_info, real_exit = images._directory_enter, directories._get_info, images._directory_exit
    calls = {"info": 0, "exit": 0}
    def enter(pin):
        # Only the actual private pin's own entry uses this finite consumer seam.
        def info(*args):
            returned = real_info(*args)
            assert type(returned) is int and returned != 0
            calls["info"] += 1
            raise first
        with monkeypatch.context() as patch:
            patch.setattr(directories, "_get_info", info)
            return real_enter(pin)
    def exit_pin(*args):
        calls["exit"] += 1
        return real_exit(*args)
    monkeypatch.setattr(images, "_directory_enter", enter)
    monkeypatch.setattr(images, "_directory_exit", exit_pin)
    same_error(selected.__enter__, first)
    receipt = selected.private_pin_close_receipt
    common(receipt, "directory", entered=False, failed=True)
    assert receipt.handle_states == ("closed",) and receipt.handle_native_identities == (None,)
    assert selected.evidence["private_pin_state"] == "unknown" and selected.evidence["normal_exit"] is False
    same_error(lambda: selected.__exit__(None, None, None), first)
    assert calls == {"info": 1, "exit": 0} and selected.private_pin_close_receipt == receipt
    assert lifecycle.require_guard(item.profile, exclusive=True) is item.guard
    item.directory.verify()
