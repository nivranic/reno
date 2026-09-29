/** Knowledge-model v2 tags: 知识维度 (dimension) + 证据性质 (evidence_nature).
 * Dimension chips use neutral outline (they are an axis, not a verdict);
 * evidence nature maps to tones because it tells the reader how much weight
 * a claim may carry (standard > test > opinion > marketing). */
import { Badge } from "@/components/ui/badge";
import type { Tone } from "@/components/ui/badge";

const DIMENSION_LABEL: Record<string, string> = {
  安全合规: "安全合规",
  材料产品: "材料",
  性能可靠: "性能",
  场景适配: "场景",
  施工工艺: "工艺",
  使用注意: "使用注意",
  维护维修: "维护",
  验收质检: "验收",
  问题排查: "排查",
  报价采购: "报价",
  成本行情: "成本行情",
  方案比较: "比较",
  空间体验: "体验",
  工期协同: "工期",
  其他: "其他",
};

/** null/undefined renders nothing (待归类) — callers may show their own hint. */
export function DimensionBadge({
  dimension,
  className,
}: {
  dimension?: string | null;
  className?: string;
}) {
  if (!dimension) return null;
  return (
    <Badge tone="neutral" className={className}>
      {DIMENSION_LABEL[dimension] ?? dimension}
    </Badge>
  );
}

const NATURE: Record<string, { tone: Tone; label: string }> = {
  cited_standard: { tone: "run", label: "转述标准" },
  author_test: { tone: "ok", label: "实测" },
  third_party: { tone: "ok", label: "第三方" },
  author_opinion: { tone: "neutral", label: "经验观点" },
  user_feedback: { tone: "neutral", label: "他人经验" },
  inference: { tone: "wait", label: "推断" },
  product_claim: { tone: "review", label: "商家宣传" },
};

export function NatureBadge({
  nature,
  className,
  size = "md",
}: {
  nature?: string | null;
  className?: string;
  size?: "sm" | "md";
}) {
  if (!nature) return null;
  const m = NATURE[nature];
  if (!m) return <Badge tone="review" size={size} className={className}>{nature}</Badge>;
  return (
    <Badge tone={m.tone} size={size} className={className}>
      {m.label}
    </Badge>
  );
}
