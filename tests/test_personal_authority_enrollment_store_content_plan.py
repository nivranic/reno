"""r910 independent registered-content-plan tests, static candidate only.

Root alone applies/admit/runs. The fixture uses the ordinary dormant operation
lane and a controlled consumer after the actual packet factory returns. Every
pure API wrapper calls its saved actual function unless the explicit before-
return fault case throws. No positive registry row, fake native result, second
packet consume, fresh budget or authority receipt is supplied by this test.
"""
import base64
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile

import pytest

from tests.test_personal_authority_owned_ex_group import actual_prefix_env, original_bytes, raw_database_node
from tests.test_personal_authority_enrollment_store import independent_oracle, ORIGINAL_SPECS, ORIGINAL_NAMES
from tests.test_personal_authority_enrollment_store_stage import observed_stage, acquire_two, cleaned
from reno import personal_authority_enrollment as enrollment
from reno import personal_authority_enrollment_custody as source
from reno import personal_authority_enrollment_packet as packet
from reno import personal_authority_enrollment_store_custody as custody
from reno import personal_authority_enrollment_store_runtime as runtime
from reno import personal_authority_enrollment_store_selections as subject
from reno import personal_authority_enrollment_store as store
from reno import personal_authority_enrollment_successor as prepare
from reno import personal_authority_joint_sql_schema as schema
from reno import personal_authority_owned_ex_group as groups
from reno import personal_authority_replica_operation as operation
from reno import storage_lifecycle as lifecycle

pytestmark = pytest.mark.skipif(os.name != "nt", reason="r910 actual Windows source continuation")
ACTIVE_ORIGINAL_GRAPH_NATIVE_WHOLE_ACCEPTANCE = False
LIMIT = 1048576
PREFIX = ".reno-personal-authority-sql-store-v1-"
STEMS = ("intent", "contract", "seal", "genesis", "prepare")
SUFFIXES = ("slot.json", "reservation.json", "envelope.json", "published.json", "receipt.json")
WRAPPERS = (PREFIX + "header.json", PREFIX + "operation.json") + tuple(
    PREFIX + stem + "-" + suffix for stem in STEMS for suffix in SUFFIXES)
EXPECTED_NAMES = WRAPPERS[:24] + (PREFIX + "prepare-payload.json", PREFIX + "prepare-content-reservation.json") + WRAPPERS[24:]
EXPECTED_MAXIMA = (16384, 4096) + (4096, 65536, 16384, 4096, 4096) * 4 + (
    4096, 65536, 32768, 65536, 16384, 4096, 4096)
EXPECTED_LOCATIONS = tuple(("data_parent" if i in (0, 1, 2, 4) else "namespace_root", name)
                           for i, name in enumerate(ORIGINAL_NAMES))
EXPECTED_PIN_NAMES = (
    "_originals", "_prepare_raw", "_reservation_raw", "_file_raws", "_file_names", "_file_maxima",
    "_original_locations", "_continuation_pins", "_group", "_guard", "_operation", "_budget",
    "_parent", "_effective", "_source_scope", "_stage_scope", "_original_bytes", "_store_bytes",
    "_authority_bytes", "_empty_io_bytes",
)
PURE_APIS = (
    (prepare, "build_prepare"), (prepare, "cross_validate_prepare"),
    (prepare, "build_reservation"), (prepare, "cross_validate_reservation"),
    (store, "build_prepare_files"), (store, "cross_validate_prepare_files"),
)
PURE_ORDER = ("build_prepare", "cross_validate_prepare", "build_reservation",
              "cross_validate_reservation", "build_prepare_files", "cross_validate_prepare_files")


@pytest.fixture(autouse=True)
def isolated_content_plan_qa(tmp_path):
    qa = Path(os.environ["RENO_QA_ROOT"]).resolve(strict=True)
    tmp_path.resolve(strict=True).relative_to(qa)
    Path(tempfile.gettempdir()).resolve(strict=True).relative_to(qa)
    assert lifecycle._CURRENT.get() is source._CURRENT.get() is custody._CURRENT.get() is None
    assert schema._PUBLISHED_PROFILE_KEYS == ()
    yield
    assert lifecycle._CURRENT.get() is source._CURRENT.get() is custody._CURRENT.get() is None
    assert schema._PUBLISHED_PROFILE_KEYS == ()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def independent_pure2(originals):
    """Whole declarations from actual raw11, using only stdlib/fixed roles."""
    first, header, intent = (json.loads(originals[i]) for i in (0, 4, 7))
    context = {"domain_id": intent["domain_id"], "init_operation_id": intent["operation_id"],
               "issuer": deepcopy(intent["issuer"])}
    profiles = {"authority_profile_paths_role": "canonical_four_paths_array_v1",
                "authority_profile_paths_sha256": sha(canonical(first["profile"]["profile_paths"])),
                "physical_profile_role": "canonical_physical_profile_dict_v1",
                "physical_profile_sha256": sha(canonical(first["profile"]))}
    assert profiles["authority_profile_paths_sha256"] == first["authority_profile_paths_sha256"]
    assert profiles["physical_profile_sha256"] == first["physical_profile_sha256"]
    slot = {"slot": 1, "namespace_version": 3, "domain_id": context["domain_id"],
            "facility_id": context["issuer"]["facility_id"], "owner_node_id": context["issuer"]["node_id"],
            "init_operation_id": context["init_operation_id"]}
    refs = {key: {"role": role, "codec": codec, "record_kind": kind, "format_version": 1,
                  "raw_byte_size": len(raw), "raw_sha256": sha(raw), "identity": deepcopy(context)}
            for raw, (key, role, codec, kind) in zip(originals, ORIGINAL_SPECS)}
    payload = {"format_version": 1, "family": "personal_authority_enrollment_sql_prepare",
               "kind": "sql_registration_prepare", "scope": "durable_prepare_content_only",
               "context": context, "profiles": profiles,
               "namespace": {"family": "personal_authority_namespace3", "format_version": 1,
                             "root_name": header["root_name"], "root_native_identity": header["root_native_identity"],
                             "root_path_identity": header["root_path_identity"]},
               "logical_operation_id": context["init_operation_id"], "slot_identity": slot,
               "original_refs": refs,
               "publication_order": ["origin_intent", "local_contract", "init_seal", "genesis_grant", "binding", "prefix"]}
    payload_raw = canonical(payload)
    reservation_raw = canonical({
        "format_version": 1, "family": "personal_authority_enrollment_sql_prepare",
        "kind": "sql_registration_prepare_reservation", "scope": "durable_prepare_content_only",
        "context": deepcopy(context), "logical_operation_id": context["init_operation_id"],
        "slot_identity": deepcopy(slot), "prepare_payload_base64": base64.b64encode(payload_raw).decode("ascii"),
        "prepare_payload_byte_size": len(payload_raw), "prepare_payload_sha256": sha(payload_raw),
    })
    return payload_raw, reservation_raw


