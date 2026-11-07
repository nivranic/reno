"""真实隔离家庭事务与已安装云镜像的摘要预览；不操作用户库或创建分享链接。"""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import uuid

from fastapi import Request
import pytest

from app.personal_read_api import VERSION_HEADER
from reno import checklists, config, db, personal, storage_admin
from reno import personal_checklist_share_contract as contract, personal_checklist_share_preview as preview
from reno import personal_mirror_read as reader, storage_lifecycle as lifecycle
from tests.test_personal_projects import personal_env, envelope  # noqa: F401
from tests.test_personal_cloud_read_api import cloud_read_env, client, assert_private  # noqa: F401
from tests.test_personal_mirror_transfer import mirror_env, table_state  # noqa: F401
from tests.test_personal_mirror_read import install
from tests.test_checklist_instances import requirement, standard, observe


def path(project_id):
    return f"/api/projects/{project_id}/checklists/share-preview"


def selection(instance):
    return {"selection": [{"instance_id": instance["instance_id"], "revision": instance["revision"], "items": [
        {"item_id": item["item_id"], "revision": item["revision"],
         "last_commit_seq": max((check["commit_seq"] for check in item["checks"]), default=0)}
        for item in instance["items"]]}]}


def create(env, items=None):
    return checklists.create(env["project"]["project_id"], envelope({"title": "本人的现场记录 <b>原文</b>",
        "space": "excluded-space", "stage": "excluded-stage", "items": items or [requirement()]}))[0]["checklist"]


def detail(instance):
    con = db.connect()
    try:
        return checklists.detail(con, instance["instance_id"])
    finally:
        con.close()


def local_state():
    con = db.connect()
    try:
        return tuple(con.iterdump())
    finally:
        con.close()


def assert_preview(response, instance, version=None):
    assert response.status_code == 200, response.text
    assert_private(response, version)
    answer = response.json()
    assert set(answer) == {"snapshot", "snapshot_sha256", "personal_read_version"}
    assert answer["personal_read_version"] == version
    assert answer["snapshot"] == contract.project_snapshot([instance])
    assert answer["snapshot_sha256"] == hashlib.sha256(contract.encode_snapshot(answer["snapshot"])).hexdigest()
    return answer


@pytest.mark.parametrize("role", ["all", "home"])
def test_original_read_transaction_and_whitelist_leave_all_sql_unchanged(personal_env, monkeypatch, role):
    config.set_local({"node_role": role})
    instance = create(personal_env, [requirement(title="第一项"), requirement(title="第二项")])
    observed = observe(instance["items"][0], note="excluded-note",
                       measurements=[{"key": "photo_note", "value": "excluded-observation"}])
    instance = observed["checklist"]
    before, seen = local_state(), []
    original = checklists.detail
    def actual_detail(con, instance_id):
        assert con.in_transaction and con.execute("PRAGMA query_only").fetchone()[0] == 1
        assert lifecycle.current_guard() is not None
        seen.append(con)
        return original(con, instance_id)
    monkeypatch.setattr(checklists, "detail", actual_detail)
    response = personal_env["client"].post(path(instance["project_id"]), json=selection(instance))
    assert_preview(response, instance)
    assert len(seen) == 1
    assert VERSION_HEADER not in response.headers
    for private in (instance["project_id"], instance["instance_id"], instance["items"][0]["item_id"],
                    observed["check"]["check_id"], "excluded-space", "excluded-stage", "excluded-note",
                    "excluded-observation", "管根封闭前留存照片"):
        assert private not in response.text
    assert local_state() == before


def test_append_backdated_remediation_and_void_all_invalidate_original_selection(personal_env):
    instance = create(personal_env)
    first = observe(instance["items"][0])
    instance = first["checklist"]
    http, endpoint = personal_env["client"], path(instance["project_id"])
    for changes in (
        {"result": "fail", "checked_at": "2025-01-01T00:00:00Z"},
        {"record_kind": "remediation", "note": "记录整改但不覆盖结果"},
        {"record_kind": "void", "note": "原记录误录", "supersedes_check_id": first["check"]["check_id"]},
    ):
        old = selection(instance)
        updated = checklists.append_check(instance["items"][0]["item_id"], envelope(changes, base=1))[0]["checklist"]
        assert updated["revision"] == instance["revision"] == 1
        assert updated["items"][0]["revision"] == instance["items"][0]["revision"] == 1
        before = local_state()
        rejected = http.post(endpoint, json=old)
        assert rejected.status_code == 409 and rejected.json()["code"] == "SHARE_SELECTION_CHANGED"
        assert "snapshot" not in rejected.json()
        assert_preview(http.post(endpoint, json=selection(updated)), updated)
        assert local_state() == before
        instance = updated
    assert instance["items"][0]["current_result"] == "fail"
    assert instance["progress"]["complete"] == 0


