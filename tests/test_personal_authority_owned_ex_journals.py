"""r860 selected actual opted directory/unlock journals; static prepared only.

Root admits/runs separately. Exact legacy types and verification dispatch are
retained. These lower owner facts never create a complete EX group, Guard,
source_closed, SQL registration, live writer grant or a new sampling budget.
"""
from collections import Counter
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, fields
import ctypes
import errno
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
from reno.sync_protocol import SyncError


pytestmark = pytest.mark.skipif(os.name != "nt", reason="actual Windows opted native journals")
OPERATION = ("00000000-0000-4000-8000-000000000860",
             "00000000-0000-4000-8000-000000000861",
             "00000000-0000-4000-8000-000000000862")


def forbidden(*_args, **_kwargs):
    pytest.fail("cached/retired journal reached forbidden native or consumer")


def legal_bool(actual):
    # Real native success is validated before any controlled fake result/count.
    assert type(actual) is int and -(1 << 31) <= actual < (1 << 31) and actual != 0
    return actual


def same_error(call, first):
    with pytest.raises(type(first)) as caught:
        call()
    assert caught.value is first


def caught_error(call, cls, code=None):
    with pytest.raises(cls) as caught:
        call()
    if code is not None:
        assert caught.value.code == code
    return caught.value


def frozen(value):
    with pytest.raises(FrozenInstanceError):
        value.phase = "changed"
    assert not hasattr(value, "__dict__")
    assert not ({row.name for row in fields(value)} &
                {"handle", "fd", "path", "directory", "stream", "callback", "exception",
                 "source_closed", "owned_outer_ex_closed", "writer_authority"})


def common(value, cls, kind, *, phase="retired", failed=False, normal=False):
    assert type(value) is cls
    assert type(value.format_version) is int and value.format_version == 1
    assert value.kind == kind and value.scope == "native_owner_close_facts_only"
    assert value.phase == phase and type(value.root_ordinal) is int and value.root_ordinal == 0
    assert type(value.failed) is bool and value.failed is failed
    assert type(value.normal_exit) is bool and value.normal_exit is normal
    frozen(value)


def directory_facts(directory, *, phase="retired", entered=True, exited=True, failed=False, normal=False):
    value = directory.owned_close_receipt
    common(value, safe.OwnedDirectoryCloseReceiptV1, "owned_directory",
           phase=phase, failed=failed, normal=normal)
    assert value.enter_returned is entered and value.exit_attempted is exited
    assert type(value.enter_returned) is type(value.exit_attempted) is bool
    assert type(value.handle_states) is tuple and len(value.handle_states) == 1
    return value


def lock_facts(lock, *, phase="retired", failed=False, normal=False):
    value = lock.owned_lock_receipt
    common(value, lifecycle.OwnedLockCloseReceiptV1, "owned_lock",
           phase=phase, failed=failed, normal=normal)
    assert value.constructed_returned is True
    assert value.directory_borrowed is value.stream_borrowed is True
    assert type(value.acquire_attempts) is int and value.acquire_attempts >= 0
    return value


@pytest.fixture
def actual_roots():
    qa = Path(os.environ["RENO_QA_ROOT"]).resolve(strict=True)
    selected = Path(tempfile.gettempdir()).resolve(strict=True)
    selected.relative_to(qa)
    assert selected.is_dir() and lifecycle.current_guard() is None
    with tempfile.TemporaryDirectory(prefix="j", dir=selected) as text:
        root = Path(text)
        profile = lifecycle.StorageProfile(root / "d/r.db", root / "m", root / "r", root / "c")
        assert lifecycle.initialize(profile)["created_locks"] == len(profile.roots)
        limited = deadline._RestrictedBudget(deadline.Namespace3Deadline(1.0e15))
        parent, token, scope = limited.requested_deadline, limited.effective_deadline, object()
        assert parent is not token and 0.0 < token.absolute_monotonic - deadline._now() <= 30.0
        binding = owned._new_owned_ex_binding(OPERATION, parent_token=parent,
                                              effective_token=token, scope_identity=scope)
        yield SimpleNamespace(profile=profile, root=profile.roots[0], limited=limited,
                              parent=parent, token=token, scope=scope, binding=binding)
        limited.clock_only()
    assert lifecycle.current_guard() is None


def new_directory(item):
    journal = safe._new_owned_directory_journal(item.binding, root_ordinal=0)
    return safe._Directory(item.root, create=False, base=item.root, _owned_close_journal=journal)


@contextmanager
def resources(item):
    directory = new_directory(item)
    with directory:
        stream_owner = owned.LockStreamDeadlineLease(directory, binding=item.binding, root_ordinal=0)
        with stream_owner as stream:
            journal = lifecycle._new_owned_lock_journal(item.binding, root_ordinal=0,
                stream_owner=stream_owner, directory_owner=directory)
            lock = lifecycle._FileLock(directory, stream, _owned_lock_journal=journal)
            try:
                yield SimpleNamespace(directory=directory, stream_owner=stream_owner,
                                      stream=stream, lock=lock, journal=journal)
            except BaseException as primary:
                lifecycle._remember_owned_lock_primary(lock, primary)
                raise
            finally:
                lock.release()


def native_traps(patch):
    for name in ("_owned_directory_create_file", "_owned_directory_get_info",
                 "_owned_directory_close_handle", "_owned_directory_clock"):
        patch.setattr(safe, name, forbidden)
    for name in ("_owned_lock_get_osfhandle", "_owned_lock_lock_file", "_owned_lock_unlock_file", "_owned_lock_clock"):
        patch.setattr(lifecycle, name, forbidden)


