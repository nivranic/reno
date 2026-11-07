import { expect, test } from "./qa-fixtures";
import type { Page } from "@playwright/test";

async function ask(page: Page, question = "卫生间防水怎么做？") {
  await page.getByLabel("问题输入框").fill(question);
  const response = page.waitForResponse((res) => res.url().endsWith("/api/ask") && res.request().method() === "POST");
  await page.getByRole("button", { name: "发送问题" }).click();
  const result = await response;
  expect(result.ok()).toBeTruthy();
  await expect(page.getByText("正在检索知识库并生成回答…")).not.toBeVisible();
  return { body: await result.json(), request: result.request().postDataJSON() };
}

test.describe("reading continuity against the isolated real backend", () => {
  test("A03 the same question keeps distinct one-off condition snapshots and never borrows another condition's cached answer", async ({ page, context, request }) => {
    const projectBefore = await (await request.get("/api/projects/default")).json();
    const firstConditions = { 空间: "卫生间", 基层: "水泥" };
    const secondConditions = { 空间: "厨房", 基层: "木基层" };
    const setConditions = async (target: Page, conditions: typeof firstConditions) => {
      await target.getByRole("button", { name: "调整本次条件" }).click();
      const dialog = target.getByRole("dialog", { name: "本次使用的条件" });
      await dialog.getByLabel("条件空间", { exact: true }).fill(conditions.空间);
      await dialog.getByLabel("条件基层", { exact: true }).fill(conditions.基层);
      await dialog.getByRole("button", { name: "仅用于本次", exact: true }).click();
    };
    let posts = 0;
    page.on("request", (outgoing) => { if (outgoing.url().endsWith("/api/ask") && outgoing.method() === "POST") posts++; });
    await page.goto("/ask");
    await setConditions(page, firstConditions);
    const first = await ask(page);
    await setConditions(page, secondConditions);
    const second = await ask(page);
    expect(first.request.question).toBe(second.request.question);
    expect(first.request.context_snapshot).toEqual(firstConditions);
    expect(second.request.context_snapshot).toEqual(secondConditions);
    expect(first.body.context_used).toEqual(firstConditions);
    expect(second.body.context_used).toEqual(secondConditions);
    expect(first.body.context_signature).not.toBe(second.body.context_signature);
    const snapshots = page.locator('[data-reading-anchor] p').filter({ hasText: /^本回答使用：/ });
    await expect(snapshots).toHaveText([
      "本回答使用：空间：卫生间；基层：水泥（本次条件已修改，旧回答未重算）",
      "本回答使用：空间：厨房；基层：木基层",
    ]);
    await page.reload();
    await expect(snapshots).toHaveText([
      "本回答使用：空间：卫生间；基层：水泥（本次条件已修改，旧回答未重算）",
      "本回答使用：空间：厨房；基层：木基层",
    ]);
    expect(posts).toBe(2);

    // A fresh tab sends no conversation history, so this request exercises
    // the backend cache key after the first condition's cacheable response.
    const fresh = await context.newPage();
    try {
      await fresh.goto("/ask");
      await setConditions(fresh, secondConditions);
      const uncoupled = await ask(fresh);
      expect(uncoupled.request.history ?? []).toEqual([]);
      expect(uncoupled.body.context_used).toEqual(secondConditions);
      expect(uncoupled.body.context_signature).toBe(second.body.context_signature);
      expect(uncoupled.body.context_signature).not.toBe(first.body.context_signature);
    } finally {
      await fresh.close();
    }
    const projectAfter = await (await request.get("/api/projects/default")).json();
    expect(projectAfter.project.profile).toEqual(projectBefore.project.profile);
    expect(projectAfter.project.revision).toBe(projectBefore.project.revision);
  });

  test("ask keeps answer, draft, citations and nested scroll through Back, explicit return and refresh", async ({ page }, info) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    let posts = 0;
    page.on("request", (request) => { if (request.url().endsWith("/api/ask") && request.method() === "POST") posts++; });
    await page.goto("/ask");
    await ask(page);
    await ask(page, "卫生间防水高度应该是多少？");
    await ask(page, "闭水检查怎样判断合格？");
    await page.getByLabel("问题输入框").fill("我家还有地暖，先保留这条草稿");
    const scroll = page.locator('[data-reading-scroll="ask"]');
    const source = scroll.locator('a[href^="/videos/"]').nth(1);
    await source.scrollIntoViewIfNeeded();
    const before = await scroll.evaluate((element) => element.scrollTop);
    expect(before).toBeGreaterThan(0);
    await source.click();
    await expect(page.getByRole("link", { name: "返回问答" })).toBeVisible();
    // Seeking used to erase router state. A second seek must keep return state.
    await page.locator("article:visible").first().click();
    await expect(page.getByRole("link", { name: "返回问答" })).toBeVisible();
    await page.goBack();
    await expect(page.getByLabel("问题输入框")).toHaveValue("我家还有地暖，先保留这条草稿");
    await expect.poll(() => scroll.evaluate((element) => element.scrollTop)).toBeCloseTo(before, 0);
    await source.click();
    await page.getByRole("link", { name: "返回问答" }).click();
    await expect(page.getByLabel("问题输入框")).toHaveValue("我家还有地暖，先保留这条草稿");
    await expect.poll(() => scroll.evaluate((element) => element.scrollTop)).toBeCloseTo(before, 0);
    await page.reload();
    await expect(page.getByLabel("问题输入框")).toHaveValue("我家还有地暖，先保留这条草稿");
    await expect(scroll.locator('[data-reading-anchor]')).toHaveCount(6);
    await expect.poll(() => scroll.evaluate((element) => element.scrollTop)).toBeCloseTo(before, 0);
    expect(posts).toBe(3);
    await expect(page).toHaveURL(/\/ask$/);
    await page.screenshot({ path: info.outputPath("ask-restored-desktop.png"), fullPage: true });
  });

  test("comparison restores its result, edited draft, expansion and main scroll on a narrow screen", async ({ page }, info) => {
    await page.setViewportSize({ width: 375, height: 812 });
    let requests = 0;
    page.on("request", (request) => { if (request.url().includes("/api/compare?")) requests++; });
    await page.goto("/compare");
    await page.getByLabel("对比对象").fill("瓷砖 木地板");
    await page.getByRole("button", { name: "对比", exact: true }).click();
    await expect(page.locator('main li[data-reading-anchor]').first()).toBeVisible();
    const disclosures = page.locator("main details");
    await disclosures.first().locator("summary").click();
    await page.getByLabel("对比对象").fill("地板 水泥（尚未提交）");
    const source = page.locator('main a[href^="/videos/"]').last();
    await source.scrollIntoViewIfNeeded();
    const before = await page.locator("main").evaluate((element) => element.scrollTop);
    expect(before).toBeGreaterThan(0);
    await source.click();
    await page.getByRole("link", { name: "返回比较" }).click();
    await expect(page.getByLabel("对比对象")).toHaveValue("地板 水泥（尚未提交）");
    await expect(disclosures.first()).toHaveAttribute("open", "");
    await expect.poll(() => page.locator("main").evaluate((element) => element.scrollTop)).toBeCloseTo(before, 0);
    await page.reload();
    await expect(page.getByLabel("对比对象")).toHaveValue("地板 水泥（尚未提交）");
    await expect.poll(() => page.locator("main").evaluate((element) => element.scrollTop)).toBeCloseTo(before, 0);
    expect(requests).toBe(1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.evaluate(() => document.documentElement.classList.add("dark"));
    await page.screenshot({ path: info.outputPath("compare-restored-mobile-dark.png"), fullPage: true });
  });

  test("cold video links preserve other parameters and fall back to the inbox", async ({ page }) => {
    await page.goto("/videos/BVQA00000001?t=1000&view=evidence");
    await page.locator("article:visible").first().click();
    await expect(page).toHaveURL(/view=evidence/);
    await expect(page.getByRole("link", { name: "返回问答" })).toHaveCount(0);
    await page.locator("main header").getByRole("link", { name: "收件箱" }).click();
    await expect(page).toHaveURL(/\/$/);
  });

  test("refresh marks an in-flight model request interrupted; clear cancels without resurrecting it", async ({ page }) => {
    let posts = 0;
    page.on("request", (request) => { if (request.url().endsWith("/api/ask") && request.method() === "POST") posts++; });
    await page.goto("/ask");
    await page.getByLabel("问题输入框").fill("延迟回答：卫生间防水怎么做？");
    await page.getByRole("button", { name: "发送问题" }).click();
    await expect(page.getByText("正在检索知识库并生成回答…")).toBeVisible();
    await page.reload();
    await expect(page.getByText("上次请求已中断，未自动重新调用模型。")).toBeVisible();
    await page.waitForTimeout(1700); // deterministic QA model delay, no browser network mock
    expect(posts).toBe(1);
    await expect(page.locator('[data-reading-anchor]')).toHaveCount(1);
    await page.getByRole("button", { name: "清空会话" }).click();
    await page.getByLabel("问题输入框").fill("延迟回答：卫生间防水怎么做？");
    await page.getByRole("button", { name: "发送问题" }).click();
    await page.getByRole("button", { name: "清空会话" }).click();
    await page.waitForTimeout(1700);
    await expect(page.getByText("向装修知识库提问")).toBeVisible();
    await expect(page.locator('[data-reading-anchor]')).toHaveCount(0);
  });

  test("broken and disabled session storage degrades without a blank screen or automatic model call", async ({ page }) => {
    await page.goto("/ask");
    await ask(page);
    await page.addInitScript(() => {
      if (sessionStorage.getItem("qa-corruption-injected")) return;
      const key = Object.keys(sessionStorage).find((key) => key.startsWith("reno-reading:") && key.endsWith(":ask"));
      if (!key) throw new Error("Library-scoped snapshot was not written");
      sessionStorage.setItem(key, "{broken");
      sessionStorage.setItem("qa-corruption-injected", "yes");
    });
    await page.reload();
    await expect(page.getByText(/刷新后可能无法恢复/)).toBeVisible();
    await expect(page.getByText("向装修知识库提问")).toBeVisible();
    await page.addInitScript(() => Object.defineProperty(window, "sessionStorage", { get: () => { throw new DOMException("disabled", "SecurityError"); } }));
    await page.reload();
    await ask(page);
    await expect(page.locator('[data-reading-anchor]')).toHaveCount(2);
    await page.getByRole("button", { name: "清空会话" }).click();
    await expect(page.getByText("向装修知识库提问")).toBeVisible();
  });
});
