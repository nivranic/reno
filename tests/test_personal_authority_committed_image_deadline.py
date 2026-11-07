"""r828 actual EX/WAL image composition and finite new-alias faults.

M01-M16 are logical labels, not pytest/native counts. One module fixture owns
the real EX, RW source and borrowed source pin under one existing 30s token.
Only Root imports, collects or runs this candidate.
"""
from collections import Counter
import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace

import pytest

from reno import personal_authority_committed_image_deadline as subject
from reno import personal_authority_directory_deadline as directories
from reno import personal_authority_namespace3_deadline as deadline
from reno import storage_lifecycle as lifecycle
from reno import storage_readonly as readonly
from reno import storage_source as source


pytestmark = pytest.mark.skipif(os.name != "nt", reason="actual Windows image owners and EX")
ALIASES = ("_monotonic", "_require_guard", "_guard_verify", "_source_state",
           "_private_directory", "_directory", "_directory_enter", "_directory_exit",
           "_image_owner", "_byte_budget", "_copy_file", "_private_sha",
           "_sql_lease", "_sql_enter", "_sql_exit", "_temp_cleanup", "_image_verify_source")
FIELDS = {"phase", "temp_state", "private_pin_state", "sql_state", "byte_evidence",
          "bootstrap_evidence", "normal_exit", "source_directory_borrowed"}
ROW = "绍兴-WAL最新已提交"


def native(path):
    text = os.path.abspath(os.fspath(path))
    if text.startswith("\\\\?\\"):
        return text
    return "\\\\?\\UNC\\" + text[2:] if text.startswith("\\\\") else "\\\\?\\" + text


def qa_temp():
    qa = Path(os.environ["RENO_QA_ROOT"]).resolve(strict=True)
    selected = Path(tempfile.gettempdir()).resolve(strict=True)
    selected.relative_to(qa)
    assert selected.is_dir()


@pytest.fixture(scope="module")
def actual_ex_wal(tmp_path_factory):
    """One real initialization/EX; close RW after all image source observations."""
    root = tmp_path_factory.mktemp("绍兴-committed-image")
    profile = lifecycle.StorageProfile(root / "data/绍兴-committed.sqlite3",
                                      root / "media", root / "reports", root / "checkpoints")
    profile.database.parent.mkdir(parents=True)
    initial = sqlite3.connect(native(profile.database))  # Extra fixture SQLite API.
    try:
        initial.executescript("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT); "
                             "INSERT INTO meta VALUES('migration_version','0');")
        initial.commit()
    finally:
        initial.close()
    lifecycle.initialize(profile)  # Original default timeout=30, no fake guard.
    with lifecycle.exclusive(profile) as guard:  # Original default timeout=30.
        assert type(guard) is lifecycle.StorageGuard and guard.exclusive is True
        rw = sqlite3.connect(native(profile.database))
        cleanup_first = None

        def close_source():
            nonlocal rw
            selected, rw = rw, None  # Mark attempted before actual close; no retry.
            if selected is not None:
                selected.close()

        try:
            assert rw.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal"
            rw.execute("PRAGMA wal_autocheckpoint=0")
            rw.execute("CREATE TABLE wal_probe(value TEXT)")
            rw.execute("INSERT INTO wal_probe VALUES(?)", (ROW,))
            rw.commit()
            limited = deadline._RestrictedBudget(deadline.Namespace3Deadline(1.0e15))
            token = limited.effective_deadline
            with directories.DeadlineDirectory(profile.database.parent,
                    base=profile.database.parent, deadline_token=token) as directory:
                before = source.source_state(directory, profile.database.name)
                expected = {suffix: Path(native(profile.database.with_name(profile.database.name + suffix))).read_bytes()
                            for suffix in ("", "-wal") if before[suffix] is not None}
                assert before["-wal"] is not None and expected["-wal"]
                qa_temp()  # Before every actual legacy TEMP constructor as well.
                try:
                    yield SimpleNamespace(profile=profile, guard=guard, directory=directory,
                                          token=token, source_con=rw, before=before, expected=expected)
                finally:
                    # All image SQL -> private pin -> TEMP exits and source
                    # observations precede this fixture-owned source close.
                    close_source()
            limited.clock_only()  # Own source pin has exited; same original token.
        except BaseException as failure:
            cleanup_first = failure
            raise
        finally:
            if rw is not None:
                try:
                    close_source()
                except BaseException:
                    if cleanup_first is None:
                        raise
                    try:
                        BaseException.add_note(cleanup_first, "R828_FIXTURE_CLEANUP_FAILED")
                    except BaseException:
                        pass
        # EX exits here. No source equality check after RW close/checkpoint.


