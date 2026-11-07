"""Actual receipt producers cannot be replaced by a caller or schema label.

The fixtures produce original Home intents and actual Cloud SQL/HTTP results.
Fault injection stops only the real publisher's original head write; it never
synthesizes a receipt, journal event, original body or completed State effect.
"""
from copy import deepcopy
import json
import sqlite3

import pytest

from reno import sync_source_authority as permanent, sync_source_lifecycle17 as effects, sync_state
from reno import sync_source_legacy_pending_intake17 as intake
from reno.sync_protocol import SyncError
from tests.test_sync_source_genesis17 import permanent_files
from tests.test_sync_source_legacy_pending_intake17 import (
    real_cycle, original_intent, selectors, received, sql_rows,
)
from tests.test_sync_source_restore_cycle17 import actual_call


def _saved_or_lagging(value, monkeypatch, phase):
    if phase == "saved":
        intent, _args, saved, answer = received(value)
        return intent, saved["record"], answer
    intent = original_intent(value)
    args = selectors(value, intent)
    with value.store.reference_operation():
        actual_call(intake.install_original_pending17_locked, **args)
    body = intent["record"]["request"]
    if value.legacy_dispose:
        with intake.original_legacy_http_call17(**args, client=value.client):
            answer = value.client.source_dispose(body)
    else:
        answer = value.client.source_readmit(body)
    original_write, events, hit = permanent.AuthorityGuard._write, [], []
    stopped = RuntimeError("isolated original authority head interruption")

    def interrupted(owner, directory, name, raw, **kwargs):
        state = None if not events else events[-1]["state"]
        selected = (
            phase == "reserved_db_head" and state == "reserved"
            and directory.path == value.profile.database.parent and name == permanent.DB_HEAD_NAME
            or phase == "committed_checkpoint_head" and state == "committed"
            and directory.path == value.profile.checkpoints and name == permanent.CHECKPOINT_HEAD_NAME
        )
        if selected and not hit:
            hit.append(name)
            raise stopped
        result = original_write(owner, directory, name, raw, **kwargs)
        if directory.path.name == "journal":
            event = json.loads(raw)
            if event["kind"] == "receipts" and event["key"] == body["request_id"]:
                events.append(event)
        return result

    with monkeypatch.context() as scoped:
        scoped.setattr(permanent.AuthorityGuard, "_write", interrupted)
        with value.store.reference_operation(), pytest.raises(RuntimeError) as raised:
            actual_call(intake.publish_original_pending_receipt17_locked, client=value.client, **args)
        assert raised.value is stopped and len(hit) == 1
    value.store = sync_state.StateStore(value.store.data_dir, profile=value.profile)
    with value.store.reference_operation(), permanent.owned(value.store) as owner:
        observed = owner.observe_readonly()
        assert observed["head_status"] == "lagging"
        item = observed["entries"]["receipts"][body["request_id"]]
        assert item["state"] == ("reserved" if phase == "reserved_db_head" else "committed")
        record = deepcopy(item["record"])
    assert record["result"] == answer
    return intent, record, answer


def _unchanged_inputs(value):
    path = value.store.data_dir / sync_state._STATE
    return (path.read_bytes(), path.stat().st_mtime_ns, permanent_files(value),
        sql_rows(value.profile), deepcopy(value.transport.seen))


def _untrusted_producer(kind):
    if kind == "none":
        return None
    if kind == "foreign":
        return object()
    # The wrong closed guard's exact class is deliberately present. Its
    # nonexistent seal/context must not let a control guard publish a receipt.
    return object.__new__(effects.ControlCompletionGuard17)


def _forbid_mutating_observation(monkeypatch):
    monkeypatch.setattr(permanent.AuthorityGuard, "observe",
        lambda *args, **kwargs: pytest.fail("Untrusted receipt reached mutating observe/head repair"))
    monkeypatch.setattr(permanent.AuthorityGuard, "_write",
        lambda *args, **kwargs: pytest.fail("Untrusted receipt changed an authority file"))


@pytest.mark.parametrize("real_cycle", [None, "legacy_dispose"], indirect=True,
    ids=["actual17-readmit", "frozen16-dispose"])
@pytest.mark.parametrize("phase", ["saved", "reserved_db_head", "committed_checkpoint_head"])
@pytest.mark.parametrize("producer_kind", ["none", "foreign", "wrong_kind"])
def test_untrusted_original_receipt_publisher_never_repairs_or_changes_saved_facts(
        real_cycle, monkeypatch, phase, producer_kind):
    value = real_cycle
    intent, record, answer = _saved_or_lagging(value, monkeypatch, phase)
    if value.legacy_dispose:
        assert intent["record"]["request"]["source"]["handshake"]["migration_version"] == 16
    assert intent["record"]["expected_before"]["source_authority"] is None
    assert record["record_id"] == intent["record"]["record_id"]
    assert record["request_sha256"] == intent["record"]["request_sha256"]
    assert record["result_sha256"] == permanent.digest(answer)
    before = _unchanged_inputs(value)
    _forbid_mutating_observation(monkeypatch)
    with value.store.reference_operation(), permanent.owned(value.store) as owner:
        with pytest.raises(SyncError) as raised:
            owner.publish("receipts", record, producer=_untrusted_producer(producer_kind))
        assert raised.value.code == "SYNC_SOURCE_AUTHORITY_INVALID"
    assert _unchanged_inputs(value) == before


@pytest.mark.parametrize("real_cycle", [None, "legacy_dispose"], indirect=True,
    ids=["actual17-readmit", "frozen16-dispose"])
@pytest.mark.parametrize("phase", ["saved", "committed_checkpoint_head"])
def test_actual17_tables_with_forged16_label_never_select_legacy_head_repair(real_cycle, monkeypatch, phase):
    value = real_cycle
    _intent, record, _answer = _saved_or_lagging(value, monkeypatch, phase)
    with sqlite3.connect(value.profile.database) as connection:
        original = connection.execute("SELECT value FROM meta WHERE key='migration_version'").fetchone()[0]
        assert original == "17"
        connection.execute("UPDATE meta SET value='16' WHERE key='migration_version'")
    try:
        before = _unchanged_inputs(value)
        _forbid_mutating_observation(monkeypatch)
        with value.store.reference_operation(), permanent.owned(value.store) as owner:
            with pytest.raises(SyncError) as raised:
                owner.publish("receipts", record, producer=None)
            assert raised.value.code == "SYNC_SOURCE_AUTHORITY_INVALID"
        assert _unchanged_inputs(value) == before
        with sqlite3.connect(value.profile.database) as connection:
            assert connection.execute("SELECT value FROM meta WHERE key='migration_version'").fetchone()[0] == "16"
    finally:
        # Restore only the isolated fixture's original metadata for teardown.
        # The failed publication itself must neither repair nor relabel it.
        with sqlite3.connect(value.profile.database) as connection:
            connection.execute("UPDATE meta SET value=? WHERE key='migration_version'", (original,))
