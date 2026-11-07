"""Versioned content identities for references that must survive recomputation.

An atom id is a position within a video, not an immutable content identifier.
Snapshots deliberately exclude extraction scores, model names and review state.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping

FINGERPRINT_VERSION = 1


def decoded(value, default=None):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value if default is None else default
    return default if value is None else value


def _normalize(value):
    if isinstance(value, Mapping):
        return {str(k): _normalize(v) for k, v in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return [_normalize(v) for v in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("content contains a non-finite number")
        return int(value) if value.is_integer() else value
    return value


def canonical_json(value) -> str:
    return json.dumps(_normalize(value), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def fingerprint(value) -> str:
    return f"v{FINGERPRINT_VERSION}:" + hashlib.sha256(
        canonical_json(value).encode("utf-8")).hexdigest()


def atom_snapshot(atom: Mapping) -> dict:
    a = dict(atom)
    evidence = []
    for raw in a.get("evidence_refs") or []:
        e = dict(raw)
        evidence.append({
            "video_id": e.get("video_id") or a.get("video_id"),
            "modality": str(e.get("modality") or "").lower(),
            "source_item_id": e.get("source_item_id"),
            "start_ms": e.get("start_ms"), "end_ms": e.get("end_ms"),
            "evidence_text": e.get("evidence_text") or "",
        })
    evidence.sort(key=canonical_json)
    return {
        "fingerprint_version": FINGERPRINT_VERSION,
        "video_id": a.get("video_id"), "claim": a.get("claim") or "",
        "polarity": a.get("polarity"),
        "conditions": decoded(a.get("conditions", a.get("conditions_json")), {}),
        "exceptions": decoded(a.get("exceptions"), []),
        "parameters": decoded(a.get("parameters", a.get("parameters_json")), []),
        "prices": decoded(a.get("prices", a.get("prices_json")), []),
        "evidence_nature": a.get("evidence_nature"),
        "evidence_refs": evidence,
    }


def atom_fingerprint(atom: Mapping) -> str:
    return fingerprint(atom_snapshot(atom))


def load_atom(con, atom_id: str) -> dict | None:
    row = con.execute("SELECT * FROM knowledge_atom WHERE id=?", (atom_id,)).fetchone()
    if row is None:
        return None
    atom = dict(row)
    atom["evidence_refs"] = [dict(r) for r in con.execute(
        "SELECT * FROM evidence_ref WHERE atom_id=? ORDER BY start_ms,id", (atom_id,))]
    return atom


def conflict_snapshot(conflict: Mapping, atoms_by_id: dict | None = None) -> dict:
    c = dict(conflict)
    members = c.get("members") or {}
    sides = {}
    for key in ("side_a", "side_b"):
        raw = decoded(c.get(key, c.get(key + "_json", members.get(key))), {})
        raw = raw if isinstance(raw, dict) else {"atoms": raw if isinstance(raw, list) else []}
        identities = []
        for aid in sorted(raw.get("atoms") or []):
            atom = (atoms_by_id or {}).get(aid)
            identities.append({"atom_id": aid,
                               "content": atom_snapshot(atom) if atom is not None else None})
        sides[key] = {"claim": raw.get("claim"), "conditions": raw.get("conditions") or {},
                      "video": raw.get("video"), "members": identities}
    return {"fingerprint_version": FINGERPRINT_VERSION,
            "ctype": c.get("ctype", c.get("type")), **sides}


def conflict_fingerprint(conflict: Mapping, atoms_by_id: dict | None = None) -> str:
    return fingerprint(conflict_snapshot(conflict, atoms_by_id))


def load_target(con, kind: str, target_id: str) -> tuple[dict, str] | None:
    if kind == "atom":
        atom = load_atom(con, target_id)
        if atom is None:
            return None
        snap = atom_snapshot(atom)
    elif kind == "conflict":
        row = con.execute("SELECT * FROM conflict_case WHERE conflict_id=?", (target_id,)).fetchone()
        if row is None:
            return None
        c = dict(row)
        ids = set()
        for key in ("side_a_json", "side_b_json"):
            side = decoded(c.get(key), {})
            if isinstance(side, dict):
                ids.update(side.get("atoms") or [])
        atoms = {aid: atom for aid in ids if (atom := load_atom(con, aid)) is not None}
        snap = conflict_snapshot(c, atoms)
    else:
        raise ValueError("unsupported evidence target")
    return snap, fingerprint(snap)


def reference_state(con, kind: str, target_id: str, saved_fingerprint: str | None) -> str:
    if not saved_fingerprint:
        return "legacy_unbound"
    target = load_target(con, kind, target_id)
    if target is None:
        return "missing"
    return "active" if target[1] == saved_fingerprint else "stale"
