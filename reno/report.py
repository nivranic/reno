# -*- coding: utf-8 -*-
"""Reports: incremental diff per new video batch + deliverables (checklist,
conflict report, decisions digest). Markdown output under <data>/generated/."""
import time
import contextvars
from contextlib import contextmanager
from functools import wraps
import hashlib
import json
import os
import uuid
from collections import defaultdict
from pathlib import Path

from . import config, db, derivation, model_scopes, storage_admin, storage_artifacts, storage_lifecycle as lifecycle
from .sync_protocol import SyncError
from .budget_contract import BudgetDeferred

REPORTS = ("incremental_diff", "checklist", "conflicts", "prices", "summary")
POLICY_VERSION = "reports-v3"
_generation = contextvars.ContextVar("reno_report_generation", default=None)
_recovery_projection = contextvars.ContextVar("reno_report_recovery_projection", default=False)

# reports live under the data dir (RENO_DATA_DIR aware), NOT the git-tracked
# repo checkout: a cloud deploy's git reset --hard would wipe repo copies, and
# the sync pipeline ships them from the home node (docs/sync-plan.md §3.3).
# Resolved per call (not module-level) so a patched/moved DATA dir is honored.
def out_dir(*, profile=None) -> Path:
    if profile is not None:
        return profile.reports
    generation = _generation.get()
    if generation is not None:
        return generation["profile"].reports
    active = lifecycle.current_guard()
    return active.profile.reports if active is not None else config.DATA / "generated"


def _profile(value=None):
    if value is not None:
        return value
    guard = lifecycle.current_guard()
    if guard is not None:
        return guard.profile
    generation = _generation.get()
    if generation is not None:
        return generation["profile"]
    from .worker import _execution_profile
    value = _execution_profile()
    active_execution = model_scopes.current_execution()
    return value or (active_execution.profile if active_execution is not None else storage_admin.profile())


@contextmanager
def _access(*, con=None, profile=None):
    value = _profile(profile)
    if con is not None:
        # An existing connection belongs to its caller: never supplement its
        # transaction with a late OS lock, commit it, or roll it back.
        lifecycle.require_guard(value)
        storage_admin.from_connection(con, profile=value)
        yield con, value
        return
    with lifecycle.shared(value):
        owned = db.connect(value.database)
        try:
            owned.execute("BEGIN")
            yield owned, value
        finally:
            owned.close()


