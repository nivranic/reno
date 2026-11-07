"""Copy and hash original SQLite bytes under one existing absolute deadline.

This explicit layer borrows exact directory/private owners and fixed leases.
Its three actual-byte ledgers do not consume namespace control bytes. It does
not open SQLite, own TEMP cleanup, grant write authority or prove source_closed.
Opaque consumers remain cooperative; owningTrue consumer replacements are
outside the direct FileIO(closefd=False) contract.
"""
from __future__ import annotations

import hashlib
import io
import math
import os
import time

from . import personal_authority_directory_deadline as directories
from . import personal_authority_live_sql_deadline as live_sql
from . import personal_authority_namespace3_deadline as deadline
from . import personal_preservation_source as preservation
from . import storage_readonly as readonly
from . import storage_source as source
from . import sync_media as safe

_monotonic = time.monotonic
_open_fd, _fstat, _close_fd, _file_io = os.open, os.fstat, os.close, io.FileIO
_sha256 = hashlib.sha256
_CHUNK = 128 * 1024
_SUFFIXES = ("", "-wal")
_NAMES = {"": "image.sqlite", "-wal": "image.sqlite-wal"}
_EMPTY, _OWNED, _ATTEMPTED = "empty", "owned", "attempted"
_CLOSED, _UNKNOWN = "closed", "unknown"


class ImageBytesDeadlineError(ValueError):
    def __init__(self, suffix):
        self.code = "IMAGE_BYTES_DEADLINE_" + suffix
        super().__init__(self.code)


Error = ImageBytesDeadlineError


def _fail(suffix):
    raise ImageBytesDeadlineError(suffix) from None


def _note(primary, note="IMAGE_BYTES_DEADLINE_CLEANUP_FAILED"):
    try:
        BaseException.add_note(primary, note)
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


def _raw_read(raw, requested):
    return io.FileIO.read(raw, requested)


def _raw_write(raw, chunk):
    return io.FileIO.write(raw, chunk)


def _raw_fileno(raw):
    return io.FileIO.fileno(raw)


def _retire_raw(raw):
    return io.FileIO.close(raw)


def _hash_update(digest, chunk):
    return digest.update(chunk)


def _hash_hexdigest(digest):
    return digest.hexdigest()


def _initialize_file(owner, path):
    return preservation._PersonalPrivateDirectory.initialize_file(owner, path)


def _verify_private(owner):
    return preservation._PersonalPrivateDirectory._verify(owner)


def _live_reader(directory, name, *, deadline_token):
    return live_sql.source_reader(directory, name, deadline_token=deadline_token)


def _private_reader(directory, name):
    return directories.DeadlineDirectory.open_file(directory, name)


def _lease_exit(lease, kind, primary, traceback):
    if type(lease) is live_sql.LiveSqlDeadlineLease:
        return live_sql.LiveSqlDeadlineLease.__exit__(lease, kind, primary, traceback)
    if type(lease) is directories.DeadlineReadonlyLease:
        return directories.DeadlineReadonlyLease.__exit__(lease, kind, primary, traceback)
    _fail("INVALID_ARGUMENT")


def _signature(value):
    if (type(value) is not tuple or len(value) != 5
            or any(type(item) is not int for item in value) or value[2] < 0):
        _fail("INVALID_ARGUMENT")
    return tuple(item for item in value)


def _suffix(value):
    if type(value) is not str or value not in _SUFFIXES:
        _fail("INVALID_ARGUMENT")
    return value


def _path_signature(owner, name):
    token = owner.deadline_token
    _clock(token)
    info = owner.directory.entry_stat(name)
    _clock(token)
    return source.file_signature(info)


def _handle_signature(raw, token, known_fd=None):
    _clock(token)
    actual = _raw_fileno(raw)
    _clock(token)
    if (type(actual) is not int or actual < 0
            or (known_fd is not None and actual != known_fd)):
        _fail("INVALID_RESULT")
    _clock(token)
    info = _fstat(actual)
    _clock(token)
    return actual, source.file_signature(info)


