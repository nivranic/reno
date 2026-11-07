"""Original held SQL/files, real sender gaps and actual receiver stored bodies.

Only deliberate failure/unsafe-file injections and peer HTTP are substituted.
No accepted mirror, sender catalog, stored SQL row or receipt is fabricated.
"""
from contextlib import ExitStack, contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import uuid

import pytest

from reno import db, personal_mirror_contract as facts
from reno import personal_mirror_transport as sender
from reno import personal_mirror_transfer as receiver
from reno import personal_mirror_transfer_files as receiver_files
from reno import personal_mirror_transfer_contract as wire
from reno import personal_mirror_transfer_schema as receiver_schema
from reno import personal_preservation_contract as contract
from reno import personal_preservation_files as files
from reno import personal_preservation_source as capture
from reno import storage_lifecycle as lifecycle, storage_source as source, sync_media as safe
from tests.test_personal_mirror_transfer import mirror_env, make_prepare, arguments, BODY
from tests.test_personal_mirror_transport import (
    sender_env, owned, initialize, freeze, deliver, request_path,
)


def uid():
    return str(uuid.uuid4())


@contextmanager
def captured_home(env, *, limits=None, check_cancelled=None, operation_id=None):
    env["select"](env["source_root"], "home")
    with lifecycle.exclusive(env["source_profile"]):
        with capture.capture_locked(env["source_profile"], operation_id=operation_id or uid(),
                request_sha256="1" * 64, limits=limits or contract.DEFAULT_LIMITS,
                check_cancelled=check_cancelled) as observed:
            yield observed


@contextmanager
def sql_copies(observed, temporary):
    temporary.mkdir()
    if os.name != "nt":
        temporary.chmod(0o700)
    with ExitStack() as stack:
        copies = {}
        for entry in observed.selection["files"]:
            if entry["role"] not in {"database_raw", "database_wal_raw", "database_journal_raw"}:
                continue
            original = observed.files[entry["entry_id"]]
            original.stream.seek(0)
            path = temporary / (entry["entry_id"] + ".raw")
            with path.open("xb") as stream:
                while block := original.stream.read(128 * 1024):
                    stream.write(block)
            if os.name != "nt":
                path.chmod(0o600)
            copies[entry["entry_id"]] = stack.enter_context(files.FileHold(
                path, maximum=entry["byte_size"], collect=False))
        yield copies


def role_file(selection, role):
    return next(row for row in selection["files"] if row["role"] == role)


def sender_raw(env):
    root = env["source_data"] / sender.ROOT
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_capture_requires_actual_existing_profile_ex(mirror_env):
    env = mirror_env
    kwargs = dict(operation_id=uid(), request_sha256="1" * 64, limits=contract.DEFAULT_LIMITS)
    with pytest.raises(lifecycle.StorageLifecycleError) as caught:
        with capture.capture_locked(env["source_profile"], **kwargs):
            pytest.fail("Capture acquired or invented a guard")
    assert caught.value.code == "STORAGE_GUARD_REQUIRED"
    with lifecycle.shared(env["source_profile"]), pytest.raises(lifecycle.StorageLifecycleError) as caught:
        with capture.capture_locked(env["source_profile"], **kwargs):
            pytest.fail("SH was upgraded or borrowed as EX")
    assert caught.value.code == "STORAGE_LOCK_UPGRADE"
    assert not (env["source_data"] / sender.ROOT).exists()


