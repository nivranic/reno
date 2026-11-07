"""Manual source revisions and streamed attachments against the authenticated app."""
import io
import json
import os
import time
import uuid

from PIL import Image
import pytest

from reno import config, db, personal, personal_attachments as files
from tests.test_personal_projects import personal_env, envelope, create  # noqa: F401


def png(color="red"):
    stream = io.BytesIO()
    Image.new("RGB", (4, 4), color).save(stream, "PNG")
    return stream.getvalue()


def source(env, **changes):
    return personal.create_source(env["project"]["project_id"], envelope({"title": "人工来源", **changes}))[0]["source"]


def upload(env, item, data=None, name="sample.png", mime="image/png", op=None, extra_headers=None):
    headers = {"X-Operation-Id": op or str(uuid.uuid4()), "X-Primary-Epoch": "1", **(extra_headers or {})}
    return env["client"].post(f"/api/sources/{item['source_id']}/attachments", files={"file": (name, png() if data is None else data, mime)}, headers=headers)


def test_source_current_requires_human_check_and_revisions_stale_refs(personal_env):
    for values in ({"validity": "current"}, {"verification_state": "user_checked"}, {"checked_at": "2026-01-01"}, {"validity": []},
                   {"published_on": "2025-02-31"}, {"valid_to": "2026-1-1"}, {"valid_from": "20260101"},
                   {"valid_from": "2026-01-02", "valid_to": "2026-01-01"}):
        with pytest.raises(personal.PersonalError):
            source(personal_env, **values)
    item = source(personal_env, source_type="standard_record", document_no="人工样本条文",
                  locator="第1条", excerpt="此处仅是测试原文", validity="current", verification_state="user_checked",
                  checked_at="2020-01-01T00:00:00+08:00", check_note="已对照人工样本原文")
    card = create(personal_env, evidence_refs=[{"target_kind": "source", "target_id": item["source_id"], "target_fingerprint": item["target_fingerprint"]}])
    updated = personal.update_source(item["source_id"], envelope({"validity": "superseded"}, base=1))[0]["source"]
    assert updated["revision"] == 2
    con = db.connect()
    assert personal.card_view(con, card["card_id"])["needs_recheck"]
    assert personal.revisions(con, "source", item["source_id"])[-1]["snapshot"]["validity"] == "current"
    con.close()


def test_ready_upload_sha_download_and_exact_retry(personal_env):
    item = source(personal_env)
    op = str(uuid.uuid4())
    first = upload(personal_env, item, op=op)
    assert first.status_code == 201, first.text
    second = upload(personal_env, item, op=op)
    assert second.json() == first.json()
    attachment = first.json()["attachment"]
    assert attachment["state"] == "ready" and not attachment["missing"]
    assert "storage_key" not in attachment
    import hashlib
    assert attachment["sha256"] == hashlib.sha256(png()).hexdigest()
    response = personal_env["client"].get(f"/api/attachments/{attachment['attachment_id']}/file")
    assert response.content == png() and response.headers["x-content-type-options"] == "nosniff"
    changed = upload(personal_env, item, png("blue"), op=op)
    assert changed.status_code == 409 and changed.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert personal_env["client"].get(f"/api/attachments/{attachment['attachment_id']}/file").content == png()
    con = db.connect()
    assert con.execute("SELECT COUNT(*) FROM source_attachment").fetchone()[0] == 1
    con.close()


@pytest.mark.parametrize("data,name,mime,status", [
    (b"not-png", "sample.png", "image/png", 415),
    (b"test", "photo.heic", "image/heic", 415),
    (b"test", "sample.png", "application/pdf", 415),
    (b"%PDF-1.4 truncated", "sample.pdf", "application/pdf", 415),
    (b"", "empty.png", "image/png", 400),
    (b"bad", "../escape.png", "image/png", 400),
])
def test_invalid_format_signature_name_or_empty_never_ready(personal_env, data, name, mime, status):
    item = source(personal_env)
    response = upload(personal_env, item, data, name, mime)
    assert response.status_code == status, response.text
    con = db.connect()
    assert con.execute("SELECT COUNT(*) FROM source_attachment WHERE state='ready'").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM mutation_receipt WHERE scope LIKE '%:attachment'").fetchone()[0] == 0
    con.close()
    assert not list((config.DATA / "personal-attachments/.staging").glob("*.part"))


def test_actual_stream_size_limit_and_interrupted_body(personal_env):
    item = source(personal_env)
    config.set_local({"attachment_image_max_bytes": 16})
    response = upload(personal_env, item)
    assert response.status_code == 413 and response.json()["code"] == "ATTACHMENT_TOO_LARGE"
    config.set_local({"attachment_image_max_bytes": 1024})
    # No Content-Length; the payload never includes a closing multipart boundary.
    chunks = [b'--test\r\nContent-Disposition: form-data; name="file"; filename="part.png"\r\nContent-Type: image/png\r\n\r\n', png()]
    response = personal_env["client"].post(f"/api/sources/{item['source_id']}/attachments", content=iter(chunks),
        headers={"Content-Type": "multipart/form-data; boundary=test", "X-Operation-Id": str(uuid.uuid4()), "X-Primary-Epoch": "1"})
    assert response.status_code == 400
    con = db.connect()
    assert con.execute("SELECT COUNT(*) FROM source_attachment WHERE state='ready'").fetchone()[0] == 0
    con.close()


