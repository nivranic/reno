"""Actual prospective17 facilities join member roots without touching live data."""
import uuid
import os

import pytest

from reno import storage_roots as roots, storage_lifecycle as lifecycle, sync_state
from reno import sync_source_authority as authority, sync_source_continuity as continuity
from reno import sync_source_readmit_schema as schema
from tests.test_storage_roots_group import libraries, enroll, registered, scan, captured, statuses  # noqa: F401
from tests.test_sync_source_readmit_contract import SOURCE, TARGET, COMMITTED
from tests.test_storage_source_authority_roots import activate, seed, control


def prospective(libraries, side, *, version=17):
    env = libraries()
    identity = SOURCE if side == "home" else TARGET
    env.update(library_id=identity["library_id"], node_id=identity["node_id"])
    env["con"].execute("UPDATE meta SET value=? WHERE key='library_id'", (env["library_id"],))
    (env["profile"].database.parent / "node-identity.json").write_text(
        '{"instance_id":"' + env["node_id"] + '"}', encoding="utf-8")
    env["con"].commit()
    enroll(env)
    if version == 17:
        schema.install(env["con"])
        env["con"].execute("UPDATE meta SET value='17' WHERE key='migration_version'")
        env["con"].commit()
    return env


def home_facility(env, *, populated=False):
    profile = env["profile"]
    store = sync_state.StateStore(profile.database.parent, profile=profile)
    store.initialize({"home_library_id": SOURCE["library_id"], "home_node_id": SOURCE["node_id"],
        "target_library_id": TARGET["library_id"], "target_node_id": TARGET["node_id"],
        "target_url": "https://synthetic-peer.example.test"}, 1)
    activate(store)
    if populated:
        seed(store, [("controls", control())])
    return store


def cloud_facility(env, *, foreign=False):
    profile = env["profile"]
    active = {"format_version": 1, "continuity_id": str(uuid.uuid4()),
        "owner_profile_sha256": continuity.profile_sha256(profile),
        "source_identity": {key: SOURCE[key] for key in ("library_id", "node_id")},
        "target_identity": {"library_id": str(uuid.uuid4()) if foreign else env["library_id"],
                            "node_id": env["node_id"]}, "target_epoch": 1, "activated_at": COMMITTED}
    with lifecycle.shared(profile), continuity.locked(profile, initialize=True) as guard:
        guard.activate_files(active)
    columns = schema.COLUMNS[schema.ACTIVATION]
    row = {"continuity_id": active["continuity_id"], "format_version": 1,
        "owner_profile_sha256": active["owner_profile_sha256"], "target_epoch": 1,
        "contract_json": continuity._encoded(active).decode(), "contract_sha256": continuity._digest(active),
        "activated_at": COMMITTED, **{side + "_" + key: active[side + "_identity"][key]
            for side in ("source", "target") for key in ("library_id", "node_id")}}
    env["con"].execute('INSERT INTO "' + schema.ACTIVATION + '"(' + ','.join(columns) + ') VALUES('
        + ','.join('?' for _ in columns) + ')', tuple(row[key] for key in columns))
    env["con"].commit()


@pytest.mark.parametrize("side", ["home", "cloud"])
def test_actual_healthy17_facility_has_explicit_scoped_binding_without_global_gc_blocker(libraries, side):
    env = prospective(libraries, side)
    if side == "home":
        home_facility(env)
    else:
        cloud_facility(env)
    _path, item = registered(env)
    before = env["con"].total_changes
    result = scan(env)
    section = "source_authority" if side == "home" else "source_continuity"
    bindings = [value for value in result.base_inputs.source_bindings if value.section == section]
    assert bindings and all(value.state == "present" for value in bindings)
    assert not result.effective_inputs.blockers and statuses(result)[item["artifact_id"]] == "candidate"
    assert env["con"].total_changes == before


def test_old16_home_facility_never_borrows_healthy17_scope_and_known_facts_survive(libraries):
    env = prospective(libraries, "home", version=16)
    home_facility(env, populated=True)
    _path, item = registered(env)
    result = scan(env)
    assert {value.state for value in result.base_inputs.source_bindings if value.section == "source_authority"} == {"unknown"}
    projection = next(value["source_authority"] for value in result.source_bindings if "source_authority" in value)
    assert projection["references"] and projection["unknown"]
    assert statuses(result)[item["artifact_id"]] == "candidate" and not result.effective_inputs.blockers


def test_actual_cloud_owner_node_identity_is_checked_beyond_self_consistent_file_and_sql(libraries):
    env = prospective(libraries, "cloud")
    cloud_facility(env, foreign=True)
    _path, item = registered(env)
    result = scan(env)
    projection = next(value["source_continuity"] for value in result.source_bindings if "source_continuity" in value)
    assert any(value["code"] == "ROOTS_SOURCE_CONTINUITY_OWNER_UNKNOWN" for value in projection["unknown"])
    assert "unknown" in {value.state for value in result.base_inputs.source_bindings if value.section == "source_continuity"}
    assert statuses(result)[item["artifact_id"]] == "candidate"


def test_actual_append_after_group_capture_is_retriable_and_never_hidden_by_a_mutable_pointer(libraries):
    env = prospective(libraries, "home")
    store = home_facility(env)
    with pytest.raises(roots.RootError, match="ROOTS_SOURCE_CHANGED") as found:
        with captured(env):
            seed(store, [("controls", control())])
    assert found.value.retriable


@pytest.mark.skipif(os.name == "nt", reason="POSIX immutable pins permit an actual adversarial record replacement")
def test_record_replacement_with_unchanged_names_is_a_group_retriable_source_change(libraries):
    env = prospective(libraries, "home")
    store = home_facility(env, populated=True)
    path = next((store.data_dir / authority.DIRECTORY / authority.VERSION_DIRECTORY / "controls").glob("*.json"))
    with pytest.raises(roots.RootError, match="ROOTS_SOURCE_CHANGED") as found:
        with captured(env):
            replacement = path.with_suffix(".replacement")
            replacement.write_bytes(path.read_bytes())
            replacement.chmod(0o600)
            replacement.replace(path)
    assert found.value.retriable


def test_actual_future_name_appearance_changes_owner_cas_while_plain16_does_not_adopt_future_tables(libraries):
    env = prospective(libraries, "home", version=16)
    before = roots.owner_sql_sha256(env["con"])
    assert roots.owner_sql_sha256(env["con"]) == before
    env["con"].execute('CREATE TEMP TABLE "' + schema.RESERVED_NAMES[-1].upper() + '"(value TEXT)')
    assert roots.owner_sql_sha256(env["con"]) != before