def test_complete_selection_requires_project_checklist_item_revisions_membership_and_order(personal_env, monkeypatch):
    instance = create(personal_env, [requirement(title="甲"), requirement(title="乙")])
    good = selection(instance)
    variants = []
    for field, value in (("instance_id", str(uuid.uuid4())), ("revision", 2)):
        bad = deepcopy(good)
        bad["selection"][0][field] = value
        variants.append(bad)
    for transform in (lambda items: items.pop(), lambda items: items.reverse(),
                      lambda items: items[0].update(revision=2),
                      lambda items: items[0].update(item_id=str(uuid.uuid4())),
                      lambda items: items[0].update(last_commit_seq=1)):
        bad = deepcopy(good)
        transform(bad["selection"][0]["items"])
        variants.append(bad)
    other = str(uuid.uuid4())
    con = db.connect()
    try:
        con.execute("INSERT INTO renovation_project(project_id,name,created_at,updated_at) VALUES(?,?,?,?)",
                    (other, "其他合成项目", "2026-10-09T00:00:00Z", "2026-10-09T00:00:00Z"))
        con.commit()
    finally:
        con.close()
    def forbidden(*args, **kwargs):
        pytest.fail("inadmissible selection must fail before private detail parsing")
    monkeypatch.setattr(checklists, "detail", forbidden)
    before = local_state()
    for endpoint, body in [(path(instance["project_id"]), value) for value in variants] + [
            (path(other), good), (path(str(uuid.uuid4())), good)]:
        response = personal_env["client"].post(endpoint, json=body)
        assert response.status_code == 409 and response.json()["code"] == "SHARE_SELECTION_CHANGED"
        assert_private(response)
        assert "snapshot" not in response.json()
    assert local_state() == before


def test_real_item_revision_and_archival_require_fresh_selection(personal_env):
    instance = create(personal_env)
    instance = observe(instance["items"][0])["checklist"]
    endpoint, http = path(instance["project_id"]), personal_env["client"]
    checklists.update_item(instance["items"][0]["item_id"], envelope({"title": "已修订本项"}, base=1))
    assert http.post(endpoint, json=selection(instance)).status_code == 409
    instance = detail(instance)
    assert instance["items"][0]["current_result"] == "unchecked"
    assert instance["items"][0]["current_check"] is None
    assert selection(instance)["selection"][0]["items"][0]["last_commit_seq"] == 1
    without_history = selection(instance)
    without_history["selection"][0]["items"][0]["last_commit_seq"] = 0
    omitted = http.post(endpoint, json=without_history)
    assert omitted.status_code == 409 and omitted.json()["code"] == "SHARE_SELECTION_CHANGED"
    assert_preview(http.post(endpoint, json=selection(instance)), instance)
    archived = checklists.update(instance["instance_id"], envelope({"lifecycle_state": "archived"}, base=1))[0]["checklist"]
    assert http.post(endpoint, json=selection(instance)).status_code == 409
    assert_preview(http.post(endpoint, json=selection(archived)), archived)
    assert archived["state"] == "archived"


