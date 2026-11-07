import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { renderWithApp } from "@/test/utils";
import { server } from "@/test/server";
import { syncPollInterval, syncStatusQuery, type HomeSyncStatus, type SyncQueueItem, type SyncStatus } from "@/lib/sync-api";
import { SyncStatusPanel } from "./sync-status-panel";

const STAMP = "2026-10-01T08:00:00.000Z";
const home = (changes: Partial<HomeSyncStatus> = {}): HomeSyncStatus => ({ enabled: true, configured: true,
  state: "idle", updated_at: STAMP, last_success_at: STAMP, retry_at: null, error_code: null,
  seeded: true, has_pending_publication: false, work_phase: null, lease_valid: true, ...changes });
export const statusFixture = (changes: Partial<SyncStatus> = {}): SyncStatus => ({ mode: "home", node_role: "all",
  configured: false, compatible: null, paired: null, online: false, last_seen: null, lease_until: null,
  counts: {}, expired_claims: 0, retry_due: 0, home: home(), queue: { total: 0, offset: 0, limit: 10, items: [] }, ...changes });
const row = (item_id: number, changes: Partial<SyncQueueItem> = {}): SyncQueueItem => ({ item_id,
  status: "queued", attempts: 0, last_error_class: null, created_at: STAMP, updated_at: STAMP,
  retry_at: null, video_id: null, source_host: "www.bilibili.com", source_path: `/video/BV${item_id}`,
  claim_active: false, can_view_video: false, ...changes });
const respond = (data: SyncStatus) => server.use(http.get("/api/sync-status", () => HttpResponse.json(data)));

describe("家庭同步摘要", () => {
  it.each([
    ["source_failed", "SYNC_SOURCE_FAILED", "来源任务已确认失败，原结果与历史记录保留。"],
    ["pending_delivery", "SYNC_SOURCE_CONTROL_DELIVERY_PENDING", "来源状态尚未取得云端确认，原记录已保留。"],
  ])("renders a closed %s result without an unknown error or implied restart", async (state, code, message) => {
    respond(statusFixture({ home: home({ state, error_code: code }) }));
    renderWithApp(<SyncStatusPanel />);
    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(screen.queryByText(/同步遇到暂无法识别/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /恢复|暂停|重试任务/ })).not.toBeInTheDocument();
  });

  it.each([
    ["disabled", "同步已关闭"], ["unconfigured", "同步尚未配置完整"], ["not_started", "首次同步尚未运行"],
    ["state_error", "同步状态不可读取"], ["waiting_lease", "等待同步租约"], ["retry_wait", "等待再次同步"],
    ["review_backlog", "等待继续同步复核记录"],
    ["recovery_held", "恢复后知识同步已暂停"], ["recovery_resuming", "正在恢复知识同步"],
    ["recovery_blocked", "恢复同步需要处理"],
    ["budget_wait", "采集等待放行"], ["pending_delivery", "等待云端确认采集状态"],
    ["source_blocked", "来源任务等待处理"],
    ["source_failed", "来源任务已确认失败"],
    ["pulling", "正在拉取复核记录"], ["pushing_reviews", "正在发送复核记录"], ["processing", "正在处理视频"],
    ["uploading", "正在上传媒体"], ["publishing", "正在等待云端确认"], ["published", "云端已确认发布"],
  ])("准确呈现 %s，不借用云端队列完成状态", async (state, label) => {
    respond(statusFixture({ home: home({ state, last_success_at: null }) }));
    renderWithApp(<SyncStatusPanel />);
    expect(await screen.findByText(label)).toBeInTheDocument();
    expect(screen.getByText("暂无成功记录")).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "采集队列" })).not.toBeInTheDocument();
  });

  it("保留最后成功、已保存进度与待发批次，未知状态/错误不回显", async () => {
    respond(statusFixture({ home: home({ state: "raw-secret-state", error_code: "raw-secret-error",
      work_phase: "judged", has_pending_publication: true, retry_at: STAMP }) }));
    renderWithApp(<SyncStatusPanel />);
    expect(await screen.findByText("同步状态待核对")).toBeInTheDocument();
    expect(screen.getByText("跨视频判定已完成")).toBeInTheDocument();
    expect(screen.getByText(/有待发送批次/)).toBeInTheDocument();
    expect(screen.getByText("最近同步成功")).toBeInTheDocument();
    expect(screen.getByText("计划再次尝试")).toBeInTheDocument();
    expect(screen.getByText(/同步遇到暂无法识别/)).toBeInTheDocument();
    expect(screen.queryByText(/raw-secret/)).not.toBeInTheDocument();
  });
});

