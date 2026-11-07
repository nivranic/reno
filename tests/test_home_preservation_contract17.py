"""Public representation only: no copied DTO can substitute an actual producer."""
from copy import deepcopy
import hashlib
import json

import pytest

from reno import home_preservation_contract17 as contract, home_recovery_schema as home
from reno import source_preservation as legacy, sync_state
from tests.test_source_preservation import install_outbox
from tests.test_source_preservation_v17 import env
from tests.test_source_preservation_certificate17 import captured
from tests.test_source_preservation_witness import install_core, seed_origin, historical_state, authority_state
from reno import sync_source_authority as authority


UUIDS = ["00000000-0000-4000-8000-" + str(n).zfill(12) for n in range(1, 12)]
SHA = "a" * 64
CORE = "c" * 64


def digest(value):
    return hashlib.sha256(home.canonical_json(value).encode("utf-8")).hexdigest()


def authority_digest(wrapper):
    authority = wrapper["state_authority"]
    authority["authority_ref_set_sha256"] = digest({"core_sha256": authority["source_core_sha256"],
        **{name: authority[name] for name in ("source_authority", "source_admission", "source_recovery_pending")}})


def capsule(kind, *, present=False, binding=True):
    value = {"format_version": 1, "kind": kind, "presence": "present" if present else "absent",
        "absence_sha256": None if present else digest({"format_version": 1, "kind": kind, "presence": "absent"}),
        "owner_profile_sha256": SHA,
        "identity": {"facility_id" if kind == "home_authority" else "continuity_id": UUIDS[0]} if present else None,
        "contract_sha256": SHA if present else None, "head_ordinal": 0,
        "head_sha256": SHA if present else None, "journal_sha256": SHA if present else None,
        "record_set_sha256": SHA if present else None,
        "reference_set_sha256": SHA if present else digest([]), "record_count": 0, "journal_count": 0}
    if kind == "home_authority":
        value["binding"] = dict(zip(("home_library_id", "home_node_id", "target_library_id", "target_node_id"), UUIDS[1:5])) if binding else None
    return value


def wrapper(*, state_version=None):
    checkpoint = {"state_present": state_version is not None,
        "absence_sha256": digest({"format_version": 1, "state": "absent"}) if state_version is None else None,
        "state_sha256": None if state_version is None else SHA, "state_bytes": 0 if state_version is None else 24,
        "canonical_state_sha256": None if state_version is None else SHA, "state_format_version": state_version,
        "source_execution_sha256": None, "source_execution_lineage": None, "outboxes": []}
    value = {"format_version": 2, "checkpoint": checkpoint,
        "certificate": {"format_version": 2, "certificate_sha256": SHA, "byte_size": 2048,
            "preserved_sha256": "b" * 64, "frontier_sha256": "d" * 64},
        "bundle": {"format_version": 1, "bundle_id": UUIDS[5], "manifest_sha256": SHA, "file_set_sha256": SHA},
        "state_authority": {"format_version": 1, "state_present": checkpoint["state_present"], "source_core_sha256": None,
            "source_authority": None, "source_admission": None, "source_recovery_pending": None,
            "authority_ref_set_sha256": None},
        "permanent_witnesses": {kind: capsule(kind) for kind in ("cloud_continuity", "home_authority")}}
    authority_digest(value)
    return value


