"""r875 Task2 SOURCE-only independent tests; Root alone may admit/run.

Full canonical modules, original ordinary dormant replica lane and actual
Windows temporary owners are required. Task1 manual-journal helpers below
are only low-level fault fixtures; automatic wiring is tested separately
through actual_prefix_env and original publish/verify owned-ex entrypoints.
Constructed UNKNOWN installation tests simulate return-delivery state at a
consumer-local _install seam using the original ContextVar. They are not a
claim that its C implementation naturally lost a token. Native/fault/context
facts are separate; fixture recovery never upgrades product CLOSED/reset.
"""
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
import os
from pathlib import Path
import tempfile

import pytest

from tests import test_personal_authority_enrollment_custody as task1
from tests.test_personal_authority_owned_ex_group import (
    actual_prefix_env, original_bytes, raw_database_node, tree,
)
from reno import personal_authority_enrollment as enrollment
from reno import personal_authority_enrollment_custody as custody
from reno import personal_authority_enrollment_io as publication
from reno import personal_authority_namespace3_deadline as deadline
from reno import personal_authority_namespace3_io as native
from reno import personal_authority_owned_ex_group as group
from reno import personal_authority_replica_operation as operation
from reno import storage_lifecycle as lifecycle

pytestmark = pytest.mark.skipif(os.name != "nt", reason="r875 actual Windows custody wiring")
ACTIVE_ORIGINAL_GRAPH_NATIVE_WHOLE_ACCEPTANCE = False


@pytest.fixture(autouse=True)
def isolated_wiring_qa(tmp_path):
    qa = Path(os.environ["RENO_QA_ROOT"]).resolve(strict=True)
    tmp_path.resolve(strict=True).relative_to(qa)
    Path(tempfile.gettempdir()).resolve(strict=True).relative_to(qa)
    assert lifecycle._CURRENT.get() is None and custody._CURRENT.get() is None
    yield
    assert lifecycle._CURRENT.get() is None and custody._CURRENT.get() is None


def first_of(kind):
    return {"runtime": RuntimeError, "memory": MemoryError,
            "exit": SystemExit, "interrupt": KeyboardInterrupt}[kind]("r875 original consumer first")


def options(env):
    return {**env["args"], **env["expected"]}


def fixed_error(call, suffix):
    return task1.code_error(call, custody.EnrollmentCustodyError, "ENROLLMENT_CUSTODY_" + suffix)


def native_resource(env, resource):
    if resource == "HANDLE":
        return native._open_parent_raw(env.profile.database.parent, write=False, budget=env.budget)
    assert resource == "LOCALALLOC"
    return native._acquire_system_sid(native.w.LPVOID(), env.budget)


def finish_first(env, primary):
    task1.same_error(lambda: custody._finish_journal(env.journal, primary=primary), primary)
    snapshot = env.journal.close_receipt
    assert type(snapshot) is custody.NativeOwnerJournalSnapshotV1
    assert snapshot.first_present is True and snapshot.all_registered_native_closed is False
    task1.no_authority(snapshot)
    assert custody._CURRENT.get() is None
    return snapshot


def assert_cleanup_helpers_consumed_once(env, monkeypatch, counts, *, real_restore=None):
    """Re-enter actual gates while the caller's original fault patch is live."""
    prior = counts.copy()
    restore = custody._restore_installation if real_restore is None else real_restore
    with monkeypatch.context() as patch:
        # Actual native closers must remain untouched by repeated metadata
        # cleanup. These spies are tests, never product providers or retries.
        patch.setattr(native._kernel, "CloseHandle", task1.forbidden)
        patch.setattr(native._kernel, "LocalFree", task1.forbidden)
        fixed_error(lambda: restore(env.journal), "CLOSED")
        fixed_error(lambda: custody._finish_journal(env.journal), "CLOSED")
    assert counts == prior


@contextmanager
def observe_automatic(monkeypatch):
    journals, events, scopes = [], [], []
    real_new, real_install = custody._new_journal, custody._install
    real_finish, real_consume = custody._finish_journal, group._consume_owned_ex_group
    real_scope_init = publication._OwnedScope.__init__
    def new(actual, **kwargs):
        assert actual._phase == "active"
        assert lifecycle._CURRENT.get() is actual._guard
        value = real_new(actual, **kwargs)
        assert value._group is actual and value._budget._owned_ex is actual
        assert value._operation is actual._binding._operation
        assert value._parent is actual._binding._parent_token
        assert value._effective is actual._binding._effective_token
        assert value._scope is actual._binding._scope_identity
        journals.append(value); events.append((value, "new"))
        return value
    def install(value):
        events.append((value, "install"))
        result = real_install(value)
        assert result is None and custody._CURRENT.get() is value
        return result
    def finish(value, *, primary=None):
        assert value._group._phase == "active" and lifecycle._CURRENT.get() is value._guard
        assert value._slots and all(slot._close_attempted for slot in value._slots)
        assert all(slot._owner is None or slot._owner.retired for slot in value._slots)
        events.append((value, "finish"))
        result = real_finish(value, primary=primary)
        assert value.close_receipt is result and custody._CURRENT.get() is None
        return result
    def consume(actual):
        matched = [value for value in journals if value._group is actual]
        assert len(matched) == 1 and matched[0]._seal_attempted is True
        assert actual._phase != "active" and custody._CURRENT.get() is None
        events.append((matched[0], "consume"))
        return real_consume(actual)
    def scope_init(self, *args, **kwargs):
        real_scope_init(self, *args, **kwargs)
        assert self._custody_journal is custody._CURRENT.get()
        scopes.append((self._custody_journal, self.create, self.creator_live))
    with monkeypatch.context() as patch:
        patch.setattr(custody, "_new_journal", new)
        patch.setattr(custody, "_install", install)
        patch.setattr(custody, "_finish_journal", finish)
        patch.setattr(group, "_consume_owned_ex_group", consume)
        patch.setattr(publication._OwnedScope, "__init__", scope_init)
        yield journals, events, scopes


