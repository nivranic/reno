"""Private Store file cycles, separate from the closed source journal.

Candidate for the r919 held-reader and r904 four-content-pass contract.
This module issues no SQL materials, source_closed or write authorization.
Its graph survives business-pin retirement until the runtime releases it.
"""
from __future__ import annotations

import contextlib
import ctypes
import hashlib
import weakref
from _operator import is_ as _identity_is

from . import personal_authority_contract as authority
from . import personal_authority_enrollment_io as chunks
from . import personal_authority_enrollment_store_custody as custody
from . import personal_authority_enrollment_store_directories as directories
from . import personal_authority_enrollment_store_runtime as runtime
from . import personal_authority_enrollment_store_selections as content
from . import personal_authority_namespace3_io as native

_SEAL = object()
_GRAPH_ATTEMPTS = {}
_GRAPH_ORIGINALS = {}
_GRAPH_STATES = {}
_GRAPH_HISTORIES = {}
_CYCLE_ORIGINALS = {}
_CYCLE_STATES = {}
_RESOURCE_ORIGINALS = {}
_RESOURCE_STATES = {}
_READER_ORIGINALS = {}
_READER_STATES = {}
_PUBLICATIONS = {}
_CLOSE_RECEIPTS = {}
_RETIRED_PROOFS = {}
_RETIRED_METADATA = {}
_GRAPH_RELEASES = {}
_CHECKPOINTS = {}
_CHECKPOINTS_BY_CYCLE = {}
_CALLBACK_PINS = {}


class StoreFileIOError(ValueError):
    def __init__(self, suffix):
        self.code = "ENROLLMENT_STORE_FILE_IO_" + suffix
        super().__init__(self.code)


def _fail(suffix):
    raise StoreFileIOError(suffix) from None


class _FileIOCleanupGraph:
    __slots__ = ("_seal", "_claim", "_pins", "_cycles", "_resources",
                 "_readers", "_publications", "_phase", "_checkpoints", "__weakref__")

    def __init__(self, *args, **kwargs):
        _fail("INVALID_ARGUMENT")

    def __init_subclass__(cls, **kwargs):
        _fail("INVALID_ARGUMENT")


class _StoreFileCycle:
    __slots__ = ("_seal", "_claim", "_pins", "_phase")

    def __init__(self, *args, **kwargs):
        _fail("INVALID_ARGUMENT")

    def __init_subclass__(cls, **kwargs):
        _fail("INVALID_ARGUMENT")


class _FileResource:
    __slots__ = ("_seal", "_claim", "_pins")

    def __init__(self, *args, **kwargs):
        _fail("INVALID_ARGUMENT")

    def __init_subclass__(cls, **kwargs):
        _fail("INVALID_ARGUMENT")


class _StoreHeldReader:
    __slots__ = ("_seal", "_claim", "_pins")

    def __init__(self, *args, **kwargs):
        _fail("INVALID_ARGUMENT")

    def __init_subclass__(cls, **kwargs):
        _fail("INVALID_ARGUMENT")

    def __enter__(self):
        _reader_original(self)
        try:
            row = _reader(self)
            state = _READER_STATES[id(self)]
            if state[0] != "initial_read" or state[3]:
                _fail("CLOSED")
            _READER_STATES[id(self)] = state[:3] + (True,) + state[4:]
            return row[0]
        except BaseException as primary:
            _abort_held_reader(self, primary=primary)
            raise

    def __exit__(self, kind, primary, traceback):
        row = _reader_original(self)
        if primary is not None:
            _abort_held_reader(self, primary=primary)
        else:
            state = _READER_STATES[id(self)]
            if state[0] == "closed":
                _reader_close_receipt(self)
            else:
                _finalize_held_reader(self)
        return False


class _StorePublicationReceipt:
    __slots__ = ("_seal", "_claim", "_pins")

    def __init__(self, *args, **kwargs):
        _fail("INVALID_ARGUMENT")

    def __init_subclass__(cls, **kwargs):
        _fail("INVALID_ARGUMENT")


class _StoreReaderCloseReceipt:
    __slots__ = ("_seal", "_claim", "_pins")

    def __init__(self, *args, **kwargs):
        _fail("INVALID_ARGUMENT")

    def __init_subclass__(cls, **kwargs):
        _fail("INVALID_ARGUMENT")


class _GraphIndexCallback:
    __slots__ = ("_key", "_kind", "_claim")

    def __init__(self, *args, **kwargs):
        _fail("INVALID_ARGUMENT")

    def __init_subclass__(cls, **kwargs):
        _fail("INVALID_ARGUMENT")

    def __call__(self, reference):
        pin = dict.get(_CALLBACK_PINS, id(self))
        kind, key = getattr(self, "_kind", None), getattr(self, "_key", None)
        if (type(self) is not _GraphIndexCallback or type(pin) is not tuple
                or len(pin) != 5 or pin[0] is not self or pin[1] is not reference
                or type(reference) is not weakref.ReferenceType or reference() is not None
                or type(kind) is not str or type(key) is not int
                or kind != pin[2] or key != pin[3] or self._claim is not pin[4]):
            return None
        row = dict.get(_GRAPH_ATTEMPTS, pin[3])
        if (pin[2] == "graph_attempt" and type(row) is tuple and len(row) == 3
                and row[0] is reference and row[1] is pin[4] and row[2] is None):
            dict.pop(_GRAPH_ATTEMPTS, pin[3], None)
        released = dict.get(_GRAPH_RELEASES, pin[3])
        if (pin[2] == "graph_release" and type(released) is tuple and len(released) == 4
                and released[0] is reference and released[1] is pin[4]):
            dict.pop(_GRAPH_RELEASES, pin[3], None)
        if dict.get(_CALLBACK_PINS, id(self)) is pin:
            dict.pop(_CALLBACK_PINS, id(self), None)
        return None


def _graph_original(graph):
    row = dict.get(_GRAPH_ORIGINALS, id(graph))
    if (type(graph) is not _FileIOCleanupGraph or type(row) is not tuple
            or len(row) != 14 or row[0] is not graph):
        _fail("INVALID_ARGUMENT")
    return row


def _graph_state(graph):
    _graph_original(graph)
    state = dict.get(_GRAPH_STATES, id(graph))
    if type(state) is not tuple or len(state) != 10:
        _fail("REGISTRY_CHANGED")
    return state


def _remember(graph, primary):
    row, state = _graph_original(graph), _graph_state(graph)
    first, traceback = state[4:6]
    if first is None:
        first, traceback = row[2]._first, row[2]._first_tb
        if first is None:
            first = primary
            traceback = BaseException.__dict__["__traceback__"].__get__(primary)
        elif first is not primary:
            runtime._note(first)
        _GRAPH_STATES[id(graph)] = state[:4] + (first, traceback) + state[6:]
    elif first is not primary:
        runtime._note(first)
    runtime._remember(row[2], first)
    return first, traceback


def _capture_history(graph):
    row = _graph_original(graph)
    identity_is, identity_all, identity_map = _identity_is, all, map
    actual = custody._history(row[5])
    old = _GRAPH_HISTORIES[id(graph)]
    if (len(actual) < len(old)
            or (actual is not old and not identity_all(identity_map(identity_is, actual, old)))):
        _fail("HISTORY_CHANGED")
    _GRAPH_HISTORIES[id(graph)] = actual
    return actual


