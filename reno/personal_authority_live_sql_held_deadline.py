"""Continuously hold original SQLite files with finite repeated-read costs.

Only the concrete origin five-pass schedule is supported. The source pin, EX,
image and all SQLite Connections are borrowed. One existing absolute token is
shared with a separate actual raw-read ledger; node/control and SQL-row costs
are not charged here. The r825 lease alone closes its numeric source FD.
"""
from __future__ import annotations

from dataclasses import dataclass

import hashlib
import io
import math
import os
import time

from . import personal_authority_directory_deadline as directories
from . import personal_authority_live_sql_deadline as live_sql
from . import personal_authority_namespace3_deadline as deadline
from . import personal_preservation_files as files
from . import storage_lifecycle as lifecycle
from . import storage_readonly as readonly
from . import storage_source as source
from . import sync_media as safe

_monotonic = time.monotonic
_NEW, _OPENING, _ACTIVE, _RETIRED = "new", "opening", "active", "retired"
_EMPTY, _PREPARED, _ATTEMPTED = "empty", "prepared", "attempted"
_CLOSED, _UNKNOWN = "closed", "unknown"
_SUFFIXES = ("", "-wal", "-journal")
_ORIGIN_FULL_READ_PASSES = 5
_CHUNK = 128 * 1024
_EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
_IDENTITY_FIELDS = frozenset(("device", "inode", "byte_size", "mtime_ns",
                              "ctime_ns", "mode", "nlink"))
_FP_FIELDS = frozenset(("device", "inode", "byte_size", "mtime_ns",
                        "ctime_ns", "sha256"))
_DECIMAL_FIELDS = ("device", "inode", "mtime_ns", "ctime_ns")


@dataclass(frozen=True, slots=True)
class LiveSqlHeldCloseReceiptV1:
    """Own context terminal facts plus its actual cached lower receipt."""
    format_version: int
    kind: str
    scope: str
    phase: str
    enter_returned: bool
    exit_attempted: bool
    failed: bool
    normal_exit: bool
    source_directory_borrowed: bool
    terminal_verified: bool
    lower_close_receipt: live_sql.LiveSqlCloseReceiptV1 | None


class LiveSqlHeldDeadlineError(ValueError):
    def __init__(self, suffix):
        self.code = "LIVE_SQL_HELD_DEADLINE_" + suffix
        super().__init__(self.code)


Error = LiveSqlHeldDeadlineError


def _fail(suffix):
    raise LiveSqlHeldDeadlineError(suffix) from None


def _changed():
    raise readonly.ReadonlyImageError("SOURCE_CHANGED", retriable=True) from None


def _note(primary):
    try:
        BaseException.add_note(primary, "LIVE_SQL_HELD_DEADLINE_CLEANUP_FAILED")
    except BaseException:
        pass


def _token(value):
    try:
        return deadline._token(value)
    except deadline.Namespace3Error:
        _fail("INVALID_ARGUMENT")


def _clock(token):
    absolute = _token(token).absolute_monotonic
    now = _monotonic()
    if type(now) is not float or not math.isfinite(now) or now < 0.0:
        _fail("INVALID_ARGUMENT")
    if now >= absolute:
        _fail("DEADLINE")


def _require_guard(profile, exclusive=True):
    return lifecycle.require_guard(profile, exclusive=exclusive)


def _directory_verify(directory):
    return directories.DeadlineDirectory.verify(directory)


def _directory_stat(path):
    return os.stat(path, follow_symlinks=False)


def _entry_stat(directory, name):
    return directories.DeadlineDirectory.entry_stat(directory, name)


def _identity(info):
    return files.identity(info)


def _live_reader(directory, name, *, deadline_token):
    return live_sql.source_reader(directory, name, deadline_token=deadline_token)


def _lease_enter(lease):
    return live_sql.LiveSqlDeadlineLease.__enter__(lease)


def _lease_terminal(lease):
    return live_sql.LiveSqlDeadlineLease._terminal(lease)


