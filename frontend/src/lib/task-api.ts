import { useEffect, useRef } from "react";
import { queryOptions, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiPost } from "./api";
import { qk } from "./queries";
import { newOperationId } from "./id";
import type { DerivationSummary } from "./api-types";
import { budgetKeys, type BudgetWaitReason } from "./budget-api";

export type TaskKind = "process" | "judge" | "report" | "ingest" | "favlist";
export interface MaintenanceTask {
  task_id: number;
  job_id: number;
  kind: TaskKind;
  video_id: string | null;
  status: "pending" | "running" | "done" | "failed" | "superseded";
  attempts: number;
  request_id: string | null;
  payload: { force?: boolean; mode?: "static" | "summary"; threshold?: number;
    target?: string; source?: "url" | "file"; process?: boolean; file_name?: string; url?: string; limit?: number };
  error: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  can_retry: boolean;
  // Older rollback servers omit wait capabilities; absence offers no controls.
  not_before?: number | null;
  blocked_reason?: BudgetWaitReason | null;
  wait_mode?: "none" | "until" | "blocked";
  manual_paused?: boolean;
  budget_operation_id?: string | null;
  can_pause?: boolean;
  can_resume?: boolean;
}

export interface ReportMetadata {
  derivation_summary?: DerivationSummary;
  report_type: string;
  status: "missing" | "generating" | "latest" | "stale" | "failed";
  has_content: boolean;
  stale: boolean;
  generated_at: string | null;
  kb_revision: number | null;
  policy_version: string | null;
}

interface TaskCapabilities { tasks: boolean; node_role: string; task_reason?: string }
export const taskKeys = { tasks: ["maintenance-tasks"] as const, reports: ["report-metadata"] as const,
  capabilities: ["task-capabilities"] as const };
export const isActiveTask = (task: MaintenanceTask) => task.status === "pending" || task.status === "running";
export function taskPollInterval(tasks: MaintenanceTask[] | undefined): number | false {
  if (!tasks?.some(isActiveTask)) return false;
  return tasks.some((task) => task.status === "running" || task.status === "pending" && !task.manual_paused
    && (task.wait_mode === undefined || task.wait_mode === "none"
      || task.wait_mode === "until" && task.not_before !== null && task.not_before !== undefined && task.not_before <= Date.now())) ? 2000 : 15_000;
}

export const taskCapabilitiesQuery = queryOptions({
  queryKey: taskKeys.capabilities,
  queryFn: ({ signal }) => apiGet<TaskCapabilities>("/api/capabilities", signal),
});

export const reportMetadataQuery = queryOptions({
  queryKey: taskKeys.reports,
  queryFn: ({ signal }) => apiGet<{ reports: Record<string, ReportMetadata> }>("/api/reports/meta", signal),
  refetchInterval: (query) => Object.values(query.state.data?.reports ?? {})
    .some((report) => report.status === "generating") ? 2000 : 15_000,
});

export function taskDate(iso: string | null | undefined) {
  if (!iso) return "—";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString("zh-CN", { hour12: false });
}

export function useMaintenanceTasks() {
  const query = useQuery({ queryKey: taskKeys.tasks,
    queryFn: ({ signal }) => apiGet<{ tasks: MaintenanceTask[] }>("/api/tasks", signal),
    refetchInterval: (state) => taskPollInterval(state.state.data?.tasks) });
  const client = useQueryClient();
  const completed = query.data?.tasks.filter((task) => !isActiveTask(task))
    .map((task) => `${task.task_id}:${task.status}:${task.updated_at}`).join("|") ?? "";
  useEffect(() => {
    if (!completed) return;
    for (const queryKey of [qk.reports, qk.conflicts, qk.videos, taskKeys.reports])
      void client.invalidateQueries({ queryKey });
  }, [client, completed]);
  const judged = query.data?.tasks.filter((task) => task.kind === "judge" && task.status === "done")
    .map((task) => `${task.task_id}:${task.updated_at}`).join("|") ?? "";
  useEffect(() => {
    if (!judged) return;
    // Read caches only. Saved answers/comparisons remain snapshots; no model
    // request or reading-session mutation belongs to task polling.
    for (const queryKey of [["video"], ["search"], ["compare"]])
      void client.invalidateQueries({ queryKey });
  }, [client, judged]);
  return query;
}

export const taskRequestId = newOperationId;

/** A failed/unknown response keeps its request id for an explicit safe retry. */
export function useCreateTask(kind: TaskKind, payload?: MaintenanceTask["payload"]) {
  const id = useRef<string | null>(null);
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => {
      id.current ??= taskRequestId();
      return apiPost<MaintenanceTask>("/api/tasks", { kind, payload, request_id: id.current });
    },
    onSuccess: (task) => {
      id.current = null;
      client.setQueryData<{ tasks: MaintenanceTask[] }>(taskKeys.tasks,
        (old) => ({ tasks: [task, ...(old?.tasks ?? []).filter((row) => row.task_id !== task.task_id)] }));
      void client.invalidateQueries({ queryKey: taskKeys.tasks });
      void client.invalidateQueries({ queryKey: taskKeys.reports });
    },
  });
}

export function useRetryTask() {
  const ids = useRef(new Map<number, string>());
  const client = useQueryClient();
  return useMutation({
    mutationFn: (jobId: number) => {
      if (!ids.current.has(jobId)) ids.current.set(jobId, taskRequestId());
      return apiPost<MaintenanceTask>(`/api/jobs/${jobId}/retry`, { request_id: ids.current.get(jobId) });
    },
    onSuccess: (_task, jobId) => {
      ids.current.delete(jobId);
      void client.invalidateQueries({ queryKey: taskKeys.tasks });
      void client.invalidateQueries({ queryKey: taskKeys.reports });
      void client.invalidateQueries({ queryKey: qk.videos });
    },
  });
}

function useTaskControl(action: "pause" | "resume") {
  const client = useQueryClient();
  return useMutation({ mutationFn: (jobId: number) => apiPost<MaintenanceTask>(`/api/jobs/${jobId}/${action}`, {}),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: taskKeys.tasks });
      void client.invalidateQueries({ queryKey: budgetKeys.snapshot });
    },
  });
}
export const usePauseTask = () => useTaskControl("pause");
export const useResumeTask = () => useTaskControl("resume");
