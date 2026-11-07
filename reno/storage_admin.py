"""Explicit lifecycle facility setup; no knowledge mutation or garbage collection.

Database setup and filesystem setup have separate entrypoints. A status check
must never initialize missing directories or silently upgrade a library.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path

from . import config, db, storage_lifecycle as lifecycle


def _configured_paths():
    return tuple(Path(value).absolute() for value in (
        config.db_path(), config.MEDIA, config.DATA / "generated", config.DATA / "sync-checkpoints"))


def profile(*, database=None, media=None, reports=None, checkpoints=None):
    values = (database, media, reports, checkpoints)
    if any(value is not None for value in values) and not all(value is not None for value in values):
        raise lifecycle.StorageLifecycleError("STORAGE_PROFILE_REQUIRED", 400)
    if database is None:
        database, media, reports, checkpoints = _configured_paths()
    return lifecycle.StorageProfile(*(Path(value).absolute() for value in (database, media, reports, checkpoints)))


def initialize(value):
    # Filesystem setup must not fabricate a database or bypass its migration
    # gate. Close the connection before taking a lifecycle lock.
    con = db.connect(value.database)
    con.close()
    return lifecycle.initialize(value)


def setup(value=None):
    """Explicit startup setup after schema validation; never called by reads.

    initialize_member must create new roots itself, before generic lifecycle
    setup or pipeline subdirectories can consume their freshness evidence.
    Existing roots are enrolled with unknown completeness and remain usable.
    """
    from . import storage_members
    value = value or profile()
    con = db.connect(value.database)
    con.close()
    return storage_members.initialize_member(value)


def from_connection(con, *, profile=None, media=None, reports=None, checkpoints=None):
    """Resolve an explicit caller profile without acquiring locks or opening DBs.

    A transaction may only borrow an already-held guard. Otherwise a custom
    database must supply all physical roots instead of guessing from config.
    PRAGMA database_list observes connection identity, not a stored file pointer.
    """
    held = lifecycle.current_guard()
    if con.in_transaction and held is None:
        raise lifecycle.StorageLifecycleError("STORAGE_GUARD_REQUIRED")
    name = next((row[2] for row in con.execute("PRAGMA database_list") if row[1] == "main"), "")
    if not name:
        raise lifecycle.StorageLifecycleError("STORAGE_PROFILE_MISMATCH")
    key = lambda path: os.path.normcase(os.path.abspath(os.fspath(path)))
    overrides = {"media": media, "reports": reports, "checkpoints": checkpoints}
    if profile is None and held is not None:
        # The main database selects its exact registered group member. Never
        # borrow the primary or construct guessed roots inside an active guard.
        members = [value for value in held.profiles if key(value.database) == key(name)]
        if not members:
            raise lifecycle.StorageLifecycleError("STORAGE_PROFILE_MISMATCH")
        if len(members) != 1:
            raise lifecycle.StorageLifecycleError("STORAGE_PROFILE_AMBIGUOUS")
        profile = members[0]
    if profile is None:
        if key(name) == key(config.db_path()):
            defaults = lifecycle.StorageProfile(*_configured_paths())
            profile = lifecycle.StorageProfile(Path(name).absolute(),
                *(Path(overrides[field]).absolute() if overrides[field] is not None else getattr(defaults, field)
                  for field in ("media", "reports", "checkpoints")))
        elif all(value is not None for value in overrides.values()):
            profile = lifecycle.StorageProfile(Path(name).absolute(),
                *(Path(overrides[field]).absolute() for field in ("media", "reports", "checkpoints")))
        else:
            raise lifecycle.StorageLifecycleError("STORAGE_PROFILE_REQUIRED", 400)
    if not isinstance(profile, lifecycle.StorageProfile) or key(name) != key(profile.database):
        raise lifecycle.StorageLifecycleError("STORAGE_PROFILE_MISMATCH")
    if any(value is not None and key(value) != key(getattr(profile, field)) for field, value in overrides.items()):
        raise lifecycle.StorageLifecycleError("STORAGE_PROFILE_MISMATCH")
    if held is not None:
        lifecycle.require_guard(profile)
    return profile


def status(value):
    # shared() performs only protected, read-only opens. A zero timeout still
    # attempts the lock once; a busy store does not block an operator probe.
    try:
        with lifecycle.shared(value, timeout=0) as guard:
            guard.verify()
            return {"initialized": True}
    except lifecycle.StorageLifecycleError as exc:
        if exc.code == "STORAGE_NOT_INITIALIZED":
            return {"initialized": False, "code": exc.code}
        raise


@contextmanager
def connection(value=None):
    """Hold the file lifetime before opening a DB, through caller completion."""
    value = profile() if value is None else lifecycle._profile(value)
    with lifecycle.shared(value):
        con = db.connect(value.database)
        try:
            yield con
        except BaseException:
            # A close failure must not replace the caller's cancellation or
            # transaction error. Normal close failures still surface below.
            try:
                con.close()
            except BaseException:
                pass
            raise
        else:
            con.close()
