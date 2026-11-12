"""Original RP1 lifetimes for three separately fixed drivers.

The original checker retains its exact zero-effect validator and lifetime.
The DATA-chain driver owns one metadata-only native unit under its own sole
ledger and original operation; it does not grant source/target or authority.
Both borrow the caller's actual G1, whose outer exit is not this unit's exit.
The source-census unit holds original bases/observer/output/source under a
readonly clock and event-checked ledger, and returns no full-preview/producer
or source-closed token.
"""
from __future__ import annotations

import asyncio
import builtins as _keyscan_builtins
import _operator as _keyscan_coperator
import operator as _keyscan_operator
import itertools as _keyscan_itertools
import contextlib
import dis
import os
import sys
import threading
import time
from types import CellType, CodeType, FunctionType, MappingProxyType, ModuleType
import uuid
import weakref

from . import storage_lifecycle as lifecycle
from . import personal_authority_replica_group as group
from . import personal_authority_replica_selector as selector
from . import personal_authority_replica_budget as accounting
from . import personal_authority_replica_policy as policy
from . import personal_authority_preservation_routing as routing
from . import personal_authority_raw_preservation as raw1
from . import personal_authority_raw_preservation2 as raw2
from . import personal_authority_preservation_budget as legacy_accounting
from . import personal_authority_owned_ex_group as owned_group


_TYPE, _GET, _SET = type, object.__getattribute__, object.__setattr__
_CLASS_GET = type.__getattribute__
_MISSING = object()
_TAG = object()
_CONSUMED = object()
_TASK = asyncio.Task
_OWNER = lifecycle._owner
_CURRENT = lifecycle._CURRENT
_BUDGET = accounting.ReplicaBudget
_GUARD = lifecycle.StorageGuard
_PROFILE = lifecycle.StorageProfile
_UUID4 = uuid.uuid4
_WEAK = weakref.ref
_FIELDS = ("database", "media", "reports", "checkpoints")
_LIVE, _OWNERS, _CLOSED, _GUARD_CALLS, _NATIVE_DEBTS = {}, {}, {}, {}, {}
_REGISTRY_LOCK = threading.RLock()
_ORIGINAL_CONTAINERS = (_LIVE, _OWNERS, _CLOSED, _GUARD_CALLS, _NATIVE_DEBTS)
_ORIGINAL_LOCK = _REGISTRY_LOCK


class AuthorityReplicaOperationError(ValueError):
    def __init__(self, suffix):
        self.code = "AUTHORITY_REPLICA_OPERATION1_" + suffix
        super().__init__(self.code)



def _make_error_kernel_original(*, kind_of=type, text_type=str, dictionary_type=dict,
        exception_new=ValueError.__new__, exception_init=BaseException.__init__,
        exception_dictionary=BaseException.__dict__["__dict__"]):
    error_type = AuthorityReplicaOperationError
    def make(suffix):
        if kind_of(suffix) is not text_type:
            suffix = "RECORD_CHANGED"
        code = "AUTHORITY_REPLICA_OPERATION1_" + suffix
        value = exception_new(error_type)
        exception_init(value, code)
        dictionary_type.__setitem__(exception_dictionary.__get__(value, error_type), "code", code)
        return value
    def reject(suffix):
        raise make(suffix) from None
    def exposed_make(suffix):
        return make(suffix)
    def exposed_fail(suffix, *, _error=error_type):
        return reject(suffix)
    def trusted_make():
        return make
    def trusted_reject():
        return reject
    return exposed_make, exposed_fail, trusted_make, trusted_reject


(_error_original, _fail, _trusted_make_error_original,
 _trusted_reject_original) = _make_error_kernel_original()



class _OperationToken:
    __slots__ = ("__weakref__",)

    def __init__(self, tag):
        if tag is not _TAG:
            _fail("GUARD_REQUIRED")


class _OperationRecord:
    __slots__ = ("token", "owner", "owner_key", "budget", "phase", "first",
                 "callback_active", "guard", "group_token", "profiles",
                 "checkpoint_count", "inputs", "run_id", "kind", "native_scope", "source_context")

    def __init__(self, token, owner, inputs):
        self.token, self.owner = token, owner
        self.owner_key = (owner[0], owner[1], id(owner[2]))
        self.inputs, self.group_token, self.profiles = inputs, inputs[1], inputs[8]
        self.budget = self.guard = self.first = self.run_id = None
        self.phase, self.callback_active, self.checkpoint_count = "registering", False, 0
        self.kind, self.native_scope = "check_only", None
        self.source_context = None


class _OperationRegistration:
    __slots__ = ("token", "record", "owner", "owner_key", "inputs", "budget",
                 "budget_dict", "budget_fixed", "budget_last", "guard", "guard_fields",
                 "phase", "first", "callback_active", "checkpoint_count", "window",
                 "run_id", "callback_wrapper", "callback_pin", "anchor", "budget_anchor", "kind", "native_scope", "source_context")

    def __init__(self, record):
        self.token, self.record = record.token, record
        self.owner, self.owner_key, self.inputs = record.owner, record.owner_key, record.inputs
        self.budget = self.budget_dict = self.budget_fixed = self.budget_last = None
        self.guard = self.guard_fields = self.first = self.window = self.run_id = None
        self.phase, self.callback_active, self.checkpoint_count = "registering", False, 0
        self.callback_wrapper = self.callback_pin = None
        self.anchor = (record, self.token, self.owner, self.owner_key, self.inputs)
        self.budget_anchor = None
        self.kind, self.native_scope = "check_only", None
        self.source_context = None


class _GuardCall:
    __slots__ = ("registration", "record", "token", "owner", "owner_key", "guard",
                 "group_token", "profiles", "phase", "checkpoint_count")

    def __init__(self, registration):
        self.registration, self.record = registration, registration.record
        self.token, self.owner, self.owner_key = registration.token, registration.owner, registration.owner_key
        self.guard, self.group_token = registration.guard, registration.inputs[1]
        self.profiles, self.phase = registration.inputs[8], registration.phase
        self.checkpoint_count = registration.checkpoint_count


class _ClosedCheck:
    __slots__ = ("fields", "anchor", "consumed", "consumed_marker")

    def __init__(self, fields):
        self.fields, self.anchor = fields, (fields,)
        self.consumed, self.consumed_marker = False, None


def _dictionary(value):
    dictionary = _GET(value, "__dict__")
    if _TYPE(dictionary) is not dict or any(_TYPE(k) is not str for k in dictionary):
        _fail("RECORD_CHANGED")
    return dictionary


def _function_pin(function):
    closure = _GET(function, "__closure__")
    cells = () if closure is None else tuple((cell, _GET(cell, "cell_contents")) for cell in closure)
    defaults, kwdefaults = _GET(function, "__defaults__"), _GET(function, "__kwdefaults__")
    keywords = () if kwdefaults is None else tuple(dict.items(kwdefaults))
    return (function, _GET(function, "__code__"), defaults, kwdefaults, keywords, closure, cells)


def _check_function(pin):
    function, code, defaults, kwdefaults, keywords, closure, cells = pin
    if (_TYPE(function) is not FunctionType or _GET(function, "__code__") is not code
            or _GET(function, "__defaults__") is not defaults
            or _GET(function, "__kwdefaults__") is not kwdefaults
            or _GET(function, "__closure__") is not closure):
        _fail("RECORD_CHANGED")
    if kwdefaults is not None:
        if (_TYPE(kwdefaults) is not dict or len(kwdefaults) != len(keywords)
                or any(_TYPE(k) is not str for k in kwdefaults)
                or any(dict.get(kwdefaults, k, _MISSING) is not v for k, v in keywords)):
            _fail("RECORD_CHANGED")
    if closure is not None:
        if _TYPE(closure) is not tuple or len(closure) != len(cells):
            _fail("RECORD_CHANGED")
        for cell, (original, contents) in zip(closure, cells):
            if _TYPE(cell) is not CellType or cell is not original:
                _fail("RECORD_CHANGED")
            try:
                value = _GET(cell, "cell_contents")
            except ValueError:
                _fail("RECORD_CHANGED")
            if value is not contents:
                _fail("RECORD_CHANGED")


def _capture_module(module, names):
    dictionary = _GET(module, "__dict__")
    return module, tuple((name, dict.get(dictionary, name, _MISSING)) for name in names)


def _source_constant_snapshot_original(value, depth=0):
    """Freeze fixed pure-codec data only, never runtime registries or holders."""
    if depth > 64:
        _fail("RECORD_CHANGED")
    kind = type(value)
    if value is None or kind in (str, int, bool, bytes):
        return (kind, value)
    if kind is dict:
        if any(type(key) is not str for key in value):
            return _SOURCE_UNSUPPORTED_CONSTANT
        entries = tuple((key, _source_constant_snapshot_original(child, depth + 1))
            for key, child in dict.items(value))
        if any(child is _SOURCE_UNSUPPORTED_CONSTANT for _key, child in entries):
            return _SOURCE_UNSUPPORTED_CONSTANT
        return (dict, entries)
    if kind in (list, tuple):
        entries = tuple(_source_constant_snapshot_original(child, depth + 1) for child in value)
        if any(child is _SOURCE_UNSUPPORTED_CONSTANT for child in entries):
            return _SOURCE_UNSUPPORTED_CONSTANT
        return (kind, entries)
    if kind in (set, frozenset) and all(type(child) is str for child in value):
        return (kind, tuple(sorted(value)))
    # Regexes, C pointers, modules/classes and function objects keep their
    # actual object/MRO/ABI pins elsewhere; no unknown __dict__/repr is read.
    return _SOURCE_UNSUPPORTED_CONSTANT


def _capture_source_constants_original(modules):
    pins = []
    for module in modules:
        dictionary = _GET(module, "__dict__")
        if type(dictionary) is not dict or any(type(key) is not str for key in dictionary):
            _fail("RECORD_CHANGED")
        for name, value in dict.items(dictionary):
            if name.upper() == name and not name.startswith("__"):
                snapshot = _source_constant_snapshot_original(value)
                if snapshot is not _SOURCE_UNSUPPORTED_CONSTANT:
                    pins.append((module, name, value, snapshot))
    return tuple(pins)


def _verify_source_constants_original(pins):
    def match(value, snapshot):
        kind, expected = snapshot
        if type(value) is not kind:
            _fail("RECORD_CHANGED")
        if kind is dict:
            if (any(type(key) is not str for key in value) or len(value) != len(expected)):
                _fail("RECORD_CHANGED")
            for key, child in expected:
                actual = dict.get(value, key, _MISSING)
                if actual is _MISSING:
                    _fail("RECORD_CHANGED")
                match(actual, child)
        elif kind in (list, tuple):
            if len(value) != len(expected):
                _fail("RECORD_CHANGED")
            for actual, child in zip(value, expected):
                match(actual, child)
        elif kind in (set, frozenset):
            if any(type(child) is not str for child in value) or len(value) != len(expected):
                _fail("RECORD_CHANGED")
            if tuple(sorted(value)) != expected:
                _fail("RECORD_CHANGED")
        elif value != expected:
            _fail("RECORD_CHANGED")
    for module, name, original, snapshot in pins:
        dictionary = _GET(module, "__dict__")
        if (type(dictionary) is not dict or any(type(key) is not str for key in dictionary)
                or dict.get(dictionary, name, _MISSING) is not original):
            _fail("RECORD_CHANGED")
        match(original, snapshot)


def _capture_source_regex_tables_original(regex):
    """Capture only three used, fixed semantic tables, never regex caches."""
    compiler, parser = regex._compiler, regex._parser
    opcode_type = regex._constants._NamedIntConstant
    specifications = (
        (compiler, "_REPEATING_CODES", "repeat", opcode_type,
            ((compiler.MIN_REPEAT, (compiler.REPEAT, compiler.MIN_UNTIL, compiler.MIN_REPEAT_ONE)),
             (compiler.MAX_REPEAT, (compiler.REPEAT, compiler.MAX_UNTIL, compiler.REPEAT_ONE)),
             (compiler.POSSESSIVE_REPEAT, (compiler.POSSESSIVE_REPEAT, compiler.SUCCESS, compiler.POSSESSIVE_REPEAT_ONE)))),
        (parser, "ESCAPES", "escapes", str,
            tuple((key, (parser.LITERAL, number)) for key, number in
                ((r"\a", 7), (r"\b", 8), (r"\f", 12), (r"\n", 10), (r"\r", 13), (r"\t", 9), (r"\v", 11), (r"\\", 92)))),
        (parser, "CATEGORIES", "categories", str,
            ((r"\A", (parser.AT, parser.AT_BEGINNING_STRING)),
             (r"\b", (parser.AT, parser.AT_BOUNDARY)),
             (r"\B", (parser.AT, parser.AT_NON_BOUNDARY)),
             (r"\d", (parser.IN, (parser.CATEGORY, parser.CATEGORY_DIGIT))),
             (r"\D", (parser.IN, (parser.CATEGORY, parser.CATEGORY_NOT_DIGIT))),
             (r"\s", (parser.IN, (parser.CATEGORY, parser.CATEGORY_SPACE))),
             (r"\S", (parser.IN, (parser.CATEGORY, parser.CATEGORY_NOT_SPACE))),
             (r"\w", (parser.IN, (parser.CATEGORY, parser.CATEGORY_WORD))),
             (r"\W", (parser.IN, (parser.CATEGORY, parser.CATEGORY_NOT_WORD))),
             (r"\Z", (parser.AT, parser.AT_END_STRING)))))
    def opcode(value):
        if type(value) is not opcode_type:
            _fail("RECORD_CHANGED")
        return (opcode_type, value)
    def snapshot(value, role, expected):
        if type(value) is not tuple or len(value) != (3 if role == "repeat" else 2):
            _fail("RECORD_CHANGED")
        if role == "repeat":
            if any(actual is not original for actual, original in zip(value, expected)):
                _fail("RECORD_CHANGED")
            children = tuple(opcode(child) for child in value)
        elif role == "escapes":
            if (value[0] is not expected[0] or type(value[1]) is not int
                    or value[1] != expected[1]):
                _fail("RECORD_CHANGED")
            children = (opcode(value[0]), (int, value[1]))
        else:
            first, second = opcode(value[0]), value[1]
            if value[0] is not expected[0]:
                _fail("RECORD_CHANGED")
            if type(expected[1]) is opcode_type:
                if second is not expected[1]:
                    _fail("RECORD_CHANGED")
                children = (first, opcode(second))
            else:
                if (type(second) is not list or len(second) != 1
                        or type(second[0]) is not tuple or len(second[0]) != 2):
                    _fail("RECORD_CHANGED")
                if any(actual is not original for actual, original in zip(second[0], expected[1])):
                    _fail("RECORD_CHANGED")
                pair = (tuple, second[0], tuple(opcode(child) for child in second[0]))
                children = (first, (list, second, (pair,)))
        return (tuple, value, children)
    pins = []
    for module, name, role, key_type, semantics in specifications:
        namespace = _GET(module, "__dict__")
        if type(namespace) is not dict or any(type(key) is not str for key in namespace):
            _fail("RECORD_CHANGED")
        table = dict.get(namespace, name, _MISSING)
        if type(table) is not dict or len(table) != len(semantics):
            _fail("RECORD_CHANGED")
        entries = tuple(dict.items(table))
        # Complete exact key domains precede any comparison or value read.
        if any(type(key) is not key_type for key, _value in entries):
            _fail("RECORD_CHANGED")
        captured = []
        for key, value in entries:
            expected = _MISSING
            for original, semantic in semantics:
                if (key is original if key_type is opcode_type else key == original):
                    expected = semantic
                    break
            if expected is _MISSING:
                _fail("RECORD_CHANGED")
            captured.append((key, snapshot(value, role, expected)))
        pins.append((module, namespace, name, table, key_type, tuple(captured)))
    return tuple(pins)


def _verify_source_regex_tables_original(pins):
    """Reject table hooks before original compiler/parser indexing/truthiness."""
    def match(value, snapshot):
        kind, original = snapshot[:2]
        if type(value) is not kind:
            _fail("RECORD_CHANGED")
        if kind in (tuple, list):
            children = snapshot[2]
            if value is not original or len(value) != len(children):
                _fail("RECORD_CHANGED")
            for actual, child in zip(value, children):
                match(actual, child)
        elif kind is int:
            if value != original:
                _fail("RECORD_CHANGED")
        elif value is not original:
            _fail("RECORD_CHANGED")
    for module, namespace, name, table, key_type, captured in pins:
        if (_GET(module, "__dict__") is not namespace or type(namespace) is not dict
                or any(type(key) is not str for key in namespace)
                or dict.get(namespace, name, _MISSING) is not table
                or type(table) is not dict or len(table) != len(captured)):
            _fail("RECORD_CHANGED")
        entries = tuple(dict.items(table))
        # Even an unknown equal/colliding key is rejected before hash/eq.
        if any(type(key) is not key_type for key, _value in entries):
            _fail("RECORD_CHANGED")
        for key, value in entries:
            expected = _MISSING
            for original, snapshot in captured:
                if (key is original if key_type is not str else key == original):
                    expected = snapshot
                    break
            if expected is _MISSING:
                _fail("RECORD_CHANGED")
            match(value, expected)


