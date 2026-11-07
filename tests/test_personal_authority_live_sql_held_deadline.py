"""r830 real sustained source FDs, five scans and finite new-alias faults.

Root alone imports/collects/runs. Fixture preparation reads are independent of
the subject's actual ledger. Only declared new aliases are patched.
"""
from collections import Counter, defaultdict
from contextlib import ExitStack
import copy
import hashlib
import io
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace

import pytest

from reno import personal_authority_committed_image_deadline as images
from reno import personal_authority_directory_deadline as directories
from reno import personal_authority_live_sql_held_deadline as subject
from reno import personal_authority_namespace3_deadline as deadline
from reno import storage_lifecycle as lifecycle
from reno import storage_readonly as readonly
from reno import storage_source as source


pytestmark = pytest.mark.skipif(os.name != "nt", reason="actual Windows shared source FDs")
SUFFIXES = ("", "-wal", "-journal")
ALIASES = ("_monotonic", "_require_guard", "_directory_verify", "_directory_stat",
           "_entry_stat", "_identity", "_live_reader", "_lease_enter", "_lease_terminal",
           "_lease_exit", "_raw_fileno", "_raw_seek", "_raw_read", "_fstat",
           "_hash_new", "_hash_update", "_hash_hexdigest", "_safe_directory_stat")
FILE_FIELDS = {"phase", "lease_state", "passes_started", "passes_completed",
               "terminal_verified", "normal_exit", "source_directory_borrowed"}
BUDGET_FIELDS = {"declared_file_count", "declared_bytes", "read_bytes", "read_bytes_limit",
                 "read_calls", "read_calls_limit", "eof_reads", "eof_limit",
                 "passes_started", "passes_completed", "failed", "retired"}
ROW = "绍兴-持续FD-WAL已提交"


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
def actual_source(tmp_path_factory):
    """One actual EX/RW source/pin and one existing effective 30s token."""
    root = tmp_path_factory.mktemp("绍兴-live-held")
    profile = lifecycle.StorageProfile(root / "data/绍兴-held.sqlite3", root / "media",
                                      root / "reports", root / "checkpoints")
    profile.database.parent.mkdir(parents=True)
    initial = sqlite3.connect(native(profile.database))
    try:
        initial.executescript("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT); "
                             "INSERT INTO meta VALUES('migration_version','0');")
        initial.commit()
    finally:
        initial.close()
    lifecycle.initialize(profile)  # Actual original default 30, no fake guard.
    with lifecycle.exclusive(profile) as guard:  # One original default 30 EX.
        assert type(guard) is lifecycle.StorageGuard and guard.exclusive is True
        rw = sqlite3.connect(native(profile.database))
        first = None

        def close_source():
            nonlocal rw
            actual, rw = rw, None  # Attempted once, even if actual close raises.
            if actual is not None:
                actual.close()

        try:
            assert rw.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal"
            rw.execute("PRAGMA wal_autocheckpoint=0")
            rw.execute("CREATE TABLE wal_probe(value TEXT)")
            rw.execute("INSERT INTO wal_probe VALUES(?)", (ROW,))
            rw.commit()
            journal = profile.database.with_name(profile.database.name + "-journal")
            with open(native(journal), "xb") as empty:
                assert empty.write(b"") == 0
            limited = deadline._RestrictedBudget(deadline.Namespace3Deadline(1.0e15))
            token = limited.effective_deadline
            with directories.DeadlineDirectory(profile.database.parent,
                    base=profile.database.parent, deadline_token=token) as directory:
                before = source.source_state(directory, profile.database.name)
                assert all(before[suffix] is not None for suffix in SUFFIXES)
                expected = {suffix: Path(native(profile.database.with_name(profile.database.name + suffix))).read_bytes()
                            for suffix in SUFFIXES}  # Real QA baseline preparation; not subject ledger.
                assert expected["-wal"] and expected["-journal"] == b""
                files = {}
                for suffix, signature in before.items():
                    device, inode, size, modified, changed = signature
                    assert len(expected[suffix]) == size
                    files[suffix] = {"device": str(device), "inode": str(inode), "byte_size": size,
                        "mtime_ns": str(modified), "ctime_ns": str(changed),
                        "sha256": hashlib.sha256(expected[suffix]).hexdigest()}
                fingerprint = {"format_version": 1, "profile_identity": list(profile.identity), "files": files}
                qa_temp()  # Before the one actual image/private TEMP positive.
                try:
                    yield SimpleNamespace(profile=profile, guard=guard, directory=directory,
                        token=token, source_con=rw, expected=expected, before=before,
                        fingerprint=fingerprint)
                finally:
                    # All image observations/cleanup and Held terminals precede
                    # fixture-owned RW close. No source equality after close.
                    close_source()
            limited.clock_only()  # Own outer source pin exited, same original token.
        except BaseException as failure:
            first = failure
            raise
        finally:
            if rw is not None:
                try:
                    close_source()
                except BaseException:
                    if first is None:
                        raise
                    try:
                        BaseException.add_note(first, "R830_FIXTURE_CLEANUP_FAILED")
                    except BaseException:
                        pass
        # Actual EX exits here. No UNKNOWN FD probe/retry or source re-verify.