def content_error(call, suffix):
    with pytest.raises(subject.StoreContentPlanError) as caught:
        call()
    assert type(caught.value) is subject.StoreContentPlanError
    assert caught.value.code == "ENROLLMENT_STORE_CONTENT_" + suffix
    return caught.value


def stage_error(call, suffix):
    with pytest.raises(runtime.StoreStageError) as caught:
        call()
    assert type(caught.value) is runtime.StoreStageError
    assert caught.value.code == "ENROLLMENT_STORE_STAGE_" + suffix
    return caught.value


def same_error(call, first):
    with pytest.raises(type(first)) as caught:
        call()
    assert caught.value is first


def copy_slots(value):
    copied = object.__new__(type(value))
    for name in type(value).__slots__:
        if name != "__weakref__" and hasattr(value, name):
            object.__setattr__(copied, name, object.__getattribute__(value, name))
    return copied


def own_throw(first, selected):
    """Capture only this test throw node; never inspect a product frame."""
    try:
        raise first
    except BaseException as caught:
        assert caught is first
        selected["own_throw_node"] = caught.__traceback__
        raise


def own_throw_survives(first, selected):
    node, expected = first.__traceback__, selected["own_throw_node"]
    while node is not None and node is not expected:
        node = node.tb_next
    assert node is expected


def source_live_unchanged(value, selected):
    assert value._raws is selected["originals"] and value._budget is selected["budget"]
    assert value._pins is selected["continuation_pins"]
    assert value._source_journal is selected["source_journal"] and value._source_snapshot is selected["source_snapshot"]
    assert source._JOURNAL_RECEIPT.__get__(selected["source_journal"], source._NativeOwnerJournal) is selected["source_snapshot"]
    assert source._GROUP_JOURNALS[id(selected["group"])] is selected["source_group_row"]
    assert source._BUDGET_JOURNALS[id(selected["budget"])] is selected["source_budget_row"]
    assert selected["group"]._phase == "active" and lifecycle._CURRENT.get() is selected["guard"]
    assert source._CURRENT.get() is custody._CURRENT.get() is None
    assert value._journal is value._stage is value._receipt is None and value._attempted is False
    assert selected["source_snapshot"].source_closed is selected["source_snapshot"].write_authority is False
    assert selected["source_snapshot"].acquisition_coverage_proven is False
    assert value._precondition.outer_ex_closed is value._precondition.source_closed is value._precondition.write_authority is False
    assert schema._PUBLISHED_PROFILE_KEYS is selected["published_profiles"] and schema._PUBLISHED_PROFILE_KEYS == ()


def plan_retired(plan, value):
    row = subject._CONTENT_PLANS[id(plan)]
    assert row[0]() is plan and row[2]() is value and row[4] == () and row[5] == "retired"
    assert row[6] is row[7] is None
    assert subject._CONTENT_ATTEMPTS[id(value)][3] == "retired"
    assert plan._phase == "retired" and plan._pins == ()
    assert plan._continuation is plan._first is plan._first_tb is None
    assert all(getattr(plan, name) is None for name in EXPECTED_PIN_NAMES)


def after_original_retirement(selected, *, debt=False):
    value, slot = selected["continuation"], selected["slot"]
    assert slot._retired is True and slot._payload is slot._context_pin is None
    if debt:
        assert value._phase == "retained_failed" and value._pins is selected["continuation_pins"]
        assert value._journal is selected["journal"] and value._stage is selected["scope"]
        scope = selected["scope"]
        if "issued_stage_receipt" in selected:
            receipt = selected["issued_stage_receipt"]
            row = runtime._CLOSE_RECEIPTS[id(scope)]
            assert row is selected["issued_stage_receipt_row"]
            assert row[0]() is scope and row[1] is scope._claim
            assert value._receipt is scope._receipt is row[2] is receipt
            assert row[3] is receipt.native_snapshot is selected["snapshot"]
            assert receipt.source_closed is receipt.write_authority is False
            assert row[3].source_closed is row[3].write_authority is False
        else:
            assert value._receipt is scope._receipt is None
            assert runtime._CLOSE_RECEIPTS.get(id(scope)) is None
    else:
        assert value._phase == "retired_uncompleted" and value._pins == ()
        assert all(getattr(value, name) is None for name in runtime._PIN_NAMES)
        assert value._journal is value._stage is value._receipt is None
    assert lifecycle._CURRENT.get() is source._CURRENT.get() is custody._CURRENT.get() is None
    if selected.get("plan") is not None:
        plan_retired(selected["plan"], value)


def no_store_files_and_originals_unchanged(env, database_before):
    assert original_bytes(env) == env["originals"]
    assert raw_database_node(env) == database_before
    root = env["data"] / ".reno-personal-authority-namespace-v3"
    assert all(not (root / name).exists() for name in EXPECTED_NAMES)


