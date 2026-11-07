import { useState, type SetStateAction } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { appendCheck, checklistQuery, patchCheckItem, sourcesQuery, personalKeys, type CheckItem, type CheckItemInput, type CheckResult, type Checklist, type PersonalRead } from "@/lib/personal-api";
import { MutationNotice, useReceiptMutation } from "@/features/project/use-receipt-mutation";
import { usePersonalDraft } from "@/features/project/use-personal-draft";
import { conditionsText } from "@/features/project/project-context-editor";
import { AttachmentUploader } from "@/features/sources/attachment-uploader";
import { SourceEditorButton } from "@/features/sources/source-editor-button";
import { EvidenceSnapshot } from "@/features/sources/evidence-snapshot";
import { Field, Input, Select, Textarea } from "@/components/ui/field";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { BASIS_LABELS, CheckItemEditor, describeCheckItem } from "./check-item-editor";
import { useFieldDraft } from "./use-field-draft";
import { usePersonalProject } from "@/features/project/use-personal-project";

export const CHECK_RESULT_LABELS: Record<CheckResult, string> = { unchecked: "未核对", pass: "符合本项", fail: "不符合本项", unable_to_verify: "待核实", not_applicable: "不适用" };
type CheckItemCardProps = { item: CheckItem; checklist: Checklist; libraryId: string; epoch: number; writable: boolean; uploadable: boolean; read?: PersonalRead };
export function CheckItemCard(props: CheckItemCardProps) {
  const access = usePersonalProject();
  if (!props.writable || !access.fieldWritable || props.read?.mode === "mirror") return <ReadOnlyCheckItem item={props.item} />;
  return <WritableCheckItemCard key={props.read?.scope ?? access.scope} {...props} />;
}
function WritableCheckItemCard({ item, checklist, libraryId, epoch, writable, uploadable, read }: CheckItemCardProps) {
  const access = usePersonalProject();
  const qc = useQueryClient(); const [editOpen, setEditOpen] = useState(false);
  const sources = useQuery({ ...sourcesQuery(checklist.project_id, read ?? access.read), enabled: access.readable });
  const record = useFieldDraft(libraryId, item.item_id, { baseRevision: item.revision, criteriaHash: item.criteria_snapshot_hash, kind: "inspection", result: "unable_to_verify", note: "", observations: {}, checkedAt: "", attachments: [], sourceId: "" }, epoch, read?.scope ?? access.scope, (draft) => [
    `核对项：${item.title}`, `草稿基于版本：${draft.baseRevision}`, `记录类型：${({ inspection: "检查或复检", remediation: "整改说明", void: "作废错误记录" })[draft.kind]}`, `本项结果：${CHECK_RESULT_LABELS[draft.result]}`, `记录说明：${draft.note}`, ...Object.entries(draft.observations).map(([key, value], index) => `${item.criteria.required_observations.find((field) => field.key === key)?.label ?? `原观察 ${index + 1}`}：${value}`), `观察时间：${draft.checkedAt}`, `附件来源：${sources.data?.sources.find((source) => source.source_id === draft.sourceId)?.title ?? (draft.sourceId ? "已选择来源" : "未选择")}`, `选定附件：${draft.attachments.map((id) => sources.data?.sources.flatMap((source) => source.attachments).find((attachment) => attachment.attachment_id === id)?.original_name ?? "原选定附件").join("；")}`, `待上传文件：${draft.filePending ?? ""}`, `作废目标：${draft.voidTarget ? item.checks.find((check) => check.check_id === draft.voidTarget)?.checked_at ?? "原选定记录" : "未选择"}`].join("\n"));
  const { draft, patch } = record; const [needsReselect, setNeedsReselect] = useState(!!draft.filePending);
  const [formOpen, setFormOpen] = useState(!!draft.pendingRequest);
  const save = useReceiptMutation((body: Parameters<typeof appendCheck>[1], signal) => appendCheck(item.item_id, body, signal), (result) => {
    qc.setQueryData(checklistQuery(checklist.instance_id, read).queryKey, { checklist: result.checklist }); void qc.invalidateQueries({ queryKey: personalKeys.checklists(checklist.project_id) }); record.clear(); setNeedsReselect(false);
  }, { initialRequest: draft.pendingRequest, onRequest: (request) => record.write({ ...draft, pendingRequest: request }), onRejected: () => patch({ pendingRequest: undefined }), enabled: access.fieldWritable && writable && checklist.lifecycle_state !== "archived", owner: access.scope, draftKey: `check:${item.item_id}:record` });
  const locked = !writable || !access.fieldWritable || checklist.lifecycle_state === "archived" || save.isPending || save.unknown;
  const outdated = draft.baseRevision !== item.revision || draft.criteriaHash !== item.criteria_snapshot_hash;
  const requiredMissing = item.criteria.required_observations.some((observation) => !draft.observations[observation.key]?.trim() || (observation.kind === "number" && !Number.isFinite(Number(draft.observations[observation.key]))));
  const invalid = (draft.kind !== "inspection" || draft.result === "not_applicable") && !draft.note.trim()
    || draft.kind === "inspection" && draft.result === "pass" && (!item.pass_eligibility.allowed || requiredMissing)
    || draft.kind === "void" && !draft.voidTarget;
  const submit = () => {
    const measuredAt = draft.checkedAt ? new Date(draft.checkedAt).toISOString() : new Date().toISOString();
    save.submit({ record_kind: draft.kind, ...(draft.kind === "inspection" ? { result: draft.result } : {}), note: draft.note,
      measurements: draft.kind === "inspection" ? item.criteria.required_observations.filter((observation) => draft.observations[observation.key]?.trim()).map((observation) => ({ key: observation.key, value: observation.kind === "number" ? Number(draft.observations[observation.key]) : draft.observations[observation.key], unit: observation.unit ?? "", method: observation.method ?? item.method_text, measured_at: measuredAt })) : [],
      attachment_refs: draft.attachments, ...(draft.checkedAt ? { checked_at: measuredAt } : {}), ...(draft.kind === "void" ? { supersedes_check_id: draft.voidTarget } : {}), criteria_snapshot_hash: draft.criteriaHash,
    }, epoch, draft.baseRevision);
  };
  return <article data-testid="check-item" className="space-y-3 rounded-panel border border-line bg-surface p-4"><div className="flex flex-wrap items-start justify-between gap-2"><div><h2 className="text-[15px] font-semibold">{item.title}</h2><p className="mt-1 text-[12px] text-muted">{BASIS_LABELS[item.basis_kind]} · 条目版本 {item.revision} · <span className={item.current_result === "fail" || item.needs_recheck ? "text-st-wait" : ""}>{item.current_result === "pass" ? item.pass_eligibility.label || CHECK_RESULT_LABELS.pass : CHECK_RESULT_LABELS[item.current_result]}{item.needs_recheck ? "（依据需复看）" : ""}</span></p></div><Button size="sm" variant="outline" disabled={locked} onClick={() => setEditOpen(true)}>修订本项</Button></div>
    <p className="text-[13px] text-ink-2">方法：{item.method_text || "待补充"}</p><p className="text-[13px] text-ink-2">判据：{item.criteria.text || "待核实，不能判定符合"}</p><p className="text-[12px] text-muted">适用条件：{conditionsText(item.conditions)}</p>
    {item.criteria.unresolved_standard_conflict && <p role="status" className="text-xs text-st-wait">已人工标记：个人要求与规范的冲突尚未核实。可在“修订本项”中核实并明确解除。</p>}
    {!item.pass_eligibility.allowed && <p className="text-[12px] text-st-wait">暂不能记录符合：{item.pass_eligibility.reasons.join("；")}</p>}
    {item.refs.length > 0 && <details><summary className="cursor-pointer text-[12px] text-muted">本项保存的依据（{item.refs.length}）</summary>{item.refs.map((ref) => <div key={ref.ref_id} className="mt-2 text-xs"><p>{String(ref.snapshot.title ?? ref.snapshot.claim ?? ref.target_id)} · {ref.binding_status === "active" ? "引用有效" : "需复看"}</p><EvidenceSnapshot snapshot={ref.snapshot} /></div>)}</details>}
    <details open={formOpen} onToggle={(event) => { if (event.currentTarget.open !== formOpen) setFormOpen(event.currentTarget.open); }}><summary className="cursor-pointer text-[13px] font-medium">填写核对、整改或复检记录</summary><form className="mt-3 space-y-3" onSubmit={(event) => { event.preventDefault(); if (!locked && !invalid && !outdated) submit(); }}>
      <p className="text-xs text-muted">文字草稿在本标签页暂存 24 小时，未收到回执前不计为完成。照片只在明确上传后保存。</p>{record.notice && <p role="status" className="text-xs text-st-wait">{record.notice}</p>}
      {outdated && <div role="status" className="space-y-2 text-xs text-st-wait"><p>判据已从草稿版本 {draft.baseRevision} 更新为 {item.revision}。先核对新版；旧观察不会自动用于新版通过。</p><Button size="sm" variant="outline" disabled={save.unknown} onClick={() => patch({ baseRevision: item.revision, criteriaHash: item.criteria_snapshot_hash, observations: {}, result: "unable_to_verify", pendingRequest: undefined })}>保留文字并按新版重新核对</Button></div>}
      <Field label="记录类型"><Select aria-label="记录类型" value={draft.kind} disabled={locked} onChange={(event) => patch({ kind: event.target.value as typeof draft.kind, pendingRequest: undefined })}><option value="inspection">检查或复检</option><option value="remediation">整改说明</option><option value="void">作废错误记录</option></Select></Field>
      {draft.kind === "inspection" && <><Field label="本项结果"><Select aria-label="本项结果" value={draft.result} disabled={locked} onChange={(event) => patch({ result: event.target.value as typeof draft.result, pendingRequest: undefined })}>{Object.entries(CHECK_RESULT_LABELS).filter(([key]) => key !== "unchecked").map(([key, label]) => <option key={key} value={key} disabled={key === "pass" && !item.pass_eligibility.allowed}>{key === "pass" ? item.pass_eligibility.label || label : label}</option>)}</Select></Field>{item.criteria.required_observations.map((observation) => <Field key={observation.key} label={`${observation.label}${observation.unit ? `（${observation.unit}）` : ""}`} hint={observation.method ? `测量方法：${observation.method}` : undefined}>{observation.kind === "number" ? <Input aria-label={observation.label} type="number" step="any" value={draft.observations[observation.key] ?? ""} disabled={locked} onChange={(event) => patch({ observations: { ...draft.observations, [observation.key]: event.target.value }, pendingRequest: undefined })} /> : <Textarea aria-label={observation.label} rows={2} value={draft.observations[observation.key] ?? ""} disabled={locked} onChange={(event) => patch({ observations: { ...draft.observations, [observation.key]: event.target.value }, pendingRequest: undefined })} />}</Field>)}</>}
      {draft.kind === "void" && <Field label="作废哪条记录"><Select aria-label="作废哪条记录" value={draft.voidTarget ?? ""} disabled={locked} onChange={(event) => patch({ voidTarget: event.target.value })}><option value="">请选择当前版本的错误记录</option>{item.checks.filter((check) => !check.voided && check.record_kind !== "void" && check.item_revision === item.revision).map((check) => <option key={check.check_id} value={check.check_id}>{check.checked_at} · {check.record_kind === "remediation" ? "整改" : CHECK_RESULT_LABELS[check.result ?? "unchecked"]}</option>)}</Select></Field>}
      <Field label={draft.kind === "void" ? "作废理由（必填）" : draft.result === "not_applicable" ? "不适用理由（必填）" : "问题、整改或补充说明"}><Textarea aria-label="记录说明" value={draft.note} disabled={locked} rows={3} onChange={(event) => patch({ note: event.target.value, pendingRequest: undefined })} /></Field>
      <Field label="实际观察时间" hint="留空按本次提交时间记录；补录早期观察不会覆盖较新的观察。"><Input aria-label="实际观察时间" type="datetime-local" value={draft.checkedAt} disabled={locked} onChange={(event) => patch({ checkedAt: event.target.value, pendingRequest: undefined })} /></Field>
      <details><summary className="cursor-pointer text-[13px]">照片与附件（{draft.attachments.length}）</summary><div className="mt-3 space-y-3"><Field label="上传到哪份来源记录"><Select aria-label="附件来源记录" value={draft.sourceId} disabled={locked} onChange={(event) => patch({ sourceId: event.target.value, filePending: undefined })}><option value="">选择已建立的来源</option>{sources.data?.sources.map((source) => <option key={source.source_id} value={source.source_id}>{source.title}</option>)}</Select></Field><div className="flex flex-wrap gap-2"><SourceEditorButton disabled={locked} />{sources.data?.sources.find((source) => source.source_id === draft.sourceId) && <SourceEditorButton source={sources.data.sources.find((source) => source.source_id === draft.sourceId)} disabled={locked} />}</div><p className="text-xs text-muted">可在这里建立或维护现场照片来源；保存来源后仍需明确选择，已填写的观察与附件不自动改变。</p>
        {needsReselect && draft.filePending && <p role="status" className="text-xs text-st-wait">待上传文件“{draft.filePending}”已不在浏览器内，请重新选择；文字草稿已恢复。</p>}
        {draft.sourceId && <AttachmentUploader key={draft.sourceId} sourceId={draft.sourceId} draftKey={`check:${item.item_id}:attachment:${draft.sourceId}`} projectId={checklist.project_id} epoch={epoch} disabled={locked || !uploadable} onFileSelected={(filePending) => { setNeedsReselect(false); patch({ filePending }); }} onReady={(attachment) => { setNeedsReselect(false); patch({ attachments: [...new Set([...draft.attachments, attachment.attachment_id])], filePending: undefined }); }} />}
        {sources.data?.sources.flatMap((source) => source.attachments.filter((attachment) => attachment.state === "ready")).map((attachment) => <label key={attachment.attachment_id} className="flex gap-2 text-xs"><input type="checkbox" disabled={locked} checked={draft.attachments.includes(attachment.attachment_id)} onChange={(event) => patch({ attachments: event.target.checked ? [...draft.attachments, attachment.attachment_id] : draft.attachments.filter((id) => id !== attachment.attachment_id), pendingRequest: undefined })} />{attachment.original_name}</label>)}
      </div></details><MutationNotice mutation={save} />
      {save.error && !save.unknown && <Button size="sm" variant="outline" onClick={() => void qc.invalidateQueries({ queryKey: personalKeys.checklist(checklist.instance_id) })}>核对服务器当前记录（保留草稿）</Button>}
      <Button type="submit" disabled={locked || invalid || outdated}>保存本次记录</Button>{save.isSuccess && <p role="status" className="text-xs text-st-ok">已收到保存回执，历史与当前结果已更新。</p>}
    </form></details>
    <CheckHistory item={item} />
    {editOpen && <EditCheckItemDialog item={item} projectId={checklist.project_id} epoch={epoch} disabled={locked} read={read ?? access.read} onClose={() => setEditOpen(false)} />}
  </article>;
}
function EditCheckItemDialog({ item, projectId, epoch, disabled, onClose, read }: { item: CheckItem; projectId: string; epoch: number; disabled: boolean; onClose: () => void; read?: PersonalRead }) {
  const access = usePersonalProject(); const qc = useQueryClient();
  const draftKey = `check:${item.item_id}:criteria`;
  const { draft: edit, setDraft: setEdit, clear, notice } = usePersonalDraft<{ item: CheckItemInput; baseRevision: number }>({ scope: read?.scope ?? access.scope, key: draftKey, label: `修订判据：${item.title}`, initial: () => ({ item: { title: item.title, conditions: item.conditions, method_text: item.method_text, basis_kind: item.basis_kind, criteria_state: item.criteria_state, criteria: item.criteria, refs: item.refs.map((ref) => ({ target_kind: ref.target_kind, target_id: ref.target_id, target_fingerprint: ref.target_fingerprint })) }, baseRevision: item.revision }), describe: (draft) => `${describeCheckItem(draft.item, (ref) => String(item.refs.find((original) => original.target_id === ref.target_id && original.target_kind === ref.target_kind)?.snapshot.title ?? item.refs.find((original) => original.target_id === ref.target_id && original.target_kind === ref.target_kind)?.snapshot.claim ?? (ref.target_kind === "source" ? qc.getQueryData<{ sources: { source_id: string; title: string }[] }>(sourcesQuery(projectId, read ?? access.read).queryKey)?.sources.find((source) => source.source_id === ref.target_id)?.title ?? "已选人工来源" : "已选视频知识")))}\n草稿基于版本：${draft.baseRevision}` });
  const draft = edit.item; const baseRevision = edit.baseRevision;
  const setDraft = (action: SetStateAction<CheckItemInput>) => setEdit((draft) => ({ ...draft, item: typeof action === "function" ? action(draft.item) : action }));
  const setBaseRevision = (baseRevision: number) => setEdit((draft) => ({ ...draft, baseRevision }));
  const [reviewedItem, setReviewedItem] = useState<CheckItem | null>(null);
  const [readingLatest, setReadingLatest] = useState(false);
  const [readError, setReadError] = useState("");
  const save = useReceiptMutation((body: Parameters<typeof patchCheckItem>[1], signal) => patchCheckItem(item.item_id, body, signal), () => { void qc.invalidateQueries({ queryKey: personalKeys.checklist(item.instance_id) }); void qc.invalidateQueries({ queryKey: personalKeys.checklists(projectId) }); clear(); onClose(); }, { enabled: access.fieldWritable && !disabled, owner: access.scope, draftKey });
  const locked = !access.fieldWritable || disabled || save.isPending || save.unknown;
  const readLatest = async () => {
    setReadingLatest(true); setReadError(""); setReviewedItem(null);
    try {
      const result = await qc.fetchQuery({ ...checklistQuery(item.instance_id, read), staleTime: 0 });
      const latest = result.checklist.items.find((entry) => entry.item_id === item.item_id);
      if (!latest) throw new Error("服务器当前清单中找不到此项，请保留草稿并核对清单。");
      setReviewedItem(latest);
    } catch (error) { setReadError(error instanceof Error ? error.message : "暂时无法读取当前判据，草稿保持不变。"); }
    finally { setReadingLatest(false); }
  };
  return <Dialog open onOpenChange={(open) => { if (!open && !save.isPending && !save.unknown) onClose(); }}><DialogContent title="修订本项判据" description="提交后创建新的条目版本，当前结果恢复未核对；历史观察保留在旧版本。"><div className="space-y-4"><p className="text-xs text-muted">本次草稿基于条目版本 {baseRevision}；后台刷新不会改变提交基线。</p><CheckItemEditor value={draft} onChange={setDraft} projectId={projectId} referenceSnapshots={item.refs} disabled={locked} /><p className="text-xs text-muted">{notice}</p><MutationNotice mutation={save} />
    {save.error && !save.unknown && <section className="space-y-3 rounded-ctl border border-st-wait-line p-3"><Button size="sm" variant="outline" disabled={locked || readingLatest} onClick={() => void readLatest()}>{readingLatest ? "正在读取…" : "读取服务器当前判据"}</Button>{readError && <p role="alert" className="text-xs text-st-wait">{readError}</p>}{reviewedItem && <div className="space-y-3"><h3 className="text-[13px] font-medium">待核对的服务器判据（版本 {reviewedItem.revision}）</h3><div aria-label="待核对的服务器判据"><CheckItemEditor value={reviewedItem} onChange={() => {}} projectId={projectId} referenceSnapshots={reviewedItem.refs} disabled /></div><p className="text-xs text-muted">核对以上服务器版本与表单中的本次草稿后，再决定是否保留草稿继续。不会自动改写已选择的引用指纹。</p><Button size="sm" variant="outline" disabled={locked || reviewedItem.revision <= baseRevision} onClick={() => { setBaseRevision(reviewedItem.revision); setReviewedItem(null); save.reset(); }}>以已核对版本为基础保留本次草稿</Button></div>}</section>}
    <div className="sticky bottom-0 -mx-5 -mb-5 border-t border-line bg-surface p-4 max-md:-mx-4 max-md:-mb-4"><Button disabled={locked || readingLatest || !draft.title.trim()} onClick={() => save.submit(draft, epoch, baseRevision)}>确认修订并重新核对</Button></div></div></DialogContent></Dialog>;
}

