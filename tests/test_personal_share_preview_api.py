"""实际家庭 SQL 与生产安装云镜像的只读预览；不创建真实分享或凭据。"""
from contextlib import contextmanager
import hashlib
import json
import uuid

from fastapi import Request
import pytest

from app.personal_read_api import VERSION_HEADER
from reno import config, db, personal, personal_mirror_read as reader, storage_admin
from reno import personal_share_contract as contract, personal_share_preview as preview
from reno import storage_lifecycle as lifecycle
from tests.test_personal_projects import personal_env, envelope, create, add_atom  # noqa: F401
from tests.test_personal_cloud_read_api import cloud_read_env, client, assert_private  # noqa: F401
from tests.test_personal_mirror_transfer import mirror_env, table_state  # noqa: F401
from tests.test_personal_mirror_read import install


def path(project_id):
    return f"/api/projects/{project_id}/decision-cards/share-preview"


def selection(*cards):
    return {"selection": [{"card_id": card["card_id"], "revision": card["revision"]} for card in cards]}


def local_state():
    con = db.connect()
    try:
        return {table: [tuple(row) for row in con.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]
                for table in ("renovation_project", "decision_card", "personal_revision", "mutation_receipt", "meta")}
    finally:
        con.close()


def assert_preview(response, expected, *, version=None):
    assert response.status_code == 200, response.text
    assert_private(response, version)
    answer = response.json()
    assert set(answer) == {"snapshot", "snapshot_sha256", "personal_read_version"}
    assert answer["personal_read_version"] == version
    assert answer["snapshot"] == contract.project_snapshot(expected)
    raw = contract.encode_snapshot(answer["snapshot"])
    assert answer["snapshot_sha256"] == hashlib.sha256(raw).hexdigest()
    return answer


@pytest.mark.parametrize("private_column", [
    "context_json", "alternatives_json", "open_questions_json", "content_snapshot_json",
])
def test_selected_summary_does_not_parse_unrelated_private_card_json(personal_env, private_column):
    card = create(personal_env, title="只读取健康的摘要", decision_text="原决定正文")
    con = db.connect()
    try:
        con.execute(f'UPDATE decision_card SET "{private_column}"=? WHERE card_id=?',
            ("{invalid-unrelated-private-json", card["card_id"]))
        con.commit()
    finally:
        con.close()
    before = local_state()
    response = personal_env["client"].post(path(personal_env["project"]["project_id"]), json=selection(card))
    assert_preview(response, [card])
    assert local_state() == before


def test_selected_summary_does_not_parse_saved_private_evidence_snapshot(personal_env):
    ref = add_atom()
    card = create(personal_env, evidence_refs=[ref])
    con = db.connect()
    try:
        con.execute("UPDATE personal_evidence_ref SET snapshot_json=? WHERE owner_kind='card' AND owner_id=?",
            ("{invalid-unrelated-private-snapshot", card["card_id"]))
        con.commit()
    finally:
        con.close()
    before = local_state()
    response = personal_env["client"].post(path(personal_env["project"]["project_id"]), json=selection(card))
    assert_preview(response, [card])
    assert local_state() == before














@pytest.mark.parametrize("binding_status", ["active", "stale", "missing", "legacy_unbound"])
def test_selected_summary_keeps_original_reference_recheck_semantics(personal_env, binding_status):
    ref = add_atom()
    card = create(personal_env, evidence_refs=[ref])
    con = db.connect()
    try:
        if binding_status == "stale":
            con.execute("UPDATE knowledge_atom SET claim='原依据已改变' WHERE id=?", (ref["target_id"],))
        elif binding_status == "missing":
            con.execute("DELETE FROM evidence_ref WHERE atom_id=?", (ref["target_id"],))
            con.execute("DELETE FROM knowledge_atom WHERE id=?", (ref["target_id"],))
        elif binding_status == "legacy_unbound":
            con.execute("UPDATE personal_evidence_ref SET target_fingerprint=NULL WHERE owner_id=?", (card["card_id"],))
        con.commit()
        expected = personal.card_view(con, card["card_id"])
        assert expected["evidence_refs"][0]["binding_status"] == binding_status
    finally:
        con.close()
    before = local_state()
    response = personal_env["client"].post(path(personal_env["project"]["project_id"]), json=selection(card))
    answer = assert_preview(response, [expected])
    assert answer["snapshot"]["decisions"][0]["needs_recheck"] is (binding_status != "active")
    assert local_state() == before


