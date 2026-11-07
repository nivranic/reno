import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { makeAsk, makeCompare } from "@/mocks/fixtures";

const api = vi.hoisted(() => ({ postAsk: vi.fn(), getCompare: vi.fn() }));
vi.mock("./api", () => api);
const open = () => import("./reading-session");
beforeEach(async () => {
  vi.resetModules(); vi.clearAllMocks(); sessionStorage.clear();
  api.postAsk.mockResolvedValue(makeAsk("防水"));
  api.getCompare.mockResolvedValue(makeCompare());
});
afterEach(() => vi.restoreAllMocks());

describe("bounded library reading sessions", () => {
  it("only persists after backend identity, and restores completed snapshots without requests", async () => {
    let store = await open();
    store.patchAsk({ draft: "anonymous" });
    expect(sessionStorage.length).toBe(0);
    store.initializeReadingNamespace("library-a");
    await store.submitAsk("防水");
    store.patchAsk({ draft: "地暖条件" });
    expect(sessionStorage.key(0)).toContain("v1:");
    expect(sessionStorage.key(0)).toContain("library-a");
    vi.resetModules(); store = await open();
    store.initializeReadingNamespace("library-a");
    expect(store.getReadingState().ask.turns).toHaveLength(2);
    expect(store.getReadingState().ask.draft).toBe("地暖条件");
    expect(api.postAsk).toHaveBeenCalledTimes(1);
  });

  it("restores a pending request as interrupted and never resubmits on refresh", async () => {
    let store = await open(); store.initializeReadingNamespace("library-a");
    api.postAsk.mockImplementation(() => new Promise(() => {}));
    void store.submitAsk("防水");
    vi.resetModules(); store = await open(); store.initializeReadingNamespace("library-a");
    expect(store.getReadingState().ask.status).toBe("interrupted");
    expect(store.getReadingState().ask.turns[0].content).toBe("防水");
    expect(api.postAsk).toHaveBeenCalledTimes(1);
  });

  it("routes a delayed response to its original session and prevents revival after clear", async () => {
    const store = await open(); store.initializeReadingNamespace("library-a");
    let done!: (value: ReturnType<typeof makeAsk>) => void;
    api.postAsk.mockImplementation(() => new Promise((resolve) => { done = resolve; }));
    const request = store.submitAsk("防水");
    const signal = api.postAsk.mock.calls[0][2] as AbortSignal;
    store.clearAskSession();
    expect(signal.aborted).toBe(true);
    done(makeAsk("防水")); await request;
    expect(store.getReadingState().ask.turns).toEqual([]);
    expect(store.getReadingState().ask.status).toBe("idle");
  });

  it("marks unload interrupted before a browser cancellation can report a network error", async () => {
    const store = await open(); store.initializeReadingNamespace("library-a");
    let reject!: (error: Error) => void;
    api.postAsk.mockImplementation(() => new Promise((_, fail) => { reject = fail; }));
    const pending = store.submitAsk("防水");
    store.interruptReadingRequests();
    reject(new TypeError("Failed to fetch")); await pending;
    expect(store.getReadingState().ask.status).toBe("interrupted");
    expect(store.getReadingState().ask.error).toBeUndefined();
  });

  it("clears old identities and rejects a pending old-library response", async () => {
    const store = await open(); store.initializeReadingNamespace("library-a");
    let done!: (value: ReturnType<typeof makeAsk>) => void;
    api.postAsk.mockImplementation(() => new Promise((resolve) => { done = resolve; }));
    const request = store.submitAsk("防水");
    store.initializeReadingNamespace("library-b");
    done(makeAsk("防水")); await request;
    expect(store.getReadingState().ask.turns).toEqual([]);
    expect([...Array(sessionStorage.length)].map((_, i) => sessionStorage.key(i)).some((key) => key?.includes("library-a"))).toBe(false);
  });

  it("bounds exchanges to twenty messages, with an explicit trimming notice", async () => {
    const store = await open(); store.initializeReadingNamespace("library-a");
    for (let i = 0; i < 15; i++) await store.submitAsk(`防水${i}`);
    expect(store.getReadingState().ask.turns).toHaveLength(20);
    expect(store.getReadingState().ask.turns[0].content).toBe("防水5");
    expect(store.getReadingState().ask.notice).toContain("上限");
    expect(new TextEncoder().encode(sessionStorage.getItem(sessionStorage.key(0)!)!).length).toBeLessThan(store.READING_MAX_BYTES);
  });

  it("drops an oversized completed exchange instead of exceeding the byte limit", async () => {
    const store = await open(); store.initializeReadingNamespace("library-a");
    api.postAsk.mockResolvedValue({ answer: "水".repeat(300_000), refs: [], conflicts: [] });
    await store.submitAsk("防水");
    expect(store.getReadingState().ask.notice).toContain("512 KiB");
    expect(new TextEncoder().encode(sessionStorage.getItem(sessionStorage.key(0)!)!).length).toBeLessThan(store.READING_MAX_BYTES);
  });

  it("expires a snapshot after twenty-four hours", async () => {
    let store = await open(); store.initializeReadingNamespace("library-a"); store.patchAsk({ draft: "过期" });
    const key = [...Array(sessionStorage.length)].map((_, i) => sessionStorage.key(i)!).find((key) => key.endsWith(":ask"))!;
    const saved = JSON.parse(sessionStorage.getItem(key)!); saved.updated_at = Date.now() - store.READING_TTL - 1;
    sessionStorage.setItem(key, JSON.stringify(saved));
    vi.resetModules(); store = await open(); store.initializeReadingNamespace("library-a");
    expect(store.getReadingState().ask.draft).toBe("");
  });

  it("recovers from invalid JSON and disabled storage in memory", async () => {
    let store = await open(); store.initializeReadingNamespace("library-a");
    sessionStorage.setItem(sessionStorage.key(0)!, "{broken");
    vi.resetModules(); store = await open(); store.initializeReadingNamespace("library-a");
    expect(store.getReadingState().storageNotice).toContain("刷新后");
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new DOMException("full", "QuotaExceededError"); });
    store.patchAsk({ draft: "内存中的草稿" });
    expect(store.getReadingState().ask.draft).toBe("内存中的草稿");
    await store.submitAsk();
    expect(store.getReadingState().ask.turns).toHaveLength(2);
  });

  it("rejects syntactically valid but malformed nested knowledge metadata", async () => {
    let store = await open(); store.initializeReadingNamespace("library-a"); await store.submitAsk("防水");
    const key = [...Array(sessionStorage.length)].map((_, i) => sessionStorage.key(i)!).find((key) => key.endsWith(":ask"))!;
    const saved = JSON.parse(sessionStorage.getItem(key)!);
    saved.session.turns[1].response.refs[0].exceptions = { invalid: true };
    sessionStorage.setItem(key, JSON.stringify(saved));
    vi.resetModules(); store = await open(); store.initializeReadingNamespace("library-a");
    expect(store.getReadingState().ask.turns).toHaveLength(0);
    expect(store.getReadingState().storageNotice).toContain("刷新后");
  });

  it("restores comparison result, draft, and expansion without an automatic request", async () => {
    let store = await open(); store.initializeReadingNamespace("library-a");
    await store.submitCompare("瓷砖 地板"); store.patchCompare({ draft: "水泥 地板", expanded: { 瓷砖: true } });
    vi.resetModules(); store = await open(); store.initializeReadingNamespace("library-a");
    expect(store.getReadingState().compare.result?.sides).toHaveLength(2);
    expect(store.getReadingState().compare.draft).toBe("水泥 地板");
    expect(store.getReadingState().compare.expanded).toEqual({ 瓷砖: true });
    expect(api.getCompare).toHaveBeenCalledTimes(1);
  });
});
