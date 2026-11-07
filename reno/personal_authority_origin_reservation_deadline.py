"""Create or strictly resume the two original immutable origin declarations.

The caller keeps its actual EX.  All new components receive the same effective
absolute token, and the first file is published and independently held before
the complete origin producer is entered.  A result proves neither enrollment
nor a grant, a live witness, or complete native/source closure.
"""
from __future__ import annotations

from contextlib import ExitStack
from copy import deepcopy
import hashlib
import io
import json
import math
import os
import time

from . import personal_authority_contract as authority
from . import personal_authority_legacy_fence as fence
from . import personal_authority_origin_reservation as ordinary
from . import personal_authority_origin_reservation_contract as contract
from . import personal_authority_origin_reservation_io as ordinary_publication
from . import personal_authority_origin_reservation_io_deadline as publication
from . import personal_authority_origin_source as ordinary_origin
from . import personal_authority_origin_capture_deadline as origin
from . import personal_authority_namespace3_deadline as deadline
from . import personal_authority_directory_deadline as directories
from . import personal_authority_source_deadline as nodes
from . import personal_preservation_files as files
from . import personal_preservation_source as source
from . import storage_lifecycle as lifecycle, sync_media as safe

OriginReservationError = ordinary.OriginReservationError
OriginReservationResult = ordinary.OriginReservationResult
Error = OriginReservationError
MAX_SECONDS = 30.0
MAX_NAMES = 4096
MAX_NAME_BYTES = 1024 * 1024
_ROOTS = ("data", "media", "reports", "checkpoints")
_PUBLISHED_IDENTITY_KEYS = ("device", "inode", "byte_size", "mtime_ns")
_NEW, _OPENING, _ACTIVE, _RETIRED = "new", "opening", "active", "retired"


def _fail(suffix):
    raise OriginReservationError(suffix) from None


def _note(primary):
    try:
        BaseException.add_note(primary, "ORIGIN_RESERVATION_CLEANUP_FAILED")
    except BaseException:
        pass


# L01--L57 are the fixed functional boundaries.  CLOCK and the bootstrap
# aliases are raw operations; their callers establish and check state.
def _monotonic():
    return time.monotonic()


def _deadline(absolute):
    return deadline.Namespace3Deadline(absolute)


def _restricted_budget(requested):
    return deadline._RestrictedBudget(requested)


def _effective_token(budget):
    return budget.effective_deadline


def _profile_paths(profile):
    return tuple(profile.identity)


def _uuid(value):
    return contract._uuid(value)


def _sha(value):
    return contract._sha(value)


def _require_guard(profile, *, exclusive=True):
    return lifecycle.require_guard(profile, exclusive=exclusive)


def _key(path):
    return lifecycle._key(path)


def _directory(path, *, deadline_token, base):
    return directories.DeadlineDirectory(path, deadline_token=deadline_token,
                                         create=False, base=base)


def _directory_enter(value):
    return directories.DeadlineDirectory.__enter__(value)


def _directory_exit(value, kind, primary, tb):
    return directories.DeadlineDirectory.__exit__(value, kind, primary, tb)


def _directory_verify(value):
    return directories.DeadlineDirectory.verify(value)


def _directory_identity(value):
    return files.directory_identity(value)


def _scandir(directory):
    return os.scandir(safe._native_path(directory.path))


def _entries_enter(value):
    return value.__enter__()


def _entries_next(value):
    return next(value)


def _entry_name(value):
    return value.name


def _entries_exit(value, kind, primary, tb):
    return value.__exit__(kind, primary, tb)


def _node_directory(directory, budget):
    return nodes.DeadlineNodeDirectory(directory, budget)


def _node(parent):
    return nodes.DeadlineHeldFile(parent)


def _node_enter(value):
    return nodes.DeadlineHeldFile.__enter__(value)


def _node_exit(value, kind, primary, tb):
    return nodes.DeadlineHeldFile.__exit__(value, kind, primary, tb)


def _node_verify(value):
    return nodes.DeadlineHeldFile.verify(value)


def _node_bytes(value, maximum):
    return nodes.DeadlineHeldFile.bytes(value, maximum)


def _node_pairs(items):
    result = {}
    for key, value in items:
        if type(key) is not str or key in result:
            ordinary_origin._fail("ORIGIN_NODE_INVALID")
        result[key] = value
    return result


def _node_constant(_value):
    ordinary_origin._fail("ORIGIN_NODE_INVALID")


def _node_id(raw):
    if type(raw) is not bytes or len(raw) > contract.MAX_NODE_BYTES:
        ordinary_origin._fail("ORIGIN_NODE_INVALID")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_node_pairs,
                           parse_constant=_node_constant)
    except ordinary_origin.OriginSourceError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        ordinary_origin._fail("ORIGIN_NODE_INVALID")
    if type(value) is not dict or set(value) != {"instance_id"}:
        ordinary_origin._fail("ORIGIN_NODE_INVALID")
    try:
        return authority._uuid(value["instance_id"])
    except authority.ContractError:
        ordinary_origin._fail("ORIGIN_NODE_INVALID")


def _file_evidence(held):
    return {"sha256": held.sha256, "byte_size": held.byte_size,
            "directory_identity": deepcopy(held.directory_identity),
            "path_identity": deepcopy(held.path_identity),
            "handle_identity": deepcopy(held.handle_identity)}


