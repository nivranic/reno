"""A20: subprocess proof of config, media and model isolation in QA."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

import pytest

REPO = Path(__file__).resolve().parents[1]


def clean_env(root: Path, local: Path | None = None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("RENO_")}
    env.update(RENO_QA_ROOT=str(root), RENO_CONFIG_FILE=str(local or root / "config.test.json"),
               RENO_DATA_DIR=str(root / "data"), RENO_LLM_TESTS="0")
    return env


def run_python(code: str, env: dict):
    return subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env,
                          capture_output=True, text=True, encoding="utf-8", timeout=30)


def test_explicit_config_read_write_same_file_and_all_paths_isolated(tmp_path):
    root = tmp_path / "qa"
    root.mkdir()
    local = root / "config.test.json"
    local.write_text(json.dumps({"zhipu_api_key": "", "test_marker": "before"}), encoding="utf-8")
    # A decoy default file proves no fallback without ever opening real secrets.
    decoy = root / "decoy"
    decoy.mkdir()
    default = decoy / "config.local.json"
    default.write_text('{"test_marker":"must-not-change"}', encoding="utf-8")
    env = clean_env(root)
    env["QA_DECOY_ROOT"] = str(decoy)
    result = run_python("""
import json, os
from pathlib import Path
from reno import config
config.ROOT = Path(os.environ['QA_DECOY_ROOT'])
assert config.get('test_marker') == 'before'
config.set_local({'test_marker': 'after'})
assert config.get('test_marker') == 'after'
root = Path(os.environ['RENO_QA_ROOT']).resolve()
for name in ('DATA', 'MEDIA', 'FRAMES', 'ORIG', 'AUDIO', 'FRAME_CACHE'):
    assert getattr(config, name).resolve().is_relative_to(root)
    assert not getattr(config, name).exists(), 'configuration created a storage directory'
print('isolated')
""", env)
    assert result.returncode == 0, result.stderr
    assert json.loads(local.read_text(encoding="utf-8"))["test_marker"] == "after"
    assert json.loads(default.read_text(encoding="utf-8"))["test_marker"] == "must-not-change"


def test_database_initialization_does_not_precreate_media_through_personal_seed(tmp_path):
    root = tmp_path / "qa"
    root.mkdir()
    (root / "config.test.json").write_text('{"node_role":"all"}', encoding="utf-8")
    result = run_python("""
from reno import config, db
db.initialize()
assert config.db_path().is_file()
assert (config.DATA / 'node-identity.json').is_file()
for name in ('MEDIA', 'FRAMES', 'ORIG', 'AUDIO', 'FRAME_CACHE'):
    assert not getattr(config, name).exists(), 'DB schema setup precreated a managed root'
