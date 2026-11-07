# -*- coding: utf-8 -*-
"""Central config: defaults overridable by config.local.json (gitignored)."""
import json
import os
import threading
import errno
import math
import uuid
from contextlib import contextmanager
from time import monotonic, sleep
from pathlib import Path, PurePosixPath

from .budget_contract import POLICY_DEFAULTS

ROOT = Path(__file__).resolve().parents[1]
# RENO_DATA_DIR relocates every mutable path (db / media / frame cache).
# Deployment flexibility + lets isolated instances (tests, multi-process
# checks) run side by side without touching the repo's real data.
_data_dir = os.environ.get("RENO_DATA_DIR")
if _data_dir:
    DATA = Path(_data_dir)
    MEDIA = DATA.parent / "media"
else:
    DATA = ROOT / "data"
    MEDIA = ROOT / "media"
FRAMES = MEDIA / "frames"
AUDIO = MEDIA / "audio"
ORIG = MEDIA / "originals"
FRAME_CACHE = MEDIA.parent / "frames_cache"

# thread safety: get()'s lazy _load and set_local's cache drop race under
# concurrent requests (robustness suite finding #11 - NoneType.get 500s)
_config_lock = threading.Lock()
_local_writer = threading.local()

DEFAULTS = {
    "zhipu_api_key": "",
    "zhipu_base_url": "https://open.bigmodel.cn/api/paas/v4",
    "atomize_model": "GLM-5.3-Flash",
    "judge_models": ["GLM-5.3-Flash"],
    "vlm_model": "glm-4.6v",
    "asr_model": "small",
    "asr_compute": "int8",
    "max_frames_per_video": 22,
    # Regenerable API thumbnails only; processing evidence in MEDIA/frames is
    # excluded. Zero serves extracted bytes without retaining managed frames.
    "frame_cache_max_bytes": 512 * 1024 * 1024,
    "safety_interval_s": 2.5,
    "hf_endpoint": "https://hf-mirror.com",
    "atomize_prompt_version": "v2",
    "judge_prompt_version": "v2",
    "taxonomy_version": "v2",
    "schema_version": "2.0.0",
    # deployment role: "all" = current single-machine behavior; "cloud" =
    # serving only (no pipeline worker, imports enqueue, glm-5.3* coding-plan
    # models refused - see docs/sync-plan.md); "home" = full pipeline + sync
    "node_role": "all",
    # cloud /ask model: a paas (non glm-5.3*) model, empty falls back to
    # atomize_model - which on cloud must not be glm-5.3* (llm guard)
    "ask_model": "",
    "summary_model": "",
    # separate credential for /api/sync/* (Phase 1 sync agent); empty = sync
    # endpoints stay closed. Header-only (X-Sync-Token), never a query param
    "sync_token": "",
    # Home sync is opt-in and identity-pinned; it never infers a cloud peer.
    "sync_enabled": False,
    "sync_cloud_url": "",
    "sync_target_library_id": "",
    "sync_target_node_id": "",
    "sync_interval_seconds": 60,
    "sync_previous_token": "",
    "sync_previous_token_until": "",
    # A test-only HTTP exception also requires a fully isolated QA tree and a
    # loopback/artificial ASGI client. This flag alone never enables HTTP sync.
    "sync_allow_insecure_test_transport": False,
    # Explicit IPs only. serve passes this value to uvicorn, so a broad ambient
    # FORWARDED_ALLOW_IPS cannot make client-supplied proxy headers authoritative.
    "trusted_proxy_ips": ["127.0.0.1", "::1"],
    # /ask per-token rate limit per minute, 0 = off
    "ask_rate_per_min": 10,
    "personal_features_enabled": False,
    # Personal snapshots are selected explicitly; enabling this transport
    # never grants a cloud reader, sharing or primary handoff.
    "personal_mirror_enabled": False,
    # User-facing reads of an installed cloud mirror are a separate opt-in.
    # This flag never grants upload, mutation, transport or primary authority.
    "personal_cloud_read_enabled": False,
    "field_checks_enabled": False,
    "attachment_image_max_bytes": 10 * 1024 * 1024,
    "attachment_pdf_max_bytes": 20 * 1024 * 1024,
    "attachment_per_source_limit": 20,
    "attachment_staging_ttl_seconds": 24 * 60 * 60,
    # P06 B01 only defines policy; later ledger/adapter batches enforce it.
    **POLICY_DEFAULTS,
    # A resolver may need six DNS/header pairs, including one authorized
    # refresh. Keep this cap outside historical Policy hashes and freeze it
    # only when the original resolution operation is first created.
    "source_resolution_max_attempts": 24,
}


def is_cloud() -> bool:
    return str(get("node_role") or "all").lower() == "cloud"


def resolve_media(p) -> "Path | None":
    """Resolve a files_json path entry against the media base (MEDIA.parent).

    Canonical stored form is POSIX-relative ("media/originals/x.mp4"), which
    resolves identically for a repo checkout and a relocated RENO_DATA_DIR.
    Legacy rows may hold backslash separators or absolute Windows paths;
    absolute values pass through unchanged, separators are normalized."""
    if not p:
        return None
    s = str(p)
    if s.startswith(("/", "\\\\")) or (len(s) > 2 and s[1] == ":"):
        return Path(s)
    return MEDIA.parent / PurePosixPath(s.replace("\\", "/"))

