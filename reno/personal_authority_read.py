"""Owned existing-only filesystem diagnostic; never personal write authority."""
from contextlib import contextmanager
import json
import os

from . import personal_authority_files as authority_files
from . import personal_authority_read_contract as read_contract
from . import personal_authority_read_target as read_target
from . import personal_preservation_files as files
from . import storage_admin, storage_lifecycle
from .personal_authority_read_target import AuthorityReadError


def _note(primary, text):
    try:
        BaseException.add_note(primary, text)
    except BaseException:
        pass  # A diagnostic allocation failure must not replace the primary.


@contextmanager
def _preserve_primary(context):
    """Do not let a nested exit replace or suppress the original body error."""
    resource = context.__enter__()
    try:
        yield resource
    except BaseException as primary:
        try:
            context.__exit__(type(primary), primary, primary.__traceback__)
        except BaseException as cleanup:
            if cleanup is not primary:
                _note(primary, "AUTHORITY_READ_CLEANUP_FAILED")
        raise
    else:
        # A normal verification/close failure aborts the diagnostic response.
        context.__exit__(None, None, None)


def _node_pairs(items):
    value = {}
    for key, item in items:
        if type(key) is not str or key in value:
            raise AuthorityReadError("NODE_INVALID") from None
        value[key] = item
    return value


def _node_constant(_value):
    raise AuthorityReadError("NODE_INVALID") from None


def _node_identity(raw):
    if type(raw) is not bytes or len(raw) > 4096:
        raise AuthorityReadError("NODE_INVALID") from None
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_node_pairs,
                           parse_constant=_node_constant)
    except AuthorityReadError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise AuthorityReadError("NODE_INVALID") from None
    if type(value) is not dict or set(value) != {"instance_id"}:
        raise AuthorityReadError("NODE_INVALID") from None
    # Historical json.dump spacing is accepted; its UUID spelling is strict.
    return read_target.canonical_uuid(value["instance_id"], code="NODE_INVALID")


def observe(*, check_cancelled=None):
    """Synchronous worker entry: observe the one explicit server selection."""
    if check_cancelled is not None and not callable(check_cancelled):
        raise AuthorityReadError("READ_CANCEL_INVALID") from None
    callback_primary = None

    def poll():
        nonlocal callback_primary
        if callback_primary is not None:
            raise callback_primary
        if check_cancelled is not None:
            try:
                check_cancelled()
            except BaseException as primary:
                callback_primary = primary
                raise

    try:
        poll()
        profile = storage_admin.profile()
        raw = os.environ.get(read_target.ENVIRONMENT_VARIABLE)
        target = read_target.parse_target(raw, profile=profile)
        node_path = profile.database.parent / "node-identity.json"
        with _preserve_primary(storage_lifecycle.shared(profile, timeout=0, check_cancelled=poll)):
            if profile.identity != target.profile_paths:
                raise AuthorityReadError("TARGET_PROFILE_MISMATCH") from None
            files.require_private(node_path, directory=False)
            with _preserve_primary(files.FileHold(node_path, maximum=4096,
                                                 check_cancelled=poll, collect=True)) as held_node:
                if _node_identity(held_node.raw) != target.expected_node_id:
                    raise AuthorityReadError("NODE_MISMATCH") from None
                report = authority_files.status(
                    target.facility_root, timeout=0, check_cancelled=poll,
                    expected_declared_binding_sha256=target.expected_declared_binding_sha256,
                    expected_node_id=target.expected_node_id,
                    expected_profile_sha256=target.profile_sha256)
                # Status may classify an OSError-shaped callback as a source
                # error; a remembered callback failure still propagates itself.
                poll()
                snapshot = dict.copy(report) if type(report) is dict else report
                result = read_contract.project_filesystem_status(snapshot)
                result["observation_error_code"] = (
                    snapshot["code"] if result["reported_filesystem_state"] == "unavailable" else None)
                if profile.identity != target.profile_paths:
                    raise AuthorityReadError("TARGET_PROFILE_MISMATCH") from None
                files.require_private(node_path, directory=False)
                held_node.verify()
        poll()
        return result
    except BaseException as error:
        # Identity protection precedes source classification, including when a
        # callback raises one of the source error classes below.
        if callback_primary is not None:
            if error is not callback_primary:
                _note(callback_primary, "AUTHORITY_READ_CLEANUP_FAILED")
                raise callback_primary from None
            raise
        if isinstance(error, storage_lifecycle.StorageLifecycleError):
            code = error.code
            if type(code) is not str or code not in {"STORAGE_BUSY", "STORAGE_NOT_INITIALIZED"}:
                code = "STORAGE_UNAVAILABLE"
            raise AuthorityReadError(code) from None
        if isinstance(error, files.PreservationFileError):
            if type(error.code) is str and error.code == "PERSONAL_PRESERVATION_FILE_CHANGED":
                raise AuthorityReadError("NODE_CHANGED") from None
            raise AuthorityReadError("NODE_FILE_UNAVAILABLE") from None
        if isinstance(error, OSError):
            raise AuthorityReadError("FILESYSTEM_UNAVAILABLE") from None
        raise  # Unknown programmer/contract errors remain errors, never reports.
