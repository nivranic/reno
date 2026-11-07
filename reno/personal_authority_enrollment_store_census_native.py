"""Registered initial Store census on the two actual held directory HANDLEs.

This private module acquires no HANDLE and has no wait, retry, buffer growth,
path enumeration, publisher, SQL, or recovery entry. A query's storage is
strongly rooted before dispatch; only the narrow synchronous completion
contract can remove that debt. Directory and Stage owning exits stay outside
this module. Metadata retirement never substitutes for query completion.
"""
from __future__ import annotations

import ctypes
import os
import weakref

from . import personal_authority_namespace3_io as native
from . import personal_authority_legacy_fence as fence

_SEAL = object()
_BUFFER_BYTES = 64 * 1024
_INFORMATION_CLASS = 37
_NO_MORE_FILES = 0x80000006
_PENDING = 0x103
_STATUS_SENTINEL = -1073741823
_INFORMATION_SENTINEL = ctypes.c_size_t(-1).value
_ROLES = ("parent", "root")
_SCOPE_CLEANUP = {}
_SCAN_ROWS = {}
_QUERY_ROWS = {}
_QUERY_DEBTS = {}
_RETIRED_SCANS = {}
_RETIRED_SCOPES = {}
_RETIREMENT_ROOTS = {}


class StoreCensusError(ValueError):
    def __init__(self, suffix):
        self.code = "ENROLLMENT_STORE_CENSUS_" + suffix
        super().__init__(self.code)


def _fail(suffix):
    raise StoreCensusError(suffix) from None


def _note(first):
    try:
        BaseException.add_note(first, "ENROLLMENT_STORE_CENSUS_ADDITIONAL_FAILURE")
    except BaseException:
        pass


class _StatusUnion(ctypes.Union):
    _fields_ = [("status", ctypes.c_int32), ("pointer", ctypes.c_void_p)]


class _IoStatus(ctypes.Structure):
    _anonymous_ = ("completion",)
    _fields_ = [("completion", _StatusUnion), ("information", ctypes.c_size_t)]


class _UnicodeString(ctypes.Structure):
    _fields_ = [("length", ctypes.c_uint16), ("maximum_length", ctypes.c_uint16),
                ("buffer", ctypes.c_void_p)]


class _FileIdBothDirectoryInformation(ctypes.Structure):
    _fields_ = [("next_entry_offset", ctypes.c_uint32), ("file_index", ctypes.c_uint32),
                ("creation_time", ctypes.c_int64), ("last_access_time", ctypes.c_int64),
                ("last_write_time", ctypes.c_int64), ("change_time", ctypes.c_int64),
                ("end_of_file", ctypes.c_int64), ("allocation_size", ctypes.c_int64),
                ("file_attributes", ctypes.c_uint32), ("file_name_length", ctypes.c_uint32),
                ("ea_size", ctypes.c_uint32), ("short_name_length", ctypes.c_int8),
                ("short_name", ctypes.c_uint16 * 12), ("file_id", ctypes.c_int64),
                ("file_name", ctypes.c_uint16 * 1)]


_QueryBuffer = ctypes.c_char * _BUFFER_BYTES
_DIR_OFFSETS = (("next_entry_offset", 0), ("file_index", 4), ("creation_time", 8),
                ("last_access_time", 16), ("last_write_time", 24), ("change_time", 32),
                ("end_of_file", 40), ("allocation_size", 48), ("file_attributes", 56),
                ("file_name_length", 60), ("ea_size", 64), ("short_name_length", 68),
                ("short_name", 70), ("file_id", 96), ("file_name", 104))
_NAME_OFFSET = 104
_ARGTYPES = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
             ctypes.POINTER(_IoStatus), ctypes.c_void_p, ctypes.c_uint32,
             ctypes.c_int32, ctypes.c_uint8, ctypes.POINTER(_UnicodeString),
             ctypes.c_uint8)
if os.name == "nt":
    # This object does not share the existing NtCreateFile DLL/function ABI.
    _ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    _query_directory = _ntdll.NtQueryDirectoryFile
    _query_directory.argtypes = list(_ARGTYPES)
    _query_directory.restype = ctypes.c_int32
