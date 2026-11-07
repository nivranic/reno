"""Actual installed producers, owned read windows and original fd results.

No installed head/receipt/body is synthesized. Negative cases deliberately
damage already-produced facts, rather than inventing successful mirror rows.
No model, platform, real library, handoff or sharing is used by this suite.
"""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from copy import deepcopy
import os
import sqlite3

import pytest

from reno import db, personal_mirror_read as reader, personal_mirror_transfer as transfer
from reno import personal_mirror_transfer_contract as wire, personal_mirror_transfer_files as files
from reno import personal_mirror_transfer_schema as schema, storage_lifecycle as lifecycle
from tests.test_personal_mirror_transfer import (mirror_env, make_prepare, arguments,
    commit_selector, BODY, table_state, uid)  # noqa: F401


def install(env, *, attachment=False, previous=None, expected_revision=0):
    prepared = make_prepare(env, with_attachment=attachment, previous=previous, expected_revision=expected_revision)
    selected = commit_selector(prepared)
    with lifecycle.shared(env["profile"]):
        transfer.prepare_mirror_locked(**arguments(env), prepare=prepared)
        for descriptor in wire.attachment_manifest(prepared):
            files.store_attachment_locked(**arguments(env), operation_id=prepared["operation_id"],
                expected_prepare_sha256=selected["prepare_sha256"], attachment_id=descriptor["attachment_id"], chunks=[BODY])
        result = transfer.commit_mirror_locked(**arguments(env), commit=selected)
    return prepared, result


def damage(env, callback):
    with lifecycle.shared(env["profile"]):
        con = db.connect(env["profile"].database)
        try:
            callback(con)
            con.commit()
        finally:
            con.close()


def test_real_install_grants_only_original_owned_read_window_and_preserves_source(mirror_env):
    env = mirror_env
    prepared, _ = install(env)
    before = table_state(env)
    node = (env["data"] / "node-identity.json").read_bytes()
    shm_before = (env["profile"].database.parent / (env["profile"].database.name + "-shm")).exists()
    ordinary = db.connect(env["profile"].database)
    try:
        assert reader.capabilities_for(ordinary) is None
        with reader.open_installed_read(env["profile"]) as con:
            assert con is not ordinary and con.in_transaction
            assert con.execute("PRAGMA query_only").fetchone()[0] == 1
            cap = reader.capabilities_for(con)
            assert cap["personal_read"] and not cap["personal_write"] and not cap["personal_upload"]
            assert cap["instance_id"] == env["receiver"]["node_id"]
            assert cap["personal_primary_id"] == env["source"]["node_id"]
            assert cap["library_id"] == env["receiver"]["library_id"]
            assert cap["personal_mirror"]["installed_revision"] == 1
            assert cap["personal_mirror"]["source_primary_epoch"] == env["primary_epoch"]
            detached = deepcopy(cap)
            assert reader.capabilities_for(ordinary) is None
            assert con.execute("SELECT name FROM renovation_project WHERE project_id=?", (env["project_id"],)).fetchone()
            assert con.execute("SELECT value FROM json_each('[1,2]')").fetchall()
        assert reader.capabilities_for(con) is None
        assert reader.capabilities_for(detached) is None
        with pytest.raises(sqlite3.ProgrammingError):
            con.execute("SELECT 1")
    finally:
        ordinary.close()
    assert table_state(env) == before
    assert (env["data"] / "node-identity.json").read_bytes() == node
    assert (env["profile"].database.parent / (env["profile"].database.name + "-shm")).exists() == shm_before
    assert prepared["snapshot_id"]


def test_prepared_request_without_installed_head_grants_nothing(mirror_env):
    env = mirror_env
    with lifecycle.shared(env["profile"]):
        transfer.prepare_mirror_locked(**arguments(env), prepare=env["prepare"])
    before = table_state(env)
    with pytest.raises(reader.PersonalMirrorReadError) as failure:
        with reader.open_installed_read(env["profile"]):
            pytest.fail("prepared request cannot grant personal reading")
    assert failure.value.code == "PERSONAL_MIRROR_READ_NOT_INSTALLED"
    assert table_state(env) == before


