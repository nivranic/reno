import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ImportDialog } from "./import-dialog";
import { renderWithApp } from "@/test/utils";

describe("ImportDialog batch parsing (frontend hint only; backend is authority)", () => {
  it("classifies valid / invalid / duplicate lines and disables submit when none valid", async () => {
    const user = userEvent.setup();
    const onImported = vi.fn();
    renderWithApp(
      <ImportDialog open onOpenChange={() => {}} onImported={onImported} />,
    );

    await user.click(screen.getByRole("tab", { name: /批量/ }));
    const textarea = screen.getByLabelText("批量链接");
    await user.type(
      textarea,
      "https://www.bilibili.com/video/BV111{enter}not a url{enter}https://www.bilibili.com/video/BV111?from=x{enter}https://v.douyin.com/abc/",
    );

    await waitFor(() => {
      // "无效" appears in the stat row AND the helper note — assert on the
      // stat itself (span whose text starts with 无效)
      const stat = screen
        .getAllByText(/无效/)
        .find((el) => el.tagName === "SPAN" && el.textContent.startsWith("无效"));
      expect(stat).toBeTruthy();
    });
    // BV111 twice (query stripped) -> 1 duplicate row (stat span, not the hint)
    const dupeStat = screen
      .getAllByText(/重复行/)
      .find((el) => el.tagName === "SPAN" && el.textContent.startsWith("重复行"));
    expect(dupeStat?.textContent).toContain("1");
    const submit = screen.getByRole("button", { name: /提交 \d+ 条/ });
    expect(submit).toHaveTextContent("提交 2 条");
    expect(submit).toBeEnabled();

    await user.clear(textarea);
    await user.type(textarea, "still not a url");
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /提交 \d+ 条/ })).toBeDisabled();
    });
  });

  it("shows per-item failures from a partially-successful batch", async () => {
    const user = userEvent.setup();
    renderWithApp(
      <ImportDialog open onOpenChange={() => {}} onImported={() => {}} />,
    );
    await user.click(screen.getByRole("tab", { name: /批量/ }));
    await user.type(
      screen.getByLabelText("批量链接"),
      "https://www.bilibili.com/video/BV111",
    );
    await user.click(screen.getByRole("button", { name: /提交 \d+ 条/ }));
    // mock returns failed:1 + sample line
    await waitFor(() => {
      expect(screen.getByText(/1 条失败/)).toBeInTheDocument();
      expect(screen.getByText(/mock ingest failure/)).toBeInTheDocument();
    });
  });
});
