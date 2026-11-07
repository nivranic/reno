"""r831 fixed SQL consumers: one real image path and finite new-alias faults.

Root alone imports, collects and executes this file. All SQL preparation owns
new QA resources; no ordinary database, provider slots or old globals are used
as an oracle. Callback observations are functional calls, not frame sampling.
"""
from collections import Counter, deque
from contextlib import ExitStack
import copy
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace

import pytest

from reno import personal_authority_committed_image_deadline as images
from reno import personal_authority_directory_deadline as directories
from reno import personal_authority_namespace3_deadline as deadline
from reno import personal_authority_sql_observation_deadline as subject
from reno import personal_mirror_contract as facts
from reno import personal_mirror_transfer_schema as receiver
from reno import personal_preservation_contract as contract
from reno import storage_lifecycle as lifecycle


pytestmark = pytest.mark.skipif(os.name != "nt", reason="actual Windows QA EX and private WAL image")
ALIASES = ("_monotonic", "_require_guard", "_guard_verify", "_profile_identity",
           "_image_token", "_image_connection", "_image_fingerprint", "_image_active",
           "_connect_memory", "_cursor", "_execute", "_fetchone", "_fetchmany",
           "_cursor_close", "_con_close", "_cursor_connection", "_setlimit",
           "_set_progress_handler", "_set_authorizer", "_row_factory", "_text_factory",
           "_typed_cell", "_canonical_bytes", "_projection_authorizer")
SESSION_FIELDS = {"phase", "retired", "failed", "normal_exit", "source_borrowed",
                  "profile_identity", "source_fingerprint", "source",
                  "personal_reference", "receiver_reference"}
ROLE_FIELDS = {"connection_state", "entered", "normal_exit", "native_call_starts",
               "native_call_completions", "cleanup_call_starts", "cursor_create_starts",
               "cursor_states", "data_rows", "data_typed_bytes", "metadata_rows",
               "metadata_typed_bytes", "fee_complete", "vm_reported_quanta",
               "previous_limits", "progress_restore_state", "authorizer_restore_state"}
ROWS_FIELDS = {"phase", "retired", "failed", "normal_exit", "cursor_state",
               "rows_observed", "eof_observed"}
PROJECT_TABLE = "renovation_project"
LIBRARY = "11111111-1111-4111-8111-111111111111"
PRIMARY = "22222222-2222-4222-8222-222222222222"
STAMP = "2026-10-12T00:00:00Z"
PROJECT_COUNT = 300
RAW_STATEMENTS = {
    "SELECT " + ",".join('"' + column + '"' for column in columns) + ' FROM main."' + table + '"'
    for table, columns in {**contract.PERSONAL_COLUMNS, **contract.RECEIVER_COLUMNS}.items()
}


def native(path):
    value = os.path.abspath(os.fspath(path))
    if value.startswith("\\\\?\\"):
        return value
    return "\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value


def qa_temp():
    qa = Path(os.environ["RENO_QA_ROOT"]).resolve(strict=True)
    current = Path(tempfile.gettempdir()).resolve(strict=True)
    current.relative_to(qa)
    assert current.is_dir()


