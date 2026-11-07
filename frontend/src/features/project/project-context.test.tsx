import { beforeEach, describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/server";
import { renderWithApp } from "@/test/utils";
import { makeAsk } from "@/mocks/fixtures";
import { resetReadingSessions } from "@/lib/reading-session";
import AskPage from "@/features/ask/ask-page";
import { installPersonalFixtures } from "./test-fixtures";

beforeEach(() => { resetReadingSessions(); installPersonalFixtures(); });
describe("explicit project conditions", () => {
  it("allows an empty profile and keeps one-request changes separate from the project and old answers", async () => {
    const user = userEvent.setup(); const writes: unknown[] = []; const asks: Record<string, unknown>[] = [];
    server.use(http.patch("/api/projects/:id", async ({ request }) => { writes.push(await request.json()); return HttpResponse.json({}); }), http.post("/api/ask", async ({ request }) => { asks.push(await request.json() as Record<string, unknown>); return HttpResponse.json(makeAsk("防水")); }));
    renderWithApp(<AskPage />);
    await user.click(await screen.findByRole("button", { name: "使用项目条件" }));
    await user.type(screen.getByLabelText("问题输入框"), "防水{Enter}");
    await screen.findByText(/主流做法如下/);
    expect(asks[0].context_snapshot).toEqual({ 空间: "卫生间", 基层: "水泥" });
    await user.click(screen.getByRole("button", { name: "调整本次条件" }));
    await user.clear(screen.getByLabelText("条件空间")); await user.type(screen.getByLabelText("条件空间"), "厨房");
    await user.click(screen.getByRole("button", { name: "仅用于本次" }));
    expect(screen.getByText(/本次使用：空间：厨房/)).toBeInTheDocument();
    expect(screen.getByText(/本回答使用：空间：卫生间.*旧回答未重算/)).toBeInTheDocument();
    expect(writes).toHaveLength(0); expect(asks).toHaveLength(1);
  });
  it("only persists project conditions after the explicit save action", async () => {
    const { projectFixture } = await import("./test-fixtures");
    const user = userEvent.setup(); const writes: { changes: { profile: { conditions: Record<string, string> } }; base_revision: number }[] = [];
    server.use(http.patch("/api/projects/:id", async ({ request }) => { const body = await request.json() as typeof writes[number]; writes.push(body); return HttpResponse.json({ project: { ...projectFixture, profile: body.changes.profile, revision: 2 }, operation_id: "op" }); }));
    renderWithApp(<AskPage />);
    await screen.findByRole("button", { name: "使用项目条件" });
    await user.click(screen.getByRole("button", { name: "调整本次条件" }));
    await user.type(screen.getByLabelText("条件空间"), "阳台");
    await user.click(screen.getByRole("button", { name: "保存为项目条件并用于本次" }));
    await waitFor(() => expect(writes).toHaveLength(1));
    expect(writes[0].base_revision).toBe(1);
    expect(writes[0].changes.profile.conditions).toEqual({ 空间: "阳台" });
    await waitFor(() => expect(screen.getByText(/本次使用：空间：阳台/)).toBeInTheDocument());
  });
});
