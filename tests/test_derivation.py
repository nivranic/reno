"""Generated relation provenance on disposable libraries; no model/network calls."""
import json
import sqlite3
from copy import deepcopy

import pytest

from reno import config, db, derivation, judge, review


@pytest.fixture()
def con(tmp_path, monkeypatch):
    path = tmp_path / "reno.db"
    for name, value in {"DATA": tmp_path, "MEDIA": tmp_path / "media",
                        "ORIG": tmp_path / "media/originals", "AUDIO": tmp_path / "media/audio",
                        "FRAMES": tmp_path / "media/frames", "FRAME_CACHE": tmp_path / "frames_cache"}.items():
        monkeypatch.setattr(config, name, value)
    monkeypatch.setattr(config, "_cache", {**config.DEFAULTS, "node_role": "all"})
    db.initialize(path)
    from reno import storage_admin
    storage_admin.setup()
    connection = db.connect(path)
    for aid in ("a", "b"):
        connection.execute("INSERT INTO video_asset(video_id,title,status) VALUES(?,?,'processed')", (aid, aid))
        connection.execute("INSERT INTO knowledge_atom(id,video_id,category,space,subject,claim,polarity,conditions_json,parameters_json) "
            "VALUES(?,?,'防水','卫生间','基层','防水施工前检查基层','recommend','{}','[]')", (aid, aid))
        connection.execute("INSERT INTO evidence_ref(atom_id,video_id,modality,source_item_id,start_ms,end_ms,evidence_text) "
            "VALUES(?,?,'asr','seg',0,100,'施工证据')", (aid, aid))
    connection.commit()
    yield connection
    connection.close()


def bound(con):
    return derivation.bind_inputs({a["id"]: a for a in db.all_atoms(con)}, ["a", "b"])


def relations(con, binding=True):
    value = bound(con) if binding else None
    cluster = {"cluster_id": "cluster", "canonical_topic": "基层", "relation": "conflicting",
               "members": ["a", "b"], "linked_conflict": "conflict", "derivation_json": value}
    conflict = {"conflict_id": "conflict", "type": "method_conflict", "members": {
        "side_a": {"atoms": ["a"], "claim": "A", "video": "a"},
        "side_b": {"atoms": ["b"], "claim": "B", "video": "b"}}, "derivation_json": value}
    db.replace_clusters(con, [cluster])
    db.replace_conflicts(con, [conflict])
    con.commit()
    return cluster, conflict


def all_states(con):
    return [dto for rows in derivation.evaluate_relations(con).values() for dto in rows.values()]


def test_unbound_legacy_relations_are_unknown_without_mutating_old_materialized_status(con):
    relations(con, binding=False)
    con.execute("UPDATE knowledge_atom SET status='disputed'")
    con.commit()
    before, changes = db.knowledge_version(con), con.total_changes
    con.execute("PRAGMA query_only=ON")
    assert all(dto == {"state": "unknown", "reason": "unbound", "changed_count": 0, "missing_count": 0}
               for dto in all_states(con))
    atom = review.annotate_atoms(con, db.all_atoms(con))[0]
    assert not atom["has_conflict"] and atom["has_outdated_conflict"] and atom["status"] == "candidate"
    assert con.execute("SELECT status FROM knowledge_atom LIMIT 1").fetchone()[0] == "disputed"
    assert con.total_changes == changes and db.knowledge_version(con) == before and not con.in_transaction


@pytest.mark.parametrize("column,value", [
    ("claim", "新的施工条件"), ("category", "水电"), ("space", "厨房"), ("subject", "新对象"),
    ("conditions_json", '{"substrate":"旧墙"}'), ("parameters_json", '[{"name":"厚度","normalized_value":20}]'),
    ("exceptions", '["潮湿"]'), ("prices_json", '[{"amount":100}]'), ("evidence_nature", "cited_standard"),
])
def test_same_id_semantic_change_stales_both_relations(con, column, value):
    relations(con)
    con.execute(f"UPDATE knowledge_atom SET {column}=? WHERE id='a'", (value,))
    assert all(dto["state"] == "stale" and dto["changed_count"] == 1 for dto in all_states(con))
    atom = review.projection(con)["atoms"]["a"]
    assert not atom["has_conflict"] and atom["has_outdated_conflict"]


def test_evidence_changes_count_but_score_status_model_time_and_unrelated_atoms_do_not(con):
    relations(con)
    con.execute("UPDATE knowledge_atom SET confidence=.99,status='verified',model='other',created_at='new'")
    con.execute("UPDATE evidence_ref SET weight=.1")
    con.execute("INSERT INTO knowledge_atom(id,claim) VALUES('unrelated','新无关知识')")
    assert all(dto["state"] == "active" for dto in all_states(con))
    con.execute("UPDATE evidence_ref SET evidence_text='新证据' WHERE atom_id='a'")
    assert all(dto["state"] == "stale" for dto in all_states(con))