else:
    _ntdll = _query_directory = None


class _CensusScan:
    __slots__ = ("_seal", "_claim", "_scope", "_role", "_binding", "_names",
                 "_charged_names", "_queries", "_phase", "_first", "_first_tb",
                 "__weakref__")

    def __init__(self, *args, **kwargs):
        _fail("INVALID_ARGUMENT")

    def __init_subclass__(cls, **kwargs):
        _fail("INVALID_ARGUMENT")


class _QueryStorage:
    __slots__ = ("_seal", "_claim", "_buffer", "_iosb")

    def __init__(self, *args, **kwargs):
        _fail("INVALID_ARGUMENT")

    def __init_subclass__(cls, **kwargs):
        _fail("INVALID_ARGUMENT")


class _RetiredIndexCallback:
    __slots__ = ("_kind", "_key", "_claim")

    def __init__(self, *args, **kwargs):
        _fail("INVALID_ARGUMENT")

    def __init_subclass__(cls, **kwargs):
        _fail("INVALID_ARGUMENT")

    def __call__(self, reference):
        # No callback is allowed to dispose native storage or an unresolved root.
        if type(self) is not _RetiredIndexCallback:
            return
        registry = _RETIRED_SCOPES if self._kind == "scope" else (
            _RETIRED_SCANS if self._kind == "scan" else None)
        if registry is None:
            return
        row = dict.get(registry, self._key)
        if (type(row) is not tuple or len(row) != (8 if self._kind == "scope" else 3)
                or row[0] is not reference or row[1] is not self._claim
                or type(row[-1]) is not tuple or len(row[-1]) != 4
                or row[-1][0] is not self or row[-1][1] != self._kind
                or row[-1][2] != self._key or row[-1][3] is not self._claim):
            return
        dict.__delitem__(registry, self._key)


def _directories():
    # The new directory owner imports this module without a cyclic eager import.
    from . import personal_authority_enrollment_store_directories as directories
    return directories


def _enrollment():
    # The actual Store continuation uses this exact budget, not namespace._Budget.
    from . import personal_authority_enrollment as enrollment
    return enrollment


def _supported():
    if os.name != "nt" or _query_directory is None:
        _fail("UNSUPPORTED")
    pointer_size = ctypes.sizeof(ctypes.c_void_p)
    if pointer_size not in (4, 8):
        _fail("ABI_UNSUPPORTED")
    expected_status = (8, 4, 4) if pointer_size == 4 else (16, 8, 8)
    if ((ctypes.sizeof(_IoStatus), _IoStatus.information.offset,
         ctypes.alignment(_IoStatus)) != expected_status
            or ctypes.sizeof(_StatusUnion) != pointer_size
            or _IoStatus.completion.offset != 0
            or _StatusUnion.status.offset != 0 or _StatusUnion.pointer.offset != 0
            or ctypes.sizeof(_UnicodeString) != (8 if pointer_size == 4 else 16)
            or _UnicodeString.length.offset != 0
            or _UnicodeString.maximum_length.offset != 2
            or _UnicodeString.buffer.offset != (4 if pointer_size == 4 else 8)
            or ctypes.sizeof(ctypes.c_int32) != 4
            or ctypes.sizeof(ctypes.c_uint32) != 4
            or ctypes.sizeof(ctypes.c_uint8) != 1
            or ctypes.sizeof(ctypes.c_uint16) != 2
            or ctypes.sizeof(ctypes.c_int64) != 8
            or ctypes.sizeof(_FileIdBothDirectoryInformation) != 112
            or ctypes.alignment(_FileIdBothDirectoryInformation) != 8
            or any(getattr(_FileIdBothDirectoryInformation, name).offset != expected
                   for name, expected in _DIR_OFFSETS)
            or ctypes.sizeof(_QueryBuffer) != _BUFFER_BYTES
            or type(_query_directory.argtypes) is not list
            or len(_query_directory.argtypes) != 11
            or any(actual is not expected for actual, expected in
                   zip(_query_directory.argtypes, _ARGTYPES))
            or _query_directory.restype is not ctypes.c_int32
            or getattr(_query_directory, "errcheck", None) is not None):
        _fail("ABI_UNSUPPORTED")


