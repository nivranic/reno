/** Shared test harness: fresh QueryClient (no retries), memory router,
 * providers — mirrors production composition without network flakiness. */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryRouter, RouterProvider } from "react-router";
import { render, type RenderOptions } from "@testing-library/react";
import { ThemeProvider } from "@/app/theme";
import { ToastProvider } from "@/components/shared/toaster";
import type { ReactNode } from "react";

export function makeTestQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
}

export function renderWithApp(
  ui: ReactNode,
  opts?: { route?: string } & Omit<RenderOptions, "wrapper">,
) {
  const router = createMemoryRouter([{ path: "/", element: ui }], {
    initialEntries: [opts?.route ?? "/"],
  });
  const client = makeTestQueryClient();
  return {
    client,
    ...render(<RouterProvider router={router} />, {
      ...opts,
      wrapper: ({ children }) => (
        <ThemeProvider>
          <QueryClientProvider client={client}>
            <ToastProvider>{children}</ToastProvider>
          </QueryClientProvider>
        </ThemeProvider>
      ),
    }),
  };
}