def observe_factory(monkeypatch, consumer):
    """The saved real factory consumes once, then this hook inserts content."""
    seen, counts = [], Counter()
    real_factory, real_consume = runtime._new_prepare_continuation, packet._consume_locked
    real_retire = runtime._retire_continuation

    def consume(slot, **kwargs):
        counts["packet_consume"] += 1
        returned = real_consume(slot, **kwargs)
        assert type(returned) is packet._PacketBytes and returned is slot._payload
        return returned

    def retire(value, *, primary=None):
        counts["normal_retire"] += 1
        return real_retire(value, primary=primary)

    def factory(slot, **kwargs):
        counts["continuation_factory"] += 1
        value = real_factory(slot, **kwargs)
        assert value._slot is slot and value._payload is slot._payload and slot._consume_attempted is True
        assert counts["packet_consume"] == counts["continuation_factory"] == 1
        selected = {"continuation": value, "slot": slot, "originals": value._raws,
                    "continuation_pins": value._pins, "source_journal": value._source_journal,
                    "source_snapshot": value._source_snapshot, "source_group_row": source._GROUP_JOURNALS[id(value._group)],
                    "source_budget_row": source._BUDGET_JOURNALS[id(value._budget)], "group": value._group,
                    "guard": value._guard, "budget": value._budget, "parent": value._parent,
                    "effective": value._effective, "published_profiles": schema._PUBLISHED_PROFILE_KEYS,
                    "natural_counter_at_hook": value._budget.control_bytes, "events": [], "budget_calls": Counter()}
        seen.append(selected)
        def retire_direct_once():
            assert selected.get("manual_retirement_started", False) is False
            selected["manual_retirement_started"] = True
            counts["manual_retire"] += 1
            return real_retire(value)
        selected["retire_direct_once"] = retire_direct_once
        assert subject._CONTENT_ATTEMPTS.get(id(value)) is None
        source_live_unchanged(value, selected)
        try:
            consumer(value, selected)
        except BaseException as first:
            selected["hook_first"] = first
            if not selected.get("manual_retirement_started", False):
                counts["exception_retire"] += 1
                try:
                    real_retire(value, primary=first)
                except BaseException as secondary:
                    if secondary is not first:
                        enrollment.native._note(first)
            raise
        finally:
            # This applies only to the one actual-stage combination case.
            # Independently confirmed, never-attempted resources may be closed
            # by this fixture; attempted UNKNOWN numbers are never retried.
            for _owner, native_slot, number in reversed(selected.get("actual_owners", ())):
                if not native_slot._close_attempted:
                    raw = selected["original_close"](number)
                    assert type(raw) is int and -(1 << 31) <= raw < (1 << 31) and raw != 0
                    selected["fixture_closed"].append(number)
        return value

    monkeypatch.setattr(packet, "_consume_locked", consume)
    monkeypatch.setattr(runtime, "_new_prepare_continuation", factory)
    monkeypatch.setattr(runtime, "_retire_continuation", retire)
    return seen, counts


@contextmanager
def observe_pure(value, selected, monkeypatch, *, fault=None, first=None):
    """Track actual six APIs, including nested old-codec public cross calls."""
    depth = 0
    original_init, original_charge = enrollment._Budget.__init__, enrollment._Budget.charge

    def budget_init(budget, *args, **kwargs):
        selected["budget_calls"]["new_budget"] += 1
        return original_init(budget, *args, **kwargs)

    def charge(budget, actual):
        if budget is selected["budget"]:
            selected["budget_calls"]["charge"] += 1
        return original_charge(budget, actual)

    def wrapper(name, original):
        def observed(*args, **kwargs):
            nonlocal depth
            assert kwargs["originals"] is selected["originals"]
            assert all(a is b for a, b in zip(kwargs["originals"], selected["originals"]))
            entry = {"name": name, "depth": depth, "args": args, "kwargs": dict(kwargs), "returned": False,
                     "counter_before": selected["budget"].control_bytes}
            selected["events"].append(entry)
            depth += 1
            try:
                if fault == "prepare-call" and name == "build_prepare" and entry["depth"] == 0:
                    own_throw(first, selected)
                returned = original(*args, **kwargs)
                entry["returned"], entry["result"] = True, returned
                assert selected["budget"].control_bytes == entry["counter_before"]
                if fault == "full-cross-after-actual" and name == "cross_validate_prepare_files" and entry["depth"] == 0:
                    assert returned is None
                    own_throw(first, selected)
                return returned
            finally:
                depth -= 1
        return observed

    with monkeypatch.context() as patch:
        patch.setattr(enrollment._Budget, "__init__", budget_init)
        patch.setattr(enrollment._Budget, "charge", charge)
        for module, name in PURE_APIS:
            patch.setattr(module, name, wrapper(name, getattr(module, name)))
        yield
    assert selected["budget_calls"] == Counter()


def actual_materials(plan, value, selected):
    assert type(plan) is subject._StoreContentPlan and subject._content_plan(plan, continuation=value) is plan
    assert plan._continuation is value and plan._originals is selected["originals"]
    assert plan._continuation_pins is selected["continuation_pins"] and plan._budget is selected["budget"]
    assert all(getattr(plan, name) is getattr(value, name) for name in
               ("_group", "_guard", "_operation", "_budget", "_parent", "_effective", "_source_scope", "_stage_scope"))
    pure2 = independent_pure2(selected["originals"])
    assert plan._prepare_raw == pure2[0] and plan._reservation_raw == pure2[1]
    expected = independent_oracle((selected["originals"], pure2[0], pure2[1]))
    assert type(plan._file_raws) is tuple and len(plan._file_raws) == 29 and plan._file_raws == expected
    assert plan._file_raws[24] == plan._prepare_raw and plan._file_raws[25] == plan._reservation_raw
    assert plan._file_names is store.FILE_NAMES and plan._file_names == EXPECTED_NAMES
    assert plan._file_maxima is store._FILE_MAXIMA and plan._file_maxima == EXPECTED_MAXIMA
    assert plan._original_locations is store._ORIGINAL_LOCATIONS and plan._original_locations == EXPECTED_LOCATIONS
    assert all(0 < len(raw) <= maximum for raw, maximum in zip(plan._file_raws, EXPECTED_MAXIMA))
    top = [entry for entry in selected["events"] if entry["depth"] == 0]
    assert tuple(entry["name"] for entry in top) == PURE_ORDER
    assert all(entry["returned"] is True for entry in top)
    assert top[0]["result"] is plan._prepare_raw and top[2]["result"] is plan._reservation_raw
    assert top[4]["result"] is plan._file_raws and all(top[i]["result"] is None for i in (1, 3, 5))
    assert top[1]["args"][0] is top[2]["args"][0] is plan._prepare_raw
    assert top[3]["args"][0] is plan._reservation_raw and top[3]["args"][1] is plan._prepare_raw
    assert top[5]["args"][0] is plan._file_raws
    for entry in top[4:]:
        assert entry["kwargs"]["prepare_payload_raw"] is plan._prepare_raw
        assert entry["kwargs"]["prepare_content_reservation_raw"] is plan._reservation_raw
    original_size, authority_size = sum(map(len, selected["originals"])), sum(map(len, selected["originals"][7:11]))
    store_size = sum(map(len, expected))
    empty_size = 6 * store_size + 2 * original_size + authority_size
    assert (plan._original_bytes, plan._store_bytes, plan._authority_bytes, plan._empty_io_bytes) == (
        original_size, store_size, authority_size, empty_size)
    selected["independent_costs"] = {"O": original_size, "W": store_size, "T": authority_size, "D": empty_size}
    assert not hasattr(plan, "__dict__")
    for name in ("source_closed", "write_authority", "prepared_materials", "publisher", "selection", "handle", "fd", "callback"):
        assert not hasattr(plan, name)
    with pytest.raises(AttributeError):
        plan.write_authority = True
    source_live_unchanged(value, selected)
    return empty_size


