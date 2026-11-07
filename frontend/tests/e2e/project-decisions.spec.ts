import { expect, test } from "./qa-fixtures";
import type { APIRequestContext } from "@playwright/test";
import { readQaManifest } from "./qa-manifest";
import { execFileSync } from "node:child_process";
import path from "node:path";

async function access(request: APIRequestContext) {
  const cap = await (await request.get("/api/capabilities")).json();
  expect(cap.personal_write).toBe(true);
  const { project } = await (await request.get("/api/projects/default")).json();
  return { project, epoch: cap.primary_epoch };
}
const note = (title: string) => ({ title, question: "", context_snapshot: {}, alternatives: [], selected_option_id: null, decision_text: "", decision_reason: "", open_questions: [], content_snapshot: { kind: "note", text: "" }, state: "considering", evidence_refs: [] });

test("project conditions stay explicit; answers, comparison and atom cards all save personal decisions", async ({ page, request }, info) => {
  const { project } = await access(request);
  const unique = Date.now();
  await page.goto("/project");
  await page.getByLabel("条件空间", { exact: true }).fill("卫生间");
  await page.getByLabel("条件基层", { exact: true }).fill("水泥");
  const projectWrite = page.waitForResponse((res) => res.url().includes(`/api/projects/${project.project_id}`) && res.request().method() === "PATCH");
  await page.getByRole("button", { name: "保存项目资料" }).click(); expect((await projectWrite).ok()).toBe(true);
  await page.goto("/ask"); await page.getByRole("button", { name: "使用项目条件" }).click();
  await page.getByLabel("问题输入框").fill("卫生间防水怎么做？");
  const answer = page.waitForResponse((res) => res.url().endsWith("/api/ask"));
  await page.getByRole("button", { name: "发送问题" }).click(); expect((await answer).ok()).toBe(true);
  await page.getByRole("button", { name: "保存为个人决定", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("决定标题").fill(`问答决定-${unique}`); await dialog.getByLabel("个人状态").selectOption("adopted");
  await dialog.getByLabel("我的决定").fill("先核对基层和材料体系，再安排防水施工。");
  const saved = page.waitForResponse((res) => res.url().endsWith("/decision-cards") && res.request().method() === "POST");
  await dialog.getByRole("button", { name: "保存决定", exact: true }).click();
  const savedResponse = await saved; expect(savedResponse.ok()).toBe(true); const { card } = await savedResponse.json();
  expect(card.context_snapshot).toEqual({ 空间: "卫生间", 基层: "水泥" }); expect(card.evidence_refs.length).toBeGreaterThan(0);
  await page.getByRole("button", { name: "调整本次条件" }).click(); await page.getByLabel("条件空间", { exact: true }).fill("厨房"); await page.getByRole("button", { name: "仅用于本次" }).click();
  await expect(page.getByText(/本回答使用：空间：卫生间.*旧回答未重算/)).toBeVisible();
  const projectAfter = await (await request.get("/api/projects/default")).json(); expect(projectAfter.project.profile.conditions.空间).toBe("卫生间");
  await page.goto("/compare"); await page.getByLabel("对比对象").fill("瓷砖 木地板"); await page.getByRole("button", { name: "对比", exact: true }).click();
  await page.getByRole("button", { name: "保存比较决定" }).click(); await dialog.getByLabel("决定标题").fill(`比较决定-${unique}`); await dialog.getByLabel("个人状态").selectOption("adopted"); await dialog.getByLabel("选定方案").selectOption({ label: "瓷砖" });
  const comparisonSave = page.waitForResponse((res) => res.url().endsWith("/decision-cards") && res.request().method() === "POST"); await dialog.getByRole("button", { name: "保存决定", exact: true }).click(); expect((await comparisonSave).ok()).toBe(true);
  await page.goto("/videos/BVQA00000001"); await page.locator("article:visible").first().getByRole("button", { name: "保存个人决定", exact: true }).click();
  await dialog.getByLabel("决定标题").fill(`知识决定-${unique}`); const atomSave = page.waitForResponse((res) => res.url().endsWith("/decision-cards") && res.request().method() === "POST"); await dialog.getByRole("button", { name: "保存决定", exact: true }).click(); expect((await atomSave).ok()).toBe(true);
  await page.goto(`/decisions/${card.card_id}`); await expect(page.getByText("保存时的条件：空间：卫生间；基层：水泥")).toBeVisible();
  await page.setViewportSize({ width: 375, height: 812 }); expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("decision-mobile.png"), fullPage: true });
});

