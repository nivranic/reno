"""Registered initial k0 Store directories and selections; no write grant.

Directory exit never finishes or resets its owning Store Stage. Original
proof rows survive business close until aggregate metadata retirement.
"""
from __future__ import annotations

import weakref

from . import personal_authority_enrollment_store_runtime as runtime
from . import personal_authority_enrollment_store_selections as content
from . import personal_authority_enrollment_store_custody as custody
from . import personal_authority_namespace3_io as native
from . import personal_authority_namespace3_contract as contract
from . import personal_authority_enrollment_store as store
from . import storage_lifecycle as lifecycle
from . import personal_authority_enrollment_store_census_native as census_native
from . import personal_authority_enrollment_store_io as file_io

_SEAL = object()
_DIRECTORY_ATTEMPTS = {}
_DIRECTORY_SCOPES = {}
_DIRECTORY_CLEANUP_ROWS = {}
_DIRECTORY_ORIGINAL_ROWS = {}
_DIRECTORY_RETIREMENT_ROWS = {}
_FILE_SELECTIONS = {}
_CENSUS_ROWS = {}
_FINAL_GENERATION_ATTEMPTS = {}
_FINAL_GENERATION_ORIGINALS = {}
_FINAL_GENERATION_STATES = {}
_FINAL_CENSUS_ROWS = {}
_CALLBACK_PINS = {}
_PIN_NAMES = ('_continuation', '_plan', '_stage', '_journal', '_budget', '_profile', '_paths', '_guard')


class StoreDirectorySelectionError(ValueError):
    def __init__(self, suffix):
        self.code = 'ENROLLMENT_STORE_DIRECTORY_' + suffix
        super().__init__(self.code)


def _fail(suffix):
    raise StoreDirectorySelectionError(suffix) from None


class _StoreDirectoryScope:
    __slots__ = ('_seal', '_claim', '_pins', '_phase', '_first', '_first_tb', *_PIN_NAMES, '__weakref__')

    def __init__(self, *args, **kwargs):
        _fail('INVALID_ARGUMENT')

    def __init_subclass__(cls, **kwargs):
        _fail('INVALID_ARGUMENT')

    def __enter__(self):
        record = _cleanup(self)
        original = _original(record)
        _raise_original_first(record)
        if original[5][15] or _scope_phase_closed(self, record):
            _fail('CLOSED')
        record._enter_attempted = True
        _mark_attempt(record, 'enter')
        try:
            _directory_scope(self, plan=record._plan, stage=record._stage, continuation=record._continuation)
            content._require_empty_io_budget(record._plan, continuation=record._continuation)
            creator = contract.decode(record._plan._originals[3])['creator']
            record._identities = tuple(tuple(sorted(creator[key].items())) for key in
                ('parent_native_identity', 'parent_path_identity', 'root_native_identity', 'root_path_identity'))
            from . import personal_authority_enrollment as enrollment
            record._parent_expected = tuple(sorted(enrollment._OUTER_NAMES))
            record._root_expected = tuple(sorted(location[1] for location in record._plan._original_locations
                                                if location[0] == 'namespace_root'))
            if len(record._parent_expected) != 5 or len(record._root_expected) != 7:
                _fail('BINDING_INVALID')
            _snapshot(record)
            _set_phase(self, 'acquiring')
            owner = native._open_parent_raw(record._profile.database.parent, write=False, budget=record._budget)
            record._owners = record._owners + (owner,)
            _snapshot(record)
            record._parent = native._Directory(record._profile.database.parent, owner, record._budget)
            _snapshot(record)
            path = record._profile.database.parent / contract.ROOT_NAME
            owner = native._open_parent_raw(path, write=True, budget=record._budget)
            record._owners = record._owners + (owner,)
            _snapshot(record)
            record._root = native._Directory(path, owner, record._budget, parent=record._parent, name=contract.ROOT_NAME)
            _snapshot(record)
            _verify_directories(record)
            _set_phase(self, 'initial_census')
            _census_initial(self)
            _register_selections(self)
            _set_phase(self, 'ready')
            return self
        except BaseException as primary:
            first, traceback = _remember(self, primary)
            try:
                _close_directory_scope(self, primary=first)
            except BaseException as cleanup:
                if cleanup is not first:
                    runtime._note(first)
            raise BaseException.with_traceback(first, traceback)

    def __exit__(self, kind, primary, traceback):
        _close_directory_scope(self, primary=primary)
        return False


class _DirectoryCleanup:
    __slots__ = ('_scope', '_claim', '_continuation', '_plan', '_stage', '_journal', '_budget',
                 '_profile', '_paths', '_guard', '_graph', '_owners', '_parent', '_root', '_identities',
                 '_history_before', '_history_after', '_scans', '_census', '_selections', '_selection_rows',
                 '_enter_attempted', '_exit_attempted', '_exit_returned', '_close_proof', '_first', '_first_tb',
                 '_retire_attempted', '_retire_returned', '_census_retire_returned', '_selection_retire_returned',
                 '_proof_retire_returned', '_parent_expected', '_root_expected',
                 '_final_generation', '_final_scans', '_final_census', '_final_witness')

    def __init__(self, *args, **kwargs):
        _fail('INVALID_ARGUMENT')

    def __init_subclass__(cls, **kwargs):
        _fail('INVALID_ARGUMENT')


class _StoreFileSelection:
    __slots__ = ('_seal', '_claim', '_pins', '__weakref__')

    def __init__(self, *args, **kwargs):
        _fail('INVALID_ARGUMENT')

    def __init_subclass__(cls, **kwargs):
        _fail('INVALID_ARGUMENT')


class _StoreInitialCensus:
    __slots__ = ('_seal', '_claim', '_pins', '__weakref__')

    def __init__(self, *args, **kwargs):
        _fail('INVALID_ARGUMENT')

    def __init_subclass__(cls, **kwargs):
        _fail('INVALID_ARGUMENT')


class _StoreDirectoryCloseReceipt:
    __slots__ = ('_seal', '_pins')

    def __init__(self, *args, **kwargs):
        _fail('INVALID_ARGUMENT')

    def __init_subclass__(cls, **kwargs):
        _fail('INVALID_ARGUMENT')