def test_actual_opted_owners_keep_original_dispatch_and_real_close_order(actual_roots, monkeypatch):
    calls, order, native_ids, native_flags = Counter(), [], [], []
    real_native_create = safe._kernel.CreateFileW
    real_create, real_info = safe._owned_directory_create_file, safe._owned_directory_get_info
    real_dir_close = safe._owned_directory_close_handle
    real_acquire, real_unlock, real_fd = lifecycle._owned_lock_lock_file, lifecycle._owned_lock_unlock_file, owned._close_fd

    def native_create(*args):
        native_flags.append(args)
        return real_native_create(*args)

    def create(path):
        calls["directory_create"] += 1
        assert path == safe._native_path(actual_roots.root)
        return real_create(path)

    def info(handle, pointer):
        result = legal_bool(real_info(handle, pointer))
        value = ctypes.cast(pointer, ctypes.POINTER(safe._HandleInfo)).contents
        native_ids.append((value.volume, value.index_high, value.index_low))
        return result

    def acquire(*args):
        result = legal_bool(real_acquire(*args))
        calls["acquire"] += 1
        return result

    def unlock(*args):
        assert current.lock.locked is False  # Logical retirement precedes actual API return.
        result = legal_bool(real_unlock(*args))
        order.append("unlock")
        calls["unlock"] += 1
        return result

    def close_fd(fd):
        assert real_fd(fd) is None
        order.append("stream_fd")
        calls["fd"] += 1

    def close_dir(handle):
        result = legal_bool(real_dir_close(handle))
        order.append("directory_handle")
        calls["directory_close"] += 1
        return result

    for module, name, value in ((safe, "_owned_directory_create_file", create),
            (safe, "_owned_directory_get_info", info), (safe, "_owned_directory_close_handle", close_dir),
            (lifecycle, "_owned_lock_lock_file", acquire), (lifecycle, "_owned_lock_unlock_file", unlock),
            (owned, "_close_fd", close_fd)):
        monkeypatch.setattr(module, name, value)
    monkeypatch.setattr(safe._kernel, "CreateFileW", native_create)
    with resources(actual_roots) as current:
        directory, lock, stream = current.directory, current.lock, current.stream
        assert type(directory) is safe._Directory and type(lock) is lifecycle._FileLock
        assert type(stream) is io.BufferedReader and type(stream.raw) is io.FileIO and stream.raw.closefd is False
        assert lifecycle._original_lock_dispatch((lock,)) is True
        assert directory.verify() is lock.verify() is None
        assert lifecycle._verify_lock_group((lock,)) is None
        assert lock.attempt(True) is True and lock.locked is True
        active = lock.owned_lock_receipt
        assert active.acquire_state == "acquired" and active.unlock_owed is True
        assert active.acquire_native_attempted is active.acquire_returned is active.acquire_return_legal is True
        assert active.file_identity == lock.identity
        assert directory.owned_close_receipt.native_file_identity == native_ids[0]
        assert directory.owned_close_receipt.directory_identity == tuple(directory.identities[-1][1:])
        assert lifecycle._original_lock_dispatch((lock,)) is True and lock.verify() is None
    dr = directory_facts(directory, normal=True)
    lr = lock_facts(lock, normal=True)
    assert dr.handle_states == ("closed",) and dr.close_consumed is dr.close_returned is dr.close_return_legal is True
    assert lr.acquire_state == "acquired" and lr.unlock_state == "unlocked"
    assert lr.unlock_consumed is lr.unlock_native_attempted is lr.unlock_returned is lr.unlock_return_legal is True
    assert calls == {"directory_create": 1, "acquire": 1, "unlock": 1, "fd": 1, "directory_close": 1}
    assert order == ["unlock", "stream_fd", "directory_handle"] and len(native_ids) == 1
    assert native_flags == [(safe._native_path(actual_roots.root), 0x1, 3, None, 3, 0x02200000, None)]
    assert current.stream_owner.close_receipt.fd_state == "closed"


def test_actual_cached_terminal_journals_do_not_read_bindings_providers_or_lower_getters(actual_roots, monkeypatch):
    with resources(actual_roots) as current:
        assert current.lock.attempt(True) is True
    directory, lock = current.directory, current.lock
    dr, lr = directory_facts(directory, normal=True), lock_facts(lock, normal=True)
    with monkeypatch.context() as patch:
        native_traps(patch)
        patch.setattr(owned, "_binding", forbidden)
        patch.setattr(owned, "_clock", forbidden)
        patch.setattr(safe, "_owned_directory_module", forbidden)
        patch.setattr(lifecycle, "_owned_lock_module", forbidden)
        for _ in range(2):
            assert directory.owned_close_receipt == dr and lock.owned_lock_receipt == lr
        assert lock.release() is None
        assert directory.__exit__(None, None, None) is None
        assert directory.owned_close_receipt == dr and lock.owned_lock_receipt == lr


def test_actual_default_none_journals_keep_original_shared_exclusive_and_verification(actual_roots, monkeypatch):
    with monkeypatch.context() as patch:
        native_traps(patch)
        with safe._Directory(actual_roots.root, create=False, base=actual_roots.root) as directory:
            assert directory.owned_close_receipt is None
            with lifecycle._open_lock(directory) as stream:
                lock = lifecycle._FileLock(directory, stream)
                assert lock.owned_lock_receipt is None and lifecycle._original_lock_dispatch((lock,)) is True
                for exclusive in (False, True):
                    assert lock.verify() is None and lock.attempt(exclusive) is True
                    assert lock.release() is None and lock.locked is False
                    assert lock.owned_lock_receipt is None and directory.owned_close_receipt is None
        for factory in (lifecycle.shared, lifecycle.exclusive):
            with factory(actual_roots.profile, timeout=0) as guard:
                assert type(guard) is lifecycle.StorageGuard and guard.verify() is guard
    assert lifecycle.current_guard() is None


