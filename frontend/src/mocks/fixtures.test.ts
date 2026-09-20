import { describe, expect, it } from "vitest";
import { makeEvents, makeVideos, mulberry32 } from "./fixtures";

describe("fixture determinism", () => {
  it("same seed produces identical sequences", () => {
    const a = mulberry32(7);
    const b = mulberry32(7);
    const seqA = Array.from({ length: 5 }, a);
    const seqB = Array.from({ length: 5 }, b);
    expect(seqA).toEqual(seqB);
  });

  it("makeVideos is stable across calls", () => {
    expect(makeVideos()).toEqual(makeVideos());
  });

  it("makeEvents ids are unique and sorted by ms at any scale", () => {
    for (const scale of [100, 1000, 10_000]) {
      const { events } = makeEvents("v" + scale, scale);
      const ids = new Set(events.map((e) => e.id));
      expect(ids.size).toBe(scale);
      const sorted = [...events].sort((x, y) => x.ms - y.ms);
      expect(events).toEqual(sorted);
    }
  });

  it("atom evidence references real event ids", () => {
    const { events, atoms } = makeEvents("vid", 200);
    const ids = new Set(events.map((e) => e.id));
    for (const a of atoms) {
      for (const e of a.evidence) expect(ids.has(e.id)).toBe(true);
    }
  });
});
