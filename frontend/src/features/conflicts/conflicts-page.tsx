/** Conflict review: two-column side-by-side (stacked on phones), real
 * four-action semantics (accept_a / accept_b / both / reject — same enums as
 * the backend), notes preserved on failure, re-decide supported (append-only
 * decision log + status overwrite), no fake undo. */
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, RefreshCw, Scale, Video } from "lucide-react";
import type { Conflict, DecisionAction } from "@/lib/api-types";
import { getConflicts, postDecision } from "@/lib/api";
import { qk } from "@/lib/queries";
import { errMessage } from "@/app/query-utils";
import { fmtDate } from "@/lib/time";
import { cn } from "@/lib/cn";
import { PageHeader, EmptyState, ErrorState, ListSkeleton, RefreshWarning } from "@/components/shared/states";
import { ConflictStatusBadge } from "@/components/shared/status-badge";
import { Badge } from "@/components/ui/badge";
import { Tabs } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { useToast } from "@/components/shared/toaster";
import { taskCapabilitiesQuery, taskKeys, taskRequestId, useCreateTask, useMaintenanceTasks, isActiveTask } from "@/lib/task-api";
import { TaskStatusPanel } from "@/features/collect/task-status-panel";
import { ReviewHistory } from "@/components/shared/review-controls";
import { DerivationBadge, DerivationNotice } from "@/components/shared/derivation-notice";
import { normalizeDerivation } from "@/lib/derivation";

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
  const capabilities = useQuery(taskCapabilitiesQuery);
  const jobs = useMaintenanceTasks();
  const refresh = useCreateTask("judge");
  const judging = jobs.data?.tasks.some((task) => task.kind === "judge" && isActiveTask(task));

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

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Button variant="outline" disabled={!capabilities.data?.tasks || refresh.isPending || judging}
          onClick={() => refresh.mutate()}>
          {refresh.isPending || judging ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
          {judging ? "争议更新中" : "更新争议"}
        </Button>
        <p className="text-[12px] text-muted">按当前知识重新识别争议，可能消耗模型配额或产生费用；已有决定保留在历史中。</p>
      </div>
      {capabilities.data?.tasks === false ? <p className="mb-3 text-[13px] text-st-wait">本节点暂不能更新争议；{capabilities.data.node_role === "cloud" ? "请到家庭节点执行，完成后通过同步发布更新结果。" : "可继续查看已有结果。"}</p> : null}
      {capabilities.isError ? <ErrorState error={capabilities.error} context="任务能力" onRetry={() => void capabilities.refetch()} /> : null}
      {refresh.isPending ? <p role="status" className="mb-3 text-[13px] text-muted">正在提交更新请求，尚未确认入队。</p> : null}
      {refresh.isError ? <p role="alert" className="mb-3 text-[13px] text-st-bad">更新请求尚未确认：{errMessage(refresh.error)}；再次点击可核对同一请求。</p> : null}
      <TaskStatusPanel kind="judge" limit={2} hideWhenEmpty />
      <div className="mb-4 space-y-2">
        <ReviewHistory label="复核历史与待核对记录" />
        <Link to="/reconciliation" className="inline-flex min-h-9 items-center text-[13px] text-acc hover:underline">查看复核对账与导出</Link>
      </div>

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
              desc={filter === "open" ? "可点击「更新争议」重新分析已有知识，或先导入更多视频。" : undefined}
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
  // §11.2: select → note → explicit submit; a bare tap never writes
  const [decision, setDecision] = useState<DecisionAction | null>(null);
  const decided = conflict.status.startsWith("decided:");
  const pendingDecision = useRef<{ signature: string; eventId: string } | null>(null);
  useEffect(() => {
    setDecision(null);
    pendingDecision.current = null;
  }, [conflict.target_fingerprint]);

  const decide = useMutation({
    mutationFn: (action: DecisionAction) => {
      const signature = JSON.stringify({ action, note: note.trim(), fingerprint: conflict.target_fingerprint });
      if (pendingDecision.current?.signature !== signature)
        pendingDecision.current = { signature, eventId: taskRequestId() };
      return postDecision({ conflict_id: conflict.conflict_id, action, note: note.trim() || undefined,
        target_fingerprint: conflict.target_fingerprint, event_id: pendingDecision.current.eventId });
    },
    onSuccess: (_d, action) => {
      setEditing(false);
      setDecision(null);
      pendingDecision.current = null;
      void qc.invalidateQueries({ queryKey: qk.conflicts });
      void qc.invalidateQueries({ queryKey: ["reviews"] });
      void qc.invalidateQueries({ queryKey: taskKeys.reports });
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
        <Badge tone="neutral">{CTYPE_LABEL[conflict.ctype] ?? conflict.ctype}</Badge>
        <ConflictStatusBadge status={conflict.status} />
        <DerivationBadge derivation={conflict.derivation} />
        <span className="ml-auto font-mono text-[11px] text-muted">{fmtDate(conflict.created_at)}</span>
      </header>
      {conflict.review_stale ? <p className="mb-3 text-[12px] text-st-wait">依据内容已变化，旧决定保留在历史中；请重新核对当前双方观点。</p> : null}
      <div className="mb-3"><DerivationNotice derivation={conflict.derivation} /></div>

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
        <div className="mt-3 rounded-ctl bg-surface-2/60 px-3 py-2 text-[12px] leading-relaxed text-muted">
          {cc ? (
            <div className="flex flex-wrap gap-x-3 gap-y-0.5">
              <span>条件重叠:{cc.condition_overlap ? "是" : "否"}</span>
              {cc.scope_split ? <span className="min-w-0 break-words">范围切分:{cc.scope_split}</span> : null}
              {cc.authority_gap ? <span className="min-w-0 break-words">权威差异:{cc.authority_gap}</span> : null}
            </div>
          ) : null}
          {conflict.analysis?.judge_note ? (
            <p className="mt-0.5 min-w-0 break-words">
              {normalizeDerivation(conflict.derivation).state === "active" ? "判读:" : "原分析:"}{conflict.analysis.judge_note}
            </p>
          ) : null}
        </div>
      )}

      {normalizeDerivation(conflict.derivation).state !== "active" ? <p className="mt-3 text-[12px] text-st-wait">仅记录个人意见，不会更新判定依据。原分析与复核历史仍保留。</p> : null}
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
            {ACTIONS.map(({ action, label, tone }) => {
              const selected = decision === action;
              return (
                <Button
                  key={action}
                  variant={selected ? "default" : tone === "default" ? "soft" : "outline"}
                  size="md"
                  disabled={decide.isPending || !conflict.target_fingerprint}
                  aria-pressed={selected}
                  onClick={() => setDecision(action)}
                >
                  {label}
                </Button>
              );
            })}
            {decided && editing ? (
              <Button
                variant="ghost"
                size="md"
                onClick={() => {
                  setEditing(false);
                  setDecision(null);
                }}
              >
                取消
              </Button>
            ) : null}
            <Input
              aria-label="决策备注(可选)"
              placeholder="备注(可选,随决策入档)"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              maxLength={500}
              className="min-w-[180px] flex-1 max-md:h-11"
            />
          </div>
          <div className="mt-2.5 flex items-center gap-3">
            <Button
              size="md"
              disabled={decision == null || decide.isPending || !conflict.target_fingerprint}
              onClick={() => decision && decide.mutate(decision)}
            >
              {decide.isPending ? <Loader2 size={14} className="animate-spin" /> : null}
              {decide.isPending
                ? "提交中…"
                : decision
                  ? `提交决策:${ACTIONS.find((a) => a.action === decision)?.label}`
                  : "先选择一个决定"}
            </Button>
            <span className="text-[11.5px] text-muted">提交前可修改选择与备注。</span>
          </div>
          {decide.isError ? (
            <p role="alert" className="mt-1.5 text-[12px] text-st-bad">
              提交失败({errMessage(decide.error)}),选择与备注已保留,可直接重试。
            </p>
          ) : null}
          {!conflict.target_fingerprint ? <p className="mt-2 text-[12px] text-muted">当前内容标识尚未确认，请刷新后再保存决定。</p> : null}
        </>
      )}
      <ReviewHistory kind="conflict" targetId={conflict.conflict_id} />
    </article>
  );
}

function Side({ side, tag }: { side: Conflict["side_a"]; tag: string }) {
  return (
    <div className="rounded-ctl border border-line bg-surface-2/40 p-3">
      <div className="mb-1 flex items-center gap-2">
        <span
          className={cn(
            "flex h-5 w-5 items-center justify-center rounded-full border text-[11px] font-bold",
            // side identity, NOT evidence modality: asr/ocr colors are
            // reserved for evidence source and must not mark A/B立场
            tag === "A"
              ? "border-acc-line bg-acc-soft text-acc"
              : "border-line bg-surface-2 text-ink-2",
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
