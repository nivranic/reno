"""Real fixed-node reads; faults alter only the new consumer aliases.

Synthetic scalar and rejection projections are not elapsed-time/native-race
evidence. The fixture never closes borrowed numeric descriptors or retries an
opaque delegated close. Only Root runs this suite after separate source review.
"""
from collections import Counter
from contextlib import contextmanager
import errno
import hashlib
import io
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from reno import personal_authority_directory_deadline as directories
from reno import personal_authority_namespace3_deadline as deadline
from reno import personal_authority_source_deadline as subject
from reno.personal_preservation_contract import PersonalPreservationError


pytestmark = pytest.mark.skipif(os.name != "nt", reason="real Windows readonly lease and FileIO")
NODE = "node-identity.json"
PAYLOAD = "绍兴\x00\r\n防水材料未定".encode("utf-8") + b"EOF-sentinel"


def native(path):
    value = os.path.abspath(os.fspath(path))
    if value.startswith("\\\\?\\"):
        return value
    return "\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value


@pytest.fixture
def env(tmp_path):
    """Precreate independent files, then protect the directory immediately."""
    count = 0

    @contextmanager
    def opened(payload=PAYLOAD, *, missing=False):
        nonlocal count
        count += 1
        root = tmp_path / ("浙江绍兴-" + str(count))
        root.mkdir()
        path = root / NODE
        if not missing:
            with open(native(path), "wb") as sink:
                sink.write(payload)
        budget = deadline._RestrictedBudget(deadline.Namespace3Deadline(1.0e15))
        with directories.DeadlineDirectory(root, base=root,
                deadline_token=budget.effective_deadline) as directory:
            node = subject.DeadlineNodeDirectory(directory, budget)
            yield SimpleNamespace(root=root, file=path, payload=payload,
                                  budget=budget, directory=directory, node=node)
        if not missing:
            assert Path(native(path)).read_bytes() == payload

    return opened


def error(call, suffix):
    with pytest.raises(subject.SourceDeadlineError) as raised:
        call()
    assert raised.value.code == "SOURCE_DEADLINE_" + suffix
    return raised.value


def same_error(call, first):
    with pytest.raises(type(first)) as raised:
        call()
    assert raised.value is first


def no_action(*_args, **_kwargs):
    pytest.fail("refused operation reached a new physical consumer")


class Scalar:
    def __init__(self, live):
        self.value, self.calls = live, 0

    def __call__(self):
        self.calls += 1
        return self.value


class Trace:
    """Finite values actually consumed by new aliases, never runtime frames."""
    def __init__(self, monkeypatch):
        self.calls, self.events, self.raws, self.fds = Counter(), [], [], []
        read, seek = subject._raw_read, subject._raw_seek
        fileno, lease_exit = subject._raw_fileno, subject._lease_exit

        def record_read(raw, amount):
            self.calls["read"] += 1
            value = read(raw, amount)
            self.raws.append(raw)
            self.events.append(("read", len(value)))
            return value

        def record_seek(raw, offset, whence):
            self.calls["seek"] += 1
            result = seek(raw, offset, whence)
            self.events.append(("seek", offset, whence))
            return result

        def record_fileno(raw):
            self.calls["fileno"] += 1
            result = fileno(raw)
            self.fds.append(result)
            self.events.append(("fileno",))
            return result

        def record_exit(lease, kind, primary, tb):
            # Attempt is recorded before dispatch; an unknown result is never retried.
            self.calls["exit"] += 1
            self.events.append(("close-start",))
            result = lease_exit(lease, kind, primary, tb)
            self.events.append(("close-return",))
            return result

        for name, action in (("_raw_read", record_read), ("_raw_seek", record_seek),
                             ("_raw_fileno", record_fileno), ("_lease_exit", record_exit)):
            monkeypatch.setattr(subject, name, action)

    @property
    def returned_bytes(self):
        return sum(event[1] for event in self.events if event[0] == "read")

    @property
    def eof(self):
        return sum(event == ("read", 0) for event in self.events)


def assert_known_closed(fd):
    # Only normal, actually returned lease closure uses this observation.
    with pytest.raises(OSError) as raised:
        os.fstat(fd)
    assert raised.value.errno == errno.EBADF


