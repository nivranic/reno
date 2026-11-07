"""r877 Task3 SOURCE-only tests; Root alone may admit/collect/run.

The original complete actual_prefix_env fixture and full product modules are
required. Faults are consumer-local call-layer projections, not OS failure or
active original graph evidence. No completion flag or receipt is fabricated.
"""
from collections import Counter
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, replace
import errno
import gc
import io
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import weakref

import pytest

from tests.test_personal_authority_owned_ex_group import (
    actual_prefix_env, original_bytes, raw_database_node, tree,
)
from reno import personal_authority_directory_deadline as directories
from reno import personal_authority_enrollment as enrollment
from reno import personal_authority_enrollment_custody as custody
from reno import personal_authority_enrollment_physical as physical
from reno import personal_authority_namespace3 as namespace
from reno import personal_authority_namespace3_deadline as deadline
from reno import personal_authority_owned_ex_deadline as owned
from reno import personal_authority_owned_ex_group as groups
from reno import personal_authority_source_deadline as nodes
from reno import storage_lifecycle as lifecycle
from reno import sync_media as safe

pytestmark = pytest.mark.skipif(os.name != "nt", reason="r877 actual Windows physical enrollment")
ACTIVE_ORIGINAL_GRAPH_NATIVE_WHOLE_ACCEPTANCE = False
NODE = "node-identity.json"
ROLES = ("node_parent", "node_budget", "node_stream", "node_readonly", "node_held")


@pytest.fixture(autouse=True)
def isolated_physical_qa(tmp_path):
    qa = Path(os.environ["RENO_QA_ROOT"]).resolve(strict=True)
    tmp_path.resolve(strict=True).relative_to(qa)
    Path(tempfile.gettempdir()).resolve(strict=True).relative_to(qa)
    assert lifecycle._CURRENT.get() is None and custody._CURRENT.get() is None
    yield
    assert lifecycle._CURRENT.get() is None and custody._CURRENT.get() is None


def options(env):
    return {**env["args"], **env["expected"]}


def no_action(*_args, **_kwargs):
    pytest.fail("refused or retired action reached a provider or repeated exit")


def first_of(kind):
    return {"runtime": RuntimeError, "memory": MemoryError,
            "exit": SystemExit, "interrupt": KeyboardInterrupt}[kind]("r877 original first")


def same_error(call, first):
    with pytest.raises(type(first)) as raised:
        call()
    assert raised.value is first
    return raised.value


def source_error(call, suffix):
    with pytest.raises(physical.EnrollmentSourceError) as raised:
        call()
    assert raised.value.code == "ENROLLMENT_SOURCE_" + suffix
    return raised.value


def rejected_constructor(call, *, suffix="INVALID_ARGUMENT"):
    with pytest.raises((physical.EnrollmentSourceError,
                        directories.DirectoryDeadlineError, nodes.SourceDeadlineError)) as raised:
        call()
    assert raised.value.code.endswith(suffix)


def no_authority(receipt):
    assert receipt.source_closed is receipt.write_authority is False
    assert receipt.acquisition_coverage_proven is False
    assert receipt.outer_ex_borrowed is True and receipt.outer_ex_closed is False
    assert receipt.registered_enrollment_sources_terminal_complete is True
    assert not hasattr(receipt, "__dict__")
    with pytest.raises(FrozenInstanceError):
        receipt.source_closed = True


def initializer_call(carrier, role):
    owner, parent = carrier._owners[role], carrier._owners["node_parent"]
    binding = carrier._binding
    return {
        "node_parent": lambda: nodes._initialize_node_directory(owner, binding.facade, binding.budget),
        "node_budget": lambda: nodes._initialize_node_file_budget(owner, binding.budget),
        "node_stream": lambda: nodes._initialize_node_stream(owner, parent),
        "node_readonly": lambda: directories._initialize_readonly_lease(owner, binding.facade, NODE),
        "node_held": lambda: nodes._initialize_held_file(owner, parent, binding=binding),
    }[role]


def assert_fd_closed(fd):
    # Only actually returned normal numeric-close facts use this observation.
    with pytest.raises(OSError) as raised:
        os.fstat(fd)
    assert raised.value.errno == errno.EBADF


