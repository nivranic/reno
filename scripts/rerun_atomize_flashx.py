"""Preview or explicitly re-atomize SELECTED videos without an upfront wipe.

Default is a read-only plan. To apply, stop serve/worker and pass --apply plus
one or more --video-id arguments. Uses the configured model, never changes it.
The shared worker OS lock fences concurrent processing, and a SQLite backup
precedes all writes. Old atoms/evidence/reviews survive a failed replacement.
--judge is a separate explicit request to rebuild global disputes afterwards.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.dont_write_bytecode = True
from reno import atomize, config, db, model_scopes, pipeline, review, tasks  # noqa: E402
from reno.budget import BudgetLedgerError  # noqa: E402
from reno.budget_contract import BudgetContractError, BudgetDeferred  # noqa: E402


class _MaintenanceTransaction:
    """Existing step helpers may commit; maintenance commits after validation."""
    def __init__(self, con):
        self.connection = con

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def commit(self):
        pass


def plan(con, video_ids=None, *, judge=False):
    rows = [dict(row) for row in con.execute("SELECT video_id,title,status FROM video_asset ORDER BY video_id")]
    selected = list(dict.fromkeys(video_ids or [row["video_id"] for row in rows]))
    by_id = {row["video_id"]: row for row in rows}
    missing = [vid for vid in selected if vid not in by_id]
    if missing:
        raise ValueError("选定视频不存在：" + ",".join(missing))
    # Reading the config directly avoids get()'s directory-initialization side
    # effect during a promised read-only preview. Same explicit path and env
    # precedence, with no fallback if the selected config is absent/invalid.
    local = config._read_local(config.local_path())
    model = os.environ.get("RENO_ATOMIZE_MODEL", local.get("atomize_model", config.DEFAULTS["atomize_model"]))
    return {"mode": "preview", **db.knowledge_version(con), "model": model,
            "videos": [{**by_id[vid], "old_atoms": con.execute("SELECT COUNT(*) FROM knowledge_atom WHERE video_id=?", (vid,)).fetchone()[0],
                        "timeline_items": len(db.timeline_index(con, vid))} for vid in selected],
            "steps": ["atomize"], "global_judge": judge, "writes": False, "model_calls": False}


def _apply(con, selected, *, judge=False, new_execution=False, profile=None):
    from reno import storage_admin, storage_lifecycle
    profile = storage_admin.from_connection(con, profile=profile)
    backup = profile.database.with_name(f"{profile.database.name}.bak-rerun-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}")
    db.snapshot(con, backup, profile=profile, kind="rerun")
    print(f"[rerun] backup: {backup}", flush=True)
    selection = tuple(row["video_id"] for row in selected["videos"])
    intent = {"video_ids": sorted(selection), "judge": judge, "model": selected["model"],
              "prompt_version": config.get("atomize_prompt_version") or "v2",
              "taxonomy_version": config.get("taxonomy_version") or "v2"}
    with model_scopes.execution(profile, "rerun_atomize", intent, selection=selection,
            new_execution=new_execution, business_connection=con) as execution:
        restored = plan(con, list(execution.selection), judge=judge)
        return _apply_selected(con, restored, judge=judge, profile=profile,
                               execution=execution, backup=backup)


def _apply_selected(con, selected, *, judge, profile, execution, backup):
    from reno import storage_lifecycle
    results, failed = {}, []
    completed = set(execution.completed_items)
    for video in selected["videos"]:
        vid = video["video_id"]
        if vid in completed:
            results[vid] = {"status": "completed", "resumed": True}
            continue
        try:
            prepared = atomize.prepare(con, vid, profile=profile)
            generated = atomize.generate(prepared, business_connection=con)
            # Model generation and validation finish before taking the writer.
            # The short transaction either applies valid frozen outputs with
            # their markers/reviews or retains all previous knowledge.
            with storage_lifecycle.shared(profile):
                con.execute("BEGIN IMMEDIATE")
                try:
                    stats = atomize.apply(_MaintenanceTransaction(con), prepared, generated, commit=False)
                    if (not isinstance(stats, dict) or stats.get("error")
                            or type(stats.get("n_atoms")) is not int or stats["n_atoms"] < 1):
                        raise ValueError("未得到可验证的非空原子结果")
                    con.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                (f"step:{vid}:atomize", db.now()))
                    review.replay(con)
                    model_scopes.checkpoint(con, execution, completed_items=(vid,),
                        completed=not judge and len(completed | {vid}) == len(selected["videos"]))
                    con.commit()
                except BaseException:
                    con.rollback()
                    raise
            results[vid] = {"status": "completed", "n_atoms": stats["n_atoms"], "model": stats.get("model")}
            completed.add(vid)
        except (BudgetDeferred, BudgetContractError, BudgetLedgerError):
            con.rollback()
            raise
        except Exception:
            con.rollback()
            db.log_run(con, vid, "atomize", ok=False, detail="维护重跑失败，旧原子和证据已保留")
            review.replay(con)
            con.commit()
            failed.append(vid)
            results[vid] = {"status": "failed", "detail": "重跑失败，旧原子和证据已保留"}
    judge_result = None
    if judge and not failed:
        try:
            from reno import judge as judge_module
            judge_result = judge_module.run(con, profile=profile)
            con.execute("INSERT INTO meta(key,value) VALUES('last_judge',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (db.now(),))
            model_scopes.checkpoint(con, execution, completed=True)
            con.commit()
        except (BudgetDeferred, BudgetContractError, BudgetLedgerError):
            raise
        except Exception:
            judge_result = {"status": "failed", "detail": "争议更新失败，未预先清除旧聚类或冲突"}
    summary = {"model": selected["model"], "video_ids": [row["video_id"] for row in selected["videos"]],
               "failed": failed, "judge": judge_result, "backup": str(backup), "per_video": results}
    target = profile.database.parent / "rerun-flashx-summary.json"
    temp = target.with_suffix(".json.tmp")
    temp.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(target)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 1 if failed or (isinstance(judge_result, dict) and judge_result.get("status") == "failed") else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video-id", action="append", default=[], help="selected video (repeat for more than one)")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--dry-run", action="store_true", help="read-only preview, the default")
    modes.add_argument("--apply", action="store_true", help="apply the selected plan after stopping service/worker")
    parser.add_argument("--judge", action="store_true", help="explicitly rebuild global disputes after successful atomization; may incur model cost")
    parser.add_argument("--new-execution", action="store_true", help="新建维护执行，保留历史用量；默认复用未完成执行")
    args = parser.parse_args(argv)
    if args.apply and not args.video_id:
        parser.error("--apply requires explicit --video-id selections; implicit whole-library clearing is disabled")
    if args.new_execution and not args.apply:
        parser.error("--new-execution requires --apply")
    from reno import storage_admin
    profile = storage_admin.profile()
    con = db.connect(profile.database)
    try:
        if not args.apply:
            print(json.dumps(plan(con, args.video_id, judge=args.judge), ensure_ascii=False))
            return 0
        tasks.require_local()
        with tasks.process_lock(profile=profile) as acquired:
            if not acquired:
                print("维护被拒绝：worker 或其它维护任务仍活动，请先停服务/worker。", file=sys.stderr)
                return 2
            storage_admin.setup(profile)
            selected = plan(con, args.video_id, judge=args.judge)
            if any(not row["timeline_items"] for row in selected["videos"]):
                print("维护被拒绝：选定视频缺少时间轴，先完成正常处理流程。", file=sys.stderr)
                return 2
            return _apply(con, selected, judge=args.judge, new_execution=args.new_execution, profile=profile)
    except BudgetDeferred as exc:
        print(json.dumps({"status": "waiting", **exc.public()}, ensure_ascii=False))
        return 3
    except (BudgetContractError, BudgetLedgerError) as exc:
        print(json.dumps(exc.public(), ensure_ascii=False), file=sys.stderr)
        return 2
    except (ValueError, db.SchemaVersionError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