class Scalar:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


class Ledger:
    """Declared new-alias arguments/returns only; no provider private slots."""
    def __init__(self, monkeypatch, item):
        self.original = {name: getattr(subject, name) for name in ALIASES}
        self.calls, self.events = Counter(), []
        self.snapshots, self.copies, self.hashes = [], [], []
        self.temps, self.temp_paths, self.pins, self.sql_leases, self.cons = [], [], [], [], []
        self.exit_attempts, self.exit_returns, self.exit_primaries = [], [], []

        def wrapper(name):
            def observed(*args, **kwargs):
                self.calls[name] += 1
                self.events.append(name)
                if name == "_private_directory":
                    qa_temp()
                if name in ("_sql_exit", "_directory_exit", "_temp_cleanup"):
                    self.exit_attempts.append(name)
                    if name != "_temp_cleanup":
                        self.exit_primaries.append((name, args[2]))
                    if name == "_directory_exit":
                        assert args[0] is not item.directory  # Image never owns outer pin.
                result = self.original[name](*args, **kwargs)
                if name == "_require_guard":
                    assert result is item.guard and kwargs == {"exclusive": True}
                elif name == "_guard_verify":
                    assert args[0] is item.guard
                elif name == "_source_state":
                    assert args == (item.directory, item.profile.database.name) and kwargs == {}
                    self.snapshots.append(result.copy())
                elif name == "_private_directory":
                    self.temps.append(result)
                    self.temp_paths.append(Path(result.path))
                elif name == "_directory":
                    assert kwargs == {"base": args[0], "deadline_token": item.token}
                    self.pins.append(result)
                elif name == "_copy_file":
                    assert args[0] is item.directory and args[2] in ("", "-wal")
                    self.copies.append(args[2])
                elif name == "_private_sha":
                    assert args[1] in ("", "-wal")
                    self.hashes.append((args[1], result))
                elif name == "_sql_lease":
                    self.sql_leases.append(result)
                elif name == "_sql_enter":
                    assert type(result) is sqlite3.Connection
                    self.cons.append(result)
                if name in ("_sql_exit", "_directory_exit", "_temp_cleanup"):
                    self.exit_returns.append(name)
                return result
            return observed

        for name in ALIASES:
            if name != "_monotonic":
                monkeypatch.setattr(subject, name, wrapper(name))


def lease(item, **kwargs):
    return subject.committed_image_locked(item.profile, item.directory,
                                         deadline_token=item.token, **kwargs)


def no_action(*_args, **_kwargs):
    pytest.fail("retired public diagnostic/sticky failure invoked a new provider")


def same_error(call, first):
    with pytest.raises(type(first)) as raised:
        call()
    assert raised.value is first


def error(call, suffix):
    with pytest.raises(subject.CommittedImageDeadlineError) as raised:
        call()
    assert raised.value.code == "COMMITTED_IMAGE_DEADLINE_" + suffix
    return raised.value


def mapped(call, code, *, retriable=False):
    with pytest.raises(readonly.ReadonlyImageError) as raised:
        call()
    assert raised.value.code == code and raised.value.retriable is retriable
    return raised.value


