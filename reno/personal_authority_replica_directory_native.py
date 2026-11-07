"""Fixed Windows DATA-chain responsibility unit for one original RP1 operation.

No caller supplies a path, handle, descriptor, ledger, closer or backend.  The
original operation imports this module once before sealing the bidirectional
catalog.  Normal dispatch uses that catalog; failure cleanup uses the separate
captured ownership kernel below and never redispatches a damaged Python method.
This unit proves neither ACLs nor source/target package closure or authority.
"""
from __future__ import annotations

import ctypes
import hashlib
import os
import stat
import sys
import weakref
from types import FunctionType, ModuleType

_OPERATION_BOOTSTRAP_NAME = __package__ + ".personal_authority_replica_operation"
_OPERATION_BOOTSTRAP = sys.modules.get(_OPERATION_BOOTSTRAP_NAME)
if (type(_OPERATION_BOOTSTRAP) is not ModuleType
        or "_FIRST_ORIGINAL" not in object.__getattribute__(_OPERATION_BOOTSTRAP, "__dict__")
        or "_integrity_original" not in object.__getattribute__(_OPERATION_BOOTSTRAP, "__dict__")):
    raise ImportError("Import reno.personal_authority_replica_operation before the fixed native backend")
from . import personal_authority_replica_operation as operation

_TYPE, _GET, _CLASS_GET = type, object.__getattribute__, type.__getattribute__
_TAG = object()
_STAT, _FSTAT, _CLOSE = os.stat, os.fstat, os.close
_HASH = hashlib.sha256
_HASH_TYPE = type(_HASH())
_IO_KEYS = ("read_bytes", "source_read_bytes", "read_calls", "source_read_calls",
    "seek_calls", "confirmed_write_bytes", "write_calls", "uncertain_read_calls",
    "uncertain_source_read_calls", "uncertain_write_bytes")
_Q_KEYS = _IO_KEYS + ("package_read_bytes", "outer_control_read_bytes",
    "observer_node_read_bytes", "other_native_control_read_bytes")
_EVENT_DOMAIN = b"reno-authority-replica-runtime-v1\0resource-events\0"
_FILE_LIST_DIRECTORY = 1
_SHARE_READ_WRITE = 3
_OPEN_EXISTING = 3
_OPEN_REPARSE_BACKUP = 0x02200000
_DIRECTORY_ATTRIBUTE = 0x10
_REPARSE_ATTRIBUTE = 0x400
_DUPLICATE_SAME_ACCESS = 2
_MAX_FINAL_PATH = 260
_MAX_DIRECTORIES = 129
_MAX_EVENTS = 774
_BACKEND_NAME = os.name
_INTERPRETER = (sys.implementation.name, tuple(sys.version_info[:3]),
    ctypes.sizeof(ctypes.c_void_p))
_DLL = _WIN_CREATE_FILEW = _WIN_GET_CURRENT_PROCESS = None
_WIN_DUPLICATE_HANDLE = _WIN_GET_FILE_INFORMATION = None
_WIN_GET_FINAL_PATH = _WIN_CLOSE_HANDLE = None
_CRT_OPEN_OSFHANDLE = _CRT_GET_OSFHANDLE = None
_GET_LAST_ERROR = _BYREF = _BUFFER = None
_HandleInfo = _HANDLE = _BOOL = _DWORD = None

if os.name == "nt":
    from ctypes import wintypes
    import msvcrt

    class _HandleInfo(ctypes.Structure):
        _fields_ = [("attributes", wintypes.DWORD), ("created", wintypes.FILETIME),
            ("accessed", wintypes.FILETIME), ("written", wintypes.FILETIME),
            ("volume", wintypes.DWORD), ("size_high", wintypes.DWORD),
            ("size_low", wintypes.DWORD), ("links", wintypes.DWORD),
            ("index_high", wintypes.DWORD), ("index_low", wintypes.DWORD)]

    # Own function-pointer objects: configuring these cannot change G1's DLL
    # cache or the prototypes of the original borrowed guard.
    _DLL = ctypes.WinDLL("kernel32", use_last_error=True)
    _HANDLE, _BOOL, _DWORD = wintypes.HANDLE, wintypes.BOOL, wintypes.DWORD
    _WIN_CREATE_FILEW = _DLL.CreateFileW
    _WIN_CREATE_FILEW.argtypes = [wintypes.LPCWSTR, _DWORD, _DWORD,
        wintypes.LPVOID, _DWORD, _DWORD, _HANDLE]
    _WIN_CREATE_FILEW.restype = _HANDLE
    _WIN_GET_CURRENT_PROCESS = _DLL.GetCurrentProcess
    _WIN_GET_CURRENT_PROCESS.argtypes = []
    _WIN_GET_CURRENT_PROCESS.restype = _HANDLE
    _WIN_DUPLICATE_HANDLE = _DLL.DuplicateHandle
    _WIN_DUPLICATE_HANDLE.argtypes = [_HANDLE, _HANDLE, _HANDLE,
        ctypes.POINTER(_HANDLE), _DWORD, _BOOL, _DWORD]
    _WIN_DUPLICATE_HANDLE.restype = _BOOL
    _WIN_GET_FILE_INFORMATION = _DLL.GetFileInformationByHandle
    _WIN_GET_FILE_INFORMATION.argtypes = [_HANDLE, ctypes.POINTER(_HandleInfo)]
    _WIN_GET_FILE_INFORMATION.restype = _BOOL
    _WIN_GET_FINAL_PATH = _DLL.GetFinalPathNameByHandleW
    _WIN_GET_FINAL_PATH.argtypes = [_HANDLE, wintypes.LPWSTR, _DWORD, _DWORD]
    _WIN_GET_FINAL_PATH.restype = _DWORD
    _WIN_CLOSE_HANDLE = _DLL.CloseHandle
    _WIN_CLOSE_HANDLE.argtypes = [_HANDLE]
    _WIN_CLOSE_HANDLE.restype = _BOOL
    _CRT_OPEN_OSFHANDLE, _CRT_GET_OSFHANDLE = msvcrt.open_osfhandle, msvcrt.get_osfhandle
    _GET_LAST_ERROR, _BYREF, _BUFFER = ctypes.get_last_error, ctypes.byref, ctypes.create_unicode_buffer


class AuthorityReplicaDirectoryError(ValueError):
    def __init__(self, suffix):
        self.code = "AUTHORITY_REPLICA_DIRECTORY1_" + suffix
        super().__init__(self.code)


def _fail(suffix, *, _error=AuthorityReplicaDirectoryError,
        _new=ValueError.__new__, _init=BaseException.__init__,
        _storage=BaseException.__dict__["__dict__"], _dict=dict, _type=type, _str=str):
    if _type(suffix) is not _str:
        suffix = "RECORD_CHANGED"
    code = "AUTHORITY_REPLICA_DIRECTORY1_" + suffix
    error = _new(_error)
    _init(error, code)
    _dict.__setitem__(_storage.__get__(error, _error), "code", code)
    raise error from None


class _NativeScopeKey:
    __slots__ = ("__weakref__",)

    def __init__(self, tag):
        if tag is not _TAG:
            _fail("GUARD_REQUIRED")


class _DirectoryKey:
    __slots__ = ("__weakref__",)

    def __init__(self, tag):
        if tag is not _TAG:
            _fail("GUARD_REQUIRED")


class _ResourceKey:
    __slots__ = ("__weakref__",)

    def __init__(self, tag):
        if tag is not _TAG:
            _fail("GUARD_REQUIRED")


class _NativeScope:
    __slots__ = ("key", "registration", "operation_token", "owner", "budget",
        "chain", "scope_id", "phase", "holders", "directory_keys", "events",
        "next_ordinal", "resource_acquires", "resource_normal_closes",
        "current_resources", "peak_owned_resources", "pending_identities",
        "unknown_resources", "begin", "end", "attributable", "normal_closed",
        "debt_transferred", "retired")


class _ResourceHolder:
    __slots__ = ("key", "scope", "ordinal", "purpose", "kind", "ticket", "lease",
        "raw", "fd", "outcome", "ownership", "closer", "resource_id",
        "acquired_sequence", "D", "identity_sha256", "transfer", "close_attempted",
        "close_outcome", "normal_closed", "release_attempted", "lease_released",
        "debt_submitted")


