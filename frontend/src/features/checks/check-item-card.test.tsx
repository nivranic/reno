import { beforeEach, expect, it } from "vitest";
import { useQuery } from "@tanstack/react-query";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/server";
import { renderWithApp } from "@/test/utils";
import { installPersonalFixtures, projectFixture } from "@/features/project/test-fixtures";
import { checklistQuery, personalKeys, type Checklist, type CheckItem, type CheckItemInput, type MutationEnvelope } from "@/lib/personal-api";
import { CheckItemCard } from "./check-item-card";

const original: CheckItem = {
  item_id: "cas-item", instance_id: "cas-list", title: "要修订的个人要求", conditions: { 空间: "卫生间" },
  method_text: "打开表单时的方法", basis_kind: "personal_requirement", criteria_state: "needs_verification",
  criteria: { text: "仍需核实的要求", conditions_confirmed: true, requirement_confirmed: true, unresolved_standard_conflict: true,
    required_observations: [{ key: "observation", label: "现场观察", kind: "text" }] },
  refs: [{ ref_id: "original-ref", target_kind: "atom", target_id: "original-atom", target_fingerprint: "selected-fingerprint", binding_status: "active", snapshot: { claim: "选定时的原文" } }],
  revision: 1, checks: [], current_result: "unchecked", needs_recheck: false, criteria_snapshot_hash: "criteria-v1",
  pass_eligibility: { allowed: false, reasons: ["分歧尚未核实"] },
};
const list = (item: CheckItem): Checklist => ({
  instance_id: item.instance_id, project_id: projectFixture.project_id, title: "并发修订清单", space: "卫生间", stage: "泥瓦",
  revision: 1, state: "active", lifecycle_state: "active", items: [item], progress: { total: 1, complete: 0 },
  created_at: "2026-09-30T00:00:00Z", updated_at: "2026-09-30T00:00:00Z",
});
function LiveItem() {
  const query = useQuery(checklistQuery(original.instance_id));
  if (!query.data) return null;
  return <CheckItemCard item={query.data.checklist.items[0]} checklist={query.data.checklist} libraryId="cas-test-library" epoch={1} writable uploadable />;
}
beforeEach(() => { installPersonalFixtures(); sessionStorage.clear(); });

it("keeps the edit revision across a background refresh and only rebases after explicit review", async () => {
  let current = structuredClone(original);
  const writes: MutationEnvelope<CheckItemInput>[] = [];
  server.use(
    http.get(`/api/checklists/${original.instance_id}`, () => HttpResponse.json({ checklist: list(current) })),
    http.patch(`/api/checklist-items/${original.item_id}`, async ({ request }) => {
      const body = await request.json() as MutationEnvelope<CheckItemInput>;
      writes.push(body);
      if (body.base_revision !== current.revision) return HttpResponse.json({ detail: "条目已被修改", code: "REVISION_CONFLICT", current_revision: current.revision }, { status: 409 });
      current = { ...current, ...body.changes, refs: current.refs, revision: current.revision + 1 };
      return HttpResponse.json({ item: current, checklist: list(current) });
    }),
  );
  const user = userEvent.setup();
  const { client } = renderWithApp(<LiveItem />);
  await user.click(await screen.findByRole("button", { name: "修订本项" }));
  const editor = within(screen.getByRole("dialog"));
  await user.clear(editor.getByLabelText("检查方法"));
  await user.type(editor.getByLabelText("检查方法"), "本次尚未保存的方法草稿");

  current = { ...current, method_text: "另一个窗口保存的方法", revision: 2 };
  await act(async () => { await client.invalidateQueries({ queryKey: personalKeys.checklist(original.instance_id) }); });
  expect(editor.getByLabelText("检查方法")).toHaveValue("本次尚未保存的方法草稿");
  expect(editor.getByText(/本次草稿基于条目版本 1/)).toBeVisible();
  await user.click(editor.getByRole("button", { name: "确认修订并重新核对" }));
  await editor.findByText(/本次草稿仍保留/);
  expect(writes[0].base_revision).toBe(1);
  expect(current.method_text).toBe("另一个窗口保存的方法");

  await user.click(editor.getByRole("button", { name: "读取服务器当前判据" }));
  await editor.findByRole("heading", { name: "待核对的服务器判据（版本 2）" });
  expect(within(editor.getByLabelText("待核对的服务器判据")).getByLabelText("检查方法")).toHaveValue("另一个窗口保存的方法");
  expect(editor.getByText(/本次草稿基于条目版本 1/)).toBeVisible();
  expect(writes).toHaveLength(1);
  await user.click(editor.getByRole("button", { name: "以已核对版本为基础保留本次草稿" }));
  expect(editor.getByText(/本次草稿基于条目版本 2/)).toBeVisible();
  expect(editor.getByLabelText("检查方法")).toHaveValue("本次尚未保存的方法草稿");
  await user.click(editor.getByRole("button", { name: "确认修订并重新核对" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(writes.map((write) => write.base_revision)).toEqual([1, 2]);
  expect(current.method_text).toBe("本次尚未保存的方法草稿");
  expect(writes[1].changes.refs).toEqual([{ target_kind: "atom", target_id: "original-atom", target_fingerprint: "selected-fingerprint" }]);
  expect(writes[1].changes.criteria.unresolved_standard_conflict).toBe(true);
});

it("keeps the edit dialog mounted while a write receipt is unknown", async () => {
  server.use(
    http.get(`/api/checklists/${original.instance_id}`, () => HttpResponse.json({ checklist: list(original) })),
    http.patch(`/api/checklist-items/${original.item_id}`, () => HttpResponse.error()),
  );
  const user = userEvent.setup();
  renderWithApp(<LiveItem />);
  await user.click(await screen.findByRole("button", { name: "修订本项" }));
  await user.click(screen.getByRole("button", { name: "确认修订并重新核对" }));
  await screen.findByText(/尚未收到保存回执/);
  await user.keyboard("{Escape}");
  expect(screen.getByRole("dialog")).toBeInTheDocument();
  await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "关闭" }));
  expect(screen.getByRole("dialog")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "查询提交结果" })).toBeEnabled();
});