def _exit_selected(lease, primary):
    # Never add a preclock that could prevent cleanup after expiry.
    traceback = (BaseException.__dict__["__traceback__"].__get__(primary)
                 if primary is not None else None)
    result = _lease_exit(lease, type(primary) if primary is not None else None,
                         primary, traceback)
    if result is not None:
        _fail("INVALID_RESULT")


class ImageByteBudget:
    """Frozen declarations and independent successful-return byte ledgers."""
    __slots__ = ("_database_name", "_before", "_deadline_token", "_limits",
                 "_actual", "_phases", "_source_directory", "_private_owner",
                 "_copied", "_first", "_retired")

    def __init_subclass__(cls, **kwargs):
        _fail("INVALID_ARGUMENT")

    def __init__(self, database_name, before, *, deadline_token):
        if type(self) is not ImageByteBudget or type(database_name) is not str:
            _fail("INVALID_ARGUMENT")
        token = _token(deadline_token)
        _clock(token)
        safe._entry_name(database_name)
        if (type(before) is not dict
                or any(type(key) is not str for key in before)
                or set(before) != {"", "-wal", "-journal"} or before[""] is None):
            _fail("INVALID_ARGUMENT")
        frozen = {key: None if before[key] is None else _signature(before[key])
                  for key in ("", "-wal", "-journal")}
        if frozen["-journal"] is not None and frozen["-journal"][2] != 0:
            _fail("INVALID_ARGUMENT")
        total = sum(frozen[key][2] for key in _SUFFIXES if frozen[key] is not None)
        count = sum(frozen[key] is not None for key in _SUFFIXES)
        if total > readonly.MAX_BYTES_LIMIT or not 1 <= count <= 2:
            _fail("INVALID_ARGUMENT")
        limits = {"copy_read": total + count, "hash_read": total + count * _CHUNK,
                  "target_write": total}
        if any(type(value) is not int or value < 0 for value in limits.values()):
            _fail("INVALID_ARGUMENT")
        self._database_name, self._before, self._deadline_token = database_name, frozen, token
        self._limits, self._actual = limits, {key: 0 for key in limits}
        self._phases = {key: "new" if frozen[key] is not None else "absent"
                        for key in _SUFFIXES}
        self._source_directory = self._private_owner = None
        self._copied, self._first, self._retired = {}, None, False
        _clock(token)

    @property
    def deadline_token(self):
        return self._deadline_token

    @property
    def evidence(self):
        # Diagnostic only: no clock, provider call or references to mutable state.
        complete = (self._first is None and all(
            phase in ("hash_complete", "absent") for phase in self._phases.values()))
        return {"copy_read": self._actual["copy_read"],
                "hash_read": self._actual["hash_read"],
                "target_write": self._actual["target_write"],
                "copy_read_limit": self._limits["copy_read"],
                "hash_read_limit": self._limits["hash_read"],
                "target_write_limit": self._limits["target_write"],
                "phases": dict(self._phases), "retired": self._retired,
                "complete": complete}

    def _live(self):
        if self._first is not None:
            raise self._first
        if self._retired:
            _fail("CLOSED")
        _clock(self.deadline_token)

    def _failure(self, primary):
        if self._first is None:
            self._first = primary
        self._retired = True
        return self._first

    def _request(self, ledger, requested):
        if type(requested) is not int or requested < 0:
            _fail("INVALID_ARGUMENT")
        if requested > self._limits[ledger] - self._actual[ledger]:
            _fail("LIMIT")
        _clock(self.deadline_token)

    def _charge(self, ledger, actual):
        if type(actual) is not int or actual < 0:
            _fail("INVALID_RESULT")
        # Preserve every legal reported quantity, including rejection/EOF0.
        self._actual[ledger] += actual
        if self._actual[ledger] > self._limits[ledger]:
            _fail("LIMIT")
        _clock(self.deadline_token)

    def _claim_copy(self, directory, owner, suffix):
        if (type(directory) is not directories.DeadlineDirectory
                or type(owner) is not ImagePrivateOwner
                or directory.deadline_token is not self.deadline_token
                or owner.deadline_token is not self.deadline_token):
            _fail("INVALID_ARGUMENT")
        suffix = _suffix(suffix)
        if self._before[suffix] is None:
            _fail("INVALID_ARGUMENT")
        if self._phases[suffix] != "new":
            _fail("CLOSED")
        if self._source_directory is None:
            self._source_directory, self._private_owner = directory, owner
        elif self._source_directory is not directory or self._private_owner is not owner:
            _fail("INVALID_ARGUMENT")
        self._phases[suffix] = "copy_started"
        directory._live()
        owner._verify()
        _clock(self.deadline_token)

    def _claim_hash(self, owner, suffix):
        if (type(owner) is not ImagePrivateOwner
                or owner.deadline_token is not self.deadline_token):
            _fail("INVALID_ARGUMENT")
        suffix = _suffix(suffix)
        if self._before[suffix] is None:
            _fail("INVALID_ARGUMENT")
        if self._phases[suffix] != "copy_complete":
            _fail("CLOSED")
        if self._private_owner is not owner:
            _fail("INVALID_ARGUMENT")
        self._phases[suffix] = "hash_started"
        owner._verify()
        _clock(self.deadline_token)


