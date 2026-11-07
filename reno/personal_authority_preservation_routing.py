"""Fixed internal routing between the two actual preservation registries.

There is no registration API, version argument, caller-supplied adapter or
exception-based fallback. Registry membership only selects the original
validator; it does not replace that validator's owner/guard/phase checks.
"""

from asyncio import Task as _OWNER_TASK_TYPE
from .storage_lifecycle import _replica_operation_entry_original


def _coordinators():
    from . import personal_authority_raw_preservation as first
    from . import personal_authority_raw_preservation2 as second
    return first, second


def _fail(suffix):
    # Preserve the existing stable public error type through v1 translation.
    from .personal_authority_raw_preservation import AuthorityRawPreservationError
    raise AuthorityRawPreservationError(suffix) from None


def _owner_record_original(owners, owner):
    """Read original owner keys without hashing or comparing a Task object."""
    if type(owners) is not dict or type(owner) is not tuple or len(owner) != 3:
        _fail("OWNER_MISMATCH")
    if (type(owner[0]) is not int or owner[0] < 0
            or type(owner[1]) is not int or owner[1] < 0
            or owner[2] is not None and not issubclass(type(owner[2]), _OWNER_TASK_TYPE)):
        _fail("OWNER_MISMATCH")
    matched, found = None, False
    try:
        for registered, record in dict.items(owners):
            if (type(registered) is not tuple or len(registered) != 3
                    or type(registered[0]) is not int or registered[0] < 0
                    or type(registered[1]) is not int or registered[1] < 0
                    or registered[2] is not None
                    and not issubclass(type(registered[2]), _OWNER_TASK_TYPE)):
                _fail("OWNER_MISMATCH")
            if (registered[0] == owner[0] and registered[1] == owner[1]
                    and registered[2] is owner[2]):
                if found:
                    _fail("OWNER_MISMATCH")
                matched, found = record, True
    except RuntimeError:
        # An unrelated concurrent registry mutation is not an empty lookup.
        _fail("OWNER_MISMATCH")
    return matched


@_replica_operation_entry_original("route")
def require_no_reentry(owner):
    """Reject the actual current owner before either public entry inspects a guard."""
    from .personal_authority_replica_operation import _reject_reentry_original as reject_replica
    reject_replica(owner)
    first, second = _coordinators()
    for coordinator in (first, second):
        previous = _owner_record_original(coordinator._OWNERS, owner)
        if previous is not None:
            raise previous.budget.remember(first.AuthorityRawPreservationError("REENTRANT"))
    from .personal_authority_replica_group import _reject_reentry_original
    _reject_reentry_original(owner)


def operation_record(token, *, failure=False):
    if type(token) is not object or type(failure) is not bool:
        _fail("OWNER_MISMATCH")
    first, second = _coordinators()
    matches = []
    for coordinator in (first, second):
        original = coordinator._LIVE.get(token)
        if original is not None:
            matches.append((coordinator, original))
    if len(matches) != 1:
        _fail("OWNER_MISMATCH")
    coordinator, original = matches[0]
    if original.token is not token:
        _fail("OWNER_MISMATCH")
    validator = coordinator._failure_operation_record if failure else coordinator._operation_record
    return validator(token)


def target_module(token):
    """Select the unique actual target before its original writer validator runs."""
    from . import personal_authority_preservation_target as first
    from . import personal_authority_preservation_target2 as second
    matches = []
    for target in (first, second):
        original = target._LIVE.get(id(token))
        if original is not None:
            matches.append((target, original))
    if len(matches) != 1:
        _fail("OWNER_MISMATCH")
    target, original = matches[0]
    if type(token) is not target._TargetToken or original.token is not token:
        _fail("OWNER_MISMATCH")
    return target