def test_missing_takes_precedence_and_retains_changed_count(con):
    relations(con)
    con.execute("DELETE FROM evidence_ref WHERE atom_id='b'")
    con.execute("DELETE FROM knowledge_atom WHERE id='b'")
    con.execute("UPDATE knowledge_atom SET claim='changed' WHERE id='a'")
    assert all(dto == {"state": "missing", "reason": "members_missing", "changed_count": 1, "missing_count": 1}
               for dto in all_states(con))


def test_exact_member_proof_is_required_and_invalid_data_never_becomes_active(con):
    relations(con)
    con.execute("UPDATE knowledge_cluster SET members_json='[\"a\"]'")
    con.execute("UPDATE conflict_case SET derivation_json='broken'")
    result = derivation.evaluate_relations(con)
    assert result["clusters"]["cluster"]["reason"] == "members_mismatch"
    assert result["conflicts"]["conflict"]["reason"] == "invalid_binding"
    assert all(dto["state"] == "unknown" for dto in all_states(con))


@pytest.mark.parametrize("damage", ["version_bool", "version_unknown", "duplicate", "unsorted", "extra", "bad_hash", "empty", "nested_key"])
def test_binding_shape_is_strict_but_does_not_require_live_content(con, damage):
    value = bound(con)
    if damage == "version_bool": value["version"] = True
    elif damage == "version_unknown": value["version"] = 2
    elif damage == "duplicate": value["inputs"][1] = value["inputs"][0]
    elif damage == "unsorted": value["inputs"].reverse()
    elif damage == "extra": value["unknown"] = 0
    elif damage == "bad_hash": value["inputs"][0]["fingerprint"] = "v1:" + "A" * 64
    elif damage == "empty": value["inputs"] = []
    else: value["inputs"][0]["extra"] = 0
    with pytest.raises(ValueError): derivation.validate_binding(value)
    with pytest.raises(ValueError): derivation.validate_binding(json.dumps(value))
    valid = bound(con)
    con.execute("UPDATE knowledge_atom SET claim='changed'")
    assert derivation.validate_binding(json.dumps(valid)) == valid
    assert derivation.validate_binding(None) is None


def test_duplicate_json_keys_are_rejected(con):
    value = json.dumps(bound(con))
    with pytest.raises(ValueError):
        derivation.validate_binding(value.replace('"version": 1', '"version": 1, "version": 1'))


def test_same_content_replacement_must_install_explicit_new_binding(con):
    clusters, conflicts = relations(con, binding=False)
    for value in (clusters, conflicts): value["derivation_json"] = bound(con)
    db.replace_clusters(con, [clusters])
    db.replace_conflicts(con, [conflicts])
    assert all(dto["state"] == "active" for dto in all_states(con))
    con.commit()
    before = db.knowledge_version(con)
    db.replace_clusters(con, [clusters])
    db.replace_conflicts(con, [conflicts])
    assert db.knowledge_version(con) == before


def test_human_review_of_stale_relation_is_preserved_without_refreshing_binding(con):
    relations(con)
    original = con.execute("SELECT derivation_json FROM conflict_case").fetchone()[0]
    con.execute("UPDATE knowledge_atom SET category='水电' WHERE id='a'")
    event = review.record_decision(con, conflict_id="conflict", action="accept_a")
    review.record_decision(con, atom_id="a", action="confirm")
    projected = review.replay(con)
    assert projected["conflicts"]["conflict"] == "decided:accept_a"
    assert projected["atoms"]["a"]["status"] == "verified"
    assert not projected["atoms"]["a"]["has_conflict"] and projected["atoms"]["a"]["has_outdated_conflict"]
    assert con.execute("SELECT derivation_json FROM conflict_case").fetchone()[0] == original
    assert any(row["event_id"] == event["event_id"] for row in review.review_history(con))
    assert all(dto["state"] == "stale" for dto in all_states(con))


def test_public_summary_applies_cloud_visibility_before_exposing_relations(con, monkeypatch):
    relations(con)
    monkeypatch.setattr(config, "_cache", {**config.DEFAULTS, "node_role": "cloud"})
    con.execute("UPDATE video_asset SET sync_state='pending' WHERE video_id='b'")
    assert derivation.evaluate_relations(con, public=True) == {"clusters": {}, "conflicts": {}}
    assert all(counts == {"active": 0, "stale": 0, "missing": 0, "unknown": 0, "total": 0}
               for counts in derivation.summary(con, public=True).values())
    assert all(counts["active"] == counts["total"] == 1 for counts in derivation.summary(con).values())


def verdicts(candidates, **_kwargs):
    return [{"pair_id": item["pair_id"], "relation": "conflicting", "reason": "synthetic"}
            for item in candidates], "synthetic"


