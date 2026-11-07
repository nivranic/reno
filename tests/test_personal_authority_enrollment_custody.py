"""r872 SOURCE-only test candidate; Root alone may admit/collect/run.

Complete canonical modules and actual Windows owned EX are required. Fault
seams use the ordinary dormant replica scope and never replace its verifier.
active_original_graph_native_whole_acceptance = False. Registered native slot
terminal facts do not prove acquisition coverage, source closure or writing.
"""
from collections import Counter
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, fields
import ctypes
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace

import pytest

from reno import personal_authority_enrollment as enrollment
from reno import personal_authority_enrollment_custody as custody
from reno import personal_authority_namespace3_deadline as deadline
from reno import personal_authority_namespace3_io as native
from reno import personal_authority_owned_ex_deadline as owned
from reno import personal_authority_owned_ex_group as group
from reno import personal_authority_replica_operation as operation
from reno import storage_lifecycle as lifecycle

pytestmark = pytest.mark.skipif(os.name != "nt", reason="r872 actual Windows namespace native custody")
OPERATION = ("00000000-0000-4000-8000-000000000872",
             "00000000-0000-4000-8000-000000000873",
             "00000000-0000-4000-8000-000000000874")
ACTIVE_ORIGINAL_GRAPH_NATIVE_WHOLE_ACCEPTANCE = False
NATIVE_INVENTORY = (
    ("_kernel", ("CreateFileW", "CloseHandle", "LocalFree", "GetCurrentProcess",
        "GetFileInformationByHandle", "ReadFile", "WriteFile", "FlushFileBuffers", "SetFilePointerEx")),
    ("_advapi", ("OpenProcessToken", "GetTokenInformation", "ConvertSidToStringSidW", "ConvertStringSidToSidW",
        "ConvertStringSecurityDescriptorToSecurityDescriptorW", "GetSecurityInfo", "GetSecurityDescriptorControl",
        "IsValidSecurityDescriptor", "IsValidSid", "GetLengthSid", "EqualSid", "GetAclInformation", "GetAce")),
    ("_ntdll", ("NtCreateFile", "NtSetInformationFile")),
)


@pytest.fixture(autouse=True)
def isolated_qa(tmp_path):
    qa = Path(os.environ["RENO_QA_ROOT"]).resolve(strict=True)
    tmp_path.resolve(strict=True).relative_to(qa)
    Path(tempfile.gettempdir()).resolve(strict=True).relative_to(qa)
    assert lifecycle._CURRENT.get() is None and custody._CURRENT.get() is None
    yield
    assert lifecycle._CURRENT.get() is None and custody._CURRENT.get() is None


def forbidden(*_args, **_kwargs):
    pytest.fail("cached getter or repeated numeric close reached a forbidden dynamic seam")


def same_error(call, first):
    with pytest.raises(type(first)) as caught:
        call()
    assert caught.value is first
    return caught.value


def code_error(call, kind, code):
    with pytest.raises(kind) as caught:
        call()
    assert caught.value.code == code
    return caught.value


def fixture_cleanup_owner(owner, primary=None):
    # retired is only a once-attempt guard here, never a native-close proof.
    # A product attempt with unknown result must not cause numeric retries.
    if owner.retired:
        return
    try:
        owner.close()
    except BaseException:
        if primary is None:
            raise
        native._note(primary)


def first_of(kind):
    return {"runtime": RuntimeError, "memory": MemoryError,
            "exit": SystemExit, "interrupt": KeyboardInterrupt}[kind]("r872 consumer-local first")


def legal_bool(value):
    assert type(value) is int and -(1 << 31) <= value < (1 << 31) and value != 0
    return value


class IntegerSubclass(int):
    pass


def invalid_value(kind):
    return {"zero": 0, "false": False, "true": True, "none": None,
            "one": 1, "wide-positive": 1 << 31, "wide-negative": -(1 << 31) - 1,
            "int-subclass": IntegerSubclass(1), "opaque": object()}[kind]


def no_authority(receipt):
    assert receipt.source_closed is False and receipt.write_authority is False
    assert not hasattr(receipt, "__dict__")
    assert not ({field.name for field in fields(receipt)} &
                {"handle", "pointer", "fd", "resource", "provider", "closer", "callback", "exception"})
    with pytest.raises(FrozenInstanceError):
        receipt.source_closed = True
    if type(receipt) is custody.NativeOwnerJournalSnapshotV1:
        assert receipt.acquisition_coverage_proven is False


def complete_catalog():
    from reno import personal_authority_enrollment_owner_dispatch as dispatch
    from reno import personal_authority_enrollment_store_custody as store_custody
    assert native._custody is dispatch and enrollment.native is native
    assert dispatch.source is custody and enrollment.custody is custody
    assert dispatch.store is store_custody
    assert dispatch._signed32 is custody._signed32
    assert dispatch._HANDLE is custody._HANDLE and dispatch._LOCALALLOC is custody._LOCALALLOC
    assert dispatch._POINTER_MAX is custody._POINTER_MAX
    modules = group._catalog_modules_original()
    assert any(value is custody for value in modules) and any(value is native for value in modules)
    assert len(operation._OWNED_EX_FIXED_MODULES) == len(modules)
    assert all(left is right for left, right in zip(operation._OWNED_EX_FIXED_MODULES, modules))
    expected = tuple(getattr(getattr(native, dll), name) for dll, names in NATIVE_INVENTORY for name in names)
    assert len(expected) == 24
    actual = group._native_custody_functions_original()
    assert len(actual) == len(expected) and all(left is right for left, right in zip(actual, expected))
    for function in expected:
        rows = [row for row in operation._NATIVE_SIGNATURES if row[0] is function]
        assert len(rows) == 1
        row = rows[0]
        assert row[1] is function.argtypes and tuple(function.argtypes) == row[2]
        assert row[3] is function.restype and row[4] is function.errcheck
    abi = operation.native._classes_of_native_signature_original(expected)
    assert all(any(selected is fixed for fixed in operation._OWNED_EX_ABI_CLASSES) for selected in abi)
    extras = group._catalog_extra_original()
    selected = [names for selected, names in extras if selected is custody]
    assert len(selected) == 1
    assert {"_CURRENT", "ContextVar", "_SEAL", "_GROUP_JOURNALS", "_BUDGET_JOURNALS",
            "_OWNER_SLOTS", "_ROLES", "_JOURNAL_RECEIPT", "_SLOT_RECEIPT"} <= set(selected[0])
    assert ACTIVE_ORIGINAL_GRAPH_NATIVE_WHOLE_ACCEPTANCE is False


