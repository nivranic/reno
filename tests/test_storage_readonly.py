"""Isolated synthetic files; no config, network, service or production DB."""
from contextlib import contextmanager
import hashlib
import mmap
import os
from pathlib import Path
import sqlite3
import tempfile

import pytest

from reno import config, storage_lifecycle as lifecycle, storage_readonly as readonly, storage_source as source


@pytest.fixture(autouse=True)
def isolated_config_and_temp(tmp_path, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Real configuration must not be consulted")
    monkeypatch.setattr(config, "get", forbidden)
    monkeypatch.setattr(config, "local_path", forbidden)
    root = tmp_path / "private-temp"
    root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(root))
    yield root


def synthetic(root, *, schema_version="0", initialize=True):
    profile = lifecycle.StorageProfile(root / "data/reno.db", root / "media", root / "reports", root / "checkpoints")
    profile.database.parent.mkdir(parents=True)
    con = sqlite3.connect(profile.database)
    con.executescript("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT); CREATE TABLE probe(value TEXT);")
    con.execute("INSERT INTO meta VALUES('migration_version',?)", (schema_version,))
    con.execute("INSERT INTO probe VALUES('committed')")
    con.commit()
    con.close()
    if initialize:
        lifecycle.initialize(profile)
    return profile, sqlite3.connect(profile.database)


def source_files(profile):
    result = {}
    for suffix in ("", "-wal", "-shm", "-journal"):
        path = profile.database.with_name(profile.database.name + suffix)
        if not path.exists():
            result[suffix] = None
        elif os.name == "nt" and suffix == "-shm":
            # SQLite holds mandatory byte-range locks during a pending write.
            # Read-only mapping lets the TEST observe bytes without unlocking
            # SQLite or changing the implementation's no-source-SHM rule.
            with path.open("rb") as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as view:
                result[suffix] = (view[:], path.stat().st_mtime_ns)
        else:
            result[suffix] = (path.read_bytes(), path.stat().st_mtime_ns)
    return result


@contextmanager
def failure(code):
    with pytest.raises((readonly.ReadonlyImageError, lifecycle.StorageLifecycleError)) as caught:
        yield caught
    assert caught.value.code == code


@pytest.mark.parametrize("schema", ["0", "6", "10", "999"])
def test_schema_version_is_consumer_data_not_an_assumed_current_schema(tmp_path, isolated_config_and_temp, schema):
    profile, con = synthetic(tmp_path / "source", schema_version=schema)
    try:
        before = source_files(profile)
        with lifecycle.shared(profile), readonly.committed_image_locked(profile) as image:
            assert image.connection.execute("SELECT value FROM meta WHERE key='migration_version'").fetchone() == (schema,)
            private = Path(image.connection.execute("PRAGMA database_list").fetchone()[2])
            assert private.parent.parent == isolated_config_and_temp
            assert private != profile.database
            assert image.source_fingerprint["files"][""]["sha256"] == hashlib.sha256(before[""][0]).hexdigest()
        assert source_files(profile) == before
        assert list(isolated_config_and_temp.iterdir()) == []
    finally:
        con.close()


def test_wal_committed_changes_visible_without_source_shm_read_or_write(tmp_path, isolated_config_and_temp, monkeypatch):
    profile, con = synthetic(tmp_path / "source")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("UPDATE probe SET value='committed in WAL'")
    con.commit()
    before = source_files(profile)
    assert before["-wal"] is not None and before["-wal"][0]
    observed = []
    original = source.source_reader
    @contextmanager
    def spy(directory, name):
        assert not name.endswith("-shm")
        observed.append(name)
        with original(directory, name) as stream:
            yield stream
    monkeypatch.setattr(source, "source_reader", spy)
    entry_stat = readonly.safe._Directory.entry_stat
    def no_shm_stat(directory, name):
        assert not name.endswith("-shm")
        return entry_stat(directory, name)
    monkeypatch.setattr(readonly.safe._Directory, "entry_stat", no_shm_stat)
    connect = readonly.sqlite3.connect
    def private_connect(database, *args, **kwargs):
        assert str(database).startswith(isolated_config_and_temp.as_uri() + "/")
        return connect(database, *args, **kwargs)
    monkeypatch.setattr(readonly.sqlite3, "connect", private_connect)
    try:
        with lifecycle.shared(profile), readonly.committed_image_locked(profile) as image:
            assert image.connection.execute("SELECT value FROM probe").fetchall() == [("committed in WAL",)]
            assert set(observed) == {"reno.db", "reno.db-wal"}
            assert image.source_fingerprint["files"]["-wal"]["sha256"] == hashlib.sha256(before["-wal"][0]).hexdigest()
            assert source_files(profile) == before
        assert source_files(profile) == before
        assert list(isolated_config_and_temp.iterdir()) == []
    finally:
        con.close()


