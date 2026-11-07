"""Pure closed control families keep all history and settle only current slots."""
from copy import deepcopy
import hashlib
import uuid

import pytest

from reno import sync_source_authority as permanent, sync_source_readmit_local2 as local
from reno import sync_source_readmit_contract as wire
from reno.sync_protocol import SyncError
from tests import test_sync_source_authority as historical
from tests import test_sync_source_readmit_contract as sample


def identifier():
    return str(uuid.uuid4())


class Family:
    """Decoded facts only; these fixtures never provide an operational seal."""
    def __init__(self, kind="budget_resume", rounds=3):
        self.activation, old = historical.projection_fixture()
        self.origin = deepcopy(old[0][1])
        self.core = self.origin["core"]
        self.entries = {name: {} for name in permanent.KINDS}
        self.add("origins", self.origin)
        self.parent, self.parent_receipt, self.parent_disposition = None, None, None
        if kind == "budget_resume":
            self.parent = self.control("budget_defer", 0, version=1)
            self.parent_disposition = self.dispose(self.parent, applied=True)
            self.parent_receipt = self.parent_disposition["result"]["original_receipt"]
        self.controls, self.negatives = [], []
        first = self.control(kind, 0, version=1)
        self.controls.append(first)
        for stage in range(1, rounds + 1):
            negative = self.dispose(self.controls[-1], applied=False)
            self.negatives.append(negative)
            self.controls.append(self.control(kind, stage, version=2, head=permanent.record_ref(negative)))
        self.current = self.controls[-1]

    def add(self, kind, record):
        self.entries[kind][record["record_id"]] = {"state": "committed", "record": record}
        return record

    def basis(self, stage):
        return {"format_version": 1, "kind": "original_core", "core_sha256": permanent.digest(self.core),
            "home_epoch": 2 + stage, "session": deepcopy(sample.LEASE), "claim": deepcopy(self.origin["claim"]),
            "recovery_generation": 2 + stage, "recovery_stream": deepcopy(sample.STREAM), "control_revision": 4 + stage,
            "origin_ref": permanent.record_ref(self.origin), "admission_ref": None,
            "continuity": {"format_version": 1, "continuity_id": identifier(), "contract_sha256": "e" * 64,
                "target_epoch": sample.STREAM["library_epoch"]}}

    def before(self, basis, selected=None):
        authority = {"format_version": 1, "core_sha256": permanent.digest(self.core),
            "origin": permanent.record_ref(self.origin), "defer": None, "resume": None, "failure": None, "publication": None}
        if self.parent is not None:
            authority["defer"] = permanent.record_ref(self.parent)
        if selected is not None:
            authority[{"budget_defer": "defer", "budget_resume": "resume", "source_fail": "failure"}[selected["request"]["kind"]]] = permanent.record_ref(selected)
        return {"binding": deepcopy(self.activation["binding"]), "local_epoch": basis["home_epoch"],
            "core_sha256": permanent.digest(self.core), "admission_ref": None, "claim": deepcopy(basis["claim"]),
            **{key: deepcopy(basis[key]) for key in ("recovery_generation", "recovery_stream", "control_revision")},
            "budget_pending_sha256": None, "source_control_pending_sha256": None,
            "pending_publication": None, "source_authority": authority}

    def control(self, kind, stage, *, version, head=None):
        basis = self.basis(stage)
        body = sample.old_request(kind, defer_request_id=None if self.parent is None else self.parent["record_id"])
        body["source"]["handshake"]["migration_version"] = 17
        body.update(request_id=identifier(), lease=deepcopy(basis["session"]),
            recovery_generation=basis["recovery_generation"], recovery_stream=deepcopy(basis["recovery_stream"]))
        if "claim" in body:
            body["claim"] = deepcopy(basis["claim"])
        if kind == "source_fail":
            body.update(core_sha256=permanent.digest(self.core), submitted_url_sha256=self.core["submitted_url_sha256"])
        if hasattr(self, "controls") and self.controls:
            previous = self.controls[-1]["request"]
            if kind == "budget_defer":
                body["budget_operation_id"] = previous["budget_operation_id"]
            elif kind == "source_fail":
                body["failure"] = deepcopy(previous["failure"])
        record = {"format_version": version, "record_kind": "control", "record_id": body["request_id"],
            "core_sha256": permanent.digest(self.core), "request_sha256": permanent.digest(body),
            "request": body, "creation_basis": basis}
        if version == 2:
            record.update(control_scope={key: deepcopy(basis[key]) for key in local.CONTROL_SCOPE_FIELDS2},
                expected_before=self.before(basis), preservation_provenance={"format_version": 1,
                    "producer": "home_knowledge_restore", "operation_id": identifier(), "receipt_sha256": "f" * 64},
                resume_provenance=None, parent_defer_refs=None, retirement_head=deepcopy(head))
            if kind == "budget_resume":
                record["parent_defer_refs"] = {"control_ref": permanent.record_ref(self.parent),
                    "receipt_ref": permanent.record_ref(self.parent_disposition)}
                record["resume_provenance"] = {"format_version": 1, "producer": "sync_budget_resume",
                    "operation_id": identifier(), "receipt_sha256": "d" * 64}
        permanent.validate_record("controls", record, self.activation)
        return self.add("controls", record)

    def receipt(self, request, result, endpoint, *, parent=None):
        record = {"format_version": 2, "record_kind": "recovery_receipt", "record_id": request["record_id"],
            "core_sha256": permanent.digest(self.core), "request_sha256": request["request_sha256"],
            "request_ref": permanent.record_ref(request), "result": result, "result_sha256": permanent.digest(result),
            "parent_defer": parent, "transport_provenance": {"format_version": 1, "producer": "sync_http",
                "endpoint": endpoint, "target_url_sha256": hashlib.sha256(self.activation["binding"]["target_url"].encode()).hexdigest(),
                "request_sha256": request["request_sha256"]}}
        permanent.validate_record("receipts", record, self.activation)
        return self.add("receipts", record)

    def dispose(self, control, *, applied):
        basis = self.basis(control["creation_basis"]["home_epoch"] - 1)
        original_scope = control.get("control_scope", control["creation_basis"])
        register = sample.register(control["request"])
        register.update({key: deepcopy(control["request"][key]) for key in
            ("source", "target", "lease", "recovery_generation", "recovery_stream")})
        register.update(core_sha256=permanent.digest(self.core), item_id=self.core["item_id"],
            continuity=deepcopy(basis["continuity"]), control_revision=original_scope["control_revision"])
        registration_intent = {"format_version": 1, "record_kind": "recovery_intent", "record_id": register["request_id"],
            "core_sha256": permanent.digest(self.core), "request_sha256": wire.request_sha256(register),
            "request": register, "expected_before": self.before(original_scope, control), "preservation_basis": None,
            "manual_resume_basis": None}
        self.add("requests", registration_intent)
        registered = sample.registration(register)
        registered.update(core_sha256=permanent.digest(self.core), item_id=self.core["item_id"])
        registered["registration_sha256"] = wire.registration_sha256(registered)
        self.receipt(registration_intent, registered, "source/register")
        body = sample.dispose(control["request"], registered=True)
        body.update(format_version=2, continuity=deepcopy(basis["continuity"]), registration=registered,
            current_home_epoch=basis["home_epoch"], lease=deepcopy(basis["session"]),
            recovery_generation=basis["recovery_generation"], recovery_stream=deepcopy(basis["recovery_stream"]),
            control_revision=basis["control_revision"])
        body["source"]["handshake"]["migration_version"] = 17
        body["original"].update(core_sha256=permanent.digest(self.core), item_id=self.core["item_id"],
            submitted_url_sha256=self.core["submitted_url_sha256"], claim=deepcopy(self.origin["claim"]))
        body["preservation"]["to_epoch"] = basis["home_epoch"]
        intent = {"format_version": 2, "record_kind": "recovery_intent", "record_id": body["request_id"],
            "core_sha256": permanent.digest(self.core), "request_sha256": wire.request_sha256(body), "request": body,
            "creation_basis": basis, "expected_before": self.before(basis, control), "resume_provenance": None,
            "preservation_provenance": {"format_version": 1, "producer": "home_knowledge_restore",
                "operation_id": identifier(), "receipt_sha256": "c" * 64}}
        permanent.validate_record("requests", intent, self.activation)
        self.add("requests", intent)
        answer = sample.disposition(body, applied=applied, parent=self.parent_receipt)
        answer.update(format_version=2, core_sha256=permanent.digest(self.core), item_id=self.core["item_id"])
        if applied and control["request"]["kind"] == "source_fail":
            answer["original_receipt"]["core_sha256"] = permanent.digest(self.core)
        parent = None if not applied or control["request"]["kind"] != "budget_resume" else {
            "request": deepcopy(self.parent["request"]), "receipt": deepcopy(self.parent_receipt)}
        return self.receipt(intent, answer, "source/dispose", parent=parent)

    def state(self):
        authority = self.before(self.current["creation_basis"])["source_authority"]
        authority[{"budget_defer": "defer", "budget_resume": "resume", "source_fail": "failure"}[self.current["request"]["kind"]]] = permanent.record_ref(self.current)
        return {"source_execution": deepcopy(self.core), "source_authority": authority, "source_admission": None}


