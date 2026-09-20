import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeAll, vi } from "vitest";
import { server } from "./server";

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  cleanup();
  server.resetHandlers();
  delete (globalThis as { __renoScenario?: string }).__renoScenario;
});

// jsdom lacks these; only the APIs we actually use are stubbed
if (typeof window !== "undefined") {
  if (!window.matchMedia) {
    window.matchMedia = vi.fn().mockImplementation((q: string) => ({
      matches: false,
      media: q,
      addEventListener: () => {},
      removeEventListener: () => {},
    }));
  }
  Object.defineProperty(window, "ResizeObserver", {
    writable: true,
    value: class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  });
  window.scrollTo = vi.fn();
}
