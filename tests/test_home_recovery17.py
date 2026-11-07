"""Genuine old SDK source and isolated17 restore, never DTO-granted authority."""
from contextlib import contextmanager
from copy import deepcopy
from importlib import import_module
import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import uuid

import pytest

from reno import config, db, home_recovery as legacy, home_recovery_schema as home_schema
from reno import home_recovery_source as sources, source_preservation as preservation
from reno import source_preservation_witness as preservation_witness
from reno import storage_admin, sync_source_authority as authority, sync_source_readmit_schema as readmit_schema
from reno import sync_source_continuity_service as cloud_service, sync_state, tasks
from tests import test_home_recovery_home as old_home
from tests.test_home_recovery_home import env as old16_environment
from tests.test_source_preservation_witness import seed_origin, historical_state, authority_state
from tests.test_sync_source_continuity_service import env as cloud_environment
from tests import test_sync_source_continuity_service as cloud_fixture
from tests import test_sync_source_readmit_contract as sample, test_sync_source_readmit_schema as sql_records
from tests.schema_fixtures import assert_current17_runtime, assert_released16_image, migrate_released16_to_current


@pytest.fixture
def recovery17():
    # Fail if the actual new producer does not exist. No fixture substitute or
    # importorskip can turn an unavailable restore entry point into success.
    return import_module("reno.home_recovery17")


def complete_home16(value):
    profile, source_id, library, _node = value
    store, operation, plan = old_home.home_dns(value)
    unknown = old_home.source_reservation(value, operation, request_sha=plan.request_sha256, kind="source_resolution")
    historical_job = tasks.submit("process", "home_video", profile=profile)["job_id"]
    paid = old_home.reserve(value, historical_job, dispatch=True, settle=True)
    with storage_admin.connection(profile) as con:
        con.execute("UPDATE job SET status='done' WHERE job_id=?", (historical_job,))
        con.commit()
        job = dict(con.execute("SELECT * FROM job WHERE job_id=?", (operation.root_job_id,)).fetchone())
    store.enable_source_readmit()
    fixture = SimpleNamespace(profile=profile, source_id=source_id, library=library, store=store,
        root=operation.root_job_id, job=job, core=store.read()["source_execution"],
        unknown=unknown, paid=paid, old=value)
    fixture.origin = seed_origin(fixture, fixture.core)
    historical_state(fixture, {"source_authority": authority_state(fixture.core, origin=authority.record_ref(fixture.origin))})
    old_home.changed(value)
    return fixture


def upgrade17(value):
    # Historical prospective17-v1 only. Ordinary production17 migration has
    # its separate closed-old-lifetime factory below; do not change this seam.
    assert_released16_image(value.profile.database)
    with storage_admin.connection(value.profile) as con:
        readmit_schema.install(con)
        con.execute("UPDATE meta SET value='17' WHERE key='migration_version'")
        con.commit()
    return value


@contextmanager
def completed_released16_home(tmp_path, monkeypatch):
    """Produce and use original paid/unknown/core/origin facts entirely on16."""
    with old_home.released16_home(tmp_path, monkeypatch) as old:
        yield complete_home16(old)


@contextmanager
def historical17_v1_home(tmp_path, monkeypatch):
    """Declared historical v1 image with original global16/cap0 behavior."""
    with completed_released16_home(tmp_path, monkeypatch) as value:
        yield upgrade17(value)


def create_current17_home(tmp_path, monkeypatch):
    """Close every old owner before ordinary guarded/backed-up17 migration."""
    with completed_released16_home(tmp_path, monkeypatch) as value:
        pass
    assert_current17_runtime()
    migrate_released16_to_current(value.profile)
    return value


@pytest.fixture
def current17_environment(tmp_path, monkeypatch):
    # No yielding historical parent fixture may outlive this migration.
    return create_current17_home(tmp_path, monkeypatch)


@pytest.fixture
def actual_home16(old16_environment):
    return complete_home16(old16_environment)


@pytest.fixture
def env(tmp_path, monkeypatch):
    # Existing consumers explicitly exercise prospective17-v1/global16.
    with historical17_v1_home(tmp_path, monkeypatch) as value:
        yield value


