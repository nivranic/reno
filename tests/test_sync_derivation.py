"""P04 wire and archive compatibility on disposable libraries and real files."""
import hashlib
import json
import os
import sqlite3
import uuid

import pytest

from reno import db, derivation, report, sync_checkpoints as checkpoints, sync_recovery as recovery
from reno import sync_protocol as protocol, sync_reports, sync_snapshot as snapshot, sync_state
from reno import storage_lifecycle
from reno.sync_protocol import SyncError
from tests.test_sync_checkpoints import receiver, seed, stage, commit, preview, publication, STAMP  # noqa: F401
from tests.test_sync_snapshot import source, export  # noqa: F401
from tests.test_sync_transfer import env, publish, report_entry, VIDEO, receiver_guard  # noqa: F401


def bind_relations(con):
    atoms = {atom["id"]: atom for atom in db.all_atoms(con)}
    saved = {}
    for kind, table, key in (("cluster", "knowledge_cluster", "cluster_id"),
                             ("conflict", "conflict_case", "conflict_id")):
        for row in con.execute("SELECT * FROM " + table).fetchall():
            binding = derivation.bind_inputs(atoms, derivation.member_ids(kind, row))
            raw = protocol._json_bytes(binding).decode()
            con.execute(f"UPDATE {table} SET derivation_json=? WHERE {key}=?", (raw, row[key]))
            saved[row[key]] = raw
    return saved


def add_relations(con, atom_id):
    video_id = con.execute("SELECT video_id FROM knowledge_atom WHERE id=?", (atom_id,)).fetchone()[0]
    con.execute("INSERT INTO knowledge_atom(id,video_id,claim) VALUES('z_atom',?,'另一项建议')", (video_id,))
    con.execute("DELETE FROM knowledge_cluster")
    con.execute("DELETE FROM conflict_case")
    con.execute("UPDATE knowledge_atom SET cluster_id='bound_cluster' WHERE id IN (?, 'z_atom')", (atom_id,))
    con.execute("INSERT INTO knowledge_cluster(cluster_id,relation,members_json,linked_conflict) VALUES(?,?,?,?)",
                ("bound_cluster", "conflicting", json.dumps([atom_id, "z_atom"]), "bound_conflict"))
    con.execute("INSERT INTO conflict_case(conflict_id,ctype,side_a_json,side_b_json) VALUES(?,?,?,?)",
                ("bound_conflict", "polarity_conflict", json.dumps({"atoms": [atom_id]}), json.dumps({"atoms": ["z_atom"]})))
    return bind_relations(con)


@pytest.mark.parametrize("stale", [False, True])
def test_export_keeps_original_binding_and_permits_valid_stale_content(source, stale):
    con = source[0]
    saved = bind_relations(con)
    if stale:
        con.execute("UPDATE knowledge_atom SET claim='变更后的输入' WHERE id='atom_v_1'")
    con.commit()
    payload = export(source)
    for table, key in (("knowledge_cluster", "cluster_id"), ("conflict_case", "conflict_id")):
        for row in payload["globals"][table]:
            assert row["derivation_json"] == saved[row[key]]
    assert snapshot.validate_snapshot(payload) == payload
    states = derivation.evaluate_relations(con)
    assert states["clusters"]["cluster_main"]["state"] == ("stale" if stale else "active")
    assert states["conflicts"]["conflict_main"]["state"] == ("stale" if stale else "active")


def test_null_new_columns_are_omitted_without_mutating_legacy_snapshot(source):
    original = export(source)
    assert all("derivation_json" not in row for rows in original["globals"].values() for row in rows)
    raw = protocol._json_bytes(original)
    assert protocol._json_bytes(snapshot.validate_snapshot(original)) == raw
    assert protocol._json_bytes(original) == raw
    assert all("derivation_json" in spec["columns"] for name, spec in protocol.TABLES.items()
               if name in ("knowledge_cluster", "conflict_case"))