def _capture_source_dispatch_original(rawfact, utf8):
    """One initial selection, not proof of C registry/binary provenance."""
    regex, codecs = rawfact.re, rawfact.codecs
    regex_dictionary = _GET(regex, "__dict__")
    if type(regex_dictionary) is not dict or any(type(key) is not str for key in regex_dictionary):
        _fail("RECORD_CHANGED")
    pattern_type = regex.Pattern
    pattern_slots = _CLASS_GET(pattern_type, "__dict__")
    pattern_slot, flags_slot = pattern_slots["pattern"], pattern_slots["flags"]
    cache, cache2 = regex_dictionary["_cache"], regex_dictionary["_cache2"]
    maxima = regex_dictionary["_MAXCACHE"], regex_dictionary["_MAXCACHE2"]
    if (type(cache) is not dict or type(cache2) is not dict
            or any(type(limit) is not int or limit <= 0 for limit in maxima)
            or maxima != (512, 256)):
        _fail("RECORD_CHANGED")
    # Both actual runtime _compile string intentions have no inline flags.
    # bytes and compiled-Pattern key domains still receive their own exact
    # type/value gates; no arbitrary key can dispatch a caller object.
    intentions = (r"[0-9a-f]{64}", r"^[A-Za-z]:[\\/]")
    # CPython 3.12.10's int flags & T/DEBUG uses IntFlag's reverse AND,
    # EnumType.__call__ and Enum.__new__'s unchecked by-value cache return.
    # The two original callers pass exact zero flags, so require the original
    # zero hit and original operands before that fixed path can dispatch.
    flag_type, enum_type = regex_dictionary["RegexFlag"], regex_dictionary["enum"].EnumType
    flag_namespace = _CLASS_GET(flag_type, "__dict__")
    value_map, member_map = flag_namespace["_value2member_map_"], flag_namespace["_member_map_"]
    operands = []
    for name, number in (("T", 1), ("DEBUG", 128), ("NOFLAG", 0)):
        member = regex_dictionary[name]
        if type(member) is not flag_type or int.__int__(member) != number:
            _fail("RECORD_CHANGED")
        own = _GET(member, "__dict__")
        if type(own) is not dict or any(type(key) is not str for key in own):
            _fail("RECORD_CHANGED")
        # Ordinary inversion may add/change _inverted_; it is not used by
        # this path. Every other original own field stays the original value.
        fields = tuple((key, value) for key, value in dict.items(own) if key != "_inverted_")
        operands.append((name, member, number, own, fields))
    flag_pin = (flag_type, enum_type, value_map, member_map, tuple(operands))
    _verify_source_flag_dispatch_original(flag_pin)
    regex_pin = (regex, regex_dictionary, cache, cache2, maxima, pattern_type,
        pattern_slot, flags_slot, intentions, flag_pin)
    lookup, info_type = codecs.lookup, codecs.CodecInfo
    selected = lookup("utf-8")
    if type(selected) is not info_type or tuple.__len__(selected) != 4:
        _fail("RECORD_CHANGED")
    dictionary = _GET(selected, "__dict__")
    names = ("name", "encode", "decode", "incrementalencoder", "incrementaldecoder", "streamwriter", "streamreader")
    if (type(dictionary) is not dict or any(type(key) is not str for key in dictionary)
            or len(dictionary) != len(names) or any(name not in dictionary for name in names)):
        _fail("RECORD_CHANGED")
    expected = ("utf-8", utf8.encode, utf8.decode, utf8.IncrementalEncoder,
        utf8.IncrementalDecoder, utf8.StreamWriter, utf8.StreamReader)
    if (type(dictionary["name"]) is not str or dictionary["name"] != expected[0]
            or any(dictionary[name] is not value for name, value in zip(names[1:], expected[1:]))):
        _fail("RECORD_CHANGED")
    immutable = tuple(tuple.__getitem__(selected, index) for index in range(4))
    if any(a is not b for a, b in zip(immutable, (utf8.encode, utf8.decode, utf8.StreamReader, utf8.StreamWriter))):
        _fail("RECORD_CHANGED")
    entries = tuple((name, dictionary[name]) for name in names)
    codec_pin = (codecs, lookup, info_type, selected, dictionary, immutable, entries, utf8.IncrementalDecoder)
    return regex_pin, codec_pin


def _verify_source_flag_dispatch_original(pin):
    """Finite current admission for the two original zero-flag AND paths.

    This admits normal cache growth inside the supported nine-bit domain,
    including negative aliases, without freezing Enum cache contents. It is
    not admission for arbitrary Enum construction or a continuous history.
    """
    flag_type, enum_type, value_map, member_map, operands = pin
    namespace = _CLASS_GET(flag_type, "__dict__")
    if (type(flag_type) is not enum_type
            or namespace.get("_value2member_map_", _MISSING) is not value_map
            or namespace.get("_member_map_", _MISSING) is not member_map
            or type(value_map) is not dict or len(value_map) > 1024
            or type(member_map) is not dict or not 0 < len(member_map) <= 64):
        _fail("RECORD_CHANGED")
    entries = tuple(dict.items(value_map))
    # Exact domains precede any hash/eq, attribute lookup or int conversion.
    # In this supported nine-bit domain a negative cache alias points to the
    # corresponding nonnegative member (for example -1 -> 511).
    if any(type(key) is not int or not -512 <= key <= 511 for key, _value in entries):
        _fail("RECORD_CHANGED")
    allowed = ("_value_", "_name_", "__objclass__", "_sort_order_", "_inverted_")
    for key, member in entries:
        if type(member) is not flag_type:
            _fail("RECORD_CHANGED")
        own = _GET(member, "__dict__")
        if (type(own) is not dict or any(type(name) is not str for name in own)
                or any(name not in allowed for name in own)):
            _fail("RECORD_CHANGED")
        number = int.__int__(member)
        value, name = dict.get(own, "_value_", _MISSING), dict.get(own, "_name_", _MISSING)
        if (type(number) is not int or number != (key if key >= 0 else key + 512)
                or type(value) is not int or value != number
                or (name is not None and type(name) is not str)):
            _fail("RECORD_CHANGED")
        if "__objclass__" in own and dict.get(own, "__objclass__") is not flag_type:
            _fail("RECORD_CHANGED")
        if "_sort_order_" in own and (type(dict.get(own, "_sort_order_")) is not int
                or dict.get(own, "_sort_order_") < 0):
            _fail("RECORD_CHANGED")
        inverted = dict.get(own, "_inverted_", None)
        if inverted is not None and type(inverted) is not flag_type:
            _fail("RECORD_CHANGED")
    for name, member, number, own, fields in operands:
        if (type(member) is not flag_type or int.__int__(member) != number
                or _GET(member, "__dict__") is not own or type(own) is not dict
                or any(type(key) is not str for key in own)
                or len(own) != len(fields) + (1 if "_inverted_" in own else 0)):
            _fail("RECORD_CHANGED")
        for key, original in fields:
            if dict.get(own, key, _MISSING) is not original:
                _fail("RECORD_CHANGED")
        if type(dict.get(own, "_value_")) is not int or dict.get(own, "_value_") != number:
            _fail("RECORD_CHANGED")
        inverted = dict.get(own, "_inverted_", None)
        if inverted is not None and type(inverted) is not flag_type:
            _fail("RECORD_CHANGED")
    # The fixed original zero hit bypasses Enum._missing_ and all mutable
    # pseudo-member builders on the admitted T/DEBUG dispatch path.
    if dict.get(value_map, 0, _MISSING) is not operands[2][1]:
        _fail("RECORD_CHANGED")


def _verify_source_dispatch_original(pins):
    """Admit current cache/CodecInfo selections before the original dispatcher.

    Runtime cache contents may change normally. This gate fixes two original
    dict pointers, admits bounded exact key/value domains and verifies the
    actual selectable Pattern's immutable key intention. It does not rebuild
    regexes, freeze stdlib caches, parse caller objects or repair bad state.
    """
    if pins is None:
        _fail("RECORD_CHANGED")
    regex_pin, codec_pin = pins
    regex, namespace, cache, cache2, maxima, pattern_type, pattern_slot, flags_slot, intentions, flag_pin = regex_pin
    if (_GET(regex, "__dict__") is not namespace or type(namespace) is not dict
            or any(type(key) is not str for key in namespace)
            or dict.get(namespace, "_cache") is not cache or dict.get(namespace, "_cache2") is not cache2
            or dict.get(namespace, "Pattern") is not pattern_type
            or type(dict.get(namespace, "_MAXCACHE")) is not int or dict.get(namespace, "_MAXCACHE") != maxima[0]
            or type(dict.get(namespace, "_MAXCACHE2")) is not int or dict.get(namespace, "_MAXCACHE2") != maxima[1]):
        _fail("RECORD_CHANGED")
    _verify_source_flag_dispatch_original(flag_pin)
    cache_entries = []
    for table, maximum in zip((cache, cache2), maxima):
        if type(table) is not dict or len(table) > maximum:
            _fail("RECORD_CHANGED")
        entries = tuple(dict.items(table))
        # Validate the COMPLETE key domain before any lookup/hash/equality of
        # cache keys or any access to a selected object's properties.
        for key, _value in entries:
            if type(key) is not tuple or len(key) != 3 or type(key[2]) is not int or not 0 <= key[2] <= 511:
                _fail("RECORD_CHANGED")
            key_type, pattern, flags = key
            if key_type is str:
                if type(pattern) is not str:
                    _fail("RECORD_CHANGED")
            elif key_type is bytes:
                if type(pattern) is not bytes:
                    _fail("RECORD_CHANGED")
            elif key_type is pattern_type:
                if type(pattern) is not pattern_type or flags != 0:
                    _fail("RECORD_CHANGED")
            else:
                _fail("RECORD_CHANGED")
        cache_entries.extend(entries)
    for key, value in cache_entries:
        key_type, pattern, flags = key
        if type(value) is not pattern_type:
            _fail("RECORD_CHANGED")
        literal = pattern_slot.__get__(value, pattern_type)
        actual_flags = flags_slot.__get__(value, pattern_type)
        if type(literal) not in (str, bytes) or type(actual_flags) is not int or not 0 <= actual_flags <= 511:
            _fail("RECORD_CHANGED")
        if key_type is pattern_type:
            if value is not pattern:
                _fail("RECORD_CHANGED")
            continue
        if type(literal) is not key_type or literal != pattern:
            _fail("RECORD_CHANGED")
        fixed = intentions if key_type is str else tuple(text.encode("ascii") for text in intentions)
        if pattern in fixed:
            # re's supported string compiler adds UNICODE unless ASCII was
            # explicitly requested. These fixed literals carry no inline
            # modifiers, so a same-text Pattern with different flags fails.
            intended_flags = flags | (32 if key_type is str and not flags & 256 else 0)
            if actual_flags != intended_flags:
                _fail("RECORD_CHANGED")
    codecs, lookup, info_type, selected, dictionary, immutable, entries, decoder = codec_pin
    if (codecs.lookup is not lookup or codecs.CodecInfo is not info_type
            or lookup("utf-8") is not selected or type(selected) is not info_type
            or tuple.__len__(selected) != len(immutable) or _GET(selected, "__dict__") is not dictionary
            or type(dictionary) is not dict or any(type(key) is not str for key in dictionary)
            or len(dictionary) != len(entries)):
        _fail("RECORD_CHANGED")
    for index, value in enumerate(immutable):
        if tuple.__getitem__(selected, index) is not value:
            _fail("RECORD_CHANGED")
    for name, value in entries:
        if dict.get(dictionary, name, _MISSING) is not value:
            _fail("RECORD_CHANGED")
    if dict.get(dictionary, "incrementaldecoder") is not decoder:
        _fail("RECORD_CHANGED")


def _source_dispatch_catalog_extra_original(rawfact, utf8):
    """Only actual new dispatch links, supplementing the closed source graph."""
    return ((rawfact.json, ("detect_encoding", "codecs")),
        (rawfact.json.codecs, ("BOM_UTF8", "BOM_UTF16_BE", "BOM_UTF16_LE", "BOM_UTF32_BE", "BOM_UTF32_LE")),
        (rawfact.re, ("match", "fullmatch", "_compile", "Pattern", "RegexFlag", "_cache", "_cache2",
            "_MAXCACHE", "_MAXCACHE2", "T", "DEBUG", "NOFLAG", "enum", "_compiler", "_parser", "_constants")),
        (rawfact.re._compiler, ("_REPEATING_CODES", "MIN_REPEAT", "MAX_REPEAT", "POSSESSIVE_REPEAT",
            "REPEAT", "MIN_UNTIL", "MIN_REPEAT_ONE", "MAX_UNTIL", "REPEAT_ONE", "SUCCESS", "POSSESSIVE_REPEAT_ONE")),
        (rawfact.re._parser, ("ESCAPES", "CATEGORIES", "LITERAL", "AT", "AT_BEGINNING_STRING",
            "AT_BOUNDARY", "AT_NON_BOUNDARY", "IN", "CATEGORY", "CATEGORY_DIGIT", "CATEGORY_NOT_DIGIT",
            "CATEGORY_SPACE", "CATEGORY_NOT_SPACE", "CATEGORY_WORD", "CATEGORY_NOT_WORD", "AT_END_STRING",
            "MIN_REPEAT", "MAX_REPEAT", "POSSESSIVE_REPEAT")),
        (rawfact.re._constants, ("_NamedIntConstant",)),
        (rawfact.re.enum, ("EnumType", "_not_given")),
        (rawfact.re._compiler._sre, ("compile", "unicode_iscased", "unicode_tolower",
            "ascii_iscased", "ascii_tolower", "CODESIZE", "MAGIC")),
        (rawfact.codecs, ("lookup", "getincrementaldecoder", "CodecInfo")),
        (utf8, ("IncrementalDecoder", "IncrementalEncoder", "StreamReader", "StreamWriter", "encode", "decode", "getregentry", "codecs")))


def _capture_graph(modules, extra):
    """Capture fixed module definitions and their actual descriptor/closure graph."""
    bindings, classes, functions, seen, seen_classes = [], [], [], set(), set()

    def function(value):
        if _TYPE(value) is not FunctionType or id(value) in seen:
            return
        if value is _integrity_original:
            return  # The original minimal verifier closure is the trust root.
        seen.add(id(value))
        pin = _function_pin(value)
        functions.append(pin)
        for _cell, contents in pin[-1]:
            function(contents)

    def class_pins(value):
        if not issubclass(_TYPE(value), type) or id(value) in seen_classes:
            return
        seen_classes.add(id(value))
        original_mro = _CLASS_GET(value, "__mro__")
        entries = []
        for base in original_mro:
            members = _CLASS_GET(base, "__dict__")
            # Include inherited Python implementations, such as contextlib's
            # generator manager constructor; builtin object/exception slots
            # are immutable but their actual lookup positions are pinned too.
            selected = set(members)
            selected.update(("__getattribute__", "__getattr__", "__setattr__", "__delattr__"))
            fields = tuple((key, members.get(key, _MISSING)) for key in sorted(selected))
            entries.append((base, fields))
            for _key, descriptor in fields:
                function(descriptor)
                if _TYPE(descriptor) is property:
                    function(descriptor.fget)
                    function(descriptor.fset)
                    function(descriptor.fdel)
                elif _TYPE(descriptor) in (staticmethod, classmethod):
                    function(_GET(descriptor, "__func__"))
        classes.append((value, original_mro, tuple(entries)))

    for module in modules:
        dictionary = _GET(module, "__dict__")
        names = []
        for name, value in tuple(dict.items(dictionary)):
            if name == "_bind_integrity_once":
                continue
            if _TYPE(value) is FunctionType and _GET(value, "__module__") == module.__name__:
                names.append(name)
                function(value)
            elif issubclass(_TYPE(value), type) and _CLASS_GET(value, "__module__") == module.__name__:
                names.append(name)
                class_pins(value)
        bindings.append(_capture_module(module, tuple(names)))
    for module, names in extra:
        pin = _capture_module(module, names)
        bindings.append(pin)
        for _name, value in pin[1]:
            function(value)
            class_pins(value)
    for cls in native._ABI_CLASSES + source_native._ABI_CLASSES:
        class_pins(cls)
    links = []
    for pin in functions:
        value, code = pin[:2]
        namespace, builtins = _GET(value, "__globals__"), _GET(value, "__builtins__")
        names = set()
        def globals_in_code(body):
            for instruction in dis.get_instructions(body):
                if instruction.opname == "LOAD_GLOBAL":
                    names.add(instruction.argval)
                elif instruction.opname == "IMPORT_NAME":
                    names.add("__import__")
            for constant in body.co_consts:
                if _TYPE(constant) is CodeType:
                    globals_in_code(constant)
        globals_in_code(code)
        fields = []
        for name in sorted(names):
            # This one future binding has its separate original closure gate.
            if value is _verify_catalog_original and name == "_CALLEE_PINS":
                continue
            original = dict.get(namespace, name, _MISSING)
            builtin = dict.get(builtins, name, _MISSING)
            # Missing globals must remain missing, including when they resolve
            # a builtin. Callable/type/module links are fixed. Noncallable
            # control data have explicit policy/registry/constant anchors;
            # unrelated mutable runtime state is not frozen by this rule.
            if original is _MISSING or callable(original) or _TYPE(original) is ModuleType:
                fields.append((name, original, builtin))
        links.append((value, namespace, builtins, tuple(fields)))
    return tuple(bindings), tuple(classes), tuple(functions), tuple(links)


