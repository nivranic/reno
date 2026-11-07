import { useEffect, useState } from "react";
import { useSearchParams } from "react-router";
import { keepPreviousData, useMutation, useQuery } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, Download, FileSearch, Loader2, RefreshCw } from "lucide-react";
import { PageHeader, EmptyState, ErrorState, ListSkeleton, RefreshWarning } from "@/components/shared/states";
import { EvidenceLink } from "@/components/shared/evidence-link";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs } from "@/components/ui/tabs";
import { errMessage } from "@/app/query-utils";
import { fmtDate } from "@/lib/time";
import { cn } from "@/lib/cn";
import { downloadReconciliation, exportReconciliation, getReconciliation, reconciliationLink,
  RECONCILIATION_REASONS, type ReconciliationFilters, type ReconciliationItem,
  type ReconciliationReason, type ReconciliationResponse, type ReconciliationScope } from "@/lib/reconciliation-api";

const LIMIT = 30;
const REASON: Record<ReconciliationReason | "all", string> = {
  all: "全部原因", active: "绑定有效", stale: "内容已变化", missing: "目标已缺失",
  legacy_unbound: "旧记录未绑定", invalid_time: "记录时间异常",
};
const SCOPE: Record<ReconciliationScope, string> = { attention: "当前待核对", history: "历史记录", all: "全部记录" };
const ACTION: Record<string, string> = { confirm: "确认知识", reject: "驳回", clear_review: "清除人工复核",
  accept_a: "采纳 A", accept_b: "采纳 B", both: "两者各适用" };

