"""Compose one private committed SQLite image under an existing deadline.

The source directory and actual EX are borrowed. This lease owns only its new
TEMP, private directory pin and SQL sidecar; internal normal_exit is not an
outer source_closed or product-completion claim. Raw yielded SQL APIs, legacy
TEMP/ACL/native internals and continuous live source readers remain opaque.
"""
from __future__ import annotations

import json
import math
import sqlite3
import time

from . import personal_authority_directory_deadline as directories
from . import personal_authority_image_bytes_deadline as images
from . import personal_authority_namespace3_deadline as deadline
from . import personal_authority_sql_bootstrap_deadline as bootstrap
from . import personal_preservation_source as preservation
from . import storage_lifecycle as lifecycle
from . import storage_readonly as readonly
from . import storage_source as source

_monotonic = time.monotonic
_NEW, _ACTIVE, _RETIRED = "new", "active", "retired"
_EMPTY, _OWNED, _PREPARED = "empty", "owned", "prepared"
_ATTEMPTED, _CLOSED, _UNKNOWN = "attempted", "closed", "unknown"
_SUFFIXES = ("", "-wal", "-journal")
_EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
_BYTE_FIELDS = frozenset(("copy_read", "hash_read", "target_write",
    "copy_read_limit", "hash_read_limit", "target_write_limit", "phases",
    "retired", "complete"))
_SQL_FIELDS = frozenset(("phase", "connection_state", "cursor_states",
    "bootstrap_complete", "normal_exit", "previous_limits", "limits_restored",
    "exit_in_transaction", "retired"))


class CommittedImageDeadlineError(ValueError):
    def __init__(self, suffix):
        self.code = "COMMITTED_IMAGE_DEADLINE_" + suffix
        super().__init__(self.code)


Error = CommittedImageDeadlineError


def _fail(suffix):
    raise CommittedImageDeadlineError(suffix) from None


def _note(primary):
    try:
        BaseException.add_note(primary, "COMMITTED_IMAGE_DEADLINE_CLEANUP_FAILED")
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


def _guard_verify(guard):
    return lifecycle.StorageGuard.verify(guard)


def _source_state(directory, name):
    return source.source_state(directory, name)


def _private_directory():
    return preservation._PersonalPrivateDirectory()


def _directory(path, *, deadline_token, base):
    return directories.DeadlineDirectory(path, deadline_token=deadline_token,
                                         base=base)


def _directory_enter(directory):
    return directories.DeadlineDirectory.__enter__(directory)


def _directory_exit(directory, kind, primary, tb):
    return directories.DeadlineDirectory.__exit__(directory, kind, primary, tb)


def _image_owner(temp, pin, *, deadline_token):
    return images.ImagePrivateOwner(temp, pin, deadline_token=deadline_token)


def _byte_budget(name, before, *, deadline_token):
    return images.ImageByteBudget(name, before, deadline_token=deadline_token)


def _copy_file(source_dir, image_owner, suffix, *, byte_budget):
    return images.copy_file_locked(source_dir, image_owner, suffix,
                                    byte_budget=byte_budget)


def _private_sha(image_owner, suffix, *, byte_budget):
    return images.private_sha_locked(image_owner, suffix, byte_budget=byte_budget)


def _sql_lease(image_owner, *, byte_budget):
    return bootstrap.private_connection(image_owner, byte_budget=byte_budget)


def _sql_enter(lease):
    return bootstrap.SqlBootstrapDeadlineLease.__enter__(lease)


def _sql_exit(lease, kind, primary, tb):
    return bootstrap.SqlBootstrapDeadlineLease.__exit__(lease, kind, primary, tb)


def _temp_cleanup(temp):
    return preservation._PersonalPrivateDirectory.cleanup(temp)


def _image_verify_source(image):
    return DeadlineCommittedImage._verify_source_core(image)


def _signature(value):
    if (type(value) is not tuple or len(value) != 5
            or any(type(item) is not int for item in value) or value[2] < 0):
        _fail("INVALID_RESULT")
    return tuple(item for item in value)


def _freeze_source(value):
    if (type(value) is not dict or any(type(key) is not str for key in value)
            or set(value) != set(_SUFFIXES) or value[""] is None):
        _fail("INVALID_RESULT")
    return {key: None if value[key] is None else _signature(value[key])
            for key in _SUFFIXES}


def _detach(value):
    # Only validated plain public evidence reaches this recursive copier.
    if type(value) is dict:
        return {key: _detach(item) for key, item in value.items()}
    if type(value) is tuple:
        return tuple(_detach(item) for item in value)
    return value


