"""r854 selected actual owner-close facts and finite controlled consumer seams.

Root alone imports, collects and runs this candidate after separate admission.
TEMP return facts deliberately leave path-missing None. This block does not
test the future outer-EX receipt or authorize SQL installation/live writers.
"""
from collections import Counter
from contextlib import ExitStack
from dataclasses import FrozenInstanceError, fields
import ctypes
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
from reno import personal_authority_live_sql_deadline as live
from reno import personal_authority_live_sql_held_deadline as held
from reno import personal_authority_namespace3_deadline as deadline
from reno import personal_authority_sql_bootstrap_deadline as sql
from reno import personal_preservation_source as preservation
from reno import storage_lifecycle as lifecycle
from reno import storage_source as source


pytestmark = pytest.mark.skipif(os.name != "nt", reason="actual Windows close-chain owners")
SUFFIXES = ("", "-wal", "-journal")
ROW = "close-chain-绍兴"
IMAGE_FIELDS = {"phase", "temp_state", "private_pin_state", "sql_state", "byte_evidence",
                "bootstrap_evidence", "normal_exit", "source_directory_borrowed"}


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


def query(con, text, parameters=()):
    cursor = con.execute(text, parameters)
    try:
        return cursor.fetchall()
    finally:
        assert cursor.close() is None  # Extra caller/fixture cursor, not bootstrap's four slots.


def forbidden(*_args, **_kwargs):
    pytest.fail("cached receipt or retired owner reached a forbidden consumer")


def fixture_note(first):
    try:
        BaseException.add_note(first, "R854_FIXTURE_CLOSE_FAILED")
    except BaseException:
        pass


def same_error(call, first):
    with pytest.raises(type(first)) as caught:
        call()
    assert caught.value is first


def captured_error(call, cls, code=None):
    with pytest.raises(cls) as caught:
        call()
    if code is not None:
        assert caught.value.code == code
    return caught.value


def frozen(receipt):
    with pytest.raises(FrozenInstanceError):
        receipt.phase = "changed"
    assert not hasattr(receipt, "__dict__")
    assert not ({field.name for field in fields(receipt)} &
                {"path", "handle", "fd", "connection", "cursor", "callback", "exception",
                 "source_closed", "writable", "writer_authority"})


def common(receipt, kind, *, phase="retired", entered=True, exited=True, failed=False, normal=False):
    assert type(receipt.format_version) is int and receipt.format_version == 1
    assert receipt.kind == kind and receipt.scope == "native_owner_close_facts_only"
    assert receipt.phase == phase
    for key, expected in (("enter_returned", entered), ("exit_attempted", exited),
                          ("failed", failed), ("normal_exit", normal)):
        assert type(getattr(receipt, key)) is bool and getattr(receipt, key) is expected
    frozen(receipt)


def live_closed(receipt, *, normal=True, failed=False):
    assert type(receipt) is live.LiveSqlCloseReceiptV1
    common(receipt, "live_sql_file", normal=normal, failed=failed)
    assert receipt.directory_borrowed is True
    assert receipt.handle_state == receipt.transfer_state == "transferred"
    assert receipt.fd_state == receipt.raw_state == "closed"
    assert type(receipt.native_file_identity) is tuple and len(receipt.native_file_identity) == 3
    assert all(type(value) is int and 0 <= value < (1 << 32) for value in receipt.native_file_identity)


def temp_facts(receipt, *, state="closed", returned=True, legal=True, failed=False):
    assert type(receipt) is preservation.TempCleanupReceiptV1
    assert type(receipt.format_version) is int and receipt.format_version == 1
    assert receipt.kind == "private_temp" and receipt.scope == "owner_cleanup_facts_only"
    assert receipt.phase == "retired" and receipt.cleanup_state == state
    for key, value in (("created_returned", True), ("cleanup_attempted", True),
                       ("cleanup_returned", returned), ("cleanup_return_legal", legal), ("failed", failed)):
        assert type(getattr(receipt, key)) is bool and getattr(receipt, key) is value
    assert type(receipt.cached_directory_identity) is tuple and len(receipt.cached_directory_identity) == 2
    assert all(type(value) is int for value in receipt.cached_directory_identity)
    assert receipt.post_cleanup_path_missing is None  # No new absence probe was admitted.
    frozen(receipt)


class Scalar:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


