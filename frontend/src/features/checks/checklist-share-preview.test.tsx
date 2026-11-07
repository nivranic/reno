import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider } from "@tanstack/react-query";
import { createMemoryRouter, RouterProvider } from "react-router";
import { http, HttpResponse } from "msw";
import { server } from "@/test/server";
import { makeTestQueryClient } from "@/test/utils";
import { installPersonalFixtures, projectFixture } from "@/features/project/test-fixtures";
import { usePersonalProject } from "@/features/project/use-personal-project";
import { checklistShareSelection, personalKeys, previewChecklistShare, type CheckEvent, type CheckItem,
  type Checklist, type ChecklistSharePreviewResponse, type ChecklistShareSummary, type PersonalCapabilities, type PersonalRead } from "@/lib/personal-api";
import { setToken } from "@/lib/api";
import { ChecklistSharePreview } from "./checklist-share-preview";
import CheckDetailPage from "./check-detail-page";

const path = "/api/projects/:id/checklists/share-preview";
const capabilities: PersonalCapabilities = { library_id: "personal-test", primary_epoch: 1, personal_read: true,
  personal_write: true, personal_upload: true, field_checks: true };
const local: PersonalRead = { mode: "local", version: null, scope: "api-local" };
const date = "2026-10-09T00:00:00Z";
const event: CheckEvent = { check_id: "private-check", item_revision: 2, commit_seq: 4, record_kind: "inspection",
  result: "unable_to_verify", measurements: [{ key: "private-measurement", value: "private-value" }], note: "private-note",
  attachment_refs: ["private-attachment"], checked_at: date, created_at: date, criteria_snapshot_hash: "private-hash" };
const item: CheckItem = { item_id: "private-item-1", instance_id: "private-list", title: "本地旧核对项",
  revision: 2, conditions: { 地址: "private-address" }, method_text: "private-method", basis_kind: "observation_only",
  criteria_state: "needs_verification", criteria: { text: "private-criteria", conditions_confirmed: false,
    requirement_confirmed: false, required_observations: [] }, refs: [], checks: [event,
    { ...event, check_id: "private-void", commit_seq: 9, record_kind: "void", result: null, checked_at: "2020-01-01T00:00:00Z", voided: true },
    { ...event, check_id: "private-backdated", commit_seq: 7, checked_at: "2021-01-01T00:00:00Z" }],
  current_result: "unable_to_verify", needs_recheck: false, criteria_snapshot_hash: "private-hash",
  pass_eligibility: { allowed: false, reasons: ["待核实"] } };
const checklist: Checklist = { instance_id: "private-list", project_id: projectFixture.project_id, title: "本地旧清单",
  space: "private-space", stage: "private-stage", revision: 3, state: "active", lifecycle_state: "active",
  progress: { complete: 0, total: 2 }, items: [item, { ...item, item_id: "private-item-2", revision: 1, checks: [], current_result: "unchecked" }],
  created_at: date, updated_at: date };
const selection = [{ instance_id: "private-list", revision: 3,
  items: [{ item_id: "private-item-1", revision: 2, last_commit_seq: 9 }, { item_id: "private-item-2", revision: 1, last_commit_seq: 0 }] }];
function summary(list = checklist): ChecklistShareSummary {
  return { title: "服务端冻结清单", state: list.state, lifecycle_state: list.lifecycle_state, revision: list.revision,
    updated_at: list.updated_at, progress: { ...list.progress }, items: list.items.map((entry, index) => ({
      title: `服务端核对项 ${index + 1}`, revision: entry.revision, current_result: entry.current_result,
      needs_recheck: entry.needs_recheck, basis_kind: entry.basis_kind,
    })) };
}
function response(row = summary(), version: string | null = null): ChecklistSharePreviewResponse {
  return { snapshot: { format_version: 1, kind: "personal_checklist_share_snapshot", checklists: [row] },
    snapshot_sha256: "a".repeat(64), personal_read_version: version };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}
beforeEach(() => { installPersonalFixtures(); sessionStorage.clear(); });
afterEach(() => { cleanup(); setToken(""); vi.restoreAllMocks(); });

