"""Actual Windows namespace facts from actual origin pair; no seeded grant.

An independent current17 fixture obtains its original pair through r819. New
namespace success uses production namespace3 I/O and fresh r815 captures.
Explicit consumer-local fault seams are distinguished from actual OS failure.
"""
import base64
from contextlib import closing, contextmanager
from dataclasses import FrozenInstanceError
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import uuid

import pytest

from reno import config, db, personal
from reno import personal_authority_legacy_fence as fence
from reno import personal_authority_origin_reservation as origin_reservation
from reno import personal_authority_origin_reservation_contract as origin_contract
from reno import personal_authority_origin_reservation_owner as origin_owner
from reno import personal_authority_origin_source as origin_source
from reno import personal_authority_namespace3 as namespace
from reno import personal_authority_namespace3_contract as contract
from reno import personal_authority_namespace3_io as namespace_io
from reno import personal_mirror_contract as facts
from reno import personal_preservation_contract as preservation
from reno import storage_lifecycle as lifecycle


RESERVATION = ".reno-personal-authority-namespace-v3-reservation.json"
ROOT = ".reno-personal-authority-namespace-v3"
SEAL = ".reno-personal-authority-namespace-v3-root-seal.json"
HEADER = ".reno-personal-authority-namespace-v3-header.json"
ORIGIN_FIRST = ".reno-personal-authority-origin-v1-reservation.json"
ORIGIN_SECOND = ".reno-personal-authority-origin-v1-observation.json"
SCOPE = "filesystem_namespace3_facts_only"
CREATION = "windows_ntcreate_directory_handle_v1"
pytestmark = pytest.mark.skipif(os.name != "nt", reason="namespace3 requires original Windows creator HANDLE")


def uid():
    return str(uuid.uuid4())


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def namespace_env(tmp_path, monkeypatch):
    root = tmp_path / "actual-ns-home"
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
    with closing(sqlite3.connect(database)) as con, con:
        meta = dict(con.execute("SELECT key,value FROM meta WHERE key IN (?,?,?,?)", facts.META_KEYS))
        assert con.execute("SELECT value FROM meta WHERE key='migration_version'").fetchone() == ("17",)
    mutation_operation = uid()
    created, status = personal.create_card(meta["default_project_id"], {
        "operation_id": mutation_operation, "primary_epoch": int(meta["primary_epoch"]),
        "changes": {"title": "绍兴防水核对", "question": "工序与材料品牌型号未定",
                    "content_snapshot": {"kind": "note", "text": "独立 namespace3 临时真实记录"}}})
    assert status == 201 and created["card"]["card_id"]
    with closing(sqlite3.connect(database)) as con, con:
        assert con.execute("SELECT COUNT(*) FROM mutation_receipt WHERE operation_id=?", (mutation_operation,)).fetchone() == (1,)
    arguments = {"domain_id": uid(), "facility_id": uid(), "init_operation_id": uid()}
    pair_result = origin_owner.reserve_origin(profile, **arguments)
    first, second = tuple((data / name).read_bytes() for name in (ORIGIN_FIRST, ORIGIN_SECOND))
    assert sha(first) == pair_result.reservation_sha256 and sha(second) == pair_result.observation_sha256
    origin_contract.cross_validate(first, second)
    assert lifecycle.current_guard() is None
    return {"root": root, "data": data, "database": database, "profile": profile,
            "node": data / "node-identity.json", "meta": meta, "args": arguments,
            "first": first, "second": second,
            "expected": {"expected_origin_reservation_sha256": sha(first),
                         "expected_origin_observation_sha256": sha(second)}}


def tree(env):
    return {str(path.relative_to(env["root"])): "directory" if path.is_dir() else path.read_bytes()
            for path in env["root"].rglob("*")}


def raw_sources(env):
    paths = {suffix: Path(str(env["database"]) + suffix) for suffix in ("", "-wal", "-journal")}
    paths["node"] = env["node"]
    return {key: path.read_bytes() if path.exists() else None for key, path in paths.items()}


def sql_snapshot(env):
    with closing(sqlite3.connect(env["database"])) as con, con:
        result = {"meta": list(con.execute("SELECT key,value FROM meta ORDER BY key"))}
        for name, columns in preservation.PERSONAL_COLUMNS.items():
            query = 'SELECT ' + ','.join('"' + column + '"' for column in columns) + ' FROM "' + name + '"'
            result[name] = list(con.execute(query))
    return result


def originals(env):
    return tuple((env["data"] / name).read_bytes() for name in (ORIGIN_FIRST, ORIGIN_SECOND))


def records(env):
    return ((env["data"] / RESERVATION).read_bytes(),
            (env["data"] / ROOT / SEAL).read_bytes(),
            (env["data"] / HEADER).read_bytes())


def initialize(env, **options):
    with lifecycle.exclusive(env["profile"]):
        return namespace.initialize_namespace3_locked(env["profile"], **env["args"], **env["expected"], **options)


def resume(env, reservation_sha, **options):
    with lifecycle.exclusive(env["profile"]):
        return namespace.resume_unstarted_namespace3_locked(env["profile"], **env["args"], **env["expected"],
            expected_namespace_reservation_sha256=reservation_sha, **options)


def verify(env, expected, **options):
    with lifecycle.exclusive(env["profile"]):
        return namespace.verify_namespace3_locked(env["profile"], **env["args"], **env["expected"],
            expected_namespace_reservation_sha256=expected.namespace_reservation_sha256,
            expected_root_seal_sha256=expected.root_seal_sha256, expected_header_sha256=expected.header_sha256,
            **options)


