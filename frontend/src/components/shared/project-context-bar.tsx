import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router";
import { usePersonalProject } from "@/features/project/use-personal-project";
import { ProjectContextEditor, conditionsText } from "@/features/project/project-context-editor";
import { MutationNotice, useReceiptMutation } from "@/features/project/use-receipt-mutation";
import { usePersonalDraft } from "@/features/project/use-personal-draft";
import { defaultProjectFor, patchProject, type ReadingContext } from "@/lib/personal-api";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";

export function ProjectContextBar({ value, onChange }: { value?: ReadingContext; onChange: (next: ReadingContext) => void }) {
  const access = usePersonalProject();
  return <ProjectContextBarView key={access.scope} value={value} onChange={onChange} access={access} />;
}
function ProjectContextBarView({ value, onChange, access }: { value?: ReadingContext; onChange: (next: ReadingContext) => void; access: ReturnType<typeof usePersonalProject> }) {
  const { project, capabilities, writable, reason, scope } = access;
  const qc = useQueryClient();
  const currentProject = project.data?.project;
  const [open, setOpen] = useState(false);
  const draftKey = `project:${currentProject?.project_id ?? "reading"}:context:${JSON.stringify(value ?? {})}`;
  const { draft: edit, setDraft: setEdit, clear, notice } = usePersonalDraft({ scope: access.read?.scope ?? "", key: draftKey, label: "本次使用的条件", initial: () => ({ conditions: { ...value?.context_snapshot }, baseRevision: currentProject?.revision }), describe: (draft) => `本次条件：${conditionsText(draft.conditions)}\n草稿基于版本：${draft.baseRevision ?? "未关联项目"}` });
  const [localEdit, setLocalEdit] = useState(() => ({ conditions: { ...value?.context_snapshot }, baseRevision: currentProject?.revision }));
  const currentEdit = access.read ? edit : localEdit;
  const draft = currentEdit.conditions; const baseRevision = currentEdit.baseRevision;
  const setDraft = (conditions: typeof draft) => { if (access.read) setEdit((draft) => ({ ...draft, conditions })); else setLocalEdit((draft) => ({ ...draft, conditions })); };
  const setBaseRevision = (baseRevision: number | undefined) => { if (access.read) setEdit((draft) => ({ ...draft, baseRevision })); else setLocalEdit((draft) => ({ ...draft, baseRevision })); };
  const save = useReceiptMutation((request: Parameters<typeof patchProject>[1], signal) => patchProject(currentProject!.project_id, request, signal), (result) => {
    qc.setQueryData(defaultProjectFor(access.read).queryKey, { enabled: true, project: result.project });
    onChange({ project_id: result.project.project_id, context_revision: result.project.revision, context_snapshot: { ...result.project.profile.conditions } }); clear(); setOpen(false);
  }, { enabled: writable, owner: scope, draftKey });
  const useOnce = () => { onChange({ ...(currentProject ? { project_id: currentProject.project_id, context_revision: baseRevision } : {}), context_snapshot: { ...draft } }); clear(); setOpen(false); };
  return <section className="mb-3 rounded-panel border border-line bg-surface p-3 text-[12px]">
    <div className="flex flex-wrap items-center gap-2"><p className="min-w-0 flex-1 text-ink-2">本次使用：{conditionsText(value?.context_snapshot ?? {})}</p>
      <Button size="sm" variant="ghost" onClick={() => setOpen(true)}>调整本次条件</Button>
      {currentProject && <Button size="sm" variant="ghost" onClick={() => onChange({ project_id: currentProject.project_id, context_revision: currentProject.revision, context_snapshot: { ...currentProject.profile.conditions } })}>使用项目条件</Button>}
    </div>
    <p className="mt-1 text-muted">条件可留空。只改本次不会修改项目；已有结果保留生成时的条件。{!currentProject && !project.isPending ? ` ${reason}` : ""} <Link className="text-acc hover:underline" to="/project">我的装修</Link></p>
    <Dialog open={open} onOpenChange={(next) => { if (!save.isPending) setOpen(next); }}><DialogContent title="本次使用的条件" description="填写已确认的信息；未知项留空，避免把假设当作事实。">
      <div className="space-y-4"><ProjectContextEditor value={draft} onChange={setDraft} disabled={save.isPending || save.unknown} />
        {access.read && <p className="text-xs text-muted">{notice}</p>}<MutationNotice mutation={save} />
        {save.error && !save.unknown && <Button size="sm" variant="outline" onClick={async () => { const latest = await project.refetch(); setBaseRevision(latest.data?.project?.revision); }}>查看最新项目版本并保留本次草稿</Button>}
        <div className="sticky bottom-0 -mx-5 -mb-5 flex flex-wrap gap-2 border-t border-line bg-surface p-4 max-md:-mx-4 max-md:-mb-4 max-md:mt-auto">
          <Button onClick={useOnce} disabled={save.isPending || save.unknown}>仅用于本次</Button>
          <Button variant="outline" disabled={!writable || save.isPending || save.unknown} onClick={() => save.submit({ profile: { ...currentProject!.profile, conditions: draft } }, capabilities.data!.primary_epoch, baseRevision)}>保存为项目条件并用于本次</Button>
        </div>
      </div>
    </DialogContent></Dialog>
  </section>;
}
