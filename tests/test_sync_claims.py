"""Completion proof distinguishes a URL's source from another published video."""
import copy
import uuid

import pytest

from reno.sync_claims import validate_claim_origin, validate_origin, _origin_digest
from reno.sync_protocol import SyncError

BV = "BV13x41117TL"
OTHER_BV = "BV11x411K7CN"
DY = "6961737553342991651"


def asset(url=None, **changes):
    return {"video_id": BV, "source_platform": "BiliBili", "source_type": "share_url",
            "source_url": url or f"https://www.bilibili.com/video/{BV}", **changes}


@pytest.mark.parametrize("queue_url,source_url", [
    (f"https://www.bilibili.com/video/{BV}", f"https://www.bilibili.com/video/{BV}"),
    (f"HTTPS://WWW.BILIBILI.COM:443/video/{BV}", f"https://www.bilibili.com/video/{BV}"),
    (f"http://www.bilibili.com:80/video/{BV}", f"http://www.bilibili.com/video/{BV}"),
    (f"https://bilibili.com/video/{BV}/", f"https://www.bilibili.com/video/{BV}"),
    (f"http://bilibili.com/video/{BV}", f"https://www.bilibili.com/video/{BV}/"),
    (f"https://www.bilibili.com/video/{BV}?x=1&token=private-test", f"https://bilibili.com/video/{BV}/?x=1&token=private-test"),
    ("https://b23.tv/ExactShortCode", "https://b23.tv/ExactShortCode"),
    ("https://B23.TV:443/ExactShortCode?share=1", "https://b23.tv/ExactShortCode?share=1"),
])
def test_verified_same_source_or_recognized_public_video_identity(queue_url, source_url):
    record = asset(source_url)
    original = copy.deepcopy(record)
    assert validate_claim_origin(queue_url, record) is None
    assert record == original


@pytest.mark.parametrize("queue_url,source_url", [
    (f"https://www.douyin.com/video/{DY}", f"https://www.douyin.com/video/{DY}"),
    (f"http://douyin.com/video/{DY}/", f"https://www.douyin.com/video/{DY}"),
    ("https://v.douyin.com/ExactShort/", "https://v.douyin.com/ExactShort/"),
])
def test_douyin_ids_remain_strings_and_public_aliases_are_platform_scoped(queue_url, source_url):
    assert validate_claim_origin(queue_url, asset(source_url, video_id=DY, source_platform="Douyin")) is None


@pytest.mark.parametrize("video_id,query", [
    (BV + "_p1", "?p=1"), (BV + "_p4", "?p=4"),
    (BV + "_p4_0", "?p=4&tracking=kept"), (BV + "_400950101", ""),
    (BV + "_p2", ""), (BV + "_p2", "?p=%32"),
])
def test_actual_ytdlp_page_and_segment_identity_forms_are_supported(video_id, query):
    url = f"https://www.bilibili.com/video/{BV}{query}"
    assert validate_claim_origin(url, asset(url, video_id=video_id)) is None


