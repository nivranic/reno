/** URL-held UI state helpers (search / filters / sort / deep links).
 * Query params are the shareable source of truth; components read and write
 * through these small adapters so serialization stays in one place. */
import { useLayoutEffect, type RefObject } from "react";
import { useLocation, useSearchParams } from "react-router";
import { getReadingState, patchAsk, patchCompare, type ScrollAnchor } from "./reading-session";

export function useUrlParam(key: string, fallback = ""): [string, (v: string) => void] {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const value = params.get(key) ?? fallback;
  const set = (v: string) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (v && v !== fallback) next.set(key, v);
        else next.delete(key);
        return next;
      },
      { replace: true, state: location.state },
    );
  return [value, set];
}

/** Save/restore vertical scroll for list pages so returning from a detail
 * page lands where the user left off (session-scoped, per URL key). */
const positions = new Map<string, ScrollAnchor>();
export function captureScroll(element: HTMLElement): ScrollAnchor {
  const top = element.getBoundingClientRect().top;
  const anchor = [...element.querySelectorAll<HTMLElement>("[data-reading-anchor]")].find((node) => node.getBoundingClientRect().bottom > top + 1);
  return { top: element.scrollTop, anchor: anchor?.dataset.readingAnchor, offset: anchor ? anchor.getBoundingClientRect().top - top : undefined };
}
export function restoreScroll(element: HTMLElement, saved: ScrollAnchor) {
  const anchor = [...element.querySelectorAll<HTMLElement>("[data-reading-anchor]")].find((node) => node.dataset.readingAnchor === saved.anchor);
  element.scrollTop = anchor && typeof saved.offset === "number"
    ? element.scrollTop + anchor.getBoundingClientRect().top - element.getBoundingClientRect().top - saved.offset
    : Math.max(0, Number.isFinite(saved.top) ? saved.top : 0);
}
function readPosition(key: string) {
  if (key === "ask") return getReadingState().ask.scroll;
  if (key === "main:/compare") return getReadingState().compare.scroll;
  return positions.get(key);
}
function savePosition(key: string, saved: ScrollAnchor) {
  if (key === "ask") patchAsk({ scroll: saved });
  else if (key === "main:/compare") patchCompare({ scroll: saved });
  else { positions.set(key, saved); if (positions.size > 50) positions.delete(positions.keys().next().value!); }
}
export function captureReadingPositions() {
  for (const element of document.querySelectorAll<HTMLElement>("[data-reading-scroll]")) {
    const key = element.dataset.readingScroll;
    if (key) savePosition(key, captureScroll(element));
  }
}
export function useReadingScroll(key: string, ref: RefObject<HTMLElement | null>, sessionId?: string) {
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    const saved = readPosition(key);
    let restoring = !!saved;
    let timer = 0;
    const apply = () => { if (restoring && saved) restoreScroll(element, saved); };
    apply();
    const frame = requestAnimationFrame(apply);
    const observer = new ResizeObserver(apply);
    observer.observe(element);
    if (element.firstElementChild) observer.observe(element.firstElementChild);
    const mutation = new MutationObserver(() => {
      if (element.firstElementChild) observer.observe(element.firstElementChild);
      apply();
    });
    mutation.observe(element, { childList: true, subtree: true });
    void document.fonts?.ready.then(apply);
    const stop = () => { restoring = false; observer.disconnect(); mutation.disconnect(); };
    const stopTimer = window.setTimeout(stop, 5000);
    const save = () => { if (!restoring) savePosition(key, captureScroll(element)); };
    const onScroll = () => { window.clearTimeout(timer); timer = window.setTimeout(save, 100); };
    element.addEventListener("scroll", onScroll, { passive: true });
    element.addEventListener("wheel", stop, { passive: true });
    element.addEventListener("touchstart", stop, { passive: true });
    element.addEventListener("pointerdown", stop, { passive: true });
    element.addEventListener("keydown", stop);
    const pagehide = () => savePosition(key, captureScroll(element));
    window.addEventListener("pagehide", pagehide);
    return () => {
      observer.disconnect(); mutation.disconnect(); cancelAnimationFrame(frame);
      window.clearTimeout(timer); window.clearTimeout(stopTimer);
      element.removeEventListener("scroll", onScroll); element.removeEventListener("wheel", stop);
      element.removeEventListener("touchstart", stop); element.removeEventListener("pointerdown", stop);
      element.removeEventListener("keydown", stop); window.removeEventListener("pagehide", pagehide);
    };
  }, [key, ref, sessionId]);
}

/** Compatibility for existing list pages: main is the actual scroll owner. */
export function useScrollRestore(key: string) {
  return {
    save: () => { const el = document.querySelector("main"); if (el) savePosition(key, captureScroll(el)); },
    restore: () => { const el = document.querySelector("main"); const saved = readPosition(key); if (el && saved) requestAnimationFrame(() => restoreScroll(el, saved)); },
  };
}
export function clearReadingPositions() { positions.clear(); }