_cache = None


def get(key: str = "", default=None):
    global _cache
    with _config_lock:
        if _cache is None:
            _load()
        if not key:
            return _cache
        return _cache.get(key, DEFAULTS.get(key, default))


def local_path() -> Path:
    """One source for config reads and writes; explicit paths never fall back."""
    explicit = os.environ.get("RENO_CONFIG_FILE")
    if explicit is not None and not explicit.strip():
        raise ValueError("RENO_CONFIG_FILE must name an existing JSON file")
    local = Path(explicit).expanduser().resolve() if explicit is not None else ROOT / "config.local.json"
    qa_root = os.environ.get("RENO_QA_ROOT")
    if qa_root is not None:
        if not qa_root.strip() or explicit is None or not os.environ.get("RENO_DATA_DIR"):
            raise ValueError("QA requires RENO_QA_ROOT, RENO_CONFIG_FILE and RENO_DATA_DIR")
        root = Path(qa_root).expanduser().resolve()
        if Path(__file__).resolve().parents[1].is_relative_to(root):
            raise ValueError("QA root must not contain the repository or its production data")
        for path in (local, DATA, MEDIA, FRAMES, AUDIO, ORIG, FRAME_CACHE):
            resolved = path.resolve()
            if resolved == root or not resolved.is_relative_to(root):
                raise ValueError("QA config and mutable paths must stay inside RENO_QA_ROOT")
    return local


def _read_local(local: Path) -> dict:
    if not local.exists() and "RENO_CONFIG_FILE" not in os.environ:
        return {}
    data = json.loads(local.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Config JSON must be an object")
    return data


def is_isolated_qa() -> bool:
    """Read-only proof required by insecure test transport, never a sole flag."""
    qa_root = os.environ.get("RENO_QA_ROOT")
    if not qa_root or not qa_root.strip():
        return False
    try:
        local = local_path()  # Validates config and every mutable directory.
        _read_local(local)  # A cached config must not excuse a missing QA file.
        root = Path(qa_root).expanduser().resolve()
        repository = Path(__file__).resolve().parents[1]
        if root.is_relative_to(repository) or repository.is_relative_to(root):
            return False
        return Path(os.environ["RENO_DATA_DIR"]).expanduser().resolve() == DATA.resolve()
    except (OSError, ValueError, TypeError, KeyError):
        return False


def sync_access_settings() -> dict:
    """Fresh credential snapshot: external atomic file edits take effect live.

    This read-only path deliberately does not reuse or invalidate the general
    model/application cache and never creates mutable directories.
    """
    names = ("sync_token", "sync_previous_token", "sync_previous_token_until",
             "sync_allow_insecure_test_transport")
    local = _read_local(local_path())
    return {name: os.environ.get(f"RENO_{name.upper()}", local.get(name, DEFAULTS[name])) for name in names}


def frame_cache_budget_bytes() -> int:
    """Read the cache budget without creating directories or loading secrets
    into the general config cache. The read-only cache CLI uses this too.
    """
    name = "frame_cache_max_bytes"
    local = _read_local(local_path())
    value = os.environ.get(f"RENO_{name.upper()}", local.get(name, DEFAULTS[name]))
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        value = int(value)
    if type(value) is not int or value < 0 or value > 2**63 - 1:
        raise ValueError("frame_cache_max_bytes must be a nonnegative integer")
    return value


def budget_settings() -> dict:
    """Fresh, non-secret policy; no app-cache changes or storage creation."""
    from . import budget_contract
    try:
        with _config_lock:
            local = _read_local(local_path())
            environment = dict(os.environ)
            values = {name: local.get(name, DEFAULTS[name]) for name in POLICY_DEFAULTS}
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        raise budget_contract.BudgetContractError("BUDGET_CONFIG_INVALID") from None
    for name in POLICY_DEFAULTS:
        raw = environment.get("RENO_" + name.upper())
        if raw is None:
            continue
        if name in {"budget_timezone", "model_total_tokens_mode"}:
            values[name] = raw
        else:
            try:
                values[name] = json.loads(raw)
            except (ValueError, TypeError, UnicodeError):
                raise budget_contract.BudgetContractError("BUDGET_POLICY_INVALID", field=name) from None
    return budget_contract.parse_policy(values).public()


def source_resolution_attempt_limit() -> int:
    """Fresh resolver cap; historical budget policy bytes stay unchanged."""
    from . import budget_contract
    name = "source_resolution_max_attempts"
    try:
        with _config_lock:
            local = _read_local(local_path())
            value = local.get(name, DEFAULTS[name])
            override = os.environ.get("RENO_" + name.upper())
        if override is not None:
            value = json.loads(override)
        if type(value) is not int or not 1 <= value <= 128:
            raise ValueError
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        raise budget_contract.BudgetContractError("BUDGET_CONFIG_INVALID", field=name) from None
    return value


def _load():
    global _cache
    cfg = dict(DEFAULTS)
    cfg.update(_read_local(local_path()))
    # env vars win (RENO_<UPPER_KEY>)
    for k in list(cfg):
        env = os.environ.get(f"RENO_{k.upper()}")
        if env is not None:
            cfg[k] = env
    _cache = cfg
    # Reading configuration must not create storage roots. Explicit startup
    # owns member registration, including proof that a root is actually new.


def db_path() -> Path:
    if os.environ.get("RENO_QA_ROOT") is not None:
        # Database setup can precede get(); validate before it can write.
        _read_local(local_path())
    return DATA / "reno.db"


class ConfigWriteBusy(OSError):
    def __init__(self):
        super().__init__("CONFIG_WRITE_BUSY")


@contextmanager
def local_write_lock(*, timeout=10.0):
    """All local-config writers share one persistent no-follow OS lock.

    Ordinary reads retain the original short thread lock and create nothing.
    The selected parent must already exist. Never delete the lock file: doing
    so would let different processes lock different inodes for the same config.
    """
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout < 0:
        raise ValueError("CONFIG_WRITE_TIMEOUT_INVALID")
    deadline = monotonic() + timeout
    if not _config_lock.acquire(timeout=timeout):
        raise ConfigWriteBusy()
    try:
        from . import sync_media as safe
        from .sync_protocol import SyncError
        local = local_path()
        try:
            with safe._Directory(local.parent) as directory:
                name = "." + local.name + ".write.lock"
                with directory.open_file(name, write=True, lock=True) as stream:
                    if os.name == "nt":
                        import msvcrt
                    else:
                        import fcntl
                    while True:
                        try:
                            if os.name == "nt":
                                stream.seek(0)
                                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                            else:
                                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                            break
                        except OSError as exc:
                            contention = exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}
                            if os.name == "nt":
                                contention = contention or getattr(exc, "winerror", None) == 33
                            if not contention:
                                raise
                            remaining = deadline - monotonic()
                            if remaining <= 0:
                                raise ConfigWriteBusy() from None
                            sleep(min(0.05, remaining))
                    try:
                        opened, current = os.fstat(stream.fileno()), directory.entry_stat(name)
                        safe._safe_stat(current, directory=False)
                        if current.st_nlink != 1 or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
                            raise OSError("CONFIG_WRITE_LOCK_CHANGED")
                        directory.verify()
                        _local_writer.context = (local, directory)
                        try:
                            yield local
                            directory.verify()
                            after = directory.entry_stat(name)
                            if (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino) or after.st_nlink != 1:
                                raise OSError("CONFIG_WRITE_LOCK_CHANGED")
                        finally:
                            del _local_writer.context
                    finally:
                        if os.name == "nt":
                            stream.seek(0)
                            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                        else:
                            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        except SyncError:
            raise OSError("CONFIG_WRITE_PATH_INVALID") from None
    finally:
        _config_lock.release()


