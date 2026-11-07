"""Original Windows source-census responsibility unit (r777 candidate).

This separate kernel owns the RP1 coordinator roots, observer, output metadata
and source readers under ONE original readonly ledger.  It neither calls nor
changes directory_native, BufferedReader, scandir, Directory, or G1 nativeview.
No caller path/fd/clock/closer/backend/counter is an admission interface.  The
operation imports this module before its original graph is sealed.  The small
captured ownership kernel and the operation's original-entry verification root
are the explicit trust roots; modifying those roots is outside this model.

Candidate only: not imported, compiled or executed by its author.  Final
metadata is one metadata re-enumeration, not the S4 two full body reads, and no
producer/source_closed, copy, restore, write or sender authority is produced.
"""
from __future__ import annotations

import builtins
import ctypes
import _ctypes
import hashlib
import json
import ntpath
import os
import stat
import sys
import uuid
from types import FunctionType, ModuleType

_OPERATION_NAME = __package__ + ".personal_authority_replica_operation"
_OPERATION_BOOTSTRAP = sys.modules.get(_OPERATION_NAME)
if (type(_OPERATION_BOOTSTRAP) is not ModuleType
        or "_FIRST_ORIGINAL" not in object.__getattribute__(_OPERATION_BOOTSTRAP, "__dict__")
        or "_integrity_original" not in object.__getattribute__(_OPERATION_BOOTSTRAP, "__dict__")):
    raise ImportError("Import original replica operation before source native")
from . import personal_authority_replica_operation as operation
from . import personal_authority_replica_path_roles as roles

_TYPE, _GET, _SET, _CLASS_GET = type, object.__getattribute__, object.__setattr__, type.__getattribute__
_TAG, _MISSING = object(), object()
_STAT, _FSTAT, _READ, _SEEK, _CLOSE = os.stat, os.fstat, os.read, os.lseek, os.close
_HASH, _UUID4 = hashlib.sha256, uuid.uuid4
_HASH_TYPE = type(_HASH())
_JSON_LOADS, _JSON_DUMPS = json.loads, json.dumps
_IO_KEYS = ("read_bytes", "source_read_bytes", "read_calls", "source_read_calls",
    "seek_calls", "confirmed_write_bytes", "write_calls", "uncertain_read_calls",
    "uncertain_source_read_calls", "uncertain_write_bytes")
_Q_KEYS = _IO_KEYS + ("package_read_bytes", "outer_control_read_bytes",
    "observer_node_read_bytes", "other_native_control_read_bytes")
_KINDS = ("directory_handle", "file_handle", "stream", "scan_iterator",
    "token_handle", "security_buffer")
_SCOPES = ("coordinator", "observer", "output", "source")
_EVENT_DOMAIN = b"reno-authority-replica-runtime-v1\0resource-events\0"
_SOURCE_FAMILY = ".reno-authority-raw-preservation-v2"
_OUTPUT_FAMILY = ".reno-authority-raw-preservation-replicas-v1"
_CHUNK, _MAX_PATH, _MAX_CHAIN = 131072, 260, 129
_MAX_ENTRIES, _MAX_FACTS, _MAX_RESOURCES = 400064, 300032, 100000000
_MAX_FILE, _MAX_SOURCE, _MAX_CONTROL = 64 * 1024**3, 128 * 1024**3, 128 * 1024**2
_DIR_ATTR, _REPARSE, _ERROR_NOT_FOUND, _ERROR_NO_MORE = 0x10, 0x400, 2, 18
_READ_CONTROL, _GENERIC_READ, _BACKUP_REPARSE = 0x20000, 0x80000000, 0x02200000
_INVALID_HANDLE = 2**64 - 1
_INTERPRETER = (sys.implementation.name, tuple(sys.version_info[:3]), ctypes.sizeof(ctypes.c_void_p))
_BACKEND_NAME = os.name
_DLL = _ADVAPI = _HandleInfo = _FindData = None
_HANDLE = _BOOL = _DWORD = _LPVOID = None
_CREATE = _DUPLICATE = _HANDLE_INFO = _FINAL_PATH = _CLOSE_HANDLE = None
_CURRENT_PROCESS = _LOCAL_FREE = _LOCAL_SIZE = _FIND_FIRST = _FIND_NEXT = _FIND_CLOSE = None
_OPEN_TOKEN = _TOKEN_INFO = _SID_TEXT = _VALID_SID = _SID_LENGTH = None
_SECURITY_INFO = _SECURITY_OWNER = _SECURITY_TEXT = _SECURITY_LENGTH = _SECURITY_VALID = None
_CRT_OPEN = _CRT_GET = _LAST_ERROR = _BYREF = _BUFFER = _BYTE_BUFFER = None
_CAST = _POINTER = _WSTRING = None
_WCHAR_POINTER = _LPVOID_POINTER = _WCHAR_BUFFER_TYPE = _BYTE_BUFFER_TYPE = None
_POINTER_CACHE = None
_POINTER_SELECTIONS = ()

if os.name == "nt":
    from ctypes import wintypes
    import msvcrt

    class _HandleInfo(ctypes.Structure):
        _fields_ = [("attributes", wintypes.DWORD), ("created", wintypes.FILETIME),
            ("accessed", wintypes.FILETIME), ("written", wintypes.FILETIME),
            ("volume", wintypes.DWORD), ("size_high", wintypes.DWORD),
            ("size_low", wintypes.DWORD), ("links", wintypes.DWORD),
            ("index_high", wintypes.DWORD), ("index_low", wintypes.DWORD)]

    class _FindData(ctypes.Structure):
        _fields_ = [("attributes", wintypes.DWORD), ("created", wintypes.FILETIME),
            ("accessed", wintypes.FILETIME), ("written", wintypes.FILETIME),
            ("size_high", wintypes.DWORD), ("size_low", wintypes.DWORD),
            ("reserved0", wintypes.DWORD), ("reserved1", wintypes.DWORD),
            ("name", wintypes.WCHAR * 260), ("alternate", wintypes.WCHAR * 14)]

    # Separate DLL pointer objects; no shared prototype mutation with G1/old IO.
    _DLL = ctypes.WinDLL("kernel32", use_last_error=True)
    _ADVAPI = ctypes.WinDLL("advapi32", use_last_error=True)
    _HANDLE, _BOOL, _DWORD, _LPVOID = wintypes.HANDLE, wintypes.BOOL, wintypes.DWORD, wintypes.LPVOID

    def _prototype_original(dll, name, arguments, result):
        function = getattr(dll, name)
        function.argtypes, function.restype = arguments, result
        return function

    _CREATE = _prototype_original(_DLL, "CreateFileW", [wintypes.LPCWSTR, _DWORD, _DWORD,
        _LPVOID, _DWORD, _DWORD, _HANDLE], _HANDLE)
    _CURRENT_PROCESS = _prototype_original(_DLL, "GetCurrentProcess", [], _HANDLE)
    _DUPLICATE = _prototype_original(_DLL, "DuplicateHandle", [_HANDLE, _HANDLE, _HANDLE,
        ctypes.POINTER(_HANDLE), _DWORD, _BOOL, _DWORD], _BOOL)
    _HANDLE_INFO = _prototype_original(_DLL, "GetFileInformationByHandle",
        [_HANDLE, ctypes.POINTER(_HandleInfo)], _BOOL)
    _FINAL_PATH = _prototype_original(_DLL, "GetFinalPathNameByHandleW",
        [_HANDLE, wintypes.LPWSTR, _DWORD, _DWORD], _DWORD)
    _CLOSE_HANDLE = _prototype_original(_DLL, "CloseHandle", [_HANDLE], _BOOL)
    _LOCAL_FREE = _prototype_original(_DLL, "LocalFree", [wintypes.HLOCAL], wintypes.HLOCAL)
    _LOCAL_SIZE = _prototype_original(_DLL, "LocalSize", [wintypes.HLOCAL], ctypes.c_size_t)
    _FIND_FIRST = _prototype_original(_DLL, "FindFirstFileW",
        [wintypes.LPCWSTR, ctypes.POINTER(_FindData)], _HANDLE)
    _FIND_NEXT = _prototype_original(_DLL, "FindNextFileW",
        [_HANDLE, ctypes.POINTER(_FindData)], _BOOL)
    _FIND_CLOSE = _prototype_original(_DLL, "FindClose", [_HANDLE], _BOOL)
    _OPEN_TOKEN = _prototype_original(_ADVAPI, "OpenProcessToken",
        [_HANDLE, _DWORD, ctypes.POINTER(_HANDLE)], _BOOL)
    _TOKEN_INFO = _prototype_original(_ADVAPI, "GetTokenInformation",
        [_HANDLE, ctypes.c_int, _LPVOID, _DWORD, ctypes.POINTER(_DWORD)], _BOOL)
    # LPVOID prevents implicit unbounded LPWSTR.value conversion.
    _SID_TEXT = _prototype_original(_ADVAPI, "ConvertSidToStringSidW",
        [_LPVOID, ctypes.POINTER(_LPVOID)], _BOOL)
    _VALID_SID = _prototype_original(_ADVAPI, "IsValidSid", [_LPVOID], _BOOL)
    _SID_LENGTH = _prototype_original(_ADVAPI, "GetLengthSid", [_LPVOID], _DWORD)
    _SECURITY_INFO = _prototype_original(_ADVAPI, "GetSecurityInfo", [_HANDLE, ctypes.c_int,
        _DWORD, _LPVOID, _LPVOID, _LPVOID, _LPVOID, ctypes.POINTER(_LPVOID)], _DWORD)
    _SECURITY_OWNER = _prototype_original(_ADVAPI, "GetSecurityDescriptorOwner",
        [_LPVOID, ctypes.POINTER(_LPVOID), ctypes.POINTER(_BOOL)], _BOOL)
    _SECURITY_LENGTH = _prototype_original(_ADVAPI, "GetSecurityDescriptorLength", [_LPVOID], _DWORD)
    _SECURITY_VALID = _prototype_original(_ADVAPI, "IsValidSecurityDescriptor", [_LPVOID], _BOOL)
    _SECURITY_TEXT = _prototype_original(_ADVAPI, "ConvertSecurityDescriptorToStringSecurityDescriptorW",
        [_LPVOID, _DWORD, _DWORD, ctypes.POINTER(_LPVOID), ctypes.POINTER(_DWORD)], _BOOL)
    _CRT_OPEN, _CRT_GET = msvcrt.open_osfhandle, msvcrt.get_osfhandle
    _LAST_ERROR, _BYREF = ctypes.get_last_error, ctypes.byref
    _BUFFER, _BYTE_BUFFER = ctypes.create_unicode_buffer, ctypes.create_string_buffer
    _CAST, _POINTER, _WSTRING = ctypes.cast, ctypes.POINTER, ctypes.wstring_at
    # Original fixed factory selections happen only during bootstrap. Runtime
    # pointer readers use these same classes, not a mutable POINTER dispatcher.
    # Two fixed buffer sizes also close their actual array-class lookup graph.
    _WCHAR_POINTER, _LPVOID_POINTER = _POINTER(ctypes.c_wchar), _POINTER(_LPVOID)
    _WCHAR_BUFFER_TYPE, _BYTE_BUFFER_TYPE = ctypes.c_wchar * _MAX_PATH, ctypes.c_char * 65536
    _POINTER_CACHE = ctypes._pointer_type_cache
    _POINTER_SELECTIONS = ((ctypes.c_wchar, _WCHAR_POINTER), (_LPVOID, _LPVOID_POINTER))


class AuthorityReplicaSourceNativeError(ValueError):
    def __init__(self, suffix):
        self.code = "AUTHORITY_REPLICA_SOURCE_NATIVE1_" + suffix
        super().__init__(self.code)


def _fail(suffix, *, error=AuthorityReplicaSourceNativeError, new=ValueError.__new__,
        init=BaseException.__init__, storage=BaseException.__dict__["__dict__"],
        kind=type, dictionary=dict):
    if kind(suffix) is not str:
        suffix = "RECORD_CHANGED"
    value = new(error)
    code = "AUTHORITY_REPLICA_SOURCE_NATIVE1_" + suffix
    init(value, code)
    dictionary.__setitem__(storage.__get__(value, error), "code", code)
    raise value from None


class _SourceUnitKey:
    __slots__ = ("__weakref__",)
    def __init__(self, tag):
        if tag is not _TAG:
            _fail("GUARD_REQUIRED")


class _SourceHolderKey:
    __slots__ = ("__weakref__",)
    def __init__(self, tag):
        if tag is not _TAG:
            _fail("GUARD_REQUIRED")


class _SourceUnitNativeScope:
    __slots__ = ("key", "context", "registration", "operation_token", "owner", "budget",
        "phase", "holders", "scopes", "bases", "target_observation", "output_binding",
        "source_token", "source_record", "source_graph", "begin", "end", "normal_closed",
        "retired", "unknown", "global_acquires", "global_normal_closes", "global_current",
        "global_peak", "pending_identities", "incomplete_portable_identities")


class _SourceUnitHolder:
    __slots__ = ("key", "unit_key", "role", "kind", "purpose", "path", "parent_key",
        "ticket", "lease", "raw", "fd", "ownership", "closer", "transfer", "attempted",
        "outcome", "resource_id", "identity", "identity_sha256", "lease_released", "debt",
        "identity_pending", "portable_incomplete", "known_close_evidence")


def _classes_of_native_signature_original(functions, extra_classes=()):
    result, seen, todo = [], set(), list(extra_classes)
    for function in functions:
        todo.extend((type(function), *_GET(function, "argtypes"), _GET(function, "restype")))
    while todo:
        value = todo.pop()
        if not isinstance(value, type) or id(value) in seen:
            continue
        seen.add(id(value))
        result.append(value)
        todo.extend(_CLASS_GET(value, "__mro__"))
        todo.append(type(value))
        members = _CLASS_GET(value, "__dict__")
        fields = members.get("_fields_", ())
        if type(fields) in (list, tuple):
            todo.extend(field[1] for field in fields if type(field) is tuple and len(field) >= 2)
        for name in ("_type_", "_argtypes_", "_restype_"):
            nested = members.get(name)
            if isinstance(nested, type):
                todo.append(nested)
            elif type(nested) is tuple:
                todo.extend(x for x in nested if isinstance(x, type))
    return tuple(result)


_NATIVE_FUNCTIONS = () if os.name != "nt" else (_CREATE, _CURRENT_PROCESS, _DUPLICATE,
    _HANDLE_INFO, _FINAL_PATH, _CLOSE_HANDLE, _LOCAL_FREE, _LOCAL_SIZE, _FIND_FIRST, _FIND_NEXT,
    _FIND_CLOSE, _OPEN_TOKEN, _TOKEN_INFO, _SID_TEXT, _VALID_SID, _SID_LENGTH,
    _SECURITY_INFO, _SECURITY_OWNER, _SECURITY_TEXT, _SECURITY_LENGTH, _SECURITY_VALID)
_CTYPES_HELPER_FUNCTIONS = () if os.name != "nt" else (ctypes._cast, ctypes._wstring_at)
_CTYPES_FACTORY_CLASSES = () if os.name != "nt" else (_WCHAR_POINTER, _LPVOID_POINTER,
    _WCHAR_BUFFER_TYPE, _BYTE_BUFFER_TYPE, ctypes.c_char, ctypes.c_wchar, ctypes.Array)
_ABI_CLASSES = _classes_of_native_signature_original(_NATIVE_FUNCTIONS + _CTYPES_HELPER_FUNCTIONS,
    _CTYPES_FACTORY_CLASSES)
_NATIVE_SIGNATURES = tuple((f, _GET(f, "argtypes"), tuple(_GET(f, "argtypes")),
    _GET(f, "restype"), _GET(f, "errcheck")) for f in _NATIVE_FUNCTIONS + _CTYPES_HELPER_FUNCTIONS)
_NATIVE_META_PINS = tuple((cls, type(cls)) for cls in _ABI_CLASSES +
    (_SourceUnitKey, _SourceHolderKey, _SourceUnitNativeScope, _SourceUnitHolder,
        AuthorityReplicaSourceNativeError, _HASH_TYPE))
