/** Minimal toast system — the single surface for transient feedback.
 * Errors are deduped per (key) for a few seconds so a failing query and its
 * retry don't stack identical messages; different operations stay separate. */
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { AlertTriangle, CheckCircle2, Info, X } from "lucide-react";
import { cn } from "@/lib/cn";

type ToastKind = "info" | "success" | "error";
interface ToastItem {
  id: number;
  kind: ToastKind;
  message: string;
  action?: { label: string; run: () => void };
}

interface ToastApi {
  push: (kind: ToastKind, message: string, opts?: { key?: string; action?: ToastItem["action"] }) => void;
  info: (m: string) => void;
  success: (m: string) => void;
  error: (m: string, opts?: { key?: string; action?: ToastItem["action"] }) => void;
}

const ToastCtx = createContext<ToastApi | null>(null);

export function useToast(): ToastApi {
  const api = useContext(ToastCtx);
  if (!api) throw new Error("useToast must be used within ToastProvider");
  return api;
}

const DEDUP_MS = 4000;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const seq = useRef(0);
  const lastByKey = useRef(new Map<string, number>());

  const remove = useCallback((id: number) => {
    setItems((cur) => cur.filter((t) => t.id !== id));
  }, []);

  const push = useCallback<ToastApi["push"]>((kind, message, opts) => {
    const now = Date.now();
    if (opts?.key) {
      const last = lastByKey.current.get(opts.key) ?? 0;
      if (now - last < DEDUP_MS) return; // same origin already shown recently
      lastByKey.current.set(opts.key, now);
    }
    const id = ++seq.current;
    setItems((cur) => [...cur.slice(-3), { id, kind, message, action: opts?.action }]);
    window.setTimeout(() => remove(id), kind === "error" ? 7000 : 4000);
  }, [remove]);

  const api = useMemo<ToastApi>(
    () => ({
      push,
      info: (m) => push("info", m),
      success: (m) => push("success", m),
      error: (m, o) => push("error", m, o),
    }),
    [push],
  );

  return (
    <ToastCtx.Provider value={api}>
      {children}
      <div
        aria-live="polite"
        className="pointer-events-none fixed bottom-4 right-4 z-[70] flex w-[min(92vw,360px)] flex-col gap-2"
      >
        {items.map((t) => (
          <div
            key={t.id}
            role="status"
            className={cn(
              "pointer-events-auto flex items-start gap-2.5 rounded-panel border px-3.5 py-2.5 shadow-lg",
              t.kind === "error"
                ? "border-st-bad-line bg-st-bad-bg text-st-bad"
                : t.kind === "success"
                  ? "border-st-ok-line bg-st-ok-bg text-st-ok"
                  : "border-line bg-surface text-ink-2",
            )}
          >
            {t.kind === "error" ? (
              <AlertTriangle size={15} className="mt-0.5 shrink-0" />
            ) : t.kind === "success" ? (
              <CheckCircle2 size={15} className="mt-0.5 shrink-0" />
            ) : (
              <Info size={15} className="mt-0.5 shrink-0 text-muted" />
            )}
            <div className="min-w-0 flex-1 text-[13px] leading-5">{t.message}</div>
            {t.action ? (
              <button
                onClick={() => {
                  t.action?.run();
                  remove(t.id);
                }}
                className="shrink-0 cursor-pointer font-medium underline underline-offset-2"
              >
                {t.action.label}
              </button>
            ) : null}
            <button
              aria-label="关闭提示"
              onClick={() => remove(t.id)}
              className="shrink-0 cursor-pointer opacity-60 hover:opacity-100"
            >
              <X size={13} />
            </button>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}
