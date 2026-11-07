"""Real isolated legacy writes and metadata-only authority refusal.

No-marker success preserves the old mutation; it is not an enrollment history
claim or a grant. Name-stream and close/cancellation failures below are explicit
consumer-local substitutes around real acquired directories/scandir owners.
No old test module, accepted authority, or successful observation DTO is used.
"""
from contextlib import closing, contextmanager
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import uuid

import pytest

from reno import config, db, personal, storage_admin
from reno import personal_attachments as attachments
from reno import personal_authority_legacy_fence as fence
from reno import personal_mirror_contract as facts
from reno import storage_lifecycle as lifecycle, sync_media as safe


def uid():
    return str(uuid.uuid4())


def envelope(changes, *, operation_id=None, base=None):
    body = {"operation_id": operation_id or uid(), "primary_epoch": 1, "changes": changes}
    if base is not None:
        body["base_revision"] = base
    return body


@pytest.fixture
def library(tmp_path, monkeypatch):
    root, data = tmp_path / "actual-home", tmp_path / "actual-home/data"
    data.mkdir(parents=True)
    local = root / "config.synthetic.json"
    local.write_text(json.dumps({"node_role": "home", "personal_features_enabled": True,
                                "field_checks_enabled": True}), encoding="utf-8")
    monkeypatch.setenv("RENO_DATA_DIR", str(data))
    monkeypatch.setenv("RENO_CONFIG_FILE", str(local))
    for key in ("RENO_NODE_ROLE", "RENO_PERSONAL_FEATURES_ENABLED", "RENO_FIELD_CHECKS_ENABLED"):
        monkeypatch.delenv(key, raising=False)
    for name, path in {"DATA": data, "MEDIA": root / "media", "ORIG": root / "media/originals",
                       "FRAMES": root / "media/frames", "AUDIO": root / "media/audio",
                       "FRAME_CACHE": root / "frames_cache"}.items():
        monkeypatch.setattr(config, name, path)
    monkeypatch.setattr(config, "_cache", None)
    path = data / "reno.db"
    assert db.initialize(path).version == 17
    profile = lifecycle.StorageProfile(path, root / "media", data / "generated", data / "sync-checkpoints")
    lifecycle.initialize(profile)
    with closing(sqlite3.connect(path)) as con, con:
        meta = dict(con.execute("SELECT key,value FROM meta WHERE key IN (?,?,?,?)", facts.META_KEYS))
    source_envelope = envelope({"source_type": "manual_note", "title": "绍兴未定材料的原始记录"})
    created, status = personal.create_source(meta["default_project_id"], source_envelope)
    assert status == 201
    return {"root": root, "data": data, "path": path, "profile": profile,
            "project_id": meta["default_project_id"], "source_id": created["source"]["source_id"],
            "source_envelope": source_envelope}


def sql_state(env):
    with closing(sqlite3.connect(env["path"])) as con, con:
        return {name: list(con.execute('SELECT * FROM "' + name + '"'))
                for name in ("meta", *facts.TABLE_NAMES)}


def file_state(env):
    return {str(path.relative_to(env["root"])): path.read_bytes()
            for path in env["root"].rglob("*") if path.is_file()}


def make_marker(env, kind="empty", name=".reno-personal-authority-unknown"):
    marker = env["data"] / name
    if kind == "file":
        marker.write_bytes(b"unknown original marker bytes; no accepted authority")
    else:
        marker.mkdir()
        if kind == "partial":
            (marker / "unclassified.part").write_bytes(b"original unfinished artifact")
    return marker


def call_gate(env, **options):
    with lifecycle.shared(env["profile"]):
        return fence.assert_legacy_write_allowed_locked(env["profile"], **options)


def assert_ex_released(env):
    with lifecycle.exclusive(env["profile"], timeout=0) as held:
        assert lifecycle.require_guard(env["profile"], exclusive=True) is held


