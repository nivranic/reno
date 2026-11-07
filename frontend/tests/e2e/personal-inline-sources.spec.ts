import { expect, test } from "./qa-fixtures";
import type { APIRequestContext, Page } from "@playwright/test";
import type { CheckItem, Checklist, DecisionCard, SourceRecord } from "../../src/lib/personal-api";

async function access(request: APIRequestContext) {
  const response = await request.get("/api/capabilities");
  expect(response.ok()).toBe(true);
  const cap = await response.json();
  expect(cap.personal_write).toBe(true);
  const projectResponse = await request.get("/api/projects/default");
  expect(projectResponse.ok()).toBe(true);
  const { project } = await projectResponse.json();
  return { projectId: project.project_id as string, epoch: cap.primary_epoch as number };
}

async function saveSource(page: Page, title: string, excerpt: string, sourceId?: string) {
  const dialog = page.getByRole("dialog", { name: sourceId ? "编辑来源记录" : "记录一份依据" });
  await dialog.getByLabel("资料标题", { exact: true }).fill(title);
  await dialog.getByLabel("原文摘录", { exact: true }).fill(excerpt);
  const pending = page.waitForResponse((response) => {
    const pathname = new URL(response.url()).pathname;
    return sourceId ? pathname === `/api/sources/${sourceId}` && response.request().method() === "PATCH"
      : /\/api\/projects\/[^/]+\/sources$/.test(pathname) && response.request().method() === "POST";
  });
  await dialog.getByRole("button", { name: "保存来源记录", exact: true }).click();
  const response = await pending;
  expect(response.ok()).toBe(true);
  const source = (await response.json()).source as SourceRecord;
  await expect(dialog).not.toBeVisible();
  return source;
}

async function prepareChecklist(request: APIRequestContext, title: string) {
  const { projectId, epoch } = await access(request);
  const response = await request.post(`/api/projects/${projectId}/checklists`, { data: {
    operation_id: crypto.randomUUID(), primary_epoch: epoch,
    changes: { title, space: "卫生间", stage: "泥瓦", items: [{ title: "个人要求：指定区域清洁",
      conditions: { 空间: "卫生间" }, method_text: "现场目视确认", basis_kind: "personal_requirement", criteria_state: "known",
      criteria: { text: "指定区域无可见垃圾", conditions_confirmed: true, requirement_confirmed: true,
        required_observations: [{ key: "surface", label: "现场观察", kind: "text" }] }, refs: [] }] },
  } });
  expect(response.ok()).toBe(true);
  return (await response.json()).checklist as Checklist;
}

async function saveItemRevision(page: Page, itemId: string) {
  const pending = page.waitForResponse((response) => new URL(response.url()).pathname === `/api/checklist-items/${itemId}` && response.request().method() === "PATCH");
  await page.getByRole("dialog", { name: "修订本项判据" }).getByRole("button", { name: "确认修订并重新核对", exact: true }).click();
  const response = await pending;
  expect(response.ok()).toBe(true);
  await expect(page.getByRole("dialog", { name: "修订本项判据" })).not.toBeVisible();
  return (await response.json()).item as CheckItem;
}

