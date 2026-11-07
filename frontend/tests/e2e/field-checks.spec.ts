import { expect, test } from "./qa-fixtures";
import type { APIRequestContext } from "@playwright/test";

async function prepare(request: APIRequestContext, title: string) {
  const cap = await (await request.get("/api/capabilities")).json(); expect(cap.field_checks).toBe(true);
  const { project } = await (await request.get("/api/projects/default")).json();
  const response = await request.post(`/api/projects/${project.project_id}/checklists`, { data: { operation_id: crypto.randomUUID(), primary_epoch: cap.primary_epoch, changes: { title, space: "卫生间", stage: "泥瓦", items: [
    { title: "个人要求：表面清洁", conditions: { 空间: "卫生间" }, method_text: "现场目视确认", basis_kind: "personal_requirement", criteria_state: "known", criteria: { text: "表面无可见垃圾", conditions_confirmed: true, requirement_confirmed: true, required_observations: [{ key: "observation", label: "现场观察", kind: "text" }] }, refs: [] },
    { title: "防水阈值待核实", conditions: {}, method_text: "先记录现状", basis_kind: "observation_only", criteria_state: "needs_verification", criteria: { text: "", conditions_confirmed: false, requirement_confirmed: false, required_observations: [{ key: "observation", label: "现场观察", kind: "text" }] }, refs: [] },
  ] } } }); expect(response.ok()).toBe(true);
  return { ...(await response.json()), project, epoch: cap.primary_epoch };
}

test("field records preserve fail → remediation → pass → void and bind each inspection to its criteria version", async ({ page, request }, info) => {
  const { checklist } = await prepare(request, `现场流程-${Date.now()}`);
  await page.setViewportSize({ width: 375, height: 812 }); await page.goto(`/checks/${checklist.instance_id}`);
  const card = page.getByTestId("check-item").first(); const unknown = page.getByTestId("check-item").nth(1);
  await card.getByText("填写核对、整改或复检记录", { exact: true }).click();
  await card.getByLabel("本项结果").selectOption("fail"); await card.getByLabel("现场观察", { exact: true }).fill("表面有包装碎片"); await card.getByLabel("记录说明").fill("首次核对发现残留物");
  const save = async () => { const result = page.waitForResponse((res) => /\/api\/checklist-items\/[^/]+\/checks$/.test(res.url()) && res.request().method() === "POST"); await card.getByRole("button", { name: "保存本次记录" }).click(); const response = await result; expect(response.ok()).toBe(true); return response.json(); };
  await save(); await expect(card.getByText("不符合本项", { exact: true }).first()).toBeVisible();
  await card.getByLabel("记录类型").selectOption("remediation"); await card.getByLabel("记录说明").fill("已完成清理，等待复检"); await save();
  await card.getByLabel("记录类型").selectOption("inspection"); await card.getByLabel("本项结果").selectOption("pass"); await card.getByLabel("现场观察", { exact: true }).fill("复检表面无可见垃圾"); await card.getByLabel("记录说明").fill("本项复检符合个人要求"); const passed = await save();
  expect(passed.item.current_result).toBe("pass");
  await unknown.getByText("填写核对、整改或复检记录", { exact: true }).click(); await expect(unknown.getByLabel("本项结果").locator('option[value="pass"]')).toBeDisabled();
  await unknown.getByLabel("本项结果").selectOption("not_applicable"); await expect(unknown.getByRole("button", { name: "保存本次记录" })).toBeDisabled(); await unknown.getByLabel("记录说明").fill("本次不涉及该区域，不套用未核实阈值"); const naResponse = page.waitForResponse((res) => res.url().endsWith("/checks") && res.request().method() === "POST"); await unknown.getByRole("button", { name: "保存本次记录" }).click(); expect((await naResponse).ok()).toBe(true);
  await expect(page.getByText(/本清单当前项目已完成/)).toBeVisible();
  await card.getByLabel("记录类型").selectOption("void"); await card.getByLabel("作废哪条记录").selectOption(passed.check.check_id); await card.getByLabel("记录说明").fill("复检位置写错，作废本条并重新核对"); const voided = await save(); expect(voided.item.current_result).toBe("fail");
  await expect(page.getByText(/仍有未核对、需复看或待处理项目/)).toBeVisible();
  await card.getByText(/检查、整改与复检历史/).click(); await expect(card.getByText("首次核对发现残留物", { exact: true })).toBeVisible(); await expect(card.getByText("已完成清理，等待复检", { exact: true })).toBeVisible(); await expect(card.getByText(/当时记录为符合本项.*已作废/)).toBeVisible();
  await card.getByRole("button", { name: "修订本项" }).click(); await page.getByRole("dialog").getByLabel("检查方法", { exact: true }).fill("修订后按指定区域目视并拍照"); const revisionResponse = page.waitForResponse((res) => /\/api\/checklist-items\/[^/]+$/.test(res.url()) && res.request().method() === "PATCH"); await page.getByRole("button", { name: "确认修订并重新核对" }).click(); expect((await revisionResponse).ok()).toBe(true); await expect(card.getByText(/条目版本 2/).first()).toBeVisible();
  await expect(card.getByText(/历史判据版本/).first()).toBeVisible();
  await expect(card.getByRole("button", { name: "保留文字并按新版重新核对" })).toBeEnabled();
  await card.getByRole("button", { name: "保留文字并按新版重新核对" }).click();
  await expect(card.getByLabel("现场观察", { exact: true })).toBeEnabled();
  for (const [width, height, dark] of [[375, 812, false], [768, 1024, true], [1440, 900, false]] as const) {
    await page.setViewportSize({ width, height }); await page.evaluate((isDark) => document.documentElement.classList.toggle("dark", isDark), dark); await card.scrollIntoViewIfNeeded();
    await page.waitForTimeout(200); // wait for the theme's 150ms color transition before visual evidence
    await expect(card.getByLabel("现场观察", { exact: true })).toBeEnabled();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true); await page.screenshot({ path: info.outputPath(`field-${width}-${dark ? "dark" : "light"}.png`), fullPage: true });
  }
});

