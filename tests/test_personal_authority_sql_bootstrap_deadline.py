"""r827 real private SQL bootstrap and finite new-alias failure protocols.

Q01-Q30 are logical design labels, not pytest counts. Root alone imports,
collects and runs this candidate. Only the new module's declared aliases are
patched; extra raw-Connection fixture/receiver APIs are outside bootstrap gates.
"""
import ast
from collections import Counter
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace

import pytest

from reno import personal_authority_directory_deadline as directories
from reno import personal_authority_image_bytes_deadline as image_bytes
from reno import personal_authority_namespace3_deadline as deadline
from reno import personal_authority_sql_bootstrap_deadline as subject
from reno import personal_mirror_transfer_schema as receiver
from reno import personal_preservation_source as preservation
from reno import storage_readonly as readonly
from reno import storage_source as source


pytestmark = pytest.mark.skipif(os.name != "nt", reason="actual Windows private image/bootstrap owners")
NAME = "绍兴-bootstrap.sqlite3"
PLAIN = b"finite SQLite source fixture"
FIELDS = {"phase", "connection_state", "cursor_states", "bootstrap_complete",
          "normal_exit", "previous_limits", "limits_restored", "exit_in_transaction", "retired"}
SQL = ("PRAGMA query_only=ON", "PRAGMA trusted_schema=OFF", "BEGIN",
       "SELECT rootpage FROM sqlite_schema LIMIT 1")
LENGTH, SQL_LENGTH = sqlite3.SQLITE_LIMIT_LENGTH, sqlite3.SQLITE_LIMIT_SQL_LENGTH


def native(path):
    text = os.path.abspath(os.fspath(path))
    if text.startswith("\\\\?\\"):
        return text
    return "\\\\?\\UNC\\" + text[2:] if text.startswith("\\\\") else "\\\\?\\" + text


def note(first):
    try:
        BaseException.add_note(first, "R827_FIXTURE_CLEANUP_FAILED")
    except BaseException:
        pass


def qa_temp():
    qa = Path(os.environ["RENO_QA_ROOT"]).resolve(strict=True)
    selected = Path(tempfile.gettempdir()).resolve(strict=True)
    selected.relative_to(qa)
    assert selected.is_dir()


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
    def opened(*, wal=False, plain=None, facility=False, stage="complete"):
        nonlocal sequence
        sequence += 1
        root = tmp_path / ("浙江绍兴-bootstrap-" + str(sequence))
        root.mkdir()
        connection, first = None, None

        def close_connection():
            nonlocal connection
            actual, connection = connection, None
            if actual is not None:
                actual.close()

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
                if facility:
                    # Only this fixture's RW source; no profile/install/real DB.
                    receiver._install_reference(connection)
                else:
                    connection.execute("CREATE TABLE samples(id INTEGER PRIMARY KEY, payload BLOB)")
                    connection.execute("INSERT INTO samples(payload) VALUES (?)", (PLAIN,))
                connection.commit()
            before = state(root)
            expected = {suffix: Path(native(root / (NAME + suffix))).read_bytes()
                        for suffix in ("", "-wal") if before[suffix] is not None}
            effective = deadline._RestrictedBudget(deadline.Namespace3Deadline(1.0e15)).effective_deadline
            with directories.DeadlineDirectory(root, base=root, deadline_token=effective) as directory:
                qa_temp()  # Before the actual legacy TEMP constructor.
                legacy = preservation._PersonalPrivateDirectory()
                owner_first = None
                try:
                    with directories.DeadlineDirectory(legacy.path, base=legacy.path,
                            deadline_token=effective) as private:
                        owner = image_bytes.ImagePrivateOwner(legacy, private, deadline_token=effective)
                        budget = image_bytes.ImageByteBudget(NAME, before, deadline_token=effective)
                        if stage == "failed":
                            with open(native(owner.path / "image.sqlite"), "xb") as sink:
                                assert sink.write(b"existing") == 8
                            with pytest.raises(FileExistsError):
                                image_bytes.copy_file_locked(directory, owner, "", byte_budget=budget)
                        elif stage != "new":
                            for suffix in ("", "-wal"):
                                if before[suffix] is None:
                                    continue
                                assert image_bytes.copy_file_locked(directory, owner, suffix, byte_budget=budget) is None
                                if stage == "complete":
                                    assert image_bytes.private_sha_locked(owner, suffix, byte_budget=budget) == hashlib.sha256(expected[suffix]).hexdigest()
                        yield SimpleNamespace(root=root, source_con=connection, before=before,
                            expected=expected, token=effective, directory=directory, private=private,
                            legacy=legacy, owner=owner, budget=budget)
                except BaseException as failure:
                    owner_first = failure
                    raise
                finally:
                    # SQL sidecar is nested inside yield and must already exit;
                    # private pin -> RW source connection -> TEMP -> source pin.
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


def lease(item, **kwargs):
    return subject.private_connection(item.owner, byte_budget=item.budget, **kwargs)


def error(call, suffix):
    with pytest.raises(subject.SqlBootstrapDeadlineError) as raised:
        call()
    assert raised.value.code == "SQL_BOOTSTRAP_DEADLINE_" + suffix
    return raised.value


def same_error(call, first):
    with pytest.raises(type(first)) as raised:
        call()
    assert raised.value is first


