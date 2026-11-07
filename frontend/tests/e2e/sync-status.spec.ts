import { request as playwrightRequest, type Page } from "@playwright/test";
import { expect, test } from "./qa-fixtures";
import { readQaManifest, type QaManifest } from "./qa-manifest";

interface SyncFixture {
  version: string;
  role: string;
  row_count: number;
  page_size: number;
  row_ids: Record<string, number>;
  ready_video_id: string;
  missing_video_id: string;
  query_sentinel: string;
  synthetic_sync_token: string;
  lease_until: string;
  expected_counts: Record<string, number>;
}

const manifest = readQaManifest() as QaManifest & { sync_fixture?: SyncFixture };
const fixture = manifest.sync_fixture;

async function openQueue(page: Page) {
  await page.goto("/collect");
  const panel = page.getByRole("region", { name: "采集与同步状态" });
  await expect(panel.getByRole("heading", { name: "云端采集队列" })).toBeVisible();
  await expect(panel.getByText("家庭节点在线", { exact: true })).toBeVisible();
  await expect(panel.getByRole("list", { name: "采集队列" }).locator(":scope > li")).toHaveCount(fixture!.page_size);
  return panel;
}

test.describe("cloud sync status on the isolated real backend and built SPA", () => {
  test.skip(fixture?.version !== "sync-status-v1", "Requires scripts/prepare_sync_fixture.py synthetic cloud manifest");

  test("GET status uses ordinary browser authentication and strips source query data", async ({ request }) => {
    expect(fixture!.role).toBe("cloud");
    expect(fixture!.row_count).toBeGreaterThanOrEqual(31);
    expect(new Date(fixture!.lease_until).getTime()).toBeGreaterThan(Date.now());
    // Project defaults include the browser token; explicitly remove them so
    // this context actually exercises the unauthenticated API boundary.
    const anonymous = await playwrightRequest.newContext({
      baseURL: manifest.base_url,
      extraHTTPHeaders: {},
      storageState: { cookies: [], origins: [] },
    });
    try {
      expect((await anonymous.get("/api/sync-status")).status()).toBe(401);
      expect((await anonymous.get("/api/sync-status", {
        headers: { "X-Sync-Token": fixture!.synthetic_sync_token },
      })).status()).toBe(401);
      expect((await request.get("/api/sync/handshake")).status()).toBe(401);
    } finally {
      await anonymous.dispose();
    }
    const response = await request.get(`/api/sync-status?offset=0&limit=${fixture!.page_size}`);
    expect(response.ok()).toBeTruthy();
    const raw = await response.text();
    expect(raw).not.toContain(fixture!.query_sentinel);
    expect(raw).not.toContain("qa_secret");
    expect(raw).not.toContain(fixture!.synthetic_sync_token);
    const data = JSON.parse(raw);
    expect(data.mode).toBe("cloud");
    expect(data.configured && data.paired && data.compatible && data.online).toBe(true);
    expect(data.counts).toEqual(fixture!.expected_counts);
    expect(data.expired_claims).toBe(1);
    expect(data.queue.total).toBe(fixture!.row_count);
    const active = data.queue.items.find((item: { item_id: number }) => item.item_id === fixture!.row_ids.claimed_active);
    const expired = data.queue.items.find((item: { item_id: number }) => item.item_id === fixture!.row_ids.claimed_expired);
    expect(active.claim_active).toBe(true);
    expect(expired.claim_active).toBe(false);
    for (const key of ["url", "lease_token", "owner_id", "owner_generation", "publication_id", "result_hash"]) {
      expect(active).not.toHaveProperty(key);
    }
  });

  test("cloud controls and wording distinguish receipt, active claim and expired claim", async ({ page }, info) => {
    const unsupportedRequests: string[] = [];
    page.on("request", (request) => {
      const path = new URL(request.url()).pathname;
      if (path === "/api/tasks" || path === "/api/config/model") unsupportedRequests.push(path);
    });
    await page.setViewportSize({ width: 1280, height: 900 });
    const panel = await openQueue(page);
    await expect(page.getByRole("combobox", { name: /处理模型/ })).toHaveCount(0);
    await expect(page.getByRole("region", { name: "处理任务" })).toHaveCount(0);
    await expect(page.getByText("config.local.json", { exact: false })).toHaveCount(0);
    await expect(page.getByText("ytdlp_cookies", { exact: false })).toHaveCount(0);
    await expect(page.getByText("云端接收节点", { exact: true })).toBeVisible();
    await expect(panel.getByText("云端接收链接，家庭节点负责下载和处理。已领取不等于完成，以云端发布回执为准。", { exact: true })).toBeVisible();
    const list = panel.getByRole("list", { name: "采集队列" });
    for (const label of ["等待家庭节点领取", "已领取，尚未确认完成", "等待重试", "暂时无法完成", "云端已确认完成"]) {
      await expect(list.getByText(label, { exact: true }).first()).toBeVisible();
    }
    const expired = list.locator(":scope > li").filter({ has: page.getByText(`#${fixture!.row_ids.claimed_expired}`, { exact: true }) });
    await expect(expired.getByText("领取已失效，等待重新领取；尚未确认完成。", { exact: true })).toBeVisible();
    const active = list.locator(":scope > li").filter({ has: page.getByText(`#${fixture!.row_ids.claimed_active}`, { exact: true }) });
    await expect(active.getByText("领取已失效，等待重新领取；尚未确认完成。", { exact: true })).toHaveCount(0);
    await expect(panel.getByText("1 项领取已失效，等待重新领取。", { exact: true })).toBeVisible();
    await page.getByRole("tab", { name: "B站收藏夹", exact: true }).click();
    await expect(page.getByRole("heading", { name: /打开B站收藏夹页$/ })).toBeVisible();
    await expect(page.getByText("python -m reno import-favlist", { exact: false })).toHaveCount(0);
    expect(unsupportedRequests).toEqual([]);
    expect(await page.content()).not.toContain(fixture!.query_sentinel);
    await page.screenshot({ path: info.outputPath("cloud-sync-status-desktop.png"), fullPage: true });
  });

  test("real pagination visits every queue row without leaking URL queries", async ({ page }) => {
    const panel = await openQueue(page);
    const pages = Math.ceil(fixture!.row_count / fixture!.page_size);
    const seen: number[] = [];
    await expect(panel.getByRole("button", { name: "上一页采集队列" })).toBeDisabled();
    for (let current = 1; current <= pages; current++) {
      await expect(panel.getByText(`共 ${fixture!.row_count} 条 · 第 ${current} / ${pages} 页`, { exact: true })).toBeVisible();
      const rows = panel.getByRole("list", { name: "采集队列" }).locator(":scope > li");
      const expected = Math.min(fixture!.page_size, fixture!.row_count - (current - 1) * fixture!.page_size);
      await expect(rows).toHaveCount(expected);
      for (const text of await rows.allTextContents()) {
        const id = text.match(/^#(\d+)/)?.[1];
        expect(id).toBeTruthy();
        seen.push(Number(id));
      }
      expect(await panel.innerHTML()).not.toContain(fixture!.query_sentinel);
      if (current < pages) await panel.getByRole("button", { name: "下一页采集队列" }).click();
    }
    expect(new Set(seen).size).toBe(fixture!.row_count);
    expect(seen).toEqual([...seen].sort((a, b) => b - a));
    await expect(panel.getByRole("button", { name: "下一页采集队列" })).toBeDisabled();
    await panel.getByRole("button", { name: "上一页采集队列" }).click();
    await expect(panel.getByText(`共 ${fixture!.row_count} 条 · 第 ${pages - 1} / ${pages} 页`, { exact: true })).toBeVisible();
  });

  test("only a ready completed video exposes a working video entry", async ({ page, request }) => {
    const panel = await openQueue(page);
    const list = panel.getByRole("list", { name: "采集队列" });
    const ready = list.locator(":scope > li").filter({ has: page.getByText(`#${fixture!.row_ids.done_ready}`, { exact: true }) });
    const missing = list.locator(":scope > li").filter({ has: page.getByText(`#${fixture!.row_ids.done_missing}`, { exact: true }) });
    await expect(ready.getByRole("link", { name: "查看视频" })).toHaveAttribute("href", `/videos/${fixture!.ready_video_id}`);
    await expect(missing.getByRole("link", { name: "查看视频" })).toHaveCount(0);
    await expect(missing.getByText("视频暂不可查看。", { exact: true })).toBeVisible();
    await expect(list.getByRole("link", { name: "查看视频" })).toHaveCount(1);
    const media = await request.get(`/api/video/${fixture!.ready_video_id}/file`, { headers: { Range: "bytes=0-31" } });
    expect(media.status()).toBe(206);
    expect((await media.body()).length).toBe(32);
    expect((await request.get(`/api/video/${fixture!.missing_video_id}/file`)).status()).toBe(404);
    await ready.getByRole("link", { name: "查看视频" }).click();
    await expect(page).toHaveURL(new RegExp(`/videos/${fixture!.ready_video_id}$`));
    await expect(page.locator("video")).toBeVisible();
    await expect.poll(() => page.locator("video").evaluate((element: HTMLVideoElement) => element.readyState)).toBeGreaterThanOrEqual(1);
  });

  for (const theme of ["light", "dark"] as const) {
    test(`375px ${theme} has no page or queue horizontal overflow`, async ({ page }, info) => {
      await page.setViewportSize({ width: 375, height: 812 });
      await page.addInitScript((value) => window.localStorage.setItem("reno-theme", value), theme);
      const panel = await openQueue(page);
      await expect(page.locator("html")).toHaveClass(theme === "dark" ? /dark/ : /^(?!.*\bdark\b).*$/);
      const widths = await page.evaluate(() => {
        const elements = [document.documentElement, document.body, document.querySelector("main"),
          document.querySelector('[aria-label="采集与同步状态"]')].filter((element): element is HTMLElement => element instanceof HTMLElement);
        return elements.map((element) => ({ tag: element.tagName, scroll: element.scrollWidth, client: element.clientWidth }));
      });
      for (const width of widths) expect(width.scroll, `${width.tag} horizontal overflow`).toBeLessThanOrEqual(width.client + 1);
      const sourceTexts = panel.getByRole("list", { name: "采集队列" }).locator("p[title]");
      expect((await sourceTexts.allTextContents()).join(" ")).not.toContain(fixture!.query_sentinel);
      expect((await sourceTexts.evaluateAll((nodes) => nodes.map((node) => node.getAttribute("title")))).join(" ")).not.toContain("qa_secret");
      await panel.getByRole("button", { name: "下一页采集队列" }).scrollIntoViewIfNeeded();
      await page.screenshot({ path: info.outputPath(`cloud-sync-mobile-${theme}-pagination.png`), fullPage: true });
      await panel.getByRole("heading", { name: "云端采集队列" }).scrollIntoViewIfNeeded();
      await page.screenshot({ path: info.outputPath(`cloud-sync-mobile-${theme}.png`), fullPage: true });
    });
  }
});
