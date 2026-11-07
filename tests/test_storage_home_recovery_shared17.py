"""Actual17 shared root/backup/checkpoint integration; isolated libraries only."""
from contextlib import contextmanager
from importlib import import_module
import json
from pathlib import Path
import sqlite3
import time
from types import SimpleNamespace
import uuid

import pytest

from reno import home_recovery17 as recovery17, home_recovery_schema as home_schema
from reno import storage_artifacts as artifacts, storage_backups as backups
from reno import storage_backup_roots as backup_roots, storage_checkpoint_roots as checkpoint_roots
from reno import storage_gc_contract as contract, storage_home_recovery_roots as home_roots
from reno import storage_lifecycle as lifecycle, storage_members as members
from reno import storage_readonly as readonly, storage_root_inputs as inputs, storage_roots as roots
from reno import source_preservation_bundle as bundles, source_preservation_witness as witness
from reno.sync_protocol import SyncError
from tests import test_home_recovery_home as legacy_home
from tests.test_home_recovery17 import old16_environment, actual_home16, upgrade17, read_sql, receipt, canonical, digest, damage_new_row
from tests.test_home_recovery_source17 import create as create_source, paths as source_paths
from tests.test_source_preservation_bundle import produce as produce_bundle, documents as bundle_documents
from tests.test_storage_roots_group import enroll


def attach_group(value):
    profile = value.profile
    con = sqlite3.connect(profile.database)
    con.row_factory = sqlite3.Row
    library = con.execute("SELECT value FROM meta WHERE key='library_id'").fetchone()[0]
    node = json.loads((profile.database.parent / "node-identity.json").read_bytes())["instance_id"]
    with lifecycle.shared(profile):
        root_ids = {ns: artifacts.root_identity(getattr(profile, roots._ROOT_ROLE[ns]), profile=profile)["root_id"]
                    for ns in roots.NAMESPACES}
    group = {"profile": profile, "con": con, "library_id": library, "node_id": node,
             "member_id": "mem_" + "a" * 64, "root_ids": root_ids}
    enroll(group)
    value.group = group
    return value


@pytest.fixture
def env(actual_home16):
    value = attach_group(actual_home16)
    try:
        yield upgrade17(value)
    finally:
        value.group["con"].close()


@pytest.fixture
def restored(env):
    sdk = import_module("reno.home_recovery_source17")
    env.selected_source, _ = create_source(sdk, env)
    with sqlite3.connect(env.profile.database) as con:
        con.execute("UPDATE knowledge_atom SET claim='共享读者恢复前的新知识' WHERE id='home_atom'")
    observed = recovery17.preview(profile=env.profile, source_id=env.selected_source)
    env.restore_id = str(uuid.uuid4())
    env.restore_result = recovery17.apply(profile=env.profile, source_id=env.selected_source,
        expected_digest=observed["digest"], operation_id=env.restore_id)
    env.restore_row = receipt(env, env.restore_id)
    return env


def options(value, **extra):
    group = value.group
    return {key: group[key] for key in ("root_ids", "member_id", "library_id", "node_id")} | {
        "policy": contract.default_policy(), **extra}


@contextmanager
def live_capture(value, **kwargs):
    with members._group([value.profile], False) as (expanded, _, _):
        with roots.capture_locked(expanded, owner_profile=value.profile, **kwargs) as captured:
            yield captured


@contextmanager
def backup_capture(value, **kwargs):
    with lifecycle.shared(value.profile):
        with backup_roots.capture_locked(value.profile, **options(value, **kwargs)) as captured:
            yield captured


@contextmanager
def checkpoint_capture(value, **kwargs):
    with lifecycle.shared(value.profile), readonly.committed_image_locked(value.profile) as image:
        with checkpoint_roots.capture_image_locked(image.connection, profile=value.profile,
                **options(value, **kwargs)) as captured:
            yield captured
        image.verify_source()


def bindings(scan, section, *, state=None, source_kind=None):
    return [item for item in scan.source_bindings if item.section == section
            and (state is None or item.state == state) and (source_kind is None or item.source_kind == source_kind)]


def maintenance_image(value, label):
    identifier = str(uuid.uuid4())
    destination = value.profile.database.with_name(label + "-" + identifier + ".sqlite")
    with value.store.reference_operation():
        with witness.sql_window_locked(profile=value.profile, store=value.store, writable=False) as con:
            con.execute("PRAGMA query_only=ON")
            result = backups.create(con, destination, profile=value.profile, kind="maintenance", backup_id=identifier)
    return identifier, destination, result


