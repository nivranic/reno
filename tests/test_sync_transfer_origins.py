from tests.test_sync_transfer import receiver_guard
"""Provenance is transaction-bound and part of idempotent publication payloads."""
from copy import deepcopy

import pytest

from reno import sync_queue, sync_transfer
from reno.sync_claims import _origin_digest
from reno.sync_protocol import SyncError
from tests.test_sync_transfer import env, publish, NOW, VIDEO, state
from tests.test_sync_claims import origin_proof


def binding(env):
    proof = origin_proof(source_library_id=env["identity"]["library_id"],
                         source_node_id=env["identity"]["node_id"], video_id=VIDEO,
                         extracted_video_id=VIDEO, resolved_url=f"https://www.bilibili.com/video/{VIDEO}",
                         media_sha256=env["media"]["sha256"])
    sync_queue.enqueue(env["con"], [proof["submitted_url"]], now=NOW)
    item = sync_queue.claim(env["con"], env["identity"], env["lease"], now=NOW)[0]
    env["con"].commit()
    return {"video_id": VIDEO, "claim": item["claim"], "origin": proof}


def test_origin_allows_alias_to_dedup_asset_and_is_retained_in_bindings(env):
    with receiver_guard(env):
        claim = binding(env)
        asset = env["payload"]["videos"][0]["tables"]["video_asset"][0]
        asset.update(source_type="local_file", source_url=None, source_platform=None)
        normalized = sync_transfer._bindings([claim], {VIDEO})
        assert normalized == [claim] and normalized[0]["origin"] is not claim["origin"]
        result = publish(env, claims=[claim])
        env["con"].commit()
        assert result["replayed"] is False
        assert env["con"].execute("SELECT status FROM sync_queue").fetchone()[0] == "done"
        assert env["con"].execute("SELECT source_url FROM video_asset").fetchone()[0] is None
        assert publish(env, claims=[claim])["replayed"] is True


def test_same_publication_uuid_cannot_change_valid_origin(env):
    with receiver_guard(env):
        claim = binding(env)
        publish(env, claims=[claim])
        env["con"].commit()
        altered = deepcopy(claim)
        altered["origin"]["resolution_sha256"] = "f" * 64
        altered["origin"]["mapping_id"] = _origin_digest(altered["origin"])
        with pytest.raises(SyncError) as error:
            publish(env, claims=[altered])
        assert error.value.code == "SYNC_PUBLICATION_CONFLICT"


@pytest.mark.parametrize("key,value", [("submitted_url", "https://b23.tv/other"),
    ("source_node_id", "00000000-0000-0000-0000-000000000001"), ("media_sha256", "d" * 64),
    ("video_id", "file_other")])
def test_mismatched_proof_cannot_publish_or_complete_queue(env, key, value):
    with receiver_guard(env):
        claim = binding(env)
        claim["origin"][key] = value
        claim["origin"]["mapping_id"] = _origin_digest(claim["origin"])
        before = state(env["con"])
        with pytest.raises(SyncError) as error:
            publish(env, claims=[claim])
        assert error.value.code == "SYNC_CLAIM_SOURCE_MISMATCH"
        assert state(env["con"]) == before
        assert env["con"].execute("SELECT status FROM sync_queue").fetchone()[0] == "claimed"


def test_null_origin_is_not_a_silent_legacy_downgrade(env):
    with receiver_guard(env):
        claim = binding(env)
        claim["origin"] = None
        with pytest.raises(SyncError):
            sync_transfer._bindings([claim], {VIDEO})