def test_real_integrity_pragmas_keep_the_domain_projection_restricted(mirror_env, monkeypatch):
    original = capture._authorizer
    denied = []
    def tracked(columns, *, checking_integrity=False):
        policy = original(columns, checking_integrity=checking_integrity)
        def authorize(action, first, second, database, origin):
            result = policy(action, first, second, database, origin)
            if result == sqlite3.SQLITE_DENY:
                denied.append((action, first, second, database, origin, checking_integrity))
            return result
        return authorize
    monkeypatch.setattr(capture, "_authorizer", tracked)
    try:
        with captured_home(mirror_env) as observed:
            assert observed.selection["sql"]["integrity"] == "ok"
            assert observed.selection["sql"]["foreign_keys"] == "ok"
    except BaseException:
        # Only synthetic schema metadata is printed, never SQL cell values.
        print("PERSONAL_INTEGRITY_DENIED_ACTIONS", denied)
        raise
    policy = original({"meta": {"key", "value"}})
    assert policy(sqlite3.SQLITE_READ, "job", "payload_json", "main", None) == sqlite3.SQLITE_DENY
    assert policy(sqlite3.SQLITE_DELETE, "meta", None, "main", None) == sqlite3.SQLITE_DENY
    assert policy(sqlite3.SQLITE_FUNCTION, None, "load_extension", None, None) == sqlite3.SQLITE_DENY
    assert policy(sqlite3.SQLITE_FUNCTION, None, "substr", None, None) == sqlite3.SQLITE_DENY
    assert policy(sqlite3.SQLITE_PRAGMA, "data_version", None, "main", None) == sqlite3.SQLITE_DENY
    integrity_policy = original({}, checking_integrity=True)
    assert integrity_policy(sqlite3.SQLITE_PRAGMA, "data_version", None, "main", None) == sqlite3.SQLITE_OK
    assert integrity_policy(sqlite3.SQLITE_PRAGMA, "data_version", "1", "main", None) == sqlite3.SQLITE_DENY
    assert integrity_policy(sqlite3.SQLITE_PRAGMA, "wal_checkpoint", None, "main", None) == sqlite3.SQLITE_DENY


def test_absent_facilities_and_actual_external_configuration_are_original_facts(mirror_env):
    env = mirror_env
    before = env["source_path"].read_bytes()
    with captured_home(env) as observed:
        selection = observed.selection
        assert selection["source_observation"]["sender_facility"] == "absent"
        assert selection["source_observation"]["receiver_facility"] == "absent"
        assert selection["sql"]["receiver_tables"] == {}
        assert set(selection["sql"]["personal_tables"]) == set(facts.TABLE_NAMES)
        config_entry = role_file(selection, "local_config_raw")
        assert config_entry["root_kind"] == "CONFIG"
        assert selection["source_observation"]["roots"]["CONFIG"]["path"] == contract.canonical_path(str(env["source_root"]))
        config_entry["sha256"] = "f" * 64
        assert observed.selection != selection  # Detached facts cannot weaken holds.
        observed.verify()
    with pytest.raises(contract.Error, match="SOURCE_OBSERVER_CLOSED"):
        observed.verify()
    with pytest.raises(contract.Error, match="SOURCE_OBSERVER_CLOSED"):
        _ = observed.selection
    assert env["source_path"].read_bytes() == before
    assert not (env["source_data"] / sender.ROOT).exists()


def test_complete_current_ready_body_and_unknown_staging_raw_are_held(mirror_env):
    env = mirror_env
    make_prepare(env, with_attachment=True)
    unknown = env["source_data"] / "personal-attachments" / ".mirror-staging" / "partial-original.bin"
    unknown.parent.mkdir(exist_ok=True)
    unknown.write_bytes(b"unclassified original partial bytes")
    with captured_home(env) as observed:
        selection = observed.selection
        ready = role_file(selection, "current_personal_attachment")
        assert ready["sha256"] == hashlib.sha256(BODY).hexdigest()
        requirements = [r for r in selection["requirements"] if r["kind"] == "current_ready"]
        assert len(requirements) == 1 and requirements[0]["entry_id"] == ready["entry_id"]
        unclassified = next(row for row in selection["files"] if row["relative_key"].endswith("partial-original.bin"))
        assert unclassified["role"] == "unclassified_raw"
        assert unclassified["entry_id"] in selection["unknowns"]
        assert observed.files[unclassified["entry_id"]].bytes(1024) == unknown.read_bytes()
    assert unknown.read_bytes() == b"unclassified original partial bytes"


