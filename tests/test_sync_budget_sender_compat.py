"""Home state v3 cannot hide P05 permanent sender authority."""
from copy import deepcopy
import uuid

import pytest

from reno import sync_no_resend as ledger
from reno.sync_protocol import SyncError
from tests.test_sync_no_resend import bundle


def state_v3(store):
    value = deepcopy(store.read())
    value.update(format_version=3, source_execution=None, budget_pending=None)
    return value


def test_v3_requires_original_sender_witness(bundle):
    profile, store, *_ = bundle
    value = state_v3(store)
    value["sender_contract"] = {"version": 1, "ledger_id": str(uuid.uuid4())}
    value["publication_media"] = {}
    with pytest.raises(SyncError) as caught:
        ledger.observe(profile, state=value)
    assert caught.value.code == "SYNC_NO_RESEND_CONTRACT_MISSING"


def test_v3_checks_exact_original_ledger_identity(bundle):
    profile, store, *_ = bundle
    store.enable_no_resend()
    value = state_v3(store)
    original = ledger.observe(profile, state=value)
    assert original["contract"]["ledger_id"] == value["sender_contract"]["ledger_id"]
    value["sender_contract"]["ledger_id"] = str(uuid.uuid4())
    with pytest.raises(SyncError) as caught:
        ledger.observe(profile, state=value)
    assert caught.value.code == "SYNC_NO_RESEND_CONTRACT_MISMATCH"
