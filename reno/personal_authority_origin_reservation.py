"""Reserve an init UUID before observing origin SQL; never issue write rights.

Both fixed files are immutable declarations. The first binds actual physical
roots/node; only after its publication and independent readback does the real
origin producer run. Its exact bytes form the second file. Raised source,
verification, close or deadline errors prevent delivery and retain all durable
files. An explicit resume cannot adopt partial stages or silently refresh facts.
Neither a result nor an existing same-named file proves a live resource owner,
complete source_closed, namespace enrollment, grant, journal, CAS or witness.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import os
import time

from . import personal_authority_legacy_fence as fence
from . import personal_authority_origin_reservation_contract as contract
from . import personal_authority_origin_reservation_io as publication
from . import personal_authority_origin_source as origin
from . import personal_preservation_files as files
from . import personal_preservation_source as source
from . import storage_lifecycle as lifecycle, sync_media as safe

MAX_SECONDS = 30.0
MAX_NAMES = 4096
MAX_NAME_BYTES = 1024 * 1024
_ROOTS = ("data", "media", "reports", "checkpoints")
_PUBLISHED_IDENTITY_KEYS = ("device", "inode", "byte_size", "mtime_ns")


class OriginReservationError(ValueError):
    def __init__(self, suffix):
        self.code = "ORIGIN_RESERVATION_" + suffix
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class OriginReservationResult:
    init_operation_id: str
    reservation_sha256: str
    observation_sha256: str


def _fail(suffix):
    raise OriginReservationError(suffix) from None


def _note(primary):
    try:
        BaseException.add_note(primary, "ORIGIN_RESERVATION_CLEANUP_FAILED")
    except BaseException:
        pass


@contextmanager
def _preserve_primary(context):
    resource = context.__enter__()
    try:
        yield resource
    except BaseException as primary:
        original_tb = BaseException.__dict__["__traceback__"].__get__(primary)
        try:
            context.__exit__(type(primary), primary, original_tb)
        except BaseException as cleanup:
            if cleanup is not primary:
                _note(primary)
        raise
    else:
        context.__exit__(None, None, None)


class _Budget:
    def __init__(self, callback):
        self.deadline = time.monotonic() + MAX_SECONDS
        self.callback, self.callback_primary = callback, None
        self.names = self.name_bytes = 0
        if callback is not None and not callable(callback):
            _fail("INVALID_ARGUMENT")

    def clock_only(self):
        if self.callback_primary is not None:
            raise self.callback_primary
        if time.monotonic() >= self.deadline:
            _fail("DEADLINE")

    def poll(self):
        self.clock_only()
        if self.callback is not None:
            try:
                self.callback()
            except BaseException as primary:
                self.callback_primary = primary
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


class _FileBudget:
    """One bounded metadata owner using the factory's original deadline."""
    def __init__(self, budget, maximum):
        self.budget, self.maximum, self.count = budget, maximum, 0

    def check(self):
        self.budget.clock_only()

    def file(self, size):
        self.check()
        self.count += 1
        if type(size) is not int or not 0 <= size <= self.maximum or self.count != 1:
            _fail("STATE_UNAVAILABLE")


def _same_guard(profile, expected_paths, guard, budget):
    budget.clock_only()
    if (tuple(profile.identity) != expected_paths
            or lifecycle.require_guard(profile, exclusive=True) is not guard):
        _fail("PROFILE_CHANGED")
    budget.clock_only()


def _physical(profile, stack, budget):
    roots, directories = {}, []
    for name, path in zip(_ROOTS, (profile.database.parent, profile.media, profile.reports, profile.checkpoints)):
        budget.clock_only()
        directory = stack.enter_context(safe._Directory(path, base=path))
        directories.append(directory)
        roots[name] = {"path": lifecycle._key(path), "identity": files.directory_identity(directory)}
    parent = directories[0]
    held_node = source._HeldFile(parent, "node-identity.json", stack,
                                _FileBudget(budget, contract.MAX_NODE_BYTES))
    node_id = origin._node_id(held_node.bytes(contract.MAX_NODE_BYTES))
    physical = {"profile_paths": list(profile.identity), "roots": roots}
    node = {"node_id": node_id, **origin._file_evidence(held_node)}
    return tuple(directories), held_node, physical, node