def _control(directory, name, *, budget, maximum):
    return _ControlHeld(directory, name, budget=budget, maximum=maximum)


def _control_enter(value):
    return _ControlHeld.__enter__(value)


def _control_exit(value, kind, primary, tb):
    return _ControlHeld.__exit__(value, kind, primary, tb)


def _control_verify(value):
    return _ControlHeld.verify(value)


def _control_bytes(value, maximum):
    return _ControlHeld.bytes(value, maximum)


def _read_lease(directory, name):
    return directories.DeadlineDirectory.open_file(directory, name)


def _read_enter(value):
    return directories.DeadlineReadonlyLease.__enter__(value)


def _read_exit(value, kind, primary, tb):
    return directories.DeadlineReadonlyLease.__exit__(value, kind, primary, tb)


def _raw_seek(stream, offset, whence):
    return io.BufferedReader.seek(stream, offset, whence)


def _raw_read(stream, requested):
    return io.BufferedReader.read(stream, requested)


def _raw_fileno(stream):
    return io.BufferedReader.fileno(stream)


def _fstat(fd):
    return os.fstat(fd)


def _file_identity(info):
    return files.identity(info)


def _hash_new():
    return hashlib.sha256()


def _hash_update(digest, raw):
    return digest.update(raw)


def _hash_hex(digest):
    return digest.hexdigest()


def _bytes_join(pieces):
    return b"".join(pieces)


def _encode(value):
    return contract.encode(value)


def _decode(raw):
    return contract.decode(raw)


def _stage_name(name, operation):
    return contract.stage_name(name, operation)


def _cross_validate(first, second):
    return contract.cross_validate(first, second)


def _encode_observation(first, intent, evidence):
    return contract.encode_observation(first, intent, evidence)


def _digest(value):
    return contract._digest(value)


def _sha256_hex(raw):
    return hashlib.sha256(raw).hexdigest()


def _deepcopy(value):
    return deepcopy(value)


def _publish(profile, directory, name, stage_name, raw, *, deadline_token):
    return publication.publish_new_locked(profile, directory, name, stage_name,
                                         raw, deadline_token=deadline_token)


def _origin_context(profile, *, domain_id, facility_id, init_operation_id,
                    deadline_token, check_cancelled):
    return origin.capture_locked(profile, domain_id=domain_id,
        facility_id=facility_id, init_operation_id=init_operation_id,
        deadline_token=deadline_token, check_cancelled=check_cancelled)


def _origin_enter(context):
    return context.__enter__()


def _origin_exit(context, kind, primary, tb):
    return context.__exit__(kind, primary, tb)


def _result(operation, first_sha, second_sha):
    return OriginReservationResult(operation, first_sha, second_sha)


class _FirstState:
    __slots__ = ("token", "first", "first_tb")

    def __init__(self, token):
        self.token, self.first, self.first_tb = token, None, None


def _remember(state, failure, tb=None):
    if state.first is None:
        state.first = failure
        state.first_tb = (BaseException.__dict__["__traceback__"].__get__(failure)
                          if tb is None else tb)
    elif failure is not state.first:
        _note(state.first)


def _raise_first(state):
    if state.first is not None:
        raise BaseException.with_traceback(state.first, state.first_tb) from None


def _clock(state):
    _raise_first(state)
    try:
        now = _monotonic()
        if type(now) is not float or not math.isfinite(now) or now < 0.0:
            _fail("STATE_UNAVAILABLE")
        if now >= state.token.absolute_monotonic:
            _fail("DEADLINE")
    except BaseException as failure:
        _remember(state, failure)
        _raise_first(state)


def _cleanup_clock(state):
    # Clock failures cannot prevent the one attempt owed to a known owner.
    try:
        _clock(state)
    except BaseException as failure:
        _remember(state, failure)


def _none(value):
    return value is None


def _integer(value):
    return type(value) is int and value >= 0


def _text(value):
    return type(value) is str


def _sha_text(value):
    return type(value) is str and contract._SHA.fullmatch(value) is not None


def _bounded_bytes(value, maximum):
    return type(value) is bytes and len(value) <= maximum


def _directory_evidence(value):
    return (type(value) is dict and set(value) == set(contract._IDENTITY)
            and all(type(item) is str and contract._DECIMAL.fullmatch(item) is not None
                    for item in value.values()))


def _file_identity_evidence(value):
    if type(value) is not dict or set(value) != set(contract._FILE_IDENTITY):
        return False
    try:
        contract._identity(value, file=True)
    except contract.OriginReservationContractError:
        return False
    return True


def _file_evidence_value(value):
    if type(value) is not dict or set(value) != set(contract._FILE):
        return False
    try:
        contract._file(value)
    except contract.OriginReservationContractError:
        return False
    return True


def _hash_object(value):
    kind = type(value)
    return (kind.__module__ in {"_hashlib", "_sha256"}
            and kind.__name__ in {"HASH", "SHA256Type", "sha256"}
            and value.name == "sha256")


def _bytes(value):
    return type(value) is bytes


def _mapping(value):
    return type(value) is dict


def _paths(value):
    return (type(value) is tuple and len(value) == 4
            and all(type(item) is str for item in value))


