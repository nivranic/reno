import { request as playwrightRequest } from "@playwright/test";
import { expect, test } from "./qa-fixtures";
import { readSyncChainManifest } from "./sync-chain-manifest";

const manifest = readSyncChainManifest();

test("actual publication outcomes appear in cloud queue and search", async ({ page, request }) => {
  const response = await request.get("/api/sync-status");
  expect(response.ok()).toBeTruthy();
  const status = await response.json();
  expect(status.mode).toBe("cloud");
  expect(status.queue.total).toBe(3);
  expect(status.queue.items.every((row: { status: string; attempts: number; can_view_video: boolean }) =>
    row.status === "done" && row.attempts === 1 && row.can_view_video)).toBe(true);
  const search = await request.get("/api/search?q=" + encodeURIComponent("防水基层"));
  const atoms = (await search.json()).results.map((atom: { id: string }) => atom.id).sort();
  expect(atoms).toEqual(manifest.sync_chain.video_ids.map((id) => `atom_${id}`).sort());
  await page.goto("/collect");
  const panel = page.getByRole("region", { name: "采集与同步状态" });
  await expect(panel.getByRole("heading", { name: "云端采集队列" })).toBeVisible();
  await expect(panel.getByRole("link", { name: "查看视频" })).toHaveCount(3);
  for (const id of manifest.sync_chain.video_ids) {
    await expect(panel.locator(`a[href="/videos/${id}"]`)).toBeVisible();
  }
});

for (const id of manifest.sync_chain.video_ids) {
  test(`published ${id} really decodes, plays and seeks in Chromium`, async ({ page }, info) => {
    await page.goto(`/videos/${id}`);
    const video = page.locator("video");
    await expect(video).toBeVisible();
    await expect.poll(() => video.evaluate((element: HTMLVideoElement) => element.readyState)).toBeGreaterThanOrEqual(2);
    await video.evaluate(async (element: HTMLVideoElement) => { element.muted = true; await element.play(); });
    await expect.poll(() => video.evaluate((element: HTMLVideoElement) => element.currentTime)).toBeGreaterThan(0.25);
    await video.evaluate((element: HTMLVideoElement) => { element.pause(); element.currentTime = 4; });
    await expect.poll(() => video.evaluate((element: HTMLVideoElement) =>
      !element.seeking && element.currentTime >= 3.95 && element.currentTime <= 4.1)).toBe(true);
    await video.evaluate(async (element: HTMLVideoElement) => { await element.play(); });
    await expect.poll(() => video.evaluate((element: HTMLVideoElement) => element.currentTime)).toBeGreaterThan(4.25);
    const metrics = await video.evaluate((element: HTMLVideoElement) => ({ duration: element.duration,
      width: element.videoWidth, height: element.videoHeight, currentTime: element.currentTime,
      readyState: element.readyState, error: element.error?.code ?? null }));
    expect(metrics.duration).toBeGreaterThanOrEqual(5.9);
    expect(metrics.width).toBe(320);
    expect(metrics.height).toBe(240);
    expect(metrics.error).toBeNull();
    await video.evaluate((element: HTMLVideoElement) => element.pause());
    await info.attach("decoded-playback-and-seek.json", { body: JSON.stringify(metrics), contentType: "application/json" });
    await page.screenshot({ path: info.outputPath(`${id}-decoded.png`), fullPage: true });
  });
}

test("published media supports Range; interrupted media remains inaccessible", async ({ request }) => {
  for (const id of manifest.sync_chain.video_ids) {
    const response = await request.get(`/api/video/${id}/file`, { headers: { Range: "bytes=10-49" } });
    expect(response.status()).toBe(206);
    expect((await response.body()).length).toBe(40);
    expect(response.headers()["content-range"]).toMatch(/^bytes 10-49\/\d+$/);
  }
  expect((await request.get(`/api/video/${manifest.sync_chain.partial_video_id}/file`)).status()).toBe(404);
  const anonymous = await playwrightRequest.newContext({ baseURL: manifest.base_url,
    extraHTTPHeaders: {}, storageState: { cookies: [], origins: [] } });
  try {
    expect((await anonymous.get(`/api/video/${manifest.sync_chain.video_ids[0]}/file`)).status()).toBe(401);
  } finally {
    await anonymous.dispose();
  }
});
