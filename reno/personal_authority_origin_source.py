"""Held committed origin facts for H01 enrollment; no registration or grant.

The two immutable byte strings are candidates, never a writer witness. A future
enrollment owner must reserve its init UUID permanently before publishing these
facts in its own continuously held window. Returning or decoding this DTO does
not preserve an original resource owner or establish enrollment.

Version 1 hashes four personal meta summaries and the ten typed personal table
summaries. Physical DB/WAL/node fingerprints are separate evidence. Receiver
tables are observer prerequisites outside the logical digest and returned
evidence. The reused SQL observer still checks whole-DB integrity/FKs and
receiver semantics/budgets. Existing source
ACLs are not inspected or changed; only new private TEMP copies use the existing
personal preservation privacy policy. No original file, identity, facility, SQL
row, grant, journal, or head is created or modified here.

A raised verification/exit error prevents delivery. Tracked directory exits now
check native CloseHandle BOOL, while creation failures and other resource
closers have separate remaining gates. Returning these bytes is no complete
resource-close witness, permanent enrollment, or writer permission.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import re
import time

from . import personal_authority_contract as authority
from . import personal_mirror_contract as facts
from . import personal_preservation_contract as preservation
from . import personal_preservation_source as source
from . import storage_lifecycle as lifecycle, storage_readonly as readonly
from . import sync_media as safe

MAX_SECONDS = 30
MAX_SOURCE_BYTES = readonly.MAX_BYTES_LIMIT
MAX_NODE_BYTES = 4096
META_SCOPE = "personal-authority-meta-v1"
SQL_SCOPE = "personal-authority-ten-and-meta-v1"
EVIDENCE_SCOPE = "personal-authority-origin-source-v1"
_IDENTITY_KEYS = ("device", "inode", "byte_size", "mtime_ns", "ctime_ns")
_EPOCH = re.compile(r"[1-9][0-9]{0,18}\Z")


class OriginSourceError(ValueError):
    """Finite source failure without source paths, cells, or identity values."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class OriginObservation:
    intent_bytes: bytes
    evidence_bytes: bytes


def _fail(code):
    raise OriginSourceError(code) from None


def _note(primary, text):
    try:
        BaseException.add_note(primary, text)
    except BaseException:
        pass


@contextmanager
def _preserve_primary(context):
    """Exit every nested resource without replacing a body exception."""
    resource = context.__enter__()
    try:
        yield resource
    except BaseException as primary:
        try:
            original_tb = BaseException.__dict__["__traceback__"].__get__(primary)
            context.__exit__(type(primary), primary, original_tb)
        except BaseException as cleanup:
            if cleanup is not primary:
                _note(primary, "ORIGIN_SOURCE_CLEANUP_FAILED")
        raise
    else:
        # A normal verification/close failure remains a failed observation.
        context.__exit__(None, None, None)


class _Budget:
    def __init__(self, callback):
        # Start before argument checking, source admission, and lock acquisition.
        self.deadline = time.monotonic() + MAX_SECONDS
        self.callback = callback
        self.callback_primary = None
        self.files = self.byte_size = 0
        if callback is not None and not callable(callback):
            _fail("ORIGIN_INVALID_ARGUMENT")

    def check(self):
        if self.callback_primary is not None:
            raise self.callback_primary
        if self.callback is not None:
            try:
                self.callback()
            except BaseException as primary:
                self.callback_primary = primary
                raise
        if time.monotonic() >= self.deadline:
            _fail("ORIGIN_TIME_BUDGET_EXCEEDED")

    def file(self, size):
        self.check()
        if type(size) is not int or size < 0:
            _fail("ORIGIN_SOURCE_CHANGED")
        self.files += 1
        self.byte_size += size
        if self.files > 3 or self.byte_size > MAX_SOURCE_BYTES:
            _fail("ORIGIN_SOURCE_TOO_LARGE")


class _NodeBudget:
    """Separate node cap; reuse the original deadline/cancellation owner."""
    def __init__(self, budget):
        self.budget, self.files = budget, 0

    def check(self):
        self.budget.check()

    def file(self, size):
        self.check()
        self.files += 1
        if type(size) is not int or not 0 <= size <= MAX_NODE_BYTES or self.files != 1:
            _fail("ORIGIN_NODE_INVALID")


