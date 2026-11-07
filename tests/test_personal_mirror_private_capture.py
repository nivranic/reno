"""Personal mirror SQL TEMP is private before the first original byte arrives."""
import os
import uuid

from reno import personal_mirror_admin as admin
from reno import personal_preservation_files as files, personal_preservation_source as preservation_source
from tests.test_personal_mirror_transfer import mirror_env
from tests.test_personal_mirror_transport import sender_env


def test_public_preview_sql_temp_has_current_owner_and_private_acl_before_copy(sender_env, monkeypatch):
    env = sender_env
    env["select_home"]()
    admin.initialize(env["source_profile"])
    original = preservation_source._PersonalPrivateDirectory.initialize_file
    initialized = []

    def initialize_before_copy(temporary, path):
        assert path.stat().st_size == 0
        result = original(temporary, path)
        files.require_private(temporary.path, directory=True)
        files.require_private(path, directory=False)
        assert path.stat().st_size == 0
        if os.name != "nt":
            assert path.stat().st_uid == os.getuid()
            assert path.stat().st_mode & 0o077 == 0
        initialized.append(path.name)
        return result

    monkeypatch.setattr(preservation_source._PersonalPrivateDirectory, "initialize_file", initialize_before_copy)
    selection = admin.preview(env["source_profile"], operation_id=str(uuid.uuid4()), snapshot_id=str(uuid.uuid4()))
    assert selection["snapshot_sha256"] and "image.sqlite" in initialized
