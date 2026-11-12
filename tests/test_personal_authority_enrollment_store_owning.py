"""r937 publication owning-route test candidate: source only, never collect now.

Requires reviewed r924 application, rechecked r926 baseline, complete fresh
source/test reviews and separate bounded QA admission. These 94 fixed IDs do
not replace the full r919 matrix. Consumer seams observe the existing Python
consumer boundaries; controlled faults are not native failure evidence.
"""

from contextlib import closing
import hashlib
import importlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import uuid

import pytest


if os.name != "nt":
    pytest.skip("r937 Store owning-route requires Windows native custody", allow_module_level=True)


OPERATION_MODULE = "reno.personal_authority_replica_operation"
ROOT_NAME = ".reno-personal-authority-namespace-v3"
STORE_PREFIX = ".reno-personal-authority-sql-store-v1-"
EXPECTED_FIELDS = (
    "expected_origin_reservation_sha256",
    "expected_origin_observation_sha256",
    "expected_namespace_reservation_sha256",
    "expected_root_seal_sha256",
    "expected_header_sha256",
)
ORIGINAL_NAMES = (
    ".reno-personal-authority-origin-v1-reservation.json",
    ".reno-personal-authority-origin-v1-observation.json",
    ".reno-personal-authority-namespace-v3-reservation.json",
    ".reno-personal-authority-namespace-v3-root-seal.json",
    ".reno-personal-authority-namespace-v3-header.json",
    ".reno-personal-authority-enrollment-binding.json",
    ".reno-personal-authority-enrollment-prefix.json",
    ".reno-personal-authority-origin-init-intent.json",
    ".reno-personal-authority-local-contract.json",
    ".reno-personal-authority-init-seal.json",
    ".reno-personal-authority-genesis-grant.json",
)
ORIGINAL_MAXIMA = (
    16384, 65536, 32768, 16384, 16384, 16384,
    16384, 16384, 16384, 16384, 16384,
)
ORIGINAL_LOCATIONS = tuple(
    ("data_parent" if index in (0, 1, 2, 4) else "namespace_root", name)
    for index, name in enumerate(ORIGINAL_NAMES)
)
SOURCE_RESULT_FIELDS = frozenset((
    "scope", "init_operation_id", "domain_id", "facility_id",
    "origin_reservation_sha256", "origin_observation_sha256",
    "namespace_reservation_sha256", "root_seal_sha256", "header_sha256",
    "binding_sha256", "prefix_sha256",
))
BODY_OPERATION_PLAN = {
    "R001": ("_owned_store_initial_with_actual_ex", 1),
    "R002": ("verify_prefix_owned_ex", 1),
    "N001": ("_owned_store_initial_with_actual_ex", 1),
}
PREFIX_FIXTURE_OPERATION_PLAN = (
    ("db.initialize", 1),
    ("storage_lifecycle.initialize", 1),
    ("personal.create_card", 1),
    ("origin_owner.reserve_origin", 1),
    ("namespace_owner.initialize_namespace3", 1),
    ("enrollment.publish_prefix_owned_ex", 1),
)

# Own observation storage, never installed in a product registry/catalog.
METHOD_EVENTS = []
METHOD_PINS = []
_COMMON_BINDINGS = None


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def sha(raw):
    assert type(raw) is bytes
    return hashlib.sha256(raw).hexdigest()


def _require_fresh_operation():
    if OPERATION_MODULE in sys.modules:
        raise RuntimeError("r915 fixture requires first original operation import")


def _transparent_method(saved_original, identifier, event_log):
    # Keep this wrapper's honest test-module provenance. No functools.wraps,
    # __module__ graft, __code__ clone, or __closure__ rewrite is performed.
    def observed(*args, **kwargs):
        call_arguments = (args, tuple(kwargs.items()))
        event_log.append((identifier, "call", call_arguments))
        try:
            actual_return = saved_original(*args, **kwargs)
        except BaseException as ordinary_exception:
            event_log.append((identifier, "raise", call_arguments, ordinary_exception))
            raise
        event_log.append((identifier, "return", call_arguments, actual_return))
        return actual_return

    return observed


def _require_method_capture(operation, method_pins):
    """Finite capture identity check; not full active-catalog acceptance."""
    catalog = operation._CALLEE_PINS
    assert type(catalog) is tuple and len(catalog) == 4
    classes, functions = catalog[1], catalog[2]
    assert type(classes) is tuple and type(functions) is tuple
    for pin in method_pins:
        kind, name, saved, wrapper, original_module, original_closure, wrapper_closure = pin
        assert saved.__module__ == original_module
        assert saved.__closure__ is original_closure
        assert wrapper.__module__ == __name__
        assert wrapper.__closure__ is wrapper_closure
        assert any(cell.cell_contents is saved for cell in wrapper_closure)
        class_rows = tuple(row for row in classes if row[0] is kind)
        assert len(class_rows) == 1
        own_rows = tuple(row for row in class_rows[0][2] if row[0] is kind)
        assert len(own_rows) == 1
        assert dict(own_rows[0][1])[name] is wrapper
        assert sum(row[0] is wrapper for row in functions) == 1
        assert sum(row[0] is saved for row in functions) == 1


def bootstrap_fresh_methods():
    """Future module-scope assembly step, before any original product API.

    Root must independently review the eventual import graph and class pins.
    Failure does not remove modules, reload, reset the gate, rebind a catalog,
    replace native functions, or restore class methods under an active capture.
    """
    global _COMMON_BINDINGS
    if _COMMON_BINDINGS is not None or METHOD_PINS:
        raise RuntimeError("r915 common fixture bootstrap already attempted")
    _require_fresh_operation()
    if os.name != "nt":
        raise RuntimeError("r915 common fixture requires isolated Windows child")
    enrollment = importlib.import_module("reno.personal_authority_enrollment")
    runtime = importlib.import_module("reno.personal_authority_enrollment_store_runtime")
    directories = importlib.import_module("reno.personal_authority_enrollment_store_directories")
    _require_fresh_operation()
    selected = (
        (runtime._StorePrepareStage, "__enter__", "stage.enter"),
        (runtime._StorePrepareStage, "__exit__", "stage.exit"),
        (directories._StoreDirectoryScope, "__enter__", "directory.enter"),
        (directories._StoreDirectoryScope, "__exit__", "directory.exit"),
    )
    for kind, name, identifier in selected:
        saved = kind.__dict__[name]
        assert type(saved) is type(_transparent_method)
        wrapper = _transparent_method(saved, identifier, METHOD_EVENTS)
        pin = (
            kind, name, saved, wrapper, saved.__module__,
            saved.__closure__, wrapper.__closure__,
        )
        METHOD_PINS.append(pin)
        setattr(kind, name, wrapper)
    _install_consumer_seams(runtime, directories)
    _require_fresh_operation()
    operation = importlib.import_module(OPERATION_MODULE)
    _require_method_capture(operation, tuple(METHOD_PINS))
    _require_consumer_capture(operation)
    _COMMON_BINDINGS = {
        "enrollment": enrollment,
        "runtime": runtime,
        "directories": directories,
        "operation": operation,
        "method_pins": tuple(METHOD_PINS),
    }
    return _COMMON_BINDINGS


def _bindings():
    if _COMMON_BINDINGS is None:
        raise RuntimeError("r915 final module must bootstrap before fixture execution")
    return _COMMON_BINDINGS


def method_events_since(index):
    assert type(index) is int and 0 <= index <= len(METHOD_EVENTS)
    # The caller reads own ordinary logs, never a product frame or local.
    return tuple(METHOD_EVENTS[index:])


def _read_originals(data, count):
    assert type(count) is int and count in (5, 11)
    raws = []
    for (location, name), maximum in zip(ORIGINAL_LOCATIONS[:count], ORIGINAL_MAXIMA[:count]):
        parent = data if location == "data_parent" else data / ROOT_NAME
        raw = (parent / name).read_bytes()
        assert type(raw) is bytes and 0 < len(raw) <= maximum
        raws.append(raw)
    assert len(raws) == count
    return tuple(raws)


def original_identity_oracle(originals):
    """Independent original-file order/size/digest declaration, not codec success."""
    assert type(originals) is tuple and len(originals) == 11
    return tuple(
        (index, ORIGINAL_LOCATIONS[index], ORIGINAL_MAXIMA[index], len(raw), sha(raw))
        for index, raw in enumerate(originals)
    )


def database_node_bytes(env):
    database = env["database"]
    paths = tuple((suffix, Path(str(database) + suffix)) for suffix in ("", "-wal", "-journal"))
    paths += (("node", env["data"] / "node-identity.json"),)
    return tuple((key, path.read_bytes() if path.exists() else None) for key, path in paths)


def assert_no_store_files(env):
    # Test-owned isolated parent/root only. No normal DB or arbitrary caller path.
    for parent in (env["data"], env["data"] / ROOT_NAME):
        assert not tuple(path.name for path in parent.iterdir() if path.name.startswith(STORE_PREFIX))


@pytest.fixture
def r915_prefix_environment(tmp_path, monkeypatch):
    """Future complete isolated current17 + five originals + source-only prefix.

    Six explicit top-level precursor API calls appear in
    PREFIX_FIXTURE_OPERATION_PLAN. Root's final R001/R002/N001 adds one complete
    body operation each. SQL/codec fixture checks are separately listed in the
    common-fixture notes; none are claimed to have run by this source draft.
    """
    selected = _bindings()
    from reno import config, db, personal
    from reno import personal_authority_namespace3_contract as namespace_contract
    from reno import personal_authority_namespace3_effect_owner_deadline as namespace_owner
    from reno import personal_authority_origin_reservation_contract as origin_contract
    from reno import personal_authority_origin_reservation_owner_deadline as origin_owner
    from reno import personal_mirror_contract as mirror
    from reno import storage_lifecycle as lifecycle
    from reno import personal_authority_enrollment_custody as source_custody
    from reno import personal_authority_enrollment_store_custody as store_custody

    _assert_sealed_subject_sources(SEALED_SUBJECT_SOURCE_PINS[12:])

    qa = Path(os.environ["RENO_QA_ROOT"]).resolve(strict=True)
    tmp_path.resolve(strict=True).relative_to(qa)
    Path(tempfile.gettempdir()).resolve(strict=True).relative_to(qa)
    assert lifecycle._CURRENT.get() is source_custody._CURRENT.get() is store_custody._CURRENT.get() is None
    root = tmp_path / "r915-private-profile"
    root.mkdir()
    data = root / "data"
    data.mkdir()
    local = root / "config.synthetic.json"
    local.write_bytes(canonical({
        "node_role": "home", "personal_features_enabled": True, "field_checks_enabled": True,
    }))
    monkeypatch.setenv("RENO_DATA_DIR", str(data))
    monkeypatch.setenv("RENO_CONFIG_FILE", str(local))
    for name in ("RENO_NODE_ROLE", "RENO_PERSONAL_FEATURES_ENABLED", "RENO_FIELD_CHECKS_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    for name, path in {
        "DATA": data, "MEDIA": root / "media", "ORIG": root / "media/originals",
        "FRAMES": root / "media/frames", "AUDIO": root / "media/audio", "FRAME_CACHE": root / "frames_cache",
    }.items():
        monkeypatch.setattr(config, name, path)
    monkeypatch.setattr(config, "_cache", None)
    database = data / "reno.db"
    db.initialize(database)
    profile = lifecycle.StorageProfile(database, root / "media", data / "generated", data / "sync-checkpoints")
    lifecycle.initialize(profile)
    with closing(sqlite3.connect(database)) as connection:
        meta = dict(connection.execute(
            "SELECT key,value FROM meta WHERE key IN (?,?,?,?)", mirror.META_KEYS,
        ))
        assert connection.execute("SELECT value FROM meta WHERE key='migration_version'").fetchone() == ("17",)
    mutation = str(uuid.uuid4())
    created, status = personal.create_card(meta["default_project_id"], {
        "operation_id": mutation, "primary_epoch": int(meta["primary_epoch"]),
        "changes": {
            "title": "r915 独立装修记录", "question": "材料与施工次序待核对",
            "content_snapshot": {"kind": "note", "text": "隔离 current17 的实际 mutation receipt"},
        },
    })
    assert status == 201 and created["card"]["card_id"]
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM mutation_receipt WHERE operation_id=?", (mutation,),
        ).fetchone() == (1,)
    arguments = dict(zip(("domain_id", "facility_id", "init_operation_id"), (str(uuid.uuid4()) for _ in range(3))))
    pair = origin_owner.reserve_origin(profile, **arguments)
    first = (data / ORIGINAL_NAMES[0]).read_bytes()
    second = (data / ORIGINAL_NAMES[1]).read_bytes()
    assert (pair.reservation_sha256, pair.observation_sha256) == (sha(first), sha(second))
    origin_contract.cross_validate(first, second)
    namespace_facts = namespace_owner.initialize_namespace3(
        profile, **arguments,
        expected_origin_reservation_sha256=sha(first), expected_origin_observation_sha256=sha(second),
    )
    five = _read_originals(data, 5)
    namespace_contract.cross_validate_complete(*five)
    assert (namespace_facts.namespace_reservation_sha256, namespace_facts.root_seal_sha256,
            namespace_facts.header_sha256) == tuple(sha(raw) for raw in five[2:])
    expected = dict(zip(EXPECTED_FIELDS, (sha(raw) for raw in five)))
    assert len(expected) == 5
    prefix_facts = selected["enrollment"].publish_prefix_owned_ex(profile, **arguments, **expected)
    originals = _read_originals(data, 11)
    assert originals[:5] == five
    assert prefix_facts.binding_sha256 == sha(originals[5])
    assert prefix_facts.prefix_sha256 == sha(originals[6])
    env = {
        "root": root, "data": data, "database": database, "profile": profile,
        "arguments": arguments, "expected": expected, "expected_tuple": tuple(expected[name] for name in EXPECTED_FIELDS),
        "operation_tuple": tuple(arguments[name] for name in ("domain_id", "facility_id", "init_operation_id")),
        "prefix_facts": prefix_facts, "originals": originals,
        "original_identity_oracle": original_identity_oracle(originals),
        "bindings": selected, "body_event_start": len(METHOD_EVENTS),
    }
    assert lifecycle.current_guard() is None
    assert_no_store_files(env)
    env["database_node_before"] = database_node_bytes(env)
    yield env
    # Ordinary ownership only: no helper retirement, close retry, guessed
    # HANDLE, normal DB cleanup, context reset, registry edit or claim replay.
    assert lifecycle._CURRENT.get() is source_custody._CURRENT.get() is store_custody._CURRENT.get() is None


def invoke_private_initial(env):
    """One future whole body operation, fixed literal private entry."""
    facts = env["prefix_facts"]
    return env["bindings"]["enrollment"]._owned_store_initial_with_actual_ex(
        env["profile"], env["operation_tuple"], env["expected_tuple"],
        facts.binding_sha256, facts.prefix_sha256,
    )


def invoke_default_source_verify(env):
    """One future whole source-only regression operation; no caller callback."""
    facts = env["prefix_facts"]
    return env["bindings"]["enrollment"].verify_prefix_owned_ex(
        env["profile"], **env["arguments"], **env["expected"],
        expected_binding_sha256=facts.binding_sha256, expected_prefix_sha256=facts.prefix_sha256,
    )


# No automatic bootstrap, test_* function, per-ID test case, or execution
# entrypoint is present. Root must assemble the finally sealed product contract
# and fixed tests separately before authorizing collection or execution.


from collections import Counter
from dataclasses import fields


CONSUMER_EVENTS = []
CONSUMER_PINS = []
OBSERVATION_BINDINGS = {}
CASE_CONTROL = {'mode': None, 'first': None, 'triggered': False}
BODY_OPERATION_PLAN = {
    'K001': ('_owned_store_initial_with_actual_ex', 1),
    'K002': ('verify_prefix_owned_ex', 1),
    'K003': ('_owned_store_initial_with_actual_ex', 1),
    'K004': ('_owned_store_initial_with_actual_ex', 1),
    'K005': ('_owned_store_initial_with_actual_ex', 1),
}
FIXED_SUCCESS_IDS = ('K001', 'K002', 'K003', 'K004', 'K005')
FIXED_FAULT_CASES = (
    ('F001', 'read.initial', 'before', ValueError),
    ('F002', 'read.final', 'before', ValueError),
    ('F003', 'read.checkpoint', 'before', SystemExit),
    ('F004', 'read.live', 'before', RuntimeError),
    ('F005', 'io.retire', 'before', ValueError),
    ('F006', 'io.retire', 'after', SystemExit),
    ('F007', 'dir.retire', 'before', ValueError),
    ('F008', 'dir.retire', 'after', KeyboardInterrupt),
    ('F009', 'history.release', 'before', ValueError),
    ('F010', 'history.release', 'after', SystemExit),
    ('F011', 'io.release', 'before', ValueError),
    ('F012', 'io.release', 'after', KeyboardInterrupt),
    ('F013', 'dir.release', 'before', RuntimeError),
    ('F014', 'dir.release', 'after', ValueError),
    ('F015', 'close.held', 'before', RuntimeError),
    ('F016', 'gen.run', 'before', SystemExit),
)

BODY_OPERATION_PLAN.update({row[0]: ('_owned_store_initial_with_actual_ex', 1) for row in FIXED_FAULT_CASES})


def _resource_observation(resource):
    io = OBSERVATION_BINDINGS['io']
    original = io._RESOURCE_ORIGINALS[id(resource)]
    state = io._RESOURCE_STATES[id(resource)]
    cycle = io._CYCLE_ORIGINALS[id(original[3])]
    # These are existing original records, not a native identity query. Keep
    # actual objects and immutable pins; no numeric HANDLE is printed/exported.
    return (resource, original[4], cycle[0], cycle[5][2], cycle[5][3], cycle[5][7], state[1], state[2], state[3])


