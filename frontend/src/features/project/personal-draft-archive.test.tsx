import { describe, expect, it } from "vitest";
import { act, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithApp } from "@/test/utils";
import { PersonalDraftArchive } from "./personal-draft-archive";
import { forgetPersonalRequest, readPersonalRequest, rememberPersonalRequest, usePersonalDraft } from "./use-personal-draft";

function Editor({ scope }: { scope: string }) {
  const draft = usePersonalDraft({ scope, key: "source:one", label: "来源记录草稿", initial: () => ({ title: "" }), describe: (value) => `标题：${value.title}` });
  return <><input aria-label="草稿标题" value={draft.draft.title} onChange={(event) => draft.setDraft({ title: event.target.value })} />
    <button onClick={draft.clear}>完成原保存</button><PersonalDraftArchive /></>;
}
describe("owner-bound personal drafts", () => {
  it("keeps edited text across unmount, never fills a different owner, and exposes it only through explicit local viewing", async () => {
    const user = userEvent.setup(); const view = renderWithApp(<Editor scope="owner-a" />);
    expect(screen.queryByRole("button", { name: /暂存草稿/ })).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("草稿标题"), "绍兴防水说明");
    // Replace the mounted edit while preserving the actual QueryClient.
    view.rerender(<Editor scope="owner-b" />);
    expect(screen.getByLabelText("草稿标题")).toHaveValue("");
    expect(screen.queryByText("标题：绍兴防水说明")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "暂存草稿（1）" }));
    expect(await screen.findByText("标题：绍兴防水说明")).toBeInTheDocument();
    expect(screen.getByText(/不会自动提交到当前实例/)).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.getByRole("button", { name: "暂存草稿（1）" })).toHaveFocus();
    view.rerender(<Editor scope="owner-a" />);
    expect(screen.getByLabelText("草稿标题")).toHaveValue("绍兴防水说明");
  });

  it("retains unknown original requests independently of clearing their draft and never assigns them to another owner", async () => {
    const user = userEvent.setup(); const view = renderWithApp(<Editor scope="owner-a" />);
    await user.type(screen.getByLabelText("草稿标题"), "待核实");
    const original = { operation_id: "original-operation", primary_epoch: 1, changes: { title: "待核实", file: new File(["pdf"], "资料.pdf") } };
    act(() => rememberPersonalRequest(view.client, "owner-a", "source:one", original));
    expect(readPersonalRequest(view.client, "owner-b", "source:one")).toBeUndefined();
    await user.click(screen.getByRole("button", { name: "完成原保存" }));
    expect(readPersonalRequest(view.client, "owner-a", "source:one")).toBe(original);
    expect(screen.getByRole("button", { name: "待确认保存（1）" })).toBeInTheDocument();
    act(() => forgetPersonalRequest(view.client, "owner-a", "source:one"));
    expect(screen.queryByRole("button", { name: /待确认保存/ })).not.toBeInTheDocument();
  });
});
