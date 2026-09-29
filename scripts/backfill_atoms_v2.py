# -*- coding: utf-8 -*-
"""Backfill v2 enrichment columns for atoms created before taxonomy v2.

Per atom (selector: dimension IS NULL — natural idempotence; atoms already
carrying a dimension are skipped, reruns only catch stragglers):
  1. deterministic: stage re-derived from category via stage_map (v1 left
     44 free-text values), legacy conditions.authority_level migrated into
     evidence_nature (single source of truth)
  2. LLM (batched ~20/call): dimension / evidence_nature / exceptions judged
     from subject+claim+reason+risk+conditions+polarity only — original
     claim/evidence/decisions are never modified.

Checkpoint in meta('backfill:v2:cursor', last atom id) so an interrupted run
continues; --restart clears it. DB snapshot taken before the first write.
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reno import config, db, llm  # noqa: E402
from reno.dict import evidence_natures, stage_for, taxonomy  # noqa: E402

TAX = taxonomy()
NATURES = set(evidence_natures())
BATCH = 20
CURSOR_KEY = "backfill:v2:cursor"


def deterministic_pass(con) -> dict:
    """stage re-derivation + authority_level migration for ALL rows."""
    rows = [dict(r) for r in con.execute(
        "SELECT id, category, stage, conditions_json, evidence_nature FROM knowledge_atom")]
    n_stage, n_auth = 0, 0
    for r in rows:
        new_stage = stage_for(r["category"] or "")
        if new_stage and new_stage != r["stage"]:
            con.execute("UPDATE knowledge_atom SET stage=? WHERE id=?",
                        (new_stage, r["id"]))
            n_stage += 1
        conds = db.uj(r["conditions_json"]) or {}
        legacy = conds.pop("authority_level", None)
        if legacy and not r["evidence_nature"] and legacy in NATURES:
            con.execute("UPDATE knowledge_atom SET evidence_nature=?, conditions_json=? WHERE id=?",
                        (legacy, db.j(conds), r["id"]))
            n_auth += 1
        elif legacy:
            con.execute("UPDATE knowledge_atom SET conditions_json=? WHERE id=?",
                        (db.j(conds), r["id"]))
            n_auth += 1  # key stripped even when nature already set
    con.commit()
    return {"stage_fixed": n_stage, "authority_migrated": n_auth}


def pending(con):
    return [dict(r) for r in con.execute(
        """SELECT id, category, subject, claim, reason, risk_if_ignored, polarity,
                  conditions_json, evidence_nature
           FROM knowledge_atom WHERE dimension IS NULL ORDER BY id""")]


def _clip(s, n):
    s = str(s or "").strip().replace("\n", " ")
    return s if len(s) <= n else s[:n] + "…"


def llm_batch(rows) -> dict:
    """One call per batch -> {atom_id: {dimension, evidence_nature, exceptions}}."""
    items = [{
        "id": r["id"],
        "category": r["category"] or "",
        "subject": _clip(r["subject"], 40),
        "claim": _clip(r["claim"], 160),
        "reason": _clip(r["reason"], 120),
        "risk": _clip(r["risk_if_ignored"], 80),
        "polarity": r["polarity"],
        "conditions": db.uj(r["conditions_json"]) or {},
    } for r in rows]
    system = (
        "你是装修知识库的标注器。对每个知识原子输出三个字段:\n"
        "dimension(知识维度,从 安全合规/材料产品/性能可靠/场景适配/施工工艺/使用注意/"
        "维护维修/验收质检/问题排查/报价采购/成本行情/方案比较/空间体验/工期协同/其他 中选一个;"
        "拿不准用\"其他\",不要硬塞)\n"
        "evidence_nature(证据性质,从 author_opinion/cited_standard/author_test/"
        "product_claim/third_party/user_feedback/inference 中选一个;只有明确转述标准规范"
        "才用 cited_standard,拿不准用 author_opinion)\n"
        "exceptions(不适用情形/例外,字符串数组,原文没有依据就给空数组,不得编造)\n"
        "只输出 JSON: {\"labels\":[{\"id\":\"\",\"dimension\":\"\","
        "\"evidence_nature\":\"\",\"exceptions\":[\"\"]}]},每条输入都必须有对应输出。")
    obj, _model = llm.chat_json(
        [{"role": "system", "content": system},
         {"role": "user", "content": json.dumps(items, ensure_ascii=False)}],
        max_tokens=4096)
    out = {}
    for lab in obj.get("labels", []) if isinstance(obj, dict) else []:
        if not isinstance(lab, dict) or not lab.get("id"):
            continue
        dim = lab.get("dimension") if lab.get("dimension") in TAX["dimensions"] else None
        nat = lab.get("evidence_nature") if lab.get("evidence_nature") in NATURES else None
        exc = [str(x).strip()[:200] for x in (lab.get("exceptions") or [])
               if isinstance(x, str) and str(x).strip()][:8]
        if not dim and not nat and not exc:
            continue  # nothing usable -> atom stays 待归类, no empty write
        out[lab["id"]] = {"dimension": dim, "evidence_nature": nat, "exceptions": exc}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--restart", action="store_true", help="ignore checkpoint cursor")
    ap.add_argument("--limit", type=int, default=0, help="stop after N atoms (0=all)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    con = db.connect()
    try:
        det = deterministic_pass(con)
        rows = pending(con)
        cursor = None if args.restart else con.execute(
            "SELECT value FROM meta WHERE key=?", (CURSOR_KEY,)).fetchone()
        cursor = cursor["value"] if cursor else None
        if cursor:
            rows = [r for r in rows if r["id"] > cursor]
        if args.limit:
            rows = rows[:args.limit]
        print(f"[backfill] deterministic: {det}; pending={len(rows)} "
              f"(cursor={cursor or 'none'})", flush=True)
        if not rows or args.dry_run:
            return
        baks = sorted(config.DATA.glob("reno.db.bak-backfill-*"))
        if not baks:
            db.snapshot(con, config.DATA / f"reno.db.bak-backfill-{time.strftime('%Y%m%d-%H%M%S')}")
            print("[backfill] snapshot written", flush=True)

        done = 0
        t0 = time.time()
        for i in range(0, len(rows), BATCH):
            chunk = rows[i:i + BATCH]
            labels = {}
            try:
                labels = llm_batch(chunk)
            except Exception as e:  # noqa: BLE001 - keep going; cursor records progress
                print(f"[backfill] batch {i // BATCH} LLM error: {str(e)[:200]}", flush=True)
            for r in chunk:
                lab = labels.get(r["id"])
                if not lab:
                    continue
                if lab["dimension"]:
                    con.execute("UPDATE knowledge_atom SET dimension=? WHERE id=?",
                                (lab["dimension"], r["id"]))
                if lab["evidence_nature"] and not r["evidence_nature"]:
                    con.execute("UPDATE knowledge_atom SET evidence_nature=? WHERE id=?",
                                (lab["evidence_nature"], r["id"]))
                if lab["exceptions"]:
                    con.execute("UPDATE knowledge_atom SET exceptions=? WHERE id=?",
                                (db.j(lab["exceptions"]), r["id"]))
            cursor = chunk[-1]["id"]
            con.execute("INSERT INTO meta(key,value) VALUES(?,?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (CURSOR_KEY, cursor))
            con.commit()
            done += len(chunk)
            print(f"[backfill] {done}/{len(rows)} labeled={len(labels)} "
                  f"cursor={cursor} {time.time() - t0:.0f}s", flush=True)
        remain = con.execute(
            "SELECT COUNT(*) FROM knowledge_atom WHERE dimension IS NULL").fetchone()[0]
        print(f"[backfill] done. still unclassified (LLM 未能判定/其他): {remain}", flush=True)
    finally:
        con.close()


if __name__ == "__main__":
    main()