def _cleanup_graph(scope):
    graph = _directories()._census_cleanup_binding(scope)
    if type(graph) is not tuple or len(graph) != 7 or graph[0] is not scope:
        _fail("BINDING_INVALID")
    return graph


def _cleanup(scope):
    graph = _cleanup_graph(scope)
    row = dict.get(_SCOPE_CLEANUP, id(scope))
    if row is None:
        return graph, None
    if (type(row) is not tuple or len(row) != 13 or row[0] is not scope
            or row[2] is not graph or type(row[3]) is not tuple or type(row[4]) is not tuple):
        _fail("REGISTRY_CHANGED")
    return graph, row


def _replace_cleanup(scope, before, after):
    if dict.get(_SCOPE_CLEANUP, id(scope)) is not before:
        _fail("REGISTRY_CHANGED")
    _SCOPE_CLEANUP[id(scope)] = after


def _binding(scope, role):
    if type(role) is not str or role not in _ROLES:
        _fail("INVALID_ARGUMENT")
    binding = _directories()._census_binding(scope, role)
    graph = _cleanup_graph(scope)
    if (type(binding) is not tuple or len(binding) != 5 or binding[4] is not graph
            or type(binding[0]) is not native._Directory
            or type(binding[1]) is not _enrollment()._Budget
            or binding[0].budget is not binding[1]
            or type(binding[2]) is not tuple or not binding[2]
            or any(type(name) is not str or not name for name in binding[2])
            or len(frozenset(binding[2])) != len(binding[2])
            or type(binding[3]) is not bool or binding[3] is not (role == "parent")):
        _fail("BINDING_INVALID")
    return binding


def _scan_row(scan):
    if type(scan) is not _CensusScan:
        _fail("INVALID_ARGUMENT")
    row = dict.get(_SCAN_ROWS, id(scan))
    if row is None:
        retired = dict.get(_RETIRED_SCANS, id(scan))
        if type(retired) is tuple and retired[0]() is scan:
            _fail("CLOSED")
        _fail("INVALID_ARGUMENT")
    if type(row) is not tuple or len(row) != 15 or row[0] is not scan:
        _fail("REGISTRY_CHANGED")
    return row


def _replace_scan(scan, before, after):
    if dict.get(_SCAN_ROWS, id(scan)) is not before:
        _fail("REGISTRY_CHANGED")
    _SCAN_ROWS[id(scan)] = after


def _remember(scope, scan, primary):
    """Preserve the original directory/continuation first, including cleanup."""
    fallback_tb = BaseException.__dict__["__traceback__"].__get__(primary)
    cleanup = dict.get(_SCOPE_CLEANUP, id(scope))
    first, traceback = (cleanup[5], cleanup[6]) if (
        type(cleanup) is tuple and len(cleanup) == 13 and cleanup[0] is scope
        and cleanup[5] is not None) else (primary, fallback_tb)
    try:
        remembered = _directories()._remember_census_subject(scope, primary)
        if (type(remembered) is not tuple or len(remembered) != 2
                or not isinstance(remembered[0], BaseException)):
            _fail("BINDING_INVALID")
        first, traceback = remembered
    except BaseException as secondary:
        if secondary is not first:
            _note(first)
    try:
        graph, cleanup = _cleanup(scope)
        if cleanup is not None:
            _replace_cleanup(scope, cleanup,
                cleanup[:5] + (first, traceback, "failed") + cleanup[8:])
        if scan is not None:
            row = _scan_row(scan)
            _replace_scan(scan, row, row[:8] + ("failed", first, traceback) + row[11:])
            scan._phase, scan._first, scan._first_tb = "failed", first, traceback
    except BaseException as secondary:
        if secondary is not first:
            _note(first)
    return first, traceback