@pytest.mark.parametrize("kind", ["ordinary", "control"])
def test_controlled_directory_failed_query_closes_inside_call_before_public_return(actual_roots, monkeypatch, kind):
    directory, calls = new_directory(actual_roots), Counter()
    first = OSError("directory query first") if kind == "ordinary" else SystemExit(860)
    real_info, real_close = safe._owned_directory_get_info, safe._owned_directory_close_handle

    def info(*args):
        legal_bool(real_info(*args))
        calls["query"] += 1
        raise first

    def close(handle):
        result = legal_bool(real_close(handle))
        calls["close"] += 1
        return result

    monkeypatch.setattr(safe, "_owned_directory_get_info", info)
    monkeypatch.setattr(safe, "_owned_directory_close_handle", close)
    same_error(directory.__enter__, first)
    value = directory_facts(directory, entered=False, failed=True)
    assert value.handle_states == ("closed",)
    assert value.create_returned is value.create_return_legal is True
    assert value.query_native_attempted is True and value.query_returned is False
    assert value.close_consumed is value.close_native_attempted is value.close_returned is True
    same_error(directory.__enter__, first)
    same_error(lambda: directory.__exit__(None, None, None), first)
    assert calls == {"query": 1, "close": 1} and directory.owned_close_receipt == value


@pytest.mark.parametrize("fault", ["reparse", "not-directory"])
def test_controlled_directory_unsafe_native_attributes_have_one_real_handle_cleanup(actual_roots, monkeypatch, fault):
    directory, calls = new_directory(actual_roots), Counter()
    real_info, real_close = safe._owned_directory_get_info, safe._owned_directory_close_handle

    def info(handle, pointer):
        result = legal_bool(real_info(handle, pointer))
        value = ctypes.cast(pointer, ctypes.POINTER(safe._HandleInfo)).contents
        value.attributes = (value.attributes | safe._REPARSE) if fault == "reparse" else (value.attributes & ~0x10)
        return result

    def close(handle):
        result = legal_bool(real_close(handle))
        calls["close"] += 1
        return result

    monkeypatch.setattr(safe, "_owned_directory_get_info", info)
    monkeypatch.setattr(safe, "_owned_directory_close_handle", close)
    first = caught_error(directory.__enter__, SyncError, "SYNC_MEDIA_UNSAFE_PATH")
    value = directory_facts(directory, entered=False, failed=True)
    assert value.handle_states == ("closed",) and value.query_returned is value.query_return_legal is True
    same_error(lambda: directory.__exit__(None, None, None), first)
    assert calls == {"close": 1} and directory.owned_close_receipt == value


@pytest.mark.parametrize("after", [False, True], ids=["before-append", "after-append"])
def test_controlled_directory_handle_append_fault_cannot_double_close_owned_slot(actual_roots, monkeypatch, after):
    directory, first, calls = new_directory(actual_roots), MemoryError("synthetic handle append"), Counter()
    original = safe._owned_directory_close_handle

    class FaultList(list):
        def append(self, value):
            calls["append"] += 1
            if after:
                super().append(value)
            raise first

    directory.handles = FaultList()

    def close(handle):
        result = legal_bool(original(handle))
        calls["close"] += 1
        return result

    monkeypatch.setattr(safe, "_owned_directory_close_handle", close)
    same_error(directory.__enter__, first)
    value = directory_facts(directory, entered=False, failed=True)
    assert value.handle_states == ("closed",) and value.close_consumed is True
    same_error(lambda: directory.__exit__(None, None, None), first)
    assert calls == {"append": 1, "close": 1} and directory.owned_close_receipt == value


@pytest.mark.parametrize("reported", [None, False, True, 0, 1.0, 1 << 31],
                         ids=["none", "false", "true", "zero", "float", "out-of-signed32"])
def test_controlled_directory_close_return_domain_is_strict_and_unknown_not_retried(actual_roots, monkeypatch, reported):
    directory, calls = new_directory(actual_roots), Counter()
    original = safe._owned_directory_close_handle
    assert directory.__enter__() is directory

    def close(handle):
        legal_bool(original(handle))
        calls["close"] += 1
        if type(reported) is int and reported == 0:
            ctypes.set_last_error(5)
        return reported

    monkeypatch.setattr(safe, "_owned_directory_close_handle", close)
    if type(reported) is int and reported == 0:
        first = caught_error(lambda: directory.__exit__(None, None, None), OSError)
        assert first.winerror == 5
    else:
        first = caught_error(lambda: directory.__exit__(None, None, None), owned.OwnedExDeadlineError,
                             "OWNED_EX_DEADLINE_INVALID_RESULT")
    value = directory_facts(directory, failed=True)
    assert value.handle_states == ("unknown",)
    assert value.close_consumed is value.close_native_attempted is value.close_returned is True
    assert value.close_return_legal is (type(reported) is int and -(1 << 31) <= reported < (1 << 31))
    same_error(lambda: directory.__exit__(None, None, None), first)
    assert calls == {"close": 1} and directory.owned_close_receipt == value


@pytest.mark.parametrize("after", [False, True], ids=["before-native", "after-native"])
def test_controlled_directory_opaque_create_unknown_has_only_known_fixture_recovery(actual_roots, monkeypatch, after):
    directory, first, witness, recovery = new_directory(actual_roots), OSError("opaque directory create"), {}, Counter()
    real_create, real_close = safe._owned_directory_create_file, safe._owned_directory_close_handle

    def create(path):
        if after:
            handle = real_create(path)
            assert type(handle) is int and 0 < handle < ctypes.c_void_p(-1).value
            witness["handle"] = handle
        raise first

    monkeypatch.setattr(safe, "_owned_directory_create_file", create)
    monkeypatch.setattr(safe, "_owned_directory_close_handle", forbidden)
    try:
        same_error(directory.__enter__, first)
        value = directory_facts(directory, entered=False, failed=True)
        assert value.handle_states == ("unknown",)
        assert value.create_native_attempted is True and value.create_returned is False
        assert value.close_native_attempted is value.close_consumed is False
        same_error(lambda: directory.__exit__(None, None, None), first)
        assert directory.owned_close_receipt == value
    finally:
        if "handle" in witness:
            legal_bool(real_close(witness["handle"]))
            recovery["known_fixture_handle"] += 1
    assert recovery == ({"known_fixture_handle": 1} if after else {})
    assert directory.owned_close_receipt == value  # Never upgrade UNKNOWN after fixture recovery.


