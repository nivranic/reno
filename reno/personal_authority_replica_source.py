"""Original readonly RP1 source census lifecycle; no producer closure token.

The manager, token and live record belong to the SAME original operation.
Native results must remain associated with their original held-reader registry.
Detached declarations are returned only by the fixed operation driver; neither
ordinary RawFacts, a matching DTO nor a caller context grants entry here.
"""
from __future__ import annotations

import hashlib
import encodings.utf_8 as utf8
import asyncio
import os
import sys
import threading
from types import FunctionType, GeneratorType
import uuid

from . import personal_authority_replica_contract as replica
from . import personal_authority_raw_preservation2_contract as raw2
from . import personal_authority_replica_selector as selector
from . import personal_authority_replica_rawfact as rawfact
from . import personal_authority_replica_source_declaration as builder
from . import personal_authority_replica_operation as operation
from . import personal_authority_replica_source_native as native


_GET = object.__getattribute__
_SET = object.__setattr__
_TAG = object()
_LIVE, _MANAGERS = {}, {}
_ORIGINAL_CONTAINERS = (_LIVE, _MANAGERS)
_U64 = 2**63 - 1
_SOURCE_COUNTERS = ("source_read_bytes", "source_read_calls", "uncertain_source_read_calls")
_WORK_FIELDS = ("raw_preflight_input_bytes", "raw_preflight_codepoints", "raw_preflight_tokens",
    "raw_parse_calls", "raw_parse_input_bytes", "native_hash_input_bytes", "pure_hash_input_bytes",
    "own_canonical_output_bytes", "own_structural_visits", "original_codec_calls", "original_codec_input_bytes")


def _make_unknown_original():
    blocked = False
    def mark():
        nonlocal blocked
        blocked = True
    def peek():
        return blocked
    return mark, peek


_MARK_UNKNOWN, _failure_blocked_original = _make_unknown_original()


class AuthorityReplicaSourceError(ValueError):
    def __init__(self, suffix):
        self.code = "AUTHORITY_REPLICA_SOURCE1_" + suffix
        super().__init__(self.code)


def _fail(suffix):
    raise AuthorityReplicaSourceError(suffix) from None


class _SourceToken:
    __slots__ = ("__weakref__",)

    def __init__(self, tag):
        if tag is not _TAG:
            _fail("GUARD_REQUIRED")


class _ParserWork:
    __slots__ = _WORK_FIELDS

    def __init__(self):
        for name in _WORK_FIELDS:
            _SET(self, name, 0)


class _SourceRecord:
    __slots__ = ("token", "context", "operation_token", "selector", "scope_id", "begin", "phase", "first",
        "unknown", "location", "metadata", "read", "cache", "declaration", "encoded", "selection",
        "parser_work", "reader_bindings", "builder_result", "cleanup", "audit", "cleanup_attempted", "cleanup_normal",
        "progress", "progress_totals", "work_complete")

    def __init__(self, tag, token, context, projection, scope_id, begin):
        if tag is not _TAG:
            _fail("GUARD_REQUIRED")
        self.token, self.context, self.operation_token = token, context, context.token
        self.selector, self.scope_id, self.begin = projection, scope_id, begin
        self.phase, self.first, self.unknown = "begin", None, False
        self.location = self.metadata = self.read = self.cache = self.declaration = self.encoded = self.selection = None
        self.builder_result = self.cleanup = self.audit = None
        self.parser_work, self.reader_bindings = _ParserWork(), ()
        self.cleanup_attempted = self.cleanup_normal = False
        self.progress, self.progress_totals, self.work_complete = None, {}, False


class _SourceManager:
    __slots__ = ("context", "token", "record", "entered", "retired", "enter", "cleanup", "retire", "remember_body")

    def __init__(self, tag, context):
        if tag is not _TAG:
            _fail("GUARD_REQUIRED")
        self.context, self.token, self.record = context, None, None
        self.entered = self.retired = False
        self.enter = self.cleanup = self.retire = self.remember_body = None

