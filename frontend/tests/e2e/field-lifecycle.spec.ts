import { expect, test } from "./qa-fixtures";
import type { APIRequestContext, Page } from "@playwright/test";
import type { CheckEvent, CheckItem, CheckItemInput, Checklist, SourceRecord } from "../../src/lib/personal-api";

async function prepare(request: APIRequestContext, title: string, withSource = false) {
  const capabilities = await request.get("/api/capabilities");
  expect(capabilities.ok()).toBe(true);
  const cap = await capabilities.json();
  expect(cap.field_checks).toBe(true);
  const projectResponse = await request.get("/api/projects/default");
  expect(projectResponse.ok()).toBe(true);
  const { project } = await projectResponse.json();
  let source: SourceRecord | undefined;
  const criteriaText = "人工测试判据：指定区域表面无可见垃圾";
  if (withSource) {
    const created = await request.post(`/api/projects/${project.project_id}/sources`, { data: {
      operation_id: crypto.randomUUID(), primary_epoch: cap.primary_epoch,
      changes: { source_type: "standard_record", title: `${title}-人工依据`, publisher: "人工测试资料",
        document_no: "QA-FIELD-LIFECYCLE", edition: "人工版本 1", locator: "人工样本第 1 条",
        excerpt: criteriaText, scope: { 空间: "卫生间" }, verification_state: "user_checked",
        validity: "current", checked_at: new Date().toISOString(), check_note: "仅用于隔离验收的合成原文，无真实规范效力" },
    } });
    expect(created.ok()).toBe(true);
    source = (await created.json()).source as SourceRecord;
  }
  const item: CheckItemInput = {
    title: "指定区域表面清洁", conditions: { 空间: "卫生间" }, method_text: "现场目视指定区域",
    basis_kind: source ? "standard_clause" : "personal_requirement", criteria_state: "known",
    criteria: { text: criteriaText, conditions_confirmed: true, requirement_confirmed: !source,
      required_observations: [{ key: "surface", label: "现场观察", kind: "text" }] },
    refs: source ? [{ target_kind: "source", target_id: source.source_id, target_fingerprint: source.target_fingerprint }] : [],
  };
  const created = await request.post(`/api/projects/${project.project_id}/checklists`, { data: {
    operation_id: crypto.randomUUID(), primary_epoch: cap.primary_epoch,
    changes: { title, space: "卫生间", stage: "泥瓦", items: [item] },
  } });
  expect(created.ok()).toBe(true);
  return { checklist: (await created.json()).checklist as Checklist, source, epoch: cap.primary_epoch as number };
}

async function inspect(page: Page, itemId: string, result: "pass" | "fail", note: string) {
  const card = page.getByTestId("check-item");
  await expect(card).toBeVisible();
  if (!await card.getByLabel("本项结果").isVisible()) {
    await card.getByText("填写核对、整改或复检记录", { exact: true }).click();
  }
  await card.getByLabel("本项结果").selectOption(result);
  await card.getByLabel("现场观察", { exact: true }).fill(result === "pass" ? "指定区域表面无可见垃圾" : "指定区域发现残留垃圾");
  await card.getByLabel("记录说明").fill(note);
  const saved = page.waitForResponse((response) => new URL(response.url()).pathname === `/api/checklist-items/${itemId}/checks` && response.request().method() === "POST");
  await card.getByRole("button", { name: "保存本次记录", exact: true }).click();
  const response = await saved;
  expect(response.ok()).toBe(true);
  return await response.json() as { check: CheckEvent; item: CheckItem; checklist: Checklist };
}

async function current(request: APIRequestContext, id: string) {
  const response = await request.get(`/api/checklists/${id}`);
  expect(response.ok()).toBe(true);
  return (await response.json()).checklist as Checklist;
}

