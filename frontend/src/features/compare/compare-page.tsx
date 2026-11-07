/** Compare: side-by-side evidence for 2-3 objects/schemes. Deterministic
 * grouping over real retrieved atoms (no LLM synthesis); dimensions without
 * evidence are shown as explicit gaps, judge conflicts spanning both sides
 * surface on top. */
import { newOperationId } from "@/lib/id";
import { Scale } from "lucide-react";
import { clearCompareSession, expandCompare, patchCompare, submitCompare, useReadingSession } from "@/lib/reading-session";
import { captureReadingPositions } from "@/lib/url-state";
import { EvidenceLink } from "@/components/shared/evidence-link";
import { KnowledgeDetails } from "@/components/shared/knowledge-details";
import { ProjectContextBar } from "@/components/shared/project-context-bar";
import { SaveDecisionButton } from "@/features/decisions/decision-dialog";
import { conditionsEqual, conditionsText } from "@/features/project/project-context-editor";
import type { CompareSide, SearchRow } from "@/lib/api-types";
import { fmtMs } from "@/lib/time";
import { PageHeader, EmptyState, ErrorState, InlineSpinner } from "@/components/shared/states";
import { PolarityBadge } from "@/components/shared/status-badge";
import { DimensionBadge } from "@/components/shared/knowledge-tags";
import { Input } from "@/components/ui/field";
import { Button } from "@/components/ui/button";
import { RelationNotices } from "@/components/shared/derivation-notice";