def assert_error(call, suffix):
    with pytest.raises(namespace.Namespace3Error) as raised:
        call()
    assert raised.value.code == "NAMESPACE3_" + suffix


def assert_pair_preserved(env):
    assert originals(env) == (env["first"], env["second"])


def test_real_creation_binds_actual_pair_physical_roots_and_root_seal_without_writer_grant(namespace_env):
    env = namespace_env
    before_sources, before_sql, before_tree = raw_sources(env), sql_snapshot(env), tree(env)
    with lifecycle.exclusive(env["profile"]) as guard:
        result = namespace.initialize_namespace3_locked(env["profile"], **env["args"], **env["expected"])
        assert lifecycle.current_guard() is guard and lifecycle.require_guard(env["profile"], exclusive=True) is guard
    reservation_raw, seal_raw, header_raw = records(env)
    reservation, seal, header = [contract.decode(raw) for raw in (reservation_raw, seal_raw, header_raw)]
    for raw in (reservation_raw, seal_raw, header_raw):
        assert contract.encode(contract.decode(raw)) == raw
        assert contract.record_sha256(raw) == sha(raw)
    contract.cross_validate_complete(env["first"], env["second"], reservation_raw, seal_raw, header_raw)
    assert type(result) is namespace.Namespace3FilesystemFacts
    assert result.scope == SCOPE and result.creation_kind == CREATION
    assert set(result.__slots__) == {"scope", "init_operation_id", "domain_id", "facility_id",
        "origin_reservation_sha256", "origin_observation_sha256", "namespace_reservation_sha256",
        "root_seal_sha256", "header_sha256", "creation_kind"}
    assert all(type(getattr(result, field)) is str for field in result.__slots__)
    for field, value in env["args"].items():
        assert getattr(result, field) == value
    assert result.origin_reservation_sha256 == sha(env["first"])
    assert result.origin_observation_sha256 == sha(env["second"])
    assert result.namespace_reservation_sha256 == sha(reservation_raw)
    assert result.root_seal_sha256 == sha(seal_raw) and result.header_sha256 == sha(header_raw)
    with pytest.raises(FrozenInstanceError):
        result.header_sha256 = "0" * 64
    first, second = origin_contract.decode(env["first"]), origin_contract.decode(env["second"])
    assert reservation["profile"] == first["profile"] and reservation["node"] == first["node"]
    assert reservation["origin"] == {
        "reservation_sha256": sha(env["first"]), "observation_sha256": sha(env["second"]),
        "intent_sha256": second["origin_intent_sha256"], "evidence_sha256": second["origin_evidence_sha256"],
        "authority_profile_paths_sha256": sha(encoded(first["profile"]["profile_paths"])),
        "physical_profile_sha256": sha(encoded(first["profile"])),
        "authority_profile_paths_role": "canonical_four_paths_array_v1",
        "physical_profile_role": "canonical_physical_profile_dict_v1"}
    assert reservation["layout"] == {"reservation_name": RESERVATION, "root_name": ROOT,
                                    "root_seal_name": SEAL, "header_name": HEADER}
    assert "root_native_identity" not in reservation and "root_path_identity" not in reservation
    assert seal["namespace_reservation_sha256"] == sha(reservation_raw)
    assert header["namespace_reservation_sha256"] == sha(reservation_raw)
    assert header["root_seal_sha256"] == sha(seal_raw)
    info = (env["data"] / ROOT).stat(follow_symlinks=False)
    identity = {"device": str(info.st_dev), "inode": str(info.st_ino)}
    assert header["root_path_identity"] == seal["creator"]["root_path_identity"] == identity
    assert header["root_native_identity"] == seal["creator"]["root_native_identity"]
    assert seal["creator"]["parent_path_identity"] == first["profile"]["roots"]["data"]["identity"]
    assert seal["creator"]["private_policy"] == "current_user_and_system_full_control_protected_v1"
    assert seal["creator"]["creation_kind"] == CREATION
    assert "owner_sid" not in seal["creator"]
    assert {path.name for path in (env["data"] / ROOT).iterdir()} == {SEAL}
    assert_pair_preserved(env)
    assert raw_sources(env) == before_sources and sql_snapshot(env) == before_sql
    after_tree = tree(env)
    for path in (env["data"] / RESERVATION, env["data"] / HEADER,
                 env["data"] / ROOT / SEAL, env["data"] / ROOT):
        after_tree.pop(str(path.relative_to(env["root"])))
    assert after_tree == before_tree and lifecycle.current_guard() is None


def test_completed_verify_is_readonly_and_r818_closed_name_set_remains_strict(namespace_env):
    env = namespace_env
    result = initialize(env)
    before = tree(env), raw_sources(env), sql_snapshot(env)
    verified = verify(env, result)
    assert verified == result and (tree(env), raw_sources(env), sql_snapshot(env)) == before
    with lifecycle.exclusive(env["profile"]):
        with pytest.raises(origin_reservation.OriginReservationError) as raised:
            origin_reservation.resume_origin_locked(env["profile"], **env["args"],
                expected_reservation_sha256=sha(env["first"]), expected_observation_sha256=sha(env["second"]))
        assert raised.value.code == "ORIGIN_RESERVATION_UNKNOWN_NAMES"
    assert (tree(env), raw_sources(env), sql_snapshot(env)) == before