function CheckHistory({ item }: { item: CheckItem }) {
  return <details><summary className="cursor-pointer text-[13px]">检查、整改与复检历史（{item.checks.length}）</summary><ol className="mt-3 space-y-2">{item.checks.map((check) => <li key={check.check_id} className="rounded-ctl border border-line p-3 text-[12px]"><p>{check.checked_at} · 条目版本 {check.item_revision} · {check.record_kind === "inspection" ? `当时记录为${CHECK_RESULT_LABELS[check.result ?? "unchecked"]}` : check.record_kind === "remediation" ? "整改说明" : "作废记录"}{check.voided ? "（已作废）" : ""}{!check.current_version ? "（历史判据版本）" : ""}</p><p className="mt-1 whitespace-pre-wrap">{check.note || "无补充说明"}</p>{check.measurements.map((measurement) => <p key={measurement.key}>{check.item_revision === item.revision ? item.criteria.required_observations.find((field) => field.key === measurement.key)?.label ?? measurement.key : `历史观察 ${measurement.key}`}：{measurement.value}{measurement.unit} {measurement.method}</p>)}{check.attachment_refs.length > 0 && <p className="text-muted">关联 {check.attachment_refs.length} 份附件{check.attachment_issues?.length ? "，部分文件缺失需复看" : ""}</p>}</li>)}</ol></details>;
}
function ReadOnlyCheckItem({ item }: { item: CheckItem }) {
  return <article data-testid="check-item" className="space-y-3 rounded-panel border border-line bg-surface p-4"><div><h2 className="text-[15px] font-semibold">{item.title}</h2><p className="mt-1 text-[12px] text-muted">{BASIS_LABELS[item.basis_kind]} · 条目版本 {item.revision} · {item.current_result === "pass" ? item.pass_eligibility.label || CHECK_RESULT_LABELS.pass : CHECK_RESULT_LABELS[item.current_result]}{item.needs_recheck ? "（依据需复看）" : ""}</p></div>
    <p className="text-[13px] text-ink-2">方法：{item.method_text || "待补充"}</p><p className="text-[13px] text-ink-2">判据：{item.criteria.text || "待核实，不能判定符合"}</p><p className="text-[12px] text-muted">适用条件：{conditionsText(item.conditions)}</p>
    {item.criteria.unresolved_standard_conflict && <p role="status" className="text-xs text-st-wait">已人工标记：个人要求与规范的冲突尚未核实。</p>}
    {!item.pass_eligibility.allowed && <p className="text-[12px] text-st-wait">判定符合仍缺少依据：{item.pass_eligibility.reasons.join("；")}</p>}
    {item.refs.length > 0 && <details><summary className="cursor-pointer text-[12px] text-muted">本项保存的依据（{item.refs.length}）</summary>{item.refs.map((ref) => <div key={ref.ref_id} className="mt-2 text-xs"><p>{String(ref.snapshot.title ?? ref.snapshot.claim ?? ref.target_id)} · {ref.binding_status === "active" ? "引用有效" : "需复看"}</p><EvidenceSnapshot snapshot={ref.snapshot} /></div>)}</details>}
    <CheckHistory item={item} />
  </article>;
}