_ABI_STRUCTURE_FIELDS = tuple((cls, _CLASS_GET(cls, "__dict__")["_fields_"],
    tuple(_CLASS_GET(cls, "__dict__")["_fields_"])) for cls in _ABI_CLASSES
    if "_fields_" in _CLASS_GET(cls, "__dict__"))
_NATIVE_MODULE_LINKS = (sys.modules, tuple((m.__name__, m) for m in
    (builtins, ctypes, _ctypes, hashlib, json, ntpath, os, stat, sys, uuid, roles) +
    ((wintypes, msvcrt) if os.name == "nt" else ())))
_STRUCTURE_LAYOUT = () if os.name != "nt" else tuple((cls, ctypes.sizeof(cls),
    ctypes.alignment(cls), tuple((name, field, _GET(_CLASS_GET(cls, name), "offset"),
        _GET(_CLASS_GET(cls, name), "size")) for name, field in cls._fields_))
    for cls in (_HandleInfo, _FindData))


def _canonical_original(value):
    return _JSON_DUMPS(value, ensure_ascii=False, allow_nan=False,
        sort_keys=True, separators=(",", ":")).encode("utf-8")


def _detached_original(value):
    """Bounded builtin-only snapshots, never adoption into the registry."""
    nodes = [0]
    def visit(item, depth):
        nodes[0] += 1
        if depth > 32 or nodes[0] > 8001280:
            _fail("REPRESENTATION_LIMIT")
        kind = type(item)
        if kind in (str, int, bool, bytes) or item is None:
            return item
        if kind in (tuple, list):
            copied = [visit(x, depth + 1) for x in item]
            return tuple(copied) if kind is tuple else copied
        if kind is dict and all(type(k) is str for k in item):
            return {k: visit(v, depth + 1) for k, v in dict.items(item)}
        _fail("RECORD_CHANGED")
    return visit(value, 0)


def _result_fingerprint_original(value):
    """Pin detached result graphs without rehashing large original control bytes."""
    nodes = []
    def visit(item):
        kind = type(item)
        if kind is dict:
            if any(type(k) is not str for k in item):
                _fail("RECORD_CHANGED")
            nodes.append((item, tuple((k, v) for k, v in dict.items(item))))
            for value in dict.values(item):
                visit(value)
        elif kind in (tuple, list):
            nodes.append((item, tuple(item)))
            for value in item:
                visit(value)
        elif kind not in (str, int, bool, bytes, _SourceHolderKey) and item is not None:
            _fail("RECORD_CHANGED")
    visit(value)
    return tuple(nodes)


def _check_result_fingerprint_original(pin):
    for item, fields in pin:
        if type(item) is dict:
            if (len(item) != len(fields) or any(type(k) is not str for k in item)
                    or any(dict.get(item, k, _MISSING) is not v for k, v in fields)):
                _fail("RECORD_CHANGED")
        elif len(item) != len(fields) or any(a is not b for a, b in zip(item, fields)):
            _fail("RECORD_CHANGED")