@pytest.mark.parametrize("guard", ["none", "shared", "wrong-profile"], ids=["none", "sh", "wrong-profile"])
def test_namespace_requires_actual_exact_profile_ex_before_new_effects(namespace_env, guard):
    env = namespace_env
    before = tree(env)
    if guard == "none":
        assert_error(lambda: namespace.initialize_namespace3_locked(env["profile"], **env["args"], **env["expected"]), "GUARD_REQUIRED")
    elif guard == "shared":
        with lifecycle.shared(env["profile"]):
            assert_error(lambda: namespace.initialize_namespace3_locked(env["profile"], **env["args"], **env["expected"]), "GUARD_REQUIRED")
    else:
        other = lifecycle.StorageProfile(env["database"], env["profile"].media,
                    env["data"] / "other-reports", env["profile"].checkpoints)
        with lifecycle.exclusive(env["profile"]):
            assert_error(lambda: namespace.initialize_namespace3_locked(other, **env["args"], **env["expected"]), "GUARD_REQUIRED")
    assert tree(env) == before and originals(env) == (env["first"], env["second"])


@pytest.mark.parametrize("field", ["domain_id", "facility_id", "init_operation_id"], ids=["domain", "facility", "init"])
def test_original_pair_context_cannot_be_rebound_to_new_uuid(namespace_env, field):
    env = namespace_env
    changed = {**env, "args": {**env["args"], field: uid()}}
    before = tree(env)
    assert_error(lambda: initialize(changed), "ORIGIN_MISMATCH")
    assert tree(env) == before and originals(env) == (env["first"], env["second"])


@pytest.mark.parametrize("field", ["expected_origin_reservation_sha256", "expected_origin_observation_sha256"],
                         ids=["first-sha", "second-sha"])
def test_required_origin_expected_hash_is_strict_not_adopted_from_disk(namespace_env, field):
    env = namespace_env
    changed = {**env, "expected": {**env["expected"], field: "0" * 64}}
    before = tree(env)
    assert_error(lambda: initialize(changed), "ORIGIN_MISMATCH")
    assert tree(env) == before


def test_actual_node_replacement_is_refused_without_namespace_effects(namespace_env):
    env = namespace_env
    env["node"].write_bytes(encoded({"instance_id": uid()}))
    before = tree(env)
    assert_error(lambda: initialize(env), "ORIGIN_MISMATCH")
    assert tree(env) == before
    assert_pair_preserved(env)


@pytest.mark.parametrize("name", [ORIGIN_FIRST, ORIGIN_SECOND], ids=["first-raw", "second-raw"])
def test_modified_original_pair_bytes_are_refused_without_repair_or_new_namespace(namespace_env, name):
    env = namespace_env
    path = env["data"] / name
    altered = json.loads(path.read_bytes())
    altered["init_operation_id"] = uid()
    changed_raw = encoded(altered)
    path.write_bytes(changed_raw)
    before = tree(env)
    with pytest.raises((namespace.Namespace3Error, origin_contract.OriginReservationContractError,
                        contract.Namespace3ContractError)):
        initialize(env)
    assert tree(env) == before and path.read_bytes() == changed_raw


def test_raw_sql_change_with_unchanged_personal_rows_is_not_refreshed_into_pair(namespace_env):
    env = namespace_env
    before_personal = sql_snapshot(env)
    with closing(sqlite3.connect(env["database"])) as con, con:
        con.execute("PRAGMA user_version=820")
    assert sql_snapshot(env) == before_personal
    before = tree(env)
    assert_error(lambda: initialize(env), "ORIGIN_MISMATCH")
    assert tree(env) == before
    assert_pair_preserved(env)


@pytest.mark.parametrize("name,kind", [(".RENO-PERSONAL-AUTHORITY-unknown", "file"),
                                       (RESERVATION + ".unrecognized.stage", "file"),
                                       (ROOT.upper(), "directory")],
                         ids=["unknown-prefix", "unknown-stage", "root-case-alias"])
def test_unknown_stage_or_alias_is_not_adopted_repaired_or_deleted(namespace_env, name, kind):
    env = namespace_env
    path = env["data"] / name
    if kind == "directory":
        path.mkdir()
    else:
        path.write_bytes(b"unknown prior artifact")
    before = tree(env)
    assert_error(lambda: initialize(env), "UNKNOWN_NAMES")
    assert tree(env) == before


def test_complete_namespace_must_verify_instead_of_init_or_unstarted_resume(namespace_env):
    env = namespace_env
    result = initialize(env)
    before = tree(env)
    assert_error(lambda: initialize(env), "STATE_INVALID")
    assert_error(lambda: resume(env, result.namespace_reservation_sha256), "STATE_INVALID")
    assert tree(env) == before
    assert verify(env, result) == result


@pytest.mark.parametrize("state", ["only-origin", "foreign-root", "header-only"],
                         ids=["only-origin", "foreign-root", "header-only"])
def test_mixed_or_not_reserved_states_cannot_be_resumed(namespace_env, state):
    env = namespace_env
    if state == "foreign-root":
        (env["data"] / ROOT).mkdir()
    elif state == "header-only":
        (env["data"] / HEADER).write_bytes(b"not a completed header")
    before = tree(env)
    assert_error(lambda: resume(env, "a" * 64), "STATE_INVALID")
    assert tree(env) == before


def test_false_valued_callback_dispatches_once_before_clock_only_phases(namespace_env):
    env = namespace_env
    class Callback:
        calls = bool_calls = 0
        def __bool__(self):
            self.bool_calls += 1
            return False
        def __call__(self):
            self.calls += 1
            assert lifecycle.require_guard(env["profile"], exclusive=True)
    callback = Callback()
    initialize(env, check_cancelled=callback)
    assert callback.calls == 1 and callback.bool_calls == 0


