import { expect, test } from "@playwright/test";

test.describe("inbox (real backend)", () => {
  test("lists real videos and stats", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "收件箱" })).toBeVisible();
    await expect(page.locator("table tbody tr").first()).toBeVisible({ timeout: 15_000 });
    const rows = await page.locator("table tbody tr").count();
    expect(rows).toBeGreaterThan(5);
  });

  test("SPA deep link: direct open + refresh works", async ({ page }) => {
    await page.goto("/search?q=%E9%98%B2%E6%B0%B4");
    await expect(page.getByText(/条结果/)).toBeVisible({ timeout: 15_000 });
    await page.reload();
    await expect(page.getByText(/条结果/)).toBeVisible({ timeout: 15_000 });
  });

  test("unknown API route returns JSON 404, not index.html", async ({ request }) => {
    const res = await request.get("/api/definitely-missing");
    expect(res.status()).toBe(404);
    expect(res.headers()["content-type"]).toContain("application/json");
  });
});