def _make_ownership_kernel_original(*, kind=type, dictionary=dict, sequence=list,
        tuple_type=tuple, integer=int, text=str, length=len, identity=id,
        reverse=reversed, maximum=max, sum_values=sum, all_values=all, any_values=any,
        getattribute=object.__getattribute__, class_get=type.__getattribute__,
        new_error=ValueError.__new__, init_error=BaseException.__init__,
        error_storage=BaseException.__dict__["__dict__"], q_keys=_Q_KEYS,
        exception_type=BaseException, function_type=FunctionType, missing=_MISSING,
        object_type=object, zip_values=zip, clock=operation.time.monotonic_ns):
    """Authoritative registry/cleanup closure. Mirrors confer no ownership.

    Actual raw returns are installed here before lease settlement or ordinary
    validation. Cleanup uses captured immutable descriptors and original native
    closers after full pointer/prototype/marshalling pins; no normal helper,
    cancellation, guard, deadline or user callback dispatch occurs in cleanup.
    The closure itself is part of the declared original-entry trust root.
    """
    units, holders, permanent_gate = {}, {}, [False]
    append, get, put, pop = sequence.append, dictionary.get, dictionary.__setitem__, dictionary.pop
    unit_type, holder_type = _SourceUnitNativeScope, _SourceUnitHolder
    unit_key_type, holder_key_type, tag = _SourceUnitKey, _SourceHolderKey, _TAG
    error_type = AuthorityReplicaSourceNativeError
    us = {name: class_get(unit_type, "__dict__")[name] for name in unit_type.__slots__}
    hs = {name: class_get(holder_type, "__dict__")[name] for name in holder_type.__slots__}
    remembered, peek = operation._FIRST_ORIGINAL, operation._FIRST_PEEK
    integrity, actual_owner, same_owner = operation._integrity_original, operation._owner_original, operation._same_owner
    context_gate = operation._source_context_original
    context_type = operation._SourceUnitContext
    context_slots = {name: class_get(context_type, "__dict__")[name] for name in context_type.__slots__}
    registration_type = operation._OperationRegistration
    registration_context = class_get(registration_type, "__dict__")["source_context"]
    pure_validate = _validate_registration_state_original
    operation_error_type = operation.AuthorityReplicaOperationError
    debt_map = operation._ORIGINAL_CONTAINERS[4]
    original_last = class_get(operation._OperationRegistration, "__dict__")["budget_last"]
    # CPython actor observation for already-bound cleanup. No mutable Python
    # _owner/current_task dispatcher, loop hash/equality, callback or catalog.
    actual_pid, actual_thread = operation.os.getpid, operation.threading.get_ident
    running_loop = operation.asyncio.events._get_running_loop
    current_tasks = operation.asyncio.tasks._current_tasks
    raw_fd_close, raw_handle_close = _CLOSE, _CLOSE_HANDLE
    raw_find_close, raw_local_free = _FIND_CLOSE, _LOCAL_FREE
    close_pointers = () if raw_handle_close is None else (raw_handle_close, raw_find_close, raw_local_free)
    close_signatures = tuple((f, getattribute(f, "argtypes"), tuple_type(getattribute(f, "argtypes")),
        getattribute(f, "restype"), getattribute(f, "errcheck")) for f in close_pointers)
    close_classes = _classes_of_native_signature_original(close_pointers)
    close_class_pins = tuple_type((cls, kind(cls), class_get(cls, "__mro__"),
        tuple_type((base, tuple_type(class_get(base, "__dict__").items()))
            for base in class_get(cls, "__mro__"))) for cls in close_classes)
    close_python_pins = []
    for _cls, _meta, _mro, bases in close_class_pins:
        for _base, fields in bases:
            for _name, value in fields:
                if kind(value) is function_type:
                    append(close_python_pins, (value, getattribute(value, "__code__"),
                        getattribute(value, "__defaults__"), getattribute(value, "__kwdefaults__"),
                        getattribute(value, "__closure__")))

    def error(suffix):
        code = "AUTHORITY_REPLICA_SOURCE_NATIVE1_" + suffix
        value = new_error(error_type)
        init_error(value, code)
        put(error_storage.__get__(value, error_type), "code", code)
        return value

    def first(unit, exc):
        # Original operation minimal first root is sealed before callbacks.
        return remembered(unit["registration"], exc)

    def sync_unit(unit):
        obj = unit["object"]
        for name, descriptor in us.items():
            if name in unit:
                descriptor.__set__(obj, unit[name])

    def sync_holder(holder):
        obj = holder["object"]
        for name, descriptor in hs.items():
            if name in holder:
                descriptor.__set__(obj, holder[name])

    def resolve(key):
        unit = get(units, identity(key))
        if kind(key) is not unit_key_type or unit is None or unit["key"] is not key or unit["retired"]:
            raise error("GUARD_REQUIRED")
        return unit

    def resolve_holder(key):
        holder = get(holders, identity(key))
        if kind(key) is not holder_key_type or holder is None or holder["key"] is not key:
            raise error("GUARD_REQUIRED")
        resolve(holder["unit_key"])
        return holder

    def reject_callback():
        integrity()
        owner = actual_owner()
        for unit in tuple_type(dictionary.values(units)):
            if unit["inside_callback"] and same_owner(unit["owner"], owner):
                coded = new_error(operation_error_type)
                code = "AUTHORITY_REPLICA_OPERATION1_REENTRANT"
                init_error(coded, code)
                put(error_storage.__get__(coded, operation_error_type), "code", code)
                raise first(unit, coded)

    def minimum_owner():
        pid, thread, loop = actual_pid(), actual_thread(), running_loop()
        task = None
        if loop is not None:
            if kind(current_tasks) is not dictionary:
                raise error("OWNER_MISMATCH")
            for actual_loop, active_task in tuple_type(dictionary.items(current_tasks)):
                if actual_loop is loop:
                    # Original admission already bound the actual Task,
                    # including lawful subclasses. Cleanup observes only
                    # that same value by identity below; no hash/eq/class
                    # dispatcher can issue or adopt a different owner.
                    task = active_task
                    break
        return (pid, thread, task)

    def cleanup_gate(key):
        unit = admit_unit(resolve(key))
        bound = unit["owner"]
        def require_original_cleanup_actor():
            owner = minimum_owner()
            if owner[0] != bound[0] or owner[1] != bound[1] or owner[2] is not bound[2]:
                raise error("OWNER_MISMATCH")
            if unit["inside_callback"]:
                coded = new_error(operation_error_type)
                code = "AUTHORITY_REPLICA_OPERATION1_REENTRANT"
                init_error(coded, code)
                put(error_storage.__get__(coded, operation_error_type), "code", code)
                raise first(unit, coded)
        return require_original_cleanup_actor

    def admit_unit(unit):
        reject_callback()
        if (kind(unit) is not dictionary or any_values(kind(k) is not text for k in unit)
                or resolve(get(unit, "key")) is not unit):
            raise error("GUARD_REQUIRED")
        if not same_owner(unit["owner"], actual_owner()):
            raise error("OWNER_MISMATCH")
        return unit

    def admit_holder(holder):
        reject_callback()
        if (kind(holder) is not dictionary or any_values(kind(k) is not text for k in holder)
                or resolve_holder(get(holder, "key")) is not holder):
            raise error("GUARD_REQUIRED")
        admit_unit(holder["unit"])
        return holder

    def new_unit(context):
        reject_callback()
        if kind(context) is not context_type:
            raise error("GUARD_REQUIRED")
        token = context_slots["token"].__get__(context, context_type)
        if context_gate(token) is not context:
            raise error("GUARD_REQUIRED")
        if permanent_gate[0]:
            raise error("OWNERSHIP_UNKNOWN")
        key, obj = unit_key_type(tag), object.__new__(unit_type)
        scopes = {}
        for role in ("coordinator", "observer", "output", "source"):
            scopes[role] = {"scope_id": str(_UUID4()), "events": [], "holders": [],
                "Q": {name: 0 for name in q_keys}, "begin": None, "end": None,
                "acquires": 0, "normal_closes": 0, "current": 0, "peak": 0,
                "pending_identities": 0, "incomplete_portable_identities": 0,
                "unknown": 0, "normal_closed": False}
        unit = {"key": key, "object": obj, "context": context,
            "registration": context.registration, "operation_token": context.token,
            "owner": context.owner, "budget": context.budget, "budget_dict": context.budget_dict,
            "operation_id": context.inputs[4],
            "resource_pending_original": get(context.budget_dict, "_resource_pending"),
            "resource_leases_original": get(context.budget_dict, "_resource_leases"),
            "phase": "created", "holders": [], "scopes": scopes,
            "bases": None, "target_observation": None, "output_binding": None,
            "source_token": None, "source_record": None, "source_graph": None,
            "begin": None, "end": None, "normal_closed": False, "retired": False,
            "unknown": False, "global_acquires": 0, "global_normal_closes": 0,
            "global_current": 0, "global_peak": 0, "pending_identities": 0,
            "incomplete_portable_identities": 0,
            "read_events": [], "seek_events": [], "results": {}, "result_pins": {},
            "inside_callback": False, "terminal_depth": 0, "base_paths": {},
            "base_profiles": (), "target_data_key": None, "source_data_key": None,
            "output_keys": [], "output_absence": None, "source_holder_keys": [],
            "directory_identity_window": None, "global_event_order": [], "capacity_peak": 0}
        put(units, identity(key), unit)
        sync_unit(unit)
        return key

    def new_holder(unit, role, resource_kind, purpose, path=None, parent_key=None):
        if role not in unit["scopes"] or resource_kind not in _KINDS:
            raise error("PHASE_INVALID")
        scope = unit["scopes"][role]
        if scope["acquires"] >= 100000000:
            raise first(unit, error("REPRESENTATION_LIMIT"))
        key, obj = holder_key_type(tag), object.__new__(holder_type)
        holder = {"key": key, "object": obj, "unit_key": unit["key"], "unit": unit, "unit_state": unit,
            "role": role, "kind": resource_kind, "purpose": purpose, "path": path,
            "parent_key": parent_key, "ticket": None, "lease": None, "raw": None,
            "fd": None, "ownership": "empty", "closer": None,
            "transfer": "not_started", "attempted": False, "outcome": "empty",
            "resource_id": None, "identity": None, "identity_sha256": None,
            "lease_released": False, "debt": False, "native_called": False,
            "settlement_attempted": False, "lease_count": None,
            "acquired_event": None, "narrow": None, "private": False,
            "pathF": None, "handleF": None, "size": None, "certified_closed": False,
            "uncertain_acquire": False, "identity_pending": False,
            "portable_incomplete": False, "known_close_evidence": None}
        put(holders, identity(key), holder)
        append(unit["holders"], holder)
        append(scope["holders"], holder)
        sync_holder(holder)
        return holder

    def acquire(holder, raw, closer, portable):
        unit, scope = holder["unit"], holder["unit"]["scopes"][holder["role"]]
        holder["raw"], holder["closer"], holder["ownership"], holder["outcome"] = raw, closer, "owned", "acquired"
        holder["resource_id"] = "r" + str(scope["acquires"]).zfill(8)
        event = {"sequence": length(scope["events"]) + 1, "resource_id": holder["resource_id"],
            "kind": holder["kind"], "action": "acquired", "identity_sha256": None}
        holder["acquired_event"] = event
        append(scope["events"], event)
        append(unit["global_event_order"], (holder["role"], event))
        scope["acquires"] += 1
        scope["current"] += 1
        scope["peak"] = maximum(scope["peak"], scope["current"])
        unit["global_acquires"] += 1
        unit["global_current"] += 1
        unit["global_peak"] = maximum(unit["global_peak"], unit["global_current"])
        if portable:
            holder["identity_pending"] = True
            scope["pending_identities"] += 1
            unit["pending_identities"] += 1
        sync_holder(holder)
        sync_unit(unit)

    def finalize(holder, portable_identity, digest):
        if (holder["ownership"] != "owned" or holder["identity"] is not None
                or holder["identity_pending"] is not True
                or holder["kind"] not in ("directory_handle", "file_handle")):
            raise error("PHASE_INVALID")
        unit, scope = holder["unit"], holder["unit"]["scopes"][holder["role"]]
        holder["identity"], holder["identity_sha256"] = portable_identity, digest
        holder["acquired_event"]["identity_sha256"] = digest
        holder["identity_pending"] = False
        scope["pending_identities"] -= 1
        unit["pending_identities"] -= 1
        sync_holder(holder)
        sync_unit(unit)

    def retain(holder):
        unit = holder["unit"]
        permanent_gate[0] = True
        unit["unknown"] = True
        if not holder["debt"]:
            # Permanent bounded evidence contains no live raw HANDLE/fd/Task.
            holder["debt"] = True
            put(debt_map, ("source_unit", identity(unit["key"]), identity(holder["key"])),
                ("source_unit", unit["operation_id"], holder["role"],
                    holder["resource_id"], holder["kind"], holder["outcome"]))
        sync_holder(holder)
        sync_unit(unit)

    def unknown(holder, outcome):
        if holder["ownership"] != "unknown":
            holder["unit"]["scopes"][holder["role"]]["unknown"] += 1
        holder["ownership"], holder["outcome"] = "unknown", outcome
        retain(holder)

    def closer_intact():
        for function, args, fields, result, errcheck in close_signatures:
            if (getattribute(function, "argtypes") is not args
                    or getattribute(function, "restype") is not result
                    or getattribute(function, "errcheck") is not errcheck
                    or kind(args) is not sequence or length(args) != length(fields)
                    or any_values(a is not b for a, b in zip_values(args, fields))):
                return False
        for cls, meta, mro, bases in close_class_pins:
            if kind(cls) is not meta or class_get(cls, "__mro__") is not mro:
                return False
            for base, fields in bases:
                members = class_get(base, "__dict__")
                if (length(members) != length(fields) or any_values(kind(k) is not text for k in members)
                        or any_values(members.get(k, missing) is not v for k, v in fields)):
                    return False
        for function, code, defaults, keywords, closure in close_python_pins:
            if (getattribute(function, "__code__") is not code
                    or getattribute(function, "__defaults__") is not defaults
                    or getattribute(function, "__kwdefaults__") is not keywords
                    or getattribute(function, "__closure__") is not closure):
                return False
        return True

    def release(holder):
        unit, budget_dictionary, lease = holder["unit"], holder["unit"]["budget_dict"], holder["lease"]
        if holder["lease_released"]:
            return
        if kind(budget_dictionary) is not dictionary or any_values(kind(k) is not text for k in budget_dictionary):
            raise error("ACCOUNTING_CHANGED")
        pending, leases = get(budget_dictionary, "_resource_pending"), get(budget_dictionary, "_resource_leases")
        if (kind(pending) is not dictionary or kind(leases) is not dictionary
                or pending is not unit["resource_pending_original"] or leases is not unit["resource_leases_original"]
                or any_values(kind(k) is not object_type or kind(v) is not integer or v < 0 for k, v in dictionary.items(pending))
                or any_values(kind(k) is not object_type or kind(v) is not integer or v < 0 for k, v in dictionary.items(leases))
                or kind(lease) is not object_type or get(leases, lease) != holder["lease_count"]
                or kind(get(budget_dictionary, "_current_resources")) is not integer):
            raise error("ACCOUNTING_CHANGED")
        count = pop(leases, lease)
        put(budget_dictionary, "_current_resources", get(budget_dictionary, "_current_resources") - count)
        holder["lease_released"] = True
        sync_holder(holder)

    def close(holder):
        unit = holder["unit"]
        if holder["attempted"] or holder["ownership"] in ("closed", "unknown"):
            return
        if holder["ownership"] == "empty":
            if holder["ticket"] is not None and not holder["settlement_attempted"]:
                # An interrupted acquisition with no certified return cannot be
                # transformed to an empty success or issued another ticket.
                unknown(holder, "unknown_acquire")
                first(unit, error("OWNERSHIP_UNKNOWN"))
                return
            if holder["lease"] is not None:
                try:
                    release(holder)
                except exception_type as exc:
                    first(unit, exc)
                    unknown(holder, "unknown_release")
            return
        if (holder["ownership"] != "owned" or holder["resource_id"] is None
                or holder["acquired_event"] is None or holder["certified_closed"]):
            # A BOOL/NULL closer result cannot create ownership evidence for
            # a holder that never had the original observed acquire record.
            first(unit, error("OWNERSHIP_UNKNOWN"))
            unknown(holder, "unknown_close")
            return
        holder["attempted"] = True
        sync_holder(holder)
        try:
            if holder["closer"] == "fd":
                returned = raw_fd_close(holder["fd"])
                success = returned is None
            elif not closer_intact():
                raise error("RECORD_CHANGED")
            elif holder["closer"] == "handle":
                returned = raw_handle_close(holder["raw"])
                success = kind(returned) is integer and returned != 0
            elif holder["closer"] == "find":
                returned = raw_find_close(holder["raw"])
                success = kind(returned) is integer and returned != 0
            elif holder["closer"] == "local":
                returned = raw_local_free(holder["raw"])
                success = returned is None or kind(returned) is integer and returned == 0
            else:
                raise error("OWNERSHIP_UNKNOWN")
            if not success:
                raise error("CLOSE_FAILED")
        except exception_type as exc:
            first(unit, exc)
            unknown(holder, "unknown_close")
            return
        holder["ownership"], holder["outcome"], holder["certified_closed"] = "closed", "normal_closed", True
        # This evidence describes the actually observed unique closer return,
        # independently of an unavailable portable directory/file identity.
        # It contains no raw HANDLE/fd and is not a portable resource event.
        holder["known_close_evidence"] = (holder["resource_id"], holder["kind"],
            holder["closer"], returned)
        scope = unit["scopes"][holder["role"]]
        scope["current"] -= 1
        scope["normal_closes"] += 1
        unit["global_current"] -= 1
        unit["global_normal_closes"] += 1
        # A certified close without acquisition D still does not invent a
        # portable normal-close event or establish a successful summary.
        if holder["kind"] in ("directory_handle", "file_handle") and holder["identity"] is None:
            first(unit, error("IDENTITY_CHANGED"))
        else:
            event = {"sequence": length(scope["events"]) + 1, "resource_id": holder["resource_id"],
                "kind": holder["kind"], "action": "normal_closed", "identity_sha256": holder["identity_sha256"]}
            append(scope["events"], event)
            append(unit["global_event_order"], (holder["role"], event))
        try:
            release(holder)
        except exception_type as exc:
            first(unit, exc)
            unknown(holder, "unknown_release")
        else:
            if holder["identity_pending"]:
                # Only a real successful OS close AND original lease release
                # end this live identity obligation. Retain the uncompleted
                # observation and original acquire event without D/hash or a
                # fabricated portable normal-close event. Body first remains.
                if (holder["identity"] is not None or not holder["certified_closed"]
                        or not holder["lease_released"] or scope["pending_identities"] <= 0
                        or unit["pending_identities"] <= 0):
                    first(unit, error("ACCOUNTING_CHANGED"))
                    unknown(holder, "unknown_release")
                else:
                    holder["identity_pending"], holder["portable_incomplete"] = False, True
                    scope["pending_identities"] -= 1
                    unit["pending_identities"] -= 1
                    scope["incomplete_portable_identities"] += 1
                    unit["incomplete_portable_identities"] += 1
        sync_holder(holder)
        sync_unit(unit)

    def cleanup(unit, role=None):
        # Registry sequence is held in this closure, not taken from mirrors.
        unit["phase"] = "failed_cleanup" if peek(unit["registration"]) is not None else "closing"
        sync_unit(unit)
        for holder in reverse(unit["holders"]):
            if role is None or holder["role"] == role:
                try:
                    close(holder)
                except exception_type as exc:
                    first(unit, exc)
                    unknown(holder, "unknown_close")
        # Latch and retain every debt before retirement can remove any key.
        for holder in unit["holders"]:
            if holder["ownership"] == "unknown":
                retain(holder)
        for name, scope in dictionary.items(unit["scopes"]):
            if (role is None or role == name) and scope["begin"] is not None and scope["end"] is None:
                if (not scope["current"] and not scope["unknown"] and not scope["pending_identities"]
                        and all_values(h["ownership"] in ("empty", "closed")
                            and (h["ticket"] is None or h["lease_released"]) for h in scope["holders"])):
                    try:
                        scope["end"] = snapshot(unit)
                    except exception_type as exc:
                        first(unit, exc)

    def snapshot(unit):
        # Direct original-ledger functional snapshot: no mutable budget method
        # or callback dispatch from trusted cleanup after actual role closure.
        values = unit["budget_dict"]
        required = ("_read_bytes", "_source_read_bytes", "_read_calls", "_source_read_calls", "_seek_calls",
            "_confirmed", "_write_calls", "_uncertain_reads", "_uncertain_source_reads", "_uncertain_writes",
            "_started", "_last_tick", "_current_resources", "_peak_resources", "_pending_resources", "_uncertain_resources")
        if kind(values) is not dictionary or any_values(kind(k) is not text for k in values) or any_values(kind(get(values, k)) is not integer or get(values, k) < 0 for k in required):
            raise error("ACCOUNTING_CHANGED")
        tick = clock()
        if kind(tick) is not integer or kind(get(values, "_last_tick")) is not integer or tick < get(values, "_last_tick"):
            raise error("ACCOUNTING_CHANGED")
        put(values, "_last_tick", tick)
        registration = unit["registration"]
        # Mirror slot uses its original immutable C member descriptor.
        original_last.__set__(registration, tick)
        return {"read_bytes": get(values, "_read_bytes"), "source_read_bytes": get(values, "_source_read_bytes"),
            "read_calls": get(values, "_read_calls"), "source_read_calls": get(values, "_source_read_calls"),
            "seek_calls": get(values, "_seek_calls"), "confirmed_write_bytes": get(values, "_confirmed"),
            "write_calls": get(values, "_write_calls"), "uncertain_read_calls": get(values, "_uncertain_reads"),
            "uncertain_source_read_calls": get(values, "_uncertain_source_reads"),
            "uncertain_write_bytes": get(values, "_uncertain_writes"), "elapsed_ns": tick - get(values, "_started"),
            "current_resources": get(values, "_current_resources"), "peak_resources": get(values, "_peak_resources"),
            "pending_resources": get(values, "_pending_resources"), "uncertain_resource_acquires": get(values, "_uncertain_resources")}

    def retirement(unit, success):
        cleanup(unit)
        if any_values(h["ownership"] == "unknown" for h in unit["holders"]):
            permanent_gate[0] = True
        unit["retired"] = True
        unit["phase"] = "retired_closed" if success and unit["normal_closed"] else "retired_failed"
        sync_unit(unit)
        for holder in unit["holders"]:
            pop(holders, identity(holder["key"]), None)
        pop(units, identity(unit["key"]), None)

    def trusted_callbacks(key):
        unit = resolve(key)
        if unit["phase"] != "created":
            raise error("PHASE_INVALID")
        def cleanup_original():
            return cleanup(unit)
        def retire_original(success):
            return retirement(unit, success)
        return cleanup_original, retire_original

    def source_callbacks(key, source_token):
        unit = resolve(key)
        if unit["source_token"] is not source_token:
            raise error("GUARD_REQUIRED")
        original_begin = unit["scopes"]["source"]["begin_actual_items"]
        def cleanup_source_original():
            return cleanup(unit, "source")
        def audit_source_original():
            scope = unit["scopes"]["source"]
            if (scope["current"] or scope["unknown"] or scope["pending_identities"]
                    or any_values(h["ownership"] not in ("empty", "closed")
                        or h["ticket"] is not None and not h["lease_released"] for h in scope["holders"])):
                raise first(unit, error("UNSETTLED"))
            if scope["end"] is None or scope["acquires"] != scope["normal_closes"]:
                raise first(unit, error("UNSETTLED"))
            scope["normal_closed"] = True
            return {"normal_closed": True, "Q": {k: v for k, v in dictionary.items(scope["Q"])},
                "begin": {k: v for k, v in original_begin},
                "end": {k: v for k, v in dictionary.items(scope["end"])},
                "resource_acquires": scope["acquires"], "resource_normal_closes": scope["normal_closes"],
                "current_resources": scope["current"], "pending_identities": scope["pending_identities"],
                "unknown_resources": scope["unknown"], "peak_owned_resources": scope["peak"],
                "portable_identity_complete": scope["incomplete_portable_identities"] == 0,
                "incomplete_portable_identities": scope["incomplete_portable_identities"],
                "incomplete_acquisitions": tuple_type((h["resource_id"], h["kind"],
                    h["acquired_event"]["identity_sha256"], h["known_close_evidence"], h["lease_released"])
                    for h in scope["holders"] if h["portable_incomplete"])}
        return cleanup_source_original, audit_source_original

    def blocked():
        return permanent_gate[0]

    def public_resolve(key):
        reject_callback()
        return admit_unit(resolve(key))

    def public_holder(key):
        reject_callback()
        return admit_holder(resolve_holder(key))

    def public_new_holder(unit, role, resource_kind, purpose, path=None, parent_key=None):
        admit_unit(unit)
        if (kind(role) is not text or kind(resource_kind) is not text or kind(purpose) is not text
                or path is not None and kind(path) is not text):
            raise error("GUARD_REQUIRED")
        if parent_key is not None:
            parent = resolve_holder(parent_key)
            if parent["unit"] is not unit or parent["ownership"] != "owned":
                raise error("GUARD_REQUIRED")
        return new_holder(unit, role, resource_kind, purpose, path, parent_key)

    def public_acquire(holder, raw, closer, portable):
        admit_holder(holder)
        if kind(raw) is not integer or kind(closer) is not text or kind(portable) is not bool or holder["ticket"] is None or not holder["native_called"] or holder["ownership"] != "empty":
            raise error("PHASE_INVALID")
        return acquire(holder, raw, closer, portable)

    def public_finalize(holder, portable_identity, digest):
        admit_holder(holder)
        return finalize(holder, portable_identity, digest)

    def public_unknown(holder, outcome):
        admit_holder(holder)
        if kind(outcome) is not text:
            raise error("PHASE_INVALID")
        return unknown(holder, outcome)

    def public_close(holder):
        admit_holder(holder)
        return close(holder)

    def public_cleanup(unit, role=None):
        admit_unit(unit)
        if role is not None and (kind(role) is not text or role not in unit["scopes"]):
            raise error("PHASE_INVALID")
        return cleanup(unit, role)

    def public_retirement(unit, success):
        admit_unit(unit)
        if kind(success) is not bool:
            raise error("PHASE_INVALID")
        return retirement(unit, success)

    def public_retain(holder):
        admit_holder(holder)
        if holder["ownership"] != "unknown":
            raise error("PHASE_INVALID")
        return retain(holder)

    def public_sync_unit(unit):
        admit_unit(unit)
        return sync_unit(unit)

    def public_sync_holder(holder):
        admit_holder(holder)
        return sync_holder(holder)

    def public_trusted_callbacks(key):
        admit_unit(resolve(key))
        return trusted_callbacks(key)

    def public_source_callbacks(key, source_token):
        admit_unit(resolve(key))
        return source_callbacks(key, source_token)

    def bound_registration(registration):
        # Pure original callback audit cannot expose the authority dict. It
        # captures the unexported resolver and fixed validator, never accepts
        # a callback/consumer and remains valid inside the original latch.
        integrity()
        if kind(registration) is not registration_type:
            raise error("GUARD_REQUIRED")
        context = registration_context.__get__(registration, registration_type)
        if context is None:
            return None
        if kind(context) is not context_type:
            raise error("GUARD_REQUIRED")
        key = context_slots["unit_key"].__get__(context, context_type)
        if key is None:
            return None
        unit = resolve(key)
        if not same_owner(unit["owner"], actual_owner()):
            raise error("OWNER_MISMATCH")
        pure_validate(registration, unit)
        return unit

    def public_validate_registration(registration):
        bound_registration(registration)

    def public_callback_allowed(registration):
        unit = bound_registration(registration)
        if unit is None:
            return False
        return (not unit["pending_identities"] and unit["budget_dict"]["_active"] is None
            and not unit["budget_dict"]["_resource_pending"] and unit["terminal_depth"] == 0
            and unit["budget_dict"]["_terminal"] == 0 and not unit["unknown"]
            and unit["phase"] not in ("closing", "failed_cleanup", "retired_closed", "retired_failed"))

    return (new_unit, public_resolve, public_holder, public_new_holder, public_acquire,
        public_finalize, public_unknown, public_close, public_cleanup, public_retirement,
        public_retain, public_trusted_callbacks, public_source_callbacks, blocked,
        reject_callback, public_sync_unit, public_sync_holder,
        public_validate_registration, public_callback_allowed, cleanup_gate)


