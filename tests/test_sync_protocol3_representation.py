"""Protocol3 representation supports current17 without rewriting released bytes.

These are pure protocol checks. They neither activate global17 nor replace a
real Cloud/Home capability, authorizer, writer, HTTP or publication witness.
"""
from copy import deepcopy
import hashlib

import pytest

from reno import sync_source_continuity_status_contract as status
from reno import sync_source_readmit_contract as wire
from reno.sync_protocol import SyncError
from tests import test_sync_source_readmit_contract as old
from tests import test_sync_source_readmit_contract_v2 as readmit2
from tests import test_sync_source_dispose_contract_v2 as dispose2


def continuity_request():
    source = deepcopy(old.SOURCE)
    source["handshake"].update(protocol_version=3, migration_version=17)
    return {"kind": "source_continuity", "source": source,
            "target": deepcopy(old.TARGET)}


def request(kind):
    if kind == "source_continuity":
        return continuity_request(), status.normalize_request
    if kind == "source_readmit":
        value = readmit2.request(basis="manual")
    elif kind == "source_dispose":
        value = dispose2.request()
    else:
        value = old.register()
        value["original_request"]["source"]["handshake"].update(
            protocol_version=3, migration_version=17)
    value["source"]["handshake"].update(protocol_version=3, migration_version=17)
    return value, wire.normalize_request


@pytest.mark.parametrize("kind", ["source_continuity", "source_register", "source_dispose", "source_readmit"])
def test_current_protocol3_representation_roundtrips_without_mutating_input(kind):
    value, normalize = request(kind)
    original = deepcopy(value)
    assert normalize(value) == original == value
    detached = normalize(value)
    detached["source"]["handshake"]["protocol_version"] = 999
    assert value == original


@pytest.mark.parametrize("kind", ["source_continuity", "source_register", "source_dispose", "source_readmit"])
@pytest.mark.parametrize("protocol,migration", [(True, 17), (3.0, 17), ("3", 17), (1, 17),
                                               (4, 17), (3, 16), (3, True)])
def test_protocol3_does_not_adopt_unknown_or_pre17_peer_representations(kind, protocol, migration):
    value, normalize = request(kind)
    value["source"]["handshake"].update(protocol_version=protocol, migration_version=migration)
    # The status parser keeps the existing queue validator's public SyncError;
    # wrapped control parsers retain SourceReadmitContractError.
    with pytest.raises((wire.SourceReadmitContractError, SyncError)):
        normalize(value)


@pytest.mark.parametrize("basis", [None, "manual", "timer"])
def test_protocol3_readmit_result_binds_actual_new_request_sha(basis):
    value = readmit2.request(basis=basis)
    original = deepcopy(value)
    old_sha = wire.request_sha256(original)
    value["source"]["handshake"]["protocol_version"] = 3
    answer = readmit2.result(value)
    assert wire.normalize_readmit_result(value, answer) == answer
    assert wire.request_sha256(value) != old_sha
    old_answer = readmit2.result(original)
    with pytest.raises(wire.SourceReadmitContractError):
        wire.normalize_readmit_result(value, old_answer)


@pytest.mark.parametrize("kind", ["budget_defer", "source_fail"])
def test_protocol3_disposition_keeps_original_protocol2_body_and_receipt(kind):
    value = dispose2.request(kind)
    original_request = wire.canonical_json(value["original_request"]).encode()
    value["source"]["handshake"]["protocol_version"] = 3
    answer = dispose2.result(value)
    assert wire.normalize_dispose_result(value, answer) == answer
    assert wire.canonical_json(value["original_request"]).encode() == original_request
    assert answer["original_request_sha256"] == hashlib.sha256(original_request).hexdigest()
    assert value["original_request"]["source"]["handshake"]["protocol_version"] == 2


@pytest.mark.parametrize("basis", [None, "manual", "timer"])
def test_released_protocol2_readmit_bytes_and_sha_are_unchanged(basis):
    value = readmit2.request(basis=basis)
    answer = readmit2.result(value)
    request_bytes, result_bytes = wire.canonical_json(value).encode(), wire.canonical_json(answer).encode()
    assert wire.canonical_json(wire.normalize_request(value)).encode() == request_bytes
    assert wire.canonical_json(wire.normalize_result(value, answer)).encode() == result_bytes
    assert wire.request_sha256(value) == hashlib.sha256(request_bytes).hexdigest()


def test_protocol2_continuity_observation_remains_a_historical_representation():
    value = continuity_request()
    value["source"]["handshake"]["protocol_version"] = 2
    assert status.normalize_request(value) == value
