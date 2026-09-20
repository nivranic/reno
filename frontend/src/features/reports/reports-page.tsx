/** Reports: catalog + reading view. Markdown rendered safely (no raw HTML,
 * remote images blocked), heading TOC (labeled as heading-only search),
 * raw/copy/download/print affordances. */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Copy, Download, FileText, Printer } from "lucide-react";
import { reportsQuery } from "@/lib/queries";
import { PageHeader, EmptyState, ErrorState, ListSkeleton } from "@/components/shared/states";
import { MarkdownView, extractToc } from "@/components/shared/markdown-view";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { useToast } from "@/components/shared/toaster";
import { cn } from "@/lib/cn";

export default function ReportsPage() {
  const { data, error, isError, isPending, refetch } = useQuery(reportsQuery);
  const [active, setActive] = useState<string | null>(null);
  const [tocFilter, setTocFilter] = useState("");
  const [raw, setRaw] = useState(false);
  const toast = useToast();

  const names = Object.keys(data?.reports ?? {});
  const current = active && names.includes(active) ? active : (names[0] ?? null);
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
        desc={
          <>
            由 <code className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[11.5px]">reno report</code>{" "}
            生成;内容随处理进度更新
          </>
        }
        actions={
          current ? (
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

      {isPending ? (
        <ListSkeleton rows={8} />
      ) : isError ? (
        <ErrorState error={error} onRetry={() => void refetch()} context="报告" />
      ) : names.length === 0 ? (
        <EmptyState icon={<FileText size={30} />} title="暂无报告" desc="运行 `reno report` 生成后此处可阅读。" />
      ) : (
        <div className="grid gap-4 lg:grid-cols-[220px_1fr]">
          {/* catalog */}
          <nav aria-label="报告目录" className="space-y-1">
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
                {REPORT_LABEL[n] ?? n}
              </button>
            ))}
          </nav>

          <div className="min-w-0">
            {md === "" ? (
              <EmptyState title="该报告尚未生成" desc="运行 `reno report` 后刷新。" />
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
                  <h2 className="mb-3 border-b border-line pb-2 text-[16px] font-semibold text-ink">
                    {REPORT_LABEL[current ?? ""] ?? current}
                  </h2>
                  {raw ? (
                    <pre className="overflow-x-auto whitespace-pre-wrap rounded-panel border border-line bg-surface p-4 font-mono text-[12.5px] leading-relaxed text-ink-2">
                      {md}
                    </pre>
                  ) : (
                    <MarkdownView md={md} className="reno-md-wrap" />
                  )}
                  <p className="mt-4 text-[11px] text-muted">内容生成于最近一次 `reno report` 运行</p>
                </article>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

const REPORT_LABEL: Record<string, string> = {
  incremental_diff: "增量对比",
  checklist: "验收清单",
  conflicts: "争议汇总",
};