class Scalar:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


class Ledger:
    """Finite new-alias parameters and actual returns, no private slots."""
    def __init__(self, monkeypatch, item):
        self.original = {name: getattr(subject, name) for name in ALIASES}
        self.calls, self.returns = Counter(), Counter()
        self.events, self.reads, self.seeks, self.fstats, self.native_terminals = [], [], [], [], []
        self.lease_suffix, self.raw_suffix, self.raw_fds = {}, {}, defaultdict(set)
        self.exit_attempts, self.exit_returns, self.exit_primaries, self.before_exit = [], [], [], []
        self.budget, self.image_lease = None, None

        def wrapper(name):
            def observed(*args, **kwargs):
                self.calls[name] += 1
                self.events.append(name)
                if name == "_lease_exit":
                    self.exit_attempts.append(self.lease_suffix[id(args[0])])
                    self.exit_primaries.append(args[2])
                    if self.budget is not None:
                        self.before_exit.append(copy.deepcopy(self.budget.evidence))
                result = self.original[name](*args, **kwargs)
                self.returns[name] += 1
                if name == "_require_guard":
                    assert result is item.guard
                    assert kwargs == {"exclusive": True} or args[1:] == (True,) and kwargs == {}
                elif name == "_live_reader":
                    assert args[0] is item.directory and kwargs == {"deadline_token": item.token}
                    assert args[1].startswith(item.profile.database.name)
                    suffix = args[1][len(item.profile.database.name):]
                    assert suffix in SUFFIXES
                    self.lease_suffix[id(result)] = suffix
                elif name == "_lease_enter":
                    assert type(result) is io.FileIO and result.closefd is False
                    self.raw_suffix[id(result)] = self.lease_suffix[id(args[0])]
                elif name == "_raw_fileno":
                    assert type(result) is int and result >= 0
                    self.raw_fds[id(args[0])].add(result)
                elif name == "_raw_seek":
                    assert args[1:] == (0, os.SEEK_SET) and result == 0
                    stage = None if self.image_lease is None else self.image_lease.evidence["phase"]
                    self.seeks.append((self.raw_suffix[id(args[0])], stage))
                elif name == "_raw_read":
                    assert type(args[1]) is int and 1 <= args[1] <= 128 * 1024
                    self.reads.append((self.raw_suffix[id(args[0])], args[0], args[1], result))
                elif name == "_fstat":
                    assert type(args[0]) is int and args[0] in set().union(*self.raw_fds.values())
                    self.fstats.append(args[0])
                elif name == "_lease_terminal":
                    stage = None if self.image_lease is None else self.image_lease.evidence["phase"]
                    self.native_terminals.append((self.lease_suffix[id(args[0])], stage))
                if name == "_lease_exit":
                    self.exit_returns.append(self.lease_suffix[id(args[0])])
                return result
            return observed

        for name in ALIASES:
            if name != "_monotonic":
                monkeypatch.setattr(subject, name, wrapper(name))


