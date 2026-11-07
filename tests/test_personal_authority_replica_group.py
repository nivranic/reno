"""Independent checked-outer G1 ownership and native-lock regressions.

Only fresh synthetic profiles are initialized. Ordinary cases exercise real
filesystem objects and the original lifecycle byte locks. Named fault cases
must remain distinct from that evidence. Root alone imports/runs this file.
The returned history proves only this group closed; it is not a replica result.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import copy_context
import gc
import hashlib
import json
import os
from pathlib import Path
import queue
import sqlite3
import stat
import subprocess
import sys
import threading
import time
from types import MappingProxyType, SimpleNamespace
import uuid
import weakref

import pytest

from reno import personal_authority_replica_group as group
from reno import personal_authority_preservation_routing as routing
from reno import personal_authority_raw_preservation as raw1
from reno import personal_authority_raw_preservation2 as raw2
from reno import storage_lifecycle as lifecycle


ERROR = group.AuthorityReplicaGroupError
PREFIX = "AUTHORITY_REPLICA_GROUP1_"
FIELDS = ("database", "media", "reports", "checkpoints")
CLOSED_FIELDS = {
    "format_version", "kind", "scope_id", "profile_identities", "root_keys_sha256",
    "native_resources_acquired", "native_resources_normal_closed", "peak_resources",
    "pending_resources", "current_resources", "unknown_acquires", "unknown_closes",
    "native_normal_closed", "lifecycle_context_returned", "write_authority", "sender_authority",
}
COUNTERS = ("native_resources_acquired", "native_resources_normal_closed", "peak_resources",
            "pending_resources", "current_resources", "unknown_acquires", "unknown_closes")
HELPER = Path(__file__).with_name("helpers") / "replica_group_child.py"


def wire(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def path_key(path):
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def make_profile(root, *, shared_media=None):
    return lifecycle.StorageProfile(root / "data/reno.db", shared_media or root / "media",
                                    root / "reports", root / "checkpoints")


def profile_rows(profiles):
    unique = []
    for value in profiles:
        row = [path_key(getattr(value, field)) for field in FIELDS]
        if row not in unique:
            unique.append(row)
    return unique


def root_keys(profiles):
    paths = {path_key(root) for value in profiles for root in
             (value.database.parent, value.media, value.reports, value.checkpoints)}
    return sorted(paths, key=lambda text: text.encode("utf-8"))


def snapshot(root):
    """Observe only this test's isolated tree, without atime side effects."""
    result = {}
    if not root.exists():
        return result
    for path in [root, *root.rglob("*")]:
        info = path.stat(follow_symlinks=False)
        assert stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)
        result[path.relative_to(root).as_posix()] = (
            info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns,
            path.read_bytes() if stat.S_ISREG(info.st_mode) else None,
        )
    return result


def plain(value):
    if type(value) is dict:
        assert all(type(key) is str for key in value)
        for item in value.values():
            plain(item)
    elif type(value) is list:
        for item in value:
            plain(item)
    else:
        assert value is None or type(value) in (int, str, bool)


def assert_closed(value, profiles):
    assert type(value) is dict and set(value) == CLOSED_FIELDS
    plain(value)
    assert type(value["format_version"]) is int and value["format_version"] == 1
    assert value["kind"] == "replica_checked_outer_group"
    scope_id = uuid.UUID(value["scope_id"])
    assert scope_id.int and str(scope_id) == value["scope_id"]
    assert value["profile_identities"] == profile_rows(profiles)
    assert value["root_keys_sha256"] == hashlib.sha256(wire(root_keys(profiles))).hexdigest()
    for field in COUNTERS:
        assert type(value[field]) is int and 0 <= value[field] <= 2 ** 63 - 1
    assert value["native_resources_acquired"] == value["native_resources_normal_closed"] > 0
    assert 0 < value["peak_resources"] <= min(1041, value["native_resources_acquired"])
    for field in ("pending_resources", "current_resources", "unknown_acquires", "unknown_closes"):
        assert value[field] == 0
    assert value["native_normal_closed"] is value["lifecycle_context_returned"] is True
    assert value["write_authority"] is value["sender_authority"] is False


def reject(function, *args, **kwargs):
    with pytest.raises(ERROR) as raised:
        function(*args, **kwargs)
    assert type(raised.value) is ERROR and raised.value.code.startswith(PREFIX)
    return raised.value


def reject_enter(profiles, **kwargs):
    def enter():
        with group.checked_exclusive_many(profiles, **kwargs):
            pytest.fail("Invalid checked-group admission yielded a token")
    return reject(enter)


def no_history(token):
    reject(group.guard_original, token)
    reject(group.consume_closed_original, token)


@pytest.fixture
def single(tmp_path):
    value = make_profile(tmp_path / "one")
    lifecycle.initialize(value)
    (value.media / "original.bin").write_bytes(b"synthetic unchanged media\x00\xff")
    (value.reports / "empty-directory").mkdir()
    return value


@pytest.fixture
def pair(tmp_path):
    shared = tmp_path / "shared-media"
    values = (make_profile(tmp_path / "a", shared_media=shared),
              make_profile(tmp_path / "b", shared_media=shared))
    for value in values:
        lifecycle.initialize(value)
    (shared / "original.bin").write_bytes(b"shared synthetic bytes")
    return values


def close_successfully(profiles):
    with group.checked_exclusive_many(tuple(profiles)) as token:
        guard = group.guard_original(token)
        assert type(guard) is lifecycle.StorageGuard
        assert lifecycle.current_guard() is guard
        for value in profiles:
            assert lifecycle.require_guard(value, exclusive=True) is guard
    result = group.consume_closed_original(token)
    assert_closed(result, profiles)
    return token, result


def test_native_group_holds_original_guard_and_preserves_all_initialized_bytes(single, tmp_path, monkeypatch):
    before = snapshot(tmp_path)
    assert not single.database.exists()

    def forbidden_sql(*args, **kwargs):
        pytest.fail("Checked group attempted SQL")

    monkeypatch.setattr(sqlite3, "connect", forbidden_sql)
    token, observed = close_successfully((single,))
    assert observed["native_resources_acquired"] >= 2 * len(root_keys((single,)))
    assert snapshot(tmp_path) == before
    assert not single.database.exists() and lifecycle.current_guard() is None
    no_history(token)


def test_two_profiles_share_roots_once_and_preserve_primary(pair, tmp_path):
    a, b = pair
    assert len(root_keys(pair)) == 7
    before = snapshot(tmp_path)
    with group.checked_exclusive_many((b, a)) as token:
        guard = group.guard_original(token)
        assert guard.profile is b and guard.profiles == (b, a)
        assert len(guard._locks) == 7
        assert [path_key(lock.directory.path) for lock in guard._locks] == root_keys(pair)
        assert all(lock.locked is True for lock in guard._locks)
        assert lifecycle.require_guard(a, exclusive=True) is guard
        assert lifecycle.require_guard(b, exclusive=True) is guard
    assert_closed(group.consume_closed_original(token), (b, a))
    assert snapshot(tmp_path) == before


def test_exact_duplicate_profiles_deduplicate_without_replacing_primary(single):
    duplicate = lifecycle.StorageProfile(*(getattr(single, field) for field in FIELDS))
    assert duplicate is not single
    with group.checked_exclusive_many((single, duplicate)) as token:
        guard = group.guard_original(token)
        assert guard.profiles == (single,) and guard.profiles[0] is single
        assert len(guard._locks) == 4
        assert lifecycle.require_guard(duplicate, exclusive=True) is guard
    assert_closed(group.consume_closed_original(token), (single,))