@pytest.mark.parametrize("stage", ["create-return", "last-close"])
def test_controlled_directory_postreturn_deadline_keeps_closed_but_no_normal_exit(actual_roots, monkeypatch, stage):
    item, directory, calls, expired = actual_roots, new_directory(actual_roots), Counter(), [False]
    real_clock = safe._owned_directory_clock
    api = "_owned_directory_create_file" if stage == "create-return" else "_owned_directory_close_handle"
    original = getattr(safe, api)

    def returned(*args):
        result = original(*args)
        if stage == "last-close":
            legal_bool(result)
        calls[api] += 1
        expired[0] = True
        return result

    def clock(token):
        assert token is item.token
        if expired[0]:
            raise owned.OwnedExDeadlineError("DEADLINE")
        return real_clock(token)

    monkeypatch.setattr(safe, "_owned_directory_clock", clock)
    monkeypatch.setattr(safe, api, returned)
    if stage == "create-return":
        first = caught_error(directory.__enter__, owned.OwnedExDeadlineError, "OWNED_EX_DEADLINE_DEADLINE")
    else:
        assert directory.__enter__() is directory
        first = caught_error(lambda: directory.__exit__(None, None, None), owned.OwnedExDeadlineError,
                             "OWNED_EX_DEADLINE_DEADLINE")
    value = directory_facts(directory, entered=stage != "create-return", failed=True)
    assert value.handle_states == ("closed",) and value.close_consumed is value.close_returned is True
    same_error(lambda: directory.__exit__(None, None, None), first)
    assert calls == {api: 1} and directory.owned_close_receipt == value


@pytest.mark.parametrize("reported_error", [33, 87], ids=["actual-busy33", "controlled-error87-after-busy"])
def test_actual_native_zero_is_known_not_acquired_without_phantom_unlock(actual_roots, monkeypatch, reported_error):
    calls, selected = Counter(), []
    real_lock = lifecycle._owned_lock_lock_file

    def native_attempt(*args):
        result = real_lock(*args)
        assert type(result) is int and result == 0 and ctypes.get_last_error() == 33
        calls["known_busy"] += 1
        ctypes.set_last_error(reported_error)
        return result

    def invoke():
        with resources(actual_roots) as current:
            selected.append(current)
            with lifecycle._open_lock(current.directory) as holder_stream:
                holder = lifecycle._FileLock(current.directory, holder_stream)
                assert holder.attempt(True) is True
                try:
                    with monkeypatch.context() as patch:
                        patch.setattr(lifecycle, "_owned_lock_lock_file", native_attempt)
                        patch.setattr(lifecycle, "_owned_lock_unlock_file", forbidden)
                        if reported_error == 33:
                            assert current.lock.attempt(True) is False
                        else:
                            first = caught_error(lambda: current.lock.attempt(True), lifecycle.StorageLifecycleError,
                                                 "STORAGE_IO_ERROR")
                            same_error(current.lock.release, first)
                            raise first
                finally:
                    assert holder.release() is None

    if reported_error == 33:
        invoke()
        first = None
    else:
        first = caught_error(invoke, lifecycle.StorageLifecycleError, "STORAGE_IO_ERROR")
    current = selected[0]
    value = lock_facts(current.lock, failed=reported_error != 33)
    assert value.acquire_state == "not_acquired" and value.acquire_attempts == 1
    assert value.acquire_native_attempted is value.acquire_returned is value.acquire_return_legal is True
    assert value.unlock_owed is value.unlock_native_attempted is False and value.unlock_state == "not_owed"
    assert value.unlock_consumed is True and calls == {"known_busy": 1}
    if first is None:
        assert current.lock.release() is None
    else:
        same_error(current.lock.release, first)
    assert current.lock.owned_lock_receipt == value and current.stream_owner.close_receipt.fd_state == "closed"
    assert current.directory.owned_close_receipt.handle_states == ("closed",)


@pytest.mark.parametrize("reported", [None, False, True, 1.0, 1 << 31],
                         ids=["none", "false", "true", "float", "out-of-signed32"])
def test_controlled_illegal_acquire_after_actual_success_owes_unlock_despite_locked_false(actual_roots, monkeypatch, reported):
    calls, selected = Counter(), []
    real_lock, real_unlock = lifecycle._owned_lock_lock_file, lifecycle._owned_lock_unlock_file

    def acquire(*args):
        legal_bool(real_lock(*args))
        calls["acquire"] += 1
        return reported

    def unlock(*args):
        assert selected[0].lock.locked is False
        result = legal_bool(real_unlock(*args))
        calls["unlock"] += 1
        return result

    monkeypatch.setattr(lifecycle, "_owned_lock_lock_file", acquire)
    monkeypatch.setattr(lifecycle, "_owned_lock_unlock_file", unlock)

    def invoke():
        with resources(actual_roots) as current:
            selected.append(current)
            current.lock.attempt(True)

    first = caught_error(invoke, owned.OwnedExDeadlineError, "OWNED_EX_DEADLINE_INVALID_RESULT")
    current = selected[0]
    value = lock_facts(current.lock, failed=True)
    assert value.acquire_state == "unknown" and value.acquire_native_attempted is value.acquire_returned is True
    assert value.acquire_return_legal is False and value.acquire_attempts == 1
    assert value.unlock_owed is value.unlock_consumed is value.unlock_native_attempted is True
    assert value.unlock_state == "unlocked" and value.unlock_returned is value.unlock_return_legal is True
    same_error(current.lock.release, first)
    same_error(lambda: current.lock.attempt(True), first)
    assert calls == {"acquire": 1, "unlock": 1} and current.lock.owned_lock_receipt == value
    assert current.stream_owner.close_receipt.fd_state == "closed"
    assert current.directory.owned_close_receipt.handle_states == ("closed",)