def publish(env):
    return enrollment.publish_prefix_owned_ex(env["profile"], **env["args"], **env["expected"])


def successful_outer(seen, counts, env, database_before):
    assert len(seen) == 1
    assert counts == Counter(packet_consume=1, continuation_factory=1, normal_retire=1)
    assert seen[0]["budget_calls"] == Counter()
    after_original_retirement(seen[0])
    no_store_files_and_originals_unchanged(env, database_before)


def failed_outer(seen, counts, env, database_before, first, *, debt=False, manual=False):
    assert len(seen) == 1 and seen[0]["hook_first"] is first
    expected = Counter(packet_consume=1, continuation_factory=1)
    expected["manual_retire" if manual else "exception_retire"] = 1
    assert counts == expected
    assert seen[0]["budget_calls"] == Counter()
    after_original_retirement(seen[0], debt=debt)
    assert seen[0]["continuation"]._first is first
    no_store_files_and_originals_unchanged(env, database_before)


def test_fixed_content_plan_catalog_and_closed_authority_surface():
    assert runtime.selections is subject and subject.runtime is runtime and subject.prepare is prepare and subject.store is store
    assert subject.namespace is enrollment.namespace
    assert subject._PIN_NAMES == EXPECTED_PIN_NAMES
    modules = groups._catalog_modules_original()
    assert len(modules) == len(operation._OWNED_EX_FIXED_MODULES)
    assert all(a is b for a, b in zip(modules, operation._OWNED_EX_FIXED_MODULES))
    for module in (subject, prepare, store, prepare.authority, prepare.enrollment, prepare.namespace,
                   prepare.origin, prepare.origin.preservation):
        assert any(actual is module for actual in modules)
    extras = groups._catalog_extra_original()
    required = ((runtime, ("selections", "_RETAINED_RETIREMENTS", "_RetainedRetirement")),
                (subject, ("runtime", "prepare", "store", "namespace", "_SEAL", "_CONTENT_ATTEMPTS", "_CONTENT_PLANS", "_PIN_NAMES", "_StoreContentPlan")),
                (prepare, ("_REFERENCE_SPECS", "_ORIGINAL_MAXIMA", "MAX_PREPARE_BYTES", "MAX_RESERVATION_BYTES")),
                (store, ("FILE_NAMES", "_FILE_MAXIMA", "_ORIGINAL_LOCATIONS")))
    for module, names in required:
        present = {name for actual, fields in extras if actual is module for name in fields}
        assert set(names) <= present
    bindings, classes, functions, _links = operation._CALLEE_PINS
    for name in ("_new_content_plan", "_content_plan", "_require_empty_io_budget", "_retire_content_plan", "_retire_for_continuation"):
        expected = getattr(subject, name)
        assert any(module is subject and any(key == name and actual is expected for key, actual in fields)
                   for module, fields in bindings)
        assert any(row[0] is expected for row in functions)
    assert any(row[0] is subject._StoreContentPlan for row in classes)
    assert store.FILE_NAMES == EXPECTED_NAMES and store._FILE_MAXIMA == EXPECTED_MAXIMA
    assert store._ORIGINAL_LOCATIONS == EXPECTED_LOCATIONS and len(EXPECTED_NAMES) == 29
    assert subject.namespace.MAX_CONTROL_BYTES == LIMIT
    assert schema._PUBLISHED_PROFILE_KEYS == () and ACTIVE_ORIGINAL_GRAPH_NATIVE_WHOLE_ACCEPTANCE is False
    assert "__dict__" not in subject._StoreContentPlan.__slots__
    with pytest.raises(subject.StoreContentPlanError) as caught:
        subject._StoreContentPlan()
    assert caught.value.code == "ENROLLMENT_STORE_CONTENT_INVALID_ARGUMENT"


def test_actual_factory_exact_originals_pure2_full29_and_natural_budget(actual_prefix_env, monkeypatch):
    def consumer(value, selected):
        with observe_pure(value, selected, monkeypatch):
            before = value._budget.control_bytes
            plan = subject._new_content_plan(value)
            selected["plan"] = plan
            cost = actual_materials(plan, value, selected)
            assert type(before) is int and 0 <= before and before + cost < LIMIT
            assert value._budget.control_bytes == before
            assert subject._require_empty_io_budget(plan, continuation=value) is None
            assert value._budget.control_bytes == before
            selected["natural_gate"] = {"C0": before, "D": cost, "sum": before + cost, "controlled": False}
            count = len(selected["events"])
            content_error(lambda: subject._new_content_plan(value), "CLOSED")
            assert len(selected["events"]) == count
            assert value._first is None
    seen, counts = observe_factory(monkeypatch, consumer)
    database_before = raw_database_node(actual_prefix_env)
    publish(actual_prefix_env)
    successful_outer(seen, counts, actual_prefix_env, database_before)


