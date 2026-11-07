import { useEffect, useRef, useState } from "react";
import { previewDecisionShare, type DecisionShareSelection, type DecisionShareSummary, type PersonalRead } from "@/lib/personal-api";
import { ApiError } from "@/lib/api";
import { isPersonalAbort, usePersonalTransfer } from "@/features/project/use-personal-transfer";
import { ErrorState, InlineSpinner } from "@/components/shared/states";
import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent, DialogTrigger } from "@/components/ui/dialog";
import { DECISION_STATES } from "./decision-dialog";

export type { DecisionShareSummary } from "@/lib/personal-api";

export function DecisionSharePreview({ projectId, selection, read, available, onSelectionChanged }: {
  projectId: string;
  selection: readonly DecisionShareSelection[];
  read?: PersonalRead;
  available: boolean;
  onSelectionChanged: () => void;
}) {
  // Revocation/failure unmounts the open state; restoring access never reopens it.
  if (!available || !projectId || !read || selection.length === 0) return <Button size="sm" variant="outline" disabled>预览分享摘要</Button>;
  const key = JSON.stringify([projectId, read.scope, read.version, selection]);
  return <AvailablePreview key={key} projectId={projectId} selection={selection} read={read} onSelectionChanged={onSelectionChanged} />;
}

type PreviewState = { phase: "idle" | "loading" } | { phase: "error"; error: ApiError } | { phase: "ready"; summaries: DecisionShareSummary[] };
function AvailablePreview({ projectId, selection, read, onSelectionChanged }: {
  projectId: string; selection: readonly DecisionShareSelection[]; read: PersonalRead; onSelectionChanged: () => void;
}) {
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
          return await previewDecisionShare(projectId, selection, read, controller.signal);
        } finally { scopeSignal.removeEventListener("abort", abort); }
      });
      if (controller.signal.aborted || active.current !== controller) return;
      setPreview({ phase: "ready", summaries: result.snapshot.decisions });
    } catch (error) {
      if (controller.signal.aborted || active.current !== controller || isPersonalAbort(error)) return;
      const controlled = error instanceof ApiError ? error : new ApiError(0, "分享摘要读取失败，请重试。", "");
      setPreview({ phase: "error", error: controlled.status === 409
        ? new ApiError(409, "所选决定或读取范围已变化，请重新读取列表后重新选择。", controlled.url) : controlled });
    } finally { if (active.current === controller) active.current = null; }
  };
  const changeOpen = (next: boolean) => {
    if (!next) { active.current?.abort(); active.current = null; setPreview({ phase: "idle" }); }
    setOpen(next);
    if (next) void request();
  };
  return <Dialog open={open} onOpenChange={changeOpen}>
    <DialogTrigger asChild><Button size="sm" variant="outline">预览分享摘要</Button></DialogTrigger>
    <DialogContent title="分享摘要预览" description="摘要预览，尚未生成分享链接。">
      <div className="space-y-4 max-md:flex max-md:min-h-0 max-md:flex-1 max-md:flex-col">
        <p className="text-[13px] text-muted">标题、结论和理由由你填写，请自行核对是否包含私人信息。</p>
        {preview.phase === "loading" ? <InlineSpinner label="读取分享摘要…" /> : null}
        {preview.phase === "error" ? <ErrorState error={preview.error} context="分享摘要"
          onRetry={preview.error.status === 409 ? undefined : () => void request()} /> : null}
        {preview.phase === "ready" ? <div className="space-y-3">
          {preview.summaries.map((summary, index) => <article key={index} className="space-y-3 rounded-panel border border-line bg-surface-2 p-4 text-[13px]">
            <h3 className="whitespace-pre-wrap break-words font-semibold text-ink">{summary.title}</h3>
            <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted">
              <span>{DECISION_STATES[summary.state]}</span><span>版本 {summary.revision}</span>
              <span className="break-all">更新于 {summary.updated_at}</span>
            </div>
            <dl className="space-y-3">
              <div><dt className="text-xs text-muted">个人结论</dt><dd className="mt-1 whitespace-pre-wrap break-words text-ink-2">{summary.decision_text.trim() ? summary.decision_text : "尚未决定"}</dd></div>
              <div><dt className="text-xs text-muted">理由</dt><dd className="mt-1 whitespace-pre-wrap break-words text-ink-2">{summary.decision_reason.trim() ? summary.decision_reason : "未填写理由"}</dd></div>
            </dl>
            <p className={`text-xs ${summary.needs_recheck ? "text-st-wait" : "text-muted"}`}>{summary.needs_recheck ? "依据需复看" : "当前未标记需复看"}</p>
          </article>)}
        </div> : null}
        <div className="sticky bottom-0 -mx-5 -mb-5 flex flex-wrap gap-2 border-t border-line bg-surface p-4 max-md:-mx-4 max-md:-mb-4 max-md:mt-auto">
          {preview.phase === "error" && preview.error.status === 409 ? <Button onClick={onSelectionChanged}>重新读取并重选</Button> : null}
          <DialogClose asChild><Button variant="ghost">关闭预览</Button></DialogClose>
        </div>
      </div>
    </DialogContent>
  </Dialog>;
}
