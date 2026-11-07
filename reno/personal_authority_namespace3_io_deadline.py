"""Fixed namespace3 native effects at one shared cooperative deadline.

New explicit consumers only; immutable facts are not writer authority or a
source-close witness. All owned resources retain one fixed sole closer.
"""
from __future__ import annotations
from contextlib import contextmanager, _GeneratorContextManager
from copy import deepcopy
import ctypes
import errno
import hashlib
import os
from pathlib import Path
from . import personal_authority_namespace3_deadline as deadline
from . import personal_authority_namespace3_contract as contract
from . import personal_authority_namespace3_io as original_io
from . import personal_authority_directory_deadline as directories
from . import personal_authority_source_deadline as nodes
from . import personal_authority_origin_capture_deadline as capture
from . import personal_preservation_files as facts
from . import storage_lifecycle as lifecycle, sync_media as safe
Namespace3IOError = original_io.Namespace3IOError
PublishedFile = original_io.PublishedFile
Error = Namespace3IOError
CHUNK_BYTES = 8192
_DIR_READ = 1179777
_DIR_CREATE = _DIR_READ | 6
_FILE_READ = 2148663296
_FILE_CREATE = 3222470656
_INVALID_HANDLE = ctypes.c_void_p(-1).value
_POINTER_BYTES = ctypes.sizeof(ctypes.c_void_p)
_POINTER_MAX = (1 << _POINTER_BYTES * 8) - 1
_PATH_TYPE = type(Path())
_HASH_TYPE = type(hashlib.sha256())

def _fail(suffix):
    raise Namespace3IOError(suffix) from None

def _native_error(number, operation):
    return OSError(errno.EIO, 'Namespace3 native ' + operation + ' failed', None, number)
if os.name == 'nt':
    from ctypes import wintypes as w

    class _UnicodeString(ctypes.Structure):
        _fields_ = [('length', w.USHORT), ('maximum_length', w.USHORT), ('buffer', w.LPWSTR)]

    class _ObjectAttributes(ctypes.Structure):
        _fields_ = [('length', w.ULONG), ('root', w.HANDLE), ('name', ctypes.POINTER(_UnicodeString)), ('attributes', w.ULONG), ('security_descriptor', w.LPVOID), ('security_qos', w.LPVOID)]

    class _StatusUnion(ctypes.Union):
        _fields_ = [('status', w.LONG), ('pointer', w.LPVOID)]

    class _IoStatus(ctypes.Structure):
        _anonymous_ = ('completion',)
        _fields_ = [('completion', _StatusUnion), ('information', ctypes.c_size_t)]

    class _FileTime(ctypes.Structure):
        _fields_ = [('low', w.DWORD), ('high', w.DWORD)]

    class _HandleInfo(ctypes.Structure):
        _fields_ = [('attributes', w.DWORD), ('created', _FileTime), ('accessed', _FileTime), ('written', _FileTime), ('volume', w.DWORD), ('size_high', w.DWORD), ('size_low', w.DWORD), ('links', w.DWORD), ('index_high', w.DWORD), ('index_low', w.DWORD)]

    class _AclSize(ctypes.Structure):
        _fields_ = [('count', w.DWORD), ('used', w.DWORD), ('free', w.DWORD)]

    class _AceHeader(ctypes.Structure):
        _fields_ = [('kind', w.BYTE), ('flags', w.BYTE), ('size', w.WORD)]

    class _AllowedAce(ctypes.Structure):
        _fields_ = [('header', _AceHeader), ('mask', w.DWORD), ('sid_start', w.DWORD)]
    _kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    _advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    _ntdll = ctypes.WinDLL('ntdll', use_last_error=True)

    def _abi(dll, name, arguments, result):
        function = getattr(dll, name)
        function.argtypes, function.restype = (arguments, result)
        return function
    _abi(_kernel, 'CreateFileW', [w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE], w.HANDLE)
    _abi(_kernel, 'CloseHandle', [w.HANDLE], w.BOOL)
    _abi(_kernel, 'LocalFree', [w.HLOCAL], w.HLOCAL)
    _abi(_kernel, 'GetCurrentProcess', [], w.HANDLE)
    _abi(_kernel, 'GetFileInformationByHandle', [w.HANDLE, ctypes.POINTER(_HandleInfo)], w.BOOL)
    _abi(_kernel, 'ReadFile', [w.HANDLE, w.LPVOID, w.DWORD, ctypes.POINTER(w.DWORD), w.LPVOID], w.BOOL)
    _abi(_kernel, 'WriteFile', [w.HANDLE, w.LPVOID, w.DWORD, ctypes.POINTER(w.DWORD), w.LPVOID], w.BOOL)
    _abi(_kernel, 'FlushFileBuffers', [w.HANDLE], w.BOOL)
    _abi(_kernel, 'SetFilePointerEx', [w.HANDLE, ctypes.c_longlong, ctypes.POINTER(ctypes.c_longlong), w.DWORD], w.BOOL)
    _abi(_advapi, 'OpenProcessToken', [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)], w.BOOL)
    _abi(_advapi, 'GetTokenInformation', [w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD, ctypes.POINTER(w.DWORD)], w.BOOL)
    _abi(_advapi, 'ConvertSidToStringSidW', [w.LPVOID, ctypes.POINTER(w.LPWSTR)], w.BOOL)
    _abi(_advapi, 'ConvertStringSidToSidW', [w.LPCWSTR, ctypes.POINTER(w.LPVOID)], w.BOOL)
    _abi(_advapi, 'ConvertStringSecurityDescriptorToSecurityDescriptorW', [w.LPCWSTR, w.DWORD, ctypes.POINTER(w.LPVOID), ctypes.POINTER(w.DWORD)], w.BOOL)
    _abi(_advapi, 'GetSecurityInfo', [w.HANDLE, ctypes.c_int, w.DWORD, ctypes.POINTER(w.LPVOID), ctypes.POINTER(w.LPVOID), ctypes.POINTER(w.LPVOID), ctypes.POINTER(w.LPVOID), ctypes.POINTER(w.LPVOID)], w.DWORD)
    _abi(_advapi, 'GetSecurityDescriptorControl', [w.LPVOID, ctypes.POINTER(w.WORD), ctypes.POINTER(w.DWORD)], w.BOOL)
    _abi(_advapi, 'IsValidSecurityDescriptor', [w.LPVOID], w.BOOL)
    _abi(_advapi, 'IsValidSid', [w.LPVOID], w.BOOL)
    _abi(_advapi, 'GetLengthSid', [w.LPVOID], w.DWORD)
    _abi(_advapi, 'EqualSid', [w.LPVOID, w.LPVOID], w.BOOL)
    _abi(_advapi, 'GetAclInformation', [w.LPVOID, w.LPVOID, w.DWORD, ctypes.c_int], w.BOOL)
    _abi(_advapi, 'GetAce', [w.LPVOID, w.DWORD, ctypes.POINTER(w.LPVOID)], w.BOOL)
    _abi(_ntdll, 'NtCreateFile', [ctypes.POINTER(w.HANDLE), w.DWORD, ctypes.POINTER(_ObjectAttributes), ctypes.POINTER(_IoStatus), w.LPVOID, w.ULONG, w.ULONG, w.ULONG, w.ULONG, w.LPVOID, w.ULONG], w.LONG)
    _abi(_ntdll, 'NtSetInformationFile', [w.HANDLE, ctypes.POINTER(_IoStatus), w.LPVOID, w.ULONG, ctypes.c_int], w.LONG)
    _UNICODE_POINTER_TYPE = ctypes.POINTER(_UnicodeString)
    _LPVOID_POINTER_TYPE = ctypes.POINTER(w.LPVOID)
    _ALLOWED_ACE_POINTER_TYPE = ctypes.POINTER(_AllowedAce)
    _OBJECT_ATTRIBUTES_SIZE = ctypes.sizeof(_ObjectAttributes)
from . import personal_authority_origin_reservation_contract as origin_contract
_ARRAY_BASE = ctypes.Array
_ARRAY_METATYPE = type(ctypes.Array)

def _char_array_domain(value):
    current = type(value)
    return type(current) is _ARRAY_METATYPE and current.__base__ is _ARRAY_BASE and (current._type_ is ctypes.c_char) and (type(current._length_) is int) and (current._length_ > 0)

def _wchar_array_domain(value):
    current = type(value)
    return type(current) is _ARRAY_METATYPE and current.__base__ is _ARRAY_BASE and (current._type_ is ctypes.c_wchar) and (type(current._length_) is int) and (current._length_ > 0)

def _native_identity_domain(value):
    try:
        contract._native_identity(value)
    except contract.Namespace3ContractError:
        _fail('INVALID_RESULT')

def _directory_identity_domain(value):
    try:
        contract._path_identity(value)
    except contract.Namespace3ContractError:
        _fail('INVALID_RESULT')

def _file_identity_domain(value):
    try:
        origin_contract._identity(value, file=True)
    except origin_contract.ContractError:
        _fail('INVALID_RESULT')

def _profile_identity_domain(value):
    if type(value) is not tuple or len(value) != 4:
        _fail('INVALID_RESULT')
    for component in value:
        try:
            origin_contract._path(component)
        except origin_contract.ContractError:
            _fail('INVALID_RESULT')

def _sha256_domain(value):
    if type(value) is not str or len(value) != 64 or any((c not in '0123456789abcdef' for c in value)):
        _fail('INVALID_RESULT')

def _held_size_domain(value, receiver):
    maximum = receiver.maximum
    if type(maximum) is not int or not 0 < maximum <= 64 * 1024:
        _fail('INVALID_RESULT')
    if type(value) is not int or not 0 <= value <= maximum:
        _fail('INVALID_RESULT')

def _owner_domain(value, state):
    if type(value) is not _IOOwner or value.state is not state or type(value.slot) is not _Slot or (value.slot.state is not state) or (value.slot.kind not in ('handle', 'pointer')):
        _fail('INVALID_RESULT')

def _directory_domain(value, state):
    if type(value) is not _IODirectory or value.state is not state or value.budget is not state.restricted:
        _fail('INVALID_RESULT')

def _directory_handle_domain(value, receiver, state):
    _handle_value(value)
    owner = receiver.owner
    _owner_domain(owner, state)
    if owner.slot.kind != 'handle' or owner.slot.phase != _OWNED or value != owner.slot.value:
        _fail('INVALID_RESULT')

def _stamp_domain(value):
    if type(value) is not tuple or len(value) != 11:
        _fail('INVALID_RESULT')
    for item in value:
        _uint32(item)

class _NamespaceState:
    __slots__ = ('restricted', 'token', 'first', 'first_tb', 'first_tb_available', 'profile_paths', 'guard', 'prepared_head', 'probe_error', 'active_scandir_head')

    def __init__(self, exact_restricted_budget, exact_effective_token, /):
        if type(self) is not _NamespaceState or type(exact_restricted_budget) is not deadline._RestrictedBudget or type(exact_effective_token) is not deadline.Namespace3Deadline or (exact_restricted_budget.effective_deadline is not exact_effective_token):
            _fail('INVALID_RESULT')
        self.restricted, self.token = (exact_restricted_budget, exact_effective_token)
        self.first, self.first_tb, self.first_tb_available = (None, None, False)
        self.profile_paths, self.guard, self.prepared_head = (None, None, None)
        self.probe_error = None
        self.active_scandir_head = None
        _clock(self)

def _state(value):
    if type(value) is not _NamespaceState or type(value.restricted) is not deadline._RestrictedBudget or type(value.token) is not deadline.Namespace3Deadline or (value.token is not value.restricted.effective_deadline):
        _fail('INVALID_RESULT')

def _cleanup_note(state, original_primary, fixed_note_role, /):
    if fixed_note_role not in ('cleanup', 'secondary', 'traceback'):
        fixed_note_role = 'cleanup'
    try:
        BaseException.add_note(original_primary, 'NAMESPACE3_IO_CLEANUP_FAILED')
    except BaseException:
        pass

def _primary_kind(state, original_primary, /):
    return type(original_primary) if original_primary is not None else None

def _primary_traceback(state, original_primary, /):
    return BaseException.__dict__['__traceback__'].__get__(original_primary)

def _remember_primary(state, original_primary, /):
    if state.first is None:
        state.first = original_primary
        try:
            state.first_tb = _primary_traceback(state, original_primary)
            state.first_tb_available = True
        except BaseException:
            state.first_tb = None
            _cleanup_note(state, original_primary, 'traceback')
    elif state.first is not original_primary:
        _cleanup_note(state, state.first, 'secondary')
    return state.first

def _record_cleanup_secondary(state, original_secondary, /):
    _remember_primary(state, original_secondary)

def _raise_first(state):
    if state.first is not None:
        if state.first_tb_available:
            raise BaseException.with_traceback(state.first, state.first_tb) from None
        raise state.first from None

def _clock(state):
    _state(state)
    _raise_first(state)
    try:
        state.restricted.clock_only()
    except BaseException as primary:
        _remember_primary(state, primary)
        raise

def _cleanup_clock(state):
    try:
        _state(state)
        state.restricted.clock_only()
    except BaseException as primary:
        _record_cleanup_secondary(state, primary)

def _none(value):
    if value is not None:
        _fail('INVALID_RESULT')

def _uint32(value):
    if type(value) is not int or not 0 <= value <= 4294967295:
        _fail('INVALID_RESULT')
    return value

def _signed32(value):
    if type(value) is not int or not -(1 << 31) <= value < 1 << 31:
        _fail('INVALID_RESULT')
    return value

def _uintptr(value, *, nonzero=False):
    if type(value) is not int or not (1 if nonzero else 0) <= value <= _POINTER_MAX:
        _fail('INVALID_RESULT')
    return value

def _handle_value(value):
    _uintptr(value, nonzero=True)
    if value == _INVALID_HANDLE:
        _fail('HANDLE_UNCONFIRMED')
    return value

def _failure_error_raw(state, /):
    try:
        return _uint32(ctypes.get_last_error())
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _probe_error_raw(state, /):
    try:
        return ctypes.get_last_error()
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)
_FAILURE_CODES = frozenset({'INVALID_RESULT', 'INVALID_ARGUMENT', 'PRIVATE_OWNER_UNPROVEN', 'ABI_UNSUPPORTED', 'UNSUPPORTED', 'OWNER_RETIRED', 'OBJECT_UNSAFE', 'ROOT_IDENTITY_MISMATCH', 'PROFILE_CHANGED', 'CREATE_UNAVAILABLE', 'OPEN_UNAVAILABLE', 'HANDLE_UNCONFIRMED', 'COMPLETION_UNCONFIRMED', 'READ_INVALID', 'WRITE_INVALID', 'LIMIT', 'READ_INCOMPLETE', 'READBACK_CHANGED', 'STAGE_CREATE_FAILED', 'PUBLISH_UNAVAILABLE'})

def _fixed_failure(state, fixed_failure_role, /):
    if type(fixed_failure_role) is not str or fixed_failure_role not in _FAILURE_CODES:
        _fail('INVALID_RESULT')
    _fail(fixed_failure_role)
_SEAL = object()
_EMPTY, _OWNED, _ATTEMPTED, _CLOSED, _UNKNOWN = ('empty', 'owned', 'attempted', 'closed', 'unknown')

class _Receipt:
    __slots__ = ('state', 'seal', 'kind', 'context', 'payload', 'phase', 'entered', 'attempted', 'retired', 'previous', 'prepared_previous', 'stack', 'expected', 'factory_owned', 'scan_previous')

    def __init__(self, state):
        _state(state)
        self.state, self.seal, self.kind = (state, _SEAL, None)
        self.context, self.payload, self.phase = (None, None, 'new')
        self.entered, self.attempted, self.retired = (False, False, False)
        self.previous, self.prepared_previous, self.stack = (None, None, None)
        self.expected, self.factory_owned = (None, False)
        self.scan_previous = None

def _prepare_receipt(state, receipt):
    receipt.phase = 'prepared'
    receipt.prepared_previous = state.prepared_head
    state.prepared_head = receipt