@pytest.mark.parametrize("failure", [RuntimeError("original callback"), SystemExit(820)], ids=["ordinary", "control"])
def test_original_callback_failure_leaves_only_original_pair(namespace_env, failure):
    env = namespace_env
    before = tree(env)
    def refused():
        raise failure
    with pytest.raises(type(failure)) as raised:
        initialize(env, check_cancelled=refused)
    assert raised.value is failure and tree(env) == before


def test_actual_completed_contract_rejects_role_swap_and_disconnected_hash_chains(namespace_env):
    env = namespace_env
    initialize(env)
    reservation_raw, seal_raw, header_raw = records(env)
    reserved = contract.decode(reservation_raw)
    altered = json.loads(reservation_raw)
    altered["origin"]["authority_profile_paths_role"], altered["origin"]["physical_profile_role"] = (
        altered["origin"]["physical_profile_role"], altered["origin"]["authority_profile_paths_role"])
    with pytest.raises(contract.Namespace3ContractError):
        contract.decode(encoded(altered))
    for key in ("authority_profile_paths_sha256", "physical_profile_sha256"):
        altered = json.loads(reservation_raw)
        other = "physical_profile_sha256" if key == "authority_profile_paths_sha256" else "authority_profile_paths_sha256"
        altered["origin"][key] = reserved["origin"][other]
        with pytest.raises(contract.Namespace3ContractError):
            contract.cross_validate_reservation(env["first"], env["second"], encoded(altered))
    altered = json.loads(header_raw)
    altered["root_seal_sha256"] = "0" * 64
    with pytest.raises(contract.Namespace3ContractError):
        contract.cross_validate_complete(env["first"], env["second"], reservation_raw, seal_raw, encoded(altered))
    altered = json.loads(header_raw)
    altered["root_native_identity"]["file_index"] = str((int(altered["root_native_identity"]["file_index"]) + 1) % (2**64))
    with pytest.raises(contract.Namespace3ContractError):
        contract.cross_validate_complete(env["first"], env["second"], reservation_raw, seal_raw, encoded(altered))
    assert records(env) == (reservation_raw, seal_raw, header_raw)

    for raw in (reservation_raw, seal_raw, header_raw):
        altered = json.loads(raw)
        altered["format_version"] = True
        with pytest.raises(contract.Namespace3ContractError):
            contract.decode(encoded(altered))
        altered = json.loads(raw)
        altered["source_closed"] = True
        with pytest.raises(contract.Namespace3ContractError):
            contract.decode(encoded(altered))


def test_verify_expected_metadata_hashes_are_strict_and_never_repaired(namespace_env):
    env = namespace_env
    result = initialize(env)
    before = tree(env)
    for field in ("namespace_reservation_sha256", "root_seal_sha256", "header_sha256"):
        altered = SimpleNamespace(**{name: getattr(result, name) for name in (
            "namespace_reservation_sha256", "root_seal_sha256", "header_sha256")})
        setattr(altered, field, "0" * 64)
        with pytest.raises(namespace.Namespace3Error):
            verify(env, altered)
    assert tree(env) == before


class _Overlay:
    """A consumer-local alias, leaving the real module/ABI untouched."""
    def __init__(self, original, **overrides):
        self.original, self.overrides = original, overrides

    def __getattr__(self, name):
        return self.overrides.get(name, getattr(self.original, name))


def assert_reserved_only(env):
    raw = (env["data"] / RESERVATION).read_bytes()
    contract.cross_validate_reservation(env["first"], env["second"], raw)
    assert not (env["data"] / ROOT).exists() and not (env["data"] / HEADER).exists()
    assert not any(path.name.endswith(".stage") for path in env["data"].iterdir())
    assert_pair_preserved(env)
    return sha(raw)


def assert_partial_is_preserved_and_cannot_resume(env):
    before = tree(env)
    reservation_sha = sha((env["data"] / RESERVATION).read_bytes())
    assert_error(lambda: resume(env, reservation_sha), "STATE_INVALID")
    assert tree(env) == before
    with lifecycle.exclusive(env["profile"]):
        assert_error(lambda: namespace.verify_namespace3_locked(env["profile"], **env["args"],
            **env["expected"], expected_namespace_reservation_sha256=reservation_sha,
            expected_root_seal_sha256="a" * 64, expected_header_sha256="b" * 64), "STATE_INVALID")
    assert tree(env) == before
    assert_pair_preserved(env)