def test_changed_source_recomputes_current_pass_recheck_and_progress_from_original_domain(personal_env):
    source = standard(personal_env["project"]["project_id"])
    ref = {"target_kind": "source", "target_id": source["source_id"], "target_fingerprint": source["target_fingerprint"]}
    instance = create(personal_env, [requirement(basis_kind="standard_clause", refs=[ref])])
    instance = observe(instance["items"][0])["checklist"]
    assert instance["state"] == "completed"
    old = selection(instance)
    personal.update_source(source["source_id"], envelope({"validity": "superseded"}, base=1))
    current = detail(instance)
    assert selection(current) == old
    assert current["items"][0]["current_result"] == "pass" and current["items"][0]["needs_recheck"]
    assert current["progress"] == {"total": 1, "complete": 0} and current["state"] == "active"
    before = local_state()
    response = personal_env["client"].post(path(instance["project_id"]), json=old)
    assert_preview(response, current)
    assert source["source_id"] not in response.text and source["excerpt"] not in response.text
    assert local_state() == before


def test_invalid_selection_and_duplicate_json_are_rejected_without_snapshot(personal_env):
    instance = create(personal_env)
    good = selection(instance)
    invalid = [None, {}, {**good, "extra": "private"}, {"selection": []},
               {"selection": good["selection"] * 2}]
    for key, value in (("instance_id", True), ("instance_id", instance["instance_id"].replace("-", "")),
                       ("revision", True), ("revision", 0), ("revision", 1 << 63), ("revision", 1.0),
                       ("items", []), ("items", good["selection"][0]["items"] * 2)):
        bad = deepcopy(good)
        bad["selection"][0][key] = value
        invalid.append(bad)
    for key, value in (("last_commit_seq", -1), ("last_commit_seq", True), ("revision", True),
                       ("last_commit_seq", 1.0), ("extra", "private")):
        bad = deepcopy(good)
        bad["selection"][0]["items"][0][key] = value
        invalid.append(bad)
    endpoint, http, before = path(instance["project_id"]), personal_env["client"], local_state()
    for value in invalid:
        response = http.post(endpoint, json=value)
        assert response.status_code == 400 and response.json()["code"] == "INVALID_REQUEST"
        assert "snapshot" not in response.json()
        assert_private(response)
    raw = json.dumps(good).encode()
    for value in (raw.replace(b'"revision": 1', b'"revision": 1,"revision": 1'), b"\xff",
                  b"{" + b" " * (256 * 1024)):
        response = http.post(endpoint, content=value)
        assert response.status_code in {400, 413} and "snapshot" not in response.json()
    assert local_state() == before


def test_token_feature_and_field_gates_reject_before_body_or_factory(personal_env, monkeypatch):
    project_id = personal_env["project"]["project_id"]
    config.set_local({"auth_token": "synthetic-checklist-preview-token"})
    def forbidden(*args, **kwargs):
        pytest.fail("disabled preview reached factory")
    async def forbidden_stream(*args, **kwargs):
        pytest.fail("disabled preview consumed request body")
        yield b""
    monkeypatch.setattr(storage_admin, "profile", forbidden)
    monkeypatch.setattr(Request, "stream", forbidden_stream)
    assert personal_env["client"].post(path(project_id), content=b"unparsed").status_code == 401
    headers = {"X-Reno-Token": "synthetic-checklist-preview-token"}
    for changes, code in (({"personal_features_enabled": False}, "CAPABILITY_DISABLED"),
            ({"personal_features_enabled": True, "field_checks_enabled": False}, "CAPABILITY_DISABLED"),
            ({"node_role": "cloud", "personal_cloud_read_enabled": False}, "PERSONAL_MIRROR_READ_DISABLED"),
            ({"personal_cloud_read_enabled": True, "field_checks_enabled": False}, "CAPABILITY_DISABLED")):
        config.set_local(changes)
        response = personal_env["client"].post(path(project_id), content=b"unparsed", headers=headers)
        assert response.status_code == 403 and response.json()["code"] == code
        assert_private(response)


@pytest.mark.parametrize("failure", ["callback", "exit"])
def test_original_read_failures_never_finalize_or_publish(personal_env, monkeypatch, failure):
    from app import personal_read_api
    instance = create(personal_env)
    if failure == "callback":
        original = checklists.detail
        def failed_read(con, instance_id):
            original(con, instance_id)
            raise personal.PersonalError("合成原回调失败", "PREVIEW_SOURCE_FAILED", 503)
        monkeypatch.setattr(checklists, "detail", failed_read)
    else:
        original = personal_read_api._ordinary_read
        @contextmanager
        def failed_exit(*args, **kwargs):
            with original(*args, **kwargs) as con:
                yield con
            raise personal.PersonalError("合成原退出失败", "PREVIEW_SOURCE_FAILED", 503)
        monkeypatch.setattr(personal_read_api, "_ordinary_read", failed_exit)
    def forbidden(*args, **kwargs):
        pytest.fail("failed original factory cannot freeze or publish snapshot")
    monkeypatch.setattr(preview, "finalize_snapshot", forbidden)
    response = personal_env["client"].post(path(instance["project_id"]), json=selection(instance))
    assert response.status_code == 503 and response.json()["code"] == "PREVIEW_SOURCE_FAILED"
    assert "snapshot" not in response.json()
    assert_private(response)