def no_action(*_args, **_kwargs):
    pytest.fail("refused SQL lease invoked a forbidden new consumer")


def failed(selected, first, monkeypatch):
    for name in ("_monotonic", "_connect", "_execute", "_owner_verify", "_entry_stat", "_in_transaction"):
        monkeypatch.setattr(subject, name, no_action)
    evidence = selected.evidence
    assert set(evidence) == FIELDS and evidence["retired"] is True and evidence["normal_exit"] is False
    assert type(evidence["cursor_states"]) is tuple and len(evidence["cursor_states"]) == 4
    assert type(evidence["previous_limits"]) is tuple and len(evidence["previous_limits"]) == 2
    assert all(value is None or type(value) is int and value >= 0 for value in evidence["previous_limits"])
    same_error(selected.__enter__, first)
    same_error(lambda: selected.__exit__(None, None, None), first)
    changed = selected.evidence
    changed["normal_exit"] = True
    changed["connection_state"] = "owned"
    assert selected.evidence == evidence
    return evidence


class Scalar:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


class Ledger:
    """Declared new alias resources, returns and attempts, not private slots."""
    def __init__(self, monkeypatch):
        names = ("_connect", "_setlimit", "_set_progress_handler", "_set_authorizer",
                 "_execute", "_fetchall", "_cursor_close", "_con_close",
                 "_cursor_connection", "_in_transaction", "_owner_verify", "_entry_stat")
        self.original = {name: getattr(subject, name) for name in names}
        self.calls, self.events = Counter(), []
        self.cons, self.cursors, self.limits, self.initial_limits = [], [], [], []
        self.cur_attempts, self.cur_returns, self.con_attempts, self.con_returns = [], [], [], []

        def connected(database, **kwargs):
            self.calls["connect"] += 1
            assert kwargs == {"uri": True} and database.endswith("?mode=ro") and "immutable" not in database
            value = self.original["_connect"](database, **kwargs)
            assert type(value) is sqlite3.Connection
            self.cons.append(value)
            self.initial_limits.append((value.getlimit(LENGTH), value.getlimit(SQL_LENGTH)))  # Extra test APIs.
            self.events.append(("connect", database))
            return value

        def limit(con, category, value):
            self.calls["setlimit"] += 1
            actual = self.original["_setlimit"](con, category, value)
            self.limits.append((category, value, actual))
            self.events.append(("setlimit", category, value))
            return actual

        def progress(con, callback, interval):
            self.calls["progress"] += 1
            assert callback is None and interval == 0 or callable(callback) and interval == 1000
            actual = self.original["_set_progress_handler"](con, callback, interval)
            self.events.append(("progress", "clear" if callback is None else "install", interval))
            return actual

        def authorizer(con, callback):
            self.calls["authorizer"] += 1
            assert callback is readonly._readonly_authorizer
            actual = self.original["_set_authorizer"](con, callback)
            self.events.append(("authorizer",))
            return actual

        def execute(con, sql):
            self.calls["execute"] += 1
            actual = self.original["_execute"](con, sql)
            assert type(actual) is sqlite3.Cursor
            self.cursors.append(actual)
            self.events.append(("execute", sql))
            return actual

        def fetch(cursor):
            self.calls["fetchall"] += 1
            value = self.original["_fetchall"](cursor)
            self.events.append(("fetchall",))
            return value

        def curclose(cursor):
            self.calls["cursor-close"] += 1
            self.cur_attempts.append(cursor)
            index = next(i for i, actual in enumerate(self.cursors) if actual is cursor)
            self.events.append(("cursor-attempt", index))
            value = self.original["_cursor_close"](cursor)
            self.cur_returns.append(cursor)
            self.events.append(("cursor-return", index))
            return value

        def conclose(con):
            self.calls["con-close"] += 1
            self.con_attempts.append(con)
            self.events.append(("con-attempt",))
            value = self.original["_con_close"](con)
            self.con_returns.append(con)
            self.events.append(("con-return",))
            return value

        def relation(cursor):
            self.calls["relation"] += 1
            value = self.original["_cursor_connection"](cursor)
            self.events.append(("cursor-connection",))
            return value

        def transaction(con):
            self.calls["transaction"] += 1
            value = self.original["_in_transaction"](con)
            self.events.append(("in-transaction", value))
            return value

        def verify(owner):
            self.calls["verify"] += 1
            return self.original["_owner_verify"](owner)

        def stat(directory, name):
            self.calls["stat"] += 1
            assert name in ("image.sqlite", "image.sqlite-wal", "image.sqlite-journal", "image.sqlite-shm")
            return self.original["_entry_stat"](directory, name)

        actions = (connected, limit, progress, authorizer, execute, fetch, curclose, conclose, relation, transaction, verify, stat)
        for name, action in zip(names, actions):
            monkeypatch.setattr(subject, name, action)


def normal_closed(ledger):
    assert ledger.con_attempts == ledger.cons == ledger.con_returns and len(ledger.cons) == 1
    assert ledger.cur_attempts == ledger.cursors == ledger.cur_returns
    # Actual close returned, with no opaque close fault in callers of this helper.
    with pytest.raises(sqlite3.ProgrammingError):
        ledger.cons[0].execute("SELECT 1")
    for cursor in ledger.cursors:
        with pytest.raises(sqlite3.ProgrammingError):
            cursor.fetchall()


