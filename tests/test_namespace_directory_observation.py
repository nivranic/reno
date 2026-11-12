"""Real closed-directory custody with phase-local physical observations.

The legacy baseline is selected with H01_NAMESPACE_OBSERVATION_PACKAGE=r384.
No namespace authority owner is fabricated by these physical-layer tests.
"""
from collections import Counter
import hashlib
import os
from pathlib import Path
import stat

import pytest

from tests.historical_sources import import_historical_snapshot

from reno import storage_lifecycle as lifecycle


@pytest.fixture
def namespace(monkeypatch):
    revision = os.environ.get("H01_NAMESPACE_OBSERVATION_PACKAGE", "r649")
    assert revision in {"r384", "r649"}
    graph = Path(__file__).resolve().parents[1] / "data" / f"t16-runtime-origin-graph-{revision}"
    monkeypatch.syspath_prepend(str(graph))
    module = import_historical_snapshot(f"authority_runtime_origin_{revision}.personal_authority_namespace")
    assert Path(module.__file__).resolve() == graph / f"authority_runtime_origin_{revision}" / "personal_authority_namespace.py"
    assert module.DEADLINE_SECONDS == 30.0
    return module


@pytest.fixture
def directories(namespace, tmp_path):
    profile = lifecycle.StorageProfile(tmp_path / "data/reno.db", tmp_path / "media",
                                       tmp_path / "reports", tmp_path / "checkpoints")
    lifecycle.initialize(profile)
    parent_path = profile.database.parent / "observed"
    child_path, sibling_path = parent_path / "child", profile.database.parent / "sibling"
    child_path.mkdir(parents=True)
    sibling_path.mkdir()
    with lifecycle.shared(profile, timeout=0):
        stack = namespace._ClosedNamespaceStack(profile, namespace._closed_io.CancellationData())
        try:
            parent = stack.enter_context(namespace.safe._Directory(parent_path, base=parent_path))
            stack.enter_context(namespace.safe._Directory(child_path, base=child_path, parent=parent))
            stack.enter_context(namespace.safe._Directory(sibling_path, base=sibling_path))
            records = tuple(stack.directories)
            assert len(records) == 3
            assert all(record.owner == namespace._owner() for record in records)
            yield stack, records
        finally:
            stack.close()


class _ChangedStat:
    def __init__(self, original, **changes):
        self._original = original
        self.__dict__.update(changes)

    def __getattr__(self, name):
        return getattr(self._original, name)


def _paths(namespace, records):
    return {namespace.safe._native_path(path) for record in records
            for path, _, _ in record.original.identities}


