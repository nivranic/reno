import { beforeEach, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { makeTestQueryClient } from "@/test/utils";
import { usePersonalDraftArchive } from "@/features/project/use-personal-draft";
import { useFieldDraft, type FieldDraft } from "./use-field-draft";

const initial: FieldDraft = { baseRevision: 1, criteriaHash: "criteria", kind: "inspection", result: "unable_to_verify", note: "", observations: {}, checkedAt: "", attachments: [], sourceId: "" };
const scope = (primary: string, epoch = 1) => JSON.stringify(["local", "library", epoch, primary, "instance", null, null, true, true, true, true]);
const request = { operation_id: "11111111-1111-4111-8111-111111111111", primary_epoch: 1, base_revision: 1, changes: { record_kind: "inspection" as const, result: "unable_to_verify" as const, measurements: [], note: "原观察", attachment_refs: [], criteria_snapshot_hash: "criteria" } };
function setup(owner = scope("first"), epoch = 1) {
  const client = makeTestQueryClient();
  return renderHook(({ owner, epoch }) => ({ record: useFieldDraft("library", "item", initial, epoch, owner), archive: usePersonalDraftArchive() }), {
    initialProps: { owner, epoch }, wrapper: ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>,
  });
}
beforeEach(() => sessionStorage.clear());

it("keeps all entered fields and the original pending request under its complete owner", async () => {
  const view = setup();
  expect(view.result.current.archive.drafts).toHaveLength(0);
  act(() => view.result.current.record.patch({ note: "原观察", observations: { height: "120" }, attachments: ["attachment"], checkedAt: "2026-10-07T12:00", sourceId: "source", filePending: "现场.jpg", pendingRequest: request }));
  const originalKey = Object.keys(sessionStorage).find((key) => key.includes(":owner:"))!;
  const originalBytes = sessionStorage.getItem(originalKey);
  view.rerender({ owner: scope("second"), epoch: 1 });
  expect(view.result.current.record.draft).toMatchObject({ note: "", observations: {}, attachments: [], sourceId: "" });
  expect(view.result.current.record.draft.pendingRequest).toBeUndefined();
  expect(sessionStorage.getItem(originalKey)).toBe(originalBytes);
  expect(view.result.current.archive.drafts[0].text).toContain("原观察");
  view.rerender({ owner: scope("first", 2), epoch: 2 });
  expect(view.result.current.record.draft.pendingRequest).toBeUndefined();
  view.rerender({ owner: scope("first"), epoch: 1 });
  await waitFor(() => expect(view.result.current.record.draft).toMatchObject({ note: "原观察", observations: { height: "120" }, attachments: ["attachment"], checkedAt: "2026-10-07T12:00", sourceId: "source", filePending: "现场.jpg", pendingRequest: request }));
});

it("restores a same-owner session with all fields and clears only after confirmed success", async () => {
  const first = setup();
  act(() => first.result.current.record.patch({ note: "刷新后仍核对", observations: { text: "观察原文" }, sourceId: "source", pendingRequest: request }));
  first.unmount();
  const second = setup();
  await waitFor(() => expect(second.result.current.record.draft).toMatchObject({ note: "刷新后仍核对", observations: { text: "观察原文" }, sourceId: "source", pendingRequest: request }));
  expect(second.result.current.archive.drafts[0].text).toContain("刷新后仍核对");
  act(() => second.result.current.record.clear());
  expect(second.result.current.archive.drafts).toHaveLength(0);
  expect(second.result.current.record.draft.pendingRequest).toBeUndefined();
  second.unmount();
  const third = setup();
  expect(third.result.current.record.draft.note).toBe("");
  expect(third.result.current.archive.drafts).toHaveLength(0);
});

it("preserves an unscoped legacy record without admitting it on epoch alone", () => {
  const key = `reno-reading:v1:${encodeURIComponent(window.location.origin)}:library:field:item`;
  const bytes = JSON.stringify({ updated_at: Date.now(), draft: { ...initial, note: "无法证明原主实例", pendingRequest: request } });
  sessionStorage.setItem(key, bytes);
  const view = setup();
  expect(view.result.current.record.draft.note).toBe("");
  expect(view.result.current.record.draft.pendingRequest).toBeUndefined();
  expect(view.result.current.record.notice).toContain("缺少完整归属证明");
  expect(sessionStorage.getItem(key)).toBe(bytes);
  expect(view.result.current.archive.drafts).toHaveLength(0);
});

it("keeps newer memory after a session write fails and never promotes the stale session over it", async () => {
  const client = makeTestQueryClient();
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  const hook = () => ({ record: useFieldDraft("library", "item", initial, 1, scope("first")), archive: usePersonalDraftArchive() });
  const first = renderHook(hook, { wrapper });
  act(() => first.result.current.record.patch({ note: "已暂存的旧观察" }));
  const key = Object.keys(sessionStorage).find((key) => key.includes(":owner:"))!;
  const oldBytes = sessionStorage.getItem(key);
  const failure = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("session unavailable"); });
  try {
    act(() => first.result.current.record.patch({ note: "只在内存的更新观察" }));
    expect(first.result.current.record.notice).toContain("暂存不可用");
    expect(sessionStorage.getItem(key)).toBe(oldBytes);
  } finally { failure.mockRestore(); }
  first.unmount();
  const second = renderHook(hook, { wrapper });
  await waitFor(() => expect(second.result.current.record.draft.note).toBe("只在内存的更新观察"));
  expect(second.result.current.archive.drafts[0].text).toContain("只在内存的更新观察");
  expect(sessionStorage.getItem(key)).toBe(oldBytes);
});