def test_equal_plan_copy_and_unregistered_plan_are_rejected(actual_prefix_env, monkeypatch):
    def consumer(value, selected):
        with observe_pure(value, selected, monkeypatch):
            plan = subject._new_content_plan(value)
            selected["plan"] = plan
            actual_materials(plan, value, selected)
            copied, fake = copy_slots(plan), object.__new__(subject._StoreContentPlan)
            for substitute in (copied, fake):
                content_error(lambda: subject._content_plan(substitute, continuation=value), "INVALID_ARGUMENT")
                content_error(lambda: subject._require_empty_io_budget(substitute, continuation=value), "INVALID_ARGUMENT")
                content_error(lambda: subject._retire_content_plan(substitute), "INVALID_ARGUMENT")
            assert value._first is None and subject._content_plan(plan, continuation=value) is plan
            original_files = plan._file_raws
            plan._file_raws = tuple(list(original_files))
            assert plan._file_raws == original_files and plan._file_raws is not original_files
            first = content_error(lambda: subject._content_plan(plan, continuation=value), "BINDING_INVALID")
            selected["expected_first"] = first
            assert subject._CONTENT_PLANS[id(plan)][6] is value._first is first
            same_error(lambda: subject._retire_content_plan(plan), first)
            plan_retired(plan, value)
            content_error(lambda: subject._retire_content_plan(plan), "CLOSED")
            raise first
    seen, counts = observe_factory(monkeypatch, consumer)
    database_before = raw_database_node(actual_prefix_env)
    with pytest.raises(subject.StoreContentPlanError) as caught:
        publish(actual_prefix_env)
    assert caught.value is seen[0]["expected_first"]
    failed_outer(seen, counts, actual_prefix_env, database_before, caught.value)


@pytest.mark.parametrize("damage", ["equal-tuple"], ids=["equal-tuple"])
def test_original_identity_replacement_spends_factory_attempt(actual_prefix_env, monkeypatch, damage):
    def consumer(value, selected):
        with observe_pure(value, selected, monkeypatch):
            assert damage == "equal-tuple"
            original = value._raws
            value._raws = tuple(list(original))
            assert value._raws == original and value._raws is not original
            first = stage_error(lambda: subject._new_content_plan(value), "INVALID_ARGUMENT")
            selected["expected_first"] = first
            assert selected["events"] == [] and value._first is first
            attempt = subject._CONTENT_ATTEMPTS[id(value)]
            assert attempt[0]() is value and attempt[2] is None and attempt[3] == "attempted"
            content_error(lambda: subject._new_content_plan(value), "CLOSED")
            assert subject._CONTENT_ATTEMPTS[id(value)] is attempt and selected["events"] == []
            raise first
    seen, counts = observe_factory(monkeypatch, consumer)
    database_before = raw_database_node(actual_prefix_env)
    with pytest.raises(runtime.StoreStageError) as caught:
        publish(actual_prefix_env)
    assert caught.value is seen[0]["expected_first"]
    failed_outer(seen, counts, actual_prefix_env, database_before, caught.value)


def test_copied_continuation_is_rejected_without_spending_actual_attempt(actual_prefix_env, monkeypatch):
    def consumer(value, selected):
        with observe_pure(value, selected, monkeypatch):
            copied = copy_slots(value)
            assert copied is not value and copied._row is value._row and copied._raws is value._raws
            stage_error(lambda: subject._new_content_plan(copied), "INVALID_ARGUMENT")
            assert subject._CONTENT_ATTEMPTS.get(id(value)) is None
            assert subject._CONTENT_ATTEMPTS.get(id(copied)) is None and selected["events"] == []
            assert value._first is None
            plan = subject._new_content_plan(value)
            selected["plan"] = plan
            actual_materials(plan, value, selected)
    seen, counts = observe_factory(monkeypatch, consumer)
    database_before = raw_database_node(actual_prefix_env)
    publish(actual_prefix_env)
    successful_outer(seen, counts, actual_prefix_env, database_before)


@pytest.mark.parametrize("fault", ["prepare-call", "full-cross-after-actual"], ids=["prepare-call", "full-cross-after-actual"])
def test_factory_first_and_failed_once(actual_prefix_env, monkeypatch, fault):
    expected_first = RuntimeError("r910 fixed own producer-call fault")
    def consumer(value, selected):
        with observe_pure(value, selected, monkeypatch, fault=fault, first=expected_first):
            try:
                subject._new_content_plan(value)
            except RuntimeError as first:
                assert first is expected_first and value._first is first
                top = [event for event in selected["events"] if event["depth"] == 0]
                if fault == "prepare-call":
                    assert tuple(event["name"] for event in top) == ("build_prepare",)
                    assert top[0]["returned"] is False
                else:
                    assert tuple(event["name"] for event in top) == PURE_ORDER
                    assert all(event["returned"] is True for event in top) and top[-1]["result"] is None
                    pure2 = independent_pure2(selected["originals"])
                    assert top[0]["result"] == pure2[0] and top[2]["result"] == pure2[1]
                    assert top[4]["result"] == independent_oracle((selected["originals"], *pure2))
                    scope_keys, journal_keys = set(runtime._SCOPE_ROWS), set(custody._GROUP_JOURNALS)
                    same_error(lambda: runtime._store_prepare_scope(value), first)
                    assert set(runtime._SCOPE_ROWS) == scope_keys and set(custody._GROUP_JOURNALS) == journal_keys
                    assert runtime._STAGES_BY_CONTINUATION.get(id(value)) is None
                    assert value._stage is value._journal is None and custody._CURRENT.get() is None
                attempt = subject._CONTENT_ATTEMPTS[id(value)]
                assert attempt[2] is None and attempt[3] == "attempted"
                size = len(selected["events"])
                content_error(lambda: subject._new_content_plan(value), "CLOSED")
                assert subject._CONTENT_ATTEMPTS[id(value)] is attempt and len(selected["events"]) == size
                own_throw_survives(first, selected)
                raise
            pytest.fail("fixed content factory fault returned successfully")
    seen, counts = observe_factory(monkeypatch, consumer)
    database_before = raw_database_node(actual_prefix_env)
    with pytest.raises(RuntimeError) as caught:
        publish(actual_prefix_env)
    assert caught.value is expected_first
    own_throw_survives(caught.value, seen[0])
    failed_outer(seen, counts, actual_prefix_env, database_before, expected_first)


