"""DATA-only actual entry-domain test candidate; not imported or executed here."""
from contextlib import contextmanager
from copy import deepcopy

import pytest

from reno import home_creation_recovery17 as creation
from reno import sync_source_authority as authority
from reno import sync_source_readmit_local2 as local
from reno.sync_protocol import SyncError
from tests.test_sync_agent import env
from tests.test_sync_source_genesis17 import home17, live17, permanent_files
from tests.test_sync_source_lifecycle17 import waiting_control


def held_resources(owner):
    assert authority._CURRENT.get() is owner and owner.lock.held
    directories = []
    for directory in (owner.root, owner.checkpoint_root,
                      *owner.directory_index.values(),
                      *(lock.directory for lock in owner.lock.storage_guard._locks)):
        while directory is not None:
            if not any(item is directory for item in directories):
                assert directory.handles
                directories.append(directory)
            directory = directory.parent
    streams = tuple(item[2] for item in owner.pins) + tuple(
        lock.stream for lock in owner.lock.storage_guard._locks)
    assert directories and streams and all(not stream.closed for stream in streams)
    return owner, tuple(directories), streams


def assert_retired(value, resources):
    owner, directories, streams = resources
    assert not owner.lock.held and authority._CURRENT.get() is None
    assert all(not directory.handles for directory in directories)
    assert all(stream.closed for stream in streams)
    assert creation._CURRENT.get() is None and creation._CONTEXT.get() is None
    assert creation._READ_CURRENT.get() is None and creation._READ_CONTEXT.get() is None
    assert not creation._READ_SCOPES and value.store._directory is None


def forbid_rpc(value, monkeypatch):
    def no_rpc(*args, **kwargs):
        pytest.fail("An invalid original selector dispatched a new RPC")
    monkeypatch.setattr(value.client, "_request", no_rpc)


def test_new_owned_entry_keeps_original_sync_error_and_closes_real_resources(live17, monkeypatch):
    value = live17
    _, _, reference, _record = waiting_control(value)
    before = deepcopy(value.store.read()), permanent_files(value), deepcopy(value.remote.seen)
    observed = []
    real_owned = authority.owned

    @contextmanager
    def observed_owned(store, **options):
        with real_owned(store, **options) as owner:
            try:
                yield owner
            finally:
                observed.append(held_resources(owner))

    monkeypatch.setattr(authority, "owned", observed_owned)
    forbid_rpc(value, monkeypatch)
    with value.store.reference_operation(), pytest.raises(SyncError) as raised:
        with local.capture_control_intent_locked(profile=value.profile, store=value.store,
                request_id=reference["record_id"], expected_record_sha256="f" * 64):
            pytest.fail("A wrong SHA yielded a new owned capture")
    assert raised.value.code == "SYNC_SOURCE_AUTHORITY_INVALID" and len(observed) == 1
    assert_retired(value, observed[0])
    assert (value.store.read(), permanent_files(value), value.remote.seen) == before


def test_borrowed_owned_entry_keeps_direct_creation_error_and_original_owner(live17, monkeypatch):
    value = live17
    _, _, reference, _record = waiting_control(value)
    before = deepcopy(value.store.read()), permanent_files(value), deepcopy(value.remote.seen)
    forbid_rpc(value, monkeypatch)
    with value.store.reference_operation(), authority.owned(value.store) as owner:
        with pytest.raises(creation.CreationRecoveryError) as raised:
            with local.capture_control_intent_locked(profile=value.profile, store=value.store,
                    request_id=reference["record_id"], expected_record_sha256="f" * 64):
                pytest.fail("A wrong SHA yielded a borrowed capture")
        assert raised.value.code == "HOME_CREATION_RECOVERY17_ORIGINAL_CHANGED"
        resources = held_resources(owner)
        assert authority._CURRENT.get() is owner and owner.lock.held
        assert creation._CURRENT.get() is None and creation._CONTEXT.get() is None
        assert creation._READ_CURRENT.get() is None and creation._READ_CONTEXT.get() is None
        assert not creation._READ_SCOPES
        owner.verify()
        assert value.store.read() == before[0]
    assert_retired(value, resources)
    assert (value.store.read(), permanent_files(value), value.remote.seen) == before