# T01: exact binding, fixed name and NEW/RETIRED interfaces.
def test_exact_active_binding_fixed_name_and_readonly_api(env, monkeypatch):
    with env() as item:
        for alias in ("_raw_read", "_raw_fileno", "_fstat", "_stat"):
            monkeypatch.setattr(subject, alias, no_action)
        for selected, budget in ((object(), item.budget), (item.directory, object())):
            error(lambda: subject.DeadlineNodeDirectory(selected, budget), "INVALID_ARGUMENT")
        for constructor in (subject.DeadlineNodeStream, subject.DeadlineHeldFile):
            error(lambda: constructor(object()), "INVALID_ARGUMENT")
        class BudgetChild(deadline._RestrictedBudget):
            pass
        child = BudgetChild(deadline.Namespace3Deadline(1.0e15))
        error(lambda: subject.DeadlineNodeDirectory(item.directory, child), "INVALID_ARGUMENT")
        other_token = deadline.Namespace3Deadline(item.budget.effective_deadline.absolute_monotonic)
        assert other_token == item.budget.effective_deadline and other_token is not item.budget.effective_deadline
        with directories.DeadlineDirectory(item.root, base=item.root, deadline_token=other_token) as other:
            error(lambda: subject.DeadlineNodeDirectory(other, item.budget), "INVALID_ARGUMENT")
        fresh = directories.DeadlineDirectory(item.root, base=item.root,
                                              deadline_token=item.budget.effective_deadline)
        with pytest.raises(directories.DirectoryDeadlineError) as raised:
            subject.DeadlineNodeDirectory(fresh, item.budget)
        assert raised.value.code == "DIRECTORY_DEADLINE_CLOSED"
        class NameChild(str):
            pass
        for name in ("other.json", NameChild(NODE), True):
            for action in (item.node.entry_stat, item.node.open_file):
                error(lambda: action(name), "INVALID_ARGUMENT")
        for field in ("path", "budget"):
            with pytest.raises(AttributeError):
                setattr(item.node, field, getattr(item.node, field))
        for option in ("write", "create", "lock", "live_sql"):
            with pytest.raises(TypeError):
                item.node.open_file(NODE, **{option: True})
        item.directory.verify()
    with pytest.raises(directories.DirectoryDeadlineError) as retired:
        subject.DeadlineNodeDirectory(item.directory, item.budget)
    assert retired.value.code == "DIRECTORY_DEADLINE_CLOSED"


# T02: declarations are one-file metadata, never fabricated read fees.
def test_one_declaration_preserves_count_and_does_not_charge(env):
    with env() as item:
        assert not hasattr(item.budget, "file")
        for size in (0, 4096):
            declared = subject._NodeFileBudget(item.budget)
            declared.file(size)
            assert declared.count == 1 and item.budget.control_bytes == 0
            error(lambda: declared.file(0), "PROFILE_CHANGED")
            assert declared.count == 2 and item.budget.control_bytes == 0


@pytest.mark.parametrize("size", [4097, -1, True], ids=["oversize", "negative", "bool"])
def test_invalid_declaration_keeps_attempt_count(env, size):
    with env() as item:
        declared = subject._NodeFileBudget(item.budget)
        error(lambda: declared.file(size), "PROFILE_CHANGED")
        assert declared.count == 1 and item.budget.control_bytes == 0


def test_oversize_node_rejected_before_lease_entry_or_raw_read(env, monkeypatch):
    with env(b"x" * 4097) as item:
        trace = Trace(monkeypatch)
        monkeypatch.setattr(subject, "_raw_fileno", no_action)
        held = subject.DeadlineHeldFile(item.node)
        first = error(held.__enter__, "PROFILE_CHANGED")
        assert trace.calls["read"] == trace.calls["exit"] == 0
        assert item.budget.control_bytes == 0
        same_error(held.__enter__, first)
        item.directory.verify()