test("inline decision sources preserve the parent draft and custom tags survive combined filters and explicit return", async ({ page, request }, info) => {
  const { projectId, epoch } = await access(request);
  const unique = crypto.randomUUID().slice(0, 8);
  const title = `内联决定-${unique}`;
  const sourceTitle = `决定人工依据-${unique}`;
  const space = `自定义露台-${unique}`;
  const stage = `自定义复查-${unique}`;
  let parentWrites = 0;
  page.on("request", (outgoing) => { if (outgoing.method() === "POST" && outgoing.url().endsWith("/decision-cards")) parentWrites++; });
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/decisions");
  await page.getByRole("button", { name: "记一个待决问题", exact: true }).click();
  const parent = page.getByRole("dialog", { name: "保存个人决定", exact: true });
  await parent.getByLabel("决定标题", { exact: true }).fill(title);
  await parent.getByLabel("决定空间标签", { exact: true }).fill(space);
  await parent.getByLabel("决定阶段标签", { exact: true }).fill(stage);
  await parent.getByLabel("个人状态", { exact: true }).selectOption("adopted");
  await parent.getByLabel("我的决定", { exact: true }).fill("先保留现场方案与依据，再逐项确认");
  await parent.getByLabel("决定理由", { exact: true }).fill("父决定草稿不得在来源保存时丢失");
  await parent.getByRole("button", { name: "新建人工依据", exact: true }).click();
  const nested = page.getByRole("dialog", { name: "记录一份依据", exact: true });
  await nested.getByLabel("资料标题", { exact: true }).fill(sourceTitle);
  await nested.getByLabel("原文摘录", { exact: true }).fill("来源原文：仅为人工备注，不是规范认证");
  await nested.getByLabel("资料标题", { exact: true }).scrollIntoViewIfNeeded();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("inline-source-nested-mobile.png"), fullPage: true });
  const source = await saveSource(page, sourceTitle, "来源原文：仅为人工备注，不是规范认证");
  expect(parentWrites).toBe(0);
  await expect(parent.getByLabel("决定标题", { exact: true })).toHaveValue(title);
  await expect(parent.getByLabel("决定理由", { exact: true })).toHaveValue("父决定草稿不得在来源保存时丢失");
  await expect(parent.getByLabel("决定空间标签", { exact: true })).toHaveValue(space);
  const selection = parent.getByRole("checkbox", { name: new RegExp(sourceTitle) });
  await expect(selection).not.toBeChecked();
  await page.evaluate(() => document.documentElement.classList.add("dark"));
  await parent.getByLabel("决定标题", { exact: true }).scrollIntoViewIfNeeded();
  await page.waitForTimeout(200); // allow the existing theme transition before evidence capture
  await page.screenshot({ path: info.outputPath("inline-parent-draft-mobile-dark.png"), fullPage: true });
  await selection.check();
  const saved = page.waitForResponse((response) => response.url().endsWith("/decision-cards") && response.request().method() === "POST");
  await parent.getByRole("button", { name: "保存决定", exact: true }).click();
  const saveResponse = await saved;
  expect(saveResponse.ok()).toBe(true);
  const card = (await saveResponse.json()).card as DecisionCard;
  expect(parentWrites).toBe(1);
  expect(card).toMatchObject({ space, stage, state: "adopted", context_snapshot: {} });
  expect(card.evidence_refs).toHaveLength(1);
  expect(card.evidence_refs[0]).toMatchObject({ target_id: source.source_id, target_fingerprint: source.target_fingerprint });
  await expect(parent).not.toBeVisible();

  // Each control must contribute to the filter, including labels outside taxonomy.
  for (const mismatch of [{ space: `${space}-别处`, stage, state: "adopted" }, { space, stage: `${stage}-另期`, state: "adopted" }, { space, stage, state: "considering" }]) {
    const response = await request.post(`/api/projects/${projectId}/decision-cards`, { data: {
      operation_id: crypto.randomUUID(), primary_epoch: epoch,
      changes: { title: `筛选排除-${crypto.randomUUID()}`, decision_text: "人工对照记录", ...mismatch },
    } });
    expect(response.ok()).toBe(true);
  }
  await page.getByLabel("筛选空间", { exact: true }).fill(space);
  await page.getByLabel("筛选阶段", { exact: true }).fill(stage);
  const filtered = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname.endsWith("/decision-cards") && response.request().method() === "GET"
      && url.searchParams.get("space") === space && url.searchParams.get("stage") === stage && url.searchParams.get("state") === "adopted";
  });
  await page.getByLabel("筛选个人状态", { exact: true }).selectOption("adopted");
  const filterResponse = await filtered;
  expect(filterResponse.ok()).toBe(true);
  const result = await filterResponse.json();
  expect(result.total).toBe(1);
  expect(result.cards[0].card_id).toBe(card.card_id);
  const filterUrl = page.url();
  await page.reload();
  await expect(page.getByLabel("筛选空间", { exact: true })).toHaveValue(space);
  await expect(page.getByLabel("筛选阶段", { exact: true })).toHaveValue(stage);
  await expect(page.getByLabel("筛选个人状态", { exact: true })).toHaveValue("adopted");
  await expect(page.locator("main article")).toHaveCount(1);
  await page.getByRole("heading", { name: title, exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/decisions/${card.card_id}$`));
  await page.getByRole("link", { name: "← 个人决定", exact: true }).click();
  await expect(page).toHaveURL(filterUrl);
  await expect(page.locator("main article")).toHaveCount(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("custom-decision-filters-mobile.png"), fullPage: true });
});

test("inline checklist sources preserve edits and require an explicit comparison before adopting the changed fingerprint", async ({ page, request }) => {
  const checklist = await prepareChecklist(request, `核对内联来源-${crypto.randomUUID()}`);
  const item = checklist.items[0];
  const sourceTitle = `核对人工依据-${crypto.randomUUID().slice(0, 8)}`;
  let itemWrites = 0;
  page.on("request", (outgoing) => { if (outgoing.method() === "PATCH" && new URL(outgoing.url()).pathname === `/api/checklist-items/${item.item_id}`) itemWrites++; });
  await page.goto(`/checks/${checklist.instance_id}`);
  await page.getByRole("button", { name: "修订本项", exact: true }).click();
  const parent = page.getByRole("dialog", { name: "修订本项判据", exact: true });
  await parent.getByLabel("检查方法", { exact: true }).fill("必须保留的父表单方法草稿");
  await parent.getByRole("button", { name: "新建人工依据", exact: true }).click();
  const source = await saveSource(page, sourceTitle, "选定前人工摘录旧内容");
  expect(itemWrites).toBe(0);
  await expect(parent.getByLabel("检查方法", { exact: true })).toHaveValue("必须保留的父表单方法草稿");
  const selection = parent.getByRole("checkbox", { name: new RegExp(sourceTitle) });
  await expect(selection).not.toBeChecked();
  await selection.check();
  const row = parent.locator("label").filter({ hasText: sourceTitle }).locator("..");
  await row.getByRole("button", { name: "维护此依据", exact: true }).click();
  const edited = await saveSource(page, sourceTitle, "来源编辑后人工摘录新内容", source.source_id);
  expect(edited.target_fingerprint).not.toBe(source.target_fingerprint);
  expect(itemWrites).toBe(0);
  await expect(selection).toBeChecked();
  await expect(parent.getByText(/当前仍保留之前选定的版本/)).toBeVisible();
  await parent.getByText("查看选定时与当前内容", { exact: true }).click();
  await expect(parent.getByText("选定前人工摘录旧内容", { exact: true })).toBeVisible();
  await expect(parent.getByText("来源编辑后人工摘录新内容", { exact: true })).toBeVisible();
  const unchangedResponse = await request.get(`/api/checklists/${checklist.instance_id}`);
  expect(unchangedResponse.ok()).toBe(true);
  const unchanged = (await unchangedResponse.json()).checklist as Checklist;
  expect(unchanged.items[0].revision).toBe(1);
  expect(unchanged.items[0].refs).toHaveLength(0);
  await parent.getByRole("button", { name: "核对后采用当前来源版本", exact: true }).click();
  await expect(parent.getByText(/当前仍保留之前选定的版本/)).toHaveCount(0);
  await expect(parent.getByLabel("检查方法", { exact: true })).toHaveValue("必须保留的父表单方法草稿");
  const revised = await saveItemRevision(page, item.item_id);
  expect(itemWrites).toBe(1);
  expect(revised.revision).toBe(2);
  expect(revised.refs).toHaveLength(1);
  expect(revised.refs[0]).toMatchObject({ target_id: source.source_id, target_fingerprint: edited.target_fingerprint,
    snapshot: { excerpt: "来源编辑后人工摘录新内容" } });
});

test("field attachments can create and maintain a source in place without selecting it or submitting the inspection", async ({ page, request }) => {
  const checklist = await prepareChecklist(request, `附件内联来源-${crypto.randomUUID()}`);
  const item = checklist.items[0];
  const sourceTitle = `现场照片来源-${crypto.randomUUID().slice(0, 8)}`;
  let checkWrites = 0;
  page.on("request", (outgoing) => { if (outgoing.method() === "POST" && new URL(outgoing.url()).pathname === `/api/checklist-items/${item.item_id}/checks`) checkWrites++; });
  await page.goto(`/checks/${checklist.instance_id}`);
  const card = page.getByTestId("check-item");
  await card.getByText("填写核对、整改或复检记录", { exact: true }).click();
  await card.getByLabel("记录说明", { exact: true }).fill("现场文字草稿保持在原清单中");
  await card.getByText(/照片与附件/).click();
  await card.getByRole("button", { name: "新建人工依据", exact: true }).click();
  await page.getByRole("dialog", { name: "记录一份依据" }).getByLabel("资料类型", { exact: true }).selectOption("site_photo");
  const source = await saveSource(page, sourceTitle, "尚未上传文件的现场照片来源");
  expect(checkWrites).toBe(0);
  await expect(page).toHaveURL(new RegExp(`/checks/${checklist.instance_id}$`));
  await expect(card.getByLabel("记录说明", { exact: true })).toHaveValue("现场文字草稿保持在原清单中");
  await expect(card.getByLabel("附件来源记录", { exact: true })).toHaveValue("");
  await card.getByLabel("附件来源记录", { exact: true }).selectOption(source.source_id);
  await card.getByRole("button", { name: "维护此依据", exact: true }).click();
  const updated = await saveSource(page, sourceTitle, "现场来源补充说明，仍无上传附件", source.source_id);
  expect(updated.revision).toBe(2);
  expect(checkWrites).toBe(0);
  await expect(card.getByLabel("附件来源记录", { exact: true })).toHaveValue(source.source_id);
  await expect(card.getByLabel("记录说明", { exact: true })).toHaveValue("现场文字草稿保持在原清单中");
  const saved = page.waitForResponse((response) => new URL(response.url()).pathname === `/api/checklist-items/${item.item_id}/checks` && response.request().method() === "POST");
  await card.getByRole("button", { name: "保存本次记录", exact: true }).click();
  const response = await saved;
  expect(response.ok()).toBe(true);
  const value = await response.json();
  expect(checkWrites).toBe(1);
  expect(value.check.note).toBe("现场文字草稿保持在原清单中");
  expect(value.check.attachment_refs).toEqual([]);
  expect(value.checklist.state).toBe("active");
});

test("declaring and clearing an unresolved standards conflict both require renewed verification before pass", async ({ page, request }) => {
  const checklist = await prepareChecklist(request, `规范分歧声明-${crypto.randomUUID()}`);
  const item = checklist.items[0];
  await page.goto(`/checks/${checklist.instance_id}`);
  const card = page.getByTestId("check-item");
  await card.getByText("填写核对、整改或复检记录", { exact: true }).click();
  await expect(card.getByLabel("本项结果").locator('option[value="pass"]')).toBeEnabled();
  await card.getByRole("button", { name: "修订本项", exact: true }).click();
  let editor = page.getByRole("dialog", { name: "修订本项判据", exact: true });
  await editor.getByRole("checkbox", { name: "个人要求可能与规范冲突，尚未核实", exact: true }).check();
  await expect(editor.getByLabel("判据核实状态", { exact: true })).toHaveValue("needs_verification");
  await expect(editor.getByLabel("判据核实状态", { exact: true })).toBeDisabled();
  const declared = await saveItemRevision(page, item.item_id);
  expect(declared.revision).toBe(2);
  expect(declared.criteria).toMatchObject({ unresolved_standard_conflict: true });
  expect(declared.pass_eligibility.allowed).toBe(false);
  await expect(card.getByLabel("本项结果").locator('option[value="pass"]')).toBeDisabled();
  await card.getByRole("button", { name: "修订本项", exact: true }).click();
  editor = page.getByRole("dialog", { name: "修订本项判据", exact: true });
  await expect(editor.getByRole("checkbox", { name: "个人要求可能与规范冲突，尚未核实", exact: true })).toBeChecked();
  await editor.getByRole("checkbox", { name: "个人要求可能与规范冲突，尚未核实", exact: true }).uncheck();
  await expect(editor.getByLabel("判据核实状态", { exact: true })).toHaveValue("needs_verification");
  const cleared = await saveItemRevision(page, item.item_id);
  expect(cleared.revision).toBe(3);
  expect(cleared.criteria).toMatchObject({ unresolved_standard_conflict: false });
  expect(cleared.criteria_state).toBe("needs_verification");
  expect(cleared.current_result).toBe("unchecked");
  expect(cleared.pass_eligibility.allowed).toBe(false);
  await expect(card.getByLabel("本项结果").locator('option[value="pass"]')).toBeDisabled();
  const revisions = await request.get(`/api/checklist-items/${item.item_id}/revisions`);
  expect(revisions.ok()).toBe(true);
  const history = (await revisions.json()).revisions as { revision: number; snapshot: CheckItem }[];
  expect(history.find((entry) => entry.revision === 2)?.snapshot.criteria).toMatchObject({ unresolved_standard_conflict: true });
  expect(history.find((entry) => entry.revision === 3)?.snapshot.criteria).toMatchObject({ unresolved_standard_conflict: false });
});