def _verify_catalog_original(catalog, *, _TYPE=type, _GET=object.__getattribute__,
        _CLASS_GET=type.__getattribute__, _MISSING=_MISSING, _fail=_trusted_reject_original(),
        FunctionType=FunctionType, CellType=CellType, dict=dict, str=str,
        tuple=tuple, list=list, any=any, len=len, zip=zip, ValueError=ValueError,
        RuntimeError=RuntimeError, _KEYSCAN_ALL=_keyscan_builtins.all,
        _KEYSCAN_MAP=_keyscan_builtins.map, _KEYSCAN_IS=_keyscan_coperator.is_,
        _KEYSCAN_REPEAT=_keyscan_itertools.repeat):
    # This pure verifier and the original public entry form the trusted anchor.
    # No source-file hash, arbitrary descriptor or caller repr is evaluated.
    if _CALLEE_PINS is not catalog:
        _fail("RECORD_CHANGED")
    for function, namespace, builtins, fields in catalog[3]:
        if (_GET(function, "__globals__") is not namespace
                or _GET(function, "__builtins__") is not builtins
                or _TYPE(namespace) is not dict or _TYPE(builtins) is not dict
                or not _KEYSCAN_ALL(_KEYSCAN_MAP(_KEYSCAN_IS,
                    _KEYSCAN_MAP(_TYPE, namespace), _KEYSCAN_REPEAT(str)))
                or not _KEYSCAN_ALL(_KEYSCAN_MAP(_KEYSCAN_IS,
                    _KEYSCAN_MAP(_TYPE, builtins), _KEYSCAN_REPEAT(str)))):
            _fail("RECORD_CHANGED")
        for name, original, builtin in fields:
            if (dict.get(namespace, name, _MISSING) is not original
                    or dict.get(builtins, name, _MISSING) is not builtin):
                _fail("RECORD_CHANGED")
    for module, entries in catalog[0]:
        dictionary = _GET(module, "__dict__")
        if _TYPE(dictionary) is not dict or not _KEYSCAN_ALL(_KEYSCAN_MAP(_KEYSCAN_IS, _KEYSCAN_MAP(_TYPE, dictionary), _KEYSCAN_REPEAT(str))):
            _fail("RECORD_CHANGED")
        for name, original in entries:
            if dict.get(dictionary, name, _MISSING) is not original:
                _fail("RECORD_CHANGED")
    modules, package, selected = _MODULE_LINKS
    if sys.modules is not modules:
        _fail("RECORD_CHANGED")
    wanted, found = dict(selected), {}
    try:
        # Select the fixed entries by exact-string iteration, rather than a
        # lookup that could compare a hostile non-string colliding key.
        for name, module in dict.items(modules):
            if _TYPE(name) is str and name in wanted:
                found[name] = module
    except RuntimeError:
        _fail("RECORD_CHANGED")
    if len(found) != len(wanted) or any(found[name] is not module for name, module in selected):
        _fail("RECORD_CHANGED")
    if _TYPE(package) is not dict or not _KEYSCAN_ALL(_KEYSCAN_MAP(_KEYSCAN_IS, _KEYSCAN_MAP(_TYPE, package), _KEYSCAN_REPEAT(str))):
        _fail("RECORD_CHANGED")
    for name, module in selected:
        if dict.get(package, name.rsplit(".", 1)[1], _MISSING) is not module:
            _fail("RECORD_CHANGED")
    gate_cache, gate_expected = _SHARED_GATE_CACHE_PINS
    if (_TYPE(gate_cache) is not list or len(gate_cache) != len(gate_expected)
            or any(a is not b for a, b in zip(gate_cache, gate_expected))):
        _fail("RECORD_CHANGED")
    for cls, original_mro, entries in catalog[1]:
        mro = _CLASS_GET(cls, "__mro__")
        if len(mro) != len(original_mro) or any(a is not b for a, b in zip(mro, original_mro)):
            _fail("RECORD_CHANGED")
        for base, fields in entries:
            dictionary = _CLASS_GET(base, "__dict__")
            if (any(_TYPE(k) is not str for k in dictionary)
                    or len(dictionary) != len(tuple(value for _name, value in fields if value is not _MISSING))):
                _fail("RECORD_CHANGED")
            for name, original in fields:
                if dictionary.get(name, _MISSING) is not original:
                    _fail("RECORD_CHANGED")
    for function, code, defaults, kwdefaults, keywords, closure, cells in catalog[2]:
        # Inline the minimal function verifier: a replaced _check_function
        # must be rejected before any dispatch to that downstream helper.
        if (_TYPE(function) is not FunctionType or _GET(function, "__code__") is not code
                or _GET(function, "__defaults__") is not defaults
                or _GET(function, "__kwdefaults__") is not kwdefaults
                or _GET(function, "__closure__") is not closure):
            _fail("RECORD_CHANGED")
        if kwdefaults is not None:
            if (_TYPE(kwdefaults) is not dict or len(kwdefaults) != len(keywords)
                    or not _KEYSCAN_ALL(_KEYSCAN_MAP(_KEYSCAN_IS, _KEYSCAN_MAP(_TYPE, kwdefaults), _KEYSCAN_REPEAT(str)))
                    or any(dict.get(kwdefaults, k, _MISSING) is not v for k, v in keywords)):
                _fail("RECORD_CHANGED")
        if closure is not None:
            if _TYPE(closure) is not tuple or len(closure) != len(cells):
                _fail("RECORD_CHANGED")
            for cell, (original, contents) in zip(closure, cells):
                if _TYPE(cell) is not CellType or cell is not original:
                    _fail("RECORD_CHANGED")
                try:
                    value = _GET(cell, "cell_contents")
                except ValueError:
                    _fail("RECORD_CHANGED")
                if value is not contents:
                    _fail("RECORD_CHANGED")
    native_modules, native_selected = _NATIVE_MODULE_LINKS
    if sys.modules is not native_modules:
        _fail("RECORD_CHANGED")
    native_wanted, native_found = dict(native_selected), {}
    try:
        for name, module in dict.items(native_modules):
            if _TYPE(name) is str and name in native_wanted:
                native_found[name] = module
    except RuntimeError:
        _fail("RECORD_CHANGED")
    if len(native_found) != len(native_wanted) or any(native_found[name] is not module for name, module in native_selected):
        _fail("RECORD_CHANGED")
    for cls, original_metaclass in _NATIVE_META_PINS:
        if _TYPE(cls) is not original_metaclass:
            _fail("RECORD_CHANGED")
    for cls, original_fields, entries in _ABI_STRUCTURE_FIELDS:
        current_fields = _CLASS_GET(cls, "__dict__").get("_fields_", _MISSING)
        if current_fields is not original_fields or _TYPE(current_fields) is not list or len(current_fields) != len(entries):
            _fail("RECORD_CHANGED")
        for current, original in zip(current_fields, entries):
            if (_TYPE(current) is not tuple or len(current) != len(original)
                    or any(a is not b for a, b in zip(current, original))):
                _fail("RECORD_CHANGED")
    for function, argtypes, arguments, restype, errcheck in _NATIVE_SIGNATURES:
        current = _GET(function, "argtypes")
        if (current is not argtypes or _GET(function, "restype") is not restype
                or _GET(function, "errcheck") is not errcheck
                or _TYPE(current) not in (list, tuple) or _TYPE(current) is not _TYPE(argtypes)
                or len(current) != len(arguments)
                or any(a is not b for a, b in zip(current, arguments))):
            _fail("RECORD_CHANGED")
    # All downstream code/default/closure/class and native pins were checked
    # above. This fixed pure callee can now inspect nested codec constants.
    _verify_source_constants_original(_SOURCE_CONSTANT_PINS)
    _verify_source_regex_tables_original(_SOURCE_REGEX_TABLE_PINS)
    _verify_source_dispatch_original(_SOURCE_DISPATCH_PINS)


def _registries_original():
    current = (_LIVE, _OWNERS, _CLOSED, _GUARD_CALLS, _NATIVE_DEBTS)
    if (_REGISTRY_LOCK is not _ORIGINAL_LOCK
            or any(a is not b or _TYPE(a) is not dict for a, b in zip(current, _ORIGINAL_CONTAINERS))):
        _fail("REGISTRY_CHANGED")
    for index, dictionary in enumerate(current):
        for key in dictionary:
            if index in (1, 3):
                if (_TYPE(key) is not tuple or len(key) != 3
                        or any(_TYPE(item) is not int or item < 0 for item in key)):
                    _fail("REGISTRY_CHANGED")
            elif _TYPE(key) is not int or key < 0:
                _fail("REGISTRY_CHANGED")


def _unknown_original():
    _registries_original()
    if (_NATIVE_DEBTS or native._failure_blocked_original()
            or source_native._failure_blocked_original() or source_module._failure_blocked_original()):
        _fail("UNSETTLED")


def _owner_original():
    owner = _OWNER()
    if (_TYPE(owner) is not tuple or len(owner) != 3
            or _TYPE(owner[0]) is not int or owner[0] < 0
            or _TYPE(owner[1]) is not int or owner[1] < 0
            or owner[2] is not None and not issubclass(_TYPE(owner[2]), _TASK)):
        _fail("OWNER_MISMATCH")
    return owner


def _same_owner(left, right):
    return (left[0] == right[0] and left[1] == right[1] and left[2] is right[2])


def _active_original(owner):
    owners = _ORIGINAL_CONTAINERS[1]
    key = (owner[0], owner[1], id(owner[2]))
    registration = dict.get(owners, key)
    if registration is None:
        return None
    if _TYPE(registration) is not _OperationRegistration:
        _fail("REGISTRY_CHANGED")
    _registration_anchor_original(registration)
    if not _same_owner(registration.owner, owner):
        _fail("REGISTRY_CHANGED")
    return registration


def _registration_anchor_original(registration):
    anchor = registration.anchor
    if (_TYPE(anchor) is not tuple or len(anchor) != 5
            or registration.record is not anchor[0] or registration.token is not anchor[1]
            or registration.owner is not anchor[2] or registration.owner_key is not anchor[3]
            or registration.inputs is not anchor[4]
            or _TYPE(anchor[0]) is not _OperationRecord or _TYPE(anchor[1]) is not _OperationToken
            or _TYPE(anchor[2]) is not tuple or len(anchor[2]) != 3
            or _TYPE(anchor[3]) is not tuple or len(anchor[3]) != 3
            or any(_TYPE(value) is not int for value in anchor[3])
            or _TYPE(anchor[4]) is not tuple or len(anchor[4]) != 12
            or _TYPE(registration.phase) is not str or _TYPE(registration.callback_active) is not bool
            or _TYPE(registration.checkpoint_count) is not int):
        _fail("RECORD_CHANGED")


def _make_first_original(*, dict=dict, type=type, str=str, id=id,
        issubclass=issubclass, BaseException=BaseException, all=all, any=any,
        make_error=_trusted_make_error_original()):
    """Build the minimal trusted failure-delivery kernel with original slots."""
    slots = _CLASS_GET(_OperationRegistration, "__dict__")
    first_slot, dictionary_slot, record_slot = (slots[name] for name in ("first", "budget_dict", "record"))
    record_type, registration_type = _OperationRecord, _OperationRegistration
    record_first = _CLASS_GET(record_type, "__dict__")["first"]
    error_type = AuthorityReplicaOperationError
    originals = {}
    phase_slot = slots["phase"]
    live, owners = _ORIGINAL_CONTAINERS[:2]

    def peek(registration):
        item = dict.get(originals, id(registration))
        return None if item is None else item[1]

    def remember(registration, error):
        # Independent expected-first is authoritative. A caller-written
        # budget._first is never adopted as a historical original exception.
        item = dict.get(originals, id(registration))
        first = None if item is None else item[1]
        if first is None:
            first = error if issubclass(type(error), BaseException) else make_error("RECORD_CHANGED")
            originals[id(registration)] = (registration, first)
        first_slot.__set__(registration, first)
        record = record_slot.__get__(registration, registration_type)
        if type(record) is record_type:
            record_first.__set__(record, first)
        dictionary = dictionary_slot.__get__(registration, registration_type)
        if type(dictionary) is dict and all(type(k) is str for k in dictionary):
            dict.__setitem__(dictionary, "_first", first)
        if error is not first:
            try:
                BaseException.add_note(first, "Additional replica operation failure")
            except BaseException:
                pass
        return first

    def retire(registration):
        # This fixed cleanup release cannot clear a live first, even when a
        # caller can see Python private names. No callback invokes it.
        phase = phase_slot.__get__(registration, registration_type)
        if (type(phase) is not str or phase not in ("closed", "retired_failed")
                or any(value is registration for value in dict.values(live))
                or any(value is registration for value in dict.values(owners))):
            raise error_type("PHASE_INVALID")
        item = dict.pop(originals, id(registration), None)
        return None if item is None else item[1]
    return remember, peek, retire


def _remember_original(registration, error):
    return _FIRST_ORIGINAL(registration, error)


def _reject_reentry_original(owner):
    # Called by original routing, including during callbacks with bad arguments.
    if not _ORIGINAL_CONTAINERS[1]:
        return
    _integrity_original()
    registration = _active_original(owner)
    if registration is not None:
        raise _remember_original(registration, AuthorityReplicaOperationError("REENTRANT"))


def _observer_original(kind, guard=None):
    # No RP1 live owner means the original legacy/G1 flow is unchanged.
    if not _ORIGINAL_CONTAINERS[1]:
        return
    _integrity_original()
    owner = _owner_original()
    registration = _active_original(owner)
    if registration is None:
        return
    if registration.callback_active or kind == "scope":
        raise _remember_original(registration, AuthorityReplicaOperationError("REENTRANT"))
    try:
        _integrity_original()
        _registries_original()
        window = registration.window
        if (_TYPE(window) is not _GuardCall
                or dict.get(_GUARD_CALLS, registration.owner_key) is not window):
            _fail("REENTRANT")
        if (window.registration is not registration or window.record is not registration.record
                or window.token is not registration.token or window.owner is not registration.owner
                or window.owner_key is not registration.owner_key or window.guard is not registration.guard
                or window.group_token is not registration.inputs[1] or window.profiles is not registration.inputs[8]
                or window.phase is not registration.phase or window.checkpoint_count is not registration.checkpoint_count
                or not _guard_phase_original(registration)
                or _CURRENT.get() is not registration.guard
                or kind == "guard" and guard is not registration.guard):
            _fail("GUARD_CHANGED")
        _record_original(registration, guard_check=False)
    except BaseException as exc:
        raise _FIRST_ORIGINAL(registration, exc)


def _observe_lifecycle_entry_original():
    _observer_original("entry")


def _observe_lifecycle_scope_original():
    _observer_original("scope")


def _observe_lifecycle_guard_original(guard):
    _observer_original("guard", guard)


def _bind_original(args, kwargs):
    names = ("target_profile", "group_token", "source", "mode", "operation_id",
             "expected_request_sha256", "check_cancelled")
    if len(args) > 1 or any(_TYPE(k) is not str for k in kwargs):
        _fail("ARGUMENT_INVALID")
    if any(k not in names for k in kwargs) or args and "target_profile" in kwargs:
        _fail("ARGUMENT_INVALID")
    values = dict(kwargs)
    if args:
        values["target_profile"] = args[0]
    if any(k not in values for k in names[:5]):
        _fail("ARGUMENT_INVALID")
    return tuple(values.get(k) for k in names)


def _uuid_original(value):
    if _TYPE(value) is not str:
        _fail("TYPE_INVALID")
    if (len(value) != 36 or any(value[i] != "-" for i in (8, 13, 18, 23))
            or any(c not in "0123456789abcdef" for i, c in enumerate(value) if i not in (8, 13, 18, 23))
            or not any(c != "0" for c in value if c != "-")):
        _fail("ARGUMENT_INVALID")


def _source_value(source):
    projection = selector.project_selector(source)
    return (projection["source_kind"], projection["package_root"], projection["generation"],
            projection["expected_request_sha256"],
            None if projection["profile_paths"] is None else tuple(projection["profile_paths"]),
            projection["original_operation_id"])


def _inputs_original(values):
    target, group_token, source, mode, operation_id, expected, callback = values
    if _TYPE(mode) is not str:
        _fail("TYPE_INVALID")
    if mode not in ("preview", "create", "resume", "verify"):
        _fail("ARGUMENT_INVALID")
    _uuid_original(operation_id)
    if mode in ("resume", "verify"):
        if _TYPE(expected) is not str:
            _fail("TYPE_INVALID")
        if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
            _fail("ARGUMENT_INVALID")
    elif expected is not None:
        _fail("ARGUMENT_INVALID")
    if callback is not None and not callable(callback):
        _fail("TYPE_INVALID")
    target_paths = selector.project_profile(target)
    profiles, paths = (target,), (target_paths,)
    if mode == "verify":
        if source is not None:
            _fail("ARGUMENT_INVALID")
        source_value = None
    else:
        source_value = _source_value(source)
        if source_value[0] == "profile_raw2":
            original_source = _GET(source, "profile")
            if original_source is not target:
                source_paths = selector.project_profile(original_source)
                if source_paths == target_paths:
                    _fail("PROFILE_ALIAS")
                profiles, paths = (target, original_source), (target_paths, source_paths)
    path_objects = tuple(tuple(_GET(profile, name) for name in _FIELDS) for profile in profiles)
    budget_mode = "producer" if mode in ("create", "resume") else "readonly"
    return (*values, budget_mode, profiles, paths, path_objects, source_value)


def _profiles_original(registration):
    inputs = registration.inputs
    for profile, original_paths, projection in zip(inputs[8], inputs[10], inputs[9]):
        current = selector.project_profile(profile)
        if current != projection or any(_GET(profile, name) is not original for name, original in zip(_FIELDS, original_paths)):
            _fail("RECORD_CHANGED")
    if inputs[2] is not None and _source_value(inputs[2]) != inputs[11]:
        _fail("RECORD_CHANGED")


def _guard_original(registration):
    guard = group.guard_original(registration.inputs[1])
    if _TYPE(guard) is not _GUARD or guard is not registration.guard or _CURRENT.get() is not guard:
        _fail("GUARD_CHANGED")
    dictionary = _dictionary(guard)
    if registration.guard_fields is not None:
        if dictionary is not registration.guard_fields[0]:
            _fail("GUARD_CHANGED")
        for name, original in registration.guard_fields[1]:
            if dict.get(dictionary, name, _MISSING) is not original:
                _fail("GUARD_CHANGED")
    profiles = dict.get(dictionary, "_profiles")
    owner = dict.get(dictionary, "_owner")
    if (_TYPE(profiles) is not tuple or len(profiles) != len(registration.inputs[8])
            or any(a is not b for a, b in zip(profiles, registration.inputs[8]))
            or _TYPE(owner) is not tuple or len(owner) != 3
            or _TYPE(owner[0]) is not int or _TYPE(owner[1]) is not int
            or not _same_owner(owner, registration.owner)
            or dict.get(dictionary, "_active") is not True or dict.get(dictionary, "_exclusive") is not True):
        _fail("GUARD_CHANGED")


_BUDGET_ZERO = ("_pending_resources", "_current_resources", "_peak_resources", "_uncertain_resources",
    "_read_bytes", "_source_read_bytes", "_read_calls", "_source_read_calls", "_seek_calls",
    "_uncertain_reads", "_uncertain_source_reads", "_confirmed", "_write_calls", "_uncertain_writes", "_terminal")


def _budget_original(registration):
    budget = registration.budget
    if _TYPE(budget) is not _BUDGET:
        _fail("RECORD_CHANGED")
    dictionary = _dictionary(budget)
    if dictionary is not registration.budget_dict:
        _fail("RECORD_CHANGED")
    anchor = registration.budget_anchor
    if (_TYPE(anchor) is not tuple or len(anchor) != 5
            or budget is not anchor[0] or dictionary is not anchor[1]
            or registration.budget_fixed is not anchor[2]
            or registration.callback_wrapper is not anchor[3]
            or registration.callback_pin is not anchor[4]):
        _fail("RECORD_CHANGED")
    fixed = registration.budget_fixed
    if len(dictionary) != len(fixed) + 3:
        _fail("RECORD_CHANGED")
    for name, original in fixed:
        if dict.get(dictionary, name, _MISSING) is not original:
            _fail("RECORD_CHANGED")
    for name in _BUDGET_ZERO:
        value = dictionary[name]
        if _TYPE(value) is not int or value != 0:
            _fail("UNSETTLED")
    for name in ("_resource_pending", "_resource_leases"):
        if _TYPE(dictionary[name]) is not dict or dictionary[name]:
            _fail("UNSETTLED")
    if dictionary["_active"] is not None or any(dictionary[k] is not False for k in (
            "_failure_started", "_failure_write_started", "_receipt_started")):
        _fail("UNSETTLED")
    if dictionary["_first"] is not registration.first:
        if registration.first is not None:
            raise registration.first
        _fail("RECORD_CHANGED")
    if dictionary["_in_callback"] is not registration.callback_active:
        _fail("RECORD_CHANGED")
    tick = dictionary["_last_tick"]
    if (_TYPE(tick) is not int or _TYPE(registration.budget_last) is not int
            or registration.budget_last < 0 or tick < registration.budget_last):
        _fail("RECORD_CHANGED")
    if dict(dictionary["limits"]) != policy.runtime_limits(registration.inputs[7]):
        _fail("RECORD_CHANGED")
    _check_function(registration.callback_pin)