# T03: direct FileIO and repeated actual fileno retain one real descriptor.
def test_stream_actual_raw_same_fd_eof_no_prefetch_and_repeat_charge(env, monkeypatch):
    with env() as item:
        trace = Trace(monkeypatch)
        item.budget.name(NODE)
        name_counts = item.budget.names, item.budget.name_bytes
        with item.node.open_file(NODE) as stream:
            fd = stream.fileno()
            assert stream.read(1) == PAYLOAD[:1]
            assert trace.returned_bytes == item.budget.control_bytes == 1
            pieces = [PAYLOAD[:1]]
            while True:
                raw = stream.read(7)
                assert stream.fileno() == fd
                if not raw:
                    break
                pieces.append(raw)
            whole = b"".join(pieces)
            assert whole == PAYLOAD and hashlib.sha256(whole).digest() == hashlib.sha256(PAYLOAD).digest()
            assert item.budget.control_bytes == len(PAYLOAD) and trace.eof == 1
            before = item.budget.control_bytes
            assert stream.seek(0) == 0 and stream.fileno() == fd
            assert item.budget.control_bytes == before
            assert stream.read(100000) == PAYLOAD and stream.read(1) == b""
            assert item.budget.control_bytes == trace.returned_bytes == 2 * len(PAYLOAD)
            assert (item.budget.names, item.budget.name_bytes) == name_counts
            assert trace.eof == 2 and set(trace.fds) == {fd}
            assert all(type(raw) is io.FileIO and raw.closefd is False for raw in trace.raws)
            assert all(raw is trace.raws[0] for raw in trace.raws)
            assert not hasattr(stream, "close") and trace.calls["exit"] == 0
        assert trace.calls["exit"] == 1
        assert_known_closed(fd)
        item.directory.verify()


def test_stream_exact_count_and_whence_domains_refuse_new_physical_action(env, monkeypatch):
    with env() as item:
        trace = Trace(monkeypatch)
        for method, args in (("read", (True,)), ("seek", (0, True))):
            with pytest.raises(subject.SourceDeadlineError) as propagated:
                with item.node.open_file(NODE) as stream:
                    first = error(lambda: getattr(stream, method)(*args), "INVALID_ARGUMENT")
                    assert trace.calls["read"] == trace.calls["seek"] == item.budget.control_bytes == 0
            assert propagated.value is first
        assert trace.calls["exit"] == 2


def test_changed_actual_fileno_is_rejected_before_fstat_and_not_adopted(env, monkeypatch):
    with env() as item:
        trace = Trace(monkeypatch)
        with pytest.raises(subject.SourceDeadlineError) as propagated:
            with item.node.open_file(NODE) as stream:
                fd = stream.fileno()
                actual_fileno = subject._raw_fileno
                def changed(raw):
                    assert actual_fileno(raw) == fd
                    return fd + 100000
                monkeypatch.setattr(subject, "_raw_fileno", changed)
                monkeypatch.setattr(subject, "_fstat", no_action)
                first = error(stream.fileno, "PROFILE_CHANGED")
                before = dict(trace.calls)
                for call in (lambda: stream.read(1), lambda: stream.seek(0), stream.fileno):
                    same_error(call, first)
                assert dict(trace.calls) == before
        assert propagated.value is first and trace.calls["exit"] == 1
        assert_known_closed(fd)


# T04: independent complete bytes, cost and EOF observations at public boundaries.
@pytest.mark.parametrize("payload", [b"", PAYLOAD, b"z" * 4096], ids=["empty", "utf8", "4096"])
def test_held_enter_bytes_exit_keeps_six_actual_full_passes(env, monkeypatch, payload):
    with env(payload) as item:
        trace = Trace(monkeypatch)
        with subject.DeadlineHeldFile(item.node) as held:
            assert held.byte_size == len(payload)
            assert held.sha256 == hashlib.sha256(payload).hexdigest()
            assert trace.eof == 2 and trace.returned_bytes == item.budget.control_bytes == 2 * len(payload)
            assert held.bytes(100000) == payload
            assert trace.eof == 5 and trace.returned_bytes == item.budget.control_bytes == 5 * len(payload)
            assert trace.calls["exit"] == 0
        assert trace.eof == 6 and trace.returned_bytes == item.budget.control_bytes == 6 * len(payload)
        assert trace.calls["exit"] == 1 and len(set(trace.fds)) == 1
        assert all(raw is trace.raws[0] for raw in trace.raws)
        close_index = trace.events.index(("close-start",))
        assert sum(event == ("read", 0) for event in trace.events[:close_index]) == 6
        assert not any(event[0] == "read" for event in trace.events[close_index:])
        assert_known_closed(trace.fds[0])
        item.directory.verify()