function Preview({ list = checklist, available = true, viewVersion = 1, onSelectionChanged = () => {} }: {
  list?: Checklist; available?: boolean; viewVersion?: number; onSelectionChanged?: () => void;
}) {
  const access = usePersonalProject();
  return <ChecklistSharePreview projectId={list.project_id} selection={checklistShareSelection(list)} read={access.read}
    available={available && access.fieldReadable} viewVersion={viewVersion} onSelectionChanged={onSelectionChanged} />;
}
function renderPreview(props: Parameters<typeof Preview>[0] = {}) {
  const client = makeTestQueryClient();
  return { client, ...render(<Preview {...props} />, { wrapper: ({ children }) =>
    <QueryClientProvider client={client}>{children}</QueryClientProvider> }) };
}
async function openPreview(user: ReturnType<typeof userEvent.setup>) {
  await waitFor(() => expect(screen.getByRole("button", { name: "预览本人摘要" })).toBeEnabled());
  const trigger = screen.getByRole("button", { name: "预览本人摘要" }); await user.click(trigger); return trigger;
}
function renderPage(writable = true) {
  server.use(http.get("/api/capabilities", () => HttpResponse.json({ ...capabilities, personal_write: writable })),
    http.get("/api/checklists/:id", () => HttpResponse.json({ checklist })),
    http.post(path, () => HttpResponse.json(response())));
  const client = makeTestQueryClient();
  const router = createMemoryRouter([{ path: "/checks/:instanceId", element: <CheckDetailPage /> }],
    { initialEntries: ["/checks/private-list"] });
  return { client, ...render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>) };
}

it("binds all items in detail order to max commit sequence including voids and backdated entries, sends no private values", async () => {
  expect(checklistShareSelection(checklist)).toEqual(selection);
  const version = "b".repeat(64); setToken("synthetic-checklist-token");
  const row = { ...summary(), instance_id: "excluded-id", criteria: "excluded-criteria",
    progress: { ...summary().progress, secret: "excluded-progress" },
    items: summary().items.map((entry) => ({ ...entry, item_id: "excluded-item", note: "excluded-note", refs: ["excluded-ref"] })) };
  server.use(http.post(path, async ({ request }) => {
    expect(await request.json()).toEqual({ selection });
    expect(request.headers.get("X-Reno-Token")).toBe("synthetic-checklist-token");
    expect(request.headers.get("X-Reno-Personal-Read-Version")).toBe(version);
    return HttpResponse.json(response(row, version), { headers: { "X-Reno-Personal-Read-Version": version } });
  }));
  const controller = new AbortController(); const fetchSpy = vi.spyOn(globalThis, "fetch");
  const originalSelection = [{ ...selection[0], private_field: "not-sent", items: selection[0].items.map((entry) => ({ ...entry, note: "not-sent" })) }];
  const result = await previewChecklistShare(checklist.project_id, originalSelection,
    { mode: "mirror", version, scope: "api-mirror" }, controller.signal);
  expect(result).toEqual(response(summary(), version));
  expect(JSON.stringify(result)).not.toContain("excluded");
  expect(fetchSpy.mock.calls[0][1]?.signal).toBe(controller.signal);
});

it.each(["checklist-count", "item-count", "parent-revision", "item-revision", "progress-total", "progress-complete", "state", "lifecycle", "basis", "read-version", "hash"])("rejects malformed or substituted %s without retaining the raw payload", async (kind) => {
  const value = response(); const row = value.snapshot.checklists[0];
  if (kind === "checklist-count") value.snapshot.checklists.push(row);
  if (kind === "item-count") row.items.pop();
  if (kind === "parent-revision") row.revision++;
  if (kind === "item-revision") row.items[0].revision++;
  if (kind === "progress-total") row.progress.total++;
  if (kind === "progress-complete") row.progress.complete = 1;
  if (kind === "state") row.state = "completed";
  if (kind === "lifecycle") Object.assign(row, { lifecycle_state: ["active"] });
  if (kind === "basis") Object.assign(row.items[0], { basis_kind: "private-unknown-basis" });
  if (kind === "read-version") Object.assign(value, { personal_read_version: undefined });
  if (kind === "hash") value.snapshot_sha256 = "private-invalid-hash";
  server.use(http.post(path, () => HttpResponse.json(value)));
  await expect(previewChecklistShare(checklist.project_id, selection, local)).rejects.toMatchObject({ status: 0, message: "清单摘要响应无效，请重试。" });
});

