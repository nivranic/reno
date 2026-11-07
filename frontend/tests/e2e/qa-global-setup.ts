import { request } from "@playwright/test";
import { readQaManifest } from "./qa-manifest";

export default async function setup() {
  const manifest = readQaManifest();
  const context = await request.newContext({
    baseURL: manifest.base_url,
    extraHTTPHeaders: { "X-Reno-Token": manifest.auth_token },
  });
  try {
    const response = await context.get("/api/health");
    if (!response.ok() || response.headers()["x-reno-qa-instance"] !== manifest.qa_instance_id
        || response.headers()["x-reno-qa-fixture"] !== manifest.fixture_version) {
      throw new Error("Refusing E2E: server is not the prepared QA instance");
    }
    console.log(`QA fixture ${manifest.fixture_version}: ${manifest.root} (port ${manifest.port})`);
  } finally {
    await context.dispose();
  }
}