def _call(state, operation, /, *args, legal=None, **kwargs):
    _clock(state)
    try:
        result = operation(*args, **kwargs)
        if legal is not None and not legal(result):
            _fail("STATE_UNAVAILABLE")
    except BaseException as failure:
        _remember(state, failure)
        _raise_first(state)
    _clock(state)
    return result


def _cpu(state, operation, /, *args, legal=None, **kwargs):
    # The complete pure helper performs its original error conversion before
    # this boundary remembers the resulting semantic primary.
    return _call(state, operation, *args, legal=legal, **kwargs)


def _prepared(state, operation, expected, /, *args, **kwargs):
    return _call(state, operation, *args,
                 legal=lambda value: type(value) is expected, **kwargs)


class _Budget:
    """Compose the original fixed counters and an exact restricted node budget."""
    __slots__ = ("restricted", "effective_deadline", "state", "callback",
                 "callback_primary", "callback_tb", "names", "name_bytes")

    def __init__(self, restricted, effective, callback):
        self.restricted, self.effective_deadline = restricted, effective
        self.state = _FirstState(effective)
        self.callback, self.callback_primary, self.callback_tb = callback, None, None
        self.names = self.name_bytes = 0

    def clock_only(self):
        _clock(self.state)

    def poll(self):
        self.clock_only()
        if self.callback is not None:
            try:
                self.callback()
            except BaseException as primary:
                self.callback_primary = primary
                self.callback_tb = BaseException.__dict__["__traceback__"].__get__(primary)
                _remember(self.state, primary, self.callback_tb)
                raise
        self.clock_only()

    def before_next(self):
        self.clock_only()
        if self.names >= MAX_NAMES:
            _fail("STATE_UNAVAILABLE")

    def name(self, value):
        self.names += 1
        remaining = MAX_NAME_BYTES - self.name_bytes
        if type(value) is not str or not value or len(value) > remaining:
            _fail("STATE_UNAVAILABLE")
        size = len(value.encode("utf-8"))
        if size > remaining:
            _fail("STATE_UNAVAILABLE")
        self.name_bytes += size


def _domain(requested):
    try:
        return deadline._token(requested)
    except deadline.Namespace3Error:
        _fail("INVALID_ARGUMENT")


def _make_budget(deadline_token, check_cancelled=None):
    requested = _domain(deadline_token)
    if check_cancelled is not None and not callable(check_cancelled):
        _fail("INVALID_ARGUMENT")
    try:
        restricted = _restricted_budget(requested)
    except deadline.Namespace3Error as error:
        if error.code == "NAMESPACE3_DEADLINE":
            _fail("DEADLINE")
        raise
    if type(restricted) is not deadline._RestrictedBudget:
        _fail("STATE_UNAVAILABLE")
    effective = _effective_token(restricted)
    try:
        deadline._token(effective)
    except deadline.Namespace3Error:
        _fail("STATE_UNAVAILABLE")
    if (effective is not restricted.effective_deadline
            or effective.absolute_monotonic > requested.absolute_monotonic):
        _fail("STATE_UNAVAILABLE")
    result = _Budget(restricted, effective, check_cancelled)
    _clock(result.state)
    return result


class _FileBudget:
    """One original bounded control file; no second time allowance."""
    __slots__ = ("budget", "maximum", "count")

    def __init__(self, budget, maximum):
        self.budget, self.maximum, self.count = budget, maximum, 0

    def check(self):
        self.budget.clock_only()

    def file(self, size):
        self.check()
        self.count += 1
        if type(size) is not int or not 0 <= size <= self.maximum or self.count != 1:
            _fail("STATE_UNAVAILABLE")


class _Scope:
    """Adopt only a legal enter; attempt the fixed known exit at most once."""
    __slots__ = ("state", "owner", "enter", "exit", "expected", "same",
                 "exit_false", "entered", "attempted", "normal")

    def __init__(self, state, owner, enter, exit, expected, *, same=True,
                 exit_false=False):
        self.state, self.owner, self.enter, self.exit = state, owner, enter, exit
        self.expected, self.same, self.exit_false = expected, same, exit_false
        self.entered = self.attempted = self.normal = False

    def __enter__(self):
        _clock(self.state)
        try:
            value = self.enter(self.owner)
            if type(value) is not self.expected or (self.same and value is not self.owner):
                _fail("STATE_UNAVAILABLE")
            if self.expected is ordinary_origin.OriginObservation:
                if type(value.intent_bytes) is not bytes or type(value.evidence_bytes) is not bytes:
                    _fail("STATE_UNAVAILABLE")
        except BaseException as failure:
            # Invalid or missing enter output is UNKNOWN, never an exit probe.
            _remember(self.state, failure)
            _raise_first(self.state)
        self.entered = True
        try:
            _clock(self.state)
        except BaseException as failure:
            self.__exit__(type(failure), failure,
                          BaseException.__dict__["__traceback__"].__get__(failure))
            raise
        return value

    def __exit__(self, kind, primary, tb):
        if primary is not None:
            _remember(self.state, primary, tb)
        if not self.entered or self.attempted:
            _fail("STATE_UNAVAILABLE")
        self.attempted = True
        _cleanup_clock(self.state)
        first, first_tb = self.state.first, self.state.first_tb
        try:
            result = self.exit(self.owner, None if first is None else type(first),
                               first, first_tb)
            if (result is not False if self.exit_false else result is not None):
                _fail("STATE_UNAVAILABLE")
            self.normal = True
        except BaseException as failure:
            _remember(self.state, failure)
        _cleanup_clock(self.state)
        _raise_first(self.state)
        return None