def _classes_of_native_signature_original(functions):
    result, seen = [], set()
    for function in functions:
        if function is None:
            continue
        todo = [type(function), *_GET(function, "argtypes"), _GET(function, "restype")]
        while todo:
            value = todo.pop()
            if not isinstance(value, type) or id(value) in seen:
                continue
            seen.add(id(value))
            result.append(value)
            todo.extend(type.__getattribute__(value, "__mro__"))
            fields = type.__getattribute__(value, "__dict__").get("_fields_", ())
            if type(fields) in (list, tuple):
                todo.extend(field[1] for field in fields if type(field) is tuple and len(field) >= 2)
            dictionary = type.__getattribute__(value, "__dict__")
            for name in ("_type_", "_argtypes_", "_restype_"):
                nested = dictionary.get(name)
                if isinstance(nested, type):
                    todo.append(nested)
                elif type(nested) is tuple:
                    todo.extend(x for x in nested if isinstance(x, type))
    return tuple(result)


_NATIVE_FUNCTIONS = () if os.name != "nt" else (_WIN_CREATE_FILEW,
    _WIN_GET_CURRENT_PROCESS, _WIN_DUPLICATE_HANDLE, _WIN_GET_FILE_INFORMATION,
    _WIN_GET_FINAL_PATH, _WIN_CLOSE_HANDLE)
_ABI_CLASSES = _classes_of_native_signature_original(_NATIVE_FUNCTIONS)
_NATIVE_SIGNATURES = tuple((f, _GET(f, "argtypes"), tuple(_GET(f, "argtypes")),
    _GET(f, "restype"), _GET(f, "errcheck")) for f in _NATIVE_FUNCTIONS)
_NATIVE_META_PINS = tuple((cls, type(cls)) for cls in
    (_ABI_CLASSES + (_NativeScopeKey, _DirectoryKey, _ResourceKey, _NativeScope,
        _ResourceHolder, AuthorityReplicaDirectoryError)))
_ABI_STRUCTURE_FIELDS = tuple((cls, type.__getattribute__(cls, "__dict__")["_fields_"],
    tuple(type.__getattribute__(cls, "__dict__")["_fields_"]))
    for cls in _ABI_CLASSES if "_fields_" in type.__getattribute__(cls, "__dict__"))
_NATIVE_MODULE_LINKS = (sys.modules, tuple((m.__name__, m) for m in
    ((ctypes, hashlib, os, stat, sys, weakref) + ((wintypes, msvcrt) if os.name == "nt" else ()))))
_STRUCTURE_LAYOUT = () if os.name != "nt" else (
    ctypes.sizeof(_HandleInfo), ctypes.alignment(_HandleInfo),
    tuple((name, field_type, getattr(_HandleInfo, name).offset,
        getattr(_HandleInfo, name).size) for name, field_type in _HandleInfo._fields_))


def _plan_data_chain_original(inputs):
    """Pure DOS representation preflight, before registration/guard native."""
    path = inputs[9][0][0]
    if type(path) is not str:
        _fail("PATH_UNSUPPORTED")
    # The profile has already passed S0.  Preserve its exact projection and
    # reject unsupported representations instead of resolve/long-path rewriting.
    if (len(path) < 3 or path[0] not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
            or path[1:3] != ":\\" or "/" in path or "\0" in path
            or len(path) >= 260 or path.startswith("\\")):
        _fail("PATH_UNSUPPORTED")
    pieces = path[3:].split("\\")
    if not pieces or not pieces[-1]:
        _fail("PATH_UNSUPPORTED")
    # Last component is the database; DATA is exactly its lexical parent.
    directories = pieces[:-1]
    for component in pieces:
        if (not component or component in (".", "..") or component[-1] in " ."
                or any(ord(c) < 32 or c in '<>:"/\\|?*' for c in component)
                or component.split(".", 1)[0].upper() in
                    ("CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5",
                     "COM6", "COM7", "COM8", "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5",
                     "LPT6", "LPT7", "LPT8", "LPT9")):
            _fail("PATH_UNSUPPORTED")
    chain = [path[:3]]
    for component in directories:
        chain.append(chain[-1] + ("" if len(chain) == 1 else "\\") + component)
    n = len(chain)
    if not 1 <= n <= _MAX_DIRECTORIES or 6 * n > _MAX_EVENTS or 3 * n >= 100000000:
        _fail("REPRESENTATION_LIMIT")
    return tuple(chain)


def _require_supported_original():
    """Pure supported-ABI gate; never open/stat/duplicate/probe an object."""
    operation._integrity_original()
    if (_BACKEND_NAME != "nt" or os.name != "nt"
            or _INTERPRETER != ("cpython", (3, 12, 10), 8)
            or (sys.implementation.name, tuple(sys.version_info[:3]),
                ctypes.sizeof(ctypes.c_void_p)) != _INTERPRETER
            or sys.maxsize != 2**63 - 1 or ctypes.sizeof(ctypes.c_wchar) != 2
            or ctypes.sizeof(_HANDLE) != 8 or ctypes.sizeof(_BOOL) != 4
            or ctypes.sizeof(_DWORD) != 4 or _STRUCTURE_LAYOUT[:2] != (52, 4)
            or _STRUCTURE_LAYOUT[2] != (
                ("attributes", _DWORD, 0, 4), ("created", wintypes.FILETIME, 4, 8),
                ("accessed", wintypes.FILETIME, 12, 8), ("written", wintypes.FILETIME, 20, 8),
                ("volume", _DWORD, 28, 4), ("size_high", _DWORD, 32, 4),
                ("size_low", _DWORD, 36, 4), ("links", _DWORD, 40, 4),
                ("index_high", _DWORD, 44, 4), ("index_low", _DWORD, 48, 4))):
        _fail("BACKEND_UNSUPPORTED")


def _identity_original(value):
    if type(value) is not os.stat_result:
        _fail("RECORD_CHANGED")
    device, inode = value.st_dev, value.st_ino
    if (type(device) is not int or type(inode) is not int or device < 0 or inode <= 0
            or len(str(device)) > 40 or len(str(inode)) > 40):
        _fail("IDENTITY_CHANGED")
    if not stat.S_ISDIR(value.st_mode) or value.st_file_attributes & _REPARSE_ATTRIBUTE:
        _fail("IDENTITY_CHANGED")
    return {"device": str(device), "inode": str(inode)}


def _identity_hash_original(identity):
    # All values are bounded decimal ASCII from original stat_result.  This
    # local encoder is the canonical J for exactly the two fixed keys.
    raw = ('{"device":"' + identity["device"] + '","inode":"' + identity["inode"] + '"}').encode("ascii")
    return _HASH(raw).hexdigest()


def _events_hash_original(events):
    rows = []
    for event in events:
        if (type(event) is not dict or set(event) != {"sequence", "resource_id", "kind", "action", "identity_sha256"}
                or event["kind"] != "directory_handle" or event["action"] not in ("acquired", "normal_closed")
                or type(event["sequence"]) is not int or not 1 <= event["sequence"] <= _MAX_EVENTS
                or type(event["resource_id"]) is not str or len(event["resource_id"]) != 9
                or event["resource_id"][0] != "r" or not event["resource_id"][1:].isdigit()
                or type(event["identity_sha256"]) is not str or len(event["identity_sha256"]) != 64
                or any(c not in "0123456789abcdef" for c in event["identity_sha256"])):
            _fail("RECORD_CHANGED")
        rows.append('{"action":"' + event["action"] + '","identity_sha256":"' + event["identity_sha256"]
            + '","kind":"directory_handle","resource_id":"' + event["resource_id"]
            + '","sequence":' + str(event["sequence"]) + '}')
    return _HASH(_EVENT_DOMAIN + ("[" + ",".join(rows) + "]").encode("ascii")).hexdigest()


