import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiPost } from "./api";

export const budgetFields = ["budget_enabled", "budget_timezone", "download_interval_min_seconds",
  "download_interval_max_seconds", "download_daily_videos", "download_rate_bytes_per_second",
  "model_daily_attempts", "model_reported_tokens_stop_at", "model_daily_output_tokens",
  "model_total_tokens_mode", "model_operation_max_attempts", "source_resolution_max_attempts"] as const;
export type BudgetField = typeof budgetFields[number];
export type BudgetValue = boolean | string | number | null;
export type BudgetPolicy = Record<Exclude<BudgetField, "source_resolution_max_attempts">, BudgetValue>;
export interface BudgetSettings {
  effective_policy: BudgetPolicy;
  source_resolution_max_attempts: number;
  fields: Record<BudgetField, { value: BudgetValue; source: "default" | "local" | "environment"; editable: boolean }>;
  settings_revision: string;
}
export type BudgetKind = "model" | "download" | "source_resolution" | "source_metadata" | "source_enumeration";
export type BudgetWaitReason = "daily_limit" | "output_limit" | "reported_tokens_limit" | "attempt_limit"
  | "download_pacing" | "clock_rollback" | "indeterminate" | "result_capacity"
  | "manual_pause" | "ledger_unavailable" | "unsupported_capability";
export interface BudgetAttemptCounts { admissions: number; confirmed: number; held: number; in_flight: number; unknown: number }
export interface BudgetTokenCount { reported: number | null; covered_attempts: number; missing_attempts: number; dispatched_attempts: number }
export interface HomeBudgetTask {
  job_id: number; status: "pending" | "running"; updated_at: string;
  not_before: number | null; blocked_reason: BudgetWaitReason | null; wait_mode: "none" | "until" | "blocked";
  manual_paused: boolean; can_pause: boolean; can_resume: boolean;
}
export interface HomeBudgetControl { available: boolean; task: HomeBudgetTask | null; control_revision: string | null }
export interface BudgetSnapshot {
  settings: BudgetSettings;
  usage: {
    scope: "local_ledger_only"; history_before_ledger: "unknown";
    window: { timezone: string; local_date: string; period_key: string; start_utc: string; end_utc: string };
    attempts: Record<BudgetKind, BudgetAttemptCounts>; download_logical_admissions: number;
    tokens: { input_tokens: BudgetTokenCount; output_tokens: BudgetTokenCount; total_tokens: BudgetTokenCount;
      quality_counts: { reported: number; partial: number; missing: number; invalid: number; unobserved: number } };
    conservative_output_tokens: { held: number; in_flight: number; unknown: number; confirmed_missing_output: number; total: number };
    all_history: { unknown_attempts: number; waiting_operations: number; stored_results: number };
    waiting: { scope: "all_history"; total: number; limit: number;
      operations: { operation_id: string; kind: BudgetKind; root_job_id: number | null; reason: BudgetWaitReason; resume_at: string | null }[] };
  };
  home: HomeBudgetControl;
  capabilities: { read: boolean; edit_policy: boolean; task_control: boolean; home_control: boolean; node_role: "all" | "home" | "cloud" };
}

export const budgetKeys = { snapshot: ["budget-snapshot"] as const };
export function budgetPollInterval(data: BudgetSnapshot | undefined) {
  return Object.values(data?.usage.attempts ?? {}).some((row) => row.in_flight > 0) ? 3000 : 15_000;
}
export const budgetQuery = queryOptions({ queryKey: budgetKeys.snapshot,
  queryFn: ({ signal }) => apiGet<BudgetSnapshot>("/api/budget", signal),
  refetchInterval: (query) => budgetPollInterval(query.state.data),
});
const waitReasons: Record<string, string> = { daily_limit: "今日次数额度已占用",
  output_limit: "输出额度已占用", reported_tokens_limit: "已报告用量达到停止线", attempt_limit: "原操作次数已到上限",
  download_pacing: "等待下载间隔", clock_rollback: "等待核对本机时间", indeterminate: "结果未知，原请求不会自动重发",
  result_capacity: "等待处理结果存储空间", manual_pause: "已手动暂停", ledger_unavailable: "预算账本暂不可用",
  unsupported_capability: "等待核对处理能力" };
export const waitReasonText = (reason: string | null | undefined) => waitReasons[reason ?? ""] ?? "等待条件核对";

export function useSaveBudget() {
  const client = useQueryClient();
  return useMutation({ mutationFn: (body: { expected_settings_revision: string; updates: Partial<Record<BudgetField, BudgetValue>> }) =>
    apiPost<BudgetSettings>("/api/config/budget", body),
    onSuccess: () => { void client.invalidateQueries({ queryKey: budgetKeys.snapshot }); },
  });
}
export function useHomeBudgetControl() {
  const client = useQueryClient();
  return useMutation({ mutationFn: ({ action, revision }: { action: "pause" | "resume"; revision: string }) =>
    apiPost<HomeBudgetControl>(`/api/budget/home/${action}`, { expected_control_revision: revision }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: budgetKeys.snapshot });
      void client.invalidateQueries({ queryKey: ["sync-status"] });
    },
  });
}
