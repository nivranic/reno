import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { useFrame } from "./use-frame";

/** Controllable Image stub: tests decide when each request "completes" and
 * in what order, reproducing the A→B→C / C,A,B out-of-order scenario. */
class FakeImage {
  static instances: FakeImage[] = [];
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  src = "";
  constructor() {
    FakeImage.instances.push(this);
  }
  complete(targetMs: number) {
    // resolve only if this instance is still the current request
    this.onload?.();
    void targetMs;
  }
  fail() {
    this.onerror?.();
  }
}

let originalImage: typeof Image;

beforeEach(() => {
  FakeImage.instances = [];
  originalImage = window.Image;
  (window as unknown as { Image: typeof FakeImage }).Image = FakeImage;
});

afterEach(() => {
  (window as unknown as { Image: typeof Image }).Image = originalImage;
  vi.restoreAllMocks();
});

describe("useFrame race handling (task §6.3)", () => {
  it("shows the LAST requested frame when responses arrive out of order (C,A,B)", async () => {
    const { result, rerender } = renderHook(
      ({ ms }) => useFrame("vid", ms),
      { initialProps: { ms: 1_000 as number | null } },
    );
    // A = 1000 issued on mount
    await waitFor(() => expect(FakeImage.instances.length).toBe(1));

    // user rapidly clicks B then C
    act(() => rerender({ ms: 2_000 }));
    await waitFor(() => expect(FakeImage.instances.length).toBe(2));
    act(() => rerender({ ms: 3_000 }));
    await waitFor(() => expect(FakeImage.instances.length).toBe(3));

    const [imgA, imgB, imgC] = FakeImage.instances;

    // responses land in order C, A, B (A and B are stale)
    act(() => {
      imgC.onload?.();
      imgA.onload?.();
      imgB.onload?.();
    });

    await waitFor(() => {
      expect(result.current.pendingMs).toBe(3_000);
      expect(result.current.shownMs).toBe(3_000);
      expect(result.current.loading).toBe(false);
      expect(result.current.url).toContain("/api/frame/vid/3000");
    });
  });

  it("keeps the previous frame visible (stale) while a newer one loads", async () => {
    const { result, rerender } = renderHook(({ ms }) => useFrame("vid", ms), {
      initialProps: { ms: 1_000 as number | null },
    });
    const [imgA] = FakeImage.instances;
    act(() => imgA.onload?.());
    await waitFor(() => expect(result.current.shownMs).toBe(1_000));

    act(() => rerender({ ms: 9_000 }));
    await waitFor(() => {
      expect(result.current.pendingMs).toBe(9_000);
      expect(result.current.shownMs).toBe(1_000); // old frame stays visible
      expect(result.current.stale).toBe(true);
      expect(result.current.loading).toBe(true);
    });
  });

  it("surfaces failure with retry, and retry can succeed", async () => {
    const { result } = renderHook(() => useFrame("vid", 5_000));
    const [imgA] = FakeImage.instances;
    act(() => imgA.onerror?.());
    await waitFor(() => {
      expect(result.current.error).toBe(true);
      expect(result.current.loading).toBe(false);
    });

    act(() => result.current.retry());
    await waitFor(() => expect(FakeImage.instances.length).toBe(2));
    act(() => FakeImage.instances[1].onload?.());
    await waitFor(() => {
      expect(result.current.error).toBe(false);
      expect(result.current.shownMs).toBe(5_000);
    });
  });
});