def _validate_pointer_selections_original():
    """Pure check of only the two actual selections; unrelated cache is live.

    Iterate keys by identity so a hostile nonselected key cannot execute hash
    or equality. No clear/reset, dynamic factory call or caller class adoption.
    Original selected class/metaclass/MRO/descriptor/code pins are separately
    covered by the combined operation ABI graph before native effects.
    """
    if _BACKEND_NAME != "nt":
        return
    if (type(_POINTER_CACHE) is not dict
            or ctypes._pointer_type_cache is not _POINTER_CACHE
            or _ctypes._pointer_type_cache is not _POINTER_CACHE
            or ctypes._sys is not sys):
        _fail("RECORD_CHANGED")
    selected = [None, None]
    try:
        for key, value in dict.items(_POINTER_CACHE):
            for index, (original_type, original_class) in enumerate(_POINTER_SELECTIONS):
                if key is original_type:
                    selected[index] = value
    except RuntimeError:
        _fail("RECORD_CHANGED")
    if any(value is not original_class for value, (_original_type, original_class)
            in zip(selected, _POINTER_SELECTIONS)):
        _fail("RECORD_CHANGED")


def _require_supported_original():
    operation._integrity_original()
    _validate_pointer_selections_original()
    if (_BACKEND_NAME != "nt" or os.name != "nt"
            or _INTERPRETER != ("cpython", (3, 12, 10), 8)
            or (sys.implementation.name, tuple(sys.version_info[:3]), ctypes.sizeof(ctypes.c_void_p)) != _INTERPRETER
            or sys.maxsize != 2**63 - 1 or ctypes.sizeof(ctypes.c_wchar) != 2
            or ctypes.sizeof(_HANDLE) != 8 or ctypes.sizeof(_BOOL) != 4 or ctypes.sizeof(_DWORD) != 4
            or ctypes.sizeof(_WCHAR_POINTER) != 8 or ctypes.sizeof(_LPVOID_POINTER) != 8
            or _CLASS_GET(_WCHAR_POINTER, "__dict__").get("_type_") is not ctypes.c_wchar
            or _CLASS_GET(_LPVOID_POINTER, "__dict__").get("_type_") is not _LPVOID
            or _CLASS_GET(_WCHAR_BUFFER_TYPE, "__dict__").get("_type_") is not ctypes.c_wchar
            or _CLASS_GET(_WCHAR_BUFFER_TYPE, "__dict__").get("_length_") != _MAX_PATH
            or _CLASS_GET(_BYTE_BUFFER_TYPE, "__dict__").get("_type_") is not ctypes.c_char
            or _CLASS_GET(_BYTE_BUFFER_TYPE, "__dict__").get("_length_") != 65536
            or len(_STRUCTURE_LAYOUT) != 2
            or _STRUCTURE_LAYOUT[0][1:3] != (52, 4)
            or _STRUCTURE_LAYOUT[1][1:3] != (592, 4)
            or tuple((name, offset, size) for name, _field, offset, size in _STRUCTURE_LAYOUT[1][3]) != (
                ("attributes", 0, 4), ("created", 4, 8), ("accessed", 12, 8), ("written", 20, 8),
                ("size_high", 28, 4), ("size_low", 32, 4), ("reserved0", 36, 4), ("reserved1", 40, 4),
                ("name", 44, 520), ("alternate", 564, 28))):
        _fail("BACKEND_UNSUPPORTED")


def _dos_chain_original(path):
    if (type(path) is not str or len(path) < 3 or len(path) >= _MAX_PATH
            or path[0] not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
            or path[1:3] != ":\\" or "/" in path or "\0" in path or path.startswith("\\")):
        _fail("PATH_UNSUPPORTED")
    if path.endswith("\\") and len(path) != 3:
        _fail("PATH_UNSUPPORTED")
    pieces = [] if len(path) == 3 else path[3:].split("\\")
    for piece in pieces:
        if (not piece or piece in (".", "..") or piece[-1] in " ."
                or len(piece.encode("utf-8")) > 255
                or any(ord(c) < 32 or 127 <= ord(c) <= 159 or c in '<>:"/\\|?*' for c in piece)
                or piece.split(".", 1)[0].upper() in roles._RESERVED):
            _fail("PATH_UNSUPPORTED")
    chain = [path[:3]]
    for piece in pieces:
        chain.append(chain[-1] + ("" if len(chain) == 1 else "\\") + piece)
    if len(chain) > _MAX_CHAIN:
        _fail("REPRESENTATION_LIMIT")
    return tuple(chain)


def _components_original(path):
    return tuple(piece.casefold() for piece in _dos_chain_original(path))


def _related_paths_original(left, right):
    a, b = _components_original(left), _components_original(right)
    return a[:len(b)] == b or b[:len(a)] == a


def _plan_source_unit_original(inputs):
    """Pure fixed paths only; no metadata or permission observation."""
    if type(inputs) is not tuple or len(inputs) != 12 or inputs[3] != "preview" or inputs[7] != "readonly":
        _fail("TYPE_INVALID")
    profile_plans = []
    for paths in inputs[9]:
        if type(paths) is not tuple or len(paths) != 4:
            _fail("RECORD_CHANGED")
        roots = (ntpath.dirname(paths[0]), *paths[1:])
        profile_plans.append((paths, tuple(_dos_chain_original(path) for path in roots)))
    selector = inputs[11]
    if type(selector) is not tuple or len(selector) != 6 or selector[0] not in ("profile_raw2", "detached_raw2"):
        _fail("TYPE_INVALID")
    source_chain = _dos_chain_original(selector[1])
    target_data = profile_plans[0][1][0][-1]
    output_path = ntpath.join(target_data, _OUTPUT_FAMILY, inputs[4])
    _dos_chain_original(output_path)
    if _related_paths_original(output_path, selector[1]):
        _fail("SOURCE_OUTPUT_ALIAS")
    return (tuple(profile_plans), source_chain, output_path)


def _first_original(unit, exc):
    return operation._FIRST_ORIGINAL(unit["registration"], exc)


def _registered_unit_original(unit):
    _NO_CALLBACK()
    if (type(unit) is not dict or any(type(k) is not str for k in unit)
            or _RESOLVE_UNIT(dict.get(unit, "key")) is not unit):
        _fail("GUARD_REQUIRED")
    return unit


def _registered_holder_original(holder):
    _NO_CALLBACK()
    if (type(holder) is not dict or any(type(k) is not str for k in holder)
            or _RESOLVE_HOLDER(dict.get(holder, "key")) is not holder):
        _fail("GUARD_REQUIRED")
    return holder


def _unit_state_original(unit_key):
    _NO_CALLBACK()
    operation._integrity_original()
    unit = _RESOLVE_UNIT(unit_key)
    context = operation._source_context_original(unit["operation_token"])
    if context is not unit["context"] or context.unit_key is not unit_key:
        _fail("RECORD_CHANGED")
    if not operation._same_owner(unit["owner"], operation._owner_original()):
        _fail("OWNER_MISMATCH")
    return unit


def _holder_state_original(holder_key):
    holder = _RESOLVE_HOLDER(holder_key)
    if _unit_state_original(holder["unit_key"]) is not holder["unit"]:
        _fail("RECORD_CHANGED")
    return holder


def _validate_new_unit_original(context, unit_key):
    unit = _RESOLVE_UNIT(unit_key)
    if (unit["context"] is not context or unit["registration"] is not context.registration
            or unit["operation_token"] is not context.token or unit["phase"] != "created"
            or unit["holders"] or unit["read_events"] or context.unit_key is not None):
        _fail("RECORD_CHANGED")
    return True


def _validate_registration_state_original(registration, unit):
    """Pure exact event/lease/Q reconciliation; NO operation admission recursion."""
    _validate_pointer_selections_original()
    context = registration.source_context
    if unit["registration"] is not registration or unit["context"] is not context or unit["budget"] is not registration.budget:
        _fail("RECORD_CHANGED")
    obj = unit["object"]
    for name in _SourceUnitNativeScope.__slots__:
        if name in unit and _GET(obj, name) is not unit[name]:
            _fail("RECORD_CHANGED")
    budget_dict = unit["budget_dict"]
    if _GET(unit["budget"], "__dict__") is not budget_dict or any(type(k) is not str for k in budget_dict):
        _fail("ACCOUNTING_CHANGED")
    events = unit["read_events"]
    read_calls = sum(int(event["begun"]) for event in events)
    source_calls = sum(int(event["begun"] and event["source"]) for event in events)
    returned = [event for event in events if event["settlement_started"]]
    expected = {
        "_read_bytes": sum(event["actual_bytes"] for event in returned if event["actual_bytes"] is not None),
        "_source_read_bytes": sum(event["actual_bytes"] for event in returned if event["source"] and event["actual_bytes"] is not None),
        "_read_calls": read_calls, "_source_read_calls": source_calls,
        "_uncertain_reads": sum(int(event["actual_bytes"] is None) for event in returned),
        "_uncertain_source_reads": sum(int(event["source"] and event["actual_bytes"] is None) for event in returned),
        "_seek_calls": len(unit["seek_events"]), "_confirmed": 0, "_write_calls": 0, "_uncertain_writes": 0}
    if any(budget_dict.get(k) != v or type(budget_dict.get(k)) is not int for k, v in expected.items()):
        _fail("ACCOUNTING_CHANGED")
    active = budget_dict["_active"]
    pending_events = [e for e in events if e["begun"] and not e["settlement_started"]]
    if pending_events:
        if (len(pending_events) != 1 or type(active) is not tuple or len(active) != 4
                or active[0] is not pending_events[0]["ticket"] or active[1] != "read"
                or active[2:] != (pending_events[0]["requested"], pending_events[0]["source"])):
            _fail("ACCOUNTING_CHANGED")
    elif active is not None:
        _fail("ACCOUNTING_CHANGED")
    holders = unit["holders"]
    pending = {h["ticket"]: 1 for h in holders if h["ticket"] is not None and not h["settlement_attempted"]}
    leases = {h["lease"]: h["lease_count"] for h in holders if h["lease"] is not None and not h["lease_released"]}
    if (type(budget_dict["_resource_pending"]) is not dict or type(budget_dict["_resource_leases"]) is not dict
            or budget_dict["_resource_pending"] != pending or budget_dict["_resource_leases"] != leases
            or budget_dict["_pending_resources"] != len(pending)
            or budget_dict["_current_resources"] != sum(leases.values())
            or budget_dict["_peak_resources"] != unit["capacity_peak"]
            or budget_dict["_uncertain_resources"] != sum(int(h["uncertain_acquire"]) for h in holders)):
        _fail("ACCOUNTING_CHANGED")
    for role, scope in unit["scopes"].items():
        actual = {name: 0 for name in _Q_KEYS}
        for event in events:
            if event["role"] != role or not event["Q_settled"]:
                continue
            actual["read_calls"] += 1
            actual["source_read_calls"] += int(event["source"])
            if event["actual_bytes"] is not None:
                size = event["actual_bytes"]
                actual["read_bytes"] += size
                actual["source_read_bytes"] += size * int(event["source"])
                actual[event["partition"]] += size
            else:
                actual["uncertain_read_calls"] += 1
                actual["uncertain_source_read_calls"] += int(event["source"])
        actual["seek_calls"] = sum(int(event[0] == role) for event in unit["seek_events"])
        if scope["Q"] != actual:
            _fail("ACCOUNTING_CHANGED")
        if (scope["acquires"] != sum(int(h["resource_id"] is not None) for h in scope["holders"])
                or scope["normal_closes"] != sum(int(h["certified_closed"]) for h in scope["holders"])
                or scope["current"] != scope["acquires"] - scope["normal_closes"]
                or scope["pending_identities"] != sum(int(h["identity_pending"]) for h in scope["holders"])
                or scope["incomplete_portable_identities"] != sum(int(h["portable_incomplete"]) for h in scope["holders"])):
            _fail("ACCOUNTING_CHANGED")
        for holder in scope["holders"]:
            if holder["portable_incomplete"] and (holder["identity_pending"]
                    or holder["identity"] is not None or holder["identity_sha256"] is not None
                    or holder["acquired_event"]["identity_sha256"] is not None
                    or not holder["certified_closed"] or not holder["lease_released"]
                    or holder["known_close_evidence"] is None):
                _fail("ACCOUNTING_CHANGED")
    if unit["global_acquires"] != sum(s["acquires"] for s in unit["scopes"].values()) or unit["global_normal_closes"] != sum(s["normal_closes"] for s in unit["scopes"].values()):
        _fail("ACCOUNTING_CHANGED")
    if (unit["pending_identities"] != sum(s["pending_identities"] for s in unit["scopes"].values())
            or unit["incomplete_portable_identities"] != sum(s["incomplete_portable_identities"] for s in unit["scopes"].values())):
        _fail("ACCOUNTING_CHANGED")
    for holder in holders:
        for name in _SourceUnitHolder.__slots__:
            if name in holder and _GET(holder["object"], name) is not holder[name]:
                _fail("RECORD_CHANGED")
    for pin in unit["result_pins"].values():
        _check_result_fingerprint_original(pin)


def _validate_registration_original(registration):
    return _VALIDATE_BOUND_REGISTRATION(registration)


def _require_no_callback_original():
    return _NO_CALLBACK()


def _failure_blocked_original():
    return _BLOCKED()


def _callback_allowed_original(registration):
    return _CALLBACK_ALLOWED(registration)


def _begin_callback_original(registration):
    _NO_CALLBACK()
    context = registration.source_context
    if context is None or context.unit_key is None:
        _fail("PHASE_INVALID")
    unit = _RESOLVE_UNIT(context.unit_key)
    if not _callback_allowed_original(registration):
        _fail("PHASE_INVALID")
    unit["inside_callback"] = True
    def leave_original_callback():
        unit["inside_callback"] = False
    return leave_original_callback


def _checkpoint_allowed_original(context, source_token, stage):
    if context.unit_key is None:
        if stage != "bases" or source_token is not None:
            _fail("PHASE_INVALID")
        return True
    unit = _RESOLVE_UNIT(context.unit_key)
    if unit["context"] is not context or source_token is not None and unit["source_token"] is not source_token:
        _fail("PHASE_INVALID")
    phases = {"bases": ("created",), "target_begin": ("bases_open",), "target_observed": ("target_observed",),
        "output_bound": ("output_bound",), "source_begin": ("output_bound",),
        "location": ("output_bound", "source_located"), "metadata": ("source_located", "source_metadata"),
        "before_body": ("source_metadata",), "captured": ("source_captured",),
        "selection": ("source_captured",), "final_metadata": ("source_final_metadata",),
        "before_close": ("source_final_metadata",)}
    source_stages = ("source_begin", "location", "metadata", "before_body", "captured", "selection", "final_metadata", "before_close")
    if (stage not in phases or unit["phase"] not in phases[stage]
            or stage in source_stages and (source_token is None or unit["source_token"] is not source_token)
            or stage not in source_stages and source_token is not None
            or not _callback_allowed_original(context.registration)):
        _fail("PHASE_INVALID")
    if stage in source_stages:
        record_phase = unit["source_record"].phase
        paired = {
            "source_begin": (("output_bound", "begin"),),
            "location": (("output_bound", "begin"), ("output_bound", "opening"),
                ("source_located", "opening"), ("source_located", "location")),
            "metadata": (("source_located", "location"), ("source_located", "metadata"), ("source_metadata", "metadata")),
            "before_body": (("source_metadata", "metadata"), ("source_metadata", "before_body")),
            "captured": (("source_captured", "reading"), ("source_captured", "captured")),
            "selection": (("source_captured", "captured"), ("source_captured", "selection")),
            "final_metadata": (("source_final_metadata", "selection"), ("source_final_metadata", "final_metadata")),
            "before_close": (("source_final_metadata", "final_metadata"), ("source_final_metadata", "before_close"))}
        if (unit["phase"], record_phase) not in paired[stage]:
            _fail("PHASE_INVALID")
    return True


def _trusted_callbacks_original(unit_key):
    _NO_CALLBACK()
    return _TRUSTED_CALLBACKS(unit_key)


def _source_cleanup_callbacks_original(unit_key, source_token):
    _NO_CALLBACK()
    unit = _unit_state_original(unit_key)
    if unit["source_token"] is None:
        from . import personal_authority_replica_source as source
        record = source._native_context_original(source_token, unit["operation_token"])
        unit["source_token"], unit["source_record"] = source_token, record
        _SYNC_UNIT(unit)
    record = unit["source_record"]
    scope = unit["scopes"]["source"]
    if scope["begin"] is None:
        if (type(record.begin) is not dict or any(type(k) is not str for k in record.begin)
                or any(type(v) is not int or v < 0 for v in record.begin.values())):
            _fail("SOURCE_BEGIN_INVALID")
        scope["begin"], scope["scope_id"] = record.begin, record.scope_id
        scope["begin_actual_items"] = tuple(dict.items(record.begin))
        unit["result_pins"]["source_begin"] = _result_fingerprint_original(record.begin)
    return _SOURCE_CALLBACKS(unit_key, source_token)


