"""Real legacy receipt scopes retain their original owners through cleanup."""

from contextvars import copy_context
from copy import copy, deepcopy
from types import SimpleNamespace

import pytest

from reno import home_creation_recovery17 as creation
from reno import source_preservation_witness as witness
from reno import storage_lifecycle as lifecycle
from reno import storage_source_authority_roots as home_roots
from reno import sync_source_authority as permanent
from reno import sync_source_legacy_pending_intake17 as intake
from reno import sync_source_legacy_pending_receipt17 as pending_receipt
from reno import sync_source_legacy_receipt_completion17 as legacy_receipt
from reno import sync_source_transport17 as transport
from reno.sync_protocol import SyncError
from tests.test_sync_source_legacy_pending_intake17 import (
    real_cycle, original_intent, selectors, sql_rows)
from tests.test_sync_source_legacy_receipt_completion17 import partial, forbidden
from tests.test_sync_source_restore_cycle17 import restored_origin17, actual_call
from tests.test_sync_source_genesis17 import permanent_files


class CallerFailure(RuntimeError):
    def __getattribute__(self, name):
        if name == "__traceback__":
            raise RuntimeError("caller traceback lookup interruption")
        return super().__getattribute__(name)


@pytest.fixture(params=["pending", "legacy"])
def receipt_case(request, monkeypatch):
    if request.param == "pending":
        value = request.getfixturevalue("real_cycle")
        intent = original_intent(value)
        args = selectors(value, intent)
        with value.store.reference_operation():
            actual_call(intake.install_original_pending17_locked, **args)
        value.client.source_readmit(intent["record"]["request"])
        stop = RuntimeError("isolated receipt body interruption")
        write = permanent.AuthorityGuard._write

        def interrupt(owner, directory, name, raw, **kwargs):
            if directory.path.name == "receipts" and name == intent["record"]["record_id"] + ".json":
                raise stop
            return write(owner, directory, name, raw, **kwargs)

        with monkeypatch.context() as scoped:
            scoped.setattr(permanent.AuthorityGuard, "_write", interrupt)
            with value.store.reference_operation(), pytest.raises(RuntimeError) as raised:
                actual_call(intake.publish_original_pending_receipt17_locked, client=value.client, **args)
            assert raised.value is stop
        module = pending_receipt
        factory = module.capture_original_pending_receipt17_locked
        complete = module.complete_original_pending_receipt17_locked
    else:
        value = request.getfixturevalue("restored_origin17")
        _core, intent, _record, _state, _paid, _files = partial(value, monkeypatch, "before_body")
        module = legacy_receipt
        factory = module.capture_original_legacy_receipt17_locked
        complete = module.complete_original_legacy_receipt17_locked
    with value.store.reference_operation(), permanent.owned(value.store) as owner:
        record = deepcopy(owner.observe_readonly()["entries"]["receipts"][intent["record"]["record_id"]]["record"])
    arguments = {"profile": value.profile, "store": value.store,
        "request_id": record["record_id"], "request_sha256": intent["record"]["request_sha256"],
        "expected_record_sha256": permanent.digest(record)}
    monkeypatch.delattr(value.client, "_source_transport17_broker")
    monkeypatch.setattr(value.client, "_request", forbidden)
    monkeypatch.setattr(transport, "capture_receipt", forbidden)
    return SimpleNamespace(value=value, module=module, factory=factory,
        complete=complete, arguments=arguments, record=record)