@pytest.fixture
def actual_source(tmp_path):
    """Short private QA EX/WAL/empty journal; each case keeps the original30."""
    qa_temp()
    root = tmp_path / "p"
    profile = lifecycle.StorageProfile(root / "d/r.db", root / "m", root / "r", root / "c")
    os.makedirs(native(profile.database.parent))
    initial = sqlite3.connect(native(profile.database))
    try:
        cursor = initial.executescript("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT); "
                                       "INSERT INTO meta VALUES('migration_version','0');")
        assert cursor.close() is None
        initial.commit()
    finally:
        assert initial.close() is None
    lifecycle.initialize(profile)
    with lifecycle.exclusive(profile) as guard:
        rw, first = sqlite3.connect(native(profile.database)), None

        def close_source():
            nonlocal rw
            current, rw = rw, None
            if current is not None:
                assert current.close() is None

        try:
            assert query(rw, "PRAGMA journal_mode=WAL")[0][0].lower() == "wal"
            query(rw, "PRAGMA wal_autocheckpoint=0")
            query(rw, "CREATE TABLE wal_probe(value TEXT)")
            query(rw, "INSERT INTO wal_probe VALUES(?)", (ROW,))
            rw.commit()
            with open(native(profile.database.with_name(profile.database.name + "-journal")), "xb") as sink:
                assert sink.write(b"") == 0
            limited = deadline._RestrictedBudget(deadline.Namespace3Deadline(1.0e15))
            token = limited.effective_deadline  # Naturally clipped to the original30, never extended.
            with directories.DeadlineDirectory(profile.database.parent, base=profile.database.parent,
                    deadline_token=token) as directory:
                before = source.source_state(directory, profile.database.name)
                expected = {suffix: Path(native(profile.database.with_name(profile.database.name + suffix))).read_bytes()
                            for suffix in SUFFIXES}
                files = {}
                for suffix, signature in before.items():
                    device, inode, size, modified, changed = signature
                    assert len(expected[suffix]) == size
                    files[suffix] = {"device": str(device), "inode": str(inode), "byte_size": size,
                        "mtime_ns": str(modified), "ctime_ns": str(changed),
                        "sha256": hashlib.sha256(expected[suffix]).hexdigest()}
                fingerprint = {"format_version": 1, "profile_identity": list(profile.identity), "files": files}
                yielded_first = None
                try:
                    yield SimpleNamespace(profile=profile, guard=guard, directory=directory,
                        token=token, source_con=rw, expected=expected, fingerprint=fingerprint)
                    assert {suffix: Path(native(profile.database.with_name(profile.database.name + suffix))).read_bytes()
                            for suffix in SUFFIXES} == expected
                except BaseException as failure:
                    yielded_first = failure
                    raise
                finally:
                    try:
                        close_source()  # Subject observations/close precede fixture RW checkpoint/close.
                    except BaseException:
                        if yielded_first is None:
                            raise
                        fixture_note(yielded_first)
            limited.clock_only()
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
                    fixture_note(first)
    assert lifecycle.current_guard() is None


def live_lease(item, suffix=""):
    return live.source_reader(item.directory, item.profile.database.name + suffix, deadline_token=item.token)


def held_lease(item, suffix="", budget=None):
    selected_budget = budget if budget is not None else held.LiveSqlHeldBudget(
        item.profile, item.fingerprint, deadline_token=item.token)
    return held.held_file_locked(item.directory, suffix, read_budget=selected_budget, deadline_token=item.token)


def image_lease(item):
    return images.committed_image_locked(item.profile, item.directory, deadline_token=item.token, attempts=1)


def capture_bootstrap(patch, captured):
    original = images._sql_lease

    def prepare(*args, **kwargs):
        value = original(*args, **kwargs)
        assert type(value) is sql.SqlBootstrapDeadlineLease
        captured.append(value)
        return value

    patch.setattr(images, "_sql_lease", prepare)


def trap_live(patch):
    for name in ("_clock", "_monotonic", "_create_file", "_get_info", "_get_osfhandle",
                 "_open_osfhandle", "_fstat", "_retire_raw", "_close_fd", "_close_handle"):
        patch.setattr(live, name, forbidden)


def trap_sql(patch):
    for name in ("_clock", "_monotonic", "_connect", "_execute", "_fetchall", "_in_transaction",
                 "_cursor_close", "_con_close", "_owner_verify", "_entry_stat"):
        patch.setattr(sql, name, forbidden)


