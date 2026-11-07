import { request as playwrightRequest } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { expect, test } from "./qa-fixtures";
import { readQaManifest } from "./qa-manifest";
import type { ReconciliationResponse } from "../../src/lib/reconciliation-api";

const manifest = readQaManifest() as ReturnType<typeof readQaManifest> & {
  reconciliation_fixture?: { version: string };
};
test.skip(!manifest.reconciliation_fixture, "Requires prepare_product_fixture.py --reconciliation in a new isolated QA root");
test.beforeAll(() => {
  expect(manifest.reconciliation_fixture?.version).toBe("reconciliation-v1");
});

for (const [label, width, height, theme] of [
  ["desktop-light", 1280, 900, "light"], ["desktop-dark", 1280, 900, "dark"],
  ["mobile-light", 375, 812, "light"], ["mobile-dark", 375, 812, "dark"],
] as const) {
  test(`reconciliation real API layout and reasons: ${label}`, async ({ page, request }, info) => {
    const reply = await request.get("/api/reconciliation");
    expect(reply.ok()).toBe(true);
    const before: ReconciliationResponse = await reply.json();
    expect(before.total).toBeGreaterThan(30); // Explicit synthetic reconciliation fixture prerequisite.
    await page.setViewportSize({ width, height });
    await page.addInitScript((choice) => localStorage.setItem("reno-theme", choice), theme);
    await page.goto("/reconciliation");
    await expect(page.getByRole("heading", { name: "复核对账", exact: true })).toBeVisible();
    const list = page.getByRole("list", { name: "复核对账记录" });
    await expect(list.locator(":scope > li")).toHaveCount(before.items.length);
    await expect(page.getByRole("button", { name: `目标已缺失 ${before.summary.by_reason.missing.total}` })).toBeVisible();
    await expect(page.getByRole("tab", { name: `待核对 ${before.summary.needs_attention}` })).toHaveAttribute("aria-selected", "true");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    expect(await page.locator("html").evaluate((node) => node.classList.contains("dark"))).toBe(theme === "dark");
    const controls = await page.getByRole("button", { name: "导出 Markdown" }).boundingBox();
    expect(controls).not.toBeNull();
    expect(controls!.x).toBeGreaterThanOrEqual(0);
    expect(controls!.x + controls!.width).toBeLessThanOrEqual(width);
    const tabHeights = await page.getByRole("tablist", { name: "复核记录范围" }).getByRole("tab").evaluateAll((tabs) =>
      tabs.map((tab) => tab.getBoundingClientRect().height));
    expect(Math.max(...tabHeights) - Math.min(...tabHeights)).toBeLessThanOrEqual(1);
    await page.screenshot({ path: info.outputPath(`${label}.png`), fullPage: true });
    const after: ReconciliationResponse = await (await request.get("/api/reconciliation")).json();
    expect(after.version).toEqual(before.version);
    expect(after.event_watermark).toEqual(before.event_watermark);
  });
}

