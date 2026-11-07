"""Fresh physical raw captures; malformed files never become handoff authority."""
import hashlib
import sqlite3

import pytest

from reno import personal_authority_files as primitives
from reno import personal_authority_preservation_source as prior
from reno import personal_authority_preservation_source3 as source
from reno import personal_authority_preservation3_contract as capsule
from reno import personal_authority_source_projection2 as projection
from reno import personal_preservation_files as private
from reno import storage_lifecycle as lifecycle, sync_media as safe
from tests.test_preservation_source3 import profile, root, capture, OP, LIMITS  # noqa: F401

HANDOFF = '.reno-personal-authority-namespace-handoff-v1'
SLOT = 'handoff-pending-' + 'a' * 64
RAW = {'format.json': b'\xffinvalid format original',
    SLOT + '/pending.json': b'{broken pending', SLOT + '/intent.json': b'opaque intent',
    SLOT + '/fence.json': b'opaque fence', SLOT + '/stop.json': b'opaque stop',
    SLOT + '/unknown.stage': b'raw unknown stage must survive'}


def handoff(profile, *, name=HANDOFF, lock=b'\0', old_lock=False):
    data = profile.database.parent
    with safe._Directory(data, base=data) as parent:
        private.mkdir_child(parent, name)
    path = data / name
    with safe._Directory(path, base=data) as directory:
        if lock is not None:
            private.publish(directory, 'handoff.lock', lock)
        if old_lock:
            private.publish(directory, 'authority.lock', b'\0')
        private.mkdir_child(directory, SLOT)
        private.mkdir_child(directory, 'empty')
    for relative, raw in RAW.items():
        (path / relative).write_bytes(raw)
    return path


def test_exact_handoff_captures_all_opaque_originals_under_original_wire_formats(profile, monkeypatch):
    path = handoff(profile)
    def no_sql(*_args, **_options):
        pytest.fail('Raw capture must not construct SQL state or handoff authority')
    monkeypatch.setattr(sqlite3, 'connect', no_sql)
    originals = {HANDOFF + '/' + key: raw for key, raw in RAW.items()}
    originals[HANDOFF + '/handoff.lock'] = b'\0'
    with capture(profile) as held:
        document = source.source_metadata(held)
        value = document['capsule']
        assert document['format_version'] == 2 and value['format_version'] == 3
        assert value['layout_source_sha256'] == capsule.LAYOUT_SOURCE_SHA256
        assert value['coverage'] == {'declared_tree': 'complete', 'declared_copies': 'none', 'reason': None}
        assert {row['relative_key'] for row in value['files']} == set(originals)
        assert HANDOFF + '/empty' in {row['relative_key'] for row in value['directories']}
        assert {row['role'] for row in value['files']} == {'unclassified_authority_raw'}
        buffers = {row['entry_id']: originals[row['relative_key']] for row in value['files']}
        capsule.cross_validate_originals(value, source_bytes=buffers, copy_bytes={}, artifact_bytes={})
        assert capsule.decode(capsule.encode(value)) == value
        projection.cross_validate_capsule(document['source_projection'], value)
        for row in value['files']:
            raw = originals[row['relative_key']]
            assert row['sha256'] == hashlib.sha256(raw).hexdigest() and row['byte_size'] == len(raw)
            assert row['source_handle_identity']['inode'] == str((profile.database.parent / row['relative_key']).stat().st_ino)
        source.verify_source(held)
    closed = source.closed_observation(held)
    assert closed['source_file_count'] == len(originals) and closed['source_byte_size'] == sum(map(len, originals.values()))
    assert 0 < closed['read_bytes'] <= LIMITS.max_read_bytes
    assert not (path / 'authority.lock').exists() and not profile.database.exists()


@pytest.mark.parametrize('lock', [None, b'x'])
def test_missing_or_bad_handoff_lock_cannot_yield_or_create_old_lock(profile, lock):
    path = handoff(profile, lock=lock)
    with pytest.raises(source.AuthorityRawCaptureError):
        with capture(profile):
            pytest.fail('No capture without the actual required valid lock')
    assert not (path / 'authority.lock').exists()
    if lock is None:
        assert not (path / 'handoff.lock').exists()


def test_old_authority_lock_does_not_substitute_for_handoff_lock(profile):
    path = handoff(profile, lock=None, old_lock=True)
    with pytest.raises(source.AuthorityRawCaptureError):
        with capture(profile):
            pytest.fail('Old lock cannot grant new root capture')
    assert not (path / 'handoff.lock').exists()


def test_name_alias_retains_old_rule_and_is_rejected_when_old_lock_is_missing(profile):
    path = handoff(profile, name=HANDOFF.upper())
    with pytest.raises(source.AuthorityRawCaptureError):
        with capture(profile):
            pytest.fail('Name alias only has handoff.lock and lacks original required authority.lock')
    assert not (path / 'authority.lock').exists()