@contextmanager
def observe_automatic(monkeypatch):
    """No strong carrier retention before issuance; product owns its lifetime."""
    observed = SimpleNamespace(refs=[], issued=[], journals=[], events=[], counts=Counter())
    real_new = physical._new_source_carrier
    real_finish = custody._finish_journal
    real_issue = physical._issue_borrowed_source_precondition
    real_consume = groups._consume_owned_ex_group

    def new(*args, **kwargs):
        value = real_new(*args, **kwargs)
        observed.refs.append(weakref.ref(value))
        observed.events.append((id(value), "new"))
        return value

    def finish(journal, *, primary=None):
        observed.counts[(id(journal), "finish")] += 1
        assert journal._group._phase == "active"
        assert lifecycle._CURRENT.get() is journal._guard
        assert custody._CURRENT.get() is journal
        result = real_finish(journal, primary=primary)
        observed.journals.append(journal)
        observed.events.append((id(journal), "finish"))
        assert custody._CURRENT.get() is None and journal.close_receipt is result
        return result

    def issue(consumer, *, native_snapshot):
        carrier = consumer._carrier_ref()
        assert type(carrier) is physical._EnrollmentSourceCarrier
        assert custody._CURRENT.get() is None
        assert lifecycle._CURRENT.get() is carrier._guard and carrier._guard._active is True
        assert carrier._group._phase == "active" and carrier._group._consume_attempted is False
        assert consumer._attempted is False
        result = real_issue(consumer, native_snapshot=native_snapshot)
        observed.events.append((id(carrier._journal), "issue"))
        observed.issued.append((carrier, consumer, result))
        assert consumer._attempted is True and consumer._receipt is carrier._receipt is result
        assert result.native_snapshot is native_snapshot
        return result

    def consume(group):
        selected = [j for j in observed.journals if j._group is group]
        assert len(selected) == 1 and group._phase == "retired"
        assert custody._CURRENT.get() is None and lifecycle._CURRENT.get() is None
        observed.events.append((id(selected[0]), "consume"))
        return real_consume(group)

    with monkeypatch.context() as patch:
        patch.setattr(physical, "_new_source_carrier", new)
        patch.setattr(custody, "_finish_journal", finish)
        patch.setattr(physical, "_issue_borrowed_source_precondition", issue)
        patch.setattr(groups, "_consume_owned_ex_group", consume)
        yield observed


def assert_normal_issuance(carrier, consumer, receipt):
    assert type(receipt) is physical.BorrowedEnrollmentSourcePreconditionV1
    no_authority(receipt)
    assert carrier._terminal_attempted is carrier._result_verified is True
    assert carrier._entry_returned is carrier._outer_owner_verified is True
    assert carrier._first is None
    assert len(receipt.capture_receipts) == 2
    assert len({id(child) for child in carrier._captures}) == 2
    for ordinal, (child, fact) in enumerate(zip(carrier._captures, receipt.capture_receipts)):
        assert child._group is carrier._group and child._ordinal == ordinal
        assert carrier._capture_returns[ordinal] is child and child._receipt is fact
        assert child._phase == "retired" and child._completed is child._finish_attempted is True
        assert child._first is None and fact.context_completed is fact.normal_exit is True
        assert fact.failed is False and fact.source_closed is False
        assert fact.outer_ex_borrowed is True and fact.owned_outer_ex_closed is False
    assert carrier._node_receipt is receipt.node_receipt
    assert carrier._directory_receipts is receipt.physical_directory_receipts
    assert len(receipt.physical_directory_receipts) == 4
    assert groups._source_node_terminal(receipt.node_receipt)
    for ordinal, (directory, journal, fact) in enumerate(zip(carrier._directories,
            carrier._directory_journals, receipt.physical_directory_receipts)):
        assert type(directory) is safe._Directory and journal._owner is directory
        assert journal._root_ordinal == ordinal and fact.root_ordinal == ordinal
        assert fact.normal_exit is True and fact.failed is False
        assert fact.handle_states and all(state == "closed" for state in fact.handle_states)
    assert consumer._attempted is True and consumer._receipt is receipt


def test_actual_publish_cold_verify_join_two_sources_after_reset_before_ex_exit(actual_prefix_env, monkeypatch):
    env = actual_prefix_env
    source_before = raw_database_node(env)
    with observe_automatic(monkeypatch) as seen:
        published = enrollment.publish_prefix_owned_ex(env["profile"], **options(env))
        written = tree(env["root"])
        verified = enrollment.verify_prefix_owned_ex(env["profile"], **options(env),
            expected_binding_sha256=published.binding_sha256,
            expected_prefix_sha256=published.prefix_sha256)
    assert verified == published and len(seen.refs) == len(seen.issued) == 2
    for carrier, consumer, receipt in seen.issued:
        assert_normal_issuance(carrier, consumer, receipt)
        journal = carrier._journal
        assert seen.counts[(id(journal), "finish")] == 1
        phases = [phase for identity, phase in seen.events if identity == id(journal)]
        assert phases == ["finish", "issue", "consume"]
        assert carrier._group._consume_attempted is True and carrier._guard._active is False
        source_error(lambda: physical._issue_borrowed_source_precondition(consumer,
            native_snapshot=receipt.native_snapshot), "CLOSED")
    assert tree(env["root"]) == written and original_bytes(env) == env["originals"]
    assert raw_database_node(env) == source_before


def test_actual_private_node_read_same_fd_eof_charges_original_shared_budget(actual_prefix_env, monkeypatch):
    env, reads, closes = actual_prefix_env, [], []
    real_read, real_close = nodes.DeadlineNodeStream.read, directories._close_fd

    def read(stream, amount):
        pin = getattr(stream, "_enrollment_mode", None)
        if pin is None:
            return real_read(stream, amount)
        budget, raw, fd = pin.binding.budget, stream._raw, stream._expected_fd
        assert type(raw) is io.FileIO and raw.closefd is False
        assert stream._node.budget is budget and pin.binding.directory is pin.binding.carrier_ref()._directories[0]
        before = budget.control_bytes
        returned = real_read(stream, amount)
        assert stream.fileno() == fd
        assert budget.control_bytes - before == len(returned)
        reads.append((id(budget), id(raw), fd, before, budget.control_bytes, returned))
        return returned

    def close(fd):
        returned = real_close(fd)
        closes.append(fd)
        return returned

    with observe_automatic(monkeypatch) as seen:
        monkeypatch.setattr(nodes.DeadlineNodeStream, "read", read)
        monkeypatch.setattr(directories, "_close_fd", close)
        enrollment.publish_prefix_owned_ex(env["profile"], **options(env))
    assert len(seen.issued) == 1 and reads
    carrier = seen.issued[0][0]
    assert len({item[0] for item in reads}) == len({item[1] for item in reads}) == len({item[2] for item in reads}) == 1
    assert reads[0][0] == id(carrier._budget)
    payload = env["data"].joinpath(NODE).read_bytes()
    total = sum(len(row[5]) for row in reads)
    assert total >= 6 * len(payload) and total % len(payload) == 0
    assert sum(row[5] == b"" for row in reads) == total // len(payload)
    assert all(later[3] >= earlier[4] for earlier, later in zip(reads, reads[1:]))
    fd = reads[0][2]
    assert closes.count(fd) == 1
    assert_fd_closed(fd)


