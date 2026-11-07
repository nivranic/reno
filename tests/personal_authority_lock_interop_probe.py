"""Actual mixed storage/authority locks against the installed modules.

This standalone probe only accesses its own retained TEMP files. Both import
orders, real child contention, and independent release are exercised. It never
opens SQL or contacts a network; filesystem guards grant no domain authority.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid


ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
CAPTURED = {path: path.read_bytes() for path in (
    SELF, ROOT / "reno/personal_authority_files.py",
    ROOT / "reno/storage_lifecycle.py", ROOT / "reno/sync_media.py",
    ROOT / "reno/personal_preservation_files.py",
)}
sql_attempts = network_attempts = 0


def audit(event, args):
    global sql_attempts, network_attempts
    if event == "sqlite3.connect":
        sql_attempts += 1
        raise RuntimeError("Mixed lock probe does not open databases")
    if event in {"socket.connect", "socket.bind", "socket.getaddrinfo"}:
        network_attempts += 1
        raise RuntimeError("Mixed lock probe refuses all network")


sys.addaudithook(audit)


def bootstrap(base, order):
    assert order in {"lifecycle-first", "authority-first"}
    assert base.is_absolute() and base.name.startswith("reno-authority-files-interop-")
    assert base.is_dir() and not base.is_symlink()
    for name in tuple(os.environ):
        if name.startswith("RENO_"):
            del os.environ[name]
    config_file = base / "empty-config.json"
    empty = b'{"zhipu_api_key":"","auth_token":"","sync_token":"","ask_rate_per_min":0,"budget_enabled":false}'
    if not config_file.exists():
        with config_file.open("xb") as handle:
            handle.write(empty)
    assert config_file.read_bytes() == empty
    os.environ.update(RENO_CONFIG_FILE=str(config_file),
                      RENO_DATA_DIR=str(base / "never-default-data"),
                      RENO_QA_ROOT=str(base), RENO_LLM_TESTS="0")
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT))
    if order == "lifecycle-first":
        from reno import storage_lifecycle as lifecycle
        from reno import personal_authority_files as authority
    else:
        from reno import personal_authority_files as authority
        from reno import storage_lifecycle as lifecycle
    assert Path(authority.__file__).resolve() == ROOT / "reno/personal_authority_files.py"
    assert Path(lifecycle.__file__).resolve() == ROOT / "reno/storage_lifecycle.py"
    return authority, lifecycle


def profile(base, lifecycle):
    return lifecycle.StorageProfile(base / "storage/data/reno.db", base / "storage/media",
                                    base / "storage/reports", base / "storage/checkpoints")


def unchanged():
    assert all(path.read_bytes() == before for path, before in CAPTURED.items())
    assert sql_attempts == network_attempts == 0


def child(base, order, mode):
    authority, lifecycle = bootstrap(base, order)
    assert mode in {"lifecycle-exclusive", "authority-shared", "authority-exclusive"}
    try:
        if mode == "lifecycle-exclusive":
            with lifecycle.exclusive(profile(base, lifecycle), timeout=0) as guard:
                guard.verify()
        else:
            with authority.guard(base / "authority", exclusive=mode == "authority-exclusive", timeout=0) as guard:
                authority._verify_guard(guard)
        result = "held"
    except authority.FilePrimitiveError as error:
        result = error.code
    except lifecycle.StorageLifecycleError as error:
        result = error.code
    unchanged()
    print(json.dumps({"result": result, "sql_attempts": sql_attempts,
                      "network_attempts": network_attempts}), flush=True)


def run_child(base, order, mode):
    result = subprocess.run(
        [sys.executable, "-u", "-B", str(SELF), "--child", str(base), order, mode],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    value = json.loads(result.stdout.splitlines()[-1])
    assert value["sql_attempts"] == value["network_attempts"] == 0
    return value["result"]


def main(order):
    base = Path(tempfile.mkdtemp(prefix="reno-authority-files-interop-"))
    authority, lifecycle = bootstrap(base, order)
    with authority.safe._Directory(base, base=base):
        if os.name == "nt":
            authority.files._windows_acl(base, directory=True, initialize=True)
        else:
            base.chmod(0o700)
    selected = profile(base, lifecycle)
    lifecycle.initialize(selected)
    binding = json.dumps({"domain_id": str(uuid.uuid4()), "facility_id": str(uuid.uuid4()),
                          "node_id": str(uuid.uuid4()), "profile_sha256": "a" * 64},
                         sort_keys=True, separators=(",", ":")).encode()
    authority.initialize(base / "authority", init_operation_id=str(uuid.uuid4()),
                         declared_binding_raw=binding)
    observations = []
    with lifecycle.shared(selected) as storage_guard:
        storage_guard.verify()
        with authority.guard(base / "authority", exclusive=True) as authority_guard:
            authority.put_immutable(authority_guard, "actual_interop", b"native-both-locks")
            assert run_child(base, order, "authority-shared") == "FILES_LOCK_BUSY"
            assert run_child(base, order, "authority-exclusive") == "FILES_LOCK_BUSY"
            assert run_child(base, order, "lifecycle-exclusive") == "STORAGE_BUSY"
            observations.append("both_original_guards_held_actual_children_denied")
        storage_guard.verify()
        with authority.guard(base / "authority") as authority_guard:
            assert authority.read_fact(authority_guard, "actual_interop").raw == b"native-both-locks"
            assert run_child(base, order, "authority-shared") == "held"
            assert run_child(base, order, "authority-exclusive") == "FILES_LOCK_BUSY"
        assert run_child(base, order, "authority-exclusive") == "held"
        assert run_child(base, order, "lifecycle-exclusive") == "STORAGE_BUSY"
        observations.append("authority_release_preserves_actual_lifecycle_lock")
    assert run_child(base, order, "authority-exclusive") == "held"
    assert run_child(base, order, "lifecycle-exclusive") == "held"
    with lifecycle.exclusive(selected) as storage_guard:
        with authority.guard(base / "authority", exclusive=True) as authority_guard:
            storage_guard.verify()
            authority._verify_guard(authority_guard, writing=True)
    if os.name == "nt":
        assert authority._lock_kernel is not lifecycle._kernel
        assert authority._lock_kernel.LockFileEx is not lifecycle._kernel.LockFileEx
        assert authority._lock_kernel.UnlockFileEx is not lifecycle._kernel.UnlockFileEx
        assert lifecycle._kernel.LockFileEx.argtypes[-1] is authority.safe.ctypes.POINTER(lifecycle._Overlapped)
        assert authority._lock_kernel.LockFileEx.argtypes[-1] is authority.safe.ctypes.POINTER(authority._LockOverlapped)
        assert authority.safe.ctypes.sizeof(authority._LockOverlapped) == authority.safe.ctypes.sizeof(lifecycle._Overlapped)
    unchanged()
    print(json.dumps({"module_integrated": True, "order": order,
        "actual_mixed_lock_semantics": True, "observations": observations,
        "all_after_release_children_acquired": True, "sql_attempts": sql_attempts,
        "network_attempts": network_attempts, "source_files_unchanged": True,
        "runtime_authority_granted": False,
        "source_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(raw).hexdigest()
                          for path, raw in CAPTURED.items()}, "temp": str(base)}, sort_keys=True), flush=True)


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "--child":
        child(Path(sys.argv[2]), sys.argv[3], sys.argv[4])
    else:
        assert len(sys.argv) == 2
        main(sys.argv[1])