# Q01 / Q03: exact domain and NEW factory without a connect/global claim.
def test_exact_binding_factory_laziness_and_each_lease_single_enter(env, monkeypatch):
    with env() as item:
        ledger = Ledger(monkeypatch)
        selected, unused = lease(item), lease(item)
        assert type(selected) is subject.SqlBootstrapDeadlineLease and selected is not unused
        assert ledger.calls["connect"] == ledger.calls["stat"] == 0
        assert selected.evidence["phase"] == "new" and unused.evidence["retired"] is False
        error(lambda: subject.private_connection(object(), byte_budget=item.budget), "INVALID_ARGUMENT")
        error(lambda: subject.private_connection(item.owner, byte_budget=object()), "INVALID_ARGUMENT")
        for option in ("path", "deadline_token", "factory", "connection", "consumer"):
            with pytest.raises(TypeError):
                lease(item, **{option: object()})
        with pytest.raises(subject.SqlBootstrapDeadlineError):
            type("LeaseChild", (subject.SqlBootstrapDeadlineLease,), {})
        with selected as con:
            assert type(con) is sqlite3.Connection and con.in_transaction is True
        normal_closed(ledger)
        completed = selected.evidence
        assert completed["normal_exit"] is True and completed["retired"] is True
        monkeypatch.setattr(subject, "_monotonic", no_action)
        error(selected.__enter__, "CLOSED")
        error(lambda: selected.__exit__(None, None, None), "CLOSED")
        assert selected.evidence == completed and unused.evidence["phase"] == "new"


@pytest.mark.parametrize("stage", ["new", "copy", "failed"])
def test_incomplete_failed_and_detached_fake_complete_budget_cannot_connect(env, monkeypatch, stage):
    with env(stage=stage) as item:
        ledger = Ledger(monkeypatch)
        original = item.budget.evidence
        fake = item.budget.evidence
        fake["complete"], fake["retired"] = True, True
        fake["phases"] = {"": "hash_complete", "-wal": "absent"}
        error(lambda: lease(item), "INVALID_ARGUMENT")
        assert item.budget.evidence == original and ledger.calls["connect"] == 0


# Q01/Q04: two exact owners at the same real path cannot migrate binding.
@pytest.mark.parametrize("equal_token", [False, True], ids=["same-object-token", "equal-other-token"])
def test_another_actual_owner_at_same_path_is_not_bound_owner(env, monkeypatch, equal_token):
    with env() as item:
        ledger = Ledger(monkeypatch)
        selected = deadline.Namespace3Deadline(item.token.absolute_monotonic) if equal_token else item.token
        with directories.DeadlineDirectory(item.owner.path, base=item.owner.path, deadline_token=selected) as other_dir:
            other_owner = image_bytes.ImagePrivateOwner(item.legacy, other_dir, deadline_token=selected)
            error(lambda: subject.private_connection(other_owner, byte_budget=item.budget), "INVALID_ARGUMENT")
        assert ledger.calls["connect"] == ledger.calls["stat"] == 0


def test_exit_before_enter_is_closed_and_borrowed_owner_remains_live(env, monkeypatch):
    with env() as item:
        ledger = Ledger(monkeypatch)
        selected = lease(item)
        before = selected.evidence
        error(lambda: selected.__exit__(None, None, None), "CLOSED")
        assert ledger.cons == [] and ledger.calls["con-close"] == 0
        item.private.verify()
        # NEW early-exit is a phase guard, not a recorded enter/body failure.
        assert selected.evidence == before and before["phase"] == "new" and before["retired"] is False


# Q05/Q09-Q12/Q29/Q30: all actual APIs, limits, four Cursors and Con close.
def test_real_ro_db_thirteen_api_order_four_cursor_close_limits_and_public_snapshot(env, monkeypatch):
    with env() as item:
        image_evidence = item.budget.evidence
        assert image_evidence["complete"] is image_evidence["retired"] is True
        ledger = Ledger(monkeypatch)
        selected = lease(item)
        with selected as con:
            assert type(con) is sqlite3.Connection and con.in_transaction is True
            cursor = con.execute("SELECT payload FROM samples")  # Extra raw consumer APIs.
            try:
                assert cursor.fetchall() == [(PLAIN,)]
            finally:
                cursor.close()
            assert (con.getlimit(LENGTH), con.getlimit(SQL_LENGTH)) == ledger.initial_limits[0]
            evidence = selected.evidence
            assert set(evidence) == FIELDS and evidence["bootstrap_complete"] is True
            assert evidence["connection_state"] == "owned" and evidence["cursor_states"] == ("closed",) * 4
            assert evidence["previous_limits"] == ledger.initial_limits[0] and evidence["limits_restored"] is True
            assert evidence["normal_exit"] is False
        normal_closed(ledger)
        original_limits = ledger.initial_limits[0]
        events = [event for event in ledger.events if event[0] in ("connect", "setlimit", "progress", "execute", "authorizer", "fetchall")]
        assert events == [("connect", (item.owner.path / "image.sqlite").as_uri() + "?mode=ro"),
            ("setlimit", LENGTH, 1024 * 1024), ("setlimit", SQL_LENGTH, 1024 * 1024),
            ("progress", "install", 1000), ("execute", SQL[0]), ("execute", SQL[1]),
            ("authorizer",), ("execute", SQL[2]), ("execute", SQL[3]), ("fetchall",),
            ("progress", "clear", 0), ("setlimit", LENGTH, original_limits[0]), ("setlimit", SQL_LENGTH, original_limits[1])]
        for index in range(3):
            assert ledger.events.index(("execute", SQL[index])) < ledger.events.index(("cursor-return", index)) < ledger.events.index(("execute", SQL[index + 1]))
        assert ledger.events.index(("fetchall",)) < ledger.events.index(("cursor-return", 3)) < ledger.events.index(("progress", "clear", 0))
        assert ledger.events.index(("cursor-return", 3)) < ledger.events.index(("con-attempt",))
        assert selected.evidence["exit_in_transaction"] is True and selected.evidence["normal_exit"] is True
        assert item.budget.evidence == image_evidence  # Successful retired image is borrowed, not reset.
        item.private.verify()


