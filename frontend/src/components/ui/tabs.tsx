/** Simple controlled tabs (no portal, keyboard accessible via roving buttons). */
import { cn } from "@/lib/cn";
import type { ReactNode } from "react";

export interface TabItem<T extends string> {
  value: T;
  label: ReactNode;
}

export function Tabs<T extends string>({
  items,
  value,
  onChange,
  className,
  ariaLabel,
}: {
  items: TabItem<T>[];
  value: T;
  onChange: (v: T) => void;
  className?: string;
  ariaLabel?: string;
}) {
  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      className={cn(
        "inline-flex items-center gap-1 rounded-ctl border border-line bg-surface-2 p-1",
        className,
      )}
    >
      {items.map((it) => {
        const active = it.value === value;
        return (
          <button
            key={it.value}
            role="tab"
            aria-selected={active}
            onClick={() => onChange(it.value)}
            className={cn(
              "cursor-pointer rounded-[6px] px-3 py-1.5 text-[13px] font-medium transition-colors duration-150",
              active
                ? "bg-surface text-ink shadow-sm"
                : "text-muted hover:text-ink-2",
            )}
          >
            {it.label}
          </button>
        );
      })}
    </div>
  );
}
