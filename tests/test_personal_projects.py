"""Personal decisions: single-primary gates, CAS, idempotency and durable sources."""
import concurrent.futures
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from reno import config, db, evidence_identity as identity, personal, personal_schema


def envelope(changes, *, base=None, epoch=1, op=None):
    value = {"operation_id": op or str(uuid.uuid4()), "primary_epoch": epoch, "changes": changes}
    if base is not None:
        value["base_revision"] = base
    return value


@pytest.fixture()
def personal_env(tmp_path, monkeypatch):
    root = tmp_path / "personal"
    for name, path in {"DATA": root / "data", "MEDIA": root / "media", "ORIG": root / "media/originals",
                       "FRAMES": root / "media/frames", "AUDIO": root / "media/audio", "FRAME_CACHE": root / "frames_cache"}.items():
        monkeypatch.setattr(config, name, path)
    monkeypatch.setenv("RENO_DATA_DIR", str(root / "data"))
    config.set_local({"personal_features_enabled": True, "field_checks_enabled": True, "web_ui": "classic",
                      "node_role": "all", "auth_token": ""})
    db.initialize()
    from reno import storage_admin
    storage_admin.setup()
    con = db.connect()
    personal_schema.install(con)
    personal_schema.seed(con)
    con.commit()
    project = personal.default_project(con)["project"]
    con.close()
    from app.main import create_app
    from app.personal_api import router
    app = create_app()
    if not any(getattr(r, "path", "") == "/api/projects/default" for r in app.routes):
        app.include_router(router)
    yield {"project": project, "client": TestClient(app), "root": root}


def add_atom():
    con = db.connect()
    con.execute("INSERT INTO video_asset(video_id,title,content_sha256,source_url,files_json) VALUES('BVpersonal','人工样本','personal-sha','https://www.bilibili.com/video/BVpersonal?token=sample-private&p=1','{}')")
    con.execute("INSERT INTO knowledge_atom(id,video_id,claim,conditions_json,parameters_json,evidence_nature,exceptions) VALUES('personal-atom','BVpersonal','防水应先确认基层条件','{}','[]','author_opinion','[\"木基层不适用\"]')")
    con.execute("INSERT INTO evidence_ref(atom_id,video_id,modality,start_ms,end_ms,evidence_text) VALUES('personal-atom','BVpersonal','asr',1500,3000,'人工证据文本')")
    con.commit()
    fingerprint = identity.load_target(con, "atom", "personal-atom")[1]
    con.close()
    return {"target_kind": "atom", "target_id": "personal-atom", "target_fingerprint": fingerprint}


def create(env, **changes):
    return personal.create_card(env["project"]["project_id"], envelope({"title": "待决定事项", **changes}))[0]["card"]


def test_default_project_seed_is_idempotent_and_reads_do_not_create(personal_env):
    con = db.connect()
    personal_schema.install(con)
    personal_schema.seed(con)
    con.commit()
    assert con.execute("SELECT COUNT(*) FROM renovation_project").fetchone()[0] == 1
    assert personal.default_project(con)["project"]["project_id"] == personal_env["project"]["project_id"]
    con.close()
    config.set_local({"personal_features_enabled": False})
    assert personal_env["client"].get("/api/projects/default").json()["enabled"] is False
    con = db.connect()
    assert con.execute("SELECT COUNT(*) FROM renovation_project").fetchone()[0] == 1
    con.close()


@pytest.mark.parametrize("mode,code", [("disabled", "CAPABILITY_DISABLED"), ("cloud", "PERSONAL_CLOUD_READ_ONLY"), ("replica", "READ_ONLY_REPLICA")])
def test_each_personal_write_gate_is_server_side(personal_env, mode, code):
    project_id = personal_env["project"]["project_id"]
    if mode == "disabled":
        config.set_local({"personal_features_enabled": False})
    elif mode == "cloud":
        config.set_local({"node_role": "cloud"})
    else:
        (config.DATA / "node-identity.json").write_text(json.dumps({"instance_id": str(uuid.uuid4())}), encoding="utf-8")
    paths = [("PATCH", f"/api/projects/{project_id}", {"name": "changed"}),
             ("POST", f"/api/projects/{project_id}/decision-cards", {"title": "decision"}),
             ("POST", f"/api/projects/{project_id}/sources", {"title": "source"}),
             ("PATCH", f"/api/decision-cards/{uuid.uuid4()}", {"title": "x"}),
             ("PATCH", f"/api/sources/{uuid.uuid4()}", {"title": "x"})]
    for method, path, changes in paths:
        response = personal_env["client"].request(method, path, json=envelope(changes, base=1))
        assert response.status_code == 403 and response.json()["code"] == code