def test_stale_reference_does_not_hide_a_later_target_error(personal_env, monkeypatch):
    ref = add_atom()
    project_id = personal_env["project"]["project_id"]
    source = personal.create_source(project_id, envelope({"source_type": "manual_note", "title": "第二条实际依据"}))[0]["source"]
    card = create(personal_env, evidence_refs=[ref, {"target_kind": "source", "target_id": source["source_id"],
        "target_fingerprint": source["target_fingerprint"]}])
    con = db.connect()
    try:
        con.execute("UPDATE knowledge_atom SET claim='第一条依据已经过期' WHERE id=?", (ref["target_id"],))
        con.execute("UPDATE personal_evidence_ref SET created_at=? WHERE owner_id=? AND target_kind='atom'",
            ("2026-10-01T00:00:00.000Z", card["card_id"]))
        con.execute("UPDATE personal_evidence_ref SET created_at=?,target_kind='invalid-kind' WHERE owner_id=? AND target_kind='source'",
            ("2026-10-01T00:00:01.000Z", card["card_id"]))
        con.commit()
    finally:
        con.close()
    original, resolved = personal.resolve_target, []
    def actual_resolve(con, project_id, kind, target_id):
        resolved.append(target_id)
        return original(con, project_id, kind, target_id)
    monkeypatch.setattr(personal, "resolve_target", actual_resolve)
    before = local_state()
    response = personal_env["client"].post(path(project_id), json=selection(card))
    assert response.status_code == 400 and response.json()["code"] == "INVALID_REQUEST"
    assert "snapshot" not in response.json()
    assert_private(response)
    assert resolved == [ref["target_id"], source["source_id"]]
    assert local_state() == before


def test_legacy_reference_still_resolves_the_target_before_recheck(personal_env, monkeypatch):
    ref = add_atom()
    card = create(personal_env, evidence_refs=[ref])
    con = db.connect()
    try:
        con.execute("UPDATE personal_evidence_ref SET target_fingerprint=NULL,target_kind='invalid-kind' WHERE owner_id=?",
            (card["card_id"],))
        con.commit()
    finally:
        con.close()
    original, resolved = personal.resolve_target, []
    def actual_resolve(con, project_id, kind, target_id):
        resolved.append(target_id)
        return original(con, project_id, kind, target_id)
    monkeypatch.setattr(personal, "resolve_target", actual_resolve)
    response = personal_env["client"].post(path(personal_env["project"]["project_id"]), json=selection(card))
    assert response.status_code == 400 and response.json()["code"] == "INVALID_REQUEST"
    assert "snapshot" not in response.json()
    assert_private(response)
    assert resolved == [ref["target_id"]]


def test_reference_project_is_not_replaced_with_the_selected_cards_project(personal_env):
    project_id = personal_env["project"]["project_id"]
    source = personal.create_source(project_id, envelope({"source_type": "manual_note", "title": "本项目的原依据"}))[0]["source"]
    card = create(personal_env, evidence_refs=[{"target_kind": "source", "target_id": source["source_id"],
        "target_fingerprint": source["target_fingerprint"]}])
    other = str(uuid.uuid4())
    con = db.connect()
    try:
        con.execute("INSERT INTO renovation_project(project_id,name,created_at,updated_at) VALUES(?,?,?,?)",
            (other, "另一合成项目", "2026-10-01T00:00:00.000Z", "2026-10-01T00:00:00.000Z"))
        con.execute("UPDATE personal_evidence_ref SET project_id=? WHERE owner_id=?", (other, card["card_id"]))
        con.commit()
    finally:
        con.close()
    before = local_state()
    response = personal_env["client"].post(path(project_id), json=selection(card))
    assert response.status_code == 400 and response.json()["code"] == "INVALID_REQUEST"
    assert "snapshot" not in response.json()
    assert_private(response)
    assert local_state() == before


def test_selected_summary_fields_still_obey_the_closed_snapshot_contract(personal_env):
    card = create(personal_env)
    con = db.connect()
    try:
        con.execute("UPDATE decision_card SET title=? WHERE card_id=?", (" " * 3, card["card_id"]))
        con.commit()
    finally:
        con.close()
    response = personal_env["client"].post(path(personal_env["project"]["project_id"]), json=selection(card))
    assert response.status_code == 409 and response.json()["code"] == "SHARE_SELECTION_CHANGED"
    assert "snapshot" not in response.json()
    assert_private(response)