def _make_anchor_original(*, type=type, dict=dict, id=id, tuple=tuple, get=object.__getattribute__):
    records, managers = {}, {}
    manager_type = _SourceManager
    first_slot = _SourceRecord.__dict__["first"]
    unknown_slot = _SourceRecord.__dict__["unknown"]
    closed_error = AuthorityReplicaSourceError("CLOSED")
    guard_error = AuthorityReplicaSourceError("GUARD_REQUIRED")
    work_fields = _WORK_FIELDS
    work_slots = tuple((name, _ParserWork.__dict__[name]) for name in work_fields)

    def enroll_manager(manager, trusted):
        if id(manager) in managers:
            _fail("REENTRANT")
        callbacks = (manager.enter, manager.cleanup, manager.retire, manager.remember_body)
        pins = tuple((callback, callback.__code__, callback.__defaults__, callback.__kwdefaults__, callback.__closure__,
            tuple((cell, cell.cell_contents) for cell in callback.__closure__ or ())) for callback in callbacks + trusted)
        managers[id(manager)] = (manager, manager.context, *callbacks, pins, trusted)

    def manager_anchor(manager):
        held = dict.get(managers, id(manager))
        if held is None or held[0] is not manager:
            _fail("CLOSED")
        if (manager.context is not held[1] or manager.enter is not held[2]
                or manager.cleanup is not held[3] or manager.retire is not held[4] or manager.remember_body is not held[5]):
            _fail("RECORD_CHANGED")
        for callback, code, defaults, kwdefaults, closure, cells in held[6]:
            if (type(callback) is not FunctionType or callback.__code__ is not code or callback.__defaults__ is not defaults
                    or callback.__kwdefaults__ is not kwdefaults or callback.__closure__ is not closure):
                _fail("BINDING_CHANGED")
            for cell, original in cells:
                if cell.cell_contents is not original:
                    _fail("BINDING_CHANGED")
        return held

    def exit_roots(manager):
        # Independent original CM responsibility storage.  This lookup is
        # captured by the actual class lifetime methods before enter; it is
        # not published as a runtime getter or an ordinary integrity gate.
        if type(manager) is not manager_type:
            raise guard_error
        held = dict.get(managers, id(manager))
        if held is None or held[0] is not manager:
            raise closed_error
        trusted = held[7]
        return trusted[3], trusted[1], trusted[2]

    def enroll(record, manager):
        ctx = record.context
        fixed = (record, record.token, ctx, record.operation_token, record.selector,
            record.scope_id, record.begin, record.parser_work)
        manager_anchor(manager)
        # Authority rows are indexed by their independently enrolled original
        # record identity.  The original token is retained in fixed[1]; its
        # caller-visible mirror must never select a responsibility row.
        records[id(record)] = [fixed, "begin", None, False, {}, {}, dict((name, 0) for name in work_fields), manager]

    def peek(record):
        row = dict.get(records, id(record))
        if row is None or row[0][0] is not record:
            _fail("CLOSED")
        fixed = row[0]
        manager_anchor(row[7])
        for value, expected in zip((record, record.token, record.context, record.operation_token,
                record.selector, record.scope_id, record.begin, record.parser_work), fixed):
            if value is not expected:
                _fail("RECORD_CHANGED")
        if record.phase is not row[1] or record.first is not row[2] or record.unknown is not row[3]:
            _fail("RECORD_CHANGED")
        for name, value in row[4].items():
            if get(record, name) is not value:
                _fail("RECORD_CHANGED")
        for name, slot in work_slots:
            actual = slot.__get__(record.parser_work, _ParserWork)
            if type(actual) is not int or actual != row[6][name]:
                _fail("WORK_INVALID")
        return row

    def add_work(record, name, amount):
        row = peek(record)
        if type(name) is not str or name not in work_fields or type(amount) is not int or amount < 0:
            _fail("WORK_INVALID")
        now = row[6][name] + amount
        if now > _U64:
            _fail("WORK_INVALID")
        row[6][name] = now
        for field, slot in work_slots:
            if field == name:
                slot.__set__(record.parser_work, now)
                return

    def seal(record, name, value, fingerprint):
        row = peek(record)
        row[4][name], row[5][name] = value, fingerprint
        _SET(record, name, value)

    def outputs(record):
        row = peek(record)
        return tuple((name, row[4][name], expected) for name, expected in row[5].items())

    def transition(record, phase):
        row = peek(record)
        row[1] = phase
        record.phase = phase

    def failure(record, first, unknown=False):
        # Minimal failure/exit root: select only this independently enrolled
        # original record's row, never its caller-writable token mirror or a
        # potentially rejected ordinary record/class/integrity lookup.
        row = dict.get(records, id(record))
        if row is None or row[0][0] is not record:
            return first
        if row[2] is None:
            row[2] = first
        first_slot.__set__(record, row[2])
        if unknown:
            row[3] = True
            unknown_slot.__set__(record, True)
        return row[2]

    def drop(record, manager, token):
        if record is not None:
            row = dict.get(records, id(record))
            if row is not None and row[0][0] is record and row[0][1] is token:
                dict.pop(records, id(record))
        held = dict.get(managers, id(manager))
        if held is not None and held[0] is manager:
            dict.pop(managers, id(manager))

    return enroll_manager, manager_anchor, enroll, peek, transition, failure, drop, seal, outputs, add_work, exit_roots


(_ENROLL_MANAGER, _MANAGER_ANCHOR, _ENROLL, _PEEK, _TRANSITION, _FAILURE, _DROP, _SEAL, _OUTPUTS, _ADD_WORK, _EXIT_CAPTURE) = _make_anchor_original()


def _make_minimal_owner_original(owner):
    """Capture actual owner primitives; never enter the rejected normal graph."""
    getpid, getident = os.getpid, threading.get_ident
    running_loop, tasks = asyncio._get_running_loop, asyncio.tasks._current_tasks
    items, kind, dictionary, get = dict.items, type, dict, dict.get
    original_pid, original_thread, original_task = owner
    # This original error object is constructed before any callback.  Raising
    # it needs no mutable source Error constructor or string diagnostic hook.
    mismatch = AuthorityReplicaSourceError("OWNER_MISMATCH")

    def require_owner():
        if getpid() != original_pid or getident() != original_thread:
            raise mismatch
        loop, actual_task = running_loop(), None
        if kind(tasks) is not dictionary:
            raise mismatch
        if loop is not None:
            for original_loop, candidate in items(tasks):
                if original_loop is loop:
                    actual_task = candidate
                    break
        if actual_task is not original_task:
            raise mismatch
    return require_owner


def _make_manager_lifetime_original(capture_exit, public_entry, *, exception_type=BaseException):
    """Seal original CM exits before enter, outside ordinary admission graphs.

    The capture closure and hidden responsibility functions themselves are
    the declared validation root.  Their own rewriting and initial C binary
    provenance are outside this model; public code/proxy pollution is covered.
    """
    def enter(manager):
        # Actual exit roots already belong to the registered original manager
        # before its first enter.  No body later selects/reconstructs them.
        capture_exit(manager)
        actual_enter, _cleanup, _retire = public_entry(manager)
        return actual_enter()

    def exit(manager, kind, error, traceback):
        # Independent original storage lookup: no public manager getter,
        # proxy, normal integrity, deadline, cancellation or guard check.
        body_remember, cleanup, retire = capture_exit(manager)
        first = error
        try:
            if error is not None:
                try:
                    first = body_remember(error)
                except exception_type as caught:
                    if first is None:
                        first = caught
            try:
                cleanup()
            except exception_type as caught:
                if first is None:
                    first = caught
        finally:
            try:
                retire(first is None)
            except exception_type as caught:
                if first is None:
                    first = caught
        if first is not None:
            raise first
        return False
    return enter, exit