@pytest.mark.parametrize("after, kind", [(False, "ordinary"), (True, "ordinary"), (False, "control"), (True, "control")],
                         ids=["before-oserror", "after-oserror", "before-control", "after-control"])
def test_controlled_opaque_native_attempt_owes_one_unlock_and_retains_first_mapping(actual_roots, monkeypatch, after, kind):
    calls, selected = Counter(), []
    original_first = OSError(errno.EIO, "opaque actual lock") if kind == "ordinary" else SystemExit(860)
    real_lock, real_unlock = lifecycle._owned_lock_lock_file, lifecycle._owned_lock_unlock_file

    def acquire(*args):
        calls["expression"] += 1
        if after:
            legal_bool(real_lock(*args))
            calls["actual_acquire"] += 1
        raise original_first

    def unlock(*args):
        assert selected[0].lock.locked is False
        result = real_unlock(*args)
        assert type(result) is int and -(1 << 31) <= result < (1 << 31)
        if after:
            legal_bool(result)
        else:
            assert result == 0  # Known fixture never called native acquire.
        calls["actual_unlock_attempt"] += 1
        return result

    monkeypatch.setattr(lifecycle, "_owned_lock_lock_file", acquire)
    monkeypatch.setattr(lifecycle, "_owned_lock_unlock_file", unlock)

    def invoke():
        with resources(actual_roots) as current:
            selected.append(current)
            current.lock.attempt(True)

    if kind == "ordinary":
        first = caught_error(invoke, lifecycle.StorageLifecycleError, "STORAGE_IO_ERROR")
        assert first is not original_first
    else:
        same_error(invoke, original_first)
        first = original_first
    current = selected[0]
    value = lock_facts(current.lock, failed=True)
    assert value.acquire_native_attempted is True and value.acquire_returned is False
    assert value.acquire_state == "unknown" and value.acquire_attempts == 1
    assert value.unlock_owed is value.unlock_consumed is value.unlock_native_attempted is True
    assert value.unlock_returned is value.unlock_return_legal is True
    assert value.unlock_state == ("unlocked" if after else "unknown")
    same_error(current.lock.release, first)
    assert calls == ({"expression": 1, "actual_acquire": 1, "actual_unlock_attempt": 1} if after
                     else {"expression": 1, "actual_unlock_attempt": 1})
    assert current.stream_owner.close_receipt.fd_state == "closed"
    assert current.directory.owned_close_receipt.handle_states == ("closed",)


@pytest.mark.parametrize("fault", ["closed-fileno", "get-oserror", "get-control"])
def test_controlled_before_native_expression_failure_never_owes_phantom_unlock(actual_roots, monkeypatch, fault):
    selected, injected = [], SystemExit(860) if fault == "get-control" else OSError(errno.EIO, "get handle before native")

    def get_handle(_fd):
        raise injected

    def invoke():
        with resources(actual_roots) as current:
            selected.append(current)
            with monkeypatch.context() as patch:
                patch.setattr(lifecycle, "_owned_lock_lock_file", forbidden)
                patch.setattr(lifecycle, "_owned_lock_unlock_file", forbidden)
                if fault == "closed-fileno":
                    # Exact closefd=False wrappers can retire without owning numeric FD.
                    assert io.BufferedReader.close(current.stream) is None
                    assert current.stream.raw.closefd is False
                else:
                    patch.setattr(lifecycle, "_owned_lock_get_osfhandle", get_handle)
                current.lock.attempt(True)

    if fault == "closed-fileno":
        first = caught_error(invoke, ValueError)
    elif fault == "get-oserror":
        first = caught_error(invoke, lifecycle.StorageLifecycleError, "STORAGE_IO_ERROR")
    else:
        same_error(invoke, injected)
        first = injected
    current = selected[0]
    value = lock_facts(current.lock, failed=True)
    assert value.acquire_expression_started is True and value.acquire_native_attempted is False
    assert value.acquire_attempts == 0 and value.acquire_state == "not_started"
    assert value.unlock_owed is value.unlock_native_attempted is False and value.unlock_state == "not_owed"
    same_error(current.lock.release, first)
    assert current.lock.owned_lock_receipt == value and current.stream_owner.close_receipt.fd_state == "closed"
    assert current.directory.owned_close_receipt.handle_states == ("closed",)