def public_sql_closed(evidence):
    child = evidence["bootstrap_evidence"]
    assert type(child) is dict and child["phase"] == "retired" and child["retired"] is True
    assert child["connection_state"] == "closed"
    assert type(child["cursor_states"]) is tuple and len(child["cursor_states"]) == 4
    assert all(state in ("empty", "closed") for state in child["cursor_states"])
    assert evidence["sql_state"] == "closed"


def failed(selected, first, monkeypatch):
    with monkeypatch.context() as patch:
        for name in ALIASES:
            patch.setattr(subject, name, no_action)
        evidence = selected.evidence
        assert set(evidence) == FIELDS and evidence["phase"] == "retired"
        assert evidence["normal_exit"] is False and evidence["source_directory_borrowed"] is True
        same_error(selected.__enter__, first)
        same_error(lambda: selected.__exit__(None, None, None), first)
        detached = selected.evidence
        detached["normal_exit"], detached["temp_state"] = True, "owned"
        if detached["bootstrap_evidence"] is not None:
            detached["bootstrap_evidence"]["connection_state"] = "owned"
        if detached["byte_evidence"] is not None:
            detached["byte_evidence"]["complete"] = False
        assert selected.evidence == evidence
    return evidence


def known_cleanup(ledger, *, sql=True, pin=True):
    expected = (["_sql_exit"] if sql else []) + (["_directory_exit"] if pin else []) + ["_temp_cleanup"]
    assert ledger.exit_attempts == expected and ledger.exit_returns == expected
    assert all(ledger.calls[name] == 1 for name in expected)


# M01/M02/M12: real EX-backed factory domain, lazy resource acquisition and
# explicitly projected fixed-source faults. No fake positive guards/budgets.
def test_real_ex_factory_domain_lazy_and_preflight_faults(actual_ex_wal, monkeypatch):
    item = actual_ex_wal
    ledger = Ledger(monkeypatch, item)
    selected = lease(item)
    assert type(selected) is subject.CommittedImageDeadlineLease
    assert set(selected.evidence) == FIELDS and selected.evidence["phase"] == "new"
    assert ledger.calls["_private_directory"] == ledger.calls["_sql_enter"] == 0
    for options in ({"attempts": True}, {"attempts": 0}, {"attempts": 2},
                    {"max_bytes": True}, {"max_bytes": 0}, {"max_bytes": readonly.MAX_BYTES_LIMIT + 1}):
        error(lambda: lease(item, **options), "INVALID_ARGUMENT")
    error(lambda: subject.committed_image_locked(object(), item.directory,
          deadline_token=item.token), "INVALID_ARGUMENT")
    other_token = deadline.Namespace3Deadline(item.token.absolute_monotonic)
    error(lambda: subject.committed_image_locked(item.profile, item.directory,
          deadline_token=other_token), "INVALID_ARGUMENT")
    for option in ("source_path", "private_factory", "check_cancelled", "connection", "consumer"):
        with pytest.raises(TypeError):
            lease(item, **{option: object()})
    with pytest.raises(subject.CommittedImageDeadlineError):
        type("LeaseChild", (subject.CommittedImageDeadlineLease,), {})
    capped = lease(item, max_bytes=1)
    first = mapped(capped.__enter__, "SOURCE_SNAPSHOT_TOO_LARGE")
    failed(capped, first, monkeypatch)
    original = subject._source_state
    with monkeypatch.context() as patch:
        def journal_projection(*args, **kwargs):
            state = original(*args, **kwargs)
            info = state[""]
            state["-journal"] = (info[0], info[1], 1, info[3], info[4])
            return state  # Declared small journal projection, no journal I/O.
        patch.setattr(subject, "_source_state", journal_projection)
        candidate = lease(item)
        first = mapped(candidate.__enter__, "SOURCE_ROLLBACK_JOURNAL_PRESENT", retriable=True)
        failed(candidate, first, monkeypatch)
    for code in ("SOURCE_MISSING", "SOURCE_UNSAFE", "SOURCE_CHANGED"):
        with monkeypatch.context() as patch:
            first_provider = FileNotFoundError("r828 fixed-source missing projection") if code == "SOURCE_MISSING" else source.SourceReadError(code)
            def refused(*args, **kwargs):
                raise first_provider
            patch.setattr(subject, "_source_state", refused)
            candidate = lease(item)
            first = mapped(candidate.__enter__, code, retriable=code == "SOURCE_CHANGED")
            failed(candidate, first, monkeypatch)
    assert ledger.calls["_private_directory"] == ledger.calls["_sql_enter"] == 0
    item.directory.verify()


