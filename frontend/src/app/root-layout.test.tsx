/** Shell tests: the phone bottom nav exists on top-level pages and drops on
 * detail/reading pages (workbench, ask, report reading) per the mobile H5
 * plan §4.2 — one primary fixed area at a time. */
import { describe, expect, it } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider } from "@/app/theme";
import { RootLayout } from "./root-layout";

function renderShell(route: string) {
  const router = createMemoryRouter(
    [
      {
        path: "/",
        element: <RootLayout />,
        children: [
          { index: true, element: <p>page:inbox</p> },
          { path: "videos/:videoId", element: <p>page:workbench</p> },
          { path: "ask", element: <p>page:ask</p> },
          { path: "reports", element: <p>page:reports</p> },
        ],
      },
    ],
    { initialEntries: [route] },
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <ThemeProvider>
      <QueryClientProvider client={client}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </ThemeProvider>,
  );
}

describe("RootLayout bottom nav", () => {
  it("shows the bottom nav on top-level pages", async () => {
    renderShell("/");
    await waitFor(() => expect(screen.getByTestId("bottom-nav")).toBeInTheDocument());
    expect(screen.getByText("page:inbox")).toBeInTheDocument();
  });

  it("drops the bottom nav on the workbench (video detail)", () => {
    renderShell("/videos/BVmock000");
    expect(screen.queryByTestId("bottom-nav")).not.toBeInTheDocument();
    expect(screen.getByText("page:workbench")).toBeInTheDocument();
  });

  it("drops the bottom nav on ask (conversation owns the bottom)", () => {
    renderShell("/ask");
    expect(screen.queryByTestId("bottom-nav")).not.toBeInTheDocument();
  });

  it("keeps the nav on the reports catalog but drops it while reading ?r=", async () => {
    const { unmount } = renderShell("/reports");
    await waitFor(() => expect(screen.getByTestId("bottom-nav")).toBeInTheDocument());
    unmount();
    renderShell("/reports?r=summary");
    expect(screen.queryByTestId("bottom-nav")).not.toBeInTheDocument();
    expect(screen.getByText("page:reports")).toBeInTheDocument();
  });
});