test("A17 revising criteria reopens a completed checklist and keeps the passing inspection on its old version", async ({ page, request }) => {
  const { checklist } = await prepare(request, `判据修订-${crypto.randomUUID()}`);
  const item = checklist.items[0];
  await page.goto(`/checks/${checklist.instance_id}`);
  const passed = await inspect(page, item.item_id, "pass", "按原判据完成核对");
  expect(passed.checklist.state).toBe("completed");
  await expect(page.getByText(/本清单当前项目已完成/)).toBeVisible();

  const card = page.getByTestId("check-item");
  await card.getByRole("button", { name: "修订本项", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "修订本项判据" });
  await dialog.getByLabel("本项判据", { exact: true }).fill("个人修订要求：指定区域与周边均无可见垃圾");
  const revision = page.waitForResponse((response) => new URL(response.url()).pathname === `/api/checklist-items/${item.item_id}` && response.request().method() === "PATCH");
  await dialog.getByRole("button", { name: "确认修订并重新核对", exact: true }).click();
  const response = await revision;
  expect(response.ok()).toBe(true);
  const updated = (await response.json()).item as CheckItem;
  expect(updated.revision).toBe(2);
  expect(updated.current_result).toBe("unchecked");
  expect(updated.criteria_snapshot_hash).not.toBe(item.criteria_snapshot_hash);
  expect(updated.checks).toHaveLength(1);
  expect(updated.checks[0]).toMatchObject({ check_id: passed.check.check_id, result: "pass", item_revision: 1, current_version: false });
  await expect(page.getByText(/仍有未核对、需复看或待处理项目/)).toBeVisible();
  await expect(page.getByText(/本清单当前项目已完成/)).toHaveCount(0);
  await card.getByText(/检查、整改与复检历史/).click();
  await expect(card.getByText(/当时记录为符合本项.*历史判据版本/)).toBeVisible();
  await expect(card.getByText("按原判据完成核对", { exact: true })).toBeVisible();

  await page.reload();
  await expect(page.getByText(/仍有未核对、需复看或待处理项目/)).toBeVisible();
  const reopened = await current(request, checklist.instance_id);
  expect(reopened.state).toBe("active");
  expect(reopened.progress).toEqual({ total: 1, complete: 0 });
  const revisions = await request.get(`/api/checklist-items/${item.item_id}/revisions`);
  expect(revisions.ok()).toBe(true);
  const history = (await revisions.json()).revisions as { revision: number; snapshot: CheckItemInput }[];
  expect(history.find((entry) => entry.revision === 1)?.snapshot.criteria.text).toBe(item.criteria.text);
});

test("A17 withdrawing a recorded source removes completion while retaining the original pass and source snapshot", async ({ page, request }) => {
  const { checklist, source } = await prepare(request, `来源失效-${crypto.randomUUID()}`, true);
  expect(source).toBeDefined();
  const item = checklist.items[0];
  await page.goto(`/checks/${checklist.instance_id}`);
  const passed = await inspect(page, item.item_id, "pass", "按当时保存的人工依据核对");
  expect(passed.checklist.state).toBe("completed");
  await expect(page.getByText(/本清单当前项目已完成/)).toBeVisible();

  await page.goto("/sources");
  const sourceCard = page.locator("article").filter({ has: page.getByRole("heading", { name: source!.title, exact: true }) });
  await sourceCard.getByRole("button", { name: "编辑记录", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "编辑来源记录" });
  await dialog.getByLabel("有效性记录", { exact: true }).selectOption("withdrawn");
  const withdrawn = page.waitForResponse((response) => new URL(response.url()).pathname === `/api/sources/${source!.source_id}` && response.request().method() === "PATCH");
  await dialog.getByRole("button", { name: "保存来源记录", exact: true }).click();
  const response = await withdrawn;
  expect(response.ok()).toBe(true);
  expect((await response.json()).source.validity).toBe("withdrawn");
  await expect(dialog).not.toBeVisible();

  await page.goto(`/checks/${checklist.instance_id}`);
  await expect(page.getByText(/仍有未核对、需复看或待处理项目/)).toBeVisible();
  await expect(page.getByText(/本清单当前项目已完成/)).toHaveCount(0);
  const card = page.getByTestId("check-item");
  await expect(card.getByText(/依据需复看/)).toBeVisible();
  await card.getByText("填写核对、整改或复检记录", { exact: true }).click();
  await expect(card.getByLabel("本项结果").locator('option[value="pass"]')).toBeDisabled();
  await card.getByText(/检查、整改与复检历史/).click();
  await expect(card.getByText("按当时保存的人工依据核对", { exact: true })).toBeVisible();
  const reopened = await current(request, checklist.instance_id);
  expect(reopened.state).toBe("active");
  expect(reopened.progress).toEqual({ total: 1, complete: 0 });
  expect(reopened.items[0]).toMatchObject({ revision: 1, current_result: "pass", needs_recheck: true });
  expect(reopened.items[0].checks[0]).toMatchObject({ check_id: passed.check.check_id, item_revision: 1, result: "pass" });
  expect(reopened.items[0].refs[0]).toMatchObject({ binding_status: "stale", snapshot: { validity: "current" }, current_snapshot: { validity: "withdrawn" } });
});

