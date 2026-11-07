"""GC must preserve original pending evidence even with overlapping roots."""
from reno import sync_source_legacy_pending_state17 as capsules
from tests.test_storage_roots_group import libraries, enroll, registered, scan, statuses


def test_registered_report_inside_pending_evidence_cannot_be_collected(libraries, tmp_path):
    # Custom roots can overlap private DATA. Even a valid owned report with no
    # logical State reference cannot enroll any file from this recovery tree.
    data = tmp_path / "home/data"
    evidence = data / capsules.DIRECTORY
    env = libraries("home", reports=evidence)
    enroll(env)
    path, item = registered(env, "local_report", content=b"private original recovery evidence")
    before = path.read_bytes(), path.stat().st_mtime_ns, env["con"].total_changes
    result = scan(env)
    assert statuses(result)[item["artifact_id"]] == "protected"
    assert item["artifact_id"] not in {value["artifact_id"] for value in result.eligible_artifacts}
    assert (path.read_bytes(), path.stat().st_mtime_ns, env["con"].total_changes) == before
