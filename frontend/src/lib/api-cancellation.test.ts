import { describe, expect, it, vi } from "vitest";
import { parseError } from "./api";

describe("obsolete personal response cancellation", () => {
  it.each([401, 409])("does not dispatch authority events from an aborted %s error body", async (status) => {
    const controller = new AbortController(); let finish!: (value: unknown) => void;
    const response = new Response("{}", { status });
    vi.spyOn(response, "json").mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    const listener = vi.fn();
    window.addEventListener("reno-unauthorized", listener);
    window.addEventListener("reno-personal-version-changed", listener);
    try {
      const pending = parseError(response, "/api/projects/project-a", controller.signal);
      controller.abort(); finish({ code: "PRIMARY_CHANGED", detail: "旧任期" });
      await expect(pending).rejects.toMatchObject({ name: "AbortError" });
      expect(listener).not.toHaveBeenCalled();
    } finally {
      window.removeEventListener("reno-unauthorized", listener);
      window.removeEventListener("reno-personal-version-changed", listener);
    }
  });
});