test("photo refresh asks for reselection; uploaded evidence and offline text drafts only complete after a receipt", async ({ page, request, context }) => {
  const { checklist, project, epoch } = await prepare(request, `照片草稿-${Date.now()}`);
  const sourceResponse = await request.post(`/api/projects/${project.project_id}/sources`, { data: { operation_id: crypto.randomUUID(), primary_epoch: epoch, changes: { source_type: "site_photo", title: `现场照片-${Date.now()}`, excerpt: "现场合成样本照片", scope: {}, verification_state: "unverified", validity: "unknown" } } }); expect(sourceResponse.ok()).toBe(true); const { source } = await sourceResponse.json();
  const frame = await request.get("/api/frame/BVQA00000001/1000"); expect(frame.ok()).toBe(true); const file = { name: "现场样本.jpg", mimeType: "image/jpeg", buffer: await frame.body() };
  await page.setViewportSize({ width: 375, height: 812 }); await page.goto(`/checks/${checklist.instance_id}`);
  let card = page.getByTestId("check-item").first(); await card.getByText("填写核对、整改或复检记录", { exact: true }).click(); await card.getByLabel("记录说明").fill("尚未提交的现场文字草稿");
  await card.getByText(/照片与附件/).click(); await card.getByLabel("附件来源记录").selectOption(source.source_id); await card.getByLabel("选择附件").setInputFiles(file); await expect(card.getByAltText("待上传照片预览")).toBeVisible();
  await page.reload(); card = page.getByTestId("check-item").first(); await card.getByText("填写核对、整改或复检记录", { exact: true }).click(); await expect(card.getByLabel("记录说明")).toHaveValue("尚未提交的现场文字草稿"); await card.getByText(/照片与附件/).click(); await expect(card.getByText(/请重新选择；文字草稿已恢复/)).toBeVisible();
  await card.getByLabel("选择附件").setInputFiles(file); const uploadResponse = page.waitForResponse((res) => res.url().endsWith("/attachments") && res.request().method() === "POST"); await card.getByRole("button", { name: "上传所选文件" }).click(); const upload = await uploadResponse; expect(upload.ok()).toBe(true); const { attachment } = await upload.json(); expect(attachment.state).toBe("ready");
  await expect(card.getByText("附件已就绪，可用于保存记录。", { exact: true })).toBeVisible();
  await context.setOffline(true); await card.getByRole("button", { name: "保存本次记录" }).click(); await expect(card.getByText(/尚未收到保存回执/)).toBeVisible(); await expect(card.getByLabel("记录说明")).toHaveValue("尚未提交的现场文字草稿"); await context.setOffline(false);
  await page.reload(); card = page.getByTestId("check-item").first(); await expect(card.getByText(/尚未收到保存回执/)).toBeVisible(); await card.getByRole("button", { name: "查询提交结果" }).click(); await expect(card.getByText(/尚未查到回执/)).toBeVisible();
  const savedResponse = page.waitForResponse((res) => res.url().endsWith("/checks") && res.request().method() === "POST"); await card.getByRole("button", { name: "使用同一请求重试" }).click(); const saved = await savedResponse; expect(saved.ok()).toBe(true); const value = await saved.json(); expect(value.check.attachment_refs).toContain(attachment.attachment_id); expect(value.check.note).toBe("尚未提交的现场文字草稿"); expect(value.checklist.state).toBe("active");
  await expect(card.getByText("已收到保存回执，历史与当前结果已更新。", { exact: true })).toBeVisible();
});

