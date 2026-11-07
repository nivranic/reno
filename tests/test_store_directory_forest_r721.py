"""Genuine Store directory forests retain physical coverage and original failure tails."""
from collections import Counter
from contextlib import contextmanager
import hashlib
import importlib
from pathlib import Path
import time
import uuid

import pytest

from reno import storage_lifecycle as lifecycle, sync_media as safe
from tests.test_namespace_guard_runtime import initialized, profile  # noqa: F401


@pytest.fixture
def namespace(monkeypatch):
    graph = Path(__file__).resolve().parents[1] / "data/t16-runtime-origin-graph-r721"
    monkeypatch.syspath_prepend(str(graph))
    module = importlib.import_module("authority_runtime_origin_r721.personal_authority_namespace")
    path = graph / "authority_runtime_origin_r721/personal_authority_namespace.py"
    assert Path(module.__file__).resolve() == path.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "1cf99b186b8193725abd4a6fda2bfa4a3c7062efaf11730f9ec38cc072933cbd"
    assert module.lifecycle is lifecycle and module.DEADLINE_SECONDS == 30.0
    return module


@pytest.fixture
def store(namespace):
    module = importlib.import_module("authority_runtime_origin_r721.personal_authority_store")
    path = Path(namespace.__file__).with_name("personal_authority_store.py")
    assert Path(module.__file__).resolve() == path.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "70535e0e91b8a955c398ec6c92a47b9f1078f62e3141da2ee5c9e922ae4a4384"
    assert module.ns is namespace
    return module


@pytest.fixture
def stage(namespace, store):
    module = importlib.import_module("authority_runtime_origin_r721.owned_stage_io_r251")
    path = Path(namespace.__file__).with_name("owned_stage_io_r251.py")
    assert Path(module.__file__).resolve() == path.resolve()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "3fd2746f41c2759d489c6067df2123741026d2de2a5e725e511ccb10198e4611"
    assert module.ns is namespace and module.store is store
    return module


@contextmanager
def live_entry(namespace, store, profile):
    before_db = Path(safe._native_path(profile.database)).read_bytes()
    with lifecycle.exclusive(profile):
        with namespace._closed_namespace_owned_locked(profile, exclusive=True) as owner:
            budget = store._Budget(owner)
            with store._ClosingStack(budget) as stack:
                store._source_pins(stack, budget)
                custody = store._STACK_RECORDS[id(stack)]
                assert custody.stage is None and len(custody.held_original.resources) == 3
                assert all(row.resource_kind == "filehold" for row in custody.held_original.resources)
                yield stack, custody, budget
            assert custody.closed and id(stack) not in store._STACK_RECORDS
            assert all(row.finished and row.native_close_returned for row in custody.held_original.resources)
            assert all(row.closed for row in custody.pins)
            if custody.stage is not None:
                assert custody.stage.closed
        assert id(budget) not in store._BUDGET_RECORDS and id(budget) not in store._RETAINED_BUDGETS
    assert Path(safe._native_path(profile.database)).read_bytes() == before_db


def make_tree(tmp_path, names):
    root = tmp_path / "store-forest"
    paths = {name: root if name == "root" else root.joinpath(*name.split("/")) for name in names}
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def enter_tree(namespace, stack, paths, parents):
    directories, records = {}, {}
    for name, path in paths.items():
        parent = directories[parents[name]] if parents.get(name) is not None else None
        directory = stack.enter_context(safe._Directory(path, base=path, parent=parent))
        directories[name] = directory
        records[name] = namespace._CLOSED_DIRECTORY_RECORDS[id(directory)]
        assert records[name].original is directory and len(records[name].handles) == 1
    return directories, records


def reference_only(patch, store):
    original = store._store_directory_forest_plan_original

    def transparent(record):
        return original(record)

    patch.setattr(store, "_store_directory_forest_plan_original", transparent)


def observe_native(patch, namespace, records, *, on_native=None):
    original = namespace._closed_directory_native
    selected = {row.handles[0]: row for row in records.values()}
    counts = Counter()

    def observe(handle):
        result = original(handle)
        if handle in selected:
            counts[handle] += 1
            if on_native is not None:
                on_native(selected[handle], counts[handle])
        return result

    patch.setattr(namespace, "_closed_directory_native", observe)
    return counts


