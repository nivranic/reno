/** Three-lane modality timeline with zoom/pan/aggregate, smooth playhead,
 * and keyboard access. Pointer/keyboard actions emit locate commands; the
 * playhead itself is DOM-only (rAF) so playback never re-renders React. */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { TimelineEvent } from "@/lib/api-types";
import { fmtMs } from "@/lib/time";
import { cn } from "@/lib/cn";

const MODS = ["ASR", "OCR", "VIS"] as const;
const LANE_H = 12;
const LANE_GAP = 3;
const RULER_H = 16;
const TICK_PX = 4; // minimum horizontal separation before aggregation

export function Timeline({
  events,
  durationMs,
  videoRef,
  onLocate,
  follow,
}: {
  events: TimelineEvent[];
  durationMs: number;
  videoRef: React.RefObject<HTMLVideoElement | null>;
  onLocate: (ms: number) => void;
  follow: boolean;
}) {
  const boxRef = useRef<HTMLDivElement>(null);
  const playheadRef = useRef<HTMLDivElement>(null);
  const [view, setView] = useState<{ start: number; end: number }>({ start: 0, end: durationMs });
  const [width, setWidth] = useState(600);

  // reset view when the video changes
  useEffect(() => {
    setView({ start: 0, end: durationMs });
  }, [durationMs]);

  // track container width (resize)
  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width;
      if (w && w > 0) setWidth(w);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const span = Math.max(1, view.end - view.start);
  const msPerPx = span / Math.max(1, width);

  const clampView = useCallback(
    (start: number, end: number) => {
      const minSpan = 2000;
      let s = Math.max(0, start);
      let e = Math.min(durationMs, end);
      if (e - s < minSpan) {
        const mid = (s + e) / 2;
        s = Math.max(0, mid - minSpan / 2);
        e = Math.min(durationMs, s + minSpan);
        s = e - minSpan < 0 ? 0 : e - minSpan;
      }
      setView({ start: s, end: e });
    },
    [durationMs],
  );

  const zoomAt = useCallback(
    (anchorMs: number, factor: number) => {
      const newSpan = Math.min(durationMs, Math.max(2000, span * factor));
      const ratio = (anchorMs - view.start) / span;
      clampView(anchorMs - newSpan * ratio, anchorMs - newSpan * ratio + newSpan);
    },
    [clampView, span, view.start, durationMs],
  );

  // wheel zoom (non-passive so preventDefault works)
  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      const x = Math.max(0, Math.min(rect.width, e.clientX - rect.left));
      const anchor = view.start + x * msPerPx;
      zoomAt(anchor, e.deltaY > 0 ? 1.3 : 1 / 1.3);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [view.start, msPerPx, zoomAt]);

  // drag-to-pan + click-to-seek
  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    let downX = 0;
    let downStart = 0;
    let moved = false;
    let active = false;
    const onDown = (e: PointerEvent) => {
      if (e.button !== 0) return;
      active = true;
      moved = false;
      downX = e.clientX;
      downStart = view.start;
      el.setPointerCapture(e.pointerId);
    };
    const onMove = (e: PointerEvent) => {
      if (!active) return;
      const dx = e.clientX - downX;
      if (Math.abs(dx) > 3) moved = true;
      if (moved) {
        const shift = -dx * msPerPx;
        const s = Math.max(0, Math.min(durationMs - span, downStart + shift));
        setView({ start: s, end: s + span });
      }
    };
    const onUp = (e: PointerEvent) => {
      if (!active) return;
      active = false;
      if (!moved) {
        const rect = el.getBoundingClientRect();
        const x = Math.max(0, Math.min(rect.width, e.clientX - rect.left));
        onLocate(Math.round(view.start + x * msPerPx));
      }
    };
    el.addEventListener("pointerdown", onDown);
    el.addEventListener("pointermove", onMove);
    el.addEventListener("pointerup", onUp);
    return () => {
      el.removeEventListener("pointerdown", onDown);
      el.removeEventListener("pointermove", onMove);
      el.removeEventListener("pointerup", onUp);
    };
  }, [view.start, view.end, msPerPx, span, durationMs, onLocate]);

  // smooth playhead + follow-pan, DOM-only
  useEffect(() => {
    let raf = 0;
    const tick = () => {
      const v = videoRef.current;
      const ph = playheadRef.current;
      if (v && ph && Number.isFinite(v.duration)) {
        const ms = v.currentTime * 1000;
        const x = ((ms - view.start) / span) * width;
        ph.style.opacity = ms >= view.start && ms <= view.end ? "1" : "0";
        ph.style.left = `${x}px`;
        if (follow && !v.paused) {
          const frac = x / Math.max(1, width);
          if (frac < 0.08 || frac > 0.92) {
            const s = Math.max(0, Math.min(durationMs - span, ms - span / 2));
            setView({ start: s, end: s + span });
          }
        }
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [view.start, view.end, span, width, durationMs, follow, videoRef]);

  // ruler labels: ~5 nice ticks
  const ruler = useMemo(() => {
    const step = span / 5;
    return Array.from({ length: 6 }, (_, i) => view.start + step * i);
  }, [span, view.start]);

  // lane aggregation
  const lanes = useMemo(() => {
    return MODS.map((mod) => {
      const evs = events.filter((e) => e.mod === mod && e.ms >= view.start && e.ms <= view.end);
      const groups: { x: number; w: number; first: TimelineEvent; count: number; msA: number; msB: number }[] = [];
      let g: (typeof groups)[number] | null = null;
      for (const e of evs) {
        const x = ((e.ms - view.start) / span) * width;
        if (!g || x - (g.x + g.w) > TICK_PX) {
          g = { x, w: Math.max(3, TICK_PX - 1), first: e, count: 1, msA: e.ms, msB: e.ms };
          groups.push(g);
        } else {
          g.count += 1;
          g.msB = e.ms;
          g.w = Math.max(g.w, x - g.x + 3);
        }
      }
      return { mod, groups };
    });
  }, [events, view.start, view.end, span, width]);

  const onKeyDown = (e: React.KeyboardEvent) => {
    const v = videoRef.current;
    const cur = v ? v.currentTime * 1000 : view.start;
    const step = e.shiftKey ? 1000 : 5000;
    let target: number | null = null;
    if (e.key === "ArrowLeft") target = cur - step;
    else if (e.key === "ArrowRight") target = cur + step;
    else if (e.key === "Home") target = 0;
    else if (e.key === "End") target = durationMs - 1;
    if (target != null) {
      e.preventDefault();
      onLocate(Math.max(0, Math.min(durationMs - 1, Math.round(target))));
    }
  };

  const fitAll = () => setView({ start: 0, end: durationMs });
  const zoomWindow = (sec: number) => {
    const v = videoRef.current;
    const center = v && Number.isFinite(v.duration) ? v.currentTime * 1000 : (view.start + view.end) / 2;
    clampView(center - sec * 500, center + sec * 500);
  };

  const nowAria = (() => {
    const v = videoRef.current;
    return v ? Math.round(v.currentTime * 1000) : 0;
  })();

  return (
    <div className="select-none">
      <div className="mb-1 flex items-center justify-between gap-2">
        <div className="flex items-center gap-1">
          <span className="mr-1 text-[11.5px] font-medium text-muted">时间轴</span>
          <button onClick={fitAll} className="cursor-pointer rounded border border-line bg-surface px-1.5 py-0.5 text-[10.5px] text-ink-2 hover:border-acc">
            全程
          </button>
          <button onClick={() => zoomWindow(60)} className="cursor-pointer rounded border border-line bg-surface px-1.5 py-0.5 text-[10.5px] text-ink-2 hover:border-acc">
            60s
          </button>
          <button onClick={() => zoomWindow(10)} className="cursor-pointer rounded border border-line bg-surface px-1.5 py-0.5 text-[10.5px] text-ink-2 hover:border-acc">
            10s
          </button>
        </div>
        <span className="text-[10.5px] text-muted">滚轮缩放 · 拖动平移 · 点击定位</span>
      </div>
      <div
        ref={boxRef}
        tabIndex={0}
        role="slider"
        aria-label="视频时间轴,左右方向键定位"
        aria-valuemin={0}
        aria-valuemax={Math.round(durationMs)}
        aria-valuenow={nowAria}
        aria-valuetext={fmtMs(nowAria)}
        onKeyDown={onKeyDown}
        className="relative cursor-crosshair rounded-ctl border border-line bg-surface-2/60 px-1"
        style={{ height: RULER_H + 3 * (LANE_H + LANE_GAP) + 6, paddingTop: RULER_H }}
      >
        {/* ruler */}
        <div className="absolute inset-x-1 top-0" style={{ height: RULER_H }}>
          {ruler.map((ms, i) => {
            const last = i === ruler.length - 1;
            return (
              <span
                key={i}
                className="absolute top-0 font-mono text-[9.5px] tabular-nums text-muted"
                style={last ? { right: 0 } : { left: ((ms - view.start) / span) * width }}
              >
                {fmtMs(ms).slice(0, 5)}
              </span>
            );
          })}
        </div>
        {/* lanes */}
        {lanes.map(({ mod, groups }, li) => (
          <div key={mod} className="absolute inset-x-1 flex items-center" style={{ top: RULER_H + li * (LANE_H + LANE_GAP) + 2, height: LANE_H }}>
            <span className="absolute -left-0.5 top-1/2 -translate-y-1/2 font-mono text-[8.5px] font-semibold opacity-70" aria-hidden>
              {mod}
            </span>
            {groups.map((gr, gi) => {
              const single = gr.count === 1;
              return (
                <button
                  key={gi}
                  title={
                    single
                      ? `${fmtMs(gr.first.ms)} ${gr.first.text.slice(0, 60)}`
                      : `${gr.count} 条 ${mod} 事件 ${fmtMs(gr.msA)}–${fmtMs(gr.msB)},点击放大`
                  }
                  aria-label={single ? `定位到 ${fmtMs(gr.first.ms)}` : `放大 ${gr.count} 条${mod}事件`}
                  onClick={(e) => {
                    e.stopPropagation();
                    if (single) onLocate(gr.first.ms);
                    else clampView(gr.msA - span * 0.05, gr.msB + span * 0.05);
                  }}
                  className={cn(
                    "absolute top-1/2 -translate-y-1/2 cursor-pointer",
                    mod === "ASR" && "bg-asr/80 hover:bg-asr",
                    mod === "OCR" && "bg-ocr/80 hover:bg-ocr",
                    mod === "VIS" && "bg-vis/80 hover:bg-vis",
                  )}
                  style={{
                    left: gr.x,
                    width: gr.w,
                    height: single ? LANE_H - 3 : LANE_H,
                    borderRadius: 2,
                    opacity: single ? 0.85 : 1,
                  }}
                />
              );
            })}
          </div>
        ))}
        {/* playhead */}
        <div
          ref={playheadRef}
          aria-hidden
          className="pointer-events-none absolute top-1 bottom-0 z-10 w-0.5 bg-acc opacity-0 transition-opacity duration-150"
        >
          <div className="absolute -top-0.5 left-1/2 h-2 w-2 -translate-x-1/2 rounded-full bg-acc" />
        </div>
      </div>
    </div>
  );
}
