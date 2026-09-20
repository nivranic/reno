/** Semantic status badges — one map per domain enum, unknown values stay
 * visible & diagnosable instead of being silently grouped into "ok". */
import { Badge } from "@/components/ui/badge";
import type { Tone } from "@/components/ui/badge";
import { cn } from "@/lib/cn";

const VIDEO_STATUS: Record<string, { tone: Tone; label: string }> = {
  imported: { tone: "wait", label: "待处理" },
  error: { tone: "bad", label: "失败" },
  processed: { tone: "ok", label: "已完成" },
  done_media: { tone: "run", label: "媒体就绪" },
  done_asr: { tone: "run", label: "语音识别完成" },
  done_ocr: { tone: "run", label: "OCR 完成" },
  done_vlm: { tone: "run", label: "视觉理解完成" },
  done_atomize: { tone: "run", label: "原子化完成" },
};

const ATOM_STATUS: Record<string, { tone: Tone; label: string }> = {
  candidate: { tone: "neutral", label: "候选" },
  disputed: { tone: "review", label: "存疑" },
  verified: { tone: "ok", label: "已核实" },
  rejected: { tone: "bad", label: "已否决" },
};

const POLARITY: Record<string, { tone: Tone; label: string }> = {
  recommend: { tone: "ok", label: "推荐" },
  avoid: { tone: "bad", label: "避坑" },
  require: { tone: "run", label: "规范要求" },
  neutral: { tone: "neutral", label: "中性" },
  optional: { tone: "wait", label: "可选" },
};

export function VideoStatusBadge({ status }: { status: string }) {
  const m = VIDEO_STATUS[status];
  if (!m) return <Badge tone="review">未知状态:{status}</Badge>;
  return <Badge tone={m.tone}>{m.label}</Badge>;
}

export function AtomStatusBadge({ status }: { status: string }) {
  const m = ATOM_STATUS[status];
  if (!m) return <Badge tone="review">未知:{status}</Badge>;
  return <Badge tone={m.tone}>{m.label}</Badge>;
}

export function PolarityBadge({
  polarity,
  className,
}: {
  polarity: string;
  className?: string;
}) {
  const m = POLARITY[polarity];
  if (!m) return <Badge className={className}>未知:{polarity}</Badge>;
  return (
    <Badge tone={m.tone} className={className}>
      {m.label}
    </Badge>
  );
}

export function ConflictStatusBadge({ status }: { status: string }) {
  if (status.startsWith("decided:")) {
    const action = status.slice(8);
    const label =
      action === "accept_a"
        ? "已采纳 A"
        : action === "accept_b"
          ? "已采纳 B"
          : action === "both"
            ? "两者各适用"
            : action === "reject"
              ? "都不采"
              : action;
    return <Badge tone="ok">{label}</Badge>;
  }
  if (status === "needs_user_decision") return <Badge tone="wait">待你裁决</Badge>;
  if (status === "needs_review") return <Badge tone="review">需复核</Badge>;
  return <Badge tone="review">未知:{status}</Badge>;
}

/** Small dot for compact contexts (table rows, timeline legend). */
export function StatusDot({ tone }: { tone: Tone }) {
  return (
    <span
      className={cn("inline-block h-1.5 w-1.5 rounded-full", {
        "bg-st-ok": tone === "ok",
        "bg-st-run": tone === "run",
        "bg-st-wait": tone === "wait",
        "bg-st-bad": tone === "bad",
        "bg-st-review": tone === "review",
        "bg-muted": tone === "neutral",
        "bg-acc": tone === "acc",
      })}
    />
  );
}