def test_actual_shared_live_file_and_three_held_receipts_keep_same_fd_five_scans(actual_source, monkeypatch):
    item = actual_source
    selected = live_lease(item)
    old = selected.close_receipt
    common(old, "live_sql_file", phase="new", entered=False, exited=False)
    calls, flags = Counter(), []
    real_create, real_fd, real_handle = live._create_file, live._close_fd, live._close_handle

    def create(*args):
        flags.append(args[2])
        return real_create(*args)

    def close_fd(value):
        calls["fd"] += 1
        return real_fd(value)

    def close_handle(value):
        calls["handle"] += 1
        return real_handle(value)

    monkeypatch.setattr(live, "_create_file", create)
    monkeypatch.setattr(live, "_close_fd", close_fd)
    monkeypatch.setattr(live, "_close_handle", close_handle)
    with selected as raw:
        assert type(raw) is io.FileIO and raw.closefd is False
        fd = raw.fileno()
        assert raw.read() == item.expected[""] and raw.read(1) == b"" and raw.fileno() == fd
        assert query(item.source_con, "SELECT value FROM wal_probe") == [(ROW,)]
        common(selected.close_receipt, "live_sql_file", phase="active", exited=False)
    live_closed(selected.close_receipt)
    selected_receipt = selected.close_receipt
    assert flags == [3] and calls == {"fd": 1} and old.phase == "new"
    chosen = held.LiveSqlHeldBudget(item.profile, item.fingerprint, deadline_token=item.token)
    real_fileno, actual_fds = held._raw_fileno, {}

    def fileno(raw):
        value = real_fileno(raw)
        actual_fds.setdefault(id(raw), set()).add(value)
        return value

    monkeypatch.setattr(held, "_raw_fileno", fileno)
    with ExitStack() as stack:
        holders = {suffix: stack.enter_context(held_lease(item, suffix, chosen)) for suffix in SUFFIXES}
        for owner in holders.values():
            common(owner.close_receipt, "live_sql_held", phase="active", exited=False)
            assert owner.verify() is None and owner.verify() is None
    assert chosen.evidence["passes_completed"] == {suffix: 5 for suffix in SUFFIXES}
    assert chosen.evidence["eof_reads"] == 15
    assert len(actual_fds) == 3 and all(len(values) == 1 for values in actual_fds.values())
    assert len(set().union(*actual_fds.values())) == 3
    receipts = {suffix: owner.close_receipt for suffix, owner in holders.items()}
    for receipt in receipts.values():
        assert type(receipt) is held.LiveSqlHeldCloseReceiptV1
        common(receipt, "live_sql_held", normal=True)
        assert receipt.source_directory_borrowed is True and receipt.terminal_verified is True
        live_closed(receipt.lower_close_receipt)
    assert calls == {"fd": 4} and flags == [3] * 4
    with monkeypatch.context() as patch:
        trap_live(patch)
        assert selected.close_receipt == selected_receipt
        patch.setattr(live.LiveSqlDeadlineLease, "close_receipt", property(forbidden))
        patch.setattr(held.LiveSqlHeldBudget, "_raise_first", forbidden)
        patch.setattr(held, "_clock", forbidden)
        patch.setattr(held, "_lease_exit", forbidden)
        assert {suffix: owner.close_receipt for suffix, owner in holders.items()} == receipts
    assert lifecycle.require_guard(item.profile, exclusive=True) is item.guard


@pytest.mark.parametrize("case", ["negative", "minimum", "maximum", "false", "zero",
                                 "bool", "float", "truthy", "above", "below"])
def test_controlled_live_handle_exact_return_domain_preserves_acquisition_first(actual_source, monkeypatch, case):
    selected = live_lease(actual_source)
    first = OSError("controlled info return then failure before CRT")
    real_info, real_close = live._get_info, live._close_handle
    attempts = []

    class TruthTrap:
        def __bool__(self):
            pytest.fail("CloseHandle result was truth-coerced")

    reported = {"negative": -1, "minimum": -(1 << 31), "maximum": (1 << 31) - 1,
                "false": False, "zero": 0, "bool": True, "float": 1.0,
                "truthy": TruthTrap(), "above": 1 << 31, "below": -(1 << 31) - 1}[case]

    def info(*args):
        assert real_info(*args) != 0
        raise first

    def close(handle):
        attempts.append(handle)
        actual = real_close(handle)
        assert type(actual) is int and -(1 << 31) <= actual < (1 << 31) and actual != 0
        ctypes.set_last_error(5)
        return reported

    monkeypatch.setattr(live, "_get_info", info)
    monkeypatch.setattr(live, "_close_handle", close)
    same_error(selected.__enter__, first)
    receipt = selected.close_receipt
    common(receipt, "live_sql_file", entered=False, failed=True)
    assert receipt.handle_state == ("closed" if case in ("negative", "minimum", "maximum") else "unknown")
    assert receipt.fd_state == receipt.raw_state == "empty" and receipt.transfer_state == "not_started"
    same_error(lambda: selected.__exit__(None, None, None), first)
    assert len(attempts) == 1 and selected.close_receipt == receipt


@pytest.mark.parametrize("api,reported", [("_retire_raw", False), ("_retire_raw", 0),
                                          ("_close_fd", False), ("_close_fd", 0)])
def test_controlled_live_non_none_retirement_is_unknown_and_never_retried(actual_source, monkeypatch, api, reported):
    selected = live_lease(actual_source)
    original, attempts = getattr(live, api), []

    def retire(value):
        attempts.append(value)
        assert original(value) is None
        return reported

    monkeypatch.setattr(live, api, retire)
    first = captured_error(lambda: run_live(selected), live.LiveSqlDeadlineError, "LIVE_SQL_DEADLINE_INVALID_RESULT")
    receipt = selected.close_receipt
    common(receipt, "live_sql_file", failed=True)
    assert receipt.handle_state == "transferred"
    assert receipt.raw_state == ("unknown" if api == "_retire_raw" else "closed")
    assert receipt.fd_state == ("unknown" if api == "_close_fd" else "closed")
    same_error(lambda: selected.__exit__(None, None, None), first)
    assert len(attempts) == 1 and selected.close_receipt == receipt


