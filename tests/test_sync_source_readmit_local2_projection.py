"""Validated typed2 pending edges; public projections never convey authority."""
from copy import deepcopy
import uuid

import pytest

from reno import sync_source_authority as permanent, sync_source_readmit_local2 as local
from reno import sync_source_readmit_contract as wire
from reno.sync_protocol import SyncError
from tests import test_sync_source_authority as historical
from tests import test_sync_source_readmit_contract_v2 as readmit


def fixture(kind):
    active, records = historical.projection_fixture()
    control, old_intent = records[1][1], records[2][1]
    basis = deepcopy(control["creation_basis"])
    continuity = {"format_version": 1, "continuity_id": str(uuid.uuid4()),
        "contract_sha256": "e" * 64, "target_epoch": basis["recovery_stream"]["library_epoch"]}
    if kind == "source_readmit":
        request = readmit.request(same_epoch=False)
        request["original"]["core_sha256"] = old_intent["core_sha256"]
    else:
        request = deepcopy(old_intent["request"])
        request.update(format_version=2, continuity=deepcopy(continuity))
        request["source"]["handshake"]["migration_version"] = 17
    basis["continuity"] = continuity
    record = {"format_version": 2, "record_kind": "recovery_intent", "record_id": request["request_id"],
        "core_sha256": old_intent["core_sha256"], "request_sha256": wire.request_sha256(request),
        "request": request, "expected_before": deepcopy(old_intent["expected_before"]),
        "creation_basis": basis, "preservation_provenance": deepcopy(old_intent["preservation_basis"]),
        "resume_provenance": None}
    local.validate_record2("requests", record, active)
    return active, record


@pytest.mark.parametrize("kind", ["source_readmit", "source_dispose"])
def test_pending2_projection_has_the_exact_edge_required_by_restore_witness(kind):
    active, record = fixture(kind)
    row = permanent.public_record_projection("requests", record, state="committed", activation=active)
    edge = next(ref for ref in row["refs"] if ref["kind"] == "recovery_request")
    # These are the four comparisons performed by the real preservation
    # witness against the immutable pending recovery identity.
    assert edge == {"kind": "recovery_request", "request_id": record["request"]["request_id"],
        "request_sha256": wire.request_sha256(record["request"]), "request_kind": kind,
        "core_sha256": record["core_sha256"]}
    assert set(edge) == {"kind", "request_id", "request_sha256", "request_kind", "core_sha256"}
    assert record["request"]["lease"]["lease_token"] not in wire.canonical_json(row)
    assert active["binding"]["target_url"] not in wire.canonical_json(row)


@pytest.mark.parametrize("kind", ["source_readmit", "source_dispose"])
@pytest.mark.parametrize("change", ["request_id", "request_sha256", "request_kind", "core_sha256"])
def test_pending2_edge_is_not_emitted_for_mismatched_body_identity(kind, change):
    active, record = fixture(kind)
    if change == "request_id":
        record["request"]["request_id"] = str(uuid.uuid4())
        record["request_sha256"] = wire.digest(record["request"])
    elif change == "request_sha256":
        record["request_sha256"] = "f" * 64
    elif change == "request_kind":
        record["request"]["kind"] = "source_dispose" if kind == "source_readmit" else "source_readmit"
        record["request_sha256"] = wire.digest(record["request"])
    else:
        record["core_sha256"] = "f" * 64
    with pytest.raises(SyncError):
        permanent.public_record_projection("requests", record, state="committed", activation=active)