def _checkpoint(scan):
    row = _scan_row(scan)
    if row[9] is not None:
        raise BaseException.with_traceback(row[9], row[10])
    actual = _binding(row[2], row[3])
    if any(current is not original for current, original in zip(actual, row[4])):
        _fail("BINDING_INVALID")
    if (scan._seal is not _SEAL or scan._claim is not row[1]
            or scan._scope is not row[2] or scan._role is not row[3]
            or scan._binding is not row[4] or scan._names is not row[6]
            or scan._charged_names is not row[7] or scan._queries is not row[5]
            or scan._phase != row[8] or scan._first is not row[9]
            or scan._first_tb is not row[10]):
        _fail("BINDING_INVALID")
    row[4][1].clock_only()
    return row


def _new_scan(scope, role, binding):
    graph, cleanup = _cleanup(scope)
    if cleanup is None:
        marker = dict.get(_RETIRED_SCOPES, id(scope))
        if type(marker) is tuple and marker[0]() is scope:
            _fail("CLOSED")
        cleanup = (scope, object(), graph, (), (), None, None, "active",
                   False, False, False, False, None)
        _SCOPE_CLEANUP[id(scope)] = cleanup
    if cleanup[8] or any(_scan_row(prior)[3] == role for prior in cleanup[3]):
        _fail("CLOSED")
    scan = object.__new__(_CensusScan)
    claim = object()
    row = (scan, claim, scope, role, binding, (), (), (), "claimed", None, None,
           False, False, False, False)
    # Strongly capture the actual scan before exposing mutable presentation.
    _replace_cleanup(scope, cleanup, cleanup[:3] + (cleanup[3] + (scan,),) + cleanup[4:])
    _SCAN_ROWS[id(scan)] = row
    scan._seal, scan._claim, scan._scope, scan._role = _SEAL, claim, scope, role
    scan._binding, scan._queries, scan._names, scan._charged_names = binding, (), (), ()
    scan._phase, scan._first, scan._first_tb = "claimed", None, None
    return scan


def _query_row(query):
    row = dict.get(_QUERY_ROWS, id(query))
    if (type(query) is not _QueryStorage or type(row) is not tuple or len(row) != 18
            or row[0] is not query):
        _fail("REGISTRY_CHANGED")
    return row


def _replace_query(query, before, after):
    if dict.get(_QUERY_ROWS, id(query)) is not before:
        _fail("REGISTRY_CHANGED")
    _QUERY_ROWS[id(query)] = after


def _new_query(scan):
    row = _checkpoint(scan)
    directory, budget, expected, outer, graph = row[4]
    handle = directory.handle
    if type(handle) is not int or handle <= 0 or handle == ctypes.c_void_p(-1).value:
        _fail("BINDING_INVALID")
    buffer, status = _QueryBuffer(), _IoStatus()
    if (ctypes.addressof(buffer) % ctypes.alignment(_FileIdBothDirectoryInformation)
            or ctypes.addressof(status) % ctypes.alignment(_IoStatus)):
        _fail("ABI_UNSUPPORTED")
    status.status, status.information = _STATUS_SENTINEL, _INFORMATION_SENTINEL
    query = object.__new__(_QueryStorage)
    claim = object()
    query._seal, query._claim, query._buffer, query._iosb = _SEAL, claim, buffer, status
    query_row = (query, claim, scan, row[2], graph, buffer, status, handle,
                 len(row[5]), False, False, None, None, None, None,
                 False, False, "prepared")
    _QUERY_ROWS[id(query)] = query_row
    graph, cleanup = _cleanup(row[2])
    _replace_cleanup(row[2], cleanup,
        cleanup[:4] + (cleanup[4] + (query,),) + cleanup[5:])
    next_queries = row[5] + (query,)
    _replace_scan(scan, row, row[:5] + (next_queries,) + row[6:])
    scan._queries = next_queries
    # This root is installed even before dispatch is attempted. No later clock,
    # ctypes argument conversion, call exception, or output read can lose storage.
    _QUERY_DEBTS[id(query)] = (query, claim, scan, row[2], graph, buffer, status, handle,
                               query_row[8])
    return query