def test_real_copied_wal_ddl_and_latest_committed_row_are_read_without_immutable(env, monkeypatch):
    with env(wal=True) as item:
        assert set(item.expected) == {"", "-wal"} and len(item.expected["-wal"]) > 0
        ledger = Ledger(monkeypatch)
        selected = lease(item)
        with selected as con:
            cursor = con.execute("SELECT payload FROM samples")
            try:
                assert cursor.fetchall() == [(PLAIN,)]
            finally:
                cursor.close()
        normal_closed(ledger)
        assert selected.evidence["normal_exit"] is True
        # SQLite may create/use private SHM: no directory-only-DB/WAL assertion.


def test_actual_invalid_database_bytes_map_only_bootstrap_error_and_close_known_con(env, monkeypatch):
    with env(plain=b"this is not an SQLite database\x00" * 4) as item:
        ledger = Ledger(monkeypatch)
        selected = lease(item)
        with pytest.raises(readonly.ReadonlyImageError) as raised:
            selected.__enter__()
        first = raised.value
        assert first.code == "SOURCE_DATABASE_INVALID" and ledger.calls["connect"] == 1
        assert ledger.con_attempts == ledger.cons == ledger.con_returns
        failed(selected, first, monkeypatch)


# Q08: raw caller authorizer change is explicitly outside the 13 gated APIs.
def test_receiver_exact_con_compatibility_after_explicit_caller_authorizer_change(env, monkeypatch):
    with env(facility=True) as item:
        ledger = Ledger(monkeypatch)
        with lease(item) as con:
            assert type(con) is sqlite3.Connection and con.in_transaction is True
            con.set_authorizer(None)  # Extra caller API; default authorizer denies temp metadata.
            assert receiver.validate(con) is None  # Reference :memory: APIs are a separate seam.
        normal_closed(ledger)
        assert ledger.calls["authorizer"] == 1  # Only bootstrap forwarding counted.


# Q17: valid NEW first, then only the new clock expires before enter.
def test_valid_new_lease_then_expiry_refuses_connect_and_keeps_first(env, monkeypatch):
    with env() as item:
        ledger = Ledger(monkeypatch)
        selected = lease(item)
        monkeypatch.setattr(subject, "_monotonic", lambda: item.token.absolute_monotonic)
        first = error(selected.__enter__, "DEADLINE")
        assert ledger.calls["connect"] == 0 and selected.evidence["bootstrap_complete"] is False
        failed(selected, first, monkeypatch)


# Q13-Q15/Q18: actual legal resources/old limits register before postclock.
@pytest.mark.parametrize("stage", ["connect", "length", "sql-length", "cursor-first", "cursor-select", "fetchall", "progress-clear", "restore-sql"])
def test_actual_api_postreturn_expiry_keeps_known_resources_and_no_followup_restore(env, monkeypatch, stage):
    with env() as item:
        ledger = Ledger(monkeypatch)
        selected = lease(item)
        scalar = Scalar(subject._monotonic())
        monkeypatch.setattr(subject, "_monotonic", scalar)
        name = {"connect": "_connect", "length": "_setlimit", "sql-length": "_setlimit",
            "cursor-first": "_execute", "cursor-select": "_execute", "fetchall": "_fetchall",
            "progress-clear": "_set_progress_handler", "restore-sql": "_setlimit"}[stage]
        actual = getattr(subject, name)
        def post(*args, **kwargs):
            value = actual(*args, **kwargs)
            arm = stage == "connect" or stage == "fetchall"
            if name == "_setlimit":
                arm = stage == "length" and ledger.calls["setlimit"] == 1 or stage == "sql-length" and ledger.calls["setlimit"] == 2 or stage == "restore-sql" and ledger.calls["setlimit"] == 4
            elif name == "_execute":
                arm = args[1] == (SQL[0] if stage == "cursor-first" else SQL[3])
            elif name == "_set_progress_handler":
                arm = args[1] is None
            if arm:
                scalar.value = item.token.absolute_monotonic
            return value
        monkeypatch.setattr(subject, name, post)
        first = error(selected.__enter__, "DEADLINE")
        normal_closed(ledger)
        evidence = selected.evidence
        if stage == "connect":
            assert evidence["previous_limits"] == (None, None) and ledger.calls["setlimit"] == 0
        elif stage == "length":
            assert evidence["previous_limits"] == (ledger.initial_limits[0][0], None) and ledger.calls["setlimit"] == 1
        elif stage == "sql-length":
            assert evidence["previous_limits"] == ledger.initial_limits[0] and ledger.calls["setlimit"] == 2
        if stage != "restore-sql":
            assert not any(event[0] == "setlimit" and event[2] != 1024 * 1024 for event in ledger.events)
        assert evidence["limits_restored"] is False
        failed(selected, first, monkeypatch)


