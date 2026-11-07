import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { PersonalRead } from "@/lib/personal-api";
import { registerPersonalTransfer } from "./use-personal-project";

export const isPersonalAbort = (error: unknown) => error instanceof DOMException && error.name === "AbortError";
/** Every transfer is cancelled when its mounted owner or access scope changes.
 * The final abort check prevents a late response from creating an artifact. */
export function usePersonalTransfer(read?: PersonalRead) {
  const client = useQueryClient(); const controllers = useRef(new Set<AbortController>());
  useEffect(() => { const current = controllers.current; return () => { current.forEach((controller) => controller.abort()); current.clear(); }; }, [read?.scope]);
  return async <T,>(run: (signal: AbortSignal) => Promise<T>): Promise<T> => {
    if (!read) throw new DOMException("读取权限尚未确认", "AbortError");
    const controller = new AbortController(); controllers.current.add(controller);
    const unregister = registerPersonalTransfer(client, read, controller);
    try { controller.signal.throwIfAborted(); const result = await run(controller.signal); controller.signal.throwIfAborted(); return result; }
    finally { unregister(); controllers.current.delete(controller); }
  };
}
