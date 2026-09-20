import { cva, type VariantProps } from "class-variance-authority";
import type { ButtonHTMLAttributes } from "react";
import { cn } from "@/lib/cn";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-1.5 rounded-ctl font-medium whitespace-nowrap select-none transition-colors duration-150 disabled:pointer-events-none disabled:opacity-50 cursor-pointer",
  {
    variants: {
      variant: {
        default: "bg-acc text-white dark:text-acc-contrast hover:bg-acc-strong",
        outline:
          "border border-line-2 bg-surface text-ink hover:bg-surface-2 hover:border-acc",
        ghost: "text-ink-2 hover:bg-surface-2 hover:text-ink",
        soft: "bg-acc-soft text-acc border border-acc-line hover:brightness-[0.97]",
        danger: "bg-danger text-white hover:opacity-90",
      },
      size: {
        sm: "h-8 px-3 text-[13px]",
        md: "h-9 px-4 text-sm",
        lg: "h-10 px-5 text-[15px]",
        icon: "h-9 w-9 p-0",
        iconSm: "h-7 w-7 p-0",
      },
    },
    defaultVariants: { variant: "default", size: "md" },
  },
);

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {}

export function Button({ className, variant, size, type, ...props }: ButtonProps) {
  return (
    <button
      type={type ?? "button"}
      className={cn(buttonVariants({ variant, size }), className)}
      {...props}
    />
  );
}