def test_actual_publish_and_cold_verify_automatically_install_original_journal(actual_prefix_env, monkeypatch):
    env, calls = actual_prefix_env, Counter()
    source_before = raw_database_node(env)
    def callback():
        calls["callback"] += 1
        assert custody._CURRENT.get() is None and lifecycle._CURRENT.get() is None
    with observe_automatic(monkeypatch) as (journals, events, scopes):
        published = enrollment.publish_prefix_owned_ex(env["profile"], **options(env), check_cancelled=callback)
        written = tree(env["root"])
        verified = enrollment.verify_prefix_owned_ex(env["profile"], **options(env),
            expected_binding_sha256=published.binding_sha256,
            expected_prefix_sha256=published.prefix_sha256, check_cancelled=callback)
    assert type(published) is enrollment.AuthorityPrefixFiles and verified == published
    assert calls == Counter(callback=2) and len(journals) == 2
    assert journals[0] is not journals[1] and journals[0]._scope is not journals[1]._scope
    assert journals[0]._group is not journals[1]._group and journals[0]._budget is not journals[1]._budget
    for journal in journals:
        assert [event for selected, event in events if selected is journal] == ["new", "install", "finish", "consume"]
        snapshot = journal.close_receipt
        task1.all_closed(snapshot)
        assert snapshot.install_state == "installed" and snapshot.reset_state == "reset"
        assert snapshot.first_present is False and snapshot.all_registered_native_closed is True
        assert journal._group.close_receipt.source_closed is False
        assert journal._group.close_receipt.write_authority is False
    assert {slot._role for slot in journals[0]._slots} == {name for name, _kind in custody._ROLES}
    assert all(value in journals for value, _create, _live in scopes)
    assert any(create for _value, create, _live in scopes)
    assert any(live for _value, _create, live in scopes)
    assert any(not create and not live for _value, create, live in scopes)
    assert original_bytes(env) == env["originals"] and raw_database_node(env) == source_before
    assert tree(env["root"]) == written


def test_actual_default_owned_and_borrowed_locked_calls_do_not_mint_journal(actual_prefix_env, monkeypatch):
    env, calls = actual_prefix_env, Counter()
    def mint(*_args, **_kwargs):
        calls["mint"] += 1
        pytest.fail("original default entry minted opt-in journal")
    with monkeypatch.context() as patch:
        patch.setattr(custody, "_new_journal", mint)
        published = enrollment.publish_prefix(env["profile"], **options(env))
        hashes = dict(expected_binding_sha256=published.binding_sha256, expected_prefix_sha256=published.prefix_sha256)
        verified = enrollment.verify_prefix(env["profile"], **options(env), **hashes)
        with lifecycle.exclusive(env["profile"], timeout=0):
            borrowed = enrollment.verify_prefix_locked(env["profile"], **options(env), **hashes,
                deadline_token=deadline.Namespace3Deadline(deadline._now() + 30.0))
    assert verified == borrowed == published and calls["mint"] == 0
    assert custody._CURRENT.get() is None and original_bytes(env) == env["originals"]


def test_fresh_complete_catalog_contains_new_helpers_aliases_and_original_native_abi():
    task1.complete_catalog()
    assert enrollment.custody is publication.custody is native._custody is custody
    assert custody._CONTEXT_VARIABLE is custody._CURRENT and type(custody._CURRENT) is ContextVar
    extras = group._catalog_extra_original()
    for module in (enrollment, publication):
        assert any(value is module and "custody" in names for value, names in extras)
    assert any(value is custody and "_CONTEXT_VARIABLE" in names for value, names in extras)
    functions = operation._CALLEE_PINS[2]
    for name in ("_active_journal_for_budget", "_remember_owner_primary", "_restore_installation", "_finish_journal"):
        assert any(row[0] is getattr(custody, name) for row in functions)
    assert ACTIVE_ORIGINAL_GRAPH_NATIVE_WHOLE_ACCEPTANCE is False