@pytest.mark.parametrize("role", ["node_parent", "node_budget", "node_stream", "node_readonly", "node_held"],
                         ids=["parent", "budget", "stream", "readonly", "held"])
def test_actual_factory_exact_owner_reinit_rejected_before_fields_reset(actual_prefix_env, monkeypatch, role):
    real_open = physical._open_physical
    escaped = []

    def opened(profile, stack, budget):
        result = real_open(profile, stack, budget)
        carrier = physical._lookup_installed_carrier(budget)
        owner, pin = carrier._owners[role], carrier._owner_pins[role]
        assert physical._require_factory_owner_live(owner, role) is pin
        assert owner._enrollment_mode is pin and pin.binding is carrier._binding
        before = tuple(object.__getattribute__(owner, name) for name in type(owner).__slots__
                       if name != "__weakref__" and hasattr(owner, name))
        arguments = {"node_parent": (carrier._binding.facade, budget), "node_budget": (budget,),
            "node_stream": (carrier._owners["node_parent"],),
            "node_readonly": (carrier._binding.facade, NODE),
            "node_held": (carrier._owners["node_parent"],)}[role]
        suffix = "INVALID_ARGUMENT" if role in ("node_parent", "node_readonly") else "CLOSED"
        rejected_constructor(lambda: type(owner).__init__(owner, *arguments), suffix=suffix)
        after = tuple(object.__getattribute__(owner, name) for name in type(owner).__slots__
                      if name != "__weakref__" and hasattr(owner, name))
        assert len(before) == len(after) and all(left is right for left, right in zip(before, after))
        escaped.append(owner)
        return result

    monkeypatch.setattr(physical, "_open_physical", opened)
    enrollment.publish_prefix_owned_ex(actual_prefix_env["profile"], **options(actual_prefix_env))
    assert len(escaped) == 1
    source_error(lambda: physical._reject_factory_reinit(escaped[0]), "CLOSED")


@pytest.mark.parametrize("fault", ["none", "missing", "foreign-pin"], ids=["clear-mode", "delete-mode", "foreign-mode"])
def test_claimed_factory_owner_mode_damage_never_falls_back_to_default(actual_prefix_env, monkeypatch, fault):
    real_open = physical._open_physical

    def opened(profile, stack, budget):
        result = real_open(profile, stack, budget)
        carrier = physical._lookup_installed_carrier(budget)
        for role in ROLES:
            owner = carrier._owners[role]
            original = owner._enrollment_mode
            if fault == "missing":
                del owner._enrollment_mode
            else:
                owner._enrollment_mode = None if fault == "none" else object()
            try:
                source_error(lambda: physical._require_factory_owner(owner, role), "INVALID_ARGUMENT")
                source_error(lambda: physical._reject_factory_reinit(owner), "CLOSED")
                source_error(initializer_call(carrier, role), "INVALID_ARGUMENT")
            finally:
                owner._enrollment_mode = original
            assert physical._require_factory_owner(owner, role) is original
        return result

    monkeypatch.setattr(physical, "_open_physical", opened)
    enrollment.publish_prefix_owned_ex(actual_prefix_env["profile"], **options(actual_prefix_env))


def test_shared_initializers_cannot_reset_registered_active_fd_first_or_file_count(actual_prefix_env, monkeypatch):
    real_open = physical._open_physical

    def opened(profile, stack, budget):
        result = real_open(profile, stack, budget)
        carrier = physical._lookup_installed_carrier(budget)
        lease, file_budget = carrier._owners["node_readonly"], carrier._owners["node_budget"]
        original_ledger, original_fd = lease._fd, tuple(lease._fd)
        count = file_budget.count
        assert count == 1 and lease._phase == "active"
        for role in ROLES:
            source_error(initializer_call(carrier, role), "CLOSED")
            assert lease._fd is original_ledger and tuple(lease._fd) == original_fd
            assert file_budget.count == count
        assert nodes.DeadlineNodeStream.fileno(carrier._owners["node_stream"]) == original_fd[0]
        return result

    monkeypatch.setattr(physical, "_open_physical", opened)
    enrollment.publish_prefix_owned_ex(actual_prefix_env["profile"], **options(actual_prefix_env))


