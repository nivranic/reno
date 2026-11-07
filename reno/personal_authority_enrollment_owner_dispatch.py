"""Two fixed native-owner custody families, with no injectable dispatcher."""
from . import personal_authority_enrollment_custody as source
from . import personal_authority_enrollment_store_custody as store

_HANDLE, _LOCALALLOC = source._HANDLE, source._LOCALALLOC
_POINTER_MAX = source._POINTER_MAX
_signed32 = source._signed32


def _family(slot):
    if slot is None or type(slot) is source._NativeSlot:
        return source
    if type(slot) is store._StoreNativeSlot:
        return store
    source._fail("INVALID_ARGUMENT")


def _reserve(budget, role, carrier):
    if store._selected_for_budget(budget):
        if source._CURRENT is not source._CONTEXT_VARIABLE or source._CURRENT.get() is not None:
            source._fail("CURRENT_JOURNAL_UNKNOWN")
        return store._reserve(budget, role, carrier)
    return source._reserve(budget, role, carrier)


def _slot(value):
    return _family(value)._slot(value)


def _returned_bool(slot, raw):
    return _family(slot)._returned_bool(slot, raw)


def _returned_status(slot, raw):
    return _family(slot)._returned_status(slot, raw)


def _returned_handle(slot, raw):
    return _family(slot)._returned_handle(slot, raw)


def _returned_relative(slot, raw, status):
    return _family(slot)._returned_relative(slot, raw, status)


def _bind_owner(slot, owner, value, closer):
    return _family(slot)._bind_owner(slot, owner, value, closer)


def _begin_close(slot, kind):
    return _family(slot)._begin_close(slot, kind)


def _closed(slot, raw):
    return _family(slot)._closed(slot, raw)


def _failed(slot, failure):
    return _family(slot)._failed(slot, failure)


def _find_owner_slot(owner):
    from . import personal_authority_namespace3_io as native
    fields = native._OWNER_DICT.__get__(owner, native._Owner)
    source_row = dict.get(source._OWNER_SLOTS, id(owner))
    store_row = dict.get(store._OWNER_SLOTS, id(owner))
    if source_row is not None and store_row is not None:
        source._fail("INVALID_ARGUMENT")
    marker = dict.get(fields, "_custody_slot")
    if store_row is not None or type(marker) is store._StoreNativeSlot:
        return store._find_owner_slot(owner)
    return source._find_owner_slot(owner)


def _owner_slot(owner, slot):
    return _family(slot)._owner_slot(owner, slot)
