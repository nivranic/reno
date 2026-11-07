"""r915 common-fixture source draft; Root alone may assemble/admit/execute.

This file has no test cases and does not bootstrap product imports at module
scope. The final independent test module must call bootstrap_fresh_methods()
at its own module scope, before any original operation import or product API.
No existing tests module is imported. Product revision02 is deliberately not
pinned here; its eventual sealed owning topology belongs to final test review.

The four method wrappers only save their actual call arguments, actual return,
or ordinary raised object. They call each saved original exactly once and do
not add a getter, consumer, callback, route, deadline, retirement, or fault.
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
    _require_fresh_operation()
    operation = importlib.import_module(OPERATION_MODULE)
    _require_method_capture(operation, tuple(METHOD_PINS))
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


# BEGIN FINAL R915 SOURCE03 THREE-ID TESTS.
# The mechanically embedded common header describes its sealed draft component.
# This appended section assembles that component into a final candidate module.
import ctypes
from dataclasses import fields


FIXED_TEST_IDS = ("R001", "R002", "N001")
SEALED_SOURCE03_PINS = (
    ("personal_authority_enrollment_store_runtime.py", 59433, "a51a7062f7757d710c9c6d92a823876cd678ae3a70f3438c9ec7577c4c7429e8"),
    ("personal_authority_enrollment.py", 31900, "a8a7bbc9b1f196ecffe747f75d8f16c0d7889c6b5fa1aaccf92422e6b13990b3"),
    ("personal_authority_owned_ex_group.py", 76542, "01594eb3fe4a50c23896d77b98ec9b5642566aff99452bbadc384ee3baea2416"),
    ("personal_authority_replica_operation.py", 164318, "74b55bbac5193659b3f25b72f1247cd15c23af67251887550040649654d8def5"),
    ("personal_authority_enrollment_store_selections.py", 14354, "69d9ee344e33785dde251106e752b880d409b2c99bbdc3b66349463412e24e74"),
    ("personal_authority_enrollment_store_directories.py", 35543, "fe916f58119b2340235ac60e389427745bd1e8b6b4093c29e74248fcc91ba607"),
    ("personal_authority_enrollment_store_census_native.py", 30021, "f84f96a740c26cc0d57342eaec72cad4753613702cd333e6865ff34e1950c31f"),
)

# This is the only assembly bootstrap, before fixtures and before any product
# API. The common guard rejects an already imported operation; no reload/reset.
FINAL_BINDINGS = bootstrap_fresh_methods()


def _assert_source03_loaded(bindings):
    runtime, directories = bindings["runtime"], bindings["directories"]
    modules = (
        runtime, bindings["enrollment"], runtime.groups, bindings["operation"],
        runtime.selections, directories, directories.census_native,
    )
    assert len(modules) == len(SEALED_SOURCE03_PINS) == 7
    for module, (filename, size, digest) in zip(modules, SEALED_SOURCE03_PINS):
        source_path = Path(module.__file__)
        assert source_path.name == filename
        held_raw = source_path.read_bytes()
        assert len(held_raw) == size and sha(held_raw) == digest


_assert_source03_loaded(FINAL_BINDINGS)


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


def _assert_original_data_unchanged(env):
    assert _read_originals(env["data"], 11) == env["originals"]
    assert original_identity_oracle(env["originals"]) == env["original_identity_oracle"]
    assert database_node_bytes(env) == env["database_node_before"]
    assert_no_store_files(env)


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


def _function_abi_pin(function):
    arguments = function.argtypes
    assert type(arguments) is list
    return function, tuple(arguments), function.restype, getattr(function, "errcheck", None)


def _assert_same_function_abi(before, function):
    after = _function_abi_pin(function)
    assert after[0] is before[0] and after[2] is before[2] and after[3] is before[3]
    assert len(after[1]) == len(before[1])
    assert all(current is original for current, original in zip(after[1], before[1]))


def _assert_literal_census_abi(env):
    census = env["bindings"]["directories"].census_native
    namespace_native = env["bindings"]["enrollment"].native
    pointer_size = ctypes.sizeof(ctypes.c_void_p)
    assert pointer_size in (4, 8)
    assert ctypes.sizeof(ctypes.c_uint8) == 1
    assert ctypes.sizeof(ctypes.c_int32) == ctypes.sizeof(ctypes.c_uint32) == 4
    assert ctypes.sizeof(ctypes.c_uint16) == 2 and ctypes.sizeof(ctypes.c_int64) == 8
    assert census._BUFFER_BYTES == 65536 and census._INFORMATION_CLASS == 37
    assert census._NAME_OFFSET == 104 and census._NO_MORE_FILES == 0x80000006 and census._PENDING == 0x103
    expected_arguments = (
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.POINTER(census._IoStatus), ctypes.c_void_p, ctypes.c_uint32,
        ctypes.c_int32, ctypes.c_uint8, ctypes.POINTER(census._UnicodeString), ctypes.c_uint8,
    )
    function = census._query_directory
    assert function is census._ntdll.NtQueryDirectoryFile
    assert census._ntdll is not namespace_native._ntdll
    assert function is not namespace_native._ntdll.NtCreateFile
    assert type(function.argtypes) is list and len(function.argtypes) == 11
    assert all(actual is expected for actual, expected in zip(function.argtypes, expected_arguments))
    assert function.restype is ctypes.c_int32 and getattr(function, "errcheck", None) is None
    status_layout = (8, 4, 4) if pointer_size == 4 else (16, 8, 8)
    assert (ctypes.sizeof(census._IoStatus), census._IoStatus.information.offset,
            ctypes.alignment(census._IoStatus)) == status_layout
    assert ctypes.sizeof(census._StatusUnion) == pointer_size
    assert census._IoStatus.completion.offset == census._StatusUnion.status.offset == census._StatusUnion.pointer.offset == 0
    assert tuple(census._StatusUnion._fields_) == (("status", ctypes.c_int32), ("pointer", ctypes.c_void_p))
    assert tuple(census._IoStatus._fields_) == (("completion", census._StatusUnion), ("information", ctypes.c_size_t))
    assert ctypes.sizeof(census._UnicodeString) == (8 if pointer_size == 4 else 16)
    assert (census._UnicodeString.length.offset, census._UnicodeString.maximum_length.offset,
            census._UnicodeString.buffer.offset) == (0, 2, 4 if pointer_size == 4 else 8)
    assert tuple(census._UnicodeString._fields_) == (
        ("length", ctypes.c_uint16), ("maximum_length", ctypes.c_uint16), ("buffer", ctypes.c_void_p),
    )
    offsets = (
        ("next_entry_offset", 0), ("file_index", 4), ("creation_time", 8), ("last_access_time", 16),
        ("last_write_time", 24), ("change_time", 32), ("end_of_file", 40), ("allocation_size", 48),
        ("file_attributes", 56), ("file_name_length", 60), ("ea_size", 64), ("short_name_length", 68),
        ("short_name", 70), ("file_id", 96), ("file_name", 104),
    )
    record = census._FileIdBothDirectoryInformation
    assert ctypes.sizeof(record) == 112 and ctypes.alignment(record) == 8
    assert tuple(name for name, kind in record._fields_) == tuple(name for name, offset in offsets)
    assert tuple((name, getattr(record, name).offset) for name, offset in offsets) == offsets
    assert census._DIR_OFFSETS == offsets
    kinds = dict(record._fields_)
    assert kinds["next_entry_offset"] is kinds["file_index"] is ctypes.c_uint32
    assert all(kinds[name] is ctypes.c_int64 for name in (
        "creation_time", "last_access_time", "last_write_time", "change_time", "end_of_file", "allocation_size", "file_id",
    ))
    assert all(kinds[name] is ctypes.c_uint32 for name in ("file_attributes", "file_name_length", "ea_size"))
    assert kinds["short_name_length"] is ctypes.c_int8
    assert kinds["short_name"]._length_ == 12 and kinds["short_name"]._type_ is ctypes.c_uint16
    assert kinds["file_name"]._length_ == 1 and kinds["file_name"]._type_ is ctypes.c_uint16
    assert ctypes.sizeof(census._QueryBuffer) == 65536
    assert census._QueryBuffer._length_ == 65536 and census._QueryBuffer._type_ is ctypes.c_char


def test_R001_actual_private_initial_owned_route(r915_prefix_environment):
    env = r915_prefix_environment
    _assert_source03_loaded(env["bindings"])
    result = invoke_private_initial(env)
    _assert_source_prefix_result(result, env)
    _assert_initial_terminal_state(env)
    _assert_original_data_unchanged(env)


def test_R002_default_source_only_unchanged(r915_prefix_environment):
    env = r915_prefix_environment
    _assert_source03_loaded(env["bindings"])
    runtime = env["bindings"]["runtime"]
    directories = env["bindings"]["directories"]
    census = directories.census_native
    route_tables = (
        runtime._INITIAL_ROUTES, runtime._INITIAL_ROUTE_VALUES, runtime._INITIAL_HOOK_ATTEMPTS,
        runtime._INITIAL_HOOKS, runtime._INITIAL_HOOK_METADATA, runtime._INITIAL_ROUTE_OWNERS,
        runtime._INITIAL_FACTORY_ATTEMPTS, runtime._INITIAL_FACTORY_OWNERS, runtime._INITIAL_FACTORY_ROWS,
        directories._DIRECTORY_ATTEMPTS, directories._DIRECTORY_SCOPES, directories._FILE_SELECTIONS,
        directories._CENSUS_ROWS, census._SCOPE_CLEANUP, census._SCAN_ROWS, census._QUERY_ROWS, census._QUERY_DEBTS,
    )
    before = tuple(tuple(table.items()) for table in route_tables)
    # Keep already settled referents during the comparison; natural retirement
    # of an old weak index is not creation of a new Store route by this API.
    observed_referents = tuple(
        item() for table_rows in before for key, row in table_rows for item in row
        if type(item) is runtime.weakref.ReferenceType
    )
    result = invoke_default_source_verify(env)
    _assert_source_prefix_result(result, env)
    assert method_events_since(env["body_event_start"]) == ()
    after = tuple(tuple(table.items()) for table in route_tables)
    assert len(after) == len(before)
    for prior_rows, actual_rows in zip(before, after):
        assert len(prior_rows) == len(actual_rows)
        assert all(key == old_key and row is old_row for (key, row), (old_key, old_row) in zip(actual_rows, prior_rows))
    assert type(observed_referents) is tuple
    _assert_original_data_unchanged(env)


def test_N001_actual_private_directory_abi(r915_prefix_environment):
    env = r915_prefix_environment
    _assert_source03_loaded(env["bindings"])
    namespace_native = env["bindings"]["enrollment"].native
    original_create_file = _function_abi_pin(namespace_native._ntdll.NtCreateFile)
    _assert_literal_census_abi(env)
    result = invoke_private_initial(env)
    _assert_source_prefix_result(result, env)
    _assert_initial_terminal_state(env)
    _assert_literal_census_abi(env)
    _assert_same_function_abi(original_create_file, namespace_native._ntdll.NtCreateFile)
    _assert_original_data_unchanged(env)


# END FINAL R915 SOURCE03 THREE-ID TESTS.
# No pending ID has a skipped placeholder. No fault/status/buffer is injected.
# These cases do not claim actual kernel pending, Store persistence, SQL
# successor, active original graph closure, source_closed or write authority.
