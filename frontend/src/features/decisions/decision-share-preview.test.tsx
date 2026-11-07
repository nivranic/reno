import { beforeEach, expect, it, vi } from "vitest";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "@/test/server";
import { makeTestQueryClient } from "@/test/utils";
import { cardFixture, projectFixture, decisionShareFixture, installPersonalFixtures } from "@/features/project/test-fixtures";
import { usePersonalProject } from "@/features/project/use-personal-project";
import { personalKeys, type DecisionShareSelection, type DecisionSharePreviewResponse } from "@/lib/personal-api";
import { DecisionSharePreview } from "./decision-share-preview";

const selection = [{ card_id: cardFixture.card_id, revision: cardFixture.revision }];
const path = "/api/projects/:id/decision-cards/share-preview";
beforeEach(installPersonalFixtures);

function Preview({ available = true, selected = selection, onSelectionChanged = () => {} }: {
  available?: boolean; selected?: DecisionShareSelection[]; onSelectionChanged?: () => void;
}) {
  const access = usePersonalProject();
  return <DecisionSharePreview projectId={projectFixture.project_id} selection={selected} read={access.read}
    available={available && access.readable && !!access.project.data?.project} onSelectionChanged={onSelectionChanged} />;
}
function renderPreview(props: Parameters<typeof Preview>[0] = {}) {
  const client = makeTestQueryClient();
  return { client, ...render(<Preview {...props} />, { wrapper: ({ children }) =>
    <QueryClientProvider client={client}>{children}</QueryClientProvider> }) };
}
async function openPreview(user: ReturnType<typeof userEvent.setup>) {
  await waitFor(() => expect(screen.getByRole("button", { name: "预览分享摘要" })).toBeEnabled());
  const trigger = screen.getByRole("button", { name: "预览分享摘要" });
  await user.click(trigger);
  return trigger;
}
function deferredPreview() {
  let resolve!: (value: DecisionSharePreviewResponse) => void;
  const result = new Promise<DecisionSharePreviewResponse>((done) => { resolve = done; });
  return { result, resolve };
}

it("requests only after click and renders server summary values as literal text without private payload", async () => {
  const user = userEvent.setup(); let calls = 0;
  const card = { ...cardFixture, title: '<img src="/private-image">',
    decision_text: "[服务端结论](https://example.invalid/private)", decision_reason: "<script>服务端原理由</script>",
    revision: 12, needs_recheck: true, question: "private-question", context_snapshot: { 住址: "private-context" },
    content_snapshot: { kind: "note", text: "private-original-content" }, profile: { city: "private-profile" },
    attachments: [{ original_name: "private-attachment" }], evidence_refs: [{ target_id: "private-evidence" }],
  };
  server.use(http.post(path, ({ request }) => {
    calls++; expect(request.headers.get("Content-Type")).toBe("application/json");
    return HttpResponse.json(decisionShareFixture([card]));
  }));
  const view = renderPreview({ selected: [{ card_id: cardFixture.card_id, revision: 12 }] });
  await waitFor(() => expect(screen.getByRole("button", { name: "预览分享摘要" })).toBeEnabled());
  expect(calls).toBe(0);
  const keys = view.client.getQueryCache().getAll().map((query) => query.queryHash).sort();
  await openPreview(user);
  await screen.findByText(card.decision_text);
  const dialog = screen.getByRole("dialog", { name: "分享摘要预览" });
  expect(within(dialog).getByText(card.title)).toBeVisible();
  expect(within(dialog).getByText(card.decision_reason)).toBeVisible();
  expect(within(dialog).getByText("版本 12")).toBeVisible();
  expect(within(dialog).getByText("依据需复看")).toBeVisible();
  expect(within(dialog).getByText("摘要预览，尚未生成分享链接。")).toBeVisible();
  for (const value of [card.card_id, card.project_id, "private-question", "private-context", "private-original-content",
    "private-profile", "private-attachment", "private-evidence"]) expect(dialog).not.toHaveTextContent(value);
  expect(dialog.querySelector("a,img,script,iframe,input,textarea")).toBeNull();
  expect(view.client.getQueryCache().getAll().map((query) => query.queryHash).sort()).toEqual(keys);
  expect(calls).toBe(1);
});

it("shows server empty-text labels, closes on Escape and returns focus to the trigger", async () => {
  const user = userEvent.setup();
  server.use(http.post(path, () => HttpResponse.json(decisionShareFixture([{ ...cardFixture, decision_text: "  ", decision_reason: "" }]))));
  renderPreview(); const trigger = await openPreview(user);
  expect(await screen.findByText("尚未决定")).toBeVisible();
  expect(within(screen.getByRole("dialog")).getByText("未填写理由")).toBeVisible();
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
});

it("removes data on lost availability and requires a fresh click after it returns", async () => {
  const user = userEvent.setup(); let calls = 0;
  server.use(http.post(path, () => { calls++; return HttpResponse.json(decisionShareFixture([cardFixture])); }));
  const view = renderPreview(); await openPreview(user); await screen.findByText("尚未决定");
  view.rerender(<Preview available={false} />);
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "预览分享摘要" })).toBeDisabled();
  view.rerender(<Preview />);
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument(); expect(calls).toBe(1);
  await openPreview(user); await screen.findByText("尚未决定"); expect(calls).toBe(2);
});

