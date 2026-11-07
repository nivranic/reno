"""r826: real private image bytes and finite new-consumer rejection faults.

I01-I32 are design labels, not pytest counts. Only this module's declared
consumer aliases are patched. Root owns the admitted isolated execution.
"""
import ast
from collections import Counter
from contextlib import contextmanager
import errno
import hashlib
import io
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace

import pytest

from reno import personal_authority_directory_deadline as directories
from reno import personal_authority_image_bytes_deadline as subject
from reno import personal_authority_live_sql_deadline as live_sql
from reno import personal_authority_namespace3_deadline as deadline
from reno import personal_preservation_source as preservation
from reno import storage_readonly as readonly
from reno import storage_source as source
from reno import sync_media as safe


pytestmark = pytest.mark.skipif(os.name != "nt", reason="actual Windows private image leases")
NAME = "绍兴-image.sqlite3"
C = 128 * 1024
PLAIN = "浙江绍兴\x00\r\noriginal".encode() + bytes(range(32))
FIELDS = {"copy_read", "hash_read", "target_write", "copy_read_limit",
          "hash_read_limit", "target_write_limit", "phases", "retired", "complete"}


def native(path):
    text = os.path.abspath(os.fspath(path))
    if text.startswith("\\\\?\\"):
        return text
    return "\\\\?\\UNC\\" + text[2:] if text.startswith("\\\\") else "\\\\?\\" + text


def note(first):
    try:
        BaseException.add_note(first, "R826_FIXTURE_CLEANUP_FAILED")
    except BaseException:
        pass


def qa_temp():
    """Assert before the actual legacy constructor can create any TEMP."""
    qa = Path(os.environ["RENO_QA_ROOT"]).resolve(strict=True)
    selected = Path(tempfile.gettempdir()).resolve(strict=True)
    selected.relative_to(qa)
    assert selected.is_dir()
    return selected


def token():
    # The original restricted budget clips this fixture to the existing 30s.
    # No counter from that independent budget is sampled or patched.
    return deadline._RestrictedBudget(deadline.Namespace3Deadline(1.0e15)).effective_deadline


def state(root):
    result = {}
    for suffix in ("", "-wal", "-journal"):
        try:
            result[suffix] = source.file_signature(os.stat(native(root / (NAME + suffix)), follow_symlinks=False))
        except FileNotFoundError:
            result[suffix] = None
    assert result[""] is not None
    return result


@pytest.fixture
def env(tmp_path):
    sequence = 0

    @contextmanager
    def opened(*, plain=None, large=False, wal=False):
        nonlocal sequence
        sequence += 1
        root = tmp_path / ("浙江绍兴-image-" + str(sequence))
        root.mkdir()
        connection = None
        first = None

        def close_connection():
            nonlocal connection
            value, connection = connection, None
            if value is not None:
                value.close()

        try:
            if plain is not None:
                with open(native(root / NAME), "wb") as sink:
                    assert sink.write(plain) == len(plain)
            else:
                connection = sqlite3.connect(native(root / NAME))
                mode = "WAL" if wal else "DELETE"
                assert connection.execute("PRAGMA journal_mode=" + mode).fetchone()[0].lower() == mode.lower()
                if wal:
                    connection.execute("PRAGMA wal_autocheckpoint=0")
                connection.execute("CREATE TABLE samples(id INTEGER PRIMARY KEY, payload BLOB)")
                connection.execute("INSERT INTO samples(payload) VALUES (?)", (PLAIN,))
                if large:
                    connection.execute("INSERT INTO samples(payload) VALUES (zeroblob(1179648))")
                connection.commit()
            before = state(root)
            expected = {suffix: Path(native(root / (NAME + suffix))).read_bytes()
                        for suffix in ("", "-wal") if before[suffix] is not None}
            effective = token()
            with directories.DeadlineDirectory(root, base=root, deadline_token=effective) as directory:
                qa_temp()
                legacy = preservation._PersonalPrivateDirectory()
                owner_first = None
                try:
                    with directories.DeadlineDirectory(legacy.path, base=legacy.path,
                            deadline_token=effective) as private:
                        image = subject.ImagePrivateOwner(legacy, private, deadline_token=effective)
                        budget = subject.ImageByteBudget(NAME, before, deadline_token=effective)
                        yield SimpleNamespace(root=root, connection=connection, before=before,
                            expected=expected, token=effective, directory=directory,
                            legacy=legacy, private=private, owner=image, budget=budget)
                except BaseException as failure:
                    owner_first = failure
                    raise
                finally:
                    # The private noDELETE pin has exited before either action.
                    # Actual SQLite close precedes TEMP cleanup; source pin last.
                    cleanup_first = owner_first
                    for action in (close_connection, legacy.cleanup):
                        try:
                            action()
                        except BaseException as failure:
                            if cleanup_first is None:
                                cleanup_first = failure
                            else:
                                note(cleanup_first)
                    if owner_first is None and cleanup_first is not None:
                        raise cleanup_first
        except BaseException as failure:
            first = failure
            raise
        finally:
            if connection is not None:
                try:
                    close_connection()
                except BaseException:
                    if first is None:
                        raise
                    note(first)

    return opened


def copy(item, suffix="", ledger=None, owner=None, directory=None):
    if ledger is not None:
        ledger.stage = "copy"
    return subject.copy_file_locked(item.directory if directory is None else directory,
        item.owner if owner is None else owner, suffix, byte_budget=item.budget)


def sha(item, suffix="", ledger=None, owner=None):
    if ledger is not None:
        ledger.stage = "hash"
    return subject.private_sha_locked(item.owner if owner is None else owner,
                                      suffix, byte_budget=item.budget)


def error(call, suffix):
    with pytest.raises(subject.ImageBytesDeadlineError) as raised:
        call()
    assert raised.value.code == "IMAGE_BYTES_DEADLINE_" + suffix
    return raised.value


