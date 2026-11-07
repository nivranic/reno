import { beforeEach, expect, it } from "vitest";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useLocation, useNavigate, type NavigateFunction } from "react-router";
import { http, HttpResponse } from "msw";
import { server } from "@/test/server";
import { renderWithApp } from "@/test/utils";
import { installPersonalFixtures, cardFixture, projectFixture, decisionShareFixture } from "@/features/project/test-fixtures";
import { personalKeys } from "@/lib/personal-api";
import DecisionsPage from "./decisions-page";

beforeEach(installPersonalFixtures);
it("restores all URL filters, preserves them through pagination, and resets pagination on a filter change", async () => {
  const user = userEvent.setup(); const requests: URLSearchParams[] = [];
  server.use(http.get("/api/projects/:id/decision-cards", ({ request }) => { const params = new URL(request.url).searchParams; requests.push(params); return HttpResponse.json({ cards: [{ ...cardFixture, space: "庭院", stage: "交付后复查" }], total: 100, offset: Number(params.get("offset")), limit: 30 }); }));
  function Harness() { const location = useLocation(); return <><DecisionsPage /><output data-testid="url-search">{location.search}</output></>; }
  renderWithApp(<Harness />, { route: "/?state=considering&space=庭院&stage=交付后复查&offset=30" });
  await screen.findByRole("heading", { name: "防水方案" });
  expect(screen.getByLabelText("筛选空间")).toHaveValue("庭院"); expect(screen.getByLabelText("筛选阶段")).toHaveValue("交付后复查");
  expect(requests.at(-1)?.get("offset")).toBe("30");
  await user.click(screen.getByRole("button", { name: "下一页" }));
  await waitFor(() => expect(requests.at(-1)?.get("offset")).toBe("60"));
  expect(requests.at(-1)?.get("space")).toBe("庭院"); expect(requests.at(-1)?.get("stage")).toBe("交付后复查");
  await user.selectOptions(screen.getByLabelText("筛选个人状态"), "adopted");
  await waitFor(() => expect(requests.at(-1)?.get("state")).toBe("adopted"));
  expect(requests.at(-1)?.get("offset")).toBe("0"); expect(requests.at(-1)?.get("space")).toBe("庭院");
  expect(screen.getByTestId("url-search").textContent).not.toContain("offset=");
  await user.click(screen.getByRole("button", { name: "清除筛选" }));
  await waitFor(() => expect(screen.getByTestId("url-search")).toBeEmptyDOMElement());
});

function installSelectedCards(total = 2) {
  const cards = [{ ...cardFixture, decision_text: "只预览所选结论", decision_reason: "所选理由" },
    { ...cardFixture, card_id: "44444444-4444-4444-8444-444444444444", title: "未选决定", decision_text: "未选私人结论" }];
  server.use(http.get("/api/projects/:id/decision-cards", ({ request }) => HttpResponse.json({
    cards, total, offset: Number(new URL(request.url).searchParams.get("offset")), limit: 30,
  })), http.post("/api/projects/:id/decision-cards/share-preview", async ({ request }) => {
    const body = await request.json() as { selection: { card_id: string; revision: number }[] };
    const summaries = body.selection.map(({ card_id, revision }) => cards.find((card) => card.card_id === card_id && card.revision === revision));
    if (summaries.some((card) => !card)) return HttpResponse.json({ detail: "选集变化", code: "SHARE_SELECTION_CHANGED" }, { status: 409 });
    return HttpResponse.json(decisionShareFixture(summaries.map((card) => ({ ...card!, decision_text: "服务端所选结论" }))));
  }));
}

