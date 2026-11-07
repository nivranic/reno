import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { renderWithApp } from "@/test/utils";
import { server } from "@/test/server";
import { budgetFields, budgetKeys, budgetPollInterval, type BudgetSettings, type BudgetSnapshot, type BudgetValue } from "@/lib/budget-api";
import { BudgetPanel } from "./budget-panel";

export function budgetFixture(): BudgetSnapshot {
  const values: Record<string, BudgetValue> = { budget_enabled: false, budget_timezone: "Asia/Shanghai",
    download_interval_min_seconds: 0, download_interval_max_seconds: 0, download_daily_videos: 2,
    download_rate_bytes_per_second: null, model_daily_attempts: 8, model_reported_tokens_stop_at: null,
    model_daily_output_tokens: null, model_total_tokens_mode: "reported_only", model_operation_max_attempts: 8,
    source_resolution_max_attempts: 24 };
  const fields = Object.fromEntries(budgetFields.map((key) => [key, { value: values[key], source: "default",
    editable: key !== "model_total_tokens_mode" }])) as BudgetSettings["fields"];
  const counts = { admissions: 0, confirmed: 0, held: 0, in_flight: 0, unknown: 0 };
  const empty = { reported: 0, covered_attempts: 0, missing_attempts: 0, dispatched_attempts: 0 };
  const { source_resolution_max_attempts: _resolver, ...policy } = values;
  return { settings: { effective_policy: policy as BudgetSettings["effective_policy"], source_resolution_max_attempts: 24,
    fields, settings_revision: "a".repeat(64) },
    usage: { scope: "local_ledger_only", history_before_ledger: "unknown",
      window: { timezone: "Asia/Shanghai", local_date: "2026-10-02", period_key: "Asia/Shanghai/2026-10-02",
        start_utc: "2026-10-01T16:00:00.000000Z", end_utc: "2026-10-02T16:00:00.000000Z" },
      attempts: { model: { ...counts }, download: { ...counts }, source_resolution: { ...counts },
        source_metadata: { ...counts }, source_enumeration: { ...counts } }, download_logical_admissions: 0,
      tokens: { input_tokens: { ...empty }, output_tokens: { ...empty }, total_tokens: { ...empty },
        quality_counts: { reported: 0, partial: 0, missing: 0, invalid: 0, unobserved: 0 } },
      conservative_output_tokens: { held: 0, in_flight: 0, unknown: 0, confirmed_missing_output: 0, total: 0 },
      all_history: { unknown_attempts: 0, waiting_operations: 0, stored_results: 0 },
      waiting: { scope: "all_history", total: 0, operations: [], limit: 50 } },
    home: { available: false, task: null, control_revision: null },
    capabilities: { read: true, edit_policy: true, task_control: true, home_control: false, node_role: "all" } };
}
const respond = (data: BudgetSnapshot) => server.use(http.get("/api/budget", () => HttpResponse.json(data)));

