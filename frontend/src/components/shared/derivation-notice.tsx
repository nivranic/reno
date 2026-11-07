import { Link } from "react-router";
import { ShieldAlert } from "lucide-react";
import type { Derivation, DerivationSummary } from "@/lib/api-types";
import { normalizeDerivation, pendingRelations } from "@/lib/derivation";
import { Badge } from "@/components/ui/badge";

const LABELS = { active: "依据匹配", stale: "依据已变化", missing: "依据成员缺失", unknown: "判定依据未知" };
const REASONS: Record<string, string> = {
  inputs_changed: "部分原子内容已变化，原分析保留供核对。",
  members_missing: "部分原子已不可用，原分析不能代表当前完整关系。",
  unbound: "缺少可信的生成依据，无法确认原分析是否仍适用。",
  invalid_binding: "生成依据无法校验，原分析需重新核对。",
  invalid_members: "成员信息无法校验，原分析需重新核对。",
  members_mismatch: "成员与生成时记录不一致，原分析需重新核对。",
};

export function DerivationBadge({ derivation }: { derivation?: Derivation }) {
  const value = normalizeDerivation(derivation);
  return <Badge tone={value.state === "active" ? "ok" : "wait"}>{LABELS[value.state]}</Badge>;
}

export function DerivationNotice({ derivation }: { derivation?: Derivation }) {
  const value = normalizeDerivation(derivation);
  if (value.state === "active") return null;
  return <div className="mt-2 rounded-ctl border border-st-wait-line bg-st-wait-bg px-3 py-2 text-[12px] leading-relaxed text-st-wait" aria-label="关联判定依据">
    <p>{LABELS[value.state]}。{REASONS[value.reason] ?? "无法确认生成依据，原分析需重新核对。"}</p>
    {value.changed_count > 0 || value.missing_count > 0 ? <p>已变化 {value.changed_count} 项，缺失 {value.missing_count} 项。</p> : null}
  </div>;
}

interface Relation { id: string; topic: string | null; derivation?: Derivation }
export function RelationNotices({ relations, snapshotLabel }: { relations: Relation[]; snapshotLabel: string }) {
  const current = relations.filter((row) => normalizeDerivation(row.derivation).state === "active");
  const history = relations.filter((row) => normalizeDerivation(row.derivation).state !== "active");
  if (!relations.length) return null;
  return <div className="mb-3 space-y-2 text-[12px] leading-relaxed">
    {current.length > 0 ? <section className="rounded-ctl border border-st-review-line bg-st-review-bg px-3 py-2 text-st-review" aria-label="依据匹配的分歧">
      <p className="flex items-start gap-1.5"><ShieldAlert size={13} className="mt-0.5 shrink-0" /><span>{snapshotLabel}：{current.length} 个有分歧的主题，判定依据匹配。</span></p>
      <p className="mt-1 break-words">{current.map((row) => row.topic || "未命名主题").join("、")} · <Link to="/conflicts" className="underline underline-offset-2">去复核</Link></p>
    </section> : null}
    {history.length > 0 ? <section className="rounded-ctl border border-st-wait-line bg-st-wait-bg px-3 py-2 text-st-wait" aria-label="历史关联待核对">
      <p className="font-medium">历史关联待核对（{history.length}）</p>
      <p>这些关联不能据此认定为当前有效分歧，请结合保留的原文核对。</p>
      <ul className="mt-1 space-y-2">{history.map((row) => <li key={row.id}>
        <span className="break-words">{row.topic || "未命名主题"}</span> · {LABELS[normalizeDerivation(row.derivation).state]}
        <DerivationNotice derivation={row.derivation} />
      </li>)}</ul>
      <Link to="/conflicts" className="mt-2 inline-flex min-h-9 items-center underline underline-offset-2">查看历史关联与更新争议</Link>
    </section> : null}
    <p className="text-[11px] text-muted">此处保留{snapshotLabel}的状态；后续更新不会自动重算这份结果。</p>
  </div>;
}

export function ReportDerivationNotice({ summary }: { summary?: DerivationSummary }) {
  const pending = pendingRelations(summary);
  if (pending === 0) return <p className="mt-2 text-[12px] text-muted">当前可见关联无待更新项；此状态不替代报告生成版本。</p>;
  return <section className="mb-3 rounded-ctl border border-st-wait-line bg-st-wait-bg px-3 py-2 text-[12px] leading-relaxed text-st-wait" aria-label="报告关联状态">
    <p className="font-medium">{pending === null ? "关联判定依据尚未确认" : `关联待更新：${pending} 项`}</p>
    {pending !== null && summary ? <p>聚类：匹配 {summary.clusters.active}、变化 {summary.clusters.stale}、缺失 {summary.clusters.missing}、未知 {summary.clusters.unknown}；争议：匹配 {summary.conflicts.active}、变化 {summary.conflicts.stale}、缺失 {summary.conflicts.missing}、未知 {summary.conflicts.unknown}。</p> : null}
    <p>报告文件最新也可能包含待核对的关联；重新生成报告不会更新争议的判定依据。</p>
    <Link to="/conflicts" className="inline-flex min-h-9 items-center underline underline-offset-2">查看关联并更新争议</Link>
  </section>;
}