def _byte_snapshot(value):
    if (type(value) is not dict or any(type(key) is not str for key in value)
            or set(value) != _BYTE_FIELDS):
        _fail("INVALID_RESULT")
    for key in _BYTE_FIELDS - {"phases", "retired", "complete"}:
        if type(value[key]) is not int or value[key] < 0:
            _fail("INVALID_RESULT")
    phases = value["phases"]
    if (type(phases) is not dict or set(phases) != {"", "-wal"}
            or any(type(key) is not str for key in phases)
            or any(type(item) is not str or item not in {
                "new", "absent", "copy_started", "copy_complete",
                "hash_started", "hash_complete"} for item in phases.values())
            or type(value["retired"]) is not bool
            or type(value["complete"]) is not bool):
        _fail("INVALID_RESULT")
    return _detach(value)


def _sql_snapshot(value):
    if (type(value) is not dict or any(type(key) is not str for key in value)
            or set(value) != _SQL_FIELDS):
        _fail("INVALID_RESULT")
    states = {_EMPTY, _OWNED, _ATTEMPTED, _CLOSED, _UNKNOWN}
    cursors, previous = value["cursor_states"], value["previous_limits"]
    if (type(value["phase"]) is not str
            or value["phase"] not in {_NEW, _ACTIVE, _RETIRED}
            or type(value["connection_state"]) is not str
            or value["connection_state"] not in states
            or type(cursors) is not tuple or len(cursors) != 4
            or any(type(item) is not str or item not in states for item in cursors)
            or type(previous) is not tuple or len(previous) != 2
            or any(item is not None and (type(item) is not int or item < 0)
                   for item in previous)
            or any(type(value[key]) is not bool for key in (
                "bootstrap_complete", "normal_exit", "limits_restored", "retired"))
            or (value["exit_in_transaction"] is not None
                and type(value["exit_in_transaction"]) is not bool)
            or value["retired"] != (value["phase"] == _RETIRED)):
        _fail("INVALID_RESULT")
    return _detach(value)


def _sql_aggregate(value, *, not_entered):
    con, cursors = value["connection_state"], value["cursor_states"]
    complete, normal = value["bootstrap_complete"], value["normal_exit"]
    all_empty = con == _EMPTY and all(item == _EMPTY for item in cursors)
    previous_empty = all(item is None for item in value["previous_limits"])
    if complete and (not value["limits_restored"]
            or any(item is None for item in value["previous_limits"])
            or any(item != _CLOSED for item in cursors)):
        return _UNKNOWN
    if normal and (not complete or value["phase"] != _RETIRED
                   or value["exit_in_transaction"] is None):
        return _UNKNOWN
    if value["limits_restored"] and any(item is None for item in value["previous_limits"]):
        return _UNKNOWN
    if not complete and value["exit_in_transaction"] is not None:
        return _UNKNOWN
    unused = (all_empty and previous_empty and not complete and not normal
              and not value["limits_restored"] and value["exit_in_transaction"] is None)
    if value["phase"] == _NEW and unused and not_entered:
        return _NEW
    if value["phase"] == _RETIRED:
        if unused:
            return _EMPTY
        if con == _CLOSED and all(item in {_EMPTY, _CLOSED} for item in cursors):
            return _CLOSED
    return _UNKNOWN


class DeadlineCommittedImage:
    """Readonly interface over the one image created by its exact lease."""
    __slots__ = ("_lease", "_fingerprint", "_active")

    def __init_subclass__(cls, **kwargs):
        _fail("INVALID_ARGUMENT")

    def __init__(self, *args, **kwargs):
        # Public callers cannot inject a Connection, guard or fingerprint.
        _fail("INVALID_ARGUMENT")

    @property
    def connection(self):
        return self._lease._con

    @property
    def deadline_token(self):
        return self._lease._token

    @property
    def source_fingerprint(self):
        return json.loads(self._fingerprint)

    def require_active(self):
        lease = self._lease
        lease._raise_first()
        if self._active is not True or lease._phase != _ACTIVE:
            raise readonly.ReadonlyImageError("IMAGE_CLOSED") from None
        try:
            _clock(lease._token)
            lease._same_guard()
            _clock(lease._token)
        except BaseException as failure:
            lease._capture(failure)
            self._active = False
            lease._raise_first()
        return None

    def _verify_source_core(self):
        lease = self._lease
        lease._raise_first()
        _clock(lease._token)
        if not self._active:
            raise readonly.ReadonlyImageError("IMAGE_CLOSED") from None
        lease._same_guard()
        lease._source_equal()
        _clock(lease._token)
        actual = _guard_verify(lease._guard)
        _clock(lease._token)
        if actual is not lease._guard:
            _fail("INVALID_RESULT")
        return self.source_fingerprint

    def verify_source(self):
        lease = self._lease
        lease._raise_first()
        try:
            _clock(lease._token)
            actual = _image_verify_source(self)
            _clock(lease._token)
            if type(actual) is not dict or actual != self.source_fingerprint:
                _fail("INVALID_RESULT")
            return actual
        except BaseException as failure:
            if lease._phase == _ACTIVE:
                lease._capture(failure)
                self._active = False
                lease._raise_first()
            raise


