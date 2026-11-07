"""Read one existing node file through a lease at one absolute deadline.

The caller retains its directory and EX. Only direct consumers below receive
cooperative gates. This metadata reader does not grant authority, prove a late
native file witness or source_closed, or replace live SQL file sharing.
"""
from __future__ import annotations

from dataclasses import dataclass

import hashlib
import io
import math
import os
import time

from . import personal_authority_directory_deadline as directories
from . import personal_authority_namespace3_deadline as deadline
from . import personal_preservation_files as files
from . import personal_preservation_source as original
from . import sync_media as safe

_NODE, _MAX_NODE, _CHUNK = "node-identity.json", 4096, 128 * 1024
_NEW, _OPENING, _ACTIVE, _RETIRED = "new", "opening", "active", "retired"
_monotonic, _fstat, _stat = time.monotonic, os.fstat, os.stat


@dataclass(frozen=True, slots=True)
class NodeStreamCloseReceiptV1:
    """Cached context and lower owner facts; never a source-close grant."""
    format_version: int
    kind: str
    scope: str
    phase: str
    enter_returned: bool
    exit_attempted: bool
    failed: bool
    normal_exit: bool
    directory_borrowed: bool
    readonly_close_receipt: directories.ReadonlyCloseReceiptV1 | None


@dataclass(frozen=True, slots=True)
class HeldFileCloseReceiptV1:
    """Own terminal context is distinct from retained physical close facts."""
    format_version: int
    kind: str
    scope: str
    phase: str
    enter_returned: bool
    exit_attempted: bool
    failed: bool
    normal_exit: bool
    directory_borrowed: bool
    stream_close_receipt: NodeStreamCloseReceiptV1 | None


class SourceDeadlineError(ValueError):
    def __init__(self, suffix):
        self.code = "SOURCE_DEADLINE_" + suffix
        super().__init__(self.code)


Error = SourceDeadlineError


def _fail(suffix):
    raise SourceDeadlineError(suffix) from None


def _note(primary, note="SOURCE_DEADLINE_CLEANUP_FAILED"):
    try:
        BaseException.add_note(primary, note)
    except BaseException:
        pass


def _remember(owner, failure):
    if owner._first is None:
        owner._first = failure
    elif owner._first is not failure:
        _note(owner._first, "SOURCE_DEADLINE_SECONDARY_FAILED")
    return owner._first


def _parent(value):
    if type(value) is not deadline._RestrictedBudget:
        _fail("INVALID_ARGUMENT")
    try:
        deadline._token(value.effective_deadline)
    except AttributeError:
        _fail("INVALID_ARGUMENT")
    return value


def _clock(parent):
    parent.clock_only()
    now = _monotonic()
    if type(now) is not float or not math.isfinite(now) or now < 0.0:
        _fail("INVALID_ARGUMENT")
    if now >= parent.effective_deadline.absolute_monotonic:
        _fail("DEADLINE")


def _name(value):
    if type(value) is not str or value != _NODE:
        _fail("INVALID_ARGUMENT")


def _raw_read(raw, count):
    return io.FileIO.read(raw, count)


def _raw_seek(raw, offset, whence):
    return io.FileIO.seek(raw, offset, whence)


def _raw_fileno(raw):
    return io.FileIO.fileno(raw)


def _lease_exit(lease, kind, primary, tb):
    return directories.DeadlineReadonlyLease.__exit__(lease, kind, primary, tb)


def _traceback(primary):
    return (None if primary is None
            else BaseException.__dict__["__traceback__"].__get__(primary))