def new_journal(env, **replace):
    args = dict(operation=env.binding._operation, parent_token=env.binding._parent_token,
                effective_token=env.binding._effective_token,
                scope_identity=env.binding._scope_identity, budget=env.budget)
    args.update(replace)
    return custody._new_journal(env.lease, **args)


def activate(env):
    env.journal = new_journal(env)
    assert env.journal.close_receipt is None
    custody._install(env.journal)
    assert custody._CURRENT.get() is env.journal
    return env.journal


def finish(env, *, primary=None):
    custody._reset(env.journal)
    env.snapshot = custody._seal(env.journal, primary=primary)
    no_authority(env.snapshot)
    assert env.journal.close_receipt is env.snapshot
    return env.snapshot


@contextmanager
def actual_scope(tmp_path, *, install=True):
    qa = Path(os.environ["RENO_QA_ROOT"]).resolve(strict=True)
    tmp_path.resolve(strict=False).relative_to(qa)
    complete_catalog()
    root = tmp_path / "p"
    profile = lifecycle.StorageProfile(root / "d/r.db", root / "m", root / "r", root / "c")
    assert lifecycle.initialize(profile)["created_locks"] == len(profile.roots)
    requested = deadline.Namespace3Deadline(deadline._now() + 30.0)
    budget = enrollment._Budget(requested)
    assert budget.requested_deadline is requested
    assert 0.0 < budget.effective_deadline.absolute_monotonic - deadline._now() <= 30.0
    binding = owned._new_owned_ex_binding(OPERATION, parent_token=budget.requested_deadline,
        effective_token=budget.effective_deadline, scope_identity=object())
    lease = group.OwnedExclusiveDeadlineLease(profile, binding=binding, declared_capture_count=0)
    budget._owned_ex = lease
    env = SimpleNamespace(profile=profile, budget=budget, binding=binding,
                          lease=lease, journal=None, snapshot=None, guard=None)
    primary = None
    with lease as guard:
        env.guard = guard
        assert guard is lifecycle.require_guard(profile, exclusive=True)
        assert group._registered(lease) is lease and lease._phase == "active"
        if install:
            activate(env)
        try:
            yield env
        except BaseException as failure:
            primary = failure
            raise
        finally:
            if env.journal is not None:
                if not env.journal._reset_attempted:
                    custody._reset(env.journal)
                if not env.journal._seal_attempted:
                    env.snapshot = custody._seal(env.journal, primary=primary)
    receipt = lease.close_receipt
    assert receipt.source_closed is False and receipt.write_authority is False


def parent_owner(env):
    return native._open_parent_raw(env.profile.database.parent, write=False, budget=env.budget)


def slot_for(owner):
    slot = custody._find_owner_slot(owner)
    assert type(slot) is custody._NativeSlot
    assert slot._known is True and slot._representation_confirmed is True
    return slot


def assert_closed(slot):
    fact = slot.close_receipt
    assert type(fact) is custody.NativeOwnerCloseFactV1
    assert fact.acquisition_attempted is True and fact.native_acquired is True
    assert fact.close_attempted is True and fact.native_state == "CLOSED"
    no_authority(fact)


def all_closed(snapshot):
    assert snapshot.registered_count == len(snapshot.facts) > 0
    assert snapshot.unknown_count == 0
    assert all(fact.native_state == "CLOSED" for fact in snapshot.facts)
    for fact in snapshot.facts:
        no_authority(fact)
    no_authority(snapshot)


def actual_tree(env):
    with native.open_parent_locked(env.profile, env.budget, write=True) as parent:
        with native.create_root_locked(env.profile, parent, env.budget) as root:
            parent.verify()
            root.verify()
            native._security_check(root.handle, env.budget)
        with native.open_root_locked(env.profile, parent, env.budget) as root:
            root.verify()
            native._security_check(root.handle, env.budget)


def test_complete_fixed_catalog_and_actual_native_abi_integration():
    complete_catalog()
    assert custody._JOURNAL_RECEIPT is custody._NativeOwnerJournal.__dict__["_receipt"]
    assert custody._SLOT_RECEIPT is custody._NativeSlot.__dict__["_receipt"]
    assert native._OWNER_DICT is native._Owner.__dict__["__dict__"]


def test_actual_seven_acquisition_roles_recursive_verify_security_close_without_authority(tmp_path):
    with actual_scope(tmp_path) as env:
        actual_tree(env)
        roles = Counter(slot._role for slot in env.journal._slots)
        assert set(roles) == {name for name, _kind in custody._ROLES}
        assert roles["parent"] > 1 and roles["relative"] > 1 and roles["security_descriptor"] > 1
        assert len(env.journal._slots) == sum(roles.values())
        snapshot = finish(env)
        all_closed(snapshot)
        assert snapshot.all_registered_native_closed is True
        assert snapshot.first_present is False
        assert snapshot.install_state == "installed" and snapshot.reset_state == "reset"


@pytest.mark.parametrize("reported", [1, -1, (1 << 31) - 1, -(1 << 31)],
                         ids=["one", "minus-one", "signed32-max", "signed32-min"])
def test_controlled_closehandle_legal_signed32_returns_close_once(tmp_path, monkeypatch, reported):
    with actual_scope(tmp_path) as env:
        owner, counts = parent_owner(env), Counter()
        slot, real = slot_for(owner), native._kernel.CloseHandle
        def close(handle):
            counts["target"] += 1
            legal_bool(real(handle))
            return reported
        with monkeypatch.context() as patch:
            patch.setattr(native._kernel, "CloseHandle", close)
            owner.close()
            owner.close()
        assert counts["target"] == 1 and slot._close_return is reported
        assert_closed(slot)
        snapshot = finish(env)
        all_closed(snapshot)
        assert snapshot.all_registered_native_closed is True


@pytest.mark.parametrize("kind", ["zero", "false", "true", "none", "wide-positive", "wide-negative", "int-subclass", "opaque"],
                         ids=["zero", "false", "true", "none", "wide-positive", "wide-negative", "int-subclass", "opaque"])