def run_live(selected, primary=None):
    with selected:
        if primary is not None:
            raise primary


def test_controlled_live_foreign_closed_raw_never_becomes_closed_or_suppresses_fd_owner(actual_source, monkeypatch):
    selected, calls = live_lease(actual_source), Counter()
    original = live._close_fd

    class ForeignClosed:
        closefd = False

        @property
        def closed(self):
            calls["foreign_closed"] += 1
            return True

        def close(self):
            calls["foreign_close"] += 1
            pytest.fail("foreign raw is not an actual owned FileIO")

    def close_fd(fd):
        calls["fd"] += 1
        return original(fd)

    monkeypatch.setattr(live, "_file_io", lambda *_args, **_kwargs: ForeignClosed())
    monkeypatch.setattr(live, "_close_fd", close_fd)
    first = captured_error(selected.__enter__, live.LiveSqlDeadlineError, "LIVE_SQL_DEADLINE_INVALID_ARGUMENT")
    receipt = selected.close_receipt
    common(receipt, "live_sql_file", entered=False, failed=True)
    assert receipt.raw_state == "unknown" and receipt.fd_state == "closed"
    assert receipt.handle_state == receipt.transfer_state == "transferred"
    same_error(lambda: selected.__exit__(None, None, None), first)
    assert calls == {"fd": 1} and selected.close_receipt == receipt


@pytest.mark.parametrize("after", [False, True], ids=["before-crt", "after-crt"])
def test_controlled_live_unknown_transfer_uses_only_independent_known_fixture_recovery(actual_source, monkeypatch, after):
    selected = live_lease(actual_source)
    first, witness, attempts = OSError("controlled opaque CRT call"), {}, []
    real_create, real_transfer = live._create_file, live._open_osfhandle
    real_handle, real_fd = live._close_handle, live._close_fd

    def create(*args):
        witness["handle"] = real_create(*args)
        return witness["handle"]

    def transfer(handle, flags):
        if after:
            witness["fd"] = real_transfer(handle, flags)
        raise first

    def close(value):
        attempts.append(value)
        pytest.fail("subject attempted unknown numeric transfer ownership")

    monkeypatch.setattr(live, "_create_file", create)
    monkeypatch.setattr(live, "_open_osfhandle", transfer)
    monkeypatch.setattr(live, "_close_handle", close)
    monkeypatch.setattr(live, "_close_fd", close)
    try:
        same_error(selected.__enter__, first)
        receipt = selected.close_receipt
        common(receipt, "live_sql_file", entered=False, failed=True)
        assert receipt.handle_state == receipt.transfer_state == "unknown"
        assert receipt.fd_state == receipt.raw_state == "empty"
        same_error(lambda: selected.__exit__(None, None, None), first)
        assert attempts == [] and selected.close_receipt == receipt
    finally:
        # Actual seam inputs/returns select this independent sole fixture closer.
        # Subject never attempted numeric close; no probe and no receipt upgrade.
        if "fd" in witness:
            assert real_fd(witness["fd"]) is None
        elif "handle" in witness:
            actual = real_handle(witness["handle"])
            assert type(actual) is int and -(1 << 31) <= actual < (1 << 31) and actual != 0


@pytest.mark.parametrize("primary", [RuntimeError("body"), SystemExit(854), MemoryError("synthetic")],
                         ids=["ordinary", "control", "synthetic-memory"])
def test_controlled_live_body_first_survives_completed_fd_close_alias_fault(actual_source, monkeypatch, primary):
    selected = live_lease(actual_source)
    original, attempts = live._close_fd, []

    def close(fd):
        attempts.append(fd)
        assert original(fd) is None
        raise OSError("controlled close postreturn")

    monkeypatch.setattr(live, "_close_fd", close)
    same_error(lambda: run_live(selected, primary), primary)
    receipt = selected.close_receipt
    common(receipt, "live_sql_file", failed=True)
    assert receipt.raw_state == "closed" and receipt.fd_state == "unknown"
    same_error(lambda: selected.__exit__(None, None, None), primary)
    assert len(attempts) == 1 and selected.close_receipt == receipt


