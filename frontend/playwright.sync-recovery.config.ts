import { defineConfig } from "@playwright/test";
import { readFileSync } from "node:fs";
import { readSyncChainManifest } from "./tests/e2e/sync-chain-manifest";

const manifest = readSyncChainManifest() as ReturnType<typeof readSyncChainManifest> & {
  sync_recovery?: { exercised: boolean; synthetic: boolean };
};
const evidence = JSON.parse(readFileSync(manifest.sync_chain.evidence_file, "utf8"));
if (manifest.sync_recovery?.exercised !== true || manifest.sync_recovery.synthetic !== true
    || evidence.recovery?.restored_and_removed_newer_members !== true
    || evidence.recovery.hold_survived_process_restart !== true
    || evidence.recovery.resume_fresh_full_generation !== true
    || evidence.recovery.lost_resume_reply_replayed_once !== true
    || evidence.recovery.queue_resumed_after_publication !== true
    || evidence.recovery.final_control?.state !== "running") {
  throw new Error("Expected completed --recovery scenario before browser verification");
}
export default defineConfig({
  testDir: "./tests/e2e", testMatch: "sync-recovery.spec.ts",
  globalSetup: "./tests/e2e/qa-global-setup.ts", timeout: 60_000,
  fullyParallel: false, workers: 1, retries: 0, reporter: [["list"]],
  outputDir: `${manifest.root}/playwright-sync-recovery-results`,
  use: { baseURL: manifest.base_url, extraHTTPHeaders: { "X-Reno-Token": manifest.auth_token },
    storageState: { cookies: [], origins: [{ origin: manifest.base_url,
      localStorage: [{ name: "reno-auth-token", value: manifest.auth_token }] }] },
    trace: "off", video: "off" },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