def test_controlled_closehandle_invalid_return_keeps_unknown_without_retry(tmp_path, monkeypatch, kind):
    with actual_scope(tmp_path) as env:
        owner, counts, reported = parent_owner(env), Counter(), invalid_value(kind)
        slot, real = slot_for(owner), native._kernel.CloseHandle
        def close(handle):
            counts["target"] += 1
            legal_bool(real(handle))
            counts["fixture_witnessed_actual_close"] += 1
            return reported
        with monkeypatch.context() as patch:
            patch.setattr(native._kernel, "CloseHandle", close)
            if kind == "zero":
                with pytest.raises(OSError) as caught:
                    owner.close()
            else:
                with pytest.raises(native.Namespace3IOError) as caught:
                    owner.close()
                assert caught.value.code == "NAMESPACE3_IO_NATIVE_CLOSE_UNCONFIRMED"
            same_error(owner.close, caught.value)
        assert counts == Counter(target=1, fixture_witnessed_actual_close=1)
        assert slot._close_return is reported and slot._failure is caught.value
        assert slot.close_receipt.native_state == "UNKNOWN" and slot.close_receipt.close_attempted is True
        snapshot = finish(env)
        assert snapshot.unknown_count == 1 and snapshot.all_registered_native_closed is False
        assert snapshot.first_present is True


@pytest.mark.parametrize("kind", ["zero", "false", "true", "one", "int-subclass", "opaque"],
                         ids=["zero", "false", "true", "one", "int-subclass", "opaque"])
def test_controlled_localfree_nonnull_or_illegal_return_keeps_unknown_without_retry(tmp_path, monkeypatch, kind):
    with actual_scope(tmp_path) as env:
        carrier = native.w.LPVOID()
        owner = native._acquire_system_sid(carrier, env.budget)
        slot, counts, reported, real = slot_for(owner), Counter(), invalid_value(kind), native._kernel.LocalFree
        def free(pointer):
            counts["target"] += 1
            assert real(pointer) is None
            counts["fixture_witnessed_actual_free"] += 1
            return reported
        with monkeypatch.context() as patch:
            patch.setattr(native._kernel, "LocalFree", free)
            if kind == "one":
                with pytest.raises(OSError) as caught:
                    owner.close()
            else:
                with pytest.raises(native.Namespace3IOError) as caught:
                    owner.close()
                assert caught.value.code == "NAMESPACE3_IO_NATIVE_FREE_UNCONFIRMED"
            same_error(owner.close, caught.value)
        assert counts == Counter(target=1, fixture_witnessed_actual_free=1)
        assert slot._close_return is reported and slot.close_receipt.native_state == "UNKNOWN"
        snapshot = finish(env)
        assert snapshot.unknown_count == 1 and snapshot.first_present is True
        assert snapshot.all_registered_native_closed is False


def test_controlled_reserve_failure_rejects_before_native(tmp_path, monkeypatch):
    with actual_scope(tmp_path) as env:
        first, counts = MemoryError("r872 slot reservation first"), Counter()
        def allocate_fact(*args, **_kwargs):
            assert args[1] == custody._HANDLE and args[2] == 0
            counts["reservation_fact_allocation"] += 1
            raise first
        def create(*_args):
            counts["native"] += 1
            pytest.fail("native API reached after reservation failure")
        with monkeypatch.context() as patch:
            patch.setattr(custody, "NativeOwnerCloseFactV1", allocate_fact)
            patch.setattr(native._kernel, "CreateFileW", create)
            same_error(lambda: parent_owner(env), first)
        assert counts == Counter(reservation_fact_allocation=1) and env.journal._slots == []
        snapshot = finish(env, primary=first)
        assert snapshot.registered_count == 0 and snapshot.first_present is True
        assert snapshot.all_registered_native_closed is False


def test_actual_each_api_reserves_before_and_adopts_before_registration(tmp_path, monkeypatch):
    with actual_scope(tmp_path) as env:
        counts = Counter()
        sites = ((native._advapi, "OpenProcessToken", "token"),
                 (native._advapi, "ConvertStringSidToSidW", "system_sid"),
                 (native._advapi, "ConvertSidToStringSidW", "sid_text"),
                 (native._advapi, "ConvertStringSecurityDescriptorToSecurityDescriptorW", "private_descriptor"),
                 (native._advapi, "GetSecurityInfo", "security_descriptor"),
                 (native._kernel, "CreateFileW", "parent"), (native._ntdll, "NtCreateFile", "relative"))
        def wrapped(real, role):
            def call(*args):
                slot = env.journal._slots[-1]
                assert slot._role == role and slot._acquire_attempted is True
                assert slot._known is False and slot._return_seen is False
                counts[role] += 1
                return real(*args)
            return call
        real_init = native._Owner.__init__
        def construct(self, value, closer, *, _custody_slot=None):
            assert _custody_slot is not None and _custody_slot._known is True
            assert _custody_slot._representation_confirmed is True and _custody_slot._return_seen is True
            assert _custody_slot.close_receipt.native_acquired is True
            counts["constructor"] += 1
            return real_init(self, value, closer, _custody_slot=_custody_slot)
        with monkeypatch.context() as patch:
            for dll, name, role in sites:
                patch.setattr(dll, name, wrapped(getattr(dll, name), role))
            patch.setattr(native._Owner, "__init__", construct)
            actual_tree(env)
        roles = Counter(slot._role for slot in env.journal._slots)
        assert set(roles) == {name for name, _kind in custody._ROLES}
        assert all(counts[role] == count for role, count in roles.items())
        assert counts["constructor"] == len(env.journal._slots)
        all_closed(finish(env))


@pytest.mark.parametrize("stage", ["before-native", "after-witnessed-native", "after-zero-return", "after-opaque-return"],
                         ids=["before-native", "after-witnessed-native", "after-zero-return", "after-opaque-return"])
