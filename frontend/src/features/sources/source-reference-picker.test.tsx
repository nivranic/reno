import { useState } from "react";
import { beforeEach, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/server";
import { renderWithApp } from "@/test/utils";
import { installPersonalFixtures, projectFixture, sourceFixture } from "@/features/project/test-fixtures";
import type { EvidenceInput, SourceChanges } from "@/lib/personal-api";
import { SourceReferencePicker } from "./source-reference-picker";

beforeEach(installPersonalFixtures);
it("preserves a selected fingerprint and frozen excerpt until the user explicitly adopts an edited source", async () => {
  const user = userEvent.setup(); let source = structuredClone(sourceFixture);
  server.use(http.get("/api/projects/:id/sources", () => HttpResponse.json({ sources: [source], total: 1, offset: 0, limit: 100 })),
    http.patch("/api/sources/:id", async ({ request }) => { const { changes } = await request.json() as { changes: SourceChanges }; source = { ...source, ...changes, revision: 2, target_fingerprint: "source-after" }; return HttpResponse.json({ source, operation_id: "source-op" }); }));
  function Harness() { const [value, setValue] = useState<EvidenceInput[]>([]); return <><SourceReferencePicker projectId={projectFixture.project_id} value={value} onChange={setValue} /><output data-testid="fingerprints">{value.map((ref) => ref.target_fingerprint).join(",")}</output></>; }
  renderWithApp(<Harness />);
  await user.click(await screen.findByRole("checkbox", { name: /业主的核对说明/ }));
  await user.click(screen.getByRole("button", { name: "维护此依据" }));
  const dialog = await screen.findByRole("dialog", { name: "编辑来源记录" });
  await user.clear(within(dialog).getByLabelText("原文摘录")); await user.type(within(dialog).getByLabelText("原文摘录"), "重新核实后的原文");
  await user.click(within(dialog).getByRole("button", { name: "保存来源记录" }));
  await screen.findByText("此来源在选定后已变化，当前仍保留之前选定的版本。");
  expect(screen.getByTestId("fingerprints")).toHaveTextContent("source-before");
  await user.click(screen.getByText("查看选定时与当前内容"));
  expect(screen.getByText("选定时的原文")).toBeVisible(); expect(screen.getByText("重新核实后的原文")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "核对后采用当前来源版本" }));
  await waitFor(() => expect(screen.getByTestId("fingerprints")).toHaveTextContent("source-after"));
});
