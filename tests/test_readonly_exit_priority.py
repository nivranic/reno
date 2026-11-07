"""Real TEMP/SQLite producers with explicitly synthetic post-close failures.

No guard, producer identity, SQL authorizer or production module is substituted.
Fault hooks close the originally held resource before raising a test error.
"""
from pathlib import Path
import sqlite3
import sys
import tempfile

import pytest

from reno import db, personal_preservation_source as capture
from reno import storage_lifecycle as lifecycle, storage_readonly as readonly


class _FirstFailure(BaseException):
    pass


class _CleanupFault(RuntimeError):
    pass


class _RejectsNotes(_FirstFailure):
    def add_note(self, _note):
        raise _CleanupFault("synthetic diagnostic failure")


def _raise_original(error, tracebacks):
    try:
        raise error
    except BaseException:
        tracebacks.append(error.__traceback__)
        raise


def _assert_original(caught, original, tracebacks):
    assert caught is original
    assert len(tracebacks) == 1
    current = caught.__traceback__
    while current is not None and current is not tracebacks[0]:
        current = current.tb_next
    assert current is tracebacks[0]


def _source_files(profile):
    result = {}
    for suffix in ("", "-wal", "-journal"):
        path = profile.database.with_name(profile.database.name + suffix)
        result[suffix] = (path.read_bytes(), path.stat().st_mtime_ns) if path.exists() else None
    return result


@pytest.fixture
def real_profile(tmp_path, monkeypatch):
    # Use the ordinary schema initializer, not the namespace candidate fixtures.
    profile = lifecycle.StorageProfile(tmp_path / "source/reno.db", tmp_path / "media",
        tmp_path / "reports", tmp_path / "checkpoints")
    profile.database.parent.mkdir()
    db.initialize(profile.database)
    lifecycle.initialize(profile)
    private_root = tmp_path / "private-images"
    private_root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(private_root))
    before = _source_files(profile)
    yield profile, private_root
    assert _source_files(profile) == before
    assert list(private_root.iterdir()) == []


def _directory_fault(monkeypatch, image, error, events):
    directory = image._directory
    original_close = directory.__exit__

    def close_then_fail(*args):
        original_close(*args)
        assert directory.handles == []
        events.append("directory")
        raise error

    # Only the actual source directory held by this committed image is patched.
    monkeypatch.setattr(directory, "__exit__", close_then_fail)


def _private_cleanup_fault(monkeypatch, image, error, events):
    path = Path(image.connection.execute("PRAGMA database_list").fetchone()[2]).parent
    original_cleanup = readonly._PrivateDirectory.cleanup

    def cleanup_then_fail(temporary):
        original_cleanup(temporary)
        if temporary.path == path:
            assert not path.exists()
            events.append("private")
            raise error

    monkeypatch.setattr(readonly._PrivateDirectory, "cleanup", cleanup_then_fail)


def test_consumer_first_survives_real_source_directory_close_fault(real_profile, monkeypatch):
    profile, _ = real_profile
    first, traces, events = _FirstFailure("original consumer failure"), [], []
    with lifecycle.shared(profile):
        with pytest.raises(_FirstFailure) as caught:
            with readonly.committed_image_locked(profile) as image:
                _directory_fault(monkeypatch, image, _CleanupFault("directory after close"), events)
                _raise_original(first, traces)
    _assert_original(caught.value, first, traces)
    assert events == ["directory"]
    assert "READONLY_SOURCE_DIRECTORY_CLOSE_FAILED" in first.__notes__


def test_successful_consumer_still_reports_real_source_directory_close_fault(real_profile, monkeypatch):
    profile, _ = real_profile
    failure, events = _CleanupFault("directory after successful body"), []
    with lifecycle.shared(profile):
        with pytest.raises(_CleanupFault) as caught:
            with readonly.committed_image_locked(profile) as image:
                assert image.source_fingerprint["files"][""]["sha256"]
                _directory_fault(monkeypatch, image, failure, events)
    assert caught.value is failure
    assert events == ["directory"]


def test_earliest_private_cleanup_error_precedes_later_directory_close_fault(real_profile, monkeypatch):
    profile, _ = real_profile
    events = []
    with lifecycle.shared(profile):
        with pytest.raises(readonly.ReadonlyImageError) as caught:
            with readonly.committed_image_locked(profile) as image:
                _private_cleanup_fault(monkeypatch, image, _CleanupFault("private after cleanup"), events)
                _directory_fault(monkeypatch, image, _CleanupFault("later directory after close"), events)
    assert caught.value.code == "PRIVATE_IMAGE_CLEANUP_FAILED"
    assert "READONLY_SOURCE_DIRECTORY_CLOSE_FAILED" in caught.value.__notes__
    assert events == ["private", "directory"]