test("knowledge becomes an observation candidate and works without secure-context randomUUID", async ({ page, request }) => {
  await accessOnly(request);
  await page.addInitScript(() => Object.defineProperty(crypto, "randomUUID", { value: undefined, configurable: true }));
  await page.goto("/checks"); await page.getByRole("button", { name: "新建现场清单" }).click();
  await page.getByLabel("清单标题", { exact: true }).fill(`知识候选-${Date.now()}`);
  await page.getByText("从知识库选取现场观察候选", { exact: true }).click(); await page.getByLabel("搜索现场候选").fill("防水"); await page.getByRole("button", { name: "查找候选" }).click();
  await page.getByRole("button", { name: "加入为观察候选" }).first().click(); await expect(page.getByLabel("判据来源类型")).toHaveValue("video_suggestion"); await expect(page.getByLabel("判据核实状态")).toHaveValue("needs_verification");
  const creation = page.waitForResponse((res) => /\/api\/projects\/[^/]+\/checklists$/.test(res.url()) && res.request().method() === "POST"); await page.getByRole("button", { name: "建立清单", exact: true }).click(); const created = await creation; expect(created.ok()).toBe(true);
  const item = page.getByTestId("check-item").first(); await item.getByText("填写核对、整改或复检记录", { exact: true }).click(); await expect(item.getByLabel("本项结果").locator('option[value="pass"]')).toBeDisabled(); await item.getByLabel("现场观察", { exact: true }).fill("先记现场状况，判据另行核实");
  const submission = page.waitForResponse((res) => res.url().endsWith("/checks") && res.request().method() === "POST"); await item.getByRole("button", { name: "保存本次记录" }).click(); expect((await submission).ok()).toBe(true);
});
async function accessOnly(request: APIRequestContext) { const response = await (await request.get("/api/capabilities")).json(); expect(response.field_checks).toBe(true); }