class ImagePrivateOwner:
    """Borrow an existing private TEMP owner and its ACTIVE noDELETE pin."""
    __slots__ = ("_owner", "_directory", "_deadline_token")

    def __init_subclass__(cls, **kwargs):
        _fail("INVALID_ARGUMENT")

    def __init__(self, private_owner, directory, *, deadline_token):
        if (type(self) is not ImagePrivateOwner
                or type(private_owner) is not preservation._PersonalPrivateDirectory
                or type(directory) is not directories.DeadlineDirectory):
            _fail("INVALID_ARGUMENT")
        token = _token(deadline_token)
        if directory.deadline_token is not token or private_owner.path != directory.path:
            _fail("INVALID_ARGUMENT")
        self._owner, self._directory, self._deadline_token = private_owner, directory, token
        self._verify()

    @property
    def path(self):
        return self.directory.path

    @property
    def directory(self):
        return self._directory

    @property
    def deadline_token(self):
        return self._deadline_token

    def _verify(self):
        if (self.directory.deadline_token is not self.deadline_token
                or self._owner.path != self.directory.path):
            _fail("INVALID_ARGUMENT")
        self.directory._live()
        _clock(self.deadline_token)
        _verify_private(self._owner)
        _clock(self.deadline_token)
        self.directory.verify()
        _clock(self.deadline_token)

    def _initialize(self, path):
        self._verify()
        if path.parent != self.path or path.name not in _NAMES.values():
            _fail("INVALID_ARGUMENT")
        _clock(self.deadline_token)
        _initialize_file(self._owner, path)
        _clock(self.deadline_token)
        self._verify()