def _checkpoint_fields(graph):
    row, state = _graph_original(graph), _graph_state(graph)
    if state[4] is not None:
        raise BaseException.with_traceback(state[4], state[5])
    if state[6] != "live":
        _fail("CLOSED")
    pins = row[13]
    if (graph._seal is not _SEAL or graph._claim is not row[1]
            or graph._pins is not pins
            or graph._phase != state[6]
            or any(a is not b for a, b in zip(graph._pins, pins))
            or graph._cycles is not state[0] or graph._resources is not state[1]
            or graph._readers is not state[2] or graph._publications is not state[3]
            or graph._checkpoints is not state[9]):
        _fail("BINDING_INVALID")
    return row


def _checkpoint_registration(graph):
    row = _checkpoint_fields(graph)
    directories._directory_scope(row[3], plan=row[8], stage=row[4], continuation=row[2])
    if custody._active_journal_for_budget(row[7]) is not row[5]:
        _fail("BINDING_INVALID")
    directories._verify_directory_registration(directories._cleanup(row[3]))
    _capture_history(graph)
    row[7].clock_only()
    return row


def _checkpoint(graph):
    row = _checkpoint_fields(graph)
    directories._directory_scope(row[3], plan=row[8], stage=row[4], continuation=row[2])
    if custody._active_journal_for_budget(row[7]) is not row[5]:
        _fail("BINDING_INVALID")
    directories._verify_directories(directories._cleanup(row[3]))
    _capture_history(graph)
    row[7].clock_only()
    return row


def _new_file_io_graph(scope):
    record = directories._cleanup(scope)
    original = directories._original(record)
    value = original[3][0]
    if dict.get(_GRAPH_ATTEMPTS, id(value)) is not None:
        _fail("CLOSED")
    graph, claim = object.__new__(_FileIOCleanupGraph), object()
    callback = object.__new__(_GraphIndexCallback)
    callback._key, callback._kind, callback._claim = id(value), "graph_attempt", claim
    reference = weakref.ref(value, callback)
    _CALLBACK_PINS[id(callback)] = (callback, reference, "graph_attempt", id(value), claim)
    # Claim and strong cleanup binding precede the full business getters.
    _GRAPH_ATTEMPTS[id(value)] = (reference, claim, graph)
    row = (graph, claim, value, scope, original[3][2], original[3][3],
           original[3][7], original[3][4], original[3][1], original[3][5],
           original[4], original[5][9], reference)
    row = row + (row[2:12],)
    _GRAPH_ORIGINALS[id(graph)] = row
    _GRAPH_STATES[id(graph)] = ((), (), (), (), None, None, "live", False, False, ())
    _GRAPH_HISTORIES[id(graph)] = row[11]
    graph._seal, graph._claim, graph._pins = _SEAL, claim, row[13]
    graph._cycles = graph._resources = graph._readers = graph._publications = ()
    graph._phase = "live"
    graph._checkpoints = ()
    try:
        _checkpoint(graph)
        content._require_empty_io_budget(row[8], continuation=value)
        if scope._phase != "ready":
            _fail("CLOSED")
        return graph
    except BaseException as primary:
        first, traceback = _record_and_abort(graph, primary)
        if first is primary:
            raise
        raise BaseException.with_traceback(first, traceback)


def _resource_original(resource):
    row = dict.get(_RESOURCE_ORIGINALS, id(resource))
    if (type(resource) is not _FileResource or type(row) is not tuple
            or len(row) != 6 or row[0] is not resource):
        _fail("INVALID_ARGUMENT")
    return row


def _resource(resource):
    row = _resource_original(resource)
    _cycle(row[3])
    return _resource_fields(resource, row)


def _resource_fields(resource, row):
    if (resource._seal is not _SEAL or resource._claim is not row[1]
            or resource._pins is not row[5]
            or not any(item is resource for item in _graph_state(row[2])[1])):
        _fail("BINDING_INVALID")
    return row


def _new_resource_fields(cycle, role, row):
    if role not in ("descriptor", "creator", "live_readback", "post_readback", "held_reader", "authority_checkpoint"):
        _fail("INVALID_ARGUMENT")
    for item in _graph_state(row[2])[1]:
        prior = _resource_original(item)
        if prior[3] is cycle and prior[4] == role:
            _fail("CLOSED")
    resource = object.__new__(_FileResource)
    claim, pins = object(), (row[2], cycle, role)
    resource._seal, resource._claim, resource._pins = _SEAL, claim, pins
    _RESOURCE_ORIGINALS[id(resource)] = (resource, claim, row[2], cycle, role, pins)
    # attempt, actual owner, original slot, original owner pins, actual
    # descriptor context, returned pointer, close attempt, ordinary return.
    _RESOURCE_STATES[id(resource)] = (False, None, None, None, None, None, False, False)
    _append_graph_object(row[2], 1, resource)
    return resource


def _new_resource(cycle, role):
    row = _cycle(cycle)
    return _new_resource_fields(cycle, role, row)


def _new_resource_from_row(cycle, role, row):
    if _cycle_original(cycle) is not row:
        _fail("BINDING_INVALID")
    _checkpoint_registration(row[2])
    _cycle_fields(cycle, row)
    return _new_resource_fields(cycle, role, row)


def _bind_resource_owner(resource, owner):
    row = _resource_original(resource)
    state = _RESOURCE_STATES[id(resource)]
    # Retain the actual return before a fallible slot lookup or history check.
    _RESOURCE_STATES[id(resource)] = state[:1] + (owner,) + state[2:]
    _capture_history(row[2])
    if type(owner) is not native._Owner:
        _fail("OWNER_UNCONFIRMED")
    slot = custody._find_owner_slot(owner)
    if slot is None or slot._journal is not _graph_original(row[2])[5]:
        _fail("OWNER_UNCONFIRMED")
    custody._owner_slot(owner, slot)
    for prior in _graph_state(row[2])[1]:
        if prior is not resource and _RESOURCE_STATES[id(prior)][2] is slot:
            _fail("OWNER_REUSED")
    state = _RESOURCE_STATES[id(resource)]
    _RESOURCE_STATES[id(resource)] = state[:2] + (slot, slot._owner_pins) + state[4:]
    return owner


def _acquire_descriptor(cycle):
    resource = _new_resource(cycle, "descriptor")
    graph = _resource_original(resource)[2]
    budget = _graph_original(graph)[7]
    context = native._private_descriptor(budget)
    if type(context) is not contextlib._GeneratorContextManager:
        _fail("BINDING_INVALID")
    _RESOURCE_STATES[id(resource)] = (True, None, None, None, context, None, False, False)
    pointer = contextlib._GeneratorContextManager.__enter__(context)
    state = _RESOURCE_STATES[id(resource)]
    _RESOURCE_STATES[id(resource)] = state[:5] + (pointer,) + state[6:]
    history = _capture_history(graph)
    if type(pointer) is not int or not 0 < pointer <= custody._POINTER_MAX:
        _fail("OWNER_UNCONFIRMED")
    matches = tuple(slot for slot in history[len(_graph_original(graph)[11]):]
                    if slot._kind == custody._LOCALALLOC and slot._known
                    and not slot._close_attempted and type(slot._owner_pins) is tuple
                    and slot._owner_pins[1] == pointer)
    if len(matches) != 1:
        _fail("OWNER_UNCONFIRMED")
    _bind_resource_owner(resource, matches[0]._owner)
    _checkpoint(graph)
    return resource