export default function ReconciliationPage() {
  const [params, setParams] = useSearchParams();
  const rawReason = params.get("reason");
  const reason: ReconciliationFilters["reason"] = RECONCILIATION_REASONS.includes(rawReason as ReconciliationReason)
    ? rawReason as ReconciliationReason : "all";
  const rawScope = params.get("scope");
  const scope: ReconciliationScope = rawScope === "history" || rawScope === "all" ? rawScope : "attention";
  const rawOffset = Number(params.get("offset") ?? 0);
  const offset = Number.isSafeInteger(rawOffset) && rawOffset >= 0 ? rawOffset : 0;
  const [lastGood, setLastGood] = useState<ReconciliationResponse | null>(null);
  const query = useQuery({
    queryKey: ["reconciliation", reason, scope, offset, LIMIT],
    queryFn: ({ signal }) => getReconciliation({ reason, scope }, offset, LIMIT, signal),
    placeholderData: keepPreviousData,
    retry: false,
  });
  useEffect(() => {
    if (query.data && !query.isPlaceholderData) setLastGood(query.data);
  }, [query.data, query.isPlaceholderData]);
  const data = query.data ?? lastGood;
  const selectionMatches = !!data && data.filters.reason === reason && data.filters.scope === scope && data.offset === offset;
  const outdated = query.isError || query.isPlaceholderData || !selectionMatches;
  const download = useMutation({ mutationFn: async (filters: ReconciliationFilters) => {
    const content = await exportReconciliation(filters);
    downloadReconciliation(content);
    return filters;
  } });

  const choose = (next: Partial<ReconciliationFilters> & { offset?: number }) => {
    setParams((previous) => {
      const value = new URLSearchParams(previous);
      const selected = { reason, scope, offset: 0, ...next };
      for (const [key, entry] of Object.entries(selected)) {
        if ((key === "reason" && entry === "all") || (key === "scope" && entry === "attention") || (key === "offset" && entry === 0)) value.delete(key);
        else value.set(key, String(entry));
      }
      return value;
    });
  };
  const pageCount = data ? Math.max(1, Math.ceil(data.total / data.limit)) : 1;
  const shownPage = data ? Math.floor(data.offset / data.limit) + 1 : 1;

  return <div className="mx-auto w-full max-w-[980px] px-4 py-5 md:px-6">
    <PageHeader title="复核对账" desc="核对旧决定与当前知识的关联；查看和导出不会改变审核结论。" actions={<>
      <Button size="iconSm" variant="outline" aria-label="刷新复核对账" disabled={query.isFetching} onClick={() => void query.refetch()}>
        <RefreshCw size={14} className={query.isFetching ? "animate-spin" : undefined} />
      </Button>
      <Button size="sm" variant="outline" disabled={!data || outdated || query.isFetching || download.isPending}
        onClick={() => download.mutate({ reason, scope })}>
        {download.isPending ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />}导出 Markdown
      </Button>
    </>} />
    {download.isError && <p role="alert" className="mb-3 text-[13px] text-st-bad">导出失败：{errMessage(download.error)}；当前筛选仍保留。</p>}
    {download.isSuccess && <p role="status" className="mb-3 text-[12.5px] text-muted">已导出：{SCOPE[download.data.scope]} · {REASON[download.data.reason]}（全部匹配记录）。</p>}

    <section aria-label="按关联原因筛选" className="mb-4">
      <p className="mb-2 text-[12px] text-muted">{outdated && data ? "上次统计 · " : ""}原因计数覆盖全部可见复核记录，按记录数统计。</p>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        {(["all", ...RECONCILIATION_REASONS] as const).map((value) => <button key={value} aria-pressed={reason === value}
          onClick={() => choose({ reason: value })}
          className={cn("flex min-h-11 cursor-pointer items-center justify-between gap-2 rounded-ctl border px-3 py-2 text-left text-[13px] transition-colors",
            reason === value ? "border-acc-line bg-acc-soft text-acc" : "border-line bg-surface text-ink-2 hover:border-acc")}>
          <span>{REASON[value]}</span><span className="font-mono tabular-nums">{data ? value === "all" ? data.summary.total : data.summary.by_reason[value].total : "—"}</span>
        </button>)}
      </div>
    </section>

    <div className="mb-3 flex flex-wrap items-center gap-2">
      <Tabs ariaLabel="复核记录范围" value={scope} onChange={(value) => choose({ scope: value })}
        className="w-full [&>button]:whitespace-nowrap max-sm:[&>button]:flex-1 sm:w-auto" items={[
          { value: "attention", label: `待核对${data ? ` ${data.summary.needs_attention}` : ""}` },
          { value: "history", label: `历史${data ? ` ${data.summary.historical}` : ""}` },
          { value: "all", label: `全部${data ? ` ${data.summary.total}` : ""}` },
        ]} />
    </div>
    <p className="mb-4 text-[12.5px] leading-relaxed text-muted">
      {scope === "attention" ? "保留仍需核对的旧决定；内容变化、目标缺失、原决定无效或无法可靠绑定时，不会自动套用到新内容。"
        : scope === "history" ? "历史记录保留当时的决定。后续记录可能已替代它，不代表当前仍需处理。"
          : "同时查看当前与历史记录；关联有效不等于全库知识已经审核通过。"}
    </p>
    {query.isError && data && <RefreshWarning onRetry={() => void query.refetch()} />}
    {data && !selectionMatches && <p role="status" className="mb-3 text-[12.5px] text-st-wait">
      当前筛选：{SCOPE[scope]} · {REASON[reason]}；{query.isError ? "读取失败，仍展示" : "正在读取，暂展示"}
      上次结果（{SCOPE[data.filters.scope]} · {REASON[data.filters.reason]}，第 {shownPage} 页）。
    </p>}

    {!data ? query.isError ? <ErrorState error={query.error} context="复核对账" onRetry={() => void query.refetch()} />
      : <ListSkeleton rows={3} /> : <>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2 text-[12px] text-muted">
        <span>{outdated ? "上次结果" : "筛选结果"}：{data.total} 条 · 知识版本 {data.version.library_epoch}.{data.version.kb_revision}</span>
        <span>复核记录水位 {data.event_watermark.high_watermark}</span>
      </div>
      {data.items.length ? <ul aria-label="复核对账记录" className="space-y-3">{data.items.map((item) => <ReconciliationCard key={item.record_id} item={item} />)}</ul>
        : <EmptyState icon={<FileSearch size={28} />} title="当前筛选下没有复核记录" desc="可调整原因或切换到全部记录；这不表示全库知识已通过审核。" />}
    </>}
    <nav aria-label="复核对账分页" className="mt-4 flex flex-wrap items-center justify-between gap-2">
      <Button variant="outline" size="sm" aria-label="上一页复核记录" disabled={offset === 0 || query.isFetching}
        onClick={() => choose({ offset: Math.max(0, offset - LIMIT) })}><ArrowLeft size={14} />上一页</Button>
      <span className="text-[12px] text-muted">{data ? `${outdated ? "上次结果 · " : ""}第 ${shownPage} / ${pageCount} 页` : "等待对账结果"}</span>
      <Button variant="outline" size="sm" aria-label="下一页复核记录" disabled={!data || outdated || query.isFetching || data.offset + data.limit >= data.total}
        onClick={() => choose({ offset: data ? data.offset + data.limit : 0 })}>下一页<ArrowRight size={14} /></Button>
    </nav>
  </div>;
}

