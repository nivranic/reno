/** Inbox: import entries (dialog), live stats, sortable/filterable video list.
 * Adaptive polling lives in videosQuery (3s while work is active, off when
 * idle) — no setTimeout page reloads. */
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDownUp, Inbox as InboxIcon, Plus, RefreshCw } from "lucide-react";
import { qk, videosQuery } from "@/lib/queries";
import { useUrlParam, useScrollRestore } from "@/lib/url-state";
import { PageHeader, EmptyState, ErrorState, RefreshWarning, ListSkeleton } from "@/components/shared/states";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/field";
import { ImportDialog } from "./import-dialog";
import { VideoTable } from "./video-table";
import { useToast } from "@/components/shared/toaster";

type SortKey = "imported" | "atoms" | "duration" | "title";

export default function InboxPage() {
  const toast = useToast();
  const qc = useQueryClient();
  const { data, isError, error, isPending, refetch, isFetching, dataUpdatedAt } = useQuery(videosQuery);
  const [q, setQ] = useUrlParam("q");
  const [status, setStatus] = useUrlParam("status");
  const [sort, setSort] = useUrlParam("sort", "imported");
  const [importOpen, setImportOpen] = useState(false);
  const scroll = useScrollRestore("inbox");
  // restore list scroll on back-navigation; save when leaving for a detail page
  useEffect(() => {
    scroll.restore();
    return scroll.save;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const videos = useMemo(() => data?.videos ?? [], [data]);
  const filtered = useMemo(() => {
    let rows = videos;
    if (q.trim()) {
      const needle = q.trim().toLowerCase();
      rows = rows.filter(
        (v) =>
          (v.title ?? "").toLowerCase().includes(needle) ||
          (v.author ?? "").toLowerCase().includes(needle) ||
          v.video_id.toLowerCase().includes(needle),
      );
    }
    if (status) rows = rows.filter((v) => v.status === status);
    const by: Record<SortKey, (a: (typeof videos)[number], b: (typeof videos)[number]) => number> = {
      imported: (a, b) => (b.imported_at ?? "").localeCompare(a.imported_at ?? ""),
      atoms: (a, b) => b.atoms - a.atoms,
      duration: (a, b) => (b.duration_ms ?? 0) - (a.duration_ms ?? 0),
      title: (a, b) => (a.title ?? "").localeCompare(b.title ?? "", "zh"),
    };
    return [...rows].sort(by[(sort as SortKey) in by ? (sort as SortKey) : "imported"]);
  }, [videos, q, status, sort]);

  // invalidate list on import success (dialog mutations call these too via qc)
  const refresh = () => void qc.invalidateQueries({ queryKey: qk.videos });

  const stats = data?.stats;

  return (
    <div className="mx-auto w-full max-w-[1180px] px-4 py-5 md:px-6">
      <PageHeader
        title="收件箱"
        desc={stats ? `${stats.videos} 个视频 · ${stats.atoms} 条知识原子 · ${stats.conflicts_open} 个争议待复核` : undefined}
        actions={
          <>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void refetch()}
              disabled={isFetching}
              title="刷新列表"
            >
              <RefreshCw size={14} className={isFetching ? "animate-spin" : ""} /> 刷新
            </Button>
            <Button size="sm" onClick={() => setImportOpen(true)}>
              <Plus size={14} /> 导入视频
            </Button>
          </>
        }
      />

      {/* stats strip */}
      {stats ? (
        <div className="mb-4 grid grid-cols-2 gap-2.5 sm:grid-cols-4">
          <StatCard label="视频" value={stats.videos} />
          <StatCard label="知识原子" value={stats.atoms} />
          <StatCard label="聚类" value={stats.clusters} />
          <StatCard
            label="待复核争议"
            value={stats.conflicts_open}
            hint={`${stats.conflicts} 总计`}
            link="/conflicts"
          />
        </div>
      ) : null}

      {/* toolbar */}
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Input
          aria-label="搜索视频库"
          placeholder="搜索标题 / UP主 / 视频ID…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          className="h-8 w-full max-w-[280px] text-[13px]"
        />
        <Select
          aria-label="按状态筛选"
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          className="h-8 w-[130px] text-[13px]"
        >
          <option value="">全部状态</option>
          <option value="processed">已完成</option>
          <option value="imported">待处理</option>
          <option value="error">失败</option>
        </Select>
        <Select
          aria-label="排序"
          value={sort}
          onChange={(e) => setSort(e.target.value)}
          className="h-8 w-[120px] text-[13px]"
        >
          <option value="imported">最近导入</option>
          <option value="atoms">原子最多</option>
          <option value="duration">时长最长</option>
          <option value="title">标题</option>
        </Select>
        <span className="ml-auto hidden items-center gap-1 text-[11.5px] text-muted sm:flex">
          <ArrowDownUp size={12} /> {filtered.length}/{videos.length}
        </span>
      </div>

      {isPending ? (
        <ListSkeleton rows={8} />
      ) : isError ? (
        <ErrorState error={error} onRetry={() => void refetch()} context="视频列表" />
      ) : (
        <>
          {isFetching && dataUpdatedAt > 0 ? <RefreshWarning onRetry={() => void refetch()} /> : null}
          {filtered.length === 0 ? (
            <EmptyState
              icon={<InboxIcon size={30} />}
              title={videos.length === 0 ? "还没有视频" : "没有匹配的视频"}
              desc={
                videos.length === 0
                  ? "导入B站/抖音链接,或用采集书签把收藏页链接批量提交进来。"
                  : "调整搜索词或筛选条件试试。"
              }
              action={
                videos.length === 0 ? (
                  <Button size="sm" onClick={() => setImportOpen(true)}>
                    <Plus size={14} /> 导入第一个视频
                  </Button>
                ) : undefined
              }
            />
          ) : (
            <VideoTable rows={filtered} />
          )}
        </>
      )}

      <ImportDialog
        open={importOpen}
        onOpenChange={setImportOpen}
        onImported={() => {
          refresh();
          toast.success("已加入处理队列,状态将自动更新");
        }}
      />
    </div>
  );
}

function StatCard({
  label,
  value,
  hint,
  link,
}: {
  label: string;
  value: number;
  hint?: string;
  link?: string;
}) {
  const body = (
    <>
      <div className="font-mono text-[22px] font-semibold tabular-nums leading-7 text-ink">
        {value}
      </div>
      <div className="text-[11.5px] text-muted">
        {label}
        {hint ? <span className="ml-1 opacity-70">/{hint}</span> : null}
      </div>
    </>
  );
  return link ? (
    <Link
      to={link}
      className="rounded-panel border border-line bg-surface px-3.5 py-2.5 transition-colors hover:border-acc-line"
    >
      {body}
    </Link>
  ) : (
    <div className="rounded-panel border border-line bg-surface px-3.5 py-2.5">{body}</div>
  );
}