def _cleanup_callback_gate_original(operation_token):
    """Bind once before callback; returned actor/latch gate has no normal graph.

    Fixed source construction captures this original producer and retains the
    returned gate in its hidden responsibility closure. No caller provides a
    token factory, context, owner, closer, body, backend or clear operation.
    """
    _NO_CALLBACK()
    context = operation._source_context_original(operation_token)
    if context.unit_key is None:
        _fail("PHASE_INVALID")
    return _MINIMAL_CLEANUP_GATE(context.unit_key)


def _retain_state_original(holder):
    if type(holder) is not dict or _RESOLVE_HOLDER(holder.get("key")) is not holder:
        _fail("GUARD_REQUIRED")
    if holder["ownership"] != "unknown":
        _fail("PHASE_INVALID")
    return _RETAIN(holder)


def _close_source_scope_original(unit_key, source_token):
    unit = _unit_state_original(unit_key)
    if unit["source_token"] is not source_token:
        _fail("GUARD_REQUIRED")
    return _CLEANUP(unit, "source")


def _open_source_unit_original(operation_token):
    _NO_CALLBACK()
    context = operation._source_context_original(operation_token)
    if context.phase != "source_native_ready" or context.unit_key is not None:
        _fail("PHASE_INVALID")
    _require_supported_original()
    return _NEW_UNIT(context)


def _normal_gate_original(unit, *, callback=False):
    _registered_unit_original(unit)
    operation._integrity_original()
    _validate_pointer_selections_original()
    if unit["unknown"] or _BLOCKED():
        _fail("OWNERSHIP_UNKNOWN")
    operation._source_budget_original(unit["registration"])
    unit["budget"].check(callback=callback)
    unit["registration"].budget_last = unit["budget_dict"]["_last_tick"]
    operation._source_budget_original(unit["registration"])


def _enter_native_original(unit):
    _normal_gate_original(unit)
    if unit["terminal_depth"] or unit["budget_dict"]["_terminal"]:
        _fail("PHASE_INVALID")
    unit["budget_dict"]["_terminal"] = 1
    unit["terminal_depth"] = 1


def _leave_native_original(unit):
    try:
        if unit["terminal_depth"] != 1 or unit["budget_dict"]["_terminal"] != 1:
            _fail("ACCOUNTING_CHANGED")
        unit["budget_dict"]["_terminal"] = 0
        unit["terminal_depth"] = 0
        _normal_gate_original(unit)
    except BaseException as exc:
        raise _first_original(unit, exc)


def _begin_holder_original(unit, role, kind, purpose, path=None, parent_key=None):
    # Every empty holder is authoritative before begin_resources/native.
    holder = _NEW_HOLDER(unit, role, kind, purpose, path, parent_key)
    holder["ticket"] = unit["budget"].begin_resources(1)
    unit["registration"].budget_last = unit["budget_dict"]["_last_tick"]
    _SYNC_HOLDER(holder)
    return holder


def _settle_holder_original(holder, exception, acquired):
    unit = holder["unit"]
    if holder["settlement_attempted"] or holder["ticket"] is None:
        _fail("ACCOUNTING_CHANGED")
    holder["settlement_attempted"] = True
    if exception is None:
        returned = unit["budget"].end_resources(holder["ticket"], acquired)
    else:
        returned = unit["budget"].fail_resources(holder["ticket"], exception, acquired=acquired)
    if type(returned) is not tuple or len(returned) != 2 or type(returned[0]) is not object:
        _UNKNOWN(holder, "unknown_acquire")
        _fail("ACCOUNTING_CHANGED")
    # Retain the lease before returning original first or validating anything.
    holder["lease"], holder["lease_count"] = returned[0], 1 if acquired is None else acquired
    holder["uncertain_acquire"] = acquired is None
    unit["capacity_peak"] = max(unit["capacity_peak"], sum(h["lease_count"] for h in unit["holders"]
        if h["lease"] is not None and not h["lease_released"]))
    _SYNC_HOLDER(holder)
    if acquired is None:
        _UNKNOWN(holder, "unknown_acquire")
    if returned[1] is not None:
        raise _first_original(unit, returned[1])


def _valid_handle_original(raw):
    return type(raw) is int and 0 < raw < _INVALID_HANDLE


def _confirmed_acquire_original(holder, raw, closer, portable):
    # Save ownership immediately and always obtain the original lease before
    # delivering an event/identity-recording fault. No retry of native/ticket.
    pending_error = None
    try:
        _ACQUIRED(holder, raw, closer, portable)
    except BaseException as exc:
        pending_error = _first_original(holder["unit"], exc)
    _settle_holder_original(holder, None, 1)
    if pending_error is not None:
        raise pending_error


def _create_handle_original(unit, role, kind, purpose, path, parent_key, access, flags):
    holder = _begin_holder_original(unit, role, kind, purpose, path, parent_key)
    holder["native_called"] = True
    try:
        raw = _CREATE(path, access, 3, None, 3, flags, None)
        last_error = _LAST_ERROR()
    except BaseException as exc:
        _UNKNOWN(holder, "unknown_acquire")
        _settle_holder_original(holder, exc, None)
        raise
    holder["raw"], holder["native_error"] = raw, last_error
    _SYNC_HOLDER(holder)
    if type(raw) is int and raw in (-1, _INVALID_HANDLE):
        holder["outcome"] = "no_acquire"
        _settle_holder_original(holder, AuthorityReplicaSourceNativeError("NATIVE_FAILED"), 0)
    elif _valid_handle_original(raw):
        _confirmed_acquire_original(holder, raw, "handle", kind in ("directory_handle", "file_handle"))
    else:
        _UNKNOWN(holder, "unknown_acquire")
        _settle_holder_original(holder, AuthorityReplicaSourceNativeError("OWNERSHIP_UNKNOWN"), None)
    return holder


def _transfer_original(holder):
    _registered_holder_original(holder)
    if holder["ownership"] != "owned" or holder["closer"] != "handle" or holder["transfer"] != "not_started":
        _fail("PHASE_INVALID")
    holder["transfer"] = "pending"
    _SYNC_HOLDER(holder)
    try:
        fd = _CRT_OPEN(holder["raw"], os.O_RDONLY | os.O_BINARY)
    except OSError:
        holder["transfer"] = "not_transferred"
        _SYNC_HOLDER(holder)
        raise
    except BaseException:
        _UNKNOWN(holder, "unknown_transfer")
        raise
    if type(fd) is not int or fd < 0:
        _UNKNOWN(holder, "unknown_transfer")
        _fail("OWNERSHIP_UNKNOWN")
    holder["fd"], holder["closer"], holder["transfer"] = fd, "fd", "transferred"
    _SYNC_HOLDER(holder)
    if _CRT_GET(fd) != holder["raw"]:
        _fail("IDENTITY_CHANGED")


def _directory_identity_original(info):
    if (type(info) is not os.stat_result or not stat.S_ISDIR(info.st_mode)
            or type(info.st_dev) is not int or type(info.st_ino) is not int
            or info.st_dev < 0 or info.st_ino <= 0
            or info.st_file_attributes & _REPARSE
            or len(str(info.st_dev)) > 40 or len(str(info.st_ino)) > 40):
        _fail("IDENTITY_CHANGED")
    return {"device": str(info.st_dev), "inode": str(info.st_ino)}


def _file_identity_original(info):
    if (type(info) is not os.stat_result or not stat.S_ISREG(info.st_mode)
            or type(info.st_dev) is not int or type(info.st_ino) is not int
            or info.st_dev < 0 or info.st_ino <= 0 or info.st_nlink != 1
            or info.st_file_attributes & _REPARSE or type(info.st_size) is not int
            or not 0 <= info.st_size <= _MAX_FILE):
        _fail("IDENTITY_CHANGED")
    result = {"device": str(info.st_dev), "inode": str(info.st_ino),
        "byte_size": info.st_size, "mtime_ns": str(info.st_mtime_ns),
        "ctime_ns": str(info.st_ctime_ns), "mode": info.st_mode, "nlink": info.st_nlink}
    if any(len(result[k]) > 40 or not result[k].isdigit() for k in ("device", "inode", "mtime_ns", "ctime_ns")):
        _fail("IDENTITY_CHANGED")
    return result


def _identity_key_original(identity):
    return (identity["device"], identity["inode"])


def _identity_digest_original(identity):
    return _HASH(_canonical_original({"device": identity["device"], "inode": identity["inode"]})).hexdigest()


def _handle_info_original(holder, directory):
    _registered_holder_original(holder)
    if holder["ownership"] != "owned" or holder["attempted"]:
        _fail("IDENTITY_CHANGED")
    info = _HandleInfo()
    result = _HANDLE_INFO(holder["raw"], _BYREF(info))
    if type(result) is not int or result == 0 or bool(info.attributes & _DIR_ATTR) != directory or info.attributes & _REPARSE or not directory and info.links != 1:
        _fail("IDENTITY_CHANGED")
    narrow = (info.volume, info.index_high, info.index_low)
    if holder["narrow"] is not None and holder["narrow"] != narrow:
        _fail("IDENTITY_CHANGED")
    holder["narrow"] = narrow
    needed = _FINAL_PATH(holder["raw"], None, 0, 0)
    if type(needed) is not int or not 1 <= needed < _MAX_PATH:
        _fail("PATH_UNSUPPORTED")
    buffer = _BUFFER(_MAX_PATH)
    if type(buffer) is not _WCHAR_BUFFER_TYPE:
        _fail("RECORD_CHANGED")
    size = _FINAL_PATH(holder["raw"], buffer, needed + 1, 0)
    if type(size) is not int or not 1 <= size <= needed:
        _fail("IDENTITY_CHANGED")
    final = buffer[:size]
    if type(final) is not str or not final.startswith("\\\\?\\") or "\0" in final:
        _fail("IDENTITY_CHANGED")
    expected, actual = holder["path"], final[4:]
    if len(expected) == 3 and actual == expected[:-1]:
        actual += "\\"
    if actual.casefold() != expected.casefold():
        _fail("IDENTITY_CHANGED")
    if holder["parent_key"] is not None:
        parent = _RESOLVE_HOLDER(holder["parent_key"])
        if parent["ownership"] != "owned" or parent["attempted"] or parent["identity"] is None or ntpath.dirname(expected).casefold().rstrip("\\") != parent["path"].casefold().rstrip("\\"):
            _fail("IDENTITY_CHANGED")
        if _directory_identity_original(_STAT(parent["path"], follow_symlinks=False)) != parent["identity"]:
            _fail("IDENTITY_CHANGED")
    return info


def _duplicate_identity_original(original, duplicate):
    _registered_holder_original(original)
    _registered_holder_original(duplicate)
    if (duplicate["unit"] is not original["unit"] or duplicate["role"] != original["role"]
            or original["unit"]["directory_identity_window"] != (original["key"], duplicate["key"])
            or original["kind"] != "directory_handle" or duplicate["kind"] != "directory_handle"):
        _fail("PHASE_INVALID")
    unit = original["unit"]
    output = _HANDLE()
    process = _CURRENT_PROCESS()
    duplicate["ticket"] = unit["budget"].begin_resources(1)
    unit["registration"].budget_last = unit["budget_dict"]["_last_tick"]
    duplicate["native_called"] = True
    _SYNC_HOLDER(duplicate)
    try:
        result = _DUPLICATE(process, original["raw"], process, _BYREF(output), 0, False, 2)
        raw = output.value
        last_error = _LAST_ERROR()
    except BaseException as exc:
        _UNKNOWN(duplicate, "unknown_acquire")
        _settle_holder_original(duplicate, exc, None)
        raise
    duplicate["raw"], duplicate["native_error"] = raw, last_error
    _SYNC_HOLDER(duplicate)
    if type(result) is int and result == 0 and raw is None:
        duplicate["outcome"] = "no_acquire"
        _settle_holder_original(duplicate, AuthorityReplicaSourceNativeError("NATIVE_FAILED"), 0)
    elif type(result) is int and result != 0 and _valid_handle_original(raw):
        _confirmed_acquire_original(duplicate, raw, "handle", True)
    else:
        _UNKNOWN(duplicate, "unknown_acquire")
        _settle_holder_original(duplicate, AuthorityReplicaSourceNativeError("OWNERSHIP_UNKNOWN"), None)
    _transfer_original(duplicate)
    observed = _directory_identity_original(_FSTAT(duplicate["fd"]))
    _handle_info_original(original, True)
    _handle_info_original(duplicate, True)
    if _directory_identity_original(_STAT(original["path"], follow_symlinks=False)) != observed or original["narrow"] != duplicate["narrow"]:
        _fail("IDENTITY_CHANGED")
    return observed


def _open_directory_original(unit, role, path, parent_key, *, private=False, access=1):
    if unit["pending_identities"] or unit["directory_identity_window"] is not None:
        _fail("PHASE_INVALID")
    scope = unit["scopes"][role]
    if scope["acquires"] > _MAX_RESOURCES - 2:
        _fail("REPRESENTATION_LIMIT")
    # H AND H2 cleanup objects exist before the single H CreateFileW.
    original = _NEW_HOLDER(unit, role, "directory_handle", "D", path, parent_key)
    duplicate = _NEW_HOLDER(unit, role, "directory_handle", "D_identity", path, parent_key)
    unit["directory_identity_window"] = (original["key"], duplicate["key"])
    original["ticket"] = unit["budget"].begin_resources(1)
    unit["registration"].budget_last = unit["budget_dict"]["_last_tick"]
    original["native_called"] = True
    _SYNC_HOLDER(original)
    try:
        raw = _CREATE(path, access, 3, None, 3, _BACKUP_REPARSE, None)
        original["raw"], original["native_error"] = raw, _LAST_ERROR()
        _SYNC_HOLDER(original)
        if type(raw) is int and raw in (-1, _INVALID_HANDLE):
            original["outcome"] = "no_acquire"
            _settle_holder_original(original, AuthorityReplicaSourceNativeError("NATIVE_FAILED"), 0)
        elif _valid_handle_original(raw):
            _confirmed_acquire_original(original, raw, "handle", True)
        else:
            _UNKNOWN(original, "unknown_acquire")
            _settle_holder_original(original, AuthorityReplicaSourceNativeError("OWNERSHIP_UNKNOWN"), None)
        _handle_info_original(original, True)
        observed = _duplicate_identity_original(original, duplicate)
        digest = _identity_digest_original(observed)
        # Actual positions remain H acquired then H2 acquired; finalize both
        # prior to any other acquire/body/callback and close H2 before return.
        _FINALIZED(original, observed, digest)
        _FINALIZED(duplicate, _detached_original(observed), digest)
        _CLOSE_OWNED(duplicate)
        if duplicate["outcome"] != "normal_closed" or not duplicate["lease_released"]:
            _fail("UNSETTLED")
    except BaseException as exc:
        if not original["settlement_attempted"]:
            _UNKNOWN(original, "unknown_acquire")
            _settle_holder_original(original, exc, None)
        raise _first_original(unit, exc)
    finally:
        unit["directory_identity_window"] = None
    if private:
        _require_private_original(original, True)
        original["private"] = True
    return original


def _verify_directory_original(holder):
    _registered_holder_original(holder)
    _handle_info_original(holder, True)
    if _directory_identity_original(_STAT(holder["path"], follow_symlinks=False)) != holder["identity"]:
        _fail("IDENTITY_CHANGED")


def _open_chain_original(unit, role, chain, *, private_last=False):
    result, parent = [], None
    for path in chain:
        _normal_gate_original(unit)
        if parent is not None:
            _verify_directory_original(parent)
        holder = _open_directory_original(unit, role, path, None if parent is None else parent["key"],
            private=private_last and path == chain[-1])
        result.append(holder)
        parent = holder
    return tuple(result)