def test_actual17_root_input_and_checkpoint_parsers_accept_original_wrapper_with_public_same_image_provenance(restored):
    value = restored
    before, state = read_sql(value), value.store.read()
    with lifecycle.shared(value.profile), readonly.committed_image_locked(value.profile) as image:
        scan = inputs.parse_image(image.connection, profile=value.profile, root_ids=value.group["root_ids"],
            member_id=value.group["member_id"])
        assert bindings(scan, home_roots.TABLE, state="present")
        assert bindings(scan, "source_provenance", state="present"), bindings(scan, "source_provenance")
        assert not any(item.code == "HOME_RECOVERY_ROOT_INVALID" for item in scan.blockers)
        image.verify_source()
    with checkpoint_capture(value) as captured:
        assert bindings(captured.scan.inputs, home_roots.TABLE)
        assert not any(item.code == "HOME_RECOVERY_ROOT_INVALID" for item in captured.scan.inputs.blockers)
    assert read_sql(value) == before and value.store.read() == state


@pytest.mark.parametrize("retire_source_metadata", [False, True])
def test_actual17_live_graph_uses_original_bundle_and_keeps_closed_dependencies_after_source_metadata_retirement(restored, retire_source_metadata):
    value = restored
    if retire_source_metadata:
        for path in source_paths(value, value.selected_source):
            path.unlink()
    before, state = read_sql(value), value.store.read()
    with live_capture(value) as captured:
        assert bindings(captured.scan.base_inputs, "home17_dependency_files", state="closed", source_kind="live")
        assert not any(item.code in {"HOME_RECOVERY_ROOT_INVALID", "HOME17_SOURCE_IMAGE_UNPROVEN",
            "HOME17_BUNDLE_DEPENDENCY_UNPROVEN"} for item in captured.scan.base_inputs.blockers)
        captured.verify_source()
    assert read_sql(value) == before and value.store.read() == state


def test_registered_actual17_backup_graph_reads_the_snapshot_wrapper_and_its_own_provenance(restored):
    value = restored
    backup_id, image, _ = maintenance_image(value, "shared17-completed")
    original = image.read_bytes()
    with backup_capture(value) as captured:
        assert bindings(captured.inputs, "home17_dependency_files", state="closed", source_kind="backup")
        assert not any(item.code == "HOME_RECOVERY_ROOT_INVALID" for item in captured.inputs.blockers)
        assert any(graph for graph in captured.checkpoint_graphs)
        captured.verify_source()
    assert image.read_bytes() == original
    assert (backups._catalog(value.profile) / (backup_id + ".json")).exists()


def test_backup_provenance_is_from_that_original_image_and_cannot_be_repaired_by_newer_live_sql(restored):
    value = restored
    original = receipt(value, value.restore_id)
    body = json.loads(original["result_json"])
    body["knowledge_sha256"] = "8" * 64
    wrong = damage_new_row(value, value.restore_id, {"result_json": canonical(body), "result_sha256": digest(body)})
    assert wrong["result_sha256"] != original["result_sha256"]
    _backup_id, image, _ = maintenance_image(value, "shared17-wrong-original-provenance")
    damage_new_row(value, value.restore_id, {"result_json": original["result_json"], "result_sha256": original["result_sha256"]})
    assert receipt(value, value.restore_id) == original
    with sqlite3.connect(image.as_uri() + "?mode=ro&immutable=1", uri=True) as archived:
        assert archived.execute("SELECT result_sha256 FROM " + home_roots.TABLE).fetchone()[0] == wrong["result_sha256"]
    with backup_capture(value) as captured:
        assert bindings(captured.inputs, "home17_dependency_files", state="unproven", source_kind="backup")
        assert not bindings(captured.inputs, "home17_dependency_files", state="closed", source_kind="backup")
    with live_capture(value) as captured:
        assert bindings(captured.scan.base_inputs, "home17_dependency_files", state="closed", source_kind="live")


@pytest.fixture
def mixed17(old16_environment):
    from types import SimpleNamespace
    old = old16_environment
    store = legacy_home.state(old)
    legacy_home.changed(old)
    original_id, _ = legacy_home.apply(old)
    value = attach_group(SimpleNamespace(profile=old[0], source_id=old[1], store=store))
    try:
        original_row = receipt(value, original_id)
        upgrade17(value)
        observed = recovery17.preview(profile=value.profile, source_id=value.source_id)
        new_id = str(uuid.uuid4())
        recovery17.apply(profile=value.profile, source_id=value.source_id, expected_digest=observed["digest"], operation_id=new_id)
        value.original_id, value.original_row, value.new_id = original_id, original_row, new_id
        yield value
    finally:
        value.group["con"].close()