def _make_ownership_kernel_original(*, type=type, dict=dict, list=list, tuple=tuple,
        str=str, int=int, bool=bool, len=len, id=id, any=any, all=all, zip=zip,
        enumerate=enumerate, reversed=reversed, max=max, BaseException=BaseException,
        next=next, sum=sum, range=range, function_type=FunctionType, q_keys=_Q_KEYS,
        exception_new=ValueError.__new__, exception_init=BaseException.__init__,
        exception_dictionary=BaseException.__dict__["__dict__"],
        object=object, weak=weakref.ref):
    """Trusted captured ownership/cleanup root, bound once before catalog seal.

    Mirrors are inspectable but do not confer ownership.  Authoritative holder
    state lives in these closure registries and is retired, never retained in
    weak history/debt.  Cleanup uses original C member descriptors, immutable
    builtin primitives, and per-closer prototype/class snapshots.  Mutating
    this minimal closure root itself is outside the original-entry model.
    """
    scopes, resources = {}, {}
    gate = [False]  # Preallocated permanent failure latch, no clear interface.
    S, H, SK, DK, RK = _NativeScope, _ResourceHolder, _NativeScopeKey, _DirectoryKey, _ResourceKey
    tag, error_type = _TAG, AuthorityReplicaDirectoryError
    operation_error_type = operation.AuthorityReplicaOperationError
    ss = tuple((name, _CLASS_GET(S, "__dict__")[name]) for name in S.__slots__)
    hs = tuple((name, _CLASS_GET(H, "__dict__")[name]) for name in H.__slots__)
    ssmap, hsmap = dict(ss), dict(hs)
    get, setitem, pop = dict.get, dict.__setitem__, dict.pop
    append, put = list.append, list.__setitem__
    first, peek = operation._FIRST_ORIGINAL, operation._FIRST_PEEK
    debts, lock = operation._ORIGINAL_CONTAINERS[4], operation._ORIGINAL_LOCK
    close_fd, close_handle, last_error = _CLOSE, _WIN_CLOSE_HANDLE, _GET_LAST_ERROR
    getattribute, class_get = object.__getattribute__, type.__getattribute__
    native_function_type = None if close_handle is None else type(close_handle)
    native_pin = None if close_handle is None else (
        close_handle, getattribute(close_handle, "argtypes"), tuple(getattribute(close_handle, "argtypes")),
        getattribute(close_handle, "restype"), getattribute(close_handle, "errcheck"))
    # Snapshot both function-pointer and every marshalling type's full MRO,
    # descriptors, fields and Python hooks, not just the callable object.
    close_classes = () if close_handle is None else _classes_of_native_signature_original((close_handle,))
    close_class_pins = tuple((cls, type(cls), class_get(cls, "__mro__"), tuple(
        (base, tuple(class_get(base, "__dict__").items()))
        for base in class_get(cls, "__mro__"))) for cls in close_classes)
    close_python_pins = []
    for _cls, _meta, _mro, entries in close_class_pins:
        for _base, fields in entries:
            for _name, value in fields:
                if type(value) is function_type:
                    append(close_python_pins, (value, getattribute(value, "__code__"),
                        getattribute(value, "__defaults__"), getattribute(value, "__kwdefaults__"),
                        getattribute(value, "__closure__")))
    pending_name, leases_name = "_resource_pending", "_resource_leases"

    def make_error(suffix):
        # The error class itself is mutable. Trusted cleanup must not call a
        # poisoned __init__/__new__/__setattr__/code or __dict__ descriptor.
        # Original immutable C exception allocation/init/storage descriptors
        # create the coded instance without dispatching any Python class hook.
        code = "AUTHORITY_REPLICA_DIRECTORY1_" + suffix
        value = exception_new(error_type)
        exception_init(value, code)
        storage = exception_dictionary.__get__(value, error_type)
        setitem(storage, "code", code)
        return value

    def make_reentrant():
        code = "AUTHORITY_REPLICA_OPERATION1_REENTRANT"
        value = exception_new(operation_error_type)
        exception_init(value, code)
        setitem(exception_dictionary.__get__(value, operation_error_type), "code", code)
        return value

    def reject_callback():
        # Original inside-callback state is independent of all inspectable
        # record/budget flags. Reject before binding helper inputs or returning
        # any authoritative dictionary, including malformed-key/type calls.
        operation._integrity_original()
        owner = operation._owner_original()
        for state in tuple(dict.values(scopes)):
            if state["inside_callback"] and operation._same_owner(state["owner"], owner):
                raise remember(state, make_reentrant())

    def sync_scope(state):
        for name, slot in ss:
            slot.__set__(state["object"], state[name])

    def sync_holder(state):
        for name, slot in hs:
            slot.__set__(state["object"], state[name])

    def remember(state, error):
        return first(state["registration"], error)

    def new_scope(registration, chain):
        reject_callback()
        operation._integrity_original()
        if type(registration) is not operation._OperationRegistration:
            raise make_error("GUARD_REQUIRED")
        actual = operation._token_registration_original(registration.token)
        if (actual is not registration or registration.kind != "target_data_chain_unit"
                or registration.phase != "native_ready" or registration.native_scope is not None
                or registration.callback_active or type(chain) is not tuple
                or operation._driver_anchor_original(registration)[2] is not chain):
            raise make_error("PHASE_INVALID")
        operation._record_original(registration)
        key = SK(tag)
        scope = object.__new__(S)
        state = {"object": scope, "key": key, "registration": registration,
            "operation_token": registration.token, "owner": registration.owner,
            "budget": registration.budget, "chain": chain,
            "scope_id": str(operation._UUID4()), "phase": "created", "holders": [],
            "directory_keys": [], "events": [], "next_ordinal": 0, "resource_acquires": 0,
            "resource_normal_closes": 0, "current_resources": 0, "peak_owned_resources": 0,
            "pending_identities": 0, "unknown_resources": 0, "begin": None, "end": None,
            "attributable": {name: 0 for name in q_keys}, "normal_closed": False,
            "debt_transferred": False, "retired": False,
            "budget_dict": registration.budget_dict, "holder_states": [],
            "event_slots": [{"sequence": 0, "resource_id": "", "kind": "directory_handle",
                "action": "", "identity_sha256": None} for _ in range(6 * len(chain))],
            "event_facts": [], "pending_identity_keys": [], "original_limits": registration.budget_dict["limits"],
            "started": registration.budget_dict["_started"],
            "fixed_budget": registration.budget_fixed, "budget_anchor": registration.budget_anchor,
            "operation_id": registration.inputs[4], "run_id": registration.run_id,
            "original_directory_keys": [], "identity_facts": {}, "snapshot_facts": {}, "terminal_depth": 0, "inside_callback": False}
        scopes[id(key)] = state
        sync_scope(state)
        return scope

    def validate_new_scope(registration, scope, chain):
        # A pre-attach pure gate: verify this sole original ownership registry,
        # not a caller scope/mirror or a newly constructed kernel. No state or
        # cleanup capability is returned to the operation driver.
        reject_callback()
        if type(registration) is not operation._OperationRegistration or type(scope) is not S:
            raise make_error("GUARD_REQUIRED")
        state = resolve(ssmap["key"].__get__(scope, S))
        if not operation._same_owner(state["owner"], operation._owner_original()):
            raise make_error("OWNER_MISMATCH")
        if (operation._token_registration_original(state["operation_token"]) is not registration
                or state["registration"] is not registration or state["object"] is not scope
                or state["chain"] is not chain or state["budget"] is not registration.budget
                or state["budget_dict"] is not registration.budget_dict
                or state["budget_anchor"] is not registration.budget_anchor
                or state["phase"] != "created" or state["inside_callback"] is not False
                or registration.phase != "native_ready" or registration.native_scope is not None
                or registration.callback_active is not False
                or state["budget_dict"]["_in_callback"] is not False
                or operation._driver_anchor_original(registration)[2] is not chain):
            raise make_error("PHASE_INVALID")
        operation._record_original(registration)
        validate(state)

    def resolve(key):
        state = get(scopes, id(key))
        if type(key) is not SK or state is None or state["key"] is not key or state["retired"]:
            raise make_error("CLOSED")
        return state

    def resolve_holder(key):
        state = get(resources, id(key))
        if type(key) not in (DK, RK) or state is None or state["key"] is not key:
            raise make_error("GUARD_REQUIRED")
        if state["scope_state"]["retired"]:
            raise make_error("CLOSED")
        return state

    def holder(scope_state, ordinal, purpose):
        value = object.__new__(H)
        key = DK(tag) if purpose == "H" else RK(tag)
        state = {"object": value, "key": key, "scope": scope_state["object"],
            "ordinal": ordinal, "purpose": purpose, "kind": "directory_handle", "ticket": None,
            "lease": None, "raw": None, "fd": None, "outcome": "not_started", "ownership": "empty",
            "closer": None, "resource_id": None, "acquired_sequence": None, "D": None,
            "identity_sha256": None, "transfer": "not_started", "close_attempted": False,
            "close_outcome": "not_attempted", "normal_closed": False,
            "release_attempted": False, "lease_released": False, "debt_submitted": False,
            "scope_state": scope_state, "event": None, "native_started": False,
            "settlement_attempted": False, "lease_count": None, "native_error": None}
        # Complete registration before a begin ticket or any native primitive.
        append(scope_state["holder_states"], state)
        resources[id(key)] = state
        append(scope_state["holders"], value)
        sync_holder(state)
        sync_scope(scope_state)
        return state

    def update_scope(state, **fields):
        for name, value in dict.items(fields):
            if name in ("begin", "end"):
                if type(value) is not dict or any(type(k) is not str or type(v) is not int or v < 0
                        for k, v in dict.items(value)):
                    raise make_error("ACCOUNTING_CHANGED")
                state["snapshot_facts"][name] = tuple(dict.items(value))
            state[name] = value
        sync_scope(state)

    def update_holder(state, **fields):
        for name, value in dict.items(fields):
            state[name] = value
        sync_holder(state)

    def append_event(state, action):
        scope = state["scope_state"]
        events, facts = scope["events"], scope["event_facts"]
        position = len(events)
        if position >= len(scope["event_slots"]):
            raise make_error("REPRESENTATION_LIMIT")
        event = scope["event_slots"][position]
        event["sequence"], event["resource_id"] = position + 1, state["resource_id"]
        event["action"], event["identity_sha256"] = action, state["identity_sha256"]
        append(events, event)
        append(facts, (event, position + 1, state["resource_id"], action, state["identity_sha256"]))
        if action == "acquired":
            state["event"], state["acquired_sequence"] = event, position + 1
            append(scope["pending_identity_keys"], state["key"])
            scope["pending_identities"] += 1
        sync_holder(state)
        sync_scope(scope)

    def acquired(state, raw):
        scope = state["scope_state"]
        # Actual ownership and scalar outcome precede encoding/lease/errors.
        state["raw"], state["ownership"], state["outcome"], state["closer"] = raw, "owned", "acquired", "handle"
        state["resource_id"] = "r" + str(scope["resource_acquires"]).zfill(8)
        scope["resource_acquires"] += 1
        scope["current_resources"] += 1
        scope["peak_owned_resources"] = max(scope["peak_owned_resources"], scope["current_resources"])
        sync_holder(state)
        sync_scope(scope)
        # Event insertion is a separately fallible step after real ownership.
        append_event(state, "acquired")

    def finalize(state, identity, commitment):
        scope, event = state["scope_state"], state["event"]
        if state["identity_sha256"] is not None or event is None or state["key"] not in scope["pending_identity_keys"]:
            raise make_error("PHASE_INVALID")
        state["D"], state["identity_sha256"] = dict(identity), commitment
        scope["identity_facts"][id(state["key"])] = (identity["device"], identity["inode"])
        event["identity_sha256"] = commitment
        index = state["acquired_sequence"] - 1
        old = scope["event_facts"][index]
        scope["event_facts"][index] = (*old[:4], commitment)
        scope["pending_identity_keys"].remove(state["key"])
        scope["pending_identities"] -= 1
        sync_holder(state)
        sync_scope(scope)

    def validate(state):
        # Integrity is checked before calling this successful-path validator.
        # Mirrors may expose inspection fields; all authority remains here.
        for name, slot in ss:
            if slot.__get__(state["object"], S) is not state[name]:
                raise make_error("RECORD_CHANGED")
        holders = state["holders"]
        if type(holders) is not list or len(holders) != len(state["holder_states"]):
            raise make_error("RECORD_CHANGED")
        for index, item in enumerate(state["holder_states"]):
            if holders[index] is not item["object"] or get(resources, id(item["key"])) is not item:
                raise make_error("RECORD_CHANGED")
            for name, slot in hs:
                if slot.__get__(item["object"], H) is not item[name]:
                    raise make_error("RECORD_CHANGED")
        for name in ("begin", "end"):
            value = state[name]
            if value is not None:
                original = get(state["snapshot_facts"], name)
                if (type(value) is not dict or original is None or len(value) != len(original)
                        or any(type(k) is not str or type(v) is not int or v < 0 for k, v in dict.items(value))
                        or any(get(value, k) != v for k, v in original)):
                    raise make_error("ACCOUNTING_CHANGED")
        keys = state["directory_keys"]
        if type(keys) is not list or len(keys) != len(state["original_directory_keys"]) or any(
                a is not b for a, b in zip(keys, state["original_directory_keys"])):
            raise make_error("RECORD_CHANGED")
        for item in state["holder_states"]:
            if item["D"] is not None:
                value, original = item["D"], get(state["identity_facts"], id(item["key"]))
                if (type(value) is not dict or len(value) != 2 or any(type(k) is not str for k in value)
                        or type(get(value, "device")) is not str or type(get(value, "inode")) is not str
                        or original != (get(value, "device"), get(value, "inode"))):
                    raise make_error("RECORD_CHANGED")
        events, facts = state["events"], state["event_facts"]
        if type(events) is not list or len(events) != len(facts):
            raise make_error("RECORD_CHANGED")
        for event, fact in zip(events, facts):
            if (type(event) is not dict or event is not fact[0] or len(event) != 5
                    or any(type(k) is not str for k in event)
                    or type(get(event, "sequence")) is not int or type(get(event, "resource_id")) is not str
                    or type(get(event, "action")) is not str or type(get(event, "kind")) is not str
                    or get(event, "identity_sha256") is not None and type(get(event, "identity_sha256")) is not str
                    or get(event, "sequence") != fact[1] or get(event, "resource_id") != fact[2]
                    or get(event, "action") != fact[3] or get(event, "identity_sha256") != fact[4]
                    or get(event, "kind") != "directory_handle"):
                raise make_error("RECORD_CHANGED")
        dictionary = state["budget_dict"]
        if type(dictionary) is not dict or any(type(k) is not str for k in dictionary):
            raise make_error("ACCOUNTING_CHANGED")
        if dictionary is not getattribute(state["budget"], "__dict__"):
            raise make_error("ACCOUNTING_CHANGED")
        for name, original in state["fixed_budget"]:
            if get(dictionary, name) is not original:
                raise make_error("ACCOUNTING_CHANGED")
        pending, leases = get(dictionary, pending_name), get(dictionary, leases_name)
        if type(pending) is not dict or type(leases) is not dict or any(type(k) is not object or type(v) is not int or v < 0 for k, v in dict.items(pending)) or any(
                type(k) is not object or type(v) is not int or v < 0 for k, v in dict.items(leases)):
            raise make_error("ACCOUNTING_CHANGED")
        expected_pending, expected_leases, uncertain = {}, {}, 0
        for item in state["holder_states"]:
            if item["ticket"] is not None and not item["settlement_attempted"]:
                expected_pending[item["ticket"]] = 1
            if item["lease"] is not None and not item["lease_released"]:
                expected_leases[item["lease"]] = item["lease_count"]
            if item["outcome"] == "unknown_acquire":
                uncertain += 1
        if pending != expected_pending or leases != expected_leases:
            raise make_error("ACCOUNTING_CHANGED")
        if (get(dictionary, "_pending_resources") != sum(expected_pending.values())
                or get(dictionary, "_current_resources") != sum(expected_leases.values())
                or get(dictionary, "_peak_resources") != state["peak_owned_resources"]
                or get(dictionary, "_uncertain_resources") != uncertain):
            raise make_error("ACCOUNTING_CHANGED")
        if (state["inside_callback"] and (state["registration"].callback_active is not True
                or state["registration"].record.callback_active is not True
                or get(dictionary, "_in_callback") is not True)):
            raise make_error("RECORD_CHANGED")
        if get(dictionary, "_terminal") != state["terminal_depth"]:
            raise make_error("ACCOUNTING_CHANGED")
        if (type(state["attributable"]) is not dict or any(type(k) is not str or type(v) is not int
                for k, v in dict.items(state["attributable"])) or state["attributable"] != {name: 0 for name in q_keys}):
            raise make_error("ACCOUNTING_CHANGED")

    def trusted_handle_closer():
        if native_pin is None:
            return False
        try:
            function, args, entries, result_type, errcheck = native_pin
            if type(function) is not native_function_type:
                return False
            # Verify function-pointer/marshalling classes BEFORE retrieving any
            # mutable signature descriptor from the native callable object.
            for cls, metaclass, mro, bases in close_class_pins:
                if type(cls) is not metaclass:
                    return False
                actual_mro = class_get(cls, "__mro__")
                if len(actual_mro) != len(mro) or any(a is not b for a, b in zip(actual_mro, mro)):
                    return False
                for base, fields in bases:
                    dictionary = class_get(base, "__dict__")
                    if len(dictionary) != len(fields) or any(type(k) is not str for k in dictionary):
                        return False
                    if any(name not in dictionary or dictionary.get(name) is not original for name, original in fields):
                        return False
            current = getattribute(function, "argtypes")
            if (current is not args or type(current) is not list or len(current) != len(entries)
                    or any(a is not b for a, b in zip(current, entries))
                    or getattribute(function, "restype") is not result_type
                    or getattribute(function, "errcheck") is not errcheck):
                return False
            for function, code, defaults, kwdefaults, closure in close_python_pins:
                if (getattribute(function, "__code__") is not code or getattribute(function, "__defaults__") is not defaults
                        or getattribute(function, "__kwdefaults__") is not kwdefaults
                        or getattribute(function, "__closure__") is not closure):
                    return False
            return True
        except BaseException:
            return False

    def release_known_closed(state):
        # Strict captured implementation of the original release operation:
        # exact original ledger containers + exact lease, once, only AFTER
        # actual normal native close. This never refunds an unknown resource.
        if state["release_attempted"] or state["lease"] is None:
            if state["lease"] is None:
                raise make_error("ACCOUNTING_CHANGED")
            return
        state["release_attempted"] = True
        sync_holder(state)
        scope, lease = state["scope_state"], state["lease"]
        dictionary = scope["budget_dict"]
        if (not state["normal_closed"] or type(dictionary) is not dict
                or any(type(k) is not str for k in dictionary)
                or get(dictionary, "_in_callback") is not False):
            raise make_error("ACCOUNTING_CHANGED")
        anchor = scope["budget_anchor"]
        if type(anchor) is not tuple or anchor[0] is not scope["budget"] or anchor[1] is not dictionary:
            raise make_error("ACCOUNTING_CHANGED")
        leases = get(dictionary, leases_name)
        original_leases = next(v for k, v in scope["fixed_budget"] if k == leases_name)
        if (leases is not original_leases or type(leases) is not dict
                or any(type(k) is not object for k in leases) or type(lease) is not object
                or get(leases, lease, None) != state["lease_count"]
                or type(state["lease_count"]) is not int or state["lease_count"] not in (0, 1)
                or type(get(dictionary, "_current_resources")) is not int
                or get(dictionary, "_current_resources") < state["lease_count"]):
            raise make_error("ACCOUNTING_CHANGED")
        count = pop(leases, lease)
        setitem(dictionary, "_current_resources", get(dictionary, "_current_resources") - count)
        state["lease_released"] = True
        sync_holder(state)

    def retain(state):
        scope = state["scope_state"]
        if state["debt_submitted"]:
            return
        if (state["ownership"] != "unknown" and state["close_outcome"] not in ("false", "unknown", "untrusted")):
            raise make_error("PHASE_INVALID")
        # This independent preallocated gate precedes every detailed allocation.
        put(gate, 0, True)
        state["debt_submitted"] = True
        scope["debt_transferred"] = True
        sync_holder(state)
        sync_scope(scope)
        owner = scope["owner"]
        raw = state["raw"] if type(state["raw"]) is int else None
        payload = {"operation_id": scope["operation_id"],
            "run_id": scope["run_id"], "scope_id": scope["scope_id"],
            "kind": "directory_handle", "purpose": state["purpose"], "ordinal": state["ordinal"],
            "raw_outcome": raw, "fd_outcome": state["fd"] if type(state["fd"]) is int else None,
            "ownership": state["ownership"], "unique_closer": state["closer"],
            "close_attempted": state["close_attempted"], "close_outcome": state["close_outcome"],
            "pending_reservation": int(state["ticket"] is not None and not state["settlement_attempted"]),
            "lease_reservation": 0 if state["lease_released"] else (state["lease_count"] or 0),
            "code": "AUTHORITY_REPLICA_DIRECTORY1_OWNERSHIP_UNKNOWN",
            "pid": owner[0], "thread": owner[1], "task": None if owner[2] is None else weak(owner[2])}
        with lock:
            if type(debts) is not dict or any(type(k) is not int for k in debts):
                raise make_error("UNSETTLED")
            setitem(debts, id(state["key"]), payload)

    def close_one(state):
        scope = state["scope_state"]
        if state["ownership"] == "unknown":
            retain(state)
            return
        if state["normal_closed"]:
            # A prior actual normal close can never be retried. If an earlier
            # Python bookkeeping interruption preceded its independent release
            # stage, only the not-yet-attempted lease stage remains eligible.
            if not state["release_attempted"]:
                release_known_closed(state)
            return
        if state["close_attempted"]:
            return
        if state["ownership"] == "empty":
            if state["lease"] is not None and not state["lease_released"]:
                # Explicit acquired=0 has no OS closer; strict once-empty lease.
                state["normal_closed"] = True
                try:
                    release_known_closed(state)
                finally:
                    state["normal_closed"] = False
                    sync_holder(state)
            return
        if state["ownership"] != "owned":
            raise make_error("RECORD_CHANGED")
        try:
            if state["closer"] == "handle":
                if (type(state["raw"]) is not int or not 0 < state["raw"] < 2**64 - 1
                        or not trusted_handle_closer()):
                    state["close_outcome"] = "untrusted"
                    raise make_error("CLOSE_UNKNOWN")
                state["close_attempted"] = True
                sync_holder(state)
                result = close_handle(state["raw"])
                captured_error = last_error()
                if type(result) is not int or result == 0:
                    state["close_outcome"] = "false" if type(result) is int and result == 0 else "unknown"
                    state["native_error"] = captured_error
                    raise make_error("CLOSE_UNKNOWN")
            elif state["closer"] == "fd":
                if type(state["fd"]) is not int or state["fd"] < 0:
                    state["close_outcome"] = "untrusted"
                    raise make_error("CLOSE_UNKNOWN")
                state["close_attempted"] = True
                sync_holder(state)
                result = close_fd(state["fd"])
                if result is not None:
                    state["close_outcome"] = "unknown"
                    raise make_error("CLOSE_UNKNOWN")
            else:
                state["close_outcome"] = "untrusted"
                raise make_error("CLOSE_UNKNOWN")
        except BaseException as exc:
            if state["close_outcome"] == "not_attempted":
                state["close_outcome"] = "unknown"
            state["ownership"] = "unknown"
            scope["unknown_resources"] += 1
            sync_holder(state)
            sync_scope(scope)
            remember(scope, exc)
            retain(state)
            return
        # Record real normal native outcome BEFORE event/release processing.
        state["normal_closed"], state["close_outcome"], state["ownership"] = True, "normal", "closed"
        scope["current_resources"] -= 1
        scope["resource_normal_closes"] += 1
        sync_holder(state)
        sync_scope(scope)
        try:
            if state["identity_sha256"] is None:
                raise make_error("IDENTITY_CHANGED")
            append_event(state, "normal_closed")
        except BaseException as exc:
            remember(scope, exc)
        finally:
            try:
                release_known_closed(state)
            except BaseException as exc:
                remember(scope, exc)

    def leave_terminal(state):
        if state["terminal_depth"] == 0:
            return
        dictionary = state["budget_dict"]
        if (state["terminal_depth"] != 1 or type(dictionary) is not dict
                or any(type(k) is not str for k in dictionary)
                or type(get(dictionary, "_terminal")) is not int or get(dictionary, "_terminal") != 1
                or get(dictionary, "_in_callback") is not False):
            remember(state, make_error("ACCOUNTING_CHANGED"))
            return
        # One genuine entered suppression window exits once. No reset of a
        # damaged field/first/clock and no callback on this trusted finally.
        setitem(dictionary, "_terminal", 0)
        state["terminal_depth"] = 0

    def cleanup(state):
        state["phase"] = "failed_cleanup" if peek(state["registration"]) is not None else "closing"
        sync_scope(state)
        # Authoritative sequence retained before caller code, never the mirror.
        for transient in reversed(state["holder_states"]):
            if transient["purpose"] != "H":
                try:
                    close_one(transient)
                except BaseException as exc:
                    remember(state, exc)
        for original in reversed(state["holder_states"]):
            if original["purpose"] == "H":
                try:
                    close_one(original)
                except BaseException as exc:
                    remember(state, exc)
        # All unknowns must cross the preallocated permanent gate before pop.
        for item in state["holder_states"]:
            if item["ownership"] == "unknown" and not item["debt_submitted"]:
                put(gate, 0, True)
                try:
                    retain(item)
                except BaseException as exc:
                    remember(state, exc)

        leave_terminal(state)

    def retirement(state, success):
        cleanup(state)
        if any(item["ownership"] == "unknown" for item in state["holder_states"]):
            put(gate, 0, True)
        state["retired"] = True
        state["phase"] = "retired_closed" if success and state["normal_closed"] else "retired_failed"
        sync_scope(state)
        for item in state["holder_states"]:
            pop(resources, id(item["key"]), None)
        pop(scopes, id(state["key"]), None)

    def blocked():
        return gate[0] is True

    # Export separate wrappers. Holding a wrapper object does not make its
    # mutable __code__ trusted: fixed drivers instead capture the unexported
    # per-scope closures below BEFORE the first user callback.
    def admit_scope(state):
        reject_callback()
        operation._integrity_original()
        if (type(state) is not dict or any(type(k) is not str for k in state)
                or resolve(get(state, "key")) is not state):
            raise make_error("GUARD_REQUIRED")
        if not operation._same_owner(state["owner"], operation._owner_original()):
            raise make_error("OWNER_MISMATCH")
        if state["inside_callback"]:
            raise remember(state, make_error("PHASE_INVALID"))
        registration = operation._token_registration_original(state["operation_token"])
        if registration is not state["registration"] or registration.native_scope is not state["object"]:
            raise make_error("RECORD_CHANGED")
        return state
    def admit_holder(state):
        reject_callback()
        operation._integrity_original()
        if (type(state) is not dict or any(type(k) is not str for k in state)
                or resolve_holder(get(state, "key")) is not state):
            raise make_error("GUARD_REQUIRED")
        admit_scope(state["scope_state"])
        return state
    def exposed_scope(key):
        reject_callback()
        state = resolve(key)
        return admit_scope(state)
    def exposed_holder(key):
        reject_callback()
        state = resolve_holder(key)
        return admit_holder(state)
    def exposed_holder_registration(state, ordinal, purpose):
        admit_scope(state)
        if (type(ordinal) is not int or type(purpose) is not str or purpose not in ("H", "H2", "H3")
                or not 0 <= ordinal < len(state["chain"])
                or purpose in ("H", "H2") and (state["phase"] != "acquiring" or ordinal != state["next_ordinal"])
                or purpose == "H3" and state["phase"] != "closing"):
            raise make_error("PHASE_INVALID")
        return holder(state, ordinal, purpose)
    def exposed_scope_update(state, **fields):
        admit_scope(state)
        allowed = ("phase", "next_ordinal", "resource_acquires", "resource_normal_closes",
            "current_resources", "peak_owned_resources", "pending_identities", "unknown_resources",
            "begin", "end", "normal_closed", "debt_transferred", "retired")
        if any(type(k) is not str or k not in allowed for k in fields):
            raise make_error("PHASE_INVALID")
        return update_scope(state, **fields)
    def exposed_holder_update(state, **fields):
        admit_holder(state)
        allowed = ("ticket", "lease", "raw", "fd", "outcome", "ownership", "closer",
            "resource_id", "acquired_sequence", "D", "identity_sha256", "transfer",
            "close_attempted", "close_outcome", "normal_closed", "release_attempted",
            "lease_released", "debt_submitted", "native_started", "settlement_attempted",
            "lease_count", "native_error")
        if any(type(k) is not str or k not in allowed for k in fields):
            raise make_error("PHASE_INVALID")
        return update_holder(state, **fields)
    def exposed_acquired(state, raw):
        admit_holder(state)
        if (not state["native_started"] or state["ticket"] is None or state["ownership"] != "empty"
                or type(raw) is not int or state["raw"] is not raw):
            raise make_error("PHASE_INVALID")
        return acquired(state, raw)
    def exposed_finalize(state, identity, commitment):
        admit_holder(state)
        return finalize(state, identity, commitment)
    def exposed_validate(state):
        admit_scope(state)
        return validate(state)
    def readonly_state(registration):
        operation._integrity_original()
        if type(registration) is not operation._OperationRegistration:
            raise make_error("GUARD_REQUIRED")
        scope = registration.native_scope
        if type(scope) is not S:
            raise make_error("GUARD_REQUIRED")
        state = resolve(ssmap["key"].__get__(scope, S))
        if not operation._same_owner(state["owner"], operation._owner_original()):
            raise make_error("OWNER_MISMATCH")
        if state["registration"] is not registration or scope is not state["object"]:
            raise make_error("RECORD_CHANGED")
        return state
    def validate_registration(registration):
        # Sole read-only callback admission: no authority dict/capability
        # escapes. Inspectable mirrors never grant access to mutable state.
        state = readonly_state(registration)
        try:
            validate(state)
        except BaseException as exc:
            raise remember(state, exc)
    def callback_allowed(registration):
        state = readonly_state(registration)
        try:
            validate(state)
        except BaseException as exc:
            raise remember(state, exc)
        return (state["phase"] in ("before_acquire", "component_finalized", "before_final_validation")
            and state["pending_identities"] == 0 and state["terminal_depth"] == 0
            and all(h["purpose"] == "H" or h["ownership"] in ("empty", "closed") for h in state["holder_states"]))
    def begin_callback(registration):
        reject_callback()
        state = readonly_state(registration)
        if (state["inside_callback"] or registration.callback_active is not False
                or state["budget_dict"]["_in_callback"] is not True
                or registration.phase != "native_checkpoint" or not callback_allowed(registration)):
            raise make_error("PHASE_INVALID")
        state["inside_callback"] = True
        def leave_original_callback():
            # Not exported, returned only before the actual user callback.
            # Calling exported setters/getters cannot clear this independent
            # gate even after falsifying record/budget callback mirrors.
            state["inside_callback"] = False
        return leave_original_callback
    def exposed_close(state):
        admit_holder(state)
        return close_one(state)
    def exposed_cleanup(state):
        admit_scope(state)
        return cleanup(state)
    def exposed_retain(state):
        admit_holder(state)
        return retain(state)
    def exposed_retirement(state, success):
        admit_scope(state)
        if type(success) is not bool:
            raise make_error("PHASE_INVALID")
        return retirement(state, success)
    def trusted_callbacks(key):
        state = admit_scope(resolve(key))
        if state["phase"] != "created" or state["registration"].phase != "native_ready":
            raise make_error("PHASE_INVALID")
        def close_original_scope():
            return cleanup(state)
        def retire_original_scope(success):
            return retirement(state, success)
        return close_original_scope, retire_original_scope
    return (new_scope, exposed_scope, exposed_holder, exposed_holder_registration,
        exposed_scope_update, exposed_holder_update, exposed_acquired, exposed_finalize,
        exposed_validate, exposed_close, exposed_cleanup, exposed_retain,
        exposed_retirement, blocked, trusted_callbacks, validate_registration,
        callback_allowed, begin_callback, reject_callback, validate_new_scope)


