# -*- coding: utf-8 -*-
"""Judge stage: LLM verdicts on cross-video candidate pairs -> clusters +
conflict cases persisted. Graph clustering over 'same' edges (union-find).
Conflict/cluster ids are content-derived so user decisions and deep links
survive judge reruns."""
import hashlib
import json
import time
from contextlib import nullcontext
from pathlib import Path

from . import config, db, derivation, llm, model_scopes, storage_lifecycle
from . import dedup

PROMPT_DIR = Path(__file__).parent / "prompts"


class JudgeError(ValueError):
    def __init__(self, message, code="JUDGE_INVALID_RESULT"):
        super().__init__(message)
        self.code = code


def _judgment_map(candidates, judgments):
    """Incomplete, duplicate or mismatched model output is never a fresh result."""
    if type(judgments) is not list:
        raise JudgeError("Judge results must be a list")
    by_pair = {(item["a"], item["b"]): item for item in candidates}
    by_id = {item["pair_id"]: item for item in candidates}
    result = {}
    for item in judgments:
        if type(item) is not dict or item.get("relation") not in {"same", "related", "different", "conflicting"}:
            raise JudgeError("Judge returned an invalid relation")
        if "pair_id" in item:
            if type(item["pair_id"]) is not int or item["pair_id"] not in by_id:
                raise JudgeError("Judge returned an unknown pair")
            candidate = by_id[item["pair_id"]]
            pair = (candidate["a"], candidate["b"])
            if ("a" in item or "b" in item) and (item.get("a"), item.get("b")) != pair:
                raise JudgeError("Judge pair ID disagrees with its members")
        else:
            pair = (item.get("a"), item.get("b"))
            if not all(type(value) is str for value in pair) or pair not in by_pair:
                raise JudgeError("Judge returned unknown members")
        if pair in result:
            raise JudgeError("Judge returned duplicate verdicts")
        result[pair] = item
    if set(result) != set(by_pair):
        raise JudgeError("Judge omitted candidate verdicts")
    return result


def _commit_results(con, frozen, clusters, conflicts):
    """Compare the entire candidate input set after LLM work, under a write lock."""
    if con.in_transaction:
        raise JudgeError("Judge requires ownership of its result transaction", "JUDGE_TRANSACTION_OPEN")
    con.execute("BEGIN IMMEDIATE")
    try:
        current = {a["id"]: derivation.input_fingerprint(a) for a in db.all_atoms(con)}
        if current != frozen:
            raise JudgeError("Judge inputs changed; existing results were preserved", "JUDGE_INPUT_CHANGED")
        db.replace_clusters(con, clusters)
        db.replace_conflicts(con, conflicts)
        # Human review binding remains an independent content identity.
        from . import review
        review.replay(con)
        execution = model_scopes.current_execution()
        if execution is not None and execution.consumer == "judge":
            model_scopes.checkpoint(con, execution, completed=True)
        con.commit()
    except BaseException:
        con.rollback()
        raise


def _prompt_text() -> str:
    ver = config.get("judge_prompt_version") or "v2"
    p = PROMPT_DIR / f"judge_{ver}.txt"
    if not p.exists():
        p = PROMPT_DIR / "judge_v2.txt"
    return p.read_text(encoding="utf-8")


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
                        "a_nature": sim.get("a_nature"), "b_nature": sim.get("b_nature"),
                        "similarity": sim["similarity"]})
        else:
            out.append({"pair_id": i, "a": param["a"]["atom"], "b": param["b"]["atom"],
                        "a_claim": param["a"]["claim"], "b_claim": param["b"]["claim"],
                        "a_polarity": None, "b_polarity": None,
                        "a_conditions": param["a"]["conditions"], "b_conditions": param["b"]["conditions"],
                        "a_nature": param["a"].get("nature"), "b_nature": param["b"].get("nature"),
                        "parameter": param["parameter"],
                        "a_value": param["a"]["value"], "b_value": param["b"]["value"]})
    return out


def _judge_llm(cands, batch_size: int = 15):
    """Batch judging: one JSON response per <=15 pairs. A single call over
    dozens of pairs exceeds max_tokens and gets truncated mid-JSON."""
    system = _prompt_text()
    execution = model_scopes.current_execution()
    prompt_version = config.get("judge_prompt_version") or "v2"
    all_j, model_used = [], None
    for i in range(0, len(cands), batch_size):
        chunk = cands[i:i + batch_size]
        user = json.dumps(chunk, ensure_ascii=False)
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": f"候选对列表(JSON,共{len(chunk)}对,pair_id 已重编号):\n{user}"}]
        with (model_scopes.call(execution, f"judge:batch:{i // batch_size}", version_tags=(
                ("prompt_version", str(prompt_version)),
                ("candidate_sha256", hashlib.sha256(db.j(chunk).encode()).hexdigest()),
                ("frozen_input_sha256", hashlib.sha256(db.j(messages).encode()).hexdigest())))
                if execution is not None else nullcontext()):
            obj, model = llm.chat_json(messages, max_tokens=4096)
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


