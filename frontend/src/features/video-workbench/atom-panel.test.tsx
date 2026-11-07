import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithApp } from "@/test/utils";
import { makeEvents } from "@/mocks/fixtures";
import { AtomPanel } from "./atom-panel";
import { createWorkbenchStore, WorkbenchStoreProvider } from "./workbench-store";

describe("workbench relation hints", () => {
  it("shows outdated relations separately from an active conflict and keeps original evidence", () => {
    const atom = makeEvents("BVmock000").atoms[0];
    atom.has_conflict = false;
    atom.has_outdated_conflict = true;
    renderWithApp(<WorkbenchStoreProvider store={createWorkbenchStore("BVmock000")}><AtomPanel atoms={[atom]} /></WorkbenchStoreProvider>);
    expect(screen.getByText(/旧关联待更新/)).toBeInTheDocument();
    expect(screen.queryByText(/存在来源分歧，人工确认不消除冲突/)).not.toBeInTheDocument();
    expect(screen.getByText(atom.claim)).toBeInTheDocument();
  });
});