def by_name(records, counts):
    return {name: counts[row.handles[0]] for name, row in records.items()}


def test_real_chain_visits_each_native_record_once(namespace, store, initialized, monkeypatch, tmp_path):
    paths = make_tree(tmp_path, ["root", "a", "a/b"])
    with live_entry(namespace, store, initialized) as (stack, custody, budget):
        _, rows = enter_tree(namespace, stack, paths, {"a": "root", "a/b": "a"})
        before = budget.bytes, tuple(row.observed_bytes for row in custody.held_original.resources)
        with monkeypatch.context() as patch:
            counts = observe_native(patch, namespace, rows)
            store._store_directories_verify_original(custody)
            assert by_name(rows, counts) == {"root": 1, "a": 1, "a/b": 1}
            store._store_directories_verify_original(custody)
            assert by_name(rows, counts) == {"root": 2, "a": 2, "a/b": 2}
            counts.clear()
            reference_only(patch, store)
            store._store_directories_verify_original(custody)
            assert by_name(rows, counts) == {"root": 3, "a": 2, "a/b": 1}
        assert before == (budget.bytes, tuple(row.observed_bytes for row in custody.held_original.resources))
        assert custody.stage is None
    assert all(row.closed and len(row.released) == len(row.handles) for row in rows.values())


def test_real_branch_keeps_each_leaf_and_its_shared_parent(namespace, store, initialized, monkeypatch, tmp_path):
    paths = make_tree(tmp_path, ["root", "a", "b"])
    with live_entry(namespace, store, initialized) as (stack, custody, _):
        _, rows = enter_tree(namespace, stack, paths, {"a": "root", "b": "root"})
        with monkeypatch.context() as patch:
            counts = observe_native(patch, namespace, rows)
            store._store_directories_verify_original(custody)
            assert by_name(rows, counts) == {"root": 2, "a": 1, "b": 1}
            counts.clear()
            reference_only(patch, store)
            store._store_directories_verify_original(custody)
            assert by_name(rows, counts) == {"root": 3, "a": 1, "b": 1}


def test_same_path_distinct_registered_records_both_get_native_checks(namespace, store, initialized, monkeypatch, tmp_path):
    path = tmp_path / "independent-handles"
    path.mkdir()
    with live_entry(namespace, store, initialized) as (stack, custody, _):
        _, rows = enter_tree(namespace, stack, {"first": path, "second": path}, {})
        assert rows["first"] is not rows["second"]
        assert rows["first"].handles[0] != rows["second"].handles[0]
        with monkeypatch.context() as patch:
            counts = observe_native(patch, namespace, rows)
            store._store_directories_verify_original(custody)
            assert by_name(rows, counts) == {"first": 1, "second": 1}


def test_actual_namespace_parent_outside_store_tuple_is_still_checked(namespace, store, initialized, monkeypatch):
    with live_entry(namespace, store, initialized) as (stack, custody, _):
        parent = custody.namespace.directory
        parent_record = namespace._CLOSED_DIRECTORY_RECORDS[id(parent)]
        assert all(row is not parent_record for row in custody.directories)
        # Open the already-existing genuine facts directory through a distinct
        # Store-owned handle, with the original Namespace-owned parent.
        path = custody.namespace.facts.path
        child = stack.enter_context(safe._Directory(path, base=path, parent=parent))
        child_record = namespace._CLOSED_DIRECTORY_RECORDS[id(child)]
        assert child is not custody.namespace.facts
        assert all(row is not parent_record for row in custody.directories)
        rows = {"namespace_parent": parent_record, "store_child": child_record}
        assert all(len(row.handles) == 1 for row in rows.values())
        with monkeypatch.context() as patch:
            counts = observe_native(patch, namespace, rows)
            store._store_directories_verify_original(custody)
            assert by_name(rows, counts) == {"namespace_parent": 1, "store_child": 1}