@pytest.mark.parametrize("change", ["missing", "hash"])
def test_current_ready_required_body_cannot_be_invented_or_repaired(mirror_env, change):
    env = mirror_env
    prepared = make_prepare(env, with_attachment=True)
    descriptor = wire.attachment_manifest(prepared)[0]
    body = env["source_data"] / "personal-attachments" / descriptor["storage_key"]
    if change == "missing":
        body.unlink()
    else:
        body.write_bytes(b"x" * len(BODY))
    with pytest.raises(contract.Error, match="REQUIRED_BODY"):
        with captured_home(env):
            pytest.fail("Invalid original ready body was repaired")
    assert body.exists() is (change != "missing")


def test_directory_names_and_original_readers_remain_live_until_success_exit(mirror_env):
    env = mirror_env
    make_prepare(env, with_attachment=True)
    addition = env["source_data"] / "personal-attachments" / "new-original.bin"
    with pytest.raises(contract.Error, match="SOURCE_CHANGED"):
        with captured_home(env) as observed:
            addition.write_bytes(b"newer file must not enter an older selection")
            assert all(row["relative_key"] != "personal-attachments/new-original.bin" for row in observed.selection["files"])
    with pytest.raises(contract.Error, match="SOURCE_OBSERVER_CLOSED"):
        observed.verify()
    assert addition.read_bytes() == b"newer file must not enter an older selection"


def test_own_private_package_directories_do_not_change_data_parent_scope(mirror_env):
    env = mirror_env
    with captured_home(env) as observed:
        for name in (contract.CATALOG, contract.DIRECTORY):
            (env["source_data"] / name).mkdir()
        observed.verify()


def test_sql_heartbeat_bypass_changes_raw_source_even_with_actual_ex(mirror_env):
    env = mirror_env
    with pytest.raises((contract.Error, source.SourceReadError, capture.readonly.ReadonlyImageError)) as caught:
        with captured_home(env):
            con = sqlite3.connect(env["source_path"])
            try:
                # Represents the existing non-SH SQL writer. No fake success
                # rows or source fingerprint are introduced by the test.
                con.execute("UPDATE meta SET value='2' WHERE key='primary_epoch'")
                con.commit()
            finally:
                con.close()
    assert "SOURCE_CHANGED" in caught.value.code


def test_raw_db_and_committed_wal_replay_without_source_shm(mirror_env, tmp_path, monkeypatch):
    env = mirror_env
    con = sqlite3.connect(env["source_path"])
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("UPDATE source_record SET title='committed WAL original' WHERE source_id=?", (env["source_id"],))
    con.commit()
    original_entry = safe._Directory.entry_stat
    def no_source_shm(directory, name):
        if directory.path == env["source_data"] and name == "reno.db-shm":
            pytest.fail("Source SHM was observed")
        return original_entry(directory, name)
    monkeypatch.setattr(safe._Directory, "entry_stat", no_source_shm)
    try:
        with captured_home(env) as observed:
            assert observed.selection["sql"]["source_fingerprint"]["files"]["-wal"]["byte_size"] > 0
            with sql_copies(observed, tmp_path / "exact-raw-payload") as copies:
                capture.verify_sql_payload(observed.selection, copies)
                with capture.payload_image_locked(observed.selection, copies) as image:
                    assert image.connection.execute("SELECT title FROM source_record WHERE source_id=?", (env["source_id"],)).fetchone() == ("committed WAL original",)
                    assert image.snapshot_bytes > 0
                    image.verify()
        assert con.execute("SELECT title FROM source_record WHERE source_id=?", (env["source_id"],)).fetchone()[0] == "committed WAL original"
    finally:
        con.close()


@pytest.mark.parametrize("suffix", ["-wal", "-journal"])
def test_real_zero_byte_sql_companion_is_selected_not_faked_absent(mirror_env, suffix):
    env = mirror_env
    companion = Path(str(env["source_path"]) + suffix)
    companion.write_bytes(b"")
    with captured_home(env) as observed:
        fingerprint = observed.selection["sql"]["source_fingerprint"]["files"][suffix]
        assert fingerprint["byte_size"] == 0
        role = "database_wal_raw" if suffix == "-wal" else "database_journal_raw"
        assert role_file(observed.selection, role)["sha256"] == hashlib.sha256(b"").hexdigest()
    assert companion.exists() and companion.stat().st_size == 0