def _registries_original():
    if _LIVE is not _ORIGINAL_CONTAINERS[0] or _MANAGERS is not _ORIGINAL_CONTAINERS[1]:
        _fail("REGISTRY_CHANGED")
    if type(_LIVE) is not dict or type(_MANAGERS) is not dict:
        _fail("REGISTRY_CHANGED")


def _add_work_original(record, name, amount):
    _ADD_WORK(record, name, amount)


def _validate_context_original(context):
    """Pure source registry proof; never calls operation record validation."""
    _registries_original()
    if type(context) is not operation._SourceUnitContext:
        _fail("GUARD_REQUIRED")
    source_token, source_record = context.source_token, context.source_record
    if source_token is None and source_record is None:
        return
    if type(source_token) is not _SourceToken or type(source_record) is not _SourceRecord:
        _fail("REGISTRY_CHANGED")
    if dict.get(_LIVE, id(source_token)) is not source_record or source_record.token is not source_token:
        _fail("REGISTRY_CHANGED")
    _PEEK(source_record)
    if source_record.context is not context or source_record.operation_token is not context.token:
        _fail("RECORD_CHANGED")
    projection = source_record.selector
    if type(projection) is not dict or any(type(key) is not str for key in projection):
        _fail("RECORD_CHANGED")
    if frozenset(projection) != frozenset(("source_kind", "package_root", "generation", "expected_request_sha256", "profile_paths", "original_operation_id")):
        _fail("RECORD_CHANGED")
    for name in ("source_kind", "package_root", "expected_request_sha256"):
        if type(projection[name]) is not str:
            _fail("RECORD_CHANGED")
    if type(projection["generation"]) is not int:
        _fail("RECORD_CHANGED")
    paths, original_operation_id = projection["profile_paths"], projection["original_operation_id"]
    if paths is not None and (type(paths) is not list or any(type(path) is not str for path in paths)):
        _fail("RECORD_CHANGED")
    if original_operation_id is not None and type(original_operation_id) is not str:
        _fail("RECORD_CHANGED")
    wanted = context.inputs[11]
    fields = (projection.get("source_kind"), projection.get("package_root"), projection.get("generation"),
        projection.get("expected_request_sha256"),
        None if projection.get("profile_paths") is None else tuple(projection["profile_paths"]), projection.get("original_operation_id"))
    if fields != wanted:
        _fail("RECORD_CHANGED")
    for name in _WORK_FIELDS:
        number = _GET(source_record.parser_work, name)
        if type(number) is not int or not 0 <= number <= _U64:
            _fail("WORK_INVALID")


def _validate_new_source_original(context, token, record):
    _registries_original()
    if type(context) is not operation._SourceUnitContext:
        _fail("GUARD_REQUIRED")
    if type(token) is not _SourceToken or type(record) is not _SourceRecord:
        _fail("GUARD_REQUIRED")
    if context.source_token is not None or context.source_record is not None:
        _fail("REENTRANT")
    if dict.get(_LIVE, id(token)) is not record or record.token is not token or record.context is not context:
        _fail("REGISTRY_CHANGED")
    _PEEK(record)
    if record.operation_token is not context.token or record.phase != "begin":
        _fail("RECORD_CHANGED")


def _source_record_original(source_token):
    native._require_no_callback_original()
    _registries_original()
    if type(source_token) is not _SourceToken:
        _fail("GUARD_REQUIRED")
    record = dict.get(_LIVE, id(source_token))
    if type(record) is not _SourceRecord or record.token is not source_token:
        _fail("CLOSED")
    _PEEK(record)
    if not operation._same_owner(record.context.owner, operation._owner_original()):
        _fail("OWNER_MISMATCH")
    return record


def _native_context_original(source_token, operation_token):
    record = _source_record_original(source_token)
    if record.operation_token is not operation_token:
        _fail("GUARD_REQUIRED")
    context = operation._source_context_original(operation_token)
    if record.context is not context:
        _fail("RECORD_CHANGED")
    if record.first is not None:
        raise record.first
    if record.unknown:
        _fail("UNKNOWN_NATIVE_EFFECT")
    return record


def _checkpoint_allowed_original(context, source_token, stage):
    _validate_context_original(context)
    if source_token is None and context.source_token is None and context.source_record is None:
        if type(stage) is not str or stage not in ("bases", "target_begin", "target_observed", "output_bound"):
            _fail("PHASE_INVALID")
        return True
    record = context.source_record
    if record.token is not source_token or type(stage) is not str:
        _fail("GUARD_REQUIRED")
    allowed = {"source_begin": ("begin",), "location": ("begin", "opening", "location"),
        "metadata": ("location", "metadata"), "before_body": ("metadata", "before_body"),
        "captured": ("reading", "captured"), "selection": ("captured", "selection"),
        "final_metadata": ("selection", "final_metadata"), "before_close": ("final_metadata", "before_close"),
        "source_native": ("begin", "opening", "location", "metadata", "before_body", "reading", "selection")}
    if stage not in allowed or record.phase not in allowed[stage]:
        _fail("PHASE_INVALID")
    _validate_outputs_original(record)
    return True


def _check_original(record, stage=None):
    _integrity_original()
    current = operation._source_context_original(record.operation_token)
    if current is not record.context:
        _fail("RECORD_CHANGED")
    if stage is None:
        operation._source_budget_original(current.registration)
        current.budget.check(callback=False)
        operation._source_budget_original(current.registration)
    else:
        operation._source_checkpoint_original(record.operation_token, record.token, stage)
        _validate_outputs_original(record)
    _PEEK(record)


def _remember_original(record, exc, *, unknown=False):
    first = operation._remember_original(record.context.registration, exc)
    return _FAILURE(record, first, unknown)