class _IndexCallback:
    __slots__ = ('_kind', '_key', '_claim')

    def __init__(self, *args, **kwargs):
        _fail('INVALID_ARGUMENT')

    def __init_subclass__(cls, **kwargs):
        _fail('INVALID_ARGUMENT')

    def __call__(self, reference):
        pin = dict.get(_CALLBACK_PINS, id(self))
        if (type(self) is not _IndexCallback or type(pin) is not tuple or len(pin) != 5
                or pin[0] is not self or pin[1] is not reference
                or type(reference) is not weakref.ReferenceType
                or reference() is not None):
            return None
        kind, key, claim = (getattr(self, '_kind', None), getattr(self, '_key', None),
                            getattr(self, '_claim', None))
        if (type(kind) is not str or type(key) is not int
                or kind != pin[2] or key != pin[3] or claim is not pin[4]):
            return None
        tables = {'attempt': (_DIRECTORY_ATTEMPTS, 3), 'scope': (_DIRECTORY_SCOPES, 5),
                  'selection': (_FILE_SELECTIONS, 4), 'census': (_CENSUS_ROWS, 3),
                  'final_attempt': (_FINAL_GENERATION_ATTEMPTS, 3), 'final_census': (_FINAL_CENSUS_ROWS, 3)}
        table, length = tables[pin[2]]
        row = dict.get(table, pin[3])
        if type(row) is tuple and len(row) == length and row[0] is reference and row[1] is pin[4]:
            dict.pop(table, pin[3], None)
        # The original value is already dead. Remove only this callback's pin,
        # including when its weak index was already absent or has a newer row.
        # Neither the pin nor this callback ever strongly owns that value.
        if dict.get(_CALLBACK_PINS, id(self)) is pin:
            dict.pop(_CALLBACK_PINS, id(self), None)
        return None


def _scope_phase_closed(scope, record):
    row = dict.get(_DIRECTORY_SCOPES, id(scope))
    return (type(row) is not tuple or len(row) != 5 or row[0]() is not scope
            or row[1] is not record._claim or row[3] != 'claimed')


def _mark_attempt(record, kind):
    row = _original(record)
    snapshot = row[5]
    index = 15 if kind == 'enter' else 14
    if snapshot[index] is not False:
        _fail('CLOSED')
    _DIRECTORY_ORIGINAL_ROWS[id(record._scope)] = row[:5] + (
        snapshot[:index] + (True,) + snapshot[index + 1:],)
    return None


def _reference(value, kind, claim):
    if type(kind) is not str or kind not in ('attempt', 'scope', 'selection', 'census', 'final_attempt', 'final_census'):
        _fail('INVALID_ARGUMENT')
    callback = object.__new__(_IndexCallback)
    key = id(value)
    callback._kind, callback._key, callback._claim = kind, key, claim
    reference = weakref.ref(value, callback)
    # This immutable pin contains the weakref, never its strongly held value.
    _CALLBACK_PINS[id(callback)] = (callback, reference, kind, key, claim)
    return reference


def _snapshot(record):
    # Actual returned objects/rows survive independently of mutable presentations.
    census_row = None if record._census is None else dict.get(_CENSUS_ROWS, id(record._census))
    snapshot = (record._owners, record._parent, record._root, record._identities,
                record._scans, record._census, census_row, record._selections,
                record._selection_rows, record._history_before, record._close_proof,
                record._exit_returned, record._first, record._first_tb, record._exit_attempted, record._enter_attempted,
                record._parent_expected, record._root_expected,
                None if record._close_proof is None else record._close_proof._pins,
                record._final_generation, record._final_scans, record._final_census,
                None if record._final_census is None else dict.get(_FINAL_CENSUS_ROWS, id(record._final_census)),
                record._final_witness)
    prior = dict.get(_DIRECTORY_ORIGINAL_ROWS, id(record._scope))
    if prior is not None and (type(prior) is not tuple or len(prior) != 6
            or prior[0] is not record._scope or prior[1] is not record._claim or prior[2] is not record):
        _fail('BINDING_INVALID')
    if prior is not None:
        old = prior[5]
        for index in (0, 4, 7, 8, 20):
            if (type(snapshot[index]) is not tuple or len(snapshot[index]) < len(old[index])
                    or any(current is not original for current, original in zip(snapshot[index], old[index]))):
                _fail('BINDING_INVALID')
        for index in (1, 2, 3, 5, 6, 9, 10, 16, 17, 18, 19, 21, 22, 23):
            if old[index] is not None and old[index] != () and snapshot[index] is not old[index]:
                _fail('BINDING_INVALID')
        if prior[4] is not record._graph and (old[0] or old[4] or old[7]):
            _fail('BINDING_INVALID')
        original_pins = prior[3]
    else:
        original_pins = record._scope._pins
    _DIRECTORY_ORIGINAL_ROWS[id(record._scope)] = (
        record._scope, record._claim, record, original_pins, record._graph, snapshot)
    return None


def _original(record):
    row = dict.get(_DIRECTORY_ORIGINAL_ROWS, id(record._scope))
    if (type(row) is not tuple or len(row) != 6 or row[0] is not record._scope
            or row[1] is not record._claim or row[2] is not record
            or type(row[3]) is not tuple or len(row[3]) != len(_PIN_NAMES)
            or type(row[4]) is not tuple or len(row[4]) != 7
            or row[4][0] is not row[0] or row[4][6] is not record
            or type(row[5]) is not tuple or len(row[5]) != 24):
        _fail('BINDING_INVALID')
    return row


def _cleanup(scope):
    record = dict.get(_DIRECTORY_CLEANUP_ROWS, id(scope))
    if record is None and type(scope) is _StoreDirectoryScope:
        row = dict.get(_DIRECTORY_SCOPES, id(scope))
        if (type(row) is tuple and len(row) == 5 and row[0]() is scope
                and row[1] is scope._claim and row[3] == 'retired'):
            _fail('CLOSED')
    if type(scope) is not _StoreDirectoryScope or type(record) is not _DirectoryCleanup or record._scope is not scope:
        _fail('INVALID_ARGUMENT')
    return record