@pytest.fixture
def dual_env(tmp_path, request, monkeypatch):
    with completed_released16_home(tmp_path, monkeypatch) as value:
        actual_local_role = config.is_cloud
        # Temporary Cloud17-v1 globals belong only to its real producer phase;
        # close them before the surrounding historical Home17-v1 lifetime.
        with monkeypatch.context() as historical17:
            cloud = cloud_fixture.env.__wrapped__(tmp_path, historical17, request)
            assert cloud.profile.identity == value.profile.identity
            active = cloud_fixture.activated(cloud)
            original = cloud_fixture.register_request(cloud, active)
            registered = cloud_service.register(profile=cloud.profile, request=original, now=cloud_fixture.NOW)
            disposed = cloud_service.dispose(profile=cloud.profile,
                request=cloud_fixture.dispose_request(cloud, original["original_request"], registered), now=cloud_fixture.NOW)
            assert disposed["disposition"] == "not_applied"
            # Canonical existing historical SQL, not a caller admission seal.
            admitted = sql_records.admission(body=cloud_fixture.bound(cloud, sample.readmit()))
            with storage_admin.connection(value.profile) as con:
                sql_records.insert(con, readmit_schema.ADMISSION, readmit_schema.validate_record(readmit_schema.ADMISSION, admitted))
                con.commit()
                epoch = db.knowledge_version(con)["library_epoch"]
            value.store.update({"local_epoch": epoch})
            historical17.setattr(config, "is_cloud", actual_local_role)
        yield value


