"""H01 prefix delivery from a new, actual Windows namespace3 origin.

Only the integration fixture creates native facts. The remaining tests use
consumer-local lifecycle/context/clock seams and synthetic rejection inputs;
they do not prove OS fault behavior, source closure, or native-close receipts.
"""
from contextlib import closing, contextmanager
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import uuid

import pytest

from reno import config, db, personal
from reno import personal_authority_enrollment as enrollment
from reno import personal_authority_enrollment_contract as contract
from reno import personal_authority_enrollment_io as publication
from reno import personal_authority_namespace3 as namespace
from reno import personal_authority_namespace3_contract as ns_contract
from reno import personal_authority_namespace3_deadline as deadline
from reno import personal_authority_namespace3_effect_owner_deadline as namespace_owner
from reno import personal_authority_origin_reservation_contract as origin_contract
from reno import personal_authority_origin_reservation_owner_deadline as origin_owner
from reno import personal_mirror_contract as mirror
from reno import storage_lifecycle as lifecycle


EXPECTED_FIELDS = (
    "expected_origin_reservation_sha256", "expected_origin_observation_sha256",
    "expected_namespace_reservation_sha256", "expected_root_seal_sha256",
    "expected_header_sha256",
)
KINDS = ("enrollment_binding", "origin_init_intent", "local_contract",
         "init_seal", "genesis_grant", "authority_prefix")
RESULT_FIELDS = {
    "scope", "init_operation_id", "domain_id", "facility_id",
    "origin_reservation_sha256", "origin_observation_sha256",
    "namespace_reservation_sha256", "root_seal_sha256", "header_sha256",
    "binding_sha256", "prefix_sha256",
}


def uid():
    return str(uuid.uuid4())


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def token():
    # Each borrowed entry gets its own original 30s; no caller expands it.
    return deadline.Namespace3Deadline(deadline.time.monotonic() + 30.0)


def assert_error(call, error_type, code):
    with pytest.raises(error_type) as raised:
        call()
    assert raised.value.code == code
    return raised.value


def tree(root):
    return {str(path.relative_to(root)): "directory" if path.is_dir() else path.read_bytes()
            for path in root.rglob("*")}


def raw_database_node(env):
    paths = {suffix: Path(str(env["database"]) + suffix) for suffix in ("", "-wal", "-journal")}
    paths["node"] = env["data"] / "node-identity.json"
    return {key: path.read_bytes() if path.exists() else None for key, path in paths.items()}


def original_bytes(env):
    data = env["data"]
    return tuple(path.read_bytes() for path in (
        data / origin_contract.RESERVATION_NAME, data / origin_contract.OBSERVATION_NAME,
        data / ns_contract.RESERVATION_NAME,
        data / ns_contract.ROOT_NAME / ns_contract.ROOT_SEAL_NAME,
        data / ns_contract.HEADER_NAME,
    ))


