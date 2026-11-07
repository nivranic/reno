import { useState } from "react";
import { beforeEach, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithApp } from "@/test/utils";
import { installPersonalFixtures, projectFixture } from "@/features/project/test-fixtures";
import type { CheckItemInput } from "@/lib/personal-api";
import { CheckItemEditor, newCheckItem } from "./check-item-editor";

beforeEach(installPersonalFixtures);
it("requires verification both when declaring and explicitly resolving a personal requirement's standard conflict", async () => {
  const user = userEvent.setup();
  function Harness() { const [value, setValue] = useState<CheckItemInput>(() => ({ ...newCheckItem(), basis_kind: "personal_requirement", criteria_state: "known" })); return <><CheckItemEditor projectId={projectFixture.project_id} value={value} onChange={setValue} /><output data-testid="conflict-state">{String(value.criteria.unresolved_standard_conflict)}</output></>; }
  renderWithApp(<Harness />);
  const conflict = screen.getByRole("checkbox", { name: "个人要求可能与规范冲突，尚未核实" });
  await user.click(conflict);
  expect(screen.getByLabelText("判据核实状态")).toHaveValue("needs_verification"); expect(screen.getByLabelText("判据核实状态")).toBeDisabled();
  expect(screen.getByTestId("conflict-state")).toHaveTextContent("true");
  await user.click(conflict);
  expect(screen.getByLabelText("判据核实状态")).toHaveValue("needs_verification"); expect(screen.getByLabelText("判据核实状态")).toBeEnabled();
  expect(screen.getByTestId("conflict-state")).toHaveTextContent("false");
});
