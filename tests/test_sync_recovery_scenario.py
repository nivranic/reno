"""Actual two-process CLI recovery and fenced full republication."""
import json
import socket

import pytest

from scripts import run_sync_scenario as scenario


def test_real_recovery_pause_reviews_and_republication(tmp_path):
    pytest.importorskip("imageio_ffmpeg")
    manifest_path = scenario.run(tmp_path / "recovery-chain", recovery=True)
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    evidence = json.loads((manifest_path.parent / "evidence.json").read_text(encoding="utf-8"))
    assert evidence["status"] == "passed"
    assert evidence["checks"]["interrupted_upload_intent_reserved_before_body"] is True
    assert evidence["checks"]["interrupted_upload_pin_retained_after_disconnect"] is True
    result = evidence["recovery"]
    for key in ("restored_and_removed_newer_members", "restore_idempotent", "publication_and_review_history_preserved",
                "hold_survived_process_restart", "reviews_continue_while_queue_attempts_unchanged", "old_outbox_fenced",
                "resume_fresh_full_generation", "lost_resume_reply_replayed_once", "queue_resumed_after_publication"):
        assert result[key] is True
    assert result["restored_video_count"] == 1 and result["republished_video_count"] == 3
    assert result["final_control"]["state"] == "running"
    assert evidence["personal_domain"]["before"] == evidence["personal_domain"]["after"]
    assert value["auth_token"] not in json.dumps(evidence) and "lease_token" not in json.dumps(evidence)
    with socket.socket() as connection:
        connection.settimeout(0.25)
        assert connection.connect_ex(("127.0.0.1", value["port"])) != 0