@pytest.mark.parametrize("damage", ["version", "bool_version", "extra", "fingerprint", "duplicate", "unordered", "null_string"])
def test_wire_rejects_malformed_derivation_shapes_before_database_mutation(source, damage):
    con = source[0]
    bind_relations(con)
    payload = export(source)
    row = next(row for row in payload["globals"]["knowledge_cluster"] if row["cluster_id"] == "cluster_main")
    binding = json.loads(row["derivation_json"])
    if damage == "version":
        binding["version"] = 2
    elif damage == "bool_version":
        binding["version"] = True
    elif damage == "extra":
        binding["private"] = "forbidden"
    elif damage == "fingerprint":
        binding["inputs"][0]["fingerprint"] = "not-a-fingerprint"
    elif damage == "duplicate":
        binding["inputs"].append(binding["inputs"][0])
    elif damage == "unordered":
        binding["inputs"].reverse()
    else:
        binding = None
    row["derivation_json"] = json.dumps(binding)
    changes = con.total_changes
    with pytest.raises(SyncError):
        snapshot.validate_snapshot(payload)
    assert con.total_changes == changes


@pytest.mark.parametrize("kind", ["knowledge_cluster", "conflict_case"])
def test_legal_stale_fingerprints_do_not_relax_exact_input_reference_closure(source, kind):
    con = source[0]
    bind_relations(con)
    payload = export(source)
    row = payload["globals"][kind][0]
    binding = json.loads(row["derivation_json"])
    binding["inputs"] = [{"atom_id": "missing_atom", "fingerprint": "v1:" + "a" * 64}]
    row["derivation_json"] = json.dumps(binding)
    with pytest.raises(SyncError) as caught:
        snapshot.validate_snapshot(payload)
    assert caught.value.code == "SYNC_INCOMPLETE_SNAPSHOT"


@pytest.mark.parametrize("stale", [False, True])
def test_publication_preserves_bindings_without_rebinding_to_receiver_content(env, stale):
    source_con, target = env["source"], env["con"]
    saved = add_relations(source_con, "atom")
    if stale:
        source_con.execute("UPDATE knowledge_atom SET claim='改变原子内容' WHERE id='atom'")
    source_con.commit()
    source_con.execute("BEGIN")
    value = snapshot.export_snapshot(source_con, publication_id=str(uuid.uuid4()), media={VIDEO: env["media"]})
    source_con.rollback()
    with receiver_guard(env):
        publish(env, value)
        target.commit()
    for table, key in (("knowledge_cluster", "cluster_id"), ("conflict_case", "conflict_id")):
        row = target.execute("SELECT * FROM " + table).fetchone()
        assert row["derivation_json"] == saved[row[key]]
    states = derivation.evaluate_relations(target)
    assert states["clusters"]["bound_cluster"]["state"] == ("stale" if stale else "active")
    assert states["conflicts"]["bound_conflict"]["state"] == ("stale" if stale else "active")


