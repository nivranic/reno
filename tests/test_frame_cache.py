"""Private disposable JPEG cache: persistence, integrity, budget and OS locks."""
import base64
from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from PIL import Image
import pytest

from reno import frame_cache as cache


def jpeg(color="red"):
    output = io.BytesIO()
    Image.new("RGB", (16, 16), color).save(output, format="JPEG")
    return output.getvalue()


@pytest.fixture()
def env(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"synthetic source; extraction is local")
    calls = []
    def extractor(path, ms, out):
        assert path == source
        calls.append(ms)
        out.write_bytes(jpeg())
        return True
    return {"root": tmp_path / "frames_cache", "source": source, "extractor": extractor, "calls": calls}


def get(env, ms=1, *, budget=100_000, sha="synthetic-content", extractor=None):
    return cache.get_frame("video", sha, ms, env["source"], root=env["root"], budget_bytes=budget,
                           extractor=extractor or env["extractor"])


def index(env):
    path = env["root"] / cache._INDEX
    return json.loads(path.read_text(encoding="utf-8"))["index"] if path.exists() else {"entries": {}}


def stored(env):
    return [env["root"] / cache._FILES / entry["file"] for entry in index(env)["entries"].values()]


def plan(env, budget=0):
    return cache.preview(root=env["root"], budget_bytes=budget)


def prune(env, preview=None, budget=0):
    preview = preview or plan(env, budget)
    return cache.prune(root=env["root"], budget_bytes=budget, expected_digest=preview["digest"])


def test_preview_does_not_create_root_lock_or_index_and_preserves_unknown_old_cache(env):
    first = plan(env)
    assert not env["root"].exists() and first["managed_entries"] == 0
    legacy = env["root"] / "video/old-content/f0000001.jpg"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(jpeg())
    before = {str(path.relative_to(env["root"])): path.stat().st_mtime_ns for path in env["root"].rglob("*")}
    value = plan(env)
    assert value["preserved_unknown"]["root_entries"] == 1 and value["candidates"] == []
    assert {str(path.relative_to(env["root"])): path.stat().st_mtime_ns for path in env["root"].rglob("*")} == before
    prune(env, value)
    assert legacy.read_bytes() == jpeg()


def test_hit_returns_verified_bytes_and_updates_durable_access_only_for_that_frame(env, monkeypatch):
    for ms in range(3): get(env, ms)
    seen = []
    original = cache._read_frame
    def record(*args, **kwargs):
        seen.append(args[1])
        return original(*args, **kwargs)
    monkeypatch.setattr(cache, "_read_frame", record)
    before = index(env)["clock"]
    assert get(env, 0) == jpeg() and len(seen) == 1
    assert env["calls"] == [0, 1, 2] and index(env)["clock"] == before + 1
    assert plan(env, 100_000)["managed_bytes"] == 3 * len(jpeg())
    if os.name != "nt":
        assert (env["root"] / cache._FILES).stat().st_mode & 0o777 == 0o700


def test_byte_lru_and_entry_limit_evict_oldest_and_do_not_touch_media_or_unknown(env, monkeypatch):
    unknown = env["root"] / "unknown.bin"
    env["root"].mkdir()
    unknown.write_bytes(b"preserve")
    source_bytes = env["source"].read_bytes()
    for ms in (1, 2): get(env, ms, budget=2 * len(jpeg()))
    get(env, 1, budget=2 * len(jpeg()))
    get(env, 3, budget=2 * len(jpeg()))
    get(env, 1, budget=2 * len(jpeg()))
    assert env["calls"] == [1, 2, 3]
    get(env, 2, budget=2 * len(jpeg()))
    assert env["calls"] == [1, 2, 3, 2]
    monkeypatch.setattr(cache, "MAX_ENTRIES", 2)
    get(env, 4)
    assert len(index(env)["entries"]) == 2
    assert unknown.read_bytes() == b"preserve" and env["source"].read_bytes() == source_bytes


