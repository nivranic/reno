/** Accessible dialog built on Radix (focus trap, ESC, focus return).
 * Content composition is kept local to each usage via DialogHeader/Footer. */
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export const Dialog = DialogPrimitive.Root;
export const DialogTrigger = DialogPrimitive.Trigger;
export const DialogClose = DialogPrimitive.Close;

export function DialogContent({
  className,
  children,
  title,
  description,
}: {
  className?: string;
  children: ReactNode;
  title: string;
  description?: string;
}) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/40 backdrop-blur-[2px] data-[state=open]:animate-[fade-in_150ms_ease-out]" />
      <DialogPrimitive.Content
        className={cn(
          "fixed left-1/2 top-1/2 z-50 w-[min(92vw,560px)] max-h-[86vh] -translate-x-1/2 -translate-y-1/2 overflow-y-auto",
          "rounded-panel border border-line bg-surface p-5 shadow-xl",
          "data-[state=open]:animate-[pop-in_160ms_ease-out]",
          // phone: fullscreen task panel instead of a floating box (§14)
          "max-md:inset-0 max-md:h-dvh max-md:max-h-dvh max-md:w-full max-md:translate-x-0 max-md:translate-y-0 max-md:rounded-none max-md:border-0 max-md:p-4",
          className,
        )}
      >
        <div className="mb-4 flex items-start justify-between gap-4">
          <div>
            <DialogPrimitive.Title className="text-[16px] font-semibold text-ink">
              {title}
            </DialogPrimitive.Title>
            {description ? (
              <DialogPrimitive.Description className="mt-1 text-[13px] text-muted">
                {description}
              </DialogPrimitive.Description>
            ) : null}
          </div>
          <DialogPrimitive.Close
            aria-label="关闭"
            className="rounded-ctl p-1 text-muted transition-colors hover:bg-surface-2 hover:text-ink"
          >
            <X size={16} />
          </DialogPrimitive.Close>
        </div>
        {children}
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
}
