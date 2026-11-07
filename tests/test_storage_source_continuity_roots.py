"""Actual private permanent files and supplied public SQL; no live DB/service."""
from copy import deepcopy
from dataclasses import replace
import os
import uuid

import pytest

from reno import storage_source_continuity_roots as roots, storage_lifecycle as lifecycle
from reno import sync_source_readmit_schema as schema, sync_source_continuity as external
from reno import sync_source_readmit_contract as wire
from tests.test_sync_source_continuity import profile, activated, activation, owner, bodies, negative, snapshot  # noqa: F401
from tests.test_sync_source_readmit_contract import dispose, disposition


def empty():
    return {table: [] for table in roots.SQL_COLUMNS}


def sql(active, registration=None, tombstone=None):
    value = empty()
    value[schema.ACTIVATION] = [{"continuity_id": active["continuity_id"], "format_version": 1,
        "owner_profile_sha256": active["owner_profile_sha256"], "target_epoch": active["target_epoch"],
        "contract_sha256": external._digest(active), "activated_at": active["activated_at"], **{
            side + "_" + key: active[side + "_identity"][key] for side in ("source", "target") for key in ("library_id", "node_id")}}]
    def row(table, record):
        result = {key: record[key] for key in roots.SQL_COLUMNS[table] if key in record}
        result.update({key: value[schema.ACTIVATION][0][key] for key in roots.IDENTITIES})
        result["result_sha256"] = wire.digest(record)
        if table == schema.DISPOSITION:
            result.update(disposition_sha256=wire.disposition_sha256(record), defer_receipt_sha256=None)
        return result
    if registration is not None:
        value[schema.REGISTRATION] = [row(schema.REGISTRATION, registration)]
    if tombstone is not None:
        value[schema.DISPOSITION] = [row(schema.DISPOSITION, tombstone)]
    return value


def capture(profile, projection=None, **kwargs):
    with lifecycle.shared(profile):
        with roots.capture_locked(profile, sql_projection=empty() if projection is None else projection, **kwargs) as result:
            result.verify_source()
            return result.public_summary()


def populated(profile, *, effect=False, tombstone=False):
    active = activated(profile)
    request, registered = bodies(active)
    disposed = None
    with owner(profile) as guard:
        guard.register(request, registered)
        if effect:
            guard.begin_effect(registered, begun_at=active["activated_at"])
        if tombstone:
            body, disposed = negative(request, registered)
            guard.commit_not_applied(body, disposed)
    return active, request, registered, sql(active, registered, disposed)