def _acquire_file(cycle, role, *, descriptor=None):
    row = _cycle(cycle)
    graph, pins = row[2], row[5]
    resource = _new_resource_from_row(cycle, role, row)
    directory, name, budget = pins[4], pins[5], _graph_original(graph)[7]
    if role == "creator":
        if pins[2] != "store" or descriptor is None:
            _fail("BINDING_INVALID")
        desc = _resource(descriptor)
        desc_state = _RESOURCE_STATES[id(descriptor)]
        if desc[3] is not cycle or desc[4] != "descriptor" or desc_state[6]:
            _fail("BINDING_INVALID")
        lineage_operation = _graph_original(graph)[8]._operation
        if type(lineage_operation) is not tuple or len(lineage_operation) != 3:
            _fail("BINDING_INVALID")
        operation = authority._uuid(lineage_operation[2])
        name = name + "." + operation + ".stage"
        pointer = desc_state[5]
    elif role in ("live_readback", "post_readback", "held_reader", "authority_checkpoint"):
        if descriptor is not None:
            _fail("INVALID_ARGUMENT")
        pointer = None
    else:
        _fail("INVALID_ARGUMENT")
    native.safe._entry_name(name)
    _RESOURCE_STATES[id(resource)] = (True, None, None, None, None, None, False, False)
    owner = native._relative_open(directory.handle, name, budget, directory=False,
                                  create=role == "creator", descriptor=pointer,
                                  reader_share=7 if role == "live_readback" else 1)
    _bind_resource_owner(resource, owner)
    _checkpoint(graph)
    return resource


def _owner_handle(resource):
    row = _resource(resource)
    return _owner_handle_fields(resource, row)


def _owner_handle_fields(resource, row):
    state = _RESOURCE_STATES[id(resource)]
    owner, slot, pins = state[1:4]
    if (type(owner) is not native._Owner or type(slot) is not custody._StoreNativeSlot
            or state[6] or pins is not slot._owner_pins or pins[0] is not owner):
        _fail("OWNER_UNCONFIRMED")
    custody._owner_slot(owner, slot)
    fields = native._OWNER_DICT.__get__(owner, native._Owner)
    if dict.get(fields, "retired") is not False or dict.get(fields, "failure") is not None:
        _fail("OWNER_RETIRED")
    if row[4] == "descriptor":
        _fail("INVALID_ARGUMENT")
    return pins[1]


def _file_operand(cycle, resource):
    row = _cycle(cycle)
    original = _resource_original(resource)
    if original[3] is not cycle or original[2] is not row[2]:
        _fail("BINDING_INVALID")
    _resource_fields(resource, original)
    return row, _owner_handle_fields(resource, original)


def _require_resource_closed(resource):
    _resource_original(resource)
    state = _RESOURCE_STATES[id(resource)]
    owner, slot, pins = state[1:4]
    if (type(slot) is not custody._StoreNativeSlot or state[6] is not True
            or state[7] is not True or type(pins) is not tuple or len(pins) != 4
            or slot._owner_pins is not pins
            or pins[0] is not owner):
        _fail("CLOSE_UNCONFIRMED")
    custody._slot(slot)
    legal = (custody._signed32(slot._close_return) and slot._close_return != 0) if slot._kind == custody._HANDLE else slot._close_return is None
    if (slot._known is not True or slot._return_seen is not True
            or slot._close_attempted is not True or slot._representation_confirmed is not True
            or slot._receipt is not slot._closed_fact or not legal):
        _fail("CLOSE_UNCONFIRMED")
    return slot._closed_fact


def _close_resource(resource):
    row = _resource_original(resource)
    graph, state = row[2], _RESOURCE_STATES[id(resource)]
    if state[6]:
        _fail("CLOSED")
    _RESOURCE_STATES[id(resource)] = state[:6] + (True, False)
    first = None
    try:
        _checkpoint(graph)
    except BaseException as primary:
        first = primary
        _remember(graph, primary)
    try:
        if row[4] == "descriptor":
            context = state[4]
            if type(context) is not contextlib._GeneratorContextManager or state[5] is None:
                _fail("OWNER_UNCONFIRMED")
            traceback = None if first is None else _graph_state(graph)[5]
            suppressed = contextlib._GeneratorContextManager.__exit__(
                context, None if first is None else type(first), first, traceback)
            if suppressed is not False:
                _fail("CLOSE_UNCONFIRMED")
        else:
            if type(state[1]) is not native._Owner:
                _fail("OWNER_UNCONFIRMED")
            native._Owner.close(state[1])
        current = _RESOURCE_STATES[id(resource)]
        _RESOURCE_STATES[id(resource)] = current[:7] + (True,)
        _require_resource_closed(resource)
    except BaseException as primary:
        _remember(graph, primary)
        if first is None:
            first = primary
    try:
        _capture_history(graph)
        if first is None:
            _checkpoint(graph)
    except BaseException as primary:
        _remember(graph, primary)
        if first is None:
            first = primary
    if first is not None:
        original_first, traceback = _graph_state(graph)[4:6]
        raise BaseException.with_traceback(original_first, traceback)
    return _require_resource_closed(resource)


def _file_snapshot(cycle, resource):
    row, handle = _file_operand(cycle, resource)
    graph, pins = row[2], row[5]
    budget = _graph_original(graph)[7]
    native._security_check(handle, budget)
    native_info = native._info(handle, directory=False, budget=budget)
    info = (tuple(sorted(native_info[0].items())), native_info[1], native_info[2])
    path_stat = (pins[4].path / pins[5]).stat(follow_symlinks=False)
    native.safe._safe_stat(path_stat, directory=False)
    path_identity = native.facts.identity(path_stat)
    if (type(info[1]) is not int or info[1] != len(pins[7])
            or path_identity["byte_size"] != info[1]):
        _fail("READBACK_CHANGED")
    _checkpoint(graph)
    return info, tuple(sorted(path_identity.items()))


def _read_selected_exact(cycle, resource):
    row, handle = _file_operand(cycle, resource)
    graph, pins = row[2], row[5]
    raw = native._read_exact(handle, len(pins[7]), pins[6],
                             _graph_original(graph)[7])
    if raw != pins[7]:
        _fail("READBACK_CHANGED")
    _checkpoint(graph)
    return raw


def _reader_original(reader):
    row = dict.get(_READER_ORIGINALS, id(reader))
    if (type(reader) is not _StoreHeldReader or type(row) is not tuple
            or len(row) != 5 or row[0] is not reader):
        _fail("INVALID_ARGUMENT")
    return row


def _reader(reader):
    row = _reader_original(reader)
    _cycle(row[2])
    if reader._seal is not _SEAL or reader._claim is not row[1] or reader._pins is not row[4]:
        _fail("BINDING_INVALID")
    return row


def _open_held_reader(cycle):
    row = _cycle_original(cycle)
    graph, pins = row[2], row[5]
    state = _CYCLE_STATES[id(cycle)]
    if state[1] is not None or state[0] != ("claimed" if pins[2] == "original" else "published_closed"):
        _fail("CLOSED")
    try:
        _cycle(cycle)
        publication = None if pins[2] == "original" else _publication_receipt(cycle)
        _set_cycle_phase(cycle, "reader_opening")
        resource = _acquire_file(cycle, "held_reader")
        reader, claim = object.__new__(_StoreHeldReader), object()
        reader._seal, reader._claim, reader._pins = _SEAL, claim, (cycle, resource)
        _READER_ORIGINALS[id(reader)] = (reader, claim, cycle, resource, reader._pins)
        _READER_STATES[id(reader)] = ("opening", None, None, False, None)
        _append_graph_object(graph, 2, reader)
        state = _CYCLE_STATES[id(cycle)]
        _CYCLE_STATES[id(cycle)] = (state[0], reader, state[2])
        before, path = _file_snapshot(cycle, resource)
        if publication is not None and (before != publication[6] or path != publication[7]):
            _fail("READBACK_CHANGED")
        raw = _read_selected_exact(cycle, resource)
        if _file_snapshot(cycle, resource) != (before, path):
            _fail("READBACK_CHANGED")
        _READER_STATES[id(reader)] = ("initial_read", before, path, False, raw)
        _set_cycle_phase(cycle, "reader_held")
        return reader
    except BaseException as primary:
        first, traceback = _record_and_abort(graph, primary)
        if first is primary:
            raise
        raise BaseException.with_traceback(first, traceback)