def _lease_exit(lease, kind, primary, tb):
    return live_sql.LiveSqlDeadlineLease.__exit__(lease, kind, primary, tb)


def _raw_fileno(raw):
    return io.FileIO.fileno(raw)


def _raw_seek(raw, offset, whence):
    return io.FileIO.seek(raw, offset, whence)


def _raw_read(raw, requested):
    return io.FileIO.read(raw, requested)


def _fstat(fd):
    return os.fstat(fd)


def _hash_new():
    return hashlib.sha256()


def _hash_update(digest, chunk):
    return digest.update(chunk)


def _hash_hexdigest(digest):
    return digest.hexdigest()


def _safe_directory_stat(info):
    return safe._safe_stat(info, directory=True)


def _decimal(value, *, result=False):
    if type(value) is not str:
        _fail("INVALID_RESULT" if result else "INVALID_ARGUMENT")
    try:
        number = int(value)
    except ValueError:
        _fail("INVALID_RESULT" if result else "INVALID_ARGUMENT")
    if str(number) != value:
        _fail("INVALID_RESULT" if result else "INVALID_ARGUMENT")
    return value


def _hexadecimal(value, *, result=False):
    if (type(value) is not str or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)):
        _fail("INVALID_RESULT" if result else "INVALID_ARGUMENT")
    return value


def _fingerprint(profile, value):
    if (type(value) is not dict or any(type(key) is not str for key in value)
            or set(value) != {"format_version", "profile_identity", "files"}
            or type(value["format_version"]) is not int or value["format_version"] != 1
            or type(value["profile_identity"]) is not list
            or len(value["profile_identity"]) != 4
            or any(type(item) is not str for item in value["profile_identity"])
            or value["profile_identity"] != list(profile.identity)):
        _fail("INVALID_ARGUMENT")
    submitted = value["files"]
    if (type(submitted) is not dict or any(type(key) is not str for key in submitted)
            or set(submitted) != set(_SUFFIXES) or submitted[""] is None):
        _fail("INVALID_ARGUMENT")
    expected = {}
    for suffix in _SUFFIXES:
        item = submitted[suffix]
        if item is None:
            expected[suffix] = None
            continue
        if (type(item) is not dict or any(type(key) is not str for key in item)
                or set(item) != _FP_FIELDS or type(item["byte_size"]) is not int
                or item["byte_size"] < 0):
            _fail("INVALID_ARGUMENT")
        frozen = {key: _decimal(item[key]) for key in _DECIMAL_FIELDS}
        frozen["byte_size"], frozen["sha256"] = item["byte_size"], _hexadecimal(item["sha256"])
        if suffix == "-journal" and (frozen["byte_size"] != 0
                                      or frozen["sha256"] != _EMPTY_SHA256):
            _fail("INVALID_ARGUMENT")
        expected[suffix] = frozen
    if sum(item["byte_size"] for item in expected.values()
           if item is not None) > readonly.MAX_BYTES_LIMIT:
        _fail("LIMIT")
    return expected


def _identity_result(value):
    if (type(value) is not dict or any(type(key) is not str for key in value)
            or set(value) != _IDENTITY_FIELDS
            or any(type(value[key]) is not int or value[key] < 0
                   for key in ("byte_size", "mode", "nlink"))):
        _fail("INVALID_RESULT")
    frozen = {key: _decimal(value[key], result=True) for key in _DECIMAL_FIELDS}
    frozen.update({key: value[key] for key in ("byte_size", "mode", "nlink")})
    if frozen["nlink"] != 1:
        raise readonly.ReadonlyImageError("SOURCE_UNSAFE") from None
    return frozen


def _source_call(operation, *args, **kwargs):
    # This narrow boundary is used only for fixed source/identity consumers.
    try:
        return operation(*args, **kwargs)
    except source.SourceReadError as failure:
        if failure.code == "SOURCE_CHANGED":
            _changed()
        if failure.code == "SOURCE_UNSAFE":
            raise readonly.ReadonlyImageError("SOURCE_UNSAFE") from None
        raise
    except FileNotFoundError:
        if operation is _entry_stat:
            _changed()
        raise