def test_legacy_capture_does_not_gain_new_named_lock_rule(profile):
    handoff(profile)
    with lifecycle.exclusive(profile, timeout=0):
        with pytest.raises(prior.AuthorityRawCaptureError):
            with prior.capture_raw_locked(profile, operation_id=OP, limits=LIMITS):
                pytest.fail('Original capture still requires original authority.lock')


def test_busy_actual_handoff_lock_then_new_fresh_capture(profile):
    path = handoff(profile)
    with safe._Directory(path, base=profile.database.parent) as directory:
        with primitives._ExistingLock(directory, exclusive=True, timeout=0,
                check=lambda: None, lock_name='handoff.lock'):
            with pytest.raises(source.AuthorityRawCaptureError):
                with capture(profile):
                    pytest.fail('Cannot yield while another lock acquisition holds EX')
    with capture(profile) as held:
        assert source.source_metadata(held)['capsule']['coverage']['declared_tree'] == 'complete'
    assert source.closed_observation(held)['source_file_count'] == len(RAW) + 1


def test_mixed_namespace3_legacy_and_handoff_keep_each_original_lock_rule(profile):
    handoff(profile)
    modern = root(profile)
    legacy = root(profile, name='.reno-personal-authority-namespace-v2', lock=b'\0')
    with capture(profile) as held:
        document = source.source_metadata(held)
        paths = {row['relative_key'] for row in document['capsule']['files']}
        assert HANDOFF + '/handoff.lock' in paths
        assert '.reno-personal-authority-namespace-v2/authority.lock' in paths
        projection.cross_validate_capsule(document['source_projection'], document['capsule'])
    assert not (modern / 'authority.lock').exists() and (legacy / 'authority.lock').read_bytes() == b'\0'


def test_new_handoff_child_prevents_closed_success(profile):
    path = handoff(profile)
    with pytest.raises(source.AuthorityRawCaptureError):
        with capture(profile) as held:
            (path / 'late.raw').write_bytes(b'new raw source after captured listing')
    with pytest.raises(source.AuthorityRawCaptureError):
        source.closed_observation(held)


def test_actual_named_lock_reads_share_original_read_budget(profile):
    path = handoff(profile)
    with lifecycle.exclusive(profile, timeout=0):
        with pytest.raises(source.AuthorityRawCaptureError) as error:
            with source.capture_raw3_locked(profile, operation_id=OP,
                    limits=source.RawCaptureLimits(max_read_bytes=1, deadline_seconds=12)):
                pytest.fail('Named lock must not escape read accounting')
    assert error.value.code == 'AUTHORITY_RAW_CAPTURE_READ_LIMIT'
    assert (path / 'handoff.lock').read_bytes() == b'\0'


def test_shared_profile_guard_cannot_authorize_handoff_capture(profile):
    handoff(profile)
    with lifecycle.shared(profile, timeout=0):
        with pytest.raises(lifecycle.StorageLifecycleError):
            with source.capture_raw3_locked(profile, operation_id=OP, limits=LIMITS):
                pytest.fail('New root does not upgrade SH to EX')


def test_cancellation_after_actual_capture_keeps_first_and_denies_closed_success(profile):
    handoff(profile)
    cancel = [False]
    first = RuntimeError('synthetic requested cancellation')
    def callback():
        if cancel[0]:
            raise first
    with pytest.raises(RuntimeError) as error:
        with capture(profile, check_cancelled=callback) as held:
            cancel[0] = True
            source.verify_source(held)
    assert error.value is first
    with pytest.raises(source.AuthorityRawCaptureError):
        source.closed_observation(held)


def test_name_alias_with_original_valid_authority_lock_keeps_original_capture_rule(profile):
    handoff(profile, name=HANDOFF.upper(), old_lock=True)
    with capture(profile) as held:
        value = source.source_metadata(held)['capsule']
        assert value['coverage']['declared_tree'] == 'complete'
        assert HANDOFF.upper() + '/authority.lock' in {row['relative_key'] for row in value['files']}
        assert {row['role'] for row in value['files']} == {'unclassified_authority_raw'}
    assert source.closed_observation(held)['source_file_count'] == len(RAW) + 2


def test_metered_lock_names_reject_nonexact_strings_before_custom_comparison():
    class StringSubclass(str):
        pass
    class UnexpectedComparison:
        def __eq__(self, other):
            raise AssertionError('Invalid name must be rejected without custom comparison')
    budget = prior._Budget(prior._validated_options(LIMITS), None)
    for name in [StringSubclass('authority.lock'), b'authority.lock', UnexpectedComparison(),
                 '../handoff.lock', 'HANDOFF.LOCK']:
        with pytest.raises(primitives.FilePrimitiveError) as error:
            prior._MeteredLock(None, budget, lock_name=name)
        assert error.value.code == 'FILES_LOCK_OPTIONS_INVALID'