(_new_scope_original, _scope_state_original, _holder_state_original,
 _register_holder_original, _update_scope_original, _update_holder_original,
 _record_acquired_original, _finalize_event_original, _validate_scope_original,
 _close_state_original, _cleanup_state_original, _retain_state_original,
 _retire_state_original, _failure_blocked_original, _trusted_callbacks_original,
 _validate_registration_original, _callback_allowed_original,
 _begin_callback_original, _require_no_callback_original,
 _validate_new_scope_original) = _make_ownership_kernel_original()
del _make_ownership_kernel_original


def _actual_scope_original(scope_key):
    operation._integrity_original()
    state = _scope_state_original(scope_key)
    registration = state["registration"]
    if not operation._same_owner(state["owner"], operation._owner_original()):
        _fail("OWNER_MISMATCH")
    if (registration.kind != "target_data_chain_unit" or registration.native_scope is not state["object"]
            or registration.token is not state["operation_token"]
            or operation._token_registration_original(registration.token) is not registration):
        _fail("RECORD_CHANGED")
    operation._record_original(registration)
    _validate_scope_original(state)
    return state


def _actual_holder_original(resource_key):
    operation._integrity_original()
    item = _holder_state_original(resource_key)
    state = _actual_scope_original(item["scope_state"]["key"])
    if item["scope_state"] is not state:
        _fail("RECORD_CHANGED")
    return item