test("manual source verification is explicit and source files upload through the authenticated UI", async ({ page, request }) => {
  await accessOnly(request); const title = `人工合成依据-${Date.now()}`;
  await page.setViewportSize({ width: 375, height: 812 }); await page.goto("/sources"); await page.getByRole("button", { name: "添加依据", exact: true }).click();
  const dialog = page.getByRole("dialog"); await dialog.getByLabel("资料类型").selectOption("standard_record"); await dialog.getByLabel("资料标题").fill(title); await dialog.getByLabel("原文定位").fill("合成样本第1页"); await dialog.getByLabel("原文摘录").fill("本条仅用于软件验收，不是真实标准。"); await dialog.getByLabel("原文核对", { exact: true }).selectOption("user_checked"); await dialog.getByLabel("有效性记录").selectOption("current");
  await dialog.getByLabel("版本", { exact: true }).fill("2026版·修订A");
  await dialog.getByLabel("发布日期", { exact: true }).fill("2026-01-01");
  await expect(dialog.getByRole("button", { name: "保存来源记录" })).toBeDisabled(); await dialog.getByLabel("核对时间", { exact: true }).fill("2026-09-01T09:00"); await dialog.getByLabel("核对说明").fill("已对照人工合成原文，仅验证记录流程。");
  const create = page.waitForResponse((res) => res.url().endsWith("/sources") && res.request().method() === "POST"); await dialog.getByRole("button", { name: "保存来源记录" }).click(); const createResponse = await create; expect(createResponse.ok()).toBe(true); const { source: createdSource } = await createResponse.json();
  expect(createdSource).toMatchObject({ edition: "2026版·修订A", published_on: "2026-01-01" });
  const card = page.locator("article").filter({ has: page.getByRole("heading", { name: title, exact: true }) }); await expect(card.getByText(/用户已核对原文.*用户记录为现行/)).toBeVisible();
  await card.getByLabel("选择附件").setInputFiles({ name: "暂不支持.heic", mimeType: "image/heic", buffer: Buffer.from("invalid") }); await expect(card.getByText(/HEIC 请先转换后重选/)).toBeVisible();
  const frame = await request.get("/api/frame/BVQA00000001/1000"); await card.getByLabel("选择附件").setInputFiles({ name: "合成现场.jpg", mimeType: "image/jpeg", buffer: await frame.body() });
  const upload = page.waitForResponse((res) => res.url().endsWith("/attachments") && res.request().method() === "POST"); await card.getByRole("button", { name: "上传所选文件" }).click(); expect((await upload).ok()).toBe(true); await expect(card.getByRole("button", { name: /合成现场.jpg.*查看文件/ })).toBeVisible();
  await card.getByRole("button", { name: "编辑记录" }).click();
  await expect(dialog.getByLabel("版本", { exact: true })).toHaveValue("2026版·修订A");
  await dialog.getByLabel("有效性记录").selectOption("withdrawn"); const update = page.waitForResponse((res) => /\/api\/sources\/[^/]+$/.test(res.url()) && res.request().method() === "PATCH"); await dialog.getByRole("button", { name: "保存来源记录" }).click(); expect((await update).ok()).toBe(true); await expect(card.getByText(/版本 2.*已撤销/)).toBeVisible();
  await card.getByRole("button", { name: "编辑记录" }).click(); await dialog.getByLabel("原文摘录").fill("需要保留的本次来源草稿");
  const capabilities = await (await request.get("/api/capabilities")).json();
  const external = await request.patch(`/api/sources/${createdSource.source_id}`, { data: { operation_id: crypto.randomUUID(), primary_epoch: capabilities.primary_epoch, base_revision: 2, changes: { excerpt: "另一个窗口保存的新原文" } } }); expect(external.ok()).toBe(true);
  await dialog.getByRole("button", { name: "保存来源记录" }).click(); await expect(dialog.getByText(/本次草稿仍保留/)).toBeVisible(); await expect(dialog.getByLabel("原文摘录")).toHaveValue("需要保留的本次来源草稿");
  await dialog.getByRole("button", { name: "读取最新来源版本" }).click(); await expect(dialog.getByText("另一个窗口保存的新原文", { exact: true })).toBeVisible();
  await dialog.getByRole("button", { name: "以最新版本为基础保留本次来源草稿" }).click(); await expect(dialog.getByLabel("原文摘录")).toHaveValue("需要保留的本次来源草稿");
  const merged = page.waitForResponse((res) => /\/api\/sources\/[^/]+$/.test(res.url()) && res.request().method() === "PATCH"); await dialog.getByRole("button", { name: "保存来源记录" }).click(); expect((await merged).ok()).toBe(true); await expect(card.getByText(/版本 4.*已撤销/)).toBeVisible(); await expect(card.getByText("需要保留的本次来源草稿", { exact: true })).toBeVisible();
});
