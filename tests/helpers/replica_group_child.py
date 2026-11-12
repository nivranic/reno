"""Fixed isolated G1 process scenarios; not a product entry or reset interface."""
import json
import importlib.util
import asyncio
from concurrent.futures import ThreadPoolExecutor
import gc
import os
from pathlib import Path
import re
import sys
import threading
import weakref


REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY))


def install_audit():
    # Audit hooks do not survive exec. Both the Root runner and this child use
    # this one fixed reviewed file; there is no caller path or enable switch.
    path = Path(__file__).with_name("replica_group_native_test_audit.py")
    spec = importlib.util.spec_from_file_location("replica_group_native_test_audit", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("The fixed native-test audit module is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.install()


def emit(value):
    print(json.dumps(value, sort_keys=True), flush=True)


def legacy_holder(root, payload):
    from reno import storage_lifecycle as lifecycle
    fields = ("database", "media", "reports", "checkpoints")
    if type(payload) is not dict or set(payload) != set(fields):
        raise ValueError("Invalid synthetic profile")
    paths = {}
    for field in fields:
        if type(payload[field]) is not str:
            raise ValueError("Profile paths must be exact strings")
        path = Path(payload[field])
        if not path.is_absolute() or ".." in path.parts or not path.is_relative_to(root):
            raise ValueError("Profile escaped its isolated root")
        paths[field] = path
    value = lifecycle.StorageProfile(**paths)
    with lifecycle.shared(value, timeout=3) as guard:
        assert lifecycle.require_guard(value) is guard
        emit({"state": "held", "pid": os.getpid(), "ppid": os.getppid(), "exclusive": False})
        if sys.stdin.readline() != "release\n":
            raise ValueError("Missing original parent release command")
    assert lifecycle.current_guard() is None
    emit({"state": "released", "pid": os.getpid(), "ppid": os.getppid()})


UNKNOWN_CASES = frozenset({
    "acquire-after-return", "allocator-hostile-exception", "fd-close-raised", "fd-close-non-none",
    "fd-close-with-body-first", "fd-close-unlisted-code",
    "handle-close-false", "transfer-after-return", "duplicate-false-nonnull",
})
KNOWN_CASES = frozenset({"cleanup-bookkeeping-raised", "ancillary-error-create", "ancillary-error-duplicate"})


def unknown_outcome(root, case):
    """One process poison per real-resource fault; no state reset or re-close."""
    from reno import personal_authority_replica_group as group
    from reno import storage_lifecycle as lifecycle
    if os.name != "nt" and case in {"handle-close-false", "transfer-after-return", "duplicate-false-nonnull"}:
        raise ValueError("This fixed scenario requires Windows")

    def profile(directory):
        return lifecycle.StorageProfile(directory / "data/reno.db", directory / "media",
                                        directory / "reports", directory / "checkpoints")

    value, independent = profile(root / "fault"), profile(root / "independent")
    lifecycle.initialize(value)
    lifecycle.initialize(independent)
    ready, release = threading.Event(), threading.Event()

    def already_held():
        with group.checked_exclusive_many((independent,)) as token:
            assert lifecycle.require_guard(independent, exclusive=True) is group.guard_original(token)
            ready.set()
            if not release.wait(45):
                raise AssertionError("Original held group was not released")
        observed = group.consume_closed_original(token)
        assert observed["native_normal_closed"] is True
        assert observed["native_resources_acquired"] == observed["native_resources_normal_closed"] > 0
        return True

    originals, calls = {}, {"fault": 0}
    target_fd, target_handle = [None], [None]
    directory_handles = []
    close_fd, close_handle = [], []
    references = {}
    hooks = []

    class HostileNativeFailure(BaseException):
        @property
        def __class__(self):
            hooks.append("class")
            raise AssertionError("Native failure dispatched caller __class__")
        def __str__(self):
            hooks.append("str")
            raise AssertionError("Native failure dispatched caller __str__")

    injected_error = (HostileNativeFailure() if case == "allocator-hostile-exception" else
                      RuntimeError("synthetic uncertainty after a real native return"))
    body_error = (group.AuthorityReplicaGroupError("UNLISTED_CODE") if case == "fd-close-unlisted-code" else
                  RuntimeError("synthetic body first before uncertain close"))
    armed = [case in {"acquire-after-return", "allocator-hostile-exception", "transfer-after-return", "duplicate-false-nonnull"}]

    def install(name, replacement):
        originals.setdefault(name, getattr(group, name))
        setattr(group, name, replacement)

    saved_fd_close = group._OS_CLOSE

    def fd_closer(descriptor):
        close_fd.append(descriptor)
        result = saved_fd_close(descriptor)
        if armed[0] and descriptor == target_fd[0] and calls["fault"] == 0 and case.startswith("fd-close"):
            calls["fault"] += 1
            if case == "fd-close-non-none":
                return False
            raise injected_error
        return result

    def configure():
        install("_OS_CLOSE", fd_closer)
        if os.name == "nt":
            saved_handle_close = group._WIN_CLOSE_HANDLE
            saved_create = group._WIN_CREATE_FILEW

            def observe_directory_returns(*args):
                result = saved_create(*args)
                if args[5] & 0x02000000 and type(result) is int and result > 0 and result != group._INVALID_HANDLE:
                    directory_handles.append(result)
                return result

            install("_WIN_CREATE_FILEW", observe_directory_returns)

            def handle_closer(handle):
                close_handle.append(handle)
                result = saved_handle_close(handle)
                if armed[0] and handle == target_handle[0] and calls["fault"] == 0 and case == "handle-close-false":
                    calls["fault"] += 1
                    return 0
                return result

            install("_WIN_CLOSE_HANDLE", handle_closer)
        if case in {"acquire-after-return", "allocator-hostile-exception"}:
            name = "_WIN_CREATE_FILEW" if os.name == "nt" else "_OS_OPEN"
            allocator = getattr(group, name)

            def allocate(*args, **kwargs):
                result = allocator(*args, **kwargs)
                if calls["fault"] == 0:
                    (target_handle if os.name == "nt" else target_fd)[0] = result
                    calls["fault"] += 1
                    raise injected_error
                return result

            install(name, allocate)
        elif case == "transfer-after-return":
            transfer = group._CRT_OPEN_OSFHANDLE

            def transfer_unknown(handle, flags):
                result = transfer(handle, flags)
                if calls["fault"] == 0:
                    target_handle[0], target_fd[0] = handle, result
                    calls["fault"] += 1
                    raise injected_error
                return result

            install("_CRT_OPEN_OSFHANDLE", transfer_unknown)
        elif case == "duplicate-false-nonnull":
            duplicate = group._WIN_DUPLICATE_HANDLE

            def ambiguous_duplicate(*args):
                result = duplicate(*args)
                assert result
                if calls["fault"] == 0:
                    target_handle[0] = args[3]._obj.value
                    assert type(target_handle[0]) is int and target_handle[0] > 0
                    calls["fault"] += 1
                    # Actual native allocation, deliberately ambiguous reported
                    # outcome. Never fabricate or free a made-up pointer.
                    return 0
                return result

            install("_WIN_DUPLICATE_HANDLE", ambiguous_duplicate)

    class Callback:
        def __call__(self):
            pass

    async def produce_unknown():
        references["task"] = weakref.ref(asyncio.current_task())
        callback = Callback()
        references["callback"] = weakref.ref(callback)
        token = None
        failure = None
        try:
            with group.checked_exclusive_many((value,), check_cancelled=callback) as token:
                guard = group.guard_original(token)
                references["guard"] = weakref.ref(guard)
                target_fd[0] = guard._locks[-1].stream.fileno()
                if os.name == "nt":
                    # Last successful directory CreateFile is the final root's
                    # persistent HANDLE; views must not expose its holder.
                    target_handle[0] = directory_handles[-1]
                # Descriptor/HANDLE numbers can have been reused by earlier
                # completed width probes. Count only this held resource epoch.
                close_fd.clear()
                close_handle.clear()
                armed[0] = True
                if case in {"fd-close-with-body-first", "fd-close-unlisted-code"}:
                    raise body_error
            raise AssertionError("Unknown outcome produced a successful group")
        except BaseException as error:
            failure = error
        assert calls["fault"] == 1
        if case in {"fd-close-with-body-first", "fd-close-unlisted-code"}:
            assert failure is body_error
        elif case in {"acquire-after-return", "allocator-hostile-exception", "fd-close-raised", "transfer-after-return"}:
            assert failure is injected_error
        else:
            assert isinstance(failure, (group.AuthorityReplicaGroupError, OSError))
        assert lifecycle.current_guard() is None
        if token is not None:
            try:
                group.consume_closed_original(token)
            except group.AuthorityReplicaGroupError:
                pass
            else:
                raise AssertionError("Uncertain close acquired normal history")
        # The test's caught exception, unlike a tombstone, legitimately carries
        # traceback frames. Drop only those test-owned references before asking
        # whether the product's detached debt accidentally retains Task/callback.
        for error in (failure, injected_error, body_error):
            if issubclass(type(error), BaseException):
                error.__traceback__ = None
                error.__context__ = None
                error.__cause__ = None

    with ThreadPoolExecutor(max_workers=1) as pool:
        held = pool.submit(already_held)
        if not ready.wait(15):
            release.set()
            held.result(timeout=15)
            raise AssertionError("Independent group did not become held")
        configure()
        try:
            asyncio.run(produce_unknown())
        finally:
            for name, original in originals.items():
                setattr(group, name, original)
            release.set()
        assert held.result(timeout=15) is True

    gc.collect()
    assert references["task"]() is None and references["callback"]() is None
    if "guard" in references:
        assert references["guard"]() is None
    assert group._POISONED is True and group._TOMBSTONES and group._DEBTS
    assert group._LIVE == {} and hooks == []
    for debt in group._DEBTS:
        assert set(debt) == {"scope_id", "serial", "kind", "raw", "closer_state"}
        assert debt["raw"] is None or type(debt["raw"]) is int
        assert type(debt["scope_id"]) is str and type(debt["serial"]) is int
    for tombstone in group._TOMBSTONES:
        assert tombstone["status"] == "unsettled"
        if case == "fd-close-unlisted-code":
            assert tombstone["failure_code"] == "GROUP_FAILED"
        for field in ("directory_bindings", "lock_bindings"):
            for binding in tombstone[field]:
                identity = binding["identity"]
                if identity is not None:
                    assert type(identity) is dict and set(identity) == {"device", "inode"}
                    assert all(type(value) is str and value.isascii() and value.isdecimal() for value in identity.values())
                    assert int(identity["inode"]) > 0
        reference = tombstone["owner_task_weak"]
        assert reference is None or isinstance(reference, weakref.ReferenceType) and reference() is None
    if case.startswith("fd-close"):
        assert close_fd.count(target_fd[0]) == 1
    elif case == "handle-close-false":
        assert close_handle.count(target_handle[0]) == 1
    else:
        if target_fd[0] is not None:
            assert close_fd.count(target_fd[0]) == 0
        if target_handle[0] is not None:
            assert close_handle.count(target_handle[0]) == 0

    followup_calls = []
    followup_originals = {}

    def forbidden_native(*args, **kwargs):
        followup_calls.append(True)
        raise AssertionError("Poisoned G1 touched native state")

    names = ("_OS_OPEN", "_OS_STAT", "_OS_FSTAT", "_OS_READ", "_OS_LSEEK", "_OS_CLOSE")
    if os.name == "nt":
        names += ("_WIN_CREATE_FILEW", "_WIN_DUPLICATE_HANDLE", "_WIN_CLOSE_HANDLE", "_CRT_OPEN_OSFHANDLE")
    for name in names:
        followup_originals[name] = getattr(group, name)
        setattr(group, name, forbidden_native)

    def blocked(profiles):
        try:
            with group.checked_exclusive_many(profiles):
                raise AssertionError("Process poison admitted another group")
        except group.AuthorityReplicaGroupError as error:
            assert error.code == "AUTHORITY_REPLICA_GROUP1_UNSETTLED"
        else:
            raise AssertionError("Process poison was not reported")

    try:
        blocked((value,))
        blocked((profile(root / "new-uninitialized-roots"),))
        blocked(None)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(blocked, (value,)).result(timeout=10)

        async def next_task():
            blocked((value,))

        asyncio.run(next_task())
    finally:
        for name, original in followup_originals.items():
            setattr(group, name, original)
    assert followup_calls == [] and group._POISONED is True
    assert lifecycle.current_guard() is None
    emit({
        "scenario": case, "fault_injected": True, "fault_calls": calls["fault"],
        "normal_history": False, "poisoned": True, "debt_retained": True,
        "followup_native_calls": len(followup_calls), "same_owner_blocked": True,
        "other_thread_blocked": True, "other_task_blocked": True,
        "other_roots_blocked": True, "invalid_parameters_blocked": True,
        "task_released": True, "callback_released": True,
        "no_closer_retry": True, "current_reset": True,
        "first_preserved": True, "already_held_group_closed": True,
    })


def known_cleanup_fault(root, case):
    """Isolate a regression that would leak a resource if bookkeeping fails."""
    import pytest
    from reno import personal_authority_replica_group as group
    from reno import storage_lifecycle as lifecycle
    from tests.test_personal_authority_replica_group import native_return_trace, make_profile, close_successfully, no_history
    if case != "cleanup-bookkeeping-raised" and os.name != "nt":
        raise ValueError("This fixed ancillary scenario requires Windows")
    value = make_profile(root / "known-failure")
    lifecycle.initialize(value)
    first = RuntimeError("synthetic ancillary or cleanup bookkeeping failure")
    calls, armed = [0], [False]
    failure, token = None, None
    with pytest.MonkeyPatch.context() as patch:
        with native_return_trace(patch) as trace:
            with patch.context() as faults:
                if case == "cleanup-bookkeeping-raised":
                    original_close = group._close

                    def after_real_close(record, holder):
                        result = original_close(record, holder)
                        if record.phase in {"closing", "failed_cleanup"} and holder.state == "closed" and calls[0] == 0:
                            calls[0] += 1
                            raise first
                        return result

                    faults.setattr(group, "_close", after_real_close)
                else:
                    name = "_WIN_CREATE_FILEW" if case == "ancillary-error-create" else "_WIN_DUPLICATE_HANDLE"
                    original_native = getattr(group, name)
                    original_last_error = group.ctypes.get_last_error

                    def after_success(*args, **kwargs):
                        result = original_native(*args, **kwargs)
                        armed[0] = True
                        return result

                    def ancillary_error():
                        if armed[0] and calls[0] == 0:
                            calls[0] += 1
                            raise first
                        return original_last_error()

                    faults.setattr(group, name, after_success)
                    faults.setattr(group.ctypes, "get_last_error", ancillary_error)
                try:
                    with group.checked_exclusive_many((value,)) as token:
                        pass
                except BaseException as error:
                    failure = error
            assert trace["acquired"][0] > 0 and trace["active"] == {}
            assert trace["acquired"] == trace["closed"]
    assert lifecycle.current_guard() is None and group._LIVE == {} and group._POISONED is False
    if calls[0]:
        assert failure is first
        if token is not None:
            no_history(token)
    else:
        assert case != "cleanup-bookkeeping-raised" and failure is None
        observed = group.consume_closed_original(token)
        assert observed["native_normal_closed"] is True
    close_successfully((value,))
    emit({"scenario": case, "fault_calls": calls[0], "all_real_resources_closed": True,
          "poisoned": False, "live_empty": True, "first_preserved": True,
          "fresh_group_succeeded": True})


def admitted_root(value):
    """Require the retained runner tree or the current isolated pytest tree."""
    configured = os.environ.get("RENO_QA_ROOT")
    if type(configured) is not str or not configured:
        raise ValueError("The isolated runner QA root is required")
    qa, root = Path(configured), Path(value)
    if not qa.is_absolute() or ".." in qa.parts or qa.resolve(strict=True) != qa:
        raise ValueError("QA root is not the original canonical runner directory")
    fixed = REPOSITORY / "data/t16-h02-replica-group-r748"
    retained = (qa.name == "isolated" and re.fullmatch(r"run-[0-9]{2}", qa.parent.name) is not None
        and qa.parent.parent == fixed)
    if retained:
        pytest_root = qa / "pytest"
    else:
        from reno import config
        if not config.is_isolated_qa():
            raise ValueError("QA root is not the current isolated pytest tree")
        pytest_root = qa
    if (not root.is_absolute() or ".." in root.parts or root == pytest_root
            or not root.is_relative_to(pytest_root)
            or root.resolve(strict=True) != root or not root.is_dir()):
        raise ValueError("Scenario root escaped the current retained pytest tree")
    return root


def main():
    install_audit()
    if len(sys.argv) not in (3, 4):
        raise ValueError("Expected one fixed isolated scenario")
    root = admitted_root(sys.argv[2])
    if sys.argv[1] == "legacy-holder" and len(sys.argv) == 4:
        legacy_holder(root, json.loads(sys.argv[3]))
    elif sys.argv[1] in UNKNOWN_CASES and len(sys.argv) == 3:
        unknown_outcome(root, sys.argv[1])
    elif sys.argv[1] in KNOWN_CASES and len(sys.argv) == 3:
        known_cleanup_fault(root, sys.argv[1])
    else:
        raise ValueError("Unknown fixed isolated scenario")


if __name__ == "__main__":
    main()
