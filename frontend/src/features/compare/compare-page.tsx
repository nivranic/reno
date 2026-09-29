/** Compare: side-by-side evidence for 2-3 objects/schemes. Deterministic
 * grouping over real retrieved atoms (no LLM synthesis); dimensions without
 * evidence are shown as explicit gaps, judge conflicts spanning both sides
 * surface on top. */
import { useState } from "react";
import { Link } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { Scale } from "lucide-react";
import { compareQuery } from "@/lib/queries";
import type { CompareSide, SearchRow } from "@/lib/api-types";
import { fmtMs } from "@/lib/time";
import { PageHeader, EmptyState, ErrorState, InlineSpinner } from "@/components/shared/states";
import { PolarityBadge } from "@/components/shared/status-badge";
import { DimensionBadge, NatureBadge } from "@/components/shared/knowledge-tags";
import { Input } from "@/components/ui/field";

export default function ComparePage() {
  const [raw, setRaw] = useState("");
  const [items, setItems] = useState("");
  const results = useQuery(compareQuery(items));

  const submit = (value: string) => {
    const parts = value
      .split(/[,，/ ]+/)
      .map((s) => s.trim())
      .filter(Boolean)
      .slice(0, 3);
    if (parts.length >= 2) setItems(parts.join(","));
  };

  return (
    <div className="mx-auto w-full max-w-[1100px] px-4 py-5 md:px-6">
      <PageHeader
        title="方案对比"
        desc="按知识维度分组展示库内真实证据;没有证据的维度如实标注,不做强行排名。"
      />

      <form
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          submit(raw);
        }}
        className="mb-4 flex flex-wrap gap-2"
      >
        <div className="relative min-w-[260px] flex-[1_1_260px]">
          <Scale size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
          <Input
            aria-label="对比对象"
            placeholder="输入 2-3 个对象,用空格/逗号分隔,如:瓷砖 地板"
            value={raw}
            onChange={(e) => setRaw(e.target.value)}
            className="pl-9"
          />
        </div>
        <button
          type="submit"
          className="h-9 cursor-pointer rounded-ctl bg-acc px-4 text-sm font-semibold text-white hover:bg-acc-strong disabled:opacity-50 dark:text-acc-contrast"
          disabled={raw.split(/[,，/ ]+/).filter((s) => s.trim()).length < 2}
        >
          对比
        </button>
      </form>

      {!items ? (
        <EmptyState
          icon={<Scale size={30} />}
          title="输入要对比的对象"
          desc="例如「瓷砖 / 地板」「墙压地 / 地压墙」。结果完全来自库内已有证据。"
        />
      ) : results.isError ? (
        <ErrorState error={results.error} onRetry={() => void results.refetch()} context="对比" />
      ) : results.isPending ? (
        <InlineSpinner label="检索证据中…" />
      ) : (
        <>
          {results.data!.conflicts.length > 0 ? (
            <div className="mb-3 rounded-panel border border-st-review-line bg-st-review-bg p-3 text-[12.5px] leading-relaxed text-st-review">
              {results.data!.conflicts.map((c) => (
                <p key={c.cluster_id}>
                  ⚠ 库内存在观点分歧:{c.topic}
                  {c.linked_conflict ? (
                    <Link to="/conflicts" className="ml-1 underline">
                      去复核
                    </Link>
                  ) : null}
                </p>
              ))}
            </div>
          ) : null}
          <div
            className={`grid gap-3 ${
              results.data!.sides.length === 3 ? "md:grid-cols-3" : "md:grid-cols-2"
            }`}
          >
            {results.data!.sides.map((s) => (
              <SideColumn key={s.item} side={s} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function SideColumn({ side }: { side: CompareSide }) {
  return (
    <section className="rounded-panel border border-line bg-surface p-3.5">
      <header className="mb-2 flex items-baseline justify-between gap-2">
        <h2 className="text-[15px] font-semibold text-ink">{side.item}</h2>
        <span className="text-[11.5px] text-muted">{side.total} 条证据</span>
      </header>
      {side.total === 0 ? (
        <p className="py-4 text-center text-[12.5px] text-muted">
          库内没有与「{side.item}」词面相关的原子,可先去采集页补充视频。
        </p>
      ) : (
        <div className="space-y-3">
          {side.groups.map((g) => (
            <div key={g.dimension}>
              <div className="mb-1 flex items-center gap-1.5">
                <DimensionBadge dimension={g.dimension} />
                <span className="text-[11px] text-muted">{g.atoms.length}</span>
              </div>
              <ul className="space-y-1.5">
                {g.atoms.map((a) => (
                  <AtomLine key={a.id} row={a} />
                ))}
              </ul>
            </div>
          ))}
          <details className="text-[11.5px] text-muted">
            <summary className="cursor-pointer select-none">
              库内暂无证据的维度({side.missing_dimensions.length})
            </summary>
            <p className="mt-1 leading-relaxed">
              {side.missing_dimensions.join("、")}
            </p>
          </details>
        </div>
      )}
    </section>
  );
}

function AtomLine({ row }: { row: SearchRow }) {
  return (
    <li>
      <Link
        to={row.video ? `/videos/${row.video}?t=${row.ms}` : "#"}
        className={`block rounded-ctl border border-line px-2.5 py-2 transition-colors hover:bg-acc-soft/20 ${
          row.video ? "" : "pointer-events-none"
        }`}
      >
        <div className="flex items-start gap-1.5">
          <PolarityBadge polarity={row.polarity} className="mt-0.5 shrink-0" />
          <p className="min-w-0 flex-1 text-[12.5px] leading-relaxed text-ink">
            {row.claim}
          </p>
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 pl-0.5 text-[11px] text-muted">
          <NatureBadge nature={row.evidence_nature} />
          <span>{row.space ?? "—"}</span>
          <span className="font-mono tabular-nums">{fmtMs(row.ms)}</span>
          <span className="min-w-0 truncate">{row.video_title || "无来源"}</span>
        </div>
      </Link>
    </li>
  );
}