@pytest.fixture
def actual_prefix_env(tmp_path, monkeypatch):
    """New physical profile, actual personal receipt, and actual five originals."""
    root = tmp_path / "p"
    data = root / "data"
    data.mkdir(parents=True)
    local = root / "config.synthetic.json"
    local.write_bytes(encoded({"node_role": "home", "personal_features_enabled": True,
                              "field_checks_enabled": True}))
    monkeypatch.setenv("RENO_DATA_DIR", str(data))
    monkeypatch.setenv("RENO_CONFIG_FILE", str(local))
    for name in ("RENO_NODE_ROLE", "RENO_PERSONAL_FEATURES_ENABLED", "RENO_FIELD_CHECKS_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    for name, path in {"DATA": data, "MEDIA": root / "media", "ORIG": root / "media/originals",
                       "FRAMES": root / "media/frames", "AUDIO": root / "media/audio",
                       "FRAME_CACHE": root / "frames_cache"}.items():
        monkeypatch.setattr(config, name, path)
    monkeypatch.setattr(config, "_cache", None)
    database = data / "reno.db"
    db.initialize(database)
    profile = lifecycle.StorageProfile(database, root / "media", data / "generated", data / "sync-checkpoints")
    lifecycle.initialize(profile)
    with closing(sqlite3.connect(database)) as con:
        meta = dict(con.execute("SELECT key,value FROM meta WHERE key IN (?,?,?,?)", mirror.META_KEYS))
        assert con.execute("SELECT value FROM meta WHERE key='migration_version'").fetchone() == ("17",)
    mutation = uid()
    created, status = personal.create_card(meta["default_project_id"], {
        "operation_id": mutation, "primary_epoch": int(meta["primary_epoch"]),
        "changes": {"title": "H01 独立装修记录", "question": "材料与施工次序待核对",
                    "content_snapshot": {"kind": "note", "text": "新 profile 的真实 mutation receipt"}},
    })
    assert status == 201 and created["card"]["card_id"]
    with closing(sqlite3.connect(database)) as con:
        assert con.execute("SELECT COUNT(*) FROM mutation_receipt WHERE operation_id=?", (mutation,)).fetchone() == (1,)
    arguments = {"domain_id": uid(), "facility_id": uid(), "init_operation_id": uid()}
    pair = origin_owner.reserve_origin(profile, **arguments)
    first, second = ((data / name).read_bytes() for name in
                     (origin_contract.RESERVATION_NAME, origin_contract.OBSERVATION_NAME))
    assert (pair.reservation_sha256, pair.observation_sha256) == (sha(first), sha(second))
    origin_contract.cross_validate(first, second)
    namespace_facts = namespace_owner.initialize_namespace3(profile, **arguments,
        expected_origin_reservation_sha256=sha(first), expected_origin_observation_sha256=sha(second))
    env = {"root": root, "data": data, "database": database, "profile": profile, "args": arguments}
    originals = original_bytes(env)
    ns_contract.cross_validate_complete(*originals)
    assert (namespace_facts.namespace_reservation_sha256, namespace_facts.root_seal_sha256,
            namespace_facts.header_sha256) == tuple(sha(raw) for raw in originals[2:])
    env["originals"] = originals
    env["expected"] = dict(zip(EXPECTED_FIELDS, (sha(raw) for raw in originals)))
    assert lifecycle.current_guard() is None
    return env


