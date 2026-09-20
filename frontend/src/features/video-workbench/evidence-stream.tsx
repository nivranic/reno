/** Virtualized evidence stream with real-range active highlighting, follow
 * mode that yields to user scrolling, and expandable long text. */
import { memo, useEffect, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { ChevronDown, ChevronUp, LocateFixed } from "lucide-react";
import type { TimelineEvent } from "@/lib/api-types";
import { fmtMs } from "@/lib/time";
import { cn } from "@/lib/cn";
import { ModalityTag } from "@/components/shared/modality-tag";

interface RowProps {
  event: TimelineEvent;
  active: boolean;
  selected: boolean;
  onLocate: (ms: number) => void;
}

const CLAMP_CHARS = 90;

const EvidenceRow = memo(function EvidenceRow({ event, active, selected, onLocate }: RowProps) {
  const long = event.text.length > CLAMP_CHARS;
  const [expanded, setExpanded] = useState(false);
  return (
    <div
      id={`ev-${event.id}`}
      className={cn(
        "group border-b border-line/70 px-3 py-2 transition-colors duration-150",
        active ? "bg-acc-soft/50 shadow-[inset_2px_0_0_var(--acc)]" : "bg-surface",
        selected && "ring-1 ring-acc-line",
      )}
    >
      <div className="flex items-baseline gap-2">
        <button
          onClick={() => onLocate(event.ms)}
          title="定位到该证据"
          className="cursor-pointer font-mono text-[11.5px] tabular-nums text-muted underline-offset-2 hover:text-acc hover:underline"
        >
          {fmtMs(event.ms)}
        </button>
        <ModalityTag mod={event.mod} size="sm" />
        {active ? (
          <span className="ml-auto inline-flex items-center gap-1 text-[10.5px] font-medium text-acc">
            <LocateFixed size={11} /> 播放中
          </span>
        ) : null}
      </div>
      <p
        className={cn(
          "mt-1 text-[13.5px] leading-relaxed text-ink-2",
          !expanded && long && "line-clamp-3",
        )}
      >
        {event.text}
      </p>
      {long ? (
        <button
          onClick={() => setExpanded(!expanded)}
          className="mt-0.5 inline-flex cursor-pointer items-center gap-0.5 text-[11.5px] text-muted hover:text-acc"
        >
          {expanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
          {expanded ? "收起" : "展开全文"}
        </button>
      ) : null}
    </div>
  );
});

/** Virtualized evidence stream: see component above. */

export function EvidenceStream({
  events,
  activeIds,
  selectedEvId,
  follow,
  onUserScroll,
  onLocate,
  height,
}: {
  events: TimelineEvent[];
  activeIds: Set<string>;
  selectedEvId: string | null;
  follow: boolean;
  onUserScroll: () => void;
  onLocate: (ms: number) => void;
  height: number | string;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: events.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 58,
    overscan: 10,
    getItemKey: (i) => events[i].id,
  });

  // follow playback: scroll to the first active event
  const lastFollowIdx = useRef(-1);
  useEffect(() => {
    if (!follow || activeIds.size === 0) return;
    const idx = events.findIndex((e) => activeIds.has(e.id));
    if (idx >= 0 && idx !== lastFollowIdx.current) {
      lastFollowIdx.current = idx;
      virtualizer.scrollToIndex(idx, { align: "center", behavior: "smooth" });
    }
    if (activeIds.size === 0) lastFollowIdx.current = -1;
  }, [activeIds, events, follow, virtualizer]);

  // scroll to the selected evidence when an atom evidence chip is clicked
  const lastSelected = useRef<string | null>(null);
  useEffect(() => {
    if (!selectedEvId || selectedEvId === lastSelected.current) return;
    lastSelected.current = selectedEvId;
    const idx = events.findIndex((e) => e.id === selectedEvId);
    if (idx >= 0) virtualizer.scrollToIndex(idx, { align: "center", behavior: "smooth" });
  }, [selectedEvId, events, virtualizer]);

  // expanded long text changes row heights -> remeasure
  useEffect(() => {
    virtualizer.measure();
  }, [events, virtualizer]);

  return (
    <div className="relative min-h-0 flex-1">
      <div
        ref={scrollRef}
        onWheel={onUserScroll}
        onTouchMove={onUserScroll}
        className="h-full overflow-y-auto overscroll-contain rounded-panel border border-line bg-surface"
        style={{ maxHeight: typeof height === "number" ? height : undefined }}
        role="list"
        aria-label={`证据流,共 ${events.length} 条`}
      >
        {events.length === 0 ? (
          <p className="px-4 py-10 text-center text-[13px] text-muted">
            当前筛选条件下没有证据事件
          </p>
        ) : (
          <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
            {virtualizer.getVirtualItems().map((vi) => {
              const e = events[vi.index];
              if (!e) return null;
              return (
                <div
                  key={e.id}
                  data-index={vi.index}
                  ref={virtualizer.measureElement}
                  role="listitem"
                  style={{
                    position: "absolute",
                    top: 0,
                    left: 0,
                    width: "100%",
                    transform: `translateY(${vi.start}px)`,
                  }}
                >
                  <EvidenceRow
                    event={e}
                    active={activeIds.has(e.id)}
                    selected={selectedEvId === e.id}
                    onLocate={onLocate}
                  />
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