def test_controlled_opaque_acquisition_keeps_unknown_fixture_recovery_separate(tmp_path, monkeypatch, stage):
    with actual_scope(tmp_path) as env:
        first, counts, witness = SystemExit("r872 opaque acquisition first"), Counter(), {}
        carrier = native.w.LPVOID()
        real, real_free = native._advapi.ConvertStringSidToSidW, native._kernel.LocalFree
        def product_free(pointer):
            counts["product_native_free_attempt"] += 1
            return real_free(pointer)
        def acquire(*args):
            counts["target"] += 1
            slot = env.journal._slots[-1]
            assert slot._role == "system_sid" and slot._known is False
            if stage == "before-native":
                raise first
            legal_bool(real(*args))
            # Only this fixture witnessed the exact original successful output.
            witness["known_fixture_allocation"] = carrier.value
            counts["witnessed_actual_acquire"] += 1
            if stage == "after-zero-return":
                return 0
            if stage == "after-opaque-return":
                return object()
            raise first
        try:
            with monkeypatch.context() as patch:
                patch.setattr(native._advapi, "ConvertStringSidToSidW", acquire)
                patch.setattr(native._kernel, "LocalFree", product_free)
                if stage in ("before-native", "after-witnessed-native"):
                    same_error(lambda: native._acquire_system_sid(carrier, env.budget), first)
                elif stage == "after-zero-return":
                    with pytest.raises(OSError):
                        native._acquire_system_sid(carrier, env.budget)
                else:
                    code_error(lambda: native._acquire_system_sid(carrier, env.budget),
                               custody.EnrollmentCustodyError, "ENROLLMENT_CUSTODY_NATIVE_RESULT_UNKNOWN")
            slot = env.journal._slots[-1]
            assert counts["target"] == 1 and slot._known is False and slot._close_attempted is False
            assert counts["product_native_free_attempt"] == 0
            assert slot.close_receipt.native_acquired is False and slot.close_receipt.native_state == "UNKNOWN"
            snapshot = finish(env)
            assert snapshot.unknown_count == 1 and snapshot.all_registered_native_closed is False
        finally:
            if "known_fixture_allocation" in witness and not counts["product_native_free_attempt"]:
                assert real_free(witness.pop("known_fixture_allocation")) is None
                counts["fixture_recovery"] += 1
        assert counts["fixture_recovery"] == int(stage != "before-native")
        assert slot.close_receipt.native_state == "UNKNOWN"


@pytest.mark.parametrize("selected", ["constructor-memory", "constructor-exit", "postclock-runtime", "postclock-interrupt"],
                         ids=["constructor-memory", "constructor-exit", "postclock-runtime", "postclock-interrupt"])
def test_controlled_constructor_and_postclock_first_close_known_owner_once(tmp_path, monkeypatch, selected):
    stage, kind = selected.split("-")
    with actual_scope(tmp_path) as env:
        first, counts = first_of(kind), Counter()
        real_init, real_clock, real_close = native._Owner.__init__, enrollment._Budget.clock_only, native._kernel.CloseHandle
        def construct(self, value, closer, *, _custody_slot=None):
            assert _custody_slot is not None and _custody_slot._known is True
            counts["constructor"] += 1
            if stage == "constructor":
                counts["target"] += 1
                raise first
            return real_init(self, value, closer, _custody_slot=_custody_slot)
        def clock(self):
            if (self is env.budget and env.journal._slots and env.journal._slots[-1]._owner is not None
                    and not counts["target"]):
                counts["target"] += 1
                raise first
            return real_clock(self)
        def close(handle):
            counts["close"] += 1
            return legal_bool(real_close(handle))
        with monkeypatch.context() as patch:
            patch.setattr(native._Owner, "__init__", construct)
            patch.setattr(native._kernel, "CloseHandle", close)
            if stage == "postclock":
                patch.setattr(enrollment._Budget, "clock_only", clock)
            same_error(lambda: parent_owner(env), first)
        assert counts == Counter(constructor=1, target=1, close=1)
        slot = env.journal._slots[-1]
        assert_closed(slot)
        assert slot._failure is first and env.journal._first is first
        snapshot = finish(env)
        all_closed(snapshot)
        assert snapshot.first_present is True and snapshot.all_registered_native_closed is False


@pytest.mark.parametrize("kind", ["memory", "exit"], ids=["memory", "exit"])
def test_controlled_sid_text_cast_first_closes_adopted_localalloc_and_prior_owners(tmp_path, monkeypatch, kind):
    with actual_scope(tmp_path) as env:
        first, counts, real_cast = first_of(kind), Counter(), ctypes.cast
        real_free, real_close = native._kernel.LocalFree, native._kernel.CloseHandle
        def cast(value, target):
            if type(value) is native.w.LPWSTR and target is native.w.LPVOID:
                counts["target"] += 1
                slot = env.journal._slots[-1]
                assert slot._role == "sid_text" and slot._known is True
                assert slot._representation_confirmed is True
                raise first
            return real_cast(value, target)
        def free(pointer):
            counts["free"] += 1
            assert real_free(pointer) is None
            return None
        def close(handle):
            counts["close"] += 1
            return legal_bool(real_close(handle))
        with monkeypatch.context() as patch:
            patch.setattr(ctypes, "cast", cast)
            patch.setattr(native._kernel, "LocalFree", free)
            patch.setattr(native._kernel, "CloseHandle", close)
            def call():
                with native._private_descriptor(env.budget):
                    pytest.fail("descriptor body reached after SID cast fault")
            same_error(call, first)
        assert counts == Counter(target=1, free=2, close=1)
        assert Counter(slot._role for slot in env.journal._slots) == Counter(token=1, system_sid=1, sid_text=1)
        assert env.journal._first is first
        snapshot = finish(env)
        all_closed(snapshot)
        assert snapshot.first_present is True and snapshot.all_registered_native_closed is False


@pytest.mark.parametrize("selected", ["runtime-invalid", "memory-exception", "exit-invalid", "interrupt-exception"],
                         ids=["runtime-invalid", "memory-exception", "exit-invalid", "interrupt-exception"])
def test_controlled_body_first_survives_cleanup_unknown_and_exception_once(tmp_path, monkeypatch, selected):
    kind, seam = selected.split("-")
    with actual_scope(tmp_path) as env:
        first, cleanup, counts = first_of(kind), RuntimeError("r872 cleanup secondary"), Counter()
        owner, prior = parent_owner(env), parent_owner(env)
        slot, prior_slot, real = slot_for(owner), slot_for(prior), native._kernel.CloseHandle
        def close(handle):
            counts["close"] += 1
            legal_bool(real(handle))
            if counts["close"] == 1:
                counts["target"] += 1
                if seam == "exception":
                    raise cleanup
                return False
            return 1
        with monkeypatch.context() as patch:
            patch.setattr(native._kernel, "CloseHandle", close)
            def call():
                with native._preserve_primary(prior), native._preserve_primary(owner):
                    counts["body"] += 1
                    raise first
            same_error(call, first)
            assert owner.failure is not None
            same_error(owner.close, owner.failure)
            prior.close()
        assert counts == Counter(close=2, target=1, body=1)
        assert slot._failure is first and env.journal._first is first
        assert slot.close_receipt.native_state == "UNKNOWN"
        assert_closed(prior_slot)
        snapshot = finish(env)
        assert snapshot.unknown_count == 1 and snapshot.first_present is True
        assert snapshot.all_registered_native_closed is False