# Q14: legal Cursor registration precedes relationship consumer verification.
@pytest.mark.parametrize("fault", ["original", "different-con"])
def test_legal_cursor_is_closed_when_relation_descriptor_faults_or_rejects(env, monkeypatch, fault):
    with env() as item:
        ledger = Ledger(monkeypatch)
        selected = lease(item)
        relationship = subject._cursor_connection
        first = OSError("finite cursor relationship failure")
        def refused(cursor):
            relationship(cursor)
            if fault == "original":
                raise first
            return object()  # Explicit rejection projection, no foreign resource.
        monkeypatch.setattr(subject, "_cursor_connection", refused)
        if fault == "original":
            same_error(selected.__enter__, first)
        else:
            first = error(selected.__enter__, "INVALID_RESULT")
        normal_closed(ledger)
        assert len(ledger.cursors) == 1 and selected.evidence["cursor_states"][0] == "closed"
        assert ledger.events.index(("cursor-return", 0)) < ledger.events.index(("con-attempt",))
        failed(selected, first, monkeypatch)


# Q16: malformed old limits stay out of trusted diagnostic previous_limits.
@pytest.mark.parametrize("reported", [True, -1, None], ids=["bool", "negative", "none"])
def test_malformed_first_old_limit_never_becomes_trusted_saved_limit(env, monkeypatch, reported):
    with env() as item:
        ledger = Ledger(monkeypatch)
        selected = lease(item)
        limit = subject._setlimit
        def malformed(con, category, value):
            limit(con, category, value)
            return reported
        monkeypatch.setattr(subject, "_setlimit", malformed)
        first = error(selected.__enter__, "INVALID_RESULT")
        normal_closed(ledger)
        assert selected.evidence["previous_limits"] == (None, None)
        assert ledger.calls["setlimit"] == 1 and selected.evidence["limits_restored"] is False
        failed(selected, first, monkeypatch)


@pytest.mark.parametrize("name", ["_set_progress_handler", "_set_authorizer", "_fetchall"])
def test_malformed_nonresource_api_result_refuses_and_closes_known_resources(env, monkeypatch, name):
    with env() as item:
        ledger = Ledger(monkeypatch)
        selected = lease(item)
        actual = getattr(subject, name)
        def malformed(*args):
            actual(*args)
            return True if name != "_fetchall" else ()
        monkeypatch.setattr(subject, name, malformed)
        first = error(selected.__enter__, "INVALID_RESULT")
        normal_closed(ledger)
        failed(selected, first, monkeypatch)


@pytest.mark.parametrize("name,expired", [("_connect", False), ("_execute", False), ("_connect", True), ("_execute", True)])
def test_illegal_no_resource_return_is_unknown_without_fake_closer(env, monkeypatch, name, expired):
    with env() as item:
        ledger = Ledger(monkeypatch)
        selected = lease(item)
        scalar = Scalar(subject._monotonic())
        monkeypatch.setattr(subject, "_monotonic", scalar)
        def malformed(*_args, **_kwargs):
            if expired:
                scalar.value = item.token.absolute_monotonic
            return None  # No Connection/Cursor is physically created.
        monkeypatch.setattr(subject, name, malformed)
        first = error(selected.__enter__, "DEADLINE" if expired else "INVALID_RESULT")
        if name == "_connect":
            assert ledger.cons == ledger.con_attempts == [] and selected.evidence["connection_state"] == "unknown"
        else:
            normal_closed(ledger)
            assert ledger.cursors == [] and selected.evidence["cursor_states"][0] == "unknown"
        failed(selected, first, monkeypatch)


# Q16: opaque actual connection return belongs only to the fixture recovery.
@pytest.mark.parametrize("after", [False, True], ids=["before-actual-connect", "after-actual-connect-return"])
def test_opaque_connect_never_guesses_owner_and_fixture_recovers_only_its_known_unattempted_con(env, monkeypatch, after):
    with env() as item:
        ledger = Ledger(monkeypatch)
        selected = lease(item)
        actual = subject._connect
        first = OSError("finite opaque connect failure")
        known, protected_first = [], None
        def opaque(*args, **kwargs):
            if after:
                known.append(actual(*args, **kwargs))
            raise first
        monkeypatch.setattr(subject, "_connect", opaque)
        try:
            same_error(selected.__enter__, first)
            assert ledger.con_attempts == [] and selected.evidence["connection_state"] == "unknown"
            failed(selected, first, monkeypatch)
        except BaseException as failure:
            protected_first = failure
            raise
        finally:
            for con in known:
                assert not any(value is con for value in ledger.con_attempts)
                try:
                    ledger.original["_con_close"](con)  # Fixture-only, no probe/double close.
                except BaseException:
                    if protected_first is None:
                        raise
                    note(protected_first)