class DeadlineNodeDirectory:
    """Borrow an exact active directory and its exact existing budget token."""
    __slots__ = ("_directory", "_budget", "_enrollment_mode", "__weakref__")

    def __init__(self, directory, budget):
        if (type(self) is not DeadlineNodeDirectory
                or type(directory) is not directories.DeadlineDirectory):
            _fail("INVALID_ARGUMENT")
        _enrollment_factory_module()._reject_factory_reinit(self)
        budget = _parent(budget)
        if directory.deadline_token is not budget.effective_deadline:
            _fail("INVALID_ARGUMENT")
        directory._live()
        _clock(budget)
        self._enrollment_mode = None
        _initialize_node_directory(self, directory, budget)

    @property
    def path(self):
        pin = _enrollment_factory_module()._require_factory_owner(self, "node_parent")
        return self._directory.path if pin is None else pin.binding.data_path

    @property
    def budget(self):
        pin = _enrollment_factory_module()._require_factory_owner(self, "node_parent")
        return self._budget if pin is None else pin.binding.budget

    def _live(self):
        _enrollment_factory_module()._require_factory_owner_live(self, "node_parent")
        if self._directory.deadline_token is not self.budget.effective_deadline:
            _fail("INVALID_ARGUMENT")
        self._directory._live()
        _clock(self.budget)

    def verify(self):
        self._live()
        self._directory.verify()
        _clock(self.budget)

    def entry_stat(self, name):
        _name(name)
        self.verify()
        result = self._directory.entry_stat(name)
        _clock(self.budget)
        return result

    def open_file(self, name):
        _name(name)
        self.verify()
        pin = _enrollment_factory_module()._require_factory_owner(self, "node_parent")
        result = DeadlineNodeStream(self) if pin is None else _new_enrollment_node_stream(self)
        _clock(self.budget)
        return result


class _NodeFileBudget:
    """One <=4096-byte declaration; actual bytes use the shared parent ledger."""
    __slots__ = ("parent", "count", "_enrollment_mode", "__weakref__")

    def __init__(self, parent):
        if type(self) is not _NodeFileBudget:
            _fail("INVALID_ARGUMENT")
        _enrollment_factory_module()._reject_factory_reinit(self)
        self._enrollment_mode = None
        _initialize_node_file_budget(self, _parent(parent))

    def check(self):
        _enrollment_factory_module()._require_factory_owner_live(self, "node_budget")
        _clock(self.parent)

    def file(self, size):
        self.check()
        self.count += 1
        if type(size) is not int or not 0 <= size <= _MAX_NODE or self.count != 1:
            _fail("PROFILE_CHANGED")