def budget(item, ledger, fingerprint=None):
    chosen = item.fingerprint if fingerprint is None else fingerprint
    value = subject.LiveSqlHeldBudget(item.profile, chosen, deadline_token=item.token)
    assert value.deadline_token is item.token
    ledger.budget = value
    return value


def held(item, chosen, suffix):
    return subject.held_file_locked(item.directory, suffix, read_budget=chosen, deadline_token=item.token)


def no_action(*_args, **_kwargs):
    pytest.fail("retired diagnostic/sticky failure invoked a new consumer")


def same_error(call, first):
    with pytest.raises(type(first)) as caught:
        call()
    assert caught.value is first


def error(call, suffix):
    with pytest.raises(subject.LiveSqlHeldDeadlineError) as caught:
        call()
    assert caught.value.code == "LIVE_SQL_HELD_DEADLINE_" + suffix
    return caught.value


def failed(chosen, selected, first, monkeypatch):
    with monkeypatch.context() as patch:
        for name in ALIASES:
            patch.setattr(subject, name, no_action)
        file_evidence, evidence = selected.evidence, chosen.evidence
        assert set(file_evidence) == FILE_FIELDS and file_evidence["phase"] == "retired"
        assert file_evidence["normal_exit"] is False and file_evidence["source_directory_borrowed"] is True
        assert set(evidence) == BUDGET_FIELDS and evidence["failed"] is True and evidence["retired"] is True
        assert set(evidence["passes_started"]) == set(evidence["passes_completed"]) == set(SUFFIXES)
        same_error(selected.__enter__, first)
        same_error(selected.verify, first)
        same_error(lambda: selected.__exit__(None, None, None), first)
        detached, local = chosen.evidence, selected.evidence
        detached["read_bytes"], detached["retired"] = 0, False
        detached["passes_started"][""] = 0
        local["normal_exit"], local["lease_state"] = True, "closed"
        assert chosen.evidence == evidence and selected.evidence == file_evidence
    return file_evidence, evidence


