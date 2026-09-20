import { expect, test } from "@playwright/test";

test.describe("search -> deep link round trip (real backend)", () => {
  test("search, jump to detail, back restores query and results", async ({ page }) => {
    await page.goto("/search");
    await page.getByLabel("搜索知识原子").fill("防水");
    await page.keyboard.press("Enter");
    await expect(page.getByText(/条结果/)).toBeVisible({ timeout: 15_000 });

    await page.locator('a[href*="/videos/"]').first().click();
    await expect(page).toHaveURL(/\/videos\/BV\w+\?t=\d+/);
    await expect(page.locator("video")).toBeVisible();

    await page.goBack();
    await expect(page.getByLabel("搜索知识原子")).toHaveValue("防水");
    await expect(page.getByText(/条结果/)).toBeVisible({ timeout: 10_000 });
  });
});