# Explicit stable IDs below are declarations, not collection or pass results.
F001_CASES = [('HANDLE', 'runtime', 'ordinary'), ('HANDLE', 'runtime', 'control'), ('HANDLE', 'runtime', 'illegal'), ('HANDLE', 'memory', 'ordinary'), ('HANDLE', 'memory', 'control'), ('HANDLE', 'memory', 'illegal'), ('HANDLE', 'exit', 'ordinary'), ('HANDLE', 'exit', 'control'), ('HANDLE', 'exit', 'illegal'), ('HANDLE', 'interrupt', 'ordinary'), ('HANDLE', 'interrupt', 'control'), ('HANDLE', 'interrupt', 'illegal'), ('LOCALALLOC', 'runtime', 'ordinary'), ('LOCALALLOC', 'runtime', 'control'), ('LOCALALLOC', 'runtime', 'illegal'), ('LOCALALLOC', 'memory', 'ordinary'), ('LOCALALLOC', 'memory', 'control'), ('LOCALALLOC', 'memory', 'illegal'), ('LOCALALLOC', 'exit', 'ordinary'), ('LOCALALLOC', 'exit', 'control'), ('LOCALALLOC', 'exit', 'illegal'), ('LOCALALLOC', 'interrupt', 'ordinary'), ('LOCALALLOC', 'interrupt', 'control'), ('LOCALALLOC', 'interrupt', 'illegal')]
F001_IDS = ['HANDLE-runtime-ordinary', 'HANDLE-runtime-control', 'HANDLE-runtime-illegal', 'HANDLE-memory-ordinary', 'HANDLE-memory-control', 'HANDLE-memory-illegal', 'HANDLE-exit-ordinary', 'HANDLE-exit-control', 'HANDLE-exit-illegal', 'HANDLE-interrupt-ordinary', 'HANDLE-interrupt-control', 'HANDLE-interrupt-illegal', 'LOCALALLOC-runtime-ordinary', 'LOCALALLOC-runtime-control', 'LOCALALLOC-runtime-illegal', 'LOCALALLOC-memory-ordinary', 'LOCALALLOC-memory-control', 'LOCALALLOC-memory-illegal', 'LOCALALLOC-exit-ordinary', 'LOCALALLOC-exit-control', 'LOCALALLOC-exit-illegal', 'LOCALALLOC-interrupt-ordinary', 'LOCALALLOC-interrupt-control', 'LOCALALLOC-interrupt-illegal']
PROPAGATION_CASES = [('HANDLE', 'memory', 'legal'), ('HANDLE', 'memory', 'exception'), ('HANDLE', 'memory', 'illegal'), ('HANDLE', 'exit', 'legal'), ('HANDLE', 'exit', 'exception'), ('HANDLE', 'exit', 'illegal'), ('LOCALALLOC', 'memory', 'legal'), ('LOCALALLOC', 'memory', 'exception'), ('LOCALALLOC', 'memory', 'illegal'), ('LOCALALLOC', 'exit', 'legal'), ('LOCALALLOC', 'exit', 'exception'), ('LOCALALLOC', 'exit', 'illegal')]
PROPAGATION_IDS = ['HANDLE-memory-legal', 'HANDLE-memory-exception', 'HANDLE-memory-illegal', 'HANDLE-exit-legal', 'HANDLE-exit-exception', 'HANDLE-exit-illegal', 'LOCALALLOC-memory-legal', 'LOCALALLOC-memory-exception', 'LOCALALLOC-memory-illegal', 'LOCALALLOC-exit-legal', 'LOCALALLOC-exit-exception', 'LOCALALLOC-exit-illegal']


@pytest.mark.parametrize("resource,kind,cleanup", F001_CASES, ids=F001_IDS)
def test_actual_registered_internal_postclock_primary_precedes_cleanup_secondary(tmp_path, monkeypatch, resource, kind, cleanup):
    with task1.actual_scope(tmp_path) as env:
        prior = task1.parent_owner(env)
        prior_slot, first, secondary = task1.slot_for(prior), first_of(kind), RuntimeError("r875 cleanup secondary")
        if cleanup == "control":
            secondary = SystemExit("r875 cleanup control")
        counts, events, target = Counter(), [], {}
        baseline = len(env.journal._slots)
        role = "parent" if resource == "HANDLE" else "system_sid"
        real_clock, real_failed = enrollment._Budget.clock_only, custody._failed
        real_native = native._kernel.CloseHandle if resource == "HANDLE" else native._kernel.LocalFree
        def clock(value):
            if (value is env.budget and len(env.journal._slots) > baseline
                    and env.journal._slots[-1]._role == role
                    and env.journal._slots[-1]._owner is not None and not counts["postclock"]):
                slot = env.journal._slots[-1]
                target.update(slot=slot, resource=slot._resource)
                counts["postclock"] += 1; events.append("postclock-P")
                raise first
            return real_clock(value)
        def failed(slot, failure):
            if slot is target.get("slot") and failure is first:
                events.append("record-P")
            return real_failed(slot, failure)
        def closer(value):
            if value != target.get("resource"):
                return real_native(value)
            counts["native_attempt"] += 1; events.append("native-close")
            assert target["slot"]._failure is first and env.journal._first is first
            actual = real_native(value)
            counts["fixture_witness_actual_native_success"] += 1
            if resource == "HANDLE":
                task1.legal_bool(actual)
            else:
                assert actual is None
            if cleanup == "illegal":
                return True if resource == "HANDLE" else 0
            raise secondary
        def acquire():
            owner = native_resource(env, resource)
            counts["returned_owner"] += 1
            return owner
        with monkeypatch.context() as patch:
            patch.setattr(enrollment._Budget, "clock_only", clock)
            patch.setattr(custody, "_failed", failed)
            patch.setattr(native._kernel, "CloseHandle" if resource == "HANDLE" else "LocalFree", closer)
            task1.same_error(lambda: enrollment._call(env.budget, acquire), first)
            native._cleanup_pending(target["slot"], first)
        assert counts == Counter(postclock=1, native_attempt=1, fixture_witness_actual_native_success=1)
        assert events.index("record-P") < events.index("native-close")
        slot = target["slot"]
        assert slot._failure is env.journal._first is env.budget.callback_primary is first
        assert slot.close_receipt.native_state == "UNKNOWN" and slot.close_receipt.close_attempted is True
        prior.close(); task1.assert_closed(prior_slot)
        snapshot = finish_first(env, first)
        assert snapshot.unknown_count == 1


