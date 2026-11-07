import { http, HttpResponse } from "msw";
import { server } from "@/test/server";
import type { DecisionCard, DecisionSharePreviewResponse, DecisionShareSummary, Project, SourceRecord } from "@/lib/personal-api";

export const projectFixture: Project = { project_id: "11111111-1111-4111-8111-111111111111", name: "我的装修", profile: { conditions: { 空间: "卫生间", 基层: "水泥" } }, revision: 1, created_at: "2026-09-30T00:00:00Z", updated_at: "2026-09-30T00:00:00Z" };
export const cardFixture: DecisionCard = { card_id: "22222222-2222-4222-8222-222222222222", project_id: projectFixture.project_id, title: "防水方案", question: "防水怎么做", context_snapshot: { 空间: "卫生间" }, alternatives: [], selected_option_id: null, decision_text: "", decision_reason: "", open_questions: [], content_snapshot: { kind: "note", text: "" }, state: "considering", revision: 1, evidence_refs: [], needs_recheck: false, created_at: "2026-09-30T00:00:00Z", updated_at: "2026-09-30T00:00:00Z" };
/** Synthetic server response for client state tests; no real receipt proof. */
export function decisionShareFixture(decisions: DecisionShareSummary[], version: string | null = null): DecisionSharePreviewResponse {
  return { snapshot: { format_version: 1, kind: "personal_decision_share_snapshot", decisions },
    snapshot_sha256: "a".repeat(64), personal_read_version: version };
}
export function installPersonalFixtures() {
  server.use(
    http.get("/api/capabilities", () => HttpResponse.json({ library_id: "personal-test", primary_epoch: 1, personal_read: true, personal_write: true, personal_upload: true, field_checks: true })),
    http.get("/api/projects/default", () => HttpResponse.json({ enabled: true, project: projectFixture })),
    http.get(`/api/projects/${projectFixture.project_id}/decision-cards`, () => HttpResponse.json({ cards: [], total: 0, offset: 0, limit: 30 })),
    http.get(`/api/projects/${projectFixture.project_id}/sources`, () => HttpResponse.json({ sources: [], total: 0, offset: 0, limit: 100 })),
  );
}

export const sourceFixture: SourceRecord = { source_id: "33333333-3333-4333-8333-333333333333", project_id: projectFixture.project_id, source_type: "manual_note", title: "业主的核对说明", publisher: "", document_no: "", edition: "", published_on: "", valid_from: "", valid_to: "", source_url: "", locator: "", excerpt: "选定时的原文", scope: {}, verification_state: "unverified", validity: "unknown", checked_at: null, check_note: "", revision: 1, target_fingerprint: "source-before", attachments: [], created_at: "2026-09-30T00:00:00Z", updated_at: "2026-09-30T00:00:00Z" };
