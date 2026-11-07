"""Short physical commit checks on actual isolated permanent-file captures."""
from contextlib import contextmanager
import os
from types import SimpleNamespace

import pytest

from reno import storage_lifecycle as lifecycle
from reno import storage_source_authority_roots as home
from reno import storage_source_continuity_roots as cloud
from reno import sync_source_authority as authority, sync_source_continuity as continuity
from tests.test_storage_source_authority_roots import store, populated as populate_home, ledger, write  # noqa: F401
from tests.test_storage_source_continuity_roots import profile, populated as populate_cloud, empty  # noqa: F401


@pytest.fixture(params=["home", "cloud"])
def facility(request):
    if request.param == "home":
        store = request.getfixturevalue("store")
        _active, value = populate_home(store)
        return SimpleNamespace(module=home, profile=store.profile, head=store.data_dir / authority.DB_HEAD_NAME,
            record=ledger(store) / "controls" / (value["record_id"] + ".json"), ledger=ledger(store),
            options={"binding": store.read()["binding"]})
    profile = request.getfixturevalue("profile")
    _active, _request, value, projection = populate_cloud(profile)
    root = profile.database.parent / continuity.DIRECTORY
    return SimpleNamespace(module=cloud, profile=profile, head=profile.database.parent / continuity.DB_HEAD_NAME,
        record=root / "registrations" / (value["original_request_id"] + ".json"), ledger=root,
        options={"sql_projection": projection})


@contextmanager
def captured(facility, **options):
    with lifecycle.shared(facility.profile):
        with facility.module.capture_locked(facility.profile, **facility.options, **options) as result:
            assert result.summary["presence"] == "present" and not result.unknown
            yield result


def test_commit_verifier_never_reads_immutable_pins_and_exit_still_reads_all(facility, monkeypatch):
    reads, stage, immutable = [], "entry", set()
    original = os.read
    def observed(fd, count):
        data = original(fd, count)
        reads.append((stage, fd, len(data), count))
        return data
    with monkeypatch.context() as patch:
        patch.setattr(facility.module.os, "read", observed)
        with captured(facility) as result:
            immutable = {stream.fileno() for _directory, _name, stream, *_ in result._reader.files if stream is not None}
            mutable_bytes = sum(before[2] + 1 for _directory, _name, stream, before, *_ in result._reader.files
                                if stream is None)
            assert immutable and mutable_bytes
            stage = "commit"
            result.verify_for_commit()
            commit_reads = [(fd, size, limit) for when, fd, size, limit in reads if when == "commit"]
            assert not any(fd in immutable for fd, _size, _limit in commit_reads)
            # The lifecycle guard also reads its fixed small marker.
            assert sum(size for _fd, size, _limit in commit_reads) <= mutable_bytes + 4 * 1024
            stage = "exit"
        assert immutable <= {fd for when, fd, _size, _limit in reads if when == "exit"}


@pytest.mark.parametrize("damage", ["head_write", "head_same_bytes_replace", "new_member", "handle_signature"])
def test_commit_verifier_rejects_actual_changes_before_return(facility, monkeypatch, damage):
    error_type = facility.module.AuthorityRootError if facility.module is home else facility.module.ContinuityRootError
    with pytest.raises(error_type, match="SOURCE_CHANGED") as found:
        with captured(facility) as result:
            if damage == "head_write":
                write(facility.head, b"x" * facility.head.stat().st_size, raw=True)
            elif damage == "head_same_bytes_replace":
                replacement = facility.head.with_suffix(".replacement")
                write(replacement, facility.head.read_bytes(), raw=True)
                replacement.replace(facility.head)
            elif damage == "new_member":
                write(facility.record.parent / "unexpected.json", {})
            else:
                pinned = next(stream.fileno() for _directory, _name, stream, *_ in result._reader.files
                              if stream is not None)
                original = os.fstat
                def changed(fd):
                    info = original(fd)
                    if fd != pinned:
                        return info
                    value = SimpleNamespace(**{key: getattr(info, key) for key in dir(info) if key.startswith("st_")})
                    value.st_ctime_ns += 1
                    return value
                monkeypatch.setattr(facility.module.os, "fstat", changed)
            result.verify_for_commit()
            pytest.fail("The short verifier must reject before the capture exit verifier")
    assert found.value.retriable


@pytest.mark.skipif(os.name == "nt", reason="POSIX immutable pins permit actual adversarial writes/replacements")
@pytest.mark.parametrize("damage", ["same_bytes_replace", "same_size_write", "ancestor_replace"])
def test_commit_verifier_rejects_immutable_replacements_and_in_place_writes(facility, damage):
    error_type = facility.module.AuthorityRootError if facility.module is home else facility.module.ContinuityRootError
    with pytest.raises(error_type, match="SOURCE_CHANGED") as found:
        with captured(facility) as result:
            path = facility.record
            old, raw = path.stat(), path.read_bytes()
            if damage == "same_bytes_replace":
                replacement = path.with_suffix(".replacement")
                write(replacement, raw, raw=True)
                replacement.replace(path)
            elif damage == "same_size_write":
                write(path, b"x" * len(raw), raw=True)
                os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns))
            else:
                facility.ledger.rename(facility.ledger.with_name("replaced-ledger"))
                facility.ledger.mkdir(mode=0o700)
            result.verify_for_commit()
            pytest.fail("Immutable pins must retain their exact path and handle signatures")
    assert found.value.retriable


