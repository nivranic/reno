"""Executable QA isolation and real subprocess recovery, with synthetic I/O."""
import json
from pathlib import Path
import socket
import tempfile

import pytest

from scripts import run_sync_scenario as scenario
from scripts.sync_scenario_node import load_manifest


def test_scenario_refuses_existing_repository_and_non_temp_roots(tmp_path):
    for root in (tmp_path, scenario.REPO, scenario.REPO.parent,
                 scenario.REPO / "data/new-qa", Path(tempfile.gettempdir())):
        with pytest.raises(ValueError, match="NEW directory"):
            scenario.validate_new_root(root)


@pytest.mark.parametrize("port,origin", [(8765, "http://127.0.0.1:8765"),
    (18000, "http://external.invalid:18000"), (True, "http://127.0.0.1:1")])
def test_child_rejects_production_port_and_foreign_origin_before_loading_nodes(tmp_path, port, origin):
    instance = "a" * 32
    path = tmp_path / "qa-manifest.json"
    path.write_text(json.dumps({"root": str(tmp_path), "qa_instance_id": instance, "fixture_version": "sync-chain-v1",
        "auth_token": "reno-qa-" + instance, "port": port, "base_url": origin}), encoding="utf-8")
    with pytest.raises(ValueError, match="QA (port|origin)"):
        load_manifest(path)


def test_real_subprocess_sync_chain_faults_and_default_cleanup(tmp_path):
    pytest.importorskip("imageio_ffmpeg")
    manifest_path = scenario.run(tmp_path / "sync-chain")
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    evidence = json.loads((manifest_path.parent / "evidence.json").read_text(encoding="utf-8"))
    assert evidence["status"] == "passed" and not evidence["server_retained"]
    for key in ("normal_ingest_publish_search_range", "lost_reply_same_publication_replayed_once",
                "process_crash_network_outage_durable_recovery", "uploaded_unpublished_media_invisible",
                "interrupted_upload_intent_reserved_before_body", "interrupted_upload_pin_retained_after_disconnect",
                "interrupted_upload_cleaned_and_invisible", "bidirectional_review_events",
                "personal_tables_and_attachments_unchanged"):
        assert evidence["checks"][key] is True
    assert evidence["checks"]["expired_lease_rejected"] == "SYNC_LEASE_EXPIRED"
    assert evidence["source_mapping_count"] == 3
    from reno import tasks
    assert all(item["synthetic_worker_lease_ms"] == tasks.LEASE_MS for item in evidence["home_runs"])
    pids = [item["pid"] for item in evidence["home_runs"]]
    assert len(set(pids)) == len(pids)
    raw = json.dumps(evidence)
    assert value["auth_token"] not in raw
    assert "synthetic-sync-" + value["qa_instance_id"] not in raw
    assert "lease_token" not in raw
    with socket.socket() as client:
        client.settimeout(0.25)
        assert client.connect_ex(("127.0.0.1", value["port"])) != 0
