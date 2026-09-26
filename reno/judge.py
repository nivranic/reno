# -*- coding: utf-8 -*-
"""Judge stage: LLM verdicts on cross-video candidate pairs -> clusters +
conflict cases persisted. Graph clustering over 'same' edges (union-find).
Conflict/cluster ids are content-derived so user decisions and deep links
survive judge reruns."""
import hashlib
import json
import time
from pathlib import Path

from . import config, db, llm
from . import dedup

PROMPT_FILE = Path(__file__).parent / "prompts" / "judge_v1.txt"


def _merge_candidates(sim_pairs, param_pairs):
    """sim pairs keyed by (a,b); param pairs may duplicate -> merge flags."""
    merged = {}
    for p in sim_pairs:
        merged[(p["a"], p["b"])] = {"sim": p}
    for p in param_pairs:
        key = (p["a"]["atom"], p["b"]["atom"])
        if key in merged:
            merged[key]["param"] = p
        else:
            merged[key] = {"param": p}
    out = []
    for i, (key, v) in enumerate(sorted(merged.items())):
        sim = v.get("sim")
        param = v.get("param")
        if sim:
            out.append({"pair_id": i, "a": sim["a"], "b": sim["b"],
                        "a_claim": sim["a_claim"], "b_claim": sim["b_claim"],
                        "a_polarity": sim["a_polarity"], "b_polarity": sim["b_polarity"],
                        "a_conditions": sim["a_conditions"], "b_conditions": sim["b_conditions"],
                        "similarity": sim["similarity"]})
        else:
            out.append({"pair_id": i, "a": param["a"]["atom"], "b": param["b"]["atom"],
                        "a_claim": param["a"]["claim"], "b_claim": param["b"]["claim"],
                        "a_polarity": None, "b_polarity": None,
                        "a_conditions": param["a"]["conditions"], "b_conditions": param["b"]["conditions"],
                        "parameter": param["parameter"],
                        "a_value": param["a"]["value"], "b_value": param["b"]["value"]})
    return out


def _judge_llm(cands, batch_size: int = 15):
    """Batch judging: one JSON response per <=15 pairs. A single call over
    dozens of pairs exceeds max_tokens and gets truncated mid-JSON."""
    system = PROMPT_FILE.read_text(encoding="utf-8")
    all_j, model_used = [], None
    for i in range(0, len(cands), batch_size):
        chunk = cands[i:i + batch_size]
        user = json.dumps(chunk, ensure_ascii=False)
        obj, model = llm.chat_json(
            [{"role": "system", "content": system},
             {"role": "user", "content": f"候选对列表(JSON,共{len(chunk)}对,pair_id 已重编号):\n{user}"}],
            max_tokens=4096)
        model_used = model_used or model
        all_j.extend(obj.get("judgments", []))
    return all_j, model_used


class UF:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