@pytest.mark.skipif(os.name != "nt", reason="actual Windows original profile and creator HANDLE required")
def test_actual_prefix_publish_borrowed_verify_cold_verify_and_retained_stage(actual_prefix_env):
    env = actual_prefix_env
    profile, options = env["profile"], {**env["args"], **env["expected"]}
    root = env["data"] / ns_contract.ROOT_NAME
    before_sources, before_tree = raw_database_node(env), tree(env["root"])
    binding = contract.build_binding(*env["originals"])
    content = contract.prepare_authority_prefix(binding, *env["originals"])
    hashes = {"expected_binding_sha256": sha(binding), "expected_prefix_sha256": sha(content.prefix_bytes)}
    with lifecycle.exclusive(profile) as guard:
        wrong = {**options, "expected_origin_reservation_sha256": "0" * 64}
        assert_error(lambda: enrollment.publish_prefix_locked(profile, **wrong, deadline_token=token()),
                     enrollment.EnrollmentError, "ENROLLMENT_ORIGINAL_MISMATCH")
        # EX lockfiles are never opened by the test while this guard is live.
        assert set(path.name for path in root.iterdir()) == {ns_contract.ROOT_SEAL_NAME}
        assert original_bytes(env) == env["originals"]
        # An original seal alone has no completed prefix and no resume route.
        assert_error(lambda: enrollment.verify_prefix_locked(profile, **options, **hashes, deadline_token=token()),
                     namespace.Namespace3Error, "NAMESPACE3_UNKNOWN_NAMES")
        result = enrollment.publish_prefix_locked(profile, **options, deadline_token=token())
        assert type(result) is enrollment.AuthorityPrefixFiles
        assert result.scope == "durable_authority_prefix_files_only"
        assert set(result.__slots__) == RESULT_FIELDS
        assert (result.origin_reservation_sha256, result.origin_observation_sha256,
                result.namespace_reservation_sha256, result.root_seal_sha256,
                result.header_sha256) == tuple(sha(raw) for raw in env["originals"])
        assert (result.domain_id, result.facility_id, result.init_operation_id) == tuple(env["args"].values())
        assert lifecycle.current_guard() is guard
        assert lifecycle.require_guard(profile, exclusive=True) is guard
        assert enrollment.verify_prefix_locked(profile, **options, **hashes, deadline_token=token()) == result
        assert lifecycle.require_guard(profile, exclusive=True) is guard
        # A completed prefix cannot be adopted by either publisher or old layer.
        completed_controls = tuple((root / publication.NAMES[kind]).read_bytes() for kind in KINDS)
        completed_names = {ns_contract.ROOT_SEAL_NAME} | set(publication.NAMES.values())
        assert set(path.name for path in root.iterdir()) == completed_names
        assert_error(lambda: enrollment.publish_prefix_locked(profile, **options, deadline_token=token()),
                     namespace.Namespace3Error, "NAMESPACE3_UNKNOWN_NAMES")
        assert_error(lambda: deadline.verify_namespace3_locked(profile, **options, deadline_token=token()),
                     namespace.Namespace3Error, "NAMESPACE3_UNKNOWN_NAMES")
        assert tuple((root / publication.NAMES[kind]).read_bytes() for kind in KINDS) == completed_controls
        assert set(path.name for path in root.iterdir()) == completed_names
        assert original_bytes(env) == env["originals"]
    assert lifecycle.current_guard() is None
    records = tuple((root / publication.NAMES[kind]).read_bytes() for kind in KINDS)
    assert records == completed_controls
    expected_tree = {**before_tree, **{
        str((root / publication.NAMES[kind]).relative_to(env["root"])): raw
        for kind, raw in zip(KINDS, records)
    }}
    completed = tree(env["root"])
    assert completed == expected_tree
    assert set(path.name for path in root.iterdir()) == {ns_contract.ROOT_SEAL_NAME} | set(publication.NAMES.values())
    assert records == (binding, content.origin_intent_bytes, content.local_contract_bytes,
                       content.init_seal_bytes, content.genesis_grant_bytes, content.prefix_bytes)
    original_intent = origin_contract._blob(origin_contract.decode(env["originals"][1])["origin_intent_base64"],
                                            origin_contract.MAX_INTENT_BYTES)
    assert records[1] == original_intent
    contract.cross_validate_authority_prefix(contract.AuthorityPrefixContent(records[5], *records[1:5]),
                                             records[0], *env["originals"])
    seal = ns_contract.decode(env["originals"][3])["creator"]
    bound_root = contract.decode(records[0])["namespace"]
    assert bound_root["root_native_identity"] == seal["root_native_identity"]
    assert bound_root["root_path_identity"] == seal["root_path_identity"]
    assert (result.binding_sha256, result.prefix_sha256) == (sha(records[0]), sha(records[5]))
    assert enrollment.verify_prefix(profile, **options, **hashes) == result
    assert lifecycle.current_guard() is None and tree(env["root"]) == completed
    assert original_bytes(env) == env["originals"] and raw_database_node(env) == before_sources
    # Exercise the actual publisher's no-replace collision, preserving its stage.
    stage = root / publication.stage_name(publication.NAMES["enrollment_binding"], env["args"]["init_operation_id"])
    with lifecycle.exclusive(profile) as guard:
        budget = enrollment._Budget(token())
        with enrollment.native.open_parent_locked(profile, budget, write=True) as parent:
            with publication.open_root_for_publication_locked(profile, parent, budget) as opened:
                assert_error(lambda: publication.publish_new_locked(profile, opened,
                    publication.NAMES["enrollment_binding"], binding, budget),
                    enrollment.native.Namespace3IOError, "NAMESPACE3_IO_PUBLISH_UNAVAILABLE")
                assert lifecycle.require_guard(profile, exclusive=True) is guard
                assert tuple((root / publication.NAMES[kind]).read_bytes() for kind in KINDS) == records
                assert stage.read_bytes() == binding and original_bytes(env) == env["originals"]
        assert lifecycle.require_guard(profile, exclusive=True) is guard
    assert lifecycle.current_guard() is None
    staged = tree(env["root"])
    assert staged == {**completed, str(stage.relative_to(env["root"])): binding}
    assert_error(lambda: enrollment.verify_prefix(profile, **options, **hashes),
                 namespace.Namespace3Error, "NAMESPACE3_UNKNOWN_NAMES")
    assert_error(lambda: enrollment.publish_prefix(profile, **options),
                 namespace.Namespace3Error, "NAMESPACE3_UNKNOWN_NAMES")
    assert stage.read_bytes() == binding and tree(env["root"]) == staged
    assert original_bytes(env) == env["originals"] and raw_database_node(env) == before_sources
    assert lifecycle.current_guard() is None