def _signed32(value):
    if type(value) is not int or not -(1 << 31) <= value < (1 << 31):
        _fail("COMPLETION_UNCONFIRMED")
    return value & 0xFFFFFFFF


def _dispatch(query):
    row = _query_row(query)
    if row[9] or row[17] != "prepared":
        _fail("CLOSED")
    scan = row[2]
    scan_row = _checkpoint(scan)
    if (row[4] is not scan_row[4][4] or row[3] is not scan_row[2]
            or query._seal is not _SEAL or query._claim is not row[1]
            or query._buffer is not row[5] or query._iosb is not row[6]
            or type(row[5]) is not _QueryBuffer or type(row[6]) is not _IoStatus
            or scan_row[4][0].handle != row[7]):
        _fail("BINDING_INVALID")
    # Make argument storage before consuming the one native dispatch attempt.
    status_pointer = ctypes.byref(row[6])
    scan_row[4][1].clock_only()
    _replace_query(query, row, row[:9] + (True,) + row[10:17] + ("dispatch_attempted",))
    result = _query_directory(row[7], None, None, None, status_pointer, row[5],
        _BUFFER_BYTES, _INFORMATION_CLASS, 0, None, 1 if row[8] == 0 else 0)
    row = _query_row(query)
    _replace_query(query, row, row[:10] + (True, result) + row[12:])
    result_status = _signed32(result)
    actual_status = _signed32(row[6].status)
    information = row[6].information
    if type(information) is not int or not 0 <= information <= _BUFFER_BYTES:
        _fail("COMPLETION_UNCONFIRMED")
    row = _query_row(query)
    _replace_query(query, row,
        row[:12] + (result_status, actual_status, information) + row[15:])
    completed = (result_status == actual_status == 0 and information > 0)
    terminal = (result_status == actual_status == _NO_MORE_FILES and information == 0)
    if not completed and not terminal:
        # Includes pending, success-with-zero, untouched sentinel, mismatch,
        # overflow, too-small and other unsupported completion representations.
        _fail("QUERY_PENDING" if _PENDING in (result_status, actual_status)
              else "COMPLETION_UNCONFIRMED")
    row = _query_row(query)
    _replace_query(query, row,
        row[:15] + (True, terminal, "terminal" if terminal else "completed"))
    debt = dict.get(_QUERY_DEBTS, id(query))
    if (type(debt) is not tuple or len(debt) != 9 or debt[0] is not query
            or debt[1] is not row[1] or debt[4] is not row[4]
            or debt[5] is not row[5] or debt[6] is not row[6]):
        _fail("REGISTRY_CHANGED")
    dict.__delitem__(_QUERY_DEBTS, id(query))
    # Completion is already recorded before any post-call clock or parser.
    scan_row[4][1].clock_only()
    return terminal, row[5][:information]


def _u32(raw, offset):
    if type(raw) is not bytes or type(offset) is not int or not 0 <= offset <= len(raw) - 4:
        _fail("RECORD_INVALID")
    return int.from_bytes(raw[offset:offset + 4], "little", signed=False)


def _charged_name(scan, name):
    row = _checkpoint(scan)
    row[4][1].name(name)
    charged = row[7] + (name,)
    _replace_scan(scan, row, row[:7] + (charged,) + row[8:])
    scan._charged_names = charged


