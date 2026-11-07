"""Pure lexical roles in the original raw2 package; no graph or authority proof.

A permitted key does not prove its parents, bytes, generation or history.  A
later caller must still pass the complete actual inventory and original bytes
to the existing public codecs.  This module imports no private codec helpers.
"""
from __future__ import annotations


_RESERVED = frozenset({"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    | {prefix + number for prefix in ("COM", "LPT") for number in "123456789¹²³"})
_ROOT_CONTROLS = frozenset({"request.json", "source-metadata.json", "source-projection.json", "selection.json"})
_RUN_CONTROLS = frozenset({"run-attempt.json", "capsule.json", "source-verified.json", "manifest.json", "receipt.json"})
_FIXED_DIRS = frozenset({"copy-attempts", "copy-records", "payload", "failures"})


class AuthorityReplicaPathRoleError(ValueError):
    def __init__(self, suffix):
        super().__init__("Replica package path role is not valid")
        self.code = "AUTHORITY_REPLICA_PATH_ROLE1_" + suffix


def _fail(suffix):
    raise AuthorityReplicaPathRoleError(suffix) from None


def _key(value, *, root=False):
    if type(value) is not str:
        _fail("TYPE_INVALID")
    if value == "" and root:
        return ()
    if len(value) > 32768:
        _fail("PATH_INVALID")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        _fail("PATH_INVALID")
    if (not value or len(encoded) > 32768 or "\\" in value
            or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value)):
        _fail("PATH_INVALID")
    parts = value.split("/")
    if len(parts) > 128:
        _fail("PATH_INVALID")
    for part in parts:
        if (not part or part in (".", "..") or part[-1:] in (".", " ")
                or any(char in '<>:"|?*' for char in part)
                or len(part) > 255 or len(part.encode("utf-8")) > 255
                or part.split(".", 1)[0].rstrip(" ").upper() in _RESERVED):
            _fail("PATH_INVALID")
    return tuple(parts)


def _uuid(value):
    if (len(value) != 36 or any(value[index] != "-" for index in (8, 13, 18, 23))
            or any(char not in "0123456789abcdef" for index, char in enumerate(value) if index not in (8, 13, 18, 23))
            or not any(char != "0" for char in value if char != "-")):
        _fail("ORIGIN_INVALID")


def _entry(value):
    return len(value) == 9 and value[0] == "f" and all(char in "0123456789" for char in value[1:])


def _sha(value):
    if type(value) is not str:
        _fail("TYPE_INVALID")
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        _fail("ORIGIN_INVALID")


def classify_file(relative_key):
    """Classify only the frozen original raw2 file-name grammar."""
    parts = _key(relative_key)
    if len(parts) == 1:
        if relative_key in _ROOT_CONTROLS:
            return "control"
        if relative_key == "resume-slot-02.claim":
            return "marker"
    if len(parts) < 3 or parts[0] != "runs" or parts[1] not in ("01", "02"):
        _fail("PATH_INVALID")
    if len(parts) == 3:
        leaf = parts[2]
        if leaf in _RUN_CONTROLS:
            return "control"
        if leaf.startswith(".write-") and leaf.endswith(".tmp"):
            _uuid(leaf[7:-4])
            return "retained"
    if len(parts) == 4 and parts[3].endswith(".json"):
        identifier = parts[3][:-5]
        if parts[2] in ("copy-attempts", "failures"):
            _uuid(identifier)
            return "control" if parts[2] == "copy-attempts" else "retained"
        if parts[2] == "copy-records" and _entry(identifier):
            return "control"
    if len(parts) == 5 and parts[2] == "payload" and parts[4].endswith(".raw"):
        _uuid(parts[3])
        if _entry(parts[4][:-4]):
            return "payload"
    _fail("PATH_INVALID")


def classify_directory(relative_key, *, expected_request_sha256):
    """Classify a directory name, including only the selected request anchor."""
    parts = _key(relative_key, root=True)
    _sha(expected_request_sha256)
    if not parts:
        return "root"
    if relative_key == "runs":
        return "runs"
    if relative_key == "admission-q1-sha256-" + expected_request_sha256:
        return "anchor"
    if len(parts) >= 2 and parts[0] == "runs" and parts[1] in ("01", "02"):
        if len(parts) == 2:
            return "run"
        if len(parts) == 3 and parts[2] in _FIXED_DIRS:
            return "fixed"
        if len(parts) == 4 and parts[2] == "payload":
            _uuid(parts[3])
            return "payload_copy"
    _fail("PATH_INVALID")