@pytest.fixture
def controlled_owner(tmp_path, monkeypatch):
    """Exact-type seam objects, without native locks, source files, or native facts."""
    profile = lifecycle.StorageProfile(tmp_path / "data/reno.db", tmp_path / "media",
                                       tmp_path / "reports", tmp_path / "checkpoints")
    arguments = {"domain_id": uid(), "facility_id": uid(), "init_operation_id": uid()}
    expected = dict(zip(EXPECTED_FIELDS, (character * 64 for character in "abcde")))
    facts = enrollment.AuthorityPrefixFiles(enrollment.SCOPE, arguments["init_operation_id"],
        arguments["domain_id"], arguments["facility_id"], *expected.values(), "f" * 64, "1" * 64)
    state = SimpleNamespace(current=None, entered=0, exits=[], performed=[], clock=0.0,
        exit_return=False, exit_failure=None, exit_clock=None, result=facts)
    guard = lifecycle.StorageGuard([profile], True, [])
    state.guard = guard
    actual_perform = enrollment._perform
    monkeypatch.setattr(enrollment, "os", SimpleNamespace(name="nt"))
    clock = SimpleNamespace(monotonic=lambda: state.clock)
    monkeypatch.setattr(enrollment, "time", clock)
    monkeypatch.setattr(deadline, "time", clock)
    monkeypatch.setattr(lifecycle, "current_guard", lambda: state.current)
    def require(selected, exclusive=False):
        assert selected is profile and exclusive is True
        if state.current is None:
            raise lifecycle.StorageLifecycleError("STORAGE_GUARD_REQUIRED")
        return state.current
    monkeypatch.setattr(lifecycle, "require_guard", require)
    class Exclusive:
        def __enter__(self):
            assert state.current is None
            state.current = guard
            state.entered += 1
            return guard
        def __exit__(self, kind, primary, traceback):
            state.exits.append((kind, primary, traceback))
            state.current = None
            if state.exit_clock is not None:
                state.clock = state.exit_clock
            if state.exit_failure is not None:
                raise state.exit_failure
            return state.exit_return
    def exclusive(selected, *, timeout, check_cancelled):
        assert selected is profile and timeout == 0
        check_cancelled()
        return Exclusive()
    monkeypatch.setattr(lifecycle, "exclusive", exclusive)
    def perform(selected, operation, pins, mode, budget, binding, prefix):
        assert selected is profile and state.current is guard
        assert budget.effective_deadline is budget.requested_deadline
        assert budget.deadline == 30.0
        state.performed.append((mode, budget))
        budget.clock_only()
        return state.result
    monkeypatch.setattr(enrollment, "_perform", perform)
    return SimpleNamespace(profile=profile, options={**arguments, **expected}, state=state,
        facts=facts, hashes={"expected_binding_sha256": facts.binding_sha256,
                            "expected_prefix_sha256": facts.prefix_sha256}, actual_perform=actual_perform)


def owned(env, mode="publish", **changes):
    options = {**env.options, **(env.hashes if mode == "verify" else {}), **changes}
    return getattr(enrollment, mode + "_prefix")(env.profile, **options)


def enrollment_error(call, suffix):
    return assert_error(call, enrollment.EnrollmentError, "ENROLLMENT_" + suffix)


