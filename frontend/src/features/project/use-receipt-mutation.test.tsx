import type { PropsWithChildren } from "react";
import { QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { http, HttpResponse } from "msw";
import { ApiError } from "@/lib/api";
import { downloadAttachment, exportDecision, personalKeys, type Attachment, type MutationEnvelope, type PersonalCapabilities } from "@/lib/personal-api";
import { server } from "@/test/server";
import { makeTestQueryClient } from "@/test/utils";
import { installPersonalFixtures } from "./test-fixtures";
import { readPersonalRequest } from "./use-personal-draft";
import { usePersonalProject } from "./use-personal-project";
import { useReceiptMutation } from "./use-receipt-mutation";

type Changes = { title: string; nested: { notes: string[] }; file?: File };
type Saved = { saved: string };
type Send = (request: MutationEnvelope<Changes>, signal?: AbortSignal) => Promise<Saved>;
type Limits = { enabled?: boolean; owner?: string; draftKey?: string; initialRequest?: MutationEnvelope<Changes> };
const local: PersonalCapabilities = { library_id: "personal-test", primary_epoch: 1,
  personal_read: true, personal_write: true, personal_upload: true, field_checks: true };
const changes = (): Changes => ({ title: "原保存内容", nested: { notes: ["原观察"] } });

function deferred<T>() {
  let resolve!: (value: T) => void; let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function renderMutation(send: Send, initialProps: Limits = {}, initialRequest?: MutationEnvelope<Changes>) {
  const client = makeTestQueryClient(); const onSuccess = vi.fn(); const onRejected = vi.fn();
  const wrapper = ({ children }: PropsWithChildren) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  const view = renderHook((limits: Limits) => {
    const access = usePersonalProject();
    const mutation = useReceiptMutation(send, onSuccess, { ...limits,
      owner: limits.owner ?? access.read?.scope, draftKey: limits.draftKey ?? "shared-request",
      initialRequest: limits.initialRequest ?? initialRequest, onRejected });
    return { access, mutation };
  }, { wrapper, initialProps });
  return { ...view, client, onSuccess, onRejected };
}

beforeEach(installPersonalFixtures);
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("requires confirmed local permission and the current epoch even without recovery options", async () => {
  const capability = deferred<Response>(); const send = vi.fn(async () => ({ saved: "done" }));
  server.use(http.get("/api/capabilities", () => capability.promise));
  const client = makeTestQueryClient();
  const wrapper = ({ children }: PropsWithChildren) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  const view = renderHook(() => ({ access: usePersonalProject(), mutation: useReceiptMutation(send, vi.fn()) }), { wrapper });
  act(() => { view.result.current.mutation.submit(changes(), 1); });
  expect(send).not.toHaveBeenCalled();
  await act(async () => { capability.resolve(HttpResponse.json(local)); });
  await waitFor(() => expect(view.result.current.access.writable).toBe(true));
  act(() => { view.result.current.mutation.submit(changes(), 2); });
  expect(send).not.toHaveBeenCalled();
  await act(async () => { client.setQueryData(personalKeys.capabilities, { ...local, personal_write: false }); });
  await waitFor(() => expect(view.result.current.access.writable).toBe(false));
  act(() => { view.result.current.mutation.submit(changes(), 1); });
  expect(send).not.toHaveBeenCalled();
});

it("honors the form's additional gate before creating or sending a request", async () => {
  const send = vi.fn(async () => ({ saved: "done" })); const view = renderMutation(send, { enabled: false });
  await waitFor(() => expect(view.result.current.access.writable).toBe(true));
  act(() => { view.result.current.mutation.submit(changes(), 1); });
  expect(send).not.toHaveBeenCalled();
  expect(readPersonalRequest(view.client, view.result.current.access.scope, "shared-request")).toBeUndefined();
});

it("freezes nested editor values and retains the original File and UUID for an explicit retry", async () => {
  const send = vi.fn(async (_request: MutationEnvelope<Changes>, _signal?: AbortSignal): Promise<Saved> => {
    throw new ApiError(0, "回执未确认", "/api/projects/current");
  });
  const view = renderMutation(send); await waitFor(() => expect(view.result.current.access.writable).toBe(true));
  const file = new File(["original bytes"], "原照片.jpg", { type: "image/jpeg" });
  const editor = { ...changes(), file }; const scope = view.result.current.access.scope;
  act(() => { view.result.current.mutation.submit(editor, 1, 7); editor.title = "下一次编辑"; editor.nested.notes.push("新增观察"); });
  await waitFor(() => expect(view.result.current.mutation.isError).toBe(true));
  const original = send.mock.calls[0][0];
  expect(original).toMatchObject({ primary_epoch: 1, base_revision: 7, changes: changes() });
  expect(original.changes.file).toBe(file);
  expect(Object.isFrozen(original)).toBe(true); expect(Object.isFrozen(original.changes.nested.notes)).toBe(true);
  expect(readPersonalRequest(view.client, scope, "shared-request")).toBe(original);
  act(() => { view.result.current.mutation.retrySame(); });
  await waitFor(() => expect(send).toHaveBeenCalledTimes(2));
  expect(send.mock.calls[1][0]).toBe(original);
  expect(view.onSuccess).not.toHaveBeenCalled(); expect(view.onRejected).not.toHaveBeenCalled();
});

it.each(["success", "rejection"] as const)("aborts on a new epoch and rejects a late %s without moving the original request", async (outcome) => {
  const old = deferred<Saved>(); const send = vi.fn((_request: MutationEnvelope<Changes>, _signal?: AbortSignal) => old.promise);
  const view = renderMutation(send); await waitFor(() => expect(view.result.current.access.writable).toBe(true));
  const scope = view.result.current.access.scope;
  act(() => { view.result.current.mutation.submit(changes(), 1); });
  await waitFor(() => expect(send).toHaveBeenCalledTimes(1)); const original = send.mock.calls[0][0];
  await act(async () => { view.client.setQueryData(personalKeys.capabilities, { ...local, primary_epoch: 2 }); });
  await waitFor(() => expect(view.result.current.access.writable).toBe(true));
  expect(view.result.current.access.scope).not.toBe(scope); expect(send.mock.calls[0][1]?.aborted).toBe(true);
  expect(readPersonalRequest(view.client, scope, "shared-request")).toBe(original);
  expect(readPersonalRequest(view.client, view.result.current.access.scope, "shared-request")).toBeUndefined();
  await act(async () => { if (outcome === "success") old.resolve({ saved: "旧任期迟到" });
    else old.reject(new ApiError(403, "旧任期迟到拒绝", "/api/projects/current")); });
  await waitFor(() => expect(view.result.current.mutation.isPending).toBe(false));
  expect(send).toHaveBeenCalledTimes(1); expect(view.onSuccess).not.toHaveBeenCalled(); expect(view.onRejected).not.toHaveBeenCalled();
  expect(readPersonalRequest(view.client, scope, "shared-request")).toBe(original);
});

it.each(["epoch", "deny", "removed"] as const)("synchronously aborts on capability %s before React can render or an old response can settle", async (change) => {
  const old = deferred<Saved>(); const send = vi.fn((_request: MutationEnvelope<Changes>, _signal?: AbortSignal) => old.promise);
  const view = renderMutation(send); await waitFor(() => expect(view.result.current.access.writable).toBe(true));
  const scope = view.result.current.access.scope;
  act(() => { view.result.current.mutation.submit(changes(), 1); });
  await waitFor(() => expect(send).toHaveBeenCalledTimes(1)); const original = send.mock.calls[0][0];
  await act(async () => {
    if (change === "removed") view.client.removeQueries({ queryKey: personalKeys.capabilities, exact: true });
    else view.client.setQueryData(personalKeys.capabilities, change === "epoch" ? { ...local, primary_epoch: 2 } : { ...local, personal_write: false });
    // Keep this assertion inside the same stack as the cache update. Waiting
    // for a render first would hide the stale authorization delivery window.
    expect(send.mock.calls[0][1]?.aborted).toBe(true);
    old.resolve({ saved: "缓存变化同刻迟到" });
  });
  await waitFor(() => expect(view.result.current.mutation.isPending).toBe(false));
  expect(view.onSuccess).not.toHaveBeenCalled(); expect(view.onRejected).not.toHaveBeenCalled();
  expect(readPersonalRequest(view.client, scope, "shared-request")).toBe(original);
  expect(send).toHaveBeenCalledTimes(1);
  if (view.result.current.access.scope !== scope) expect(readPersonalRequest(view.client, view.result.current.access.scope, "shared-request")).toBeUndefined();
});

it("rejects a submit made in the same stack as capability revocation", async () => {
  const send = vi.fn(async () => ({ saved: "unexpected" })); const view = renderMutation(send);
  await waitFor(() => expect(view.result.current.access.writable).toBe(true)); const scope = view.result.current.access.scope;
  await act(async () => {
    view.client.setQueryData(personalKeys.capabilities, { ...local, personal_write: false });
    view.result.current.mutation.submit(changes(), 1);
    expect(readPersonalRequest(view.client, scope, "shared-request")).toBeUndefined();
  });
  expect(send).not.toHaveBeenCalled(); expect(view.onSuccess).not.toHaveBeenCalled();
});

it.each(["owner", "enabled"] as const)("cancels an in-flight request when its additional %s gate changes", async (gate) => {
  const old = deferred<Saved>(); const send = vi.fn((_request: MutationEnvelope<Changes>, _signal?: AbortSignal) => old.promise);
  const view = renderMutation(send, { owner: "original-editor", enabled: true });
  await waitFor(() => expect(view.result.current.access.writable).toBe(true)); const scope = view.result.current.access.scope;
  act(() => { view.result.current.mutation.submit(changes(), 1); });
  await waitFor(() => expect(send).toHaveBeenCalledTimes(1)); const original = send.mock.calls[0][0];
  view.rerender({ owner: gate === "owner" ? "other-editor" : "original-editor", enabled: gate !== "enabled" });
  await waitFor(() => expect(send.mock.calls[0][1]?.aborted).toBe(true));
  act(() => { view.result.current.mutation.retrySame(); view.result.current.mutation.checkReceipt(); });
  expect(send).toHaveBeenCalledTimes(1);
  await act(async () => { old.resolve({ saved: "迟到结果" }); });
  await waitFor(() => expect(view.result.current.mutation.isPending).toBe(false));
  expect(view.onSuccess).not.toHaveBeenCalled(); expect(view.onRejected).not.toHaveBeenCalled();
  expect(readPersonalRequest(view.client, scope, "shared-request")).toBe(original);
});

it("keeps an aborted attempt's settlement separate from a newer retry of the same envelope", async () => {
  const old = deferred<Saved>(); const retry = deferred<Saved>();
  let attempts = 0;
  const send = vi.fn((_request: MutationEnvelope<Changes>, _signal?: AbortSignal) =>
    ++attempts === 1 ? old.promise : retry.promise);
  const view = renderMutation(send, { enabled: true }); await waitFor(() => expect(view.result.current.access.writable).toBe(true));
  const scope = view.result.current.access.scope;
  act(() => { view.result.current.mutation.submit(changes(), 1); });
  await waitFor(() => expect(send).toHaveBeenCalledTimes(1)); const original = send.mock.calls[0][0];
  view.rerender({ enabled: false }); await waitFor(() => expect(send.mock.calls[0][1]?.aborted).toBe(true));
  view.rerender({ enabled: true }); act(() => { view.result.current.mutation.retrySame(); });
  await waitFor(() => expect(send).toHaveBeenCalledTimes(2));
  expect(send.mock.calls[1][0]).toBe(original);
  await act(async () => { old.resolve({ saved: "旧尝试" }); });
  expect(view.onSuccess).not.toHaveBeenCalled(); expect(send.mock.calls[1][1]?.aborted).toBe(false);
  expect(readPersonalRequest(view.client, scope, "shared-request")).toBe(original);
  await act(async () => { retry.resolve({ saved: "新尝试" }); });
  await waitFor(() => expect(view.onSuccess).toHaveBeenCalledWith({ saved: "新尝试" }));
  expect(view.onSuccess).toHaveBeenCalledTimes(1); expect(readPersonalRequest(view.client, scope, "shared-request")).toBeUndefined();
});

it("queries only the retained UUID, keeps an absent receipt, and clears it only after confirmation", async () => {
  const send = vi.fn(async (_request: MutationEnvelope<Changes>, _signal?: AbortSignal): Promise<Saved> => {
    throw new ApiError(0, "回执未确认", "/api/projects/current");
  });
  const queried: string[] = []; let confirmed = false;
  server.use(http.get("/api/operations/:id", ({ params }) => {
    queried.push(String(params.id));
    return confirmed ? HttpResponse.json({ operation_id: params.id, response: { saved: "已确认" } })
      : HttpResponse.json({ detail: "未查到回执" }, { status: 404 });
  }));
  const view = renderMutation(send); await waitFor(() => expect(view.result.current.access.writable).toBe(true));
  const scope = view.result.current.access.scope;
  act(() => { view.result.current.mutation.submit(changes(), 1); });
  await waitFor(() => expect(view.result.current.mutation.isError).toBe(true)); const original = send.mock.calls[0][0];
  act(() => { view.result.current.mutation.checkReceipt(); });
  await waitFor(() => expect(view.result.current.mutation.receiptMessage).toContain("尚未查到回执"));
  expect(readPersonalRequest(view.client, scope, "shared-request")).toBe(original); expect(view.onSuccess).not.toHaveBeenCalled();
  confirmed = true; act(() => { view.result.current.mutation.checkReceipt(); });
  await waitFor(() => expect(view.onSuccess).toHaveBeenCalledWith({ saved: "已确认" }));
  expect(queried).toEqual([original.operation_id, original.operation_id]); expect(send).toHaveBeenCalledTimes(1);
  expect(view.result.current.mutation.unknown).toBe(false); expect(readPersonalRequest(view.client, scope, "shared-request")).toBeUndefined();
});

it("aborts on unmount while preserving the original request and ignoring its late result", async () => {
  const old = deferred<Saved>(); const send = vi.fn((_request: MutationEnvelope<Changes>, _signal?: AbortSignal) => old.promise);
  const view = renderMutation(send); await waitFor(() => expect(view.result.current.access.writable).toBe(true));
  const scope = view.result.current.access.scope;
  act(() => { view.result.current.mutation.submit(changes(), 1); });
  await waitFor(() => expect(send).toHaveBeenCalledTimes(1)); const original = send.mock.calls[0][0];
  view.unmount(); expect(send.mock.calls[0][1]?.aborted).toBe(true);
  await act(async () => { old.resolve({ saved: "卸载后迟到" }); });
  expect(readPersonalRequest(view.client, scope, "shared-request")).toBe(original);
  expect(view.onSuccess).not.toHaveBeenCalled(); expect(view.onRejected).not.toHaveBeenCalled();
});

it("does not adopt an initial request from another scope", async () => {
  const initial: MutationEnvelope<Changes> = { operation_id: "original-operation", primary_epoch: 1, changes: changes() };
  const send = vi.fn(async () => ({ saved: "unexpected" }));
  const view = renderMutation(send, { owner: "another-primary-scope" }, initial);
  await waitFor(() => expect(view.result.current.access.writable).toBe(true));
  await waitFor(() => expect(view.result.current.mutation.receiptMessage).toContain("不能重发"));
  act(() => { view.result.current.mutation.retrySame(); view.result.current.mutation.checkReceipt(); view.result.current.mutation.submit(changes(), 1); });
  expect(send).not.toHaveBeenCalled();
  expect(readPersonalRequest(view.client, view.result.current.access.scope, "shared-request")).toBeUndefined();
});

it("does not bind an older-epoch initial request to a confirmed current scope", async () => {
  const send = vi.fn(async () => ({ saved: "unexpected" }));
  const first = renderMutation(send); await waitFor(() => expect(first.result.current.access.writable).toBe(true));
  const scope = first.result.current.access.scope; first.unmount();
  const initial: MutationEnvelope<Changes> = { operation_id: "old-epoch-operation", primary_epoch: 0, changes: changes() };
  const view = renderMutation(send, { owner: scope }, initial);
  await waitFor(() => expect(view.result.current.access.writable).toBe(true));
  await waitFor(() => expect(view.result.current.mutation.receiptMessage).toContain("不能重发"));
  act(() => { view.result.current.mutation.retrySame(); view.result.current.mutation.checkReceipt(); });
  expect(send).not.toHaveBeenCalled(); expect(readPersonalRequest(view.client, scope, "shared-request")).toBeUndefined();
});

it.each(["export", "attachment"] as const)("rejects an aborted late %s before its mirror header can invalidate a newer owner", async (kind) => {
  const response = deferred<Response>(); const dispatch = vi.spyOn(window, "dispatchEvent");
  vi.stubGlobal("fetch", vi.fn(() => response.promise));
  const controller = new AbortController();
  const read = { mode: "mirror" as const, scope: "original-mirror", version: "a".repeat(64) };
  const attachment: Attachment = { attachment_id: "original-file", source_id: "source", project_id: "project",
    sha256: "b".repeat(64), byte_size: 4, media_type: "image/jpeg", original_name: "原照片.jpg",
    state: "ready", created_at: "昨天", target_fingerprint: "attachment", readable: true };
  const pending = kind === "export" ? exportDecision("original-card", read, controller.signal)
    : downloadAttachment(attachment, read, controller.signal);
  const outcome = expect(pending).rejects.toMatchObject({ name: "AbortError" });
  controller.abort();
  response.resolve(new HttpResponse("late body", { headers: { "X-Reno-Personal-Read-Version": "c".repeat(64) } }));
  await outcome; expect(dispatch).not.toHaveBeenCalled();
});