@pytest.mark.parametrize("legacy", ["shared", "exclusive", "shared_many", "exclusive_many"])
def test_legitimate_legacy_subset_borrows_same_actual_guard(pair, legacy):
    a, b = pair
    with group.checked_exclusive_many(pair) as token:
        guard = group.guard_original(token)
        argument = (b, a) if legacy.endswith("_many") else b
        with getattr(lifecycle, legacy)(argument, timeout=0) as borrowed:
            assert borrowed is guard and group.guard_original(token) is guard
            assert borrowed.profile is a
            assert lifecycle.require_guard(b, exclusive=True) is guard
        assert lifecycle.current_guard() is guard
    assert_closed(group.consume_closed_original(token), pair)


def test_root_set_equivalence_does_not_add_an_unlisted_profile(pair):
    a, b = pair
    unlisted = lifecycle.StorageProfile(b.database, b.media, b.checkpoints, b.reports)
    with group.checked_exclusive_many(pair) as token:
        guard = group.guard_original(token)
        assert guard.allows(unlisted) is False
        with pytest.raises(lifecycle.StorageLifecycleError) as raised:
            lifecycle.require_guard(unlisted, exclusive=True)
        assert raised.value.code == "STORAGE_PROFILE_MISMATCH"
        assert lifecycle.require_guard(a) is guard
    assert_closed(group.consume_closed_original(token), pair)


@pytest.mark.parametrize("existing", ["shared", "exclusive"])
def test_new_checked_group_cannot_borrow_upgrade_or_expand_an_existing_current(single, existing):
    with getattr(lifecycle, existing)(single, timeout=0) as original:
        reject_enter((single,))
        assert lifecycle.current_guard() is original
    assert lifecycle.current_guard() is None
    close_successfully((single,))


def test_checked_group_reentry_from_held_body_cannot_replace_current(single):
    with group.checked_exclusive_many((single,)) as token:
        original = group.guard_original(token)
        reject_enter((single,))
        assert lifecycle.current_guard() is original
    assert_closed(group.consume_closed_original(token), (single,))


@pytest.mark.parametrize("value", [None, [], (), [None], (None,), (None, None, None),
                                  MappingProxyType({}), "profile"])
def test_invalid_profile_container_never_initializes_a_missing_tree(tmp_path, value):
    before = snapshot(tmp_path)
    reject_enter(value)
    assert snapshot(tmp_path) == before
    assert lifecycle.current_guard() is None


def test_more_than_two_profiles_rejects_before_dedup_or_effects(tmp_path):
    value = make_profile(tmp_path / "absent")
    before = snapshot(tmp_path)
    reject_enter((value, value, value))
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("shape", ["tuple", "profile", "path", "callback_bool"])
def test_exact_type_admission_does_not_dispatch_untrusted_hooks(tmp_path, shape):
    calls = []

    class Tuple(tuple):
        def __iter__(self):
            calls.append("tuple.iter")
            raise AssertionError("untrusted iterable")

    class Profile(lifecycle.StorageProfile):
        @property
        def identity(self):
            calls.append("profile.identity")
            raise AssertionError("untrusted profile")

    class NativePath(type(Path())):
        def __fspath__(self):
            calls.append("path.fspath")
            raise AssertionError("untrusted path")

    class Callback:
        def __bool__(self):
            calls.append("callback.bool")
            raise AssertionError("callback truth coercion")
        def __call__(self):
            raise RuntimeError("expected cancellation")

    value = make_profile(tmp_path / "absent")
    if shape == "tuple":
        reject_enter(Tuple((value,)))
    elif shape == "profile":
        forged = object.__new__(Profile)
        for field in FIELDS:
            object.__setattr__(forged, field, getattr(value, field))
        reject_enter((forged,))
    elif shape == "path":
        object.__setattr__(value, "media", NativePath(value.media))
        reject_enter((value,))
    else:
        # Callable callbacks need not be truthy; use initialized paths so this
        # case reaches the actual cancellation boundary and preserves its error.
        lifecycle.initialize(value)
        with pytest.raises(RuntimeError, match="expected cancellation"):
            with group.checked_exclusive_many((value,), check_cancelled=Callback()):
                pytest.fail("Cancellation yielded a checked group")
    assert calls == []


@pytest.mark.parametrize("alter", ["relative", "parent",
    pytest.param("reserved", marks=pytest.mark.skipif(os.name != "nt", reason="Windows reserved component")),
    "component_limit", "wrong_field_type"])
def test_mutated_profile_paths_are_revalidated_before_native_access(tmp_path, alter):
    value = make_profile(tmp_path / "absent")
    if alter == "relative":
        changed = Path("relative")
    elif alter == "parent":
        changed = tmp_path / "parent/../escape"
    elif alter == "reserved":
        changed = tmp_path / "CON"
    elif alter == "component_limit":
        changed = Path(tmp_path.anchor).joinpath(*(["part"] * 129))
    else:
        changed = str(value.media)
    object.__setattr__(value, "media", changed)
    before = snapshot(tmp_path)
    reject_enter((value,))
    assert snapshot(tmp_path) == before


def test_missing_lock_or_root_fails_without_initializing_it(tmp_path):
    value = make_profile(tmp_path / "absent")
    before = snapshot(tmp_path)
    with pytest.raises((ERROR, lifecycle.StorageLifecycleError, OSError)):
        with group.checked_exclusive_many((value,)):
            pytest.fail("Uninitialized roots became a checked group")
    assert snapshot(tmp_path) == before
    lifecycle.initialize(value)
    lock = value.reports / lifecycle.LOCK_NAME
    lock.unlink()
    before = snapshot(tmp_path)
    with pytest.raises((ERROR, lifecycle.StorageLifecycleError, OSError)):
        with group.checked_exclusive_many((value,)):
            pytest.fail("Missing marker was initialized implicitly")
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("alter", ["marker_bytes", "marker_length", "hardlink", "database_directory", "database_hardlink"])
def test_unsafe_existing_lock_or_database_never_gets_repaired(single, tmp_path, alter):
    lock = single.reports / lifecycle.LOCK_NAME
    if alter == "marker_bytes":
        lock.write_bytes(b"x" * len(lifecycle._MARKER))
    elif alter == "marker_length":
        lock.write_bytes(lifecycle._MARKER + b"extra")
    elif alter == "hardlink":
        os.link(lock, tmp_path / "lock-alias")
    elif alter == "database_directory":
        single.database.mkdir()
    else:
        single.database.write_bytes(b"synthetic non-SQLite file")
        os.link(single.database, tmp_path / "db-alias")
    before = snapshot(tmp_path)
    with pytest.raises((ERROR, lifecycle.StorageLifecycleError, OSError)):
        with group.checked_exclusive_many((single,)):
            pytest.fail("Unsafe storage yielded a checked group")
    assert lifecycle.current_guard() is None and snapshot(tmp_path) == before


def test_regular_database_is_only_checked_as_a_file_and_never_opened_as_sql(single, monkeypatch):
    single.database.write_bytes(b"explicit synthetic bytes, not SQLite")
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("Unexpected database connection"))
    before = single.database.read_bytes()
    close_successfully((single,))
    assert single.database.read_bytes() == before