it("previews the server snapshot for the current selected revision without writes, exports or another query cache", async () => {
  const user = userEvent.setup(); let writes = 0; let exports = 0; const previews: unknown[] = [];
  installSelectedCards();
  server.use(http.post("/api/projects/:id/decision-cards/share-preview", async ({ request }) => {
    previews.push(await request.json());
    return HttpResponse.json(decisionShareFixture([{ ...cardFixture, decision_text: "服务端所选结论" }]));
  }), http.post("*", () => { writes++; return HttpResponse.json({}); }),
    http.patch("*", () => { writes++; return HttpResponse.json({}); }),
    http.get("/api/decision-cards/:id/export", () => { exports++; return HttpResponse.text("unused"); }));
  const view = renderWithApp(<DecisionsPage />);
  await screen.findByRole("heading", { name: cardFixture.title });
  expect(screen.getByRole("button", { name: "预览分享摘要" })).toBeDisabled();
  await user.click(screen.getByLabelText(`选择${cardFixture.title}`));
  const keysBefore = view.client.getQueryCache().getAll().map((query) => query.queryHash).sort();
  await user.click(screen.getByRole("button", { name: "预览分享摘要" }));
  const dialog = screen.getByRole("dialog");
  expect(await within(dialog).findByText("服务端所选结论")).toBeVisible();
  expect(dialog).not.toHaveTextContent("只预览所选结论");
  expect(previews).toEqual([{ selection: [{ card_id: cardFixture.card_id, revision: cardFixture.revision }] }]);
  expect(dialog).not.toHaveTextContent("未选私人结论");
  expect(dialog).not.toHaveTextContent("未选决定");
  expect(view.client.getQueryCache().getAll().map((query) => query.queryHash).sort()).toEqual(keysBefore);
  expect(writes).toBe(0); expect(exports).toBe(0);
  await user.click(within(dialog).getByRole("button", { name: "关闭预览" }));
  await user.click(screen.getByLabelText(`选择${cardFixture.title}`));
  expect(screen.getByRole("button", { name: "预览分享摘要" })).toBeDisabled();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it.each(["?offset=30", "?state=adopted", "?space=厨房&stage=施工中"])("clears an open preview when the URL selection context changes to %s", async (search) => {
  const user = userEvent.setup(); installSelectedCards(100); let navigate!: NavigateFunction;
  let complete!: () => void; let pendingSignal: AbortSignal | undefined;
  const pending = new Promise<void>((resolve) => { complete = resolve; });
  server.use(http.post("/api/projects/:id/decision-cards/share-preview", async ({ request }) => {
    pendingSignal = request.signal; await pending;
    return HttpResponse.json(decisionShareFixture([{ ...cardFixture, decision_text: "旧URL的迟到摘要" }]));
  }));
  function Harness() { navigate = useNavigate(); return <DecisionsPage />; }
  renderWithApp(<Harness />);
  await screen.findByRole("heading", { name: cardFixture.title });
  await user.click(screen.getByLabelText(`选择${cardFixture.title}`));
  await user.click(screen.getByRole("button", { name: "预览分享摘要" }));
  expect(screen.getByRole("dialog")).toBeVisible();
  await waitFor(() => expect(pendingSignal).toBeDefined());
  await act(async () => navigate(search));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(pendingSignal?.aborted).toBe(true);
  await act(async () => complete());
  expect(screen.queryByText("旧URL的迟到摘要")).not.toBeInTheDocument();
  expect(await screen.findByLabelText(`选择${cardFixture.title}`)).not.toBeChecked();
  expect(screen.getByRole("button", { name: "预览分享摘要" })).toBeDisabled();
  await act(async () => navigate(-1));
  expect(await screen.findByLabelText(`选择${cardFixture.title}`)).not.toBeChecked();
  expect(screen.getByRole("button", { name: "预览分享摘要" })).toBeDisabled();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it.each([
  ["筛选空间", "厨房卫生间"],
  ["筛选阶段", "施工中验收"],
])("keeps the %s input and focus while typing multiple characters", async (label, value) => {
  const user = userEvent.setup(); installSelectedCards();
  renderWithApp(<DecisionsPage />);
  await screen.findByRole("heading", { name: cardFixture.title });
  await user.click(screen.getByLabelText(`选择${cardFixture.title}`));
  const input = screen.getByLabelText(label);
  await user.type(input, value);
  expect(screen.getByLabelText(label)).toBe(input);
  expect(input).toHaveValue(value);
  expect(input).toHaveFocus();
  expect(await screen.findByLabelText(`选择${cardFixture.title}`)).not.toBeChecked();
  expect(screen.getByRole("button", { name: "预览分享摘要" })).toBeDisabled();
});

it("commits each filter character synchronously through the data-router with ReactDOM.flushSync", async () => {
  installSelectedCards(100);
  function Harness() { const location = useLocation(); return <><DecisionsPage /><output data-testid="immediate-url-search">{location.search}</output></>; }
  renderWithApp(<Harness />, { route: "/?state=considering&offset=30" });
  await screen.findByRole("heading", { name: cardFixture.title });
  for (const [label, key, text] of [["筛选空间", "space", "卫生间"], ["筛选阶段", "stage", "泥瓦"]]) {
    const input = screen.getByLabelText(label);
    act(() => {
      input.focus();
      let value = "";
      for (const character of text) {
        value += character;
        fireEvent.change(input, { target: { value } });
        // Check within the event batch, before act can drain transitions.
        // This requires the real DOM provider's flush implementation.
        expect(screen.getByLabelText(label)).toBe(input);
        expect(input).toHaveValue(value);
        expect(input).toHaveFocus();
        const params = new URLSearchParams(screen.getByTestId("immediate-url-search").textContent ?? "");
        expect(params.get(key)).toBe(value);
        expect(params.has("offset")).toBe(false);
        expect(params.get("state")).toBe("considering");
      }
    });
  }
  const finalParams = new URLSearchParams(screen.getByTestId("immediate-url-search").textContent ?? "");
  expect(finalParams.get("space")).toBe("卫生间");
  expect(finalParams.get("stage")).toBe("泥瓦");
});

it("rejects missing selected IDs instead of substituting another current-page card", async () => {
  const user = userEvent.setup(); installSelectedCards();
  const view = renderWithApp(<DecisionsPage />);
  await screen.findByRole("heading", { name: cardFixture.title });
  await user.click(screen.getByLabelText(`选择${cardFixture.title}`));
  await user.click(screen.getByRole("button", { name: "预览分享摘要" }));
  await act(async () => { view.client.setQueriesData({ queryKey: personalKeys.cards(projectFixture.project_id) }, {
    cards: [{ ...cardFixture, card_id: "55555555-5555-4555-8555-555555555555", title: "替代决定" }], total: 1, offset: 0, limit: 30,
  }); });
  await screen.findByRole("heading", { name: "替代决定" });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "预览分享摘要" })).toBeDisabled();
  expect(screen.getByText("所选决定已不在当前页，请重新选择后预览。")).toBeVisible();
});

it.each(["permission", "scope"])("discards the open preview and selection when the read %s changes", async (kind) => {
  const user = userEvent.setup(); installSelectedCards();
  const view = renderWithApp(<DecisionsPage />);
  await screen.findByRole("heading", { name: cardFixture.title });
  await user.click(screen.getByLabelText(`选择${cardFixture.title}`));
  await user.click(screen.getByRole("button", { name: "预览分享摘要" }));
  await act(async () => { view.client.setQueryData(personalKeys.capabilities, {
    library_id: "personal-test", primary_epoch: kind === "scope" ? 2 : 1,
    personal_read: kind === "scope", personal_write: true, personal_upload: true, field_checks: true,
  }); });
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(screen.getByRole("button", { name: "预览分享摘要" })).toBeDisabled();
  if (kind === "scope") expect(await screen.findByLabelText(`选择${cardFixture.title}`)).not.toBeChecked();
});

it("removes the preview when the current query fails even if its prior data remains cached", async () => {
  const user = userEvent.setup(); installSelectedCards();
  const view = renderWithApp(<DecisionsPage />);
  await screen.findByRole("heading", { name: cardFixture.title });
  await user.click(screen.getByLabelText(`选择${cardFixture.title}`));
  await user.click(screen.getByRole("button", { name: "预览分享摘要" }));
  server.use(http.get("/api/projects/:id/decision-cards", () => HttpResponse.json({ detail: "读取失败" }, { status: 503 })));
  await act(async () => { await view.client.refetchQueries({ queryKey: personalKeys.cards(projectFixture.project_id) }); });
  await screen.findByRole("button", { name: "重试" });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "预览分享摘要" })).toBeDisabled();
  expect(view.client.getQueriesData({ queryKey: personalKeys.cards(projectFixture.project_id) }).some(([, value]) => value !== undefined)).toBe(true);
});