# R03/R04/R05/R13/R15/R16: the only complete positive, including real image.
def test_real_ex_image_outer_three_held_five_scans_and_actual_eof(actual_source, monkeypatch):
    item = actual_source
    ledger = Ledger(monkeypatch, item)
    image_lease = images.committed_image_locked(item.profile, item.directory,
                                               deadline_token=item.token, attempts=1)
    ledger.image_lease = image_lease
    with ExitStack() as holder_stack:
        with image_lease as image:
            fingerprint = image.source_fingerprint
            assert fingerprint == item.fingerprint  # Actual copied image joins real QA raw/metadata reference.
            chosen = budget(item, ledger, fingerprint)
            files = {suffix: holder_stack.enter_context(held(item, chosen, suffix)) for suffix in SUFFIXES}
            assert chosen.evidence["passes_completed"] == {suffix: 2 for suffix in SUFFIXES}
            for suffix, selected in files.items():
                assert type(selected) is subject.DeadlineLiveSqlHeldFile
                assert selected.byte_size == len(item.expected[suffix])
                assert selected.sha256 == hashlib.sha256(item.expected[suffix]).hexdigest()
                assert set(selected.directory_identity) == {"device", "inode"}
                path_identity, handle_identity = selected.path_identity, selected.handle_identity
                assert set(path_identity) == set(handle_identity) == {"device", "inode", "byte_size", "mtime_ns", "ctime_ns", "mode", "nlink"}
                for key in set(path_identity) - {"ctime_ns"}:
                    assert path_identity[key] == handle_identity[key]
                changed = selected.path_identity
                changed["inode"] = "detached"
                assert selected.path_identity == path_identity
                assert selected.verify() is None  # Origin yield-before pass3.
            assert image.verify_source() == fingerprint  # Active image only.
            cursor = image.connection.execute("SELECT value FROM wal_probe")  # Extra raw caller SQL.
            try:
                assert cursor.fetchall() == [(ROW,)]
            finally:
                cursor.close()
            for selected in files.values():
                assert selected.verify() is None  # Origin consumer-after pass4.
            assert image.verify_source() == fingerprint
            assert chosen.evidence["passes_completed"] == {suffix: 4 for suffix in SUFFIXES}
        assert image_lease.evidence["phase"] == "retired" and image_lease.evidence["normal_exit"] is True
        assert chosen.evidence["retired"] is False
        assert all(selected.evidence["phase"] == "active" for selected in files.values())
        assert image.source_fingerprint == fingerprint  # Historical; never call inactive verify.
    evidence = chosen.evidence
    declared = sum(len(item.expected[suffix]) for suffix in SUFFIXES)
    assert set(evidence) == BUDGET_FIELDS
    assert evidence["declared_file_count"] == 3 and evidence["declared_bytes"] == declared
    assert evidence["read_bytes"] == 5 * declared
    assert evidence["read_bytes_limit"] == 5 * (declared + 3)
    assert evidence["eof_reads"] == evidence["eof_limit"] == 15
    assert evidence["read_calls_limit"] == evidence["read_bytes_limit"] + 15
    assert evidence["read_calls"] == len(ledger.reads)
    assert evidence["read_bytes"] == sum(len(value) for _, _, _, value in ledger.reads)
    assert evidence["eof_reads"] == sum(value == b"" for _, _, _, value in ledger.reads)
    assert evidence["passes_started"] == evidence["passes_completed"] == {suffix: 5 for suffix in SUFFIXES}
    assert evidence["failed"] is False and evidence["retired"] is True
    assert len(ledger.raw_fds) == 3 and all(len(values) == 1 for values in ledger.raw_fds.values())
    assert len(set().union(*ledger.raw_fds.values())) == 3
    for suffix in SUFFIXES:
        assert [stage for current, stage in ledger.seeks if current == suffix] == ["active"] * 4 + ["retired"]
        assert [stage for current, stage in ledger.native_terminals if current == suffix][-1] == "retired"
        local = files[suffix].evidence
        assert set(local) == FILE_FIELDS and local["phase"] == "retired" and local["lease_state"] == "closed"
        assert local["passes_started"] == local["passes_completed"] == 5
        assert local["terminal_verified"] is True and local["normal_exit"] is True
    assert ledger.exit_attempts == ledger.exit_returns == list(reversed(SUFFIXES))
    assert ledger.exit_primaries == [None] * 3
    assert ledger.before_exit[-1]["passes_completed"] == {suffix: 5 for suffix in SUFFIXES}
    assert ledger.before_exit[-1]["retired"] is False  # Last pass5 alone cannot retire budget.
    journal_reads = [(requested, value) for suffix, _, requested, value in ledger.reads if suffix == "-journal"]
    assert journal_reads == [(1, b"")] * 5
    with monkeypatch.context() as patch:
        for name in ALIASES:
            patch.setattr(subject, name, no_action)
        assert chosen.evidence == evidence
        for selected in files.values():
            assert selected.byte_size >= 0 and len(selected.sha256) == 64
            assert selected.path_identity and selected.handle_identity and selected.directory_identity
            history = selected.evidence
            error(selected.__enter__, "CLOSED")
            assert chosen.evidence == evidence and selected.evidence == history
            error(selected.verify, "CLOSED")
            assert chosen.evidence == evidence and selected.evidence == history
            error(lambda: selected.__exit__(None, None, None), "CLOSED")
            assert chosen.evidence == evidence and selected.evidence == history
    item.directory.verify()  # Outer pin still actual ACTIVE, source RW still open.