def _initial_reader_bytes(reader):
    original = _reader_original(reader)
    try:
        row = _reader(reader)
        state = _READER_STATES[id(reader)]
        if state[0] != "initial_read" or state[4] is None:
            _fail("CLOSED")
        if _file_snapshot(row[2], row[3]) != state[1:3]:
            _fail("READBACK_CHANGED")
        # Metadata is fresh, but this accessor never performs a third body read.
        return state[4]
    except BaseException as primary:
        _abort_held_reader(original[0], primary=primary)
        raise


def _finalize_held_reader(reader):
    row = _reader_original(reader)
    graph = _cycle_original(row[2])[2]
    try:
        _reader(reader)
        state = _READER_STATES[id(reader)]
        if state[0] != "initial_read":
            _fail("CLOSED")
        _READER_STATES[id(reader)] = ("finalizing",) + state[1:]
        if _file_snapshot(row[2], row[3]) != state[1:3]:
            _fail("READBACK_CHANGED")
        raw = _read_selected_exact(row[2], row[3])
        if raw != state[4] or _file_snapshot(row[2], row[3]) != state[1:3]:
            _fail("READBACK_CHANGED")
        close_fact = _close_resource(row[3])
        receipt, claim = object.__new__(_StoreReaderCloseReceipt), object()
        pins = (reader, row[1], row[2], row[3], state[1], state[2], state[4], raw, close_fact)
        receipt._seal, receipt._claim, receipt._pins = _SEAL, claim, pins
        _CLOSE_RECEIPTS[id(receipt)] = (receipt, claim, pins)
        _READER_STATES[id(reader)] = ("closed",) + state[1:]
        _set_cycle_phase(row[2], "reader_closed")
        _CYCLE_STATES[id(row[2])] = _CYCLE_STATES[id(row[2])] + (receipt,)
        return receipt
    except BaseException as primary:
        first, traceback = _record_and_abort(graph, primary)
        if first is primary:
            raise
        raise BaseException.with_traceback(first, traceback)


def _reader_close_receipt(reader):
    row = _reader_original(reader)
    cycle = _cycle_original(row[2])
    state = _CYCLE_STATES[id(row[2])]
    if len(state) != 4 or state[0] != "reader_closed" or state[1] is not reader:
        _fail("CLOSE_UNCONFIRMED")
    receipt = state[3]
    proof = dict.get(_CLOSE_RECEIPTS, id(receipt))
    if (type(receipt) is not _StoreReaderCloseReceipt or type(proof) is not tuple
            or len(proof) != 3 or proof[0] is not receipt
            or receipt._seal is not _SEAL or receipt._claim is not proof[1]
            or receipt._pins is not proof[2] or proof[2][0] is not reader
            or proof[2][2] is not row[2] or proof[2][3] is not row[3]
            or proof[2][6] != cycle[5][7] or proof[2][7] != cycle[5][7]
            or _require_resource_closed(row[3]) is not proof[2][8]):
        _fail("CLOSE_UNCONFIRMED")
    return receipt


def _abort_held_reader(reader, *, primary):
    row = _reader_original(reader)
    graph = _cycle_original(row[2])[2]
    _record_and_abort(graph, primary)
    return None


def _append_graph_object(graph, ordinal, value):
    state = _graph_state(graph)
    names = ("_cycles", "_resources", "_readers", "_publications")
    collection = state[ordinal] + (value,)
    _GRAPH_STATES[id(graph)] = state[:ordinal] + (collection,) + state[ordinal + 1:]
    setattr(graph, names[ordinal], collection)


def _original_graph_for_continuation(value):
    row = dict.get(_GRAPH_ATTEMPTS, id(value))
    if row is None:
        return None
    if (type(row) is not tuple or len(row) != 3
            or type(row[0]) is not weakref.ReferenceType or row[0]() is not value):
        _fail("INVALID_ARGUMENT")
    graph = row[2]
    if graph is None:
        _fail("CLOSED")
    original = _graph_original(graph)
    if original[1] is not row[1] or original[2] is not value or original[12] is not row[0]:
        _fail("BINDING_INVALID")
    return graph


def _cycle_original(cycle):
    row = dict.get(_CYCLE_ORIGINALS, id(cycle))
    if (type(cycle) is not _StoreFileCycle or type(row) is not tuple
            or len(row) != 6 or row[0] is not cycle):
        _fail("INVALID_ARGUMENT")
    return row


def _cycle_fields(cycle, row):
    if (cycle._seal is not _SEAL or cycle._claim is not row[1]
            or cycle._pins is not row[5] or cycle._phase != _CYCLE_STATES[id(cycle)][0]
            or not any(item is cycle for item in _graph_state(row[2])[0])):
        _fail("BINDING_INVALID")
    actual = dict.get(directories._FILE_SELECTIONS, id(row[3]))
    before = row[4]
    if (type(actual) is not tuple or len(actual) != 4
            or any(a is not b for a, b in zip(actual[:3], before[:3]))
            or actual[3] is not True):
        _fail("SELECTION_CHANGED")
    directories._selection(row[3], _graph_original(row[2])[3])
    return row


def _cycle(cycle):
    row = _cycle_original(cycle)
    _checkpoint(row[2])
    return _cycle_fields(cycle, row)


def _set_cycle_phase(cycle, phase):
    state = _CYCLE_STATES[id(cycle)]
    _CYCLE_STATES[id(cycle)] = (phase,) + state[1:]
    cycle._phase = phase


def _begin_file_cycle(selection, *, scope):
    record = directories._cleanup(scope)
    original = directories._original(record)
    graph = _original_graph_for_continuation(original[3][0])
    if graph is None or _graph_original(graph)[3] is not scope:
        _fail("INVALID_ARGUMENT")
    state = _graph_state(graph)
    if state[4] is not None:
        raise BaseException.with_traceback(state[4], state[5])
    if state[6] != "live":
        _fail("CLOSED")
    before = dict.get(directories._FILE_SELECTIONS, id(selection))
    # The directory function owns the sole attempt, including getter failure.
    try:
        directories._consume_selection(selection, scope=scope)
        _checkpoint(graph)
        if (type(before) is not tuple or len(before) != 4 or before[3] is not False
                or before[0]() is not selection or len(before[2]) != 8):
            _fail("BINDING_INVALID")
        pins = before[2]
        if (pins[2] not in ("original", "store") or type(pins[3]) is not int
                or type(pins[5]) is not str or type(pins[6]) is not int
                or type(pins[7]) is not bytes or not 0 < len(pins[7]) <= pins[6]):
            _fail("BINDING_INVALID")
        cycle = object.__new__(_StoreFileCycle)
        cycle._seal, cycle._claim, cycle._pins, cycle._phase = _SEAL, object(), pins, "claimed"
        _CYCLE_ORIGINALS[id(cycle)] = (cycle, cycle._claim, graph, selection, before, pins)
        _CYCLE_STATES[id(cycle)] = ("claimed", None, None)
        _append_graph_object(graph, 0, cycle)
        _cycle(cycle)
        return cycle
    except BaseException as primary:
        first, traceback = _record_and_abort(graph, primary)
        if first is primary:
            raise
        raise BaseException.with_traceback(first, traceback)


