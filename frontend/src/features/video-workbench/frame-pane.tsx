/** Evidence frame pane — the backend-extracted frame for the requested
 * evidence time. Visually and semantically distinct from the live player:
 * labeled, timestamped, and explicit about staleness during loading. */
import { ImageIcon, RotateCcw } from "lucide-react";
import { fmtMs } from "@/lib/time";
import { cn } from "@/lib/cn";
import { Spinner } from "@/components/ui/skeleton";
import { useFrame } from "./use-frame";

export function FramePane({
  videoId,
  requestedMs,
  compact,
}: {
  videoId: string;
  requestedMs: number | null;
  compact?: boolean;
}) {
  const frame = useFrame(videoId, requestedMs);

  return (
    <section
      aria-label="证据帧面板"
      className={cn(
        "flex min-h-0 flex-col overflow-hidden rounded-panel border border-line bg-surface",
        compact ? "h-full" : "h-full",
      )}
    >
      <header className="flex items-center justify-between gap-2 border-b border-line px-3 py-1.5">
        <span className="text-[12px] font-semibold text-ink-2">证据帧 · 后端精确抽取</span>
        {frame.pendingMs != null ? (
          <span className="font-mono text-[11.5px] tabular-nums text-muted">
            {fmtMs(frame.pendingMs)}
            {frame.stale ? ` (显示上一帧 ${fmtMs(frame.shownMs ?? 0)})` : ""}
          </span>
        ) : null}
      </header>

      <div className="relative min-h-0 flex-1 bg-surface-2/40">
        {frame.url ? (
          <img
            src={frame.url}
            alt={`证据帧 ${fmtMs(frame.shownMs ?? 0)}`}
            className="h-full w-full object-contain"
            draggable={false}
          />
        ) : (
          <div className="flex h-full min-h-[120px] flex-col items-center justify-center gap-1.5 px-3 py-6 text-center text-muted">
            <ImageIcon size={22} />
            <p className="text-[12px]">点击原子或证据,查看该时刻的精确证据帧</p>
          </div>
        )}
        {frame.loading ? (
          <div className="absolute inset-0 flex items-center justify-center bg-surface/50 backdrop-blur-[1px]">
            <div className="flex items-center gap-2 rounded-ctl border border-line bg-surface px-3 py-1.5 text-[12px] text-ink-2 shadow">
              <Spinner className="h-4 w-4" />
              抽取 {fmtMs(frame.pendingMs ?? 0)} 帧…
            </div>
          </div>
        ) : null}
        {frame.error ? (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-st-bad-bg/80 px-3 text-center">
            <p className="text-[12.5px] text-st-bad">抽帧失败(视频文件缺失或解码错误)</p>
            <button
              onClick={frame.retry}
              className="inline-flex cursor-pointer items-center gap-1 rounded-ctl border border-st-bad-line bg-surface px-3 py-1.5 text-[12.5px] font-medium text-st-bad"
            >
              <RotateCcw size={13} /> 重试
            </button>
          </div>
        ) : null}
      </div>
      <footer className="border-t border-line px-3 py-1 text-[10.5px] leading-relaxed text-muted">
        与播放器画面相互独立:此帧由 ffmpeg 在证据时间点即时抽取
      </footer>
    </section>
  );
}
