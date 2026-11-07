import { test, expect } from "./qa-fixtures";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { readQaManifest } from "./qa-manifest";

test("真实预算 API 保存零值与未设额度，手机表单保持可用", async ({ page, request }) => {
  const initial = await (await request.get("/api/budget")).json();
  const seeded = await request.post("/api/config/budget", { data: {
    expected_settings_revision: initial.settings.settings_revision,
    updates: { model_daily_attempts: 7, download_daily_videos: null },
  } });
  expect(seeded.ok()).toBeTruthy();
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/collect");
  const panel = page.getByRole("region", { name: "预算与等待任务" });
  await expect(panel.getByText("本机账本", { exact: false })).toBeVisible();
  await panel.getByText("预算设置", { exact: true }).click();
  await expect(page.getByLabel("总用量口径", { exact: true })).toBeDisabled();
  const daily = page.getByLabel("每日新视频名额", { exact: true });
  const model = page.getByLabel("每日模型请求次数", { exact: true });
  await expect(model).toHaveValue("7");
  await daily.fill("0");
  await model.fill("");
  const saved = page.waitForResponse((response) => response.url().endsWith("/api/config/budget")
    && response.request().method() === "POST");
  await page.getByRole("button", { name: "保存预算设置", exact: true }).click();
  expect((await saved).status()).toBe(200);
  const actual = await (await request.get("/api/budget")).json();
  expect(actual.settings.effective_policy.download_daily_videos).toBe(0);
  expect(actual.settings.effective_policy.model_daily_attempts).toBeNull();
  await page.reload();
  await panel.getByText("预算设置", { exact: true }).click();
  await expect(daily).toHaveValue("0");
  await expect(model).toHaveValue("");
  const bounds = await daily.boundingBox();
  expect(bounds).not.toBeNull();
  expect(bounds!.x).toBeGreaterThanOrEqual(0);
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(375);
  await page.screenshot({ path: `${test.info().outputDir}/budget-375.png`, fullPage: true });
});

test("真实设置更新使旧表单保留草稿并拒绝覆盖", async ({ page, request }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/collect");
  const panel = page.getByRole("region", { name: "预算与等待任务" });
  await panel.getByText("预算设置", { exact: true }).click();
  const daily = page.getByLabel("每日新视频名额", { exact: true });
  await daily.fill("2");
  const initial = await (await request.get("/api/budget")).json();
  const changed = await request.post("/api/config/budget", { data: {
    expected_settings_revision: initial.settings.settings_revision,
    updates: { model_daily_attempts: 9 },
  } });
  expect(changed.status()).toBe(200);
  const rejected = page.waitForResponse((response) => response.url().endsWith("/api/config/budget")
    && response.request().method() === "POST");
  await page.getByRole("button", { name: "保存预算设置", exact: true }).click();
  expect((await rejected).status()).toBe(409);
  await expect(daily).toHaveValue("2");
  const actual = await (await request.get("/api/budget")).json();
  expect(actual.settings.effective_policy.download_daily_videos).toBe(0);
  expect(actual.settings.effective_policy.model_daily_attempts).toBe(9);
  await page.screenshot({ path: `${test.info().outputDir}/budget-stale-1280.png`, fullPage: true });
});

test("普通等待任务的暂停恢复使用真实 API 且保留未知账本", async ({ page, request }) => {
  const fixture = JSON.parse(readFileSync(join(readQaManifest().root, "budget-task-fixture.json"), "utf8"));
  const before = await (await request.get("/api/budget")).json();
  expect(before.usage.all_history.unknown_attempts).toBeGreaterThan(0);
  await page.goto("/collect");
  const panel = page.getByRole("region", { name: "处理任务" });
  await expect(panel.getByText("等待条件允许", { exact: true })).toBeVisible();
  const paused = page.waitForResponse((response) => response.url().endsWith(`/api/jobs/${fixture.job_id}/pause`));
  await panel.getByRole("button", { name: "暂停任务", exact: true }).click();
  expect((await paused).status()).toBe(202);
  await expect(panel.getByText("已手动暂停", { exact: true })).toBeVisible();
  const resumed = page.waitForResponse((response) => response.url().endsWith(`/api/jobs/${fixture.job_id}/resume`));
  await panel.getByRole("button", { name: "请求继续任务", exact: true }).click();
  expect((await resumed).status()).toBe(202);
  const task = await (await request.get(`/api/tasks/${fixture.job_id}`)).json();
  expect(task.manual_paused).toBe(false);
  const after = await (await request.get("/api/budget")).json();
  expect(after.usage.all_history.unknown_attempts).toBe(before.usage.all_history.unknown_attempts);
  expect(after.usage.tokens.total_tokens.reported).toBeNull();
});