class DeadlineNodeStream:
    """Borrow exact raw FileIO; only its successfully entered lease closes FD."""
    __slots__ = ("_node", "_phase", "_first", "_lease", "_entered",
                 "_buffer", "_raw", "_expected_fd", "_enter_returned",
                 "_exit_attempted", "_normal_exit", "_readonly_close_receipt",
                 "_enrollment_mode", "__weakref__")

    def __init__(self, node_directory):
        if type(self) is not DeadlineNodeStream or type(node_directory) is not DeadlineNodeDirectory:
            _fail("INVALID_ARGUMENT")
        _enrollment_factory_module()._reject_factory_reinit(self)
        if _enrollment_factory_module()._require_factory_owner(node_directory, "node_parent") is not None:
            _fail("INVALID_ARGUMENT")
        node_directory._live()
        self._enrollment_mode = None
        _initialize_node_stream(self, node_directory)

    @property
    def close_receipt(self):
        # Own cached fields only; no lower property, clock or native access.
        return NodeStreamCloseReceiptV1(1, "node_stream", "native_owner_close_facts_only",
            self._phase, self._enter_returned, self._exit_attempted,
            self._first is not None, self._normal_exit and self._first is None,
            True, self._readonly_close_receipt)

    def _cache_readonly_close_receipt(self):
        if self._lease is None:
            return
        try:
            if type(self._lease) is not directories.DeadlineReadonlyLease:
                _fail("INVALID_RESULT")
            pin = _enrollment_factory_module()._require_factory_owner(self, "node_stream")
            if pin is None:
                actual = self._lease.close_receipt
            else:
                lower_pin = _enrollment_factory_module()._require_factory_owner(self._lease, "node_readonly")
                if lower_pin is None or lower_pin.binding is not pin.binding:
                    _fail("INVALID_RESULT")
                actual = directories.DeadlineReadonlyLease.close_receipt.__get__(self._lease, directories.DeadlineReadonlyLease)
            if type(actual) is not directories.ReadonlyCloseReceiptV1:
                _fail("INVALID_RESULT")
            self._readonly_close_receipt = actual
        except BaseException as failure:
            self._readonly_close_receipt = None
            _remember(self, failure)

    def _live(self):
        if self._first is not None:
            raise self._first
        if self._phase != _ACTIVE:
            _fail("CLOSED")
        _enrollment_factory_module()._require_factory_owner_live(self, "node_stream")
        self._node._live()

    def __enter__(self):
        if self._first is not None:
            raise self._first
        if self._phase != _NEW:
            raise _remember(self, SourceDeadlineError("CLOSED")) from None
        pin = _enrollment_factory_module()._require_factory_owner_live(self, "node_stream")
        self._phase = _OPENING
        try:
            self._node._live()
            lease = self._node._directory.open_file(_NODE)
            if type(lease) is not directories.DeadlineReadonlyLease:
                _fail("PROFILE_CHANGED")
            self._lease = lease
            buffer = lease.__enter__()
            # Set before any postcheck: enter returned, so we owe one exit.
            self._entered = True
            self._buffer = buffer
            if type(buffer) is not io.BufferedReader:
                _fail("PROFILE_CHANGED")
            raw = buffer.raw
            self._raw = raw
            if type(raw) is not io.FileIO or raw.closefd is not False:
                _fail("PROFILE_CHANGED")
            _clock(self._node.budget)
            fd = _raw_fileno(raw)
            if type(fd) is not int or fd < 0:
                _fail("PROFILE_CHANGED")
            self._expected_fd = fd
            _clock(self._node.budget)
            self._phase = _ACTIVE
            _clock(self._node.budget)
            self._cache_readonly_close_receipt()
            if self._first is not None:
                raise self._first
            _clock(self._node.budget)
            if pin is not None:
                _enrollment_factory_module()._mark_enter_owed(self)
            self._enter_returned = True
            return self
        except BaseException as primary:
            first = _remember(self, primary)
            self._cleanup(first)
            if first is primary:
                raise
            raise first from None

    def read(self, requested):
        try:
            self._live()
            if type(requested) is not int or requested <= 0:
                _fail("INVALID_ARGUMENT")
            parent = self._node.budget
            amount = parent.request(min(requested, _MAX_NODE + 1))
            # request uses the original clock; gate this consumer again.
            _clock(parent)
            raw = _raw_read(self._raw, amount)
            if type(raw) is not bytes or len(raw) > amount:
                _fail("PROFILE_CHANGED")
            parent.charge(len(raw))
            _clock(parent)
            return raw
        except BaseException as primary:
            first = _remember(self, primary)
            if first is primary:
                raise
            raise first from None

    def seek(self, offset, whence=0):
        try:
            self._live()
            if type(offset) is not int or type(whence) is not int or whence not in (0, 1, 2):
                _fail("INVALID_ARGUMENT")
            result = _raw_seek(self._raw, offset, whence)
            if type(result) is not int or result < 0:
                _fail("PROFILE_CHANGED")
            _clock(self._node.budget)
            return result
        except BaseException as primary:
            first = _remember(self, primary)
            if first is primary:
                raise
            raise first from None

    def fileno(self):
        try:
            self._live()
            result = _raw_fileno(self._raw)
            if type(result) is not int or result < 0 or result != self._expected_fd:
                _fail("PROFILE_CHANGED")
            _clock(self._node.budget)
            return result
        except BaseException as primary:
            first = _remember(self, primary)
            if first is primary:
                raise
            raise first from None

    def _cleanup(self, primary):
        pin, failure = _enrollment_cleanup_identity(self)
        if failure is not None:
            _remember(self, failure)
        if pin is not None:
            return _cleanup_enrollment_stream(self, primary)
        if failure is not None:
            self._phase = _RETIRED
            self._entered = False
            self._readonly_close_receipt = None
            return
        if self._phase == _RETIRED:
            return  # No repeated lower receipt read or exit after retirement.
        self._phase = _RETIRED
        if not self._entered:
            # A failed lower enter already owns its own cleanup; cache once.
            self._cache_readonly_close_receipt()
            return
        # Never retry, even if the underlying exit reports an unknown close.
        self._entered = False
        self._exit_attempted = True
        try:
            actual = _lease_exit(self._lease, None if primary is None else type(primary),
                                 primary, _traceback(primary))
            if actual is not None:
                _fail("INVALID_RESULT")
        except BaseException as failure:
            if self._first is None:
                self._first = failure
            elif failure is not self._first:
                _note(self._first)
            self._cache_readonly_close_receipt()
        else:
            self._cache_readonly_close_receipt()

    def __exit__(self, _kind, primary, _tb):
        pin, failure = _enrollment_cleanup_identity(self)
        if failure is not None:
            try:
                if primary is not None:
                    _remember(self, primary)
                _remember(self, failure)
            except BaseException:
                _preserve_node_exit_failure(self, primary, failure)
            if pin is None:
                _preserve_node_exit_failure(self, primary, failure)
        if pin is not None:
            try:
                if primary is not None:
                    _remember(self, primary)
                claim = _enrollment_factory_module()._reserve_owner_exit(self)
                if claim is None:
                    _fail("CLOSED")
                return _exit_enrollment_node_stream(self, _kind, primary, _tb, claim=claim)
            except BaseException as failure:
                _preserve_node_exit_failure(self, primary, failure)
        return _exit_node_stream_body(self, _kind, primary, _tb)