def _verify_file_original(holder):
    _registered_holder_original(holder)
    _handle_info_original(holder, False)
    pathF = _file_identity_original(_STAT(holder["path"], follow_symlinks=False))
    handleF = _file_identity_original(_FSTAT(holder["fd"]))
    if pathF != holder["pathF"] or handleF != holder["handleF"]:
        _fail("CONTENT_CHANGED")
    return True


def _open_file_original(unit, role, path, parent, *, private, access=_GENERIC_READ):
    _verify_directory_original(parent)
    pathF = _file_identity_original(_STAT(path, follow_symlinks=False))
    holder = _create_handle_original(unit, role, "file_handle", "reader", path,
        parent["key"], access, 0x00200000)
    _handle_info_original(holder, False)
    _transfer_original(holder)
    handleF = _file_identity_original(_FSTAT(holder["fd"]))
    if any(pathF[name] != handleF[name] for name in pathF if name != "ctime_ns"):
        _fail("IDENTITY_CHANGED")
    holder["pathF"], holder["handleF"], holder["size"] = pathF, handleF, pathF["byte_size"]
    _FINALIZED(holder, {"device": handleF["device"], "inode": handleF["inode"]}, _identity_digest_original(handleF))
    if private:
        _require_private_original(holder, False)
        holder["private"] = True
    _verify_file_original(holder)
    return holder


def _settled_outpointer_original(unit, role, kind, purpose, function, arguments, output, *, boolean=True):
    _registered_unit_original(unit)
    fixed = ((_OPEN_TOKEN, "token_handle", "acl_token", True),
        (_SID_TEXT, "security_buffer", "process_sid_text", True),
        (_SID_TEXT, "security_buffer", "owner_sid_text", True),
        (_SECURITY_INFO, "security_buffer", "security_descriptor", False),
        (_SECURITY_TEXT, "security_buffer", "sddl_text", True))
    if not any(function is f and kind == k and purpose == p and boolean is b for f, k, p, b in fixed):
        _fail("PHASE_INVALID")
    holder = _begin_holder_original(unit, role, kind, purpose)
    holder["native_called"] = True
    try:
        result = function(*arguments)
        raw, last_error = output.value, _LAST_ERROR()
    except BaseException as exc:
        _UNKNOWN(holder, "unknown_acquire")
        _settle_holder_original(holder, exc, None)
        raise
    holder["raw"], holder["native_error"] = raw, last_error
    _SYNC_HOLDER(holder)
    success = type(result) is int and (result != 0 if boolean else result == 0)
    if success and _valid_handle_original(raw):
        _confirmed_acquire_original(holder, raw, "handle" if kind == "token_handle" else "local", False)
    elif type(result) is int and not success and raw is None:
        holder["outcome"] = "no_acquire"
        _settle_holder_original(holder, AuthorityReplicaSourceNativeError("PRIVATE_UNPROVEN"), 0)
    else:
        # Failure with nonnull outpointer is unknown, never dereferenced/freed.
        _UNKNOWN(holder, "unknown_acquire")
        _settle_holder_original(holder, AuthorityReplicaSourceNativeError("OWNERSHIP_UNKNOWN"), None)
    return holder


def _bounded_sid_text_original(pointer):
    # IsValidSid/GetLengthSid pin a borrowed SID inside its live token/descriptor.
    result = _VALID_SID(pointer)
    if type(result) is not int or result == 0:
        _fail("PRIVATE_UNPROVEN")
    size = _SID_LENGTH(pointer)
    if type(size) is not int or not 8 <= size <= 68:
        _fail("PRIVATE_UNPROVEN")
    return pointer