class _Target:
    """Own one numeric write FD; raw only retires its non-owning wrapper."""
    __slots__ = ("owner", "name", "token", "fd", "raw")

    def __init__(self, owner, name):
        self.owner, self.name, self.token = owner, name, owner.deadline_token
        self.fd, self.raw = [None, _EMPTY], [None, _EMPTY]

    def open(self):
        self.owner._verify()
        path = self.owner.path / self.name
        _clock(self.token)
        native = safe._native_path(path)
        _clock(self.token)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
        _clock(self.token)
        try:
            fd = _open_fd(native, flags, 0o600)
            if type(fd) is not int or fd < 0:
                _fail("INVALID_RESULT")
        except BaseException as primary:
            self.fd[1] = _UNKNOWN
            _note(primary, "IMAGE_BYTES_DEADLINE_CREATE_UNKNOWN")
            raise
        self.fd[0], self.fd[1] = fd, _OWNED
        _clock(self.token)
        _clock(self.token)
        raw = _file_io(fd, "wb", closefd=False)
        if type(raw) is io.FileIO and raw.closefd is False:
            self.raw[0], self.raw[1] = raw, _OWNED
        _clock(self.token)
        if type(raw) is not io.FileIO or raw.closefd is not False or raw.closed:
            _fail("INVALID_RESULT")
        _, empty = _handle_signature(raw, self.token, fd)
        if empty[2] != 0 or _path_signature(self.owner, self.name)[:3] != empty[:3]:
            raise readonly.ReadonlyImageError("PRIVATE_IMAGE_CHANGED")
        self.owner._initialize(path)
        if (_handle_signature(raw, self.token, fd)[1][:3] != empty[:3]
                or _path_signature(self.owner, self.name)[:3] != empty[:3]):
            raise readonly.ReadonlyImageError("PRIVATE_IMAGE_CHANGED")
        _clock(self.token)

    def cleanup(self, primary):
        first = primary
        if self.raw[1] == _OWNED:
            self.raw[1] = _ATTEMPTED
            try:
                if not self.raw[0].closed:
                    _retire_raw(self.raw[0])
            except BaseException as failure:
                self.raw[1] = _UNKNOWN
                if first is None:
                    first = failure
                else:
                    _note(first)
            else:
                self.raw[1] = _CLOSED
        if self.fd[1] == _OWNED:
            self.fd[1] = _ATTEMPTED
            try:
                _close_fd(self.fd[0])
            except BaseException as failure:
                self.fd[1] = _UNKNOWN
                if first is None:
                    first = failure
                else:
                    _note(first)
            else:
                self.fd[1] = _CLOSED
        return first

    def finish(self, expected_size):
        _, opened = _handle_signature(self.raw[0], self.token, self.fd[0])
        if opened[2] != expected_size:
            raise readonly.ReadonlyImageError("PRIVATE_IMAGE_CHANGED")
        failure = self.cleanup(None)
        if failure is not None:
            raise failure
        _clock(self.token)
        after = _path_signature(self.owner, self.name)
        # Windows timestamps can settle at final write-HANDLE close.
        if after[:3] != opened[:3]:
            raise readonly.ReadonlyImageError("PRIVATE_IMAGE_CHANGED")
        self.owner._verify()
        _clock(self.token)
        return after


def _read(raw, requested, budget, ledger):
    budget._request(ledger, requested)
    chunk = _raw_read(raw, requested)
    if type(chunk) is not bytes:
        _fail("INVALID_RESULT")
    budget._charge(ledger, len(chunk))
    if len(chunk) > requested:
        _fail("INVALID_RESULT")
    return chunk


def _write(raw, chunk, budget):
    offset = 0
    while offset < len(chunk):
        remaining = chunk[offset:]
        budget._request("target_write", len(remaining))
        actual = _raw_write(raw, remaining)
        if type(actual) is not int or actual < 0:
            _fail("INVALID_RESULT")
        budget._charge("target_write", actual)
        if actual == 0 or actual > len(remaining):
            _fail("INVALID_RESULT")
        offset += actual


def _budget(value):
    if type(value) is not ImageByteBudget:
        _fail("INVALID_ARGUMENT")
    # Completed/failing budgets cannot have their historical state rewritten.
    if value._first is not None:
        raise value._first
    if value._retired:
        _fail("CLOSED")
    return value