function ReconciliationCard({ item }: { item: ReconciliationItem }) {
  const target = reconciliationLink(item.target_url);
  const sources = item.source_links.map((source) => ({ ...source, href: reconciliationLink(source.url) })).filter((source) => source.href);
  return <li data-reconciliation-record={item.record_id} className="min-w-0 rounded-panel border border-line bg-surface p-4 text-[13.5px]">
    <div className="mb-2 flex flex-wrap items-center gap-2">
      <Badge tone={item.needs_attention ? "wait" : "neutral"}>{item.needs_attention ? "当前待核对" : item.attention_state === "historical" ? "历史记录" : "当前记录"}</Badge>
      <Badge tone="neutral">{REASON[item.binding_status] ?? "关联待核对"}</Badge>
      {!item.decision_valid && <Badge tone="wait">原决定无效，需重新核对</Badge>}
      <span className="text-[12px] text-muted">{item.target_kind === "atom" ? "知识原子" : item.target_kind === "conflict" ? "争议" : "旧记录"}</span>
    </div>
    <h2 className="break-words font-medium text-ink">{item.title || "未命名复核记录"}</h2>
    <p className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[12px] text-muted"><span>当时决定：{ACTION[item.action] ?? "其他复核决定"}</span>
      <span>{item.binding_status === "invalid_time" ? "原记录时间待核对" : fmtDate(item.decided_at || item.created_at)}</span></p>
    <div className="mt-3 rounded-ctl bg-surface-2 px-3 py-2.5">
      <p className="mb-1 text-[12px] font-medium text-ink-2">当时内容快照</p>
      <p className="whitespace-pre-wrap break-words leading-relaxed text-ink-2">{item.snapshot_summary || "未保存可读的内容快照。"}</p>
      <p className="mt-1.5 text-[12px] text-muted">这是作出决定时的记录，不代表当前知识或来源仍然有效。</p>
    </div>
    {item.current_summary && <div className="mt-3"><p className="text-[12px] font-medium text-ink-2">当前内容</p>
      <p className="mt-1 whitespace-pre-wrap break-words leading-relaxed text-ink-2">{item.current_summary}</p></div>}
    {item.note && <p className="mt-3 whitespace-pre-wrap break-words text-ink-2"><span className="text-muted">复核备注：</span>{item.note}</p>}
    {item.superseded_by && <p className="mt-2 text-[12px] text-muted">这条记录已有后续决定；原记录继续保留。</p>}
    <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-[12.5px]">
      {target && <EvidenceLink to={target} className="py-1.5 text-acc underline underline-offset-2">{item.target_kind === "conflict" ? "查看争议列表" : "查看当前来源"}</EvidenceLink>}
      {sources.map((source, index) => <EvidenceLink key={`${source.video_id}-${index}`} to={source.href!}
        className="min-w-0 break-all py-1.5 text-acc underline underline-offset-2" title={source.title ?? undefined}>来源 {index + 1}{source.title ? `：${source.title}` : ""}</EvidenceLink>)}
      {!target && !sources.length && <span className="text-muted">当前没有可定位的可读来源。</span>}
    </div>
  </li>;
}