def test_real_normal_source_exit_publisher_return_and_independent_read_precede_root_creation(namespace_env, monkeypatch):
    env, events = namespace_env, []
    real_capture = origin_source.capture_locked
    real_publish = namespace_io.publish_new_locked
    real_read = namespace_io.read_control_locked
    real_create = namespace_io.create_root_locked
    capture_count = 0

    @contextmanager
    def capture(*args, **kwargs):
        nonlocal capture_count
        capture_count += 1
        ordinal = capture_count
        with real_capture(*args, **kwargs) as observed:
            yield observed
        events.append("source-" + str(ordinal) + "-normal-exit")

    def publish(*args, **kwargs):
        result = real_publish(*args, **kwargs)
        events.append(args[2] + ":publisher-return")
        return result

    @contextmanager
    def read(*args, **kwargs):
        with real_read(*args, **kwargs) as held:
            events.append(args[2] + ":independent-ready")
            yield held

    @contextmanager
    def create(*args, **kwargs):
        with real_create(*args, **kwargs) as root:
            events.append("root-created-and-verified")
            yield root
        events.append("root-creator-normal-exit")

    monkeypatch.setattr(namespace, "origin", _Overlay(origin_source, capture_locked=capture))
    monkeypatch.setattr(namespace, "publication", _Overlay(namespace_io,
        publish_new_locked=publish, read_control_locked=read, create_root_locked=create))
    result = initialize(env)
    required = ["source-1-normal-exit", RESERVATION + ":publisher-return",
        RESERVATION + ":independent-ready", "root-created-and-verified",
        SEAL + ":publisher-return", HEADER + ":publisher-return",
        "root-creator-normal-exit", "source-2-normal-exit"]
    positions = [events.index(event) for event in required]
    assert positions == sorted(positions) and len(set(positions)) == len(positions)
    assert capture_count == 2 and result.namespace_reservation_sha256 == sha(records(env)[0])
    assert_pair_preserved(env)


def test_original_source_close_failure_blocks_all_new_namespace_publication(namespace_env, monkeypatch):
    env = namespace_env
    before, failure = tree(env), RuntimeError("source completed then explicit close seam")
    real_capture = origin_source.capture_locked
    completed = []

    @contextmanager
    def capture(*args, **kwargs):
        with real_capture(*args, **kwargs) as observed:
            yield observed
        completed.append(True)
        raise failure

    def forbidden(*args, **kwargs):
        pytest.fail("namespace publication must wait for source normal exit")

    monkeypatch.setattr(namespace, "origin", _Overlay(origin_source, capture_locked=capture))
    monkeypatch.setattr(namespace, "publication", _Overlay(namespace_io,
        publish_new_locked=forbidden, create_root_locked=forbidden))
    with pytest.raises(RuntimeError) as raised:
        initialize(env)
    assert raised.value is failure and completed == [True] and tree(env) == before


@pytest.mark.parametrize("gate", ["independent-read", "root-entry"], ids=["r-read", "root-entry"])
def test_real_r_only_survives_failed_gate_and_resumes_only_with_exact_expected_hash(namespace_env, monkeypatch, gate):
    env, failure, root_calls = namespace_env, RuntimeError("explicit R-only gate"), []
    real_read = namespace_io.read_control_locked

    @contextmanager
    def read(*args, **kwargs):
        with real_read(*args, **kwargs) as held:
            if args[2] == RESERVATION:
                raise failure
            yield held

    @contextmanager
    def create(*args, **kwargs):
        root_calls.append(True)
        raise failure
        yield  # contextmanager shape only; no synthetic directory

    overrides = {"create_root_locked": create}
    if gate == "independent-read":
        overrides["read_control_locked"] = read
    with monkeypatch.context() as patch:
        patch.setattr(namespace, "publication", _Overlay(namespace_io, **overrides))
        with pytest.raises(RuntimeError) as raised:
            initialize(env)
        assert raised.value is failure
    assert root_calls == ([] if gate == "independent-read" else [True])
    expected_sha = assert_reserved_only(env)
    before = tree(env)
    assert_error(lambda: resume(env, "0" * 64), "ORIGIN_MISMATCH")
    assert tree(env) == before
    result = resume(env, expected_sha)
    assert result.namespace_reservation_sha256 == expected_sha
    assert verify(env, result) == result
    assert_pair_preserved(env)


@pytest.mark.parametrize("gate", ["root-normal-exit", "seal-publisher-return"], ids=["root-only", "root-and-seal"])
def test_actual_started_root_and_seal_residuals_are_not_adopted_or_repaired(namespace_env, monkeypatch, gate):
    env, failure = namespace_env, RuntimeError("explicit partial namespace gate")
    real_create, real_publish = namespace_io.create_root_locked, namespace_io.publish_new_locked
    normal_closes = []

    @contextmanager
    def create(*args, **kwargs):
        with real_create(*args, **kwargs) as root:
            pass
        normal_closes.append(root.owner.retired)
        raise failure
        yield

    def publish(*args, **kwargs):
        result = real_publish(*args, **kwargs)
        if args[2] == SEAL:
            raise failure
        return result

    with monkeypatch.context() as patch:
        patch.setattr(namespace, "publication", _Overlay(namespace_io,
            **({"create_root_locked": create} if gate == "root-normal-exit" else {"publish_new_locked": publish})))
        with pytest.raises(RuntimeError) as raised:
            initialize(env)
        assert raised.value is failure
    assert (env["data"] / ROOT).is_dir() and not (env["data"] / HEADER).exists()
    assert (env["data"] / ROOT / SEAL).exists() == (gate == "seal-publisher-return")
    if gate == "root-normal-exit":
        assert normal_closes == [True]
    assert_partial_is_preserved_and_cannot_resume(env)


def test_completed_readonly_verify_never_calls_namespace_create_or_publish(namespace_env, monkeypatch):
    env = namespace_env
    result = initialize(env)
    before = tree(env), sql_snapshot(env), raw_sources(env)

    def forbidden(*args, **kwargs):
        pytest.fail("read-only namespace verification called an authority mutation primitive")

    monkeypatch.setattr(namespace, "publication", _Overlay(namespace_io,
        create_root_locked=forbidden, publish_new_locked=forbidden))
    assert verify(env, result) == result
    assert (tree(env), sql_snapshot(env), raw_sources(env)) == before