def test_duplicate_tuple_uses_original_full_sweep(namespace, store, initialized, monkeypatch, tmp_path):
    paths = make_tree(tmp_path, ["root", "a"])
    with live_entry(namespace, store, initialized) as (stack, custody, _):
        _, rows = enter_tree(namespace, stack, paths, {"a": "root"})
        original = custody.directories
        custody.directories = original + (rows["a"],)
        try:
            with monkeypatch.context() as patch:
                counts = observe_native(patch, namespace, rows)
                store._store_directories_verify_original(custody)
                assert by_name(rows, counts) == {"root": 3, "a": 2}
                counts.clear()
                reference_only(patch, store)
                store._store_directories_verify_original(custody)
                assert by_name(rows, counts) == {"root": 3, "a": 2}
        finally:
            custody.directories = original


@pytest.mark.parametrize("custom", ["single", "sweep", "paths"])
def test_custom_directory_dispatch_keeps_original_native_counts(namespace, store, initialized, monkeypatch, tmp_path, custom):
    paths = make_tree(tmp_path, ["root", "a", "a/b"])
    with live_entry(namespace, store, initialized) as (stack, custody, _):
        _, rows = enter_tree(namespace, stack, paths, {"a": "root", "a/b": "a"})
        name = {"single": "_closed_directory_verify_original", "sweep": "_closed_directory_verify_sweep_original",
                "paths": "_closed_directory_verify_with_paths_original"}[custom]
        original = getattr(namespace, name)
        with monkeypatch.context() as patch:
            def transparent(*args, **kwargs):
                return original(*args, **kwargs)

            patch.setattr(namespace, name, transparent)
            counts = observe_native(patch, namespace, rows)
            store._store_directories_verify_original(custody)
            actual = by_name(rows, counts)
            counts.clear()
            reference_only(patch, store)
            store._store_directories_verify_original(custody)
            assert actual == by_name(rows, counts) == {"root": 3, "a": 2, "a/b": 1}


@pytest.mark.parametrize("broken", ["root", "a", "a/b"])
def test_every_real_record_expected_native_identity_is_verified(namespace, store, initialized, monkeypatch, tmp_path, broken):
    paths = make_tree(tmp_path, ["root", "a", "a/b"])
    with live_entry(namespace, store, initialized) as (stack, custody, _):
        _, rows = enter_tree(namespace, stack, paths, {"a": "root", "a/b": "a"})
        target = rows[broken]
        expected = list(target.native)
        target.native[0] = (expected[0][0], expected[0][1] + 1)
        try:
            with pytest.raises(namespace.NamespaceError) as caught:
                store._store_directories_verify_original(custody)
            assert caught.value.code == "NAMESPACE_STORE_ORIGINAL_DIRECTORY_HANDLE_CHANGED"
        finally:
            target.native[:] = expected


@pytest.mark.parametrize("which", ["store", "stage"])
def test_original_first_precedes_new_after_graph_dispatch(namespace, store, stage, initialized, monkeypatch, tmp_path, which):
    paths = make_tree(tmp_path, ["root", "a"])
    first = RuntimeError("original Store or Stage first from native-sample boundary")
    with pytest.raises(RuntimeError) as outer:
        with live_entry(namespace, store, initialized) as (stack, custody, budget):
            _, rows = enter_tree(namespace, stack, paths, {"a": "root"})
            actual_stage = store._stage_scope_original(stack) if which == "stage" else None
            fired = []
            with monkeypatch.context() as patch:
                def unexpected(record):
                    raise AssertionError("new after-graph must not precede original first")

                def inject(record, count):
                    if record is rows["a"] and count == 1:
                        fired.append(record)
                        if which == "store":
                            custody.first = first
                        else:
                            actual_stage.first_failure = first
                        patch.setattr(store, "_store_directory_forest_plan_original", unexpected)

                counts = observe_native(patch, namespace, rows, on_native=inject)
                try:
                    store._store_directories_verify_original(custody)
                except RuntimeError as caught:
                    assert caught is first and fired == [rows["a"]]
                    assert (custody.first if which == "store" else actual_stage.first_failure) is first
                    assert by_name(rows, counts) == {"root": 1, "a": 1}
                    raise  # Let the original failure-close path retain this first.
                raise AssertionError("the recorded first must be raised")
    assert outer.value is first and custody.first is first and custody.closed
    assert store._RETAINED_STACKS[id(stack)] is custody
    assert store._RETAINED_BUDGETS[id(budget)].first is first
    if actual_stage is not None:
        assert actual_stage.first_failure is first and actual_stage.closed


