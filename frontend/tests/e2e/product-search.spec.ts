import { expect, test } from "./qa-fixtures";

test("filtered browsing paginates without a query and preserves its page through evidence", async ({ page }, info) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/search?space=卫生间&page_size=5");
  await expect(page.locator("[data-search-result]")).toHaveCount(5);
  const firstIds = await page.locator("[data-search-result]").evaluateAll((elements) => elements.map((e) => e.getAttribute("data-search-result")));
  const nextResponse = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === "/api/search" && url.searchParams.get("page") === "2";
  });
  await page.getByRole("button", { name: "下一页", exact: true }).click();
  const nextPage = await (await nextResponse).json();
  await expect(page).toHaveURL(/page=2/);
  await expect.poll(() => page.locator("[data-search-result]").evaluateAll((elements) => elements.map((e) => e.getAttribute("data-search-result"))))
    .toEqual(nextPage.results.map((row: { id: string }) => row.id));
  const secondIds = await page.locator("[data-search-result]").evaluateAll((elements) => elements.map((e) => e.getAttribute("data-search-result")));
  expect(secondIds.every((id) => !firstIds.includes(id))).toBeTruthy();
  await page.locator('a[href^="/videos/"]').first().click();
  await page.getByRole("link", { name: "返回搜索", exact: true }).click();
  await expect(page).toHaveURL(/page=2/);
  await expect(page.getByLabel("搜索知识原子")).toHaveValue("");
  await page.locator("details").first().locator("summary").click();
  await expect(page.getByText("不适用例外：").first()).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("search-mobile-browse.png"), fullPage: true });
  await page.getByLabel("按空间筛选").selectOption("厨房");
  await expect(page).not.toHaveURL(/page=2/);
});
