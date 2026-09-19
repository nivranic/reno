# -*- coding: utf-8 -*-
"""Reports: incremental diff per new video batch + deliverables (checklist,
conflict report, decisions digest). Markdown output under docs/generated/."""
import time
from collections import defaultdict
from pathlib import Path

from . import config, db

OUT_DIR = config.ROOT / "docs" / "generated"


def diff_report(video_ids=None) -> dict:
    con = db.connect()
    try:
        all_atoms = db.all_atoms(con)
        by_video = defaultdict(list)
        for a in all_atoms:
            vid = a["evidence_refs"][0]["video_id"] if a["evidence_refs"] else "?"
            by_video[vid].append(a)
        clusters = [dict(r) for r in con.execute("SELECT * FROM knowledge_cluster")]
        members = {}
        import json as _j
        for c in clusters:
            for m in _j.loads(c["members_json"] or "[]"):
                members[m] = c["cluster_id"]
        conflicts = [dict(r) for r in con.execute("SELECT * FROM conflict_case")]

        order = [dict(r) for r in con.execute(
            "SELECT video_id, imported_at FROM video_asset ORDER BY imported_at")]
        lines = ["# 增量 diff 报告", "",
                 f"生成时间:{time.strftime('%Y-%m-%d %H:%M')}",
                 f"视频总数:{len(order)} | 原子总数:{len(all_atoms)} | "
                 f"聚类:{len(clusters)} | 冲突:{len(conflicts)}", ""]
        seen_clusters = set()
        n_conf = 0
        for o in order:
            vid = o["video_id"]
            if video_ids and vid not in video_ids:
                # still count prior clusters as seen
                for a in by_video.get(vid, []):
                    if a["id"] in members:
                        seen_clusters.add(members[a["id"]])
                continue
            atoms = by_video.get(vid, [])
            new_cl, sup_cl, dup_cl = 0, 0, 0
            for a in atoms:
                cid = members.get(a["id"])
                if not cid or cid not in seen_clusters:
                    new_cl += 1
                elif True:
                    sup_cl += 1
                if cid:
                    seen_clusters.add(cid)
            lines.append(f"## {vid}")
            lines.append(f"- 原子 {len(atoms)}:全新观点 {new_cl} / 补充已有观点 {sup_cl} / 重复 {dup_cl}")
            lines.append("")
        new_atoms = len(all_atoms)
        info_gain = new_atoms and round(
            sum(1 for a in all_atoms if members.get(a["id"])) / new_atoms, 2) or 0
        lines.append(f"**信息增益(聚类覆盖率)**:{info_gain} —— "
                     "新增视频若 90%+ 原子落入已有聚类,继续收藏同类视频的边际价值低。")
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "incremental_diff.md").write_text("\n".join(lines), encoding="utf-8")
        return {"videos": len(order), "atoms": new_atoms, "clusters": len(clusters),
                "conflicts": len(conflicts), "cluster_coverage": info_gain}
    finally:
        con.close()


def checklist_report() -> Path:
    con = db.connect()
    try:
        atoms = db.all_atoms(con)
        by_stage = defaultdict(list)
        for a in atoms:
            if a["polarity"] in ("recommend", "require", "avoid") and a.get("confidence", 0) >= 0.55:
                by_stage[a.get("stage") or a["category"] or "其他"].append(a)
        lines = ["# 施工与验收 Checklist(自动生成)", ""]
        for stage in sorted(by_stage):
            lines.append(f"## {stage}")
            lines.append("")
            for a in sorted(by_stage[stage], key=lambda x: -x.get("confidence", 0)):
                mark = {"require": "[必须]", "recommend": "[建议]",
                        "avoid": "[避免]"}.get(a["polarity"], "[ ]")
                cond = a.get("conditions") or {}
                cs = ";".join(f"{k}={v}" for k, v in cond.items()
                              if k != "authority_level") if cond else ""
                ev = a["evidence_refs"][0] if a["evidence_refs"] else None
                src = f"_{ev['video_id']}@{ev['start_ms']//1000}s_" if ev else ""
                lines.append(f"- {mark} {a['claim']}"
                             + (f"(条件:{cs})" if cs else "")
                             + (f" [来源 {src}]" if src else ""))
            lines.append("")
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        p = OUT_DIR / "checklist.md"
        p.write_text("\n".join(lines), encoding="utf-8")
        return p
    finally:
        con.close()


def conflict_report() -> Path:
    con = db.connect()
    try:
        import json as _j
        rows = [dict(r) for r in con.execute("SELECT * FROM conflict_case ORDER BY conflict_id")]
        lines = ["# 争议事项报告(自动生成)", ""]
        for c in rows:
            a = _j.loads(c["side_a_json"] or "{}")
            b = _j.loads(c["side_b_json"] or "{}")
            an = _j.loads(c["analysis_json"] or "{}")
            lines += [f"## {c['conflict_id']} · {c['ctype']} · {c['status']}", "",
                      f"- 观点A:{a.get('claim')}({a.get('video')})",
                      f"- 观点B:{b.get('claim')}({b.get('video')})",
                      f"- 条件重叠:{an.get('conditional_conclusion', {}).get('condition_overlap')}",
                      f"- 权威差异:{an.get('conditional_conclusion', {}).get('authority_gap')}",
                      f"- 判定理由:{an.get('judge_note')}",
                      f"- 建议动作:{c['recommended_action']}", ""]
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        p = OUT_DIR / "conflicts.md"
        p.write_text("\n".join(lines), encoding="utf-8")
        return p
    finally:
        con.close()


def run_all(video_ids=None):
    d = diff_report(video_ids)
    checklist_report()
    conflict_report()
    return d
