import { expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/server";
import { renderWithApp } from "@/test/utils";
import { makeHealth } from "@/mocks/fixtures";
import CollectPage, { bookmarkletCode } from "./collect-page";

it("云端隐藏本地模型、cookie配置和本地任务，书签流程仍可用", async () => {
  server.use(http.get("/api/health", () => HttpResponse.json({ ...makeHealth(), node_role: "cloud" })),
    http.get("/api/sync-status", () => HttpResponse.json({ mode: "cloud", node_role: "cloud", configured: false,
      paired: false, compatible: false, online: false, last_seen: null, counts: {}, expired_claims: 0,
      queue: { total: 0, offset: 0, limit: 10, items: [] }, home: null })));
  const user = userEvent.setup();
  renderWithApp(<CollectPage />);
  expect(await screen.findByText("云端接收节点")).toBeInTheDocument();
  expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "处理任务" })).not.toBeInTheDocument();
  expect(screen.queryByText(/下载 cookie|config.local.json|coding plan/)).not.toBeInTheDocument();
  await user.click(screen.getByRole("tab", { name: "B站收藏夹" }));
  expect(screen.queryByText(/config.local.json|import-favlist/)).not.toBeInTheDocument();
  expect(screen.getByText("等待家庭节点领取与处理")).toBeInTheDocument();
  expect(screen.getByTitle("拖动我到浏览器书签栏").getAttribute("href")).toMatch(/^javascript:/);
});

it("执行书签代码时保留完整查询、分P和重复键，仅去片段再去重", async () => {
  const hrefs = ["https://www.bilibili.com/video/BV1?p=1&x=1&x=2#top",
    "https://www.bilibili.com/video/BV1?p=1&x=1&x=2#other",
    "https://www.bilibili.com/video/BV1?p=2&x=1&x=2", "https://www.bilibili.com/video/BV1?p=2&x=2&x=1"];
  const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ queued: 3 }) });
  const alert = vi.fn();
  const document = { querySelectorAll: () => hrefs.map((href) => ({ href })) };
  const execute = new Function("document", "fetch", "alert", `return ${bookmarkletCode("https://reno.example").slice("javascript:".length)}`);
  await execute(document, fetch, alert);
  expect(fetch).toHaveBeenCalledOnce();
  const [url, options] = fetch.mock.calls[0];
  expect(url).toBe("https://reno.example/api/import-batch");
  expect(JSON.parse(options.body).urls).toEqual([hrefs[0].split("#")[0], hrefs[2], hrefs[3]]);
});
