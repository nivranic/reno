"""Complete origin candidate capture under one cooperative absolute deadline.

The original immutable two-byte DTO is a candidate, not enrollment, a grant,
or a complete native source-close witness. Borrowed capture retains the caller's
actual EX; owned observation returns only after its own ordinary EX exit. The
new fixed source components use one effective token and retain their own sole
closers. No original DB, identity, ACL, journal, or writer permission is changed.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from copy import deepcopy
import hashlib
import json
import math
import os
import re
import time

from . import personal_authority_contract as authority
from . import personal_mirror_contract as facts
from . import personal_preservation_contract as preservation
from . import personal_authority_origin_source as ordinary
from . import personal_authority_namespace3_deadline as deadline
from . import personal_authority_directory_deadline as directories
from . import personal_authority_source_deadline as nodes
from . import personal_authority_committed_image_deadline as images
from . import personal_authority_live_sql_held_deadline as live_sql
from . import personal_authority_sql_projection_deadline as projection
from . import storage_lifecycle as lifecycle, storage_readonly as readonly
from . import personal_authority_owned_ex_deadline as owned_ex
from . import personal_authority_owned_ex_group as owned_group

OriginObservation = ordinary.OriginObservation
OriginSourceError = ordinary.OriginSourceError
Error = OriginSourceError
MAX_SECONDS = 30
MAX_SOURCE_BYTES = readonly.MAX_BYTES_LIMIT
MAX_NODE_BYTES = 4096
META_SCOPE = "personal-authority-meta-v1"
SQL_SCOPE = "personal-authority-ten-and-meta-v1"
EVIDENCE_SCOPE = "personal-authority-origin-source-v1"
_IDENTITY_KEYS = ("device", "inode", "byte_size", "mtime_ns", "ctime_ns")
_EPOCH = re.compile(r"[1-9][0-9]{0,18}\Z")
_SQL_KEYS = {"schema_version", "source_fingerprint", "meta", "personal_tables",
             "receiver_facility", "receiver_tables", "integrity", "foreign_keys"}
_SUFFIXES = ("", "-wal", "-journal")


def _fail(code):
    raise OriginSourceError(code) from None


def _note(primary, text):
    try:
        BaseException.add_note(primary, text)
    except BaseException:
        pass


def _monotonic():
    return time.monotonic()


def _deadline(absolute):
    return deadline.Namespace3Deadline(absolute)


def _restricted_budget(requested):
    return deadline._RestrictedBudget(requested)


def _effective_token(budget):
    return budget.effective_deadline


def _profile(profile):
    return lifecycle._profile(profile)


def _uuid(value):
    return authority._uuid(value)


def _current_guard():
    return lifecycle.current_guard()


def _require_guard(profile, *, exclusive=True):
    return lifecycle.require_guard(profile, exclusive=exclusive)


def _exclusive(profile, budget, *, state):
    initial_pending = True

    def initial_poll():
        nonlocal initial_pending
        _raise_first(state)
        budget.check()
        if initial_pending:
            if _current_guard() is not None:
                _fail("ORIGIN_GUARD_ALREADY_HELD")
            initial_pending = False

    return lifecycle.exclusive(profile, timeout=0, check_cancelled=initial_poll)


def _directory(path, *, deadline_token, base):
    return directories.DeadlineDirectory(path, deadline_token=deadline_token,
                                         create=False, base=base)


def _directory_enter(value):
    return directories.DeadlineDirectory.__enter__(value)


def _directory_exit(value, kind, primary, tb):
    return directories.DeadlineDirectory.__exit__(value, kind, primary, tb)


def _directory_verify(value):
    return directories.DeadlineDirectory.verify(value)


def _entry_stat(value, name):
    return directories.DeadlineDirectory.entry_stat(value, name)


def _image(profile, directory, *, deadline_token):
    return images.committed_image_locked(profile, directory,
        deadline_token=deadline_token, max_bytes=MAX_SOURCE_BYTES, attempts=1)


def _image_enter(value):
    return images.CommittedImageDeadlineLease.__enter__(value)


def _image_exit(value, kind, primary, tb):
    return images.CommittedImageDeadlineLease.__exit__(value, kind, primary, tb)


def _fingerprint(image):
    return image.source_fingerprint


def _image_verify(image):
    return images.DeadlineCommittedImage.verify_source(image)


def _read_budget(profile, fp, *, deadline_token):
    return live_sql.LiveSqlHeldBudget(profile, fp, deadline_token=deadline_token)


def _read_budget_evidence(value):
    return value.evidence


def _held(directory, suffix, *, read_budget, deadline_token):
    return live_sql.held_file_locked(directory, suffix, read_budget=read_budget,
                                    deadline_token=deadline_token)


def _held_enter(value):
    return live_sql.DeadlineLiveSqlHeldFile.__enter__(value)


def _held_exit(value, kind, primary, tb):
    return live_sql.DeadlineLiveSqlHeldFile.__exit__(value, kind, primary, tb)


def _held_verify(value):
    return live_sql.DeadlineLiveSqlHeldFile.verify(value)


def _held_evidence(value):
    return value.evidence


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


def _project_sql(profile, image, *, deadline_token):
    return projection.project_sql_locked(profile, image, deadline_token=deadline_token)


def _canonical_bytes(value):
    return preservation.canonical_bytes(value)


def _digest(value):
    return preservation.digest(value)


def _encode_intent(value):
    return authority.encode(value)


def _sha256_hex(raw):
    return hashlib.sha256(raw).hexdigest()


def _deepcopy(value):
    return deepcopy(value)


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
        _note(state.first, "ORIGIN_SOURCE_CLEANUP_FAILED")


def _raise_first(state):
    if state.first is not None:
        raise BaseException.with_traceback(state.first, state.first_tb) from None


def _clock(state):
    _raise_first(state)
    try:
        now = _monotonic()
        if type(now) is not float or not math.isfinite(now) or now < 0.0:
            _fail("ORIGIN_INVALID_RESULT")
        if now >= state.token.absolute_monotonic:
            _fail("ORIGIN_TIME_BUDGET_EXCEEDED")
    except BaseException as failure:
        _remember(state, failure)
        _raise_first(state)


def _cleanup_clock(state):
    # An earlier failure or a new expiry never prevents a known cleanup.
    try:
        _clock(state)
    except BaseException as failure:
        _remember(state, failure)


def _call(state, operation, /, *args, **kwargs):
    _clock(state)
    try:
        result = operation(*args, **kwargs)
    except BaseException as failure:
        _remember(state, failure)
        _raise_first(state)
    _clock(state)
    return result


def _cpu(state, operation, /, *args, **kwargs):
    # Opaque pure helpers finish their original error conversion before this
    # boundary captures the primary; no later clock replaces an opaque error.
    return _call(state, operation, *args, **kwargs)


class _OriginBudget:
    """Compose the exact node budget without changing its callback or slots."""
    __slots__ = ("restricted", "effective_deadline", "state", "callback",
                 "callback_primary", "callback_tb")

    def __init__(self, restricted, effective, callback):
        self.restricted, self.effective_deadline = restricted, effective
        self.state = _FirstState(effective)
        self.callback, self.callback_primary, self.callback_tb = callback, None, None

    def check(self):
        _raise_first(self.state)
        if self.callback_primary is not None:
            raise BaseException.with_traceback(self.callback_primary, self.callback_tb)
        if self.callback is not None:
            try:
                self.callback()
            except BaseException as primary:
                self.callback_primary = primary
                self.callback_tb = BaseException.__dict__["__traceback__"].__get__(primary)
                _remember(self.state, primary, self.callback_tb)
                raise
        _clock(self.state)


def _budget(requested, callback):
    # Token domain precedes all new local clock/factory effects.
    try:
        requested = deadline._token(requested)
    except deadline.Namespace3Error:
        _fail("ORIGIN_INVALID_ARGUMENT")
    if callback is not None and not callable(callback):
        _fail("ORIGIN_INVALID_ARGUMENT")
    try:
        restricted = _restricted_budget(requested)
    except deadline.Namespace3Error as error:
        if error.code == "NAMESPACE3_DEADLINE":
            _fail("ORIGIN_TIME_BUDGET_EXCEEDED")
        raise
    if type(restricted) is not deadline._RestrictedBudget:
        _fail("ORIGIN_INVALID_RESULT")
    effective = _effective_token(restricted)
    if type(effective) is not deadline.Namespace3Deadline:
        _fail("ORIGIN_INVALID_RESULT")
    try:
        deadline._token(effective)
    except deadline.Namespace3Error:
        _fail("ORIGIN_INVALID_RESULT")
    if effective.absolute_monotonic > requested.absolute_monotonic:
        _fail("ORIGIN_INVALID_RESULT")
    result = _OriginBudget(restricted, effective, callback)
    _clock(result.state)
    return result


class _Scope:
    """Adopt a legal enter before postclock; attempt only its fixed exit once."""
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
                _fail("ORIGIN_INVALID_RESULT")
        except BaseException as failure:
            # A lost/invalid enter result is UNKNOWN. The lower owns partial
            # cleanup; no second exit, numeric probe, or guessed close occurs.
            _remember(self.state, failure)
            _raise_first(self.state)
        self.entered = True
        try:
            _clock(self.state)
        except BaseException as failure:
            tb = BaseException.__dict__["__traceback__"].__get__(failure)
            self.__exit__(type(failure), failure, tb)
            raise
        return value

    def __exit__(self, kind, primary, tb):
        if primary is not None:
            _remember(self.state, primary, tb)
        if not self.entered or self.attempted:
            _fail("ORIGIN_INVALID_RESULT")
        self.attempted = True
        _cleanup_clock(self.state)
        first = self.state.first
        first_tb = self.state.first_tb
        try:
            result = self.exit(self.owner, None if first is None else type(first),
                               first, first_tb)
            if self.exit_false:
                if result is not False:
                    _fail("ORIGIN_INVALID_RESULT")
            elif result is not None:
                _fail("ORIGIN_INVALID_RESULT")
        except BaseException as failure:
            _remember(self.state, failure)
        _cleanup_clock(self.state)
        if self.state.first is not None:
            _raise_first(self.state)
        self.normal = True
        return None


def _protocol_enter(context):
    return context.__enter__()


def _protocol_exit(context, kind, primary, tb):
    return context.__exit__(kind, primary, tb)


def _prepared(state, operation, expected, /, *args, **kwargs):
    value = _call(state, operation, *args, **kwargs)
    if type(value) is not expected:
        _fail("ORIGIN_INVALID_RESULT")
    return value


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
    state = budget.state
    if type(profile) is not lifecycle.StorageProfile:
        _fail("ORIGIN_INVALID_ARGUMENT")
    profile = _cpu(state, _profile, profile)
    _cpu(state, _uuid, domain_id)
    _cpu(state, _uuid, facility_id)
    _cpu(state, _uuid, init_operation_id)
    profile_paths = _cpu(state, _profile_paths, profile)
    budget.check()
    return profile, profile_paths


def _profile_paths(profile):
    return tuple(profile.identity)


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
    state = budget.state
    if _cpu(state, _profile_paths, profile) != profile_paths:
        _fail("ORIGIN_PROFILE_CHANGED")
    if _call(state, _require_guard, profile, exclusive=True) is not guard:
        _fail("ORIGIN_PROFILE_CHANGED")
    budget.check()


def _verify_sources(profile, profile_paths, guard, image, fingerprint, held_files,
                    node, directory, budget):
    _verify_owner(profile, profile_paths, guard, budget)
    state = budget.state
    _call(state, _directory_verify, directory)
    for held in held_files.values():
        if held is not None:
            budget.check()
            _call(state, _held_verify, held)
            budget.check()
    budget.check()
    _call(state, _node_verify, node)
    budget.check()
    # Preserve the original ordinary equality and its two live positions.
    if _cpu(state, _same, _call(state, _image_verify, image), fingerprint) is not True:
        _fail("ORIGIN_SOURCE_CHANGED")
    _verify_owner(profile, profile_paths, guard, budget)


def _same(left, right):
    return left == right


def _match_held(held, expected):
    if ({key: held.path_identity[key] for key in _IDENTITY_KEYS} !=
            {key: expected[key] for key in _IDENTITY_KEYS} or
            (held.sha256, held.byte_size) != (expected["sha256"], expected["byte_size"])):
        _fail("ORIGIN_SOURCE_CHANGED")


def _projection_result(value):
    if (type(value) is not tuple or len(value) != 3
            or any(type(item) is not dict for item in value)):
        _fail("ORIGIN_INVALID_RESULT")
    sql, meta, rows = value
    if (set(sql) != _SQL_KEYS or type(sql["schema_version"]) is not int
            or sql["schema_version"] != 17 or type(sql["meta"]) is not dict
            or type(sql["personal_tables"]) is not dict
            or type(sql["receiver_tables"]) is not dict
            or type(sql["source_fingerprint"]) is not dict
            or type(sql["receiver_facility"]) is not str
            or sql["receiver_facility"] not in {"installed", "absent"}
            or sql["integrity"] != "ok" or sql["foreign_keys"] != "ok"):
        _fail("ORIGIN_INVALID_RESULT")
    return sql, meta, rows


def _verify_read_retired(budget_evidence, held_evidences, fp):
    if (type(budget_evidence) is not dict or budget_evidence.get("failed") is not False
            or budget_evidence.get("retired") is not True):
        _fail("ORIGIN_INVALID_RESULT")
    for suffix in _SUFFIXES:
        expected = 0 if fp["files"][suffix] is None else 5
        if (type(budget_evidence["passes_started"][suffix]) is not int
                or type(budget_evidence["passes_completed"][suffix]) is not int
                or budget_evidence["passes_started"][suffix] != expected
                or budget_evidence["passes_completed"][suffix] != expected):
            _fail("ORIGIN_INVALID_RESULT")
        if expected == 0:
            continue
        held = held_evidences[suffix]
        if (type(held) is not dict or held.get("phase") != "retired"
                or held.get("lease_state") != "closed"
                or type(held.get("passes_started")) is not int
                or type(held.get("passes_completed")) is not int
                or held["passes_started"] != 5 or held["passes_completed"] != 5
                or held.get("terminal_verified") is not True
                or held.get("normal_exit") is not True
                or held.get("source_directory_borrowed") is not True):
            _fail("ORIGIN_INVALID_RESULT")


def _missing_probe(directory, name, state):
    _clock(state)
    try:
        found = _entry_stat(directory, name)
    except FileNotFoundError:
        # Only this fixed metadata call has accepted absence semantics.
        _raise_first(state)
        _clock(state)
        return None
    except BaseException as failure:
        _remember(state, failure)
        _raise_first(state)
    _clock(state)
    if type(found) is not os.stat_result:
        _fail("ORIGIN_INVALID_RESULT")
    _fail("ORIGIN_SOURCE_CHANGED")


def _verify_missing_optional(profile, paths, guard, source_pin, fp, budget):
    state = budget.state
    budget.check()
    _verify_owner(profile, paths, guard, budget)
    _call(state, _directory_verify, source_pin)
    for suffix in ("-wal", "-journal"):
        if fp["files"][suffix] is not None:
            continue
        _missing_probe(source_pin, profile.database.name + suffix, state)
    _call(state, _directory_verify, source_pin)
    _verify_owner(profile, paths, guard, budget)
    budget.check()


@contextmanager
def _capture(profile, profile_paths, *, domain_id, facility_id, init_operation_id, budget, _capture_owner=None):
    state, token = budget.state, budget.effective_deadline
    guard = _call(state, _require_guard, profile, exclusive=True)
    if type(guard) is not lifecycle.StorageGuard:
        _fail("ORIGIN_INVALID_RESULT")
    if _capture_owner is not None:
        owned_group._begin_capture(_capture_owner, budget, guard)
    _verify_owner(profile, profile_paths, guard, budget)
    source_pin = _prepared(state, _directory, directories.DeadlineDirectory,
        profile.database.parent, deadline_token=token, base=profile.database.parent)
    if _capture_owner is not None:
        owned_group._register_capture_source(_capture_owner, "source_directory", source_pin)
    with _Scope(state, source_pin, _directory_enter, _directory_exit,
                directories.DeadlineDirectory) as directory:
        if (directory.deadline_token is not token or directory.path != profile.database.parent
                or directory.base != profile.database.parent):
            _fail("ORIGIN_INVALID_RESULT")
        # Holders outlive the image. Their original fifth pass runs only after
        # image Con/privatepin/TEMP exits; no inactive image is used thereafter.
        with ExitStack() as holders:
            lease = _prepared(state, _image, images.CommittedImageDeadlineLease,
                              profile, directory, deadline_token=token)
            if _capture_owner is not None:
                owned_group._register_capture_source(_capture_owner, "image", lease)
            with _Scope(state, lease, _image_enter, _image_exit,
                        images.DeadlineCommittedImage, same=False) as image:
                if image.deadline_token is not token:
                    _fail("ORIGIN_INVALID_RESULT")
                fingerprint = _call(state, _fingerprint, image)
                if type(fingerprint) is not dict:
                    _fail("ORIGIN_INVALID_RESULT")
                if fingerprint["profile_identity"] != _cpu(state, list, profile_paths):
                    _fail("ORIGIN_PROFILE_CHANGED")
                if _capture_owner is not None:
                    owned_group._register_capture_image(_capture_owner, image, fingerprint)
                read_budget = _prepared(state, _read_budget, live_sql.LiveSqlHeldBudget,
                                       profile, fingerprint, deadline_token=token)
                if _capture_owner is not None:
                    owned_group._register_capture_support(_capture_owner, "read_budget", read_budget)
                if read_budget.deadline_token is not token:
                    _fail("ORIGIN_INVALID_RESULT")
                held_files = {}
                for suffix in ("", "-wal", "-journal"):
                    expected = fingerprint["files"][suffix]
                    if expected is None:
                        held_files[suffix] = None
                        continue
                    held_owner = _prepared(state, _held, live_sql.DeadlineLiveSqlHeldFile,
                        directory, suffix, read_budget=read_budget, deadline_token=token)
                    if _capture_owner is not None:
                        owned_group._register_capture_source(_capture_owner,
                            {"": "held_main", "-wal": "held_wal", "-journal": "held_journal"}[suffix], held_owner)
                    budget.check()
                    held = holders.enter_context(_Scope(state, held_owner,
                        _held_enter, _held_exit, live_sql.DeadlineLiveSqlHeldFile))
                    _cpu(state, _match_held, held, expected)
                    held_files[suffix] = held
                parent = _prepared(state, _node_directory, nodes.DeadlineNodeDirectory,
                                   directory, budget.restricted)
                if _capture_owner is not None:
                    owned_group._register_capture_support(_capture_owner, "node_parent", parent)
                node_owner = _prepared(state, _node, nodes.DeadlineHeldFile, parent)
                if _capture_owner is not None:
                    owned_group._register_capture_source(_capture_owner, "node", node_owner)
                budget.check()
                node = holders.enter_context(_Scope(state, node_owner,
                    _node_enter, _node_exit, nodes.DeadlineHeldFile))
                budget.check()
                node_raw = _call(state, _node_bytes, node, MAX_NODE_BYTES)
                budget.check()
                node_id = _cpu(state, _node_id, node_raw)
                budget.check()
                sql, meta, rows = _cpu(state, _projection_result,
                    _call(state, _project_sql, profile, image, deadline_token=token))
                budget.check()
                epoch = _cpu(state, _origin_meta, meta, rows, node_id)
                meta_document = {"format_version": 1, "scope": META_SCOPE, "schema_version": 17,
                                 "meta": _cpu(state, _deepcopy, sql["meta"])}
                sql_document = {"format_version": 1, "scope": SQL_SCOPE, "schema_version": 17,
                                "meta": _cpu(state, _deepcopy, sql["meta"]),
                                "personal_tables": _cpu(state, _deepcopy, sql["personal_tables"])}
                profile_sha256 = _cpu(state, _digest, _cpu(state, list, profile_paths))
                intent = _cpu(state, _encode_intent, {"format_version": 1, "kind": "origin_init_intent",
                    "domain_id": domain_id, "operation_id": init_operation_id,
                    "issuer": {"facility_id": facility_id, "node_id": node_id,
                               "profile_sha256": profile_sha256},
                    "payload": {"current_epoch": epoch, "observed_primary_node_id": node_id,
                                "observed_personal_meta_sha256": _cpu(state, _digest, meta_document),
                                "observed_personal_sql_sha256": _cpu(state, _digest, sql_document)}})
                if type(intent) is not bytes:
                    _fail("ORIGIN_INVALID_RESULT")
                evidence = _cpu(state, _canonical_bytes, {
                    "format_version": 1, "scope": EVIDENCE_SCOPE,
                    "profile_paths": _cpu(state, list, profile_paths), "profile_sha256": profile_sha256,
                    "source_fingerprint": _cpu(state, _deepcopy, fingerprint),
                    "node": _cpu(state, _file_evidence, node),
                    "raw_sql_files": {suffix: None if held is None else _cpu(state, _file_evidence, held)
                                      for suffix, held in held_files.items()},
                    "meta_document": meta_document, "sql_document": sql_document,
                    "intent_sha256": _cpu(state, _sha256_hex, intent)})
                if type(evidence) is not bytes:
                    _fail("ORIGIN_INVALID_RESULT")
                observation = _cpu(state, OriginObservation, intent, evidence)
                budget.check()
                _verify_sources(profile, profile_paths, guard, image, fingerprint,
                                held_files, node, directory, budget)
                try:
                    yield observation
                except BaseException as primary:
                    _remember(state, primary)
                    raise
                budget.check()
                _verify_sources(profile, profile_paths, guard, image, fingerprint,
                                held_files, node, directory, budget)
        retired = _call(state, _read_budget_evidence, read_budget)
        held_evidences = {suffix: _call(state, _held_evidence, held)
                          for suffix, held in held_files.items() if held is not None}
        _cpu(state, _verify_read_retired, retired, held_evidences, fingerprint)
        _verify_missing_optional(profile, profile_paths, guard, directory, fingerprint, budget)
    _verify_owner(profile, profile_paths, guard, budget)


@contextmanager
def capture_locked(profile, *, domain_id, facility_id, init_operation_id,
                   deadline_token, check_cancelled=None, _owned_ex=None):
    """Yield original candidate bytes; success requires normal capture exit.

    The caller retains its actual EX through the final same-owner gate. Yield
    is not enrollment or a resource-close witness and does not close that EX.
    """
    if _owned_ex is None:
        budget = _budget(deadline_token, check_cancelled)
        with _preserve_callback(budget):
            profile, paths = _arguments(profile, domain_id, facility_id, init_operation_id, budget)
            with _capture(profile, paths, domain_id=domain_id, facility_id=facility_id,
                          init_operation_id=init_operation_id, budget=budget) as result:
                yield result
            budget.check()
        return
    budget = _budget(deadline_token, check_cancelled)
    with _preserve_callback(budget):
        profile, paths = _arguments(profile, domain_id, facility_id, init_operation_id, budget)
        operation = (domain_id, facility_id, init_operation_id)
        child = owned_group._new_owned_capture(_owned_ex, operation=operation,
            parent_token=deadline_token, effective_token=budget.effective_deadline)
        try:
            with _capture(profile, paths, domain_id=domain_id, facility_id=facility_id,
                    init_operation_id=init_operation_id, budget=budget, _capture_owner=child) as result:
                yield result
            budget.check()
        except BaseException as primary:
            _remember(budget.state, primary)
            owned_group._finish_capture(child, budget.state, primary=primary,
                traceback=BaseException.__dict__["__traceback__"].__get__(primary))
            raise
        else:
            owned_group._finish_capture(child, budget.state, completed=True)

def observe(profile, *, domain_id, facility_id, init_operation_id, check_cancelled=None):
    """Own a fresh immediate actual EX; return only after its normal exit."""
    started = _monotonic()
    if type(started) is not float or not math.isfinite(started) or started < 0.0:
        _fail("ORIGIN_INVALID_RESULT")
    requested = _deadline(started + MAX_SECONDS)
    budget = _budget(requested, check_cancelled)
    state = budget.state
    with _preserve_callback(budget):
        profile, paths = _arguments(profile, domain_id, facility_id, init_operation_id, budget)
        if _call(state, _current_guard) is not None:
            _fail("ORIGIN_GUARD_ALREADY_HELD")
        budget.check()
        if _call(state, _current_guard) is not None:
            _fail("ORIGIN_GUARD_ALREADY_HELD")
        if _cpu(state, _profile_paths, profile) != paths:
            _fail("ORIGIN_PROFILE_CHANGED")
        context = _call(state, _exclusive, profile, budget, state=state)
        with _Scope(state, context, _protocol_enter, _protocol_exit,
                    lifecycle.StorageGuard, same=False, exit_false=True) as acquired:
            if _call(state, _require_guard, profile, exclusive=True) is not acquired:
                _fail("ORIGIN_PROFILE_CHANGED")
            _verify_owner(profile, paths, acquired, budget)
            with _capture(profile, paths, domain_id=domain_id, facility_id=facility_id,
                          init_operation_id=init_operation_id, budget=budget) as result:
                observation = result
        budget.check()
        if _call(state, _current_guard) is not None:
            _fail("ORIGIN_GUARD_ALREADY_HELD")
        if _cpu(state, _profile_paths, profile) != paths:
            _fail("ORIGIN_PROFILE_CHANGED")
        _clock(state)
        return observation


def observe_owned_ex(profile, *, domain_id, facility_id, init_operation_id, check_cancelled=None):
    """Own and consume registered actual EX facts; return original candidate DTO."""
    started = _monotonic()
    if type(started) is not float or not math.isfinite(started) or started < 0.0:
        _fail("ORIGIN_INVALID_RESULT")
    requested = _deadline(started + MAX_SECONDS)
    budget = _budget(requested, check_cancelled)
    state = budget.state
    operation = (domain_id, facility_id, init_operation_id)
    with _preserve_callback(budget):
        profile, paths = _arguments(profile, domain_id, facility_id, init_operation_id, budget)
        if _call(state, _current_guard) is not None:
            _fail("ORIGIN_GUARD_ALREADY_HELD")
        budget.check()
        if _call(state, _current_guard) is not None:
            _fail("ORIGIN_GUARD_ALREADY_HELD")
        if _cpu(state, _profile_paths, profile) != paths:
            _fail("ORIGIN_PROFILE_CHANGED")
        binding = owned_ex._new_owned_ex_binding(operation, parent_token=requested,
            effective_token=budget.effective_deadline, scope_identity=object())
        context = owned_group.OwnedExclusiveDeadlineLease(profile, binding=binding,
            declared_capture_count=1, check_cancelled=budget.check)
        with _Scope(state, context, _protocol_enter, _protocol_exit,
                    lifecycle.StorageGuard, same=False, exit_false=True) as acquired:
            if _call(state, _require_guard, profile, exclusive=True) is not acquired:
                _fail("ORIGIN_PROFILE_CHANGED")
            _verify_owner(profile, paths, acquired, budget)
            # The original locked API creates the actual restricted invocation
            # requested by this EX token, with its own FirstState identity.
            with capture_locked(profile, domain_id=domain_id, facility_id=facility_id,
                    init_operation_id=init_operation_id, deadline_token=context.deadline_token,
                    check_cancelled=budget.check, _owned_ex=context) as result:
                observation = result
        budget.check()
        if _call(state, _current_guard) is not None:
            _fail("ORIGIN_GUARD_ALREADY_HELD")
        if _cpu(state, _profile_paths, profile) != paths:
            _fail("ORIGIN_PROFILE_CHANGED")
        _clock(state)
        owned_group._consume_owned_ex_group(context)
        return observation
