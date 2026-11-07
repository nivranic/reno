"""Report file lifetimes and publication failures on explicit artificial roots."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import uuid

import pytest

from reno import config, db, llm, report, storage_artifacts, sync_reports
from reno import storage_lifecycle as lifecycle
from reno.sync_protocol import SyncError


def make_profile(root):
    return lifecycle.StorageProfile(root / "database/reno.db", root / "media",
                                    root / "report-files", root / "archives")


@pytest.fixture()
def store(tmp_path):
    value = make_profile(tmp_path)
    db.initialize(value.database)
    lifecycle.initialize(value)
    config.set_local({"node_role": "all"})
    return value


def connect(store):
    return db.connect(store.database)


def manifest(store, name="checklist"):
    return json.loads((store.reports / f"{name}.manifest.json").read_text(encoding="utf-8"))


def artifact_rows(store):
    con = connect(store)
    try:
        return [dict(row) for row in con.execute("SELECT * FROM storage_artifact ORDER BY storage_key")]
    finally:
        con.close()


def exclusive_available(store):
    payload = json.dumps({name: str(getattr(store, name)) for name in
                          ("database", "media", "reports", "checkpoints")})
    code = """
import json, sys
from reno import storage_lifecycle as lifecycle
value = lifecycle.StorageProfile(**json.loads(sys.argv[1]))
try:
    with lifecycle.exclusive(value, timeout=0):
        pass
except lifecycle.StorageLifecycleError as exc:
    if exc.code != 'STORAGE_BUSY':
        raise
    sys.exit(7)