@pytest.mark.parametrize("boundary", ["below", "equal", "above"], ids=["below", "equal", "above"])
def test_same_budget_strict_empty_boundary(actual_prefix_env, monkeypatch, boundary):
    def consumer(value, selected):
        with observe_pure(value, selected, monkeypatch):
            plan = subject._new_content_plan(value)
            selected["plan"] = plan
            cost = actual_materials(plan, value, selected)
            natural = value._budget.control_bytes
            target = LIMIT - cost + {"below": -1, "equal": 0, "above": 1}[boundary]
            assert type(natural) is int and 0 <= natural <= target <= LIMIT
            assert value._budget is selected["budget"] and value._budget._requested_deadline is selected["parent"]
            assert value._budget._effective_deadline is selected["effective"]
            names = (value._budget.names, value._budget.name_bytes)
            value._budget.control_bytes = target
            selected["controlled_gate"] = {"natural_C0": natural, "injected_C0": target, "D": cost,
                                           "sum": target + cost, "controlled": True, "monotonic": True}
            if boundary == "below":
                assert subject._require_empty_io_budget(plan, continuation=value) is None
                assert value._first is None
            else:
                first = content_error(lambda: subject._require_empty_io_budget(plan, continuation=value), "LIMIT")
                selected["expected_first"] = first
                assert subject._CONTENT_PLANS[id(plan)][6] is value._first is first
                same_error(lambda: subject._content_plan(plan, continuation=value), first)
                assert value._budget.control_bytes == target and (value._budget.names, value._budget.name_bytes) == names
                raise first
            assert value._budget.control_bytes == target and (value._budget.names, value._budget.name_bytes) == names
    seen, counts = observe_factory(monkeypatch, consumer)
    database_before = raw_database_node(actual_prefix_env)
    if boundary == "below":
        publish(actual_prefix_env)
        successful_outer(seen, counts, actual_prefix_env, database_before)
    else:
        with pytest.raises(subject.StoreContentPlanError) as caught:
            publish(actual_prefix_env)
        assert caught.value is seen[0]["expected_first"] and caught.value.code == "ENROLLMENT_STORE_CONTENT_LIMIT"
        failed_outer(seen, counts, actual_prefix_env, database_before, caught.value)


def test_gate_rechecks_current_original_budget_without_charge_or_new_budget(actual_prefix_env, monkeypatch):
    def consumer(value, selected):
        with observe_pure(value, selected, monkeypatch):
            plan = subject._new_content_plan(value)
            selected["plan"] = plan
            cost = actual_materials(plan, value, selected)
            original = value._budget.control_bytes
            assert type(original) is int and original + cost < LIMIT
            assert subject._require_empty_io_budget(plan, continuation=value) is None
            assert value._budget.control_bytes == original
            value._budget.control_bytes = LIMIT - cost
            first = content_error(lambda: subject._require_empty_io_budget(plan, continuation=value), "LIMIT")
            selected["expected_first"] = first
            assert value._budget is selected["budget"] and value._budget.control_bytes >= original
            assert value._budget.control_bytes + cost == LIMIT
            assert value._budget._requested_deadline is selected["parent"] and value._budget._effective_deadline is selected["effective"]
            assert selected["budget_calls"] == Counter()
            same_error(lambda: subject._require_empty_io_budget(plan, continuation=value), first)
            raise first
    seen, counts = observe_factory(monkeypatch, consumer)
    database_before = raw_database_node(actual_prefix_env)
    with pytest.raises(subject.StoreContentPlanError) as caught:
        publish(actual_prefix_env)
    assert caught.value is seen[0]["expected_first"] and caught.value.code == "ENROLLMENT_STORE_CONTENT_LIMIT"
    failed_outer(seen, counts, actual_prefix_env, database_before, caught.value)


@pytest.mark.parametrize("kind", ["runtime", "exit"], ids=["runtime", "exit"])
def test_damaged_plan_retires_with_same_original_first(actual_prefix_env, monkeypatch, kind):
    expected_first = RuntimeError("r910 fixed own body first") if kind == "runtime" else SystemExit("r910 fixed own control first")
    def consumer(value, selected):
        with observe_pure(value, selected, monkeypatch):
            plan = subject._new_content_plan(value)
            selected["plan"] = plan
            actual_materials(plan, value, selected)
            try:
                own_throw(expected_first, selected)
            except BaseException as first:
                assert first is expected_first and value._first is None
                original_pins = plan._pins
                plan._pins = tuple(list(original_pins))
                assert plan._pins == original_pins and plan._pins is not original_pins
                assert subject._CONTENT_PLANS[id(plan)][4] is original_pins
                assert subject._retire_content_plan(plan, primary=first) is None
                assert value._first is first
                assert "ENROLLMENT_STORE_CONTENT_ADDITIONAL_FAILURE" in first.__notes__
                plan_retired(plan, value)
                content_error(lambda: subject._content_plan(plan, continuation=value), "CLOSED")
                content_error(lambda: subject._retire_content_plan(plan, primary=first), "CLOSED")
                own_throw_survives(first, selected)
                raise
    seen, counts = observe_factory(monkeypatch, consumer)
    database_before = raw_database_node(actual_prefix_env)
    with pytest.raises(type(expected_first)) as caught:
        publish(actual_prefix_env)
    assert caught.value is expected_first
    own_throw_survives(caught.value, seen[0])
    failed_outer(seen, counts, actual_prefix_env, database_before, expected_first)