def test_actual_old_bare_and_new_wrapper_rows_keep_separate_formats_in_same17_shared_image(mixed17):
    value = mixed17
    assert len(json.loads(value.original_row["checkpoint_json"])) == 9
    assert json.loads(receipt(value, value.new_id)["checkpoint_json"])["format_version"] == 2
    with live_capture(value) as captured:
        assert bindings(captured.scan.base_inputs, "home17_dependency_files", state="closed")
        assert not any(item.code == "HOME_RECOVERY_ROOT_INVALID" for item in captured.scan.base_inputs.blockers)
    assert receipt(value, value.original_id) == value.original_row


@pytest.mark.parametrize("reader_kind", ["input", "live", "checkpoint"])
def test_actual_shared_authorizers_explicitly_deny_every_private_home_column(restored, reader_kind):
    value = restored
    private_columns = set(home_schema.COLUMNS) - set(home_roots.COLUMNS) - {"result_sha256"}
    with lifecycle.shared(value.profile), readonly.committed_image_locked(value.profile) as image:
        con = image.connection
        if reader_kind == "input":
            reader = inputs._Reader(con, inputs.InputLimits())
            reader.active = home_roots.TABLE
            reader.provenance17 = True
            con.set_authorizer(reader.authorize)
            finish = lambda: con.set_authorizer(None)
        elif reader_kind == "live":
            reader = roots._Queries(con, roots._Budget(roots.RootLimits(), None))
            reader.__enter__()
            reader.provenance17 = True
            finish = lambda: reader.__exit__(None, None, None)
        else:
            reader = checkpoint_roots._Reader(con, checkpoint_roots._Budget(checkpoint_roots.CheckpointLimits()))
            reader.install()
            finish = reader.finish
        try:
            # Read the real public projection first; a blanket SQL denial is
            # not evidence that the adapter preserved its declared contract.
            if reader_kind == "input":
                assert home_roots.normalize_projection(reader.table(home_roots.TABLE), schema_version=17)
            else:
                assert home_roots.read_projection(con)
            for column in sorted(private_columns):
                with pytest.raises(sqlite3.DatabaseError):
                    con.execute('SELECT "' + column + '" FROM main."' + home_roots.TABLE + '"').fetchall()
        finally:
            finish()
        image.verify_source()


@pytest.mark.parametrize("change", ["file", "directory"])
def test_actual_shared_graph_final_cas_rejects_noncooperating_producer_file_or_directory_changes(restored, change):
    value = restored
    recovery_directory = value.profile.database.parent / recovery17.DIRECTORY
    target = recovery_directory / (value.restore_id + ".receipt.json")
    wrapper = json.loads(value.restore_row["checkpoint_json"])
    directory = value.profile.database.parent / bundles.DIRECTORY / wrapper["bundle"]["bundle_id"] / "files"
    replacement = value.profile.database.parent / "replacement-original-receipt.json"
    if change == "file":
        replacement.write_bytes(target.read_bytes())
        replacement.chmod(target.stat().st_mode & 0o777)
    changed, blocked = False, False
    try:
        with live_capture(value) as captured:
            try:
                if change == "file":
                    replacement.replace(target)
                else:
                    (directory / (str(uuid.uuid4()) + ".unexpected-copy")).write_bytes(b"{}")
                changed = True
            except PermissionError:
                blocked = True
            captured.verify_source()
    except (roots.RootError, home_roots.HomeRecoveryRootError, SyncError) as error:
        assert changed and error.code in {"ROOTS_SOURCE_CHANGED", "HOME17_DEPENDENCY_CHANGED", "SYNC_CHECKPOINT_DAMAGED"}
        if isinstance(error, roots.RootError):
            assert error.retriable
        elif isinstance(error, SyncError):
            assert error.code == "SYNC_CHECKPOINT_DAMAGED" and error.status == 409
    else:
        assert blocked and not changed


