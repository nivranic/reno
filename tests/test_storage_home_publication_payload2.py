"""Actual body capture and pure conservative publication2 media projections."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import uuid

import pytest

from reno import storage_home_roots as home, storage_lifecycle as lifecycle
from reno import storage_source_authority_roots as authority_roots, sync_state
from reno import sync_source_publication_local2 as publications
from tests.test_storage_home_roots import env as filesystem_env, outbox, write, ROOTS, SHA, OTHER_SHA
from tests.test_sync_agent import env
from tests.test_sync_source_genesis17 import home17, live17
from tests.test_sync_source_publication_freeze17 import publication17, freeze


def body_candidate(value, *, video="BV1Test", sha=SHA, title=None, name=None):
    path, metadata, envelope = outbox(value, video=video, sha=sha)
    path.unlink()
    if title is not None:
        envelope["snapshot"]["videos"][0]["tables"]["video_asset"][0]["title"] = title
    raw = sync_state._bounded(envelope, maximum=sync_state.MAX_OUTBOX_BYTES)
    token = hashlib.sha256(raw).hexdigest()
    write(value, publications.PAYLOAD_FAMILY + "/" + (token + ".json" if name is None else name), raw)
    return raw, token


def scan_files(value, *, limits=None):
    profile, identity = value
    with lifecycle.shared(profile), home.capture_locked(profile, limits=limits) as captured:
        bundle = deepcopy(captured.bundle)
        result = home.parse_bundle(bundle, profile=profile, root_ids=ROOTS,
            member_id="payload_home_test", source_identity=identity, limits=limits)
    return bundle, result


def ref_keys(result):
    return {ref.storage_key for ref in result.references}


def test_unselected_valid_body_retains_media_without_state_or_outbox(filesystem_env):
    raw, sha = body_candidate(filesystem_env)
    bundle, result = scan_files(filesystem_env)
    assert any(record.kind == "publication_payload" and record.name == sha + ".json" and record.raw == raw
               for record in bundle.records)
    assert "sync/BV1Test/" + SHA + ".mp4" in ref_keys(result)
    assert not result.blockers


@pytest.mark.parametrize("change", ["wrong_sha", "part", "corrupt"])
def test_unknown_candidate_or_part_is_media_only_and_keeps_known_media(filesystem_env, change):
    if change == "wrong_sha":
        body_candidate(filesystem_env, name="f" * 64 + ".json")
        expected = "sync/BV1Test/" + SHA + ".mp4"
    elif change == "part":
        body_candidate(filesystem_env, name=".sync-" + uuid.uuid4().hex + ".part")
        expected = "sync/BV1Test/" + SHA + ".mp4"
    else:
        body_candidate(filesystem_env, video="BV1Keep", sha=OTHER_SHA)
        write(filesystem_env, publications.PAYLOAD_FAMILY + "/" + "f" * 64 + ".json", b"{incomplete")
        expected = "sync/BV1Keep/" + OTHER_SHA + ".mp4"
    _, result = scan_files(filesystem_env)
    assert expected in ref_keys(result)
    assert result.blockers and {block.namespace for block in result.blockers} == {"sync_media"}


def test_body_above_small_record_budget_uses_its_own_declared_cap(filesystem_env):
    raw, _ = body_candidate(filesystem_env, title="source body " * 8000)
    assert len(raw) > 64 * 1024
    _, result = scan_files(filesystem_env, limits=home.HomeLimits(max_state_bytes=128))
    assert "sync/BV1Test/" + SHA + ".mp4" in ref_keys(result)
    assert not result.blockers


@pytest.fixture
def selected_payload(publication17):
    value = publication17
    freeze(value)
    with value.store.reference_operation(), authority_roots.capture_locked(
            value.profile, binding=value.store.read()["binding"]) as captured:
        projection = {"summary": deepcopy(captured.summary),
            "references": deepcopy(list(captured.references)), "unknown": deepcopy(list(captured.unknown))}
    row = next(row for row in projection["references"] if row["kind"] == "publications")
    edge = next(edge for edge in row["refs"] if edge["kind"] == "publication_payload")
    raw = (value.store.data_dir / publications.PAYLOAD_FAMILY / (edge["sha256"] + ".json")).read_bytes()
    body = home.CapturedRecord("publication_payload", edge["sha256"] + ".json", raw)
    outbox_record = home.CapturedRecord("outbox", row["record_id"] + ".json", raw)
    return value, projection, row, body, outbox_record


def parse_selected(value, projection, records):
    return home.parse_authority_links(home.CapturedBundle(tuple(records), ()), projection,
        profile=value.profile, root_ids=ROOTS, member_id="selected_payload_test",
        source_identity={"library_id": value.source["library_id"], "node_id": value.source["node_id"]})


def authority_state(result):
    return next(binding.state for binding in result.source_bindings if binding.section == "authority")


def test_real_selected_payload_protects_media_without_mutable_state_or_outbox(selected_payload, monkeypatch):
    value, projection, row, body, _ = selected_payload
    def forbidden(*args, **kwargs):
        pytest.fail("The pure original-body parser opened live files or SQL")
    monkeypatch.setattr(home.safe._Directory, "__enter__", forbidden)
    result = parse_selected(value, projection, [body])
    assert any(ref.storage_key.startswith("sync/publication17-video/") for ref in result.references)
    assert authority_state(result) == "present" and not result.blockers


@pytest.mark.parametrize("change", ["missing", "duplicate_edge", "claims", "core", "outbox"])
def test_selected_body_mismatch_is_unknown_and_preserves_actual_known_media(selected_payload, change):
    value, projection, row, body, outbox_record = selected_payload
    records = [body, outbox_record]
    if change == "missing":
        records = [outbox_record]
    elif change == "duplicate_edge":
        row["refs"].append(deepcopy(next(edge for edge in row["refs"] if edge["kind"] == "publication_payload")))
    elif change == "claims":
        next(edge for edge in row["refs"] if edge["kind"] == "publication_payload")["claims_sha256"] = "f" * 64
    elif change == "core":
        origin = next(row for row in projection["references"] if row["kind"] == "origins")
        next(edge for edge in origin["refs"] if edge["kind"] == "source_core")["submitted_url_sha256"] = "f" * 64
    else:
        records[1] = replace(outbox_record, raw=outbox_record.raw + b" ")
    result = parse_selected(value, projection, records)
    assert authority_state(result) == "unknown"
    assert result.blockers and {block.namespace for block in result.blockers} == {"sync_media"}
    assert any(ref.storage_key.startswith("sync/publication17-video/") for ref in result.references)


def test_original_copied_unpublished_part_keeps_authority_healthy_and_media_unknown(selected_payload):
    value, projection, row, body, _ = selected_payload
    part = home.CapturedRecord("part", publications.PAYLOAD_FAMILY + "/.sync-" + uuid.uuid4().hex + ".part", body.raw)
    result = parse_selected(value, projection, [body, part])
    assert authority_state(result) == "present"
    assert result.blockers and {block.namespace for block in result.blockers} == {"sync_media"}
    assert any(ref.storage_key.startswith("sync/publication17-video/") for ref in result.references)