test("reconciliation server paging, history filters and source return retain URL selection", async ({ page, request }) => {
  const before: ReconciliationResponse = await (await request.get("/api/reconciliation?reason=stale&scope=attention&offset=30&limit=30")).json();
  expect(before.items.length).toBeGreaterThan(0);
  expect(before.items.length).toBeLessThan(30);
  await page.goto("/reconciliation?reason=stale&offset=30");
  await expect(page.getByRole("list", { name: "复核对账记录" }).locator(":scope > li")).toHaveCount(before.items.length);
  await expect(page.getByRole("button", { name: "下一页复核记录" })).toBeDisabled();
  const source = page.getByRole("link", { name: "查看当前来源", exact: true }).first();
  await source.click();
  await expect(page).toHaveURL(/\/videos\//);
  await page.getByRole("link", { name: "返回对账", exact: true }).click();
  await expect(page).toHaveURL(/\/reconciliation\?/);
  expect(new URL(page.url()).searchParams.get("reason")).toBe("stale");
  expect(new URL(page.url()).searchParams.get("offset")).toBe("30");
  await page.getByRole("button", { name: "上一页复核记录" }).click();
  await expect(page.getByRole("list", { name: "复核对账记录" }).locator(":scope > li")).toHaveCount(30);
  await page.getByRole("tab", { name: `历史 ${before.summary.historical}` }).click();
  const history: ReconciliationResponse = await (await request.get("/api/reconciliation?reason=stale&scope=history")).json();
  await expect(page.getByRole("list", { name: "复核对账记录" }).locator(":scope > li")).toHaveCount(history.items.length);
  expect(new URL(page.url()).searchParams.get("offset")).toBeNull();
  await expect(page.getByText("这条记录已有后续决定；原记录继续保留。")).toBeVisible();
});

test("missing source stays text; Markdown export contains every matching record beyond one page", async ({ page, request }, info) => {
  const missing: ReconciliationResponse = await (await request.get("/api/reconciliation?reason=missing&scope=attention")).json();
  const unavailable = missing.items.find((item) => item.target_url === null && item.source_links.length === 0);
  expect(unavailable).toBeDefined();
  await page.goto("/reconciliation?reason=missing");
  const row = page.locator(`[data-reconciliation-record="${unavailable!.record_id}"]`);
  await expect(row).toBeVisible();
  await expect(row.getByRole("link")).toHaveCount(0);
  await expect(row.getByText("当前没有可定位的可读来源。")).toBeVisible();
  await expect(page.getByRole("button", { name: /自动重绑|一键处理|保存个人决定/ })).toHaveCount(0);
  await page.goto("/reconciliation?scope=all");
  const all: ReconciliationResponse = await (await request.get("/api/reconciliation?scope=all&limit=100")).json();
  expect(all.total).toBeGreaterThan(30);
  await expect(page.getByRole("button", { name: "导出 Markdown" })).toBeEnabled();
  const downloading = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出 Markdown" }).click();
  const downloaded = await downloading;
  expect(downloaded.suggestedFilename()).toBe("reconciliation.md");
  const destination = info.outputPath("reconciliation.md");
  await downloaded.saveAs(destination);
  const markdown = await readFile(destination, "utf8");
  expect(markdown).toContain(`# 复核对账`);
  expect(markdown).toContain(`本次导出：${all.total} 条`);
  expect(markdown.match(/^## \d+\./gm)).toHaveLength(all.total);
  expect(markdown).not.toContain(manifest.auth_token);
  const after: ReconciliationResponse = await (await request.get("/api/reconciliation?scope=all&limit=100")).json();
  expect(after.version).toEqual(all.version);
  expect(after.event_watermark).toEqual(all.event_watermark);
});

test("reconciliation APIs and Markdown export require the existing ordinary token", async () => {
  const anonymous = await playwrightRequest.newContext({ baseURL: manifest.base_url,
    extraHTTPHeaders: {}, storageState: { cookies: [], origins: [] } });
  try {
    for (const endpoint of ["/api/reconciliation", "/api/reconciliation/export"]) {
      const response = await anonymous.get(endpoint);
      expect(response.status()).toBe(401);
    }
  } finally { await anonymous.dispose(); }
});

test("synthetic transport failure preserves real loaded records and the requested filter", async ({ page }) => {
  await page.goto("/reconciliation");
  const list = page.getByRole("list", { name: "复核对账记录" });
  await expect(list.locator(":scope > li")).toHaveCount(30);
  const original = await list.locator(":scope > li").first().getAttribute("data-reconciliation-record");
  // Fault injection affects transport only, after the first REAL backend read.
  await page.route("**/api/reconciliation?**", async (route) => {
    if (new URL(route.request().url()).searchParams.get("reason") === "missing") await route.abort("failed");
    else await route.continue();
  });
  await page.getByRole("button", { name: /^目标已缺失 / }).click();
  await expect(page.getByText(/后台刷新失败/)).toBeVisible();
  await expect(page.getByRole("button", { name: /^目标已缺失 / })).toHaveAttribute("aria-pressed", "true");
  await expect(list.locator(":scope > li").first()).toHaveAttribute("data-reconciliation-record", original!);
  await expect(page.getByText(/读取失败，仍展示上次结果/)).toBeVisible();
  await expect(page.getByRole("button", { name: "导出 Markdown" })).toBeDisabled();
});
