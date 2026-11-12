"""Default pytest runs never read the developer's credentials or mutable data.

Isolation is installed before test collection (some modules import the app at
module scope). Real-model probes require a separately authorized harness; this
suite deliberately disables them and refuses unstubbed model HTTP requests.
"""
import copyreg
import json
import os
from pathlib import Path
import shutil
import subprocess
import sysconfig

import pytest


@pytest.fixture(scope="session")
def artificial_tls_certificate(tmp_path_factory):
    """Use public CPython test data or generate an isolated, untrusted test key."""
    public_fixture = Path(sysconfig.get_path("stdlib")) / "test/certdata/keycert.pem"
    if public_fixture.is_file():
        return public_fixture
    openssl = os.environ.get("OPENSSL_TEST_BINARY") or shutil.which("openssl")
    if not openssl:
        pytest.skip("TLS integration needs OpenSSL or CPython's public test certificate")
    root = tmp_path_factory.mktemp("artificial-tls")
    configuration = root / "openssl.cnf"
    configuration.write_text("[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n"
                             "[dn]\nCN=reno-synthetic.test\n[ext]\nbasicConstraints=critical,CA:TRUE\n"
                             "subjectAltName=DNS:reno-synthetic.test,IP:127.0.0.1\n", encoding="ascii")
    certificate, key = root / "certificate.pem", root / "key.pem"
    result = subprocess.run([openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                             "-config", str(configuration), "-out", str(certificate), "-keyout", str(key)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert result.returncode == 0, "Unable to generate an artificial local TLS certificate"
    combined = root / "keycert.pem"
    combined.write_bytes(certificate.read_bytes() + key.read_bytes())
    return combined


def pytest_sessionstart(session):
    root = session.config._tmp_path_factory.getbasetemp()
    local = root / "config.test.json"
    local.write_text(json.dumps({"zhipu_api_key": "", "auth_token": "",
                                 "sync_token": "", "ask_rate_per_min": 0}), encoding="utf-8")
    # RENO_* overrides can include credentials, provider URLs and node role.
    session.config._reno_saved_env = {k: v for k, v in os.environ.items()
                                     if k.startswith("RENO_")}
    for key in list(os.environ):
        if key.startswith("RENO_"):
            os.environ.pop(key)
    os.environ.update(RENO_QA_ROOT=str(root), RENO_CONFIG_FILE=str(local),
                      RENO_DATA_DIR=str(root / "data"), RENO_LLM_TESTS="0")
    # Spawn pickles profiles and copyreg lazily adds a class-level cache.
    # Establish it before collection captures exact source class dictionaries.
    from reno.storage_lifecycle import StorageProfile
    copyreg._slotnames(StorageProfile)


def pytest_sessionfinish(session):
    for key in list(os.environ):
        if key.startswith("RENO_"):
            os.environ.pop(key)
    os.environ.update(getattr(session.config, "_reno_saved_env", {}))


@pytest.fixture(scope="module", autouse=True)
def isolated_test_config(tmp_path_factory):
    """Each module owns fresh config/media; existing module DB fixtures still work."""
    from reno import config, db, llm

    root = tmp_path_factory.mktemp("config-isolation")
    local = root / "config.test.json"
    local.write_text(json.dumps({"zhipu_api_key": "", "auth_token": "",
                                 "sync_token": "", "ask_rate_per_min": 0}), encoding="utf-8")
    mp = pytest.MonkeyPatch()
    mp.setenv("RENO_CONFIG_FILE", str(local))
    mp.setenv("RENO_DATA_DIR", str(root / "data"))
    for name, path in {"DATA": root / "data", "MEDIA": root / "media",
                       "ORIG": root / "media" / "originals",
                       "AUDIO": root / "media" / "audio",
                       "FRAMES": root / "media" / "frames",
                       "FRAME_CACHE": root / "frames_cache"}.items():
        mp.setattr(config, name, path)
    mp.setattr(config, "_cache", None)
    db.initialize()
    from reno import storage_admin
    storage_admin.setup()

    def refuse_model_http(*args, **kwargs):
        raise RuntimeError("Unstubbed model HTTP is disabled in the isolated pytest suite")

    mp.setattr(llm, "_post", refuse_model_http)
    mp.setattr(llm, "_post_anthropic", refuse_model_http)
    yield local
    _stop_test_workers()
    mp.undo()


@pytest.fixture(autouse=True)
def reset_test_config(isolated_test_config):
    """Model/auth/rate-limit changes in one test must not affect the next."""
    from reno import config
    isolated_test_config.write_text(json.dumps({"zhipu_api_key": "", "auth_token": "",
                                               "sync_token": "", "ask_rate_per_min": 0}), encoding="utf-8")
    config._cache = None
    yield
    _stop_test_workers()
    config._cache = None


def _stop_test_workers():
    # A test's worker must terminate before fixtures restore config.DATA.
    # Otherwise a late task can cross into the next module's database.
    from reno import worker
    if not worker.stop_running(timeout=5):
        raise RuntimeError("Test worker is still running; refusing to restore data paths")


@pytest.fixture
def released16_env(tmp_path, monkeypatch):
    """Own the complete original16 producer lifetime for historical fixtures."""
    from tests.schema_fixtures import released16
    from tests.test_sync_agent import env as original_env

    with released16(monkeypatch):
        yield from original_env.__wrapped__(tmp_path, monkeypatch)
