/** Conflict review: two-column side-by-side (stacked on phones), real
 * four-action semantics (accept_a / accept_b / both / reject — same enums as
 * the backend), notes preserved on failure, re-decide supported (append-only
 * decision log + status overwrite), no fake undo. */
import { useMemo, useState } from "react";
import { Link } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Scale, Video } from "lucide-react";
import type { Conflict, DecisionAction } from "@/lib/api-types";
import { getConflicts, postDecision } from "@/lib/api";
import { qk } from "@/lib/queries";
import { errMessage } from "@/app/query-utils";
import { fmtDate } from "@/lib/time";
import { cn } from "@/lib/cn";
import { PageHeader, EmptyState, ErrorState, ListSkeleton, RefreshWarning } from "@/components/shared/states";
import { ConflictStatusBadge } from "@/components/shared/status-badge";
import { Tabs } from "@/components/ui/tabs";
import { useToast } from "@/components/shared/toaster";

const ACTIONS: { action: DecisionAction; label: string; tone: string }[] = [
  { action: "accept_a", label: "采纳 A", tone: "default" },
  { action: "accept_b", label: "采纳 B", tone: "default" },
  { action: "both", label: "两者各适用", tone: "outline" },
  { action: "reject", label: "都不采", tone: "outline" },
];

const CTYPE_LABEL: Record<string, string> = {
  numeric_conflict: "数值冲突",
  method_conflict: "方法冲突",
  polarity_conflict: "立场冲突",
  scope_conflict: "适用范围冲突",
};

