import { describe, expect, it } from "vitest";
import {
  activeEvents,
  clampMs,
  findEventIndex,
  fmtDur,
  fmtMs,
  parseDeepLinkMs,
} from "./time";

describe("fmtMs", () => {
  it("formats mm:ss.d", () => {
    expect(fmtMs(0)).toBe("00:00.0");
    expect(fmtMs(123_456)).toBe("02:03.4");
    expect(fmtMs(3_602_345)).toBe("60:02.3");
  });
  it("handles non-finite input", () => {
    expect(fmtMs(NaN)).toBe("--:--.-");
  });
});

describe("fmtDur", () => {
  it("compacts durations", () => {
    expect(fmtDur(null)).toBe("—");
    expect(fmtDur(0)).toBe("—");
    expect(fmtDur(45_000)).toBe("45″");
    expect(fmtDur(125_000)).toBe("2′05″");
  });
});

describe("parseDeepLinkMs", () => {
  it("accepts valid ms", () => {
    expect(parseDeepLinkMs("42000")).toBe(42_000);
  });
  it("rejects garbage and negatives", () => {
    expect(parseDeepLinkMs("abc")).toBeNull();
    expect(parseDeepLinkMs("-5")).toBeNull();
    expect(parseDeepLinkMs(null)).toBeNull();
  });
});

describe("clampMs", () => {
  it("clamps to range", () => {
    expect(clampMs(-10, 0, 100)).toBe(0);
    expect(clampMs(999, 0, 100)).toBe(100);
    expect(clampMs(50.6, 0, 100)).toBe(51);
  });
});

const EVS = [
  { id: "a", ms: 0, end: 2000 },
  { id: "b", ms: 1500, end: 5000 }, // overlaps a
  { id: "c", ms: 6000, end: 6000 }, // point event
  { id: "d", ms: 9000, end: 12000 },
];

describe("findEventIndex / activeEvents", () => {
  it("finds last event starting at or before t", () => {
    expect(findEventIndex(EVS, 1000)).toBe(0);
    expect(findEventIndex(EVS, 1600)).toBe(1);
    expect(findEventIndex(EVS, 5999)).toBe(1);
    expect(findEventIndex(EVS, 11_000)).toBe(3);
  });

  it("returns overlapping actives together (no forced uniqueness)", () => {
    const ids = activeEvents(EVS, 1800).map((e) => e.id);
    expect(ids).toEqual(["a", "b"]);
  });

  it("does NOT extend point events into ranges", () => {
    expect(activeEvents(EVS, 6000).map((e) => e.id)).toEqual(["c"]);
    expect(activeEvents(EVS, 6500).map((e) => e.id)).toEqual([]);
  });

  it("before-first and after-last", () => {
    expect(activeEvents(EVS, -1)).toEqual([]);
    expect(activeEvents(EVS, 13_000)).toEqual([]);
  });
});