def test_successful_judge_binds_its_actual_inputs_and_old_id_change_stales(con, monkeypatch):
    monkeypatch.setattr(judge, "_judge_llm", verdicts)
    assert judge.run(con)["conflicts"] == 1
    assert all(dto["state"] == "active" for dto in all_states(con))
    original = bound(con)
    assert json.loads(con.execute("SELECT derivation_json FROM conflict_case").fetchone()[0]) == original
    con.execute("UPDATE knowledge_atom SET claim='changed' WHERE id='a'")
    assert all(dto["state"] == "stale" for dto in all_states(con))


@pytest.mark.parametrize("change", ["content", "insert", "delete", "evidence", "selection_field"])
def test_input_change_during_llm_call_fails_cas_and_preserves_old_results(con, monkeypatch, change):
    relations(con)
    before = {table: [tuple(row) for row in con.execute("SELECT * FROM " + table)]
              for table in ("knowledge_cluster", "conflict_case")}
    def changing(candidates, **kwargs):
        path = con.execute("PRAGMA database_list").fetchone()[2]
        with sqlite3.connect(path) as writer:
            if change == "content": writer.execute("UPDATE knowledge_atom SET claim='new' WHERE id='a'")
            elif change == "insert": writer.execute("INSERT INTO knowledge_atom(id,claim) VALUES('new','extra')")
            elif change == "delete": writer.execute("DELETE FROM knowledge_atom WHERE id='a'")
            elif change == "evidence": writer.execute("UPDATE evidence_ref SET start_ms=50 WHERE atom_id='a'")
            else: writer.execute("UPDATE knowledge_atom SET category='new' WHERE id='a'")
        return verdicts(candidates)
    monkeypatch.setattr(judge, "_judge_llm", changing)
    with pytest.raises(judge.JudgeError) as caught:
        judge.run(con)
    assert caught.value.code == "JUDGE_INPUT_CHANGED" and not con.in_transaction
    assert {table: [tuple(row) for row in con.execute("SELECT * FROM " + table)] for table in before} == before


@pytest.mark.parametrize("bad", ["missing", "duplicate", "wrong_pair", "invalid_relation", "failure"])
def test_failed_or_incomplete_judge_never_overwrites_previous_results(con, monkeypatch, bad):
    relations(con)
    before = {table: [tuple(row) for row in con.execute("SELECT * FROM " + table)]
              for table in ("knowledge_cluster", "conflict_case")}
    def invalid(candidates, **kwargs):
        values, model = verdicts(candidates)
        if bad == "failure": raise RuntimeError("synthetic model failure")
        if bad == "missing": values.clear()
        elif bad == "duplicate": values.append(deepcopy(values[0]))
        elif bad == "wrong_pair": values[0]["a"], values[0]["b"] = "foreign", "pair"
        else: values[0]["relation"] = "unsupported"
        return values, model
    monkeypatch.setattr(judge, "_judge_llm", invalid)
    with pytest.raises((judge.JudgeError, RuntimeError)):
        judge.run(con)
    assert {table: [tuple(row) for row in con.execute("SELECT * FROM " + table)] for table in before} == before


def test_review_only_change_during_judge_does_not_invalidate_semantic_input(con, monkeypatch):
    def reviewing(candidates, **kwargs):
        other = db.connect(con.execute("PRAGMA database_list").fetchone()[2])
        try:
            review.record_decision(other, atom_id="a", action="confirm")
            other.commit()
        finally:
            other.close()
        return verdicts(candidates)
    monkeypatch.setattr(judge, "_judge_llm", reviewing)
    judge.run(con)
    assert all(dto["state"] == "active" for dto in all_states(con))
    assert review.projection(con)["atoms"]["a"]["review_status"] == "user_confirmed"


def test_result_write_failure_rolls_back_both_relation_tables_and_bindings(con, monkeypatch):
    relations(con)
    before = {table: [tuple(row) for row in con.execute("SELECT * FROM " + table)]
              for table in ("knowledge_cluster", "conflict_case", "knowledge_atom")}
    version = db.knowledge_version(con)
    monkeypatch.setattr(judge, "_judge_llm", verdicts)
    original = db.replace_conflicts
    def fail(connection, values):
        original(connection, values)
        raise RuntimeError("synthetic failure after replacing both result tables")
    monkeypatch.setattr(db, "replace_conflicts", fail)
    with pytest.raises(RuntimeError, match="synthetic"):
        judge.run(con)
    assert not con.in_transaction and db.knowledge_version(con) == version
    assert {table: [tuple(row) for row in con.execute("SELECT * FROM " + table)] for table in before} == before


def test_judge_refuses_caller_writes_without_committing_or_rolling_them_back(con):
    con.execute("INSERT INTO meta VALUES('caller-sentinel','preserve')")
    with pytest.raises(judge.JudgeError) as caught:
        judge.run(con)
    assert caught.value.code == "JUDGE_TRANSACTION_OPEN" and con.in_transaction
    assert con.execute("SELECT value FROM meta WHERE key='caller-sentinel'").fetchone()[0] == "preserve"