# T05: copied evidence cannot alter expectations; shared read budget never resets.
def test_evidence_copy_explicit_verify_retirement_and_shared_budget(env, monkeypatch):
    with env() as item:
        trace = Trace(monkeypatch)
        early_probe = subject.DeadlineHeldFile(item.node)
        properties = ("byte_size", "sha256", "path_identity", "handle_identity", "directory_identity")
        for name in properties:
            error(lambda: getattr(early_probe, name), "CLOSED")
        held = subject.DeadlineHeldFile(item.node)
        with held:
            for name in ("path_identity", "handle_identity", "directory_identity"):
                saved = getattr(held, name)
                copy = getattr(held, name)
                copy["device"] = "caller-changed"
                assert getattr(held, name) == saved
            assert {"mode", "nlink", "ctime_ns"} <= held.path_identity.keys()
            assert {"mode", "nlink", "ctime_ns"} <= held.handle_identity.keys()
            before = item.budget.control_bytes
            held.verify()
            assert item.budget.control_bytes - before == len(PAYLOAD) and trace.eof == 3
        assert item.budget.control_bytes == 4 * len(PAYLOAD)
        events = list(trace.events)
        for call in (held.__enter__, held.verify, lambda: held.bytes(4096)):
            error(call, "CLOSED")
        for name in properties:
            error(lambda: getattr(held, name), "CLOSED")
        assert trace.events == events
        with subject.DeadlineHeldFile(item.node):
            assert item.budget.control_bytes == 6 * len(PAYLOAD)
        assert item.budget.control_bytes == 7 * len(PAYLOAD) and trace.calls["exit"] == 2


@pytest.mark.parametrize("maximum", [-1, True, len(PAYLOAD) - 1], ids=["negative", "bool", "too-small"])
def test_bytes_maximum_rejects_before_extra_read_and_keeps_first(env, monkeypatch, maximum):
    with env() as item:
        trace = Trace(monkeypatch)
        expected_type = PersonalPreservationError if type(maximum) is int and maximum >= 0 else subject.SourceDeadlineError
        with pytest.raises(expected_type) as propagated:
            with subject.DeadlineHeldFile(item.node) as held:
                before = item.budget.control_bytes
                with pytest.raises(expected_type) as raised:
                    held.bytes(maximum)
                first = raised.value
                assert first.code == ("PERSONAL_PRESERVATION_CONTROL_FILE_TOO_LARGE"
                    if expected_type is PersonalPreservationError else "SOURCE_DEADLINE_INVALID_ARGUMENT")
                same_error(held.verify, first)
                assert item.budget.control_bytes == before and trace.eof == 2
        assert propagated.value is first and trace.calls["exit"] == 1 and trace.eof == 2


# T06: real 4096-byte returns reach exactly the unchanged 1MiB ledger cap.
def test_actual_repeated_reads_exhaust_original_one_mib_before_next_raw_call(env, monkeypatch):
    payload = b"budget-node" * 372 + b"tail"
    assert len(payload) == 4096
    with env(payload) as item:
        trace = Trace(monkeypatch)
        with pytest.raises(deadline.Namespace3Error) as propagated:
            with item.node.open_file(NODE) as stream:
                for _ in range(256):
                    assert stream.seek(0) == 0
                    assert stream.read(4096) == payload
                assert trace.returned_bytes == item.budget.control_bytes == 1048576
                before = trace.calls["read"]
                with pytest.raises(deadline.Namespace3Error) as raised:
                    stream.read(1)
                first = raised.value
                assert first.code == "NAMESPACE3_LIMIT" and trace.calls["read"] == before
                same_error(lambda: stream.read(1), first)
        assert propagated.value is first and trace.calls["exit"] == 1


# T07: returned bytes are charged by actual parent before a synthetic new gate.
@pytest.mark.parametrize("fault", ["equality", "nan"], ids=["synthetic-equality", "synthetic-invalid"])
def test_actual_return_charged_before_new_scalar_refuses_delivery(env, monkeypatch, fault):
    with env() as item:
        trace = Trace(monkeypatch)
        scalar = Scalar(subject._monotonic())
        monkeypatch.setattr(subject, "_monotonic", scalar)
        read = subject._raw_read
        with pytest.raises(subject.SourceDeadlineError) as propagated:
            with item.node.open_file(NODE) as stream:
                def after_read(raw, count):
                    value = read(raw, count)
                    scalar.value = (item.budget.effective_deadline.absolute_monotonic
                                    if fault == "equality" else float("nan"))
                    return value
                monkeypatch.setattr(subject, "_raw_read", after_read)
                first = error(lambda: stream.read(3), "DEADLINE" if fault == "equality" else "INVALID_ARGUMENT")
                assert trace.returned_bytes == item.budget.control_bytes == 3
                calls = dict(trace.calls)
                for action in (lambda: stream.read(1), lambda: stream.seek(0), stream.fileno):
                    same_error(action, first)
                assert dict(trace.calls) == calls
        assert propagated.value is first and trace.calls["exit"] == 1
        item.budget.clock_only()  # Actual parent remains live; synthetic fault is new only.


