"""Direct synthetic TEMP checks; not pytest, live data or runtime authority.

TEMP is retained as evidence, including deliberately damaged namespaces.
All configuration/environment isolation and HTTP refusal precede importing
the candidate or its two production low-level filesystem dependencies.
"""
from __future__ import annotations

import copy
import hashlib
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import types
import urllib.request
import uuid


SOURCE = Path(__file__).resolve().parents[1] / "reno/personal_authority_files.py"
SELF = Path(__file__)
CAPTURED = {SOURCE: SOURCE.read_bytes(), SELF: SELF.read_bytes()}
BASE = None
checked = []


def deny_http(*args, **kwargs):
    raise RuntimeError("Synthetic filesystem checker refuses all model/network HTTP")


def checked_path(value):
    path = Path(value)
    assert path.is_absolute() and ".." not in path.parts
    assert BASE is not None and path != BASE and path.is_relative_to(BASE)
    return path


def bootstrap(base):
    global BASE, candidate
    BASE = Path(base)
    assert BASE.is_absolute() and BASE.parent != BASE and BASE.name.startswith("reno-authority-files-")
    assert BASE.is_dir() and not BASE.is_symlink()
    for key in tuple(os.environ):
        if key.startswith("RENO_"):
            del os.environ[key]
    config_file = checked_path(BASE / "empty-config.json")
    empty = b'{"zhipu_api_key":"","auth_token":"","sync_token":"","ask_rate_per_min":0,"budget_enabled":false}'
    if not config_file.exists():
        config_file.write_bytes(empty)
    assert config_file.read_bytes() == empty
    os.environ.update(RENO_CONFIG_FILE=str(config_file), RENO_DATA_DIR=str(checked_path(BASE / "never-default-data")),
                      RENO_QA_ROOT=str(BASE), RENO_LLM_TESTS="0")
    urllib.request.urlopen = deny_http
    urllib.request.OpenerDirector.open = deny_http
    http.client.HTTPConnection.connect = deny_http
    http.client.HTTPSConnection.connect = deny_http
    socket.create_connection = deny_http
    socket.socket.connect = deny_http
    sys.dont_write_bytecode = True
    # Standalone scripts run with data/ as sys.path[0]. This is only the known
    # source-package path; private/default data roots remain isolated above.
    sys.path.insert(0, str(SOURCE.parent.parent))
    from reno import personal_authority_files as candidate
    assert Path(candidate.__file__).resolve() == SOURCE



class Cancelled(Exception):
    pass


def note(label):
    checked.append(label)


def expect_error(code, action):
    try:
        action()
    except candidate.FilePrimitiveError as error:
        assert error.code == code, (code, error.code)
    else:
        raise AssertionError("Expected " + code)


def binding():
    return candidate._canonical({"domain_id": str(uuid.uuid4()), "facility_id": str(uuid.uuid4()),
                                 "node_id": str(uuid.uuid4()), "profile_sha256": "a" * 64})


def new_root(name, *, limits=None):
    root = checked_path(BASE / name)
    operation, raw = str(uuid.uuid4()), binding()
    result = candidate.initialize(root, init_operation_id=operation, declared_binding_raw=raw,
                                  limits=limits or candidate.DEFAULT_LIMITS)
    assert result["state"] == "ready" and result["filesystem_only"]
    return root, operation, raw


def new_bytes(path, raw):
    path = checked_path(path)
    assert not path.exists()
    with candidate.safe._Directory(path.parent, base=path.parent) as directory:
        candidate.files.publish(directory, path.name, raw)
    return path