class LiveSqlHeldBudget:
    """Three fixed declarations and shared actual fees; no resource closer."""
    __slots__ = ("_profile", "_expected", "_token", "_guard", "_directory",
                 "_count", "_declared", "_read_limits", "_call_limits", "_eof_limits",
                 "_read_bytes", "_read_calls", "_eofs", "_passes_started",
                 "_passes_completed", "_claims", "_exit_done", "_first", "_first_tb",
                 "_retired")

    def __init_subclass__(cls, **kwargs):
        _fail("INVALID_ARGUMENT")

    def __init__(self, profile, source_fingerprint, *, deadline_token):
        if type(self) is not LiveSqlHeldBudget or type(profile) is not lifecycle.StorageProfile:
            _fail("INVALID_ARGUMENT")
        token = _token(deadline_token)
        _clock(token)
        expected = _fingerprint(profile, source_fingerprint)
        safe._entry_name(profile.database.name)
        self._profile, self._expected, self._token = profile, expected, token
        self._guard = self._directory = None
        self._count = sum(item is not None for item in expected.values())
        self._declared = sum(item["byte_size"] for item in expected.values() if item is not None)
        self._read_limits = {key: 0 if expected[key] is None else
            _ORIGIN_FULL_READ_PASSES * (expected[key]["byte_size"] + 1) for key in _SUFFIXES}
        self._eof_limits = {key: 0 if expected[key] is None else _ORIGIN_FULL_READ_PASSES
                            for key in _SUFFIXES}
        self._call_limits = {key: self._read_limits[key] + self._eof_limits[key]
                             for key in _SUFFIXES}
        self._read_bytes = {key: 0 for key in _SUFFIXES}
        self._read_calls = {key: 0 for key in _SUFFIXES}
        self._eofs = {key: 0 for key in _SUFFIXES}
        self._passes_started = {key: 0 for key in _SUFFIXES}
        self._passes_completed = {key: 0 for key in _SUFFIXES}
        self._claims = {key: False for key in _SUFFIXES}
        self._exit_done = {key: expected[key] is None for key in _SUFFIXES}
        self._first = self._first_tb = None
        self._retired = False
        _clock(token)
        guard = _require_guard(profile, exclusive=True)
        _clock(token)
        if type(guard) is not lifecycle.StorageGuard:
            _fail("INVALID_RESULT")
        self._guard = guard

    @property
    def deadline_token(self):
        return self._token

    @property
    def evidence(self):
        return {"declared_file_count": self._count, "declared_bytes": self._declared,
                "read_bytes": sum(self._read_bytes.values()),
                "read_bytes_limit": sum(self._read_limits.values()),
                "read_calls": sum(self._read_calls.values()),
                "read_calls_limit": sum(self._call_limits.values()),
                "eof_reads": sum(self._eofs.values()), "eof_limit": sum(self._eof_limits.values()),
                "passes_started": dict(self._passes_started),
                "passes_completed": dict(self._passes_completed),
                "failed": self._first is not None, "retired": self._retired}

    def _capture(self, failure, *, original_tb=None, cleanup=False):
        if self._first is None:
            self._first = failure
            self._first_tb = (BaseException.__dict__["__traceback__"].__get__(failure)
                              if original_tb is None else original_tb)
        elif cleanup and failure is not self._first:
            _note(self._first)
        # Failure retirement blocks new business work, never known cleanup.
        self._retired = True

    def _raise_first(self):
        if self._first is not None:
            raise BaseException.with_traceback(self._first, self._first_tb) from None

    def _live(self):
        self._raise_first()
        if self._retired:
            _fail("CLOSED")
        _clock(self._token)

    def _same_guard(self):
        self._live()
        _clock(self._token)
        current = _require_guard(self._profile, exclusive=True)
        _clock(self._token)
        if current is not self._guard:
            _fail("INVALID_ARGUMENT")

    def _check_directory(self, directory):
        self._live()
        if (type(directory) is not directories.DeadlineDirectory
                or directory.deadline_token is not self._token
                or directory.path != self._profile.database.parent
                or (self._directory is not None and directory is not self._directory)):
            _fail("INVALID_ARGUMENT")
        _clock(self._token)
        directories.DeadlineDirectory._live(directory)
        _clock(self._token)

    def _claim(self, directory, suffix):
        self._check_directory(directory)
        self._same_guard()
        if self._claims[suffix]:
            _fail("CLOSED")
        if self._directory is None:
            self._directory = directory
        self._claims[suffix] = True

    def _start_pass(self, suffix):
        self._live()
        if not self._claims[suffix] or self._passes_started[suffix] >= _ORIGIN_FULL_READ_PASSES:
            _fail("LIMIT")
        self._passes_started[suffix] += 1

    def _request(self, suffix, requested):
        self._live()
        if type(requested) is not int or not 1 <= requested <= _CHUNK:
            _fail("INVALID_ARGUMENT")
        if (requested > self._read_limits[suffix] - self._read_bytes[suffix]
                or requested > sum(self._read_limits.values()) - sum(self._read_bytes.values())
                or self._read_calls[suffix] >= self._call_limits[suffix]
                or sum(self._read_calls.values()) >= sum(self._call_limits.values())
                or self._eofs[suffix] >= self._eof_limits[suffix]
                or sum(self._eofs.values()) >= sum(self._eof_limits.values())):
            _fail("LIMIT")

    def _charge(self, suffix, chunk):
        # Exact bytes report their actual length, including growth and EOF0.
        size = len(chunk)
        self._read_bytes[suffix] += size
        self._read_calls[suffix] += 1
        if size == 0:
            self._eofs[suffix] += 1
        # The legal returned fees must survive a post-return deadline failure.
        _clock(self._token)
        if (self._read_bytes[suffix] > self._read_limits[suffix]
                or sum(self._read_bytes.values()) > sum(self._read_limits.values())
                or self._read_calls[suffix] > self._call_limits[suffix]
                or sum(self._read_calls.values()) > sum(self._call_limits.values())
                or self._eofs[suffix] > self._eof_limits[suffix]
                or sum(self._eofs.values()) > sum(self._eof_limits.values())):
            _fail("LIMIT")

    def _complete_pass(self, suffix):
        self._live()
        if self._passes_completed[suffix] + 1 != self._passes_started[suffix]:
            _fail("INVALID_RESULT")
        self._passes_completed[suffix] += 1
        # Completion of scan five does not retire a still-held source lease.

    def _fixed_exit_done(self, suffix):
        self._exit_done[suffix] = True
        if all(self._exit_done.values()):
            self._retired = True