@pytest.mark.parametrize("kind", ["memory", "exit"], ids=["memory", "exit"])
def test_controlled_cleanup_first_preserved_and_repeated_close_does_not_retry(tmp_path, monkeypatch, kind):
    with actual_scope(tmp_path) as env:
        owner, first, counts = parent_owner(env), first_of(kind), Counter()
        slot, real = slot_for(owner), native._kernel.CloseHandle
        def close(handle):
            counts["target"] += 1
            legal_bool(real(handle))
            raise first
        with monkeypatch.context() as patch:
            patch.setattr(native._kernel, "CloseHandle", close)
            same_error(owner.close, first)
            same_error(owner.close, first)
        assert counts["target"] == 1 and slot._failure is first and env.journal._first is first
        assert slot.close_receipt.native_state == "UNKNOWN" and slot.close_receipt.close_attempted is True
        snapshot = finish(env)
        assert snapshot.unknown_count == 1 and snapshot.first_present is True


def test_actual_cached_frozen_slot_and_journal_getters_have_no_dynamic_access(tmp_path, monkeypatch):
    with actual_scope(tmp_path) as env:
        owner = parent_owner(env)
        slot = slot_for(owner)
        owner.close()
        fact, snapshot = slot.close_receipt, finish(env)
        assert_closed(slot)
        real_slot_get, real_journal_get = object.__getattribute__, object.__getattribute__
        def slot_get(self, name):
            if name in {"_resource", "_carrier", "_owner", "_journal"}:
                forbidden()
            return real_slot_get(self, name)
        def journal_get(self, name):
            if name in {"_group", "_budget", "_guard", "_owner", "_slots", "_pins"}:
                forbidden()
            return real_journal_get(self, name)
        with monkeypatch.context() as patch:
            patch.setattr(enrollment._Budget, "clock_only", forbidden)
            patch.setattr(native._kernel, "CloseHandle", forbidden)
            patch.setattr(native._kernel, "LocalFree", forbidden)
            patch.setattr(group, "_registered", forbidden)
            patch.setattr(lifecycle, "_owner", forbidden)
            patch.setattr(native._Owner, "__getattribute__", forbidden)
            patch.setattr(custody._NativeSlot, "__getattribute__", slot_get)
            patch.setattr(custody._NativeOwnerJournal, "__getattribute__", journal_get)
            assert slot.close_receipt is fact and env.journal.close_receipt is snapshot
            no_authority(fact)
            no_authority(snapshot)
        code_error(lambda: custody._seal(env.journal), custody.EnrollmentCustodyError, "ENROLLMENT_CUSTODY_CLOSED")
        assert env.journal.close_receipt is snapshot


@pytest.mark.parametrize("kind", ["operation-equal-tuple", "parent-equal-token", "effective-equal-token", "scope-foreign", "budget-foreign", "group-dto"],
                         ids=["operation-equal-tuple", "parent-equal-token", "effective-equal-token", "scope-foreign", "budget-foreign", "group-dto"])
def test_controlled_factory_rejects_equal_or_foreign_actual_lineage(tmp_path, kind):
    with actual_scope(tmp_path, install=False) as env:
        args = {}
        if kind == "operation-equal-tuple":
            args["operation"] = tuple(list(env.binding._operation))
            assert args["operation"] == env.binding._operation and args["operation"] is not env.binding._operation
        elif kind == "parent-equal-token":
            args["parent_token"] = deadline.Namespace3Deadline(env.binding._parent_token.absolute_monotonic)
            assert args["parent_token"] == env.binding._parent_token and args["parent_token"] is not env.binding._parent_token
        elif kind == "effective-equal-token":
            args["effective_token"] = deadline.Namespace3Deadline(env.binding._effective_token.absolute_monotonic)
            assert args["effective_token"] == env.binding._effective_token and args["effective_token"] is not env.binding._effective_token
        elif kind == "scope-foreign":
            args["scope_identity"] = object()
        elif kind == "budget-foreign":
            args["budget"] = enrollment._Budget(env.budget.requested_deadline)
            args["budget"]._owned_ex = object()
        else:
            dto = SimpleNamespace(_binding=env.binding, _guard=env.guard)
            code_error(lambda: custody._new_journal(dto, operation=env.binding._operation,
                parent_token=env.binding._parent_token, effective_token=env.binding._effective_token,
                scope_identity=env.binding._scope_identity, budget=env.budget),
                owned.OwnedExDeadlineError, "OWNED_EX_DEADLINE_INVALID_ARGUMENT")
        if kind != "group-dto":
            code_error(lambda: new_journal(env, **args), custody.EnrollmentCustodyError, "ENROLLMENT_CUSTODY_INVALID_ARGUMENT")
        assert custody._CURRENT.get() is None and dict.get(custody._GROUP_JOURNALS, id(env.lease)) is None
        activate(env)
        assert env.journal._slots == []
        finish(env)


@pytest.mark.parametrize("kind", ["operation-equal-tuple", "budget-owned-foreign", "budget-parent-equal", "budget-effective-equal", "guard-foreign", "execution-owner-foreign", "group-retired"],
                         ids=["operation-equal-tuple", "budget-owned-foreign", "budget-parent-equal", "budget-effective-equal", "guard-foreign", "execution-owner-foreign", "group-retired"])
