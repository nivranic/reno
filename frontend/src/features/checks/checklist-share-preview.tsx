import { useEffect, useRef, useState } from "react";
import { previewChecklistShare, type ChecklistShareSelection, type ChecklistShareSummary, type PersonalRead } from "@/lib/personal-api";
import { ApiError } from "@/lib/api";
import { isPersonalAbort, usePersonalTransfer } from "@/features/project/use-personal-transfer";
import { ErrorState, InlineSpinner } from "@/components/shared/states";
import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent, DialogTrigger } from "@/components/ui/dialog";

const STATE_LABELS = { active: "核对中", completed: "本清单项目已完成", archived: "已归档" };
const RESULT_LABELS = { unchecked: "未核对", pass: "符合本项", fail: "不符合本项", unable_to_verify: "待核实", not_applicable: "不适用" };
const BASIS_LABELS = { standard_clause: "规范条款", manufacturer_instruction: "产品说明书", personal_requirement: "个人要求", video_suggestion: "视频建议（仅供观察）", observation_only: "观察项（判据待核实）" };

type PreviewProps = {
  projectId: string; selection: readonly ChecklistShareSelection[]; read?: PersonalRead;
  available: boolean; viewVersion: number | string; onSelectionChanged: () => void;
};
export function ChecklistSharePreview({ projectId, selection, read, available, viewVersion, onSelectionChanged }: PreviewProps) {
  // Revocation/refresh unmounts the dialog. Restoring access never reopens it.
  if (!available || !projectId || !read || selection.length !== 1) return <Button size="sm" variant="outline" disabled>预览本人摘要</Button>;
  // Only selection metadata and the detail query epoch; never private raw data.
  const key = JSON.stringify([projectId, read.scope, read.version, read.mode, selection, viewVersion]);
  return <AvailablePreview key={key} projectId={projectId} selection={selection} read={read} onSelectionChanged={onSelectionChanged} />;
}

type PreviewState = { phase: "idle" | "loading" } | { phase: "error"; error: ApiError }
  | { phase: "ready"; summary: ChecklistShareSummary };
function AvailablePreview({ projectId, selection, read, onSelectionChanged }: Omit<PreviewProps, "available" | "viewVersion" | "read"> & { read: PersonalRead }) {
  const [open, setOpen] = useState(false);
  const [preview, setPreview] = useState<PreviewState>({ phase: "idle" });
  const transfer = usePersonalTransfer(read);
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => { active.current?.abort(); active.current = null; }, []);
  const request = async () => {
    active.current?.abort();
    const controller = new AbortController(); active.current = controller;
    setPreview({ phase: "loading" });
    try {
      const result = await transfer(async (scopeSignal) => {
        const abort = () => controller.abort();
        scopeSignal.addEventListener("abort", abort, { once: true });
        try {
          scopeSignal.throwIfAborted();
          return await previewChecklistShare(projectId, selection, read, controller.signal);
        } finally { scopeSignal.removeEventListener("abort", abort); }
      });
      if (controller.signal.aborted || active.current !== controller) return;
      setPreview({ phase: "ready", summary: result.snapshot.checklists[0] });
    } catch (error) {
      if (controller.signal.aborted || active.current !== controller || isPersonalAbort(error)) return;
      const controlled = error instanceof ApiError ? error : new ApiError(0, "清单摘要读取失败，请重试。", "");
      setPreview({ phase: "error", error: controlled.status === 409
        ? new ApiError(409, "清单内容或读取范围已变化，请重新读取清单后再预览。", controlled.url) : controlled });
    } finally { if (active.current === controller) active.current = null; }
  };
  const changeOpen = (next: boolean) => {
    if (!next) { active.current?.abort(); active.current = null; setPreview({ phase: "idle" }); }
    setOpen(next);
    if (next) void request();
  };
  return <Dialog open={open} onOpenChange={changeOpen}>
    <DialogTrigger asChild><Button size="sm" variant="outline">预览本人摘要</Button></DialogTrigger>
    <DialogContent title="现场清单本人摘要" description="摘要预览，尚未生成分享链接。">
      <div className="space-y-4 max-md:flex max-md:min-h-0 max-md:flex-1 max-md:flex-col">
        <p className="text-[13px] text-muted">清单和核对项标题由你填写，请自行核对是否包含私人信息。</p>
        <p className="text-[13px] text-muted">结果仅表示所记录的本项核对情况，不等于工程验收合格。</p>
        {preview.phase === "loading" ? <InlineSpinner label="读取清单摘要…" /> : null}
        {preview.phase === "error" ? <ErrorState error={preview.error} context="清单摘要"
          onRetry={preview.error.status === 409 ? undefined : () => void request()} /> : null}
        {preview.phase === "ready" ? <Summary summary={preview.summary} /> : null}
        <div className="sticky bottom-0 -mx-5 -mb-5 flex flex-wrap gap-2 border-t border-line bg-surface p-4 max-md:-mx-4 max-md:-mb-4 max-md:mt-auto">
          {preview.phase === "error" && preview.error.status === 409 ? <Button onClick={() => { changeOpen(false); onSelectionChanged(); }}>重新读取清单</Button> : null}
          <DialogClose asChild><Button variant="ghost">关闭预览</Button></DialogClose>
        </div>
      </div>
    </DialogContent>
  </Dialog>;
}

function Summary({ summary }: { summary: ChecklistShareSummary }) {
  return <article className="space-y-3 rounded-panel border border-line bg-surface-2 p-4 text-[13px]">
    <h3 className="whitespace-pre-wrap break-words font-semibold text-ink">{summary.title}</h3>
    <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted">
      <span>{STATE_LABELS[summary.state]}</span><span>版本 {summary.revision}</span>
      <span>{summary.lifecycle_state === "archived" ? "归档状态：已归档" : "归档状态：未归档"}</span>
      <span className="break-all">更新于 {summary.updated_at}</span>
    </div>
    <p>{summary.progress.complete}/{summary.progress.total} 项完成</p>
    {summary.items.length === 0 ? <p className="text-muted">暂无核对项。</p> : <ol className="space-y-3">
      {summary.items.map((item, index) => <li key={index} className="space-y-1 border-t border-line pt-3">
        <h4 className="whitespace-pre-wrap break-words font-medium">{index + 1}. {item.title}</h4>
        <p className="text-xs text-muted">条目版本 {item.revision} · {BASIS_LABELS[item.basis_kind]}</p>
        <p>{item.current_result === "pass" && item.basis_kind === "personal_requirement" ? "符合个人本项要求" : RESULT_LABELS[item.current_result]}</p>
        <p className={`text-xs ${item.needs_recheck ? "text-st-wait" : "text-muted"}`}>{item.needs_recheck ? "依据需复看" : "当前未标记需复看"}</p>
      </li>)}
    </ol>}
  </article>;
}