def _iterator(value):
    kind = type(value)
    return kind.__name__ == "ScandirIterator" and kind.__module__ in {"nt", "posix"}


class _Entries:
    """The scandir factory itself acquires the iterator, before enter/postclock."""
    __slots__ = ("state", "directory", "owner", "known", "attempted", "normal")

    def __init__(self, state, directory):
        self.state, self.directory, self.owner = state, directory, None
        self.known = self.attempted = self.normal = False

    def __enter__(self):
        _clock(self.state)
        try:
            value = _scandir(self.directory)
            if not _iterator(value):
                _fail("STATE_UNAVAILABLE")
            self.owner, self.known = value, True
            _clock(self.state)
            entered = _call(self.state, _entries_enter, value,
                            legal=lambda result: result is value)
            return entered
        except BaseException as failure:
            _remember(self.state, failure)
            if self.known:
                self.__exit__(type(failure), failure,
                              BaseException.__dict__["__traceback__"].__get__(failure))
            _raise_first(self.state)

    def __exit__(self, kind, primary, tb):
        if primary is not None:
            _remember(self.state, primary, tb)
        if not self.known or self.attempted:
            _fail("STATE_UNAVAILABLE")
        self.attempted = True
        _cleanup_clock(self.state)
        first, first_tb = self.state.first, self.state.first_tb
        try:
            result = _entries_exit(self.owner, None if first is None else type(first),
                                   first, first_tb)
            if result is not None:
                _fail("STATE_UNAVAILABLE")
            self.normal = True
        except BaseException as failure:
            _remember(self.state, failure)
        _cleanup_clock(self.state)
        _raise_first(self.state)
        return None


def _next_entry(state, entries):
    _clock(state)
    try:
        result = _entries_next(entries)
    except StopIteration:
        # This fixed next call alone accepts EOF, still followed by its clock.
        _clock(state)
        return None
    except BaseException as failure:
        _remember(state, failure)
        _raise_first(state)
    if type(result) is not os.DirEntry:
        _fail("STATE_UNAVAILABLE")
    _clock(state)
    return result