def state5_refs():
    value = wrapper(state_version=5)
    value["checkpoint"].update(source_execution_sha256=CORE, source_execution_lineage={
        "root_job_id": 7, "continuation_id": UUIDS[6], "execution_generation": 2})
    ref = lambda kind, n: {"format_version": 1, "record_kind": kind, "record_id": UUIDS[n], "record_sha256": SHA}
    value["state_authority"].update(source_core_sha256=CORE,
        source_authority={"format_version": 1, "core_sha256": CORE,
            **{slot: ref("origin" if slot == "origin" else "publication" if slot == "publication" else "control", n)
               for n, slot in enumerate(("origin", "defer", "resume", "failure", "publication"))}},
        source_admission={"format_version": 1, "admission_id": UUIDS[7], "core_sha256": CORE,
            "request_sha256": SHA, "admission_sha256": SHA, "record_sha256": SHA},
        source_recovery_pending={"format_version": 1, "kind": "source_readmit", "request_id": UUIDS[8],
            "core_sha256": CORE, "request_sha256": SHA, "intent_sha256": SHA})
    value["permanent_witnesses"]["home_authority"] = capsule("home_authority", present=True)
    value["permanent_witnesses"]["home_authority"].update(record_count=7, journal_count=7, head_ordinal=7)
    authority_digest(value)
    return value


def from_capture(before):
    document = before.public()
    return {"format_version": 2, "checkpoint": document["checkpoint"],
        "certificate": {"format_version": 2, "certificate_sha256": before.sha256,
            "byte_size": len(before.document_json.encode("utf-8")), "preserved_sha256": document["preserved_sha256"],
            "frontier_sha256": digest(document["frontier"])},
        "bundle": {"format_version": 1, "bundle_id": UUIDS[5], "manifest_sha256": SHA, "file_set_sha256": SHA},
        "state_authority": document["state_authority"], "permanent_witnesses": document["permanent_witnesses"]}


def test_whole_wrapper_sha_and_deep_detachment_do_not_create_a_capability():
    value = state5_refs()
    raw = home.canonical_json(value)
    copied = contract.normalize_checkpoint(raw, expected_sha256=digest(value))
    assert copied == value
    assert set(copied) == contract.WRAPPER_FIELDS
    copied["checkpoint"]["source_execution_lineage"]["root_job_id"] = 99
    assert value["checkpoint"]["source_execution_lineage"]["root_job_id"] == 7
    assert type(copied) is dict
    assert not any(hasattr(copied, name) for name in ("verify_source", "before_effect", "ticket", "owner", "admit"))
    with pytest.raises(contract.HomePreservationContractError):
        contract.normalize_checkpoint(raw, expected_sha256=digest(value["checkpoint"]))


@pytest.mark.parametrize("version", [1, 2, 3, 4])
def test_actual_legacy_state_outboxes_remain_original_nine_keys_and_bytes(env, version):
    if version > 1:
        getattr(env.store, {2: "enable_no_resend", 3: "enable_source_execution", 4: "enable_source_control"}[version])()
    install_outbox(env)
    with env.store.reference_operation():
        state = env.store.read()
        checkpoint = legacy._checkpoint(env.store, env.root,
            state["binding"]["home_library_id"], state["binding"]["home_node_id"])
    with captured(env) as (_con, _witness, before):
        copied = contract.normalize_checkpoint(from_capture(before))
        assert copied["checkpoint"] == checkpoint
        assert home.canonical_json(copied["checkpoint"]) == home.canonical_json(checkpoint)
        assert len(copied["checkpoint"]) == 9


def test_actual_state5_factory_public_digest_and_complete_capture_are_not_truncated(env):
    install_core(env)
    with captured(env) as (_con, _witness, before):
        original_bytes = before.document_json
        copied = contract.normalize_checkpoint(from_capture(before))
        assert copied["checkpoint"]["state_format_version"] == 5
        assert copied["state_authority"]["authority_ref_set_sha256"] == sync_state.source_readmit_projection(env.store.read())["refset_sha256"]
        assert copied["certificate"]["certificate_sha256"] == before.sha256
        assert before.document_json == original_bytes


def test_actual_state_absence_accepts_home_absent_null_binding(env):
    (env.store.data_dir / sync_state._STATE).unlink()
    with captured(env, root=False) as (_con, _witness, before):
        value = from_capture(before)
        assert value["permanent_witnesses"]["home_authority"]["binding"] is None
        assert contract.normalize_checkpoint(value) == value