def test_opaque_execute_before_actual_call_preserves_original_and_closes_only_known_con(env, monkeypatch):
    with env() as item:
        ledger = Ledger(monkeypatch)
        selected = lease(item)
        first = OSError("finite execute before-call failure")
        def refused(_con, _sql):
            raise first
        monkeypatch.setattr(subject, "_execute", refused)
        same_error(selected.__enter__, first)
        normal_closed(ledger)
        assert ledger.cursors == [] and selected.evidence["cursor_states"][0] == "unknown"
        failed(selected, first, monkeypatch)


# Q20/Q21: real private files change only before SQLite has acquired them.
@pytest.mark.parametrize("change", ["replace-db", "grow-wal", "new-wal", "journal-empty", "journal-nonempty", "shm-empty"])
def test_actual_unbound_or_changed_private_file_is_refused_before_connect(env, monkeypatch, change):
    with env(wal=change == "grow-wal") as item:
        ledger = Ledger(monkeypatch)
        selected = lease(item)
        path = item.owner.path / "image.sqlite"
        if change == "replace-db":
            preserved = item.owner.path / "preserved-original.sqlite"
            assert preserved.resolve().parent == item.owner.path.resolve()
            os.replace(native(path), native(preserved))
            with open(native(path), "xb") as sink:
                assert sink.write(item.expected[""]) == len(item.expected[""])
            assert source.file_signature(os.stat(native(path)))[:2] != source.file_signature(os.stat(native(preserved)))[:2]
        elif change == "grow-wal":
            with open(native(item.owner.path / "image.sqlite-wal"), "ab") as sink:
                assert sink.write(b"growth") == 6
        else:
            suffix = "-wal" if change == "new-wal" else "-shm" if change == "shm-empty" else "-journal"
            payload = b"journal" if change == "journal-nonempty" else b""
            with open(native(path.with_name(path.name + suffix)), "xb") as sink:
                assert sink.write(payload) == len(payload)
        with pytest.raises(readonly.ReadonlyImageError) as raised:
            selected.__enter__()
        first = raised.value
        assert first.code == "PRIVATE_IMAGE_CHANGED" and ledger.calls["connect"] == 0
        failed(selected, first, monkeypatch)


# Q22: owner/preflight SQLite-origin errors must not become bootstrap SQL map.
@pytest.mark.parametrize("kind", [OSError, sqlite3.DatabaseError], ids=["io", "sqlite-origin"])
def test_owner_verifier_original_exception_is_not_bootstrap_sql_mapping(env, monkeypatch, kind):
    with env() as item:
        ledger = Ledger(monkeypatch)
        selected = lease(item)
        verify = subject._owner_verify
        first = kind("finite owner verification failure")
        def refused(owner):
            verify(owner)
            raise first
        monkeypatch.setattr(subject, "_owner_verify", refused)
        same_error(selected.__enter__, first)
        assert ledger.calls["connect"] == 0
        failed(selected, first, monkeypatch)


# Q23: narrow fixed-API/boot-Cursor-close mapping; each callback is finite.
@pytest.mark.parametrize("name", ["_connect", "_execute", "_fetchall", "_cursor_close"])
def test_bootstrap_sql_error_is_mapped_once_and_known_resources_are_closed(env, monkeypatch, name):
    with env() as item:
        ledger = Ledger(monkeypatch)
        selected = lease(item)
        actual = getattr(subject, name)
        raw = sqlite3.DatabaseError("finite declared bootstrap API failure")
        fired = False
        def refused(*args, **kwargs):
            nonlocal fired
            if fired:
                return actual(*args, **kwargs)
            fired = True
            if name in ("_fetchall", "_cursor_close"):
                actual(*args, **kwargs)
            raise raw
        monkeypatch.setattr(subject, name, refused)
        with pytest.raises(readonly.ReadonlyImageError) as raised:
            selected.__enter__()
        first = raised.value
        assert first.code == "SOURCE_DATABASE_INVALID" and first is not raw
        if name != "_connect":
            assert ledger.con_attempts == ledger.cons == ledger.con_returns
        if name == "_cursor_close":
            assert len(ledger.cur_attempts) == 1 and selected.evidence["cursor_states"][0] == "unknown"
        failed(selected, first, monkeypatch)


# Q24/Q25: original controls and exception attribute overrides are preserved.
@pytest.mark.parametrize("kind", [OSError, KeyboardInterrupt, SystemExit], ids=["ordinary", "interrupt", "exit"])
def test_non_sql_first_preserves_identity_and_cleanup_after_actual_cursor_return(env, monkeypatch, kind):
    with env() as item:
        ledger = Ledger(monkeypatch)
        selected = lease(item)
        actual = subject._cursor_connection
        first = kind("finite original relationship consumer failure")
        def refused(cursor):
            actual(cursor)
            raise first
        monkeypatch.setattr(subject, "_cursor_connection", refused)
        same_error(selected.__enter__, first)
        normal_closed(ledger)
        failed(selected, first, monkeypatch)