def _remember(scope, primary):
    record = _cleanup(scope)
    original = dict.get(_DIRECTORY_ORIGINAL_ROWS, id(scope))
    recorded = original[5][12:14] if type(original) is tuple and len(original) == 6 else (None, None)
    record._first, record._first_tb = recorded
    if record._first is None:
        continuation = record._continuation if original is None else original[3][0]
        first, traceback = continuation._first, continuation._first_tb
        if first is None:
            first, traceback = primary, BaseException.__dict__['__traceback__'].__get__(primary)
        elif first is not primary:
            runtime._note(first)
        record._first, record._first_tb = first, traceback
        runtime._remember(continuation, first)
    elif record._first is not primary:
        runtime._note(record._first)
    scope._first, scope._first_tb = record._first, record._first_tb
    row = dict.get(_DIRECTORY_ORIGINAL_ROWS, id(scope))
    if type(row) is tuple and len(row) == 6 and row[0] is scope and row[2] is record:
        old = row[5]
        _DIRECTORY_ORIGINAL_ROWS[id(scope)] = row[:5] + (old[:12] + (record._first, record._first_tb) + old[14:],)
    return record._first, record._first_tb


def _original_first(record):
    row = _original(record)
    if row[5][12] is not None:
        return row[5][12], row[5][13]
    value = runtime._minimal(row[3][0])
    return value._first, value._first_tb


def _raise_original_first(record):
    first, traceback = _original_first(record)
    if first is not None:
        raise BaseException.with_traceback(first, traceback)
    return None


def _set_phase(scope, phase):
    record = _cleanup(scope)
    before = dict.get(_DIRECTORY_SCOPES, id(scope))
    if type(before) is not tuple or len(before) != 5 or before[0]() is not scope or before[1] is not record._claim:
        _fail('BINDING_INVALID')
    _DIRECTORY_SCOPES[id(scope)] = before[:3] + (phase, record._close_proof)
    scope._phase = phase


def _open_initial_directory_scope(plan, stage, *, continuation):
    value = runtime._minimal(continuation)
    if value._first is not None:
        raise BaseException.with_traceback(value._first, value._first_tb)
    if dict.get(_DIRECTORY_ATTEMPTS, id(value)) is not None:
        _fail('CLOSED')
    scope, record, claim = object.__new__(_StoreDirectoryScope), object.__new__(_DirectoryCleanup), object()
    reference = _reference(scope, 'scope', claim)
    value_ref = _reference(value, 'attempt', claim)
    _DIRECTORY_ATTEMPTS[id(value)] = (value_ref, claim, reference)
    scope._seal, scope._claim, scope._phase = _SEAL, claim, 'claimed'
    scope._first, scope._first_tb = value._first, value._first_tb
    scope._continuation, scope._plan, scope._stage = value, plan, stage
    scope._journal, scope._budget = value._journal, value._budget
    scope._profile, scope._paths, scope._guard = value._profile, value._paths, value._guard
    scope._pins = tuple(getattr(scope, name) for name in _PIN_NAMES)
    record._scope, record._claim, record._continuation = scope, claim, value
    record._plan, record._stage, record._journal = plan, stage, value._journal
    record._budget, record._profile, record._paths, record._guard = value._budget, value._profile, value._paths, value._guard
    record._owners = record._scans = record._selections = record._selection_rows = ()
    record._parent = record._root = record._census = record._close_proof = record._identities = None
    record._history_before = record._history_after = ()
    record._final_generation = record._final_census = record._final_witness = None
    record._final_scans = ()
    record._parent_expected = record._root_expected = ()
    record._first, record._first_tb = value._first, value._first_tb
    for name in ('_enter_attempted', '_exit_attempted', '_exit_returned', '_retire_attempted', '_retire_returned',
                 '_census_retire_returned', '_selection_retire_returned', '_proof_retire_returned'):
        setattr(record, name, False)
    record._graph = (scope, value, plan, stage, record._journal, record._history_before, record)
    _DIRECTORY_CLEANUP_ROWS[id(scope)] = record
    _DIRECTORY_SCOPES[id(scope)] = (reference, claim, scope._pins, 'claimed', None)
    _snapshot(record)
    try:
        _directory_scope(scope, plan=plan, stage=stage, continuation=value)
        record._history_before = custody._history(record._journal)
        record._graph = (scope, value, plan, stage, record._journal, record._history_before, record)
        _snapshot(record)
        return scope
    except BaseException as primary:
        _remember(scope, primary)
        record._exit_attempted = True  # No acquisition/enter occurred.
        _mark_attempt(record, 'exit')
        _set_phase(scope, 'failed')
        raise


def _directory_scope(scope, *, plan, stage, continuation):
    record = _cleanup(scope)
    original = _original(record)
    _raise_original_first(record)
    value = runtime._minimal(original[3][0])
    row = dict.get(_DIRECTORY_SCOPES, id(scope))
    if (type(row) is not tuple or len(row) != 5 or row[0]() is not scope or row[1] is not record._claim
            or scope._seal is not _SEAL or scope._claim is not record._claim
            or type(row[2]) is not tuple or len(row[2]) != len(_PIN_NAMES)
            or scope._pins is not row[2] or scope._phase != row[3]
            or any(getattr(scope, name) is not pin for name, pin in zip(_PIN_NAMES, row[2]))
            or any(getattr(record, name) is not pin for name, pin in zip(_PIN_NAMES, original[3]))
            or any(getattr(record, name) is not original[5][index] for name, index in
                   (('_owners', 0), ('_parent', 1), ('_root', 2), ('_identities', 3),
                    ('_scans', 4), ('_census', 5), ('_selections', 7), ('_selection_rows', 8),
                    ('_history_before', 9), ('_parent_expected', 16), ('_root_expected', 17),
                    ('_final_generation', 19), ('_final_scans', 20), ('_final_census', 21), ('_final_witness', 23)))
            or plan is not record._plan or stage is not record._stage or continuation is not value
            or original[5][14] or record._exit_attempted or record._retire_attempted):
        _fail('CLOSED' if record._exit_attempted else 'BINDING_INVALID')
    content._content_plan(plan, continuation=value)
    if (runtime._stage(stage) is not value or value._phase != 'installed'
            or custody._active_journal_for_budget(record._budget) is not record._journal
            or lifecycle.current_guard() is not record._guard
            or tuple(record._profile.identity) != record._paths):
        _fail('BINDING_INVALID')
    record._budget.clock_only()
    return scope