def legacy_archive(env, kind):
    """Build an actual schema-7 SQLite image, not a relabeled schema-9 file."""
    con, identifier, origin = env["con"], str(uuid.uuid4()), str(uuid.uuid4())
    columns = checkpoints.columns_for_schema(7)
    ddl = checkpoints._ddl(7)
    assert ddl["knowledge_cluster"] == ('CREATE TABLE "knowledge_cluster" ("cluster_id" TEXT,"canonical_topic" TEXT,'
        '"relation" TEXT,"judge_reason" TEXT,"members_json" TEXT,"linked_conflict" TEXT,"created_at" TEXT,PRIMARY KEY ("cluster_id"))')
    assert ddl["conflict_case"] == ('CREATE TABLE "conflict_case" ("conflict_id" TEXT,"ctype" TEXT,"status" TEXT,'
        '"side_a_json" TEXT,"side_b_json" TEXT,"analysis_json" TEXT,"recommended_action" TEXT,"created_at" TEXT,PRIMARY KEY ("conflict_id"))')
    archive = sqlite3.connect(":memory:")
    archive.row_factory = sqlite3.Row
    for sql in ddl.values():
        archive.execute(sql)
    for table, names in columns.items():
        selected = ",".join('"' + name + '"' for name in names)
        where = " WHERE substr(key,1,5)='step:'" if table == "meta" else ""
        checkpoints._insert_rows(archive, table, names, [tuple(row) for row in con.execute(f'SELECT {selected} FROM "{table}"{where}')])
    assert checkpoints._capture_media(archive, env["store"].root) == []
    entry = {"checkpoint_id": identifier, "receiver_version_json": checkpoints._json(db.knowledge_version(con)),
             "format_version": 1 if kind == "publication" else 2, "schema_version": 7,
             "storage_key": identifier + ".sqlite", "created_at": STAMP}
    entry.update({"publication_id": origin} if kind == "publication" else {"operation_id": origin, "origin_kind": "recovery"})
    origin_keys = ("publication_id",) if kind == "publication" else ("operation_id", "origin_kind")
    manifest = {key: entry[key] for key in ("checkpoint_id", *origin_keys, "format_version", "schema_version", "created_at")}
    manifest.update(receiver_version=db.knowledge_version(con), receiver_node_id=checkpoints._node_id(con), issues=[],
                    row_counts={table: archive.execute('SELECT count(*) FROM "' + table + '"').fetchone()[0] for table in columns})
    archive.execute("INSERT INTO checkpoint_manifest VALUES(1,?)", (checkpoints._json(manifest),))
    archive.commit()
    raw = archive.serialize()
    archive.close()
    env["root"].mkdir(exist_ok=True)
    (env["root"] / entry["storage_key"]).write_bytes(raw)
    entry.update(sha256=hashlib.sha256(raw).hexdigest(), byte_size=len(raw))
    if kind == "publication":
        con.execute("BEGIN IMMEDIATE")
        publication(con, origin)
        checkpoints.register(con, entry)
    else:
        con.execute("INSERT INTO sync_recovery_operation VALUES(?, 'restore', ?, '{}', '{}', ?)", (origin, "a" * 64, STAMP))
        checkpoints._insert_rows(con, "sync_recovery_checkpoint", tuple(entry), [tuple(entry.values())])
    con.commit()
    return entry


@pytest.mark.parametrize("kind", ["publication", "recovery"])
def test_real_schema7_archive_preview_and_restore_leave_new_bindings_unknown(receiver, kind):
    seed(receiver)
    con = receiver["con"]
    add_relations(con, "atom1")
    con.commit()
    entry = legacy_archive(receiver, kind)
    result = recovery.preview_restore(con, entry["checkpoint_id"], checkpoint_kind=kind, media_store=receiver["store"])
    assert result["recoverable"] and result["schema_version"] == 7
    con.execute("UPDATE knowledge_atom SET claim='恢复前的新内容' WHERE id='atom1'")
    con.commit()
    recovered = recovery.restore(con, entry["checkpoint_id"], checkpoint_kind=kind,
        expected_version=db.knowledge_version(con), expected_control_revision=0,
        operation_id=str(uuid.uuid4()), media_store=receiver["store"])
    assert recovered["control"]["state"] == "held"
    assert con.execute("SELECT derivation_json FROM knowledge_cluster").fetchone()[0] is None
    assert con.execute("SELECT derivation_json FROM conflict_case").fetchone()[0] is None
    states = derivation.evaluate_relations(con)
    assert states["clusters"]["bound_cluster"]["state"] == "unknown"
    assert states["conflicts"]["bound_conflict"]["state"] == "unknown"
    protection = next(row for row in recovery.list_recovery_checkpoints(con, media_store=receiver["store"])
                      if row["checkpoint_id"] == recovered["protection_checkpoint_id"])
    assert protection["schema_version"] == 9


