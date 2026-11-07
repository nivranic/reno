import { beforeEach, expect, it } from "vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import type { QueryClient } from "@tanstack/react-query";
import { server } from "@/test/server";
import { renderWithApp } from "@/test/utils";
import { personalKeys } from "@/lib/personal-api";
import ProjectPage from "./project-page";
import { installPersonalFixtures, projectFixture, sourceFixture } from "./test-fixtures";
import { usePersonalDraftArchive } from "./use-personal-draft";
import { DecisionDialog } from "@/features/decisions/decision-dialog";
import { SourceRecordDialog } from "@/features/sources/source-record-dialog";
import ChecksPage from "@/features/checks/checks-page";

function DraftProbe() {
  const archive = usePersonalDraftArchive();
  return <pre data-testid="drafts">{archive.drafts.map((draft) => `${draft.label}\n${draft.text}`).join("\n\n")}</pre>;
}
function installOwnerSwitch() {
  let primary = "first";
  server.use(http.get("/api/capabilities", () => HttpResponse.json({ library_id: "personal-test", primary_epoch: 1, personal_primary_id: primary, instance_id: "same-instance", personal_read: true, personal_write: true, personal_upload: true, field_checks: true })));
  return async (client: QueryClient) => {
    primary = "second";
    await act(async () => { await client.invalidateQueries({ queryKey: personalKeys.capabilities }); });
  };
}
beforeEach(() => { installPersonalFixtures(); sessionStorage.clear(); });

it("retains a complete project draft for explicit viewing without applying it to a different primary", async () => {
  const changeOwner = installOwnerSwitch(); const user = userEvent.setup();
  const { client } = renderWithApp(<><ProjectPage /><DraftProbe /></>);
  const name = await screen.findByLabelText("项目名称");
  expect(screen.getByTestId("drafts")).toBeEmptyDOMElement();
  await user.clear(name); await user.type(name, "还没有提交的项目名称");
  await user.click(screen.getByText(/可选资料（无需填写/));
  await user.type(screen.getByLabelText("城市"), "绍兴");
  await user.type(screen.getByLabelText("预算范围"), "尚未确认的预算草稿");
  await user.clear(screen.getByLabelText("条件材料")); await user.type(screen.getByLabelText("条件材料"), "待核对的材料");
  expect(screen.getByTestId("drafts")).toHaveTextContent("城市：绍兴");
  await changeOwner(client);
  await waitFor(() => expect(screen.getByLabelText("项目名称")).toHaveValue(projectFixture.name));
  expect(screen.getByLabelText("城市")).toHaveValue("");
  expect(screen.getByTestId("drafts")).toHaveTextContent("还没有提交的项目名称");
  expect(screen.getByTestId("drafts")).toHaveTextContent("尚未确认的预算草稿");
  expect(screen.getByTestId("drafts")).toHaveTextContent("待核对的材料");
});

it("keeps all decision fields and selected option text under the original seed and owner", async () => {
  const changeOwner = installOwnerSwitch(); const user = userEvent.setup();
  const seed = { title: "原问题", content_snapshot: { kind: "note" as const, text: "原保存片段" }, alternatives: [{ option_id: "choice", label: "方案甲", description: "保留完整方案说明" }], evidence: [] };
  const { client } = renderWithApp(<><DecisionDialog seed={seed} open onOpenChange={() => {}} /><DraftProbe /></>);
  await screen.findByLabelText("决定理由");
  expect(screen.getByTestId("drafts")).toBeEmptyDOMElement();
  await user.type(screen.getByLabelText("决定理由"), "尚未提交的完整理由");
  await user.type(screen.getByLabelText("我的决定"), "先核实基层");
  await user.type(screen.getByLabelText("待确认问题"), "时间还未确认\n型号还未确认");
  await user.selectOptions(screen.getByLabelText("选定方案"), "choice");
  await user.click(screen.getByText("查看或选取保存的内容片段"));
  await user.type(screen.getByLabelText("保存的内容片段"), "，未提交补充片段");
  await changeOwner(client);
  await waitFor(() => expect(screen.getByLabelText("决定理由")).toHaveValue(""));
  expect(screen.getByLabelText("我的决定")).toHaveValue("");
  expect(screen.getByLabelText("选定方案")).toHaveValue("");
  expect(screen.getByTestId("drafts")).toHaveTextContent("尚未提交的完整理由");
  expect(screen.getByTestId("drafts")).toHaveTextContent("时间还未确认");
  expect(screen.getByTestId("drafts")).toHaveTextContent("方案（已选）：方案甲");
  expect(screen.getByTestId("drafts")).toHaveTextContent("未提交补充片段");
});

