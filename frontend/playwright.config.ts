import { defineConfig } from "@playwright/test";
import { readQaManifest } from "./tests/e2e/qa-manifest";

/** Real FastAPI + built frontend, against synthetic data and in-process LLM stubs.
 * Run prepare_product_fixture.py first and supply RENO_E2E_MANIFEST. */
const manifest = readQaManifest();
export default defineConfig({
  testDir: "./tests/e2e",
  globalSetup: "./tests/e2e/qa-global-setup.ts",
  timeout: 45_000,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  outputDir: `${manifest.root}/playwright-results`,
  use: {
    baseURL: manifest.base_url,
    extraHTTPHeaders: { "X-Reno-Token": manifest.auth_token },
    storageState: {
      cookies: [],
      origins: [{ origin: manifest.base_url, localStorage: [
        { name: "reno-auth-token", value: manifest.auth_token },
      ] }],
    },
    trace: "off",
    video: "off",
  },
  webServer: {
    command: "node tests/e2e/start-qa-server.mjs",
    // Authenticated health and exact instance checks run in global setup.
    url: manifest.base_url,
    reuseExistingServer: process.env.RENO_E2E_REUSE_SERVER === "1",
    timeout: 30_000,
    env: { RENO_E2E_MANIFEST: process.env.RENO_E2E_MANIFEST! },
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