def _handle_identity(stream, node):
    fd = stream.fileno()
    _clock(node.budget)
    result = _fstat(fd)
    _clock(node.budget)
    return files.identity(result)


def _directory_identity(node):
    # Preserve the old extra verify + path stat instead of borrowing its
    # opaque Path.stat or using a previous stat as a new observation.
    node.verify()
    _clock(node.budget)
    native = safe._native_path(node.path)
    _clock(node.budget)
    _clock(node.budget)
    result = _stat(native, follow_symlinks=False)
    _clock(node.budget)
    safe._safe_stat(result, directory=True)
    return {"device": str(result.st_dev), "inode": str(result.st_ino)}


class DeadlineHeldFile:
    """Keep the original node identities, repeated digests and terminal pass."""
    __slots__ = ("_node", "_phase", "_first", "_stream", "_entered", "_budget",
                 "_path_identity", "_handle_identity", "_directory_identity",
                 "_byte_size", "_sha256", "_raw", "_enter_returned",
                 "_exit_attempted", "_normal_exit", "_stream_close_receipt",
                 "_enrollment_mode", "__weakref__")

    def __init__(self, node_directory):
        if type(self) is not DeadlineHeldFile or type(node_directory) is not DeadlineNodeDirectory:
            _fail("INVALID_ARGUMENT")
        _enrollment_factory_module()._reject_factory_reinit(self)
        if _enrollment_factory_module()._require_factory_owner(node_directory, "node_parent") is not None:
            _fail("INVALID_ARGUMENT")
        node_directory._live()
        self._enrollment_mode = None
        _initialize_held_file(self, node_directory)

    @property
    def close_receipt(self):
        # Read only this owner; the exact lower snapshot was already cached.
        return HeldFileCloseReceiptV1(1, "held_file", "native_owner_close_facts_only",
            self._phase, self._enter_returned, self._exit_attempted,
            self._first is not None, self._normal_exit and self._first is None,
            True, self._stream_close_receipt)

    def _cache_stream_close_receipt(self):
        if self._stream is None:
            return
        try:
            if type(self._stream) is not DeadlineNodeStream:
                _fail("INVALID_RESULT")
            pin = _enrollment_factory_module()._require_factory_owner(self, "node_held")
            if pin is None:
                actual = self._stream.close_receipt
            else:
                lower_pin = _enrollment_factory_module()._require_factory_owner(self._stream, "node_stream")
                if lower_pin is None or lower_pin.binding is not pin.binding:
                    _fail("INVALID_RESULT")
                actual = DeadlineNodeStream.close_receipt.__get__(self._stream, DeadlineNodeStream)
            if type(actual) is not NodeStreamCloseReceiptV1:
                _fail("INVALID_RESULT")
            self._stream_close_receipt = actual
        except BaseException as failure:
            self._stream_close_receipt = None
            _remember(self, failure)

    def _check(self):
        if self._first is not None:
            raise self._first
        if self._phase not in (_OPENING, _ACTIVE):
            _fail("CLOSED")
        _enrollment_factory_module()._require_factory_owner_live(self, "node_held")
        self._node._live()
        self._budget.check()

    def _live(self):
        if self._first is not None:
            raise self._first
        if self._phase != _ACTIVE:
            _fail("CLOSED")
        _enrollment_factory_module()._require_factory_owner_live(self, "node_held")
        self._node._live()

    def _evidence(self, value):
        try:
            self._live()
            result = dict(value) if type(value) is dict else value
            _clock(self._node.budget)
            return result
        except BaseException as primary:
            first = _remember(self, primary)
            if first is primary:
                raise
            raise first from None

    @property
    def byte_size(self):
        return self._evidence(self._byte_size)

    @property
    def sha256(self):
        return self._evidence(self._sha256)

    @property
    def path_identity(self):
        return self._evidence(self._path_identity)

    @property
    def handle_identity(self):
        return self._evidence(self._handle_identity)

    @property
    def directory_identity(self):
        return self._evidence(self._directory_identity)

    def __enter__(self):
        if self._first is not None:
            raise self._first
        if self._phase != _NEW:
            raise _remember(self, SourceDeadlineError("CLOSED")) from None
        pin = _enrollment_factory_module()._require_factory_owner_live(self, "node_held")
        self._phase = _OPENING
        try:
            self._path_identity = files.identity(self._node.entry_stat(_NODE))
            self._budget.file(self._path_identity["byte_size"])
            self._byte_size = self._path_identity["byte_size"]
            self._stream = self._node.open_file(_NODE)
            self._stream.__enter__()
            self._entered = True
            self._handle_identity = _handle_identity(self._stream, self._node)
            original._same(
                {k: v for k, v in self._path_identity.items() if k != "ctime_ns"},
                {k: v for k, v in self._handle_identity.items() if k != "ctime_ns"})
            self._directory_identity = _directory_identity(self._node)
            self._sha256, self._raw = self._read(collect=False)
            self._verify()
            _clock(self._node.budget)
            self._phase = _ACTIVE
            _clock(self._node.budget)
            self._cache_stream_close_receipt()
            if self._first is not None:
                raise self._first
            _clock(self._node.budget)
            if pin is not None:
                _enrollment_factory_module()._mark_enter_owed(self)
            self._enter_returned = True
            return self
        except BaseException as primary:
            first = _remember(self, primary)
            self._cleanup(first)
            if first is primary:
                raise
            raise first from None

    def _read(self, *, collect):
        self._check()
        self._stream.seek(0)
        digest, total, pieces = hashlib.sha256(), 0, []
        while True:
            self._check()
            chunk = self._stream.read(_CHUNK)
            if not chunk:
                break
            total += len(chunk)
            if total > self._byte_size:
                original._fail("SOURCE_CHANGED", retriable=True)
            digest.update(chunk)
            if collect:
                pieces.append(chunk)
        self._check()
        original._same(total, self._byte_size)
        return digest.hexdigest(), b"".join(pieces) if collect else None

    def _verify_identities(self):
        self._check()
        self._node.verify()
        original._same(_directory_identity(self._node), self._directory_identity)
        original._same(files.identity(self._node.entry_stat(_NODE)), self._path_identity)
        original._same(_handle_identity(self._stream, self._node), self._handle_identity)

    def _verify(self):
        self._verify_identities()
        if self._sha256 is not None:
            digest = self._read(collect=False)[0]
            self._verify_identities()
            original._same(digest, self._sha256)
        self._check()

    def verify(self):
        try:
            self._live()
            self._verify()
        except BaseException as primary:
            first = _remember(self, primary)
            if first is primary:
                raise
            raise first from None

    def bytes(self, maximum):
        try:
            self._live()
            if type(maximum) is not int or maximum < 0:
                _fail("INVALID_ARGUMENT")
            if self._byte_size > maximum:
                original._fail("CONTROL_FILE_TOO_LARGE")
            self._verify()
            digest, raw = self._read(collect=True)
            self._verify_identities()
            original._same(digest, self._sha256)
            self._verify()
            _clock(self._node.budget)
            return raw
        except BaseException as primary:
            first = _remember(self, primary)
            if first is primary:
                raise
            raise first from None

    def _cleanup(self, primary):
        pin, failure = _enrollment_cleanup_identity(self)
        if failure is not None:
            _remember(self, failure)
        if pin is not None:
            return _cleanup_enrollment_held(self, primary)
        if failure is not None:
            self._phase = _RETIRED
            self._entered = False
            self._stream_close_receipt = None
            return
        if self._phase == _RETIRED:
            return  # The lower owner was already consumed; do not retry it.
        self._phase = _RETIRED
        if not self._entered:
            self._cache_stream_close_receipt()
            return
        self._entered = False
        self._exit_attempted = True
        try:
            actual = self._stream.__exit__(None if primary is None else type(primary),
                                           primary, _traceback(primary))
            if actual is not None:
                _fail("INVALID_RESULT")
        except BaseException as failure:
            if self._first is None:
                self._first = failure
            elif failure is not self._first:
                _note(self._first)
            self._cache_stream_close_receipt()
        else:
            self._cache_stream_close_receipt()

    def __exit__(self, _kind, primary, _tb):
        pin, failure = _enrollment_cleanup_identity(self)
        if failure is not None:
            try:
                if primary is not None:
                    _remember(self, primary)
                _remember(self, failure)
            except BaseException:
                _preserve_node_exit_failure(self, primary, failure)
            if pin is None:
                _preserve_node_exit_failure(self, primary, failure)
        if pin is not None:
            try:
                if primary is not None:
                    _remember(self, primary)
                claim = _enrollment_factory_module()._reserve_owner_exit(self)
                if claim is None:
                    _fail("CLOSED")
                return _exit_enrollment_held_file(self, _kind, primary, _tb, claim=claim)
            except BaseException as failure:
                _preserve_node_exit_failure(self, primary, failure)
        return _exit_held_file_body(self, _kind, primary, _tb)