def test_exception_overridden_traceback_and_with_traceback_getters_do_not_skip_cleanup(env, monkeypatch):
    class TracebackTrap(OSError):
        def __getattribute__(self, name):
            if name in ("__traceback__", "with_traceback"):
                raise RuntimeError("finite overridden exception getter rejection")
            return BaseException.__getattribute__(self, name)

    with env() as item:
        ledger = Ledger(monkeypatch)
        selected = lease(item)
        actual = subject._cursor_connection
        first = TracebackTrap("finite original bootstrap relationship failure")
        def refused(cursor):
            actual(cursor)
            raise first
        monkeypatch.setattr(subject, "_cursor_connection", refused)
        same_error(selected.__enter__, first)
        normal_closed(ledger)
        failed(selected, first, monkeypatch)


# Q19/Q26: a physical close completed; subject opaque slot remains UNKNOWN.
@pytest.mark.parametrize("name", ["_cursor_close", "_con_close"])
def test_actual_close_postfailure_is_unknown_single_attempt_without_probe_or_retry(env, monkeypatch, name):
    with env() as item:
        ledger = Ledger(monkeypatch)
        selected = lease(item)
        actual = getattr(subject, name)
        first = OSError("finite completed close then opaque failure")
        fired = False
        def post(value):
            nonlocal fired
            actual(value)
            if not fired:
                fired = True
                raise first
        monkeypatch.setattr(subject, name, post)
        with pytest.raises(OSError) as raised:
            with selected:
                pass
        assert raised.value is first
        assert ledger.con_attempts == ledger.cons == ledger.con_returns
        if name == "_cursor_close":
            assert len(ledger.cur_attempts) == 1 and selected.evidence["cursor_states"][0] == "unknown"
        else:
            assert selected.evidence["connection_state"] == "unknown"
        failed(selected, first, monkeypatch)
        # Subject attempted: no fixture extra close/probe of UNKNOWN resources.


@pytest.mark.parametrize("stage", ["cursor-post", "con-pre", "con-post"])
def test_expiry_at_close_gate_still_attempts_actual_close_and_keeps_closed_facts(env, monkeypatch, stage):
    with env() as item:
        ledger = Ledger(monkeypatch)
        selected = lease(item)
        scalar = Scalar(subject._monotonic())
        monkeypatch.setattr(subject, "_monotonic", scalar)
        name = "_cursor_close" if stage == "cursor-post" else "_con_close"
        actual = getattr(subject, name)
        def post(value):
            result = actual(value)
            if stage != "con-pre":
                scalar.value = item.token.absolute_monotonic
            return result
        monkeypatch.setattr(subject, name, post)
        with pytest.raises(subject.SqlBootstrapDeadlineError) as raised:
            with selected:
                if stage == "con-pre":
                    scalar.value = item.token.absolute_monotonic
        first = raised.value
        assert first.code == "SQL_BOOTSTRAP_DEADLINE_DEADLINE"
        normal_closed(ledger)
        assert selected.evidence["connection_state"] == "closed"
        failed(selected, first, monkeypatch)


# Q27: body first, including SQLite, is outside bootstrap mapping.
@pytest.mark.parametrize("kind", [OSError, KeyboardInterrupt, SystemExit, sqlite3.DatabaseError],
                         ids=["ordinary", "interrupt", "exit", "body-sqlite"])
def test_yielded_body_first_survives_cleanup_sql_error_and_expiry(env, monkeypatch, kind):
    with env() as item:
        ledger = Ledger(monkeypatch)
        selected = lease(item)
        scalar = Scalar(subject._monotonic())
        monkeypatch.setattr(subject, "_monotonic", scalar)
        first, cleanup = kind("finite raw consumer first"), sqlite3.DatabaseError("finite con-close postfailure")
        with pytest.raises(kind) as raised:
            with selected:
                conclose = subject._con_close
                def post(con):
                    conclose(con)
                    raise cleanup
                monkeypatch.setattr(subject, "_con_close", post)
                scalar.value = item.token.absolute_monotonic
                raise first
        assert raised.value is first and ledger.calls["transaction"] == 0
        assert ledger.con_attempts == ledger.cons == ledger.con_returns
        assert selected.evidence["connection_state"] == "unknown"
        assert "SQL_BOOTSTRAP_DEADLINE_CLEANUP_FAILED" in getattr(first, "__notes__", [])
        failed(selected, first, monkeypatch)


def test_yielded_con_close_first_sqlite_error_keeps_original_without_bootstrap_mapping(env, monkeypatch):
    with env() as item:
        ledger = Ledger(monkeypatch)
        selected = lease(item)
        actual = subject._con_close
        first = sqlite3.DatabaseError("finite normal terminal close postfailure")
        def post(con):
            actual(con)
            raise first
        monkeypatch.setattr(subject, "_con_close", post)
        with pytest.raises(sqlite3.DatabaseError) as raised:
            with selected:
                pass
        assert raised.value is first and selected.evidence["bootstrap_complete"] is True
        assert ledger.con_attempts == ledger.cons == ledger.con_returns
        failed(selected, first, monkeypatch)


# Q28: actual public transaction state is liveness, not a True-only requirement.
@pytest.mark.parametrize("method", ["rollback", "commit"])
def test_actual_caller_transaction_end_false_is_live_and_normal_exit_allowed(env, monkeypatch, method):
    with env() as item:
        ledger = Ledger(monkeypatch)
        selected = lease(item)
        with selected as con:
            assert con.in_transaction is True
            getattr(con, method)()  # Extra raw caller API, no automatic gate claim.
            assert con.in_transaction is False
        normal_closed(ledger)
        assert selected.evidence["exit_in_transaction"] is False and selected.evidence["normal_exit"] is True