def test_actual_committed_origin_and_full_home_capsule_are_kept(env):
    core = install_core(env)
    origin = seed_origin(env, core)
    historical_state(env, {"source_authority": authority_state(core, origin=authority.record_ref(origin))})
    with captured(env) as (_con, _witness, before):
        value = from_capture(before)
        copied = contract.normalize_checkpoint(value)
        assert copied["state_authority"]["source_authority"]["origin"] == authority.record_ref(origin)
        assert copied["permanent_witnesses"]["home_authority"]["presence"] == "present"
        assert copied["permanent_witnesses"]["home_authority"]["record_count"] == 1
        assert copied == value


@pytest.mark.parametrize("kind", ["source_register", "source_dispose", "source_readmit"])
def test_exact_state5_authority_admission_pending_refs_are_preserved(kind):
    value = state5_refs()
    value["state_authority"]["source_recovery_pending"]["kind"] = kind
    authority_digest(value)
    assert contract.normalize_checkpoint(value) == value


def test_facility_presence_and_cloud_reference_digest_are_independent():
    value = wrapper(state_version=1)
    value["permanent_witnesses"]["cloud_continuity"]["reference_set_sha256"] = "e" * 64
    value["permanent_witnesses"]["home_authority"] = capsule("home_authority", present=True)
    assert contract.normalize_checkpoint(value) == value
    value["permanent_witnesses"]["cloud_continuity"] = capsule("cloud_continuity", present=True)
    value["permanent_witnesses"]["cloud_continuity"]["reference_set_sha256"] = "f" * 64
    value["permanent_witnesses"]["home_authority"] = capsule("home_authority", binding=False)
    assert contract.normalize_checkpoint(value) == value


def test_default_reader_limits_do_not_become_historical_metadata_limits():
    value = wrapper()
    for kind in value["permanent_witnesses"]:
        value["permanent_witnesses"][kind] = capsule(kind, present=True)
        value["permanent_witnesses"][kind].update(record_count=10_000, journal_count=20_000, head_ordinal=20_000)
    assert contract.normalize_checkpoint(value) == value


@pytest.mark.parametrize("component", ["wrapper", "checkpoint", "certificate", "bundle", "state_authority",
    "permanent_witnesses", "cloud_continuity", "home_authority", "origin", "admission", "pending"])
def test_unknown_public_or_private_keys_are_never_silently_projected(component):
    value = state5_refs()
    selected = {"wrapper": value, **{key: value[key] for key in ("checkpoint", "certificate", "bundle", "state_authority", "permanent_witnesses")},
        **value["permanent_witnesses"], "origin": value["state_authority"]["source_authority"]["origin"],
        "admission": value["state_authority"]["source_admission"], "pending": value["state_authority"]["source_recovery_pending"]}[component]
    selected["path"] = "/synthetic/private"
    with pytest.raises(contract.HomePreservationContractError):
        contract.normalize_checkpoint(value)


@pytest.mark.parametrize("component,field", [("wrapper", "format_version"), ("checkpoint", "state_format_version"),
    ("checkpoint", "state_bytes"), ("certificate", "format_version"), ("certificate", "byte_size"),
    ("bundle", "format_version"), ("state_authority", "format_version"), ("cloud_continuity", "format_version"),
    ("cloud_continuity", "head_ordinal"), ("home_authority", "record_count")])
@pytest.mark.parametrize("fake", [True, 1.0])
def test_bool_and_float_cannot_impersonate_integer_fields(component, field, fake):
    value = state5_refs()
    selected = value if component == "wrapper" else value["permanent_witnesses"][component] if component in value["permanent_witnesses"] else value[component]
    selected[field] = fake
    with pytest.raises(contract.HomePreservationContractError):
        contract.normalize_checkpoint(value)


