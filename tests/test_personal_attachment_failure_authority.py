"""Failed upload diagnostics retain the original transaction authority.

The ordinary17 fixture creates the source through the production mutation API.
The actual parser creates its stage. Only the existing next_chunk fault callback
then commits a synthetic authority change in the real isolated SQLite database.
No require_write, capability, lifecycle guard or database producer is replaced.
"""
import asyncio
import uuid

import pytest

from reno import personal, personal_attachments as files
from reno import storage_admin, storage_lifecycle as lifecycle
from tests.test_personal_attachment_lifecycle import attachment_env, BODY, BOUNDARY, HEADER, TAIL  # noqa: F401


class _StreamCancellation(BaseException):
    pass


def _raise_original(failure, tracebacks):
    try:
        raise failure
    except BaseException:
        tracebacks.append(failure.__traceback__)
        raise


def _assert_original(actual, original, tracebacks):
    assert actual is original and len(tracebacks) == 1
    observed = actual.__traceback__
    while observed is not None and observed is not tracebacks[0]:
        observed = observed.tb_next
    assert observed is tracebacks[0]


def _row(connection, operation):
    row = connection.execute("SELECT * FROM source_attachment WHERE operation_id=?", (operation,)).fetchone()
    return dict(row) if row is not None else None


def _receipts(connection, operation):
    return [dict(row) for row in connection.execute(
        "SELECT * FROM mutation_receipt WHERE operation_id=?", (operation,))]


def _assert_resources_released(value, operation):
    assert lifecycle.current_guard() is None
    assert not list((files.root_path() / ".staging").glob("*.part"))
    assert (value.profile.database.parent / "node-identity.json").read_bytes() == value.identity
    # Exercise real lock and writer admission after the original upload scopes
    # have exited. No synthetic successful cleanup callback is substituted.
    with lifecycle.exclusive(value.profile, timeout=0):
        with storage_admin.connection(value.profile) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("UPDATE meta SET value=value WHERE key='primary_epoch'")
            connection.rollback()
            assert not connection.in_transaction
    with files.upload_lock(operation) as acquired:
        assert acquired
    assert lifecycle.current_guard() is None


@pytest.mark.parametrize("failure_type", [_StreamCancellation, asyncio.CancelledError],
    ids=["base-exception", "async-cancellation"])
@pytest.mark.parametrize("transition,code", [("epoch", "PRIMARY_CHANGED"), ("primary", "READ_ONLY_REPLICA")])
def test_failure_diagnostic_cannot_write_after_actual_authority_change(attachment_env, transition, code, failure_type):
    value, operation = attachment_env, str(uuid.uuid4())
    original, tracebacks = failure_type("original synthetic upload cancellation"), []
    captured, calls = {}, []
    expected = value.epoch + 1 if transition == "epoch" else str(uuid.uuid4())

    with lifecycle.shared(value.profile) as guard:
        def check():
            assert lifecycle.require_guard(value.profile) is guard

        def next_chunk():
            check()
            calls.append(len(calls) + 1)
            if len(calls) == 1:
                return HEADER + BODY[:12]
            assert len(calls) == 2
            with storage_admin.connection(value.profile) as connection:
                captured["stage"] = _row(connection, operation)
                captured["receipts"] = _receipts(connection, operation)
                captured["meta"] = dict(connection.execute("SELECT key,value FROM meta"))
                assert captured["stage"]["state"] == "staging"
                assert captured["stage"]["error"] is None
                assert captured["receipts"] == []
                assert personal.require_write(connection, value.epoch, upload=True)["personal_upload"] is True
                connection.execute("BEGIN IMMEDIATE")
                key = "primary_epoch" if transition == "epoch" else "personal_primary_id"
                connection.execute("UPDATE meta SET value=? WHERE key=?", (str(expected), key))
                connection.commit()
            _raise_original(original, tracebacks)

        with pytest.raises(failure_type) as caught:
            files._receive_upload_locked(value.profile, "multipart/form-data; boundary=" + BOUNDARY,
                value.source["source_id"], operation, str(value.epoch), next_chunk, check)
        _assert_original(caught.value, original, tracebacks)
        assert lifecycle.require_guard(value.profile) is guard
        with storage_admin.connection(value.profile) as connection:
            assert _row(connection, operation) == captured["stage"]
            assert _receipts(connection, operation) == captured["receipts"]
            expected_meta = dict(captured["meta"])
            expected_meta["primary_epoch" if transition == "epoch" else "personal_primary_id"] = str(expected)
            assert dict(connection.execute("SELECT key,value FROM meta")) == expected_meta
            with pytest.raises(personal.PersonalError) as denied:
                personal.require_write(connection, value.epoch, upload=True)
            assert denied.value.code == code
    assert calls == [1, 2]
    _assert_resources_released(value, operation)


@pytest.mark.parametrize("failure_type", [_StreamCancellation, asyncio.CancelledError],
    ids=["base-exception", "async-cancellation"])
def test_authorized_failure_retains_original_failed_diagnostic_and_exception(attachment_env, failure_type):
    value, operation = attachment_env, str(uuid.uuid4())
    original, tracebacks = failure_type("original authorized upload cancellation"), []
    captured, calls = {}, []

    with lifecycle.shared(value.profile) as guard:
        def check():
            assert lifecycle.require_guard(value.profile) is guard

        def next_chunk():
            check()
            calls.append(len(calls) + 1)
            if len(calls) == 1:
                return HEADER + BODY[:12]
            assert len(calls) == 2
            with storage_admin.connection(value.profile) as connection:
                captured["stage"] = _row(connection, operation)
                captured["meta"] = dict(connection.execute("SELECT key,value FROM meta"))
                assert captured["stage"]["state"] == "staging"
                assert personal.require_write(connection, value.epoch, upload=True)["personal_upload"] is True
            _raise_original(original, tracebacks)

        with pytest.raises(failure_type) as caught:
            files._receive_upload_locked(value.profile, "multipart/form-data; boundary=" + BOUNDARY,
                value.source["source_id"], operation, str(value.epoch), next_chunk, check)
        _assert_original(caught.value, original, tracebacks)
        with storage_admin.connection(value.profile) as connection:
            expected_stage = {**captured["stage"], "state": "failed", "error": "上传未完成，可重试"}
            assert _row(connection, operation) == expected_stage
            assert _receipts(connection, operation) == []
            assert dict(connection.execute("SELECT key,value FROM meta")) == captured["meta"]
    assert calls == [1, 2]
    _assert_resources_released(value, operation)

    # The same original operation resumes through the actual stage/ready path.
    pieces = iter([HEADER, BODY, TAIL])
    with lifecycle.shared(value.profile) as guard:
        def check_retry():
            assert lifecycle.require_guard(value.profile) is guard

        result, status = files._receive_upload_locked(value.profile, "multipart/form-data; boundary=" + BOUNDARY,
            value.source["source_id"], operation, str(value.epoch), lambda: next(pieces, None), check_retry)
        assert status == 201 and result["attachment"]["state"] == "ready"
        assert result["attachment"]["attachment_id"] == captured["stage"]["attachment_id"]
        with storage_admin.connection(value.profile) as connection:
            assert len(_receipts(connection, operation)) == 1
            ready = _row(connection, operation)
            assert ready["state"] == "ready" and ready["error"] is None
            assert files.resolve_storage(ready["storage_key"]).read_bytes() == BODY
    _assert_resources_released(value, operation)