def child(root, mode, *, timeout=0, cancel=False):
    root = checked_path(root)
    result = subprocess.run([sys.executable, "-B", str(SELF), "--child", str(BASE), str(root),
                             mode, str(timeout), "cancel" if cancel else "plain"],
                            capture_output=True, text=True, timeout=10,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert result.returncode == 0, (result.returncode, result.stderr)
    return json.loads(result.stdout)


def child_main():
    bootstrap(sys.argv[2])
    root = checked_path(sys.argv[3])
    count = 0

    def cancel():
        nonlocal count
        count += 1
        if sys.argv[6] == "cancel" and count >= 8:
            raise Cancelled("synthetic cancellation")

    try:
        with candidate.guard(root, exclusive=sys.argv[4] == "exclusive", timeout=float(sys.argv[5]),
                             check_cancelled=cancel) as held:
            held.verify()
        result = {"result": "held"}
    except candidate.FilePrimitiveError as error:
        result = {"result": error.code}
    except Cancelled:
        result = {"result": "cancelled"}
    print(json.dumps(result), flush=True)


def main():
    bootstrap(tempfile.mkdtemp(prefix="reno-authority-files-"))
    # This is a brand-new TEMP parent, so initial private ACL is authorized.
    if os.name == "nt":
        with candidate.safe._Directory(BASE, base=BASE):
            candidate.files._windows_acl(BASE, directory=True, initialize=True)
    candidate.files.require_private(BASE, directory=True)
    absent = checked_path(BASE / "absent")
    assert candidate.status(absent)["state"] == "absent" and not absent.exists()
    expect_error("FILES_IO_UNAVAILABLE", lambda: candidate.guard(absent).__enter__())
    assert not absent.exists()
    note("absent_status_and_guard_create_nothing")

    for value in ("relative", str(BASE / "x" / ".." / "y")):
        expect_error("FILES_ROOT_INVALID", lambda value=value: candidate.status(value))
    for limits in ({"max_facts": 65}, {"max_metadata_bytes": True}, {"max_fact_bytes": 32769}):
        expect_error("FILES_LIMIT_INVALID", lambda limits=limits: candidate.Limits(**limits))
    for timeout in (True, -1, 31, float("inf")):
        target = checked_path(BASE / ("bad-options-" + str(uuid.uuid4())))
        expect_error("FILES_LOCK_OPTIONS_INVALID", lambda target=target, timeout=timeout:
                     candidate.initialize(target, init_operation_id=str(uuid.uuid4()),
                                          declared_binding_raw=binding(), timeout=timeout))
        assert not target.exists()
    note("lexical_absolute_and_only_shrinking_limits_and_bounded_timeout")

    class FalsyCancellation:
        def __init__(self):
            self.calls = 0
        def __bool__(self):
            return False
        def __call__(self):
            self.calls += 1
            raise Cancelled("actual supplied false-valued cancellation")

    false_root = checked_path(BASE / "false-valued-cancellation")
    supplied = FalsyCancellation()
    try:
        candidate.initialize(false_root, init_operation_id=str(uuid.uuid4()), declared_binding_raw=binding(),
                             check_cancelled=supplied)
    except Cancelled as error:
        assert str(error) == "actual supplied false-valued cancellation"
    else:
        raise AssertionError("false-valued callback swallowed")
    assert supplied.calls == 1 and not false_root.exists()
    note("actual_false_valued_cancellation_preserved_before_any_root_creation")

    root, operation, raw = new_root("normal")
    expected_binding = hashlib.sha256(raw).hexdigest()
    result = candidate.status(root)
    assert result["state"] == "ready" and result["head_state"] == "missing"
    assert result["windows_directory_fsync_claimed"] is False
    assert set(root.iterdir()) == {root / name for name in candidate._REQUIRED}
    expect_error("FILES_DECLARED_BINDING_MISMATCH", lambda:
                 candidate.guard(root, expected_declared_binding_sha256="b" * 64).__enter__())
    note("exact_layout_private_reopen_and_declared_binding_is_only_a_digest")

    with candidate.guard(root, exclusive=True, expected_declared_binding_sha256=expected_binding) as held:
        first = candidate.put_immutable(held, "original", b'{"synthetic":1}')
        target = checked_path(root / "facts" / "original.json")
        before = candidate.files.identity(target.stat(follow_symlinks=False))
        assert candidate.put_immutable(held, "original", b'{"synthetic":1}') == first
        assert candidate.files.identity(target.stat(follow_symlinks=False)) == before
        expect_error("FILES_IMMUTABLE_CONFLICT", lambda: candidate.put_immutable(held, "original", b"different"))
        assert candidate.read_fact(held, "original").raw == b'{"synthetic":1}'
        candidate.derive_head(held)
        candidate.derive_head(held)
    expect_error("FILES_GUARD_INVALID", held.verify)
    assert candidate.status(root)["head_state"] == "matches"
    with candidate.guard(root, exclusive=True) as held:
        candidate.put_immutable(held, "second", b"original bytes")
    assert candidate.status(root)["head_state"] == "stale"
    note("immutable_same_bytes_no_write_conflict_and_derived_head_only")

    with candidate.guard(root, exclusive=False) as held:
        expect_error("FILES_EXCLUSIVE_REQUIRED", lambda: candidate.put_immutable(held, "third", b"x"))
        failures = []
        worker = threading.Thread(target=lambda: _thread_guard(held, failures))
        worker.start()
        worker.join(5)
        assert not worker.is_alive() and failures == ["FILES_GUARD_INVALID"]
        assert child(root, "shared")["result"] == "held"
        assert child(root, "exclusive")["result"] == "FILES_LOCK_BUSY"
    with candidate.guard(root, exclusive=True):
        assert child(root, "shared")["result"] == "FILES_LOCK_BUSY"
        assert child(root, "shared", timeout=2, cancel=True)["result"] == "cancelled"
    assert child(root, "exclusive")["result"] == "held"
    note("actual_child_shared_exclusive_contention_cancel_release_and_thread_owner")

    def cancelled():
        raise Cancelled("original cancellation")
    try:
        with candidate.guard(root, check_cancelled=cancelled):
            raise AssertionError("unreachable")
    except Cancelled as error:
        assert str(error) == "original cancellation"
    assert child(root, "exclusive")["result"] == "held"
    note("entry_cancellation_preserves_primary_and_releases_resources")

    # Independent real shared holder proves this acquisition remains SH even
    # after caller-facing flags and methods are overwritten.
    guarded, guarded_operation, guarded_raw = new_root("factory-owned-guard")
    with candidate.guard(guarded, exclusive=False) as held:
        assert held.lock.exclusive is False and child(guarded, "shared")["result"] == "held"
        duplicate = copy.copy(held)
        expect_error("FILES_GUARD_INVALID", duplicate.verify)
        expect_error("FILES_GUARD_INVALID", lambda: candidate.put_immutable(duplicate, "copied", b"x"))
        held._exclusive = True
        held._active = True
        held._owner = (0, 0)
        held.verify = lambda **kwargs: None
        expect_error("FILES_EXCLUSIVE_REQUIRED", lambda: candidate.put_immutable(held, "shared_write", b"synthetic"))
        assert not (guarded / "facts/shared_write.json").exists()
        assert child(guarded, "shared")["result"] == "held"
        actual_summary = candidate.FileGuard.summary(held)
        reservation_snapshot = held.reservation
        reservation_snapshot["init_operation_id"] = str(uuid.uuid4())
        reservation_snapshot["root_identity"]["inode"] = "0"
        seal_snapshot = held.seal
        seal_snapshot["binding_sha256"] = "b" * 64
        metadata_snapshot = held.metadata
        # Even bypassing a frozen observation's ordinary setter only modifies
        # a detached copy, never the real held original or expected raw bytes.
        object.__setattr__(metadata_snapshot[0], "raw", b"caller mutation")
        limits_snapshot = held.limits
        object.__setattr__(limits_snapshot, "max_facts", 10000)
        held.__dict__.update(reservation=reservation_snapshot, seal=seal_snapshot,
                             metadata=(), limits=limits_snapshot, directory=object(), facts=object(), lock=object())
        assert candidate.FileGuard.summary(held) == actual_summary
        assert actual_summary["init_operation_id"] == guarded_operation
        assert actual_summary["declared_binding_sha256"] == hashlib.sha256(guarded_raw).hexdigest()
    held._active = True
    expect_error("FILES_GUARD_INVALID", lambda: candidate.FileGuard.verify(held))
    expect_error("FILES_GUARD_INVALID", lambda: candidate.put_immutable(held, "closed", b"x"))
    note("exact_original_factory_guard_and_real_shared_mode_resist_copy_flags_method_shadowing_and_close")
    note("public_metadata_reservation_seal_limits_and_resource_observations_are_detached")

    with candidate.guard(guarded, exclusive=True) as held:
        assert held.lock.exclusive is True and child(guarded, "shared")["result"] == "FILES_LOCK_BUSY"
        candidate.put_immutable(held, "actual_exclusive", b"original bytes under actual EX")
        assert candidate.read_fact(held, "actual_exclusive").raw == b"original bytes under actual EX"

    for action in (lambda callback: candidate.status(guarded, check_cancelled=callback),
                   lambda callback: candidate.guard(guarded, check_cancelled=callback).__enter__(),
                   lambda callback: candidate.resume_initialize(guarded, init_operation_id=guarded_operation,
                       declared_binding_raw=guarded_raw, check_cancelled=callback)):
        callback = FalsyCancellation()
        try:
            action(callback)
        except Cancelled:
            pass
        else:
            raise AssertionError("cancellation endpoint swallowed false-valued callback")
        assert callback.calls == 1

    # Publish reservation, then interrupt before any following step. This is a
    # durable reserved operation with no random temporary remnant.
    reserved_root = checked_path(BASE / "reserved")
    reserved_operation, reserved_raw = str(uuid.uuid4()), binding()
    real_publish = candidate.files.publish

    def stop_after_reservation(directory, name, body, **kwargs):
        result = real_publish(directory, name, body, **kwargs)
        if name == "init-reservation.json":
            raise Cancelled("after durable reservation")
        return result

    candidate.files.publish = stop_after_reservation
    try:
        try:
            candidate.initialize(reserved_root, init_operation_id=reserved_operation, declared_binding_raw=reserved_raw)
        except Cancelled:
            pass
        else:
            raise AssertionError("reservation interruption not reached")
    finally:
        candidate.files.publish = real_publish
    assert candidate.status(reserved_root)["state"] == "reserved"
    assert not (reserved_root / "authority.lock").exists()
    expect_error("FILES_IO_UNAVAILABLE", lambda: candidate.guard(reserved_root).__enter__())
    assert not (reserved_root / "authority.lock").exists()
    original_reservation = (reserved_root / "init-reservation.json").read_bytes()
    expect_error("FILES_RESUME_MISMATCH", lambda: candidate.resume_initialize(reserved_root,
                 init_operation_id=str(uuid.uuid4()), declared_binding_raw=reserved_raw))
    expect_error("FILES_RESUME_MISMATCH", lambda: candidate.resume_initialize(reserved_root,
                 init_operation_id=reserved_operation, declared_binding_raw=binding()))
    assert candidate.resume_initialize(reserved_root, init_operation_id=reserved_operation,
                                       declared_binding_raw=reserved_raw)["state"] == "ready"
    assert (reserved_root / "init-reservation.json").read_bytes() == original_reservation
    assert candidate.resume_initialize(reserved_root, init_operation_id=reserved_operation,
                                       declared_binding_raw=reserved_raw)["state"] == "ready"
    expect_error("FILES_INIT_ROOT_EXISTS", lambda: candidate.initialize(reserved_root,
                 init_operation_id=str(uuid.uuid4()), declared_binding_raw=binding()))
    note("durable_original_uuid_body_resume_idempotence_and_no_missing_lock_creation_by_reads")

    # Actual random .preservation-write failure residues have no stage proof.
    for name, fail_target, expected_state in (("pre-reservation", "init-reservation.json", "retained_unproven"),
                                              ("post-reservation-temp", "declared-binding.json", "unavailable")):
        interrupted_root = checked_path(BASE / name)
        interrupted_operation, interrupted_raw = str(uuid.uuid4()), binding()
        real_rename = candidate.safe._rename_new

        def fail_rename(source, source_name, destination, target):
            if target == fail_target:
                raise OSError("synthetic publication interruption")
            return real_rename(source, source_name, destination, target)

        candidate.safe._rename_new = fail_rename
        try:
            expect_error("FILES_IO_UNAVAILABLE", lambda: candidate.initialize(interrupted_root,
                         init_operation_id=interrupted_operation, declared_binding_raw=interrupted_raw))
        finally:
            candidate.safe._rename_new = real_rename
        remnants = {p.name: p.read_bytes() for p in interrupted_root.iterdir() if p.is_file()}
        assert any(n.startswith(".preservation-write-") for n in remnants)
        assert candidate.status(interrupted_root)["state"] == expected_state
        expect_error("FILES_UNKNOWN_ENTRY", lambda: candidate.resume_initialize(interrupted_root,
                     init_operation_id=interrupted_operation, declared_binding_raw=interrupted_raw))
        expect_error("FILES_INIT_ROOT_EXISTS", lambda: candidate.initialize(interrupted_root,
                     init_operation_id=str(uuid.uuid4()), declared_binding_raw=binding()))
        assert remnants == {p.name: p.read_bytes() for p in interrupted_root.iterdir() if p.is_file()}
    note("real_publish_random_residue_retained_unproven_or_unavailable_never_adopted_or_deleted")

    limited, _, _ = new_root("limited", limits=candidate.Limits(max_facts=2, max_total_bytes=16))
    with candidate.guard(limited, exclusive=True, limits=candidate.Limits(max_facts=2, max_total_bytes=16)) as held:
        candidate.put_immutable(held, "one", b"12345678")
        candidate.put_immutable(held, "two", b"12345678")
        expect_error("FILES_FACT_COUNT_LIMIT", lambda: candidate.put_immutable(held, "three", b"x"))
        expect_error("FILES_FACT_BYTES_LIMIT", lambda: candidate.put_immutable(held, "huge", b"x" * 32769))
    assert candidate.status(limited, limits=candidate.Limits(max_total_bytes=15))["state"] == "unavailable"
    note("fact_count_single_bytes_and_aggregate_only_shrinking_limits")

    # A live scope rejects replacement for both objects. Across scopes, the
    # seal persists lock identity and binding original bytes/SHA separately;
    # same-byte binding replacement is not a permanent inode violation.
    replacement_observations = []
    for object_name in ("authority.lock", "declared-binding.json"):
        replaced, _, _ = new_root("replacement-" + object_name[:-5].replace(".", "-"))
        target = checked_path(replaced / object_name)
        original_raw = target.read_bytes()
        original_binding = (replaced / "declared-binding.json").read_bytes()
        original_reservation = (replaced / "init-reservation.json").read_bytes()
        original_seal_raw = (replaced / "files-seal.json").read_bytes()
        original_seal = json.loads(original_seal_raw)
        original_root_identity = original_seal["root_identity"]
        original_facts_identity = original_seal["facts_identity"]
        original_lock_path_identity = candidate.files.identity((replaced / "authority.lock").stat(follow_symlinks=False))
        outside = new_bytes(BASE / ("outside-" + str(uuid.uuid4())), original_raw)
        blocked = False
        replacement_succeeded = live_rejected = False
        live_error = exit_error = None
        expected_error = "FILES_LOCK_CHANGED" if object_name == "authority.lock" else "PERSONAL_PRESERVATION_FILE_CHANGED"
        try:
            with candidate.guard(replaced) as held:
                try:
                    os.replace(outside, target)
                except PermissionError:
                    blocked = True
                    held.verify()
                else:
                    replacement_succeeded = True
                    try:
                        held.verify()
                    except (candidate.FilePrimitiveError, candidate.files.PreservationFileError) as error:
                        assert error.code == expected_error, (object_name, error.code)
                        live_rejected, live_error = True, error.code
                    else:
                        raise AssertionError("real replacement not detected")
        except (candidate.FilePrimitiveError, candidate.files.PreservationFileError) as error:
            # An entry error, unrelated I/O, or missing live rejection cannot
            # masquerade as this actual replacement experiment succeeding.
            assert replacement_succeeded and live_rejected and not blocked
            assert error.code == expected_error, (object_name, error.code)
            exit_error = error.code
        reopened = candidate.status(replaced)
        if blocked:
            assert os.name == "nt" and not replacement_succeeded and not live_rejected
            assert reopened["state"] == "ready" and target.read_bytes() == original_raw
        elif object_name == "authority.lock":
            assert replacement_succeeded and live_rejected and exit_error == expected_error
            assert reopened["state"] == "unavailable" and reopened["code"] == "FILES_LOCK_CHANGED"
        else:
            assert replacement_succeeded and live_rejected and exit_error == expected_error
            assert reopened["state"] == "ready"
            assert (replaced / "declared-binding.json").read_bytes() == original_binding
            assert reopened["declared_binding_sha256"] == hashlib.sha256(original_binding).hexdigest()
            assert (replaced / "init-reservation.json").read_bytes() == original_reservation
            assert (replaced / "files-seal.json").read_bytes() == original_seal_raw
            with candidate.guard(replaced) as current:
                with candidate.safe._Directory(replaced, base=replaced) as actual_directory:
                    assert candidate.files.directory_identity(actual_directory) == original_root_identity
                    with candidate._open_lock(actual_directory) as lock_stream:
                        assert candidate.files.identity(os.fstat(lock_stream.fileno())) == original_seal["lock_handle_identity"]
                with candidate.safe._Directory(replaced / "facts", base=replaced / "facts") as actual_facts:
                    assert candidate.files.directory_identity(actual_facts) == original_facts_identity
                assert candidate.files.identity((replaced / "authority.lock").stat(follow_symlinks=False)) == original_lock_path_identity
                assert current.seal == original_seal
        replacement_observations.append({"object": object_name, "os_delete_denied": blocked,
            "replacement_succeeded": replacement_succeeded, "live_rejected": live_rejected,
            "live_error": live_error, "scope_exit_error": exit_error, "reopen_state": reopened["state"]})
    note("real_lock_and_original_metadata_replacement_denied_or_detected")

    # Distinct closed canonical binding bytes still contradict the original
    # seal/reservation SHA, even after every prior holder has closed.
    different, _, different_raw = new_root("different-binding-after-close")
    changed = json.loads(different_raw)
    changed["profile_sha256"] = "b" * 64
    changed_raw = candidate._canonical(changed)
    candidate._binding(changed_raw, candidate.DEFAULT_LIMITS)
    assert changed_raw != different_raw
    changed_source = new_bytes(BASE / ("different-outside-" + str(uuid.uuid4())), changed_raw)
    original_reservation = (different / "init-reservation.json").read_bytes()
    original_seal_raw = (different / "files-seal.json").read_bytes()
    os.replace(changed_source, checked_path(different / "declared-binding.json"))
    assert (different / "declared-binding.json").read_bytes() == changed_raw
    changed_status = candidate.status(different)
    assert changed_status["state"] == "unavailable" and changed_status["code"] == "FILES_SEAL_BINDING_INVALID"
    expect_error("FILES_DECLARED_BINDING_MISMATCH", lambda: candidate.guard(different,
                 expected_declared_binding_sha256=hashlib.sha256(different_raw).hexdigest()).__enter__())
    assert (different / "init-reservation.json").read_bytes() == original_reservation
    assert (different / "files-seal.json").read_bytes() == original_seal_raw
    note("different_closed_canonical_binding_after_close_rejected_by_original_sha_and_expected_binding")

    directory_root, _, _ = new_root("root-replacement")
    moved = checked_path(BASE / "displaced-root")
    blocked = False
    try:
        with candidate.guard(directory_root) as held:
            try:
                os.rename(directory_root, moved)
            except PermissionError:
                blocked = True
                held.verify()
            else:
                os.mkdir(directory_root, mode=0o700)
                try:
                    held.verify()
                except (candidate.FilePrimitiveError, candidate.safe.SyncError):
                    pass
                else:
                    raise AssertionError("actual directory replacement not detected")
    except (candidate.FilePrimitiveError, candidate.safe.SyncError):
        assert not blocked
    note("real_root_directory_replacement_denied_or_detected")

    for corruption in ("missing-lock", "missing-facts", "bad-seal", "unknown-root", "unknown-fact"):
        damaged, damaged_operation, damaged_raw = new_root(corruption)
        if corruption == "missing-lock":
            os.rename(checked_path(damaged / "authority.lock"), checked_path(BASE / ("removed-lock-" + str(uuid.uuid4()))))
        elif corruption == "missing-facts":
            os.rename(checked_path(damaged / "facts"), checked_path(BASE / ("removed-facts-" + str(uuid.uuid4()))))
        elif corruption == "bad-seal":
            checked_path(damaged / "files-seal.json").write_bytes(b"{}")
        elif corruption == "unknown-root":
            new_bytes(damaged / "unknown.json", b"unknown")
        else:
            new_bytes(damaged / "facts" / "unknown.bin", b"unknown")
        before = sorted(str(p.relative_to(damaged)) for p in damaged.rglob("*"))
        assert candidate.status(damaged)["state"] == "unavailable"
        try:
            candidate.resume_initialize(damaged, init_operation_id=damaged_operation, declared_binding_raw=damaged_raw)
        except candidate.FilePrimitiveError:
            pass
        else:
            raise AssertionError("sealed damage was repaired")
        assert before == sorted(str(p.relative_to(damaged)) for p in damaged.rglob("*"))
    note("sealed_missing_bad_unknown_objects_never_reconstructed_as_empty")

    acl_root, _, _ = new_root("private-boundary")
    if os.name == "nt":
        result = subprocess.run(["icacls", str(checked_path(acl_root)), "/grant", "*S-1-1-0:(RX)"],
            capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        assert result.returncode == 0
        before = subprocess.run(["icacls", str(acl_root)], capture_output=True, timeout=10,
                                creationflags=subprocess.CREATE_NO_WINDOW).stdout
    else:
        os.chmod(checked_path(acl_root), 0o755)
        before = acl_root.stat().st_mode
    assert candidate.status(acl_root)["state"] == "unavailable"
    expect_error("FILES_INIT_ROOT_EXISTS", lambda: candidate.initialize(acl_root,
                 init_operation_id=str(uuid.uuid4()), declared_binding_raw=binding()))
    if os.name == "nt":
        after = subprocess.run(["icacls", str(acl_root)], capture_output=True, timeout=10,
                               creationflags=subprocess.CREATE_NO_WINDOW).stdout
    else:
        after = acl_root.stat().st_mode
    assert before == after
    note("existing_private_permission_failure_never_repaired")

    bad_metadata = checked_path(BASE / "bad-canonical")
    expect_error("FILES_METADATA_INVALID", lambda: candidate.initialize(bad_metadata,
                 init_operation_id=str(uuid.uuid4()), declared_binding_raw=b'{"unknown":true}'))
    assert not bad_metadata.exists()
    assert all(path.read_bytes() == raw for path, raw in CAPTURED.items())
    note("closed_metadata_and_candidate_checker_bytes_unchanged")
    print(json.dumps({"scope": "synthetic_filesystem_primitives_only", "platform": sys.platform,
        "candidate_sha256": hashlib.sha256(CAPTURED[SOURCE]).hexdigest(),
        "checker_sha256": hashlib.sha256(CAPTURED[SELF]).hexdigest(),
        "checks": checked, "check_groups": len(checked), "evidence_temp_root": str(BASE),
        "live_replacement_observations": replacement_observations,
        "source_files_unchanged": True, "module_integrated": True, "pytest_run": False, "real_defaults_used": False,
        "runtime_authority_granted": False, "network_http_refused": True,
        "windows_directory_fsync_claimed": False}, sort_keys=True), flush=True)


def _thread_guard(held, result):
    try:
        held.verify()
    except candidate.FilePrimitiveError as error:
        result.append(error.code)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        child_main()
    else:
        main()