# R01/R02/R14: exact domain and lazy resource, plus immutable real-input fp.
def test_exact_domain_lazy_budget_and_detached_fixed_fields(actual_source, monkeypatch):
    item = actual_source
    ledger = Ledger(monkeypatch, item)
    reference = copy.deepcopy(item.fingerprint)
    chosen = budget(item, ledger, reference)
    reference["files"][""]["byte_size"] += 99
    reference["files"][""]["sha256"] = "0" * 64
    selected = held(item, chosen, "")
    assert selected.evidence["phase"] == "new" and ledger.calls["_live_reader"] == ledger.calls["_lease_enter"] == 0
    assert set(chosen.evidence) == BUDGET_FIELDS and chosen.evidence["declared_bytes"] == sum(map(len, item.expected.values()))
    assert chosen.evidence["passes_started"] == chosen.evidence["passes_completed"] == {suffix: 0 for suffix in SUFFIXES}
    assert chosen.evidence["retired"] is False
    for options in ("callback", "max_seconds", "passes_per_file", "provider", "image", "connection"):
        with pytest.raises(TypeError):
            subject.LiveSqlHeldBudget(item.profile, item.fingerprint, deadline_token=item.token, **{options: object()})
    error(lambda: subject.LiveSqlHeldBudget(object(), item.fingerprint, deadline_token=item.token), "INVALID_ARGUMENT")
    other_token = deadline.Namespace3Deadline(item.token.absolute_monotonic)
    wrong_token_budget = subject.LiveSqlHeldBudget(item.profile, item.fingerprint, deadline_token=item.token)
    error(lambda: subject.held_file_locked(item.directory, "", read_budget=wrong_token_budget, deadline_token=other_token), "INVALID_ARGUMENT")
    for suffix in ("-shm", "../outside", "-invalid"):
        separate_budget = subject.LiveSqlHeldBudget(item.profile, item.fingerprint, deadline_token=item.token)
        error(lambda: held(item, separate_budget, suffix), "INVALID_ARGUMENT")
    with pytest.raises(subject.LiveSqlHeldDeadlineError):
        type("BudgetChild", (subject.LiveSqlHeldBudget,), {})
    with pytest.raises(subject.LiveSqlHeldDeadlineError):
        type("HeldChild", (subject.DeadlineLiveSqlHeldFile,), {})
    absent = copy.deepcopy(item.fingerprint)
    absent["files"]["-journal"] = None
    other = subject.LiveSqlHeldBudget(item.profile, absent, deadline_token=item.token)
    assert other.evidence["declared_file_count"] == 2
    assert other.evidence["passes_started"]["-journal"] == other.evidence["passes_completed"]["-journal"] == 0
    error(lambda: held(item, other, "-journal"), "INVALID_ARGUMENT")
    assert ledger.calls["_live_reader"] == 0


# R08: known legal bytes/EOF0 first, then only the new scalar expires.
@pytest.mark.parametrize("suffix", ["", "-journal"], ids=["data", "empty-eof"])
def test_actual_read_return_postexpiry_charges_bytes_call_and_eof(actual_source, monkeypatch, suffix):
    item = actual_source
    ledger = Ledger(monkeypatch, item)
    chosen = budget(item, ledger)
    selected = held(item, chosen, suffix)
    scalar = Scalar(subject._monotonic())
    original = subject._raw_read
    def returned(*args, **kwargs):
        value = original(*args, **kwargs)
        assert type(value) is bytes
        scalar.value = item.token.absolute_monotonic
        return value
    monkeypatch.setattr(subject, "_monotonic", scalar)
    monkeypatch.setattr(subject, "_raw_read", returned)
    first = error(selected.__enter__, "DEADLINE")
    local, evidence = failed(chosen, selected, first, monkeypatch)
    assert len(ledger.reads) == 1
    returned_bytes = ledger.reads[0][3]
    assert evidence["read_bytes"] == len(returned_bytes) and evidence["read_calls"] == 1
    assert evidence["eof_reads"] == int(returned_bytes == b"")
    assert evidence["passes_started"][suffix] == 1 and evidence["passes_completed"][suffix] == 0
    assert local["lease_state"] == "unknown" and local["terminal_verified"] is False
    assert ledger.exit_attempts == ledger.exit_returns == [suffix] and ledger.exit_primaries == [first]