@contextmanager
def _preserve_callback(budget):
    try:
        yield
    except BaseException as error:
        if budget.callback_primary is not None and error is not budget.callback_primary:
            _note(budget.callback_primary, "ORIGIN_SOURCE_CLEANUP_FAILED")
            raise budget.callback_primary from None
        raise


def _arguments(profile, domain_id, facility_id, init_operation_id, budget):
    budget.check()
    profile = lifecycle._profile(profile)
    authority._uuid(domain_id)
    authority._uuid(facility_id)
    authority._uuid(init_operation_id)
    profile_paths = tuple(profile.identity)
    budget.check()
    return profile, profile_paths


def _node_pairs(items):
    result = {}
    for key, value in items:
        if type(key) is not str or key in result:
            _fail("ORIGIN_NODE_INVALID")
        result[key] = value
    return result


def _node_constant(_value):
    _fail("ORIGIN_NODE_INVALID")


def _node_id(raw):
    if type(raw) is not bytes or len(raw) > MAX_NODE_BYTES:
        _fail("ORIGIN_NODE_INVALID")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_node_pairs,
                           parse_constant=_node_constant)
    except OriginSourceError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        _fail("ORIGIN_NODE_INVALID")
    if type(value) is not dict or set(value) != {"instance_id"}:
        _fail("ORIGIN_NODE_INVALID")
    try:
        return authority._uuid(value["instance_id"])
    except authority.ContractError:
        _fail("ORIGIN_NODE_INVALID")


def _origin_meta(meta, rows, node_id):
    if type(meta) is not dict or set(meta) != set(facts.META_KEYS):
        _fail("ORIGIN_META_INVALID")
    try:
        authority._uuid(meta["library_id"])
        authority._uuid(meta["personal_primary_id"])
        project_id = authority._uuid(meta["default_project_id"])
    except authority.ContractError:
        _fail("ORIGIN_META_INVALID")
    if node_id != meta["personal_primary_id"]:
        _fail("ORIGIN_PRIMARY_MISMATCH")
    if not any(row["project_id"] == project_id for row in rows["renovation_project"]):
        _fail("ORIGIN_DEFAULT_PROJECT_INVALID")
    epoch_text = meta["primary_epoch"]
    if type(epoch_text) is not str or _EPOCH.fullmatch(epoch_text) is None:
        _fail("ORIGIN_META_INVALID")
    epoch = int(epoch_text)
    if epoch > authority.MAX_INTEGER:
        _fail("ORIGIN_META_INVALID")
    return epoch


def _file_evidence(held):
    return {"sha256": held.sha256, "byte_size": held.byte_size,
            "directory_identity": deepcopy(held.directory_identity),
            "path_identity": deepcopy(held.path_identity),
            "handle_identity": deepcopy(held.handle_identity)}


def _verify_owner(profile, profile_paths, guard, budget):
    budget.check()
    if tuple(profile.identity) != profile_paths:
        _fail("ORIGIN_PROFILE_CHANGED")
    if lifecycle.require_guard(profile, exclusive=True) is not guard:
        _fail("ORIGIN_PROFILE_CHANGED")
    budget.check()


def _verify_sources(profile, profile_paths, guard, image, fingerprint, held_files,
                    node, directory, budget):
    _verify_owner(profile, profile_paths, guard, budget)
    directory.verify()
    for held in held_files.values():
        if held is not None:
            held.verify()
    node.verify()
    # This also proves that originally missing optional files remain missing.
    if image.verify_source() != fingerprint:
        _fail("ORIGIN_SOURCE_CHANGED")
    _verify_owner(profile, profile_paths, guard, budget)


