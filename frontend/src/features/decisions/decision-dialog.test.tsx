import { beforeEach, describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/server";
import { renderWithApp } from "@/test/utils";
import { installPersonalFixtures, cardFixture, sourceFixture } from "@/features/project/test-fixtures";
import type { SourceChanges, SourceRecord } from "@/lib/personal-api";
import { DecisionDialog } from "./decision-dialog";

beforeEach(installPersonalFixtures);
const seed = { title: "防水方案", content_snapshot: { kind: "note" as const, text: "" }, evidence: [] };
describe("decision saves with receipts", () => {
  it("keeps custom tags independent of context and creates inline sources without submitting or selecting the parent", async () => {
    const user = userEvent.setup(); const sources: SourceRecord[] = []; const bodies: unknown[] = [];
    server.use(http.get("/api/facets", () => HttpResponse.json({ spaces: [], stages: [], categories: [], dimensions: [] })),
      http.get("/api/projects/:id/sources", () => HttpResponse.json({ sources, total: sources.length, offset: 0, limit: 100 })),
      http.post("/api/projects/:id/sources", async ({ request }) => { const { changes } = await request.json() as { changes: SourceChanges }; const source = { ...sourceFixture, ...changes }; sources.push(source); return HttpResponse.json({ source, operation_id: "source-op" }); }),
      http.post("/api/projects/:id/decision-cards", async ({ request }) => { bodies.push(await request.json()); return HttpResponse.json({ card: cardFixture, operation_id: "card-op" }); }));
    renderWithApp(<DecisionDialog seed={{ ...seed, context_snapshot: { 空间: "卫生间", 时间: "2026-10" } }} open onOpenChange={() => {}} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "保存决定" })).toBeEnabled());
    await user.type(screen.getByLabelText("决定空间标签"), "庭院"); await user.type(screen.getByLabelText("决定阶段标签"), "交付后复查");
    await user.type(screen.getByLabelText("决定理由"), "父表单草稿");
    await user.click(screen.getByRole("button", { name: "新建人工依据" }));
    const nested = await screen.findByRole("dialog", { name: "记录一份依据" });
    await user.type(within(nested).getByLabelText("资料标题"), "就地新增依据");
    await user.click(within(nested).getByRole("button", { name: "保存来源记录" }));
    const checkbox = await screen.findByRole("checkbox", { name: /就地新增依据/ });
    expect(checkbox).not.toBeChecked(); expect(bodies).toHaveLength(0);
    expect(screen.getByLabelText("决定理由")).toHaveValue("父表单草稿");
    await user.click(checkbox); await user.click(screen.getByRole("button", { name: "保存决定" }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toMatchObject({ changes: { space: "庭院", stage: "交付后复查", context_snapshot: { 空间: "卫生间", 时间: "2026-10" }, evidence_refs: [{ target_id: sourceFixture.source_id, target_fingerprint: "source-before" }] } });
  });
  it("permits a pending question, but requires an explicit decision for adoption", async () => {
    const user = userEvent.setup(); const bodies: unknown[] = [];
    server.use(http.post("/api/projects/:id/decision-cards", async ({ request }) => { bodies.push(await request.json()); return HttpResponse.json({ card: cardFixture, operation_id: "op" }); }));
    renderWithApp(<DecisionDialog seed={seed} open onOpenChange={() => {}} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "保存决定" })).toBeEnabled());
    await user.selectOptions(screen.getByLabelText("个人状态"), "adopted");
    expect(screen.getByRole("button", { name: "保存决定" })).toBeDisabled();
    await user.type(screen.getByLabelText("我的决定"), "先确认基层处理，再选择防水体系");
    await user.click(screen.getByRole("button", { name: "保存决定" }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toMatchObject({ primary_epoch: 1, changes: { state: "adopted", evidence_refs: [], decision_reason: "" } });
  });

  it("queries the original receipt after a lost response without creating another card", async () => {
    const user = userEvent.setup(); let operation = ""; let writes = 0;
    server.use(http.post("/api/projects/:id/decision-cards", async ({ request }) => { operation = (await request.json() as { operation_id: string }).operation_id; writes++; return HttpResponse.error(); }),
      http.get("/api/operations/:id", ({ params }) => { expect(params.id).toBe(operation); return HttpResponse.json({ operation_id: operation, response: { card: cardFixture, operation_id: operation }, response_status: 201 }); }));
    let saved = 0;
    renderWithApp(<DecisionDialog seed={seed} open onOpenChange={() => {}} onSaved={() => saved++} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "保存决定" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "保存决定" }));
    await screen.findByText(/尚未收到保存回执/);
    expect(screen.getByLabelText("决定标题")).toHaveValue("防水方案");
    expect(screen.getByRole("button", { name: "保存决定" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "查询提交结果" }));
    await waitFor(() => expect(saved).toBe(1));
    expect(writes).toBe(1);
  });

  it("retries the same immutable envelope after a network failure", async () => {
    const user = userEvent.setup(); const bodies: unknown[] = [];
    server.use(http.post("/api/projects/:id/decision-cards", async ({ request }) => { bodies.push(await request.json()); return bodies.length === 1 ? HttpResponse.error() : HttpResponse.json({ card: cardFixture, operation_id: "op" }); }));
    renderWithApp(<DecisionDialog seed={seed} open onOpenChange={() => {}} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "保存决定" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "保存决定" })); await screen.findByText(/尚未收到保存回执/);
    await user.click(screen.getByRole("button", { name: "使用同一请求重试" }));
    await waitFor(() => expect(bodies).toHaveLength(2)); expect(bodies[1]).toEqual(bodies[0]);
  });

  it("keeps the draft and explains revision conflicts", async () => {
    const user = userEvent.setup(); server.use(http.post("/api/projects/:id/decision-cards", () => HttpResponse.json({ detail: "依据已变化", code: "REVISION_CONFLICT", current_revision: 2 }, { status: 409 })));
    renderWithApp(<DecisionDialog seed={seed} open onOpenChange={() => {}} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "保存决定" })).toBeEnabled());
    await user.type(screen.getByLabelText("决定理由"), "这是我未提交的理由");
    await user.click(screen.getByRole("button", { name: "保存决定" }));
    await screen.findByText(/本次草稿仍保留/); expect(screen.getByLabelText("决定理由")).toHaveValue("这是我未提交的理由");
  });
});