def test_uncommitted_wal_frames_are_not_visible_and_caller_transaction_unchanged(tmp_path, isolated_config_and_temp):
    profile, con = synthetic(tmp_path / "source")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA cache_size=1")
    con.execute("UPDATE probe SET value='new committed'")
    con.commit()
    try:
        with lifecycle.shared(profile):
            con.execute("UPDATE probe SET value='uncommitted'")
            con.executemany("INSERT INTO probe VALUES(?)", [("pending-" + "x" * 8192,)] * 32)
            assert con.in_transaction
            before = source_files(profile)
            with readonly.committed_image_locked(profile) as image:
                assert image.connection.execute("SELECT value FROM probe").fetchall() == [("new committed",)]
                assert con.in_transaction
            assert con.in_transaction
            assert source_files(profile) == before
            assert con.execute("SELECT count(*) FROM probe").fetchone()[0] == 33
            con.rollback()
        assert con.execute("SELECT value FROM probe").fetchall() == [("new committed",)]
        assert list(isolated_config_and_temp.iterdir()) == []
    finally:
        con.close()


def test_nonempty_rollback_journal_blocks_instead_of_ignoring_pending_pages(tmp_path, isolated_config_and_temp):
    profile, con = synthetic(tmp_path / "source")
    try:
        with lifecycle.shared(profile):
            con.execute("UPDATE probe SET value='pending rollback mode'")
            before = source_files(profile)
            assert before["-journal"] is not None and before["-journal"][0]
            with failure("SOURCE_ROLLBACK_JOURNAL_PRESENT") as error, readonly.committed_image_locked(profile):
                pass
            assert error.value.retriable
            assert con.in_transaction
            assert source_files(profile) == before
            assert list(isolated_config_and_temp.iterdir()) == []
            con.rollback()
    finally:
        con.close()


def test_empty_journal_is_recorded_but_never_recovered_or_modified(tmp_path):
    profile, con = synthetic(tmp_path / "source")
    journal = profile.database.with_name("reno.db-journal")
    journal.write_bytes(b"")
    try:
        before = source_files(profile)
        with lifecycle.shared(profile), readonly.committed_image_locked(profile) as image:
            assert image.source_fingerprint["files"]["-journal"]["sha256"] == hashlib.sha256(b"").hexdigest()
        assert source_files(profile) == before
    finally:
        con.close()


def test_guard_required_and_missing_facilities_are_never_created(tmp_path, isolated_config_and_temp):
    profile, con = synthetic(tmp_path / "source", initialize=False)
    try:
        before = set(profile.database.parent.iterdir())
        with failure("STORAGE_GUARD_REQUIRED"), readonly.committed_image_locked(profile):
            pass
        assert set(profile.database.parent.iterdir()) == before
        assert not profile.media.exists()
        assert list(isolated_config_and_temp.iterdir()) == []
    finally:
        con.close()


def test_foreign_profile_guard_cannot_be_reused(tmp_path):
    profile, con = synthetic(tmp_path / "first")
    other, other_con = synthetic(tmp_path / "second")
    try:
        with lifecycle.shared(profile), failure("STORAGE_PROFILE_MISMATCH"), readonly.committed_image_locked(other):
            pass
        with lifecycle.shared_many((profile, other)), readonly.committed_image_locked(other) as image:
            assert image.connection.execute("SELECT value FROM probe").fetchone()[0] == "committed"
    finally:
        other_con.close()
        con.close()


def test_combined_db_and_wal_byte_ceiling_includes_wal(tmp_path, isolated_config_and_temp):
    profile, con = synthetic(tmp_path / "source")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("UPDATE probe SET value='written to WAL'")
    con.commit()
    try:
        with lifecycle.shared(profile), failure("SOURCE_SNAPSHOT_TOO_LARGE"), readonly.committed_image_locked(profile, max_bytes=profile.database.stat().st_size):
            pass
        assert list(isolated_config_and_temp.iterdir()) == []
    finally:
        con.close()