def test_attachment_auth_same_project_and_ready_checks(personal_env):
    item = source(personal_env)
    attachment = upload(personal_env, item).json()["attachment"]
    config.set_local({"auth_token": "artificial-test-access"})
    assert personal_env["client"].get(f"/api/attachments/{attachment['attachment_id']}/file").status_code == 401
    assert personal_env["client"].get(f"/api/attachments/{attachment['attachment_id']}/file", headers={"X-Reno-Token": "artificial-test-access"}).status_code == 200
    con = db.connect()
    other = str(uuid.uuid4())
    con.execute("INSERT INTO renovation_project(project_id,name,created_at,updated_at) VALUES(?,?,?,?)", (other, "另一个项目", "2020-01-01", "2020-01-01"))
    con.commit()
    con.close()
    with pytest.raises(personal.PersonalError):
        personal.create_card(other, envelope({"title": "非法跨项目引用", "evidence_refs": [{"target_kind": "attachment", "target_id": attachment["attachment_id"], "target_fingerprint": attachment["target_fingerprint"]}]}))
    card = create(personal_env, evidence_refs=[{"target_kind": "attachment", "target_id": attachment["attachment_id"], "target_fingerprint": attachment["target_fingerprint"]}])
    con = db.connect()
    key = con.execute("SELECT storage_key FROM source_attachment").fetchone()[0]
    files.resolve_storage(key).unlink()
    assert personal.card_view(con, card["card_id"])["evidence_refs"][0]["status"] == "missing"
    with pytest.raises(personal.PersonalError):
        files.ready_file(con, attachment["attachment_id"])
    assert files.attachment_manifest(con)[0]["complete"] is False
    con.close()


def test_pdf_and_upload_limit_failed_retry_no_overwrite(personal_env):
    item = source(personal_env)
    pdf = b"%PDF-1.4\n1 0 obj <</Type/Catalog>> endobj\n%%EOF\n"
    response = upload(personal_env, item, pdf, "document.pdf", "application/pdf")
    assert response.status_code == 201
    config.set_local({"attachment_per_source_limit": 1})
    assert upload(personal_env, item).status_code == 400


def test_cleanup_old_staging_keeps_all_ready_and_archive_does_not_delete(personal_env):
    item = source(personal_env)
    attachment = upload(personal_env, item).json()["attachment"]
    card = create(personal_env, evidence_refs=[{"target_kind": "attachment", "target_id": attachment["attachment_id"], "target_fingerprint": attachment["target_fingerprint"]}])
    personal.update_card(card["card_id"], envelope({"state": "archived"}, base=1))
    orphan = config.DATA / "personal-attachments/.staging/orphan.part"
    orphan.write_bytes(b"old incomplete upload")
    os.utime(orphan, (0, 0))
    assert files.cleanup_staging(now=time.time()) >= 1
    assert not orphan.exists()
    assert personal_env["client"].get(f"/api/attachments/{attachment['attachment_id']}/file").status_code == 200
    with pytest.raises(personal.PersonalError):
        files.resolve_storage("../config.test.json")
    with pytest.raises(personal.PersonalError):
        files.resolve_storage("C:/outside/file.pdf")


def test_cleanup_never_removes_an_active_upload_and_failed_retry_refreshes_age(personal_env):
    item = source(personal_env)
    op = str(uuid.uuid4())
    key = f"files/{uuid.uuid5(uuid.UUID(op), 'personal-attachment')}.png"
    aid, _ = files._stage(item["source_id"], op, 1, "sample.png", "image/png", key)
    final = files.resolve_storage(key)
    final.parent.mkdir(parents=True, exist_ok=True)
    final.write_bytes(b"incomplete")
    temp = files.resolve_storage(f".staging/{op}-active.part")
    temp.parent.mkdir(parents=True, exist_ok=True)
    temp.write_bytes(b"active bytes")
    os.utime(temp, (0, 0))
    con = db.connect()
    con.execute("UPDATE source_attachment SET created_at='2000-01-01T00:00:00Z' WHERE attachment_id=?", (aid,))
    con.commit()
    con.close()
    with files.upload_lock(op) as acquired:
        assert acquired
        files.cleanup_staging()
        assert temp.exists() and final.exists()
    files.cleanup_staging()
    assert not temp.exists() and not final.exists()
    files._stage(item["source_id"], op, 1, "sample.png", "image/png", key)
    con = db.connect()
    row = con.execute("SELECT state,created_at FROM source_attachment WHERE attachment_id=?", (aid,)).fetchone()
    assert row["state"] == "staging" and not row["created_at"].startswith("2000")
    con.close()