it.each(["header", "body"])("invalidates a changed mirror %s version", async (kind) => {
  const version = "b".repeat(64); const changed = vi.fn(); window.addEventListener("reno-personal-version-changed", changed);
  server.use(http.post(path, () => HttpResponse.json(response(summary(), kind === "body" ? "c".repeat(64) : version),
    { headers: { "X-Reno-Personal-Read-Version": kind === "header" ? "c".repeat(64) : version } })));
  try {
    await expect(previewChecklistShare(checklist.project_id, selection, { mode: "mirror", version, scope: "mirror" }))
      .rejects.toMatchObject({ status: 409, code: "PERSONAL_MIRROR_READ_VERSION_CHANGED" });
    expect(changed).toHaveBeenCalledOnce();
  } finally { window.removeEventListener("reno-personal-version-changed", changed); }
});

it("refuses an unconfirmed mirror, invalid selection, and already cancelled request before fetch", async () => {
  const fetchSpy = vi.spyOn(globalThis, "fetch"); const controller = new AbortController(); controller.abort();
  await expect(previewChecklistShare(checklist.project_id, selection, { mode: "mirror", version: null, scope: "mirror" })).rejects.toMatchObject({ status: 403 });
  await expect(previewChecklistShare(checklist.project_id, [], local)).rejects.toMatchObject({ status: 0 });
  await expect(previewChecklistShare(checklist.project_id, [{ ...selection[0], items: [] }], local)).rejects.toMatchObject({ status: 0 });
  await expect(previewChecklistShare(checklist.project_id, [{ ...selection[0], items: Array.from({ length: 51 }, (_, index) => ({ ...selection[0].items[0], item_id: `item-${index}` })) }], local)).rejects.toMatchObject({ status: 0 });
  await expect(previewChecklistShare(checklist.project_id, selection, local, controller.signal)).rejects.toMatchObject({ name: "AbortError" });
  expect(fetchSpy).not.toHaveBeenCalled();
});

it("reads only on click, uses server literal text and excludes private fields from the dialog and query cache", async () => {
  const user = userEvent.setup(); let calls = 0;
  const row = { ...summary(), ...checklist, title: '<img src="/private-image">', items: checklist.items.map((entry) => ({
    ...entry, title: '[服务端核对项](https://example.invalid/private)<script>literal</script>', needs_recheck: true,
  })) };
  server.use(http.post(path, async ({ request }) => { calls++; expect(await request.json()).toEqual({ selection }); return HttpResponse.json(response(row)); }));
  const view = renderPreview(); await waitFor(() => expect(screen.getByRole("button", { name: "预览本人摘要" })).toBeEnabled());
  expect(calls).toBe(0); const keys = view.client.getQueryCache().getAll().map((query) => query.queryHash).sort();
  const trigger = await openPreview(user); await screen.findByText(row.title);
  const dialog = screen.getByRole("dialog", { name: "现场清单本人摘要" });
  expect(within(dialog).getByText("摘要预览，尚未生成分享链接。")).toBeVisible();
  expect(dialog).toHaveTextContent("不等于工程验收合格"); expect(dialog).toHaveTextContent("请自行核对是否包含私人信息");
  expect(dialog).toHaveTextContent(row.items[0].title); expect(dialog).toHaveTextContent("依据需复看");
  for (const privateValue of [checklist.instance_id, checklist.project_id, checklist.space, checklist.stage, item.item_id,
    item.criteria.text, item.method_text, event.note, event.measurements[0].key, event.attachment_refs[0]]) expect(dialog).not.toHaveTextContent(privateValue);
  expect(dialog.querySelector("a,img,script,iframe,input,textarea")).toBeNull();
  expect(view.client.getQueryCache().getAll().map((query) => query.queryHash).sort()).toEqual(keys);
  await user.keyboard("{Escape}"); expect(screen.queryByRole("dialog")).not.toBeInTheDocument(); expect(trigger).toHaveFocus();
});