it("invalidates an already selected revision after the same ID changes in the current query", async () => {
  const user = userEvent.setup(); installSelectedCards(); const selections: unknown[] = [];
  let resolveOld!: () => void; let oldSignal: AbortSignal | undefined;
  const oldResponse = new Promise<void>((resolve) => { resolveOld = resolve; });
  server.use(http.post("/api/projects/:id/decision-cards/share-preview", async ({ request }) => {
    const body = await request.json() as { selection: { card_id: string; revision: number }[] };
    selections.push(body);
    if (body.selection[0].revision === 1) { oldSignal = request.signal; await oldResponse; }
    return HttpResponse.json(decisionShareFixture([{ ...cardFixture, revision: body.selection[0].revision,
      decision_text: body.selection[0].revision === 1 ? "迟到旧版本" : "已重选版本二" }]));
  }));
  const view = renderWithApp(<DecisionsPage />);
  await screen.findByRole("heading", { name: cardFixture.title });
  await user.click(screen.getByLabelText(`选择${cardFixture.title}`));
  await user.click(screen.getByRole("button", { name: "预览分享摘要" }));
  await waitFor(() => expect(oldSignal).toBeDefined());
  await act(async () => { view.client.setQueriesData({ queryKey: personalKeys.cards(projectFixture.project_id) }, {
    cards: [{ ...cardFixture, revision: 2, decision_text: "缓存版本二" }], total: 1, offset: 0, limit: 30,
  }); });
  expect(await screen.findByText("缓存版本二")).toBeVisible();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument(); expect(oldSignal?.aborted).toBe(true);
  expect(screen.getByRole("button", { name: "预览分享摘要" })).toBeDisabled();
  expect(screen.getByText("所选决定的版本已变化，请重新选择后预览。")).toBeVisible();
  expect(selections).toEqual([{ selection: [{ card_id: cardFixture.card_id, revision: 1 }] }]);
  await user.click(screen.getByLabelText(`选择${cardFixture.title}`));
  await user.click(screen.getByLabelText(`选择${cardFixture.title}`));
  await user.click(screen.getByRole("button", { name: "预览分享摘要" }));
  expect(await screen.findByText("已重选版本二")).toBeVisible();
  await act(async () => resolveOld());
  expect(screen.getByRole("dialog")).not.toHaveTextContent("迟到旧版本");
  expect(screen.getByRole("dialog")).not.toHaveTextContent("缓存版本二");
  expect(selections).toEqual([{ selection: [{ card_id: cardFixture.card_id, revision: 1 }] },
    { selection: [{ card_id: cardFixture.card_id, revision: 2 }] }]);
});

