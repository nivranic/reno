/** Race-safe evidence frame loader.
 *
 * Contract (task §6.3): rapid clicks A→B→C with responses landing C,A,B must
 * end showing C's frame. Guard = monotonic request sequence checked in every
 * completion callback. The image URL is deterministic per (video, ms) — the
 * backend caches extracted JPEGs on disk, and the browser HTTP-caches the
 * response, so NO Date.now() cache-buster is needed (the old UI busted the
 * cache on every view for no reason). While a newer request is pending, the
 * previous frame stays visible but is explicitly labeled as the previous
 * timestamp; failures surface a retry affordance scoped to the frame panel. */
import { useEffect, useRef, useState } from "react";
import { frameUrl } from "@/lib/api";

export interface FrameState {
  /** url of the image currently safe to display (may be one request behind) */
  url: string | null;
  /** ms the displayed image was captured at */
  shownMs: number | null;
  /** ms currently requested */
  pendingMs: number | null;
  loading: boolean;
  error: boolean;
  /** bump to force a retry of the same ms */
  retryToken: number;
}

export function useFrame(videoId: string | null, ms: number | null) {
  const [state, setState] = useState<FrameState>({
    url: null,
    shownMs: null,
    pendingMs: null,
    loading: false,
    error: false,
    retryToken: 0,
  });
  const seq = useRef(0);

  useEffect(() => {
    if (videoId == null || ms == null) return;
    const mySeq = ++seq.current;
    const url = frameUrl(videoId, ms);
    setState((st) => ({
      ...st,
      pendingMs: ms,
      loading: true,
      error: false, // optimistic: previous error cleared on new request
    }));
    const img = new Image();
    img.onload = () => {
      if (seq.current !== mySeq) return; // superseded by a newer request
      setState({ url, shownMs: ms, pendingMs: ms, loading: false, error: false, retryToken: 0 });
    };
    img.onerror = () => {
      if (seq.current !== mySeq) return;
      setState((st) => ({ ...st, pendingMs: ms, loading: false, error: true }));
    };
    img.src = url;
    return () => {
      // unmount / superseded: detach handlers so a late load can't setState
      img.onload = null;
      img.onerror = null;
    };
    // retryToken participates: same ms can be re-attempted
  }, [videoId, ms, state.retryToken]);

  const retry = () => setState((st) => ({ ...st, retryToken: st.retryToken + 1 }));

  return { ...state, stale: state.shownMs !== null && state.shownMs !== state.pendingMs, retry };
}