def test_nonempty_rollback_journal_prevents_complete_source(mirror_env):
    env = mirror_env
    journal = Path(str(env["source_path"]) + "-journal")
    journal.write_bytes(b"unmaterialized original journal")
    with pytest.raises(capture.readonly.ReadonlyImageError) as caught:
        with captured_home(env):
            pytest.fail("Unmaterialized journal was accepted")
    assert caught.value.code == "SOURCE_ROLLBACK_JOURNAL_PRESENT"
    assert journal.read_bytes() == b"unmaterialized original journal"


def test_original_json_cell_spacing_and_blob_types_are_not_rewritten(mirror_env, tmp_path):
    env = mirror_env
    original_json = '{ "scope" : [ "绍兴", "原文" ] }'
    con = sqlite3.connect(env["source_path"])
    con.execute("UPDATE source_record SET scope_json=?,excerpt=? WHERE source_id=?", (original_json, b"original BLOB cell\x00", env["source_id"]))
    con.commit()
    con.close()
    with captured_home(env) as observed, sql_copies(observed, tmp_path / "typed-payload") as copies:
        with capture.payload_image_locked(observed.selection, copies) as image:
            assert image.connection.execute("SELECT scope_json,excerpt FROM source_record WHERE source_id=?", (env["source_id"],)).fetchone() == (original_json, b"original BLOB cell\x00")


def test_receiver_actual_stored_not_installed_body_and_original_sql_identity(mirror_env):
    env = mirror_env
    prepared = make_prepare(env, with_attachment=True)
    descriptor = wire.attachment_manifest(prepared)[0]
    with lifecycle.shared(env["profile"]):
        receiver.prepare_mirror_locked(**arguments(env), prepare=prepared)
        receiver_files.store_attachment_locked(**arguments(env), operation_id=prepared["operation_id"],
            expected_prepare_sha256=wire.prepare_sha256(prepared), attachment_id=descriptor["attachment_id"], chunks=[BODY])
    with lifecycle.exclusive(env["profile"]), capture.capture_locked(env["profile"], operation_id=uid(),
            request_sha256="2" * 64, limits=contract.DEFAULT_LIMITS) as observed:
        selection = observed.selection
        assert selection["sql"]["meta"]["count"] == 3
        assert all(summary["count"] == 0 for summary in selection["sql"]["personal_tables"].values())
        assert selection["source_observation"]["personal_primary_id"] is None
        assert selection["sql"]["receiver_facility"] == "installed"
        assert selection["sql"]["receiver_tables"][receiver_schema.ATTACHMENT]["count"] == 1
        assert selection["sql"]["receiver_tables"][receiver_schema.RECEIPT]["count"] == 0
        body = role_file(selection, "receiver_stored_attachment")
        required = next(r for r in selection["requirements"] if r["kind"] == "receiver_stored")
        assert required["entry_id"] == body["entry_id"]
        assert required["expected_file_identity"] == {k: body["source_handle_identity"][k] for k in capture._IDENTITY_KEYS}
        assert observed.files[body["entry_id"]].bytes(1024) == BODY
    con = sqlite3.connect(env["profile"].database)
    try:
        original_meta = list(con.execute("SELECT key,value FROM meta WHERE key IN (?,?,?,?) ORDER BY key", facts.META_KEYS))
        assert [row[0] for row in original_meta] == ["library_id", "personal_primary_id", "primary_epoch"]
        assert dict(original_meta)["personal_primary_id"] == ""
        assert all(con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone() == (0,) for table in facts.TABLE_NAMES)
        assert con.execute(f"SELECT state FROM {receiver_schema.ATTACHMENT}").fetchone() == ("stored",)
        assert con.execute(f"SELECT COUNT(*) FROM {receiver_schema.RECEIPT}").fetchone() == (0,)
    finally:
        con.close()