@pytest.mark.parametrize("changes", [1, 2])
def test_one_change_retries_but_continuous_changes_are_bounded(tmp_path, isolated_config_and_temp, monkeypatch, changes):
    profile, con = synthetic(tmp_path / "source")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("UPDATE probe SET value='first committed'")
    con.commit()
    real_copy, copies = source.copy_file, []
    def changing_copy(directory, name, destination, expected):
        real_copy(directory, name, destination, expected)
        if name == profile.database.name:
            copies.append(name)
            if len(copies) <= changes:
                con.execute("UPDATE probe SET value=?", (f"commit {len(copies)}",))
                con.commit()
    monkeypatch.setattr(source, "copy_file", changing_copy)
    try:
        with lifecycle.shared(profile):
            if changes == 1:
                with readonly.committed_image_locked(profile) as image:
                    assert image.connection.execute("SELECT value FROM probe").fetchone()[0] == "commit 1"
            else:
                with failure("SOURCE_CHANGED") as caught, readonly.committed_image_locked(profile):
                    pass
                assert caught.value.retriable
        assert len(copies) == 2
        assert list(isolated_config_and_temp.iterdir()) == []
    finally:
        con.close()


def test_changed_source_after_yield_is_retryable_and_private_view_stays_frozen(tmp_path, isolated_config_and_temp):
    profile, con = synthetic(tmp_path / "source")
    con.execute("PRAGMA journal_mode=WAL")
    try:
        with lifecycle.shared(profile), failure("SOURCE_CHANGED") as caught:
            with readonly.committed_image_locked(profile) as image:
                con.execute("UPDATE probe SET value='later heartbeat-like write'")
                con.commit()
                assert image.connection.execute("SELECT value FROM probe").fetchone()[0] == "committed"
                image.verify_source()
        assert caught.value.retriable
        assert list(isolated_config_and_temp.iterdir()) == []
    finally:
        con.close()


def test_wal_disappearing_during_capture_retries_a_fresh_committed_image(tmp_path, isolated_config_and_temp, monkeypatch):
    profile, con = synthetic(tmp_path / "source")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("UPDATE probe SET value='last committed'")
    con.commit()
    real_copy, copies = source.copy_file, []
    def close_after_copy(directory, name, destination, expected):
        real_copy(directory, name, destination, expected)
        if name == profile.database.name:
            copies.append(name)
            if len(copies) == 1:
                con.close()  # SQLite checkpoints and removes its WAL.
    monkeypatch.setattr(source, "copy_file", close_after_copy)
    try:
        with lifecycle.shared(profile), readonly.committed_image_locked(profile) as image:
            assert image.connection.execute("SELECT value FROM probe").fetchone()[0] == "last committed"
            assert image.source_fingerprint["files"]["-wal"] is None
        assert len(copies) == 2
        assert list(isolated_config_and_temp.iterdir()) == []
    finally:
        con.close()


def test_context_exit_detects_source_changes_without_explicit_verify_call(tmp_path, isolated_config_and_temp):
    profile, con = synthetic(tmp_path / "source")
    con.execute("PRAGMA journal_mode=WAL")
    try:
        with lifecycle.shared(profile), failure("SOURCE_CHANGED") as caught:
            with readonly.committed_image_locked(profile):
                con.execute("UPDATE probe SET value='later change'")
                con.commit()
        assert caught.value.retriable
        assert list(isolated_config_and_temp.iterdir()) == []
    finally:
        con.close()


def test_cleanup_failure_does_not_replace_original_cancellation(tmp_path, isolated_config_and_temp, monkeypatch):
    profile, con = synthetic(tmp_path / "source")
    original = OSError("cancel this exact request")
    real_cleanup = tempfile.TemporaryDirectory.cleanup
    def cleanup_then_fail(instance):
        real_cleanup(instance)
        raise FileNotFoundError("synthetic post-cleanup failure")
    try:
        with lifecycle.shared(profile), pytest.raises(OSError) as caught:
            with readonly.committed_image_locked(profile):
                monkeypatch.setattr(tempfile.TemporaryDirectory, "cleanup", cleanup_then_fail)
                raise original
        assert caught.value is original
        assert "PRIVATE_IMAGE_CLEANUP_FAILED" in original.__notes__
        assert list(isolated_config_and_temp.iterdir()) == []
    finally:
        con.close()