@pytest.mark.parametrize("queue_url,record", [
    (f"https://www.bilibili.com/video/{OTHER_BV}", asset()),
    (f"https://www.bilibili.com/video/{OTHER_BV}", asset(f"https://www.bilibili.com/video/{OTHER_BV}")),
    (f"https://www.bilibili.com/video/{BV}", asset(video_id=OTHER_BV)),
    (f"https://www.bilibili.com/video/{BV}?p=2", asset(f"https://www.bilibili.com/video/{BV}?p=1", video_id=BV + "_p1")),
    (f"https://www.bilibili.com/video/{BV}?p=2", asset(f"https://www.bilibili.com/video/{BV}?p=2", video_id=BV)),
    (f"https://www.bilibili.com/video/{BV}?p=2", asset(f"https://www.bilibili.com/video/{BV}?p=2", video_id=BV + "_p1")),
    (f"https://bilibili.com/video/{BV}?p=1&p=2", asset(f"https://bilibili.com/video/{BV}?p=1&p=2", video_id=BV + "_p2")),
    (f"https://bilibili.com/video/{BV}?p=", asset(f"https://bilibili.com/video/{BV}?p=")),
    (f"https://bilibili.com/video/{BV}?p=0", asset(f"https://bilibili.com/video/{BV}?p=0")),
    (f"https://www.bilibili.com/video/{BV}?tracking=new", asset(f"https://bilibili.com/video/{BV}?tracking=old")),
    (f"https://www.bilibili.com/video/{BV}?x=1&y=2", asset(f"https://bilibili.com/video/{BV}?y=2&x=1")),
    ("https://b23.tv/NewShort", asset("https://b23.tv/OldShort")),
    ("https://b23.tv/NewShort", asset()),
    (f"https://www.bilibili.com/video/{BV}", asset("https://b23.tv/OldShort")),
    ("https://b23.tv/SameShort/", asset("https://b23.tv/SameShort")),
    ("https://v.douyin.com/NewShort/", asset("https://v.douyin.com/OldShort/", video_id=DY, source_platform="Douyin")),
    (f"https://www.douyin.com/video/{DY}", asset(f"https://www.douyin.com/video/{DY}", video_id="6961737553342991652", source_platform="Douyin")),
    (f"https://www.douyin.com/video/{DY}", asset()),
    ("https://b23.tv/SameShort", asset("https://b23.tv/SameShort", video_id=DY, source_platform="Douyin")),
    ("https://v.douyin.com/SameShort/", asset("https://v.douyin.com/SameShort/", video_id=BV, source_platform="Douyin")),
    (f"https://www.bilibili.com/video/{BV}", asset(source_type="local_file")),
    (f"https://www.bilibili.com/video/{BV}", asset(source_type="local")),
    (f"https://www.bilibili.com/video/{BV}", asset(source_platform=None)),
    (f"https://www.bilibili.com/video/{BV}", asset(source_platform="douyin")),
    (f"https://www.bilibili.com/video/{BV}", asset(source_platform="bilibili.com.evil")),
    (f"https://live.bilibili.com/video/{BV}", asset()),
    (f"https://www.bilibili.com/video/{BV}/extra-path", asset()),
])
def test_unrelated_video_alias_page_or_platform_cannot_complete_a_claim(queue_url, record):
    with pytest.raises(SyncError) as error:
        validate_claim_origin(queue_url, record)
    assert error.value.code == "SYNC_CLAIM_SOURCE_MISMATCH" and error.value.status == 409


def test_unverified_resolved_url_and_alias_lists_are_not_accepted_as_proof():
    queued = "https://b23.tv/NewAlias"
    record = asset("https://b23.tv/OldAlias", resolved_url=queued, aliases=[queued], requested_url=queued)
    with pytest.raises(SyncError) as error:
        validate_claim_origin(queued, record)
    assert error.value.code == "SYNC_CLAIM_SOURCE_MISMATCH"


@pytest.mark.parametrize("invalid", [
    "http://127.0.0.1/private", "https://bilibili.com.evil.test/private",
    "https://private-user:private-token@bilibili.com/video/BV1", "file:///private/video.mp4",
    "https://bilibili.com:22/", "https://bilibili.com/%0dprivate", None, {}, [],
])
def test_s01_input_rejection_is_reused_and_all_errors_are_sanitized(invalid):
    for queued, record in ((invalid, asset()), (f"https://www.bilibili.com/video/{BV}", asset(source_url=invalid))):
        with pytest.raises(SyncError) as error:
            validate_claim_origin(queued, record)
        assert error.value.code == "SYNC_CLAIM_SOURCE_MISMATCH"
        assert "private" not in str(error.value) and "127.0.0.1" not in str(error.value)


def test_legacy_url_type_uses_the_same_origin_proof():
    assert validate_claim_origin(f"https://www.bilibili.com/video/{BV}", asset(source_type="url")) is None