def test_controlled_get_osfhandle_postreturn_expiry_prevents_native_attempt_and_debt(actual_roots, monkeypatch):
    selected, calls, scalar = [], Counter(), [owned._monotonic()]
    real_get, real_clock = lifecycle._owned_lock_get_osfhandle, lifecycle._owned_lock_clock

    def get_handle(fd):
        result = real_get(fd)
        assert type(result) is int and 0 < result < ctypes.c_void_p(-1).value
        calls["get_handle"] += 1
        scalar[0] = actual_roots.token.absolute_monotonic
        return result

    def clock(token):
        assert token is actual_roots.token
        if scalar[0] >= token.absolute_monotonic:
            raise owned.OwnedExDeadlineError("DEADLINE")
        return real_clock(token)

    def invoke():
        with resources(actual_roots) as current:
            selected.append(current)
            with monkeypatch.context() as patch:
                patch.setattr(lifecycle, "_owned_lock_get_osfhandle", get_handle)
                patch.setattr(lifecycle, "_owned_lock_clock", clock)
                patch.setattr(lifecycle, "_owned_lock_lock_file", forbidden)
                patch.setattr(lifecycle, "_owned_lock_unlock_file", forbidden)
                first = caught_error(lambda: current.lock.attempt(True), owned.OwnedExDeadlineError,
                                     "OWNED_EX_DEADLINE_DEADLINE")
                value = current.lock.owned_lock_receipt
                assert value.acquire_native_attempted is False and value.acquire_attempts == 0
                assert value.unlock_owed is False and value.unlock_state == "not_owed"
                same_error(current.lock.release, first)
                raise first

    first = caught_error(invoke, owned.OwnedExDeadlineError, "OWNED_EX_DEADLINE_DEADLINE")
    current = selected[0]
    value = lock_facts(current.lock, failed=True)
    assert value.acquire_expression_started is True and value.acquire_native_attempted is False
    assert value.unlock_consumed is True and value.unlock_owed is value.unlock_native_attempted is False
    assert calls == {"get_handle": 1}
    same_error(current.lock.release, first)
    assert current.stream_owner.close_receipt.fd_state == "closed"
    assert current.directory.owned_close_receipt.handle_states == ("closed",)


@pytest.mark.parametrize("reported", [None, False, True, 0, 1.0, 1 << 31],
                         ids=["none", "false", "true", "zero", "float", "out-of-signed32"])
def test_controlled_actual_unlock_then_invalid_return_stays_unknown_and_other_owners_close(actual_roots, monkeypatch, reported):
    selected, calls = [], Counter()
    real_unlock = lifecycle._owned_lock_unlock_file

    def unlock(*args):
        assert selected[0].lock.locked is False
        legal_bool(real_unlock(*args))
        calls["unlock"] += 1
        if type(reported) is int and reported == 0:
            ctypes.set_last_error(87)
        return reported

    monkeypatch.setattr(lifecycle, "_owned_lock_unlock_file", unlock)

    def invoke():
        with resources(actual_roots) as current:
            selected.append(current)
            assert current.lock.attempt(True) is True

    if type(reported) is int and reported == 0:
        first = caught_error(invoke, lifecycle.StorageLifecycleError, "STORAGE_IO_ERROR")
    else:
        first = caught_error(invoke, owned.OwnedExDeadlineError, "OWNED_EX_DEADLINE_INVALID_RESULT")
    current = selected[0]
    value = lock_facts(current.lock, failed=True)
    assert current.lock.locked is False and value.acquire_state == "acquired"
    assert value.unlock_owed is value.unlock_consumed is value.unlock_native_attempted is value.unlock_returned is True
    assert value.unlock_return_legal is (type(reported) is int and -(1 << 31) <= reported < (1 << 31))
    assert value.unlock_state == "unknown"
    same_error(current.lock.release, first)
    assert calls == {"unlock": 1} and current.lock.owned_lock_receipt == value
    assert current.stream_owner.close_receipt.fd_state == "closed"
    assert current.directory.owned_close_receipt.handle_states == ("closed",)


def test_controlled_release_get_osfhandle_fault_consumed_before_getter_without_second_unlock(actual_roots, monkeypatch):
    selected, calls = [], Counter()

    def get_handle(_fd):
        calls["release_get"] += 1
        raise OSError(errno.EIO, "release get handle")

    def invoke():
        with resources(actual_roots) as current:
            selected.append(current)
            assert current.lock.attempt(True) is True
            monkeypatch.setattr(lifecycle, "_owned_lock_get_osfhandle", get_handle)
            monkeypatch.setattr(lifecycle, "_owned_lock_unlock_file", forbidden)

    first = caught_error(invoke, lifecycle.StorageLifecycleError, "STORAGE_IO_ERROR")
    current = selected[0]
    value = lock_facts(current.lock, failed=True)
    assert value.acquire_state == "acquired" and value.unlock_consumed is value.unlock_owed is True
    assert value.unlock_native_attempted is value.unlock_returned is False and value.unlock_state == "unknown"
    same_error(current.lock.release, first)
    assert calls == {"release_get": 1} and current.lock.owned_lock_receipt == value
    assert current.stream_owner.close_receipt.fd_state == "closed"
    assert current.directory.owned_close_receipt.handle_states == ("closed",)


@pytest.mark.parametrize("kind", ["ordinary", "control", "synthetic-memory"])
def test_controlled_original_body_first_survives_actual_unlock_alias_fault_and_other_close(actual_roots, monkeypatch, kind):
    first = {"ordinary": RuntimeError("body first"), "control": SystemExit(860),
             "synthetic-memory": MemoryError("synthetic body first")}[kind]
    selected, calls = [], Counter()
    real_unlock, real_fd, real_dir = lifecycle._owned_lock_unlock_file, owned._close_fd, safe._owned_directory_close_handle

    def unlock(*args):
        legal_bool(real_unlock(*args))
        calls["unlock"] += 1
        raise OSError(errno.EIO, "unlock actual postreturn alias")

    def fd_close(fd):
        assert real_fd(fd) is None
        calls["fd"] += 1

    def directory_close(handle):
        result = legal_bool(real_dir(handle))
        calls["directory"] += 1
        return result

    monkeypatch.setattr(lifecycle, "_owned_lock_unlock_file", unlock)
    monkeypatch.setattr(owned, "_close_fd", fd_close)
    monkeypatch.setattr(safe, "_owned_directory_close_handle", directory_close)

    def invoke():
        with resources(actual_roots) as current:
            selected.append(current)
            assert current.lock.attempt(True) is True
            raise first

    same_error(invoke, first)
    current = selected[0]
    value = lock_facts(current.lock, failed=True)
    assert value.acquire_state == "acquired" and value.unlock_state == "unknown"
    assert value.unlock_native_attempted is True and value.unlock_returned is False
    same_error(current.lock.release, first)
    assert calls == {"unlock": 1, "fd": 1, "directory": 1}
    assert current.lock.owned_lock_receipt == value and current.stream_owner.close_receipt.fd_state == "closed"
    assert current.directory.owned_close_receipt.handle_states == ("closed",)