describe("云端采集队列", () => {
  it("预算等待显示实际计划时间，尚未确认完成的视频不提供查看链接", async () => {
    respond(statusFixture({ mode: "cloud", home: null, counts: { budget_wait: 2 }, budget_due: 1,
      queue: { total: 2, offset: 0, limit: 10, items: [
        row(8, { status: "budget_wait", retry_at: STAMP, video_id: "saved" }),
        row(9, { status: "budget_wait" }),
      ] } }));
    renderWithApp(<SyncStatusPanel />);
    expect(await screen.findByText("预算等待")).toBeInTheDocument();
    expect(screen.getAllByText("等待采集条件允许后继续")).toHaveLength(2);
    expect(screen.getByText(/^计划继续\s/)).toBeInTheDocument();
    expect(screen.getByText("尚无计划继续时间，等待家庭节点核对。")).toBeInTheDocument();
    expect(screen.queryByText(/队列状态待核对/)).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "查看视频" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /恢复|暂停|重试任务/ })).not.toBeInTheDocument();
  });

  it("区分等待、领取、领取过期与完成，只有 ready 的 done 项可查看视频", async () => {
    const items = [row(1), row(2, { status: "claimed", claim_active: true, video_id: "not-ready" }),
      row(3, { status: "claimed" }), row(4, { status: "retry_wait", last_error_class: "network", retry_at: STAMP }),
      row(5, { status: "dead", last_error_class: "secret-raw-error" }),
      row(6, { status: "done", video_id: "missing" }), row(7, { status: "done", video_id: "BVready", can_view_video: true })];
    respond(statusFixture({ mode: "cloud", home: null, configured: true, paired: true, compatible: true, online: true,
      counts: { queued: 1, claimed: 2, retry_wait: 1, dead: 1, done: 2 }, expired_claims: 1,
      queue: { total: 7, offset: 0, limit: 10, items } }));
    renderWithApp(<SyncStatusPanel />);
    expect(await screen.findByText("家庭节点在线")).toBeInTheDocument();
    expect(screen.getByText("等待家庭节点领取")).toBeInTheDocument();
    expect(screen.getAllByText("已领取，尚未确认完成")).toHaveLength(2);
    expect(screen.getByText(/领取已失效，等待重新领取；/)).toBeInTheDocument();
    expect(screen.getByText("视频暂不可查看。")).toBeInTheDocument();
    expect(screen.getByText("网络连接异常")).toBeInTheDocument();
    expect(screen.queryByText(/secret-raw-error/)).not.toBeInTheDocument();
    expect(screen.getAllByRole("link")).toHaveLength(1);
    expect(screen.getByRole("link", { name: "查看视频" })).toHaveAttribute("href", "/videos/BVready");
    expect(screen.queryByRole("button", { name: /删除|重试任务/ })).not.toBeInTheDocument();
  });

  it.each([
    [{ configured: false }, "云端同步尚未开放"],
    [{ configured: true, paired: false }, "家庭节点尚未配对"],
    [{ configured: true, paired: true, compatible: false }, "两端同步版本待核对"],
    [{ configured: true, paired: true, compatible: true, online: false }, "家庭节点离线"],
  ])("空队列如实显示节点状态 %s", async (changes, label) => {
    respond(statusFixture({ mode: "cloud", home: null, ...changes }));
    renderWithApp(<SyncStatusPanel />);
    expect(await screen.findByText(label)).toBeInTheDocument();
    expect(screen.getByText("暂无采集链接")).toBeInTheDocument();
  });

  it("按服务器 offset 分页，末页停止前进并可回到上一页", async () => {
    const requests: number[] = [];
    server.use(http.get("/api/sync-status", ({ request }) => {
      const params = new URL(request.url).searchParams;
      const offset = Number(params.get("offset"));
      requests.push(offset);
      expect(params.get("limit")).toBe("10");
      return HttpResponse.json(statusFixture({ mode: "cloud", home: null,
        queue: { total: 11, offset, limit: 10, items: [row(offset + 1)] } }));
    }));
    const user = userEvent.setup();
    renderWithApp(<SyncStatusPanel />);
    await screen.findByText(/第 1 \/ 2 页/);
    expect(screen.getByRole("button", { name: "上一页采集队列" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "下一页采集队列" }));
    expect(await screen.findByText(/第 2 \/ 2 页/)).toBeInTheDocument();
    expect(screen.getByText("#11")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "下一页采集队列" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "上一页采集队列" }));
    expect(await screen.findByText(/第 1 \/ 2 页/)).toBeInTheDocument();
    expect(requests).toContain(10);
  });
});