def _snapshot_result(identifier, args, result):
    io = OBSERVATION_BINDINGS['io']
    if identifier == 'io.new':
        row = io._GRAPH_ORIGINALS[id(result)]
        plan, budget = row[8], row[7]
        return (result, row, plan._originals, plan._file_names, plan._file_raws,
                budget, budget.control_bytes, plan._empty_io_bytes)
    if identifier == 'io.read':
        return _resource_observation(args[1]) + (result,)
    if identifier in ('scan.initial', 'scan.final'):
        return (result, result._scope, result._role, result._binding, result._names)
    if identifier == 'gen.begin':
        return (result, result._pins)
    if identifier == 'collection':
        graph_event = next(event for event in reversed(CONSUMER_EVENTS)
                           if event[0] == 'io.new' and event[1] == 'return'
                           and event[4][1][3] is args[0])
        budget = graph_event[4][5]
        return (budget, budget.control_bytes)
    return None


def _matches_fault(identifier, args):
    mode = CASE_CONTROL['mode']
    if mode is None or CASE_CONTROL['triggered']:
        return False
    wanted, when = mode
    if identifier == 'io.read' and wanted.startswith('read.'):
        observed = _resource_observation(args[1])
        role, family, ordinal = observed[1], observed[3], observed[4]
        if wanted == 'read.live':
            return role == 'live_readback' and family == 'store' and ordinal == 0
        if wanted == 'read.checkpoint':
            return role == 'authority_checkpoint' and ordinal == 7
        if role != 'held_reader' or family != 'original' or ordinal != 0:
            return False
        actual_returns = sum(event[0] == 'io.read' and event[1] == 'return'
                             and event[2][0][1] is args[1] for event in CONSUMER_EVENTS)
        return actual_returns == (0 if wanted == 'read.initial' else 1)
    if identifier == 'io.close' and wanted == 'close.held':
        observed = _resource_observation(args[0])
        return observed[1] == 'held_reader' and observed[3:5] == ('original', 0)
    return identifier == wanted


def _consumer_wrapper(original, identifier):
    # Honest pre-import capture. With no active test action, each saved
    # original is invoked once. Explicit rejection/context actions are listed
    # separately in BOUNDARY_EXTRA_CALLS, never concealed as observation.
    def observed(*args, **kwargs):
        previous = BOUNDARY_READ_CONTEXT[0]
        previous_open = UNCERTAINTY_OPEN_CONTEXT[0]
        previous_query = QUERY_DISPATCH_CONTEXT[0]
        previous_registration = CLOCK_REGISTER_CONTEXT[0]
        previous_publication = PUBLICATION_CONTEXT[0]
        if identifier in PUBLICATION_SEAMS:
            selected_publication = _publication_selected_resource(identifier, args, kwargs)
            if selected_publication is None:
                return original(*args, **kwargs)
            PUBLICATION_CONTEXT[0] = selected_publication
        if identifier == 'native.register':
            selected_registration = _clock_selected_registration(args, kwargs)
            if selected_registration is None:
                return original(*args, **kwargs)
            CLOCK_REGISTER_CONTEXT[0] = selected_registration
        if identifier == 'native.clock' and (CLOCK_REGISTER_CONTEXT[0] is None or CLOCK_CONTROL['triggered']):
            return original(*args, **kwargs)
        if identifier == 'census.signed' and previous_query is None:
            return original(*args, **kwargs)
        if identifier in ('native.exact', 'native.chunk') and previous is None:
            return original(*args, **kwargs)
        if identifier in ('native.open', 'native.parent'):
            budget = args[2] if identifier == 'native.open' and len(args) > 2 else kwargs['budget']
            if id(budget) not in OBSERVATION_BINDINGS['custody']._BUDGET_JOURNALS:
                return original(*args, **kwargs)
        if identifier == 'native.open':
            UNCERTAINTY_OPEN_CONTEXT[0] = (args, tuple(kwargs.items()))
        if identifier == 'census.dispatch':
            QUERY_DISPATCH_CONTEXT[0] = [args[0], 0]
        close_slot = None
        close_before = None
        if identifier in ('native.close', 'native.free'):
            close_slot = kwargs.get('_custody_slot')
            if type(close_slot) is not OBSERVATION_BINDINGS['custody']._StoreNativeSlot:
                return original(*args, **kwargs)
            close_before = close_slot._close_attempted
        if identifier == 'io.read':
            BOUNDARY_READ_CONTEXT[0] = (args[0], args[1])
        arguments = (args, tuple(kwargs.items()))
        CONSUMER_EVENTS.append((identifier, 'call', arguments))
        try:
            trigger = _matches_fault(identifier, args)
            if trigger and CASE_CONTROL['mode'][1] == 'before':
                CASE_CONTROL['triggered'] = True
                first = CASE_CONTROL['first']
                CONSUMER_EVENTS.append((identifier, 'injected', arguments, first))
                raise first
            forwarded, forwarded_keywords = _boundary_before(identifier, args, kwargs)
            try:
                forwarded, forwarded_keywords = _uncertainty_before(identifier, forwarded, forwarded_keywords)
                forwarded, forwarded_keywords = _query_before(identifier, forwarded, forwarded_keywords)
                forwarded, forwarded_keywords = _completion_before(identifier, forwarded, forwarded_keywords)
                forwarded, forwarded_keywords = _observer_before(identifier, forwarded, forwarded_keywords)
                forwarded, forwarded_keywords = _clock_before(identifier, forwarded, forwarded_keywords)
                forwarded, forwarded_keywords = _publication_before(identifier, forwarded, forwarded_keywords)
                result = original(*forwarded, **forwarded_keywords)
            except BaseException as primary:
                _completion_failure(identifier, args, primary)
                _boundary_selection_failure(identifier, args, primary)
                CONSUMER_EVENTS.append((identifier, 'raise', arguments, primary))
                raise
            if identifier in ('native.close', 'native.free'):
                snapshot = (close_slot, close_before, close_slot._close_attempted,
                            close_slot._close_return, close_slot._closed_fact)
            elif identifier in ('native.exact', 'native.chunk'):
                snapshot = (BOUNDARY_READ_CONTEXT[0], result)
            else:
                snapshot = _snapshot_result(identifier, args, result)
            CONSUMER_EVENTS.append((identifier, 'return', arguments, result, snapshot))
            if trigger and CASE_CONTROL['mode'][1] == 'after':
                CASE_CONTROL['triggered'] = True
                first = CASE_CONTROL['first']
                CONSUMER_EVENTS.append((identifier, 'injected', arguments, first))
                raise first
            try:
                result = _uncertainty_after(identifier, args, kwargs, result)
                result = _query_after(identifier, args, kwargs, result)
                result = _completion_after(identifier, args, kwargs, result)
                result = _observer_after(identifier, args, kwargs, result)
                result = _clock_after(identifier, args, kwargs, result)
                result = _publication_after(identifier, args, kwargs, result)
                return _boundary_after(identifier, args, kwargs, result)
            except BaseException as primary:
                _completion_failure(identifier, args, primary)
                _boundary_selection_failure(identifier, args, primary)
                CONSUMER_EVENTS.append((identifier, 'raise', arguments, primary))
                raise
        finally:
            if identifier == 'io.read':
                BOUNDARY_READ_CONTEXT[0] = previous
            if identifier == 'native.open':
                UNCERTAINTY_OPEN_CONTEXT[0] = previous_open
            if identifier == 'census.dispatch':
                QUERY_DISPATCH_CONTEXT[0] = previous_query
            if identifier == 'native.register':
                CLOCK_REGISTER_CONTEXT[0] = previous_registration
            if identifier in PUBLICATION_SEAMS:
                PUBLICATION_CONTEXT[0] = previous_publication
    return observed


def _install_consumer_seams(runtime, directories):
    assert not CONSUMER_PINS and not OBSERVATION_BINDINGS
    io, census, custody = runtime.file_io, directories.census_native, runtime.file_io.custody
    OBSERVATION_BINDINGS.update(io=io, census=census, custody=custody, runtime=runtime, directories=directories)
    selected = (
        ('io.new', io, '_new_file_io_graph'), ('io.read', io, '_read_selected_exact'),
        ('io.close', io, '_close_resource'), ('scan.initial', census, '_scan'),
        ('scan.final', census, '_scan_final_generation'), ('gen.begin', directories, '_begin_final_census_generation'),
        ('gen.run', directories, '_run_final_census'), ('io.retire', io, '_retire_for_continuation'),
        ('dir.retire', directories, '_retire_for_continuation'), ('history.release', custody, '_release_retired_history'),
        ('io.release', io, '_release_retired_graph'), ('dir.release', directories, '_release_retired_graph'),
        ('collection', runtime, '_prepare_file_collection_owned'),
        ('io.begin', io, '_begin_file_cycle'), ('io.open', io, '_open_held_reader'),
        ('io.final', io, '_finalize_held_reader'), ('io.snapshot', io, '_file_snapshot'),
        ('selection.consume', directories, '_consume_selection'),
        ('native.exact', io.native, '_read_exact'), ('native.chunk', io.native, '_read_chunk'),
        ('native.close', io.native, '_native_close'), ('native.free', io.native, '_native_free'),
        ('native.open', io.native, '_relative_open'), ('native.parent', io.native, '_open_parent_raw'),
        ('custody.closed', custody, '_closed'), ('custody.relative', custody, '_returned_relative'),
        ('census.dispatch', census, '_dispatch'), ('census.signed', census, '_signed32'),
        ('census.retire', census, '_retire_for_scope'),
        ('runtime.clear', runtime, '_replace_cleanup_row'), ('runtime.observe', runtime, '_retirement_observed'),
        ('io.effect', io, '_retirement_observation_for_continuation'),
        ('io.release.effect', io, '_release_observation_for_graph'),
        ('dir.effect', directories, '_retirement_observation_for_continuation'),
        ('native.register', io.native, '_registered'),
        ('native.clock', runtime.deadline._RestrictedBudget, 'clock_only'),
        ('write.chunk', io.chunks, '_write_chunk'), ('creator.flush', io, '_flush_creator'),
        ('native.publish', io.native, '_publish_owned'), ('creator.info', io, '_creator_info'),
    )
    for identifier, module, name in selected:
        original = module.__dict__[name]
        wrapper = _consumer_wrapper(original, identifier)
        CONSUMER_PINS.append((identifier, module, name, original, wrapper,
                              original.__module__, original.__closure__, wrapper.__closure__))
        setattr(module, name, wrapper)


def _require_consumer_capture(operation):
    functions = operation._CALLEE_PINS[2]
    for identifier, module, name, original, wrapper, module_name, closure, wrapper_closure in CONSUMER_PINS:
        assert module.__dict__[name] is wrapper
        assert original.__module__ == module_name and original.__closure__ is closure
        assert wrapper.__module__ == __name__ and wrapper.__closure__ is wrapper_closure
        assert any(cell.cell_contents is original for cell in wrapper_closure)
        assert sum(row[0] is original for row in functions) == 1
        assert sum(row[0] is wrapper for row in functions) == 1


def _events_since(start, identifier=None, phase=None):
    return tuple(event for event in CONSUMER_EVENTS[start:]
                 if (identifier is None or event[0] == identifier)
                 and (phase is None or event[1] == phase))


def _graph_snapshot(start):
    events = _events_since(start, 'io.new', 'return')
    assert len(events) == 1
    return events[0][4]


def _assert_base_data(env):
    assert _read_originals(env['data'], 11) == env['originals']
    assert original_identity_oracle(env['originals']) == env['original_identity_oracle']
    assert database_node_bytes(env) == env['database_node_before']


def _assert_real_store_files(env, snapshot):
    names, expected_raws = snapshot[3:5]
    assert len(names) == len(expected_raws) == 29 and len(set(names)) == 29
    directory = env['data'] / ROOT_NAME
    found = tuple(sorted(path.name for path in directory.iterdir() if path.name.startswith(STORE_PREFIX)))
    assert found == tuple(sorted(names))
    for name, expected in zip(names, expected_raws):
        actual = (directory / name).read_bytes()
        assert actual == expected and sha(actual) == sha(expected)
    assert not tuple(path.name for path in directory.iterdir() if path.name.endswith('.stage'))


def _assert_read_and_owner_facts(start, snapshot):
    read_events = _events_since(start, 'io.read', 'return')
    counts = Counter(event[4][1] for event in read_events)
    assert counts == {'held_reader': 80, 'creator': 29, 'live_readback': 29, 'post_readback': 29, 'authority_checkpoint': 4}
    for event in read_events:
        resource, role, cycle, family, ordinal, expected, owner, slot, owner_pins, raw = event[4]
        assert event[2][0] == (cycle, resource) and raw is event[3]
        assert raw == expected and owner is not None and slot is not None
        assert type(owner_pins) is tuple and owner_pins[0] is owner
        assert slot._owner_pins is owner_pins and slot._owner is owner
        assert slot._known is slot._close_attempted is slot._representation_confirmed is True
        assert slot._receipt is slot._closed_fact
    for family, amount in (('original', 11), ('store', 29)):
        for ordinal in range(amount):
            selected = tuple(event[4] for event in read_events if event[4][3:5] == (family, ordinal))
            held = tuple(row for row in selected if row[1] == 'held_reader')
            assert len(held) == 2 and held[0][0] is held[1][0] and held[0][6] is held[1][6] and held[0][7] is held[1][7]
            assert held[0][8] is held[1][8] and held[0][9] == held[1][9]
            if family == 'store':
                publisher = tuple(next(row for row in selected if row[1] == role) for role in ('creator', 'live_readback', 'post_readback'))
                assert len({id(row[6]) for row in (*publisher, held[0])}) == 4
                assert len({id(row[7]) for row in (*publisher, held[0])}) == 4
            elif ordinal in (7, 8, 9, 10):
                checkpoint = tuple(row for row in selected if row[1] == 'authority_checkpoint')
                assert len(checkpoint) == 1 and checkpoint[0][6] is not held[0][6]
    body_bytes = sum(len(event[3]) for event in read_events)
    original_bytes = sum(map(len, snapshot[2]))
    store_bytes = sum(map(len, snapshot[4]))
    checkpoint_bytes = sum(map(len, snapshot[2][7:11]))
    assert body_bytes == 2 * original_bytes + 5 * store_bytes + checkpoint_bytes


def _assert_two_actual_generations(start, snapshot):
    initial = _events_since(start, 'scan.initial', 'return')
    final = _events_since(start, 'scan.final', 'return')
    assert tuple(event[4][2] for event in initial) == tuple(event[4][2] for event in final) == ('parent', 'root')
    assert tuple(len(event[4][4]) for event in initial) == (5, 7)
    assert tuple(len(event[4][4]) for event in final) == (5, 36)
    generation = _events_since(start, 'gen.begin', 'return')
    assert len(generation) == 1
    actual_generation, pins = generation[0][4]
    assert initial[0][4][1] is initial[1][4][1]
    assert final[0][4][1] is final[1][4][1] is actual_generation
    assert actual_generation is not initial[0][4][1]
    for index in (0, 1):
        assert initial[index][4][3][0] is final[index][4][3][0]
        assert initial[index][4][3][1] is final[index][4][3][1] is snapshot[5]
        assert initial[index][4][3][2] == tuple(sorted(initial[index][4][4]))
        assert frozenset(final[index][4][3][2]) == frozenset(final[index][4][4])
    assert len(pins[10]) == 5 and len(pins[11]) == 36
    assert frozenset(final[1][4][4]) == frozenset((*initial[1][4][4], *snapshot[3]))
    positions = {id(event): index for index, event in enumerate(CONSUMER_EVENTS[start:])}
    reads = _events_since(start, 'io.read', 'return')
    for event in final:
        prior = tuple(row for row in reads if positions[id(row)] < positions[id(event)] and row[4][1] == 'held_reader')
        assert len(prior) == 40
    io, census, dirs = OBSERVATION_BINDINGS['io'], OBSERVATION_BINDINGS['census'], OBSERVATION_BINDINGS['directories']
    assert id(actual_generation) not in dirs._FINAL_GENERATION_ORIGINALS
    assert id(actual_generation) not in dirs._FINAL_GENERATION_STATES
    assert id(actual_generation) not in census._SCOPE_CLEANUP
    marker = census._RETIRED_SCOPES[id(actual_generation)]
    assert marker[0]() is actual_generation and marker[2:7] == ('retired', True, True, True, True)
    assert not any(row[3] is actual_generation for row in census._QUERY_DEBTS.values())


def _assert_actual_budget(start, snapshot):
    collection = _events_since(start, 'collection', 'return')
    assert len(collection) == 1 and collection[0][3] is None
    budget, actual_final = collection[0][4]
    assert budget is snapshot[5] and type(snapshot[6]) is type(actual_final) is int
    expected_demand = 6 * sum(map(len, snapshot[4])) + 2 * sum(map(len, snapshot[2])) + sum(map(len, snapshot[2][7:11]))
    assert snapshot[7] == expected_demand and snapshot[6] + expected_demand < 1048576
    assert actual_final - snapshot[6] == expected_demand and actual_final < 1048576


def _run_success(env):
    assert CASE_CONTROL['mode'] is None and CASE_CONTROL['first'] is None
    start = len(CONSUMER_EVENTS)
    result = invoke_private_initial(env)
    _assert_source_prefix_result(result, env)
    snapshot = _graph_snapshot(start)
    _assert_initial_terminal_state(env)
    _assert_base_data(env)
    _assert_real_store_files(env, snapshot)
    return start, snapshot


