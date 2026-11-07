"""Pure census3 declarations and byte comparison, never capture or authority."""
from functools import wraps
import hashlib
import json
import posixpath
from . import personal_authority_preservation_contract as prior
from .personal_authority_preservation_contract import (
    _DEFAULT_ITEMS, _TOP, _DIR, _FILE, _TREE_STATES, _UNRESOLVED_REASONS,
    _snapshot, _exact, _uuid, _ordered_entries, _relative, _directory_identity,
    _enum, _int, _sha, _file_pair, _context, _copies, _artifacts, _links,
    _coverage, _limits, _canonical, _json, _original, _buffer_map, _node_original,
    _component)

FORMAT_VERSION = 3
KIND = "personal_authority_preservation_capsule"
SCOPE = "personal-authority-raw-census-v3"
LAYOUT_PROFILE = "namespace3-origin1-opaque-authority-r1158"
DEFAULT_LIMITS = dict(_DEFAULT_ITEMS)
_PREFIX = prior._PREFIX
_ROOT3 = ".reno-personal-authority-namespace-v3"
LAYOUT_SOURCE_BYTES = b'{"legacy_layout_sha256":"7965e1b9d8e4b3a3336e88ec6545f4448ad6cb208f16e653cb81310b01f71aeb","modern_exact_roles":{".reno-personal-authority-namespace-v3-header.json":"namespace3_anchor_raw",".reno-personal-authority-namespace-v3-reservation.json":"namespace3_anchor_raw",".reno-personal-authority-namespace-v3/.reno-personal-authority-namespace-v3-root-seal.json":"namespace3_root_seal_raw",".reno-personal-authority-origin-v1-observation.json":"origin_observation_raw",".reno-personal-authority-origin-v1-reservation.json":"origin_reservation_raw"},"otherwise":"legacy-v2-roles-or-unclassified-raw-no-authority","selection":{"comparison":"casefold-rstrip-dot-space-with-original-safe-components","namespace_prefix":".reno-personal-authority-namespace-","node":"node-identity.json","origin_names":[".reno-personal-authority-origin-v1-reservation.json",".reno-personal-authority-origin-v1-observation.json"]}}'
LAYOUT_SOURCE_SHA256 = hashlib.sha256(LAYOUT_SOURCE_BYTES).hexdigest()
_ORIGINS = frozenset(['.reno-personal-authority-origin-v1-reservation.json', '.reno-personal-authority-origin-v1-observation.json'])
_MODERN_ROLES = {'.reno-personal-authority-origin-v1-reservation.json': 'origin_reservation_raw', '.reno-personal-authority-origin-v1-observation.json': 'origin_observation_raw', '.reno-personal-authority-namespace-v3-reservation.json': 'namespace3_anchor_raw', '.reno-personal-authority-namespace-v3-header.json': 'namespace3_anchor_raw', '.reno-personal-authority-namespace-v3/.reno-personal-authority-namespace-v3-root-seal.json': 'namespace3_root_seal_raw'}


class AuthorityPreservationContractError(ValueError):
    def __init__(self, suffix):
        self.code = "AUTHORITY_PRESERVATION3_" + suffix
        super().__init__(self.code)


def _fail(suffix):
    raise AuthorityPreservationContractError(suffix) from None