""", clean_env(root))
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("contents", [None, "{broken", "[]"])
@pytest.mark.parametrize("operation", ["config.get()", "config.set_local({'x': 1})", "config.db_path()"])
def test_missing_or_malformed_explicit_config_fails_without_fallback(tmp_path, contents, operation):
    root = tmp_path / "qa"
    root.mkdir()
    local = root / "config.test.json"
    if contents is not None:
        local.write_text(contents, encoding="utf-8")
    result = run_python("from reno import config; " + operation, clean_env(root))
    assert result.returncode != 0
    assert not (root / "data").exists()
    if contents is None:
        assert not local.exists()
    else:
        assert local.read_text(encoding="utf-8") == contents


@pytest.mark.parametrize("escape", ["config", "data", "no-explicit-config"])
def test_qa_paths_cannot_escape_root(tmp_path, escape):
    root = tmp_path / "qa"
    root.mkdir()
    local = root / "config.test.json"
    local.write_text("{}", encoding="utf-8")
    external = tmp_path / "outside.json"
    external.write_text("{}", encoding="utf-8")
    env = clean_env(root)
    if escape == "config":
        env["RENO_CONFIG_FILE"] = str(external)
    elif escape == "data":
        env["RENO_DATA_DIR"] = str(tmp_path / "outside-data")
    else:
        env.pop("RENO_CONFIG_FILE")
    result = run_python("from reno import config; config.get()", env)
    assert result.returncode != 0
    assert "QA" in result.stderr
    assert not (tmp_path / "outside-data").exists()


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@pytest.fixture(scope="module")
def prepared():
    # The real preparer requires an external, disposable instance root.
    with tempfile.TemporaryDirectory(prefix="reno-qa-preparer-") as temporary:
        root = Path(temporary) / "instance"
        result = subprocess.run([sys.executable, str(REPO / "scripts/prepare_product_fixture.py"),
                                 "--root", str(root), "--port", str(free_port())],
                                cwd=REPO, capture_output=True, text=True, encoding="utf-8", timeout=60)
        assert result.returncode == 0, result.stderr
        manifest = root / "qa-manifest.json"
        # API isolation selects classic rendering. Browser fixtures keep
        # prepare's default web_ui=react.
        local_file = root / "config.test.json"
        local = json.loads(local_file.read_text(encoding="utf-8"))
        local["web_ui"] = "classic"
        local_file.write_text(json.dumps(local), encoding="utf-8")
        yield manifest, json.loads(manifest.read_text(encoding="utf-8"))


def test_prepare_creates_synthetic_media_20_questions_and_refuses_overwrite(prepared):
    manifest, data = prepared
    root = manifest.parent
    assert data["question_count"] == 20 and data["atom_count"] == 20
    assert (root / "media/originals/qa-test-pattern.mp4").stat().st_size > 1000
    fixtures = json.loads((root / "product_questions.json").read_text(encoding="utf-8"))
    ids = {atom["id"] for atom in fixtures["atoms"]}
    assert len({item["id"] for item in fixtures["questions"]}) == 20
    for item in fixtures["questions"]:
        assert item["expected_atom_ids"] and set(item["expected_atom_ids"]) <= ids
        assert item["required_conditions"] and item["forbidden_claims"]
    from reno.dict import taxonomy
    from reno.atomize import _validate_price
    tax = taxonomy()
    for atom in fixtures["atoms"]:
        assert atom["dimension"] in tax["dimensions"]
        assert atom["category"] in tax["categories"] and atom["space"] in tax["spaces"]
        assert set(atom["conditions"]) <= set(tax["condition_keys"])
        assert isinstance(atom["exceptions"], list) and atom["exceptions"]
        assert len(_validate_price(atom.get("prices"))) == len(atom.get("prices", []))
    local = json.loads((root / "config.test.json").read_text(encoding="utf-8"))
    assert local["zhipu_api_key"] == "" and local["ytdlp_cookies"] == ""
    result = subprocess.run([sys.executable, str(REPO / "scripts/prepare_product_fixture.py"),
                             "--root", str(root)], cwd=REPO, capture_output=True, timeout=30)
    assert result.returncode != 0
    assert json.loads(manifest.read_text(encoding="utf-8")) == data


def request_qa(data: dict, path: str, payload=None):
    req = urllib.request.Request(data["base_url"] + path,
                                 data=json.dumps(payload).encode() if payload is not None else None,
                                 headers={"X-Reno-Token": data["auth_token"], "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, response.headers, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers, json.loads(exc.read())


def test_actual_service_process_identifies_instance_stubs_models_and_isolates_writes(prepared):
    manifest, data = prepared
    env = {**os.environ, "RENO_ZHIPU_API_KEY": "artificial-inherited-sentinel",
           "RENO_CONFIG_FILE": str(manifest.parent / "must-not-read.json"),
           "RENO_DATA_DIR": str(manifest.parent / "must-not-create")}
    with (manifest.parent / "service-test.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen([sys.executable, str(REPO / "scripts/serve_product_fixture.py"),
                                    "--manifest", str(manifest)], cwd=REPO, env=env,
                                   stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 30
            while True:
                assert process.poll() is None, (manifest.parent / "service-test.log").read_text(encoding="utf-8")
                try:
                    status, headers, body = request_qa(data, "/api/health")
                    break
                except (OSError, urllib.error.URLError):
                    if time.monotonic() > deadline:
                        pytest.fail("isolated QA server did not become healthy")
                    time.sleep(0.1)
            assert status == 200 and body["ok"]
            assert headers["X-Reno-QA-Instance"] == data["qa_instance_id"]
            assert headers["X-Reno-QA-Fixture"] == data["fixture_version"]
            status, _, body = request_qa(data, "/api/ask", {"question": "卫生间防水怎么做？"})
            assert status == 200 and body["refs"] and not body.get("degraded")
            assert "人工验收样本" in body["answer"]
            status, _, body = request_qa(data, "/api/ask", {"question": "防水未登记的问题是什么？"})
            assert status == 500 and body["detail"] == "qa_model_stub_miss"
            status, _, _ = request_qa(data, "/api/config/model", {"model": "GLM-5.3-Flash"})
            assert status == 200
            saved = json.loads(Path(data["config_file"]).read_text(encoding="utf-8"))
            assert saved["atomize_model"] == "GLM-5.3-Flash" and saved["zhipu_api_key"] == ""
            assert not (manifest.parent / "must-not-create").exists()
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


def test_unknown_chat_json_fails_in_separate_process(prepared):
    manifest, data = prepared
    env = clean_env(manifest.parent)
    env["QA_MANIFEST"] = str(manifest)
    result = run_python("""
import os
from pathlib import Path
from scripts.serve_product_fixture import load_manifest, configure_environment, install_model_stubs
manifest = load_manifest(Path(os.environ['QA_MANIFEST']))
configure_environment(manifest)
install_model_stubs(manifest)
from reno import llm
llm.chat_json([{'role':'user','content':'unregistered request'}])
""", env)
    assert result.returncode != 0 and "qa_model_stub_miss" in result.stderr


def test_service_refuses_occupied_port(prepared):
    manifest, data = prepared
    with socket.socket() as listener:
        if os.name == "nt":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            # The prior real-service test used this same prepared origin. Its
            # TIME_WAIT connections must not prevent creating our live owner.
            # No SO_REUSEPORT: the child must still fail against this listener.
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", data["port"]))
        listener.listen()
        result = subprocess.run([sys.executable, str(REPO / "scripts/serve_product_fixture.py"),
                                 "--manifest", str(manifest)], cwd=REPO,
                                capture_output=True, timeout=15)
        assert result.returncode != 0
        assert b"listener.bind" in result.stderr
        # An unrelated startup error cannot substitute for a live port owner.
        listener.settimeout(2)
        with socket.create_connection(("127.0.0.1", data["port"]), timeout=2):
            accepted, _ = listener.accept()
            accepted.close()


def test_service_refuses_missing_explicit_config_before_start(prepared):
    manifest, data = prepared
    local = Path(data["config_file"])
    saved = local.read_bytes()
    local.unlink()
    try:
        result = subprocess.run([sys.executable, str(REPO / "scripts/serve_product_fixture.py"),
                                 "--manifest", str(manifest)], cwd=REPO, capture_output=True, timeout=15)
        assert result.returncode != 0
        assert not local.exists()
    finally:
        local.write_bytes(saved)