# T08: new pre-request boundary and postconditions on direct consumers.
def test_post_request_new_scalar_refuses_before_actual_read(env, monkeypatch):
    with env() as item:
        trace = Trace(monkeypatch)
        with pytest.raises(subject.SourceDeadlineError) as propagated:
            with item.node.open_file(NODE) as stream:
                live = subject._monotonic()
                calls = 0
                def sequence():
                    nonlocal calls
                    calls += 1
                    # Static read contract: _live; real request; new pre-read; read; charge; post.
                    return item.budget.effective_deadline.absolute_monotonic if calls >= 2 else live
                monkeypatch.setattr(subject, "_monotonic", sequence)
                first = error(lambda: stream.read(1), "DEADLINE")
                assert calls == 2 and trace.calls["read"] == item.budget.control_bytes == 0
        assert propagated.value is first and trace.calls["exit"] == 1


def test_new_pre_scalar_gate_prevents_seek_without_patching_parent(env, monkeypatch):
    with env() as item:
        trace = Trace(monkeypatch)
        with pytest.raises(subject.SourceDeadlineError) as propagated:
            with item.node.open_file(NODE) as stream:
                monkeypatch.setattr(subject, "_monotonic", lambda: item.budget.effective_deadline.absolute_monotonic)
                first = error(lambda: stream.seek(0), "DEADLINE")
                assert trace.calls["seek"] == trace.calls["read"] == item.budget.control_bytes == 0
        assert propagated.value is first and trace.calls["exit"] == 1


@pytest.mark.parametrize("stage", ["seek", "fileno", "fstat", "stat"])
def test_actual_consumer_success_then_new_scalar_refuses_result(env, monkeypatch, stage):
    with env() as item:
        trace = Trace(monkeypatch)
        scalar = Scalar(subject._monotonic())
        monkeypatch.setattr(subject, "_monotonic", scalar)
        alias = {"seek": "_raw_seek", "fileno": "_raw_fileno", "fstat": "_fstat", "stat": "_stat"}[stage]
        context = (item.node.open_file(NODE) if stage in ("seek", "fileno")
                   else subject.DeadlineHeldFile(item.node))
        with pytest.raises(subject.SourceDeadlineError) as propagated:
            with context as active:
                physical = getattr(subject, alias)
                reached = 0
                def post_fault(*args, **kwargs):
                    nonlocal reached
                    result = physical(*args, **kwargs)
                    reached += 1
                    scalar.value = item.budget.effective_deadline.absolute_monotonic
                    return result
                monkeypatch.setattr(subject, alias, post_fault)
                before_bytes, before_read = item.budget.control_bytes, trace.calls["read"]
                call = (lambda: active.seek(0)) if stage == "seek" else active.fileno if stage == "fileno" else active.verify
                first = error(call, "DEADLINE")
                assert reached == 1 and item.budget.control_bytes == before_bytes
                assert trace.calls["read"] == before_read
                same_error(call, first)
        assert propagated.value is first and trace.calls["exit"] == 1


# T09: explicit rejection projections are not natural identity races/disk bytes.
@pytest.mark.parametrize("field", ["st_mode", "st_ino"], ids=["mode", "inode"])
def test_same_representation_identity_projection_preserves_source_changed(env, monkeypatch, field):
    with env() as item:
        trace = Trace(monkeypatch)
        with pytest.raises(PersonalPreservationError) as propagated:
            with subject.DeadlineHeldFile(item.node) as held:
                fstat = subject._fstat
                def projected(fd):
                    info = fstat(fd)
                    values = {name: getattr(info, name) for name in (
                        "st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode", "st_nlink", "st_file_attributes")}
                    values[field] += 1
                    return SimpleNamespace(**values)
                monkeypatch.setattr(subject, "_fstat", projected)
                with pytest.raises(PersonalPreservationError) as raised:
                    held.verify()
                first = raised.value
                assert first.code == "PERSONAL_PRESERVATION_SOURCE_CHANGED" and first.retriable is True
                same_error(lambda: held.bytes(4096), first)
        assert propagated.value is first and trace.calls["exit"] == 1