@pytest.mark.parametrize("entry", ["require_guard", "current_guard", "original_verify", "original_allows", "borrow_shared", "borrow_exclusive"])
@pytest.mark.parametrize("shadow", ["verify", "allows", "exclusive", "active", "profiles", "profile_ids", "locks", "owner", "class"])
def test_all_original_authorization_entries_reject_shadowed_actual_guard(single, entry, shadow):
    calls = []
    token = None
    with pytest.raises(ERROR):
        with group.checked_exclusive_many((single,)) as token:
            guard = group.guard_original(token)
            if shadow in {"verify", "allows"}:
                setattr(guard, shadow, lambda *a, **k: calls.append(shadow) or True)
            elif shadow == "exclusive":
                guard._exclusive = False
            elif shadow == "active":
                guard._active = False
            elif shadow == "profiles":
                guard._profiles = tuple(list(guard._profiles))
            elif shadow == "profile_ids":
                guard._profile_ids = frozenset(list(guard._profile_ids))
            elif shadow == "locks":
                guard._locks = tuple(list(guard._locks))
            elif shadow == "owner":
                guard._owner = (os.getpid(), threading.get_ident(), object())
            else:
                class Shadow(lifecycle.StorageGuard):
                    pass
                guard.__class__ = Shadow
            if entry == "require_guard":
                lifecycle.require_guard(single, exclusive=True)
            elif entry == "current_guard":
                lifecycle.current_guard()
            elif entry == "original_verify":
                lifecycle.StorageGuard.verify(guard)
            elif entry == "original_allows":
                lifecycle.StorageGuard.allows(guard, single)
            else:
                factory = lifecycle.shared if entry == "borrow_shared" else lifecycle.exclusive
                with factory(single, timeout=0):
                    pytest.fail("Shadow guard authorized a legacy borrower")
            pytest.fail("Shadow guard passed an original authorization entry")
    assert calls == [] and lifecycle.current_guard() is None
    assert token is not None
    no_history(token)


@pytest.mark.parametrize("legacy", ["shared", "exclusive", "shared_many", "exclusive_many"])
def test_legacy_borrow_exit_observer_precedes_a_shadowed_verify(single, legacy):
    called = []
    with pytest.raises(ERROR):
        with group.checked_exclusive_many((single,)) as token:
            guard = group.guard_original(token)
            argument = (single,) if legacy.endswith("_many") else single
            with getattr(lifecycle, legacy)(argument, timeout=0):
                guard.verify = lambda: called.append("shadow") or guard
            pytest.fail("Changed guard escaped the borrow's exit observer")
    assert called == [] and lifecycle.current_guard() is None
    no_history(token)


@pytest.mark.parametrize("field", FIELDS)
def test_mutating_a_registered_profile_is_rejected_on_fresh_projection(single, field):
    original = getattr(single, field)
    with pytest.raises(ERROR):
        with group.checked_exclusive_many((single,)) as token:
            object.__setattr__(single, field, original.with_name("unregistered"))
            lifecycle.require_guard(single, exclusive=True)
    object.__setattr__(single, field, original)
    no_history(token)
    close_successfully((single,))


@pytest.mark.parametrize("shadow", ["locked", "directory", "stream", "identity", "verify", "verify_stat", "entry_stat", "attempt", "release"])
def test_registered_lock_links_and_methods_are_pinned_but_cleanup_uses_original_ownership(single, shadow):
    calls = []
    with pytest.raises(ERROR):
        with group.checked_exclusive_many((single,)) as token:
            lock = group.guard_original(token)._locks[0]
            if shadow == "locked":
                lock.locked = False
            elif shadow in {"directory", "stream"}:
                setattr(lock, shadow, object())
            elif shadow == "identity":
                lock.identity = (0, 0)
            else:
                name = "_verify_entry_stat" if shadow == "entry_stat" else shadow
                setattr(lock, name, lambda *a, **k: calls.append(name))
            lifecycle.require_guard(single, exclusive=True)
    assert calls == [] and lifecycle.current_guard() is None
    no_history(token)
    # This second actual acquisition proves that field tampering did not skip
    # the recorded release responsibility of the first group.
    close_successfully((single,))


@pytest.mark.skipif(os.name != "nt", reason="Windows OVERLAPPED contract")
@pytest.mark.parametrize("field", ["Offset", "OffsetHigh", "hEvent", "replace"])
def test_windows_overlapped_identity_and_fixed_offsets_cannot_change(single, field):
    with pytest.raises(ERROR):
        with group.checked_exclusive_many((single,)) as token:
            lock = group.guard_original(token)._locks[0]
            if field == "replace":
                lock.overlapped = lifecycle._Overlapped()
            else:
                setattr(lock.overlapped, field, 1)
            lifecycle.require_guard(single, exclusive=True)
    no_history(token)
    close_successfully((single,))


@pytest.mark.parametrize("when", ["held", "closed"])
def test_unknown_tokens_and_caller_dictionaries_never_become_history(single, when):
    with group.checked_exclusive_many((single,)) as token:
        if when == "held":
            for false_token in (object(), {}, MappingProxyType({}), None):
                reject(group.guard_original, false_token)
                reject(group.consume_closed_original, false_token)
            reject(group.consume_closed_original, token)
    if when == "closed":
        for false_token in (object(), {}, MappingProxyType({}), None):
            reject(group.guard_original, false_token)
            reject(group.consume_closed_original, false_token)
    assert_closed(group.consume_closed_original(token), (single,))


def test_close_observation_is_detached_single_use_and_never_an_authorization_token(single):
    with group.checked_exclusive_many((single,)) as token:
        guard = group.guard_original(token)
    observed = group.consume_closed_original(token)
    assert_closed(observed, (single,))
    observed["profile_identities"][0][0] = "changed by caller"
    observed["write_authority"] = True
    reject(group.consume_closed_original, token)
    reject(group.guard_original, observed)
    reject(group.consume_closed_original, observed)
    assert guard._active is False and lifecycle.current_guard() is None


@pytest.mark.parametrize("shadow", ["active", "allows", "verify", "class"])
def test_closed_guard_weak_marker_survives_current_and_attribute_forgery(single, shadow):
    with group.checked_exclusive_many((single,)) as token:
        guard = group.guard_original(token)
    assert_closed(group.consume_closed_original(token), (single,))
    calls = []
    if shadow == "active":
        guard._active = True
    elif shadow in {"allows", "verify"}:
        setattr(guard, shadow, lambda *a, **k: calls.append(shadow) or True)
    else:
        class Shadow(lifecycle.StorageGuard):
            pass
        guard.__class__ = Shadow
    current = lifecycle._CURRENT.set(guard)
    try:
        reject(lifecycle.require_guard, single, exclusive=True)
        reject(lifecycle.current_guard)
        with pytest.raises(ERROR), lifecycle.shared(single, timeout=0):
            pytest.fail("Closed guard authorized a legacy borrow")
    finally:
        lifecycle._CURRENT.reset(current)
    assert calls == []
    close_successfully((single,))


def test_normal_closed_history_does_not_keep_guard_or_callback_alive(single):
    class Callback:
        def __call__(self):
            pass

    callback = Callback()
    callback_ref = weakref.ref(callback)
    with group.checked_exclusive_many((single,), check_cancelled=callback) as token:
        guard = group.guard_original(token)
        guard_ref = weakref.ref(guard)
    del guard, callback
    gc.collect()
    assert guard_ref() is None and callback_ref() is None
    assert_closed(group.consume_closed_original(token), (single,))


def test_body_first_is_the_same_exception_and_known_cleanup_does_not_poison_next_group(single):
    first = RuntimeError("synthetic body first")
    with pytest.raises(RuntimeError) as terminal:
        with group.checked_exclusive_many((single,)) as token:
            raise first
    assert terminal.value is first and lifecycle.current_guard() is None
    no_history(token)
    close_successfully((single,))


def test_cancellation_preserves_the_actual_exception_and_does_not_set_current(single):
    first = RuntimeError("synthetic acquisition cancellation")
    calls = []

    def cancel():
        calls.append(1)
        raise first

    with pytest.raises(RuntimeError) as terminal:
        with group.checked_exclusive_many((single,), check_cancelled=cancel):
            pytest.fail("Cancelled acquisition yielded")
    assert terminal.value is first and calls
    assert lifecycle.current_guard() is None
    close_successfully((single,))