@pytest.mark.parametrize("resource,propagation,cleanup", PROPAGATION_CASES, ids=PROPAGATION_IDS)
def test_registered_postclock_propagation_fault_keeps_original_once_cleanup(tmp_path, monkeypatch, resource, propagation, cleanup):
    with task1.actual_scope(tmp_path) as env:
        prior, first = task1.parent_owner(env), first_of("runtime")
        prior_slot = task1.slot_for(prior)
        prop_error = first_of(propagation)
        secondary = RuntimeError("r875 cleanup after propagation fault")
        counts, events, target = Counter(), [], {}
        baseline = len(env.journal._slots)
        role = "parent" if resource == "HANDLE" else "system_sid"
        real_clock, real_failed = enrollment._Budget.clock_only, custody._failed
        real_native = native._kernel.CloseHandle if resource == "HANDLE" else native._kernel.LocalFree
        def clock(value):
            if (value is env.budget and len(env.journal._slots) > baseline
                    and env.journal._slots[-1]._role == role
                    and env.journal._slots[-1]._owner is not None and not counts["postclock"]):
                slot = env.journal._slots[-1]
                target.update(slot=slot, resource=slot._resource)
                counts["postclock"] += 1
                raise first
            return real_clock(value)
        def failed(slot, failure):
            if slot is target.get("slot") and failure is first and not counts["propagation_fault"]:
                counts["propagation_fault"] += 1; events.append("propagation-fault")
                raise prop_error
            return real_failed(slot, failure)
        def closer(value):
            if value != target.get("resource"):
                return real_native(value)
            counts["native_attempt"] += 1; events.append("native-close")
            assert target["slot"]._failure is None and env.journal._first is None
            actual = real_native(value)
            counts["fixture_witness_actual_native_success"] += 1
            if resource == "HANDLE":
                task1.legal_bool(actual)
            else:
                assert actual is None
            if cleanup == "legal":
                return actual
            if cleanup == "illegal":
                return True if resource == "HANDLE" else 0
            raise secondary
        with monkeypatch.context() as patch:
            patch.setattr(enrollment._Budget, "clock_only", clock)
            patch.setattr(custody, "_failed", failed)
            patch.setattr(native._kernel, "CloseHandle" if resource == "HANDLE" else "LocalFree", closer)
            task1.same_error(lambda: enrollment._call(env.budget, native_resource, env, resource), first)
            native._cleanup_pending(target["slot"], first)
        assert counts == Counter(postclock=1, propagation_fault=1, native_attempt=1, fixture_witness_actual_native_success=1)
        assert events == ["propagation-fault", "native-close"]
        assert env.budget.callback_primary is first
        slot = target["slot"]
        assert slot.close_receipt.native_state == ("CLOSED" if cleanup == "legal" else "UNKNOWN")
        if cleanup == "legal":
            assert slot._failure is env.journal._first is first  # Late observation, not preclose proof.
        else:
            assert slot._failure is env.journal._first and slot._failure is not first
        prior.close(); task1.assert_closed(prior_slot)
        task1.same_error(lambda: custody._finish_journal(env.journal, primary=first), env.journal._first)
        snapshot = env.journal.close_receipt
        task1.no_authority(snapshot)
        assert snapshot.first_present is True and snapshot.all_registered_native_closed is False
        assert snapshot.unknown_count == int(cleanup != "legal")


@pytest.mark.parametrize("kind", ["runtime", "memory", "exit", "interrupt"], ids=["runtime", "memory", "exit", "interrupt"])
def test_direct_optin_publication_scope_body_first_survives_cleanup_control(tmp_path, monkeypatch, kind):
    with task1.actual_scope(tmp_path) as env:
        first, secondary, counts, target = first_of(kind), SystemExit("r875 direct scope cleanup"), Counter(), {}
        real = native._kernel.CloseHandle
        def close(value):
            if value != target.get("resource"):
                return real(value)
            counts["native_attempt"] += 1
            assert target["slot"]._failure is env.journal._first is first
            task1.legal_bool(real(value))
            counts["fixture_witness_actual_native_success"] += 1
            raise secondary
        def run():
            with publication._OwnedScope(env.budget, root=env.profile.database.parent) as owner:
                target.update(resource=owner.value, slot=task1.slot_for(owner))
                assert custody._active_journal_for_budget(env.budget) is env.journal
                raise first
        with monkeypatch.context() as patch:
            patch.setattr(native._kernel, "CloseHandle", close)
            task1.same_error(run, first)
        assert counts == Counter(native_attempt=1, fixture_witness_actual_native_success=1)
        assert target["slot"].close_receipt.native_state == "UNKNOWN"
        finish_first(env, first)


