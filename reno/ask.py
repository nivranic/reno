# -*- coding: utf-8 -*-
"""Ask: RAG-style Q&A grounded in the atom knowledge base.

Retrieval = FTS5 over atom_fts (phrase first, 2-char-shingle OR + ascii prefix
fallback, ranked by bm25), then conflicting-cluster sibling augmentation so
both sides of a dispute reach the model. Answer = one grounded LLM call with
[n] citations pointing at evidence timestamps. Stateless multi-turn: the
caller passes trimmed history; each turn re-retrieves from scratch.
"""
import json

from . import config, db, llm


def retrieve(con, question: str, k: int = 24) -> list[dict]:
    """Top-k atoms for a question, best first (bm25)."""
    from .db import fts_prep

    def _match(query: str, limit: int) -> list[str]:
        try:
            cur = con.execute(
                "SELECT atom_id FROM atom_fts WHERE atom_fts MATCH ? "
                "ORDER BY rank LIMIT ?", (query, limit))
            return [r["atom_id"] for r in cur.fetchall()]
        except Exception:  # noqa: BLE001 - malformed query -> no hits
            return []

    atoms = {a["id"]: a for a in db.all_atoms(con)}
    phrase = fts_prep(question).replace('"', " ").strip()
    hits: list[str] = []
    if phrase:
        hits = _match('"' + phrase + '"', k * 2)
    if len(hits) < k:
        chars = [c for c in question if "\u4e00" <= c <= "\u9fff"]
        terms = [f'"{chars[i]} {chars[i + 1]}"' for i in range(len(chars) - 1)]
        import re as _re
        terms += [t + "*" for t in _re.findall(r"[A-Za-z0-9]{2,}", question)]
        if terms:
            for aid in _match(" OR ".join(terms[:48]), k * 3):
                if aid not in hits:
                    hits.append(aid)
    return [atoms[aid] for aid in hits if aid in atoms][:k]


def _augment_conflicts(con, atoms: list[dict], cap: int = 12) -> tuple[list[dict], list[dict]]:
    """Pull sibling atoms of conflicting clusters so both sides are in context.
    Returns (extra_atoms, cluster_summaries)."""
    if not atoms:
        return [], []
    ids = {a["id"] for a in atoms}
    seen_cids = {a.get("cluster_id") for a in atoms}
    if not any(seen_cids):
        return [], []
    extra, summaries = [], []
    all_atoms = {a["id"]: a for a in db.all_atoms(con)}
    for c in con.execute("SELECT * FROM knowledge_cluster WHERE relation='conflicting'"):
        if c["cluster_id"] not in seen_cids:
            continue
        members = json.loads(c["members_json"] or "[]")
        summaries.append({"cluster_id": c["cluster_id"],
                          "topic": c["canonical_topic"],
                          "conflict_id": c["linked_conflict"]})
        for aid in members:
            if aid in ids or len(extra) >= cap:
                continue
            a = all_atoms.get(aid)
            if a:
                extra.append(a)
    return extra, summaries


def ask(question: str, history: list[dict] | None = None, k: int = 24) -> dict:
    """Answer a question with cited atoms. Returns {answer, refs, conflicts}."""
    question = (question or "").strip()
    if not question:
        raise ValueError("question required")

    con = db.connect()
    try:
        retrieved = retrieve(con, question, k=k)
        extra, conflict_summaries = _augment_conflicts(con, retrieved)
        seen_ids = {a["id"] for a in retrieved}
        atoms = retrieved + [a for a in extra if a["id"] not in seen_ids]
        titles = {r["video_id"]: dict(r) for r in con.execute(
            "SELECT video_id, title, source_platform FROM video_asset")}
    finally:
        con.close()

    if not atoms:
        return {"answer": "知识库里没有检索到与这个问题相关的原子。"
                          "可以先换一组关键词试试,或去采集页补充相关视频。",
                "refs": [], "conflicts": conflict_summaries}

    refs = []
    lines = []
    for i, a in enumerate(atoms, 1):
        ev = a["evidence_refs"][0] if a["evidence_refs"] else None
        vid = ev["video_id"] if ev else None
        refs.append({
            "n": i, "atom_id": a["id"], "claim": a["claim"],
            "video": vid, "video_title": titles.get(vid, {}).get("title", "") if vid else "",
            "ms": ev["start_ms"] if ev else 0,
            "modality": ev["modality"] if ev else None,
            "polarity": a.get("polarity"), "stage": a.get("stage") or a.get("category"),
        })
        pol = {"require": "必须", "recommend": "建议", "avoid": "避免"}.get(
            a.get("polarity"), "陈述")
        stage = a.get("stage") or a.get("category") or "未分类"
        src = ""
        if vid:
            t = int((ev["start_ms"] or 0) / 1000)
            src = f" | 来源[{vid}]{t // 60:02d}:{t % 60:02d}({ev['modality'] or '?'})"
        lines.append(f"[{i}] [{pol}/{stage}] {a['claim']}{src}")

    vid_lines = [f"- {vid}: {info['title']}({info.get('source_platform') or '?'})"
                 for vid, info in titles.items()]

    hist = []
    for m in (history or [])[-4:]:
        role = m.get("role")
        if role in ("user", "assistant") and m.get("content"):
            hist.append({"role": role, "content": str(m["content"])[:800]})

    conflict_note = ""
    if conflict_summaries:
        topics = ";".join(s["topic"] for s in conflict_summaries)
        conflict_note = (f"\n注意:本次材料包含存在分歧的聚类({topics}),"
                         "回答这些主题时必须同时呈现双方观点与各自适用条件。\n")

    system = ("你是装修知识库的问答助手。只依据给定材料回答,不编造;每个事实性陈述的句尾"
              "用[n]标注所依据的原子编号;材料里有分歧的主题要同时呈现双方观点与适用条件;"
              "材料不足以完整回答时,如实说明已答部分与缺失部分。输出简体中文 Markdown,"
              "不要开场白和结束语。")
    user = f"""## 视频索引
{chr(10).join(vid_lines)}

## 检索到的知识原子(编号即引用编号)
{chr(10).join(lines)}
{conflict_note}
## 对话历史(供理解追问语境,引用仍只来自本轮材料)
{chr(10).join(f'{"用户" if m["role"] == "user" else "助手"}: {m["content"]}' for m in hist) or "(无)"}

## 当前问题
{question}"""

    answer, _model = llm.chat(
        [{"role": "system", "content": system},
         {"role": "user", "content": user}],
        model=config.get("ask_model") or None,
        temperature=0.3, max_tokens=3000, json_mode=False)

    cited = {n for n in _cited_ns(answer) if n <= len(refs)}
    ordered_refs = [r for r in refs if r["n"] in cited] + \
                   [r for r in refs if r["n"] not in cited]
    return {"answer": answer.strip(), "refs": ordered_refs,
            "conflicts": conflict_summaries}


def _cited_ns(answer: str) -> list[int]:
    import re
    ns = []
    for m in re.finditer(r"\[(\d{1,3})\]", answer or ""):
        n = int(m.group(1))
        if n not in ns:
            ns.append(n)
    return ns