def _scope_factory_original(operation_token, enroll_manager, enroll):
    """Return an original non-generator manager, preserving BaseException identity."""
    _integrity_original()
    context = operation._source_scope_context_original(operation_token)
    if context.source_token is not None or context.source_record is not None:
        _fail("REENTRANT")
    manager = _SourceManager(_TAG, context)
    original_close_callbacks = native._source_cleanup_callbacks_original
    # Independent captured storage and original descriptors survive rejected
    # record/class/global mutations during a callback.  No release gate reads
    # a caller-modifiable manager.record or record.cleanup attribute.
    close_state = [None, None, None, None, False, False, False, None]
    remember_first, retain_first, mark_unknown = operation._FIRST_ORIGINAL, _FAILURE, _MARK_UNKNOWN
    live, managers, drop = _ORIGINAL_CONTAINERS[0], _ORIGINAL_CONTAINERS[1], _DROP
    record_attempted = _SourceRecord.__dict__["cleanup_attempted"]
    record_normal = _SourceRecord.__dict__["cleanup_normal"]
    manager_retired = _SourceManager.__dict__["retired"]
    kind_of, get_item, pop_item, key_of, every_kind = type, dict.get, dict.pop, id, any
    dictionary_type, text_type, boolean_type = dict, str, bool
    owner_original, same_owner, owner = operation._owner_original, operation._same_owner, context.owner
    no_callback = native._require_no_callback_original
    cleanup_gate_factory = native._cleanup_callback_gate_original
    require_original_owner = _make_minimal_owner_original(owner)
    original_unsettled = AuthorityReplicaSourceError("UNSETTLED")
    registration, original_manager_type = context.registration, _SourceManager
    exception_type = BaseException

    def require_cleanup_actor():
        # Hidden responsibility root: real actual owner and independently
        # registered native callback latch only.  No full graph, record,
        # manager/proxy or ordinary native admission can prevent unwinding.
        require_original_owner()
        gate = close_state[7]
        if gate is not None:
            gate()

    def enter():
        _MANAGER_ANCHOR(manager)
        if manager.entered or manager.retired:
            _fail("REENTRANT")
        if operation._source_context_original(operation_token) is not context:
            _fail("RECORD_CHANGED")
        operation._source_budget_original(context.registration)
        context.budget.check(callback=False)
        begin = context.budget.snapshot()
        operation._source_budget_original(context.registration)
        if any(begin[name] != 0 for name in _SOURCE_COUNTERS):
            _fail("SOURCE_BEGIN_INVALID")
        projection = selector.project_selector(context.inputs[2])
        token = _SourceToken(_TAG)
        scope_id = str(uuid.uuid4())
        record = _SourceRecord(_TAG, token, context, projection, scope_id, begin)
        close_state[0], close_state[3] = record, token
        manager.token, manager.record, manager.entered = token, record, True
        enroll(record, manager)
        _LIVE[id(token)] = record
        _seal_output_original(record, "begin", begin)
        try:
            operation._attach_source_record_original(operation_token, token, record)
            actual_close, actual_audit = original_close_callbacks(context.unit_key, token)
            close_state[1], close_state[2] = actual_close, actual_audit
            if type(actual_close) is not FunctionType or type(actual_audit) is not FunctionType:
                _fail("CALLBACK_INVALID")
            close_state[7] = cleanup_gate_factory(operation_token)
            def close_proxy():
                no_callback()
                _source_record_original(token)
                return actual_close()
            def audit_proxy():
                no_callback()
                _source_record_original(token)
                return actual_audit()
            # Inspectable mirrors are guarded proxies.  The real close roots
            # remain in independent captured storage and cannot be obtained
            # from ordinary manager/record fields during a callback.
            record.cleanup, record.audit = close_proxy, audit_proxy
            _check_original(record, "source_begin")
            return token
        except BaseException as exc:
            raise _remember_original(record, exc)

    def cleanup():
        require_cleanup_actor()
        record, close, audit, token, attempted, normal, unknown, gate = close_state
        if record is None or attempted:
            return
        close_state[4] = True
        first = None
        try:
            record_attempted.__set__(record, True)
        except exception_type as exc:
            first = remember_first(registration, exc)
        if close is None:
            # No source native acquire can precede installation of these.
            close_state[5] = True
            try:
                record_normal.__set__(record, True)
            except exception_type as exc:
                if first is None:
                    first = remember_first(registration, exc)
            if first is not None:
                raise first
            return
        try:
            close()
        except exception_type as exc:
            # An existing body/cancel/parser first may be delivered by close
            # after every actual holder was released.  Only the independent
            # resource audit below can establish unfinished/unknown closure.
            if first is None:
                first = remember_first(registration, exc)
            retain_first(record, first)
        try:
            result = audit()
            if kind_of(result) is not dictionary_type or every_kind(kind_of(key) is not text_type for key in result) or get_item(result, "normal_closed") is not True:
                raise original_unsettled
        except exception_type as exc:
            mark_unknown()
            close_state[6] = True
            if first is None:
                first = remember_first(registration, exc)
                retain_first(record, first, True)
        else:
            close_state[5] = True
            try:
                record_normal.__set__(record, True)
            except exception_type as exc:
                # A rejected mirror descriptor/class is an ordinary first;
                # the actual independent native audit already closed normal.
                if first is None:
                    first = remember_first(registration, exc)
        if first is not None:
            raise first

    def retire(normal):
        require_cleanup_actor()
        first = None
        if kind_of(normal) is not boolean_type:
            first = remember_first(registration, original_unsettled)
        record, close, audit, token, attempted, closed, unknown, gate = close_state
        try:
            if record is not None and (not attempted or not closed or unknown):
                # Native cleanup already retained each original debt before
                # its holder registry was retired.  Source never clears it.
                mark_unknown()
                first = remember_first(registration, original_unsettled)
                retain_first(record, first, True)
        except exception_type as exc:
            if first is None:
                first = remember_first(registration, exc)
        finally:
            try:
                if record is not None and get_item(live, key_of(token)) is record:
                    pop_item(live, key_of(token))
            finally:
                try:
                    # Also retires the original pre-enter manager enrollment
                    # when enter failed before any source token was created.
                    drop(record, manager, token)
                finally:
                    try:
                        if get_item(managers, key_of(manager)) is manager:
                            pop_item(managers, key_of(manager))
                    finally:
                        manager_retired.__set__(manager, True)
        if first is not None:
            raise first

    def remember_body(error):
        require_cleanup_actor()
        first = remember_first(registration, error)
        record = close_state[0]
        if record is not None:
            retain_first(record, first)
        return first

    def enter_proxy():
        return _trusted_scope_callbacks_original(manager)[0]()
    def cleanup_proxy():
        return _trusted_scope_callbacks_original(manager)[1]()
    def retire_proxy(normal):
        return _trusted_scope_callbacks_original(manager)[2](normal)
    def body_proxy(error):
        return _trusted_body_remember_original(manager)(error)
    manager.enter, manager.cleanup, manager.retire, manager.remember_body = enter_proxy, cleanup_proxy, retire_proxy, body_proxy
    enroll_manager(manager, (enter, cleanup, retire, remember_body))
    _MANAGERS[id(manager)] = manager
    return manager