def _terminal_original(state, enter):
    # Successful-path caller only. Cleanup never uses this ordinary dispatcher.
    operation._integrity_original()
    _validate_scope_original(state)
    dictionary = state["budget_dict"]
    value = dictionary["_terminal"]
    if type(value) is not int or value not in (0, 1) or dictionary["_in_callback"] is not False:
        _fail("PHASE_INVALID")
    if enter and value != 0 or not enter and value != 1:
        _fail("PHASE_INVALID")
    dictionary["_terminal"] = 1 if enter else 0
    state["terminal_depth"] = 1 if enter else 0


def _registered_item_original(item):
    """Exact registry identity admission without interpreting caller DTOs."""
    operation._integrity_original()
    if type(item) is not dict or any(type(k) is not str for k in item):
        _fail("GUARD_REQUIRED")
    if _holder_state_original(dict.get(item, "key")) is not item:
        _fail("GUARD_REQUIRED")
    state = item["scope_state"]
    if type(state) is not dict or _scope_state_original(state["key"]) is not state:
        _fail("GUARD_REQUIRED")
    if not operation._same_owner(state["owner"], operation._owner_original()):
        _fail("OWNER_MISMATCH")
    registration = operation._token_registration_original(state["operation_token"])
    if registration is not state["registration"] or registration.native_scope is not state["object"]:
        _fail("RECORD_CHANGED")
    return item