def same_error(call, first):
    with pytest.raises(type(first)) as raised:
        call()
    assert raised.value is first


def no_action(*_args, **_kwargs):
    pytest.fail("refused image stage invoked a forbidden new consumer")


def failed(item, first, monkeypatch):
    """I32: diagnostic has no clock; sticky business refusal has no IO."""
    for name in ("_monotonic", "_open_fd", "_raw_read", "_raw_write", "_live_reader", "_private_reader"):
        monkeypatch.setattr(subject, name, no_action)
    evidence = item.budget.evidence
    assert set(evidence) == FIELDS and evidence["retired"] is True and evidence["complete"] is False
    same_error(lambda: copy(item), first)
    same_error(lambda: sha(item), first)
    changed = item.budget.evidence
    changed["copy_read"] = -1
    changed["phases"][""] = "hash_complete"
    assert item.budget.evidence == evidence
    return evidence


class Scalar:
    def __init__(self, value):
        self.value, self.calls = value, 0

    def __call__(self):
        self.calls += 1
        return self.value


class Ledger:
    """Finite alias inputs/returns and attempts, never subject private state."""
    def __init__(self, monkeypatch):
        names = ("_open_fd", "_file_io", "_fstat", "_raw_fileno", "_raw_read", "_raw_write",
                 "_retire_raw", "_close_fd", "_initialize_file", "_verify_private", "_lease_exit")
        self.original = {name: getattr(subject, name) for name in names}
        self.calls, self.events = Counter(), []
        self.fds, self.raws, self.reads, self.writes = [], [], [], []
        self.fd_attempts, self.fd_returns, self.source_returns, self.private_returns = [], [], [], []
        self.stage = "copy"

        def opened(*args):
            self.calls["open"] += 1
            value = self.original["_open_fd"](*args)
            self.fds.append(value)
            self.events.append(("target-open-return", value))
            assert args[1] == os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_BINARY
            assert args[2] == 0o600
            return value

        def raw(*args, **kwargs):
            self.calls["raw"] += 1
            value = self.original["_file_io"](*args, **kwargs)
            self.raws.append(value)
            assert type(value) is io.FileIO and value.closefd is False
            self.events.append(("target-raw-return",))
            return value

        def fstat(fd):
            self.calls["fstat"] += 1
            value = self.original["_fstat"](fd)
            self.events.append(("fstat", self.stage, fd, source.file_signature(value)))
            return value

        def fileno(raw):
            self.calls["fileno"] += 1
            value = self.original["_raw_fileno"](raw)
            self.events.append(("fileno", self.stage, value))
            return value

        def read(raw, size):
            self.calls["read"] += 1
            assert type(raw) is io.FileIO and raw.closefd is False
            fd = io.FileIO.fileno(raw)
            value = self.original["_raw_read"](raw, size)
            self.reads.append((self.stage, fd, size, value))
            self.events.append(("read-return", self.stage, len(value)))
            return value

        def write(raw, chunk):
            self.calls["write"] += 1
            value = self.original["_raw_write"](raw, chunk)
            self.writes.append((chunk, value))
            self.events.append(("write-return", value))
            return value

        def retire(raw):
            self.calls["retire"] += 1
            self.events.append(("target-raw-attempt",))
            value = self.original["_retire_raw"](raw)
            self.events.append(("target-raw-return",))
            return value

        def close(fd):
            self.calls["close"] += 1
            self.fd_attempts.append(fd)
            self.events.append(("target-close-attempt", fd))
            value = self.original["_close_fd"](fd)
            self.fd_returns.append(fd)
            self.events.append(("target-close-return", fd))
            return value

        def initialize(owner, path):
            self.calls["initialize"] += 1
            assert Path(native(path)).read_bytes() == b""
            self.events.append(("initialize-start",))
            value = self.original["_initialize_file"](owner, path)
            self.events.append(("initialize-return",))
            return value

        def verify(owner):
            self.calls["verify"] += 1
            return self.original["_verify_private"](owner)

        def exit_lease(lease, kind, primary, tb):
            label = "source" if type(lease) is live_sql.LiveSqlDeadlineLease else "private"
            assert type(lease) in (live_sql.LiveSqlDeadlineLease, directories.DeadlineReadonlyLease)
            self.calls[label + "-exit"] += 1
            self.events.append((label + "-exit-start", primary))
            value = self.original["_lease_exit"](lease, kind, primary, tb)
            self.events.append((label + "-exit-return",))
            (self.source_returns if label == "source" else self.private_returns).append(primary)
            return value

        actions = (opened, raw, fstat, fileno, read, write, retire, close, initialize, verify, exit_lease)
        for name, action in zip(names, actions):
            monkeypatch.setattr(subject, name, action)


def known_closed(fd):
    # Only after an actual successful close or exact provider exit return.
    with pytest.raises(OSError) as raised:
        os.fstat(fd)
    assert raised.value.errno == errno.EBADF


def assert_target_close(ledger):
    assert ledger.fd_attempts == ledger.fds == ledger.fd_returns
    assert len(ledger.fds) == 1 and all(raw.closed for raw in ledger.raws)
    known_closed(ledger.fds[0])


def write_growth(item):
    path = item.root / NAME
    original = os.stat(native(path)).st_size
    item.connection.execute("INSERT INTO samples(payload) VALUES (zeroblob(131072))")
    item.connection.commit()
    assert os.stat(native(path)).st_size > original


def projected_stat(info, field):
    values = {name: getattr(info, name) for name in (
        "st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode", "st_nlink")}
    values["st_file_attributes"] = getattr(info, "st_file_attributes", 0)
    values[field] += 1
    return SimpleNamespace(**values)


