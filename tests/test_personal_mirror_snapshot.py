"""Actual committed captures from synthetic roots; no mirror/open-cloud action."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import uuid

import pytest

from reno import checklists, config, evidence_identity as identity, personal, personal_schema
from reno import personal_mirror_contract as contract, personal_mirror_snapshot as capture
from reno import storage_admin, storage_lifecycle as lifecycle, storage_readonly as readonly

NOW = "2026-10-03T00:00:00Z"
BODY = b"%PDF-1.4\nsynthetic personal evidence\n%%EOF\n"


def uid(number):
    return str(uuid.UUID(int=number))


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def insert(con, table, **values):
    con.execute(f"INSERT INTO {table}({','.join(values)}) VALUES({','.join('?' for _ in values)})", tuple(values.values()))


def raw_row(con, table, pk, key):
    columns = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
    return dict(zip(columns, con.execute(f"SELECT * FROM {table} WHERE {pk}=?", (key,)).fetchone()))


def historical_view(row):
    result = dict(row)
    for name in list(result):
        if name.endswith("_json"):
            view_name = "context_snapshot" if name == "context_json" else name[:-5]
            result[view_name] = json.loads(result.pop(name))
    return result


def save_history(con, kind, key, snapshot, operation_id):
    # Spaces and Chinese escape style are deliberately part of stored history.
    original = json.dumps(snapshot, ensure_ascii=False, indent=2)
    insert(con, "personal_revision", entity_kind=kind, entity_id=key, revision=snapshot["revision"],
           snapshot_json=original, operation_id=operation_id, created_at=NOW)
    return original


def save_receipt(con, operation_id, scope, **body):
    body["operation_id"] = operation_id
    insert(con, "mutation_receipt", operation_id=operation_id, scope=scope,
           request_hash=hashlib.sha256(scope.encode("utf-8")).hexdigest(), response_status=201,
           response_json=json.dumps(body, ensure_ascii=False, indent=1), created_at=NOW)


@pytest.fixture
def source_env(tmp_path, monkeypatch):
    root = tmp_path / "source"
    data, media = root / "data", root / "media"
    data.mkdir(parents=True)
    monkeypatch.setattr(config, "DATA", data)
    monkeypatch.setattr(config, "MEDIA", media)
    monkeypatch.setenv("RENO_DATA_DIR", str(data))
    local = root / "config.synthetic.json"
    local.write_text(encoded({"node_role": "home", "personal_features_enabled": True}), encoding="utf-8")
    monkeypatch.setenv("RENO_CONFIG_FILE", str(local))
    monkeypatch.setattr(config, "_cache", None)
    library, primary, project = uid(101), uid(102), uid(1)
    ids = {"project": project, "source": uid(2), "card": uid(3), "checklist": uid(4),
           "item": uid(5), "attachment": uid(6), "check": uid(7), "ref": uid(8)}
    operations = {"source": uid(11), "card": uid(12), "checklist": uid(13), "attachment": uid(14), "check": uid(15)}
    node = data / "node-identity.json"
    node.write_text(json.dumps({"instance_id": primary}, indent=1), encoding="utf-8")
    database = data / "reno.db"
    con = sqlite3.connect(database)
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)")
    personal_schema.install(con)
    con.executemany("INSERT INTO meta VALUES(?,?)", [("library_id", library), ("personal_primary_id", primary),
                       ("primary_epoch", "1"), ("default_project_id", project), ("kb_revision", "91"),
                       ("migration_version", "10"), ("private_worker_state", "must not be mirrored")])
    insert(con, "renovation_project", project_id=project, name="原始项目", profile_json=' { "city" : "绍兴" } ',
           created_at=NOW, updated_at=NOW)
    pview = historical_view(raw_row(con, "renovation_project", "project_id", project))
    save_history(con, "project", project, pview, None)
    insert(con, "source_record", source_id=ids["source"], project_id=project, source_type="product_document",
           title="说明书", excerpt="原文保留", created_at=NOW, updated_at=NOW)
    sview = historical_view(raw_row(con, "source_record", "source_id", ids["source"]))
    save_history(con, "source", ids["source"], sview, operations["source"])
    save_receipt(con, operations["source"], f"project:{project}:sources:create", source=sview)
    insert(con, "decision_card", card_id=ids["card"], project_id=project, title="待核对",
           context_json=' { "基层" : "水泥" } ', content_snapshot_json=' { "kind": "manual", "text" : "原话" } ',
           created_at=NOW, updated_at=NOW)
    # Follow the actual producer's bind_refs -> saved view -> history ->
    # receipt order, rather than adding a raw ref after saving revision 1.
    target = personal.resolve_target(con, project, "source", ids["source"])
    personal.bind_refs(con, project, "card", ids["card"],
                       [{"target_kind": "source", "target_id": ids["source"], "target_fingerprint": target[1]}])
    ref = con.execute("SELECT ref_id,snapshot_json FROM personal_evidence_ref WHERE owner_kind='card' AND owner_id=?",
                      (ids["card"],)).fetchone()
    ids["ref"], ref_snapshot = ref["ref_id"], ref["snapshot_json"]
    cview = personal.card_view(con, ids["card"])
    save_history(con, "card", ids["card"], cview, operations["card"])
    save_receipt(con, operations["card"], f"project:{project}:cards:create", card=cview)
    insert(con, "checklist_instance", instance_id=ids["checklist"], project_id=project, title="现场核对",
           basis_kb_revision=91, created_at=NOW, updated_at=NOW)
    insert(con, "checklist_item", item_id=ids["item"], instance_id=ids["checklist"], title="核对记录",
           basis_kind="observation_only", created_at=NOW, updated_at=NOW)
    personal.bind_refs(con, project, "checklist_item", ids["item"], [])
    iview = historical_view(raw_row(con, "checklist_item", "item_id", ids["item"]))
    iview.update(project_id=project, refs=[])
    lview = historical_view(raw_row(con, "checklist_instance", "instance_id", ids["checklist"]))
    save_history(con, "checklist_item", ids["item"], iview, operations["checklist"])
    save_history(con, "checklist", ids["checklist"], lview, operations["checklist"])
    save_receipt(con, operations["checklist"], f"project:{project}:checklists:create", checklist={**lview, "items": [iview]})
    folder = data / "personal-attachments" / "files"
    folder.mkdir(parents=True)
    storage_key = f"files/{ids['attachment']}.pdf"
    attachment_path = folder / f"{ids['attachment']}.pdf"
    attachment_path.write_bytes(BODY)
    sha = hashlib.sha256(BODY).hexdigest()
    insert(con, "source_attachment", attachment_id=ids["attachment"], source_id=ids["source"], project_id=project,
           sha256=sha, byte_size=len(BODY), media_type="application/pdf", original_name="原资料.pdf",
           storage_key=storage_key, state="ready", created_at=NOW, operation_id=operations["attachment"])
    attachment = raw_row(con, "source_attachment", "attachment_id", ids["attachment"])
    attachment_view = {k: attachment[k] for k in ("attachment_id", "source_id", "project_id", "sha256", "byte_size",
                      "media_type", "original_name", "state", "created_at")}
    save_receipt(con, operations["attachment"], f"source:{ids['source']}:attachment", attachment=attachment_view)
    insert(con, "source_attachment", attachment_id=uid(9), source_id=ids["source"], project_id=project,
           media_type="application/pdf", original_name="未完成.pdf", storage_key=f"files/{uid(9)}.pdf", state="failed",
           created_at=NOW, operation_id=uid(16), error="原失败记录")
    criteria = {key: iview[key] for key in ("title", "conditions", "method_text", "basis_kind", "criteria", "criteria_state")}
    criteria["refs"] = []
    insert(con, "checklist_check", check_id=ids["check"], item_id=ids["item"], item_revision=1,
           criteria_snapshot_hash=identity.fingerprint(criteria), operation_id=operations["check"], commit_seq=1,
           record_kind="inspection", result="unable_to_verify", attachment_refs_json=json.dumps([ids["attachment"]]),
           checked_at=NOW, created_at=NOW)
    check_view = historical_view(raw_row(con, "checklist_check", "check_id", ids["check"]))
    save_receipt(con, operations["check"], f"checklist-item:{ids['item']}:check", check=check_view)
    con.commit()
    profile = storage_admin.profile()
    lifecycle.initialize(profile)
    yield {"root": root, "data": data, "database": database, "con": con, "profile": profile, "ids": ids,
           "operations": operations, "library": library, "primary": primary, "body": attachment_path,
           "ref_snapshot": ref_snapshot, "node": node, "local": local}
    con.close()


def take(env, **kwargs):
    return capture.capture_snapshot(snapshot_id=kwargs.pop("snapshot_id", uid(201)), expected_library_id=env["library"],
                                    expected_primary_id=env["primary"], expected_epoch=1, **kwargs)


def source_bytes(env):
    result = {}
    for path in env["root"].rglob("*"):
        if path.is_file():
            result[str(path.relative_to(env["root"]))] = (path.read_bytes(), path.stat().st_mtime_ns)
    return result


def assert_code(code, action):
    with pytest.raises(contract.PersonalSnapshotError) as caught:
        action()
    assert caught.value.code == code
    return caught.value


def test_actual_capture_preserves_all_ten_tables_raw_json_meta_and_source_files(source_env, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Capture must not seed, connect to live SQLite, initialize, or create upload locks")
    from reno import db, personal_attachments
    monkeypatch.setattr(personal_schema, "seed", forbidden)
    monkeypatch.setattr(db, "connect", forbidden)
    monkeypatch.setattr(lifecycle, "initialize", forbidden)
    monkeypatch.setattr(personal_attachments, "upload_lock", forbidden)
    before = source_bytes(source_env)
    result = take(source_env)
    document = result.snapshot.document
    assert set(document["tables"]) == set(personal_schema.TABLES) == set(contract.TABLE_NAMES)
    for table in personal_schema.TABLES:
        columns = [row[1] for row in source_env["con"].execute(f"PRAGMA table_info({table})")]
        order = document["schema"]["tables"][table]["primary_key"]
        expected = [dict(zip(columns, row)) for row in source_env["con"].execute(f"SELECT * FROM {table} ORDER BY {','.join(order)}")]
        assert document["tables"][table] == expected
        assert result.snapshot.counts[table] == len(expected)
    assert document["tables"]["personal_evidence_ref"][0]["snapshot_json"] == source_env["ref_snapshot"]
    assert {row["key"] for row in document["meta"]} == set(contract.META_KEYS)
    assert "private_worker_state" not in encoded(document)
    assert result.snapshot.counts["source_attachment"] == 2
    assert result.snapshot.counts["attachment_manifest"] == 1
    manifest = document["attachment_manifest"][0]
    assert manifest["sha256"] == hashlib.sha256(BODY).hexdigest()
    assert manifest["byte_size"] == manifest["file_identity"]["byte_size"] == len(BODY)
    assert manifest["file_identity"]["inode"] == str(source_env["body"].stat().st_ino)
    assert result.observations["instance_id"] == source_env["primary"]
    assert source_bytes(source_env) == before
    assert not (source_env["data"] / "personal-attachments" / ".locks").exists()
    assert not any(name in document for name in ("personal_write", "publish", "primary_authorized"))


def test_validator_keeps_original_outer_and_inner_json_bytes_and_verifies_hash_and_counts(source_env):
    facts = take(source_env).snapshot
    original = json.dumps(facts.document, ensure_ascii=False, indent=4).encode("utf-8") + b"\n"
    validated = contract.validate_snapshot(original, expected_sha256=hashlib.sha256(original).hexdigest(),
                                           expected_counts=dict(facts.counts))
    assert validated.snapshot_bytes is original
    assert validated.snapshot_json.encode("utf-8") == original
    assert validated.document["tables"]["personal_evidence_ref"][0]["snapshot_json"] == source_env["ref_snapshot"]
    assert_code("SNAPSHOT_SHA256_MISMATCH", lambda: contract.validate_snapshot(original, expected_sha256="0" * 64))
    assert_code("SNAPSHOT_COUNT_MISMATCH", lambda: contract.validate_snapshot(original, expected_counts={}))
    detached = validated.document
    detached["tables"]["decision_card"][0]["title"] = "does not mutate retained bytes"
    assert validated.snapshot_bytes == original


@pytest.mark.parametrize("mutation,code", [
    (lambda d: d["attachment_manifest"].clear(), "SNAPSHOT_ATTACHMENT_SET_MISMATCH"),
    (lambda d: d["attachment_manifest"][0].update(byte_size=len(BODY) + 1), "SNAPSHOT_ATTACHMENT_CONTENT_MISMATCH"),
    (lambda d: d["attachment_manifest"][0].update(sha256="b" * 64), "SNAPSHOT_ATTACHMENT_CONTENT_MISMATCH"),
    (lambda d: d["attachment_manifest"][0]["file_identity"].update(byte_size=0), "SNAPSHOT_ATTACHMENT_CONTENT_MISMATCH"),
    (lambda d: d["tables"].pop("mutation_receipt"), "SNAPSHOT_TABLE_SET_MISMATCH"),
    (lambda d: d["schema"]["tables"]["decision_card"]["columns"][0].update(hidden=1), "SNAPSHOT_SCHEMA_DRIFT"),
    (lambda d: d["counts"].update(mutation_receipt=999), "SNAPSHOT_COUNT_MISMATCH"),
    (lambda d: d["tables"]["personal_revision"].append(deepcopy(d["tables"]["personal_revision"][0])), "SNAPSHOT_DUPLICATE_OR_UNSORTED_KEY"),
    (lambda d: d["tables"]["mutation_receipt"][0].update(request_hash="not-a-SHA"), "SNAPSHOT_INVALID_SHA256"),
    (lambda d: d["tables"]["personal_evidence_ref"][0].update(owner_kind="unknown"), "SNAPSHOT_INVALID_REFERENCE"),
    (lambda d: d["tables"]["checklist_check"][0].update(criteria_snapshot_hash="v1:" + "b" * 64), "SNAPSHOT_CHECK_CRITERIA_MISMATCH"),
])
def test_contract_rejects_incomplete_or_inconsistent_facts(source_env, mutation, code):
    document = take(source_env).snapshot.document
    mutation(document)
    # A changed check hash is also recorded in its immutable operation result,
    # so this case reaches the historical-criteria binding validation.
    if code == "SNAPSHOT_CHECK_CRITERIA_MISMATCH":
        op = source_env["operations"]["check"]
        receipt = next(r for r in document["tables"]["mutation_receipt"] if r["operation_id"] == op)
        body = json.loads(receipt["response_json"])
        body["check"]["criteria_snapshot_hash"] = "v1:" + "b" * 64
        receipt["response_json"] = encoded(body)
    assert_code(code, lambda: contract.validate_snapshot(encoded(document)))


@pytest.mark.parametrize("damage,code", [
    ("missing", "PERSONAL_CAPTURE_ATTACHMENT_MISSING_OR_UNSAFE"),
    ("size", "PERSONAL_CAPTURE_ATTACHMENT_SIZE_MISMATCH"),
    ("sha", "PERSONAL_CAPTURE_ATTACHMENT_SHA256_MISMATCH"),
])
def test_capture_checks_actual_attachment_body(source_env, damage, code):
    body = source_env["body"]
    if damage == "missing":
        body.unlink()
    elif damage == "size":
        body.write_bytes(BODY + b"x")
    else:
        body.write_bytes(bytes([BODY[0] ^ 1]) + BODY[1:])
    assert_code(code, lambda: take(source_env))


def add_second_project(env):
    second = uid(50)
    insert(env["con"], "renovation_project", project_id=second, name="另一个项目", created_at=NOW, updated_at=NOW)
    snapshot = historical_view(raw_row(env["con"], "renovation_project", "project_id", second))
    save_history(env["con"], "project", second, snapshot, None)
    env["con"].commit()
    return second


def test_capture_rejects_cross_project_attachment_even_with_individual_fks_intact(source_env):
    other = add_second_project(source_env)
    source_env["con"].execute("UPDATE source_attachment SET project_id=? WHERE attachment_id=?",
                              (other, source_env["ids"]["attachment"]))
    source_env["con"].commit()
    assert_code("SNAPSHOT_CROSS_PROJECT_REFERENCE", lambda: take(source_env))


def test_contract_rejects_cross_project_reference_owner(source_env):
    other = add_second_project(source_env)
    document = take(source_env).snapshot.document
    document["tables"]["personal_evidence_ref"][0]["project_id"] = other
    assert_code("SNAPSHOT_CROSS_PROJECT_REFERENCE", lambda: contract.validate_snapshot(encoded(document)))


def test_same_history_key_with_different_original_text_cannot_replace_prior_snapshot(source_env):
    facts = take(source_env).snapshot
    document = facts.document
    document["snapshot_id"] = uid(202)
    row = document["tables"]["personal_revision"][0]
    row["snapshot_json"] = encoded(json.loads(row["snapshot_json"]))
    contract.validate_snapshot(encoded(document))
    assert_code("SNAPSHOT_IMMUTABLE_HISTORY_CHANGED", lambda: contract.validate_snapshot(
        encoded(document), prior_snapshot_json=facts.snapshot_bytes))


def test_same_receipt_operation_with_different_result_bytes_is_rejected(source_env):
    facts = take(source_env).snapshot
    document = facts.document
    document["snapshot_id"] = uid(202)
    receipt = document["tables"]["mutation_receipt"][0]
    receipt["response_json"] = encoded(json.loads(receipt["response_json"]))
    contract.validate_snapshot(encoded(document))
    assert_code("SNAPSHOT_IMMUTABLE_HISTORY_CHANGED", lambda: contract.validate_snapshot(
        encoded(document), prior_snapshot_json=facts.snapshot_bytes))


def test_old_revision_cannot_disagree_with_its_original_operation_result_without_prior_snapshot(source_env):
    con, ids = source_env["con"], source_env["ids"]
    con.execute("UPDATE decision_card SET title='后续版本',revision=2 WHERE card_id=?", (ids["card"],))
    card = personal.card_view(con, ids["card"])
    save_history(con, "card", ids["card"], card, uid(17))
    save_receipt(con, uid(17), f"card:{ids['card']}", card=card)
    con.commit()
    document = take(source_env).snapshot.document
    older = next(r for r in document["tables"]["personal_revision"]
                 if r["entity_kind"] == "card" and r["revision"] == 1)
    saved = json.loads(older["snapshot_json"])
    saved["title"] = "与首次操作回执不一致的历史"
    older["snapshot_json"] = encoded(saved)
    assert_code("SNAPSHOT_HISTORY_RECEIPT_CONTENT_MISMATCH", lambda: contract.validate_snapshot(encoded(document)))


def test_checklist_create_receipt_carries_exact_item_revision_and_persistent_content(source_env):
    document = take(source_env).snapshot.document
    receipt = next(r for r in document["tables"]["mutation_receipt"]
                   if r["operation_id"] == source_env["operations"]["checklist"])
    body = json.loads(receipt["response_json"])
    body["checklist"]["items"][0]["revision"] = 2
    receipt["response_json"] = encoded(body)
    assert_code("SNAPSHOT_HISTORY_RECEIPT_CONTENT_MISMATCH", lambda: contract.validate_snapshot(encoded(document)))


@pytest.mark.parametrize("changed_field", ["ref_id", "fingerprint", "created_at", "target", "snapshot", "missing"])
def test_capture_rejects_current_raw_reference_changed_at_the_same_latest_card_revision(source_env, changed_field):
    env, con, card_id = source_env, source_env["con"], source_env["ids"]["card"]
    before = tuple(con.execute("SELECT card_id,revision FROM decision_card WHERE card_id=?", (card_id,)).fetchone())
    ref_id = env["ids"]["ref"]
    if changed_field == "ref_id":
        con.execute("UPDATE personal_evidence_ref SET ref_id=? WHERE ref_id=?", (uid(30), ref_id))
    elif changed_field == "fingerprint":
        con.execute("UPDATE personal_evidence_ref SET target_fingerprint=? WHERE ref_id=?", ("v1:" + "b" * 64, ref_id))
    elif changed_field == "created_at":
        con.execute("UPDATE personal_evidence_ref SET created_at=? WHERE ref_id=?", ("2026-10-02T00:00:00Z", ref_id))
    elif changed_field == "target":
        con.execute("UPDATE personal_evidence_ref SET target_kind='atom',target_id='different-knowledge-target' WHERE ref_id=?", (ref_id,))
    elif changed_field == "snapshot":
        changed = json.loads(con.execute("SELECT snapshot_json FROM personal_evidence_ref WHERE ref_id=?", (ref_id,)).fetchone()[0])
        changed["title"] = "当前行与保存内容不同"
        con.execute("UPDATE personal_evidence_ref SET snapshot_json=? WHERE ref_id=?", (encoded(changed), ref_id))
    else:
        con.execute("DELETE FROM personal_evidence_ref WHERE ref_id=?", (ref_id,))
    con.commit()
    assert tuple(con.execute("SELECT card_id,revision FROM decision_card WHERE card_id=?", (card_id,)).fetchone()) == before
    assert_code("SNAPSHOT_CURRENT_REFERENCE_HISTORY_MISMATCH", lambda: take(env))


def test_capture_rejects_a_new_raw_item_reference_without_the_matching_latest_saved_refs(source_env):
    env, con = source_env, source_env["con"]
    item_id, project_id = env["ids"]["item"], env["ids"]["project"]
    before = tuple(con.execute("SELECT item_id,revision FROM checklist_item WHERE item_id=?", (item_id,)).fetchone())
    target = personal.resolve_target(con, project_id, "source", env["ids"]["source"])
    personal.bind_refs(con, project_id, "checklist_item", item_id,
                       [{"target_kind": "source", "target_id": env["ids"]["source"], "target_fingerprint": target[1]}])
    con.commit()
    assert tuple(con.execute("SELECT item_id,revision FROM checklist_item WHERE item_id=?", (item_id,)).fetchone()) == before
    assert_code("SNAPSHOT_CURRENT_REFERENCE_HISTORY_MISMATCH", lambda: take(env))


def confirm_current_source_refs(env, kind, operation_id):
    """Follow the actual reference-confirmation producer on a synthetic DB."""
    con, project_id = env["con"], env["ids"]["project"]
    entity_id = env["ids"]["card" if kind == "card" else "item"]
    entity = personal.require_entity(con, kind, entity_id)
    personal.cas_update(con, kind, entity_id, entity["revision"], {"updated_at": NOW})
    target = personal.resolve_target(con, project_id, "source", env["ids"]["source"])
    personal.bind_refs(con, project_id, kind, entity_id,
                       [{"target_kind": "source", "target_id": env["ids"]["source"], "target_fingerprint": target[1]}])
    if kind == "card":
        snapshot = personal.card_view(con, entity_id)
        response, field, scope = snapshot, "card", f"card:{entity_id}:evidence"
    else:
        snapshot = checklists._item_snapshot(con, entity_id)
        response, field, scope = checklists.item_detail(con, entity_id), "item", f"checklist-item:{entity_id}"
    save_history(con, kind, entity_id, snapshot, operation_id)
    save_receipt(con, operation_id, scope, **{field: response})
    con.commit()
    return snapshot


@pytest.mark.parametrize("kind", ["card", "checklist_item"])
def test_legal_ref_update_matches_latest_set_and_retains_old_ref_history_bytes(source_env, kind):
    env, con = source_env, source_env["con"]
    if kind == "checklist_item":
        confirm_current_source_refs(env, kind, uid(20))
    entity_id = env["ids"]["card" if kind == "card" else "item"]
    old = con.execute("SELECT revision,snapshot_json FROM personal_revision WHERE entity_kind=? AND entity_id=? ORDER BY revision DESC LIMIT 1",
                      (kind, entity_id)).fetchone()
    old_revision, original_history_json = old["revision"], old["snapshot_json"]
    field = "evidence_refs" if kind == "card" else "refs"
    old_refs = json.loads(original_history_json)[field]
    assert len(old_refs) == 1
    prior = take(env).snapshot
    current = confirm_current_source_refs(env, kind, uid(21))
    facts = take(env, snapshot_id=uid(202), prior_snapshot_json=prior.snapshot_bytes).snapshot
    latest = json.loads(next(r["snapshot_json"] for r in facts.document["tables"]["personal_revision"]
                             if r["entity_kind"] == kind and r["entity_id"] == entity_id and r["revision"] == current["revision"]))
    assert current["revision"] == old_revision + 1
    assert latest[field][0]["ref_id"] != old_refs[0]["ref_id"]
    matching_raw = [r for r in facts.document["tables"]["personal_evidence_ref"]
                    if r["owner_kind"] == kind and r["owner_id"] == entity_id]
    assert matching_raw[0]["ref_id"] == latest[field][0]["ref_id"]
    retained = con.execute("SELECT snapshot_json FROM personal_revision WHERE entity_kind=? AND entity_id=? AND revision=?",
                           (kind, entity_id, old_revision)).fetchone()[0]
    assert retained == original_history_json
    assert any(r["snapshot_json"] == original_history_json for r in facts.document["tables"]["personal_revision"])


def test_reference_snapshot_semantics_and_derived_status_do_not_rewrite_original_bytes(source_env):
    env, con = source_env, source_env["con"]
    original_snapshot = env["ref_snapshot"]
    formatted = json.dumps(json.loads(original_snapshot), ensure_ascii=False, indent=4)
    con.execute("UPDATE personal_evidence_ref SET snapshot_json=? WHERE ref_id=?", (formatted, env["ids"]["ref"]))
    con.commit()
    document = take(env).snapshot.document
    assert document["tables"]["personal_evidence_ref"][0]["snapshot_json"] == formatted
    history = next(r for r in document["tables"]["personal_revision"] if r["entity_kind"] == "card")
    saved = json.loads(history["snapshot_json"])
    saved["evidence_refs"][0].update(current_snapshot={"changed": "derived only"}, status="missing", binding_status="stale")
    history["snapshot_json"] = json.dumps(saved, ensure_ascii=False, indent=3)
    facts = contract.validate_snapshot(encoded(document))
    assert next(r["snapshot_json"] for r in facts.document["tables"]["personal_revision"]
                if r["entity_kind"] == "card") == history["snapshot_json"]
    assert facts.document["tables"]["personal_evidence_ref"][0]["snapshot_json"] == formatted


def test_real_personal_api_enriched_atom_refs_survive_confirmation_and_current_knowledge_loss(tmp_path, monkeypatch):
    from reno import db
    root = tmp_path / "actual-personal-api"
    data, media = root / "data", root / "media"
    root.mkdir()
    local = root / "config.synthetic.json"
    local.write_text(encoded({"node_role": "home", "personal_features_enabled": True}), encoding="utf-8")
    monkeypatch.setenv("RENO_CONFIG_FILE", str(local))
    monkeypatch.setenv("RENO_DATA_DIR", str(data))
    monkeypatch.setattr(config, "_cache", None)
    for name, path in {"DATA": data, "MEDIA": media, "ORIG": media / "originals", "AUDIO": media / "audio",
                       "FRAMES": media / "frames", "FRAME_CACHE": root / "frame-cache"}.items():
        monkeypatch.setattr(config, name, path)
    db.initialize()
    profile = storage_admin.profile()
    lifecycle.initialize(profile)
    con = db.connect()
    try:
        metadata = dict(con.execute("SELECT key,value FROM meta WHERE key IN ('library_id','personal_primary_id','primary_epoch','default_project_id')"))
        with lifecycle.shared(profile):
            con.execute("INSERT INTO video_asset(video_id,title,content_sha256,source_url,source_published_at,files_json) "
                        "VALUES('BVmirror-real-api','增补视频标题','synthetic-mirror-content','https://example.invalid/mirror-origin','2026-09-01','{}')")
            con.execute("INSERT INTO knowledge_atom(id,video_id,claim,conditions_json,parameters_json,evidence_nature,exceptions) "
                        "VALUES('mirror-real-api-atom','BVmirror-real-api','核对基层条件','{}','[]','author_opinion','[]')")
            con.commit()
        base_snapshot, base_fingerprint = identity.load_target(con, "atom", "mirror-real-api-atom")
    finally:
        con.close()
    epoch, project = int(metadata["primary_epoch"]), metadata["default_project_id"]
    reference = {"target_kind": "atom", "target_id": "mirror-real-api-atom", "target_fingerprint": base_fingerprint}
    body, status = personal.create_card(project, {"operation_id": uid(1001), "primary_epoch": epoch,
                                                "changes": {"title": "真实个人接口保存", "evidence_refs": [reference]}})
    assert status == 201
    card_id = body["card"]["card_id"]
    con = db.connect()
    try:
        original_ref = dict(con.execute("SELECT * FROM personal_evidence_ref WHERE owner_kind='card' AND owner_id=?", (card_id,)).fetchone())
        original_history = con.execute("SELECT snapshot_json FROM personal_revision WHERE entity_kind='card' AND entity_id=? AND revision=1", (card_id,)).fetchone()[0]
    finally:
        con.close()
    enriched = json.loads(original_ref["snapshot_json"])
    assert enriched["source_url"] == "https://example.invalid/mirror-origin"
    assert enriched["video_title"] == "增补视频标题"
    assert enriched["source_published_at"] == "2026-09-01"
    assert original_ref["target_fingerprint"] == base_fingerprint == identity.fingerprint(base_snapshot)
    assert identity.fingerprint(enriched) != base_fingerprint
    def actual_capture(snapshot_id, prior=None):
        return capture.capture_snapshot(snapshot_id=snapshot_id, expected_library_id=metadata["library_id"],
                                         expected_primary_id=metadata["personal_primary_id"], expected_epoch=epoch,
                                         prior_snapshot_json=prior).snapshot
    first = actual_capture(uid(301))
    confirmed, status = personal.update_card_evidence(card_id, {"operation_id": uid(1002), "primary_epoch": epoch,
                    "base_revision": 1, "changes": {"confirm": True, "evidence_refs": [reference]}})
    assert status == 200 and confirmed["card"]["revision"] == 2
    second = actual_capture(uid(302), first.snapshot_bytes)
    new_ref = second.document["tables"]["personal_evidence_ref"][0]
    assert new_ref["ref_id"] != original_ref["ref_id"]
    assert new_ref["snapshot_json"] == original_ref["snapshot_json"]
    assert new_ref["target_fingerprint"] == base_fingerprint
    assert any(r["snapshot_json"] == original_history for r in second.document["tables"]["personal_revision"])
    con = db.connect()
    try:
        with lifecycle.shared(profile):
            con.execute("UPDATE video_asset SET sync_state='staging' WHERE video_id='BVmirror-real-api'")
            con.commit()
    finally:
        con.close()
    unready = actual_capture(uid(303), second.snapshot_bytes)
    assert unready.document["tables"]["personal_evidence_ref"][0]["snapshot_json"] == original_ref["snapshot_json"]
    con = db.connect()
    try:
        with lifecycle.shared(profile):
            con.execute("DELETE FROM knowledge_atom WHERE id='mirror-real-api-atom'")
            con.commit()
    finally:
        con.close()
    changed, status = personal.update_card(card_id, {"operation_id": uid(1003), "primary_epoch": epoch,
                     "base_revision": 2, "changes": {"decision_reason": "当前知识缺失仍保留已保存依据"}})
    assert status == 200 and changed["card"]["evidence_refs"][0]["binding_status"] == "missing"
    missing = actual_capture(uid(304), unready.snapshot_bytes)
    retained = missing.document["tables"]["personal_evidence_ref"][0]
    assert retained["snapshot_json"] == original_ref["snapshot_json"] and retained["target_fingerprint"] == base_fingerprint
    assert any(r["snapshot_json"] == original_history for r in missing.document["tables"]["personal_revision"])


def test_snapshot_uuid_cannot_be_reused_for_different_outer_bytes(source_env):
    facts = take(source_env).snapshot
    pretty = json.dumps(facts.document, indent=1, ensure_ascii=False)
    assert_code("SNAPSHOT_ID_REUSED", lambda: contract.validate_snapshot(pretty, prior_snapshot_json=facts.snapshot_bytes))


@pytest.mark.parametrize("mode,code", [
    ("cloud", "PERSONAL_CAPTURE_ROLE_DISABLED"), ("disabled", "PERSONAL_CAPTURE_DISABLED"),
    ("replica", "PERSONAL_CAPTURE_READ_ONLY_REPLICA"), ("missing_node", "PERSONAL_CAPTURE_NODE_IDENTITY_UNSAFE"),
    ("epoch", "PERSONAL_CAPTURE_PRIMARY_CHANGED"),
])
def test_capture_rejects_cloud_disabled_nonprimary_and_changed_epoch(source_env, mode, code, monkeypatch):
    if mode in {"cloud", "disabled"}:
        config.set_local({"node_role": "cloud" if mode == "cloud" else "home",
                          "personal_features_enabled": mode != "disabled"})
        def forbidden(*_args, **_kwargs):
            raise AssertionError("Disabled capture must reject before opening a committed source image")
        monkeypatch.setattr(readonly, "committed_image_locked", forbidden)
    elif mode == "replica":
        source_env["node"].write_text(encoded({"instance_id": uid(999)}), encoding="utf-8")
    elif mode == "missing_node":
        source_env["node"].unlink()
    else:
        source_env["con"].execute("UPDATE meta SET value='2' WHERE key='primary_epoch'")
        source_env["con"].commit()
    assert_code(code, lambda: take(source_env))


def test_ordinary_table_descriptor_rejects_extra_column_without_projection_loss(source_env):
    source_env["con"].execute("ALTER TABLE decision_card ADD COLUMN future_private TEXT")
    source_env["con"].commit()
    assert_code("SNAPSHOT_SCHEMA_DRIFT", lambda: take(source_env))


def test_missing_lifecycle_facilities_are_not_created_by_capture(source_env):
    source_env["profile"].media.joinpath(lifecycle.LOCK_NAME).unlink()
    with pytest.raises(lifecycle.StorageLifecycleError) as caught:
        take(source_env)
    assert caught.value.code == "STORAGE_NOT_INITIALIZED"
    assert not source_env["profile"].media.joinpath(lifecycle.LOCK_NAME).exists()


def test_only_ready_requires_a_body_and_failed_rows_are_preserved(source_env):
    env, con = source_env, source_env["con"]
    con.execute("DELETE FROM source_attachment WHERE state='ready'")
    con.execute("DELETE FROM mutation_receipt WHERE operation_id=?", (env["operations"]["attachment"],))
    con.execute("UPDATE checklist_check SET attachment_refs_json='[]'")
    receipt = con.execute("SELECT response_json FROM mutation_receipt WHERE operation_id=?", (env["operations"]["check"],)).fetchone()[0]
    response = json.loads(receipt)
    response["check"]["attachment_refs"] = []
    con.execute("UPDATE mutation_receipt SET response_json=? WHERE operation_id=?", (encoded(response), env["operations"]["check"]))
    con.commit()
    env["body"].unlink()
    env["body"].parent.rmdir()
    env["body"].parent.parent.rmdir()
    result = take(env)
    rows = result.snapshot.document["tables"]["source_attachment"]
    assert rows[0]["state"] == "failed" and rows[0]["error"] == "原失败记录"
    assert result.snapshot.document["attachment_manifest"] == []
    assert result.observations["attachment_root_identity"] is None
    assert not (env["data"] / "personal-attachments").exists()


def test_committed_wal_image_excludes_uncommitted_personal_update(source_env):
    con = source_env["con"]
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("BEGIN IMMEDIATE")
    con.execute("UPDATE decision_card SET title='未提交的草稿'")
    try:
        facts = take(source_env).snapshot
        assert facts.document["tables"]["decision_card"][0]["title"] == "待核对"
        assert con.in_transaction
    finally:
        con.rollback()


def test_existing_reader_strict_source_change_is_preserved_and_retriable(source_env, monkeypatch):
    original = capture._HeldFile.digest
    def commit_knowledge_after_body(self, expected_size, check):
        result = original(self, expected_size, check)
        source_env["con"].execute("UPDATE meta SET value='92' WHERE key='kb_revision'")
        source_env["con"].commit()
        return result
    monkeypatch.setattr(capture._HeldFile, "digest", commit_knowledge_after_body)
    with pytest.raises(readonly.ReadonlyImageError) as caught:
        take(source_env)
    assert caught.value.code == "SOURCE_CHANGED" and caught.value.retriable


@pytest.mark.skipif(os.name == "nt", reason="Windows held read handles deny external writes; POSIX requires exit recheck")
def test_actual_attachment_change_after_last_explicit_verify_is_caught_on_held_handle_exit(source_env, monkeypatch):
    original = capture._admission
    calls = 0
    def change_during_final_admission():
        nonlocal calls
        result = original()
        calls += 1
        if calls == 2:
            source_env["body"].write_bytes(BODY + b"modified after the explicit file verify")
        return result
    monkeypatch.setattr(capture, "_admission", change_during_final_admission)
    caught = assert_code("PERSONAL_CAPTURE_FILE_CHANGED", lambda: take(source_env))
    assert caught.retriable
    assert source_env["body"].read_bytes() != BODY


@pytest.mark.skipif(os.name != "nt", reason="Windows no-follow reader uses mandatory write/delete sharing protection")
def test_windows_actual_held_attachment_handle_denies_final_external_write(source_env, monkeypatch):
    original = capture._admission
    attempts = []
    def try_write_during_final_admission():
        result = original()
        if attempts:
            with pytest.raises(OSError):
                source_env["body"].write_bytes(BODY + b"attempted final mutation")
            attempts.append("denied")
        else:
            attempts.append("initial")
        return result
    monkeypatch.setattr(capture, "_admission", try_write_during_final_admission)
    result = take(source_env)
    assert attempts == ["initial", "denied"]
    assert result.snapshot.document["attachment_manifest"][0]["sha256"] == hashlib.sha256(BODY).hexdigest()
    assert source_env["body"].read_bytes() == BODY


@pytest.mark.skipif(os.name == "nt", reason="POSIX permits the actual tamper that must not replace a primary cancellation")
def test_exit_verification_does_not_mask_original_cancellation_after_file_change(source_env, monkeypatch):
    class Cancelled(Exception):
        pass
    original = capture._admission
    calls, cancelled = 0, Cancelled("original cancellation")
    def cancel_during_final_admission():
        nonlocal calls
        result = original()
        calls += 1
        if calls == 2:
            source_env["body"].write_bytes(BODY + b"tampered")
            raise cancelled
        return result
    monkeypatch.setattr(capture, "_admission", cancel_during_final_admission)
    with pytest.raises(Cancelled) as caught:
        take(source_env)
    assert caught.value is cancelled


def test_sql_progress_callback_keeps_original_cancellation(source_env):
    class Cancelled(Exception):
        pass
    cancelled = Cancelled("cancelled during actual SQLite progress")
    con = source_env["con"]
    # This actual raw scan is large enough to run the SQLite progress handler.
    # History validation is intentionally never reached after cancellation.
    con.executemany("INSERT INTO renovation_project(project_id,name,created_at,updated_at) VALUES(?,?,?,?)",
                    [(uid(1000 + i), "取消测试项目", NOW, NOW) for i in range(1000)])
    con.commit()
    def stop_in_sql_progress():
        if sys._getframe(1).f_code.co_name == "budget":
            raise cancelled
    with pytest.raises(Cancelled) as caught:
        take(source_env, check_cancelled=stop_in_sql_progress)
    assert caught.value is cancelled


def test_cancellation_is_not_translated_to_filesystem_or_contract_error(source_env):
    class Cancelled(Exception):
        pass
    def stop():
        raise Cancelled("cancelled before source access")
    with pytest.raises(Cancelled):
        take(source_env, check_cancelled=stop)


def test_duplicate_json_key_and_nonfinite_inner_json_are_rejected(source_env):
    facts = take(source_env).snapshot
    duplicate = facts.snapshot_json.replace('"format_version":1', '"format_version":1,"format_version":1', 1)
    assert_code("SNAPSHOT_DUPLICATE_JSON_KEY", lambda: contract.validate_snapshot(duplicate))
    document = facts.document
    document["tables"]["decision_card"][0]["context_json"] = '{"number": 1e999}'
    assert_code("SNAPSHOT_INVALID_JSON", lambda: contract.validate_snapshot(encoded(document)))