def test_summary_sql_reads_fixed_fields_without_project_card_or_reference_private_payloads(personal_env, monkeypatch):
    ref = add_atom()
    card = create(personal_env, evidence_refs=[ref])
    original, statements = preview.snapshot_locked, []
    def observed_read(con, project_id, data):
        assert con.in_transaction and con.execute("PRAGMA query_only").fetchone()[0] == 1
        assert lifecycle.current_guard() is not None
        con.set_trace_callback(statements.append)
        try:
            return original(con, project_id, data)
        finally:
            con.set_trace_callback(None)
    monkeypatch.setattr(preview, "snapshot_locked", observed_read)
    response = personal_env["client"].post(path(personal_env["project"]["project_id"]), json=selection(card))
    assert_preview(response, [card])
    private_columns = ("profile_json", "context_json", "alternatives_json", "open_questions_json",
        "content_snapshot_json", "snapshot_json")
    targeted = [sql.lower() for sql in statements if any("from " + table in sql.lower()
        for table in ("renovation_project", "decision_card", "personal_evidence_ref"))]
    assert len(targeted) >= 4
    assert all("select *" not in sql and not any(column in sql for column in private_columns) for sql in targeted)


@pytest.mark.parametrize("role", ["all", "home"])
def test_actual_local_read_orders_selected_summaries_and_excludes_private_fields(personal_env, monkeypatch, role):
    config.set_local({"node_role": role})
    option = str(uuid.uuid4())
    first = create(personal_env, title="原文 <b>只作文本</b>", decision_reason="保留 **理由**",
        question="excluded-question", context_snapshot={"private": "excluded-condition"},
        content_snapshot={"kind": "note", "text": "excluded-content"},
        alternatives=[{"option_id": option, "label": "excluded-candidate-label"}],
        selected_option_id=option, state="adopted", decision_text="")
    second = create(personal_env, title="后一个决定", decision_text="如实保留个人正文")
    before, seen = local_state(), []
    original = preview._card_summary

    def actual_view(con, card_id):
        assert con.in_transaction and con.execute("PRAGMA query_only").fetchone()[0] == 1
        assert lifecycle.current_guard() is not None
        seen.append(con)
        return original(con, card_id)

    monkeypatch.setattr(preview, "_card_summary", actual_view)
    response = personal_env["client"].post(path(personal_env["project"]["project_id"]), json=selection(second, first))
    answer = assert_preview(response, [second, first])
    assert len(seen) == 2 and seen[0] is seen[1]
    assert answer["snapshot"]["decisions"][1]["decision_text"] == ""
    for private in ("excluded-question", "excluded-condition", "excluded-content", "excluded-candidate-label",
                    first["card_id"], personal_env["project"]["project_id"], option):
        assert private not in response.text
    assert VERSION_HEADER not in response.headers
    assert local_state() == before


def test_invalid_closed_selection_never_returns_a_partial_snapshot(personal_env):
    card = create(personal_env)
    valid = selection(card)
    item = valid["selection"][0]
    invalid = [None, {}, {**valid, "extra": "private"}, {"selection": []},
        {"selection": [item] * 51}, {"selection": [item, item]},
        {"selection": [{**item, "unknown": "private"}]}, {"selection": [{"card_id": card["card_id"]}]},
        {"selection": [{**item, "card_id": card["card_id"].replace("-", "")}]},
        {"selection": [{**item, "card_id": "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"}]},
        {"selection": [{**item, "card_id": True}]},
        {"selection": [{**item, "revision": True}]}, {"selection": [{**item, "revision": 0}]},
        {"selection": [{**item, "revision": 1 << 63}]}, {"selection": [{**item, "revision": 1.0}]}]
    before = local_state()
    http = personal_env["client"]
    for data in invalid:
        response = http.post(path(personal_env["project"]["project_id"]), json=data)
        assert response.status_code == 400 and response.json()["code"] == "INVALID_REQUEST"
        assert_private(response)
        assert "snapshot" not in response.json()
    raw = json.dumps(valid).encode()
    duplicated = b'{"selection":[],"selection":' + json.dumps(valid["selection"]).encode() + b'}'
    for malformed in (duplicated, raw.replace(b'"revision": 1', b'"revision": 1,"revision": 1'),
                      b"\xff", b"{" + b" " * (256 * 1024)):
        response = http.post(path(personal_env["project"]["project_id"]), content=malformed)
        assert response.status_code in {400, 413}
        assert "snapshot" not in response.json()
        assert_private(response)
    assert local_state() == before