# I01: pure declaration domains, without creating files or 2GiB real IO.
def test_budget_exact_domains_and_fixed_two_gib_declaration_boundary(monkeypatch):
    effective = token()
    valid = {"": (-2, -3, 0, -4, -5), "-wal": None, "-journal": None}
    make = lambda before=valid, name=NAME, selected=effective: subject.ImageByteBudget(name, before, deadline_token=selected)
    assert make().evidence["copy_read_limit"] == 1
    invalid = [None, {}, {**valid, "extra": None}, {**valid, "": None},
        {**valid, "": [0, 0, 0, 0, 0]}, {**valid, "": (0, 0, True, 0, 0)},
        {**valid, "": (0, 0, -1, 0, 0)}, {**valid, "": (0, 0, 0, 0)},
        {**valid, "-journal": (0, 0, 1, 0, 0)},
        {**valid, "": (0, 0, readonly.MAX_BYTES_LIMIT + 1, 0, 0)}]
    for before in invalid:
        error(lambda: make(before), "INVALID_ARGUMENT")
    for selected in (None, object(), True):
        error(lambda: make(selected=selected), "INVALID_ARGUMENT")
    error(lambda: make(name=True), "INVALID_ARGUMENT")
    for name in ("", "../other", "bad\\entry"):
        with pytest.raises(safe.SyncError) as raised:
            make(name=name)
        assert raised.value.code == "SYNC_MEDIA_UNSAFE_PATH"
    before = {**valid, "": (0, 0, readonly.MAX_BYTES_LIMIT, 0, 0)}
    boundary = make(before).evidence
    assert boundary["copy_read_limit"] == readonly.MAX_BYTES_LIMIT + 1
    assert boundary["hash_read_limit"] == readonly.MAX_BYTES_LIMIT + C
    assert boundary["target_write_limit"] == readonly.MAX_BYTES_LIMIT
    before[""] = (0, 0, readonly.MAX_BYTES_LIMIT - 7, 0, 0)
    before["-wal"] = (0, 0, 7, 0, 0)
    pair = make(before).evidence
    assert pair["copy_read_limit"] == readonly.MAX_BYTES_LIMIT + 2
    assert pair["hash_read_limit"] == readonly.MAX_BYTES_LIMIT + 2 * C
    with pytest.raises(subject.ImageBytesDeadlineError):
        type("BudgetChild", (subject.ImageByteBudget,), {})
    with pytest.raises(subject.ImageBytesDeadlineError):
        type("OwnerChild", (subject.ImagePrivateOwner,), {})


# I02 / I04: snapshot and declaration freezing are independent of stage state.
def test_frozen_declarations_and_detached_nine_field_diagnostic(env, monkeypatch):
    with env(plain=PLAIN) as item:
        original = dict(item.before)
        item.before[""] = (0, 0, 0, 0, 0)
        evidence = item.budget.evidence
        assert set(evidence) == FIELDS and evidence["phases"] == {"": "new", "-wal": "absent"}
        evidence["copy_read_limit"] = 0
        evidence["phases"][""] = "hash_complete"
        assert item.budget.evidence["copy_read_limit"] == original[""][2] + 1
        assert copy(item) is None
        assert item.budget.evidence["phases"][""] == "copy_complete"
        assert sha(item) == hashlib.sha256(PLAIN).hexdigest()
        complete = item.budget.evidence
        assert complete["complete"] is True and complete["retired"] is True
        monkeypatch.setattr(subject, "_monotonic", no_action)
        error(lambda: copy(item), "CLOSED")
        error(lambda: sha(item), "CLOSED")
        assert item.budget.evidence == complete


# I03: exact ACTIVE object and token identity; no writable public property.
def test_owner_binding_readonly_and_equal_token_is_not_same_token(env, monkeypatch):
    with env(plain=PLAIN) as item:
        other = deadline.Namespace3Deadline(item.token.absolute_monotonic)
        assert other == item.token and other is not item.token
        error(lambda: subject.ImagePrivateOwner(item.legacy, item.private, deadline_token=other), "INVALID_ARGUMENT")
        error(lambda: subject.ImagePrivateOwner(object(), item.private, deadline_token=item.token), "INVALID_ARGUMENT")
        error(lambda: subject.ImagePrivateOwner(item.legacy, object(), deadline_token=item.token), "INVALID_ARGUMENT")
        assert item.owner.path == item.legacy.path and item.owner.directory is item.private
        assert item.owner.deadline_token is item.budget.deadline_token is item.token
        for name, value in (("path", item.root), ("directory", item.directory), ("deadline_token", other)):
            with pytest.raises(AttributeError):
                setattr(item.owner, name, value)
        with pytest.raises(AttributeError):
            item.budget.deadline_token = other
        assert copy(item) is None
        alternate = subject.ImagePrivateOwner(item.legacy, item.private, deadline_token=item.token)
        monkeypatch.setattr(subject, "_private_reader", no_action)
        first = error(lambda: sha(item, owner=alternate), "INVALID_ARGUMENT")
        failed(item, first, monkeypatch)


def test_hash_before_copy_and_copy_reuse_claim_once(env, monkeypatch):
    with env(plain=PLAIN) as item:
        monkeypatch.setattr(subject, "_private_reader", no_action)
        first = error(lambda: sha(item), "CLOSED")
        assert item.budget.evidence["phases"][""] == "new"
        failed(item, first, monkeypatch)


def test_copy_complete_cannot_be_claimed_again(env, monkeypatch):
    with env(plain=PLAIN) as item:
        assert copy(item) is None
        monkeypatch.setattr(subject, "_live_reader", no_action)
        first = error(lambda: copy(item), "CLOSED")
        assert item.budget.evidence["phases"][""] == "copy_complete"
        failed(item, first, monkeypatch)


