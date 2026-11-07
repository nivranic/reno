"""Real source runner control flow with actual offline children, no platform."""
import hashlib
import json
import sys

from reno import config, db, import_tasks, ingest, source_runner, storage_admin, tasks, worker


def test_worker_recovers_paid_runner_files_after_business_commit_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path / "data")
    monkeypatch.setattr(config, "MEDIA", tmp_path / "media")
    monkeypatch.setattr(config, "ORIG", tmp_path / "media/originals")
    config.set_local({"node_role": "all", "budget_enabled": True, "download_rate_bytes_per_second": 1000})
    db.initialize()
    profile = storage_admin.profile()
    storage_admin.initialize(profile)
    metadata = {"id": "BV1ActualRunner", "title": "Synthetic", "duration": 1, "formats": [
        {"format_id": "video", "url": "https://media.synthetic.test/video.mp4", "ext": "mp4",
         "protocol": "https", "vcodec": "avc1", "acodec": "none", "height": 360, "width": 640},
        {"format_id": "audio", "url": "https://media.synthetic.test/audio.m4a", "ext": "m4a",
         "protocol": "https", "vcodec": "none", "acodec": "mp4a", "abr": 128},
    ]}
    real_launch = source_runner._ProcessTree.launch
    physical = []

    def offline_launch(tree, argv, **kwargs):
        # Keep actual prepare, dispatch policy, input files, Job/session,
        # output drains and artifact verification. Only network transport is
        # replaced by a local Python child which writes the requested bytes.
        assert argv[:4] == [sys.executable, "-I", "-m", "yt_dlp"]
        assert "--ignore-config" in argv and "--no-plugin-dirs" in argv and "--no-js-runtimes" in argv
        assert argv[argv.index("--limit-rate") + 1] == "1000"
        if "--skip-download" in argv:
            physical.append("metadata")
            code = "import sys; print(sys.argv[1])"
            replacement = [sys.executable, "-I", "-c", code, json.dumps(metadata)]
        else:
            target = argv[argv.index("--output") + 1]
            physical.append(target)
            code = "import os,pathlib,sys; p=pathlib.Path(sys.argv[1]); part=pathlib.Path(str(p)+'.part'); part.write_bytes(('synthetic-'+p.name).encode()); os.replace(part,p)"
            replacement = [sys.executable, "-I", "-c", code, target]
        return real_launch(tree, replacement, **kwargs)

    monkeypatch.setattr(source_runner._ProcessTree, "launch", offline_launch)
    real_register = ingest._register
    registration_calls = []

    def fail_first_registration(*args, **kwargs):
        registration_calls.append(True)
        if len(registration_calls) == 1:
            raise RuntimeError("Synthetic registration interruption after paid results")
        return real_register(*args, **kwargs)

    monkeypatch.setattr(ingest, "_register", fail_first_registration)
    receipt = import_tasks.submit_one("https://source.synthetic.test/actual-runner", process=False, profile=profile)
    assert worker.process_pending(max_jobs=1) == 1
    assert tasks.get(receipt["task_id"], profile=profile)["status"] == "failed"
    assert physical == ["metadata", "video.mp4", "audio.m4a"]
    config.set_local({"download_daily_videos": 0, "download_rate_bytes_per_second": 9999})
    retried = tasks.retry(receipt["task_id"], profile=profile)
    assert worker.process_pending(max_jobs=1) == 1
    assert tasks.get(retried["task_id"], profile=profile)["status"] == "done"
    assert len(physical) == 3 and len(registration_calls) == 2
    con = db.connect(profile.database)
    try:
        attempts = con.execute("SELECT state,response_complete FROM budget_attempt").fetchall()
        assert [(row["state"], row["response_complete"]) for row in attempts] == [("settled", 1)] * 3
        asset = db.get_asset(con, "BV1ActualRunner")
        files = json.loads(asset["files_json"])
        video = profile.media.parent / files["video"]
        assert video.read_bytes() == b"synthetic-video.mp4"
        assert hashlib.sha256(video.read_bytes()).hexdigest() == asset["content_sha256"]
    finally:
        con.close()
    assert worker.stop_running()