def test_content_first_keeps_priority_over_actual_stage_close_secondary(actual_prefix_env, monkeypatch):
    secondary = RuntimeError("r910 own close-layer secondary after actual OS closure")
    def consumer(value, selected):
        with observe_pure(value, selected, monkeypatch):
            plan = subject._new_content_plan(value)
            selected["plan"] = plan
            cost = actual_materials(plan, value, selected)
        # The native observation uses the already verified r907 oracle.
        # Its control charges are actual native work, outside the pure observer.
        selected.update({"original_pins": selected["continuation_pins"], "packet_slot": selected["slot"],
                         "actual_owners": [], "native_calls": [], "os_closed": [], "fixture_closed": [],
                         "counts": Counter(), "scope": None, "journal": None})
        first = None
        original_first_tb = None
        try:
            with observed_stage(value, selected, monkeypatch, close_fault=secondary):
                acquire_two(value, selected)
                actual_counter = value._budget.control_bytes
                target = LIMIT - cost
                assert type(actual_counter) is int and 0 <= actual_counter <= target <= LIMIT
                assert value._budget is selected["budget"]
                value._budget.control_bytes = target
                selected["controlled_gate"] = {"actual_native_C0": actual_counter, "injected_C0": target,
                                               "D": cost, "sum": target + cost, "controlled": True, "monotonic": True}
                first = content_error(lambda: subject._require_empty_io_budget(plan, continuation=value), "LIMIT")
                selected["expected_first"] = first
                assert type(first) is subject.StoreContentPlanError and first is not secondary
                assert subject._CONTENT_PLANS[id(plan)][6] is value._first is first
                original_first_tb = value._first_tb
                assert subject._CONTENT_PLANS[id(plan)][7] is original_first_tb
                assert value._budget.control_bytes == target
                # A is caught here and not supplied as a body primary. Ordinary
                # stage exit must inherit it before the actual close fault B.
        except subject.StoreContentPlanError as observed:
            assert observed is first and observed.code == "ENROLLMENT_STORE_CONTENT_LIMIT"
            assert value._first is first and value._first_tb is original_first_tb
            assert runtime._SCOPE_ROWS[id(selected["scope"])][6] is first
            assert runtime._SCOPE_ROWS[id(selected["scope"])][7] is original_first_tb
            cleaned(selected, unknown=1, first=True)
            assert selected["actual_owners"][-1][1]._receipt.native_state == "UNKNOWN"
            assert selected["actual_owners"][0][1]._receipt.native_state == "CLOSED"
            assert secondary in (selected["actual_owners"][-1][0].failure, selected["actual_owners"][-1][1]._failure)
            assert selected["scope"]._receipt is value._receipt is None
            assert id(selected["scope"]) not in runtime._CLOSE_RECEIPTS
            before = list(selected["native_calls"])
            same_error(selected["actual_owners"][-1][0].close, secondary)
            assert selected["native_calls"] == before
            assert selected["fixture_closed"] == []
            raise
        pytest.fail("actual stage cleanup replaced or lost the existing content first")
    seen, counts = observe_factory(monkeypatch, consumer)
    database_before = raw_database_node(actual_prefix_env)
    with pytest.raises(subject.StoreContentPlanError) as caught:
        publish(actual_prefix_env)
    assert caught.value is seen[0]["expected_first"] and caught.value is not secondary
    assert seen[0]["fixture_closed"] == []
    failed_outer(seen, counts, actual_prefix_env, database_before, caught.value, debt=True)


@pytest.mark.parametrize("fault", ["release-before-actual", "row-clear-after-actual"],
                         ids=["release-before-actual", "row-clear-after-actual"])
def test_content_first_retires_plan_when_registered_cleanup_delivery_fails(actual_prefix_env, monkeypatch, fault):
    secondary = RuntimeError("r910 own registered-cleanup delivery secondary")
    def consumer(value, selected):
        with observe_pure(value, selected, monkeypatch):
            plan = subject._new_content_plan(value)
            selected["plan"] = plan
            cost = actual_materials(plan, value, selected)
        selected.update({"original_pins": selected["continuation_pins"], "packet_slot": selected["slot"],
                         "actual_owners": [], "native_calls": [], "os_closed": [], "fixture_closed": [],
                         "counts": Counter(), "scope": None, "journal": None})
        with observed_stage(value, selected, monkeypatch):
            acquire_two(value, selected)
        cleaned(selected)
        assert selected["snapshot"].all_registered_native_closed is True
        assert all(item.native_state == "CLOSED" for item in selected["snapshot"].facts)
        issued = runtime._stage_close_receipt(selected["scope"])
        selected["issued_stage_receipt"] = issued
        selected["issued_stage_receipt_row"] = runtime._CLOSE_RECEIPTS[id(selected["scope"])]
        assert selected["issued_stage_receipt_row"][2] is issued
        assert selected["issued_stage_receipt_row"][3] is selected["snapshot"]
        assert issued is selected["scope"]._receipt is value._receipt
        assert issued.native_snapshot is selected["snapshot"] and issued.registered_native_count == 2
        assert issued.source_closed is issued.write_authority is False
        current = value._budget.control_bytes
        target = LIMIT - cost
        assert type(current) is int and 0 <= current <= target <= LIMIT
        value._budget.control_bytes = target
        first = content_error(lambda: subject._require_empty_io_budget(plan, continuation=value), "LIMIT")
        selected["expected_first"] = first
        assert first is not secondary and subject._CONTENT_PLANS[id(plan)][6] is value._first is first
        original_first_tb = value._first_tb
        before_native = (list(selected["native_calls"]), list(selected["os_closed"]), selected["counts"].copy())
        delivery_calls = Counter()
        real_release, real_replace = custody._release_retired_history, runtime._replace_cleanup_row

        def release(journal):
            if journal is selected["journal"]:
                delivery_calls["release_call"] += 1
                if fault == "release-before-actual":
                    own_throw(secondary, selected)
            result = real_release(journal)
            if journal is selected["journal"]:
                delivery_calls["release_actual_return"] += 1
                assert result is None
            return result

        def replace(scope, before, after):
            is_clear = scope is selected["scope"] and after[3] == () and after[4] is None
            if is_clear:
                delivery_calls["row_clear_call"] += 1
            result = real_replace(scope, before, after)
            if is_clear:
                delivery_calls["row_clear_actual_return"] += 1
                assert result is None and runtime._SCOPE_ROWS[id(scope)] is after
                selected["actual_cleared_row"] = after
                if fault == "row-clear-after-actual":
                    own_throw(secondary, selected)
            return result

        with monkeypatch.context() as patch:
            patch.setattr(custody, "_release_retired_history", release)
            patch.setattr(runtime, "_replace_cleanup_row", replace)
            # The actual product retirement itself must raise A. The factory
            # hook records this only attempt and does not retire a second time.
            same_error(selected["retire_direct_once"], first)
        assert (selected["native_calls"], selected["os_closed"], selected["counts"]) == before_native
        assert selected["fixture_closed"] == []
        assert value._first is first and value._first_tb is original_first_tb
        assert value._phase == "retained_failed" and value._pins is selected["continuation_pins"]
        retained = runtime._RETAINED_RETIREMENTS[id(value)]
        assert type(retained) is runtime._RetainedRetirement
        assert retained._value is value and retained._scope is selected["scope"]
        assert retained._journal is selected["journal"] and retained._history is selected["original_history"]
        assert retained._row[3] is selected["continuation_pins"] and retained._row[4] is selected["journal"]
        assert retained._first is first and retained._first_tb is original_first_tb
        assert retained._content_returned is True and retained._phase == "retained_failed"
        assert retained._observation_returned is True
        if fault == "release-before-actual":
            assert delivery_calls == Counter(release_call=1)
            assert retained._history_release_returned is retained._row_clear_returned is retained._row_clear_observed is False
            assert retained._history_release_observed is False
            assert runtime._SCOPE_ROWS[id(selected["scope"])][4] is selected["journal"]
        else:
            assert delivery_calls == Counter(release_call=1, release_actual_return=1, row_clear_call=1, row_clear_actual_return=1)
            assert retained._history_release_returned is True and retained._row_clear_returned is False
            assert retained._history_release_observed is True
            assert retained._row_clear_observed is True
            actual = runtime._SCOPE_ROWS[id(selected["scope"])]
            assert actual is selected["actual_cleared_row"] and actual[3] == () and actual[4] is None
            assert retained._row is not actual
        plan_retired(plan, value)
        own_throw_survives(secondary, selected)
        raise first
    seen, counts = observe_factory(monkeypatch, consumer)
    database_before = raw_database_node(actual_prefix_env)
    with pytest.raises(subject.StoreContentPlanError) as caught:
        publish(actual_prefix_env)
    assert caught.value is seen[0]["expected_first"] and caught.value is not secondary
    assert seen[0]["fixture_closed"] == []
    failed_outer(seen, counts, actual_prefix_env, database_before, caught.value, debt=True, manual=True)


