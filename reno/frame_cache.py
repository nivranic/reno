"""Disposable API JPEGs with a private index and one cross-process lifetime lock.

Only files this module created and registered are eligible for eviction. Old
cache layouts and unregistered files are preserved. The caller supplies the
dedicated FRAME_CACHE root; no knowledge DB, media frames or config is opened.

The lifetime lock protects cooperating processes. Private managed directories
and no-follow identity checks reject observable substitutions; they cannot
prevent a same-account adversary ignoring the lock and replacing POSIX names
between the final check and unlink (POSIX has no portable unlink-by-handle).
"""
from __future__ import annotations

from contextlib import contextmanager
import errno
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import time
import uuid
import warnings

from . import sync_media as safe
from .sync_protocol import SyncError
from .sync_state import _write as _atomic_write

_INDEX = ".reno-frame-cache-v1.json"
_LOCK = ".reno-frame-cache-v1.lock"
_FILES = ".reno-frames-v1"
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_FILE = re.compile(r"[0-9a-f]{32}\.jpg\Z")
MAX_FRAME_BYTES = 16 * 1024 * 1024
MAX_INDEX_BYTES = 8 * 1024 * 1024
MAX_ENTRIES = 10_000
LOCK_WAIT_SECONDS = 30.0


class FrameCacheError(ValueError):
    def __init__(self, code, status=503):
        super().__init__("Frame cache operation could not be completed safely")
        self.code, self.status = code, status


class _InvalidFrame(ValueError):
    def __init__(self, digest=None):
        super().__init__("Frame bytes do not match the registered JPEG")
        self.digest = digest


def _fail(code="FRAME_CACHE_UNSAFE_PATH", status=503):
    raise FrameCacheError(code, status)


@contextmanager
def _errors():
    try:
        yield
    except FrameCacheError:
        raise
    except SyncError as exc:
        _fail("FRAME_CACHE_UNSAFE_PATH" if "UNSAFE" in exc.code or "PATH" in exc.code else "FRAME_CACHE_STORAGE_ERROR")
    except OSError as exc:
        full = exc.errno in (errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC))
        _fail("FRAME_CACHE_STORAGE_FULL" if full else "FRAME_CACHE_STORAGE_ERROR", 507 if full else 503)
    except (ValueError, TypeError, UnicodeError):
        _fail("FRAME_CACHE_CHANGED", 409)


def _root(root):
    value = Path(root)
    if not value.is_absolute() or ".." in value.parts or len(value.parts) < 3:
        _fail("FRAME_CACHE_INVALID_ARGUMENT", 400)
    return Path(os.path.abspath(os.fspath(value)))


def _budget(value):
    if type(value) is not int or not 0 <= value <= 2**63 - 1:
        _fail("FRAME_CACHE_INVALID_ARGUMENT", 400)
    return value


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _stat(info):
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def _identity(directory, name, stream):
    directory.verify()
    entry, handle = directory.entry_stat(name), os.fstat(stream.fileno())
    safe._safe_stat(entry, directory=False)
    # Windows stat(path) and fstat(fd) expose different ctime semantics on
    # supported Python versions. Compare their stable identity/size/mtime;
    # before/after checks on the same handle still include its ctime.
    if entry.st_nlink != 1 or handle.st_nlink != 1 or _stat(entry)[:4] != _stat(handle)[:4]:
        _fail()
    return handle


def _private(info, *, directory=False):
    if os.name != "nt" and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077):
        _fail()


@contextmanager
def _managed(directory, *, create=False):
    if create:
        try:
            directory.entry_stat(_FILES)
        except FileNotFoundError:
            if os.name == "nt":
                os.mkdir(safe._native_path(directory.path / _FILES), mode=0o700)
            else:
                os.mkdir(_FILES, mode=0o700, dir_fd=directory.fd)
    with safe._Directory(directory.path / _FILES, base=directory.path, parent=directory) as files:
        if os.name != "nt":
            _private(os.fstat(files.fd), directory=True)
        yield files