@pytest.mark.parametrize("scope", ["creator-root", "held-origin-reader"], ids=["root-close", "reader-close"])
def test_normal_real_scope_close_then_explicit_failure_prevents_dto(namespace_env, monkeypatch, scope):
    env, failure, closed = namespace_env, OSError("explicit post-close failure"), []
    real_create, real_read = namespace_io.create_root_locked, namespace_io.read_control_locked

    @contextmanager
    def create(*args, **kwargs):
        with real_create(*args, **kwargs) as root:
            yield root
        closed.append(root.owner.retired)
        raise failure

    @contextmanager
    def read(*args, **kwargs):
        with real_read(*args, **kwargs) as held:
            yield held
        if args[2] == ORIGIN_FIRST and not closed:
            closed.append(held.owner.retired)
            raise failure

    with monkeypatch.context() as patch:
        patch.setattr(namespace, "publication", _Overlay(namespace_io,
            **({"create_root_locked": create} if scope == "creator-root" else {"read_control_locked": read})))
        with lifecycle.exclusive(env["profile"]) as guard:
            with pytest.raises(OSError) as raised:
                namespace.initialize_namespace3_locked(env["profile"], **env["args"], **env["expected"])
            assert raised.value is failure and lifecycle.current_guard() is guard
    assert closed == [True]
    contract.cross_validate_complete(env["first"], env["second"], *records(env))
    assert_pair_preserved(env)


def test_original_control_exception_after_actual_header_publication_survives_real_cleanup_then_fault(namespace_env, monkeypatch):
    env, primary, cleanup = namespace_env, SystemExit(820), RuntimeError("explicit cleanup seam")
    real_publish, real_create = namespace_io.publish_new_locked, namespace_io.create_root_locked
    closed = []

    def publish(*args, **kwargs):
        result = real_publish(*args, **kwargs)
        if args[2] == HEADER:
            raise primary
        return result

    @contextmanager
    def create(*args, **kwargs):
        root = None
        try:
            with real_create(*args, **kwargs) as root:
                yield root
        finally:
            if root is not None:
                closed.append(root.owner.retired)
                raise cleanup

    monkeypatch.setattr(namespace, "publication", _Overlay(namespace_io,
        publish_new_locked=publish, create_root_locked=create))
    with pytest.raises(SystemExit) as raised:
        initialize(env)
    assert raised.value is primary and closed == [True]
    assert "NAMESPACE3_CLEANUP_FAILED" in getattr(primary, "__notes__", [])
    contract.cross_validate_complete(env["first"], env["second"], *records(env))
    assert_pair_preserved(env)


@pytest.mark.parametrize("operation", ["CloseHandle", "LocalFree", "FlushFileBuffers", "GetSecurityDescriptorControl"],
                         ids=["native-close", "native-free", "native-flush", "native-acl-control"])
def test_native_bool_or_free_reported_fault_follows_real_operation_and_prevents_acceptance(namespace_env, monkeypatch, operation):
    """Actual operations succeed first; only their reported result is synthetic."""
    env, armed, injected, target, attempts = namespace_env, [False], [], [None], []
    kernel, advapi = namespace_io._kernel, namespace_io._advapi
    real_create, real_publish = namespace_io.create_root_locked, namespace_io.publish_new_locked

    def close(handle):
        result = kernel.CloseHandle(handle)
        if target[0] is not None and handle == target[0]:
            attempts.append(True)
            assert result, "selected actual HANDLE close did not succeed before synthetic report"
            if not injected:
                injected.append(True)
                namespace_io.ctypes.set_last_error(5)
                return False
        return result

    def free(pointer):
        result = kernel.LocalFree(pointer)
        if armed[0] and not injected:
            assert not result, "actual LocalFree did not succeed before synthetic report"
            injected.append(True)
            namespace_io.ctypes.set_last_error(5)
            return pointer
        return result

    def flush(handle):
        result = kernel.FlushFileBuffers(handle)
        if armed[0] and not injected:
            assert result, "actual FlushFileBuffers did not succeed before synthetic report"
            injected.append(True)
            namespace_io.ctypes.set_last_error(5)
            return False
        return result

    def sdcontrol(*args):
        result = advapi.GetSecurityDescriptorControl(*args)
        if armed[0] and not injected:
            assert result, "actual descriptor query did not succeed before synthetic report"
            injected.append(True)
            namespace_io.ctypes.set_last_error(5)
            return False
        return result

    @contextmanager
    def create(*args, **kwargs):
        if operation in {"LocalFree", "GetSecurityDescriptorControl"}:
            armed[0] = True
        try:
            with real_create(*args, **kwargs) as root:
                if operation == "CloseHandle":
                    target[0] = root.owner.value
                yield root
        finally:
            armed[0] = False

    def publish(*args, **kwargs):
        if operation == "FlushFileBuffers" and args[2] == HEADER:
            armed[0] = True
        try:
            return real_publish(*args, **kwargs)
        finally:
            if operation == "FlushFileBuffers":
                armed[0] = False

    with monkeypatch.context() as patch:
        patch.setattr(namespace_io, "_kernel", _Overlay(kernel,
            **{operation: {"CloseHandle": close, "LocalFree": free, "FlushFileBuffers": flush}.get(operation)}
            if operation != "GetSecurityDescriptorControl" else {}))
        if operation == "GetSecurityDescriptorControl":
            patch.setattr(namespace_io, "_advapi", _Overlay(advapi, GetSecurityDescriptorControl=sdcontrol))
        patch.setattr(namespace, "publication", _Overlay(namespace_io,
            create_root_locked=create, publish_new_locked=publish))
        with pytest.raises(OSError) as raised:
            initialize(env)
        assert raised.value.winerror == 5
    assert injected == [True] and (env["data"] / ROOT).is_dir()
    if operation == "CloseHandle":
        assert attempts == [True]
        contract.cross_validate_complete(env["first"], env["second"], *records(env))
    elif operation == "FlushFileBuffers":
        assert (env["data"] / ROOT / SEAL).is_file() and not (env["data"] / HEADER).exists()
        stage = env["data"] / contract.stage_name(HEADER, env["args"]["init_operation_id"])
        assert stage.is_file()
        before = tree(env)
        assert_error(lambda: resume(env, sha((env["data"] / RESERVATION).read_bytes())), "UNKNOWN_NAMES")
        assert tree(env) == before
    else:
        assert not (env["data"] / ROOT / SEAL).exists()
        assert_partial_is_preserved_and_cannot_resume(env)
    assert_pair_preserved(env)


