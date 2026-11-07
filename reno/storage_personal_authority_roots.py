"""Held permanent GC exclusions for authority and raw-preservation trees.

This observer reads names and physical metadata, plus the original lock byte.
It does not parse grants, read payloads, initialize facilities or grant writes.
Unknown observations fence the entire caller group, including inode aliases.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import time

from . import personal_authority_files as authority_files
from . import storage_artifacts as artifacts, storage_lifecycle as lifecycle
from . import storage_root_inputs as inputs, sync_media as safe

PREFIX = ".reno-personal-authority"
RAW_PRESERVATION_PREFIX = ".reno-authority-raw-preservation"
RAW_PRESERVATION_DIRECTORY = RAW_PRESERVATION_PREFIX + "-v1"
RAW_PRESERVATION2_DIRECTORY = RAW_PRESERVATION_PREFIX + "-v2"
REPLICA_DIRECTORY = RAW_PRESERVATION_PREFIX + "-replicas-v1"
NODE = "node-identity.json"
# Raw GC protection uses this real lock; it does not validate handoff authority.
HANDOFF_DIRECTORY = ".reno-personal-authority-namespace-handoff-v1"
# These exact names are the namespace3/origin-v1 wire layout, not a grant.
# Existing SH/EX lifecycle ownership is required by capture_group_locked.
_NAMESPACE3_ROOT = ".reno-personal-authority-namespace-v3"
_NAMESPACE3_ANCHORS = frozenset({
    ".reno-personal-authority-origin-v1-reservation.json",
    ".reno-personal-authority-origin-v1-observation.json",
    ".reno-personal-authority-namespace-v3-reservation.json",
    ".reno-personal-authority-namespace-v3-header.json",
})
_NS = tuple(sorted(artifacts.NAMESPACES))
_ROOT_ROLE = {"sync_media": "media", "local_report": "reports",
              "sync_report": "reports", "checkpoint": "checkpoints"}
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_MEMBER = re.compile(r"[A-Za-z0-9_.:-]{1,200}\Z")
_DECIMAL = re.compile(r"0|[1-9][0-9]*\Z")
_TOKEN = object()
_GROUPS = {}
_CAPTURES = {}


if os.name == "nt":
    # A private DLL instance keeps these ABI declarations separate from the
    # lifecycle/media modules' cached ctypes function objects.
    _directory_kernel = safe.ctypes.WinDLL("kernel32", use_last_error=True)
    _directory_kernel.GetCurrentProcess.argtypes = []
    _directory_kernel.GetCurrentProcess.restype = safe.wintypes.HANDLE
    _directory_kernel.DuplicateHandle.argtypes = [
        safe.wintypes.HANDLE, safe.wintypes.HANDLE, safe.wintypes.HANDLE,
        safe.ctypes.POINTER(safe.wintypes.HANDLE), safe.wintypes.DWORD,
        safe.wintypes.BOOL, safe.wintypes.DWORD]
    _directory_kernel.DuplicateHandle.restype = safe.wintypes.BOOL
    _directory_kernel.CloseHandle.argtypes = [safe.wintypes.HANDLE]
    _directory_kernel.CloseHandle.restype = safe.wintypes.BOOL


class PersonalAuthorityRootError(ValueError):
    def __init__(self, code, *, retriable=False):
        super().__init__(code)
        self.code, self.retriable = code, retriable


def _fail(suffix, *, retriable=False):
    raise PersonalAuthorityRootError("PERSONAL_AUTHORITY_ROOT_" + suffix,
                                     retriable=retriable)


class _CallerCancelled(BaseException):
    def __init__(self, original):
        self.original = original


@dataclass(frozen=True)
class PersonalAuthorityRootLimits:
    max_entries: int = 100_000
    max_directories: int = 100_000
    max_metadata_bytes: int = 128 * 1024 * 1024
    deadline_seconds: float = 120.0

    def __post_init__(self):
        for name, maximum in (("max_entries", 100_000), ("max_directories", 100_000),
                              ("max_metadata_bytes", 128 * 1024 * 1024)):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError("Invalid personal authority root limits")
        if (type(self.deadline_seconds) not in (int, float)
                or not math.isfinite(self.deadline_seconds)
                or not 0 < self.deadline_seconds <= 120):
            raise ValueError("Invalid personal authority root limits")


# The shorter spelling was used in the initial closed interface proposal.
PersonalAuthorityLimits = PersonalAuthorityRootLimits


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":"), allow_nan=False).encode("ascii")


def _hash(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _windows_name(value):
    return value.rstrip(" .").casefold()


def _selected(name):
    key = _windows_name(name)
    return key == NODE or key.startswith((PREFIX, RAW_PRESERVATION_PREFIX))


def _identity(info):
    return str(info.st_dev), str(info.st_ino)


def _file_metadata(info):
    return {"device": str(info.st_dev), "inode": str(info.st_ino),
            "byte_size": info.st_size, "mtime_ns": str(info.st_mtime_ns),
            "ctime_ns": str(info.st_ctime_ns), "mode": info.st_mode,
            "nlink": info.st_nlink}


def _compatible(path, handle):
    return all(path[key] == handle[key] for key in path if key != "ctime_ns")


class _Budget:
    def __init__(self, limits, callback):
        self.limits, self.callback = limits, callback
        self.started, self.cancelled = time.monotonic(), None
        self.terminal_depth = 0

    def check(self):
        if self.cancelled is not None:
            raise _CallerCancelled(self.cancelled)
        if self.callback is not None and self.terminal_depth == 0:
            try:
                self.callback()
            except BaseException as exc:
                self.cancelled = exc
                raise _CallerCancelled(exc) from None
        if time.monotonic() - self.started > self.limits.deadline_seconds:
            _fail("DEADLINE_LIMIT")


@contextmanager
def _terminal_checks(budget):
    """Internal final window: retain recorded cancellation and all limits.

    No caller option can request this mode. A complete ordinary pass already
    polled the caller; callbacks must not then mutate an earlier observation
    during the final census or the original lock's normal close verification.
    """
    previous = budget.terminal_depth
    budget.terminal_depth = previous + 1
    try:
        yield
    finally:
        budget.terminal_depth = previous


class _Pass:
    """One bounded full group census; its lifetime deadline is shared."""
    def __init__(self, budget):
        self.budget = budget
        self.entries = self.directories = self.metadata = 0

    def entry(self, name):
        self.budget.check()
        if self.entries >= self.budget.limits.max_entries:
            _fail("ENTRY_LIMIT")
        self.entries += 1
        self.record(name)

    def directory(self):
        self.budget.check()
        if self.directories >= self.budget.limits.max_directories:
            _fail("DIRECTORY_LIMIT")
        self.directories += 1

    def record(self, value):
        self.budget.check()
        size = len(_canonical(value))
        if self.metadata + size > self.budget.limits.max_metadata_bytes:
            _fail("METADATA_LIMIT")
        self.metadata += size


def _listing(directory, census, *, selected=False, observed=None):
    census.budget.check()
    directory.verify()
    names = [] if observed is None else observed
    with os.scandir(safe._native_path(directory.path) if os.name == "nt" else directory.fd) as entries:
        for entry in entries:
            name = entry.name
            census.entry(name)
            if not selected or _selected(name):
                names.append(name)
    directory.verify()
    return tuple(sorted(names))


def _windows_directory_stat(handle):
    """fstat the original held directory through an owned duplicate handle.

    Python's Windows stat identity can exceed the legacy volume/file-index
    fields. Use its same fstat implementation rather than mix those schemas.
    open_osfhandle takes ownership only of the duplicate, never the original.
    """
    process = _directory_kernel.GetCurrentProcess()
    duplicate = safe.wintypes.HANDLE()
    if not _directory_kernel.DuplicateHandle(process, handle, process,
            safe.ctypes.byref(duplicate), 0, False, 2):  # DUPLICATE_SAME_ACCESS
        raise safe.ctypes.WinError(safe.ctypes.get_last_error())
    descriptor, primary = None, None
    try:
        descriptor = safe.msvcrt.open_osfhandle(duplicate.value, os.O_RDONLY | os.O_BINARY)
        return os.fstat(descriptor)
    except BaseException as exc:
        primary = exc
        raise
    finally:
        try:
            if descriptor is None:
                if not _directory_kernel.CloseHandle(duplicate):
                    raise safe.ctypes.WinError(safe.ctypes.get_last_error())
            else:
                os.close(descriptor)
        except BaseException:
            if primary is None:
                raise
            primary.add_note("PERSONAL_AUTHORITY_ROOT_DUPLICATE_CLOSE_FAILED")


def _directory_identity(directory):
    directory.verify()
    info = os.stat(safe._native_path(directory.path), follow_symlinks=False)
    safe._safe_stat(info, directory=True)
    identity = _identity(info)
    if os.name == "nt":
        # Retain the original native type/reparse gate independently of which
        # optional Windows attribute fields Python exposes through fstat.
        native = safe._HandleInfo()
        if not safe._kernel.GetFileInformationByHandle(directory.handles[-1], safe.ctypes.byref(native)):
            raise safe.ctypes.WinError(safe.ctypes.get_last_error())
        if native.attributes & safe._REPARSE or not native.attributes & 0x10:
            _fail("SOURCE_CHANGED", retriable=True)
        held = _windows_directory_stat(directory.handles[-1])
    else:
        held = os.fstat(directory.fd)
    safe._safe_stat(held, directory=True)
    actual = _identity(held)
    if actual != identity:
        _fail("SOURCE_CHANGED", retriable=True)
    return identity


class _Tree:
    def __init__(self, data, stack, budget, census):
        self.data, self.stack, self.budget = data, stack, budget
        self.data_identity = _directory_identity(data)
        self.directories, self.files, self.observed, self.locks = {}, {}, {}, {}
        self.issues, self.paths, self.directory_paths = [], set(), set()
        self.inodes, self._positions = set(), {}
        self.roots = ()
        self.roots_complete = False
        self.profile_locked_roots = set()
        self.required_lock_names = {}
        self.entry_count = self.directory_count = self.metadata_bytes = 0
        self._capture(census)

    @property
    def complete(self):
        return self.roots_complete and not self.issues

    def _issue(self, key, code):
        issue = {"relative_key": key, "code": code}
        if issue not in self.issues:
            self.issues.append(issue)

    def _remember(self, key, info, census):
        metadata = _file_metadata(info)
        census.record([key, metadata])
        self.observed[key] = metadata
        identity = _identity(info)
        self.inodes.add(identity)
        previous = self._positions.setdefault(identity, key)
        if previous != key or identity == self.data_identity:
            self._issue(key, "PERSONAL_AUTHORITY_ROOT_IDENTITY_ALIAS")
            return False
        return True

    def _lock(self, directory, key, names, *, required):
        lock_name = "handoff.lock" if key == HANDOFF_DIRECTORY else "authority.lock"
        if key == HANDOFF_DIRECTORY:
            self.required_lock_names[key] = lock_name
        variants = [name for name in names if _windows_name(name) == lock_name]
        if not variants:
            if required:
                self._issue(key, "PERSONAL_AUTHORITY_ROOT_LOCK_MISSING")
            return
        if variants != [lock_name] and os.name != "nt":
            self._issue(key, "PERSONAL_AUTHORITY_ROOT_LOCK_UNPROVEN")
            return
        try:
            self.budget.check()
            if lock_name == "authority.lock":
                original = authority_files._ExistingLock(
                    directory, exclusive=False, timeout=0, check=self.budget.check)
            else:
                original = authority_files._ExistingLock(directory, exclusive=False, timeout=0,
                    check=self.budget.check, lock_name=lock_name)
            lock = self.stack.enter_context(original)
            self.locks[key] = lock
        except (authority_files.FilePrimitiveError, safe.SyncError, OSError) as exc:
            self._issue(key, "PERSONAL_AUTHORITY_ROOT_LOCK_UNPROVEN")

    def _file(self, directory, name, key, info, census):
        safe._safe_stat(info, directory=False)
        if info.st_nlink != 1:
            self._issue(key, "PERSONAL_AUTHORITY_ROOT_UNSAFE_LINK")
            return
        # The lock already has an original held stream. Opening it again on
        # Windows would needlessly create a second sharing/byte-lock owner.
        parent = key.rsplit("/", 1)[0] if "/" in key else ""
        lock = self.locks.get(parent)
        if lock is not None and _windows_name(name) != lock.lock_name:
            lock = None
        stream = lock.stream if lock is not None else self.stack.enter_context(directory.open_file(name))
        self.budget.check()
        handle = os.fstat(stream.fileno())
        safe._safe_stat(handle, directory=False)
        after = directory.entry_stat(name)
        safe._safe_stat(after, directory=False)
        before, current, held = _file_metadata(info), _file_metadata(after), _file_metadata(handle)
        if before != current or not _compatible(current, held) or handle.st_nlink != 1:
            _fail("SOURCE_CHANGED", retriable=True)
        census.record([key, current, held])
        self.files[key] = (directory, name, stream, current, held)

    def _capture(self, census):
        before = census.entries, census.directories, census.metadata
        pending = []
        root_names = []
        try:
            self.roots = _listing(self.data, census, selected=True, observed=root_names)
            self.roots_complete = True
            pending.append((self.data, "", self.roots))
            while pending:
                directory, prefix, names = pending.pop()
                for name in names:
                    self.budget.check()
                    key = prefix + "/" + name if prefix else name
                    self.paths.add(key)
                    try:
                        safe._entry_name(name)
                        info = directory.entry_stat(name)
                        if not self._remember(key, info, census):
                            continue
                        if stat.S_ISDIR(info.st_mode):
                            self.directory_paths.add(key)
                            safe._safe_stat(info, directory=True)
                            census.directory()
                            child = self.stack.enter_context(safe._Directory(
                                directory.path / name, base=self.data.path, parent=directory))
                            identity = _directory_identity(child)
                            if identity != _identity(info):
                                _fail("SOURCE_CHANGED", retriable=True)
                            children = _listing(child, census)
                            self.directories[key] = (child, identity, children)
                            if not prefix and _windows_name(name).startswith(PREFIX):
                                modern = name == _NAMESPACE3_ROOT
                                if modern:
                                    # child is held, its identity matched lstat,
                                    # and its complete listing was verified.
                                    self.profile_locked_roots.add(key)
                                self._lock(child, key, children, required=not modern)
                            pending.append((child, key, children))
                        else:
                            self._file(directory, name, key, info, census)
                    except (safe.SyncError, OSError, authority_files.FilePrimitiveError, RecursionError):
                        self._issue(key, "PERSONAL_AUTHORITY_ROOT_UNREADABLE")
            locked_roots = {_windows_name(key) for key in self.locks if "/" not in key}
            for name in self.roots:
                key = _windows_name(name)
                if not key.startswith(PREFIX) or name in self.directory_paths:
                    continue
                if (name in _NAMESPACE3_ANCHORS
                        and _NAMESPACE3_ROOT in self.profile_locked_roots):
                    continue
                root = next((key[:-len(suffix)] for suffix in ("-reservation.json", "-header.json")
                             if key.endswith(suffix)), None)
                if root not in locked_roots:
                    self._issue(name, "PERSONAL_AUTHORITY_ROOT_ORPHAN_ENTRY")
            self.data.verify()
        except PersonalAuthorityRootError as exc:
            if exc.retriable:
                raise
            self._issue("", exc.code)
        except (safe.SyncError, OSError, RecursionError):
            self._issue("", "PERSONAL_AUTHORITY_ROOT_UNREADABLE")
        finally:
            if not self.roots_complete:
                self.roots = tuple(sorted(root_names))
                self.paths.update(root_names)
            self.entry_count = census.entries - before[0]
            self.directory_count = census.directories - before[1]
            self.metadata_bytes = census.metadata - before[2]

    def verify(self, census):
        self.budget.check()
        try:
            if _directory_identity(self.data) != self.data_identity:
                _fail("SOURCE_CHANGED", retriable=True)
            for lock in self.locks.values():
                authority_files._ExistingLock.verify(lock)
            # An incomplete tree never grants collection anywhere in the
            # group; do not pretend that a bounded partial re-list is complete.
            if not self.complete:
                return
            if _listing(self.data, census, selected=True) != self.roots:
                _fail("SOURCE_CHANGED", retriable=True)
            for key, (directory, identity, names) in self.directories.items():
                census.directory()
                if (_directory_identity(directory) != identity
                        or _listing(directory, census) != names):
                    _fail("SOURCE_CHANGED", retriable=True)
                census.record([key, identity, names])
            for key, (directory, name, stream, path, handle) in self.files.items():
                self.budget.check()
                actual = directory.entry_stat(name)
                native = os.fstat(stream.fileno())
                safe._safe_stat(actual, directory=False)
                safe._safe_stat(native, directory=False)
                if _file_metadata(actual) != path or _file_metadata(native) != handle:
                    _fail("SOURCE_CHANGED", retriable=True)
                census.record([key, path, handle])
            self.data.verify()
        except (safe.SyncError, authority_files.FilePrimitiveError, OSError, RecursionError):
            _fail("SOURCE_CHANGED", retriable=True)

    def binding(self):
        # The raw-only family has no externally referenced artifacts to infer
        # from its documents. Completeness here describes physical discovery,
        # never a valid receipt, completed package or deletion authorization.
        result = {"format_version": 1, "algorithm_version": 2,
                "selection": {"prefix": PREFIX, "raw_preservation_prefix": RAW_PRESERVATION_PREFIX,
                              "node": NODE, "comparison": "casefold-rstrip-dot-space"},
                "data_identity": self.data_identity, "roots": self.roots,
                "roots_complete": self.roots_complete, "complete": self.complete,
                "directories": [[key, identity, _hash(names)]
                                for key, (_, identity, names) in sorted(self.directories.items())],
                "files": [[key, path, handle] for key, (_, _, _, path, handle) in sorted(self.files.items())],
                "observed": sorted(self.observed.items()), "issues": self.issues,
                "limits": asdict(self.budget.limits)}
        if self.profile_locked_roots:
            result.update(algorithm_version=3,
                          profile_locked_roots=sorted(self.profile_locked_roots))
        if self.required_lock_names:
            result.update(algorithm_version=4,
                          required_lock_names=sorted(self.required_lock_names.items()))
        return result


class _GroupRecord:
    def __init__(self, token, profiles, guard, budget, stack):
        self.token, self.profiles, self.guard = token, profiles, guard
        self.owner, self.budget, self.stack = lifecycle._owner(), budget, stack
        self.trees, self.views, self.data_aliases = {}, {}, []


class _CaptureRecord:
    def __init__(self, token, group, profile, tree, root_ids, member_id):
        self.token, self.group, self.profile, self.tree = token, group, profile, tree
        self.root_ids, self.member_id = dict(root_ids), member_id


def _group_record(group, *, exclusive=False):
    record = _GROUPS.get(id(group))
    if type(group) is not AuthorityProtectionGroup or record is None or record.token is not group:
        _fail("GROUP_CLOSED")
    if lifecycle._owner() != record.owner:
        _fail("OWNER_MISMATCH")
    guard = lifecycle.require_guard(record.profiles[0], exclusive=exclusive)
    if guard is not record.guard or any(not guard.allows(profile) for profile in record.profiles):
        _fail("GUARD_CHANGED")
    return record


def _capture_record(capture, *, exclusive=False):
    record = _CAPTURES.get(id(capture))
    if type(capture) is not PersonalAuthorityCapture or record is None or record.token is not capture:
        _fail("CAPTURE_CLOSED")
    _group_record(record.group, exclusive=exclusive)
    return record


def _scan(record):
    tree = record.tree
    source_id = _hash("discovery")
    blockers = tuple(inputs.Blocker(ns, record.root_ids[ns], record.member_id,
        "personal_authority", source_id, issue["code"], _hash(issue["relative_key"]))
        for issue in tree.issues for ns in _NS)
    bindings = (inputs.SourceBinding(record.member_id, "personal_authority", source_id,
                    "limits", "present", _hash(asdict(tree.budget.limits)), 1),
                inputs.SourceBinding(record.member_id, "personal_authority", source_id,
                    "authority_tree", "complete" if tree.complete else "unknown",
                    _hash(tree.binding()), len(tree.observed)))
    return inputs.InputScan(blockers=blockers, source_bindings=bindings)


class PersonalAuthorityCapture:
    __slots__ = ()

    def __new__(cls, token):
        if token is not _TOKEN:
            _fail("CAPTURE_CLOSED")
        return super().__new__(cls)

    @property
    def inputs(self):
        return _scan(_capture_record(self))

    @property
    def protected_paths(self):
        record = _capture_record(self)
        return tuple(record.profile.database.parent / key for key in sorted(record.tree.paths))

    @property
    def protected_directories(self):
        record = _capture_record(self)
        return tuple(record.profile.database.parent / key for key in sorted(record.tree.directory_paths))

    @property
    def protected_inodes(self):
        return frozenset(_capture_record(self).tree.inodes)

    @property
    def source_binding(self):
        record = _capture_record(self)
        return json.loads(_canonical({"profile": record.profile.identity,
                                      "tree": record.tree.binding()}))

    @property
    def complete(self):
        return _capture_record(self).tree.complete

    @property
    def entry_count(self):
        return _capture_record(self).tree.entry_count

    @property
    def directory_count(self):
        return _capture_record(self).tree.directory_count

    @property
    def file_count(self):
        return len(_capture_record(self).tree.files)

    @property
    def metadata_bytes(self):
        return _capture_record(self).tree.metadata_bytes

    def verify_source(self):
        verify_capture(self)


class AuthorityProtectionGroup:
    __slots__ = ()

    def __new__(cls, token):
        if token is not _TOKEN:
            _fail("GROUP_CLOSED")
        return super().__new__(cls)

    @property
    def profiles(self):
        return _group_record(self).profiles

    def capture_for(self, profile):
        record = _group_record(self)
        profile = lifecycle._profile(profile)
        capture = record.views.get(profile.identity)
        if capture is None:
            _fail("PROFILE_MISMATCH")
        return capture


def _verify_pass(record):
    record.budget.check()
    for directory, observed in record.data_aliases:
        if _directory_identity(directory) != observed:
            _fail("SOURCE_CHANGED", retriable=True)
    census = _Pass(record.budget)
    for tree in record.trees.values():
        tree.verify(census)
    record.guard.verify()


def _verify_group(group, *, profiles=None, exclusive=False):
    record = _group_record(group, exclusive=exclusive)
    if profiles is not None:
        expected = {profile.identity for profile in lifecycle._profiles(profiles)}
        if expected != {profile.identity for profile in record.profiles}:
            _fail("GROUP_INCOMPLETE")
    _verify_pass(record)
    # A late caller poll can change a directory checked earlier, or even a
    # different profile's tree. Repeat the whole exact group with no further
    # caller code; the same physical checks, deadline and budgets still apply.
    with _terminal_checks(record.budget):
        _verify_pass(record)


def verify_group(group, *, profiles=None, exclusive=False):
    try:
        _verify_group(group, profiles=profiles, exclusive=exclusive)
    except _CallerCancelled as exc:
        raise exc.original from None


def verify_capture(capture, *, profile=None, exclusive=False):
    record = _capture_record(capture, exclusive=exclusive)
    if profile is not None and lifecycle._profile(profile).identity != record.profile.identity:
        _fail("PROFILE_MISMATCH")
    verify_group(record.group, exclusive=exclusive)


def assert_unprotected(group, *, profile, path, identity):
    verify_group(group, exclusive=True)
    record = _group_record(group, exclusive=True)
    profile = lifecycle._profile(profile)
    actual_profiles = {item.identity for item in record.profiles}
    if (profile.identity not in actual_profiles
            or actual_profiles != {item.identity for item in record.guard.profiles}):
        _fail("GROUP_INCOMPLETE")
    if (type(identity) is not tuple or len(identity) != 2
            or any(type(value) is not str or not _DECIMAL.fullmatch(value) for value in identity)):
        raise ValueError("Invalid personal authority physical identity")
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Invalid personal authority candidate path")
    if any(not tree.complete for tree in record.trees.values()):
        _fail("UNKNOWN")
    key = lifecycle._key(path)
    for view in record.views.values():
        captured = _capture_record(view)
        tree, base = captured.tree, captured.profile.database.parent
        base_key = lifecycle._key(base)
        if key.startswith(base_key + os.sep):
            first = key[len(base_key) + 1:].split(os.sep, 1)[0]
            # A previously staged entry may now be absent from the original
            # selection. Reserved names remain permanent exclusions anyway.
            if _selected(first):
                _fail("PROTECTED")
        if identity in tree.inodes:
            _fail("PROTECTED")
        if key in {lifecycle._key(base / item) for item in tree.paths}:
            _fail("PROTECTED")
        for item in tree.directory_paths:
            root = lifecycle._key(base / item)
            if key == root or key.startswith(root + os.sep):
                _fail("PROTECTED")


def _root_ids(profile):
    return {ns: artifacts.root_identity(getattr(profile, _ROOT_ROLE[ns]), profile=profile)["root_id"]
            for ns in _NS}


@contextmanager
def capture_group_locked(profiles, *, limits=None, check_cancelled=None):
    profiles = lifecycle._profiles(profiles)
    limits = PersonalAuthorityLimits() if limits is None else limits
    if type(limits) is not PersonalAuthorityLimits or check_cancelled is not None and not callable(check_cancelled):
        raise ValueError("Invalid personal authority capture options")
    limits.__post_init__()
    guard = lifecycle.require_guard(profiles[0])
    for profile in profiles:
        if lifecycle.require_guard(profile) is not guard:
            _fail("GUARD_CHANGED")
    budget, stack = _Budget(limits, check_cancelled), ExitStack()
    group = AuthorityProtectionGroup(_TOKEN)
    record = _GroupRecord(group, profiles, guard, budget, stack)
    _GROUPS[id(group)] = record
    primary = None
    try:
        census = _Pass(budget)
        for profile in profiles:
            budget.check()
            data = stack.enter_context(safe._Directory(profile.database.parent, base=profile.database.parent))
            identity = _directory_identity(data)
            record.data_aliases.append((data, identity))
            tree = record.trees.get(identity)
            if tree is None:
                tree = _Tree(data, stack, budget, census)
                record.trees[identity] = tree
            capture = PersonalAuthorityCapture(_TOKEN)
            _CAPTURES[id(capture)] = _CaptureRecord(capture, group, profile, tree, _root_ids(profile), "authority")
            record.views[profile.identity] = capture
        verify_group(group)
        yield group
        verify_group(group)
    except _CallerCancelled as exc:
        primary = exc.original
        raise exc.original from None
    except BaseException as exc:
        primary = exc
        raise
    finally:
        for capture in record.views.values():
            _CAPTURES.pop(id(capture), None)
        _GROUPS.pop(id(group), None)
        try:
            # Normal lock close performs its original identity/byte checks.
            # Do not reopen a caller mutation window after the final census.
            with _terminal_checks(budget):
                stack.__exit__(type(primary) if primary is not None else None,
                               primary, primary.__traceback__ if primary is not None else None)
        except BaseException as close_error:
            if primary is None:
                if isinstance(close_error, _CallerCancelled):
                    raise close_error.original from None
                raise
            primary.add_note("PERSONAL_AUTHORITY_ROOT_CLOSE_FAILED")


@contextmanager
def capture_locked(profile, *, root_ids, member_id, limits=None, check_cancelled=None):
    profile = lifecycle._profile(profile)
    if (type(root_ids) is not dict or set(root_ids) != set(_NS)
            or any(type(value) is not str or not _SHA.fullmatch(value) for value in root_ids.values())
            or type(member_id) is not str or not _MEMBER.fullmatch(member_id)):
        raise ValueError("Invalid personal authority source binding")
    if _root_ids(profile) != root_ids:
        _fail("BINDING_MISMATCH")
    with capture_group_locked((profile,), limits=limits, check_cancelled=check_cancelled) as group:
        captured = AuthorityProtectionGroup.capture_for(group, profile)
        _capture_record(captured).member_id = member_id
        yield captured