def test_K001_full_k0_store_files_and_normal_ownership(r915_prefix_environment):
    start, snapshot = _run_success(r915_prefix_environment)
    io = OBSERVATION_BINDINGS['io']
    graph, row = snapshot[:2]
    assert graph._phase == 'retired' and graph._pins == ()
    assert id(graph) not in io._GRAPH_ORIGINALS and id(graph) not in io._GRAPH_STATES
    attempt = io._GRAPH_ATTEMPTS[id(row[2])]
    assert attempt[0]() is row[2] and attempt[1] is row[1] and attempt[2] is None
    assert all(len(_events_since(start, name, 'return')) == 1 for name in
               ('io.retire', 'dir.retire', 'history.release', 'io.release', 'dir.release'))


def test_K002_default_source_only_no_store_or_io_route(r915_prefix_environment):
    env = r915_prefix_environment
    start = len(CONSUMER_EVENTS)
    io, dirs = OBSERVATION_BINDINGS['io'], OBSERVATION_BINDINGS['directories']
    tables = (io._GRAPH_ATTEMPTS, io._GRAPH_ORIGINALS, io._GRAPH_STATES,
              dirs._FINAL_GENERATION_ATTEMPTS, dirs._FINAL_GENERATION_ORIGINALS, dirs._FINAL_GENERATION_STATES)
    before = tuple(tuple(table.items()) for table in tables)
    result = invoke_default_source_verify(env)
    _assert_source_prefix_result(result, env)
    assert _events_since(start) == () and method_events_since(env['body_event_start']) == ()
    after = tuple(tuple(table.items()) for table in tables)
    assert len(before) == len(after)
    for old, actual in zip(before, after):
        assert len(old) == len(actual) and all(k == old_k and row is old_row for (k, row), (old_k, old_row) in zip(actual, old))
    _assert_base_data(env)
    assert_no_store_files(env)


def test_K003_actual_initial_and_final_census_distinct(r915_prefix_environment):
    start, snapshot = _run_success(r915_prefix_environment)
    _assert_two_actual_generations(start, snapshot)


def test_K004_held_two_reads_and_separate_publication_checkpoint_owners(r915_prefix_environment):
    start, snapshot = _run_success(r915_prefix_environment)
    _assert_read_and_owner_facts(start, snapshot)


def test_K005_same_actual_budget_content_demand(r915_prefix_environment):
    start, snapshot = _run_success(r915_prefix_environment)
    _assert_actual_budget(start, snapshot)


@pytest.mark.parametrize('identifier,target,when,kind', FIXED_FAULT_CASES,
                         ids=tuple(row[0] for row in FIXED_FAULT_CASES))
def test_fixed_owning_fault_first_and_retained_facts(r915_prefix_environment, identifier, target, when, kind):
    env = r915_prefix_environment
    start = len(CONSUMER_EVENTS)
    first = kind('r927 fixed ordinary consumer-seam fault ' + identifier)
    CASE_CONTROL.update(mode=(target, when), first=first, triggered=False)
    try:
        with pytest.raises(BaseException) as error:
            invoke_private_initial(env)
        assert error.value is first and CASE_CONTROL['triggered'] is True
        injected = _events_since(start, phase='injected')
        assert len(injected) == 1 and injected[0][3] is first
        snapshot = _graph_snapshot(start)
        graph, row = snapshot[:2]
        value, journal = row[2], row[5]
        _assert_base_data(env)
        custody = OBSERVATION_BINDINGS['custody']
        actual = custody._JOURNAL_RECEIPT.__get__(journal, custody._StorePrepareJournal)
        before_seal = target.startswith('read.') or target == 'close.held' or target == 'gen.run'
        assert actual.unknown_count == 0 and actual.first_present is before_seal
        assert actual.all_registered_native_closed is (not before_seal)
        _assert_real_store_close_calls(start, journal, actual)
        assert actual.reset_state == 'reset' and actual.source_closed is actual.write_authority is False
        if target.startswith('read.') or target == 'close.held' or target == 'gen.run':
            position = CONSUMER_EVENTS.index(injected[0])
            assert not any(event[0] == 'io.read' and event[1] == 'call' for event in CONSUMER_EVENTS[position+1:])
            assert value._first is first and value._phase == 'retired_uncompleted'
            assert id(value) not in OBSERVATION_BINDINGS['runtime']._RETAINED_RETIREMENTS
            assert id(graph) not in OBSERVATION_BINDINGS['io']._GRAPH_ORIGINALS
            assert id(graph) not in OBSERVATION_BINDINGS['io']._GRAPH_STATES
        else:
            runtime = OBSERVATION_BINDINGS['runtime']
            retained = runtime._RETAINED_RETIREMENTS[id(value)]
            assert retained._journal is journal and retained._row is not None
            assert retained._value is value and retained._file_io_graph is graph
            assert retained._first is first and value._phase == 'retained_failed'
            if target == 'io.retire':
                assert retained._file_io_retire_returned is False
                assert retained._file_io_effects_confirmed is (when == 'after')
                assert retained._directory_retire_returned is retained._history_release_returned is False
            elif target == 'dir.retire':
                assert retained._file_io_retire_returned is retained._file_io_effects_confirmed is True
                assert retained._directory_retire_returned is retained._history_release_returned is False
            elif target == 'history.release':
                history_row = custody._REGISTRATION_HISTORY[id(journal)]
                assert retained._history_release_returned is False and retained._row_clear_returned is False
                assert (history_row[2] is None) is (when == 'after')
                assert retained._history_release_observed is (when == 'after')
            elif target == 'io.release':
                assert retained._history_release_returned is retained._row_clear_returned is True
                assert retained._file_io_release_returned is False
                assert retained._file_io_release_effects_confirmed is (when == 'after')
                assert retained._directory_release_returned is False
                assert (id(graph) not in OBSERVATION_BINDINGS['io']._GRAPH_ORIGINALS) is (when == 'after')
            elif target == 'dir.release':
                assert retained._file_io_release_returned is retained._file_io_release_effects_confirmed is True
                assert retained._directory_release_returned is False
                directory_graph = retained._directory_graph
                directory_scope = directory_graph[2]._scope
                assert (id(directory_scope) not in OBSERVATION_BINDINGS['directories']._DIRECTORY_CLEANUP_ROWS) is (when == 'after')
            # The real retained record rejects the next attempt before a seam
            # or native operation. This is a refusal assertion, not a retry.
            before = len(CONSUMER_EVENTS)
            with pytest.raises(BaseException) as replay:
                runtime._retire_continuation(value)
            assert replay.value is first and len(CONSUMER_EVENTS) == before
    finally:
        CASE_CONTROL.update(mode=None, first=None, triggered=False)


# Bootstrap and exact source pins are added only by the source assembly helper.
# This draft is never collected/imported/executed as a test module directly.


def _assert_source_prefix_result(result, env):
    enrollment = env["bindings"]["enrollment"]
    assert type(result) is enrollment.AuthorityPrefixFiles
    assert frozenset(field.name for field in fields(result)) == SOURCE_RESULT_FIELDS
    assert result.scope == "durable_authority_prefix_files_only"
    assert (result.domain_id, result.facility_id, result.init_operation_id) == env["operation_tuple"]
    expected_digests = tuple(sha(raw) for raw in env["originals"][:7])
    assert (
        result.origin_reservation_sha256, result.origin_observation_sha256,
        result.namespace_reservation_sha256, result.root_seal_sha256,
        result.header_sha256, result.binding_sha256, result.prefix_sha256,
    ) == expected_digests
    assert result is not env["prefix_facts"]
    # These fields do not include a permission/complete-close assertion.
    assert not any(hasattr(result, name) for name in ("source_closed", "write_authority", "accepted_authority"))


def _ordinary_method_objects(env):
    events = method_events_since(env["body_event_start"])
    assert tuple((event[0], event[1]) for event in events) == (
        ("stage.enter", "call"), ("stage.enter", "return"),
        ("directory.enter", "call"), ("directory.enter", "return"),
        ("directory.exit", "call"), ("directory.exit", "return"),
        ("stage.exit", "call"), ("stage.exit", "return"),
    )
    for event in events:
        actual_args, actual_keywords = event[2]
        assert type(actual_args) is tuple and actual_keywords == ()
        assert len(event) == (3 if event[1] == "call" else 4)
    stage = events[0][2][0][0]
    directory = events[2][2][0][0]
    journal = events[1][3]
    assert events[0][2] == events[1][2] == ((stage,), ())
    assert events[2][2] == events[3][2] == ((directory,), ())
    assert events[3][3] is directory
    assert events[4][2] == events[5][2] == ((directory, None, None, None), ())
    assert events[6][2] == events[7][2] == ((stage, None, None, None), ())
    assert events[5][3] is events[7][3] is False
    assert type(stage) is env["bindings"]["runtime"]._StorePrepareStage
    assert type(directory) is env["bindings"]["directories"]._StoreDirectoryScope
    return stage, directory, journal


def _assert_initial_terminal_state(env):
    """Read genuine post-operation objects; add no live consumer or retirement."""
    runtime = env["bindings"]["runtime"]
    directories = env["bindings"]["directories"]
    census = directories.census_native
    stage, directory, journal = _ordinary_method_objects(env)
    value = stage._continuation
    assert type(value) is runtime._StoreContinuation
    assert value._phase == "retired_closed" and value._first is value._first_tb is None
    assert value._pins == () and value._journal is value._stage is value._receipt is None
    assert stage._enter_attempted is stage._exit_attempted is stage._entered is True
    receipt = stage._receipt
    assert type(receipt) is runtime._StorePrepareCloseReceipt
    assert receipt.format_version == 1 and receipt.kind == "registered_store_prepare_native_stage_only"
    assert all(flag is True for flag in (
        receipt.all_registered_native_closed, receipt.context_reset, receipt.journal_sealed,
        receipt.source_snapshot_unchanged, receipt.outer_ex_borrowed,
    ))
    assert receipt.source_closed is receipt.write_authority is False
    snapshot = receipt.native_snapshot
    assert snapshot.registered_count == receipt.registered_native_count > 0
    assert snapshot.unknown_count == 0 and snapshot.first_present is False
    assert snapshot.all_registered_native_closed is True
    assert snapshot.install_state == "installed" and snapshot.reset_state == "reset"
    assert snapshot.source_closed is snapshot.write_authority is False
    custody = sys.modules["reno.personal_authority_enrollment_store_custody"]
    assert type(journal) is custody._StorePrepareJournal
    assert type(snapshot) is custody.StoreNativeOwnerJournalSnapshotV1
    assert snapshot is custody._JOURNAL_RECEIPT.__get__(journal, custody._StorePrepareJournal)
    route_binding = dict.get(runtime._INITIAL_ROUTE_VALUES, id(value))
    assert type(route_binding) is tuple and len(route_binding) == 4
    assert route_binding[0]() is value and route_binding[1] is value._claim
    assert route_binding[2] is None
    hook_attempt = dict.get(runtime._INITIAL_HOOK_ATTEMPTS, id(value))
    assert type(hook_attempt) is tuple and len(hook_attempt) == 3
    assert hook_attempt[0]() is value and hook_attempt[1] is value._claim
    assert dict.get(runtime._INITIAL_HOOKS, id(value)) is None
    assert dict.get(runtime._RETAINED_RETIREMENTS, id(value)) is None
    assert directory._phase == "retired" and directory._pins == ()
    assert directory._first is directory._first_tb is None
    assert all(getattr(directory, name) is None for name in (
        "_continuation", "_plan", "_stage", "_journal", "_budget", "_profile", "_paths", "_guard",
    ))
    row = dict.get(directories._DIRECTORY_SCOPES, id(directory))
    assert type(row) is tuple and len(row) == 5 and row[0]() is directory and row[1] is directory._claim
    assert row[2:] == ((), "retired", None)
    for table in (directories._DIRECTORY_CLEANUP_ROWS, directories._DIRECTORY_ORIGINAL_ROWS,
                  directories._DIRECTORY_RETIREMENT_ROWS, census._SCOPE_CLEANUP, census._RETIREMENT_ROOTS):
        assert dict.get(table, id(directory)) is None
    marker = dict.get(census._RETIRED_SCOPES, id(directory))
    assert type(marker) is tuple and len(marker) == 8 and marker[0]() is directory
    assert marker[2:7] == ("retired", True, True, True, True)
    assert not any(row[3] is directory for row in census._QUERY_DEBTS.values())
    # The sole aggregate is statically fixed in _initial_prepare_factory_owned.
    # No module-function call counter or unobserved kernel status is invented.
    return stage, directory, journal




"""Additional fixed consumer-boundary cases, assembled before bootstrap."""
BOUNDARY_CONTROL = {'case': None, 'triggered': False, 'context_reader': None, 'context_exit': False}
BOUNDARY_READ_CONTEXT = [None]
BOUNDARY_EVENTS = []
FIXED_BOUNDARY_IDS = tuple('B%03d' % index for index in range(1, 23))
BOUNDARY_SUCCESS_IDS = ('B001', 'B002', 'B018', 'B022')
BOUNDARY_EXPECTED_CODES = {
    'B003': 'NAMESPACE3_IO_READBACK_CHANGED', 'B004': 'NAMESPACE3_IO_READBACK_CHANGED',
    'B005': 'ENROLLMENT_STORE_FILE_IO_READBACK_CHANGED', 'B006': 'ENROLLMENT_STORE_FILE_IO_READBACK_CHANGED',
    'B007': 'ENROLLMENT_STORE_FILE_IO_READBACK_CHANGED', 'B008': 'ENROLLMENT_STORE_FILE_IO_READBACK_CHANGED',
    'B009': 'ENROLLMENT_STORE_DIRECTORY_CLOSED', 'B010': 'ENROLLMENT_STORE_DIRECTORY_CLOSED',
    'B011': 'ENROLLMENT_STORE_DIRECTORY_BINDING_INVALID',
    'B012': 'ENROLLMENT_STORE_FILE_IO_BINDING_INVALID', 'B013': 'ENROLLMENT_STORE_FILE_IO_BINDING_INVALID',
    'B014': 'ENROLLMENT_STORE_FILE_IO_BINDING_INVALID', 'B015': 'ENROLLMENT_STORE_FILE_IO_OWNER_RETIRED',
    'B016': 'ENROLLMENT_STORE_FILE_IO_BINDING_INVALID', 'B017': 'NAMESPACE3_IO_LIMIT',
    'B019': 'ENROLLMENT_STORE_DIRECTORY_INVALID_ARGUMENT',
    'B020': 'ENROLLMENT_STORE_DIRECTORY_BINDING_INVALID', 'B021': 'ENROLLMENT_STORE_DIRECTORY_BINDING_INVALID',
}
BODY_OPERATION_PLAN.update({identifier: ('_owned_store_initial_with_actual_ex', 1) for identifier in FIXED_BOUNDARY_IDS})
BOUNDARY_EXTRA_CALLS = {
    'B003': ('actual_budget.charge', 1), 'B004': ('actual_budget.charge', 1),
    'B010': ('directories._consume_selection_rejection', 1),
    'B022': ('reader.__enter__', 1, 'reader.__exit__', 1),
}


def _boundary_role(cycle):
    row = OBSERVATION_BINDINGS['io']._CYCLE_ORIGINALS[id(cycle)]
    return row[5][2:4]


def _boundary_read_phase():
    active = BOUNDARY_READ_CONTEXT[0]
    if active is None:
        return None
    cycle, resource = active
    observed = _resource_observation(resource)
    if observed[1] != 'held_reader' or observed[3:5] != ('original', 0):
        return None
    returns = sum(event[0] == 'io.read' and event[1] == 'return' and event[2][0][1] is resource
                  for event in CONSUMER_EVENTS)
    return 'initial' if returns == 0 else 'final'


def _boundary_target(identifier, args):
    case = BOUNDARY_CONTROL['case']
    if case is None or BOUNDARY_CONTROL['triggered']:
        return False
    phase = _boundary_read_phase()
    if case in ('B001', 'B002'):
        return identifier == 'native.chunk' and args[1] > 1 and phase == ('initial' if case == 'B001' else 'final')
    if case in ('B003', 'B004'):
        return identifier == 'native.chunk' and args[1] == 1 and phase == ('initial' if case == 'B003' else 'final')
    if case in ('B005', 'B006', 'B017', 'B018'):
        return identifier == 'native.exact' and phase == ('final' if case == 'B006' else 'initial')
    if case in ('B007', 'B008'):
        if identifier != 'io.snapshot' or _boundary_role(args[0]) != ('original', 0):
            return False
        prior = sum(event[0] == 'io.read' and event[1] == 'return' and event[2][0][1] is args[1]
                    for event in CONSUMER_EVENTS)
        return prior == 1
    if case in ('B009', 'B010', 'B011'):
        return identifier == 'selection.consume' and args[0]._pins[2:4] == ('original', 0)
    if case in ('B012', 'B016'):
        return identifier == 'io.begin' and args[0]._pins[2:4] == ('original', 0)
    if case in ('B013', 'B014', 'B015', 'B022'):
        return identifier == 'io.open' and _boundary_role(args[0]) == ('original', 0)
    if case in ('B019', 'B020', 'B021'):
        return identifier == 'gen.run'
    return False


def _boundary_mark(identifier, detail):
    BOUNDARY_CONTROL['triggered'] = True
    BOUNDARY_EVENTS.append((BOUNDARY_CONTROL['case'], identifier, detail))


