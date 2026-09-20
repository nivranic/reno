import { afterEach, describe, expect, it } from "vitest";
import { ApiError, apiGet, getEvents } from "./api";
import { server } from "@/test/server";
import { http, HttpResponse } from "msw";

// lifecycle (listen/reset/close) is owned by src/test/setup.ts
afterEach(() => server.resetHandlers());

describe("apiGet error mapping", () => {
  it("maps HTTP 404 detail into ApiError", async () => {
    server.use(
      http.get("/api/missing-thing", () => new HttpResponse(null, { status: 404 })),
    );
    await expect(apiGet("/api/missing-thing")).rejects.toSatisfy((e: unknown) => {
      return e instanceof ApiError && e.status === 404;
    });
  });

  it("maps an unhandled (network-level rejected) request to status 0", async () => {
    server.use(
      http.get("/api/nope-unhandled", () => HttpResponse.error()),
    );
    await expect(apiGet("/api/nope-unhandled")).rejects.toSatisfy(
      (e: unknown) => e instanceof ApiError && e.status === 0,
    );
  });
});

describe("getEvents normalization", () => {
  it("uppercases atom evidence modality", async () => {
    server.use(
      http.get("/api/video/:id/events", () =>
        Response.json({
          events: [],
          atoms: [
            {
              id: "a1",
              evidence: [
                { id: "e1", ms: 1, end: 2, mod: "asr", text: "" },
                { id: "e2", ms: 3, end: 4, mod: "vision", text: "" },
              ],
            },
          ],
        }),
      ),
    );
    const data = await getEvents("v1");
    expect(data.atoms[0].evidence.map((e) => e.mod)).toEqual(["ASR", "VIS"]);
  });
});
