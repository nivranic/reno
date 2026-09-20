import { expect, test } from "@playwright/test";

/** Picks the video with the most evidence events from the real DB (via API). */
async function richestVideoId(request: import("@playwright/test").APIRequestContext) {
  const res = await request.get("/api/videos");
  const data = (await res.json()) as { videos: { video_id: string; atoms: number }[] };
  const withAtoms = data.videos.filter((v) => v.atoms >= 5);
  return withAtoms[0]?.video_id ?? data.videos[0].video_id;
}

test.describe("workbench four-linkage (real backend)", () => {
  test("atom click seeks player, updates ?t= and loads the evidence frame", async ({ page, request }) => {
    const vid = await richestVideoId(request);
    await page.goto(`/videos/${vid}`);
    const atom = page.locator("article").first();
    await expect(atom).toBeVisible({ timeout: 20_000 });

    await atom.click();
    // URL deep link updated
    await expect(page).toHaveURL(new RegExp(`/videos/${vid}\\?t=\\d+`));
    // frame pane loaded a decoded image (exactly one pane is visible per breakpoint)
    const img = page.locator('section[aria-label="证据帧面板"] img:visible');
    await expect(img).toHaveCount(1, { timeout: 15_000 });
    // player is positioned and playing
    await page.waitForTimeout(2_500);
    const t = await page.locator("video").evaluate((v: HTMLVideoElement) => v.currentTime);
    expect(t).toBeGreaterThan(0);
  });

  test("modality filter reduces the evidence stream total", async ({ page, request }) => {
    const vid = await richestVideoId(request);
    await page.goto(`/videos/${vid}`);
    const stream = page.locator('[aria-label^="证据流,共"]');
    await expect(stream).toBeVisible({ timeout: 20_000 });
    const total = async () =>
      Number(/共 (\d+) 条/.exec((await stream.getAttribute("aria-label")) ?? "")?.[1] ?? 0);
    const before = await total();
    await page.getByRole("button", { name: "OCR", exact: true }).click();
    await expect.poll(total, { timeout: 5_000 }).toBeLessThan(before);
  });

  test("deep link ?t=ms cold-loads at the right position", async ({ page, request }) => {
    const vid = await richestVideoId(request);
    const ev = await request.get(`/api/video/${vid}/events`);
    const data = (await ev.json()) as { events: { ms: number }[] };
    const target = data.events[Math.floor(data.events.length / 2)].ms;
    await page.goto(`/videos/${vid}?t=${target}`);
    await page.waitForTimeout(3_000);
    const t = await page.locator("video").evaluate((v: HTMLVideoElement) => v.currentTime * 1000);
    expect(t).toBeGreaterThanOrEqual(target - 500);
  });
});
