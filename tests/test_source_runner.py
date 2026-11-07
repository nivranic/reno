"""Controlled arguments, actual synthetic children, and file identity proofs."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from reno import source_contract as contract, source_runner as runner, storage_lifecycle

_REAL_POPEN = subprocess.Popen


@pytest.fixture
def profile(tmp_path):
    value = storage_lifecycle.StorageProfile(tmp_path / "data/reno.db", tmp_path / "media",
                                            tmp_path / "reports", tmp_path / "checkpoints")
    value.media.mkdir(parents=True)
    return value


def metadata_plan():
    return contract.SourcePlan("source_metadata", "https://synthetic.invalid/source")


def download_plan():
    info = {"id": "synthetic", "url": "https://synthetic.invalid/media.mp4", "protocol": "https", "ext": "mp4"}
    return contract.SourcePlan("download", "https://synthetic.invalid/source", "video",
        contract.canonical_json(info), "originals/synthetic/source-test/video.mp4")


def artifact(profile, path):
    info = path.stat()
    return contract.SourceArtifact("video", path.relative_to(profile.media).as_posix(), info.st_size,
        hashlib.sha256(path.read_bytes()).hexdigest(), info.st_dev, info.st_ino, info.st_mtime_ns)


@pytest.mark.parametrize("extra", [("--exec", "private-command"), ("--retries=9",), ("--",),
    ("https://synthetic.invalid/another",), ("--config-locations", "private"), ("--proxy", "private"),
    ("--force-ipv4", "--force-ipv4"), ("--force-ipv4", "--force-ipv6"), ("--limit-rate", "5"),
    ("--external-downloader", "private"), ("--plugin-dirs", "private"), ("--cookies", "private")])
def test_extra_parser_rejects_control_overrides_before_any_process(profile, monkeypatch, extra):
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("prepare launched a child"))
    with pytest.raises(contract.SourceContractError):
        runner.prepare(metadata_plan(), profile=profile, policy={}, extra_args=extra)


@pytest.mark.parametrize("suffix", ["", ".part"])
def test_unknown_existing_target_or_partial_is_preserved_and_blocks_prepare(profile, suffix):
    plan = download_plan()
    path = profile.media / (plan.artifact_relpath + suffix)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"preexisting unknown content")
    with pytest.raises(contract.SourceContractError):
        runner.prepare(plan, profile=profile, policy={})
    assert path.read_bytes() == b"preexisting unknown content"


def test_paid_artifact_content_change_is_not_accepted_as_exists(profile):
    path = profile.media / "paid.mp4"
    path.write_bytes(b"original")
    proof = artifact(profile, path)
    path.write_bytes(b"modified")
    with pytest.raises(contract.SourceContractError):
        runner.verify_artifact(proof, profile=profile)


def test_paid_artifact_replaced_identity_is_not_accepted(profile):
    path = profile.media / "paid.mp4"
    path.write_bytes(b"original")
    proof = artifact(profile, path)
    other = profile.media / "other.mp4"
    other.write_bytes(b"original")
    os.replace(other, path)
    with pytest.raises(contract.SourceContractError):
        runner.verify_artifact(proof, profile=profile)


def synthetic(monkeypatch, script):
    """Run real Python children while preserving production process controls."""
    original = _REAL_POPEN
    observed = []
    def launch(argv, **kwargs):
        observed.append((tuple(argv), dict(kwargs)))
        return original([sys.executable, "-I", "-c", script, json.dumps(list(argv))], **kwargs)
    monkeypatch.setattr(runner.subprocess, "Popen", launch)
    return observed


def test_installed_parser_fixed_module_retry_controls_and_frozen_rate(profile, monkeypatch):
    observed = synthetic(monkeypatch, 'import sys; sys.stdout.write(\'{"id":"synthetic"}\')')
    settings = {"download_rate_bytes_per_second": 1234}
    prepared = runner.prepare(metadata_plan(), profile=profile, policy=settings,
                              extra_args=("--geo-bypass", "--force-ipv4"))
    settings["download_rate_bytes_per_second"] = 9999
    result = runner.run(prepared)
    assert result.successful and json.loads(result.metadata_json)["id"] == "synthetic"
    argv, kwargs = observed[0]
    assert argv[:4] == (sys.executable, "-I", "-m", "yt_dlp")
    for key in ("--retries", "--fragment-retries", "--extractor-retries", "--file-access-retries"):
        assert argv[argv.index(key) + 1] == "0"
    assert argv[argv.index("--concurrent-fragments") + 1] == "1"
    assert argv[argv.index("--limit-rate") + 1] == "1234"
    assert {"--no-plugin-dirs", "--no-js-runtimes", "--ignore-config", "--no-cache-dir"}.issubset(argv)
    assert kwargs["shell"] is False and kwargs["stdin"] == subprocess.DEVNULL
    assert not any(thread.name == "reno-source-drain" and thread.is_alive() for thread in threading.enumerate())


def test_simultaneous_large_stdout_stderr_complete_hashes_no_deadlock(profile, monkeypatch):
    raw = b'{"id":"synthetic"}' + b' ' * (600 * 1024)
    err = b"private diagnostic" * 10000
    synthetic(monkeypatch, 'import os, threading; '
        'a=threading.Thread(target=lambda:os.write(1,b\'{"id":"synthetic"}\'+b" "*(600*1024))); '
        'b=threading.Thread(target=lambda:os.write(2,b"private diagnostic"*10000)); '
        'a.start();b.start();a.join();b.join()')
    result = runner.run(runner.prepare(metadata_plan(), profile=profile, policy={}), timeout=10)
    assert result.successful
    assert (result.stdout_bytes, result.stdout_sha256) == (len(raw), hashlib.sha256(raw).hexdigest())
    assert (result.stderr_bytes, result.stderr_sha256) == (len(err), hashlib.sha256(err).hexdigest())
    assert "private diagnostic" not in contract.encode_envelope(metadata_plan(), result)


def test_normal_positive_exit_authorizes_only_complete_process_failure(profile, monkeypatch):
    synthetic(monkeypatch, 'import os,sys; os.write(2,b"private login URL"); sys.exit(7)')
    plan = metadata_plan()
    result = runner.run(runner.prepare(plan, profile=profile, policy={}))
    assert result.response_complete and result.returncode == 7 and result.failure_class == "process_failure"
    text = contract.encode_envelope(plan, result)
    assert "private login URL" not in text
    assert contract.validate_retry_envelope(text, request_sha256=plan.request_sha256,
        input_sha256=plan.input_sha256, kind=plan.kind, segment=None) == result


@pytest.mark.parametrize("reason", ["timeout", "cancelled", "output_limit"])
def test_actual_stop_waits_and_joins_then_is_indeterminate(profile, monkeypatch, reason):
    if reason == "output_limit":
        script = 'import os,time; os.write(2,b"x"*(600*1024)); time.sleep(30)'
    else:
        script = 'import time; time.sleep(30)'
    original = _REAL_POPEN
    processes = []
    def launch(argv, **kwargs):
        value = original([sys.executable, "-I", "-c", script], **kwargs)
        processes.append(value)
        return value
    monkeypatch.setattr(runner.subprocess, "Popen", launch)
    stop = threading.Event()
    timer = threading.Timer(0.15, stop.set) if reason == "cancelled" else None
    if timer is not None:
        timer.start()
    try:
        result = runner.run(runner.prepare(metadata_plan(), profile=profile, policy={}), stop_event=stop,
                            timeout=0.2 if reason == "timeout" else 10)
    finally:
        if timer is not None:
            timer.cancel()
            timer.join()
    assert result.termination == reason and not result.response_complete
    assert result.process_exited and result.drain_complete
    assert processes[0].poll() is not None
    assert not any(thread.name == "reno-source-drain" and thread.is_alive() for thread in threading.enumerate())


def test_launch_failure_is_unknown_without_leaking_error(profile, monkeypatch):
    def launch(*args, **kwargs):
        raise OSError("private cookie and URL")
    monkeypatch.setattr(runner.subprocess, "Popen", launch)
    result = runner.run(runner.prepare(metadata_plan(), profile=profile, policy={}))
    assert result.termination == "launch_unknown" and not result.response_complete and result.returncode is None
    assert "private" not in repr(result)


@pytest.mark.parametrize("payload,failure", [
    (b'not json', "invalid_output"),
    (b'{"id":"synthetic","formats":[{"url":"https://synthetic.invalid/a", "http_headers":{"Authorization":"private"}}]}', "unsafe_metadata"),
    (b'{"id":"synthetic","title":"\\ud800"}', "unsafe_metadata"),
])
def test_invalid_or_secret_output_is_terminal_without_raw_copy(profile, monkeypatch, payload, failure):
    synthetic(monkeypatch, 'import os; os.write(1,' + repr(payload) + ')')
    plan = metadata_plan()
    result = runner.run(runner.prepare(plan, profile=profile, policy={}))
    assert result.response_complete and result.failure_class == failure and result.metadata_json is None
    assert "private" not in contract.encode_envelope(plan, result)


@pytest.mark.parametrize("entries", [[], [{"id": "synthetic", "title": "an item"}]])
def test_enumeration_full_json_including_empty_is_complete(profile, monkeypatch, entries):
    payload = json.dumps({"entries": entries}).encode()
    synthetic(monkeypatch, 'import os; os.write(1,' + repr(payload) + ')')
    plan = contract.SourcePlan("source_enumeration", "https://synthetic.invalid/list")
    result = runner.run(runner.prepare(plan, profile=profile, policy={}))
    assert result.successful and result.items == tuple((item["id"], item["title"]) for item in entries)


def test_download_preserves_selectors_and_proves_owned_output(profile, monkeypatch):
    observed = synthetic(monkeypatch,
        'import json,os,sys; from pathlib import Path; a=json.loads(sys.argv[1]); '
        'p=Path(a[a.index("--output")+1]); q=Path(str(p)+".part"); '
        'q.write_bytes(b"actual synthetic media"); os.rename(q,p)')
    plan = download_plan()
    result = runner.run(runner.prepare(plan, profile=profile, policy={}))
    assert result.successful and result.artifact is not None
    assert runner.verify_artifact(result.artifact, profile=profile).read_bytes() == b"actual synthetic media"
    argv = observed[0][0]
    assert argv[argv.index("-S") + 1] == "res:480" and argv[argv.index("-f") + 1] == "bv*"
    assert "--load-info-json" in argv and plan.source not in argv
    assert not list((profile.media / ".source-inputs").glob("*/metadata.json"))


def test_normal_failure_partial_exact_proof_can_resume_same_request(profile, monkeypatch):
    synthetic(monkeypatch,
        'import json,sys; from pathlib import Path; a=json.loads(sys.argv[1]); '
        'p=Path(a[a.index("--output")+1]+".part"); p.write_bytes(b"partial"); sys.exit(3)')
    plan = download_plan()
    failure = runner.run(runner.prepare(plan, profile=profile, policy={}))
    assert failure.response_complete and failure.failure_class == "process_failure" and len(failure.partials) == 1
    prepared = runner.prepare(plan, profile=profile, policy={}, resume_artifacts=failure.partials)
    witness = runner.verify_prepared_files(prepared)
    prepared = runner.prepare(plan, profile=profile, policy={}, resume_artifacts=failure.partials, verified_files=witness)
    synthetic(monkeypatch,
        'import json,sys,os; from pathlib import Path; a=json.loads(sys.argv[1]); '
        'p=Path(a[a.index("--output")+1]); q=Path(str(p)+".part"); '
        'assert q.read_bytes()==b"partial"; q.write_bytes(b"partial finished"); os.rename(q,p)')
    result = runner.run(prepared)
    assert result.successful and result.artifact.size_bytes == len(b"partial finished")


def test_sha_preflight_is_external_fast_guard_never_reads_large_file(profile, monkeypatch):
    plan = download_plan()
    path = profile.media / (plan.artifact_relpath + ".part")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"partial")
    info = path.stat()
    proof = contract.SourcePartial("video", path.relative_to(profile.media).as_posix(), info.st_size,
        hashlib.sha256(path.read_bytes()).hexdigest(), info.st_dev, info.st_ino, info.st_mtime_ns, plan.request_sha256)
    prepared = runner.prepare(plan, profile=profile, policy={}, resume_artifacts=(proof,))
    witness = runner.verify_prepared_files(prepared)
    monkeypatch.setattr(runner, "_file_snapshot", lambda *args, **kwargs: pytest.fail("writer hashed a file"))
    assert runner.prepare(plan, profile=profile, policy={}, resume_artifacts=(proof,), verified_files=witness)


def test_post_dispatch_changed_partial_is_unknown_and_spawns_nothing(profile, monkeypatch):
    plan = download_plan()
    path = profile.media / (plan.artifact_relpath + ".part")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"partial")
    info = path.stat()
    proof = contract.SourcePartial("video", path.relative_to(profile.media).as_posix(), info.st_size,
        hashlib.sha256(path.read_bytes()).hexdigest(), info.st_dev, info.st_ino, info.st_mtime_ns, plan.request_sha256)
    prepared = runner.prepare(plan, profile=profile, policy={}, resume_artifacts=(proof,))
    path.write_bytes(b"changed")
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("changed file spawned"))
    result = runner.run(prepared)
    assert not result.response_complete and result.termination == "launch_unknown"
    assert path.read_bytes() == b"changed"


def test_original_cookie_stays_exact_and_rewritten_private_copy_is_cleaned(profile, monkeypatch):
    cookie = profile.media / "original-cookie.txt"
    cookie.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    identity = cookie.stat().st_ino
    observed = synthetic(monkeypatch,
        'import json,sys; from pathlib import Path; a=json.loads(sys.argv[1]); '
        'p=Path(a[a.index("--cookies")+1]); p.write_text("updated private copy"); '
        'sys.stdout.write(\'{"id":"synthetic"}\')')
    result = runner.run(runner.prepare(metadata_plan(), profile=profile, policy={}, cookies=cookie))
    assert result.successful
    assert cookie.stat().st_ino == identity and cookie.read_text() == "# Netscape HTTP Cookie File\n"
    argv = observed[0][0]
    copied = Path(argv[argv.index("--cookies") + 1])
    assert copied != cookie and not copied.exists()


def test_cookie_replaced_in_private_directory_is_not_deleted(profile, monkeypatch):
    cookie = profile.media / "original-cookie.txt"
    cookie.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    observed = synthetic(monkeypatch,
        'import json,sys,os; from pathlib import Path; a=json.loads(sys.argv[1]); '
        'p=Path(a[a.index("--cookies")+1]); q=p.with_suffix(".replacement"); '
        'q.write_bytes(b"unknown replacement"); os.replace(q,p); sys.stdout.write(\'{"id":"synthetic"}\')')
    result = runner.run(runner.prepare(metadata_plan(), profile=profile, policy={}, cookies=cookie))
    copied = Path(observed[0][0][observed[0][0].index("--cookies") + 1])
    assert result.termination == "cleanup_unknown" and not result.response_complete
    assert copied.read_bytes() == b"unknown replacement"
    assert cookie.read_text() == "# Netscape HTTP Cookie File\n"


@pytest.mark.parametrize("protocol", ["m3u8", "m3u8_native", "rtmp", "https+https"])
def test_ffmpeg_or_merged_protocol_is_rejected_before_spawn(profile, monkeypatch, protocol):
    plan = download_plan()
    info = json.loads(plan.info_json)
    info["protocol"] = protocol
    plan = replace(plan, info_json=contract.canonical_json(info))
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("unsupported protocol spawned"))
    with pytest.raises(contract.SourceContractError):
        runner.prepare(plan, profile=profile, policy={})


def test_actual_descendant_is_reaped_by_tree_stop(profile, monkeypatch):
    pid_path = profile.media / "synthetic-descendant-pid"
    child_script = 'import os,time; from pathlib import Path; Path(' + repr(str(pid_path)) + ').write_text(str(os.getpid())); time.sleep(30)'
    parent_script = ('import subprocess,sys,time; from pathlib import Path; '
        'subprocess.Popen([sys.executable,"-I","-c",' + repr(child_script) + ']); '
        'time.sleep(30)')
    synthetic(monkeypatch, parent_script)
    result = runner.run(runner.prepare(metadata_plan(), profile=profile, policy={}), timeout=0.7)
    assert result.termination == "timeout" and result.process_exited and result.drain_complete
    assert pid_path.exists(), "actual descendant must have run before the stop"
    pid = int(pid_path.read_text())
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x100000, False, pid)
        if handle:
            try:
                assert kernel.WaitForSingleObject(handle, 0) == 0
            finally:
                kernel.CloseHandle(handle)
    else:
        # An orphan may briefly be a zombie until its system parent reaps it.
        stat_path = Path(f"/proc/{pid}/stat")
        if stat_path.exists():
            assert stat_path.read_text().split(") ", 1)[1].startswith("Z")


def test_original_selector_capability_rejects_selected_protocol_without_fallback(profile):
    info = {"id": "synthetic", "formats": [
        {"format_id": "360", "url": "https://synthetic.invalid/360", "ext": "mp4", "protocol": "https", "height": 360, "vcodec": "avc1", "acodec": "none"},
        {"format_id": "480", "url": "https://synthetic.invalid/480", "ext": "mp4", "protocol": "m3u8_native", "height": 480, "vcodec": "avc1", "acodec": "none"},
        {"format_id": "1080", "url": "https://synthetic.invalid/1080", "ext": "mp4", "protocol": "https", "height": 1080, "vcodec": "avc1", "acodec": "none"},
    ]}
    plan = replace(download_plan(), info_json=contract.canonical_json(info))
    with pytest.raises(contract.SourceContractError):
        runner.prepare(plan, profile=profile, policy={})
    info["formats"][1]["protocol"] = "http_dash_segments"
    info["formats"][1]["fragments"] = [{"url": "https://synthetic.invalid/fragment"}]
    prepared = runner.prepare(replace(plan, info_json=contract.canonical_json(info)), profile=profile, policy={})
    assert prepared.argv[prepared.argv.index("-S") + 1] == "res:480"
    assert prepared.argv[prepared.argv.index("-f") + 1] == "bv*"
    normalized = json.loads(prepared.download_info_json)["formats"]
    assert {item["format_id"] for item in normalized} == {item["format_id"] for item in info["formats"]}
    assert {item["url"] for item in normalized} == {item["url"] for item in info["formats"]}


def test_audio_selector_stays_ba_bestaudio_and_no_merge(profile):
    info = {"id": "synthetic", "formats": [
        {"format_id": "video", "url": "https://synthetic.invalid/video", "ext": "mp4", "protocol": "https", "vcodec": "avc1", "acodec": "none"},
        {"format_id": "audio", "url": "https://synthetic.invalid/audio", "ext": "m4a", "protocol": "https", "vcodec": "none", "acodec": "mp4a", "abr": 128},
    ]}
    plan = contract.SourcePlan("download", metadata_plan().source, "audio", contract.canonical_json(info), "originals/synthetic/source-test/audio.m4a")
    prepared = runner.prepare(plan, profile=profile, policy={})
    assert prepared.argv[prepared.argv.index("-f") + 1] == "ba/bestaudio"
    assert "-S" not in prepared.argv


def test_failed_primary_replacement_is_not_claimed_or_retryable(profile, monkeypatch):
    synthetic(monkeypatch,
        'import json,sys,os; from pathlib import Path; a=json.loads(sys.argv[1]); '
        'p=Path(a[a.index("--output")+1]+".part"); q=p.with_suffix(".replacement"); '
        'q.write_bytes(b"unknown replacement"); os.replace(q,p); sys.exit(3)')
    plan = download_plan()
    result = runner.run(runner.prepare(plan, profile=profile, policy={}))
    assert result.response_complete and result.failure_class == "artifact_invalid" and result.partials == ()
    assert (profile.media / (plan.artifact_relpath + ".part")).read_bytes() == b"unknown replacement"
    with pytest.raises(contract.SourceContractError):
        contract.validate_retry_envelope(contract.encode_envelope(plan, result), request_sha256=plan.request_sha256,
            input_sha256=plan.input_sha256, kind=plan.kind, segment=plan.segment)


def test_hardlinked_paid_artifact_cannot_be_restored(profile):
    path = profile.media / "paid.mp4"
    path.write_bytes(b"original")
    proof = artifact(profile, path)
    try:
        os.link(path, profile.media / "linked.mp4")
    except OSError:
        pytest.skip("filesystem does not allow hard links")
    with pytest.raises(contract.SourceContractError):
        runner.verify_artifact(proof, profile=profile)


def test_unknown_zero_byte_final_cannot_be_success(profile, monkeypatch):
    synthetic(monkeypatch,
        'import json,sys,os; from pathlib import Path; a=json.loads(sys.argv[1]); '
        'p=Path(a[a.index("--output")+1]); os.rename(Path(str(p)+".part"),p)')
    plan = download_plan()
    result = runner.run(runner.prepare(plan, profile=profile, policy={}))
    assert result.response_complete and result.failure_class == "artifact_invalid" and result.artifact is None
    assert (profile.media / plan.artifact_relpath).stat().st_size == 0


def test_native_dash_failed_fragment_sidecar_requires_exact_retry_proof(profile, monkeypatch):
    info = json.loads(download_plan().info_json)
    info.update(protocol="http_dash_segments", fragments=[{"url": "https://synthetic.invalid/fragment"}])
    plan = replace(download_plan(), info_json=contract.canonical_json(info))
    synthetic(monkeypatch,
        'import json,sys; from pathlib import Path; a=json.loads(sys.argv[1]); '
        'p=a[a.index("--output")+1]; Path(p+".part").write_bytes(b"part"); '
        'Path(p+".ytdl").write_text(\'{"downloader":{"current_fragment":{"index":0}}}\'); '
        'Path(p+".part-Frag1.part").write_bytes(b"fragment"); sys.exit(4)')
    failure = runner.run(runner.prepare(plan, profile=profile, policy={}))
    assert failure.response_complete and failure.failure_class == "process_failure"
    assert len(failure.partials) == 3
    prepared = runner.prepare(plan, profile=profile, policy={}, resume_artifacts=failure.partials)
    witness = runner.verify_prepared_files(prepared)
    assert runner.prepare(plan, profile=profile, policy={}, resume_artifacts=failure.partials, verified_files=witness)
    sidecar = profile.media / (plan.artifact_relpath + ".ytdl")
    sidecar.write_bytes(b"changed")
    with pytest.raises(contract.SourceContractError):
        runner.prepare(plan, profile=profile, policy={}, resume_artifacts=failure.partials, verified_files=witness)


def test_pre_cancel_no_spawn_and_no_controlled_directory_write(profile, monkeypatch):
    stop = threading.Event()
    stop.set()
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("pre-cancel spawned"))
    result = runner.run(runner.prepare(metadata_plan(), profile=profile, policy={}), stop_event=stop)
    assert result.termination == "cancelled" and not result.process_exited and not result.response_complete
    assert not list(profile.media.iterdir())


def test_normal_parent_with_silent_live_descendant_is_not_retry_proof(profile, monkeypatch):
    script = ('import subprocess,sys; '
        'subprocess.Popen([sys.executable,"-I","-c","import time; time.sleep(30)"], '
        'stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); '
        'sys.exit(7)')
    synthetic(monkeypatch, script)
    plan = metadata_plan()
    result = runner.run(runner.prepare(plan, profile=profile, policy={}))
    assert result.termination == "cleanup_unknown" and not result.response_complete
    assert result.process_exited and result.drain_complete


def test_cancel_arriving_during_post_dispatch_sha_never_launches(profile, monkeypatch):
    stop = threading.Event()
    verify = runner.verify_prepared_files
    def cancel_after_sha(prepared, **kwargs):
        proof = verify(prepared, **kwargs)
        stop.set()
        return proof
    monkeypatch.setattr(runner, "verify_prepared_files", cancel_after_sha)
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("cancel after SHA spawned"))
    result = runner.run(runner.prepare(metadata_plan(), profile=profile, policy={}), stop_event=stop)
    assert result.termination == "cancelled" and not result.process_exited and not result.response_complete
    assert not list(profile.media.iterdir())


def test_closed_job_never_queries_the_callers_job(profile, monkeypatch):
    tree = runner._ProcessTree()
    tree.close()
    tree.process = object()
    assert not tree.remaining()
    tree.kill()
    tree.finish()


def test_child_filesize_estimation_cannot_change_selected_protocol_after_prepare(profile, monkeypatch):
    info = {"id": "synthetic", "duration": 10, "formats": [
        {"format_id": "a", "url": "https://synthetic.invalid/a", "ext": "mp4", "protocol": "m3u8_native",
         "height": 480, "vcodec": "avc1", "acodec": "none", "tbr": 1000},
        {"format_id": "b", "url": "https://synthetic.invalid/b", "ext": "mp4", "protocol": "https",
         "height": 480, "vcodec": "avc1", "acodec": "none", "tbr": 100, "filesize": 1000},
    ]}
    plan = replace(download_plan(), info_json=contract.canonical_json(info))
    package, options = runner._package_options(runner._argv(plan, None, ())[4:])
    # Exercise the protocol decision directly, without allowing an unrelated
    # argument/path rejection to masquerade as this sorting counterexample.
    with pytest.raises(contract.SourceContractError):
        runner._download_input(plan, package, options)
    info["formats"][0]["protocol"] = "https"
    safe = replace(plan, info_json=contract.canonical_json(info))
    prepared = runner.prepare(safe, profile=profile, policy={})
    normalized = json.loads(prepared.download_info_json)
    a = next(item for item in normalized["formats"] if item["format_id"] == "a")
    assert a["filesize_approx"] == 1_250_000
    assert len(normalized["formats"]) == 2
    # A second preparation of the exact child input has the same candidate set,
    # generated fields and bytes; original selectors remain in the argv.
    repeat_plan = replace(safe, info_json=contract.canonical_json(contract.sanitize_metadata(normalized)))
    assert runner.prepare(repeat_plan, profile=profile, policy={}).download_info_json == prepared.download_info_json


def test_old_metadata_merge_projection_cannot_override_the_actual_child_selector(profile, monkeypatch):
    info = {"id": "synthetic", "formats": [
        {"format_id": "video", "url": "https://synthetic.invalid/video", "ext": "mp4", "protocol": "https", "vcodec": "avc1", "acodec": "none"},
        {"format_id": "audio", "url": "https://synthetic.invalid/audio", "ext": "m4a", "protocol": "https", "vcodec": "none", "acodec": "mp4a"},
    ]}
    info["requested_formats"] = [dict(item) for item in info["formats"]]
    info["requested_downloads"] = [{"id": "synthetic", "url": "https://synthetic.invalid/old", "protocol": "https+https"}]
    info["webpage_url"] = metadata_plan().source
    info["original_url"] = metadata_plan().source
    synthetic(monkeypatch,
        'import json,os,sys; from pathlib import Path; a=json.loads(sys.argv[1]); '
        'v=json.loads(Path(a[a.index("--load-info-json")+1]).read_text()); '
        'assert not ({"requested_formats","requested_downloads","webpage_url","original_url"} & v.keys()); '
        'assert len(v["formats"])==2; '
        'p=Path(a[a.index("--output")+1]); q=Path(str(p)+".part"); q.write_bytes(b"single video"); os.rename(q,p)')
    plan = replace(download_plan(), info_json=contract.canonical_json(info))
    result = runner.run(runner.prepare(plan, profile=profile, policy={}))
    assert result.successful and result.artifact.size_bytes == len(b"single video")


def test_merge_projection_without_candidate_set_is_explicitly_unsupported(profile):
    info = json.loads(download_plan().info_json)
    info["requested_formats"] = [{"url": "https://synthetic.invalid/a", "protocol": "https"}]
    plan = replace(download_plan(), info_json=contract.canonical_json(info))
    with pytest.raises(contract.SourceContractError):
        runner.prepare(plan, profile=profile, policy={})


def test_format_level_live_stream_cannot_pass_native_dash_gate(profile):
    info = {"id": "synthetic", "formats": [
        {"url": "https://synthetic.invalid/a", "protocol": "http_dash_segments", "ext": "mp4", "is_live": True,
         "fragments": [{"url": "https://synthetic.invalid/fragment"}]},
    ]}
    with pytest.raises(contract.SourceContractError):
        runner.prepare(replace(download_plan(), info_json=contract.canonical_json(info)), profile=profile, policy={})