def _creator_info(cycle, resource):
    row, handle = _file_operand(cycle, resource)
    budget = _graph_original(row[2])[7]
    native._security_check(handle, budget)
    result = native._info(handle, directory=False, budget=budget)
    _checkpoint(row[2])
    return tuple(sorted(result[0].items())), result[1], result[2]


def _publication_readback(cycle, role, expected_native):
    resource = _acquire_file(cycle, role)
    before, path = _file_snapshot(cycle, resource)
    if before[0] != expected_native:
        _fail("READBACK_CHANGED")
    raw = _read_selected_exact(cycle, resource)
    if _file_snapshot(cycle, resource) != (before, path):
        _fail("READBACK_CHANGED")
    close_fact = _close_resource(resource)
    return before, path, raw, resource, close_fact


def _flush_creator(resource):
    """Flush only an actual creator, using its original graph budget."""
    row = _resource_original(resource)
    if row[4] != "creator":
        _fail("INVALID_ARGUMENT")
    budget = _graph_original(row[2])[7]
    if not native._kernel.FlushFileBuffers(_owner_handle(resource)):
        chunks._native_failure(ctypes.get_last_error(), "flush", budget)
    budget.clock_only()
    return None


def _publish_missing_store_file(cycle):
    row = _cycle_original(cycle)
    graph, pins = row[2], row[5]
    state = _CYCLE_STATES[id(cycle)]
    if pins[2] != "store" or state != ("claimed", None, None):
        _fail("CLOSED")
    try:
        _set_cycle_phase(cycle, "publishing")
        _cycle(cycle)
        descriptor = _acquire_descriptor(cycle)
        creator = _acquire_file(cycle, "creator", descriptor=descriptor)
        initial = _creator_info(cycle, creator)
        if initial[1] != 0:
            _fail("CREATE_UNCONFIRMED")
        budget, raw = _graph_original(graph)[7], pins[7]
        offset = 0
        while offset < len(raw):
            _checkpoint(graph)
            offset += chunks._write_chunk(_owner_handle(creator), raw[offset:offset + chunks.CHUNK_BYTES], budget)
        _checkpoint(graph)
        _flush_creator(creator)
        creator_read = _read_selected_exact(cycle, creator)
        written = _creator_info(cycle, creator)
        if written[0] != initial[0] or written[1] != len(raw):
            _fail("READBACK_CHANGED")
        # Fixed no-replace rename, followed by metadata and other readers.
        # This new 4L contract does not read the creator body after rename.
        native._publish_owned(pins[4], _RESOURCE_STATES[id(creator)][1], pins[5], budget)
        _checkpoint(graph)
        live = _publication_readback(cycle, "live_readback", initial[0])
        after = _creator_info(cycle, creator)
        if after[0] != initial[0] or after[1] != len(raw):
            _fail("READBACK_CHANGED")
        creator_close = _close_resource(creator)
        descriptor_close = _close_resource(descriptor)
        # Neither post-close reader nor held reader can precede both closes.
        _require_resource_closed(creator)
        _require_resource_closed(descriptor)
        post = _publication_readback(cycle, "post_readback", initial[0])
        _checkpoint(graph)
        receipt, claim = object.__new__(_StorePublicationReceipt), object()
        proof = (cycle, row[3], raw, creator, descriptor, live, post[0], post[1], post,
                 creator_read, (creator_close, descriptor_close), False,
                 hashlib.sha256(raw).hexdigest(), len(raw))
        receipt._seal, receipt._claim, receipt._pins = _SEAL, claim, proof
        _PUBLICATIONS[id(receipt)] = (receipt, claim, proof)
        _append_graph_object(graph, 3, receipt)
        _CYCLE_STATES[id(cycle)] = ("published_closed", None, receipt)
        cycle._phase = "published_closed"
        _publication_receipt(cycle)
        return receipt
    except BaseException as primary:
        first, traceback = _record_and_abort(graph, primary)
        if first is primary:
            raise
        raise BaseException.with_traceback(first, traceback)


def _publication_receipt(cycle):
    row = _cycle(cycle)
    state = _CYCLE_STATES[id(cycle)]
    receipt = state[2]
    actual = dict.get(_PUBLICATIONS, id(receipt))
    if (row[5][2] != "store" or type(receipt) is not _StorePublicationReceipt
            or type(actual) is not tuple or len(actual) != 3 or actual[0] is not receipt
            or receipt._seal is not _SEAL or receipt._claim is not actual[1]
            or receipt._pins is not actual[2] or len(actual[2]) != 14):
        _fail("PUBLICATION_UNCONFIRMED")
    pins = actual[2]
    if (pins[0] is not cycle or pins[1] is not row[3] or pins[2] is not row[5][7]
            or pins[9] != pins[2] or pins[5][2] != pins[2] or pins[8][2] != pins[2]
            or pins[11] is not False or pins[12] != hashlib.sha256(pins[2]).hexdigest()
            or pins[13] != len(pins[2]) or pins[5][0][0] != pins[6][0]
            or pins[8][0] is not pins[6] or pins[8][1] is not pins[7]
            or _require_resource_closed(pins[3]) is not pins[10][0]
            or _require_resource_closed(pins[4]) is not pins[10][1]
            or _require_resource_closed(pins[5][3]) is not pins[5][4]
            or _require_resource_closed(pins[8][3]) is not pins[8][4]):
        _fail("PUBLICATION_UNCONFIRMED")
    resources = (pins[3], pins[5][3], pins[8][3])
    if len({id(_RESOURCE_STATES[id(value)][2]) for value in resources}) != 3:
        _fail("OWNER_REUSED")
    return pins


def _abort_file_io_graph(graph, *, primary):
    """No body reads/acquisitions; trusted actual owners and history only."""
    original = _graph_original(graph)
    first, traceback = _remember(graph, primary)
    state = _graph_state(graph)
    if state[6] in ("retiring", "retired"):
        return None
    _GRAPH_STATES[id(graph)] = state[:6] + ("failed",) + state[7:]
    graph._phase = "failed"
    try:
        _capture_history(graph)
    except BaseException as failure:
        _remember(graph, failure)
    try:
        history = _capture_history(graph)
    except BaseException as failure:
        _remember(graph, failure)
        history = _GRAPH_HISTORIES[id(graph)]
    for slot in reversed(history[len(original[11]):]):
        try:
            slot = custody._slot(slot)
            if slot._known and not slot._close_attempted:
                custody._failed(slot, first)
                native._close_custody(slot)
        except BaseException as failure:
            _remember(graph, failure)
    # Numeric cleanup above follows the complete original acquisition history,
    # including a returned owner whose binding validation later failed. Only
    # then unwind descriptor generators; their custody close is already spent.
    for resource in reversed(_graph_state(graph)[1]):
        row, value = _resource_original(resource), _RESOURCE_STATES[id(resource)]
        if row[4] != "descriptor" or value[4] is None or value[5] is None or value[6]:
            continue
        _RESOURCE_STATES[id(resource)] = value[:6] + (True, False)
        try:
            contextlib._GeneratorContextManager.__exit__(value[4], type(first), first, traceback)
            actual = _RESOURCE_STATES[id(resource)]
            _RESOURCE_STATES[id(resource)] = actual[:7] + (True,)
        except BaseException as failure:
            _remember(graph, failure)
    return None


