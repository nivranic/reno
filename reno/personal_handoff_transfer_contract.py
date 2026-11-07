"""Pure H04 declaration diagnostics using the existing H01 canonical DAG.

The selected reservation may be historical. A matching declaration hash does
not attest a live sender, stopped source, installed target, or current grant.
No new authority record kind, wire format, runtime permit, or write is created.
"""
from __future__ import annotations

import hashlib

from . import personal_authority_contract as authority


_MAX_RECORDS = 64
_MAX_RECORD_BYTES = 32 * 1024
_MAX_TOTAL_BYTES = 512 * 1024
_HEX = frozenset("0123456789abcdef")


class HandoffTransferContractError(ValueError):
    def __init__(self):
        self.code = "INVALID_DECLARATION"
        super().__init__(self.code)


def _require(valid):
    if not valid:
        raise HandoffTransferContractError() from None


def _snapshot(raw_records):
    kind = type(raw_records)
    _require(kind is list or kind is tuple)
    _require(0 < len(raw_records) <= _MAX_RECORDS)
    # Exact built-in slicing allocates at most N+1 references even if a caller
    # list grows after admission. It does not provide atomic live observation.
    owned = tuple(raw_records[:_MAX_RECORDS + 1])
    _require(0 < len(owned) <= _MAX_RECORDS)
    # No hashing, decoding, or parser dispatch precedes the complete bytes gate.
    _require(all(type(raw) is bytes for raw in owned))
    _require(all(0 < len(raw) <= _MAX_RECORD_BYTES for raw in owned))
    _require(sum(len(raw) for raw in owned) <= _MAX_TOTAL_BYTES)
    return owned


def diagnose_handoff_transfer(raw_records, *, promotion_reservation_sha256):
    """Select one existing reservation from a closed canonical declaration DAG.

    H01 validates the original byte references, source/target facilities, UUIDs,
    predecessor lineage, epochs, handoff/fence/stop, and target contract/seal.
    Opaque final snapshot/SQL/attachment/journal hashes remain declarations.
    This function returns neither records nor any install/promotion permission.
    """
    selector = promotion_reservation_sha256
    _require(type(selector) is str and len(selector) == 64)
    _require(all(character in _HEX for character in selector))
    owned = _snapshot(raw_records)
    limits = authority.Limits(
        max_record_bytes=_MAX_RECORD_BYTES, max_records=_MAX_RECORDS,
        max_total_bytes=_MAX_TOTAL_BYTES, max_json_depth=12, max_json_nodes=512,
    )
    try:
        authority.cross_validate(owned, limits=limits)
        # Duplicate original bytes/hashes have already been rejected by H01.
        records = {
            hashlib.sha256(raw).hexdigest(): authority.decode(raw, limits=limits)
            for raw in owned
        }
    except authority.ContractError:
        raise HandoffTransferContractError() from None
    selected = records.get(selector)
    _require(selected is not None and selected["kind"] == "promotion_reservation")
    _require(all(record["domain_id"] == selected["domain_id"] for record in records.values()))
    return {
        "diagnostic_kind": "personal_handoff_transfer_declaration",
        "observation_scope": "canonical_declaration_dag",
        "declared_stage": "promotion_reservation",
        "selector_scope": "historical_or_current_declaration",
        "declaration_bindings": "consistent",
        "effective_authority_state": "unknown",
        "install_authority": "not_established",
        "promotion_authority": "not_established",
        "write_authority": "not_established",
    }
