import { KnowledgeDetails, type KnowledgeDetailFields } from "@/components/shared/knowledge-details";
import { conditionsText } from "@/features/project/project-context-editor";
import type { Conditions } from "@/lib/personal-api";

/** Render source material in the reader's vocabulary. Revision metadata and
 * internal identifiers are not a substitute for the saved words and scope. */
export function EvidenceSnapshot({ snapshot }: { snapshot: Record<string, unknown> }) {
  if (typeof snapshot.claim === "string") return <div className="space-y-1"><p className="whitespace-pre-wrap text-[13px] text-ink">{snapshot.claim}</p><KnowledgeDetails knowledge={snapshot as KnowledgeDetailFields} /></div>;
  const scope = snapshot.scope && typeof snapshot.scope === "object" ? snapshot.scope as Conditions : {};
  const sourceTypes: Record<string, string> = { manual_note: "人工备注", standard_record: "规范条文记录", product_document: "产品说明", quote: "报价资料", site_photo: "现场照片" };
  return <div className="space-y-1 text-[12.5px] leading-relaxed text-ink-2">
    <p className="font-medium">{String(snapshot.title ?? snapshot.original_name ?? "保存的依据")}</p>
    {snapshot.excerpt ? <p className="whitespace-pre-wrap">{String(snapshot.excerpt)}</p> : null}
    {snapshot.source_type ? <><p className="text-muted">{sourceTypes[String(snapshot.source_type)] ?? "来源记录"} · {snapshot.verification_state === "user_checked" ? "用户已核对原文" : "尚未核对原文"}</p><p className="text-muted">版本与定位：{[snapshot.document_no, snapshot.edition, snapshot.locator].filter(Boolean).join(" · ") || "待补充"}</p><p className="text-muted">适用范围：{conditionsText(scope)}</p><p className="text-muted">发布日期：{String(snapshot.published_on || "未知")} · 核对日期：{String(snapshot.checked_at || "尚未核对")}</p><p className="text-muted">有效性：{({ unknown: "未知", current: "用户记录为现行", superseded: "已被替代", withdrawn: "已撤销" } as Record<string, string>)[String(snapshot.validity)] ?? "未知"}</p></> : null}
    {snapshot.side_a && typeof snapshot.side_a === "object" ? <p>观点甲：{String((snapshot.side_a as { claim?: string }).claim || "见当时保存内容")}</p> : null}
    {snapshot.side_b && typeof snapshot.side_b === "object" ? <p>观点乙：{String((snapshot.side_b as { claim?: string }).claim || "见当时保存内容")}</p> : null}
  </div>;
}