def _settle_acquire_original(item, error, acquired):
    # Exactly one ticket settlement, after the actual return/outcome was saved.
    # end_resources errors never route into fail_resources on the same ticket.
    _registered_item_original(item)
    state, budget = item["scope_state"], item["scope_state"]["budget"]
    operation._integrity_original()
    if item["settlement_attempted"] or item["ticket"] is None:
        _fail("ACCOUNTING_CHANGED")
    _update_holder_original(item, settlement_attempted=True)
    if error is None:
        result = budget.end_resources(item["ticket"], acquired)
    else:
        result = budget.fail_resources(item["ticket"], error, acquired=acquired)
    if type(result) is not tuple or len(result) != 2 or type(result[0]) is not object:
        _fail("ACCOUNTING_CHANGED")
    # Capture the returned original lease before handling the returned first.
    _update_holder_original(item, lease=result[0], lease_count=1 if acquired is None else acquired)
    if result[1] is not None:
        raise operation._FIRST_ORIGINAL(state["registration"], result[1])


def _begin_acquire_original(item):
    _registered_item_original(item)
    state = item["scope_state"]
    operation._integrity_original()
    operation._record_original(state["registration"])
    _validate_scope_original(state)
    if state["budget_dict"]["_terminal"] != 1 or item["ticket"] is not None:
        _fail("PHASE_INVALID")
    ticket = state["budget"].begin_resources(1)
    _update_holder_original(item, ticket=ticket)
    state["registration"].budget_last = state["budget_dict"]["_last_tick"]
    _validate_scope_original(state)


