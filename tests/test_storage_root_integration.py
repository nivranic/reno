"""Real schema + committed WAL projections, still entirely synthetic TEMP data."""
import hashlib
import json
import sqlite3
import uuid

import pytest

from reno import config, db, storage_artifacts, storage_gc_contract
from reno import storage_lifecycle as lifecycle, storage_readonly, storage_root_inputs


@pytest.fixture
def library(tmp_path, monkeypatch):
    profile = lifecycle.StorageProfile(tmp_path / "data" / "reno.db", tmp_path / "media",
                                       tmp_path / "reports", tmp_path / "checkpoints")
    db.initialize(profile.database)
    lifecycle.initialize(profile)
    con = sqlite3.connect(profile.database)
    con.execute("PRAGMA journal_mode=WAL")
    digest = hashlib.sha256(b"synthetic media").hexdigest()
    keys = [f"sync/video/{digest}.mp4", f"sync/video/{'a' * 64}.mp4", f"sync/video/{'b' * 64}.mp4"]
    files = {"video": "media/" + keys[0], "audio": "media/" + keys[1]}
    con.execute("INSERT INTO video_asset(video_id,files_json,sync_state) VALUES('video',?,'pending')", (json.dumps(files),))
    con.execute("INSERT INTO frame(video_id,frame_id,path) VALUES('video','frame',?)", ("media/" + keys[2],))
    con.execute("INSERT INTO job(video_id,kind,status,payload_json) VALUES('video','process','running','{}')")
    con.commit()
    # Parsing/capture must depend exclusively on the supplied physical profile.
    def no_config(*args, **kwargs):
        raise AssertionError("Reference readers must not consult global configuration")
    monkeypatch.setattr(config, "get", no_config)
    monkeypatch.setattr(config, "local_path", no_config)
    yield {"profile": profile, "con": con, "keys": keys}
    con.close()


def capture(library):
    profile = library["profile"]
    with lifecycle.shared(profile):
        roots = {namespace: storage_artifacts.root_identity(
                    profile.media if namespace == "sync_media" else
                    profile.checkpoints if namespace == "checkpoint" else profile.reports, profile=profile)["root_id"]
                 for namespace in storage_artifacts.NAMESPACES}
        with storage_readonly.committed_image_locked(profile) as image:
            scan = storage_root_inputs.parse_image(image.connection, profile=profile, root_ids=roots,
                                                    member_id="mem_" + "a" * 64)
            fingerprint = image.source_fingerprint
            assert image.connection.in_transaction and image.connection.total_changes == 0
            return scan, fingerprint


def test_real_schema_unready_assets_all_fields_and_frames_are_reference_roots(library):
    scan, _ = capture(library)
    assert scan.blockers == ()
    assert {reference.storage_key for reference in scan.references} == set(library["keys"])
    assert {selector.video_id for selector in scan.selectors} == {"video"}
    assert library["con"].execute("SELECT sync_state FROM video_asset").fetchone()[0] == "pending"


def test_pending_wal_pages_do_not_become_root_evidence_or_terminate_the_writer(library):
    baseline, _ = capture(library)
    con = library["con"]
    con.execute("UPDATE video_asset SET files_json=?", (json.dumps({"video": "media/sync/video/" + "c" * 64 + ".mp4"}),))
    assert con.in_transaction
    scan, _ = capture(library)
    assert scan.digest == baseline.digest and con.in_transaction
    assert "c" * 64 not in {reference.storage_key.split("/")[-1].split(".")[0] for reference in scan.references}
    con.rollback()
    assert capture(library)[0].digest == baseline.digest


def test_worker_lease_only_updates_change_read_evidence_not_reference_semantics(library):
    before, raw_before = capture(library)
    con = library["con"]
    con.execute("UPDATE job SET lease_until='heartbeat-only',updated_at='display-only'")
    con.commit()
    after, raw_after = capture(library)
    assert before.digest == after.digest
    assert raw_before != raw_after


def test_reference_and_retirement_parsers_share_one_snapshot_without_changing_its_transaction(library):
    profile = library["profile"]
    with lifecycle.shared(profile), storage_readonly.committed_image_locked(profile) as image:
        roots = {namespace: storage_artifacts.root_identity(
                    profile.media if namespace == "sync_media" else
                    profile.checkpoints if namespace == "checkpoint" else profile.reports, profile=profile)["root_id"]
                 for namespace in storage_artifacts.NAMESPACES}
        scan = storage_root_inputs.parse_image(image.connection, profile=profile, root_ids=roots, member_id="mem_" + "a" * 64)
        library_id = library["con"].execute("SELECT value FROM meta WHERE key='library_id'").fetchone()[0]
        checkpoint_id = str(uuid.uuid4())
        entry = {"checkpoint_id": checkpoint_id, "publication_id": str(uuid.uuid4()),
                 "receiver_version_json": json.dumps({"library_id": library_id, "library_epoch": 1, "kb_revision": 0}),
                 "format_version": 1, "schema_version": 9, "storage_key": checkpoint_id + ".sqlite",
                 "sha256": "0" * 64, "byte_size": 1, "created_at": "2026-10-01T00:00:00.000Z"}
        assert storage_gc_contract.verify_retirement(image.connection, entry, expected_library_id=library_id).status == "not_retired"
        assert scan.blockers == () and image.connection.in_transaction and image.connection.total_changes == 0
        with pytest.raises(sqlite3.DatabaseError):
            image.connection.execute("ATTACH ':memory:' AS extra")
