"""A published multi-report backup remains verifiable after JSON round trips."""
import base64
import hashlib
import json
import uuid

import pytest

from reno import storage_backups as backups, storage_lifecycle as lifecycle
from tests.test_storage_backups import (
    MEDIA_KEY, SHA, SYNC_KEY, create, discover, no_real_config, record, synthetic,
)


@pytest.mark.parametrize("report_names", [
    ("incremental_diff", "checklist"),
    ("incremental_diff", "checklist", "conflicts", "prices"),
])
def test_ready_multi_manifest_backup_survives_json_roundtrip_and_original_replay(tmp_path, report_names):
    profile, con = synthetic(tmp_path)
    backup_id = str(uuid.uuid4())
    originals, expected = {}, {
        ("sync_media", MEDIA_KEY, SHA, None),
        ("sync_report", SYNC_KEY, "c" * 64, 3),
    }
    try:
        for name in report_names:
            storage_key = f".versions/{name}-{uuid.uuid4().hex}.md"
            content = (f"# {name}\n\nFrozen report evidence.\n").encode("utf-8")
            content_path = profile.reports / storage_key
            content_path.parent.mkdir(exist_ok=True)
            content_path.write_bytes(content)
            digest = hashlib.sha256(content).hexdigest()
            raw = (json.dumps({"content_file": storage_key, "content_sha256": digest,
                "byte_size": len(content)}, indent=2) + "\n").encode("utf-8")
            (profile.reports / (name + ".manifest.json")).write_bytes(raw)
            originals[name] = raw
            expected.add(("local_report", storage_key, digest, len(content)))

        result = create(con, profile, "before", backup_id=backup_id)
        assert result["replayed"] is False
        catalog_path, ready = record(profile, backup_id)
        sidecar_raw = result["descriptor_path"].read_bytes()
        descriptor = json.loads(sidecar_raw)
        sidecar_sha = hashlib.sha256(sidecar_raw).hexdigest()
        assert ready["state"] == "ready"
        assert ready["descriptor"] == descriptor
        assert ready["descriptor_sha256"] == sidecar_sha
        # The actual persisted JSON changes object order while retaining the
        # report set. Dependency verification must not depend on that order.
        assert tuple(name for name in descriptor["manifests"] if name in report_names) != report_names
        assert {(item["namespace"], item["storage_key"], item["sha256"], item["byte_size"])
            for item in descriptor["dependencies"]} == expected
        assert len(descriptor["dependencies"]) == len(expected)
        for name, raw in originals.items():
            frozen = descriptor["manifests"][name]
            assert frozen["state"] == "present"
            assert base64.b64decode(frozen["raw_base64"], validate=True) == raw
            assert frozen["sha256"] == hashlib.sha256(raw).hexdigest()
            assert frozen["byte_size"] == len(raw)
            # Later live report status cannot replace this backup's evidence.
            (profile.reports / (name + ".manifest.json")).write_bytes(b'{"status":"generating"}\n')

        protected = (result["path"], result["descriptor_path"], catalog_path)
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in protected}
        with lifecycle.shared(profile):
            verified, verified_sha = backups._verify_ready(ready, profile)
        assert verified == descriptor and verified_sha == sidecar_sha
        found = discover(profile)
        assert found["blockers"] == {}
        assert len(found["backups"]) == 1
        saved = found["backups"][0]
        assert saved["state"] == "ready" and saved["blockers"] == {}
        assert saved["dependencies"] == descriptor["dependencies"]
        assert saved["frozen_manifests"] == descriptor["manifests"]

        replay = create(con, profile, "before", backup_id=backup_id)
        assert replay["replayed"] is True
        assert replay["backup_id"] == backup_id
        assert replay["path"] == result["path"]
        assert replay["descriptor_path"] == result["descriptor_path"]
        assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in protected} == before
    finally:
        con.close()