def _writer_directory(local):
    context = getattr(_local_writer, "context", None)
    if context is None or context[0] != local:
        raise OSError("CONFIG_WRITE_GUARD_REQUIRED")
    context[1].verify()
    return context[1]


def _read_local_locked(local: Path):
    """Read the same pinned directory selected by the shared writer lock."""
    directory = _writer_directory(local)
    try:
        with directory.open_file(local.name) as stream:
            data = json.loads(stream.read().decode("utf-8"))
    except FileNotFoundError:
        if "RENO_CONFIG_FILE" in os.environ:
            raise FileNotFoundError("CONFIG_LOCAL_MISSING") from None
        return {}
    if type(data) is not dict:
        raise ValueError("Config JSON must be an object")
    directory.verify()
    return data


def _write_local_locked(local: Path, data: dict):
    """Atomic replacement core; caller must hold local_write_lock."""
    global _cache
    directory = _writer_directory(local)
    raw = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    name = "." + local.name + "." + uuid.uuid4().hex + ".tmp"
    created = False
    try:
        with directory.open_file(name, write=True, create=True) as stream:
            created = True
            stream.write(raw.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        directory.verify()
        if os.name == "nt":
            # The directory handle denies rename/delete throughout this scope.
            os.replace(directory.path / name, local)
        else:
            # A renamed/replaced parent path cannot redirect secret JSON to a
            # different directory. Both operands address the pinned inode.
            os.replace(name, local.name, src_dir_fd=directory.fd, dst_dir_fd=directory.fd)
        _cache = None
    finally:
        if created:
            try:
                directory.unlink(name)
            except FileNotFoundError:
                pass


def set_local(updates: dict):
    """Merge fresh config under the common process/thread lock, then replace it."""
    with local_write_lock() as local:
        # A missing/malformed explicitly selected file is a hard failure,
        # including on writes. It must never fall back to repository secrets.
        try:
            data = _read_local_locked(local)
        except (json.JSONDecodeError, ValueError):
            if "RENO_CONFIG_FILE" in os.environ:
                raise
            data = {}
        data.update(updates)
        _write_local_locked(local, data)
