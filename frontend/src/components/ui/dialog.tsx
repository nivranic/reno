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
          // phone: fullscreen task panel instead of a floating box (§14);
          // flex column so forms can pin their action bar via mt-auto
          "max-md:inset-0 max-md:flex max-md:h-dvh max-md:max-h-dvh max-md:w-full max-md:flex-col max-md:translate-x-0 max-md:translate-y-0 max-md:rounded-none max-md:border-0 max-md:p-4",
          className,
        )}
      >
        {/* sticky header: long content scrolls UNDER it, close stays reachable.
            Spacing to the body comes from the wrapper below — a margin here
            would leave a transparent strip with content showing through. */}
        <div className="sticky top-0 z-10 -mx-5 -mt-5 flex shrink-0 items-start justify-between gap-4 border-b border-line bg-surface px-5 pb-3 pt-5 max-md:-mx-4 max-md:-mt-4 max-md:px-4 max-md:pt-4">
          <div className="min-w-0">
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
            className="shrink-0 cursor-pointer rounded-ctl p-2 text-muted transition-colors hover:bg-surface-2 hover:text-ink"
          >
            <X size={16} />
          </DialogPrimitive.Close>
        </div>
        {/* body wrapper: keeps the mobile flex chain (forms pin their action
            bars via mt-auto inside it) and provides the header gap */}
        <div className="pt-4 max-md:flex max-md:min-h-0 max-md:flex-1 max-md:flex-col">
          {children}
        </div>
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
}
