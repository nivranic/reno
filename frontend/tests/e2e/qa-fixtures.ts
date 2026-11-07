import { expect, test as base } from "@playwright/test";
import { readQaManifest } from "./qa-manifest";

/** Re-check identity before each test, in addition to the global startup gate. */
export const test = base.extend<{ qaIdentity: void }>({
  qaIdentity: [async ({ request }, use) => {
    const manifest = readQaManifest();
    const response = await request.get("/api/health");
    expect(response.ok(), "QA health must succeed before test operations").toBeTruthy();
    expect(response.headers()["x-reno-qa-instance"]).toBe(manifest.qa_instance_id);
    expect(response.headers()["x-reno-qa-fixture"]).toBe(manifest.fixture_version);
    await use();
  }, { auto: true }],
});

export { expect };