def test_ordinary_import_graph_and_source_bindings(namespace):
    names = ("initialization_directory_context", "personal_preservation_files",
             "personal_authority_files", "shared_original_io_r237", "shared_held_io_r250",
             "personal_authority_namespace", "personal_authority_schema", "personal_authority_store_v2",
             "personal_mutation_contract", "personal_authority_store", "personal_authority_store_v2_bridge",
             "owned_stage_io_r251", "storage_readonly", "personal_preservation_source", "personal_origin_observer")
    package = importlib.import_module(namespace.__package__)
    modules = {name: importlib.import_module(f"{namespace.__package__}.{name}") for name in names}
    folder = Path(namespace.__file__).resolve().parent
    assert Path(package.__file__).resolve() == folder / "__init__.py"
    digests = {}
    for name, module in modules.items():
        path = Path(module.__file__).resolve()
        assert path == folder / f"{name}.py"
        digests[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    observer = modules["personal_origin_observer"]
    store = modules["personal_authority_store"]
    v2 = modules["personal_authority_store_v2"]
    stage = modules["owned_stage_io_r251"]
    source = modules["personal_preservation_source"]
    readonly = modules["storage_readonly"]
    pf = modules["personal_preservation_files"]
    af = modules["personal_authority_files"]
    lower = modules["shared_original_io_r237"]
    held = modules["shared_held_io_r250"]
    bridge = modules["personal_authority_store_v2_bridge"]
    assert observer._source_bindings_original() is None
    for field, name in (("_NAMESPACE_SHA", "personal_authority_namespace"),
                        ("_SCHEMA_SHA", "personal_authority_schema"),
                        ("_PF_SHA", "personal_preservation_files"),
                        ("_LOWER_SHA", "shared_original_io_r237"),
                        ("_HELD_SHA", "shared_held_io_r250")):
        assert getattr(observer, field) == digests[name]
    assert store.NAMESPACE_SOURCE_SHA256 == digests["personal_authority_namespace"]
    assert store.V2_SOURCE_SHA256 == digests["personal_authority_store_v2"]
    assert store.MUTATION_SOURCE_SHA256 == digests["personal_mutation_contract"]
    assert namespace.lifecycle is observer.lifecycle is source.lifecycle is readonly.lifecycle is lifecycle
    assert modules["personal_authority_namespace"] is observer.namespace is store.ns is stage.ns is namespace
    assert namespace.pf is af.files is lower.pf is held.pf is v2.private_files is store.pf is stage.pf is pf
    assert observer.personal_files is source.package_files is pf
    assert observer.personal_source is source and source.readonly is observer.readonly is readonly
    assert namespace.af is held.af is v2.af is af
    assert namespace._closed_io is held.original is stage.lower is lower
    assert namespace._held_io is stage.held is held
    assert stage.store is bridge.s is store and store.v2 is bridge.v2 is v2
    assert store.mc is modules["personal_mutation_contract"]


def test_closed_child_reads_each_ancestor_once_and_every_native_handle(namespace, directories, monkeypatch):
    _, records = directories
    parent, child, _ = records
    paths = _paths(namespace, (parent, child))
    observed, native = Counter(), Counter()
    original_stat, original_native = os.stat, namespace._closed_directory_native

    def observe_stat(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        if path in paths:
            assert kwargs.get("follow_symlinks") is False
            observed[path] += 1
        return result

    def observe_native(handle):
        native[handle] += 1
        return original_native(handle)

    with monkeypatch.context() as patch:
        patch.setattr(os, "stat", observe_stat)
        patch.setattr(namespace, "_closed_directory_native", observe_native)
        namespace._closed_directory_verify_original(child)
    assert native == Counter(parent.handles + child.handles)
    assert observed == Counter({path: 1 for path in paths})


def test_sweep_reads_shared_ancestors_once_and_every_native_handle(namespace, directories, monkeypatch):
    _, records = directories
    paths = _paths(namespace, records)
    observed, native = Counter(), Counter()
    original_stat, original_native = os.stat, namespace._closed_directory_native

    def observe_stat(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        if path in paths:
            observed[path] += 1
        return result

    def observe_native(handle):
        native[handle] += 1
        return original_native(handle)

    with monkeypatch.context() as patch:
        patch.setattr(os, "stat", observe_stat)
        patch.setattr(namespace, "_closed_directory_native", observe_native)
        namespace._closed_directory_verify_sweep_original(records)
    # Native observations are never reused merely because paths overlap.
    assert set(native) == {handle for record in records for handle in record.handles}
    assert all(count >= 1 for count in native.values())
    assert observed == Counter({path: 1 for path in paths})


@pytest.mark.parametrize("damage", ["owner", "registry", "handles_list", "identities_list", "fields", "extra_field"])
def test_original_record_and_directory_structure_remain_required(namespace, directories, monkeypatch, damage):
    _, records = directories
    parent, child, _ = records
    with monkeypatch.context() as patch:
        if damage == "owner":
            patch.setattr(parent, "owner", (None, None, None))
        elif damage == "registry":
            patch.delitem(namespace._CLOSED_DIRECTORY_RECORDS, id(parent.original))
        elif damage == "handles_list":
            patch.setattr(parent.original, "handles", list(parent.original.handles))
        elif damage == "identities_list":
            patch.setattr(parent.original, "identities", list(parent.original.identities))
        elif damage == "fields":
            patch.setattr(parent.original, "create", True)
        else:
            patch.setattr(parent.original, "unexpected", True, raising=False)
        with pytest.raises(namespace.NamespaceError) as caught:
            namespace._closed_directory_verify_original(child)
    assert caught.value.code == "NAMESPACE_STORE_ORIGINAL_DIRECTORY_CHANGED"


def test_shared_path_different_expected_identity_is_not_hidden(namespace, directories, monkeypatch):
    _, records = directories
    parent, _, sibling = records
    target = parent.original.path.parent
    original = sibling.original.identities
    conflicting = [(path, device, inode + 1 if path == target else inode)
                   for path, device, inode in original]
    assert conflicting != original
    with monkeypatch.context() as patch:
        # Keep the structural list/tuple agreement while corrupting one retained
        # expectation, so a path-only dedup must still compare this second row.
        patch.setattr(sibling.original, "identities", conflicting)
        patch.setattr(sibling, "initial_identity_list", conflicting)
        patch.setattr(sibling, "identity_tuple", tuple(conflicting))
        with pytest.raises(namespace.safe.SyncError):
            namespace._closed_directory_verify_sweep_original(records)


@pytest.mark.parametrize("record_index", [0, 1, 2])
def test_each_original_native_handle_identity_remains_required(namespace, directories, monkeypatch, record_index):
    _, records = directories
    target = records[record_index].handles[-1]
    original_native = namespace._closed_directory_native
    observed = []

    def observe_native(handle):
        result = original_native(handle)
        observed.append(handle)
        return (result[0], result[1] + 1) if handle == target else result

    with monkeypatch.context() as patch:
        patch.setattr(namespace, "_closed_directory_native", observe_native)
        with pytest.raises(namespace.NamespaceError) as caught:
            namespace._closed_directory_verify_sweep_original(records)
    assert target in observed
    assert caught.value.code == "NAMESPACE_STORE_ORIGINAL_DIRECTORY_HANDLE_CHANGED"


@pytest.mark.parametrize("change", ["identity", "unsafe_type", "reparse"])
def test_later_phase_observes_new_path_state(namespace, directories, monkeypatch, change):
    _, records = directories
    target = namespace.safe._native_path(records[0].original.path.parent)
    original_stat = os.stat
    phase = 0
    observations = []

    def observe_stat(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        if path == target:
            observations.append(phase)
            if phase:
                if change == "identity":
                    return _ChangedStat(result, st_ino=result.st_ino + 1)
                if change == "unsafe_type":
                    return _ChangedStat(result, st_mode=stat.S_IFREG | stat.S_IMODE(result.st_mode))
                return _ChangedStat(result, st_file_attributes=
                                    getattr(result, "st_file_attributes", 0) | namespace.safe._REPARSE)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(os, "stat", observe_stat)
        namespace._closed_directory_verify_sweep_original(records)
        phase = 1
        with pytest.raises(namespace.safe.SyncError):
            namespace._closed_directory_verify_sweep_original(records)
    assert observations[0] == 0 and 1 in observations


def test_sweep_checks_later_directories_after_failure_and_keeps_first_object(namespace, directories, monkeypatch):
    _, records = directories
    original_stat = os.stat
    target = namespace.safe._native_path(records[0].original.path)
    last = namespace.safe._native_path(records[-1].original.path)
    first = OSError("synthetic first directory observation failure")
    later = OSError("synthetic later directory observation failure")
    observed = []

    def observe_stat(path, *args, **kwargs):
        if path in {target, last}:
            observed.append(path)
            raise first if path == target else later
        return original_stat(path, *args, **kwargs)

    failures = namespace._ExitFailures()
    with monkeypatch.context() as patch:
        patch.setattr(os, "stat", observe_stat)
        namespace._closed_directory_verify_sweep_original(records, failures=failures)
    assert target in observed and last in observed
    with pytest.raises(OSError) as caught:
        failures.raise_first()
    assert caught.value is first


def test_failed_body_error_precedes_all_exit_observation_errors(namespace, directories, monkeypatch):
    _, records = directories
    original_stat = os.stat
    paths = _paths(namespace, records)
    last = namespace.safe._native_path(records[-1].original.path)
    body = ValueError("synthetic original body failure")
    failures = namespace._ExitFailures()
    failures.capture(body)
    observed = []

    def observe_stat(path, *args, **kwargs):
        if path in paths:
            observed.append(path)
        result = original_stat(path, *args, **kwargs)
        if path == last:
            raise OSError("synthetic exit observation failure")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(os, "stat", observe_stat)
        namespace._closed_directory_verify_sweep_original(records, failures=failures)
    assert set(observed) == paths
    assert failures.notes >= 1
    with pytest.raises(ValueError) as caught:
        failures.raise_first()
    assert caught.value is body
