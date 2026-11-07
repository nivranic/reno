"""First-core origins use real isolated worker, State and private SQL ownership."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import FrozenInstanceError
import json
import uuid

import pytest

from reno import sync_source_authority as permanent, sync_source_execution as execution, sync_state
from reno.sync_protocol import SyncError
from tests.test_sync_agent import env, install_import
from tests.test_sync_budget_propagation import home_cycle
from tests.test_sync_source_execution_binding import executing, bind


def permanent_files(value):
    profile = value.storage_profile
    anchors = {permanent.INTENT_NAME, permanent.SEAL_NAME, permanent.DB_HEAD_NAME, permanent.CHECKPOINT_HEAD_NAME}
    return {str(path): path.read_bytes() for root in (profile.database.parent, profile.checkpoints)
        for path in root.rglob("*") if path.is_file() and (permanent.DIRECTORY in path.parts or path.name in anchors)}


def origin_fact(value):
    with value.store.reference_operation(), permanent.owned(value.store) as owner:
        observed = owner.observe()
        origins = observed["entries"]["origins"]
        assert len(origins) == 1
        entry = next(iter(origins.values()))
        assert entry["state"] == "committed"
        return deepcopy(entry["record"])


def test_actual_origin_is_committed_before_the_first_state_core(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value):
        install = value.store.install_source_execution
        seen = []
        def install_after_origin(core):
            assert value.store.read().get("source_execution") is None
            ticket = execution._GENESIS.get()
            assert type(ticket) is execution.OriginGenesisGuard
            observed = ticket.owner.observe()
            record = next(iter(observed["entries"]["origins"].values()))
            assert record["state"] == "committed" and record["record"]["core"] == core
            seen.append(ticket)
            return install(core)
        monkeypatch.setattr(value.store, "install_source_execution", install_after_origin)
        binding = bind(value)
        fact = origin_fact(value)
        assert fact["core"] == binding.core and fact["claim"] == value.store.read()["claims"][0]["claim"]
        assert value.store.read()["format_version"] == 3
        assert len(seen) == 1
        with pytest.raises(SyncError):
            seen[0].check(fact)


def test_origin_state_gap_reuses_exact_first_uuid_body_and_timestamp(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value):
        install = value.store.install_source_execution
        def crash_after_origin(core):
            raise SyncError("Controlled state publication failure", "CONTROLLED_STATE_PUBLICATION", 409)
        monkeypatch.setattr(value.store, "install_source_execution", crash_after_origin)
        with pytest.raises(SyncError):
            bind(value)
        assert value.store.read()["source_execution"] is None
        fact = origin_fact(value)
        frozen = permanent_files(value)
        monkeypatch.setattr(value.store, "install_source_execution", install)
        binding = bind(value)
        assert binding.core == fact["core"] and origin_fact(value) == fact
        assert permanent_files(value) == frozen


@pytest.mark.parametrize("ordinal", [1, 2])
def test_origin_head_gap_repairs_only_its_original_typed_tail(env, monkeypatch, ordinal):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value):
        write = sync_state._write
        fired = False
        def crash_checkpoint_head(directory, name, data, **kwargs):
            nonlocal fired
            if not fired and name == permanent.CHECKPOINT_HEAD_NAME and json.loads(data).get("ordinal") == ordinal:
                fired = True
                raise OSError("Controlled original head publication gap")
            return write(directory, name, data, **kwargs)
        monkeypatch.setattr(sync_state, "_write", crash_checkpoint_head)
        with pytest.raises(SyncError):
            bind(value)
        assert fired and value.store.read()["source_execution"] is None
        tail_path = value.store.data_dir / permanent.DIRECTORY / "v1" / "journal" / f"{ordinal:08d}.json"
        tail = json.loads(tail_path.read_bytes())
        original_tail = tail_path.read_bytes()
        monkeypatch.setattr(sync_state, "_write", write)
        binding = bind(value)
        fact = origin_fact(value)
        assert fact == tail["record"] and fact["core"] == binding.core
        assert tail_path.read_bytes() == original_tail


def test_preexisting_legacy_core_has_no_genesis_backfill_or_activation(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value):
        state = value.store.read()
        core = execution._candidate_core(state, execution._job(value.storage_profile), state["claims"][0],
            recovery_generation=value.knowledge_generation, recovery_stream=value.knowledge_stream)
        value.store.enable_source_execution()
        value.store.install_source_execution(core)
        before = permanent_files(value)
        assert before == {}
        assert bind(value).core == core
        assert permanent_files(value) == before
        with value.store.reference_operation(), permanent.owned(value.store) as owner:
            assert owner.observe()["contract"] is None


@pytest.mark.parametrize("change", ["claim", "worker", "epoch"])
def test_origin_state_gap_cannot_bind_changed_actual_facts(env, monkeypatch, change):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value) as (job, _):
        install = value.store.install_source_execution
        monkeypatch.setattr(value.store, "install_source_execution", lambda core: (_ for _ in ()).throw(RuntimeError("Controlled state gap")))
        with pytest.raises(RuntimeError):
            bind(value)
        before = permanent_files(value)
        monkeypatch.setattr(value.store, "install_source_execution", install)
        if change == "claim":
            claims = deepcopy(value.store.read()["claims"])
            claims[0]["claim"]["lease_token"] = str(uuid.uuid4())
            value.store.update({"claims": claims})
        elif change == "worker":
            from reno import storage_admin
            with storage_admin.connection(value.storage_profile) as connection:
                connection.execute("UPDATE job SET owner_id=? WHERE job_id=?", (str(uuid.uuid4()), job["job_id"]))
                connection.commit()
        else:
            value.store.update({"local_epoch": value.store.read()["local_epoch"] + 1})
        with pytest.raises(Exception):
            bind(value)
        assert value.store.read()["source_execution"] is None
        assert permanent_files(value) == before


def test_genesis_ticket_is_not_old_binding_and_cannot_cross_thread(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value):
        publish = permanent.AuthorityGuard.publish
        attempts = []
        def observed_publish(owner, kind, record, *, producer=None):
            if kind == "origins":
                assert type(producer) is execution.OriginGenesisGuard
                with ThreadPoolExecutor(max_workers=1) as pool:
                    with pytest.raises(SyncError):
                        pool.submit(producer.check, record).result()
                attempts.append(record["record_id"])
            return publish(owner, kind, record, producer=producer)
        monkeypatch.setattr(permanent.AuthorityGuard, "publish", observed_publish)
        binding = bind(value)
        fact = origin_fact(value)
        assert attempts == [fact["record_id"]]
        frozen = permanent_files(value)
        assert bind(value).resolution_operation == binding.resolution_operation
        assert permanent_files(value) == frozen


def test_genesis_private_sql_identity_cannot_be_swapped_and_mutated_owner_is_rejected(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value) as (job, _):
        publish = permanent.AuthorityGuard.publish
        def corrupt_private_owner(owner, kind, record, *, producer=None):
            if kind == "origins":
                with pytest.raises(FrozenInstanceError):
                    producer._connection = object()
                producer._connection.execute("UPDATE job SET owner_id=? WHERE job_id=?", (str(uuid.uuid4()), job["job_id"]))
            return publish(owner, kind, record, producer=producer)
        monkeypatch.setattr(permanent.AuthorityGuard, "publish", corrupt_private_owner)
        with pytest.raises(Exception):
            bind(value)
        assert value.store.read()["source_execution"] is None
        with value.store.reference_operation(), permanent.owned(value.store) as owner:
            assert owner.observe()["entries"]["origins"] == {}
        monkeypatch.setattr(permanent.AuthorityGuard, "publish", publish)
        binding = bind(value)
        assert origin_fact(value)["core"] == binding.core


@pytest.mark.parametrize("stage", ["intent_after_publish", "seal_before_publish"])
def test_unsealed_empty_setup_resumes_only_actual_original_intent(env, monkeypatch, stage):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value):
        write = sync_state._write
        fired = False
        def interrupt_setup(directory, name, data, **kwargs):
            nonlocal fired
            if not fired and stage == "seal_before_publish" and name == permanent.SEAL_NAME:
                fired = True
                raise OSError("Controlled unsealed setup gap")
            result = write(directory, name, data, **kwargs)
            if not fired and stage == "intent_after_publish" and name == permanent.INTENT_NAME:
                fired = True
                raise OSError("Controlled intent publication gap")
            return result
        monkeypatch.setattr(sync_state, "_write", interrupt_setup)
        with pytest.raises(SyncError):
            bind(value)
        assert fired and value.store.read()["source_execution"] is None
        original_intent = (value.store.data_dir / permanent.INTENT_NAME).read_bytes()
        monkeypatch.setattr(sync_state, "_write", write)
        binding = bind(value)
        assert (value.store.data_dir / permanent.INTENT_NAME).read_bytes() == original_intent
        assert origin_fact(value)["core"] == binding.core


def test_unsealed_setup_with_unknown_file_is_never_adopted(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value):
        write = sync_state._write
        def interrupt_seal(directory, name, data, **kwargs):
            if name == permanent.SEAL_NAME:
                raise OSError("Controlled unsealed setup gap")
            return write(directory, name, data, **kwargs)
        monkeypatch.setattr(sync_state, "_write", interrupt_seal)
        with pytest.raises(SyncError):
            bind(value)
        directory = value.store.data_dir / permanent.DIRECTORY / "v1"
        path = directory / "unknown.json"
        path.write_text("{}", encoding="utf-8")
        import os
        if os.name != "nt":
            path.chmod(0o600)
        before = permanent_files(value)
        monkeypatch.setattr(sync_state, "_write", write)
        with pytest.raises(SyncError):
            bind(value)
        assert value.store.read()["source_execution"] is None
        assert permanent_files(value) == before


def test_old_execution_seal_cannot_gain_fresh17_permission_from_existing_core(env, monkeypatch):
    from reno import sync_access
    from reno.budget_contract import BudgetDeferred
    install_import(env, monkeypatch)
    with home_cycle(env) as value, executing(value):
        binding = bind(value)
        frozen = permanent_files(value)
        identity = sync_access.identity
        def prospective17(connection):
            result = deepcopy(identity(connection))
            result["handshake"]["migration_version"] = 17
            return result
        monkeypatch.setattr(sync_access, "identity", prospective17)
        with pytest.raises(BudgetDeferred) as caught:
            binding.check()
        assert caught.value.reason == "unsupported_capability"
        assert permanent_files(value) == frozen
