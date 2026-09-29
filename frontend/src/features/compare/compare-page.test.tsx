import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ComparePage from "./compare-page";
import { renderWithApp } from "@/test/utils";

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
    expect(screen.getAllByText(/库内暂无证据的维度/).length).toBe(2);
  });

  it("keeps the submit disabled until two items are present", () => {
    renderWithApp(<ComparePage />);
    const btn = screen.getByRole("button", { name: "对比" });
    expect(btn).toBeDisabled();
  });
});