def test_missing_cross_project_and_changed_revision_are_one_selection_conflict(personal_env, monkeypatch):
    first, second = create(personal_env, title="仍合法的第一项"), create(personal_env, title="后来修改的第二项")
    personal.update_card(second["card_id"], envelope({"decision_reason": "后来已提交"}, base=1))
    other = str(uuid.uuid4())
    con = db.connect()
    try:
        con.execute("INSERT INTO renovation_project(project_id,name,created_at,updated_at) VALUES(?,?,?,?)",
            (other, "另一个合成项目", "2026-10-06T00:00:00.000Z", "2026-10-06T00:00:00.000Z"))
        con.commit()
    finally:
        con.close()
    foreign = personal.create_card(other, envelope({"title": "不可跨项目选择"}))[0]["card"]
    con = db.connect()
    try:
        # Damaged private JSON outside this project's/current-revision selection
        # must remain untouched and must never outrank the selection conflict.
        con.execute("UPDATE decision_card SET context_json=? WHERE card_id IN (?,?)",
            ("{invalid-private-json", second["card_id"], foreign["card_id"]))
        con.commit()
    finally:
        con.close()
    original, viewed = preview._card_summary, []
    def only_admitted_view(con, card_id):
        assert card_id not in {second["card_id"], foreign["card_id"]}
        viewed.append(card_id)
        return original(con, card_id)
    monkeypatch.setattr(preview, "_card_summary", only_admitted_view)
    before = local_state()
    missing = {**first, "card_id": str(uuid.uuid4())}
    for selected in (selection(first, second), selection(first, foreign), selection(first, missing)):
        response = personal_env["client"].post(path(personal_env["project"]["project_id"]), json=selected)
        assert response.status_code == 409 and response.json()["code"] == "SHARE_SELECTION_CHANGED"
        assert set(response.json()) == {"detail", "code", "personal_read_version"}
        assert_private(response)
        assert first["title"] not in response.text
    response = personal_env["client"].post(path(str(uuid.uuid4())), json=selection(first))
    assert response.status_code == 409 and response.json()["code"] == "SHARE_SELECTION_CHANGED"
    assert viewed == [first["card_id"]] * 3
    assert local_state() == before


def test_existing_token_gate_and_disabled_switch_reject_before_body_or_factory(personal_env, monkeypatch):
    project_id = personal_env["project"]["project_id"]
    config.set_local({"auth_token": "synthetic-preview-token"})
    with monkeypatch.context() as patch:
        def forbidden(*args, **kwargs):
            pytest.fail("denied preview reached body or read factory")
        async def forbidden_stream(*args, **kwargs):
            pytest.fail("denied preview consumed request body")
            yield b""
        patch.setattr(Request, "stream", forbidden_stream)
        patch.setattr(storage_admin, "profile", forbidden)
        response = personal_env["client"].post(path(project_id), content=b"unparsed")
        assert response.status_code == 401
        headers = {"X-Reno-Token": "synthetic-preview-token"}
        config.set_local({"personal_features_enabled": False})
        response = personal_env["client"].post(path(project_id), content=b"unparsed", headers=headers)
        assert response.status_code == 403 and response.json()["code"] == "CAPABILITY_DISABLED"
        assert_private(response)
        config.set_local({"node_role": "cloud", "personal_cloud_read_enabled": False})
        response = personal_env["client"].post(path(project_id), content=b"unparsed", headers=headers)
        assert response.status_code == 403 and response.json()["code"] == "PERSONAL_MIRROR_READ_DISABLED"
        assert_private(response)


def test_callback_error_keeps_original_code_and_never_finalizes_snapshot(personal_env, monkeypatch):
    card = create(personal_env)
    original = preview._card_summary
    def failed_read(con, card_id):
        original(con, card_id)
        raise personal.PersonalError("合成真实读取之后的失败", "PREVIEW_SOURCE_FAILED", 503)
    def forbidden(*args, **kwargs):
        pytest.fail("failed actual read cannot freeze or publish a snapshot")
    monkeypatch.setattr(preview, "_card_summary", failed_read)
    monkeypatch.setattr(preview, "finalize_snapshot", forbidden)
    response = personal_env["client"].post(path(personal_env["project"]["project_id"]), json=selection(card))
    assert response.status_code == 503 and response.json()["code"] == "PREVIEW_SOURCE_FAILED"
    assert "snapshot" not in response.json()
    assert_private(response)