def copy_file_locked(source_directory, private_owner, suffix, *, byte_budget):
    budget = _budget(byte_budget)
    lease, entered, target, primary, copied = None, False, None, None, None
    try:
        budget._live()
        budget._claim_copy(source_directory, private_owner, suffix)
        expected, token = budget._before[suffix], budget.deadline_token
        _clock(token)
        lease = _live_reader(source_directory, budget._database_name + suffix,
                             deadline_token=token)
        _clock(token)
        if type(lease) is not live_sql.LiveSqlDeadlineLease:
            _fail("INVALID_RESULT")
        _clock(token)
        raw = live_sql.LiveSqlDeadlineLease.__enter__(lease)
        entered = True
        _clock(token)
        if type(raw) is not io.FileIO or raw.closefd is not False:
            _fail("INVALID_RESULT")
        if _handle_signature(raw, token)[1][:4] != expected[:4]:
            raise source.SourceReadError("SOURCE_CHANGED")
        target = _Target(private_owner, _NAMES[suffix])
        target.open()
        remaining = expected[2]
        while remaining:
            chunk = _read(raw, min(_CHUNK, remaining), budget, "copy_read")
            if not chunk:
                raise source.SourceReadError("SOURCE_CHANGED")
            _write(target.raw[0], chunk, budget)
            remaining -= len(chunk)
        if _read(raw, 1, budget, "copy_read"):
            raise source.SourceReadError("SOURCE_CHANGED")
        copied = target.finish(expected[2])
    except BaseException as failure:
        primary = budget._failure(failure)
    if target is not None:
        primary = target.cleanup(primary)
        if primary is not None:
            primary = budget._failure(primary)
    if entered:
        try:
            _exit_selected(lease, primary)
        except BaseException as failure:
            if primary is None:
                primary = budget._failure(failure)
            elif failure is not primary:
                _note(primary)
    if primary is not None:
        raise primary
    try:
        _clock(budget.deadline_token)
        budget._copied[suffix] = copied
        budget._phases[suffix] = "copy_complete"
    except BaseException as failure:
        raise budget._failure(failure)
    return None


def private_sha_locked(private_owner, suffix, *, byte_budget):
    budget = _budget(byte_budget)
    lease, entered, primary, hexadecimal = None, False, None, None
    try:
        budget._live()
        budget._claim_hash(private_owner, suffix)
        token, name = budget.deadline_token, _NAMES[suffix]
        copied = budget._copied[suffix]
        if _path_signature(private_owner, name) != copied:
            raise readonly.ReadonlyImageError("PRIVATE_IMAGE_CHANGED")
        _clock(token)
        digest = _sha256()
        _clock(token)
        _clock(token)
        lease = _private_reader(private_owner.directory, name)
        _clock(token)
        if type(lease) is not directories.DeadlineReadonlyLease:
            _fail("INVALID_RESULT")
        _clock(token)
        buffer = directories.DeadlineReadonlyLease.__enter__(lease)
        entered = True
        _clock(token)
        if type(buffer) is not io.BufferedReader:
            _fail("INVALID_RESULT")
        raw = buffer.raw
        if type(raw) is not io.FileIO or raw.closefd is not False:
            _fail("INVALID_RESULT")
        known_fd, opened = _handle_signature(raw, token)
        if opened[:4] != copied[:4]:
            raise readonly.ReadonlyImageError("PRIVATE_IMAGE_CHANGED")
        total, expected_size = 0, budget._before[suffix][2]
        while True:
            chunk = _read(raw, _CHUNK, budget, "hash_read")
            if not chunk:
                break
            total += len(chunk)
            if total > expected_size:
                raise readonly.ReadonlyImageError("PRIVATE_IMAGE_CHANGED")
            _clock(token)
            result = _hash_update(digest, chunk)
            _clock(token)
            if result is not None:
                _fail("INVALID_RESULT")
        if total != expected_size:
            raise readonly.ReadonlyImageError("PRIVATE_IMAGE_CHANGED")
        if (_handle_signature(raw, token, known_fd)[1] != opened
                or _path_signature(private_owner, name) != copied):
            raise readonly.ReadonlyImageError("PRIVATE_IMAGE_CHANGED")
        private_owner._verify()
        _clock(token)
        hexadecimal = _hash_hexdigest(digest)
        _clock(token)
        if (type(hexadecimal) is not str or len(hexadecimal) != 64
                or any(char not in "0123456789abcdef" for char in hexadecimal)):
            _fail("INVALID_RESULT")
    except BaseException as failure:
        primary = budget._failure(failure)
    if entered:
        try:
            _exit_selected(lease, primary)
        except BaseException as failure:
            if primary is None:
                primary = budget._failure(failure)
            elif failure is not primary:
                _note(primary)
    if primary is not None:
        raise primary
    try:
        private_owner._verify()
        _clock(budget.deadline_token)
        budget._phases[suffix] = "hash_complete"
        if all(phase in ("hash_complete", "absent") for phase in budget._phases.values()):
            budget._retired = True
    except BaseException as failure:
        raise budget._failure(failure)
    return hexadecimal
