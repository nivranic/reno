import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SearchPage from "./search-page";
import { renderWithApp } from "@/test/utils";

describe("SearchPage (MSW-backed)", () => {
  it("searches on submit and highlights tokens", async () => {
    const user = userEvent.setup();
    renderWithApp(<SearchPage />);
    const input = screen.getByLabelText("搜索知识原子");
    await user.type(input, "防水");
    await user.keyboard("{Enter}");
    await waitFor(() => {
      expect(screen.getByText(/条结果/)).toBeInTheDocument();
    });
    // token highlighting is a <mark>, never raw HTML injection
    expect(document.querySelector("mark")?.textContent).toContain("防水");
    // deep link carries ms
    const link = document.querySelector<HTMLAnchorElement>('a[href*="/videos/"]');
    expect(link?.getAttribute("href")).toMatch(/\/videos\/BVmock000\?t=\d+/);
  });

  it("shows empty state for a query with no results", async () => {
    const user = userEvent.setup();
    renderWithApp(<SearchPage />);
    await user.type(screen.getByLabelText("搜索知识原子"), "不存在的词xyz{Enter}");
    await waitFor(() => {
      expect(screen.getByText(/的匹配结果/)).toBeInTheDocument();
    });
  });

  it("does not commit the query while an IME composition is active", async () => {
    const user = userEvent.setup();
    renderWithApp(<SearchPage />);
    const input = screen.getByLabelText("搜索知识原子") as HTMLInputElement;
    // CompositionEvent defaults to bubbles:false — React would never see it
    input.dispatchEvent(new CompositionEvent("compositionstart", { bubbles: true }));
    await user.type(input, "防水");
    // composition still active: even past the debounce window nothing searches
    await new Promise((r) => setTimeout(r, 600));
    expect(input.value).toBe("防水");
    expect(screen.queryByText(/条结果/)).not.toBeInTheDocument();
    // ending the composition lets the debounced commit run
    input.value = "防水";
    input.dispatchEvent(new CompositionEvent("compositionend", { bubbles: true }));
    await waitFor(() => {
      expect(screen.getByText(/条结果/)).toBeInTheDocument();
    });
  });
});