export default function ConflictsPage() {
  const { data, error, isError, isPending, refetch, isFetching, dataUpdatedAt } = useQuery({
    queryKey: qk.conflicts,
    queryFn: ({ signal }) => getConflicts(signal),
  });
  const [filter, setFilter] = useState<"open" | "decided" | "all">("open");

  const conflicts = useMemo(() => data?.conflicts ?? [], [data]);
  const filtered = useMemo(() => {
    if (filter === "open")
      return conflicts.filter((c) => !c.status.startsWith("decided:"));
    if (filter === "decided") return conflicts.filter((c) => c.status.startsWith("decided:"));
    return conflicts;
  }, [conflicts, filter]);

  const open = conflicts.filter((c) => !c.status.startsWith("decided:")).length;

  return (
    <div className="mx-auto w-full max-w-[980px] px-4 py-5 md:px-6">
      <PageHeader
        title="争议复核"
        desc="规则与模型只产生候选;最终决策由你做出,且不会被 AI 覆盖。"
        actions={
          <Tabs
            ariaLabel="筛选争议"
            value={filter}
            onChange={setFilter}
            items={[
              { value: "open", label: `待复核 ${open}` },
              { value: "decided", label: `已决策 ${conflicts.length - open}` },
              { value: "all", label: "全部" },
            ]}
          />
        }
      />

      {isPending ? (
        <ListSkeleton rows={5} />
      ) : isError ? (
        <ErrorState error={error} onRetry={() => void refetch()} context="争议列表" />
      ) : (
        <>
          {isFetching && dataUpdatedAt > 0 ? <RefreshWarning onRetry={() => void refetch()} /> : null}
          {filtered.length === 0 ? (
            <EmptyState
              icon={<Scale size={30} />}
              title={filter === "open" ? "没有待复核的争议" : "没有匹配的争议"}
              desc={filter === "open" ? "运行 `reno judge` 重新生成聚类与冲突,或先导入更多视频。" : undefined}
            />
          ) : (
            <div className="space-y-3.5">
              {filtered.map((c) => (
                <ConflictCard key={c.conflict_id} conflict={c} />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function ConflictCard({ conflict }: { conflict: Conflict }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [note, setNote] = useState("");
  const [editing, setEditing] = useState(false);
  const decided = conflict.status.startsWith("decided:");

  const decide = useMutation({
    mutationFn: (action: DecisionAction) =>
      postDecision({ conflict_id: conflict.conflict_id, action, note: note.trim() || undefined }),
    onSuccess: (_d, action) => {
      setEditing(false);
      void qc.invalidateQueries({ queryKey: qk.conflicts });
      toast.success(`已记录决策:${ACTIONS.find((a) => a.action === action)?.label ?? action}`);
    },
    // failure feedback stays INLINE at the action site (§8.3: one primary
    // surface — no duplicated toast for the same failure)
  });

  const cc = conflict.analysis?.conditional_conclusion;

  return (
    <article
      className={cn(
        "rounded-panel border bg-surface p-4",
        decided && !editing ? "border-line opacity-[0.72]" : "border-line",
      )}
      aria-label={`争议 ${conflict.conflict_id}`}
    >
      <header className="mb-3 flex flex-wrap items-center gap-2">
        <span className="font-mono text-[11.5px] text-muted">{conflict.conflict_id}</span>
        <span className="rounded-full border border-line bg-surface-2 px-2 py-0.5 text-[11.5px] text-ink-2">
          {CTYPE_LABEL[conflict.ctype] ?? conflict.ctype}
        </span>
        <ConflictStatusBadge status={conflict.status} />
        <span className="ml-auto font-mono text-[11px] text-muted">{fmtDate(conflict.created_at)}</span>
      </header>

      <div className="grid gap-2.5 md:grid-cols-[1fr_auto_1fr] md:items-stretch">
        <Side side={conflict.side_a} tag="A" />
        <div className="hidden items-center md:flex" aria-hidden>
          <span className="rounded-full border border-line bg-surface-2 px-2 py-0.5 text-[11px] font-bold text-muted">
            VS
          </span>
        </div>
        <Side side={conflict.side_b} tag="B" />
      </div>

      {(cc || conflict.analysis?.judge_note) && (
        <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-ctl bg-surface-2/60 px-3 py-2 text-[12px] text-muted">
          {cc ? (
            <>
              <span>条件重叠:{cc.condition_overlap ? "是" : "否"}</span>
              {cc.scope_split ? <span>范围切分:{cc.scope_split}</span> : null}
              {cc.authority_gap ? <span>权威差异:{cc.authority_gap}</span> : null}
            </>
          ) : null}
          {conflict.analysis?.judge_note ? (
            <span className="min-w-0 flex-1 truncate" title={conflict.analysis.judge_note}>
              判读:{conflict.analysis.judge_note}
            </span>
          ) : null}
        </div>
      )}

      {decided && !editing ? (
        <div className="mt-3 flex items-center gap-3">
          <p className="text-[12.5px] text-muted">决策已记录(决策日志只增不改)。</p>
          <button
            onClick={() => setEditing(true)}
            className="cursor-pointer rounded-ctl border border-line-2 bg-surface px-2.5 py-1 text-[12.5px] font-medium text-ink-2 hover:border-acc hover:text-acc"
          >
            修改决定
          </button>
        </div>
      ) : (
        <>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            {ACTIONS.map(({ action, label, tone }) => (
              <button
                key={action}
                disabled={decide.isPending}
                onClick={() => decide.mutate(action)}
                className={cn(
                  "inline-flex cursor-pointer items-center gap-1.5 rounded-ctl px-3.5 py-1.5 text-[13px] font-medium transition-colors duration-150 disabled:opacity-50",
                  tone === "default"
                    ? "bg-acc text-white hover:bg-acc-strong dark:text-acc-contrast"
                    : "border border-line-2 bg-surface text-ink-2 hover:border-acc hover:text-acc",
                )}
              >
                {decide.isPending && decide.variables === action ? (
                  <Loader2 size={13} className="animate-spin" />
                ) : null}
                {label}
              </button>
            ))}
            {decided && editing ? (
              <button
                onClick={() => setEditing(false)}
                className="cursor-pointer rounded-ctl px-2.5 py-1.5 text-[12.5px] text-muted hover:text-ink"
              >
                取消
              </button>
            ) : null}
            <input
              aria-label="决策备注(可选)"
              placeholder="备注(可选,随决策入档)"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              maxLength={500}
              className="min-w-[180px] flex-1 rounded-ctl border border-line-2 bg-surface px-3 py-1.5 text-[13px] text-ink placeholder:text-muted/80 focus:border-acc focus:outline-none"
            />
          </div>
          {decide.isError ? (
            <p role="alert" className="mt-1.5 text-[12px] text-st-bad">
              提交失败({errMessage(decide.error)}),输入已保留,可直接重试。
            </p>
          ) : null}
        </>
      )}
    </article>
  );
}

function Side({ side, tag }: { side: Conflict["side_a"]; tag: string }) {
  return (
    <div className="rounded-ctl border border-line bg-surface-2/40 p-3">
      <div className="mb-1 flex items-center gap-2">
        <span
          className={cn(
            "flex h-5 w-5 items-center justify-center rounded-full text-[11px] font-bold",
            tag === "A" ? "bg-asr-bg text-asr" : "bg-ocr-bg text-ocr",
          )}
        >
          {tag}
        </span>
        {side.video ? (
          <Link
            to={`/videos/${side.video}`}
            className="inline-flex items-center gap-1 font-mono text-[11px] text-muted hover:text-acc"
            title="打开来源视频"
          >
            <Video size={11} /> {side.video}
          </Link>
        ) : null}
        {side.conditions?.space ? (
          <span className="rounded-full border border-line bg-surface px-2 py-0.5 text-[10.5px] text-muted">
            {side.conditions.space}
          </span>
        ) : null}
      </div>
      <p className="text-[13.5px] leading-relaxed text-ink">{side.claim}</p>
    </div>
  );
}