def _boundary_before(identifier, args, kwargs):
    if not _boundary_target(identifier, args):
        return args, kwargs
    case = BOUNDARY_CONTROL['case']
    if case in ('B001', 'B002'):
        requested = max(1, min(args[1], OBSERVATION_BINDINGS['io'].native.CHUNK_BYTES) // 2)
        forwarded = (args[0], requested, args[2])
        _boundary_mark(identifier, (args, forwarded, BOUNDARY_READ_CONTEXT[0]))
        return forwarded, kwargs
    if case in ('B017', 'B018'):
        maximum = args[1] - 1 if case == 'B017' else args[1]
        forwarded = (args[0], args[1], maximum, args[3])
        _boundary_mark(identifier, (args, forwarded, BOUNDARY_READ_CONTEXT[0]))
        return forwarded, kwargs
    if case == 'B009':
        actual = args[0]
        clone = object.__new__(type(actual))
        clone._seal, clone._claim, clone._pins = actual._seal, actual._claim, actual._pins
        _boundary_mark(identifier, (actual, clone, OBSERVATION_BINDINGS['directories']._FILE_SELECTIONS[id(actual)]))
        return (clone,), kwargs
    if case == 'B011':
        actual, pins = args[0], args[0]._pins
        _boundary_mark(identifier, (actual, pins))
        actual._pins = tuple(list(pins))
    elif case == 'B019':
        actual = args[0]
        clone = object.__new__(type(actual))
        clone._seal, clone._claim, clone._pins, clone._phase = actual._seal, actual._claim, actual._pins, actual._phase
        _boundary_mark(identifier, (actual, clone, actual._pins))
        return (clone,), kwargs
    elif case in ('B020', 'B021'):
        actual, pins = args[0], args[0]._pins
        _boundary_mark(identifier, (actual, pins))
        index = 7 if case == 'B020' else 6
        actual._pins = pins[:index] + (object(),) + pins[index + 1:]
    return args, kwargs


def _boundary_after(identifier, args, kwargs, result):
    # Explicit context exit follows the real finalizer; its receipt getter
    # must not cause a third body read. This is a declared test action.
    if (BOUNDARY_CONTROL['case'] == 'B022' and identifier == 'io.final'
            and args[0] is BOUNDARY_CONTROL['context_reader'] and not BOUNDARY_CONTROL['context_exit']):
        BOUNDARY_CONTROL['context_exit'] = True
        before = sum(event[0] == 'io.read' and event[1] == 'call' for event in CONSUMER_EVENTS)
        actual = type(args[0]).__exit__(args[0], None, None, None)
        after = sum(event[0] == 'io.read' and event[1] == 'call' for event in CONSUMER_EVENTS)
        BOUNDARY_EVENTS.append(('B022', 'context.exit', (args[0], actual, before, after)))
        return result
    if not _boundary_target(identifier, args):
        return result
    case = BOUNDARY_CONTROL['case']
    if case in ('B003', 'B004'):
        if result != b'':
            return result
        _boundary_mark(identifier, (result, BOUNDARY_READ_CONTEXT[0]))
        args[2].charge(1)
        return b'!'
    if case in ('B005', 'B006'):
        assert result
        changed = result[:-1] + bytes((result[-1] ^ 1,))
        _boundary_mark(identifier, (result, changed, BOUNDARY_READ_CONTEXT[0]))
        return changed
    if case == 'B007':
        native, path = result
        assert any(key == 'byte_size' for key, _ in path)
        changed = tuple((key, value + 1 if key == 'byte_size' else value) for key, value in path)
        _boundary_mark(identifier, (result, (native, changed)))
        return native, changed
    if case == 'B008':
        native, path = result
        stamp = native[2]
        changed = native[:2] + (stamp[:-1] + (stamp[-1] ^ 1,),)
        _boundary_mark(identifier, (result, (changed, path)))
        return changed, path
    if case == 'B010':
        actual = args[0]
        _boundary_mark(identifier, (actual, OBSERVATION_BINDINGS['directories']._FILE_SELECTIONS[id(actual)]))
        # Exactly one explicit guard refusal; it never retries the body.
        OBSERVATION_BINDINGS['directories']._consume_selection(actual, **kwargs)
        raise AssertionError('spent actual selection accepted a second consume')
    if case == 'B012':
        pins = result._pins
        _boundary_mark(identifier, (result, pins))
        result._pins = tuple(list(pins))
    elif case == 'B016':
        graph = OBSERVATION_BINDINGS['io']._CYCLE_ORIGINALS[id(result)][2]
        original = graph._cycles
        _boundary_mark(identifier, (graph, original))
        graph._cycles = original[:-1]
    elif case in ('B013', 'B014', 'B015', 'B022'):
        io = OBSERVATION_BINDINGS['io']
        reader_row = io._READER_ORIGINALS[id(result)]
        resource = reader_row[3]
        observation = _resource_observation(resource)
        if case == 'B013':
            pins = result._pins
            _boundary_mark(identifier, (result, pins, observation))
            result._pins = tuple(list(pins))
        elif case == 'B014':
            graph = io._CYCLE_ORIGINALS[id(reader_row[2])][2]
            resources = graph._resources
            _boundary_mark(identifier, (graph, resources, observation))
            graph._resources = resources[:-1]
        elif case == 'B015':
            owner = observation[6]
            assert owner.retired is False
            _boundary_mark(identifier, (owner, observation))
            owner.retired = True
        else:
            _boundary_mark(identifier, (result, observation))
            BOUNDARY_CONTROL['context_reader'] = result
            actual = type(result).__enter__(result)
            BOUNDARY_EVENTS.append(('B022', 'context.enter', (result, actual)))
    return result


def _boundary_selection_failure(identifier, args, primary):
    if identifier == 'selection.consume' and BOUNDARY_CONTROL['case'] in ('B009', 'B010', 'B011'):
        row = OBSERVATION_BINDINGS['directories']._FILE_SELECTIONS[id(args[0])]
        BOUNDARY_EVENTS.append((BOUNDARY_CONTROL['case'], 'consume.failure', (args[0], row, primary)))


def _assert_real_store_close_calls(start, journal, snapshot, *, acknowledged_raise=None):
    custody = OBSERVATION_BINDINGS['custody']
    assert type(snapshot) is custody.StoreNativeOwnerJournalSnapshotV1
    assert type(snapshot.facts) is tuple and snapshot.facts
    assert type(snapshot.registered_count) is int and snapshot.registered_count == len(snapshot.facts)
    assert snapshot.unknown_count == 0 and snapshot.source_closed is snapshot.write_authority is False
    for ordinal, fact in enumerate(snapshot.facts):
        assert type(fact) is custody.StoreNativeOwnerCloseFactV1 and fact.ordinal == ordinal
        assert fact.native_state == 'CLOSED'
        assert fact.acquisition_attempted is fact.native_acquired is fact.close_attempted is True
        assert fact.source_closed is fact.write_authority is False
    closed = tuple(event for event in _events_since(start, phase='return')
                   if event[0] in ('native.close', 'native.free') and event[4][0]._journal is journal)
    slots = tuple(event[4][0] for event in closed if event[4][1] is False)
    assert len({id(slot) for slot in slots}) == len(slots)
    for event in closed:
        slot, before_attempt, after_attempt, raw, fact = event[4]
        assert type(before_attempt) is bool and after_attempt is True
        assert type(slot) is custody._StoreNativeSlot
        assert fact is slot._closed_fact is slot._receipt is snapshot.facts[slot._ordinal]
        assert slot._known is slot._return_seen is slot._close_attempted is slot._representation_confirmed is True
        if event[0] == 'native.close':
            assert type(raw) is int and -(1 << 31) <= raw < (1 << 31) and raw != 0
        else:
            assert raw is None
    if acknowledged_raise is not None:
        # U005 has a real successful inner acknowledgement and an outer
        # exception. Keep that evidence separate from native normal returns.
        assert type(acknowledged_raise) is tuple and len(acknowledged_raise) == 2
        selected, first = acknowledged_raise
        assert type(selected) is custody._StoreNativeSlot and selected._journal is journal
        assert selected._kind == 'HANDLE' and not any(slot is selected for slot in slots)
        assert selected._receipt is selected._closed_fact is snapshot.facts[selected._ordinal]
        assert selected._known is selected._return_seen is selected._close_attempted is selected._representation_confirmed is True
        raw = selected._close_return
        assert type(raw) is int and -(1 << 31) <= raw < (1 << 31) and raw != 0
        native_events = tuple(event for event in _events_since(start, 'native.close')
                              if dict(event[2][1]).get('_custody_slot') is selected)
        assert tuple(event[1] for event in native_events) == ('call', 'raise')
        native_call, native_raise = native_events
        assert native_call[2][0] == (None,)
        assert native_call[2][1] == (('_custody_slot', selected),)
        assert native_raise[2][0] == (None,) and native_raise[2][1] == (('_custody_slot', selected),)
        assert native_raise[3] is first
        acknowledgments = tuple(event for event in _events_since(start, 'custody.closed')
                                if event[2][0][0] is selected)
        assert tuple(event[1] for event in acknowledgments) == ('call', 'return', 'raise')
        ack_call, ack_return, ack_raise = acknowledgments
        assert all(len(event[2][0]) == 2 and event[2][0][0] is selected
                   and event[2][0][1] is raw and event[2][1] == () for event in acknowledgments)
        assert ack_return[3] is True and ack_raise[3] is first
        positions = tuple(next(index for index, event in enumerate(CONSUMER_EVENTS) if event is wanted)
                          for wanted in (native_call, ack_call, ack_return, ack_raise, native_raise))
        assert positions == tuple(sorted(set(positions)))
        slots += (selected,)
    assert len(slots) == snapshot.registered_count
    assert {slot._ordinal for slot in slots} == set(range(snapshot.registered_count))
    assert {id(slot._closed_fact) for slot in slots} == {id(fact) for fact in snapshot.facts}
    # This observes actual registered closer calls/slot returns, never a new
    # DLL query or guessed HANDLE. UNKNOWN is not accepted by this assertion.
    return slots


@pytest.mark.parametrize('identifier', FIXED_BOUNDARY_IDS, ids=FIXED_BOUNDARY_IDS)
def test_fixed_full_owning_boundaries(r915_prefix_environment, identifier):
    env = r915_prefix_environment
    assert BOUNDARY_CONTROL['case'] is None and BOUNDARY_READ_CONTEXT[0] is None
    start, boundary_start = len(CONSUMER_EVENTS), len(BOUNDARY_EVENTS)
    BOUNDARY_CONTROL.update(case=identifier, triggered=False, context_reader=None, context_exit=False)
    try:
        if identifier in BOUNDARY_SUCCESS_IDS:
            result = invoke_private_initial(env)
            _assert_source_prefix_result(result, env)
            snapshot = _graph_snapshot(start)
            _assert_initial_terminal_state(env)
            _assert_base_data(env)
            _assert_real_store_files(env, snapshot)
            _assert_read_and_owner_facts(start, snapshot)
            _assert_two_actual_generations(start, snapshot)
            _assert_actual_budget(start, snapshot)
        else:
            with pytest.raises(BaseException) as error:
                invoke_private_initial(env)
            assert getattr(error.value, 'code', None) == BOUNDARY_EXPECTED_CODES[identifier]
            snapshot = _graph_snapshot(start)
            value, journal = snapshot[1][2], snapshot[1][5]
            actual = OBSERVATION_BINDINGS['custody']._JOURNAL_RECEIPT.__get__(journal, OBSERVATION_BINDINGS['custody']._StorePrepareJournal)
            assert value._first is error.value and value._phase == 'retired_uncompleted'
            assert actual.unknown_count == 0 and actual.all_registered_native_closed is False
            assert actual.reset_state == 'reset' and actual.first_present is True
            assert actual.source_closed is actual.write_authority is False
            assert id(value) not in OBSERVATION_BINDINGS['runtime']._RETAINED_RETIREMENTS
            assert id(snapshot[0]) not in OBSERVATION_BINDINGS['io']._GRAPH_ORIGINALS
            raises = _events_since(start, phase='raise')
            assert raises and raises[0][3] is error.value
            first_position = next(index for index, event in enumerate(CONSUMER_EVENTS[start:]) if event[1] == 'raise')
            assert not any(event[0] in ('io.read', 'native.exact', 'native.chunk', 'native.open', 'native.parent',
                                      'io.begin', 'io.open', 'gen.begin') and event[1] == 'call'
                           for event in CONSUMER_EVENTS[start + first_position + 1:])
            _assert_base_data(env)
        assert BOUNDARY_CONTROL['triggered'] is True
        observed = tuple(BOUNDARY_EVENTS[boundary_start:])
        markers = tuple(event for event in observed if event[1] not in ('consume.failure', 'context.enter', 'context.exit'))
        assert len(markers) == 1 and markers[0][0] == identifier
        journal = snapshot[1][5]
        actual = OBSERVATION_BINDINGS['custody']._JOURNAL_RECEIPT.__get__(journal, OBSERVATION_BINDINGS['custody']._StorePrepareJournal)
        _assert_real_store_close_calls(start, journal, actual)
        if identifier in ('B009', 'B010', 'B011'):
            failures = tuple(event for event in observed if event[1] == 'consume.failure')
            assert failures and all(event[2][1][3] is (identifier != 'B009') for event in failures)
        if identifier == 'B022':
            assert BOUNDARY_CONTROL['context_exit'] is True
            enter, = (event[2] for event in observed if event[1] == 'context.enter')
            exit_, = (event[2] for event in observed if event[1] == 'context.exit')
            assert enter[0] is enter[1] is exit_[0] and exit_[1] is False and exit_[2] == exit_[3]
    finally:
        BOUNDARY_CONTROL.update(case=None, triggered=False, context_reader=None, context_exit=False)
        assert BOUNDARY_READ_CONTEXT[0] is None


"""Fixed uncertain-ack and real weakref-callback tests, source draft only."""
import gc

UNCERTAINTY_CONTROL = {'case': None, 'triggered': False, 'first': None, 'slot': None}
UNCERTAINTY_OPEN_CONTEXT = [None]
UNCERTAINTY_EVENTS = []
FIXED_UNCERTAINTY_IDS = tuple('U%03d' % index for index in range(1, 9))
FIXED_WEAK_IDS = ('W001', 'W002', 'W003')
UNKNOWN_IDS = ('U001', 'U002', 'U003', 'U004', 'U006')
UNCERTAINTY_ERROR_CODES = {
    'U001': 'NAMESPACE3_IO_NATIVE_CLOSE_UNCONFIRMED',
    'U002': 'NAMESPACE3_IO_NATIVE_CLOSE_UNCONFIRMED',
    'U003': 'NAMESPACE3_IO_NATIVE_FREE_UNCONFIRMED',
}
BODY_OPERATION_PLAN.update({identifier: ('_owned_store_initial_with_actual_ex', 1)
                            for identifier in (*FIXED_UNCERTAINTY_IDS, *FIXED_WEAK_IDS)})
DEBT_EXTRA_CALLS = {'retained_cases': ('runtime._retire_continuation_rejection', 1),
                    'weak_cases': ('gc.collect', 1, 'own_event_lists.clear', 4)}


def _uncertainty_resource_role(slot):
    io = OBSERVATION_BINDINGS['io']
    if type(slot) is not OBSERVATION_BINDINGS['custody']._StoreNativeSlot:
        return None
    for key, state in io._RESOURCE_STATES.items():
        if state[2] is slot:
            original = io._RESOURCE_ORIGINALS[key]
            cycle = io._CYCLE_ORIGINALS[id(original[3])]
            return original[4], cycle[5][2], cycle[5][3]
    return None


def _uncertainty_target(identifier, args, kwargs):
    case = UNCERTAINTY_CONTROL['case']
    if case is None or UNCERTAINTY_CONTROL['triggered']:
        return False
    if case in ('U006', 'U007'):
        if identifier != 'custody.relative' or UNCERTAINTY_OPEN_CONTEXT[0] is None:
            return False
        opened, keywords = UNCERTAINTY_OPEN_CONTEXT[0]
        declared = dict(keywords)
        return (declared.get('directory') is False and declared.get('create', False) is False
                and opened[1] == ORIGINAL_NAMES[0] and args[0]._role == 'relative')
    if identifier == 'native.close' and case == 'U008':
        return _uncertainty_resource_role(kwargs['_custody_slot']) == ('held_reader', 'original', 0)
    if identifier != 'custody.closed':
        return False
    expected = ('creator', 'store', 0) if case == 'U002' else (
        ('descriptor', 'store', 0) if case == 'U003' else ('held_reader', 'original', 0))
    return _uncertainty_resource_role(args[0]) == expected


def _uncertainty_mark(identifier, slot, detail):
    UNCERTAINTY_CONTROL.update(triggered=True, slot=slot)
    # The original slot's immutable receipts and ordinary return arguments
    # stay in child memory; no HANDLE/pointer is queried, printed or exported.
    state = (slot._known, slot._return_seen, slot._close_attempted, slot._receipt)
    UNCERTAINTY_EVENTS.append((UNCERTAINTY_CONTROL['case'], identifier, slot, detail, state))


def _uncertainty_before(identifier, args, kwargs):
    if not _uncertainty_target(identifier, args, kwargs):
        return args, kwargs
    case = UNCERTAINTY_CONTROL['case']
    if case in ('U001', 'U002', 'U003'):
        slot, actual = args
        if slot._kind == 'HANDLE':
            assert type(actual) is int and actual != 0
            bad = None
        else:
            assert actual is None
            bad = 1
        _uncertainty_mark(identifier, slot, (actual, bad))
        return (slot, bad), kwargs
    if case in ('U004', 'U006', 'U008'):
        slot = kwargs['_custody_slot'] if case == 'U008' else args[0]
        _uncertainty_mark(identifier, slot, (args, tuple(kwargs.items())))
        raise UNCERTAINTY_CONTROL['first']
    return args, kwargs


def _uncertainty_after(identifier, args, kwargs, result):
    if not _uncertainty_target(identifier, args, kwargs):
        return result
    if UNCERTAINTY_CONTROL['case'] in ('U005', 'U007'):
        _uncertainty_mark(identifier, args[0], (args, result))
        raise UNCERTAINTY_CONTROL['first']
    return result


def _assert_no_business_after_first(start, first):
    events = tuple(CONSUMER_EVENTS[start:])
    first_position = next(index for index, event in enumerate(events) if event[1] == 'raise')
    assert events[first_position][3] is first
    assert not any(event[0] in ('io.read', 'native.exact', 'native.chunk', 'native.open', 'native.parent',
                              'io.begin', 'io.open', 'gen.begin') and event[1] == 'call'
                   for event in events[first_position + 1:])


@pytest.mark.parametrize('identifier', FIXED_UNCERTAINTY_IDS, ids=FIXED_UNCERTAINTY_IDS)
def test_fixed_native_ack_debt_and_effect(r915_prefix_environment, identifier):
    env = r915_prefix_environment
    assert UNCERTAINTY_CONTROL['case'] is None and UNCERTAINTY_OPEN_CONTEXT[0] is None
    assert BOUNDARY_CONTROL['case'] is CASE_CONTROL['mode'] is None
    start, marker_start = len(CONSUMER_EVENTS), len(UNCERTAINTY_EVENTS)
    kinds = {'U004': RuntimeError, 'U005': SystemExit, 'U006': SystemExit,
             'U007': KeyboardInterrupt, 'U008': RuntimeError}
    first = kinds[identifier]('r930 controlled consumer acknowledgment fault ' + identifier) if identifier in kinds else None
    UNCERTAINTY_CONTROL.update(case=identifier, triggered=False, first=first, slot=None)
    try:
        with pytest.raises(BaseException) as error:
            invoke_private_initial(env)
        assert UNCERTAINTY_CONTROL['triggered'] is True
        if first is None:
            assert getattr(error.value, 'code', None) == UNCERTAINTY_ERROR_CODES[identifier]
        else:
            assert error.value is first
        marked, = UNCERTAINTY_EVENTS[marker_start:]
        assert marked[0] == identifier
        slot = UNCERTAINTY_CONTROL['slot']
        snapshot = _graph_snapshot(start)
        graph, original = snapshot[:2]
        value, journal = original[2], original[5]
        assert slot._journal is journal and value._first is error.value
        custody = OBSERVATION_BINDINGS['custody']
        actual = custody._JOURNAL_RECEIPT.__get__(journal, custody._StorePrepareJournal)
        assert actual.first_present is True and actual.reset_state == 'reset'
        assert actual.source_closed is actual.write_authority is False
        _assert_no_business_after_first(start, error.value)
        _assert_base_data(env)
        if identifier in UNKNOWN_IDS:
            assert value._phase == 'retained_failed'
            assert actual.unknown_count == 1 and actual.all_registered_native_closed is False
            assert actual.facts[slot._ordinal] is slot._receipt
            assert slot._receipt.native_state == 'UNKNOWN'
            if identifier == 'U006':
                assert slot._known is slot._return_seen is slot._close_attempted is False
                assert slot._receipt is slot._unknown_acquire
                assert not any(event[0] == 'custody.closed' and event[2][0][0] is slot
                               for event in _events_since(start))
            else:
                assert slot._known is slot._return_seen is slot._close_attempted is True
                assert slot._receipt is slot._unknown_close
                acknowledgments = _events_since(start, 'custody.closed', 'call')
                assert sum(event[2][0][0] is slot for event in acknowledgments) == 1
            io = OBSERVATION_BINDINGS['io']
            assert io._GRAPH_ORIGINALS[id(graph)] is original
            history_row = custody._REGISTRATION_HISTORY[id(journal)]
            assert type(history_row[2]) is tuple and len(history_row[2]) == actual.registered_count
            assert any(item is slot for item in history_row[2])
            assert any(item is slot for item in io._GRAPH_HISTORIES[id(graph)])
            assert not _events_since(start, 'io.retire', 'call')
            assert not _events_since(start, 'history.release', 'call')
            assert not _events_since(start, 'io.release', 'call')
            event_count = len(CONSUMER_EVENTS)
            with pytest.raises(BaseException) as replay:
                OBSERVATION_BINDINGS['runtime']._retire_continuation(value)
            assert replay.value is error.value and len(CONSUMER_EVENTS) == event_count
        else:
            assert value._phase == 'retired_uncompleted'
            assert actual.unknown_count == 0 and actual.all_registered_native_closed is False
            assert slot._receipt is slot._closed_fact and slot._receipt.native_state == 'CLOSED'
            _assert_real_store_close_calls(start, journal, actual,
                acknowledged_raise=(slot, error.value) if identifier == 'U005' else None)
            assert id(graph) not in OBSERVATION_BINDINGS['io']._GRAPH_ORIGINALS
            assert id(value) not in OBSERVATION_BINDINGS['runtime']._RETAINED_RETIREMENTS
            acknowledgments = _events_since(start, 'custody.closed', 'call')
            assert sum(event[2][0][0] is slot for event in acknowledgments) == 1
        if identifier in ('U002', 'U003'):
            read_events = _events_since(start, 'io.read', 'call')
            assert not any(_resource_role_from_original_argument(event[2][0][1])
                           in (('post_readback', 'store', 0), ('held_reader', 'store', 0)) for event in read_events)
    finally:
        UNCERTAINTY_CONTROL.update(case=None, triggered=False, first=None, slot=None)
        assert UNCERTAINTY_OPEN_CONTEXT[0] is None


def _resource_role_from_original_argument(resource):
    # Return-event snapshots preserve this row before globals are retired.
    for event in CONSUMER_EVENTS:
        if event[0] == 'io.read' and event[1] == 'return' and event[4][0] is resource:
            row = event[4]
            return row[1], row[3], row[4]
    original = OBSERVATION_BINDINGS['io']._RESOURCE_ORIGINALS[id(resource)]
    cycle = OBSERVATION_BINDINGS['io']._CYCLE_ORIGINALS[id(original[3])]
    return original[4], cycle[5][2], cycle[5][3]


def _prepare_actual_weak_release(env, identifier):
    start, snapshot = _run_success(env)
    _assert_read_and_owner_facts(start, snapshot)
    _assert_two_actual_generations(start, snapshot)
    _assert_actual_budget(start, snapshot)
    io = OBSERVATION_BINDINGS['io']
    graph = snapshot[0]
    key = id(graph)
    released = io._GRAPH_RELEASES[key]
    reference = released[0]
    callback = reference.__callback__
    pin = io._CALLBACK_PINS[id(callback)]
    assert reference() is graph and released[2] == 'released'
    assert pin == (callback, reference, 'graph_release', key, released[1])
    if identifier == 'W002':
        callback._key = key + 1
    elif identifier == 'W003':
        callback._kind = 'r930 controlled wrong kind'
    # Only own observation lists lose refs; no product registry/owner is reset,
    # retired, deleted, restored or directly passed to its weak callback.
    CONSUMER_EVENTS.clear()
    METHOD_EVENTS.clear()
    BOUNDARY_EVENTS.clear()
    UNCERTAINTY_EVENTS.clear()
    return io, key, released, reference, callback, pin


@pytest.mark.parametrize('identifier', FIXED_WEAK_IDS, ids=FIXED_WEAK_IDS)
def test_actual_graph_weak_callback_after_explicit_gc(r915_prefix_environment, identifier):
    assert BOUNDARY_CONTROL['case'] is CASE_CONTROL['mode'] is UNCERTAINTY_CONTROL['case'] is None
    io, key, released, reference, callback, pin = _prepare_actual_weak_release(r915_prefix_environment, identifier)
    gc.collect()
    assert reference() is None
    if identifier == 'W001':
        assert key not in io._GRAPH_RELEASES and id(callback) not in io._CALLBACK_PINS
    else:
        assert io._GRAPH_RELEASES[key] is released and io._CALLBACK_PINS[id(callback)] is pin
    _assert_base_data(r915_prefix_environment)
    assert BOUNDARY_READ_CONTEXT[0] is UNCERTAINTY_OPEN_CONTEXT[0] is None


"""Controlled query representations and independent census retirements."""
QUERY_CONTROL = {'case': None, 'triggered': False, 'query': None, 'first': None, 'original_row': None}
QUERY_DISPATCH_CONTEXT = [None]
QUERY_EVENTS = []
FIXED_QUERY_IDS = tuple('Q%03d' % index for index in range(1, 9))
FIXED_CENSUS_RETIRE_IDS = ('D001', 'D002', 'D003', 'D004')
QUERY_DEBT_IDS = ('Q001', 'Q002', 'Q003', 'Q004', 'Q005')
QUERY_EXPECTED_CODES = {
    'Q001': 'ENROLLMENT_STORE_CENSUS_QUERY_PENDING',
    'Q002': 'ENROLLMENT_STORE_CENSUS_QUERY_PENDING',
    'Q003': 'ENROLLMENT_STORE_CENSUS_QUERY_PENDING',
    'Q004': 'ENROLLMENT_STORE_CENSUS_COMPLETION_UNCONFIRMED',
    'Q005': 'ENROLLMENT_STORE_CENSUS_COMPLETION_UNCONFIRMED',
    'Q008': 'ENROLLMENT_STORE_CENSUS_BINDING_INVALID',
}
BODY_OPERATION_PLAN.update({identifier: ('_owned_store_initial_with_actual_ex', 1)
                            for identifier in (*FIXED_QUERY_IDS, *FIXED_CENSUS_RETIRE_IDS)})
QUERY_EXTRA_CALLS = {'retained_cases': ('runtime._retire_continuation_rejection', 1)}


def _query_expected_subject(row):
    case = QUERY_CONTROL['case']
    scope, role = row[3], OBSERVATION_BINDINGS['census']._SCAN_ROWS[id(row[2])][3]
    directories = OBSERVATION_BINDINGS['directories']
    if case == 'Q001':
        return type(scope) is directories._StoreDirectoryScope and role == 'parent'
    return type(scope) is directories._StoreFinalCensusGeneration and role == ('root' if case in ('Q003', 'Q004') else 'parent')


def _query_record(identifier, query, detail):
    QUERY_CONTROL.update(triggered=True, query=query)
    QUERY_EVENTS.append((QUERY_CONTROL['case'], identifier, query, detail))


def _query_before(identifier, args, kwargs):
    case = QUERY_CONTROL['case']
    if case is None or QUERY_CONTROL['triggered']:
        return args, kwargs
    census = OBSERVATION_BINDINGS['census']
    if identifier == 'census.dispatch' and case in ('Q006', 'Q008'):
        query = args[0]
        row = census._QUERY_ROWS[id(query)]
        if row[8] == 0 and _query_expected_subject(row):
            QUERY_CONTROL['original_row'] = row
            _query_record(identifier, query, row)
            if case == 'Q006':
                raise QUERY_CONTROL['first']
            query._buffer = type(row[5])()
    if identifier == 'census.retire' and case in ('D001', 'D003'):
        subject = args[0]
        is_initial = type(subject) is OBSERVATION_BINDINGS['directories']._StoreDirectoryScope
        if is_initial is (case == 'D001'):
            QUERY_CONTROL['triggered'] = True
            QUERY_EVENTS.append((case, identifier, subject, None))
            raise QUERY_CONTROL['first']
    return args, kwargs


def _query_after(identifier, args, kwargs, result):
    case = QUERY_CONTROL['case']
    if case is None:
        return result
    census = OBSERVATION_BINDINGS['census']
    if identifier == 'census.signed' and case in QUERY_DEBT_IDS and QUERY_DISPATCH_CONTEXT[0] is not None:
        context = QUERY_DISPATCH_CONTEXT[0]
        query = context[0]
        row = census._QUERY_ROWS[id(query)]
        if QUERY_CONTROL['query'] is None and row[8] == 0 and _query_expected_subject(row):
            assert row[9] is row[10] is True and row[11] == 0
            assert result == 0 and row[6].status == 0 and row[6].information > 0
            QUERY_CONTROL['original_row'] = row
            _query_record(identifier, query, (row, args, result))
        if QUERY_CONTROL['query'] is query:
            ordinal = context[1]
            context[1] += 1
            assert ordinal in (0, 1)
            if case in ('Q001', 'Q002', 'Q003'):
                return census._PENDING
            if case == 'Q004':
                return 0 if ordinal == 0 else census._NO_MORE_FILES
            if ordinal == 1:
                # Controlled completion representation, not a native return
                # or query-row edit. Original output storage remains owned.
                row[6].information = 0
            return 0
    if QUERY_CONTROL['triggered']:
        return result
    if identifier == 'census.dispatch' and case == 'Q007':
        query = args[0]
        row = census._QUERY_ROWS[id(query)]
        if row[8] == 0 and _query_expected_subject(row):
            assert row[15] is True and id(query) not in census._QUERY_DEBTS
            QUERY_CONTROL['original_row'] = row
            _query_record(identifier, query, row)
            raise QUERY_CONTROL['first']
    if identifier == 'census.retire' and case in ('D002', 'D004'):
        subject = args[0]
        is_initial = type(subject) is OBSERVATION_BINDINGS['directories']._StoreDirectoryScope
        if is_initial is (case == 'D002'):
            QUERY_CONTROL['triggered'] = True
            QUERY_EVENTS.append((case, identifier, subject, result))
            raise QUERY_CONTROL['first']
    return result


def _actual_store_objects_from_methods(env):
    events = method_events_since(env['body_event_start'])
    stage_enter, = (event for event in events if event[0] == 'stage.enter' and event[1] == 'return')
    directory_enter, = (event for event in events if event[0] == 'directory.enter' and event[1] == 'call')
    # Initial census runs inside __enter__. Q001 raises before that method
    # returns; its actual self is already recorded by the original call seam.
    directory = directory_enter[2][0][0]
    terminal, = (event for event in events if event[0] == 'directory.enter' and event[1] in ('return', 'raise'))
    assert terminal[2][0][0] is directory
    if QUERY_CONTROL['case'] == 'Q001':
        assert terminal[1] == 'raise'
    else:
        assert terminal[1] == 'return' and terminal[3] is directory
    return stage_enter[2][0][0], stage_enter[3], directory


@pytest.mark.parametrize('identifier', FIXED_QUERY_IDS, ids=FIXED_QUERY_IDS)
def test_fixed_query_debt_and_safe_retirement(r915_prefix_environment, identifier):
    env = r915_prefix_environment
    assert QUERY_CONTROL['case'] is None and QUERY_DISPATCH_CONTEXT[0] is None
    assert BOUNDARY_CONTROL['case'] is CASE_CONTROL['mode'] is UNCERTAINTY_CONTROL['case'] is None
    first = RuntimeError('r931 before dispatch') if identifier == 'Q006' else (
        SystemExit('r931 after completed dispatch') if identifier == 'Q007' else None)
    start, marker_start = len(CONSUMER_EVENTS), len(QUERY_EVENTS)
    QUERY_CONTROL.update(case=identifier, triggered=False, query=None, first=first, original_row=None)
    try:
        with pytest.raises(BaseException) as error:
            invoke_private_initial(env)
        assert QUERY_CONTROL['triggered'] is True
        if first is None:
            assert getattr(error.value, 'code', None) == QUERY_EXPECTED_CODES[identifier]
        else:
            assert error.value is first
        marked, = QUERY_EVENTS[marker_start:]
        assert marked[0] == identifier
        query, original = QUERY_CONTROL['query'], QUERY_CONTROL['original_row']
        assert query is marked[2] and type(original) is tuple and len(original) == 18
        stage, journal, directory = _actual_store_objects_from_methods(env)
        assert type(stage) is OBSERVATION_BINDINGS['runtime']._StorePrepareStage
        census, dirs = OBSERVATION_BINDINGS['census'], OBSERVATION_BINDINGS['directories']
        if identifier in QUERY_DEBT_IDS:
            directory_original = dirs._DIRECTORY_ORIGINAL_ROWS[id(directory)]
            value = directory_original[3][0]
        else:
            value = stage._continuation
        assert value._first is error.value
        custody = OBSERVATION_BINDINGS['custody']
        actual = custody._JOURNAL_RECEIPT.__get__(journal, custody._StorePrepareJournal)
        assert actual.first_present is True and actual.reset_state == 'reset'
        assert actual.unknown_count == 0 and actual.all_registered_native_closed is False
        assert actual.source_closed is actual.write_authority is False
        _assert_real_store_close_calls(start, journal, actual)
        _assert_no_business_after_first(start, error.value)
        _assert_base_data(env)
        if identifier in QUERY_DEBT_IDS:
            assert value._phase == 'retained_failed'
            current, debt = census._QUERY_ROWS[id(query)], census._QUERY_DEBTS[id(query)]
            assert current[0] is query and current[1] is original[1]
            assert current[9] is current[10] is True and current[15] is False
            assert debt[0] is query and debt[1] is original[1] and debt[4] is original[4]
            assert debt[5] is current[5] is original[5] and debt[6] is current[6] is original[6]
            assert debt[3] is current[3] is original[3] and debt[2] is current[2] is original[2]
            assert debt[7] == current[7] == original[7] and debt[8] == current[8] == original[8]
            assert query._buffer is current[5] and query._iosb is current[6]
            assert id(current[3]) in census._SCOPE_CLEANUP
            assert not _events_since(start, 'census.retire', 'call')
            assert not _events_since(start, 'history.release', 'call')
            if identifier == 'Q001':
                assert not _events_since(start, 'io.new', 'call')
            else:
                graph, row = _graph_snapshot(start)[:2]
                assert OBSERVATION_BINDINGS['io']._GRAPH_ORIGINALS[id(graph)] is row
                assert row[2] is value and row[5] is journal
            before = len(CONSUMER_EVENTS)
            with pytest.raises(BaseException) as replay:
                OBSERVATION_BINDINGS['runtime']._retire_continuation(value)
            assert replay.value is error.value and len(CONSUMER_EVENTS) == before
        else:
            assert value._phase == 'retired_uncompleted'
            assert id(query) not in census._QUERY_ROWS and id(query) not in census._QUERY_DEBTS
            assert id(original[3]) not in census._SCOPE_CLEANUP
            assert id(value) not in OBSERVATION_BINDINGS['runtime']._RETAINED_RETIREMENTS
            if identifier in ('Q006', 'Q008'):
                assert original[9] is original[10] is original[15] is False
            else:
                assert original[9] is original[10] is original[15] is True
            assert len(_events_since(start, 'history.release', 'return')) == 1
    finally:
        QUERY_CONTROL.update(case=None, triggered=False, query=None, first=None, original_row=None)
        assert QUERY_DISPATCH_CONTEXT[0] is None


@pytest.mark.parametrize('identifier', FIXED_CENSUS_RETIRE_IDS, ids=FIXED_CENSUS_RETIRE_IDS)
def test_independent_generation_retirement_return_effect(r915_prefix_environment, identifier):
    env = r915_prefix_environment
    assert QUERY_CONTROL['case'] is None
    kinds = {'D001': RuntimeError, 'D002': SystemExit, 'D003': ValueError, 'D004': KeyboardInterrupt}
    first = kinds[identifier]('r931 fixed census metadata consumer ' + identifier)
    start, marker_start = len(CONSUMER_EVENTS), len(QUERY_EVENTS)
    QUERY_CONTROL.update(case=identifier, triggered=False, query=None, first=first, original_row=None)
    try:
        with pytest.raises(BaseException) as error:
            invoke_private_initial(env)
        assert error.value is first and QUERY_CONTROL['triggered'] is True
        assert len(QUERY_EVENTS[marker_start:]) == 1
        snapshot = _graph_snapshot(start)
        value, journal = snapshot[1][2], snapshot[1][5]
        runtime, dirs, census = (OBSERVATION_BINDINGS[name] for name in ('runtime', 'directories', 'census'))
        retained = runtime._RETAINED_RETIREMENTS[id(value)]
        assert retained._first is first and value._phase == 'retained_failed'
        assert retained._file_io_graph is snapshot[0]
        assert retained._file_io_retire_returned is retained._file_io_effects_confirmed is True
        assert retained._directory_retire_returned is retained._history_release_returned is False
        stage, observed_journal, directory = _actual_store_objects_from_methods(env)
        assert observed_journal is journal
        record = dirs._DIRECTORY_RETIREMENT_ROWS[id(directory)]
        original = record[0]
        generation = original[5][19]
        selected = directory if identifier in ('D001', 'D002') else generation
        other = generation if selected is directory else directory
        assert len(_events_since(start, 'census.retire', 'call')) == 2
        assert record[1] is (selected is generation) and record[2] is (selected is directory)
        assert record[3:6] == (False, False, False)
        assert not _events_since(start, 'history.release', 'call')
        assert id(other) not in census._SCOPE_CLEANUP
        other_marker = census._RETIRED_SCOPES[id(other)]
        assert other_marker[2:7] == ('retired', True, True, True, True)
        if identifier in ('D001', 'D003'):
            assert id(selected) in census._SCOPE_CLEANUP and id(selected) not in census._RETIRED_SCOPES
        else:
            assert id(selected) not in census._SCOPE_CLEANUP
            selected_marker = census._RETIRED_SCOPES[id(selected)]
            assert selected_marker[2:7] == ('retired', True, True, True, True)
        custody = OBSERVATION_BINDINGS['custody']
        actual = custody._JOURNAL_RECEIPT.__get__(journal, custody._StorePrepareJournal)
        assert actual.unknown_count == 0 and actual.all_registered_native_closed is True
        assert actual.source_closed is actual.write_authority is False
        assert actual.first_present is False
        _assert_real_store_close_calls(start, journal, actual)
        assert type(custody._REGISTRATION_HISTORY[id(journal)][2]) is tuple
        _assert_base_data(env)
        before = len(CONSUMER_EVENTS)
        with pytest.raises(BaseException) as replay:
            runtime._retire_continuation(value)
        assert replay.value is first and len(CONSUMER_EVENTS) == before
    finally:
        QUERY_CONTROL.update(case=None, triggered=False, query=None, first=None, original_row=None)
        assert QUERY_DISPATCH_CONTEXT[0] is None


"""Explicit same-budget boundary charges and metadata completion faults."""
COMPLETION_CONTROL = {'case': None, 'triggered': False, 'first': None, 'boundary': None, 'snapshot': None}
COMPLETION_EVENTS = []
FIXED_CAPACITY_IDS = ('C001', 'C002')
FIXED_COMPLETION_IDS = ('M001', 'M002', 'M003', 'M004')
BODY_OPERATION_PLAN.update({identifier: ('_owned_store_initial_with_actual_ex', 1)
                            for identifier in (*FIXED_CAPACITY_IDS, *FIXED_COMPLETION_IDS)})
COMPLETION_EXTRA_CALLS = {
    'capacity_each': ('actual_budget.request_positive_delta_once', 'actual_budget.charge_positive_delta_once'),
    'retained_each': ('runtime._retire_continuation_rejection', 1),
}


def _completion_target(identifier, args):
    case = COMPLETION_CONTROL['case']
    if case in ('M001', 'M002') and identifier == 'runtime.clear':
        runtime = OBSERVATION_BINDINGS['runtime']
        scope, before, after = args
        retained = runtime._RETAINED_RETIREMENTS.get(id(scope._continuation))
        return (retained is not None and retained._scope is scope
                and before is retained._row and after is retained._cleared_row)
    return case in ('M003', 'M004') and identifier == 'runtime.observe' and args[0]._file_io_graph is not None


def _completion_before(identifier, args, kwargs):
    case = COMPLETION_CONTROL['case']
    if case is None or COMPLETION_CONTROL['triggered']:
        return args, kwargs
    if case in FIXED_CAPACITY_IDS and identifier == 'io.new':
        scope = args[0]
        original = OBSERVATION_BINDINGS['directories']._DIRECTORY_ORIGINAL_ROWS[id(scope)]
        value, plan, budget, journal = original[3][0], original[3][1], original[3][4], original[3][3]
        assert value._budget is plan._budget is budget
        demand = 6 * sum(map(len, plan._file_raws)) + 2 * sum(map(len, plan._originals)) + sum(map(len, plan._originals[7:11]))
        assert plan._empty_io_bytes == demand and 0 < demand < 1048576
        before = budget.control_bytes
        target = 1048576 - demand - (1 if case == 'C001' else 0)
        assert type(before) is int and 0 <= before < target
        delta = target - before
        # Explicit controlled extra consumption through the original methods.
        # It is not evidence that these bytes were naturally read or written.
        requested = budget.request(delta)
        assert requested == delta
        returned = budget.charge(delta)
        assert returned is None and budget.control_bytes == target
        detail = (budget, before, delta, target, demand, value, journal, scope)
        COMPLETION_CONTROL.update(triggered=True, boundary=detail)
        COMPLETION_EVENTS.append((case, identifier, 'charged', detail))
    elif case in ('M001', 'M003') and _completion_target(identifier, args):
        COMPLETION_CONTROL['triggered'] = True
        COMPLETION_EVENTS.append((case, identifier, 'before', args))
        raise COMPLETION_CONTROL['first']
    return args, kwargs


def _completion_after(identifier, args, kwargs, result):
    case = COMPLETION_CONTROL['case']
    if case in ('M002', 'M004') and not COMPLETION_CONTROL['triggered'] and _completion_target(identifier, args):
        COMPLETION_CONTROL['triggered'] = True
        COMPLETION_EVENTS.append((case, identifier, 'after', (args, result)))
        raise COMPLETION_CONTROL['first']
    return result


def _completion_failure(identifier, args, primary):
    if COMPLETION_CONTROL['case'] != 'C002' or identifier != 'io.new':
        return None
    assert COMPLETION_CONTROL['triggered'] is True and COMPLETION_CONTROL['snapshot'] is None
    detail = COMPLETION_CONTROL['boundary']
    io = OBSERVATION_BINDINGS['io']
    attempt = io._GRAPH_ATTEMPTS[id(detail[5])]
    graph = attempt[2]
    snapshot = _snapshot_result('io.new', args, graph)
    assert snapshot[1][2] is detail[5] and snapshot[1][5] is detail[6]
    assert io._GRAPH_STATES[id(graph)][:4] == ((), (), (), ())
    assert io._GRAPH_STATES[id(graph)][9] == ()
    COMPLETION_CONTROL['snapshot'] = snapshot
    COMPLETION_EVENTS.append(('C002', identifier, 'original_raise', primary))
    return None


@pytest.mark.parametrize('identifier', FIXED_CAPACITY_IDS, ids=FIXED_CAPACITY_IDS)
def test_same_actual_budget_strict_capacity_boundary(r915_prefix_environment, identifier):
    env = r915_prefix_environment
    assert COMPLETION_CONTROL['case'] is None
    assert CASE_CONTROL['mode'] is BOUNDARY_CONTROL['case'] is UNCERTAINTY_CONTROL['case'] is QUERY_CONTROL['case'] is None
    start, marker_start = len(CONSUMER_EVENTS), len(COMPLETION_EVENTS)
    COMPLETION_CONTROL.update(case=identifier, triggered=False, first=None, boundary=None, snapshot=None)
    try:
        if identifier == 'C001':
            result = invoke_private_initial(env)
            assert COMPLETION_CONTROL['triggered'] is True
            _assert_source_prefix_result(result, env)
            snapshot = _graph_snapshot(start)
            _assert_initial_terminal_state(env)
            _assert_real_store_files(env, snapshot)
            _assert_read_and_owner_facts(start, snapshot)
            _assert_two_actual_generations(start, snapshot)
            _assert_actual_budget(start, snapshot)
            detail = COMPLETION_CONTROL['boundary']
            assert snapshot[5] is detail[0] and snapshot[6] == detail[3]
            assert snapshot[7] == detail[4] and detail[3] + detail[4] == 1048575
            assert detail[0].control_bytes == 1048575
        else:
            with pytest.raises(BaseException) as error:
                invoke_private_initial(env)
            assert COMPLETION_CONTROL['triggered'] is True
            assert getattr(error.value, 'code', None) == 'ENROLLMENT_STORE_CONTENT_LIMIT'
            detail, snapshot = COMPLETION_CONTROL['boundary'], COMPLETION_CONTROL['snapshot']
            assert detail[3] + detail[4] == 1048576 and detail[0].control_bytes == detail[3]
            assert snapshot[5] is detail[0] and snapshot[6] == detail[3] and snapshot[7] == detail[4]
            graph, original = snapshot[:2]
            value, journal = original[2], original[5]
            assert value is detail[5] and journal is detail[6]
            assert value._first is error.value and value._phase == 'retired_uncompleted'
            io, runtime = OBSERVATION_BINDINGS['io'], OBSERVATION_BINDINGS['runtime']
            assert id(graph) not in io._GRAPH_ORIGINALS and id(graph) not in io._GRAPH_STATES
            assert id(value) not in runtime._RETAINED_RETIREMENTS
            assert io._GRAPH_ATTEMPTS[id(value)][2] is None
            assert not _events_since(start, 'io.new', 'return')
            assert len(_events_since(start, 'io.new', 'raise')) == 1
            assert not any(event[0] in ('io.begin', 'io.open', 'io.read', 'native.open', 'gen.begin')
                           and event[1] == 'call' for event in CONSUMER_EVENTS[start:])
            assert len(_events_since(start, 'history.release', 'return')) == 1
            custody = OBSERVATION_BINDINGS['custody']
            receipt = custody._JOURNAL_RECEIPT.__get__(journal, custody._StorePrepareJournal)
            assert receipt.unknown_count == 0 and receipt.all_registered_native_closed is False
            assert receipt.first_present is True and receipt.reset_state == 'reset'
            assert receipt.source_closed is receipt.write_authority is False
            _assert_real_store_close_calls(start, journal, receipt)
            _assert_no_business_after_first(start, error.value)
            assert_no_store_files(env)
        detail = COMPLETION_CONTROL['boundary']
        assert detail[1] + detail[2] == detail[3] and detail[2] > 0
        marked = tuple(COMPLETION_EVENTS[marker_start:])
        assert len(marked) == (1 if identifier == 'C001' else 2)
        assert marked[0][3] is detail and marked[0][2] == 'charged'
        if identifier == 'C002':
            assert marked[1][2] == 'original_raise' and marked[1][3] is error.value
        _assert_base_data(env)
    finally:
        COMPLETION_CONTROL.update(case=None, triggered=False, first=None, boundary=None, snapshot=None)


@pytest.mark.parametrize('identifier', FIXED_COMPLETION_IDS, ids=FIXED_COMPLETION_IDS)
def test_metadata_row_clear_and_observer_return_effect(r915_prefix_environment, identifier):
    env = r915_prefix_environment
    assert COMPLETION_CONTROL['case'] is None
    assert CASE_CONTROL['mode'] is BOUNDARY_CONTROL['case'] is UNCERTAINTY_CONTROL['case'] is QUERY_CONTROL['case'] is None
    kinds = {'M001': RuntimeError, 'M002': SystemExit, 'M003': ValueError, 'M004': KeyboardInterrupt}
    first = kinds[identifier]('r932 metadata completion fault ' + identifier)
    start, marker_start = len(CONSUMER_EVENTS), len(COMPLETION_EVENTS)
    COMPLETION_CONTROL.update(case=identifier, triggered=False, first=first, boundary=None, snapshot=None)
    try:
        with pytest.raises(BaseException) as error:
            invoke_private_initial(env)
        assert error.value is first and COMPLETION_CONTROL['triggered'] is True
        marked, = COMPLETION_EVENTS[marker_start:]
        assert marked[0] == identifier and marked[2] == ('before' if identifier in ('M001', 'M003') else 'after')
        snapshot = _graph_snapshot(start)
        graph, original = snapshot[:2]
        value, journal = original[2], original[5]
        runtime, io, custody = (OBSERVATION_BINDINGS[name] for name in ('runtime', 'io', 'custody'))
        retained = runtime._RETAINED_RETIREMENTS[id(value)]
        assert retained._first is value._first is first and value._phase == 'retained_failed'
        assert retained._file_io_graph is graph and retained._journal is journal
        assert retained._file_io_retire_returned is retained._file_io_effects_confirmed is True
        assert retained._directory_retire_returned is retained._content_returned is retained._history_release_returned is True
        assert retained._file_io_release_returned is retained._directory_release_returned is False
        history = custody._REGISTRATION_HISTORY[id(journal)]
        assert history[0] is retained._history_row[0] and history[1] is retained._history_row[1]
        assert history[2] is None and history[3] == retained._history_row[3]
        actual_row = runtime._SCOPE_ROWS[id(retained._scope)]
        if identifier in ('M001', 'M002'):
            assert retained._row_clear_returned is False and retained._observation_returned is True
            assert retained._history_release_observed is True
            assert retained._row_clear_observed is (identifier == 'M002')
            assert actual_row is (retained._row if identifier == 'M001' else retained._cleared_row)
        else:
            assert retained._row_clear_returned is True and retained._observation_returned is False
            assert actual_row is retained._cleared_row
            assert retained._history_release_observed is retained._row_clear_observed is (identifier == 'M004')
        assert io._GRAPH_ORIGINALS[id(graph)] is original and id(graph) in io._RETIRED_METADATA
        assert not _events_since(start, 'io.release', 'call') and not _events_since(start, 'dir.release', 'call')
        receipt = custody._JOURNAL_RECEIPT.__get__(journal, custody._StorePrepareJournal)
        assert receipt.unknown_count == 0 and receipt.all_registered_native_closed is True
        assert receipt.reset_state == 'reset' and receipt.first_present is False
        assert receipt.source_closed is receipt.write_authority is False
        _assert_real_store_close_calls(start, journal, receipt)
        _assert_base_data(env)
        before = len(CONSUMER_EVENTS)
        with pytest.raises(BaseException) as replay:
            runtime._retire_continuation(value)
        assert replay.value is first and len(CONSUMER_EVENTS) == before
    finally:
        COMPLETION_CONTROL.update(case=None, triggered=False, first=None, boundary=None, snapshot=None)


"""First observer failures preserve return, actual effect and strong roots."""
OBSERVER_CONTROL = {'case': None, 'triggered': False, 'first': None, 'record': None}
OBSERVER_EVENTS = []
FIXED_OBSERVER_IDS = ('O001', 'O002', 'O003', 'O004', 'O005', 'O006')
OBSERVER_SEAMS = {'O001': 'io.effect', 'O002': 'io.effect',
                  'O003': 'io.release.effect', 'O004': 'io.release.effect',
                  'O005': 'dir.effect', 'O006': 'dir.effect'}
OBSERVER_EXTRA_CALLS = {'retained_each': ('runtime._retire_continuation_rejection', 1)}
BODY_OPERATION_PLAN.update({identifier: ('_owned_store_initial_with_actual_ex', 1) for identifier in FIXED_OBSERVER_IDS})


def _observer_record_at_target(identifier, args):
    case = OBSERVER_CONTROL['case']
    if case is None or OBSERVER_CONTROL['triggered'] or OBSERVER_SEAMS[case] != identifier:
        return None
    runtime = OBSERVATION_BINDINGS['runtime']
    if identifier == 'io.release.effect':
        graph = args[0]
        released = OBSERVATION_BINDINGS['io']._GRAPH_RELEASES[id(graph)]
        value_id = released[3][0]
    else:
        value_id = id(args[0])
    record = runtime._RETAINED_RETIREMENTS[value_id]
    assert record._file_io_graph is not None and record._file_io_retire_returned is True
    if identifier == 'io.effect':
        # Select the first, separate observation; do not inject into the later
        # aggregate effect observation, which may safely read the same effect.
        assert record._file_io_effects_confirmed is False
    elif identifier == 'io.release.effect':
        assert args[0] is record._file_io_graph
        assert record._file_io_release_returned is True and record._file_io_release_effects_confirmed is False
    else:
        assert record._file_io_effects_confirmed is True and record._directory_retire_returned is False
    return record


def _observer_mark(identifier, args, phase, record, result=None):
    OBSERVER_CONTROL.update(triggered=True, record=record)
    OBSERVER_EVENTS.append((OBSERVER_CONTROL['case'], identifier, phase, record, args, result))


def _observer_before(identifier, args, kwargs):
    if OBSERVER_CONTROL['case'] in ('O001', 'O003', 'O005'):
        record = _observer_record_at_target(identifier, args)
        if record is not None:
            _observer_mark(identifier, args, 'before', record)
            raise OBSERVER_CONTROL['first']
    return args, kwargs


def _observer_after(identifier, args, kwargs, result):
    if OBSERVER_CONTROL['case'] in ('O002', 'O004', 'O006'):
        record = _observer_record_at_target(identifier, args)
        if record is not None:
            if identifier in ('io.effect', 'io.release.effect'):
                assert result is True
            else:
                assert type(result) is tuple and len(result) == 2
                assert result[0] == (True, True, True, True, True, True, False)
                assert result[1][:2] == (('retired', True, True, True, True),) * 2
                assert result[1][2:] == (True, True, True, True)
            _observer_mark(identifier, args, 'after', record, result)
            raise OBSERVER_CONTROL['first']
    return result


@pytest.mark.parametrize('identifier', FIXED_OBSERVER_IDS, ids=FIXED_OBSERVER_IDS)
def test_fixed_metadata_observer_failure_facts(r915_prefix_environment, identifier):
    env = r915_prefix_environment
    assert OBSERVER_CONTROL['case'] is None
    assert CASE_CONTROL['mode'] is BOUNDARY_CONTROL['case'] is UNCERTAINTY_CONTROL['case'] is QUERY_CONTROL['case'] is COMPLETION_CONTROL['case'] is None
    kinds = {'O001': RuntimeError, 'O002': SystemExit, 'O003': ValueError,
             'O004': KeyboardInterrupt, 'O005': RuntimeError, 'O006': SystemExit}
    first = kinds[identifier]('r933 observer first ' + identifier)
    start, marker_start = len(CONSUMER_EVENTS), len(OBSERVER_EVENTS)
    OBSERVER_CONTROL.update(case=identifier, triggered=False, first=first, record=None)
    try:
        with pytest.raises(BaseException) as error:
            invoke_private_initial(env)
        assert error.value is first and OBSERVER_CONTROL['triggered'] is True
        marked, = OBSERVER_EVENTS[marker_start:]
        assert marked[0] == identifier and marked[1] == OBSERVER_SEAMS[identifier]
        before_fault = identifier in ('O001', 'O003', 'O005')
        assert marked[2] == ('before' if before_fault else 'after')
        snapshot = _graph_snapshot(start)
        graph, original = snapshot[:2]
        value, journal = original[2], original[5]
        runtime, io, dirs, custody = (OBSERVATION_BINDINGS[name] for name in ('runtime', 'io', 'directories', 'custody'))
        record = runtime._RETAINED_RETIREMENTS[id(value)]
        assert record is marked[3] is OBSERVER_CONTROL['record']
        assert record._value is value and record._file_io_graph is graph and record._journal is journal
        assert record._first is value._first is first and value._phase == 'retained_failed'
        assert record._file_io_retire_returned is record._file_io_effects_confirmed is True
        assert record._observation_returned is True
        scope = record._directory_graph[0]
        assert dirs._DIRECTORY_CLEANUP_ROWS[id(scope)] is record._directory_graph[2]
        assert id(scope) in dirs._DIRECTORY_ORIGINAL_ROWS
        assert record._directory_release_returned is False
        assert not _events_since(start, 'dir.release', 'call')
        actual_row = runtime._SCOPE_ROWS[id(record._scope)]
        history = custody._REGISTRATION_HISTORY[id(journal)]
        assert history[0] is record._history_row[0] and history[1] is record._history_row[1]
        assert history[3] == record._history_row[3]
        calls = _events_since(start, marked[1], 'call')
        returns = _events_since(start, marked[1], 'return')
        raised = _events_since(start, marked[1], 'raise')
        assert len(raised) == 1 and raised[0][3] is first
        if identifier in ('O003', 'O004'):
            # The graph really released before its effect observer fault. No
            # product table or retired fields are restored to mimic a root.
            assert len(calls) == 1 and len(returns) == (0 if before_fault else 1)
            assert record._directory_retire_returned is record._content_returned is record._history_release_returned is True
            assert record._row_clear_returned is record._history_release_observed is record._row_clear_observed is True
            assert actual_row is record._cleared_row and history[2] is None
            assert record._file_io_release_returned is True and record._file_io_release_effects_confirmed is False
            assert all(id(graph) not in table for table in (io._GRAPH_ORIGINALS, io._GRAPH_STATES,
                io._GRAPH_HISTORIES, io._RETIRED_METADATA, io._RETIRED_PROOFS))
            released = io._GRAPH_RELEASES[id(graph)]
            assert released[0]() is graph and released[1] is original[1] and released[2] == 'released'
            assert io._GRAPH_ATTEMPTS[id(value)][2] is None
            assert graph._phase == 'retired'
            assert graph._pins == graph._cycles == graph._resources == graph._readers == graph._publications == graph._checkpoints == ()
            assert len(_events_since(start, 'io.release', 'return')) == 1
        else:
            assert len(calls) == 2 and len(returns) == (1 if before_fault else 2)
            assert record._directory_retire_returned is record._content_returned is record._history_release_returned is False
            assert record._row_clear_returned is record._history_release_observed is record._row_clear_observed is False
            assert actual_row is record._row and history[2] is record._history_row[2]
            assert record._file_io_release_returned is record._file_io_release_effects_confirmed is False
            assert io._GRAPH_ORIGINALS[id(graph)] is original and id(graph) in io._RETIRED_METADATA
            assert not _events_since(start, 'io.release', 'call')
            assert not _events_since(start, 'history.release', 'call')
            if identifier in ('O001', 'O002'):
                # The later aggregate observer can confirm IO effects, but it
                # cannot retroactively execute the directory path skipped at
                # the earlier decision or erase the original observer first.
                assert not _events_since(start, 'dir.retire', 'call')
                assert id(scope) not in dirs._DIRECTORY_RETIREMENT_ROWS
                assert returns[-1][3] is True
            else:
                assert len(_events_since(start, 'dir.retire', 'raise')) == 1
                retirement = dirs._DIRECTORY_RETIREMENT_ROWS[id(scope)]
                assert retirement[0] is record._directory_graph
                assert retirement[1:6] == (True, True, True, True, True)
                assert retirement[7] is True and retirement[8] is False
                assert retirement[6][:2] == (('retired', True, True, True, True),) * 2
                assert retirement[6][2:] == (True, True, True, True)
                assert returns[-1][3][0] == (True, True, True, True, True, True, False)
                assert scope._phase == 'retired' and scope._pins == ()
                assert dirs._DIRECTORY_ORIGINAL_ROWS[id(scope)][5][12] is first
        receipt = custody._JOURNAL_RECEIPT.__get__(journal, custody._StorePrepareJournal)
        assert receipt.unknown_count == 0 and receipt.all_registered_native_closed is True
        assert receipt.first_present is False and receipt.reset_state == 'reset'
        assert receipt.source_closed is receipt.write_authority is False
        _assert_real_store_close_calls(start, journal, receipt)
        _assert_base_data(env)
        before = len(CONSUMER_EVENTS)
        with pytest.raises(BaseException) as replay:
            runtime._retire_continuation(value)
        assert replay.value is first and len(CONSUMER_EVENTS) == before
    finally:
        OBSERVER_CONTROL.update(case=None, triggered=False, first=None, record=None)


"""Controlled faults at the existing clock after actual owner registration."""
CLOCK_CONTROL = {'case': None, 'triggered': False, 'first': None}
CLOCK_REGISTER_CONTEXT = [None]
CLOCK_EVENTS = []
FIXED_CLOCK_IDS = tuple('T%03d' % index for index in range(1, 9))
CLOCK_TARGETS = {
    'T001': ('held_reader', 'original', 0), 'T002': ('descriptor', 'store', 0),
    'T003': ('creator', 'store', 0), 'T004': ('live_readback', 'store', 0),
    'T005': ('post_readback', 'store', 0), 'T006': ('held_reader', 'store', 0),
    'T007': ('authority_checkpoint', 'original', 7), 'T008': ('creator', 'store', 0),
}
CLOCK_SOURCE_PIN = ('reno/personal_authority_namespace3_deadline.py', 4867,
                    'c0e48b6cdf642d819cc9e68938cb4b62ed95c43f0baa5bb9cca4095b34402e18')
BODY_OPERATION_PLAN.update({identifier: ('_owned_store_initial_with_actual_ex', 1) for identifier in FIXED_CLOCK_IDS})
CLOCK_EXTRA_CALLS = {}


def _clock_selected_registration(args, kwargs):
    case = CLOCK_CONTROL['case']
    if case is None or CLOCK_CONTROL['triggered']:
        return None
    slot = kwargs.get('_custody_slot')
    custody, io = OBSERVATION_BINDINGS['custody'], OBSERVATION_BINDINGS['io']
    if type(slot) is not custody._StoreNativeSlot:
        return None
    wanted = CLOCK_TARGETS[case]
    if slot._role != ('private_descriptor' if wanted[0] == 'descriptor' else 'relative'):
        return None
    candidates = []
    for key, original in io._RESOURCE_ORIGINALS.items():
        state = io._RESOURCE_STATES[key]
        cycle = io._CYCLE_ORIGINALS[id(original[3])]
        if (state[0] is True and state[1] is None and state[2] is None
                and (original[4], cycle[5][2], cycle[5][3]) == wanted):
            candidates.append((original, cycle))
    if not candidates:
        return None
    original, cycle = candidates[0]
    assert len(candidates) == 1
    graph_original = io._GRAPH_ORIGINALS[id(original[2])]
    if wanted[0] != 'descriptor':
        opened_context = UNCERTAINTY_OPEN_CONTEXT[0]
        if opened_context is None:
            return None
        opened, open_keywords = opened_context
        declared = dict(open_keywords)
        expected_name = cycle[5][5]
        if wanted[0] == 'creator':
            operation = graph_original[8]._operation
            assert type(operation) is tuple and len(operation) == 3
            assert type(operation[2]) is str and operation[2]
            expected_name = expected_name + '.' + operation[2] + '.stage'
        if (declared.get('directory') is not False or opened[2] is not args[2]
                or opened[1] != expected_name
                or declared.get('create', False) is not (wanted[0] == 'creator')
                or declared.get('reader_share', 1) != (7 if wanted[0] == 'live_readback' else 1)):
            return None
    assert args[2] is graph_original[7] is slot._journal._budget
    assert slot._journal is graph_original[5]
    assert slot._known is slot._return_seen is slot._representation_confirmed is True
    assert slot._owner is None and slot._close_attempted is False
    return args, tuple(kwargs.items()), original, cycle, slot


def _clock_mark(args, phase):
    context = CLOCK_REGISTER_CONTEXT[0]
    assert type(context) is tuple and len(context) == 5
    registered_args, declared, resource_original, cycle_original, slot = context
    assert args == (registered_args[2],)
    native = OBSERVATION_BINDINGS['io'].native
    owner, pins = slot._owner, slot._owner_pins
    assert type(owner) is native._Owner and pins[0] is owner
    assert pins[1] == registered_args[0] and pins[2] is registered_args[1]
    assert slot._known is slot._return_seen is slot._representation_confirmed is True
    assert slot._close_attempted is False and slot._receipt is slot._owned_fact
    CLOCK_CONTROL['triggered'] = True
    CLOCK_EVENTS.append((CLOCK_CONTROL['case'], phase, context, owner, pins,
                         (slot._known, slot._return_seen, slot._close_attempted, slot._receipt)))


def _clock_before(identifier, args, kwargs):
    if identifier == 'native.clock' and CLOCK_CONTROL['case'] != 'T008':
        _clock_mark(args, 'before')
        raise CLOCK_CONTROL['first']
    return args, kwargs


def _clock_after(identifier, args, kwargs, result):
    if identifier == 'native.clock' and CLOCK_CONTROL['case'] == 'T008':
        assert result is None
        _clock_mark(args, 'after')
        raise CLOCK_CONTROL['first']
    return result


def _assert_clock_source():
    target, size, digest = CLOCK_SOURCE_PIN
    module = sys.modules['reno.personal_authority_namespace3_deadline']
    actual = Path(module.__file__).resolve(strict=True)
    expected = (Path(__file__).resolve().parents[1] / target).resolve(strict=True)
    raw = actual.read_bytes()
    assert actual == expected and len(raw) == size and sha(raw) == digest


@pytest.mark.parametrize('identifier', FIXED_CLOCK_IDS, ids=FIXED_CLOCK_IDS)
def test_registered_owner_post_acquisition_clock_fault(r915_prefix_environment, identifier):
    env = r915_prefix_environment
    assert CLOCK_CONTROL['case'] is None and CLOCK_REGISTER_CONTEXT[0] is None
    assert CASE_CONTROL['mode'] is BOUNDARY_CONTROL['case'] is UNCERTAINTY_CONTROL['case'] is QUERY_CONTROL['case'] is COMPLETION_CONTROL['case'] is OBSERVER_CONTROL['case'] is None
    kinds = {'T001': ValueError, 'T002': SystemExit, 'T003': RuntimeError,
             'T004': KeyboardInterrupt, 'T005': ValueError, 'T006': SystemExit,
             'T007': RuntimeError, 'T008': SystemExit}
    first = kinds[identifier]('r934 controlled actual clock consumer ' + identifier)
    start, marker_start = len(CONSUMER_EVENTS), len(CLOCK_EVENTS)
    CLOCK_CONTROL.update(case=identifier, triggered=False, first=first)
    try:
        with pytest.raises(BaseException) as error:
            invoke_private_initial(env)
        assert error.value is first and CLOCK_CONTROL['triggered'] is True
        marked, = CLOCK_EVENTS[marker_start:]
        assert marked[0] == identifier and marked[1] == ('after' if identifier == 'T008' else 'before')
        context, owner, pins, at_clock = marked[2:]
        resource_original, cycle_original, slot = context[2:]
        assert (resource_original[4], cycle_original[5][2], cycle_original[5][3]) == CLOCK_TARGETS[identifier]
        assert at_clock == (True, True, False, slot._owned_fact)
        assert slot._owner is owner and slot._owner_pins is pins
        snapshot = _graph_snapshot(start)
        graph, original = snapshot[:2]
        value, journal = original[2], original[5]
        assert resource_original[2] is graph and slot._journal is journal
        assert value._first is first and value._phase == 'retired_uncompleted'
        io, runtime, custody = (OBSERVATION_BINDINGS[name] for name in ('io', 'runtime', 'custody'))
        assert id(graph) not in io._GRAPH_ORIGINALS and id(graph) not in io._GRAPH_STATES
        assert id(value) not in runtime._RETAINED_RETIREMENTS
        assert slot._known is slot._return_seen is slot._close_attempted is slot._representation_confirmed is True
        assert slot._receipt is slot._closed_fact and slot._failure is first
        fields = io.native._OWNER_DICT.__get__(owner, io.native._Owner)
        assert fields['retired'] is True and fields['failure'] is None
        native_kind = 'native.free' if identifier == 'T002' else 'native.close'
        target_closes = tuple(event for event in _events_since(start, native_kind, 'return')
                              if event[4][0] is slot and event[4][1] is False)
        assert len(target_closes) == 1 and target_closes[0][4][4] is slot._closed_fact
        assert len(_events_since(start, 'native.register', 'call')) == 1
        assert len(_events_since(start, 'native.register', 'raise')) == 1
        assert not _events_since(start, 'native.register', 'return')
        assert len(_events_since(start, 'native.clock', 'call')) == 1
        assert len(_events_since(start, 'native.clock', 'raise')) == 1
        assert len(_events_since(start, 'native.clock', 'return')) == (1 if identifier == 'T008' else 0)
        receipt = custody._JOURNAL_RECEIPT.__get__(journal, custody._StorePrepareJournal)
        assert receipt.unknown_count == 0 and receipt.all_registered_native_closed is False
        assert receipt.first_present is True and receipt.reset_state == 'reset'
        assert receipt.source_closed is receipt.write_authority is False
        _assert_real_store_close_calls(start, journal, receipt)
        _assert_no_business_after_first(start, first)
        _assert_base_data(env)
        assert CLOCK_REGISTER_CONTEXT[0] is None and UNCERTAINTY_OPEN_CONTEXT[0] is None
    finally:
        CLOCK_CONTROL.update(case=None, triggered=False, first=None)
        assert CLOCK_REGISTER_CONTEXT[0] is None


"""Fixed Store0 publication consumer actions; dormant source candidate only."""

PUBLICATION_IDS = tuple('P%03d' % index for index in range(1, 9))
PUBLICATION_SEAMS = ('write.chunk', 'creator.flush', 'native.publish', 'creator.info')
PUBLICATION_CONTROL = {'case': None, 'triggered': False, 'first': None, 'renamed': False}
PUBLICATION_CONTEXT = [None]
PUBLICATION_EVENTS = []
PUBLICATION_ACTIONS = []
BODY_OPERATION_PLAN.update({identifier: ('_owned_store_initial_with_actual_ex', 1)
                            for identifier in PUBLICATION_IDS})


def _publication_selected_resource(identifier, args, kwargs):
    """Use only already recorded original rows, never a product/native getter."""
    if PUBLICATION_CONTROL['case'] is None:
        return None
    io = OBSERVATION_BINDINGS['io']
    selected = []
    for original in io._RESOURCE_ORIGINALS.values():
        if original[4] != 'creator':
            continue
        resource, graph, cycle = original[0], original[2], original[3]
        cycle_original = io._CYCLE_ORIGINALS[id(cycle)]
        pins = cycle_original[5]
        if pins[2:4] != ('store', 0):
            continue
        state = io._RESOURCE_STATES[id(resource)]
        graph_original = io._GRAPH_ORIGINALS[id(graph)]
        budget = graph_original[7]
        if identifier == 'write.chunk':
            matches = len(args) == 3 and args[0] is state[3][1] and args[2] is budget
        elif identifier == 'creator.flush':
            matches = len(args) == 1 and args[0] is resource
        elif identifier == 'native.publish':
            matches = (len(args) == 4 and args[0] is pins[4] and args[1] is state[1]
                       and args[2] == pins[5] and args[3] is budget)
        elif identifier == 'creator.info':
            matches = len(args) == 2 and args[0] is cycle and args[1] is resource
        else:
            matches = False
        if not matches:
            continue
        assert not kwargs and original[2] is cycle_original[2]
        owner, slot, owner_pins = state[1:4]
        assert type(owner) is io.native._Owner and type(slot) is io.custody._StoreNativeSlot
        assert slot._owner is owner and slot._owner_pins is owner_pins and owner_pins[0] is owner
        assert slot._journal is graph_original[5] and slot._known is slot._return_seen is True
        assert slot._representation_confirmed is True and slot._close_attempted is False
        assert state[6] is False and state[7] is False
        lineage_operation = graph_original[8]._operation
        assert type(lineage_operation) is tuple and len(lineage_operation) == 3
        operation = lineage_operation[2]
        assert type(operation) is str and operation
        stage_name = pins[5] + '.' + operation + '.stage'
        selected.append((resource, original, state, cycle_original, graph_original, stage_name))
    assert len(selected) <= 1
    return selected[0] if selected else None


def _publication_mark(identifier, phase, detail):
    selected = PUBLICATION_CONTEXT[0]
    assert selected is not None and PUBLICATION_CONTROL['triggered'] is False
    PUBLICATION_CONTROL['triggered'] = True
    PUBLICATION_ACTIONS.append((PUBLICATION_CONTROL['case'], identifier, phase, selected, detail))


def _publication_before(identifier, args, kwargs):
    selected = PUBLICATION_CONTEXT[0]
    if identifier not in PUBLICATION_SEAMS or selected is None:
        return args, kwargs
    case = PUBLICATION_CONTROL['case']
    budget = selected[4][7]
    forwarded = args
    if not PUBLICATION_CONTROL['triggered']:
        if case == 'P001' and identifier == 'write.chunk':
            assert type(args[1]) is bytes and len(args[1]) > 1
            forwarded = (args[0], args[1][:1], args[2])
            _publication_mark(identifier, 'smaller-request', (args[1], forwarded[1]))
        elif (case, identifier) in (('P003', 'creator.flush'), ('P005', 'native.publish')):
            _publication_mark(identifier, 'before', PUBLICATION_CONTROL['first'])
            raise PUBLICATION_CONTROL['first']
    PUBLICATION_EVENTS.append((case, identifier, 'call', selected, forwarded,
                               tuple(kwargs.items()), budget.control_bytes))
    return forwarded, kwargs


def _publication_after(identifier, args, kwargs, result):
    selected = PUBLICATION_CONTEXT[0]
    if identifier not in PUBLICATION_SEAMS or selected is None:
        return result
    case, budget = PUBLICATION_CONTROL['case'], selected[4][7]
    PUBLICATION_EVENTS.append((case, identifier, 'return', selected, result, budget.control_bytes))
    if identifier == 'native.publish':
        assert result is None
        # Set only after the saved original actually returned, before a test
        # after-action can fail. This is test context, never a product marker.
        PUBLICATION_CONTROL['renamed'] = True
    if PUBLICATION_CONTROL['triggered']:
        return result
    if (case, identifier) in (('P002', 'write.chunk'), ('P004', 'creator.flush'),
                              ('P006', 'native.publish')):
        _publication_mark(identifier, 'after', (result, budget.control_bytes, PUBLICATION_CONTROL['first']))
        raise PUBLICATION_CONTROL['first']
    if case in ('P007', 'P008') and identifier == 'creator.info' and PUBLICATION_CONTROL['renamed']:
        assert type(result) is tuple and len(result) == 3
        native_identity, size, stamp = result
        assert type(native_identity) is tuple and type(size) is int
        if case == 'P007':
            changed = (native_identity, size + 1, stamp)
        else:
            assert tuple(key for key, value in native_identity) == ('file_index', 'volume_serial')
            changed_native = tuple((key, str(int(value) + 1) if key == 'file_index' else value)
                                   for key, value in native_identity)
            changed = (changed_native, size, stamp)
        _publication_mark(identifier, 'changed-representation', (result, changed))
        return changed
    return result


def _assert_publication_write_returns(events, expected_budget):
    calls = tuple(event for event in events if event[1:3] == ('write.chunk', 'call'))
    returns = tuple(event for event in events if event[1:3] == ('write.chunk', 'return'))
    assert calls and len(calls) == len(returns)
    forwarded_raws, actual_counts = [], []
    for call, returned in zip(calls, returns):
        selected, args, keywords, before = call[3:]
        actual, after = returned[4:]
        assert returned[3][0] is selected[0] and selected[4][7] is expected_budget
        assert keywords == () and args[2] is expected_budget
        assert type(args[1]) is bytes and type(actual) is int and 0 < actual <= len(args[1])
        assert after - before == actual
        forwarded_raws.append(args[1])
        actual_counts.append(actual)
    return tuple(forwarded_raws), tuple(actual_counts)


def _assert_publication_failed_cleanup(env, start, snapshot, first, selected):
    graph, original = snapshot[:2]
    value, journal = original[2], original[5]
    io, runtime, custody = (OBSERVATION_BINDINGS[name] for name in ('io', 'runtime', 'custody'))
    resource, resource_original, state, cycle_original, graph_original, stage_name = selected
    assert resource_original[2] is graph and graph_original is original
    assert cycle_original[5][2:4] == ('store', 0)
    owner, slot, owner_pins = state[1:4]
    assert slot._journal is journal and slot._owner is owner and slot._owner_pins is owner_pins
    assert value._first is first and value._phase == 'retired_uncompleted'
    assert value._pins == () and value._journal is value._stage is value._receipt is None
    assert id(value) not in runtime._RETAINED_RETIREMENTS
    assert id(graph) not in io._GRAPH_ORIGINALS and id(graph) not in io._GRAPH_STATES
    assert id(resource) not in io._RESOURCE_ORIGINALS and id(resource) not in io._RESOURCE_STATES
    assert graph._phase == 'retired' and graph._pins == ()
    assert slot._known is slot._close_attempted is slot._representation_confirmed is True
    assert slot._receipt is slot._closed_fact and slot._failure is first
    receipt = custody._JOURNAL_RECEIPT.__get__(journal, custody._StorePrepareJournal)
    assert receipt.unknown_count == 0 and receipt.first_present is True
    assert receipt.all_registered_native_closed is False and receipt.reset_state == 'reset'
    assert receipt.source_closed is receipt.write_authority is False
    _assert_real_store_close_calls(start, journal, receipt)
    _assert_no_business_after_first(start, first)
    # The additional publication consumer calls are also forbidden after the
    # original primary escaped. Real cleanup closers remain observable.
    escaped = next(index for index, event in enumerate(CONSUMER_EVENTS[start:]) if event[1] == 'raise')
    assert not any(event[0] in PUBLICATION_SEAMS and event[1] == 'call'
                   for event in CONSUMER_EVENTS[start + escaped + 1:])
    _assert_base_data(env)


@pytest.mark.parametrize('identifier', PUBLICATION_IDS, ids=PUBLICATION_IDS)
def test_fixed_full_publication_return_effects(r915_prefix_environment, identifier):
    env = r915_prefix_environment
    assert PUBLICATION_CONTROL['case'] is None and PUBLICATION_CONTEXT[0] is None
    assert CASE_CONTROL['mode'] is BOUNDARY_CONTROL['case'] is UNCERTAINTY_CONTROL['case'] is QUERY_CONTROL['case'] is COMPLETION_CONTROL['case'] is OBSERVER_CONTROL['case'] is CLOCK_CONTROL['case'] is None
    kinds = {'P002': SystemExit, 'P003': RuntimeError, 'P004': SystemExit,
             'P005': ValueError, 'P006': KeyboardInterrupt}
    first = kinds[identifier]('r937 controlled publication consumer ' + identifier) if identifier in kinds else None
    start, event_start, action_start = len(CONSUMER_EVENTS), len(PUBLICATION_EVENTS), len(PUBLICATION_ACTIONS)
    PUBLICATION_CONTROL.update(case=identifier, triggered=False, first=first, renamed=False)
    try:
        if identifier == 'P001':
            result = invoke_private_initial(env)
            _assert_source_prefix_result(result, env)
            snapshot = _graph_snapshot(start)
            _assert_initial_terminal_state(env)
            _assert_real_store_files(env, snapshot)
            _assert_read_and_owner_facts(start, snapshot)
            _assert_two_actual_generations(start, snapshot)
            _assert_actual_budget(start, snapshot)
            _assert_base_data(env)
        else:
            with pytest.raises(BaseException) as error:
                invoke_private_initial(env)
            if first is not None:
                assert error.value is first
            else:
                assert type(error.value) is OBSERVATION_BINDINGS['io'].StoreFileIOError
                assert error.value.code == 'ENROLLMENT_STORE_FILE_IO_READBACK_CHANGED'
                first = error.value
            snapshot = _graph_snapshot(start)
        assert PUBLICATION_CONTROL['triggered'] is True and PUBLICATION_CONTEXT[0] is None
        action, = PUBLICATION_ACTIONS[action_start:]
        case, seam, phase, selected, detail = action
        assert case == identifier and selected[1][4] == 'creator'
        expected = {'P001': ('write.chunk', 'smaller-request'), 'P002': ('write.chunk', 'after'),
                    'P003': ('creator.flush', 'before'), 'P004': ('creator.flush', 'after'),
                    'P005': ('native.publish', 'before'), 'P006': ('native.publish', 'after'),
                    'P007': ('creator.info', 'changed-representation'), 'P008': ('creator.info', 'changed-representation')}
        assert (seam, phase) == expected[identifier]
        observed = tuple(PUBLICATION_EVENTS[event_start:])
        forwarded, counts = _assert_publication_write_returns(observed, snapshot[5])
        raw = snapshot[4][0]
        if identifier == 'P001':
            assert len(forwarded) >= 2 and forwarded[0] == detail[1] == raw[:1]
            assert len(detail[0]) > len(detail[1]) == counts[0] == 1
            assert sum(counts) == len(raw) and PUBLICATION_CONTROL['renamed'] is True
        else:
            _assert_publication_failed_cleanup(env, start, snapshot, first, selected)
            if identifier == 'P002':
                assert len(counts) == 1 and detail[0] == counts[0]
            else:
                assert sum(counts) == len(raw)
            expected_counts = {
                'P002': ((0, 0), (0, 0), 1), 'P003': ((1, 0), (0, 0), 1),
                'P004': ((1, 1), (0, 0), 1), 'P005': ((1, 1), (1, 0), 2),
                'P006': ((1, 1), (1, 1), 2), 'P007': ((1, 1), (1, 1), 3),
                'P008': ((1, 1), (1, 1), 3),
            }
            flush_counts, rename_counts, creator_info_returns = expected_counts[identifier]
            for identifier_, expected_ in (('creator.flush', flush_counts), ('native.publish', rename_counts)):
                assert tuple(len(_events_since(start, identifier_, phase_)) for phase_ in ('call', 'return')) == expected_
            assert len(_events_since(start, 'creator.info', 'return')) == creator_info_returns
            renamed = identifier in ('P006', 'P007', 'P008')
            assert PUBLICATION_CONTROL['renamed'] is renamed
            if identifier in ('P007', 'P008'):
                before, changed = detail
                assert before[2] is changed[2]
                if identifier == 'P007':
                    assert before[0] is changed[0] and changed[1] == before[1] + 1
                else:
                    assert changed[1] == before[1] and changed[0] != before[0]
                live, = (event for event in _events_since(start, 'io.read', 'return')
                         if event[4][1] == 'live_readback')
                assert live[4][3:5] == ('store', 0) and live[3] == raw
                assert not any(event[4][1] == 'post_readback' for event in _events_since(start, 'io.read', 'return'))
            # Snapshot only this test-owned path after the body ended. A
            # failure may leave .stage or the renamed file; never delete,
            # roll back, adopt, or claim successful authority from residuals.
            directory, name = env['data'] / ROOT_NAME, snapshot[3][0]
            stage_name = selected[5]
            residual = directory / (name if renamed else stage_name)
            assert residual.read_bytes() == (raw[:counts[0]] if identifier == 'P002' else raw)
            assert not (directory / (stage_name if renamed else name)).exists()
            assert not any((directory / later).exists() for later in snapshot[3][1:])
    finally:
        PUBLICATION_CONTROL.update(case=None, triggered=False, first=None, renamed=False)
        assert PUBLICATION_CONTEXT[0] is None


# Only designated modules are checked here; this is not full source closure.
SEALED_SUBJECT_SOURCE_PINS = (('reno/personal_authority_enrollment_store_io.py', 57439, '675529a9762d14936d559af57e76df849ff65baf1f3ef8d7dd959e929fedf92c'), ('reno/personal_authority_enrollment_store_directories.py', 50526, 'cd8249727bd85742dec3938739e169c108c480a398a5567271fe0a5fb149178d'), ('reno/personal_authority_enrollment_store_census_native.py', 30354, 'fbb52dbea40d5c05a809370ffbf227bdfa8a666b0965dea89270eaa13e007ec9'), ('reno/personal_authority_enrollment_store_runtime.py', 64060, '6fc17787edb4a8aeac16c633897038ea2fbf57d07742ad5181c5579e3efbf324'), ('reno/personal_authority_owned_ex_group.py', 79040, '30231d790074d91a1d4d34ed1bbaecb7d0edaf92318164046443016721230b05'), ('reno/sync_media.py', 51232, '6142b73a0ecfe6c0b01bc8aee13f623b51a1d618c80c69e5ca4f57e0bbde8724'), ('reno/personal_authority_replica_operation.py', 164318, '74b55bbac5193659b3f25b72f1247cd15c23af67251887550040649654d8def5'), ('reno/personal_authority_enrollment.py', 31900, 'a8a7bbc9b1f196ecffe747f75d8f16c0d7889c6b5fa1aaccf92422e6b13990b3'), ('reno/personal_authority_enrollment_store_custody.py', 40979, 'e6f0996ae5b15e9a8ff27363ae3e66e3536aa49822d2dd3263b7918ca62a958f'), ('reno/personal_authority_enrollment_store_selections.py', 14354, '69d9ee344e33785dde251106e752b880d409b2c99bbdc3b66349463412e24e74'), ('reno/personal_authority_namespace3_io.py', 37295, '58cf03f088b27d3add1d432ea750067cf0a72c944b3ccaae223faced3fcca01a'), ('reno/personal_authority_enrollment_io.py', 17393, '0e46d3afbaf065446b621e944e75226f0074204a217e23604204579b5c69d042'), ('reno/personal_authority_origin_reservation_io_deadline.py', 41975, '7bf57b6dfd58ba6fec8a319bf2c87577bffe7e80d1c3deed97d70fe72f68d873'))


def _assert_sealed_subject_sources(sources):
    for target, size, digest in sources:
        module_name = target[:-3].replace('/', '.')
        module = sys.modules[module_name]
        actual_path = Path(module.__file__).resolve(strict=True)
        expected_path = Path(__file__).resolve().parents[1] / target
        assert actual_path == expected_path.resolve(strict=True)
        raw = actual_path.read_bytes()
        assert len(raw) == size and sha(raw) == digest


# Exactly one bootstrap; all helper definitions precede active capture.
FINAL_BINDINGS = bootstrap_fresh_methods()
_assert_sealed_subject_sources(SEALED_SUBJECT_SOURCE_PINS[:12])
_assert_clock_source()