it("aborts on close and ignores the late old response after reopening", async () => {
  const user = userEvent.setup(); const old = deferred<ChecklistSharePreviewResponse>(); let signal: AbortSignal | undefined; let calls = 0;
  server.use(http.post(path, async ({ request }) => { calls++; if (calls === 1) { signal = request.signal; return HttpResponse.json(await old.promise); }
    return HttpResponse.json(response({ ...summary(), title: "第二次服务端摘要" })); }));
  renderPreview(); await openPreview(user); await waitFor(() => expect(signal).toBeDefined());
  expect(screen.getByText("读取清单摘要…")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "关闭预览" })); expect(signal?.aborted).toBe(true);
  await openPreview(user); expect(await screen.findByText("第二次服务端摘要")).toBeVisible();
  await act(async () => old.resolve(response({ ...summary(), title: "迟到的私人旧摘要" })));
  expect(screen.getByRole("dialog")).not.toHaveTextContent("迟到的私人旧摘要");
});

it.each(["availability", "commit", "view-version", "scope", "permission"])("closes and cancels pending data on %s changes, without reopening on restoration", async (kind) => {
  const user = userEvent.setup(); const pending = deferred<ChecklistSharePreviewResponse>(); let signal: AbortSignal | undefined;
  server.use(http.post(path, async ({ request }) => { signal = request.signal; return HttpResponse.json(await pending.promise); }));
  const view = renderPreview(); await openPreview(user); await waitFor(() => expect(signal).toBeDefined());
  if (kind === "availability") view.rerender(<Preview available={false} />);
  else if (kind === "commit") view.rerender(<Preview list={{ ...checklist, items: [{ ...item, checks: [...item.checks, { ...event, commit_seq: 10 }] }, checklist.items[1]] }} />);
  else if (kind === "view-version") view.rerender(<Preview viewVersion={2} />);
  else await act(async () => { view.client.setQueryData(personalKeys.capabilities,
    { ...capabilities, ...(kind === "scope" ? { primary_epoch: 2 } : { field_checks: false }) }); });
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument()); expect(signal?.aborted).toBe(true);
  await act(async () => pending.resolve(response({ ...summary(), title: "已失效的摘要" })));
  if (kind === "scope" || kind === "permission") await act(async () => { view.client.setQueryData(personalKeys.capabilities, capabilities); });
  else view.rerender(<Preview />);
  await waitFor(() => expect(screen.getByRole("button", { name: "预览本人摘要" })).toBeEnabled());
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument(); expect(screen.queryByText("已失效的摘要")).not.toBeInTheDocument();
});

it("never retries stale selection on 409 and closes before asking the parent to reread", async () => {
  const user = userEvent.setup(); const refresh = vi.fn(); let calls = 0;
  server.use(http.post(path, () => { calls++; return HttpResponse.json({ detail: "选集已变化", code: "SHARE_SELECTION_CHANGED" }, { status: 409 }); }));
  renderPreview({ onSelectionChanged: refresh }); await openPreview(user);
  expect(await screen.findByRole("alert")).toHaveTextContent("重新读取清单后再预览");
  expect(screen.queryByRole("button", { name: "重试" })).not.toBeInTheDocument(); expect(calls).toBe(1);
  await user.click(screen.getByRole("button", { name: "重新读取清单" }));
  expect(refresh).toHaveBeenCalledOnce(); expect(screen.queryByRole("dialog")).not.toBeInTheDocument(); expect(calls).toBe(1);
});

it("removes a previous successful snapshot when a subsequent explicit read fails", async () => {
  const user = userEvent.setup(); let calls = 0;
  server.use(http.post(path, () => ++calls === 1 ? HttpResponse.json(response()) : HttpResponse.json({ detail: "本次摘要读取失败" }, { status: 503 })));
  renderPreview(); await openPreview(user); await screen.findByText("服务端冻结清单");
  await user.click(screen.getByRole("button", { name: "关闭预览" })); await openPreview(user);
  expect(await screen.findByRole("alert")).toHaveTextContent("本次摘要读取失败");
  expect(screen.getByRole("dialog")).not.toHaveTextContent("服务端冻结清单"); expect(calls).toBe(2);
});