def _parse_batch(scan, raw, seen, folded, accepted):
    if type(raw) is not bytes or not 0 < len(raw) <= _BUFFER_BYTES:
        _fail("RECORD_INVALID")
    cursor = 0
    while True:
        row = _checkpoint(scan)
        row[4][1].before_next()
        if not 0 <= cursor <= len(raw) - _NAME_OFFSET:
            _fail("RECORD_INVALID")
        following = _u32(raw, cursor)
        name_bytes = _u32(raw, cursor + 60)
        short_bytes = raw[cursor + 68]
        if short_bytes > 24 or short_bytes % 2 or name_bytes == 0 or name_bytes % 2:
            _fail("RECORD_INVALID")
        record_bytes = _NAME_OFFSET + name_bytes
        aligned_bytes = (record_bytes + 7) & ~7
        if following:
            if (following % 8 or following != aligned_bytes
                    or following > len(raw) - cursor or following < record_bytes):
                _fail("RECORD_INVALID")
            end = cursor + following
        else:
            end = len(raw)
            if end - cursor not in (record_bytes, aligned_bytes):
                _fail("RECORD_INVALID")
        if record_bytes > end - cursor:
            _fail("RECORD_INVALID")
        name = raw[cursor + _NAME_OFFSET:cursor + record_bytes].decode("utf-16-le", errors="strict")
        _charged_name(scan, name)
        if "\0" in name or "/" in name or "\\" in name or name in seen:
            _fail("UNKNOWN_NAMES")
        seen.add(name)
        if name not in (".", ".."):
            normalized = name.rstrip(" .").casefold()
            if normalized in folded:
                _fail("UNKNOWN_NAMES")
            folded.add(normalized)
            if not row[4][3] or normalized.startswith(fence.PREFIX):
                accepted.append(name)
        if following == 0:
            # Only this returned batch ended. The caller must obtain native EOF.
            return None
        cursor = end


def _scan_subject(scope, role):
    """One original scope/role attempt; actual same-HANDLE census receipt."""
    scan = None
    try:
        binding = _binding(scope, role)
        scan = _new_scan(scope, role, binding)
        _supported()
        row = _checkpoint(scan)
        row[4][0].verify()
        row = _scan_row(scan)
        _replace_scan(scan, row, row[:8] + ("running",) + row[9:11] + (True,) + row[12:])
        scan._phase = "running"
        seen, folded, accepted = set(), set(), []
        while True:
            row = _checkpoint(scan)
            # Preserve the exhaustion-before-next budget check as well as the
            # per-record checks; a query itself never increments name counts.
            row[4][1].before_next()
            terminal, raw = _dispatch(_new_query(scan))
            if terminal:
                row = _scan_row(scan)
                _replace_scan(scan, row, row[:13] + (True,) + row[14:])
                break
            _parse_batch(scan, raw, seen, folded, accepted)
        row = _checkpoint(scan)
        row[4][0].verify()
        row = _scan_row(scan)
        _replace_scan(scan, row, row[:12] + (True,) + row[13:])
        if frozenset(accepted) != frozenset(row[4][2]) or len(accepted) != len(row[4][2]):
            _fail("UNKNOWN_NAMES")
        _checkpoint(scan)
        row = _scan_row(scan)
        names = tuple(accepted)
        _replace_scan(scan, row, row[:6] + (names, row[7], "ready") + row[9:])
        scan._names, scan._phase = names, "ready"
        _scan_names(scan, scope=scope, role=role)
        return scan
    except BaseException as primary:
        first, traceback = _remember(scope, scan, primary)
        raise BaseException.with_traceback(first, traceback)


def _scan_names(scan, *, scope, role):
    """Actual receipt getter, unavailable after directory business cutoff."""
    row = _scan_row(scan)
    if row[9] is not None:
        raise BaseException.with_traceback(row[9], row[10])
    if scope is not row[2] or type(role) is not str or role != row[3]:
        _fail("INVALID_ARGUMENT")
    row = _checkpoint(scan)
    if row[8] != "ready" or not all(row[index] for index in (11, 12, 13)) or row[14]:
        _fail("CLOSED")
    return row[6]


def _require_settled(scope):
    """Trusted cleanup proof; no live/ready/clock or business-pins requirement."""
    graph, cleanup = _cleanup(scope)
    if cleanup is None:
        return None
    for scan in cleanup[3]:
        row = _scan_row(scan)
        if row[2] is not scope or row[4][4] is not graph:
            _fail("REGISTRY_CHANGED")
    for query in cleanup[4]:
        row = _query_row(query)
        if (row[3] is not scope or row[4] is not graph
                or not any(row[2] is scan for scan in cleanup[3])):
            _fail("REGISTRY_CHANGED")
        debt = dict.get(_QUERY_DEBTS, id(query))
        if row[9] and (not row[15] or debt is not None):
            _fail("CLEANUP_DEBT_REMAINS")
    return None