def test_same_length_digest_fault_is_rejected_with_original_first(env, monkeypatch):
    with env() as item:
        trace = Trace(monkeypatch)
        with pytest.raises(PersonalPreservationError) as propagated:
            with subject.DeadlineHeldFile(item.node) as held:
                actual_read = subject._raw_read
                def projected(raw, count):
                    value = actual_read(raw, count)
                    return bytes([value[0] ^ 1]) + value[1:] if value else value
                monkeypatch.setattr(subject, "_raw_read", projected)
                before = item.budget.control_bytes
                with pytest.raises(PersonalPreservationError) as raised:
                    held.verify()
                first = raised.value
                assert first.code == "PERSONAL_PRESERVATION_SOURCE_CHANGED"
                assert item.budget.control_bytes - before == len(PAYLOAD)
                same_error(held.verify, first)
        assert propagated.value is first and trace.calls["exit"] == 1


# T10: lease return owes one cleanup; self-raising genuine entry owes no second exit.
def test_first_actual_fileno_post_fault_closes_successfully_entered_lease_once(env, monkeypatch):
    with env() as item:
        trace = Trace(monkeypatch)
        scalar = Scalar(subject._monotonic())
        monkeypatch.setattr(subject, "_monotonic", scalar)
        actual_fileno = subject._raw_fileno
        def post_fault(raw):
            fd = actual_fileno(raw)
            scalar.value = item.budget.effective_deadline.absolute_monotonic
            return fd
        monkeypatch.setattr(subject, "_raw_fileno", post_fault)
        stream = item.node.open_file(NODE)
        first = error(stream.__enter__, "DEADLINE")
        assert trace.calls["fileno"] == trace.calls["exit"] == 1 and trace.calls["read"] == 0
        same_error(stream.__enter__, first)
        same_error(lambda: stream.__exit__(None, None, None), first)
        assert trace.calls["exit"] == 1
        item.directory.verify()


def test_real_missing_file_lease_enter_failure_is_not_exited_again(env, monkeypatch):
    with env(missing=True) as item:
        trace = Trace(monkeypatch)
        stream = item.node.open_file(NODE)
        with pytest.raises(FileNotFoundError) as raised:
            stream.__enter__()
        first = raised.value
        same_error(stream.__enter__, first)
        same_error(lambda: stream.__exit__(None, None, None), first)
        assert trace.calls["exit"] == trace.calls["read"] == 0
        assert not os.path.exists(native(item.file))
        item.directory.verify()


def test_held_initialization_failure_after_stream_enter_closes_once(env, monkeypatch):
    with env() as item:
        trace = Trace(monkeypatch)
        first = OSError("finite initial fstat failure")
        def refuse(_fd):
            raise first
        monkeypatch.setattr(subject, "_fstat", refuse)
        held = subject.DeadlineHeldFile(item.node)
        same_error(held.__enter__, first)
        assert trace.calls["exit"] == 1 and trace.calls["read"] == 0
        same_error(held.__enter__, first)
        same_error(lambda: held.__exit__(None, None, None), first)
        assert trace.calls["exit"] == 1


# T11: BaseException identity and old-first precedence survive cleanup failures.
@pytest.mark.parametrize("kind", [RuntimeError, KeyboardInterrupt, SystemExit], ids=["ordinary", "interrupt", "exit"])
def test_body_first_skips_terminal_read_and_survives_post_close_fault(env, monkeypatch, kind):
    with env() as item:
        trace = Trace(monkeypatch)
        first, cleanup = kind("finite body failure"), OSError("finite post-close failure")
        exit_lease = subject._lease_exit
        def fail_after_close(*args):
            exit_lease(*args)
            raise cleanup
        monkeypatch.setattr(subject, "_lease_exit", fail_after_close)
        with pytest.raises(kind) as propagated:
            with subject.DeadlineHeldFile(item.node):
                before = item.budget.control_bytes
                raise first
        assert propagated.value is first and item.budget.control_bytes == before
        assert trace.eof == 2 and trace.calls["exit"] == 1
        assert "SOURCE_DEADLINE_CLEANUP_FAILED" in getattr(first, "__notes__", [])