def _bound_directory_pair(record, original):
    _raise_original_first(record)
    if _original(record) is not original:
        _fail('BINDING_INVALID')
    parent, root, budget = original[5][1], original[5][2], original[3][4]
    if (type(parent) is not native._Directory or type(root) is not native._Directory
            or parent is root or parent.parent is not None or root.parent is not parent
            or parent.budget is not budget or root.budget is not budget):
        _fail('BINDING_INVALID')
    for value in (parent, root):
        fields = native._Directory.__dict__['__dict__'].__get__(value, native._Directory)
        if type(fields) is not dict or 'verify' in fields:
            _fail('BINDING_INVALID')
    return parent, root, budget


def _verify_directory_fields(record, original, parent, root):
    _bound_directory_pair(record, original)
    actual = (parent.native_identity, parent.path_identity, root.native_identity, root.path_identity)
    if tuple(tuple(sorted(value.items())) for value in actual) != original[5][3]:
        _fail('IDENTITY_MISMATCH')
    if tuple(original[3][5].identity) != original[3][6] or lifecycle.current_guard() is not original[3][7]:
        _fail('BINDING_INVALID')


def _verify_directory_registration(record):
    original = _original(record)
    parent, root, budget = _bound_directory_pair(record, original)
    _verify_directory_fields(record, original, parent, root)


def _verify_directories(record):
    original = _original(record)
    parent, root, budget = _bound_directory_pair(record, original)
    native._Directory.verify(root)
    native._security_check(root.handle, budget)
    _verify_directory_fields(record, original, parent, root)


def _census_cleanup_binding(scope):
    if type(scope) is _StoreFinalCensusGeneration:
        return _final_generation_original(scope)[5]
    record = _cleanup(scope)
    return _original(record)[4]


def _census_binding(scope, role):
    if type(scope) is _StoreFinalCensusGeneration:
        return _final_generation_binding(scope, role)
    record = _cleanup(scope)
    _directory_scope(scope, plan=record._plan, stage=record._stage, continuation=record._continuation)
    if type(role) is not str or role not in ('parent', 'root') or scope._phase not in ('initial_census', 'ready'):
        _fail('INVALID_ARGUMENT')
    if role == 'parent':
        return record._parent, record._budget, record._parent_expected, True, _original(record)[4]
    expected = record._root_expected
    if type(expected) is not tuple or len(expected) != 7:
        _fail('BINDING_INVALID')
    return record._root, record._budget, expected, False, _original(record)[4]


def _census_initial(scope):
    record = _cleanup(scope)
    if record._census is not None or record._scans:
        _fail('CLOSED')
    try:
        for role in ('parent', 'root'):
            scan = census_native._scan(scope, role)
            record._scans = record._scans + (scan,)
            _snapshot(record)
            census_native._scan_names(scan, scope=scope, role=role)
        value = object.__new__(_StoreInitialCensus)
        value._seal, value._claim = _SEAL, object()
        value._pins = (scope, record._claim, record._scans)
        _CENSUS_ROWS[id(value)] = (_reference(value, 'census', value._claim), value._claim, value._pins)
        record._census = value
        _snapshot(record)
        return value
    except BaseException as failure:
        first, traceback = _remember(scope, failure)
        raise BaseException.with_traceback(first, traceback)


def _initial_census(scope):
    record = _cleanup(scope)
    _directory_scope(scope, plan=record._plan, stage=record._stage, continuation=record._continuation)
    value = record._census
    row = dict.get(_CENSUS_ROWS, id(value))
    if (type(value) is not _StoreInitialCensus or type(row) is not tuple or len(row) != 3
            or row[0]() is not value or value._seal is not _SEAL or value._claim is not row[1]
            or value._pins is not row[2] or type(row[2]) is not tuple or len(row[2]) != 3
            or row[2][0] is not scope or row[2][1] is not record._claim or row[2][2] is not record._scans
            or type(record._scans) is not tuple or len(record._scans) != 2):
        _fail('CENSUS_UNCONFIRMED')
    for scan, role in zip(record._scans, ('parent', 'root')):
        census_native._scan_names(scan, scope=scope, role=role)
    return value


def _register_selections(scope):
    record = _cleanup(scope)
    _initial_census(scope)
    for family, raws, names, maxima in (
        ('original', record._plan._originals, record._plan._original_locations, store._ORIGINAL_MAXIMA),
        ('store', record._plan._file_raws, record._plan._file_names, record._plan._file_maxima)):
        count = 11 if family == 'original' else 29
        if len(raws) != count or len(names) != count or len(maxima) != count:
            _fail('BINDING_INVALID')
        for ordinal in range(count):
            directory = record._root
            if family == 'original':
                location, name = names[ordinal]
                directory = record._parent if location == 'data_parent' else record._root
            else:
                name = names[ordinal]
            value = object.__new__(_StoreFileSelection)
            value._seal, value._claim = _SEAL, object()
            value._pins = (scope, record._claim, family, ordinal, directory, name, maxima[ordinal], raws[ordinal])
            row = (_reference(value, 'selection', value._claim), value._claim, value._pins, False)
            _FILE_SELECTIONS[id(value)] = row
            record._selections = record._selections + (value,)
            record._selection_rows = record._selection_rows + (row,)
            _snapshot(record)


def _selection(value, scope):
    row = dict.get(_FILE_SELECTIONS, id(value))
    record = _cleanup(scope)
    _directory_scope(scope, plan=record._plan, stage=record._stage, continuation=record._continuation)
    if (type(value) is not _StoreFileSelection or type(row) is not tuple or len(row) != 4
            or row[0]() is not value or value._seal is not _SEAL or value._claim is not row[1]
            or value._pins is not row[2] or type(row[2]) is not tuple or len(row[2]) != 8
            or row[2][0] is not scope or row[2][1] is not record._claim
            or scope._phase != 'ready' or not any(value is prior for prior in record._selections)):
        _fail('BINDING_INVALID')
    return value


