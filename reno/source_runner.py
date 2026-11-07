"""One installed yt-dlp opportunity, with private inputs and bounded receipts.

prepare never launches or hashes large files. SHA preflight is explicit and
outside SQL writers; run rechecks those files before starting its one child.
No configuration, ledger, or retry policy is read during execution.
"""
from contextlib import ExitStack
from dataclasses import dataclass, field
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
import uuid

from . import source_contract as contract
from .storage_lifecycle import StorageProfile
from .sync_media import _Directory, _safe_stat, _native_path

_SEAL = object()
_COOKIE_LIMIT = 4 * 1024 * 1024
_INFO_ARG = "{reno-private-info}"
_COOKIE_ARG = "{reno-private-cookie}"
_EXTRAS = frozenset({"--geo-bypass", "--force-ipv4", "--force-ipv6"})
_CONTROL = ("--ignore-config", "--no-plugin-dirs", "--no-js-runtimes", "--no-cache-dir",
    "--retries", "0", "--fragment-retries", "0", "--extractor-retries", "0",
    "--file-access-retries", "0", "--concurrent-fragments", "1", "--fixup", "never",
    "--no-progress", "--no-warnings", "--no-check-formats", "--socket-timeout", "30")


def _fail(code="SOURCE_CAPABILITY_UNSUPPORTED"):
    raise contract.SourceContractError(code) from None