def _new_retired_reference(value, kind, claim):
    callback = object.__new__(_RetiredIndexCallback)
    callback._kind, callback._key, callback._claim = kind, id(value), claim
    pin = (callback, kind, id(value), claim)
    return weakref.ref(value, callback), pin


def _clear_scope_metadata(scope, cleanup):
    """One fixed metadata operation; it neither closes nor completes queries."""
    for scan in cleanup[3]:
        row = _scan_row(scan)
        reference, callback_pin = _new_retired_reference(scan, "scan", row[1])
        _RETIRED_SCANS[id(scan)] = (reference, row[1], callback_pin)
        scan._phase, scan._binding, scan._scope = "retired", None, None
        scan._queries, scan._names, scan._charged_names = (), (), ()
        scan._first = scan._first_tb = None
        dict.__delitem__(_SCAN_ROWS, id(scan))
    for query in cleanup[4]:
        row = _query_row(query)
        # Undispatched storage is safe to discard; dispatched storage must have
        # the independently recorded completion and no predispatch debt root.
        if row[9] and (not row[15] or dict.get(_QUERY_DEBTS, id(query)) is not None):
            _fail("CLEANUP_DEBT_REMAINS")
        if not row[9] and dict.get(_QUERY_DEBTS, id(query)) is not None:
            dict.__delitem__(_QUERY_DEBTS, id(query))
        dict.__delitem__(_QUERY_ROWS, id(query))
    dict.__delitem__(_SCOPE_CLEANUP, id(scope))


def _metadata_effect(scope, cleanup):
    return (dict.get(_SCOPE_CLEANUP, id(scope)) is None
            and all(dict.get(_SCAN_ROWS, id(scan)) is None and scan._phase == "retired"
                    for scan in cleanup[3])
            and all(dict.get(_QUERY_ROWS, id(query)) is None
                    and dict.get(_QUERY_DEBTS, id(query)) is None for query in cleanup[4]))


def _retirement_observation(scope):
    """Independent normal-return/effect facts; never a completion substitute."""
    graph = _cleanup_graph(scope)
    marker = dict.get(_RETIRED_SCOPES, id(scope))
    if marker is None:
        return None
    if type(marker) is not tuple or len(marker) != 8 or marker[0]() is not scope:
        _fail("REGISTRY_CHANGED")
    return marker[2:7]