@pytest.mark.parametrize("consumer", ["live", "backup", "checkpoint"])
def test_caller_cancellation_after_shared_capture_keeps_original_exception_and_live_facts(restored, consumer):
    value = restored
    before, state = read_sql(value), value.store.read()
    marker = ValueError("explicit-shared17-caller-cancel")
    armed = False

    def cancelled():
        if armed:
            raise marker

    context = {"live": live_capture, "backup": backup_capture, "checkpoint": checkpoint_capture}[consumer]
    with pytest.raises(ValueError) as caught:
        with context(value, check_cancelled=cancelled) as captured:
            armed = True
            captured.verify_source()
    assert caught.value is marker
    assert read_sql(value) == before and value.store.read() == state


@pytest.mark.parametrize("row_formats", ["new", "mixed"])
@pytest.mark.parametrize("verify_part", ["outer_check", "nested_pins"])
def test_public_recovery_root_verify_inside_context_raises_original_one_shot_cancellation(request, row_formats, verify_part):
    value = request.getfixturevalue("restored" if row_formats == "new" else "mixed17")
    before, state = read_sql(value), value.store.read()
    marker = ValueError("inside-public-shared17-caller-cancel")
    armed, raised, calls = False, False, 0
    # The first callback is the public combined check. The second belongs to
    # its actual child capture, after that check passes. The new child adds a
    # second cancellation wrapper; the mixed branch also keeps the old path.
    cancel_at = 1 if verify_part == "outer_check" else 2

    def cancelled():
        nonlocal raised, calls
        if armed:
            calls += 1
        if armed and calls == cancel_at and not raised:
            raised = True
            raise marker

    with lifecycle.shared(value.profile), readonly.committed_image_locked(value.profile) as image:
        rows = home_roots.read_projection(image.connection)
        provenance = {row[0]: row[1] for row in image.connection.execute(
            'SELECT operation_id,result_sha256 FROM main."' + home_roots.TABLE + '"')}
        with home_roots.capture_locked(rows, profile=value.profile, **{key: value.group[key]
                for key in ("root_ids", "member_id", "library_id", "node_id")},
                schema_version=17, provenance=provenance, check_cancelled=cancelled) as captured:
            assert type(captured) is home_roots.RecoveryRootCapture
            armed = True
            with pytest.raises(ValueError) as caught:
                captured.verify_source()
            assert caught.value is marker and raised
            captured.verify_source()
        image.verify_source()
    assert read_sql(value) == before and value.store.read() == state


