import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "./qa-fixtures";
import { readQaManifest } from "./qa-manifest";

const manifest = readQaManifest() as ReturnType<typeof readQaManifest> & {
  derivation_fixture?: { synthetic: boolean; version: number; relations: { state: string; conflict_id: string }[] };
};
function judgeCalls() {
  const path = join(manifest.root, "derivation-model-calls.json");
  return existsSync(path) ? JSON.parse(readFileSync(path, "utf8")).calls as number : 0;
}

test.describe.serial("P04 actual relation provenance and explicit maintenance", () => {
  test.skip(!manifest.derivation_fixture, "Requires --derivation fixture");
  test.beforeAll(async ({ request }) => {
    expect(manifest.derivation_fixture?.synthetic).toBe(true);
    const response = await request.get("/api/conflicts");
    expect(response.ok()).toBe(true);
    const rows = (await response.json()).conflicts;
    for (const expected of manifest.derivation_fixture!.relations) {
      expect(rows.find((row: { conflict_id: string }) => row.conflict_id === expected.conflict_id).derivation.state).toBe(expected.state);
    }
    expect(judgeCalls()).toBe(0);
  });

  for (const [label, width, height, theme] of [
    ["desktop-light", 1280, 900, "light"], ["desktop-dark", 1280, 900, "dark"],
    ["mobile-light", 375, 812, "light"], ["mobile-dark", 375, 812, "dark"],
  ] as const) {
    test(`four states retain original analysis without model calls: ${label}`, async ({ page }, info) => {
      await page.setViewportSize({ width, height });
      await page.addInitScript((value) => localStorage.setItem("reno-theme", value), theme);
      await page.goto("/conflicts");
      const labels = { active: "依据匹配", stale: "依据已变化", missing: "依据成员缺失", unknown: "判定依据未知" };
      for (const [state, text] of Object.entries(labels)) {
        const card = page.getByRole("article", { name: `争议 cfl_p04_${state}`, exact: true });
        await expect(card.getByText(text, { exact: true })).toBeVisible();
      }
      const stale = page.getByRole("article", { name: "争议 cfl_p04_stale", exact: true });
      await expect(stale.getByText(/仅记录个人意见/)).toBeVisible();
      await stale.scrollIntoViewIfNeeded();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      expect(await page.locator("html").evaluate((node) => node.classList.contains("dark"))).toBe(theme === "dark");
      await page.screenshot({ path: info.outputPath(`${label}.png`), fullPage: true });
      expect(judgeCalls()).toBe(0);
    });
  }

  test("ask and compare preserve historical caveats without rejudging", async ({ page }, info) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto("/ask");
    await page.getByLabel("问题输入框").fill("P04 防水方案甲和乙有什么差别？");
    await page.getByRole("button", { name: "发送问题" }).click();
    await expect(page.getByLabel("历史关联待核对")).toBeVisible();
    await expect(page.getByText(/人工关联检查回答/)).toBeVisible();
    await page.screenshot({ path: info.outputPath("ask-history.png"), fullPage: true });
    await page.goto("/compare");
    await page.getByLabel("对比对象").fill("P04甲 P04乙");
    await page.getByRole("button", { name: "对比", exact: true }).click();
    await expect(page.getByLabel("历史关联待核对")).toBeVisible();
    await expect(page.getByLabel("依据匹配的分歧")).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: info.outputPath("compare-history.png"), fullPage: true });
    expect(judgeCalls()).toBe(0);
  });

  test("latest static report download retains unresolved relation notice", async ({ page, request }, info) => {
    const meta = (await (await request.get("/api/reports/meta")).json()).reports.conflicts;
    expect(meta.status).toBe("latest");
    expect(meta.derivation_summary.conflicts.stale).toBe(1);
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto("/reports?r=conflicts");
    await expect(page.getByLabel("报告关联状态")).toContainText("关联待更新");
    const download = page.waitForEvent("download");
    await page.getByTitle("下载 .md", { exact: true }).click();
    const path = info.outputPath("conflicts.md");
    await (await download).saveAs(path);
    const content = readFileSync(path, "utf8");
    expect(content).toContain("当前关联判定依据：需要更新");
    expect(content).toContain("历史判定：依据已变化");
    await page.screenshot({ path: info.outputPath("report-history.png"), fullPage: true });
    expect(judgeCalls()).toBe(0);
  });

  test("personal opinion survives explicit real judge task with fresh bindings", async ({ page, request }, info) => {
    await page.goto("/conflicts");
    const card = page.getByRole("article", { name: "争议 cfl_p04_unknown", exact: true });
    await card.getByRole("button", { name: "两者各适用", exact: true }).click();
    await card.getByRole("button", { name: "提交决策:两者各适用", exact: true }).click();
    await expect(card).toHaveCount(0);
    const reviewUrl = "/api/reviews?kind=conflict&target_id=cfl_p04_unknown";
    const old = (await (await request.get(reviewUrl)).json()).reviews;
    expect(old).toHaveLength(1);
    expect(judgeCalls()).toBe(0);
    const submitted = page.waitForResponse((response) => response.url().endsWith("/api/tasks") && response.request().method() === "POST");
    await page.getByRole("button", { name: "更新争议", exact: true }).click();
    const response = await submitted;
    expect(response.status()).toBe(202);
    const task = await response.json();
    await expect.poll(async () => (await (await request.get(`/api/tasks/${task.task_id}`)).json()).status).toBe("done");
    const finished = (await (await request.get(`/api/tasks/${task.task_id}`)).json());
    expect(finished.result.model).toBe("qa-derivation");
    const rows = (await (await request.get("/api/conflicts")).json()).conflicts;
    expect(rows.length).toBeGreaterThan(0);
    expect(rows.every((row: { derivation: { state: string } }) => row.derivation.state === "active")).toBe(true);
    expect((await (await request.get(reviewUrl)).json()).reviews.map((row: { event_id: string }) => row.event_id))
      .toEqual(old.map((row: { event_id: string }) => row.event_id));
    expect(judgeCalls()).toBe(1);
    await expect(page.getByRole("article").filter({ hasText: "P04显式重算的合成判断" }).first()).toBeVisible();
    await expect(page.getByText("判定依据未知", { exact: true })).toHaveCount(0);
    await page.screenshot({ path: info.outputPath("judge-completed.png"), fullPage: true });
  });
});
