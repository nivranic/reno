"""Independent readonly-directory tests with real Windows resources.

Only the new module's declared consumer aliases and local clock are replaced.
Finite failures are synthetic; fixture-owned recovery of unknown transfer is
separate from subject cleanup and never establishes subject normal close.
"""
import ctypes
import errno
import hashlib
import io
import os
import shutil
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from reno import personal_authority_directory_deadline as subject
from reno import personal_authority_namespace3_deadline as deadline
from reno import sync_media as safe
from reno.sync_media import SyncError


pytestmark = pytest.mark.skipif(os.name != "nt", reason="actual Windows readonly HANDLE and CRT fixture")


def native(path):
    text = os.path.abspath(os.fspath(path))
    if text.startswith("\\\\?\\"):
        return text
    return "\\\\?\\UNC\\" + text[2:] if text.startswith("\\\\") else "\\\\?\\" + text


class Clock:
    def __init__(self, value=0.0):
        self.value = value
    def monotonic(self):
        return self.value


@pytest.fixture
def clock(monkeypatch):
    value = Clock()
    monkeypatch.setattr(subject, "time", SimpleNamespace(monotonic=value.monotonic))
    return value


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "existing"
    child = root / "浙江绍兴"
    deep = child / "防水材料未定"
    os.makedirs(native(deep))
    payload = bytes(range(256)) * 79 + "绍兴\x00\r\n防水未定".encode() + b"EOF-sentinel"
    files = {root / "node-identity.json": payload,
             child / "中文资料.bin": payload[::-1], deep / "inner.bin": b"actual-inner"}
    for path, raw in files.items():
        with open(native(path), "wb") as stream:
            stream.write(raw)
    return {"root": root, "child": child, "deep": deep, "files": files,
            "file": root / "node-identity.json", "payload": payload}


def tree(env):
    return {str(path.relative_to(env["root"])): Path(native(path)).read_bytes()
            for path in env["files"]}


def directory(env, token=None, **options):
    return subject.DeadlineDirectory(env["root"], base=env["root"],
        deadline_token=token if token is not None else deadline.Namespace3Deadline(15.0), **options)


def error(call, suffix):
    with pytest.raises(subject.DirectoryDeadlineError) as raised:
        call()
    assert raised.value.code == "DIRECTORY_DEADLINE_" + suffix
    return raised.value


def unsafe(call):
    with pytest.raises(SyncError) as raised:
        call()
    assert raised.value.code == "SYNC_MEDIA_UNSAFE_PATH"
    return raised.value


def read_exact(stream, expected):
    assert type(stream) is io.BufferedReader
    assert type(stream.raw) is io.FileIO and stream.raw.closefd is False
    fd = stream.fileno()
    assert fd == stream.raw.fileno()
    pieces = []
    while True:
        piece = stream.raw.read(4096)
        assert stream.fileno() == stream.raw.fileno() == fd
        if not piece:
            break
        pieces.append(piece)
    raw = b"".join(pieces)
    assert raw == expected and len(raw) == len(expected)
    assert hashlib.sha256(raw).digest() == hashlib.sha256(expected).digest()
    assert stream.raw.read(1) == b""
    assert stream.raw.seek(0) == 0 and stream.raw.read() == expected
    assert stream.raw.read(1) == b"" and stream.raw.fileno() == fd
    return fd


def closure_ledger(monkeypatch):
    ledger = {"handles": [], "fds": [], "order": []}
    close_handle, close_fd = subject._close_handle, subject._close_fd
    def handle(value):
        result = close_handle(value)
        ledger["handles"].append(value)
        ledger["order"].append("handle")
        return result
    def fd(value):
        result = close_fd(value)
        ledger["fds"].append(value)
        ledger["order"].append("fd")
        return result
    monkeypatch.setattr(subject, "_close_handle", handle)
    monkeypatch.setattr(subject, "_close_fd", fd)
    return ledger