@pytest.mark.parametrize("fault", ["source_missing", "wal_hardlink"])
def test_missing_or_unsafe_source_does_not_create_a_fake_image(tmp_path, isolated_config_and_temp, fault):
    profile, con = synthetic(tmp_path / "source")
    try:
        if fault == "source_missing":
            con.close()
            profile.database.unlink()
            code = "SOURCE_MISSING"
        else:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("UPDATE probe SET value='has WAL'")
            con.commit()
            os.link(profile.database.with_name("reno.db-wal"), tmp_path / "wal-hardlink")
            code = "SOURCE_UNSAFE"
        with lifecycle.shared(profile), failure(code), readonly.committed_image_locked(profile):
            pass
        assert list(isolated_config_and_temp.iterdir()) == []
    finally:
        con.close()


@pytest.mark.parametrize("phase", ["entry", "hash", "verify"])
@pytest.mark.parametrize("exception_type", [OSError, FileNotFoundError, RuntimeError])
def test_cancellation_keeps_exact_exception_object_outside_fs_error_wrapping(tmp_path, isolated_config_and_temp, monkeypatch, phase, exception_type):
    profile, con = synthetic(tmp_path / "source")
    cancellation = exception_type("synthetic cancellation")
    armed = phase == "entry"
    real_sha = readonly._private_sha
    def hash_and_arm(path, size, check):
        nonlocal armed
        if phase == "hash":
            armed = True
        return real_sha(path, size, check)
    monkeypatch.setattr(readonly, "_private_sha", hash_and_arm)
    def check():
        if armed:
            raise cancellation
    try:
        with lifecycle.shared(profile), pytest.raises(exception_type) as caught:
            with readonly.committed_image_locked(profile, check_cancelled=check) as image:
                if phase == "verify":
                    armed = True
                    image.verify_source()
        assert caught.value is cancellation
        assert list(isolated_config_and_temp.iterdir()) == []
    finally:
        con.close()


def test_private_connection_is_query_only_and_does_not_attach_an_external_database(tmp_path):
    profile, con = synthetic(tmp_path / "source")
    target = tmp_path / "must-not-create.db"
    try:
        with lifecycle.shared(profile), readonly.committed_image_locked(profile) as image:
            for query, args in [("UPDATE probe SET value='changed'", ()), ("PRAGMA query_only=OFF", ()), ("ATTACH DATABASE ? AS external", (str(target),))]:
                with pytest.raises(sqlite3.DatabaseError):
                    image.connection.execute(query, args)
            assert image.connection.execute("SELECT value FROM probe").fetchone()[0] == "committed"
            assert not con.in_transaction
        assert not target.exists()
    finally:
        con.close()


def test_fingerprint_is_detached_and_image_cannot_outlive_context(tmp_path):
    profile, con = synthetic(tmp_path / "source")
    try:
        with lifecycle.shared(profile):
            with readonly.committed_image_locked(profile) as image:
                changed = image.source_fingerprint
                changed["files"][""]["sha256"] = "bad"
                assert image.verify_source()["files"][""]["sha256"] != "bad"
            with failure("IMAGE_CLOSED"):
                image.verify_source()
            with pytest.raises(sqlite3.ProgrammingError):
                image.connection.execute("SELECT value FROM probe")
    finally:
        con.close()


def test_consumer_exception_preserved_and_cleanup_only_removes_own_tree(tmp_path, isolated_config_and_temp):
    profile, con = synthetic(tmp_path / "source")
    sentinel = isolated_config_and_temp / "unrelated"
    sentinel.mkdir()
    marker = sentinel / "keep.txt"
    marker.write_text("keep")
    original = FileNotFoundError("consumer cancelled its lookup")
    try:
        with lifecycle.shared(profile), pytest.raises(FileNotFoundError) as caught:
            with readonly.committed_image_locked(profile):
                raise original
        assert caught.value is original
        assert list(isolated_config_and_temp.iterdir()) == [sentinel]
        assert marker.read_text() == "keep"
    finally:
        con.close()