def _original_selection(scope, ordinal):
    record = _cleanup(scope)
    _directory_scope(scope, plan=record._plan, stage=record._stage, continuation=record._continuation)
    if type(ordinal) is not int or not 0 <= ordinal < 11:
        _fail('INVALID_ARGUMENT')
    if type(record._selections) is not tuple or len(record._selections) != 40:
        _fail('BINDING_INVALID')
    return _selection(record._selections[ordinal], scope)


def _store_selection(scope, ordinal):
    record = _cleanup(scope)
    _directory_scope(scope, plan=record._plan, stage=record._stage, continuation=record._continuation)
    if type(ordinal) is not int or not 0 <= ordinal < 29:
        _fail('INVALID_ARGUMENT')
    if type(record._selections) is not tuple or len(record._selections) != 40:
        _fail('BINDING_INVALID')
    return _selection(record._selections[11 + ordinal], scope)


def _consume_selection(value, *, scope):
    record = _cleanup(scope)
    _raise_original_first(record)
    row = dict.get(_FILE_SELECTIONS, id(value))
    if type(row) is not tuple or len(row) != 4 or row[0]() is not value or row[3] is not False:
        _fail('CLOSED')
    _FILE_SELECTIONS[id(value)] = row[:3] + (True,)
    try:
        return _selection(value, scope)
    except BaseException as failure:
        first, traceback = _remember(scope, failure)
        raise BaseException.with_traceback(first, traceback)


def _close_directory_scope(scope, *, primary=None):
    record = _cleanup(scope)
    if _original(record)[5][14]:
        _raise_original_first(record)
        _fail('CLOSED')
    record._exit_attempted = True
    _mark_attempt(record, 'exit')
    record._first, record._first_tb = _original_first(record)
    if primary is not None:
        _remember(scope, primary)
    _set_phase(scope, 'closing')
    if record._first is None:
        try:
            _verify_directories(record)
        except BaseException as failure:
            _remember(scope, failure)
    original = _original(record)
    try:
        _require_census_generations_settled(record)
        if record._first is None:
            _require_final_census_terminal_original(record)
    except BaseException as failure:
        _remember(scope, failure)
    for owner in reversed(original[5][0]):
        try:
            owner.close()
        except BaseException as failure:
            _remember(scope, failure)
    try:
        record._history_after = custody._history(original[4][4])
        _require_census_generations_settled(record)
        _require_native_subset(record)
        proof = object.__new__(_StoreDirectoryCloseReceipt)
        proof._seal, proof._pins = _SEAL, (scope, original[1], original[5][0], original[5][4], record._history_after,
            original[5][19], original[5][20], original[5][21], original[5][22])
        record._close_proof = proof
        record._exit_returned = True
        _snapshot(record)
    except BaseException as failure:
        _remember(scope, failure)
    _set_phase(scope, 'closed' if record._exit_returned else 'failed')
    if record._first is not None and primary is None:
        raise BaseException.with_traceback(record._first, record._first_tb)
    return None


def _require_native_subset(record):
    original = _original(record)
    history = custody._history(original[4][4])
    before = original[5][9]
    if type(history) is not tuple or type(before) is not tuple or len(history) < len(before) or any(a is not b for a, b in zip(history, before)):
        _fail('CLEANUP_DEBT_REMAINS')
    for slot in history[len(before):]:
        slot = custody._slot(slot)
        legal = (custody._signed32(slot._close_return) and slot._close_return != 0) if slot._kind == custody._HANDLE else slot._close_return is None
        if not (slot._known is True and slot._return_seen is True and slot._close_attempted is True
                and slot._representation_confirmed is True and slot._closed_fact is not None and legal):
            _fail('CLEANUP_DEBT_REMAINS')


def _directory_scope_close_receipt(scope):
    record = _cleanup(scope)
    original = _original(record)
    value = original[5][10]
    if (type(value) is not _StoreDirectoryCloseReceipt or value._seal is not _SEAL
            or original[5][11] is not True or record._close_proof is not value
            or value._pins is not original[5][18]
            or type(value._pins) is not tuple or len(value._pins) != 9
            or value._pins[0] is not scope or value._pins[1] is not original[1]):
        _fail('CLEANUP_DEBT_REMAINS')
    return value


def _for_continuation(value):
    attempt = dict.get(_DIRECTORY_ATTEMPTS, id(value))
    if attempt is None:
        return None
    if type(attempt) is not tuple or len(attempt) != 3 or attempt[0]() is not value:
        _fail('BINDING_INVALID')
    scope = attempt[2]()
    return None if scope is None else _cleanup(scope)


def _require_retirable_for_continuation(value):
    record = _for_continuation(value)
    if record is None:
        return None
    original = _original(record)
    if original[5][14] is not True or dict.get(_DIRECTORY_RETIREMENT_ROWS, id(record._scope)) is not None:
        _fail('CLEANUP_DEBT_REMAINS')
    _require_census_generations_settled(record)
    _require_native_subset(record)
    return record


def _retirement_step(record, index):
    row = dict.get(_DIRECTORY_RETIREMENT_ROWS, id(record._scope))
    if type(row) is not tuple or len(row) != 9 or row[0][2] is not record or row[index] is not False:
        _fail('BINDING_INVALID')
    _DIRECTORY_RETIREMENT_ROWS[id(record._scope)] = row[:index] + (True,) + row[index + 1:]
    return None


