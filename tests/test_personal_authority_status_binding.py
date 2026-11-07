"""Real status control flow with explicit metadata/lock substitutes.

These cases do not establish native no-follow, same-FD or OS lock behavior.
"""
from contextlib import contextmanager, nullcontext
import hashlib
from types import SimpleNamespace

import pytest

from reno import personal_authority_files as files


NODE = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
PROFILE = "a" * 64


@pytest.fixture
def window(tmp_path, monkeypatch):
    root = tmp_path / ".reno-personal-authority-test"
    root.mkdir()
    binding_raw = files._canonical({"domain_id": NODE, "facility_id": OTHER,
                                    "node_id": NODE, "profile_sha256": PROFILE})
    digest = hashlib.sha256(binding_raw).hexdigest()
    identity = {"device": "1", "inode": "2"}
    reservation_raw = files._canonical({"format_version": 1, "init_operation_id": NODE,
        "binding_sha256": digest, "root_identity": identity})
    options = {"ready": False, "binding": True, "guard_digest": digest}
    events = []

    class Directory:
        def __init__(self, path, **kwargs):
            self.path = path
        def __enter__(self):
            events.append("directory_enter")
            return self
        def __exit__(self, *args):
            events.append("directory_exit")
        def entry_stat(self, name):
            return object()

    def scan(directory, **kwargs):
        names = {"init-reservation.json"}
        if options["binding"]:
            names.add("declared-binding.json")
        if options["ready"]:
            names.add("files-seal.json")
        return names

    def hold(stack, path, maximum, check):
        check()
        events.append("hold:" + path.name)
        raw = reservation_raw if path.name == "init-reservation.json" else binding_raw
        return SimpleNamespace(raw=raw, sha256=hashlib.sha256(raw).hexdigest(),
                               verify=lambda: events.append("verify:" + path.name))

    @contextmanager
    def guard(path, **kwargs):
        events.append(("guard", kwargs["expected_declared_binding_sha256"]))
        expected = kwargs["expected_declared_binding_sha256"]
        if expected is not None and expected != options["guard_digest"]:
            raise files.FilePrimitiveError("FILES_DECLARED_BINDING_MISMATCH")
        try:
            yield object()
        finally:
            events.append("guard_exit")

    summary = {"state": "ready", "filesystem_only": True, "init_operation_id": NODE,
               "declared_binding_sha256": digest, "windows_directory_fsync_claimed": False}
    acquisition = SimpleNamespace(directory=Directory(root), metadata=(
        SimpleNamespace(path=root / "declared-binding.json", raw=binding_raw),))
    monkeypatch.setattr(files, "_filesystem", nullcontext)
    monkeypatch.setattr(files.safe, "_Directory", Directory)
    monkeypatch.setattr(files.files, "require_private", lambda *a, **kw: None)
    monkeypatch.setattr(files.files, "directory_identity", lambda d: identity)
    monkeypatch.setattr(files, "_scan", scan)
    monkeypatch.setattr(files, "_hold", hold)
    monkeypatch.setattr(files, "guard", guard)
    monkeypatch.setattr(files, "_acquisition", lambda held: acquisition)
    monkeypatch.setattr(files, "_facts_snapshot", lambda held, stack: ([], 0))
    monkeypatch.setattr(files.FileGuard, "summary", lambda held: dict(summary))
    return SimpleNamespace(root=root, options=options, events=events, digest=digest,
                           acquisition=acquisition)


def expected(window):
    return dict(expected_declared_binding_sha256=window.digest,
                expected_node_id=NODE, expected_profile_sha256=PROFILE)


@pytest.mark.parametrize("ready", [False, True])
def test_matching_binding_observed_and_resources_exit(window, ready):
    window.options["ready"] = ready
    report = files.status(window.root, **expected(window))
    assert report["state"] == ("ready" if ready else "reserved")
    if ready:
        assert window.events[-1] == "guard_exit"
        assert ("guard", window.digest) in window.events
        assert "hold:declared-binding.json" not in window.events
    else:
        assert window.events[-1] == "directory_exit"


@pytest.mark.parametrize("ready", [False, True])
@pytest.mark.parametrize("field,value,code", [
    ("expected_declared_binding_sha256", "b" * 64, "FILES_DECLARED_BINDING_MISMATCH"),
    ("expected_node_id", OTHER, "FILES_NODE_MISMATCH"),
    ("expected_profile_sha256", "b" * 64, "FILES_PROFILE_MISMATCH"),
])
def test_server_pins_reject_mismatch(window, ready, field, value, code):
    window.options["ready"] = ready
    pins = expected(window)
    pins[field] = value
    assert files.status(window.root, **pins) == {
        "state": "unavailable", "filesystem_only": True, "code": code}
    assert window.events[-1] in ("directory_exit", "guard_exit")


def test_ready_guard_rechecks_digest_after_initial_window(window):
    window.options.update(ready=True, guard_digest="b" * 64)
    result = files.status(window.root, **expected(window))
    assert result["code"] == "FILES_DECLARED_BINDING_MISMATCH"
    assert ("guard", window.digest) in window.events


def test_ready_checks_owned_binding_bytes(window):
    window.options["ready"] = True
    window.acquisition.metadata = (SimpleNamespace(
        path=window.root / "declared-binding.json",
        raw=files._canonical({"domain_id": NODE, "facility_id": OTHER,
                             "node_id": OTHER, "profile_sha256": PROFILE})),)
    result = files.status(window.root, **expected(window))
    assert result["code"] == "FILES_NODE_MISMATCH"
    assert window.events[-1] == "guard_exit"


def test_reserved_without_binding_is_not_promoted(window):
    window.options["binding"] = False
    assert files.status(window.root, **expected(window)) == {
        "state": "reserved", "filesystem_only": True, "init_operation_id": NODE}
    pins = expected(window)
    pins["expected_declared_binding_sha256"] = "b" * 64
    assert files.status(window.root, **pins)["code"] == "FILES_DECLARED_BINDING_MISMATCH"


@pytest.mark.parametrize("ready", [False, True])
def test_original_defaults_still_accept_valid_observation(window, ready):
    window.options["ready"] = ready
    assert files.status(window.root)["state"] == ("ready" if ready else "reserved")


@pytest.mark.parametrize("failure", [KeyboardInterrupt(), SystemExit(31)])
def test_original_cancellation_object_propagates(window, failure):
    def stop():
        raise failure
    with pytest.raises(type(failure)) as caught:
        files.status(window.root, check_cancelled=stop, **expected(window))
    assert caught.value is failure