@pytest.mark.parametrize("namespace", ["absent", "partial", "prefix_collision"])
def test_missing_namespace_is_uninstalled_but_partial_or_colliding_schema_is_damaged(mirror_env, namespace):
    root = mirror_env["target_root"] / "fresh-reader"
    data = mirror_env["select"](root, "cloud")
    path = data / "reno.db"
    db.initialize(path)
    profile = lifecycle.StorageProfile(path, root / "media", data / "generated", data / "sync-checkpoints")
    lifecycle.initialize(profile)
    with lifecycle.shared(profile):
        con = db.connect(path)
        try:
            con.execute("BEGIN IMMEDIATE")
            if namespace == "partial":
                schema.install(con, profile=profile)
                con.execute(f'DROP TABLE "{schema.HEAD}"')
            elif namespace == "prefix_collision":
                con.execute("CREATE TABLE personal_mirror_transfer_unknown (value TEXT)")
            con.commit()
            before_schema = [tuple(row) for row in con.execute("SELECT * FROM sqlite_master ORDER BY name")]
            before_meta = [tuple(row) for row in con.execute("SELECT * FROM meta ORDER BY key")]
        finally:
            con.close()
    # An absent namespace must not inspect or recreate an unrelated node file.
    if namespace == "absent":
        (data / "node-identity.json").unlink(missing_ok=True)
    with pytest.raises(reader.PersonalMirrorReadError) as failure:
        with reader.open_installed_read(profile):
            pytest.fail("no complete installed namespace can grant reading")
    expected = "NOT_INSTALLED" if namespace == "absent" else "PROOF_CHANGED"
    assert failure.value.code == "PERSONAL_MIRROR_READ_" + expected
    con = db.connect(path)
    try:
        assert [tuple(row) for row in con.execute("SELECT * FROM sqlite_master ORDER BY name")] == before_schema
        assert [tuple(row) for row in con.execute("SELECT * FROM meta ORDER BY key")] == before_meta
    finally:
        con.close()
    if namespace == "absent":
        assert not (data / "node-identity.json").exists()


@pytest.mark.parametrize("state", ["expired", "absent"])
def test_expired_or_absent_transport_lease_does_not_cancel_installed_reads(mirror_env, monkeypatch, state):
    env = mirror_env
    install(env)
    damage(env, lambda con: con.execute("UPDATE sync_session SET lease_until='2000-01-01T00:00:00.000Z'"
        if state == "expired" else "DELETE FROM sync_session"))
    def forbidden(*args, **kwargs):
        pytest.fail("user reads must not call live transport admission")
    monkeypatch.setattr(transfer, "_admit_receiver_locked", forbidden)
    with reader.open_installed_read(env["profile"]) as con:
        assert reader.capabilities_for(con)["personal_read"]


@pytest.mark.parametrize("case", ["receiver_library", "personal_meta", "project", "source", "head", "receipt"])
def test_actual_installed_chain_meta_and_every_personal_projection_are_required(mirror_env, case):
    env = mirror_env
    prepared, _ = install(env)
    def corrupt(con):
        if case == "receiver_library":
            con.execute("UPDATE meta SET value=? WHERE key='library_id'", (uid(),))
        elif case == "personal_meta":
            con.execute("UPDATE meta SET value='99' WHERE key='primary_epoch'")
        elif case == "project":
            con.execute("UPDATE renovation_project SET name='damaged'")
        elif case == "source":
            con.execute("UPDATE source_record SET title='damaged'")
        elif case == "head":
            con.execute("PRAGMA foreign_keys=OFF")
            con.execute(f'UPDATE "{schema.HEAD}" SET revision=99')
        else:
            # Restore the exact original trigger after negative-only removal;
            # the schema remains canonical and only the real receipt is absent.
            con.execute("PRAGMA foreign_keys=OFF")
            ddl = con.execute("SELECT sql FROM sqlite_master WHERE name='trg_personal_mirror_receipt_delete'").fetchone()[0]
            con.execute("DROP TRIGGER trg_personal_mirror_receipt_delete")
            con.execute(f'DELETE FROM "{schema.RECEIPT}" WHERE operation_id=?', (prepared["operation_id"],))
            con.execute(ddl)
    damage(env, corrupt)
    with pytest.raises(reader.PersonalMirrorReadError):
        with reader.open_installed_read(env["profile"]):
            pytest.fail("changed installed proof cannot grant reading")


def test_actual_receiver_node_identity_is_pinned_and_not_created(mirror_env):
    env = mirror_env
    install(env)
    path = env["data"] / "node-identity.json"
    original = path.read_bytes()
    path.unlink()
    with pytest.raises(reader.PersonalMirrorReadError):
        with reader.open_installed_read(env["profile"]):
            pytest.fail("no node identity cannot grant reading")
    assert not path.exists()
    path.write_bytes(original)
    with reader.open_installed_read(env["profile"]) as con:
        assert reader.capabilities_for(con)["instance_id"] == env["receiver"]["node_id"]


