"""Original publication bytes in real readers, preservation and copied roots.

Only isolated fixture stores are used. Large raw-I/O files test physical reader
limits; legal unselected candidates and selected records use the actual
snapshot/Cycle producers. A preservation copy never grants source admission.
"""
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import json
import os
import uuid

import pytest

from reno import source_preservation_bundle as bundles
from reno import storage_home_recovery_dependency17 as dependency
from reno import storage_lifecycle as lifecycle, storage_source_authority_roots as roots
from reno import sync_media as safe, sync_source_publication_local2 as publications
from tests.test_storage_source_authority_roots import store, write
from tests.test_sync_agent import env
from tests.test_sync_source_genesis17 import home17, live17
from tests.test_sync_source_publication_freeze17 import publication17, freeze, snapshot
from tests.test_sync_source_publication_local2 import actual, original


def body_file(store, raw):
    directory = store.data_dir / publications.PAYLOAD_FAMILY
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (hashlib.sha256(raw).hexdigest() + ".json")
    write(path, raw, raw=True)
    return path


def reader(store, stack, *, limits=None):
    return roots._Reader(store.profile, stack, limits or roots.CaptureLimits(), None)


def test_large_held_body_has_its_own_budget_and_short_commit_never_rehashes(store, monkeypatch):
    # Physical I/O only: these bytes are not passed off as a publication record.
    raw = b"large-body-io\x00" * 10_000
    assert len(raw) > 64 * 1024
    path = body_file(store, raw)
    write(store.data_dir / "first.json", {})
    write(store.data_dir / "second.json", {})
    limits = roots.CaptureLimits(max_record_bytes=16, max_total_bytes=2)
    with lifecycle.shared(store.profile), ExitStack() as stack:
        captured = reader(store, stack, limits=limits)
        directory = captured.directory(path.parent, base=store.data_dir)
        assert captured.read_publication_payload(directory, path.name) == raw
        data = captured.directory(store.data_dir, base=store.data_dir)
        assert captured.read(data, "first.json") == {}
        assert captured.payload_total == len(raw) and captured.payload_count == 1
        assert captured.total == 2

        def rehash_forbidden(*args, **kwargs):
            pytest.fail("Short writer verification rehashed an immutable held file")

        monkeypatch.setattr(captured, "_rehash", rehash_forbidden)
        captured.verify_for_commit()
        # The same body still fails the ordinary small-record limit.
        with pytest.raises(roots.AuthorityRootError, match="RECORD_LIMIT"):
            captured.read(directory, path.name, decode=False)
        # Reading the body did not create a fresh metadata budget.
        with pytest.raises(roots.AuthorityRootError, match="TOTAL_LIMIT"):
            captured.read(data, "second.json")


@pytest.mark.skipif(os.name == "nt", reason="Windows held handles deny file replacement")
def test_short_commit_rejects_actual_same_bytes_replacement_of_held_body(store):
    raw = b"original-body" * 6_000
    path = body_file(store, raw)
    with lifecycle.shared(store.profile), ExitStack() as stack:
        captured = reader(store, stack)
        directory = captured.directory(path.parent, base=store.data_dir)
        assert captured.read_publication_payload(directory, path.name) == raw
        previous = path.stat()
        replacement = path.with_name("replacement.json")
        write(replacement, raw, raw=True)
        os.utime(replacement, ns=(previous.st_atime_ns, previous.st_mtime_ns))
        replacement.replace(path)
        with pytest.raises(roots.AuthorityRootError, match="SOURCE_CHANGED") as caught:
            captured.verify_for_commit()
        assert caught.value.retriable


@pytest.mark.parametrize("initial", ["absent", "present"])
def test_complete_payload_family_pin_rejects_new_candidate_after_observation(store, initial):
    if initial == "present":
        body_file(store, b"first physical candidate")
    with lifecycle.shared(store.profile), ExitStack() as stack:
        captured = reader(store, stack)
        data = captured.directory(store.data_dir, base=store.data_dir)
        roots._capture_publication_payloads(captured, data)
        captured.verify_for_commit()
        body_file(store, b"second physical candidate")
        with pytest.raises(roots.AuthorityRootError, match="SOURCE_CHANGED") as caught:
            captured.verify_for_commit()
        assert caught.value.retriable


def manifest_entry(key, role, *, root_kind="DATA"):
    return {"root_kind": root_kind, "relative_key": key, "role": role,
            "sha256": "a" * 64, "byte_size": 100_000}


