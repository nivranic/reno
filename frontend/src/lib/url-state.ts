/** URL-held UI state helpers (search / filters / sort / deep links).
 * Query params are the shareable source of truth; components read and write
 * through these small adapters so serialization stays in one place. */
import { useSearchParams } from "react-router";

export function useUrlParam(key: string, fallback = ""): [string, (v: string) => void] {
  const [params, setParams] = useSearchParams();
  const value = params.get(key) ?? fallback;
  const set = (v: string) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (v && v !== fallback) next.set(key, v);
        else next.delete(key);
        return next;
      },
      { replace: true },
    );
  return [value, set];
}

/** Save/restore vertical scroll for list pages so returning from a detail
 * page lands where the user left off (session-scoped, per URL key). */
export function useScrollRestore(key: string) {
  const storeKey = `reno-scroll:${key}`;
  const save = () => {
    try {
      sessionStorage.setItem(storeKey, String(window.scrollY));
    } catch {
      /* private mode */
    }
  };
  const restore = () => {
    try {
      const y = Number(sessionStorage.getItem(storeKey));
      if (Number.isFinite(y) && y > 0) window.scrollTo(0, y);
    } catch {
      /* ignore */
    }
  };
  return { save, restore };
}