@pytest.mark.parametrize("kind", ["budget_resume", "budget_defer", "source_fail"])
def test_three_negative_predecessors_are_all_closed_and_original_v1_is_unchanged(kind):
    family = Family(kind)
    original = deepcopy(family.controls[0])
    local.record_relations2(family.entries)
    graph = local.control_retirement_graph2(family.entries, family.current["record_id"])
    assert len(graph["ancestor_refs"]) == len(graph["retired_disposition_refs"]) == 3
    assert graph["leaf_ref"] == permanent.record_ref(family.current)
    assert family.controls[0] == original
    assert family.current["request_sha256"] == wire.digest(family.current["request"])
    row = permanent.public_record_projection("controls", family.current, state="committed", activation=family.activation)
    assert family.current["control_scope"]["session"]["lease_token"] not in wire.canonical_json(row)


def test_readmit_settles_exactly_current_defer_resume_and_keeps_three_old_negatives():
    family = Family()
    current = family.dispose(family.current, applied=True)
    local.record_relations2(family.entries)
    selection = local.current_settlement2(family.entries, family.state())
    assert selection["dispositions"] == [family.parent_disposition["result"], current["result"]]
    assert len(selection["retired_control_refs"]) == 3
    assert len(family.negatives) == 3
    assert selection["defer_fact"]["request"]["budget_operation_id"] == family.parent["request"]["budget_operation_id"]
    assert selection["resume_fact"]["request"]["defer_request_id"] == family.parent["record_id"]