it("clears a conflicting selection before re-reading and requires a new checkbox choice", async () => {
  const user = userEvent.setup(); let currentRevision = 1; const selections: unknown[] = [];
  server.use(http.get("/api/projects/:id/decision-cards", () => HttpResponse.json({
    cards: [{ ...cardFixture, revision: currentRevision }], total: 1, offset: 0, limit: 30,
  })), http.post("/api/projects/:id/decision-cards/share-preview", async ({ request }) => {
    const body = await request.json() as { selection: { card_id: string; revision: number }[] };
    selections.push(body);
    if (body.selection[0].revision !== currentRevision) return HttpResponse.json({ code: "SHARE_SELECTION_CHANGED", detail: "版本已变化" }, { status: 409 });
    return HttpResponse.json(decisionShareFixture([{ ...cardFixture, revision: currentRevision, decision_text: "重新选择后的服务端结论" }]));
  }));
  renderWithApp(<DecisionsPage />); await screen.findByRole("heading", { name: cardFixture.title });
  await user.click(screen.getByLabelText(`选择${cardFixture.title}`)); currentRevision = 2;
  await user.click(screen.getByRole("button", { name: "预览分享摘要" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("重新读取列表后重新选择");
  await user.click(screen.getByRole("button", { name: "重新读取并重选" }));
  await waitFor(() => expect(screen.getByText(/版本 2/)).toBeVisible());
  expect(screen.getByLabelText(`选择${cardFixture.title}`)).not.toBeChecked();
  expect(screen.getByRole("button", { name: "预览分享摘要" })).toBeDisabled();
  expect(selections).toHaveLength(1);
  await user.click(screen.getByLabelText(`选择${cardFixture.title}`));
  await user.click(screen.getByRole("button", { name: "预览分享摘要" }));
  expect(await screen.findByText("重新选择后的服务端结论")).toBeVisible();
  expect(selections).toEqual([{ selection: [{ card_id: cardFixture.card_id, revision: 1 }] },
    { selection: [{ card_id: cardFixture.card_id, revision: 2 }] }]);
});