def test_actual_parent_field_corruption_refuses_before_foreign_budget_clock(actual_prefix_env, monkeypatch):
    real_open = physical._open_physical

    class ForeignParent:
        @property
        def budget(self):
            no_action()

        def _live(self):
            no_action()

    def opened(profile, stack, budget):
        result = real_open(profile, stack, budget)
        carrier = physical._lookup_installed_carrier(budget)
        for role in ("node_stream", "node_held"):
            owner = carrier._owners[role]
            parent = owner._node
            owner._node = ForeignParent()
            try:
                source_error(owner._live, "INVALID_ARGUMENT")
            finally:
                owner._node = parent
            assert physical._require_factory_owner_live(owner, role).parent is parent
        return result

    monkeypatch.setattr(physical, "_open_physical", opened)
    enrollment.publish_prefix_owned_ex(actual_prefix_env["profile"], **options(actual_prefix_env))


def test_claimed_missing_carrier_row_refuses_default_fallback_before_new_physical_work(actual_prefix_env, monkeypatch):
    real_open = physical._open_physical

    def opened(profile, stack, budget):
        result = real_open(profile, stack, budget)
        carrier = physical._lookup_installed_carrier(budget)
        row = physical._BUDGET_CARRIERS.pop(id(budget))
        try:
            source_error(lambda: physical._lookup_installed_carrier(budget), "INVALID_ARGUMENT")
        finally:
            physical._BUDGET_CARRIERS[id(budget)] = row
        assert physical._lookup_installed_carrier(budget) is carrier
        return result

    monkeypatch.setattr(physical, "_open_physical", opened)
    enrollment.publish_prefix_owned_ex(actual_prefix_env["profile"], **options(actual_prefix_env))


def test_exit_expression_fault_after_real_lower_close_keeps_closed_fact_without_retry(actual_prefix_env, monkeypatch):
    first, selected, closes, calls = RuntimeError("r877 after lower exit"), [], [], Counter()
    real_exit, real_close = nodes._exit_enrollment_node_stream, directories._close_fd

    def exit(stream, kind, primary, tb, *, claim):
        calls["exit-expression"] += 1
        result = real_exit(stream, kind, primary, tb, claim=claim)
        selected.append(stream)
        calls["real-exit-return"] += 1
        assert result is None and stream.close_receipt.normal_exit is True
        raise first

    def close(fd):
        result = real_close(fd)
        closes.append(fd)
        return result

    monkeypatch.setattr(nodes, "_exit_enrollment_node_stream", exit)
    monkeypatch.setattr(directories, "_close_fd", close)
    same_error(lambda: enrollment.publish_prefix_owned_ex(actual_prefix_env["profile"],
        **options(actual_prefix_env)), first)
    assert calls == {"exit-expression": 1, "real-exit-return": 1} and len(selected) == 1
    stream = selected[0]
    assert stream.close_receipt.readonly_close_receipt.fd_state == "closed"
    assert closes.count(stream._expected_fd) == 1
    assert_fd_closed(stream._expected_fd)
    with monkeypatch.context() as patch:
        patch.setattr(directories, "_close_fd", no_action)
        source_error(lambda: physical._reserve_owner_exit(stream), "CLOSED")


def test_ordinary_constructor_does_not_implicitly_adopt_private_node_parent(actual_prefix_env, monkeypatch):
    real_open = physical._open_physical

    def opened(profile, stack, budget):
        result = real_open(profile, stack, budget)
        carrier = physical._lookup_installed_carrier(budget)
        parent = carrier._owners["node_parent"]
        for constructor in (nodes.DeadlineNodeStream, nodes.DeadlineHeldFile):
            rejected_constructor(lambda: constructor(parent))
        rejected_constructor(lambda: nodes.DeadlineNodeDirectory(carrier._directories[0], budget))
        rejected_constructor(lambda: directories.DeadlineReadonlyLease(carrier._binding.facade, NODE))
        assert parent.budget is budget and parent.path is carrier._binding.data_path
        return result

    monkeypatch.setattr(physical, "_open_physical", opened)
    enrollment.publish_prefix_owned_ex(actual_prefix_env["profile"], **options(actual_prefix_env))


