import { Link } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { Loader2, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { EmptyState, ErrorState, ListSkeleton, RefreshWarning } from "@/components/shared/states";
import { taskCapabilitiesQuery, useMaintenanceTasks, usePauseTask, useResumeTask, useRetryTask, taskDate, type TaskKind } from "@/lib/task-api";
import { waitReasonText } from "@/lib/budget-api";
import { errMessage } from "@/app/query-utils";

const statusText = { pending: "已排队", running: "处理中", done: "已完成", failed: "失败", superseded: "已合并" };
const taskText = { process: "视频处理", judge: "更新争议", report: "更新报告", ingest: "视频导入", favlist: "收藏夹导入" };

export function TaskStatusPanel({ kind, limit = 6, hideWhenEmpty = false }: { kind?: TaskKind; limit?: number; hideWhenEmpty?: boolean }) {
  const query = useMaintenanceTasks();
  const capabilities = useQuery(taskCapabilitiesQuery);
  const retry = useRetryTask();
  const pause = usePauseTask();
  const resume = useResumeTask();
  const controlling = pause.isPending || resume.isPending;
  const rows = (query.data?.tasks ?? []).filter((task) => !kind || task.kind === kind).slice(0, limit);
  if (hideWhenEmpty && query.data && !query.isError && rows.length === 0) return null;
  return <section className="mb-4 rounded-panel border border-line bg-surface p-4" aria-label="处理任务">
    <div className="mb-3 flex items-center justify-between gap-2">
      <h2 className="text-[14.5px] font-semibold text-ink">最近任务</h2>
      <Button size="iconSm" variant="ghost" aria-label="刷新任务" onClick={() => void query.refetch()}>
        <RefreshCw size={14} className={query.isFetching ? "animate-spin" : ""} />
      </Button>
    </div>
    {query.isPending ? <ListSkeleton rows={2} /> : query.isError && !query.data ?
      <ErrorState error={query.error} context="任务" onRetry={() => void query.refetch()} /> : <>
        {query.isError ? <RefreshWarning onRetry={() => void query.refetch()} /> : null}
        {rows.length === 0 ? <EmptyState compact title="暂无处理任务" desc="新任务提交后，会在这里显示实际进度。" /> :
          <ul className="space-y-2">
            {rows.map((task) => <li key={task.task_id} className="flex flex-wrap items-center gap-2 rounded-ctl bg-surface-2/40 px-3 py-2.5 text-[12.5px]">
              <Badge tone={task.status === "done" ? "ok" : task.status === "failed" ? "bad" : task.status === "running" ? "run" : "wait"}>
                {task.status === "running" ? <Loader2 size={12} className="animate-spin" /> : null}
                {task.manual_paused ? "已手动暂停" : task.wait_mode && task.wait_mode !== "none" ? "等待条件允许" : statusText[task.status]}
              </Badge>
              <span className="text-ink-2">{task.kind === "report" && task.payload.mode === "summary" ? "AI 全库总结" : taskText[task.kind]}</span>
              {task.video_id ? <Link className="text-acc hover:underline" to={`/videos/${encodeURIComponent(task.video_id)}`}>查看视频</Link> : null}
              <span className="ml-auto text-[11px] text-muted">{taskDate(task.updated_at)}</span>
              {task.can_retry && capabilities.data?.tasks ? <Button size="sm" variant="outline"
                disabled={retry.isPending} onClick={() => retry.mutate(task.job_id)}>
                {retry.isPending && retry.variables === task.job_id ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
                {task.kind === "process" ? "继续未完成步骤" : "重试任务"}
              </Button> : null}
              {task.can_pause && capabilities.data?.tasks ? <Button size="sm" variant="outline" disabled={controlling}
                onClick={() => pause.mutate(task.job_id)}>暂停任务</Button> : null}
              {task.can_resume && capabilities.data?.tasks ? <Button size="sm" variant="outline" disabled={controlling}
                onClick={() => resume.mutate(task.job_id)}>请求继续任务</Button> : null}
              {task.wait_mode && task.wait_mode !== "none" ? <p className="w-full text-muted">{waitReasonText(task.blocked_reason)} ·
                {task.not_before === null || task.not_before === undefined ? " 尚无计划继续时间，等待条件核对。" : ` 计划继续 ${taskDate(new Date(task.not_before).toISOString())}`}</p> : null}
              {task.error ? <p className="w-full break-words text-st-bad">{task.error}</p> : null}
            </li>)}
          </ul>}
        {retry.isError ? <p role="alert" className="mt-2 text-[12px] text-st-bad">{errMessage(retry.error)}；可再次点击同一任务核对提交。</p> : null}
        {pause.isError || resume.isError ? <p role="alert" className="mt-2 text-[12px] text-st-bad">{errMessage(pause.error ?? resume.error)}；请刷新核对原任务状态。</p> : null}
      </>}
  </section>;
}