@pytest.mark.parametrize("key", ["library_id", "personal_primary_id", "primary_epoch"])
def test_native_cloud_missing_core_metadata_is_never_filled(mirror_env, key):
    env = mirror_env
    con = sqlite3.connect(env["profile"].database)
    con.execute("DELETE FROM meta WHERE key=?", (key,))
    con.commit()
    con.close()
    with lifecycle.exclusive(env["profile"]), pytest.raises(contract.Error, match="PERSONAL_META_INVALID"):
        with capture.capture_locked(env["profile"], operation_id=uid(), request_sha256="2" * 64,
                limits=contract.DEFAULT_LIMITS):
            pytest.fail("Missing original core cloud metadata was synthesized")
    con = sqlite3.connect(env["profile"].database)
    try:
        assert con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone() is None
        assert con.execute("SELECT value FROM meta WHERE key='default_project_id'").fetchone() is None
    finally:
        con.close()


def test_receiver_partial_reserved_namespace_is_not_repaired(mirror_env):
    env = mirror_env
    con = sqlite3.connect(env["source_path"])
    con.execute("CREATE TABLE personal_mirror_transfer_intent(operation_id TEXT PRIMARY KEY)")
    con.commit()
    con.close()
    with pytest.raises((receiver_schema.PersonalMirrorSchemaError, contract.Error)):
        with captured_home(env):
            pytest.fail("Partial receiver facilities were installed or ignored")


def test_all_sender_history_and_frozen_bodies_survive_current_home_advancement(sender_env):
    env = sender_env
    with owned(env):
        initialize(env)
        first = freeze(env, attachment=True)
        deliver(env, first)
        second = freeze(env, previous=first["operation_id"])
        deliver(env, second)
    before = sender_raw(env)
    with captured_home(env) as observed:
        selection = observed.selection
        selected = {r["relative_key"].split("/")[-1] for r in selection["files"] if r["role"] == "sender_request_raw"}
        assert selected == {sender._request_name(first["operation_id"]), sender._request_name(second["operation_id"])}
        assert len([r for r in selection["files"] if r["role"] == "sender_receipt_raw"]) == 2
        assert len([r for r in selection["requirements"] if r["kind"] == "sender_frozen"]) == 2
        assert selection["source_observation"]["sender_facility"] == "installed"
    assert sender_raw(env) == before


@pytest.mark.parametrize("gap", ["request", "blobs", "receipt"])
def test_actual_sender_publish_gaps_preserve_raw_without_repairing_catalog(sender_env, monkeypatch, gap):
    env = sender_env
    make_prepare(env, with_attachment=True)
    env["select_home"]()
    operation = uid()
    failure = RuntimeError("controlled original publication interruption")
    with owned(env):
        initialize(env)
        if gap == "request":
            original = sender._Context.replace_catalog
            def interrupt(context, raw, value):
                if value["entries"]:
                    raise failure
                return original(context, raw, value)
            with monkeypatch.context() as patch:
                patch.setattr(sender._Context, "replace_catalog", interrupt)
                with pytest.raises(RuntimeError) as caught:
                    freeze(env, operation_id=operation)
                assert caught.value is failure
        elif gap == "blobs":
            with monkeypatch.context() as patch:
                def interrupt(*args, **kwargs):
                    raise failure
                patch.setattr(sender, "_register_blobs", interrupt)
                with pytest.raises(RuntimeError) as caught:
                    freeze(env, operation_id=operation)
                assert caught.value is failure
        else:
            frozen = freeze(env, operation_id=operation)
            with monkeypatch.context() as patch:
                def interrupt(*args, **kwargs):
                    raise failure
                patch.setattr(sender, "_advance", interrupt)
                with pytest.raises(RuntimeError) as caught:
                    deliver(env, frozen)
                assert caught.value is failure
    before = sender_raw(env)
    catalog = json.loads(before[sender.CATALOG])
    with captured_home(env) as observed:
        selection = observed.selection
        request = role_file(selection, "sender_request_raw")
        assert request["sha256"] == hashlib.sha256(before[sender._request_name(operation)]).hexdigest()
        assert any(r["kind"] == ("historical_home" if gap == "request" else "sender_frozen") for r in selection["requirements"])
        if gap == "receipt":
            assert role_file(selection, "sender_receipt_raw")
    assert sender_raw(env) == before
    if gap == "request":
        assert catalog["entries"] == []
    else:
        assert catalog["head_operation_id"] is None
        assert catalog["entries"][0]["receipt_file_sha256"] is None
        if gap == "blobs":
            assert catalog["entries"][0]["blob_manifest_sha256"] is None


