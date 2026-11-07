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

Default/--dry-run only previews. --apply requires the same OS lock as worker
and creates a fresh SQLite backup before its first write. Stop the serving
process/worker before maintenance. The configured model is never changed.
The saved cursor records progress only: missing/invalid labels remain pending
and a normal subsequent --apply retries them without requiring --restart.
"""
import argparse
import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.dont_write_bytecode = True

from reno import config, db, llm, model_scopes, review, tasks  # noqa: E402
import hashlib
from reno.dict import evidence_natures, stage_for, taxonomy  # noqa: E402
from reno.budget import BudgetLedgerError  # noqa: E402
from reno.budget_contract import BudgetContractError, BudgetDeferred  # noqa: E402

TAX = taxonomy()
NATURES = set(evidence_natures())
BATCH = 20
CURSOR_KEY = "backfill:v2:cursor"


def deterministic_plan(con):
    """Read-only plan shared by the preview and applying implementation."""
    rows = [dict(r) for r in con.execute(
        "SELECT id, category, stage, conditions_json, evidence_nature FROM knowledge_atom")]
    n_stage, n_auth, updates = 0, 0, []
    for r in rows:
        new_stage = stage_for(r["category"] or "")
        if new_stage and new_stage != r["stage"]:
            updates.append(("UPDATE knowledge_atom SET stage=? WHERE id=?", (new_stage, r["id"])))
            n_stage += 1
        conds = db.uj(r["conditions_json"]) or {}
        legacy = conds.pop("authority_level", None)
        if legacy and not r["evidence_nature"] and legacy in NATURES:
            updates.append(("UPDATE knowledge_atom SET evidence_nature=?, conditions_json=? WHERE id=?",
                            (legacy, db.j(conds), r["id"])))
            n_auth += 1
        elif legacy:
            updates.append(("UPDATE knowledge_atom SET conditions_json=? WHERE id=?", (db.j(conds), r["id"])))
            n_auth += 1  # key stripped even when nature already set
    return {"stage_fixed": n_stage, "authority_migrated": n_auth}, updates


def deterministic_pass(con) -> dict:
    """Apply within the caller's coordinated maintenance window."""
    stats, updates = deterministic_plan(con)
    for statement, params in updates:
        con.execute(statement, params)
    review.replay(con)
    con.commit()
    return stats


def pending(con):
    return [dict(r) for r in con.execute(
        """SELECT id, category, subject, claim, reason, risk_if_ignored, polarity,
                  conditions_json, evidence_nature
           FROM knowledge_atom WHERE dimension IS NULL ORDER BY id""")]


def _clip(s, n):
    s = str(s or "").strip().replace("\n", " ")
    return s if len(s) <= n else s[:n] + "…"


def _messages(rows):
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
    return [{"role": "system", "content": system},
            {"role": "user", "content": json.dumps(items, ensure_ascii=False)}]


def llm_batch(rows) -> dict:
    """One call per frozen batch -> validated enrichment labels."""
    obj, _model = llm.chat_json(_messages(rows), max_tokens=4096)
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


def _selected_rows(con, args):
    rows = pending(con)
    cursor = None if args.restart else con.execute("SELECT value FROM meta WHERE key=?", (CURSOR_KEY,)).fetchone()
    cursor = cursor["value"] if cursor else None
    # Model batches can omit rows or return invalid dimensions. Their IDs may
    # precede the last attempted ID, so only dimension IS NULL defines pending.
    if args.limit:
        rows = rows[:args.limit]
    return rows, cursor


def _apply(con, args, *, profile=None):
    # Fresh backup on EVERY application, before deterministic normalization,
    # cursor changes or model outputs. A stale earlier backup is not sufficient.
    from reno import storage_admin
    profile = storage_admin.from_connection(con, profile=profile)
    backup = profile.database.with_name(f"{profile.database.name}.bak-backfill-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}")
    db.snapshot(con, backup, profile=profile, kind="backfill")
    print(f"[backfill] backup: {backup}", flush=True)
    det = deterministic_pass(con)
    rows, cursor = _selected_rows(con, args)
    print(f"[backfill] deterministic: {det}; pending={len(rows)} (cursor={cursor or 'none'})", flush=True)
    intent = {"selector": "dimension-is-null", "limit": args.limit,
              "prompt_version": "backfill-v2", "taxonomy_version": config.get("taxonomy_version") or "v2"}
    with model_scopes.execution(profile, "backfill_v2", intent, selection=tuple(row["id"] for row in rows),
            new_execution=getattr(args, "new_execution", False), business_connection=con) as execution:
        return _apply_batches(con, execution, profile=profile, taxonomy_version=intent["taxonomy_version"])


def _fixed_rows(con, ids):
    if not ids:
        return []
    rows = {row["id"]: dict(row) for row in con.execute("""SELECT id, category, subject, claim, reason,
        risk_if_ignored, polarity, conditions_json, evidence_nature, dimension, exceptions FROM knowledge_atom
        WHERE id IN (""" + ",".join("?" for _ in ids) + ")", ids)}
    if any(identifier not in rows for identifier in ids):
        raise BudgetDeferred("ledger_unavailable")
    return [rows[identifier] for identifier in ids]