@pytest.mark.parametrize("sha", [None, ""])
def test_missing_source_hash_never_persists_unsafe_identity(env, sha):
    assert get(env, sha=sha) == get(env, sha=sha) == jpeg()
    assert env["calls"] == [1, 1] and not index(env)["entries"]
    assert not list((env["root"] / cache._FILES).iterdir())


def test_zero_budget_and_oversize_frame_return_bytes_without_retaining_files(env):
    get(env)
    assert get(env, 2, budget=0) == jpeg()
    assert plan(env)["managed_entries"] == 0 and not stored(env)
    assert get(env, 3, budget=1) == jpeg()
    assert not list((env["root"] / cache._FILES).iterdir())


def test_preview_digest_fences_hit_budget_and_same_size_substitution(env):
    get(env)
    old = plan(env)
    get(env)
    with pytest.raises(cache.FrameCacheError) as error: prune(env, old)
    assert error.value.code == "FRAME_CACHE_CHANGED"
    fresh = plan(env)
    with pytest.raises(cache.FrameCacheError) as error: prune(env, fresh, budget=1)
    assert error.value.code == "FRAME_CACHE_CHANGED"
    path = stored(env)[0]
    raw = bytearray(path.read_bytes())
    raw[len(raw) // 2] ^= 1
    prior = path.stat()
    path.write_bytes(raw)
    os.utime(path, ns=(prior.st_atime_ns, prior.st_mtime_ns))
    with pytest.raises(cache.FrameCacheError) as error: prune(env, fresh)
    assert error.value.code == "FRAME_CACHE_CHANGED"
    invalid = plan(env)
    assert invalid["preserved_invalid"] == 1 and invalid["candidates"] == []
    prune(env, invalid)
    assert path.read_bytes() == raw
    assert get(env) == jpeg() and env["calls"] == [1, 1]
    assert path.read_bytes() == raw  # Old corrupt bytes are never overwritten.


def test_missing_registered_file_is_regenerated_and_preview_never_writes(env):
    get(env)
    path = stored(env)[0]
    path.unlink()
    before = (env["root"] / cache._INDEX).read_bytes()
    assert plan(env)["missing_entries"] == 1
    assert (env["root"] / cache._INDEX).read_bytes() == before
    assert get(env) == jpeg() and len(stored(env)) == 1 and env["calls"] == [1, 1]


@pytest.mark.parametrize("failure", ["false", "raise", "not_jpeg", "bomb"])
def test_failed_extraction_removes_only_its_own_output_and_can_retry(env, monkeypatch, failure):
    if failure == "bomb":
        monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)
    def failed(path, ms, out):
        out.write_bytes(jpeg() if failure == "bomb" else b"broken")
        if failure == "raise": raise RuntimeError("private provider detail")
        return failure not in ("false",)
    with pytest.raises(cache.FrameCacheError) as error:
        get(env, extractor=failed)
    assert "private provider" not in str(error.value)
    assert not list((env["root"] / cache._FILES).iterdir())
    assert not index(env)["entries"]
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 89_478_485)
    assert get(env) == jpeg()


def test_failed_index_write_cleans_unregistered_new_frame_and_retry_succeeds(env, monkeypatch):
    original = cache._atomic_write
    def fail(*args, **kwargs): raise OSError("synthetic index write failure")
    monkeypatch.setattr(cache, "_atomic_write", fail)
    with pytest.raises(cache.FrameCacheError): get(env)
    assert not list((env["root"] / cache._FILES).iterdir())
    monkeypatch.setattr(cache, "_atomic_write", original)
    assert get(env) == jpeg()