def _cross(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _stable(info):
    return (*_cross(info), info.st_ctime_ns, info.st_nlink)


def _regular(info):
    _safe_stat(info, directory=False)
    if info.st_nlink != 1:
        _fail("SOURCE_ARTIFACT_INVALID")


def _proof_identity(proof):
    return proof.device, proof.inode, proof.size_bytes, proof.mtime_ns


@dataclass(frozen=True, slots=True, repr=False)
class VerifiedFiles:
    request_sha256: str
    media: Path
    identities: tuple
    cookie: tuple | None
    _seal: object = field(default=None)


@dataclass(frozen=True, slots=True, repr=False)
class PreparedSource:
    plan: contract.SourcePlan
    profile: StorageProfile
    argv: tuple[str, ...]
    cookies: Path | None = None
    resume_artifacts: tuple[contract.SourcePartial, ...] = ()
    download_info_json: str | None = None
    rate: int | None = None
    extra_args: tuple[str, ...] = ()
    verified_files: VerifiedFiles | None = None
    _seal: object = field(default=None)


def _package_options(arguments):
    # Reject a cwd/PYTHONPATH package before importing or executing its code.
    spec = importlib.util.find_spec("yt_dlp")
    if spec is None or spec.origin is None or not Path(spec.origin).resolve().is_relative_to(Path(sys.prefix).resolve()):
        _fail()
    try:
        import yt_dlp
        if not Path(yt_dlp.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()):
            _fail()
        parsed = yt_dlp.parse_options(list(arguments))
        opts = parsed.ydl_opts
        if (any(opts.get(key) != 0 for key in ("retries", "fragment_retries", "extractor_retries", "file_access_retries"))
                or opts.get("concurrent_fragment_downloads") != 1 or opts.get("fixup") != "never"
                or parsed.options.plugin_dirs != [] or opts.get("js_runtimes") != {} or opts.get("cachedir") is not False):
            _fail()
        return yt_dlp, opts
    except (SystemExit, ValueError, TypeError, AttributeError, ImportError, OSError):
        _fail()


def _download_input(plan, package, options):
    """Use the installed pure sorter/selector without YoutubeDL initialization.

    YoutubeDL.__init__ loads plugins even with auto_init=False. This deliberately
    calls only sorting/selection methods, never extract/process/check_formats.
    """
    value = json.loads(plan.info_json)
    value.pop("webpage_url", None)
    value.pop("original_url", None)
    if value.get("requested_formats") and not value.get("formats"):
        _fail()
    # These are the previous metadata selector's execution projections, not
    # candidate formats. process_info otherwise inherits an old merge plan even
    # after our -f bv*/ba has chosen a single format from the full candidate set.
    value.pop("requested_formats", None)
    value.pop("requested_downloads", None)
    if value.get("is_live") or value.get("live_status") == "is_live":
        _fail()
    try:
        from yt_dlp.utils import FormatSorter, determine_protocol, filesize_from_tbr, sanitize_url
        numeric = package.YoutubeDL._NUMERIC_FIELDS
        if any(value.get(name) is not None and type(value[name]) not in {int, float} for name in numeric):
            _fail()
        if value.get("duration") is not None and value["duration"] <= 0:
            value.pop("duration")
        formats = value.get("formats") if value.get("formats") is not None else [dict(value)]
        if type(formats) is not list or not formats:
            _fail()
        formats = [dict(item) for item in formats if type(item) is dict and item.get("url")
                   and (not item.get("has_drm") or item.get("has_drm") == "maybe")]
        for index, item in enumerate(formats):
            # Reject ambiguous numeric coercion rather than letting child-only
            # int_or_none conversions change the static capability decision.
            if any(item.get(name) is not None and type(item[name]) not in {int, float} for name in numeric):
                _fail()
            item["url"] = sanitize_url(item["url"])
            FormatSorter._fill_sorting_fields(item)
            item.setdefault("format_id", str(index))
            if item.get("ext") in {"aac", "opus", "mp3", "flac", "vorbis"} and item.get("acodec") is None:
                item["acodec"] = item["ext"]
            if item.get("dynamic_range") is None and item.get("vcodec") != "none":
                item["dynamic_range"] = "SDR"
            if item.get("resolution") is None:
                item["resolution"] = package.YoutubeDL.format_resolution(item, default=None)
            if item.get("aspect_ratio") is None and item.get("width") and item.get("height"):
                item["aspect_ratio"] = round(item["width"] / item["height"], 2)
            # Match process_video_result's pre-sort size estimate. Without it
            # an apparent safe HTTPS candidate can lose to an unsupported HLS
            # candidate after the actual child prepares its format fields.
            if (("manifest-filesize-approx" in options["compat_opts"] or not item.get("manifest_url"))
                    and not item.get("filesize") and not item.get("filesize_approx")):
                item["filesize_approx"] = filesize_from_tbr(item.get("tbr"), value.get("duration"))
        ydl = object.__new__(package.YoutubeDL)
        ydl.params = options
        ydl.deprecated_feature = lambda *args, **kwargs: None
        ydl._check_formats = lambda *args, **kwargs: _fail()
        ydl.sort_formats({"formats": formats})
        selected = ydl._select_formats(formats, ydl.build_format_selector(options["format"]))
        if len(selected) != 1:
            _fail()
        chosen = selected[0]
        protocol = determine_protocol(chosen)
        if (protocol not in {"http", "https", "http_dash_segments"} or chosen.get("requested_formats")
                or chosen.get("has_drm") or chosen.get("hls_aes") or chosen.get("is_live")):
            _fail()
        if protocol == "http_dash_segments":
            fragments = chosen.get("fragments")
            if type(fragments) is not list or not fragments or any(type(item) is not dict or not item.get("url") for item in fragments):
                _fail()
        # Feed the exact normalized full candidate set to the child. Repeating
        # the installed preparation is idempotent; no unsupported candidate is
        # removed to make a different format pass this protocol gate.
        if "formats" in value:
            value["formats"] = formats
        else:
            value.update(formats[0])
        return contract.canonical_json(value)
    except (ValueError, TypeError, KeyError, AttributeError, AssertionError, RecursionError):
        _fail()


def _argv(plan, rate, extras):
    args = [sys.executable, "-I", "-m", "yt_dlp", *_CONTROL, *extras]
    if rate is not None:
        args.extend(("--limit-rate", str(rate)))
    if plan.kind == "source_metadata":
        args.extend(("--no-playlist", "--skip-download", "--dump-single-json", plan.source))
    elif plan.kind == "source_enumeration":
        args.extend(("--flat-playlist", "--skip-download", "--dump-single-json", plan.source))
    else:
        args.extend(("--no-playlist", "--no-overwrites", "--no-post-overwrites", "--no-write-info-json",
            "--no-write-playlist-metafiles", "--no-keep-fragments", "--load-info-json", _INFO_ARG))
        if plan.segment == "video":
            args.extend(("-S", "res:480", "-f", "bv*"))
        else:
            args.extend(("-f", "ba/bestaudio"))
        args.extend(("--output", Path(plan.artifact_relpath).name.replace("%", "%%")))
    return tuple(args)


def _with_parent(path):
    # As with the media root, ancestors are checked but need no listing handles
    # on system roots. The exact credential parent and file are pinned.
    return _Directory(path.parent, base=path.parent)


def _cookie_stat(path):
    with _with_parent(path) as directory:
        value = directory.entry_stat(path.name)
        _regular(value)
        if value.st_size > _COOKIE_LIMIT:
            _fail("SOURCE_COOKIE_INVALID")
        directory.verify()
        return _stable(value)


def _candidate(name, target):
    return name == target or name.startswith(target + ".")


def _partial_name(name, target):
    return name in {target + ".part", target + ".ytdl"} or re.fullmatch(re.escape(target) + r"\.part-Frag[0-9]+(?:\.part)?", name) is not None


def _check_paths(prepared):
    plan, root = prepared.plan, prepared.profile.media
    cookie = None if prepared.cookies is None else _cookie_stat(prepared.cookies)
    identities = []
    if plan.kind == "download":
        path = root / plan.artifact_relpath
        ancestor = path.parent
        while not os.path.lexists(ancestor) and ancestor != root:
            ancestor = ancestor.parent
        with _Directory(ancestor, base=root) as directory:
            directory.verify()
        if path.parent.exists():
            with _Directory(path.parent, base=root) as directory:
                found = {name for name in directory.names() if _candidate(name, path.name)}
                expected = {Path(item.relpath).name: item for item in prepared.resume_artifacts}
                if found != set(expected):
                    _fail("SOURCE_ARTIFACT_INVALID")
                for name, proof in expected.items():
                    actual = directory.entry_stat(name)
                    _regular(actual)
                    if _cross(actual) != _proof_identity(proof):
                        _fail("SOURCE_ARTIFACT_INVALID")
                    identities.append((proof.relpath, _stable(actual)))
                directory.verify()
        elif prepared.resume_artifacts:
            _fail("SOURCE_ARTIFACT_INVALID")
    witness = prepared.verified_files
    if witness is not None:
        if (type(witness) is not VerifiedFiles or witness._seal is not _SEAL
                or witness.request_sha256 != plan.request_sha256 or witness.media != root
                or witness.identities != tuple(identities) or witness.cookie != (None if cookie is None else (prepared.cookies, cookie))):
            _fail("SOURCE_ARTIFACT_INVALID")
    return tuple(identities), None if cookie is None else (prepared.cookies, cookie)


def prepare(plan, *, profile, policy, cookies=None, extra_args=(), resume_artifacts=(), verified_files=None):
    """Pure capabilities and fast file identities; no process, writes, or SHA."""
    if type(plan) is not contract.SourcePlan or type(profile) is not StorageProfile:
        _fail()
    if type(extra_args) not in {tuple, list} or any(type(item) is not str or item not in _EXTRAS for item in extra_args):
        _fail()
    extras = tuple(extra_args)
    if len(set(extras)) != len(extras) or {"--force-ipv4", "--force-ipv6"}.issubset(extras):
        _fail()
    rate = policy.get("download_rate_bytes_per_second") if isinstance(policy, dict) else getattr(policy, "download_rate_bytes_per_second", None)
    if rate is not None and (type(rate) is not int or not 1 <= rate <= 2**63 - 1):
        _fail()
    try:
        cookie = None if cookies is None else Path(os.path.abspath(os.fspath(cookies)))
        partials = tuple(resume_artifacts)
        if len(partials) > 8 or len({item.relpath for item in partials}) != len(partials):
            _fail()
        for item in partials:
            if (type(item) is not contract.SourcePartial or plan.kind != "download" or item.segment != plan.segment
                    or item.request_sha256 != plan.request_sha256 or Path(item.relpath).parent != Path(plan.artifact_relpath).parent
                    or not _partial_name(Path(item.relpath).name, Path(plan.artifact_relpath).name)):
                _fail()
        argv = _argv(plan, rate, extras)
        if cookie is not None:
            argv += ("--cookies", _COOKIE_ARG)
        package, options = _package_options(argv[4:])
        if options.get("ratelimit") != rate:
            _fail()
        info = None if plan.kind != "download" else _download_input(plan, package, options)
        value = PreparedSource(plan, profile, argv, cookie, partials, info, rate, extras, verified_files, _SEAL)
        _check_paths(value)
        return value
    except contract.SourceContractError:
        raise
    except Exception:
        _fail()


def _file_snapshot(directory, name, *, segment, relpath, request=None, expected=None, limit=None, stop_event=None):
    before_path = directory.entry_stat(name)
    _regular(before_path)
    if limit is not None and before_path.st_size > limit:
        _fail("SOURCE_ARTIFACT_INVALID")
    if expected is not None and _cross(before_path) != _proof_identity(expected):
        _fail("SOURCE_ARTIFACT_INVALID")
    with directory.open_file(name) as stream:
        before = os.fstat(stream.fileno())
        if _cross(before) != _cross(before_path):
            _fail("SOURCE_ARTIFACT_INVALID")
        digest, size = hashlib.sha256(), 0
        for chunk in iter(lambda: stream.read(128 * 1024), b""):
            if stop_event is not None and stop_event.is_set():
                _fail("SOURCE_CANCELLED")
            digest.update(chunk)
            size += len(chunk)
            if size > before.st_size:
                _fail("SOURCE_ARTIFACT_INVALID")
        after = os.fstat(stream.fileno())
        if (_stable(before) != _stable(after) or size != before.st_size
                or _cross(directory.entry_stat(name)) != _cross(after)
                or expected is not None and digest.hexdigest() != expected.sha256):
            _fail("SOURCE_ARTIFACT_INVALID")
        directory.verify()
    values = (segment, relpath, size, digest.hexdigest(), after.st_dev, after.st_ino, after.st_mtime_ns)
    proof = contract.SourceArtifact(*values) if request is None else contract.SourcePartial(*values, request)
    return proof, _stable(before_path)


def verify_artifact(artifact, *, profile):
    if type(artifact) is not contract.SourceArtifact or type(profile) is not StorageProfile:
        _fail("SOURCE_ARTIFACT_INVALID")
    path = profile.media / artifact.relpath
    try:
        with _Directory(path.parent, base=profile.media) as directory:
            _file_snapshot(directory, path.name, segment=artifact.segment, relpath=artifact.relpath, expected=artifact)
        return path
    except Exception:
        _fail("SOURCE_ARTIFACT_INVALID")


def verify_prepared_files(prepared, *, stop_event=None):
    """Long SHA preflight, outside a database writer; return a fast witness."""
    if type(prepared) is not PreparedSource or prepared._seal is not _SEAL:
        _fail()
    try:
        identities, cookie = _check_paths(prepared)
        for proof in prepared.resume_artifacts:
            path = prepared.profile.media / proof.relpath
            with _Directory(path.parent, base=prepared.profile.media) as directory:
                _file_snapshot(directory, path.name, segment=proof.segment, relpath=proof.relpath,
                    request=proof.request_sha256, expected=proof, stop_event=stop_event)
        if prepared.cookies is not None:
            path = prepared.cookies
            with _with_parent(path) as directory:
                _file_snapshot(directory, path.name, segment="video", relpath="credential", request=prepared.plan.request_sha256, limit=_COOKIE_LIMIT, stop_event=stop_event)
        if _check_paths(prepared) != (identities, cookie):
            _fail("SOURCE_ARTIFACT_INVALID")
        return VerifiedFiles(prepared.plan.request_sha256, prepared.profile.media, identities, cookie, _SEAL)
    except contract.SourceContractError:
        raise
    except Exception:
        _fail("SOURCE_ARTIFACT_INVALID")


class _Drain:
    def __init__(self, stream, limit, overflow, *, keep):
        self.stream, self.limit, self.overflow, self.keep = stream, limit, overflow, keep
        self.size, self.digest, self.buffer = 0, hashlib.sha256(), bytearray()
        self.eof, self.error = False, False
        self.thread = threading.Thread(target=self._read, name="reno-source-drain", daemon=True)

    def _read(self):
        try:
            while chunk := self.stream.read(32768):
                self.size += len(chunk)
                self.digest.update(chunk)
                if self.keep and len(self.buffer) < self.limit:
                    self.buffer.extend(chunk[:self.limit - len(self.buffer)])
                if self.size > self.limit:
                    self.overflow.set()
            self.eof = True
        except Exception:
            self.error = True
        finally:
            self.stream.close()


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    class _JobBasic(ctypes.Structure):
        _fields_ = [("per_process", ctypes.c_longlong), ("per_job", ctypes.c_longlong),
                    ("flags", wintypes.DWORD), ("min_working", ctypes.c_size_t), ("max_working", ctypes.c_size_t),
                    ("active_limit", wintypes.DWORD), ("affinity", ctypes.c_size_t),
                    ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]

    class _IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]

    class _JobExtended(ctypes.Structure):
        _fields_ = [("basic", _JobBasic), ("io", _IoCounters), ("process_memory", ctypes.c_size_t),
                    ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]

    class _JobAccounting(ctypes.Structure):
        _fields_ = [(name, ctypes.c_longlong) for name in ("user_time", "kernel_time", "period_user", "period_kernel")] + [
            (name, wintypes.DWORD) for name in ("page_faults", "total_processes", "active_processes", "terminated_processes")]

    _kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    _kernel.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    _kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, wintypes.LPVOID]
    _kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    _nt = ctypes.WinDLL("ntdll")
    _nt.NtResumeProcess.argtypes = [wintypes.HANDLE]
    _nt.NtResumeProcess.restype = ctypes.c_long