@pytest.mark.parametrize("body_failure", [False, True, "hostile"])
def test_receipt_scope_preserves_context_first_failure_and_final_ownership(
        receipt_case, monkeypatch, body_failure):
    case, value = receipt_case, receipt_case.value
    before = deepcopy(value.store.read()), sql_rows(value.profile), permanent_files(value)
    failure_type = CallerFailure if body_failure == "hostile" else RuntimeError
    body_stop = failure_type("original receipt caller failure")
    cleanup_stop = RuntimeError("receipt final checkpoint failure")
    verify_reader = home_roots._Reader.verify
    verify_checkpoint = witness._CheckpointCache.verify_full
    final_readers, streams = [], []

    def observe_reader(reader):
        scope = creation._READ_CURRENT.get()
        if scope is not None and scope.closing:
            binding = scope.check()
            assert binding.capture_current is case.module._CURRENT
            assert binding.capture_context is case.module._CONTEXT
            assert binding.capture_seal is case.module._SEAL
            assert not binding.capture._active
            assert case.module._CURRENT.get() is None and case.module._CONTEXT.get() is None
            assert permanent._CURRENT.get() is binding.owner and binding.lock.held
            assert lifecycle._CURRENT.get() is binding.storage_guard
            final_readers.append(reader)
            streams.extend(stream for _directory, _name, stream, *_ in reader.files if stream is not None)
        return verify_reader(reader)

    def fail_final_checkpoint(cache):
        answer = verify_checkpoint(cache)
        scope = creation._READ_CURRENT.get()
        if scope is not None and scope.closing:
            raise cleanup_stop
        return answer

    with monkeypatch.context() as scoped:
        scoped.setattr(home_roots._Reader, "verify", observe_reader)
        scoped.setattr(witness._CheckpointCache, "verify_full", fail_final_checkpoint)
        with pytest.raises(RuntimeError) as raised:
            with value.store.reference_operation(), case.factory(**case.arguments) as captured:
                scope = creation._read_scope(captured)
                assert scope.check().capture is captured
                with pytest.raises(SyncError) as denied_copy:
                    copy(captured)
                assert denied_copy.value.code.endswith("FACTORY_REQUIRED")
                copied_capture = object.__new__(type(captured))
                copied_capture.__dict__.update(captured.__dict__)
                for call in (copied_capture._read_control, copy(scope).check,
                             lambda: copy_context().run(captured._read_control)):
                    with pytest.raises(creation.CreationRecoveryError) as denied:
                        call()
                    assert denied.value.code == "HOME_CREATION_RECOVERY17_FACTORY_REQUIRED"
                captured.verify_source()
                if body_failure:
                    raise body_stop
        assert raised.value is (body_stop if body_failure else cleanup_stop)
    assert final_readers and streams and all(stream.closed for stream in streams)
    assert all(not directory.handles for reader in final_readers for directory in reader.directories)
    assert creation._READ_CURRENT.get() is None and creation._READ_CONTEXT.get() is None
    assert not creation._READ_SCOPES and permanent._CURRENT.get() is None
    assert case.module._CURRENT.get() is None and case.module._CONTEXT.get() is None
    assert value.store._directory is None
    assert (value.store.read(), sql_rows(value.profile), permanent_files(value)) == before
    with pytest.raises(creation.CreationRecoveryError):
        captured._read_control()
    with value.store.reference_operation():
        saved = actual_call(case.complete, **case.arguments)
    assert saved == {"record": case.record, "ref": permanent.record_ref(case.record)}
    assert value.store.read() == before[0] and sql_rows(value.profile) == before[1]


def test_receipt_initial_full_failure_retires_scope_and_both_stacks(receipt_case, monkeypatch):
    case, value = receipt_case, receipt_case.value
    before = deepcopy(value.store.read()), sql_rows(value.profile), permanent_files(value)
    stop = RuntimeError("receipt initial scope full failure")
    full = creation._OwnedReadScope17.full
    entered = []

    def interrupt(scope):
        if not entered:
            entered.append(scope)
            binding = scope.check()
            assert binding.capture_current is case.module._CURRENT
            assert binding.owner.lock.held and permanent._CURRENT.get() is binding.owner
            raise stop
        return full(scope)

    with monkeypatch.context() as scoped:
        scoped.setattr(creation._OwnedReadScope17, "full", interrupt)
        with value.store.reference_operation(), pytest.raises(RuntimeError) as raised:
            with case.factory(**case.arguments):
                pytest.fail("Initial failure reached the public capture")
        assert raised.value is stop and len(entered) == 1
    assert creation._READ_CURRENT.get() is None and creation._READ_CONTEXT.get() is None
    assert not creation._READ_SCOPES and permanent._CURRENT.get() is None
    assert case.module._CURRENT.get() is None and case.module._CONTEXT.get() is None
    assert value.store._directory is None
    assert (value.store.read(), sql_rows(value.profile), permanent_files(value)) == before
    with value.store.reference_operation():
        saved = actual_call(case.complete, **case.arguments)
    assert saved["record"] == case.record


def test_first_failure_capture_uses_the_original_base_exception_traceback():
    original = CallerFailure("original caller failure")
    failures = creation._ExitFailures17()
    try:
        raise original
    except CallerFailure as error:
        failures.capture(error)
        assert failures.first == (
            CallerFailure, original, BaseException.__dict__["__traceback__"].__get__(original))
    closed = []
    failures.run(closed.append, "cleanup completed")
    assert closed == ["cleanup completed"]
    with pytest.raises(CallerFailure) as raised:
        failures.reraise()
    assert raised.value is original