class ScanFacade:
    """Always acquire/close the real scandir; optionally replace entry names."""
    def __init__(self, original, *, names=None, on_exit=None, exit_error=None):
        self.original = original
        self.names, self.on_exit, self.exit_error = names, on_exit, exit_error
        self.calls, self.closed = 0, 0

    def __call__(self, target):
        actual = self.original(target)
        index = self.calls
        self.calls += 1
        owner = self

        class Scan:
            def __enter__(self):
                stream = actual.__enter__()
                self.entries = iter(stream) if owner.names is None else iter(
                    SimpleNamespace(name=value) for value in owner.names(index))
                return self

            def __iter__(self):
                return self

            def __next__(self):
                return next(self.entries)

            def __exit__(self, kind, error, traceback):
                result = actual.__exit__(kind, error, traceback)
                owner.closed += 1
                if owner.on_exit is not None:
                    owner.on_exit(index)
                if owner.exit_error is not None:
                    raise owner.exit_error
                return result
        return Scan()


def substitute_scandir(monkeypatch, facade):
    # Replace the consumer's namespace, not os.scandir used by other modules.
    monkeypatch.setattr(fence, "os", SimpleNamespace(name=os.name, scandir=facade))


def substitute_directory(monkeypatch, *, entered=None, after_close=None, close_error=None):
    actual_factory = safe._Directory

    def factory(*args, **kwargs):
        actual = actual_factory(*args, **kwargs)

        class Context:
            def __enter__(self):
                result = actual.__enter__()
                if entered is not None:
                    entered()
                return result

            def __exit__(self, kind, error, traceback):
                result = actual.__exit__(kind, error, traceback)
                if after_close is not None:
                    after_close()
                if close_error is not None:
                    raise close_error
                return result
        return Context()
    monkeypatch.setattr(fence, "safe", SimpleNamespace(
        _Directory=factory, _native_path=safe._native_path))


def test_real_no_marker_preserves_old_mutation_and_receipt_without_claiming_history(library):
    env = library
    before = sql_state(env)
    assert call_gate(env) is None
    body = envelope({"name": "保留旧语义的真实个人更新"}, base=1)
    response, status = personal.update_project(env["project_id"], body)
    assert status == 200 and response["project"]["name"] == body["changes"]["name"]
    after = sql_state(env)
    assert after["renovation_project"] != before["renovation_project"]
    assert len(after["mutation_receipt"]) == len(before["mutation_receipt"]) + 1
    assert personal.update_project(env["project_id"], body) == (response, status)
    assert sql_state(env) == after
    assert_ex_released(env)


def test_gate_requires_actual_profile_guard_and_accepts_existing_ex(library):
    env = library
    with pytest.raises(fence.LegacyAuthorityFenceError) as caught:
        fence.assert_legacy_write_allowed_locked(env["profile"])
    assert caught.value.code == "PERSONAL_AUTHORITY_STATE_UNAVAILABLE"
    with lifecycle.exclusive(env["profile"]):
        assert fence.assert_legacy_write_allowed_locked(env["profile"]) is None
    assert_ex_released(env)


@pytest.mark.parametrize("kind,name", [
    ("empty", ".reno-personal-authority-empty"),
    ("file", ".reno-personal-authority-corrupt-file"),
    ("partial", ".reno-personal-authority-temp-unknown"),
    ("empty", ".RENO-PERSONAL-AUTHORITY-UNKNOWN"),
    ("file", ".reno-personal-authority-trailing-alias. "),
])
def test_any_actual_authority_prefixed_object_refuses_without_repair_or_sql_changes(library, kind, name):
    env = library
    make_marker(env, kind, name)
    before_sql, before_files = sql_state(env), file_state(env)
    with pytest.raises(fence.LegacyAuthorityFenceError) as caught:
        call_gate(env)
    assert caught.value.code == "PERSONAL_AUTHORITY_REQUIRED"
    assert sql_state(env) == before_sql and file_state(env) == before_files
    assert_ex_released(env)


def test_marker_blocks_mutation_and_actual_receipt_replay_before_callback_or_cas(library):
    env = library
    make_marker(env)
    before_sql, before_files = sql_state(env), file_state(env)
    applied = []

    def callback(con, changes, base, operation_id):
        applied.append(True)
        con.execute("UPDATE renovation_project SET name='must not run'")
        return {"saved": True}
    actions = [lambda: personal.mutate("synthetic:fenced-mutation", envelope({}), callback),
               lambda: personal.create_source(env["project_id"], env["source_envelope"]),
               lambda: personal.update_project(env["project_id"], envelope({"name": "refused"}, base=0))]
    for action in actions:
        with pytest.raises(personal.PersonalError) as caught:
            action()
        assert caught.value.code == "PERSONAL_AUTHORITY_REQUIRED" and caught.value.status == 503
        assert applied == []
        assert sql_state(env) == before_sql and file_state(env) == before_files
    assert_ex_released(env)