def _record_and_abort(graph, primary):
    first, traceback = _remember(graph, primary)
    try:
        _abort_file_io_graph(graph, primary=first)
    except BaseException as cleanup:
        if cleanup is not first:
            runtime._note(first)
    return first, traceback


def _require_original_metadata_sets(graph):
    """An allocation/update failure cannot hide an orphan registration."""
    state = _graph_state(graph)
    cycles = tuple(row[0] for row in _CYCLE_ORIGINALS.values() if row[2] is graph)
    resources = tuple(row[0] for row in _RESOURCE_ORIGINALS.values() if row[2] is graph)
    readers = tuple(row[0] for row in _READER_ORIGINALS.values()
                    if _cycle_original(row[2])[2] is graph)
    publications = tuple(row[0] for row in _PUBLICATIONS.values()
                         if _cycle_original(row[2][0])[2] is graph)
    for actual, expected in zip((cycles, resources, readers, publications), state[:4]):
        if len(actual) != len(expected) or {id(item) for item in actual} != {id(item) for item in expected}:
            _fail("METADATA_COVERAGE_CHANGED")
    checkpoints = tuple(row[0] for row in _CHECKPOINTS.values() if _cycle_original(row[2][0])[2] is graph)
    if len(checkpoints) != len(state[9]) or {id(item) for item in checkpoints} != {id(item) for item in state[9]}:
        _fail("METADATA_COVERAGE_CHANGED")
    return None


def _require_retirable_for_continuation(value):
    graph = _original_graph_for_continuation(value)
    if graph is None:
        return None
    row, state = _graph_original(graph), _graph_state(graph)
    _require_original_metadata_sets(graph)
    history = custody._require_retirable_history(row[5])
    previous = _GRAPH_HISTORIES[id(graph)]
    if len(history) < len(previous) or any(a is not b for a, b in zip(history, previous)):
        _fail("HISTORY_CHANGED")
    _GRAPH_HISTORIES[id(graph)] = history
    if state[4] is None and value._first is None:
        # This proof covers the file collection, never the independent final
        # census. The directory/runtime integration must require that separately.
        if len(state[0]) != 40 or len(state[2]) != 40 or len(state[3]) != 29 or len(state[9]) != 4:
            _fail("FILE_COLLECTION_UNCONFIRMED")
        selections = tuple(_cycle_original(cycle)[3] for cycle in state[0])
        original = directories._original(directories._cleanup(row[3]))
        if {id(item) for item in selections} != {id(item) for item in original[5][7]}:
            _fail("FILE_COLLECTION_UNCONFIRMED")
        for reader in state[2]:
            proof = _reader_original(reader)
            if (reader._seal is not _SEAL or reader._claim is not proof[1]
                    or reader._pins is not proof[4]):
                _fail("BINDING_INVALID")
            _reader_close_receipt(reader)
        for receipt in state[3]:
            proof = _PUBLICATIONS[id(receipt)]
            if (receipt._seal is not _SEAL or receipt._claim is not proof[1]
                    or receipt._pins is not proof[2]):
                _fail("BINDING_INVALID")
            for resource in (proof[2][3], proof[2][4], proof[2][5][3], proof[2][8][3]):
                _require_resource_closed(resource)
    if state[4] is None and value._first is None:
        for receipt in state[9]:
            _checkpoint_receipt(receipt)
    proofs = tuple((slot, slot._closed_fact, slot._close_return, slot._kind,
                    slot._owner_pins) for slot in history[len(row[11]):])
    _RETIRED_PROOFS[id(graph)] = proofs
    return graph


def _retire_for_continuation(value, *, primary=None):
    graph = _original_graph_for_continuation(value)
    if graph is None:
        return None
    row, state = _graph_original(graph), _graph_state(graph)
    if state[7]:
        if state[4] is not None:
            raise BaseException.with_traceback(state[4], state[5])
        _fail("CLOSED")
    if primary is not None:
        _remember(graph, primary)
    _require_retirable_for_continuation(value)
    state = _graph_state(graph)
    cycles = tuple(_cycle_original(item) for item in state[0])
    resources = tuple(_resource_original(item) for item in state[1])
    readers = tuple(_reader_original(item) for item in state[2])
    publications = tuple(_PUBLICATIONS[id(item)] for item in state[3])
    receipts = tuple(_CLOSE_RECEIPTS[id(_CYCLE_STATES[id(item[2])][3])]
                     for item in readers if len(_CYCLE_STATES[id(item[2])]) == 4)
    # This independent, complete row set outlives partial business-table clear.
    _RETIRED_METADATA[id(graph)] = (cycles, resources, readers, publications, receipts,
        tuple(_CHECKPOINTS[id(item)] for item in state[9]))
    _GRAPH_STATES[id(graph)] = state[:6] + ("retiring", True, False, state[9])
    graph._phase = "retiring"
    # Actual effects remain recorded even if a later clear/return raises.
    try:
        for receipt in state[3] + state[9]:
            receipt._pins = ()
        for reader in state[2]:
            reader._pins = ()
            cycle_state = _CYCLE_STATES[id(_reader_original(reader)[2])]
            if len(cycle_state) == 4:
                cycle_state[3]._pins = ()
        for resource in state[1]:
            resource._pins = ()
        for cycle in state[0]:
            cycle._pins = ()
        actual = _graph_state(graph)
        _GRAPH_STATES[id(graph)] = actual[:8] + (True,) + actual[9:]
    except BaseException as failure:
        _remember(graph, failure)
        raise
    return None


def _retirement_observation_for_continuation(value):
    graph = _original_graph_for_continuation(value)
    if graph is None:
        return None
    state = _graph_state(graph)
    if state[7] is not True:
        _fail("RETIREMENT_UNCONFIRMED")
    original_rows = dict.get(_RETIRED_METADATA, id(graph))
    if type(original_rows) is not tuple or len(original_rows) != 6:
        _fail("RETIREMENT_UNCONFIRMED")
    for collection in original_rows:
        for row in collection:
            if type(row[0]._pins) is not tuple or len(row[0]._pins) != 0:
                _fail("RETIREMENT_UNCONFIRMED")
    return state[8]


