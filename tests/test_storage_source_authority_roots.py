"""Read-only capture of actual private facilities; synthetic permanent facts.

The raw historical fixture represents already stored records only. It is not a
source creation factory, admission, provider opportunity or GC permission.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import os
import uuid

import pytest

from reno import storage_source_authority_roots as roots, storage_lifecycle as lifecycle
from reno import sync_source_authority as authority, sync_state
from tests.test_sync_source_readmit_contract import (SOURCE, TARGET, CLAIM,
    COMMITTED, CORE, old_request, dispose, disposition)


@pytest.fixture
def store(tmp_path):
    profile = lifecycle.StorageProfile(tmp_path / "data" / "reno.db", tmp_path / "media",
        tmp_path / "reports", tmp_path / "checkpoints")
    lifecycle.initialize(profile)
    value = sync_state.StateStore(profile.database.parent, profile=profile)
    value.initialize({"home_library_id": SOURCE["library_id"], "home_node_id": SOURCE["node_id"],
        "target_library_id": TARGET["library_id"], "target_node_id": TARGET["node_id"],
        "target_url": "https://synthetic-peer.example.test"}, 1)
    return value


@contextmanager
def owner(store, *, initialize=False):
    with store.reference_operation(), authority.owned(store, initialize=initialize) as value:
        yield value


def activate(store):
    active = {"format_version": 1, "facility_id": str(uuid.uuid4()),
        "owner_profile_sha256": authority.profile_sha256(store.profile),
        "binding": store.read()["binding"], "activated_at": COMMITTED}
    with owner(store, initialize=True) as guard:
        guard.activate_files(active)
    return active


def ledger(store):
    return store.data_dir / authority.DIRECTORY / authority.VERSION_DIRECTORY


def snapshot(store):
    return {str(path): (path.stat().st_ino, path.stat().st_mtime_ns, path.stat().st_mode, path.read_bytes())
        for root in (store.data_dir, store.profile.checkpoints) for path in root.rglob("*") if path.is_file()}


def write(path, value, *, raw=False):
    path.write_bytes(value if raw else authority._encoded(value))
    if os.name != "nt":
        path.chmod(0o600)


@authority._checked
def reserve_historical_record(guard, observed, kind, record):
    """A bounded historical write, separate from the completed read window."""
    guard._append(observed, kind, record["record_id"], record, "reserved")


@authority._checked
def commit_historical_record(guard, observed, kind, record):
    """Keep the original body/commit together under one checked write budget."""
    directory = guard._directory(observed["ledger"], kind)
    sync_state._write(directory, record["record_id"] + ".json", authority._encoded(record), immutable=True)
    guard._expect_name(directory, record["record_id"] + ".json")
    guard._append(observed, kind, record["record_id"], record, "committed")


def seed(store, records, *, reserved=False):
    """Actual files/heads/journal under real owner; no fresh production factory."""
    with owner(store) as guard:
        for kind, record in records:
            observed = guard.observe()
            reserve_historical_record(guard, observed, kind, record)
            if reserved:
                continue
            observed = guard.observe()
            commit_historical_record(guard, observed, kind, record)


def control():
    body = old_request()
    basis = {"format_version": 1, "kind": "original_core", "core_sha256": CORE, "home_epoch": 1,
        "session": deepcopy(body["lease"]), "claim": deepcopy(body["claim"]),
        "recovery_generation": body["recovery_generation"], "recovery_stream": deepcopy(body["recovery_stream"]),
        "control_revision": 4, "origin_ref": None, "admission_ref": None, "continuity": None}
    return {"format_version": 1, "record_kind": "control", "record_id": body["request_id"],
        "core_sha256": CORE, "request_sha256": authority.digest(body), "request": body, "creation_basis": basis}


def intent(store):
    body = dispose()
    before = {"binding": store.read()["binding"], "local_epoch": 2, "core_sha256": CORE,
        "admission_ref": None, "recovery_generation": body["recovery_generation"],
        "recovery_stream": deepcopy(body["recovery_stream"]), "control_revision": body["control_revision"],
        "claim": deepcopy(CLAIM), "budget_pending_sha256": None, "source_control_pending_sha256": None,
        "pending_publication": None, "source_authority": None}
    return {"format_version": 1, "record_kind": "recovery_intent", "record_id": body["request_id"],
        "core_sha256": CORE, "request_sha256": authority.digest(body), "request": body,
        "expected_before": before, "preservation_basis": None, "manual_resume_basis": None}


def receipt(request):
    result = disposition(request["request"])
    return {"format_version": 1, "record_kind": "recovery_receipt", "record_id": request["record_id"],
        "request_sha256": request["request_sha256"], "result": result,
        "result_sha256": authority.digest(result), "parent_defer": None}


def publication():
    value = control()
    record_id = str(uuid.uuid4())
    return {"format_version": 1, "record_kind": "publication", "record_id": record_id,
        "metadata": {"publication_id": record_id, "sha256": "a" * 64, "byte_size": 123},
        "core_sha256": CORE, "claims_sha256": "b" * 64, "creation_basis": value["creation_basis"]}


def capture(store, *, binding=None, **kwargs):
    with lifecycle.shared(store.profile):
        with roots.capture_locked(store.profile, binding=store.read()["binding"] if binding is None else binding,
                                  **kwargs) as value:
            value.verify_source()
            return value.public_summary()


def populated(store):
    active = activate(store)
    value = control()
    seed(store, [("controls", value)])
    return active, value


def test_absent_is_read_only_without_sql_state_writer_or_initialization(store, monkeypatch):
    import sqlite3
    expected = store.read()["binding"]
    before = snapshot(store)
    def forbidden(*_args, **_kwargs):
        pytest.fail("Root capture must never create a StateStore, SQL connection or writer")
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(sync_state, "StateStore", forbidden)
    monkeypatch.setattr(authority, "owned", forbidden)
    monkeypatch.setattr(authority.AuthorityGuard, "activate_files", forbidden)
    with lifecycle.shared(store.profile), roots.capture_locked(store.profile, binding=expected) as result:
        assert result.summary["presence"] == "absent" and not result.references and not result.unknown
    assert snapshot(store) == before


def absent_capture(store):
    with lifecycle.shared(store.profile), roots.capture_absence_locked(store.profile) as result:
        return result.public_summary()


def test_absence_only_requires_no_binding_and_never_creates_a_state_or_sql_writer(store, monkeypatch):
    import sqlite3
    before = snapshot(store)
    def forbidden(*_args, **_kwargs):
        pytest.fail("Absence observation cannot create an identity, binding, SQL or State writer")
    monkeypatch.setattr(sync_state, "StateStore", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(authority, "owned", forbidden)
    result = absent_capture(store)
    assert result["summary"]["presence"] == "absent" and result["summary"]["binding"] is None
    assert not result["unknown"] and not result["references"]
    assert snapshot(store) == before


@pytest.mark.parametrize("damage", ["healthy", "reserved", "empty_outer", "null", "malformed", "private_part"])
def test_absence_only_any_actual_fragment_is_unknown_and_never_present(store, damage):
    value = None
    if damage in {"healthy", "reserved", "private_part"}:
        activate(store)
        value = control()
        seed(store, [("controls", value)], reserved=damage == "reserved")
        if damage == "private_part":
            write(ledger(store) / "controls" / (".sync-" + uuid.uuid4().hex + ".part"), b"unpublished", raw=True)
    elif damage == "empty_outer":
        (store.data_dir / authority.DIRECTORY).mkdir()
    else:
        write(store.data_dir / authority.DB_HEAD_NAME, b"null" if damage == "null" else b"malformed", raw=True)
    before = snapshot(store)
    result = absent_capture(store)
    assert result["summary"]["presence"] == "unknown" and result["unknown"]
    if value is not None:
        assert any(row["record_id"] == value["record_id"] for row in result["references"])
    assert snapshot(store) == before


def test_absence_only_final_verifier_rejects_appearance_after_capture(store):
    with lifecycle.shared(store.profile), pytest.raises(roots.AuthorityRootError, match="SOURCE_CHANGED"):
        with roots.capture_absence_locked(store.profile) as result:
            assert result.summary["presence"] == "absent"
            write(store.data_dir / authority.INTENT_NAME, {})


@pytest.mark.parametrize("kind", ["empty", "control", "request", "receipt", "publication"])
def test_healthy_facility_closes_only_its_scope_and_exports_no_private_body_or_path(store, kind):
    active = activate(store)
    if kind == "control":
        seed(store, [("controls", control())])
    elif kind in {"request", "receipt"}:
        request = intent(store)
        seed(store, [("requests", request)])
        if kind == "receipt":
            seed(store, [("receipts", receipt(request))])
    elif kind == "publication":
        seed(store, [("publications", publication())])
    before = snapshot(store)
    result = capture(store)
    assert result["summary"]["presence"] == "present" and not result["unknown"]
    assert result["summary"]["identity"] == {"facility_id": active["facility_id"]}
    assert result["summary"]["record_count"] == {"empty": 0, "control": 1, "request": 1, "receipt": 2, "publication": 1}[kind]
    assert all(private not in str(result) for private in ("lease_token", "target_url", "'original_request':", "expected_before",
        "creation_basis", str(store.data_dir)))
    assert snapshot(store) == before


@pytest.mark.parametrize("anchor", [authority.INTENT_NAME, authority.DB_HEAD_NAME, authority.SEAL_NAME,
    authority.CHECKPOINT_HEAD_NAME, "contract.json"])
def test_missing_anchor_is_scoped_unknown_not_absent_and_is_not_repaired(store, anchor):
    populated(store)
    path = (store.profile.checkpoints if anchor in {authority.SEAL_NAME, authority.CHECKPOINT_HEAD_NAME}
            else ledger(store) if anchor == "contract.json" else store.data_dir) / anchor
    path.unlink()
    before = snapshot(store)
    result = capture(store)
    assert result["summary"]["presence"] == "unknown" and result["unknown"][0]["scope"] == "home_authority"
    assert result["references"]
    assert snapshot(store) == before


@pytest.mark.parametrize("anchor", [authority.INTENT_NAME, authority.DB_HEAD_NAME, authority.SEAL_NAME,
    authority.CHECKPOINT_HEAD_NAME, "contract.json"])
def test_unparseable_anchor_keeps_independent_typed_journal_roots_and_is_never_repaired(store, anchor):
    _, value = populated(store)
    path = (store.profile.checkpoints if anchor in {authority.SEAL_NAME, authority.CHECKPOINT_HEAD_NAME}
            else ledger(store) if anchor == "contract.json" else store.data_dir) / anchor
    write(path, b"unparseable-anchor", raw=True)
    before = snapshot(store)
    result = capture(store)
    assert result["summary"]["presence"] == "unknown" and result["unknown"][0]["code"] == "AUTHORITY_ROOT_ANCHOR_INVALID"
    assert any(ref["record_id"] == value["record_id"] for ref in result["references"])
    assert snapshot(store) == before


def test_unparseable_anchor_without_facility_cannot_be_turned_into_absence(store):
    write(store.data_dir / authority.DB_HEAD_NAME, b"unparseable-anchor", raw=True)
    result = capture(store)
    assert result["summary"]["presence"] == "unknown" and result["unknown"]


def test_actual_null_anchor_without_facility_cannot_be_turned_into_absence(store):
    write(store.data_dir / authority.DB_HEAD_NAME, b"null", raw=True)
    result = capture(store)
    assert result["summary"]["presence"] == "unknown" and result["unknown"]


def test_unsafe_anchor_is_not_treated_as_a_deferred_decode_error(store, monkeypatch):
    populated(store)
    path = store.data_dir / authority.INTENT_NAME
    os.link(path, store.data_dir / "synthetic-intent-link.json")
    def forbidden(*_args, **_kwargs):
        pytest.fail("Unsafe physical anchor must stop before independent journal traversal")
    monkeypatch.setattr(authority, "public_record_projection", forbidden)
    result = capture(store)
    assert result["summary"]["presence"] == "unknown" and not result["references"]


def test_owner_mismatch_keeps_typed_facts_but_never_clears_unknown(store):
    active, value = populated(store)
    # Build a self-consistent copied ledger with another physical owner. Its
    # real bytes remain root facts, but cannot authorize this actual profile.
    active["owner_profile_sha256"] = "f" * 64
    for path in (store.data_dir / authority.INTENT_NAME, store.profile.checkpoints / authority.SEAL_NAME,
                 ledger(store) / "contract.json"):
        write(path, active)
    previous = authority.digest(active)
    for ordinal in (1, 2):
        path = ledger(store) / "journal" / f"{ordinal:08d}.json"
        event = sync_state._decode(path.read_bytes())
        event["previous_sha256"] = previous
        write(path, event)
        previous = authority.digest(event)
    head = {"format_version": 1, "facility_id": active["facility_id"], "ordinal": 2, "sha256": previous}
    for path in (store.data_dir / authority.DB_HEAD_NAME, store.profile.checkpoints / authority.CHECKPOINT_HEAD_NAME):
        write(path, head)
    before = snapshot(store)
    result = capture(store)
    assert result["summary"]["presence"] == "unknown"
    assert result["unknown"][0]["code"] == "AUTHORITY_ROOT_OWNER_MISMATCH"
    assert any(ref["record_id"] == value["record_id"] for ref in result["references"])
    assert snapshot(store) == before


@pytest.mark.parametrize("binding_damage", ["missing", "identity", "url", "invalid"])
def test_missing_or_wrong_expected_binding_cannot_be_filled_from_contract(store, binding_damage):
    _, value = populated(store)
    expected = deepcopy(store.read()["binding"])
    if binding_damage == "missing":
        expected.pop("target_url")
    elif binding_damage == "identity":
        expected["home_node_id"] = str(uuid.uuid4())
    elif binding_damage == "url":
        expected["target_url"] = "https://other-peer.example.test"
    else:
        expected = False
    result = capture(store, binding=expected)
    assert result["summary"]["presence"] == "unknown"
    assert any(ref["record_id"] == value["record_id"] for ref in result["references"])


def test_raw_projection_is_exact_eight_keys_and_contains_only_decoded_bounded_files(store, monkeypatch):
    populated(store)
    original = authority.pure_projection
    def inspect(value, **kwargs):
        assert set(value) == {"format_version", "contract", "intent", "seal", "db_head", "checkpoint_head", "journal", "records"}
        assert set(value["records"]) == set(authority.KINDS)
        assert type(value["journal"]) is list
        return original(value, **kwargs)
    monkeypatch.setattr(authority, "pure_projection", inspect)
    assert not capture(store)["unknown"]


def test_invalid_caller_binding_is_not_deepcopied_or_used_as_an_authority_factory(store):
    populated(store)
    class CallerObject(dict):
        def __deepcopy__(self, _memo):
            pytest.fail("Unvalidated binding must not invoke a caller factory")
    result = capture(store, binding=CallerObject(store.read()["binding"]))
    assert result["summary"]["presence"] == "unknown" and result["references"]


def test_healthy_capture_never_enters_sql_or_a_state_writer(store, monkeypatch):
    import sqlite3
    populated(store)
    expected = store.read()["binding"]
    before = snapshot(store)
    def forbidden(*_args, **_kwargs):
        pytest.fail("Permanent capture cannot read SQL, State or acquire writer ownership")
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(sync_state, "StateStore", forbidden)
    monkeypatch.setattr(authority, "owned", forbidden)
    with lifecycle.shared(store.profile), roots.capture_locked(store.profile, binding=expected) as result:
        assert result.summary["presence"] == "present" and not result.unknown
    assert snapshot(store) == before


def test_reserved_journal_keeps_typed_reference_and_never_becomes_empty_or_repaired(store):
    activate(store)
    value = control()
    seed(store, [("controls", value)], reserved=True)
    before = snapshot(store)
    result = capture(store)
    assert result["summary"]["presence"] == "unknown"
    assert any(ref["record_id"] == value["record_id"] and ref["state"] == "reserved" for ref in result["references"])
    assert snapshot(store) == before


def test_reserved_with_actual_record_is_still_unknown_despite_pure_projection_success(store):
    activate(store)
    value = control()
    seed(store, [("controls", value)], reserved=True)
    write(ledger(store) / "controls" / (value["record_id"] + ".json"), value)
    before = snapshot(store)
    result = capture(store)
    assert result["unknown"][0]["code"] == "AUTHORITY_ROOT_RESERVED"
    assert any(ref["record_id"] == value["record_id"] and ref["state"] == "reserved" for ref in result["references"])
    assert snapshot(store) == before


@pytest.mark.parametrize("damage", ["orphan", "missing_record", "rewound_head", "wrong_head", "journal_gap", "unexpected_file"])
def test_record_or_journal_damage_preserves_known_refs_and_zero_mutation(store, damage):
    active, value = populated(store)
    if damage == "orphan":
        orphan = control()
        write(ledger(store) / "controls" / (orphan["record_id"] + ".json"), orphan)
    elif damage == "missing_record":
        (ledger(store) / "controls" / (value["record_id"] + ".json")).unlink()
    elif damage in {"rewound_head", "wrong_head"}:
        write(store.profile.checkpoints / authority.CHECKPOINT_HEAD_NAME,
            {"format_version": 1, "facility_id": active["facility_id"], "ordinal": 0,
             "sha256": authority.digest(active) if damage == "rewound_head" else "0" * 64})
    elif damage == "journal_gap":
        (ledger(store) / "journal" / "00000002.json").rename(ledger(store) / "journal" / "00000003.json")
    else:
        write(ledger(store) / "receipts" / "unexpected.json", {})
    before = snapshot(store)
    result = capture(store)
    assert result["summary"]["presence"] == "unknown" and result["unknown"]
    assert any(ref["record_id"] == value["record_id"] for ref in result["references"])
    assert snapshot(store) == before


def test_open_capture_holds_no_state_writer_lock_or_mutable_head_handle(store):
    activate(store)
    expected = store.read()["binding"]
    with lifecycle.shared(store.profile), pytest.raises(roots.AuthorityRootError, match="SOURCE_CHANGED") as caught:
        with roots.capture_locked(store.profile, binding=expected) as result:
            assert not result.unknown
            seed(store, [("controls", control())])
    assert caught.value.retriable


@pytest.mark.parametrize("damage", ["head", "new_file", "missing_absent_anchor"])
def test_final_cas_rejects_mutation_after_capture(store, damage):
    if damage != "missing_absent_anchor":
        populated(store)
    expected = store.read()["binding"]
    with lifecycle.shared(store.profile), pytest.raises(roots.AuthorityRootError, match="SOURCE_CHANGED") as caught:
        with roots.capture_locked(store.profile, binding=expected) as result:
            assert not result.unknown
            if damage == "head":
                write(store.data_dir / authority.DB_HEAD_NAME, {})
            elif damage == "new_file":
                write(ledger(store) / "controls" / "unexpected.json", {})
            else:
                write(store.data_dir / authority.INTENT_NAME, {})
    assert caught.value.retriable


def test_capture_output_is_detached_and_closed_verifier_cannot_be_reused(store):
    populated(store)
    expected = store.read()["binding"]
    with lifecycle.shared(store.profile), roots.capture_locked(store.profile, binding=expected) as result:
        expected["target_node_id"] = str(uuid.uuid4())
        result.summary["presence"] = "absent"
        result.references[0]["record_id"] = "FAKE"
        assert result.summary["presence"] == "present" and result.references[0]["record_id"] != "FAKE"
    with pytest.raises(roots.AuthorityRootError, match="CAPTURE_CLOSED"):
        result.verify_source()


def test_guard_is_required_and_other_profile_guard_cannot_be_borrowed(store, tmp_path):
    expected = store.read()["binding"]
    with pytest.raises(lifecycle.StorageLifecycleError):
        with roots.capture_locked(store.profile, binding=expected):
            pass
    other = lifecycle.StorageProfile(tmp_path / "other" / "reno.db", tmp_path / "om", tmp_path / "or", tmp_path / "oc")
    lifecycle.initialize(other)
    with lifecycle.shared(other), pytest.raises(lifecycle.StorageLifecycleError):
        with roots.capture_locked(store.profile, binding=expected):
            pass


@pytest.mark.parametrize("limit", ["max_record_bytes", "max_total_bytes", "max_journal", "max_records"])
def test_actual_bounds_are_unknown_not_an_empty_healthy_facility(store, limit):
    populated(store)
    seed(store, [("controls", control())])
    result = capture(store, limits=replace(roots.CaptureLimits(), **{limit: 1}))
    assert result["summary"]["presence"] == "unknown" and result["unknown"]


@pytest.mark.parametrize("field", ["max_records", "max_journal", "max_parts", "max_record_bytes", "max_total_bytes"])
def test_hard_bounds_cannot_be_increased(field):
    with pytest.raises(ValueError):
        replace(roots.CaptureLimits(), **{field: getattr(roots.CaptureLimits(), field) + 1})


@pytest.mark.parametrize("invalid", [False, 0, -1, float("nan")])
def test_invalid_deadline_does_not_silently_mean_default(invalid):
    with pytest.raises(ValueError):
        roots.CaptureLimits(deadline_seconds=invalid)


def test_one_shot_cancellation_preserves_original_exception_identity(store):
    populated(store)
    original = ValueError("synthetic cancellation")
    calls = 0
    def check():
        nonlocal calls
        calls += 1
        if calls == 8:
            raise original
    with pytest.raises(ValueError) as found:
        capture(store, check_cancelled=check)
    assert found.value is original


def test_actual_deadline_is_unknown_and_cannot_continue_unbounded(store, monkeypatch):
    populated(store)
    actual = roots.time.monotonic
    started = actual()
    calls = 0
    def elapsed():
        nonlocal calls
        calls += 1
        return started + (20 if calls > 3 else 0)
    # A deadline during capture must terminate instead of returning a healthy
    # summary. The final verifier has the same total budget.
    monkeypatch.setattr(roots.time, "monotonic", elapsed)
    with pytest.raises(roots.AuthorityRootError, match="DEADLINE_LIMIT"):
        capture(store)


@pytest.mark.parametrize("kind", ["private", "hardlink", "size", "count"])
def test_internal_parts_are_preserved_and_bounded_without_published_authority(store, kind):
    activate(store)
    path = ledger(store) / "controls" / (".sync-" + uuid.uuid4().hex + ".part")
    write(path, b"unpublished" * 4096, raw=True)
    if kind == "hardlink":
        path.unlink()
        os.link(store.data_dir / authority.INTENT_NAME, path)
    elif kind == "size":
        write(path, b"x" * (64 * 1024 + 1), raw=True)
    elif kind == "count":
        for _ in range(32):
            write(ledger(store) / "controls" / (".sync-" + uuid.uuid4().hex + ".part"), b"", raw=True)
    before = snapshot(store)
    result = capture(store)
    assert result["summary"]["presence"] == ("present" if kind == "private" else "unknown")
    assert snapshot(store) == before


def test_record_hardlink_is_unknown_and_not_accepted_as_pinned_private_record(store):
    _, value = populated(store)
    path = ledger(store) / "controls" / (value["record_id"] + ".json")
    os.link(path, store.data_dir / "synthetic-control-link.json")
    assert capture(store)["unknown"]


def test_noncanonical_record_bytes_are_unknown_and_known_journal_roots_survive(store):
    _, value = populated(store)
    path = ledger(store) / "controls" / (value["record_id"] + ".json")
    path.write_bytes(path.read_bytes() + b"\n")
    before = snapshot(store)
    result = capture(store)
    assert result["summary"]["presence"] == "unknown" and result["unknown"]
    assert any(ref["record_id"] == value["record_id"] for ref in result["references"])
    assert snapshot(store) == before


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions and symlinks")
@pytest.mark.parametrize("damage", ["public_mode", "symlink", "record_replace", "same_size_write", "ancestor_replace"])
def test_posix_private_links_and_actual_replacements_fail_closed(store, damage):
    _, value = populated(store)
    path = ledger(store) / "controls" / (value["record_id"] + ".json")
    if damage in {"public_mode", "symlink"}:
        if damage == "public_mode":
            path.chmod(0o644)
        else:
            original = store.data_dir / "synthetic-outside-control.json"
            path.rename(original)
            path.symlink_to(original)
        assert capture(store)["unknown"]
        return
    expected = store.read()["binding"]
    with lifecycle.shared(store.profile), pytest.raises(roots.AuthorityRootError, match="SOURCE_CHANGED") as caught:
        with roots.capture_locked(store.profile, binding=expected):
            old_stat, raw = path.stat(), path.read_bytes()
            if damage == "record_replace":
                other = path.with_suffix(".replacement")
                write(other, raw, raw=True)
                other.replace(path)
            elif damage == "same_size_write":
                write(path, raw.replace(b"daily_limit", b"fake__limit"), raw=True)
                os.utime(path, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
            else:
                original = ledger(store)
                original.rename(original.with_name("old-v1"))
                original.mkdir()
    assert caught.value.retriable
