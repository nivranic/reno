import { apiGet, apiText } from "./api";

export const RECONCILIATION_REASONS = ["active", "stale", "missing", "legacy_unbound", "invalid_time"] as const;
export type ReconciliationReason = typeof RECONCILIATION_REASONS[number];
export type ReconciliationScope = "attention" | "history" | "all";
export interface ReconciliationFilters { reason: ReconciliationReason | "all"; scope: ReconciliationScope }
export interface ReconciliationItem {
  record_id: string;
  event_id: string | null;
  decision_id: number;
  target_kind: "atom" | "conflict" | "unknown";
  target_id: string | null;
  binding_status: ReconciliationReason;
  attention_state: "current" | "historical";
  needs_attention: boolean;
  decision_valid: boolean;
  action: string;
  decided_at: string | null;
  created_at: string | null;
  note: string | null;
  title: string | null;
  snapshot_summary: string | null;
  current_summary: string | null;
  target_url: string | null;
  source_links: { video_id: string; url: string; title?: string | null }[];
  superseded_by: string | null;
}
export interface ReconciliationResponse {
  version: { library_id: string; library_epoch: number; kb_revision: number };
  event_watermark: { stream: { library_id: string; library_epoch: number }; high_watermark: number };
  visibility_signature: string;
  evaluated_at?: string;
  summary: {
    total: number;
    needs_attention: number;
    historical: number;
    by_reason: Record<ReconciliationReason, { total: number; current: number; historical: number }>;
  };
  filters: ReconciliationFilters;
  offset: number;
  limit: number;
  total: number;
  items: ReconciliationItem[];
}

export function getReconciliation(filters: ReconciliationFilters, offset = 0, limit = 30, signal?: AbortSignal) {
  const params = new URLSearchParams({ ...filters, offset: String(offset), limit: String(limit) });
  return apiGet<ReconciliationResponse>(`/api/reconciliation?${params}`, signal);
}

export function exportReconciliation(filters: ReconciliationFilters, signal?: AbortSignal) {
  return apiText(`/api/reconciliation/export?${new URLSearchParams({ ...filters })}`, signal);
}

/** Only server-approved local reading routes can become actionable links. */
export function reconciliationLink(value: string | null): string | null {
  if (!value || !value.startsWith("/") || value.startsWith("//") || /[\\\s]/.test(value)) return null;
  try {
    const parsed = new URL(value, "https://reno.invalid");
    if (parsed.origin !== "https://reno.invalid"
        || !(/^\/videos\/[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(parsed.pathname) || parsed.pathname === "/conflicts")) return null;
    if ([...parsed.searchParams.keys()].some((key) => !["t", "conflict", "target"].includes(key))) return null;
    if (parsed.searchParams.has("t") && !/^\d+$/.test(parsed.searchParams.get("t") ?? "")) return null;
    return `${parsed.pathname}${parsed.search}${parsed.hash}`;
  } catch { return null; }
}

export function downloadReconciliation(markdown: string) {
  const url = URL.createObjectURL(new Blob([markdown], { type: "text/markdown;charset=utf-8" }));
  const revoke = URL.revokeObjectURL.bind(URL);
  const link = document.createElement("a");
  link.href = url;
  link.download = "reconciliation.md";
  link.hidden = true;
  document.body.append(link);
  try { link.click(); }
  finally { link.remove(); window.setTimeout(() => revoke(url), 1_000); }
}