def _verify_physical(profile, expected_paths, guard, directories, held_node, physical, node, budget):
    _same_guard(profile, expected_paths, guard, budget)
    for name, directory in zip(_ROOTS, directories):
        if (lifecycle._key(directory.path) != physical["roots"][name]["path"]
                or files.directory_identity(directory) != physical["roots"][name]["identity"]):
            _fail("PROFILE_CHANGED")
    held_node.verify()
    if (origin._node_id(held_node.bytes(contract.MAX_NODE_BYTES)) != node["node_id"]
            or origin._file_evidence(held_node) != {key: node[key] for key in contract._FILE}):
        _fail("PROFILE_CHANGED")
    _same_guard(profile, expected_paths, guard, budget)


def _names(parent, budget):
    """Bound all entries; select the broad fence prefix without subtree reads."""
    budget.clock_only()
    parent.verify()
    selected, seen = set(), set()
    target = safe._native_path(parent.path) if os.name == "nt" else parent.fd
    with _preserve_primary(os.scandir(target)) as entries:
        while True:
            budget.before_next()
            try:
                entry = next(entries)
            except StopIteration:
                break
            name = entry.name
            budget.name(name)
            if name in seen:
                _fail("STATE_UNAVAILABLE")
            seen.add(name)
            if name.rstrip(" .").casefold().startswith(fence.PREFIX):
                selected.add(name)
        budget.clock_only()
    parent.verify()
    budget.clock_only()
    return frozenset(selected)


def _need_names(parent, expected, budget):
    if _names(parent, budget) != frozenset(expected):
        _fail("UNKNOWN_NAMES")


def _read_control(parent, name, stack, budget, maximum):
    held = source._HeldFile(parent, name, stack, _FileBudget(budget, maximum))
    raw = held.bytes(maximum)
    contract.decode(raw)
    return held, raw


def _publish(profile, parent, name, operation, raw, stack, budget, maximum):
    budget.clock_only()
    published = publication.publish_new_locked(profile, parent, name,
        contract.stage_name(name, operation), raw, budget.clock_only)
    held, observed = _read_control(parent, name, stack, budget, maximum)
    if (observed != raw or published.raw_sha256 != hashlib.sha256(raw).hexdigest()
            or type(published.byte_size) is not int or published.byte_size != len(raw)
            or any(published.identity[key] != held.handle_identity[key]
                   or published.identity[key] != held.path_identity[key]
                   for key in _PUBLISHED_IDENTITY_KEYS)):
        _fail("PUBLICATION_MISMATCH")
    budget.clock_only()
    return held, observed


def _arguments(profile, domain_id, facility_id, init_operation_id, budget):
    budget.clock_only()
    if type(profile) is not lifecycle.StorageProfile:
        _fail("INVALID_ARGUMENT")
    contract._uuid(domain_id)
    contract._uuid(facility_id)
    contract._uuid(init_operation_id)
    paths = tuple(profile.identity)
    guard = lifecycle.require_guard(profile, exclusive=True)
    # This is the last user callback. All downstream producer polls receive
    # clock_only, which clamps their local budgets to this original deadline.
    budget.poll()
    _same_guard(profile, paths, guard, budget)
    return paths, guard


