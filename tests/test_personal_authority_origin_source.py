"""Actual isolated origin SQL/WAL and owners, never an enrolled authority.

Success uses production schema initialization, personal writes, EX, source
readers and committed-image replay. Only explicitly named failure seams are
substituted. No successful OriginObservation or accepted receiver is seeded.
Raised close-fault substitutes do not cover the inherited unchecked Windows
Directory CloseHandle BOOL or establish a complete native closure witness.
"""
from contextlib import closing, contextmanager
from dataclasses import FrozenInstanceError
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import uuid

import pytest

from reno import config, db, personal
from reno import personal_authority_contract as authority
from reno import personal_authority_origin_source as origin
from reno import personal_mirror_contract as facts
from reno import personal_mirror_transfer_schema as receiver_schema
from reno import personal_preservation_contract as preservation
from reno import personal_preservation_source as preservation_source
from reno import storage_lifecycle as lifecycle, storage_readonly as readonly


def uid():
    return str(uuid.uuid4())


def encoded(value):
    """Independent canonical JSON encoding, without calling the observer."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def origin_env(tmp_path, monkeypatch, request):
    role = getattr(request, "param", "home")
    assert role in {"home", "cloud"}
    root = tmp_path / "actual-home"
    data = root / "data"
    data.mkdir(parents=True)
    local = root / "config.synthetic.json"
    local.write_bytes(encoded({"node_role": role, "personal_features_enabled": True,
                              "field_checks_enabled": True}))
    monkeypatch.setenv("RENO_DATA_DIR", str(data))
    monkeypatch.setenv("RENO_CONFIG_FILE", str(local))
    for name in ("RENO_NODE_ROLE", "RENO_PERSONAL_FEATURES_ENABLED",
                 "RENO_FIELD_CHECKS_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    for name, path in {"DATA": data, "MEDIA": root / "media",
                       "ORIG": root / "media/originals", "FRAMES": root / "media/frames",
                       "AUDIO": root / "media/audio", "FRAME_CACHE": root / "frames_cache"}.items():
        monkeypatch.setattr(config, name, path)
    monkeypatch.setattr(config, "_cache", None)
    path = data / "reno.db"
    db.initialize(path)
    profile = lifecycle.StorageProfile(path, root / "media", data / "generated",
                                       data / "sync-checkpoints")
    lifecycle.initialize(profile)
    with closing(sqlite3.connect(path)) as con, con:
        meta = dict(con.execute("SELECT key,value FROM meta WHERE key IN (?,?,?,?)",
                                facts.META_KEYS))
        assert con.execute("SELECT value FROM meta WHERE key='migration_version'").fetchone() == ("17",)
    card_id = operation_id = None
    if role == "home":
        operation_id = uid()
        created, status = personal.create_card(meta["default_project_id"], {
            "operation_id": operation_id, "primary_epoch": int(meta["primary_epoch"]),
            "changes": {"title": "绍兴防水核对", "question": "材料型号尚未确定",
                        "content_snapshot": {"kind": "note", "text": "真实隔离的个人决策"}}})
        assert status == 201
        card_id = created["card"]["card_id"]
    return {"data": data, "path": path, "profile": profile,
            "node": data / "node-identity.json", "meta": meta,
            "card_id": card_id, "mutation_operation_id": operation_id,
            "arguments": {"domain_id": uid(), "facility_id": uid(), "init_operation_id": uid()}}


def observe(env, **options):
    return origin.observe(env["profile"], **env["arguments"], **options)


@contextmanager
def captured(env, **options):
    with lifecycle.exclusive(env["profile"]):
        with origin.capture_locked(env["profile"], **env["arguments"], **options) as result:
            yield result


def documents(observation):
    return authority.decode(observation.intent_bytes), json.loads(observation.evidence_bytes)


def logical_hashes(observation):
    payload = documents(observation)[0]["payload"]
    return payload["observed_personal_meta_sha256"], payload["observed_personal_sql_sha256"]


def raw_sql_fingerprints(observation):
    files = documents(observation)[1]["raw_sql_files"]
    assert set(files) == {"", "-wal", "-journal"}
    return {suffix: None if files[suffix] is None else {
        "sha256": files[suffix]["sha256"], "byte_size": files[suffix]["byte_size"]}
        for suffix in ("", "-wal", "-journal")}


def original_bytes(env):
    paths = {suffix: Path(str(env["path"]) + suffix) for suffix in ("", "-wal", "-journal")}
    paths["node"] = env["node"]
    return {key: path.read_bytes() if path.exists() else None for key, path in paths.items()}


def expected_documents(env):
    """Read actual committed rows independently of _project_sql/_capture.

    Only the existing typed-cell digest and schema descriptor are shared. This
    does not reuse an observer result as the expected value.
    """
    with closing(sqlite3.connect(env["path"])) as con, con:
        meta_rows = list(con.execute(
            "SELECT key,value FROM meta WHERE key IN (?,?,?,?) ORDER BY key", facts.META_KEYS))
        meta_summary = preservation.typed_table_digest(["key", "value"], meta_rows)
        meta_summary["schema_sha256"] = sha(encoded(facts._describe_table(con, "meta")))
        summaries = {}
        for name, columns in preservation.PERSONAL_COLUMNS.items():
            # Names are the fixed production schema contract, never caller SQL.
            query = 'SELECT ' + ','.join('"' + c + '"' for c in columns) + ' FROM "' + name + '"'
            summary = preservation.typed_table_digest(list(columns), list(con.execute(query)))
            summary["schema_sha256"] = sha(encoded(facts._describe_table(con, name)))
            summaries[name] = summary
    meta = {"format_version": 1, "scope": "personal-authority-meta-v1", "schema_version": 17,
            "meta": meta_summary}
    sql = {"format_version": 1, "scope": "personal-authority-ten-and-meta-v1", "schema_version": 17,
           "meta": meta_summary, "personal_tables": summaries}
    return meta, sql


def update_meta(env, key, value):
    with closing(sqlite3.connect(env["path"])) as con, con:
        con.execute("UPDATE meta SET value=? WHERE key=?", (value, key))


def install_receiver(env):
    # Real production producer creates an empty facility; no accepted mirror,
    # pair, receipt, or stored attachment is invented.
    with lifecycle.shared(env["profile"]):
        with closing(sqlite3.connect(env["path"])) as con, con:
            con.execute("BEGIN IMMEDIATE")
            receiver_schema.install(con, profile=env["profile"])


def close_fault_context(actual, error, *, on_close=None):
    """Failure seam: close the actual acquired owner, then raise the object."""
    @contextmanager
    def replacement(*args, **kwargs):
        context = actual(*args, **kwargs)
        result = context.__enter__()
        try:
            yield result
        except BaseException as primary:
            context.__exit__(type(primary), primary, primary.__traceback__)
            if on_close is not None:
                on_close()
            raise error
        else:
            context.__exit__(None, None, None)
            if on_close is not None:
                on_close()
            raise error
    return replacement


def substitute_ex(monkeypatch, replacement):
    # Keep canonical lifecycle module globals intact. Only this consumer's
    # acquisition call is substituted; the actual EX/require_guard still run.
    monkeypatch.setattr(origin, "lifecycle", SimpleNamespace(
        _profile=lifecycle._profile, require_guard=lifecycle.require_guard, exclusive=replacement))


def substitute_source(monkeypatch, **changes):
    selected = {"_HeldFile": preservation_source._HeldFile,
                "_PersonalPrivateDirectory": preservation_source._PersonalPrivateDirectory,
                "_project_sql": preservation_source._project_sql}
    selected.update(changes)
    monkeypatch.setattr(origin, "source", SimpleNamespace(**selected))


def substitute_image(monkeypatch, replacement):
    monkeypatch.setattr(origin, "readonly", SimpleNamespace(committed_image_locked=replacement))


def test_real_nonempty_origin_matches_independent_typed_sql_and_original_raw(origin_env):
    env = origin_env
    expected_meta, expected_sql = expected_documents(env)
    before = original_bytes(env)
    names = set(env["data"].iterdir())
    observed = observe(env)
    intent, evidence = documents(observed)
    assert type(observed.intent_bytes) is bytes and type(observed.evidence_bytes) is bytes
    assert observed.intent_bytes == encoded(intent)
    assert observed.evidence_bytes == encoded(evidence)
    assert intent == {"format_version": 1, "kind": "origin_init_intent",
        "domain_id": env["arguments"]["domain_id"], "operation_id": env["arguments"]["init_operation_id"],
        "issuer": {"facility_id": env["arguments"]["facility_id"],
                   "node_id": env["meta"]["personal_primary_id"],
                   "profile_sha256": sha(encoded(list(env["profile"].identity)))},
        "payload": {"current_epoch": 1, "observed_primary_node_id": env["meta"]["personal_primary_id"],
                    "observed_personal_meta_sha256": sha(encoded(expected_meta)),
                    "observed_personal_sql_sha256": sha(encoded(expected_sql))}}
    assert evidence["meta_document"] == expected_meta
    assert evidence["sql_document"] == expected_sql
    assert evidence["scope"] == "personal-authority-origin-source-v1"
    assert evidence["profile_paths"] == list(env["profile"].identity)
    assert evidence["profile_sha256"] == intent["issuer"]["profile_sha256"]
    assert evidence["intent_sha256"] == sha(observed.intent_bytes)
    assert set(expected_sql["personal_tables"]) == set(facts.TABLE_NAMES)
    for name in ("renovation_project", "decision_card", "mutation_receipt"):
        assert expected_sql["personal_tables"][name]["count"] > 0
    assert evidence["node"]["sha256"] == sha(before["node"])
    assert evidence["node"]["byte_size"] == len(before["node"])
    assert set(evidence["raw_sql_files"]) == {"", "-wal", "-journal"}
    for suffix in ("", "-wal", "-journal"):
        actual = evidence["raw_sql_files"][suffix]
        if before[suffix] is None:
            assert actual is None
        else:
            assert actual["sha256"] == sha(before[suffix])
            assert actual["byte_size"] == len(before[suffix])
            assert actual["path_identity"]["byte_size"] == len(before[suffix])
            assert actual["handle_identity"]["byte_size"] == len(before[suffix])
    with pytest.raises(FrozenInstanceError):
        observed.intent_bytes = b"replacement"
    assert original_bytes(env) == before
    assert set(env["data"].iterdir()) == names  # No enrollment facility is created.
    with lifecycle.exclusive(env["profile"], timeout=0) as guard:
        assert lifecycle.require_guard(env["profile"], exclusive=True) is guard


def test_real_wal_committed_view_excludes_uncommitted_and_rollback_then_changes_on_commit(origin_env):
    env = origin_env
    writer = sqlite3.connect(env["path"])
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("UPDATE decision_card SET question='committed WAL original' WHERE card_id=?", (env["card_id"],))
        writer.commit()
        assert Path(str(env["path"]) + "-wal").stat().st_size > 0
        before = original_bytes(env)
        first = observe(env)
        assert original_bytes(env) == before
        assert documents(first)[1]["source_fingerprint"]["files"]["-wal"]["byte_size"] > 0
        assert documents(first)[1]["sql_document"] == expected_documents(env)[1]
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE decision_card SET question='uncommitted private change' WHERE card_id=?", (env["card_id"],))
        assert writer.execute("SELECT question FROM decision_card WHERE card_id=?", (env["card_id"],)).fetchone() == ("uncommitted private change",)
        uncommitted_raw = original_bytes(env)
        assert logical_hashes(observe(env)) == logical_hashes(first)
        assert original_bytes(env) == uncommitted_raw
        assert writer.in_transaction is True  # Observer does not commit/rollback the writer.
        writer.rollback()
        assert logical_hashes(observe(env)) == logical_hashes(first)
        writer.execute("UPDATE decision_card SET question='new committed personal value' WHERE card_id=?", (env["card_id"],))
        writer.commit()
        changed = observe(env)
        assert logical_hashes(changed)[0] == logical_hashes(first)[0]
        assert logical_hashes(changed)[1] != logical_hashes(first)[1]
        assert documents(changed)[1]["sql_document"] == expected_documents(env)[1]
    finally:
        writer.close()


def test_knowledge_rows_and_revision_are_excluded_from_personal_logical_hash(origin_env):
    env = origin_env
    first = observe(env)
    with lifecycle.shared(env["profile"]):
        with closing(sqlite3.connect(env["path"])) as con, con:
            db.upsert_asset(con, {"video_id": "synthetic-origin-knowledge", "source_platform": "manual",
                "source_type": "manual", "title": "knowledge outside personal ten tables",
                "content_sha256": "a" * 64}, profile=env["profile"])
            db.bump_kb_revision(con)
    changed = observe(env)
    assert logical_hashes(changed) == logical_hashes(first)
    assert raw_sql_fingerprints(changed) != raw_sql_fingerprints(first)


def test_valid_receiver_install_is_excluded_but_invalid_receiver_schema_is_still_rejected(origin_env):
    env = origin_env
    first = observe(env)
    install_receiver(env)
    with closing(sqlite3.connect(env["path"])) as con, con:
        receiver_schema.validate(con)
    changed = observe(env)
    assert logical_hashes(changed) == logical_hashes(first)
    with closing(sqlite3.connect(env["path"])) as con, con:
        con.execute('ALTER TABLE "' + receiver_schema.INTENT + '" ADD COLUMN synthetic_drift TEXT')
    before = original_bytes(env)
    with pytest.raises(receiver_schema.PersonalMirrorSchemaError) as caught:
        observe(env)
    assert caught.value.code == "PERSONAL_MIRROR_SCHEMA_INCOMPATIBLE"
    assert original_bytes(env) == before  # No schema repair during observation.


def test_raw_vacuum_and_node_json_whitespace_do_not_change_logical_digest(origin_env):
    env = origin_env
    first = observe(env)
    with closing(sqlite3.connect(env["path"])) as con, con:
        con.execute("VACUUM")
    env["node"].write_text(' { "instance_id" : "' + env["meta"]["personal_primary_id"] + '" }\n', encoding="utf-8")
    changed = observe(env)
    assert logical_hashes(changed) == logical_hashes(first)
    first_raw, changed_raw = documents(first)[1], documents(changed)[1]
    assert raw_sql_fingerprints(first) != raw_sql_fingerprints(changed)
    assert first_raw["node"]["sha256"] != changed_raw["node"]["sha256"]


def test_capture_requires_existing_actual_ex_and_rejects_sh(origin_env):
    env = origin_env
    with pytest.raises(lifecycle.StorageLifecycleError) as caught:
        with origin.capture_locked(env["profile"], **env["arguments"]):
            pytest.fail("Capture invented its own EX")
    assert caught.value.code == "STORAGE_GUARD_REQUIRED"
    with lifecycle.shared(env["profile"]):
        with pytest.raises(lifecycle.StorageLifecycleError) as caught:
            with origin.capture_locked(env["profile"], **env["arguments"]):
                pytest.fail("Capture borrowed SH as EX")
        assert caught.value.code == "STORAGE_LOCK_UPGRADE"
    with lifecycle.exclusive(env["profile"], timeout=0):
        pass


def test_observe_does_not_initialize_missing_profile_facilities(origin_env):
    env = origin_env
    absent = env["data"] / "never-initialized-reports"
    profile = lifecycle.StorageProfile(env["path"], env["profile"].media,
                                       absent, env["profile"].checkpoints)
    before = original_bytes(env)
    with pytest.raises(lifecycle.StorageLifecycleError) as caught:
        origin.observe(profile, **env["arguments"])
    assert caught.value.code == "STORAGE_NOT_INITIALIZED"
    assert not absent.exists()
    assert original_bytes(env) == before


@pytest.mark.parametrize("key", list(facts.META_KEYS))
def test_each_missing_personal_meta_key_blocks_nonempty_origin(origin_env, key):
    with closing(sqlite3.connect(origin_env["path"])) as con, con:
        con.execute("DELETE FROM meta WHERE key=?", (key,))
    before = original_bytes(origin_env)
    with pytest.raises(preservation.PersonalPreservationError) as caught:
        observe(origin_env)
    assert caught.value.code == "PERSONAL_PRESERVATION_PERSONAL_META_INVALID"
    assert original_bytes(origin_env) == before


@pytest.mark.parametrize("origin_env", ["cloud"], indirect=True)
def test_real_empty_cloud_three_meta_cannot_be_registered_as_origin(origin_env):
    env = origin_env
    assert set(env["meta"]) == {"library_id", "personal_primary_id", "primary_epoch"}
    assert env["meta"]["personal_primary_id"] == ""
    with closing(sqlite3.connect(env["path"])) as con, con:
        for name in facts.TABLE_NAMES:
            assert con.execute('SELECT count(*) FROM "' + name + '"').fetchone() == (0,)
    before = original_bytes(env)
    with pytest.raises(origin.OriginSourceError) as caught:
        observe(env)
    assert caught.value.code == "ORIGIN_META_INVALID"
    assert original_bytes(env) == before


@pytest.mark.parametrize("key", ["library_id", "personal_primary_id", "default_project_id"])
def test_nil_personal_meta_uuid_cannot_produce_origin(origin_env, key):
    update_meta(origin_env, key, str(uuid.UUID(int=0)))
    with pytest.raises(origin.OriginSourceError) as caught:
        observe(origin_env)
    assert caught.value.code == "ORIGIN_META_INVALID"


def test_default_project_must_exist_in_actual_personal_rows(origin_env):
    update_meta(origin_env, "default_project_id", uid())
    with pytest.raises(origin.OriginSourceError) as caught:
        observe(origin_env)
    assert caught.value.code == "ORIGIN_DEFAULT_PROJECT_INVALID"


def test_local_node_and_committed_primary_must_match(origin_env):
    update_meta(origin_env, "personal_primary_id", uid())
    with pytest.raises(origin.OriginSourceError) as caught:
        observe(origin_env)
    assert caught.value.code == "ORIGIN_PRIMARY_MISMATCH"


@pytest.mark.parametrize("value,code", [
    ("0", "PERSONAL_PRESERVATION_PERSONAL_META_INVALID"),
    ("01", "PERSONAL_PRESERVATION_PERSONAL_META_INVALID"),
    ("1.0", "PERSONAL_PRESERVATION_PERSONAL_META_INVALID"),
    (str(2**63), "ORIGIN_META_INVALID"),
])
def test_epoch_text_and_signed_integer_ceiling_are_strict(origin_env, value, code):
    update_meta(origin_env, "primary_epoch", value)
    with pytest.raises((preservation.PersonalPreservationError, origin.OriginSourceError)) as caught:
        observe(origin_env)
    assert caught.value.code == code


@pytest.mark.parametrize("epoch", [1, 2**63 - 1])
def test_epoch_closed_boundaries_are_observed_as_strict_integers(origin_env, epoch):
    update_meta(origin_env, "primary_epoch", str(epoch))
    payload = documents(observe(origin_env))[0]["payload"]
    assert type(payload["current_epoch"]) is int
    assert payload["current_epoch"] == epoch


@pytest.mark.parametrize("damage", ["duplicate", "extra", "nil", "noncanonical"])
def test_node_original_json_has_one_canonical_nonnil_uuid(origin_env, damage):
    env = origin_env
    node_id = env["meta"]["personal_primary_id"]
    if damage == "duplicate":
        raw = ('{"instance_id":"' + node_id + '","instance_id":"' + node_id + '"}').encode()
    elif damage == "extra":
        raw = encoded({"instance_id": node_id, "extra": True})
    else:
        value = str(uuid.UUID(int=0)) if damage == "nil" else "{" + node_id + "}"
        raw = encoded({"instance_id": value})
    env["node"].write_bytes(raw)
    with pytest.raises(origin.OriginSourceError) as caught:
        observe(env)
    assert caught.value.code == "ORIGIN_NODE_INVALID"
    assert env["node"].read_bytes() == raw


def test_false_valued_cancel_callable_is_polled_without_truthiness(origin_env):
    class FalseCallback:
        calls = bool_calls = 0

        def __bool__(self):
            self.bool_calls += 1
            return False

        def __call__(self):
            self.calls += 1
    callback = FalseCallback()
    observed = observe(origin_env, check_cancelled=callback)
    assert documents(observed)[0]["kind"] == "origin_init_intent"
    assert callback.calls > 0
    assert callback.bool_calls == 0


@pytest.mark.parametrize("failure", [OSError("synthetic late cancellation"), SystemExit(37)],
                         ids=["oserror", "systemexit"])
def test_late_cancel_original_object_survives_image_and_ex_close_failures(origin_env, monkeypatch, failure):
    env = origin_env
    state = {"after_sql": False, "cancel_calls": 0, "image_closed": False, "ex_closed": False}
    actual_sql = preservation_source._project_sql

    def arm_after_actual_sql(*args, **kwargs):
        result = actual_sql(*args, **kwargs)
        state["after_sql"] = True
        return result

    def cancelled():
        if state["after_sql"]:
            state["cancel_calls"] += 1
            raise failure
    substitute_source(monkeypatch, _project_sql=arm_after_actual_sql)
    substitute_image(monkeypatch, close_fault_context(
        readonly.committed_image_locked, RuntimeError("synthetic image close failure"),
        on_close=lambda: state.__setitem__("image_closed", True)))
    substitute_ex(monkeypatch, close_fault_context(lifecycle.exclusive,
        RuntimeError("synthetic EX close failure"), on_close=lambda: state.__setitem__("ex_closed", True)))
    before = original_bytes(env)
    with pytest.raises(type(failure)) as caught:
        observe(env, check_cancelled=cancelled)
    assert caught.value is failure
    assert state["cancel_calls"] == 1  # Sticky error; no repeated caller execution.
    assert state["image_closed"] and state["ex_closed"]
    assert original_bytes(env) == before
    with lifecycle.exclusive(env["profile"], timeout=0):
        pass


@pytest.mark.parametrize("owner", ["image", "ex"])
def test_normal_close_failure_blocks_delivery_after_actual_owner_close(origin_env, monkeypatch, owner):
    env = origin_env
    failure = OSError("synthetic " + owner + " close failure")
    closed = []
    if owner == "image":
        substitute_image(monkeypatch, close_fault_context(
            readonly.committed_image_locked, failure, on_close=lambda: closed.append(owner)))
    else:
        substitute_ex(monkeypatch, close_fault_context(lifecycle.exclusive, failure,
                                                      on_close=lambda: closed.append(owner)))
    before = original_bytes(env)
    delivered = []
    with pytest.raises(OSError) as caught:
        delivered.append(observe(env))
    assert caught.value is failure
    assert closed == [owner] and delivered == []
    assert original_bytes(env) == before
    with lifecycle.exclusive(env["profile"], timeout=0):
        pass


def test_actual_database_change_after_yield_rejects_the_stale_candidate(origin_env):
    env = origin_env
    node_before = env["node"].read_bytes()
    with pytest.raises(preservation.PersonalPreservationError) as caught:
        with captured(env) as candidate:
            assert documents(candidate)[0]["kind"] == "origin_init_intent"
            # Deliberately represents a noncooperating existing SQL writer.
            with closing(sqlite3.connect(env["path"])) as con, con:
                con.execute("UPDATE decision_card SET question='bypass changed original' WHERE card_id=?", (env["card_id"],))
    assert caught.value.code == "PERSONAL_PRESERVATION_SOURCE_CHANGED"
    with closing(sqlite3.connect(env["path"])) as con, con:
        assert con.execute("SELECT question FROM decision_card WHERE card_id=?", (env["card_id"],)).fetchone() == ("bypass changed original",)
    assert env["node"].read_bytes() == node_before


def test_terminal_node_verification_fault_rejects_candidate_and_preserves_real_files(origin_env, monkeypatch):
    env = origin_env
    state = {"armed": False, "terminal_checks": 0}
    actual_verify = preservation_source._HeldFile.verify
    failure = preservation.PersonalPreservationError("SOURCE_CHANGED", retriable=True)

    class TerminalFaultHeld(preservation_source._HeldFile):
        def verify(self):
            actual_verify(self)
            if state["armed"] and self.name == "node-identity.json":
                state["terminal_checks"] += 1
                raise failure
    # Explicit failure seam, because Windows' actual node hold denies writers.
    # The successful origin and each pre-fault verification remain real.
    substitute_source(monkeypatch, _HeldFile=TerminalFaultHeld)
    before = original_bytes(env)
    with pytest.raises(preservation.PersonalPreservationError) as caught:
        with captured(env):
            state["armed"] = True
    assert caught.value is failure
    assert state["terminal_checks"] > 0
    assert original_bytes(env) == before


def test_real_held_node_denies_windows_write_or_detects_posix_changed_bytes(origin_env):
    env = origin_env
    before = env["node"].read_bytes()
    replacement = encoded({"instance_id": uid()})
    if os.name == "nt":
        with captured(env):
            with pytest.raises(OSError):
                env["node"].write_bytes(replacement)
        assert env["node"].read_bytes() == before
    else:
        with pytest.raises(preservation.PersonalPreservationError) as caught:
            with captured(env):
                env["node"].write_bytes(replacement)
        assert caught.value.code == "PERSONAL_PRESERVATION_SOURCE_CHANGED"
        assert env["node"].read_bytes() == replacement


@pytest.mark.parametrize("suffix", ["-wal", "-journal"])
def test_initially_missing_sql_companion_appearing_at_terminal_is_not_absorbed(origin_env, suffix):
    env = origin_env
    companion = Path(str(env["path"]) + suffix)
    assert not companion.exists()
    before = original_bytes(env)
    with pytest.raises(readonly.ReadonlyImageError) as caught:
        with captured(env) as candidate:
            assert documents(candidate)[1]["raw_sql_files"][suffix] is None
            companion.write_bytes(b"")
    assert caught.value.code == "SOURCE_CHANGED"
    assert companion.read_bytes() == b""
    after = original_bytes(env)
    assert after[""] == before[""] and after["node"] == before["node"]


def test_final_profile_tuple_mutation_cannot_deliver_candidate(origin_env):
    env = origin_env
    profile = env["profile"]
    original_reports = profile.reports
    before = original_bytes(env)
    try:
        with pytest.raises(origin.OriginSourceError) as caught:
            with captured(env):
                # Explicit fault against an otherwise frozen profile; no root
                # is moved or created, and it is restored before test cleanup.
                object.__setattr__(profile, "reports", env["data"] / "changed-profile-reports")
        assert caught.value.code == "ORIGIN_PROFILE_CHANGED"
    finally:
        object.__setattr__(profile, "reports", original_reports)
    assert original_bytes(env) == before
    assert not (env["data"] / "changed-profile-reports").exists()


@pytest.mark.parametrize("phase", ["capture-terminal", "ex-close"])
def test_original_thirty_second_deadline_includes_terminal_and_ex_close(origin_env, monkeypatch, phase):
    env = origin_env
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(origin, "time", SimpleNamespace(monotonic=lambda: clock.now))
    if phase == "capture-terminal":
        with pytest.raises(origin.OriginSourceError) as caught:
            with captured(env):
                clock.now = 30.0
    else:
        actual_ex = lifecycle.exclusive

        @contextmanager
        def elapsed_close(*args, **kwargs):
            with actual_ex(*args, **kwargs) as guard:
                yield guard
            clock.now = 30.0
        substitute_ex(monkeypatch, elapsed_close)
        with pytest.raises(origin.OriginSourceError) as caught:
            observe(env)
    assert caught.value.code == "ORIGIN_TIME_BUDGET_EXCEEDED"
    with lifecycle.exclusive(env["profile"], timeout=0):
        pass