def _enrollment_factory_module():
    from . import personal_authority_enrollment_physical as physical
    return physical


def _enrollment_cleanup_failure(owner):
    # A metadata/call-layer failure must not skip independent known ledger work.
    try:
        return _enrollment_factory_module()._begin_owner_cleanup(owner)
    except BaseException as failure:
        return failure


def _enrollment_cleanup_identity(owner):
    try:
        return _enrollment_factory_module()._factory_cleanup_pin(owner)
    except BaseException as failure:
        return None, failure


def _initialize_node_directory(self, directory, budget):
    _enrollment_factory_module()._begin_factory_initialization(self, "node_parent")
    self._directory, self._budget = directory, budget


def _initialize_node_file_budget(self, parent):
    _enrollment_factory_module()._begin_factory_initialization(self, "node_budget")
    self.parent, self.count = parent, 0


def _initialize_node_stream(self, node_directory):
    _enrollment_factory_module()._begin_factory_initialization(self, "node_stream")
    self._node, self._phase, self._first = node_directory, _NEW, None
    self._lease, self._entered = None, False
    self._buffer = self._raw = self._expected_fd = None
    self._enter_returned = self._exit_attempted = self._normal_exit = False
    self._readonly_close_receipt = None


def _initialize_held_file(self, node_directory, *, binding=None):
    _enrollment_factory_module()._begin_factory_initialization(self, "node_held")
    self._node, self._phase, self._first = node_directory, _NEW, None
    self._stream, self._entered = None, False
    self._budget = (_NodeFileBudget(node_directory.budget) if binding is None
                    else _new_enrollment_node_file_budget(binding))
    self._path_identity = self._handle_identity = self._directory_identity = None
    self._byte_size = self._sha256 = self._raw = None
    self._enter_returned = self._exit_attempted = self._normal_exit = False
    self._stream_close_receipt = None