def test_old_epoch_rejected_even_if_receipt_exists(personal_env):
    body = envelope({"title": "first"})
    personal.create_card(personal_env["project"]["project_id"], body)
    con = db.connect()
    con.execute("UPDATE meta SET value='2' WHERE key='primary_epoch'")
    con.commit()
    con.close()
    with pytest.raises(personal.PersonalError) as error:
        personal.create_card(personal_env["project"]["project_id"], body)
    assert error.value.code == "PRIMARY_CHANGED"


def test_replay_before_cas_returns_original_and_key_reuse_conflicts(personal_env):
    card = create(personal_env)
    original = envelope({"decision_reason": "最初理由"}, base=1)
    result = personal.update_card(card["card_id"], original)
    personal.update_card(card["card_id"], envelope({"decision_reason": "后续理由"}, base=2))
    assert personal.update_card(card["card_id"], original) == result
    changed = {**original, "changes": {"decision_reason": "不同请求"}}
    with pytest.raises(personal.PersonalError) as error:
        personal.update_card(card["card_id"], changed)
    assert error.value.code == "IDEMPOTENCY_KEY_REUSED"
    response = personal_env["client"].get(f"/api/operations/{original['operation_id']}")
    assert response.json()["response"] == result[0]


def test_same_revision_concurrent_edit_only_one_commit(personal_env):
    card = create(personal_env)

    def edit(reason):
        try:
            return personal.update_card(card["card_id"], envelope({"decision_reason": reason}, base=1))[1]
        except personal.PersonalError as error:
            return error.code
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(edit, ["first", "second"]))
    assert sorted(str(r) for r in results) == ["200", "REVISION_CONFLICT"]
    con = db.connect()
    assert len(personal.revisions(con, "card", card["card_id"])) == 2
    con.close()


def test_duplicate_creation_one_card_and_response_loss_recovery(personal_env):
    body = envelope({"title": "同一次保存"})
    first = personal.create_card(personal_env["project"]["project_id"], body)
    second = personal.create_card(personal_env["project"]["project_id"], body)
    assert first == second
    result = personal_env["client"].get(f"/api/projects/{personal_env['project']['project_id']}/decision-cards").json()
    assert result["total"] == 1
    assert personal_env["client"].get(f"/api/operations/{uuid.uuid4()}").status_code == 404


def test_mutation_requires_revision_and_adoption_requires_explicit_choice(personal_env):
    card = create(personal_env)
    with pytest.raises(personal.PersonalError) as missing:
        personal.update_card(card["card_id"], envelope({"title": "new"}))
    assert missing.value.status == 428
    with pytest.raises(personal.PersonalError):
        create(personal_env, state="adopted")
    option = str(uuid.uuid4())
    adopted = create(personal_env, state="adopted", alternatives=[{"option_id": option, "label": "方案 A"}], selected_option_id=option)
    assert adopted["selected_option_id"] == option
    with pytest.raises(personal.PersonalError):
        create(personal_env, alternatives=[{"option_id": option, "label": "A"}], selected_option_id="0")


def test_adoption_does_not_change_knowledge_and_stale_missing_keep_snapshots(personal_env):
    ref = add_atom()
    con = db.connect()
    original = dict(con.execute("SELECT * FROM knowledge_atom").fetchone())
    con.close()
    card = create(personal_env, evidence_refs=[ref], state="adopted", decision_text="本次先核实基层")
    con = db.connect()
    assert dict(con.execute("SELECT * FROM knowledge_atom").fetchone()) == original
    con.execute("UPDATE knowledge_atom SET claim='同ID内容已改变' WHERE id='personal-atom'")
    con.commit()
    stale = personal.card_view(con, card["card_id"])
    assert stale["needs_recheck"] and stale["state"] == "adopted"
    assert stale["evidence_refs"][0]["status"] == "stale"
    assert stale["evidence_refs"][0]["snapshot"]["claim"] == original["claim"]
    con.execute("DELETE FROM evidence_ref WHERE atom_id='personal-atom'")
    con.execute("DELETE FROM knowledge_atom WHERE id='personal-atom'")
    con.commit()
    assert personal.card_view(con, card["card_id"])["evidence_refs"][0]["status"] == "missing"
    assert personal.revisions(con, "card", card["card_id"])[0]["snapshot"]["evidence_refs"][0]["snapshot"]["claim"] == original["claim"]
    con.close()