def test_token_clock_and_flags_rejected_before_any_resource(env, clock, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid input acquired a resource")
    monkeypatch.setattr(subject, "_create_file", forbidden)
    class Child(deadline.Namespace3Deadline):
        pass
    class FalseFlag:
        def __bool__(self):
            return False
    missing = object.__new__(deadline.Namespace3Deadline)
    invalid = object.__new__(deadline.Namespace3Deadline)
    object.__setattr__(invalid, "absolute_monotonic", float("nan"))
    for token in (None, 15.0, True, Child(15.0), missing, invalid):
        error(lambda token=token: subject.DeadlineDirectory(env["root"], deadline_token=token), "INVALID_ARGUMENT")
    for value in (True, 0, None, FalseFlag()):
        error(lambda value=value: directory(env, create=value), "INVALID_ARGUMENT")
    for value in (float("nan"), float("inf"), float("-inf"), -1.0, True, 1):
        clock.value = value
        error(lambda: directory(env).__enter__(), "INVALID_ARGUMENT")
    assert tree(env) == {str(path.relative_to(env["root"])): raw for path, raw in env["files"].items()}


def test_absolute_base_parent_and_single_use_state(env, clock):
    token = deadline.Namespace3Deadline(15.0)
    error(lambda: subject.DeadlineDirectory(Path("relative"), deadline_token=token), "INVALID_ARGUMENT")
    error(lambda: subject.DeadlineDirectory(env["root"], base=env["root"] / "outside", deadline_token=token), "INVALID_ARGUMENT")
    root = directory(env, token)
    for call in (root.verify, lambda: root.entry_stat("node-identity.json"), lambda: root.open_file("node-identity.json")):
        error(call, "CLOSED")
    error(lambda: subject.DeadlineDirectory(env["child"], parent=root, deadline_token=token), "CLOSED")
    error(lambda: subject.DeadlineDirectory(env["child"], parent=SimpleNamespace(path=env["root"]), deadline_token=token), "INVALID_ARGUMENT")
    with root:
        for kwargs in ({"path": root.path, "deadline_token": token},
                       {"path": env["deep"], "deadline_token": token},
                       {"path": env["child"], "deadline_token": deadline.Namespace3Deadline(15.0)}):
            error(lambda kwargs=kwargs: subject.DeadlineDirectory(parent=root, **kwargs), "INVALID_ARGUMENT")
        for field in ("path", "base", "parent", "deadline_token"):
            with pytest.raises(AttributeError):
                setattr(root, field, getattr(root, field))
        for name in ("names", "unlink", "mkdir", "close"):
            assert not hasattr(root, name)
    error(root.__enter__, "CLOSED")
    error(root.verify, "CLOSED")
    error(lambda: subject.DeadlineDirectory(env["child"], parent=root, deadline_token=token), "CLOSED")


def test_file_flags_names_and_factory_do_not_acquire(env, clock, monkeypatch):
    with directory(env) as root:
        def forbidden(*args, **kwargs):
            pytest.fail("file factory acquired a HANDLE")
        monkeypatch.setattr(subject, "_create_file", forbidden)
        for field in ("write", "create", "lock"):
            for value in (True, 0, None):
                error(lambda field=field, value=value: root.open_file("node-identity.json", **{field: value}), "INVALID_ARGUMENT")
        for name in ("", ".", "..", "other/name", "other\\name", "C:bad", "bad\x00name", 1):
            unsafe(lambda name=name: root.entry_stat(name))
            unsafe(lambda name=name: root.open_file(name))
        lease = root.open_file("node-identity.json")
        assert type(lease) is subject.DeadlineReadonlyLease


def test_actual_read_same_fd_eof_and_wrappers_do_not_close_fd(env, clock, monkeypatch):
    before = tree(env)
    ledger = closure_ledger(monkeypatch)
    # Naturally obtain r822's actual clipped token; do not replace its clock/Budget.
    budget = deadline._RestrictedBudget(deadline.Namespace3Deadline(1.0e15))
    effective = budget.effective_deadline
    with directory(env, effective) as root:
        assert root.deadline_token is budget.effective_deadline is effective
        with root.open_file("node-identity.json") as stream:
            fd = read_exact(stream, env["payload"])
            stream.close()
            assert stream.closed and stream.raw.closed
            assert os.fstat(fd).st_size == len(env["payload"])
            assert ledger["fds"] == []
        assert ledger["fds"] == [fd]
        with pytest.raises(OSError) as closed:
            os.fstat(fd)
        assert closed.value.errno == errno.EBADF
        root.verify()
    assert len(ledger["handles"]) == 1 and tree(env) == before


def test_parent_child_same_effective_token_does_not_refresh(env, clock):
    token = deadline.Namespace3Deadline(10.0)
    clock.value = 1.0
    with pytest.raises(subject.DirectoryDeadlineError) as outer:
        with directory(env, token) as root:
            clock.value = 9.0
            with subject.DeadlineDirectory(env["child"], parent=root, deadline_token=token) as child:
                assert root.deadline_token is child.deadline_token is token
                with child.open_file("中文资料.bin") as stream:
                    read_exact(stream, env["files"][env["child"] / "中文资料.bin"])
            root.verify()
            assert token.absolute_monotonic == 10.0
            clock.value = 10.0
            error(root.verify, "DEADLINE")
    assert outer.value.code == "DIRECTORY_DEADLINE_DEADLINE"


def test_actual_deep_chinese_existing_path(tmp_path, clock):
    path = tmp_path / "浙江" / "绍兴" / "防水材料未定"
    while len(str(path)) <= 280:
        path = path / "真实中文目录防水材料未定"
    try:
        os.makedirs(native(path))
        file = path / "原字节.bin"
        payload = "绍兴实际深层读取".encode() * 701 + b"\x00EOF"
        with open(native(file), "wb") as sink:
            sink.write(payload)
        assert Path(native(file)).read_bytes() == payload
    except OSError:
        pytest.skip("actual extended-length fixture unavailable")
    assert path.is_absolute() and len(str(path)) > 260
    with subject.DeadlineDirectory(path, deadline_token=deadline.Namespace3Deadline(15.0)) as held:
        with held.open_file(file.name) as stream:
            read_exact(stream, payload)
    assert Path(native(file)).read_bytes() == payload


def test_missing_existing_paths_preserve_original_exception(env, clock, monkeypatch):
    with pytest.raises(FileNotFoundError):
        with subject.DeadlineDirectory(env["root"] / "missing", deadline_token=deadline.Namespace3Deadline(15.0)):
            pytest.fail("missing directory was created")
    assert not os.path.exists(native(env["root"] / "missing"))
    primary = FileNotFoundError(errno.ENOENT, "synthetic missing entry")
    with directory(env) as root:
        original = subject._stat
        def stat(path, *args, **kwargs):
            if str(path).endswith("missing-file"):
                raise primary
            return original(path, *args, **kwargs)
        monkeypatch.setattr(subject, "_stat", stat)
        with pytest.raises(FileNotFoundError) as raised:
            root.entry_stat("missing-file")
        assert raised.value is primary


def test_nonregular_refused_before_file_acquire(env, clock, monkeypatch):
    with directory(env) as root:
        info = root.entry_stat(env["child"].name)
        assert os.path.isdir(native(env["child"])) and info.st_file_attributes & 0x10
        def forbidden(*args, **kwargs):
            pytest.fail("unsafe prestats reached file acquire")
        monkeypatch.setattr(subject, "_create_file", forbidden)
        unsafe(lambda: root.open_file(env["child"].name).__enter__())


def test_actual_hardlinks_refused_before_file_acquire(env, clock, monkeypatch):
    link = env["root"] / "hardlink.bin"
    try:
        os.link(native(env["file"]), native(link))
    except OSError:
        pytest.skip("actual hardlink fixture unavailable")
    assert os.stat(native(env["file"])).st_nlink >= 2
    with directory(env) as root:
        def forbidden(*args, **kwargs):
            pytest.fail("unsafe prestats reached file acquire")
        monkeypatch.setattr(subject, "_create_file", forbidden)
        unsafe(lambda: root.open_file(env["file"].name).__enter__())
    assert Path(native(env["file"])).read_bytes() == env["payload"]


def test_actual_junction_refused_without_following(env, clock):
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("PowerShell 7 junction fixture unavailable")
    result = subprocess.run([str(pwsh), "-NoProfile", "-NonInteractive", "-Command",
        "New-Item -ItemType Junction -Path 'junction' -Target '浙江绍兴' -ErrorAction Stop | Out-Null"],
        cwd=env["root"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)
    junction = env["root"] / "junction"
    if result.returncode != 0:
        pytest.skip("actual junction capability unavailable")
    assert os.lstat(native(junction)).st_file_attributes & 0x400
    unsafe(lambda: subject.DeadlineDirectory(junction,
        deadline_token=deadline.Namespace3Deadline(15.0)).__enter__())
    with directory(env) as root:
        assert root.entry_stat("junction").st_file_attributes & 0x400
        unsafe(lambda: root.open_file("junction").__enter__())
    assert tree(env) == {str(path.relative_to(env["root"])): raw for path, raw in env["files"].items()}


def test_directory_does_not_adopt_independent_lease(env, clock, monkeypatch):
    ledger = closure_ledger(monkeypatch)
    with directory(env) as root:
        with root.open_file(env["file"].name) as stream:
            fd = stream.fileno()
            root.__exit__(None, None, None)
            assert ledger["fds"] == [] and os.fstat(fd).st_size == len(env["payload"])
            read_exact(stream, env["payload"])
            error(lambda: root.open_file(env["file"].name), "CLOSED")
    assert ledger["fds"] == [fd] and len(ledger["handles"]) == 1


def test_expired_and_equal_deadline_stop_before_resources(env, clock, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("expired request reached physical action")
    monkeypatch.setattr(subject, "_stat", forbidden)
    monkeypatch.setattr(subject, "_create_file", forbidden)
    for now in (15.0, 16.0):
        clock.value = now
        error(lambda: directory(env).__enter__(), "DEADLINE")


def test_expired_active_directory_and_lease_refuse_physical_actions(env, clock, monkeypatch):
    ledger = closure_ledger(monkeypatch)
    with pytest.raises(subject.DirectoryDeadlineError) as outer:
        with directory(env) as root:
            lease = root.open_file(env["file"].name)
            def forbidden(*args, **kwargs):
                pytest.fail("expired active object reached physical action")
            monkeypatch.setattr(subject, "_stat", forbidden)
            monkeypatch.setattr(subject, "_create_file", forbidden)
            clock.value = 15.0
            for call in (root.verify, lambda: root.entry_stat(env["file"].name),
                         lambda: root.open_file(env["file"].name), lease.__enter__):
                error(call, "DEADLINE")
    assert outer.value.code == "DIRECTORY_DEADLINE_DEADLINE"
    assert len(ledger["handles"]) == 1 and ledger["fds"] == []


@pytest.mark.parametrize("stage", ["directory-open", "directory-info", "file-open", "file-info",
    "fd-transfer", "raw-construction", "buffer-construction", "fd-stat"],
    ids=["dir-open", "dir-info", "file-open", "file-info", "transfer", "raw", "buffer", "fstat"])
def test_actual_success_is_registered_before_post_expiry(env, clock, monkeypatch, stage):
    ledger = closure_ledger(monkeypatch)
    original = {name: getattr(subject, name) for name in
        ("_create_file", "_get_info", "_open_osfhandle", "_file_io", "_buffered_reader", "_fstat")}
    acquired, wrappers = [], []
    file_phase = [False]
    def create(*args, **kwargs):
        value = original["_create_file"](*args, **kwargs)
        acquired.append(value)
        if stage == ("file-open" if file_phase[0] else "directory-open"):
            clock.value = 15.0
        return value
    def info(*args, **kwargs):
        value = original["_get_info"](*args, **kwargs)
        target = acquired[-1] if acquired else None
        if args[0] == target and stage == ("file-info" if file_phase[0] else "directory-info"):
            clock.value = 15.0
        return value
    monkeypatch.setattr(subject, "_create_file", create)
    monkeypatch.setattr(subject, "_get_info", info)
    for alias, target in (("_open_osfhandle", "fd-transfer"), ("_file_io", "raw-construction"),
                          ("_buffered_reader", "buffer-construction"), ("_fstat", "fd-stat")):
        def forward(*args, _alias=alias, _target=target, **kwargs):
            value = original[_alias](*args, **kwargs)
            if _alias in ("_file_io", "_buffered_reader"):
                wrappers.append(value)
            if stage == _target:
                clock.value = 15.0
            return value
        monkeypatch.setattr(subject, alias, forward)
    if stage.startswith("directory"):
        error(lambda: directory(env).__enter__(), "DEADLINE")
        assert acquired and ledger["handles"] == acquired[::-1] and ledger["fds"] == []
    else:
        with pytest.raises(subject.DirectoryDeadlineError) as outer:
            with directory(env) as root:
                acquired.clear()
                ledger["handles"].clear()
                file_phase[0] = True
                error(lambda: root.open_file(env["file"].name).__enter__(), "DEADLINE")
                assert len(acquired) == 1
                if stage in ("file-open", "file-info"):
                    assert ledger["handles"] == acquired and ledger["fds"] == []
                else:
                    assert ledger["handles"] == [] and len(ledger["fds"]) == 1
                assert all(value.closed for value in wrappers)
        assert outer.value.code == "DIRECTORY_DEADLINE_DEADLINE"


def test_expiry_before_transfer_invocation_keeps_known_handle_owner(env, clock, monkeypatch):
    ledger = closure_ledger(monkeypatch)
    with pytest.raises(subject.DirectoryDeadlineError) as outer:
        with directory(env) as root:
            original_create, original_stat = subject._create_file, subject._stat
            acquired = []
            def create(*args, **kwargs):
                value = original_create(*args, **kwargs)
                acquired.append(value)
                return value
            def stat(path, *args, **kwargs):
                value = original_stat(path, *args, **kwargs)
                if acquired and str(path).endswith(env["file"].name):
                    clock.value = 15.0
                return value
            def forbidden(*args, **kwargs):
                pytest.fail("transfer was invoked after prior gate expiry")
            monkeypatch.setattr(subject, "_create_file", create)
            monkeypatch.setattr(subject, "_stat", stat)
            monkeypatch.setattr(subject, "_open_osfhandle", forbidden)
            error(lambda: root.open_file(env["file"].name).__enter__(), "DEADLINE")
            assert ledger["handles"] == acquired and ledger["fds"] == []
    assert outer.value.code == "DIRECTORY_DEADLINE_DEADLINE"


@pytest.mark.parametrize("after_crt", [False, True], ids=["before-real-crt", "after-real-crt-return"])
def test_no_fd_return_is_unknown_with_only_fixture_owned_recovery(env, clock, monkeypatch, after_crt):
    primary = OSError(errno.EIO, "synthetic transfer wrapper failure")
    original_transfer, original_handle_close, original_fd_close = subject._open_osfhandle, subject._close_handle, subject._close_fd
    known = {"handle": None, "fd": None}
    subject_closes = {"handles": [], "fds": []}
    def transfer(handle, flags):
        known["handle"] = handle
        if after_crt:
            known["fd"] = original_transfer(handle, flags)
            assert type(known["fd"]) is int and known["fd"] >= 0
        raise primary
    def close_handle(handle):
        subject_closes["handles"].append(handle)
        return original_handle_close(handle)
    def close_fd(fd):
        subject_closes["fds"].append(fd)
        return original_fd_close(fd)
    def forbidden(*args, **kwargs):
        pytest.fail("unknown transfer was probed or wrapped")
    monkeypatch.setattr(subject, "_open_osfhandle", transfer)
    monkeypatch.setattr(subject, "_close_handle", close_handle)
    monkeypatch.setattr(subject, "_close_fd", close_fd)
    monkeypatch.setattr(subject, "_fstat", forbidden)
    monkeypatch.setattr(subject, "_file_io", forbidden)
    monkeypatch.setattr(subject, "_buffered_reader", forbidden)
    first_failure = None
    try:
        with directory(env) as root:
            lease = root.open_file(env["file"].name)
            with pytest.raises(OSError) as raised:
                lease.__enter__()
            assert raised.value is primary
            assert "DIRECTORY_DEADLINE_TRANSFER_UNKNOWN" in getattr(primary, "__notes__", ())
            assert known["handle"] is not None and subject_closes["fds"] == []
            assert known["handle"] not in subject_closes["handles"]
            try:
                lease.__exit__(None, None, None)
            except BaseException as repeated:
                assert repeated is primary
            assert subject_closes["fds"] == [] and known["handle"] not in subject_closes["handles"]
    except BaseException as failure:
        first_failure = failure
        raise
    finally:
        # A regression's attempted numeric close is unknown: never retry it.
        # Lists are appended before the original closer, not after success.
        untouched = (known["handle"] not in subject_closes["handles"]
                     and not subject_closes["fds"])
        if untouched:
            try:
                # Fixture knows its own injection boundary; not subject cleanup.
                if known["fd"] is not None:
                    original_fd_close(known["fd"])
                elif known["handle"] is not None:
                    if not original_handle_close(known["handle"]):
                        number = ctypes.get_last_error()
                        raise ctypes.WinError(number)
            except BaseException:
                if first_failure is None:
                    raise
                try:
                    BaseException.add_note(first_failure, "R823_FIXTURE_CLEANUP_FAILED")
                except BaseException:
                    pass


@pytest.mark.parametrize("alias", ["_file_io", "_buffered_reader"], ids=["raw", "buffer"])
def test_wrapper_constructor_failure_preserves_primary_and_sole_fd_cleanup(env, clock, monkeypatch, alias):
    primary = RuntimeError("synthetic wrapper construction failure")
    ledger = closure_ledger(monkeypatch)
    with directory(env) as root:
        def fail(*args, **kwargs):
            raise primary
        monkeypatch.setattr(subject, alias, fail)
        with pytest.raises(RuntimeError) as raised:
            root.open_file(env["file"].name).__enter__()
        assert raised.value is primary and len(ledger["fds"]) == 1 and ledger["handles"] == []


@pytest.mark.parametrize("kind", ["ordinary", "keyboard", "system"], ids=["ordinary", "keyboard", "system"])
def test_body_first_survives_expiry_and_actual_cleanup_post_return_faults(env, clock, monkeypatch, kind):
    primary = {"ordinary": RuntimeError("body primary"), "keyboard": KeyboardInterrupt("body primary"),
               "system": SystemExit("body primary")}[kind]
    cleanup = RuntimeError("synthetic cleanup after actual normal return")
    original_fd_close, original_handle_close = subject._close_fd, subject._close_handle
    counts = {"fd": 0, "handle": 0}
    def fd_close(fd):
        original_fd_close(fd)
        counts["fd"] += 1
        raise cleanup
    def handle_close(handle):
        assert original_handle_close(handle)
        counts["handle"] += 1
        raise cleanup
    monkeypatch.setattr(subject, "_close_fd", fd_close)
    monkeypatch.setattr(subject, "_close_handle", handle_close)
    with pytest.raises(type(primary)) as raised:
        with directory(env) as root:
            with root.open_file(env["file"].name):
                clock.value = 15.0
                raise primary
    assert raised.value is primary and counts == {"fd": 1, "handle": 1}
    assert "DIRECTORY_DEADLINE_CLEANUP_FAILED" in getattr(primary, "__notes__", ())


def test_first_close_failure_is_sticky_and_remaining_owned_handles_are_attempted(env, clock, monkeypatch):
    original_close = subject._close_handle
    failures = [RuntimeError("first post-close fault"), RuntimeError("later post-close fault")]
    calls = []
    def close(handle):
        assert original_close(handle)
        calls.append(handle)
        raise failures[0] if len(calls) == 1 else failures[1]
    monkeypatch.setattr(subject, "_close_handle", close)
    root = subject.DeadlineDirectory(env["deep"], base=env["root"], deadline_token=deadline.Namespace3Deadline(15.0))
    root.__enter__()
    with pytest.raises(RuntimeError) as raised:
        root.__exit__(None, None, None)
    assert raised.value is failures[0] and len(calls) == 3
    for call in (lambda: root.__exit__(None, None, None), root.__enter__, root.verify):
        with pytest.raises(RuntimeError) as repeated:
            call()
        assert repeated.value is failures[0]
    assert len(calls) == 3


def test_unknown_fd_close_result_is_not_retried(env, clock, monkeypatch):
    primary = RuntimeError("unknown fd-close post-return fault")
    original_close = subject._close_fd
    calls = []
    def close(fd):
        original_close(fd)
        calls.append(fd)
        raise primary
    monkeypatch.setattr(subject, "_close_fd", close)
    with directory(env) as root:
        lease = root.open_file(env["file"].name)
        stream = lease.__enter__()
        fd = stream.fileno()
        with pytest.raises(RuntimeError) as raised:
            lease.__exit__(None, None, None)
        assert raised.value is primary and stream.closed and stream.raw.closed
        for call in (lambda: lease.__exit__(None, None, None), lease.__enter__):
            with pytest.raises(RuntimeError) as repeated:
                call()
            assert repeated.value is primary
        assert calls == [fd]


@pytest.mark.parametrize("after_retire", [False, True], ids=["before-real-retire", "after-real-retire"])
def test_wrapper_retirement_failure_cannot_skip_sole_fd_close(env, clock, monkeypatch, after_retire):
    primary = RuntimeError("synthetic wrapper retirement post-return fault")
    original = subject._retire_buffer
    ledger = closure_ledger(monkeypatch)
    def retire(buffer):
        if after_retire:
            original(buffer)
        raise primary
    monkeypatch.setattr(subject, "_retire_buffer", retire)
    with directory(env) as root:
        lease = root.open_file(env["file"].name)
        stream = lease.__enter__()
        fd = stream.fileno()
        with pytest.raises(RuntimeError) as raised:
            lease.__exit__(None, None, None)
        assert raised.value is primary and stream.raw.closed and ledger["fds"] == [fd]
        if after_retire:
            assert stream.closed


def test_ordinary_exit_checks_deadline_after_actual_cleanup(env, clock, monkeypatch):
    original_close = subject._close_fd
    closed = []
    def close(fd):
        result = original_close(fd)
        closed.append(fd)
        clock.value = 15.0
        return result
    monkeypatch.setattr(subject, "_close_fd", close)
    with pytest.raises(subject.DirectoryDeadlineError) as outer:
        with directory(env) as root:
            lease = root.open_file(env["file"].name)
            stream = lease.__enter__()
            fd = stream.fileno()
            error(lambda: lease.__exit__(None, None, None), "DEADLINE")
            assert stream.closed and stream.raw.closed and closed == [fd]
    assert outer.value.code == "DIRECTORY_DEADLINE_DEADLINE"


def test_verify_observations_are_fresh_and_native_identity_is_not_redefined(env, clock, monkeypatch):
    with directory(env) as root:
        original_stat, original_info = subject._stat, subject._get_info
        counts = {"stat": 0, "info": 0}
        fault = [False]
        def stat(*args, **kwargs):
            value = original_stat(*args, **kwargs)
            counts["stat"] += 1
            return value
        def info(handle, address):
            value = original_info(handle, address)
            counts["info"] += 1
            if fault[0] and value:
                actual = ctypes.cast(address, ctypes.POINTER(safe._HandleInfo)).contents
                actual.volume ^= 1
            return value
        monkeypatch.setattr(subject, "_stat", stat)
        monkeypatch.setattr(subject, "_get_info", info)
        root.verify()
        first = dict(counts)
        assert first["stat"] > 0 and first["info"] == 1
        root.verify()
        assert counts == {name: value * 2 for name, value in first.items()}
        fault[0] = True
        unsafe(root.verify)
        fault[0] = False
        root.verify()


@pytest.mark.parametrize("kind", ["reparse", "directory", "links"], ids=["reparse", "directory", "links"])
def test_unsafe_native_file_observation_releases_actual_handle(env, clock, monkeypatch, kind):
    ledger = closure_ledger(monkeypatch)
    with directory(env) as root:
        original_create, original_info = subject._create_file, subject._get_info
        file_handles = []
        def create(*args, **kwargs):
            value = original_create(*args, **kwargs)
            file_handles.append(value)
            return value
        def info(handle, address):
            value = original_info(handle, address)
            if value and file_handles and handle == file_handles[-1]:
                actual = ctypes.cast(address, ctypes.POINTER(safe._HandleInfo)).contents
                if kind == "links":
                    actual.links = 2
                else:
                    actual.attributes |= 0x400 if kind == "reparse" else 0x10
            return value
        monkeypatch.setattr(subject, "_create_file", create)
        monkeypatch.setattr(subject, "_get_info", info)
        unsafe(lambda: root.open_file(env["file"].name).__enter__())
        assert len(file_handles) == 1 and ledger["handles"] == file_handles and ledger["fds"] == []


@pytest.mark.parametrize("phase,field", [("path-after-open", "st_ino"), ("path-after-open", "st_mode"),
    ("fd-after-transfer", "st_ino"), ("fd-after-transfer", "st_mode")],
    ids=["path-inode", "path-mode", "fd-inode", "fd-mode"])
def test_same_representation_file_identity_mismatch_is_refused(env, clock, monkeypatch, phase, field):
    ledger = closure_ledger(monkeypatch)
    with directory(env) as root:
        original_create, original_stat, original_fstat = subject._create_file, subject._stat, subject._fstat
        acquired = []
        def changed(value):
            # Explicit rejection observation; never accepted as a success oracle.
            return SimpleNamespace(st_mode=value.st_mode ^ 0o200 if field == "st_mode" else value.st_mode,
                st_nlink=value.st_nlink, st_dev=value.st_dev,
                st_ino=value.st_ino + 1 if field == "st_ino" else value.st_ino, st_size=value.st_size,
                st_mtime_ns=value.st_mtime_ns, st_ctime_ns=value.st_ctime_ns,
                st_file_attributes=getattr(value, "st_file_attributes", 0) or 0)
        def create(*args, **kwargs):
            value = original_create(*args, **kwargs)
            acquired.append(value)
            return value
        def stat(path, *args, **kwargs):
            value = original_stat(path, *args, **kwargs)
            if phase == "path-after-open" and acquired and str(path).endswith(env["file"].name):
                return changed(value)
            return value
        def fstat(fd):
            value = original_fstat(fd)
            return changed(value) if phase == "fd-after-transfer" else value
        monkeypatch.setattr(subject, "_create_file", create)
        monkeypatch.setattr(subject, "_stat", stat)
        monkeypatch.setattr(subject, "_fstat", fstat)
        unsafe(lambda: root.open_file(env["file"].name).__enter__())
        assert len(acquired) == 1
        if phase == "path-after-open":
            assert ledger["handles"] == acquired and ledger["fds"] == []
        else:
            assert ledger["handles"] == [] and len(ledger["fds"]) == 1


@pytest.mark.parametrize("stage", ["directory-open-invalid", "file-open-invalid", "directory-info-false",
    "file-info-false", "handle-close-false"],
    ids=["dir-open", "file-open", "dir-info", "file-info", "close"])
def test_native_failure_captures_last_error_before_later_clock(env, clock, monkeypatch, stage):
    original_create, original_info, original_close = subject._create_file, subject._get_info, subject._close_handle
    armed = [False]
    file_phase = [False]
    acquired, closed = [], []
    def failure():
        ctypes.set_last_error(5)
        armed[0] = True
    def monotonic():
        if armed[0]:
            ctypes.set_last_error(6)
            raise RuntimeError("post-failure clock must not replace native error")
        return 0.0
    def create(*args, **kwargs):
        target = "file-open-invalid" if file_phase[0] else "directory-open-invalid"
        if stage == target:
            failure()
            return ctypes.c_void_p(-1).value
        value = original_create(*args, **kwargs)
        acquired.append(value)
        return value
    def info(handle, address):
        target = "file-info-false" if file_phase[0] else "directory-info-false"
        if stage == target and acquired and handle == acquired[-1]:
            failure()
            return False
        return original_info(handle, address)
    def close(handle):
        value = original_close(handle)
        assert value
        closed.append(handle)
        if stage == "handle-close-false":
            failure()
            return False
        return value
    monkeypatch.setattr(subject, "time", SimpleNamespace(monotonic=monotonic))
    monkeypatch.setattr(subject, "_create_file", create)
    monkeypatch.setattr(subject, "_get_info", info)
    monkeypatch.setattr(subject, "_close_handle", close)
    if stage.startswith("directory"):
        with pytest.raises(OSError) as raised:
            directory(env).__enter__()
        assert raised.value.winerror == 5
        assert closed == acquired[::-1]
    elif stage == "handle-close-false":
        held = directory(env)
        held.__enter__()
        with pytest.raises(OSError) as raised:
            held.__exit__(None, None, None)
        assert raised.value.winerror == 5 and len(closed) == 1
        with pytest.raises(OSError) as repeated:
            held.__exit__(None, None, None)
        assert repeated.value is raised.value and len(closed) == 1
    else:
        with directory(env) as held:
            acquired.clear()
            closed.clear()
            file_phase[0] = True
            with pytest.raises(OSError) as raised:
                held.open_file(env["file"].name).__enter__()
            assert raised.value.winerror == 5 and closed == acquired[::-1]
            armed[0] = False