def _perform(profile, domain_id, facility_id, operation, budget, *, expected_first=None, expected_second=None):
    paths, guard = _arguments(profile, domain_id, facility_id, operation, budget)
    if expected_first is not None:
        contract._sha(expected_first)
    if expected_second is not None:
        contract._sha(expected_second)
    with _preserve_primary(ExitStack()) as stack:
        if expected_first is None:
            try:
                fence.assert_legacy_write_allowed_locked(profile, budget.clock_only)
            except fence.LegacyAuthorityFenceError as error:
                _fail("ALREADY_PRESENT" if error.code == fence.REQUIRED else "STATE_UNAVAILABLE")
        directories, held_node, physical, node = _physical(profile, stack, budget)
        parent = directories[0]
        first_value = {"format_version": contract.FORMAT_VERSION, "family": contract.FAMILY,
            "kind": "origin_reservation", "init_operation_id": operation, "domain_id": domain_id,
            "facility_id": facility_id, "profile": deepcopy(physical),
            "authority_profile_paths_sha256": contract._digest(physical["profile_paths"]),
            "physical_profile_sha256": contract._digest(physical), "node": deepcopy(node)}
        expected_raw = contract.encode(first_value)
        previous_second = second_held = None
        if expected_first is None:
            _need_names(parent, set(), budget)
            _verify_physical(profile, paths, guard, directories, held_node, physical, node, budget)
            first_held, first_raw = _publish(profile, parent, contract.RESERVATION_NAME, operation,
                expected_raw, stack, budget, contract.MAX_RESERVATION_BYTES)
            _need_names(parent, {contract.RESERVATION_NAME}, budget)
        else:
            names = _names(parent, budget)
            if names not in ({contract.RESERVATION_NAME}, {contract.RESERVATION_NAME, contract.OBSERVATION_NAME}):
                _fail("UNKNOWN_NAMES")
            first_held, first_raw = _read_control(parent, contract.RESERVATION_NAME, stack,
                                               budget, contract.MAX_RESERVATION_BYTES)
            if first_raw != expected_raw or hashlib.sha256(first_raw).hexdigest() != expected_first:
                _fail("RESERVATION_MISMATCH")
            if contract.OBSERVATION_NAME in names:
                second_held, previous_second = _read_control(parent, contract.OBSERVATION_NAME,
                    stack, budget, contract.MAX_OBSERVATION_BYTES)
                contract.cross_validate(first_raw, previous_second)
            if expected_second is not None and (previous_second is None
                    or hashlib.sha256(previous_second).hexdigest() != expected_second):
                _fail("OBSERVATION_MISMATCH")
        # The immutable first file has now been independently read through its
        # own held FD. No origin capture/SQL facts are produced before this gate.
        first_held.verify()
        _verify_physical(profile, paths, guard, directories, held_node, physical, node, budget)
        with origin.capture_locked(profile, domain_id=domain_id, facility_id=facility_id,
                                   init_operation_id=operation, check_cancelled=budget.clock_only) as observation:
            second_raw = contract.encode_observation(first_raw, observation.intent_bytes, observation.evidence_bytes)
            if previous_second is not None:
                if second_raw != previous_second:
                    _fail("OBSERVATION_MISMATCH")
                second_held.verify()
            else:
                _need_names(parent, {contract.RESERVATION_NAME}, budget)
                _verify_physical(profile, paths, guard, directories, held_node, physical, node, budget)
                second_held, second_raw = _publish(profile, parent, contract.OBSERVATION_NAME, operation,
                    second_raw, stack, budget, contract.MAX_OBSERVATION_BYTES)
            contract.cross_validate(first_raw, second_raw)
            first_held.verify()
            second_held.verify()
        # A yielded DTO is not success: the real capture must return normally
        # from its terminal source verification, source exits and deadline.
        _verify_physical(profile, paths, guard, directories, held_node, physical, node, budget)
        first_held.verify()
        second_held.verify()
        _need_names(parent, {contract.RESERVATION_NAME, contract.OBSERVATION_NAME}, budget)
        _need_names(parent, {contract.RESERVATION_NAME, contract.OBSERVATION_NAME}, budget)
        _same_guard(profile, paths, guard, budget)
        budget.clock_only()
    # Include every registered ordinary resource exit before result delivery.
    _same_guard(profile, paths, guard, budget)
    budget.clock_only()
    return OriginReservationResult(operation, hashlib.sha256(first_raw).hexdigest(),
                                   hashlib.sha256(second_raw).hexdigest())


def _entry(profile, domain_id, facility_id, operation, callback, *, expected_first=None, expected_second=None):
    budget = None
    try:
        budget = _Budget(callback)
        return _perform(profile, domain_id, facility_id, operation, budget,
                        expected_first=expected_first, expected_second=expected_second)
    except BaseException as error:
        if budget is not None and budget.callback_primary is not None:
            if error is not budget.callback_primary:
                _note(budget.callback_primary)
                raise budget.callback_primary from None
            raise
        if isinstance(error, (OriginReservationError, contract.OriginReservationContractError)):
            raise
        if isinstance(error, Exception):
            _fail("STATE_UNAVAILABLE")
        raise


def reserve_origin_locked(profile, *, domain_id, facility_id, init_operation_id, check_cancelled=None):
    """Create the two fixed immutable declarations under the caller's actual EX."""
    return _entry(profile, domain_id, facility_id, init_operation_id, check_cancelled)


def resume_origin_locked(profile, *, domain_id, facility_id, init_operation_id,
                         expected_reservation_sha256, expected_observation_sha256=None, check_cancelled=None):
    """Require the exact first identity/raw and freshly re-observe origin facts.

    Existing second bytes must equal this new actual observation in full. Raw
    knowledge/SQL-file changes can therefore reject even unchanged logical
    personal summaries. Partial stages and unknown names are never adopted.
    """
    if expected_reservation_sha256 is None:
        _fail("INVALID_ARGUMENT")
    return _entry(profile, domain_id, facility_id, init_operation_id, check_cancelled,
                  expected_first=expected_reservation_sha256, expected_second=expected_observation_sha256)