def _release_retired_graph(graph):
    """No live custody getter: runtime may have already released history."""
    original, state = _graph_original(graph), _graph_state(graph)
    if state[6] != "retiring" or state[7] is not True or state[8] is not True:
        _fail("RETIREMENT_UNCONFIRMED")
    if _retirement_observation_for_continuation(original[2]) is not True:
        _fail("RETIREMENT_UNCONFIRMED")
    proofs = dict.get(_RETIRED_PROOFS, id(graph))
    if type(proofs) is not tuple:
        _fail("CLOSE_UNCONFIRMED")
    for slot, fact, close_return, kind, owner_pins in proofs:
        if (slot._receipt is not fact or slot._closed_fact is not fact
                or type(slot._close_return) is not type(close_return) or slot._close_return != close_return
                or slot._close_attempted is not True or slot._known is not True
                or slot._return_seen is not True or slot._representation_confirmed is not True
                or slot._kind is not kind or slot._owner_pins is not owner_pins):
            _fail("CLOSE_UNCONFIRMED")
    # Keep the weak spent attempt while the actual continuation remains alive.
    attempt = dict.get(_GRAPH_ATTEMPTS, id(original[2]))
    if attempt[0] is not original[12] or attempt[1] is not original[1] or attempt[2] is not graph:
        _fail("REGISTRY_CHANGED")
    if dict.get(_GRAPH_RELEASES, id(graph)) is not None:
        _fail("CLOSED")
    original_rows = _RETIRED_METADATA[id(graph)]
    identifiers = (id(original[2]), *(tuple(id(row[0]) for row in collection) for collection in original_rows))
    callback = object.__new__(_GraphIndexCallback)
    callback._kind, callback._key, callback._claim = "graph_release", id(graph), original[1]
    reference = weakref.ref(graph, callback)
    _CALLBACK_PINS[id(callback)] = (callback, reference, "graph_release", id(graph), original[1])
    _GRAPH_RELEASES[id(graph)] = (reference, original[1], "started", identifiers)
    for row in original_rows[5]:
        dict.pop(_CHECKPOINTS_BY_CYCLE, id(row[2][0]))
        dict.pop(_CHECKPOINTS, id(row[0]))
    for row in original_rows[3]:
        dict.pop(_PUBLICATIONS, id(row[0]))
    for row in original_rows[4]:
        dict.pop(_CLOSE_RECEIPTS, id(row[0]))
    for row in original_rows[2]:
        dict.pop(_READER_ORIGINALS, id(row[0]))
        dict.pop(_READER_STATES, id(row[0]))
    for row in original_rows[1]:
        dict.pop(_RESOURCE_ORIGINALS, id(row[0]))
        dict.pop(_RESOURCE_STATES, id(row[0]))
    for row in original_rows[0]:
        dict.pop(_CYCLE_ORIGINALS, id(row[0]))
        dict.pop(_CYCLE_STATES, id(row[0]))
    _GRAPH_ATTEMPTS[id(original[2])] = (attempt[0], attempt[1], None)
    graph._pins = ()
    graph._cycles = graph._resources = graph._readers = graph._publications = ()
    graph._phase = "retired"
    graph._checkpoints = ()
    dict.pop(_RETIRED_PROOFS, id(graph))
    dict.pop(_RETIRED_METADATA, id(graph))
    dict.pop(_GRAPH_HISTORIES, id(graph))
    dict.pop(_GRAPH_STATES, id(graph))
    dict.pop(_GRAPH_ORIGINALS, id(graph))
    released = _GRAPH_RELEASES[id(graph)]
    _GRAPH_RELEASES[id(graph)] = released[:2] + ("released", released[3])
    return None


def _release_observation_for_graph(graph):
    """Actual metadata effect independently of the release call's return."""
    row = dict.get(_GRAPH_RELEASES, id(graph))
    if (type(graph) is not _FileIOCleanupGraph or type(row) is not tuple or len(row) != 4
            or type(row[0]) is not weakref.ReferenceType or row[0]() is not graph
            or graph._claim is not row[1] or row[2] != "released"):
        _fail("RELEASE_UNCONFIRMED")
    identifiers = row[3]
    tables = ((_CYCLE_ORIGINALS, _CYCLE_STATES), (_RESOURCE_ORIGINALS, _RESOURCE_STATES),
              (_READER_ORIGINALS, _READER_STATES), (_PUBLICATIONS,), (_CLOSE_RECEIPTS,), (_CHECKPOINTS,))
    for keys, mappings in zip(identifiers[1:], tables):
        if any(dict.__contains__(mapping, key) for mapping in mappings for key in keys):
            _fail("RELEASE_UNCONFIRMED")
    if any(dict.__contains__(_CHECKPOINTS_BY_CYCLE, key) for key in identifiers[1]):
        _fail("RELEASE_UNCONFIRMED")
    if any(dict.__contains__(mapping, id(graph)) for mapping in
           (_GRAPH_ORIGINALS, _GRAPH_STATES, _GRAPH_HISTORIES, _RETIRED_METADATA, _RETIRED_PROOFS)):
        _fail("RELEASE_UNCONFIRMED")
    for items in (graph._pins, graph._cycles, graph._resources, graph._readers, graph._publications, graph._checkpoints):
        if type(items) is not tuple or len(items) != 0:
            _fail("RELEASE_UNCONFIRMED")
    if graph._phase != "retired":
        _fail("RELEASE_UNCONFIRMED")
    attempt = dict.get(_GRAPH_ATTEMPTS, identifiers[0])
    if attempt is not None and (type(attempt) is not tuple or len(attempt) != 3
                                or attempt[1] is not row[1] or attempt[2] is not None):
        _fail("RELEASE_UNCONFIRMED")
    return True


def _catalog_extra_original():
    import sys
    classes = (_FileIOCleanupGraph, _StoreFileCycle, _FileResource, _StoreHeldReader,
               _StorePublicationReceipt, _StoreReaderCloseReceipt, _GraphIndexCallback, _AuthorityCheckpointReceipt)
    names = ("_identity_is", "contextlib", "ctypes", "hashlib", "weakref", "authority", "chunks", "custody", "directories", "runtime", "content", "native",
             "_SEAL", "_GRAPH_ATTEMPTS", "_GRAPH_ORIGINALS", "_GRAPH_STATES", "_GRAPH_HISTORIES",
             "_CYCLE_ORIGINALS", "_CYCLE_STATES", "_RESOURCE_ORIGINALS", "_RESOURCE_STATES",
             "_READER_ORIGINALS", "_READER_STATES", "_PUBLICATIONS", "_CLOSE_RECEIPTS",
             "_RETIRED_PROOFS", "_RETIRED_METADATA", "_GRAPH_RELEASES", "_CALLBACK_PINS",
             "StoreFileIOError", "_FileIOCleanupGraph", "_StoreFileCycle", "_FileResource", "_StoreHeldReader",
             "_StorePublicationReceipt", "_StoreReaderCloseReceipt", "_GraphIndexCallback",
             "_fail", "_graph_original", "_graph_state", "_remember", "_capture_history", "_checkpoint",
             "_new_file_io_graph", "_resource_original", "_resource", "_resource_fields", "_new_resource", "_checkpoint_fields", "_checkpoint_registration", "_cycle_fields", "_new_resource_fields", "_new_resource_from_row", "_bind_resource_owner",
             "_acquire_descriptor", "_acquire_file", "_owner_handle", "_owner_handle_fields", "_file_operand", "_require_resource_closed", "_close_resource",
             "_file_snapshot", "_read_selected_exact", "_reader_original", "_reader", "_open_held_reader",
             "_initial_reader_bytes", "_finalize_held_reader", "_reader_close_receipt", "_abort_held_reader",
             "_append_graph_object", "_original_graph_for_continuation", "_cycle_original", "_cycle", "_set_cycle_phase",
             "_begin_file_cycle", "_creator_info", "_flush_creator", "_publication_readback", "_publish_missing_store_file",
             "_publication_receipt", "_abort_file_io_graph", "_record_and_abort", "_require_original_metadata_sets",
             "_require_retirable_for_continuation", "_retire_for_continuation", "_retirement_observation_for_continuation",
             "_release_retired_graph", "_release_observation_for_graph", "_catalog_extra_original",
             "_CHECKPOINTS", "_CHECKPOINTS_BY_CYCLE", "_AuthorityCheckpointReceipt", "_authority_checkpoint_cycle",
             "_checkpoint_receipt", "_held_reader_collection", "_initial_file_witness_for_scope", "_verify_initial_file_witness",
             "_require_file_collection_closed_owned")
    return ((sys.modules[__name__], names),
            (StoreFileIOError, ("__init__",)),
            (weakref, ("ref", "ReferenceType")),
            (contextlib._GeneratorContextManager, ("__enter__", "__exit__")),
            *tuple((kind, ("__slots__", "__init__", "__init_subclass__",
                            *(name for name in kind.__slots__ if name != "__weakref__"))) for kind in classes),
            (_StoreHeldReader, ("__enter__", "__exit__")), (_GraphIndexCallback, ("__call__",)))