# I05 / I12 / I24 / I25 / I29: one real SQLite source >namespace's 1MiB.
def test_real_multichunk_rw_sqlite_copy_hash_close_order_and_independent_fees(env, monkeypatch):
    with env(large=True) as item:
        ledger = Ledger(monkeypatch)
        expected = item.expected[""]
        assert len(expected) > 1024 * 1024
        assert copy(item, ledger=ledger) is None
        target = item.owner.path / "image.sqlite"
        assert Path(native(target)).read_bytes() == expected
        assert item.connection.execute("SELECT count(*) FROM samples").fetchone()[0] == 2
        assert_target_close(ledger)
        close_index = ledger.events.index(("target-close-return", ledger.fds[0]))
        exit_index = ledger.events.index(("source-exit-start", None))
        assert close_index < exit_index
        assert ledger.events.index(("initialize-return",)) < next(
            index for index, event in enumerate(ledger.events) if event[0] == "write-return")
        before_sha = source.file_signature(os.stat(native(target), follow_symlinks=False))
        copy_fd_rows = [event[3] for event in ledger.events if event[:3] == ("fstat", "copy", ledger.fds[0])]
        assert len(copy_fd_rows) == 3 and copy_fd_rows[-1][:3] == before_sha[:3]
        assert sha(item, ledger=ledger) == hashlib.sha256(expected).hexdigest()
        evidence = item.budget.evidence
        assert evidence["copy_read"] == evidence["hash_read"] == evidence["target_write"] == len(expected)
        assert evidence["complete"] is True and evidence["phases"] == {"": "hash_complete", "-wal": "absent"}
        hash_rows = [row for row in ledger.reads if row[0] == "hash"]
        assert sum(len(row[3]) for row in hash_rows) == len(expected)
        assert hash_rows[-1][3] == b"" and all(row[2] == C for row in hash_rows)
        assert len({row[1] for row in hash_rows}) == 1 and ledger.private_returns == [None]
        known_closed(hash_rows[0][1])
        known_closed(next(row[1] for row in ledger.reads if row[0] == "copy"))
        raw = (Path(__file__).parents[1] / "reno/personal_authority_image_bytes_deadline.py").read_bytes()
        tree = ast.parse(raw)
        assert not any(isinstance(node, ast.Attribute) and node.attr == "control_bytes" for node in ast.walk(tree))
        assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name) and node.func.value.id == "buffer"
            and node.func.attr in ("read", "read1", "peek") for node in ast.walk(tree))


# I06 / I23: real SQLite-created WAL, no SQLimage/content-consistency claim.
def test_actual_db_wal_fixed_names_cumulative_ledgers_and_all_hash_completion(env, monkeypatch):
    with env(wal=True) as item:
        assert set(item.expected) == {"", "-wal"} and len(item.expected["-wal"]) > 0
        ledger = Ledger(monkeypatch)
        total = sum(map(len, item.expected.values()))
        for suffix in ("", "-wal"):
            assert copy(item, suffix, ledger) is None
            assert sha(item, suffix, ledger) == hashlib.sha256(item.expected[suffix]).hexdigest()
            current = item.budget.evidence
            if suffix == "":
                assert current["complete"] is False and current["retired"] is False
        evidence = item.budget.evidence
        assert evidence["copy_read"] == evidence["hash_read"] == evidence["target_write"] == total
        assert evidence["copy_read_limit"] == total + 2 and evidence["hash_read_limit"] == total + 2 * C
        assert evidence["target_write_limit"] == total and evidence["complete"] is True
        assert set(p.name for p in item.owner.path.iterdir()) == {"image.sqlite", "image.sqlite-wal"}
        assert ledger.fd_attempts == ledger.fds == ledger.fd_returns and len(ledger.fds) == 2


# I07: empty real file still creates, initializes, probes EOF and closes.
def test_actual_empty_unicode_named_file_has_zero_fees_and_empty_digest(env, monkeypatch):
    with env(plain=b"") as item:
        ledger = Ledger(monkeypatch)
        assert copy(item, ledger=ledger) is None
        assert_target_close(ledger)
        assert ledger.calls["initialize"] == 1 and ledger.calls["write"] == 0
        assert ledger.reads == [("copy", ledger.reads[0][1], 1, b"")]
        assert sha(item, ledger=ledger) == hashlib.sha256(b"").hexdigest()
        evidence = item.budget.evidence
        assert evidence["copy_read"] == evidence["hash_read"] == evidence["target_write"] == 0
        assert evidence["complete"] is True


# I08: never overwrite an existing private target.
def test_existing_target_keeps_original_bytes_and_file_exists_first(env, monkeypatch):
    with env(plain=PLAIN) as item:
        path = item.owner.path / "image.sqlite"
        with open(native(path), "xb") as sink:
            assert sink.write(b"existing-target") == 15
        ledger = Ledger(monkeypatch)
        with pytest.raises(FileExistsError) as raised:
            copy(item, ledger=ledger)
        first = raised.value
        assert Path(native(path)).read_bytes() == b"existing-target"
        assert ledger.fds == [] and ledger.calls["write"] == 0 and ledger.source_returns == [first]
        assert failed(item, first, monkeypatch)["target_write"] == 0


