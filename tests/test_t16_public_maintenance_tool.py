"""Real isolated package inventories used by the future same-schema reload tool."""
import hashlib
import importlib.util
from pathlib import Path
import sys
import uuid

import pytest

from reno import personal_preservation as producer, storage_lifecycle as lifecycle
from reno import personal_preservation_files as private_files, personal_preservation_source
from tests.test_personal_preservation import library


@pytest.fixture
def tool(library, monkeypatch):
    profile, _project, _config = library
    path = Path(__file__).resolve().parents[1] / "data/reload_t16_public_operations.py"
    if not Path(path).is_file():
        pytest.skip("Historical maintenance tool is absent from this checkout")
    spec = importlib.util.spec_from_file_location("reno_t16_public_maintenance_test_module", path)
    module = importlib.util.module_from_spec(spec)
    # Keep the imported source visible to the terminal fingerprint collector;
    # per-test ROOT/DATA substitutions below still unwind with the fixture.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    root = profile.database.parent.parent
    monkeypatch.setattr(module, "ROOT", root)
    monkeypatch.setattr(module, "DATA", profile.database.parent)

    def inside(path, *, existing=False):
        path = Path(path).absolute()
        assert path.is_relative_to(root)
        if existing:
            assert path.exists() and not path.is_symlink()
        return path

    def file_facts(path):
        path = inside(path, existing=True)
        info = path.stat()
        assert path.is_file() and info.st_nlink == 1
        return {"path": path.relative_to(root).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "byte_size": info.st_size, "device": str(info.st_dev), "inode": str(info.st_ino),
                "mtime_ns": str(info.st_mtime_ns), "ctime_ns": str(info.st_ctime_ns)}

    return profile, module, {"inside": inside, "file_facts": file_facts, "MAX_FILES": 100_000}


def actual_package(tool):
    profile, module, helpers = tool
    operation = str(uuid.uuid4())
    with lifecycle.exclusive(profile):
        producer.create_locked(profile, operation_id=operation)
        package = module.package_facts(helpers, profile, operation)
    return profile, module, helpers, package


def test_inventory_contains_exact_raw_bodies_first_anchors_and_payload_attempt_directories(tool):
    _profile, module, helpers, package = actual_package(tool)
    observed = module.trees(helpers)
    assert {item["path"] for tree in observed for item in tree["files"]} == {
        item["path"] for item in package["files"]}
    assert {item["path"] for tree in observed for item in tree["directories"]} == {
        item["path"] for item in package["directories"]}
    assert any("/payload/" in item["path"] for item in package["directories"])
    assert any(".copy-index/" in item["path"] for item in package["files"])
    assert package["verified_ready"] is True


def test_comparison_accepts_only_original_files_and_the_exact_new_package(tool, monkeypatch):
    profile, module, helpers, package = actual_package(tool)
    original = helpers["file_facts"](profile.database.parent / "node-identity.json")
    before = {"sql": {"unchanged": True}, "files": [original], "preservation_trees": []}
    current = {"sql": before["sql"], "files": [original, *package["files"]],
               "preservation_trees": module.trees(helpers)}
    monkeypatch.setattr(module, "capture", lambda *args: current)
    assert module.compare_original(helpers, {}, profile, before, package) == current
    extra = {"path": "data/unknown-evidence", "device": "1", "inode": "2"}
    current["preservation_trees"][0]["directories"].append(extra)
    with pytest.raises(module.PublicReloadError) as caught:
        module.compare_original(helpers, {}, profile, before, package)
    assert str(caught.value) == "PRESERVATION_DIRECTORY_SET_CHANGED"


def test_package_fact_reads_require_exact_original_sha_and_preserve_payload_identity(tool):
    profile, module, helpers, package = actual_package(tool)
    before = {item["path"]: helpers["file_facts"](module.ROOT / item["path"]) for item in package["files"]}
    with lifecycle.shared(profile):
        repeated = module.package_facts(helpers, profile, package["operation_id"],
                                         expected_request_sha256=package["request_sha256"])
        assert repeated == package
        with pytest.raises(ValueError):
            module.package_facts(helpers, profile, package["operation_id"], expected_request_sha256="0" * 64)
    assert {key: helpers["file_facts"](module.ROOT / key) for key in before} == before


def test_maintenance_personal_sql_temp_is_private_before_first_byte_and_keeps_original_sql(tool, monkeypatch):
    profile, module, helpers = tool
    original_raw = profile.database.read_bytes()
    initialized = []
    original_initialize = personal_preservation_source._PersonalPrivateDirectory.initialize_file

    def initialize(temporary, path):
        assert path.stat().st_size == 0
        original_initialize(temporary, path)
        private_files.require_private(temporary.path, directory=True)
        private_files.require_private(path, directory=False)
        assert path.stat().st_size == 0
        initialized.append(path.name)

    monkeypatch.setattr(personal_preservation_source._PersonalPrivateDirectory, "initialize_file", initialize)
    with lifecycle.shared(profile), module.personal_private_readonly(helpers, profile) as con:
        assert con.execute("SELECT count(*) FROM renovation_project").fetchone()[0] == 1
        assert con.execute("PRAGMA query_only").fetchone()[0] == 1
        assert con.execute("PRAGMA trusted_schema").fetchone()[0] == 0
    assert "image.sqlite" in initialized and profile.database.read_bytes() == original_raw


def test_only_cloned_inspectors_use_personal_private_adapter_without_changing_old_globals(tool):
    profile, module, helpers = tool

    def original_reader(*args, **kwargs):
        raise AssertionError("Original inspector globals must not be used or replaced")

    namespace = {"private_readonly": original_reader}
    exec("def capture(h, value):\n    with private_readonly(h, value) as con:\n"
         "        return con.execute('SELECT count(*) FROM renovation_project').fetchone()[0]\n"
         "def worker(h, value):\n    with private_readonly(h, value) as con:\n"
         "        return con.execute('PRAGMA query_only').fetchone()[0]\n", namespace)
    previous = {name: namespace[name] for name in ("capture", "worker")}
    previous["other_fact"] = object()
    adapted = module.adapt_private_inspectors(previous)
    assert adapted["other_fact"] is previous["other_fact"]
    for name in ("capture", "worker"):
        assert adapted[name].__code__ is previous[name].__code__
        assert previous[name].__globals__["private_readonly"] is original_reader
        assert adapted[name].__globals__["private_readonly"] is module.personal_private_readonly
    with lifecycle.shared(profile):
        assert adapted["capture"](helpers, profile) == 1
        assert adapted["worker"](helpers, profile) == 1
    assert namespace["private_readonly"] is original_reader
