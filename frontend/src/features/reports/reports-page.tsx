/** Reports: catalog + reading view. Markdown rendered safely (no raw HTML,
 * remote images blocked), heading TOC (labeled as heading-only search),
 * raw/copy/download/print affordances. The open report lives in ?r= so the
 * reading state is shareable and participates in browser history (§15.3),
 * which also lets the shell drop the bottom nav while reading (§4.2). */
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { Copy, Download, FileText, Loader2, Printer, RefreshCw, Sparkles } from "lucide-react";
import { reportsQuery } from "@/lib/queries";
import { PageHeader, EmptyState, ErrorState, ListSkeleton } from "@/components/shared/states";
import { MarkdownView, extractToc } from "@/components/shared/markdown-view";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { useToast } from "@/components/shared/toaster";
import { cn } from "@/lib/cn";
import { Badge } from "@/components/ui/badge";
import { reportMetadataQuery, taskCapabilitiesQuery, useCreateTask, useMaintenanceTasks, isActiveTask, taskDate, type ReportMetadata } from "@/lib/task-api";
import { TaskStatusPanel } from "@/features/collect/task-status-panel";
import { errMessage } from "@/app/query-utils";
import { ReportDerivationNotice } from "@/components/shared/derivation-notice";
import { pendingRelations } from "@/lib/derivation";

