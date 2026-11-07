"""Actual Namespace permit/stack exercise Stage's directory factory component.

The public initializer currently uses its original Namespace writers. This
test adds an explicit factory probe inside that real lifetime; it does not
claim that the public initializer naturally dispatches through Stage writers.
"""
from pathlib import Path
import uuid

from reno import storage_lifecycle as lifecycle, sync_media as safe
from tests.test_stage_directory_entry_r701 import namespace, store, stage, profile  # noqa: F401


def test_actual_initialization_stack_enters_namespace_stage_factory(namespace, store, stage, profile, monkeypatch):
    original_snapshot = namespace._initialization_remember_snapshot
    original_match = namespace._store_directory_close_matches
    attempts, completed, matches = [], [], []
    observing = False
    before = Path(safe._native_path(profile.database)).read_bytes()

    def match(record):
        result = original_match(record)
        if observing:
            matches.append(record)
        return result

    def remember(stack, directories, node, observed, node_observed):
        nonlocal observing
        result = original_snapshot(stack, directories, node, observed, node_observed)
        if attempts:
            return result
        attempts.append(stack)
        # The original snapshot and all original resource acquisitions have
        # returned before this component probe borrows the actual lifetime.
        assert id(stack) not in namespace._LIVE_STORE_ACQUIRE_GATES
        state = namespace._initialization_state(stack.budget, required=True)
        custody = namespace._store_original_resource_custody(stack)
        assert namespace._LIVE_STORE_CLOSES[id(stack)] is stack
        assert any(value is stack for value in state.stacks)
        assert state.permit.profile is profile and state.permit.guard is lifecycle._CURRENT.get()
        assert state.permit.guard.exclusive and stack.guard is state.permit.guard
        assert state.snapshots and state.snapshots[-1].stack is stack
        assert stack.resources is custody.resources
        directory = directories[0]
        expected = next(value for value in custody.resources
                        if type(value) is namespace._StoreDirectoryClose and value.original is directory)
        assert expected.handles and directory.handles
        assert len(custody.resources) >= 5  # The real four roots and pinned local node.

        cancellation = stage.lower.CancellationData()
        held_scope = stage.held._new_scope(cancellation)
        held_record = stage.held._scope_original(held_scope)
        assert held_record.cancellation is cancellation and not held_record.resources
        scope = None
        primary = None
        close_error = None
        held_close = None
        try:
            scope = stage._new_initialization_stage_scope_original(
                stack, held_scope, cancellation=cancellation)
            assert stage._SCOPES[id(scope)] is scope and scope.mode == "namespace"
            assert scope.stack is stack and scope.permit is state.permit
            assert scope.budget is stack.budget and scope.held_scope is held_scope
            assert scope.cancellation is cancellation
            assert scope.clock == (stack.budget.started, stack.budget.callback)
            assert namespace.DEADLINE_SECONDS == 30.0
            observing = True
            try:
                assert stage._directory_original(scope, directory) is expected
                assert stage._directory_original(scope, directory) is expected
            finally:
                observing = False
            assert matches == [expected, expected]
            assert not scope.resources and scope.first_failure is None
            assert namespace._initialization_state(stack.budget, required=True) is state
            assert namespace._store_original_resource_custody(stack) is custody
            assert stack.resources is custody.resources
        except BaseException as error:
            primary = error
            raise
        finally:
            # Both original closers are attempted in their ownership order.
            # A cleanup failure cannot replace the component's first error.
            try:
                if scope is not None:
                    stage._close_stage_scope_original(scope, original_error=primary)
            except BaseException as error:
                if primary is None:
                    close_error = error
                else:
                    BaseException.add_note(primary, "Namespace Stage component cleanup failed")
            try:
                held_close = stage.held._close_scope_original(
                    held_scope, original_error=primary if primary is not None else close_error)
            except BaseException as error:
                if primary is None and close_error is None:
                    close_error = error
                else:
                    BaseException.add_note(primary if primary is not None else close_error,
                                           "Namespace held component cleanup failed")
            if primary is None and close_error is not None:
                raise close_error
        assert scope.closed and scope.first_failure is None
        assert id(scope) not in stage._SCOPES and id(scope) not in stage._RETAINED_SCOPES
        assert held_record.phase == "closed" and held_record.first is None
        assert id(held_scope) not in stage.held._SCOPES and id(held_scope) not in stage.held._RETAINED_SCOPES
        assert held_close.resource_count == held_close.native_close_returned == held_close.observed_bytes == 0
        assert namespace._initialization_state(stack.budget, required=True) is state
        assert namespace._store_original_resource_custody(stack) is custody
        completed.append((stack, scope, held_scope, stack.budget))
        return result

    operation = str(uuid.uuid4())
    with lifecycle.exclusive(profile):
        with monkeypatch.context() as patch:
            patch.setattr(namespace, "_initialization_remember_snapshot", remember)
            patch.setattr(namespace, "_store_directory_close_matches", match)
            result = namespace.initialize_explicit_locked(profile, init_operation_id=operation,
                domain_id=str(uuid.uuid4()), facility_id=str(uuid.uuid4()))
    assert result["state"] == "namespace_ready" and result["init_operation_id"] == operation
    assert len(attempts) == len(completed) == 1 and len(matches) == 2
    stack, scope, held_scope, budget = completed[0]
    assert attempts == [stack]
    assert id(stack) not in namespace._LIVE_STORE_CLOSES
    assert id(stack) not in namespace._LIVE_STORE_RESOURCE_CUSTODY
    assert id(budget) not in namespace._LIVE_INITIALIZATION_BUDGETS
    assert scope.closed and id(scope) not in stage._SCOPES
    assert id(held_scope) not in stage.held._SCOPES
    assert Path(safe._native_path(profile.database)).read_bytes() == before