def test_douyin_numeric_identity_is_not_coerced_from_integer_or_other_platform():
    url = f"https://www.douyin.com/video/{DY}"
    with pytest.raises(SyncError):
        validate_claim_origin(url, asset(url, video_id=int(DY), source_platform="Douyin"))
    with pytest.raises(SyncError):
        validate_claim_origin(url, asset(url, video_id=DY, source_platform="BiliBili"))


def origin_proof(**changes):
    origin = {"origin_version": 1, "source_library_id": str(uuid.uuid4()), "source_node_id": str(uuid.uuid4()),
              "submitted_url": "https://b23.tv/short?x=1&x=2", "resolved_url": f"https://www.bilibili.com/video/{BV}",
              "resolution_sha256": "a" * 64, "extracted_video_id": BV, "video_id": "file_canonical",
              "media_sha256": "b" * 64, "resolved_at": "2026-01-01T00:00:00.000000Z",
              "recorded_at": "2026-01-01T00:00:01.000000Z", **changes}
    origin["mapping_id"] = _origin_digest(origin)
    return origin


def proof_source(proof):
    return {"library_id": proof["source_library_id"], "node_id": proof["source_node_id"]}


def test_paired_proof_binds_local_dedup_asset_without_rewriting_its_source():
    proof = origin_proof()
    record = asset(video_id="file_canonical", source_type="local_file", source_url=None,
                   source_platform=None, content_sha256=proof["media_sha256"])
    validate_claim_origin(proof["submitted_url"], record, origin=proof, source=proof_source(proof))
    assert record["source_url"] is None


@pytest.mark.parametrize("changes", [{"origin_version": True}, {"origin_version": 2}, {"extra": 1},
    {"source_library_id": "not-uuid"}, {"source_node_id": "bad"}, {"resolution_sha256": "A" * 64},
    {"media_sha256": None}, {"submitted_url": "https://b23.tv/" + "a" * 4100},
    {"submitted_url": "https://v.douyin.com/different-platform/"}, {"resolved_url": "https://b23.tv/unresolved"},
    {"extracted_video_id": "BV1Another"}, {"video_id": "../bad"}, {"recorded_at": "2025-01-01T00:00:00.000000Z"},
    {"resolved_at": "2026-01-32T00:00:00.000000Z"}, {"recorded_at": "2026-01-01T00:00:01Z"}])
def test_closed_origin_contract_rejects_malformed_proof_even_with_recomputed_hash(changes):
    with pytest.raises(SyncError):
        validate_origin(origin_proof(**changes))


@pytest.mark.parametrize("change", ["queue_query", "queue_order", "source_library", "source_node", "asset_id", "asset_sha", "proof_hash"])
def test_origin_binding_rejects_other_queue_node_asset_or_hash(change):
    proof = origin_proof()
    source, queued = proof_source(proof), proof["submitted_url"]
    record = asset(video_id=proof["video_id"], content_sha256=proof["media_sha256"])
    if change == "queue_query":
        queued += "&tracking=other"
    elif change == "queue_order":
        queued = "https://b23.tv/short?x=2&x=1"
    elif change.startswith("source_"):
        source["library_id" if change == "source_library" else "node_id"] = str(uuid.uuid4())
    elif change == "asset_id":
        record["video_id"] = "other"
    elif change == "asset_sha":
        record["content_sha256"] = "f" * 64
    else:
        proof["submitted_url"] += "x"
    with pytest.raises(SyncError) as error:
        validate_claim_origin(queued, record, source=source, origin=proof)
    assert error.value.code == "SYNC_CLAIM_SOURCE_MISMATCH"


@pytest.mark.parametrize("proof", [None, [], {}, "not-json"])
def test_origin_validator_rejects_absent_or_untyped_attestation(proof):
    with pytest.raises(SyncError):
        validate_origin(proof)
