/** App providers: theme inside out, toasts above everything, query on top.
 * Query defaults: 2 retries for reads, exponential backoff, no retries for
 * 4xx (permanent client errors), refetch on window focus. */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { isAxiosLike } from "./query-utils";
import { ThemeProvider } from "./theme";
import { ToastProvider } from "@/components/shared/toaster";

export function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 15_000,
        retry: (failureCount, error) => {
          const status = isAxiosLike(error) ? error.status : 0;
          if (status >= 400 && status < 500) return false; // permanent
          return failureCount < 2;
        },
        retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 5000),
        refetchOnWindowFocus: true,
      },
      mutations: { retry: false }, // writes: never auto-retry (no idempotency)
    },
  });
}

export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(makeQueryClient);
  return (
    <ThemeProvider>
      <QueryClientProvider client={client}>
        <ToastProvider>{children}</ToastProvider>
      </QueryClientProvider>
    </ThemeProvider>
  );
}