def _retirement_observation_for_continuation(value):
    record = _for_continuation(value)
    if record is None:
        return None
    row = dict.get(_DIRECTORY_RETIREMENT_ROWS, id(record._scope))
    if row is None:
        return None
    if type(row) is not tuple or len(row) != 9 or row[0][2] is not record:
        _fail('BINDING_INVALID')
    original, snapshot = row[0], row[0][5]
    selections_effect = len(snapshot[7]) == len(snapshot[8]) and all(
        type(dict.get(_FILE_SELECTIONS, id(selection))) is tuple
        and len(dict.get(_FILE_SELECTIONS, id(selection))) == 4
        and dict.get(_FILE_SELECTIONS, id(selection))[0] is prior[0]
        and dict.get(_FILE_SELECTIONS, id(selection))[1] is prior[1]
        and dict.get(_FILE_SELECTIONS, id(selection))[2] == ()
        and dict.get(_FILE_SELECTIONS, id(selection))[3] is True and selection._pins == ()
        for selection, prior in zip(snapshot[7], snapshot[8]))
    census = snapshot[5]
    current = None if census is None else dict.get(_CENSUS_ROWS, id(census))
    census_effect = census is None or (type(current) is tuple and len(current) == 3
        and current[0] is snapshot[6][0] and current[1] is snapshot[6][1]
        and current[2] == () and census._pins == ())
    scope_row = dict.get(_DIRECTORY_SCOPES, id(original[0]))
    scope_effect = (type(scope_row) is tuple and len(scope_row) == 5
        and scope_row[0]() is original[0] and scope_row[1] is original[1]
        and scope_row[2] == () and scope_row[3] == 'retired' and scope_row[4] is None
        and original[0]._pins == () and all(getattr(original[0], name) is None for name in _PIN_NAMES)
        and (snapshot[10] is None or snapshot[10]._pins == ()))
    generation, final = snapshot[19], snapshot[21]
    final_observation = None if generation is None else census_native._retirement_observation(generation)
    gen_effect = generation is None or (generation._pins == () and generation._phase == 'retired'
        and _FINAL_GENERATION_STATES[id(generation)][0] == 'retired'
        and (final is None or (final._pins == () and _FINAL_CENSUS_ROWS[id(final)][2] == ())))
    effects = (census_native._retirement_observation(original[0]), final_observation,
               selections_effect, census_effect, scope_effect, gen_effect)
    _DIRECTORY_RETIREMENT_ROWS[id(original[0])] = row[:6] + (effects,) + row[7:]
    return row[1:6] + row[7:9], effects


def _retire_for_continuation(value, *, primary=None):
    record = _require_retirable_for_continuation(value)
    if record is None:
        return None
    record._retire_attempted = True
    original, snapshot = _original(record), _original(record)[5]
    generation = snapshot[19]
    _DIRECTORY_RETIREMENT_ROWS[id(record._scope)] = (original, False, generation is None,
        False, False, False, None, generation is None, False)
    # Both independently settled generations get their own once retirement.
    first = None
    for subject, index in ((record._scope, 1), (generation, 2)):
        if subject is None:
            continue
        try:
            census_native._retire_for_scope(subject, primary=primary)
            _retirement_step(record, index)
            if index == 1:
                record._census_retire_returned = True
        except BaseException as failure:
            first, traceback = _remember(record._scope, failure)
    try:
        if first is not None:
            raise BaseException.with_traceback(first, traceback)
        if len(snapshot[7]) != len(snapshot[8]):
            _fail('BINDING_INVALID')
        for selection, before in zip(snapshot[7], snapshot[8]):
            current = dict.get(_FILE_SELECTIONS, id(selection))
            if (type(current) is not tuple or len(current) != 4
                    or any(current[index] is not before[index] for index in (0, 1, 2))):
                _fail('BINDING_INVALID')
            _FILE_SELECTIONS[id(selection)] = current[:2] + ((), True)
            selection._pins = ()
        _retirement_step(record, 3)
        record._selection_retire_returned = True
        if snapshot[5] is not None:
            current = dict.get(_CENSUS_ROWS, id(snapshot[5]))
            if any(current[index] is not snapshot[6][index] for index in (0, 1, 2)):
                _fail('BINDING_INVALID')
            _CENSUS_ROWS[id(snapshot[5])] = current[:2] + ((),)
            snapshot[5]._pins = ()
        if snapshot[10] is not None:
            if snapshot[10]._pins is not snapshot[18]:
                _fail('BINDING_INVALID')
            snapshot[10]._pins = ()
        _retirement_step(record, 4)
        record._proof_retire_returned = True
        if generation is not None:
            if snapshot[21] is not None:
                current = dict.get(_FINAL_CENSUS_ROWS, id(snapshot[21]))
                if any(current[index] is not snapshot[22][index] for index in (0, 1, 2)):
                    _fail('BINDING_INVALID')
                _FINAL_CENSUS_ROWS[id(snapshot[21])] = current[:2] + ((),)
                snapshot[21]._pins = ()
            state = _FINAL_GENERATION_STATES[id(generation)]
            _FINAL_GENERATION_STATES[id(generation)] = ('retired',) + state[1:]
            generation._pins, generation._phase = (), 'retired'
            _retirement_step(record, 7)
        _set_phase(record._scope, 'retired')
        record._scope._pins = ()
        for name in _PIN_NAMES:
            setattr(record._scope, name, None)
        record._scope._first = record._scope._first_tb = None
        scope_row = dict.get(_DIRECTORY_SCOPES, id(record._scope))
        _DIRECTORY_SCOPES[id(record._scope)] = scope_row[:2] + ((), 'retired', None)
        _retirement_step(record, 5)
        observation = _retirement_observation_for_continuation(value)
        if (not _census_retirement_complete(observation[1][0])
                or not _census_retirement_complete(observation[1][1])
                or any(effect is not True for effect in observation[1][2:])):
            _fail('RETIREMENT_UNCONFIRMED')
        _retirement_step(record, 8)
        record._retire_returned = True
    except BaseException as failure:
        first, traceback = _remember(record._scope, failure)
        try:
            _retirement_observation_for_continuation(value)
        except BaseException as secondary:
            if secondary is not first:
                runtime._note(first)
        raise BaseException.with_traceback(first, traceback)
    return None


def _original_for_continuation(value):
    record = _for_continuation(value)
    return None if record is None else _original(record)


