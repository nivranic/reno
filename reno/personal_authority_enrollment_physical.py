"""Registered enrollment source carrier; cached facts never grant write authority.

The original enrollment budget and actual physical directories remain owners.
Only the explicit node lease acquisition changes its raw/FD closing contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from contextvars import ContextVar
import weakref

from . import personal_authority_directory_deadline as directories
from . import personal_authority_source_deadline as nodes
from . import personal_authority_enrollment_custody as custody
from . import personal_authority_owned_ex_group as groups
from . import personal_authority_owned_ex_deadline as owned
from . import personal_authority_namespace3 as namespace
from . import personal_authority_origin_source as origin
from . import personal_preservation_files as files
from . import storage_lifecycle as lifecycle
from . import sync_media as safe

_SEAL = object()
_BUDGET_CARRIERS = {}
_FACTORY_OWNERS = {}
_ROLES = ("node_parent", "node_budget", "node_stream", "node_readonly", "node_held")
_MISSING = object()


class EnrollmentSourceError(ValueError):
    def __init__(self, suffix):
        self.code = "ENROLLMENT_SOURCE_" + suffix
        super().__init__(self.code)


def _fail(suffix):
    raise EnrollmentSourceError(suffix) from None


def _remember(carrier, failure):
    if carrier._first is None:
        carrier._first = failure
    elif carrier._first is not failure:
        try:
            BaseException.add_note(carrier._first, "ENROLLMENT_SOURCE_SECONDARY_FAILED")
        except BaseException:
            pass
    return carrier._first


class _EnrollmentSourceCarrier:
    __slots__ = ("_seal", "_claim", "_budget", "_journal", "_group", "_guard", "_profile",
                 "_paths", "_operation", "_parent", "_effective", "_scope", "_owner", "_pins",
                 "_registry_row", "_binding", "_owners", "_owner_pins", "_directories",
                 "_directory_journals", "_directory_exit_attempted", "_directory_receipts",
                 "_node_receipt", "_captures", "_capture_returns", "_capture_receipts", "_first",
                 "_phase", "_terminal_attempted", "_result", "_result_verified", "_entry_returned",
                 "_outer_owner_verified", "_consumer", "_receipt", "_fact_pin", "_guard_pin", "__weakref__")

    def __init__(self, *args, **kwargs):
        _fail("INVALID_ARGUMENT")

    def __init_subclass__(cls, **kwargs):
        _fail("INVALID_ARGUMENT")


class _CarrierFactPin:
    """Registered original owner references and the one terminal cache read.

    Native state stays in the original owners; this pin never copies that
    state or produces a second closing authority.
    """
    __slots__ = ("_seal", "claim", "directories", "directory_journals", "terminal_pin", "_pins", "__weakref__")

    def __init__(self, *args, **kwargs):
        _fail("INVALID_ARGUMENT")


class _NodeParentBinding:
    __slots__ = ("_seal", "carrier_ref", "claim", "budget", "journal", "group", "guard", "profile",
                 "paths", "operation", "parent_token", "effective_token", "scope", "directory",
                 "directory_journal", "data_path", "facade", "_pins")

    def __init__(self, *args, **kwargs):
        _fail("INVALID_ARGUMENT")

    def __init_subclass__(cls, **kwargs):
        _fail("INVALID_ARGUMENT")


class _FactoryOwnerPin:
    __slots__ = ("_seal", "binding", "role", "parent", "owner_ref", "claim", "enter_owed",
                 "exit_expression_attempted", "exit_body_started", "cleanup_started",
                 "initialization_started", "_pins")

    def __init__(self, *args, **kwargs):
        _fail("INVALID_ARGUMENT")


class _BorrowedSourceConsumer:
    __slots__ = ("_seal", "_carrier_ref", "_claim", "_result", "_attempted", "_receipt", "_pins")

    def __init__(self, *args, **kwargs):
        _fail("INVALID_ARGUMENT")


@dataclass(frozen=True, slots=True)
class BorrowedEnrollmentSourcePreconditionV1:
    format_version: int
    kind: str
    scope: str
    native_snapshot: custody.NativeOwnerJournalSnapshotV1
    capture_receipts: tuple
    physical_directory_receipts: tuple
    node_receipt: nodes.HeldFileCloseReceiptV1
    registered_enrollment_sources_terminal_complete: bool
    outer_ex_borrowed: bool
    outer_ex_closed: bool
    acquisition_coverage_proven: bool
    source_closed: bool
    write_authority: bool


def _carrier(value):
    from . import personal_authority_enrollment as enrollment
    if type(value) is not _EnrollmentSourceCarrier or value._seal is not _SEAL:
        _fail("INVALID_ARGUMENT")
    if type(value._budget) is not enrollment._Budget:
        _fail("INVALID_ARGUMENT")
    pins = value._pins
    if (type(pins) is not tuple or len(pins) != 20
            or any(current is not prior for current, prior in zip((value._budget, value._journal,
                value._group, value._guard, value._profile, value._paths, value._operation,
                value._parent, value._effective, value._scope, value._owner, value._claim,
                value._owners, value._owner_pins, value._directories, value._directory_journals,
                value._captures, value._capture_returns, value._fact_pin, value._guard_pin), pins))):
        _fail("INVALID_ARGUMENT")
    row = dict.get(_BUDGET_CARRIERS, id(value._budget))
    if (row is not value._registry_row or type(row) is not tuple or len(row) != 4
            or type(row[0]) is not weakref.ReferenceType or type(row[1]) is not weakref.ReferenceType
            or row[0]() is not value._budget or row[1]() is not value or row[2] is not value._claim
            or type(row[3]) is not weakref.ReferenceType or row[3]() is not value._fact_pin):
        _fail("INVALID_ARGUMENT")
    fact_pin = row[3]()
    if (type(fact_pin) is not _CarrierFactPin or fact_pin._seal is not _SEAL
            or fact_pin.claim is not value._claim or type(fact_pin._pins) is not tuple
            or len(fact_pin._pins) != 3
            or any(current is not prior for current, prior in zip(
                (fact_pin.claim, fact_pin.directories, fact_pin.directory_journals), fact_pin._pins))
            or type(value._directories) is not list or len(value._directories) != 4
            or type(value._directory_journals) is not list or len(value._directory_journals) != 4
            or type(fact_pin.directories) is not list or len(fact_pin.directories) != 4
            or type(fact_pin.directory_journals) is not list or len(fact_pin.directory_journals) != 4
            or type(value._directory_receipts) is not tuple or len(value._directory_receipts) != 4
            or any(value._directories[index] is not fact_pin.directories[index]
                   or value._directory_journals[index] is not fact_pin.directory_journals[index]
                   for index in range(4))):
        _fail("INVALID_ARGUMENT")
    return value


def _lineage(carrier, *, installed):
    carrier = _carrier(carrier)
    # Reuse the original sealed dormant/active guard observer at this site.
    from . import personal_authority_replica_operation as operation
    operation._observe_lifecycle_guard_original(carrier._guard)
    journal = custody._journal(carrier._journal, live=True)
    group = groups._registered(carrier._group)
    binding = owned._binding(group._binding)
    guard, guard_pin = carrier._guard, carrier._guard_pin
    if (type(guard) is not lifecycle.StorageGuard or type(guard_pin) is not tuple or len(guard_pin) != 4
            or guard._active is not True or guard._exclusive is not True
            or guard._profiles is not guard_pin[0] or guard._profile_ids is not guard_pin[1]
            or guard._locks is not guard_pin[2] or guard._owner is not guard_pin[3]
            or type(guard._profiles) is not tuple or len(guard._profiles) != 1
            or guard._profiles[0] is not carrier._profile or type(guard._profile_ids) is not frozenset
            or guard._profile_ids != frozenset((carrier._paths,))
            or type(guard._locks) is not tuple or not guard._locks
            or not groups._same_owner(guard._owner, carrier._owner)):
        _fail("INVALID_ARGUMENT")
    if (group._phase != "active" or group._profile is not carrier._profile
            or group._guard is not carrier._guard or lifecycle._CURRENT.get() is not carrier._guard
            or not groups._same_owner(carrier._owner, lifecycle._owner())
            or binding._operation is not carrier._operation or binding._parent_token is not carrier._parent
            or binding._effective_token is not carrier._effective or binding._scope_identity is not carrier._scope
            or journal._budget is not carrier._budget or journal._group is not group
            or carrier._budget._owned_ex is not group or carrier._budget._requested_deadline is not carrier._parent
            or carrier._budget._effective_deadline is not carrier._effective
            or tuple(carrier._profile.identity) != carrier._paths):
        _fail("INVALID_ARGUMENT")
    if custody._CURRENT is not custody._CONTEXT_VARIABLE or type(custody._CURRENT) is not ContextVar:
        _fail("INVALID_ARGUMENT")
    current = custody._CURRENT.get()
    if installed:
        if (current is not journal or journal._install_state != "installed"
                or journal._reset_attempted or custody._active_journal_for_budget(carrier._budget) is not journal):
            _fail("INVALID_ARGUMENT")
    elif current is not None:
        _fail("INVALID_ARGUMENT")
    return carrier


def _new_source_carrier(profile, budget, journal, *, group, guard):
    from . import personal_authority_enrollment as enrollment
    if type(budget) is not enrollment._Budget:
        _fail("INVALID_ARGUMENT")
    journal = custody._journal(journal, live=True)
    group = groups._registered(group)
    if (journal._budget is not budget or journal._group is not group or journal._guard is not guard
            or group._profile is not profile or type(guard) is not lifecycle.StorageGuard
            or group._declared_capture_count != 2
            or len(group._captures) != 2 or dict.get(_BUDGET_CARRIERS, id(budget)) is not None):
        _fail("INVALID_ARGUMENT")
    result = object.__new__(_EnrollmentSourceCarrier)
    result._seal, result._claim = _SEAL, object()
    result._budget, result._journal, result._group = budget, journal, group
    result._guard, result._profile, result._paths = guard, profile, tuple(profile.identity)
    result._operation, result._parent = journal._operation, journal._parent
    result._effective, result._scope, result._owner = journal._effective, journal._scope, journal._owner
    result._binding = None
    result._owners, result._owner_pins = {}, {}
    result._directories, result._directory_journals = [None] * 4, [None] * 4
    result._directory_exit_attempted = [False] * 4
    result._directory_receipts = (None,) * 4
    result._node_receipt = None
    result._captures = group._captures
    result._capture_returns, result._capture_receipts = [None] * 2, (None,) * 2
    result._first, result._result, result._consumer, result._receipt = None, None, None, None
    result._phase, result._terminal_attempted = "registered", False
    result._result_verified = result._entry_returned = result._outer_owner_verified = False
    result._guard_pin = (guard._profiles, guard._profile_ids, guard._locks, guard._owner)
    fact_pin = object.__new__(_CarrierFactPin)
    fact_pin._seal, fact_pin.claim = _SEAL, result._claim
    fact_pin.directories, fact_pin.directory_journals = [None] * 4, [None] * 4
    fact_pin.terminal_pin = None
    fact_pin._pins = (fact_pin.claim, fact_pin.directories, fact_pin.directory_journals)
    result._fact_pin = fact_pin
    result._pins = (budget, journal, group, guard, profile, result._paths, result._operation,
                    result._parent, result._effective, result._scope, result._owner, result._claim,
                    result._owners, result._owner_pins, result._directories, result._directory_journals,
                    result._captures, result._capture_returns, fact_pin, result._guard_pin)
    key, claim = id(budget), result._claim

    def retire(reference):
        row = dict.get(_BUDGET_CARRIERS, key)
        if (type(row) is tuple and len(row) == 4 and row[2] is claim
                and (row[0] is reference or row[1] is reference)):
            dict.pop(_BUDGET_CARRIERS, key, None)

    row = (weakref.ref(budget, retire), weakref.ref(result, retire), claim, weakref.ref(fact_pin))
    result._registry_row = row
    _BUDGET_CARRIERS[key] = row
    _lineage(result, installed=True)
    return result


def _lookup_installed_carrier(budget):
    journal = custody._active_journal_for_budget(budget)
    row = dict.get(_BUDGET_CARRIERS, id(budget))
    if journal is None:
        if row is not None:
            _fail("INVALID_ARGUMENT")
        return None
    if type(row) is not tuple or len(row) != 4 or type(row[1]) is not weakref.ReferenceType:
        _fail("INVALID_ARGUMENT")
    carrier = _lineage(row[1](), installed=True)
    if carrier._journal is not journal or carrier._budget is not budget:
        _fail("INVALID_ARGUMENT")
    return carrier


class _BorrowedNodeFacade:
    __slots__ = ("_binding",)

    @property
    def path(self):
        return self._binding.data_path

    @property
    def deadline_token(self):
        return self._binding.effective_token

    def _live(self):
        binding = _validate_node_binding(self._binding, live=True)
        binding.budget.clock_only()

    def verify(self):
        self._live()
        safe._Directory.verify(self._binding.directory)
        self._binding.budget.clock_only()

    def entry_stat(self, name):
        if type(name) is not str or name != "node-identity.json":
            _fail("INVALID_ARGUMENT")
        self.verify()
        result = safe._Directory.entry_stat(self._binding.directory, name)
        self._binding.budget.clock_only()
        return result

    def open_file(self, name):
        if type(name) is not str or name != "node-identity.json":
            _fail("INVALID_ARGUMENT")
        self.verify()
        return directories._new_enrollment_readonly_lease(self._binding)


def _new_node_binding(carrier):
    carrier = _lineage(carrier, installed=True)
    directory, journal = carrier._directories[0], carrier._directory_journals[0]
    if type(directory) is not safe._Directory or journal._owner is not directory or carrier._binding is not None:
        _fail("INVALID_ARGUMENT")
    binding = object.__new__(_NodeParentBinding)
    binding._seal, binding.carrier_ref, binding.claim = _SEAL, weakref.ref(carrier), carrier._claim
    for field, value in (("budget", carrier._budget), ("journal", carrier._journal), ("group", carrier._group),
                         ("guard", carrier._guard), ("profile", carrier._profile), ("paths", carrier._paths),
                         ("operation", carrier._operation), ("parent_token", carrier._parent),
                         ("effective_token", carrier._effective), ("scope", carrier._scope)):
        object.__setattr__(binding, field, value)
    binding.directory, binding.directory_journal, binding.data_path = directory, journal, directory.path
    facade = object.__new__(_BorrowedNodeFacade)
    facade._binding = binding
    binding.facade = facade
    binding._pins = tuple(object.__getattribute__(binding, field) for field in
                          ("budget", "journal", "group", "guard", "profile", "paths", "operation",
                           "parent_token", "effective_token", "scope", "directory", "directory_journal",
                           "data_path", "facade", "claim"))
    carrier._binding = binding
    _validate_node_binding(binding, live=True)
    return binding


def _validate_node_binding(binding, *, live=False):
    if type(binding) is not _NodeParentBinding or binding._seal is not _SEAL:
        _fail("INVALID_ARGUMENT")
    carrier = _carrier(binding.carrier_ref())
    if carrier._binding is not binding or binding.claim is not carrier._claim:
        _fail("INVALID_ARGUMENT")
    fields = ("budget", "journal", "group", "guard", "profile", "paths", "operation", "parent_token",
              "effective_token", "scope", "directory", "directory_journal", "data_path", "facade", "claim")
    if (type(binding._pins) is not tuple or len(binding._pins) != len(fields)
            or any(object.__getattribute__(binding, name) is not prior for name, prior in zip(fields, binding._pins))
            or binding.budget is not carrier._budget or binding.directory is not carrier._directories[0]
            or binding.directory_journal is not carrier._directory_journals[0]
            or type(binding.facade) is not _BorrowedNodeFacade or binding.facade._binding is not binding
            or binding.directory.path is not binding.data_path
            or binding.data_path != carrier._profile.database.parent
            or binding.directory_journal._owner is not binding.directory):
        _fail("INVALID_ARGUMENT")
    if live:
        _lineage(carrier, installed=True)
    safe._owned_directory_journal(binding.directory_journal)
    if live:
        if binding.directory_journal._phase != "active" or binding.directory_journal._exit_attempted:
            _fail("CLOSED")
    return binding


def _reject_factory_reinit(owner):
    supported = (nodes.DeadlineNodeDirectory, nodes._NodeFileBudget, nodes.DeadlineNodeStream,
                 nodes.DeadlineHeldFile, directories.DeadlineReadonlyLease)
    if type(owner) not in supported:
        _fail("INVALID_ARGUMENT")
    try:
        mode = object.__getattribute__(owner, "_enrollment_mode")
    except AttributeError:
        mode = None
    # The same-self original marker survives a missing or damaged weak row.
    # It refuses reinitialization without any live/native/parent probe.
    if type(mode) is _FactoryOwnerPin:
        try:
            seal = object.__getattribute__(mode, "_seal")
            reference = object.__getattribute__(mode, "owner_ref")
        except AttributeError:
            pass
        else:
            if seal is _SEAL and type(reference) is weakref.ReferenceType and reference() is owner:
                _fail("CLOSED")
    row = dict.get(_FACTORY_OWNERS, id(owner), _MISSING)
    if row is _MISSING:
        if mode is not None:
            _fail("INVALID_ARGUMENT")
        return None
    # An explicitly stored None is a malformed row, not an absent claim.
    if (type(row) is not tuple or len(row) != 3 or type(row[0]) is not weakref.ReferenceType
            or row[0]() is not owner or type(row[1]) is not weakref.ReferenceType
            or type(row[2]) is not object):
        _fail("INVALID_ARGUMENT")
    _fail("CLOSED")


def _register_factory_owner(binding, owner, role, *, parent=None):
    binding = _validate_node_binding(binding, live=True)
    carrier = _carrier(binding.carrier_ref())
    expected = {"node_parent": nodes.DeadlineNodeDirectory, "node_budget": nodes._NodeFileBudget,
                "node_stream": nodes.DeadlineNodeStream, "node_readonly": directories.DeadlineReadonlyLease,
                "node_held": nodes.DeadlineHeldFile}
    if (type(role) is not str or role not in _ROLES or type(owner) is not expected[role]
            or role in carrier._owners or dict.get(_FACTORY_OWNERS, id(owner)) is not None):
        _fail("INVALID_ARGUMENT")
    if role == "node_parent":
        if parent is not binding.directory:
            _fail("INVALID_ARGUMENT")
    elif role == "node_readonly":
        if parent is not binding.facade:
            _fail("INVALID_ARGUMENT")
    else:
        node_parent = dict.get(carrier._owners, "node_parent")
        if role == "node_budget" and parent is None:
            parent = node_parent
        if parent is not node_parent or type(parent) is not nodes.DeadlineNodeDirectory:
            _fail("INVALID_ARGUMENT")
    pin = object.__new__(_FactoryOwnerPin)
    pin._seal, pin.binding, pin.role, pin.parent = _SEAL, binding, role, parent
    pin.owner_ref, pin.claim = weakref.ref(owner), object()
    pin.enter_owed = pin.exit_expression_attempted = pin.exit_body_started = pin.cleanup_started = False
    pin.initialization_started = False
    pin._pins = (binding, role, parent, pin.owner_ref, pin.claim)
    key, claim = id(owner), pin.claim

    def retire(reference):
        row = dict.get(_FACTORY_OWNERS, key)
        if type(row) is tuple and len(row) == 3 and row[0] is reference and row[2] is claim:
            dict.pop(_FACTORY_OWNERS, key, None)

    row = (weakref.ref(owner, retire), weakref.ref(carrier), claim)
    carrier._owners[role], carrier._owner_pins[role] = owner, pin
    _FACTORY_OWNERS[key] = row
    return pin


def _begin_factory_initialization(owner, role):
    """Consume the one registered initializer before resetting any ledger field.

    The first initializer has no actual owner fields yet. Authenticate its
    registry claim and mode, then validate the live binding; never substitute
    the later strict actual-field gate at this partial construction boundary.
    """
    expected = {"node_parent": nodes.DeadlineNodeDirectory, "node_budget": nodes._NodeFileBudget,
                "node_stream": nodes.DeadlineNodeStream, "node_readonly": directories.DeadlineReadonlyLease,
                "node_held": nodes.DeadlineHeldFile}
    if type(role) is not str or role not in _ROLES or type(owner) is not expected[role]:
        _fail("INVALID_ARGUMENT")
    row = dict.get(_FACTORY_OWNERS, id(owner))
    try:
        mode = object.__getattribute__(owner, "_enrollment_mode")
    except AttributeError:
        mode = None
    if row is None:
        if mode is not None:
            _fail("INVALID_ARGUMENT")
        return None
    if (type(row) is not tuple or len(row) != 3 or type(row[0]) is not weakref.ReferenceType
            or row[0]() is not owner or type(row[1]) is not weakref.ReferenceType
            or type(mode) is not _FactoryOwnerPin or mode._seal is not _SEAL):
        _fail("INVALID_ARGUMENT")
    carrier = _carrier(row[1]())
    if (mode.role != role or dict.get(carrier._owners, role) is not owner
            or dict.get(carrier._owner_pins, role) is not mode or mode.owner_ref() is not owner
            or mode.claim is not row[2] or mode.binding is not carrier._binding
            or type(mode._pins) is not tuple or len(mode._pins) != 5
            or any(current is not prior for current, prior in zip(
                (mode.binding, mode.role, mode.parent, mode.owner_ref, mode.claim), mode._pins))):
        _fail("INVALID_ARGUMENT")
    if mode.initialization_started is not False:
        _fail("CLOSED")
    mode.initialization_started = True
    _validate_node_binding(mode.binding, live=True)
    return mode


def _require_factory_owner(owner, role):
    expected = {"node_parent": nodes.DeadlineNodeDirectory, "node_budget": nodes._NodeFileBudget,
                "node_stream": nodes.DeadlineNodeStream, "node_readonly": directories.DeadlineReadonlyLease,
                "node_held": nodes.DeadlineHeldFile}
    if type(role) is not str or role not in _ROLES or type(owner) is not expected[role]:
        _fail("INVALID_ARGUMENT")
    row = dict.get(_FACTORY_OWNERS, id(owner))
    try:
        mode = object.__getattribute__(owner, "_enrollment_mode")
    except AttributeError:
        mode = None
    if row is None:
        if mode is not None:
            _fail("INVALID_ARGUMENT")
        return None
    if (type(row) is not tuple or len(row) != 3 or type(row[0]) is not weakref.ReferenceType
            or row[0]() is not owner or type(row[1]) is not weakref.ReferenceType
            or type(mode) is not _FactoryOwnerPin or mode._seal is not _SEAL):
        _fail("INVALID_ARGUMENT")
    carrier = _carrier(row[1]())
    if (type(role) is not str or role not in _ROLES or mode.role != role
            or dict.get(carrier._owners, role) is not owner or dict.get(carrier._owner_pins, role) is not mode
            or mode.owner_ref() is not owner or mode.claim is not row[2]
            or type(mode._pins) is not tuple or len(mode._pins) != 5
            or any(current is not prior for current, prior in zip(
                (mode.binding, mode.role, mode.parent, mode.owner_ref, mode.claim), mode._pins))
            or mode.binding is not carrier._binding or mode.initialization_started is not True):
        _fail("INVALID_ARGUMENT")
    _validate_node_binding(mode.binding)
    original, failure = _factory_cleanup_pin(owner)
    if original is not mode or failure is not None:
        if failure is not None:
            raise failure
        _fail("INVALID_ARGUMENT")
    if role == "node_readonly":
        name = object.__getattribute__(owner, "_name")
        if type(name) is not str or name != "node-identity.json":
            _fail("INVALID_ARGUMENT")
    elif role == "node_stream" and object.__getattribute__(owner, "_phase") == nodes._ACTIVE:
        if (object.__getattribute__(owner, "_lease") is None
                or type(object.__getattribute__(owner, "_buffer")) is not nodes.io.BufferedReader
                or type(object.__getattribute__(owner, "_raw")) is not nodes.io.FileIO
                or type(object.__getattribute__(owner, "_expected_fd")) is not int):
            _fail("INVALID_ARGUMENT")
    elif role == "node_held" and object.__getattribute__(owner, "_phase") == nodes._ACTIVE:
        stream = object.__getattribute__(owner, "_stream")
        if type(stream) is not nodes.DeadlineNodeStream or stream is not dict.get(carrier._owners, "node_stream"):
            _fail("INVALID_ARGUMENT")
    elif role == "node_budget":
        count = object.__getattribute__(owner, "count")
        if type(count) is not int or not 0 <= count <= 1:
            _fail("INVALID_ARGUMENT")
    return mode


def _require_factory_owner_live(owner, role):
    pin = _require_factory_owner(owner, role)
    if pin is not None:
        _validate_node_binding(pin.binding, live=True)
    return pin


def _pin_for_registered_owner(owner):
    try:
        pin = object.__getattribute__(owner, "_enrollment_mode")
    except AttributeError:
        _fail("INVALID_ARGUMENT")
    if type(pin) is not _FactoryOwnerPin:
        _fail("INVALID_ARGUMENT")
    return _require_factory_owner(owner, pin.role)


def _factory_cleanup_pin(owner):
    """Recover registered debt without using damaged mode or a live provider.

    Return failure as data so the caller retains P and attempts independently
    confirmed original ledger obligations. No native state is copied here.
    """
    try:
        row = dict.get(_FACTORY_OWNERS, id(owner))
        supported = (nodes.DeadlineNodeDirectory, nodes._NodeFileBudget, nodes.DeadlineNodeStream,
                     nodes.DeadlineHeldFile, directories.DeadlineReadonlyLease)
        if type(owner) not in supported:
            return None, EnrollmentSourceError("INVALID_ARGUMENT")
        try:
            mode = object.__getattribute__(owner, "_enrollment_mode")
        except AttributeError:
            mode = None
        if row is None:
            return (None, None) if mode is None else (None, EnrollmentSourceError("INVALID_ARGUMENT"))
        if (type(row) is not tuple or len(row) != 3 or type(row[0]) is not weakref.ReferenceType
                or row[0]() is not owner or type(row[1]) is not weakref.ReferenceType):
            return None, EnrollmentSourceError("INVALID_ARGUMENT")
        carrier = row[1]()
        if type(carrier) is not _EnrollmentSourceCarrier or carrier._seal is not _SEAL:
            return None, EnrollmentSourceError("INVALID_ARGUMENT")
        matches = [(role, dict.get(carrier._owner_pins, role)) for role in _ROLES
                   if dict.get(carrier._owners, role) is owner]
        if len(matches) != 1:
            return None, EnrollmentSourceError("INVALID_ARGUMENT")
        role, pin = matches[0]
        if (type(pin) is not _FactoryOwnerPin or pin._seal is not _SEAL or pin.claim is not row[2]
                or pin.owner_ref() is not owner or pin.binding is not carrier._binding
                or type(pin._pins) is not tuple or len(pin._pins) != 5
                or any(current is not prior for current, prior in zip(
                    (pin.binding, pin.role, pin.parent, pin.owner_ref, pin.claim), pin._pins))):
            return None, EnrollmentSourceError("INVALID_ARGUMENT")
        binding = pin.binding
        if type(binding) is not _NodeParentBinding or binding._seal is not _SEAL or binding.claim is not carrier._claim:
            return None, EnrollmentSourceError("INVALID_ARGUMENT")
        if role == "node_parent":
            valid = (pin.parent is binding.directory
                     and object.__getattribute__(owner, "_directory") is binding.facade
                     and object.__getattribute__(owner, "_budget") is binding.budget)
        elif role == "node_readonly":
            valid = pin.parent is binding.facade and object.__getattribute__(owner, "_directory") is binding.facade
        elif role == "node_budget":
            valid = pin.parent is dict.get(carrier._owners, "node_parent") and object.__getattribute__(owner, "parent") is binding.budget
        else:
            valid = pin.parent is dict.get(carrier._owners, "node_parent") and object.__getattribute__(owner, "_node") is pin.parent
            if role == "node_held":
                valid = valid and object.__getattribute__(owner, "_budget") is dict.get(carrier._owners, "node_budget")
                lower = object.__getattribute__(owner, "_stream")
                valid = valid and (lower is None or lower is dict.get(carrier._owners, "node_stream"))
            elif role == "node_stream":
                lower = object.__getattribute__(owner, "_lease")
                valid = valid and (lower is None or lower is dict.get(carrier._owners, "node_readonly"))
                buffer = object.__getattribute__(owner, "_buffer")
                raw = object.__getattribute__(owner, "_raw")
                fd = object.__getattribute__(owner, "_expected_fd")
                if lower is None:
                    valid = valid and buffer is None and raw is None and fd is None
                else:
                    valid = valid and type(lower) is directories.DeadlineReadonlyLease
                    if buffer is not None:
                        valid = (valid and type(buffer) is nodes.io.BufferedReader
                                 and buffer is object.__getattribute__(lower, "_buffer")[0])
                    if raw is not None:
                        valid = (valid and type(raw) is nodes.io.FileIO and raw.closefd is False
                                 and raw is object.__getattribute__(lower, "_raw")[0]
                                 and (buffer is None or buffer.raw is raw))
                    if fd is not None:
                        valid = (valid and type(fd) is int and fd >= 0
                                 and fd == object.__getattribute__(lower, "_fd")[0])
        if not valid:
            return None, EnrollmentSourceError("INVALID_ARGUMENT")
        return pin, None if mode is pin else EnrollmentSourceError("INVALID_ARGUMENT")
    except BaseException as failure:
        return None, failure


def _mark_enter_owed(owner):
    pin = _pin_for_registered_owner(owner)
    if pin.enter_owed or pin.cleanup_started or pin.exit_expression_attempted:
        _fail("CLOSED")
    pin.enter_owed = True


def _begin_owner_cleanup(owner):
    # Contract: do not raise before independent, known original ledger closes.
    try:
        pin, failure = _factory_cleanup_pin(owner)
        if pin is not None:
            pin.enter_owed = False
            pin.cleanup_started = True
        return failure
    except BaseException as failure:
        return failure
    return None


def _reserve_owner_exit(owner):
    pin, failure = _factory_cleanup_pin(owner)
    if pin is None:
        if failure is not None:
            raise failure
        _fail("INVALID_ARGUMENT")
    if pin.exit_expression_attempted or pin.exit_body_started:
        _fail("CLOSED")
    if pin.cleanup_started or not pin.enter_owed:
        return None
    pin.exit_expression_attempted = True
    return pin.claim


def _reserve_lower_exit(upper, lower):
    lower_pin, failure = _factory_cleanup_pin(lower)
    if lower_pin is None:
        if failure is not None:
            raise failure
        _fail("INVALID_ARGUMENT")
    if type(upper) is _EnrollmentSourceCarrier:
        if (lower_pin.role != "node_held" or _carrier(upper)._binding is not lower_pin.binding
                or dict.get(upper._owners, "node_held") is not lower):
            _fail("INVALID_ARGUMENT")
    else:
        upper_pin, failure = _factory_cleanup_pin(upper)
        if upper_pin is None:
            if failure is not None:
                raise failure
            _fail("INVALID_ARGUMENT")
        if upper_pin.binding is not lower_pin.binding:
            _fail("INVALID_ARGUMENT")
        relation = (upper_pin.role, lower_pin.role)
        field = "_stream" if relation == ("node_held", "node_stream") else "_lease" if relation == ("node_stream", "node_readonly") else None
        if field is None or object.__getattribute__(upper, field) is not lower:
            _fail("INVALID_ARGUMENT")
    if lower_pin.cleanup_started:
        return None
    return _reserve_owner_exit(lower)


def _begin_claimed_exit(lower, claim):
    pin, failure = _factory_cleanup_pin(lower)
    if pin is None:
        if failure is not None:
            raise failure
        _fail("INVALID_ARGUMENT")
    if claim is not pin.claim or not pin.exit_expression_attempted or pin.exit_body_started:
        _fail("CLOSED")
    pin.exit_body_started = True


class _PhysicalDirectoryScope:
    __slots__ = ("_carrier", "_ordinal", "_directory", "_journal")

    def __enter__(self):
        try:
            result = safe._Directory.__enter__(self._directory)
            if result is not self._directory:
                _fail("INVALID_RESULT")
            return result
        except BaseException as primary:
            try:
                self._exit_once(type(primary), primary, BaseException.__dict__["__traceback__"].__get__(primary))
            except BaseException as failure:
                _remember(self._carrier, primary)
                _remember(self._carrier, failure)
            raise

    def _exit_once(self, kind, primary, tb):
        carrier, ordinal, journal = self._carrier, self._ordinal, self._journal
        # Actual safe journal, not a fabricated new deadline hook.
        if carrier._directory_exit_attempted[ordinal] or journal._slot._close_consumed:
            return None
        if journal._phase != "active" or journal._enter_returned is not True:
            return None
        carrier._directory_exit_attempted[ordinal] = True
        return safe._Directory.__exit__(self._directory, kind, primary, tb)

    def __exit__(self, kind, primary, tb):
        try:
            return self._exit_once(kind, primary, tb)
        except BaseException as failure:
            if primary is None:
                raise
            _remember(self._carrier, primary)
            _remember(self._carrier, failure)
            return None


class _NodeHeldScope:
    __slots__ = ("_carrier", "_held")

    def __enter__(self):
        try:
            result = nodes.DeadlineHeldFile.__enter__(self._held)
            if result is not self._held:
                _fail("INVALID_RESULT")
            return result
        except BaseException as primary:
            try:
                claim = _reserve_lower_exit(self._carrier, self._held)
                if claim is not None:
                    nodes._exit_enrollment_held_file(self._held, type(primary), primary,
                        BaseException.__dict__["__traceback__"].__get__(primary), claim=claim)
            except BaseException as failure:
                _remember(self._carrier, primary)
                _remember(self._carrier, failure)
            raise

    def __exit__(self, kind, primary, tb):
        try:
            claim = _reserve_lower_exit(self._carrier, self._held)
            if claim is not None:
                return nodes._exit_enrollment_held_file(self._held, kind, primary, tb, claim=claim)
            return None
        except BaseException as failure:
            if primary is None:
                raise
            _remember(self._carrier, primary)
            _remember(self._carrier, failure)
            return None


class _InnerTerminalScope:
    __slots__ = ("_carrier",)

    def __enter__(self):
        return self

    def __exit__(self, _kind, primary, _tb):
        try:
            _seal_inner_terminal(self._carrier, primary=primary)
        except BaseException as failure:
            if primary is None:
                raise
            _remember(self._carrier, primary)
            _remember(self._carrier, failure)
        return None


def _open_physical(profile, stack, budget):
    carrier = _lookup_installed_carrier(budget)
    if carrier is None or profile is not carrier._profile or carrier._phase != "registered":
        _fail("INVALID_ARGUMENT")
    carrier._phase = "acquiring"
    terminal = object.__new__(_InnerTerminalScope)
    terminal._carrier = carrier
    stack.enter_context(terminal)
    roots = {}
    paths = (profile.database.parent, profile.media, profile.reports, profile.checkpoints)
    for ordinal, (name, path) in enumerate(zip(namespace._ROOTS, paths)):
        budget.clock_only()
        journal = safe._new_owned_directory_journal(carrier._group._binding, root_ordinal=ordinal)
        directory = object.__new__(safe._Directory)
        carrier._directories[ordinal], carrier._directory_journals[ordinal] = directory, journal
        carrier._fact_pin.directories[ordinal], carrier._fact_pin.directory_journals[ordinal] = directory, journal
        safe._Directory.__init__(directory, path, base=path, _owned_close_journal=journal)
        context = object.__new__(_PhysicalDirectoryScope)
        context._carrier, context._ordinal, context._directory, context._journal = carrier, ordinal, directory, journal
        stack.enter_context(context)
        budget.clock_only()
        roots[name] = {"path": lifecycle._key(path), "identity": files.directory_identity(directory)}
    binding = _new_node_binding(carrier)
    parent = nodes._new_enrollment_node_directory(binding)
    held = nodes._new_enrollment_held_file(parent)
    node_scope = object.__new__(_NodeHeldScope)
    node_scope._carrier, node_scope._held = carrier, held
    node = stack.enter_context(node_scope)
    raw = node.bytes(namespace.origin_contract.MAX_NODE_BYTES)
    # Evidence has the same original schema and original node-id parser.
    physical = {"profile_paths": list(profile.identity), "roots": roots}
    evidence = {"node_id": origin._node_id(raw), **origin._file_evidence(node)}
    return tuple(carrier._directories), node, physical, evidence


def _seal_inner_terminal(carrier, *, primary=None):
    carrier = _carrier(carrier)
    if carrier._terminal_attempted:
        _fail("CLOSED")
    carrier._terminal_attempted = True
    if primary is not None:
        _remember(carrier, primary)
    receipts = []
    for directory in carrier._directories:
        try:
            receipts.append(None if directory is None else safe._Directory.owned_close_receipt.__get__(directory, safe._Directory))
        except BaseException as failure:
            _remember(carrier, failure)
            receipts.append(None)
    carrier._directory_receipts = tuple(receipts)
    held = dict.get(carrier._owners, "node_held")
    try:
        carrier._node_receipt = None if held is None else nodes.DeadlineHeldFile.close_receipt.__get__(held, nodes.DeadlineHeldFile)
    except BaseException as failure:
        _remember(carrier, failure)
        carrier._node_receipt = None
    carrier._fact_pin.terminal_pin = (tuple(carrier._directories), tuple(carrier._directory_journals),
                                      carrier._directory_receipts, carrier._node_receipt)
    carrier._phase = "inner_terminal_cached"
    if carrier._first is not None and carrier._first is not primary:
        raise carrier._first


def _record_capture_context_return(carrier, capture, *, ordinal):
    carrier = _lineage(carrier, installed=True)
    capture = groups._capture_registered(capture)
    if (type(ordinal) is not int or not 0 <= ordinal < 2 or carrier._captures is not carrier._group._captures
            or carrier._captures[ordinal] is not capture or capture._group is not carrier._group
            or carrier._capture_returns[ordinal] is not None or capture._ordinal != ordinal
            or capture._phase != "retired" or not capture._completed or not capture._finish_attempted
            or capture._first is not None):
        _fail("INVALID_RESULT")
    carrier._capture_returns[ordinal] = capture


def _record_result_success(carrier, result):
    from . import personal_authority_enrollment as enrollment
    carrier = _lineage(carrier, installed=True)
    if (carrier._phase != "inner_terminal_cached" or carrier._result_verified
            or type(result) is not enrollment.AuthorityPrefixFiles or carrier._first is not None):
        _fail("INVALID_RESULT")
    carrier._result, carrier._result_verified = result, True


def _require_entry_return(carrier, result):
    carrier = _lineage(carrier, installed=True)
    if not carrier._result_verified or carrier._result is not result or carrier._entry_returned:
        _fail("INVALID_RESULT")
    carrier._entry_returned = True


def _record_outer_owner_success(carrier):
    carrier = _lineage(carrier, installed=True)
    if not carrier._entry_returned or carrier._outer_owner_verified:
        _fail("INVALID_RESULT")
    carrier._outer_owner_verified = True


def _register_borrowed_source_consumer(carrier, *, result):
    carrier = _carrier(carrier)
    if carrier._consumer is not None:
        _fail("CLOSED")
    consumer = object.__new__(_BorrowedSourceConsumer)
    consumer._seal, consumer._carrier_ref, consumer._claim = _SEAL, weakref.ref(carrier), object()
    consumer._result, consumer._attempted, consumer._receipt = result, False, None
    consumer._pins = (consumer._carrier_ref, consumer._claim, result)
    carrier._consumer = consumer
    return consumer


def _capture_source_facts_complete(carrier, capture, receipt):
    if (type(receipt) is not groups.CaptureLineageReceiptV1 or capture._receipt is not receipt
            or capture._first is not None or capture._finish_attempted is not True or capture._completed is not True
            or receipt.invocation_registered is not True or receipt.context_completed is not True
            or receipt.failed is not False or receipt.normal_exit is not True or receipt.outer_ex_borrowed is not True
            or receipt.owned_outer_ex_closed is not False or receipt.source_closed is not False):
        _fail("INVALID_RESULT")
    facts, roles = receipt.source_close_facts, groups._SOURCE_ROLES
    if type(facts) is not tuple or len(facts) != 6 or type(receipt.lower_fact_reads_attempted) is not tuple or len(receipt.lower_fact_reads_attempted) != 6:
        _fail("INVALID_RESULT")
    if (not groups._source_directory_terminal(facts[0]) or not groups._source_image_terminal(facts[1])
            or not groups._source_held_terminal(facts[2]) or not groups._source_node_terminal(facts[5])):
        _fail("INVALID_RESULT")
    fingerprint = capture._image_pin.returned_fingerprint
    for index, suffix in ((3, "-wal"), (4, "-journal")):
        exists = fingerprint["files"][suffix] is not None
        if exists:
            if capture._sources[index] is None or not groups._source_held_terminal(facts[index]):
                _fail("INVALID_RESULT")
        elif capture._sources[index] is not None or capture._source_pins[index] is not None or facts[index] is not None:
            _fail("INVALID_RESULT")
    for index in range(len(roles)):
        if receipt.lower_fact_reads_attempted[index] is not (capture._sources[index] is not None):
            _fail("INVALID_RESULT")


def _issue_borrowed_source_precondition(consumer, *, native_snapshot):
    # Only the minimum self/claim/registry recognition precedes attempt consumption.
    if type(consumer) is not _BorrowedSourceConsumer or consumer._seal is not _SEAL:
        _fail("INVALID_ARGUMENT")
    if (type(consumer._carrier_ref) is not weakref.ReferenceType
            or type(consumer._pins) is not tuple or len(consumer._pins) != 3
            or any(current is not prior for current, prior in zip(
                (consumer._carrier_ref, consumer._claim, consumer._result), consumer._pins))):
        _fail("INVALID_ARGUMENT")
    carrier = consumer._carrier_ref()
    if type(carrier) is not _EnrollmentSourceCarrier or carrier._consumer is not consumer:
        _fail("INVALID_ARGUMENT")
    if consumer._attempted:
        _fail("CLOSED")
    consumer._attempted = True
    carrier = _lineage(carrier, installed=False)
    if (carrier._first is not None or carrier._budget.callback_primary is not None
            or not carrier._terminal_attempted or not carrier._result_verified or not carrier._entry_returned
            or not carrier._outer_owner_verified or carrier._result is not consumer._result):
        _fail("INVALID_RESULT")
    journal = carrier._journal
    actual_native = custody._JOURNAL_RECEIPT.__get__(journal, custody._NativeOwnerJournal)
    if (type(native_snapshot) is not custody.NativeOwnerJournalSnapshotV1 or native_snapshot is not actual_native
            or not journal._reset_attempted or not journal._seal_attempted
            or native_snapshot.install_state != "installed" or native_snapshot.reset_state != "reset"
            or native_snapshot.first_present is not False or native_snapshot.unknown_count != 0
            or native_snapshot.all_registered_native_closed is not True
            or native_snapshot.source_closed is not False or native_snapshot.write_authority is not False
            or native_snapshot.acquisition_coverage_proven is not False):
        _fail("INVALID_RESULT")
    if len(native_snapshot.facts) != len(journal._slots) or any(fact is not slot._receipt for fact, slot in zip(native_snapshot.facts, journal._slots)):
        _fail("INVALID_RESULT")
    if carrier._captures is not carrier._group._captures or len(carrier._captures) != 2:
        _fail("INVALID_RESULT")
    identities, states, restrictions, capture_receipts = [], [], [], []
    for ordinal, capture in enumerate(carrier._captures):
        if carrier._capture_returns[ordinal] is not capture:
            _fail("INVALID_RESULT")
        registered, receipt = groups._capture_terminal_lineage_row(carrier._group, capture, ordinal,
            prior_rows=(identities, states, restrictions))
        _capture_source_facts_complete(carrier, registered, receipt)
        capture_receipts.append(receipt)
    terminal_pin = carrier._fact_pin.terminal_pin
    if (type(terminal_pin) is not tuple or len(terminal_pin) != 4
            or type(terminal_pin[0]) is not tuple or len(terminal_pin[0]) != 4
            or type(terminal_pin[1]) is not tuple or len(terminal_pin[1]) != 4
            or carrier._directory_receipts is not terminal_pin[2] or carrier._node_receipt is not terminal_pin[3]):
        _fail("INVALID_RESULT")
    for ordinal in range(4):
        directory, journal, receipt = (carrier._directories[ordinal], carrier._directory_journals[ordinal],
                                       carrier._directory_receipts[ordinal])
        if directory is not terminal_pin[0][ordinal] or journal is not terminal_pin[1][ordinal]:
            _fail("INVALID_RESULT")
        if (type(directory) is not safe._Directory or journal._owner is not directory
                or directory._owned_close_journal is not journal or journal._binding is not carrier._group._binding
                or journal._root_ordinal != ordinal or type(receipt) is not safe.OwnedDirectoryCloseReceiptV1
                or receipt.root_ordinal != ordinal or receipt.phase != "retired" or receipt.normal_exit is not True
                or receipt.failed is not False or receipt.enter_returned is not True or receipt.exit_attempted is not True
                or not receipt.handle_states or any(state != "closed" for state in receipt.handle_states)
                or receipt.close_consumed is not True or receipt.close_return_legal is not True):
            _fail("INVALID_RESULT")
    _validate_node_binding(carrier._binding)
    for role in _ROLES:
        owner = dict.get(carrier._owners, role)
        if owner is None or _require_factory_owner(owner, role) is None:
            _fail("INVALID_RESULT")
    if not groups._source_node_terminal(carrier._node_receipt):
        _fail("INVALID_RESULT")
    carrier._budget.clock_only()
    receipt = BorrowedEnrollmentSourcePreconditionV1(1, "borrowed_enrollment_source_completion_precondition",
        "registered_enrollment_captures_native_physical_node_only", native_snapshot, tuple(capture_receipts),
        carrier._directory_receipts, carrier._node_receipt, True, True, False, False, False, False)
    consumer._receipt, carrier._receipt = receipt, receipt
    carrier._capture_receipts = tuple(capture_receipts)
    carrier._phase = "borrowed_precondition_issued"
    return receipt
