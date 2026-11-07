"""Explicit membership administration never guesses a complete owner list."""
import hashlib
import json
from pathlib import Path
import uuid

import pytest

from reno import cli, db, storage_admin, storage_lifecycle as lifecycle, storage_members as members


def arguments(root):
    return ["--db", str(root / "data/reno.db"), "--media", str(root / "media"),
            "--reports", str(root / "reports"), "--checkpoints", str(root / "checkpoints")]


def profile(root):
    return storage_admin.profile(database=root / "data/reno.db", media=root / "media",
                                  reports=root / "reports", checkpoints=root / "checkpoints")


def contents(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


def test_absent_membership_preview_creates_neither_files_nor_database(tmp_path, capsys):
    root = tmp_path / "absent"
    assert cli.main(["storage-members", *arguments(root)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["blocked"] and result["digest"] is None
    assert not root.exists()


def test_initializer_requires_current_database_before_creating_roots(tmp_path, capsys):
    root = tmp_path / "absent"
    assert cli.main(["storage-member-init", *arguments(root)]) == 2
    assert json.loads(capsys.readouterr().err)["code"] == "SCHEMA_UPGRADE_REQUIRED"
    assert not root.exists()


@pytest.mark.parametrize("suffix", [["--apply"], ["--apply", "--operation-id", str(uuid.uuid4())],
    ["--apply", "--expected-digest", "a" * 64], ["--expected-digest", "a" * 64],
    ["--operation-id", str(uuid.uuid4())]])
def test_mutation_requires_both_expectations_and_explicit_apply(tmp_path, capsys, suffix):
    root = tmp_path / "absent"
    assert cli.main(["storage-members", *arguments(root), *suffix]) == 2
    assert json.loads(capsys.readouterr().err)["code"] == "MEMBERSHIP_EXPECTATION_REQUIRED"
    assert not root.exists()


def test_existing_roots_stay_unknown_until_exact_declaration_is_applied(tmp_path, capsys):
    value = profile(tmp_path)
    db.initialize(value.database)
    storage_admin.initialize(value)
    before = contents(tmp_path)
    assert cli.main(["storage-members", *arguments(tmp_path)]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert not preview["blocked"]
    assert {row["proposed_state"] for row in preview["roots"]} == {"membership_unknown"}
    assert contents(tmp_path) == before
    assert cli.main(["storage-members", *arguments(tmp_path), "--apply", "--expected-digest",
                     preview["digest"], "--operation-id", str(uuid.uuid4())]) == 0
    capsys.readouterr()
    with lifecycle.shared(value):
        assert members.require_enrolled(value)["library_id"]
    assert {row["state"] for row in members.membership_preview([value])["roots"]} == {"membership_unknown"}

    document = tmp_path / "owners.json"
    document.write_text(json.dumps({"profiles": [members._profile_json(value)], "declarations": [
        {"root": str(getattr(value, role)), "databases": [str(value.database)]}
        for role in ("media", "reports", "checkpoints")]}), encoding="utf-8")
    assert cli.main(["storage-members", "--members-file", str(document)]) == 0
    exact = json.loads(capsys.readouterr().out)
    assert {row["proposed_state"] for row in exact["roots"]} == {"complete"}
    assert cli.main(["storage-members", "--members-file", str(document), "--apply", "--expected-digest",
                     exact["digest"], "--operation-id", str(uuid.uuid4())]) == 0
    capsys.readouterr()
    assert {row["state"] for row in members.membership_preview([value])["roots"]} == {"complete"}


def test_new_root_initializer_is_idempotent_and_preserves_knowledge(tmp_path, capsys):
    value = profile(tmp_path)
    db.initialize(value.database)
    with db.connect(value.database) as con:
        before = db.knowledge_version(con)
    assert cli.main(["storage-member-init", *arguments(tmp_path)]) == 0
    capsys.readouterr()
    saved = contents(tmp_path)
    assert cli.main(["storage-member-init", *arguments(tmp_path)]) == 0
    capsys.readouterr()
    assert contents(tmp_path) == saved
    assert {row["state"] for row in members.membership_preview([value])["roots"]} == {"complete"}
    with db.connect(value.database) as con:
        assert db.knowledge_version(con) == before


@pytest.mark.parametrize("body", ['{"profiles":[],"declarations":[],"profiles":[]}',
    '{"profiles":[],"declarations":[]}', '{"profiles":[],"declarations":[],"private":"secret-body"}',
    '{"profiles":[{"database":"relative"}],"declarations":[]}', '{broken secret-body'])
def test_invalid_member_document_is_controlled_and_not_echoed(tmp_path, capsys, body):
    document = tmp_path / "owners.json"
    document.write_text(body, encoding="utf-8")
    assert cli.main(["storage-members", "--members-file", str(document)]) == 2
    output = capsys.readouterr()
    assert json.loads(output.err)["code"] == "MEMBERSHIP_CONFIG_INVALID"
    assert "secret-body" not in output.err
    assert sorted(p.name for p in tmp_path.iterdir()) == ["owners.json"]


def test_document_and_custom_roots_cannot_be_mixed(tmp_path, capsys):
    assert cli.main(["storage-members", "--members-file", str(tmp_path / "missing.json"), *arguments(tmp_path)]) == 2
    assert json.loads(capsys.readouterr().err)["code"] == "MEMBERSHIP_CONFIG_INVALID"
    assert not tuple(tmp_path.iterdir())


def test_stale_preview_and_partial_profile_are_rejected(tmp_path, capsys):
    value = profile(tmp_path)
    db.initialize(value.database)
    storage_admin.initialize(value)
    assert cli.main(["storage-members", "--db", str(value.database)]) == 2
    assert json.loads(capsys.readouterr().err)["code"] == "STORAGE_PROFILE_REQUIRED"
    assert cli.main(["storage-members", *arguments(tmp_path), "--apply", "--expected-digest", "a" * 64,
                     "--operation-id", str(uuid.uuid4())]) == 2
    assert json.loads(capsys.readouterr().err)["code"] == "MEMBERSHIP_PREVIEW_STALE"
    assert all(not (root / members.REGISTRY).exists() for root in (value.media, value.reports, value.checkpoints))