@pytest.mark.parametrize("layer", ["physical", "stream", "readonly"], ids=["physical", "stream", "readonly"])
@pytest.mark.parametrize("position", ["before", "after"], ids=["before-real", "after-real-return"])
def test_partial_enter_uses_original_lower_debt_once_without_delivery_assumption(actual_prefix_env, monkeypatch, layer, position):
    first, calls, selected, fds = RuntimeError("r877 partial enter"), Counter(), [], []
    cls = {"physical": safe._Directory, "stream": nodes.DeadlineNodeStream,
           "readonly": directories.DeadlineReadonlyLease}[layer]
    real_enter, real_exit = cls.__enter__, cls.__exit__
    real_close = directories._close_fd
    carriers = []
    real_new = physical._new_source_carrier

    def new(*args, **kwargs):
        value = real_new(*args, **kwargs)
        carriers.append(value)
        return value

    def target(owner):
        if not carriers:
            return False
        carrier = carriers[0]
        return any(owner is item for item in carrier._directories) if layer == "physical" else getattr(owner, "_enrollment_mode", None) is not None

    def enter(owner):
        if not target(owner) or selected:
            return real_enter(owner)
        selected.append(owner)
        calls["enter-expression"] += 1
        if position == "after":
            result = real_enter(owner)
            calls["enter-returned"] += 1
            if layer == "readonly":
                fd = result.raw.fileno()
                fds.append(fd)
        raise first

    def exit(owner, kind, primary, tb):
        if selected and owner is selected[0]:
            calls["public-exit"] += 1
        return real_exit(owner, kind, primary, tb)

    def close(fd):
        result = real_close(fd)
        calls[("fd-closed", fd)] += 1
        return result

    monkeypatch.setattr(physical, "_new_source_carrier", new)
    monkeypatch.setattr(cls, "__enter__", enter)
    monkeypatch.setattr(cls, "__exit__", exit)
    monkeypatch.setattr(directories, "_close_fd", close)
    same_error(lambda: enrollment.publish_prefix_owned_ex(actual_prefix_env["profile"],
        **options(actual_prefix_env)), first)
    assert len(selected) == 1 and calls["enter-expression"] == 1
    assert calls["enter-returned"] == (position == "after")
    owner, carrier = selected[0], carriers[0]
    assert carrier._receipt is None and lifecycle._CURRENT.get() is custody._CURRENT.get() is None
    if layer == "physical":
        journal = carrier._directory_journals[0]
        assert calls["public-exit"] == (position == "after")
        if position == "after":
            assert journal._slot._close_consumed is True
    else:
        pin = carrier._owner_pins["node_" + layer]
        assert pin.enter_owed is False
        assert pin.exit_expression_attempted is (position == "after")
        assert pin.exit_body_started is (position == "after")
        if position == "after":
            with monkeypatch.context() as patch:
                patch.setattr(directories, "_close_fd", no_action)
                source_error(lambda: physical._reserve_owner_exit(owner), "CLOSED")
    for fd in fds:
        assert calls[("fd-closed", fd)] == 1
        assert_fd_closed(fd)


@pytest.mark.parametrize("kind", ["runtime", "memory", "exit", "interrupt"], ids=["runtime", "memory", "exit", "interrupt"])
def test_original_body_first_survives_metadata_cleanup_fault_and_once_known_fd_close(actual_prefix_env, monkeypatch, kind):
    first, secondary, helds, closes = first_of(kind), RuntimeError("r877 cleanup metadata"), [], []
    real_open, real_hook, real_close = physical._open_physical, physical._begin_owner_cleanup, directories._close_fd

    def opened(profile, stack, budget):
        result = real_open(profile, stack, budget)
        helds.append(result[1])
        raise first

    def cleanup(owner):
        original = real_hook(owner)
        return secondary if original is None else original

    def close(fd):
        returned = real_close(fd)
        closes.append(fd)
        return returned

    monkeypatch.setattr(physical, "_open_physical", opened)
    monkeypatch.setattr(physical, "_begin_owner_cleanup", cleanup)
    monkeypatch.setattr(directories, "_close_fd", close)
    same_error(lambda: enrollment.publish_prefix_owned_ex(actual_prefix_env["profile"],
        **options(actual_prefix_env)), first)
    assert len(helds) == 1
    held = helds[0]
    fd = held._stream._expected_fd
    assert type(fd) is int and closes.count(fd) == 1
    assert_fd_closed(fd)
    receipt = held.close_receipt
    assert receipt.failed is True and receipt.normal_exit is False
    assert receipt.stream_close_receipt.readonly_close_receipt.fd_state == "closed"
    assert held._first is first
    with monkeypatch.context() as patch:
        patch.setattr(directories, "_close_fd", no_action)
        source_error(lambda: physical._reserve_owner_exit(held), "CLOSED")


@pytest.mark.parametrize("fault", ["snapshot-copy", "missing-producer", "capture-not-completed", "foreign-source", "current-journal", "result-unverified"],
                         ids=["copied-snapshot", "missing-producer", "incomplete-capture", "foreign-source", "current-not-reset", "result-unverified"])
def test_postreset_join_failure_consumes_attempt_and_cannot_finish_or_issue_twice(actual_prefix_env, monkeypatch, fault):
    real_issue, real_finish = physical._issue_borrowed_source_precondition, custody._finish_journal
    observed, counts = [], Counter()

    def finish(journal, *, primary=None):
        counts["finish"] += 1
        return real_finish(journal, primary=primary)

    def issue(consumer, *, native_snapshot):
        carrier = consumer._carrier_ref()
        assert custody._CURRENT.get() is None and carrier._group._phase == "active"
        assert carrier._guard._active is True
        token = None
        if fault == "snapshot-copy":
            native_snapshot = replace(native_snapshot)
        elif fault == "missing-producer":
            carrier._capture_returns[1] = None
        elif fault == "capture-not-completed":
            carrier._captures[1]._completed = False
        elif fault == "foreign-source":
            carrier._captures[1]._sources[0] = carrier._captures[0]._sources[0]
        elif fault == "current-journal":
            token = custody._CURRENT.set(carrier._journal)
        else:
            carrier._result_verified = False
        try:
            with pytest.raises((physical.EnrollmentSourceError, owned.OwnedExDeadlineError)) as raised:
                real_issue(consumer, native_snapshot=native_snapshot)
            first = raised.value
            assert consumer._attempted is True and consumer._receipt is carrier._receipt is None
            source_error(lambda: real_issue(consumer, native_snapshot=native_snapshot), "CLOSED")
            observed.append((carrier, consumer, first))
            raise first
        finally:
            if token is not None:
                custody._CURRENT.reset(token)

    monkeypatch.setattr(custody, "_finish_journal", finish)
    monkeypatch.setattr(physical, "_issue_borrowed_source_precondition", issue)
    with pytest.raises((physical.EnrollmentSourceError, owned.OwnedExDeadlineError)) as raised:
        enrollment.publish_prefix_owned_ex(actual_prefix_env["profile"], **options(actual_prefix_env))
    assert len(observed) == 1 and raised.value is observed[0][2]
    assert counts["finish"] == 1
    assert observed[0][0]._journal.close_receipt.all_registered_native_closed is True
    assert lifecycle._CURRENT.get() is custody._CURRENT.get() is None


