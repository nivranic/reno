import { defineConfig } from "@playwright/test";
import { readSyncChainManifest } from "./tests/e2e/sync-chain-manifest";

/** run_sync_scenario.py --keep-server owns this service; --stop cleans it up. */
const manifest = readSyncChainManifest();
export default defineConfig({
  testDir: "./tests/e2e",
  testMatch: "sync-chain.spec.ts",
  globalSetup: "./tests/e2e/qa-global-setup.ts",
  timeout: 45_000,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  outputDir: `${manifest.root}/playwright-sync-chain-results`,
  use: {
    baseURL: manifest.base_url,
    extraHTTPHeaders: { "X-Reno-Token": manifest.auth_token },
    storageState: { cookies: [], origins: [{ origin: manifest.base_url, localStorage: [
      { name: "reno-auth-token", value: manifest.auth_token },
    ] }] },
    trace: "off",
    video: "off",
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