def _record_original(registration, *, guard_check=True):
    _registries_original()
    _registration_anchor_original(registration)
    if _source_driver_selected_original(registration):
        _source_driver_anchor_original(registration)
    else:
        _driver_anchor_original(registration)
        if registration.source_context is not None or registration.record.source_context is not None:
            _fail("RECORD_CHANGED")
    original_first = _FIRST_PEEK(registration)
    if registration.first is not original_first:
        if original_first is not None:
            raise original_first
        _fail("RECORD_CHANGED")
    record = registration.record
    if (_TYPE(registration) is not _OperationRegistration or _TYPE(record) is not _OperationRecord
            or _TYPE(registration.token) is not _OperationToken
            or dict.get(_LIVE, id(registration.token)) is not registration
            or dict.get(_OWNERS, registration.owner_key) is not registration):
        _fail("REGISTRY_CHANGED")
    if not _same_owner(registration.owner, _owner_original()):
        _fail("OWNER_MISMATCH")
    for name in ("token", "owner", "owner_key", "inputs", "budget", "guard", "phase",
                 "first", "callback_active", "checkpoint_count", "run_id", "kind", "native_scope", "source_context"):
        if _GET(record, name) is not _GET(registration, name):
            _fail("RECORD_CHANGED")
    if record.group_token is not registration.inputs[1] or record.profiles is not registration.inputs[8]:
        _fail("RECORD_CHANGED")
    if registration.first is not None:
        raise registration.first
    _profiles_original(registration)
    if registration.kind == "check_only":
        _budget_original(registration)
    elif registration.kind == "target_data_chain_unit":
        _native_budget_original(registration)
    elif registration.kind == "source_census_unit":
        _source_budget_original(registration)
    else:
        _fail("RECORD_CHANGED")
    if guard_check:
        _guard_original(registration)


def _phase_original(registration, phase):
    registration.phase = phase
    registration.record.phase = phase
    if _source_driver_selected_original(registration) and registration.source_context is not None:
        _SOURCE_CONTEXT_PHASE.__set__(registration.source_context, phase)


def _check_budget_original(registration, *, callback=False):
    registration.budget.check(callback=callback)
    registration.budget_last = registration.budget._last_tick


def _callback_original(registration, *, BaseException=BaseException):
    remember = _FIRST_ORIGINAL
    registration_callback, record_callback = _REGISTRATION_CALLBACK, _RECORD_CALLBACK
    original_record = registration.record
    native_callback_finally = None
    try:
        _integrity_original()
        if _source_driver_selected_original(registration):
            native_callback_finally = source_native._begin_callback_original(registration)
        elif registration.kind == "target_data_chain_unit":
            native_callback_finally = native._begin_callback_original(registration)
        # Budget sets its in-callback flag before calling this fixed wrapper.
        registration.callback_active = registration.record.callback_active = True
        _record_original(registration)
        if not _callback_phase_original(registration) or registration.window is not None:
            _fail("PHASE_INVALID")
        callback = registration.inputs[6]
        if callback is not None:
            callback()
        _integrity_original()
        _record_original(registration)
    except BaseException as exc:
        raise remember(registration, exc)
    finally:
        registration_callback.__set__(registration, False)
        record_callback.__set__(original_record, False)
        if native_callback_finally is not None:
            native_callback_finally()


def _verify_selected_guard_original(record, *, dict=dict):
    registration = dict.get(_ORIGINAL_CONTAINERS[0], id(record.token))
    if _TYPE(registration) is not _OperationRegistration or registration.record is not record:
        _fail("GUARD_REQUIRED")
    _integrity_original()
    _record_original(registration)
    if not _guard_phase_original(registration) or registration.callback_active:
        _fail("PHASE_INVALID")
    windows = _ORIGINAL_CONTAINERS[3]
    key = registration.owner_key
    window_slot = _REGISTRATION_WINDOW
    registration_type = _OperationRegistration
    if registration.window is not None or key in windows:
        _fail("REENTRANT")
    window = _GuardCall(registration)
    registration.window = window
    windows[key] = window
    try:
        for profile in registration.inputs[8]:
            if lifecycle.require_guard(profile, exclusive=True) is not registration.guard:
                _fail("GUARD_CHANGED")
    finally:
        if dict.get(windows, key) is window:
            dict.pop(windows, key)
        if window_slot.__get__(registration, registration_type) is window:
            window_slot.__set__(registration, None)


def _token_registration_original(token):
    _integrity_original()
    owner = _owner_original()
    active = _active_original(owner)
    if active is not None and active.callback_active:
        raise _remember_original(active, AuthorityReplicaOperationError("REENTRANT"))
    _registries_original()
    if _TYPE(token) is not _OperationToken:
        _fail("GUARD_REQUIRED")
    registration = dict.get(_LIVE, id(token))
    if registration is None:
        _fail("CLOSED")
    if _TYPE(registration) is not _OperationRegistration or registration.token is not token:
        _fail("REGISTRY_CHANGED")
    if not _same_owner(registration.owner, owner):
        _fail("OWNER_MISMATCH")
    return registration


def _checkpoint_original(token, *, BaseException=BaseException):
    registration = _token_registration_original(token)
    remember = _FIRST_ORIGINAL
    try:
        _record_original(registration)
        if registration.phase != "ready" or registration.checkpoint_count != 0:
            _fail("PHASE_INVALID")
        _phase_original(registration, "checking")
        _check_budget_original(registration)
        _verify_selected_guard_original(registration.record)
        _integrity_original()
        _record_original(registration)
        _check_budget_original(registration, callback=True)
        _integrity_original()
        _record_original(registration)
        _verify_selected_guard_original(registration.record)
        _integrity_original()
        _record_original(registration)
        _check_budget_original(registration)
        registration.checkpoint_count = registration.record.checkpoint_count = 1
        _phase_original(registration, "checked")
    except BaseException as exc:
        raise remember(registration, exc)


def _closed_collected(reference, *, key):
    closed = _ORIGINAL_CONTAINERS[2]
    with _ORIGINAL_LOCK:
        current = dict.get(closed, key)
        if (_TYPE(current) is _ClosedCheck
                and _CLOSED_FIELDS.__get__(current, _ClosedCheck)[0] is reference):
            dict.pop(closed, key)


def _closed_original(registration, counters):
    # All strong values are immutable primitives. No live record, callback,
    # exception, ledger, Task, guard or source selector enters closed history.
    key = id(registration.token)
    closed, lock, closed_type, fields_slot = _ORIGINAL_CONTAINERS[2], _ORIGINAL_LOCK, _ClosedCheck, _CLOSED_FIELDS
    kind_of, dictionary = type, dict
    def collected(reference, *, key=key):
        with lock:
            current = dictionary.get(closed, key)
            if (kind_of(current) is closed_type
                    and fields_slot.__get__(current, closed_type)[0] is reference):
                dictionary.pop(closed, key)
    owner, inputs = registration.owner, registration.inputs
    payload = (inputs[4], registration.run_id, inputs[3], inputs[7], inputs[9],
               None if inputs[11] is None else inputs[11][0], tuple(counters.items()))
    return _ClosedCheck((_WEAK(registration.token, collected), owner[0], owner[1],
            None if owner[2] is None else _WEAK(owner[2]), _WEAK(registration.guard),
            _WEAK(inputs[1]), payload))


def _operation_scope_locked(owner, inputs, *, BaseException=BaseException, dict=dict):
    """Fixed-driver private lifetime; never a public caller body interface."""
    token = _OperationToken(_TAG)
    record = _OperationRecord(token, owner, inputs)
    registration = _OperationRegistration(record)
    live, owners, closed, windows, _debts = _ORIGINAL_CONTAINERS
    lock, key, owner_key = _ORIGINAL_LOCK, id(token), registration.owner_key
    remember, record_phase = _FIRST_ORIGINAL, _RECORD_PHASE
    first_slot, phase_slot, window_slot = _REGISTRATION_FIRST, _REGISTRATION_PHASE, _REGISTRATION_WINDOW
    retire_first, registration_type = _FIRST_RETIRE, _OperationRegistration
    with lock:
        _unknown_original()
        if owner_key in owners:
            _fail("REENTRANT")
        live[key], owners[owner_key] = registration, registration
    success, detached = False, None
    try:
        def check_cancelled():
            _callback_original(registration)
        registration.callback_wrapper = check_cancelled
        registration.callback_pin = _function_pin(check_cancelled)
        budget = _BUDGET(inputs[7], check_cancelled)
        # No intervening guard, callback, UUID or other work before attachment.
        registration.budget = record.budget = budget
        registration.budget_dict = _dictionary(budget)
        registration.budget_fixed = tuple((k, v) for k, v in registration.budget_dict.items()
            if k not in ("_first", "_in_callback", "_last_tick"))
        registration.budget_anchor = (budget, registration.budget_dict, registration.budget_fixed,
                                      check_cancelled, registration.callback_pin)
        registration.budget_last = budget._last_tick
        registration.run_id = record.run_id = str(_UUID4())
        _phase_original(registration, "opening")
        _check_budget_original(registration)
        registration.guard = record.guard = group.guard_original(inputs[1])
        _integrity_original()
        _guard_original(registration)
        guard_dictionary = _dictionary(registration.guard)
        registration.guard_fields = (guard_dictionary, tuple(guard_dictionary.items()))
        _verify_selected_guard_original(record)
        _integrity_original()
        _record_original(registration)
        _check_budget_original(registration)
        _phase_original(registration, "ready")
        yield token
        _integrity_original()
        _record_original(registration)
        if registration.phase != "checked" or registration.checkpoint_count != 1:
            _fail("CHECK_INCOMPLETE")
        _phase_original(registration, "closing")
        _check_budget_original(registration)
        _verify_selected_guard_original(record)
        _integrity_original()
        _record_original(registration)
        budget.assert_settled()
        counters = budget.snapshot()
        registration.budget_last = budget._last_tick
        _check_budget_original(registration)
        _record_original(registration)
        detached = _closed_original(registration, counters)
        success = True
    except BaseException as exc:
        remember(registration, exc)
    finally:
        # These exact original containers and identities are retained locally;
        # module alias/record mutations cannot strand or erase another entry.
        with lock:
            try:
                if first_slot.__get__(registration, registration_type) is None and success:
                    closed[key] = detached
                    phase_slot.__set__(registration, "closed")
                    record_phase.__set__(record, "closed")
                else:
                    phase_slot.__set__(registration, "failed_cleanup")
                    record_phase.__set__(record, "failed_cleanup")
            finally:
                try:
                    if dict.get(windows, owner_key) is window_slot.__get__(registration, registration_type):
                        dict.pop(windows, owner_key, None)
                    window_slot.__set__(registration, None)
                finally:
                    try:
                        if dict.get(live, key) is registration:
                            dict.pop(live, key)
                    finally:
                        if dict.get(owners, owner_key) is registration:
                            dict.pop(owners, owner_key)
                        if first_slot.__get__(registration, registration_type) is not None:
                            phase_slot.__set__(registration, "retired_failed")
                            record_phase.__set__(record, "retired_failed")
    # Return the original exception as a private terminal value. Raising a
    # callback's StopIteration inside a generator would transform its identity
    # under PEP 479. Only the fixed non-generator driver delivers this value.
    return retire_first(registration)


def _consume_check_closed_original(token):
    _integrity_original()
    owner = _owner_original()
    routing.require_no_reentry(owner)
    _unknown_original()
    if _TYPE(token) is not _OperationToken:
        _fail("GUARD_REQUIRED")
    key = id(token)
    with _ORIGINAL_LOCK:
        closed = dict.get(_CLOSED, key)
        if _TYPE(closed) is not _ClosedCheck:
            _fail("CLOSED")
        history = closed.fields
        if (_TYPE(closed.anchor) is not tuple or len(closed.anchor) != 1
                or history is not closed.anchor[0] or _TYPE(history) is not tuple or len(history) != 7
                or _TYPE(history[0]) is not _WEAK or _TYPE(history[1]) is not int
                or _TYPE(history[2]) is not int or history[3] is not None and _TYPE(history[3]) is not _WEAK
                or _TYPE(history[4]) is not _WEAK or _TYPE(history[5]) is not _WEAK
                or _TYPE(closed.consumed) is not bool
                or closed.consumed is not (closed.consumed_marker is _CONSUMED)
                or closed.consumed_marker is not None and closed.consumed_marker is not _CONSUMED):
            _fail("RECORD_CHANGED")
        if history[0]() is not token:
            _fail("CLOSED")
        token_ref, pid, thread, task_ref, guard_ref, group_ref, payload = history
        if (pid != owner[0] or thread != owner[1]
                or (owner[2] is not None if task_ref is None else task_ref() is not owner[2] or task_ref() is None)):
            _fail("OWNER_MISMATCH")
        if closed.consumed:
            _fail("CONSUMED")
        guard, group_token = guard_ref(), group_ref()
        if guard is None or group_token is None or group.guard_original(group_token) is not guard or _CURRENT.get() is not guard:
            _fail("GUARD_CHANGED")
        closed.consumed, closed.consumed_marker = True, _CONSUMED
    operation_id, run_id, mode, budget_mode, paths, source_kind, counters = payload
    return {"format_version": 1, "kind": "replica_operation_check", "operation_id": operation_id,
        "run_id": run_id, "mode": mode, "budget_mode": budget_mode,
        "target_profile_paths": list(paths[0]), "required_profile_paths": [list(row) for row in paths],
        "source_kind": source_kind, "checkpoint_completed": True, "operation_context_returned": True,
        "original_registry_retired": True, "counters": dict(counters), "rp1_owned_native_resources": 0,
        "source_observed": False, "source_native_closure": "not_established",
        "target_native_closure": "not_established", "outer_lifecycle_exited": False,
        "copy_performed": False, "data_restored": False, "write_authority": False, "sender_authority": False}


def check_operation_locked(*args, **kwargs):
    """Check one exact planned operation under the caller's actual G1 token."""
    _integrity_original()
    exception_type, stop_type, advance = BaseException, StopIteration, next
    owner = _owner_original()
    routing.require_no_reentry(owner)
    _unknown_original()
    inputs = _inputs_original(_bind_original(args, kwargs))
    scope = _operation_scope_locked(owner, inputs)
    try:
        try:
            token = advance(scope)
        except stop_type as stopped:
            if stopped.value is not None:
                raise stopped.value
            _fail("CHECK_INCOMPLETE")
        try:
            _checkpoint_original(token)
        except exception_type as exc:
            # Drive the original generator directly. An altered contextlib
            # __exit__ must never intercept retirement of the original scope.
            try:
                scope.throw(exc)
            except stop_type as stopped:
                if stopped.value is not None:
                    raise stopped.value
                raise exc
            else:
                _fail("CHECK_INCOMPLETE")
        else:
            try:
                advance(scope)
            except stop_type as stopped:
                if stopped.value is not None:
                    raise stopped.value
            else:
                _fail("CHECK_INCOMPLETE")
    finally:
        scope.close()
    return _consume_check_closed_original(token)



# The checker budget_fixed selection and _budget_original body above are
# preserved verbatim.  This separate exact native kind is not a caller option.
_NATIVE_MUTABLE_BUDGET = ("_first", "_in_callback", "_last_tick", "_terminal",
    "_pending_resources", "_current_resources", "_peak_resources", "_uncertain_resources")


class _ClosedDataChain:
    __slots__ = ("fields", "anchor", "consumed", "consumed_marker")

    def __init__(self, fields):
        self.fields, self.anchor = fields, (fields,)
        self.consumed, self.consumed_marker = False, None



def _make_data_history_original(*, type=type, dict=dict, id=id, len=len, tuple=tuple):
    """Independent weak immutable history, separate from inspectable mirrors."""
    histories = {}
    closed, lock, history_type = _ORIGINAL_CONTAINERS[2], _ORIGINAL_LOCK, _ClosedDataChain
    fields_slot = _CLASS_GET(history_type, "__dict__")["fields"]
    anchor_slot = _CLASS_GET(history_type, "__dict__")["anchor"]
    consumed_slot = _CLASS_GET(history_type, "__dict__")["consumed"]
    marker_slot = _CLASS_GET(history_type, "__dict__")["consumed_marker"]
    weak, consumed, error_type = _WEAK, _CONSUMED, AuthorityReplicaOperationError

    def make(registration, payload):
        _integrity_original()
        _record_original(registration)
        if (registration.kind != "target_data_chain_unit" or registration.callback_active
                or registration.phase != "operation_retiring" or type(payload) is not tuple or len(payload) != 12):
            raise error_type("PHASE_INVALID")
        key, owner, token = id(registration.token), registration.owner, registration.token
        def collected(reference, *, key=key):
            with lock:
                original = dict.get(histories, key)
                if original is not None and original[1][0] is reference:
                    if dict.get(closed, key) is original[0]:
                        dict.pop(closed, key)
                    dict.pop(histories, key)
        fields = (weak(token, collected), owner[0], owner[1],
            None if owner[2] is None else weak(owner[2]), weak(registration.guard),
            weak(registration.inputs[1]), payload)
        history = history_type(fields)
        histories[key] = (history, fields, False)
        return history

    def check(token, history):
        original = dict.get(histories, id(token))
        if original is None or original[0] is not history or type(history) is not history_type:
            raise error_type("CLOSED")
        fields = fields_slot.__get__(history, history_type)
        anchor = anchor_slot.__get__(history, history_type)
        if (fields is not original[1] or type(anchor) is not tuple or len(anchor) != 1 or anchor[0] is not fields
                or consumed_slot.__get__(history, history_type) is not original[2]
                or marker_slot.__get__(history, history_type) is not (consumed if original[2] else None)):
            raise error_type("RECORD_CHANGED")
        return original

    def consume(token, history):
        original = check(token, history)
        if original[2]:
            raise error_type("CONSUMED")
        consumed_slot.__set__(history, True)
        marker_slot.__set__(history, consumed)
        histories[id(token)] = (history, original[1], True)
    return make, check, consume


