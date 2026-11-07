import type { AtomParameter, AtomPrice, ConditionMatch } from "@/lib/api-types";
import { NatureBadge } from "./knowledge-tags";

export interface KnowledgeDetailFields {
  has_outdated_conflict?: boolean;
  condition_match?: ConditionMatch;
  evidence_nature?: string | null;
  conditions?: Record<string, unknown> | null;
  exceptions?: string[] | null;
  parameters?: AtomParameter[] | null;
  prices?: AtomPrice[] | null;
  review_status?: string | null;
  source_published_at?: string | null;
}
const REVIEW: Record<string, string> = { unreviewed: "尚未人工复核", user_confirmed: "已人工复核", user_rejected: "已人工驳回", needs_recheck: "内容变化，待重新复核" };
const LABELS: Record<string, string> = { space: "空间", region: "地区", climate: "气候", substrate: "基层", heating: "采暖", humidity: "湿度", use_case: "使用场景", stage: "施工阶段" };
const valueText = (value: unknown): string => typeof value === "object" ? JSON.stringify(value) : String(value);

/** Qualification text remains visible, including on touch screens. */
export function KnowledgeDetails({ knowledge, compact = false }: { knowledge: KnowledgeDetailFields; compact?: boolean }) {
  const conditions = Object.entries(knowledge.conditions ?? {}).filter(([key, value]) => key !== "authority_level" && value != null && value !== "");
  return <div className={`mt-2 space-y-1 break-words border-t border-line/70 pt-2 leading-relaxed text-ink-2 ${compact ? "text-[11.5px]" : "text-[12.5px]"}`}>
    {knowledge.has_outdated_conflict ? <p className="text-st-wait">旧关联待更新：历史争议的判定依据尚未匹配，不能据此认定当前仍有分歧。</p> : null}
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
      <span>来源性质：</span>
      {knowledge.evidence_nature === "cited_standard" ? <span>视频转述标准</span> : knowledge.evidence_nature ? <NatureBadge nature={knowledge.evidence_nature} size="sm" /> : <span className="text-muted">未注明</span>}
      <span className="text-muted">{REVIEW[knowledge.review_status ?? "unreviewed"] ?? "尚未人工复核"}</span>
    </div>
    <p><span className="text-muted">适用条件：</span>{conditions.length ? conditions.map(([key, value]) => `${LABELS[key] ?? key}：${valueText(value)}`).join("；") : "未注明，使用前需核实"}</p>
    <p><span className="text-muted">不适用例外：</span>{knowledge.exceptions?.length ? knowledge.exceptions.join("；") : "未注明，不代表普遍适用"}</p>
    {knowledge.condition_match && <div className="space-y-0.5">
      <p>本次条件比对：{{ match: "已知条件一致", mismatch: "部分条件不一致", unknown: "尚不能判断" }[knowledge.condition_match.status]}。{knowledge.condition_match.reason}</p>
      {knowledge.condition_match.details.map((detail) => <p key={detail.key} className="text-muted">{detail.key}：本次 {valueText(detail.requested)}；来源 {detail.source == null || detail.source === "" ? "未注明" : valueText(detail.source)}。{detail.reason}</p>)}
    </div>}
    {knowledge.parameters?.length ? <p><span className="text-muted">参数：</span>{knowledge.parameters.map((p) => `${p.name}=${p.value}${p.unit ?? "（单位未注明）"}${p.param_status === "unnormalized" ? "（待核实）" : ""}`).join("；")}</p> : null}
    {knowledge.prices?.map((price, index) => <div key={index} className="flex flex-wrap gap-x-2 gap-y-0.5">
      <span>价格：{price.object} {price.amount}{price.unit ?? "（单位未注明）"}</span>
      <span>口径：{price.price_kind || "未注明"}{price.basis ? ` / ${price.basis}` : ""}</span>
      <span>规格：{[price.brand, price.model, price.spec].filter(Boolean).join(" / ") || "未知"}</span>
      <span>含项：{price.includes?.join("、") || "未知"}</span>
      <span>地区：{price.region || "未知"}</span><span>价格时点：{price.valid_at || "未知"}</span>
    </div>)}
    <p className="text-muted">来源发布时间：{knowledge.source_published_at || "未知"}</p>
  </div>;
}