def test_require_write_fence_precedes_its_capability_inspection(library, monkeypatch):
    env = library
    make_marker(env)
    calls = []

    def unexpected(*args, **kwargs):
        calls.append(True)
        raise AssertionError("Fence reached capability inspection")
    with storage_admin.connection(env["profile"]) as con:
        with monkeypatch.context() as patch:
            patch.setattr(personal, "capabilities", unexpected)
            with pytest.raises(personal.PersonalError) as caught:
                personal.require_write(con, 1)
    assert caught.value.code == "PERSONAL_AUTHORITY_REQUIRED"
    assert calls == []
    assert_ex_released(env)


@pytest.mark.parametrize("action", ["stage", "upload-lock", "cleanup"])
def test_actual_attachment_entry_refuses_before_staging_lock_or_cleanup_side_effects(library, action):
    env = library
    if action == "cleanup":
        staging = env["data"] / "personal-attachments/.staging"
        staging.mkdir(parents=True)
        original = staging / "legacy-original.part"
        original.write_bytes(b"old incomplete file must stay intact")
        os.utime(original, (1, 1))
    make_marker(env)
    before_sql, before_files = sql_state(env), file_state(env)
    selected = uid()
    with pytest.raises(personal.PersonalError) as caught:
        if action == "stage":
            attachments._stage(env["source_id"], selected, 1, "original.pdf", "application/pdf",
                               "files/" + uid() + ".pdf")
        elif action == "upload-lock":
            with attachments.upload_lock(selected):
                pytest.fail("Authority fence yielded an upload lock")
        else:
            attachments.cleanup_staging(now=2_000_000_000)
    assert caught.value.code == "PERSONAL_AUTHORITY_REQUIRED" and caught.value.status == 503
    assert sql_state(env) == before_sql and file_state(env) == before_files
    assert not (env["data"] / "personal-attachments/.locks").exists()
    assert_ex_released(env)


def test_false_valued_callback_is_called_without_truthiness_dispatch(library):
    class FalseCallback:
        calls = bool_calls = 0

        def __bool__(self):
            self.bool_calls += 1
            return False

        def __call__(self):
            self.calls += 1
    callback = FalseCallback()
    assert call_gate(library, check_cancelled=callback) is None
    assert callback.calls > 0 and callback.bool_calls == 0


@pytest.mark.parametrize("failure", [OSError("synthetic fence cancellation"), SystemExit(61)],
                         ids=["oserror", "systemexit"])
def test_late_callback_original_identity_survives_real_directory_close_then_close_fault(library, monkeypatch, failure):
    state = {"entered": False, "closed": False, "callback_calls": 0}
    substitute_directory(monkeypatch, entered=lambda: state.__setitem__("entered", True),
        after_close=lambda: state.__setitem__("closed", True), close_error=OSError("synthetic secondary close"))

    def callback():
        if state["entered"]:
            state["callback_calls"] += 1
            raise failure
    before_sql, before_files = sql_state(library), file_state(library)
    with pytest.raises(type(failure)) as caught:
        call_gate(library, check_cancelled=callback)
    assert caught.value is failure
    assert state["entered"] and state["closed"] and state["callback_calls"] == 1
    assert sql_state(library) == before_sql and file_state(library) == before_files
    assert_ex_released(library)


def test_last_poll_marker_is_seen_by_callback_free_terminal_census(library, monkeypatch):
    env = library
    state = {"first_closed": False, "terminal_started": False, "marker": False}

    def names_factory(index):
        if index > 0:
            state["terminal_started"] = True
        # Real names: no manufactured no-marker success view.
        with os.scandir(env["data"]) as actual:
            return [entry.name for entry in actual]
    scans = ScanFacade(os.scandir, names=names_factory,
        on_exit=lambda index: state.__setitem__("first_closed", True) if index == 0 else None)
    substitute_scandir(monkeypatch, scans)

    def callback():
        assert not state["terminal_started"], "User callback executed after terminal census began"
        if state["first_closed"] and not state["marker"]:
            make_marker(env, "file", ".reno-personal-authority-last-poll")
            state["marker"] = True
    with pytest.raises(fence.LegacyAuthorityFenceError) as caught:
        call_gate(env, check_cancelled=callback)
    assert caught.value.code == "PERSONAL_AUTHORITY_REQUIRED"
    assert state["marker"] and state["terminal_started"]
    assert scans.closed == scans.calls
    assert_ex_released(env)


