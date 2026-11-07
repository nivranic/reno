import { useRef, useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { Loader2, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Field, Input, Select } from "@/components/ui/field";
import { EmptyState, ErrorState, ListSkeleton, RefreshWarning } from "@/components/shared/states";
import { errMessage } from "@/app/query-utils";
import { budgetFields, budgetQuery, useHomeBudgetControl, useSaveBudget, waitReasonText,
  type BudgetField, type BudgetSettings, type BudgetValue } from "@/lib/budget-api";

const labels: Record<BudgetField, string> = { budget_enabled: "启用预算限制", budget_timezone: "自然日时区",
  download_interval_min_seconds: "下载最短间隔（秒）", download_interval_max_seconds: "下载最长间隔（秒）",
  download_daily_videos: "每日新视频名额", download_rate_bytes_per_second: "下载限速（字节/秒）",
  model_daily_attempts: "每日模型请求次数", model_reported_tokens_stop_at: "已报告总用量停止线（tokens）",
  model_daily_output_tokens: "每日输出额度（tokens）", model_total_tokens_mode: "总用量口径",
  model_operation_max_attempts: "每项处理最多尝试次数", source_resolution_max_attempts: "每项来源解析最多机会" };
const optionalFields = new Set<BudgetField>(["download_daily_videos", "download_rate_bytes_per_second",
  "model_daily_attempts", "model_reported_tokens_stop_at", "model_daily_output_tokens"]);
const cloudFields = new Set<BudgetField>(["budget_enabled", "budget_timezone", "model_daily_attempts",
  "model_reported_tokens_stop_at", "model_daily_output_tokens", "model_operation_max_attempts", "model_total_tokens_mode"]);
const sources = { default: "默认设置", local: "本机设置", environment: "环境变量覆盖" };
const numberText = (value: number | null) => value === null ? "未报告" : Number.isSafeInteger(value) ? value.toLocaleString("zh-CN") : "超过界面整数精度";
const stamp = (value: string | null, timezone?: string) => {
  if (value === null) return "尚无计划继续时间";
  const date = new Date(value);
  try { return date.toLocaleString("zh-CN", { hour12: false, timeZone: timezone }); }
  catch { return value; }
};

function SettingsForm({ settings, cloud, onReadLatest }: { settings: BudgetSettings; cloud: boolean; onReadLatest: () => void }) {
  const [draft, setDraft] = useState<Partial<Record<BudgetField, string>>>({});
  const [error, setError] = useState<string | null>(null);
  const revision = useRef<string | null>(null);
  const save = useSaveBudget();
  const dirty = Object.keys(draft).length > 0;
  const stale = dirty && revision.current !== settings.settings_revision;
  const value = (key: BudgetField) => (settings.fields[key].editable ? draft[key] : undefined) ?? String(settings.fields[key].value ?? "");
  const change = (key: BudgetField, next: string) => {
    revision.current ??= settings.settings_revision;
    setDraft((old) => ({ ...old, [key]: next }));
    setError(null);
  };
  const reset = () => { revision.current = null; setDraft({}); setError(null); save.reset(); };
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const updates: Partial<Record<BudgetField, BudgetValue>> = {};
    for (const key of budgetFields) {
      if (!(key in draft)) continue;
      const raw = draft[key]!.trim();
      if (key === "budget_enabled") updates[key] = raw === "true";
      else if (key === "budget_timezone") updates[key] = raw;
      else if (raw === "" && optionalFields.has(key)) updates[key] = null;
      else {
        const parsed = Number(raw);
        if (!/^\d+$/.test(raw) || !Number.isSafeInteger(parsed)) {
          setError(`${labels[key]}需要填写界面可表示的非负整数。`); return;
        }
        updates[key] = parsed;
      }
    }
    try {
      await save.mutateAsync({ expected_settings_revision: revision.current ?? settings.settings_revision, updates });
      reset();
    } catch { /* The mutation retains the controlled server error below. */ }
  };
  return <details className="mt-4 border-t border-line pt-3">
    <summary className="cursor-pointer text-[13px] font-medium text-ink">预算设置</summary>
    <form onSubmit={(event) => void submit(event)} className="mt-3 space-y-3">
      <p className="text-[12px] leading-relaxed text-muted">空白表示不设上限；次数与用量填 0 会阻止新的准入。已开始的操作保留原次数上限，已发出的请求继续保存结果。</p>
      <div className="grid gap-3 sm:grid-cols-2">
        {budgetFields.filter((key) => !cloud || cloudFields.has(key)).map((key) => {
          const field = settings.fields[key];
          const tooLarge = typeof field.value === "number" && !Number.isSafeInteger(field.value);
          const disabled = !field.editable || save.isPending || tooLarge;
          return <Field key={key} label={labels[key]} htmlFor={`budget-${key}`}
            hint={<>{sources[field.source]}{tooLarge ? " · 超出界面整数精度，请在本机配置维护" : !field.editable ? " · 只读" : ""}
              {key === "model_operation_max_attempts" ? cloud ? " · 用于本机模型请求" : " · 模型、下载与来源信息处理共用；来源解析另设上限" : ""}</>}>
            {key === "budget_enabled" ? <Select id={`budget-${key}`} value={value(key)} disabled={disabled}
              onChange={(event) => change(key, event.target.value)}><option value="false">关闭限制</option><option value="true">启用限制</option></Select> :
              key === "model_total_tokens_mode" ? <Input id={`budget-${key}`} value="仅平台已报告总量" disabled /> :
              <Input id={`budget-${key}`} value={tooLarge ? "" : value(key)} type="text"
                inputMode={key === "budget_timezone" ? "text" : "numeric"} disabled={disabled}
                placeholder={optionalFields.has(key) ? "不设上限" : key === "budget_timezone" ? "Asia/Shanghai" : undefined}
                onChange={(event) => change(key, event.target.value)} />}
          </Field>;
        })}
      </div>
      {stale ? <p role="alert" className="text-[12px] text-st-wait">服务器设置已有更新，当前填写内容保留。请先读取最新设置再保存。</p> : null}
      {error || save.isError ? <p role="alert" className="text-[12px] text-st-bad">{error ?? errMessage(save.error)}；可刷新核对实际有效值。</p> : null}
      <div className="flex flex-wrap items-center gap-2">
        <Button type="submit" size="md" disabled={!dirty || stale || save.isPending}>
          {save.isPending ? <Loader2 size={14} className="animate-spin" /> : null}保存预算设置
        </Button>
        <Button type="button" size="md" variant="outline" disabled={save.isPending} onClick={() => { reset(); onReadLatest(); }}>读取最新设置</Button>
      </div>
    </form>
  </details>;
}