def _new_enrollment_node_directory(binding):
    physical = _enrollment_factory_module()
    binding = physical._validate_node_binding(binding, live=True)
    owner = object.__new__(DeadlineNodeDirectory)
    pin = physical._register_factory_owner(binding, owner, "node_parent", parent=binding.directory)
    owner._enrollment_mode = pin
    directory, budget = binding.facade, binding.budget
    if directory.deadline_token is not budget.effective_deadline:
        _fail("INVALID_ARGUMENT")
    directory._live()
    _clock(budget)
    _initialize_node_directory(owner, directory, budget)
    physical._require_factory_owner_live(owner, "node_parent")
    return owner


def _new_enrollment_node_file_budget(binding):
    physical = _enrollment_factory_module()
    binding = physical._validate_node_binding(binding, live=True)
    owner = object.__new__(_NodeFileBudget)
    pin = physical._register_factory_owner(binding, owner, "node_budget", parent=None)
    owner._enrollment_mode = pin
    _initialize_node_file_budget(owner, binding.budget)
    physical._require_factory_owner_live(owner, "node_budget")
    return owner


def _new_enrollment_node_stream(node_directory):
    physical = _enrollment_factory_module()
    parent_pin = physical._require_factory_owner_live(node_directory, "node_parent")
    if parent_pin is None:
        _fail("INVALID_ARGUMENT")
    binding = physical._validate_node_binding(parent_pin.binding, live=True)
    owner = object.__new__(DeadlineNodeStream)
    pin = physical._register_factory_owner(binding, owner, "node_stream", parent=node_directory)
    owner._enrollment_mode = pin
    node_directory._live()
    _initialize_node_stream(owner, node_directory)
    physical._require_factory_owner_live(owner, "node_stream")
    return owner


