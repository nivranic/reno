import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { Spinner } from "@/components/ui/skeleton";

export { ListSkeleton } from "@/components/ui/skeleton";

/** Page title block used under the topbar: title + optional description +
 * right-aligned actions. Detail/workbench pages may skip the max width. */
export function PageHeader({
  title,
  desc,
  actions,
  className,
}: {
  title: ReactNode;
  desc?: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("mb-4 flex flex-wrap items-end justify-between gap-3", className)}>
      <div className="min-w-0">
        <h1 className="text-[19px] font-semibold leading-7 text-ink">{title}</h1>
        {desc ? <p className="mt-0.5 text-[13px] text-muted">{desc}</p> : null}
      </div>
      {actions ? (
        <div className="flex shrink-0 items-center gap-2 print:hidden">{actions}</div>
      ) : null}
    </div>
  );
}

/** EmptyState: page-level by default; compact for dense panels (workbench
 *  side panels, compare sidebars) so empty states stay single-entry. */
export function EmptyState({
  icon,
  title,
  desc,
  action,
  compact,
}: {
  icon?: ReactNode;
  title: string;
  desc?: ReactNode;
  action?: ReactNode;
  compact?: boolean;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-1.5 rounded-panel border border-dashed border-line-2 bg-surface/50 text-center",
        compact ? "px-4 py-8" : "gap-2 px-6 py-14",
      )}
    >
      {icon ? <div className="text-muted">{icon}</div> : null}
      <p className={cn(compact ? "text-[13px]" : "text-[15px]", "font-medium text-ink-2")}>
        {title}
      </p>
      {desc ? (
        <p
          className={cn(
            compact ? "max-w-sm text-[12px]" : "max-w-md text-[13px]",
            "leading-relaxed text-muted",
          )}
        >
          {desc}
        </p>
      ) : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}

/** In-place error for a failed first load: keeps diagnostics, offers retry. */
export function ErrorState({
  error,
  onRetry,
  context,
}: {
  error: unknown;
  onRetry?: () => void;
  context?: string;
}) {
  const msg =
    error instanceof Error
      ? error.message
      : error && typeof error === "object" && "message" in error
        ? String((error as { message?: unknown }).message ?? "")
        : error
          ? String(error)
          : "未知错误";
  const status =
    error && typeof error === "object" && "status" in error
      ? ` (HTTP ${String((error as { status: unknown }).status)})`
      : "";
  return (
    <div
      role="alert"
      className="rounded-panel border border-st-bad-line bg-st-bad-bg px-5 py-4"
    >
      <p className="text-sm font-medium text-st-bad">
        {context ? `${context}加载失败` : "加载失败"}
        {status}
      </p>
      <p className="mt-1 break-all font-mono text-xs text-st-bad/80">{msg}</p>
      {onRetry ? (
        <button
          onClick={onRetry}
          className="mt-3 cursor-pointer rounded-ctl border border-st-bad-line bg-surface px-3 py-1.5 text-[13px] font-medium text-st-bad hover:brightness-95"
        >
          重试
        </button>
      ) : null}
    </div>
  );
}

/** Light, non-blocking hint that a background refresh failed (old data stays). */
export function RefreshWarning({ onRetry }: { onRetry?: () => void }) {
  return (
    <div className="mb-3 flex items-center gap-2 rounded-ctl border border-st-wait-line bg-st-wait-bg px-3 py-1.5 text-[12.5px] text-st-wait">
      <span>后台刷新失败,当前显示的是上次成功的数据</span>
      {onRetry ? (
        <button onClick={onRetry} className="cursor-pointer font-medium underline underline-offset-2">
          重试
        </button>
      ) : null}
    </div>
  );
}

export function InlineSpinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 py-8 text-[13px] text-muted">
      <Spinner />
      {label ?? "加载中…"}
    </div>
  );
}