def _atomic_write(path, content):
    from .sync_media import _Directory, _native_path, _rename_new
    value = _profile()
    guard = lifecycle.require_guard(value)
    path = Path(path).absolute()
    relative = path.relative_to(value.reports).as_posix()
    immutable = relative.startswith(".versions/")
    if immutable:
        storage_artifacts.validate_storage_key("local_report", relative)
    elif relative not in {name + suffix for name in REPORTS for suffix in (".md", ".manifest.json")}:
        raise lifecycle.StorageLifecycleError("STORAGE_UNSAFE_PATH", 503)
    with _Directory(path.parent, create=True, base=value.reports) as directory:
        temporary = f".{path.name}.{uuid.uuid4().hex}.tmp"
        identity = None
        try:
            with directory.open_file(temporary, write=True, create=True) as stream:
                info = os.fstat(stream.fileno())
                identity = info.st_dev, info.st_ino
                stream.write(content.encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            directory.verify()
            if immutable:
                _rename_new(directory, temporary, directory, path.name)
            elif os.name == "nt":
                os.replace(_native_path(directory.path / temporary), _native_path(path))
            else:
                os.replace(temporary, path.name, src_dir_fd=directory.fd, dst_dir_fd=directory.fd)
            if os.name != "nt":
                os.fsync(directory.fd)
            guard.verify()
        finally:
            if identity is not None:
                try:
                    info = directory.entry_stat(temporary)
                    if (info.st_dev, info.st_ino) == identity and info.st_nlink == 1:
                        directory.unlink(temporary)
                except FileNotFoundError:
                    pass


def _manifest(name, *, con=None, profile=None):
    if name not in REPORTS:
        return {}
    from .sync_media import _Directory
    with _access(con=con, profile=profile) as (con, value):
        if config.is_cloud():
            from . import sync_reports
            manifest = sync_reports.manifest(con, name, profile=value)
            if manifest is not None:
                return manifest
        try:
            with _Directory(value.reports, base=value.reports) as directory:
                with directory.open_file(f"{name}.manifest.json") as stream:
                    raw = stream.read(256 * 1024 + 1)
                if len(raw) > 256 * 1024:
                    return {}
                manifest = json.loads(raw.decode("utf-8"))
                directory.verify()
            return manifest if isinstance(manifest, dict) else {}
        except (OSError, ValueError):
            return {}


def _set_status(name, status, *, error_class=None, budget_wait=None):
    from .tasks import utc_now
    with lifecycle.shared(_profile()):
        previous = _manifest(name)
        previous.setdefault("policy_version", POLICY_VERSION)
        previous.update(report_type=name, status=status,
                        last_attempt_at=utc_now(), error_class=error_class)
        if budget_wait is None:
            previous.pop("budget_wait", None)
        else:
            previous["budget_wait"] = budget_wait
        _atomic_write(out_dir() / f"{name}.manifest.json", json.dumps(previous, ensure_ascii=False, indent=2) + "\n")


def _managed(name):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            from .tasks import require_local
            require_local()
            value = _profile(kwargs.pop("profile", None))
            token = _generation.set({"name": name, "basis": None, "profile": value})
            try:
                _set_status(name, "generating")
                return function(*args, **kwargs)
            except BudgetDeferred as exc:
                try:
                    _set_status(name, "waiting", budget_wait=exc.public())
                except Exception:
                    pass
                raise
            except Exception as exc:
                try:
                    _set_status(name, "failed", error_class=type(exc).__name__)
                except Exception:
                    pass  # Preserve the original failure if status storage also failed.
                raise
            finally:
                _generation.reset(token)
        return wrapped
    return decorate


@contextmanager
def _read_connection():
    from .sync_reports import event_fingerprint
    from .sync_recovery_projection import processed_view
    from contextlib import nullcontext
    with _access(profile=_profile()) as (con, value):
        with processed_view(con) if _recovery_projection.get() else nullcontext(con):
            _generation.get()["basis"] = db.knowledge_version(con)
            _generation.get()["review_events_sha256"] = event_fingerprint(con)
            _generation.get()["derivation_states"] = derivation.evaluate_relations(con)
            _generation.get()["derivation_summary"] = derivation.summary(con)
            yield con


def _derivation_notice(summary, *, current=False):
    """A visible qualifier is part of the exported text, not just UI metadata."""
    attention = sum(group.get(state, 0) for group in summary.values()
                    for state in ("stale", "missing", "unknown"))
    if not attention:
        return ""
    heading = "当前关联判定依据" if current else "生成时的关联判定依据"
    lines = [f"> **{heading}：需要更新。**"]
    for key, label in (("clusters", "聚类"), ("conflicts", "冲突")):
        group = summary[key]
        lines.append(f"> {label}：依据匹配 {group['active']}；已变化 {group['stale']}；"
                     f"成员缺失 {group['missing']}；旧记录无法确认 {group['unknown']}。")
    qualification = ("下方正文保留生成时内容，未因本提示重算；请仅将依据匹配的判定用于当前归纳。"
                     if current else "历史关系保留供核对；统计和归纳仅采用依据匹配的判定。")
    lines.append("> " + qualification +
                 "请显式更新争议后再生成报告，更新争议会调用模型；"
                 "尚无匹配判定不表示观点一致。")
    return "\n".join(lines) + "\n\n"


def _eligible_atoms(con):
    from . import review
    return [a for a in review.annotate_atoms(con, db.all_atoms(con))
            if a.get("review_status") != "user_rejected"]


def _publish(name, content):
    from .tasks import utc_now
    basis = _generation.get()["basis"]
    relation_summary = _generation.get()["derivation_summary"]
    content = _derivation_notice(relation_summary) + content
    if _recovery_projection.get():
        content = '> **恢复同步资料：**仅包含已处理视频；未完成处理和关联未纳入本报告。\n\n' + content
    value = _profile()
    data = content.encode("utf-8")
    from .sync_reports import MAX_REPORT_BYTES
    if len(data) > MAX_REPORT_BYTES:
        from .sync_protocol import SyncError
        raise SyncError("Report is too large", "SYNC_REPORT_TOO_LARGE", 413)
    with lifecycle.shared(value):
        con = db.connect(value.database)
        relative = f".versions/{name}-{uuid.uuid4().hex}.md"
        try:
            con.execute("BEGIN IMMEDIATE")
            current = db.knowledge_version(con)
            _atomic_write(value.reports / relative, content)
            storage_artifacts.register(con, profile=value, namespace="local_report", root=value.reports,
                storage_key=relative, sha256=hashlib.sha256(data).hexdigest(), byte_size=len(data))
            con.commit()
        finally:
            con.close()
        # SQLite and manifest replacement cannot form one atomic commit. The
        # artifact commits first under the same shared guard; failed SQL leaves
        # an unregistered orphan, failed manifest leaves a registered orphan.
        # Both files remain for conservative later reconciliation, and the old
        # manifest stays authoritative. No failure path unlinks report content.
        manifest = {"report_type": name, "generated_at": utc_now(), **basis,
                    "review_events_sha256": _generation.get().get("review_events_sha256"),
                    "derivation_summary": relation_summary, "policy_version": POLICY_VERSION,
                    "status": "latest" if basis == current else "stale",
                    "content_file": relative, "content_sha256": hashlib.sha256(data).hexdigest(),
                    "error_class": None}
        if _recovery_projection.get():
            manifest['knowledge_scope'] = 'recovery_processed'
        _atomic_write(value.reports / f"{name}.manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        path = value.reports / f"{name}.md"
        try:
            _atomic_write(path, content)
        except OSError:
            pass  # The compatibility mirror is not the authoritative pointer.
        return path


def read_report(name, *, manifest=None, con=None, profile=None):
    if name not in REPORTS:
        return None
    with _access(con=con, profile=profile) as (con, value):
        manifest = _manifest(name, con=con, profile=value) if manifest is None else manifest
        if config.is_cloud():
            from . import sync_visibility
            if not sync_visibility.report_visible(con, manifest):
                return None
        return _read_report_content(name, manifest, profile=value)


def _read_report_content(name, manifest, *, profile=None):
    value = _profile(profile)
    lifecycle.require_guard(value)
    if manifest.get("synced"):
        from . import sync_reports
        try:
            return sync_reports.read_content(value.reports, manifest, profile=value)
        except SyncError:
            return None
    filename = manifest.get("content_file")
    path = value.reports / f"{name}.md"
    if filename:
        if not isinstance(filename, str):
            return None
        try:
            storage_artifacts.validate_storage_key("local_report", filename)
        except ValueError:
            return None
        path = value.reports / filename
    try:
        from .sync_media import _Directory
        from .sync_reports import MAX_REPORT_BYTES
        with _Directory(path.parent, base=value.reports) as directory:
            with directory.open_file(path.name) as stream:
                data = stream.read(MAX_REPORT_BYTES + 1)
            if len(data) > MAX_REPORT_BYTES:
                return None
            directory.verify()
        content = data.decode("utf-8")
    except (OSError, UnicodeError, SyncError):
        return None
    expected_hash = manifest.get("content_sha256")
    if expected_hash and hashlib.sha256(content.encode("utf-8")).hexdigest() != expected_hash:
        return None
    return content


def present_report(name, *, con=None, profile=None):
    """Qualify browser/copy/download text without altering immutable artifacts."""
    with _access(con=con, profile=profile) as (con, value):
        manifest = _manifest(name, con=con, profile=value)
        content = read_report(name, manifest=manifest, con=con, profile=value)
        if content is None:
            return None
        current = db.knowledge_version(con)
        stale = ({key: manifest.get(key) for key in current} != current
                 or manifest.get("policy_version") != POLICY_VERSION
                 or manifest.get("source_reviews_match") is False)
        # The immutable projected body already carries its scope notice.
        # A later knowledge/review change still needs the independent history
        # qualifier; scope must not hide stale-version provenance.
        prefix = ("> **历史报告：**正文使用的知识版本、复核或生成规则已变化；"
                  "请更新报告后再用于当前判断。\n\n") if stale else ""
        prefix += _derivation_notice(derivation.summary(con, public=True), current=True)
        return prefix + content


def metadata(*, con=None, profile=None):
    with _access(con=con, profile=profile) as (con, value):
        current = db.knowledge_version(con)
        relation_summary = derivation.summary(con, public=True)
        queued = set() if config.is_cloud() else {
            db.uj(row["payload_json"], {}).get("mode", "static") for row in con.execute(
                "SELECT payload_json FROM job WHERE kind='report' AND status IN ('pending','running')")}
        result = {}
        for name in REPORTS:
            manifest = _manifest(name, con=con, profile=value)
            has_content = read_report(name, manifest=manifest, con=con, profile=value) is not None
            basis = {key: manifest.get(key) for key in current}
            stale = has_content and (basis != current or manifest.get("policy_version") != POLICY_VERSION
                                     or manifest.get("source_reviews_match") is False
                                     or manifest.get('knowledge_scope') == 'recovery_processed')
            status = manifest.get("status", "stale" if has_content else "missing")
            if status not in ("failed", "generating", "waiting"):
                status = "stale" if stale else "latest" if has_content else "missing"
            if status != "waiting" and ((name == "summary" and "summary" in queued) or (name != "summary" and "static" in queued)):
                status = "generating"
            result[name] = {"report_type": name, "status": status, "has_content": has_content,
                            "stale": stale, "generated_at": manifest.get("generated_at"),
                            "kb_revision": manifest.get("kb_revision"), "policy_version": manifest.get("policy_version"),
                            "derivation_summary": relation_summary,
                            "error_class": manifest.get("error_class")}
            if manifest.get("budget_wait") is not None:
                result[name]["budget_wait"] = manifest["budget_wait"]
            if manifest.get("synced"):
                result[name].update(source_version=manifest["source_version"],
                                    publication_id=manifest["publication_id"])
        return result


@_managed("incremental_diff")
def diff_report(video_ids=None) -> dict:
    with _read_connection() as con:
        all_atoms = db.all_atoms(con)
        by_video = defaultdict(list)
        for a in all_atoms:
            vid = a["evidence_refs"][0]["video_id"] if a["evidence_refs"] else "?"
            by_video[vid].append(a)
        states = _generation.get()["derivation_states"]
        clusters = [dict(r) for r in con.execute("SELECT * FROM knowledge_cluster")
                    if states["clusters"][r["cluster_id"]]["state"] == "active"]
        members = {}
        import json as _j
        for c in clusters:
            for m in _j.loads(c["members_json"] or "[]"):
                members[m] = c["cluster_id"]
        conflicts = [dict(r) for r in con.execute("SELECT * FROM conflict_case")
                     if states["conflicts"][r["conflict_id"]]["state"] == "active"]

        order = [dict(r) for r in con.execute(
            "SELECT video_id, imported_at FROM video_asset ORDER BY imported_at")]
        lines = ["# 增量 diff 报告", "",
                 f"生成时间:{time.strftime('%Y-%m-%d %H:%M')}",
                 f"视频总数:{len(order)} | 原子总数:{len(all_atoms)} | "
                 f"依据匹配聚类:{len(clusters)} | 依据匹配冲突:{len(conflicts)}", ""]
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
            new_cl, sup_cl, unknown_cl = 0, 0, 0
            for a in atoms:
                cid = members.get(a["id"])
                if not cid:
                    unknown_cl += 1
                elif cid not in seen_clusters:
                    new_cl += 1
                else:
                    sup_cl += 1
                if cid:
                    seen_clusters.add(cid)
            lines.append(f"## {vid}")
            lines.append(f"- 原子 {len(atoms)}:首次出现的已判定观点 {new_cl} / "
                         f"补充已有判定 {sup_cl} / 尚未确认关联 {unknown_cl}")
            lines.append("")
        new_atoms = len(all_atoms)
        info_gain = new_atoms and round(
            sum(1 for a in all_atoms if members.get(a["id"])) / new_atoms, 2) or 0
        lines.append(f"**信息增益(聚类覆盖率)**:{info_gain} —— "
                     "仅统计依据匹配的聚类；未归组或待更新的原子不能据此视为新观点。")
    _publish("incremental_diff", "\n".join(lines))
    return {"videos": len(order), "atoms": new_atoms, "clusters": len(clusters),
            "conflicts": len(conflicts), "cluster_coverage": info_gain}


@_managed("checklist")
def checklist_report() -> Path:
    """v2: grouped by the DERIVED controlled stage; acceptance atoms get the
    full inspection structure (stage/object/conditions/method/criteria).
    Acceptance items without numeric parameters are flagged 判定依据待核实 —
    the model must not invent thresholds."""
    with _read_connection() as con:
        atoms = _eligible_atoms(con)
        by_stage = defaultdict(list)
        for a in atoms:
            if a["polarity"] in ("recommend", "require", "avoid", "optional") \
                    and (a.get("confidence") or 0) >= 0.55:
                by_stage[a.get("stage") or "未分类"].append(a)
        lines = ["# 施工与验收 Checklist(自动生成)", "",
                 f"生成时间:{time.strftime('%Y-%m-%d %H:%M')}"
                 " | 判定依据缺失的验收项标注「待核实」,不代表库中无此要求。", ""]
        for stage in sorted(by_stage):
            lines.append(f"## {stage}")
            lines.append("")
            check_atoms = [a for a in by_stage[stage]
                           if a.get("dimension") == "验收质检" or a.get("category") == "验收"]
            other = [a for a in by_stage[stage]
                     if a not in check_atoms]
            for a in sorted(check_atoms, key=lambda x: -x.get("confidence", 0)):
                mark = {"require": "[必须检查]", "recommend": "[建议检查]",
                        "avoid": "[禁止]", "optional": "[可选]"}.get(
                            a["polarity"], "[检查]")
                criteria = ";".join(
                    f"{p.get('name')}={p.get('value')}{p.get('unit') or ''}"
                    for p in a.get("parameters") or [])
                basis = (f"判定依据:{criteria}" if criteria
                         else "判定依据:**待核实**(视频中未给出可执行阈值)")
                cond = a.get("conditions") or {}
                cs = ";".join(f"{k}={v}" for k, v in cond.items()
                              if k not in ("authority_level", "_notes")) if cond else ""
                ev = a["evidence_refs"][0] if a["evidence_refs"] else None
                src = f"`{ev['video_id']}@{ev['start_ms'] // 1000}s`" if ev else ""
                nature_cn = {"cited_standard": "转述标准", "author_test": "实测"}.get(
                    a.get("evidence_nature"), a.get("evidence_nature") or "经验观点")
                lines.append(f"- {mark} **{a['claim']}**"
                             + (f"(对象条件:{cs})" if cs else "")
                             + f" | {basis}"
                             + f" | 证据性质:{nature_cn}"
                             + (f" | 来源 {src}" if src else ""))
            for a in sorted(other, key=lambda x: -x.get("confidence", 0)):
                mark = {"require": "[必须]", "recommend": "[建议]",
                        "avoid": "[避免]"}.get(a["polarity"], "[ ]")
                cond = a.get("conditions") or {}
                cs = ";".join(f"{k}={v}" for k, v in cond.items()
                              if k not in ("authority_level", "_notes")) if cond else ""
                ev = a["evidence_refs"][0] if a["evidence_refs"] else None
                src = f"_{ev['video_id']}@{ev['start_ms'] // 1000}s_" if ev else ""
                lines.append(f"- {mark} {a['claim']}"
                             + (f"(条件:{cs})" if cs else "")
                             + (f" [来源 {src}]" if src else ""))
            lines.append("")
    return _publish("checklist", "\n".join(lines))


@_managed("prices")
def prices_report() -> Path:
    """Structured price observations with their quoting basis. Entries missing
    kind/spec/region are shown AS-IS with the gap marked; nothing is inferred.
    With no data at all the report says so explicitly — no fabricated market
    conclusions (§8: 口径不可比不排序)."""
    with _read_connection() as con:
        atoms = _eligible_atoms(con)
        rows = []
        for a in atoms:
            for p in a.get("prices") or []:
                rows.append((a, p))
        lines = ["# 价格记录(自动生成)", "",
                 f"生成时间:{time.strftime('%Y-%m-%d %H:%M')}"
                 " | 全部来自视频口播/字幕,未经渠道核验;口径(规格/单位/含项/地区)"
                 "不同的记录不可直接比较。", ""]
        if not rows:
            lines.append("**暂无可核验价格数据。** 当前库内没有结构化价格记录;"
                         "含价格描述的原子可在搜索页按维度「报价采购」筛选查看原文。")
        else:
            lines.append("| 对象 | 金额 | 计价 | 口径类型 | 规格 | 地区/渠道 | 含项 | 时点 | 来源 |")
            lines.append("|---|---|---|---|---|---|---|---|---|")
            for a, p in rows:
                ev = a["evidence_refs"][0] if a["evidence_refs"] else None
                src = f"{ev['video_id']}@{ev['start_ms'] // 1000}s" if ev else "-"
                kind = p.get("price_kind") or "**未注明口径**"
                inc = ";".join(p.get("includes") or []) or "-"
                lines.append(
                    f"| {p.get('object')} | {p.get('amount')}{p.get('unit') or ''} "
                    f"| {p.get('basis') or '-'} | {kind} "
                    f"| {p.get('spec') or '未注明'} | {p.get('region') or '未注明'}/"
                    f"{p.get('channel') or '未注明'} | {inc} "
                    f"| {p.get('valid_at') or '未注明'} | {src} |")
            lines.append("")
            lines.append("> 源头成本、经销价与行情预测:当前无可靠数据来源,"
                         "不提供估算或预测,以免误导。")
    return _publish("prices", "\n".join(lines))


@_managed("conflicts")
def conflict_report() -> Path:
    with _read_connection() as con:
        import json as _j
        rows = [dict(r) for r in con.execute("SELECT * FROM conflict_case ORDER BY conflict_id")]
        states = _generation.get()["derivation_states"]["conflicts"]
        from . import review
        projected = review.projection(con)
        rows.sort(key=lambda row: (states[row["conflict_id"]]["state"] != "active", row["conflict_id"]))
        lines = ["# 争议事项报告(自动生成)", ""]
        for c in rows:
            validity = states[c["conflict_id"]]
            current = validity["state"] == "active"
            label = {"active": "判定依据匹配", "stale": "历史判定：依据已变化",
                     "missing": "历史判定：成员已缺失", "unknown": "历史判定：生成依据无法确认"}[validity["state"]]
            c["status"] = projected["conflicts"].get(c["conflict_id"], c["status"])
            a = _j.loads(c["side_a_json"] or "{}")
            b = _j.loads(c["side_b_json"] or "{}")
            an = _j.loads(c["analysis_json"] or "{}")
            conclusion = an.get("conditional_conclusion") or {}
            lines += [f"## {c['conflict_id']} · {c['ctype']} · {c['status']}", "",
                      f"- 关联有效性：{label}。" + ("" if current else "以下分析保留供回看，需更新后再用于当前判断。"),
                      f"- 观点A:{a.get('claim')}({a.get('video')})",
                      f"- 观点B:{b.get('claim')}({b.get('video')})",
                      f"- 条件重叠:{conclusion.get('condition_overlap')}",
                      f"- 权威差异:{conclusion.get('authority_gap')}",
                      f"- 判定理由:{an.get('judge_note')}",
                      f"- 建议动作:{c['recommended_action']}", ""]
    return _publish("conflicts", "\n".join(lines))


@_managed("summary")
def summary_report() -> Path:
    """LLM 全库叙述总结:视频一览 + 主题综述 + 要点提炼 + 覆盖缺口。"""
    import json as _j

    from . import llm

    with _read_connection() as con:
        videos = [dict(r) for r in con.execute(
            "SELECT video_id, title, author, duration_ms, source_platform "
            "FROM video_asset ORDER BY imported_at")]
        all_candidates = db.all_atoms(con)
        atoms = _eligible_atoms(con)
        rejected_ids = {a["id"] for a in all_candidates} - {a["id"] for a in atoms}
        states = _generation.get()["derivation_states"]
        clusters = [dict(r) for r in con.execute(
            "SELECT * FROM knowledge_cluster ORDER BY cluster_id")
            if states["clusters"][r["cluster_id"]]["state"] == "active"]
        conflicts = [dict(r) for r in con.execute(
            "SELECT * FROM conflict_case ORDER BY conflict_id")
            if states["conflicts"][r["conflict_id"]]["state"] == "active"]

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
        members = [aid for aid in members if aid not in rejected_ids]
        if not members:
            continue
        vids = sorted({vidx.get(atom_by_id[aid]["evidence_refs"][0]["video_id"], 0)
                       for aid in members if aid in atom_by_id
                       and atom_by_id[aid]["evidence_refs"]})
        vs = ",".join(f"[{x}]" for x in vids if x) or "-"
        cl.append(f"- {c['canonical_topic']}({c['relation']}, {len(members)}条) 涉及视频 {vs}")

    cf = []
    for c in conflicts:
        a = _j.loads(c["side_a_json"] or "{}")
        b = _j.loads(c["side_b_json"] or "{}")
        for side in (a, b):
            if set(side.get("atoms") or []) & rejected_ids:
                side["claim"] = "该侧含已人工驳回的知识，不作为推荐依据"
        cf.append(f"- [{c['ctype']}/{c['status']}] "
                  f"A:{_clip(a.get('claim'), 70)} ⇄ B:{_clip(b.get('claim'), 70)} | "
                  f"判定:{_clip(c['recommended_action'], 60)}")

    nl = "\n"
    system = ("你是装修知识库的分析员。只依据给定材料写报告,不编造视频中不存在的内容;"
              "历史关联待更新时，只依据当前原子归纳并说明限制；"
              "未提供依据匹配的聚类或冲突，不表示观点一致或没有分歧。"
              "引用视频一律用[编号]。输出简体中文 Markdown,不要任何开场白和结束语。")
    user = f"""以下是装修知识库从 {len(videos)} 个B站/抖音装修视频抽取的知识原子。极性:必须/建议/避免;阶段为施工环节。

## 视频与原子材料
{nl.join(mat)}

## 跨视频聚类(观点归组)
{nl.join(cl) or "(无)"}

## 已识别冲突
{nl.join(cf) or "(无)"}

## 关联判定覆盖说明
{json.dumps(_generation.get()['derivation_summary'], ensure_ascii=False)}
上述聚类和冲突仅包含依据匹配的当前判定。stale/missing/unknown 是保留但未用于当前推断的历史关系数量。

请输出「全库内容总结」报告,固定结构(不要输出一级标题,从二级标题开始):
## 一、视频一览
逐个视频一行:`[编号] 一句话:这个视频讲了什么、最有价值的点`。{len(videos)} 个视频全部覆盖,不得遗漏。
## 二、主题综述
归纳为若干施工主题(防水/水电/瓦工/墙面/验收/预算合同等),每个主题一小段:主流做法、跨视频分歧(引用双方[编号]与适用条件)、被多次提到的关键数值参数。
## 三、全库要点提炼
8~12条最重要结论,每条一句话;存在分歧的注明双方与条件。
## 四、覆盖缺口
列出3~6个尚未覆盖或覆盖薄弱的常见装修主题,并说明判断依据。"""

    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    model = config.get("summary_model") or None
    value = _profile()
    basis = _generation.get()["basis"]
    with model_scopes.execution(value, "summary", {"policy_version": POLICY_VERSION, "model": model}) as execution:
        with model_scopes.call(execution, "summary:all", version_tags=(
                ("prompt_version", POLICY_VERSION),
                ("library_epoch", str(basis["library_epoch"])),
                ("kb_revision", str(basis["kb_revision"])),
                ("frozen_input_sha256", hashlib.sha256(db.j(messages).encode()).hexdigest()))):
            content, used = llm.chat(messages, model=model, temperature=0.3, max_tokens=8000, json_mode=False)
        header = (f"# 全库总结(自动生成)\n\n"
                  f"- 生成时间:{time.strftime('%Y-%m-%d %H:%M')} | 模型:{used}\n"
                  f"- 视频:{len(videos)} | 原子:{len(atoms)} | 聚类:{len(clusters)} | 冲突:{len(conflicts)}\n\n")
        path = _publish("summary", header + content.strip() + "\n")
        if execution.consumer == "summary":
            with lifecycle.shared(value):
                con = db.connect(value.database)
                try:
                    con.execute("BEGIN IMMEDIATE")
                    model_scopes.checkpoint(con, execution, completed=True)
                    con.commit()
                finally:
                    con.close()
        return path


def run_all(video_ids=None, *, profile=None, recovery_projection=False):
    """Generate static reports only. AI summary is a separate explicit task."""
    if type(recovery_projection) is not bool:
        raise SyncError('Recovery projection flag must be boolean', 'SYNC_RECOVERY_PROJECTION_INVALID', 400)
    token = _recovery_projection.set(recovery_projection)
    try:
        d = diff_report(video_ids, profile=profile)
        checklist_report(profile=profile)
        conflict_report(profile=profile)
        prices_report(profile=profile)
        d["mode"] = "static"
        return d
    finally:
        _recovery_projection.reset(token)
