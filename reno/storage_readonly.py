"""Stable committed SQLite images in private TEMP, never source SHM.

Reuse the common no-follow source readers. Consumers own schema-specific
ordinary-table/field authorizers and row/byte/SQL budgets before reading tables;
this layer never assumes the current schema version.
Source signatures are read-consistency evidence, not semantic GC CAS tokens.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from . import storage_lifecycle as lifecycle, storage_source as source, sync_media as safe

DEFAULT_MAX_BYTES = source.DEFAULT_COPY_MAX_BYTES
MAX_BYTES_LIMIT = 2 * 1024 * 1024 * 1024
MAX_ATTEMPTS = source.MAX_CAPTURE_ATTEMPTS
BOOTSTRAP_MAX_BYTES = 1024 * 1024
BOOTSTRAP_VM_STEPS = 50_000


class ReadonlyImageError(ValueError):
    def __init__(self, code, *, retriable=False):
        super().__init__(code)
        self.code, self.retriable = code, retriable


class _Unstable(Exception):
    """Only trusted filesystem adapters create this retry signal."""


def _fail(code, *, retriable=False):
    raise ReadonlyImageError(code, retriable=retriable)


def _fs(operation, *, code="SOURCE_IO_ERROR", missing_changed=False):
    """A single filesystem operation only: never enclose user callbacks."""
    try:
        return operation()
    except source.SourceReadError as exc:
        if exc.code == "SOURCE_CHANGED":
            raise _Unstable() from None
        if exc.code == "SOURCE_UNSAFE":
            _fail("SOURCE_UNSAFE")
        _fail(code)
    except FileNotFoundError:
        if missing_changed:
            raise _Unstable() from None
        _fail("SOURCE_MISSING" if code == "SOURCE_IO_ERROR" else code)
    except safe.SyncError:
        _fail("SOURCE_UNSAFE" if code == "SOURCE_IO_ERROR" else code)
    except OSError:
        _fail(code)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _signature(value):
    if value is None:
        return None
    device, inode, size, modified, changed = value
    return {"device": str(device), "inode": str(inode), "byte_size": size,
            "mtime_ns": str(modified), "ctime_ns": str(changed)}


def _private_sha(path, expected_size, check):
    """Hash the copied original bytes before SQLite can touch TEMP WAL/SHM."""
    digest, size = hashlib.sha256(), 0
    stream = _fs(lambda: path.open("rb"), code="PRIVATE_IMAGE_IO_ERROR")
    try:
        while True:
            check()  # Outside _fs: even OSError/FileNotFoundError stays original.
            chunk = _fs(lambda: stream.read(128 * 1024), code="PRIVATE_IMAGE_IO_ERROR")
            if not chunk:
                break
            size += len(chunk)
            if size > expected_size:
                _fail("PRIVATE_IMAGE_CHANGED")
            digest.update(chunk)
        check()
        if size != expected_size:
            _fail("PRIVATE_IMAGE_CHANGED")
        return digest.hexdigest()
    finally:
        stream.close()


def _readonly_authorizer(action, first, second, schema, _source):
    if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_TRANSACTION):
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ and schema == "main":
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_PRAGMA and first in {"table_info", "table_xinfo", "index_list", "index_info", "index_xinfo", "foreign_key_list", "database_list"}:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION and second in {"count", "length", "typeof", "coalesce", "ifnull", "substr", "min", "max", "sum"}:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def _open_private(path):
    con = None
    try:
        # No immutable=1: SQLite must interpret the copied committed WAL.
        con = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        # Consumers cannot install their budgets until yield. Bound bootstrap
        # schema loading itself, then hand schema-specific budgets to them.
        previous_length = con.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, BOOTSTRAP_MAX_BYTES)
        previous_sql = con.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, BOOTSTRAP_MAX_BYTES)
        steps = 0
        def budget():
            nonlocal steps
            steps += 1000
            return int(steps > BOOTSTRAP_VM_STEPS)
        con.set_progress_handler(budget, 1000)
        con.execute("PRAGMA query_only=ON")
        con.execute("PRAGMA trusted_schema=OFF")
        con.set_authorizer(_readonly_authorizer)
        con.execute("BEGIN")
        con.execute("SELECT rootpage FROM sqlite_schema LIMIT 1").fetchall()
        con.set_progress_handler(None, 0)
        con.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, previous_length)
        con.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, previous_sql)
        return con
    except sqlite3.Error:
        if con is not None:
            con.close()
        _fail("SOURCE_DATABASE_INVALID")


class CommittedImage:
    def __init__(self, connection, profile, guard, directory, source_state, fingerprint, check):
        self.connection = connection
        self._profile, self._guard, self._directory = profile, guard, directory
        self._source_state, self._fingerprint = source_state, _json(fingerprint)
        self._check, self._active = check, True

    @property
    def source_fingerprint(self):
        # Return a detached copy; callers cannot weaken future verification.
        return json.loads(self._fingerprint)

    def verify_source(self):
        self._check()
        try:
            self._verify_source_state()
        except _Unstable:
            _fail("SOURCE_CHANGED", retriable=True)
        self._check()
        return self.source_fingerprint

    def _verify_source_state(self):
        if not self._active:
            _fail("IMAGE_CLOSED")
        if lifecycle.require_guard(self._profile) is not self._guard:
            _fail("IMAGE_GUARD_CHANGED")
        current = _fs(lambda: source.source_state(self._directory, self._profile.database.name), missing_changed=True)
        if current != self._source_state:
            raise _Unstable()
        self._guard.verify()


class _PrivateDirectory:
    """Own a physical TEMP directory; replacement paths are never cleaned."""
    def __init__(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="reno-readonly-image-")
        # Automatic finalizers only know a pathname. Disable them before any
        # validation can fail; every deletion below rechecks our own identity.
        self.temporary._finalizer.detach()
        self.path = Path(self.temporary.name)
        self.parent = Path(tempfile.gettempdir()).resolve()
        info = self.path.stat(follow_symlinks=False)
        safe._safe_stat(info, directory=True)
        self.identity = info.st_dev, info.st_ino
        parent_info = self.path.parent.stat(follow_symlinks=False)
        safe._safe_stat(parent_info, directory=True)
        self.parent_identity = parent_info.st_dev, parent_info.st_ino
        self._verify()

    def _verify(self):
        if (not self.path.is_absolute() or not self.path.name.startswith("reno-readonly-image-")
                or self.path.resolve() != self.path or self.path.parent.resolve() != self.parent):
            _fail("PRIVATE_IMAGE_UNSAFE")
        info = self.path.stat(follow_symlinks=False)
        parent_info = self.path.parent.stat(follow_symlinks=False)
        safe._safe_stat(info, directory=True)
        safe._safe_stat(parent_info, directory=True)
        if ((info.st_dev, info.st_ino) != self.identity
                or (parent_info.st_dev, parent_info.st_ino) != self.parent_identity):
            _fail("PRIVATE_IMAGE_UNSAFE")

    def cleanup(self):
        self.temporary._finalizer.detach()
        self._verify()
        self.temporary.cleanup()


def _best_effort_note(error, note):
    # A diagnostic annotation must not replace the original failure.
    try:
        BaseException.add_note(error, note)
    except BaseException:
        pass


class _ExitFailure:
    """First original exception/tb; never a resource or permission token."""
    __slots__ = ("first", "first_tb")

    def __init__(self):
        self.first = self.first_tb = None

    def capture(self, failure, *, original_tb=None, note=None):
        if self.first is None:
            self.first = failure
            self.first_tb = (BaseException.__dict__["__traceback__"].__get__(failure)
                if original_tb is None else original_tb)
        if note is not None:
            _best_effort_note(self.first, note)

    def raise_if_any(self):
        if self.first is not None:
            raise BaseException.with_traceback(self.first, self.first_tb) from None


def _cleanup(image, connection, temporary, *, primary=None):
    """Preserve a cancellation/consumer exception if cleanup also fails."""
    if image is not None:
        image._active = False
    failures = False
    if connection is not None:
        try:
            connection.close()
        except BaseException:
            failures = True
    if temporary is not None:
        try:
            temporary.cleanup()
        except BaseException:
            failures = True
    if failures:
        if primary is not None:
            _best_effort_note(primary, "PRIVATE_IMAGE_CLEANUP_FAILED")
        else:
            _fail("PRIVATE_IMAGE_CLEANUP_FAILED")


@contextmanager
def committed_image_locked(profile, *, max_bytes=DEFAULT_MAX_BYTES, attempts=MAX_ATTEMPTS,
                           check_cancelled=None, private_directory_factory=None):
    """Caller holds the whole group before entering; no facilities are created.

    At most two pre-yield capture attempts. A source change after yield raises a
    retriable SOURCE_CHANGED: the caller discards its whole scan and retries.
    Cancellation is checked at file-copy boundaries and every private SHA chunk.
    The common source-copy helper has file-boundary cancellation granularity.
    This accepts the protected live profile only, not arbitrary backup paths.
    A domain can supply its own private directory owner to establish stricter
    platform permissions before any original SQL bytes are copied to TEMP.
    """
    if (type(max_bytes) is not int or not 1 <= max_bytes <= MAX_BYTES_LIMIT
            or type(attempts) is not int or not 1 <= attempts <= MAX_ATTEMPTS
            or check_cancelled is not None and not callable(check_cancelled)
            or private_directory_factory is not None and not callable(private_directory_factory)):
        _fail("READONLY_INVALID_ARGUMENT")
    guard = lifecycle.require_guard(profile)
    check = check_cancelled or (lambda: None)
    check()
    exit_failure = _ExitFailure()
    directory = _fs(lambda: safe._Directory(profile.database.parent, base=profile.database.parent).__enter__())
    try:
        for attempt in range(attempts):
            temporary, connection, image = None, None, None
            try:
                check()
                before = _fs(lambda: source.source_state(directory, profile.database.name))
                journal = before["-journal"]
                if journal is not None and journal[2]:
                    _fail("SOURCE_ROLLBACK_JOURNAL_PRESENT", retriable=True)
                total_bytes = sum(item[2] for suffix, item in before.items() if suffix != "-journal" and item is not None)
                if total_bytes > max_bytes:
                    _fail("SOURCE_SNAPSHOT_TOO_LARGE")
                check()
                temporary = _fs(private_directory_factory or _PrivateDirectory, code="PRIVATE_IMAGE_IO_ERROR")
                private_root = temporary.path
                copied = private_root / "image.sqlite"
                copied_sha = {}
                for suffix in ("", "-wal"):
                    if before[suffix] is None:
                        copied_sha[suffix] = None
                        continue
                    check()
                    target = copied.with_name(copied.name + suffix)
                    initialize_file = getattr(temporary, "initialize_file", None)
                    copy_options = {} if initialize_file is None else {"destination_initializer": initialize_file}
                    _fs(lambda: source.copy_file(directory, profile.database.name + suffix, target,
                        before[suffix], **copy_options), missing_changed=True)
                    check()
                    copied_sha[suffix] = _private_sha(target, before[suffix][2], check)
                if _fs(lambda: source.source_state(directory, profile.database.name), missing_changed=True) != before:
                    raise _Unstable()
                check()
                connection = _open_private(copied)
                if _fs(lambda: source.source_state(directory, profile.database.name), missing_changed=True) != before:
                    raise _Unstable()
                fingerprint = {"format_version": 1, "profile_identity": list(profile.identity), "files": {
                    suffix: None if info is None else {**_signature(info), "sha256": (
                        hashlib.sha256(b"").hexdigest() if suffix == "-journal" else copied_sha[suffix])}
                    for suffix, info in before.items()}}
                image = CommittedImage(connection, profile, guard, directory, before, fingerprint, check)
                image._verify_source_state()
                check()
            except _Unstable as exc:
                _cleanup(image, connection, temporary)
                if attempt + 1 == attempts:
                    _fail("SOURCE_CHANGED", retriable=True)
                continue
            except BaseException as exc:
                _cleanup(image, connection, temporary, primary=exc)
                raise
            try:
                yield image
                image.verify_source()
            except BaseException as exc:
                _cleanup(image, connection, temporary, primary=exc)
                raise
            else:
                _cleanup(image, connection, temporary)
            return
    except BaseException as original:
        exit_failure.capture(original)
        raise
    finally:
        try:
            directory.__exit__(None, None, None)
        except BaseException as cleanup:
            exit_failure.capture(cleanup, note="READONLY_SOURCE_DIRECTORY_CLOSE_FAILED")
        exit_failure.raise_if_any()