def test_complete_frozen_original_same_sha_new_inode_is_rejected(sender_env):
    env = sender_env
    with owned(env):
        initialize(env)
        frozen = freeze(env, attachment=True)
    request = json.loads(request_path(env, frozen["operation_id"]).read_bytes())
    descriptor = wire.attachment_manifest(request["prepare"])[0]
    blob = env["source_data"] / sender.ROOT / sender._blob_directory(frozen["operation_id"]) / facts.storage_key(descriptor["storage_key"]).name
    original = blob.with_name("original-retained-body.bin")
    blob.rename(original)
    blob.write_bytes(BODY)
    with pytest.raises(contract.Error, match="REQUIRED_BODY_IDENTITY_CHANGED"):
        with captured_home(env):
            pytest.fail("Same SHA created a replacement original frozen body")
    assert original.read_bytes() == BODY and blob.read_bytes() == BODY


def test_damaged_canonical_sender_control_is_preserved_and_never_adopted(sender_env):
    env = sender_env
    with owned(env):
        initialize(env)
        frozen = freeze(env)
    request = request_path(env, frozen["operation_id"])
    request.write_bytes(b'{"format_version":1,"format_version":1}')
    with pytest.raises(contract.Error):
        with captured_home(env):
            pytest.fail("Corrupt canonical sender control became unknown successful raw")
    assert request.read_bytes() == b'{"format_version":1,"format_version":1}'


def test_sender_facility_requires_actual_local_primary_ownership(sender_env):
    env = sender_env
    with owned(env):
        initialize(env)
        freeze(env)
    original = sender_raw(env)
    con = sqlite3.connect(env["source_path"])
    con.execute("UPDATE meta SET value='' WHERE key='personal_primary_id'")
    con.commit()
    con.close()
    with pytest.raises(contract.Error, match="SENDER_PRIMARY_INVALID"):
        with captured_home(env):
            pytest.fail("Original sender raw granted missing primary ownership")
    assert sender_raw(env) == original


@pytest.mark.parametrize("unsafe", ["hardlink", "symlink", "fifo"])
def test_unknown_unsafe_raw_cannot_be_skipped_or_claimed_covered(sender_env, unsafe):
    env = sender_env
    with owned(env):
        initialize(env)
    original = env["source_data"] / sender.ROOT / "unknown-original.bin"
    original.write_bytes(b"retained unknown")
    other = original.with_name("unknown-second.bin")
    try:
        if unsafe == "hardlink":
            os.link(original, other)
        elif unsafe == "symlink":
            other.symlink_to(original)
        elif hasattr(os, "mkfifo"):
            os.mkfifo(other)
        else:
            pytest.skip("Platform has no FIFO producer")
    except OSError:
        pytest.skip("Platform does not authorize this unsafe-file fixture")
    with pytest.raises((safe.SyncError, files.PreservationFileError, contract.Error)):
        with captured_home(env):
            pytest.fail("Unsafe unknown raw was omitted from claimed coverage")
    assert original.read_bytes() == b"retained unknown"


@pytest.mark.parametrize("limit", ["max_files", "max_bytes"])
def test_source_file_and_byte_budgets_are_enforced_without_cleanup(mirror_env, limit):
    env = mirror_env
    limits = {**contract.DEFAULT_LIMITS, limit: 1}
    original = env["source_path"].read_bytes()
    with pytest.raises((contract.Error, capture.readonly.ReadonlyImageError)):
        with captured_home(env, limits=limits):
            pytest.fail("Source raw exceeded the fixed operation budget")
    assert env["source_path"].read_bytes() == original