def _claim_receipt(state, receiver, fixed_kind):
    _state(state)
    current, previous = (state.prepared_head, None)
    while current is not None:
        if type(current) is not _Receipt or current.seal is not _SEAL or current.state is not state:
            _fail('INVALID_RESULT')
        if current.context is receiver:
            if current.kind != fixed_kind or current.phase != 'prepared' or current.retired:
                _fail('INVALID_RESULT')
            if previous is None:
                state.prepared_head = current.prepared_previous
            else:
                previous.prepared_previous = current.prepared_previous
            current.prepared_previous = None
            current.phase = 'claimed'
            return current
        previous, current = (current, current.prepared_previous)
    _fail('INVALID_RESULT')

class _Slot:
    __slots__ = ('state', 'kind', 'value', 'phase', 'failure', 'owner', 'receipt')

    def __init__(self, state):
        self.state, self.kind, self.value = (state, None, None)
        self.phase, self.failure = (_EMPTY, None)
        self.receipt = _Receipt(state)
        self.owner = _IOOwner(state, self)
        self.receipt.context = self.owner

def _handle_slot(state):
    slot = _Slot(state)
    slot.kind, slot.receipt.kind = ('handle', 'IO_HANDLE_OWNER')
    return slot

def _pointer_slot(state):
    slot = _Slot(state)
    slot.kind, slot.receipt.kind = ('pointer', 'IO_POINTER_OWNER')
    return slot

class _IOOwner:
    __slots__ = ('state', 'slot')

    def __init__(self, state, slot):
        self.state, self.slot = (state, slot)

    @property
    def value(self):
        return self.slot.value

    @property
    def retired(self):
        return self.slot.phase != _OWNED

    @property
    def failure(self):
        return self.slot.failure

    def __enter__(self):
        if type(self) is not _IOOwner or self.slot.phase != _OWNED:
            _fail('OWNER_RETIRED')
        return self

    def __exit__(self, kind, primary, tb):
        if primary is not None:
            _remember_primary(self.state, primary)
        _close_known_slot(self.state, self.slot)
        if primary is None or self.state.first is not primary:
            _raise_first(self.state)
        return None

def _mark_attempted(state, exact_known_slot, /):
    if type(exact_known_slot) is not _Slot or exact_known_slot.state is not state:
        _fail('INVALID_RESULT')
    exact_known_slot.phase = _ATTEMPTED

def _close_known_slot(state, exact_known_slot, /):
    if type(exact_known_slot) is not _Slot or exact_known_slot.state is not state:
        _record_cleanup_secondary(state, Namespace3IOError('INVALID_RESULT'))
        return
    if exact_known_slot.phase != _OWNED:
        return
    _mark_attempted(state, exact_known_slot)
    _cleanup_clock(state)
    try:
        if exact_known_slot.kind == 'handle':
            _native_closehandle(state, exact_known_slot.value)
        elif exact_known_slot.kind == 'pointer':
            _native_localfree(state, exact_known_slot.value)
        else:
            _fail('INVALID_RESULT')
    except BaseException as failure:
        exact_known_slot.phase, exact_known_slot.failure = (_UNKNOWN, failure)
        _record_cleanup_secondary(state, failure)
    else:
        exact_known_slot.phase = _CLOSED
    _cleanup_clock(state)

def _register_known_return(state, exact_known_slot, /):
    if type(exact_known_slot) is not _Slot or exact_known_slot.state is not state or exact_known_slot.phase != _OWNED:
        _fail('INVALID_RESULT')
    try:
        if exact_known_slot.kind == 'handle':
            owner = _issue_handle_owner_receipt(state, exact_known_slot)
        elif exact_known_slot.kind == 'pointer':
            owner = _issue_pointer_owner_receipt(state, exact_known_slot)
        else:
            _fail('INVALID_RESULT')
        _clock(state)
    except BaseException as primary:
        _remember_primary(state, primary)
        _close_known_slot(state, exact_known_slot)
        if state.first is primary:
            raise
        _raise_first(state)
    return owner

def _issue_handle_owner_receipt(state, exact_handle_slot, /):
    if type(exact_handle_slot) is not _Slot or exact_handle_slot.state is not state or exact_handle_slot.kind != 'handle' or (exact_handle_slot.phase != _OWNED) or (exact_handle_slot.receipt.phase != 'new'):
        _fail('INVALID_RESULT')
    exact_handle_slot.receipt.factory_owned = True
    _prepare_receipt(state, exact_handle_slot.receipt)
    return exact_handle_slot.owner

def _issue_pointer_owner_receipt(state, exact_pointer_slot, /):
    if type(exact_pointer_slot) is not _Slot or exact_pointer_slot.state is not state or exact_pointer_slot.kind != 'pointer' or (exact_pointer_slot.phase != _OWNED) or (exact_pointer_slot.receipt.phase != 'new'):
        _fail('INVALID_RESULT')
    exact_pointer_slot.receipt.factory_owned = True
    _prepare_receipt(state, exact_pointer_slot.receipt)
    return exact_pointer_slot.owner

def _receipt(state, value):
    if type(value) is not _Receipt or value.seal is not _SEAL or value.state is not state or (value.phase != 'claimed') or value.retired or value.attempted:
        _fail('INVALID_RESULT')

def _payload(state, receipt, value):
    kind, receiver = (receipt.kind, receipt.context)
    if kind == 'PHYSICAL':
        if type(value) is not directories.DeadlineDirectory or value is not receiver or value.deadline_token is not state.token:
            _fail('INVALID_RESULT')
    elif kind == 'NODE':
        if type(value) is not nodes.DeadlineHeldFile or value is not receiver:
            _fail('INVALID_RESULT')
        if receipt.expected.budget is not state.restricted:
            _fail('INVALID_RESULT')
    elif kind == 'CAPTURE':
        if type(value) is not capture.OriginObservation:
            _fail('INVALID_RESULT')
        if type(value.intent_bytes) is not bytes or type(value.evidence_bytes) is not bytes:
            _fail('INVALID_RESULT')
    elif kind in ('IO_PARENT', 'IO_ROOT_CREATE', 'IO_ROOT_OPEN'):
        if type(value) is not _IODirectory or value.state is not state or value.owner.slot.phase != _OWNED:
            _fail('INVALID_RESULT')
    elif kind == 'IO_HELD':
        if type(value) is not _IOHeldControl or value.state is not state or value.owner.slot.phase != _OWNED:
            _fail('INVALID_RESULT')
    elif kind in ('IO_HANDLE_OWNER', 'IO_POINTER_OWNER'):
        if type(value) is not _IOOwner or value is not receiver or value.state is not state or (value.slot.phase != _OWNED):
            _fail('INVALID_RESULT')
    elif kind == 'SCANDIR':
        if value is not receiver:
            _fail('INVALID_RESULT')
    elif kind == 'STACK':
        if type(value) is not _KnownStack or value is not receiver or value.state is not state:
            _fail('INVALID_RESULT')
    else:
        _fail('INVALID_RESULT')

def _enter_fixed_receipt(state, exact_known_receipt, /):
    _receipt(state, exact_known_receipt)
    receipt, receiver = (exact_known_receipt, exact_known_receipt.context)
    try:
        _clock(state)
        if receipt.kind == 'PHYSICAL':
            value = directories.DeadlineDirectory.__enter__(receiver)
        elif receipt.kind == 'NODE':
            value = nodes.DeadlineHeldFile.__enter__(receiver)
        elif receipt.kind == 'CAPTURE':
            value = _GeneratorContextManager.__enter__(receiver)
        elif receipt.kind in ('IO_PARENT', 'IO_ROOT_CREATE', 'IO_ROOT_OPEN', 'IO_HELD'):
            value = _IOContext.__enter__(receiver)
        elif receipt.kind in ('IO_HANDLE_OWNER', 'IO_POINTER_OWNER'):
            value = _IOOwner.__enter__(receiver)
        elif receipt.kind == 'SCANDIR':
            value = receiver.__enter__()
        elif receipt.kind == 'STACK':
            value = _KnownStack.__enter__(receiver)
        else:
            _fail('INVALID_RESULT')
    except BaseException as primary:
        _remember_primary(state, primary)
        if receipt.factory_owned:
            _exit_known_receipt(state, receipt)
        else:
            receipt.phase, receipt.retired = (_UNKNOWN, True)
        if state.first is primary:
            raise
        _raise_first(state)
    receipt.entered, receipt.phase, receipt.payload = (True, 'entered', value)
    if receipt.stack is not None:
        receipt.previous = receipt.stack.head
        receipt.stack.head = receipt
    try:
        _payload(state, receipt, value)
        _clock(state)
    except BaseException as primary:
        _remember_primary(state, primary)
        _exit_known_receipt(state, receipt)
        if state.first is primary:
            raise
        _raise_first(state)
    return value

def _exit_known_receipt(state, exact_known_receipt, /):
    receipt = exact_known_receipt
    if type(receipt) is not _Receipt or receipt.seal is not _SEAL or receipt.state is not state:
        _record_cleanup_secondary(state, Namespace3IOError('INVALID_RESULT'))
        return None
    if receipt.attempted or receipt.retired:
        return None
    if not receipt.entered and (not receipt.factory_owned):
        return None
    receipt.attempted, receipt.retired, receipt.phase = (True, True, _ATTEMPTED)
    _cleanup_clock(state)
    primary = state.first
    kind, tb = (_primary_kind(state, primary), state.first_tb if state.first_tb_available else None)
    receiver = receipt.context
    try:
        if receipt.kind == 'PHYSICAL':
            result = directories.DeadlineDirectory.__exit__(receiver, kind, primary, tb)
            _none(result)
        elif receipt.kind == 'NODE':
            result = nodes.DeadlineHeldFile.__exit__(receiver, kind, primary, tb)
            _none(result)
        elif receipt.kind == 'CAPTURE':
            result = _GeneratorContextManager.__exit__(receiver, kind, primary, tb)
            if result is not False:
                _fail('INVALID_RESULT')
        elif receipt.kind in ('IO_PARENT', 'IO_ROOT_CREATE', 'IO_ROOT_OPEN', 'IO_HELD'):
            result = _IOContext.__exit__(receiver, kind, primary, tb)
            if result is not False:
                _fail('INVALID_RESULT')
        elif receipt.kind in ('IO_HANDLE_OWNER', 'IO_POINTER_OWNER'):
            result = _IOOwner.__exit__(receiver, kind, primary, tb)
            _none(result)
        elif receipt.kind == 'SCANDIR':
            _none(receiver.close())
        elif receipt.kind == 'STACK':
            result = _KnownStack.__exit__(receiver, kind, primary, tb)
            if result is not False:
                _fail('INVALID_RESULT')
        else:
            _fail('INVALID_RESULT')
    except BaseException as secondary:
        receipt.phase = _UNKNOWN
        _record_cleanup_secondary(state, secondary)
    else:
        receipt.phase = _CLOSED
    _cleanup_clock(state)
    return None

class _ReceiptGuard:
    __slots__ = ('state', 'receipt')

    def __init__(self, state, receipt):
        _receipt(state, receipt)
        self.state, self.receipt = (state, receipt)

    def __enter__(self):
        return _enter_fixed_receipt(self.state, self.receipt)

    def __exit__(self, kind, primary, tb):
        if primary is not None:
            _remember_primary(self.state, primary)
        _exit_known_receipt(self.state, self.receipt)
        if primary is None or self.state.first is not primary:
            _raise_first(self.state)
        return False

def _typed_scope_guard(state, exact_known_receipt, /):
    try:
        return _ReceiptGuard(state, exact_known_receipt)
    except BaseException as primary:
        _remember_primary(state, primary)
        if type(exact_known_receipt) is _Receipt and exact_known_receipt.state is state and exact_known_receipt.factory_owned:
            _exit_known_receipt(state, exact_known_receipt)
        if state.first is primary:
            raise
        _raise_first(state)

class _KnownStack:
    __slots__ = ('state', 'head', 'retired', 'receipt')

    def __init__(self, state):
        self.state, self.head, self.retired = (state, None, False)
        self.receipt = _Receipt(state)
        self.receipt.kind, self.receipt.context = ('STACK', self)

    def __enter__(self):
        if self.retired:
            _fail('INVALID_RESULT')
        return self

    def _enter(self, receipt):
        if self.retired or type(receipt) is not _Receipt or receipt.state is not self.state:
            _fail('INVALID_RESULT')
        receipt.stack = self
        return _enter_fixed_receipt(self.state, receipt)

    def enter_physical(self, receiver):
        return self._enter(_claim_physical_receipt(self.state, receiver))

    def enter_node(self, receiver):
        return self._enter(_claim_node_receipt(self.state, receiver))

    def enter_capture(self, receiver):
        return self._enter(_claim_capture_receipt(self.state, receiver))

    def enter_parent(self, receiver):
        return self._enter(_claim_parent_receipt(self.state, receiver))

    def enter_root_create(self, receiver):
        return self._enter(_claim_root_create_receipt(self.state, receiver))

    def enter_root_open(self, receiver):
        return self._enter(_claim_root_open_receipt(self.state, receiver))

    def enter_held(self, receiver):
        return self._enter(_claim_held_receipt(self.state, receiver))

    def enter_scandir(self, receiver):
        return self._enter(_claim_scandir_receipt(self.state, receiver))

    def enter_handle_owner(self, receiver):
        return self._enter(_claim_handle_owner_receipt(self.state, receiver))

    def enter_pointer_owner(self, receiver):
        return self._enter(_claim_pointer_owner_receipt(self.state, receiver))

    def enter_stack(self, receiver):
        return self._enter(_claim_stack_receipt(self.state, receiver))

    def __exit__(self, kind, primary, tb):
        if primary is not None:
            _remember_primary(self.state, primary)
        if self.retired:
            _fail('INVALID_RESULT')
        self.retired = True
        while self.head is not None:
            receipt = self.head
            self.head = receipt.previous
            _exit_known_receipt(self.state, receipt)
        if primary is None or self.state.first is not primary:
            _raise_first(self.state)
        return False

def _new_known_stack(state, /):
    try:
        _clock(state)
        value = _KnownStack(state)
        if type(value) is not _KnownStack or value.state is not state or value.head is not None or value.retired is not False:
            _fail('INVALID_RESULT')
        receipt = value.receipt
        if type(receipt) is not _Receipt or receipt.state is not state or receipt.seal is not _SEAL or type(receipt.kind) is not str or receipt.kind != 'STACK' or receipt.context is not value or type(receipt.phase) is not str or receipt.phase != 'new' or receipt.entered is not False or receipt.attempted is not False or receipt.retired is not False or receipt.previous is not None or receipt.prepared_previous is not None or receipt.stack is not None or receipt.factory_owned is not False:
            _fail('INVALID_RESULT')
        _clock(state)
        return _issue_stack_receipt(state, value)
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _issue_stack_receipt(state, exact_stack, /):
    if type(exact_stack) is not _KnownStack or exact_stack.state is not state or exact_stack.receipt.phase != 'new':
        _fail('INVALID_RESULT')
    _prepare_receipt(state, exact_stack.receipt)
    return exact_stack

def _issue_physical_context(state, path, /):
    _clock(state)
    receipt = _Receipt(state)
    receipt.kind = 'PHYSICAL'
    value = directories.DeadlineDirectory(path, deadline_token=state.token, create=False, base=path)
    if type(value) is not directories.DeadlineDirectory:
        _fail('INVALID_RESULT')
    receipt.context = value
    _prepare_receipt(state, receipt)
    _clock(state)
    return value

def _issue_node_context(state, exact_data_directory, /):
    _clock(state)
    if type(exact_data_directory) is not directories.DeadlineDirectory or exact_data_directory.deadline_token is not state.token:
        _fail('INVALID_RESULT')
    receipt = _Receipt(state)
    receipt.kind = 'NODE'
    parent = nodes.DeadlineNodeDirectory(exact_data_directory, state.restricted)
    if type(parent) is not nodes.DeadlineNodeDirectory or parent.budget is not state.restricted:
        _fail('INVALID_RESULT')
    _clock(state)
    value = nodes.DeadlineHeldFile(parent)
    if type(value) is not nodes.DeadlineHeldFile:
        _fail('INVALID_RESULT')
    receipt.context, receipt.expected = (value, parent)
    _prepare_receipt(state, receipt)
    _clock(state)
    return value