def _make_driver_anchor_original(*, type=type, dict=dict, id=id, tuple=tuple):
    anchors, original_registrations, native_selections = {}, {}, {}
    registration_type, record_type = _OperationRegistration, _OperationRecord
    registration_slots = _CLASS_GET(registration_type, "__dict__")
    record_slots = _CLASS_GET(record_type, "__dict__")
    kind, scope = registration_slots["kind"], registration_slots["native_scope"]
    record_kind, record_scope = record_slots["kind"], record_slots["native_scope"]
    record_slot = registration_slots["record"]
    callback_slot, phase_slot = registration_slots["callback_active"], registration_slots["phase"]
    error_type = AuthorityReplicaOperationError
    fixed_kind = "target_data_chain_unit"

    def admit_effect(registration, phase):
        # Consult the same original native callback latch BEFORE interpreting
        # inspectable registration/record/budget mirrors.  No callable effect
        # factory survives bootstrap to create another ownership responsibility.
        _integrity_original()
        native._require_no_callback_original()
        if type(registration) is not registration_type:
            raise error_type("GUARD_REQUIRED")
        selected = dict.get(native_selections, id(registration))
        if selected is None or selected[0] is not registration:
            # LIVE/owner/token and caller mirrors do not choose the driver.
            # Only the complete original DATA driver below writes this proof,
            # after its original owner-conflict gate and before any callback.
            raise error_type("GUARD_REQUIRED")
        if not _same_owner(selected[3], _owner_original()):
            raise error_type("OWNER_MISMATCH")
        if (record_slot.__get__(registration, registration_type) is not selected[1]
                or registration.token is not selected[2] or registration.owner is not selected[3]
                or registration.owner_key is not selected[4] or registration.inputs is not selected[5]):
            raise error_type("RECORD_CHANGED")
        original = dict.get(original_registrations, id(registration))
        if original is not None:
            # Actual enrollment ownership precedes caller mirror parsing.
            # Foreign threads/tasks never remember an error into the parent.
            if original[0] is not registration:
                raise error_type("REGISTRY_CHANGED")
            if not _same_owner(original[3], _owner_original()):
                raise error_type("OWNER_MISMATCH")
            if (record_slot.__get__(registration, registration_type) is not original[1]
                    or registration.token is not original[2] or registration.owner is not original[3]
                    or registration.owner_key is not original[4] or registration.inputs is not original[5]):
                raise error_type("RECORD_CHANGED")
        _registries_original()
        _registration_anchor_original(registration)
        record = record_slot.__get__(registration, registration_type)
        live, owners = _ORIGINAL_CONTAINERS[:2]
        if (dict.get(live, id(registration.token)) is not registration
                or dict.get(owners, registration.owner_key) is not registration
                or _token_registration_original(registration.token) is not registration):
            raise error_type("REGISTRY_CHANGED")
        if (callback_slot.__get__(registration, registration_type) is not False
                or record.callback_active is not False
                or phase_slot.__get__(registration, registration_type) != phase
                or record.phase is not phase_slot.__get__(registration, registration_type)
                or scope.__get__(registration, registration_type) is not None
                or record_scope.__get__(record, record_type) is not None
                or record.token is not registration.token or record.owner is not registration.owner
                or record.owner_key is not registration.owner_key or record.inputs is not registration.inputs
                or record.budget is not registration.budget or registration.window is not None):
            raise error_type("PHASE_INVALID")
        if phase == "registering":
            if (registration.budget is not None or registration.budget_dict is not None
                    or registration.budget_anchor is not None or _FIRST_PEEK(registration) is not None
                    or registration.first is not None or record.first is not None):
                raise error_type("PHASE_INVALID")
        else:
            _record_original(registration)

    def enroll(registration, chain):
        admit_effect(registration, "registering")
        if (chain is not dict.get(native_selections, id(registration))[6]
                or type(chain) is not tuple or not 1 <= len(chain) <= 129
                or kind.__get__(registration, registration_type) != "check_only"
                or record_kind.__get__(record_slot.__get__(registration, registration_type), record_type) != "check_only"):
            raise error_type("PHASE_INVALID")
        if id(registration) in anchors:
            raise error_type("REENTRANT")
        record = record_slot.__get__(registration, registration_type)
        original_registrations[id(registration)] = (registration, record, registration.token,
            registration.owner, registration.owner_key, registration.inputs)
        anchors[id(registration)] = (registration, record, chain, None)
        kind.__set__(registration, fixed_kind)
        record_kind.__set__(record, fixed_kind)

    def attach(registration, native_scope):
        admit_effect(registration, "native_ready")
        value = dict.get(anchors, id(registration))
        if value is None or value[0] is not registration or value[3] is not None:
            raise error_type("RECORD_CHANGED")
        # This pure original-registry validator returns None, never an
        # authority dictionary; an arbitrary caller-created S is not adopted.
        native._validate_new_scope_original(registration, native_scope, value[2])
        anchors[id(registration)] = (*value[:3], native_scope)
        scope.__set__(registration, native_scope)
        record_scope.__set__(value[1], native_scope)

    def check(registration):
        value = dict.get(anchors, id(registration))
        actual_kind = kind.__get__(registration, registration_type)
        actual_scope = scope.__get__(registration, registration_type)
        if value is None:
            if actual_kind != "check_only" or type(actual_kind) is not str or actual_scope is not None:
                raise error_type("RECORD_CHANGED")
            return None
        original = dict.get(original_registrations, id(registration))
        if (original is None or original[0] is not registration or original[1] is not value[1]
                or registration.token is not original[2] or registration.owner is not original[3]
                or registration.owner_key is not original[4] or registration.inputs is not original[5]):
            raise error_type("RECORD_CHANGED")
        if (value[0] is not registration or actual_kind is not fixed_kind or actual_scope is not value[3]
                or record_slot.__get__(registration, registration_type) is not value[1]
                or record_kind.__get__(value[1], record_type) is not fixed_kind
                or record_scope.__get__(value[1], record_type) is not value[3]):
            raise error_type("RECORD_CHANGED")
        return value

    def retire(registration):
        dict.pop(anchors, id(registration), None)
        dict.pop(original_registrations, id(registration), None)
        dict.pop(native_selections, id(registration), None)
    def exposed_retire(registration):
        admit_effect(registration, "registering")
        value = check(registration)
        if value is None or value[3] is not None:
            raise error_type("PHASE_INVALID")
        return retire(registration)
    def trusted_retirement(registration):
        admit_effect(registration, "registering")
        if check(registration) is not None:
            raise error_type("PHASE_INVALID")
        def retire_original_driver():
            return retire(registration)
        return retire_original_driver
    def _operation_data_chain_locked(owner, inputs, chain):
        """Complete non-generator unit; BaseException/StopIteration retain identity."""
        _integrity_original()
        if type(owner) is not tuple or len(owner) != 3 or not _same_owner(owner, _owner_original()):
            _fail("OWNER_MISMATCH")
        token = _OperationToken(_TAG)
        record, registration = _OperationRecord(token, owner, inputs), None
        registration = _OperationRegistration(record)
        enroll = _enroll_native_driver_original
        retire_driver = None
        _driver_anchor_original(registration)
        live, owners, closed, windows, debts = _ORIGINAL_CONTAINERS
        lock, key, owner_key = _ORIGINAL_LOCK, id(token), registration.owner_key
        remember, retire_first, first = _FIRST_ORIGINAL, _FIRST_RETIRE, _FIRST_PEEK
        phase_slot, record_phase, window_slot = _REGISTRATION_PHASE, _RECORD_PHASE, _REGISTRATION_WINDOW
        registration_type = _OperationRegistration
        success, detached, native_cleanup, native_retirement = False, None, None, None
        try:
            with lock:
                _unknown_original()
                if owner_key in owners:
                    _fail("REENTRANT")
                live[key], owners[owner_key] = registration, registration
                # This is an unexported original driver-selection fact, produced
                # only in this complete unit after the original busy-owner gate.
                # Checker registration/mirrors cannot manufacture DATA eligibility.
                native_selections[id(registration)] = (registration, record, token,
                    owner, owner_key, inputs, chain)
                # Capture the unexported original retirement before enrollment or
                # any budget/user callback. Partial failures still cross finally.
                retire_driver = _trusted_driver_retirement_original(registration)
                enroll(registration, chain)
            def check_cancelled():
                _callback_original(registration)
            registration.callback_wrapper = check_cancelled
            registration.callback_pin = _function_pin(check_cancelled)
            budget = _BUDGET(inputs[7], check_cancelled)
            registration.budget = record.budget = budget
            registration.budget_dict = _dictionary(budget)
            registration.budget_fixed = tuple((name, value) for name, value in registration.budget_dict.items()
                if name not in _NATIVE_MUTABLE_BUDGET)
            registration.budget_anchor = (budget, registration.budget_dict, registration.budget_fixed,
                check_cancelled, registration.callback_pin)
            registration.budget_last = budget._last_tick
            registration.run_id = record.run_id = str(_UUID4())
            _phase_original(registration, "registered")
            # ABI support is a pure gate AFTER clock/registration, BEFORE this
            # unit's first borrowed guard or backend object/metadata primitive.
            native._require_supported_original()
            _phase_original(registration, "guard_checking")
            _check_budget_original(registration)
            registration.guard = record.guard = group.guard_original(inputs[1])
            _integrity_original()
            _guard_original(registration)
            guard_dictionary = _dictionary(registration.guard)
            registration.guard_fields = (guard_dictionary, tuple(guard_dictionary.items()))
            _verify_selected_guard_original(record)
            _integrity_original()
            _record_original(registration)
            _check_budget_original(registration)
            _phase_original(registration, "native_ready")
            scope_key = _open_native_scope_original(token)
            state = native._scope_state_original(scope_key)
            native_cleanup, native_retirement = native._trusted_callbacks_original(scope_key)
            native._run_target_data_chain_original(token, scope_key)
            _finish_native_scope_original(token, scope_key)
            _phase_original(registration, "operation_retiring")
            _verify_selected_guard_original(record)
            _record_original(registration)
            _check_budget_original(registration)
            detached = _closed_data_chain_original(registration, state)
            success = True
        except BaseException as exc:
            remember(registration, exc)
        finally:
            # Native cleanup/debt transfer completes before any original live pop.
            if native_cleanup is not None:
                try:
                    native_cleanup()
                except BaseException as exc:
                    remember(registration, exc)
            if native_retirement is not None:
                try:
                    native_retirement(success and first(registration) is None)
                except BaseException as exc:
                    remember(registration, exc)
            with lock:
                try:
                    if success and first(registration) is None:
                        closed[key] = detached
                        phase_slot.__set__(registration, "closed")
                        record_phase.__set__(record, "closed")
                    else:
                        phase_slot.__set__(registration, "native_failed_cleanup")
                        record_phase.__set__(record, "native_failed_cleanup")
                finally:
                    try:
                        if dict.get(windows, owner_key) is window_slot.__get__(registration, registration_type):
                            dict.pop(windows, owner_key, None)
                        window_slot.__set__(registration, None)
                    finally:
                        try:
                            if dict.get(live, key) is registration:
                                dict.pop(live, key)
                        finally:
                            if dict.get(owners, owner_key) is registration:
                                dict.pop(owners, owner_key)
                            if first(registration) is not None:
                                phase_slot.__set__(registration, "retired_failed")
                                record_phase.__set__(record, "retired_failed")
                            if retire_driver is not None:
                                retire_driver()
                            else:
                                # Selection allocation/admission can fail before
                                # returning a retire closure; still clear only this
                                # fresh original registration's partial facts.
                                retire(registration)
        error = retire_first(registration)
        if error is not None:
            raise error
        return token

    return enroll, attach, check, exposed_retire, trusted_retirement, _operation_data_chain_locked


def _guard_phase_original(registration):
    if registration.kind == "check_only":
        return registration.phase in ("opening", "checking", "closing")
    if registration.kind == "target_data_chain_unit":
        return registration.phase in ("guard_checking", "native_checkpoint", "data_chain_finalizing",
            "native_unit_closed", "operation_retiring")
    if registration.kind == "source_census_unit":
        return registration.phase in ("guard_checking", "source_checkpoint",
            "source_finalizing", "source_unit_closed", "operation_retiring")
    return False


def _callback_phase_original(registration):
    if registration.kind == "check_only":
        return registration.phase == "checking"
    if registration.kind == "source_census_unit":
        return (registration.phase in ("source_native", "source_checkpoint")
            and source_native._callback_allowed_original(registration))
    if registration.kind != "target_data_chain_unit" or registration.phase != "native_checkpoint":
        return False
    scope = registration.native_scope
    if type(scope) is not native._NativeScope:
        return False
    return native._callback_allowed_original(registration)


def _native_budget_original(registration):
    budget, dictionary = registration.budget, registration.budget_dict
    if type(budget) is not _BUDGET or _dictionary(budget) is not dictionary:
        _fail("RECORD_CHANGED")
    anchor, fixed = registration.budget_anchor, registration.budget_fixed
    if (type(anchor) is not tuple or len(anchor) != 5 or anchor[0] is not budget
            or anchor[1] is not dictionary or anchor[2] is not fixed
            or anchor[3] is not registration.callback_wrapper or anchor[4] is not registration.callback_pin
            or len(dictionary) != len(fixed) + len(_NATIVE_MUTABLE_BUDGET)):
        _fail("RECORD_CHANGED")
    for name, original in fixed:
        if dict.get(dictionary, name, _MISSING) is not original:
            _fail("RECORD_CHANGED")
    for name in ("_terminal", "_pending_resources", "_current_resources", "_peak_resources", "_uncertain_resources"):
        value = dictionary[name]
        if type(value) is not int or value < 0:
            _fail("RECORD_CHANGED")
    if dictionary["_terminal"] not in (0, 1):
        _fail("PHASE_INVALID")
    for name in _BUDGET_ZERO:
        if name not in ("_terminal", "_pending_resources", "_current_resources", "_peak_resources", "_uncertain_resources"):
            if type(dictionary[name]) is not int or dictionary[name] != 0:
                _fail("UNSETTLED")
    if (dictionary["_active"] is not None
            or any(dictionary[k] is not False for k in ("_failure_started", "_failure_write_started", "_receipt_started"))
            or dictionary["_in_callback"] is not registration.callback_active
            or dictionary["_first"] is not _FIRST_PEEK(registration)):
        _fail("RECORD_CHANGED")
    tick = dictionary["_last_tick"]
    if (type(tick) is not int or type(registration.budget_last) is not int
            or registration.budget_last < 0 or tick < registration.budget_last
            or dict(dictionary["limits"]) != policy.runtime_limits(registration.inputs[7])):
        _fail("RECORD_CHANGED")
    _check_function(registration.callback_pin)
    if registration.native_scope is None:
        if any(dictionary[name] for name in ("_pending_resources", "_current_resources", "_peak_resources", "_uncertain_resources")):
            _fail("UNSETTLED")
        if dictionary["_resource_pending"] or dictionary["_resource_leases"] or dictionary["_terminal"]:
            _fail("UNSETTLED")
    else:
        native._validate_registration_original(registration)


def _open_native_scope_original(operation_token):
    registration = _token_registration_original(operation_token)
    _record_original(registration)
    anchor = _driver_anchor_original(registration)
    if (anchor is None or registration.phase != "native_ready" or registration.native_scope is not None
            or registration.callback_active or registration.window is not None):
        _fail("PHASE_INVALID")
    chain = anchor[2]
    if len(chain) + 1 > registration.budget_dict["limits"]["max_resources"]:
        raise _FIRST_ORIGINAL(registration, accounting.Error("RESOURCE_LIMIT"))
    scope = native._new_scope_original(registration, chain)
    _attach_native_scope_original(registration, scope)
    _record_original(registration)
    return scope.key


def _native_checkpoint_original(operation_token, stage):
    registration = _token_registration_original(operation_token)
    remember = _FIRST_ORIGINAL
    try:
        _record_original(registration)
        if registration.kind != "target_data_chain_unit" or registration.native_scope is None:
            _fail("PHASE_INVALID")
        state = native._scope_state_original(registration.native_scope.key)
        if (type(stage) is not str or stage not in ("before_acquire", "component_finalized", "before_final_validation")
                or state["pending_identities"] or registration.budget_dict["_terminal"] != 0
                or stage == "before_acquire" and (state["resource_acquires"] or registration.checkpoint_count)
                or stage == "component_finalized" and not 1 <= state["next_ordinal"] <= len(state["chain"])
                or stage == "before_final_validation" and state["next_ordinal"] != len(state["chain"])):
            _fail("PHASE_INVALID")
        native._update_scope_original(state, phase=stage)
        _phase_original(registration, "native_checkpoint")
        _check_budget_original(registration)
        _verify_selected_guard_original(registration.record)
        _integrity_original()
        _record_original(registration)
        _check_budget_original(registration, callback=True)
        _integrity_original()
        _record_original(registration)
        _verify_selected_guard_original(registration.record)
        _check_budget_original(registration)
        registration.checkpoint_count = registration.record.checkpoint_count = registration.checkpoint_count + 1
    except BaseException as exc:
        raise remember(registration, exc)


def _retain_native_debt_original(operation_token, scope_key, resource_key):
    # No caller DTO/count/exception can create native debt. A callback is not
    # permitted to run this producer; the native captured failure kernel uses
    # the same authoritative state when ordinary validation cannot proceed.
    _integrity_original()
    owner = _owner_original()
    state = native._scope_state_original(scope_key)
    if not _same_owner(state["owner"], owner):
        _fail("OWNER_MISMATCH")
    registration = state["registration"]
    if (state["operation_token"] is not operation_token or registration.callback_active
            or registration.native_scope is not state["object"]):
        _fail("PHASE_INVALID")
    item = native._holder_state_original(resource_key)
    if item["scope_state"] is not state:
        _fail("GUARD_REQUIRED")
    native._retain_state_original(item)


def _finish_native_scope_original(operation_token, scope_key):
    registration = _token_registration_original(operation_token)
    _record_original(registration)
    state = native._scope_state_original(scope_key)
    native._validate_scope_original(state)
    if (state["registration"] is not registration or registration.kind != "target_data_chain_unit"
            or registration.phase != "native_unit_closed" or state["operation_token"] is not operation_token
            or registration.native_scope is not state["object"]):
        _fail("PHASE_INVALID")
    n = len(state["chain"])
    if (state["next_ordinal"] != n or registration.checkpoint_count != n + 2
            or state["pending_identities"] or state["current_resources"] or state["unknown_resources"]
            or state["resource_acquires"] != 3 * n or state["resource_normal_closes"] != 3 * n
            or len(state["events"]) != 6 * n or state["peak_owned_resources"] != n + 1
            or any(not item["normal_closed"] or not item["lease_released"] or item["debt_submitted"]
                for item in state["holder_states"])):
        _fail("UNSETTLED")
    if any(event["sequence"] != index + 1 for index, event in enumerate(state["events"])):
        _fail("RECORD_CHANGED")
    for item in state["holder_states"]:
        matches = [event for event in state["events"] if event["resource_id"] == item["resource_id"]]
        if len(matches) != 2 or tuple(event["action"] for event in matches) != ("acquired", "normal_closed"):
            _fail("UNSETTLED")
    registration.budget.assert_settled()
    _check_budget_original(registration)
    end = registration.budget.snapshot()
    registration.budget_last = registration.budget_dict["_last_tick"]
    _check_budget_original(registration)
    _record_original(registration)
    native._update_scope_original(state, end=end, normal_closed=True, phase="native_unit_closed")


