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


def summary_report() -> Path:
    """LLM 全库叙述总结:视频一览 + 主题综述 + 要点提炼 + 覆盖缺口。"""
    import json as _j

    from . import llm

    con = db.connect()
    try:
        videos = [dict(r) for r in con.execute(
            "SELECT video_id, title, author, duration_ms, source_platform "
            "FROM video_asset ORDER BY imported_at")]
        atoms = db.all_atoms(con)
        clusters = [dict(r) for r in con.execute(
            "SELECT * FROM knowledge_cluster ORDER BY cluster_id")]
        conflicts = [dict(r) for r in con.execute(
            "SELECT * FROM conflict_case ORDER BY conflict_id")]
    finally:
        con.close()

    by_video = defaultdict(list)
    atom_by_id = {}
    for a in atoms:
        atom_by_id[a["id"]] = a
        vid = a["evidence_refs"][0]["video_id"] if a["evidence_refs"] else "?"
        by_video[vid].append(a)
    vidx = {v["video_id"]: i + 1 for i, v in enumerate(videos)}
    polarity_cn = {"require": "必须", "recommend": "建议", "avoid": "避免"}

    def _clip(s, n):
        s = str(s or "").strip().replace("\n", " ")
        return s if len(s) <= n else s[:n] + "…"

    mat = []
    for v in videos:
        i = vidx[v["video_id"]]
        mins = max(1, round((v["duration_ms"] or 0) / 60000))
        atoms_n = len(by_video.get(v["video_id"], []))
        mat.append(f"[{i}] ({v.get('source_platform') or '?'} · {mins}分钟 · {atoms_n}条原子) "
                   f"{_clip(v['title'], 60)} —— 作者:{v.get('author') or '未知'}")
        for a in by_video.get(v["video_id"], []):
            mark = polarity_cn.get(a.get("polarity"), "陈述")
            stage = a.get("stage") or a.get("category") or "未分类"
            mat.append(f"  - [{mark}/{stage}] {_clip(a['claim'], 110)}")

    cl = []
    for c in clusters:
        members = _j.loads(c["members_json"] or "[]")
        vids = sorted({vidx.get(atom_by_id[aid]["evidence_refs"][0]["video_id"], 0)
                       for aid in members if aid in atom_by_id
                       and atom_by_id[aid]["evidence_refs"]})
        vs = ",".join(f"[{x}]" for x in vids if x) or "-"
        cl.append(f"- {c['canonical_topic']}({c['relation']}, {len(members)}条) 涉及视频 {vs}")

    cf = []
    for c in conflicts:
        a = _j.loads(c["side_a_json"] or "{}")
        b = _j.loads(c["side_b_json"] or "{}")
        cf.append(f"- [{c['ctype']}/{c['status']}] "
                  f"A:{_clip(a.get('claim'), 70)} ⇄ B:{_clip(b.get('claim'), 70)} | "
                  f"判定:{_clip(c['recommended_action'], 60)}")

    nl = "\n"
    system = ("你是装修知识库的分析员。只依据给定材料写报告,不编造视频中不存在的内容;"
              "引用视频一律用[编号]。输出简体中文 Markdown,不要任何开场白和结束语。")
    user = f"""以下是装修知识库从 {len(videos)} 个B站/抖音装修视频抽取的知识原子。极性:必须/建议/避免;阶段为施工环节。

## 视频与原子材料
{nl.join(mat)}

## 跨视频聚类(观点归组)
{nl.join(cl) or "(无)"}

## 已识别冲突
{nl.join(cf) or "(无)"}

请输出「全库内容总结」报告,固定结构(不要输出一级标题,从二级标题开始):
## 一、视频一览
逐个视频一行:`[编号] 一句话:这个视频讲了什么、最有价值的点`。{len(videos)} 个视频全部覆盖,不得遗漏。
## 二、主题综述
归纳为若干施工主题(防水/水电/瓦工/墙面/验收/预算合同等),每个主题一小段:主流做法、跨视频分歧(引用双方[编号]与适用条件)、被多次提到的关键数值参数。
## 三、全库要点提炼
8~12条最重要结论,每条一句话;存在分歧的注明双方与条件。
## 四、覆盖缺口
列出3~6个尚未覆盖或覆盖薄弱的常见装修主题,并说明判断依据。"""

    content, used = llm.chat(
        [{"role": "system", "content": system},
         {"role": "user", "content": user}],
        model=config.get("summary_model") or None,
        temperature=0.3, max_tokens=8000, json_mode=False)

    header = (f"# 全库总结(自动生成)\n\n"
              f"- 生成时间:{time.strftime('%Y-%m-%d %H:%M')} | 模型:{used}\n"
              f"- 视频:{len(videos)} | 原子:{len(atoms)} | 聚类:{len(clusters)} | 冲突:{len(conflicts)}\n\n")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    p = OUT_DIR / "summary.md"
    p.write_text(header + content.strip() + "\n", encoding="utf-8")
    return p


def run_all(video_ids=None):
    d = diff_report(video_ids)
    checklist_report()
    conflict_report()
    try:
        summary_report()
        d["summary"] = "ok"
    except Exception as e:  # noqa: BLE001
        d["summary"] = f"ERROR: {str(e)[:200]}"
    return d