@pytest.mark.parametrize("stage", ["enter", "last-close"])
def test_controlled_live_postreturn_deadline_cannot_upgrade_empty_exit(actual_source, monkeypatch, stage):
    item = actual_source
    selected, calls = live_lease(item), Counter()
    scalar = Scalar(live._monotonic())
    name = "_create_file" if stage == "enter" else "_close_fd"
    original = getattr(live, name)

    def returned(*args):
        value = original(*args)
        calls[name] += 1
        scalar.value = item.token.absolute_monotonic
        return value

    monkeypatch.setattr(live, "_monotonic", scalar)
    monkeypatch.setattr(live, name, returned)
    first = captured_error(selected.__enter__ if stage == "enter" else lambda: run_live(selected),
                           live.LiveSqlDeadlineError, "LIVE_SQL_DEADLINE_DEADLINE")
    receipt = selected.close_receipt
    common(receipt, "live_sql_file", entered=stage != "enter", failed=True)
    if stage == "enter":
        assert receipt.handle_state == "closed" and receipt.fd_state == receipt.raw_state == "empty"
    else:
        live_closed(receipt, normal=False, failed=True)
    same_error(lambda: selected.__exit__(None, None, None), first)
    assert calls == {name: 1} and selected.close_receipt == receipt


@pytest.mark.parametrize("fault", ["false", "post-return"])
def test_controlled_held_alias_failure_retains_physical_lower_normal_receipt(actual_source, monkeypatch, fault):
    selected, original = held_lease(actual_source), held._lease_exit
    first, attempts = OSError("held close alias postreturn"), []

    def exit_lower(*args):
        attempts.append(args[0])
        assert original(*args) is None
        if fault == "post-return":
            raise first
        return False

    monkeypatch.setattr(held, "_lease_exit", exit_lower)
    if fault == "false":
        first = captured_error(lambda: run_held(selected), held.LiveSqlHeldDeadlineError,
                               "LIVE_SQL_HELD_DEADLINE_INVALID_RESULT")
    else:
        same_error(lambda: run_held(selected), first)
    receipt = selected.close_receipt
    common(receipt, "live_sql_held", failed=True)
    assert receipt.terminal_verified is True and selected.evidence["lease_state"] == "unknown"
    live_closed(receipt.lower_close_receipt)
    same_error(lambda: selected.__exit__(None, None, None), first)
    assert len(attempts) == 1 and selected.close_receipt == receipt


def run_held(selected, primary=None):
    with selected:
        assert selected.verify() is None and selected.verify() is None
        if primary is not None:
            raise primary


def test_controlled_held_failed_lower_enter_caches_self_cleanup_without_second_exit(actual_source, monkeypatch):
    selected = held_lease(actual_source)
    original_info, original_exit = live._get_info, held._lease_exit
    first, calls = OSError("lower opening info postreturn"), Counter()

    def info(*args):
        assert original_info(*args) != 0
        raise first

    def exit_lower(*args):
        calls["exit"] += 1
        return original_exit(*args)

    monkeypatch.setattr(live, "_get_info", info)
    monkeypatch.setattr(held, "_lease_exit", exit_lower)
    same_error(selected.__enter__, first)
    receipt = selected.close_receipt
    common(receipt, "live_sql_held", entered=False, exited=False, failed=True)
    common(receipt.lower_close_receipt, "live_sql_file", entered=False, failed=True)
    assert receipt.lower_close_receipt.handle_state == "closed"
    same_error(lambda: selected.__exit__(None, None, None), first)
    assert calls == {} and selected.close_receipt == receipt


def test_controlled_held_cache_postreturn_expiry_blocks_enter_delivery(actual_source, monkeypatch):
    item, selected = actual_source, held_lease(actual_source)
    original = live.LiveSqlDeadlineLease.close_receipt.fget
    scalar, reads = Scalar(held._monotonic()), []

    def receipt(lower):
        value = original(lower)
        reads.append(value.phase)
        scalar.value = item.token.absolute_monotonic
        return value

    monkeypatch.setattr(held, "_monotonic", scalar)
    monkeypatch.setattr(live.LiveSqlDeadlineLease, "close_receipt", property(receipt))
    first = captured_error(selected.__enter__, held.LiveSqlHeldDeadlineError, "LIVE_SQL_HELD_DEADLINE_DEADLINE")
    cached = selected.close_receipt
    common(cached, "live_sql_held", entered=False, failed=True)
    live_closed(cached.lower_close_receipt, normal=False, failed=True)
    before = list(reads)
    same_error(lambda: selected.__exit__(None, None, None), first)
    assert reads == before and selected.close_receipt == cached


@pytest.mark.parametrize("primary", [RuntimeError("body"), SystemExit(854), MemoryError("synthetic")],
                         ids=["ordinary", "control", "synthetic-memory"])
def test_controlled_held_lower_receipt_failure_preserves_body_first(actual_source, monkeypatch, primary):
    selected = held_lease(actual_source)

    def run():
        with selected:
            monkeypatch.setattr(live.LiveSqlDeadlineLease, "close_receipt",
                                property(lambda _owner: (_ for _ in ()).throw(OSError("cache fault"))))
            raise primary

    same_error(run, primary)
    receipt = selected.close_receipt
    common(receipt, "live_sql_held", failed=True)
    assert receipt.lower_close_receipt is None
    same_error(lambda: selected.__exit__(None, None, None), primary)
    assert selected.close_receipt == receipt


