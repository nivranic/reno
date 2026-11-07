"""Pure projection of a filesystem report, never a live authority decision.

The caller supplies a report. This module cannot attest its origin, freshness,
or completeness. A ready facility or matching head does not establish a grant.
"""

_HEX = frozenset("0123456789abcdef")
_BASE = frozenset({"state", "filesystem_only"})
_FIELDS = {
    "absent": _BASE,
    "retained_unproven": _BASE,
    "reserved": _BASE | {"init_operation_id"},
    "unavailable": _BASE | {"code"},
    "ready": _BASE | {
        "init_operation_id", "declared_binding_sha256",
        "windows_directory_fsync_claimed", "fact_count", "total_bytes", "head_state",
    },
}


class AuthorityReadContractError(ValueError):
    def __init__(self):
        self.code = "INVALID_OBSERVATION"
        super().__init__(self.code)


def _require(valid):
    if not valid:
        raise AuthorityReadContractError() from None


def _uuid(value):
    _require(type(value) is str and len(value) == 36)
    _require(all(
        character == "-" if index in (8, 13, 18, 23) else character in _HEX
        for index, character in enumerate(value)
    ))
    _require(value != "00000000-0000-0000-0000-000000000000")


def _sha(value):
    _require(type(value) is str and len(value) == 64)
    _require(all(character in _HEX for character in value))


def _uint(value, maximum):
    _require(type(value) is int and 0 <= value <= maximum)


def _error_code(value):
    _require(type(value) is str and 0 < len(value) <= 96)
    _require(all(
        "A" <= character <= "Z" or "0" <= character <= "9" or character == "_"
        for character in value
    ))


def _snapshot(report):
    _require(type(report) is dict)
    _require(2 <= len(report) <= 8)
    items = []
    iterator = iter(dict.items(report))
    try:
        for _ in range(9):
            try:
                item = next(iterator)
            except StopIteration:
                break
            items.append(item)
    except RuntimeError:
        raise AuthorityReadContractError() from None
    _require(2 <= len(items) <= 8)
    # Inspect every captured key before hashing/comparison or keyed lookup.
    _require(all(type(key) is str for key, _ in items))
    owned = dict(items)
    _require(len(owned) == len(items))
    return owned


def project_filesystem_status(report):
    """Validate a complete files.status report and return a fresh diagnostic DTO.

    Rejected data is never quoted in the error or copied into the result.
    A FileGuard.summary alone is deliberately not a complete status report.
    """
    report = _snapshot(report)
    _require("state" in report and "filesystem_only" in report)
    state = report["state"]
    _require(type(state) is str and len(state) <= 17)
    _require(state in _FIELDS)
    _require(set(report) == _FIELDS[state])
    _require(report["filesystem_only"] is True)

    head = None
    if state in ("reserved", "ready"):
        _uuid(report["init_operation_id"])
    if state == "unavailable":
        _error_code(report["code"])
    if state == "ready":
        _sha(report["declared_binding_sha256"])
        _require(report["windows_directory_fsync_claimed"] is False)
        _uint(report["fact_count"], 64)
        _uint(report["total_bytes"], 524288)
        head = report["head_state"]
        _require(type(head) is str and head in ("missing", "matches", "stale"))

    return {
        "format_version": 1,
        "kind": "personal_authority_filesystem_diagnostic",
        "observation_scope": "filesystem_only_report",
        "reported_filesystem_state": state,
        "reported_head_state": head,
        "effective_authority_state": "unknown",
        "write_authority": "not_established",
        "handoff_authority": "not_established",
    }