class CommittedImageDeadlineLease:
    """Single entry; own TEMP/private pin/SQL, borrow source pin and EX."""
    __slots__ = ("_profile", "_directory", "_token", "_max_bytes", "_guard",
                 "_phase", "_enter_started", "_before", "_temp", "_temp_state",
                 "_pin", "_pin_state", "_pin_entered", "_owner", "_budget",
                 "_byte_evidence", "_sql", "_sql_state", "_sql_entered",
                 "_sql_started", "_bootstrap_evidence", "_con", "_image",
                 "_first", "_first_tb", "_normal_exit", "_private_pin_close_receipt",
                 "_sql_close_receipt", "_temp_cleanup_receipt")

    def __init_subclass__(cls, **kwargs):
        _fail("INVALID_ARGUMENT")

    def __init__(self, profile, directory, *, deadline_token,
                 max_bytes=readonly.DEFAULT_MAX_BYTES, attempts=1):
        if (type(self) is not CommittedImageDeadlineLease
                or type(max_bytes) is not int
                or not 1 <= max_bytes <= readonly.MAX_BYTES_LIMIT
                or type(attempts) is not int or attempts != 1):
            _fail("INVALID_ARGUMENT")
        self._profile, self._directory = profile, directory
        self._token, self._max_bytes = _token(deadline_token), max_bytes
        self._guard = None
        self._phase, self._enter_started = _NEW, False
        self._before = self._temp = self._pin = self._owner = self._budget = None
        self._temp_state = self._pin_state = self._sql_state = _EMPTY
        self._pin_entered = self._sql_entered = self._sql_started = False
        self._byte_evidence = self._bootstrap_evidence = None
        self._sql = self._con = self._image = None
        self._first = self._first_tb = None
        self._normal_exit = False
        self._private_pin_close_receipt = None
        self._sql_close_receipt = self._temp_cleanup_receipt = None
        self._bind_source()
        _clock(self._token)
        guard = _require_guard(profile, exclusive=True)
        _clock(self._token)
        if type(guard) is not lifecycle.StorageGuard:
            _fail("INVALID_RESULT")
        self._guard = guard

    @property
    def evidence(self):
        # Cached public snapshots only: zero clocks or provider reads here.
        return {"phase": self._phase, "temp_state": self._temp_state,
                "private_pin_state": self._pin_state, "sql_state": self._sql_state,
                "byte_evidence": _detach(self._byte_evidence),
                "bootstrap_evidence": _detach(self._bootstrap_evidence),
                "normal_exit": self._normal_exit, "source_directory_borrowed": True}

    @property
    def private_pin_close_receipt(self):
        # Own cached exact DTO only; no clock/provider/lower/native reads.
        return self._private_pin_close_receipt

    def _cache_private_pin_close_receipt(self):
        if self._pin is None:
            return
        try:
            if type(self._pin) is not directories.DeadlineDirectory:
                _fail("INVALID_RESULT")
            actual = self._pin.close_receipt
            if type(actual) is not directories.DirectoryCloseReceiptV1:
                _fail("INVALID_RESULT")
            self._private_pin_close_receipt = actual
        except BaseException as failure:
            self._private_pin_close_receipt = None
            self._capture(failure, cleanup=True)

    @property
    def sql_close_receipt(self):
        return self._sql_close_receipt

    @property
    def temp_cleanup_receipt(self):
        return self._temp_cleanup_receipt

    def _cache_sql_close_receipt(self):
        if self._sql is None:
            return
        try:
            if type(self._sql) is not bootstrap.SqlBootstrapDeadlineLease:
                _fail("INVALID_RESULT")
            actual = self._sql.close_receipt
            if type(actual) is not bootstrap.SqlBootstrapCloseReceiptV1:
                _fail("INVALID_RESULT")
            self._sql_close_receipt = actual
        except BaseException as failure:
            self._sql_close_receipt = None
            self._capture(failure, cleanup=True)

    def _cache_temp_cleanup_receipt(self):
        if self._temp is None:
            return
        try:
            if type(self._temp) is not preservation._PersonalPrivateDirectory:
                _fail("INVALID_RESULT")
            actual = self._temp.cleanup_receipt
            if type(actual) is not preservation.TempCleanupReceiptV1:
                _fail("INVALID_RESULT")
            self._temp_cleanup_receipt = actual
        except BaseException as failure:
            self._temp_cleanup_receipt = None
            self._capture(failure, cleanup=True)

    def _capture(self, failure, *, original_tb=None, cleanup=False):
        if self._first is None:
            self._first = failure
            self._first_tb = (BaseException.__dict__["__traceback__"].__get__(failure)
                              if original_tb is None else original_tb)
        elif cleanup and failure is not self._first:
            _note(self._first)

    def _raise_first(self):
        if self._first is not None:
            raise BaseException.with_traceback(self._first, self._first_tb) from None

    def _bind_source(self):
        if (type(self._profile) is not lifecycle.StorageProfile
                or type(self._directory) is not directories.DeadlineDirectory
                or self._directory.deadline_token is not self._token
                or self._directory.path != self._profile.database.parent):
            _fail("INVALID_ARGUMENT")
        _clock(self._token)
        directories.DeadlineDirectory._live(self._directory)
        _clock(self._token)

    def _same_guard(self):
        self._bind_source()
        _clock(self._token)
        current = _require_guard(self._profile, exclusive=True)
        _clock(self._token)
        if current is not self._guard:
            raise readonly.ReadonlyImageError("IMAGE_GUARD_CHANGED") from None

    def _source_snapshot(self):
        _clock(self._token)
        try:
            actual = _source_state(self._directory, self._profile.database.name)
        except source.SourceReadError as failure:
            if failure.code == "SOURCE_CHANGED":
                raise readonly.ReadonlyImageError("SOURCE_CHANGED", retriable=True) from None
            if failure.code == "SOURCE_UNSAFE":
                raise readonly.ReadonlyImageError("SOURCE_UNSAFE") from None
            raise
        except FileNotFoundError:
            if self._before is None:
                raise readonly.ReadonlyImageError("SOURCE_MISSING") from None
            raise readonly.ReadonlyImageError("SOURCE_CHANGED", retriable=True) from None
        _clock(self._token)
        return _freeze_source(actual)

    def _source_equal(self):
        if self._source_snapshot() != self._before:
            raise readonly.ReadonlyImageError("SOURCE_CHANGED", retriable=True) from None

    def _cache_bytes(self):
        if self._budget is None:
            return
        try:
            self._byte_evidence = _byte_snapshot(self._budget.evidence)
        except BaseException as failure:
            self._byte_evidence = None
            self._capture(failure, cleanup=True)

    def _cache_bootstrap(self, *, aggregate=True):
        if self._sql is None:
            return
        try:
            actual = _sql_snapshot(self._sql.evidence)
            self._bootstrap_evidence = actual
            if aggregate:
                self._sql_state = _sql_aggregate(actual, not_entered=not self._sql_started)
        except BaseException as failure:
            self._bootstrap_evidence = None
            self._sql_state = _UNKNOWN
            self._capture(failure, cleanup=True)
        self._cache_sql_close_receipt()

    def _prepare_private(self):
        _clock(self._token)
        try:
            temp = _private_directory()
        except BaseException:
            self._temp_state = _UNKNOWN
            raise
        legal = type(temp) is preservation._PersonalPrivateDirectory
        if legal:
            self._temp, self._temp_state = temp, _OWNED
            self._cache_temp_cleanup_receipt()
        else:
            self._temp_state = _UNKNOWN
        _clock(self._token)
        self._raise_first()
        if not legal:
            _fail("INVALID_RESULT")
        _clock(self._token)
        try:
            pin = _directory(temp.path, deadline_token=self._token, base=temp.path)
        except BaseException:
            self._pin_state = _UNKNOWN
            raise
        legal = type(pin) is directories.DeadlineDirectory
        if legal:
            self._pin, self._pin_state = pin, _PREPARED
        else:
            self._pin_state = _UNKNOWN
        _clock(self._token)
        if not legal:
            _fail("INVALID_RESULT")
        _clock(self._token)
        try:
            actual = _directory_enter(pin)
        except BaseException as failure:
            self._pin_state = _UNKNOWN
            self._capture(failure)
            self._cache_private_pin_close_receipt()
            raise
        legal = type(actual) is directories.DeadlineDirectory and actual is pin
        if legal:
            self._pin_entered, self._pin_state = True, _ACTIVE
        else:
            self._pin_state = _UNKNOWN
        self._cache_private_pin_close_receipt()
        self._raise_first()
        _clock(self._token)
        if not legal:
            _fail("INVALID_RESULT")
        _clock(self._token)
        owner = _image_owner(temp, pin, deadline_token=self._token)
        if type(owner) is images.ImagePrivateOwner:
            self._owner = owner
        _clock(self._token)
        if self._owner is None:
            _fail("INVALID_RESULT")

    def _copy_stage(self, suffix):
        try:
            _clock(self._token)
            try:
                actual = _copy_file(self._directory, self._owner, suffix,
                                    byte_budget=self._budget)
            except source.SourceReadError as failure:
                if failure.code == "SOURCE_CHANGED":
                    raise readonly.ReadonlyImageError("SOURCE_CHANGED", retriable=True) from None
                if failure.code == "SOURCE_UNSAFE":
                    raise readonly.ReadonlyImageError("SOURCE_UNSAFE") from None
                raise
        except BaseException as failure:
            self._capture(failure)
            self._cache_bytes()
            self._raise_first()
        self._cache_bytes()
        self._raise_first()
        _clock(self._token)
        if actual is not None:
            _fail("INVALID_RESULT")

    def _hash_stage(self, suffix):
        try:
            _clock(self._token)
            actual = _private_sha(self._owner, suffix, byte_budget=self._budget)
        except BaseException as failure:
            self._capture(failure)
            self._cache_bytes()
            self._raise_first()
        self._cache_bytes()
        self._raise_first()
        _clock(self._token)
        if (type(actual) is not str or len(actual) != 64
                or any(char not in "0123456789abcdef" for char in actual)):
            _fail("INVALID_RESULT")
        return actual

    def _prepare_sql(self):
        _clock(self._token)
        try:
            lease = _sql_lease(self._owner, byte_budget=self._budget)
        except BaseException:
            self._sql_state = _UNKNOWN
            raise
        legal = type(lease) is bootstrap.SqlBootstrapDeadlineLease
        if legal:
            self._sql, self._sql_state = lease, _NEW
            self._cache_bootstrap()
        else:
            self._sql_state = _UNKNOWN
        _clock(self._token)
        self._raise_first()
        if not legal:
            _fail("INVALID_RESULT")
        _clock(self._token)
        self._sql_started = True
        try:
            con = _sql_enter(lease)
        except BaseException as failure:
            self._capture(failure)
            self._cache_bootstrap()
            self._raise_first()
        legal = type(con) is sqlite3.Connection
        if legal:
            self._con, self._sql_entered, self._sql_state = con, True, _ACTIVE
        else:
            self._sql_state = _UNKNOWN
        self._cache_bootstrap(aggregate=not legal)
        _clock(self._token)
        self._raise_first()
        if not legal:
            _fail("INVALID_RESULT")

    def _make_image(self, copied_sha):
        _clock(self._token)
        fingerprint = {"format_version": 1,
            "profile_identity": list(self._profile.identity), "files": {
                suffix: None if info is None else {
                    "device": str(info[0]), "inode": str(info[1]),
                    "byte_size": info[2], "mtime_ns": str(info[3]),
                    "ctime_ns": str(info[4]), "sha256": (
                        _EMPTY_SHA256 if suffix == "-journal" else copied_sha[suffix])}
                for suffix, info in self._before.items()}}
        encoded = json.dumps(fingerprint, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")
        image = object.__new__(DeadlineCommittedImage)
        image._lease, image._fingerprint, image._active = self, encoded, True
        self._image = image
        _clock(self._token)
        return image

    def __enter__(self):
        self._raise_first()
        if self._phase != _NEW or self._enter_started:
            _fail("CLOSED")
        self._enter_started = True
        try:
            self._same_guard()
            self._before = self._source_snapshot()
            journal = self._before["-journal"]
            if journal is not None and journal[2] != 0:
                raise readonly.ReadonlyImageError("SOURCE_ROLLBACK_JOURNAL_PRESENT",
                                                  retriable=True) from None
            if sum(self._before[key][2] for key in ("", "-wal")
                   if self._before[key] is not None) > self._max_bytes:
                raise readonly.ReadonlyImageError("SOURCE_SNAPSHOT_TOO_LARGE") from None
            _clock(self._token)
            budget = _byte_budget(self._profile.database.name, self._before,
                                  deadline_token=self._token)
            if type(budget) is images.ImageByteBudget:
                self._budget = budget
                self._cache_bytes()
            _clock(self._token)
            self._raise_first()
            if self._budget is None:
                _fail("INVALID_RESULT")
            self._prepare_private()
            copied_sha = {}
            for suffix in ("", "-wal"):
                if self._before[suffix] is None:
                    copied_sha[suffix] = None
                    continue
                self._copy_stage(suffix)
                copied_sha[suffix] = self._hash_stage(suffix)
            self._source_equal()
            self._prepare_sql()
            self._source_equal()
            self._phase = _ACTIVE
            image = self._make_image(copied_sha)
            image.verify_source()
            _clock(self._token)
            return image
        except BaseException as failure:
            self._capture(failure)
            self._cleanup()
            self._raise_first()

    def _cleanup_clock(self):
        try:
            _clock(self._token)
        except BaseException as failure:
            self._capture(failure, cleanup=True)

    def _close_sql(self):
        if not self._sql_entered:
            return
        self._cleanup_clock()
        self._sql_entered, self._sql_state = False, _ATTEMPTED
        primary, tb = self._first, self._first_tb
        try:
            actual = _sql_exit(self._sql, type(primary) if primary is not None else None,
                               primary, tb)
        except BaseException as failure:
            self._capture(failure, cleanup=True)
            self._cache_bootstrap()
        else:
            # A public physical CLOSED fact survives an alias post-return fault.
            self._cache_bootstrap()
            if actual is not None:
                self._capture(CommittedImageDeadlineError("INVALID_RESULT"), cleanup=True)
        self._cleanup_clock()

    def _close_pin(self):
        if not self._pin_entered:
            return
        self._cleanup_clock()
        self._pin_entered, self._pin_state = False, _ATTEMPTED
        primary, tb = self._first, self._first_tb
        try:
            actual = _directory_exit(self._pin,
                type(primary) if primary is not None else None, primary, tb)
        except BaseException as failure:
            self._pin_state = _UNKNOWN
            self._capture(failure, cleanup=True)
            self._cache_private_pin_close_receipt()
        else:
            # The old aggregate stays UNKNOWN on a primary-bearing return.
            self._pin_state = _CLOSED if primary is None and actual is None else _UNKNOWN
            if actual is not None:
                self._capture(CommittedImageDeadlineError("INVALID_RESULT"), cleanup=True)
            self._cache_private_pin_close_receipt()
        self._cleanup_clock()

    def _close_temp(self):
        if self._temp_state != _OWNED:
            return
        self._cleanup_clock()
        self._temp_state = _ATTEMPTED
        try:
            actual = _temp_cleanup(self._temp)
        except BaseException as failure:
            self._temp_state = _UNKNOWN
            self._capture(failure, cleanup=True)
        else:
            self._temp_state = _CLOSED if actual is None else _UNKNOWN
            if actual is not None:
                self._capture(CommittedImageDeadlineError("INVALID_RESULT"), cleanup=True)
        self._cache_temp_cleanup_receipt()
        self._cleanup_clock()

    def _cleanup(self):
        if self._image is not None:
            self._image._active = False
        self._phase = _RETIRED
        self._cache_bytes()
        self._close_sql()
        self._close_pin()
        self._close_temp()
        self._cleanup_clock()

    def __exit__(self, _kind, primary, traceback):
        if self._phase != _ACTIVE:
            self._raise_first()
            _fail("CLOSED")
        if primary is not None:
            self._capture(primary, original_tb=traceback)
        elif self._first is None:
            try:
                self._image.verify_source()
            except BaseException as failure:
                self._capture(failure)
        self._cleanup()
        if self._first is not None:
            if self._first is primary:
                return None
            self._raise_first()
        if (self._sql_state != _CLOSED or self._pin_state != _CLOSED
                or self._temp_state != _CLOSED):
            self._capture(CommittedImageDeadlineError("INVALID_RESULT"))
            self._raise_first()
        self._normal_exit = True
        return None


def committed_image_locked(profile, directory, *, deadline_token,
                           max_bytes=readonly.DEFAULT_MAX_BYTES, attempts=1):
    return CommittedImageDeadlineLease(profile, directory,
        deadline_token=deadline_token, max_bytes=max_bytes, attempts=attempts)
