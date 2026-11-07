import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { readQaManifest } from "./qa-manifest";

const manifest = readQaManifest();
const fixture = JSON.parse(readFileSync(process.env.RENO_E2E_MANIFEST!, "utf8")).cloud_read_fixture as {
  format_version: number; synthetic: boolean; project_id: string; source_id: string;
  card_id: string; instance_id: string; item_id: string; attachment_id: string;
  attachment_sha256: string; source_node_id: string; installed_revision: number;
};
test.beforeAll(() => {
  expect(fixture?.format_version).toBe(1);
  expect(fixture.synthetic).toBe(true);
});

test("actual installed mirror reads four domains with version headers and no edit forms", async ({ page, request }) => {
  test.setTimeout(120_000);
  const capResponse = await request.get("/api/capabilities");
  expect(capResponse.ok()).toBe(true);
  const cap = await capResponse.json();
  expect(cap.personal_mode).toBe("mirror");
  expect(cap.personal_read).toBe(true);
  expect(cap.personal_write).toBe(false);
  expect(cap.personal_upload).toBe(false);
  expect(cap.field_checks_read).toBe(true);
  expect(cap.field_checks_write).toBe(false);
  expect(cap.personal_read_version).toMatch(/^[0-9a-f]{64}$/);
  expect(cap.personal_mirror.installed_revision).toBe(fixture.installed_revision);
  const observed: { path: string; method: string; version?: string }[] = [];
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (req) => {
    const path = new URL(req.url()).pathname;
    if (/^\/api\/(projects|decision-cards|sources|attachments|checklists|checklist-items|operations)(\/|$)/.test(path)) {
      observed.push({ path, method: req.method(), version: req.headers()["x-reno-personal-read-version"] });
    }
  });
  for (const width of [1280, 375]) {
    await page.setViewportSize({ width, height: 850 });
    for (const [path, heading] of [
      ["/project", "T16 云端只读合成项目"], ["/decisions", "个人决定"],
      ["/sources", "依据资料"], [`/checks/${fixture.instance_id}`, "现场原始记录"],
    ]) {
      const capabilityResponse = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/capabilities");
      await page.goto(path);
      const pageCapResponse = await capabilityResponse;
      const pageCap = await pageCapResponse.json();
      expect({ status: pageCapResponse.status(), code: pageCap.code, mode: pageCap.personal_mode, readable: pageCap.personal_read }).toEqual({ status: 200, code: undefined, mode: "mirror", readable: true });
      await page.waitForLoadState("networkidle");
      await expect(page.getByRole("heading", { name: heading, exact: true })).toBeVisible();
      await expect(page.getByText("云端只读镜像。修改请在来源主实例完成，再同步到这里。", { exact: true })).toBeVisible();
      await expect(page.getByText(new RegExp(`来源主实例：${fixture.source_node_id}`))).toBeVisible();
      await expect(page.getByRole("button", { name: "保存项目资料" })).toHaveCount(0);
      await expect(page.getByRole("button", { name: "编辑记录" })).toHaveCount(0);
      await expect(page.locator('input[type="file"]')).toHaveCount(0);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
    }
    await page.screenshot({ path: join(manifest.root, `cloud-read-${width}.png`), fullPage: true });
  }
  expect(observed.length).toBeGreaterThan(4);
  expect(observed.every((req) => req.method === "GET" && req.version === cap.personal_read_version)).toBe(true);
  expect(errors).toEqual([]);
});

test("mirror decision history and Markdown export retain original source facts", async ({ page }) => {
  await page.goto(`/decisions/${fixture.card_id}`);
  await expect(page.getByRole("heading", { name: "个人防水决定", exact: true })).toBeVisible();
  await expect(page.getByText("理由与备注：原家庭主实例的实际修订", { exact: true })).toBeVisible();
  await expect(page.getByLabel("决定理由")).toHaveCount(0);
  await page.getByRole("button", { name: "查看历史版本" }).click();
  await expect(page.locator("summary").filter({ hasText: /^版本 1 ·/ })).toBeVisible();
  await expect(page.locator("summary").filter({ hasText: /^版本 2 ·/ })).toBeVisible();
  const pending = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出 Markdown" }).click();
  const file = await pending;
  const path = join(manifest.root, "cloud-decision.md");
  await file.saveAs(path);
  expect(readFileSync(path, "utf8")).toContain("原家庭主实例的实际修订");
  expect(await file.failure()).toBeNull();
});

test("actual uploaded mirrored attachment downloads the original bytes", async ({ page, request }) => {
  const source = await request.get(`/api/sources/${fixture.source_id}`);
  expect(source.ok()).toBe(true);
  const attachment = (await source.json()).source.attachments.find((entry: { attachment_id: string }) => entry.attachment_id === fixture.attachment_id);
  expect(attachment.readable).toBe(true);
  await page.goto("/sources");
  const button = page.getByRole("button", { name: /synthetic\.pdf · 查看文件/ });
  await expect(button).toBeEnabled();
  await expect(page.getByRole("button", { name: "添加依据" })).toBeDisabled();
  const pending = page.waitForEvent("download");
  await button.click();
  const file = await pending;
  const path = join(manifest.root, "cloud-attachment.pdf");
  await file.saveAs(path);
  expect(createHash("sha256").update(readFileSync(path)).digest("hex")).toBe(fixture.attachment_sha256);
  expect(await file.failure()).toBeNull();
});

test("actual cloud rejects wrong versions and all personal writes while keeping reads intact", async ({ request }) => {
  const before = await (await request.get("/api/capabilities")).json();
  const wrong = await request.get(`/api/sources/${fixture.source_id}`, { headers: { "X-Reno-Personal-Read-Version": "0".repeat(64) } });
  expect(wrong.status()).toBe(409);
  expect((await wrong.json()).code).toBe("PERSONAL_MIRROR_READ_VERSION_CHANGED");
  expect(wrong.headers()["cache-control"]).toBe("private, no-store");
  for (const path of [`/api/projects/${fixture.project_id}`, `/api/sources/${fixture.source_id}`, `/api/checklists/${fixture.instance_id}`]) {
    const denied = await request.patch(path, { data: {} });
    expect(denied.status()).toBe(403);
    expect((await denied.json()).code).toBe("PERSONAL_CLOUD_READ_ONLY");
  }
  const receipt = await request.get("/api/operations/11111111-1111-4111-8111-111111111111");
  expect(receipt.status()).toBe(403);
  const after = await (await request.get("/api/capabilities")).json();
  expect(after.personal_read_version).toBe(before.personal_read_version);
});
