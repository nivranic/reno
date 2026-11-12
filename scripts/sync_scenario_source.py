"""Synthetic provider responses for the isolated sync scenario's source APIs."""

import hashlib
from pathlib import Path
import shutil
from types import SimpleNamespace

import httpx

from reno import ingest, source_budget, source_contract, source_resolution, sync_sources


class SyntheticSourceRunner:
    def __init__(self, value, step):
        self.root = Path(value["root"])
        self.videos = value["videos"]
        self.step = step

    def item(self, url):
        return next(item for item in self.videos if url in (item["short_url"], item["final_url"]))

    def resolve(self, prepared, *, late_sink=None):
        prepared.check()
        plan = prepared.plan
        item = self.item(plan.submitted_url)
        assert plan.current_url in (item["short_url"], item["final_url"])
        if plan.phase == "dns":
            if plan.hop_index == 0:
                self.step("resolve", item["video_id"])
            return source_resolution.contract.ResolutionResult(plan.request_sha256, "dns", True, "completed",
                addresses=("8.8.8.8",), observed_at=source_resolution._now())
        assert plan.phase == "headers"
        redirect = plan.current_url == item["short_url"]
        headers = httpx.Headers({"location": item["final_url"]} if redirect else {"content-type": "text/html"})
        return source_resolution._header_result(plan, 302 if redirect else 200, headers)

    def prepare(self, plan, *, profile, **kwargs):
        assert type(plan) is source_contract.SourcePlan
        self.item(plan.source)
        return SimpleNamespace(plan=plan, profile=profile)

    def run(self, prepared, *, stop_event=None):
        plan, profile = prepared.plan, prepared.profile
        if stop_event is not None and stop_event.is_set():
            return source_contract.SourceProcessResult(plan.request_sha256, False, False, None, "cancelled")
        item = self.item(plan.source)
        if plan.kind == "source_metadata":
            self.step("metadata", item["video_id"])
            info = source_contract.canonical_json(source_contract.sanitize_metadata({
                "id": item["video_id"], "title": item["title"], "extractor": "BiliBili",
                "duration": 6, "webpage_url": item["final_url"],
            }))
            raw = info.encode("utf-8")
            return source_contract.SourceProcessResult(plan.request_sha256, True, True, 0, "exited",
                stdout_bytes=len(raw), stdout_sha256=hashlib.sha256(raw).hexdigest(), metadata_json=info)
        assert plan.kind == "download"
        if plan.segment == "audio":
            return source_contract.SourceProcessResult(plan.request_sha256, True, True, 1, "exited",
                failure_class="process_failure")
        assert plan.segment == "video"
        self.step("download", item["video_id"])
        target = profile.media / plan.artifact_relpath
        assert not target.exists()
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copyfile(self.root / "synthetic-input" / item["filename"], target)
        target.chmod(0o600)
        stat = target.stat()
        artifact = source_contract.SourceArtifact(plan.segment, plan.artifact_relpath, stat.st_size,
            hashlib.sha256(target.read_bytes()).hexdigest(), stat.st_dev, stat.st_ino, stat.st_mtime_ns)
        return source_contract.SourceProcessResult(plan.request_sha256, True, True, 0, "exited", artifact=artifact)

    @staticmethod
    def verify_artifact(artifact, *, profile):
        from reno.source_runner import verify_artifact
        return verify_artifact(artifact, profile=profile)


def install_sources(value, step):
    runner = SyntheticSourceRunner(value, step)
    source_budget._components = lambda: (source_contract, runner)
    source_resolution.run = runner.resolve

    def unexpected_legacy(*args, **kwargs):
        raise AssertionError("The Home17 scenario requires the budgeted synthetic source provider")

    ingest._ytdlp = unexpected_legacy
    sync_sources.resolve_url = unexpected_legacy