def _release_retired_graph(graph):
    if graph is None:
        return None
    if type(graph) is not tuple or len(graph) != 6 or type(graph[2]) is not _DirectoryCleanup:
        _fail('BINDING_INVALID')
    record = graph[2]
    retirement = dict.get(_DIRECTORY_RETIREMENT_ROWS, id(record._scope))
    if (type(retirement) is not tuple or len(retirement) != 9 or retirement[0] is not graph
            or any(value is not True for value in retirement[1:6] + retirement[7:9])
            or type(retirement[6]) is not tuple or len(retirement[6]) != 6
            or not _census_retirement_complete(retirement[6][0])
            or not _census_retirement_complete(retirement[6][1])
            or any(effect is not True for effect in retirement[6][2:])):
        _fail('CLEANUP_DEBT_REMAINS')
    if dict.get(_DIRECTORY_CLEANUP_ROWS, id(record._scope)) is not record:
        _fail('BINDING_INVALID')
    generation = graph[5][19]
    if generation is not None:
        _final_generation_original(generation)
        dict.pop(_FINAL_GENERATION_ORIGINALS, id(generation))
        dict.pop(_FINAL_GENERATION_STATES, id(generation))
    dict.pop(_DIRECTORY_CLEANUP_ROWS, id(record._scope))
    dict.pop(_DIRECTORY_ORIGINAL_ROWS, id(record._scope))
    dict.pop(_DIRECTORY_RETIREMENT_ROWS, id(record._scope))
    return None


def _catalog_extra_original():
    import sys
    return ((sys.modules[__name__], ('weakref', 'runtime', 'content', 'custody', 'native', 'contract', 'store', 'lifecycle',
        'census_native', 'file_io', '_bound_directory_pair', '_verify_directory_fields', '_verify_directory_registration', '_FINAL_GENERATION_ATTEMPTS', '_FINAL_GENERATION_ORIGINALS',
        '_FINAL_GENERATION_STATES', '_FINAL_CENSUS_ROWS', '_StoreFinalCensusGeneration', '_StoreFinalCensus', '_SEAL', '_DIRECTORY_ATTEMPTS', '_DIRECTORY_SCOPES', '_DIRECTORY_CLEANUP_ROWS',
        '_FILE_SELECTIONS', '_CENSUS_ROWS', '_CALLBACK_PINS', '_DIRECTORY_ORIGINAL_ROWS', '_DIRECTORY_RETIREMENT_ROWS', '_PIN_NAMES', 'StoreDirectorySelectionError', '_StoreDirectoryScope',
        '_DirectoryCleanup', '_StoreFileSelection', '_StoreInitialCensus', '_StoreDirectoryCloseReceipt', '_IndexCallback')),
        (weakref, ('ref', 'ReferenceType')), (weakref.ReferenceType, ('__callback__', '__call__')),
        (lifecycle, ('current_guard',)),
        *tuple((kind, ('__slots__', '__init__', '__init_subclass__', *(name for name in kind.__slots__ if name != '__weakref__')))
               for kind in (_StoreDirectoryScope, _DirectoryCleanup, _StoreFileSelection, _StoreInitialCensus,
                            _StoreDirectoryCloseReceipt, _IndexCallback, _StoreFinalCensusGeneration, _StoreFinalCensus)),
        (_StoreDirectoryScope, ('__enter__', '__exit__')), (_IndexCallback, ('__call__',)))


class _StoreFinalCensusGeneration:
    __slots__ = ('_seal', '_claim', '_pins', '_phase', '__weakref__')

    def __init__(self, *args, **kwargs):
        _fail('INVALID_ARGUMENT')

    def __init_subclass__(cls, **kwargs):
        _fail('INVALID_ARGUMENT')


class _StoreFinalCensus:
    __slots__ = ('_seal', '_claim', '_pins', '__weakref__')

    def __init__(self, *args, **kwargs):
        _fail('INVALID_ARGUMENT')

    def __init_subclass__(cls, **kwargs):
        _fail('INVALID_ARGUMENT')


def _final_generation_original(generation):
    row = dict.get(_FINAL_GENERATION_ORIGINALS, id(generation))
    if (type(generation) is not _StoreFinalCensusGeneration or type(row) is not tuple
            or len(row) != 6 or row[0] is not generation):
        _fail('INVALID_ARGUMENT')
    return row


def _final_generation_live(generation):
    row = _final_generation_original(generation)
    state = _FINAL_GENERATION_STATES[id(generation)]
    record = _cleanup(row[2])
    _raise_original_first(record)
    _directory_scope(row[2], plan=row[3][1], stage=row[3][2], continuation=row[3][0])
    pins = row[4]
    if (type(pins) is not tuple or len(pins) != 13
            or generation._seal is not _SEAL or generation._claim is not row[1]
            or generation._pins is not pins or generation._phase != state[0]
            or pins[0] is not row[2] or pins[1] is not record._claim
            or any(a is not b for a, b in zip(pins[2:8], (record._continuation, record._plan,
                   record._stage, record._journal, record._guard, record._budget)))
            or pins[8] is not record._parent or pins[9] is not record._root
            or record._final_generation is not generation
            or state[0] not in ('available', 'scanning', 'ready')):
        _fail('BINDING_INVALID')
    if state[0] in ('available', 'scanning'):
        file_io._verify_initial_file_witness(pins[12], scope=row[2])
    return row


def _begin_final_census_generation(scope):
    record = _cleanup(scope)
    original = _original(record)
    if dict.get(_FINAL_GENERATION_ATTEMPTS, id(scope)) is not None:
        _fail('CLOSED')
    generation, claim = object.__new__(_StoreFinalCensusGeneration), object()
    generation._seal, generation._claim = _SEAL, claim
    generation._pins, generation._phase = None, 'claimed'
    reference = _reference(scope, 'final_attempt', claim)
    _FINAL_GENERATION_ATTEMPTS[id(scope)] = (reference, claim, weakref.ref(generation))
    graph = (generation, original[3][0], original[3][1], original[3][2],
             original[3][3], original[5][9], record)
    row = (generation, claim, scope, original[3], None, graph)
    _FINAL_GENERATION_ORIGINALS[id(generation)] = row
    _FINAL_GENERATION_STATES[id(generation)] = ('claimed', (), None, None, None)
    record._final_generation = generation
    _snapshot(record)
    try:
        _directory_scope(scope, plan=record._plan, stage=record._stage, continuation=record._continuation)
        if scope._phase != 'ready':
            _fail('CLOSED')
        witness = file_io._initial_file_witness_for_scope(scope)
        expected = tuple(sorted((*original[5][17], *record._plan._file_names)))
        if (len(expected) != 36 or len(frozenset(expected)) != 36
                or len(record._plan._file_names) != 29 or len(original[5][17]) != 7
                or len(original[5][16]) != 5):
            _fail('BINDING_INVALID')
        pins = (scope, original[1], record._continuation, record._plan, record._stage,
                record._journal, record._guard, record._budget, record._parent, record._root,
                original[5][16], expected, witness)
        _FINAL_GENERATION_ORIGINALS[id(generation)] = row[:4] + (pins, graph)
        _FINAL_GENERATION_STATES[id(generation)] = ('available', (), None, None, None)
        generation._pins, generation._phase = pins, 'available'
        record._final_witness = witness
        _snapshot(record)
        _final_generation_live(generation)
        return generation
    except BaseException as primary:
        first, traceback = _remember_census_subject(generation, primary)
        if first is primary:
            raise
        raise BaseException.with_traceback(first, traceback)


