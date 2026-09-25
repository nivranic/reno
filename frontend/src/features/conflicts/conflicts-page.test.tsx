import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ConflictsPage from "./conflicts-page";
import { renderWithApp } from "@/test/utils";
import { http } from "msw";
import { server } from "@/test/server";

describe("ConflictsPage", () => {
  it("renders sides and submits a real decision (persists via API)", async () => {
    const user = userEvent.setup();
    const decisions: unknown[] = [];
    server.use(
      http.post("/api/decision", async ({ request }) => {
        decisions.push(await request.json());
        return Response.json({ ok: true });
      }),
    );
    renderWithApp(<ConflictsPage />);

    await waitFor(() => {
      expect(screen.getByText("conflict_mock_001")).toBeInTheDocument();
    });
    expect(screen.getByText(/美缝应在材料半凝固状态时刮平/)).toBeInTheDocument();
    expect(screen.getByText(/干透后再施工/)).toBeInTheDocument();

    // §11.2 two-step: select → note → explicit submit (no write on bare tap)
    await user.click(screen.getByRole("button", { name: "采纳 A" }));
    expect(decisions).toHaveLength(0);
    await user.type(screen.getByLabelText("决策备注(可选)"), "测试备注");
    await user.click(screen.getByRole("button", { name: /提交决策:采纳 A/ }));

    await waitFor(() => {
      expect(decisions).toEqual([
        { conflict_id: "conflict_mock_001", action: "accept_a", note: "测试备注" },
      ]);
    });
    // success state visible
    await waitFor(() => {
      expect(screen.getByText(/已记录决策/)).toBeInTheDocument();
    });
  });

  it("keeps the selection and note and shows a retry affordance when submission fails", async () => {
    const user = userEvent.setup();
    server.use(
      http.post("/api/decision", () => new Response(null, { status: 500 })),
    );
    renderWithApp(<ConflictsPage />);
    await waitFor(() => screen.getByText("conflict_mock_001"));

    const note = screen.getByLabelText("决策备注(可选)");
    await user.type(note, "保留我");
    await user.click(screen.getByRole("button", { name: "都不采" }));
    await user.click(screen.getByRole("button", { name: /提交决策:都不采/ }));

    await waitFor(() => {
      expect(screen.getByText(/提交失败/)).toBeInTheDocument();
    });
    // input preserved on failure
    expect(note).toHaveValue("保留我");
  });
});