def _issue_capture_context(state, profile, domain_id, facility_id, init_operation_id, /):
    _clock(state)
    receipt = _Receipt(state)
    receipt.kind = 'CAPTURE'
    value = capture.capture_locked(profile, domain_id=domain_id, facility_id=facility_id, init_operation_id=init_operation_id, deadline_token=state.token, check_cancelled=None)
    if type(value) is not _GeneratorContextManager:
        _fail('INVALID_RESULT')
    receipt.context = value
    _prepare_receipt(state, receipt)
    _clock(state)
    return value

def _issue_scandir_context(state, exact_native_path, /):
    _clock(state)
    if type(exact_native_path) is not str:
        _fail('INVALID_RESULT')
    receipt = _Receipt(state)
    receipt.kind = 'SCANDIR'
    value = os.scandir(exact_native_path)
    receipt.context, receipt.factory_owned = (value, True)
    receipt.scan_previous = state.active_scandir_head
    state.active_scandir_head = receipt
    _prepare_receipt(state, receipt)
    try:
        _clock(state)
    except BaseException as primary:
        _remember_primary(state, primary)
        _exit_known_receipt(state, receipt)
        raise
    return value

def _known_scandir_receiver(state, exact_receiver, /):
    _clock(state)
    current = state.active_scandir_head
    while current is not None:
        if type(current) is not _Receipt or current.seal is not _SEAL or current.state is not state or (current.kind != 'SCANDIR'):
            _fail('INVALID_RESULT')
        if current.context is exact_receiver:
            if not current.entered or current.retired or current.attempted:
                _fail('INVALID_RESULT')
            return exact_receiver
        current = current.scan_previous
    _fail('INVALID_RESULT')

