import { readFileSync, realpathSync } from "node:fs";
import { dirname, isAbsolute, join, resolve } from "node:path";

export interface QaManifest {
  qa_instance_id: string;
  fixture_version: string;
  root: string;
  port: number;
  base_url: string;
  auth_token: string;
}

export function readQaManifest(): QaManifest {
  const file = process.env.RENO_E2E_MANIFEST;
  if (!file) throw new Error("RENO_E2E_MANIFEST is required. Run scripts/prepare_product_fixture.py first.");
  const path = realpathSync(file);
  const manifest = JSON.parse(readFileSync(path, "utf8")) as QaManifest;
  if (!isAbsolute(manifest.root) || realpathSync(manifest.root) !== dirname(path)
      || path !== join(realpathSync(manifest.root), "qa-manifest.json")
      || !/^[0-9a-f]{32}$/.test(manifest.qa_instance_id)
      || !Number.isInteger(manifest.port) || manifest.port < 1024 || manifest.port > 65535
      || manifest.port === 8765 || manifest.base_url !== `http://127.0.0.1:${manifest.port}`
      || manifest.auth_token !== `reno-qa-${manifest.qa_instance_id}`) {
    throw new Error("Invalid isolated QA manifest");
  }
  if (process.env.RENO_E2E_BASE_URL && process.env.RENO_E2E_BASE_URL !== manifest.base_url) {
    throw new Error("RENO_E2E_BASE_URL must match the QA manifest");
  }
  if (process.env.RENO_QA_ROOT && resolve(process.env.RENO_QA_ROOT) !== resolve(manifest.root)) {
    throw new Error("RENO_QA_ROOT must match the QA manifest");
  }
  return manifest;
}