def test_actual_caller_early_connection_close_rejects_normal_exit_with_original_programming_error(env, monkeypatch):
    with env() as item:
        ledger = Ledger(monkeypatch)
        selected = lease(item)
        with pytest.raises(sqlite3.ProgrammingError) as raised:
            with selected as con:
                con.close()  # Actual caller close, not a new module ownership claim.
        first = raised.value
        assert selected.evidence["normal_exit"] is False and selected.evidence["exit_in_transaction"] is None
        assert ledger.con_attempts == ledger.cons == ledger.con_returns  # Sidecar one idempotent attempt.
        failed(selected, first, monkeypatch)


def test_terminal_non_bool_projection_is_invalid_result_and_con_still_closed(env, monkeypatch):
    with env() as item:
        ledger = Ledger(monkeypatch)
        selected = lease(item)
        actual = subject._in_transaction
        def projected(con):
            actual(con)
            return 1
        monkeypatch.setattr(subject, "_in_transaction", projected)
        with pytest.raises(subject.SqlBootstrapDeadlineError) as raised:
            with selected:
                pass
        first = raised.value
        assert first.code == "SQL_BOOTSTRAP_DEADLINE_INVALID_RESULT"
        normal_closed(ledger)
        assert selected.evidence["exit_in_transaction"] is None
        failed(selected, first, monkeypatch)


# Q29/Q30: diagnostics have no resource objects, clock or ability to reset.
def test_successful_public_diagnostic_is_detached_zero_clock_and_borrowed_root_still_live(env, monkeypatch):
    with env() as item:
        ledger = Ledger(monkeypatch)
        selected = lease(item)
        with selected:
            pass
        normal_closed(ledger)
        original = selected.evidence
        assert set(original) == FIELDS and original["phase"] == "retired" and original["normal_exit"] is True
        assert original["cursor_states"] == ("closed",) * 4 and original["connection_state"] == "closed"
        item.private.verify()
        assert item.budget.evidence["complete"] is True
        for name in ("_monotonic", "_connect", "_owner_verify", "_entry_stat"):
            monkeypatch.setattr(subject, name, no_action)
        changed = selected.evidence
        changed["phase"], changed["retired"], changed["normal_exit"] = "new", False, False
        assert selected.evidence == original
        error(selected.__enter__, "CLOSED")
        assert selected.evidence == original


# Q03: per-lease entry claim is set before a resource-producing alias begins.
def test_reentrant_entry_from_new_connect_alias_is_closed_without_second_connection(env, monkeypatch):
    with env() as item:
        ledger = Ledger(monkeypatch)
        selected = lease(item)
        actual = subject._connect
        nested = []
        def reentered(*args, **kwargs):
            nested.append(error(selected.__enter__, "CLOSED"))
            return actual(*args, **kwargs)
        monkeypatch.setattr(subject, "_connect", reentered)
        with selected as con:
            assert type(con) is sqlite3.Connection and con.in_transaction is True
        normal_closed(ledger)
        assert len(nested) == 1 and ledger.calls["connect"] == 1
        assert selected.evidence["normal_exit"] is True


# Q18: source-only VM protocol check, no callback execution or closure sampling.
def test_vm_callback_static_body_keeps_interval_threshold_and_has_no_clock():
    root = Path(__file__).parents[1]
    raw = (root / "reno/personal_authority_sql_bootstrap_deadline.py").read_bytes()
    tree = ast.parse(raw)
    candidates = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "budget"]
    assert len(candidates) == 1
    callback = candidates[0]
    increment = [node for node in ast.walk(callback) if isinstance(node, ast.AugAssign)]
    assert len(increment) == 1 and isinstance(increment[0].target, ast.Name) and increment[0].target.id == "steps"
    assert isinstance(increment[0].op, ast.Add) and isinstance(increment[0].value, ast.Name) and increment[0].value.id == "_VM_INTERVAL"
    comparisons = [node for node in ast.walk(callback) if isinstance(node, ast.Compare)]
    assert len(comparisons) == 1 and isinstance(comparisons[0].left, ast.Name) and comparisons[0].left.id == "steps"
    assert len(comparisons[0].ops) == 1 and isinstance(comparisons[0].ops[0], ast.Gt)
    threshold = comparisons[0].comparators[0]
    assert isinstance(threshold, ast.Attribute) and threshold.attr == "BOOTSTRAP_VM_STEPS"
    assert isinstance(threshold.value, ast.Name) and threshold.value.id == "readonly"
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id in ("_clock", "_monotonic") for node in ast.walk(callback))
    interval = [node.value.value for node in tree.body if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "_VM_INTERVAL" for target in node.targets)
        and isinstance(node.value, ast.Constant)]
    assert interval == [1000]
    old_tree = ast.parse((root / "reno/storage_readonly.py").read_bytes())
    maximum = [node.value.value for node in old_tree.body if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "BOOTSTRAP_VM_STEPS" for target in node.targets)
        and isinstance(node.value, ast.Constant)]
    assert maximum == [50000]