def _budget_before_next(state, /):
    try:
        _state(state)
        _raise_first(state)
        try:
            value = state.restricted.before_next()
        except BaseException as primary:
            _remember_primary(state, primary)
            if state.first is primary:
                raise
            _raise_first(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _budget_charge(state, actual, /):
    try:
        _state(state)
        _raise_first(state)
        try:
            value = state.restricted.charge(actual)
        except BaseException as primary:
            _remember_primary(state, primary)
            if state.first is primary:
                raise
            _raise_first(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _budget_clock_only(state, /):
    try:
        _state(state)
        _raise_first(state)
        try:
            value = state.restricted.clock_only()
        except BaseException as primary:
            _remember_primary(state, primary)
            if state.first is primary:
                raise
            _raise_first(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _budget_name(state, original_name, /):
    try:
        _state(state)
        _raise_first(state)
        try:
            value = state.restricted.name(original_name)
        except BaseException as primary:
            _remember_primary(state, primary)
            if state.first is primary:
                raise
            _raise_first(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _budget_poll(state, /):
    try:
        _state(state)
        _raise_first(state)
        try:
            value = state.restricted.poll()
        except BaseException as primary:
            _remember_primary(state, primary)
            if state.first is primary:
                raise
            _raise_first(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _budget_request(state, requested, /):
    try:
        _state(state)
        _raise_first(state)
        try:
            value = state.restricted.request(requested)
        except BaseException as primary:
            _remember_primary(state, primary)
            if state.first is primary:
                raise
            _raise_first(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _ct_bootstrap_pointer_UnicodeString_l108c29():
    return ctypes.POINTER(_UnicodeString)

def _ct_byref_info_value_l365c54(state, exact_value, /):
    try:
        _clock(state)
        if type(exact_value) is not _HandleInfo:
            _fail('INVALID_RESULT')
        value = ctypes.byref(exact_value)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _ct_byref_private_descriptor_descriptor_l291c50(state, exact_descriptor, /):
    try:
        _clock(state)
        if type(exact_descriptor) is not w.LPVOID:
            _fail('INVALID_RESULT')
        value = ctypes.byref(exact_descriptor)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _ct_byref_private_descriptor_text_l280c55(state, exact_text, /):
    try:
        _clock(state)
        if type(exact_text) is not w.LPWSTR:
            _fail('INVALID_RESULT')
        value = ctypes.byref(exact_text)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _ct_byref_publish_owned_status_l627c54(state, exact_status, /):
    try:
        _clock(state)
        if type(exact_status) is not _IoStatus:
            _fail('INVALID_RESULT')
        value = ctypes.byref(exact_status)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _ct_byref_publish_owned_value_l627c76(state, exact_value, /):
    try:
        _clock(state)
        _rename_value(exact_value)
        value = ctypes.byref(exact_value)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _ct_byref_read_chunk_count_l517c52(state, exact_count, /):
    try:
        _clock(state)
        if type(exact_count) is not w.DWORD:
            _fail('INVALID_RESULT')
        value = ctypes.byref(exact_count)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _ct_byref_relative_open_attributes_l402c63(state, exact_attributes, /):
    try:
        _clock(state)
        if type(exact_attributes) is not _ObjectAttributes:
            _fail('INVALID_RESULT')
        value = ctypes.byref(exact_attributes)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _ct_byref_relative_open_handle_l402c33(state, exact_handle, /):
    try:
        _clock(state)
        if type(exact_handle) is not w.HANDLE:
            _fail('INVALID_RESULT')
        value = ctypes.byref(exact_handle)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _ct_byref_relative_open_status_l402c89(state, exact_status, /):
    try:
        _clock(state)
        if type(exact_status) is not _IoStatus:
            _fail('INVALID_RESULT')
        value = ctypes.byref(exact_status)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _ct_byref_security_check_control_l318c68(state, exact_control, /):
    try:
        _clock(state)
        if type(exact_control) is not w.WORD:
            _fail('INVALID_RESULT')
        value = ctypes.byref(exact_control)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _ct_byref_security_check_dacl_l306c39(state, exact_dacl, /):
    try:
        _clock(state)
        if type(exact_dacl) is not w.LPVOID:
            _fail('INVALID_RESULT')
        value = ctypes.byref(exact_dacl)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _ct_byref_security_check_descriptor_l306c65(state, exact_descriptor, /):
    try:
        _clock(state)
        if type(exact_descriptor) is not w.LPVOID:
            _fail('INVALID_RESULT')
        value = ctypes.byref(exact_descriptor)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _ct_byref_security_check_owner_l305c55(state, exact_owner, /):
    try:
        _clock(state)
        if type(exact_owner) is not w.LPVOID:
            _fail('INVALID_RESULT')
        value = ctypes.byref(exact_owner)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _ct_byref_security_check_pointer_l338c51(state, exact_pointer, /):
    try:
        _clock(state)
        if type(exact_pointer) is not w.LPVOID:
            _fail('INVALID_RESULT')
        value = ctypes.byref(exact_pointer)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _ct_byref_security_check_revision_l318c91(state, exact_revision, /):
    try:
        _clock(state)
        if type(exact_revision) is not w.DWORD:
            _fail('INVALID_RESULT')
        value = ctypes.byref(exact_revision)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _ct_byref_security_check_size_l326c51(state, exact_size, /):
    try:
        _clock(state)
        if type(exact_size) is not _AclSize:
            _fail('INVALID_RESULT')
        value = ctypes.byref(exact_size)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _ct_byref_system_sid_sid_l266c54(state, exact_sid, /):
    try:
        _clock(state)
        if type(exact_sid) is not w.LPVOID:
            _fail('INVALID_RESULT')
        value = ctypes.byref(exact_sid)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _ct_byref_token_user_length_l238c65(state, exact_length, /):
    try:
        _clock(state)
        if type(exact_length) is not w.DWORD:
            _fail('INVALID_RESULT')
        value = ctypes.byref(exact_length)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _ct_byref_token_user_length_l245c74(state, exact_length, /):
    try:
        _clock(state)
        if type(exact_length) is not w.DWORD:
            _fail('INVALID_RESULT')
        value = ctypes.byref(exact_length)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _ct_byref_token_user_token_l233c48(state, exact_token, /):
    try:
        _clock(state)
        if type(exact_token) is not w.HANDLE:
            _fail('INVALID_RESULT')
        value = ctypes.byref(exact_token)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _ct_byref_write_chunk_count_l529c53(state, exact_count, /):
    try:
        _clock(state)
        if type(exact_count) is not w.DWORD:
            _fail('INVALID_RESULT')
        value = ctypes.byref(exact_count)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _ct_cast_borrowed_ace_structure_pointer_l344c22(state, exact_ace_out, /):
    try:
        _clock(state)
        if type(exact_ace_out) is not w.LPVOID:
            _fail('INVALID_RESULT')
        value = ctypes.cast(exact_ace_out, _ct_pointer_type_AllowedAce_l344c43(state))
        if type(value) is not _ALLOWED_ACE_POINTER_TYPE:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _ct_cast_relative_unicode_buffer_pointer_l396c61(state, exact_name_buffer, /):
    try:
        _clock(state)
        if not _wchar_array_domain(exact_name_buffer):
            _fail('INVALID_RESULT')
        value = ctypes.cast(exact_name_buffer, w.LPWSTR)
        if type(value) is not w.LPWSTR:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _ct_cast_sid_text_allocation_pointer_l283c43(state, exact_text_out, /):
    try:
        if type(exact_text_out) is not w.LPWSTR:
            _fail('INVALID_RESULT')
        value = ctypes.cast(exact_text_out, w.LPVOID)
        if type(value) is not w.LPVOID:
            _fail('INVALID_RESULT')
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _ct_cast_token_buffer_sid_pointer_l251c14(state, exact_buffer, /):
    try:
        _clock(state)
        if not _char_array_domain(exact_buffer):
            _fail('INVALID_RESULT')
        value = ctypes.cast(exact_buffer, _ct_pointer_type_w_LPVOID_l251c34(state))
        if type(value) is not _LPVOID_POINTER_TYPE:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _ct_get_borrowed_ace_structure_pointer_l344c22(state, exact_cast_view, /):
    try:
        _clock(state)
        if type(exact_cast_view) is not _ALLOWED_ACE_POINTER_TYPE:
            _fail('INVALID_RESULT')
        value = exact_cast_view.contents
        if type(value) is not _AllowedAce:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _ct_get_sid_text_allocation_pointer_l283c43(state, exact_cast_view, /):
    try:
        if type(exact_cast_view) is not w.LPVOID:
            _fail('INVALID_RESULT')
        value = exact_cast_view.value
        if value is not None:
            _uintptr(value)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _ct_get_token_buffer_sid_pointer_l251c14(state, exact_cast_view, /):
    try:
        _clock(state)
        if type(exact_cast_view) is not _LPVOID_POINTER_TYPE:
            _fail('INVALID_RESULT')
        value = exact_cast_view[0]
        if value is not None:
            _uintptr(value)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _ct_pointer_relative_unicode_l398c34(state, exact_unicode, /):
    try:
        _clock(state)
        if type(exact_unicode) is not _UnicodeString:
            _fail('INVALID_RESULT')
        value = ctypes.pointer(exact_unicode)
        if type(value) is not _UNICODE_POINTER_TYPE:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _ct_pointer_type_AllowedAce_l344c43(state, /):
    try:
        _clock(state)
        value = ctypes.POINTER(_AllowedAce)
        if value is not _ALLOWED_ACE_POINTER_TYPE:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _ct_pointer_type_w_LPVOID_l251c34(state, /):
    try:
        _clock(state)
        value = ctypes.POINTER(w.LPVOID)
        if value is not _LPVOID_POINTER_TYPE:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _ct_sizeof_acl_instance_l326c71(state, exact_acl_size, /):
    try:
        _clock(state)
        if type(exact_acl_size) is not _AclSize:
            _fail('INVALID_RESULT')
        value = ctypes.sizeof(exact_acl_size)
        if type(value) is not int or value <= 0:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _ct_sizeof_current_rename_instance_l627c97(state, exact_rename_info, /):
    try:
        _clock(state)
        _rename_value(exact_rename_info)
        value = ctypes.sizeof(exact_rename_info)
        if type(value) is not int or value <= 0:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _ct_sizeof_fill_pointer_minimum_l249c15(state, /):
    try:
        _clock(state)
        value = ctypes.sizeof(ctypes.c_void_p)
        if type(value) is not int or value not in (4, 8):
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _ct_sizeof_object_attributes_type_l397c35(state, /):
    try:
        _clock(state)
        value = ctypes.sizeof(_ObjectAttributes)
        if type(value) is not int or value not in (24, 48):
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _ct_sizeof_probe_pointer_minimum_l241c43(state, /):
    try:
        _clock(state)
        value = ctypes.sizeof(ctypes.c_void_p)
        if type(value) is not int or value not in (4, 8):
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _ct_sizeof_supported_attributes_l180c13(state, /):
    try:
        _clock(state)
        value = ctypes.sizeof(_ObjectAttributes)
        if type(value) is not int or value <= 0:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _ct_sizeof_supported_pointer_width_l179c31(state, /):
    try:
        _clock(state)
        value = ctypes.sizeof(ctypes.c_void_p)
        if type(value) is not int or value <= 0:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _ct_sizeof_supported_status_l180c13(state, /):
    try:
        _clock(state)
        value = ctypes.sizeof(_IoStatus)
        if type(value) is not int or value <= 0:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _ct_sizeof_supported_unicode_l180c13(state, /):
    try:
        _clock(state)
        value = ctypes.sizeof(_UnicodeString)
        if type(value) is not int or value <= 0:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_aceheader_borrowed_field_of_known_allowedace_flags_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not _AceHeader:
            _fail('INVALID_RESULT')
        value = exact_receiver.flags
        if type(value) is not int or not 0 <= value <= 255:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_aceheader_borrowed_field_of_known_allowedace_kind_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not _AceHeader:
            _fail('INVALID_RESULT')
        value = exact_receiver.kind
        if type(value) is not int or not 0 <= value <= 255:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_aceheader_borrowed_field_of_known_allowedace_size_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not _AceHeader:
            _fail('INVALID_RESULT')
        value = exact_receiver.size
        if type(value) is not int or not 0 <= value <= 65535:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_aclsize_output_count_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not _AclSize:
            _fail('INVALID_RESULT')
        value = exact_receiver.count
        _uint32(value)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_aclsize_output_used_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not _AclSize:
            _fail('INVALID_RESULT')
        value = exact_receiver.used
        _uint32(value)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_allowedace_borrowed_view_header_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not _AllowedAce:
            _fail('INVALID_RESULT')
        value = exact_receiver.header
        if type(value) is not _AceHeader:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_allowedace_borrowed_view_mask_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not _AllowedAce:
            _fail('INVALID_RESULT')
        value = exact_receiver.mask
        _uint32(value)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_current_known_private_io_owner_fixed_closer_kind_failure_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not _IOOwner or exact_receiver.state is not state:
            _fail('INVALID_RESULT')
        value = exact_receiver.failure
        if value is not None and (not isinstance(value, BaseException)):
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_current_known_private_io_owner_fixed_closer_kind_retired_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not _IOOwner or exact_receiver.state is not state:
            _fail('INVALID_RESULT')
        value = exact_receiver.retired
        if type(value) is not bool:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_current_known_private_io_owner_fixed_closer_kind_value_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not _IOOwner or exact_receiver.state is not state:
            _fail('INVALID_RESULT')
        value = exact_receiver.value
        if value is not None:
            _uintptr(value)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_current_private_io_directory_with_same_b_t_budget_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not _IODirectory or exact_receiver.state is not state:
            _fail('INVALID_RESULT')
        value = exact_receiver.budget
        if value is not state.restricted:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_current_private_io_directory_with_same_b_t_handle_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not _IODirectory or exact_receiver.state is not state:
            _fail('INVALID_RESULT')
        value = exact_receiver.handle
        _directory_handle_domain(value, exact_receiver, state)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_current_private_io_directory_with_same_b_t_name_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not _IODirectory or exact_receiver.state is not state:
            _fail('INVALID_RESULT')
        value = exact_receiver.name
        if value is not None and type(value) is not str:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_current_private_io_directory_with_same_b_t_native_identity_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not _IODirectory or exact_receiver.state is not state:
            _fail('INVALID_RESULT')
        value = exact_receiver.native_identity
        _native_identity_domain(value)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_current_private_io_directory_with_same_b_t_owner_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not _IODirectory or exact_receiver.state is not state:
            _fail('INVALID_RESULT')
        value = exact_receiver.owner
        _owner_domain(value, state)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_current_private_io_directory_with_same_b_t_parent_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not _IODirectory or exact_receiver.state is not state:
            _fail('INVALID_RESULT')
        value = exact_receiver.parent
        if value is not None:
            _directory_domain(value, state)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_current_private_io_directory_with_same_b_t_path_identity_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not _IODirectory or exact_receiver.state is not state:
            _fail('INVALID_RESULT')
        value = exact_receiver.path_identity
        _directory_identity_domain(value)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_current_private_io_directory_with_same_b_t_path_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not _IODirectory or exact_receiver.state is not state:
            _fail('INVALID_RESULT')
        value = exact_receiver.path
        if type(value) is not _PATH_TYPE:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_current_private_io_held_control_known_lifetime_byte_size_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not _IOHeldControl or exact_receiver.state is not state:
            _fail('INVALID_RESULT')
        value = exact_receiver.byte_size
        _held_size_domain(value, exact_receiver)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_current_private_io_held_control_known_lifetime_native_identity_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not _IOHeldControl or exact_receiver.state is not state:
            _fail('INVALID_RESULT')
        value = exact_receiver.native_identity
        _native_identity_domain(value)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_current_private_io_held_control_known_lifetime_path_identity_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not _IOHeldControl or exact_receiver.state is not state:
            _fail('INVALID_RESULT')
        value = exact_receiver.path_identity
        _file_identity_domain(value)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_current_private_io_held_control_known_lifetime_raw_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not _IOHeldControl or exact_receiver.state is not state:
            _fail('INVALID_RESULT')
        value = exact_receiver.raw
        if type(value) is not bytes:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_current_private_io_held_control_known_lifetime_raw_sha256_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not _IOHeldControl or exact_receiver.state is not state:
            _fail('INVALID_RESULT')
        value = exact_receiver.raw_sha256
        _sha256_domain(value)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_current_private_io_held_control_with_same_b_t_budget_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not _IOHeldControl or exact_receiver.state is not state:
            _fail('INVALID_RESULT')
        value = exact_receiver.budget
        if value is not state.restricted:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_current_private_io_held_control_with_same_b_t_byte_size_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not _IOHeldControl or exact_receiver.state is not state:
            _fail('INVALID_RESULT')
        value = exact_receiver.byte_size
        _held_size_domain(value, exact_receiver)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_current_private_io_held_control_with_same_b_t_directory_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not _IOHeldControl or exact_receiver.state is not state:
            _fail('INVALID_RESULT')
        value = exact_receiver.directory
        _directory_domain(value, state)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_current_private_io_held_control_with_same_b_t_maximum_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not _IOHeldControl or exact_receiver.state is not state:
            _fail('INVALID_RESULT')
        value = exact_receiver.maximum
        if type(value) is not int or not 0 < value <= 64 * 1024:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_current_private_io_held_control_with_same_b_t_native_identity_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not _IOHeldControl or exact_receiver.state is not state:
            _fail('INVALID_RESULT')
        value = exact_receiver.native_identity
        _native_identity_domain(value)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_current_private_io_held_control_with_same_b_t_owner_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not _IOHeldControl or exact_receiver.state is not state:
            _fail('INVALID_RESULT')
        value = exact_receiver.owner
        _owner_domain(value, state)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_current_private_io_held_control_with_same_b_t_path_identity_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not _IOHeldControl or exact_receiver.state is not state:
            _fail('INVALID_RESULT')
        value = exact_receiver.path_identity
        _file_identity_domain(value)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_current_private_io_held_control_with_same_b_t_path_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not _IOHeldControl or exact_receiver.state is not state:
            _fail('INVALID_RESULT')
        value = exact_receiver.path
        if type(value) is not _PATH_TYPE:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_current_private_io_held_control_with_same_b_t_raw_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not _IOHeldControl or exact_receiver.state is not state:
            _fail('INVALID_RESULT')
        value = exact_receiver.raw
        if type(value) is not bytes:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_current_private_io_held_control_with_same_b_t_stamp_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not _IOHeldControl or exact_receiver.state is not state:
            _fail('INVALID_RESULT')
        value = exact_receiver.stamp
        _stamp_domain(value)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_current_read_ctypes_buffer_raw_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if not _char_array_domain(exact_receiver):
            _fail('INVALID_RESULT')
        value = exact_receiver.raw
        if type(value) is not bytes:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_filetime_borrowed_field_of_fresh_handleinfo_high_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not _FileTime:
            _fail('INVALID_RESULT')
        value = exact_receiver.high
        _uint32(value)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_filetime_borrowed_field_of_fresh_handleinfo_low_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not _FileTime:
            _fail('INVALID_RESULT')
        value = exact_receiver.low
        _uint32(value)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_handleinfo_output_attributes_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not _HandleInfo:
            _fail('INVALID_RESULT')
        value = exact_receiver.attributes
        _uint32(value)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_handleinfo_output_created_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not _HandleInfo:
            _fail('INVALID_RESULT')
        value = exact_receiver.created
        if type(value) is not _FileTime:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_handleinfo_output_index_high_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not _HandleInfo:
            _fail('INVALID_RESULT')
        value = exact_receiver.index_high
        _uint32(value)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_handleinfo_output_index_low_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not _HandleInfo:
            _fail('INVALID_RESULT')
        value = exact_receiver.index_low
        _uint32(value)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_handleinfo_output_links_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not _HandleInfo:
            _fail('INVALID_RESULT')
        value = exact_receiver.links
        _uint32(value)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_handleinfo_output_size_high_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not _HandleInfo:
            _fail('INVALID_RESULT')
        value = exact_receiver.size_high
        _uint32(value)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_handleinfo_output_size_low_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not _HandleInfo:
            _fail('INVALID_RESULT')
        value = exact_receiver.size_low
        _uint32(value)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_handleinfo_output_volume_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not _HandleInfo:
            _fail('INVALID_RESULT')
        value = exact_receiver.volume
        _uint32(value)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_handleinfo_output_written_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not _HandleInfo:
            _fail('INVALID_RESULT')
        value = exact_receiver.written
        if type(value) is not _FileTime:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_iostatus_output_information_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not _IoStatus:
            _fail('INVALID_RESULT')
        value = exact_receiver.information
        _uintptr(value)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_iostatus_output_status_existing_creator_known_raw_nt_completion_before_original630_clock(state, exact_receiver, /):
    try:
        if type(exact_receiver) is not _IoStatus:
            _fail('INVALID_RESULT')
        value = exact_receiver.status
        _signed32(value)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_iostatus_output_status_required_legal_success_known_owner_value_occurrence_before_adoption_pos(state, exact_receiver, /):
    try:
        if type(exact_receiver) is not _IoStatus:
            _fail('INVALID_RESULT')
        value = exact_receiver.status
        _signed32(value)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_os_stat_result_fresh_path_observation_st_dev_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not os.stat_result:
            _fail('INVALID_RESULT')
        value = exact_receiver.st_dev
        if type(value) is not int:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_os_stat_result_fresh_path_observation_st_ino_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not os.stat_result:
            _fail('INVALID_RESULT')
        value = exact_receiver.st_ino
        if type(value) is not int:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_path_exact_profile_database_parent_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not _PATH_TYPE:
            _fail('INVALID_RESULT')
        value = exact_receiver.parent
        if type(value) is not _PATH_TYPE:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_private_io_owner_with_current_slot_lifetime_failure_known_context_slot_first_primary_tb_note_a(state, exact_receiver, /):
    if type(exact_receiver) is not _IOOwner or exact_receiver.state is not state:
        _fail('INVALID_RESULT')
    value = exact_receiver.failure
    if value is not None and (not isinstance(value, BaseException)):
        _fail('INVALID_RESULT')
    return value

def _get_exact_private_io_owner_with_current_slot_lifetime_retired_known_context_slot_first_primary_tb_note_a(state, exact_receiver, /):
    if type(exact_receiver) is not _IOOwner or exact_receiver.state is not state:
        _fail('INVALID_RESULT')
    value = exact_receiver.retired
    if type(value) is not bool:
        _fail('INVALID_RESULT')
    return value

def _get_exact_private_io_owner_with_current_slot_lifetime_retired_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not _IOOwner or exact_receiver.state is not state:
            _fail('INVALID_RESULT')
        value = exact_receiver.retired
        if type(value) is not bool:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_private_io_owner_with_current_slot_lifetime_value_known_context_slot_first_primary_tb_note_att(state, exact_receiver, /):
    if type(exact_receiver) is not _IOOwner or exact_receiver.state is not state:
        _fail('INVALID_RESULT')
    value = exact_receiver.value
    return value

def _get_exact_storageprofile_already_validated_database_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not lifecycle.StorageProfile:
            _fail('INVALID_RESULT')
        value = exact_receiver.database
        if type(value) is not _PATH_TYPE:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_storageprofile_already_validated_identity_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not lifecycle.StorageProfile:
            _fail('INVALID_RESULT')
        value = exact_receiver.identity
        _profile_identity_domain(value)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_w_dword_actual_read_count_value_read_count_g1_validate(state, exact_receiver, /):
    try:
        if type(exact_receiver) is not w.DWORD:
            _fail('INVALID_RESULT')
        value = exact_receiver.value
        _uint32(value)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_w_dword_actual_read_count_value_read_count_g2_charge_operand(state, exact_receiver, /):
    try:
        if type(exact_receiver) is not w.DWORD:
            _fail('INVALID_RESULT')
        value = exact_receiver.value
        _uint32(value)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_w_dword_actual_read_count_value_read_count_g3_return_reread(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not w.DWORD:
            _fail('INVALID_RESULT')
        value = exact_receiver.value
        _uint32(value)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_w_dword_actual_write_count_value_write_count_g1_validate(state, exact_receiver, /):
    try:
        if type(exact_receiver) is not w.DWORD:
            _fail('INVALID_RESULT')
        value = exact_receiver.value
        _uint32(value)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_w_dword_actual_write_count_value_write_count_g2_charge_operand(state, exact_receiver, /):
    try:
        if type(exact_receiver) is not w.DWORD:
            _fail('INVALID_RESULT')
        value = exact_receiver.value
        _uint32(value)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_w_dword_actual_write_count_value_write_count_g3_return_reread(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not w.DWORD:
            _fail('INVALID_RESULT')
        value = exact_receiver.value
        _uint32(value)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_w_dword_output_value_fill_length_later_reread_after_248(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not w.DWORD:
            _fail('INVALID_RESULT')
        value = exact_receiver.value
        _uint32(value)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_w_dword_output_value_probe_length_buffer_size_reread_after_240(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not w.DWORD:
            _fail('INVALID_RESULT')
        value = exact_receiver.value
        _uint32(value)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_w_dword_output_value_probe_length_domain_after_240(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not w.DWORD:
            _fail('INVALID_RESULT')
        value = exact_receiver.value
        _uint32(value)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_w_handle_output_value_required_legal_success_known_owner_value_occurrence_before_adoption_post(state, exact_receiver, /):
    try:
        if type(exact_receiver) is not w.HANDLE:
            _fail('INVALID_RESULT')
        value = exact_receiver.value
        if value is not None:
            _uintptr(value)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_w_lpvoid_allocation_output_value_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not w.LPVOID:
            _fail('INVALID_RESULT')
        value = exact_receiver.value
        if value is not None:
            _uintptr(value)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_w_lpvoid_allocation_output_value_required_legal_success_known_owner_value_occurrence_before_ad(state, exact_receiver, /):
    try:
        if type(exact_receiver) is not w.LPVOID:
            _fail('INVALID_RESULT')
        value = exact_receiver.value
        if value is not None:
            _uintptr(value)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_w_lpvoid_borrowed_ace_output_value_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not w.LPVOID:
            _fail('INVALID_RESULT')
        value = exact_receiver.value
        if value is not None:
            _uintptr(value)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_w_lpvoid_borrowed_dacl_output_value_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not w.LPVOID:
            _fail('INVALID_RESULT')
        value = exact_receiver.value
        if value is not None:
            _uintptr(value)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_w_lpvoid_borrowed_owner_output_value_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not w.LPVOID:
            _fail('INVALID_RESULT')
        value = exact_receiver.value
        if value is not None:
            _uintptr(value)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_w_lpvoid_output_value_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not w.LPVOID:
            _fail('INVALID_RESULT')
        value = exact_receiver.value
        if value is not None:
            _uintptr(value)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_w_lpvoid_output_value_required_legal_success_known_owner_value_occurrence_before_adoption_post(state, exact_receiver, /):
    try:
        if type(exact_receiver) is not w.LPVOID:
            _fail('INVALID_RESULT')
        value = exact_receiver.value
        if value is not None:
            _uintptr(value)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_w_lpwstr_output_value_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not w.LPWSTR:
            _fail('INVALID_RESULT')
        value = exact_receiver.value
        if type(value) is not str or not 0 < len(value) <= 256:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_exact_w_word_output_value_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if type(exact_receiver) is not w.WORD:
            _fail('INVALID_RESULT')
        value = exact_receiver.value
        if type(value) is not int or not 0 <= value <= 65535:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_pinned_imported_os_module_name_original_ordinary_field_read(state, exact_receiver, /):
    try:
        _clock(state)
        if exact_receiver is not os:
            _fail('INVALID_RESULT')
        value = exact_receiver.name
        if type(value) is not str:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_stat_st_ctime_ns(state, exact_stat, /):
    try:
        _clock(state)
        if type(exact_stat) is not os.stat_result:
            _fail('INVALID_RESULT')
        value = exact_stat.st_ctime_ns
        if type(value) is not int:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_stat_st_dev(state, exact_stat, /):
    try:
        _clock(state)
        if type(exact_stat) is not os.stat_result:
            _fail('INVALID_RESULT')
        value = exact_stat.st_dev
        if type(value) is not int:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_stat_st_ino(state, exact_stat, /):
    try:
        _clock(state)
        if type(exact_stat) is not os.stat_result:
            _fail('INVALID_RESULT')
        value = exact_stat.st_ino
        if type(value) is not int:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_stat_st_mode(state, exact_stat, /):
    try:
        _clock(state)
        if type(exact_stat) is not os.stat_result:
            _fail('INVALID_RESULT')
        value = exact_stat.st_mode
        if type(value) is not int:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_stat_st_mtime_ns(state, exact_stat, /):
    try:
        _clock(state)
        if type(exact_stat) is not os.stat_result:
            _fail('INVALID_RESULT')
        value = exact_stat.st_mtime_ns
        if type(value) is not int:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_stat_st_nlink(state, exact_stat, /):
    try:
        _clock(state)
        if type(exact_stat) is not os.stat_result:
            _fail('INVALID_RESULT')
        value = exact_stat.st_nlink
        if type(value) is not int:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _get_stat_st_size(state, exact_stat, /):
    try:
        _clock(state)
        if type(exact_stat) is not os.stat_result:
            _fail('INVALID_RESULT')
        value = exact_stat.st_size
        if type(value) is not int:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _native_closehandle(state, known_handle, /):
    value = _kernel.CloseHandle(known_handle)
    _signed32(value)
    if value == 0:
        raise _native_error(_failure_error_raw(state), 'CloseHandle')
    return value

def _native_convertsidtostringsidw(state, known_sid, exact_text_out, /):
    try:
        _clock(state)
        value = _advapi.ConvertSidToStringSidW(known_sid, exact_text_out)
        _signed32(value)
        if value == 0:
            raise _native_error(_failure_error_raw(state), 'ConvertSidToStringSidW')
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _native_convertstringsecuritydescriptortosecuritydescriptorw(state, bounded_sddl, revision, exact_pointer_out, length_out, /):
    try:
        _clock(state)
        value = _advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(bounded_sddl, revision, exact_pointer_out, length_out)
        _signed32(value)
        if value == 0:
            raise _native_error(_failure_error_raw(state), 'ConvertStringSecurityDescriptorToSecurityDescriptorW')
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _native_convertstringsidtosidw(state, constant_system_sid, exact_pointer_out, /):
    try:
        _clock(state)
        value = _advapi.ConvertStringSidToSidW(constant_system_sid, exact_pointer_out)
        _signed32(value)
        if value == 0:
            raise _native_error(_failure_error_raw(state), 'ConvertStringSidToSidW')
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _native_createfilew(state, path, access, share, attributes, disposition, flags, template, /):
    try:
        _clock(state)
        value = _kernel.CreateFileW(path, access, share, attributes, disposition, flags, template)
        if value is None or value == _INVALID_HANDLE:
            raise _native_error(_failure_error_raw(state), 'parent open')
        _handle_value(value)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _native_equalsid(state, known_left_sid, known_right_sid, /):
    try:
        _clock(state)
        value = _advapi.EqualSid(known_left_sid, known_right_sid)
        _signed32(value)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _native_flushfilebuffers(state, known_handle, /):
    try:
        _clock(state)
        value = _kernel.FlushFileBuffers(known_handle)
        _signed32(value)
        if value == 0:
            raise _native_error(_failure_error_raw(state), 'FlushFileBuffers')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _native_getace(state, known_acl, index, exact_pointer_out, /):
    try:
        _clock(state)
        value = _advapi.GetAce(known_acl, index, exact_pointer_out)
        _signed32(value)
        if value == 0:
            raise _native_error(_failure_error_raw(state), 'GetAce')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _native_getaclinformation(state, known_acl, exact_size_out, size, information_class, /):
    try:
        _clock(state)
        value = _advapi.GetAclInformation(known_acl, exact_size_out, size, information_class)
        _signed32(value)
        if value == 0:
            raise _native_error(_failure_error_raw(state), 'GetAclInformation')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _native_getcurrentprocess(state, /):
    try:
        _clock(state)
        value = _kernel.GetCurrentProcess()
        _uintptr(value, nonzero=True)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _native_getfileinformationbyhandle(state, known_handle, exact_info_out, /):
    try:
        _clock(state)
        value = _kernel.GetFileInformationByHandle(known_handle, exact_info_out)
        _signed32(value)
        if value == 0:
            raise _native_error(_failure_error_raw(state), 'GetFileInformationByHandle')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _native_getlengthsid(state, known_sid, /):
    try:
        _clock(state)
        value = _advapi.GetLengthSid(known_sid)
        _uint32(value)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _native_getsecuritydescriptorcontrol(state, known_descriptor, control_out, revision_out, /):
    try:
        _clock(state)
        value = _advapi.GetSecurityDescriptorControl(known_descriptor, control_out, revision_out)
        _signed32(value)
        if value == 0:
            raise _native_error(_failure_error_raw(state), 'GetSecurityDescriptorControl')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _native_getsecurityinfo(state, known_handle, object_kind, information_mask, owner_out, group_out, dacl_out, sacl_out, descriptor_out, /):
    try:
        _clock(state)
        value = _advapi.GetSecurityInfo(known_handle, object_kind, information_mask, owner_out, group_out, dacl_out, sacl_out, descriptor_out)
        _uint32(value)
        if value != 0:
            raise _native_error(value, 'security information')
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _native_isvalidsecuritydescriptor(state, known_descriptor, /):
    try:
        _clock(state)
        value = _advapi.IsValidSecurityDescriptor(known_descriptor)
        _signed32(value)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _native_isvalidsid(state, known_sid, /):
    try:
        _clock(state)
        value = _advapi.IsValidSid(known_sid)
        _signed32(value)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _native_localfree(state, known_pointer, /):
    value = _kernel.LocalFree(known_pointer)
    if value is None:
        return value
    _uintptr(value)
    if value != 0:
        raise _native_error(_failure_error_raw(state), 'free')
    return value

def _native_ntcreatefile(state, exact_handle_out, access, exact_attributes, exact_status_out, allocation_size, file_attributes, share, disposition, options, ea, ea_length, /):
    try:
        _clock(state)
        value = _ntdll.NtCreateFile(exact_handle_out, access, exact_attributes, exact_status_out, allocation_size, file_attributes, share, disposition, options, ea, ea_length)
        _signed32(value)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _native_ntsetinformationfile(state, known_handle, exact_status_out, exact_information, information_size, information_class, /):
    try:
        _clock(state)
        value = _ntdll.NtSetInformationFile(known_handle, exact_status_out, exact_information, information_size, information_class)
        _signed32(value)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _native_openprocesstoken(state, borrowed_process, access, exact_handle_out, /):
    try:
        _clock(state)
        value = _advapi.OpenProcessToken(borrowed_process, access, exact_handle_out)
        _signed32(value)
        if value == 0:
            raise _native_error(_failure_error_raw(state), 'OpenProcessToken')
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _native_readfile(state, known_handle, exact_buffer, requested, exact_count_out, overlapped, /):
    try:
        _clock(state)
        value = _kernel.ReadFile(known_handle, exact_buffer, requested, exact_count_out, overlapped)
        _signed32(value)
        if value == 0:
            raise _native_error(_failure_error_raw(state), 'ReadFile')
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _native_setfilepointerex(state, known_handle, offset, new_position, method, /):
    try:
        _clock(state)
        value = _kernel.SetFilePointerEx(known_handle, offset, new_position, method)
        _signed32(value)
        if value == 0:
            raise _native_error(_failure_error_raw(state), 'SetFilePointerEx')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _native_writefile(state, known_handle, exact_buffer, requested, exact_count_out, overlapped, /):
    try:
        _clock(state)
        value = _kernel.WriteFile(known_handle, exact_buffer, requested, exact_count_out, overlapped)
        _signed32(value)
        if value == 0:
            raise _native_error(_failure_error_raw(state), 'WriteFile')
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _new_aclsize(state, /):
    try:
        _clock(state)
        value = _AclSize()
        if type(value) is not _AclSize:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _new_dword(state, /):
    try:
        _clock(state)
        value = w.DWORD()
        if type(value) is not w.DWORD:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _new_handle(state, /):
    try:
        _clock(state)
        value = w.HANDLE()
        if type(value) is not w.HANDLE:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _new_handleinfo(state, /):
    try:
        _clock(state)
        value = _HandleInfo()
        if type(value) is not _HandleInfo:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _new_iostatus(state, /):
    try:
        _clock(state)
        value = _IoStatus()
        if type(value) is not _IoStatus:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _new_lpvoid(state, /):
    try:
        _clock(state)
        value = w.LPVOID()
        if type(value) is not w.LPVOID:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _new_lpwstr(state, /):
    try:
        _clock(state)
        value = w.LPWSTR()
        if type(value) is not w.LPWSTR:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _new_word(state, /):
    try:
        _clock(state)
        value = w.WORD()
        if type(value) is not w.WORD:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _claim_physical_receipt(state, exact_receiver, /):
    return _claim_receipt(state, exact_receiver, 'PHYSICAL')

def _claim_node_receipt(state, exact_receiver, /):
    return _claim_receipt(state, exact_receiver, 'NODE')

def _claim_capture_receipt(state, exact_receiver, /):
    return _claim_receipt(state, exact_receiver, 'CAPTURE')

def _claim_parent_receipt(state, exact_receiver, /):
    return _claim_receipt(state, exact_receiver, 'IO_PARENT')

def _claim_root_create_receipt(state, exact_receiver, /):
    return _claim_receipt(state, exact_receiver, 'IO_ROOT_CREATE')

def _claim_root_open_receipt(state, exact_receiver, /):
    return _claim_receipt(state, exact_receiver, 'IO_ROOT_OPEN')

def _claim_held_receipt(state, exact_receiver, /):
    return _claim_receipt(state, exact_receiver, 'IO_HELD')

def _claim_scandir_receipt(state, exact_receiver, /):
    return _claim_receipt(state, exact_receiver, 'SCANDIR')

def _claim_handle_owner_receipt(state, exact_receiver, /):
    return _claim_receipt(state, exact_receiver, 'IO_HANDLE_OWNER')

def _claim_pointer_owner_receipt(state, exact_receiver, /):
    return _claim_receipt(state, exact_receiver, 'IO_POINTER_OWNER')

def _claim_stack_receipt(state, exact_receiver, /):
    return _claim_receipt(state, exact_receiver, 'STACK')

def _append_bytes(state, exact_parts_list, exact_chunk, /):
    try:
        _clock(state)
        if type(exact_parts_list) is not list or type(exact_chunk) is not bytes:
            _fail('INVALID_RESULT')
        _none(list.append(exact_parts_list, exact_chunk))
        _clock(state)
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _copy_dict(state, exact_fixed_mapping, /):
    try:
        _clock(state)
        if type(exact_fixed_mapping) is not dict:
            _fail('INVALID_RESULT')
        value = dict(exact_fixed_mapping)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _deepcopy_fixed_json(state, exact_bounded_json, /):
    try:
        _clock(state)
        if type(exact_bounded_json) not in (dict, list):
            _fail('INVALID_RESULT')
        value = deepcopy(exact_bounded_json)
        if type(value) is not type(exact_bounded_json):
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _join_bytes(state, exact_byte_parts, /):
    try:
        _clock(state)
        if type(exact_byte_parts) is not list or any((type(piece) is not bytes for piece in exact_byte_parts)):
            _fail('INVALID_RESULT')
        value = b''.join(exact_byte_parts)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _set_add_seen(state, exact_set, bounded_member, /):
    try:
        _clock(state)
        if type(exact_set) is not set or type(bounded_member) is not str:
            _fail('INVALID_RESULT')
        _none(set.add(exact_set, bounded_member))
        _clock(state)
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _entry_name(state, exact_fixed_name, /):
    try:
        _clock(state)
        if type(exact_fixed_name) is not str:
            _fail('INVALID_RESULT')
        value = safe._entry_name(exact_fixed_name)
        if type(value) is not str or value is not exact_fixed_name:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _profile(state, exact_profile, /):
    try:
        _clock(state)
        if type(exact_profile) is not lifecycle.StorageProfile:
            _fail('INVALID_RESULT')
        value = lifecycle._profile(exact_profile)
        if type(value) is not lifecycle.StorageProfile or value is not exact_profile:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _guard(state, exact_profile, /):
    try:
        _clock(state)
        if type(exact_profile) is not lifecycle.StorageProfile:
            _fail('INVALID_RESULT')
        value = lifecycle.require_guard(exact_profile, exclusive=True)
        if type(value) is not lifecycle.StorageGuard:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _path(state, validated_absolute_path, /):
    try:
        _clock(state)
        value = Path(validated_absolute_path)
        if type(value) is not _PATH_TYPE or not value.is_absolute():
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _native_absolute(state, exact_absolute_text, /):
    try:
        _clock(state)
        if type(exact_absolute_text) is not str or not exact_absolute_text or '\x00' in exact_absolute_text:
            _fail('INVALID_RESULT')
        text = exact_absolute_text
        if text.startswith('\\\\?\\'):
            rest = text[4:]
            if rest.startswith('UNC\\'):
                if len(rest[4:].split('\\')) < 2 or not all(rest[4:].split('\\')[:2]):
                    _fail('INVALID_ARGUMENT')
            elif not (len(rest) >= 3 and rest[0].isascii() and rest[0].isalpha() and (rest[1:3] == ':\\')):
                _fail('INVALID_ARGUMENT')
            value = text
        elif text.startswith('\\\\'):
            if len(text[2:].split('\\')) < 2 or not all(text[2:].split('\\')[:2]):
                _fail('INVALID_ARGUMENT')
            value = '\\\\?\\UNC\\' + text[2:]
        elif len(text) >= 3 and text[0].isascii() and text[0].isalpha() and (text[1:3] == ':\\'):
            value = '\\\\?\\' + text
        else:
            _fail('INVALID_ARGUMENT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _path_stat(state, exact_absolute_path, /):
    try:
        native = _native_absolute(state, str(exact_absolute_path))
        _clock(state)
        value = os.stat(native, follow_symlinks=False)
        if type(value) is not os.stat_result:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _safe_stat(state, exact_stat, directory_role, /):
    try:
        _clock(state)
        if type(exact_stat) is not os.stat_result or type(directory_role) is not bool:
            _fail('INVALID_RESULT')
        _none(safe._safe_stat(exact_stat, directory=directory_role))
        _clock(state)
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _stat_file_identity(state, exact_path_stat, /):
    try:
        _safe_stat(state, exact_path_stat, False)
        if _get_stat_st_nlink(state, exact_path_stat) != 1:
            raise facts.PreservationFileError('PERSONAL_PRESERVATION_FILE_UNSAFE')
        return {'device': str(_get_stat_st_dev(state, exact_path_stat)), 'inode': str(_get_stat_st_ino(state, exact_path_stat)), 'byte_size': _get_stat_st_size(state, exact_path_stat), 'mtime_ns': str(_get_stat_st_mtime_ns(state, exact_path_stat)), 'ctime_ns': str(_get_stat_st_ctime_ns(state, exact_path_stat)), 'mode': _get_stat_st_mode(state, exact_path_stat), 'nlink': _get_stat_st_nlink(state, exact_path_stat)}
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _new_sha256(state, bounded_exact_raw_or_empty, /):
    try:
        _clock(state)
        if type(bounded_exact_raw_or_empty) is not bytes or len(bounded_exact_raw_or_empty) > 64 * 1024:
            _fail('INVALID_RESULT')
        value = hashlib.sha256(bounded_exact_raw_or_empty)
        if type(value) is not _HASH_TYPE:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _hash_hex(state, exact_sha256, /):
    try:
        _clock(state)
        if type(exact_sha256) is not _HASH_TYPE:
            _fail('INVALID_RESULT')
        value = exact_sha256.hexdigest()
        if type(value) is not str or len(value) != 64 or any((c not in '0123456789abcdef' for c in value)):
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _name_utf16(state, fixed_exact_name, /):
    try:
        _clock(state)
        if type(fixed_exact_name) is not str:
            _fail('INVALID_RESULT')
        value = str.encode(fixed_exact_name, 'utf-16-le', errors='strict')
        if type(value) is not bytes or len(value) > 65532:
            _fail('INVALID_ARGUMENT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _record_decode(state, exact_bounded_raw, /):
    try:
        _clock(state)
        if type(exact_bounded_raw) is not bytes or not 0 < len(exact_bounded_raw) <= contract.MAX_RECORD_BYTES:
            _fail('INVALID_RESULT')
        value = contract.decode(exact_bounded_raw)
        if type(value) is not dict:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _stage_name(state, fixed_name, canonical_operation, /):
    try:
        _clock(state)
        value = contract.stage_name(fixed_name, canonical_operation)
        if type(value) is not str:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _prepare_read_buffer(state, original_prepared_operand, /):
    try:
        _clock(state)
        if type(original_prepared_operand) is not int or not 0 < original_prepared_operand <= CHUNK_BYTES:
            _fail('INVALID_RESULT')
        value = ctypes.create_string_buffer(original_prepared_operand)
        if not _char_array_domain(value):
            _fail('INVALID_RESULT')
        if len(value) != original_prepared_operand:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _prepare_token_buffer(state, original_prepared_operand, /):
    try:
        _clock(state)
        if type(original_prepared_operand) is not int or not _POINTER_BYTES <= original_prepared_operand <= 65536:
            _fail('INVALID_RESULT')
        value = ctypes.create_string_buffer(original_prepared_operand)
        if not _char_array_domain(value):
            _fail('INVALID_RESULT')
        if len(value) != original_prepared_operand:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _prepare_token_buffer_base(state, original_prepared_operand, /):
    try:
        _clock(state)
        if not _char_array_domain(original_prepared_operand):
            _fail('INVALID_RESULT')
        value = ctypes.addressof(original_prepared_operand)
        _uintptr(value, nonzero=True)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _prepare_write_buffer(state, original_prepared_operand, /):
    try:
        _clock(state)
        if type(original_prepared_operand) is not tuple or len(original_prepared_operand) != 2 or type(original_prepared_operand[0]) is not bytes or (type(original_prepared_operand[1]) is not int) or (not 0 < original_prepared_operand[1] <= CHUNK_BYTES) or (len(original_prepared_operand[0]) != original_prepared_operand[1]):
            _fail('INVALID_RESULT')
        raw, amount = original_prepared_operand
        value = ctypes.create_string_buffer(raw, amount)
        if not _char_array_domain(value):
            _fail('INVALID_RESULT')
        if len(value) != amount:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _prepare_unicode_name_buffer(state, original_prepared_operand, /):
    try:
        _clock(state)
        if type(original_prepared_operand) is not str or len(original_prepared_operand) > 32766:
            _fail('INVALID_RESULT')
        value = ctypes.create_unicode_buffer(original_prepared_operand)
        if not _wchar_array_domain(value):
            _fail('INVALID_RESULT')
        if len(value) != len(original_prepared_operand) + 1:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _new_unicodestring(state, byte_length, maximum_length, exact_text_pointer, /):
    try:
        _clock(state)
        if type(byte_length) is not int or not 0 < byte_length <= 65532 or type(maximum_length) is not int or (maximum_length != byte_length + 2) or (type(exact_text_pointer) is not w.LPWSTR):
            _fail('INVALID_RESULT')
        value = _UnicodeString(byte_length, maximum_length, exact_text_pointer)
        if type(value) is not _UnicodeString:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _new_objectattributes(state, structure_size, known_parent_handle, exact_unicode_pointer, attributes_flag, borrowed_descriptor, security_qos, /):
    try:
        _clock(state)
        if structure_size != _OBJECT_ATTRIBUTES_SIZE or type(exact_unicode_pointer) is not _UNICODE_POINTER_TYPE or attributes_flag != 64 or (security_qos is not None):
            _fail('INVALID_RESULT')
        _handle_value(known_parent_handle)
        if borrowed_descriptor is not None:
            _uintptr(borrowed_descriptor, nonzero=True)
        value = _ObjectAttributes(structure_size, known_parent_handle, exact_unicode_pointer, attributes_flag, borrowed_descriptor, security_qos)
        if type(value) is not _ObjectAttributes:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _new_renameinfo(state, replace_false, known_directory_handle, encoded_length, fixed_name, /):
    try:
        _clock(state)
        if replace_false is not False or type(fixed_name) is not str or fixed_name not in (contract.RESERVATION_NAME, contract.ROOT_SEAL_NAME, contract.HEADER_NAME):
            _fail('INVALID_RESULT')
        _handle_value(known_directory_handle)
        if type(encoded_length) is not int or encoded_length != len(fixed_name) * 2:
            _fail('INVALID_RESULT')

        class _RenameInfo(ctypes.Structure):
            _fields_ = [('replace', w.BYTE), ('directory', w.HANDLE), ('length', w.DWORD), ('name', w.WCHAR * len(fixed_name))]
        value = _RenameInfo(False, known_directory_handle, encoded_length, fixed_name)
        if type(value) is not _RenameInfo:
            _fail('INVALID_RESULT')
        value._current_layout, value._fixed_name = (_RenameInfo, fixed_name)
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _rename_value(value):
    if type(value) is not value._current_layout or value._fixed_name not in (contract.RESERVATION_NAME, contract.ROOT_SEAL_NAME, contract.HEADER_NAME):
        _fail('INVALID_RESULT')

def _new_publishedfile(state, raw_sha256, byte_size, native_identity, path_identity, /):
    try:
        _clock(state)
        if type(raw_sha256) is not str or len(raw_sha256) != 64 or type(byte_size) is not int or (byte_size <= 0) or (type(native_identity) is not dict) or (type(path_identity) is not dict):
            _fail('INVALID_RESULT')
        value = PublishedFile(raw_sha256, byte_size, native_identity, path_identity)
        if type(value) is not PublishedFile:
            _fail('INVALID_RESULT')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _ct_supported_sizes(state, /):
    try:
        return (_ct_sizeof_supported_unicode_l180c13(state), _ct_sizeof_supported_attributes_l180c13(state), _ct_sizeof_supported_status_l180c13(state))
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _expand_supported(state, /):
    try:
        _clock(state)
        if _get_pinned_imported_os_module_name_original_ordinary_field_read(state, os) != 'nt':
            _fail('UNSUPPORTED')
        expected = (16, 48, 16) if _ct_sizeof_supported_pointer_width_l179c31(state) == 8 else (8, 24, 8)
        if _ct_supported_sizes(state) != expected:
            _fail('ABI_UNSUPPORTED')
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _token_user_probe(state, known_token, kind, buffer, requested, exact_length_out, /):
    try:
        _clock(state)
        value = _advapi.GetTokenInformation(known_token, kind, buffer, requested, exact_length_out)
        _signed32(value)
        state.probe_error = _probe_error_raw(state) if value == 0 else 0
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _token_user_fill(state, known_token, kind, buffer, requested, exact_length_out, /):
    try:
        _clock(state)
        value = _advapi.GetTokenInformation(known_token, kind, buffer, requested, exact_length_out)
        _signed32(value)
        if value == 0:
            raise _native_error(_failure_error_raw(state), 'token information')
        _clock(state)
        return value
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _owner_guard(state, owner):
    if type(owner) is not _IOOwner or owner.state is not state:
        _fail('INVALID_RESULT')
    if owner.slot.kind == 'handle':
        return _typed_scope_guard(state, _claim_handle_owner_receipt(state, owner))
    if owner.slot.kind == 'pointer':
        return _typed_scope_guard(state, _claim_pointer_owner_receipt(state, owner))
    _fail('INVALID_RESULT')

@contextmanager
def _expand_token_user(state, /):
    try:
        slot = _handle_slot(state)
        try:
            token, length = (_new_handle(state), _new_dword(state))
            process = _native_getcurrentprocess(state)
            token_address = _ct_byref_token_user_token_l233c48(state, token)
            _native_openprocesstoken(state, process, 8, token_address)
            slot.value, slot.phase = (_handle_value(_get_exact_w_handle_output_value_required_legal_success_known_owner_value_occurrence_before_adoption_post(state, token)), _OWNED)
            owner = _register_known_return(state, slot)
            with _owner_guard(state, owner):
                length_address = _ct_byref_token_user_length_l238c65(state, length)
                success = _token_user_probe(state, _get_exact_current_known_private_io_owner_fixed_closer_kind_value_original_ordinary_field_read(state, owner), 1, None, 0, length_address)
                if success or _uint32(state.probe_error) != 122 or (not _ct_sizeof_probe_pointer_minimum_l241c43(state) <= _get_exact_w_dword_output_value_probe_length_domain_after_240(state, length) <= 65536):
                    _fail('PRIVATE_OWNER_UNPROVEN')
                buffer = _prepare_token_buffer(state, _get_exact_w_dword_output_value_probe_length_buffer_size_reread_after_240(state, length))
                fill_address = _ct_byref_token_user_length_l245c74(state, length)
                _token_user_fill(state, _get_exact_current_known_private_io_owner_fixed_closer_kind_value_original_ordinary_field_read(state, owner), 1, buffer, len(buffer), fill_address)
                if not _ct_sizeof_fill_pointer_minimum_l249c15(state) <= _get_exact_w_dword_output_value_fill_length_later_reread_after_248(state, length) <= len(buffer):
                    _fail('PRIVATE_OWNER_UNPROVEN')
                view = _ct_cast_token_buffer_sid_pointer_l251c14(state, buffer)
                sid = _ct_get_token_buffer_sid_pointer_l251c14(state, view)
                base = _prepare_token_buffer_base(state, buffer)
                if not sid or not base <= sid < base + _get_exact_w_dword_output_value_fill_length_later_reread_after_248(state, length) or (not _native_isvalidsid(state, sid)):
                    _fail('PRIVATE_OWNER_UNPROVEN')
                size = _native_getlengthsid(state, sid)
                if not 8 <= size <= 68 or sid + size > base + _get_exact_w_dword_output_value_fill_length_later_reread_after_248(state, length):
                    _fail('PRIVATE_OWNER_UNPROVEN')
                yield sid
        except BaseException as primary:
            _remember_primary(state, primary)
            if state.first is primary:
                raise
            _raise_first(state)
        finally:
            _close_known_slot(state, slot)
            _raise_first(state)
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

@contextmanager
def _expand_system_sid(state, /):
    try:
        slot = _pointer_slot(state)
        try:
            sid = _new_lpvoid(state)
            address = _ct_byref_system_sid_sid_l266c54(state, sid)
            _native_convertstringsidtosidw(state, 'S-1-5-18', address)
            slot.value, slot.phase = (_uintptr(_get_exact_w_lpvoid_output_value_required_legal_success_known_owner_value_occurrence_before_adoption_post(state, sid), nonzero=True), _OWNED)
            owner = _register_known_return(state, slot)
            with _owner_guard(state, owner):
                if not _get_exact_w_lpvoid_output_value_original_ordinary_field_read(state, sid) or not _native_isvalidsid(state, sid):
                    _fail('PRIVATE_OWNER_UNPROVEN')
                yield _get_exact_w_lpvoid_output_value_original_ordinary_field_read(state, sid)
        except BaseException as primary:
            _remember_primary(state, primary)
            if state.first is primary:
                raise
            _raise_first(state)
        finally:
            _close_known_slot(state, slot)
            _raise_first(state)
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

@contextmanager
def _expand_private_descriptor(state, /):
    try:
        text_slot, descriptor_slot = (_pointer_slot(state), _pointer_slot(state))
        try:
            with _expand_token_user(state) as current, _expand_system_sid(state) as system:
                text = _new_lpwstr(state)
                address = _ct_byref_private_descriptor_text_l280c55(state, text)
                _native_convertsidtostringsidw(state, current, address)
                view = _ct_cast_sid_text_allocation_pointer_l283c43(state, text)
                text_slot.value, text_slot.phase = (_uintptr(_ct_get_sid_text_allocation_pointer_l283c43(state, view), nonzero=True), _OWNED)
                text_owner = _register_known_return(state, text_slot)
                with _owner_guard(state, text_owner):
                    sid = _get_exact_w_lpwstr_output_value_original_ordinary_field_read(state, text)
                    if type(sid) is not str or not 0 < len(sid) <= 256:
                        _fail('PRIVATE_OWNER_UNPROVEN')
                    aces = f'(A;;FA;;;{sid})' + ('' if _native_equalsid(state, current, system) else '(A;;FA;;;SY)')
                    descriptor = _new_lpvoid(state)
                    out = _ct_byref_private_descriptor_descriptor_l291c50(state, descriptor)
                    _native_convertstringsecuritydescriptortosecuritydescriptorw(state, 'O:' + sid + 'D:P' + aces, 1, out, None)
                    descriptor_slot.value, descriptor_slot.phase = (_uintptr(_get_exact_w_lpvoid_allocation_output_value_required_legal_success_known_owner_value_occurrence_before_ad(state, descriptor), nonzero=True), _OWNED)
                    descriptor_owner = _register_known_return(state, descriptor_slot)
                    with _owner_guard(state, descriptor_owner):
                        if not _get_exact_w_lpvoid_allocation_output_value_original_ordinary_field_read(state, descriptor) or not _native_isvalidsecuritydescriptor(state, descriptor):
                            _fail('PRIVATE_OWNER_UNPROVEN')
                        yield _get_exact_w_lpvoid_allocation_output_value_original_ordinary_field_read(state, descriptor)
        except BaseException as primary:
            _remember_primary(state, primary)
            if state.first is primary:
                raise
            _raise_first(state)
        finally:
            _close_known_slot(state, descriptor_slot)
            _close_known_slot(state, text_slot)
            _raise_first(state)
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _expand_security_check(state, handle, /):
    try:
        _budget_clock_only(state)
        descriptor_slot = _pointer_slot(state)
        try:
            with _expand_token_user(state) as current, _expand_system_sid(state) as system:
                owner, dacl, descriptor = (_new_lpvoid(state), _new_lpvoid(state), _new_lpvoid(state))
                owner_out = _ct_byref_security_check_owner_l305c55(state, owner)
                dacl_out = _ct_byref_security_check_dacl_l306c39(state, dacl)
                descriptor_out = _ct_byref_security_check_descriptor_l306c65(state, descriptor)
                _native_getsecurityinfo(state, handle, 1, 5, owner_out, None, dacl_out, None, descriptor_out)
                descriptor_slot.value, descriptor_slot.phase = (_uintptr(_get_exact_w_lpvoid_allocation_output_value_required_legal_success_known_owner_value_occurrence_before_ad(state, descriptor), nonzero=True), _OWNED)
                allocation = _register_known_return(state, descriptor_slot)
                with _owner_guard(state, allocation):
                    control, revision = (_new_word(state), _new_dword(state))
                    if not _get_exact_w_lpvoid_allocation_output_value_original_ordinary_field_read(state, descriptor) or not _get_exact_w_lpvoid_borrowed_owner_output_value_original_ordinary_field_read(state, owner) or (not _get_exact_w_lpvoid_borrowed_dacl_output_value_original_ordinary_field_read(state, dacl)) or (not _native_isvalidsecuritydescriptor(state, descriptor)) or (not _native_isvalidsid(state, owner)) or (not _native_equalsid(state, owner, current)):
                        _fail('PRIVATE_OWNER_UNPROVEN')
                    control_out = _ct_byref_security_check_control_l318c68(state, control)
                    revision_out = _ct_byref_security_check_revision_l318c91(state, revision)
                    _native_getsecuritydescriptorcontrol(state, descriptor, control_out, revision_out)
                    if not _get_exact_w_word_output_value_original_ordinary_field_read(state, control) & 4096:
                        _fail('PRIVATE_OWNER_UNPROVEN')
                    size = _new_aclsize(state)
                    size_out = _ct_byref_security_check_size_l326c51(state, size)
                    size_bytes = _ct_sizeof_acl_instance_l326c71(state, size)
                    _native_getaclinformation(state, dacl, size_out, size_bytes, 2)
                    same = bool(_native_equalsid(state, current, system))
                    expected = {'current'} if same else {'current', 'system'}
                    if _get_exact_aclsize_output_count_original_ordinary_field_read(state, size) != len(expected) or not 8 <= _get_exact_aclsize_output_used_original_ordinary_field_read(state, size) <= 65536:
                        _fail('PRIVATE_OWNER_UNPROVEN')
                    seen = set()
                    for index in range(_get_exact_aclsize_output_count_original_ordinary_field_read(state, size)):
                        _budget_clock_only(state)
                        pointer = _new_lpvoid(state)
                        pointer_out = _ct_byref_security_check_pointer_l338c51(state, pointer)
                        _native_getace(state, dacl, index, pointer_out)
                        if not _get_exact_w_lpvoid_borrowed_ace_output_value_original_ordinary_field_read(state, pointer):
                            _fail('PRIVATE_OWNER_UNPROVEN')
                        view = _ct_cast_borrowed_ace_structure_pointer_l344c22(state, pointer)
                        ace = _ct_get_borrowed_ace_structure_pointer_l344c22(state, view)
                        sid = _get_exact_w_lpvoid_borrowed_ace_output_value_original_ordinary_field_read(state, pointer) + _AllowedAce.sid_start.offset
                        if _get_exact_aceheader_borrowed_field_of_known_allowedace_kind_original_ordinary_field_read(state, _get_exact_allowedace_borrowed_view_header_original_ordinary_field_read(state, ace)) != 0 or _get_exact_aceheader_borrowed_field_of_known_allowedace_flags_original_ordinary_field_read(state, _get_exact_allowedace_borrowed_view_header_original_ordinary_field_read(state, ace)) != 0 or (not 16 <= _get_exact_aceheader_borrowed_field_of_known_allowedace_size_original_ordinary_field_read(state, _get_exact_allowedace_borrowed_view_header_original_ordinary_field_read(state, ace)) <= 80) or (_get_exact_allowedace_borrowed_view_mask_original_ordinary_field_read(state, ace) != 2032127) or (not _native_isvalidsid(state, sid)):
                            _fail('PRIVATE_OWNER_UNPROVEN')
                        sid_size = _native_getlengthsid(state, sid)
                        if not 8 <= sid_size <= 68 or _AllowedAce.sid_start.offset + sid_size != _get_exact_aceheader_borrowed_field_of_known_allowedace_size_original_ordinary_field_read(state, _get_exact_allowedace_borrowed_view_header_original_ordinary_field_read(state, ace)):
                            _fail('PRIVATE_OWNER_UNPROVEN')
                        role = 'current' if _native_equalsid(state, sid, current) else 'system' if _native_equalsid(state, sid, system) else None
                        if role not in expected or role in seen:
                            _fail('PRIVATE_OWNER_UNPROVEN')
                        _set_add_seen(state, seen, role)
                    if seen != expected:
                        _fail('PRIVATE_OWNER_UNPROVEN')
            _budget_clock_only(state)
        except BaseException as primary:
            _remember_primary(state, primary)
            if state.first is primary:
                raise
            _raise_first(state)
        finally:
            _close_known_slot(state, descriptor_slot)
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _expand_info(state, handle, /, *, directory):
    try:
        value = _new_handleinfo(state)
        address = _ct_byref_info_value_l365c54(state, value)
        _native_getfileinformationbyhandle(state, handle, address)
        if _get_exact_handleinfo_output_attributes_original_ordinary_field_read(state, value) & 1024 or bool(_get_exact_handleinfo_output_attributes_original_ordinary_field_read(state, value) & 16) != directory:
            _fail('OBJECT_UNSAFE')
        if not directory and _get_exact_handleinfo_output_links_original_ordinary_field_read(state, value) != 1:
            _fail('OBJECT_UNSAFE')
        native = {'volume_serial': str(_get_exact_handleinfo_output_volume_original_ordinary_field_read(state, value)), 'file_index': str(_get_exact_handleinfo_output_index_high_original_ordinary_field_read(state, value) << 32 | _get_exact_handleinfo_output_index_low_original_ordinary_field_read(state, value))}
        stamp = (_get_exact_handleinfo_output_attributes_original_ordinary_field_read(state, value), _get_exact_handleinfo_output_volume_original_ordinary_field_read(state, value), _get_exact_handleinfo_output_index_high_original_ordinary_field_read(state, value), _get_exact_handleinfo_output_index_low_original_ordinary_field_read(state, value), _get_exact_handleinfo_output_size_high_original_ordinary_field_read(state, value), _get_exact_handleinfo_output_size_low_original_ordinary_field_read(state, value), _get_exact_handleinfo_output_links_original_ordinary_field_read(state, value), _get_exact_filetime_borrowed_field_of_fresh_handleinfo_high_original_ordinary_field_read(state, _get_exact_handleinfo_output_created_original_ordinary_field_read(state, value)), _get_exact_filetime_borrowed_field_of_fresh_handleinfo_low_original_ordinary_field_read(state, _get_exact_handleinfo_output_created_original_ordinary_field_read(state, value)), _get_exact_filetime_borrowed_field_of_fresh_handleinfo_high_original_ordinary_field_read(state, _get_exact_handleinfo_output_written_original_ordinary_field_read(state, value)), _get_exact_filetime_borrowed_field_of_fresh_handleinfo_low_original_ordinary_field_read(state, _get_exact_handleinfo_output_written_original_ordinary_field_read(state, value)))
        return (native, _get_exact_handleinfo_output_size_high_original_ordinary_field_read(state, value) << 32 | _get_exact_handleinfo_output_size_low_original_ordinary_field_read(state, value), stamp)
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _expand_open_parent_raw(state, path, /, *, write):
    try:
        slot = _handle_slot(state)
        try:
            native = _native_absolute(state, str(path))
            value = _native_createfilew(state, native, _DIR_CREATE if write else _DIR_READ, 3, None, 3, 35651584, None)
            slot.value, slot.phase = (value, _OWNED)
            return _register_known_return(state, slot)
        except BaseException as primary:
            _remember_primary(state, primary)
            _close_known_slot(state, slot)
            if state.first is primary:
                raise
            _raise_first(state)
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _expand_relative_open(state, parent, name, /, *, directory, create, descriptor, reader_share):
    try:
        slot = _handle_slot(state)
        try:
            _entry_name(state, name)
            encoded = _name_utf16(state, name)
            text = _prepare_unicode_name_buffer(state, name)
            pointer = _ct_cast_relative_unicode_buffer_pointer_l396c61(state, text)
            unicode = _new_unicodestring(state, len(encoded), len(encoded) + 2, pointer)
            size = _ct_sizeof_object_attributes_type_l397c35(state)
            unicode_pointer = _ct_pointer_relative_unicode_l398c34(state, unicode)
            attributes = _new_objectattributes(state, size, parent, unicode_pointer, 64, descriptor, None)
            handle, status = (_new_handle(state), _new_iostatus(state))
            access = (_DIR_CREATE if create else _DIR_READ) if directory else _FILE_CREATE if create else _FILE_READ
            handle_out = _ct_byref_relative_open_handle_l402c33(state, handle)
            attributes_out = _ct_byref_relative_open_attributes_l402c63(state, attributes)
            status_out = _ct_byref_relative_open_status_l402c89(state, status)
            result = _native_ntcreatefile(state, handle_out, access, attributes_out, status_out, None, 16 if directory else 128, 3 if directory else 1 if create else reader_share, 2 if create else 1, (1 if directory else 64) | 32 | 2097152, None, 0)
            if result != 0 or _get_exact_iostatus_output_status_required_legal_success_known_owner_value_occurrence_before_adoption_pos(state, status) != 0:
                slot.phase = _UNKNOWN
                _fail('CREATE_UNAVAILABLE' if create else 'OPEN_UNAVAILABLE')
            if _get_exact_w_handle_output_value_required_legal_success_known_owner_value_occurrence_before_adoption_post(state, handle) is None or _get_exact_w_handle_output_value_required_legal_success_known_owner_value_occurrence_before_adoption_post(state, handle) == _INVALID_HANDLE:
                slot.phase = _UNKNOWN
                _fail('HANDLE_UNCONFIRMED')
            slot.value, slot.phase = (_handle_value(_get_exact_w_handle_output_value_required_legal_success_known_owner_value_occurrence_before_adoption_post(state, handle)), _OWNED)
            owner = _register_known_return(state, slot)
            if _get_exact_iostatus_output_information_original_ordinary_field_read(state, status) != (2 if create else 1):
                _fail('COMPLETION_UNCONFIRMED')
            return owner
        except BaseException as primary:
            _remember_primary(state, primary)
            _close_known_slot(state, slot)
            if state.first is primary:
                raise
            _raise_first(state)
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

class _IODirectory:
    __slots__ = ('state', 'path', 'owner', 'budget', 'parent', 'name', 'native_identity', 'path_identity')

    def __init__(self, state, path, owner, parent, name):
        self.state, self.path, self.owner = (state, _path(state, path), owner)
        self.budget, self.parent, self.name = (state.restricted, parent, name)
        self.native_identity = _expand_info(state, _get_exact_current_known_private_io_owner_fixed_closer_kind_value_original_ordinary_field_read(state, owner), directory=True)[0]
        value = _path_stat(state, self.path)
        _safe_stat(state, value, True)
        self.path_identity = {'device': str(_get_stat_st_dev(state, value)), 'inode': str(_get_stat_st_ino(state, value))}
        _iodirectory_verify(state, self)

    @property
    def handle(self):
        if _get_exact_current_known_private_io_owner_fixed_closer_kind_retired_original_ordinary_field_read(self.state, self.owner):
            failure = _get_exact_current_known_private_io_owner_fixed_closer_kind_failure_original_ordinary_field_read(self.state, self.owner)
            if failure is not None:
                raise failure
            _fail('OWNER_RETIRED')
        return _get_exact_current_known_private_io_owner_fixed_closer_kind_value_original_ordinary_field_read(self.state, self.owner)

    def verify(self):
        return _iodirectory_verify(self.state, self)

def _new_directory(state, path, owner, parent, name, /):
    try:
        if type(owner) is not _IOOwner or owner.state is not state or owner.slot.phase != _OWNED:
            _fail('INVALID_RESULT')
        return _IODirectory(state, path, owner, parent, name)
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _iodirectory_verify(state, exact_receiver, /):
    try:
        _budget_clock_only(state)
        if type(exact_receiver) is not _IODirectory or exact_receiver.state is not state:
            _fail('INVALID_RESULT')
        directory = exact_receiver
        if _get_exact_current_private_io_directory_with_same_b_t_parent_original_ordinary_field_read(state, directory) is not None:
            _iodirectory_verify(state, _get_exact_current_private_io_directory_with_same_b_t_parent_original_ordinary_field_read(state, directory))
        native = _expand_info(state, _get_exact_current_private_io_directory_with_same_b_t_handle_original_ordinary_field_read(state, directory), directory=True)[0]
        value = _path_stat(state, _get_exact_current_private_io_directory_with_same_b_t_path_original_ordinary_field_read(state, directory))
        _safe_stat(state, value, True)
        path = {'device': str(_get_stat_st_dev(state, value)), 'inode': str(_get_stat_st_ino(state, value))}
        if native != _get_exact_current_private_io_directory_with_same_b_t_native_identity_original_ordinary_field_read(state, directory) or path != _get_exact_current_private_io_directory_with_same_b_t_path_identity_original_ordinary_field_read(state, directory):
            _fail('ROOT_IDENTITY_MISMATCH')
        if _get_exact_current_private_io_directory_with_same_b_t_parent_original_ordinary_field_read(state, directory) is None:
            other = _expand_open_parent_raw(state, _get_exact_current_private_io_directory_with_same_b_t_path_original_ordinary_field_read(state, directory), write=False)
        else:
            parent = _get_exact_current_private_io_directory_with_same_b_t_parent_original_ordinary_field_read(state, directory)
            other = _expand_relative_open(state, _get_exact_current_private_io_directory_with_same_b_t_handle_original_ordinary_field_read(state, parent), _get_exact_current_private_io_directory_with_same_b_t_name_original_ordinary_field_read(state, directory), directory=True, create=False, descriptor=None, reader_share=1)
        try:
            with _owner_guard(state, other):
                if _expand_info(state, _get_exact_current_known_private_io_owner_fixed_closer_kind_value_original_ordinary_field_read(state, other), directory=True)[0] != _get_exact_current_private_io_directory_with_same_b_t_native_identity_original_ordinary_field_read(state, directory):
                    _fail('ROOT_IDENTITY_MISMATCH')
        finally:
            _close_known_slot(state, other.slot)
        _budget_clock_only(state)
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _expand_check(state, profile, directory, /):
    try:
        _expand_supported(state)
        _budget_clock_only(state)
        profile = _profile(state, profile)
        _guard(state, profile)
        if type(directory) is not _IODirectory or directory.state is not state or _get_exact_current_private_io_directory_with_same_b_t_budget_original_ordinary_field_read(state, directory) is not state.restricted:
            _fail('INVALID_ARGUMENT')
        parent = _get_exact_path_exact_profile_database_parent_original_ordinary_field_read(state, _get_exact_storageprofile_already_validated_database_original_ordinary_field_read(state, profile))
        if _get_exact_current_private_io_directory_with_same_b_t_path_original_ordinary_field_read(state, directory) not in (parent, parent / contract.ROOT_NAME):
            _fail('PROFILE_CHANGED')
        _iodirectory_verify(state, directory)
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _same_profile(state, profile, paths, guard):
    if tuple(_get_exact_storageprofile_already_validated_identity_original_ordinary_field_read(state, profile)) != paths or _guard(state, profile) is not guard:
        _fail('PROFILE_CHANGED')

class _IOContext:
    __slots__ = ('state', 'generator', 'kind')

    def __enter__(self):
        if type(self) is not _IOContext:
            _fail('INVALID_RESULT')
        return _GeneratorContextManager.__enter__(self.generator)

    def __exit__(self, kind, primary, tb):
        return _GeneratorContextManager.__exit__(self.generator, kind, primary, tb)

def _issue_parent_context(state, profile, /, *, write):
    _clock(state)
    if type(write) is not bool:
        _fail('INVALID_ARGUMENT')
    receipt = _Receipt(state)
    receiver = object.__new__(_IOContext)
    receipt.kind, receiver.kind = ('IO_PARENT', 'IO_PARENT')
    receiver.state, receiver.generator = (state, _parent_generator(state, profile, write=write))
    receipt.context = receiver
    _prepare_receipt(state, receipt)
    _clock(state)
    return receiver

def _issue_root_create_context(state, profile, exact_parent, /):
    _clock(state)
    receipt = _Receipt(state)
    receiver = object.__new__(_IOContext)
    receipt.kind, receiver.kind = ('IO_ROOT_CREATE', 'IO_ROOT_CREATE')
    receiver.state, receiver.generator = (state, _root_create_generator(state, profile, exact_parent))
    receipt.context = receiver
    _prepare_receipt(state, receipt)
    _clock(state)
    return receiver

def _issue_root_open_context(state, profile, exact_parent, /):
    _clock(state)
    receipt = _Receipt(state)
    receiver = object.__new__(_IOContext)
    receipt.kind, receiver.kind = ('IO_ROOT_OPEN', 'IO_ROOT_OPEN')
    receiver.state, receiver.generator = (state, _root_open_generator(state, profile, exact_parent))
    receipt.context = receiver
    _prepare_receipt(state, receipt)
    _clock(state)
    return receiver

def _issue_held_context(state, profile, exact_directory, name, maximum, /, *, creator_live):
    _clock(state)
    if type(creator_live) is not bool:
        _fail('INVALID_ARGUMENT')
    receipt = _Receipt(state)
    receiver = object.__new__(_IOContext)
    receipt.kind, receiver.kind = ('IO_HELD', 'IO_HELD')
    receiver.state = state
    receiver.generator = _held_generator(state, profile, exact_directory, name, maximum, creator_live=creator_live)
    receipt.context = receiver
    _prepare_receipt(state, receipt)
    _clock(state)
    return receiver

@contextmanager
def _parent_generator(state, profile, *, write):
    _expand_supported(state)
    _budget_clock_only(state)
    profile = _profile(state, profile)
    paths = tuple(_get_exact_storageprofile_already_validated_identity_original_ordinary_field_read(state, profile))
    guard = _guard(state, profile)
    owner = _expand_open_parent_raw(state, _get_exact_path_exact_profile_database_parent_original_ordinary_field_read(state, _get_exact_storageprofile_already_validated_database_original_ordinary_field_read(state, profile)), write=write)
    try:
        with _owner_guard(state, owner):
            parent = _new_directory(state, _get_exact_path_exact_profile_database_parent_original_ordinary_field_read(state, _get_exact_storageprofile_already_validated_database_original_ordinary_field_read(state, profile)), owner, None, None)
            yield parent
            _iodirectory_verify(state, parent)
            _same_profile(state, profile, paths, guard)
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)
    finally:
        _close_known_slot(state, owner.slot)
    _budget_clock_only(state)

@contextmanager
def _root_create_generator(state, profile, parent):
    _expand_check(state, profile, parent)
    if _get_exact_current_private_io_directory_with_same_b_t_path_original_ordinary_field_read(state, parent) != _get_exact_path_exact_profile_database_parent_original_ordinary_field_read(state, _get_exact_storageprofile_already_validated_database_original_ordinary_field_read(state, profile)):
        _fail('PROFILE_CHANGED')
    with _expand_private_descriptor(state) as descriptor:
        owner = _expand_relative_open(state, _get_exact_current_private_io_directory_with_same_b_t_handle_original_ordinary_field_read(state, parent), contract.ROOT_NAME, directory=True, create=True, descriptor=descriptor, reader_share=1)
        try:
            with _owner_guard(state, owner):
                root = _new_directory(state, _get_exact_current_private_io_directory_with_same_b_t_path_original_ordinary_field_read(state, parent) / contract.ROOT_NAME, owner, parent, contract.ROOT_NAME)
                _expand_security_check(state, _get_exact_current_private_io_directory_with_same_b_t_handle_original_ordinary_field_read(state, root))
                yield root
                _iodirectory_verify(state, root)
                _expand_security_check(state, _get_exact_current_private_io_directory_with_same_b_t_handle_original_ordinary_field_read(state, root))
        except BaseException as primary:
            _remember_primary(state, primary)
            if state.first is primary:
                raise
            _raise_first(state)
        finally:
            _close_known_slot(state, owner.slot)
    _budget_clock_only(state)

@contextmanager
def _root_open_generator(state, profile, parent):
    _expand_check(state, profile, parent)
    if _get_exact_current_private_io_directory_with_same_b_t_path_original_ordinary_field_read(state, parent) != _get_exact_path_exact_profile_database_parent_original_ordinary_field_read(state, _get_exact_storageprofile_already_validated_database_original_ordinary_field_read(state, profile)):
        _fail('PROFILE_CHANGED')
    owner = _expand_relative_open(state, _get_exact_current_private_io_directory_with_same_b_t_handle_original_ordinary_field_read(state, parent), contract.ROOT_NAME, directory=True, create=False, descriptor=None, reader_share=1)
    try:
        with _owner_guard(state, owner):
            root = _new_directory(state, _get_exact_current_private_io_directory_with_same_b_t_path_original_ordinary_field_read(state, parent) / contract.ROOT_NAME, owner, parent, contract.ROOT_NAME)
            _expand_security_check(state, _get_exact_current_private_io_directory_with_same_b_t_handle_original_ordinary_field_read(state, root))
            yield root
            _iodirectory_verify(state, root)
            _expand_security_check(state, _get_exact_current_private_io_directory_with_same_b_t_handle_original_ordinary_field_read(state, root))
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)
    finally:
        _close_known_slot(state, owner.slot)
    _budget_clock_only(state)

def _expand_read_chunk(state, handle, requested, /):
    try:
        amount = _budget_request(state, min(requested, CHUNK_BYTES))
        buffer, count = (_prepare_read_buffer(state, amount), _new_dword(state))
        output = _ct_byref_read_chunk_count_l517c52(state, count)
        _native_readfile(state, handle, buffer, amount, output, None)
        if not 0 <= _get_exact_w_dword_actual_read_count_value_read_count_g1_validate(state, count) <= amount:
            _fail('READ_INVALID')
        _budget_charge(state, _get_exact_w_dword_actual_read_count_value_read_count_g2_charge_operand(state, count))
        return _get_exact_current_read_ctypes_buffer_raw_original_ordinary_field_read(state, buffer)[:_get_exact_w_dword_actual_read_count_value_read_count_g3_return_reread(state, count)]
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _expand_write_chunk(state, handle, raw, /):
    try:
        if type(raw) is not bytes:
            _fail('INVALID_ARGUMENT')
        amount = _budget_request(state, min(len(raw), CHUNK_BYTES))
        buffer, count = (_prepare_write_buffer(state, (raw[:amount], amount)), _new_dword(state))
        output = _ct_byref_write_chunk_count_l529c53(state, count)
        _native_writefile(state, handle, buffer, amount, output, None)
        if not 0 < _get_exact_w_dword_actual_write_count_value_write_count_g1_validate(state, count) <= amount:
            _fail('WRITE_INVALID')
        _budget_charge(state, _get_exact_w_dword_actual_write_count_value_write_count_g2_charge_operand(state, count))
        return _get_exact_w_dword_actual_write_count_value_write_count_g3_return_reread(state, count)
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _expand_rewind(state, handle, /):
    try:
        _native_setfilepointerex(state, handle, 0, None, 0)
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _expand_read_exact(state, handle, byte_size, maximum, /):
    try:
        if type(byte_size) is not int or not 0 < byte_size <= maximum:
            _fail('LIMIT')
        _expand_rewind(state, handle)
        parts, total = ([], 0)
        while total < byte_size:
            chunk = _expand_read_chunk(state, handle, byte_size - total)
            if not chunk:
                _fail('READ_INCOMPLETE')
            _append_bytes(state, parts, chunk)
            total += len(chunk)
        if _expand_read_chunk(state, handle, 1) != b'':
            _fail('READBACK_CHANGED')
        _budget_clock_only(state)
        return _join_bytes(state, parts)
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

class _IOHeldControl:
    __slots__ = ('state', 'directory', 'name', 'owner', 'maximum', 'budget', 'path', 'native_identity', 'byte_size', 'stamp', 'path_identity', 'raw', 'raw_sha256')

    def __init__(self, state, directory, name, owner, maximum):
        self.state, self.directory, self.name, self.owner = (state, directory, name, owner)
        self.maximum, self.budget = (maximum, state.restricted)
        self.path = _get_exact_current_private_io_directory_with_same_b_t_path_original_ordinary_field_read(state, directory) / name
        self.native_identity, self.byte_size, self.stamp = _expand_info(state, _get_exact_current_known_private_io_owner_fixed_closer_kind_value_original_ordinary_field_read(state, owner), directory=False)
        self.path_identity = _stat_file_identity(state, _path_stat(state, self.path))
        if self.path_identity['byte_size'] != self.byte_size:
            _fail('READBACK_CHANGED')
        self.raw = _expand_read_exact(state, _get_exact_current_known_private_io_owner_fixed_closer_kind_value_original_ordinary_field_read(state, owner), self.byte_size, maximum)
        self.raw_sha256 = _hash_hex(state, _new_sha256(state, self.raw))
        _ioheldcontrol_verify(state, self)

    def verify(self):
        return _ioheldcontrol_verify(self.state, self)

    def bytes(self):
        return _ioheldcontrol_bytes(self.state, self)

def _new_heldcontrol(state, directory, name, owner, maximum, /):
    try:
        if type(directory) is not _IODirectory or directory.state is not state or type(owner) is not _IOOwner or (owner.state is not state):
            _fail('INVALID_RESULT')
        return _IOHeldControl(state, directory, name, owner, maximum)
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _ioheldcontrol_verify(state, exact_receiver, /):
    try:
        _budget_clock_only(state)
        if type(exact_receiver) is not _IOHeldControl or exact_receiver.state is not state:
            _fail('INVALID_RESULT')
        held = exact_receiver
        _iodirectory_verify(state, _get_exact_current_private_io_held_control_with_same_b_t_directory_original_ordinary_field_read(state, held))
        owner = _get_exact_current_private_io_held_control_with_same_b_t_owner_original_ordinary_field_read(state, held)
        if _get_exact_current_known_private_io_owner_fixed_closer_kind_retired_original_ordinary_field_read(state, owner):
            failure = _get_exact_current_known_private_io_owner_fixed_closer_kind_failure_original_ordinary_field_read(state, owner)
            if failure is not None:
                raise failure
            _fail('OWNER_RETIRED')
        native, size, stamp = _expand_info(state, _get_exact_current_known_private_io_owner_fixed_closer_kind_value_original_ordinary_field_read(state, owner), directory=False)
        if native != _get_exact_current_private_io_held_control_with_same_b_t_native_identity_original_ordinary_field_read(state, held) or size != _get_exact_current_private_io_held_control_with_same_b_t_byte_size_original_ordinary_field_read(state, held) or stamp != _get_exact_current_private_io_held_control_with_same_b_t_stamp_original_ordinary_field_read(state, held) or (_stat_file_identity(state, _path_stat(state, _get_exact_current_private_io_held_control_with_same_b_t_path_original_ordinary_field_read(state, held))) != _get_exact_current_private_io_held_control_with_same_b_t_path_identity_original_ordinary_field_read(state, held)):
            _fail('READBACK_CHANGED')
        _budget_clock_only(state)
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _ioheldcontrol_bytes(state, exact_receiver, /):
    try:
        _ioheldcontrol_verify(state, exact_receiver)
        owner = _get_exact_current_private_io_held_control_with_same_b_t_owner_original_ordinary_field_read(state, exact_receiver)
        raw = _expand_read_exact(state, _get_exact_current_known_private_io_owner_fixed_closer_kind_value_original_ordinary_field_read(state, owner), _get_exact_current_private_io_held_control_with_same_b_t_byte_size_original_ordinary_field_read(state, exact_receiver), _get_exact_current_private_io_held_control_with_same_b_t_maximum_original_ordinary_field_read(state, exact_receiver))
        if raw != _get_exact_current_private_io_held_control_with_same_b_t_raw_original_ordinary_field_read(state, exact_receiver):
            _fail('READBACK_CHANGED')
        _ioheldcontrol_verify(state, exact_receiver)
        return raw
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

@contextmanager
def _held_generator(state, profile, directory, name, maximum, *, creator_live):
    _expand_check(state, profile, directory)
    _entry_name(state, name)
    if type(maximum) is not int or not 0 < maximum <= 64 * 1024:
        _fail('INVALID_ARGUMENT')
    owner = _expand_relative_open(state, _get_exact_current_private_io_directory_with_same_b_t_handle_original_ordinary_field_read(state, directory), name, directory=False, create=False, descriptor=None, reader_share=7 if creator_live else 1)
    try:
        with _owner_guard(state, owner):
            held = _new_heldcontrol(state, directory, name, owner, maximum)
            yield held
            _ioheldcontrol_bytes(state, held)
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)
    finally:
        _close_known_slot(state, owner.slot)
    _budget_clock_only(state)

def _expand_read_control_locked(state, profile, directory, name, maximum, /, *, creator_live):
    try:
        return _issue_held_context(state, profile, directory, name, maximum, creator_live=creator_live)
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def _expand_publish_owned(state, directory, owner, name, /):
    try:
        _budget_clock_only(state)
        encoded = _name_utf16(state, name)
        value = _new_renameinfo(state, False, _get_exact_current_private_io_directory_with_same_b_t_handle_original_ordinary_field_read(state, directory), len(encoded), name)
        status = _new_iostatus(state)
        status_out = _ct_byref_publish_owned_status_l627c54(state, status)
        value_out = _ct_byref_publish_owned_value_l627c76(state, value)
        size = _ct_sizeof_current_rename_instance_l627c97(state, value)
        result = _native_ntsetinformationfile(state, _get_exact_current_known_private_io_owner_fixed_closer_kind_value_original_ordinary_field_read(state, owner), status_out, value_out, size, 10)
        if result != 0 or _get_exact_iostatus_output_status_existing_creator_known_raw_nt_completion_before_original630_clock(state, status) != 0:
            _fail('PUBLISH_UNAVAILABLE')
        _budget_clock_only(state)
    except BaseException as primary:
        _remember_primary(state, primary)
        if state.first is primary:
            raise
        _raise_first(state)

def publish_new_locked(profile, directory, name, stage_name, raw, state):
    _expand_check(state, profile, directory)
    if type(raw) is not bytes or not 0 < len(raw) <= contract.MAX_RECORD_BYTES:
        _fail('INVALID_ARGUMENT')
    decoded = _record_decode(state, raw)
    expected_name = {'namespace_reservation': contract.RESERVATION_NAME, 'namespace_root_seal': contract.ROOT_SEAL_NAME, 'namespace_header': contract.HEADER_NAME}[decoded['kind']]
    if name != expected_name or stage_name != _stage_name(state, name, decoded['init_operation_id']) or (name == contract.ROOT_SEAL_NAME) != (_get_exact_current_private_io_directory_with_same_b_t_path_original_ordinary_field_read(state, directory) == _get_exact_path_exact_profile_database_parent_original_ordinary_field_read(state, _get_exact_storageprofile_already_validated_database_original_ordinary_field_read(state, profile)) / contract.ROOT_NAME):
        _fail('INVALID_ARGUMENT')
    with _expand_private_descriptor(state) as descriptor:
        owner = _expand_relative_open(state, _get_exact_current_private_io_directory_with_same_b_t_handle_original_ordinary_field_read(state, directory), stage_name, directory=False, create=True, descriptor=descriptor, reader_share=1)
        try:
            with _owner_guard(state, owner):
                original = _expand_info(state, _get_exact_current_known_private_io_owner_fixed_closer_kind_value_original_ordinary_field_read(state, owner), directory=False)
                if original[1] != 0:
                    _fail('STAGE_CREATE_FAILED')
                offset = 0
                while offset < len(raw):
                    offset += _expand_write_chunk(state, _get_exact_current_known_private_io_owner_fixed_closer_kind_value_original_ordinary_field_read(state, owner), raw[offset:offset + CHUNK_BYTES])
                _native_flushfilebuffers(state, _get_exact_current_known_private_io_owner_fixed_closer_kind_value_original_ordinary_field_read(state, owner))
                if _expand_read_exact(state, _get_exact_current_known_private_io_owner_fixed_closer_kind_value_original_ordinary_field_read(state, owner), len(raw), contract.MAX_RECORD_BYTES) != raw:
                    _fail('READBACK_CHANGED')
                written = _expand_info(state, _get_exact_current_known_private_io_owner_fixed_closer_kind_value_original_ordinary_field_read(state, owner), directory=False)
                if written[0] != original[0] or written[1] != len(raw):
                    _fail('READBACK_CHANGED')
                _iodirectory_verify(state, directory)
                _expand_publish_owned(state, directory, owner, name)
                if _expand_read_exact(state, _get_exact_current_known_private_io_owner_fixed_closer_kind_value_original_ordinary_field_read(state, owner), len(raw), contract.MAX_RECORD_BYTES) != raw:
                    _fail('READBACK_CHANGED')
                context = _issue_held_context(state, profile, directory, name, contract.MAX_RECORD_BYTES, creator_live=True)
                with _typed_scope_guard(state, _claim_held_receipt(state, context)) as held:
                    if _get_exact_current_private_io_held_control_with_same_b_t_native_identity_original_ordinary_field_read(state, held) != original[0] or _get_exact_current_private_io_held_control_with_same_b_t_raw_original_ordinary_field_read(state, held) != raw:
                        _fail('READBACK_CHANGED')
                published_native = _expand_info(state, _get_exact_current_known_private_io_owner_fixed_closer_kind_value_original_ordinary_field_read(state, owner), directory=False)[0]
        except BaseException as primary:
            _remember_primary(state, primary)
            if state.first is primary:
                raise
            _raise_first(state)
        finally:
            _close_known_slot(state, owner.slot)
    context = _issue_held_context(state, profile, directory, name, contract.MAX_RECORD_BYTES, creator_live=False)
    with _typed_scope_guard(state, _claim_held_receipt(state, context)) as held:
        if _get_exact_current_private_io_held_control_with_same_b_t_native_identity_original_ordinary_field_read(state, held) != published_native or _get_exact_current_private_io_held_control_with_same_b_t_raw_original_ordinary_field_read(state, held) != raw:
            _fail('READBACK_CHANGED')
        result = _new_publishedfile(state, _get_exact_current_private_io_held_control_known_lifetime_raw_sha256_original_ordinary_field_read(state, held), _get_exact_current_private_io_held_control_with_same_b_t_byte_size_original_ordinary_field_read(state, held), _copy_dict(state, _get_exact_current_private_io_held_control_with_same_b_t_native_identity_original_ordinary_field_read(state, held)), _copy_dict(state, _get_exact_current_private_io_held_control_with_same_b_t_path_identity_original_ordinary_field_read(state, held)))
    _iodirectory_verify(state, directory)
    _budget_clock_only(state)
    return result

def open_parent_locked(profile, state, *, write=False):
    return _issue_parent_context(state, profile, write=write)

def create_root_locked(profile, parent, state):
    return _issue_root_create_context(state, profile, parent)

def open_root_locked(profile, parent, state):
    return _issue_root_open_context(state, profile, parent)

def read_control_locked(profile, directory, name, state, maximum, *, creator_live=False):
    return _issue_held_context(state, profile, directory, name, maximum, creator_live=creator_live)