def _create_original(item):
    _begin_acquire_original(item)
    _update_holder_original(item, native_started=True)
    try:
        raw = _WIN_CREATE_FILEW(item["scope_state"]["chain"][item["ordinal"]],
            _FILE_LIST_DIRECTORY, _SHARE_READ_WRITE, None, _OPEN_EXISTING, _OPEN_REPARSE_BACKUP, None)
        last_error = _GET_LAST_ERROR()
    except BaseException as exc:
        _update_holder_original(item, ownership="unknown", outcome="unknown_acquire")
        _update_scope_original(item["scope_state"], unknown_resources=item["scope_state"]["unknown_resources"] + 1)
        _settle_acquire_original(item, exc, None)
        raise
    _update_holder_original(item, raw=raw, native_error=last_error)
    if type(raw) is int and raw in (-1, 2**64 - 1):
        _update_holder_original(item, outcome="no_acquire")
        _settle_acquire_original(item, AuthorityReplicaDirectoryError("NATIVE_FAILED"), 0)
    elif type(raw) is int and 0 < raw < 2**64 - 1:
        pending_error = None
        try:
            _record_acquired_original(item, raw)
        except BaseException as exc:
            pending_error = operation._FIRST_ORIGINAL(item["scope_state"]["registration"], exc)
        _settle_acquire_original(item, None, 1)
        if pending_error is not None:
            raise pending_error
    else:
        _update_holder_original(item, ownership="unknown", outcome="unknown_acquire")
        _update_scope_original(item["scope_state"], unknown_resources=item["scope_state"]["unknown_resources"] + 1)
        _settle_acquire_original(item, AuthorityReplicaDirectoryError("OWNERSHIP_UNKNOWN"), None)


def _duplicate_original(original, item):
    _registered_item_original(original)
    _registered_item_original(item)
    if (original["scope_state"] is not item["scope_state"] or original["purpose"] != "H"
            or item["purpose"] not in ("H2", "H3") or original["ordinal"] != item["ordinal"]
            or original["ownership"] != "owned" or original["close_attempted"]):
        _fail("PHASE_INVALID")
    output = _HANDLE()
    process = _WIN_GET_CURRENT_PROCESS()
    _begin_acquire_original(item)
    if type(process) is not int or process not in (-1, 2**64 - 1):
        _settle_acquire_original(item, AuthorityReplicaDirectoryError("NATIVE_FAILED"), 0)
        return
    _update_holder_original(item, native_started=True)
    try:
        returned = _WIN_DUPLICATE_HANDLE(process, original["raw"], process,
            _BYREF(output), 0, False, _DUPLICATE_SAME_ACCESS)
        last_error = _GET_LAST_ERROR()
    except BaseException as exc:
        _update_holder_original(item, raw=output.value, ownership="unknown", outcome="unknown_acquire")
        _update_scope_original(item["scope_state"], unknown_resources=item["scope_state"]["unknown_resources"] + 1)
        _settle_acquire_original(item, exc, None)
        raise
    raw = output.value
    _update_holder_original(item, raw=raw, native_error=last_error)
    if type(returned) is int and returned == 0 and raw is None:
        _update_holder_original(item, outcome="no_acquire")
        _settle_acquire_original(item, AuthorityReplicaDirectoryError("NATIVE_FAILED"), 0)
    elif type(returned) is int and returned != 0 and type(raw) is int and 0 < raw < 2**64 - 1:
        pending_error = None
        try:
            _record_acquired_original(item, raw)
        except BaseException as exc:
            pending_error = operation._FIRST_ORIGINAL(item["scope_state"]["registration"], exc)
        _settle_acquire_original(item, None, 1)
        if pending_error is not None:
            raise pending_error
    else:
        _update_holder_original(item, ownership="unknown", outcome="unknown_acquire")
        _update_scope_original(item["scope_state"], unknown_resources=item["scope_state"]["unknown_resources"] + 1)
        _settle_acquire_original(item, AuthorityReplicaDirectoryError("OWNERSHIP_UNKNOWN"), None)


def _transfer_original(item):
    _registered_item_original(item)
    operation._integrity_original()
    operation._check_budget_original(item["scope_state"]["registration"])
    if item["ownership"] != "owned" or item["closer"] != "handle" or item["transfer"] != "not_started":
        _fail("PHASE_INVALID")
    _update_holder_original(item, transfer="pending")
    try:
        fd = _CRT_OPEN_OSFHANDLE(item["raw"], os.O_RDONLY | os.O_BINARY)
    except OSError:
        # CPython's builtin open_osfhandle raises on CRT -1: ownership was not
        # transferred. No arbitrary wrapper/exception contract is accepted.
        _update_holder_original(item, transfer="not_transferred")
        raise
    except BaseException:
        _update_holder_original(item, transfer="unknown", ownership="unknown")
        _update_scope_original(item["scope_state"], unknown_resources=item["scope_state"]["unknown_resources"] + 1)
        raise
    if type(fd) is not int or fd < 0:
        _update_holder_original(item, transfer="unknown", ownership="unknown")
        _update_scope_original(item["scope_state"], unknown_resources=item["scope_state"]["unknown_resources"] + 1)
        _fail("OWNERSHIP_UNKNOWN")
    # Same original id/ticket/lease, exactly one new unique closer.
    _update_holder_original(item, fd=fd, closer="fd", transfer="transferred")
    if _CRT_GET_OSFHANDLE(fd) != item["raw"]:
        _fail("IDENTITY_CHANGED")