def test_actual_name_change_between_fresh_passes_refuses_stale_no_marker_result(library, monkeypatch):
    env = library
    addition = env["data"] / "ordinary-added-during-census.bin"
    before_sql = sql_state(env)

    def changed(index):
        if index == 0:
            addition.write_bytes(b"explicit noncooperating name change")
    scans = ScanFacade(os.scandir, on_exit=changed)
    substitute_scandir(monkeypatch, scans)
    with pytest.raises(fence.LegacyAuthorityFenceError) as caught:
        call_gate(env)
    assert caught.value.code == "PERSONAL_AUTHORITY_STATE_UNAVAILABLE"
    assert addition.read_bytes() == b"explicit noncooperating name change"
    assert sql_state(env) == before_sql and scans.closed == scans.calls
    assert_ex_released(env)


@pytest.mark.parametrize("metric", ["single-pass-names", "cumulative-names", "cumulative-utf8-bytes"])
def test_hard_name_and_utf8_budget_is_shared_across_all_fresh_passes(library, monkeypatch, metric):
    if metric == "single-pass-names":
        names = [f"ordinary-{i:04d}" for i in range(4097)]
    elif metric == "cumulative-names":
        names = [f"ordinary-{i:04d}" for i in range(1500)]
    else:
        names = ["汉" * 200 + f"-{i:04d}" for i in range(1000)]
    scans = ScanFacade(os.scandir, names=lambda index: names)
    substitute_scandir(monkeypatch, scans)
    with pytest.raises(fence.LegacyAuthorityFenceError) as caught:
        call_gate(library)
    assert caught.value.code == "PERSONAL_AUTHORITY_STATE_UNAVAILABLE"
    assert scans.closed == scans.calls
    if metric == "cumulative-names":
        assert scans.calls >= 3  # Every one-pass input is below the count cap.
    elif metric == "cumulative-utf8-bytes":
        assert scans.calls >= 2  # Every one-pass input is below the byte cap.
    assert_ex_released(library)


@pytest.mark.parametrize("name", [None, "ordinary-\ud800"], ids=["non-text", "invalid-utf8"])
def test_unsupported_census_name_cannot_be_treated_as_absent(library, monkeypatch, name):
    scans = ScanFacade(os.scandir, names=lambda index: [name])
    substitute_scandir(monkeypatch, scans)
    with pytest.raises(fence.LegacyAuthorityFenceError) as caught:
        call_gate(library)
    assert caught.value.code == "PERSONAL_AUTHORITY_STATE_UNAVAILABLE"
    assert scans.closed == scans.calls


def test_scandir_io_failure_is_unavailable_and_does_not_grant_a_legacy_write(library, monkeypatch):
    def failed(target):
        raise OSError("synthetic census I/O failure")
    substitute_scandir(monkeypatch, failed)
    with pytest.raises(fence.LegacyAuthorityFenceError) as caught:
        call_gate(library)
    assert caught.value.code == "PERSONAL_AUTHORITY_STATE_UNAVAILABLE"
    assert_ex_released(library)


def test_normal_directory_close_fault_discards_no_marker_result_after_actual_close(library, monkeypatch):
    closed = []
    substitute_directory(monkeypatch, after_close=lambda: closed.append(True),
                         close_error=OSError("synthetic normal directory close"))
    before_sql, before_files = sql_state(library), file_state(library)
    with pytest.raises(fence.LegacyAuthorityFenceError) as caught:
        call_gate(library)
    assert caught.value.code == "PERSONAL_AUTHORITY_STATE_UNAVAILABLE"
    assert closed == [True]
    assert sql_state(library) == before_sql and file_state(library) == before_files
    assert_ex_released(library)


def test_original_thirty_second_deadline_includes_actual_directory_close(library, monkeypatch):
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(fence, "time", SimpleNamespace(monotonic=lambda: clock.now))
    substitute_directory(monkeypatch, after_close=lambda: setattr(clock, "now", 30.0))
    with pytest.raises(fence.LegacyAuthorityFenceError) as caught:
        call_gate(library)
    assert caught.value.code == "PERSONAL_AUTHORITY_STATE_UNAVAILABLE"
    assert_ex_released(library)