@pytest.mark.parametrize("completion", ["pending", "opened-instead-created"], ids=["nt-pending", "nt-information"])
def test_root_nt_completion_gate_does_not_adopt_unconfirmed_or_wrong_created_fact(namespace_env, monkeypatch, completion):
    env, armed, calls = namespace_env, [False], []
    ntdll, real_create = namespace_io._ntdll, namespace_io.create_root_locked

    def ntcreate(*args):
        if not armed[0]:
            return ntdll.NtCreateFile(*args)
        calls.append(True)
        status = namespace_io.ctypes.cast(args[3], namespace_io.ctypes.POINTER(namespace_io._IoStatus)).contents
        if completion == "pending":
            status.status = 0x103
            return 0x103  # explicit synthetic pending; no fabricated HANDLE or real creation
        result = ntdll.NtCreateFile(*args)
        assert result == 0 and status.status == 0 and status.information == 2
        status.information = 1  # real new directory/confirmed HANDLE, synthetic completion report
        return result

    @contextmanager
    def create(*args, **kwargs):
        armed[0] = True
        try:
            with real_create(*args, **kwargs) as root:
                yield root
        finally:
            armed[0] = False

    with monkeypatch.context() as patch:
        patch.setattr(namespace_io, "_ntdll", _Overlay(ntdll, NtCreateFile=ntcreate))
        patch.setattr(namespace, "publication", _Overlay(namespace_io, create_root_locked=create))
        with pytest.raises(namespace_io.Namespace3IOError) as raised:
            initialize(env)
        suffix = "CREATE_UNAVAILABLE" if completion == "pending" else "COMPLETION_UNCONFIRMED"
        assert raised.value.code == "NAMESPACE3_IO_" + suffix
    assert calls == [True]
    if completion == "pending":
        assert_reserved_only(env)
    else:
        assert (env["data"] / ROOT).is_dir() and not (env["data"] / ROOT / SEAL).exists()
        assert_partial_is_preserved_and_cannot_resume(env)


@pytest.mark.parametrize("boundary", ["callback", "second-source-exit", "outer-parent-exit"],
                         ids=["callback-30", "second-source-30", "all-scopes-30"])
def test_original_thirty_second_deadline_is_checked_at_entry_and_after_normal_terminal_scopes(namespace_env, monkeypatch, boundary):
    env, clock, completions = namespace_env, [0.0], []
    real_capture, real_parent = origin_source.capture_locked, namespace_io.open_parent_locked

    @contextmanager
    def capture(*args, **kwargs):
        with real_capture(*args, **kwargs) as observed:
            yield observed
        completions.append("source")
        if boundary == "second-source-exit" and completions.count("source") == 2:
            clock[0] = 30.0

    @contextmanager
    def parent(*args, **kwargs):
        with real_parent(*args, **kwargs) as directory:
            yield directory
        if boundary == "outer-parent-exit" and kwargs.get("write") is True:
            completions.append("outer-parent")
            clock[0] = 30.0

    def callback():
        completions.append("callback")
        if boundary == "callback":
            clock[0] = 30.0

    monkeypatch.setattr(namespace, "time", _Overlay(namespace.time, monotonic=lambda: clock[0]))
    monkeypatch.setattr(namespace, "origin", _Overlay(origin_source, capture_locked=capture))
    monkeypatch.setattr(namespace, "publication", _Overlay(namespace_io, open_parent_locked=parent))
    before = tree(env)
    assert_error(lambda: initialize(env, check_cancelled=callback), "DEADLINE")
    assert completions.count("callback") == 1
    if boundary == "callback":
        assert tree(env) == before
    else:
        assert completions.count("source") == 2
        contract.cross_validate_complete(env["first"], env["second"], *records(env))
    assert_pair_preserved(env)


def test_actual_parent_native_open_is_registered_and_closed_once_before_post_call_deadline_failure(namespace_env, monkeypatch):
    env, clock, selected, closed = namespace_env, [0.0], [None], []
    kernel = namespace_io._kernel

    def open_file(*args):
        result = kernel.CreateFileW(*args)
        assert result is not None and result != namespace_io._INVALID_HANDLE
        selected[0] = result
        clock[0] = 30.0
        return result

    def close(handle):
        result = kernel.CloseHandle(handle)
        if selected[0] is not None and handle == selected[0]:
            assert result
            closed.append(True)
        return result

    before = tree(env)
    monkeypatch.setattr(namespace, "time", _Overlay(namespace.time, monotonic=lambda: clock[0]))
    monkeypatch.setattr(namespace_io, "_kernel", _Overlay(kernel, CreateFileW=open_file, CloseHandle=close))
    assert_error(lambda: initialize(env), "DEADLINE")
    assert selected[0] is not None and closed == [True] and tree(env) == before