export function BudgetPanel() {
  const query = useQuery(budgetQuery);
  const control = useHomeBudgetControl();
  const data = query.data;
  const act = (action: "pause" | "resume") => {
    if (data?.home.control_revision) control.mutate({ action, revision: data.home.control_revision });
  };
  return <section className="mb-4 rounded-panel border border-line bg-surface p-4" aria-label="预算与等待任务">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h2 className="text-[14.5px] font-semibold text-ink">预算与等待任务</h2>
      <Button size="iconSm" variant="ghost" aria-label="刷新预算" onClick={() => void query.refetch()}>
        <RefreshCw size={14} className={query.isFetching ? "animate-spin" : ""} />
      </Button>
    </div>
    {query.isPending ? <ListSkeleton rows={3} /> : !data ? <ErrorState error={query.error} context="预算账本" onRetry={() => void query.refetch()} /> : <>
      {query.isError ? <RefreshWarning onRetry={() => void query.refetch()} /> : null}
      <div className="mt-3 flex flex-wrap items-center gap-2 text-[12px]">
        <Badge tone={data.settings.effective_policy.budget_enabled ? "ok" : "neutral"}>
          {data.settings.effective_policy.budget_enabled ? "预算限制已启用" : "预算限制未启用"}
        </Badge>
        <span className="text-muted">本机账本 · {data.usage.window.local_date} · {data.usage.window.timezone}</span>
      </div>
      <p className="mt-2 text-[12px] leading-relaxed text-muted">{data.capabilities.node_role === "cloud" ? "这里只维护当前云节点的模型预算，家庭采集预算请到家庭节点查看。" : "预留、进行中与未知结果都会占用对应额度。暂停阻止后续请求，已发出的请求会继续保存结果。"}</p>
      <p className="mt-1 text-[11px] text-muted">本自然日结束：{stamp(data.usage.window.end_utc, data.usage.window.timezone)}</p>
      <div className="mt-3 grid gap-2 sm:grid-cols-2">
        {([ ["model", "模型请求"], ["download", "下载进程"], ["source_resolution", "来源解析"],
          ["source_metadata", "来源信息"], ["source_enumeration", "收藏列表"] ] as const).map(([kind, title]) => {
          const row = data.usage.attempts[kind];
          return <div key={kind} className="rounded-ctl bg-surface-2/40 px-3 py-2.5 text-[12px]">
            <p className="font-medium text-ink">{title}机会：{numberText(row.admissions)}</p>
            <dl className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-ink-2">
              <div><dt className="inline">已返回结果 </dt><dd className="inline tabular-nums">{numberText(row.confirmed)}</dd></div>
              <div><dt className="inline">未发出预留 </dt><dd className="inline tabular-nums">{numberText(row.held)}</dd></div>
              <div><dt className="inline">进行中 </dt><dd className="inline tabular-nums">{numberText(row.in_flight)}</dd></div>
              <div><dt className="inline">结果未知 </dt><dd className="inline tabular-nums">{numberText(row.unknown)}</dd></div>
            </dl>
          </div>;
        })}
        <div className="rounded-ctl bg-surface-2/40 px-3 py-2.5 text-[12px]">
          <p className="font-medium text-ink">新视频名额占用：{numberText(data.usage.download_logical_admissions)}</p>
          <p className="mt-1 text-muted">同一视频的音频与下载重试另计进程机会。</p>
        </div>
      </div>
      <div className="mt-4 text-[12px]">
        <h3 className="font-medium text-ink">模型已报告用量（tokens）</h3>
        <dl className="mt-2 grid gap-2 sm:grid-cols-3">
          {([ ["input_tokens", "输入"], ["output_tokens", "输出"], ["total_tokens", "总量"] ] as const).map(([key, title]) => {
            const value = data.usage.tokens[key];
            return <div key={key}><dt className="text-muted">{title}</dt><dd className="mt-1 font-mono text-ink">{numberText(value.reported)}</dd>
              <p className="mt-1 text-[11px] text-muted">已报告覆盖 {value.covered_attempts}/{value.dispatched_attempts} 次，缺失 {value.missing_attempts} 次</p></div>;
          })}
        </dl>
        <p className="mt-2 text-muted">部分报告 {data.usage.tokens.quality_counts.partial} 次；无用量报告 {data.usage.tokens.quality_counts.missing} 次；无效字段 {data.usage.tokens.quality_counts.invalid} 次；尚无结果 {data.usage.tokens.quality_counts.unobserved} 次。</p>
        <p className="mt-1 text-muted">总量仅采用平台报告，缺失不会按 0 处理；账本启用前的历史用量未知。</p>
        <h3 className="mt-3 font-medium text-ink">输出额度保守占用：{numberText(data.usage.conservative_output_tokens.total)}</h3>
        <p className="mt-1 leading-relaxed text-muted">未发出预留 {numberText(data.usage.conservative_output_tokens.held)} · 进行中 {numberText(data.usage.conservative_output_tokens.in_flight)} · 结果未知 {numberText(data.usage.conservative_output_tokens.unknown)} · 已返回但缺少输出报告 {numberText(data.usage.conservative_output_tokens.confirmed_missing_output)}</p>
      </div>
      <div className="mt-4 border-t border-line pt-3 text-[12px]">
        <h3 className="font-medium text-ink">等待操作（全部历史）</h3>
        <p className="mt-1 text-muted">等待 {data.usage.waiting.total} 项 · 未知结果 {data.usage.all_history.unknown_attempts} 次</p>
        {data.usage.waiting.operations.length === 0 ? <EmptyState compact title="暂无预算等待操作" desc="等待记录按实际任务条件显示。" /> :
          <ul className="mt-2 space-y-2">{data.usage.waiting.operations.map((row) => <li key={row.operation_id} className="rounded-ctl bg-surface-2/40 px-3 py-2">
            <p className="text-ink-2">{waitReasonText(row.reason)}{row.root_job_id === null ? "" : ` · 任务 ${row.root_job_id}`}</p>
            <p className="mt-1 text-muted">{row.resume_at === null ? "尚无计划继续时间，等待条件核对。" : `计划继续 ${stamp(row.resume_at)}`}</p>
          </li>)}</ul>}
        {data.usage.waiting.total > data.usage.waiting.operations.length ? <p className="mt-2 text-muted">当前显示前 {data.usage.waiting.limit} 项。</p> : null}
      </div>
      {data.capabilities.home_control && data.home.task ? <div className="mt-3 rounded-ctl border border-line p-3 text-[12px]">
        <h3 className="font-medium text-ink">家庭采集任务 {data.home.task.job_id}</h3>
        <p className="mt-1 text-muted">{data.home.task.manual_paused ? "已手动暂停" : data.home.task.wait_mode !== "none" ? waitReasonText(data.home.task.blocked_reason) : "处理任务已保留"}</p>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {data.home.task.can_pause ? <Button size="sm" variant="outline" disabled={control.isPending} onClick={() => act("pause")}>暂停家庭采集</Button> : null}
          {data.home.task.can_resume ? <Button size="sm" variant="outline" disabled={control.isPending} onClick={() => act("resume")}>请求继续家庭采集</Button> : null}
        </div>
        <p className="mt-2 text-muted">继续请求会再次核对当前条件；不会清除未知结果或重新发送原请求。</p>
        {control.isError ? <p role="alert" className="mt-2 text-st-bad">{errMessage(control.error)}；请刷新预算核对原任务状态。</p> : null}
      </div> : null}
      <SettingsForm settings={data.settings} cloud={data.capabilities.node_role === "cloud"} onReadLatest={() => void query.refetch()} />
    </>}
  </section>;
}