class _ControlHeld:
    """Full original HeldFile algorithm; the r823 lease alone closes its FD."""
    __slots__ = ("directory", "name", "path", "budget", "state", "maximum",
                 "phase", "lease", "lease_entered", "lease_attempted", "stream",
                 "path_identity", "handle_identity", "directory_identity",
                 "byte_size", "sha256", "raw")

    def __init__(self, directory, name, *, budget, maximum):
        if (type(self) is not _ControlHeld
                or type(directory) is not directories.DeadlineDirectory
                or type(budget) is not _Budget
                or directory.deadline_token is not budget.effective_deadline):
            _fail("STATE_UNAVAILABLE")
        expected = {contract.RESERVATION_NAME: contract.MAX_RESERVATION_BYTES,
                    contract.OBSERVATION_NAME: contract.MAX_OBSERVATION_BYTES}
        if type(name) is not str or name not in expected or type(maximum) is not int or maximum != expected[name]:
            _fail("STATE_UNAVAILABLE")
        self.directory, self.name = directory, name
        self.path, self.maximum = directory.path / name, maximum
        self.budget, self.state = _FileBudget(budget, maximum), budget.state
        self.phase, self.lease, self.stream = _NEW, None, None
        self.lease_entered = self.lease_attempted = False
        self.path_identity = self.handle_identity = self.directory_identity = None
        self.byte_size = self.sha256 = self.raw = None

    def _check(self):
        _raise_first(self.state)
        if self.phase not in (_OPENING, _ACTIVE):
            _fail("STATE_UNAVAILABLE")
        self.budget.check()

    def _identity_at_path(self):
        # The original independent entry stat is retained, not reused from a
        # lease or another verification.  The exact r823 operation is gated.
        info = _call(self.state, directories.DeadlineDirectory.entry_stat,
                     self.directory, self.name,
                     legal=lambda value: type(value) is os.stat_result)
        return _cpu(self.state, _file_identity, info, legal=_file_identity_evidence)

    def _identity_at_handle(self):
        fd = _call(self.state, _raw_fileno, self.stream, legal=_integer)
        info = _call(self.state, _fstat, fd,
                     legal=lambda value: type(value) is os.stat_result)
        return _cpu(self.state, _file_identity, info, legal=_file_identity_evidence)

    def __enter__(self):
        _raise_first(self.state)
        if self.phase != _NEW:
            _fail("STATE_UNAVAILABLE")
        self.phase = _OPENING
        try:
            self.path_identity = self._identity_at_path()
            self.budget.file(self.path_identity["byte_size"])
            self.byte_size = self.path_identity["byte_size"]
            self.lease = _prepared(self.state, _read_lease,
                directories.DeadlineReadonlyLease, self.directory, self.name)
            _clock(self.state)
            stream = _read_enter(self.lease)
            if type(stream) is not io.BufferedReader:
                _fail("STATE_UNAVAILABLE")
            self.stream, self.lease_entered = stream, True
            _clock(self.state)
            self.handle_identity = self._identity_at_handle()
            _cpu(self.state, source._same,
                {key: value for key, value in self.path_identity.items() if key != "ctime_ns"},
                {key: value for key, value in self.handle_identity.items() if key != "ctime_ns"}, legal=_none)
            self.directory_identity = _cpu(self.state, _directory_identity,
                                           self.directory, legal=_directory_evidence)
            self.sha256, self.raw = self._read(collect=False)
            self._verify()
            self.phase = _ACTIVE
            _clock(self.state)
            return self
        except BaseException as failure:
            _remember(self.state, failure)
            self._cleanup()
            _raise_first(self.state)

    def _read_chunk(self, total):
        # L37's actual successful quantity, held-size and EOF legality belong
        # to this fixed read boundary, before its postclock can reject delivery.
        _clock(self.state)
        try:
            chunk = _raw_read(self.stream, 128 * 1024)
            if type(chunk) is not bytes or len(chunk) > 128 * 1024:
                _fail("STATE_UNAVAILABLE")
            total += len(chunk)
            if total > self.byte_size:
                source._fail("SOURCE_CHANGED", retriable=True)
            if not chunk:
                source._same(total, self.byte_size)
        except BaseException as failure:
            _remember(self.state, failure)
            _raise_first(self.state)
        _clock(self.state)
        return chunk, total

    def _read(self, *, collect):
        self._check()
        _call(self.state, _raw_seek, self.stream, 0, os.SEEK_SET, legal=_integer)
        digest, total, pieces = _cpu(self.state, _hash_new, legal=_hash_object), 0, []
        while True:
            self._check()
            chunk, total = self._read_chunk(total)
            if not chunk:
                break
            _cpu(self.state, _hash_update, digest, chunk, legal=_none)
            if collect:
                pieces.append(chunk)
        self._check()
        _cpu(self.state, source._same, total, self.byte_size, legal=_none)
        return (_cpu(self.state, _hash_hex, digest, legal=_sha_text),
                _cpu(self.state, _bytes_join, pieces, legal=_bytes) if collect else None)

    def bytes(self, maximum):
        try:
            if self.phase != _ACTIVE or type(maximum) is not int or maximum != self.maximum:
                _fail("STATE_UNAVAILABLE")
            if self.byte_size > maximum:
                source._fail("CONTROL_FILE_TOO_LARGE")
            self._verify()
            digest, raw = self._read(collect=True)
            self._verify_identities()
            _cpu(self.state, source._same, digest, self.sha256, legal=_none)
            self._verify()
            return raw
        except BaseException as failure:
            _remember(self.state, failure)
            _raise_first(self.state)

    def _verify_identities(self):
        self._check()
        _call(self.state, _directory_verify, self.directory, legal=_none)
        _cpu(self.state, source._same,
             _cpu(self.state, _directory_identity, self.directory, legal=_directory_evidence),
             self.directory_identity, legal=_none)
        _cpu(self.state, source._same, self._identity_at_path(), self.path_identity, legal=_none)
        _cpu(self.state, source._same, self._identity_at_handle(), self.handle_identity, legal=_none)

    def _verify(self):
        self._verify_identities()
        if self.sha256 is not None:
            digest = self._read(collect=False)[0]
            self._verify_identities()
            _cpu(self.state, source._same, digest, self.sha256, legal=_none)
        self._check()

    def verify(self):
        try:
            if self.phase != _ACTIVE:
                _fail("STATE_UNAVAILABLE")
            self._verify()
        except BaseException as failure:
            _remember(self.state, failure)
            _raise_first(self.state)

    def _cleanup(self):
        self.phase = _RETIRED
        if not self.lease_entered or self.lease_attempted:
            return
        self.lease_attempted = True
        _cleanup_clock(self.state)
        first, first_tb = self.state.first, self.state.first_tb
        try:
            result = _read_exit(self.lease, None if first is None else type(first),
                                first, first_tb)
            if result is not None:
                _fail("STATE_UNAVAILABLE")
        except BaseException as failure:
            _remember(self.state, failure)
        _cleanup_clock(self.state)

    def __exit__(self, kind, primary, tb):
        if primary is not None:
            _remember(self.state, primary, tb)
        if self.state.first is None:
            try:
                if self.phase != _ACTIVE:
                    _fail("STATE_UNAVAILABLE")
                # The original _finish verification precedes lease retirement.
                self._verify()
            except BaseException as failure:
                _remember(self.state, failure)
        self._cleanup()
        if self.state.first is not None and self.state.first is not primary:
            _raise_first(self.state)
        return None


class _FenceBudget:
    """Keep the fence's separate original aggregate counters, same token only."""
    __slots__ = ("state", "names", "name_bytes", "callback_primary")

    def __init__(self, token):
        self.state = _FirstState(token)
        self.names = self.name_bytes = 0
        self.callback_primary = None

    def clock_only(self):
        _clock(self.state)

    def poll(self):
        # The old lower callback was the reservation's clock_only, never user.
        self.clock_only()
        self.clock_only()
        self.clock_only()

    def before_next(self):
        if self.names >= fence.MAX_NAMES:
            fence._fail()

    def name(self, name):
        self.names += 1
        if type(name) is not str or not name:
            fence._fail()
        remaining = fence.MAX_NAME_BYTES - self.name_bytes
        if len(name) > remaining:
            fence._fail()
        size = len(name.encode("utf-8"))
        if size > remaining:
            fence._fail()
        self.name_bytes += size
        if name.rstrip(" .").casefold().startswith(fence.PREFIX):
            fence._fail(fence.REQUIRED)