def _new_enrollment_held_file(node_directory):
    physical = _enrollment_factory_module()
    parent_pin = physical._require_factory_owner_live(node_directory, "node_parent")
    if parent_pin is None:
        _fail("INVALID_ARGUMENT")
    binding = physical._validate_node_binding(parent_pin.binding, live=True)
    owner = object.__new__(DeadlineHeldFile)
    pin = physical._register_factory_owner(binding, owner, "node_held", parent=node_directory)
    owner._enrollment_mode = pin
    node_directory._live()
    _initialize_held_file(owner, node_directory, binding=binding)
    physical._require_factory_owner_live(owner, "node_held")
    return owner


def _cleanup_enrollment_stream(self, primary):
    if self._phase == _RETIRED:
        return
    failure = _enrollment_cleanup_failure(self)
    if failure is not None:
        _remember(self, failure)
    if self._first is not None:
        primary = self._first
    self._phase = _RETIRED
    self._entered = False
    try:
        if self._lease is not None:
            claim = _enrollment_factory_module()._reserve_lower_exit(self, self._lease)
            if claim is not None:
                self._exit_attempted = True
                actual = directories._exit_enrollment_readonly(self._lease,
                    None if primary is None else type(primary), primary, _traceback(primary), claim=claim)
                if actual is not None:
                    _fail("INVALID_RESULT")
    except BaseException as failure:
        _remember(self, failure)
    self._cache_readonly_close_receipt()