"""
    result = subprocess.run([sys.executable, "-c", code, payload],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert result.returncode in (0, 7), result.stderr.decode("utf-8", "replace")
    return result.returncode == 0


def entries(con, content="# 人工同步报告\n"):
    version = db.knowledge_version(con)
    entry = {"name": "checklist", "content": content,
             "sha256": hashlib.sha256(content.encode()).hexdigest(), "policy_version": report.POLICY_VERSION,
             "version": version, "generated_at": "2026-10-01T00:00:00.000Z",
             "review_events_sha256": sync_reports.event_fingerprint(con)}
    return sync_reports.validate_reports([entry], version)


def publication_row(con, publication):
    version = db.knowledge_version(con)
    con.execute("INSERT INTO sync_publication VALUES(?,?,?,?,?,?,?,?,?)",
                (publication, version["library_id"], "synthetic-node", 1, 0, "a" * 64, "b" * 64,
                 "2026-10-01T00:00:00.000Z", json.dumps(version)))


def test_missing_read_facilities_never_initialize_or_open_the_database(tmp_path, monkeypatch):
    value = make_profile(tmp_path / "missing")
    monkeypatch.setattr(db, "connect", lambda *a, **k: pytest.fail("Missing facilities must fail before DB open"))
    for operation in (lambda: report.read_report("checklist", profile=value),
                      lambda: report.present_report("checklist", profile=value),
                      lambda: report.metadata(profile=value),
                      lambda: report._manifest("checklist", profile=value)):
        with pytest.raises(lifecycle.StorageLifecycleError) as caught:
            operation()
        assert caught.value.code == "STORAGE_NOT_INITIALIZED"
    assert not value.database.parent.parent.exists()


def test_top_level_read_holds_guard_before_sql_and_until_content_is_materialized(store, monkeypatch):
    report.checklist_report(profile=store)
    original_connect, original_read = db.connect, report._read_report_content
    observed = []
    def protected_connect(path=None):
        assert path == store.database
        assert lifecycle.require_guard(store)
        con = original_connect(path)
        con.set_trace_callback(lambda sql: observed.append((sql, lifecycle.current_guard() is not None)))
        return con
    def protected_read(*args, **kwargs):
        assert not exclusive_available(store)
        return original_read(*args, **kwargs)
    monkeypatch.setattr(db, "connect", protected_connect)
    monkeypatch.setattr(report, "_read_report_content", protected_read)
    content = report.present_report("checklist", profile=store)
    assert isinstance(content, str) and "Checklist" in content
    assert any(sql == "BEGIN" for sql, _ in observed)
    assert all(held for _, held in observed)
    assert lifecycle.current_guard() is None and exclusive_available(store)


def test_supplied_connection_requires_existing_guard_without_touching_its_transaction(store):
    con = connect(store)
    try:
        con.execute("BEGIN")
        for operation in (lambda: report.read_report("checklist", con=con, profile=store),
                          lambda: report.present_report("checklist", con=con, profile=store),
                          lambda: report.metadata(con=con, profile=store),
                          lambda: sync_reports.manifest(con, "checklist", profile=store),
                          lambda: sync_reports.export_reports(con, profile=store)):
            with pytest.raises(lifecycle.StorageLifecycleError) as caught:
                operation()
            assert caught.value.code == "STORAGE_GUARD_REQUIRED" and con.in_transaction
    finally:
        con.close()


def test_active_custom_profile_is_reused_and_other_database_is_rejected(store, tmp_path):
    report.checklist_report(profile=store)
    other = make_profile(tmp_path / "other")
    db.initialize(other.database)
    con = connect(store)
    wrong = connect(other)
    try:
        with lifecycle.shared(store):
            con.execute("BEGIN")
            assert report.read_report("checklist", con=con)
            assert report.metadata(con=con)["checklist"]["has_content"]
            with pytest.raises(lifecycle.StorageLifecycleError) as caught:
                report.read_report("checklist", con=wrong)
            assert caught.value.code == "STORAGE_PROFILE_MISMATCH"
    finally:
        con.close()
        wrong.close()


def test_summary_does_not_hold_lifecycle_or_sql_transaction_during_model_call(store, monkeypatch):
    def model(*args, **kwargs):
        assert lifecycle.current_guard() is None
        assert exclusive_available(store)
        con = connect(store)
        try:
            con.execute("BEGIN EXCLUSIVE")
            con.rollback()
        finally:
            con.close()
        return "## 合成总结\n仅用于隔离验证", "synthetic"
    monkeypatch.setattr(llm, "chat", model)
    report.summary_report(profile=store)
    assert "合成总结" in report.read_report("summary", profile=store)
    assert [row["namespace"] for row in artifact_rows(store)] == ["local_report"]


def test_local_file_and_manifest_publication_are_protected_and_registered(store, monkeypatch):
    original = report._atomic_write
    observed = []
    def write(path, content):
        assert lifecycle.require_guard(store)
        if path.parent.name == ".versions":
            assert not exclusive_available(store)
        observed.append(path.name)
        return original(path, content)
    monkeypatch.setattr(report, "_atomic_write", write)
    report.checklist_report(profile=store)
    row, = artifact_rows(store)
    value = manifest(store)
    assert row["namespace"] == "local_report" and row["storage_key"] == value["content_file"]
    assert row["sha256"] == value["content_sha256"]
    assert row["byte_size"] == len(report.read_report("checklist", profile=store).encode())
    assert "checklist.manifest.json" in observed and "checklist.md" in observed


@pytest.mark.parametrize("stage", ["register", "commit", "manifest"])
def test_publish_failure_keeps_old_pointer_and_preserves_new_orphan(store, monkeypatch, stage):
    report.checklist_report(profile=store)
    old = manifest(store)
    previous_files = set((store.reports / ".versions").iterdir())
    previous_rows = artifact_rows(store)
    if stage == "register":
        def fail(*args, **kwargs):
            raise SyncError("synthetic registration failure")
        monkeypatch.setattr(storage_artifacts, "register", fail)
    elif stage == "commit":
        real_connect = db.connect
        class Connection:
            def __init__(self, con): self.con = con
            def __getattr__(self, key): return getattr(self.con, key)
            def commit(self): raise sqlite3.OperationalError("synthetic commit failure")
        monkeypatch.setattr(db, "connect", lambda *a, **k: Connection(real_connect(*a, **k)))
    else:
        write = report._atomic_write
        def fail(path, content):
            if path.name == "checklist.manifest.json" and json.loads(content).get("status") == "latest":
                raise OSError("synthetic manifest failure before replace")
            return write(path, content)
        monkeypatch.setattr(report, "_atomic_write", fail)
    with pytest.raises((SyncError, OSError, sqlite3.OperationalError)):
        report.checklist_report(profile=store)
    current = manifest(store)
    assert current["content_file"] == old["content_file"] and current["content_sha256"] == old["content_sha256"]
    assert current["status"] == "failed" and report.read_report("checklist", profile=store)
    remaining = set((store.reports / ".versions").iterdir())
    assert previous_files < remaining and len(remaining - previous_files) == 1
    rows = artifact_rows(store)
    assert len(rows) == len(previous_rows) + (stage == "manifest")


def test_sync_file_helpers_require_guard_and_matching_root_without_side_effects(store, tmp_path):
    unknown = tmp_path / "not-the-profile-root"
    for operation in (lambda: sync_reports.prepare_files(unknown, [], profile=store),
                      lambda: sync_reports.read_content(unknown, {}, profile=store)):
        with pytest.raises(lifecycle.StorageLifecycleError) as caught:
            operation()
        assert caught.value.code == "STORAGE_GUARD_REQUIRED"
    with lifecycle.shared(store):
        with pytest.raises(lifecycle.StorageLifecycleError) as caught:
            sync_reports.prepare_files(unknown, [], profile=store)
        assert caught.value.code == "STORAGE_PROFILE_MISMATCH"
    assert not unknown.exists()


def test_sync_artifact_registration_and_pointer_share_callers_transaction(store):
    con = connect(store)
    publication = str(uuid.uuid4())
    try:
        with lifecycle.shared(store):
            con.execute("BEGIN IMMEDIATE")
            publication_row(con, publication)
            values = entries(con)
            prepared = sync_reports.prepare_files(store.reports, values, profile=store)
            sync_reports.install_pointers(con, prepared, publication, db.knowledge_version(con), profile=store)
            assert con.in_transaction
            assert con.execute("SELECT count(*) FROM sync_report").fetchone()[0] == 1
            assert con.execute("SELECT count(*) FROM storage_artifact").fetchone()[0] == 1
            pointer = sync_reports.manifest(con, "checklist", profile=store)
            con.rollback()
            assert con.execute("SELECT count(*) FROM sync_report").fetchone()[0] == 0
            assert con.execute("SELECT count(*) FROM storage_artifact").fetchone()[0] == 0
            assert sync_reports.read_content(store.reports, pointer, profile=store) == values[0]["content"]
            assert (store.reports / prepared[0]["storage_key"]).exists()
            con.execute("BEGIN IMMEDIATE")
            publication_row(con, publication)
            sync_reports.install_pointers(con, prepared, publication, db.knowledge_version(con), profile=store)
            con.commit()
        assert len(artifact_rows(store)) == 1
    finally:
        con.close()


def test_failed_sync_pointer_rolls_back_its_artifact_but_preserves_callers_earlier_writes(store):
    con = connect(store)
    try:
        con.execute("CREATE TRIGGER deny_report BEFORE INSERT ON sync_report BEGIN SELECT RAISE(ABORT,'synthetic'); END")
        con.commit()
        with lifecycle.shared(store):
            con.execute("BEGIN IMMEDIATE")
            con.execute("INSERT INTO meta(key,value) VALUES('synthetic_previous','preserve')")
            prepared = sync_reports.prepare_files(store.reports, entries(con), profile=store)
            with pytest.raises(sqlite3.IntegrityError):
                sync_reports.install_pointers(con, prepared, str(uuid.uuid4()), db.knowledge_version(con), profile=store)
            assert con.in_transaction
            assert con.execute("SELECT value FROM meta WHERE key='synthetic_previous'").fetchone()[0] == "preserve"
            assert con.execute("SELECT count(*) FROM storage_artifact").fetchone()[0] == 0
            assert con.execute("SELECT count(*) FROM sync_report").fetchone()[0] == 0
            con.rollback()
            assert (store.reports / prepared[0]["storage_key"]).exists()
    finally:
        con.close()


def test_sync_export_reuses_guard_and_returns_verified_immutable_text(store):
    report.checklist_report(profile=store)
    con = connect(store)
    try:
        with lifecycle.shared(store):
            con.execute("BEGIN")
            exported = sync_reports.export_reports(con, profile=store)
            assert len(exported) == 1 and exported[0]["name"] == "checklist"
            assert exported[0]["sha256"] == hashlib.sha256(exported[0]["content"].encode()).hexdigest()
            assert con.in_transaction
            con.rollback()
        value = manifest(store)
        (store.reports / value["content_file"]).write_text("synthetic corruption", encoding="utf-8")
        assert report.read_report("checklist", profile=store) is None
    finally:
        con.close()


def test_unsafe_local_report_file_is_unavailable_without_exposing_a_storage_exception(store, tmp_path):
    report.checklist_report(profile=store)
    value = manifest(store)
    original = store.reports / value["content_file"]
    linked = tmp_path / "external-alias.md"
    expected = original.read_bytes()
    os.link(original, linked)
    assert report.read_report("checklist", profile=store) is None
    assert report.present_report("checklist", profile=store) is None
    assert report.metadata(profile=store)["checklist"]["has_content"] is False
    assert linked.read_bytes() == expected and original.read_bytes() == expected
