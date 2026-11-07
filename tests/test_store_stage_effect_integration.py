"""Natural Stage publication exercises the fixed Store effect gate.

This verifies one real metadata publication and normal cleanup, not a complete
Store installation or the original preview/install/resume consumer deadline.
"""
import hashlib
import importlib
import json
from pathlib import Path
import uuid

from reno import storage_lifecycle as lifecycle, sync_media as safe
from tests.test_namespace_guard_runtime import initialized, profile  # noqa: F401
from tests.test_store_namespace_effect_observation import namespace, store  # noqa: F401


def test_natural_store_metadata_stage_enters_fresh_inside_and_post_gates(namespace, store, initialized, monkeypatch):
    stage = importlib.import_module(f"{namespace.__package__}.owned_stage_io_r251")
    stage_path = Path(namespace.__file__).with_name("owned_stage_io_r251.py")
    assert Path(stage.__file__).resolve() == stage_path.resolve()
    stage_digests = {
        "authority_runtime_origin_r668": "868fce0e0dbed00ff591aeaf7f2cc19a8f8cf943a35557cf059f2e3d59c22825",
        "authority_runtime_origin_r671": "868fce0e0dbed00ff591aeaf7f2cc19a8f8cf943a35557cf059f2e3d59c22825",
        "authority_runtime_origin_r673": "b8cf7065c7c684d7eb48c6d48c5768e0e7e15ff4b4a87091cff3c31b108fb420",
        "authority_runtime_origin_r675": "b8cf7065c7c684d7eb48c6d48c5768e0e7e15ff4b4a87091cff3c31b108fb420",
        "authority_runtime_origin_r677": "09fc023f0a7ac13845257ee64447f524cc5f15a06f1ce879c6d569cb66482ab1",
        "authority_runtime_origin_r679": "09fc023f0a7ac13845257ee64447f524cc5f15a06f1ce879c6d569cb66482ab1",
        "authority_runtime_origin_r681": "09fc023f0a7ac13845257ee64447f524cc5f15a06f1ce879c6d569cb66482ab1",
        "authority_runtime_origin_r685": "09fc023f0a7ac13845257ee64447f524cc5f15a06f1ce879c6d569cb66482ab1",
        "authority_runtime_origin_r687": "09fc023f0a7ac13845257ee64447f524cc5f15a06f1ce879c6d569cb66482ab1",
        "authority_runtime_origin_r691": "09fc023f0a7ac13845257ee64447f524cc5f15a06f1ce879c6d569cb66482ab1",
        "authority_runtime_origin_r693": "09fc023f0a7ac13845257ee64447f524cc5f15a06f1ce879c6d569cb66482ab1",
        "authority_runtime_origin_r695": "09fc023f0a7ac13845257ee64447f524cc5f15a06f1ce879c6d569cb66482ab1",
        "authority_runtime_origin_r697": "09fc023f0a7ac13845257ee64447f524cc5f15a06f1ce879c6d569cb66482ab1",
        "authority_runtime_origin_r701": "17e1a4facef032d74dd66573a6c1d5c14104031e5a99f9c9b759f2b7720ade9a",
        "authority_runtime_origin_r705": "5e714478c4f982ea66293ac4bc5125a632cf0cae77f27ee4e0bad116574a0496",
        "authority_runtime_origin_r709": "3fd2746f41c2759d489c6067df2123741026d2de2a5e725e511ccb10198e4611",
    }
    assert hashlib.sha256(stage_path.read_bytes()).hexdigest() == stage_digests[namespace.__package__]
    assert stage.store is store and stage.ns is namespace
    assert stage._N2_EFFECT is store._native_namespace_verify
    assert stage._N2_CHECK is store._Budget.check
    original_effect = stage._effect
    original_inside = store._native_effect_budget_inside_original
    original_after = store._native_effect_budget_after_original
    scopes, after_scopes, stage_scopes = [], [], []
    before_db = Path(safe._native_path(initialized.database)).read_bytes()

    with lifecycle.exclusive(initialized) as guard:
        with namespace._closed_namespace_owned_locked(initialized, exclusive=True) as owner:
            budget = store._Budget(owner)
            budget_record = store._budget_record_original(budget)
            actual = budget_record.acquisition
            assert actual.storage_guard is guard and guard.exclusive and actual.exclusive
            with store._ClosingStack(budget) as stack:
                # Preserve genuine source pins and the original reservation
                # inputs. No prebuilt owner, journal or metadata is injected.
                store._source_pins(stack, budget)
                legacy, _, names = store._legacy(actual, stack, budget)
                assert not ({"store-init-reservation.json", "store-header.json", store.v2.JOURNAL} & names)
                operation = str(uuid.uuid4())
                raw = store._canonical({
                    "format_version": store.v2.FORMAT,
                    "kind": "store_init_reservation",
                    "store_init_operation_id": operation,
                    "declared_binding_sha256": store._sha(store._canonical(actual.declaration)),
                    "legacy": legacy,
                    "layout": store.v2._layout(),
                })
                assert store.v2._validate_init(raw, legacy, actual.declaration)["store_init_operation_id"] == operation

                def inside(value, expected_record, held, scope):
                    assert value is budget and expected_record is budget_record and held is actual
                    binding = namespace._runtime_directory_binding_original(scope)
                    assert binding.held is actual and binding.phase == "reading"
                    assert binding.guard_plan is not None and binding.guard_plan.guard is guard
                    result = original_inside(value, expected_record, held, scope)
                    scopes.append(scope)
                    return result

                def after(value, expected_record, held):
                    assert value is budget and expected_record is budget_record and held is actual
                    assert namespace._RUNTIME_DIRECTORY_CURRENT.get() is None
                    assert namespace._RUNTIME_DIRECTORY_CONTEXT.get() is None
                    assert scopes and id(scopes[-1]) not in namespace._RUNTIME_DIRECTORY_SCOPES
                    result = original_after(value, expected_record, held)
                    after_scopes.append(scopes[-1])
                    return result

                def natural_effect(stage_scope):
                    assert stage_scope.mode == "store" and stage_scope.budget is budget
                    count = len(scopes)
                    result = original_effect(stage_scope)
                    # Each actual Stage gate must complete its own inside and
                    # post path. The admitted callback itself stays original.
                    assert len(scopes) == len(after_scopes) == count + 1
                    assert scopes[-1] is after_scopes[-1]
                    stage_scopes.append(stage_scope)
                    return result

                with monkeypatch.context() as patch:
                    patch.setattr(store, "_native_effect_budget_inside_original", inside)
                    patch.setattr(store, "_native_effect_budget_after_original", after)
                    patch.setattr(stage, "_effect", natural_effect)
                    assert stage._N2_EFFECT is store._native_namespace_verify
                    assert store._native_effect_scope_dispatch_original()
                    result = store._publish_store_metadata_original(
                        stack, actual.directory, "store-init-reservation.json", raw, budget)
                assert result == hashlib.sha256(raw).hexdigest()
                assert stage_scopes and len(scopes) == len(stage_scopes)
                assert len({id(scope) for scope in scopes}) == len(scopes)
                target = actual.directory.path / "store-init-reservation.json"
                assert Path(safe._native_path(target)).read_bytes() == raw
                custody = store._STACK_RECORDS[id(stack)]
                stage_scope = custody.stage
                assert all(value is stage_scope for value in stage_scopes)
                assert stage_scope.first_failure is None and not stage_scope.resources
                assert not any(creation.scope is stage_scope for creation in stage._STORE_METADATA_ORIGINALS.values())
                assert not any(creation.scope is stage_scope for creation in stage._RETAINED_STORE_METADATA_ORIGINALS.values())
                assert budget_record.first is None and custody.first is None
            assert custody.closed and stage_scope.closed and stage_scope.first_failure is None
            assert id(stack) not in store._STACK_RECORDS and id(stage_scope) not in stage._SCOPES
            assert budget_record.first is None
        assert id(budget) not in store._BUDGET_RECORDS and id(budget) not in store._RETAINED_BUDGETS
    assert Path(safe._native_path(initialized.database)).read_bytes() == before_db
    assert namespace._RUNTIME_DIRECTORY_CURRENT.get() is None
    assert namespace._RUNTIME_DIRECTORY_CONTEXT.get() is None
    assert all(id(scope) not in namespace._RUNTIME_DIRECTORY_SCOPES for scope in scopes)
    print(json.dumps({"natural_stage_effects": len(stage_scopes), "distinct_scopes": len(scopes),
                      "reservation_published": True, "normal_cleanup": True,
                      "complete_store_installation": False}))