def _census(directory, budget, *, terminal):
    check = budget.clock_only if terminal else budget.poll
    state = budget.state
    check()
    _call(state, _directory_verify, directory, legal=_none)
    check()
    names = set()
    with _Entries(state, directory) as entries:
        while True:
            check()
            budget.before_next()
            entry = _next_entry(state, entries)
            if entry is None:
                break
            name = _cpu(state, _entry_name, entry, legal=_text)
            _cpu(state, budget.name, name, legal=_none)
            if name in names:
                fence._fail()
            names.add(name)
        check()
    check()
    _call(state, _directory_verify, directory, legal=_none)
    check()
    return frozenset(names)


def _assert_legacy_write_allowed_locked(profile, token):
    # This complete lower semantic conversion finishes before the reservation
    # captures ALREADY_PRESENT/STATE_UNAVAILABLE as its own primary.
    budget = _FenceBudget(token)
    state = budget.state
    try:
        budget.poll()
        if type(profile) is not lifecycle.StorageProfile:
            fence._fail()
        expected_profile = _cpu(state, _profile_paths, profile, legal=_paths)
        guard = _call(state, _require_guard, profile,
                      legal=lambda value: type(value) is lifecycle.StorageGuard)
        parent = profile.database.parent
        budget.poll()
        prepared = _prepared(state, _directory, directories.DeadlineDirectory,
                              parent, deadline_token=token, base=parent)
        with _Scope(state, prepared, _directory_enter, _directory_exit,
                    directories.DeadlineDirectory) as directory:
            initial = _census(directory, budget, terminal=False)
            budget.poll()
            first = _census(directory, budget, terminal=True)
            second = _census(directory, budget, terminal=True)
            if initial != first or first != second:
                fence._fail()
            budget.clock_only()
            if (_cpu(state, _profile_paths, profile, legal=_paths) != expected_profile
                    or _call(state, _require_guard, profile) is not guard
                    or directory.path != parent):
                fence._fail()
            _call(state, _directory_verify, directory, legal=_none)
            budget.clock_only()
        budget.clock_only()
    except BaseException as error:
        if isinstance(error, OriginReservationError):
            # Same-token clock errors retain the original reservation-clock
            # callback priority instead of becoming a new fence source fault.
            raise
        if isinstance(error, fence.LegacyAuthorityFenceError):
            raise
        if isinstance(error, Exception):
            fence._fail()
        raise


def _same_guard(profile, expected_paths, guard, budget):
    state = budget.state
    budget.clock_only()
    if (_cpu(state, _profile_paths, profile, legal=_paths) != expected_paths
            or _call(state, _require_guard, profile,
                     legal=lambda value: type(value) is lifecycle.StorageGuard) is not guard):
        _fail("PROFILE_CHANGED")
    budget.clock_only()


def _physical(profile, stack, budget):
    state, token = budget.state, budget.effective_deadline
    roots, physical_directories = {}, []
    for name, path in zip(_ROOTS, (profile.database.parent, profile.media,
                                   profile.reports, profile.checkpoints)):
        budget.clock_only()
        prepared = _prepared(state, _directory, directories.DeadlineDirectory,
                              path, deadline_token=token, base=path)
        directory = stack.enter_context(_Scope(state, prepared, _directory_enter,
            _directory_exit, directories.DeadlineDirectory))
        if directory.deadline_token is not token or directory.path != path or directory.base != path:
            _fail("STATE_UNAVAILABLE")
        physical_directories.append(directory)
        roots[name] = {"path": _cpu(state, _key, path, legal=_text),
                      "identity": _cpu(state, _directory_identity, directory, legal=_directory_evidence)}
    parent = physical_directories[0]
    node_parent = _prepared(state, _node_directory, nodes.DeadlineNodeDirectory,
                            parent, budget.restricted)
    prepared_node = _prepared(state, _node, nodes.DeadlineHeldFile, node_parent)
    held_node = stack.enter_context(_Scope(state, prepared_node, _node_enter,
                                          _node_exit, nodes.DeadlineHeldFile))
    node_id = _cpu(state, _node_id,
                   _call(state, _node_bytes, held_node, contract.MAX_NODE_BYTES,
                         legal=lambda value: _bounded_bytes(value, contract.MAX_NODE_BYTES)), legal=_text)
    physical = {"profile_paths": list(_cpu(state, _profile_paths, profile, legal=_paths)),
                "roots": roots}
    node = {"node_id": node_id, **_cpu(state, _file_evidence, held_node, legal=_file_evidence_value)}
    return tuple(physical_directories), held_node, physical, node


def _verify_physical(profile, expected_paths, guard, physical_directories,
                     held_node, physical, node, budget):
    state = budget.state
    _same_guard(profile, expected_paths, guard, budget)
    for name, directory in zip(_ROOTS, physical_directories):
        if (_cpu(state, _key, directory.path, legal=_text) != physical["roots"][name]["path"]
                or _cpu(state, _directory_identity, directory, legal=_directory_evidence)
                != physical["roots"][name]["identity"]):
            _fail("PROFILE_CHANGED")
    _call(state, _node_verify, held_node, legal=_none)
    if (_cpu(state, _node_id,
            _call(state, _node_bytes, held_node, contract.MAX_NODE_BYTES,
                  legal=lambda value: _bounded_bytes(value, contract.MAX_NODE_BYTES)), legal=_text) != node["node_id"]
            or _cpu(state, _file_evidence, held_node, legal=_file_evidence_value)
            != {key: node[key] for key in contract._FILE}):
        _fail("PROFILE_CHANGED")
    _same_guard(profile, expected_paths, guard, budget)