@pytest.mark.parametrize("consumer_error", [False, True])
def test_replaced_private_directory_is_not_deleted(tmp_path, isolated_config_and_temp, consumer_error):
    profile, con = synthetic(tmp_path / "source")
    original = OSError("consumer failure while private directory was replaced")
    caught_type = OSError if consumer_error else readonly.ReadonlyImageError
    try:
        before = source_files(profile)
        with lifecycle.shared(profile), pytest.raises(caught_type) as caught:
            with readonly.committed_image_locked(profile) as image:
                path = Path(image.connection.execute("PRAGMA database_list").fetchone()[2]).parent
                if os.name == "nt":
                    image.connection.close()  # Windows otherwise protects the open SQLite file.
                moved = isolated_config_and_temp / "moved-owned-image"
                path.rename(moved)
                path.mkdir()
                sentinel = path / "must-remain.txt"
                sentinel.write_text("replacement directory belongs to someone else")
                if consumer_error:
                    raise original
        if consumer_error:
            assert caught.value is original
            assert "PRIVATE_IMAGE_CLEANUP_FAILED" in original.__notes__
        else:
            assert caught.value.code == "PRIVATE_IMAGE_CLEANUP_FAILED"
        assert sentinel.read_text() == "replacement directory belongs to someone else"
        assert (moved / "image.sqlite").is_file()
        assert source_files(profile) == before
    finally:
        con.close()


def test_cleanup_verification_exception_cannot_leave_an_unsafe_automatic_finalizer(tmp_path, isolated_config_and_temp, monkeypatch):
    profile, con = synthetic(tmp_path / "source")
    captured = []
    original_init = readonly._PrivateDirectory.__init__
    def capture(instance):
        original_init(instance)
        captured.append(instance)
    monkeypatch.setattr(readonly._PrivateDirectory, "__init__", capture)
    original_resolve = Path.resolve
    try:
        with lifecycle.shared(profile), failure("PRIVATE_IMAGE_CLEANUP_FAILED"):
            with readonly.committed_image_locked(profile) as image:
                private = Path(image.connection.execute("PRAGMA database_list").fetchone()[2]).parent
                def fail_private_resolve(path, *args, **kwargs):
                    if path == private:
                        raise OSError("synthetic cleanup path lookup failure")
                    return original_resolve(path, *args, **kwargs)
                monkeypatch.setattr(Path, "resolve", fail_private_resolve)
        assert not captured[0].temporary._finalizer.alive
        captured[0].temporary._finalizer()  # Even an explicit finalizer call cannot delete the tree.
        assert (private / "image.sqlite").is_file()
    finally:
        con.close()


def test_bootstrap_schema_loading_has_a_budget_before_consumers_install_their_own(tmp_path, isolated_config_and_temp):
    profile, con = synthetic(tmp_path / "source")
    con.execute('CREATE TABLE "' + 'x' * readonly.BOOTSTRAP_MAX_BYTES + '" (value)')
    con.commit()
    try:
        before = source_files(profile)
        with lifecycle.shared(profile), failure("SOURCE_DATABASE_INVALID"), readonly.committed_image_locked(profile):
            pass
        assert source_files(profile) == before
        assert list(isolated_config_and_temp.iterdir()) == []
    finally:
        con.close()


def test_invalid_sqlite_bytes_fail_closed_without_modifying_source(tmp_path, isolated_config_and_temp):
    profile, con = synthetic(tmp_path / "source")
    con.close()
    profile.database.write_bytes(b"not a SQLite database")
    before = source_files(profile)
    with lifecycle.shared(profile), failure("SOURCE_DATABASE_INVALID"), readonly.committed_image_locked(profile):
        pass
    assert source_files(profile) == before
    assert list(isolated_config_and_temp.iterdir()) == []


@pytest.mark.parametrize("options", [{"max_bytes": True}, {"max_bytes": 0}, {"attempts": 0}, {"attempts": 3}, {"attempts": True}, {"check_cancelled": "not callable"}])
def test_options_are_bounded_before_temp_creation(tmp_path, isolated_config_and_temp, options):
    profile, con = synthetic(tmp_path / "source")
    try:
        with lifecycle.shared(profile), failure("READONLY_INVALID_ARGUMENT"), readonly.committed_image_locked(profile, **options):
            pass
        assert list(isolated_config_and_temp.iterdir()) == []
    finally:
        con.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX private mode contract")
def test_private_files_and_directory_have_private_posix_modes(tmp_path):
    profile, con = synthetic(tmp_path / "source")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("UPDATE probe SET value='WAL content'")
    con.commit()
    try:
        with lifecycle.shared(profile), readonly.committed_image_locked(profile) as image:
            path = Path(image.connection.execute("PRAGMA database_list").fetchone()[2])
            assert path.parent.stat().st_mode & 0o777 == 0o700
            assert path.stat().st_mode & 0o777 == 0o600
            for child in path.parent.iterdir():
                assert not child.stat().st_mode & 0o077
    finally:
        con.close()