_WITNESS_FIELDS = frozenset({"format_version", "consumer", "library_id", "continuation_id", "execution_generation",
    "suboperation_key", "selection_sha256", "input_sha256", "current_sha256"})
_WITNESS_IDENTITIES = _WITNESS_FIELDS - {"input_sha256", "current_sha256"}


def _candidate_sha(rows):
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _witness_record(con, key, expected):
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    if row is None:
        return None
    try:
        value = json.loads(row[0]) if len(row[0].encode()) <= 4096 else None
        if (type(value) is not dict or value.keys() != _WITNESS_FIELDS
                or any(type(value[field]) is not type(expected[field]) or value[field] != expected[field]
                       for field in _WITNESS_IDENTITIES)
                or any(type(value[field]) is not str or len(value[field]) != 64
                       or any(char not in "0123456789abcdef" for char in value[field])
                       for field in ("input_sha256", "current_sha256"))):
            raise ValueError
        return value
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise BudgetLedgerError("BUDGET_LEDGER_INVALID") from None


def _existing_batch_operation(con, expected):
    return con.execute("SELECT 1 FROM budget_operation WHERE continuation_id=? AND execution_generation=? "
        "AND kind='model' AND suboperation_key=?", (expected["continuation_id"],
            expected["execution_generation"], expected["suboperation_key"])).fetchone() is not None


def _batch_witness(con, execution, batch_number, rows):
    """Bind old paid bytes to their original candidate state across restarts."""
    stage = f"backfill-v2:batch:{batch_number}"
    key = f"budget:consumer:backfill-input:v1:{execution.continuation_id}:{execution.execution_generation}:{batch_number}"
    digest = _candidate_sha(rows)
    expected = {"format_version": 1, "consumer": "backfill_v2",
        "library_id": db.knowledge_version(con)["library_id"], "continuation_id": execution.continuation_id,
        "execution_generation": execution.execution_generation, "suboperation_key": stage,
        "selection_sha256": _candidate_sha([row["id"] for row in rows]),
        "input_sha256": digest, "current_sha256": digest}
    stored = _witness_record(con, key, expected)
    if stored is None:
        # A legacy paid/unknown operation with no input witness cannot be
        # rebound to freshly read human edits. Keep its original receipt.
        if _existing_batch_operation(con, expected):
            raise BudgetLedgerError("BUDGET_REQUEST_CONFLICT")
    else:
        if stored["current_sha256"] != digest:
            raise BudgetLedgerError("BUDGET_REQUEST_CONFLICT")
        expected = stored
    return key, expected


def _batch_guard(execution, key, expected, batch_ids, connection=None):
    """Outside-writer hook persists; supplied budget writer only observes."""
    from reno import storage_admin
    def verify(con, *, create):
        if (db.knowledge_version(con)["library_id"] != expected["library_id"]
                or _candidate_sha(_fixed_rows(con, batch_ids)) != expected["current_sha256"]):
            raise BudgetLedgerError("BUDGET_REQUEST_CONFLICT")
        stored = _witness_record(con, key, expected)
        if stored is not None:
            if stored != expected:
                raise BudgetLedgerError("BUDGET_REQUEST_CONFLICT")
            return
        if not create:
            raise BudgetDeferred("ledger_unavailable")
        if _existing_batch_operation(con, expected):
            raise BudgetLedgerError("BUDGET_REQUEST_CONFLICT")
        if config.budget_settings()["budget_enabled"]:
            con.execute("INSERT INTO meta(key,value) VALUES(?,?)", (key, db.j(expected)))
    if connection is not None:
        verify(connection, create=False)
        return
    with storage_admin.connection(execution.profile) as owned:
        owned.execute("BEGIN IMMEDIATE")
        try:
            verify(owned, create=True)
            owned.commit()
        except BaseException:
            owned.rollback()
            raise


def _checkpoint_batch_witness(con, key, expected, batch_ids):
    stored = _witness_record(con, key, expected)
    if stored is None:
        return  # Untouched disabled legacy calls create no private metadata.
    if stored != expected:
        raise BudgetLedgerError("BUDGET_REQUEST_CONFLICT")
    stored["current_sha256"] = _candidate_sha(_fixed_rows(con, batch_ids))
    con.execute("UPDATE meta SET value=? WHERE key=?", (db.j(stored), key))