def _names(parent, budget):
    state = budget.state
    budget.clock_only()
    _call(state, _directory_verify, parent, legal=_none)
    selected, seen = set(), set()
    with _Entries(state, parent) as entries:
        while True:
            budget.before_next()
            entry = _next_entry(state, entries)
            if entry is None:
                break
            name = _cpu(state, _entry_name, entry, legal=_text)
            _cpu(state, budget.name, name, legal=_none)
            if name in seen:
                _fail("STATE_UNAVAILABLE")
            seen.add(name)
            if name.rstrip(" .").casefold().startswith(fence.PREFIX):
                selected.add(name)
        budget.clock_only()
    _call(state, _directory_verify, parent, legal=_none)
    budget.clock_only()
    return frozenset(selected)


def _need_names(parent, expected, budget):
    if _names(parent, budget) != frozenset(expected):
        _fail("UNKNOWN_NAMES")


def _read_control(parent, name, stack, budget, maximum):
    state = budget.state
    prepared = _prepared(state, _control, _ControlHeld, parent, name,
                          budget=budget, maximum=maximum)
    held = stack.enter_context(_Scope(state, prepared, _control_enter,
                                     _control_exit, _ControlHeld))
    raw = _call(state, _control_bytes, held, maximum,
                legal=lambda value: _bounded_bytes(value, maximum))
    _cpu(state, _decode, raw, legal=_mapping)
    return held, raw


def _publish_control(profile, parent, name, operation, raw, stack, budget, maximum):
    state = budget.state
    budget.clock_only()
    published = _call(state, _publish, profile, parent, name,
        _cpu(state, _stage_name, name, operation, legal=_text), raw,
        deadline_token=budget.effective_deadline,
        legal=lambda value: type(value) is ordinary_publication.PublishedFile)
    held, observed = _read_control(parent, name, stack, budget, maximum)
    if (observed != raw
            or published.raw_sha256 != _cpu(state, _sha256_hex, raw, legal=_sha_text)
            or type(published.byte_size) is not int or published.byte_size != len(raw)
            or any(published.identity[key] != held.handle_identity[key]
                   or published.identity[key] != held.path_identity[key]
                   for key in _PUBLISHED_IDENTITY_KEYS)):
        _fail("PUBLICATION_MISMATCH")
    budget.clock_only()
    return held, observed


def _arguments(profile, domain_id, facility_id, init_operation_id, budget):
    state = budget.state
    budget.clock_only()
    if type(profile) is not lifecycle.StorageProfile:
        _fail("INVALID_ARGUMENT")
    _cpu(state, _uuid, domain_id, legal=_text)
    _cpu(state, _uuid, facility_id, legal=_text)
    _cpu(state, _uuid, init_operation_id, legal=_text)
    paths = _cpu(state, _profile_paths, profile, legal=_paths)
    guard = _call(state, _require_guard, profile,
                  legal=lambda value: type(value) is lifecycle.StorageGuard)
    budget.poll()  # The last and only user callback, before subject owners.
    _same_guard(profile, paths, guard, budget)
    return paths, guard


