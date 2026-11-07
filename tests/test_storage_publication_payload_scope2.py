"""Real full-member scans preserve publication body uncertainty in its scope."""
import hashlib
import uuid

import pytest

from reno import storage_home_roots as home, storage_lifecycle as lifecycle
from reno import sync_protocol, sync_source_readmit_schema2 as schema2
from reno import sync_source_publication_local2 as publications
from tests.schema_fixtures import assert_current17_runtime
from tests.test_storage_home_roots import write
from tests.test_storage_home_publication_payload2 import body_candidate
from tests.test_storage_permanent_roots_group17 import home_facility
from tests.test_storage_roots_group import libraries, enroll, registered, scan, statuses  # noqa: F401
from tests.test_storage_home_recovery_shared17 import attach_group
from tests.test_sync_agent import env  # noqa: F401
from tests.test_sync_source_genesis17 import home17, live17  # noqa: F401
from tests.test_sync_source_publication_freeze17 import publication17, freeze
from tests.test_sync_source_readmit_contract import SOURCE


def current_home(libraries):
    """Use normal current17 initialization, with the original Home identity."""
    assert_current17_runtime()
    group = libraries()
    group.update(library_id=SOURCE["library_id"], node_id=SOURCE["node_id"])
    con = group["con"]
    con.execute("UPDATE meta SET value=? WHERE key='library_id'", (group["library_id"],))
    (group["profile"].database.parent / "node-identity.json").write_text(
        '{"instance_id":"' + group["node_id"] + '"}', encoding="utf-8")
    con.commit()
    enroll(group)
    assert con.execute("SELECT value FROM meta WHERE key='migration_version'").fetchone()[0] == "17"
    assert schema2.detect(con) == "admission2_dispose2"
    handshake = sync_protocol.schema_handshake(con)
    assert (handshake["protocol_version"], handshake["migration_version"]) == (3, 17)
    return group


def identity(group):
    return {key: group[key] for key in ("library_id", "node_id")}


def authority_projection(result):
    return next(value["source_authority"] for value in result.source_bindings
                if "source_authority" in value)


def section_states(result, section):
    return {value.state for value in result.base_inputs.source_bindings
            if value.section == section}


def assert_media_only(result, report):
    assert result.effective_inputs.blockers
    assert {value.namespace for value in result.effective_inputs.blockers} == {"sync_media"}
    assert statuses(result)[report["artifact_id"]] == "candidate"


@pytest.mark.parametrize("change", ["wrong_sha", "part", "corrupt"])
def test_full_group_unselected_candidate_unknown_does_not_change_healthy_authority(libraries, change):
    group = current_home(libraries)
    home_facility(group)
    _media_path, media = registered(group, content=b"original candidate media", video="candidate-known")
    _report_path, report = registered(group, "local_report")
    filesystem = group["profile"], identity(group)
    if change == "wrong_sha":
        body_candidate(filesystem, video="candidate-known", sha=media["sha256"], name="f" * 64 + ".json")
    elif change == "part":
        body_candidate(filesystem, video="candidate-known", sha=media["sha256"],
            name=".sync-" + uuid.uuid4().hex + ".part")
    else:
        body_candidate(filesystem, video="candidate-known", sha=media["sha256"])
        write(filesystem, publications.PAYLOAD_FAMILY + "/" + "f" * 64 + ".json", b"{incomplete")
    before = group["con"].total_changes
    result = scan(group)
    assert section_states(result, "source_authority") == {"present"}
    assert section_states(result, "authority") == {"present"}
    assert "unknown" in section_states(result, "publication_payload")
    assert not authority_projection(result)["unknown"]
    assert_media_only(result, report)
    assert any(value.storage_key == media["storage_key"] for value in result.base_inputs.references)
    assert group["con"].total_changes == before


@pytest.mark.parametrize("count", [16, 32])
def test_full_group_many_outer_parts_keep_every_name_and_byte_without_report_blocker(libraries, count):
    group = current_home(libraries)
    home_facility(group)
    _media_path, media = registered(group, content=b"original outer part media", video="outer-part-known")
    _report_path, report = registered(group, "local_report")
    filesystem = group["profile"], identity(group)
    raw, _sha = body_candidate(filesystem, video="outer-part-known", sha=media["sha256"])
    parts = {".sync-" + uuid.uuid4().hex + ".part": raw for _ in range(count)}
    for name, content in parts.items():
        write(filesystem, publications.PAYLOAD_DIRECTORY + "/" + name, content)
    with lifecycle.shared(group["profile"]), home.capture_locked(group["profile"]) as captured:
        bundle = captured.bundle
        assert dict(bundle.directory_entries)["publication_payload_root"] == tuple(sorted((*parts, "v1")))
        assert {record.name.removeprefix(publications.PAYLOAD_DIRECTORY + "/"): record.raw
                for record in bundle.records if record.kind == "part"} == parts
        assert bundle.issues == (("publication_payload_root", "HOME_PUBLICATION_PART_UNKNOWN"),)
    before = group["con"].total_changes
    result = scan(group)
    assert_media_only(result, report)
    assert section_states(result, "source_authority") == {"present"}
    assert section_states(result, "authority") == {"present"}
    assert "unknown" in section_states(result, "part")
    assert not authority_projection(result)["unknown"]
    assert any(value.storage_key == media["storage_key"] for value in result.base_inputs.references)
    assert group["con"].total_changes == before


@pytest.mark.parametrize("count", [33])
def test_outer_part_count_cap_still_blocks_media_without_blocking_reports(libraries, count):
    group = current_home(libraries)
    home_facility(group)
    _report_path, report = registered(group, "local_report")
    filesystem = group["profile"], identity(group)
    raw, _sha = body_candidate(filesystem)
    for _ in range(count):
        write(filesystem, publications.PAYLOAD_DIRECTORY + "/.sync-" + uuid.uuid4().hex + ".part", raw)
    result = scan(group)
    assert_media_only(result, report)
    assert "HOME_FILE_COUNT_LIMIT" in {value.code for value in result.effective_inputs.blockers}


@pytest.mark.parametrize("change", ["missing", "corrupt"])
def test_full_group_bad_selected_original_body_remains_authority_unknown(publication17, change):
    value = publication17
    freeze(value)
    attach_group(value)
    group = value.group
    try:
        _report_path, report = registered(group, "local_report")
        metadata = value.store.read()["pending_publication"]
        body = value.profile.database.parent / publications.PAYLOAD_FAMILY / (metadata["sha256"] + ".json")
        outbox = value.profile.database.parent / "sync-outbox" / (metadata["publication_id"] + ".json")
        original = outbox.read_bytes()
        assert hashlib.sha256(original).hexdigest() == metadata["sha256"]
        if change == "missing":
            body.unlink()
        else:
            body.write_bytes(b"{incomplete")
        before = group["con"].total_changes
        result = scan(group)
        assert section_states(result, "source_authority") == {"unknown"}
        assert section_states(result, "authority") == {"unknown"}
        assert authority_projection(result)["unknown"]
        assert_media_only(result, report)
        assert any(ref.storage_key.startswith("sync/publication17-video/")
                   for ref in result.base_inputs.references)
        assert outbox.read_bytes() == original and group["con"].total_changes == before
    finally:
        group["con"].close()