it("retains source text, scope and version fields while a replacement primary starts from its source", async () => {
  const changeOwner = installOwnerSwitch(); const user = userEvent.setup();
  const { client } = renderWithApp(<><SourceRecordDialog source={sourceFixture} open onOpenChange={() => {}} /><DraftProbe /></>);
  await screen.findByLabelText("原文摘录");
  await user.clear(screen.getByLabelText("原文摘录")); await user.type(screen.getByLabelText("原文摘录"), "整段尚未提交的原文");
  await user.type(screen.getByLabelText("发布方"), "待核对发布方");
  await user.type(screen.getByLabelText("版本"), "本次填写版本");
  await user.type(screen.getByLabelText("原文定位"), "第十二页第三条");
  await user.click(screen.getByText("适用范围"));
  await user.type(screen.getByLabelText("条件材料"), "本次填写材料范围");
  await changeOwner(client);
  await waitFor(() => expect(screen.getByLabelText("原文摘录")).toHaveValue(sourceFixture.excerpt));
  expect(screen.getByLabelText("版本")).toHaveValue("");
  expect(screen.getByTestId("drafts")).toHaveTextContent("整段尚未提交的原文");
  expect(screen.getByTestId("drafts")).toHaveTextContent("待核对发布方");
  expect(screen.getByTestId("drafts")).toHaveTextContent("第十二页第三条");
  expect(screen.getByTestId("drafts")).toHaveTextContent("本次填写材料范围");
});

it("preserves every new checklist item instead of transferring the old checklist into a replacement scope", async () => {
  const changeOwner = installOwnerSwitch(); const user = userEvent.setup();
  server.use(http.get(`/api/projects/${projectFixture.project_id}/checklists`, () => HttpResponse.json({ checklists: [], total: 0, offset: 0, limit: 30 })));
  const { client } = renderWithApp(<><ChecksPage /><DraftProbe /></>);
  await waitFor(() => expect(screen.getByRole("button", { name: "新建现场清单" })).toBeEnabled());
  await user.click(screen.getByRole("button", { name: "新建现场清单" }));
  const dialog = within(screen.getByRole("dialog"));
  await user.type(dialog.getByLabelText("清单标题"), "尚未建立的清单");
  await user.type(dialog.getByLabelText("清单空间"), "卫生间");
  await user.type(dialog.getByLabelText("核对项标题"), "第一项原草稿");
  await user.type(dialog.getByLabelText("检查方法"), "完整检查方法");
  await user.type(dialog.getByLabelText("本项判据"), "仍待核实的判据");
  await user.click(dialog.getByRole("button", { name: "添加核对项" }));
  await user.type(dialog.getAllByLabelText("核对项标题")[1], "第二项原草稿");
  await changeOwner(client);
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  await waitFor(() => expect(screen.getByRole("button", { name: "新建现场清单" })).toBeEnabled());
  await user.click(screen.getByRole("button", { name: "新建现场清单" }));
  expect(screen.getByLabelText("清单标题")).toHaveValue("");
  expect(screen.getAllByLabelText("核对项标题")).toHaveLength(1);
  expect(screen.getByTestId("drafts")).toHaveTextContent("尚未建立的清单");
  expect(screen.getByTestId("drafts")).toHaveTextContent("第一项原草稿");
  expect(screen.getByTestId("drafts")).toHaveTextContent("第二项原草稿");
  expect(screen.getByTestId("drafts")).toHaveTextContent("完整检查方法");
  expect(screen.getByTestId("drafts")).toHaveTextContent("仍待核实的判据");
});