def _sid_string_original(unit, role, sid, purpose):
    _bounded_sid_text_original(sid)
    output = _LPVOID()
    holder = _settled_outpointer_original(unit, role, "security_buffer", purpose,
        _SID_TEXT, (sid, _BYREF(output)), output)
    try:
        # LocalSize is measured on the certified owned HLOCAL. It bounds every
        # WCHAR dereference by the actual allocation, not a theoretical SID cap.
        allocated = _LOCAL_SIZE(holder["raw"])
        if type(allocated) is not int or not 2 <= allocated <= 512 or allocated % 2:
            _fail("PRIVATE_UNPROVEN")
        chars, pointer = [], _CAST(holder["raw"], _WCHAR_POINTER)
        for offset in range(min(184, allocated // 2)):
            character = pointer[offset]
            if type(character) is not str or len(character) != 1:
                _fail("PRIVATE_UNPROVEN")
            if character == "\0":
                value = "".join(chars)
                if not value.startswith("S-1-") or any(c not in "S0123456789-" for c in value):
                    _fail("PRIVATE_UNPROVEN")
                return value
            chars.append(character)
        _fail("PRIVATE_UNPROVEN")
    except BaseException as exc:
        raise _first_original(unit, exc)
    finally:
        _CLOSE_OWNED(holder)
        if holder["outcome"] != "normal_closed":
            first = operation._FIRST_PEEK(unit["registration"])
            if first is not None:
                raise first


def _check_private_sddl_original(sddl, sid, directory):
    if type(sddl) is not str or len(sddl) > 65535 or not sddl.startswith("D:"):
        _fail("PRIVATE_UNPROVEN")
    header_end = sddl.find("(")
    if header_end < 0:
        _fail("PRIVATE_UNPROVEN")
    flags = sddl[2:header_end]
    if any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" for c in flags) or directory and "P" not in flags:
        _fail("PRIVATE_UNPROVEN")
    offset, principals, aces = header_end, set(), 0
    while offset < len(sddl):
        if sddl[offset] != "(" or aces >= 1024:
            _fail("PRIVATE_UNPROVEN")
        end = sddl.find(")", offset + 1)
        if end < 0 or "(" in sddl[offset + 1:end]:
            _fail("PRIVATE_UNPROVEN")
        fields = sddl[offset + 1:end].split(";")
        if (len(fields) != 6 or fields[0] != "A" or fields[2] not in ("FA", "0x1f01ff")
                or fields[3:5] != ["", ""] or fields[5] not in (sid, "SY")
                or len(fields[1]) % 2 or any(fields[1][i:i + 2] not in ("OI", "CI", "IO", "ID") for i in range(0, len(fields[1]), 2))):
            _fail("PRIVATE_UNPROVEN")
        principals.add(fields[5])
        aces += 1
        offset = end + 1
    if sid not in principals and not (sid == "S-1-5-18" and "SY" in principals):
        _fail("PRIVATE_UNPROVEN")


def _require_private_original(object_holder, directory):
    """Existing-only ACL verification. No initialize/SetSecurityInfo/write."""
    _registered_holder_original(object_holder)
    unit, role = object_holder["unit"], object_holder["role"]
    token_output = _HANDLE()
    token = _settled_outpointer_original(unit, role, "token_handle", "acl_token",
        _OPEN_TOKEN, (_CURRENT_PROCESS(), 8, _BYREF(token_output)), token_output)
    try:
        length = _DWORD()
        result = _TOKEN_INFO(token["raw"], 1, None, 0, _BYREF(length))
        last_error = _LAST_ERROR()
        if type(result) is not int or result != 0 or last_error != 122 or type(length.value) is not int or not 8 <= length.value <= 65536:
            _fail("PRIVATE_UNPROVEN")
        capacity = length.value
        buffer = _BYTE_BUFFER(65536)
        if type(buffer) is not _BYTE_BUFFER_TYPE:
            _fail("RECORD_CHANGED")
        result = _TOKEN_INFO(token["raw"], 1, buffer, capacity, _BYREF(length))
        if type(result) is not int or result == 0 or not 8 <= length.value <= capacity:
            _fail("PRIVATE_UNPROVEN")
        sid_pointer = _CAST(buffer, _LPVOID_POINTER)[0]
        start = ctypes.addressof(buffer)
        if type(sid_pointer) is not int or not start <= sid_pointer <= start + length.value - 8:
            _fail("PRIVATE_UNPROVEN")
        _bounded_sid_text_original(sid_pointer)
        if sid_pointer + _SID_LENGTH(sid_pointer) > start + length.value:
            _fail("PRIVATE_UNPROVEN")
        sid = _sid_string_original(unit, role, sid_pointer, "process_sid_text")
    except BaseException as exc:
        raise _first_original(unit, exc)
    finally:
        _CLOSE_OWNED(token)
    if token["outcome"] != "normal_closed":
        _fail("UNSETTLED")
    # Token and SID text are closed before ACL object. Subsequent serial peak:
    # READ_CONTROL HANDLE + descriptor HLOCAL + one owner/SDDL text HLOCAL = 3.
    if directory:
        acl = _open_directory_original(unit, role, object_holder["path"],
            object_holder["parent_key"], private=False, access=_READ_CONTROL)
    else:
        acl = _open_file_original(unit, role, object_holder["path"],
            _RESOLVE_HOLDER(object_holder["parent_key"]), private=False, access=_READ_CONTROL)
    acl["purpose"] = "acl_object"
    _SYNC_HOLDER(acl)
    try:
        _handle_info_original(acl, directory)
        if acl["narrow"] != object_holder["narrow"]:
            _fail("IDENTITY_CHANGED")
        if acl["identity"] != object_holder["identity"]:
            _fail("IDENTITY_CHANGED")
        descriptor_output = _LPVOID()
        descriptor = _settled_outpointer_original(unit, role, "security_buffer", "security_descriptor",
            _SECURITY_INFO, (acl["raw"], 1, 5, None, None, None, None, _BYREF(descriptor_output)),
            descriptor_output, boolean=False)
        try:
            allocated = _LOCAL_SIZE(descriptor["raw"])
            if type(allocated) is not int or not 20 <= allocated <= 65536:
                _fail("PRIVATE_UNPROVEN")
            valid = _SECURITY_VALID(descriptor["raw"])
            descriptor_length = _SECURITY_LENGTH(descriptor["raw"])
            if type(valid) is not int or valid == 0 or type(descriptor_length) is not int or not 20 <= descriptor_length <= allocated:
                _fail("PRIVATE_UNPROVEN")
            owner, defaulted = _LPVOID(), _BOOL()
            result = _SECURITY_OWNER(descriptor["raw"], _BYREF(owner), _BYREF(defaulted))
            if (type(result) is not int or result == 0 or not _valid_handle_original(owner.value)
                    or not descriptor["raw"] <= owner.value <= descriptor["raw"] + allocated - 8):
                _fail("PRIVATE_UNPROVEN")
            _bounded_sid_text_original(owner.value)
            if owner.value + _SID_LENGTH(owner.value) > descriptor["raw"] + allocated:
                _fail("PRIVATE_UNPROVEN")
            owner_sid = _sid_string_original(unit, role, owner.value, "owner_sid_text")
            if owner_sid != sid:
                _fail("PRIVATE_UNPROVEN")
            text_pointer, count = _LPVOID(), _DWORD()
            text = _settled_outpointer_original(unit, role, "security_buffer", "sddl_text",
                _SECURITY_TEXT, (descriptor["raw"], 1, 4, _BYREF(text_pointer), _BYREF(count)), text_pointer)
            try:
                allocated = _LOCAL_SIZE(text["raw"])
                if (type(allocated) is not int or not 2 <= allocated <= 131072 or allocated % 2
                        or type(count.value) is not int or not 1 <= count.value <= 65536
                        or count.value * 2 > allocated):
                    _fail("PRIVATE_UNPROVEN")
                # The API reports WCHAR buffer capacity, including possible
                # space after the first NUL. Bound every dereference by the
                # certified allocation and decode only the actual text.
                chars, pointer = [], _CAST(text["raw"], _WCHAR_POINTER)
                for offset in range(count.value):
                    character = pointer[offset]
                    if type(character) is not str or len(character) != 1:
                        _fail("PRIVATE_UNPROVEN")
                    if character == "\0":
                        sddl = "".join(chars)
                        break
                    chars.append(character)
                else:
                    _fail("PRIVATE_UNPROVEN")
                _check_private_sddl_original(sddl, sid, directory)
            except BaseException as exc:
                raise _first_original(unit, exc)
            finally:
                _CLOSE_OWNED(text)
        except BaseException as exc:
            raise _first_original(unit, exc)
        finally:
            _CLOSE_OWNED(descriptor)
    except BaseException as exc:
        raise _first_original(unit, exc)
    finally:
        _CLOSE_OWNED(acl)
    first = operation._FIRST_PEEK(unit["registration"])
    if first is not None:
        raise first


def _find_name_original(data):
    # The struct field is a fixed 260 WCHAR array; ctypes field conversion can
    # examine at most that array. Require bounded portable basename afterward.
    name = data.name
    if type(name) is not str or len(name) > 259 or "\0" in name:
        _fail("NAME_INVALID")
    return name


def _portable_name_original(name):
    if (type(name) is not str or not name or name in (".", "..") or len(name.encode("utf-8")) > 255
            or name[-1] in " ." or any(ord(c) < 32 or 127 <= ord(c) <= 159 or c in '<>:"/\\|?*' for c in name)
            or name.split(".", 1)[0].upper() in roles._RESERVED):
        _fail("NAME_INVALID")


def _list_directory_original(parent, *, census, role=None):
    _registered_holder_original(parent)
    unit, role = parent["unit"], parent["role"] if role is None else role
    _verify_directory_original(parent)
    holder = _begin_holder_original(unit, role, "scan_iterator", "find", parent["path"], parent["key"])
    data = _FindData()
    holder["native_called"] = True
    try:
        raw = _FIND_FIRST(ntpath.join(parent["path"], "*"), _BYREF(data))
        last_error = _LAST_ERROR()
    except BaseException as exc:
        _UNKNOWN(holder, "unknown_acquire")
        _settle_holder_original(holder, exc, None)
        raise
    holder["raw"], holder["native_error"] = raw, last_error
    _SYNC_HOLDER(holder)
    if type(raw) is int and raw in (-1, _INVALID_HANDLE):
        holder["outcome"] = "no_acquire"
        # FindFirst no-match is explicit ERROR_FILE_NOT_FOUND; it is accepted
        # only with held-parent before/after identity. No FindClose is invented.
        if last_error != _ERROR_NOT_FOUND:
            _settle_holder_original(holder, AuthorityReplicaSourceNativeError("NATIVE_FAILED"), 0)
        _settle_holder_original(holder, None, 0)
        _CLOSE_OWNED(holder)
        _verify_directory_original(parent)
        return ()
    if not _valid_handle_original(raw):
        _UNKNOWN(holder, "unknown_acquire")
        _settle_holder_original(holder, AuthorityReplicaSourceNativeError("OWNERSHIP_UNKNOWN"), None)
    _confirmed_acquire_original(holder, raw, "find", False)
    names, seen, examined = [], set(), 0
    try:
        while True:
            examined += 1
            if examined > _MAX_ENTRIES + 2:
                _fail("REPRESENTATION_LIMIT")
            name = _find_name_original(data)
            if name not in (".", ".."):
                _portable_name_original(name)
                if data.attributes & _REPARSE:
                    _fail("IDENTITY_CHANGED")
                folded = name.casefold()
                if folded in seen:
                    _fail("CASEFOLD_COLLISION")
                seen.add(folded)
                names.append((name, bool(data.attributes & _DIR_ATTR)))
            _normal_gate_original(unit)
            result = _FIND_NEXT(raw, _BYREF(data))
            last_error = _LAST_ERROR()
            if type(result) is not int:
                _fail("NATIVE_FAILED")
            if result == 0:
                if last_error != _ERROR_NO_MORE:
                    _fail("NATIVE_FAILED")
                break
        names.sort(key=lambda item: item[0].encode("utf-8"))
    except BaseException as exc:
        raise _first_original(unit, exc)
    finally:
        _CLOSE_OWNED(holder)
    if holder["outcome"] != "normal_closed" or not holder["lease_released"]:
        _fail("UNSETTLED")
    _verify_directory_original(parent)
    return tuple(names)


def _read_once_original(holder, requested, partition, source_read):
    """One native call, one settlement, Q finally once; no settlement retry."""
    _registered_holder_original(holder)
    unit, budget = holder["unit"], holder["unit"]["budget"]
    if (holder["ownership"] != "owned" or holder["attempted"] or holder["kind"] != "file_handle"
            or holder["purpose"] != "reader" or type(requested) is not int or not 1 <= requested <= _CHUNK
            or not ((holder["role"] == "observer" and unit["phase"] == "bases_open"
                and partition == "observer_node_read_bytes" and source_read is False)
                or (holder["role"] == "source" and unit["phase"] == "source_metadata"
                and partition == "package_read_bytes" and source_read is True))):
        _fail("PHASE_INVALID")
    event = {"role": holder["role"], "holder_key": holder["key"], "partition": partition,
        "source": source_read, "requested": requested, "ticket": None,
        "begun": False, "native_called": False, "native_raised": False, "raw": None,
        "native_error": None, "settlement_started": False, "Q_settled": False, "actual_bytes": None}
    unit["read_events"].append(event)
    ticket = budget.begin_read(requested, source=source_read)
    event["ticket"], event["begun"] = ticket, True
    unit["registration"].budget_last = unit["budget_dict"]["_last_tick"]
    event["native_called"] = True
    try:
        raw = _READ(holder["fd"], requested)
        event["raw"] = raw
    except BaseException as exc:
        event["native_raised"], event["native_error"] = True, exc
        if unit["budget_dict"]["_active"] is None or unit["budget_dict"]["_active"][0] is not ticket:
            raise _first_original(unit, AuthorityReplicaSourceNativeError("ACCOUNTING_CHANGED"))
        event["settlement_started"] = True
        try:
            budget.fail_read(ticket, exc)
        finally:
            _settle_Q_original(unit, event)
        raise _first_original(unit, exc)
    event["settlement_started"] = True
    try:
        # End is attempted exactly once even for nonbytes/overreturn.
        budget.end_read(ticket, raw)
    finally:
        _settle_Q_original(unit, event)
        event["raw"] = None
    return raw


def _settle_Q_original(unit, event):
    _registered_unit_original(unit)
    if (type(event) is not dict or any(type(k) is not str for k in event)
            or not any(item is event for item in unit["read_events"])):
        _fail("GUARD_REQUIRED")
    if event["Q_settled"] or not event["native_called"]:
        _fail("ACCOUNTING_CHANGED")
    Q = unit["scopes"][event["role"]]["Q"]
    Q["read_calls"] += 1
    Q["source_read_calls"] += int(event["source"])
    if not event["native_raised"] and type(event["raw"]) is bytes:
        amount = len(event["raw"])
        Q["read_bytes"] += amount
        Q["source_read_bytes"] += amount * int(event["source"])
        Q[event["partition"]] += amount
    else:
        Q["uncertain_read_calls"] += 1
        Q["uncertain_source_read_calls"] += int(event["source"])
    event["Q_settled"] = True
    # Do not retain payload chunks in audit records. Exact original len/type
    # remains separately pinned; resource/Q validation uses these scalars.
    event["actual_bytes"] = len(event["raw"]) if type(event["raw"]) is bytes and not event["native_raised"] else None


def _stream_file_original(holder, *, partition, source_read, cap, keep):
    _registered_holder_original(holder)
    unit = holder["unit"]
    expected = holder["size"]
    if expected > cap:
        _fail("FILE_LIMIT")
    _verify_file_original(holder)
    seen, chunks, digest = 0, [] if keep else None, _HASH()
    while True:
        _normal_gate_original(unit)
        requested = min(_CHUNK, expected - seen + 1)
        raw = _read_once_original(holder, requested, partition, source_read)
        # Native-return settlement and Q precede length/hash/buffer/callback.
        if type(raw) is not bytes:
            _fail("SOURCE_FAILED")
        amount = len(raw)
        if seen + amount > expected or seen + amount > cap:
            _fail("CONTENT_CHANGED")
        if amount == 0:
            if seen != expected:
                _fail("CONTENT_CHANGED")
            break
        digest.update(raw)
        seen += amount
        if keep:
            chunks.append(raw)
        # Payload chunks survive only in this fixed local until next read.
        _verify_file_original(holder)
    _verify_file_original(holder)
    return digest.hexdigest(), seen, None if chunks is None else b"".join(chunks)


def _save_result_original(unit, kind, value):
    if kind in unit["results"]:
        _fail("PHASE_INVALID")
    unit["results"][kind] = value
    unit["result_pins"][kind] = _result_fingerprint_original(value)
    return value


def _source_result_original(unit_key, source_token, kind):
    unit = _unit_state_original(unit_key)
    if unit["source_token"] is not source_token or type(kind) is not str or kind not in ("location", "metadata", "read", "final_metadata"):
        _fail("GUARD_REQUIRED")
    value = unit["results"].get(kind, _MISSING)
    if value is _MISSING:
        _fail("PHASE_INVALID")
    _check_result_fingerprint_original(unit["result_pins"][kind])
    return value


def _assert_source_result_original(unit_key, source_token, kind, value):
    if _source_result_original(unit_key, source_token, kind) is not value:
        _fail("GUARD_REQUIRED")
    return True


def _validate_observation_original(context, stage, value):
    unit = _RESOLVE_UNIT(context.unit_key)
    if unit["context"] is not context or stage not in ("bases", "target", "output"):
        _fail("GUARD_REQUIRED")
    key = {"bases": "bases", "target": "target_observation", "output": "output_binding"}[stage]
    if unit[key] is not value or value is None:
        _fail("GUARD_REQUIRED")
    _check_result_fingerprint_original(unit["result_pins"][stage])
    return True


def _open_coordinator_bases_original(unit_key):
    unit = _unit_state_original(unit_key)
    if unit["phase"] != "created" or unit["bases"] is not None:
        _fail("PHASE_INVALID")
    unit["begin"] = unit["budget"].snapshot()
    unit["registration"].budget_last = unit["budget_dict"]["_last_tick"]
    unit["scopes"]["coordinator"]["begin"] = _detached_original(unit["begin"])
    _SYNC_UNIT(unit)
    _enter_native_original(unit)
    try:
        plans = unit["context"].native_plan
        if plans != _plan_source_unit_original(unit["context"].inputs):
            _fail("RECORD_CHANGED")
        profiles, held_by_path = [], {}
        for ordinal, (paths, chains) in enumerate(plans[0]):
            roots = {}
            chains_by_name = {}
            for name, chain in zip(("data", "media", "reports", "checkpoints"), chains):
                # Sharing an already original coordinator path is borrowing;
                # fresh opens of any other chain are all real acquisitions.
                if chain[-1] in held_by_path:
                    actual_chain = held_by_path[chain[-1]]
                    for holder in actual_chain:
                        _verify_directory_original(holder)
                else:
                    actual_chain = _open_chain_original(unit, "coordinator", chain, private_last=True)
                    held_by_path[chain[-1]] = actual_chain
                root = actual_chain[-1]
                if root["private"] is not True:
                    _fail("PRIVATE_UNPROVEN")
                roots[name] = _detached_original(root["identity"])
                chains_by_name[name] = tuple(h["key"] for h in actual_chain)
                if ordinal == 0 and name == "data":
                    unit["target_data_key"] = root["key"]
                if name == "data" and paths == unit["context"].inputs[11][4]:
                    unit["source_data_key"] = root["key"]
            profiles.append({"profile_paths": list(paths),
                "profile_sha256": _HASH(_canonical_original(list(paths))).hexdigest(),
                "roots": roots})
            unit["base_paths"][ordinal] = chains_by_name
        if unit["context"].inputs[11][0] == "profile_raw2" and unit["source_data_key"] is None:
            _fail("RECORD_CHANGED")
        unit["base_profiles"] = tuple(profiles)
        value = {"profiles": profiles}
        unit["bases"], unit["phase"] = value, "bases_open"
        unit["result_pins"]["bases"] = _result_fingerprint_original(value)
        _SYNC_UNIT(unit)
    except BaseException as exc:
        raise _first_original(unit, exc)
    finally:
        _leave_native_original(unit)
    return value


def _node_pairs_original(pairs):
    value = {}
    for key, item in pairs:
        if type(key) is not str or key in value:
            _fail("NODE_INVALID")
        value[key] = item
    return value


def _node_invalid_number_original(value):
    _fail("NODE_INVALID")


def _observe_target_original(unit_key):
    unit = _unit_state_original(unit_key)
    if unit["phase"] != "bases_open" or unit["target_observation"] is not None:
        _fail("PHASE_INVALID")
    scope = unit["scopes"]["observer"]
    scope["begin"] = unit["budget"].snapshot()
    unit["registration"].budget_last = unit["budget_dict"]["_last_tick"]
    _enter_native_original(unit)
    try:
        parent = _RESOLVE_HOLDER(unit["target_data_key"])
        holder = _open_file_original(unit, "observer", ntpath.join(parent["path"], "node-identity.json"), parent, private=False)
        if not 0 < holder["size"] <= 4096:
            _fail("NODE_INVALID")
        sha, size, raw = _stream_file_original(holder, partition="observer_node_read_bytes", source_read=False, cap=4096, keep=True)
        try:
            text = raw.decode("utf-8", errors="strict")
            value = _JSON_LOADS(text, object_pairs_hook=_node_pairs_original,
                parse_float=_node_invalid_number_original, parse_int=_node_invalid_number_original,
                parse_constant=_node_invalid_number_original)
        except (UnicodeError, ValueError, RecursionError):
            _fail("NODE_INVALID")
        if type(value) is not dict or set(value) != {"instance_id"}:
            _fail("NODE_INVALID")
        operation._uuid_original(value["instance_id"])
        node = {"node_id": value["instance_id"], "sha256": sha, "byte_size": size,
            "parent_identity": _detached_original(parent["identity"]),
            "path_identity": _detached_original(holder["pathF"]),
            "handle_identity": _detached_original(holder["handleF"])}
        observed = {"profile": _detached_original(unit["base_profiles"][0]), "node": node}
        unit["target_observation"], unit["phase"] = observed, "target_observed"
        unit["result_pins"]["target"] = _result_fingerprint_original(observed)
        _SYNC_UNIT(unit)
    except BaseException as exc:
        raise _first_original(unit, exc)
    finally:
        _leave_native_original(unit)
    return observed


def _bind_output_original(unit_key):
    unit = _unit_state_original(unit_key)
    if unit["phase"] != "target_observed" or unit["output_binding"] is not None:
        _fail("PHASE_INVALID")
    scope = unit["scopes"]["output"]
    scope["begin"] = unit["budget"].snapshot()
    unit["registration"].budget_last = unit["budget_dict"]["_last_tick"]
    _enter_native_original(unit)
    try:
        data = _RESOLVE_HOLDER(unit["target_data_key"])
        opid = unit["context"].inputs[4]
        parent, names = data, _list_directory_original(data, census=False, role="output")
        if any(name.casefold() == _OUTPUT_FAMILY.casefold() and name != _OUTPUT_FAMILY for name, _directory in names):
            _fail("OUTPUT_CHANGED")
        namespace_matches = [isdir for name, isdir in names if name == _OUTPUT_FAMILY]
        if namespace_matches:
            if namespace_matches != [True]:
                _fail("OUTPUT_INVALID")
            namespace = _open_directory_original(unit, "output", ntpath.join(data["path"], _OUTPUT_FAMILY), data["key"], private=True)
            unit["output_keys"].append(namespace["key"])
            parent, names = namespace, _list_directory_original(namespace, census=False, role="output")
            if any(name.casefold() == opid.casefold() and name != opid for name, _directory in names):
                _fail("OUTPUT_CHANGED")
            operation_matches = [isdir for name, isdir in names if name == opid]
            if operation_matches:
                if operation_matches != [True]:
                    _fail("OUTPUT_INVALID")
                actual = _open_directory_original(unit, "output", ntpath.join(namespace["path"], opid), namespace["key"], private=True)
                unit["output_keys"].append(actual["key"])
                value = {"state": "exists", "canonical_path": unit["context"].native_plan[2],
                    "namespace_identity": _detached_original(namespace["identity"]),
                    "operation_identity": _detached_original(actual["identity"])}
            else:
                unit["output_absence"] = (namespace["key"], opid, names)
                value = {"state": "operation_absent", "canonical_path": unit["context"].native_plan[2],
                    "held_parent_identity": _detached_original(namespace["identity"]), "missing_child": opid}
        else:
            # Do not manufacture namespaceD/operationD for a missing target.
            unit["output_absence"] = (data["key"], _OUTPUT_FAMILY, names)
            value = {"state": "namespace_absent", "canonical_path": unit["context"].native_plan[2],
                "held_parent_identity": _detached_original(data["identity"]), "missing_child": _OUTPUT_FAMILY}
        unit["output_binding"], unit["phase"] = value, "output_bound"
        unit["result_pins"]["output"] = _result_fingerprint_original(value)
        _SYNC_UNIT(unit)
    except BaseException as exc:
        raise _first_original(unit, exc)
    finally:
        _leave_native_original(unit)
    return value


def _source_record_original(unit, source_token):
    from . import personal_authority_replica_source as source
    record = source._native_context_original(source_token, unit["operation_token"])
    if record.context is not unit["context"] or record.operation_token is not unit["operation_token"]:
        _fail("RECORD_CHANGED")
    if unit["source_token"] is None:
        unit["source_token"], unit["source_record"] = source_token, record
        _SYNC_UNIT(unit)
    elif unit["source_token"] is not source_token or unit["source_record"] is not record:
        _fail("RECORD_CHANGED")
    return record


def _alias_original(unit, holder, *, source_tree):
    identity = _identity_key_original(holder["identity"])
    if source_tree:
        for key in unit["output_keys"]:
            if _identity_key_original(_RESOLVE_HOLDER(key)["identity"]) == identity:
                _fail("SOURCE_OUTPUT_ALIAS")
        observer = unit["target_observation"]
        if observer is not None and _identity_key_original(observer["node"]["handle_identity"]) == identity:
            _fail("SOURCE_OUTPUT_ALIAS")
        graph = unit["source_graph"]
        if graph is not None:
            if identity in graph["identities"]:
                _fail("SOURCE_ALIAS")
            graph["identities"].add(identity)


def _open_source_location_original(unit_key, source_token):
    unit = _unit_state_original(unit_key)
    record = _source_record_original(unit, source_token)
    if unit["phase"] != "output_bound" or "location" in unit["results"]:
        _fail("PHASE_INVALID")
    scope = unit["scopes"]["source"]
    required_begin = set(_IO_KEYS) | {"elapsed_ns", "current_resources", "peak_resources",
        "pending_resources", "uncertain_resource_acquires"}
    if (scope["begin"] is not record.begin or type(record.begin) is not dict
            or any(type(k) is not str for k in record.begin) or set(record.begin) != required_begin
            or any(type(v) is not int or v < 0 for v in record.begin.values())
            or any(record.begin[k] != 0 for k in ("source_read_bytes", "source_read_calls", "uncertain_source_read_calls"))
            or any(unit["budget_dict"][k] != 0 for k in ("_source_read_bytes", "_source_read_calls", "_uncertain_source_reads"))):
        _fail("SOURCE_BEGIN_INVALID")
    _check_result_fingerprint_original(unit["result_pins"]["source_begin"])
    _enter_native_original(unit)
    try:
        selector = unit["context"].inputs[11]
        if selector[0] == "profile_raw2":
            data = _RESOLVE_HOLDER(unit["source_data_key"])
            base_ordinal = next(i for i, (paths, _chain) in enumerate(unit["context"].native_plan[0]) if paths == selector[4])
            ancestors = tuple(_RESOLVE_HOLDER(k) for k in unit["base_paths"][base_ordinal]["data"])
            namespace = _open_directory_original(unit, "source", ntpath.join(data["path"], _SOURCE_FAMILY), data["key"], private=True)
            root = _open_directory_original(unit, "source", ntpath.join(namespace["path"], selector[5]), namespace["key"], private=True)
            ancestors = (*ancestors, namespace, root)
        else:
            ancestors = _open_chain_original(unit, "source", unit["context"].native_plan[1], private_last=True)
            root = ancestors[-1]
        if root["path"] != selector[1] or len(ancestors) > _MAX_CHAIN or ancestors[-1]["identity"] != root["identity"]:
            _fail("IDENTITY_CHANGED")
        for holder in ancestors:
            _verify_directory_original(holder)
        unit["source_graph"] = {"root": root["key"], "ancestors": tuple(h["key"] for h in ancestors),
            "directories": {}, "files": {}, "identities": set(), "S": 0, "N": 0, "D": 0,
            "controls": {}, "read_complete": False, "final_complete": False}
        _alias_original(unit, root, source_tree=True)
        profile = None if selector[0] == "detached_raw2" else _detached_original(unit["base_profiles"][base_ordinal])
        raw2_profile = None if profile is None else {"profile_paths": list(profile["profile_paths"]),
            "roots": {name: {"path": path, "identity": _detached_original(profile["roots"][name])}
                for name, path in zip(("data", "media", "reports", "checkpoints"),
                    (ntpath.dirname(profile["profile_paths"][0]), *profile["profile_paths"][1:]))}}
        value = {"source_kind": selector[0], "package_root": selector[1],
            "generation": selector[2], "expected_request_sha256": selector[3],
            "profile": profile, "raw2_profile": raw2_profile,
            "original_operation_id": selector[5],
            "namespace_identity": None if selector[0] == "detached_raw2" else _detached_original(namespace["identity"]),
            "root_identity": _detached_original(root["identity"]),
            "ancestors": [{"path": h["path"], "identity": _detached_original(h["identity"])} for h in ancestors]}
        unit["phase"] = "source_located"
        _SYNC_UNIT(unit)
        _save_result_original(unit, "location", value)
    except BaseException as exc:
        raise _first_original(unit, exc)
    finally:
        _leave_native_original(unit)
    return value


def _file_cap_original(key, role):
    if role == "marker":
        return 0
    if role == "control":
        return 1024**2 if key == "request.json" else _MAX_CONTROL
    if role == "retained" and "/failures/" in key:
        return 16384
    return _MAX_FILE


def _census_metadata_original(unit_key, source_token):
    unit = _unit_state_original(unit_key)
    _source_record_original(unit, source_token)
    if unit["phase"] != "source_located":
        _fail("PHASE_INVALID")
    graph, selector = unit["source_graph"], unit["context"].inputs[11]
    _enter_native_original(unit)
    try:
        stack = [("", graph["root"])]
        while stack:
            relative, key = stack.pop()
            parent = _RESOLVE_HOLDER(key)
            roles.classify_directory(relative, expected_request_sha256=selector[3])
            names = _list_directory_original(parent, census=True)
            if graph["N"] + graph["D"] + 1 + len(names) > _MAX_ENTRIES:
                _fail("REPRESENTATION_LIMIT")
            graph["D"] += 1
            children = [name for name, _directory in names]
            graph["directories"][relative] = {"holder_key": key,
                "row": {"relative_key": relative, "identity": _detached_original(parent["identity"]), "children": children}}
            # Find was checked closed before any child H/H2 acquisition.
            pending_directories = []
            for name, directory in names:
                child_key = name if relative == "" else relative + "/" + name
                path = ntpath.join(parent["path"], name)
                _dos_chain_original(path)
                if directory:
                    roles.classify_directory(child_key, expected_request_sha256=selector[3])
                    child = _open_directory_original(unit, "source", path, key, private=True)
                    _alias_original(unit, child, source_tree=True)
                    pending_directories.append((child_key, child["key"]))
                else:
                    role = roles.classify_file(child_key)
                    child = _open_file_original(unit, "source", path, parent, private=True)
                    _alias_original(unit, child, source_tree=True)
                    cap = _file_cap_original(child_key, role)
                    if child["size"] > cap:
                        _fail("FILE_LIMIT")
                    graph["N"] += 1
                    graph["S"] += child["size"]
                    if graph["S"] > _MAX_SOURCE or graph["N"] + graph["D"] > _MAX_ENTRIES:
                        _fail("REPRESENTATION_LIMIT")
                    graph["files"][child_key] = {"holder_key": child["key"], "role": role,
                        "row": {"relative_key": child_key, "parent_identity": _detached_original(parent["identity"]),
                            "path_identity": _detached_original(child["pathF"]), "handle_identity": _detached_original(child["handleF"]),
                            "byte_size": child["size"], "role": role}}
            stack.extend(reversed(pending_directories))
        if sum(int(item["role"] != "payload") for item in graph["files"].values()) > _MAX_FACTS:
            _fail("REPRESENTATION_LIMIT")
        directory_rows = [graph["directories"][k]["row"] for k in sorted(graph["directories"], key=lambda value: value.encode("utf-8"))]
        file_rows = [graph["files"][k]["row"] for k in sorted(graph["files"], key=lambda value: value.encode("utf-8"))]
        value = {"directories": _detached_original(directory_rows), "files": _detached_original(file_rows),
            "N": graph["N"], "D": graph["D"], "S": graph["S"]}
        unit["phase"] = "source_metadata"
        _SYNC_UNIT(unit)
        _save_result_original(unit, "metadata", value)
    except BaseException as exc:
        raise _first_original(unit, exc)
    finally:
        _leave_native_original(unit)
    return value


def _read_source_package_original(unit_key, source_token):
    unit = _unit_state_original(unit_key)
    _source_record_original(unit, source_token)
    if unit["phase"] != "source_metadata":
        _fail("PHASE_INVALID")
    graph, budget = unit["source_graph"], unit["budget"]
    _normal_gate_original(unit)
    S, dictionary = graph["S"], unit["budget_dict"]
    if (S + _CHUNK > unit["context"].limits["max_read_bytes"] - dictionary["_read_bytes"]
            or S + _CHUNK > unit["context"].limits["source_max_read_bytes"] - dictionary["_source_read_bytes"]):
        _fail("SOURCE_HEADROOM_LIMIT")
    _enter_native_original(unit)
    try:
        controls, rows, readers, native_hash_bytes = {}, [], [], 0
        for key in sorted(graph["files"], key=lambda value: value.encode("utf-8")):
            item = graph["files"][key]
            holder = _RESOLVE_HOLDER(item["holder_key"])
            role = item["role"]
            sha, size, raw = _stream_file_original(holder, partition="package_read_bytes", source_read=True,
                cap=_file_cap_original(key, role), keep=role == "control")
            native_hash_bytes += size
            row = {name: _detached_original(value) for name, value in item["row"].items() if name != "role"}
            row["sha256"] = sha
            rows.append(row)
            item["sha256"] = sha
            if role == "control":
                controls[key] = raw
                readers.append((key, holder["key"], raw, sha, size))
        directory_rows = [graph["directories"][k]["row"] for k in sorted(graph["directories"], key=lambda value: value.encode("utf-8"))]
        graph["controls"], graph["read_complete"] = controls, True
        value = {"control_bytes": controls,
            "inventory": {"directories": _detached_original(directory_rows), "files": rows},
            "native_hash_input_bytes": native_hash_bytes, "readers": tuple(readers)}
        unit["phase"] = "source_captured"
        _SYNC_UNIT(unit)
        _save_result_original(unit, "read", value)
    except BaseException as exc:
        raise _first_original(unit, exc)
    finally:
        _leave_native_original(unit)
    return value


def _verify_output_final_original(unit):
    for key in unit["output_keys"]:
        _verify_directory_original(_RESOLVE_HOLDER(key))
    if unit["output_absence"] is not None:
        parent_key, missing, initial = unit["output_absence"]
        parent = _RESOLVE_HOLDER(parent_key)
        final = _list_directory_original(parent, census=False, role="output")
        if final != initial or any(name.casefold() == missing.casefold() for name, _directory in final):
            _fail("OUTPUT_CHANGED")


def _final_source_metadata_original(unit_key, source_token):
    unit = _unit_state_original(unit_key)
    _source_record_original(unit, source_token)
    if unit["phase"] != "source_captured" or not unit["source_graph"]["read_complete"]:
        _fail("PHASE_INVALID")
    graph = unit["source_graph"]
    _enter_native_original(unit)
    try:
        for key in graph["ancestors"]:
            _verify_directory_original(_RESOLVE_HOLDER(key))
        for relative in sorted(graph["directories"], key=lambda value: value.encode("utf-8")):
            item = graph["directories"][relative]
            parent = _RESOLVE_HOLDER(item["holder_key"])
            names = _list_directory_original(parent, census=True)
            actual = tuple((name, (name if relative == "" else relative + "/" + name) in graph["directories"]) for name in item["row"]["children"])
            if names != actual:
                _fail("SOURCE_CHANGED")
            _verify_directory_original(parent)
        for item in graph["files"].values():
            _verify_file_original(_RESOLVE_HOLDER(item["holder_key"]))
        _verify_output_final_original(unit)
        # All coordinator chains and actual observer domains remain original.
        for holder in unit["holders"]:
            if holder["role"] == "coordinator" and holder["purpose"] == "D":
                _verify_directory_original(holder)
            if holder["role"] == "observer" and holder["purpose"] == "reader":
                _verify_file_original(holder)
        graph["final_complete"] = True
        unit["phase"] = "source_final_metadata"
        _SYNC_UNIT(unit)
        value = {"metadata_unchanged": True, "output_binding_unchanged": True,
            "full_body_final_reads": 0, "complete_preview": False}
        _save_result_original(unit, "final_metadata", value)
    except BaseException as exc:
        raise _first_original(unit, exc)
    finally:
        _leave_native_original(unit)
    return value


def _assert_unit_settled_original(unit_key):
    unit = _RESOLVE_UNIT(unit_key)
    _validate_registration_original(unit["registration"])
    first = operation._FIRST_PEEK(unit["registration"])
    if first is not None:
        raise first
    if (unit["unknown"] or unit["pending_identities"] or unit["incomplete_portable_identities"] or unit["global_current"]
            or unit["global_acquires"] != unit["global_normal_closes"]
            or unit["source_graph"] is None or not unit["source_graph"]["final_complete"]
            or any(h["ownership"] not in ("empty", "closed")
                or h["ticket"] is not None and not h["lease_released"] for h in unit["holders"])):
        _fail("UNSETTLED")
    unit["budget"].assert_settled()
    unit["registration"].budget_last = unit["budget_dict"]["_last_tick"]
    for role, scope in unit["scopes"].items():
        if (scope["acquires"] != scope["normal_closes"] or len(scope["events"]) != 2 * scope["acquires"]
                or scope["pending_identities"] or scope["incomplete_portable_identities"] or scope["unknown"] or scope["current"]):
            _fail("UNSETTLED")
        for index, event in enumerate(scope["events"]):
            if event["sequence"] != index + 1:
                _fail("RECORD_CHANGED")
        events_by_id = {}
        for event in scope["events"]:
            events_by_id.setdefault(event["resource_id"], []).append(event)
        for holder in scope["holders"]:
            if holder["resource_id"] is not None:
                events = events_by_id[holder["resource_id"]]
                if len(events) != 2 or tuple(e["action"] for e in events) != ("acquired", "normal_closed") or events[0]["identity_sha256"] != events[1]["identity_sha256"]:
                    _fail("UNSETTLED")
        scope["normal_closed"] = True
    unit["end"] = unit["budget"].snapshot()
    unit["registration"].budget_last = unit["budget_dict"]["_last_tick"]
    unit["normal_closed"], unit["phase"] = True, "unit_closed"
    _SYNC_UNIT(unit)
    return True


def _source_unit_report_original(unit_key):
    unit = _RESOLVE_UNIT(unit_key)
    if not unit["normal_closed"] or unit["phase"] != "unit_closed":
        _fail("UNSETTLED")
    _validate_registration_original(unit["registration"])
    scopes = {}
    for role, scope in unit["scopes"].items():
        scopes[role] = {"Q": _detached_original(scope["Q"]),
            "observation_scope_id": scope["scope_id"],
            "begin": _detached_original(scope["begin"]), "end": _detached_original(scope["end"]),
            "resource_acquires": scope["acquires"], "resource_normal_closes": scope["normal_closes"],
            "peak_owned_resources": scope["peak"], "current_resources": scope["current"],
            "pending_identities": scope["pending_identities"], "unknown_resources": scope["unknown"],
            "incomplete_portable_identities": scope["incomplete_portable_identities"],
            "portable_identity_complete": scope["incomplete_portable_identities"] == 0,
            "resource_events_sha256": _HASH(_EVENT_DOMAIN + _canonical_original(scope["events"])).hexdigest(),
            "normal_closed": scope["normal_closed"]}
    return {"bases": _detached_original(unit["bases"]), "target_observation": _detached_original(unit["target_observation"]),
        "output_binding": _detached_original(unit["output_binding"]),
        "source": {"location": _detached_original(unit["results"]["location"]),
            "N": unit["source_graph"]["N"], "D": unit["source_graph"]["D"], "S": unit["source_graph"]["S"]},
        "begin": _detached_original(unit["begin"]), "end": _detached_original(unit["end"]),
        "global_resources": {"resource_acquires": unit["global_acquires"],
            "resource_normal_closes": unit["global_normal_closes"],
            "current_resources": unit["global_current"], "peak_owned_resources": unit["global_peak"]},
        "scope_reports": scopes, "global_settled": True, "complete_preview": False,
        "producer": False, "source_closed": False, "copy": False, "restore": False,
        "runtime_write": False, "sender_authority": False}


def _catalog_extra_original():
    """Fixed bootstrap graph; operation seals definitions/classes/closures too."""
    own = sys.modules[__name__]
    own_names = tuple(_GET(own, "__dict__"))
    modules = [(own, own_names),
        (builtins, ("type", "object", "dict", "list", "tuple", "str", "int", "bool", "len", "id", "set",
            "range", "sum", "min", "max", "all", "any", "getattr", "isinstance", "sorted", "zip", "enumerate",
            "reversed", "BaseException", "ValueError", "OSError", "UnicodeError", "RecursionError", "ImportError")),
        (ctypes, ("Structure", "Array", "c_char", "c_void_p", "c_wchar", "c_int", "py_object", "POINTER", "sizeof", "alignment", "WinDLL",
            "byref", "get_last_error", "create_unicode_buffer", "create_string_buffer", "cast", "wstring_at", "addressof",
            "_sys", "_cast", "_wstring_at", "_pointer_type_cache")),
        (_ctypes, ("POINTER", "_pointer_type_cache")),
        (hashlib, ("sha256",)), (json, ("loads", "dumps")),
        (json.decoder, ("JSONDecoder", "JSONDecodeError", "JSONObject", "JSONArray", "scanstring", "BACKSLASH",
            "WHITESPACE", "WHITESPACE_STR", "scanner")),
        (json.scanner, ("make_scanner", "c_make_scanner", "py_make_scanner", "NUMBER_RE")),
        (json.encoder, ("JSONEncoder", "encode_basestring", "encode_basestring_ascii", "c_make_encoder", "_make_iterencode",
            "ESCAPE_DCT", "ESCAPE", "ESCAPE_ASCII", "INFINITY")),
        (ntpath, ("dirname", "join")), (stat, ("S_ISDIR", "S_ISREG")),
        (sys, ("implementation", "version_info", "maxsize", "audit")),
        (os, ("stat_result", "stat", "fstat", "read", "lseek", "close", "O_RDONLY", "O_BINARY", "SEEK_SET", "name")),
        (uuid, ("uuid4", "UUID"))]
    if _BACKEND_NAME == "nt":
        modules.extend(((wintypes, ("HANDLE", "HLOCAL", "BOOL", "DWORD", "WCHAR", "FILETIME", "LPCWSTR", "LPWSTR", "LPVOID")),
            (msvcrt, ("open_osfhandle", "get_osfhandle"))))
    return tuple(modules)


(_NEW_UNIT, _RESOLVE_UNIT, _RESOLVE_HOLDER, _NEW_HOLDER, _ACQUIRED,
 _FINALIZED, _UNKNOWN, _CLOSE_OWNED, _CLEANUP, _RETIRE, _RETAIN,
 _TRUSTED_CALLBACKS, _SOURCE_CALLBACKS, _BLOCKED, _NO_CALLBACK,
 _SYNC_UNIT, _SYNC_HOLDER, _VALIDATE_BOUND_REGISTRATION,
 _CALLBACK_ALLOWED, _MINIMAL_CLEANUP_GATE) = _make_ownership_kernel_original()
del _make_ownership_kernel_original
