import { spawnSync } from "node:child_process";
import { writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { randomUUID } from "node:crypto";
import { expect, test } from "./qa-fixtures";
import { readQaManifest } from "./qa-manifest";
import { readSyncChainManifest } from "./sync-chain-manifest";

const manifest = readQaManifest() as ReturnType<typeof readQaManifest> & {
  sync_recovery?: { exercised: boolean; synthetic: boolean };
};
const repo = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const python = process.env.RENO_E2E_PYTHON || resolve(repo, process.platform === "win32" ? ".venv/Scripts/python.exe" : ".venv/bin/python");
type Preview = { current_version: { library_id: string; library_epoch: number; kb_revision: number }; control_revision: number };

function child(args: string[], label: string) {
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith("RENO_")
    && !["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ZHIPU_API_KEY", "ZHIPUAI_API_KEY"].includes(key)));
  const result = spawnSync(python, args, { cwd: repo, windowsHide: true, encoding: "utf8", timeout: 50_000,
    env: { ...env, PYTHONIOENCODING: "utf-8", RENO_QA_ROOT: manifest.root,
      RENO_CONFIG_FILE: resolve(manifest.root, "cloud/config.test.json"),
      RENO_DATA_DIR: resolve(manifest.root, "cloud/data"), RENO_LLM_TESTS: "0" } });
  writeFileSync(resolve(manifest.root, `browser-${label}.private.log`), (result.stdout ?? "") + (result.stderr ?? ""), "utf8");
  if (result.status !== 0) throw new Error(`Isolated ${label} failed; inspect its private local log`);
  return result.stdout;
}
function admin(...args: string[]) { return JSON.parse(child(["-m", "reno", ...args], "recovery-admin")); }
function applyArgs(value: Preview) {
  return ["--apply", "--operation-id", randomUUID(), "--expected-library-id", value.current_version.library_id,
    "--expected-epoch", String(value.current_version.library_epoch), "--expected-revision", String(value.current_version.kb_revision),
    "--expected-control-revision", String(value.control_revision)];
}

test.describe.serial("recovery states after real local CLI operations", () => {
  test.skip(!manifest.sync_recovery, "Requires run_sync_scenario.py --recovery --keep-server");
  test.beforeAll(() => {
    expect(manifest.sync_recovery).toEqual({ exercised: true, synthetic: true });
    readSyncChainManifest(); // TEMP, no symlinks, synthetic identity, completed actual sync evidence.
    const entries = admin("sync-checkpoints") as { checkpoint_id: string; recoverable: boolean; row_counts: Record<string, number> }[];
    const selected = entries.find((entry) => entry.recoverable && entry.row_counts.video_asset === 1);
    expect(selected).toBeDefined();
    const preview = admin("sync-restore", selected!.checkpoint_id) as Preview;
    admin("sync-restore", selected!.checkpoint_id, ...applyArgs(preview));
  });
  test.afterAll(() => {
    if (!manifest.sync_recovery) return;
    const status = admin("sync-recovery-status");
    if (status.control.state === "held") admin("sync-resume", ...applyArgs(status));
    if (admin("sync-recovery-status").control.state === "resume_pending") {
      child([resolve(repo, "scripts/sync_scenario_node.py"), "--manifest", process.env.RENO_E2E_MANIFEST!,
        "--node", "home", "--action", "work", "--label", "browser-recovery-finish"], "recovery-finish");
    }
    expect(admin("sync-recovery-status").control.state).toBe("running");
  });

  for (const [label, width, height, theme] of [
    ["desktop-light", 1280, 900, "light"], ["desktop-dark", 1280, 900, "dark"],
    ["mobile-light", 375, 812, "light"], ["mobile-dark", 375, 812, "dark"],
  ] as const) {
    test(`persistent hold remains visible with preserved queue: ${label}`, async ({ page, request }, info) => {
      const before = await (await request.get("/api/sync-status")).json();
      expect(before.recovery.state).toBe("held");
      await page.setViewportSize({ width, height });
      await page.addInitScript((value) => localStorage.setItem("reno-theme", value), theme);
      await page.goto("/collect");
      const panel = page.getByRole("region", { name: "采集与同步状态" });
      await expect(panel.getByText("恢复后知识同步已暂停", { exact: true })).toBeVisible();
      await expect(panel.getByText(/复核记录仍可同步，已有队列和处理进度保留/)).toBeVisible();
      await expect(panel.getByRole("list", { name: "采集队列" }).locator(":scope > li")).toHaveCount(before.queue.items.length);
      await expect(page.getByRole("button", { name: /恢复同步|恢复知识/ })).toHaveCount(0);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      expect(await page.locator("html").evaluate((node) => node.classList.contains("dark"))).toBe(theme === "dark");
      await page.screenshot({ path: info.outputPath(`${label}.png`), fullPage: true });
      const after = await (await request.get("/api/sync-status")).json();
      expect(after.recovery).toEqual(before.recovery);
      expect(after.counts).toEqual(before.counts);
    });
  }

  test("explicit resume waits for a new full publication and never claims completion early", async ({ page, request }, info) => {
    const preview = admin("sync-resume") as Preview;
    admin("sync-resume", ...applyArgs(preview));
    expect((await (await request.get("/api/sync-status")).json()).recovery.state).toBe("resume_pending");
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto("/collect");
    await expect(page.getByText("等待家庭节点重新发布完整知识", { exact: true })).toBeVisible();
    await expect(page.getByText(/新批次确认完成后，才会恢复领取采集任务/)).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: info.outputPath("resume-pending.png"), fullPage: true });
  });
});
