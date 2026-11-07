"""Checked, exclusive outer lifecycle for the replica family.

This module opens only existing lifecycle facilities.  It never initializes a
profile or opens SQLite.  A successful legacy context return is insufficient:
every native resource acquired here must have a checked, single close before
the original owner may consume the detached, single-use close observation.

The Path shape preflight below follows the inspected CPython 3.12.10 pathlib
representation.  Unknown representations fail closed.  It is a pure input
gate, not evidence of native support on another Python or operating system.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat
import threading
import time
from types import MethodType
import uuid
import weakref

from . import storage_lifecycle as lifecycle
from . import sync_media as safe


_MAX_RESOURCES = 1041
_MAX_COUNT = 2**63 - 1
_ACQUIRE_NS = 30_000_000_000
_PATH_TYPE = type(Path())
_PROFILE_TYPE = lifecycle.StorageProfile
_GUARD_TYPE = lifecycle.StorageGuard
_LOCK_TYPE = lifecycle._FileLock
_OWNER = lifecycle._owner
_CURRENT = lifecycle._CURRENT
_ROOTS = lifecycle._roots
_KEY = lifecycle._key
_DATABASE = lifecycle._database
_PROFILE_IDENTITY = _PROFILE_TYPE.identity
_PROFILE_ROOTS = _PROFILE_TYPE.roots
_GUARD_VERIFY = _GUARD_TYPE.verify
_GUARD_ALLOWS = _GUARD_TYPE.allows
_GUARD_INIT = _GUARD_TYPE.__init__
_LOCK_INIT = _LOCK_TYPE.__init__
_LOCK_VERIFY = _LOCK_TYPE.verify
_LOCK_STAT = _LOCK_TYPE.verify_stat
_LOCK_ENTRY = _LOCK_TYPE._verify_entry_stat
_LOCK_ATTEMPT = _LOCK_TYPE.attempt
_LOCK_RELEASE = _LOCK_TYPE.release
_MONOTONIC_NS = time.monotonic_ns
_SLEEP = time.sleep
_OS_OPEN = os.open
_OS_STAT = os.stat
_OS_FSTAT = os.fstat
_OS_READ = os.read
_OS_LSEEK = os.lseek
_OS_CLOSE = os.close
_OS_GET_INHERITABLE = os.get_inheritable
_DICT_SET = dict.__setitem__
_FIELDS = ("database", "media", "reports", "checkpoints")
_TAG = object()
_REGISTRY_LOCK = threading.RLock()
_LIVE = {}
_GUARDS = {}
_VIEWS = {}
_CLOSED = weakref.WeakKeyDictionary()
_TOMBSTONES = []
_DEBTS = []
_POISONED = False
_ERROR_CODES = frozenset("AUTHORITY_REPLICA_GROUP1_" + suffix for suffix in (
    "TYPE_INVALID", "PROFILES_INVALID", "PATH_INVALID", "OWNER_MISMATCH",
    "REENTRANT", "CURRENT_PRESENT", "GUARD_REQUIRED", "GUARD_CHANGED", "CLOSED",
    "CONSUMED", "CLOCK_INVALID", "DEADLINE", "RESOURCE_LIMIT", "COUNTER_LIMIT",
    "ACQUIRE_FAILED", "IDENTITY_CHANGED", "UNSAFE_PATH", "LOCK_INVALID",
    "LOCK_ALIAS", "UNLOCK_FAILED", "CLOSE_FAILED", "TRANSFER_UNKNOWN", "UNSETTLED",
))

if os.name == "nt":
    ctypes, wintypes = safe.ctypes, safe.wintypes
    _WIN_CREATE_FILEW = safe._kernel.CreateFileW
    _WIN_GET_FILE_INFORMATION = safe._kernel.GetFileInformationByHandle
    _WIN_CLOSE_HANDLE = safe._kernel.CloseHandle
    _WIN_DUPLICATE_HANDLE = safe._kernel.DuplicateHandle
    _WIN_DUPLICATE_HANDLE.argtypes = [
        wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
        ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD,
        wintypes.BOOL, wintypes.DWORD,
    ]
    _WIN_DUPLICATE_HANDLE.restype = wintypes.BOOL
    _WIN_GET_CURRENT_PROCESS = safe._kernel.GetCurrentProcess
    _WIN_GET_CURRENT_PROCESS.argtypes = []
    _WIN_GET_CURRENT_PROCESS.restype = wintypes.HANDLE
    _WIN_UNLOCK_FILE_EX = lifecycle._kernel.UnlockFileEx
    _CRT_OPEN_OSFHANDLE = safe.msvcrt.open_osfhandle
    _CRT_GET_OSFHANDLE = safe.msvcrt.get_osfhandle
    _OVERLAPPED = lifecycle._Overlapped
    _HANDLE_INFO = safe._HandleInfo
    _INVALID_HANDLE = ctypes.c_void_p(-1).value
else:
    import fcntl
    _FLOCK = fcntl.flock


class AuthorityReplicaGroupError(ValueError):
    def __init__(self, suffix):
        super().__init__("Replica outer lifecycle could not be established safely")
        self.code = "AUTHORITY_REPLICA_GROUP1_" + suffix
        self.status = 409


def _error(suffix):
    return AuthorityReplicaGroupError(suffix)


def _fail(suffix):
    raise _error(suffix)


def _owner_now():
    owner = _OWNER()
    if (type(owner) is not tuple or len(owner) != 3
            or type(owner[0]) is not int or owner[0] < 0
            or type(owner[1]) is not int or owner[1] < 0):
        _fail("OWNER_MISMATCH")
    return owner


def _same_owner(left, right):
    return (type(right) is tuple and len(right) == 3
            and type(right[0]) is int and type(right[1]) is int
            and left[0] == right[0] and left[1] == right[1]
            and left[2] is right[2])


def _weak_task(task):
    return None if task is None else weakref.ref(task)


def _closed_owner_matches(closed, owner):
    task_ref = closed.task
    return (closed.pid == owner[0] and closed.thread == owner[1]
            and (owner[2] is None if task_ref is None
                 else task_ref() is not None and task_ref() is owner[2]))


class _GroupToken:
    __slots__ = ("__weakref__",)

    def __init__(self, tag):
        if tag is not _TAG:
            _fail("GUARD_REQUIRED")


class _Holder:
    __slots__ = ("serial", "kind", "raw", "state", "root_key", "path_key",
                 "identity", "identity_state")

    def __init__(self, serial, kind, root_key, path_key):
        self.serial, self.kind = serial, kind
        self.raw, self.state = None, "pending"
        self.root_key, self.path_key = root_key, path_key
        self.identity, self.identity_state = None, "not_established"


class _LockBinding:
    __slots__ = ("lock", "dictionary", "directory", "stream", "holder",
                 "identity", "overlapped", "owned", "released")

    def __init__(self, lock, directory, stream, holder):
        self.lock, self.dictionary = lock, object.__getattribute__(lock, "__dict__")
        self.directory, self.stream, self.holder = directory, stream, holder
        self.identity, self.overlapped = lock.identity, lock.overlapped
        self.owned, self.released = False, False


class _RootRecord:
    __slots__ = ("path", "key", "holders", "directory", "lock_holder", "binding")

    def __init__(self, path):
        self.key = _KEY(path)
        self.path = _PATH_TYPE(self.key)
        self.holders, self.directory = [], None
        self.lock_holder, self.binding = None, None


class _GroupRecord:
    __slots__ = ("__weakref__", "token", "owner", "profiles", "projection",
                 "roots", "root_keys", "root_hash", "scope_id", "phase",
                 "callback", "callback_active", "first", "start", "last",
                 "acquisition_complete",
                 "holders", "bindings", "views", "guard", "guard_dict", "guard_fields",
                 "current_token", "acquired", "normal_closed", "current",
                 "peak", "pending", "unknown_acquires", "unknown_closes")

    def __init__(self, token, owner, profiles, projection, roots, callback):
        self.token, self.owner = token, owner
        self.profiles, self.projection = profiles, projection
        self.roots = tuple(_RootRecord(path) for path in roots)
        self.root_keys = tuple(sorted((root.key for root in self.roots), key=lambda s: s.encode("utf-8")))
        raw = json.dumps(list(self.root_keys), ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
        if len(raw) > 1024 * 1024:
            _fail("PATH_INVALID")
        self.root_hash = hashlib.sha256(raw).hexdigest()
        self.scope_id = str(uuid.uuid4())
        self.phase, self.callback, self.callback_active = "opening", callback, False
        self.first, self.start, self.last = None, None, None
        self.acquisition_complete = False
        self.holders, self.bindings = [], []
        self.views = []
        self.guard, self.guard_dict, self.guard_fields, self.current_token = None, None, None, None
        self.acquired = self.normal_closed = self.current = self.peak = self.pending = 0
        self.unknown_acquires = self.unknown_closes = 0

    def remember(self, error):
        if self.first is None:
            self.first = error
        elif error is not self.first:
            # Bypass custom exception methods and never inspect arbitrary text.
            try:
                BaseException.add_note(self.first, "Additional replica outer lifecycle failure")
            except BaseException:
                pass
        return self.first


class _GuardRegistration:
    __slots__ = ("guard", "record", "state")

    def __init__(self, guard, record):
        self.guard, self.record = weakref.ref(guard), weakref.ref(record)
        self.state = "live"


class _ViewRegistration:
    __slots__ = ("view", "record", "resource", "kind", "dictionary", "display_path")

    def __init__(self, view, record, resource, kind):
        self.view, self.record = weakref.ref(view), weakref.ref(record)
        self.resource, self.kind = resource, kind
        self.dictionary = object.__getattribute__(view, "__dict__")
        self.display_path = view.path if kind == "directory" else None


def _register_view(record, view, resource, kind):
    registration = _ViewRegistration(view, record, resource, kind)
    record.views.append((view, registration))
    _VIEWS[id(view)] = registration


def _view_pins(view, record, registration):
    expected = _DirectoryView if registration.kind == "directory" else _FdView
    if (type(view) is not expected or registration.view() is not view
            or registration.record() is not record or _VIEWS.get(id(view)) is not registration
            or object.__getattribute__(view, "__dict__") is not registration.dictionary):
        _fail("GUARD_CHANGED")
    dictionary = registration.dictionary
    if registration.kind == "directory":
        # Check key types before lookup: a forged colliding key must not run eq.
        if (len(dictionary) != 1 or any(type(key) is not str or key != "path" for key in dictionary)
                or dictionary["path"] is not registration.display_path
                or _path_projection(dictionary["path"]) != registration.resource.key
                or not _method(view, "verify", _DIRECTORY_VERIFY)
                or not _method(view, "entry_stat", _DIRECTORY_ENTRY)):
            _fail("GUARD_CHANGED")
    elif dictionary or not _method(view, "fileno", _FD_FILENO):
        _fail("GUARD_CHANGED")


def _resolve_view(view, kind):
    registration = _VIEWS.get(id(view))
    if (registration is None or registration.view() is not view
            or registration.kind != kind):
        _fail("GUARD_REQUIRED")
    record = registration.record()
    if record is None:
        _fail("CLOSED")
    _registered(record)
    try:
        _view_pins(view, record, registration)
        _pins(record)
    except BaseException as exc:
        raise record.remember(exc)
    return record, registration.resource


class _ClosedRecord:
    __slots__ = ("pid", "thread", "task", "guard", "observation", "consumed")

    def __init__(self, record, observation):
        self.pid, self.thread = record.owner[:2]
        self.task, self.guard = _weak_task(record.owner[2]), weakref.ref(record.guard)
        self.observation, self.consumed = observation, False


def _method(instance, name, original):
    method = object.__getattribute__(instance, name)
    return (type(method) is MethodType and method.__self__ is instance
            and method.__func__ is original)


def _path_projection(path):
    if type(path) is not _PATH_TYPE:
        _fail("PATH_INVALID")
    _path_shape(path)
    value = str(path)
    if type(value) is not str:
        _fail("PATH_INVALID")
    fresh = _PATH_TYPE(value)
    parts = path.parts
    if type(parts) is not tuple or any(type(piece) is not str for piece in parts):
        _fail("PATH_INVALID")
    if tuple(os.path.normcase(piece) for piece in parts) != tuple(os.path.normcase(piece) for piece in fresh.parts):
        _fail("PATH_INVALID")
    name, parent = path.name, path.parent
    if (type(name) is not str or type(parent) is not _PATH_TYPE
            or os.path.normcase(name) != os.path.normcase(fresh.name)
            or _KEY(parent) != _KEY(fresh.parent)):
        _fail("PATH_INVALID")
    # CPython's join consumes the original raw segments, independently of a
    # potentially stale _str/parts cache.  Both operands have exact safe types.
    child = ".reno-replica-path-shape-v1"
    if _KEY(path / child) != _KEY(fresh / child):
        _fail("PATH_INVALID")
    if (not path.is_absolute() or not path.anchor or ".." in path.parts
            or value.startswith(("\\\\", "//")) or "://" in value):
        _fail("PATH_INVALID")
    pieces = path.parts[1:]
    if len(pieces) > 128:
        _fail("PATH_INVALID")
    for piece in pieces:
        if (not piece or piece in (".", "..")
                or any(c in piece for c in ("/", "\\", ":"))
                or any(ord(c) < 32 or ord(c) == 127 for c in piece)):
            _fail("PATH_INVALID")
        if os.name == "nt" and (piece.endswith((".", " ")) or _PATH_TYPE(piece).is_reserved()):
            _fail("PATH_INVALID")
    try:
        value.encode("utf-8")
    except UnicodeError:
        _fail("PATH_INVALID")
    return _KEY(path)


def _path_shape(path):
    # Read slots themselves, not the _tail property that would load/interpret
    # raw segments before their element types have been checked.
    try:
        raw = object.__getattribute__(path, "_raw_paths")
    except AttributeError:
        _fail("PATH_INVALID")
    if type(raw) is not list or any(type(piece) is not str for piece in raw):
        _fail("PATH_INVALID")
    for name in ("_str", "_drv", "_root", "_tail_cached"):
        try:
            value = object.__getattribute__(path, name)
        except AttributeError:
            continue
        if name == "_tail_cached":
            if type(value) is not list or any(type(piece) is not str for piece in value):
                _fail("PATH_INVALID")
        elif type(value) is not str:
            _fail("PATH_INVALID")


def _profile_projection(profile):
    if (type(profile) is not _PROFILE_TYPE
            or _PROFILE_TYPE.identity is not _PROFILE_IDENTITY
            or _PROFILE_TYPE.roots is not _PROFILE_ROOTS):
        _fail("PROFILES_INVALID")
    dictionary = object.__getattribute__(profile, "__dict__")
    if type(dictionary) is not dict or any(type(key) is not str for key in dictionary):
        _fail("PROFILES_INVALID")
    return tuple(_path_projection(object.__getattribute__(profile, name)) for name in _FIELDS)


def _parameters(profiles, callback):
    if type(profiles) is not tuple or not 1 <= len(profiles) <= 2:
        _fail("PROFILES_INVALID")
    if callback is not None and not callable(callback):
        _fail("TYPE_INVALID")
    selected, projection = [], []
    for profile in profiles:
        identity = _profile_projection(profile)
        if identity not in projection:
            selected.append(profile)
            projection.append(identity)
    result = tuple(selected)
    roots = _ROOTS(result)
    if type(roots) is not tuple or not 1 <= len(roots) <= 8:
        _fail("PROFILES_INVALID")
    for root in roots:
        _path_projection(root)
    canonical_roots = set()
    for identity in projection:
        canonical_roots.add(_KEY(_PATH_TYPE(identity[0]).parent))
        canonical_roots.update(identity[1:])
    if tuple(_path_projection(root) for root in roots) != tuple(sorted(canonical_roots)):
        _fail("PROFILES_INVALID")
    return result, tuple(projection), roots


def _registered(record):
    if (type(record) is not _GroupRecord or _LIVE.get(id(record.token)) is not record
            or type(record.token) is not _GroupToken):
        _fail("GUARD_REQUIRED")
    if not _same_owner(record.owner, _owner_now()):
        _fail("OWNER_MISMATCH")


def _pins(record, *, held=False):
    """Pure identity checks.  In particular this never calls guard.verify."""
    _registered(record)
    if record.phase not in ("opening", "acquiring", "held") or (held and record.phase != "held"):
        _fail("CLOSED")
    if record.first is not None:
        raise record.first
    if (_GUARD_TYPE.__init__ is not _GUARD_INIT or _LOCK_TYPE.__init__ is not _LOCK_INIT
            or _GUARD_TYPE.verify is not _GUARD_VERIFY or _GUARD_TYPE.allows is not _GUARD_ALLOWS
            or _LOCK_TYPE.verify is not _LOCK_VERIFY or _LOCK_TYPE.verify_stat is not _LOCK_STAT
            or _LOCK_TYPE._verify_entry_stat is not _LOCK_ENTRY
            or _LOCK_TYPE.attempt is not _LOCK_ATTEMPT or _LOCK_TYPE.release is not _LOCK_RELEASE):
        _fail("GUARD_CHANGED")
    if tuple(_profile_projection(profile) for profile in record.profiles) != record.projection:
        _fail("GUARD_CHANGED")
    for view, registration in record.views:
        _view_pins(view, record, registration)
    for binding in record.bindings:
        lock, stream = binding.lock, binding.stream
        if (type(lock) is not _LOCK_TYPE
                or object.__getattribute__(lock, "__dict__") is not binding.dictionary
                or any(type(key) is not str for key in binding.dictionary)
                or lock.directory is not binding.directory or lock.stream is not stream
                or lock.identity is not binding.identity or type(lock.identity) is not tuple
                or lock.overlapped is not binding.overlapped
                or lock.locked is not binding.owned
                or not _method(lock, "verify", _LOCK_VERIFY)
                or not _method(lock, "verify_stat", _LOCK_STAT)
                or not _method(lock, "_verify_entry_stat", _LOCK_ENTRY)
                or not _method(lock, "attempt", _LOCK_ATTEMPT)
                or not _method(lock, "release", _LOCK_RELEASE)
                or type(stream) is not _FdView
                or not _method(stream, "fileno", _FD_FILENO)):
            _fail("GUARD_CHANGED")
        if os.name == "nt":
            overlap = binding.overlapped
            if (type(overlap) is not _OVERLAPPED or overlap.Offset != 0
                    or overlap.OffsetHigh != 0 or overlap.hEvent is not None):
                _fail("GUARD_CHANGED")
    guard = record.guard
    if guard is None:
        if _CURRENT.get() is not None:
            _fail("CURRENT_PRESENT")
    else:
        fields = record.guard_fields
        if (type(guard) is not _GUARD_TYPE
                or object.__getattribute__(guard, "__dict__") is not record.guard_dict
                or any(type(key) is not str for key in record.guard_dict)
                or guard._profiles is not fields[0] or guard._profile_ids is not fields[1]
                or guard._locks is not fields[2] or guard._owner is not fields[3]
                or guard._exclusive is not True or guard._active is not True
                or type(guard._profiles) is not tuple or type(guard._locks) is not tuple
                or type(guard._profile_ids) is not frozenset
                or len(guard._profiles) != len(record.profiles)
                or any(a is not b for a, b in zip(guard._profiles, record.profiles))
                or len(guard._locks) != len(record.bindings)
                or any(a is not b.lock for a, b in zip(guard._locks, record.bindings))
                or not _same_owner(record.owner, guard._owner)
                or not _method(guard, "verify", _GUARD_VERIFY)
                or not _method(guard, "allows", _GUARD_ALLOWS)
                or _CURRENT.get() is not guard):
            _fail("GUARD_CHANGED")


def _check_pins(record, *, held=False):
    # A foreign context must not poison the legitimate owner's live group.
    _registered(record)
    try:
        _pins(record, held=held)
    except BaseException as exc:
        raise record.remember(exc)


def require_registered_guard_original(guard):
    """Fixed legacy seam: recognize by weak object identity before dispatch."""
    with _REGISTRY_LOCK:
        registration = _GUARDS.get(id(guard))
        if registration is None or registration.guard() is not guard:
            return
        if registration.state != "live":
            _fail("CLOSED")
        record = registration.record()
        if record is None or record.guard is not guard:
            _fail("GUARD_REQUIRED")
    _check_pins(record, held=True)


def _reject_reentry_original(owner):
    """Routing observer only; ordinary held-body locked work remains valid."""
    with _REGISTRY_LOCK:
        for record in tuple(_LIVE.values()):
            if (_same_owner(record.owner, owner)
                    and (record.phase in ("opening", "acquiring", "closing", "failed_cleanup")
                         or record.callback_active)):
                raise record.remember(_error("REENTRANT"))


def _clock(record):
    value = _MONOTONIC_NS()
    if type(value) is not int or value < 0 or (record.last is not None and value < record.last):
        raise record.remember(_error("CLOCK_INVALID"))
    if record.start is None:
        record.start = value
    record.last = value
    if value - record.start >= _ACQUIRE_NS:
        raise record.remember(_error("DEADLINE"))
    return value


def _boundary(record, *, callback=False):
    _check_pins(record)
    acquiring = not record.acquisition_complete
    if acquiring:
        _clock(record)
    if (callback and record.phase in ("opening", "acquiring")
            and record.callback is not None):
        record.callback_active = True
        try:
            record.callback()
        except BaseException as exc:
            record.remember(exc)
        finally:
            record.callback_active = False
        _check_pins(record)
        _clock(record)


class _DirectoryView:
    """Non-owning adapter: the actual original lock always takes its fallback."""
    def __init__(self, path):
        self.path = path

    def verify(self):
        record, root = _resolve_view(self, "directory")
        try:
            for index, holder in enumerate(root.holders):
                _boundary(record)
                _verify_directory(record, root, index, holder)
                _boundary(record)
        except BaseException as exc:
            raise record.remember(exc)
        return self

    def entry_stat(self, name):
        record, root = _resolve_view(self, "directory")
        missing = None
        try:
            if type(name) is not str:
                _fail("PATH_INVALID")
            safe._entry_name(name)
            self.verify()
            _boundary(record)
            try:
                if os.name == "nt":
                    result = _OS_STAT(safe._native_path(_PATH_TYPE(root.key) / name), follow_symlinks=False)
                else:
                    result = _OS_STAT(name, dir_fd=root.holders[-1].raw, follow_symlinks=False)
            except FileNotFoundError as exc:
                # Original _database explicitly permits a missing business DB.
                # This narrow, actual stat absence is a valid observation; a
                # failed directory check or missing lifecycle lock is not.
                if (name == lifecycle.LOCK_NAME
                        or not any(_KEY(profile.database.parent) == root.key and profile.database.name == name
                                   for profile in record.profiles)):
                    raise
                missing = exc
            _boundary(record)
        except BaseException as exc:
            raise record.remember(exc)
        if missing is not None:
            raise missing
        return result


class _FdView:
    """A fileno-only borrowed view; it deliberately has no close method."""
    def fileno(self):
        _record, holder = _resolve_view(self, "fd")
        if holder.state != "owned" or holder.kind != "fd" or type(holder.raw) is not int:
            _fail("GUARD_CHANGED")
        return holder.raw


_DIRECTORY_VERIFY = _DirectoryView.verify
_DIRECTORY_ENTRY = _DirectoryView.entry_stat
_FD_FILENO = _FdView.fileno


def _reserve(record, kind, root_key, path_key):
    _boundary(record)
    if record.current + record.pending >= _MAX_RESOURCES:
        raise record.remember(_error("RESOURCE_LIMIT"))
    if record.acquired >= _MAX_COUNT or len(record.holders) >= _MAX_COUNT:
        raise record.remember(_error("COUNTER_LIMIT"))
    holder = _Holder(len(record.holders) + 1, kind, root_key, path_key)
    record.holders.append(holder)
    record.pending += 1
    return holder


def _owned(record, holder, raw):
    # Record a native return before any subsequent validation or cancellation.
    holder.raw, holder.state = raw, "owned"
    record.pending -= 1
    record.current += 1
    record.acquired += 1
    record.peak = max(record.peak, record.current)
    if record.acquired > _MAX_COUNT:
        raise record.remember(_error("COUNTER_LIMIT"))


def _attach_owned_return(record, holder, raw):
    # Keep the allocator's confirmed local return under its unique closer even
    # if counter bookkeeping fails.  Do not revisit the allocator failure path.
    holder.raw, holder.state = raw, "owned"
    try:
        _owned(record, holder, raw)
    except BaseException as exc:
        raise record.remember(exc)


def _no_resource(record, holder):
    holder.state = "empty"
    record.pending -= 1


def _unknown_acquire(record, holder, raw=None):
    global _POISONED
    _POISONED = True
    holder.raw, holder.state = raw, "unknown"
    record.unknown_acquires += 1


def _close(record, holder):
    global _POISONED
    if holder.state != "owned":
        return
    holder.state = "close_attempted"
    try:
        if record.normal_closed >= _MAX_COUNT:
            record.remember(_error("COUNTER_LIMIT"))
        if holder.kind == "handle":
            result = _WIN_CLOSE_HANDLE(holder.raw)
            if type(result) is not int or result == 0:
                _fail("CLOSE_FAILED")
        else:
            if _OS_CLOSE(holder.raw) is not None:
                _fail("CLOSE_FAILED")
    except BaseException as exc:
        _POISONED = True
        holder.state = "close_unknown"
        record.unknown_closes += 1
        record.remember(exc)
    else:
        holder.state = "closed"
        record.current -= 1
        record.normal_closed += 1


def _open_native(record, root, path, *, directory, parent=None):
    holder = _reserve(record, "handle" if os.name == "nt" else "fd", root.key, _KEY(path))
    # Include it in the fixed root cleanup order before calling an allocator.
    if directory:
        root.holders.append(holder)
    else:
        root.lock_holder = holder
    if os.name == "nt":
        try:
            raw = _WIN_CREATE_FILEW(safe._native_path(path), 1 if directory else 0x80000000,
                                   3, None, 3, 0x00200000 | (0x02000000 if directory else 0), None)
        except BaseException as exc:
            if issubclass(type(exc), OSError):
                _no_resource(record, holder)
            else:
                _unknown_acquire(record, holder)
            raise record.remember(exc)
        if type(raw) is int and raw == _INVALID_HANDLE:
            _no_resource(record, holder)
            raise ctypes.WinError(ctypes.get_last_error())
        if type(raw) is not int or raw <= 0:
            _unknown_acquire(record, holder, raw if type(raw) is int else None)
            _fail("ACQUIRE_FAILED")
    else:
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
        if directory:
            flags |= os.O_DIRECTORY
        try:
            raw = _OS_OPEN(str(path) if parent is None else path.name, flags,
                           **({} if parent is None else {"dir_fd": parent.raw}))
        except BaseException as exc:
            if issubclass(type(exc), OSError):
                _no_resource(record, holder)
            else:
                _unknown_acquire(record, holder)
            raise record.remember(exc)
        if type(raw) is not int or raw < 0:
            _unknown_acquire(record, holder, raw if type(raw) is int else None)
            _fail("ACQUIRE_FAILED")
    _attach_owned_return(record, holder, raw)
    _boundary(record)
    if os.name == "nt":
        info = _HANDLE_INFO()
        result = _WIN_GET_FILE_INFORMATION(holder.raw, ctypes.byref(info))
        error = ctypes.get_last_error()
        if not result:
            raise ctypes.WinError(error)
        if (info.attributes & safe._REPARSE
                or bool(info.attributes & 0x10) is not directory
                or (not directory and info.links != 1)):
            _fail("UNSAFE_PATH")
        if not directory:
            _transfer_fd(record, holder)
    elif _OS_GET_INHERITABLE(holder.raw) is not False:
        _fail("UNSAFE_PATH")
    _boundary(record)
    return holder


def _transfer_fd(record, holder):
    global _POISONED
    original = holder.raw
    try:
        descriptor = _CRT_OPEN_OSFHANDLE(original, os.O_BINARY | os.O_RDONLY)
    except OSError:
        # The fixed CRT failure reports -1 without transferring this HANDLE.
        raise
    except BaseException:
        _POISONED = True
        holder.state = "transfer_unknown"
        record.unknown_acquires += 1
        raise
    if type(descriptor) is not int or descriptor < 0:
        _POISONED = True
        holder.state = "transfer_unknown"
        record.unknown_acquires += 1
        _fail("TRANSFER_UNKNOWN")
    # Same resource, new unique closer; never retain a second HANDLE closer.
    holder.raw, holder.kind = descriptor, "fd"
    if _OS_GET_INHERITABLE(descriptor) is not False:
        _fail("UNSAFE_PATH")


def _directory_info(record, holder):
    if os.name != "nt":
        return _OS_FSTAT(holder.raw)
    _boundary(record)
    process = _WIN_GET_CURRENT_PROCESS()
    duplicate = _reserve(record, "handle", holder.root_key, holder.path_key)
    output = wintypes.HANDLE()
    try:
        try:
            result = _WIN_DUPLICATE_HANDLE(process, holder.raw, process,
                                           ctypes.byref(output), 0, False, 2)
        except BaseException as exc:
            _unknown_acquire(record, duplicate, output.value)
            raise record.remember(exc)
        raw = output.value
        if type(result) is not int or result == 0:
            if type(result) is int and result == 0 and raw is None:
                _no_resource(record, duplicate)
            else:
                _unknown_acquire(record, duplicate, raw)
            raise ctypes.WinError(ctypes.get_last_error())
        if type(raw) is not int or raw <= 0 or raw == _INVALID_HANDLE:
            _unknown_acquire(record, duplicate, raw)
            _fail("ACQUIRE_FAILED")
        _attach_owned_return(record, duplicate, raw)
        _transfer_fd(record, duplicate)
        result = _OS_FSTAT(duplicate.raw)
    except BaseException as exc:
        record.remember(exc)
        raise
    finally:
        _close(record, duplicate)
    if record.first is not None:
        raise record.first
    return result


def _stat_directory(root, index):
    holder = root.holders[index]
    if os.name == "nt":
        return _OS_STAT(safe._native_path(Path(holder.path_key)), follow_symlinks=False)
    if index == 0:
        return _OS_STAT(holder.path_key, follow_symlinks=False)
    return _OS_STAT(Path(holder.path_key).name, dir_fd=root.holders[index - 1].raw,
                    follow_symlinks=False)


def _verify_directory(record, root, index, holder):
    _boundary(record)
    if holder.state != "owned":
        _fail("GUARD_CHANGED")
    before = _stat_directory(root, index)
    safe._safe_stat(before, directory=True)
    if os.name == "nt":
        native = _HANDLE_INFO()
        result = _WIN_GET_FILE_INFORMATION(holder.raw, ctypes.byref(native))
        error = ctypes.get_last_error()
        if not result:
            raise ctypes.WinError(error)
        if native.attributes & safe._REPARSE or not native.attributes & 0x10:
            _fail("UNSAFE_PATH")
    info = _directory_info(record, holder)
    safe._safe_stat(info, directory=True)
    after = _stat_directory(root, index)
    safe._safe_stat(after, directory=True)
    identity = _identity(info)
    if (_identity(before) != identity
            or _identity(after) != identity
            or (holder.identity is not None and holder.identity != identity)):
        _fail("IDENTITY_CHANGED")
    holder.identity, holder.identity_state = identity, "held_verified"
    _boundary(record)


def _open_root(record, root):
    canonical_path = _PATH_TYPE(root.key)
    chain = [_PATH_TYPE(canonical_path.anchor)]
    for component in canonical_path.parts[1:]:
        chain.append(chain[-1] / component)
    for path in chain:
        _boundary(record, callback=True)
        parent = root.holders[-1] if root.holders else None
        if parent is not None:
            for index, held in enumerate(root.holders):
                _verify_directory(record, root, index, held)
        holder = _open_native(record, root, path, directory=True, parent=parent)
        _verify_directory(record, root, len(root.holders) - 1, holder)
        _boundary(record)
    directory = _DirectoryView(_PATH_TYPE(root.key))
    root.directory = directory
    _register_view(record, directory, root, "directory")
    directory.verify()
    holder = _open_native(record, root, _PATH_TYPE(root.key) / lifecycle.LOCK_NAME,
                          directory=False, parent=root.holders[-1])
    info = _OS_FSTAT(holder.raw)
    safe._safe_stat(info, directory=False)
    if info.st_nlink != 1:
        _fail("LOCK_INVALID")
    holder.identity, holder.identity_state = _identity(info), "held_verified"
    if any(binding.identity == holder.identity for binding in record.bindings):
        _fail("LOCK_ALIAS")
    stream = _FdView()
    _register_view(record, stream, holder, "fd")
    lock = _LOCK_TYPE(directory, stream)
    binding = _LockBinding(lock, directory, stream, holder)
    root.binding = binding
    record.bindings.append(binding)
    _check_pins(record)
    _LOCK_STAT(lock)
    for profile in record.profiles:
        if _KEY(profile.database.parent) == root.key:
            _DATABASE(profile, directory)
    while True:
        _boundary(record, callback=True)
        try:
            acquired = _LOCK_ATTEMPT(lock, True)
        except BaseException:
            # A return never established byte-lock ownership.  The known fd
            # still has its one definite close responsibility below.
            raise
        if acquired is True:
            binding.owned = True
            _boundary(record)
            break
        if acquired is not False:
            _fail("LOCK_INVALID")
        _boundary(record)
        remaining = _ACQUIRE_NS - (record.last - record.start)
        _SLEEP(min(0.025, remaining / 1_000_000_000))
    _LOCK_VERIFY(lock)
    _boundary(record)


def _release_owned_lock_original(record, binding):
    if not binding.owned or binding.released:
        return
    binding.released = True
    binding.owned = False
    try:
        if any(type(key) is not str for key in binding.dictionary):
            _fail("GUARD_CHANGED")
        _DICT_SET(binding.dictionary, "locked", False)
    except BaseException as exc:
        record.remember(exc)
    try:
        descriptor = binding.holder.raw
        if os.name == "nt":
            overlap = _OVERLAPPED()
            result = _WIN_UNLOCK_FILE_EX(_CRT_GET_OSFHANDLE(descriptor), 0, 1, 0,
                                         ctypes.byref(overlap))
            if type(result) is not int or result == 0:
                _fail("UNLOCK_FAILED")
        elif _FLOCK(descriptor, fcntl.LOCK_UN) is not None:
            _fail("UNLOCK_FAILED")
    except BaseException as exc:
        record.remember(exc)


def _close_guarded(record, holder):
    """One helper failure must not prevent another independent native close."""
    global _POISONED
    try:
        _close(record, holder)
    except BaseException as exc:
        record.remember(exc)
        if holder.state == "close_attempted":
            holder.state = "close_unknown"
            record.unknown_closes += 1
            _POISONED = True


def _cleanup(record):
    record.phase = "closing" if record.first is None else "failed_cleanup"
    if record.guard_dict is not None:
        try:
            if any(type(key) is not str for key in record.guard_dict):
                _fail("GUARD_CHANGED")
            _DICT_SET(record.guard_dict, "_active", False)
        except BaseException as exc:
            record.remember(exc)
    try:
        try:
            for root in reversed(record.roots):
                if root.binding is not None:
                    try:
                        _release_owned_lock_original(record, root.binding)
                    except BaseException as exc:
                        record.remember(exc)
                if root.lock_holder is not None:
                    _close_guarded(record, root.lock_holder)
                for holder in reversed(root.holders):
                    _close_guarded(record, holder)
        except BaseException as exc:
            record.remember(exc)
        finally:
            # Only still-owned objects enter _close.  Close-attempted, unknown
            # or actually closed identifiers are never sent to a closer again.
            for holder in reversed(record.holders):
                if holder.state == "owned":
                    _close_guarded(record, holder)
    finally:
        if record.current_token is not None:
            try:
                _CURRENT.reset(record.current_token)
            except BaseException as exc:
                record.remember(exc)
            record.current_token = None


def _reconcile_unsettled(record):
    global _POISONED
    for holder in record.holders:
        if holder.state == "pending":
            _unknown_acquire(record, holder, holder.raw)
        elif holder.state in ("owned", "close_attempted"):
            holder.state = "close_unknown"
            record.unknown_closes += 1
            _POISONED = True
    expected_pending = sum(holder.state == "unknown" for holder in record.holders)
    expected_current = sum(holder.state in ("transfer_unknown", "close_unknown")
                           for holder in record.holders)
    expected_closed = sum(holder.state == "closed" for holder in record.holders)
    expected_acquired = expected_current + expected_closed
    if (record.pending != expected_pending or record.current != expected_current
            or record.normal_closed != expected_closed or record.acquired != expected_acquired):
        # Do not normalize broken accounting into success.  Actual close facts
        # remain on their holders; uncertain accounting gets permanent poison.
        _POISONED = True
        record.unknown_acquires += 1
        record.remember(_error("UNSETTLED"))
    if record.unknown_acquires or record.unknown_closes:
        _POISONED = True
        if record.first is None:
            record.remember(_error("UNSETTLED"))


def _identity(info):
    device, inode = info.st_dev, info.st_ino
    if (type(device) is not int or device < 0 or len(str(device)) > 40
            or type(inode) is not int or inode <= 0 or len(str(inode)) > 40):
        _fail("IDENTITY_CHANGED")
    return device, inode


def _identity_projection(identity):
    return None if identity is None else {"device": str(identity[0]), "inode": str(identity[1])}


def _failure_code(error):
    if type(error) is AuthorityReplicaGroupError:
        dictionary = BaseException.__getattribute__(error, "__dict__")
        # Reject nonexact keys before a lookup could invoke an injected eq.
        if type(dictionary) is dict and all(type(key) is str for key in dictionary):
            code = dict.get(dictionary, "code")
            if type(code) is str and code in _ERROR_CODES:
                return code
    return "GROUP_FAILED"


def _retain_unsettled(record):
    global _POISONED
    if not (record.unknown_acquires or record.unknown_closes or record.pending or record.current):
        return
    _POISONED = True
    # Detached, closed field set.  Never retain the record, callback, traceback,
    # actual Task, guard, token, or a bound closer in the process poison.
    failure_code = _failure_code(record.first)
    directories, locks = [], []
    for root in record.roots:
        for holder in root.holders:
            directories.append({"root_key": root.key, "path_key": holder.path_key,
                                "identity": _identity_projection(holder.identity),
                                "identity_state": holder.identity_state})
        holder = root.lock_holder
        locks.append({"root_key": root.key,
                      "identity": None if holder is None else _identity_projection(holder.identity),
                      "identity_state": "not_established" if holder is None else holder.identity_state})
    _TOMBSTONES.append({
        "scope_id": record.scope_id, "pid": record.owner[0], "root_keys": record.root_keys,
        "directory_bindings": tuple(directories), "lock_bindings": tuple(locks),
        "unknown_acquires": record.unknown_acquires, "unknown_closes": record.unknown_closes,
        "failure_code": failure_code, "owner_thread": record.owner[1],
        "owner_task_weak": _weak_task(record.owner[2]), "status": "unsettled",
    })
    for holder in record.holders:
        if holder.state not in ("empty", "closed"):
            _DEBTS.append({"scope_id": record.scope_id, "serial": holder.serial,
                           "kind": holder.kind, "raw": holder.raw,
                           "closer_state": holder.state})


def _retire_original(record):
    """Run even when cleanup itself fails; retain unknown state before pop."""
    global _POISONED
    with _REGISTRY_LOCK:
        try:
            try:
                _reconcile_unsettled(record)
                _retain_unsettled(record)
            except BaseException as exc:
                _POISONED = True
                record.remember(exc)
            if record.first is None:
                record.phase = "closed"
                _CLOSED[record.token] = _ClosedRecord(record, _observation(record))
        except BaseException as exc:
            record.remember(exc)
        finally:
            try:
                if record.guard is not None:
                    registration = _GUARDS.get(id(record.guard))
                    if registration is not None and registration.guard() is record.guard:
                        registration.state = "closed" if record.first is None else "failed"
            finally:
                try:
                    for view, registration in record.views:
                        if _VIEWS.get(id(view)) is registration:
                            _VIEWS.pop(id(view), None)
                finally:
                    if record.first is not None:
                        record.phase = "failed_cleanup"
                    _LIVE.pop(id(record.token), None)


def _observation(record):
    if (record.first is not None or record.pending or record.current
            or record.unknown_acquires or record.unknown_closes
            or not 0 < record.acquired == record.normal_closed <= _MAX_COUNT
            or not 0 < record.peak <= min(_MAX_RESOURCES, record.acquired)
            or _CURRENT.get() is not None):
        _fail("UNSETTLED")
    return {
        "format_version": 1, "kind": "replica_checked_outer_group",
        "scope_id": record.scope_id,
        "profile_identities": [list(identity) for identity in record.projection],
        "root_keys_sha256": record.root_hash,
        "native_resources_acquired": record.acquired,
        "native_resources_normal_closed": record.normal_closed,
        "peak_resources": record.peak, "pending_resources": 0, "current_resources": 0,
        "unknown_acquires": 0, "unknown_closes": 0,
        "native_normal_closed": True, "lifecycle_context_returned": True,
        "write_authority": False, "sender_authority": False,
    }


def _register_guard(record):
    guard = _GUARD_TYPE(record.profiles, True, tuple(binding.lock for binding in record.bindings))
    record.guard, record.guard_dict = guard, object.__getattribute__(guard, "__dict__")
    record.guard_fields = (guard._profiles, guard._profile_ids, guard._locks, guard._owner)
    registration = _GuardRegistration(guard, record)
    key = id(guard)

    def collected(reference, *, key=key):
        with _REGISTRY_LOCK:
            current = _GUARDS.get(key)
            if current is not None and current.guard is reference:
                _GUARDS.pop(key, None)

    registration.guard = weakref.ref(guard, collected)
    with _REGISTRY_LOCK:
        _GUARDS[key] = registration
    record.current_token = _CURRENT.set(guard)
    return guard


@contextmanager
def checked_exclusive_many(profiles, *, check_cancelled=None):
    """Acquire an existing complete group EX and yield a private live token."""
    owner = _owner_now()
    from . import personal_authority_preservation_routing as routing
    routing.require_no_reentry(owner)
    with _REGISTRY_LOCK:
        if _POISONED:
            _fail("UNSETTLED")
    profiles, projection, roots = _parameters(profiles, check_cancelled)
    if _CURRENT.get() is not None:
        _fail("CURRENT_PRESENT")
    token = _GroupToken(_TAG)
    record = _GroupRecord(token, owner, profiles, projection, roots, check_cancelled)
    with _REGISTRY_LOCK:
        if _POISONED:
            _fail("UNSETTLED")
        _LIVE[id(token)] = record
    try:
        _clock(record)
        record.phase = "acquiring"
        for root in record.roots:
            _open_root(record, root)
        guard = _register_guard(record)
        record.phase = "held"
        _check_pins(record, held=True)
        _GUARD_VERIFY(guard)
        _check_pins(record, held=True)
        _clock(record)
        record.acquisition_complete = True
        yield token
        _check_pins(record, held=True)
        _GUARD_VERIFY(guard)
        _check_pins(record, held=True)
    except BaseException as exc:
        record.remember(exc)
    finally:
        try:
            _cleanup(record)
        except BaseException as exc:
            record.remember(exc)
        finally:
            try:
                _retire_original(record)
            except BaseException as exc:
                # Retirement's nested finally already removes LIVE; retain the
                # original error even if a later bookkeeping step also failed.
                record.remember(exc)
    if record.first is not None:
        raise record.first


def guard_original(token):
    """Return this exact live group's original guard, never a reconstructed one."""
    if type(token) is not _GroupToken:
        _fail("GUARD_REQUIRED")
    with _REGISTRY_LOCK:
        record = _LIVE.get(id(token))
        if record is None or record.token is not token:
            _fail("CLOSED")
    _check_pins(record, held=True)
    return record.guard


def consume_closed_original(token):
    """Consume once, after actual close, in the original owner's empty context."""
    if type(token) is not _GroupToken:
        _fail("GUARD_REQUIRED")
    owner = _owner_now()
    with _REGISTRY_LOCK:
        closed = _CLOSED.get(token)
        if closed is None:
            _fail("CLOSED")
        if not _closed_owner_matches(closed, owner):
            _fail("OWNER_MISMATCH")
        if closed.consumed:
            _fail("CONSUMED")
        if _CURRENT.get() is not None:
            _fail("CURRENT_PRESENT")
        guard = closed.guard()
        if guard is not None:
            registration = _GUARDS.get(id(guard))
            if (registration is None or registration.guard() is not guard
                    or registration.state != "closed" or type(guard) is not _GUARD_TYPE
                    or object.__getattribute__(guard, "_active") is not False):
                _fail("GUARD_CHANGED")
        closed.consumed = True
        return {key: ([list(row) for row in value] if key == "profile_identities" else value)
                for key, value in closed.observation.items()}