@pytest.mark.parametrize("stage", ["acquire-return", "last-unlock"])
def test_controlled_lock_postreturn_deadline_keeps_actual_unlocked_without_normal_claim(actual_roots, monkeypatch, stage):
    selected, calls, scalar = [], Counter(), [owned._monotonic()]
    api = "_owned_lock_lock_file" if stage == "acquire-return" else "_owned_lock_unlock_file"
    original, real_clock = getattr(lifecycle, api), lifecycle._owned_lock_clock

    def returned(*args):
        result = legal_bool(original(*args))
        calls[api] += 1
        scalar[0] = actual_roots.token.absolute_monotonic
        return result

    def clock(token):
        assert token is actual_roots.token
        if scalar[0] >= token.absolute_monotonic:
            raise owned.OwnedExDeadlineError("DEADLINE")
        return real_clock(token)

    monkeypatch.setattr(lifecycle, api, returned)
    monkeypatch.setattr(lifecycle, "_owned_lock_clock", clock)

    def invoke():
        with resources(actual_roots) as current:
            selected.append(current)
            assert current.lock.attempt(True) is True

    first = caught_error(invoke, owned.OwnedExDeadlineError, "OWNED_EX_DEADLINE_DEADLINE")
    current = selected[0]
    value = lock_facts(current.lock, failed=True)
    assert value.acquire_state == "acquired" and value.unlock_state == "unlocked"
    assert value.unlock_returned is value.unlock_return_legal is value.unlock_consumed is True
    same_error(current.lock.release, first)
    assert calls == {api: 1} and current.lock.owned_lock_receipt == value
    assert current.stream_owner.close_receipt.fd_state == "closed"
    assert current.directory.owned_close_receipt.handle_states == ("closed",)


def test_actual_journals_reject_dto_foreign_lineage_and_reused_owner_before_native(actual_roots, monkeypatch):
    with resources(actual_roots) as current:
        with monkeypatch.context() as patch:
            native_traps(patch)
            caught_error(lambda: safe._Directory(actual_roots.root, create=False, base=actual_roots.root,
                _owned_close_journal=SimpleNamespace()), owned.OwnedExDeadlineError, "OWNED_EX_DEADLINE_INVALID_ARGUMENT")
            caught_error(lambda: safe._Directory(actual_roots.root, create=False, base=actual_roots.root,
                _owned_close_journal=current.directory._owned_close_journal), owned.OwnedExDeadlineError,
                "OWNED_EX_DEADLINE_INVALID_ARGUMENT")
            caught_error(lambda: lifecycle._FileLock(current.directory, current.stream,
                _owned_lock_journal=current.journal), owned.OwnedExDeadlineError, "OWNED_EX_DEADLINE_INVALID_ARGUMENT")
            caught_error(lambda: lifecycle._new_owned_lock_journal(actual_roots.binding, root_ordinal=0,
                stream_owner=current.stream_owner.close_receipt, directory_owner=current.directory),
                owned.OwnedExDeadlineError, "OWNED_EX_DEADLINE_INVALID_ARGUMENT")
        equal_token = deadline.Namespace3Deadline(actual_roots.token.absolute_monotonic)
        foreign = owned._new_owned_ex_binding(OPERATION, parent_token=actual_roots.parent,
                    effective_token=equal_token, scope_identity=object())
        with monkeypatch.context() as patch:
            native_traps(patch)
            caught_error(lambda: lifecycle._new_owned_lock_journal(foreign, root_ordinal=0,
                stream_owner=current.stream_owner, directory_owner=current.directory), owned.OwnedExDeadlineError,
                "OWNED_EX_DEADLINE_INVALID_ARGUMENT")
        assert current.lock.attempt(True) is True
    assert lock_facts(current.lock, normal=True).unlock_state == "unlocked"
    assert directory_facts(current.directory, normal=True).handle_states == ("closed",)


def test_controlled_foreign_legal_handle_rejected_before_lock_without_native_debt(actual_roots, monkeypatch):
    selected, calls = [], Counter()
    with io.FileIO(safe._native_path(actual_roots.root / lifecycle.LOCK_NAME), "rb") as fixture_file:
        fixture_handle = safe.msvcrt.get_osfhandle(fixture_file.fileno())
        assert type(fixture_handle) is int and 0 < fixture_handle < ctypes.c_void_p(-1).value

        def get_handle(_fd):
            assert fixture_handle != selected[0].stream_owner._handle[0]
            calls["foreign_return"] += 1
            return fixture_handle

        def invoke():
            with resources(actual_roots) as current:
                selected.append(current)
                with monkeypatch.context() as patch:
                    patch.setattr(lifecycle, "_owned_lock_get_osfhandle", get_handle)
                    patch.setattr(lifecycle, "_owned_lock_lock_file", forbidden)
                    patch.setattr(lifecycle, "_owned_lock_unlock_file", forbidden)
                    current.lock.attempt(True)

        first = caught_error(invoke, owned.OwnedExDeadlineError, "OWNED_EX_DEADLINE_INVALID_RESULT")
        current = selected[0]
        value = lock_facts(current.lock, failed=True)
        assert value.acquire_native_attempted is False and value.acquire_attempts == 0
        assert value.acquire_state == "not_started" and value.unlock_state == "not_owed"
        assert value.unlock_owed is value.unlock_native_attempted is False
        same_error(current.lock.release, first)
        assert calls == {"foreign_return": 1} and current.lock.owned_lock_receipt == value
        # Independent known fixture file is still owned/alive; no UNKNOWN probe.
        assert fixture_file.closed is False and fixture_file.read() == lifecycle._MARKER
        assert current.stream_owner.close_receipt.fd_state == "closed"
        assert current.directory.owned_close_receipt.handle_states == ("closed",)


