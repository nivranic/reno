import { expect, test } from "./qa-fixtures";

test("atom review binds the shown content and keeps history independently from personal choices", async ({ page, request }, info) => {
  const prior = await (await request.get("/api/reviews?kind=atom&target_id=qa_waterproof_base")).json();
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/videos/BVQA00000001");
  const card = page.locator("article:visible").filter({ hasText: "卫生间防水施工前应确认基层" }).first();
  await card.getByRole("button", { name: "人工复核", exact: true }).click();
  await card.getByLabel("复核备注").fill("隔离人工样本：只确认当前内容，仍需核对现场条件");
  await card.getByRole("button", { name: "确认这条知识", exact: true }).click();
  await expect(card.getByText("已人工复核", { exact: true })).toBeVisible();
  await card.getByRole("button", { name: "驳回", exact: true }).click();
  await expect(card.getByText("已人工驳回", { exact: true })).toBeVisible();
  const search = await request.get("/api/search?q=防水&page_size=100");
  expect((await search.json()).results.some((row: { id: string }) => row.id === "qa_waterproof_base")).toBe(false);
  await card.getByRole("button", { name: "清除人工复核", exact: true }).click();
  await expect(card.getByText("尚未人工复核", { exact: true })).toBeVisible();
  await card.locator("summary").filter({ hasText: "查看复核历史" }).click();
  await expect(card.getByText("内容绑定有效", { exact: false })).toHaveCount(prior.reviews.length + 3);
  await page.screenshot({ path: info.outputPath("atom-review-history-desktop.png"), fullPage: true });
});