def _handle_relationship_original(original):
    _registered_item_original(original)
    operation._integrity_original()
    state, ordinal = original["scope_state"], original["ordinal"]
    operation._check_budget_original(state["registration"])
    if original["ownership"] != "owned" or original["close_attempted"]:
        _fail("IDENTITY_CHANGED")
    info = _HandleInfo()
    returned = _WIN_GET_FILE_INFORMATION(original["raw"], _BYREF(info))
    last_error = _GET_LAST_ERROR()
    if type(returned) is not int or returned == 0:
        _update_holder_original(original, native_error=last_error)
        _fail("NATIVE_FAILED")
    if not info.attributes & _DIRECTORY_ATTRIBUTE or info.attributes & _REPARSE_ATTRIBUTE:
        _fail("IDENTITY_CHANGED")
    needed = _WIN_GET_FINAL_PATH(original["raw"], None, 0, 0)
    last_error = _GET_LAST_ERROR()
    if type(needed) is not int or not 1 <= needed < _MAX_FINAL_PATH:
        _update_holder_original(original, native_error=last_error)
        _fail("PATH_UNSUPPORTED")
    buffer = _BUFFER(needed + 1)
    size = _WIN_GET_FINAL_PATH(original["raw"], buffer, needed + 1, 0)
    last_error = _GET_LAST_ERROR()
    if type(size) is not int or size <= 0 or size >= needed + 1:
        _update_holder_original(original, native_error=last_error)
        _fail("NATIVE_FAILED")
    final = buffer.value
    if type(final) is not str or len(final) != size or not final.startswith("\\\\?\\"):
        _fail("IDENTITY_CHANGED")
    dos = final[4:]
    expected = state["chain"][ordinal]
    # Win32 may omit a trailing slash for a volume root, but no component or
    # drive projection can change. Case follows the fixed DOS projection.
    if ordinal == 0 and dos == expected[:-1]:
        dos += "\\"
    if dos.casefold() != expected.casefold():
        _fail("IDENTITY_CHANGED")
    if ordinal:
        parent = _holder_state_original(state["directory_keys"][ordinal - 1])
        if (parent["ownership"] != "owned" or parent["close_attempted"]
                or parent["D"] is None or expected.rsplit("\\", 1)[0].casefold().rstrip("\\")
                    != state["chain"][ordinal - 1].casefold().rstrip("\\")):
            _fail("IDENTITY_CHANGED")
        operation._check_budget_original(state["registration"])
        if _identity_original(_STAT(state["chain"][ordinal - 1], follow_symlinks=False)) != parent["D"]:
            _fail("IDENTITY_CHANGED")
    operation._check_budget_original(state["registration"])
    return _identity_original(_STAT(expected, follow_symlinks=False))


def _acquire_data_component_original(scope_key, component_ordinal):
    state = _actual_scope_original(scope_key)
    if (type(component_ordinal) is not int or component_ordinal != state["next_ordinal"]
            or not 0 <= component_ordinal < len(state["chain"]) or state["pending_identities"]
            or state["phase"] != "acquiring"):
        _fail("PHASE_INVALID")
    # Both actual holders and bounded event storage exist BEFORE H's CreateFile.
    original = _register_holder_original(state, component_ordinal, "H")
    duplicate = _register_holder_original(state, component_ordinal, "H2")
    original["identity_duplicate"] = duplicate
    if component_ordinal:
        parent = _holder_state_original(state["directory_keys"][-1])
        if _handle_relationship_original(parent) != parent["D"]:
            _fail("IDENTITY_CHANGED")
    _create_original(original)
    state["original_directory_keys"].append(original["key"])
    state["directory_keys"].append(original["key"])
    _handle_relationship_original(original)
    _finalize_directory_identity_original(original["key"])
    _update_scope_original(state, next_ordinal=component_ordinal + 1)
    return original["key"]


def _finalize_directory_identity_original(directory_key):
    original = _actual_holder_original(directory_key)
    state = original["scope_state"]
    if original["purpose"] != "H" or original["D"] is not None or state["pending_identities"] != 1:
        _fail("PHASE_INVALID")
    duplicate = original["identity_duplicate"]
    _duplicate_original(original, duplicate)
    _transfer_original(duplicate)
    observed = _identity_original(_FSTAT(duplicate["fd"]))
    if _handle_relationship_original(original) != observed:
        _fail("IDENTITY_CHANGED")
    commitment = _identity_hash_original(observed)
    _finalize_event_original(original, observed, commitment)
    _finalize_event_original(duplicate, observed, commitment)
    _close_owned_original(duplicate["key"])
    first = operation._FIRST_PEEK(state["registration"])
    if first is not None:
        raise first
    if not duplicate["normal_closed"] or not duplicate["lease_released"] or state["pending_identities"]:
        _fail("UNSETTLED")


def _verify_directory_before_close_original(directory_key):
    original = _actual_holder_original(directory_key)
    state = original["scope_state"]
    if original["purpose"] != "H" or original["D"] is None or state["phase"] != "closing" or state["pending_identities"]:
        _fail("PHASE_INVALID")
    duplicate = _register_holder_original(state, original["ordinal"], "H3")
    _duplicate_original(original, duplicate)
    _transfer_original(duplicate)
    observed = _identity_original(_FSTAT(duplicate["fd"]))
    # Always preserve H3's true observed D before comparing it with old H D.
    commitment = _identity_hash_original(observed)
    _finalize_event_original(duplicate, observed, commitment)
    try:
        if observed != original["D"] or _handle_relationship_original(original) != observed:
            _fail("IDENTITY_CHANGED")
    except BaseException as exc:
        # First identity/relationship failure precedes the H3 finally closer.
        raise operation._FIRST_ORIGINAL(state["registration"], exc)
    finally:
        _close_state_original(duplicate)
    first = operation._FIRST_PEEK(state["registration"])
    if first is not None:
        raise first
    if not duplicate["normal_closed"] or not duplicate["lease_released"]:
        _fail("UNSETTLED")


def _close_owned_original(resource_key):
    item = _actual_holder_original(resource_key)
    _close_state_original(item)


def _close_data_chain_original(scope_key):
    state = _scope_state_original(scope_key)
    # Ordinary fixed interface verifies actual owner, but the driver finally
    # retains the original closure directly in case this callee is poisoned.
    if not operation._same_owner(state["owner"], operation._owner_original()):
        _fail("OWNER_MISMATCH")
    _cleanup_state_original(state)


def _run_target_data_chain_original(operation_token, scope_key):
    state = _actual_scope_original(scope_key)
    if state["operation_token"] is not operation_token or state["phase"] != "created":
        _fail("PHASE_INVALID")
    registration = state["registration"]
    # These originals survive a subsequently poisoned public helper/descriptor.
    cleanup, _unused_retire = _trusted_callbacks_original(scope_key)
    remember, first = operation._FIRST_ORIGINAL, operation._FIRST_PEEK
    try:
        _update_scope_original(state, begin=state["budget"].snapshot())
        registration.budget_last = state["budget_dict"]["_last_tick"]
        operation._native_checkpoint_original(operation_token, "before_acquire")
        _terminal_original(state, True)
        _update_scope_original(state, phase="acquiring")
        operation._phase_original(registration, "data_chain_acquiring")
        for ordinal in range(len(state["chain"])):
            _acquire_data_component_original(scope_key, ordinal)
            _terminal_original(state, False)
            operation._native_checkpoint_original(operation_token, "component_finalized")
            _terminal_original(state, True)
            _update_scope_original(state, phase="acquiring")
            operation._phase_original(registration, "data_chain_acquiring")
        _terminal_original(state, False)
        operation._native_checkpoint_original(operation_token, "before_final_validation")
        operation._phase_original(registration, "data_chain_finalizing")
        operation._verify_selected_guard_original(registration.record)
        _terminal_original(state, True)
        _update_scope_original(state, phase="closing")
        operation._phase_original(registration, "data_chain_closing")
        for key in reversed(state["directory_keys"]):
            _verify_directory_before_close_original(key)
            _close_owned_original(key)
            if first(registration) is not None:
                raise first(registration)
        _terminal_original(state, False)
        operation._phase_original(registration, "native_unit_closed")
        _update_scope_original(state, phase="native_unit_closed")
        operation._verify_selected_guard_original(registration.record)
        operation._check_budget_original(registration)
        _validate_scope_original(state)
    except BaseException as exc:
        raise remember(registration, exc)
    finally:
        # This kernel cannot be redirected by hostile holder/phase/closer or
        # budget methods, and never invokes a callback or retries native close.
        cleanup()



def _catalog_extra_original():
    """Fixed bootstrap inventory, not runtime discovery of a caller backend."""
    modules = [(sys.modules[__name__], tuple(name for name in globals()
        if name.startswith("_") and name.upper() == name) +
        ("operation", "ctypes", "hashlib", "os", "stat", "sys", "weakref", "FunctionType", "ModuleType",
         "_make_ownership_kernel_original")),
        (ctypes, ("Structure", "c_void_p", "c_wchar", "POINTER", "sizeof", "alignment", "WinDLL",
            "byref", "get_last_error", "create_unicode_buffer")),
        (hashlib, ("sha256",)),
        (stat, ("S_ISDIR",)),
        (sys, ("implementation", "version_info", "maxsize")),
        (os, ("stat_result", "stat", "fstat", "close", "O_RDONLY", "O_BINARY", "name"))]
    if _BACKEND_NAME == "nt":
        modules.extend(((wintypes, ("HANDLE", "BOOL", "DWORD", "FILETIME", "LPCWSTR", "LPWSTR", "LPVOID")),
            (msvcrt, ("open_osfhandle", "get_osfhandle"))))
    return tuple(modules)