@pytest.mark.parametrize("stale", [False, True])
def test_schema9_archive_restore_preserves_active_or_stale_binding(receiver, stale):
    seed(receiver)
    con = receiver["con"]
    saved = add_relations(con, "atom1")
    if stale:
        con.execute("UPDATE knowledge_atom SET claim='归档时已改变的内容' WHERE id='atom1'")
    con.commit()
    entry = stage(receiver)
    commit(receiver, entry)
    assert entry["schema_version"] == 9 and preview(receiver, entry)["recoverable"]
    con.execute("UPDATE knowledge_atom SET claim='归档之后的新内容' WHERE id='atom1'")
    con.commit()
    recovery.restore(con, entry["checkpoint_id"], expected_version=db.knowledge_version(con),
        expected_control_revision=0, operation_id=str(uuid.uuid4()), media_store=receiver["store"])
    for table, key in (("knowledge_cluster", "cluster_id"), ("conflict_case", "conflict_id")):
        row = con.execute("SELECT * FROM " + table).fetchone()
        assert row["derivation_json"] == saved[row[key]]
    states = derivation.evaluate_relations(con)
    assert states["clusters"]["bound_cluster"]["state"] == ("stale" if stale else "active")
    assert states["conflicts"]["bound_conflict"]["state"] == ("stale" if stale else "active")


def test_old_unbound_outbox_with_v2_report_retries_original_uuid_hash_and_bytes(env, tmp_path, monkeypatch):
    source_con = env["source"]
    add_relations(source_con, "atom")
    source_con.execute("UPDATE knowledge_cluster SET derivation_json=NULL")
    source_con.execute("UPDATE conflict_case SET derivation_json=NULL")
    source_con.commit()
    source_con.execute("BEGIN")
    value = snapshot.export_snapshot(source_con, publication_id=str(uuid.uuid4()), media={VIDEO: env["media"]})
    source_con.rollback()
    legacy_report = {**report_entry(env), "version": value["version"], "policy_version": "reports-v2"}
    value["reports"] = [legacy_report]
    assert all("derivation_json" not in row for rows in value["globals"].values() for row in rows)
    stream = {key: db.knowledge_version(env["con"])[key] for key in ("library_id", "library_epoch")}
    envelope = {"snapshot": value, "claims": [], "recovery_generation": 0, "recovery_stream": stream}
    state_root = tmp_path / "old-home-state"
    state_profile = storage_lifecycle.StorageProfile(state_root / "reno.db", tmp_path / "old-home-media",
        tmp_path / "old-home-reports", tmp_path / "old-home-checkpoints")
    storage_lifecycle.initialize(state_profile)
    store = sync_state.StateStore(state_root, profile=state_profile)
    store.initialize({"home_library_id": value["version"]["library_id"], "home_node_id": env["identity"]["node_id"],
        "target_library_id": stream["library_id"], "target_node_id": str(uuid.uuid4()), "target_url": "https://sync.example.test"}, 1)
    directory = store.data_dir / "sync-outbox"
    directory.mkdir()
    path = directory / (value["publication_id"] + ".json")
    raw = protocol._json_bytes(envelope)
    path.write_bytes(raw)
    os.chmod(path, 0o600)
    metadata = {"publication_id": value["publication_id"], "sha256": hashlib.sha256(raw).hexdigest(), "byte_size": len(raw)}
    monkeypatch.setattr(report, "POLICY_VERSION", "reports-v3")
    frozen = store.read_publication(metadata)
    assert frozen == envelope and path.read_bytes() == raw
    with receiver_guard(env):
        first = publish(env, frozen["snapshot"], recovery_stream=stream)
        env["con"].commit()
        expected_hash = hashlib.sha256(protocol._json_bytes({"snapshot": value, "claims": []})).hexdigest()
        assert env["con"].execute("SELECT payload_sha256 FROM sync_publication").fetchone()[0] == expected_hash
        assert publish(env, store.read_publication(metadata)["snapshot"], recovery_stream=stream) == {**first, "replayed": True}
        assert path.read_bytes() == raw and store.find_publication(value["publication_id"]) == metadata
        assert env["con"].execute("SELECT policy_version FROM sync_report").fetchone()[0] == "reports-v2"
        assert sync_reports.manifest(env["con"], "checklist")["policy_version"] != report.POLICY_VERSION
        assert env["con"].execute("SELECT count(*) FROM sync_checkpoint").fetchone()[0] == 1


@pytest.mark.parametrize("policy", ["reports-v999", None, True, [], {}])
def test_unknown_report_policy_still_rejected(env, policy):
    with pytest.raises(SyncError):
        sync_reports.validate_reports([{**report_entry(env), "policy_version": policy}], env["payload"]["version"])