def _perform(profile, domain_id, facility_id, operation, budget, *,
             expected_first=None, expected_second=None):
    state, token = budget.state, budget.effective_deadline
    paths, guard = _arguments(profile, domain_id, facility_id, operation, budget)
    if expected_first is not None:
        _cpu(state, _sha, expected_first, legal=_text)
    if expected_second is not None:
        _cpu(state, _sha, expected_second, legal=_text)
    with ExitStack() as stack:
        if expected_first is None:
            try:
                _assert_legacy_write_allowed_locked(profile, token)
            except fence.LegacyAuthorityFenceError as error:
                _fail("ALREADY_PRESENT" if error.code == fence.REQUIRED else "STATE_UNAVAILABLE")
        physical_directories, held_node, physical, node = _physical(profile, stack, budget)
        parent = physical_directories[0]
        first_value = {"format_version": contract.FORMAT_VERSION, "family": contract.FAMILY,
            "kind": "origin_reservation", "init_operation_id": operation, "domain_id": domain_id,
            "facility_id": facility_id, "profile": _cpu(state, _deepcopy, physical, legal=_mapping),
            "authority_profile_paths_sha256": _cpu(state, _digest, physical["profile_paths"], legal=_sha_text),
            "physical_profile_sha256": _cpu(state, _digest, physical, legal=_sha_text),
            "node": _cpu(state, _deepcopy, node, legal=_mapping)}
        expected_raw = _cpu(state, _encode, first_value, legal=_bytes)
        previous_second = second_held = None
        if expected_first is None:
            _need_names(parent, set(), budget)
            _verify_physical(profile, paths, guard, physical_directories, held_node, physical, node, budget)
            first_held, first_raw = _publish_control(profile, parent, contract.RESERVATION_NAME,
                operation, expected_raw, stack, budget, contract.MAX_RESERVATION_BYTES)
            _need_names(parent, {contract.RESERVATION_NAME}, budget)
        else:
            names = _names(parent, budget)
            if names not in ({contract.RESERVATION_NAME}, {contract.RESERVATION_NAME, contract.OBSERVATION_NAME}):
                _fail("UNKNOWN_NAMES")
            first_held, first_raw = _read_control(parent, contract.RESERVATION_NAME,
                                                stack, budget, contract.MAX_RESERVATION_BYTES)
            if first_raw != expected_raw or _cpu(state, _sha256_hex, first_raw, legal=_sha_text) != expected_first:
                _fail("RESERVATION_MISMATCH")
            if contract.OBSERVATION_NAME in names:
                second_held, previous_second = _read_control(parent, contract.OBSERVATION_NAME,
                    stack, budget, contract.MAX_OBSERVATION_BYTES)
                _cpu(state, _cross_validate, first_raw, previous_second, legal=_none)
            if expected_second is not None and (previous_second is None
                    or _cpu(state, _sha256_hex, previous_second, legal=_sha_text) != expected_second):
                _fail("OBSERVATION_MISMATCH")
        # The original first-first boundary includes our independent live FD,
        # full bytes and identity, not merely publisher metadata.
        _call(state, _control_verify, first_held, legal=_none)
        _verify_physical(profile, paths, guard, physical_directories, held_node, physical, node, budget)
        context = _call(state, _origin_context, profile, domain_id=domain_id,
            facility_id=facility_id, init_operation_id=operation,
            deadline_token=token, check_cancelled=budget.clock_only)
        with _Scope(state, context, _origin_enter, _origin_exit,
                    ordinary_origin.OriginObservation, same=False, exit_false=True) as observation:
            second_raw = _cpu(state, _encode_observation, first_raw,
                             observation.intent_bytes, observation.evidence_bytes, legal=_bytes)
            if previous_second is not None:
                if second_raw != previous_second:
                    _fail("OBSERVATION_MISMATCH")
                _call(state, _control_verify, second_held, legal=_none)
            else:
                _need_names(parent, {contract.RESERVATION_NAME}, budget)
                _verify_physical(profile, paths, guard, physical_directories, held_node, physical, node, budget)
                second_held, second_raw = _publish_control(profile, parent,
                    contract.OBSERVATION_NAME, operation, second_raw, stack, budget,
                    contract.MAX_OBSERVATION_BYTES)
            _cpu(state, _cross_validate, first_raw, second_raw, legal=_none)
            _call(state, _control_verify, first_held, legal=_none)
            _call(state, _control_verify, second_held, legal=_none)
        # Do not deliver the yielded DTO before the complete origin normal exit.
        _verify_physical(profile, paths, guard, physical_directories, held_node, physical, node, budget)
        _call(state, _control_verify, first_held, legal=_none)
        _call(state, _control_verify, second_held, legal=_none)
        _need_names(parent, {contract.RESERVATION_NAME, contract.OBSERVATION_NAME}, budget)
        _need_names(parent, {contract.RESERVATION_NAME, contract.OBSERVATION_NAME}, budget)
        _same_guard(profile, paths, guard, budget)
        budget.clock_only()
    _same_guard(profile, paths, guard, budget)
    budget.clock_only()
    return _cpu(state, _result, operation,
        _cpu(state, _sha256_hex, first_raw, legal=_sha_text),
        _cpu(state, _sha256_hex, second_raw, legal=_sha_text),
        legal=lambda value: type(value) is OriginReservationResult)


def _entry(profile, domain_id, facility_id, operation, callback, deadline_token,
           *, expected_first=None, expected_second=None, resume=False):
    budget = None
    try:
        # A missing resume hash still has the token-domain prerequisite, with
        # no clock/factory.  Other routes perform DOMAIN once in _make_budget.
        if resume and expected_first is None:
            _domain(deadline_token)
            _fail("INVALID_ARGUMENT")
        budget = _make_budget(deadline_token, callback)
        return _perform(profile, domain_id, facility_id, operation, budget,
                        expected_first=expected_first, expected_second=expected_second)
    except BaseException as error:
        if budget is not None and budget.callback_primary is not None:
            if error is not budget.callback_primary:
                _note(budget.callback_primary)
                raise BaseException.with_traceback(budget.callback_primary,
                                                  budget.callback_tb) from None
            raise
        if isinstance(error, (OriginReservationError, contract.OriginReservationContractError)):
            raise
        if isinstance(error, Exception):
            _fail("STATE_UNAVAILABLE")
        raise


def reserve_origin_locked(profile, *, domain_id, facility_id, init_operation_id,
                          deadline_token, check_cancelled=None):
    """Publish both original declarations under the caller's actual EX."""
    return _entry(profile, domain_id, facility_id, init_operation_id,
                  check_cancelled, deadline_token)


def resume_origin_locked(profile, *, domain_id, facility_id, init_operation_id,
                         expected_reservation_sha256, expected_observation_sha256=None,
                         deadline_token, check_cancelled=None):
    """Require full original first/second bytes and freshly re-observe origin."""
    return _entry(profile, domain_id, facility_id, init_operation_id,
        check_cancelled, deadline_token, expected_first=expected_reservation_sha256,
        expected_second=expected_observation_sha256, resume=True)