@pytest.mark.parametrize("component,field,bad", [("certificate", "byte_size", 1),
    ("certificate", "byte_size", 8 * 1024 * 1024 + 1), ("certificate", "certificate_sha256", "A" * 64),
    ("bundle", "bundle_id", UUIDS[5].replace("-", "")), ("bundle", "manifest_sha256", "f" * 63),
    ("checkpoint", "state_bytes", sync_state.MAX_STATE_BYTES + 1), ("checkpoint", "state_format_version", 6),
    ("home_authority", "record_count", -1), ("home_authority", "journal_count", 2**63)])
def test_metadata_bounds_and_canonical_identifiers(component, field, bad):
    value = state5_refs()
    selected = value["permanent_witnesses"][component] if component in value["permanent_witnesses"] else value[component]
    selected[field] = bad
    with pytest.raises(contract.HomePreservationContractError):
        contract.normalize_checkpoint(value)


@pytest.mark.parametrize("slot", ["source_core_sha256", "source_authority", "source_admission", "source_recovery_pending"])
def test_other_core_refs_are_rejected_even_with_recomputed_refset_digest(slot):
    value = state5_refs()
    if slot == "source_core_sha256":
        value["state_authority"][slot] = "e" * 64
    else:
        value["state_authority"][slot]["core_sha256"] = "e" * 64
    authority_digest(value)
    with pytest.raises(contract.HomePreservationContractError):
        contract.normalize_checkpoint(value)


@pytest.mark.parametrize("version", [1, 2])
def test_state_before_source_execution_cannot_invent_core_or_lineage(version):
    value = wrapper(state_version=version)
    value["checkpoint"].update(source_execution_sha256=CORE, source_execution_lineage={
        "root_job_id": 7, "continuation_id": UUIDS[6], "execution_generation": 2})
    value["state_authority"]["source_core_sha256"] = CORE
    authority_digest(value)
    with pytest.raises(contract.HomePreservationContractError):
        contract.normalize_checkpoint(value)


@pytest.mark.parametrize("slot,bad_kind", [("origin", "control"), ("defer", "origin"),
    ("resume", "publication"), ("failure", "origin"), ("publication", "control")])
def test_authority_slot_ref_kind_is_not_interchangeable(slot, bad_kind):
    value = state5_refs()
    value["state_authority"]["source_authority"][slot]["record_kind"] = bad_kind
    authority_digest(value)
    with pytest.raises(contract.HomePreservationContractError):
        contract.normalize_checkpoint(value)


def test_ref_digest_uses_core_sha256_key_and_binds_every_pending_intent():
    value = state5_refs()
    value["state_authority"]["source_recovery_pending"]["intent_sha256"] = "e" * 64
    with pytest.raises(contract.HomePreservationContractError):
        contract.normalize_checkpoint(value)
    authority_digest(value)
    assert contract.normalize_checkpoint(value) == value
    refs = {key: value["state_authority"][key] for key in ("source_core_sha256", "source_authority", "source_admission", "source_recovery_pending")}
    value["state_authority"]["authority_ref_set_sha256"] = digest(refs)
    with pytest.raises(contract.HomePreservationContractError):
        contract.normalize_checkpoint(value)


@pytest.mark.parametrize("version", [None, 1, 2, 3, 4])
def test_old_or_absent_state_cannot_sneak_state5_refs_into_wrapper(version):
    value = state5_refs()
    value["checkpoint"] = wrapper(state_version=version)["checkpoint"]
    value["state_authority"].update(state_present=version is not None, source_core_sha256=None)
    authority_digest(value)
    with pytest.raises(contract.HomePreservationContractError):
        contract.normalize_checkpoint(value)