@pytest.mark.parametrize("mode", ["publish", "verify"])
def test_controlled_falsey_callback_once_and_delivery_after_single_normal_exit(controlled_owner, mode):
    env = controlled_owner
    class Callback:
        calls = bool_calls = 0
        def __bool__(self):
            self.bool_calls += 1
            return False
        def __call__(self):
            self.calls += 1
            assert env.state.current is None and env.state.entered == 0
    callback = Callback()
    result = owned(env, mode, check_cancelled=callback)
    assert result is env.facts and callback.calls == 1 and callback.bool_calls == 0
    assert env.state.entered == len(env.state.exits) == len(env.state.performed) == 1
    assert env.state.exits[0] == (None, None, None) and env.state.current is None


@pytest.mark.parametrize("mode,exclusive", [("publish", False), ("publish", True), ("verify", False), ("verify", True)],
                         ids=["publish-sh", "publish-ex", "verify-sh", "verify-ex"])
def test_controlled_owned_entry_retains_and_rejects_ambient_guard(controlled_owner, mode, exclusive):
    env = controlled_owner
    ambient = lifecycle.StorageGuard([env.profile], exclusive, [])
    env.state.current = ambient
    try:
        enrollment_error(lambda: owned(env, mode), "GUARD_ALREADY_HELD")
        assert env.state.current is ambient
        assert not env.state.entered and not env.state.performed and not env.state.exits
    finally:
        env.state.current = None  # Synthetic seam state only; no native owner exists.


@pytest.mark.parametrize("mode,field,value", [
    ("publish", "domain_id", "00000000-0000-0000-0000-000000000000"),
    ("publish", "facility_id", 1), ("publish", "init_operation_id", "invalid"),
    ("publish", "expected_origin_reservation_sha256", True),
    ("publish", "expected_origin_observation_sha256", None),
    ("publish", "expected_namespace_reservation_sha256", "BAD"),
    ("publish", "expected_root_seal_sha256", "A" * 64),
    ("publish", "expected_header_sha256", b"a" * 64),
    ("verify", "expected_binding_sha256", None), ("verify", "expected_prefix_sha256", "BAD"),
    ("publish", "check_cancelled", 0),
], ids=["nil-domain", "facility-type", "operation", "first-type", "second-type", "namespace-sha",
        "seal-case", "header-type", "binding-type", "prefix-sha", "callback-type"])
def test_controlled_invalid_arguments_never_reach_perform(controlled_owner, mode, field, value):
    env = controlled_owner
    enrollment_error(lambda: owned(env, mode, **{field: value}), "INVALID_ARGUMENT")
    assert not env.state.performed and env.state.current is None


@pytest.mark.parametrize("failure", [RuntimeError("original callback"), SystemExit(850)], ids=["ordinary", "control"])
def test_controlled_callback_failure_is_original_before_ex(controlled_owner, failure):
    env = controlled_owner
    def callback():
        raise failure
    with pytest.raises(type(failure)) as raised:
        owned(env, check_cancelled=callback)
    assert raised.value is failure and not env.state.entered and not env.state.performed
    assert env.state.current is None


def test_controlled_normal_ex_exit_failure_prevents_facts_delivery(controlled_owner):
    env = controlled_owner
    failure = RuntimeError("synthetic normal EX exit failure")
    env.state.exit_failure = failure
    with pytest.raises(RuntimeError) as raised:
        owned(env)
    assert raised.value is failure and len(env.state.performed) == len(env.state.exits) == 1
    assert env.state.exits[0] == (None, None, None) and env.state.current is None


@pytest.mark.parametrize("exit_return", [None, True], ids=["none", "suppression"])
def test_controlled_illegal_normal_ex_exit_return_is_rejected(controlled_owner, exit_return):
    env = controlled_owner
    env.state.exit_return = exit_return
    enrollment_error(lambda: owned(env), "RESULT_INVALID")
    assert len(env.state.performed) == len(env.state.exits) == 1 and env.state.current is None


@pytest.mark.parametrize("failure", [RuntimeError("original body"), SystemExit(850), MemoryError("synthetic memory failure")],
                         ids=["ordinary", "control", "synthetic-memory"])
def test_controlled_body_exception_wins_cleanup_exception_and_exit_attempts_once(controlled_owner, monkeypatch, failure):
    env = controlled_owner
    env.state.exit_failure = RuntimeError("synthetic EX cleanup failure")
    def refused(*args, **kwargs):
        raise failure
    monkeypatch.setattr(enrollment, "_perform", refused)
    with pytest.raises(type(failure)) as raised:
        owned(env)
    assert raised.value is failure and len(env.state.exits) == 1
    assert env.state.exits[0][0] is type(failure) and env.state.exits[0][1] is failure
    assert env.state.current is None