def test_partial_delete_failure_retains_new_registration_until_explicit_retry(env, monkeypatch):
    get(env)
    before = plan(env)
    original = cache.safe._Directory.unlink
    def fail(self, name):
        if self.path.name == cache._FILES and name.endswith(".jpg"):
            raise PermissionError("synthetic sharing conflict")
        return original(self, name)
    monkeypatch.setattr(cache.safe._Directory, "unlink", fail)
    with pytest.raises(cache.FrameCacheError): prune(env, before)
    assert len(stored(env)) == 1 and stored(env)[0].read_bytes() == jpeg()
    monkeypatch.setattr(cache.safe._Directory, "unlink", original)
    after = plan(env)
    assert after["digest"] != before["digest"]
    assert prune(env, after)["deleted_entries"] == 1


def test_corrupt_index_is_controlled_and_does_not_delete_cached_or_unknown_files(env):
    get(env)
    path = stored(env)[0]
    (env["root"] / cache._INDEX).write_bytes(b'{"not":"our index"}')
    for operation in (lambda: plan(env), lambda: get(env)):
        with pytest.raises(cache.FrameCacheError) as error: operation()
        assert error.value.code == "FRAME_CACHE_INDEX_INVALID"
    assert path.read_bytes() == jpeg()


def test_hardlink_index_and_managed_file_are_never_followed_or_deleted(env, tmp_path):
    get(env)
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(jpeg("blue"))
    frame = stored(env)[0]
    frame.unlink()
    os.link(outside, frame)
    with pytest.raises(cache.FrameCacheError) as error: get(env)
    assert error.value.code == "FRAME_CACHE_UNSAFE_PATH"
    before = plan(env)
    assert before["preserved_invalid"] == 1
    prune(env, before)
    assert outside.read_bytes() == frame.read_bytes() == jpeg("blue")


@pytest.mark.skipif(os.name == "nt", reason="Unprivileged Windows symlink creation varies by host")
def test_directory_links_and_index_links_fail_without_touching_external_paths(env, tmp_path):
    external = tmp_path / "external"
    external.mkdir()
    env["root"].symlink_to(external, target_is_directory=True)
    for operation in (lambda: get(env), lambda: plan(env)):
        with pytest.raises(cache.FrameCacheError): operation()
    assert list(external.iterdir()) == []


def test_preview_contends_on_existing_os_lock_and_never_creates_files(env, monkeypatch):
    get(env)
    monkeypatch.setattr(cache, "LOCK_WAIT_SECONDS", 0.15)
    with cache._locked(env["root"], create=True), ThreadPoolExecutor(max_workers=1) as pool:
        started = time.monotonic()
        with pytest.raises(cache.FrameCacheError) as error: pool.submit(plan, env).result(timeout=2)
        assert error.value.code == "FRAME_CACHE_BUSY" and time.monotonic() - started >= 0.1
    assert plan(env)["managed_entries"] == 1


def test_two_independent_processes_extract_once_and_return_bytes_after_eviction(env, tmp_path):
    encoded = base64.b64encode(jpeg()).decode("ascii")
    program = '''import base64, json, pathlib, sys, time
from reno import frame_cache
root, source = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
count = root.parent / 'extractions.txt'
def extract(path, ms, target):
    count.write_text(str(int(count.read_text()) + 1) if count.exists() else '1')
    time.sleep(.2)
    target.write_bytes(base64.b64decode(sys.argv[3]))
    return True
data = frame_cache.get_frame('video', 'synthetic-content', 1, source, root=root, budget_bytes=100000, extractor=extract)
print(json.dumps({'sha': __import__('hashlib').sha256(data).hexdigest(), 'size': len(data)}))
'''
    args = [sys.executable, "-c", program, str(env["root"]), str(env["source"]), encoded]
    children = [subprocess.Popen(args, cwd=Path(__file__).resolve().parents[1], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0) for _ in range(2)]
    try:
        outputs = [child.communicate(timeout=20) for child in children]
        assert all(child.returncode == 0 for child in children), outputs
        assert json.loads(outputs[0][0]) == json.loads(outputs[1][0])
        assert (tmp_path / "extractions.txt").read_text() == "1"
        data = get(env)
        assert prune(env)["deleted_entries"] == 1 and data == jpeg()
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