it.each([true, false])("wires the real detail page for writable=%s with exact selection and server-only summary", async (writable) => {
  const user = userEvent.setup(); renderPage(writable); let calls = 0;
  server.use(http.post(path, async ({ request }) => { calls++; expect(await request.json()).toEqual({ selection }); return HttpResponse.json(response()); }));
  await screen.findByRole("heading", { name: checklist.title }); expect(calls).toBe(0);
  await openPreview(user); expect(await screen.findByText("服务端冻结清单")).toBeVisible();
  expect(screen.getByRole("dialog")).not.toHaveTextContent(checklist.title); expect(calls).toBe(1);
});

it.each(["append", "recheck"])("closes detail preview for %s changes without a parent revision increment", async (kind) => {
  const user = userEvent.setup(); const view = renderPage(); await openPreview(user); await screen.findByText("服务端冻结清单");
  const query = view.client.getQueryCache().find({ queryKey: personalKeys.checklist(checklist.instance_id), exact: false })!;
  const changed = { ...checklist, items: [{ ...item, ...(kind === "append"
    ? { checks: [...item.checks, { ...event, check_id: "private-new-check", commit_seq: 10 }] } : { needs_recheck: true }) }, checklist.items[1]] };
  await act(async () => { view.client.setQueryData(query.queryKey, { checklist: changed },
    { updatedAt: query.state.dataUpdatedAt }); });
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(screen.getByRole("button", { name: "预览本人摘要" })).toBeEnabled();
});

it("closes on detail refetch, clears on its failure, and does not reopen after recovery", async () => {
  const user = userEvent.setup(); const view = renderPage(); await openPreview(user); await screen.findByText("服务端冻结清单");
  const pending = deferred<void>(); server.use(http.get("/api/checklists/:id", async () => { await pending.promise; return HttpResponse.json({ detail: "清单刷新失败" }, { status: 503 }); }));
  let refresh!: Promise<void>;
  await act(async () => { refresh = view.client.refetchQueries({ queryKey: personalKeys.checklist(checklist.instance_id) }); });
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(screen.getByRole("button", { name: "预览本人摘要" })).toBeDisabled();
  await act(async () => { pending.resolve(); await refresh; }); expect(await screen.findByRole("alert")).toHaveTextContent("清单刷新失败");
  server.use(http.get("/api/checklists/:id", () => HttpResponse.json({ checklist })));
  await user.click(screen.getByRole("button", { name: "重试" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "预览本人摘要" })).toBeEnabled());
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("closes on a capabilities refresh even when the same permission returns", async () => {
  const user = userEvent.setup(); const view = renderPage(); await openPreview(user); await screen.findByText("服务端冻结清单");
  const pending = deferred<void>(); server.use(http.get("/api/capabilities", async () => { await pending.promise; return HttpResponse.json(capabilities); }));
  let refresh!: Promise<void>;
  await act(async () => { refresh = view.client.refetchQueries({ queryKey: personalKeys.capabilities }); });
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(screen.getByRole("button", { name: "预览本人摘要" })).toBeDisabled();
  await act(async () => { pending.resolve(); await refresh; });
  await waitFor(() => expect(screen.getByRole("button", { name: "预览本人摘要" })).toBeEnabled());
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("closes the preview while archiving and keeps it disabled after an unknown receipt", async () => {
  const user = userEvent.setup(); renderPage(); const pending = deferred<void>();
  server.use(http.patch("/api/checklists/:id", async () => { await pending.promise; return HttpResponse.error(); }));
  await waitFor(() => expect(screen.getByRole("button", { name: "归档现场清单" })).toBeEnabled());
  const archive = screen.getByRole("button", { name: "归档现场清单" });
  await openPreview(user); await screen.findByText("服务端冻结清单");
  fireEvent.click(archive);
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(screen.getByRole("button", { name: "预览本人摘要" })).toBeDisabled();
  await act(async () => pending.resolve());
  await screen.findByText(/尚未收到保存回执/);
  expect(screen.getByRole("button", { name: "预览本人摘要" })).toBeDisabled();
});
