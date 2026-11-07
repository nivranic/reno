import { beforeEach, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/server";
import { renderWithApp } from "@/test/utils";
import { installPersonalFixtures, projectFixture } from "@/features/project/test-fixtures";
import type { SourceRecord } from "@/lib/personal-api";
import { SourceRecordDialog } from "./source-record-dialog";

const source: SourceRecord = { source_id: "33333333-3333-4333-8333-333333333333", project_id: projectFixture.project_id, source_type: "manual_note", title: "保存的依据", publisher: "", document_no: "", edition: "", published_on: "", valid_from: "", valid_to: "", source_url: "", locator: "", excerpt: "原始摘录", scope: {}, verification_state: "unverified", validity: "unknown", checked_at: null, check_note: "", revision: 1, created_at: "2026-09-30T00:00:00Z", updated_at: "2026-09-30T00:00:00Z", target_fingerprint: "old", attachments: [] };
beforeEach(installPersonalFixtures);

it("keeps a source draft on 409 and only rebases after the user reviews the latest source", async () => {
  const user = userEvent.setup(); const writes: { base_revision: number; changes: { excerpt: string } }[] = [];
  const latest = { ...source, revision: 2, excerpt: "另一个窗口保存的新原文" };
  server.use(http.get(`/api/sources/${source.source_id}`, () => HttpResponse.json({ source: latest })), http.patch(`/api/sources/${source.source_id}`, async ({ request }) => {
    const body = await request.json() as typeof writes[number]; writes.push(body);
    return body.base_revision === 1 ? HttpResponse.json({ detail: "版本已变化", code: "REVISION_CONFLICT", current_revision: 2 }, { status: 409 }) : HttpResponse.json({ source: { ...latest, excerpt: body.changes.excerpt, revision: 3 }, operation_id: "op" });
  }));
  let savedRevision = 0;
  renderWithApp(<SourceRecordDialog open onOpenChange={() => {}} source={source} onSaved={(result) => { savedRevision = result.revision; }} />);
  await waitFor(() => expect(screen.getByRole("button", { name: "保存来源记录" })).toBeEnabled());
  await user.clear(screen.getByLabelText("原文摘录")); await user.type(screen.getByLabelText("原文摘录"), "本次尚未提交的摘录");
  await user.click(screen.getByRole("button", { name: "保存来源记录" })); await screen.findByText(/本次草稿仍保留/); expect(writes).toHaveLength(1);
  await user.click(screen.getByRole("button", { name: "读取最新来源版本" })); await screen.findByText("另一个窗口保存的新原文");
  expect(screen.getByLabelText("原文摘录")).toHaveValue("本次尚未提交的摘录"); expect(screen.getByText(/本次编辑基于版本 1/)).toBeInTheDocument(); expect(writes).toHaveLength(1);
  await user.click(screen.getByRole("button", { name: "以最新版本为基础保留本次来源草稿" })); expect(writes).toHaveLength(1); expect(screen.getByLabelText("原文摘录")).toHaveValue("本次尚未提交的摘录");
  await user.click(screen.getByRole("button", { name: "保存来源记录" })); await waitFor(() => expect(savedRevision).toBe(3)); expect(writes[1]).toMatchObject({ base_revision: 2, changes: { excerpt: "本次尚未提交的摘录" } });
});
