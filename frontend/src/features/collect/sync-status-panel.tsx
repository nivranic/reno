import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { ArrowUpRight, ChevronLeft, ChevronRight, RefreshCw } from "lucide-react";
import { Badge, type Tone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState, RefreshWarning } from "@/components/shared/states";
import { taskDate } from "@/lib/task-api";
import { syncStatusQuery, type HomeSyncStatus, type SyncQueueItem, type SyncStatus } from "@/lib/sync-api";

const HOME_STATES: Record<string, { label: string; desc: string; tone: Tone }> = {
  disabled: { label: "同步已关闭", desc: "当前只使用本地知识库，尚未启用家庭同步。", tone: "neutral" },
  unconfigured: { label: "同步尚未配置完整", desc: "请在家庭节点完成同步目标与配对配置后再运行。", tone: "wait" },
  not_started: { label: "首次同步尚未运行", desc: "配置已就绪，等待家庭同步任务首次运行。", tone: "wait" },
  state_error: { label: "同步状态不可读取", desc: "无法确认本地同步进度，请检查家庭节点的状态文件。", tone: "bad" },
  idle: { label: "等待下一轮同步", desc: "当前轮次已结束，将按计划检查新链接与复核记录。", tone: "neutral" },
  pulling: { label: "正在拉取复核记录", desc: "先同步已有复核，再处理新的采集链接。", tone: "run" },
  pushing_reviews: { label: "正在发送复核记录", desc: "正在将本地复核结果同步到云端。", tone: "run" },
  processing: { label: "正在处理视频", desc: "下载与知识提取在家庭节点执行，尚未确认云端完成。", tone: "run" },
  uploading: { label: "正在上传媒体", desc: "媒体上传后还需等待云端确认整个批次。", tone: "run" },
  publishing: { label: "正在等待云端确认", desc: "批次已提交，收到有效回执后才会确认完成。", tone: "run" },
  published: { label: "云端已确认发布", desc: "本批次已取得云端发布回执。", tone: "ok" },
  waiting_lease: { label: "等待同步租约", desc: "正在等待可用的同步租约，将按计划再次尝试。", tone: "wait" },
  retry_wait: { label: "等待再次同步", desc: "本轮未能完成，将保留已有进度并按计划重试。", tone: "wait" },
  review_backlog: { label: "等待继续同步复核记录", desc: "本轮已保存同步位置，将在下一轮继续剩余复核记录。", tone: "wait" },
  budget_wait: { label: "采集等待放行", desc: "原采集任务与已有进度已保留，等待预算或处理条件允许后继续。", tone: "wait" },
  pending_delivery: { label: "等待云端确认采集状态", desc: "家庭端已保留采集状态记录，正在等待云端确认原记录。", tone: "wait" },
  source_blocked: { label: "来源任务等待处理", desc: "原来源任务与已有进度已保留，请在家庭节点核对处理条件后继续。", tone: "wait" },
  source_failed: { label: "来源任务已确认失败", desc: "原来源任务已取得失败终态，失败记录与已保存结果保留。", tone: "bad" },
  recovery_held: { label: "恢复后知识同步已暂停", desc: "云端正在保留恢复后的知识。复核记录继续同步，采集与知识发布等待明确恢复。", tone: "wait" },
  recovery_resuming: { label: "正在恢复知识同步", desc: "家庭节点将完成已保存的处理进度，再重新发布完整知识；收到云端确认后恢复领取任务。", tone: "run" },
  recovery_blocked: { label: "恢复同步需要处理", desc: "已保留原有进度。请在家庭节点核对待处理问题，完整知识发布成功前不会领取新任务。", tone: "wait" },
};
const RECORDED_SESSION_STATES = new Set(["idle", "pulling", "pushing_reviews", "processing", "uploading", "publishing", "published", "review_backlog"]);
const UNKNOWN = { label: "同步状态待核对", desc: "暂时无法识别当前状态，请刷新或检查家庭节点。", tone: "wait" as const };
const PHASES = { downloaded: "下载已保存", processed: "知识提取已完成", judged: "跨视频判定已完成", reported: "报告已生成" };
const ERROR_TEXT: Record<string, string> = {
  SYNC_CLIENT_NETWORK: "连接云端失败，请检查家庭节点网络。",
  SYNC_CLIENT_TIMEOUT: "云端响应超时，等待下次同步。",
  SYNC_CLIENT_AUTH: "云端未通过同步身份校验，请检查家庭节点配置。",
  SYNC_CLIENT_CREDENTIAL: "同步凭据尚不可用，请检查家庭节点配置。",
  SYNC_PEER_MISMATCH: "同步目标身份不一致，请核对节点配对。",
  INCOMPATIBLE_SYNC_SCHEMA: "两端同步版本不兼容，请核对升级与配对状态。",
  SYNC_LEASE_EXPIRED: "同步租约已过期，等待重新取得租约。",
  SYNC_SESSION_BUSY: "同步会话正在使用中，等待可用租约。",
  SYNC_LEASE_MISMATCH: "同步租约尚未确认，等待再次核对。",
  SYNC_CANCELLED: "本轮同步已停止，已有进度保留。",
  SYNC_PROCESSING_FAILED: "本轮处理未完成，请在家庭节点检查处理记录。",
  SYNC_RECOVERY_HELD: "云端保留恢复后的知识，等待本机管理员明确恢复同步。",
  SYNC_RECOVERY_GENERATION_MISMATCH: "云端恢复状态已更新，等待重新核对后再发送。",
  SYNC_RECOVERY_STREAM_MISMATCH: "云端知识库已经恢复到新的代次，等待重新核对并完整同步。",
  SYNC_RECOVERY_FRESH_PUBLICATION_REQUIRED: "需要先重新发布完整知识，确认后才能继续采集任务。",
  SYNC_RECOVERY_SOURCE_EPOCH_BEHIND: "家庭知识库版本落后于云端保留记录，请先核对家庭库恢复状态。",
  SYNC_RECOVERY_LOCAL_WORK_INVALID: "已保存的处理进度缺少可核验来源，请在家庭节点核对下载文件。",
  SYNC_SOURCE_EXECUTION_BLOCKED: "来源任务暂时不能继续，原任务与已保存进度保留，请在家庭节点核对。",
  SYNC_BUDGET_DELIVERY_PENDING: "采集等待或恢复记录尚未取得云端确认，已有记录保留。",
  SYNC_SOURCE_CONTROL_DELIVERY_PENDING: "来源状态尚未取得云端确认，原记录已保留。",
  SYNC_SOURCE_FAILED: "来源任务已确认失败，原结果与历史记录保留。",
};
const QUEUE_STATES: Record<string, { label: string; tone: Tone }> = {
  queued: { label: "等待家庭节点领取", tone: "neutral" },
  claimed: { label: "已领取，尚未确认完成", tone: "run" },
  retry_wait: { label: "等待重试", tone: "wait" },
  budget_wait: { label: "等待采集条件允许后继续", tone: "wait" },
  dead: { label: "暂时无法完成", tone: "bad" },
  done: { label: "云端已确认完成", tone: "ok" },
};
const QUEUE_ERRORS: Record<string, string> = {
  network: "网络连接异常", rate_limited: "平台暂时限流", quota: "处理额度暂不可用",
  processing_failed: "家庭节点处理未完成", invalid_url: "来源链接无法验证", source_unavailable: "来源暂不可访问",
  media_invalid: "媒体校验未通过", content_invalid: "发布内容校验未通过", auth_failed: "来源身份校验未通过",
  lease_expired: "领取租约已过期",
};

