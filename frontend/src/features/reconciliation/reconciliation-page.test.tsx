import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { renderWithApp } from "@/test/utils";
import { server } from "@/test/server";
import { reconciliationLink, type ReconciliationItem, type ReconciliationResponse } from "@/lib/reconciliation-api";
import ReconciliationPage from "./reconciliation-page";

const item = (changes: Partial<ReconciliationItem> = {}): ReconciliationItem => ({
  record_id: "event-one", event_id: "event-one", decision_id: 1, target_kind: "atom", target_id: "atom-old",
  binding_status: "stale", attention_state: "current", needs_attention: true, decision_valid: true, action: "confirm",
  decided_at: "2026-10-01T08:00:00.000Z", created_at: "2026-10-01T08:00:00.000Z", note: "先核对基层材料",
  title: "防水基层原建议", snapshot_summary: "原记录：基层处理后即可涂刷", current_summary: "当前记录：先检查基层含水条件",
  target_url: "/videos/BVvisible", source_links: [{ video_id: "BVvisible", url: "/videos/BVvisible", title: "防水施工记录" }],
  superseded_by: null, ...changes,
});
const response = (changes: Partial<ReconciliationResponse> = {}): ReconciliationResponse => ({
  version: { library_id: "library", library_epoch: 1, kb_revision: 42 },
  event_watermark: { stream: { library_id: "library", library_epoch: 1 }, high_watermark: 7 },
  visibility_signature: "visible", summary: { total: 7, needs_attention: 3, historical: 3,
    by_reason: { active: { total: 2, current: 1, historical: 1 }, stale: { total: 2, current: 1, historical: 1 },
      missing: { total: 1, current: 1, historical: 0 }, legacy_unbound: { total: 1, current: 0, historical: 1 },
      invalid_time: { total: 1, current: 1, historical: 0 } } },
  filters: { reason: "all", scope: "attention" }, offset: 0, limit: 30, total: 1, items: [item()], ...changes,
});
const respond = (value = response()) => server.use(http.get("/api/reconciliation", () => HttpResponse.json(value)));
afterEach(() => { vi.unstubAllGlobals(); });

describe("复核对账只读呈现", () => {
  it("关联有效但原决定无效时仍明确标记待核对", async () => {
    respond(response({ items: [item({ binding_status: "active", decision_valid: false, needs_attention: true, action: "invalid-action" })] }));
    renderWithApp(<ReconciliationPage />);
    expect(await screen.findByText("原决定无效，需重新核对")).toBeInTheDocument();
    expect(screen.getByText("当前待核对", { exact: true })).toBeInTheDocument();
    expect(screen.getByText("当时决定：其他复核决定")).toBeInTheDocument();
  });

  it("区分旧快照、当前内容和历史记录，仅使用后端允许的定位链接", async () => {
    respond(response({ total: 2, items: [item(), item({ record_id: "missing", target_kind: "unknown", target_id: "hidden",
      title: "来源已缺失的旧决定", binding_status: "missing", attention_state: "historical", needs_attention: false,
      target_url: null, source_links: [], current_summary: null, snapshot_summary: null, superseded_by: "later-event" })] }));
    renderWithApp(<ReconciliationPage />);
    const list = await screen.findByRole("list", { name: "复核对账记录" });
    expect(within(list).getByText("原记录：基层处理后即可涂刷")).toBeInTheDocument();
    expect(within(list).getByText("当前记录：先检查基层含水条件")).toBeInTheDocument();
    expect(within(list).getByText("历史记录")).toBeInTheDocument();
    expect(within(list).getByText("当前没有可定位的可读来源。")).toBeInTheDocument();
    expect(within(list).getAllByRole("link").map((link) => link.getAttribute("href"))).toEqual(["/videos/BVvisible", "/videos/BVvisible"]);
    expect(screen.queryByRole("button", { name: /自动|重新绑定|采纳|保存决定|人工复核/ })).not.toBeInTheDocument();
    expect(screen.getByText(/查看和导出不会改变审核结论/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "目标已缺失 1" })).toBeInTheDocument();
  });

  it("切换原因与历史范围发送服务端筛选，并在筛选变化时回到第一页", async () => {
    const requests: URLSearchParams[] = [];
    server.use(http.get("/api/reconciliation", ({ request }) => {
      const params = new URL(request.url).searchParams;
      requests.push(params);
      const reason = params.get("reason") as "all" | "missing";
      const scope = params.get("scope") as "attention" | "history";
      return HttpResponse.json(response({ filters: { reason, scope }, offset: Number(params.get("offset")), total: 31 }));
    }));
    const user = userEvent.setup();
    renderWithApp(<ReconciliationPage />);
    await screen.findByText("防水基层原建议");
    await user.click(screen.getByRole("button", { name: "下一页复核记录" }));
    await screen.findByText("第 2 / 2 页");
    await user.click(screen.getByRole("button", { name: "目标已缺失 1" }));
    await waitFor(() => expect(requests.at(-1)?.get("reason")).toBe("missing"));
    expect(requests.at(-1)?.get("offset")).toBe("0");
    await user.click(screen.getByRole("tab", { name: "历史 3" }));
    await waitFor(() => expect(requests.at(-1)?.get("scope")).toBe("history"));
    expect(requests.at(-1)?.get("reason")).toBe("missing");
    expect(requests.at(-1)?.get("limit")).toBe("30");
    expect(screen.getByRole("tab", { name: "历史 3" })).toHaveAttribute("aria-selected", "true");
  });

  it("翻页以服务器total为准，末页禁用前进", async () => {
    server.use(http.get("/api/reconciliation", ({ request }) => {
      const offset = Number(new URL(request.url).searchParams.get("offset"));
      return HttpResponse.json(response({ offset, total: 31, items: [item({ title: offset ? "末页记录" : "首页记录" })] }));
    }));
    const user = userEvent.setup();
    renderWithApp(<ReconciliationPage />);
    await screen.findByText("首页记录");
    expect(screen.getByRole("button", { name: "上一页复核记录" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "下一页复核记录" }));
    await screen.findByText("末页记录");
    expect(screen.getByRole("button", { name: "下一页复核记录" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "上一页复核记录" }));
    await screen.findByText("首页记录");
  });

  it("筛选读取失败保留所选条件与上次结果，并标出旧结果所属范围", async () => {
    server.use(http.get("/api/reconciliation", ({ request }) => {
      const reason = new URL(request.url).searchParams.get("reason");
      return reason === "missing" ? new HttpResponse(null, { status: 503 }) : HttpResponse.json(response());
    }));
    const user = userEvent.setup();
    renderWithApp(<ReconciliationPage />);
    await screen.findByText("防水基层原建议");
    await user.click(screen.getByRole("button", { name: "目标已缺失 1" }));
    await screen.findByText(/后台刷新失败/);
    expect(screen.getByRole("button", { name: "目标已缺失 1" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("防水基层原建议")).toBeInTheDocument();
    expect(screen.getByText(/读取失败，仍展示上次结果/)).toHaveTextContent("当前待核对 · 全部原因，第 1 页");
    expect(screen.getByRole("button", { name: "导出 Markdown" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "下一页复核记录" })).toBeDisabled();
  });

  it("首次401沿用令牌门与共享错误态", async () => {
    const unauthorized = vi.fn();
    window.addEventListener("reno-unauthorized", unauthorized);
    server.use(http.get("/api/reconciliation", () => HttpResponse.json({ detail: "需要访问令牌" }, { status: 401 })));
    try {
      renderWithApp(<ReconciliationPage />);
      expect(await screen.findByText(/复核对账加载失败/)).toBeInTheDocument();
      expect(unauthorized).toHaveBeenCalledOnce();
      expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
    } finally { window.removeEventListener("reno-unauthorized", unauthorized); }
  });

  it("空结果明确筛选边界，不显示知识已全部审核", async () => {
    respond(response({ items: [], total: 0 }));
    renderWithApp(<ReconciliationPage />);
    expect(await screen.findByText("当前筛选下没有复核记录")).toBeInTheDocument();
    expect(screen.getByText(/不表示全库知识已通过审核/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "下一页复核记录" })).toBeDisabled();
  });
});