def _trusted_scope_callbacks_original(manager):
    native._require_no_callback_original()
    _registries_original()
    if type(manager) is not _SourceManager or dict.get(_MANAGERS, id(manager)) is not manager:
        _fail("GUARD_REQUIRED")
    held = _MANAGER_ANCHOR(manager)
    if not operation._same_owner(held[1].owner, operation._owner_original()):
        _fail("OWNER_MISMATCH")
    return held[7][:3]


def _trusted_callbacks_original(source_token):
    record = _source_record_original(source_token)
    manager = next((item for item in _MANAGERS.values() if item.record is record), None)
    if manager is None:
        _fail("REGISTRY_CHANGED")
    _enter, cleanup, retire = _trusted_scope_callbacks_original(manager)
    return cleanup, retire


def _trusted_body_remember_original(manager):
    _trusted_scope_callbacks_original(manager)
    return _MANAGER_ANCHOR(manager)[7][3]


def _detach_original(value, record, depth=0):
    if depth > 64:
        _fail("LIMIT")
    _add_work_original(record, "own_structural_visits", 1)
    kind = type(value)
    if kind is dict:
        if any(type(key) is not str for key in value):
            _fail("TYPE_INVALID")
        return {key: _detach_original(child, record, depth + 1) for key, child in value.items()}
    if kind is list:
        return [_detach_original(child, record, depth + 1) for child in value]
    if kind is tuple:
        return tuple(_detach_original(child, record, depth + 1) for child in value)
    if kind in (str, int, bool, bytes) or value is None:
        return value
    _fail("TYPE_INVALID")


def _binding_snapshot_original(value, record, depth=0):
    if depth > 256:
        _fail("LIMIT")
    _add_work_original(record, "own_structural_visits", 1)
    kind = type(value)
    if kind is dict:
        if any(type(key) is not str for key in value):
            _fail("TYPE_INVALID")
        return ("dict", tuple((key, _binding_snapshot_original(child, record, depth + 1)) for key, child in value.items()))
    if kind in (tuple, list):
        return ("tuple" if kind is tuple else "list", tuple(_binding_snapshot_original(child, record, depth + 1) for child in value))
    if kind is rawfact._RawFactCache:
        return ("cache", _binding_snapshot_original(value._keys, record, depth + 1), _binding_snapshot_original(value._facts, record, depth + 1))
    fields = ((rawfact.RawFact, ("relative_key", "raw", "length", "sha256", "document", "work")),
        (rawfact.LocalWork, ("lexical_nodes", "shape_nodes", "maximum_depth", "preflight_input_bytes", "decoded_codepoints",
            "lexical_character_visits", "parse_calls", "parse_input_bytes", "canonical_output_bytes", "pure_hash_input_bytes")),
        (rawfact.JsonObject, ("items",)), (rawfact.JsonArray, ("values",)),
        (builder.BuildResult, ("declaration", "raw_control_refs", "directory_index", "file_index", "package_entry_set_sha256",
            "structural_visits", "canonical_output_bytes", "pure_hash_input_bytes")))
    for admitted, names in fields:
        if kind is admitted:
            return (admitted, tuple(_binding_snapshot_original(_GET(value, name), record, depth + 1) for name in names))
    if kind in (str, int, bool, bytes) or value is None:
        return (kind, value)
    _fail("TYPE_INVALID")


def _seal_output_original(record, name, value):
    fingerprint = _binding_snapshot_original(value, record)
    _SEAL(record, name, value, fingerprint)


def _validate_outputs_original(record):
    for name, value, expected in _OUTPUTS(record):
        if name in ("location", "metadata", "read"):
            native._assert_source_result_original(record.context.unit_key, record.token, name, value)
            continue
        if _binding_snapshot_original(value, record) != expected:
            _fail("RECORD_CHANGED")


def location_original(source_token):
    record = _source_record_original(source_token)
    _native_context_original(source_token, record.operation_token)
    try:
        _check_original(record, "location")
        if record.phase != "begin":
            _fail("PHASE_INVALID")
        _TRANSITION(record, "opening")
        actual = native._open_source_location_original(record.context.unit_key, source_token)
        native._assert_source_result_original(record.context.unit_key, source_token, "location", actual)
        _SEAL(record, "location", actual, None)
        for field in ("source_kind", "package_root"):
            if actual[field] != record.selector[field]:
                _fail("SOURCE_CHANGED")
        _TRANSITION(record, "location")
        _check_original(record, "location")
        return _location_declaration_original(record)
    except BaseException as exc:
        raise _remember_original(record, exc)


def _location_declaration_original(record):
    actual = record.location
    value = {key: record.selector[key] for key in ("source_kind", "package_root", "generation", "expected_request_sha256", "original_operation_id")}
    value.update({"root_identity": actual["root_identity"], "ancestors": actual["ancestors"], "profile": actual.get("profile")})
    return _detach_original(value, record)