def _open_existing_lock(directory):
    if os.name != "nt":
        return directory.open_file(_LOCK)
    # Preview must not create a missing lock. Share READ|WRITE so its read-only
    # handle can contend on the OS byte lock instead of failing at CreateFile.
    handle = safe._kernel.CreateFileW(safe._native_path(directory.path / _LOCK),
        0x80000000, 3, None, 3, 0x00200000, None)  # READ, OPEN_EXISTING, OPEN_REPARSE_POINT
    if handle == safe.ctypes.c_void_p(-1).value:
        raise safe.ctypes.WinError(safe.ctypes.get_last_error())
    try:
        info = safe._HandleInfo()
        if not safe._kernel.GetFileInformationByHandle(handle, safe.ctypes.byref(info)):
            raise safe.ctypes.WinError(safe.ctypes.get_last_error())
        if info.attributes & (safe._REPARSE | 0x10) or info.links != 1:
            _fail()
        fd = safe.msvcrt.open_osfhandle(handle, os.O_BINARY | os.O_RDONLY)
    except BaseException:
        safe._kernel.CloseHandle(handle)
        raise
    return os.fdopen(fd, "rb")


@contextmanager
def _locked(root, *, create):
    """No lock creation in preview; all cooperating readers/writers serialize."""
    with safe._Directory(root, create=create, base=root) as directory:
        if os.name != "nt":
            info = os.fstat(directory.fd)
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o022:
                _fail()
        try:
            stream = directory.open_file(_LOCK, write=True, lock=True) if create else _open_existing_lock(directory)
        except FileNotFoundError:
            # A cache without an index has never acquired managed ownership.
            try:
                directory.entry_stat(_INDEX)
            except FileNotFoundError:
                yield directory, False
                return
            _fail("FRAME_CACHE_INDEX_INVALID")
        with stream:
            _private(os.fstat(stream.fileno()))
            if os.name != "nt":
                import fcntl
            deadline = time.monotonic() + LOCK_WAIT_SECONDS
            while True:
                try:
                    if os.name == "nt":
                        stream.seek(0)
                        safe.msvcrt.locking(stream.fileno(), safe.msvcrt.LK_NBLCK if create else safe.msvcrt.LK_NBRLCK, 1)
                    else:
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK) and getattr(exc, "winerror", None) != 33:
                        raise
                    if time.monotonic() >= deadline:
                        _fail("FRAME_CACHE_BUSY", 409)
                    time.sleep(0.025)
            try:
                _identity(directory, _LOCK, stream)
                yield directory, True
                _identity(directory, _LOCK, stream)
            finally:
                if os.name == "nt":
                    stream.seek(0)
                    safe.msvcrt.locking(stream.fileno(), safe.msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _read_index(directory):
    try:
        stream = directory.open_file(_INDEX)
    except FileNotFoundError:
        return {"version": 1, "clock": 0, "entries": {}}
    with stream:
        before = _identity(directory, _INDEX, stream)
        _private(before)
        if before.st_size > MAX_INDEX_BYTES:
            _fail("FRAME_CACHE_INDEX_INVALID")
        data = stream.read(MAX_INDEX_BYTES + 1)
        if _stat(before) != _stat(_identity(directory, _INDEX, stream)):
            _fail("FRAME_CACHE_INDEX_INVALID")
    try:
        outer = json.loads(data, object_pairs_hook=_unique)
        if type(outer) is not dict or set(outer) != {"index", "sha256"}:
            raise ValueError
        value = outer["index"]
        if outer["sha256"] != hashlib.sha256(_json(value)).hexdigest():
            raise ValueError
        if (type(value) is not dict or set(value) != {"version", "clock", "entries"}
                or type(value["version"]) is not int or value["version"] != 1
                or type(value["clock"]) is not int or not 0 <= value["clock"] <= 2**63 - 1
                or type(value["entries"]) is not dict or len(value["entries"]) > MAX_ENTRIES):
            raise ValueError
        filenames = set()
        for key, entry in value["entries"].items():
            if (type(key) is not str or not _HEX.fullmatch(key) or type(entry) is not dict
                    or set(entry) != {"file", "sha256", "byte_size", "last_access", "identity"}
                    or type(entry["file"]) is not str or not _FILE.fullmatch(entry["file"])
                    or entry["file"] in filenames or type(entry["sha256"]) is not str or not _HEX.fullmatch(entry["sha256"])
                    or type(entry["byte_size"]) is not int or not 0 < entry["byte_size"] <= MAX_FRAME_BYTES
                    or type(entry["last_access"]) is not int or not 1 <= entry["last_access"] <= value["clock"]
                    or type(entry["identity"]) is not list or len(entry["identity"]) != 2
                    or any(type(part) is not int or part < 0 for part in entry["identity"])):
                raise ValueError
            filenames.add(entry["file"])
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        _fail("FRAME_CACHE_INDEX_INVALID")
    return value


def _save(directory, value):
    body = _json({"index": value, "sha256": hashlib.sha256(_json(value)).hexdigest()})
    if len(body) > MAX_INDEX_BYTES or len(value["entries"]) > MAX_ENTRIES:
        _fail("FRAME_CACHE_INDEX_FULL", 507)
    try:
        directory.entry_stat(_INDEX)
        exists = True
    except FileNotFoundError:
        exists = False
    _atomic_write(directory, _INDEX, body, immutable=not exists)


def _jpeg(data):
    from PIL import Image
    if not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
        raise ValueError("Invalid JPEG")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format != "JPEG" or image.width * image.height > 20_000_000:
                    raise ValueError("Invalid JPEG dimensions")
                image.load()
    except (OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("Invalid JPEG data") from exc


def _read_frame(directory, name, expected=None):
    """Return bytes plus actual identity, validating final bytes, not OS atime."""
    with directory.open_file(name) as stream:
        before = _identity(directory, name, stream)
        if not 0 < before.st_size <= MAX_FRAME_BYTES:
            raise ValueError("Invalid frame size")
        data = stream.read(MAX_FRAME_BYTES + 1)
        after = _identity(directory, name, stream)
        digest = hashlib.sha256(data).hexdigest()
        if len(data) != before.st_size or _stat(before) != _stat(after):
            raise ValueError("Frame changed while reading")
        if expected is not None and (expected["identity"] != [before.st_dev, before.st_ino]
                or expected["byte_size"] != len(data) or expected["sha256"] != digest):
            raise _InvalidFrame(digest)
        try:
            _jpeg(data)
        except ValueError as exc:
            raise _InvalidFrame(digest) from exc
        return data, {"file": name, "sha256": digest, "byte_size": len(data), "identity": [before.st_dev, before.st_ino]}, _stat(after)


def _inspect(directory, value, budget):
    """Pure observation; invalid/missing files are never silently adopted."""
    observed, unknown_files = {}, 0
    entries = value["entries"]
    try:
        with _managed(directory) as files:
            registered = {entry["file"] for entry in entries.values()}
            unknown_files = len(set(files.names()) - registered)
            for key, entry in entries.items():
                try:
                    data, _, actual = _read_frame(files, entry["file"], entry)
                    observed[key] = {"state": "valid", "identity": actual, "sha256": hashlib.sha256(data).hexdigest()}
                except FileNotFoundError:
                    observed[key] = {"state": "missing"}
                except (ValueError, SyncError, OSError) as exc:
                    try:
                        info = files.entry_stat(entry["file"])
                        actual = _stat(info)
                    except OSError:
                        actual = None
                    observed[key] = {"state": "invalid", "identity": actual, "sha256": getattr(exc, "digest", None)}
    except FileNotFoundError:
        observed = {key: {"state": "missing"} for key in entries}
    unknown_root = len(set(directory.names()) - {_INDEX, _LOCK, _FILES})
    managed_bytes = sum(record["identity"][2] if record.get("identity") is not None else entries[key]["byte_size"]
                        for key, record in observed.items() if record["state"] != "missing")
    remaining, candidates = managed_bytes, []
    for key, entry in sorted(entries.items(), key=lambda pair: (pair[1]["last_access"], pair[0])):
        if remaining <= budget:
            break
        if observed[key]["state"] == "valid":
            candidates.append({"key": key, "storage_key": _FILES + "/" + entry["file"],
                               "byte_size": entry["byte_size"], "last_access": entry["last_access"]})
            remaining -= entry["byte_size"]
    digest = hashlib.sha256(_json({"root": str(directory.path), "budget_bytes": budget,
        "index": value, "observed": observed, "unknown_root": unknown_root, "unknown_files": unknown_files})).hexdigest()
    return {"digest": digest, "budget_bytes": budget, "managed_bytes": managed_bytes,
            "managed_entries": len(entries), "candidate_bytes": sum(row["byte_size"] for row in candidates),
            "candidates": candidates, "preserved_unknown": {"root_entries": unknown_root, "managed_directory_entries": unknown_files},
            "preserved_invalid": sum(row["state"] == "invalid" for row in observed.values()),
            "missing_entries": sum(row["state"] == "missing" for row in observed.values()),
            "remaining_over_budget_bytes": max(0, remaining - budget)}, observed


def _delete_registered(directory, value, key):
    """Retire under a random name before deletion; failures remain retryable.

    The index is updated before unlink. A crash in the rename/index gap can
    leave an unknown orphan, which is preserved rather than guessed away.
    """
    entry = value["entries"][key]
    with _managed(directory) as files:
        _read_frame(files, entry["file"], entry)
        original, retired = entry["file"], uuid.uuid4().hex + ".jpg"
        safe._rename_new(files, original, files, retired)
        try:
            _read_frame(files, retired, {**entry, "file": retired})
        except BaseException:
            # Do not delete anything whose identity changed at the rename.
            try:
                safe._rename_new(files, retired, files, original)
            except OSError:
                pass
            raise
        entry["file"] = retired
        _save(directory, value)
        _, _, observed = _read_frame(files, retired, entry)
        current = files.entry_stat(retired)
        safe._safe_stat(current, directory=False)
        if current.st_nlink != 1 or _stat(current)[:4] != observed[:4]:
            _fail("FRAME_CACHE_CHANGED", 409)
        files.unlink(retired)
        files.verify()
        if os.name != "nt":
            os.fsync(files.fd)
        del value["entries"][key]
        _save(directory, value)


def _evict(directory, value, plan, observed):
    deleted_entries, deleted_bytes = 0, 0
    missing = [key for key, record in observed.items() if record["state"] == "missing"]
    if missing:
        for key in missing:
            value["entries"].pop(key, None)
        _save(directory, value)
    for candidate in plan["candidates"]:
        _delete_registered(directory, value, candidate["key"])
        deleted_entries += 1
        deleted_bytes += candidate["byte_size"]
    return deleted_entries, deleted_bytes


def _request_evict(directory, value, budget, limit):
    """Ordinary hits use index sizes; only eviction candidates are decoded."""
    total = sum(entry["byte_size"] for entry in value["entries"].values())
    if total <= budget and len(value["entries"]) <= limit:
        return True
    for key, entry in sorted(list(value["entries"].items()), key=lambda pair: (pair[1]["last_access"], pair[0])):
        if total <= budget and len(value["entries"]) <= limit:
            return True
        try:
            _delete_registered(directory, value, key)
        except FileNotFoundError:
            value["entries"].pop(key, None)
            _save(directory, value)
        except (FrameCacheError, SyncError):
            raise
        except ValueError:
            # Known but no longer proven files cannot be deleted for space.
            continue
        total -= entry["byte_size"]
    return total <= budget and len(value["entries"]) <= limit


def _cleanup_attempt(files, name, owned):
    """Remove only this live attempt's exact object; never guess after a crash."""
    try:
        current = files.entry_stat(name)
    except FileNotFoundError:
        return
    safe._safe_stat(current, directory=False)
    if current.st_nlink != 1 or [current.st_dev, current.st_ino] != owned:
        _fail()
    retired = uuid.uuid4().hex + ".jpg"
    safe._rename_new(files, name, files, retired)
    current = files.entry_stat(retired)
    safe._safe_stat(current, directory=False)
    if current.st_nlink != 1 or [current.st_dev, current.st_ino] != owned:
        try:
            safe._rename_new(files, retired, files, name)
        except OSError:
            pass
        _fail()
    files.unlink(retired)
    files.verify()


def _extract(source, ms, files, extractor):
    name = uuid.uuid4().hex + ".jpg"
    with files.open_file(name, write=True, create=True) as stream:
        info = os.fstat(stream.fileno())
        owned = [info.st_dev, info.st_ino]
    try:
        with safe._Directory(source.parent, base=source.parent) as origin:
            with origin.open_file(source.name) as handle:
                before = _identity(origin, source.name, handle)
                try:
                    accepted = extractor(source, ms, files.path / name)
                except Exception:
                    _fail("FRAME_CACHE_EXTRACTION_FAILED")
                if not accepted or _stat(before) != _stat(_identity(origin, source.name, handle)):
                    _fail("FRAME_CACHE_EXTRACTION_FAILED")
        try:
            data, entry, _ = _read_frame(files, name)
        except (ValueError, FileNotFoundError):
            _fail("FRAME_CACHE_INVALID_JPEG")
        if entry["identity"] != owned:
            _fail()
        return data, entry
    except BaseException:
        _cleanup_attempt(files, name, owned)
        raise


def preview(*, root, budget_bytes):
    root, budget = _root(root), _budget(budget_bytes)
    with _errors():
        try:
            with _locked(root, create=False) as (directory, locked):
                value = _read_index(directory) if locked else {"version": 1, "clock": 0, "entries": {}}
                return _inspect(directory, value, budget)[0]
        except FileNotFoundError:
            # Match a subsequently created empty directory's observation while
            # doing no mkdir, lock-file write, or index initialization here.
            value = {"version": 1, "clock": 0, "entries": {}}
            digest = hashlib.sha256(_json({"root": str(root), "budget_bytes": budget,
                "index": value, "observed": {}, "unknown_root": 0, "unknown_files": 0})).hexdigest()
            return {"digest": digest, "budget_bytes": budget, "managed_bytes": 0, "managed_entries": 0,
                    "candidate_bytes": 0, "candidates": [], "preserved_unknown": {"root_entries": 0, "managed_directory_entries": 0},
                    "preserved_invalid": 0, "missing_entries": 0, "remaining_over_budget_bytes": 0}


def prune(*, root, budget_bytes, expected_digest=None):
    root, budget = _root(root), _budget(budget_bytes)
    if type(expected_digest) is not str or not _HEX.fullmatch(expected_digest):
        _fail("FRAME_CACHE_EXPECTATION_REQUIRED", 400)
    with _errors(), _locked(root, create=True) as (directory, _):
        value = _read_index(directory)
        plan, observed = _inspect(directory, value, budget)
        if plan["digest"] != expected_digest:
            _fail("FRAME_CACHE_CHANGED", 409)
        count, size = _evict(directory, value, plan, observed)
        return {**_inspect(directory, value, budget)[0], "deleted_entries": count, "deleted_bytes": size}


def get_frame(video_id, content_sha, ms, source_path, *, root, budget_bytes, extractor) -> bytes:
    root, budget = _root(root), _budget(budget_bytes)
    if (type(video_id) is not str or not 0 < len(video_id) <= 512
            or content_sha is not None and (type(content_sha) is not str or len(content_sha) > 256)
            or type(ms) is not int or not 0 <= ms <= 2**63 - 1
            or not callable(extractor)):
        _fail("FRAME_CACHE_INVALID_ARGUMENT", 400)
    source = Path(os.path.abspath(os.fspath(source_path)))
    if source.is_relative_to(root):
        _fail("FRAME_CACHE_INVALID_ARGUMENT", 400)
    key = hashlib.sha256(_json({"version": 1, "video_id": video_id, "content_sha": content_sha, "ms": ms})).hexdigest()
    with _errors(), _locked(root, create=True) as (directory, _):
        value = _read_index(directory)
        if value["clock"] == 2**63 - 1:
            _fail("FRAME_CACHE_INDEX_FULL", 507)
        cacheable = bool(content_sha)
        data, entry = None, value["entries"].get(key) if cacheable else None
        with _managed(directory, create=True) as files:
            if entry is not None:
                try:
                    data, _, _ = _read_frame(files, entry["file"], entry)
                except (FrameCacheError, SyncError):
                    raise
                except (FileNotFoundError, ValueError):
                    # Damaged files keep their bytes as unregistered evidence.
                    # A new random file cannot overwrite an unknown old object.
                    data = None
                    value["entries"].pop(key, None)
                    _save(directory, value)
            if data is None:
                data, entry = _extract(source, ms, files, extractor)
                try:
                    if not cacheable or entry["byte_size"] > budget:
                        _request_evict(directory, value, budget, MAX_ENTRIES)
                        return data
                    if not _request_evict(directory, value, budget - entry["byte_size"], MAX_ENTRIES - 1):
                        return data
                    value["clock"] += 1
                    value["entries"][key] = {**entry, "last_access": value["clock"]}
                    _save(directory, value)
                finally:
                    # A failed atomic index write may already have committed.
                    # Read its actual result before removing a newly made JPEG.
                    actual = _read_index(directory)
                    if not any(row["file"] == entry["file"] for row in actual["entries"].values()):
                        _cleanup_attempt(files, entry["file"], entry["identity"])
            else:
                value["clock"] += 1
                value["entries"][key] = {**entry, "last_access": value["clock"]}
                _save(directory, value)
                _request_evict(directory, value, budget, MAX_ENTRIES)
        # HTTP returns these bytes. Eviction never races a later path open.
        return data