# R02/R11: unused other suffix allowance cannot authorize DB's sixth pass.
def test_sixth_scan_refuses_seek_read_and_cannot_borrow_suffix_allowance(actual_source, monkeypatch):
    item = actual_source
    ledger = Ledger(monkeypatch, item)
    chosen = budget(item, ledger)
    selected = held(item, chosen, "")
    with pytest.raises(subject.LiveSqlHeldDeadlineError) as raised:
        with selected:
            for _ in range(3):
                assert selected.verify() is None  # Enter2 + three public calls = 5.
            evidence = chosen.evidence
            assert evidence["passes_completed"][""] == 5 and evidence["retired"] is False
            assert evidence["passes_started"]["-wal"] == evidence["passes_started"]["-journal"] == 0
            before = ledger.calls["_raw_seek"], ledger.calls["_raw_read"]
            first = error(selected.verify, "LIMIT")
            assert (ledger.calls["_raw_seek"], ledger.calls["_raw_read"]) == before
            same_error(selected.verify, first)
    assert raised.value is first
    local, evidence = failed(chosen, selected, first, monkeypatch)
    assert evidence["passes_started"][""] == evidence["passes_completed"][""] == 5
    assert local["lease_state"] == "unknown" and local["terminal_verified"] is False
    assert ledger.exit_attempts == ledger.exit_returns == [""]


def test_active_reenter_caught_first_blocks_success_and_keeps_one_unknown_cleanup(actual_source, monkeypatch):
    item = actual_source
    ledger = Ledger(monkeypatch, item)
    chosen = budget(item, ledger)
    selected = held(item, chosen, "")
    with pytest.raises(subject.LiveSqlHeldDeadlineError) as raised:
        with selected:
            assert chosen.evidence["passes_completed"][""] == 2
            before = ledger.calls["_raw_seek"], ledger.calls["_raw_read"]
            first = error(selected.__enter__, "CLOSED")
            assert chosen.evidence["failed"] is True and chosen.evidence["retired"] is True
            same_error(selected.verify, first)
            assert (ledger.calls["_raw_seek"], ledger.calls["_raw_read"]) == before
    assert raised.value is first
    local, evidence = failed(chosen, selected, first, monkeypatch)
    assert evidence["passes_started"][""] == evidence["passes_completed"][""] == 2
    assert local["terminal_verified"] is False and local["lease_state"] == "unknown"
    assert (ledger.calls["_raw_seek"], ledger.calls["_raw_read"]) == before
    assert ledger.exit_attempts == ledger.exit_returns == [""] and ledger.exit_primaries == [first]


def test_lease_enter_before_actual_call_never_reexits_unacquired_owner(actual_source, monkeypatch):
    item = actual_source
    ledger = Ledger(monkeypatch, item)
    chosen = budget(item, ledger)
    selected = held(item, chosen, "")
    first = OSError("r830 finite before actual source enter")
    def refused(*_args, **_kwargs):
        raise first
    monkeypatch.setattr(subject, "_lease_enter", refused)
    same_error(selected.__enter__, first)
    local, evidence = failed(chosen, selected, first, monkeypatch)
    assert evidence["read_bytes"] == evidence["eof_reads"] == 0
    assert evidence["passes_started"][""] == 0 and local["lease_state"] == "unknown"
    assert ledger.calls["_lease_enter"] == ledger.calls["_lease_exit"] == 0
    assert ledger.exit_attempts == []


def test_body_first_getter_traps_skip_success_scan_and_keep_primary_unknown(actual_source, monkeypatch):
    item = actual_source
    ledger = Ledger(monkeypatch, item)
    chosen = budget(item, ledger)
    selected = held(item, chosen, "")
    class GetterTrap(OSError):
        @property
        def __traceback__(self):
            pytest.fail("body traceback getter was read")
        @property
        def with_traceback(self):
            pytest.fail("body with_traceback getter was read")
    first = GetterTrap("r830 original consumer first")
    with pytest.raises(GetterTrap) as raised:
        with selected:
            assert chosen.evidence["passes_completed"][""] == 2
            before = ledger.calls["_raw_read"]
            raise first
    assert raised.value is first and ledger.calls["_raw_read"] == before
    local, evidence = failed(chosen, selected, first, monkeypatch)
    assert evidence["passes_completed"][""] == 2 and local["terminal_verified"] is False
    assert local["lease_state"] == "unknown"
    assert ledger.exit_attempts == ledger.exit_returns == [""] and ledger.exit_primaries == [first]


