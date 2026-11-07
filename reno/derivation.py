"""Read-only validity of generated relations, separate from human review state.

Bindings describe the actual atom inputs used by a successful judge run. Old
rows remain unbound; reading, migration and synchronization never invent proof.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping

from . import evidence_identity as identity

STATES = ("active", "stale", "missing", "unknown")
MAX_INPUTS = 10_000
MAX_BINDING_BYTES = 4 * 1024 * 1024
_FINGERPRINT = re.compile(r"v1:[0-9a-f]{64}\Z")


def input_fingerprint(atom: Mapping) -> str:
    """Bind semantic evidence and every extra field used for candidate selection."""
    atom = dict(atom)
    return identity.fingerprint({"atom": identity.atom_snapshot(atom),
        "category": atom.get("category"), "space": atom.get("space"), "subject": atom.get("subject")})


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate derivation key")
        value[key] = item
    return value


def _atom_id(value):
    return type(value) is str and 0 < len(value) <= 512 and not any(ord(c) < 32 for c in value)


def validate_binding(raw: str | dict | None) -> dict | None:
    """Validate only shape, never current content; legitimate stale data is valid."""
    if raw is None:
        return None
    if type(raw) is str:
        if len(raw.encode("utf-8")) > MAX_BINDING_BYTES:
            raise ValueError("Derivation binding is too large")
        try:
            raw = json.loads(raw, object_pairs_hook=_unique)
        except (ValueError, RecursionError) as exc:
            raise ValueError("Invalid derivation JSON") from exc
    if (type(raw) is not dict or set(raw) != {"version", "inputs"}
            or type(raw["version"]) is not int or raw["version"] != 1
            or type(raw["inputs"]) is not list or not 1 <= len(raw["inputs"]) <= MAX_INPUTS):
        raise ValueError("Invalid derivation binding")
    previous, inputs = None, []
    for item in raw["inputs"]:
        if (type(item) is not dict or set(item) != {"atom_id", "fingerprint"}
                or not _atom_id(item["atom_id"]) or type(item["fingerprint"]) is not str
                or not _FINGERPRINT.fullmatch(item["fingerprint"])
                or previous is not None and item["atom_id"] <= previous):
            raise ValueError("Derivation inputs must have unique sorted IDs and versioned fingerprints")
        previous = item["atom_id"]
        inputs.append(dict(item))
    return {"version": 1, "inputs": inputs}


def bind_inputs(atoms_by_id: Mapping, atom_ids) -> dict:
    """Use only a frozen judge input set; not a repair function for legacy rows."""
    members = list(atom_ids)
    if any(not _atom_id(aid) for aid in members) or len(set(members)) != len(members):
        raise ValueError("Invalid derivation members")
    binding = {"version": 1, "inputs": [{"atom_id": aid, "fingerprint": input_fingerprint(atoms_by_id[aid])}
                                        for aid in sorted(members)]}
    return validate_binding(binding)


def member_ids(kind: str, relation: Mapping) -> list[str] | None:
    """Return exact unique members or None for malformed historical references."""
    relation = dict(relation)
    if kind == "cluster":
        groups = [identity.decoded(relation.get("members_json", relation.get("members")), None)]
    elif kind == "conflict":
        groups = []
        members = relation.get("members") or {}
        for side in ("side_a", "side_b"):
            value = identity.decoded(relation.get(side + "_json", relation.get(side, members.get(side))), None)
            if type(value) is not dict:
                return None
            groups.append(value.get("atoms"))
    else:
        raise ValueError("Unknown relation kind")
    output = []
    for group in groups:
        if (type(group) is not list or not group or any(not _atom_id(aid) for aid in group)
                or len(set(group)) != len(group)):
            return None
        output.extend(group)
    if len(output) > MAX_INPUTS or len(set(output)) != len(output):
        return None
    return sorted(output)


def _dto(state, reason, changed=0, missing=0):
    return {"state": state, "reason": reason, "changed_count": changed, "missing_count": missing}


def _evaluate(kind, row, fingerprints):
    try:
        binding = validate_binding(row.get("derivation_json"))
    except (ValueError, TypeError, UnicodeError):
        return _dto("unknown", "invalid_binding")
    if binding is None:
        return _dto("unknown", "unbound")
    members = member_ids(kind, row)
    if members is None:
        return _dto("unknown", "invalid_members")
    if members != [item["atom_id"] for item in binding["inputs"]]:
        return _dto("unknown", "members_mismatch")
    missing = sum(item["atom_id"] not in fingerprints for item in binding["inputs"])
    changed = sum(item["atom_id"] in fingerprints and fingerprints[item["atom_id"]] != item["fingerprint"]
                  for item in binding["inputs"])
    return (_dto("missing", "members_missing", changed, missing) if missing else
            _dto("stale", "inputs_changed", changed) if changed else _dto("active", "inputs_match"))


def evaluate_relations(con, *, public=False) -> dict:
    """Evaluate all visible relations in a single consistent read snapshot."""
    from . import sync_visibility
    owned = not con.in_transaction
    if owned:
        con.execute("BEGIN")
    try:
        relations, needed = {}, set()
        for kind, table, key, predicate in (
            ("cluster", "knowledge_cluster", "cluster_id", sync_visibility.cluster_sql),
            ("conflict", "conflict_case", "conflict_id", sync_visibility.conflict_sql),
        ):
            where = " WHERE " + predicate() if public else ""
            relations[kind] = {row[key]: dict(row) for row in con.execute("SELECT * FROM " + table + where)}
            for row in relations[kind].values():
                try:
                    binding = validate_binding(row.get("derivation_json"))
                except (ValueError, TypeError, UnicodeError):
                    continue
                if binding is not None:
                    needed.update(item["atom_id"] for item in binding["inputs"])
        fingerprints = {}
        if needed:
            values = json.dumps(sorted(needed), ensure_ascii=False)
            where = " AND " + sync_visibility.atom_sql() if public else ""
            atoms = {row["id"]: {**dict(row), "evidence_refs": []} for row in con.execute(
                "SELECT * FROM knowledge_atom WHERE id IN (SELECT value FROM json_each(?))" + where, (values,))}
            for row in con.execute("SELECT * FROM evidence_ref WHERE atom_id IN (SELECT value FROM json_each(?))", (values,)):
                if row["atom_id"] in atoms:
                    atoms[row["atom_id"]]["evidence_refs"].append(dict(row))
            for atom in atoms.values():
                try:
                    fingerprints[atom["id"]] = input_fingerprint(atom)
                except (ValueError, TypeError):
                    # An invalid current input cannot prove an old fingerprint.
                    fingerprints[atom["id"]] = None
        result = {kind + "s": {identifier: _evaluate(kind, row, fingerprints) for identifier, row in rows.items()}
                  for kind, rows in relations.items()}
        return result
    finally:
        if owned:
            con.rollback()


def summary(con, *, public=False) -> dict:
    result = {}
    for kind, rows in evaluate_relations(con, public=public).items():
        counts = {state: 0 for state in STATES}
        for dto in rows.values():
            counts[dto["state"]] += 1
        result[kind] = {**counts, "total": len(rows)}
    return result