# M03/M04/M05/M14/M15/M16: the only complete positive composition.
def test_actual_ex_wal_image_fingerprint_snapshots_interface_and_normal_cleanup(actual_ex_wal, monkeypatch):
    item = actual_ex_wal
    ledger = Ledger(monkeypatch, item)
    selected = lease(item)
    with selected as image:
        assert type(image) is subject.DeadlineCommittedImage
        con = image.connection
        assert type(con) is sqlite3.Connection and con.in_transaction is True
        cursor = con.execute("SELECT value FROM wal_probe")  # Extra raw caller SQL, outside bootstrap gates.
        try:
            assert cursor.fetchall() == [(ROW,)]
        finally:
            cursor.close()
        fingerprint = image.source_fingerprint
        assert set(fingerprint) == {"format_version", "profile_identity", "files"}
        assert fingerprint["format_version"] == 1 and fingerprint["profile_identity"] == list(item.profile.identity)
        assert set(fingerprint["files"]) == {"", "-wal", "-journal"}
        for suffix, signature in item.before.items():
            if signature is None:
                assert fingerprint["files"][suffix] is None
                continue
            device, inode, size, modified, changed = signature
            expected_sha = hashlib.sha256(item.expected[suffix]).hexdigest()
            assert fingerprint["files"][suffix] == {"device": str(device), "inode": str(inode),
                "byte_size": size, "mtime_ns": str(modified), "ctime_ns": str(changed), "sha256": expected_sha}
        changed = image.source_fingerprint
        changed["files"][""]["sha256"] = "0" * 64
        changed["profile_identity"].append("detached")
        assert image.verify_source() == fingerprint  # One explicit consumer extra verify.
        with pytest.raises(AttributeError):
            image.connection = object()
        assert image.connection is con
    evidence = selected.evidence
    assert evidence["phase"] == "retired" and evidence["normal_exit"] is True
    assert evidence["temp_state"] == evidence["private_pin_state"] == "closed"
    assert evidence["source_directory_borrowed"] is True
    public_sql_closed(evidence)
    known_cleanup(ledger)
    assert ledger.copies == ["", "-wal"]
    assert ledger.hashes == [(suffix, hashlib.sha256(item.expected[suffix]).hexdigest()) for suffix in ("", "-wal")]
    assert ledger.snapshots == [item.before] * 6  # Five fixed positions plus explicit consumer verify.
    events = [name for name in ledger.events if name in ("_source_state", "_copy_file", "_private_sha", "_sql_enter", "_image_verify_source", "_guard_verify", "_sql_exit", "_directory_exit", "_temp_cleanup")]
    assert events == ["_source_state", "_copy_file", "_private_sha", "_copy_file", "_private_sha",
        "_source_state", "_sql_enter", "_source_state", "_image_verify_source", "_source_state", "_guard_verify",
        "_image_verify_source", "_source_state", "_guard_verify", "_image_verify_source", "_source_state", "_guard_verify",
        "_sql_exit", "_directory_exit", "_temp_cleanup"]
    assert all(primary is None for _, primary in ledger.exit_primaries)
    assert all(not path.exists() for path in ledger.temp_paths)  # Only known normal cleanup.
    with pytest.raises(sqlite3.ProgrammingError):
        con.execute("SELECT 1")  # Known public closed Con; never probe UNKNOWN.
    with pytest.raises(readonly.ReadonlyImageError) as raised:
        image.verify_source()
    assert raised.value.code == "IMAGE_CLOSED"
    with monkeypatch.context() as patch:
        for name in ALIASES:
            patch.setattr(subject, name, no_action)
        assert image.connection is con and image.source_fingerprint == fingerprint
        assert selected.evidence == evidence
        error(selected.__enter__, "CLOSED")
        error(lambda: selected.__exit__(None, None, None), "CLOSED")
    item.directory.verify()  # Borrowed root still actual ACTIVE; no source RW close yet.