def test_actual_exit_forward_then_throw_keeps_unknown_without_second_closer(actual_source, monkeypatch):
    item = actual_source
    ledger = Ledger(monkeypatch, item)
    chosen = budget(item, ledger)
    selected = held(item, chosen, "")
    original = subject._lease_exit
    first = OSError("r830 finite alias after actual source exit")
    def closed_then_throw(*args, **kwargs):
        assert original(*args, **kwargs) is None
        raise first
    monkeypatch.setattr(subject, "_lease_exit", closed_then_throw)
    with pytest.raises(OSError) as raised:
        with selected:
            assert selected.verify() is None
            assert selected.verify() is None  # Four completed scans, terminal is fifth.
    assert raised.value is first
    local, evidence = failed(chosen, selected, first, monkeypatch)
    assert evidence["passes_completed"][""] == 5 and local["terminal_verified"] is True
    assert local["lease_state"] == "unknown"  # No public r825 close evidence after outer throw.
    assert ledger.exit_attempts == ledger.exit_returns == [""] and ledger.exit_primaries == [None]


def test_late_native_after_actual_forward_cannot_upgrade_failed_verify(actual_source, monkeypatch):
    item = actual_source
    ledger = Ledger(monkeypatch, item)
    chosen = budget(item, ledger)
    selected = held(item, chosen, "-wal")
    original = subject._lease_terminal
    def changed(*args, **kwargs):
        assert original(*args, **kwargs) is None
        raise source.SourceReadError("SOURCE_CHANGED")  # Explicit late-consumer projection.
    with pytest.raises(readonly.ReadonlyImageError) as raised:
        with selected:
            monkeypatch.setattr(subject, "_lease_terminal", changed)
            with pytest.raises(readonly.ReadonlyImageError) as caught:
                selected.verify()
            first = caught.value
            assert first.code == "SOURCE_CHANGED" and first.retriable is True
            same_error(selected.verify, first)
    assert raised.value is first
    local, evidence = failed(chosen, selected, first, monkeypatch)
    assert local["terminal_verified"] is False and local["lease_state"] == "unknown"
    assert evidence["read_bytes"] >= 2 * len(item.expected["-wal"])
    assert ledger.exit_attempts == ledger.exit_returns == ["-wal"]


def test_malformed_read_projection_never_invents_unknown_byte_cost(actual_source, monkeypatch):
    item = actual_source
    ledger = Ledger(monkeypatch, item)
    chosen = budget(item, ledger)
    selected = held(item, chosen, "")
    def malformed(*_args, **_kwargs):
        return None  # No physical FileIO.read forwarding in this projection.
    monkeypatch.setattr(subject, "_raw_read", malformed)
    first = error(selected.__enter__, "INVALID_RESULT")
    local, evidence = failed(chosen, selected, first, monkeypatch)
    assert evidence["read_bytes"] == evidence["eof_reads"] == 0
    assert evidence["passes_started"][""] == 1 and evidence["passes_completed"][""] == 0
    assert ledger.reads == [] and local["lease_state"] == "unknown"
    assert ledger.exit_attempts == ledger.exit_returns == [""]


# Last physical mutation: subsequent tests never pretend the old failed budget
# or baseline recovered. This changes only the fixture-owned RW source WAL.
def test_actual_rw_commit_changes_held_wal_and_first_cannot_be_caught_away(actual_source, monkeypatch):
    item = actual_source
    ledger = Ledger(monkeypatch, item)
    chosen = budget(item, ledger)
    selected = held(item, chosen, "-wal")
    with pytest.raises(readonly.ReadonlyImageError) as raised:
        with selected:
            item.source_con.execute("INSERT INTO wal_probe VALUES(?)", ("真实QA后继提交",))
            item.source_con.commit()  # Extra caller source write, not subject SQL API.
            with pytest.raises(readonly.ReadonlyImageError) as caught:
                selected.verify()
            first = caught.value
            assert first.code == "SOURCE_CHANGED" and first.retriable is True
            same_error(selected.verify, first)
    assert raised.value is first
    local, evidence = failed(chosen, selected, first, monkeypatch)
    assert local["lease_state"] == "unknown" and local["terminal_verified"] is False
    assert evidence["passes_completed"]["-wal"] >= 2
    assert ledger.exit_attempts == ledger.exit_returns == ["-wal"]
