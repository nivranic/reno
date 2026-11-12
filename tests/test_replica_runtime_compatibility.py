"""Ordinary storage remains usable across supported application Python versions."""

import pytest

from reno import personal_authority_replica_operation as operation
from reno import personal_authority_replica_selector as selector
from reno import storage_lifecycle as lifecycle


def test_ordinary_lifecycle_observation_needs_no_replica_representation():
    assert lifecycle.current_guard() is None


def test_unsupported_selector_representation_retains_its_explicit_error(monkeypatch):
    monkeypatch.setattr(selector, "_SUPPORTED", False)
    with pytest.raises(selector.AuthorityReplicaSelectorError) as captured:
        selector._path_runtime()
    assert captured.value.code == "AUTHORITY_REPLICA_SELECTOR1_REPRESENTATION_UNSUPPORTED"


def test_missing_dispatch_contract_cannot_grant_replica_execution():
    with pytest.raises(operation.AuthorityReplicaOperationError) as captured:
        operation._verify_source_dispatch_original(None)
    assert captured.value.code == "AUTHORITY_REPLICA_OPERATION1_RECORD_CHANGED"
