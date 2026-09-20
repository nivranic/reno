/** 证据工作台 (video detail): player + frame + timeline + evidence stream +
 * atom panel, linked through ONE locate protocol. Layout:
 *   xl+: [ player | frame ] over [ timeline / stream ] | draggable [ atoms ]
 *   lg : single media column | atoms
 *   <lg: media column + tabs(证据流 | 原子知识), frame appears on selection
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ChevronsLeftRight } from "lucide-react";
import { eventsQuery, videoMetaQuery } from "@/lib/queries";
import { videoFileUrl } from "@/lib/api";
import { activeEvents, fmtDur, parseDeepLinkMs } from "@/lib/time";
import { cn } from "@/lib/cn";
import { ErrorState, ListSkeleton } from "@/components/shared/states";
import { VideoStatusBadge } from "@/components/shared/status-badge";
import { Tabs } from "@/components/ui/tabs";
import { useToast } from "@/components/shared/toaster";
import {
  WorkbenchStoreProvider,
  createWorkbenchStore,
  useWorkbench,
} from "./workbench-store";
import { Timeline } from "./timeline";
import { EvidenceStream } from "./evidence-stream";
import { AtomPanel } from "./atom-panel";
import { FramePane } from "./frame-pane";

export default function WorkbenchPage() {
  const { videoId } = useParams<{ videoId: string }>();
  if (!videoId) return null;
  // keyed remount isolates ALL per-video state on video switch
  return <WorkbenchInner key={videoId} videoId={videoId} />;
}

const ATOMS_W_KEY = "reno-wb-atoms-w";

function WorkbenchInner({ videoId }: { videoId: string }) {
  const [store] = useState(() => createWorkbenchStore(videoId));
  return (
    <WorkbenchStoreProvider store={store}>
      <WorkbenchBody videoId={videoId} />
    </WorkbenchStoreProvider>
  );
}

function WorkbenchBody({ videoId }: { videoId: string }) {
  const store = useWorkbench();
  const toast = useToast();
  const meta = useQuery(videoMetaQuery(videoId));
  const events = useQuery(eventsQuery(videoId));

  const videoRef = useRef<HTMLVideoElement>(null);
  const [nowMs, setNowMs] = useState(0);
  const [durationMs, setDurationMs] = useState(0);
  const requestedMs = store((s) => s.requestedMs);
  const follow = store((s) => s.follow);
  const modFilter = store((s) => s.modFilter);
  const selectedEvId = store((s) => s.selectedEvId);
  const selectedAtomId = store((s) => s.selectedAtomId);

  // ---- player: explicit locate commands seek; playback only observes ----
  const lastApplied = useRef<number | null>(null);
  useEffect(() => {
    if (requestedMs == null) return;
    const v = videoRef.current;
    if (!v) return; // frame-only mode: locate still updates frame + url below
    if (lastApplied.current === requestedMs) return;
    const apply = () => {
      let t = requestedMs / 1000;
      if (Number.isFinite(v.duration) && v.duration > 0) {
        t = Math.min(t, Math.max(0, v.duration - 0.05));
      }
      v.currentTime = t;
      lastApplied.current = requestedMs;
      void v.play().catch(() => {
        /* autoplay policy (deep-link cold load): position is still set */
      });
    };
    if (v.readyState >= 1) apply();
    else {
      const onMeta = () => apply();
      v.addEventListener("loadedmetadata", onMeta, { once: true });
      return () => v.removeEventListener("loadedmetadata", onMeta);
    }
  }, [requestedMs]);

  // ---- URL: explicit locate replaces ?t=ms (no history spam) ----
  useEffect(() => {
    if (requestedMs == null) return;
    window.history.replaceState(null, "", `/videos/${encodeURIComponent(videoId)}?t=${requestedMs}`);
  }, [requestedMs, videoId]);

  // ---- deep link ?t=ms: apply once, after data+metadata, never over a user action ----
  const [searchParams] = useSearchParams();
  const rawT = searchParams.get("t");
  const deepApplied = useRef(false);
  const badDeepLinkWarned = useRef(false);
  useEffect(() => {
    if (rawT != null && parseDeepLinkMs(rawT) == null && !badDeepLinkWarned.current) {
      badDeepLinkWarned.current = true;
      toast.error(`深链时间参数无效:"${rawT}" 已忽略`, { key: "deeplink" });
    }
  }, [rawT, toast]);
  const metaLoaded = meta.isSuccess;
  const eventsLoaded = events.isSuccess;
  useEffect(() => {
    if (deepApplied.current || !metaLoaded || !eventsLoaded) return;
    deepApplied.current = true;
    const t = parseDeepLinkMs(rawT);
    if (t != null && !store.getState().userActed) {
      store.getState().locate(t, { updateUrl: false });
    }
  }, [metaLoaded, eventsLoaded, rawT, store]);

  // ---- derived: filtered events + active set ----
  const filtered = useMemo(
    () =>
      (events.data?.events ?? []).filter(
        (e) => modFilter[e.mod] !== false,
      ),
    [events.data, modFilter],
  );
  const activeIds = useMemo(() => {
    const s = new Set<string>();
    for (const e of activeEvents(filtered, nowMs)) s.add(e.id);
    return s;
  }, [filtered, nowMs]);

  const locate = (ms: number) => store.getState().locate(ms);
  const backToLive = () => {
    store.getState().setFollow(true);
    store.getState().locate(Math.round(nowMs));
  };

  // ---- resizable atoms panel (lg+) ----
  const [atomsW, setAtomsW] = useState(() => {
    const saved = Number(localStorage.getItem(ATOMS_W_KEY));
    return Number.isFinite(saved) && saved >= 300 && saved <= 620 ? saved : 380;
  });
  useEffect(() => {
    const onResize = () => {
      // window shrank below the saved layout -> release to a safe width
      setAtomsW((w) => Math.min(w, Math.max(300, window.innerWidth / 2 - 80)));
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  const dragRef = useRef(false);
  useEffect(() => {
    const onMove = (e: PointerEvent) => {
      if (!dragRef.current) return;
      const w = Math.min(Math.max(300, window.innerWidth - e.clientX), 620);
      setAtomsW(w);
    };
    const onUp = () => {
      if (dragRef.current) {
        dragRef.current = false;
        setAtomsW((w) => {
          try {
            localStorage.setItem(ATOMS_W_KEY, String(w));
          } catch {
            /* ignore */
          }
          return w;
        });
      }
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
  }, []);

  const [mobileTab, setMobileTab] = useState<"stream" | "atoms">("stream");

  // metadata header
  const title = meta.data?.title ?? videoId;
  const m = meta.data;

  if (meta.isError) {
    return (
      <div className="mx-auto max-w-2xl p-6">
        {meta.error instanceof Error && "status" in meta.error && (meta.error as { status: number }).status === 404 ? (
          <ErrorState error={{ status: 404, message: `视频 ${videoId} 不存在`, name: "NotFound" }} context="视频" />
        ) : (
          <ErrorState error={meta.error} onRetry={() => void meta.refetch()} context="视频信息" />
        )}
        <Link to="/" className="mt-4 inline-block text-sm text-acc hover:underline">
          ← 回到收件箱
        </Link>
      </div>
    );
  }

  return (
    <div className="flex min-h-full flex-col">
      {/* header */}
      <header className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line bg-bg px-4 py-2.5">
        <Link
          to="/"
          className="inline-flex items-center gap-1 rounded-ctl px-1.5 py-1 text-[13px] text-muted hover:bg-surface-2 hover:text-ink"
        >
          <ArrowLeft size={15} /> 收件箱
        </Link>
        <h1 className="min-w-0 flex-1 truncate text-[15px] font-semibold text-ink" title={title}>
          {title}
        </h1>
        {m ? (
          <div className="flex flex-wrap items-center gap-2 text-[12px] text-muted">
            <VideoStatusBadge status={m.status} />
            <span>{m.author ?? "—"}</span>
            <span aria-hidden>·</span>
            <span className="font-mono tabular-nums">{fmtDur(m.duration_ms)}</span>
            <span aria-hidden>·</span>
            <span>{m.atoms} 原子</span>
          </div>
        ) : null}
      </header>

      {/* content-driven height: short viewports scroll the main area instead
          of squeezing panels into each other (e2e-caught overlap bug) */}
      <div className="flex flex-1 flex-col lg:flex-row">
        {/* ---- left: media + timeline + stream ---- */}
        <div className="flex min-w-0 flex-1 flex-col gap-2.5 p-3 lg:p-4">
          <div className="flex flex-col gap-3 xl:flex-row">
            {m?.has_media === false ? (
              <div className="flex aspect-video w-full flex-1 items-center justify-center rounded-panel border border-line bg-surface-2 text-center text-[13px] text-muted">
                视频文件缺失(可能已被清理),证据流与抽帧仍可浏览
              </div>
            ) : (
              /* wrapper owns layout in both contexts; the video itself is
                 always object-contain + height-capped so portrait videos
                 never overflow into the toolbar below (e2e-caught bug) */
              <div className="w-full xl:min-w-0 xl:flex-1">
                <video
                  ref={videoRef}
                  controls
                  preload="metadata"
                  playsInline
                  src={videoFileUrl(videoId)}
                  aria-label="视频播放器"
                  className="mx-auto block max-h-[min(48vh,420px)] w-full rounded-panel border border-line bg-black object-contain"
                  onTimeUpdate={(e) => setNowMs(e.currentTarget.currentTime * 1000)}
                  onPlay={() => setNowMs((videoRef.current?.currentTime ?? 0) * 1000)}
                  onLoadedMetadata={(e) => {
                    const d = e.currentTarget.duration;
                    if (Number.isFinite(d)) setDurationMs(d * 1000);
                  }}
                />
              </div>
            )}
            {/* frame pane: side column on xl, collapsible strip on smaller */}
            <div className="hidden xl:block xl:w-[330px] xl:shrink-0 xl:self-stretch">
              <FramePane videoId={videoId} requestedMs={requestedMs} />
            </div>
          </div>

          {requestedMs != null ? (
            <div className="max-h-64 xl:hidden">
              <FramePane videoId={videoId} requestedMs={requestedMs} />
            </div>
          ) : null}

          <Timeline
            events={filtered}
            durationMs={Math.max(durationMs, m?.duration_ms ?? 0, 1000)}
            videoRef={videoRef}
            onLocate={locate}
            follow={follow}
          />

          {/* modality filter + follow toolbar */}
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[11.5px] text-muted">证据模态:</span>
            {(["ASR", "OCR", "VIS"] as const).map((mod) => {
              const on = modFilter[mod];
              return (
                <button
                  key={mod}
                  aria-pressed={on}
                  onClick={() => store.getState().toggleMod(mod)}
                  className={cn(
                    "cursor-pointer rounded-ctl border px-2 py-0.5 font-mono text-[11px] font-semibold transition-all duration-150",
                    on
                      ? mod === "ASR"
                        ? "border-asr-line bg-asr-bg text-asr"
                        : mod === "OCR"
                          ? "border-ocr-line bg-ocr-bg text-ocr"
                          : "border-vis-line bg-vis-bg text-vis"
                      : "border-line bg-surface text-muted line-through opacity-60",
                  )}
                >
                  {mod}
                </button>
              );
            })}
            <div className="ml-auto flex items-center gap-2">
              {!follow ? (
                <button
                  onClick={backToLive}
                  className="inline-flex cursor-pointer items-center gap-1 rounded-ctl border border-acc-line bg-acc-soft px-2.5 py-1 text-[12px] font-medium text-acc"
                >
                  回到当前播放位置
                </button>
              ) : (
                <span className="text-[11.5px] text-muted">跟随播放中(滚动即暂停跟随)</span>
              )}
            </div>
          </div>

          {/* mobile tabs switch stream/atoms below lg */}
          <div className="lg:hidden">
            <Tabs
              ariaLabel="工作台面板"
              value={mobileTab}
              onChange={setMobileTab}
              items={[
                { value: "stream", label: `证据流 (${filtered.length})` },
                { value: "atoms", label: `原子 (${events.data?.atoms.length ?? 0})` },
              ]}
            />
          </div>

          <div
            className={cn(
              "flex flex-col",
              mobileTab === "atoms" && "hidden lg:flex",
            )}
            style={{ height: "clamp(240px, 34vh, 520px)" }}
          >
            {events.isError ? (
              <ErrorState error={events.error} onRetry={() => void events.refetch()} context="证据数据" />
            ) : !events.isSuccess ? (
              <ListSkeleton rows={7} />
            ) : (
              <EvidenceStream
                events={filtered}
                activeIds={activeIds}
                selectedEvId={selectedEvId}
                follow={follow}
                onUserScroll={() => store.getState().setFollow(false)}
                onLocate={locate}
                height="100%"
              />
            )}
          </div>

          {/* mobile atoms pane */}
          {mobileTab === "atoms" ? (
            <div
              className="flex flex-col lg:hidden"
              style={{ height: "clamp(300px, 40vh, 560px)" }}
            >
              {events.data ? (
                <AtomPanel atoms={events.data.atoms} className="min-h-0 flex-1" />
              ) : (
                <ListSkeleton rows={5} />
              )}
            </div>
          ) : null}
        </div>

        {/* ---- divider (lg+) ---- */}
        <div
          role="separator"
          aria-label="拖动调整原子面板宽度"
          aria-orientation="vertical"
          tabIndex={0}
          onPointerDown={() => {
            dragRef.current = true;
          }}
          onKeyDown={(e) => {
            if (e.key === "ArrowLeft") setAtomsW((w) => Math.min(620, w + 24));
            else if (e.key === "ArrowRight") setAtomsW((w) => Math.max(300, w - 24));
            else return;
            e.preventDefault();
          }}
          className="hidden w-1.5 shrink-0 cursor-col-resize bg-transparent transition-colors hover:bg-acc/30 lg:block"
        >
          <ChevronsLeftRight size={12} className="mx-auto mt-1/2 text-muted/50" aria-hidden />
        </div>

        {/* ---- right: atoms panel (lg+) ---- */}
        <aside
          className="hidden shrink-0 flex-col border-l border-line py-3 pl-1 pr-3 lg:flex"
          style={{ width: atomsW }}
        >
          {events.data ? (
            <AtomPanel atoms={events.data.atoms} className="min-h-0 flex-1" />
          ) : (
            <ListSkeleton rows={5} />
          )}
        </aside>
      </div>

      {/* selected-atom hint for screen readers (visual state is in the panel) */}
      <span className="sr-only" aria-live="polite">
        {selectedAtomId ? `已选中原子 ${selectedAtomId}` : ""}
      </span>
    </div>
  );
}