function HomeSummary({ home, stale }: { home: HomeSyncStatus; stale: boolean }) {
  const status = HOME_STATES[home.state] ?? UNKNOWN;
  const expired = RECORDED_SESSION_STATES.has(home.state) && !home.lease_valid;
  return <div>
    <Badge tone={stale || expired ? "neutral" : status.tone}>{stale ? "上次状态：" : expired ? "上次记录：" : ""}{status.label}</Badge>
    {expired ? <p className="mt-2 text-[12.5px] text-st-wait">上次同步会话已失效，等待重新确认家庭节点状态。</p> : null}
    <p className="mt-2 text-[13px] leading-relaxed text-ink-2">{status.desc}</p>
    {home.error_code ? <p className="mt-2 text-[12.5px] text-st-wait">
      {ERROR_TEXT[home.error_code] ?? "同步遇到暂无法识别的问题，请在家庭节点检查后再试。"}
    </p> : null}
    <dl className="mt-3 grid gap-x-5 gap-y-2 text-[12px] sm:grid-cols-2">
      <div><dt className="text-muted">最近同步成功</dt><dd className="mt-0.5 text-ink-2">{home.last_success_at ? taskDate(home.last_success_at) : "暂无成功记录"}</dd></div>
      <div><dt className="text-muted">家庭端状态更新</dt><dd className="mt-0.5 text-ink-2">{taskDate(home.updated_at)}</dd></div>
      {home.retry_at ? <div><dt className="text-muted">计划再次尝试</dt><dd className="mt-0.5 text-ink-2">{taskDate(home.retry_at)}</dd></div> : null}
      {home.work_phase ? <div><dt className="text-muted">已保存进度</dt><dd className="mt-0.5 text-ink-2">{PHASES[home.work_phase] ?? "进度待核对"}</dd></div> : null}
    </dl>
    {home.has_pending_publication ? <p className="mt-3 rounded-ctl border border-st-wait-line bg-st-wait-bg px-3 py-2 text-[12.5px] text-st-wait">
      {home.state === "recovery_held" || home.state === "recovery_blocked"
        ? "原待发批次与处理进度已保留；暂停期间不会用它覆盖恢复后的知识。"
        : "有待发送批次：已保存在本地，收到云端回执后才确认完成。"}
    </p> : null}
  </div>;
}

