
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet } from "@/lib/api";
import { createSource, patchSource, personalKeys, personalReadHeaders, type SourceChanges, type SourceRecord } from "@/lib/personal-api";
import { usePersonalProject } from "@/features/project/use-personal-project";
import { MutationNotice, useReceiptMutation } from "@/features/project/use-receipt-mutation";
import { usePersonalDraft } from "@/features/project/use-personal-draft";
import { conditionsText, ProjectContextEditor } from "@/features/project/project-context-editor";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Field, Input, Select, Textarea } from "@/components/ui/field";
import { useToast } from "@/components/shared/toaster";
import { EvidenceSnapshot } from "./evidence-snapshot";

export const SOURCE_TYPES = { manual_note: "人工备注", standard_record: "规范条文记录", product_document: "产品说明", quote: "报价资料", site_photo: "现场照片" };
const EMPTY_SOURCE: SourceChanges = { source_type: "manual_note", title: "", publisher: "", document_no: "", edition: "", published_on: "", valid_from: "", valid_to: "", source_url: "", locator: "", excerpt: "", scope: {}, verification_state: "unverified", validity: "unknown", checked_at: null, check_note: "" };
export function SourceRecordDialog({ open, onOpenChange, source, onSaved }: { open: boolean; onOpenChange: (open: boolean) => void; source?: SourceRecord; onSaved?: (source: SourceRecord) => void }) {
  const access = usePersonalProject();
  if (!access.writable) return null;
  return <WritableSourceRecordDialog key={JSON.stringify([access.scope, source?.source_id ?? "create"])} open={open} onOpenChange={onOpenChange} source={source} onSaved={onSaved} />;
}
function WritableSourceRecordDialog({ open, onOpenChange, source, onSaved }: { open: boolean; onOpenChange: (open: boolean) => void; source?: SourceRecord; onSaved?: (source: SourceRecord) => void }) {
  const access = usePersonalProject(); const project = access.project.data?.project; const qc = useQueryClient(); const toast = useToast();
  const draftKey = source ? `source:${source.source_id}:edit` : `source:${project!.project_id}:create`;
  const { draft: edit, setDraft: setEdit, clear, notice } = usePersonalDraft({ scope: access.scope, key: draftKey, label: source ? `来源：${source.title}` : "新建依据资料", initial: () => ({ source: source ? { ...EMPTY_SOURCE, ...source } : EMPTY_SOURCE, baseRevision: source?.revision }), describe: ({ source: value, baseRevision }) => [
    `资料类型：${SOURCE_TYPES[value.source_type]}`, `资料标题：${value.title}`, `发布方：${value.publisher}`, `规范编号或型号：${value.document_no}`, `版本：${value.edition}`, `发布日期：${value.published_on}`, `生效日期：${value.valid_from}`, `失效日期：${value.valid_to}`, `原文链接：${value.source_url}`, `原文定位：${value.locator}`, `原文摘录：${value.excerpt}`, `说明书适用型号：${value.scope.model ?? ""}`, `适用范围：${conditionsText(Object.fromEntries(Object.entries(value.scope).filter(([key]) => key !== "model")))}`, `原文核对：${value.verification_state === "user_checked" ? "用户已核对原文" : "未核对"}`, `有效性：${({ unknown: "未知", current: "用户记录为现行", superseded: "已被替代", withdrawn: "已撤销" })[value.validity]}`, `核对时间：${value.checked_at ?? ""}`, `核对说明：${value.check_note}`, `草稿基于版本：${baseRevision ?? "新建"}`].join("\n") });
  const draft = edit.source; const baseRevision = edit.baseRevision;
  const setDraft = (action: (value: SourceChanges) => SourceChanges) => setEdit((edit) => ({ ...edit, source: action(edit.source) }));
  const setBaseRevision = (baseRevision: number) => setEdit((edit) => ({ ...edit, baseRevision }));
  const latest = useQuery({ queryKey: ["personal", "source", source?.source_id, "read", access.read?.scope], meta: { personalRead: true }, queryFn: ({ signal }) => apiGet<{ source: SourceRecord }>(`/api/sources/${encodeURIComponent(source!.source_id)}`, signal, personalReadHeaders(access.read)), enabled: false, retry: false });
  const save = useReceiptMutation((body: Parameters<typeof createSource>[1], signal) => source ? patchSource(source.source_id, body, signal) : createSource(project!.project_id, body, signal), (result) => { void qc.invalidateQueries({ queryKey: personalKeys.sources(result.source.project_id) }); void qc.invalidateQueries({ queryKey: ["personal", "card"] }); void qc.invalidateQueries({ queryKey: ["personal", "checklist"] }); void qc.invalidateQueries({ queryKey: personalKeys.cards(result.source.project_id) }); void qc.invalidateQueries({ queryKey: personalKeys.checklists(result.source.project_id) }); setBaseRevision(result.source.revision); toast.success("来源记录已保存，未自动认证规范现行性。"); clear(); onSaved?.(result.source); onOpenChange(false); }, { enabled: access.writable, owner: access.scope, draftKey });
  const locked = !access.writable || save.isPending || save.unknown;
  const set = <K extends keyof SourceChanges>(key: K, value: SourceChanges[K]) => setDraft((current) => ({ ...current, [key]: value }));
  const needsCheck = draft.verification_state === "user_checked" || draft.validity === "current";
  const valid = !!draft.title.trim() && (!needsCheck || (!!draft.check_note.trim() && !!draft.checked_at && draft.verification_state === "user_checked"));
  const writableFields = (): SourceChanges => Object.fromEntries(Object.keys(EMPTY_SOURCE).map((key) => [key, draft[key as keyof SourceChanges]])) as unknown as SourceChanges;
  return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent title={source ? "编辑来源记录" : "记录一份依据"} description="手工记录原文与版本，不自动抓取网页或解析文件；用户核对记录不等于官方合规认证。"><form className="space-y-4" onSubmit={(event) => { event.preventDefault(); event.stopPropagation(); if (!locked && valid) save.submit(writableFields(), access.capabilities.data!.primary_epoch, baseRevision); }}>
    {source && <p className="text-xs text-muted">本次编辑基于版本 {baseRevision}；读取新版本不会覆盖已填写草稿。</p>}
    <Field label="资料类型"><Select aria-label="资料类型" value={draft.source_type} disabled={locked} onChange={(event) => set("source_type", event.target.value as SourceChanges["source_type"])}>{Object.entries(SOURCE_TYPES).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</Select></Field>
    <Field label="资料标题"><Input aria-label="资料标题" value={draft.title} disabled={locked} onChange={(event) => set("title", event.target.value)} required /></Field>
    <div className="grid gap-3 sm:grid-cols-2">{([["publisher", "发布方"], ["document_no", "规范编号或型号"], ["edition", "版本"], ["published_on", "发布日期"], ["valid_from", "生效日期"], ["valid_to", "失效日期"]] as const).map(([key, label]) => <Field key={key} label={label}><Input aria-label={label} value={draft[key]} disabled={locked} type={key === "published_on" || key === "valid_from" || key === "valid_to" ? "date" : "text"} onChange={(event) => set(key, event.target.value)} /></Field>)}</div>
    <Field label="原文链接（仅记录）"><Input aria-label="原文链接" value={draft.source_url} disabled={locked} type="url" onChange={(event) => set("source_url", event.target.value)} /></Field>
    <Field label="条款、页码或定位"><Input aria-label="原文定位" value={draft.locator} disabled={locked} onChange={(event) => set("locator", event.target.value)} /></Field>
    <Field label="原文摘录或说明"><Textarea aria-label="原文摘录" value={draft.excerpt} disabled={locked} rows={4} onChange={(event) => set("excerpt", event.target.value)} /></Field>
    {draft.source_type === "product_document" && <Field label="说明书适用的产品型号"><Input aria-label="说明书适用型号" value={draft.scope.model ?? ""} disabled={locked} onChange={(event) => set("scope", { ...draft.scope, model: event.target.value })} /></Field>}
    <details><summary className="cursor-pointer text-[13px] text-muted">适用范围</summary><div className="mt-3"><ProjectContextEditor value={draft.scope} onChange={(scope) => set("scope", scope)} disabled={locked} /></div></details>
    <div className="grid gap-3 sm:grid-cols-2"><Field label="原文核对"><Select aria-label="原文核对" value={draft.verification_state} disabled={locked} onChange={(event) => set("verification_state", event.target.value as SourceChanges["verification_state"])}><option value="unverified">未核对</option><option value="user_checked">用户已核对原文</option></Select></Field><Field label="有效性记录"><Select aria-label="有效性记录" value={draft.validity} disabled={locked} onChange={(event) => set("validity", event.target.value as SourceChanges["validity"])}><option value="unknown">未知</option><option value="current">用户记录为现行</option><option value="superseded">已被替代</option><option value="withdrawn">已撤销</option></Select></Field></div>
    <Field label="核对时间"><Input aria-label="核对时间" type="datetime-local" disabled={locked} value={draft.checked_at ? new Date(new Date(draft.checked_at).getTime() - new Date(draft.checked_at).getTimezoneOffset() * 60_000).toISOString().slice(0, 16) : ""} onChange={(event) => set("checked_at", event.target.value ? new Date(event.target.value).toISOString() : null)} /></Field>
    <Field label="原文核对说明"><Textarea aria-label="核对说明" value={draft.check_note} disabled={locked} onChange={(event) => set("check_note", event.target.value)} rows={2} /></Field>
    {needsCheck && !valid && <p className="text-[12px] text-st-wait">标记已核对或现行前，请填写核对时间与说明，并确认已核对原文。</p>}<p className="text-xs text-muted">{notice}</p><MutationNotice mutation={save} />
    {save.error && !save.unknown && source && <section className="space-y-3 rounded-ctl border border-st-wait-line p-3"><Button size="sm" variant="outline" disabled={latest.isFetching} onClick={() => void latest.refetch()}>{latest.isFetching ? "正在读取…" : "读取最新来源版本"}</Button>
      {latest.isError && <p role="alert" className="text-xs text-st-wait">暂时无法读取最新来源，当前草稿保持不变。{latest.error.message}</p>}
      {latest.data && <><h3 className="text-[13px] font-medium">服务器当前来源（版本 {latest.data.source.revision}）</h3><EvidenceSnapshot snapshot={latest.data.source as unknown as Record<string, unknown>} /><p className="text-xs text-muted">核对上方服务器内容与表单中的本次草稿后，再选择是否以最新版本继续。此操作不自动提交。</p><Button size="sm" variant="outline" disabled={latest.data.source.revision <= (baseRevision ?? 0)} onClick={() => { setBaseRevision(latest.data.source.revision); save.reset(); }}>以最新版本为基础保留本次来源草稿</Button></>}
    </section>}
    <div className="sticky bottom-0 -mx-5 -mb-5 flex gap-2 border-t border-line bg-surface p-4 max-md:-mx-4 max-md:-mb-4"><Button type="submit" disabled={locked || !valid}>保存来源记录</Button><Button variant="ghost" onClick={() => onOpenChange(false)}>稍后继续</Button></div>
  </form></DialogContent></Dialog>;
}