def test_controlled_held_retired_own_snapshot_does_not_read_later_shared_budget_first(actual_source):
    item = actual_source
    chosen = held.LiveSqlHeldBudget(item.profile, item.fingerprint, deadline_token=item.token)
    first_owner = held_lease(item, "", chosen)
    run_held(first_owner)
    original = first_owner.close_receipt
    common(original, "live_sql_held", normal=True)
    later = held_lease(item, "-wal", chosen)
    first = RuntimeError("separate later holder body")
    same_error(lambda: run_held(later, first), first)
    assert chosen.evidence["failed"] is True
    assert first_owner.close_receipt == original
    live_closed(first_owner.close_receipt.lower_close_receipt)


def test_actual_image_sql_and_temp_cached_owner_receipts_preserve_old_evidence(actual_source, monkeypatch):
    item, captured = actual_source, []
    capture_bootstrap(monkeypatch, captured)
    selected = image_lease(item)
    assert selected.sql_close_receipt is None and selected.temp_cleanup_receipt is None
    with selected as image:
        assert image.source_fingerprint == item.fingerprint
        assert query(image.connection, "SELECT value FROM wal_probe") == [(ROW,)]
        active = selected.sql_close_receipt
        common(active, "sql_bootstrap", phase="active", exited=False)
        assert active.connection_state == "owned" and active.cursor_states == ("closed",) * 4
        assert active.bootstrap_complete is active.limits_restored is True
    sql_receipt, temp_receipt, evidence = selected.sql_close_receipt, selected.temp_cleanup_receipt, selected.evidence
    assert active.phase == "active" and active.connection_state == "owned"
    assert type(sql_receipt) is sql.SqlBootstrapCloseReceiptV1
    common(sql_receipt, "sql_bootstrap", normal=True)
    assert sql_receipt.connection_state == "closed" and sql_receipt.cursor_states == ("closed",) * 4
    assert type(sql_receipt.exit_in_transaction) is bool
    temp_facts(temp_receipt)
    assert len(captured) == 1 and captured[0].close_receipt == sql_receipt
    assert set(evidence) == IMAGE_FIELDS and evidence["normal_exit"] is True
    with monkeypatch.context() as patch:
        trap_sql(patch)
        assert captured[0].close_receipt == sql_receipt
        patch.setattr(sql.SqlBootstrapDeadlineLease, "close_receipt", property(forbidden))
        patch.setattr(preservation._PersonalPrivateDirectory, "cleanup_receipt", property(forbidden))
        for name in ("_clock", "_monotonic", "_sql_enter", "_sql_exit", "_temp_cleanup", "_private_directory"):
            patch.setattr(images, name, forbidden)
        assert selected.sql_close_receipt == sql_receipt and selected.temp_cleanup_receipt == temp_receipt
        assert selected.evidence == evidence


@pytest.mark.parametrize("api,reported", [("_cursor_close", False), ("_cursor_close", 0),
                                          ("_con_close", False), ("_con_close", 0)])
def test_controlled_bootstrap_non_none_close_is_unknown_no_retry_and_cached_on_image(actual_source, monkeypatch, api, reported):
    captured, attempts = [], []
    capture_bootstrap(monkeypatch, captured)
    selected, original = image_lease(actual_source), getattr(sql, api)

    def close(value):
        attempts.append(value)
        assert original(value) is None
        return reported

    monkeypatch.setattr(sql, api, close)
    first = captured_error(lambda: run_image(selected), sql.SqlBootstrapDeadlineError,
                           "SQL_BOOTSTRAP_DEADLINE_INVALID_RESULT")
    receipt = selected.sql_close_receipt
    common(receipt, "sql_bootstrap", entered=api == "_con_close", failed=True)
    assert receipt.connection_state == ("unknown" if api == "_con_close" else "closed")
    assert receipt.cursor_states == (("closed",) * 4 if api == "_con_close" else ("unknown", "empty", "empty", "empty"))
    assert len(captured) == 1 and captured[0].close_receipt == receipt
    same_error(lambda: selected.__exit__(None, None, None), first)
    assert len(attempts) == 1 and selected.sql_close_receipt == receipt


def run_image(selected, primary=None):
    with selected:
        if primary is not None:
            raise primary


