import { readFileSync, realpathSync } from "node:fs";
import { tmpdir } from "node:os";
import { isAbsolute, join, relative, resolve } from "node:path";
import { readQaManifest, type QaManifest } from "./qa-manifest";

export interface SyncChainManifest extends QaManifest {
  sync_chain: { evidence_file: string; video_ids: string[]; partial_video_id: string; synthetic: true };
}

export function readSyncChainManifest(): SyncChainManifest {
  const value = readQaManifest() as SyncChainManifest;
  const relativeTemp = relative(realpathSync(tmpdir()), realpathSync(value.root));
  if (!relativeTemp || relativeTemp.startsWith("..") || isAbsolute(relativeTemp)
      || value.fixture_version !== "sync-chain-v1" || value.sync_chain?.synthetic !== true
      || value.sync_chain.evidence_file !== join(value.root, "evidence.json")
      || JSON.stringify(value.sync_chain.video_ids) !== JSON.stringify(["BVSyncQA0001", "BVSyncQA0002", "BVSyncQA0003"])
      || value.sync_chain.partial_video_id !== "BVSyncQAPartial") {
    throw new Error("Expected completed isolated sync-chain manifest");
  }
  for (const node of ["home", "cloud"]) {
    for (const file of ["config.test.json", "data/reno.db", "identity.json"]) {
      const expected = resolve(value.root, node, file);
      if (realpathSync(expected) !== expected) throw new Error("Sync-chain paths must not contain links");
    }
  }
  const evidence = JSON.parse(readFileSync(value.sync_chain.evidence_file, "utf8"));
  if (evidence.status !== "passed" || evidence.qa_instance_id !== value.qa_instance_id
      || evidence.checks.normal_ingest_publish_search_range !== true
      || evidence.checks.lost_reply_same_publication_replayed_once !== true
      || evidence.checks.process_crash_network_outage_durable_recovery !== true) {
    throw new Error("Sync-chain evidence must pass before browser verification");
  }
  return value;
}