def _cleanup_enrollment_held(self, primary):
    if self._phase == _RETIRED:
        return
    failure = _enrollment_cleanup_failure(self)
    if failure is not None:
        _remember(self, failure)
    if self._first is not None:
        primary = self._first
    self._phase = _RETIRED
    self._entered = False
    try:
        if self._stream is not None:
            claim = _enrollment_factory_module()._reserve_lower_exit(self, self._stream)
            if claim is not None:
                self._exit_attempted = True
                actual = _exit_enrollment_node_stream(self._stream,
                    None if primary is None else type(primary), primary, _traceback(primary), claim=claim)
                if actual is not None:
                    _fail("INVALID_RESULT")
    except BaseException as failure:
        _remember(self, failure)
    self._cache_stream_close_receipt()


def _exit_enrollment_node_stream(stream, kind, primary, tb, *, claim):
    try:
        if type(stream) is not DeadlineNodeStream:
            _fail("INVALID_ARGUMENT")
        if primary is not None:
            _remember(stream, primary)
        _enrollment_factory_module()._begin_claimed_exit(stream, claim)
        return _exit_node_stream_body(stream, kind, primary, tb)
    except BaseException as failure:
        _preserve_node_exit_failure(stream, primary, failure)


def _exit_enrollment_held_file(held, kind, primary, tb, *, claim):
    try:
        if type(held) is not DeadlineHeldFile:
            _fail("INVALID_ARGUMENT")
        if primary is not None:
            _remember(held, primary)
        _enrollment_factory_module()._begin_claimed_exit(held, claim)
        return _exit_held_file_body(held, kind, primary, tb)
    except BaseException as failure:
        _preserve_node_exit_failure(held, primary, failure)


def _exit_node_stream_body(self, _kind, primary, _tb):
    if primary is not None:
        _remember(self, primary)
    if self._phase == _NEW and self._first is None:
        self._first = SourceDeadlineError("CLOSED")
    self._cleanup(self._first)
    if self._first is None:
        try:
            _clock(self._node.budget)
        except BaseException as failure:
            self._first = failure
    if self._first is None:
        actual = self._readonly_close_receipt
        if (not self._enter_returned or not self._exit_attempted
                or type(actual) is not directories.ReadonlyCloseReceiptV1
                or actual.phase != _RETIRED or actual.normal_exit is not True):
            _remember(self, SourceDeadlineError("INVALID_RESULT"))
        else:
            self._normal_exit = True
    if self._first is not None and self._first is not primary:
        raise self._first from None
    return None


def _exit_held_file_body(self, _kind, primary, _tb):
    if primary is not None:
        _remember(self, primary)
    if self._first is None:
        try:
            self._live()
            self._verify()
        except BaseException as failure:
            self._first = failure
    self._cleanup(self._first)
    if self._first is None:
        try:
            _clock(self._node.budget)
        except BaseException as failure:
            self._first = failure
    if self._first is None:
        actual = self._stream_close_receipt
        if (not self._enter_returned or not self._exit_attempted
                or type(actual) is not NodeStreamCloseReceiptV1
                or actual.phase != _RETIRED or actual.normal_exit is not True):
            _remember(self, SourceDeadlineError("INVALID_RESULT"))
        else:
            self._normal_exit = True
    if self._first is not None and self._first is not primary:
        raise self._first from None
    return None


def _preserve_node_exit_failure(owner, primary, failure):
    if type(owner) is DeadlineNodeStream or type(owner) is DeadlineHeldFile:
        try:
            if primary is not None:
                _remember(owner, primary)
            first = _remember(owner, failure)
        except BaseException:
            if primary is not None:
                _note(primary)
                raise primary from None
            raise failure
        if first is not failure:
            raise first from None
    elif primary is not None:
        _note(primary)
        raise primary from None
    raise failure