@pytest.mark.parametrize("kind", ["runtime", "memory", "exit", "interrupt"], ids=["runtime", "memory", "exit", "interrupt"])
def test_issuer_first_getter_control_failure_is_consumed_after_exact_consumer_recognition(actual_prefix_env, monkeypatch, kind):
    first, observed, counts = first_of(kind), [], Counter()
    real_issue, real_finish = physical._issue_borrowed_source_precondition, custody._finish_journal
    descriptor = custody._JOURNAL_RECEIPT

    class FailingReceiptDescriptor:
        def __get__(self, owner, selected_type):
            counts["getter"] += 1
            raise first

    def finish(journal, *, primary=None):
        counts["finish"] += 1
        return real_finish(journal, primary=primary)

    def issue(consumer, *, native_snapshot):
        carrier = consumer._carrier_ref()
        assert native_snapshot is descriptor.__get__(carrier._journal, custody._NativeOwnerJournal)
        with monkeypatch.context() as patch:
            patch.setattr(custody, "_JOURNAL_RECEIPT", FailingReceiptDescriptor())
            same_error(lambda: real_issue(consumer, native_snapshot=native_snapshot), first)
            assert consumer._attempted is True and consumer._receipt is carrier._receipt is None
            source_error(lambda: real_issue(consumer, native_snapshot=native_snapshot), "CLOSED")
        observed.append((carrier, consumer))
        raise first

    monkeypatch.setattr(custody, "_finish_journal", finish)
    monkeypatch.setattr(physical, "_issue_borrowed_source_precondition", issue)
    same_error(lambda: enrollment.publish_prefix_owned_ex(actual_prefix_env["profile"],
        **options(actual_prefix_env)), first)
    assert len(observed) == 1 and counts == {"finish": 1, "getter": 1}
    assert observed[0][0]._journal.close_receipt.all_registered_native_closed is True


@pytest.mark.parametrize("gate", ["entry", "owner"], ids=["entry-delivery", "outer-owner"])
def test_later_gate_failure_preserves_result_verified_without_successful_issuance(actual_prefix_env, monkeypatch, gate):
    first, observed, counts = RuntimeError("r877 later result gate"), [], Counter()
    name = "_require_entry_return" if gate == "entry" else "_record_outer_owner_success"
    real_gate, real_finish = getattr(physical, name), custody._finish_journal

    def failing(carrier, *args, **kwargs):
        assert carrier._result_verified is True and type(carrier._result) is enrollment.AuthorityPrefixFiles
        if gate == "entry":
            real_gate(carrier, *args, **kwargs)
        observed.append(carrier)
        raise first

    def finish(journal, *, primary=None):
        counts["finish"] += 1
        return real_finish(journal, primary=primary)

    monkeypatch.setattr(physical, name, failing)
    monkeypatch.setattr(custody, "_finish_journal", finish)
    same_error(lambda: enrollment.publish_prefix_owned_ex(actual_prefix_env["profile"],
        **options(actual_prefix_env)), first)
    assert len(observed) == 1 and counts["finish"] == 1
    carrier = observed[0]
    assert carrier._result_verified is True and carrier._entry_returned is True
    assert carrier._outer_owner_verified is False and carrier._receipt is None


def test_retired_terminal_cached_facts_do_not_reread_lower_and_fresh_dto_not_stored_identity(actual_prefix_env, monkeypatch):
    with observe_automatic(monkeypatch) as seen:
        enrollment.publish_prefix_owned_ex(actual_prefix_env["profile"], **options(actual_prefix_env))
    carrier, consumer, receipt = seen.issued[0]
    held, stream, lease = (carrier._owners[role] for role in ("node_held", "node_stream", "node_readonly"))
    held_getter, stream_getter = nodes.DeadlineHeldFile.close_receipt, nodes.DeadlineNodeStream.close_receipt
    lease_getter = directories.DeadlineReadonlyLease.close_receipt
    before = (held_getter.__get__(held, type(held)), stream_getter.__get__(stream, type(stream)),
              lease_getter.__get__(lease, type(lease)))
    assert before[0] == receipt.node_receipt and before[0] is not receipt.node_receipt
    assert before[0].stream_close_receipt is held._stream_close_receipt
    assert before[1].readonly_close_receipt is stream._readonly_close_receipt
    with monkeypatch.context() as patch:
        patch.setattr(nodes, "_clock", no_action)
        patch.setattr(directories, "_close_fd", no_action)
        patch.setattr(nodes.DeadlineNodeStream, "close_receipt", property(no_action))
        patch.setattr(directories.DeadlineReadonlyLease, "close_receipt", property(no_action))
        assert held_getter.__get__(held, type(held)) == before[0]
        assert consumer._receipt is carrier._receipt is receipt
        source_error(lambda: physical._issue_borrowed_source_precondition(consumer,
            native_snapshot=receipt.native_snapshot), "CLOSED")