def _drive_original(record, generator, progress_type):
    if type(generator) is not GeneratorType:
        _fail("WORK_INVALID")
    previous = None
    while True:
        _check_original(record)
        # This yield is immediately before the fixed json.loads in the
        # original generator.  Count its actual invocation attempt before
        # advancing, including a parse exception; the deadline gate above
        # may prevent it without counting a nonexistent call.
        if progress_type is rawfact.Progress and previous is not None and previous.phase == "before_parse":
            _add_work_original(record, "raw_parse_calls", 1)
            _add_work_original(record, "raw_parse_input_bytes", previous.input_offset)
        try:
            progress = next(generator)
        except StopIteration as done:
            _check_original(record)
            return done.value
        if type(progress) is not progress_type:
            _fail("WORK_INVALID")
        record.progress = progress
        if progress_type is rawfact.Progress:
            _raw_progress_original(record, generator, progress)
        previous = progress
        _check_original(record)


def _raw_progress_original(record, generator, progress):
    key = progress.relative_key
    if key is None:
        return
    if type(key) is not str:
        _fail("WORK_INVALID")
    totals = record.progress_totals.setdefault(key, {"preflight": 0, "tokens": 0, "codepoints": 0, "canonical": 0, "hash": 0, "shape": 0})
    changes = []
    if progress.phase == "preflight_chunk":
        # Use only the original public Progress fields.  No frame, locals,
        # scanner inspection or profiling is performed.  Codepoints close
        # once from actual completed LocalWork; a failed partial stays open.
        changes = [("preflight", "raw_preflight_input_bytes", progress.input_offset),
            ("tokens", "raw_preflight_tokens", progress.local_nodes)]
    elif progress.phase == "before_parse":
        changes = [("preflight", "raw_preflight_input_bytes", progress.input_offset), ("tokens", "raw_preflight_tokens", progress.local_nodes)]
    elif progress.phase in ("shape_chunk", "before_canonical"):
        changes = [("shape", "own_structural_visits", progress.local_nodes)]
    elif progress.phase == "canonical_chunk":
        changes = [("canonical", "own_canonical_output_bytes", progress.input_offset)]
    elif progress.phase == "hash_chunk":
        changes = [("hash", "pure_hash_input_bytes", progress.input_offset)]
    for local_name, output, now in changes:
        if type(now) is not int or now < totals[local_name]:
            _fail("WORK_INVALID")
        _add_work_original(record, output, now - totals[local_name])
        totals[local_name] = now


def _bind_cache_original(record, cache):
    if type(cache) is not rawfact._RawFactCache:
        _fail("CACHE_INVALID")
    native._assert_source_result_original(record.context.unit_key, record.token, "read", record.read)
    readers = record.read["readers"]
    if type(readers) is not tuple or len(readers) != len(cache._facts):
        _fail("READER_BINDING_INVALID")
    bindings, seen = [], set()
    for reader in readers:
        if type(reader) is not tuple or len(reader) != 5 or type(reader[0]) is not str:
            _fail("READER_BINDING_INVALID")
        key, holder_key, raw, digest, size = reader
        if key in seen or type(raw) is not bytes or type(digest) is not str or type(size) is not int:
            _fail("READER_BINDING_INVALID")
        fact = rawfact.lookup(cache, key)
        if fact.raw is not raw or fact.sha256 != digest or fact.length != size:
            _fail("READER_BINDING_INVALID")
        bindings.append((holder_key, fact))
        seen.add(key)
        work = fact.work
        totals = record.progress_totals[key]
        if totals["codepoints"] != 0:
            _fail("WORK_INVALID")
        _add_work_original(record, "raw_preflight_codepoints", work.decoded_codepoints)
        totals["codepoints"] = work.decoded_codepoints
        expected = {"preflight": work.preflight_input_bytes, "tokens": work.lexical_nodes,
            "codepoints": work.decoded_codepoints, "canonical": work.canonical_output_bytes,
            "shape": work.shape_nodes, "hash": work.pure_hash_input_bytes}
        # Successful local cache binding closes every progress accounting
        # domain.  No estimate replaces an incomplete failed scanner step.
        if totals != expected or work.parse_calls != 1 or work.parse_input_bytes != len(raw):
            _fail("WORK_INVALID")
    if seen != set(record.read["control_bytes"]):
        _fail("READER_BINDING_INVALID")
    record.reader_bindings = tuple(bindings)


def _capacity_original(record):
    metadata, ctx = record.metadata, record.context
    operation._source_budget_original(ctx.registration)
    ctx.budget.check(callback=False)
    dictionary = ctx.budget_dict
    size = metadata["S"]
    if type(size) is not int or not 0 <= size <= 128 * 1024**3 or dictionary["_active"] is not None:
        _fail("METADATA_INVALID")
    if (dictionary["_read_bytes"] + size + 131072 > ctx.limits["max_read_bytes"]
            or dictionary["_source_read_bytes"] + size + 131072 > ctx.limits["source_max_read_bytes"]):
        _fail("READ_LIMIT")


def _codec_original(record, function, *args, **kwargs):
    _check_original(record)
    controls = record.read["control_bytes"]
    _add_work_original(record, "original_codec_calls", 1)
    _add_work_original(record, "original_codec_input_bytes", sum(len(raw) for raw in controls.values()))
    result = function(*args, **kwargs)
    _check_original(record)
    return result


def _profile_binding_original(record):
    fact = rawfact.lookup(record.cache, "request.json")
    # The original cache's frozen local document is traversed once.  This is
    # not an extra parser or private original-codec call.
    work = builder._Work()
    request = _drive_original(record, builder._thaw_steps(fact.document, work), builder.BuildProgress)
    _add_work_original(record, "own_structural_visits", work.visits)
    if record.selector["source_kind"] == "detached_raw2":
        return
    actual = record.location
    wanted_profile = actual["raw2_profile"]
    wanted_target = {"namespace": ".reno-authority-raw-preservation-v2",
        "namespace_identity": actual["namespace_identity"], "operation_relative_key": record.selector["original_operation_id"],
        "operation_identity": actual["root_identity"]}
    if (request["operation_id"] != record.selector["original_operation_id"] or request["profile"] != wanted_profile
            or request["target"] != wanted_target):
        _fail("PROFILE_BINDING_INVALID")