def _closed_data_chain_original(registration, state):
    _integrity_original()
    if type(registration) is not _OperationRegistration:
        _fail("GUARD_REQUIRED")
    _record_original(registration)
    if (registration.callback_active or registration.kind != "target_data_chain_unit"
            or registration.phase != "operation_retiring" or type(state) is not dict
            or native._scope_state_original(registration.native_scope.key) is not state
            or not state["normal_closed"]):
        _fail("PHASE_INVALID")
    payload = (registration.inputs[4], registration.run_id, registration.inputs[3], state["scope_id"],
        len(state["chain"]), state["resource_acquires"], state["resource_normal_closes"],
        state["peak_owned_resources"], native._events_hash_original(state["events"]),
        tuple(state["begin"].items()), tuple(state["end"].items()), tuple(state["attributable"].items()))
    return _make_closed_data_history_original(registration, payload)




def _consume_data_chain_unit_closed_original(operation_token):
    _integrity_original()
    owner = _owner_original()
    routing.require_no_reentry(owner)
    _unknown_original()
    if type(operation_token) is not _OperationToken:
        _fail("GUARD_REQUIRED")
    with _ORIGINAL_LOCK:
        history = dict.get(_CLOSED, id(operation_token))
        if type(history) is not _ClosedDataChain:
            _fail("CLOSED")
        _check_closed_data_history_original(operation_token, history)
        fields = history.fields
        if (type(fields) is not tuple or len(fields) != 7 or type(history.anchor) is not tuple
                or len(history.anchor) != 1 or history.anchor[0] is not fields
                or type(history.consumed) is not bool or history.consumed is not (history.consumed_marker is _CONSUMED)
                or history.consumed_marker is not None and history.consumed_marker is not _CONSUMED
                or type(fields[0]) is not _WEAK or fields[0]() is not operation_token
                or type(fields[1]) is not int or type(fields[2]) is not int
                or fields[3] is not None and type(fields[3]) is not _WEAK
                or type(fields[4]) is not _WEAK or type(fields[5]) is not _WEAK):
            _fail("RECORD_CHANGED")
        if (fields[1] != owner[0] or fields[2] != owner[1]
                or (owner[2] is not None if fields[3] is None else fields[3]() is not owner[2] or fields[3]() is None)):
            _fail("OWNER_MISMATCH")
        if history.consumed:
            _fail("CONSUMED")
        guard, group_token = fields[4](), fields[5]()
        if guard is None or group_token is None or group.guard_original(group_token) is not guard or _CURRENT.get() is not guard:
            _fail("GUARD_CHANGED")
        payload = fields[6]
        if type(payload) is not tuple or len(payload) != 12:
            _fail("RECORD_CHANGED")
        _consume_closed_data_history_original(operation_token, history)
    opid, runid, mode, scopeid, n, acquires, closes, peak, digest, begin, end, attributable = payload
    return {"format_version": 1, "kind": "replica_target_data_chain_check", "operation_id": opid,
        "run_id": runid, "mode": mode, "scope_id": scopeid, "directory_count": n,
        "resource_acquires": acquires, "resource_normal_closes": closes, "peak_owned_resources": peak,
        "resource_events_sha256": digest, "unit_begin": dict(begin), "unit_end": dict(end),
        "unit_attributable": dict(attributable), "unit_native_normal_closed": True,
        "operation_context_returned": True, "original_registry_retired": True, "outer_lifecycle_exited": False,
        "source_observed": False, "source_native_closure": "not_established", "target_native_closure": "not_established",
        "acl_validated": False, "copy_performed": False, "data_restored": False,
        "write_authority": False, "sender_authority": False}


def check_target_data_chain_locked(*args, **kwargs):
    """Check the target DATA ancestor chain under one original G1 operation."""
    _integrity_original()
    owner = _owner_original()
    routing.require_no_reentry(owner)
    _unknown_original()
    inputs = _inputs_original(_bind_original(args, kwargs))
    chain = native._plan_data_chain_original(inputs)
    token = _operation_data_chain_locked(owner, inputs, chain)
    return _consume_data_chain_unit_closed_original(token)


class _SourceUnitContext:
    """Inspectable mirror of one original source-unit enrollment, not an issuer."""
    __slots__ = ("token", "registration", "record", "owner", "inputs", "budget",
        "budget_dict", "limits", "started", "guard", "group_token", "profiles",
        "native_plan", "unit_key", "bases", "target_observation", "output_binding",
        "source_token", "source_record", "phase")


class _ClosedSourceCensus:
    __slots__ = ("fields", "anchor", "consumed", "consumed_marker")

    def __init__(self, fields):
        self.fields, self.anchor = fields, (fields,)
        self.consumed, self.consumed_marker = False, None


_SOURCE_MUTABLE_BUDGET = ("_first", "_in_callback", "_last_tick", "_terminal",
    "_pending_resources", "_current_resources", "_peak_resources", "_uncertain_resources",
    "_read_bytes", "_source_read_bytes", "_read_calls", "_source_read_calls", "_seek_calls",
    "_uncertain_reads", "_uncertain_source_reads", "_active")
_SOURCE_COUNTERS = ("_pending_resources", "_current_resources", "_peak_resources", "_uncertain_resources",
    "_read_bytes", "_source_read_bytes", "_read_calls", "_source_read_calls", "_seek_calls",
    "_uncertain_reads", "_uncertain_source_reads")
_SOURCE_CHECKPOINT_STAGES = ("bases", "target_begin", "target_observed", "output_bound",
    "source_begin", "location", "metadata", "before_body", "captured", "selection",
    "final_metadata", "before_close")


def _source_budget_original(registration):
    """Exact readonly fixed fields plus the original native event/ticket oracle.

    The old checker and DATA validators retain their zero-read bodies.  This
    gate never accepts counts solely because they are nonnegative: after the
    fixed field checks the sealed native holder/read journal must account for
    every live ticket, lease and byte.  Neither a snapshot nor this context's
    inspectable fields are an event issuer.
    """
    context = _source_driver_anchor_original(registration)
    budget, dictionary = registration.budget, registration.budget_dict
    if (type(budget) is not _BUDGET or _dictionary(budget) is not dictionary
            or context.budget is not budget or context.budget_dict is not dictionary
            or registration.native_scope is not None or registration.inputs[7] != "readonly"):
        _fail("RECORD_CHANGED")
    anchor, fixed = registration.budget_anchor, registration.budget_fixed
    if (type(anchor) is not tuple or len(anchor) != 5 or anchor[0] is not budget
            or anchor[1] is not dictionary or anchor[2] is not fixed
            or anchor[3] is not registration.callback_wrapper or anchor[4] is not registration.callback_pin
            or len(dictionary) != len(fixed) + len(_SOURCE_MUTABLE_BUDGET)):
        _fail("RECORD_CHANGED")
    for name, original in fixed:
        if dict.get(dictionary, name, _MISSING) is not original:
            _fail("RECORD_CHANGED")
    if (dictionary["limits"] is not context.limits or dictionary["_started"] is not context.started
            or dictionary["_readonly"] is not True
            or dictionary["_callback"] is not registration.callback_wrapper
            or dict(dictionary["limits"]) != policy.runtime_limits("readonly")):
        _fail("RECORD_CHANGED")
    if (any(type(dictionary[name]) is not int or dictionary[name] < 0 for name in _SOURCE_COUNTERS)
            or type(dictionary["_terminal"]) is not int or dictionary["_terminal"] not in (0, 1)
            or any(type(dictionary[name]) is not int or dictionary[name] != 0
                for name in ("_confirmed", "_write_calls", "_uncertain_writes", "_failure_remaining"))
            or any(dictionary[name] is not False
                for name in ("_failure_started", "_failure_write_started", "_receipt_started"))):
        _fail("RECORD_CHANGED")
    if (dictionary["_in_callback"] is not registration.callback_active
            or dictionary["_first"] is not _FIRST_PEEK(registration)):
        _fail("RECORD_CHANGED")
    for name in ("_resource_pending", "_resource_leases"):
        values = dictionary[name]
        # These original maps contain opaque exact-object keys.  Inspect key
        # types before a lookup/equality operation, and retain their pointers.
        if (type(values) is not dict or any(type(key) is not object for key in values)
                or any(type(count) is not int or count < 0 for count in dict.values(values))):
            _fail("RECORD_CHANGED")
    active = dictionary["_active"]
    if active is not None:
        if (type(active) is not tuple or len(active) != 4 or type(active[0]) is not object
                or type(active[1]) is not str or active[1] != "read"
                or type(active[2]) is not int or not 0 < active[2] <= context.limits["chunk_bytes"]
                or type(active[3]) is not bool):
            _fail("RECORD_CHANGED")
    tick = dictionary["_last_tick"]
    if (type(tick) is not int or type(registration.budget_last) is not int
            or registration.budget_last < 0 or tick < registration.budget_last):
        _fail("RECORD_CHANGED")
    _check_function(registration.callback_pin)
    if context.unit_key is None:
        if (active is not None or any(dictionary[name] for name in _SOURCE_COUNTERS)
                or dictionary["_terminal"] or dictionary["_resource_pending"] or dictionary["_resource_leases"]):
            _fail("UNSETTLED")
    else:
        source_native._validate_registration_original(registration)
    source_module._validate_context_original(context)


def _source_entry_registration_original(token):
    # Same-original-owner callback routing precedes token/type interpretation.
    # The independent native latch catches attempts to clear inspectable flags;
    # a foreign thread/task cannot remember into another owner's first.
    _integrity_original()
    owner = _owner_original()
    active = _active_original(owner)
    if active is not None and active.callback_active:
        raise _FIRST_ORIGINAL(active, AuthorityReplicaOperationError("REENTRANT"))
    source_native._require_no_callback_original()
    return _token_registration_original(token)


def _source_context_original(token):
    registration = _source_entry_registration_original(token)
    _record_original(registration)
    if registration.kind != "source_census_unit" or registration.callback_active:
        _fail("PHASE_INVALID")
    return _source_driver_anchor_original(registration)


def _source_scope_context_original(token):
    """Pure original CM-construction gate; actual guard checks follow in enter."""
    registration = _source_entry_registration_original(token)
    _record_original(registration, guard_check=False)
    context = _source_driver_anchor_original(registration)
    if (registration.kind != "source_census_unit" or registration.callback_active
            or registration.phase != "source_native" or context.unit_key is None
            or context.bases is None or context.target_observation is None or context.output_binding is None
            or context.source_token is not None or context.source_record is not None):
        _fail("PHASE_INVALID")
    return context


def _source_checkpoint_original(token, source_token, stage, *, BaseException=BaseException):
    registration = _source_entry_registration_original(token)
    remember = _FIRST_ORIGINAL
    try:
        _record_original(registration)
        context = _source_driver_anchor_original(registration)
        if (type(stage) is not str or stage not in _SOURCE_CHECKPOINT_STAGES
                or context.unit_key is None or registration.callback_active or registration.window is not None
                or source_token is not context.source_token or registration.budget_dict["_terminal"] != 0
                or registration.phase not in ("source_native", "source_checkpoint", "source_finalizing")):
            _fail("PHASE_INVALID")
        if (source_native._checkpoint_allowed_original(context, source_token, stage) is not True
                or source_module._checkpoint_allowed_original(context, source_token, stage) is not True):
            _fail("PHASE_INVALID")
        _phase_original(registration, "source_checkpoint")
        _check_budget_original(registration)
        _verify_selected_guard_original(registration.record)
        _integrity_original()
        _record_original(registration)
        _check_budget_original(registration, callback=True)
        _integrity_original()
        _record_original(registration)
        _verify_selected_guard_original(registration.record)
        _check_budget_original(registration)
        registration.checkpoint_count = registration.record.checkpoint_count = registration.checkpoint_count + 1
        _phase_original(registration, "source_native")
    except BaseException as exc:
        raise remember(registration, exc)


def _retain_source_unit_debt_original(token, scope_key, holder_key):
    _integrity_original()
    source_native._require_no_callback_original()
    state = source_native._unit_state_original(scope_key)
    if not _same_owner(state["owner"], _owner_original()):
        _fail("OWNER_MISMATCH")
    registration = state["registration"]
    context = _source_driver_anchor_original(registration)
    if (state["operation_token"] is not token or context.unit_key is not scope_key
            or registration.callback_active or dict.get(_LIVE, id(token)) is not registration):
        _fail("PHASE_INVALID")
    item = source_native._holder_state_original(holder_key)
    if item["unit_state"] is not state:
        _fail("GUARD_REQUIRED")
    source_native._retain_state_original(item)


def _freeze_source_value_original(value):
    # Fixed private schema copier.  It never adopts a class, weakref, object,
    # callable, node, reader or FD into the externally returned history.
    kind = type(value)
    if kind is dict:
        if any(type(key) is not str for key in value):
            _fail("RECORD_CHANGED")
        return ("dict", tuple((key, _freeze_source_value_original(child)) for key, child in dict.items(value)))
    if kind in (list, tuple):
        return ("list" if kind is list else "tuple", tuple(_freeze_source_value_original(child) for child in value))
    if value is None or kind in (str, int, bool, bytes):
        return ("value", value)
    _fail("RECORD_CHANGED")


def _thaw_source_value_original(value):
    tag, payload = value
    if tag == "dict":
        return {key: _thaw_source_value_original(child) for key, child in payload}
    if tag == "list":
        return [_thaw_source_value_original(child) for child in payload]
    if tag == "tuple":
        return tuple(_thaw_source_value_original(child) for child in payload)
    return payload


def _source_inputs_original(inputs):
    """Lower fixed driver cannot adopt a caller's fabricated operational tuple."""
    if (type(inputs) is not tuple or len(inputs) != 12 or type(inputs[3]) is not str
            or inputs[3] != "preview" or inputs[5] is not None or type(inputs[7]) is not str
            or inputs[7] != "readonly"):
        _fail("ARGUMENT_INVALID")
    original = _inputs_original(inputs[:7])
    for position in (0, 1, 2, 6):
        if inputs[position] is not original[position]:
            _fail("RECORD_CHANGED")
    if (type(inputs[8]) is not tuple or len(inputs[8]) != len(original[8])
            or any(a is not b for a, b in zip(inputs[8], original[8]))):
        _fail("RECORD_CHANGED")
    if (type(inputs[9]) is not tuple or len(inputs[9]) != len(original[9])
            or any(type(row) is not tuple or len(row) != 4 or any(type(path) is not str for path in row)
                for row in inputs[9]) or inputs[9] != original[9]):
        _fail("RECORD_CHANGED")
    if (type(inputs[10]) is not tuple or len(inputs[10]) != len(original[10])
            or any(type(row) is not tuple or len(row) != 4 for row in inputs[10])
            or any(a is not b for row, expected in zip(inputs[10], original[10]) for a, b in zip(row, expected))):
        _fail("RECORD_CHANGED")
    source = inputs[11]
    if (type(source) is not tuple or len(source) != 6
            or type(source[0]) is not str or type(source[1]) is not str
            or type(source[2]) is not int or type(source[3]) is not str
            or source[4] is not None and (type(source[4]) is not tuple or len(source[4]) != 4
                or any(type(path) is not str for path in source[4]))
            or source[5] is not None and type(source[5]) is not str or source != original[11]):
        _fail("RECORD_CHANGED")