@pytest.mark.parametrize("api", ["_cursor_close", "_con_close"])
def test_controlled_bootstrap_close_postreturn_expiry_retains_closed_facts(actual_source, monkeypatch, api):
    item, captured = actual_source, []
    capture_bootstrap(monkeypatch, captured)
    selected, original = image_lease(item), getattr(sql, api)
    scalar, attempts = Scalar(sql._monotonic()), []

    def close(value):
        attempts.append(value)
        actual = original(value)
        scalar.value = item.token.absolute_monotonic
        return actual

    monkeypatch.setattr(sql, "_monotonic", scalar)
    monkeypatch.setattr(sql, api, close)
    first = captured_error(lambda: run_image(selected), sql.SqlBootstrapDeadlineError,
                           "SQL_BOOTSTRAP_DEADLINE_DEADLINE")
    receipt = selected.sql_close_receipt
    common(receipt, "sql_bootstrap", entered=api == "_con_close", failed=True)
    assert receipt.connection_state == "closed"
    assert receipt.cursor_states == (("closed",) * 4 if api == "_con_close" else ("closed", "empty", "empty", "empty"))
    same_error(lambda: selected.__exit__(None, None, None), first)
    assert len(attempts) == 1 and selected.sql_close_receipt == receipt


@pytest.mark.parametrize("primary", [RuntimeError("body"), SystemExit(854), MemoryError("synthetic")],
                         ids=["ordinary", "control", "synthetic-memory"])
def test_controlled_bootstrap_body_first_survives_completed_connection_alias_throw(actual_source, monkeypatch, primary):
    selected, captured = image_lease(actual_source), []
    capture_bootstrap(monkeypatch, captured)
    original, attempts = sql._con_close, []

    def close(value):
        attempts.append(value)
        assert original(value) is None
        raise OSError("connection postreturn close fault")

    monkeypatch.setattr(sql, "_con_close", close)
    same_error(lambda: run_image(selected, primary), primary)
    receipt = selected.sql_close_receipt
    common(receipt, "sql_bootstrap", failed=True)
    assert receipt.connection_state == "unknown" and receipt.cursor_states == ("closed",) * 4
    same_error(lambda: selected.__exit__(None, None, None), primary)
    assert len(attempts) == 1 and selected.sql_close_receipt == receipt


def test_actual_temp_cleanup_once_has_cached_return_facts_without_absence_probe(monkeypatch):
    qa_temp()
    owner = preservation._PersonalPrivateDirectory()
    initial = owner.cleanup_receipt
    assert initial.created_returned is True and initial.cleanup_attempted is False
    assert initial.post_cleanup_path_missing is None
    original, calls = preservation._PersonalPrivateDirectory._actual_cleanup, []

    def cleanup(temporary):
        calls.append(temporary)
        return original(temporary)

    monkeypatch.setattr(preservation._PersonalPrivateDirectory, "_actual_cleanup", staticmethod(cleanup))
    assert owner.cleanup() is None
    receipt = owner.cleanup_receipt
    temp_facts(receipt)
    assert owner.cleanup() is None and len(calls) == 1 and initial.cleanup_attempted is False
    with monkeypatch.context() as patch:
        patch.setattr(preservation._PersonalPrivateDirectory, "_verify", forbidden)
        patch.setattr(preservation._PersonalPrivateDirectory, "_actual_cleanup", staticmethod(forbidden))
        assert owner.cleanup_receipt == receipt
        assert owner.cleanup() is None


@pytest.mark.parametrize("mode", ["false", "zero", "before", "after"])
def test_controlled_temp_cleanup_unknown_is_sticky_and_does_not_probe_or_retry(monkeypatch, mode):
    qa_temp()
    owner = preservation._PersonalPrivateDirectory()
    known_temporary = owner.temporary  # Exact constructor-returned fixture witness.
    original, attempts = preservation._PersonalPrivateDirectory._actual_cleanup, []
    first = OSError("TEMP controlled " + mode)

    def cleanup(temporary):
        assert temporary is known_temporary
        attempts.append(temporary)
        if mode == "before":
            raise first  # No actual underlying cleanup call occurred.
        assert original(temporary) is None
        if mode == "after":
            raise first
        return False if mode == "false" else 0

    monkeypatch.setattr(preservation._PersonalPrivateDirectory, "_actual_cleanup", staticmethod(cleanup))
    try:
        if mode in ("false", "zero"):
            first = captured_error(owner.cleanup, preservation.contract.PersonalPreservationError,
                                   "PERSONAL_PRESERVATION_PRIVATE_IMAGE_CLEANUP_INVALID_RESULT")
        else:
            same_error(owner.cleanup, first)
        receipt = owner.cleanup_receipt
        temp_facts(receipt, state="unknown", returned=mode in ("false", "zero"), legal=False, failed=True)
        with monkeypatch.context() as patch:
            patch.setattr(preservation._PersonalPrivateDirectory, "_verify", forbidden)
            same_error(owner.cleanup, first)
            assert owner.cleanup_receipt == receipt
        assert len(attempts) == 1
    finally:
        if mode == "before":
            # Independent fixture's first actual underlying cleanup, solely from
            # the constructor witness and explicit unforwarded seam. No probe,
            # subject cleanup retry, unknown repair or receipt upgrade.
            assert original(known_temporary) is None