def run(con, sim_threshold: float = 0.22) -> dict:
    t0 = time.time()
    # snapshot before anything: judge deletes and regenerates
    # clusters/conflicts, so a rollback point is cheap insurance
    bak = config.DATA / f"reno.db.bak-judge-{time.strftime('%Y%m%d-%H%M%S')}"
    db.snapshot(con, bak)
    old_backups = sorted(config.DATA.glob("reno.db.bak-judge-*"))
    for stale in old_backups[:-3]:
        stale.unlink(missing_ok=True)
    sim_pairs = dedup.candidates(con, sim_threshold)
    param_pairs = dedup.param_conflict_candidates(con)
    cands = _merge_candidates(sim_pairs, param_pairs)
    if not cands:
        db.replace_clusters(con, [])
        db.replace_conflicts(con, [])
        con.commit()
        return {"candidates": 0, "clusters": 0, "conflicts": 0}
    judgments, model = _judge_llm(cands)

    uf = UF()
    clusters_edges = {}   # root -> {"same": n, "related": n, "conflicting": n}
    conflicts = []
    by_id = {c["a"] + "|" + c["b"]: c for c in cands}
    atoms_by_id = {a["id"]: a for a in db.all_atoms(con)}
    jmap = {}
    for j in judgments:
        jmap[(j.get("a"), j.get("b"))] = j
    for c in cands:
        key = (c["a"], c["b"])
        j = jmap.get(key) or {}
        rel = j.get("relation", "different")
        if rel in ("same", "related", "conflicting"):
            uf.union(c["a"], c["b"])
            root = uf.find(c["a"])
            e = clusters_edges.setdefault(uf.find(c["a"]), {"same": 0, "related": 0, "conflicting": 0})
            e[rel] += 1
        if rel == "conflicting":
            ctype = j.get("conflict_type") or (
                "numeric_conflict" if c.get("parameter") else "polarity_conflict")
            aa, bb = atoms_by_id.get(c["a"]), atoms_by_id.get(c["b"])
            authority_gap = bool(j.get("authority_gap"))
            pair_key = hashlib.sha1(
                f"{c['a']}|{c['b']}|{ctype}".encode("utf-8")).hexdigest()[:10]
            conflicts.append({
                "conflict_id": f"cfl_{pair_key}",
                "type": ctype,
                "status": "needs_user_decision" if authority_gap else "needs_review",
                "members": {
                    "side_a": {"atoms": [c["a"]],
                               "claim": c["a_claim"],
                               "conditions": c["a_conditions"],
                               "video": aa["evidence_refs"][0]["video_id"] if aa and aa["evidence_refs"] else None},
                    "side_b": {"atoms": [c["b"]],
                               "claim": c["b_claim"],
                               "conditions": c["b_conditions"],
                               "video": bb["evidence_refs"][0]["video_id"] if bb and bb["evidence_refs"] else None},
                },
                "conditional_conclusion": {
                    "condition_overlap": j.get("condition_overlap"),
                    "scope_split": j.get("scope_split"),
                    "authority_gap": j.get("authority_gap"),
                },
                "judge_note": j.get("reason"),
                "recommended_action":
                    "verify_authoritative_source" if ctype == "numeric_conflict"
                    else ("verify_authoritative_source + user_decision" if authority_gap
                          else "user_review"),
            })
    # rebuild edge counts under final roots
    groups = {}
    for x in list(uf.p):
        groups.setdefault(uf.find(x), set()).add(x)
    clusters = []
    for i, (root, members) in enumerate(
            sorted(groups.items(), key=lambda kv: -len(kv[1]))):
        if len(members) < 2:
            continue
        rel_counts = {"same": 0, "related": 0, "conflicting": 0}
        for c in cands:
            if c["a"] in members and c["b"] in members:
                j = jmap.get((c["a"], c["b"])) or {}
                r = j.get("relation")
                if r in rel_counts:
                    rel_counts[r] += 1
        relation = ("conflicting" if rel_counts["conflicting"]
                    else "related" if rel_counts["related"] else "same")
        m0 = atoms_by_id.get(sorted(members)[0]) or {}
        linked = next((cf["conflict_id"] for cf in conflicts
                       if cf["members"]["side_a"]["atoms"][0] in members), None)
        members_key = hashlib.sha1(
            ",".join(sorted(members)).encode("utf-8")).hexdigest()[:10]
        clusters.append({
            "cluster_id": f"clu_{members_key}",
            "canonical_topic": f"{m0.get('category') or ''}·{m0.get('subject') or (m0.get('claim') or '')[:20]}",
            "relation": relation,
            "judge_reason": f"same:{rel_counts['same']} related:{rel_counts['related']} conflicting:{rel_counts['conflicting']}",
            "members": sorted(members),
            "linked_conflict": linked,
        })
    db.replace_clusters(con, clusters)
    db.replace_conflicts(con, conflicts)
    # carry prior user decisions over to the regenerated conflicts: stable
    # content-derived ids make this a plain status restore (last decision wins)
    con.execute(
        """UPDATE conflict_case SET status =
             'decided:' || (SELECT ud.action FROM user_decision ud
                            WHERE ud.conflict_id = conflict_case.conflict_id
                            ORDER BY ud.decision_id DESC LIMIT 1)
           WHERE EXISTS (SELECT 1 FROM user_decision ud
                         WHERE ud.conflict_id = conflict_case.conflict_id)""")
    con.commit()
    stats = {"candidates": len(cands), "clusters": len(clusters),
             "conflicts": len(conflicts),
             "conflict_types": sorted({c["type"] for c in conflicts}),
             "model": model, "process_s": round(time.time() - t0, 1)}
    db.log_run(con, video_id="*", step="judge", model=model,
               prompt_version=config.get("judge_prompt_version"), ok=True,
               detail=str(stats))
    con.commit()
    return stats