def test_broken_cleanup_annotation_cannot_replace_consumer_first(real_profile, monkeypatch):
    profile, _ = real_profile
    first, traces, events = _RejectsNotes("consumer with broken diagnostics"), [], []
    # Also make the built-in BaseException.add_note fail, not only the override.
    first.__notes__ = ()
    with lifecycle.shared(profile):
        with pytest.raises(_RejectsNotes) as caught:
            with readonly.committed_image_locked(profile) as image:
                _private_cleanup_fault(monkeypatch, image, _CleanupFault("private after cleanup"), events)
                _raise_original(first, traces)
    _assert_original(caught.value, first, traces)
    assert events == ["private"]


def test_sql_callback_first_survives_both_real_closed_connection_restores(real_profile):
    profile, _ = real_profile
    first, traces = _FirstFailure("original SQL callback failure"), []
    with lifecycle.shared(profile), readonly.committed_image_locked(profile) as image:
        connection = image.connection
        assert type(connection) is sqlite3.Connection
        cursor = connection.execute("SELECT key FROM meta")

        def close_and_fail():
            assert sys._getframe(1).f_code is capture._project_sql.__code__
            cursor.close()
            connection.close()
            _raise_original(first, traces)

        with pytest.raises(_FirstFailure) as caught:
            capture._project_sql(connection, image.source_fingerprint, close_and_fail)
        _assert_original(caught.value, first, traces)
        assert "PERSONAL_SQL_PROGRESS_RESTORE_FAILED" in first.__notes__
        assert "PERSONAL_SQL_AUTHORIZER_RESTORE_FAILED" in first.__notes__
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            cursor.fetchone()


class _CursorRecorder(sqlite3.Connection):
    """Record genuine SQLite cursors; every execute still calls the original IO."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.executions = []

    def execute(self, sql, parameters=()):
        cursor = super().execute(sql, parameters)
        assert type(cursor) is sqlite3.Cursor
        self.executions.append((sql, cursor))
        return cursor


def _recorded_connection(image):
    path = Path(image.connection.execute("PRAGMA database_list").fetchone()[2])
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, factory=_CursorRecorder)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA trusted_schema=OFF")
    connection.execute("BEGIN")
    return connection


def test_successful_sql_body_keeps_first_real_restore_error_and_attempts_second(real_profile):
    profile, _ = real_profile
    with lifecycle.shared(profile), readonly.committed_image_locked(profile) as image:
        connection = _recorded_connection(image)
        closed = False

        def close_after_complete_projection():
            nonlocal closed
            # Only a direct ordinary check can close; never a VM progress call.
            if (sys._getframe(1).f_code is capture._project_sql.__code__
                    and connection.executions[-1][0] == "PRAGMA foreign_key_list(meta)"):
                for _, cursor in connection.executions:
                    cursor.close()
                connection.close()
                closed = True

        try:
            with pytest.raises(sqlite3.ProgrammingError, match="closed") as caught:
                capture._project_sql(connection, image.source_fingerprint, close_after_complete_projection)
            assert closed
            assert "PERSONAL_SQL_PROGRESS_RESTORE_FAILED" in caught.value.__notes__
            assert "PERSONAL_SQL_AUTHORIZER_RESTORE_FAILED" in caught.value.__notes__
        finally:
            connection.close()


def test_successful_projection_restores_general_readonly_queries(real_profile):
    profile, _ = real_profile
    with lifecycle.shared(profile), readonly.committed_image_locked(profile) as image:
        connection = image.connection
        assert type(connection) is sqlite3.Connection
        result, meta, rows = capture._project_sql(connection, image.source_fingerprint, lambda: None)
        assert result["integrity"] == result["foreign_keys"] == "ok"
        assert meta["primary_epoch"] == "1"
        assert set(rows) >= set(capture.contract.PERSONAL_COLUMNS)
        # Knowledge rows are outside the personal projection's read allowlist.
        # This real query requires restoration of the general readonly policy.
        cursor = connection.execute("SELECT count(*) FROM main.video_asset")
        try:
            assert cursor.fetchone() == (0,)
        finally:
            cursor.close()