def test_escaped_retired_claim_survives_carrier_gc_and_cannot_reinitialize(actual_prefix_env, monkeypatch):
    with observe_automatic(monkeypatch) as seen:
        enrollment.publish_prefix_owned_ex(actual_prefix_env["profile"], **options(actual_prefix_env))
    carrier, consumer, receipt = seen.issued.pop()
    held = carrier._owners["node_held"]
    reference = weakref.ref(carrier)
    assert held.close_receipt.normal_exit is True
    del carrier, consumer, receipt
    gc.collect()
    assert reference() is None
    source_error(lambda: physical._reject_factory_reinit(held), "CLOSED")
    with monkeypatch.context() as patch:
        patch.setattr(directories, "_close_fd", no_action)
        rejected_constructor(lambda: nodes.DeadlineHeldFile.__init__(held, held._node),
                             suffix="CLOSED")


def test_unclaimed_original_locked_path_keeps_default_physical_source_route(actual_prefix_env, monkeypatch):
    calls = Counter()
    original = namespace._physical

    def old(*args, **kwargs):
        calls["default"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(namespace, "_physical", old)
    monkeypatch.setattr(physical, "_new_source_carrier", no_action)
    env = actual_prefix_env
    with lifecycle.exclusive(env["profile"]):
        result = enrollment.publish_prefix_locked(env["profile"], **options(env),
            deadline_token=enrollment.deadline.Namespace3Deadline(enrollment.time.monotonic() + 30.0))
    assert type(result) is enrollment.AuthorityPrefixFiles and calls["default"] == 1
    assert lifecycle._CURRENT.get() is custody._CURRENT.get() is None


def test_private_readonly_name_damage_refuses_live_gate_before_clock_and_keeps_owned_cleanup(actual_prefix_env, monkeypatch):
    real_open, real_close = physical._open_physical, directories._close_fd
    selected, closes = [], []
    # Use an actual distinct existing source, rather than a non-existent name.
    other = actual_prefix_env["data"] / enrollment._ORIGINAL_NAMES[0]
    assert other.exists() and other.name != NODE

    def opened(profile, stack, budget):
        result = real_open(profile, stack, budget)
        carrier = physical._lookup_installed_carrier(budget)
        lease = carrier._owners["node_readonly"]
        fd = lease._fd[0]
        assert type(fd) is int and result[1]._stream._expected_fd == fd
        lease._name = other.name
        # Structural live refusal must precede another provider or budget clock.
        with monkeypatch.context() as patch:
            patch.setattr(directories, "_create_file", no_action)
            patch.setattr(physical._BorrowedNodeFacade, "_live", no_action)
            patch.setattr(budget, "clock_only", no_action)
            first = source_error(lambda: physical._require_factory_owner_live(lease,
                "node_readonly"), "INVALID_ARGUMENT")
        selected.append((carrier, lease, fd, first))
        # Keep the damaged name through actual cleanup; restore does not prove
        # that an independently confirmed ledger can survive this failure.
        raise first

    def close(fd):
        returned = real_close(fd)
        closes.append(fd)
        return returned

    monkeypatch.setattr(physical, "_open_physical", opened)
    monkeypatch.setattr(directories, "_close_fd", close)
    with pytest.raises(physical.EnrollmentSourceError) as raised:
        enrollment.publish_prefix_owned_ex(actual_prefix_env["profile"], **options(actual_prefix_env))
    assert len(selected) == 1 and raised.value is selected[0][3]
    carrier, lease, fd, first = selected[0]
    assert closes.count(fd) == 1
    assert_fd_closed(fd)
    assert lease.close_receipt.fd_state == "closed"
    assert carrier._receipt is None and carrier._node_receipt.normal_exit is False
    assert carrier._owners["node_held"]._first is first


@pytest.mark.parametrize("stage", ["physical", "node"], ids=["directory-traceback", "node-traceback"])
def test_failed_original_traceback_operation_collects_only_explicit_carrier_factpin_owner_refs(actual_prefix_env, monkeypatch, stage):
    """A real failed owner/cache cycle must not be held by a global registry row.

    Only this fixture's explicit weakrefs and registry keys are inspected.
    There is no frame/locals traversal or gc.get_objects/profiling.
    """
    def one_operation():
        first = RuntimeError("r877 failed source with original traceback")
        refs, keys = {}, {"budgets": [], "owners": []}
        real_new, real_open = physical._new_source_carrier, physical._open_physical
        real_enter = safe._Directory.__enter__

        def new(*args, **kwargs):
            carrier = real_new(*args, **kwargs)
            refs["carrier"] = weakref.ref(carrier)
            refs["factpin"] = weakref.ref(carrier._fact_pin)
            keys["budgets"].append(id(carrier._budget))
            return carrier

        def remember_owners(carrier):
            for ordinal, owner in enumerate(carrier._directories):
                if owner is not None:
                    refs["directory-" + str(ordinal)] = weakref.ref(owner)
            for role, owner in carrier._owners.items():
                refs[role] = weakref.ref(owner)
                keys["owners"].append(id(owner))

        def enter(owner):
            returned = real_enter(owner)
            carrier = refs.get("carrier", lambda: None)()
            if (stage == "physical" and carrier is not None
                    and owner is carrier._directories[0]):
                remember_owners(carrier)
                raise first
            return returned

        def opened(profile, stack, budget):
            returned = real_open(profile, stack, budget)
            if stage == "node":
                carrier = refs["carrier"]()
                remember_owners(carrier)
                raise first
            return returned

        with monkeypatch.context() as patch:
            patch.setattr(physical, "_new_source_carrier", new)
            patch.setattr(safe._Directory, "__enter__", enter)
            patch.setattr(physical, "_open_physical", opened)
            same_error(lambda: enrollment.publish_prefix_owned_ex(actual_prefix_env["profile"],
                **options(actual_prefix_env)), first)
            carrier = refs["carrier"]()
            assert type(carrier) is physical._EnrollmentSourceCarrier
            assert first.__traceback__ is not None and carrier._receipt is None
            if stage == "physical":
                journal = carrier._directory_journals[0]
                assert journal._first is first and journal._first_tb is not None
            else:
                assert carrier._owners["node_held"]._first is first
                assert carrier._node_receipt.failed is True
            assert lifecycle._CURRENT.get() is custody._CURRENT.get() is None
        # No exception, traceback, carrier, factpin, owner or monkeypatch
        # wrapper is returned. The only escaped values are weakrefs/ints.
        return refs, keys

    references, keys = one_operation()
    gc.collect()
    assert references and all(reference() is None for reference in references.values())
    assert all(key not in physical._BUDGET_CARRIERS for key in keys["budgets"])
    assert all(key not in physical._FACTORY_OWNERS for key in keys["owners"])


@pytest.mark.parametrize("fault", ["missing", "wrong-shape", "explicit-none"],
                         ids=["missing-owner-row", "malformed-owner-row", "present-none-owner-row"])
def test_original_private_budget_claim_cannot_be_erased_by_reinit_after_registry_damage(actual_prefix_env, monkeypatch, fault):
    """Exercise the public constructor while the real original declaration lives.

    Registry damage is a finite metadata fault projection. It is not asserted
    to be caused by normal weakref collection, and does not replace live graph
    integrity. No parent/count/mode or terminal fact is fabricated.
    """
    real_open = physical._open_physical
    observed = []

    def opened(profile, stack, budget):
        returned = real_open(profile, stack, budget)
        carrier = physical._lookup_installed_carrier(budget)
        owner = carrier._owners["node_budget"]
        mode, parent, count = owner._enrollment_mode, owner.parent, owner.count
        assert parent is budget and count == 1 and mode is carrier._owner_pins["node_budget"]
        assert returned[1]._budget is owner
        foreign = deadline._RestrictedBudget(deadline.Namespace3Deadline(1.0e15))
        assert type(foreign) is deadline._RestrictedBudget and foreign is not parent
        key = id(owner)
        original = physical._FACTORY_OWNERS[key]
        if fault == "missing":
            del physical._FACTORY_OWNERS[key]
        elif fault == "wrong-shape":
            physical._FACTORY_OWNERS[key] = (object(),)
        else:
            physical._FACTORY_OWNERS[key] = None
        try:
            source_error(lambda: nodes._NodeFileBudget.__init__(owner, foreign), "CLOSED")
            assert owner._enrollment_mode is mode and owner.parent is parent
            assert owner.count == count == 1 and returned[1]._budget is owner
        finally:
            physical._FACTORY_OWNERS[key] = original
        assert physical._require_factory_owner_live(owner, "node_budget") is mode
        if fault == "explicit-none":
            # A present bad row cannot be treated as absence even for a fresh
            # exact ordinary self whose mode is missing/None. Preserve its
            # original fields until the ordinary key-absent path is reached.
            for marker in ("missing", "none"):
                fresh = object.__new__(nodes._NodeFileBudget)
                if marker == "none":
                    fresh._enrollment_mode = None
                fresh_key = id(fresh)
                assert fresh_key not in physical._FACTORY_OWNERS
                physical._FACTORY_OWNERS[fresh_key] = None
                try:
                    source_error(lambda: nodes._NodeFileBudget.__init__(fresh, foreign),
                                 "INVALID_ARGUMENT")
                    assert not hasattr(fresh, "parent") and not hasattr(fresh, "count")
                finally:
                    del physical._FACTORY_OWNERS[fresh_key]
                nodes._NodeFileBudget.__init__(fresh, foreign)
                assert fresh.parent is foreign and fresh.count == 0
        observed.append((carrier, owner, parent, count))
        return returned

    monkeypatch.setattr(physical, "_open_physical", opened)
    enrollment.publish_prefix_owned_ex(actual_prefix_env["profile"], **options(actual_prefix_env))
    assert len(observed) == 1
    carrier, owner, parent, count = observed[0]
    assert owner.parent is parent is carrier._budget and owner.count == count == 1
    assert carrier._node_receipt.normal_exit is True
    assert carrier._node_receipt.stream_close_receipt.readonly_close_receipt.fd_state == "closed"
    assert original_bytes(actual_prefix_env) == actual_prefix_env["originals"]