def test_controlled_reserve_rechecks_actual_live_lineage_before_native(tmp_path, monkeypatch, kind):
    with actual_scope(tmp_path) as env:
        counts = Counter()
        def create(*_args):
            counts["native"] += 1
            pytest.fail("native API reached after active lineage tamper")
        target = env.budget
        expected_kind, expected_code = custody.EnrollmentCustodyError, "ENROLLMENT_CUSTODY_INVALID_ARGUMENT"
        if kind == "operation-equal-tuple":
            target, field, replacement = env.binding, "_operation", tuple(list(env.binding._operation))
            expected_kind, expected_code = owned.OwnedExDeadlineError, "OWNED_EX_DEADLINE_INVALID_ARGUMENT"
        elif kind == "budget-owned-foreign":
            field, replacement = "_owned_ex", object()
        elif kind == "budget-parent-equal":
            field, replacement = "_requested_deadline", deadline.Namespace3Deadline(env.budget.requested_deadline.absolute_monotonic)
        elif kind == "budget-effective-equal":
            field, replacement = "_effective_deadline", deadline.Namespace3Deadline(env.budget.effective_deadline.absolute_monotonic)
        elif kind == "execution-owner-foreign":
            target, field, replacement = env.lease, "_owner", object()
            expected_kind, expected_code = owned.OwnedExDeadlineError, "OWNED_EX_DEADLINE_INVALID_ARGUMENT"
        elif kind == "group-retired":
            target, field, replacement = env.lease, "_phase", "retired"
        else:
            target = None
        original = object.__getattribute__(target, field) if target is not None else None
        context_token = None
        try:
            if target is None:
                context_token = lifecycle._CURRENT.set(object())
            else:
                object.__setattr__(target, field, replacement)
                assert object.__getattribute__(target, field) is replacement
            with monkeypatch.context() as patch:
                patch.setattr(native._kernel, "CreateFileW", create)
                code_error(lambda: parent_owner(env), expected_kind, expected_code)
            assert counts["native"] == 0 and env.journal._slots == []
        finally:
            if context_token is not None:
                lifecycle._CURRENT.reset(context_token)
            elif target is not None:
                object.__setattr__(target, field, original)
        owner = parent_owner(env)
        slot = slot_for(owner)
        owner.close()
        assert_closed(slot)
        all_closed(finish(env))


@pytest.mark.parametrize("kind", ["missing", "dto", "foreign-exact-journal"], ids=["missing", "dto", "foreign-exact-journal"])
def test_controlled_claimed_budget_missing_or_foreign_current_rejects_before_native(tmp_path, monkeypatch, kind):
    foreign = None
    if kind == "foreign-exact-journal":
        with actual_scope(tmp_path / "foreign") as other:
            foreign = other.journal
            finish(other)
    with actual_scope(tmp_path / "actual") as env:
        counts = Counter()
        current = None if kind == "missing" else (object() if kind == "dto" else foreign)
        def create(*_args):
            counts["native"] += 1
            pytest.fail("claimed budget escaped through empty or foreign context")
        saved = custody._CURRENT.set(current)
        try:
            with monkeypatch.context() as patch:
                patch.setattr(native._kernel, "CreateFileW", create)
                code_error(lambda: parent_owner(env), custody.EnrollmentCustodyError,
                           "ENROLLMENT_CUSTODY_CURRENT_JOURNAL_UNKNOWN")
            assert counts["native"] == 0 and env.journal._slots == []
        finally:
            custody._CURRENT.reset(saved)
        owner = parent_owner(env)
        slot = slot_for(owner)
        owner.close()
        assert_closed(slot)
        all_closed(finish(env))


def test_actual_reset_and_same_group_claim_remain_once_without_closure_upgrade(tmp_path, monkeypatch):
    with actual_scope(tmp_path) as env:
        counts = Counter()
        custody._reset(env.journal)
        assert custody._CURRENT.get() is None and env.journal.close_receipt is None
        code_error(lambda: new_journal(env), custody.EnrollmentCustodyError, "ENROLLMENT_CUSTODY_CLOSED")
        code_error(lambda: custody._install(env.journal), custody.EnrollmentCustodyError, "ENROLLMENT_CUSTODY_CLOSED")
        def create(*_args):
            counts["native"] += 1
            pytest.fail("reset incorrectly removed claimed budget obligation")
        with monkeypatch.context() as patch:
            patch.setattr(native._kernel, "CreateFileW", create)
            code_error(lambda: parent_owner(env), custody.EnrollmentCustodyError, "ENROLLMENT_CUSTODY_CURRENT_JOURNAL_UNKNOWN")
        assert counts["native"] == 0 and env.journal._slots == []
        snapshot = custody._seal(env.journal)
        no_authority(snapshot)
        assert snapshot.registered_count == 0 and snapshot.all_registered_native_closed is False
        code_error(lambda: custody._reset(env.journal), custody.EnrollmentCustodyError, "ENROLLMENT_CUSTODY_CLOSED")
        code_error(lambda: custody._seal(env.journal), custody.EnrollmentCustodyError, "ENROLLMENT_CUSTODY_CLOSED")
        assert env.journal.close_receipt is snapshot


@pytest.mark.parametrize("kind", ["journal-dto", "unregistered-exact", "journal-and-slot-subclass"],
                         ids=["journal-dto", "unregistered-exact", "journal-and-slot-subclass"])
def test_unregistered_foreign_or_subclass_custody_cannot_authorize(tmp_path, kind):
    with actual_scope(tmp_path) as env:
        if kind == "journal-dto":
            dto = SimpleNamespace(close_receipt=SimpleNamespace(source_closed=True, write_authority=True))
            code_error(lambda: custody._seal(dto), custody.EnrollmentCustodyError, "ENROLLMENT_CUSTODY_INVALID_ARGUMENT")
        elif kind == "unregistered-exact":
            foreign = object.__new__(custody._NativeOwnerJournal)
            for field in custody._NativeOwnerJournal.__slots__:
                if field != "__weakref__":
                    object.__setattr__(foreign, field, object.__getattribute__(env.journal, field))
            code_error(lambda: custody._seal(foreign), custody.EnrollmentCustodyError, "ENROLLMENT_CUSTODY_INVALID_ARGUMENT")
        else:
            for exact in (custody._NativeOwnerJournal, custody._NativeSlot):
                code_error(lambda exact=exact: type("ForeignCustody", (exact,), {}),
                           custody.EnrollmentCustodyError, "ENROLLMENT_CUSTODY_INVALID_ARGUMENT")
        assert env.journal._seal_attempted is False and env.journal.close_receipt is None
        snapshot = finish(env)
        assert snapshot.registered_count == 0 and snapshot.all_registered_native_closed is False


def test_actual_empty_and_unknown_snapshots_never_prove_coverage_or_authority(tmp_path, monkeypatch):
    with actual_scope(tmp_path / "empty") as env:
        empty = finish(env)
        assert empty.facts == () and empty.registered_count == 0 and empty.unknown_count == 0
        assert empty.all_registered_native_closed is False
    with actual_scope(tmp_path / "unknown") as env:
        first, counts = RuntimeError("r872 acquisition missing terminal"), Counter()
        carrier = native.w.LPVOID()
        def acquire(*_args):
            counts["target"] += 1
            raise first
        with monkeypatch.context() as patch:
            patch.setattr(native._advapi, "ConvertStringSidToSidW", acquire)
            same_error(lambda: native._acquire_system_sid(carrier, env.budget), first)
        assert counts["target"] == 1 and carrier.value is None
        unknown = finish(env)
        assert unknown.registered_count == unknown.unknown_count == 1
        assert unknown.facts[0].native_acquired is False and unknown.facts[0].close_attempted is False
        assert unknown.facts[0].native_state == "UNKNOWN" and unknown.all_registered_native_closed is False
    no_authority(empty)
    no_authority(unknown)