def test_actual_cloud_installs_versions_and_final_exit_never_publish_a_stale_preview(cloud_read_env, monkeypatch):
    env = cloud_read_env
    with client() as http:
        cap = http.get("/api/capabilities").json()
        first_version = cap["personal_read_version"]
        card = http.get(f"/api/decision-cards/{env['card_id']}").json()["card"]
        before = table_state(env)
        response = http.post(path(env["project_id"]), json=selection(card), headers={VERSION_HEADER: first_version})
        assert_preview(response, [card], version=first_version)
        assert table_state(env) == before

    env["select"](env["source_root"], "home")
    personal.update_card(env["card_id"], envelope({"decision_reason": "新镜像的实际家庭修订"},
        base=card["revision"], epoch=env["primary_epoch"]))
    previous = {key: cap["personal_mirror"][key] for key in ("snapshot_id", "snapshot_sha256")}
    install(env, previous=previous, expected_revision=1)
    config.set_local({"personal_cloud_read_enabled": True})
    before = table_state(env)
    with client() as http:
        changed = http.post(path(env["project_id"]), json=selection(card), headers={VERSION_HEADER: first_version})
        assert changed.status_code == 409 and changed.json()["code"] == "PERSONAL_MIRROR_READ_VERSION_CHANGED"
        assert_private(changed)
        assert "snapshot" not in changed.json()
        current = http.get(f"/api/decision-cards/{env['card_id']}").json()
        current_version, current_card = current["personal_read_version"], current["card"]
        assert current_version != first_version
        stale = http.post(path(env["project_id"]), json=selection(card), headers={VERSION_HEADER: current_version})
        assert stale.status_code == 409 and stale.json()["code"] == "SHARE_SELECTION_CHANGED"
        assert_private(stale)
        assert "snapshot" not in stale.json()
        fresh = http.post(path(env["project_id"]), json=selection(current_card), headers={VERSION_HEADER: current_version})
        assert_preview(fresh, [current_card], version=current_version)
    assert table_state(env) == before

    original = reader.open_installed_read
    @contextmanager
    def failed_exit(*args, **kwargs):
        with original(*args, **kwargs) as con:
            yield con
        raise reader.PersonalMirrorReadError("PROOF_CHANGED")
    def forbidden(*args, **kwargs):
        pytest.fail("failed actual factory EXIT cannot finalize or publish snapshot")
    with monkeypatch.context() as patch:
        patch.setattr(reader, "open_installed_read", failed_exit)
        patch.setattr(preview, "finalize_snapshot", forbidden)
        with client() as http:
            response = http.post(path(env["project_id"]), json=selection(current_card),
                headers={VERSION_HEADER: current_version})
            assert response.status_code == 409 and response.json()["code"] == "PERSONAL_MIRROR_READ_PROOF_CHANGED"
            assert "snapshot" not in response.json()
            assert_private(response)
    assert table_state(env) == before


def test_cloud_without_installed_head_never_returns_a_preview(mirror_env):
    config.set_local({"personal_cloud_read_enabled": True})
    with client() as http:
        response = http.post(path(mirror_env["project_id"]), json={"selection": [
            {"card_id": str(uuid.uuid4()), "revision": 1}]})
    assert response.status_code == 403 and response.json()["code"] == "PERSONAL_MIRROR_READ_NOT_INSTALLED"
    assert "snapshot" not in response.json()
    assert_private(response)


def test_cloud_writer_gate_still_rejects_original_routes_before_body(personal_env, monkeypatch):
    config.set_local({"node_role": "cloud"})
    def forbidden(*args, **kwargs):
        pytest.fail("cloud writer reached body or SQL factory")
    with monkeypatch.context() as patch:
        async def forbidden_stream(*args, **kwargs):
            pytest.fail("cloud writer consumed request body")
            yield b""
        patch.setattr(Request, "stream", forbidden_stream)
        patch.setattr(storage_admin, "profile", forbidden)
        for route in (f"/api/projects/{personal_env['project']['project_id']}/decision-cards",
                      f"/api/projects/{personal_env['project']['project_id']}/sources",
                      f"/api/decision-cards/{uuid.uuid4()}/evidence"):
            response = personal_env["client"].post(route, content=b"unparsed")
            assert response.status_code == 403 and response.json()["code"] == "PERSONAL_CLOUD_READ_ONLY"
            assert_private(response)