@pytest.mark.parametrize("nested", ["valid_group", "invalid_group", "raw1", "raw2"])
def test_acquisition_callback_reentry_is_sticky_even_when_the_callback_swallows_it(single, nested):
    caught = []

    def callback():
        if caught:
            return
        try:
            if nested in {"valid_group", "invalid_group"}:
                with group.checked_exclusive_many((single,) if nested == "valid_group" else None):
                    pytest.fail("Reentrant G1 acquired storage")
            else:
                module = raw1 if nested == "raw1" else raw2
                module.create_raw_preservation_locked(single, operation_id=str(uuid.uuid4()))
        except ERROR as error:
            caught.append(error)

    with pytest.raises(ERROR) as terminal:
        with group.checked_exclusive_many((single,), check_cancelled=callback):
            pytest.fail("Swallowed reentry let original group succeed")
    assert caught and terminal.value is caught[0]
    assert lifecycle.current_guard() is None
    close_successfully((single,))


@pytest.mark.parametrize("outer", [raw1, raw2])
@pytest.mark.parametrize("invalid_profiles", [False, True])
def test_old_callback_to_group_routes_before_parameter_and_current_rejection(single, outer, invalid_profiles):
    caught = []

    def callback():
        if caught:
            return
        with pytest.raises(raw1.AuthorityRawPreservationError) as raised:
            with group.checked_exclusive_many(None if invalid_profiles else (single,)):
                pytest.fail("Old callback acquired a nested checked group")
        caught.append(raised.value)

    with lifecycle.exclusive(single):
        with pytest.raises(raw1.AuthorityRawPreservationError) as terminal:
            outer.create_raw_preservation_locked(single, operation_id=str(uuid.uuid4()), check_cancelled=callback)
    assert caught and terminal.value is caught[0]
    assert terminal.value.code == "AUTHORITY_RAW_PRESERVATION_REENTRANT"
    assert lifecycle.current_guard() is None


def test_original_routing_registry_pair_and_unknown_token_semantics_are_preserved():
    assert routing._coordinators() == (raw1, raw2)
    with pytest.raises(raw1.AuthorityRawPreservationError) as raised:
        routing.operation_record(object())
    assert raised.value.code == "AUTHORITY_RAW_PRESERVATION_OWNER_MISMATCH"


def test_copied_context_cannot_authorize_a_different_actual_thread(single):
    def other_thread(token):
        assert threading.get_ident() != original_thread
        reject(group.guard_original, token)
        reject(lifecycle.require_guard, single, exclusive=True)

    original_thread = threading.get_ident()
    with group.checked_exclusive_many((single,)) as token:
        context = copy_context()
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(context.run, other_thread, token).result(timeout=10)
        assert lifecycle.require_guard(single, exclusive=True) is group.guard_original(token)
    assert_closed(group.consume_closed_original(token), (single,))


def test_real_asyncio_task_cannot_borrow_inherited_guard_from_another_task(single):
    async def run():
        original_task = asyncio.current_task()

        async def other(token):
            assert asyncio.current_task() is not original_task
            reject(group.guard_original, token)
            reject(lifecycle.require_guard, single, exclusive=True)

        with group.checked_exclusive_many((single,)) as token:
            await asyncio.create_task(other(token))
            assert lifecycle.require_guard(single, exclusive=True) is group.guard_original(token)
        observed = group.consume_closed_original(token)
        assert_closed(observed, (single,))

    asyncio.run(run())


def test_closed_history_is_bound_to_the_original_live_task_then_single_use(single):
    async def run():
        with group.checked_exclusive_many((single,)) as token:
            pass

        async def other():
            reject(group.consume_closed_original, token)

        await asyncio.create_task(other())
        assert_closed(group.consume_closed_original(token), (single,))
        reject(group.consume_closed_original, token)

    asyncio.run(run())


def test_completed_task_is_not_retained_by_unconsumed_close_history(single):
    retained = []

    async def produce():
        retained.append(weakref.ref(asyncio.current_task()))
        with group.checked_exclusive_many((single,)) as token:
            pass
        return token

    token = asyncio.run(produce())
    gc.collect()
    assert retained[0]() is None
    reject(group.consume_closed_original, token)


def child_command(case, root, value=None):
    command = [sys.executable, "-I", "-B", str(HELPER), case, str(root)]
    if value is not None:
        command.append(json.dumps({field: str(getattr(value, field)) for field in FIELDS}, sort_keys=True))
    return command


