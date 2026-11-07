import { useState, type SetStateAction } from "react";
import { newOperationId } from "@/lib/id";
import { Link, useNavigate } from "react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { checklistsQuery, createChecklist, personalKeys, sourcesQuery, type CheckItemInput } from "@/lib/personal-api";
import { usePersonalProject } from "@/features/project/use-personal-project";
import { MutationNotice, useReceiptMutation } from "@/features/project/use-receipt-mutation";
import { usePersonalDraft } from "@/features/project/use-personal-draft";
import { PageHeader, ErrorState, EmptyState, InlineSpinner } from "@/components/shared/states";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/field";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { CheckItemEditor, describeCheckItem, newCheckItem } from "./check-item-editor";
import { KnowledgeCandidatePicker } from "./knowledge-candidate-picker";
import { PersonalReadBanner } from "@/features/project/personal-read-banner";

export default function ChecksPage() {
  const access = usePersonalProject();
  return <ChecksView key={access.scope} access={access} />;
}
function ChecksView({ access }: { access: ReturnType<typeof usePersonalProject> }) {
  const projectId = access.project.data?.project?.project_id ?? "";
  const [offset, setOffset] = useState(0); const [open, setOpen] = useState(false);
  const checks = useQuery({ ...checklistsQuery(projectId, offset, access.read), enabled: !!projectId && access.fieldReadable });
  return <div className="mx-auto w-full max-w-[980px] px-4 py-5 md:px-6"><PageHeader title="现场核对" desc="记录本项依据、观察、整改与复检；符合某项判据不等于工程验收合格。" actions={<Button size="sm" disabled={!access.fieldWritable} onClick={() => setOpen(true)}>新建现场清单</Button>} />
    <PersonalReadBanner capabilities={access.capabilities.data} writable={access.fieldWritable} reason={access.reason} />
    {!projectId || !access.fieldReadable ? <EmptyState title="现场记录暂未开放" desc={access.reason} /> : checks.isError ? <ErrorState error={checks.error} onRetry={() => void checks.refetch()} context="现场清单" /> : checks.isPending ? <InlineSpinner label="读取清单…" /> : !checks.data.checklists.length ? <EmptyState title="还没有现场清单" desc="从一项工序开始，依据不足时先保存观察项，不填写猜测的验收阈值。" /> : <div className="space-y-3">{checks.data.checklists.map((checklist) => <Link key={checklist.instance_id} to={`/checks/${checklist.instance_id}`} className="block rounded-panel border border-line bg-surface p-4"><h2 className="font-semibold">{checklist.title}</h2><p className="mt-1 text-sm text-muted">{checklist.space || "空间待补充"} · {checklist.stage || "阶段待补充"} · {checklist.state === "completed" ? "本清单已完成" : checklist.state === "archived" ? "已归档" : "核对中"}</p><p className="mt-1 text-xs text-muted">{checklist.progress.complete} / {checklist.progress.total} 项完成</p></Link>)}</div>}
    {access.fieldReadable && checks.data && <div className="mt-4 flex items-center gap-2 text-xs text-muted"><Button size="sm" variant="outline" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 30))}>上一页</Button><span>共 {checks.data.total} 份清单</span><Button size="sm" variant="outline" disabled={offset + 30 >= checks.data.total} onClick={() => setOffset(offset + 30)}>下一页</Button></div>}
    {access.fieldWritable && open && <CreateChecklistDialog open onOpenChange={setOpen} />}
  </div>;
}
function CreateChecklistDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const access = usePersonalProject(); const qc = useQueryClient(); const navigate = useNavigate(); const projectId = access.project.data?.project?.project_id ?? "";
  const draftKey = `checklist:${projectId}:create`;
  const { draft, setDraft, clear, notice } = usePersonalDraft({ scope: access.scope, key: draftKey, label: "新建现场清单", initial: () => ({ title: "", space: "", stage: "", items: [{ id: newOperationId(), item: newCheckItem() }] }), describe: (draft) => [`清单标题：${draft.title}`, `空间：${draft.space}`, `阶段：${draft.stage}`, ...draft.items.map((row, index) => `第 ${index + 1} 项：\n${describeCheckItem(row.item, (ref) => ref.target_kind === "source" ? qc.getQueryData<{ sources: { source_id: string; title: string }[] }>(sourcesQuery(projectId, access.read).queryKey)?.sources.find((source) => source.source_id === ref.target_id)?.title ?? "已选人工来源" : `视频知识：${row.item.title}`)}`)].join("\n") });
  const { title, space, stage, items } = draft;
  const setTitle = (title: string) => setDraft((draft) => ({ ...draft, title }));
  const setSpace = (space: string) => setDraft((draft) => ({ ...draft, space }));
  const setStage = (stage: string) => setDraft((draft) => ({ ...draft, stage }));
  const setItems = (action: SetStateAction<{ id: string; item: CheckItemInput }[]>) => setDraft((draft) => ({ ...draft, items: typeof action === "function" ? action(draft.items) : action }));
  const save = useReceiptMutation((body: Parameters<typeof createChecklist>[1], signal) => createChecklist(projectId, body, signal), (result) => { void qc.invalidateQueries({ queryKey: personalKeys.checklists(projectId) }); clear(); onOpenChange(false); navigate(`/checks/${result.checklist.instance_id}`); }, { enabled: access.fieldWritable, owner: access.scope, draftKey });
  const locked = save.isPending || save.unknown || !access.fieldWritable;
  return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent title="建立一次现场清单" description="冻结这次使用的条件、方法和依据。没有明确判据时只能观察与待核实。"><form className="space-y-4" onSubmit={(event) => { event.preventDefault(); if (!locked) save.submit({ title, space, stage, items: items.map((row) => row.item) }, access.capabilities.data!.primary_epoch); }}>
    <Field label="清单标题"><Input aria-label="清单标题" value={title} onChange={(event) => setTitle(event.target.value)} disabled={locked} required /></Field><div className="grid gap-3 sm:grid-cols-2"><Field label="空间"><Input aria-label="清单空间" value={space} onChange={(event) => setSpace(event.target.value)} disabled={locked} /></Field><Field label="阶段"><Input aria-label="清单阶段" value={stage} onChange={(event) => setStage(event.target.value)} disabled={locked} /></Field></div>
    <KnowledgeCandidatePicker disabled={locked || items.length >= 20} onSelect={(row) => {
      const candidate = { id: newOperationId(), item: { ...newCheckItem(), title: row.claim.slice(0, 200), conditions: Object.fromEntries(Object.entries(row.conditions ?? {}).map(([key, value]) => [key, typeof value === "string" ? value : JSON.stringify(value)])), basis_kind: "video_suggestion" as const, refs: [{ target_kind: "atom" as const, target_id: row.id, target_fingerprint: row.target_fingerprint! }] } };
      setItems((current) => current.length === 1 && !current[0].item.title.trim() ? [candidate] : [...current, candidate]);
    }} />
    {items.map((row, index) => <details key={row.id} open className="rounded-panel border border-line p-3"><summary className="mb-3 cursor-pointer font-medium">第 {index + 1} 项：{row.item.title || "尚未填写"}</summary><CheckItemEditor value={row.item} projectId={projectId} disabled={locked} onChange={(item) => setItems((current) => current.map((entry) => entry.id === row.id ? { ...entry, item } : entry))} />{items.length > 1 && <Button size="sm" variant="ghost" disabled={locked} onClick={() => setItems((current) => current.filter((entry) => entry.id !== row.id))}>移除此项</Button>}</details>)}
    <Button size="sm" variant="outline" disabled={locked || items.length >= 20} onClick={() => setItems((current) => [...current, { id: newOperationId(), item: newCheckItem() }])}>添加核对项</Button><p className="text-xs text-muted">{notice}</p><MutationNotice mutation={save} />
    <div className="sticky bottom-0 -mx-5 -mb-5 border-t border-line bg-surface p-4 max-md:-mx-4 max-md:-mb-4"><Button type="submit" disabled={locked || !title.trim() || items.some((row) => !row.item.title.trim())}>建立清单</Button></div>
  </form></DialogContent></Dialog>;
}
