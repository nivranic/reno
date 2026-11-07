"""Exercise installed primitives, without SQL enrollment or primary grants.

The isolated probes cover canonical lineage and actual native file lifetimes.
They are subprocesses because network/config refusal and filesystem corruption
must never leak into other test modules.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import uuid

import pytest

from reno import personal_authority_contract as contract


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("probe, count_key, count", [
    ("personal_authority_contract_probe.py", "checks", 211),
    ("personal_authority_files_probe.py", "check_groups", 18),
])
def test_installed_authority_primitives(probe, count_key, count):
    process = subprocess.run(
        [sys.executable, "-u", "-B", str(ROOT / "tests" / probe)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        timeout=180,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert process.returncode == 0, process.stdout + "\n" + process.stderr
    result = json.loads(process.stdout.splitlines()[-1])
    assert result["module_integrated"] is True
    assert result[count_key] == count
    if count_key == "checks":
        assert result["runtime_proofs_examined"] is False
    else:
        assert result["runtime_authority_granted"] is False
        assert result["source_files_unchanged"] is True
        assert result["real_defaults_used"] is False


@pytest.mark.parametrize("order", ["lifecycle-first", "authority-first"])
def test_installed_authority_and_lifecycle_locks_hold_and_release_independently(order):
    process = subprocess.run(
        [sys.executable, "-u", "-B", str(ROOT / "tests/personal_authority_lock_interop_probe.py"), order],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=180,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert process.returncode == 0, process.stdout + "\n" + process.stderr
    result = json.loads(process.stdout.splitlines()[-1])
    assert result["module_integrated"] is True and result["order"] == order
    assert result["actual_mixed_lock_semantics"] is True
    assert result["all_after_release_children_acquired"] is True
    assert result["observations"] == ["both_original_guards_held_actual_children_denied",
                                      "authority_release_preserves_actual_lifecycle_lock"]
    assert result["sql_attempts"] == result["network_attempts"] == 0
    assert result["source_files_unchanged"] is True
    assert result["runtime_authority_granted"] is False


def _closed_handoff_prefix():
    """Synthetic declarations only; no SQL/runtime proof is manufactured."""
    uid = lambda number: str(uuid.UUID(int=number))
    sha = lambda text: hashlib.sha256(text.encode()).hexdigest()
    domain = uid(900)
    source = {"facility_id": uid(101), "node_id": uid(11), "profile_sha256": sha("source")}
    target = {"facility_id": uid(102), "node_id": uid(12), "profile_sha256": sha("target")}
    records = []

    def put(kind, issuer, operation, payload):
        raw = contract.encode({"format_version": 1, "kind": kind, "domain_id": domain,
            "operation_id": uid(operation), "issuer": issuer, "payload": payload})
        records.append(raw)
        return hashlib.sha256(raw).hexdigest()

    origin = put("origin_init_intent", source, 1, {
        "current_epoch": 7, "observed_primary_node_id": source["node_id"],
        "observed_personal_meta_sha256": sha("meta"), "observed_personal_sql_sha256": sha("sql"),
    })
    local = put("local_contract", source, 1, {"init_kind": "origin", "init_intent_sha256": origin})
    seal = put("init_seal", source, 1, {"init_intent_sha256": origin, "local_contract_sha256": local})
    genesis = put("genesis_grant", source, 1, {
        "origin_init_intent_sha256": origin, "local_contract_sha256": local,
        "init_seal_sha256": seal, "epoch": 7,
    })
    receiver = put("receiver_init_intent", target, 2, {"origin": source, "domain_genesis_grant_sha256": genesis})
    receiver_contract = put("local_contract", target, 2, {"init_kind": "receiver", "init_intent_sha256": receiver})
    put("init_seal", target, 2, {"init_intent_sha256": receiver, "local_contract_sha256": receiver_contract})
    put("handoff_intent", source, 3, {
        "predecessor": {"kind": "genesis_grant", "grant_sha256": genesis},
        "current_epoch": 7, "next_epoch": 8, "target": target,
        "promotion_operation_id": uid(4), "expected_personal_revision": 0,
        "expected_personal_sql_sha256": sha("final-preview"),
    })
    return records


def test_caller_list_mutation_cannot_expand_the_admitted_snapshot(monkeypatch):
    records = _closed_handoff_prefix()
    selected = [records[0]]
    entered, appended = threading.Event(), threading.Event()
    original_decode = contract.decode
    decoded = []
    mutation = []

    def barrier_decode(raw, *, limits=contract.DEFAULT_LIMITS):
        value = original_decode(raw, limits=limits)
        decoded.append(raw)
        if len(decoded) == 1:
            entered.set()
            assert appended.wait(5), "Actual second thread never appended"
        return value

    def append_after_admission():
        if entered.wait(5):
            selected.extend(records[1:3])
            mutation.append(True)
            appended.set()

    monkeypatch.setattr(contract, "decode", barrier_decode)
    mutator = threading.Thread(target=append_after_admission, daemon=True)
    mutator.start()
    try:
        assert contract.cross_validate(selected, limits=contract.Limits(max_records=1)) is None
    finally:
        entered.set()
        appended.set()
        mutator.join(5)
    assert not mutator.is_alive() and mutation == [True]
    assert len(selected) == 3 and decoded == [records[0]]


def test_known_receiver_binding_is_required_but_unknown_target_prefix_is_valid():
    records = _closed_handoff_prefix()
    assert contract.cross_validate(records) is None
    changed = deepcopy(contract.decode(records[-1]))
    changed["payload"]["target"]["node_id"] = str(uuid.UUID(int=13))
    other = contract.encode(changed)
    with pytest.raises(contract.ContractError) as caught:
        contract.cross_validate(records[:-1] + [other])
    assert caught.value.code == "FACILITY_BINDING_INVALID"
    # Before receiver initialization, either chosen target is still a valid
    # declaration prefix. The parser must not invent installation or grants.
    assert contract.cross_validate(records[:4] + [records[-1]]) is None
    assert contract.cross_validate(records[:4] + [other]) is None