@contextmanager
def legacy_holder(value, root):
    """A real separately audited process, with bounded ready/exit waits."""
    parent_pid = os.getpid()
    process = subprocess.Popen(child_command("legacy-holder", root, value),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    ready = queue.Queue()

    def read_ready():
        ready.put(process.stdout.readline())

    reader = threading.Thread(target=read_ready, daemon=True)
    reader.start()
    actual_identity = None
    try:
        line = ready.get(timeout=15)
        assert line, "Isolated legacy lock holder exited before ready"
        evidence = json.loads(line)
        assert process.poll() is None
        assert type(evidence) is dict and set(evidence) == {"state", "pid", "ppid", "exclusive"}
        assert evidence["state"] == "held" and evidence["exclusive"] is False
        child_pid, child_ppid = evidence["pid"], evidence["ppid"]
        assert type(child_pid) is type(child_ppid) is int and child_pid > 0 and child_ppid > 0
        assert child_pid != parent_pid
        # The fixed helper reports its actual interpreter identity on our own
        # inherited pipe. Windows venv python.exe can be a live launcher whose
        # direct child is that interpreter; other platforms require one level.
        assert ((child_pid == process.pid and child_ppid == parent_pid)
                or (os.name == "nt" and child_pid != process.pid and child_ppid == process.pid))
        actual_identity = {"pid": child_pid, "ppid": child_ppid}
        yield process
    finally:
        try:
            # communicate drains both owned pipes to EOF and waits for the
            # original Popen child, including the Windows venv launcher.
            output, errors = process.communicate(b"release\n", timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=15)
            pytest.fail("Isolated legacy holder failed to exit")
        reader.join(timeout=5)
        assert not reader.is_alive()
        assert process.returncode == 0, errors.decode("utf-8", "replace")
        released = json.loads(output)
        if actual_identity is not None:
            assert type(released) is dict and set(released) == {"state", "pid", "ppid"}
            assert type(released["pid"]) is type(released["ppid"]) is int
            assert released == {"state": "released", **actual_identity}


def test_real_legacy_shared_process_blocks_checked_exclusive_until_it_has_exited(single, tmp_path, monkeypatch):
    first = RuntimeError("stop waiting after observing real contention")
    busy_waits = []
    original_sleep = group._SLEEP

    def wait_after_original_busy(delay):
        # _open_root reaches this sole sleep only after the pinned original
        # FileLock.attempt returned False. It is not an elapsed-time surrogate.
        busy_waits.append(delay)
        return original_sleep(delay)

    with legacy_holder(single, tmp_path):
        def cancel():
            if busy_waits:
                raise first

        with monkeypatch.context() as patch:
            patch.setattr(group, "_SLEEP", wait_after_original_busy)
            with pytest.raises(RuntimeError) as terminal:
                with group.checked_exclusive_many((single,), check_cancelled=cancel):
                    pytest.fail("Real shared byte lock did not block G1 EX")
        assert terminal.value is first and busy_waits
    assert lifecycle.current_guard() is None
    close_successfully((single,))


def test_same_private_token_type_without_original_registry_identity_is_rejected(single):
    with group.checked_exclusive_many((single,)) as token:
        forged = object.__new__(type(token))
        assert type(forged) is type(token) and forged is not token
        reject(group.guard_original, forged)
        reject(group.consume_closed_original, forged)
        assert lifecycle.require_guard(single, exclusive=True) is group.guard_original(token)
    assert_closed(group.consume_closed_original(token), (single,))
    no_history(forged)


def test_guard_owner_field_rejects_an_equality_hook_before_it_can_run(single):
    calls = []

    class Equal:
        def __eq__(self, other):
            calls.append("owner.eq")
            return True
        def __ne__(self, other):
            calls.append("owner.ne")
            return False

    with pytest.raises(ERROR):
        with group.checked_exclusive_many((single,)) as token:
            guard = group.guard_original(token)
            guard._owner = (os.getpid(), threading.get_ident(), Equal())
            lifecycle.require_guard(single, exclusive=True)
    assert calls == [] and lifecycle.current_guard() is None
    no_history(token)


def test_an_unrelated_current_object_cannot_dispatch_duck_typed_guard_methods(single):
    calls = []

    class FalseGuard:
        def __getattr__(self, name):
            calls.append(name)
            raise AssertionError("Unrelated CURRENT object dispatched")

    with pytest.raises(ERROR):
        with group.checked_exclusive_many((single,)) as token:
            lifecycle._CURRENT.set(FalseGuard())
            group.guard_original(token)
    assert calls == [] and lifecycle.current_guard() is None
    no_history(token)
    close_successfully((single,))


@pytest.mark.parametrize("exception_type", [RuntimeError, KeyboardInterrupt, SystemExit])
def test_body_baseexception_identity_is_preserved_through_all_known_cleanup(single, exception_type):
    first = exception_type("synthetic first")
    with pytest.raises(exception_type) as raised:
        with group.checked_exclusive_many((single,)) as token:
            raise first
    assert raised.value is first and lifecycle.current_guard() is None
    no_history(token)
    close_successfully((single,))


def test_body_exception_diagnostics_do_not_call_user_string_code_or_class_hooks(single):
    calls = []

    class First(BaseException):
        @property
        def __class__(self):
            calls.append("class")
            raise AssertionError("Untrusted exception class property")
        @property
        def code(self):
            calls.append("code")
            raise AssertionError("Untrusted exception code property")
        def __str__(self):
            calls.append("str")
            raise AssertionError("Untrusted exception string")
        def __repr__(self):
            calls.append("repr")
            raise AssertionError("Untrusted exception repr")

    first = First()
    caught = None
    try:
        with group.checked_exclusive_many((single,)) as token:
            raise first
    except BaseException as error:
        caught = error
    assert caught is first and calls == []
    assert lifecycle.current_guard() is None
    no_history(token)


def test_callback_stops_before_held_body_and_never_runs_during_close(single):
    phase, seen = ["acquiring"], []

    def callback():
        seen.append(phase[0])
        assert phase[0] == "acquiring"

    with group.checked_exclusive_many((single,), check_cancelled=callback) as token:
        phase[0] = "held"
        lifecycle.require_guard(single, exclusive=True)
        phase[0] = "closing"
    assert seen and set(seen) == {"acquiring"}
    assert_closed(group.consume_closed_original(token), (single,))


def test_consume_requires_current_reset_even_when_original_owner_matches(single):
    with group.checked_exclusive_many((single,)) as token:
        guard = group.guard_original(token)
    current = lifecycle._CURRENT.set(guard)
    try:
        reject(group.consume_closed_original, token)
    finally:
        lifecycle._CURRENT.reset(current)
    assert_closed(group.consume_closed_original(token), (single,))


def test_a_closed_guard_must_still_be_inactive_when_its_history_is_consumed(single):
    with group.checked_exclusive_many((single,)) as token:
        guard = group.guard_original(token)
    guard._active = True
    reject(group.consume_closed_original, token)
    guard._active = False
    assert_closed(group.consume_closed_original(token), (single,))


def test_foreign_thread_cannot_consume_a_completed_sync_owner_history(single):
    with group.checked_exclusive_many((single,)) as token:
        pass
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(reject, group.consume_closed_original, token).result(timeout=10)
    assert_closed(group.consume_closed_original(token), (single,))


@contextmanager
def native_return_trace(monkeypatch):
    """Pass through every real acquire/transfer/close and count its return.

    This independent observer does not replace a method pin, fabricate handles,
    or alter a return. Numeric resources stay local to the test and are never
    included in a returned close observation or a child diagnostic.
    """
    active, acquired, closed, peak = {}, [0], [0], [0]
    events = []

    def acquire(kind, number):
        key = (kind, number)
        assert key not in active
        acquired[0] += 1
        active[key] = acquired[0]
        peak[0] = max(peak[0], len(active))
        events.append(("acquired", acquired[0]))

    def close(kind, number):
        key = (kind, number)
        assert key in active
        serial = active.pop(key)
        closed[0] += 1
        events.append(("closed", serial))

    def wrap(name, original):
        def observed(*args, **kwargs):
            result = original(*args, **kwargs)
            if name == "_OS_OPEN":
                acquire("fd", result)
            elif name == "_OS_CLOSE":
                close("fd", args[0])
            elif name == "_WIN_CREATE_FILEW":
                assert result not in (None, 0, lifecycle.ctypes.c_void_p(-1).value)
                acquire("handle", result)
            elif name == "_WIN_DUPLICATE_HANDLE":
                assert result
                acquire("handle", args[3]._obj.value)
            elif name == "_CRT_OPEN_OSFHANDLE":
                serial = active.pop(("handle", args[0]))
                assert ("fd", result) not in active
                active[("fd", result)] = serial
                events.append(("transferred", serial))
            else:
                assert name == "_WIN_CLOSE_HANDLE" and result
                close("handle", args[0])
            return result
        return observed

    names = ["_OS_CLOSE"]
    names += (["_WIN_CREATE_FILEW", "_WIN_DUPLICATE_HANDLE", "_CRT_OPEN_OSFHANDLE", "_WIN_CLOSE_HANDLE"]
              if os.name == "nt" else ["_OS_OPEN"])
    with monkeypatch.context() as patch:
        for name in names:
            patch.setattr(group, name, wrap(name, getattr(group, name)))
        yield {"active": active, "acquired": acquired, "closed": closed, "peak": peak, "events": events}


def test_native_return_ledger_matches_independent_acquires_transfers_and_closes(pair, monkeypatch):
    with native_return_trace(monkeypatch) as trace:
        with group.checked_exclusive_many(pair) as token:
            assert lifecycle.require_guard(pair[1], exclusive=True) is group.guard_original(token)
            assert trace["active"]
        observed = group.consume_closed_original(token)
    assert_closed(observed, pair)
    assert trace["active"] == {}
    assert observed["native_resources_acquired"] == trace["acquired"][0]
    assert observed["native_resources_normal_closed"] == trace["closed"][0]
    assert observed["peak_resources"] == trace["peak"][0]
    if os.name == "nt":
        assert any(kind == "transferred" for kind, _ in trace["events"])


@pytest.mark.parametrize("value", [-1, True, 1.5, "1"])
def test_invalid_acquisition_clock_cannot_be_coerced_to_a_valid_time(single, monkeypatch, value):
    with monkeypatch.context() as patch:
        patch.setattr(group, "_MONOTONIC_NS", lambda: value)
        reject_enter((single,))
    assert lifecycle.current_guard() is None
    close_successfully((single,))


@pytest.mark.parametrize("after", [0, 30_000_000_001])
def test_acquisition_clock_rejects_regression_or_exact_thirty_second_deadline(single, monkeypatch, after):
    ticks = iter([1, after])
    last = [after]

    def clock():
        last[0] = next(ticks, last[0])
        return last[0]

    with monkeypatch.context() as patch:
        patch.setattr(group, "_MONOTONIC_NS", clock)
        reject_enter((single,))
    assert lifecycle.current_guard() is None
    close_successfully((single,))


def test_held_group_is_not_invalidated_by_the_acquisition_only_thirty_second_limit(single, monkeypatch):
    now = [1]
    with monkeypatch.context() as patch:
        patch.setattr(group, "_MONOTONIC_NS", lambda: now[0])
        with group.checked_exclusive_many((single,)) as token:
            now[0] += 31_000_000_000
            assert lifecycle.require_guard(single, exclusive=True) is group.guard_original(token)
        observed = group.consume_closed_original(token)
    assert_closed(observed, (single,))


@pytest.mark.parametrize("kind", ["stat_exception", "fd_stat_exception", "identity_mismatch"])
def test_post_acquisition_native_identity_failure_still_closes_every_returned_resource(single, monkeypatch, kind):
    first = OSError("synthetic native identity failure")
    called = []
    name = "_OS_STAT" if kind == "stat_exception" else "_OS_FSTAT"
    original = getattr(group, name)

    def fault(*args, **kwargs):
        result = original(*args, **kwargs)
        if not called:
            called.append(True)
            if kind != "identity_mismatch":
                raise first
            fields = {name: getattr(result, name) for name in dir(result) if name.startswith("st_")}
            fields["st_ino"] += 1
            return SimpleNamespace(**fields)
        return result

    with native_return_trace(monkeypatch) as trace:
        with monkeypatch.context() as patch:
            patch.setattr(group, name, fault)
            with pytest.raises((ERROR, OSError)) as raised:
                with group.checked_exclusive_many((single,)):
                    pytest.fail("Injected native identity failure yielded")
    assert called and trace["acquired"][0] > 0
    assert trace["active"] == {} and trace["closed"] == trace["acquired"]
    if kind != "identity_mismatch":
        assert raised.value is first
    else:
        assert raised.value.code == PREFIX + "IDENTITY_CHANGED"
    close_successfully((single,))


@pytest.mark.skipif(os.name != "nt", reason="Windows HANDLE to CRT-fd transfer")
def test_known_crt_transfer_failure_keeps_the_handle_closer_and_does_not_double_close(single, monkeypatch):
    first = OSError("synthetic pre-transfer CRT failure")
    calls = []

    def failure(*args, **kwargs):
        calls.append(True)
        raise first

    with native_return_trace(monkeypatch) as trace:
        with monkeypatch.context() as patch:
            patch.setattr(group, "_CRT_OPEN_OSFHANDLE", failure)
            with pytest.raises(OSError) as raised:
                with group.checked_exclusive_many((single,)):
                    pytest.fail("Failed known transfer yielded")
    assert raised.value is first and len(calls) == 1
    assert trace["acquired"][0] >= 2
    assert trace["active"] == {} and trace["closed"] == trace["acquired"]
    assert not any(kind == "transferred" for kind, _ in trace["events"])
    close_successfully((single,))


@pytest.mark.parametrize("body_first", [False, True])
def test_unlock_failure_keeps_first_but_known_fd_closure_does_not_create_permanent_debt(single, monkeypatch, body_first):
    first = RuntimeError("synthetic original body failure")
    seen = []
    name = "_WIN_UNLOCK_FILE_EX" if os.name == "nt" else "_FLOCK"
    original = getattr(group, name)

    def false_after_original_unlock(*args, **kwargs):
        result = original(*args, **kwargs)
        if not seen:
            seen.append(True)
            # A real unlock occurred, but the reported return is injected bad.
            return 0 if os.name == "nt" else False
        return result

    with native_return_trace(monkeypatch) as trace:
        with monkeypatch.context() as patch:
            patch.setattr(group, name, false_after_original_unlock)
            with pytest.raises((ERROR, RuntimeError)) as raised:
                with group.checked_exclusive_many((single,)) as token:
                    if body_first:
                        raise first
    assert seen and lifecycle.current_guard() is None
    assert raised.value is first if body_first else raised.value.code == PREFIX + "UNLOCK_FAILED"
    assert trace["active"] == {} and trace["acquired"] == trace["closed"]
    no_history(token)
    close_successfully((single,))


def test_real_lock_attempt_fault_does_not_replace_the_pinned_original_method(single, monkeypatch):
    calls = []
    original_method = lifecycle._FileLock.attempt
    if os.name == "nt":
        original = lifecycle._kernel.LockFileEx

        def fault(*args):
            calls.append(True)
            # ERROR_INVALID_HANDLE maps to EBADF; ERROR_ACCESS_DENIED would
            # be interpreted as contention by the original EACCES branch.
            lifecycle.ctypes.set_last_error(6)
            return 0
        owner, name = lifecycle._kernel, "LockFileEx"
    else:
        import fcntl

        def fault(*args):
            calls.append(True)
            raise OSError(5, "synthetic flock I/O failure")
        owner, name = fcntl, "flock"
    with native_return_trace(monkeypatch) as trace:
        with monkeypatch.context() as patch:
            patch.setattr(owner, name, fault)
            with pytest.raises(lifecycle.StorageLifecycleError) as raised:
                with group.checked_exclusive_many((single,)):
                    pytest.fail("Native lock fault yielded")
    assert lifecycle._FileLock.attempt is original_method
    assert calls and raised.value.code == "STORAGE_IO_ERROR"
    assert trace["active"] == {} and trace["acquired"] == trace["closed"]
    close_successfully((single,))


def test_final_original_guard_check_failure_cannot_skip_exhaustive_real_close(single, monkeypatch):
    with native_return_trace(monkeypatch) as trace:
        with pytest.raises(ERROR):
            with group.checked_exclusive_many((single,)) as token:
                group.guard_original(token)._active = False
                # No explicit require_guard call: the factory's final check
                # itself must fail while cleanup still owns all the resources.
    assert trace["active"] == {} and trace["acquired"] == trace["closed"]
    assert lifecycle.current_guard() is None
    no_history(token)
    close_successfully((single,))


@pytest.mark.parametrize("case", [
    "acquire-after-return", "allocator-hostile-exception", "fd-close-raised", "fd-close-non-none",
    "fd-close-with-body-first", "fd-close-unlisted-code",
    pytest.param("handle-close-false", marks=pytest.mark.skipif(os.name != "nt", reason="Windows HANDLE closer")),
    pytest.param("transfer-after-return", marks=pytest.mark.skipif(os.name != "nt", reason="Windows CRT transfer")),
    pytest.param("duplicate-false-nonnull", marks=pytest.mark.skipif(os.name != "nt", reason="Windows DuplicateHandle")),
])
def test_unknown_native_outcomes_poison_only_their_isolated_child_and_never_retry_closers(tmp_path, case):
    root = tmp_path / case
    root.mkdir()
    process = subprocess.run(child_command(case, root), capture_output=True, timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert process.returncode == 0, process.stderr.decode("utf-8", "replace")
    observed = json.loads(process.stdout)
    assert observed == {
        "scenario": case, "fault_injected": True, "fault_calls": 1,
        "normal_history": False, "poisoned": True, "debt_retained": True,
        "followup_native_calls": 0, "same_owner_blocked": True,
        "other_thread_blocked": True, "other_task_blocked": True,
        "other_roots_blocked": True, "invalid_parameters_blocked": True,
        "task_released": True, "callback_released": True,
        "no_closer_retry": True, "current_reset": True,
        "first_preserved": True, "already_held_group_closed": True,
    }


@pytest.mark.parametrize("view_name,shadow", [
    ("directory", "_record"), ("directory", "_root"), ("directory", "_chain"),
    ("directory", "verify"), ("directory", "entry_stat"), ("directory", "__class__"),
    ("directory", "__dict__"), ("stream", "_record"), ("stream", "_holder"),
    ("stream", "fileno"), ("stream", "__class__"), ("stream", "__dict__"),
])
def test_views_cannot_supply_or_shadow_native_cleanup_responsibility(single, monkeypatch, view_name, shadow):
    calls = []
    with native_return_trace(monkeypatch) as trace:
        with pytest.raises(ERROR):
            with group.checked_exclusive_many((single,)) as token:
                view = getattr(group.guard_original(token)._locks[0], view_name)
                if shadow == "__class__":
                    class Other(type(view)):
                        pass
                    view.__class__ = Other
                elif shadow == "__dict__":
                    view.__dict__ = dict(view.__dict__)
                elif shadow in {"verify", "entry_stat", "fileno"}:
                    setattr(view, shadow, lambda *a, **k: calls.append(shadow) or 99999)
                else:
                    # These caller extras must not become a holder/record.
                    setattr(view, shadow, object())
                lifecycle.require_guard(single, exclusive=True)
    assert calls == [] and trace["active"] == {} and trace["acquired"] == trace["closed"]
    no_history(token)
    close_successfully((single,))


@pytest.mark.parametrize("object_kind", ["guard", "lock"])
def test_swapping_actual_guard_or_lock_dictionary_cannot_reassign_cleanup(single, monkeypatch, object_kind):
    with native_return_trace(monkeypatch) as trace:
        with pytest.raises(ERROR):
            with group.checked_exclusive_many((single,)) as token:
                guard = group.guard_original(token)
                target = guard if object_kind == "guard" else guard._locks[0]
                target.__dict__ = dict(target.__dict__)
                lifecycle.require_guard(single, exclusive=True)
    assert trace["active"] == {} and trace["acquired"] == trace["closed"]
    no_history(token)
    close_successfully((single,))


def test_kept_nonowning_views_do_not_keep_a_completed_actual_task_or_guard_alive(single):
    retained, references = [], {}

    async def produce():
        references["task"] = weakref.ref(asyncio.current_task())
        with group.checked_exclusive_many((single,)) as token:
            guard = group.guard_original(token)
            references["guard"] = weakref.ref(guard)
            retained.extend((guard._locks[0].directory, guard._locks[0].stream))
        return token

    token = asyncio.run(produce())
    gc.collect()
    assert retained and references["task"]() is None and references["guard"]() is None
    reject(group.consume_closed_original, token)
    reject(retained[0].verify)
    reject(retained[1].fileno)


def test_acquisition_callback_current_change_is_rejected_before_first_native_effect(single, monkeypatch):
    changed = []
    false_current = object()

    def callback():
        if not changed:
            changed.append(lifecycle._CURRENT.set(false_current))

    try:
        with native_return_trace(monkeypatch) as trace:
            reject_enter((single,), check_cancelled=callback)
        assert changed and trace["acquired"][0] == 0 and trace["active"] == {}
    finally:
        if changed:
            lifecycle._CURRENT.reset(changed[0])
    assert lifecycle.current_guard() is None
    close_successfully((single,))


@pytest.mark.parametrize("case", [
    "cleanup-bookkeeping-raised",
    pytest.param("ancillary-error-create", marks=pytest.mark.skipif(os.name != "nt", reason="Windows GetLastError after CreateFile")),
    pytest.param("ancillary-error-duplicate", marks=pytest.mark.skipif(os.name != "nt", reason="Windows GetLastError after DuplicateHandle")),
])
def test_ancillary_or_cleanup_bookkeeping_failure_never_loses_native_responsibility(tmp_path, case):
    root = tmp_path / case
    root.mkdir()
    process = subprocess.run(child_command(case, root), capture_output=True, timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert process.returncode == 0, process.stderr.decode("utf-8", "replace")
    observed = json.loads(process.stdout)
    assert observed["scenario"] == case
    assert type(observed["fault_calls"]) is int and observed["fault_calls"] in (0, 1)
    if case == "cleanup-bookkeeping-raised":
        assert observed["fault_calls"] == 1
    # Omitting GetLastError on success is also correct. If it is read, the
    # injected failure must find a registered resource and complete its close.
    assert observed["all_real_resources_closed"] is True
    assert observed["poisoned"] is False and observed["live_empty"] is True
    assert observed["first_preserved"] is True and observed["fresh_group_succeeded"] is True


@pytest.mark.parametrize("kind", ["native_exception", "path_identity_changed"])
def test_a_held_native_view_failure_remains_first_after_body_catches_it_and_restores_the_primitive(single, monkeypatch, kind):
    original_stat = group._OS_STAT
    first = OSError("synthetic held-view native error")
    reached, caught = [], []

    def fault(*args, **kwargs):
        result = original_stat(*args, **kwargs)
        reached.append(True)
        if kind == "native_exception":
            raise first
        fields = {name: getattr(result, name) for name in dir(result) if name.startswith("st_")}
        fields["st_ino"] += 1
        return SimpleNamespace(**fields)

    with native_return_trace(monkeypatch) as trace:
        with pytest.raises((ERROR, OSError)) as terminal:
            with group.checked_exclusive_many((single,)) as token:
                directory = group.guard_original(token)._locks[0].directory
                with monkeypatch.context() as patch:
                    patch.setattr(group, "_OS_STAT", fault)
                    try:
                        directory.verify()
                    except (ERROR, OSError) as error:
                        caught.append(error)
                assert reached and caught
                # Fault removed, original native primitive restored. Swallow
                # the second observation too so only the factory's final gate
                # determines whether a normal closed history can be returned.
                try:
                    directory.verify()
                except (ERROR, OSError) as error:
                    assert error is caught[0]
                else:
                    pytest.fail("Restoring a primitive erased the held scope's first failure")
    assert terminal.value is caught[0]
    if kind == "native_exception":
        assert caught[0] is first
    else:
        assert caught[0].code == PREFIX + "IDENTITY_CHANGED"
    assert trace["active"] == {} and trace["acquired"] == trace["closed"]
    no_history(token)
    close_successfully((single,))


def test_original_database_parent_path_object_cannot_change_its_canonical_projection(single, monkeypatch):
    original_path, original_text = None, None
    try:
        with native_return_trace(monkeypatch) as trace:
            with pytest.raises(ERROR):
                with group.checked_exclusive_many((single,)) as token:
                    guard = group.guard_original(token)
                    directory = next(lock.directory for lock in guard._locks if lock.directory.path == single.database.parent)
                    original_path, original_text = directory.path, str(directory.path)
                    # Python 3.12 Path keeps a mutable internal string cache.
                    # Preserve object identity while changing its canonical key.
                    object.__setattr__(original_path, "_str", str(single.database.parent.with_name("unregistered-root")))
                    assert directory.path is original_path and str(directory.path) != original_text
                    lifecycle.require_guard(single, exclusive=True)
        assert trace["active"] == {} and trace["acquired"] == trace["closed"]
        no_history(token)
    finally:
        if original_path is not None:
            object.__setattr__(original_path, "_str", original_text)
    close_successfully((single,))


@pytest.mark.parametrize("kind,key", [("guard", "_active"), ("lock", "locked")])
def test_nonexact_dictionary_keys_are_rejected_before_guard_or_lock_attribute_lookup(single, monkeypatch, kind, key):
    calls, armed = [], [False]

    class Text(str):
        def __hash__(self):
            if armed[0]:
                calls.append("hash")
                raise AssertionError("Untrusted dictionary key hash")
            return str.__hash__(self)
        def __eq__(self, other):
            if armed[0]:
                calls.append("eq")
                raise AssertionError("Untrusted dictionary key comparison")
            return str.__eq__(self, other)

    with native_return_trace(monkeypatch) as trace:
        with pytest.raises(ERROR):
            with group.checked_exclusive_many((single,)) as token:
                guard = group.guard_original(token)
                target = guard if kind == "guard" else guard._locks[0]
                dictionary = object.__getattribute__(target, "__dict__")
                original = dictionary.pop(key)
                dictionary[Text(key)] = original
                armed[0] = True
                lifecycle.require_guard(single, exclusive=True)
    assert calls == []
    assert trace["active"] == {} and trace["acquired"] == trace["closed"]
    no_history(token)
    close_successfully((single,))


@pytest.mark.parametrize("phase", ["admission", "held"])
@pytest.mark.parametrize("field", FIELDS)
def test_profile_dictionary_collision_keys_never_dispatch_before_exact_projection(single, monkeypatch, phase, field):
    calls, armed = [], [False]
    dictionary = object.__getattribute__(single, "__dict__")
    original_fields = dict(dictionary)

    class Text(str):
        def __hash__(self):
            if armed[0]:
                calls.append("hash")
                raise AssertionError("Untrusted profile key hash")
            return str.__hash__(self)
        def __eq__(self, other):
            if armed[0]:
                calls.append("eq")
                raise AssertionError("Untrusted profile key comparison")
            return str.__eq__(self, other)

    def change():
        value = dictionary.pop(field)
        dictionary[Text(field)] = value
        armed[0] = True

    try:
        with native_return_trace(monkeypatch) as trace:
            if phase == "admission":
                change()
                reject_enter((single,))
                assert trace["acquired"][0] == 0
            else:
                with pytest.raises(ERROR):
                    with group.checked_exclusive_many((single,)) as token:
                        change()
                        lifecycle.require_guard(single, exclusive=True)
                no_history(token)
        assert calls == [] and trace["active"] == {} and trace["acquired"] == trace["closed"]
    finally:
        armed[0] = False
        dictionary.clear()
        dictionary.update(original_fields)
    close_successfully((single,))


def raw_path_fault_preserving_caches(path, alternate):
    """Explicit CPython 3.12 Path fault ABI, read against stdlib 3.12.10.

    pathlib.py initializes _raw_paths independently of its cached _str/parts.
    Path(self, child) consumes the former, while str/name/parent may consume the
    latter. This test injects that inconsistent state into an actual Path.
    The product separately rejects unknown representations before comparing
    public path behavior and using its frozen strings for native access. Both
    this fault and that exact-shape gate have a current interpreter dependency;
    neither is evidence of support for another Python representation.
    """
    assert sys.implementation.name == "cpython" and sys.version_info[:2] == (3, 12)
    assert type(path) is type(Path()) and type(alternate) is type(Path())
    old_text, old_parts, old_name, old_parent = str(path), path.parts, path.name, str(path.parent)
    cache_names = ("_str", "_drv", "_root", "_tail_cached")
    caches = tuple(object.__getattribute__(path, name) for name in cache_names)
    old_raw = object.__getattribute__(path, "_raw_paths")
    assert type(old_raw) is list
    object.__setattr__(path, "_raw_paths", [str(alternate)])
    try:
        assert str(path) == old_text and path.parts == old_parts
        assert path.name == old_name and str(path.parent) == old_parent
        assert all(object.__getattribute__(path, name) is old for name, old in zip(cache_names, caches))
        # Independent evidence that a fresh child join follows the changed raw
        # source even though the ordinary string and parts projections did not.
        child = "reno-test-path-probe"
        assert str(path / child) == str(Path(str(alternate)) / child)
        assert str(path / child) != str(Path(old_text) / child)
    except BaseException:
        object.__setattr__(path, "_raw_paths", old_raw)
        raise
    return old_raw


@contextmanager
def forbid_other_synthetic_tree(monkeypatch, outside):
    """Deny a redirected path before the real OS call, retaining its count."""
    blocked = []
    outside_key = Path(path_key(outside))

    def wrapper(original):
        def observed(path, *args, **kwargs):
            if type(path) is not int:
                text = os.fsdecode(path)
                if os.name == "nt" and text.startswith("\\\\?\\"):
                    text = text[4:]
                candidate = Path(text)
                if candidate.is_absolute() and Path(path_key(candidate)).is_relative_to(outside_key):
                    blocked.append(True)
                    # No outside stat/open is issued even against a regressed
                    # implementation. OSError declares no allocator return.
                    raise OSError(5, "Independent test guard blocked path redirection")
            return original(path, *args, **kwargs)
        return observed

    with monkeypatch.context() as patch:
        names = ["_OS_STAT", "_OS_OPEN"]
        if os.name == "nt":
            names.append("_WIN_CREATE_FILEW")
        for name in names:
            patch.setattr(group, name, wrapper(getattr(group, name)))
        yield blocked


@pytest.mark.skipif(sys.implementation.name != "cpython" or sys.version_info[:2] != (3, 12),
                    reason="CPython 3.12 cache fault; other Python representations are unvalidated")
@pytest.mark.parametrize("phase", ["admission", "callback", "held"])
@pytest.mark.parametrize("field", FIELDS)
def test_profile_raw_path_change_with_preserved_caches_cannot_redirect_native_access(single, tmp_path, monkeypatch, phase, field):
    outside = tmp_path / "separate-synthetic-tree"
    outside.mkdir()
    sentinel = outside / "untouched.bin"
    sentinel.write_bytes(b"independent outside synthetic sentinel")
    before = snapshot(outside)
    target = getattr(single, field)
    alternate = outside / ("changed.db" if field == "database" else "changed-root")
    saved = []

    def change():
        if not saved:
            saved.append(raw_path_fault_preserving_caches(target, alternate))

    try:
        with native_return_trace(monkeypatch) as trace:
            with forbid_other_synthetic_tree(monkeypatch, outside) as blocked:
                if phase == "admission":
                    change()
                    reject_enter((single,))
                    assert trace["acquired"][0] == 0
                elif phase == "callback":
                    reject_enter((single,), check_cancelled=change)
                    assert saved and trace["acquired"][0] == 0
                else:
                    with pytest.raises(ERROR):
                        with group.checked_exclusive_many((single,)) as token:
                            change()
                            lifecycle.require_guard(single, exclusive=True)
                    no_history(token)
            assert blocked == []
        assert trace["active"] == {} and trace["acquired"] == trace["closed"]
    finally:
        if saved:
            object.__setattr__(target, "_raw_paths", saved[0])
    assert snapshot(outside) == before
    close_successfully((single,))


@pytest.mark.skipif(sys.implementation.name != "cpython" or sys.version_info[:2] != (3, 12),
                    reason="CPython 3.12 cache fault; other Python representations are unvalidated")
@pytest.mark.parametrize("entry", ["verify", "entry_stat"])
def test_display_path_raw_state_cannot_redirect_a_frozen_directory_or_child_observation(single, tmp_path, monkeypatch, entry):
    outside = tmp_path / "other-synthetic-root"
    outside.mkdir()
    (outside / "untouched.bin").write_bytes(b"unrelated synthetic root")
    before = snapshot(outside)
    target, original_raw = None, None
    try:
        with native_return_trace(monkeypatch) as trace:
            with forbid_other_synthetic_tree(monkeypatch, outside) as blocked:
                with pytest.raises(ERROR):
                    with group.checked_exclusive_many((single,)) as token:
                        guard = group.guard_original(token)
                        view = next(lock.directory for lock in guard._locks if lock.directory.path == single.database.parent)
                        target = view.path
                        original_raw = raw_path_fault_preserving_caches(target, outside)
                        if entry == "verify":
                            view.verify()
                        else:
                            view.entry_stat(lifecycle.LOCK_NAME)
                no_history(token)
            assert blocked == []
        assert trace["active"] == {} and trace["acquired"] == trace["closed"]
    finally:
        if target is not None and original_raw is not None:
            object.__setattr__(target, "_raw_paths", original_raw)
    assert snapshot(outside) == before
    close_successfully((single,))
