"""Private permanent records with real isolated State/SQL/process ownership.

Controlled test peers only; no source/provider/remote request is dispatched.
"""
from contextlib import contextmanager
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import hashlib
import json
import os
import threading
import uuid

import pytest

from reno import db, storage_lifecycle as lifecycle, sync_state, sync_source_authority as authority
from reno import sync_source_readmit_contract as wire, sync_source_execution as execution, sync_snapshot
from reno.sync_protocol import SyncError
from tests.test_sync_agent import env as original_env, install_import, add_video
from tests.schema_fixtures import released16
from tests.test_sync_budget_propagation import home_cycle
from tests.test_sync_source_execution_binding import executing, bind
from tests.test_sync_source_readmit_contract import COMMITTED
from tests import test_sync_source_readmit_contract as pure_wire


@pytest.fixture
def env(tmp_path, monkeypatch):
    # This retained producer predates the actual continuity/genesis contract.
    # Select release16 before cold SQL/State/worker initialization and preserve
    # that lifetime through the donor's complete owner/worker teardown.
    with released16(monkeypatch):
        yield from original_env.__wrapped__(tmp_path, monkeypatch)


def snapshot(profile):
    names = (authority.INTENT_NAME, authority.DB_HEAD_NAME, authority.SEAL_NAME, authority.CHECKPOINT_HEAD_NAME)
    return {str(path): (path.stat().st_mtime_ns, path.read_bytes())
        for root in (profile.database.parent, profile.checkpoints) for path in root.rglob("*")
        if path.is_file() and (path.name in names or authority.DIRECTORY in path.parts)}


def activation(store):
    return {"format_version": 1, "facility_id": str(uuid.uuid4()), "owner_profile_sha256": authority.profile_sha256(store.profile),
        "binding": deepcopy(store.read()["binding"]), "activated_at": COMMITTED}


@pytest.fixture
def plain(tmp_path):
    profile = lifecycle.StorageProfile(tmp_path / "data" / "reno.db", tmp_path / "media", tmp_path / "reports", tmp_path / "checkpoints")
    lifecycle.initialize(profile)
    store = sync_state.StateStore(profile.database.parent, profile=profile)
    binding = {"home_library_id": str(uuid.uuid4()), "home_node_id": str(uuid.uuid4()),
        "target_library_id": str(uuid.uuid4()), "target_node_id": str(uuid.uuid4()), "target_url": "https://peer.example.test"}
    store.initialize(binding, 1)
    return store


@contextmanager
def owner(store, *, initialize=False):
    with store.reference_operation(), authority.owned(store, initialize=initialize) as value:
        yield value