@pytest.mark.parametrize("entry", ["getter-before-damaged-business", "direct-retire-without-primary"],
                         ids=["getter-before-damaged-business", "direct-retire-without-primary"])
def test_existing_stage_first_precedes_damaged_plan_business(actual_prefix_env, monkeypatch, entry):
    expected_first = RuntimeError("r910 own actual-stage first before plan business damage")
    def consumer(value, selected):
        with observe_pure(value, selected, monkeypatch):
            plan = subject._new_content_plan(value)
            selected["plan"] = plan
            actual_materials(plan, value, selected)
        assert subject._CONTENT_PLANS[id(plan)][6] is value._first is None
        selected.update({"original_pins": selected["continuation_pins"], "packet_slot": selected["slot"],
                         "actual_owners": [], "native_calls": [], "os_closed": [], "fixture_closed": [],
                         "counts": Counter(), "scope": None, "journal": None})
        try:
            with observed_stage(value, selected, monkeypatch):
                acquire_two(value, selected)
                own_throw(expected_first, selected)
        except RuntimeError as first:
            assert first is expected_first and value._first is first
            cleaned(selected, first=True)
            assert selected["snapshot"].unknown_count == 0
            assert all(item.native_state == "CLOSED" for item in selected["snapshot"].facts)
            assert selected["scope"]._receipt is value._receipt is None
            assert id(selected["scope"]) not in runtime._CLOSE_RECEIPTS
            original_first_tb = value._first_tb
            assert runtime._SCOPE_ROWS[id(selected["scope"])][6] is first
            assert runtime._SCOPE_ROWS[id(selected["scope"])][7] is original_first_tb
            assert subject._CONTENT_PLANS[id(plan)][6] is None
            pins = plan._pins
            plan._pins = tuple(list(pins))
            assert plan._pins == pins and plan._pins is not pins
            assert subject._CONTENT_PLANS[id(plan)][4] is pins
            if entry == "getter-before-damaged-business":
                same_error(lambda: subject._content_plan(plan, continuation=value), first)
                assert value._first is first and value._first_tb is original_first_tb
                assert subject._CONTENT_PLANS[id(plan)][6] is first
                assert subject._CONTENT_PLANS[id(plan)][7] is original_first_tb
            else:
                assert entry == "direct-retire-without-primary"
                assert subject._CONTENT_PLANS[id(plan)][6] is None
            before_native = (list(selected["native_calls"]), list(selected["os_closed"]), selected["counts"].copy())
            same_error(lambda: subject._retire_content_plan(plan), first)
            plan_retired(plan, value)
            assert value._first is first and value._first_tb is original_first_tb
            assert (selected["native_calls"], selected["os_closed"], selected["counts"]) == before_native
            assert selected["fixture_closed"] == []
            own_throw_survives(first, selected)
            raise
        pytest.fail("actual stage body first returned successfully")
    seen, counts = observe_factory(monkeypatch, consumer)
    database_before = raw_database_node(actual_prefix_env)
    with pytest.raises(RuntimeError) as caught:
        publish(actual_prefix_env)
    assert caught.value is expected_first
    own_throw_survives(caught.value, seen[0])
    assert seen[0]["fixture_closed"] == []
    failed_outer(seen, counts, actual_prefix_env, database_before, expected_first)


def test_getter_and_budget_after_original_ex_are_closed(actual_prefix_env, monkeypatch):
    def consumer(value, selected):
        with observe_pure(value, selected, monkeypatch):
            plan = subject._new_content_plan(value)
            selected["plan"] = plan
            actual_materials(plan, value, selected)
            assert subject._require_empty_io_budget(plan, continuation=value) is None
    seen, counts = observe_factory(monkeypatch, consumer)
    database_before = raw_database_node(actual_prefix_env)
    publish(actual_prefix_env)
    successful_outer(seen, counts, actual_prefix_env, database_before)
    value, plan = seen[0]["continuation"], seen[0]["plan"]
    content_error(lambda: subject._content_plan(plan, continuation=value), "CLOSED")
    content_error(lambda: subject._require_empty_io_budget(plan, continuation=value), "CLOSED")
    content_error(lambda: subject._retire_content_plan(plan), "CLOSED")
    content_error(lambda: subject._new_content_plan(value), "CLOSED")
