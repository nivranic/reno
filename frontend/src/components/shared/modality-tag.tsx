/** Modality tag: color AND text label (never color alone).
 * The color axis is reserved for evidence modality; it deliberately does not
 * overlap the ok/bad/run status axis. Unknown labels render honestly as "?"
 * instead of being silently grouped into ASR. */
import { toModality } from "@/lib/api";
import type { Modality } from "@/lib/api-types";
import { cn } from "@/lib/cn";

const MOD_STYLE: Record<Modality, string> = {
  ASR: "bg-asr-bg text-asr border-asr-line",
  OCR: "bg-ocr-bg text-ocr border-ocr-line",
  VIS: "bg-vis-bg text-vis border-vis-line",
};

const MOD_TITLE: Record<Modality, string> = {
  ASR: "语音转写",
  OCR: "画面文字",
  VIS: "视觉理解",
};

export function ModalityTag({
  mod,
  size = "md",
  title: titleOverride,
}: {
  mod: string;
  size?: "sm" | "md";
  title?: string;
}) {
  const m = toModality(mod);
  if (!m) {
    return (
      <span
        title={`未知模态:${mod}`}
        className="inline-flex shrink-0 items-center rounded border border-line bg-surface-2 px-1 py-0 font-mono text-[11px] text-muted"
      >
        ?
      </span>
    );
  }
  return (
    <span
      title={titleOverride ?? MOD_TITLE[m]}
      className={cn(
        "inline-flex shrink-0 items-center rounded border font-mono font-semibold tracking-wide",
        MOD_STYLE[m],
        size === "sm" ? "px-1 py-0 text-[10px]" : "px-1.5 py-0.5 text-[11px]",
      )}
    >
      {m}
    </span>
  );
}

export function ModalityLegend() {
  return (
    <span className="inline-flex items-center gap-1.5 text-[11.5px] text-muted">
      <ModalityTag mod="ASR" size="sm" /> 语音
      <ModalityTag mod="OCR" size="sm" /> 画面文字
      <ModalityTag mod="VIS" size="sm" /> 视觉
    </span>
  );
}