@pytest.fixture(scope="module")
def actual_source(tmp_path_factory):
    """Actual schema17 source WAL, one EX/image and one original 30s token."""
    root = tmp_path_factory.mktemp("绍兴-fixed-sql")
    profile = lifecycle.StorageProfile(root / "data/绍兴-observation.sqlite3", root / "media",
                                      root / "reports", root / "checkpoints")
    profile.database.parent.mkdir(parents=True)
    initial = sqlite3.connect(native(profile.database))
    try:
        cursor = initial.executescript("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT); "
                                      "INSERT INTO meta VALUES('migration_version','17');")
        cursor.close()
        initial.commit()
    finally:
        initial.close()
    lifecycle.initialize(profile)  # Actual original default 30, no fake guard.
    with lifecycle.exclusive(profile) as guard:
        assert type(guard) is lifecycle.StorageGuard and guard.exclusive is True
        rw = sqlite3.connect(native(profile.database))
        first = None

        def sql(statement, parameters=()):
            cursor = rw.execute(statement, parameters)
            try:
                return cursor.fetchall()
            finally:
                cursor.close()

        def close_source():
            nonlocal rw
            selected, rw = rw, None
            if selected is not None:
                selected.close()

        try:
            assert sql("PRAGMA journal_mode=WAL")[0][0].lower() == "wal"
            sql("PRAGMA wal_autocheckpoint=0")
            for table, ddl in facts._DDL.items():
                sql(f"CREATE TABLE {table} ({ddl})")
            for table, ddl in receiver.TABLES.items():
                sql(f"CREATE TABLE {table} ({ddl})")
            for statement in receiver.INDEXES + receiver.TRIGGERS:
                sql(statement)
            expected_projects = []
            for index in range(PROJECT_COUNT):
                project = f"00000000-0000-4000-8000-{index + 1:012d}"
                name = f"{index:03d}-绍兴-O'灯\\线"
                profile_json = json.dumps({"city": "绍兴", "quote": '"', "slash": "\\"},
                                          ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                row = (project, name, profile_json, 1, STAMP, STAMP, None)
                sql("INSERT INTO renovation_project VALUES(?,?,?,?,?,?,?)", row)
                expected_projects.append(row)
            metadata = {"default_project_id": expected_projects[0][0], "library_id": LIBRARY,
                        "personal_primary_id": PRIMARY, "primary_epoch": "1"}
            for key, value in metadata.items():
                sql("INSERT INTO meta VALUES(?,?)", (key, value))
            rw.commit()
            assert profile.database.with_name(profile.database.name + "-wal").stat().st_size > 0
            limited = deadline._RestrictedBudget(deadline.Namespace3Deadline(1.0e15))
            token = limited.effective_deadline
            qa_temp()
            with directories.DeadlineDirectory(profile.database.parent,
                    base=profile.database.parent, deadline_token=token) as directory:
                image_lease = images.committed_image_locked(profile, directory,
                                                            deadline_token=token, attempts=1)
                with image_lease as image:
                    assert image.deadline_token is token and image.require_active() is None
                    yield SimpleNamespace(profile=profile, guard=guard, directory=directory,
                        image=image, image_lease=image_lease, token=token,
                        projects=tuple(expected_projects), meta=tuple(sorted(metadata.items())))
                close_source()  # After image source terminal and its own SQL/pin/TEMP cleanup.
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
                    try:
                        BaseException.add_note(first, "R831_FIXTURE_CLEANUP_FAILED")
                    except BaseException:
                        pass


def independent_row_bytes(row):
    """Independent tags for our returned SQLite primitives, no subject encoder."""
    cells = []
    for value in row:
        if value is None:
            cells.append(["null"])
        elif type(value) is int:
            cells.append(["int", value])
        elif type(value) is float:
            cells.append(["float", value.hex()])
        elif type(value) is bytes:
            cells.append(["blob", value.hex()])
        else:
            assert type(value) is str
            cells.append(["text", value])
    return json.dumps(cells, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


class Ledger:
    """New-alias functional calls and actual returned values only."""
    def __init__(self, monkeypatch, item):
        self.original = {name: getattr(subject, name) for name in ALIASES}
        self.calls, self.returns = Counter(), Counter()
        self.source_con = item.image.connection
        self.connection_role = {id(self.source_con): "source"}
        self.connecting_role = None
        self.connections, self.cursors, self.executions, self.fetches = [], [], [], []
        self.cursor_con, self.cursor_group = {}, {}
        self.cursor_close_attempts, self.cursor_close_returns = [], []
        self.con_close_attempts, self.con_close_returns = [], []
        self.limit_calls, self.progress_calls, self.authorizer_calls = [], [], []
        self.pending, self.encoded = deque(), []

        def wrapper(name):
            def observed(*args, **kwargs):
                self.calls[name] += 1
                if name == "_cursor_close":
                    self.cursor_close_attempts.append(args[0])
                elif name == "_con_close":
                    assert args[0] is not self.source_con
                    self.con_close_attempts.append(args[0])
                result = self.original[name](*args, **kwargs)
                self.returns[name] += 1
                if name in {"_require_guard", "_guard_verify"}:
                    assert result is item.guard
                    if name == "_require_guard":
                        assert kwargs == {"exclusive": True}
                elif name == "_image_token":
                    assert args[0] is item.image and result is item.token
                elif name == "_image_connection":
                    assert args[0] is item.image and result is self.source_con
                elif name == "_image_active":
                    assert args[0] is item.image and result is None
                elif name == "_connect_memory":
                    assert not args and not kwargs and type(result) is sqlite3.Connection
                    assert self.connecting_role in {"personal_reference", "receiver_reference"}
                    self.connection_role[id(result)] = self.connecting_role
                    self.connections.append(result)
                elif name == "_cursor":
                    assert type(result) is sqlite3.Cursor
                    self.cursors.append(result)
                    self.cursor_con[id(result)] = args[0]
                elif name == "_cursor_connection":
                    assert result is self.cursor_con[id(args[0])]
                elif name == "_execute":
                    assert result is args[0]
                    statement = args[1]
                    assert type(statement) is str and len(statement.encode("utf-8")) <= 32768
                    con = self.cursor_con[id(args[0])]
                    role = self.connection_role[id(con)]
                    group = "data" if role == "source" and statement in RAW_STATEMENTS else "metadata"
                    self.cursor_group[id(args[0])] = role, group
                    self.executions.append((args[0], con, statement, args[2] if len(args) > 2 else kwargs.get("parameters", ())))
                    if role == "source":
                        assert statement.startswith("SELECT ") or statement.startswith("PRAGMA ")
                elif name in {"_fetchone", "_fetchmany"}:
                    cursor = args[0]
                    selected = [] if result is None else [result] if name == "_fetchone" else result
                    assert type(selected) is list and all(type(row) is tuple for row in selected)
                    role, group = self.cursor_group[id(cursor)]
                    self.fetches.append((name, cursor, args[1:] or tuple(kwargs.values()), tuple(selected), result))
                    self.pending.extend((role, group, row) for row in selected)
                elif name == "_canonical_bytes":
                    value = args[0]
                    if self.pending and type(value) is list and all(type(cell) is list for cell in value):
                        role, group, row = self.pending.popleft()
                        assert type(result) is bytes and result == independent_row_bytes(row)
                        self.encoded.append((role, group, row, result))
                elif name == "_setlimit":
                    assert type(result) is int and result >= 0
                    self.limit_calls.append((args[0], args[1], args[2], result))
                elif name == "_set_progress_handler":
                    assert result is None
                    self.progress_calls.append((args[0], args[1], args[2]))
                elif name == "_set_authorizer":
                    assert result is None
                    self.authorizer_calls.append((args[0], args[1]))
                elif name == "_row_factory":
                    assert result is None
                elif name == "_text_factory":
                    assert result is str
                if name == "_cursor_close":
                    assert result is None
                    self.cursor_close_returns.append(args[0])
                elif name == "_con_close":
                    assert result is None
                    self.con_close_returns.append(args[0])
                return result
            return observed
        for name in ALIASES:
            if name != "_monotonic":
                monkeypatch.setattr(subject, name, wrapper(name))


class Scalar:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


def session(item):
    return subject.SqlObservationDeadlineLease(item.profile, item.image, deadline_token=item.token)


def code_error(call, suffix):
    with pytest.raises(subject.SqlObservationDeadlineError) as caught:
        call()
    assert caught.value.code == "SQL_OBSERVATION_DEADLINE_" + suffix
    return caught.value


def same_error(call, first):
    with pytest.raises(type(first)) as caught:
        call()
    assert caught.value is first


def no_action(*_args, **_kwargs):
    pytest.fail("retired diagnostics/CLOSED/sticky failure invoked a new consumer")


def drain(rows, size=64):
    values = []
    while True:
        chunk = rows.fetchmany(size)
        assert type(chunk) is tuple and all(type(row) is tuple for row in chunk)
        if not chunk:
            return tuple(values)
        values.extend(chunk)


def collect(owner, recipe, **kwargs):
    selected = owner.query(recipe, **kwargs)
    with selected as rows:
        assert rows is selected
        values = drain(rows)
    evidence = selected.evidence
    assert set(evidence) == ROWS_FIELDS and evidence["normal_exit"] is True
    assert evidence["cursor_state"] == "closed" and evidence["eof_observed"] is True
    assert evidence["rows_observed"] == len(values)
    return values, selected


def retired_history(chosen, resources, monkeypatch):
    original = copy.deepcopy(chosen.evidence)
    assert set(original) == SESSION_FIELDS and original["phase"] == "retired"
    assert original["retired"] is True and original["failed"] is False and original["normal_exit"] is True
    with monkeypatch.context() as patch:
        for name in ALIASES:
            patch.setattr(subject, name, no_action)
        for call in (chosen.__enter__, lambda: chosen.query("migration_version"),
                     chosen.personal_reference, chosen.receiver_reference,
                     lambda: chosen.__exit__(None, None, None)):
            code_error(call, "CLOSED")
            assert chosen.evidence == original
        for kind, selected in resources:
            history = copy.deepcopy(selected.evidence)
            calls = [selected.__enter__, lambda selected=selected: selected.__exit__(None, None, None)]
            if kind == "rows":
                assert set(history) == ROWS_FIELDS
                calls.extend((selected.fetchone, selected.fetchmany))
            else:
                assert set(history) == ROLE_FIELDS
                calls.append(lambda selected=selected: selected.query("index_list", table=PROJECT_TABLE))
            for call in calls:
                code_error(call, "CLOSED")
                assert selected.evidence == history and chosen.evidence == original
            local = selected.evidence
            local["normal_exit"] = False
            assert selected.evidence == history
        detached = chosen.evidence
        detached["source"]["metadata_rows"] = -1
        detached["source_fingerprint"]["files"][""]["sha256"] = "0" * 64
        assert chosen.evidence == original


def failed_history(chosen, first, monkeypatch):
    evidence = copy.deepcopy(chosen.evidence)
    assert set(evidence) == SESSION_FIELDS and evidence["phase"] == "retired"
    assert evidence["retired"] is True and evidence["failed"] is True and evidence["normal_exit"] is False
    assert evidence["source_borrowed"] is True and evidence["source"]["connection_state"] == "borrowed"
    assert all(set(evidence[role]) == ROLE_FIELDS for role in ("source", "personal_reference", "receiver_reference"))
    with monkeypatch.context() as patch:
        for name in ALIASES:
            patch.setattr(subject, name, no_action)
        same_error(chosen.__enter__, first)
        same_error(lambda: chosen.query("migration_version"), first)
        same_error(lambda: chosen.__exit__(None, None, None), first)
        assert chosen.evidence == evidence
        detached = chosen.evidence
        detached["source"]["data_rows"] = -1
        detached["failed"] = False
        assert chosen.evidence == evidence
    return evidence


def assert_single_closers(ledger):
    assert Counter(map(id, ledger.cursor_close_attempts)) == Counter(map(id, ledger.cursors))
    assert Counter(map(id, ledger.cursor_close_returns)) == Counter(map(id, ledger.cursors))
    assert Counter(map(id, ledger.con_close_attempts)) == Counter(map(id, ledger.connections))
    assert Counter(map(id, ledger.con_close_returns)) == Counter(map(id, ledger.connections))
    assert all(con is not ledger.source_con for con in ledger.con_close_attempts)


# Q02/Q03/Q09: only complete actual borrowed-image/two-memory positive.
def test_real_image_fixed_queries_two_memory_refs_fees_eof_and_sole_closers(actual_source, monkeypatch):
    item = actual_source
    ledger = Ledger(monkeypatch, item)
    chosen = session(item)
    resources = []
    with chosen as observation:
        assert observation is chosen and chosen.deadline_token is item.token
        assert collect(observation, "integrity")[0] == (("ok",),)
        assert collect(observation, "foreign_keys_check")[0] == ()
        assert collect(observation, "migration_version")[0] == (("17",),)
        meta, meta_rows = collect(observation, "personal_meta")
        assert meta == item.meta
        resources.append(("rows", meta_rows))
        project_rows, raw_rows = collect(observation, "raw_table", table=PROJECT_TABLE)
        assert Counter(project_rows) == Counter(item.projects)
        resources.append(("rows", raw_rows))
        objects, _ = collect(observation, "schema_object", table=PROJECT_TABLE)
        assert len(objects) == 1 and objects[0][:2] == ("table", PROJECT_TABLE)
        layout, _ = collect(observation, "table_xinfo", table=PROJECT_TABLE)
        assert tuple(row[1] for row in layout) == tuple(contract.PERSONAL_COLUMNS[PROJECT_TABLE])
        indexes, _ = collect(observation, "index_list", table=PROJECT_TABLE)
        assert indexes and type(indexes[0][1]) is str
        index_name = indexes[0][1]
        assert collect(observation, "index_info", table=PROJECT_TABLE, index_name=index_name)[0]
        ledger.connecting_role = "personal_reference"
        personal = observation.personal_reference()
        with personal as reference:
            assert reference is personal
            assert collect(reference, "schema_object", table=PROJECT_TABLE)[0][0][:2] == ("table", PROJECT_TABLE)
            assert collect(reference, "table_xinfo", table=PROJECT_TABLE)[0] == layout
            reference_indexes, _ = collect(reference, "index_list", table=PROJECT_TABLE)
            assert reference_indexes and collect(reference, "index_info", table=PROJECT_TABLE,
                                                index_name=reference_indexes[0][1])[0]
            assert collect(reference, "foreign_key_list", table=PROJECT_TABLE)[0] == ()
        resources.append(("reference", personal))
        ledger.connecting_role = "receiver_reference"
        receiving = observation.receiver_reference()
        with receiving as reference:
            assert reference is receiving
            receiver_objects, _ = collect(reference, "main_objects")
            assert {row[1] for row in receiver_objects if row[0] == "table"} == set(receiver.TABLES)
            table_info, _ = collect(reference, "table_info", table=receiver.INTENT)
            assert table_info
            received_indexes, _ = collect(reference, "index_list", table=receiver.INTENT)
            assert received_indexes
            name = received_indexes[0][1]
            assert len(collect(reference, "index_sql", table=receiver.INTENT, index_name=name)[0]) == 1
            assert collect(reference, "index_xinfo", table=receiver.INTENT, index_name=name)[0]
            assert len(collect(reference, "table_sql", table=receiver.INTENT)[0]) == 1
        resources.append(("reference", receiving))
        # A scope may close an unread Cursor normally while truthfully lacking EOF.
        partial = observation.query("raw_table", table=PROJECT_TABLE)
        with partial:
            assert partial.fetchone() in item.projects
        assert partial.evidence["normal_exit"] is True and partial.evidence["eof_observed"] is False
        resources.append(("rows", partial))
    evidence = chosen.evidence
    assert set(evidence) == SESSION_FIELDS and evidence["profile_identity"] == tuple(item.profile.identity)
    assert evidence["source_fingerprint"] == item.image.source_fingerprint
    assert evidence["source"]["data_rows"] == PROJECT_COUNT + 1
    for role in ("source", "personal_reference", "receiver_reference"):
        actual = evidence[role]
        assert set(actual) == ROLE_FIELDS and actual["fee_complete"] is True
        assert actual["normal_exit"] is True and actual["entered"] is True
        assert actual["cursor_states"] and all(state == "closed" for state in actual["cursor_states"])
        assert actual["cursor_create_starts"] == sum(ledger.connection_role[id(ledger.cursor_con[id(cursor)])] == role for cursor in ledger.cursors)
        assert actual["progress_restore_state"] == actual["authorizer_restore_state"] == "returned"
        assert actual["vm_reported_quanta"] % 1000 == 0
        for group in ("data", "metadata"):
            values = [(row, encoded) for current, kind, row, encoded in ledger.encoded if (current, kind) == (role, group)]
            assert actual[group + "_rows"] == len(values)
            assert actual[group + "_typed_bytes"] == sum(len(encoded) for _, encoded in values)
        con = ledger.source_con if role == "source" else next(con for con in ledger.connections if ledger.connection_role[id(con)] == role)
        for category, target in ((sqlite3.SQLITE_LIMIT_LENGTH, 1048576), (sqlite3.SQLITE_LIMIT_SQL_LENGTH, 32768)):
            calls = [(value, previous) for selected, actual_category, value, previous in ledger.limit_calls
                     if selected is con and actual_category == category]
            assert len(calls) == 2 and calls[0][0] == -1
            assert calls[1] == (min(calls[0][1], target), calls[0][1])
        assert [callback for selected, callback, interval in ledger.progress_calls if selected is con and interval == 0] == [None]
    assert evidence["source"]["connection_state"] == "borrowed"
    assert evidence["personal_reference"]["connection_state"] == evidence["receiver_reference"]["connection_state"] == "closed"
    assert len(ledger.connections) == 2 and not ledger.pending
    for role, expected in (("personal_reference", len(facts._DDL)),
                           ("receiver_reference", len(receiver.TABLES) + len(receiver.INDEXES) + len(receiver.TRIGGERS))):
        actual = [(cursor, sql) for cursor, con, sql, parameters in ledger.executions
                  if ledger.connection_role[id(con)] == role and sql.startswith("CREATE ")]
        assert len(actual) == expected
        ddl_cursors = {id(cursor) for cursor, _ in actual}
        assert not any(id(cursor) in ddl_cursors for _, cursor, _, _, _ in ledger.fetches)
    assert_single_closers(ledger)
    assert len(independent_row_bytes(item.projects[0])) != sum(len(value.encode("utf-8")) for value in item.projects[0] if type(value) is str)
    retired_history(chosen, resources, monkeypatch)
    assert item.image.require_active() is None  # Own image is still alive and still sole source-Con owner.


def test_exact_domain_lazy_resources_and_independent_fault_instances(actual_source, monkeypatch):
    item = actual_source
    ledger = Ledger(monkeypatch, item)
    chosen = session(item)
    assert chosen.evidence["phase"] == "new" and ledger.calls["_cursor"] == ledger.calls["_connect_memory"] == 0
    other = deadline.Namespace3Deadline(item.token.absolute_monotonic)
    code_error(lambda: subject.SqlObservationDeadlineLease(item.profile, item.image, deadline_token=other), "INVALID_ARGUMENT")
    code_error(lambda: subject.SqlObservationDeadlineLease(object(), item.image, deadline_token=item.token), "INVALID_ARGUMENT")
    for name in ("callback", "provider", "connection", "max_seconds", "max_rows", "ref_factory"):
        with pytest.raises(TypeError):
            subject.SqlObservationDeadlineLease(item.profile, item.image, deadline_token=item.token, **{name: object()})
    code_error(lambda: subject.SqlRowsDeadlineLease(), "INVALID_ARGUMENT")
    code_error(lambda: subject.SqlReferenceDeadlineLease(), "INVALID_ARGUMENT")
    for klass in (subject.SqlObservationDeadlineLease, subject.SqlRowsDeadlineLease, subject.SqlReferenceDeadlineLease):
        code_error(lambda klass=klass: type("ForbiddenSubclass", (klass,), {}), "INVALID_ARGUMENT")
    assert ledger.calls["_cursor"] == ledger.calls["_connect_memory"] == 0
    # Each invalid input has its own new session, never a reset failed object.
    for arguments in (("not-a-recipe", {}), ("raw_table", {"table": "meta;DROP"}),
                      ("index_info", {"table": PROJECT_TABLE, "index_name": "not-observed"})):
        current = session(item)
        with pytest.raises(subject.SqlObservationDeadlineError) as raised:
            with current:
                before = ledger.calls["_cursor"], ledger.calls["_execute"]
                first = code_error(lambda: current.query(arguments[0], **arguments[1]), "INVALID_ARGUMENT")
                assert (ledger.calls["_cursor"], ledger.calls["_execute"]) == before
        assert raised.value is first
        failed_history(current, first, monkeypatch)


@pytest.mark.parametrize("kind", ["cursor", "memory"], ids=["actual-cursor", "actual-memory"])
def test_actual_legal_resource_return_postexpiry_registers_and_closes_once(actual_source, monkeypatch, kind):
    item = actual_source
    ledger = Ledger(monkeypatch, item)
    chosen = session(item)
    scalar = Scalar(subject._monotonic())
    name = "_cursor" if kind == "cursor" else "_connect_memory"
    original = getattr(subject, name)
    def expired_after_actual(*args, **kwargs):
        resource = original(*args, **kwargs)
        scalar.value = item.token.absolute_monotonic
        return resource
    with pytest.raises(subject.SqlObservationDeadlineError) as raised:
        with chosen:
            ledger.connecting_role = "personal_reference"
            selected = chosen.query("migration_version") if kind == "cursor" else chosen.personal_reference()
            monkeypatch.setattr(subject, "_monotonic", scalar)
            monkeypatch.setattr(subject, name, expired_after_actual)
            first = code_error(selected.__enter__, "DEADLINE")
            same_error(selected.__enter__, first)
    assert raised.value is first
    evidence = failed_history(chosen, first, monkeypatch)
    assert ledger.calls["_execute"] == ledger.calls["_fetchone"] == ledger.calls["_fetchmany"] == 0
    assert_single_closers(ledger)
    if kind == "cursor":
        assert len(ledger.cursors) == 1 and not ledger.connections
        assert evidence["source"]["cursor_states"] == ("closed",)
    else:
        assert len(ledger.connections) == 1 and not ledger.cursors
        assert evidence["personal_reference"]["connection_state"] == "closed"


@pytest.mark.parametrize("stage", ["fetched", "encoded"], ids=["actual-batch-return", "actual-encoded-return"])
def test_actual_rows_or_encoding_return_postexpiry_preserves_partial_fees(actual_source, monkeypatch, stage):
    item = actual_source
    ledger = Ledger(monkeypatch, item)
    chosen = session(item)
    scalar = Scalar(subject._monotonic())
    name = "_fetchmany" if stage == "fetched" else "_canonical_bytes"
    original = getattr(subject, name)
    def expired_after_actual(*args, **kwargs):
        returned = original(*args, **kwargs)
        scalar.value = item.token.absolute_monotonic
        return returned
    with pytest.raises(subject.SqlObservationDeadlineError) as raised:
        with chosen:
            selected = chosen.query("personal_meta")
            with selected:
                monkeypatch.setattr(subject, "_monotonic", scalar)
                monkeypatch.setattr(subject, name, expired_after_actual)
                first = code_error(lambda: selected.fetchmany(64), "DEADLINE")
                same_error(selected.fetchone, first)
    assert raised.value is first
    evidence = failed_history(chosen, first, monkeypatch)
    role = evidence["source"]
    assert role["metadata_rows"] == selected.evidence["rows_observed"] == len(item.meta)
    assert role["fee_complete"] is False and selected.evidence["eof_observed"] is False
    assert role["metadata_typed_bytes"] == sum(len(value) for current, group, _, value in ledger.encoded if (current, group) == ("source", "metadata"))
    assert len(ledger.encoded) == (0 if stage == "fetched" else 1)
    assert role["cursor_states"] == ("closed",)
    assert_single_closers(ledger)


def test_oversized_legal_tuple_projection_records_observed_rows_without_native_growth_claim(actual_source, monkeypatch):
    item = actual_source
    ledger = Ledger(monkeypatch, item)
    chosen = session(item)
    original = subject._fetchmany
    projected = []
    def growth(cursor, size):
        actual = original(cursor, size)
        assert type(actual) is list and len(actual) == 1 and type(actual[0]) is tuple
        value = actual + [actual[0]]  # Explicit new-alias projection, not two native SQLite rows.
        projected.append(value)
        return value
    with pytest.raises(subject.SqlObservationDeadlineError) as raised:
        with chosen:
            selected = chosen.query("raw_table", table=PROJECT_TABLE)
            with selected:
                monkeypatch.setattr(subject, "_fetchmany", growth)
                first = code_error(lambda: selected.fetchmany(1), "INVALID_RESULT")
                same_error(selected.fetchone, first)
    assert raised.value is first and len(projected) == 1
    evidence = failed_history(chosen, first, monkeypatch)
    assert len(ledger.fetches) == 1 and len(ledger.fetches[0][3]) == 1  # Actual forwarded native observation.
    assert selected.evidence["rows_observed"] == evidence["source"]["data_rows"] == 2
    assert evidence["source"]["fee_complete"] is False and evidence["source"]["data_typed_bytes"] == 0
    assert not ledger.encoded
    assert_single_closers(ledger)


def test_actual_sqlite_progress_callback_original_first_precedes_sqlite_error(actual_source, monkeypatch):
    item = actual_source
    ledger = Ledger(monkeypatch, item)
    chosen = session(item)
    first = OSError("r831 original actual progress callback first")
    original_clock = subject._monotonic
    original_install = subject._set_progress_handler
    original_execute = subject._execute
    armed = False
    callback_calls, sqlite_errors = [], []
    def clock():
        if armed:
            raise first
        return original_clock()
    def install(con, callback, interval):
        if callback is None:
            return original_install(con, callback, interval)
        def actual_callback():
            nonlocal armed
            callback_calls.append(con)
            armed = True
            try:
                returned = callback()  # Original fixed callback owns first capture, then returns 1.
                assert returned == 1
                return returned
            finally:
                armed = False
        return original_install(con, actual_callback, interval)
    def execute(*args, **kwargs):
        try:
            return original_execute(*args, **kwargs)
        except sqlite3.Error as failure:
            sqlite_errors.append(failure)
            raise
    monkeypatch.setattr(subject, "_monotonic", clock)
    monkeypatch.setattr(subject, "_set_progress_handler", install)
    monkeypatch.setattr(subject, "_execute", execute)
    with pytest.raises(OSError) as raised:
        with chosen:
            with chosen.query("integrity") as rows:
                drain(rows, 2)
    assert raised.value is first and callback_calls and sqlite_errors
    assert all(con is ledger.source_con for con in callback_calls)
    failed_history(chosen, first, monkeypatch)
    assert_single_closers(ledger)


def test_body_getter_trap_primary_still_observes_primitive_cursor_and_memory_closed(actual_source, monkeypatch):
    item = actual_source
    ledger = Ledger(monkeypatch, item)
    chosen = session(item)
    class GetterTrap(OSError):
        @property
        def __traceback__(self):
            pytest.fail("primary traceback getter was read")
        @property
        def with_traceback(self):
            pytest.fail("primary with_traceback getter was read")
    first = GetterTrap("r831 original body primary")
    with pytest.raises(GetterTrap) as raised:
        with chosen:
            ledger.connecting_role = "personal_reference"
            reference = chosen.personal_reference()
            with reference:
                rows = reference.query("table_xinfo", table=PROJECT_TABLE)
                with rows:
                    assert type(rows.fetchone()) is tuple
                    raise first
    assert raised.value is first
    evidence = failed_history(chosen, first, monkeypatch)
    assert rows.evidence["cursor_state"] == "closed" and rows.evidence["normal_exit"] is False
    assert evidence["personal_reference"]["connection_state"] == "closed"
    assert evidence["personal_reference"]["normal_exit"] is False
    assert_single_closers(ledger)  # Primitive legal None is CLOSED even with primary, unlike r825 delegated exit.


@pytest.mark.parametrize("kind", ["cursor", "memory"], ids=["primitive-cursor", "primitive-memory"])
def test_actual_primitive_close_then_outer_throw_is_unknown_and_never_retried(actual_source, monkeypatch, kind):
    item = actual_source
    ledger = Ledger(monkeypatch, item)
    chosen = session(item)
    name = "_cursor_close" if kind == "cursor" else "_con_close"
    original = getattr(subject, name)
    first = OSError("r831 alias after actual primitive close")
    def closed_then_throw(*args, **kwargs):
        assert original(*args, **kwargs) is None
        raise first
    with pytest.raises(OSError) as raised:
        with chosen:
            if kind == "cursor":
                selected = chosen.query("migration_version")
                with selected:
                    assert drain(selected) == (("17",),)
                    monkeypatch.setattr(subject, name, closed_then_throw)
            else:
                ledger.connecting_role = "personal_reference"
                selected = chosen.personal_reference()
                with selected:
                    monkeypatch.setattr(subject, name, closed_then_throw)
    assert raised.value is first
    evidence = failed_history(chosen, first, monkeypatch)
    assert_single_closers(ledger)  # Actual primitive return is recorded, outer alias did not return.
    if kind == "cursor":
        assert selected.evidence["cursor_state"] == "unknown"
        assert evidence["source"]["cursor_states"] == ("unknown",)
    else:
        assert evidence["personal_reference"]["connection_state"] == "unknown"
        assert all(state == "closed" for state in evidence["personal_reference"]["cursor_states"])


def test_active_eof_repeated_fetch_caught_first_prevents_success_before_physical_call(actual_source, monkeypatch):
    item = actual_source
    ledger = Ledger(monkeypatch, item)
    chosen = session(item)
    with pytest.raises(subject.SqlObservationDeadlineError) as raised:
        with chosen:
            selected = chosen.query("foreign_keys_check")
            with selected:
                assert selected.fetchone() is None  # Actual EOF, scope/Cursor still ACTIVE.
                assert selected.evidence["phase"] == "active" and selected.evidence["cursor_state"] == "owned"
                before = ledger.calls["_fetchone"], ledger.calls["_fetchmany"]
                first = code_error(selected.fetchone, "CLOSED")
                same_error(selected.fetchmany, first)
                assert (ledger.calls["_fetchone"], ledger.calls["_fetchmany"]) == before
    assert raised.value is first
    evidence = failed_history(chosen, first, monkeypatch)
    assert selected.evidence["eof_observed"] is True and selected.evidence["rows_observed"] == 0
    assert selected.evidence["normal_exit"] is False and evidence["source"]["metadata_rows"] == 0
    assert_single_closers(ledger)


def test_cross_role_index_provenance_never_executes_unobserved_reference_index(actual_source, monkeypatch):
    item = actual_source
    ledger = Ledger(monkeypatch, item)
    chosen = session(item)
    with pytest.raises(subject.SqlObservationDeadlineError) as raised:
        with chosen:
            indexes, _ = collect(chosen, "index_list", table=PROJECT_TABLE)
            assert indexes
            ledger.connecting_role = "personal_reference"
            selected = chosen.personal_reference()
            with selected:
                before = ledger.calls["_cursor"], ledger.calls["_execute"]
                first = code_error(lambda: selected.query("index_info", table=PROJECT_TABLE,
                                                         index_name=indexes[0][1]), "INVALID_ARGUMENT")
                assert (ledger.calls["_cursor"], ledger.calls["_execute"]) == before
    assert raised.value is first
    failed_history(chosen, first, monkeypatch)
    assert_single_closers(ledger)


@pytest.mark.parametrize("kind", ["progress", "authorizer"],
                         ids=["progress-prestart", "authorizer-prestart"])
def test_native_setter_prestart_deadline_rejection_keeps_unstarted_restore_empty(actual_source, monkeypatch, kind):
    """Arm after an actual prerequisite return, allow its postclock, then reject."""
    item = actual_source
    ledger = Ledger(monkeypatch, item)
    chosen = session(item)
    original_clock = subject._monotonic
    original_limit = subject._setlimit
    original_policy = subject._projection_authorizer
    armed = False
    gates, prerequisites = [], []

    def clock():
        if not armed:
            return original_clock()
        gates.append("postcheck" if not gates else "expired")
        return original_clock() if len(gates) == 1 else item.token.absolute_monotonic

    def limit(con, category, value):
        nonlocal armed
        result = original_limit(con, category, value)
        if kind == "progress" and category == sqlite3.SQLITE_LIMIT_SQL_LENGTH and value >= 0:
            assert con is ledger.source_con and type(result) is int and not armed
            prerequisites.append(("actual-final-limit-return", con, category, value, result))
            armed = True
        return result

    def policy(*args, **kwargs):
        nonlocal armed
        result = original_policy(*args, **kwargs)
        if kind == "authorizer":
            assert callable(result) and not armed
            prerequisites.append(("actual-policy-factory-return", kwargs, result))
            armed = True
        return result

    monkeypatch.setattr(subject, "_monotonic", clock)
    monkeypatch.setattr(subject, "_setlimit", limit)
    monkeypatch.setattr(subject, "_projection_authorizer", policy)
    first = code_error(chosen.__enter__, "DEADLINE")
    evidence = failed_history(chosen, first, monkeypatch)
    source = evidence["source"]
    assert len(prerequisites) == 1 and gates[:2] == ["postcheck", "expired"]
    assert source["entered"] is False and source["normal_exit"] is False
    assert ledger.calls["_cursor"] == ledger.calls["_execute"] == 0
    assert ledger.calls["_fetchone"] == ledger.calls["_fetchmany"] == 0
    assert ledger.calls["_set_authorizer"] == ledger.returns["_set_authorizer"] == 0
    assert not ledger.authorizer_calls and source["authorizer_restore_state"] == "empty"
    assert_single_closers(ledger)
    if kind == "progress":
        assert ledger.calls["_set_progress_handler"] == ledger.returns["_set_progress_handler"] == 0
        assert not ledger.progress_calls and source["progress_restore_state"] == "empty"
        assert source["native_call_starts"] == source["native_call_completions"] == 6
        assert source["cleanup_call_starts"] == 0
    else:
        assert ledger.calls["_set_progress_handler"] == ledger.returns["_set_progress_handler"] == 2
        installed, restored = ledger.progress_calls
        assert installed[0] is restored[0] is ledger.source_con and callable(installed[1])
        assert installed[2] == 1000 and restored[1:] == (None, 0)
        assert source["progress_restore_state"] == "returned"
        assert source["native_call_starts"] == source["native_call_completions"] == 7
        assert source["cleanup_call_starts"] == 1


@pytest.mark.parametrize("kind", ["progress", "authorizer"],
                         ids=["progress-started-fault", "authorizer-started-fault"])
def test_actual_setter_return_then_outer_fault_retains_required_single_restore(actual_source, monkeypatch, kind):
    """Actual primitive None precedes the outer fault; cleanup must still restore."""
    item = actual_source
    ledger = Ledger(monkeypatch, item)
    chosen = session(item)
    name = "_set_progress_handler" if kind == "progress" else "_set_authorizer"
    original = getattr(subject, name)
    first = OSError("r831 actual " + kind + " setter returned before outer first")
    started = []

    def actual_then_fault(*args, **kwargs):
        result = original(*args, **kwargs)
        assert result is None and args[0] is ledger.source_con
        if not started:
            assert callable(args[1])
            started.append((args, kwargs, result))
            raise first
        return result

    monkeypatch.setattr(subject, name, actual_then_fault)
    same_error(chosen.__enter__, first)
    evidence = failed_history(chosen, first, monkeypatch)
    source = evidence["source"]
    assert len(started) == 1 and source["entered"] is False and source["normal_exit"] is False
    assert ledger.calls[name] == ledger.returns[name] == 2
    assert ledger.calls["_cursor"] == ledger.calls["_execute"] == 0
    assert ledger.calls["_fetchone"] == ledger.calls["_fetchmany"] == 0
    assert_single_closers(ledger)
    installed, restored = ledger.progress_calls
    assert installed[0] is restored[0] is ledger.source_con and callable(installed[1])
    assert installed[2] == 1000 and restored[1:] == (None, 0)
    assert source["progress_restore_state"] == "returned"
    if kind == "progress":
        assert ledger.calls["_set_authorizer"] == ledger.returns["_set_authorizer"] == 0
        assert not ledger.authorizer_calls and source["authorizer_restore_state"] == "empty"
        assert source["native_call_starts"] == 7 and source["native_call_completions"] == 6
        assert source["cleanup_call_starts"] == 1
    else:
        installed, restored = ledger.authorizer_calls
        assert installed[0] is restored[0] is ledger.source_con
        assert callable(installed[1]) and callable(restored[1]) and installed[1] is not restored[1]
        assert source["authorizer_restore_state"] == "returned"
        assert source["native_call_starts"] == 8 and source["native_call_completions"] == 7
        assert source["cleanup_call_starts"] == 2
