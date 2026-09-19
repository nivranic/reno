# -*- coding: utf-8 -*-
"""Dedup stage 2 + conflict candidates (deterministic part).

Stage 1 normalization lives in reno.dict. This module computes char-bigram
cosine candidate pairs (demo substitute for embeddings - thresholds differ!)
and parameter-conflict candidates via the canonical param dictionary."""
from collections import Counter
from itertools import combinations

from . import db
from .dict import normalize_text


def bigram_cosine(a: str, b: str) -> float:
    def grams(x):
        return Counter(x[i:i + 2] for i in range(len(x) - 1))
    ga, gb = grams(a), grams(b)
    if not ga or not gb:
        return 0.0
    inter = sum((ga & gb).values())
    return inter / (sum(ga.values()) ** 0.5 * sum(gb.values()) ** 0.5)


def _atom_view(con, a: dict):
    return normalize_text(f"{a['category']}|{a.get('space') or ''}|"
                          f"{a.get('subject') or ''}|{a['claim']}")


def candidates(con, sim_threshold: float = 0.22):
    atoms = db.all_atoms(con)
    for a in atoms:
        a["_norm"] = _atom_view(con, a)
    pairs = []
    for a, b in combinations(atoms, 2):
        va = a["evidence_refs"][0]["video_id"] if a["evidence_refs"] else None
        vb = b["evidence_refs"][0]["video_id"] if b["evidence_refs"] else None
        if not va or not vb or va == vb:
            continue  # cross-video only; same-video clustering is later work
        sim = bigram_cosine(a["_norm"], b["_norm"])
        if sim >= sim_threshold:
            pairs.append({"a": a["id"], "b": b["id"], "a_video": va,
                          "b_video": vb, "a_claim": a["claim"],
                          "b_claim": b["claim"], "a_polarity": a["polarity"],
                          "b_polarity": b["polarity"],
                          "a_conditions": a["conditions"],
                          "b_conditions": b["conditions"],
                          "similarity": round(sim, 3)})
    pairs.sort(key=lambda p: -p["similarity"])
    return pairs


def param_conflict_candidates(con):
    atoms = db.all_atoms(con)
    out = []
    for a, b in combinations(atoms, 2):
        va = a["evidence_refs"][0]["video_id"] if a["evidence_refs"] else None
        vb = b["evidence_refs"][0]["video_id"] if b["evidence_refs"] else None
        if not va or not vb or va == vb:
            continue
        pa = {p["name"]: p.get("normalized_value")
              for p in a["parameters"]
              if isinstance(p.get("normalized_value"), (int, float))}
        pb = {p["name"]: p.get("normalized_value")
              for p in b["parameters"]
              if isinstance(p.get("normalized_value"), (int, float))}
        for name in set(pa) & set(pb):
            if pa[name] != pb[name]:
                out.append({"type": "numeric_conflict_candidate",
                            "parameter": name,
                            "a": {"atom": a["id"], "video": va, "value": pa[name],
                                  "claim": a["claim"], "conditions": a["conditions"]},
                            "b": {"atom": b["id"], "video": vb, "value": pb[name],
                                  "claim": b["claim"], "conditions": b["conditions"]}})
    return out