class DeadlineLiveSqlHeldFile:
    """Own one r825 lease; repeated scans always borrow its same raw FD."""
    __slots__ = ("_directory", "_suffix", "_budget", "_token", "_name", "_phase",
                 "_lease", "_lease_state", "_entered", "_raw", "_fd",
                 "_directory_identity", "_path_identity", "_handle_identity",
                 "_sha256", "_terminal_verified", "_normal_exit",
                 "_enter_returned", "_exit_attempted", "_failed", "_lower_close_receipt")

    def __init_subclass__(cls, **kwargs):
        _fail("INVALID_ARGUMENT")

    def __init__(self, directory, suffix, *, read_budget, deadline_token):
        if type(self) is not DeadlineLiveSqlHeldFile or type(read_budget) is not LiveSqlHeldBudget:
            _fail("INVALID_ARGUMENT")
        read_budget._raise_first()
        if read_budget._retired:
            _fail("CLOSED")
        try:
            token = _token(deadline_token)
            if (token is not read_budget.deadline_token or type(suffix) is not str
                    or suffix not in _SUFFIXES or read_budget._expected[suffix] is None):
                _fail("INVALID_ARGUMENT")
            read_budget._check_directory(directory)
        except BaseException as failure:
            read_budget._capture(failure)
            read_budget._raise_first()
        self._directory, self._suffix, self._budget, self._token = directory, suffix, read_budget, token
        self._name = read_budget._profile.database.name + suffix
        self._phase, self._lease_state = _NEW, _EMPTY
        self._lease = self._raw = self._fd = None
        self._entered = False
        self._directory_identity = self._path_identity = self._handle_identity = None
        self._sha256 = None
        self._terminal_verified = self._normal_exit = False
        self._enter_returned = self._exit_attempted = self._failed = False
        self._lower_close_receipt = None

    @property
    def byte_size(self):
        return self._budget._expected[self._suffix]["byte_size"]

    @property
    def sha256(self):
        return self._sha256

    @property
    def directory_identity(self):
        return None if self._directory_identity is None else dict(self._directory_identity)

    @property
    def path_identity(self):
        return None if self._path_identity is None else dict(self._path_identity)

    @property
    def handle_identity(self):
        return None if self._handle_identity is None else dict(self._handle_identity)

    @property
    def evidence(self):
        return {"phase": self._phase, "lease_state": self._lease_state,
                "passes_started": self._budget._passes_started[self._suffix],
                "passes_completed": self._budget._passes_completed[self._suffix],
                "terminal_verified": self._terminal_verified, "normal_exit": self._normal_exit,
                "source_directory_borrowed": True}

    @property
    def close_receipt(self):
        # Do not read the shared budget or lower property from this getter.
        return LiveSqlHeldCloseReceiptV1(1, "live_sql_held", "native_owner_close_facts_only",
            self._phase, self._enter_returned, self._exit_attempted,
            self._failed, self._normal_exit and not self._failed, True,
            self._terminal_verified, self._lower_close_receipt)

    def _cache_lower_close_receipt(self):
        if self._lease is None:
            return
        try:
            if type(self._lease) is not live_sql.LiveSqlDeadlineLease:
                _fail("INVALID_RESULT")
            actual = self._lease.close_receipt
            if type(actual) is not live_sql.LiveSqlCloseReceiptV1:
                _fail("INVALID_RESULT")
            self._lower_close_receipt = actual
        except BaseException as failure:
            self._lower_close_receipt = None
            self._failed = True
            self._budget._capture(failure, cleanup=True)
        self._failed = self._failed or self._budget._first is not None

    def _bound(self):
        self._budget._check_directory(self._directory)
        if self._budget.deadline_token is not self._token:
            _fail("INVALID_ARGUMENT")

    def _business(self, *, opening=False):
        self._budget._raise_first()
        if self._phase != _ACTIVE and not (opening and self._phase == _OPENING):
            _fail("CLOSED")
        self._budget._live()
        self._bound()
        if not self._entered or type(self._raw) is not io.FileIO:
            _fail("CLOSED")

    def _directory_snapshot(self):
        _clock(self._token)
        actual = _directory_verify(self._directory)
        _clock(self._token)
        if actual is not None:
            _fail("INVALID_RESULT")
        _clock(self._token)
        info = _directory_stat(self._directory.path)
        _clock(self._token)
        if type(info) is not os.stat_result:
            _fail("INVALID_RESULT")
        _clock(self._token)
        actual = _safe_directory_stat(info)
        _clock(self._token)
        if actual is not None:
            _fail("INVALID_RESULT")
        return {"device": str(info.st_dev), "inode": str(info.st_ino)}

    def _path_snapshot(self):
        _clock(self._token)
        info = _source_call(_entry_stat, self._directory, self._name)
        _clock(self._token)
        if type(info) is not os.stat_result:
            _fail("INVALID_RESULT")
        _clock(self._token)
        actual = _source_call(_identity, info)
        _clock(self._token)
        return _identity_result(actual)

    def _current_fd(self):
        self._business(opening=True)
        _clock(self._token)
        actual = _raw_fileno(self._raw)
        legal = type(actual) is int and actual >= 0
        if legal and self._fd is None:
            self._fd = actual
        _clock(self._token)
        if not legal or actual != self._fd:
            _fail("INVALID_RESULT")
        return actual

    def _handle_snapshot(self):
        fd = self._current_fd()
        _clock(self._token)
        info = _fstat(fd)
        _clock(self._token)
        if type(info) is not os.stat_result:
            _fail("INVALID_RESULT")
        _clock(self._token)
        actual = _source_call(_identity, info)
        _clock(self._token)
        return _identity_result(actual)

    def _verify_identities(self):
        self._bound()
        if self._directory_snapshot() != self._directory_identity:
            _changed()
        if self._path_snapshot() != self._path_identity:
            _changed()
        if self._handle_snapshot() != self._handle_identity:
            _changed()

    def _read_whole(self):
        self._business(opening=True)
        budget, suffix = self._budget, self._suffix
        budget._start_pass(suffix)
        self._current_fd()
        _clock(self._token)
        actual = _raw_seek(self._raw, 0, os.SEEK_SET)
        _clock(self._token)
        if type(actual) is not int or actual != 0:
            _fail("INVALID_RESULT")
        _clock(self._token)
        digest = _hash_new()
        _clock(self._token)
        total = 0
        while True:
            requested = min(_CHUNK, self.byte_size - total + 1)
            self._current_fd()
            budget._request(suffix, requested)
            # Request validation is not the immediate physical consumer gate.
            _clock(self._token)
            chunk = _raw_read(self._raw, requested)
            if type(chunk) is not bytes:
                _clock(self._token)
                _fail("INVALID_RESULT")
            budget._charge(suffix, chunk)
            if len(chunk) > requested:
                _fail("INVALID_RESULT")
            if not chunk:
                break
            total += len(chunk)
            if total > self.byte_size:
                _changed()
            _clock(self._token)
            actual = _hash_update(digest, chunk)
            _clock(self._token)
            if actual is not None:
                _fail("INVALID_RESULT")
        if total != self.byte_size:
            _changed()
        _clock(self._token)
        hexadecimal = _hash_hexdigest(digest)
        _clock(self._token)
        return _hexadecimal(hexadecimal, result=True)

    def _verify_core(self, *, opening=False):
        self._business(opening=opening)
        self._budget._same_guard()
        self._verify_identities()
        hexadecimal = self._read_whole()
        self._verify_identities()
        if hexadecimal != self._sha256:
            _changed()
        _clock(self._token)
        actual = _source_call(_lease_terminal, self._lease)
        _clock(self._token)
        if actual is not None:
            _fail("INVALID_RESULT")
        _clock(self._token)
        self._budget._complete_pass(self._suffix)

    def verify(self):
        self._budget._raise_first()
        if self._budget._retired:
            _fail("CLOSED")
        try:
            self._verify_core()
        except BaseException as failure:
            self._failed = True
            self._budget._capture(failure)
            self._budget._raise_first()
        return None

    def __enter__(self):
        self._budget._raise_first()
        if self._phase != _NEW:
            if self._phase == _ACTIVE:
                try:
                    _fail("CLOSED")
                except BaseException as failure:
                    self._budget._capture(failure)
                    self._budget._raise_first()
            _fail("CLOSED")
        self._phase = _OPENING
        try:
            self._budget._claim(self._directory, self._suffix)
            self._directory_identity = self._directory_snapshot()
            self._path_identity = self._path_snapshot()
            expected = self._budget._expected[self._suffix]
            if any(self._path_identity[key] != expected[key]
                   for key in _FP_FIELDS - {"sha256"}):
                _changed()
            _clock(self._token)
            try:
                lease = _source_call(_live_reader, self._directory, self._name,
                                     deadline_token=self._token)
            except BaseException:
                self._lease_state = _UNKNOWN
                raise
            legal = type(lease) is live_sql.LiveSqlDeadlineLease
            if legal:
                self._lease, self._lease_state = lease, _PREPARED
            else:
                self._lease_state = _UNKNOWN
            _clock(self._token)
            if not legal:
                _fail("INVALID_RESULT")
            _clock(self._token)
            try:
                raw = _source_call(_lease_enter, lease)
            except BaseException:
                self._lease_state = _UNKNOWN
                raise
            legal = type(raw) is io.FileIO
            if legal:
                self._raw, self._entered, self._lease_state = raw, True, _ACTIVE
            else:
                self._lease_state = _UNKNOWN
            _clock(self._token)
            if not legal or raw.closefd is not False:
                _fail("INVALID_RESULT")
            self._handle_identity = self._handle_snapshot()
            if ({key: value for key, value in self._path_identity.items() if key != "ctime_ns"}
                    != {key: value for key, value in self._handle_identity.items() if key != "ctime_ns"}):
                _changed()
            hexadecimal = self._read_whole()
            self._verify_identities()
            if hexadecimal != expected["sha256"]:
                _changed()
            self._sha256 = hexadecimal
            self._budget._complete_pass(self._suffix)
            self._verify_core(opening=True)
            _clock(self._token)
            self._phase = _ACTIVE
            self._cache_lower_close_receipt()
            self._budget._raise_first()
            _clock(self._token)
            self._enter_returned = True
            return self
        except BaseException as failure:
            self._failed = True
            self._budget._capture(failure)
            self._cleanup()
            self._budget._raise_first()

    def _cleanup_clock(self):
        try:
            _clock(self._token)
        except BaseException as failure:
            self._failed = True
            self._budget._capture(failure, cleanup=True)

    def _cleanup(self):
        if self._phase == _RETIRED:
            return  # Never repeat the lower getter or cleanup after retirement.
        self._failed = self._failed or self._budget._first is not None
        close_candidate = False
        if self._entered:
            self._cleanup_clock()
            self._entered, self._lease_state = False, _ATTEMPTED
            self._exit_attempted = True
            primary, tb = self._budget._first, self._budget._first_tb
            try:
                actual = _lease_exit(self._lease,
                    type(primary) if primary is not None else None, primary, tb)
            except BaseException as failure:
                self._lease_state = _UNKNOWN
                self._failed = True
                self._budget._capture(failure, cleanup=True)
                self._cache_lower_close_receipt()
            else:
                close_candidate = primary is None and actual is None
                self._lease_state = _UNKNOWN
                if actual is not None:
                    self._failed = True
                    self._budget._capture(LiveSqlHeldDeadlineError("INVALID_RESULT"), cleanup=True)
                self._cache_lower_close_receipt()
            self._cleanup_clock()
        else:
            # Failed lower enter owns partial cleanup; cache, never call exit.
            self._cache_lower_close_receipt()
        self._phase = _RETIRED
        self._cleanup_clock()
        if close_candidate and self._budget._first is None:
            self._lease_state = _CLOSED
        # Logical retirement follows fixed exit and its gates, not scan five.
        self._budget._fixed_exit_done(self._suffix)
        self._failed = self._failed or self._budget._first is not None

    def __exit__(self, _kind, primary, traceback):
        if self._phase != _ACTIVE:
            self._budget._raise_first()
            _fail("CLOSED")
        if primary is not None:
            self._failed = True
            self._budget._capture(primary, original_tb=traceback)
        elif self._budget._first is None:
            try:
                self.verify()
                self._terminal_verified = True
            except BaseException as failure:
                self._failed = True
                self._budget._capture(failure)
        self._cleanup()
        if self._budget._first is not None:
            if self._budget._first is primary:
                return None
            self._budget._raise_first()
        actual = self._lower_close_receipt
        if (not self._terminal_verified or self._lease_state != _CLOSED
                or not self._enter_returned or not self._exit_attempted
                or type(actual) is not live_sql.LiveSqlCloseReceiptV1
                or actual.phase != _RETIRED or actual.normal_exit is not True):
            self._failed = True
            self._budget._capture(LiveSqlHeldDeadlineError("INVALID_RESULT"))
            self._budget._raise_first()
        self._normal_exit = True
        return None


def held_file_locked(directory, suffix, *, read_budget, deadline_token):
    return DeadlineLiveSqlHeldFile(directory, suffix, read_budget=read_budget,
                                   deadline_token=deadline_token)