def _make_source_driver_original(*, type=type, dict=dict, tuple=tuple, id=id,
        len=len, any=any, BaseException=BaseException, object=object):
    """Original enrollment and complete non-generator driver share one closure.

    The independent maps select only this complete driver.  Caller mutation of
    records/contexts cannot enroll a checker, replace a backend, or manufacture
    a native unit.  Rewriting this original closure root is outside the same
    original-entry model as the existing first/native cleanup kernels.
    """
    selections, anchors, histories = {}, {}, {}
    registration_type, record_type, context_type = _OperationRegistration, _OperationRecord, _SourceUnitContext
    rs, oslots, cs = (_CLASS_GET(cls, "__dict__") for cls in (registration_type, record_type, context_type))
    context_slots = tuple((name, cs[name]) for name in context_type.__slots__)
    kind_slot, record_kind = rs["kind"], oslots["kind"]
    context_slot, record_context = rs["source_context"], oslots["source_context"]
    fixed_kind, error_type = "source_census_unit", AuthorityReplicaOperationError
    closed_type, weak = _ClosedSourceCensus, _WEAK
    hs = _CLASS_GET(closed_type, "__dict__")
    fields_slot, anchor_slot, consumed_slot, marker_slot = (hs[name] for name in
        ("fields", "anchor", "consumed", "consumed_marker"))
    live, owners, closed, windows, debts = _ORIGINAL_CONTAINERS
    lock, consumed = _ORIGINAL_LOCK, _CONSUMED

    def selected(registration):
        # Select by the original map before interpreting a caller-mutated kind
        # mirror. A kind object with __eq__ cannot execute before anchor gates.
        actual = dict.get(anchors, id(registration))
        return actual is not None and actual["registration"] is registration

    def check(registration):
        actual = dict.get(anchors, id(registration))
        if actual is None or actual["registration"] is not registration:
            raise error_type("GUARD_REQUIRED")
        context = actual["context"]
        if (type(registration) is not registration_type or type(context) is not context_type
                or kind_slot.__get__(registration, registration_type) is not fixed_kind
                or record_kind.__get__(actual["record"], record_type) is not fixed_kind
                or context_slot.__get__(registration, registration_type) is not context
                or record_context.__get__(actual["record"], record_type) is not context):
            raise error_type("RECORD_CHANGED")
        for name, slot in context_slots:
            expected = registration.phase if name == "phase" else actual[name]
            if slot.__get__(context, context_type) is not expected:
                raise error_type("RECORD_CHANGED")
        for name in ("token", "record", "owner", "inputs", "budget", "budget_dict", "guard"):
            if _GET(registration, name) is not actual[name]:
                raise error_type("RECORD_CHANGED")
        if (registration.native_scope is not None or actual["record"].native_scope is not None
                or actual["record"].source_context is not context):
            raise error_type("RECORD_CHANGED")
        return context

    def enroll(registration, plan):
        _integrity_original()
        source_native._require_no_callback_original()
        chosen = dict.get(selections, id(registration))
        if (chosen is None or chosen[0] is not registration or chosen[1] is not plan
                or registration.phase != "registering" or registration.budget is not None
                or registration.source_context is not None or registration.kind != "check_only"
                or dict.get(live, id(registration.token)) is not registration
                or dict.get(owners, registration.owner_key) is not registration):
            raise error_type("PHASE_INVALID")
        if not _same_owner(registration.owner, _owner_original()):
            raise error_type("OWNER_MISMATCH")
        context = object.__new__(context_type)
        values = {"context": context, "token": registration.token, "registration": registration,
            "record": registration.record, "owner": registration.owner, "inputs": registration.inputs,
            "budget": None, "budget_dict": None, "limits": None, "started": None,
            "guard": None, "group_token": registration.inputs[1], "profiles": registration.inputs[8],
            "native_plan": plan, "unit_key": None, "bases": None, "target_observation": None,
            "output_binding": None, "source_token": None, "source_record": None}
        anchors[id(registration)] = values
        for name, slot in context_slots:
            slot.__set__(context, registration.phase if name == "phase" else values[name])
        kind_slot.__set__(registration, fixed_kind)
        record_kind.__set__(registration.record, fixed_kind)
        context_slot.__set__(registration, context)
        record_context.__set__(registration.record, context)
        return context

    def configure(registration, *, budget=False, guard=False):
        actual = dict.get(anchors, id(registration))
        if actual is None or actual["registration"] is not registration:
            raise error_type("GUARD_REQUIRED")
        context = actual["context"]
        if budget:
            if actual["budget"] is not None or registration.phase != "registering":
                raise error_type("PHASE_INVALID")
            for name, value in (("budget", registration.budget), ("budget_dict", registration.budget_dict),
                    ("limits", registration.budget_dict["limits"]), ("started", registration.budget_dict["_started"])):
                actual[name] = value
                cs[name].__set__(context, value)
        elif guard:
            if actual["guard"] is not None or registration.phase != "guard_checking":
                raise error_type("PHASE_INVALID")
            actual["guard"] = registration.guard
            cs["guard"].__set__(context, registration.guard)
        else:
            raise error_type("PHASE_INVALID")

    def attach_unit(token, unit_key):
        context = _source_context_original(token)
        registration = context.registration
        actual = dict.get(anchors, id(registration))
        if (registration.phase != "source_native_ready" or actual["unit_key"] is not None
                or registration.callback_active or registration.window is not None):
            raise error_type("PHASE_INVALID")
        source_native._validate_new_unit_original(context, unit_key)
        actual["unit_key"] = unit_key
        cs["unit_key"].__set__(context, unit_key)

    def attach_observation(token, stage, value):
        context = _source_context_original(token)
        registration = context.registration
        actual = dict.get(anchors, id(registration))
        if (type(stage) is not str or stage not in ("bases", "target", "output")
                or registration.phase != "source_native" or context.source_token is not None):
            raise error_type("PHASE_INVALID")
        name = {"bases": "bases", "target": "target_observation", "output": "output_binding"}[stage]
        if (actual[name] is not None or stage == "target" and actual["bases"] is None
                or stage == "output" and actual["target_observation"] is None):
            raise error_type("PHASE_INVALID")
        source_native._validate_observation_original(context, stage, value)
        actual[name] = value
        cs[name].__set__(context, value)

    def attach_source(token, source_token, source_record):
        context = _source_context_original(token)
        registration = context.registration
        actual = dict.get(anchors, id(registration))
        if (registration.phase != "source_native" or actual["source_token"] is not None
                or actual["source_record"] is not None or context.output_binding is None
                or context.target_observation is None or registration.callback_active):
            raise error_type("PHASE_INVALID")
        source_module._validate_new_source_original(context, source_token, source_record)
        actual["source_token"], actual["source_record"] = source_token, source_record
        cs["source_token"].__set__(context, source_token)
        cs["source_record"].__set__(context, source_record)

    def make_history(registration, payload):
        _record_original(registration)
        if registration.phase != "operation_retiring" or registration.callback_active:
            raise error_type("PHASE_INVALID")
        key, owner, token = id(registration.token), registration.owner, registration.token
        frozen = _freeze_source_value_original(payload)
        def collected(reference, *, key=key):
            with lock:
                original = dict.get(histories, key)
                if original is not None and original[1][0] is reference:
                    if dict.get(closed, key) is original[0]:
                        dict.pop(closed, key)
                    dict.pop(histories, key)
        fields = (weak(token, collected), owner[0], owner[1],
            None if owner[2] is None else weak(owner[2]), weak(registration.guard),
            weak(registration.inputs[1]), frozen)
        history = closed_type(fields)
        histories[key] = (history, fields, False)
        return history

    def consume(token):
        _integrity_original()
        owner = _owner_original()
        routing.require_no_reentry(owner)
        _unknown_original()
        with lock:
            original = dict.get(histories, id(token))
            history = dict.get(closed, id(token))
            if (type(token) is not _OperationToken or original is None or original[0] is not history
                    or type(history) is not closed_type):
                raise error_type("CLOSED")
            fields = fields_slot.__get__(history, closed_type)
            anchor = anchor_slot.__get__(history, closed_type)
            if (fields is not original[1] or type(anchor) is not tuple or len(anchor) != 1 or anchor[0] is not fields
                    or consumed_slot.__get__(history, closed_type) is not original[2]
                    or marker_slot.__get__(history, closed_type) is not (consumed if original[2] else None)):
                raise error_type("RECORD_CHANGED")
            if fields[0]() is not token:
                raise error_type("CLOSED")
            if (fields[1] != owner[0] or fields[2] != owner[1]
                    or (owner[2] is not None if fields[3] is None else fields[3]() is not owner[2] or fields[3]() is None)):
                raise error_type("OWNER_MISMATCH")
            if original[2]:
                raise error_type("CONSUMED")
            guard, group_token = fields[4](), fields[5]()
            if guard is None or group_token is None or group.guard_original(group_token) is not guard or _CURRENT.get() is not guard:
                raise error_type("GUARD_CHANGED")
            consumed_slot.__set__(history, True)
            marker_slot.__set__(history, consumed)
            histories[id(token)] = (history, fields, True)
        return _thaw_source_value_original(fields[6])

    def run(owner, inputs):
        _integrity_original()
        if (type(owner) is not tuple or len(owner) != 3
                or type(owner[0]) is not int or owner[0] < 0
                or type(owner[1]) is not int or owner[1] < 0
                or not _same_owner(owner, _owner_original())):
            _fail("OWNER_MISMATCH")
        routing.require_no_reentry(owner)
        _unknown_original()
        _source_inputs_original(inputs)
        # Pure plan comes only from this fixed callee after original inputs.
        # A lower caller cannot pass a plan, directory, holder or FD for adoption.
        plan = source_native._plan_source_unit_original(inputs)
        token = _OperationToken(_TAG)
        record = _OperationRecord(token, owner, inputs)
        registration = _OperationRegistration(record)
        key, owner_key = id(token), registration.owner_key
        first, remember, retire_first = _FIRST_PEEK, _FIRST_ORIGINAL, _FIRST_RETIRE
        phase_slot, record_phase, window_slot = _REGISTRATION_PHASE, _RECORD_PHASE, _REGISTRATION_WINDOW
        success, history, source_cleanup, source_retirement = False, None, None, None
        unit_cleanup, unit_retirement, source_result = None, None, None
        try:
            with lock:
                _unknown_original()
                if owner_key in owners:
                    _fail("REENTRANT")
                live[key], owners[owner_key] = registration, registration
                selections[id(registration)] = (registration, plan)
                context = enroll(registration, plan)
            def check_cancelled():
                _callback_original(registration)
            registration.callback_wrapper = check_cancelled
            registration.callback_pin = _function_pin(check_cancelled)
            budget = _BUDGET("readonly", check_cancelled)
            registration.budget = record.budget = budget
            registration.budget_dict = _dictionary(budget)
            registration.budget_fixed = tuple((name, value) for name, value in registration.budget_dict.items()
                if name not in _SOURCE_MUTABLE_BUDGET)
            registration.budget_anchor = (budget, registration.budget_dict, registration.budget_fixed,
                check_cancelled, registration.callback_pin)
            registration.budget_last = budget._last_tick
            configure(registration, budget=True)
            registration.run_id = record.run_id = str(_UUID4())
            _phase_original(registration, "registered")
            source_native._require_supported_original()
            _phase_original(registration, "guard_checking")
            _check_budget_original(registration)
            registration.guard = record.guard = group.guard_original(inputs[1])
            configure(registration, guard=True)
            _integrity_original()
            _guard_original(registration)
            guard_dictionary = _dictionary(registration.guard)
            registration.guard_fields = (guard_dictionary, tuple(guard_dictionary.items()))
            _verify_selected_guard_original(record)
            _record_original(registration)
            _check_budget_original(registration)
            _phase_original(registration, "source_native_ready")
            unit_key = source_native._open_source_unit_original(token)
            # Captured ownership roots must be available even if attachment
            # validation fails. Their own registry pins the original token.
            unit_cleanup, unit_retirement = source_native._trusted_callbacks_original(unit_key)
            attach_unit(token, unit_key)
            _phase_original(registration, "source_native")
            _source_checkpoint_original(token, None, "bases")
            bases = source_native._open_coordinator_bases_original(unit_key)
            attach_observation(token, "bases", bases)
            _source_checkpoint_original(token, None, "target_begin")
            observation = source_native._observe_target_original(unit_key)
            attach_observation(token, "target", observation)
            _source_checkpoint_original(token, None, "target_observed")
            output = source_native._bind_output_original(unit_key)
            attach_observation(token, "output", output)
            _source_checkpoint_original(token, None, "output_bound")
            manager = source_module.source_scope_original(token)
            source_enter, source_cleanup, source_retirement = source_module._trusted_scope_callbacks_original(manager)
            source_token = source_enter()
            if source_token is not context.source_token:
                _fail("RECORD_CHANGED")
            source_module.location_original(source_token)
            source_module.capture_original(source_token)
            source_module.selection_original(source_token)
            source_native._final_source_metadata_original(unit_key, source_token)
            _source_checkpoint_original(token, source_token, "final_metadata")
            source_result = source_module._source_result_original(source_token)
            _phase_original(registration, "source_finalizing")
            # Exhaustive release uses captured original roots. No deadline,
            # cancellation, guard or first error can obstruct actual closes.
            source_cleanup()
            unit_cleanup()
            source_native._assert_unit_settled_original(unit_key)
            _phase_original(registration, "source_unit_closed")
            _record_original(registration)
            budget.assert_settled()
            _check_budget_original(registration)
            report = source_native._source_unit_report_original(unit_key)
            _phase_original(registration, "operation_retiring")
            _verify_selected_guard_original(record)
            _record_original(registration)
            _check_budget_original(registration)
            counters = budget.snapshot()
            registration.budget_last = registration.budget_dict["_last_tick"]
            _check_budget_original(registration)
            _record_original(registration)
            payload = {"format_version": 1, "kind": "replica_source_census_check",
                "operation_id": inputs[4], "run_id": registration.run_id, "mode": "preview",
                "budget_mode": "readonly", "source_kind": inputs[11][0],
                "target_profile_paths": list(inputs[9][0]),
                "required_profile_paths": [list(row) for row in inputs[9]],
                "source": source_result, "native": report, "counters": counters,
                "checkpoint_count": registration.checkpoint_count,
                "source_observed": True, "unit_native_normal_closed": True,
                "global_settled": True, "operation_context_returned": True,
                "original_registry_retired": True, "complete_preview": False,
                "source_closed": False, "source_native_closure": "not_established",
                "target_native_closure": "not_established", "outer_lifecycle_exited": False,
                "producer": False, "copy_performed": False, "data_restored": False,
                "write_authority": False, "sender_authority": False}
            history = make_history(registration, payload)
            success = True
        except BaseException as exc:
            remember(registration, exc)
        finally:
            # Every captured cleanup is exhaustive and internally exactly-once.
            # Normal body cleanup may have completed; this repeat is a no-op,
            # never a second close attempt. Continue other roots on any failure.
            for cleanup in (source_cleanup, unit_cleanup):
                if cleanup is not None:
                    try:
                        cleanup()
                    except BaseException as exc:
                        remember(registration, exc)
            for retirement in (source_retirement, unit_retirement):
                if retirement is not None:
                    try:
                        retirement(success and first(registration) is None)
                    except BaseException as exc:
                        remember(registration, exc)
            # Unknown resources entered the original permanent debt registry
            # before these pops. Process exit is never a normal-close witness.
            with lock:
                try:
                    if success and first(registration) is None:
                        closed[key] = history
                        phase_slot.__set__(registration, "closed")
                        record_phase.__set__(record, "closed")
                    else:
                        phase_slot.__set__(registration, "source_failed_cleanup")
                        record_phase.__set__(record, "source_failed_cleanup")
                finally:
                    try:
                        if dict.get(windows, owner_key) is window_slot.__get__(registration, registration_type):
                            dict.pop(windows, owner_key, None)
                        window_slot.__set__(registration, None)
                    finally:
                        try:
                            if dict.get(live, key) is registration:
                                dict.pop(live, key)
                        finally:
                            if dict.get(owners, owner_key) is registration:
                                dict.pop(owners, owner_key)
                            if first(registration) is not None:
                                phase_slot.__set__(registration, "retired_failed")
                                record_phase.__set__(record, "retired_failed")
                            dict.pop(anchors, id(registration), None)
                            dict.pop(selections, id(registration), None)
                            if not success or first(registration) is not None:
                                dict.pop(histories, key, None)
        error = retire_first(registration)
        if error is not None:
            raise error
        return token

    return selected, check, attach_unit, attach_observation, attach_source, consume, run


(_source_driver_selected_original, _source_driver_anchor_original, _attach_source_unit_original,
 _attach_source_observation_original, _attach_source_record_original,
 _consume_source_census_closed_original, _operation_source_census_locked) = _make_source_driver_original()
del _make_source_driver_original
_SOURCE_CONTEXT_PHASE = _CLASS_GET(_SourceUnitContext, "__dict__")["phase"]


def check_source_census_locked(target_profile, group_token, *, source, operation_id, check_cancelled=None):
    """One readonly actual source census; it grants no producer/closed token."""
    _integrity_original()
    owner = _owner_original()
    routing.require_no_reentry(owner)
    _unknown_original()
    # Fixed preview mode and expected=None are internal decisions. A caller
    # supplies no budget/clock/reader/FD/body/backend or adoption capability.
    inputs = _inputs_original((target_profile, group_token, source, "preview", operation_id, None, check_cancelled))
    token = _operation_source_census_locked(owner, inputs)
    return _consume_source_census_closed_original(token)


_DATA_CLOSED_FIELDS = _CLASS_GET(_ClosedDataChain, "__dict__")["fields"]
(_make_closed_data_history_original, _check_closed_data_history_original,
 _consume_closed_data_history_original) = _make_data_history_original()
(_enroll_native_driver_original, _attach_native_scope_original,
 _driver_anchor_original, _retire_driver_original, _trusted_driver_retirement_original,
 _operation_data_chain_locked) = _make_driver_anchor_original()
del _make_data_history_original, _make_driver_anchor_original


_RECORD_FIRST = _CLASS_GET(_OperationRecord, "__dict__")["first"]
_RECORD_CALLBACK = _CLASS_GET(_OperationRecord, "__dict__")["callback_active"]
_RECORD_PHASE = _CLASS_GET(_OperationRecord, "__dict__")["phase"]
_CLOSED_FIELDS = _CLASS_GET(_ClosedCheck, "__dict__")["fields"]
_REGISTRATION_FIRST = _CLASS_GET(_OperationRegistration, "__dict__")["first"]
_REGISTRATION_PHASE = _CLASS_GET(_OperationRegistration, "__dict__")["phase"]
_REGISTRATION_WINDOW = _CLASS_GET(_OperationRegistration, "__dict__")["window"]
_REGISTRATION_CALLBACK = _CLASS_GET(_OperationRegistration, "__dict__")["callback_active"]
_FIRST_ORIGINAL, _FIRST_PEEK, _FIRST_RETIRE = _make_first_original()
del _make_first_original
# Original reject/make kernels are held only by fixed verifier/first defaults.
# No runtime caller obtains them through a module getter.
del _trusted_make_error_original, _trusted_reject_original


def _make_integrity_original(verify, remember, *, object=object, type=type,
        dict=dict, tuple=tuple, str=str, int=int, len=len, id=id, zip=zip,
        any=any, BaseException=BaseException, RuntimeError=RuntimeError,
        FunctionType=FunctionType, CellType=CellType, ValueError=ValueError,
        _GET=object.__getattribute__, _MISSING=_MISSING,
        _fail=_verify_catalog_original.__kwdefaults__["_fail"]):
    """Original catalog closure and first delivery are the minimal trust root.

    Rewriting this root itself is outside the original-entry threat model.
    Its downstream owner/active/remember helpers remain individually pinned.
    """
    catalog = None
    pid_now, thread_now = os.getpid, threading.get_ident
    running_loop, tasks = asyncio.events.get_running_loop, asyncio.tasks._current_tasks
    owners = _ORIGINAL_CONTAINERS[1]
    registration_type = _OperationRegistration
    owner_slot = _CLASS_GET(registration_type, "__dict__")["owner"]
    # Retain this original verifier independently of its mutable function slots.
    # This bootstrap/minimal closure remains the existing protected trust root;
    # it is not binary authenticity or continuous/atomic mutation protection.
    if type(verify) is not FunctionType:
        _fail("RECORD_CHANGED")
    verify_code = _GET(verify, "__code__")
    verify_defaults = _GET(verify, "__defaults__")
    verify_kwdefaults = _GET(verify, "__kwdefaults__")
    if verify_kwdefaults is not None and (
            type(verify_kwdefaults) is not dict
            or any(type(key) is not str for key in verify_kwdefaults)):
        _fail("RECORD_CHANGED")
    verify_keywords = () if verify_kwdefaults is None else tuple(dict.items(verify_kwdefaults))
    verify_closure = _GET(verify, "__closure__")
    verify_cells = () if verify_closure is None else tuple(
        (cell, _GET(cell, "cell_contents")) for cell in verify_closure)

    def integrity():
        try:
            # Reject slot/default/cell replacement before dispatching any new
            # pipeline object. The tail catalog checks remain unmodified.
            if (type(verify) is not FunctionType or _GET(verify, "__code__") is not verify_code
                    or _GET(verify, "__defaults__") is not verify_defaults
                    or _GET(verify, "__kwdefaults__") is not verify_kwdefaults
                    or _GET(verify, "__closure__") is not verify_closure):
                _fail("RECORD_CHANGED")
            if verify_kwdefaults is not None:
                if (type(verify_kwdefaults) is not dict
                        or len(verify_kwdefaults) != len(verify_keywords)
                        or any(type(key) is not str for key in verify_kwdefaults)):
                    _fail("RECORD_CHANGED")
                for name, original in verify_keywords:
                    if dict.get(verify_kwdefaults, name, _MISSING) is not original:
                        _fail("RECORD_CHANGED")
            if verify_closure is not None:
                if type(verify_closure) is not tuple or len(verify_closure) != len(verify_cells):
                    _fail("RECORD_CHANGED")
                for cell, (original_cell, original_contents) in zip(verify_closure, verify_cells):
                    if type(cell) is not CellType or cell is not original_cell:
                        _fail("RECORD_CHANGED")
                    try:
                        contents = _GET(cell, "cell_contents")
                    except ValueError:
                        _fail("RECORD_CHANGED")
                    if contents is not original_contents:
                        _fail("RECORD_CHANGED")
            verify(catalog)
        except BaseException as error:
            # An integrity error swallowed inside the user callback remains
            # first. The supported interpreter's immutable C primitives and
            # the original task table locate the actual execution context
            # even when the Python owner/current_task function was poisoned.
            # This is failure delivery only, never a new ownership issuer.
            pid, thread, task = pid_now(), thread_now(), None
            try:
                loop = running_loop()
            except RuntimeError:
                loop = None
            if loop is not None:
                try:
                    for actual_loop, actual_task in dict.items(tasks):
                        if actual_loop is loop:
                            task = actual_task
                            break
                except RuntimeError:
                    raise error
            try:
                candidates = tuple(dict.values(owners))
            except RuntimeError:
                raise error
            for registration in candidates:
                if type(registration) is registration_type:
                    registered_owner = owner_slot.__get__(registration, registration_type)
                    if (type(registered_owner) is tuple and len(registered_owner) == 3
                            and type(registered_owner[0]) is int and type(registered_owner[1]) is int
                            and registered_owner[0] == pid and registered_owner[1] == thread
                            and registered_owner[2] is task):
                        raise remember(registration, error)
            raise
    def bind_once(value):
        nonlocal catalog
        if catalog is not None:
            raise RuntimeError("Original integrity catalog is already bound")
        catalog = value
    return integrity, bind_once