@pytest.mark.parametrize("stage", ["before", "after"], ids=["install-before-call", "install-after-real-return"])
def test_actual_entry_install_fault_still_finishes_and_exits_actual_ex(actual_prefix_env, monkeypatch, stage):
    env, first, counts, seen = actual_prefix_env, first_of("memory"), Counter(), []
    real_new, real_install, real_entry = custody._new_journal, custody._install, enrollment._entry
    def new(*args, **kwargs):
        value = real_new(*args, **kwargs); seen.append(value); return value
    def install(value):
        counts["install_expression"] += 1
        if stage == "after":
            assert real_install(value) is None
            counts["actual_install"] += 1
        raise first
    def entry(*args, **kwargs):
        counts["entry"] += 1
        return real_entry(*args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(custody, "_new_journal", new)
        patch.setattr(custody, "_install", install)
        patch.setattr(enrollment, "_entry", entry)
        task1.same_error(lambda: enrollment.publish_prefix_owned_ex(env["profile"], **options(env)), first)
    assert len(seen) == 1 and counts == Counter(install_expression=1, actual_install=int(stage == "after"))
    journal = seen[0]; snapshot = journal.close_receipt
    assert journal._first is first and journal._reset_attempted is (stage == "after")
    assert snapshot.install_state == ("installed" if stage == "after" else "not_started")
    assert snapshot.reset_state == ("reset" if stage == "after" else "not_started")
    assert snapshot.first_present is True and snapshot.registered_count == 0
    assert journal._group._exit_attempted is True and journal._group._consume_attempted is False
    task1.no_authority(snapshot)


@pytest.mark.parametrize("foreign", [False, True], ids=["constructed-token-delivery-loss", "constructed-loss-foreign-current"])
def test_constructed_unknown_install_uses_original_context_and_limited_recovery(actual_prefix_env, monkeypatch, foreign):
    env, first, counts, seen, tokens = actual_prefix_env, first_of("exit"), Counter(), [], []
    real_context, real_new = custody._CURRENT, custody._new_journal
    foreign_value = object()
    def new(*args, **kwargs):
        value = real_new(*args, **kwargs); seen.append(value); return value
    def constructed_install(value):
        # Deliberate consumer-local state construction; original _install body
        # and ContextVar C return-loss are NOT claimed to have been executed.
        custody._journal(value, live=True)
        assert real_context.get() is None and value._install_attempted is False
        value._install_attempted = True; value._install_state = "UNKNOWN"
        counts["constructed_install"] += 1
        tokens.append(real_context.set(value)); counts["fixture_actual_context_set"] += 1
        assert value._context_token is None
        if foreign:
            tokens.append(real_context.set(foreign_value))
            counts["fixture_foreign_context_set"] += 1
        custody._remember(value, first)
        raise first
    try:
        with monkeypatch.context() as patch:
            patch.setattr(custody, "_new_journal", new)
            patch.setattr(custody, "_install", constructed_install)
            task1.same_error(lambda: enrollment.publish_prefix_owned_ex(env["profile"], **options(env)), first)
        assert custody._CURRENT is real_context and len(seen) == 1
        journal = seen[0]; snapshot = journal.close_receipt
        assert journal._first is first and journal._reset_attempted is True
        assert snapshot.install_state == snapshot.reset_state == "UNKNOWN"
        assert snapshot.first_present is True and snapshot.all_registered_native_closed is False
        assert real_context.get() is (foreign_value if foreign else None)
        assert journal._group._exit_attempted is True and journal._group._consume_attempted is False
        task1.no_authority(snapshot)
        fixed_error(lambda: custody._restore_installation(journal), "CLOSED")
        assert real_context.get() is (foreign_value if foreign else None)
        if not foreign:
            fresh = deadline._RestrictedBudget(deadline.Namespace3Deadline(deadline._now() + 30.0))
            assert custody._active_journal_for_budget(fresh) is None
            with lifecycle.exclusive(env["profile"], timeout=0):
                with publication._OwnedScope(fresh, root=env["profile"].database.parent) as afterdefault:
                    assert custody._find_owner_slot(afterdefault) is None
            assert afterdefault.retired is True and journal.close_receipt is snapshot
            counts["actual_unclaimed_afterdefault"] += 1
    finally:
        # Fixture tokens are not handed to the product journal. Reset them only
        # in this labelled fixture recovery, never count it as product reset.
        while tokens:
            real_context.reset(tokens.pop()); counts["fixture_context_recovery"] += 1
    assert counts["constructed_install"] == counts["fixture_actual_context_set"] == 1
    assert counts["fixture_context_recovery"] == 1 + int(foreign)
    assert counts["actual_unclaimed_afterdefault"] == int(not foreign)


@pytest.mark.parametrize("stage", ["before", "after"], ids=["reset-before-call", "reset-after-real-return"])
def test_reset_expression_fault_keeps_unknown_and_independent_cached_native_facts(tmp_path, monkeypatch, stage):
    with task1.actual_scope(tmp_path) as env:
        owner = task1.parent_owner(env); slot = task1.slot_for(owner)
        owner.close(); task1.assert_closed(slot)
        first, counts, real = first_of("interrupt"), Counter(), custody._reset
        original_token, original_context = env.journal._context_token, custody._CURRENT
        def reset(value):
            counts["reset_expression"] += 1
            if stage == "after":
                assert real(value) is None
                counts["actual_reset"] += 1
            raise first
        try:
            with monkeypatch.context() as patch:
                patch.setattr(custody, "_reset", reset)
                task1.same_error(lambda: custody._finish_journal(env.journal), first)
                assert_cleanup_helpers_consumed_once(env, monkeypatch, counts)
            snapshot = env.journal.close_receipt
            assert counts == Counter(reset_expression=1, actual_reset=int(stage == "after"))
            assert snapshot.reset_state == "UNKNOWN" and snapshot.first_present is True
            assert snapshot.facts == (slot.close_receipt,) and slot.close_receipt.native_state == "CLOSED"
            assert snapshot.all_registered_native_closed is False
            assert env.journal._reset_attempted is True and env.journal._seal_attempted is True
            task1.no_authority(snapshot)
        finally:
            if stage == "before":
                assert original_context.get() is env.journal
                original_context.reset(original_token); counts["fixture_context_recovery"] += 1
        assert counts["fixture_context_recovery"] == int(stage == "before")


@pytest.mark.parametrize("stage", ["seal-before-cache", "seal-after-cache"], ids=["seal-before-cache", "seal-after-cache"])
def test_seal_expression_fault_never_retries_or_replaces_primary(tmp_path, monkeypatch, stage):
    with task1.actual_scope(tmp_path) as env:
        owner = task1.parent_owner(env); slot = task1.slot_for(owner); owner.close()
        primary, secondary, counts, real = first_of("runtime"), first_of("memory"), Counter(), custody._seal
        def seal(value, *, primary=None):
            counts["seal_expression"] += 1
            if stage == "seal-after-cache":
                real(value, primary=primary); counts["actual_seal"] += 1
            raise secondary
        with monkeypatch.context() as patch:
            patch.setattr(custody, "_seal", seal)
            task1.same_error(lambda: custody._finish_journal(env.journal, primary=primary), primary)
            assert_cleanup_helpers_consumed_once(env, monkeypatch, counts)
        assert counts == Counter(seal_expression=1, actual_seal=int(stage == "seal-after-cache"))
        assert env.journal._first is primary and env.journal._reset_attempted is True
        assert env.journal._seal_attempted is True and custody._CURRENT.get() is None
        task1.assert_closed(slot)
        if stage == "seal-before-cache":
            assert env.journal.close_receipt is None
        else:
            snapshot = env.journal.close_receipt
            assert snapshot.first_present is True and snapshot.facts == (slot.close_receipt,)
            assert snapshot.all_registered_native_closed is False
            task1.no_authority(snapshot)


def test_body_first_reset_and_seal_secondary_are_independent_once_attempts(tmp_path, monkeypatch):
    with task1.actual_scope(tmp_path) as env:
        owner = task1.parent_owner(env); slot = task1.slot_for(owner); owner.close()
        primary, reset_failure, seal_failure = first_of("exit"), first_of("memory"), first_of("interrupt")
        counts, token, context = Counter(), env.journal._context_token, custody._CURRENT
        def reset(value):
            counts["reset"] += 1
            raise reset_failure
        def seal(value, *, primary=None):
            counts["seal"] += 1
            raise seal_failure
        try:
            with monkeypatch.context() as patch:
                patch.setattr(custody, "_reset", reset)
                patch.setattr(custody, "_seal", seal)
                task1.same_error(lambda: custody._finish_journal(env.journal, primary=primary), primary)
                assert_cleanup_helpers_consumed_once(env, monkeypatch, counts)
            assert counts == Counter(reset=1, seal=1)
            assert env.journal._first is primary and env.journal._reset_attempted is True
            assert env.journal._seal_attempted is True and env.journal.close_receipt is None
            task1.assert_closed(slot)
        finally:
            if context.get() is env.journal:
                context.reset(token); counts["fixture_context_recovery"] += 1
        assert counts["fixture_context_recovery"] == 1


def test_finish_primary_remember_fault_cannot_skip_reset_or_seal(tmp_path, monkeypatch):
    with task1.actual_scope(tmp_path) as env:
        owner = task1.parent_owner(env); slot = task1.slot_for(owner); owner.close()
        primary, secondary, counts, real = first_of("exit"), first_of("memory"), Counter(), custody._remember
        def remember(journal, failure):
            if journal is env.journal and failure is primary and not counts["target"]:
                counts["target"] += 1
                raise secondary
            return real(journal, failure)
        with monkeypatch.context() as patch:
            patch.setattr(custody, "_remember", remember)
            task1.same_error(lambda: custody._finish_journal(env.journal, primary=primary), primary)
        assert counts == Counter(target=1)
        snapshot = env.journal.close_receipt
        assert env.journal._reset_attempted is env.journal._seal_attempted is True
        assert snapshot.reset_state == "reset" and custody._CURRENT.get() is None
        assert env.journal._first is primary and snapshot.first_present is True
        task1.assert_closed(slot); task1.no_authority(snapshot)


@pytest.mark.parametrize("stage", ["before", "after"], ids=["restore-before-real-call", "restore-after-real-return"])
def test_restore_call_layer_fault_keeps_unknown_without_native_downgrade(tmp_path, monkeypatch, stage):
    with task1.actual_scope(tmp_path) as env:
        owner = task1.parent_owner(env); slot = task1.slot_for(owner); owner.close()
        first, counts, real = first_of("memory"), Counter(), custody._restore_installation
        original_token, original_context = env.journal._context_token, custody._CURRENT
        def restore(journal):
            counts["restore_expression"] += 1
            if stage == "after":
                assert real(journal) is None
                counts["actual_restore"] += 1
            raise first
        try:
            with monkeypatch.context() as patch:
                patch.setattr(custody, "_restore_installation", restore)
                task1.same_error(lambda: custody._finish_journal(env.journal), first)
                assert_cleanup_helpers_consumed_once(env, monkeypatch, counts, real_restore=real)
            assert counts == Counter(restore_expression=1, actual_restore=int(stage == "after"))
            snapshot = env.journal.close_receipt
            assert snapshot.reset_state == "UNKNOWN" and snapshot.first_present is True
            assert env.journal._reset_attempted is env.journal._seal_attempted is True
            assert snapshot.all_registered_native_closed is False
            task1.assert_closed(slot); task1.no_authority(snapshot)
        finally:
            if stage == "before":
                assert original_context.get() is env.journal
                original_context.reset(original_token); counts["fixture_context_recovery"] += 1
        assert counts["fixture_context_recovery"] == int(stage == "before")


@pytest.mark.parametrize("stage", ["preclose", "postclose"], ids=["preclose-clock", "postclose-clock"])
def test_publication_retirement_clock_first_preserves_original_native_close_order(tmp_path, monkeypatch, stage):
    with task1.actual_scope(tmp_path) as env:
        first, counts, target, events = first_of("runtime"), Counter(), {}, []
        real_clock, real_close = enrollment._Budget.clock_only, native._kernel.CloseHandle
        def clock(budget):
            if budget is env.budget and target.get("eligible"):
                events.append("clock")
                should_fail = stage == "preclose" or target["slot"]._close_attempted
                if should_fail and not counts["clock_fault"]:
                    counts["clock_fault"] += 1
                    raise first
            return real_clock(budget)
        def close(value):
            if value == target.get("resource"):
                counts["native_attempt"] += 1; events.append("close")
                if stage == "preclose":
                    assert target["slot"]._failure is env.journal._first is first
            return real_close(value)
        def run():
            with publication._OwnedScope(env.budget, root=env.profile.database.parent) as owner:
                target.update(eligible=True, resource=owner.value, slot=task1.slot_for(owner))
        with monkeypatch.context() as patch:
            patch.setattr(enrollment._Budget, "clock_only", clock)
            patch.setattr(native._kernel, "CloseHandle", close)
            task1.same_error(run, first)
        assert counts == Counter(clock_fault=1, native_attempt=1)
        assert events == ["clock", "close", "clock"]
        task1.assert_closed(target["slot"])
        snapshot = finish_first(env, first)
        assert snapshot.unknown_count == 0  # Clock first is not native UNKNOWN.


def test_successful_finish_is_cached_once_and_claimed_budget_cannot_fall_back(tmp_path, monkeypatch):
    with task1.actual_scope(tmp_path) as env:
        owner = task1.parent_owner(env); slot = task1.slot_for(owner); owner.close()
        snapshot = custody._finish_journal(env.journal)
        assert env.journal.close_receipt is snapshot and snapshot.facts == (slot.close_receipt,)
        task1.all_closed(snapshot)
        fixed_error(lambda: custody._finish_journal(env.journal), "CLOSED")
        fixed_error(lambda: custody._restore_installation(env.journal), "CLOSED")
        calls = Counter()
        def create(*_args):
            calls["native"] += 1; pytest.fail("claimed budget fell back to default native")
        with monkeypatch.context() as patch:
            patch.setattr(native._kernel, "CreateFileW", create)
            fixed_error(lambda: task1.parent_owner(env), "CURRENT_JOURNAL_UNKNOWN")
        assert calls["native"] == 0 and env.journal.close_receipt is snapshot
        fresh = deadline._RestrictedBudget(deadline.Namespace3Deadline(deadline._now() + 30.0))
        assert custody._active_journal_for_budget(fresh) is None
        with publication._OwnedScope(fresh, root=env.profile.database.parent) as default_owner:
            assert custody._find_owner_slot(default_owner) is None
        assert default_owner.retired is True and env.journal.close_receipt is snapshot


@pytest.mark.parametrize("damage", ["missing-current", "foreign-current", "missing-budget-row", "missing-direct-group-row"],
                         ids=["missing-current", "foreign-current", "missing-budget-row", "missing-direct-group-row"])
def test_claimed_lookup_rejects_damaged_current_or_registry_before_native(tmp_path, monkeypatch, damage):
    with task1.actual_scope(tmp_path) as env:
        context, token, saved = custody._CURRENT, None, None
        calls = Counter()
        def create(*_args):
            calls["native"] += 1; pytest.fail("damaged claim reached native")
        if damage in ("missing-current", "foreign-current"):
            token = context.set(None if damage == "missing-current" else object())
        else:
            mapping = custody._BUDGET_JOURNALS if damage == "missing-budget-row" else custody._GROUP_JOURNALS
            saved = mapping.pop(id(env.budget))
        try:
            with monkeypatch.context() as patch:
                patch.setattr(native._kernel, "CreateFileW", create)
                fixed_error(lambda: custody._active_journal_for_budget(env.budget), "CURRENT_JOURNAL_UNKNOWN")
                fixed_error(lambda: publication._OwnedScope(env.budget, root=env.profile.database.parent), "CURRENT_JOURNAL_UNKNOWN")
            assert calls["native"] == 0
        finally:
            if token is not None:
                context.reset(token)
            if saved is not None:
                mapping[id(env.budget)] = saved


@pytest.mark.parametrize("field", ["_operation", "_parent", "_effective", "_scope", "_guard", "_owner"],
                         ids=["operation", "requested-token", "effective-token", "scope", "guard", "execution-owner"])
def test_active_journal_rechecks_original_identity_before_native(tmp_path, field):
    with task1.actual_scope(tmp_path) as env:
        original = getattr(env.journal, field)
        replacement = tuple(list(original)) if field == "_operation" else object()
        if field in ("_parent", "_effective"):
            replacement = deadline.Namespace3Deadline(original.absolute_monotonic)
        assert replacement is not original
        setattr(env.journal, field, replacement)
        try:
            fixed_error(lambda: custody._active_journal_for_budget(env.budget), "INVALID_ARGUMENT")
        finally:
            setattr(env.journal, field, original)


def test_active_lookup_rejects_foreign_budget_class_before_private_getters(tmp_path):
    with task1.actual_scope(tmp_path) as env:
        calls = Counter()
        class ForeignBudget(enrollment._Budget):
            def __getattribute__(self, name):
                if name in {"_owned_ex", "_requested_deadline", "_effective_deadline"}:
                    calls["foreign_getter"] += 1
                    raise AssertionError("foreign budget getter consulted")
                return super().__getattribute__(name)
        original = type(env.budget)
        env.budget.__class__ = ForeignBudget
        try:
            assert type(env.budget) is ForeignBudget
            fixed_error(lambda: custody._active_journal_for_budget(env.budget), "INVALID_ARGUMENT")
            assert calls["foreign_getter"] == 0
        finally:
            env.budget.__class__ = original


def test_publication_scope_rechecks_captured_journal_before_acquisition(tmp_path, monkeypatch):
    with task1.actual_scope(tmp_path, install=False) as env:
        scope = publication._OwnedScope(env.budget, root=env.profile.database.parent)
        assert scope._custody_journal is None
        task1.activate(env)  # Low-level manual fixture; not automatic entry acceptance.
        calls = Counter()
        def create(*_args):
            calls["native"] += 1; pytest.fail("Scope changed lineage reached native")
        with monkeypatch.context() as patch:
            patch.setattr(native._kernel, "CreateFileW", create)
            fixed_error(scope.__enter__, "INVALID_ARGUMENT")
        assert calls["native"] == 0 and scope.owner is None


@pytest.mark.parametrize("damage", ["same-budget-class", "replacement-budget"],
                         ids=["same-budget-class", "replacement-budget"])
def test_publication_scope_budget_identity_gate_precedes_foreign_clock_and_getters(tmp_path, monkeypatch, damage):
    with task1.actual_scope(tmp_path) as env:
        scope = publication._OwnedScope(env.budget, root=env.profile.database.parent)
        assert scope._custody_journal is env.journal
        calls = Counter()
        class ForeignBudget(enrollment._Budget):
            def __getattribute__(self, name):
                if name in {"_owned_ex", "_requested_deadline", "_effective_deadline", "clock_only"}:
                    calls["foreign_access"] += 1
                    raise AssertionError("foreign budget used before identity gate")
                return super().__getattribute__(name)
        class ReplacementBudget:
            def __getattribute__(self, name):
                calls["foreign_access"] += 1
                raise AssertionError("replacement budget provider consulted")
        original_class = type(env.budget)
        if damage == "same-budget-class":
            env.budget.__class__ = ForeignBudget
            assert type(env.budget) is ForeignBudget
        else:
            scope.budget = ReplacementBudget()
        def create(*_args):
            calls["native"] += 1
            pytest.fail("foreign budget reached namespace acquisition")
        try:
            with monkeypatch.context() as patch:
                patch.setattr(native._kernel, "CreateFileW", create)
                fixed_error(scope.__enter__, "INVALID_ARGUMENT")
            assert not calls and scope.owner is None
        finally:
            # Restore only the fixture's actual object/class before its own
            # independent EX cleanup; this is not product identity recovery.
            env.budget.__class__ = original_class
            scope.budget = env.budget


@pytest.mark.parametrize("damage", ["new-exact-context", "foreign-provider"], ids=["new-exact-context", "foreign-provider"])
def test_fixed_context_anchor_rejects_replacement_before_provider_access(tmp_path, monkeypatch, damage):
    with task1.actual_scope(tmp_path) as env:
        calls = Counter()
        class Provider:
            def get(self):
                calls["get"] += 1; pytest.fail("foreign context provider reached")
            def set(self, *_args):
                calls["set"] += 1; pytest.fail("foreign context provider reached")
            def reset(self, *_args):
                calls["reset"] += 1; pytest.fail("foreign context provider reached")
        replacement = ContextVar("r875 illegal replacement", default=env.journal) if damage == "new-exact-context" else Provider()
        prior = (env.journal._install_attempted, env.journal._reset_attempted,
                 env.journal._seal_attempted, tuple(env.journal._slots))
        with monkeypatch.context() as patch:
            patch.setattr(custody, "_CURRENT", replacement)
            fixed_error(lambda: custody._active_journal_for_budget(env.budget), "INVALID_ARGUMENT")
            fixed_error(lambda: custody._restore_installation(env.journal), "INVALID_ARGUMENT")
            fixed_error(lambda: custody._install(env.journal), "INVALID_ARGUMENT")
            fixed_error(lambda: custody._reset(env.journal), "INVALID_ARGUMENT")
            fixed_error(lambda: custody._reserve(env.budget, "parent", None), "INVALID_ARGUMENT")
            assert (env.journal._install_attempted, env.journal._reset_attempted,
                    env.journal._seal_attempted, tuple(env.journal._slots)) == prior
        assert not calls and custody._CURRENT.get() is env.journal


def test_cached_native_journal_and_slots_do_not_read_live_or_reclosed_resources(tmp_path, monkeypatch):
    with task1.actual_scope(tmp_path) as env:
        owner = task1.parent_owner(env); slot = task1.slot_for(owner); owner.close()
        snapshot = custody._finish_journal(env.journal); fact = slot.close_receipt
        real_journal_get, real_slot_get = object.__getattribute__, object.__getattribute__
        def journal_get(self, name):
            if name in {"_group", "_budget", "_guard", "_owner", "_slots", "_pins"}:
                task1.forbidden()
            return real_journal_get(self, name)
        def slot_get(self, name):
            if name in {"_resource", "_carrier", "_owner", "_journal"}:
                task1.forbidden()
            return real_slot_get(self, name)
        with monkeypatch.context() as patch:
            patch.setattr(custody._NativeOwnerJournal, "__getattribute__", journal_get)
            patch.setattr(custody._NativeSlot, "__getattribute__", slot_get)
            patch.setattr(enrollment._Budget, "clock_only", task1.forbidden)
            patch.setattr(native._kernel, "CloseHandle", task1.forbidden)
            patch.setattr(native._kernel, "LocalFree", task1.forbidden)
            for _ in range(2):
                assert env.journal.close_receipt is snapshot and slot.close_receipt is fact
        task1.no_authority(snapshot)