# M06: metadata-rejection projection after actual DB/WAL copy/SHA, not an ABA
# or native real-writer proof. Known owners still receive one cleanup attempt.
def test_source_change_after_copy_latches_mapping_and_skips_sql(actual_ex_wal, monkeypatch):
    item = actual_ex_wal
    ledger = Ledger(monkeypatch, item)
    selected = lease(item)
    original = subject._source_state
    calls = 0
    def changed(*args, **kwargs):
        nonlocal calls
        calls += 1
        state = original(*args, **kwargs)
        if calls == 2:
            value = state["-wal"]
            state["-wal"] = (value[0], value[1], value[2] + 1, value[3], value[4])
        return state
    monkeypatch.setattr(subject, "_source_state", changed)
    first = mapped(selected.__enter__, "SOURCE_CHANGED", retriable=True)
    evidence = failed(selected, first, monkeypatch)
    assert ledger.calls["_sql_enter"] == 0 and ledger.copies == ["", "-wal"]
    assert evidence["sql_state"] == "empty" and evidence["private_pin_state"] == "unknown"
    assert evidence["temp_state"] == "closed" and evidence["byte_evidence"]["complete"] is True
    known_cleanup(ledger, sql=False)


# M07/M08: actual legal owner/enter return before only the new clock expires.
@pytest.mark.parametrize("stage", ["_private_directory", "_directory_enter"])
def test_temp_or_private_pin_postreturn_expiry_keeps_single_cleanup(actual_ex_wal, monkeypatch, stage):
    item = actual_ex_wal
    ledger = Ledger(monkeypatch, item)
    selected = lease(item)
    scalar = Scalar(subject._monotonic())
    original = getattr(subject, stage)
    def returned(*args, **kwargs):
        value = original(*args, **kwargs)
        scalar.value = item.token.absolute_monotonic
        return value
    monkeypatch.setattr(subject, "_monotonic", scalar)
    monkeypatch.setattr(subject, stage, returned)
    first = error(selected.__enter__, "DEADLINE")
    evidence = failed(selected, first, monkeypatch)
    assert evidence["temp_state"] == "closed" and evidence["sql_state"] == "empty"
    if stage == "_private_directory":
        assert evidence["private_pin_state"] == "empty"
        assert ledger.calls["_directory_enter"] == 0
        known_cleanup(ledger, sql=False, pin=False)
    else:
        assert evidence["private_pin_state"] == "unknown"  # Primary-bearing provider has no public close proof.
        known_cleanup(ledger, sql=False)


def test_sql_enter_postreturn_expiry_retains_public_known_closed_fact(actual_ex_wal, monkeypatch):
    item = actual_ex_wal
    ledger = Ledger(monkeypatch, item)
    selected = lease(item)
    scalar = Scalar(subject._monotonic())
    original = subject._sql_enter
    def returned(*args, **kwargs):
        value = original(*args, **kwargs)
        scalar.value = item.token.absolute_monotonic
        return value
    monkeypatch.setattr(subject, "_monotonic", scalar)
    monkeypatch.setattr(subject, "_sql_enter", returned)
    first = error(selected.__enter__, "DEADLINE")
    evidence = failed(selected, first, monkeypatch)
    public_sql_closed(evidence)
    assert evidence["private_pin_state"] == "unknown" and evidence["temp_state"] == "closed"
    known_cleanup(ledger)


