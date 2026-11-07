import { queryOptions } from "@tanstack/react-query";
import { apiGet } from "./api";

export interface SyncQueueItem {
  item_id: number;
  status: "queued" | "claimed" | "retry_wait" | "budget_wait" | "dead" | "done" | "unknown";
  attempts: number;
  last_error_class: string | null;
  created_at: string | null;
  updated_at: string | null;
  retry_at: string | null;
  video_id: string | null;
  source_host: string | null;
  source_path: string | null;
  claim_active: boolean;
  can_view_video: boolean;
}

export interface HomeSyncStatus {
  enabled: boolean;
  configured: boolean;
  state: string;
  updated_at: string | null;
  last_success_at: string | null;
  retry_at: string | null;
  error_code: string | null;
  seeded: boolean;
  has_pending_publication: boolean;
  work_phase: "downloaded" | "processed" | "judged" | "reported" | null;
  lease_valid: boolean;
}

export interface SyncStatus {
  mode: "cloud" | "home";
  node_role: string;
  configured: boolean;
  compatible: boolean | null;
  paired: boolean | null;
  online: boolean;
  last_seen: string | null;
  lease_until: string | null;
  counts: Record<string, number>;
  expired_claims: number;
  retry_due: number;
  budget_due?: number;
  recovery?: { state: "running" | "held" | "resume_pending"; generation: number; revision: number };
  home: HomeSyncStatus | null;
  queue: { total: number; offset: number; limit: number; items: SyncQueueItem[] };
}

export const syncKeys = { all: ["sync-status"] as const,
  status: (offset: number, limit: number) => ["sync-status", offset, limit] as const };

const ACTIVE = new Set(["pulling", "pushing_reviews", "uploading", "publishing", "processing", "review_backlog", "pending_delivery"]);
export function syncPollInterval(data?: SyncStatus): number {
  if (!data) return 15_000;
  if (data.recovery?.state === "held" || ["recovery_held", "recovery_blocked", "budget_wait", "source_blocked", "source_failed"].includes(data.home?.state ?? "")) return 15_000;
  if (data.recovery?.state === "resume_pending" || data.home?.state === "recovery_resuming") return 3_000;
  const active = data.mode === "cloud"
    ? (data.counts.queued ?? 0) + (data.counts.claimed ?? 0) + (data.counts.retry_wait ?? 0) + (data.budget_due ?? 0) > 0
    : ACTIVE.has(data.home?.state ?? "") || !!data.home?.has_pending_publication;
  return active ? 3_000 : 15_000;
}

export const syncStatusQuery = (offset = 0, limit = 10) => queryOptions({
  queryKey: syncKeys.status(offset, limit),
  queryFn: ({ signal }) => apiGet<SyncStatus>(`/api/sync-status?offset=${offset}&limit=${limit}`, signal),
  refetchInterval: (query) => syncPollInterval(query.state.data),
  refetchIntervalInBackground: false,
  refetchOnWindowFocus: "always",
});