@pytest.mark.parametrize("kind", ["zero-int", "one-int", "foreign-bool-trap"])
def test_controlled_exclusive_argument_requires_exact_bool_without_alien_truth_or_native(actual_roots, monkeypatch, kind):
    selected, calls = [], Counter()

    class Foreign:
        def __bool__(self):
            calls["alien_bool"] += 1
            pytest.fail("opted exclusive argument must not invoke alien truthiness")

    exclusive = 0 if kind == "zero-int" else (1 if kind == "one-int" else Foreign())

    def invoke():
        with resources(actual_roots) as current:
            selected.append(current)
            # A premature builtin fileno would now raise before the required
            # argument error; wrappers still borrow the known owner FD.
            assert io.BufferedReader.close(current.stream) is None
            assert current.stream.raw.closefd is False
            with monkeypatch.context() as patch:
                patch.setattr(lifecycle, "_owned_lock_get_osfhandle", forbidden)
                patch.setattr(lifecycle, "_owned_lock_lock_file", forbidden)
                patch.setattr(lifecycle, "_owned_lock_unlock_file", forbidden)
                current.lock.attempt(exclusive)

    first = caught_error(invoke, owned.OwnedExDeadlineError, "OWNED_EX_DEADLINE_INVALID_ARGUMENT")
    current = selected[0]
    value = lock_facts(current.lock, failed=True)
    assert value.acquire_expression_started is value.acquire_native_attempted is False
    assert value.acquire_attempts == 0 and value.unlock_owed is value.unlock_native_attempted is False
    assert value.unlock_state == "not_owed" and calls == {}
    same_error(current.lock.release, first)
    assert current.stream_owner.close_receipt.fd_state == "closed"
    assert current.directory.owned_close_receipt.handle_states == ("closed",)


def test_actual_live_default_directory_cannot_be_opted_reinitialized_or_orphan_handle(actual_roots, monkeypatch):
    directory = safe._Directory(actual_roots.root, create=False, base=actual_roots.root)
    assert directory.__enter__() is directory and directory.owned_close_receipt is None
    journal = safe._new_owned_directory_journal(actual_roots.binding, root_ordinal=0)
    original_handles, original_identities = directory.handles, directory.identities
    original_path, original_base = directory.path, directory.base
    values = tuple(original_handles)
    calls, real_close = Counter(), safe._kernel.CloseHandle
    first = None
    try:
        assert len(values) == 1
        with monkeypatch.context() as patch:
            native_traps(patch)
            caught_error(lambda: safe._Directory.__init__(directory, actual_roots.root,
                create=False, base=actual_roots.root, _owned_close_journal=journal),
                owned.OwnedExDeadlineError, "OWNED_EX_DEADLINE_CLOSED")
        assert directory.handles is original_handles and tuple(directory.handles) == values
        assert directory.identities is original_identities
        assert directory.path is original_path and directory.base is original_base
        assert directory._known_closed is False and directory.owned_close_receipt is None
        assert journal._owner is None and journal._phase == "new"
        assert journal._slot._state == "empty" and journal._slot._close_consumed is False
        assert directory.verify() is None
    except BaseException as failure:
        first = failure
        raise
    finally:
        def close(handle):
            assert handle in values
            result = legal_bool(real_close(handle))
            calls["legacy_close"] += 1
            return result

        with monkeypatch.context() as patch:
            patch.setattr(safe._kernel, "CloseHandle", close)
            assert directory.__exit__(None if first is None else type(first), first, None) is None
    assert calls == {"legacy_close": 1} and directory.handles == []
    assert directory.owned_close_receipt is None and journal._owner is None


def test_actual_acquired_default_lock_cannot_be_opted_reinitialized_or_lose_unlock_debt(actual_roots, monkeypatch):
    calls, real_unlock = Counter(), safe._kernel.UnlockFileEx
    with resources(actual_roots) as current:
        with lifecycle._open_lock(current.directory) as legacy_stream:
            legacy = lifecycle._FileLock(current.directory, legacy_stream)
            assert legacy.attempt(True) is True and legacy.owned_lock_receipt is None
            journal = lifecycle._new_owned_lock_journal(actual_roots.binding, root_ordinal=0,
                stream_owner=current.stream_owner, directory_owner=current.directory)
            original_stream, original_directory = legacy.stream, legacy.directory
            original_identity, original_overlap = legacy.identity, legacy.overlapped
            try:
                with monkeypatch.context() as patch:
                    native_traps(patch)
                    caught_error(lambda: lifecycle._FileLock.__init__(legacy, current.directory, current.stream,
                        _owned_lock_journal=journal), owned.OwnedExDeadlineError, "OWNED_EX_DEADLINE_CLOSED")
                assert legacy.stream is original_stream and legacy.directory is original_directory
                assert legacy.identity is original_identity and legacy.overlapped is original_overlap
                assert legacy.locked is True and legacy.owned_lock_receipt is None
                assert journal._owner is None and journal._phase == "new"
                assert journal._unlock_owed is journal._unlock_consumed is False
                assert lifecycle._original_lock_dispatch((legacy,)) is True and legacy.verify() is None
            finally:
                def unlock(*args):
                    result = legal_bool(real_unlock(*args))
                    calls["legacy_unlock"] += 1
                    return result

                with monkeypatch.context() as patch:
                    patch.setattr(safe._kernel, "UnlockFileEx", unlock)
                    assert legacy.release() is None
            assert legacy.locked is False and legacy.owned_lock_receipt is None
            assert journal._owner is None and calls == {"legacy_unlock": 1}
    assert current.stream_owner.close_receipt.fd_state == "closed"
    assert current.directory.owned_close_receipt.handle_states == ("closed",)