@pytest.mark.parametrize("limit", ["name-count", "utf8-bytes"], ids=["all-name-count", "all-name-bytes"])
def test_ordinary_nonprefix_names_are_charged_to_the_same_cumulative_census_budget(namespace_env, monkeypatch, limit):
    env, real_scandir, charged = namespace_env, os.scandir, []
    extra_names = (["ordinary-synthetic-" + str(index) for index in range(4097)] if limit == "name-count"
                   else ["ordinary-" + str(index) + "-" + "汉" * 5000 for index in range(100)])

    class Entries:
        def __init__(self, path):
            self.actual, self.extra, self.actual_done = real_scandir(path), iter(extra_names), False
        def __enter__(self):
            self.actual.__enter__()
            return self
        def __next__(self):
            if not self.actual_done:
                try:
                    return next(self.actual)
                except StopIteration:
                    self.actual_done = True
            value = next(self.extra)
            charged.append(value)
            return SimpleNamespace(name=value)
        def __exit__(self, kind, value, tb):
            return self.actual.__exit__(kind, value, tb)

    before = tree(env)
    monkeypatch.setattr(namespace, "os", _Overlay(os, scandir=Entries))
    assert_error(lambda: initialize(env), "LIMIT")
    assert charged and len(charged) < len(extra_names)
    assert all(not name.casefold().startswith(fence.PREFIX) for name in charged)
    assert tree(env) == before


def test_same_fileio_raw_node_read_charges_actual_bytes_without_buffer_prefetch(namespace_env, tmp_path):
    """Small remaining allowance is a budget fault seam; actual FileIO is used."""
    path = tmp_path / "budget-node-raw.bin"
    path.write_bytes(b"actual raw node bytes")
    budget = namespace._Budget(None)
    budget.control_bytes = namespace.MAX_CONTROL_BYTES - 1
    with path.open("rb") as buffer:
        with namespace._NodeStream(buffer, budget) as node:
            assert node.fileno() == buffer.raw.fileno()
            assert node.read(128) == b"a"
            assert buffer.raw.tell() == 1 and budget.control_bytes == namespace.MAX_CONTROL_BYTES
            assert_error(lambda: node.read(1), "LIMIT")
            assert buffer.raw.tell() == 1
        assert buffer.closed and buffer.raw.closed
    assert_pair_preserved(namespace_env)


def test_actual_control_read_consumes_remaining_allowance_and_eof_request_cannot_overrun_budget(namespace_env):
    env, budget = namespace_env, namespace._Budget(None)
    budget.control_bytes = namespace.MAX_CONTROL_BYTES - len(env["first"])
    before = tree(env)
    with lifecycle.exclusive(env["profile"]):
        with namespace_io.open_parent_locked(env["profile"], budget) as parent:
            with pytest.raises(namespace.Namespace3Error) as raised:
                with namespace_io.read_control_locked(env["profile"], parent, ORIGIN_FIRST, budget,
                                                      origin_contract.MAX_RESERVATION_BYTES):
                    pytest.fail("EOF request at exhausted actual byte allowance was accepted")
            assert raised.value.code == "NAMESPACE3_LIMIT"
    assert budget.control_bytes == namespace.MAX_CONTROL_BYTES and tree(env) == before


def test_actual_node_and_native_control_chunks_share_one_budget_while_fresh_source_uses_original_source_bound(namespace_env, monkeypatch):
    env, charges, budgets, source_deltas = namespace_env, [], [], []
    real_node_read = namespace._NodeStream.read
    real_read, real_write = namespace_io._read_chunk, namespace_io._write_chunk
    real_fresh = namespace._fresh_origin

    def charge(kind, budget, before, amount):
        assert budget.control_bytes - before == amount
        charges.append((kind, amount))
        budgets.append(budget)

    def node_read(node, requested):
        before = node.budget.control_bytes
        result = real_node_read(node, requested)
        charge("node-actual-FileIO", node.budget, before, len(result))
        return result

    def read(handle, requested, budget):
        before = budget.control_bytes
        result = real_read(handle, requested, budget)
        charge("control-actual-ReadFile", budget, before, len(result))
        return result

    def write(handle, raw, budget):
        before = budget.control_bytes
        result = real_write(handle, raw, budget)
        charge("control-actual-WriteFile", budget, before, result)
        return result

    def fresh(*args):
        budget = args[-1]
        before = budget.control_bytes
        result = real_fresh(*args)
        source_deltas.append(budget.control_bytes - before)
        return result

    monkeypatch.setattr(namespace._NodeStream, "read", node_read)
    monkeypatch.setattr(namespace_io, "_read_chunk", read)
    monkeypatch.setattr(namespace_io, "_write_chunk", write)
    monkeypatch.setattr(namespace, "_fresh_origin", fresh)
    result = initialize(env)
    assert {kind for kind, _ in charges} == {"node-actual-FileIO", "control-actual-ReadFile", "control-actual-WriteFile"}
    assert budgets and all(budget is budgets[0] for budget in budgets)
    assert budgets[0].control_bytes == sum(amount for _, amount in charges) <= namespace.MAX_CONTROL_BYTES
    assert source_deltas == [0, 0] and result.scope == SCOPE
    assert_pair_preserved(env)