describe("预算与等待任务", () => {
  it("shows server natural day, four states, independent token coverage and unknown history", async () => {
    const data = budgetFixture();
    data.usage.attempts.model = { admissions: 4, confirmed: 1, held: 1, in_flight: 1, unknown: 1 };
    data.usage.tokens.input_tokens = { reported: 12, covered_attempts: 1, missing_attempts: 2, dispatched_attempts: 3 };
    data.usage.tokens.total_tokens = { reported: null, covered_attempts: 0, missing_attempts: 3, dispatched_attempts: 3 };
    data.usage.conservative_output_tokens = { held: 100, in_flight: 100, unknown: 100, confirmed_missing_output: 100, total: 400 };
    data.usage.waiting = { scope: "all_history", total: 1, limit: 50, operations: [
      { operation_id: "original", kind: "model", root_job_id: null, reason: "indeterminate", resume_at: null }] };
    respond(data);
    renderWithApp(<BudgetPanel />);
    expect(await screen.findByText(/本机账本 · 2026-10-02 · Asia\/Shanghai/)).toBeInTheDocument();
    expect(screen.getByText("模型请求机会：4")).toBeInTheDocument();
    expect(screen.getByText("未报告")).toBeInTheDocument();
    expect(screen.getByText("已报告覆盖 0/3 次，缺失 3 次")).toBeInTheDocument();
    expect(screen.getByText(/已返回但缺少输出报告 100/)).toBeInTheDocument();
    expect(screen.getByText("等待操作（全部历史）")).toBeInTheDocument();
    expect(screen.getByText(/结果未知，原请求不会自动重发/)).toBeInTheDocument();
    expect(screen.getByText("尚无计划继续时间，等待条件核对。")).toBeInTheDocument();
    expect(screen.getByText(/账本启用前的历史用量未知/)).toBeInTheDocument();
  });

  it("saves only edited fields with the original revision and preserves zero versus unlimited", async () => {
    const data = budgetFixture();
    respond(data);
    const bodies: unknown[] = [];
    server.use(http.post("/api/config/budget", async ({ request }) => {
      bodies.push(await request.json());
      return HttpResponse.json({ ...data.settings, settings_revision: "b".repeat(64) });
    }));
    renderWithApp(<BudgetPanel />);
    await screen.findByText("预算限制未启用");
    const user = userEvent.setup();
    await user.click(screen.getByText("预算设置"));
    await user.clear(screen.getByLabelText("每日模型请求次数"));
    await user.type(screen.getByLabelText("每日模型请求次数"), "0");
    await user.clear(screen.getByLabelText("每日新视频名额"));
    await user.click(screen.getByRole("button", { name: "保存预算设置" }));
    await waitFor(() => expect(bodies).toEqual([{ expected_settings_revision: "a".repeat(64),
      updates: { download_daily_videos: null, model_daily_attempts: 0 } }]));
  });

  it("shows environment provenance and makes overridden fields read only", async () => {
    const data = budgetFixture();
    data.settings.fields.model_daily_attempts = { value: 7, source: "environment", editable: false };
    respond(data);
    renderWithApp(<BudgetPanel />);
    await screen.findByText("预算限制未启用");
    await userEvent.setup().click(screen.getByText("预算设置"));
    expect(screen.getByLabelText("每日模型请求次数")).toBeDisabled();
    expect(screen.getByText("环境变量覆盖 · 只读")).toBeInTheDocument();
    expect(screen.getByLabelText("每日模型请求次数")).toHaveValue("7");
  });

  it("does not overwrite a dirty form after settings change and reload really refetches", async () => {
    let data = budgetFixture();
    let reads = 0;
    server.use(http.get("/api/budget", () => { reads++; return HttpResponse.json(data); }));
    const { client } = renderWithApp(<BudgetPanel />);
    await screen.findByText("预算限制未启用");
    const user = userEvent.setup();
    await user.click(screen.getByText("预算设置"));
    await user.clear(screen.getByLabelText("每日模型请求次数"));
    await user.type(screen.getByLabelText("每日模型请求次数"), "5");
    data = { ...data, settings: { ...data.settings, settings_revision: "b".repeat(64), fields: {
      ...data.settings.fields, model_daily_attempts: { value: 2, source: "local", editable: true } } } };
    await act(async () => { client.setQueryData(budgetKeys.snapshot, data); });
    await screen.findByText(/服务器设置已有更新/);
    expect(screen.getByLabelText("每日模型请求次数")).toHaveValue("5");
    expect(screen.getByRole("button", { name: "保存预算设置" })).toBeDisabled();
    expect(screen.getByText(/服务器设置已有更新/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "读取最新设置" }));
    await waitFor(() => expect(reads).toBe(2));
    expect(screen.getByLabelText("每日模型请求次数")).toHaveValue("2");
  });

  it("uses only the original Home revision and never auto retries an unknown control response", async () => {
    const data = budgetFixture();
    data.capabilities.home_control = true;
    data.home = { available: true, control_revision: "c".repeat(64), task: { job_id: 42, status: "pending",
      updated_at: "2026-10-02T00:00:00Z", not_before: null, blocked_reason: "indeterminate", wait_mode: "blocked",
      manual_paused: true, can_pause: false, can_resume: true } };
    respond(data);
    const bodies: unknown[] = [];
    server.use(http.post("/api/budget/home/resume", async ({ request }) => {
      bodies.push(await request.json());
      return HttpResponse.json({ detail: "原任务状态已变化", code: "BUDGET_HOME_CONTROL_CHANGED" }, { status: 409 });
    }));
    renderWithApp(<BudgetPanel />);
    await userEvent.setup().click(await screen.findByRole("button", { name: "请求继续家庭采集" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("原任务状态已变化");
    expect(bodies).toEqual([{ expected_control_revision: "c".repeat(64) }]);
    expect(screen.getByText(/不会清除未知结果或重新发送原请求/)).toBeInTheDocument();
  });

  it("cloud edits its own model budget and has no Home controls or download form", async () => {
    const data = budgetFixture();
    data.capabilities.node_role = "cloud";
    data.capabilities.task_control = false;
    respond(data);
    renderWithApp(<BudgetPanel />);
    await screen.findByText(/这里只维护当前云节点的模型预算/);
    await userEvent.setup().click(screen.getByText("预算设置"));
    expect(screen.getByLabelText("每日模型请求次数")).toBeEnabled();
    expect(screen.queryByLabelText("每日新视频名额")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /家庭采集/ })).not.toBeInTheDocument();
  });

  it("an unavailable ledger is an error without fabricated zero usage", async () => {
    server.use(http.get("/api/budget", () => HttpResponse.json({ detail: "本机账本暂不可用", code: "BUDGET_VIEW_UNAVAILABLE" }, { status: 503 })));
    renderWithApp(<BudgetPanel />);
    const region = screen.getByRole("region", { name: "预算与等待任务" });
    expect(await within(region).findByText("本机账本暂不可用")).toBeInTheDocument();
    expect(within(region).queryByText(/模型请求机会/)).not.toBeInTheDocument();
    expect(within(region).queryByRole("button", { name: "保存预算设置" })).not.toBeInTheDocument();
  });

  it("polls unresolved waits slowly and actual in-flight requests quickly", () => {
    const data = budgetFixture();
    data.usage.attempts.model.unknown = 1;
    expect(budgetPollInterval(data)).toBe(15_000);
    data.usage.attempts.model.in_flight = 1;
    expect(budgetPollInterval(data)).toBe(3000);
  });
});