def test_cancellation_retains_primary_and_closes_capture(mirror_env):
    env = mirror_env
    error = KeyboardInterrupt("controlled source cancellation")
    cancelled = False
    def check():
        if cancelled:
            raise error
    with pytest.raises(KeyboardInterrupt) as caught:
        with captured_home(env, check_cancelled=check) as observed:
            cancelled = True
    assert caught.value is error
    with pytest.raises(contract.Error, match="SOURCE_OBSERVER_CLOSED"):
        observed.verify()


def test_payload_sql_typed_summary_mismatch_is_not_accepted(mirror_env, tmp_path):
    with captured_home(mirror_env) as observed, sql_copies(observed, tmp_path / "fixed-sql-copy") as copies:
        altered = observed.selection
        altered["sql"]["personal_tables"]["source_record"]["sha256"] = "f" * 64
        with pytest.raises(contract.Error, match="PAYLOAD_SQL_SEMANTICS_CHANGED"):
            capture.verify_sql_payload(altered, copies)


def test_payload_requires_actual_live_pins_not_identity_dto(mirror_env):
    with captured_home(mirror_env) as observed:
        copies = {row["entry_id"]: deepcopy(row) for row in observed.selection["files"]}
        with pytest.raises(contract.Error, match="PAYLOAD_SQL_HOLD_REQUIRED"):
            capture.verify_sql_payload(observed.selection, copies)


def test_personal_sql_temporary_is_private_before_the_first_sqlite_open(mirror_env, tmp_path, monkeypatch):
    opened = []
    original = capture.readonly._open_private
    def private_open(path):
        files.require_private(path.parent, directory=True)
        files.require_private(path, directory=False)
        opened.append(path)
        return original(path)
    monkeypatch.setattr(capture.readonly, "_open_private", private_open)
    with captured_home(mirror_env) as observed, sql_copies(observed, tmp_path / "private-sql-payload") as copies:
        capture.verify_sql_payload(observed.selection, copies)
    assert len(opened) == 2
    assert all(not path.parent.exists() for path in opened)


def test_personal_temporary_initializer_never_repermissions_external_or_nonempty_files(tmp_path):
    external = tmp_path / "outside-original.bin"
    external.write_bytes(b"original external bytes")
    before = files.identity(external.stat(follow_symlinks=False))
    temporary = capture._PersonalPrivateDirectory()
    try:
        with pytest.raises(contract.Error, match="PRIVATE_IMAGE_UNSAFE"):
            temporary.initialize_file(external)
        assert files.identity(external.stat(follow_symlinks=False)) == before
        body = temporary.path / "already-written.sqlite"
        with body.open("xb") as stream:
            stream.write(b"already written bytes")
        with pytest.raises(contract.Error, match="PRIVATE_IMAGE_NOT_EMPTY"):
            temporary.initialize_file(body)
        assert body.read_bytes() == b"already written bytes"
    finally:
        temporary.cleanup()


@pytest.mark.skipif(os.name == "nt", reason="Windows held ordinary reader forbids this write")
def test_final_hash_read_also_rechecks_original_full_identity(mirror_env, monkeypatch):
    env = mirror_env
    original = capture._HeldFile._read
    changed = False
    with pytest.raises(RuntimeError, match="controlled retained original change"):
        with captured_home(env) as observed:
            node = observed.files[role_file(observed.selection, "node_identity_raw")["entry_id"]]
            def read_then_change(held, *, collect):
                nonlocal changed
                result = original(held, collect=collect)
                if held is node and not changed:
                    changed = True
                    info = held.path.stat()
                    os.utime(held.path, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000))
                return result
            with monkeypatch.context() as patch:
                patch.setattr(capture._HeldFile, "_read", read_then_change)
                with pytest.raises(contract.Error, match="SOURCE_CHANGED"):
                    node.verify()
            # An existing primary leaves this altered original intact and
            # suppresses success-exit verification. No test restores it.
            raise RuntimeError("controlled retained original change")
