import { beforeEach, expect, it, vi } from "vitest";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/server";
import { renderWithApp } from "@/test/utils";
import { installPersonalFixtures, projectFixture, sourceFixture } from "@/features/project/test-fixtures";
import type { MutationEnvelope, SourceChanges } from "@/lib/personal-api";
import { SourceEditorButton } from "./source-editor-button";

beforeEach(installPersonalFixtures);

it("restores the latest shared source draft when alternating between two entry points", async () => {
  const user = userEvent.setup();
  renderWithApp(<><SourceEditorButton label="入口甲" /><SourceEditorButton label="入口乙" /></>);
  await waitFor(() => expect(screen.getByRole("button", { name: "入口甲" })).toBeEnabled());
  await user.click(screen.getByRole("button", { name: "入口甲" }));
  await user.type(screen.getByLabelText("资料标题"), "原草稿");
  await user.click(screen.getByRole("button", { name: "稍后继续" }));

  await user.click(screen.getByRole("button", { name: "入口乙" }));
  expect(screen.getByLabelText("资料标题")).toHaveValue("原草稿");
  await user.clear(screen.getByLabelText("资料标题"));
  await user.type(screen.getByLabelText("资料标题"), "另一入口继续填写的草稿");
  await user.click(screen.getByRole("button", { name: "稍后继续" }));

  await user.click(screen.getByRole("button", { name: "入口甲" }));
  expect(screen.getByLabelText("资料标题")).toHaveValue("另一入口继续填写的草稿");
});

it("locks a previously opened entry to the original pending request and ignores the closed editor's late success", async () => {
  const user = userEvent.setup(); const savedA = vi.fn(); const savedB = vi.fn();
  const writes: MutationEnvelope<SourceChanges>[] = []; const receipts: string[] = [];
  let release!: () => void; let responseReady!: () => void;
  const responseGate = new Promise<void>((resolve) => { release = resolve; });
  const handled = new Promise<void>((resolve) => { responseReady = resolve; });
  const result = () => ({ source: { ...sourceFixture, title: writes[0].changes.title }, operation_id: writes[0].operation_id });
  server.use(
    http.post(`/api/projects/${projectFixture.project_id}/sources`, async ({ request }) => {
      writes.push(await request.json() as MutationEnvelope<SourceChanges>);
      await responseGate;
      const response = HttpResponse.json(result()); responseReady(); return response;
    }),
    http.get("/api/operations/:id", ({ params }) => {
      receipts.push(String(params.id));
      return HttpResponse.json({ operation_id: params.id, response: result() });
    }),
  );
  renderWithApp(<><SourceEditorButton label="入口甲" onSaved={savedA} /><SourceEditorButton label="入口乙" onSaved={savedB} /></>);
  await waitFor(() => expect(screen.getByRole("button", { name: "入口甲" })).toBeEnabled());
  // Both entry points have been opened before the first request starts.
  await user.click(screen.getByRole("button", { name: "入口甲" }));
  await user.type(screen.getByLabelText("资料标题"), "待确认的来源");
  await user.click(screen.getByRole("button", { name: "稍后继续" }));
  await user.click(screen.getByRole("button", { name: "入口乙" }));
  await user.click(screen.getByRole("button", { name: "稍后继续" }));

  try {
    await user.click(screen.getByRole("button", { name: "入口甲" }));
    await user.click(screen.getByRole("button", { name: "保存来源记录" }));
    await waitFor(() => expect(writes).toHaveLength(1));
    await user.click(screen.getByRole("button", { name: "稍后继续" }));
    await user.click(screen.getByRole("button", { name: "入口乙" }));
    expect(screen.getByLabelText("资料标题")).toHaveValue("待确认的来源");
    expect(screen.getByLabelText("资料标题")).toBeDisabled();
    expect(screen.getByRole("button", { name: "保存来源记录" })).toBeDisabled();

    await act(async () => { release(); await handled; });
    expect(savedA).not.toHaveBeenCalled(); expect(savedB).not.toHaveBeenCalled();
    expect(screen.getByLabelText("资料标题")).toHaveValue("待确认的来源");
    expect(screen.getByLabelText("资料标题")).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "查询提交结果" }));
    await waitFor(() => expect(savedB).toHaveBeenCalledTimes(1));
    expect(savedA).not.toHaveBeenCalled(); expect(writes).toHaveLength(1);
    expect(receipts).toEqual([writes[0].operation_id]);
    await user.click(screen.getByRole("button", { name: "入口甲" }));
    expect(screen.getByLabelText("资料标题")).toHaveValue("");
    expect(screen.getByLabelText("资料标题")).toBeEnabled();
  } finally { release(); }
});