it("后台刷新失败保留旧数据，并将在线状态标明为上次状态", async () => {
  let failed = false;
  server.use(http.get("/api/sync-status", () => failed ? new HttpResponse(null, { status: 503 }) :
    HttpResponse.json(statusFixture({ mode: "cloud", home: null, configured: true, compatible: true,
      paired: true, online: true, queue: { total: 1, offset: 0, limit: 10, items: [row(9)] } }))));
  const user = userEvent.setup();
  renderWithApp(<SyncStatusPanel />);
  await screen.findByText("家庭节点在线");
  failed = true;
  await user.click(screen.getByRole("button", { name: "刷新同步状态" }));
  expect(await screen.findByText(/后台刷新失败/)).toBeInTheDocument();
  expect(screen.getByText("上次状态：家庭节点在线")).toBeInTheDocument();
  expect(screen.getByText("#9")).toBeInTheDocument();
  expect(screen.queryByText("家庭节点在线", { exact: true })).not.toBeInTheDocument();
  expect(screen.queryByText(/状态获取于/)).not.toBeInTheDocument();
});

it("下一页加载失败仍可返回已读取的上一页", async () => {
  server.use(http.get("/api/sync-status", ({ request }) => {
    const offset = Number(new URL(request.url).searchParams.get("offset"));
    return offset > 0 ? new HttpResponse(null, { status: 503 }) : HttpResponse.json(statusFixture({ mode: "cloud", home: null,
      queue: { total: 11, offset: 0, limit: 10, items: [row(1)] } }));
  }));
  const user = userEvent.setup();
  renderWithApp(<SyncStatusPanel />);
  await screen.findByText("#1");
  await user.click(screen.getByRole("button", { name: "下一页采集队列" }));
  expect(await screen.findByText(/同步状态加载失败/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "上一页采集队列" }));
  expect(await screen.findByText("#1")).toBeInTheDocument();
});

it("首次失败复用错误态，401继续触发既有令牌门事件", async () => {
  const unauthorized = vi.fn();
  window.addEventListener("reno-unauthorized", unauthorized);
  server.use(http.get("/api/sync-status", () => HttpResponse.json({ detail: "需要访问令牌" }, { status: 401 })));
  try {
    renderWithApp(<SyncStatusPanel />);
    expect(await screen.findByText(/同步状态加载失败/)).toBeInTheDocument();
    await waitFor(() => expect(unauthorized).toHaveBeenCalledOnce());
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
  } finally { window.removeEventListener("reno-unauthorized", unauthorized); }
});

it("有活动时加快刷新，空闲放慢并暂停后台轮询", () => {
  expect(syncPollInterval(statusFixture({ home: home({ state: "processing" }) }))).toBe(3_000);
  expect(syncPollInterval(statusFixture())).toBe(15_000);
  expect(syncPollInterval(statusFixture({ mode: "cloud", counts: { queued: 1 } }))).toBe(3_000);
  expect(syncStatusQuery().refetchIntervalInBackground).toBe(false);
});

it("家庭端旧running会话失效后仅显示上次记录，不伪称当前仍在处理", async () => {
  respond(statusFixture({ home: home({ state: "processing", lease_valid: false }) }));
  renderWithApp(<SyncStatusPanel />);
  expect(await screen.findByText("上次记录：正在处理视频")).toBeInTheDocument();
  expect(screen.getByText("上次同步会话已失效，等待重新确认家庭节点状态。")).toBeInTheDocument();
  expect(screen.queryByText("正在处理视频", { exact: true })).not.toBeInTheDocument();
});

it("首次未运行时没有租约，不误称为旧会话失效", async () => {
  respond(statusFixture({ home: home({ state: "not_started", lease_valid: false, last_success_at: null }) }));
  renderWithApp(<SyncStatusPanel />);
  expect(await screen.findByText("首次同步尚未运行")).toBeInTheDocument();
  expect(screen.queryByText(/上次同步会话已失效/)).not.toBeInTheDocument();
});