@pytest.mark.parametrize("change", ["registry", "native", "handles", "tuple", "planner"])
def test_late_forest_changes_are_rejected_without_resampling(namespace, store, initialized, monkeypatch, tmp_path, change):
    paths = make_tree(tmp_path, ["root", "a", "a/b"])
    with live_entry(namespace, store, initialized) as (stack, custody, _):
        _, rows = enter_tree(namespace, stack, paths, {"a": "root", "a/b": "a"})
        original_tuple = custody.directories
        original_native = list(rows["root"].native)
        original_handles = list(rows["root"].handles)
        original_plan = store._store_directory_forest_plan_original
        fired = []
        with monkeypatch.context() as patch:
            def transparent(record):
                return original_plan(record)

            def inject(record, count):
                if record is rows["a/b"] and count == 1:
                    assert not fired
                    fired.append(record)
                    if change == "registry":
                        namespace._CLOSED_DIRECTORY_RECORDS.pop(id(rows["a"].original))
                    elif change == "native":
                        rows["root"].native[0] = (original_native[0][0], original_native[0][1] + 1)
                    elif change == "handles":
                        rows["root"].handles.append(original_handles[0])
                    elif change == "tuple":
                        custody.directories = tuple(list(original_tuple))
                    else:
                        patch.setattr(store, "_store_directory_forest_plan_original", transparent)

            counts = observe_native(patch, namespace, rows, on_native=inject)
            try:
                with pytest.raises((namespace.NamespaceError, store.v2.StoreError)) as caught:
                    store._store_directories_verify_original(custody)
                assert caught.value.code.endswith("ORIGINAL_DIRECTORY_CHANGED")
                assert fired == [rows["a/b"]]
                assert by_name(rows, counts) == {"root": 1, "a": 1, "a/b": 1}
            finally:
                namespace._CLOSED_DIRECTORY_RECORDS[id(rows["a"].original)] = rows["a"]
                rows["root"].native[:] = original_native
                rows["root"].handles[:] = original_handles
                custody.directories = original_tuple


def test_actual_publication_creates_stage_only_after_entry_and_reads_back(namespace, store, stage, initialized, monkeypatch):
    entries = []
    with live_entry(namespace, store, initialized) as (stack, custody, budget):
        actual = custody.namespace
        legacy, _, names = store._legacy(actual, stack, budget)
        assert not ({"store-init-reservation.json", "store-header.json", store.v2.JOURNAL} & names)
        raw = store._canonical({
            "format_version": store.v2.FORMAT,
            "kind": "store_init_reservation",
            "store_init_operation_id": str(uuid.uuid4()),
            "declared_binding_sha256": store._sha(store._canonical(actual.declaration)),
            "legacy": legacy, "layout": store.v2._layout(),
        })
        original = store._publish_store_directory_entry_original

        def observe(stack_arg, directory_arg, budget_arg):
            assert stack_arg is stack and directory_arg is actual.directory and budget_arg is budget
            assert custody.stage is None
            result = original(stack_arg, directory_arg, budget_arg)
            assert custody.stage is None
            entries.append(result)
            return result

        with monkeypatch.context() as patch:
            patch.setattr(store, "_publish_store_directory_entry_original", observe)
            result = store._publish_store_metadata_original(stack, actual.directory, "store-init-reservation.json", raw, budget)
        assert entries == [namespace._CLOSED_DIRECTORY_RECORDS[id(actual.directory)]]
        assert result == hashlib.sha256(raw).hexdigest()
        assert Path(safe._native_path(actual.directory.path / "store-init-reservation.json")).read_bytes() == raw
        scope = custody.stage
        assert scope is not None and not scope.resources and scope.first_failure is None
        assert not any(row.scope is scope for row in stage._STORE_METADATA_ORIGINALS.values())
        assert not any(row.scope is scope for row in stage._RETAINED_STORE_METADATA_ORIGINALS.values())
    assert scope.closed and id(scope) not in stage._SCOPES