class _AuthorityCheckpointReceipt:
    __slots__ = ('_seal', '_claim', '_pins')

    def __init__(self, *args, **kwargs):
        _fail('INVALID_ARGUMENT')

    def __init_subclass__(cls, **kwargs):
        _fail('INVALID_ARGUMENT')


def _authority_checkpoint_cycle(cycle):
    original = _cycle_original(cycle)
    graph, pins = original[2], original[5]
    if (pins[2] != 'original' or type(pins[3]) is not int or not 7 <= pins[3] <= 10
            or dict.get(_CHECKPOINTS_BY_CYCLE, id(cycle)) is not None):
        _fail('INVALID_ARGUMENT')
    try:
        _cycle(cycle)
        state = _CYCLE_STATES[id(cycle)]
        if state[0] != 'reader_held' or state[1] is None:
            _fail('CLOSED')
        held = _reader_original(state[1])
        held_state = _READER_STATES[id(state[1])]
        if held[2] is not cycle or held_state[0] != 'initial_read':
            _fail('CLOSED')
        resource = _acquire_file(cycle, 'authority_checkpoint')
        before, path = _file_snapshot(cycle, resource)
        if (before, path) != held_state[1:3]:
            _fail('READBACK_CHANGED')
        raw = _read_selected_exact(cycle, resource)
        if _file_snapshot(cycle, resource) != (before, path):
            _fail('READBACK_CHANGED')
        fact = _close_resource(resource)
        receipt, claim = object.__new__(_AuthorityCheckpointReceipt), object()
        proof = (cycle, pins[3], raw, resource, before, path, fact)
        receipt._seal, receipt._claim, receipt._pins = _SEAL, claim, proof
        _CHECKPOINTS[id(receipt)] = (receipt, claim, proof)
        _CHECKPOINTS_BY_CYCLE[id(cycle)] = receipt
        state = _graph_state(graph)
        collection = state[9] + (receipt,)
        _GRAPH_STATES[id(graph)] = state[:9] + (collection,)
        graph._checkpoints = collection
        _checkpoint_receipt(receipt)
        return receipt
    except BaseException as primary:
        first, traceback = _record_and_abort(graph, primary)
        if first is primary:
            raise
        raise BaseException.with_traceback(first, traceback)


def _checkpoint_receipt(receipt):
    row = dict.get(_CHECKPOINTS, id(receipt))
    if (type(receipt) is not _AuthorityCheckpointReceipt or type(row) is not tuple
            or len(row) != 3 or row[0] is not receipt or receipt._seal is not _SEAL
            or receipt._claim is not row[1] or receipt._pins is not row[2]):
        _fail('CHECKPOINT_UNCONFIRMED')
    pins = row[2]
    cycle = _cycle_original(pins[0])
    if (len(pins) != 7 or cycle[5][2] != 'original' or pins[1] != cycle[5][3]
            or not 7 <= pins[1] <= 10 or pins[2] != cycle[5][7]
            or dict.get(_CHECKPOINTS_BY_CYCLE, id(pins[0])) is not receipt
            or _resource_original(pins[3])[3] is not pins[0]
            or _resource_original(pins[3])[4] != 'authority_checkpoint'
            or _require_resource_closed(pins[3]) is not pins[6]):
        _fail('CHECKPOINT_UNCONFIRMED')
    return pins


def _held_reader_collection(graph, *, scope):
    row = _checkpoint(graph)
    if row[3] is not scope:
        _fail('INVALID_ARGUMENT')
    state = _graph_state(graph)
    if len(state[0]) != 40 or len(state[2]) != 40:
        _fail('FILE_COLLECTION_UNCONFIRMED')
    return state[2]


def _initial_file_witness_for_scope(scope):
    directory = directories._original(directories._cleanup(scope))
    graph = _original_graph_for_continuation(directory[3][0])
    if graph is None or _graph_original(graph)[3] is not scope:
        _fail('INVALID_ARGUMENT')
    _checkpoint(graph)
    state = _graph_state(graph)
    if len(state[0]) != 40 or len(state[2]) != 40 or len(state[3]) != 29 or len(state[9]) != 4:
        _fail('FILE_COLLECTION_UNCONFIRMED')
    witness = (graph, scope, _graph_original(graph),
               tuple(_cycle_original(item) for item in state[0]),
               tuple(_reader_original(item) for item in state[2]),
               tuple(_PUBLICATIONS[id(item)] for item in state[3]),
               tuple(_CHECKPOINTS[id(item)] for item in state[9]))
    _verify_initial_file_witness(witness, scope=scope)
    return witness


def _verify_initial_file_witness(witness, *, scope):
    if type(witness) is not tuple or len(witness) != 7 or witness[1] is not scope:
        _fail('INVALID_ARGUMENT')
    graph = witness[0]
    original = _checkpoint(graph)
    state = _graph_state(graph)
    if witness[2] is not original or original[3] is not scope:
        _fail('BINDING_INVALID')
    collections = (state[0], state[2], state[3], state[9])
    for saved, values in zip(witness[3:], collections):
        if type(saved) is not tuple or len(saved) != len(values) or any(row[0] is not item for row, item in zip(saved, values)):
            _fail('BINDING_INVALID')
    if tuple(map(len, witness[3:])) != (40, 40, 29, 4):
        _fail('FILE_COLLECTION_UNCONFIRMED')
    expected = directories._original(directories._cleanup(scope))[5][7]
    if len(expected) != 40 or any(row[3] is not item for row, item in zip(witness[3], expected)):
        _fail('FILE_COLLECTION_UNCONFIRMED')
    for cycle in witness[3]:
        if _cycle_original(cycle[0]) is not cycle:
            _fail('BINDING_INVALID')
        _cycle(cycle[0])
    for row in witness[4]:
        if _reader_original(row[0]) is not row:
            _fail('BINDING_INVALID')
        _reader(row[0])
        actual = _READER_STATES[id(row[0])]
        if actual[0] != 'initial_read' or actual[4] != _cycle_original(row[2])[5][7]:
            _fail('FILE_COLLECTION_UNCONFIRMED')
        if _file_snapshot(row[2], row[3]) != actual[1:3]:
            _fail('READBACK_CHANGED')
    for row in witness[5]:
        if dict.get(_PUBLICATIONS, id(row[0])) is not row:
            _fail('BINDING_INVALID')
        _publication_receipt(row[2][0])
    for row in witness[6]:
        if dict.get(_CHECKPOINTS, id(row[0])) is not row:
            _fail('BINDING_INVALID')
        _checkpoint_receipt(row[0])
    if tuple(row[2][1] for row in witness[6]) != (7, 8, 9, 10):
        _fail('CHECKPOINT_UNCONFIRMED')
    return None


def _require_file_collection_closed_owned(graph, *, scope):
    original = _checkpoint(graph)
    if original[3] is not scope:
        _fail('INVALID_ARGUMENT')
    state = _graph_state(graph)
    if len(state[0]) != 40 or len(state[2]) != 40 or len(state[3]) != 29 or len(state[9]) != 4:
        _fail('FILE_COLLECTION_UNCONFIRMED')
    for reader in state[2]:
        _reader_close_receipt(reader)
    for receipt in state[3]:
        _publication_receipt(_PUBLICATIONS[id(receipt)][2][0])
    for receipt in state[9]:
        _checkpoint_receipt(receipt)
    directories._final_census(scope)
    return None