def _retire_for_scope(scope, *, primary=None):
    """Metadata only, once, after actual query-storage debt proof."""
    graph, cleanup = _cleanup(scope)
    if cleanup is None:
        marker = dict.get(_RETIRED_SCOPES, id(scope))
        if type(marker) is tuple and marker[0]() is scope:
            _fail("CLOSED")
        return None
    if cleanup[8]:
        _fail("CLOSED")
    # The sole attempt is consumed before proof/retirement; failures do not retry.
    _replace_cleanup(scope, cleanup,
        cleanup[:7] + ("retirement_attempted", True) + cleanup[9:])
    graph, cleanup = _cleanup(scope)
    if primary is not None:
        first, traceback = _remember(scope, None, primary)
        graph, cleanup = _cleanup(scope)
    else:
        first, traceback = cleanup[5], cleanup[6]
    reference, callback_pin = _new_retired_reference(scope, "scope", cleanup[1])
    marker = (reference, cleanup[1], "retirement_attempted", True, False, False, False,
              callback_pin)
    _RETIRED_SCOPES[id(scope)] = marker
    # Original complete rows, storage and graph are held before any metadata
    # deletion. A writing-then-throwing retire retains this original graph.
    root = (scope, graph, cleanup, tuple(_scan_row(scan) for scan in cleanup[3]),
            tuple(_query_row(query) for query in cleanup[4]), first, traceback)
    _RETIREMENT_ROOTS[id(scope)] = root
    normal_return = False
    try:
        _require_settled(scope)
        _clear_scope_metadata(scope, cleanup)
        normal_return = True
        effect = _metadata_effect(scope, cleanup)
        if not effect:
            _fail("RETIREMENT_UNCONFIRMED")
        if first is not None and primary is None:
            # Metadata returned, but this API will raise the original business
            # first. Do not report its normal return or release the strong root.
            raise BaseException.with_traceback(first, traceback)
        after = marker[:2] + ("retired", True, True, effect, True, marker[-1])
        _RETIRED_SCOPES[id(scope)] = after
        if dict.get(_RETIREMENT_ROOTS, id(scope)) is not root:
            _fail("REGISTRY_CHANGED")
        dict.__delitem__(_RETIREMENT_ROOTS, id(scope))
    except BaseException as failure:
        remembered, remembered_tb = _remember(scope, None, failure)
        if first is None:
            first, traceback = remembered, remembered_tb
        elif failure is not first:
            _note(first)
        effect = False
        try:
            effect = _metadata_effect(scope, cleanup)
        except BaseException as additional:
            if additional is not first:
                _note(first)
        try:
            _RETIRED_SCOPES[id(scope)] = marker[:2] + (
                "retained_failed", True, normal_return, effect, False, marker[-1])
        except BaseException as additional:
            if additional is not first:
                _note(first)
        raise BaseException.with_traceback(first, traceback)
    return None


def _catalog_extra_original():
    """Fixed selected native ABI, structures and stdlib attributes for the group."""
    enrollment = _enrollment()
    rows = (
        (ctypes, ("Union", "Structure", "Array", "c_int32", "c_uint32", "c_uint8",
            "c_int8", "c_uint16", "c_int64", "c_size_t", "c_void_p", "c_char",
            "POINTER", "byref", "sizeof", "alignment", "addressof")),
        (os, ("name",)), (weakref, ("ref", "ReferenceType")),
        (native, ("_Directory",)), (enrollment, ("_Budget",)), (fence, ("PREFIX",)),
        (native._Directory, ("verify", "handle")),
        (enrollment._Budget, ("clock_only", "before_next", "name")),
        (int, ("from_bytes",)), (bytes, ("decode",)),
        (str, ("rstrip", "casefold", "startswith")),
        (dict, ("get", "__delitem__")), (object, ("__new__",)),
        (BaseException, ("add_note", "with_traceback", "__traceback__")),
        (ctypes.Array, ("__getitem__", "__setitem__", "__len__")),
        (_StatusUnion, ("_fields_", "status", "pointer")),
        (_IoStatus, ("_fields_", "_anonymous_", "completion", "status", "pointer", "information")),
        (_UnicodeString, ("_fields_", "length", "maximum_length", "buffer")),
        (_FileIdBothDirectoryInformation, ("_fields_", *(name for name, offset in _DIR_OFFSETS))),
        (_QueryBuffer, ("_length_", "_type_", "raw", "value")),
        (_CensusScan, ("__slots__", "__init__", "__init_subclass__", *(
            name for name in _CensusScan.__slots__ if name != "__weakref__"))),
        (_QueryStorage, ("__slots__", "__init__", "__init_subclass__", *(_QueryStorage.__slots__))),
        (_RetiredIndexCallback, ("__slots__", "__init__", "__init_subclass__", "__call__",
            *(_RetiredIndexCallback.__slots__))),
    )
    if os.name == "nt":
        return rows + ((ctypes, ("WinDLL",)), (_ntdll, ("NtQueryDirectoryFile",)),
            (_query_directory, ("argtypes", "restype", "errcheck")))
    return rows


def _scan(scope, role):
    if type(scope) is not _directories()._StoreDirectoryScope:
        _fail('INVALID_ARGUMENT')
    return _scan_subject(scope, role)


def _scan_final_generation(generation, role):
    _directories()._final_generation_original(generation)
    return _scan_subject(generation, role)