export default function ComparePage() {
  const { compare, storageNotice } = useReadingSession();
  const { draft: raw, result } = compare;

  return (
    <div className="mx-auto w-full max-w-[1100px] px-4 py-5 md:px-6">
      <PageHeader
        title="方案对比"
        actions={<Button variant="outline" size="sm" onClick={clearCompareSession}>清空比较</Button>}
        desc="按知识维度展示真实证据与覆盖范围，结合适用条件比较，不做强行排名。"
      />

      <ProjectContextBar value={compare.context} onChange={(context) => patchCompare({ context })} />

      <form
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          void submitCompare(raw);
        }}
        className="mb-4 flex flex-wrap gap-2"
      >
        <div className="relative min-w-[260px] flex-[1_1_260px]">
          <Scale size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
          <Input
            aria-label="对比对象"
            placeholder="输入 2-3 个对象,用空格/逗号分隔,如:瓷砖 地板"
            value={raw}
            maxLength={300}
            onChange={(e) => patchCompare({ draft: e.target.value })}
            className="pl-9 max-md:h-11"
          />
        </div>
        <Button
          type="submit"
          size="md"
          disabled={raw.split(/[,，/ ]+/).filter((s) => s.trim()).length < 2}
        >
          对比
        </Button>
      </form>

      <p className="mb-2 text-[11px] text-muted">本标签页暂存，可清空 · 修改对象后点击对比才会重新检索。</p>
      {(storageNotice || compare.notice) && <p role="status" className="mb-2 text-[12px] text-st-wait">{storageNotice || compare.notice}</p>}

      {compare.status === "loading" && !result ? <InlineSpinner label="检索证据中…" /> : compare.status === "failed" && !result ? (
        <ErrorState error={new Error(compare.error)} onRetry={() => void submitCompare(compare.items)} context="对比" />
      ) : !result ? (
        <EmptyState
          icon={<Scale size={30} />}
          title="输入要对比的对象"
          desc="例如「瓷砖 / 地板」「墙压地 / 地压墙」。结果完全来自库内已有证据。"
        />
      ) : (
        <>
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2"><p className="text-[12px] text-muted">本结果使用：{conditionsText(compare.resultContext?.context_snapshot ?? {})}{!conditionsEqual(compare.resultContext?.context_snapshot, compare.context?.context_snapshot) ? "（条件已修改，旧结果保持不变）" : ""}</p><SaveDecisionButton label="保存比较决定" seed={() => ({ title: `${result.sides.map((side) => side.item).join("与")}怎么选`, context_snapshot: compare.resultContext?.context_snapshot ?? {}, alternatives: result.sides.map((side) => ({ option_id: newOperationId(), label: side.item })), content_snapshot: { kind: "comparison", text: result.sides.map((side) => `## ${side.item}\n${side.groups.map((group) => `### ${group.dimension}\n${group.atoms.map((atom) => `- ${atom.claim}`).join("\n")}`).join("\n")}`).join("\n\n") }, evidence: [...new Map(result.sides.flatMap((side) => side.groups.flatMap((group) => group.atoms)).map((atom) => [atom.id, atom])).values()].map((atom) => ({ target_kind: "atom", target_id: atom.id, target_fingerprint: atom.target_fingerprint ?? "", label: atom.claim })) })} /></div>
          {compare.error && <ErrorState error={new Error(compare.error)} onRetry={() => void expandCompare()} context="展开对比" />}
          <RelationNotices relations={result.conflicts.map((row) => ({ ...row, id: row.cluster_id }))} snapshotLabel="对比检索时" />
          <div
            className={`grid gap-3 ${
              result.sides.length === 3 ? "md:grid-cols-3" : "md:grid-cols-2"
            }`}
          >
            {result.sides.map((s) => (
              <SideColumn key={s.item} side={s} expanded={compare.expanded[s.item] ?? false} onExpand={(open) => patchCompare({ expanded: { ...compare.expanded, [s.item]: open } })} limit={compare.perDimensionLimit} loading={compare.status === "loading"} onLoadMore={() => { captureReadingPositions(); void expandCompare(); }} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function SideColumn({ side, expanded, onExpand, limit, loading, onLoadMore }: { side: CompareSide; expanded: boolean; onExpand: (open: boolean) => void; limit: number; loading: boolean; onLoadMore: () => void }) {
  return (
    <section className="rounded-panel border border-line bg-surface p-3.5">
      <header className="mb-2 flex items-baseline justify-between gap-2">
        <h2 className="text-[15px] font-semibold text-ink">{side.item}</h2>
        <span className="text-[11.5px] text-muted">展示 {side.groups.reduce((sum, group) => sum + group.atoms.length, 0)} / {side.total} 条证据</span>
      </header>
      {side.total === 0 ? (
        <EmptyState
          compact
          title={side.coverage_scope === "full_filtered_set" ? `本次完整筛选范围内没有与「${side.item}」词面相关的原子` : `当前结果未覆盖「${side.item}」的证据`}
          desc="可先去采集页补充视频。"
        />
      ) : (
        <div className="space-y-3">
          {side.groups.map((g) => (
            <div key={g.dimension}>
              <div className="mb-1 flex items-center gap-1.5">
                <DimensionBadge dimension={g.dimension} />
                <span className="text-[11px] text-muted">{g.atoms.length} / {g.total ?? g.atoms.length} 条</span>
              </div>
              <ul className="space-y-1.5">
                {g.atoms.map((a) => (
                  <AtomLine key={a.id} row={a} anchor={`${side.item}:${g.dimension}:${a.id}`} />
                ))}
              </ul>
              {g.truncated && <p className="mt-1 text-[11.5px] text-muted">本维度还有未展示的证据，当前片段不能代表全部观点。</p>}
            </div>
          ))}
          <details open={expanded} onToggle={(event) => { if (event.currentTarget.open !== expanded) onExpand(event.currentTarget.open); }} className="text-[11.5px] text-muted">
            <summary className="cursor-pointer select-none">
              {side.coverage_scope === "full_filtered_set" ? "完整筛选范围内暂无证据的维度" : "当前结果未覆盖的维度"}({side.missing_dimensions.length})
            </summary>
            <p className="mt-1 leading-relaxed">
              {side.missing_dimensions.join("、")}
            </p>
          </details>
          {side.truncated && (limit < 100 ? <Button variant="outline" size="sm" disabled={loading} onClick={onLoadMore}>{loading ? "正在展开…" : "展开更多证据"}</Button> : <p className="text-[11.5px] text-st-wait">已达每维度 100 条展示上限，仍有未展示证据，请缩小对象范围。</p>)}
        </div>
      )}
    </section>
  );
}

function AtomLine({ row, anchor }: { row: SearchRow; anchor: string }) {
  const body = (
    <>
      <div className="flex items-start gap-1.5">
        <PolarityBadge polarity={row.polarity} className="mt-0.5 shrink-0" />
        <p className="min-w-0 flex-1 text-[12.5px] leading-relaxed text-ink">
          {row.claim}
        </p>
      </div>
      <KnowledgeDetails knowledge={row} compact />
      <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 pl-0.5 text-[11px] text-muted">
        <span>{row.space ?? "—"}</span>
        <span className="font-mono tabular-nums">{fmtMs(row.ms)}</span>
        <span className="min-w-0 truncate" title={row.video_title || undefined}>
          {row.video_title || "无来源"}
        </span>
      </div>
    </>
  );
  // no-video rows render as a static block, not a dead "#" link that stays
  // keyboard-focusable (pointer-events-none only blocks the mouse)
  if (!row.video) {
    return <li data-reading-anchor={anchor} className="block rounded-ctl border border-line px-2.5 py-2">{body}</li>;
  }
  return (
    <li data-reading-anchor={anchor}>
      <EvidenceLink
        to={`/videos/${encodeURIComponent(row.video)}?t=${row.ms}`}
        className="block rounded-ctl border border-line px-2.5 py-2 transition-colors hover:bg-acc-soft/20"
      >
        {body}
      </EvidenceLink>
    </li>
  );
}