def _apply_batches(con, execution, *, profile, taxonomy_version):
    from reno import storage_lifecycle
    completed = set(execution.completed_items)
    done, errors, t0 = 0, 0, time.time()
    ids = execution.selection
    if not ids:
        con.execute("BEGIN IMMEDIATE")
        model_scopes.checkpoint(con, execution, completed=True)
        con.commit()
    for i in range(0, len(ids), BATCH):
        batch_ids = ids[i:i + BATCH]
        if set(batch_ids) <= completed:
            continue
        chunk = _fixed_rows(con, batch_ids)
        if any(row["id"] not in completed and row["dimension"] is not None for row in chunk):
            raise BudgetLedgerError("BUDGET_REQUEST_CONFLICT")
        witness_key, witness = _batch_witness(con, execution, i // BATCH, chunk)
        messages = _messages(chunk)
        try:
            with model_scopes.call(execution, f"backfill-v2:batch:{i // BATCH}",
                    business_connection=con, version_tags=(
                        ("prompt_version", "backfill-v2"),
                        ("taxonomy_version", str(taxonomy_version)),
                        ("candidate_sha256", witness["input_sha256"]),
                        ("frozen_input_sha256", hashlib.sha256(db.j(messages).encode()).hexdigest())),
                    candidate_guard=lambda connection=None: _batch_guard(
                        execution, witness_key, witness, batch_ids, connection)):
                labels = llm_batch(chunk)
        except (BudgetDeferred, BudgetContractError, BudgetLedgerError):
            raise
        except Exception:
            # Do not advance the resume cursor past a failed provider call.
            print(f"[backfill] batch {i // BATCH} failed; checkpoint unchanged", flush=True)
            errors += 1
            break
        classified = 0
        newly_completed = []
        with storage_lifecycle.shared(profile):
            con.execute("BEGIN IMMEDIATE")
            try:
                # Compare raw inputs and every enrichment field that this
                # result can overwrite, not merely the clipped model text.
                if _fixed_rows(con, batch_ids) != chunk:
                    raise BudgetLedgerError("BUDGET_REQUEST_CONFLICT")
                for row in chunk:
                    if row["id"] in completed:
                        continue
                    lab = labels.get(row["id"])
                    if not lab:
                        continue
                    if lab["dimension"] in TAX["dimensions"]:
                        con.execute("UPDATE knowledge_atom SET dimension=? WHERE id=?", (lab["dimension"], row["id"]))
                        classified += 1
                        newly_completed.append(row["id"])
                    if lab["evidence_nature"] and not row["evidence_nature"]:
                        con.execute("UPDATE knowledge_atom SET evidence_nature=? WHERE id=?", (lab["evidence_nature"], row["id"]))
                    if lab["exceptions"]:
                        con.execute("UPDATE knowledge_atom SET exceptions=? WHERE id=?", (db.j(lab["exceptions"]), row["id"]))
                cursor = chunk[-1]["id"]
                con.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (CURSOR_KEY, cursor))
                review.replay(con)
                model_scopes.checkpoint(con, execution, completed_items=tuple(newly_completed),
                    completed=len(completed | set(newly_completed)) == len(ids))
                _checkpoint_batch_witness(con, witness_key, witness, batch_ids)
                con.commit()
            except BaseException:
                con.rollback()
                raise
        completed.update(newly_completed)
        done += len(chunk)
        incomplete = len(set(batch_ids) - completed)
        if incomplete:
            errors += 1
            print(f"[backfill] batch {i // BATCH} incomplete: {incomplete} missing/invalid dimensions; retained for retry", flush=True)
        print(f"[backfill] {done}/{len(ids)} classified={classified} cursor={cursor} {time.time()-t0:.0f}s", flush=True)
    remain = con.execute("SELECT COUNT(*) FROM knowledge_atom WHERE dimension IS NULL").fetchone()[0]
    print(f"[backfill] done; unclassified={remain}; failed_batches={errors}", flush=True)
    return 1 if errors else 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--restart", action="store_true", help="ignore informational progress cursor; all unclassified atoms are always eligible")
    ap.add_argument("--limit", type=int, default=0, help="stop after N atoms (0=all)")
    ap.add_argument("--new-execution", action="store_true", help="新建维护执行，保留历史用量；--restart仅忽略进度游标")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="read-only preview (the default)")
    mode.add_argument("--apply", action="store_true", help="apply after stopping service/worker; model calls may incur cost")
    args = ap.parse_args(argv)
    if args.limit < 0:
        ap.error("--limit must be nonnegative")
    if args.new_execution and not args.apply:
        ap.error("--new-execution requires --apply")

    from reno import storage_admin
    profile = storage_admin.profile()
    con = db.connect(profile.database)
    try:
        if not args.apply:
            det, _ = deterministic_plan(con)
            rows, cursor = _selected_rows(con, args)
            print(json.dumps({"mode": "preview", "deterministic": det, "pending": len(rows),
                              "cursor": cursor, "writes": False, "model_calls": False}, ensure_ascii=False))
            return 0
        tasks.require_local()
        with tasks.process_lock(profile=profile) as acquired:
            if not acquired:
                print("维护被拒绝：worker 或其它维护任务仍活动，请先停服务/worker。", file=sys.stderr)
                return 2
            storage_admin.setup(profile)
            return _apply(con, args, profile=profile)
    except BudgetDeferred as exc:
        print(json.dumps({"status": "waiting", **exc.public()}, ensure_ascii=False))
        return 3
    except (BudgetContractError, BudgetLedgerError) as exc:
        print(json.dumps(exc.public(), ensure_ascii=False), file=sys.stderr)
        return 2
    except (tasks.TaskError, db.SchemaVersionError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