@pytest.mark.parametrize("elapsed_seconds", [11, 121])
def test_real_source17_sdk_keeps_maintenance_witness_for_11_seconds_but_rejects_over_120(env, monkeypatch, elapsed_seconds):
    from reno import storage_source_continuity_roots as cloud_roots
    from reno import storage_source_authority_roots as authority_roots

    value = env
    sdk = import_module("reno.home_recovery_source17")
    source_id = str(uuid.uuid4())
    before, state = read_sql(value), value.store.read()
    original_create = backups.create
    offset = 0
    captured, rejected = [], []
    # Inject only the original capsule readers' clocks. The source SDK keeps
    # its real independent total-work budget and SQL progress handler.
    clock = SimpleNamespace(monotonic=lambda: time.monotonic() + offset)

    def delayed_actual_backup(*args, **kwargs):
        nonlocal offset
        actual = witness._CURRENT.get()
        assert type(actual) is witness.WitnessCapture
        assert actual.capsules()["home_authority"]["presence"] == "present"
        captured.append(actual)
        offset = elapsed_seconds
        try:
            # This is the SDK's original sealed witness and owned window.
            # Only elapsed clock time is injected after its real capture.
            actual.verify_source()
        except cloud_roots.ContinuityRootError as error:
            rejected.append(error.code)
            offset = 0  # restore the artificial clock before normal unwinding
            raise
        return original_create(*args, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(cloud_roots, "time", clock)
        scoped.setattr(authority_roots, "time", clock)
        scoped.setattr(backups, "create", delayed_actual_backup)
        if elapsed_seconds > 120:
            with pytest.raises(sdk.HomeRecoverySourceError, match="SOURCE_INVALID"):
                sdk.create_checkpoint(profile=value.profile, source_id=source_id)
        else:
            result = sdk.create_checkpoint(profile=value.profile, source_id=source_id)
            assert result["source_id"] == source_id and result["source_kind"] == "home_checkpoint"
            assert json.loads(source_paths(value, source_id)[0].read_bytes())["schema_version"] == 17
    assert len(captured) == 1
    if elapsed_seconds > 120:
        assert rejected == ["CONTINUITY_ROOT_DEADLINE_LIMIT"]
        assert not source_paths(value, source_id)[0].exists()
    else:
        assert rejected == []
        observed = sdk.read_checkpoint(profile=value.profile, source_id=source_id)
        assert observed["source_id"] == source_id and observed["source_sha256"] == result["source_sha256"]
    assert read_sql(value) == before and value.store.read() == state


def retain_maintenance(value):
    return backups.retain_recent(value.profile, kind="maintenance", keep=1, operation_id=str(uuid.uuid4()))


def assert_precise_retention(value, protected, garbage, original_files):
    result = retain_maintenance(value)
    assert not set(result["retired"]) & set(protected)
    assert garbage[0][0] in result["retired"]
    assert garbage[1][0] not in result["retired"]
    assert all(path.read_bytes() == raw for path, raw in original_files.items())
    return result


def test_standalone_completed_bundle_holds_its_original_backup_under_explicit_maintenance_retention(env):
    value = env
    result = produce_bundle(bundles, value)
    manifest, _certificate = bundle_documents(result)
    protected = manifest["database_backup"]["backup_id"]
    catalog = backups._catalog(value.profile) / (protected + ".json")
    image = Path(json.loads(catalog.read_bytes())["destination"])
    original_files = {path: path.read_bytes() for path in (catalog, image, image.with_name(image.name + backups.SUFFIX))}
    garbage = [maintenance_image(value, "unrelated-standalone-" + str(index)) for index in range(2)]
    assert_precise_retention(value, {protected}, garbage, original_files)


def test_real_request_with_completed_bundle_before_sql_commit_holds_backup_during_maintenance_retention(env, monkeypatch):
    value = env
    sdk = import_module("reno.home_recovery_source17")
    selected, _ = create_source(sdk, value)
    observed = recovery17.preview(profile=value.profile, source_id=selected)
    operation = str(uuid.uuid4())
    before, state = read_sql(value), value.store.read()
    original_create = bundles.create
    marker = RuntimeError("completed original bundle before Home SQL writer")
    manifest = None

    def completed_then_stop(*args, **kwargs):
        nonlocal manifest
        result = original_create(*args, **kwargs)
        if kwargs.get("operation_id") == operation:
            manifest, _ = bundle_documents(result)
            raise marker
        return result

    with monkeypatch.context() as scoped:
        scoped.setattr(bundles, "create", completed_then_stop)
        with pytest.raises(RuntimeError) as caught:
            recovery17.apply(profile=value.profile, source_id=selected, expected_digest=observed["digest"], operation_id=operation)
    assert caught.value is marker and manifest is not None and receipt(value, operation) is None
    assert read_sql(value) == before and value.store.read() == state
    protected = manifest["database_backup"]["backup_id"]
    catalog = backups._catalog(value.profile) / (protected + ".json")
    image = Path(json.loads(catalog.read_bytes())["destination"])
    request = value.profile.database.parent / recovery17.DIRECTORY / (operation + ".request.json")
    original_files = {path: path.read_bytes() for path in (request, catalog, image, image.with_name(image.name + backups.SUFFIX))}
    garbage = [maintenance_image(value, "unrelated-before-sql-" + str(index)) for index in range(2)]
    assert_precise_retention(value, {protected}, garbage, original_files)


def test_completed_restore_retired_source_metadata_keeps_exact_images_and_allows_unrelated_maintenance_retirement(restored, monkeypatch):
    value = restored
    for path in source_paths(value, value.selected_source):
        path.unlink()
    source_backup = str(uuid.uuid5(uuid.UUID(value.selected_source), "reno.home.knowledge.checkpoint.v1"))
    protected = {source_backup, value.restore_row["protection_backup_id"]}
    original_files = {}
    for identifier in protected:
        catalog = backups._catalog(value.profile) / (identifier + ".json")
        image = Path(json.loads(catalog.read_bytes())["destination"])
        original_files.update({path: path.read_bytes() for path in (catalog, image, image.with_name(image.name + backups.SUFFIX))})
    garbage = [maintenance_image(value, "unrelated-retired-source-" + str(index)) for index in range(2)]
    original_read = home_roots.read_projection
    probed = 0

    def public_projection_with_private_probe(con, **kwargs):
        nonlocal probed
        for column in ("request_json", "result_json", "frontier_json", "before_version_json", "after_version_json"):
            with pytest.raises(sqlite3.DatabaseError):
                con.execute('SELECT "' + column + '" FROM main."' + home_roots.TABLE + '"').fetchall()
        probed += 1
        return original_read(con, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(home_roots, "read_projection", public_projection_with_private_probe)
        assert_precise_retention(value, protected, garbage, original_files)
    assert probed > 0