def _final_generation_binding(generation, role):
    row = _final_generation_live(generation)
    pins = row[4]
    if type(role) is not str or role not in ('parent', 'root'):
        _fail('INVALID_ARGUMENT')
    if role == 'parent':
        return pins[8], pins[7], pins[10], True, row[5]
    return pins[9], pins[7], pins[11], False, row[5]


def _remember_census_subject(subject, primary):
    if type(subject) is _StoreDirectoryScope:
        return _remember(subject, primary)
    row = _final_generation_original(subject)
    first, traceback = _remember(row[2], primary)
    state = _FINAL_GENERATION_STATES[id(subject)]
    _FINAL_GENERATION_STATES[id(subject)] = ('failed', state[1], state[2], first, traceback)
    subject._phase = 'failed'
    return first, traceback


def _run_final_census(generation):
    row = _final_generation_original(generation)
    state = _FINAL_GENERATION_STATES[id(generation)]
    if state[0] != 'available' or state[1] or state[2] is not None:
        _fail('CLOSED')
    _FINAL_GENERATION_STATES[id(generation)] = ('scanning', (), None, None, None)
    generation._phase = 'scanning'
    record = _cleanup(row[2])
    try:
        _final_generation_live(generation)
        for role in ('parent', 'root'):
            scan = census_native._scan_final_generation(generation, role)
            actual = _FINAL_GENERATION_STATES[id(generation)]
            scans = actual[1] + (scan,)
            _FINAL_GENERATION_STATES[id(generation)] = (actual[0], scans) + actual[2:]
            record._final_scans = scans
            _snapshot(record)
            census_native._scan_names(scan, scope=generation, role=role)
        value = object.__new__(_StoreFinalCensus)
        value._seal, value._claim = _SEAL, object()
        value._pins = (generation, row[1], row[4], record._final_scans)
        _FINAL_CENSUS_ROWS[id(value)] = (_reference(value, 'final_census', value._claim), value._claim, value._pins)
        actual = _FINAL_GENERATION_STATES[id(generation)]
        _FINAL_GENERATION_STATES[id(generation)] = ('ready', actual[1], value, None, None)
        generation._phase = 'ready'
        record._final_census = value
        _snapshot(record)
        _final_census(row[2])
        return value
    except BaseException as primary:
        first, traceback = _remember_census_subject(generation, primary)
        if first is primary:
            raise
        raise BaseException.with_traceback(first, traceback)


def _final_census(scope):
    record = _cleanup(scope)
    original = _original(record)
    generation = original[5][19]
    row = _final_generation_live(generation)
    state = _FINAL_GENERATION_STATES[id(generation)]
    value = original[5][21]
    proof = dict.get(_FINAL_CENSUS_ROWS, id(value))
    if (state[0] != 'ready' or state[2] is not value or type(value) is not _StoreFinalCensus
            or type(proof) is not tuple or len(proof) != 3 or proof[0]() is not value
            or value._seal is not _SEAL or value._claim is not proof[1]
            or value._pins is not proof[2] or len(proof[2]) != 4
            or proof[2][0] is not generation or proof[2][1] is not row[1]
            or proof[2][2] is not row[4] or proof[2][3] is not original[5][20]
            or state[1] is not original[5][20] or len(state[1]) != 2):
        _fail('CENSUS_UNCONFIRMED')
    for scan, role in zip(state[1], ('parent', 'root')):
        census_native._scan_names(scan, scope=generation, role=role)
    return value


def _require_census_generations_settled(record):
    original = _original(record)
    census_native._require_settled(original[0])
    generation = original[5][19]
    if generation is not None:
        _final_generation_original(generation)
        census_native._require_settled(generation)
    return None


def _require_final_census_terminal_original(record):
    original = _original(record)
    generation, value = original[5][19], original[5][21]
    if generation is None:
        return None
    row = _final_generation_original(generation)
    state = _FINAL_GENERATION_STATES[id(generation)]
    proof = dict.get(_FINAL_CENSUS_ROWS, id(value))
    if (state[0] != 'ready' or state[2] is not value or len(state[1]) != 2
            or generation._seal is not _SEAL or generation._claim is not row[1]
            or generation._pins is not row[4] or generation._phase != 'ready'
            or type(proof) is not tuple or len(proof) != 3 or proof[0]() is not value
            or value._seal is not _SEAL or value._claim is not proof[1] or value._pins is not proof[2]
            or proof[2][0] is not generation or proof[2][1] is not row[1]
            or proof[2][2] is not row[4] or proof[2][3] is not state[1]
            or state[1] is not original[5][20]):
        _fail('CENSUS_UNCONFIRMED')
    for scan, role, expected in zip(state[1], ('parent', 'root'), row[4][10:12]):
        actual = census_native._scan_row(scan)
        if (actual[2] is not generation or actual[3] != role or actual[8] != 'ready'
                or scan._scope is not generation or scan._role != role
                or scan._binding is not actual[4] or scan._names is not actual[6]
                or scan._queries is not actual[5] or scan._phase != 'ready'
                or scan._first is not None or actual[9] is not None
                or not all(actual[index] for index in (11, 12, 13)) or actual[14]
                or len(actual[6]) != len(expected) or frozenset(actual[6]) != frozenset(expected)):
            _fail('CENSUS_UNCONFIRMED')
    return None


def _census_retirement_complete(observation):
    return observation is None or (type(observation) is tuple and len(observation) == 5
        and observation[0] == 'retired' and all(value is True for value in observation[1:]))