export default function ReportsPage() {
  const { data, error, isError, isPending, refetch } = useQuery(reportsQuery);
  const [params, setParams] = useSearchParams();
  const active = params.get("r");
  const [tocFilter, setTocFilter] = useState("");
  const [raw, setRaw] = useState(false);
  const toast = useToast();
  const metadata = useQuery(reportMetadataQuery);
  const capabilities = useQuery(taskCapabilitiesQuery);
  const taskList = useMaintenanceTasks();
  const generateStatic = useCreateTask("report", { mode: "static" });
  const generateSummary = useCreateTask("report", { mode: "summary" });
  const busy = (mode: string) => (taskList.data?.tasks ?? []).some((task) =>
    task.kind === "report" && task.payload.mode === mode && isActiveTask(task));

  // ?r= holds the open report; push (not replace) so browser back returns
  // from reading to the catalog (§15.3)
  const setActive = (name: string | null) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (name) next.set("r", name);
        else next.delete("r");
        return next;
      },
      { replace: false },
    );

  const names = Object.keys({ ...REPORT_LABEL, ...data?.reports });
  // ?r= is the single source of truth for "reading" — no JS breakpoint probe
  // (a matchMedia snapshot goes stale when the window resizes, and left the
  // catalog unreachable between md and lg). CSS handles the rest: below lg
  // the catalog IS the page until a report is open; at lg+ both columns show
  // (the article falls back to the first report, as before).
  const reading = active != null;
  const current = active && names.includes(active) ? active : (names.find((name) => data?.reports[name]) ?? names[0] ?? null);
  const md = current ? (data?.reports[current] ?? "") : "";
  const toc = useMemo(() => extractToc(md), [md]);
  const tocFiltered = tocFilter.trim()
    ? toc.filter((t) => t.text.toLowerCase().includes(tocFilter.trim().toLowerCase()))
    : toc;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(md);
      toast.success("已复制 Markdown 原文");
    } catch {
      toast.error("复制失败(浏览器未授权剪贴板)");
    }
  };
  const download = () => {
    const blob = new Blob([md], { type: "text/markdown;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${current ?? "report"}.md`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  return (
    <div className="mx-auto w-full max-w-[1080px] px-4 py-5 md:px-6">
      <PageHeader
        title="报告"
        desc="查看报告依据的知识版本；更新失败时仍可阅读上一版。"
        actions={
          current && reading ? (
            <>
              <Button variant="ghost" size="sm" onClick={() => setRaw(!raw)}>
                {raw ? "阅读视图" : "原文"}
              </Button>
              <Button variant="ghost" size="iconSm" title="复制 Markdown" onClick={() => void copy()}>
                <Copy size={14} />
              </Button>
              <Button variant="ghost" size="iconSm" title="下载 .md" onClick={download}>
                <Download size={14} />
              </Button>
              <Button variant="ghost" size="iconSm" title="打印" onClick={() => window.print()}>
                <Printer size={14} />
              </Button>
            </>
          ) : undefined
        }
      />

      <div className="mb-3 flex flex-wrap items-center gap-2 print:hidden">
        <Button variant="outline" disabled={!capabilities.data?.tasks || generateStatic.isPending || busy("static")}
          onClick={() => generateStatic.mutate()}>
          {generateStatic.isPending || busy("static") ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
          {busy("static") ? "静态报告生成中" : "更新静态报告"}
        </Button>
        <Button variant="soft" disabled={!capabilities.data?.tasks || generateSummary.isPending || busy("summary")}
          onClick={() => generateSummary.mutate()}>
          {generateSummary.isPending || busy("summary") ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
          {busy("summary") ? "AI 总结生成中" : "生成 AI 总结"}
        </Button>
        <span className="text-[11.5px] text-muted">静态报告不调用模型；AI 总结可能消耗模型配额。</span>
      </div>
      {capabilities.data?.tasks === false ? <p className="mb-3 text-[13px] text-st-wait">本节点暂不能执行报告任务；可继续阅读已有报告。</p> : null}
      {capabilities.isError ? <ErrorState error={capabilities.error} context="任务能力" onRetry={() => void capabilities.refetch()} /> : null}
      {generateStatic.isError || generateSummary.isError ? <p role="alert" className="mb-3 text-[13px] text-st-bad">
        {errMessage(generateStatic.error ?? generateSummary.error)}；再次点击可核对同一请求。
      </p> : null}
      <TaskStatusPanel kind="report" limit={3} hideWhenEmpty />

      {isPending ? (
        <ListSkeleton rows={8} />
      ) : isError ? (
        <ErrorState error={error} onRetry={() => void refetch()} context="报告" />
      ) : names.length === 0 ? (
        <EmptyState icon={<FileText size={30} />} title="暂无报告" desc="点击上方更新静态报告，或单独生成 AI 总结。" />
      ) : (
        <div className="grid gap-4 lg:grid-cols-[220px_1fr]">
          {/* catalog: on phones it IS the page until a report is open (§13) */}
          <nav
            aria-label="报告目录"
            className={cn("space-y-1", reading ? "hidden lg:block" : "block")}
          >
            {names.map((n) => (
              <button
                key={n}
                onClick={() => {
                  setActive(n);
                  setTocFilter("");
                }}
                className={cn(
                  "block w-full cursor-pointer rounded-ctl px-3 py-2 text-left text-[13.5px] transition-colors",
                  n === current
                    ? "bg-acc-soft font-medium text-acc"
                    : "text-ink-2 hover:bg-surface-2",
                )}
              >
                <span className="flex flex-wrap items-center justify-between gap-1.5">{REPORT_LABEL[n] ?? n}
                  <ReportStatus value={metadata.data?.reports[n]} /></span>
                {metadata.data?.reports[n] && pendingRelations(metadata.data.reports[n].derivation_summary) !== 0 ? <span className="mt-1 block text-[11px] text-st-wait">关联待核对</span> : null}
              </button>
            ))}
          </nav>

          <div className={cn("min-w-0", !reading && "hidden lg:block")}>
            {active ? (
              <button
                onClick={() => setActive(null)}
                className="mb-2 inline-flex cursor-pointer items-center gap-1 rounded-ctl px-1.5 py-1 text-[13px] text-muted hover:bg-surface-2 hover:text-ink lg:hidden"
              >
                ← 返回报告目录
              </button>
            ) : null}
            {md === "" ? (
              <EmptyState title="该报告尚未生成" desc={current === "summary" ? "点击「生成 AI 总结」后，会单独提交模型总结任务。" : "点击「更新静态报告」生成，无需调用模型。"} />
            ) : (
              <div className="grid gap-4 xl:grid-cols-[200px_1fr]">
                {/* heading toc */}
                <aside className="order-2 xl:order-1">
                  <Input
                    aria-label="筛选目录标题(仅匹配标题,不是全文检索)"
                    placeholder="筛标题(非全文)…"
                    value={tocFilter}
                    onChange={(e) => setTocFilter(e.target.value)}
                    className="mb-2 h-8 text-xs"
                  />
                  <p className="mb-1.5 px-1 text-[10.5px] text-muted">目录(仅标题匹配)</p>
                  <ul className="max-h-[70vh] space-y-0.5 overflow-y-auto pr-1">
                    {tocFiltered.map((t) => (
                      <li key={t.id}>
                        <a
                          href={`#${t.id}`}
                          className={cn(
                            "block truncate rounded px-2 py-1 text-[12px] text-muted hover:bg-surface-2 hover:text-ink",
                            t.level === 2 && "pl-3",
                            t.level >= 3 && "pl-5 text-[11.5px]",
                          )}
                          title={t.text}
                        >
                          {t.text}
                        </a>
                      </li>
                    ))}
                  </ul>
                </aside>

                <article className="order-1 min-w-0 xl:order-2">
                  {/* report title sits at the page-title tier (19px) so the
                      rendered markdown h1 (18px) below it never inverts it */}
                  <h2 className="mb-3 border-b border-line pb-2 text-[19px] font-semibold text-ink">
                    {REPORT_LABEL[current ?? ""] ?? current}
                  </h2>
                  <ReportDerivationNotice summary={metadata.isError ? undefined : metadata.data?.reports[current ?? ""]?.derivation_summary} />
                  {raw ? (
                    <pre className="overflow-x-auto whitespace-pre-wrap rounded-panel border border-line bg-surface p-4 font-mono text-[12.5px] leading-relaxed text-ink-2">
                      {md}
                    </pre>
                  ) : (
                    <MarkdownView md={md} className="reno-md-wrap" />
                  )}
                  <p className="mt-4 flex flex-wrap items-center gap-2 text-[11px] text-muted">
                    <ReportStatus value={metadata.data?.reports[current ?? ""]} />
                    生成时间：{taskDate(metadata.data?.reports[current ?? ""]?.generated_at)}
                    {metadata.data?.reports[current ?? ""]?.stale ? "知识或复核已变化，请更新后再用于决策。" : ""}
                    {metadata.isError ? "报告状态读取失败，当前新鲜度尚未确认。" : ""}
                  </p>
                </article>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function ReportStatus({ value }: { value?: ReportMetadata }) {
  if (!value) return <Badge tone="neutral">状态待核对</Badge>;
  const labels = { missing: "尚未生成", generating: "生成中", latest: "最新", stale: "已过期", failed: "生成失败" };
  return <Badge tone={value.status === "latest" ? "ok" : value.status === "failed" ? "bad" : value.status === "generating" ? "run" : "wait"}>
    {labels[value.status]}{value.status === "failed" && value.has_content ? " · 保留旧版" : ""}
  </Badge>;
}

const REPORT_LABEL: Record<string, string> = {
  summary: "全库总结",
  incremental_diff: "增量对比",
  checklist: "验收清单",
  conflicts: "争议汇总",
  prices: "价格记录",
};