test("a real committed write with a lost response is recovered through its original receipt", async ({ page, request }) => {
  const { project } = await access(request); const title = `回执测试-${Date.now()}`;
  let operationId = ""; let writes = 0;
  await page.route(`**/api/projects/${project.project_id}/decision-cards`, async (route) => {
    if (route.request().method() !== "POST") { await route.continue(); return; }
    writes++; operationId = route.request().postDataJSON().operation_id;
    const committed = await route.fetch(); expect(committed.status()).toBe(201);
    await route.abort("failed"); // transport loss after a REAL server transaction, not a model/API mock
  });
  await page.goto("/decisions"); await page.getByRole("button", { name: "记一个待决问题" }).click();
  await page.getByLabel("决定标题").fill(title); await page.getByRole("button", { name: "保存决定", exact: true }).click();
  await expect(page.getByText(/尚未收到保存回执/)).toBeVisible();
  await page.getByRole("button", { name: "查询提交结果" }).click(); await expect(page.getByRole("dialog")).toHaveCount(0);
  const receipt = await (await request.get(`/api/operations/${operationId}`)).json(); expect(receipt.response.card.title).toBe(title); expect(writes).toBe(1);
  await expect(page.getByRole("heading", { name: title })).toBeVisible();
});

test("revision conflicts keep drafts; source changes and removal retain snapshots; history and export survive archive", async ({ page, request }) => {
  const { project, epoch } = await access(request);
  const sourceReply = await request.post(`/api/projects/${project.project_id}/sources`, { data: { operation_id: crypto.randomUUID(), primary_epoch: epoch, changes: { source_type: "manual_note", title: "原始依据", excerpt: "原始摘录", scope: {}, verification_state: "unverified", validity: "unknown" } } }); expect(sourceReply.ok()).toBe(true); const { source } = await sourceReply.json();
  const createReply = await request.post(`/api/projects/${project.project_id}/decision-cards`, { data: { operation_id: crypto.randomUUID(), primary_epoch: epoch, changes: { ...note(`冲突决定-${Date.now()}`), evidence_refs: [{ target_kind: "source", target_id: source.source_id, target_fingerprint: source.target_fingerprint }] } } }); expect(createReply.ok()).toBe(true); const { card } = await createReply.json();
  await page.goto(`/decisions/${card.card_id}`); await page.getByLabel("决定理由").fill("不能丢失的本地草稿");
  const remote = await request.patch(`/api/decision-cards/${card.card_id}`, { data: { operation_id: crypto.randomUUID(), primary_epoch: epoch, base_revision: 1, changes: { decision_reason: "另一个窗口的修改" } } }); expect(remote.ok()).toBe(true);
  await page.getByRole("button", { name: "保存修改" }).click(); await expect(page.getByText(/本次草稿仍保留/)).toBeVisible(); await expect(page.getByLabel("决定理由")).toHaveValue("不能丢失的本地草稿");
  await page.getByRole("button", { name: "查看最新记录", exact: true }).click(); await page.getByRole("button", { name: "以最新版本为基础保留本次草稿" }).click(); await page.getByRole("button", { name: "保存修改" }).click(); await expect(page.getByText(/版本 3 ·/)).toBeVisible();
  expect((await request.patch(`/api/sources/${source.source_id}`, { data: { operation_id: crypto.randomUUID(), primary_epoch: epoch, base_revision: 1, changes: { excerpt: "更新后的摘录" } } })).ok()).toBe(true);
  await page.reload(); await expect(page.getByText("内容已变化", { exact: true })).toBeVisible(); await page.getByRole("button", { name: "检查并更新依据" }).click(); await page.getByText("查看保存版本与当前版本").click(); await expect(page.getByRole("dialog").getByText(/原始摘录/)).toBeVisible(); await expect(page.getByRole("dialog").getByText(/更新后的摘录/)).toBeVisible(); await page.getByRole("button", { name: "确认更新为新版本" }).click(); await expect(page.getByText(/版本 4 ·/)).toBeVisible();
  // Simulate an externally lost source ONLY in this already verified disposable database.
  const manifest = readQaManifest();
  execFileSync(path.resolve("../.venv/Scripts/python.exe"), ["-c", "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); assert c.execute(\"select value from meta where key='qa_instance_id'\").fetchone()[0]==sys.argv[2]; c.execute('delete from source_record where source_id=?',(sys.argv[3],)); c.commit()", path.join(manifest.root, "data", "reno.db"), manifest.qa_instance_id, source.source_id]);
  await page.reload(); await expect(page.getByText("原来源已缺失", { exact: true })).toBeVisible(); await expect(page.getByText("原始依据", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "归档决定", exact: true }).click(); await expect(page.getByRole("button", { name: "恢复为考虑中" })).toBeVisible(); await page.getByRole("button", { name: "查看历史版本" }).click(); await expect(page.getByText(/版本 1 ·/)).toBeVisible();
  const download = page.waitForEvent("download"); await page.getByRole("button", { name: "导出 Markdown" }).click(); expect((await download).suggestedFilename()).toMatch(/\.md$/);
  const markdown = await (await request.get(`/api/decision-cards/${card.card_id}/export`)).text(); expect(markdown).toContain("不能丢失的本地草稿"); expect(markdown).not.toContain(manifest.auth_token);
});
