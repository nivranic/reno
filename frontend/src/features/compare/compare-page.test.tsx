import { beforeEach, describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ComparePage from "./compare-page";
import { renderWithApp } from "@/test/utils";
import { resetReadingSessions } from "@/lib/reading-session";
import { http } from "msw";
import { server } from "@/test/server";
import { makeCompare } from "@/mocks/fixtures";

beforeEach(() => resetReadingSessions());

describe("ComparePage (MSW-backed)", () => {
  it("renders grouped evidence for both items and flags missing dimensions", async () => {
    const user = userEvent.setup();
    renderWithApp(<ComparePage />);
    await user.type(screen.getByLabelText("对比对象"), "瓷砖 地板{Enter}");
    await waitFor(() => {
      expect(screen.getByText("瓷砖")).toBeInTheDocument();
      expect(screen.getByText("地板")).toBeInTheDocument();
    });
    // evidence atoms render with polarity + nature
    expect(await screen.findByText(/瓷砖铺贴要留缝并做美缝/)).toBeInTheDocument();
    expect(screen.getAllByText(/条证据/).length).toBe(2);
    // missing-dimension disclosure exists on each side
    expect(screen.getAllByText(/当前结果未覆盖的维度/).length).toBe(2);
  });

  it("keeps the submit disabled until two items are present", () => {
    renderWithApp(<ComparePage />);
    const btn = screen.getByRole("button", { name: "对比" });
    expect(btn).toBeDisabled();
  });

  it("restores the submitted result separately from its edited draft and expansion", async () => {
    const user = userEvent.setup();
    const first = renderWithApp(<ComparePage />);
    await user.type(screen.getByLabelText("对比对象"), "瓷砖 地板{Enter}");
    await screen.findByText(/瓷砖铺贴要留缝并做美缝/);
    await user.click(screen.getAllByText(/当前结果未覆盖的维度/)[0]);
    await user.clear(screen.getByLabelText("对比对象"));
    await user.type(screen.getByLabelText("对比对象"), "尚未提交的新对象");
    first.unmount();
    renderWithApp(<ComparePage />);
    expect(screen.getByLabelText("对比对象")).toHaveValue("尚未提交的新对象");
    expect(screen.getByText(/瓷砖铺贴要留缝并做美缝/)).toBeInTheDocument();
    expect(screen.getAllByText(/当前结果未覆盖的维度/)[0].closest("details")).toHaveAttribute("open");
  });

  it("labels truncated coverage and expands only when explicitly requested", async () => {
    const limits: string[] = [];
    server.use(http.get("/api/compare", ({ request }) => {
      const limit = new URL(request.url).searchParams.get("per_dimension_limit")!;
      limits.push(limit);
      const fixture = makeCompare();
      fixture.sides[0].coverage_scope = "full_filtered_set";
      fixture.sides[0].truncated = limit === "5";
      fixture.sides[0].groups[0].truncated = limit === "5";
      fixture.sides[0].groups[0].total = 12;
      return Response.json(fixture);
    }));
    const user = userEvent.setup();
    renderWithApp(<ComparePage />);
    await user.type(screen.getByLabelText("对比对象"), "瓷砖 地板{Enter}");
    await screen.findByText("本维度还有未展示的证据，当前片段不能代表全部观点。");
    expect(limits).toEqual(["5"]);
    await user.click(screen.getByRole("button", { name: "展开更多证据" }));
    await waitFor(() => expect(limits).toEqual(["5", "25"]));
    await waitFor(() => expect(screen.queryByRole("button", { name: "展开更多证据" })).not.toBeInTheDocument());
    expect(screen.getByText(/完整筛选范围内暂无证据的维度/)).toBeInTheDocument();
  });

  it("keeps changed, missing and legacy relationships out of matching conflict claims", async () => {
    server.use(http.get("/api/compare", () => Response.json({ ...makeCompare(), conflicts: [
      { cluster_id: "current", topic: "匹配主题", linked_conflict: "c0", items: [], derivation: { state: "active", reason: "inputs_match", changed_count: 0, missing_count: 0 } },
      { cluster_id: "changed", topic: "变化主题", linked_conflict: "c1", items: [], derivation: { state: "stale", reason: "inputs_changed", changed_count: 2, missing_count: 0 } },
      { cluster_id: "missing", topic: "缺失主题", linked_conflict: "c2", items: [], derivation: { state: "missing", reason: "members_missing", changed_count: 0, missing_count: 1 } },
      { cluster_id: "legacy", topic: "旧主题", linked_conflict: null, items: [] },
    ] })));
    renderWithApp(<ComparePage />);
    await userEvent.setup().type(screen.getByLabelText("对比对象"), "瓷砖 地板{Enter}");
    const current = await screen.findByRole("region", { name: "依据匹配的分歧" });
    expect(current).toHaveTextContent("对比检索时：1 个有分歧的主题");
    expect(current).not.toHaveTextContent("变化主题");
    const history = screen.getByRole("region", { name: "历史关联待核对" });
    expect(history).toHaveTextContent("历史关联待核对（3）");
    expect(history).toHaveTextContent("已变化 2 项，缺失 0 项");
    expect(history).toHaveTextContent("已变化 0 项，缺失 1 项");
    expect(history).toHaveTextContent("判定依据未知");
  });
});