function QueueRow({ item }: { item: SyncQueueItem }) {
  const status = QUEUE_STATES[item.status] ?? { label: "队列状态待核对", tone: "wait" as const };
  const source = [item.source_host, item.source_path].filter(Boolean).join("") || "来源信息待核对";
  return <li className="min-w-0 rounded-ctl border border-line bg-surface-2/30 p-3">
    <div className="flex flex-wrap items-center gap-2">
      <span className="font-mono text-[11px] text-muted">#{item.item_id}</span>
      <Badge tone={status.tone}>{status.label}</Badge>
      {item.status === "done" && item.can_view_video && item.video_id ?
        <Link to={`/videos/${encodeURIComponent(item.video_id)}`} className="ml-auto inline-flex min-h-8 items-center gap-1 text-[12.5px] text-acc hover:underline">
          查看视频 <ArrowUpRight size={13} />
        </Link> : null}
    </div>
    <p className="mt-2 truncate text-[12.5px] text-ink-2" title={source}>{source}</p>
    <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[11.5px] text-muted">
      <span>更新于 {taskDate(item.updated_at)}</span><span>已尝试 {item.attempts} 次</span>
      {item.retry_at && item.status === "retry_wait" ? <span>计划重试 {taskDate(item.retry_at)}</span> : null}
      {item.retry_at && item.status === "budget_wait" ? <span>计划继续 {taskDate(item.retry_at)}</span> : null}
    </div>
    {item.status === "claimed" && item.claim_active === false ? <p className="mt-1.5 text-[12px] text-st-wait">领取已失效，等待重新领取；尚未确认完成。</p> : null}
    {item.status === "done" && !item.can_view_video ? <p className="mt-1.5 text-[12px] text-muted">视频暂不可查看。</p> : null}
    {item.status === "budget_wait" && !item.retry_at ? <p className="mt-1.5 text-[12px] text-st-wait">尚无计划继续时间，等待家庭节点核对。</p> : null}
    {item.last_error_class ? <p className="mt-1.5 text-[12px] text-st-wait">{QUEUE_ERRORS[item.last_error_class] ?? "原因暂无法识别，请检查家庭节点状态。"}</p> : null}
  </li>;
}