def test_absent_has_no_writer_lock_init_or_sql_and_changes_no_files(profile, monkeypatch):
    import sqlite3
    from reno import sync_state
    before = snapshot(profile)
    def forbidden(*_args, **_kwargs):
        pytest.fail("Read-only capture cannot create a writer, lock or SQLite connection")
    monkeypatch.setattr(external, "locked", forbidden)
    monkeypatch.setattr(external.ContinuityGuard, "activate_files", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(sync_state, "StateStore", forbidden)
    result = capture(profile)
    assert result["summary"]["presence"] == "absent" and not result["unknown"] and not result["references"]
    assert snapshot(profile) == before


@pytest.mark.parametrize("kind", ["empty", "registration", "effect", "tombstone"])
def test_healthy_permanent_evidence_has_exact_public_refs_without_managed_gc_blocker(profile, kind):
    if kind == "empty":
        active = activated(profile)
        projection = sql(active)
    else:
        active, _, _, projection = populated(profile, effect=kind == "effect", tombstone=kind == "tombstone")
    before = snapshot(profile)
    result = capture(profile, projection)
    assert result["summary"]["presence"] == "present" and not result["unknown"]
    assert result["summary"]["record_count"] == {"empty": 0, "registration": 1, "effect": 2, "tombstone": 2}[kind]
    assert result["summary"]["identity"] == {"continuity_id": active["continuity_id"]}
    encoded = str(result)
    assert all(secret not in encoded for secret in ("lease_token", "original_receipt", "original_request_json", "target_url"))
    assert snapshot(profile) == before


@pytest.mark.parametrize("anchor", [external.INTENT_NAME, external.DB_HEAD_NAME, external.SEAL_NAME,
    external.CHECKPOINT_HEAD_NAME, "contract.json"])
def test_each_missing_anchor_is_unknown_not_absent_and_never_repaired(profile, anchor):
    active = activated(profile)
    path = (profile.checkpoints if anchor in (external.SEAL_NAME, external.CHECKPOINT_HEAD_NAME)
            else profile.database.parent / external.DIRECTORY if anchor == "contract.json" else profile.database.parent) / anchor
    path.unlink()
    before = snapshot(profile)
    result = capture(profile, sql(active))
    assert result["summary"]["presence"] == "unknown" and result["unknown"][0]["scope"] == "cloud_continuity"
    assert snapshot(profile) == before


def test_sql_activation_without_external_evidence_remains_unknown(profile):
    result = capture(profile, sql(activation(profile)))
    assert result["unknown"][0]["scope"] == "cloud_continuity"
    assert result["unknown"][0]["code"] == "CONTINUITY_ROOT_SQL_EXTERNAL_MISSING"
    assert result["unknown"][0]["boundscope"]["sql_rows"] == 1
    assert result["references"]


def test_actual_reserved_journal_is_unknown_with_original_scope_not_never_dispatched(profile):
    active = activated(profile)
    request, registered = bodies(active)
    with owner(profile) as guard:
        observed = guard.observe()
        guard._append(observed, "registrations", registered["original_request_id"], registered, "reserved")
    before = snapshot(profile)
    result = capture(profile, sql(active))
    assert result["unknown"][0]["code"] == "CONTINUITY_ROOT_RESERVED"
    assert result["unknown"][0]["boundscope"]["observed_files"] >= 6
    assert any(ref["record_id"] == registered["original_request_id"] and ref.get("state") == "reserved"
               for ref in result["references"])
    assert result["summary"]["presence"] == "unknown"
    assert snapshot(profile) == before


@pytest.mark.parametrize("damage", ["extra_private_json", "missing_field", "bool_epoch", "identity", "contract_sha", "registration_sha"])
def test_supplied_sql_is_closed_typed_and_crosschecked_against_actual_files(profile, damage):
    _, _, _, projection = populated(profile)
    if damage == "extra_private_json":
        projection[schema.REGISTRATION][0]["request_json"] = "PRIVATE_SECRET_BODY"
    elif damage == "missing_field":
        projection[schema.REGISTRATION][0].pop("result_sha256")
    elif damage == "bool_epoch":
        projection[schema.ACTIVATION][0]["target_epoch"] = True
    elif damage == "identity":
        projection[schema.ACTIVATION][0]["source_node_id"] = str(uuid.uuid4())
    elif damage == "contract_sha":
        projection[schema.ACTIVATION][0]["contract_sha256"] = "a" * 64
    else:
        projection[schema.REGISTRATION][0]["registration_sha256"] = "a" * 64
    result = capture(profile, projection)
    assert result["summary"]["presence"] == "unknown" and result["unknown"]
    assert "PRIVATE_SECRET_BODY" not in str(result)


@pytest.mark.parametrize("effect", [False, True])
@pytest.mark.parametrize("damage", [None, "original_request_sha256", "original_kind", "core_sha256", "item_id"])
def test_historical_applied_sql_is_bound_to_same_uuid_permanent_original(profile, effect, damage):
    _, request, registered, projection = populated(profile, effect=effect)
    # An older positive replay may legitimately omit a later registration.
    # It still cannot replace the original scalar binding for the same UUID.
    positive = disposition(dispose(request["original_request"]))
    projection[schema.DISPOSITION] = sql(activation(profile), tombstone=positive)[schema.DISPOSITION]
    row = projection[schema.DISPOSITION][0]
    assert row["continuity_id"] is None and row["registration_sha256"] is None
    if damage == "original_kind":
        row[damage] = "source_fail"
    elif damage == "item_id":
        row[damage] += 1
    elif damage is not None:
        row[damage] = "a" * 64
    before = snapshot(profile)
    result = capture(profile, projection)
    if damage is None:
        assert result["summary"]["presence"] == "present" and not result["unknown"]
    else:
        assert result["summary"]["presence"] == "unknown"
        assert result["unknown"][0]["code"] == "CONTINUITY_ROOT_SQL_DISPOSITION_MISMATCH"
        assert result["unknown"][0]["scope"] == "cloud_continuity"
        assert any(ref["record_id"] == registered["original_request_id"]
                   and ref.get("state") == "committed" for ref in result["references"])
    assert snapshot(profile) == before


def test_applied_sql_cannot_hide_same_uuid_permanent_negative_outcome(profile):
    _, request, registered, projection = populated(profile, tombstone=True)
    positive = disposition(dispose(request["original_request"]))
    projection[schema.DISPOSITION] = sql(activation(profile), tombstone=positive)[schema.DISPOSITION]
    before = snapshot(profile)
    result = capture(profile, projection)
    assert result["summary"]["presence"] == "unknown"
    assert result["unknown"][0]["code"] == "CONTINUITY_ROOT_SQL_DISPOSITION_MISMATCH"
    assert any(ref["record_kind"] == "tombstones" and ref["record_id"] == registered["original_request_id"]
               for ref in result["references"])
    assert snapshot(profile) == before


def test_wrong_or_rewound_head_is_unknown_and_does_not_reconstruct_head(profile):
    active, _, _, projection = populated(profile)
    path = profile.checkpoints / external.CHECKPOINT_HEAD_NAME
    path.write_bytes(external._encoded({"format_version": 1, "continuity_id": active["continuity_id"],
        "ordinal": 0, "sha256": external._digest(active)}))
    before = snapshot(profile)
    result = capture(profile, projection)
    assert result["unknown"][0]["code"] == "CONTINUITY_ROOT_HEAD_INCOMPLETE"
    assert snapshot(profile) == before


def test_historical_prefix_allows_real_later_append_without_borrowing_new_authority(profile):
    active = activated(profile)
    prefix = {"continuity_id": active["continuity_id"], "ordinal": 0, "sha256": external._digest(active)}
    request, registered = bodies(active)
    with owner(profile) as guard:
        guard.register(request, registered)
    result = capture(profile, sql(active, registered), historical_prefix=prefix)
    assert not result["unknown"] and result["summary"]["head_ordinal"] == 2
    bad = {**prefix, "sha256": "a" * 64}
    assert capture(profile, sql(active, registered), historical_prefix=bad)["unknown"][0]["code"] == "CONTINUITY_ROOT_PREFIX_INVALID"


def test_reader_does_not_hold_writer_lock_or_mutable_head_handles(profile):
    active = activated(profile)
    request, registered = bodies(active)
    with lifecycle.shared(profile):
        with pytest.raises(roots.ContinuityRootError, match="SOURCE_CHANGED") as caught:
            with roots.capture_locked(profile, sql_projection=sql(active)) as result:
                assert not result.unknown
                # Real concurrent writer succeeds. Its legitimate append must
                # invalidate this older observation rather than be prevented.
                with external.locked(profile, timeout=0) as guard:
                    guard.register(request, registered)
    assert caught.value.retriable


def test_one_shot_callback_error_is_preserved_by_identity(profile):
    activated(profile)
    original = ValueError("synthetic one-shot callback")
    calls = 0
    def check():
        nonlocal calls
        calls += 1
        if calls == 8:
            raise original
    with lifecycle.shared(profile), pytest.raises(ValueError) as found:
        with roots.capture_locked(profile, sql_projection=empty(), check_cancelled=check):
            pytest.fail("Cancellation cannot be swallowed as unknown evidence")
    assert found.value is original


def test_unexpected_record_preserves_original_bounded_scope_and_is_not_hidden(profile):
    active, _, registered, projection = populated(profile)
    path = profile.database.parent / external.DIRECTORY / "effects" / (str(uuid.uuid4()) + ".json")
    path.write_bytes(b"{}")
    if os.name != "nt":
        path.chmod(0o600)
    before = snapshot(profile)
    result = capture(profile, projection)
    assert result["unknown"][0]["code"] == "CONTINUITY_ROOT_UNEXPECTED_FILE"
    assert any(ref["record_id"] == registered["original_request_id"] for ref in result["references"])
    assert snapshot(profile) == before


@pytest.mark.parametrize("limit", ["max_record_bytes", "max_total_bytes", "max_sql_rows", "max_sql_bytes", "max_journal", "max_records"])
def test_all_relevant_file_sql_and_journal_bounds_remain_unknown_not_empty(profile, limit):
    _, _, _, projection = populated(profile, effect=True)
    result = capture(profile, projection, limits=replace(roots.CaptureLimits(), **{limit: 1}))
    assert result["summary"]["presence"] == "unknown" and result["unknown"]


def test_module_requires_existing_lifecycle_guard(profile):
    with pytest.raises(lifecycle.StorageLifecycleError):
        with roots.capture_locked(profile, sql_projection=empty()):
            pass


@pytest.mark.parametrize("invalid", [False, 0, -1, float("nan")])
def test_invalid_deadline_never_silently_means_default(invalid):
    with pytest.raises(ValueError):
        roots.CaptureLimits(deadline_seconds=invalid)


@pytest.mark.parametrize("field", ["max_records", "max_journal", "max_parts", "max_record_bytes", "max_total_bytes", "max_sql_rows", "max_sql_bytes"])
def test_caller_cannot_increase_contract_hard_bounds(field):
    with pytest.raises(ValueError):
        replace(roots.CaptureLimits(), **{field: getattr(roots.CaptureLimits(), field) + 1})


def test_caller_dto_mutation_cannot_erase_unknown_or_rewrite_captured_refs(profile):
    projection = sql(activation(profile))
    with lifecycle.shared(profile):
        with roots.capture_locked(profile, sql_projection=projection) as result:
            result.summary["presence"] = "present"
            result.unknown[0]["code"] = "FAKE_PERMISSION"
            result.references[0]["record_id"] = "FAKE_PERMISSION"
            assert result.summary["presence"] == "unknown"
            assert result.unknown[0]["code"] == "CONTINUITY_ROOT_SQL_EXTERNAL_MISSING"
            assert result.references[0]["record_id"] == projection[schema.ACTIVATION][0]["continuity_id"]