def test_bundle_roles_close_exact_body_family_and_bounded_unpublished_parts():
    filename = "a" * 64 + ".json"
    part = ".sync-" + uuid.uuid4().hex + ".part"
    key = publications.PAYLOAD_FAMILY + "/" + filename
    bundles._validate_file(manifest_entry(key, "publication_payload"))
    for prefix in (publications.PAYLOAD_DIRECTORY, publications.PAYLOAD_FAMILY):
        bundles._validate_file(manifest_entry(prefix + "/" + part, "unpublished_part"))
    invalid = [
        manifest_entry(key, "record"),
        manifest_entry(key, "publication_payload", root_kind="CHECKPOINTS"),
        manifest_entry(publications.PAYLOAD_DIRECTORY + "/v2/" + filename, "publication_payload"),
        manifest_entry(publications.PAYLOAD_DIRECTORY + "/" + filename, "publication_payload"),
        manifest_entry(publications.PAYLOAD_FAMILY + "/" + str(uuid.uuid4()) + ".json", "publication_payload"),
        manifest_entry(publications.PAYLOAD_FAMILY + "/" + part, "publication_payload"),
    ]
    for entry in invalid:
        with pytest.raises(bundles.PreservationBundleError, match="INVALID"):
            bundles._validate_file(entry)


def large_unselected_candidate(value):
    # Preparation may preserve a candidate, but must not select these changed
    # report bytes as the actual SQL source or create an admission/publication.
    candidate = deepcopy(snapshot(value))
    assert candidate["reports"]
    report = candidate["reports"][0]
    report["content"] += "\nUnselected preservation candidate.\n" * 4_000
    report["sha256"] = hashlib.sha256(report["content"].encode("utf-8")).hexdigest()
    raw, _metadata, ref = publications.normalize_payload2(candidate, value.bindings,
        recovery_generation=value.cycle.knowledge_generation,
        recovery_stream=value.cycle.knowledge_stream)
    assert len(raw) > 64 * 1024
    with value.store.reference_operation():
        publications.prepare_payload2_locked(profile=value.profile, store=value.store,
                                             raw=raw, payload_ref=ref)
    return raw, ref


@pytest.mark.parametrize("include_part", [False, True])
def test_actual_bundle_preserves_unselected_large_candidate_and_original_part_bytes(publication17, include_part):
    value = publication17
    raw, ref = large_unselected_candidate(value)
    key = publications.payload_key2(ref)
    expected = {key: ("publication_payload", raw)}
    if include_part:
        part_key = publications.PAYLOAD_FAMILY + "/.sync-" + uuid.uuid4().hex + ".part"
        part_raw = b"unfinished original candidate bytes\x00\xff" * 3_000
        write(value.store.data_dir / part_key, part_raw, raw=True)
        expected[part_key] = ("unpublished_part", part_raw)
    before = actual(value)
    assert before["entries"]["publications"] == {}
    result = bundles.create(profile=value.profile, store=value.store,
        operation_id=str(uuid.uuid4()), root_job_id=value.job["job_id"])
    manifest = json.loads(result.manifest_json)
    entries = {entry["relative_key"]: entry for entry in manifest["files"]
               if entry["root_kind"] == "DATA"}
    copies = value.store.data_dir / bundles.DIRECTORY / manifest["bundle_id"] / "files"
    for relative_key, (role, original_raw) in expected.items():
        entry = entries[relative_key]
        assert entry["role"] == role and entry["byte_size"] == len(original_raw)
        assert entry["sha256"] == hashlib.sha256(original_raw).hexdigest()
        assert (copies / bundles._copy_name(entry)).read_bytes() == original_raw
        assert (value.store.data_dir / relative_key).read_bytes() == original_raw
    assert actual(value) == before
    assert value.store.read()["pending_publication"] is None


def original_root_copies(value):
    copies = {}
    binding = value.store.read()["binding"]
    with lifecycle.shared(value.profile), ExitStack() as stack:
        captured = reader(value.store, stack)
        summary, _references = roots._capture(captured, binding)
        assert summary["presence"] == "present"
        for directory, name, _held, _before, _opened, _digest in captured.files:
            path = directory.path / name
            if path.is_relative_to(value.profile.checkpoints):
                root_kind, base = "CHECKPOINTS", value.profile.checkpoints
            else:
                root_kind, base = "DATA", value.store.data_dir
            copies[root_kind, path.relative_to(base).as_posix()] = path.read_bytes()
        captured.verify_for_commit()
    return binding, summary, copies


def test_copied_roots_use_only_original_map_and_never_fill_missing_selected_body_from_live(publication17, monkeypatch):
    value = publication17
    freeze(value)
    record = original(value)
    key = publications.payload_key2(record["payload_ref"])
    binding, expected, copies = original_root_copies(value)
    assert ("DATA", key) in copies
    assert (value.store.data_dir / key).read_bytes() == copies["DATA", key]

    def live_directory_forbidden(*args, **kwargs):
        pytest.fail("Copied-root parser opened a current live directory")

    monkeypatch.setattr(safe, "_Directory", live_directory_forbidden)
    copied = dependency._CopiesReader(value.profile, copies, lambda: None, home_kind=True)
    observed, references = roots._capture(copied, binding)
    assert observed == expected
    assert any(item["record_id"] == record["record_id"] for item in references)
    assert copied.publication_payloads[publications.payload_name2(record["payload_ref"])] == copies["DATA", key]
    missing = dict(copies)
    del missing["DATA", key]
    absent = dependency._CopiesReader(value.profile, missing, lambda: None, home_kind=True)
    with pytest.raises(roots.AuthorityRootError, match="PAYLOAD_MISSING"):
        roots._capture(absent, binding)