def run(con, sim_threshold: float = 0.22, *, profile=None) -> dict:
    from . import storage_admin
    from .worker import _execution_profile
    if con.in_transaction:
        raise JudgeError("Finish pending writes before running judge", "JUDGE_TRANSACTION_OPEN")
    inherited = model_scopes.current_execution()
    profile = storage_admin.from_connection(con, profile=profile or (
        inherited.profile if inherited is not None else _execution_profile()))
    return _run(con, sim_threshold, profile=profile)


def _run(con, sim_threshold: float = 0.22, *, profile=None) -> dict:
    t0 = time.time()
    if con.in_transaction:
        raise JudgeError("Finish pending writes before running judge", "JUDGE_TRANSACTION_OPEN")
    # snapshot before anything: judge deletes and regenerates
    # clusters/conflicts, so a rollback point is cheap insurance
    from . import storage_admin, storage_backups
    import uuid
    profile = storage_admin.from_connection(con, profile=profile)
    # Finish the original journal before introducing another backup or cleanup
    # request. A partial unlink must be retryable through the ordinary producer.
    for operation_id in storage_backups.pending_retention(profile, kind="judge", keep=3):
        resumed = storage_backups.retain_recent(profile, kind="judge", keep=3, operation_id=operation_id)
        if resumed["blocked"]:
            error = JudgeError(f"Backup retention operation {operation_id} remains blocked", "JUDGE_BACKUP_RETENTION_BLOCKED")
            error.operation_id = operation_id
            raise error
    bak = profile.database.with_name(f"{profile.database.name}.bak-judge-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex}")
    db.snapshot(con, bak, profile=profile, kind="judge")
    operation_id = str(uuid.uuid4())
    retention = storage_backups.retain_recent(profile, kind="judge", keep=3,
                                              operation_id=operation_id)
    if retention["blocked"]:
        error = JudgeError(f"Backup retention operation {operation_id} is blocked; retry judge to resume it", "JUDGE_BACKUP_RETENTION_BLOCKED")
        error.operation_id = operation_id
        raise error
    with model_scopes.execution(profile, "judge", {"sim_threshold": sim_threshold,
            "prompt_version": config.get("judge_prompt_version") or "v2",
            "model": config.get("atomize_model")}, business_connection=con):
        return _run_candidates(con, sim_threshold, profile=profile, t0=t0)


def _run_candidates(con, sim_threshold, *, profile, t0):
    con.execute("BEGIN")
    try:
        atoms = db.all_atoms(con)
        atoms_by_id = {a["id"]: a for a in atoms}
        frozen = {aid: derivation.input_fingerprint(atom) for aid, atom in atoms_by_id.items()}
    finally:
        con.rollback()
    sim_pairs = dedup.candidates(con, sim_threshold, atoms=atoms)
    param_pairs = dedup.param_conflict_candidates(con, atoms=atoms)
    cands = _merge_candidates(sim_pairs, param_pairs)
    if not cands:
        with storage_lifecycle.shared(profile):
            _commit_results(con, frozen, [], [])
        return {"candidates": 0, "clusters": 0, "conflicts": 0}
    judgments, model = _judge_llm(cands)

    uf = UF()
    clusters_edges = {}   # root -> {"same": n, "related": n, "conflicting": n}
    conflicts = []
    jmap = _judgment_map(cands, judgments)
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
                "derivation_json": derivation.bind_inputs(atoms_by_id, [c["a"], c["b"]]),
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
            "derivation_json": derivation.bind_inputs(atoms_by_id, sorted(members)),
            "canonical_topic": f"{m0.get('category') or ''}·{m0.get('subject') or (m0.get('claim') or '')[:20]}",
            "relation": relation,
            "judge_reason": f"same:{rel_counts['same']} related:{rel_counts['related']} conflicting:{rel_counts['conflicting']}",
            "members": sorted(members),
            "linked_conflict": linked,
        })
    with storage_lifecycle.shared(profile):
        _commit_results(con, frozen, clusters, conflicts)
    stats = {"candidates": len(cands), "clusters": len(clusters),
             "conflicts": len(conflicts),
             "conflict_types": sorted({c["type"] for c in conflicts}),
             "model": model, "process_s": round(time.time() - t0, 1)}
    db.log_run(con, video_id="*", step="judge", model=model,
               prompt_version=config.get("judge_prompt_version"), ok=True,
               detail=str(stats))
    con.commit()
    return stats
