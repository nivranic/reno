import { readFile } from "node:fs/promises";
import { expect, test } from "./qa-fixtures";

// The backend and commits are real isolated FastAPI operations. Capability
// transitions below are explicit browser-only fixtures, not an H01 handoff.
test("a committed save with a lost response keeps its draft and original receipt across a capability refresh", async ({ page, request }, info) => {
  await page.setViewportSize({ width: 375, height: 812 });
  const title = `原请求草稿-${crypto.randomUUID().slice(0, 8)}`;
  let readonly = false; let posts = 0; let operation = "";
  await page.route("**/api/capabilities", async (route) => {
    const response = await route.fetch(); const cap = await response.json();
    await route.fulfill({ response, json: readonly ? { ...cap, personal_write: false, personal_upload: false,
      field_checks_write: false, personal_reason: "测试中的只读能力变化" } : cap });
  });
  let committed!: () => void; const committedSignal = new Promise<void>((resolve) => { committed = resolve; });
  let release!: () => void; const releaseSignal = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/projects/*/sources", async (route) => {
    if (route.request().method() !== "POST") { await route.continue(); return; }
    posts++; operation = route.request().postDataJSON().operation_id;
    const response = await route.fetch(); expect(response.ok()).toBe(true);
    committed(); await releaseSignal; await route.fulfill({ response });
  });
  await page.goto("/sources");
  await page.getByRole("button", { name: "添加依据", exact: true }).click();
  const edit = page.getByRole("dialog", { name: "记录一份依据", exact: true });
  await edit.getByLabel("资料标题", { exact: true }).fill(title);
  await edit.getByLabel("原文摘录", { exact: true }).fill("原填写内容保留，取消连接不证明保存失败。");
  await edit.getByRole("button", { name: "保存来源记录", exact: true }).click();
  await committedSignal;
  readonly = true;
  await page.evaluate(() => window.dispatchEvent(new CustomEvent("reno-personal-version-changed")));
  await expect(edit).not.toBeVisible();
  await expect(page.getByRole("button", { name: "添加依据", exact: true })).toBeDisabled();
  release();
  await expect(page.getByText("来源记录已保存，未自动认证规范现行性。", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "暂存草稿（1）", exact: true }).click();
  const archive = page.getByRole("dialog", { name: "暂存草稿", exact: true });
  await archive.locator("summary").first().click();
  await expect(archive.locator("pre")).toContainText(title);
  const downloading = page.waitForEvent("download");
  await archive.getByRole("button", { name: "下载这份草稿", exact: true }).click();
  const download = await downloading; const path = info.outputPath("source-original-draft.md");
  await download.saveAs(path);
  const text = await readFile(path, "utf8");
  expect(text).toContain(title); expect(text).toContain("保存结果尚未确认"); expect(text).not.toContain(operation);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("draft-recovery-mobile.png"), fullPage: true });
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "暂存草稿（1）", exact: true })).toBeFocused();
  const receipt = await request.get(`/api/operations/${encodeURIComponent(operation)}`);
  expect(receipt.ok()).toBe(true); expect((await receipt.json()).response.source.title).toBe(title);
  readonly = false;
  await page.evaluate(() => window.dispatchEvent(new CustomEvent("reno-personal-version-changed")));
  await expect(page.getByRole("button", { name: "添加依据", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "添加依据", exact: true }).click();
  await expect(edit.getByLabel("资料标题", { exact: true })).toHaveValue(title);
  await edit.getByRole("button", { name: "查询提交结果", exact: true }).click();
  await expect(edit).not.toBeVisible();
  expect(posts).toBe(1);
  await expect(page.getByRole("button", { name: /暂存草稿|待确认保存/ })).toHaveCount(0);
});

for (const sample of [
  { route: "/decisions", open: "记一个待决问题", dialog: "保存个人决定", input: "决定标题", width: 1280, dark: true },
  { route: "/checks", open: "新建现场清单", dialog: "建立一次现场清单", input: "清单标题", width: 375, dark: false },
] as const) {
  test(`${sample.route} keeps unsent text quarantined after an explicit capability transition`, async ({ page }, info) => {
    await page.setViewportSize({ width: sample.width, height: 812 });
    let switched = false; let posts = 0;
    await page.route("**/api/capabilities", async (route) => {
      const response = await route.fetch(); const cap = await response.json();
      await route.fulfill({ response, json: switched ? { ...cap, primary_epoch: cap.primary_epoch + 1,
        personal_write: false, personal_upload: false, field_checks_write: false, personal_reason: "测试中的任期变化" } : cap });
    });
    page.on("request", (request) => { if (["POST", "PATCH"].includes(request.method()) && /\/api\/(projects|decision-cards|checklist)/.test(new URL(request.url()).pathname)) posts++; });
    await page.goto(sample.route);
    if (sample.dark) await page.evaluate(() => document.documentElement.classList.add("dark"));
    await page.getByRole("button", { name: sample.open, exact: true }).click();
    const edit = page.getByRole("dialog", { name: sample.dialog, exact: true });
    const text = `${sample.input}：未提交中文连续输入`;
    await edit.getByLabel(sample.input, { exact: true }).fill(text);
    switched = true;
    await page.evaluate(() => window.dispatchEvent(new CustomEvent("reno-personal-version-changed")));
    await expect(edit).not.toBeVisible();
    await page.getByRole("button", { name: "暂存草稿（1）", exact: true }).click();
    const archive = page.getByRole("dialog", { name: "暂存草稿", exact: true });
    await archive.locator("summary").first().click();
    await expect(archive.locator("pre")).toContainText(text);
    expect(posts).toBe(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: info.outputPath("quarantined-draft.png"), fullPage: true });
  });
}

test("project edits do not populate a new primary epoch and remain available as explicit local drafts", async ({ page }, info) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  let mode: "original" | "readonly" | "next" = "original"; let writes = 0;
  await page.route("**/api/capabilities", async (route) => {
    const response = await route.fetch(); const cap = await response.json();
    await route.fulfill({ response, json: mode === "original" ? cap : { ...cap, primary_epoch: cap.primary_epoch + 1,
      personal_write: mode === "next", personal_upload: mode === "next", field_checks_write: mode === "next" } });
  });
  page.on("request", (request) => { if (request.method() === "PATCH" && /\/api\/projects\//.test(request.url())) writes++; });
  await page.goto("/project");
  const originalName = await page.getByLabel("项目名称", { exact: true }).inputValue();
  const unsent = "绍兴未提交的项目名称";
  await page.getByLabel("项目名称", { exact: true }).fill(unsent);
  mode = "readonly";
  await page.evaluate(() => window.dispatchEvent(new CustomEvent("reno-personal-version-changed")));
  await expect(page.getByLabel("项目名称", { exact: true })).toHaveCount(0);
  mode = "next";
  await page.evaluate(() => window.dispatchEvent(new CustomEvent("reno-personal-version-changed")));
  await expect(page.getByLabel("项目名称", { exact: true })).toHaveValue(originalName);
  await page.getByRole("button", { name: "暂存草稿（1）", exact: true }).click();
  const archive = page.getByRole("dialog", { name: "暂存草稿", exact: true });
  await archive.locator("summary").first().click();
  await expect(archive.locator("pre")).toContainText(unsent);
  expect(writes).toBe(0);
  await page.screenshot({ path: info.outputPath("project-old-epoch-draft-desktop.png"), fullPage: true });
});