function CloudSummary({ data, stale }: { data: SyncStatus; stale: boolean }) {
  const label = !data.configured ? "云端同步尚未开放" : !data.paired ? "家庭节点尚未配对" : !data.compatible ? "两端同步版本待核对" : data.online ? "家庭节点在线" : "家庭节点离线";
  const stats = [["queued", "等待领取"], ["claimed", "已领取"], ["retry_wait", "等待重试"], ["budget_wait", "预算等待"], ["dead", "未能完成"], ["done", "已确认完成"]] as const;
  return <>
    <div className="flex flex-wrap items-center gap-2">
      <Badge tone={stale ? "neutral" : data.online ? "ok" : "wait"}>{stale ? "上次状态：" : ""}{label}</Badge>
      <span className="text-[11.5px] text-muted">最近联系 {taskDate(data.last_seen)}</span>
    </div>
    <p className="mt-2 text-[12.5px] leading-relaxed text-ink-2">云端接收链接，家庭节点负责下载和处理。已领取不等于完成，以云端发布回执为准。</p>
    {data.recovery?.state === "held" || data.recovery?.state === "resume_pending" ?
      <div className="mt-3 rounded-ctl border border-st-wait-line bg-st-wait-bg px-3 py-2.5 text-[12.5px] text-st-wait">
        <p className="font-medium">{stale ? "上次记录：" : ""}{data.recovery.state === "held" ? "恢复后知识同步已暂停" : "等待家庭节点重新发布完整知识"}</p>
        <p className="mt-1 leading-relaxed">{data.recovery.state === "held"
          ? "复核记录仍可同步，已有队列和处理进度保留。请在云端本机核对恢复结果，再明确恢复同步。"
          : "恢复后的知识暂时保留；新批次确认完成后，才会恢复领取采集任务。"}</p>
      </div> : null}
    <dl className="my-3 grid grid-cols-2 gap-2 sm:grid-cols-3">
      {stats.map(([key, label]) => <div key={key} className="rounded-ctl bg-surface-2/70 px-3 py-2">
        <dt className="text-[11.5px] text-muted">{label}</dt><dd className="mt-1 font-mono text-lg font-semibold text-ink">{data.counts[key] ?? 0}</dd>
      </div>)}
    </dl>
    {data.expired_claims > 0 || (data.counts.unknown ?? 0) > 0 ? <p className="mb-3 text-[12px] text-st-wait">
      {data.expired_claims > 0 ? `${data.expired_claims} 项领取已失效，等待重新领取。` : ""}
      {(data.counts.unknown ?? 0) > 0 ? ` ${data.counts.unknown} 项状态待核对。` : ""}
    </p> : null}
    {data.queue.items.length === 0 ? <EmptyState compact title="暂无采集链接" desc="通过导入或采集书签提交后，可以在这里查看领取与完成状态。" /> :
      <ul aria-label="采集队列" className="space-y-2">{data.queue.items.map((item) => <QueueRow key={item.item_id} item={item} />)}</ul>}
  </>;
}

export function SyncStatusPanel() {
  const [offset, setOffset] = useState(0);
  const query = useQuery(syncStatusQuery(offset));
  const data = query.data;
  const retry = () => void query.refetch();
  return <section className="mb-4 min-w-0 rounded-panel border border-line bg-surface p-4" aria-label="采集与同步状态">
    <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
      <h2 className="text-[14.5px] font-semibold text-ink">{data?.mode === "cloud" ? "云端采集队列" : "家庭同步"}</h2>
      <Button size="iconSm" variant="ghost" aria-label="刷新同步状态" disabled={query.isFetching} onClick={retry}>
        <RefreshCw size={14} className={query.isFetching ? "animate-spin" : ""} />
      </Button>
    </div>
    {query.isPending ? <div aria-label="正在读取同步状态"><Skeleton className="h-6 w-40" /><Skeleton className="mt-3 h-20 w-full" /></div> :
      !data ? <>
        <ErrorState error={query.error} context="同步状态" onRetry={retry} />
        {offset > 0 ? <Button className="mt-3" variant="outline" size="sm" aria-label="上一页采集队列" onClick={() => setOffset(Math.max(0, offset - 10))}><ChevronLeft size={14} />返回上一页</Button> : null}
      </> : <>
        {query.isError ? <RefreshWarning onRetry={retry} /> : null}
        {data.mode === "home" ? data.home ? <HomeSummary home={data.home} stale={query.isError} /> :
          <EmptyState compact title="同步状态待核对" desc="家庭端暂未返回可识别的同步摘要。" /> : <CloudSummary data={data} stale={query.isError} />}
        {data.mode === "cloud" && data.queue.total > 0 ? <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-line pt-3">
          <p className="text-[12px] text-muted">共 {data.queue.total} 条 · 第 {Math.floor(data.queue.offset / data.queue.limit) + 1} / {Math.max(1, Math.ceil(data.queue.total / data.queue.limit))} 页</p>
          <div className="flex gap-1.5">
            <Button variant="outline" size="sm" aria-label="上一页采集队列" disabled={data.queue.offset === 0 || query.isFetching} onClick={() => setOffset(Math.max(0, data.queue.offset - data.queue.limit))}><ChevronLeft size={14} />上一页</Button>
            <Button variant="outline" size="sm" aria-label="下一页采集队列" disabled={data.queue.offset + data.queue.limit >= data.queue.total || query.isFetching} onClick={() => setOffset(data.queue.offset + data.queue.limit)}>下一页<ChevronRight size={14} /></Button>
          </div>
        </div> : null}
        <p className="mt-3 text-[11px] text-muted">{query.isError ? "上次获取" : "状态获取于"} {taskDate(new Date(query.dataUpdatedAt).toISOString())} · 页面位于前台时自动刷新</p>
      </>}
  </section>;
}