def _seal_observer_entry_original(function, owners, verify):
    # The original entry holds its original no-active gate and verifier,
    # before resolving any mutable module alias or downstream helper.
    def entry(*args, **kwargs):
        if not owners:
            return
        verify()
        return function(*args, **kwargs)
    entry.__name__, entry.__qualname__, entry.__module__ = function.__name__, function.__qualname__, function.__module__
    return entry


def _seal_checked_entry_original(function, verify):
    def entry(*args, **kwargs):
        verify()
        return function(*args, **kwargs)
    entry.__name__, entry.__qualname__, entry.__module__ = function.__name__, function.__qualname__, function.__module__
    return entry


def _seal_source_checked_entry_original(function, verify):
    # Preserve routing-before-binding even if a callback calls this fixed
    # exact-signature entry with missing/duplicate/unexpected arguments.
    def entry(*args, **kwargs):
        verify()
        owner = _owner_original()
        routing.require_no_reentry(owner)
        _unknown_original()
        return function(*args, **kwargs)
    entry.__name__, entry.__qualname__, entry.__module__ = function.__name__, function.__qualname__, function.__module__
    return entry


def _make_shared_entry_original(owners, verify, reject, entry, scope, guard, *, type=type, str=str, fail=_fail):
    # Captured by the shared lazy-bootstrap gate before any RP1 callback.
    # No caller supplies a registry, owner capability, adapter or validator.
    def dispatch(kind, subject=None):
        if not owners:
            return
        verify()
        if type(kind) is not str:
            fail("TYPE_INVALID")
        if kind == "route":
            # The early shared gate obtains the actual owner itself before
            # the original routing body binds or validates its arguments.
            return reject(_owner_original())
        if kind == "entry":
            return entry()
        if kind == "scope":
            return scope()
        if kind == "guard":
            return guard(subject)
        fail("ARGUMENT_INVALID")
    return dispatch


def _make_shared_gate_preflight_original(cache, dispatch, *, kind_of=type, list_type=list, length=len, fail=_fail):
    """Only the fixed no-argument bootstrap calls this during module import."""
    if dispatch is not _shared_entry_original:
        fail("RECORD_CHANGED")
    owners, verify = _ORIGINAL_CONTAINERS[1], _integrity_original
    def preflight():
        if owners:
            verify()
        if kind_of(cache) is not list_type or length(cache) != 1 or cache[0] is not dispatch:
            fail("RECORD_CHANGED")
    return preflight

_NATIVE_SIGNATURES = () if os.name != "nt" else tuple(
    (function, _GET(function, "argtypes"), tuple(_GET(function, "argtypes")),
     _GET(function, "restype"), _GET(function, "errcheck"))
    for function in (group._WIN_GET_CURRENT_PROCESS, group._WIN_DUPLICATE_HANDLE,
                      group._WIN_GET_FILE_INFORMATION, group._WIN_CLOSE_HANDLE,
                      lifecycle.safe._kernel.CreateFileW, lifecycle.safe._kernel.GetFileInformationByHandle,
                      lifecycle.safe._kernel.CloseHandle, lifecycle.safe._kernel.SetFileInformationByHandle,
                      lifecycle.safe._nt.NtSetInformationFile, lifecycle.safe._nt.RtlNtStatusToDosError,
                      lifecycle._kernel.LockFileEx,
                      lifecycle._kernel.UnlockFileEx,
                      *owned_group._native_custody_functions_original()))

_integrity_original, _bind_integrity_once = _make_integrity_original(_verify_catalog_original, _FIRST_ORIGINAL)
# Fixed one-time bidirectional bootstrap. Native imports this partially built
# original module; it never chooses/imports a caller backend at runtime.
from . import personal_authority_replica_directory_native as native
from . import personal_authority_replica_source_native as source_native
from . import personal_authority_replica_source as source_module
from . import personal_authority_replica_rawfact as source_rawfact
from . import personal_authority_replica_source_declaration as source_declaration
_SOURCE_UNSUPPORTED_CONSTANT = object()
_SOURCE_CONSTANT_PINS = _capture_source_constants_original((
    source_rawfact, source_declaration, source_module.replica, source_module.raw2,
    source_module.replica.raw1, source_module.replica.census, source_module.raw2.projection,
    source_module.raw2.policy) + owned_group._content_codec_modules_original())
# These private stdlib contracts belong to the selector's supported
# representation. Ordinary storage imports do not require that capability.
_SOURCE_REGEX_TABLE_PINS = (_capture_source_regex_tables_original(source_rawfact.re)
    if selector._SUPPORTED else ())
_SOURCE_DISPATCH_PINS = (_capture_source_dispatch_original(source_rawfact, source_module.utf8)
    if selector._SUPPORTED else None)
_NATIVE_SIGNATURES = _NATIVE_SIGNATURES + native._NATIVE_SIGNATURES + source_native._NATIVE_SIGNATURES
_NATIVE_MODULE_LINKS = (sys.modules, native._NATIVE_MODULE_LINKS[1] + source_native._NATIVE_MODULE_LINKS[1]
    + tuple((module.__name__, module) for module in
        (_keyscan_coperator, _keyscan_operator, _keyscan_itertools)))
_ABI_STRUCTURE_FIELDS = native._ABI_STRUCTURE_FIELDS + source_native._ABI_STRUCTURE_FIELDS
_NATIVE_META_PINS = native._NATIVE_META_PINS + source_native._NATIVE_META_PINS
def _with_fixed_rename_layout_classes_original(signature_classes, selected):
    """Bootstrap-only fixed layout seeds plus their MRO, identity de-duplicated."""
    result, seen = list(signature_classes), {id(cls) for cls in signature_classes}
    for selected_class in selected:
        for cls in _CLASS_GET(selected_class, "__mro__"):
            if id(cls) not in seen:
                seen.add(id(cls))
                result.append(cls)
    return tuple(result)


_OWNED_EX_RENAME_LAYOUT_CLASSES = () if os.name != "nt" else (
    lifecycle.safe._WinRenameReplaceFlags, lifecycle.safe._WinRenameName,
    lifecycle.safe._WinRenameInfo, lifecycle.safe._IoStatus, lifecycle.safe.wintypes.BOOLEAN,
    lifecycle.safe.wintypes.DWORD, lifecycle.safe.wintypes.HANDLE,
    lifecycle.safe.wintypes.WCHAR)
_OWNED_EX_ABI_CLASSES = () if os.name != "nt" else native._classes_of_native_signature_original((
    lifecycle.safe._kernel.CreateFileW, lifecycle.safe._kernel.GetFileInformationByHandle,
    lifecycle.safe._kernel.CloseHandle, lifecycle.safe._kernel.SetFileInformationByHandle,
    lifecycle.safe._nt.NtSetInformationFile, lifecycle.safe._nt.RtlNtStatusToDosError,
    lifecycle._kernel.LockFileEx, lifecycle._kernel.UnlockFileEx,
    *owned_group._native_custody_functions_original()))
_OWNED_EX_ABI_CLASSES = _with_fixed_rename_layout_classes_original(
    _OWNED_EX_ABI_CLASSES, _OWNED_EX_RENAME_LAYOUT_CLASSES)
_ABI_STRUCTURE_FIELDS = _ABI_STRUCTURE_FIELDS + tuple((cls, _CLASS_GET(cls, "__dict__")["_fields_"],
    tuple(_CLASS_GET(cls, "__dict__")["_fields_"])) for cls in _OWNED_EX_ABI_CLASSES
    if "_fields_" in _CLASS_GET(cls, "__dict__"))
_NATIVE_META_PINS = _NATIVE_META_PINS + tuple((cls, type(cls)) for cls in _OWNED_EX_ABI_CLASSES)
# Fixed private bootstrap holder for the already selected native signatures.
# It is not an imported backend, is never registered in sys.modules, and is
# populated once before the original full-class catalog is captured.
_OWNED_EX_ABI_CLASS_ANCHOR = ModuleType(__name__ + "._owned_ex_abi_classes")
_OWNED_EX_ABI_CLASS_NAMES = tuple("ABI_" + str(index) for index in range(len(_OWNED_EX_ABI_CLASSES)))
dict.update(_GET(_OWNED_EX_ABI_CLASS_ANCHOR, "__dict__"),
    zip(_OWNED_EX_ABI_CLASS_NAMES, _OWNED_EX_ABI_CLASSES))
check_source_census_locked = _seal_source_checked_entry_original(check_source_census_locked, _integrity_original)
_source_context_original = _seal_checked_entry_original(_source_context_original, _integrity_original)
_source_scope_context_original = _seal_checked_entry_original(_source_scope_context_original, _integrity_original)
_source_checkpoint_original = _seal_checked_entry_original(_source_checkpoint_original, _integrity_original)
_consume_source_census_closed_original = _seal_checked_entry_original(_consume_source_census_closed_original, _integrity_original)
check_target_data_chain_locked = _seal_checked_entry_original(check_target_data_chain_locked, _integrity_original)
_native_checkpoint_original = _seal_checked_entry_original(_native_checkpoint_original, _integrity_original)
_consume_data_chain_unit_closed_original = _seal_checked_entry_original(_consume_data_chain_unit_closed_original, _integrity_original)
check_operation_locked = _seal_checked_entry_original(check_operation_locked, _integrity_original)
_checkpoint_original = _seal_checked_entry_original(_checkpoint_original, _integrity_original)
_consume_check_closed_original = _seal_checked_entry_original(_consume_check_closed_original, _integrity_original)
_reject_reentry_original = _seal_observer_entry_original(_reject_reentry_original, _OWNERS, _integrity_original)
_observer_original = _seal_observer_entry_original(_observer_original, _OWNERS, _integrity_original)
_observe_lifecycle_entry_original = _seal_observer_entry_original(_observe_lifecycle_entry_original, _OWNERS, _integrity_original)
_observe_lifecycle_scope_original = _seal_observer_entry_original(_observe_lifecycle_scope_original, _OWNERS, _integrity_original)
_observe_lifecycle_guard_original = _seal_observer_entry_original(_observe_lifecycle_guard_original, _OWNERS, _integrity_original)
_shared_entry_original = _make_shared_entry_original(_OWNERS, _integrity_original,
    _reject_reentry_original, _observe_lifecycle_entry_original,
    _observe_lifecycle_scope_original, _observe_lifecycle_guard_original)
_SHARED_GATE_CACHE_PINS = lifecycle._bootstrap_replica_operation_gate_original()
_OWNED_EX_FIXED_MODULES = owned_group._catalog_modules_original()
_MODULE_LINKS = (sys.modules, _GET(sys.modules[__package__], "__dict__"), tuple(
    (module.__name__, module) for module in (sys.modules[__name__], lifecycle, group, selector,
        accounting, policy, routing, raw1, raw2, legacy_accounting, native,
        source_native, source_module, source_rawfact, source_declaration, *_OWNED_EX_FIXED_MODULES)))

# Pin all definitions in these fixed modules; this is a fixed source
# inventory, not a runtime plugin/discovery mechanism. Class dict descriptors
# and contextmanager wrapped-function closure cells are included recursively.
_CALLEE_PINS = _capture_graph(
    (sys.modules[__name__], lifecycle, group, selector, accounting, policy, routing,
     legacy_accounting, selector.pathlib, os.path, native,
      source_native, source_module, source_rawfact, source_declaration, *_OWNED_EX_FIXED_MODULES),
    ((lifecycle, ("asyncio", "threading", "os", "safe", "_CURRENT", "_GUARD_VERIFY_ORIGINAL",
        "_GUARD_ALLOWS_ORIGINAL", "_FILE_LOCK_VERIFY_ORIGINAL", "_FILE_LOCK_STAT_ORIGINAL",
        "_FILE_LOCK_ENTRY_ORIGINAL", "_MARKER", "LOCK_NAME")),
     (group, tuple(name for name in vars(group)
                   if name.startswith("_") and name.upper() == name and name != "_POISONED") +
        ("lifecycle", "safe", "os", "Path", "stat", "threading", "time", "MethodType", "weakref", "ctypes", "wintypes")),
     (selector, tuple(name for name in vars(selector) if name.startswith("_") and name.upper() == name) + ("lifecycle",)),
     (accounting, ("policy", "time", "Error", "MappingProxyType")),
     (policy, ("_PRODUCER", "_READONLY")),
     (routing, ("_OWNER_TASK_TYPE",)),
     (raw1, ("_OWNERS", "AuthorityRawPreservationError", "accounting")),
     (raw2, ("_OWNERS", "first")),
     (asyncio, ("current_task", "_get_running_loop")),
     (asyncio.tasks, ("current_task", "events", "_current_tasks")),
     (asyncio.events, ("get_running_loop", "_get_running_loop")), (threading, ("get_ident",)),
     (os, ("getpid", "fstat", "read", "lseek", "stat", "path", "fspath", "name", "urandom", "getcwd", "getcwdb", "close", "stat_result", "O_RDONLY", "O_BINARY")),
     (time, ("monotonic_ns",)), (uuid, ("uuid4", "UUID", "os", "int_", "bytes_")),
     (lifecycle.safe, ("_safe_stat", "_native_path", "_Directory", "_REPARSE", "_error", "SyncError", "stat", "os")),
     (lifecycle.stat, ("S_ISLNK", "S_ISDIR", "S_ISREG", "S_IMODE")),
     (os.path, ("abspath", "normcase", "normpath", "isabs", "splitroot", "splitdrive", "join", "os",
                "_getfullpathname", "_path_normpath", "_get_bothseps", "_splitroot", "_reserved_names",
                "_LCMapStringEx", "_LOCALE_NAME_INVARIANT", "_LCMAP_LOWERCASE")),
     (selector.pathlib, ("sys", "os", "ntpath", "posixpath")),
     (_keyscan_builtins, ("type", "str", "all", "map")),
     (_keyscan_coperator, ("is_",)),
     (_keyscan_operator, ("is_",)),
     (_keyscan_itertools, ("repeat",)),
     (sys, ("intern",)),
     (contextlib, ("_GeneratorContextManager",)),
     (sys.modules[__name__], ("lifecycle", "group", "selector", "accounting", "policy", "routing", "raw1", "raw2", "legacy_accounting",
         "_OWNER", "_CURRENT", "_BUDGET", "_GUARD", "_PROFILE", "_UUID4", "_WEAK", "_TASK",
         "_ORIGINAL_CONTAINERS", "_ORIGINAL_LOCK", "_RECORD_FIRST", "_RECORD_CALLBACK", "_RECORD_PHASE",
         "_CLOSED_FIELDS", "_CONSUMED", "_NATIVE_SIGNATURES", "_FIRST_ORIGINAL", "_FIRST_PEEK", "_FIRST_RETIRE",
         "_REGISTRATION_FIRST", "_REGISTRATION_PHASE", "_REGISTRATION_WINDOW", "_REGISTRATION_CALLBACK",
         "_TYPE", "_GET", "_SET", "_CLASS_GET", "_MISSING", "_TAG", "_FIELDS", "_BUDGET_ZERO", "_MODULE_LINKS", "_SHARED_GATE_CACHE_PINS",
         "native", "_NATIVE_MUTABLE_BUDGET", "_DATA_CLOSED_FIELDS", "_NATIVE_MODULE_LINKS", "_ABI_STRUCTURE_FIELDS", "_NATIVE_META_PINS",
         "source_native", "source_module", "source_rawfact", "source_declaration",
         "_keyscan_builtins", "_keyscan_coperator", "_keyscan_operator", "_keyscan_itertools",
         "_SOURCE_MUTABLE_BUDGET", "_SOURCE_COUNTERS", "_SOURCE_CHECKPOINT_STAGES", "_SOURCE_CONTEXT_PHASE",
          "_SOURCE_UNSUPPORTED_CONSTANT", "_SOURCE_CONSTANT_PINS", "_SOURCE_REGEX_TABLE_PINS", "_SOURCE_DISPATCH_PINS",
         "_make_data_history_original", "_make_driver_anchor_original", "_make_first_original", "_make_source_driver_original")),
      (sys.modules[__name__], ("owned_group", "_OWNED_EX_FIXED_MODULES", "_OWNED_EX_ABI_CLASSES",
          "_OWNED_EX_RENAME_LAYOUT_CLASSES", "_OWNED_EX_ABI_CLASS_ANCHOR", "_OWNED_EX_ABI_CLASS_NAMES")),
      (_OWNED_EX_ABI_CLASS_ANCHOR, _OWNED_EX_ABI_CLASS_NAMES),
      (owned_group, ("_GROUP_SEAL", "_GROUPS", "_SOURCE_ROLES")),
      *owned_group._catalog_extra_original(),
      *native._catalog_extra_original(),
     *source_native._catalog_extra_original(),
     *source_module._catalog_extra_original(),
     *source_declaration._catalog_extra_original(),
     *_source_dispatch_catalog_extra_original(source_rawfact, source_module.utf8),
     *(( (group.ctypes, ("byref", "get_last_error", "WinError", "GetLastError", "FormatError", "c_void_p")),
          (group.wintypes, ("HANDLE",)),
          (group.safe.msvcrt, ("open_osfhandle", "get_osfhandle"))) if os.name == "nt" else ())))

_bind_integrity_once(_CALLEE_PINS)
del _bind_integrity_once