def capture_original(source_token):
    record = _source_record_original(source_token)
    _native_context_original(source_token, record.operation_token)
    try:
        _check_original(record, "metadata")
        if record.phase != "location":
            _fail("PHASE_INVALID")
        _TRANSITION(record, "metadata")
        metadata = native._census_metadata_original(record.context.unit_key, source_token)
        native._assert_source_result_original(record.context.unit_key, source_token, "metadata", metadata)
        _SEAL(record, "metadata", metadata, None)
        _check_original(record, "before_body")
        _TRANSITION(record, "before_body")
        _capacity_original(record)
        _TRANSITION(record, "reading")
        actual = native._read_source_package_original(record.context.unit_key, source_token)
        native._assert_source_result_original(record.context.unit_key, source_token, "read", actual)
        _SEAL(record, "read", actual, None)
        _add_work_original(record, "native_hash_input_bytes", actual["native_hash_input_bytes"])
        if metadata["directories"] != actual["inventory"]["directories"]:
            _fail("CONTENT_CHANGED")
        meta_files = metadata["files"]
        read_files = actual["inventory"]["files"]
        if len(meta_files) != len(read_files):
            _fail("CONTENT_CHANGED")
        for before, after in zip(meta_files, read_files):
            if {key: value for key, value in before.items() if key != "role"} != {key: value for key, value in after.items() if key != "sha256"}:
                _fail("CONTENT_CHANGED")
        cache = _drive_original(record, rawfact.build_cache_steps(actual["control_bytes"]), rawfact.Progress)
        _bind_cache_original(record, cache)
        _seal_output_original(record, "cache", cache)
        if rawfact.lookup(cache, "request.json").sha256 != record.selector["expected_request_sha256"]:
            _fail("REQUEST_CHANGED")
        _profile_binding_original(record)
        target = record.context.target_observation
        build = _drive_original(record, builder.build_declaration_steps(cache,
            directories=actual["inventory"]["directories"], files=actual["inventory"]["files"],
            generation=record.selector["generation"], replica_id=record.context.inputs[4],
            observation_scope_id=record.scope_id, profile_sha256=target["profile"]["profile_sha256"], node_id=target["node"]["node_id"]), builder.BuildProgress)
        if type(build) is not builder.BuildResult:
            _fail("DECLARATION_INVALID")
        _seal_output_original(record, "builder_result", build)
        for name, amount in (("own_structural_visits", build.structural_visits),
                ("own_canonical_output_bytes", build.canonical_output_bytes), ("pure_hash_input_bytes", build.pure_hash_input_bytes)):
            _add_work_original(record, name, amount)
        declaration = _codec_original(record, replica.normalize, build.declaration, fact_bytes=actual["control_bytes"])
        encoded = _codec_original(record, replica.encode, declaration, fact_bytes=actual["control_bytes"])
        if record.selector["source_kind"] == "profile_raw2":
            _codec_original(record, raw2.cross_validate_package, fact_bytes=actual["control_bytes"],
                generation=record.selector["generation"], inventory=actual["inventory"])
        _add_work_original(record, "own_canonical_output_bytes", len(encoded))
        _seal_output_original(record, "declaration", declaration)
        _seal_output_original(record, "encoded", encoded)
        record.work_complete = True
        _TRANSITION(record, "captured")
        _check_original(record, "captured")
        return {"source_declaration": _detach_original(declaration, record), "source_declaration_bytes": encoded,
            "raw_control_refs": _detach_original(build.raw_control_refs, record)}
    except BaseException as exc:
        raise _remember_original(record, exc)


def selection_original(source_token):
    record = _source_record_original(source_token)
    _native_context_original(source_token, record.operation_token)
    try:
        _check_original(record, "selection")
        if record.phase != "captured":
            _fail("PHASE_INVALID")
        _add_work_original(record, "pure_hash_input_bytes", len(record.encoded))
        value = {"source_location": _location_declaration_original(record),
            "source_declaration": _detach_original(record.declaration, record),
            "source_declaration_sha256": hashlib.sha256(record.encoded).hexdigest(),
            "raw_control_refs": _detach_original(record.builder_result.raw_control_refs, record),
            "package_entry_set_sha256": record.builder_result.package_entry_set_sha256}
        _seal_output_original(record, "selection", value)
        _TRANSITION(record, "selection")
        _check_original(record, "selection")
        return _detach_original(value, record)
    except BaseException as exc:
        raise _remember_original(record, exc)


def _finish_source_original(source_token):
    record = _source_record_original(source_token)
    _native_context_original(source_token, record.operation_token)
    try:
        _check_original(record)
        if record.phase == "final_metadata":
            return
        if record.phase != "selection":
            _fail("PHASE_INVALID")
        final = native._source_result_original(record.context.unit_key, source_token, "final_metadata")
        native._assert_source_result_original(record.context.unit_key, source_token, "final_metadata", final)
        if type(final) is not dict or final.get("metadata_unchanged") is not True:
            _fail("CONTENT_CHANGED")
        _TRANSITION(record, "final_metadata")
        _check_original(record, "final_metadata")
    except BaseException as exc:
        raise _remember_original(record, exc)


def _source_result_original(source_token):
    _finish_source_original(source_token)
    record = _source_record_original(source_token)
    value = {"source_location": _location_declaration_original(record),
        "source_declaration": _detach_original(record.declaration, record), "source_declaration_bytes": record.encoded,
        "selection": _detach_original(record.selection, record),
        "source_begin": _detach_original(record.begin, record),
        "package_files": record.metadata["N"], "package_directories": record.metadata["D"], "package_bytes": record.metadata["S"],
        "complete_preview": False, "source_closed": False, "copy": False, "restore": False, "write": False, "sender": False}
    value["parser_work"] = {name: _GET(record.parser_work, name) for name in _WORK_FIELDS}
    value["own_parser_work_complete"] = record.work_complete
    _check_original(record)
    return value