def test_actual_cloud_install_version_change_and_original_exit_failure(cloud_read_env, monkeypatch):
    env = cloud_read_env
    endpoint = path(env["project_id"])
    with client() as http:
        cap = http.get("/api/capabilities").json()
        version = cap["personal_read_version"]
        instance = http.get(f"/api/checklists/{env['instance_id']}").json()["checklist"]
        before = table_state(env)
        assert_preview(http.post(endpoint, json=selection(instance), headers={VERSION_HEADER: version}), instance, version)
        assert table_state(env) == before
    env["select"](env["source_root"], "home")
    checklists.append_check(env["item_id"], envelope({"record_kind": "remediation", "note": "另一条实际记录"},
                           base=1, epoch=env["primary_epoch"]))
    previous = {key: cap["personal_mirror"][key] for key in ("snapshot_id", "snapshot_sha256")}
    install(env, previous=previous, expected_revision=1)
    config.set_local({"personal_cloud_read_enabled": True})
    before = table_state(env)
    with client() as http:
        changed = http.post(endpoint, json=selection(instance), headers={VERSION_HEADER: version})
        assert changed.status_code == 409 and changed.json()["code"] == "PERSONAL_MIRROR_READ_VERSION_CHANGED"
        fresh = http.get(f"/api/checklists/{env['instance_id']}").json()
        current, new_version = fresh["checklist"], fresh["personal_read_version"]
        stale = http.post(endpoint, json=selection(instance), headers={VERSION_HEADER: new_version})
        assert stale.status_code == 409 and stale.json()["code"] == "SHARE_SELECTION_CHANGED"
        assert_preview(http.post(endpoint, json=selection(current), headers={VERSION_HEADER: new_version}), current, new_version)
    assert table_state(env) == before
    original = reader.open_installed_read
    @contextmanager
    def failed_exit(*args, **kwargs):
        with original(*args, **kwargs) as con:
            yield con
        raise reader.PersonalMirrorReadError("PROOF_CHANGED")
    def forbidden(*args, **kwargs):
        pytest.fail("failed original cloud exit cannot publish snapshot")
    monkeypatch.setattr(reader, "open_installed_read", failed_exit)
    monkeypatch.setattr(preview, "finalize_snapshot", forbidden)
    with client() as http:
        response = http.post(endpoint, json=selection(current), headers={VERSION_HEADER: new_version})
        assert response.status_code == 409 and response.json()["code"] == "PERSONAL_MIRROR_READ_PROOF_CHANGED"
        assert "snapshot" not in response.json()
        assert_private(response)
    assert table_state(env) == before


def test_cloud_without_install_and_original_writers_remain_blocked(mirror_env, monkeypatch):
    config.set_local({"personal_cloud_read_enabled": True})
    with client() as http:
        response = http.post(path(mirror_env["project_id"]), json={"selection": []})
        assert response.status_code == 403 and response.json()["code"] == "PERSONAL_MIRROR_READ_NOT_INSTALLED"
        async def forbidden_stream(*args, **kwargs):
            pytest.fail("cloud writer consumed request body")
            yield b""
        monkeypatch.setattr(Request, "stream", forbidden_stream)
        for endpoint in (f"/api/projects/{mirror_env['project_id']}/checklists",
                         f"/api/checklist-items/{uuid.uuid4()}/checks",
                         f"/api/projects/{mirror_env['project_id']}/decision-cards"):
            response = http.post(endpoint, content=b"unparsed")
            assert response.status_code == 403 and response.json()["code"] == "PERSONAL_CLOUD_READ_ONLY"