def test_explicit_evidence_refresh_retains_earlier_revision(personal_env):
    ref = add_atom()
    card = create(personal_env, evidence_refs=[ref])
    con = db.connect()
    con.execute("UPDATE knowledge_atom SET claim='新的基层条件说明' WHERE id='personal-atom'")
    con.commit()
    new_ref = {**ref, "target_fingerprint": identity.load_target(con, "atom", "personal-atom")[1]}
    con.close()
    with pytest.raises(personal.PersonalError) as stale:
        personal.update_card_evidence(card["card_id"], envelope({"confirm": True, "evidence_refs": [ref]}, base=1))
    assert stale.value.code == "EVIDENCE_CHANGED"
    updated = personal.update_card_evidence(card["card_id"], envelope({"confirm": True, "evidence_refs": [new_ref]}, base=1))[0]["card"]
    assert updated["revision"] == 2 and not updated["needs_recheck"]
    con = db.connect()
    history = personal.revisions(con, "card", card["card_id"])
    assert history[-1]["snapshot"]["evidence_refs"][0]["target_fingerprint"] == ref["target_fingerprint"]
    con.close()


def test_project_conditions_are_snapshots_and_unknown_keys_are_preserved(personal_env):
    project_id = personal_env["project"]["project_id"]
    personal.update_project(project_id, envelope({"profile": {"conditions": {"空间": "卫生间", "历史自由键": "原样"}}}, base=1))
    card = create(personal_env, context_snapshot={"空间": "卫生间"})
    personal.update_project(project_id, envelope({"profile": {"conditions": {"空间": "厨房"}}}, base=2))
    con = db.connect()
    assert personal.card_view(con, card["card_id"])["context_snapshot"] == {"空间": "卫生间"}
    assert personal.revisions(con, "project", project_id)[1]["snapshot"]["profile"]["conditions"]["历史自由键"] == "原样"
    con.close()


def test_export_only_selected_cards_redacts_internal_links_and_credentials(personal_env):
    ref = add_atom()
    card = create(personal_env, evidence_refs=[ref], space="厨房", stage="选材", context_snapshot={"空间": "卫生间"},
                  content_snapshot={"kind": "answer", "text": "http://192.168.1.2/a?token=private-token <script>x</script> token=other-key 裸地址10.0.0.2"})
    other = create(personal_env, title="不应导出的另一张卡")
    con = db.connect()
    result = personal.export_cards(con, personal_env["project"]["project_id"], [card["card_id"]])
    con.close()
    assert "192.168" not in result and "private-token" not in result and "sample-private" not in result
    assert "other-key" not in result and "<script>" not in result and other["title"] not in result
    assert "10.0.0.2" not in result
    assert "https://www.bilibili.com/video/BVpersonal?p=1" in result and "1.5 秒" in result
    assert "个人分类：空间 厨房；阶段 选材" in result
    assert "## 本次条件\n\n- 空间：卫生间" in result


def test_archive_revision_and_invalid_fields(personal_env):
    card = create(personal_env)
    updated = personal.update_card(card["card_id"], envelope({"state": "archived"}, base=1))[0]["card"]
    assert updated["archived_at"] and updated["revision"] == 2
    for changes in ({"state": []}, {"selected_option_id": []}, {"content_snapshot": {"kind": []}}, {"unexpected": "x"}):
        with pytest.raises(personal.PersonalError):
            create(personal_env, **changes)


def test_decision_space_stage_state_filters_precede_paging_and_preserve_history(personal_env):
    project_id = personal_env["project"]["project_id"]
    unknown = create(personal_env, title="未指定标签", context_snapshot={"空间": "卫生间", "时间": "泥瓦"})
    assert unknown["space"] == unknown["stage"] == ""
    for index in range(33):
        create(personal_env, title=f"其他决定{index}", space="厨房", stage="水电")
    a = create(personal_env, title="卫生间泥瓦一", space="卫生间", stage="泥瓦", state="adopted", decision_text="个人采用")
    b = create(personal_env, title="卫生间泥瓦二", space="卫生间", stage="泥瓦", state="adopted", decision_text="另一个采用")
    endpoint = f"/api/projects/{project_id}/decision-cards"
    params = {"space": "卫生间", "stage": "泥瓦", "state": "adopted", "limit": 1}
    first = personal_env["client"].get(endpoint, params=params).json()
    second = personal_env["client"].get(endpoint, params={**params, "offset": 1}).json()
    assert first["total"] == second["total"] == 2
    assert {first["cards"][0]["card_id"], second["cards"][0]["card_id"]} == {a["card_id"], b["card_id"]}
    updated = personal.update_card(a["card_id"], envelope({"stage": "验收"}, base=1))[0]["card"]
    assert updated["stage"] == "验收" and updated["space"] == "卫生间"
    con = db.connect()
    assert personal.revisions(con, "card", a["card_id"])[-1]["snapshot"]["stage"] == "泥瓦"
    assert personal.list_entities(con, project_id, "card", stage="验收")["total"] == 1
    con.close()
    for changes in ({"space": []}, {"stage": None}, {"stage": "x" * 81}):
        with pytest.raises(personal.PersonalError):
            create(personal_env, **changes)