def test_controlled_same_budget_foreign_type_rejects_before_private_getter_and_preserves_cleanup(tmp_path, monkeypatch):
    with actual_scope(tmp_path) as env:
        owner = parent_owner(env)
        slot, counts, real_close = slot_for(owner), Counter(), native._kernel.CloseHandle
        actual_type = type(env.budget)
        class ForeignBudget(enrollment._Budget):
            def __getattribute__(self, name):
                if name in {"_owned_ex", "_requested_deadline", "_effective_deadline"}:
                    counts["foreign_private_getter"] += 1
                    pytest.fail("foreign same-object budget private getter reached before exact-type rejection")
                return object.__getattribute__(self, name)
        def create(*_args):
            counts["native_acquisition"] += 1
            pytest.fail("native API reached after budget class swap")
        def close(handle):
            counts["known_owner_cleanup"] += 1
            return legal_bool(real_close(handle))
        primary = None
        try:
            # Actual successful class swap is an explicit fixture prerequisite;
            # unsupported layout is a failure, never a skip or fabricated pass.
            env.budget.__class__ = ForeignBudget
            assert type(env.budget) is ForeignBudget
            with monkeypatch.context() as patch:
                patch.setattr(native._kernel, "CreateFileW", create)
                patch.setattr(native._kernel, "CloseHandle", close)
                code_error(lambda: custody._reserve(env.budget, "parent", None),
                           custody.EnrollmentCustodyError, "ENROLLMENT_CUSTODY_INVALID_ARGUMENT")
                assert counts["foreign_private_getter"] == counts["native_acquisition"] == 0
                assert len(env.journal._slots) == 1
                owner.close()
                owner.close()
            assert counts == Counter(known_owner_cleanup=1)
            assert_closed(slot)
        except BaseException as failure:
            primary = failure
            raise
        finally:
            env.budget.__class__ = actual_type
            fixture_cleanup_owner(owner, primary)
        all_closed(finish(env))


def test_controlled_optin_owner_registry_missing_never_downgrades_to_foreign_closer(tmp_path, monkeypatch):
    with actual_scope(tmp_path) as env:
        owner, independent = parent_owner(env), parent_owner(env)
        slot, independent_slot, counts = slot_for(owner), slot_for(independent), Counter()
        # This exact value came from the original successful CreateFileW return
        # and was adopted before fault injection; it is fixture-only knowledge.
        known_fixture_handle = slot._resource
        independent_handle, real_close = independent_slot._resource, native._kernel.CloseHandle
        row = custody._OWNER_SLOTS.pop(id(owner))
        assert row[0]() is owner and row[1]() is slot
        def foreign_closer(_foreign_value):
            counts["foreign_closer"] += 1
            pytest.fail("registered opt-in owner downgraded to caller closer")
        def close(handle):
            if handle == known_fixture_handle:
                counts["damaged_owner_native"] += 1
                pytest.fail("damaged owner numeric resource closed without registered custody")
            assert handle == independent_handle
            counts["independent_cleanup"] += 1
            return legal_bool(real_close(handle))
        owner.value, owner.closer = 1, foreign_closer
        primary = None
        try:
            with monkeypatch.context() as patch:
                patch.setattr(native._kernel, "CloseHandle", close)
                first = code_error(owner.close, custody.EnrollmentCustodyError,
                                   "ENROLLMENT_CUSTODY_INVALID_ARGUMENT")
                same_error(owner.close, first)
                independent.close()
                independent.close()
            assert counts == Counter(independent_cleanup=1)
            assert owner.retired is True and owner.failure is first
            assert slot._known is True and slot._close_attempted is False
            assert slot.close_receipt.native_state == "UNKNOWN"
            assert_closed(independent_slot)
            snapshot = finish(env, primary=first)
            assert snapshot.unknown_count == 1 and snapshot.all_registered_native_closed is False
            assert snapshot.first_present is True
        except BaseException as failure:
            primary = failure
            raise
        finally:
            # The spy proves no product numeric close happened for this known
            # handle. Only the isolated fixture performs original native close;
            # restoration is not product CLOSED and is never a numeric retry.
            # Both forbidden spies raise before executing any original native
            # call, even on a failing implementation. This precise fixture
            # knowledge permits recovery without guessing an unknown close.
            try:
                legal_bool(real_close(known_fixture_handle))
                counts["fixture_recovery"] += 1
            except BaseException:
                if primary is None:
                    raise
                native._note(primary)
            finally:
                fixture_cleanup_owner(independent, primary)
        assert counts["fixture_recovery"] == 1 and slot.close_receipt.native_state == "UNKNOWN"


def test_controlled_claimed_budget_registry_missing_and_current_empty_rejects_before_native(tmp_path, monkeypatch):
    # Genuinely unclaimed legacy budget/owner remains the original path.
    with actual_scope(tmp_path / "unclaimed", install=False) as unclaimed:
        legacy = parent_owner(unclaimed)
        with native._preserve_primary(legacy):
            assert custody._find_owner_slot(legacy) is None
            legacy.close()
            legacy.close()
        assert legacy.retired is True and legacy.failure is None
    with actual_scope(tmp_path / "claimed") as env:
        independent = parent_owner(env)
        slot, counts = slot_for(independent), Counter()
        row = custody._BUDGET_JOURNALS.pop(id(env.budget))
        assert row[0]() is env.budget and row[1]() is env.journal
        assert custody._GROUP_JOURNALS[id(env.lease)][1]() is env.journal
        saved = custody._CURRENT.set(None)
        def create(*_args):
            counts["native"] += 1
            pytest.fail("claimed missing-budget-row downgraded to default acquisition")
        primary = None
        try:
            with monkeypatch.context() as patch:
                patch.setattr(native._kernel, "CreateFileW", create)
                code_error(lambda: parent_owner(env), custody.EnrollmentCustodyError,
                           "ENROLLMENT_CUSTODY_CURRENT_JOURNAL_UNKNOWN")
            assert counts["native"] == 0 and len(env.journal._slots) == 1
        except BaseException as failure:
            primary = failure
            raise
        finally:
            custody._CURRENT.reset(saved)
            custody._BUDGET_JOURNALS[id(env.budget)] = row
            fixture_cleanup_owner(independent, primary)
        assert custody._BUDGET_JOURNALS[id(env.budget)] is row
        independent.close()
        independent.close()
        assert_closed(slot)
        all_closed(finish(env))