test("A19 archive blocks editing and appended inspections until explicit browser restore recomputes completion", async ({ page, request }) => {
  const { checklist, epoch } = await prepare(request, `归档恢复-${crypto.randomUUID()}`);
  const item = checklist.items[0];
  await page.goto(`/checks/${checklist.instance_id}`);
  const passed = await inspect(page, item.item_id, "pass", "归档前已完成本项");
  expect(passed.checklist.state).toBe("completed");
  const archiveReply = page.waitForResponse((response) => new URL(response.url()).pathname === `/api/checklists/${checklist.instance_id}` && response.request().method() === "PATCH");
  await page.getByRole("button", { name: "归档现场清单", exact: true }).click();
  const archiveResponse = await archiveReply;
  expect(archiveResponse.ok()).toBe(true);
  const archived = (await archiveResponse.json()).checklist as Checklist;
  expect(archived.state).toBe("archived");
  await expect(page.getByText("已归档。历史可回看；恢复后才能追加或修订。", { exact: true })).toBeVisible();
  const card = page.getByTestId("check-item");
  await expect(card.getByRole("button", { name: "修订本项", exact: true })).toBeDisabled();
  await expect(card.getByLabel("本项结果")).toBeDisabled();
  await expect(card.getByLabel("现场观察", { exact: true })).toBeDisabled();
  await expect(card.getByLabel("记录说明")).toBeDisabled();
  await expect(card.getByRole("button", { name: "保存本次记录", exact: true })).toBeDisabled();
  await card.getByText(/检查、整改与复检历史/).click();
  await expect(card.getByText("归档前已完成本项", { exact: true })).toBeVisible();

  // Browser controls enforce the lifecycle; direct requests must not bypass it.
  const envelope = (changes: unknown, base: number) => ({ operation_id: crypto.randomUUID(), primary_epoch: epoch, base_revision: base, changes });
  const denied = [
    await request.patch(`/api/checklists/${checklist.instance_id}`, { data: envelope({ title: "不应写入归档清单" }, archived.revision) }),
    await request.patch(`/api/checklist-items/${item.item_id}`, { data: envelope({ method_text: "不应修改归档条目" }, item.revision) }),
    await request.post(`/api/checklist-items/${item.item_id}/checks`, { data: envelope({ record_kind: "inspection", result: "unable_to_verify", note: "不应追加归档记录", measurements: [], attachment_refs: [], criteria_snapshot_hash: item.criteria_snapshot_hash }, item.revision) }),
  ];
  for (const response of denied) {
    expect(response.status()).toBe(409);
    expect((await response.json()).code).toBe("CHECKLIST_ARCHIVED");
  }
  expect(await current(request, checklist.instance_id)).toEqual(archived);

  const restoreReply = page.waitForResponse((response) => new URL(response.url()).pathname === `/api/checklists/${checklist.instance_id}` && response.request().method() === "PATCH");
  await page.getByRole("button", { name: "恢复现场清单", exact: true }).click();
  const restoreResponse = await restoreReply;
  expect(restoreResponse.ok()).toBe(true);
  const restored = (await restoreResponse.json()).checklist as Checklist;
  expect(restored.lifecycle_state).toBe("active");
  expect(restored.state).toBe("completed");
  expect(restored.progress).toEqual({ total: 1, complete: 1 });
  await expect(page.getByText(/本清单当前项目已完成/)).toBeVisible();
  await expect(card.getByRole("button", { name: "修订本项", exact: true })).toBeEnabled();
  await expect(card.getByLabel("记录说明")).toBeEnabled();
  const reinspected = await inspect(page, item.item_id, "fail", "显式恢复后追加复检，发现新的残留物");
  expect(reinspected.checklist.state).toBe("active");
  expect(reinspected.item.checks).toHaveLength(2);
  expect(reinspected.item.checks[0].check_id).toBe(passed.check.check_id);
  await expect(page.getByText(/仍有未核对、需复看或待处理项目/)).toBeVisible();
});
