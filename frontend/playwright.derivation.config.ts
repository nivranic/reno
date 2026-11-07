import { defineConfig } from "@playwright/test";
import base from "./playwright.config";
import { readQaManifest } from "./tests/e2e/qa-manifest";

const manifest = readQaManifest() as ReturnType<typeof readQaManifest> & {
  derivation_fixture?: { synthetic: boolean; version: number };
};
if (manifest.derivation_fixture?.synthetic !== true || manifest.derivation_fixture.version !== 1) {
  throw new Error("Expected --derivation synthetic fixture before P04 browser verification");
}
export default defineConfig({ ...base, testMatch: "derivation.spec.ts",
  outputDir: `${manifest.root}/playwright-derivation-results` });