@pytest.mark.parametrize("role", ["sql", "temp"])
def test_controlled_image_owner_exit_postthrow_keeps_lower_close_receipt_and_old_states(actual_source, monkeypatch, role):
    selected, first = image_lease(actual_source), OSError("image lower exit postreturn " + role)
    api = "_sql_exit" if role == "sql" else "_temp_cleanup"
    original, attempts = getattr(images, api), []

    def close(*args):
        attempts.append(args[0])
        assert original(*args) is None
        raise first

    monkeypatch.setattr(images, api, close)
    same_error(lambda: run_image(selected), first)
    evidence = selected.evidence
    assert set(evidence) == IMAGE_FIELDS and evidence["normal_exit"] is False
    if role == "sql":
        common(selected.sql_close_receipt, "sql_bootstrap", normal=True)
        # Original SQL aggregate reads actual bootstrap evidence even when the
        # image alias throws after ordinary lower exit; keep that old CLOSED.
        assert selected.sql_close_receipt.connection_state == "closed" and evidence["sql_state"] == "closed"
    else:
        temp_facts(selected.temp_cleanup_receipt)
        assert evidence["temp_state"] == "unknown"
    same_error(lambda: selected.__exit__(None, None, None), first)
    assert len(attempts) == 1


@pytest.mark.parametrize("role", ["sql", "temp"])
def test_controlled_image_cache_postreturn_deadline_blocks_enter_delivery(actual_source, monkeypatch, role):
    item, selected = actual_source, image_lease(actual_source)
    scalar = Scalar(images._monotonic())
    owner_type = sql.SqlBootstrapDeadlineLease if role == "sql" else preservation._PersonalPrivateDirectory
    property_name = "close_receipt" if role == "sql" else "cleanup_receipt"
    original = getattr(owner_type, property_name).fget
    reads = []

    def receipt(owner):
        value = original(owner)
        reads.append(value.phase)
        if value.phase == "active":
            scalar.value = item.token.absolute_monotonic
        return value

    monkeypatch.setattr(images, "_monotonic", scalar)
    monkeypatch.setattr(owner_type, property_name, property(receipt))
    first = captured_error(selected.__enter__, images.CommittedImageDeadlineError,
                           "COMMITTED_IMAGE_DEADLINE_DEADLINE")
    evidence = selected.evidence
    assert evidence["phase"] == "retired" and evidence["normal_exit"] is False
    temp_facts(selected.temp_cleanup_receipt)
    if role == "sql":
        assert selected.sql_close_receipt.connection_state == "closed"
        common(selected.sql_close_receipt, "sql_bootstrap", failed=True)
        assert {"new", "active", "retired"} <= set(reads)
        assert reads.index("new") < reads.index("active") < reads.index("retired")
    before = list(reads)
    same_error(lambda: selected.__exit__(None, None, None), first)
    assert reads == before


def test_controlled_image_failed_bootstrap_enter_caches_self_cleanup_without_second_sql_exit(actual_source, monkeypatch):
    selected, captured = image_lease(actual_source), []
    capture_bootstrap(monkeypatch, captured)
    real_fetch, real_exit = sql._fetchall, images._sql_exit
    first, calls = OSError("bootstrap fetch postreturn"), Counter()

    def fetch(cursor):
        value = real_fetch(cursor)
        assert type(value) is list
        raise first

    def exit_sql(*args):
        calls["exit"] += 1
        return real_exit(*args)

    monkeypatch.setattr(sql, "_fetchall", fetch)
    monkeypatch.setattr(images, "_sql_exit", exit_sql)
    same_error(selected.__enter__, first)
    receipt = selected.sql_close_receipt
    common(receipt, "sql_bootstrap", entered=False, failed=True)
    assert receipt.connection_state == "closed" and receipt.cursor_states == ("closed",) * 4
    temp_facts(selected.temp_cleanup_receipt)
    same_error(lambda: selected.__exit__(None, None, None), first)
    assert calls == {} and selected.sql_close_receipt == receipt and len(captured) == 1


@pytest.mark.parametrize("role", ["sql", "temp"])
def test_controlled_image_lower_getter_failure_cannot_replace_body_primary(actual_source, monkeypatch, role):
    selected, first = image_lease(actual_source), SystemExit(854)
    owner_type = sql.SqlBootstrapDeadlineLease if role == "sql" else preservation._PersonalPrivateDirectory
    property_name = "close_receipt" if role == "sql" else "cleanup_receipt"

    def run():
        with selected:
            monkeypatch.setattr(owner_type, property_name,
                                property(lambda _owner: (_ for _ in ()).throw(OSError("getter secondary"))))
            raise first

    same_error(run, first)
    assert selected.evidence["normal_exit"] is False
    assert (selected.sql_close_receipt if role == "sql" else selected.temp_cleanup_receipt) is None
    same_error(lambda: selected.__exit__(None, None, None), first)
