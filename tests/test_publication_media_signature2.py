"""Different stat/fstat ctime domains must each remain fully stable.

The metadata adapter models platform differences; SQLite selection is real.
These unit tests do not assert native file, live service or backend acceptance.
"""
from contextlib import ExitStack
import json
import os
import sqlite3
import stat
from types import SimpleNamespace

import pytest

from reno import sync_source_publication_local2 as publication
from reno.sync_protocol import SyncError


@pytest.fixture
def metadata_case(tmp_path, monkeypatch):
    original_fd = 8675309
    identity = {'st_dev': 1, 'st_ino': (1 << 96) + 17, 'st_size': 5,
                'st_mtime_ns': 100, 'st_mode': stat.S_IFREG | 0o600, 'st_nlink': 1}
    state = {'path': {**identity, 'st_ctime_ns': 200},
             'fd': {**identity, 'st_ctime_ns': 300}, 'after_open': None,
             'after_first_fd': None, 'fstat_calls': 0}

    class Stream:
        def __enter__(self):
            if state['after_open'] is not None:
                state['after_open']()
            return self

        def __exit__(self, *_):
            return False

        def fileno(self):
            return original_fd

    class Directory:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def entry_stat(self, _name):
            return SimpleNamespace(**state['path'])

        def open_file(self, _name):
            return Stream()

        def verify(self):
            pass

    real_fstat = os.fstat

    def held_fstat(fd):
        if fd != original_fd:
            return real_fstat(fd)
        result = SimpleNamespace(**state['fd'])
        state['fstat_calls'] += 1
        if state['fstat_calls'] == 1 and state['after_first_fd'] is not None:
            state['after_first_fd']()
        return result

    monkeypatch.setattr(publication.safe, '_Directory', Directory)
    monkeypatch.setattr(publication.os, 'fstat', held_fstat)
    profile = SimpleNamespace(media=tmp_path / 'media')
    digest = 'a' * 64
    con = sqlite3.connect(':memory:')
    con.row_factory = sqlite3.Row
    con.execute('CREATE TABLE video_asset(video_id,content_sha256,files_json,status)')
    con.execute('INSERT INTO video_asset VALUES(?,?,?,?)',
        ('unit-video', digest, json.dumps({'video': (profile.media / 'unit.mp4').as_posix()}), 'processed'))
    envelope = {'snapshot': {'videos': [{'video_id': 'unit-video', 'media': {
        'sha256': digest, 'byte_size': 5, 'extension': 'mp4'}}]},
        'claims': [{'video_id': 'unit-video', 'origin': {'media_sha256': digest}}]}
    yield state, con, profile, envelope
    con.close()


def capture(case, stack):
    _state, con, profile, envelope = case
    return publication._actual_media2(con, profile, envelope, stack)[0]


def test_stable_different_ctime_domains_keep_both_complete_signatures(metadata_case):
    with ExitStack() as stack:
        files = capture(metadata_case, stack)
        assert files[0][3][-1] == 200 and files[0][4][-1] == 300
        assert files[0][3][1] == files[0][4][1] == (1 << 96) + 17
        publication._media_files_short(files)


@pytest.mark.parametrize('field', ['st_dev', 'st_ino', 'st_size', 'st_mtime_ns'])
def test_cross_api_file_identity_changes_are_rejected(metadata_case, field):
    metadata_case[0]['fd'][field] += 1
    with pytest.raises(SyncError) as caught:
        with ExitStack() as stack:
            capture(metadata_case, stack)
    assert caught.value.code == 'SYNC_PUBLICATION_CHANGED'


def test_path_ctime_change_during_open_is_rejected(metadata_case):
    state = metadata_case[0]
    state['after_open'] = lambda: state['path'].__setitem__('st_ctime_ns', 201)
    with pytest.raises(SyncError) as caught:
        with ExitStack() as stack:
            capture(metadata_case, stack)
    assert caught.value.code == 'SYNC_PUBLICATION_CHANGED'


def test_fd_ctime_change_after_first_held_observation_is_rejected(metadata_case):
    state = metadata_case[0]
    state['after_first_fd'] = lambda: state['fd'].__setitem__('st_ctime_ns', 301)
    with pytest.raises(SyncError) as caught:
        with ExitStack() as stack:
            capture(metadata_case, stack)
    assert caught.value.code == 'SYNC_PUBLICATION_CHANGED'


@pytest.mark.parametrize('side', ['path', 'fd'])
def test_each_ctime_domain_is_still_rechecked_at_the_tail(metadata_case, side):
    stack = ExitStack()
    files = capture(metadata_case, stack)
    metadata_case[0][side]['st_ctime_ns'] += 1
    with pytest.raises(SyncError) as caught:
        stack.close()
    assert caught.value.code == 'SYNC_PUBLICATION_CHANGED'
