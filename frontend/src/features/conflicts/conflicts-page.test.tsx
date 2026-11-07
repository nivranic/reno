import { beforeEach, describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ConflictsPage from "./conflicts-page";
import { renderWithApp } from "@/test/utils";
import { http } from "msw";
import { server } from "@/test/server";
import { makeConflicts } from "@/mocks/fixtures";
import type { DerivationState } from "@/lib/api-types";

describe("ConflictsPage", () => {
  beforeEach(() => server.use(
    http.get("/api/tasks", () => Response.json({ tasks: [] })),
    http.get("/api/capabilities", () => Response.json({ tasks: true, node_role: "all" })),
    http.get("/api/conflicts", () => {
      const data = makeConflicts();
      data.conflicts.forEach((conflict) => { conflict.target_fingerprint = "v1:test-fixture"; });
      return Response.json(data);
    }),
  ));
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
      expect(decisions).toMatchObject([
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
    const requests: Record<string, unknown>[] = [];
    server.use(
      http.post("/api/decision", async ({ request }) => {
        requests.push(await request.json() as Record<string, unknown>);
        return new Response(null, { status: 500 });
      }),
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
    await user.click(screen.getByRole("button", { name: /提交决策:都不采/ }));
    await waitFor(() => expect(requests).toHaveLength(2));
    expect(requests[1]?.event_id).toEqual(requests[0]?.event_id);
    expect(requests[0]?.target_fingerprint).toBe("v1:test-fixture");
    await user.type(note, "新理由");
    await user.click(screen.getByRole("button", { name: /提交决策:都不采/ }));
    await waitFor(() => expect(requests).toHaveLength(3));
    expect(requests[2]?.event_id).not.toEqual(requests[0]?.event_id);
  });

  it("requires another explicit choice after the displayed evidence changes", async () => {
    let fingerprint = "v1:first";
    server.use(http.get("/api/conflicts", () => {
      const data = makeConflicts();
      data.conflicts.forEach((conflict) => { conflict.target_fingerprint = fingerprint; });
      return Response.json(data);
    }));
    const { client } = renderWithApp(<ConflictsPage />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "采纳 A" }));
    await user.type(screen.getByLabelText("决策备注(可选)"), "已有草稿");
    expect(screen.getByRole("button", { name: /提交决策:采纳 A/ })).toBeEnabled();
    fingerprint = "v1:changed";
    await client.invalidateQueries({ queryKey: ["conflicts"] });
    await waitFor(() => expect(screen.getByRole("button", { name: "先选择一个决定" })).toBeDisabled());
    expect(screen.getByLabelText("决策备注(可选)")).toHaveValue("已有草稿");
  });

  it.each(["stale", "missing", "unknown", "legacy"])("keeps %s analysis and allows personal review without refreshing its basis", async (state) => {
    const requests: unknown[] = [];
    server.use(http.get("/api/conflicts", () => {
      const conflict = makeConflicts().conflicts[0];
      conflict.target_fingerprint = "v1:outdated";
      conflict.derivation = state === "legacy" ? undefined : {
        state: state as DerivationState, reason: state === "stale" ? "inputs_changed" : state === "missing" ? "members_missing" : "unbound",
        changed_count: state === "stale" ? 1 : 0, missing_count: state === "missing" ? 1 : 0,
      };
      return Response.json({ conflicts: [conflict] });
    }), http.post("/api/decision", async ({ request }) => {
      requests.push(await request.json()); return Response.json({ ok: true });
    }));
    renderWithApp(<ConflictsPage />);
    const card = await screen.findByRole("article", { name: "争议 conflict_mock_001" });
    expect(within(card).getByText(/仅记录个人意见，不会更新判定依据/)).toBeInTheDocument();
    expect(within(card).getByText(/原分析:两说分歧集中在操作时机判断/)).toBeInTheDocument();
    expect(within(card).queryByText("依据匹配")).not.toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(within(card).getByRole("button", { name: "采纳 A" }));
    await user.click(within(card).getByRole("button", { name: "提交决策:采纳 A" }));
    await waitFor(() => expect(requests).toHaveLength(1));
    expect(requests[0]).toMatchObject({ conflict_id: "conflict_mock_001", action: "accept_a", target_fingerprint: "v1:outdated" });
  });

  it("keeps judge requests explicit and reuses their id after an unconfirmed response", async () => {
    const ids: unknown[] = [];
    let reply!: () => void;
    const pending = new Promise<void>((resolve) => { reply = resolve; });
    server.use(http.post("/api/tasks", async ({ request }) => {
      const body = await request.json() as Record<string, unknown>;
      ids.push(body.request_id);
      expect(body.kind).toBe("judge");
      await pending;
      return Response.json({ detail: "暂时无法确认", code: "UNAVAILABLE" }, { status: 503 });
    }));
    renderWithApp(<ConflictsPage />);
    const user = userEvent.setup();
    const button = screen.getByRole("button", { name: "更新争议" });
    await waitFor(() => expect(button).toBeEnabled());
    expect(screen.getByText(/可能消耗模型配额或产生费用/)).toBeInTheDocument();
    expect(ids).toHaveLength(0);
    await user.click(button);
    expect(await screen.findByText(/尚未确认入队/)).toBeInTheDocument();
    expect(button).toBeDisabled();
    reply();
    expect(await screen.findByRole("alert")).toHaveTextContent("更新请求尚未确认");
    await user.click(button);
    await waitFor(() => expect(ids).toHaveLength(2));
    expect(ids[1]).toBe(ids[0]);
  });

  it("directs cloud users to the home node without submitting a judge task", async () => {
    let requests = 0;
    server.use(http.get("/api/capabilities", () => Response.json({ tasks: false, node_role: "cloud" })),
      http.post("/api/tasks", () => { requests++; return Response.json({}); }));
    renderWithApp(<ConflictsPage />);
    expect(await screen.findByText(/请到家庭节点执行/)).toBeInTheDocument();
    const button = screen.getByRole("button", { name: "更新争议" });
    expect(button).toBeDisabled();
    await userEvent.setup().click(button);
    expect(requests).toBe(0);
  });
});
