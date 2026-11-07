"""Synthetic HTTP/DNS and temporary media only; no platform access."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import socket
import threading
from time import monotonic
from types import SimpleNamespace
import uuid

import httpx
import pytest

from reno import config, db, ingest, storage_admin, storage_lifecycle, sync_sources as sources
from reno.sync_claims import validate_claim_origin
from reno.sync_protocol import SyncError

BV = "BV13x41117TL"
FINAL = f"https://www.bilibili.com/video/{BV}?tracking=1"
SHORT = "https://b23.tv/short?x=1&x=2"
STAMP = "2026-01-01T00:00:00.000000Z"
CONTENT = b"synthetic source media, no network"


def resolution(submitted=SHORT, final=FINAL):
    return {"resolution_version": 1, "submitted_url": submitted, "resolved_url": final,
            "resolved_at": STAMP, "hops": [
                {"url": submitted, "peer_ip": "8.8.8.8", "status": 302, "next_url": final},
                {"url": final, "peer_ip": "1.1.1.1", "status": 200, "next_url": None}]}


@pytest.fixture()
def mock_network(monkeypatch):
    observed, clients = [], []
    handler = [lambda request: httpx.Response(302, headers={"Location": FINAL})
               if request.headers["host"] == "b23.tv" else httpx.Response(200)]
    original = httpx.Client

    def route(request):
        observed.append(request)
        return handler[0](request)

    def client(**kwargs):
        clients.append(kwargs)
        kwargs.pop("transport").close()
        return original(transport=httpx.MockTransport(route), **kwargs)

    monkeypatch.setattr(sources.httpx, "Client", client)
    monkeypatch.setattr(sources.socket, "getaddrinfo", lambda host, port, **kw:
                        [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port))])
    return observed, clients, handler


def test_redirects_pin_ip_preserve_host_and_sni_without_proxy_or_cookies(mock_network, monkeypatch):
    observed, clients, handler = mock_network
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    handler[0] = lambda request: (httpx.Response(302, headers={"Location": FINAL, "Set-Cookie": "secret=x"})
        if request.headers["host"] == "b23.tv" else httpx.Response(200))
    result = sources.resolve_url(SHORT)
    assert result["submitted_url"] == SHORT and result["resolved_url"] == FINAL
    assert [str(r.url.host) for r in observed] == ["8.8.8.8", "8.8.8.8"]
    assert [r.headers["host"] for r in observed] == ["b23.tv", "www.bilibili.com"]
    assert [r.extensions["sni_hostname"] for r in observed] == ["b23.tv", "www.bilibili.com"]
    assert all(c["trust_env"] is False and c["follow_redirects"] is False and c["verify"] is True for c in clients)
    assert all("cookie" not in r.headers and "authorization" not in r.headers for r in observed)


@pytest.mark.parametrize("answer", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "192.168.1.1", "0.0.0.0",
    "::1", "fe80::1", "fc00::1", "::ffff:8.8.8.8", "2002:0808:0808::", "224.0.0.1", "240.0.0.1"])
def test_private_reserved_or_transition_dns_never_connects(answer, mock_network, monkeypatch):
    monkeypatch.setattr(sources.socket, "getaddrinfo", lambda *a, **k:
                        [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (answer, 443))])
    with pytest.raises(SyncError):
        sources.resolve_url(SHORT)
    assert mock_network[0] == []


def test_mixed_public_private_dns_answer_rejected_before_connect(mock_network, monkeypatch):
    monkeypatch.setattr(sources.socket, "getaddrinfo", lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))])
    with pytest.raises(SyncError):
        sources.resolve_url(SHORT)
    assert mock_network[0] == []


def test_rebinding_cannot_trigger_second_dns_resolution_for_connection(mock_network, monkeypatch):
    calls = []
    def dns(host, port, **kwargs):
        calls.append(host)
        address = "8.8.8.8" if len(calls) == 1 else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]
    monkeypatch.setattr(sources.socket, "getaddrinfo", dns)
    mock_network[2][0] = lambda request: httpx.Response(200)
    assert sources.resolve_url(FINAL)["resolved_url"] == FINAL
    assert calls == ["www.bilibili.com"] and mock_network[0][0].url.host == "8.8.8.8"


@pytest.mark.parametrize("target", ["http://www.bilibili.com/video/BV1", "https://127.0.0.1/secret",
    "https://evil.example/video/BV1", "https://user:secret@www.bilibili.com/video/BV1",
    "https://www.douyin.com/video/123", "https://www.bilibili.com/video/BV1#frag", "file:///private"])
def test_every_redirect_validated_before_next_connection(target, mock_network):
    mock_network[2][0] = lambda request: httpx.Response(302, headers={"Location": target})
    with pytest.raises(SyncError):
        sources.resolve_url(SHORT)
    assert len(mock_network[0]) == 1


def test_relative_redirects_use_domain_url_not_pinned_ip(mock_network):
    mock_network[2][0] = lambda request: (httpx.Response(302, headers={"Location": f"/video/{BV}?p=2"})
        if request.url.path == "/start" else httpx.Response(200))
    assert sources.resolve_url("https://www.bilibili.com/start")["resolved_url"] == f"https://www.bilibili.com/video/{BV}?p=2"


@pytest.mark.parametrize("kind", ["loop", "limit", "missing", "duplicate", "header", "status", "unresolved"])
def test_bounded_redirects_headers_and_final_resource(kind, mock_network):
    seen = []
    def handler(request):
        seen.append(request)
        if kind == "loop":
            return httpx.Response(302, headers={"location": SHORT})
        if kind == "limit":
            return httpx.Response(302, headers={"location": f"https://b23.tv/hop{len(seen)}"})
        if kind == "missing":
            return httpx.Response(302)
        if kind == "duplicate":
            return httpx.Response(302, headers=[("location", FINAL), ("location", FINAL)])
        if kind == "header":
            return httpx.Response(200, headers={"oversized": "a" * sources.MAX_HEADER_BYTES})
        return httpx.Response(503 if kind == "status" else 200)
    mock_network[2][0] = handler
    with pytest.raises(SyncError):
        sources.resolve_url(SHORT)
    assert len(seen) <= sources.MAX_REDIRECTS + 1


def test_response_body_is_never_consumed(mock_network):
    class RefuseBody(httpx.SyncByteStream):
        def __iter__(self):
            raise AssertionError("Resolver must not consume platform HTML")
    mock_network[2][0] = lambda request: httpx.Response(200, stream=RefuseBody())
    assert sources.resolve_url(FINAL)["resolved_url"] == FINAL


def test_ipv6_is_pinned_with_brackets_and_domain_sni(mock_network, monkeypatch):
    monkeypatch.setattr(sources.socket, "getaddrinfo", lambda *a, **k:
        [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:4700:4700::1111", 443, 0, 0))])
    mock_network[2][0] = lambda request: httpx.Response(200)
    sources.resolve_url(FINAL)
    assert str(mock_network[0][0].url).startswith("https://[2606:4700:4700::1111]/")


def test_dns_timeout_is_bounded_and_network_never_starts(mock_network, monkeypatch):
    release, finished = threading.Event(), threading.Event()
    def stalled(*args, **kwargs):
        try:
            release.wait(2)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]
        finally:
            finished.set()
    monkeypatch.setattr(sources.socket, "getaddrinfo", stalled)
    monkeypatch.setattr(sources, "DNS_TIMEOUT_SECONDS", 0.02)
    try:
        with pytest.raises(SyncError) as error:
            sources.resolve_url(SHORT)
        assert error.value.code == "SYNC_SOURCE_TIMEOUT" and mock_network[0] == []
    finally:
        release.set()
        assert finished.wait(1)


def test_cancellation_preserves_exception_identity(mock_network):
    stopped = SyncError("lost lease", "SYNC_LEASE_LOST", 409)
    def cancelled():
        raise stopped
    with pytest.raises(SyncError) as error:
        sources.resolve_url(SHORT, check_cancelled=cancelled)
    assert error.value is stopped and mock_network[0] == []


def test_cloud_role_cannot_resolve(mock_network, monkeypatch):
    monkeypatch.setattr(config, "is_cloud", lambda: True)
    with pytest.raises(SyncError) as error:
        sources.resolve_url(SHORT)
    assert error.value.code == "SYNC_ROLE_REQUIRED" and mock_network[0] == []


def test_metadata_accepts_canonical_tracker_removal():
    sources.validate_metadata(resolution(), {"id": BV, "webpage_url": FINAL.split("?")[0], "extractor": "BiliBili"})


@pytest.mark.parametrize("changes", [{"id": "../escape"}, {"id": "BV1Other"}, {"webpage_url": "https://b23.tv/other"},
    {"webpage_url": f"https://www.bilibili.com/video/{BV}?p=2"}, {"webpage_url": "http://127.0.0.1/video/BV1"},
    {"extractor": "Douyin"}, {"extractor": 1}, {"webpage_url": None}])
def test_metadata_contradictions_rejected(changes):
    with pytest.raises(SyncError):
        sources.validate_metadata(resolution(), {"id": BV, "webpage_url": FINAL, "extractor": "BiliBili", **changes})


@pytest.fixture()
def local_media(tmp_path, monkeypatch):
    for name, path in (("DATA", tmp_path / "data"), ("MEDIA", tmp_path / "media"), ("ORIG", tmp_path / "media" / "originals")):
        monkeypatch.setattr(config, name, path)
    db.initialize()
    # This fixture observes the pre-download directory. Config reads and DB
    # schema initialization deliberately no longer create pipeline directories.
    config.ORIG.mkdir(parents=True)
    media = config.MEDIA / "canonical.mp4"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(CONTENT)
    asset = {"video_id": "file_canonical", "source_type": "local_file", "source_url": None,
             "content_sha256": hashlib.sha256(CONTENT).hexdigest(), "files_json": json.dumps({"video": "media/canonical.mp4"})}
    identity = {"library_id": str(uuid.uuid4()), "node_id": str(uuid.uuid4())}
    profile = storage_admin.profile()
    storage_lifecycle.initialize(profile)
    store = sources.SourceMap(config.DATA, profile=profile, source_library_id=identity["library_id"], source_node_id=identity["node_id"])
    return store, asset, identity, media


def test_source_mapping_survives_restart_and_allows_same_content_local_file(local_media):
    store, asset, identity, media = local_media
    original = deepcopy(asset)
    origin = store.record(resolution(), extracted_video_id=BV, asset=asset)
    reopened = sources.SourceMap(config.DATA, profile=store.profile, source_library_id=identity["library_id"], source_node_id=identity["node_id"])
    assert reopened.get(origin["mapping_id"]) == origin
    validate_claim_origin(SHORT, asset, source=identity, origin=origin)
    assert asset == original and media.read_bytes() == CONTENT
    assert len(list(store.root.glob("*.json"))) == 1 and not list(store.root.glob("*.part"))


def test_fixed_source_fact_has_distinct_local_generation_wrappers(local_media, monkeypatch):
    store, asset, _, _ = local_media
    monkeypatch.setattr(sources, "_now", lambda: STAMP)
    legacy = store.record(resolution(), extracted_video_id=BV, asset=asset)
    generations = [str(uuid.uuid4()), str(uuid.uuid4())]
    origins = [store.record(resolution(), extracted_video_id=BV, asset=asset,
                           mapping_generation=value, recorded_at=STAMP) for value in generations]
    assert origins == [legacy, legacy]  # Wire origin and its ID remain v1.
    assert store.get(legacy["mapping_id"]) == legacy
    for generation in generations:
        path = store.v2_root / (generation + ".json")
        record = json.loads(path.read_bytes())
        assert set(record) == {"format_version", "mapping_generation", "origin", "resolution"}
        assert record["format_version"] == 2 and record["mapping_generation"] == generation
        assert store.get(legacy["mapping_id"], mapping_generation=generation) == legacy
        assert store.get_generation(generation) == legacy
    assert len(list(store.v2_root.glob("*.json"))) == 2


def test_same_generation_reproves_media_without_retaking_clock(local_media, monkeypatch):
    store, asset, _, media = local_media
    generation = str(uuid.uuid4())
    origin = store.record(resolution(), extracted_video_id=BV, asset=asset,
                          mapping_generation=generation, recorded_at=STAMP)
    path = store.v2_root / (generation + ".json")
    raw, modified = path.read_bytes(), path.stat().st_mtime_ns
    monkeypatch.setattr(sources, "_now", lambda: pytest.fail("saved generation must not retake its clock"))
    assert store.record(resolution(), extracted_video_id=BV, asset=asset,
                        mapping_generation=generation, recorded_at=STAMP) == origin
    assert path.read_bytes() == raw and path.stat().st_mtime_ns == modified
    media.write_bytes(b"changed current media")
    with pytest.raises(SyncError) as caught:
        store.record(resolution(), extracted_video_id=BV, asset=asset,
                     mapping_generation=generation, recorded_at=STAMP)
    assert caught.value.code == "SYNC_SOURCE_MEDIA_MISMATCH"
    assert path.read_bytes() == raw


@pytest.mark.parametrize("mutation", [
    lambda value: value.update(format_version=True),
    lambda value: value.update(format_version=2.0),
    lambda value: value.update(mapping_generation=str(uuid.uuid4())),
    lambda value: value.update(extra="unrecognized"),
    lambda value: value["resolution"]["hops"][0].update(peer_ip="1.0.0.1"),
])
def test_generation_wrapper_is_closed_typed_and_identity_bound(local_media, mutation):
    store, asset, _, _ = local_media
    generation = str(uuid.uuid4())
    store.record(resolution(), extracted_video_id=BV, asset=asset, mapping_generation=generation, recorded_at=STAMP)
    path = store.v2_root / (generation + ".json")
    value = json.loads(path.read_bytes())
    mutation(value)
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(SyncError):
        store.get_generation(generation)


def test_generation_record_cannot_change_seed_or_be_found_as_legacy(local_media):
    store, asset, _, _ = local_media
    generation = str(uuid.uuid4())
    origin = store.record(resolution(), extracted_video_id=BV, asset=asset, mapping_generation=generation, recorded_at=STAMP)
    with pytest.raises(SyncError):
        store.get(origin["mapping_id"])  # UUID instance never becomes a legacy map.
    with pytest.raises(SyncError):
        store.record(resolution(), extracted_video_id=BV, asset=asset,
                     mapping_generation=generation, recorded_at="2026-01-02T00:00:00.000000Z")
    assert store.get_generation(generation) == origin


def test_retired_generation_cannot_be_reused_but_new_generation_can_reprove_same_fact(local_media):
    store, asset, identity, media = local_media
    from reno.sync_state import StateStore
    state = StateStore(config.DATA, profile=store.profile)
    state.initialize({"home_library_id": identity["library_id"], "home_node_id": identity["node_id"],
                      "target_library_id": str(uuid.uuid4()), "target_node_id": str(uuid.uuid4()),
                      "target_url": "https://artificial-sync.example.test"}, 1)
    generation = str(uuid.uuid4())
    origin = store.record(resolution(), extracted_video_id=BV, asset=asset, mapping_generation=generation, recorded_at=STAMP)
    path = store.v2_root / (generation + ".json")
    raw = path.read_bytes()
    selected = {"format_version": 1, "selected": [{"kind": "source_map", "mapping_generation": generation,
                 "origin_mapping_id": origin["mapping_id"], "raw_sha256": hashlib.sha256(raw).hexdigest()}]}
    preview = state.preview_retirement(selected)
    state.commit_retirement(selected, expected_digest=preview["digest"], operation_id=str(uuid.uuid4()))
    with pytest.raises(SyncError) as caught:
        store.get_generation(generation)
    assert caught.value.code == "SYNC_NO_RESEND_REFERENCE_RETIRED"
    assert store.get_generation(generation, audit=True) == origin
    # A new physical media inode cannot revive the retired UUID instance.
    media.unlink()
    media.write_bytes(CONTENT)
    with pytest.raises(SyncError) as caught:
        store.record(resolution(), extracted_video_id=BV, asset=asset, mapping_generation=generation, recorded_at=STAMP)
    assert caught.value.code == "SYNC_NO_RESEND_REFERENCE_RETIRED"
    newer = str(uuid.uuid4())
    assert store.record(resolution(), extracted_video_id=BV, asset=asset, mapping_generation=newer, recorded_at=STAMP) == origin
    assert path.read_bytes() == raw
    # A missing retired map is not an invitation to reconstruct its UUID.
    # Only artificial TEMP content is removed; the permanent ledger remains.
    path.unlink()
    with pytest.raises(SyncError) as caught:
        store.get_generation(generation, missing_ok=True)
    assert caught.value.code == "SYNC_NO_RESEND_REFERENCE_RETIRED"
    with pytest.raises(SyncError) as caught:
        store.record(resolution(), extracted_video_id=BV, asset=asset, mapping_generation=generation, recorded_at=STAMP)
    assert caught.value.code == "SYNC_NO_RESEND_REFERENCE_RETIRED"
    assert not path.exists()


def test_distinct_aliases_have_distinct_immutable_source_proofs(local_media):
    store, asset, identity, _ = local_media
    first = store.record(resolution(), extracted_video_id=BV, asset=asset)
    second_url = "https://b23.tv/other?tracking=2"
    second = store.record(resolution(submitted=second_url), extracted_video_id=BV, asset=asset)
    assert first["mapping_id"] != second["mapping_id"]
    assert store.get(first["mapping_id"]) == first
    validate_claim_origin(second_url, asset, source=identity, origin=second)
    with pytest.raises(SyncError):
        validate_claim_origin(SHORT, asset, source=identity, origin=second)


def test_same_record_is_idempotent_without_overwriting(local_media, monkeypatch):
    store, asset, _, _ = local_media
    monkeypatch.setattr(sources, "_now", lambda: STAMP)
    first = store.record(resolution(), extracted_video_id=BV, asset=asset)
    path = store.root / (first["mapping_id"] + ".json")
    before = path.stat().st_mtime_ns
    assert store.record(resolution(), extracted_video_id=BV, asset=asset) == first
    assert path.stat().st_mtime_ns == before


@pytest.mark.parametrize("kind", ["media", "sha", "resolved_id", "trace", "identity", "origin"])
def test_media_mapping_and_resolution_tampering_are_detected(kind, local_media):
    store, asset, identity, media = local_media
    if kind == "media":
        media.write_bytes(b"different actual content")
    if kind == "sha":
        asset["content_sha256"] = "f" * 64
    if kind in ("media", "sha", "resolved_id"):
        with pytest.raises(SyncError):
            store.record(resolution(), extracted_video_id="BV1Other" if kind == "resolved_id" else BV, asset=asset)
        return
    origin = store.record(resolution(), extracted_video_id=BV, asset=asset)
    path = store.root / (origin["mapping_id"] + ".json")
    if kind == "identity":
        store = sources.SourceMap(config.DATA, profile=store.profile, source_library_id=str(uuid.uuid4()), source_node_id=identity["node_id"])
    else:
        record = json.loads(path.read_bytes())
        if kind == "trace":
            record["resolution"]["hops"][0]["peer_ip"] = "1.0.0.1"
        else:
            record["origin"]["submitted_url"] += "x"
        path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(SyncError):
        store.get(origin["mapping_id"])


def test_ingest_validates_id_and_metadata_before_filesystem_or_download(local_media, monkeypatch):
    calls = []
    before = list(config.ORIG.iterdir())
    def metadata(*args):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout=json.dumps({"id": "../../escape"}), stderr="")
    monkeypatch.setattr(ingest, "_ytdlp", metadata)
    with pytest.raises(SyncError):
        ingest.ingest_url(FINAL, enqueue_process=False)
    assert len(calls) == 1 and list(config.ORIG.iterdir()) == before
    calls.clear()
    monkeypatch.setattr(ingest, "_ytdlp", lambda *a: (calls.append(a) or SimpleNamespace(
        returncode=0, stdout=json.dumps({"id": BV, "webpage_url": FINAL + "&p=2"}), stderr="")))
    with pytest.raises(SyncError):
        ingest.ingest_url(FINAL, enqueue_process=False, metadata_validator=lambda info: sources.validate_metadata(resolution(), info))
    assert len(calls) == 1 and list(config.ORIG.iterdir()) == before


@pytest.mark.parametrize("enqueue", [True, False])
def test_default_ingest_queues_and_optout_does_not(local_media, monkeypatch, enqueue):
    calls = []
    frozen = []
    def ytdlp(*args):
        calls.append(args)
        if "--dump-json" in args:
            return SimpleNamespace(returncode=0, stdout=json.dumps({"id": BV, "webpage_url": FINAL, "extractor": "BiliBili"}), stderr="")
        if "--load-info-json" in args:
            frozen.append(json.loads(Path(args[args.index("--load-info-json") + 1]).read_text(encoding="utf-8")))
        Path(args[args.index("-o") + 1]).write_bytes(CONTENT + b" downloaded")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(ingest, "_ytdlp", ytdlp)
    options = {} if enqueue else {"enqueue_process": False}
    result = ingest.ingest_url(FINAL, **options, metadata_validator=lambda info: sources.validate_metadata(resolution(), info))
    assert result["video_id"] == BV and ("extracted_video_id" in result) is (not enqueue)
    with db.connect() as con:
        assert con.execute("SELECT count(*) FROM job").fetchone()[0] == int(enqueue)
    assert calls[0][-1] == FINAL
    if enqueue:
        assert all(call[-1] == FINAL for call in calls)
    else:
        assert len(frozen) == 2 and all("webpage_url" not in item and item["id"] == BV for item in frozen)
        assert all("--no-playlist" in call for call in calls)
        assert not list(config.ORIG.rglob(".sync-source-*.json"))


def test_dedup_returns_extracted_id_without_rewriting_canonical_source(local_media, monkeypatch):
    _, canonical, _, media = local_media
    con = db.connect()
    con.execute("INSERT INTO video_asset(video_id,source_type,source_url,content_sha256,files_json) VALUES(?,?,?,?,?)",
                (canonical["video_id"], "local_file", None, canonical["content_sha256"], canonical["files_json"]))
    con.commit()
    def ytdlp(*args):
        if "--dump-json" in args:
            return SimpleNamespace(returncode=0, stdout=json.dumps({"id": BV, "webpage_url": FINAL, "extractor": "BiliBili"}), stderr="")
        Path(args[args.index("-o") + 1]).write_bytes(CONTENT)
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(ingest, "_ytdlp", ytdlp)
    try:
        result = ingest.ingest_url(FINAL, enqueue_process=False)
        assert result["video_id"] == "file_canonical" and result["extracted_video_id"] == BV
        assert result["extracted_media_sha256"] == canonical["content_sha256"]
        row = dict(con.execute("SELECT * FROM video_asset").fetchone())
        assert row["source_url"] is None and row["source_type"] == "local_file"
        assert con.execute("SELECT count(*) FROM job").fetchone()[0] == 0
    finally:
        con.close()


@pytest.mark.parametrize("kind", ["absolute", "dotdot", "ads"])
def test_source_map_cannot_attest_matching_bytes_outside_media_root(kind, local_media, tmp_path):
    store, asset, _, _ = local_media
    outside = tmp_path / "private.mp4"
    outside.write_bytes(CONTENT)
    path = str(outside) if kind == "absolute" else ("media/../private.mp4" if kind == "dotdot" else "media/canonical.mp4:secret")
    asset["files_json"] = json.dumps({"video": path})
    with pytest.raises(SyncError) as error:
        store.record(resolution(), extracted_video_id=BV, asset=asset)
    assert error.value.code == "SYNC_SOURCE_MEDIA_MISMATCH"
    assert not store.root.exists()


def test_sync_ingest_rejects_same_id_with_changed_content(local_media, monkeypatch):
    con = db.connect()
    con.execute("INSERT INTO video_asset(video_id,source_type,content_sha256) VALUES(?,?,?)",
                (BV, "share_url", "f" * 64))
    con.commit()
    def ytdlp(*args):
        if "--dump-json" in args:
            return SimpleNamespace(returncode=0, stdout=json.dumps({"id": BV, "webpage_url": FINAL}), stderr="")
        Path(args[args.index("-o") + 1]).write_bytes(CONTENT)
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(ingest, "_ytdlp", ytdlp)
    try:
        with pytest.raises(SyncError) as error:
            ingest.ingest_url(FINAL, enqueue_process=False)
        assert error.value.code == "SYNC_SOURCE_MEDIA_MISMATCH"
        assert con.execute("SELECT content_sha256 FROM video_asset").fetchone()[0] == "f" * 64
        assert con.execute("SELECT count(*) FROM job").fetchone()[0] == 0
    finally:
        con.close()


@pytest.mark.parametrize("status,code", [(429, "SYNC_SOURCE_RATE_LIMITED"), (408, "SYNC_SOURCE_NETWORK"),
    (500, "SYNC_SOURCE_NETWORK"), (503, "SYNC_SOURCE_NETWORK"), (404, "SYNC_SOURCE_UNAVAILABLE"),
    (410, "SYNC_SOURCE_UNAVAILABLE")])
def test_retryable_http_failures_remain_retryable(status, code, mock_network):
    mock_network[2][0] = lambda request: httpx.Response(status)
    with pytest.raises(SyncError) as error:
        sources.resolve_url(FINAL)
    assert error.value.code == code


@pytest.mark.parametrize("failure", ["deadline", "cancel", "header_limit"])
def test_real_socket_slow_headers_obey_total_deadline_and_cancellation(monkeypatch, failure):
    # Real OS sockets and real httpcore HTTP parsing; only connect is routed
    # into a local socketpair. No public or production request is made.
    client, server = socket.socketpair()
    stop = threading.Event()
    start = monotonic()
    cancelled = SyncError("test lost lease", "SYNC_LEASE_LOST", 409)
    def sender():
        try:
            server.settimeout(2)
            server.recv(8192)
            server.sendall(b"HTTP/1.1 200 OK\r\nX-Slow: ")
            if failure == "header_limit":
                server.sendall(b"a" * (sources.MAX_HEADER_BYTES + 2048))
            else:
                while not stop.wait(0.015):
                    server.sendall(b"a")
        except OSError:
            pass
        finally:
            server.close()
    thread = threading.Thread(target=sender, daemon=True)
    thread.start()
    monkeypatch.setattr(sources.socket, "getaddrinfo", lambda *a, **k:
        [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 80))])
    monkeypatch.setattr(sources._DeadlineBackend, "connect_tcp",
        lambda self, *a, **k: sources._DeadlineStream(client, self.deadline, self.check))
    monkeypatch.setattr(sources, "RESOLVE_TIMEOUT_SECONDS", 2.0 if failure == "header_limit" else 0.2)
    def check():
        if failure == "cancel" and monotonic() - start > 0.08:
            raise cancelled
    try:
        with pytest.raises(SyncError) as error:
            sources.resolve_url(FINAL.replace("https:", "http:"), check_cancelled=check)
        if failure == "deadline":
            assert error.value.code == "SYNC_SOURCE_TIMEOUT"
        elif failure == "cancel":
            assert error.value is cancelled
        else:
            assert "headers" in str(error.value)
        assert monotonic() - start < (3.0 if failure == "header_limit" else 1.5)
    finally:
        stop.set()
        client.close()
        thread.join(timeout=2)
        assert not thread.is_alive()


def test_transport_certificate_policy_and_direct_literal_connect(monkeypatch):
    calls = []
    class FakeSocket:
        def settimeout(self, value): calls.append(("timeout", value))
        def setsockopt(self, *args): pass
        def connect(self, address): calls.append(("connect", address))
        def close(self): pass
    monkeypatch.setattr(sources.socket, "socket", lambda *a: FakeSocket())
    monkeypatch.setattr(sources.socket, "getaddrinfo", lambda *a, **k: pytest.fail("Second DNS resolution is forbidden"))
    backend = sources._DeadlineBackend(monotonic() + 1, lambda: None)
    stream = backend.connect_tcp("8.8.8.8", 443, timeout=10)
    assert ("connect", ("8.8.8.8", 443)) in calls and 0 < calls[0][1] <= 1
    with sources._HeaderTransport(monotonic() + 1, lambda: None) as transport:
        context = transport.pool._ssl_context
        assert context.check_hostname and context.verify_mode == sources.ssl.CERT_REQUIRED
    stream.close()


@pytest.mark.parametrize("kind", ["multiple", "playlist", "entries"])
def test_sync_ingest_rejects_multi_video_metadata_before_creating_paths(kind, local_media, monkeypatch):
    item = {"id": BV, "webpage_url": FINAL}
    if kind == "playlist":
        item["_type"] = "playlist"
    if kind == "entries":
        item["entries"] = []
    text = json.dumps(item)
    if kind == "multiple":
        text += "\n" + json.dumps({**item, "id": "BV1Other"})
    calls = []
    monkeypatch.setattr(ingest, "_ytdlp", lambda *a: (calls.append(a) or SimpleNamespace(returncode=0, stdout=text, stderr="")))
    with pytest.raises(SyncError):
        ingest.ingest_url(FINAL, enqueue_process=False)
    assert len(calls) == 1 and not list(config.ORIG.iterdir())


def test_frozen_metadata_is_removed_when_download_fails(local_media, monkeypatch):
    frozen_paths = []
    def ytdlp(*args):
        if "--dump-json" in args:
            return SimpleNamespace(returncode=0, stdout=json.dumps({"id": BV, "webpage_url": FINAL}), stderr="")
        path = Path(args[args.index("--load-info-json") + 1])
        frozen_paths.append(path)
        assert json.loads(path.read_text(encoding="utf-8"))["id"] == BV
        return SimpleNamespace(returncode=1, stdout="", stderr="synthetic failure")
    monkeypatch.setattr(ingest, "_ytdlp", ytdlp)
    with pytest.raises(RuntimeError):
        ingest.ingest_url(FINAL, enqueue_process=False)
    assert frozen_paths and all(not path.exists() for path in frozen_paths)


@pytest.mark.parametrize("raw", [b'{"origin":{},"origin":{}}', b'{"origin":NaN}', b"[" * 2000 + b"]" * 2000])
def test_corrupt_mapping_json_has_bounded_sanitized_failure(raw, local_media):
    store, asset, _, _ = local_media
    origin = store.record(resolution(), extracted_video_id=BV, asset=asset)
    (store.root / (origin["mapping_id"] + ".json")).write_bytes(raw)
    with pytest.raises(SyncError) as error:
        store.get(origin["mapping_id"])
    assert error.value.code == "SYNC_SOURCE_MAP_CORRUPT"


def test_actual_ytdlp_info_failure_cannot_reextract_url(local_media, monkeypatch):
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadError
    metadata = {"id": BV, "webpage_url": FINAL, "original_url": SHORT,
                "formats": [{"format_id": "fake", "url": "https://invalid.example/media"}]}
    with ingest._download_input(FINAL, metadata, config.ORIG, frozen=True) as arguments:
        path = Path(arguments[-1])
        frozen = json.loads(path.read_text(encoding="utf-8"))
        assert "webpage_url" not in frozen and "original_url" not in frozen
        assert frozen["formats"] == metadata["formats"]
        with YoutubeDL({"quiet": True, "no_warnings": True}, auto_init=False) as ydl:
            def fail_download(info, **kwargs):
                assert info["id"] == BV and "webpage_url" not in info
                raise DownloadError("synthetic expired CDN URL")
            monkeypatch.setattr(ydl, "process_ie_result", fail_download)
            monkeypatch.setattr(ydl, "download", lambda *a, **k: pytest.fail("URL re-extraction is forbidden"))
            monkeypatch.setattr(ydl, "extract_info", lambda *a, **k: pytest.fail("No platform request is allowed"))
            with pytest.raises(DownloadError, match="synthetic expired"):
                ydl.download_with_info_file(str(path))
    assert not path.exists() and metadata["webpage_url"] == FINAL