@pytest.mark.parametrize("kind", ["memory", "exit"], ids=["memory", "exit"])
def test_controlled_relative_completion_getter_first_closes_actual_adopted_handle_once(tmp_path, monkeypatch, kind):
    with actual_scope(tmp_path) as env:
        child = env.profile.database.parent / "ch"
        child.mkdir()
        parent = parent_owner(env)
        parent_slot, first, counts, witness = slot_for(parent), first_of(kind), Counter(), {}
        real_info, real_close = native._IoStatus.information, native._kernel.CloseHandle
        def information(status):
            counts["target_getter"] += 1
            slot = env.journal._slots[-1]
            assert slot._role == "relative" and slot._known is True
            assert slot._return_seen is True and slot._representation_confirmed is True
            assert slot._owner is not None and slot.close_receipt.native_acquired is True
            assert real_info.__get__(status, native._IoStatus) == 1
            witness["known_fixture_handle"] = slot._resource
            raise first
        def close(handle):
            if handle == witness.get("known_fixture_handle"):
                counts["target_close_attempt"] += 1
                result = legal_bool(real_close(handle))
                counts["target_actual_successful_close"] += 1
                return result
            assert handle == parent_slot._resource
            counts["independent_close_attempt"] += 1
            return legal_bool(real_close(handle))
        try:
            with monkeypatch.context() as patch:
                patch.setattr(native._IoStatus, "information", property(information))
                patch.setattr(native._kernel, "CloseHandle", close)
                same_error(lambda: native._relative_open(parent.value, child.name,
                    env.budget, directory=True), first)
                assert counts["target_getter"] == 1
                assert counts["target_close_attempt"] == counts["target_actual_successful_close"] == 1
                relative_slot = env.journal._slots[-1]
                assert_closed(relative_slot)
                assert relative_slot._failure is first and env.journal._first is first
                parent.close()
                parent.close()
            assert counts["independent_close_attempt"] == 1
            assert_closed(parent_slot)
            snapshot = finish(env)
            all_closed(snapshot)
            assert snapshot.first_present is True and snapshot.all_registered_native_closed is False
        finally:
            # Failure on the inherited pre-fix window may leave a known debt.
            # Recover only if no original product closer was even attempted.
            if "known_fixture_handle" in witness and not counts["target_close_attempt"]:
                legal_bool(real_close(witness["known_fixture_handle"]))
                counts["fixture_recovery"] += 1
            parent.close()
        assert counts["fixture_recovery"] == 0


@pytest.mark.parametrize("kind", ["memory", "exit"], ids=["memory", "exit"])
def test_controlled_relative_completion_mismatch_preserve_constructor_first_closes_known_owner_once(tmp_path, monkeypatch, kind):
    with actual_scope(tmp_path) as env:
        child = env.profile.database.parent / "ch"
        child.mkdir()
        parent = parent_owner(env)
        parent_slot, first, counts, witness = slot_for(parent), first_of(kind), Counter(), {}
        real_info, real_preserve, real_close = native._IoStatus.information, native._preserve_primary, native._kernel.CloseHandle
        def information(status):
            slot = env.journal._slots[-1]
            assert slot._role == "relative" and slot._known is True
            assert slot._representation_confirmed is True and slot._return_seen is True
            assert type(slot._return) is int and slot._return == 0
            assert native._IO_STATUS_STATUS_NATIVE.__get__(status, native._IoStatus) == 0
            assert real_info.__get__(status, native._IoStatus) == 1
            assert slot._owner is not None
            witness["known_fixture_handle"] = slot._resource
            witness["actual_adopted_slot"] = slot
            counts["completion_mismatch"] += 1
            return 2  # Consumer-local mismatch after actual FILE_OPENED=1.
        def preserve(owner):
            slot = custody._find_owner_slot(owner)
            if slot is not witness.get("actual_adopted_slot"):
                return real_preserve(owner)
            counts["target_constructor"] += 1
            assert counts["completion_mismatch"] == 1
            assert type(owner) is native._Owner and slot._owner is owner
            assert slot._known is True and slot._representation_confirmed is True
            assert slot.close_receipt.native_acquired is True
            raise first
        def close(handle):
            if handle == witness.get("known_fixture_handle"):
                counts["target_close_attempt"] += 1
                result = legal_bool(real_close(handle))
                counts["target_actual_successful_close"] += 1
                return result
            assert handle == parent_slot._resource
            counts["independent_close_attempt"] += 1
            return legal_bool(real_close(handle))
        primary = None
        try:
            with monkeypatch.context() as patch:
                patch.setattr(native._IoStatus, "information", property(information))
                patch.setattr(native, "_preserve_primary", preserve)
                patch.setattr(native._kernel, "CloseHandle", close)
                same_error(lambda: native._relative_open(parent.value, child.name,
                    env.budget, directory=True), first)
                assert counts["completion_mismatch"] == counts["target_constructor"] == 1
                assert counts["target_close_attempt"] == counts["target_actual_successful_close"] == 1
                relative_slot = witness["actual_adopted_slot"]
                assert_closed(relative_slot)
                assert relative_slot._failure is first and env.journal._first is first
                parent.close()
                parent.close()
            assert counts["independent_close_attempt"] == 1
            assert_closed(parent_slot)
            snapshot = finish(env)
            all_closed(snapshot)
            assert snapshot.first_present is True and snapshot.all_registered_native_closed is False
        except BaseException as failure:
            primary = failure
            raise
        finally:
            try:
                # The fixture observed the original legitimate HANDLE. Only a
                # product-native attempt count of zero permits recovery here.
                if "known_fixture_handle" in witness and not counts["target_close_attempt"]:
                    legal_bool(real_close(witness["known_fixture_handle"]))
                    counts["fixture_recovery"] += 1
            except BaseException:
                if primary is None:
                    raise
                native._note(primary)
            finally:
                fixture_cleanup_owner(parent, primary)
        assert counts["fixture_recovery"] == 0
