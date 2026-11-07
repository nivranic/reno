"""Bind immutable origin bytes to new Windows namespace filesystem facts.

The caller keeps its actual EX. Only reservation-only states may resume; an
unknown stage or a started but incomplete directory is never adopted. This
module does not install SQL/Store, issue write rights, or prove source_closed.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import io
import os
import time

from . import personal_authority_legacy_fence as fence
from . import personal_authority_namespace3_contract as contract
from . import personal_authority_namespace3_io as publication
from . import personal_authority_origin_reservation_contract as origin_contract
from . import personal_authority_origin_source as origin
from . import personal_preservation_files as files
from . import personal_preservation_source as source
from . import storage_lifecycle as lifecycle, sync_media as safe

MAX_SECONDS = 30.0
MAX_NAMES = 4096
MAX_NAME_BYTES = 1024 * 1024
MAX_CONTROL_BYTES = 1024 * 1024
_ROOTS = ("data", "media", "reports", "checkpoints")
_ORIGIN_NAMES = frozenset({origin_contract.RESERVATION_NAME, origin_contract.OBSERVATION_NAME})
_RESERVED_NAMES = _ORIGIN_NAMES | {contract.RESERVATION_NAME}
_COMPLETE_NAMES = _RESERVED_NAMES | {contract.ROOT_NAME, contract.HEADER_NAME}


class Namespace3Error(ValueError):
    def __init__(self, suffix):
        self.code = "NAMESPACE3_" + suffix
        super().__init__(self.code)


Error = Namespace3Error


@dataclass(frozen=True, slots=True)
class Namespace3FilesystemFacts:
    scope: str
    init_operation_id: str
    domain_id: str
    facility_id: str
    origin_reservation_sha256: str
    origin_observation_sha256: str
    namespace_reservation_sha256: str
    root_seal_sha256: str
    header_sha256: str
    creation_kind: str


def _fail(suffix):
    raise Namespace3Error(suffix) from None


def _note(primary):
    try:
        BaseException.add_note(primary, "NAMESPACE3_CLEANUP_FAILED")
    except BaseException:
        pass


@contextmanager
def _preserve_primary(context):
    resource = context.__enter__()
    try:
        yield resource
    except BaseException as primary:
        try:
            context.__exit__(type(primary), primary,
                BaseException.__dict__["__traceback__"].__get__(primary))
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
        self.names = self.name_bytes = self.control_bytes = 0
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
            _fail("LIMIT")

    def name(self, value):
        self.clock_only()
        remaining = MAX_NAME_BYTES - self.name_bytes
        if type(value) is not str or not value or len(value) > remaining:
            _fail("LIMIT")
        try:
            size = len(value.encode("utf-8", errors="strict"))
        except UnicodeError:
            _fail("LIMIT")
        if size > remaining:
            _fail("LIMIT")
        self.names += 1
        self.name_bytes += size
        self.clock_only()

    def request(self, requested):
        self.clock_only()
        if type(requested) is not int or requested <= 0:
            _fail("LIMIT")
        remaining = MAX_CONTROL_BYTES - self.control_bytes
        if remaining <= 0:
            _fail("LIMIT")
        return min(requested, remaining)

    def charge(self, actual):
        if type(actual) is not int or actual < 0 or actual > MAX_CONTROL_BYTES - self.control_bytes:
            _fail("LIMIT")
        self.control_bytes += actual
        self.clock_only()


class _NodeStream:
    """Budget every real read while retaining the original FileIO owner FD."""
    def __init__(self, stream, budget):
        self.stream, self.budget = stream, budget
        self.retired, self.close_failure = False, None
        try:
            if type(stream) is not io.BufferedReader or type(stream.raw) is not io.FileIO:
                _fail("PROFILE_CHANGED")
            # The original buffer remains the sole closing owner. Do not call
            # its read/seek methods, which can prefetch beyond charged bytes.
            self.raw_stream = stream.raw
        except BaseException as primary:
            try:
                self.close()
            except BaseException:
                _note(primary)
            raise

    def _live(self):
        if self.retired:
            if self.close_failure is not None:
                raise self.close_failure
            _fail("PROFILE_CHANGED")
        self.budget.clock_only()

    def __enter__(self):
        try:
            self._live()
        except BaseException as primary:
            try:
                self.close()
            except BaseException:
                _note(primary)
            raise
        return self

    def read(self, requested):
        self._live()
        if type(requested) is not int or requested <= 0:
            _fail("LIMIT")
        amount = self.budget.request(min(requested, origin_contract.MAX_NODE_BYTES + 1))
        raw = self.raw_stream.read(amount)
        if type(raw) is not bytes or len(raw) > amount:
            _fail("PROFILE_CHANGED")
        self.budget.charge(len(raw))
        return raw

    def seek(self, offset, whence=0):
        self._live()
        result = self.raw_stream.seek(offset, whence)
        self.budget.clock_only()
        return result

    def fileno(self):
        self._live()
        return self.raw_stream.fileno()

    def close(self):
        if self.retired:
            if self.close_failure is not None:
                raise self.close_failure
            return
        self.retired = True
        try:
            self.stream.close()
        except BaseException as error:
            self.close_failure = error
            raise

    def __exit__(self, _kind, primary, _tb):
        try:
            self.close()
        except BaseException:
            if primary is None:
                raise
            _note(primary)
        return None


class _NodeDirectory:
    def __init__(self, directory, budget):
        self.directory, self.budget, self.path = directory, budget, directory.path

    def verify(self):
        self.budget.clock_only()
        self.directory.verify()

    def entry_stat(self, name):
        self.verify()
        return self.directory.entry_stat(name)

    def open_file(self, name):
        self.verify()
        if name != "node-identity.json":
            _fail("PROFILE_CHANGED")
        return _NodeStream(self.directory.open_file(name), self.budget)


class _NodeBudget:
    def __init__(self, budget):
        self.budget, self.count = budget, 0

    def check(self):
        self.budget.clock_only()

    def file(self, size):
        self.check()
        self.count += 1
        if type(size) is not int or not 0 <= size <= origin_contract.MAX_NODE_BYTES or self.count != 1:
            _fail("PROFILE_CHANGED")


def _same_guard(profile, paths, guard, budget):
    budget.clock_only()
    if tuple(profile.identity) != paths or lifecycle.require_guard(profile, exclusive=True) is not guard:
        _fail("PROFILE_CHANGED")
    budget.clock_only()


def _physical(profile, stack, budget):
    roots, directories = {}, []
    for name, path in zip(_ROOTS, (profile.database.parent, profile.media, profile.reports, profile.checkpoints)):
        budget.clock_only()
        directory = stack.enter_context(_preserve_primary(safe._Directory(path, base=path)))
        directories.append(directory)
        roots[name] = {"path": lifecycle._key(path), "identity": files.directory_identity(directory)}
    node = source._HeldFile(_NodeDirectory(directories[0], budget), "node-identity.json", stack, _NodeBudget(budget))
    node_raw = node.bytes(origin_contract.MAX_NODE_BYTES)
    physical = {"profile_paths": list(profile.identity), "roots": roots}
    evidence = {"node_id": origin._node_id(node_raw), **origin._file_evidence(node)}
    return tuple(directories), node, physical, evidence


def _verify_physical(profile, paths, guard, directories, node, physical, evidence, budget):
    _same_guard(profile, paths, guard, budget)
    for name, directory in zip(_ROOTS, directories):
        if (lifecycle._key(directory.path) != physical["roots"][name]["path"]
                or files.directory_identity(directory) != physical["roots"][name]["identity"]):
            _fail("PROFILE_CHANGED")
    node.verify()
    if (origin._node_id(node.bytes(origin_contract.MAX_NODE_BYTES)) != evidence["node_id"]
            or origin._file_evidence(node) != {key: evidence[key] for key in origin_contract._FILE}):
        _fail("PROFILE_CHANGED")
    _same_guard(profile, paths, guard, budget)


def _names(directory, budget, *, outer):
    budget.clock_only()
    directory.verify()
    selected, seen = set(), set()
    with _preserve_primary(os.scandir(safe._native_path(directory.path))) as entries:
        while True:
            budget.before_next()
            try:
                entry = next(entries)
            except StopIteration:
                break
            name = entry.name
            budget.name(name)
            if name in seen:
                _fail("UNKNOWN_NAMES")
            seen.add(name)
            if not outer or name.rstrip(" .").casefold().startswith(fence.PREFIX):
                selected.add(name)
    directory.verify()
    budget.clock_only()
    return frozenset(selected)


def _need_names(directory, expected, budget, *, outer=True):
    if _names(directory, budget, outer=outer) != frozenset(expected):
        _fail("UNKNOWN_NAMES")


def _read_control(profile, parent, name, maximum, stack, budget):
    held = stack.enter_context(_preserve_primary(publication.read_control_locked(profile, parent, name, budget, maximum)))
    return held, held.raw


def _expect_sha(raw, expected):
    if hashlib.sha256(raw).hexdigest() != expected:
        _fail("ORIGIN_MISMATCH")


def _fresh_origin(profile, domain, facility, operation, second, budget):
    budget.clock_only()
    original_intent = origin_contract._blob(second["origin_intent_base64"], origin_contract.MAX_INTENT_BYTES)
    original_evidence = origin_contract._blob(second["origin_evidence_base64"], origin_contract.MAX_EVIDENCE_BYTES)
    with _preserve_primary(origin.capture_locked(profile, domain_id=domain, facility_id=facility,
          init_operation_id=operation, check_cancelled=budget.clock_only)) as observed:
        if (type(observed) is not origin.OriginObservation or observed.intent_bytes != original_intent
                or observed.evidence_bytes != original_evidence):
            _fail("ORIGIN_MISMATCH")
        budget.clock_only()
    # A producer yield was not success; all its normal exit work precedes this.
    budget.clock_only()


def _common(kind, first):
    return {"format_version": contract.FORMAT_VERSION, "family": contract.FAMILY,
            "kind": kind, "scope": contract.SCOPE,
            "init_operation_id": first["init_operation_id"], "domain_id": first["domain_id"],
            "facility_id": first["facility_id"]}


def _reservation(first_raw, second_raw):
    first, second = origin_contract.decode(first_raw), origin_contract.decode(second_raw)
    value = {**_common("namespace_reservation", first), "profile": deepcopy(first["profile"]),
        "node": deepcopy(first["node"]), "layout": deepcopy(contract.LAYOUT), "creation_kind": contract.CREATION_KIND,
        "origin": {"reservation_sha256": hashlib.sha256(first_raw).hexdigest(),
            "observation_sha256": hashlib.sha256(second_raw).hexdigest(),
            "intent_sha256": second["origin_intent_sha256"], "evidence_sha256": second["origin_evidence_sha256"],
            "authority_profile_paths_sha256": first["authority_profile_paths_sha256"],
            "physical_profile_sha256": first["physical_profile_sha256"],
            "authority_profile_paths_role": contract.AUTHORITY_ROLE, "physical_profile_role": contract.PHYSICAL_ROLE}}
    raw = contract.encode(value)
    contract.cross_validate_reservation(first_raw, second_raw, raw)
    return raw


def _publish(profile, directory, name, operation, raw, stack, budget, maximum):
    published = publication.publish_new_locked(profile, directory, name,
                                               contract.stage_name(name, operation), raw, budget)
    # Independent held readback must follow the whole publisher's normal exit.
    held, observed = _read_control(profile, directory, name, maximum, stack, budget)
    if (type(published) is not publication.PublishedFile or observed != raw
            or published.raw_sha256 != hashlib.sha256(raw).hexdigest()
            or type(published.byte_size) is not int or published.byte_size != len(raw)
            or published.native_identity != held.native_identity or published.path_identity != held.path_identity):
        _fail("PUBLICATION_MISMATCH")
    budget.clock_only()
    return held, observed


def _create(profile, parent, first_raw, second_raw, reservation_raw, budget):
    first = origin_contract.decode(first_raw)
    operation = first["init_operation_id"]
    with _preserve_primary(ExitStack()) as stack:
        root = stack.enter_context(_preserve_primary(publication.create_root_locked(profile, parent, budget)))
        _need_names(root, set(), budget, outer=False)
        creator = {"creation_kind": contract.CREATION_KIND, "private_policy": contract.PRIVATE_POLICY,
            "parent_native_identity": dict(parent.native_identity), "parent_path_identity": dict(parent.path_identity),
            "root_native_identity": dict(root.native_identity), "root_path_identity": dict(root.path_identity)}
        seal_raw = contract.encode({**_common("namespace_root_seal", first),
            "namespace_reservation_sha256": hashlib.sha256(reservation_raw).hexdigest(),
            "root_name": contract.ROOT_NAME, "creator": creator})
        _publish(profile, root, contract.ROOT_SEAL_NAME, operation, seal_raw, stack, budget, contract.MAX_ROOT_SEAL_BYTES)
        header_raw = contract.encode({**_common("namespace_header", first),
            "namespace_reservation_sha256": hashlib.sha256(reservation_raw).hexdigest(),
            "root_seal_sha256": hashlib.sha256(seal_raw).hexdigest(), "root_name": contract.ROOT_NAME,
            "creation_kind": contract.CREATION_KIND, "root_native_identity": dict(root.native_identity),
            "root_path_identity": dict(root.path_identity)})
        contract.cross_validate_complete(first_raw, second_raw, reservation_raw, seal_raw, header_raw)
        _publish(profile, parent, contract.HEADER_NAME, operation, header_raw, stack, budget, contract.MAX_HEADER_BYTES)
        _need_names(root, {contract.ROOT_SEAL_NAME}, budget, outer=False)
        _need_names(parent, _COMPLETE_NAMES, budget)
        root.verify()
    budget.clock_only()
    return seal_raw, header_raw


def _verify_complete(profile, first_raw, second_raw, reservation_raw, seal_sha, header_sha, budget):
    with _preserve_primary(ExitStack()) as stack:
        parent = stack.enter_context(_preserve_primary(publication.open_parent_locked(profile, budget)))
        _need_names(parent, _COMPLETE_NAMES, budget)
        pair_first, pair_first_raw = _read_control(profile, parent, origin_contract.RESERVATION_NAME,
                                                  origin_contract.MAX_RESERVATION_BYTES, stack, budget)
        pair_second, pair_second_raw = _read_control(profile, parent, origin_contract.OBSERVATION_NAME,
                                                    origin_contract.MAX_OBSERVATION_BYTES, stack, budget)
        if pair_first_raw != first_raw or pair_second_raw != second_raw:
            _fail("ORIGIN_MISMATCH")
        _, observed_reservation = _read_control(profile, parent, contract.RESERVATION_NAME,
                                                contract.MAX_RESERVATION_BYTES, stack, budget)
        if observed_reservation != reservation_raw:
            _fail("PUBLICATION_MISMATCH")
        root = stack.enter_context(_preserve_primary(publication.open_root_locked(profile, parent, budget)))
        _need_names(root, {contract.ROOT_SEAL_NAME}, budget, outer=False)
        _, seal_raw = _read_control(profile, root, contract.ROOT_SEAL_NAME, contract.MAX_ROOT_SEAL_BYTES, stack, budget)
        _, header_raw = _read_control(profile, parent, contract.HEADER_NAME, contract.MAX_HEADER_BYTES, stack, budget)
        _expect_sha(seal_raw, seal_sha)
        _expect_sha(header_raw, header_sha)
        contract.cross_validate_complete(first_raw, second_raw, reservation_raw, seal_raw, header_raw)
        creator = contract.decode(seal_raw)["creator"]
        if (parent.native_identity != creator["parent_native_identity"]
                or parent.path_identity != creator["parent_path_identity"]
                or root.native_identity != creator["root_native_identity"]
                or root.path_identity != creator["root_path_identity"]):
            _fail("ROOT_IDENTITY_MISMATCH")
        pair_first.bytes()
        pair_second.bytes()
        root.verify()
        _need_names(root, {contract.ROOT_SEAL_NAME}, budget, outer=False)
    budget.clock_only()
    return seal_raw, header_raw


def _arguments(profile, domain, facility, operation, expected_first, expected_second,
               expected_reservation, expected_seal, expected_header, mode, budget):
    budget.clock_only()
    if type(profile) is not lifecycle.StorageProfile:
        _fail("INVALID_ARGUMENT")
    for value in (domain, facility, operation):
        contract._uuid(value)
    required = (expected_first, expected_second)
    if mode in {"resume", "verify"}:
        required += (expected_reservation,)
    if mode == "verify":
        required += (expected_seal, expected_header)
    for value in required:
        contract._sha(value)
    paths = tuple(profile.identity)
    try:
        guard = lifecycle.require_guard(profile, exclusive=True)
    except lifecycle.StorageLifecycleError:
        _fail("GUARD_REQUIRED")
    _same_guard(profile, paths, guard, budget)
    budget.poll()
    _same_guard(profile, paths, guard, budget)
    if os.name != "nt":
        _fail("UNSUPPORTED")
    return paths, guard


def _perform(profile, domain, facility, operation, expected_first, expected_second,
             expected_reservation, expected_seal, expected_header, mode, budget):
    paths, guard = _arguments(profile, domain, facility, operation, expected_first, expected_second,
                             expected_reservation, expected_seal, expected_header, mode, budget)
    with _preserve_primary(ExitStack()) as stack:
        directories, node, physical, evidence = _physical(profile, stack, budget)
        parent = stack.enter_context(_preserve_primary(publication.open_parent_locked(profile, budget, write=mode != "verify")))
        if parent.path_identity != physical["roots"]["data"]["identity"]:
            _fail("PROFILE_CHANGED")
        selected = _names(parent, budget, outer=True)
        recognized = _ORIGIN_NAMES if mode == "init" else (_RESERVED_NAMES if mode == "resume" else _COMPLETE_NAMES)
        if selected - _COMPLETE_NAMES:
            _fail("UNKNOWN_NAMES")
        if selected != recognized:
            _fail("STATE_INVALID")
        first_held, first_raw = _read_control(profile, parent, origin_contract.RESERVATION_NAME,
                                             origin_contract.MAX_RESERVATION_BYTES, stack, budget)
        second_held, second_raw = _read_control(profile, parent, origin_contract.OBSERVATION_NAME,
                                               origin_contract.MAX_OBSERVATION_BYTES, stack, budget)
        _expect_sha(first_raw, expected_first)
        _expect_sha(second_raw, expected_second)
        origin_contract.cross_validate(first_raw, second_raw)
        first, second = origin_contract.decode(first_raw), origin_contract.decode(second_raw)
        if (first["init_operation_id"] != operation or first["domain_id"] != domain or first["facility_id"] != facility
                or first["profile"] != physical or first["node"] != evidence):
            _fail("ORIGIN_MISMATCH")
        _verify_physical(profile, paths, guard, directories, node, physical, evidence, budget)
        _fresh_origin(profile, domain, facility, operation, second, budget)
        reservation_raw = _reservation(first_raw, second_raw)
        if mode == "init":
            _need_names(parent, _ORIGIN_NAMES, budget)
            _, reservation_raw = _publish(profile, parent, contract.RESERVATION_NAME, operation,
                                          reservation_raw, stack, budget, contract.MAX_RESERVATION_BYTES)
        else:
            _, observed = _read_control(profile, parent, contract.RESERVATION_NAME,
                                        contract.MAX_RESERVATION_BYTES, stack, budget)
            _expect_sha(observed, expected_reservation)
            if observed != reservation_raw:
                _fail("ORIGIN_MISMATCH")
            contract.cross_validate_reservation(first_raw, second_raw, observed)
            reservation_raw = observed
        if mode != "verify":
            # This census is after independent R readback and before first
            # root create. Any already-started root/header/stage is rejected.
            _need_names(parent, _RESERVED_NAMES, budget)
            seal_raw, header_raw = _create(profile, parent, first_raw, second_raw, reservation_raw, budget)
            expected_seal = hashlib.sha256(seal_raw).hexdigest()
            expected_header = hashlib.sha256(header_raw).hexdigest()
        seal_raw, header_raw = _verify_complete(profile, first_raw, second_raw, reservation_raw,
                                               expected_seal, expected_header, budget)
        if mode != "verify":
            _fresh_origin(profile, domain, facility, operation, second, budget)
        if first_held.bytes() != first_raw or second_held.bytes() != second_raw:
            _fail("ORIGIN_MISMATCH")
        _verify_physical(profile, paths, guard, directories, node, physical, evidence, budget)
        _need_names(parent, _COMPLETE_NAMES, budget)
        _need_names(parent, _COMPLETE_NAMES, budget)
        _same_guard(profile, paths, guard, budget)
        result = Namespace3FilesystemFacts(contract.SCOPE, operation, domain, facility,
            hashlib.sha256(first_raw).hexdigest(), hashlib.sha256(second_raw).hexdigest(),
            hashlib.sha256(reservation_raw).hexdigest(), hashlib.sha256(seal_raw).hexdigest(),
            hashlib.sha256(header_raw).hexdigest(), contract.CREATION_KIND)
    # Every registered ordinary scope has exited; caller EX remains held.
    _same_guard(profile, paths, guard, budget)
    budget.clock_only()
    return result


def _entry(profile, domain, facility, operation, expected_first, expected_second,
           expected_reservation, expected_seal, expected_header, mode, callback):
    budget = _Budget(callback)
    try:
        return _perform(profile, domain, facility, operation, expected_first, expected_second,
                        expected_reservation, expected_seal, expected_header, mode, budget)
    except BaseException as error:
        if budget.callback_primary is not None and error is not budget.callback_primary:
            _note(budget.callback_primary)
            raise budget.callback_primary from None
        raise


def initialize_namespace3_locked(profile, *, domain_id, facility_id, init_operation_id,
        expected_origin_reservation_sha256, expected_origin_observation_sha256, check_cancelled=None):
    return _entry(profile, domain_id, facility_id, init_operation_id, expected_origin_reservation_sha256,
                  expected_origin_observation_sha256, None, None, None, "init", check_cancelled)


def resume_unstarted_namespace3_locked(profile, *, domain_id, facility_id, init_operation_id,
        expected_origin_reservation_sha256, expected_origin_observation_sha256,
        expected_namespace_reservation_sha256, check_cancelled=None):
    return _entry(profile, domain_id, facility_id, init_operation_id, expected_origin_reservation_sha256,
                  expected_origin_observation_sha256, expected_namespace_reservation_sha256,
                  None, None, "resume", check_cancelled)


def verify_namespace3_locked(profile, *, domain_id, facility_id, init_operation_id,
        expected_origin_reservation_sha256, expected_origin_observation_sha256,
        expected_namespace_reservation_sha256, expected_root_seal_sha256,
        expected_header_sha256, check_cancelled=None):
    return _entry(profile, domain_id, facility_id, init_operation_id, expected_origin_reservation_sha256,
                  expected_origin_observation_sha256, expected_namespace_reservation_sha256,
                  expected_root_seal_sha256, expected_header_sha256, "verify", check_cancelled)