it("Markdown导出携带当前筛选与既有令牌，导出全部匹配记录而非当前页", async () => {
  const created = vi.fn(() => "blob:synthetic");
  class DownloadURL extends URL {
    static createObjectURL = created;
    static revokeObjectURL = vi.fn();
  }
  vi.stubGlobal("URL", DownloadURL);
  const clicked = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  const previous = localStorage.getItem("reno-auth-token");
  localStorage.setItem("reno-auth-token", "synthetic-test-token");
  let exported: URL | undefined;
  respond(response({ filters: { reason: "stale", scope: "history" } }));
  server.use(http.get("/api/reconciliation/export", ({ request }) => {
    exported = new URL(request.url);
    expect(request.headers.get("X-Reno-Token")).toBe("synthetic-test-token");
    return new HttpResponse("# 复核对账\n\n当时内容", { headers: { "Content-Type": "text/markdown; charset=utf-8" } });
  }));
  try {
    const user = userEvent.setup();
    renderWithApp(<ReconciliationPage />, { route: "/?reason=stale&scope=history" });
    await screen.findByText("防水基层原建议");
    await user.click(screen.getByRole("button", { name: "导出 Markdown" }));
    expect(await screen.findByText(/已导出：历史记录 · 内容已变化/)).toBeInTheDocument();
    expect(exported?.searchParams.get("reason")).toBe("stale");
    expect(exported?.searchParams.get("scope")).toBe("history");
    expect(exported?.searchParams.has("offset")).toBe(false);
    expect(clicked).toHaveBeenCalledOnce();
    expect(created).toHaveBeenCalledWith(expect.any(Blob));
    expect(document.querySelector("a[download]")).toBeNull();
  } finally {
    clicked.mockRestore();
    if (previous === null) localStorage.removeItem("reno-auth-token");
    else localStorage.setItem("reno-auth-token", previous);
  }
});

it("导出失败不启动下载且保留筛选", async () => {
  respond();
  server.use(http.get("/api/reconciliation/export", () => HttpResponse.json({ detail: "报告暂不可用" }, { status: 503 })));
  const clicked = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  try {
    const user = userEvent.setup();
    renderWithApp(<ReconciliationPage />);
    await screen.findByText("防水基层原建议");
    await user.click(screen.getByRole("button", { name: "导出 Markdown" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("导出失败");
    expect(clicked).not.toHaveBeenCalled();
    expect(screen.getByRole("tab", { name: "待核对 3" })).toHaveAttribute("aria-selected", "true");
  } finally { clicked.mockRestore(); }
});

it.each([null, "//outside.test/video", "https://outside.test/video", "javascript:alert(1)",
  "/videos/BVx?token=hidden", "/videos/../config", "/videos/BVx?t=-1", "/videos/BVx\\other"])("拒绝把非阅读目标 %s 变成链接", (url) => {
  expect(reconciliationLink(url)).toBeNull();
});
