import { defineConfig } from "@playwright/test";

/** E2E runs against the REAL FastAPI backend + built frontend/dist.
 * `reuseExistingServer` lets a dev server keep running; otherwise Playwright
 * boots `reno serve` itself. Frontend must be built (`npm run build`). */
export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 45_000,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:8765",
    trace: "off",
    video: "off",
  },
  webServer: {
    command: "../.venv/Scripts/python -m reno serve --port 8765",
    url: "http://127.0.0.1:8765/api/health",
    reuseExistingServer: true,
    timeout: 30_000,
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
