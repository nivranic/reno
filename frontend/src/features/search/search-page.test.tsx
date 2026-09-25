import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SearchPage from "./search-page";
import { renderWithApp } from "@/test/utils";
import { http, HttpResponse } from "msw";
import { server } from "@/test/server";

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

  it("never lets a slow stale response overwrite a newer query's results", async () => {
    const user = userEvent.setup();
    server.use(
      http.get("/api/search", async ({ request }) => {
        const q = new URL(request.url).searchParams.get("q") ?? "";
        if (q === "慢查询") {
          await new Promise((r) => setTimeout(r, 900));
          return HttpResponse.json({
            results: [{ id: "stale", claim: "这是过期慢响应的结果", category: null,
                        space: null, polarity: "recommend", status: "candidate",
                        video: "BVslow", video_title: "慢", ms: 0, mod: "ASR",
                        evidence_text: "" }],
          });
        }
        return HttpResponse.json({
          results: [{ id: "fresh", claim: "这是新查询的结果", category: null,
                      space: null, polarity: "recommend", status: "candidate",
                      video: "BVfresh", video_title: "快", ms: 0, mod: "ASR",
                      evidence_text: "" }],
        });
      }),
    );
    renderWithApp(<SearchPage />);

    const box = screen.getByLabelText("搜索知识原子");
    await user.type(box, "慢查询");
    await waitFor(() => expect(screen.getByText(/这是过期慢响应的结果/)).toBeInTheDocument(),
                  { timeout: 2500 });
    await user.clear(box);
    await user.type(box, "新查询");
    // the claim contains the query token -> Highlight splits it across
    // <mark> elements, so assert on body text rather than one node
    await waitFor(() => expect(document.body.textContent).toContain("这是新查询的结果"),
                  { timeout: 2500 });
    // let the stale response land late - it must not clobber the newer one
    await new Promise((r) => setTimeout(r, 1100));
    expect(screen.queryByText(/这是过期慢响应的结果/)).not.toBeInTheDocument();
    expect(document.body.textContent).toContain("这是新查询的结果");
  });
});
