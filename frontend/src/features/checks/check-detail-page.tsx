import type { ReactNode } from "react";
import { Link, useParams } from "react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { checklistQuery, checklistShareSelection, patchChecklist, personalKeys, type Checklist } from "@/lib/personal-api";
import { usePersonalProject } from "@/features/project/use-personal-project";
import { PersonalReadBanner } from "@/features/project/personal-read-banner";
import { MutationNotice, useReceiptMutation } from "@/features/project/use-receipt-mutation";
import { usePersonalDraft } from "@/features/project/use-personal-draft";
import { PageHeader, EmptyState, ErrorState, InlineSpinner } from "@/components/shared/states";
import { Button } from "@/components/ui/button";
import { CheckItemCard } from "./check-item-card";
import { ChecklistSharePreview } from "./checklist-share-preview";

export default function CheckDetailPage() {
  const id = useParams().instanceId ?? ""; const access = usePersonalProject();
  const query = useQuery({ ...checklistQuery(id, access.read), enabled: access.fieldReadable && !!id });
  if (access.capabilities.isError || query.isError) return <div className="p-5"><ErrorState error={access.capabilities.error ?? query.error!} onRetry={() => { void access.capabilities.refetch(); if (access.fieldReadable) void query.refetch(); }} context="现场清单" /></div>;
  if (!access.fieldReadable) return <div className="p-5">{access.capabilities.isPending ? <InlineSpinner label="读取清单权限…" /> : <EmptyState title="现场记录暂未开放" desc={access.reason} />}</div>;
  if (!query.data) return <div className="p-5"><InlineSpinner label="读取清单…" /></div>;
  const props = { checklist: query.data.checklist, access,
    previewAvailable: !query.isFetching && !access.capabilities.isFetching,
    previewVersion: JSON.stringify([query.dataUpdatedAt, query.data.checklist.updated_at,
      query.data.checklist.state, query.data.checklist.lifecycle_state, query.data.checklist.progress.total, query.data.checklist.progress.complete,
      query.data.checklist.items.map(({ current_result, needs_recheck, basis_kind }) => [current_result, needs_recheck, basis_kind])]),
    onRefresh: () => { void query.refetch(); } };
  return access.fieldWritable ? <WritableChecklist key={`${access.scope}:${id}`} {...props} /> : <ChecklistView key={`${access.scope}:${id}`} {...props} />;
}
type ChecklistProps = { checklist: Checklist; access: ReturnType<typeof usePersonalProject>; previewAvailable: boolean; previewVersion: string; onRefresh: () => void };
function WritableChecklist({ checklist, access, previewAvailable, previewVersion, onRefresh }: ChecklistProps) {
  const qc = useQueryClient();
  const draftKey = `checklist:${checklist.instance_id}:lifecycle`;
  const { setDraft, clear } = usePersonalDraft({ scope: access.scope, key: draftKey, label: `清单状态：${checklist.title}`, initial: () => checklist.lifecycle_state, describe: (state) => `现场清单：${checklist.title}\n待保存的状态：${state === "archived" ? "归档" : "恢复为核对中"}` });
  const archive = useReceiptMutation((body: Parameters<typeof patchChecklist>[1], signal) => patchChecklist(checklist.instance_id, body, signal), (result) => {
    qc.setQueryData(checklistQuery(checklist.instance_id, access.read).queryKey, result);
    void qc.invalidateQueries({ queryKey: personalKeys.checklists(result.checklist.project_id) });
    clear();
  }, { enabled: access.fieldWritable, owner: access.scope, draftKey });
  return <ChecklistView checklist={checklist} access={access} previewAvailable={previewAvailable && !archive.isPending && !archive.unknown} previewVersion={previewVersion} onRefresh={onRefresh} action={<Button size="sm" variant="outline" disabled={!access.fieldWritable || archive.isPending || archive.unknown} onClick={() => { const next = checklist.lifecycle_state === "archived" ? "active" : "archived"; setDraft(next); archive.submit({ lifecycle_state: next }, access.capabilities.data!.primary_epoch, checklist.revision); }}>{checklist.lifecycle_state === "archived" ? "恢复现场清单" : "归档现场清单"}</Button>} notice={<MutationNotice mutation={archive} />} />;
}
function ChecklistView({ checklist, access, previewAvailable, previewVersion, onRefresh, action, notice }: ChecklistProps & { action?: ReactNode; notice?: ReactNode }) {
  const capability = access.capabilities.data!;
  return <div className="mx-auto w-full max-w-[980px] px-4 py-5 md:px-6"><Link className="mb-3 inline-block text-[13px] text-acc" to="/checks">← 现场清单</Link><PageHeader title={checklist.title} desc={`${checklist.space || "空间待补充"} · ${checklist.stage || "阶段待补充"} · ${checklist.progress.complete}/${checklist.progress.total} 项完成`} actions={<>{action}<ChecklistSharePreview projectId={checklist.project_id} selection={checklistShareSelection(checklist)} read={access.read} available={previewAvailable && access.fieldReadable} viewVersion={previewVersion} onSelectionChanged={onRefresh} /></>} />
    <PersonalReadBanner capabilities={capability} writable={access.fieldWritable} reason={access.reason} />
    <p role="status" className="mb-4 text-[13px] text-muted">{checklist.state === "completed" ? "本清单当前项目已完成；仅表示符合所记录的本项判据，不等于工程验收合格。" : checklist.state === "archived" ? "已归档。历史可回看；恢复后才能追加或修订。" : "仍有未核对、需复看或待处理项目。"}</p>{notice}
    <div className="space-y-4">{checklist.items.map((item) => <CheckItemCard key={item.item_id} item={item} checklist={checklist} libraryId={capability.library_id} epoch={capability.primary_epoch} writable={access.fieldWritable} uploadable={access.uploadable} read={access.read} />)}</div>
  </div>;
}