# I09: the reported short write is an actual FileIO write of that prefix.
def test_actual_short_writes_advance_only_written_prefix_without_rereading(env, monkeypatch):
    with env(plain=PLAIN) as item:
        ledger = Ledger(monkeypatch)
        write = subject._raw_write
        def short(raw, chunk):
            return write(raw, chunk[:max(1, len(chunk) // 2)])
        monkeypatch.setattr(subject, "_raw_write", short)
        assert copy(item, ledger=ledger) is None
        assert Path(native(item.owner.path / "image.sqlite")).read_bytes() == PLAIN
        assert sum(value for _chunk, value in ledger.writes) == len(PLAIN)
        assert ledger.calls["write"] > 1 and len(ledger.reads) == 2
        assert item.budget.evidence["target_write"] == len(PLAIN)
        assert_target_close(ledger)


# I10 / I11: no real write behind malformed or zero-return projections.
@pytest.mark.parametrize("reported", [0, None, True, -1, 1.5], ids=["zero", "none", "bool", "negative", "nonint"])
def test_malformed_or_zero_write_refuses_once_without_fabricated_fee(env, monkeypatch, reported):
    with env(plain=PLAIN) as item:
        ledger = Ledger(monkeypatch)
        calls = 0
        def malformed(_raw, _chunk):
            nonlocal calls
            calls += 1
            return reported
        monkeypatch.setattr(subject, "_raw_write", malformed)
        first = error(lambda: copy(item, ledger=ledger), "INVALID_RESULT")
        assert calls == 1 and item.budget.evidence["copy_read"] == len(PLAIN)
        assert item.budget.evidence["target_write"] == 0
        assert_target_close(ledger)
        failed(item, first, monkeypatch)


# I13 / I31: initializer/control first survives real close postfault and expiry.
@pytest.mark.parametrize("kind,after", [(OSError, False), (KeyboardInterrupt, False), (SystemExit, True)],
                         ids=["ordinary-before", "interrupt-before", "exit-after"])
def test_initializer_first_identity_survives_cleanup_and_expiry(env, monkeypatch, kind, after):
    with env(plain=PLAIN) as item:
        ledger = Ledger(monkeypatch)
        scalar = Scalar(subject._monotonic())
        monkeypatch.setattr(subject, "_monotonic", scalar)
        first, cleanup = kind("finite initializer failure"), OSError("finite numeric-close postfailure")
        initialize, close = subject._initialize_file, subject._close_fd
        def refused(owner, path):
            if after:
                initialize(owner, path)
            scalar.value = item.token.absolute_monotonic
            raise first
        def post(fd):
            close(fd)
            raise cleanup
        monkeypatch.setattr(subject, "_initialize_file", refused)
        monkeypatch.setattr(subject, "_close_fd", post)
        same_error(lambda: copy(item, ledger=ledger), first)
        assert ledger.calls["write"] == 0 and ledger.fd_attempts == ledger.fds == ledger.fd_returns
        assert ledger.calls["retire"] == ledger.calls["close"] == 1
        assert "IMAGE_BYTES_DEADLINE_CLEANUP_FAILED" in getattr(first, "__notes__", [])
        assert failed(item, first, monkeypatch)["target_write"] == 0


# I14: only the new alias fails; actual old private verification is unpatched.
@pytest.mark.parametrize("fault", ["original", "post-expiry"])
def test_private_verifier_original_or_postdeadline_refuses_before_source_io(env, monkeypatch, fault):
    with env(plain=PLAIN) as item:
        ledger = Ledger(monkeypatch)
        verify = subject._verify_private
        scalar = Scalar(subject._monotonic())
        first = RuntimeError("finite private verification failure")
        monkeypatch.setattr(subject, "_monotonic", scalar)
        def refused(owner):
            result = verify(owner)
            if fault == "original":
                raise first
            scalar.value = item.token.absolute_monotonic
            return result
        monkeypatch.setattr(subject, "_verify_private", refused)
        if fault == "original":
            same_error(lambda: copy(item), first)
        else:
            first = error(lambda: copy(item), "DEADLINE")
        assert ledger.calls["open"] == ledger.calls["read"] == 0
        failed(item, first, monkeypatch)


# I15 / I16: actual file truncation/growth during copy; surplus only rejects.
@pytest.mark.parametrize("change", ["shorter", "longer"])
def test_actual_source_length_change_is_source_changed_with_returned_read_fees(env, monkeypatch, change):
    with env(plain=PLAIN) as item:
        ledger = Ledger(monkeypatch)
        read = subject._raw_read
        changed = False
        def mutate(raw, requested):
            nonlocal changed
            if not changed:
                changed = True
                with open(native(item.root / NAME), "r+b", buffering=0) as writer:
                    if change == "shorter":
                        writer.truncate(len(PLAIN) - 3)
                    else:
                        writer.seek(0, os.SEEK_END)
                        assert writer.write(b"growth") == 6
            return read(raw, requested)
        monkeypatch.setattr(subject, "_raw_read", mutate)
        with pytest.raises(source.SourceReadError) as raised:
            copy(item, ledger=ledger)
        first = raised.value
        assert first.code == "SOURCE_CHANGED"
        evidence = item.budget.evidence
        assert evidence["copy_read"] == sum(len(row[3]) for row in ledger.reads)
        assert evidence["hash_read"] == 0 and evidence["target_write"] <= len(PLAIN)
        if change == "longer":
            assert evidence["copy_read"] == len(PLAIN) + 1 and evidence["target_write"] == len(PLAIN)
        assert_target_close(ledger)
        failed(item, first, monkeypatch)


# I17 / I29: target already closed, then a real RW commit precedes source exit.
def test_actual_writer_after_target_close_rejects_source_normal_terminal(env, monkeypatch):
    with env() as item:
        ledger = Ledger(monkeypatch)
        exit_lease = subject._lease_exit
        wrote = False
        def late(lease, kind, primary, tb):
            nonlocal wrote
            if type(lease) is live_sql.LiveSqlDeadlineLease and primary is None:
                assert ledger.fd_returns == ledger.fds and len(ledger.fds) == 1
                write_growth(item)
                wrote = True
            return exit_lease(lease, kind, primary, tb)
        monkeypatch.setattr(subject, "_lease_exit", late)
        with pytest.raises(source.SourceReadError) as raised:
            copy(item, ledger=ledger)
        first = raised.value
        assert first.code == "SOURCE_CHANGED" and wrote and ledger.source_returns == []
        assert item.budget.evidence["phases"][""] == "copy_started"
        assert_target_close(ledger)
        failed(item, first, monkeypatch)


# I18 / I19: true inter-stage replacement/size change, and separate body faults.
@pytest.mark.parametrize("change", ["replace", "grow"])
def test_actual_private_change_between_stages_refuses_before_hash_io(env, monkeypatch, change):
    with env(plain=PLAIN) as item:
        assert copy(item) is None
        path = item.owner.path / "image.sqlite"
        original = source.file_signature(os.stat(native(path), follow_symlinks=False))
        if change == "replace":
            preserved = item.owner.path / "preserved-original.sqlite"
            assert preserved.resolve().parent == item.owner.path.resolve()
            os.replace(native(path), native(preserved))
            with open(native(path), "xb") as sink:
                assert sink.write(PLAIN) == len(PLAIN)
            assert source.file_signature(os.stat(native(path), follow_symlinks=False))[:2] != original[:2]
            assert Path(native(preserved)).read_bytes() == PLAIN
        else:
            with open(native(path), "ab") as sink:
                assert sink.write(b"extra") == 5
        monkeypatch.setattr(subject, "_private_reader", no_action)
        with pytest.raises(readonly.ReadonlyImageError) as raised:
            sha(item)
        first = raised.value
        assert first.code == "PRIVATE_IMAGE_CHANGED" and item.budget.evidence["hash_read"] == 0
        failed(item, first, monkeypatch)


@pytest.mark.parametrize("change", ["early-eof", "extra-byte"])
def test_private_hash_body_length_rejection_keeps_reported_actual_fee(env, monkeypatch, change):
    with env(plain=PLAIN) as item:
        assert copy(item) is None
        ledger = Ledger(monkeypatch)
        read = subject._raw_read
        returns = []
        def projected(raw, requested):
            actual = read(raw, requested)
            value = b"" if change == "early-eof" else actual + b"X"
            returns.append(value)
            return value
        monkeypatch.setattr(subject, "_raw_read", projected)
        with pytest.raises(readonly.ReadonlyImageError) as raised:
            sha(item, ledger=ledger)
        first = raised.value
        assert first.code == "PRIVATE_IMAGE_CHANGED" and ledger.calls["private-exit"] == 1
        assert item.budget.evidence["hash_read"] == sum(map(len, returns))
        assert item.budget.evidence["target_write"] == len(PLAIN)
        failed(item, first, monkeypatch)


# I20 / I21: new scalar expires after actual bytes, including EOF0.
@pytest.mark.parametrize("stage", ["copy-read", "hash-read", "write", "copy-eof", "zero-write"])
def test_actual_return_fee_survives_postclock_expiry(env, monkeypatch, stage):
    with env(plain=PLAIN) as item:
        if stage == "hash-read":
            assert copy(item) is None
        ledger = Ledger(monkeypatch)
        scalar = Scalar(subject._monotonic())
        monkeypatch.setattr(subject, "_monotonic", scalar)
        name = "_raw_write" if stage in ("write", "zero-write") else "_raw_read"
        actual = getattr(subject, name)
        observed = []
        def post(*args):
            value = 0 if stage == "zero-write" else actual(*args)
            observed.append(value)
            if stage != "copy-eof" or value == b"":
                scalar.value = item.token.absolute_monotonic
            return value
        monkeypatch.setattr(subject, name, post)
        call = (lambda: sha(item, ledger=ledger)) if stage == "hash-read" else (lambda: copy(item, ledger=ledger))
        first = error(call, "DEADLINE")
        evidence = item.budget.evidence
        if stage in ("write", "zero-write"):
            assert evidence["target_write"] == sum(observed) == (0 if stage == "zero-write" else len(PLAIN))
        elif stage == "hash-read":
            assert evidence["hash_read"] == sum(map(len, observed)) == len(PLAIN)
        else:
            assert evidence["copy_read"] == sum(map(len, observed)) == len(PLAIN)
            assert evidence["target_write"] == (len(PLAIN) if stage == "copy-eof" else 0)
        failed(item, first, monkeypatch)


# I11 / I22: reported overrequest counts first; overlimit outranks expiry.
@pytest.mark.parametrize("consumer,overcap", [("read", False), ("read", True), ("write", False), ("write", True)])
def test_reported_overrequest_priority_and_permanent_counts(env, monkeypatch, consumer, overcap):
    payload = PLAIN if overcap else b"R" * (2 * C)
    with env(plain=payload) as item:
        ledger = Ledger(monkeypatch)
        scalar = Scalar(subject._monotonic())
        monkeypatch.setattr(subject, "_monotonic", scalar)
        reported = len(payload) + 2 if consumer == "read" and overcap else len(payload) + 1 if overcap else C + 1
        clock_at_return = []
        def projected(_raw, _request):
            clock_at_return.append(scalar.calls)
            if overcap:
                scalar.value = item.token.absolute_monotonic
            return b"P" * reported if consumer == "read" else reported
        monkeypatch.setattr(subject, "_raw_" + consumer, projected)
        first = error(lambda: copy(item, ledger=ledger), "LIMIT" if overcap else "INVALID_RESULT")
        key = "copy_read" if consumer == "read" else "target_write"
        assert item.budget.evidence[key] == reported
        if overcap:
            assert scalar.calls == clock_at_return[-1]
        assert_target_close(ledger)
        failed(item, first, monkeypatch)


# I26: fresh post-EOF raw number/fstat; rejection-only projection.
@pytest.mark.parametrize("fault", ["same-fstat-ctime", "changed-fd"])
def test_hash_terminal_fresh_identity_refuses_before_normal_close_success(env, monkeypatch, fault):
    with env(plain=PLAIN) as item:
        assert copy(item) is None
        ledger = Ledger(monkeypatch)
        fstat, fileno = subject._fstat, subject._raw_fileno
        count = 0
        if fault == "same-fstat-ctime":
            def late(fd):
                nonlocal count
                value = fstat(fd)
                count += 1
                return projected_stat(value, "st_ctime_ns") if count == 2 else value
            monkeypatch.setattr(subject, "_fstat", late)
            with pytest.raises(readonly.ReadonlyImageError) as raised:
                sha(item, ledger=ledger)
            first = raised.value
            assert first.code == "PRIVATE_IMAGE_CHANGED" and count == 2
        else:
            def changed(raw):
                nonlocal count
                value = fileno(raw)
                count += 1
                return value + 100000 if count == 2 else value
            monkeypatch.setattr(subject, "_raw_fileno", changed)
            first = error(lambda: sha(item, ledger=ledger), "INVALID_RESULT")
            assert count == 2 and ledger.calls["fstat"] == 1
        assert ledger.calls["private-exit"] == 1 and ledger.private_returns == [first]
        assert item.budget.evidence["hash_read"] == len(PLAIN)
        failed(item, first, monkeypatch)


# I27: finite direct hash callbacks, preserving original before/after failures.
@pytest.mark.parametrize("name,after", [("_sha256", False), ("_hash_update", True), ("_hash_hexdigest", True)])
def test_digest_consumer_original_failure_after_known_reads_is_sticky(env, monkeypatch, name, after):
    with env(plain=PLAIN) as item:
        assert copy(item) is None
        ledger = Ledger(monkeypatch)
        actual = getattr(subject, name)
        first = OSError("finite digest consumer failure")
        def refused(*args):
            if after:
                actual(*args)
            raise first
        monkeypatch.setattr(subject, name, refused)
        same_error(lambda: sha(item, ledger=ledger), first)
        assert item.budget.evidence["hash_read"] == (0 if name == "_sha256" else len(PLAIN))
        assert ledger.calls["private-exit"] == (0 if name == "_sha256" else 1)
        failed(item, first, monkeypatch)


# I28: a hex candidate never escapes before provider close and last clock.
@pytest.mark.parametrize("fault", ["post-expiry", "post-original"])
def test_hash_close_return_gate_prevents_hex_delivery(env, monkeypatch, fault):
    with env(plain=PLAIN) as item:
        assert copy(item) is None
        ledger = Ledger(monkeypatch)
        exit_lease = subject._lease_exit
        scalar = Scalar(subject._monotonic())
        monkeypatch.setattr(subject, "_monotonic", scalar)
        first = OSError("finite private lease exit postfailure")
        def post(lease, kind, primary, tb):
            value = exit_lease(lease, kind, primary, tb)
            if type(lease) is directories.DeadlineReadonlyLease:
                assert primary is None
                if fault == "post-original":
                    raise first
                scalar.value = item.token.absolute_monotonic
            return value
        monkeypatch.setattr(subject, "_lease_exit", post)
        if fault == "post-original":
            same_error(lambda: sha(item, ledger=ledger), first)
        else:
            first = error(lambda: sha(item, ledger=ledger), "DEADLINE")
        assert ledger.private_returns == [None] and ledger.calls["private-exit"] == 1
        assert item.budget.evidence["phases"][""] == "hash_started"
        failed(item, first, monkeypatch)


# I30: resource registration precedes postclock or raw-constructor failure.
@pytest.mark.parametrize("fault", ["open-post-expiry", "raw-before-failure"])
def test_known_target_fd_is_closed_once_after_acquisition_gate_failure(env, monkeypatch, fault):
    with env(plain=PLAIN) as item:
        ledger = Ledger(monkeypatch)
        first = OSError("finite raw constructor failure")
        if fault == "open-post-expiry":
            opened = subject._open_fd
            scalar = Scalar(subject._monotonic())
            monkeypatch.setattr(subject, "_monotonic", scalar)
            def post(*args):
                value = opened(*args)
                scalar.value = item.token.absolute_monotonic
                return value
            monkeypatch.setattr(subject, "_open_fd", post)
            first = error(lambda: copy(item, ledger=ledger), "DEADLINE")
        else:
            def refused(*_args, **_kwargs):
                raise first
            monkeypatch.setattr(subject, "_file_io", refused)
            same_error(lambda: copy(item, ledger=ledger), first)
        assert ledger.fd_attempts == ledger.fds == ledger.fd_returns and len(ledger.fds) == 1
        assert ledger.raws == [] and ledger.calls["write"] == 0
        known_closed(ledger.fds[0])
        failed(item, first, monkeypatch)


@pytest.mark.parametrize("after_open", [False, True], ids=["before-actual-open", "after-actual-open-return"])
def test_target_open_unknown_is_not_probed_and_only_fixture_known_fd_is_recovered(env, monkeypatch, after_open):
    with env(plain=PLAIN) as item:
        ledger = Ledger(monkeypatch)
        opened = subject._open_fd
        known = []
        first = OSError("finite opaque target create failure")
        def opaque(*args):
            # Source opening already completed; only target consumers are now
            # forbidden. No unknown FD is queried during subject cleanup.
            for name in ("_file_io", "_raw_fileno", "_fstat"):
                monkeypatch.setattr(subject, name, no_action)
            if after_open:
                known.append(opened(*args))
            raise first
        monkeypatch.setattr(subject, "_open_fd", opaque)
        protected_first = None
        try:
            same_error(lambda: copy(item, ledger=ledger), first)
            assert ledger.fd_attempts == [] and ledger.raws == []
            assert "IMAGE_BYTES_DEADLINE_CREATE_UNKNOWN" in getattr(first, "__notes__", [])
            failed(item, first, monkeypatch)
        except BaseException as failure:
            protected_first = failure
            raise
        finally:
            # Exact alias-returned FD known to fixture, subject never attempted.
            # No fstat/fileno/close probe and no guessed numeric value.
            for fd in known:
                if fd not in ledger.fd_attempts:
                    try:
                        ledger.original["_close_fd"](fd)
                    except BaseException:
                        if protected_first is None:
                            raise
                        note(protected_first)


# I31: wrapper/numeric cleanup first stays original and is never retried.
@pytest.mark.parametrize("fault", ["raw-post", "fd-post"])
def test_target_cleanup_original_failure_has_single_attempt_and_no_retry(env, monkeypatch, fault):
    with env(plain=PLAIN) as item:
        ledger = Ledger(monkeypatch)
        name = "_retire_raw" if fault == "raw-post" else "_close_fd"
        actual = getattr(subject, name)
        first = OSError("finite cleanup completed then opaque postfailure")
        def post(value):
            actual(value)
            raise first
        monkeypatch.setattr(subject, name, post)
        same_error(lambda: copy(item, ledger=ledger), first)
        assert ledger.fd_attempts == ledger.fds == ledger.fd_returns and len(ledger.fds) == 1
        assert ledger.calls["retire"] == ledger.calls["close"] == 1 and all(raw.closed for raw in ledger.raws)
        failed(item, first, monkeypatch)
        # Numeric close was attempted: no fixture probe or retry even though
        # this fault deliberately completed the actual close before raising.


# I01: invalid stage domains are refused before any live source is acquired.
def test_invalid_stage_domains_never_invoke_source_provider(env, monkeypatch):
    with env(plain=PLAIN) as item:
        monkeypatch.setattr(subject, "_live_reader", no_action)
        for option in ("source-type", "owner-type", "suffix-type", "suffix-value", "absent-wal", "equal-token"):
            selected = item.token
            if option == "equal-token":
                selected = deadline.Namespace3Deadline(item.token.absolute_monotonic)
            budget = subject.ImageByteBudget(NAME, item.before, deadline_token=selected)
            directory = object() if option == "source-type" else item.directory
            owner = object() if option == "owner-type" else item.owner
            suffix = True if option == "suffix-type" else "-journal" if option == "suffix-value" else "-wal" if option == "absent-wal" else ""
            error(lambda: subject.copy_file_locked(directory, owner, suffix, byte_budget=budget), "INVALID_ARGUMENT")
            assert budget.evidence["copy_read"] == budget.evidence["target_write"] == 0
        error(lambda: subject.copy_file_locked(item.directory, item.owner, "", byte_budget=object()), "INVALID_ARGUMENT")


# I03 / I23: identical path and token do not permit migrating the actual source.
def test_wal_stage_cannot_migrate_to_another_active_source_object(env, monkeypatch):
    with env(wal=True) as item:
        assert copy(item) is None
        original_fee = len(item.expected[""])
        with directories.DeadlineDirectory(item.root, base=item.root, deadline_token=item.token) as alternate:
            assert alternate.path == item.directory.path and alternate is not item.directory
            monkeypatch.setattr(subject, "_live_reader", no_action)
            first = error(lambda: copy(item, "-wal", directory=alternate), "INVALID_ARGUMENT")
            assert item.budget.evidence["copy_read"] == item.budget.evidence["target_write"] == original_fee
            same_error(lambda: sha(item, "-wal"), first)
            failed(item, first, monkeypatch)


# I12: new target empty-FD binding is checked before and after initialization.
@pytest.mark.parametrize("phase", ["before-initialize", "after-initialize"])
def test_target_empty_binding_rejection_prevents_first_sql_byte(env, monkeypatch, phase):
    with env(plain=PLAIN) as item:
        ledger = Ledger(monkeypatch)
        fstat = subject._fstat
        target_count = 0
        def projected(fd):
            nonlocal target_count
            info = fstat(fd)
            if fd in ledger.fds:
                target_count += 1
                if target_count == (1 if phase == "before-initialize" else 2):
                    return projected_stat(info, "st_ino")
            return info
        monkeypatch.setattr(subject, "_fstat", projected)
        with pytest.raises(readonly.ReadonlyImageError) as raised:
            copy(item, ledger=ledger)
        first = raised.value
        assert first.code == "PRIVATE_IMAGE_CHANGED" and ledger.calls["read"] == ledger.calls["write"] == 0
        assert ledger.calls["initialize"] == (0 if phase == "before-initialize" else 1)
        assert_target_close(ledger)
        failed(item, first, monkeypatch)


# I22: malformed read return is unknown data, with no invented read fee.
@pytest.mark.parametrize("reported", [None, bytearray(PLAIN), "text"], ids=["none", "bytearray", "text"])
def test_malformed_read_result_is_refused_without_unknown_quantity_charge(env, monkeypatch, reported):
    with env(plain=PLAIN) as item:
        ledger = Ledger(monkeypatch)
        monkeypatch.setattr(subject, "_raw_read", lambda _raw, _requested: reported)
        first = error(lambda: copy(item, ledger=ledger), "INVALID_RESULT")
        assert item.budget.evidence["copy_read"] == item.budget.evidence["target_write"] == 0
        assert_target_close(ledger)
        failed(item, first, monkeypatch)


# S001 supplements I31 / I32, without a 33rd logical plan label.
@pytest.mark.parametrize("stage", ["copy", "hash"])
def test_original_exception_traceback_attribute_override_cannot_skip_actual_lease_exit(env, monkeypatch, stage):
    class TracebackTrap(OSError):
        def __getattribute__(self, name):
            if name == "__traceback__":
                raise RuntimeError("finite overridden traceback attribute rejection")
            return BaseException.__getattribute__(self, name)

    with env(plain=PLAIN) as item:
        if stage == "hash":
            assert copy(item) is None
        ledger = Ledger(monkeypatch)
        first = TracebackTrap("finite original read consumer failure")
        known = []
        def refused(raw, _requested):
            # Only the exact raw argument already delivered to this alias.
            known.append(io.FileIO.fileno(raw))
            raise first
        monkeypatch.setattr(subject, "_raw_read", refused)
        call = (lambda: copy(item, ledger=ledger)) if stage == "copy" else (lambda: sha(item, ledger=ledger))
        same_error(call, first)
        if stage == "copy":
            assert_target_close(ledger)
            assert ledger.source_returns == [first] and ledger.calls["source-exit"] == 1
        else:
            assert ledger.private_returns == [first] and ledger.calls["private-exit"] == 1
        assert len(known) == 1
        known_closed(known[0])
        assert item.budget.evidence["copy_read" if stage == "copy" else "hash_read"] == 0
        failed(item, first, monkeypatch)