@contextmanager
def _capture(profile, profile_paths, *, domain_id, facility_id, init_operation_id, budget):
    guard = lifecycle.require_guard(profile, exclusive=True)
    _verify_owner(profile, profile_paths, guard, budget)
    # ExitStack always attempts all registered exits, including after a failure.
    with _preserve_primary(ExitStack()) as stack:
        directory = stack.enter_context(safe._Directory(profile.database.parent,
                                                       base=profile.database.parent))
        image = stack.enter_context(readonly.committed_image_locked(
            profile, max_bytes=MAX_SOURCE_BYTES, attempts=1, check_cancelled=budget.check,
            private_directory_factory=source._PersonalPrivateDirectory))
        fingerprint = image.source_fingerprint
        if fingerprint["profile_identity"] != list(profile_paths):
            _fail("ORIGIN_PROFILE_CHANGED")
        held_files = {}
        for suffix in ("", "-wal", "-journal"):
            expected = fingerprint["files"][suffix]
            if expected is None:
                held_files[suffix] = None
                continue
            held = source._HeldFile(directory, profile.database.name + suffix,
                                    stack, budget, live_sql=True)
            if ({key: held.path_identity[key] for key in _IDENTITY_KEYS} !=
                    {key: expected[key] for key in _IDENTITY_KEYS} or
                    (held.sha256, held.byte_size) != (expected["sha256"], expected["byte_size"])):
                _fail("ORIGIN_SOURCE_CHANGED")
            held_files[suffix] = held
        node = source._HeldFile(directory, "node-identity.json", stack, _NodeBudget(budget))
        node_id = _node_id(node.bytes(MAX_NODE_BYTES))
        sql, meta, rows = source._project_sql(image.connection, fingerprint, budget.check)
        epoch = _origin_meta(meta, rows, node_id)
        meta_document = {"format_version": 1, "scope": META_SCOPE, "schema_version": 17,
                         "meta": deepcopy(sql["meta"])}
        sql_document = {"format_version": 1, "scope": SQL_SCOPE, "schema_version": 17,
                        "meta": deepcopy(sql["meta"]),
                        "personal_tables": deepcopy(sql["personal_tables"])}
        profile_sha256 = preservation.digest(list(profile_paths))
        intent = authority.encode({"format_version": 1, "kind": "origin_init_intent",
            "domain_id": domain_id, "operation_id": init_operation_id,
            "issuer": {"facility_id": facility_id, "node_id": node_id,
                       "profile_sha256": profile_sha256},
            "payload": {"current_epoch": epoch, "observed_primary_node_id": node_id,
                        "observed_personal_meta_sha256": preservation.digest(meta_document),
                        "observed_personal_sql_sha256": preservation.digest(sql_document)}})
        evidence = preservation.canonical_bytes({
            "format_version": 1, "scope": EVIDENCE_SCOPE,
            "profile_paths": list(profile_paths), "profile_sha256": profile_sha256,
            "source_fingerprint": deepcopy(fingerprint), "node": _file_evidence(node),
            "raw_sql_files": {suffix: None if held is None else _file_evidence(held)
                              for suffix, held in held_files.items()},
            "meta_document": meta_document, "sql_document": sql_document,
            "intent_sha256": hashlib.sha256(intent).hexdigest()})
        observation = OriginObservation(intent, evidence)
        _verify_sources(profile, profile_paths, guard, image, fingerprint,
                        held_files, node, directory, budget)
        yield observation
        _verify_sources(profile, profile_paths, guard, image, fingerprint,
                        held_files, node, directory, budget)
    # Includes source/image/private-copy exit handler returns in the deadline.
    _verify_owner(profile, profile_paths, guard, budget)


@contextmanager
def capture_locked(profile, *, domain_id, facility_id, init_operation_id, check_cancelled=None):
    """Yield candidate bytes under an existing EX; success requires normal exit.

    The caller retains its EX through future publication and its own close.
    Body failures retain their original identity; that failed path establishes
    no successful terminal source verification or completed enrollment.
    """
    budget = _Budget(check_cancelled)
    with _preserve_callback(budget):
        profile, paths = _arguments(profile, domain_id, facility_id, init_operation_id, budget)
        with _capture(profile, paths, domain_id=domain_id, facility_id=facility_id,
                      init_operation_id=init_operation_id, budget=budget) as result:
            yield result
        budget.check()


def observe(profile, *, domain_id, facility_id, init_operation_id, check_cancelled=None):
    """Own an immediate existing EX; return after non-raising exit handlers.

    Inherited handlers do not establish a complete native resource-close witness.
    """
    budget = _Budget(check_cancelled)
    with _preserve_callback(budget):
        profile, paths = _arguments(profile, domain_id, facility_id, init_operation_id, budget)
        with _preserve_primary(lifecycle.exclusive(profile, timeout=0,
                                                 check_cancelled=budget.check)):
            with _capture(profile, paths, domain_id=domain_id, facility_id=facility_id,
                          init_operation_id=init_operation_id, budget=budget) as result:
                observation = result
        budget.check()
        if tuple(profile.identity) != paths:
            _fail("ORIGIN_PROFILE_CHANGED")
        return observation
