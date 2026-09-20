import { cva, type VariantProps } from "class-variance-authority";
import type { HTMLAttributes } from "react";
import { cn } from "@/lib/cn";

export type Tone = NonNullable<VariantProps<typeof badgeVariants>["tone"]>;

const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11.5px] leading-4 font-medium whitespace-nowrap",
  {
    variants: {
      tone: {
        neutral: "bg-surface-2 text-ink-2 border-line",
        ok: "bg-st-ok-bg text-st-ok border-st-ok-line",
        run: "bg-st-run-bg text-st-run border-st-run-line",
        wait: "bg-st-wait-bg text-st-wait border-st-wait-line",
        bad: "bg-st-bad-bg text-st-bad border-st-bad-line",
        review: "bg-st-review-bg text-st-review border-st-review-line",
        acc: "bg-acc-soft text-acc border-acc-line",
      },
    },
    defaultVariants: { tone: "neutral" },
  },
);

export interface BadgeProps
  extends HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, tone, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ tone }), className)} {...props} />;
}