@pytest.mark.parametrize("attack", ["query_only", "write", "temp", "attach", "commit", "rollback", "script"])
def test_caller_cannot_remove_readonly_or_replace_original_transaction(mirror_env, attack):
    env = mirror_env
    install(env)
    before = table_state(env)
    with pytest.raises(reader.PersonalMirrorReadError):
        with reader.open_installed_read(env["profile"]) as con:
            con.set_authorizer(None)
            con.set_progress_handler(None, 0)
            if attack == "query_only":
                con.execute("PRAGMA query_only=OFF")
            elif attack == "write":
                con.execute("UPDATE renovation_project SET name='forged'")
            elif attack == "temp":
                con.execute("CREATE TEMP TABLE renovation_project(name)")
            elif attack == "attach":
                con.execute("ATTACH ':memory:' AS forged")
            elif attack == "commit":
                con.commit()
            elif attack == "rollback":
                con.rollback()
            else:
                con.executescript("COMMIT; BEGIN; SELECT 1;")
    assert table_state(env) == before


def test_native_sqlite_write_still_fails_on_real_mode_ro_private_connection(mirror_env):
    env = mirror_env
    install(env)
    before = table_state(env)
    with pytest.raises(reader.PersonalMirrorReadError):
        with reader.open_installed_read(env["profile"]) as con:
            # Native hook manipulation is outside the Python capability API,
            # but the actual sqlite mode=ro remains a separate physical guard.
            sqlite3.Connection.set_authorizer(con, None)
            con.execute("PRAGMA query_only=OFF")
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                con.execute("UPDATE renovation_project SET name='forged'")
            reader.capabilities_for(con)
    assert table_state(env) == before


def test_copied_context_on_another_thread_cannot_borrow_original_read_capability(mirror_env):
    env = mirror_env
    install(env)
    with reader.open_installed_read(env["profile"]) as con:
        context = copy_context()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(context.run, reader.capabilities_for, con)
            with pytest.raises(reader.PersonalMirrorReadError):
                future.result()
        assert reader.capabilities_for(con)["personal_read"]


def test_read_version_rejects_wrong_token_and_changes_with_real_next_install(mirror_env):
    env = mirror_env
    first, _ = install(env)
    with reader.open_installed_read(env["profile"]) as con:
        version = reader.capabilities_for(con)["personal_read_version"]
    with reader.open_installed_read(env["profile"], expected_read_version=version) as con:
        assert reader.capabilities_for(con)["personal_read_version"] == version
    with pytest.raises(reader.PersonalMirrorReadError) as failure:
        with reader.open_installed_read(env["profile"], expected_read_version="0" * 64):
            pass
    assert failure.value.code == "PERSONAL_MIRROR_READ_VERSION_CHANGED"
    install(env, previous={"snapshot_id": first["snapshot_id"], "snapshot_sha256": first["snapshot_sha256"]}, expected_revision=1)
    with pytest.raises(reader.PersonalMirrorReadError) as failure:
        with reader.open_installed_read(env["profile"], expected_read_version=version):
            pass
    assert failure.value.status == 409


def test_ready_knowledge_changes_read_version_without_changing_personal_snapshot(mirror_env):
    env = mirror_env
    install(env)
    with reader.open_installed_read(env["profile"]) as con:
        first = reader.capabilities_for(con)["personal_read_version"]
    damage(env, lambda con: con.execute("INSERT INTO video_asset(video_id,title,files_json,sync_state) VALUES('synthetic-ready','sample','{}','staging')"))
    with reader.open_installed_read(env["profile"]) as con:
        second = reader.capabilities_for(con)["personal_read_version"]
    damage(env, lambda con: con.execute("UPDATE video_asset SET sync_state='ready' WHERE video_id='synthetic-ready'"))
    with reader.open_installed_read(env["profile"]) as con:
        third = reader.capabilities_for(con)["personal_read_version"]
    assert first != second != third