it("shows loading without cached text and retries an error only after an explicit click", async () => {
  const user = userEvent.setup(); const pending = deferredPreview(); let calls = 0;
  server.use(http.post(path, async () => {
    calls++; if (calls === 1) { await pending.result; return HttpResponse.json({ detail: "摘要暂不可用" }, { status: 503 }); }
    return HttpResponse.json(decisionShareFixture([{ ...cardFixture, decision_text: "重试后的服务端结论" }]));
  }));
  renderPreview(); await openPreview(user);
  expect(screen.getByRole("status", { name: "加载中" })).toBeVisible();
  expect(screen.getByText("读取分享摘要…")).toBeVisible();
  expect(screen.getByRole("dialog")).not.toHaveTextContent(cardFixture.title);
  await act(async () => pending.resolve(decisionShareFixture([cardFixture])));
  expect(await screen.findByRole("alert")).toHaveTextContent("摘要暂不可用"); expect(calls).toBe(1);
  await user.click(screen.getByRole("button", { name: "重试" }));
  expect(await screen.findByText("重试后的服务端结论")).toBeVisible(); expect(calls).toBe(2);
});

it("requires re-reading and re-selection after a 409 instead of retrying the stale revision", async () => {
  const user = userEvent.setup(); const reselect = vi.fn(); let calls = 0;
  server.use(http.post(path, () => { calls++; return HttpResponse.json({ detail: "选集变化", code: "SHARE_SELECTION_CHANGED" }, { status: 409 }); }));
  renderPreview({ onSelectionChanged: reselect }); await openPreview(user);
  expect(await screen.findByRole("alert")).toHaveTextContent("重新读取列表后重新选择");
  expect(screen.queryByRole("button", { name: "重试" })).not.toBeInTheDocument(); expect(calls).toBe(1);
  await user.click(screen.getByRole("button", { name: "重新读取并重选" })); expect(reselect).toHaveBeenCalledOnce();
});

it("aborts on close and ignores a late old response after a new preview opens", async () => {
  const user = userEvent.setup(); const old = deferredPreview(); let calls = 0; let firstSignal: AbortSignal | undefined;
  server.use(http.post(path, async ({ request }) => {
    calls++; if (calls === 1) { firstSignal = request.signal; return HttpResponse.json(await old.result); }
    return HttpResponse.json(decisionShareFixture([{ ...cardFixture, decision_text: "第二次服务端结论" }]));
  }));
  renderPreview(); await openPreview(user); await waitFor(() => expect(firstSignal).toBeDefined());
  await user.click(screen.getByRole("button", { name: "关闭预览" }));
  expect(firstSignal?.aborted).toBe(true);
  await openPreview(user); expect(await screen.findByText("第二次服务端结论")).toBeVisible();
  await act(async () => old.resolve(decisionShareFixture([{ ...cardFixture, decision_text: "迟到的旧结论" }])));
  expect(screen.getByRole("dialog")).not.toHaveTextContent("迟到的旧结论");
});

it.each(["availability", "selection", "scope"])("aborts pending preview when %s changes and discards its late content", async (change) => {
  const user = userEvent.setup(); const pending = deferredPreview(); let signal: AbortSignal | undefined;
  server.use(http.post(path, async ({ request }) => { signal = request.signal; return HttpResponse.json(await pending.result); }));
  const view = renderPreview(); await openPreview(user); await waitFor(() => expect(signal).toBeDefined());
  if (change === "availability") view.rerender(<Preview available={false} />);
  else if (change === "selection") view.rerender(<Preview selected={[{ card_id: cardFixture.card_id, revision: 2 }]} />);
  else await act(async () => { view.client.setQueryData(personalKeys.capabilities,
    { library_id: "personal-test", primary_epoch: 2, personal_read: true, personal_write: true, personal_upload: true, field_checks: true }); });
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(signal?.aborted).toBe(true);
  await act(async () => pending.resolve(decisionShareFixture([{ ...cardFixture, decision_text: "已失效的摘要" }])));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.queryByText("已失效的摘要")).not.toBeInTheDocument();
});


it("never retains a previous successful snapshot while a new open fails", async () => {
  const user = userEvent.setup(); let calls = 0;
  server.use(http.post(path, () => {
    calls++; return calls === 1 ? HttpResponse.json(decisionShareFixture([{ ...cardFixture, decision_text: "上次服务端摘要" }]))
      : HttpResponse.json({ detail: "本次读取失败" }, { status: 503 });
  }));
  renderPreview(); await openPreview(user); expect(await screen.findByText("上次服务端摘要")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "关闭预览" }));
  await openPreview(user); expect(await screen.findByRole("alert")).toHaveTextContent("本次读取失败");
  expect(screen.getByRole("dialog")).not.toHaveTextContent("上次服务端摘要"); expect(calls).toBe(2);
});
