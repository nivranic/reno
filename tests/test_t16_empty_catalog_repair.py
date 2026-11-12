"""Only the original empty catalog may receive this explicit maintenance ACL."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace

import pytest

from reno import personal_preservation_contract as contract, personal_preservation_files as private_files
from reno import storage_lifecycle as lifecycle, tasks
from tests.test_personal_preservation import library


def imported(name, path):
    if not Path(path).is_file():
        pytest.skip("Historical maintenance tool is absent from this checkout")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def repair_env(library, monkeypatch):
    profile, _project, local = library
    workspace = Path(__file__).resolve().parents[1]
    module = imported("reno_t16_empty_catalog_repair_test_module", workspace / "data/repair_t16_empty_catalog.py")
    reload = imported("reno_t16_empty_catalog_repair_reload_test_module", workspace / "data/reload_t16_public_operations.py")
    neutral = runpy.run_path(str(workspace / "data/maintain_schema17.py"))
    root, data = profile.database.parent.parent, profile.database.parent
    partial = data / module.PARTIAL.name
    partial.mkdir()
    observation = data / "partial-observation.json"
    source = root / "reno/personal_preservation_files.py"
    source.parent.mkdir()
    source.write_bytes((workspace / "reno/personal_preservation_files.py").read_bytes())
    for name, value in {"ROOT": root, "DATA": data, "PARTIAL": partial,
                        "OBSERVATION": observation, "FILES_SOURCE": source}.items():
        monkeypatch.setattr(module, name, value)

    def inside(path, *, existing=False):
        path = Path(path).absolute()
        assert path.is_relative_to(root) and not path.is_symlink()
        if existing:
            assert path.exists()
        return path

    def file_facts(path):
        path = inside(path, existing=True)
        info = path.stat()
        assert path.is_file() and info.st_nlink == 1
        return {"path": path.relative_to(root).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "byte_size": info.st_size, "identity_sha256": module.digest(
                    (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns))}

    originals = [local, data / "node-identity.json"]

    def capture(_h, _previous, value):
        with reload.personal_private_readonly({}, value) as con:
            sql = neutral["snapshot"](con)
            sql["columns"].pop("worker_lease", None)
            sql["tables"].pop("worker_lease", None)
            version = int(con.execute("SELECT value FROM meta WHERE key='migration_version'").fetchone()[0])
        return {"format_version": 1, "schema_version": version, "sql": sql,
                "stats": {"synthetic_library": True}, "attachment_count": 0,
                "files": [file_facts(path) for path in originals], "namespaces": [],
                "lifecycle_lock_identities": [{"path": str(path), "inode": str(path.stat().st_ino)}
                                              for path in (base / lifecycle.LOCK_NAME for base in value.roots)],
                "helper_sha256": "a" * 64}

    h = {"inside": inside, "file_facts": file_facts, "lifecycle": lifecycle, "tasks": tasks}
    with lifecycle.shared(profile):
        before = capture(h, {}, profile)
    before.update(kind="t16_public_operations_original_before", preservation_trees=[
        {"path": (data / name).relative_to(root).as_posix(), "present": False, "directories": [], "files": []}
        for name in (contract.CATALOG, contract.DIRECTORY)])
    before_path = partial / "before.json"
    before_path.write_bytes(json.dumps(before, sort_keys=True, separators=(",", ":")).encode())
    monkeypatch.setattr(module, "BEFORE_SHA", hashlib.sha256(before_path.read_bytes()).hexdigest())
    observed = {"state": "original_data_preserved_partial_folder_not_ready",
                "partial_maintenance": {"operation_id": module.ORIGINAL_OPERATION,
                    "before_file_sha256": module.BEFORE_SHA, "ready_proof_present": False},
                "personal_preservation_catalog": {"empty": True, "entry_count": 0}}
    observation.write_bytes(json.dumps(observed, sort_keys=True, separators=(",", ":")).encode())
    monkeypatch.setattr(module, "OBSERVATION_SHA", hashlib.sha256(observation.read_bytes()).hexdigest())
    catalog = data / contract.CATALOG
    catalog.mkdir(mode=0o755)
    if os.name != "nt":
        catalog.chmod(0o755)
    ctx = {"reload": {"read_fact": reload.read_fact, "capture": capture}, "h": h,
           "previous": {}, "profile": profile}
    monkeypatch.setattr(module, "load_context", lambda: ctx)
    return module, ctx, catalog, before, originals


def test_default_preview_is_readonly_and_never_takes_worker_lock_or_initializer(repair_env, monkeypatch):
    module, ctx, catalog, _before, originals = repair_env
    raw = {path: path.read_bytes() for path in [ctx["profile"].database, *originals]}
    partial = {path.name: path.read_bytes() for path in module.PARTIAL.iterdir()}

    def forbidden(*args, **kwargs):
        raise AssertionError("Default preview cannot write or take the worker lock")

    ctx["h"]["tasks"] = SimpleNamespace(process_lock=forbidden)
    monkeypatch.setattr(module, "initialize_catalog_private", forbidden)
    result = module.preview(ctx)
    assert result["eligible"] is True and result["ACL_changed"] is False
    assert result["facts"]["owner"]["owner_current"] is True
    assert len(result["facts"]["owner"]["acl_sha256"]) == 64
    assert list(catalog.iterdir()) == []
    assert {path: path.read_bytes() for path in raw} == raw
    assert {path.name: path.read_bytes() for path in module.PARTIAL.iterdir()} == partial
    assert "synthetic-private-config-only" not in json.dumps(result)


def test_apply_has_actual_worker_then_ex_and_changes_only_exact_empty_catalog_acl(repair_env, monkeypatch):
    module, ctx, catalog, _before, originals = repair_env
    first = module.preview(ctx)
    original_raw = {path: path.read_bytes() for path in [ctx["profile"].database, *originals]}
    old_identity = first["facts"]["directory_identity"]
    seen = []

    def initialize(directory):
        assert directory.path == catalog and tasks.holds_process_lock(profile=ctx["profile"])
        assert lifecycle.require_guard(ctx["profile"], exclusive=True).exclusive
        assert directory.names() == []
        seen.append(directory.path)
        if os.name == "nt":
            private_files._windows_acl(directory.path, directory=True, initialize=True)
        else:
            directory.path.chmod(0o700)  # Isolated harness for the Windows-only mutation boundary.

    monkeypatch.setattr(module, "initialize_catalog_private", initialize)
    result = module.apply(ctx, first["preview_sha256"])
    assert seen == [catalog] and result["ACL_changed"] is True
    assert result["facts"]["directory_identity"] == old_identity
    assert result["facts"]["owner"]["acl_sha256"] != first["facts"]["owner"]["acl_sha256"]
    assert result["SQL_or_original_files_changed"] is False and result["partial_folder_written"] is False
    assert {path: path.read_bytes() for path in original_raw} == original_raw
    private_files.require_private(catalog, directory=True)
    with pytest.raises(module.CatalogRepairError, match="REPAIR_CATALOG_ALREADY_PRIVATE"):
        module.apply(ctx, first["preview_sha256"])


@pytest.mark.parametrize("variant", ["original_present", "unknown_entry", "wrong_owner", "bundle_present", "partial_ready"])
def test_unproven_or_unknown_catalog_never_reaches_initializer(repair_env, monkeypatch, variant):
    module, ctx, catalog, before, _originals = repair_env
    seen = []
    monkeypatch.setattr(module, "initialize_catalog_private", lambda directory: seen.append(directory.path))
    if variant == "original_present":
        before["preservation_trees"][0]["present"] = True
        path = module.PARTIAL / "before.json"
        path.write_bytes(json.dumps(before, sort_keys=True, separators=(",", ":")).encode())
        monkeypatch.setattr(module, "BEFORE_SHA", hashlib.sha256(path.read_bytes()).hexdigest())
        observed = json.loads(module.OBSERVATION.read_bytes())
        observed["partial_maintenance"]["before_file_sha256"] = module.BEFORE_SHA
        module.OBSERVATION.write_bytes(json.dumps(observed, sort_keys=True, separators=(",", ":")).encode())
        monkeypatch.setattr(module, "OBSERVATION_SHA", hashlib.sha256(module.OBSERVATION.read_bytes()).hexdigest())
    elif variant == "unknown_entry":
        (catalog / "unknown-evidence").write_bytes(b"must stay")
    elif variant == "wrong_owner":
        original = module.owner_observation
        monkeypatch.setattr(module, "owner_observation", lambda directory: {**original(directory), "owner_current": False})
    elif variant == "bundle_present":
        (catalog.parent / contract.DIRECTORY).mkdir()
    else:
        (module.PARTIAL / "ready.json").write_bytes(b"unknown")
    expected = {"original_present": "REPAIR_ORIGINAL_ABSENCE_UNPROVEN", "unknown_entry": "REPAIR_CATALOG_NOT_EMPTY",
                "wrong_owner": "REPAIR_OWNER_NOT_CURRENT", "bundle_present": "REPAIR_BUNDLE_NAMESPACE_PRESENT",
                "partial_ready": "REPAIR_PARTIAL_ALREADY_READY"}[variant]
    with pytest.raises(module.CatalogRepairError, match=expected):
        module.apply(ctx, "f" * 64)
    assert seen == []


@pytest.mark.parametrize("variant", ["wrong_digest", "new_inode", "same_inode_new_acl"])
def test_cas_blocks_digest_identity_or_nonprivate_acl_drift(repair_env, monkeypatch, variant):
    module, ctx, catalog, _before, _originals = repair_env
    first = module.preview(ctx)
    seen = []
    monkeypatch.setattr(module, "initialize_catalog_private", lambda directory: seen.append(directory.path))
    expected = first["preview_sha256"]
    if variant == "wrong_digest":
        expected = "f" * 64
    elif variant == "new_inode":
        catalog.rename(catalog.with_name("displaced-empty-catalog"))
        catalog.mkdir(mode=0o755)
        if os.name != "nt":
            catalog.chmod(0o755)
    else:
        original = module.owner_observation
        monkeypatch.setattr(module, "owner_observation", lambda directory: {**original(directory), "acl_sha256": "e" * 64})
    with pytest.raises(module.CatalogRepairError, match="REPAIR_PREVIEW_CHANGED"):
        module.apply(ctx, expected)
    assert seen == [] and list(catalog.iterdir()) == []


def test_changed_original_file_blocks_before_any_acl_mutation(repair_env, monkeypatch):
    module, ctx, _catalog, _before, originals = repair_env
    first = module.preview(ctx)
    seen = []
    monkeypatch.setattr(module, "initialize_catalog_private", lambda directory: seen.append(directory.path))
    originals[0].write_bytes(b"changed synthetic config")
    with pytest.raises(module.CatalogRepairError, match="REPAIR_ORIGINAL_DATA_CHANGED"):
        module.apply(ctx, first["preview_sha256"])
    assert seen == []


def test_changed_original_personal_sql_blocks_before_any_acl_mutation(repair_env, monkeypatch):
    from reno import storage_admin
    module, ctx, _catalog, _before, _originals = repair_env
    first = module.preview(ctx)
    seen = []
    monkeypatch.setattr(module, "initialize_catalog_private", lambda directory: seen.append(directory.path))
    with storage_admin.connection(ctx["profile"]) as con:
        con.execute("UPDATE renovation_project SET name='changed synthetic project'")
        con.commit()
    with pytest.raises(module.CatalogRepairError, match="REPAIR_ORIGINAL_DATA_CHANGED"):
        module.apply(ctx, first["preview_sha256"])
    assert seen == []


def test_cli_argument_errors_are_fixed_json_without_private_input(repair_env, monkeypatch, capsys):
    module, _ctx, _catalog, _before, _originals = repair_env
    monkeypatch.setattr(sys, "argv", ["repair", "--unknown", "PRIVATE_ARGUMENT"])
    with pytest.raises(SystemExit) as caught:
        module.main()
    assert caught.value.code == 2
    output = capsys.readouterr().err
    assert json.loads(output)["code"] == "EMPTY_CATALOG_REPAIR_ARGUMENT_INVALID"
    assert "PRIVATE_ARGUMENT" not in output
