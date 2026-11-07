"""Permanent public refs resolve against actual files without writer authority."""
from copy import deepcopy
import hashlib
import uuid

import pytest

from reno import storage_home_roots as home, storage_source_authority_roots as roots
from reno import storage_lifecycle as lifecycle, sync_state, sync_source_authority as authority
from tests.test_storage_source_authority_roots import store, activate, seed, publication, intent, write  # noqa: F401
from tests.test_sync_source_execution_state import ready
from tests.test_sync_state import claim
from tests.test_storage_home_roots import ROOTS, outbox


@pytest.fixture
def permanent(store):
    core = ready(store)
    state = store.enable_source_readmit()
    active = activate(store)
    core_sha = authority.digest(core)
    origin = {"format_version": 1, "record_kind": "origin",
        "record_id": str(uuid.uuid5(uuid.UUID(core["continuation_id"]), "source-origin:v1:" + core_sha)),
        "binding": state["binding"], "core": core, "claim": claim(7)["claim"], "core_sha256": core_sha,
        "created_at": active["activated_at"]}
    identity = {"library_id": state["binding"]["home_library_id"], "node_id": state["binding"]["home_node_id"]}
    path, metadata, _envelope = outbox((store.profile, identity))
    published = publication()
    published.update(record_id=metadata["publication_id"], metadata=metadata, core_sha256=core_sha)
    published["creation_basis"]["core_sha256"] = core_sha
    authority.validate_record("origins", origin, active)
    authority.validate_record("publications", published, active)
    seed(store, [("origins", origin), ("publications", published)])
    def ref(record):
        return {"format_version": 1, "record_kind": record["record_kind"], "record_id": record["record_id"],
                "record_sha256": authority.digest(record)}
    state["source_authority"] = {"format_version": 1, "core_sha256": core_sha,
        "origin": ref(origin), "defer": None, "resume": None, "failure": None, "publication": ref(published)}
    sync_state._validate(state)
    write(store.data_dir / "sync-state.json", state)
    return store, state, identity, origin, published, path


def parse(permanent, *, change=None):
    store, state, identity, _origin, _published, _path = permanent
    with lifecycle.shared(store.profile), roots.capture_locked(store.profile, binding=state["binding"]) as authority_capture:
        with home.capture_locked(store.profile) as captured:
            projection = authority_capture.public_summary()
            if change is not None:
                change(projection)
            return home.parse_authority_links(captured.bundle, projection, profile=store.profile,
                root_ids=ROOTS, member_id="mem_home_test", source_identity=identity)


def test_actual_committed_refs_and_original_outbox_preserve_media_without_private_output(permanent):
    result = parse(permanent)
    assert not result.blockers and all(value.state == "present" for value in result.source_bindings)
    assert len(result.references) == 1 and result.references[0].sha256 == "a" * 64
    public = result.canonical_bytes()
    assert all(secret not in public for secret in (b"PRIVATE SNAPSHOT BODY", b"lease_token", b"target_url"))


@pytest.mark.parametrize("kind", ["old4", "null_pointer"])
def test_actual_permanent_publication_retains_media_independently_of_current_pointer(permanent, kind):
    store, state, *_ = permanent
    value = deepcopy(state)
    value.update(source_authority=None, source_admission=None, source_recovery_pending=None)
    if kind == "old4":
        value["format_version"] = 4
        value.pop("source_authority")
        value.pop("source_recovery_pending")
    else:
        value["source_execution"] = None
    sync_state._validate(value)
    write(store.data_dir / "sync-state.json", value)
    assert len(parse(permanent).references) == 1


@pytest.mark.parametrize("damage", ["row_missing", "reserved", "record_sha", "core_sha"])
def test_state5_exact_ref_mismatch_is_unknown_and_cannot_release_actual_outbox(permanent, damage):
    def changed(projection):
        row = next(value for value in projection["references"] if value["kind"] == "publications")
        if damage == "row_missing":
            projection["references"].remove(row)
        elif damage == "reserved":
            row["state"] = "reserved"
        else:
            row["record_sha256" if damage == "record_sha" else "core_sha256"] = "0" * 64
    result = parse(permanent, change=changed)
    assert any(value.state == "unknown" for value in result.source_bindings)
    assert {value.namespace for value in result.blockers} == {"sync_media"}
    if damage != "row_missing":
        assert len(result.references) == 1


@pytest.mark.parametrize("damage", ["missing", "same_size_rewrite", "foreign_library"])
def test_missing_or_wrong_original_outbox_has_only_concrete_media_scope_blocker(permanent, damage):
    _store, _state, _identity, _origin, _published, path = permanent
    if damage == "missing":
        path.unlink()
    elif damage == "same_size_rewrite":
        path.write_bytes(b"x" * path.stat().st_size)
    else:
        value = sync_state._decode(path.read_bytes())
        value["snapshot"]["version"]["library_id"] = str(uuid.uuid4())
        raw = authority._encoded(value)
        write(path, raw, raw=True)
        # Hash alone cannot substitute the actual source/library identity.
        def changed(projection):
            metadata = next(row for row in projection["references"] if row["kind"] == "publications")["publication_metadata"]
            metadata.update(sha256=hashlib.sha256(raw).hexdigest(), byte_size=len(raw))
        result = parse(permanent, change=changed)
        assert {value.namespace for value in result.blockers} == {"sync_media"}
        return
    result = parse(permanent)
    assert {value.namespace for value in result.blockers} == {"sync_media"}


def test_pending_ref_uses_original_full_intent_sha_and_exact_source_kind(permanent):
    store, state, _identity, _origin, _published, _path = permanent
    pending = intent(store)
    core_sha = state["source_authority"]["core_sha256"]
    pending["core_sha256"] = pending["expected_before"]["core_sha256"] = core_sha
    pending["request"]["original"]["core_sha256"] = core_sha
    pending["request_sha256"] = authority.digest(pending["request"])
    seed(store, [("requests", pending)])
    state["source_recovery_pending"] = {"format_version": 1, "kind": pending["request"]["kind"],
        "request_id": pending["record_id"], "request_sha256": pending["request_sha256"], "core_sha256": core_sha,
        "intent_sha256": authority.digest(pending)}
    write(store.data_dir / "sync-state.json", state)
    assert all(value.state == "present" for value in parse(permanent).source_bindings)
    state["source_recovery_pending"]["intent_sha256"] = pending["request_sha256"]
    write(store.data_dir / "sync-state.json", state)
    assert any(value.state == "unknown" for value in parse(permanent).source_bindings)


def test_state5_refs_are_observed_even_when_permanent_facility_is_actually_missing(permanent):
    store, state, identity, *_ = permanent
    with lifecycle.shared(store.profile), home.capture_locked(store.profile) as captured:
        assert home.has_authority_refs(captured.bundle, source_identity=identity)
        result = home.parse_authority_links(captured.bundle,
            {"summary": {"presence": "absent"}, "references": [], "unknown": []}, profile=store.profile,
            root_ids=ROOTS, member_id="mem_home_test", source_identity=identity)
        assert any(value.state == "unknown" for value in result.source_bindings)
        assert {value.namespace for value in result.blockers} == {"sync_media"}