@pytest.mark.parametrize("change", ["missing_negative", "missing_registration", "fork", "same_scope", "wrong_parent", "reserved_ancestor"])
def test_incomplete_or_forked_history_cannot_be_hidden_by_the_current_leaf(change):
    family = Family()
    if change == "missing_negative":
        family.entries["receipts"].pop(family.negatives[0]["record_id"])
    elif change == "missing_registration":
        intent = local._resolve(family.entries, family.negatives[0]["request_ref"])
        family.entries["receipts"].pop(intent["request"]["registration"]["request_id"])
    elif change == "fork":
        sibling = deepcopy(family.current)
        sibling["record_id"] = sibling["request"]["request_id"] = identifier()
        sibling["request_sha256"] = permanent.digest(sibling["request"])
        family.add("controls", sibling)
    elif change == "same_scope":
        family.current["control_scope"] = deepcopy(family.controls[-2]["control_scope"])
    elif change == "wrong_parent":
        family.current["retirement_head"] = permanent.record_ref(family.parent_disposition)
    else:
        family.entries["controls"][family.controls[0]["record_id"]]["state"] = "reserved"
    with pytest.raises(SyncError):
        local.control_retirement_graph2(family.entries, family.current["record_id"])


def test_state_rewind_to_retired_resume_requires_reconciliation_before_readmit():
    family = Family()
    family.dispose(family.current, applied=True)
    state = family.state()
    state["source_authority"]["resume"] = permanent.record_ref(family.controls[0])
    with pytest.raises(SyncError):
        local.current_settlement2(family.entries, state)


def test_a_reserved_leaf_is_structural_unknown_and_never_an_ordinary_settlement():
    family = Family()
    family.entries["controls"][family.current["record_id"]]["state"] = "reserved"
    graph = local.control_retirement_graph2(family.entries, family.current["record_id"], allow_reserved=True)
    assert graph["record_state"] == "reserved"
    with pytest.raises(SyncError):
        local.current_settlement2(family.entries, family.state())


def test_a_changed_paid_failure_under_the_same_attempt_cannot_create_another_family():
    family = Family("source_fail")
    family.current["request"]["failure"]["result_sha256"] = "a" * 64
    family.current["request_sha256"] = permanent.digest(family.current["request"])
    with pytest.raises(SyncError):
        local.control_retirement_graph2(family.entries, family.current["record_id"])


def test_cyclic_retirement_cannot_be_projected_as_a_closed_head():
    family = Family("budget_defer", rounds=1)
    tail_negative = family.dispose(family.current, applied=False)
    # The wire original-control bodies stay unchanged. Construct a canonical
    # structural cycle through negative receipts, without trusting timestamp
    # order or a convenient root selected by the caller.
    for item in family.entries["requests"].values():
        for slot in ("defer", "resume", "failure"):
            item["record"]["expected_before"]["source_authority"][slot] = None
    for item in family.entries["receipts"].values():
        request = family.entries["requests"][item["record"]["record_id"]]["record"]
        item["record"]["request_ref"] = permanent.record_ref(request)
    root = family.controls[0]
    replacement = deepcopy(family.current)
    replacement.update(record_id=root["record_id"], request=deepcopy(root["request"]),
        request_sha256=root["request_sha256"], creation_basis=deepcopy(root["creation_basis"]),
        control_scope={key: deepcopy(root["creation_basis"][key]) for key in local.CONTROL_SCOPE_FIELDS2},
        expected_before=family.before(root["creation_basis"]), retirement_head=permanent.record_ref(tail_negative))
    family.entries["controls"][root["record_id"]]["record"] = replacement
    family.current["retirement_head"] = permanent.record_ref(family.negatives[0])
    with pytest.raises(SyncError) as raised:
        local.control_retirement_graph2(family.entries, family.current["record_id"])
    assert raised.value.code.endswith("CONTROL_CYCLE")


def test_entire_catalog_count_limit_is_enforced_before_a_partial_history_can_be_returned():
    family = Family()
    _, samples = historical.projection_fixture()
    template = samples[-1][1]
    while sum(len(values) for values in family.entries.values()) <= permanent.MAX_RECORDS:
        record = deepcopy(template)
        record["record_id"] = record["metadata"]["publication_id"] = identifier()
        family.add("publications", record)
    with pytest.raises(SyncError) as raised:
        local.control_retirement_graph2(family.entries, family.current["record_id"])
    assert raised.value.code.endswith("COUNT_LIMIT")