@pytest.mark.parametrize("change", ["unknown", "missing", "role", "profile", "partial", "null_present_binding", "head_count", "home_record_set"])
def test_capsules_cannot_claim_healthy_presence_from_partial_metadata(change):
    value = state5_refs()
    home_capsule = value["permanent_witnesses"]["home_authority"]
    if change == "unknown":
        home_capsule["presence"] = "unknown"
    elif change == "missing":
        del value["permanent_witnesses"]["cloud_continuity"]
    elif change == "role":
        home_capsule["kind"] = "cloud_continuity"
    elif change == "profile":
        home_capsule["owner_profile_sha256"] = "e" * 64
    elif change == "partial":
        home_capsule["contract_sha256"] = None
    elif change == "null_present_binding":
        home_capsule["binding"] = None
    elif change == "head_count":
        home_capsule["head_ordinal"] += 1
    else:
        home_capsule["record_set_sha256"] = "f" * 64
    with pytest.raises(contract.HomePreservationContractError):
        contract.normalize_checkpoint(value)


@pytest.mark.parametrize("change", ["home_absence", "empty_home", "bad_absence", "hash_in_absence", "count_in_absence"])
def test_no_absent_or_empty_home_capsule_can_close_real_state5_record_refs(change):
    value = state5_refs()
    if change == "empty_home":
        value["permanent_witnesses"]["home_authority"] = capsule("home_authority", present=True)
    else:
        value["permanent_witnesses"]["home_authority"] = capsule("home_authority")
        home_capsule = value["permanent_witnesses"]["home_authority"]
        if change == "bad_absence":
            home_capsule["absence_sha256"] = SHA
        elif change == "hash_in_absence":
            home_capsule["head_sha256"] = SHA
        elif change == "count_in_absence":
            home_capsule["record_count"] = 1
    with pytest.raises(contract.HomePreservationContractError):
        contract.normalize_checkpoint(value)


@pytest.mark.parametrize("kind", ["tuple", "duplicate", "oversize", "unknown", "bad_size"])
def test_original_outbox_closed_shape_and_bounds(kind):
    value = wrapper(state_version=4)
    item = {"publication_id": UUIDS[1], "sha256": SHA, "byte_size": 2, "semantic_sha256": SHA}
    value["checkpoint"]["outboxes"] = [item]
    assert contract.normalize_checkpoint(value) == value
    if kind == "tuple":
        value["checkpoint"]["outboxes"] = (item,)
    elif kind == "duplicate":
        value["checkpoint"]["outboxes"].append(deepcopy(item))
    elif kind == "oversize":
        value["checkpoint"]["outboxes"] = [dict(item, publication_id=n) for n in UUIDS[:3]]
    elif kind == "unknown":
        item["paid"] = True
    else:
        item["byte_size"] = sync_state.MAX_OUTBOX_BYTES + 1
    with pytest.raises(contract.HomePreservationContractError):
        contract.normalize_checkpoint(value)


def test_raw_json_requires_original_canonical_text_and_whole_size_limit():
    value = wrapper()
    raw = home.canonical_json(value)
    for malformed in (json.dumps(value), raw.replace('"format_version":2', '"format_version":2,"format_version":2', 1),
                      raw.replace('"byte_size":2048', '"byte_size":NaN', 1)):
        with pytest.raises(contract.HomePreservationContractError):
            contract.normalize_checkpoint(malformed)
    value["extra"] = "测" * 6000
    with pytest.raises(contract.HomePreservationContractError, match="CONTRACT17_LIMIT"):
        contract.normalize_checkpoint(value)


def test_legacy_raw_checkpoint_is_not_relabelled_by_the_new_contract():
    value = wrapper(state_version=4)
    with pytest.raises(contract.HomePreservationContractError):
        contract.normalize_checkpoint(value["checkpoint"])
    with pytest.raises(contract.HomePreservationContractError):
        contract.normalize_checkpoint(dict(value, format_version=1))


def test_parser_never_reads_sql_state_facilities_or_preservation_bodies(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("A pure parser touched an actual authority resource")

    monkeypatch.setattr(legacy, "capture", forbidden)
    monkeypatch.setattr(sync_state.StateStore, "read", forbidden)
    value = state5_refs()
    original = deepcopy(value)
    assert contract.normalize_checkpoint(value) == original
    assert value == original