def test_terminal_read_first_is_preserved_when_real_cleanup_then_throws(env, monkeypatch):
    with env() as item:
        trace = Trace(monkeypatch)
        first, cleanup = OSError("finite terminal read failure"), RuntimeError("finite cleanup failure")
        with pytest.raises(OSError) as propagated:
            with subject.DeadlineHeldFile(item.node):
                before = item.budget.control_bytes
                def refuse(*_args):
                    raise first
                exit_lease = subject._lease_exit
                def fail_after_close(*args):
                    exit_lease(*args)
                    raise cleanup
                monkeypatch.setattr(subject, "_raw_read", refuse)
                monkeypatch.setattr(subject, "_lease_exit", fail_after_close)
        assert propagated.value is first and item.budget.control_bytes == before
        assert trace.eof == 2 and trace.calls["exit"] == 1
        assert "SOURCE_DEADLINE_CLEANUP_FAILED" in getattr(first, "__notes__", [])


def test_swallowed_old_first_wins_over_later_body_error_with_no_new_io(env, monkeypatch):
    with env() as item:
        trace = Trace(monkeypatch)
        first, later = OSError("finite original read failure"), RuntimeError("finite later body failure")
        with pytest.raises(OSError) as propagated:
            with subject.DeadlineHeldFile(item.node) as held:
                def refuse(*_args):
                    raise first
                monkeypatch.setattr(subject, "_raw_read", refuse)
                same_error(lambda: held.bytes(4096), first)
                events = list(trace.events)
                for call in (held.verify, lambda: held.bytes(4096), lambda: held.path_identity):
                    same_error(call, first)
                assert trace.events == events
                raise later
        assert propagated.value is first and trace.calls["exit"] == 1 and trace.eof == 2
        assert "SOURCE_DEADLINE_SECONDARY_FAILED" in getattr(first, "__notes__", [])


# T12: close must complete before a final scalar; opaque returns never trigger retry.
@pytest.mark.parametrize("kind", ["stream", "held"])
def test_actual_close_return_precedes_new_final_scalar_failure(env, monkeypatch, kind):
    with env() as item:
        trace = Trace(monkeypatch)
        scalar = Scalar(subject._monotonic())
        monkeypatch.setattr(subject, "_monotonic", scalar)
        context = item.node.open_file(NODE) if kind == "stream" else subject.DeadlineHeldFile(item.node)
        with pytest.raises(subject.SourceDeadlineError) as propagated:
            with context:
                exit_lease = subject._lease_exit
                def post_close(*args):
                    result = exit_lease(*args)
                    scalar.value = item.budget.effective_deadline.absolute_monotonic
                    return result
                monkeypatch.setattr(subject, "_lease_exit", post_close)
        first = propagated.value
        assert first.code == "SOURCE_DEADLINE_DEADLINE"
        assert trace.calls["exit"] == 1 and trace.events[-1] == ("close-return",)
        assert trace.eof == (3 if kind == "held" else 0)
        same_error(context.__enter__, first)
        same_error(lambda: context.__exit__(None, None, None), first)
        assert trace.calls["exit"] == 1


@pytest.mark.parametrize("kind", ["stream", "held"])
def test_opaque_exit_fault_is_first_and_never_retried_or_probed(env, monkeypatch, kind):
    with env() as item:
        trace = Trace(monkeypatch)
        scalar = Scalar(subject._monotonic())
        monkeypatch.setattr(subject, "_monotonic", scalar)
        first = OSError("finite delegated exit no return")
        context = item.node.open_file(NODE) if kind == "stream" else subject.DeadlineHeldFile(item.node)
        with pytest.raises(OSError) as propagated:
            with context:
                exit_lease = subject._lease_exit
                def opaque(*args):
                    exit_lease(*args)
                    scalar.value = item.budget.effective_deadline.absolute_monotonic
                    raise first
                monkeypatch.setattr(subject, "_lease_exit", opaque)
        assert propagated.value is first and trace.calls["exit"] == 1
        same_error(context.__enter__, first)
        same_error(lambda: context.__exit__(None, None, None), first)
        assert trace.calls["exit"] == 1
        # No FD/fstat ownership oracle and no fixture-owned numeric recovery.