def test_commit_verifier_rehashes_mutable_bytes_even_with_unchanged_signatures(facility, monkeypatch):
    error_type = facility.module.AuthorityRootError if facility.module is home else facility.module.ContinuityRootError
    with pytest.raises(error_type, match="SOURCE_CHANGED") as found:
        with captured(facility) as result:
            target = next(before[:2] for _directory, name, stream, before, *_ in result._reader.files
                          if stream is None and name == facility.head.name)
            original = os.read
            def corrupted_read(fd, count):
                value = original(fd, count)
                info = os.fstat(fd)
                if value and (info.st_dev, info.st_ino) == target:
                    return bytes([value[0] ^ 1]) + value[1:]
                return value
            monkeypatch.setattr(facility.module.os, "read", corrupted_read)
            result.verify_for_commit()
            pytest.fail("The original mutable SHA must be checked as well as its signatures")
    assert found.value.retriable


def test_commit_verifier_mutable_growth_is_bounded_to_original_size_plus_one(facility, monkeypatch):
    error_type = facility.module.AuthorityRootError if facility.module is home else facility.module.ContinuityRootError
    reads, grown = [], False
    with pytest.raises(error_type, match="SOURCE_CHANGED"):
        with captured(facility) as result:
            target = next(before[:2] for _directory, name, stream, before, *_ in result._reader.files
                          if stream is None and name == facility.head.name)
            size, original = facility.head.stat().st_size, os.read
            def growing_read(fd, count):
                nonlocal grown
                info = os.fstat(fd)
                value = original(fd, count)
                if (info.st_dev, info.st_ino) == target:
                    reads.append(len(value))
                    if not grown:
                        with facility.head.open("ab") as writer:
                            writer.write(b"x" * 128 * 1024)
                        grown = True
                return value
            monkeypatch.setattr(facility.module.os, "read", growing_read)
            result.verify_for_commit()
            pytest.fail("Growth must stop the short verifier")
    assert grown and sum(reads) <= size + 1


@pytest.mark.parametrize("kind", ["cancel", "deadline"])
def test_commit_verifier_preserves_cancellation_and_total_deadline(facility, monkeypatch, kind):
    armed, original = False, ValueError("synthetic commit cancellation")
    def check():
        if armed and kind == "cancel":
            raise original
    error_type = (ValueError if kind == "cancel" else
                  facility.module.AuthorityRootError if facility.module is home else facility.module.ContinuityRootError)
    with pytest.raises(error_type) as found:
        with captured(facility, check_cancelled=check) as result:
            armed = True
            if kind == "deadline":
                monkeypatch.setattr(facility.module.time, "monotonic", lambda: result._reader.started + 121)
            result.verify_for_commit()
            pytest.fail("Commit verification must honor the capture's cancellation and deadline")
    if kind == "cancel":
        assert found.value is original
    else:
        assert found.value.code.endswith("DEADLINE_LIMIT")


def test_commit_verifier_cannot_be_reused_after_capture_closes(facility):
    with captured(facility) as result:
        result.verify_for_commit()
    error_type = facility.module.AuthorityRootError if facility.module is home else facility.module.ContinuityRootError
    with pytest.raises(error_type, match="CAPTURE_CLOSED"):
        result.verify_for_commit()


def test_commit_verifier_requires_the_original_live_guard_context(facility):
    from contextvars import Context
    with captured(facility) as result:
        with pytest.raises(lifecycle.StorageLifecycleError) as found:
            Context().run(result.verify_for_commit)
        assert found.value.code == "STORAGE_GUARD_REQUIRED"
        result.verify_for_commit()


@pytest.mark.parametrize("kind", ["home", "cloud"])
def test_commit_verifier_preserves_observed_absences(store, profile, kind):
    # This test needs genuinely absent facilities, independent of the populated
    # fixture used above. It never constructs an expected peer for Home absence.
    module, actual = (home, store.profile) if kind == "home" else (cloud, profile)
    context = (module.capture_absence_locked(actual) if kind == "home" else
               module.capture_locked(actual, sql_projection=empty()))
    error_type = home.AuthorityRootError if kind == "home" else cloud.ContinuityRootError
    anchor = authority.INTENT_NAME if kind == "home" else continuity.INTENT_NAME
    with lifecycle.shared(actual), pytest.raises(error_type, match="SOURCE_CHANGED"):
        with context as result:
            assert result.summary["presence"] == "absent"
            result.verify_for_commit()
            write(actual.database.parent / anchor, {})
            result.verify_for_commit()
            pytest.fail("An observed absence cannot become an actual file during commit")