class _ProcessTree:
    """Windows suspended admission to a kill-on-close Job.

    POSIX uses a new session/process group. Extra executables are disabled and
    ffmpeg-requiring protocols are rejected during capability preparation.
    """
    def __init__(self):
        self.process = None
        self.job = None
        self.drains = ()
        self.closed = False
        if os.name == "nt":
            self.job = _kernel.CreateJobObjectW(None, None)
            limits = _JobExtended()
            # Windows virtualenv python.exe is a redirector which legitimately
            # starts the base interpreter. A one-process limit would reject
            # every real Python invocation; all descendants inherit this Job.
            limits.basic.flags = 0x2000
            if not self.job or not _kernel.SetInformationJobObject(self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                self.close()
                _fail()

    def launch(self, argv, **kwargs):
        if os.name == "nt":
            kwargs["creationflags"] = 0x4 | 0x08000000  # CREATE_SUSPENDED | CREATE_NO_WINDOW
        else:
            kwargs["start_new_session"] = True
        self.process = subprocess.Popen(argv, shell=False, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
        if os.name == "nt":
            if not _kernel.AssignProcessToJobObject(self.job, int(self.process._handle)):
                self.process.kill()
                self.process.wait()
                _fail()
            if _nt.NtResumeProcess(int(self.process._handle)) != 0:
                self.kill()
                self.process.wait()
                _fail()
        return self.process

    def terminate(self):
        if self.process is None:
            return
        try:
            if os.name == "nt":
                _kernel.TerminateJobObject(self.job, 1)
            else:
                os.killpg(self.process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    def kill(self):
        if self.process is None or self.closed:
            return
        try:
            if os.name == "nt":
                _kernel.TerminateJobObject(self.job, 1)
            else:
                os.killpg(self.process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def remaining(self):
        if self.process is None or self.closed:
            return False
        if os.name == "nt":
            accounting = _JobAccounting()
            if not _kernel.QueryInformationJobObject(self.job, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None):
                _fail()
            return accounting.active_processes != 0
        try:
            os.killpg(self.process.pid, 0)
            return True
        except ProcessLookupError:
            return False

    def finish(self):
        # Wait for real Job accounting to reach zero, including the Windows
        # virtualenv redirector/base-interpreter pair. Closing a Job or calling
        # TerminateJobObject alone is not a process-tree exit observation.
        if os.name == "nt":
            while self.remaining():
                self.kill()
                time.sleep(0.01)

    def close(self):
        if self.job is not None:
            _kernel.CloseHandle(self.job)
            self.job = None
        self.closed = True


def _collect(tree, process, *, stop_event, timeout):
    overflow = threading.Event()
    drains = (_Drain(process.stdout, contract.STDOUT_LIMIT, overflow, keep=True),
              _Drain(process.stderr, contract.STDERR_LIMIT, overflow, keep=False))
    tree.drains = drains
    for drain in drains:
        drain.thread.start()
    deadline, termination = time.monotonic() + timeout, "exited"
    while process.poll() is None:
        if overflow.is_set():
            termination = "output_limit"
            break
        if stop_event is not None and stop_event.is_set():
            termination = "cancelled"
            break
        if time.monotonic() >= deadline:
            termination = "timeout"
            break
        time.sleep(0.01)
    if termination != "exited":
        tree.terminate()
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            tree.kill()
    # Retain process ownership until the real parent is reaped. Kill cannot be
    # interpreted as a completed retry proof even if its return code is positive.
    process.wait()
    for drain in drains:
        drain.thread.join(timeout=0.5)
    if any(drain.thread.is_alive() for drain in drains):
        termination = "cleanup_unknown" if termination == "exited" else termination
        tree.kill()
        for drain in drains:
            drain.thread.join()
    # Job accounting can lag the redirector's wait handle by a few scheduler
    # ticks. Observe bounded natural quiescence before classifying a remaining
    # live descendant; do not kill or invalidate ordinary successful responses.
    natural_deadline = time.monotonic() + 0.2
    while termination == "exited" and tree.remaining() and time.monotonic() < natural_deadline:
        time.sleep(0.01)
    if tree.remaining():
        if termination == "exited":
            termination = "cleanup_unknown"
        tree.kill()
    tree.finish()
    if overflow.is_set():
        termination = "output_limit"
    if process.returncode < 0 and termination == "exited":
        termination = "cleanup_unknown"
    complete = all(drain.eof and not drain.error and not drain.thread.is_alive() for drain in drains)
    if not complete and termination == "exited":
        termination = "cleanup_unknown"
    return termination, complete, drains


def _write_private(directory, name, content):
    with directory.open_file(name, write=True, create=True) as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
        identity = _stable(os.fstat(stream.fileno()))
    directory.verify()
    return identity


def _remove_private(directory, name, identity):
    """Delete only the exact private file this invocation created."""
    info = directory.entry_stat(name)
    _regular(info)
    # yt-dlp may save updated cookie contents to its copied image. Only the
    # original device/inode is ours; an externally replaced image is preserved.
    if (_cross(info)[:2] != identity[:2]
            or name != "cookies.txt" and _cross(info) != identity[:4]):
        _fail("SOURCE_ARTIFACT_INVALID")
    directory.verify()
    directory.unlink(name)


def _copy_cookie(prepared, directory):
    path = prepared.cookies
    with _with_parent(path) as source:
        before_path = source.entry_stat(path.name)
        _regular(before_path)
        if before_path.st_size > _COOKIE_LIMIT:
            _fail("SOURCE_COOKIE_INVALID")
        with source.open_file(path.name) as stream:
            before = os.fstat(stream.fileno())
            raw = stream.read(_COOKIE_LIMIT + 1)
            if (len(raw) != before.st_size or _stable(before) != _stable(os.fstat(stream.fileno()))
                    or _cross(source.entry_stat(path.name)) != _cross(before) or _cross(before_path) != _cross(before)):
                _fail("SOURCE_COOKIE_INVALID")
        source.verify()
    return _write_private(directory, "cookies.txt", raw)


def _parse_output(plan, raw):
    try:
        value = json.loads(raw.decode("utf-8"))
        if plan.kind == "source_metadata":
            return {"metadata_json": contract.canonical_json(contract.sanitize_metadata(value))}
        if type(value) is not dict or type(value.get("entries")) is not list or len(value["entries"]) > contract.MAX_ITEMS:
            _fail("SOURCE_OUTPUT_INVALID")
        items = []
        for item in value["entries"]:
            if type(item) is not dict or type(item.get("id")) is not str or type(item.get("title", "")) is not str:
                _fail("SOURCE_OUTPUT_INVALID")
            items.append((item["id"], item.get("title", "")))
        # DTO validation checks bounded titles and valid identifiers.
        return {"items": tuple(items)}
    except contract.SourceContractError:
        raise
    except (UnicodeError, ValueError, TypeError, RecursionError):
        _fail("SOURCE_OUTPUT_INVALID")


def run(prepared, *, stop_event=None, timeout=600):
    """One actual process, fully drained and reaped before returning a proof."""
    if (type(prepared) is not PreparedSource or prepared._seal is not _SEAL or type(timeout) not in {int, float}
            or not math.isfinite(timeout) or timeout <= 0):
        _fail()
    plan = prepared.plan
    base = dict(request_sha256=plan.request_sha256, process_exited=False, drain_complete=False,
                returncode=None, termination="launch_unknown")
    if stop_event is not None and stop_event.is_set():
        return contract.SourceProcessResult(**{**base, "termination": "cancelled"})
    stack, private_files, tree, output = ExitStack(), [], None, None
    private_directory = None
    try:
        # After dispatch this failure is unknown, never a zero-cost safe retry.
        verify_prepared_files(prepared, stop_event=stop_event)
        if stop_event is not None and stop_event.is_set():
            base["termination"] = "cancelled"
            return contract.SourceProcessResult(**base)
        expected_argv = _argv(plan, prepared.rate, prepared.extra_args)
        if prepared.cookies is not None:
            expected_argv += ("--cookies", _COOKIE_ARG)
        if prepared.argv != expected_argv:
            _fail()
        root = prepared.profile.media
        private_directory = stack.enter_context(_Directory(root / ".source-inputs" / uuid.uuid4().hex, create=True, base=root))
        if prepared.download_info_json is not None:
            private_files.append(("metadata.json", _write_private(private_directory, "metadata.json", prepared.download_info_json.encode("utf-8"))))
        if prepared.cookies is not None:
            private_files.append(("cookies.txt", _copy_cookie(prepared, private_directory)))
        argv = [str(private_directory.path / "metadata.json") if item == _INFO_ARG else
                str(private_directory.path / "cookies.txt") if item == _COOKIE_ARG else item for item in prepared.argv]
        kwargs = {"cwd": str(private_directory.path)}
        target_directory, initial_part = None, None
        if plan.kind == "download":
            target = root / plan.artifact_relpath
            target_directory = stack.enter_context(_Directory(target.parent, create=True, base=root))
            _check_paths(prepared)
            primary = next((item for item in prepared.resume_artifacts if item.relpath == plan.artifact_relpath + ".part"), None)
            if primary is None:
                initial_part = _write_private(target_directory, target.name + ".part", b"")
            else:
                initial_part = _stable(target_directory.entry_stat(target.name + ".part"))
            # fd-relative working directory pins POSIX output to the admitted
            # directory even if its path is renamed. Windows directory handles
            # deny ancestor renames and reject junction/reparse substitution.
            if os.name == "posix":
                pinned = Path(f"/proc/self/fd/{target_directory.fd}")
                if not pinned.exists():
                    _fail()
                kwargs.update(cwd=str(pinned), pass_fds=(target_directory.fd,))
            else:
                kwargs["cwd"] = str(target_directory.path)
        tree = _ProcessTree()
        if stop_event is not None and stop_event.is_set():
            base["termination"] = "cancelled"
            return contract.SourceProcessResult(**base)
        process = tree.launch(argv, **kwargs)
        termination, drain_complete, drains = _collect(tree, process, stop_event=stop_event, timeout=timeout)
        base.update(process_exited=process.returncode is not None, drain_complete=drain_complete,
            returncode=process.returncode, termination=termination,
            stdout_bytes=drains[0].size, stdout_sha256=drains[0].digest.hexdigest(),
            stderr_bytes=drains[1].size, stderr_sha256=drains[1].digest.hexdigest())
        observation = contract.SourceProcessResult(**base)
        if not observation.response_complete:
            output = observation
        elif process.returncode > 0:
            partials = []
            if target_directory is not None:
                for name in target_directory.names():
                    if not _candidate(name, target.name):
                        continue
                    if not _partial_name(name, target.name) or len(partials) >= 8:
                        _fail("SOURCE_ARTIFACT_INVALID")
                    proof, _ = _file_snapshot(target_directory, name, segment=plan.segment,
                        relpath=(Path(plan.artifact_relpath).parent / name).as_posix(), request=plan.request_sha256)
                    if name == target.name + ".part" and (proof.device, proof.inode) != initial_part[:2]:
                        _fail("SOURCE_ARTIFACT_INVALID")
                    partials.append(proof)
            output = contract.SourceProcessResult(**base, failure_class="process_failure", partials=tuple(partials))
        elif plan.kind == "download":
            proof, _ = _file_snapshot(target_directory, target.name, segment=plan.segment, relpath=plan.artifact_relpath)
            if (proof.device, proof.inode) != initial_part[:2]:
                _fail("SOURCE_ARTIFACT_INVALID")
            output = contract.SourceProcessResult(**base, artifact=proof)
        else:
            try:
                output = contract.SourceProcessResult(**base, **_parse_output(plan, bytes(drains[0].buffer)))
            except contract.SourceContractError as error:
                output = contract.SourceProcessResult(**base, failure_class="unsafe_metadata" if error.code == "SOURCE_METADATA_UNSAFE" else "invalid_output")
    except BaseException as error:
        if tree is not None and tree.process is not None:
            tree.kill()
            tree.process.wait()
            for drain in tree.drains:
                if drain.thread.ident is not None:
                    drain.thread.join()
            for stream in (tree.process.stdout, tree.process.stderr):
                if stream is not None and not stream.closed:
                    stream.close()
            tree.finish()
        if getattr(error, "code", None) == "SOURCE_CANCELLED" or stop_event is not None and stop_event.is_set() and tree is None:
            base["termination"] = "cancelled"
        output = contract.SourceProcessResult(**base, failure_class="artifact_invalid" if base.get("termination") == "exited" else None)
        if not isinstance(error, Exception):
            raise
    finally:
        if tree is not None:
            tree.close()
        try:
            for name, identity in reversed(private_files):
                _remove_private(private_directory, name, identity)
        except Exception:
            # A rewritten cookie image may only be removed with its current
            # exact identity. Never delete an unknown replacement or the input.
            if output is not None:
                output = contract.SourceProcessResult(**{**base, "termination": "cleanup_unknown"})
        stack.close()
    return output
