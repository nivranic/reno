import { Loader2 } from "lucide-react";
import { cn } from "@/lib/cn";

export function Spinner({ className, label }: { className?: string; label?: string }) {
  return (
    <span role="status" aria-label={label ?? "加载中"} className="inline-flex items-center">
      <Loader2 className={cn("animate-spin text-muted", className)} size={18} />
    </span>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse rounded-ctl bg-surface-2", className)} />;
}

/** Full-panel skeleton for first loads (background refreshes keep old data). */
export function ListSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div className="space-y-2" aria-hidden>
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-12 w-full" />
      ))}
    </div>
  );
}
