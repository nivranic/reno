import { beforeEach, describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/server";
import { renderWithApp } from "@/test/utils";
import { installPersonalFixtures, projectFixture } from "@/features/project/test-fixtures";
import type { CheckItem, Checklist } from "@/lib/personal-api";
import { CheckItemCard } from "./check-item-card";

const item: CheckItem = { item_id: "item-1", instance_id: "list-1", title: "个人清洁要求", conditions: {}, method_text: "目视", basis_kind: "personal_requirement", criteria_state: "known", criteria: { text: "无垃圾", conditions_confirmed: true, requirement_confirmed: true, required_observations: [{ key: "observation", label: "现场观察", kind: "text" }] }, refs: [], revision: 1, checks: [], current_result: "unchecked", needs_recheck: false, criteria_snapshot_hash: "hash-v1", pass_eligibility: { allowed: true, reasons: [], label: "符合我的这项要求" } };
const checklist: Checklist = { instance_id: "list-1", project_id: projectFixture.project_id, title: "现场样例", space: "卫生间", stage: "泥瓦", revision: 1, state: "active", lifecycle_state: "active", items: [item], progress: { total: 1, complete: 0 }, created_at: "2026-09-30T00:00:00Z", updated_at: "2026-09-30T00:00:00Z" };
const renderItem = (value = item) => renderWithApp(<CheckItemCard item={value} checklist={checklist} libraryId="test-field-lib" epoch={1} writable uploadable />);
beforeEach(() => { installPersonalFixtures(); sessionStorage.clear(); });

describe("field form acceptance boundaries", () => {
  it("requires an actual observation for pass and a reason for not applicable", async () => {
    const user = userEvent.setup(); renderItem(); await user.click(await screen.findByText("填写核对、整改或复检记录"));
    await user.selectOptions(screen.getByLabelText("本项结果"), "pass");
    expect(screen.getByRole("option", { name: "符合我的这项要求" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存本次记录" })).toBeDisabled();
    await user.type(screen.getByLabelText("现场观察"), "表面无垃圾"); expect(screen.getByRole("button", { name: "保存本次记录" })).toBeEnabled();
    await user.selectOptions(screen.getByLabelText("本项结果"), "not_applicable"); expect(screen.getByRole("button", { name: "保存本次记录" })).toBeDisabled();
    await user.type(screen.getByLabelText("记录说明"), "本次不涉及该部位"); expect(screen.getByRole("button", { name: "保存本次记录" })).toBeEnabled();
  });
  it("does not offer pass for an unverified criterion", async () => {
    const user = userEvent.setup(); renderItem({ ...item, basis_kind: "observation_only", criteria_state: "needs_verification", pass_eligibility: { allowed: false, reasons: ["缺少判据"] } });
    await user.click(await screen.findByText("填写核对、整改或复检记录"));
    expect(screen.getByRole("option", { name: "符合本项" })).toBeDisabled(); expect(screen.getByText("暂不能记录符合：缺少判据")).toBeInTheDocument();
  });
  it("keeps text after a known rejection without restoring a spurious unknown receipt lock", async () => {
    const user = userEvent.setup(); server.use(http.post("/api/checklist-items/:id/checks", () => HttpResponse.json({ detail: "判据已变化", code: "EVIDENCE_CHANGED" }, { status: 409 })));
    const first = renderItem(); await user.click(await screen.findByText("填写核对、整改或复检记录")); await user.type(screen.getByLabelText("记录说明"), "不能丢失的观察"); await user.click(screen.getByRole("button", { name: "保存本次记录" }));
    await screen.findByText("判据已变化");
    await waitFor(() => expect(JSON.parse(sessionStorage.getItem(Object.keys(sessionStorage).find((key) => key.includes(":field:item-1:owner:"))!)!).draft.pendingRequest).toBeUndefined());
    first.unmount(); renderItem(); await user.click(await screen.findByText("填写核对、整改或复检记录"));
    expect(screen.getByLabelText("记录说明")).toHaveValue("不能丢失的观察"); expect(screen.getByLabelText("记录说明")).toBeEnabled(); expect(screen.queryByText(/尚未收到保存回执/)).not.toBeInTheDocument();
  });
  it("does not reinterpret an old measurement using a renamed current criterion", async () => {
    const user = userEvent.setup(); renderItem({ ...item, revision: 2, criteria: { ...item.criteria, required_observations: [{ key: "observation", label: "新版完全不同的观察", kind: "text" }] }, checks: [{ check_id: "old-check", item_revision: 1, commit_seq: 1, record_kind: "inspection", result: "unable_to_verify", measurements: [{ key: "observation", value: "旧版原始观察" }], note: "", attachment_refs: [], checked_at: "2026-09-30T00:00:00Z", created_at: "2026-09-30T00:00:00Z", criteria_snapshot_hash: "old-hash", current_version: false }] });
    await screen.findByRole("button", { name: "修订本项" });
    await user.click(screen.getByText(/检查、整改与复检历史/)); expect(screen.getByText(/历史观察 observation：旧版原始观察/)).toBeVisible();
  });
});