def test_metadata_does_not_hash_whole_body_and_bad_attachment_keeps_text(mirror_env, monkeypatch):
    env = mirror_env
    prepared, _ = install(env, attachment=True)
    descriptor = wire.attachment_manifest(prepared)[0]
    actual = files._HeldFile.full
    def forbid_hash(*args, **kwargs):
        pytest.fail("metadata must not hash every installed attachment body")
    monkeypatch.setattr(files._HeldFile, "full", forbid_hash)
    with reader.open_installed_read(env["profile"]) as con:
        view = reader.attachment_view(con, descriptor["attachment_id"])
        assert view["readable"] and not view["missing"] and view["state"] == "ready"
    monkeypatch.setattr(files._HeldFile, "full", actual)
    path = env["data"] / files.ROOT / descriptor["storage_key"]
    path.unlink()
    with reader.open_installed_read(env["profile"]) as con:
        view = reader.attachment_view(con, descriptor["attachment_id"])
        assert view["missing"] and not view["readable"] and view["state"] == "ready"
        assert view["sha256"] == descriptor["sha256"]
        assert reader.capabilities_for(con)["personal_read"]
        assert con.execute("SELECT title FROM source_record WHERE source_id=?", (env["source_id"],)).fetchone()


def test_exact_fd_is_pending_until_factory_success_then_owned_until_close(mirror_env):
    env = mirror_env
    prepared, _ = install(env, attachment=True)
    descriptor = wire.attachment_manifest(prepared)[0]
    with reader.open_installed_read(env["profile"]) as con:
        holder = reader.open_owned_attachment(con, descriptor["attachment_id"])
        assert holder.personal_read_version == reader.capabilities_for(con)["personal_read_version"]
        with pytest.raises(reader.PersonalMirrorReadError):
            holder.stream.read(1)
    with ThreadPoolExecutor(max_workers=1) as executor:
        assert executor.submit(holder.stream.read).result() == BODY
    holder.stream.seek(0)
    assert holder.stream.read(7) == BODY[:7]
    holder.close()
    holder.close()
    assert holder.stream.closed
    with pytest.raises(reader.PersonalMirrorReadError):
        holder.stream.read()
    with pytest.raises(reader.PersonalMirrorReadError):
        reader.open_owned_attachment(con, descriptor["attachment_id"])


def test_factory_cancellation_or_source_change_closes_pending_original_fds(mirror_env):
    env = mirror_env
    prepared, _ = install(env, attachment=True)
    descriptor = wire.attachment_manifest(prepared)[0]
    marker = RuntimeError("controlled consumer cancellation")
    with pytest.raises(RuntimeError) as failure:
        with reader.open_installed_read(env["profile"]) as con:
            holder = reader.open_owned_attachment(con, descriptor["attachment_id"])
            raise marker
    assert failure.value is marker and holder.stream.closed
    with pytest.raises(reader.PersonalMirrorReadError):
        with reader.open_installed_read(env["profile"]) as con:
            changed = reader.open_owned_attachment(con, descriptor["attachment_id"])
            damage(env, lambda actual: actual.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='kb_revision'"))
    assert changed.stream.closed


def test_same_size_changed_body_cannot_use_ready_row_to_download(mirror_env):
    env = mirror_env
    prepared, _ = install(env, attachment=True)
    descriptor = wire.attachment_manifest(prepared)[0]
    path = env["data"] / files.ROOT / descriptor["storage_key"]
    original = path.stat()
    path.write_bytes(b"x" * len(BODY))
    os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
    with reader.open_installed_read(env["profile"]) as con:
        view = reader.attachment_view(con, descriptor["attachment_id"])
        assert view["state"] == "ready" and view["sha256"] == descriptor["sha256"]
        # Windows ctime can describe creation rather than content change. A
        # fullstat-only metadata hint never substitutes for download's SHA.
        if os.name != "nt":
            assert view["missing"]
        with pytest.raises(reader.PersonalMirrorReadError):
            reader.open_owned_attachment(con, descriptor["attachment_id"])


def test_cancel_callback_and_sql_budget_survive_removed_caller_hooks(mirror_env, monkeypatch):
    env = mirror_env
    install(env)
    flag = [False]
    marker = RuntimeError("controlled read cancellation")
    def cancelled():
        if flag[0]:
            raise marker
    with pytest.raises(RuntimeError) as failure:
        with reader.open_installed_read(env["profile"], check_cancelled=cancelled) as con:
            flag[0] = True
            reader.capabilities_for(con)
    assert failure.value is marker
    with pytest.raises(reader.PersonalMirrorReadError) as failure:
        with reader.open_installed_read(env["profile"]) as con:
            monkeypatch.setattr(reader, "MAX_SQL_STEPS", 1000)
            con.set_progress_handler(None, 0)
            con.execute("WITH RECURSIVE numbers(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM numbers WHERE n<100000) SELECT sum(n) FROM numbers").fetchone()
    assert failure.value.status == 507