@pytest.mark.parametrize("bad_result", ["duck", "scope", "original-pin", "verify-pin"])
def test_controlled_perform_result_cannot_invent_authority_or_drop_expected_pins(controlled_owner, bad_result):
    env = controlled_owner
    if bad_result == "duck":
        env.state.result = SimpleNamespace()
    elif bad_result == "scope":
        env.state.result = replace(env.facts, scope="writer_grant")
    elif bad_result == "original-pin":
        env.state.result = replace(env.facts, header_sha256="0" * 64)
    else:
        env.state.result = replace(env.facts, prefix_sha256="0" * 64)
    enrollment_error(lambda: owned(env, "verify" if bad_result == "verify-pin" else "publish"), "RESULT_INVALID")
    assert len(env.state.exits) == 1 and env.state.current is None


def test_controlled_same_deadline_exactly_thirty_after_ex_exit_rejects_delivery(controlled_owner):
    env = controlled_owner
    env.state.exit_clock = 30.0
    assert_error(lambda: owned(env), namespace.Namespace3Error, "NAMESPACE3_DEADLINE")
    assert len(env.state.performed) == len(env.state.exits) == 1
    budget = env.state.performed[0][1]
    assert budget.requested_deadline.absolute_monotonic == budget.effective_deadline.absolute_monotonic == 30.0
    assert env.state.current is None


def test_controlled_initial_owned_deadline_expiry_has_no_ex_or_publish(controlled_owner, monkeypatch):
    env = controlled_owner
    monkeypatch.setattr(deadline, "time", SimpleNamespace(monotonic=lambda: 30.0))
    assert_error(lambda: owned(env), namespace.Namespace3Error, "NAMESPACE3_DEADLINE")
    assert not env.state.entered and not env.state.exits and not env.state.performed
    assert env.state.current is None


@pytest.mark.parametrize("mode", ["publish", "verify"])
def test_controlled_binding_only_partial_is_rejected_before_any_read_or_publication(controlled_owner, tmp_path, monkeypatch, mode):
    """Ordinary tmp names are rejection inputs, never accepted native root facts."""
    env = controlled_owner
    parent = tmp_path / "partial-rejection-input"
    root = parent / ns_contract.ROOT_NAME
    root.mkdir(parents=True)
    for name in (origin_contract.RESERVATION_NAME, origin_contract.OBSERVATION_NAME,
                 ns_contract.RESERVATION_NAME, ns_contract.HEADER_NAME):
        (parent / name).write_bytes(b"not an accepted original")
    (root / ns_contract.ROOT_SEAL_NAME).write_bytes(b"not an accepted seal")
    (root / publication.NAMES["enrollment_binding"]).write_bytes(b"test-only partial binding")
    before = tree(parent)
    @contextmanager
    def directory(path):
        yield SimpleNamespace(path=path, verify=lambda: None)
    def forbidden(*args, **kwargs):
        raise AssertionError("partial names must reject before reading or publishing bytes")
    monkeypatch.setattr(enrollment, "_perform", env.actual_perform)
    monkeypatch.setattr(namespace, "_physical", lambda *args: ((), None, None, None))
    monkeypatch.setattr(enrollment.native, "open_parent_locked", lambda *args, **kwargs: directory(parent))
    monkeypatch.setattr(enrollment.native, "open_root_locked", lambda *args, **kwargs: directory(root))
    monkeypatch.setattr(publication, "open_root_for_publication_locked", lambda *args, **kwargs: directory(root))
    monkeypatch.setattr(enrollment.native, "read_control_locked", forbidden)
    monkeypatch.setattr(publication, "publish_new_locked", forbidden)
    assert_error(lambda: owned(env, mode), namespace.Namespace3Error, "NAMESPACE3_UNKNOWN_NAMES")
    assert tree(parent) == before and len(env.state.exits) == 1 and env.state.current is None