def canonical(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def read_sql(value):
    con = sqlite3.connect(value.profile.database.as_uri() + "?mode=ro", uri=True)
    try:
        con.execute("PRAGMA query_only=ON")
        con.execute("BEGIN")
        tables = {**preservation.PROJECTIONS_V17,
            **{name: tuple(columns.split()) for name, columns in preservation._KNOWLEDGE.items()}}
        output = {}
        for table, columns in sorted(tables.items()):
            names = ",".join('"' + field + '"' for field in columns)
            output[table] = sorted(con.execute('SELECT ' + names + ' FROM main."' + table + '"').fetchall(), key=repr)
        return output
    finally:
        con.close()


def receipt(value, operation_id):
    con = sqlite3.connect(value.profile.database.as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute('SELECT ' + ",".join(home_schema.COLUMNS) + ' FROM ' + home_schema.TABLE + ' WHERE operation_id=?', (operation_id,)).fetchone()
        return None if row is None else home_schema.validate_record(row)
    finally:
        con.close()


def view(module, value):
    return module.preview(profile=value.profile, source_id=value.source_id)


def apply(module, value, *, preview=None, operation_id=None):
    observed = view(module, value) if preview is None else preview
    operation_id = operation_id or str(uuid.uuid4())
    result = module.apply(profile=value.profile, source_id=value.source_id,
        expected_digest=observed["digest"], operation_id=operation_id)
    return operation_id, result


def assert_preserved_operational(before, after):
    # Knowledge and its explicitly permitted version/step metadata can change;
    # every old operational row and original private SQL value remain exact.
    for table in preservation.PROJECTIONS_V17:
        if table not in {"meta", home_schema.TABLE}:
            assert after[table] == before[table]
    excluded = {"library_epoch", "kb_revision"}
    private = lambda rows: [row for row in rows if row[0] not in excluded and not row[0].startswith("step:")]
    assert private(after["meta"]) == private(before["meta"])
    assert set(before[home_schema.TABLE]) <= set(after[home_schema.TABLE])


def test_real_preview_is_pure_and_actual_wrapper_restore_keeps_paid_unknown_and_state5(recovery17, env):
    before, original_state = read_sql(env), env.store.read()
    preview = view(recovery17, env)
    assert read_sql(env) == before and env.store.read() == original_state
    assert preview["source"]["source_id"] == env.source_id
    operation, result = apply(recovery17, env, preview=preview)
    after = read_sql(env)
    assert_preserved_operational(before, after)
    assert env.store.read() == {**original_state, "local_epoch": result["after_version"]["library_epoch"]}
    assert result["after_version"]["library_epoch"] == result["before_version"]["library_epoch"] + 1
    assert result["replayed"] is False
    row = receipt(env, operation)
    wrapper = json.loads(row["checkpoint_json"])
    assert row["format_version"] == 1 and wrapper["format_version"] == 2
    assert wrapper["checkpoint"]["state_format_version"] == 5
    assert wrapper["state_authority"]["source_authority"]["origin"] == authority.record_ref(env.origin)
    assert row["checkpoint_sha256"] == digest(wrapper)
    assert wrapper["certificate"]["preserved_sha256"] == row["before_operational_sha256"] == row["after_operational_sha256"]
    assert wrapper["certificate"]["frontier_sha256"] == row["frontier_sha256"]
    with sqlite3.connect(env.profile.database) as con:
        assert con.execute("SELECT claim FROM knowledge_atom WHERE id='home_atom'").fetchone() == ("原知识",)
        assert con.execute("SELECT value FROM meta WHERE key='private-test-blob'").fetchone() == (b"\x00\xffprivate-stays-local",)
        assert con.execute("PRAGMA foreign_key_check").fetchone() is None


def test_actual_double_facilities_four_tables_keep_original_facts_through_restore(recovery17, dual_env):
    value = dual_env
    before, state = read_sql(value), value.store.read()
    operation, result = apply(recovery17, value)
    after = read_sql(value)
    assert_preserved_operational(before, after)
    row = receipt(value, operation)
    wrapper = json.loads(row["checkpoint_json"])
    assert all(capsule["presence"] == "present" for capsule in wrapper["permanent_witnesses"].values())
    assert all(len(after[table]) == 1 for table in readmit_schema.COLUMNS)
    assert value.store.read() == {**state, "local_epoch": result["after_version"]["library_epoch"]}


class CrashAfterSQL(RuntimeError):
    pass


def test_written_proposal_before_sql_commit_rolls_back_and_resumes_original_uuid(recovery17, env, monkeypatch):
    observed, operation = view(recovery17, env), str(uuid.uuid4())
    before, state = read_sql(env), env.store.read()
    original_write = recovery17._write
    marker = CrashAfterSQL("proposal durable but SQL still uncommitted")
    captured_row = {}

    def durable_proposal_then_stop(directory, name, value):
        result = original_write(directory, name, value)
        if name == operation + ".proposal.json":
            # Observe the actual producer's uncommitted original 35 columns;
            # a fixture or caller DTO never supplies the restored result.
            window = preservation_witness._WINDOW.get()
            row = window.connection.execute('SELECT ' + ",".join(home_schema.COLUMNS) +
                ' FROM main."' + home_schema.TABLE + '" WHERE operation_id=?', (operation,)).fetchone()
            captured_row.update(home_schema.validate_record(row))
            raise marker
        return result

    with monkeypatch.context() as scoped:
        scoped.setattr(recovery17, "_write", durable_proposal_then_stop)
        with pytest.raises(CrashAfterSQL) as caught:
            apply(recovery17, env, preview=observed, operation_id=operation)
    assert caught.value is marker
    assert receipt(env, operation) is None
    assert read_sql(env) == before and env.store.read() == state
    directory = env.profile.database.parent / recovery17.DIRECTORY
    original_files = {part: (directory / (operation + "." + part + ".json")).read_bytes()
                      for part in ("request", "intent", "proposal")}
    proposed = json.loads(original_files["proposal"])
    recovered_id, result = apply(recovery17, env, preview=observed, operation_id=operation)
    assert recovered_id == operation and not result["replayed"]
    row = receipt(env, operation)
    assert row == captured_row
    assert row["committed_at"] == proposed["committed_at"]
    assert all((directory / (operation + "." + part + ".json")).read_bytes() == raw
               for part, raw in original_files.items())
    assert len(read_sql(env)[home_schema.TABLE]) == len(before[home_schema.TABLE]) + 1
    assert env.store.read() == {**state, "local_epoch": row["after_library_epoch"]}


def test_committed_sql_before_permanent_producer_receipt_resumes_original_uuid(recovery17, env, monkeypatch):
    observed, operation = view(recovery17, env), str(uuid.uuid4())
    before_state = env.store.read()
    marker = CrashAfterSQL("after SQL before permanent receipt")
    with monkeypatch.context() as scoped:
        scoped.setattr(recovery17, "_publish_receipt", lambda *_args, **_kwargs: (_ for _ in ()).throw(marker))
        with pytest.raises(CrashAfterSQL) as caught:
            apply(recovery17, env, preview=observed, operation_id=operation)
    assert caught.value is marker
    committed = receipt(env, operation)
    assert committed is not None and env.store.read() == before_state
    before_resume = read_sql(env)
    resumed = recovery17.resume(profile=env.profile, operation_id=operation)
    assert resumed["replayed"] and env.store.read() == {**before_state, "local_epoch": committed["after_library_epoch"]}
    assert read_sql(env) == before_resume
    again = recovery17.resume(profile=env.profile, operation_id=operation)
    assert again == resumed and read_sql(env) == before_resume


def test_permanent_producer_receipt_before_state_epoch_resumes_without_retired_source(recovery17, env, monkeypatch):
    observed, operation = view(recovery17, env), str(uuid.uuid4())
    before_state = env.store.read()
    original_update = sync_state.StateStore.update
    marker = CrashAfterSQL("after producer receipt before State epoch")

    def interrupted_update(store, updates):
        if store.profile is not None and store.profile.identity == env.profile.identity and set(updates) == {"local_epoch"}:
            raise marker
        return original_update(store, updates)

    with monkeypatch.context() as scoped:
        scoped.setattr(sync_state.StateStore, "update", interrupted_update)
        with pytest.raises(CrashAfterSQL) as caught:
            apply(recovery17, env, preview=observed, operation_id=operation)
    assert caught.value is marker
    committed = receipt(env, operation)
    assert committed is not None and env.store.read() == before_state
    # Only this isolated original declaration is removed; a committed receipt
    # must not reopen it and perform a second restore merely to reconcile.
    sources._source_path(env.profile, env.source_id).unlink()
    before = read_sql(env)
    resumed = recovery17.resume(profile=env.profile, operation_id=operation)
    assert resumed["replayed"] and read_sql(env) == before
    assert env.store.read() == {**before_state, "local_epoch": committed["after_library_epoch"]}
    repeated = recovery17.apply(profile=env.profile, source_id=env.source_id,
        expected_digest=observed["digest"], operation_id=operation)
    assert repeated == resumed and read_sql(env) == before


def test_epoch_gap_resume_refuses_to_overwrite_changed_non_epoch_state(recovery17, env, monkeypatch):
    observed, operation = view(recovery17, env), str(uuid.uuid4())
    original_update = sync_state.StateStore.update

    def stop_epoch(store, updates):
        if store.profile is not None and store.profile.identity == env.profile.identity and set(updates) == {"local_epoch"}:
            raise CrashAfterSQL("stop before separate State epoch")
        return original_update(store, updates)

    with monkeypatch.context() as scoped:
        scoped.setattr(sync_state.StateStore, "update", stop_epoch)
        with pytest.raises(CrashAfterSQL):
            apply(recovery17, env, preview=observed, operation_id=operation)
    committed = receipt(env, operation)
    assert env.store.read()["local_epoch"] == committed["before_library_epoch"]
    # A valid later State change is real current persistence, not a caller DTO.
    env.store.update({"session_id": str(uuid.uuid4())})
    before, changed_state = read_sql(env), env.store.read()
    with pytest.raises(ValueError):
        recovery17.resume(profile=env.profile, operation_id=operation)
    assert read_sql(env) == before and env.store.read() == changed_state


def test_newer_state_and_sql_are_kept_when_original_receipt_is_replayed(recovery17, env):
    first, initial = apply(recovery17, env)
    _second, later = apply(recovery17, env)
    before, state = read_sql(env), env.store.read()
    assert later["after_version"]["library_epoch"] > initial["after_version"]["library_epoch"]
    replay = recovery17.resume(profile=env.profile, operation_id=first)
    assert replay == {**initial, "replayed": True}
    assert read_sql(env) == before and env.store.read() == state


def test_current_knowledge_revision_cannot_regress_below_original_committed_receipt(recovery17, env):
    operation, result = apply(recovery17, env)
    row = receipt(env, operation)
    assert row["after_kb_revision"] > 0
    with sqlite3.connect(env.profile.database) as con:
        con.execute("UPDATE meta SET value=? WHERE key='kb_revision'", (str(row["after_kb_revision"] - 1),))
    before, state = read_sql(env), env.store.read()
    with pytest.raises(ValueError):
        recovery17.resume(profile=env.profile, operation_id=operation)
    assert read_sql(env) == before and env.store.read() == state


@pytest.mark.parametrize("part", ["request", "intent", "proposal", "receipt"])
def test_reconciled_replay_authenticates_original_permanent_producer_metadata(recovery17, env, part):
    operation, result = apply(recovery17, env)
    row = receipt(env, operation)
    assert env.store.read()["local_epoch"] == row["after_library_epoch"]
    path = env.profile.database.parent / recovery17.DIRECTORY / (operation + "." + part + ".json")
    document = json.loads(path.read_bytes())
    if part == "request":
        document["source"]["source_binding_sha256"] = "8" * 64
    elif part == "intent":
        document["file_set_sha256"] = "8" * 64
    elif part == "proposal":
        document["row_sha256"] = "8" * 64
    else:
        document["result_sha256"] = "8" * 64
    # Keep valid canonical bytes and field types so integrity/provenance, rather
    # than JSON syntax or a generic SQL checksum, must reject the real file.
    path.write_bytes(canonical(document).encode())
    before, state = read_sql(env), env.store.read()
    with pytest.raises(ValueError):
        recovery17.resume(profile=env.profile, operation_id=operation)
    assert read_sql(env) == before and env.store.read() == state


def test_cas_change_and_other_operation_body_never_write_new_restore(recovery17, env):
    observed = view(recovery17, env)
    with sqlite3.connect(env.profile.database) as con:
        con.execute("UPDATE meta SET value=? WHERE key='private-test-blob'", (b"different-private-current",))
    before, state = read_sql(env), env.store.read()
    with pytest.raises(ValueError):
        apply(recovery17, env, preview=observed)
    assert read_sql(env) == before and env.store.read() == state
    operation, result = apply(recovery17, env)
    before, state = read_sql(env), env.store.read()
    with pytest.raises(ValueError):
        recovery17.apply(profile=env.profile, source_id=env.source_id, expected_digest="0" * 64, operation_id=operation)
    assert read_sql(env) == before and env.store.read() == state


def test_foreign_uncommitted_private_sql_cannot_become_restore_fact(recovery17, env):
    observed = view(recovery17, env)
    before, state = read_sql(env), env.store.read()
    writer = sqlite3.connect(env.profile.database, timeout=0)
    try:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("INSERT INTO meta(key,value) VALUES('outside-uncommitted-restore',?)", (b"outside\x00\xff",))
        with pytest.raises((ValueError, sqlite3.OperationalError)) as refused:
            apply(recovery17, env, preview=observed)
        if isinstance(refused.value, sqlite3.OperationalError):
            assert "locked" in str(refused.value).lower()
        assert writer.in_transaction
        assert writer.execute("SELECT value FROM meta WHERE key='outside-uncommitted-restore'").fetchone() == (b"outside\x00\xff",)
        assert read_sql(env) == before and env.store.read() == state
    finally:
        writer.rollback()
        writer.close()


def damage_new_row(value, operation, changes):
    # Deliberate corruption of this isolated new row, with the original SQL
    # trigger restored. Never edit a released row or weaken production DDL.
    con = sqlite3.connect(value.profile.database)
    try:
        name = "home_recovery_no_update"
        definition = con.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone()[0]
        con.execute('DROP TRIGGER "' + name + '"')
        con.execute('UPDATE "' + home_schema.TABLE + '" SET ' + ",".join('"' + field + '"=?' for field in changes) + ' WHERE operation_id=?',
            (*changes.values(), operation))
        con.execute(definition)
        con.commit()
    finally:
        con.close()
    # The red is genuine business/provenance corruption, not bad generic SQL
    # field types, missing triggers or a stale canonical JSON checksum.
    return receipt(value, operation)


@pytest.mark.parametrize("field", ["certificate", "result", "source_binding", "operational"])
def test_already_reconciled_return_never_skips_actual_original_binding_validation(recovery17, env, field):
    operation, result = apply(recovery17, env)
    row = receipt(env, operation)
    assert env.store.read()["local_epoch"] == row["after_library_epoch"]
    if field == "certificate":
        wrapper = json.loads(row["checkpoint_json"])
        wrapper["certificate"]["certificate_sha256"] = "8" * 64
        changes = {"checkpoint_json": canonical(wrapper), "checkpoint_sha256": digest(wrapper)}
    elif field == "result":
        body = json.loads(row["result_json"])
        body["knowledge_sha256"] = "8" * 64
        changes = {"result_json": canonical(body), "result_sha256": digest(body)}
    elif field == "source_binding":
        changes = {"source_binding_sha256": "8" * 64}
    else:
        changes = {"before_operational_sha256": "8" * 64, "after_operational_sha256": "8" * 64}
    damaged = damage_new_row(env, operation, changes)
    assert damaged is not None
    before, state = read_sql(env), env.store.read()
    with pytest.raises(ValueError):
        recovery17.resume(profile=env.profile, operation_id=operation)
    assert read_sql(env) == before and env.store.read() == state


def test_actual_old_nine_key_row_and_new_wrapper_row_replay_without_epoch_regression(recovery17, old16_environment):
    old = old16_environment
    store = old_home.state(old)
    old_home.changed(old)
    original, original_result = old_home.apply(old)
    value = SimpleNamespace(profile=old[0], source_id=old[1], store=store)
    original_row = receipt(value, original)
    assert len(json.loads(original_row["checkpoint_json"])) == 9
    with storage_admin.connection(value.profile) as con:
        readmit_schema.install(con)
        con.execute("UPDATE meta SET value='17' WHERE key='migration_version'")
        con.commit()
    newer, newer_result = apply(recovery17, value)
    newer_row = receipt(value, newer)
    assert json.loads(newer_row["checkpoint_json"])["format_version"] == 2
    assert receipt(value, original) == original_row
    before, state = read_sql(value), store.read()
    assert state["local_epoch"] == newer_result["after_version"]["library_epoch"]
    historical = recovery17.resume(profile=value.profile, operation_id=original)
    assert historical == {**original_result, "replayed": True}
    assert read_sql(value) == before and store.read() == state
    assert receipt(value, original) == original_row


@pytest.mark.parametrize("damage", ["row_result", "missing_new_intent"])
def test_old_receipt_replay_authenticates_every_later_new_producer_in_mixed_chain(recovery17, old16_environment, damage):
    old = old16_environment
    store = old_home.state(old)
    old_home.changed(old)
    original, _original_result = old_home.apply(old)
    value = SimpleNamespace(profile=old[0], source_id=old[1], store=store)
    original_row = receipt(value, original)
    upgrade17(value)
    newer, _newer_result = apply(recovery17, value)
    row = receipt(value, newer)
    if damage == "row_result":
        body = json.loads(row["result_json"])
        body["knowledge_sha256"] = "8" * 64
        damage_new_row(value, newer, {"result_json": canonical(body), "result_sha256": digest(body)})
    else:
        path = value.profile.database.parent / recovery17.DIRECTORY / (newer + ".intent.json")
        path.unlink()
    before, state = read_sql(value), store.read()
    with pytest.raises((ValueError, FileNotFoundError)):
        recovery17.resume(profile=value.profile, operation_id=original)
    assert read_sql(value) == before and store.read() == state
    assert receipt(value, original) == original_row