# M09: no actual PIN acquire is lost and fixture need not retry an UNKNOWN.
def test_pin_enter_before_actual_call_preserves_first_and_is_not_reexited(actual_ex_wal, monkeypatch):
    item = actual_ex_wal
    ledger = Ledger(monkeypatch, item)
    selected = lease(item)
    first = OSError("r828 finite private-pin before-call fault")
    def refused(*_args, **_kwargs):
        raise first
    monkeypatch.setattr(subject, "_directory_enter", refused)
    same_error(selected.__enter__, first)
    evidence = failed(selected, first, monkeypatch)
    assert evidence["private_pin_state"] == "unknown" and evidence["sql_state"] == "empty"
    assert evidence["temp_state"] == "closed" and ledger.calls["_directory_enter"] == 0
    known_cleanup(ledger, sql=False, pin=False)


# M11/M14: actual body-first + actual provider exit None is safely conservative
# for a primary-bearing pin, while SQL has detached public closed evidence.
def test_body_first_traceback_getter_traps_keep_conservative_pin_unknown(actual_ex_wal, monkeypatch):
    item = actual_ex_wal
    ledger = Ledger(monkeypatch, item)
    selected = lease(item)
    class GetterTrap(OSError):
        @property
        def __traceback__(self):
            pytest.fail("consumer traceback getter was read")
        @property
        def with_traceback(self):
            pytest.fail("consumer with_traceback getter was read")
    first = GetterTrap("r828 finite original consumer first")
    with pytest.raises(GetterTrap) as raised:
        with selected:
            raise first
    assert raised.value is first
    evidence = failed(selected, first, monkeypatch)
    public_sql_closed(evidence)
    assert evidence["private_pin_state"] == "unknown" and evidence["temp_state"] == "closed"
    assert all(primary is first for _, primary in ledger.exit_primaries)
    known_cleanup(ledger)


# M10: alias call outcome does not override known provider public close facts.
def test_sql_exit_forward_then_throw_keeps_public_closed_and_outer_first(actual_ex_wal, monkeypatch):
    item = actual_ex_wal
    ledger = Ledger(monkeypatch, item)
    selected = lease(item)
    original = subject._sql_exit
    first = OSError("r828 finite outer alias after actual SQL exit")
    def closed_then_throw(*args, **kwargs):
        assert original(*args, **kwargs) is None
        raise first
    monkeypatch.setattr(subject, "_sql_exit", closed_then_throw)
    with pytest.raises(OSError) as raised:
        with selected:
            pass  # A faulted exit, not a second complete positive case.
    assert raised.value is first
    evidence = failed(selected, first, monkeypatch)
    public_sql_closed(evidence)
    assert evidence["bootstrap_evidence"]["normal_exit"] is True
    assert evidence["private_pin_state"] == "unknown" and evidence["temp_state"] == "closed"
    assert ledger.exit_primaries == [("_sql_exit", None), ("_directory_exit", first)]
    known_cleanup(ledger)


# M13: public verify alias failure must latch even if caller catches it.
def test_caught_public_verify_alias_failure_cannot_restore_normal_exit(actual_ex_wal, monkeypatch):
    item = actual_ex_wal
    ledger = Ledger(monkeypatch, item)
    selected = lease(item)
    original = subject._image_verify_source
    first = OSError("r828 finite public verify alias first")
    calls = 0
    def refused_on_consumer(image):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise first
        return original(image)
    monkeypatch.setattr(subject, "_image_verify_source", refused_on_consumer)
    with pytest.raises(OSError) as raised:
        with selected as image:
            same_error(image.verify_source, first)
            same_error(image.verify_source, first)
            assert calls == 2
    assert raised.value is first and calls == 2
    evidence = failed(selected, first, monkeypatch)
    public_sql_closed(evidence)
    assert evidence["private_pin_state"] == "unknown" and evidence["temp_state"] == "closed"
    known_cleanup(ledger)
