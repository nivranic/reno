"""Compare committed-image references to the dedicated GC SQL writer."""
import json
from pathlib import Path
import sqlite3

import pytest

from reno import config, storage_roots as roots
from tests.test_storage_roots_group import libraries, enroll, registered, scan  # noqa: F401


def test_image_projection_matches_writer_and_restores_connection(libraries, monkeypatch):
    env = libraries()
    enroll(env)
    registered(env)
    baseline = scan(env).owner_sql_sha256
    con = env["con"]
    before_factory = con.row_factory
    before_length = con.getlimit(sqlite3.SQLITE_LIMIT_LENGTH)
    con.execute("BEGIN IMMEDIATE")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Owner SQL proof must use only the supplied connection")

    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(config, "get", forbidden)
    monkeypatch.setattr(config, "db_path", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    assert roots.owner_sql_sha256(con) == baseline
    assert con.in_transaction and con.row_factory is before_factory
    assert con.getlimit(sqlite3.SQLITE_LIMIT_LENGTH) == before_length
    # The proof must not leave a read-only authorizer on the GC writer.
    con.execute("UPDATE meta SET value='1' WHERE key='kb_revision'")
    con.rollback()
    assert roots.owner_sql_sha256(con) == baseline


@pytest.mark.parametrize("sql,args", [
    ("INSERT INTO video_asset(video_id,files_json) VALUES('v',?)", ('{"poster":"media/private.mp4"}',)),
    ("INSERT INTO frame(video_id,frame_id,path) VALUES('v','f','media/frame.jpg')", ()),
    ("INSERT INTO job(video_id,kind,status,payload_json) VALUES('v','process','pending','{}')", ()),
    ("INSERT INTO sync_queue(video_id,status,url) VALUES('v','queued','https://example.invalid')", ()),
    ("INSERT INTO sync_recovery_control(slot,state,generation,revision,updated_at) VALUES(1,'held',1,1,'now')", ()),
    ("UPDATE meta SET value='2' WHERE key='library_epoch'", ()),
])
def test_reference_changes_inside_transaction_do_not_match_old_image(libraries, sql, args):
    env = libraries()
    enroll(env)
    before = scan(env).owner_sql_sha256
    con = env["con"]
    con.execute("BEGIN IMMEDIATE")
    con.execute(sql, args)
    assert roots.owner_sql_sha256(con) != before
    assert con.in_transaction
    con.rollback()
    assert roots.owner_sql_sha256(con) == before


def test_lease_display_and_unrelated_metadata_do_not_change_projection(libraries):
    env = libraries()
    con = env["con"]
    con.execute("INSERT INTO job(video_id,kind,status,payload_json) VALUES('v','process','pending','{}')")
    con.commit()
    before = roots.owner_sql_sha256(con)
    con.execute("UPDATE job SET lease_until='heartbeat',updated_at='display',result_json=?", (json.dumps({"text": "private result"}),))
    con.execute("INSERT INTO meta(key,value) VALUES('unrelated_display','private display')")
    con.commit()
    assert roots.owner_sql_sha256(con) == before


def test_sql_blob_is_bound_without_executing_or_revealing_it(libraries):
    env = libraries()
    con = env["con"]
    con.execute("INSERT INTO video_asset(video_id,files_json) VALUES('v',?)", (b"bad JSON private bytes",))
    con.commit()
    before = roots.owner_sql_sha256(con)
    con.execute("UPDATE video_asset SET files_json=?", (b"new bad JSON private bytes",))
    con.commit()
    assert roots.owner_sql_sha256(con) != before


def test_unsafe_view_is_bound_but_never_executed(libraries):
    env = libraries()
    con = env["con"]
    con.execute("DROP TABLE frame")
    con.execute("CREATE VIEW frame AS SELECT dangerous() AS video_id, 'f' AS frame_id, 'p' AS path")
    con.commit()
    calls = []
    con.create_function("dangerous", 0, lambda: calls.append(True))
    first = roots.owner_sql_sha256(con)
    assert not calls
    con.execute("DROP VIEW frame")
    con.execute("CREATE VIEW frame AS SELECT dangerous() AS video_id, 'f2' AS frame_id, 'p' AS path")
    con.commit()
    assert roots.owner_sql_sha256(con) != first and not calls


@pytest.mark.parametrize("ddl", [
    "CREATE INDEX new_video_reference_index ON video_asset(video_id)",
    "CREATE TRIGGER new_gc_trigger AFTER INSERT ON storage_gc_operation BEGIN DELETE FROM video_asset; END",
])
def test_auxiliary_ddl_after_image_capture_changes_writer_projection(libraries, ddl):
    env = libraries()
    enroll(env)
    registered(env)
    baseline = scan(env).owner_sql_sha256
    con = env["con"]
    con.execute(ddl)
    con.commit()
    con.execute("BEGIN IMMEDIATE")
    assert roots.owner_sql_sha256(con) != baseline and con.in_transaction
    con.rollback()


@pytest.mark.parametrize("limits", [
    roots.RootLimits(max_rows=1),
    roots.RootLimits(max_cell_bytes=16),
    roots.RootLimits(max_metadata_bytes=1),
])
def test_exhausted_sql_budget_restores_writer_without_committing(libraries, limits):
    con = libraries()["con"]
    factory = con.row_factory
    length = con.getlimit(sqlite3.SQLITE_LIMIT_LENGTH)
    con.execute("BEGIN IMMEDIATE")
    with pytest.raises(roots.RootError):
        roots.owner_sql_sha256(con, limits=limits)
    assert con.in_transaction and con.row_factory is factory
    assert con.getlimit(sqlite3.SQLITE_LIMIT_LENGTH) == length
    con.execute("INSERT INTO meta(key,value) VALUES('after_budget','yes')")
    con.rollback()


def test_cancellation_keeps_original_exception_and_writer_transaction(libraries):
    con = libraries()["con"]
    marker = RuntimeError("original caller cancellation")
    count = 0

    def cancelled():
        nonlocal count
        count += 1
        if count == 4:
            raise marker

    con.execute("BEGIN IMMEDIATE")
    with pytest.raises(RuntimeError) as error:
        roots.owner_sql_sha256(con, check_cancelled=cancelled)
    assert error.value is marker and con.in_transaction
    con.execute("INSERT INTO meta(key,value) VALUES('after_cancel','yes')")
    con.rollback()


def test_vm_and_deadline_limits_leave_connection_usable(libraries, monkeypatch):
    con = libraries()["con"]
    con.executemany("INSERT INTO video_asset(video_id,files_json) VALUES(?, '{}')", ((str(i),) for i in range(1000)))
    con.commit()
    con.execute("BEGIN IMMEDIATE")
    with pytest.raises(roots.RootError, match="ROOTS_QUERY_LIMIT"):
        roots.owner_sql_sha256(con, limits=roots.RootLimits(max_vm_steps=1))
    assert con.in_transaction
    ticks = iter((0.0, 2.0))
    monkeypatch.setattr(roots.time, "monotonic", lambda: next(ticks))
    with pytest.raises(roots.RootError, match="ROOTS_DEADLINE_LIMIT"):
        roots.owner_sql_sha256(con, limits=roots.RootLimits(deadline_seconds=1))
    assert con.in_transaction
    con.execute("INSERT INTO meta(key,value) VALUES('after_limit','yes')")
    con.rollback()


def test_cleanup_failure_keeps_original_cancel_and_restores_other_hooks():
    class FailingReset(sqlite3.Connection):
        def set_progress_handler(self, handler, count):
            if handler is None:
                raise OSError("secondary reset error")
            return super().set_progress_handler(handler, count)

    con = sqlite3.connect(":memory:", factory=FailingReset)
    con.row_factory = sqlite3.Row
    factory, length = con.row_factory, con.getlimit(sqlite3.SQLITE_LIMIT_LENGTH)
    marker = RuntimeError("original callback")

    def cancelled():
        raise marker

    try:
        with pytest.raises(RuntimeError) as error:
            roots.owner_sql_sha256(con, check_cancelled=cancelled)
        assert error.value is marker
        assert "ROOTS_OWNER_SQL_CLEANUP_FAILED" in marker.__notes__
        assert con.row_factory is factory and con.getlimit(sqlite3.SQLITE_LIMIT_LENGTH) == length
        con.execute("CREATE TABLE after_cancel(value TEXT)")
    finally:
        con.close()


def test_gc_staging_recovery_bytes_are_never_inventory_candidates(libraries):
    env = libraries()
    enroll(env)
    _, artifact = registered(env, namespace="local_report")
    staged = env["profile"].reports / ".reno-gc-stage-v1" / "old-operation" / "private-staged.md"
    staged.parent.mkdir(parents=True)
    staged.write_bytes(b"frozen operation recovery bytes")
    result = scan(env)
    assert result.eligible_artifacts == ({key: artifact[key] for key in roots._ARTIFACT_FIELDS},)
    report_counts = [count for (namespace, _root), count in result.unknown_counts.items() if namespace == "local_report"]
    assert report_counts == [0] and staged.read_bytes() == b"frozen operation recovery bytes"
