/** MSW handlers: the same pages + api client run against these in mock mode
 * (`npm run dev:mock`) and in Vitest network tests.
 *
 * Scenario switching (dev/demo): set `window.__renoScenario` to
 * "offline" | "server-error" | "not-found" | "empty"; default = full data.
 * Scale testing: /api/video/:id/events?scale=10000 generates that many events. */
import { http, HttpResponse } from "msw";
import {
  makeAsk,
  makeConflicts,
  makeEvents,
  makeFacets,
  makeHealth,
  makeMeta,
  makeReports,
  makeSearch,
  makeVideos,
  TINY_JPEG,
} from "./fixtures";

type Scenario = string | undefined;
const scenario = (): Scenario =>
  (globalThis as { __renoScenario?: string }).__renoScenario;

function netFail() {
  return HttpResponse.error();
}

export const handlers = [
  http.get("/api/videos", () => {
    if (scenario() === "offline") return netFail();
    if (scenario() === "server-error") return new HttpResponse(null, { status: 500 });
    if (scenario() === "empty")
      return HttpResponse.json({
        videos: [],
        stats: { videos: 0, atoms: 0, clusters: 0, conflicts: 0, conflicts_open: 0, jobs: {} },
      });
    return HttpResponse.json(makeVideos());
  }),

  http.get("/api/videos/:id/meta", ({ params }) => {
    if (scenario() === "not-found") return new HttpResponse(null, { status: 404 });
    return HttpResponse.json(makeMeta(String(params.id)));
  }),

  http.get("/api/video/:id/events", ({ request, params }) => {
    const scale = Number(new URL(request.url).searchParams.get("scale") ?? 120);
    return HttpResponse.json(
      makeEvents(String(params.id), Number.isFinite(scale) ? Math.min(scale, 10_000) : 120),
    );
  }),

  http.get("/api/conflicts", () => HttpResponse.json(makeConflicts())),
  http.get("/api/facets", () => HttpResponse.json(makeFacets())),
  http.get("/api/reports", () => HttpResponse.json(makeReports())),
  http.get("/api/health", () => HttpResponse.json(makeHealth())),

  http.get("/api/search", ({ request }) => {
    const url = new URL(request.url);
    const q = url.searchParams.get("q") ?? "";
    if (!q) return HttpResponse.json({ results: [] });
    return HttpResponse.json(makeSearch(q));
  }),

  http.get("/api/frame/:vid/:ms", () =>
    HttpResponse.arrayBuffer(TINY_JPEG.buffer as ArrayBuffer, {
      headers: { "Content-Type": "image/jpeg" },
    }),
  ),

  http.post("/api/import", async ({ request }) => {
    const body = (await request.json()) as { url?: string };
    if (!body.url?.trim()) return new HttpResponse(null, { status: 400 });
    return HttpResponse.json({ status: "imported", video_id: "BVmockNEW" });
  }),

  http.post("/api/import-batch", async ({ request }) => {
    const body = (await request.json()) as { urls?: string[] };
    const urls = body.urls ?? [];
    if (urls.length === 0) return new HttpResponse(null, { status: 400 });
    if (scenario() === "server-error") return new HttpResponse(null, { status: 500 });
    return HttpResponse.json({
      imported: Math.max(0, urls.length - 1),
      duplicate: 1,
      failed: 1,
      failed_sample: [`${urls[urls.length - 1]}: mock ingest failure`],
    });
  }),

  http.post("/api/ask", async ({ request }) => {
    const body = (await request.json()) as { question?: string };
    if (!body.question?.trim()) return new HttpResponse(null, { status: 400 });
    if (scenario() === "server-error") return new HttpResponse(null, { status: 500 });
    return HttpResponse.json(makeAsk(body.question));
  }),

  http.post("/api/decision", async ({ request }) => {    const body = (await request.json()) as { action?: string };
    if (!body.action) return new HttpResponse(null, { status: 400 });
    return HttpResponse.json({ ok: true });
  }),
];