def _catalog_extra_original():
    """The fixed public-codec graph, rawfact primitives and local anchors."""
    modules = (replica, raw2, replica.raw1, replica.census, raw2.projection, raw2.policy)
    extras = []
    for module in modules:
        # This closed tuple of known source modules is inventory at original
        # bootstrap only.  It cannot import or admit a supplied module/plugin.
        names = tuple(name for name, value in vars(module).items()
            if type(name) is str and (type(value) is FunctionType or isinstance(value, type)
                or name.startswith("_") and name.upper() == name
                or name in ("FORMAT_VERSION", "KIND", "SCOPE", "DIRECTORY", "LAYOUT_PROFILE", "LAYOUT_SOURCE_SHA256",
                    "hashlib", "json", "re", "posixpath", "ntpath", "math", "raw1", "raw2", "c2", "census", "projection", "policy", "wraps")))
        extras.append((module, names))
    local = ("_GET", "_SET", "_TAG", "_LIVE", "_MANAGERS", "_ORIGINAL_CONTAINERS", "_U64", "_SOURCE_COUNTERS", "_WORK_FIELDS",
        "_ENROLL_MANAGER", "_MANAGER_ANCHOR", "_ENROLL", "_PEEK", "_TRANSITION", "_FAILURE", "_DROP", "_SEAL", "_OUTPUTS", "_ADD_WORK", "_MARK_UNKNOWN",
        "_failure_blocked_original", "replica", "raw2", "selector", "rawfact", "builder", "operation", "native", "hashlib", "uuid", "sys", "utf8", "asyncio", "os", "threading", "FunctionType", "GeneratorType")
    raw_constants = tuple(name for name in vars(rawfact) if name.startswith("_") and name.upper() == name)
    extras.extend(((sys.modules[__name__], local),
        (rawfact, raw_constants + ("bisect", "codecs", "hashlib", "json", "re", "dataclass", "Generator")),
        (rawfact.bisect, ("bisect_left",)), (rawfact.codecs, ("getincrementaldecoder", "lookup")),
        (rawfact.json, ("loads", "JSONDecoder", "JSONDecodeError", "JSONEncoder", "decoder", "encoder", "scanner")),
        (rawfact.json.decoder, ("JSONDecoder", "JSONDecodeError", "JSONObject", "JSONArray", "scanstring", "BACKSLASH", "WHITESPACE", "WHITESPACE_STR", "scanner")),
        (rawfact.json.scanner, ("make_scanner", "c_make_scanner", "py_make_scanner", "NUMBER_RE")),
        (rawfact.json.encoder, ("JSONEncoder", "encode_basestring", "encode_basestring_ascii", "c_make_encoder", "_make_iterencode", "ESCAPE_DCT", "ESCAPE", "ESCAPE_ASCII", "INFINITY")),
        (utf8, ("IncrementalDecoder", "decode", "encode", "getregentry", "codecs")),
        (rawfact.codecs, ("IncrementalDecoder", "BufferedIncrementalDecoder", "utf_8_decode", "utf_8_encode")),
        (rawfact.re, ("_compile", "_compiler", "_parser")),
        (hashlib, ("sha256",)), (uuid, ("uuid4", "UUID", "os", "int_", "bytes_")),
        (rawfact.re, ("compile", "fullmatch"))))
    for module in (rawfact.re._compiler, rawfact.re._parser):
        names = tuple(name for name, value in vars(module).items()
            if type(name) is str and (type(value) is FunctionType or isinstance(value, type)
                or name.startswith("_") and name.upper() == name or name in ("_sre", "_parser", "_code", "_compile", "isstring")))
        extras.append((module, names))
    return tuple(extras)


def _make_scope_entry_original(factory, enroll_manager, enroll):
    """Keep enrollment in the fixed factory closure, not a module issuer API."""
    def entry(operation_token):
        return factory(operation_token, enroll_manager, enroll)
    entry.__name__ = entry.__qualname__ = "source_scope_original"
    return entry


def _make_integrity_original():
    """Pin local original entry classes/functions; operation pins dependencies."""
    names = tuple(name for name, value in globals().items()
        if type(name) is str and type(value) is FunctionType and value.__module__ == __name__)
    funcs = tuple((name, globals()[name], globals()[name].__code__, globals()[name].__defaults__,
        globals()[name].__kwdefaults__, globals()[name].__closure__) for name in names)
    classes = (_SourceToken, _SourceRecord, _SourceManager, _ParserWork)
    descriptors = tuple((cls, tuple(cls.__dict__.items())) for cls in classes)
    containers, tag = _ORIGINAL_CONTAINERS, _TAG
    get_global, type_of = globals, type

    def verify():
        current = get_global()
        if current.get("_ORIGINAL_CONTAINERS") is not containers or current.get("_TAG") is not tag:
            _fail("BINDING_CHANGED")
        for name, func, code, defaults, kwdefaults, closure in funcs:
            if (current.get(name) is not func or type_of(func) is not FunctionType or func.__code__ is not code
                    or func.__defaults__ is not defaults or func.__kwdefaults__ is not kwdefaults or func.__closure__ is not closure):
                _fail("BINDING_CHANGED")
        for cls, before in descriptors:
            actual = tuple(cls.__dict__.items())
            if len(actual) != len(before) or any(a[0] != b[0] or a[1] is not b[1] for a, b in zip(actual, before)):
                _fail("BINDING_CHANGED")
        _registries_original()
    return verify


source_scope_original = _make_scope_entry_original(_scope_factory_original, _ENROLL_MANAGER, _ENROLL)
_SourceManager.__enter__, _SourceManager.__exit__ = _make_manager_lifetime_original(_EXIT_CAPTURE, _trusted_scope_callbacks_original)
del _scope_factory_original, _ENROLL_MANAGER, _ENROLL, _EXIT_CAPTURE, _make_manager_lifetime_original
_integrity_original = _make_integrity_original()
