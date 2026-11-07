import { beforeEach, expect, it, vi } from "vitest";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/server";
import { renderWithApp } from "@/test/utils";
import { installPersonalFixtures, cardFixture } from "@/features/project/test-fixtures";
import { personalKeys } from "@/lib/personal-api";
import DecisionDetailPage from "./decision-detail-page";

vi.mock("react-router", async (original) => ({ ...await original<typeof import("react-router")>(), useParams: () => ({ cardId: "22222222-2222-4222-8222-222222222222" }) }));
beforeEach(installPersonalFixtures);

it("preserves unsaved parent tags and reason after explicitly updating evidence", async () => {
  const user = userEvent.setup(); let card = structuredClone(cardFixture); let patch: unknown;
  server.use(http.get("/api/decision-cards/:id", () => HttpResponse.json({ card })),
    http.post("/api/decision-cards/:id/evidence", () => { card = { ...card, revision: 2 }; return HttpResponse.json({ card, operation_id: "evidence-op" }); }),
    http.patch("/api/decision-cards/:id", async ({ request }) => { patch = await request.json(); return HttpResponse.json({ card, operation_id: "card-op" }); }));
  renderWithApp(<DecisionDetailPage />);
  await waitFor(() => expect(screen.getByLabelText("决定理由")).toBeEnabled());
  await user.type(await screen.findByLabelText("决定理由"), "尚未提交的父草稿"); await user.type(screen.getByLabelText("决定空间标签"), "庭院");
  expect(screen.getByLabelText("决定理由")).toHaveValue("尚未提交的父草稿");
  expect(screen.getByLabelText("决定空间标签")).toHaveValue("庭院");
  await user.click(screen.getByRole("button", { name: "检查并更新依据" }));
  await user.click(await screen.findByRole("button", { name: "确认更新为新版本" }));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "核对依据变化" })).not.toBeInTheDocument());
  expect(screen.getByLabelText("决定理由")).toHaveValue("尚未提交的父草稿"); expect(screen.getByLabelText("决定空间标签")).toHaveValue("庭院");
  await user.click(screen.getByRole("button", { name: "保存修改" }));
  await waitFor(() => expect(patch).toMatchObject({ base_revision: 2, changes: { space: "庭院", decision_reason: "尚未提交的父草稿" } }));
});

it("freezes evidence CAS across refetch and requires explicit review before rebasing", async () => {
  const user = userEvent.setup(); let card = structuredClone(cardFixture); const bases: number[] = [];
  server.use(http.get("/api/decision-cards/:id", () => HttpResponse.json({ card })),
    http.post("/api/decision-cards/:id/evidence", async ({ request }) => { const body = await request.json() as { base_revision: number }; bases.push(body.base_revision); if (body.base_revision !== card.revision) return HttpResponse.json({ code: "REVISION_CONFLICT", detail: "服务器已更新" }, { status: 409 }); card = { ...card, revision: card.revision + 1 }; return HttpResponse.json({ card, operation_id: "evidence-op" }); }));
  const view = renderWithApp(<DecisionDetailPage />);
  await user.click(await screen.findByRole("button", { name: "检查并更新依据" }));
  await screen.findByRole("dialog", { name: "核对依据变化" });
  card = { ...card, revision: 2, decision_reason: "另一窗口的新理由" };
  await act(async () => { await view.client.invalidateQueries({ queryKey: personalKeys.card(card.card_id) }); });
  await user.click(screen.getByRole("button", { name: "确认更新为新版本" }));
  await screen.findByText(/本次依据选择基于决定版本 1/); expect(bases).toEqual([1]);
  await user.click(screen.getByRole("button", { name: "读取最新决定版本" }));
  await screen.findByText("理由：另一窗口的新理由");
  await user.click(screen.getByRole("button", { name: "核对后以最新版本保留本次依据选择" }));
  await user.click(screen.getByRole("button", { name: "确认更新为新版本" }));
  await waitFor(() => expect(bases).toEqual([1, 2]));
});