def _checked(function):
    @wraps(function)
    def checked(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except AuthorityPreservationContractError:
            raise
        except prior.AuthorityPreservationContractError as exc:
            prefix = "AUTHORITY_PRESERVATION2_"
            _fail(exc.code[len(prefix):] if type(exc.code) is str and exc.code.startswith(prefix) else "TYPE_INVALID")
        except (ValueError, TypeError, KeyError, IndexError, AttributeError,
                UnicodeError, RecursionError, OverflowError, RuntimeError):
            _fail("TYPE_INVALID")
    return checked


def _selected_top(comparison):
    return comparison == "node-identity.json" or comparison.startswith(_PREFIX) or comparison in _ORIGINS


def _role(path):
    return _MODERN_ROLES.get(path, prior._role(path))

def _tree(value, limits):
    directories, files, unresolved = (value[key] for key in ("directories", "files", "unresolved"))
    _ordered_entries(directories, "d", _DIR, limits["max_directories"], root=True)
    _ordered_entries(files, "f", _FILE, limits["max_files"])
    _ordered_entries(unresolved, "u", {"entry_id", "relative_key", "reason"}, limits["max_entries"])
    context = value["source_context"]
    if context is None and (directories or files or unresolved):
        _fail("TREE_INVALID")
    if context is not None and (not directories or directories[0]["relative_key"] != ""):
        _fail("TREE_INVALID")
    folded, children = set(), {}
    directory_ids, file_ids = set(), set()
    directories_by_path = {row["relative_key"]: row for row in directories}
    files_by_path = {row["relative_key"]: row for row in files}
    for rows in (directories, files, unresolved):
        for row in rows:
            path = row["relative_key"]
            comparison = _relative(path, root=row["entry_id"].startswith("d"))
            if comparison in folded:
                _fail("PATH_ALIAS")
            folded.add(comparison)
            if not path:
                continue
            first = comparison.split("/", 1)[0]
            if not _selected_top(first):
                _fail("TREE_INVALID")
            parent = posixpath.dirname(path)
            if parent not in directories_by_path:
                _fail("TREE_INVALID")
            children.setdefault(parent, []).append(row)
    entries = 0
    for row in directories:
        identity = _directory_identity(row["source_identity"])
        if identity in directory_ids:
            _fail("IDENTITY_ALIAS")
        directory_ids.add(identity)
        _enum(row["listing"], _TREE_STATES, "COVERAGE_INVALID")
        if type(row["children"]) is not list:
            _fail("TREE_INVALID")
        entries += len(row["children"])
        if entries > limits["max_entries"]:
            _fail("LIMIT")
        expected = [child["entry_id"] for child in sorted(children.get(row["relative_key"], []),
                    key=lambda child: posixpath.basename(child["relative_key"]).encode("utf-8"))]
        if row["children"] != expected:
            _fail("TREE_INVALID")
    source_bytes = 0
    for row in files:
        _sha(row["sha256"])
        _int(row["byte_size"], maximum=limits["max_total_bytes"], code="LIMIT")
        source_bytes += row["byte_size"]
        if source_bytes > limits["max_total_bytes"]:
            _fail("LIMIT")
        parent = directories_by_path[posixpath.dirname(row["relative_key"])]
        _directory_identity(row["source_directory_identity"])
        if row["source_directory_identity"] != parent["source_identity"]:
            _fail("IDENTITY_INVALID")
        identity = _file_pair(row["source_path_identity"], row["source_handle_identity"], row["byte_size"], limits)
        if identity in file_ids or identity in directory_ids:
            _fail("IDENTITY_ALIAS")
        file_ids.add(identity)
        if row["role"] != _role(row["relative_key"]):
            _fail("ROLE_INVALID")
    for row in unresolved:
        _enum(row["reason"], _UNRESOLVED_REASONS, "COVERAGE_INVALID")
    _context(context, files_by_path, limits)
    if context is not None and directories[0]["source_identity"] != context["profile"]["roots"]["data"]["identity"]:
        _fail("IDENTITY_INVALID")
    return file_ids, directory_ids, source_bytes

def _normalize(value, limits):
    copied = _snapshot(value, limits)
    _exact(copied, _TOP)
    if (type(copied["format_version"]) is not int or copied["format_version"] != FORMAT_VERSION
            or copied["kind"] != KIND or copied["scope"] != SCOPE
            or copied["layout_profile"] != LAYOUT_PROFILE
            or copied["layout_source_sha256"] != LAYOUT_SOURCE_SHA256):
        _fail("VERSION_INVALID")
    _uuid(copied["operation_id"])
    file_ids, directory_ids, source_size = _tree(copied, limits)
    _copies(copied, file_ids, directory_ids, limits)
    _artifacts(copied, source_size, limits)
    _links(copied, limits)
    _coverage(copied)
    return copied

@_checked
def normalize(value, *, limits=None):
    """Return detached metadata; no supplied bytes or physical proof required."""
    return _normalize(value, _limits(limits))

@_checked
def encode(value, *, limits=None):
    """Encode new capsule metadata only; never re-encode a legacy artifact."""
    return _canonical(_normalize(value, _limits(limits)))

@_checked
def decode(raw, *, limits=None):
    """Decode canonical capsule bytes into detached, untrusted declarations."""
    budget = _limits(limits)
    value = _json(raw, budget)
    normalized = _normalize(value, budget)
    if _canonical(normalized) != raw:
        _fail("JSON_NONCANONICAL")
    return normalized

@_checked
def verify_original_bytes(raw, *, expected_sha256, expected_byte_size, limits=None):
    """Compare one immutable supplied buffer; no authority or completion token."""
    _original(raw, expected_sha256, expected_byte_size, _limits(limits))

@_checked
def cross_validate_originals(value, *, source_bytes, copy_bytes, artifact_bytes, limits=None):
    """Optional all-map comparison. It does not establish historical originality.

    Large captures can verify individual buffers instead. Their actual fd reads,
    bounded semantic checks, full selection and final custody remain producer work.
    """
    budget = _limits(limits)
    document = _normalize(value, budget)
    source = _buffer_map(source_bytes, document["files"], "entry_id", budget)
    _buffer_map(copy_bytes, document["copies"], "source_entry_id", budget)
    artifacts = _buffer_map(artifact_bytes, document["artifacts"], "artifact_id", budget)
    raw_by_id = dict(source)
    raw_by_id.update(artifacts)
    sha_by_id = {row["entry_id"]: row["sha256"] for row in document["files"]}
    sha_by_id.update({row["artifact_id"]: row["sha256"] for row in document["artifacts"]})
    context = document["source_context"]
    node = context["node"] if context is not None else None
    node_id = next((row["entry_id"] for row in document["files"]
                    if node is not None and row["relative_key"] == "node-identity.json"), None)
    current_id = None
    parsed = None
    for link in document["links"]:
        from_id = link["from_id"]
        if from_id != current_id:
            # Release the previous parsed tree before decoding the next group.
            parsed = None
            parsed = _json(raw_by_id[from_id], budget, original=True)
            current_id = from_id
            if from_id == node_id:
                _node_original(parsed, node)
                node_id = None
        leaf = parsed
        for part in link["field_path"]:
            if type(part) is str:
                if type(leaf) is not dict or part not in leaf:
                    _fail("LINK_INVALID")
            elif type(leaf) is not list or part >= len(leaf):
                _fail("LINK_INVALID")
            leaf = leaf[part]
        if type(leaf) is not str or leaf != sha_by_id[link["to_id"]]:
            _fail("LINK_INVALID")
    parsed = None
    if node_id is not None:
        _node_original(_json(raw_by_id[node_id], budget, original=True), node)