@pytest.fixture
def home(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as cycle, executing(cycle), cycle.store.reference_operation():
        # Actual legacy State3 core installed by the retained pre-genesis
        # contract. Reentry must not manufacture its missing origin.
        state = cycle.store.read()
        core = execution._candidate_core(state, execution._job(cycle.storage_profile), state["claims"][0],
            recovery_generation=cycle.knowledge_generation, recovery_stream=cycle.knowledge_stream)
        cycle.store.enable_source_execution()
        cycle.store.install_source_execution(core)
        binding = bind(cycle)
        context = authority.owned(cycle.store, initialize=True)
        ledger = context.__enter__()
        closed = False
        try:
            active = activation(cycle.store)
            ledger.activate_files(active)
            con = db.connect(cycle.storage_profile.database)
            try:
                con.execute("BEGIN IMMEDIATE")
                def close_ledger():
                    nonlocal closed
                    try:
                        context.__exit__(None, None, None)
                    finally:
                        closed = True
                yield SimpleNamespace(cycle=cycle, binding=binding, ledger=ledger, active=active, con=con, close_ledger=close_ledger)
            finally:
                con.rollback()
                con.close()
        finally:
            if not closed:
                context.__exit__(None, None, None)


def origin(home):
    core = home.binding.core
    core_sha = authority.digest(core)
    return {"format_version": 1, "record_kind": "origin", "record_id": str(uuid.uuid5(uuid.UUID(core["continuation_id"]), "source-origin:v1:" + core_sha)),
        "binding": core["binding"], "core": core, "core_sha256": core_sha,
        "claim": deepcopy(home.cycle.store.read()["claims"][0]["claim"]), "created_at": COMMITTED}


def seed_historical_origin(home):
    """Controlled preexisting files, not evidence of an implemented factory.

    This models an already committed historical origin solely for read/root
    and exact-replay tests. It never supplies permission for fresh dispatch.
    """
    # Import lazily: the roots fixture also consumes this module's pure helpers.
    from tests.test_storage_source_authority_roots import reserve_historical_record, commit_historical_record

    body = origin(home)
    observed = home.ledger.observe()
    reserve_historical_record(home.ledger, observed, "origins", body)
    observed = home.ledger.observe()
    commit_historical_record(home.ledger, observed, "origins", body)
    return body


def creation_basis(home, *, origin_ref=None):
    state = home.cycle.store.read()
    return {"format_version": 1, "kind": "original_core", "core_sha256": authority.digest(home.binding.core),
        "home_epoch": state["local_epoch"], "session": deepcopy(state["lease"]), "claim": deepcopy(state["claims"][0]["claim"]),
        "recovery_generation": state["recovery"]["generation"], "recovery_stream": deepcopy(state["recovery"]["stream"]),
        "control_revision": state["recovery"]["revision"], "origin_ref": origin_ref, "admission_ref": None, "continuity": None}


def control(home, *, origin_ref=None):
    basis = creation_basis(home, origin_ref=origin_ref)
    core = home.binding.core
    body = {"kind": "budget_defer", "request_id": str(uuid.uuid4()), "source": deepcopy(home.cycle.source),
        "target": {"library_id": core["binding"]["target_library_id"], "node_id": core["binding"]["target_node_id"]},
        "lease": basis["session"], "claim": basis["claim"], "recovery_generation": basis["recovery_generation"],
        "recovery_stream": basis["recovery_stream"], "reason": "daily_limit", "retry_at": None, "budget_operation_id": str(uuid.uuid4())}
    # New schema17 creation requires a different authenticated factory. These
    # tests deliberately exercise the retained old creation contract.
    return {"format_version": 1, "record_kind": "control", "record_id": body["request_id"], "core_sha256": authority.digest(core),
        "request_sha256": wire.digest(body), "request": body, "creation_basis": basis}


def expected_before(home):
    state = home.cycle.store.read()
    return {"binding": state["binding"], "local_epoch": state["local_epoch"], "core_sha256": authority.digest(home.binding.core),
        "admission_ref": state.get("source_admission"), "recovery_generation": state["recovery"]["generation"],
        "recovery_stream": state["recovery"]["stream"], "control_revision": state["recovery"]["revision"], "claim": state["claims"][0]["claim"],
        "budget_pending_sha256": None if state.get("budget_pending") is None else authority.digest(sync_state._stable_pending(state["budget_pending"])),
        "source_control_pending_sha256": None if state.get("source_control_pending") is None else authority.digest(sync_state._stable_source_control(state["source_control_pending"])),
        "pending_publication": state["pending_publication"], "source_authority": state.get("source_authority")}


def intent(home, old=None):
    old = deepcopy((old or control(home))["request"])
    core = home.binding.core
    before = expected_before(home)
    body = {"kind": "source_dispose", "request_id": str(uuid.uuid4()), "source": deepcopy(old["source"]), "target": deepcopy(old["target"]),
        "lease": deepcopy(old["lease"]), "recovery_generation": before["recovery_generation"], "recovery_stream": before["recovery_stream"], "control_revision": before["control_revision"],
        "original": {"core_sha256": authority.digest(core), "home_epoch": core["local_epoch"], "item_id": core["item_id"],
            "submitted_url_sha256": core["submitted_url_sha256"], "claim": deepcopy(old["claim"]), "recovery_generation": core["recovery_generation"], "recovery_stream": core["recovery_stream"]},
        "current_home_epoch": before["local_epoch"], "preservation": {"format_version": 1, "frontier_sha256": "a" * 64, "certificate_sha256": "b" * 64,
            "from_epoch": core["local_epoch"], "to_epoch": before["local_epoch"]}, "original_request": old, "registration": None}
    return {"format_version": 1, "record_kind": "recovery_intent", "record_id": body["request_id"], "core_sha256": authority.digest(core),
        "request_sha256": wire.request_sha256(body), "request": body, "expected_before": before, "preservation_basis": None, "manual_resume_basis": None}


def receipt(value):
    body = value["request"]
    old = body["original_request"]
    parent = {"request_id": old["request_id"], "request_sha256": wire.digest(old), "item_id": old["claim"]["item_id"],
        "lease_generation": old["claim"]["lease_generation"], "status": "budget_wait", "attempts": 0, "reason": old["reason"],
        "retry_at": old["retry_at"], "budget_operation_id": old["budget_operation_id"], "committed_at": COMMITTED, "replayed": False}
    result = {"format_version": 1, "request_id": body["request_id"], "request_sha256": wire.digest(body),
        "original_request_id": old["request_id"], "original_request_sha256": wire.digest(old), "original_kind": old["kind"],
        "core_sha256": value["core_sha256"], "item_id": old["claim"]["item_id"], "committed_at": COMMITTED, "replayed": False,
        "disposition": "applied", "original_receipt": parent, "continuity_id": None, "registration_sha256": None}
    return {"format_version": 1, "record_kind": "recovery_receipt", "record_id": value["record_id"], "request_sha256": value["request_sha256"],
        "result": result, "result_sha256": authority.digest(result), "parent_defer": None}


@contextmanager
def rejected(code=None):
    with pytest.raises(SyncError) as caught:
        yield
    if code is not None:
        assert caught.value.code == "SYNC_SOURCE_AUTHORITY_" + code
    assert "/" not in str(caught.value) and "\\" not in str(caught.value)


def test_ordinary_read_never_initializes_and_needs_actual_state_owner(plain):
    before = snapshot(plain.profile)
    with rejected("LOCK_REQUIRED"):
        with authority.owned(plain):
            pass
    with owner(plain) as guard:
        assert guard.observe()["contract"] is None
    assert snapshot(plain.profile) == before


def test_explicit_activation_exact_replay_and_version1_empty_pointer_cannot_hide_it(plain):
    with owner(plain, initialize=True) as guard:
        active = activation(plain)
        assert guard.activate_files(active) == active
    before = snapshot(plain.profile)
    assert plain.read()["format_version"] == 1
    with owner(plain, initialize=True) as guard:
        assert guard.observe()["contract"] == active
        assert guard.activate_files(active) == active
        with rejected("CONTRACT_MISMATCH"):
            guard.activate_files({**active, "facility_id": str(uuid.uuid4())})
    assert snapshot(plain.profile) == before


def test_activation_requires_explicit_initialization_and_stable_binding(plain):
    active = activation(plain)
    with owner(plain) as guard, rejected("INITIALIZATION_REQUIRED"):
        guard.activate_files(active)
    with owner(plain, initialize=True) as guard, rejected("PEER_MISMATCH"):
        guard.activate_files({**active, "binding": {**active["binding"], "target_node_id": str(uuid.uuid4())}})
    assert not (plain.data_dir / authority.INTENT_NAME).exists()


@pytest.mark.parametrize("anchor", [authority.INTENT_NAME, authority.DB_HEAD_NAME, authority.SEAL_NAME, authority.CHECKPOINT_HEAD_NAME, "contract.json"])
def test_missing_anchor_fails_closed(plain, anchor):
    with owner(plain, initialize=True) as guard:
        guard.activate_files(activation(plain))
    path = (plain.profile.checkpoints / anchor if anchor in (authority.SEAL_NAME, authority.CHECKPOINT_HEAD_NAME) else
        plain.data_dir / authority.DIRECTORY / "v1" / anchor if anchor == "contract.json" else plain.data_dir / anchor)
    path.unlink()
    with owner(plain) as guard, rejected():
        guard.observe()


def test_preexisting_origin_exact_uuid_replay_never_supplies_genesis(home):
    body = origin(home)
    with rejected("PRODUCER_REQUIRED"):
        home.ledger.publish("origins", body, producer=True)
    with authority.producer(home.ledger, home.binding, home.con) as proof:
        with rejected("ORIGIN_CREATION_REQUIRED"):
            home.ledger.publish("origins", body, producer=proof)
    seed_historical_origin(home)
    before = snapshot(home.cycle.storage_profile)
    assert home.ledger.publish("origins", body) == body
    assert home.ledger.read(authority.record_ref(body)) == body
    assert snapshot(home.cycle.storage_profile) == before
    changed = {**body, "created_at": "2031-01-01T00:00:00.000Z"}
    with rejected("REQUEST_MISMATCH"):
        home.ledger.publish("origins", changed)


def test_existing_core_binding_cannot_manufacture_missing_origin(home):
    # This is a real reentry into an already persisted State3 core, with real
    # SQL/process ownership. That observation alone is not its genesis witness.
    home.cycle.store.enable_source_control()
    before = snapshot(home.cycle.storage_profile)
    with authority.producer(home.ledger, home.binding, home.con) as proof:
        with rejected("ORIGIN_CREATION_REQUIRED"):
            home.ledger.publish("origins", origin(home), producer=proof)
    assert home.ledger.observe()["entries"]["origins"] == {}
    assert snapshot(home.cycle.storage_profile) == before


def test_sealed_producer_cannot_escape_thread_or_sql_lifetime(home):
    body = control(home)
    with rejected("PRODUCER_REQUIRED"):
        with authority.producer(home.ledger, home.binding, True):
            pass
    with authority.producer(home.ledger, home.binding, home.con) as proof:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with pytest.raises(SyncError):
                pool.submit(home.ledger.publish, "controls", body, producer=proof).result()
    with rejected("PRODUCER_REQUIRED"):
        home.ledger.publish("controls", body, producer=proof)
    home.con.rollback()
    with rejected("SQL_GUARD_REQUIRED"):
        with authority.producer(home.ledger, home.binding, home.con):
            pass


def test_control_exact_creation_basis_and_existing_origin_dependency(home):
    initial = seed_historical_origin(home)
    with authority.producer(home.ledger, home.binding, home.con) as proof:
        body = control(home, origin_ref=authority.record_ref(initial))
        assert home.ledger.publish("controls", body, producer=proof) == body
        altered = deepcopy(body)
        altered["request"]["retry_at"] = "2099-01-01T00:00:00.000Z"
        altered["request_sha256"] = wire.digest(altered["request"])
        with rejected("REQUEST_MISMATCH"):
            home.ledger.publish("controls", altered, producer=proof)
    assert home.cycle.store.read()["source_execution"] == home.binding.core


def test_new17_cannot_borrow_old_binding_seal_or_caller_continuity(home):
    body = control(home)
    body["request"]["source"]["handshake"]["migration_version"] = 17
    body["request_sha256"] = wire.digest(body["request"])
    with authority.producer(home.ledger, home.binding, home.con) as proof:
        with rejected("CONTINUITY_REQUIRED"):
            home.ledger.publish("controls", body, producer=proof)
        body["creation_basis"]["continuity"] = {"format_version": 1, "continuity_id": str(uuid.uuid4()), "contract_sha256": "c" * 64,
            "target_epoch": body["request"]["recovery_stream"]["library_epoch"]}
        with rejected("CONTINUITY_PRODUCER_REQUIRED"):
            home.ledger.publish("controls", body, producer=proof)
    assert home.ledger.observe()["head"]["ordinal"] == 0


def test_recovery_intent_receipt_first_and_exact_original_body(home):
    body = intent(home)
    result = receipt(body)
    with rejected("INTENT_REQUIRED"):
        home.ledger.publish("receipts", result)
    with authority.producer(home.ledger, home.binding, home.con, historical=True) as proof:
        home.ledger.publish("requests", body, producer=proof)
    assert home.ledger.publish("receipts", result) == result
    assert home.ledger.publish("receipts", result) == result
    changed = deepcopy(result)
    changed["result"]["original_receipt"]["attempts"] += 1
    changed["result_sha256"] = authority.digest(changed["result"])
    with rejected("REQUEST_MISMATCH"):
        home.ledger.publish("receipts", changed)


def test_local_provenance_refs_are_typed_and_do_not_change_wire(home):
    body = intent(home)
    request = deepcopy(body["request"])
    body["preservation_basis"] = {"format_version": 1, "producer": "home_knowledge_restore", "operation_id": str(uuid.uuid4()), "receipt_sha256": "d" * 64}
    body["manual_resume_basis"] = {"format_version": 1, "producer": "sync_budget_resume", "operation_id": str(uuid.uuid4()), "receipt_sha256": "e" * 64}
    with authority.producer(home.ledger, home.binding, home.con, historical=True) as proof:
        home.ledger.publish("requests", body, producer=proof)
    assert home.ledger.read(authority.record_ref(body))["request"] == request
    bad = deepcopy(body)
    bad["preservation_basis"]["producer"] = "sync_budget_resume"
    with rejected():
        authority.validate_record("requests", bad, home.active)


def test_missing_committed_record_and_deleted_tail_journal_are_not_absence(home):
    body = seed_historical_origin(home)
    path = home.cycle.store.data_dir / authority.DIRECTORY / "v1" / "origins" / (body["record_id"] + ".json")
    home.close_ledger()  # Windows intentionally forbids unlinking a live pin.
    path.unlink()
    with rejected():
        with authority.owned(home.cycle.store) as fresh:
            fresh.observe()


@pytest.mark.parametrize("stage", ["controls", authority.CHECKPOINT_HEAD_NAME])
def test_interruption_resumes_only_exact_reserved_uuid_body(home, monkeypatch, stage):
    body = control(home)
    write = authority.sync_state._write
    fired = False
    def interrupted(directory, name, *args, **kwargs):
        nonlocal fired
        if not fired and (directory.path.name == stage or name == stage):
            fired = True
            raise OSError("controlled interruption")
        return write(directory, name, *args, **kwargs)
    with authority.producer(home.ledger, home.binding, home.con) as proof:
        monkeypatch.setattr(authority.sync_state, "_write", interrupted)
        with rejected():
            home.ledger.publish("controls", body, producer=proof)
        monkeypatch.setattr(authority.sync_state, "_write", write)
        changed = deepcopy(body)
        changed["request"]["retry_at"] = "2099-01-01T00:00:00.000Z"
        changed["request_sha256"] = wire.digest(changed["request"])
        before = snapshot(home.cycle.storage_profile)
        with rejected():
            home.ledger.publish("controls", changed, producer=proof)
        assert snapshot(home.cycle.storage_profile) == before
        assert home.ledger.publish("controls", body, producer=proof) == body
    assert home.ledger.observe()["head"]["ordinal"] == 2


def test_publication_descriptor_preserves_outbox_bytes_and_hash(home):
    # Populate committed fixture media before taking the guarded SQL snapshot.
    home.con.rollback()
    path_to_media = add_video("authority-video")
    home.con.execute("BEGIN IMMEDIATE")
    media_bytes = path_to_media.read_bytes()
    descriptor = {"video_id": "authority-video", "sha256": hashlib.sha256(media_bytes).hexdigest(), "byte_size": len(media_bytes), "extension": "mp4"}
    snapshot_value = sync_snapshot.export_snapshot(home.con, publication_id=str(uuid.uuid4()), media={"authority-video": descriptor})
    claim = {"video_id": "authority-video", "claim": deepcopy(home.cycle.store.read()["claims"][0]["claim"])}
    metadata = home.cycle.store.write_publication(snapshot_value, [claim], recovery_generation=home.cycle.knowledge_generation, recovery_stream=home.cycle.knowledge_stream)
    path = home.cycle.store.data_dir / "sync-outbox" / (metadata["publication_id"] + ".json")
    raw = path.read_bytes()
    body = {"format_version": 1, "record_kind": "publication", "record_id": metadata["publication_id"], "metadata": metadata,
        "core_sha256": authority.digest(home.binding.core), "claims_sha256": authority.digest([claim]), "creation_basis": creation_basis(home)}
    with authority.producer(home.ledger, home.binding, home.con) as proof:
        assert home.ledger.publish("publications", body, producer=proof) == body
    assert path.read_bytes() == raw and metadata["sha256"] == hashlib.sha256(raw).hexdigest()


def test_bounds_bad_json_and_ref_cannot_publish_or_create_record(home, monkeypatch):
    before = snapshot(home.cycle.storage_profile)
    bad = origin(home)
    bad["created_at"] = "sensitive/path"
    with rejected():
        authority.validate_record("origins", bad, home.active)
    with rejected():
        authority.digest({"x": float("nan")})
    with rejected():
        authority.digest({"x": "x" * (authority.MAX_RECORD_BYTES + 1)})
    monkeypatch.setattr(authority, "MAX_RECORDS", 0)
    with authority.producer(home.ledger, home.binding, home.con) as proof, rejected("COUNT_LIMIT"):
        home.ledger.publish("controls", control(home), producer=proof)
    assert snapshot(home.cycle.storage_profile) == before


def test_actual_sql_owner_loss_is_not_repaired_from_thread_context(home):
    home.con.execute("UPDATE job SET owner_id=? WHERE job_id=?", (str(uuid.uuid4()), home.binding.core["root_job_id"]))
    before = snapshot(home.cycle.storage_profile)
    with pytest.raises(Exception) as caught:
        with authority.producer(home.ledger, home.binding, home.con):
            pass
    assert getattr(caught.value, "code", None) in {"TASK_OWNER_LOST", "SYNC_SOURCE_AUTHORITY_INVALID"}
    assert snapshot(home.cycle.storage_profile) == before


def test_original_epoch_change_keeps_facility_id_and_cannot_create_new_origin(home):
    core = deepcopy(home.binding.core)
    home.con.execute("UPDATE meta SET value='2' WHERE key='library_epoch'")
    home.cycle.store.update({"local_epoch": 2})
    assert home.ledger.observe()["contract"] == home.active
    assert home.cycle.store.read()["source_execution"] == core
    with pytest.raises(Exception):
        with authority.producer(home.ledger, home.binding, home.con):
            pass
    assert home.ledger.observe()["entries"]["origins"] == {}


@pytest.mark.parametrize("target", ["journal", "head"])
def test_removed_journal_or_rewound_single_head_is_not_an_empty_ledger(home, target):
    zero = (home.cycle.store.data_dir / authority.DB_HEAD_NAME).read_bytes()
    seed_historical_origin(home)
    home.close_ledger()
    if target == "journal":
        (home.cycle.store.data_dir / authority.DIRECTORY / "v1" / "journal" / "00000002.json").unlink()
    else:
        (home.cycle.storage_profile.checkpoints / authority.CHECKPOINT_HEAD_NAME).write_bytes(zero)
    with rejected():
        with authority.owned(home.cycle.store) as fresh:
            fresh.observe()


def test_unexpected_namespace_file_is_rejected_without_adoption(plain):
    with owner(plain, initialize=True) as guard:
        guard.activate_files(activation(plain))
    path = plain.data_dir / authority.DIRECTORY / "v1" / "secrets-do-not-adopt.json"
    path.write_text("{}", encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o600)
    before = snapshot(plain.profile)
    with rejected("UNEXPECTED_FILE"):
        with owner(plain) as guard:
            guard.observe()
    assert snapshot(plain.profile) == before


@pytest.mark.parametrize("folder", ["", "journal", "controls"])
def test_owned_exit_verifies_observed_namespace_closure(plain, folder):
    with owner(plain, initialize=True) as guard:
        guard.activate_files(activation(plain))
    with rejected("NAMESPACE_CHANGED"):
        with owner(plain) as guard:
            guard.observe()
            path = plain.data_dir / authority.DIRECTORY / "v1" / folder / "unobserved.json"
            path.write_text("{}", encoding="utf-8")
            if os.name != "nt":
                path.chmod(0o600)


def test_original_reference_frame_cannot_be_borrowed_after_end_and_reentry(plain):
    # Keep the same actual process-directory and outer SH objects throughout.
    # Only the inner State reference lifetime ends and starts again.
    with plain.exclusive(), lifecycle.shared(plain.profile):
        first = plain.reference_operation()
        first.__enter__()
        context = authority.owned(plain, initialize=True)
        guard = context.__enter__()
        guard.activate_files(activation(plain))
        original_directory = plain._directory
        original_frame = plain._reference_frame
        original_storage_guard = guard.lock.storage_guard
        first.__exit__(None, None, None)
        with plain.reference_operation():
            assert plain._directory is original_directory
            assert lifecycle._CURRENT.get() is original_storage_guard
            assert plain._reference_frame is not original_frame
            with rejected("LOCK_REQUIRED"):
                guard.observe()
            with rejected("LOCK_REQUIRED"):
                context.__exit__(None, None, None)


def test_actual_authority_directory_replacement_cannot_escape_ancestor_checks(plain):
    with owner(plain, initialize=True) as guard:
        guard.activate_files(activation(plain))
    with plain.reference_operation():
        context = authority.owned(plain)
        guard = context.__enter__()
        guard.observe()
        path = plain.data_dir / authority.DIRECTORY / "v1" / "controls"
        saved = path.with_name("controls-original")
        try:
            path.rename(saved)
        except OSError:
            # Windows directory handles deny deletion/renaming while pinned.
            assert os.name == "nt" and path.is_dir() and not saved.exists()
            guard.observe()
            context.__exit__(None, None, None)
        else:
            path.mkdir(mode=0o700)
            with rejected():
                guard.observe()
            with rejected():
                context.__exit__(None, None, None)


def test_reacquired_actual_shared_guard_cannot_borrow_original_authority(plain):
    with plain.exclusive():
        shared = lifecycle.shared(plain.profile)
        original = shared.__enter__()
        reference = plain.reference_operation()
        reference.__enter__()
        context = authority.owned(plain, initialize=True)
        guard = context.__enter__()
        guard.activate_files(activation(plain))
        frame, directory = plain._reference_frame, plain._directory
        shared.__exit__(None, None, None)
        assert not original._active
        with lifecycle.shared(plain.profile) as replacement:
            assert replacement is not original and replacement.profile.identity == original.profile.identity
            assert plain._reference_frame is frame and plain._directory is directory
            with rejected("LOCK_REQUIRED"):
                guard.observe()
            with rejected("LOCK_REQUIRED"):
                context.__exit__(None, None, None)
            with pytest.raises(lifecycle.StorageLifecycleError):
                reference.__exit__(None, None, None)


def test_replaced_actual_state_rlock_cannot_borrow_original_authority(plain):
    with owner(plain, initialize=True) as guard:
        guard.activate_files(activation(plain))
        original = plain._guard
        frame, directory = plain._reference_frame, plain._directory
        replacement = threading.RLock()
        with replacement:
            plain._guard = replacement
            try:
                assert original._is_owned() and replacement._is_owned()
                assert plain._reference_frame is frame and plain._directory is directory
                with rejected("LOCK_REQUIRED"):
                    guard.observe()
            finally:
                plain._guard = original


def test_directory_creation_rechecks_actual_lifecycle_marker_before_mkdir(plain):
    with owner(plain, initialize=True) as guard:
        guard.activate_files(activation(plain))
        observed = guard.observe()
        parent = observed["ledger"]
        target = parent.path / "not-created-with-invalid-sh-marker"
        lock = guard.lock.storage_guard._locks[0]
        marker = lock.directory.path / lifecycle.LOCK_NAME
        raw = marker.read_bytes()
        # The OS locks byte zero. Changing the final unreserved byte exercises
        # actual marker integrity on both platforms while keeping size/path.
        with marker.open("r+b") as stream:
            stream.seek(len(raw) - 1)
            stream.write(bytes([raw[-1] ^ 1]))
            stream.flush()
            os.fsync(stream.fileno())
            try:
                with pytest.raises(lifecycle.StorageLifecycleError) as caught:
                    guard._directory(parent, target.name, create=True)
                assert caught.value.code == "STORAGE_LOCK_INVALID"
                assert not target.exists()
            finally:
                stream.seek(len(raw) - 1)
                stream.write(raw[-1:])
                stream.flush()
                os.fsync(stream.fileno())


def test_postpublication_error_requires_fresh_owner_and_exact_reserved_body(home, monkeypatch):
    body = control(home)
    write = authority.sync_state._write
    fired = False
    def interrupted_after_publish(directory, name, *args, **kwargs):
        nonlocal fired
        result = write(directory, name, *args, **kwargs)
        if not fired and directory.path.name == "controls":
            fired = True
            raise OSError("controlled postpublication failure")
        return result
    with authority.producer(home.ledger, home.binding, home.con) as proof:
        monkeypatch.setattr(authority.sync_state, "_write", interrupted_after_publish)
        with rejected():
            home.ledger.publish("controls", body, producer=proof)
        monkeypatch.setattr(authority.sync_state, "_write", write)
        with rejected("NAMESPACE_CHANGED"):
            home.ledger.publish("controls", body, producer=proof)
    with rejected("NAMESPACE_CHANGED"):
        home.close_ledger()
    with authority.owned(home.cycle.store) as fresh:
        assert fresh.observe()["incomplete"] == [("controls", body["record_id"])]
        with authority.producer(fresh, home.binding, home.con) as proof:
            assert fresh.publish("controls", body, producer=proof) == body
        assert fresh.observe()["head"]["ordinal"] == 2


def test_applied_resume_parent_is_actual_and_survives_state_wait_clear(home):
    old = control(home)
    store = home.cycle.store
    store.install_budget_defer_request(old["request"])
    parent_receipt = receipt(intent(home, old))["result"]["original_receipt"]
    store.record_budget_defer_receipt(old["record_id"], parent_receipt)
    resume = {key: deepcopy(old["request"][key]) for key in ("source", "target", "lease", "recovery_generation", "recovery_stream")}
    resume.update(kind="budget_resume", request_id=str(uuid.uuid4()), defer_request_id=old["record_id"])
    store.install_budget_resume_request(resume)
    body = intent(home, old)
    result = receipt(body)
    body["request"]["original_request"] = deepcopy(resume)
    body["request_sha256"] = wire.request_sha256(body["request"])
    resume_receipt = {**deepcopy(parent_receipt), "request_id": resume["request_id"], "request_sha256": wire.digest(resume),
        "status": "queued", "reason": None, "retry_at": None, "budget_operation_id": None}
    result["request_sha256"] = body["request_sha256"]
    result["result"].update(request_sha256=body["request_sha256"], original_request_id=resume["request_id"],
        original_request_sha256=wire.digest(resume), original_kind="budget_resume", original_receipt=resume_receipt)
    result["result_sha256"] = authority.digest(result["result"])
    result["parent_defer"] = {"request": deepcopy(old["request"]), "receipt": deepcopy(parent_receipt)}
    with authority.producer(home.ledger, home.binding, home.con, historical=True) as proof:
        home.ledger.publish("requests", body, producer=proof)
    forged = deepcopy(result)
    forged["parent_defer"]["receipt"]["attempts"] += 1
    forged["result"]["original_receipt"]["attempts"] += 1
    forged["result_sha256"] = authority.digest(forged["result"])
    with rejected("PARENT_RECEIPT_MISMATCH"):
        home.ledger.publish("receipts", forged)
    home.ledger.publish("receipts", result)
    pending = store.record_budget_resume_receipt(resume["request_id"], resume_receipt)
    store.complete_budget_resume(home.binding.core, pending)
    assert store.read()["budget_pending"] is None
    assert home.ledger.read(authority.record_ref(result)) == result


def projection_fixture():
    """Decoded historical facts only, with no State/SQL producer or factory."""
    binding = {"home_library_id": pure_wire.SOURCE["library_id"], "home_node_id": pure_wire.SOURCE["node_id"],
        "target_library_id": pure_wire.TARGET["library_id"], "target_node_id": pure_wire.TARGET["node_id"],
        "target_url": "https://private-authority.example.test"}
    active = {"format_version": 1, "facility_id": str(uuid.uuid4()), "owner_profile_sha256": "a" * 64,
        "binding": binding, "activated_at": COMMITTED}
    core = {"format_version": 1, "binding": binding, "item_id": pure_wire.CLAIM["item_id"],
        "submitted_url_sha256": pure_wire.URL_SHA, "root_job_id": 9, "continuation_id": str(uuid.uuid4()),
        "execution_generation": 1, "local_epoch": 1, "recovery_generation": 0,
        "recovery_stream": deepcopy(pure_wire.STREAM), "resolution_input_sha256": "d" * 64}
    core_sha = authority.digest(core)
    initial = {"format_version": 1, "record_kind": "origin",
        "record_id": str(uuid.uuid5(uuid.UUID(core["continuation_id"]), "source-origin:v1:" + core_sha)),
        "binding": binding, "core": core, "core_sha256": core_sha, "claim": deepcopy(pure_wire.CLAIM), "created_at": COMMITTED}
    basis = {"format_version": 1, "kind": "original_core", "core_sha256": core_sha, "home_epoch": 2,
        "session": deepcopy(pure_wire.LEASE), "claim": deepcopy(pure_wire.CLAIM), "recovery_generation": 2,
        "recovery_stream": deepcopy(pure_wire.STREAM), "control_revision": 4,
        "origin_ref": authority.record_ref(initial), "admission_ref": None, "continuity": None}
    old = pure_wire.old_request()
    control_record = {"format_version": 1, "record_kind": "control", "record_id": old["request_id"],
        "core_sha256": core_sha, "request_sha256": wire.digest(old), "request": old, "creation_basis": basis}
    before = {"binding": binding, "local_epoch": 2, "core_sha256": core_sha, "admission_ref": None,
        "recovery_generation": 2, "recovery_stream": deepcopy(pure_wire.STREAM), "control_revision": 4,
        "claim": deepcopy(pure_wire.CLAIM), "budget_pending_sha256": None, "source_control_pending_sha256": None,
        "pending_publication": None, "source_authority": None}
    request = pure_wire.dispose(old)
    request["original"]["core_sha256"] = core_sha
    intent_record = {"format_version": 1, "record_kind": "recovery_intent", "record_id": request["request_id"],
        "core_sha256": core_sha, "request_sha256": wire.request_sha256(request), "request": request,
        "expected_before": before, "preservation_basis": {"format_version": 1, "producer": "home_knowledge_restore",
            "operation_id": str(uuid.uuid4()), "receipt_sha256": "f" * 64}, "manual_resume_basis": None}
    result = pure_wire.disposition(request)
    result["core_sha256"] = core_sha
    receipt_record = {"format_version": 1, "record_kind": "recovery_receipt", "record_id": request["request_id"],
        "request_sha256": intent_record["request_sha256"], "result": result, "result_sha256": authority.digest(result), "parent_defer": None}
    publication = {"format_version": 1, "record_kind": "publication", "record_id": str(uuid.uuid4()),
        "metadata": {"publication_id": None, "sha256": "b" * 64, "byte_size": 17},
        "core_sha256": core_sha, "claims_sha256": "c" * 64, "creation_basis": basis}
    publication["metadata"]["publication_id"] = publication["record_id"]
    return active, [("origins", initial), ("controls", control_record), ("requests", intent_record),
        ("receipts", receipt_record), ("publications", publication)]


def projection_payload(active, records, *, reserved=None):
    payload = {"format_version": 1, "contract": deepcopy(active), "intent": deepcopy(active), "seal": deepcopy(active),
        "db_head": None, "checkpoint_head": None, "journal": [], "records": {kind: {} for kind in authority.KINDS}}
    previous = authority.digest(active)
    for kind, body in records:
        payload["records"][kind][body["record_id"]] = deepcopy(body)
        for state in (["reserved"] if body["record_id"] == reserved else ["reserved", "committed"]):
            event = {"format_version": 1, "facility_id": active["facility_id"], "ordinal": len(payload["journal"]) + 1,
                "previous_sha256": previous, "kind": kind, "key": body["record_id"], "record_sha256": authority.digest(body),
                "record": deepcopy(body), "state": state}
            payload["journal"].append(event)
            previous = authority.digest(event)
    payload["db_head"] = {"format_version": 1, "facility_id": active["facility_id"], "ordinal": len(payload["journal"]), "sha256": previous}
    payload["checkpoint_head"] = deepcopy(payload["db_head"])
    return payload


def project(payload, active):
    return authority.pure_projection(payload, binding=active["binding"], owner_profile_sha256=active["owner_profile_sha256"])


def test_pure_projection_closed_all_kinds_has_no_io_or_sensitive_fields(monkeypatch):
    active, records = projection_fixture()
    payload = projection_payload(active, records)
    untouched = deepcopy(payload)
    def forbidden(*args, **kwargs):
        pytest.fail("Pure projection attempted operational ownership or I/O")
    for target, name in ((authority.db, "connect"), (authority.sync_state, "StateStore"),
            (authority.media, "_Directory"), (authority.lifecycle, "require_guard"), (authority, "owned"),
            (authority, "producer"), (authority.AuthorityGuard, "observe")):
        monkeypatch.setattr(target, name, forbidden)
    result = project(payload, active)
    assert payload == untouched
    assert set(result) == {"format_version", "facility_id", "binding", "owner_profile_sha256", "head_sha256", "records"}
    assert set(result["binding"]) == set(authority._PUBLIC_BINDING_FIELDS)
    assert len(result["records"]) == 5
    assert all(set(row) == {"kind", "record_id", "record_sha256", "core_sha256", "state", "refs", "publication_metadata"} for row in result["records"])
    encoded = json.dumps(result, sort_keys=True)
    for secret in (active["binding"]["target_url"], pure_wire.LEASE["lease_token"], pure_wire.CLAIM["lease_token"], "target_url", "lease_token", "expected_before", "creation_basis"):
        assert secret not in encoded
    assert result["records"][-1]["publication_metadata"] == records[-1][1]["metadata"]
    result["records"][0]["refs"].clear()
    assert project(payload, active)["records"][0]["refs"]


@pytest.mark.parametrize("name", ["contract", "intent", "seal", "db_head", "checkpoint_head"])
def test_pure_projection_anchor_deletion_or_mismatch_is_not_absence(name):
    active, records = projection_fixture()
    payload = projection_payload(active, records)
    payload[name] = None
    with rejected():
        project(payload, active)
    payload = projection_payload(active, records)
    payload[name]["facility_id"] = str(uuid.uuid4())
    with rejected():
        project(payload, active)


@pytest.mark.parametrize("change", ["missing_reserved", "missing_committed", "extra_record", "wrong_body", "committed_without_reservation", "duplicate_commit", "journal_gap", "tail_removed", "head_rewound", "anchor_extra"])
def test_pure_projection_corrupt_closure_cannot_become_complete(change):
    active, records = projection_fixture()
    payload = projection_payload(active, records)
    kind, body = records[-1]
    if change == "missing_reserved":
        payload = projection_payload(active, records, reserved=body["record_id"])
        payload["records"][kind].clear()
    elif change == "missing_committed":
        payload["records"][kind].clear()
    elif change == "extra_record":
        payload["records"][kind][str(uuid.uuid4())] = deepcopy(body)
    elif change == "wrong_body":
        payload["records"][kind][body["record_id"]]["metadata"]["byte_size"] += 1
    elif change == "committed_without_reservation":
        payload["journal"][0]["state"] = "committed"
    elif change == "duplicate_commit":
        payload["journal"].append(deepcopy(payload["journal"][-1]))
    elif change == "journal_gap":
        payload["journal"][1]["ordinal"] += 1
    elif change == "tail_removed":
        payload["journal"].pop()
    elif change == "head_rewound":
        payload["checkpoint_head"] = {"format_version": 1, "facility_id": active["facility_id"], "ordinal": 0, "sha256": authority.digest(active)}
    else:
        payload["intent"]["private_extra"] = "do-not-accept"
    with rejected():
        project(payload, active)


def test_pure_projection_requires_committed_dependencies_and_exact_owner():
    active, records = projection_fixture()
    payload = projection_payload(active, records, reserved=records[0][1]["record_id"])
    with rejected("REFERENCE_MISSING"):
        project(payload, active)
    payload = projection_payload(active, records)
    with rejected("OWNER_MISMATCH"):
        authority.pure_projection(payload, binding=active["binding"], owner_profile_sha256="b" * 64)
    with rejected("PEER_MISMATCH"):
        authority.pure_projection(payload, binding={**active["binding"], "target_url": "https://other.example.test"},
            owner_profile_sha256=active["owner_profile_sha256"])
    with rejected():
        authority.pure_projection(payload, binding={key: active["binding"][key] for key in authority._PUBLIC_BINDING_FIELDS},
            owner_profile_sha256=active["owner_profile_sha256"])


def test_single_projection_retains_refs_without_forging_full_chain_closure():
    active, records = projection_fixture()
    record = records[1][1]
    row = authority.public_record_projection("controls", record, state="reserved", activation=active)
    assert row["state"] == "reserved" and row["publication_metadata"] is None
    assert row["refs"][0] == {"kind": "authority_record", **record["creation_basis"]["origin_ref"]}
    assert row["refs"][1]["request_id"] == record["request"]["request_id"]
    payload = projection_payload(active, [("controls", record)])
    with rejected("REFERENCE_MISSING"):
        project(payload, active)


def test_single_receipt_projection_rejects_untyped_result_even_with_matching_sha():
    active, records = projection_fixture()
    body = deepcopy(records[3][1])
    body["result"]["extra_secret"] = "private-token"
    body["result_sha256"] = authority.digest(body["result"])
    with rejected():
        authority.public_record_projection("receipts", body, state="committed", activation=active)


def test_recovery_request_public_edge_binds_exact_kind_uuid_body_and_core():
    active, records = projection_fixture()
    record = records[2][1]
    row = authority.public_record_projection("requests", record, state="committed", activation=active)
    edge = next(ref for ref in row["refs"] if ref["kind"] == "recovery_request")
    assert edge == {"kind": "recovery_request", "request_id": record["request"]["request_id"],
        "request_sha256": wire.request_sha256(record["request"]), "request_kind": "source_dispose",
        "core_sha256": record["core_sha256"]}
    assert set(edge) == {"kind", "request_id", "request_sha256", "request_kind", "core_sha256"}
    original = deepcopy(record)
    record["request"]["kind"] = "source_readmit"
    with rejected():
        authority.public_record_projection("requests", record, state="committed", activation=active)
    record = deepcopy(original)
    record["request_sha256"] = "f" * 64
    with rejected():
        authority.public_record_projection("requests", record, state="committed", activation=active)


@pytest.mark.parametrize("limit", ["MAX_RECORDS", "MAX_TOTAL_BYTES", "MAX_RECORD_BYTES", "DEADLINE_SECONDS"])
def test_pure_projection_capacity_or_deadline_never_silently_truncates(monkeypatch, limit):
    active, records = projection_fixture()
    payload = projection_payload(active, records)
    monkeypatch.setattr(authority, limit, 0)
    if limit == "DEADLINE_SECONDS":
        times = iter((0.0, 1.0))
        monkeypatch.setattr(authority, "time", SimpleNamespace(monotonic=lambda: next(times)))
    with rejected():
        project(payload, active)


def readmit_record_pair(active, before, *, previous=None, expected_claim=None):
    request = pure_wire.readmit()
    request["original"]["core_sha256"] = before["core_sha256"]
    if previous is not None:
        request["previous_admission_id"] = previous["result"]["admission_id"]
        request["expected_claim"] = deepcopy(expected_claim or previous["result"]["claim"])
    value = {"format_version": 1, "record_kind": "recovery_intent", "record_id": request["request_id"],
        "core_sha256": before["core_sha256"], "request_sha256": wire.request_sha256(request), "request": request,
        "expected_before": deepcopy(before), "preservation_basis": None, "manual_resume_basis": None}
    result = pure_wire.admission(request)
    receipt_value = {"format_version": 1, "record_kind": "recovery_receipt", "record_id": value["record_id"],
        "request_sha256": value["request_sha256"], "result": result, "result_sha256": authority.digest(result), "parent_defer": None}
    return value, receipt_value


def test_projection_admission_refs_are_exact_and_latest_chain_does_not_rewrite_old_records():
    active, records = projection_fixture()
    before = records[2][1]["expected_before"]
    first_intent, first = readmit_record_pair(active, before)
    second_intent, second = readmit_record_pair(active, before, previous=first)
    pairs = records + [("requests", first_intent), ("receipts", first), ("requests", second_intent), ("receipts", second)]
    payload = projection_payload(active, pairs)
    result = project(payload, active)
    row = next(row for row in result["records"] if row["kind"] == "receipts" and row["record_id"] == second["record_id"])
    ref = next(ref for ref in row["refs"] if ref["kind"] == "admission")
    assert ref == {"kind": "admission", "format_version": 1, "admission_id": second["record_id"],
        "core_sha256": second["result"]["core_sha256"], "request_sha256": second["request_sha256"],
        "admission_sha256": second["result"]["admission_sha256"], "record_sha256": authority.digest(second)}
    assert {"kind": "admission_parent", "admission_id": first["record_id"], "core_sha256": before["core_sha256"]} in row["refs"]
    encoded = json.dumps(result)
    assert second["result"]["claim"]["lease_token"] not in encoded
    assert first["result"]["claim"]["lease_token"] not in encoded
    assert payload["records"]["receipts"][first["record_id"]] == first


@pytest.mark.parametrize("change", ["missing_parent", "branch", "reserved_intent", "malformed_admission"])
def test_pure_projection_admission_parent_and_committed_intent_are_required(change):
    active, records = projection_fixture()
    before = records[2][1]["expected_before"]
    first_intent, first = readmit_record_pair(active, before)
    second_intent, second = readmit_record_pair(active, before, previous=first)
    pairs = [("requests", first_intent), ("receipts", first), ("requests", second_intent), ("receipts", second)]
    reserved = None
    if change == "missing_parent":
        pairs = pairs[2:]
    elif change == "branch":
        third_intent, third = readmit_record_pair(active, before, previous=first)
        pairs += [("requests", third_intent), ("receipts", third)]
    elif change == "reserved_intent":
        reserved = first_intent["record_id"]
        # Reservation state is selected per kind below; same-ID receipt still
        # has its complete own immutable event chain.
    else:
        second["result"]["attempts"] = True
        second["result"]["admission_sha256"] = wire.admission_sha256(second["result"])
        second["result_sha256"] = authority.digest(second["result"])
    payload = projection_payload(active, pairs)
    if reserved is not None:
        events = [event for event in payload["journal"] if not (event["kind"] == "requests" and event["key"] == reserved and event["state"] == "committed")]
        previous_sha = authority.digest(active)
        for ordinal, event in enumerate(events, 1):
            event.update(ordinal=ordinal, previous_sha256=previous_sha)
            previous_sha = authority.digest(event)
        payload["journal"] = events
        payload["db_head"].update(ordinal=len(events), sha256=previous_sha)
        payload["checkpoint_head"] = deepcopy(payload["db_head"])
    with rejected():
        project(payload, active)


@pytest.mark.parametrize("reference", ["origin", "admission"])
def test_projection_before_authority_ref_requires_actual_committed_record(reference):
    active, records = projection_fixture()
    intent_record = deepcopy(records[2][1])
    before = intent_record["expected_before"]
    if reference == "origin":
        before["source_authority"] = {"format_version": 1, "core_sha256": before["core_sha256"],
            "origin": authority.record_ref(records[0][1]), "defer": None, "resume": None, "failure": None, "publication": None}
    else:
        before["admission_ref"] = {"format_version": 1, "admission_id": str(uuid.uuid4()), "core_sha256": before["core_sha256"],
            "request_sha256": "a" * 64, "admission_sha256": "b" * 64, "record_sha256": "c" * 64}
    payload = projection_payload(active, [("requests", intent_record)])
    with rejected("REFERENCE_MISSING"):
        project(payload, active)