it.each([
  ["held", "恢复后知识同步已暂停"],
  ["resume_pending", "等待家庭节点重新发布完整知识"],
] as const)("云端%s与在线状态独立，保留队列但不提供远程恢复操作", async (state, label) => {
  respond(statusFixture({ mode: "cloud", home: null, configured: true, paired: true,
    compatible: true, online: true, recovery: { state, generation: 2, revision: 4 },
    counts: { queued: 1 }, queue: { total: 1, offset: 0, limit: 10, items: [row(1)] } }));
  renderWithApp(<SyncStatusPanel />);
  expect(await screen.findByText(label)).toBeInTheDocument();
  expect(screen.getByText("家庭节点在线")).toBeInTheDocument();
  expect(screen.getByRole("list", { name: "采集队列" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /恢复同步|恢复知识/ })).not.toBeInTheDocument();
});

it("家庭暂停保留旧批次说明，不暗示正在发送或已经成功", async () => {
  respond(statusFixture({ home: home({ state: "recovery_held", has_pending_publication: true,
    work_phase: "downloaded", last_success_at: null }) }));
  renderWithApp(<SyncStatusPanel />);
  expect(await screen.findByText("恢复后知识同步已暂停")).toBeInTheDocument();
  expect(screen.getByText(/暂停期间不会用它覆盖恢复后的知识/)).toBeInTheDocument();
  expect(screen.getByText("下载已保存")).toBeInTheDocument();
  expect(screen.queryByText(/有待发送批次：/)).not.toBeInTheDocument();
});

it("持久暂停时降低轮询，明确续传时检查确认进度", () => {
  expect(syncPollInterval(statusFixture({ home: home({ state: "recovery_held", has_pending_publication: true }) }))).toBe(15_000);
  expect(syncPollInterval(statusFixture({ home: home({ state: "recovery_blocked", has_pending_publication: true }) }))).toBe(15_000);
  expect(syncPollInterval(statusFixture({ mode: "cloud", counts: { claimed: 5 },
    recovery: { state: "held", generation: 1, revision: 1 } }))).toBe(15_000);
  expect(syncPollInterval(statusFixture({ mode: "cloud", recovery: { state: "resume_pending", generation: 1, revision: 2 } }))).toBe(3_000);
  expect(syncPollInterval(statusFixture({ home: home({ state: "recovery_resuming" }) }))).toBe(3_000);
});

it.each(["budget_wait", "source_blocked"])("家庭%s保留等待摘要，不因旧批次而高频轮询", async (state) => {
  respond(statusFixture({ home: home({ state, lease_valid: false, has_pending_publication: true,
    last_success_at: null, error_code: state === "source_blocked" ? "SYNC_SOURCE_EXECUTION_BLOCKED" : null }) }));
  renderWithApp(<SyncStatusPanel />);
  expect(await screen.findByText(state === "budget_wait" ? "采集等待放行" : "来源任务等待处理")).toBeInTheDocument();
  expect(screen.queryByText(/上次同步会话已失效/)).not.toBeInTheDocument();
  expect(screen.queryByText(/暂无法识别/)).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /恢复|暂停|重试任务/ })).not.toBeInTheDocument();
  expect(syncPollInterval(statusFixture({ home: home({ state, has_pending_publication: true }) }))).toBe(15_000);
});

it("等待云端确认只显示保留记录，连接错误不误报来源执行失败", async () => {
  respond(statusFixture({ home: home({ state: "pending_delivery", lease_valid: false,
    error_code: "SYNC_BUDGET_DELIVERY_PENDING", retry_at: STAMP }) }));
  renderWithApp(<SyncStatusPanel />);
  expect(await screen.findByText("等待云端确认采集状态")).toBeInTheDocument();
  expect(screen.getByText("采集等待或恢复记录尚未取得云端确认，已有记录保留。")).toBeInTheDocument();
  expect(screen.queryByText(/上次同步会话已失效|暂无法识别/)).not.toBeInTheDocument();
  expect(syncPollInterval(statusFixture({ home: home({ state: "pending_delivery" }) }))).toBe(3_000);
  expect(syncPollInterval(statusFixture({ mode: "cloud", counts: { budget_wait: 3 }, budget_due: 0 }))).toBe(15_000);
  expect(syncPollInterval(statusFixture({ mode: "cloud", counts: { budget_wait: 3 }, budget_due: 1 }))).toBe(3_000);
});
