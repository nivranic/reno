/** Unified fetch wrapper for the reno JSON API.
 *
 * - JSON only. Binary endpoints (frame images, video file) are addressed by
 *   URL builders at the bottom and consumed by <img>/<video> directly.
 * - Reads and writes share `apiGet`/`apiPost`; writes are never auto-retried
 *   (TanStack mutations retry nothing by default).
 * - Failures throw ApiError with a diagnostic `detail` from FastAPI when
 *   present. AbortError passes through silently (cancellation is not an error).
 */

import type {
  ConflictsResponse,
  EventsResponse,
  FacetsResponse,
  HealthResponse,
  ImportBatchResult,
  ImportResult,
  Modality,
  ReportsResponse,
  SearchResponse,
  VideoMeta,
  VideosResponse,
  WorkbenchAtom,
} from "./api-types";

export class ApiError extends Error {
  readonly status: number; // 0 = network unreachable / no JSON
  readonly url: string;
  constructor(status: number, message: string, url: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.url = url;
  }
}

async function parseError(res: Response, url: string): Promise<ApiError> {
  let message = `HTTP ${res.status}`;
  try {
    const body: unknown = await res.json();
    if (body && typeof body === "object" && "detail" in body) {
      message = String((body as { detail: unknown }).detail ?? message);
    }
  } catch {
    /* non-JSON error body: keep HTTP status text */
  }
  return new ApiError(res.status, message, url);
}

export async function apiGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, { signal, headers: { Accept: "application/json" } });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new ApiError(0, "无法连接本地服务(reno serve 未启动?)", path);
  }
  if (!res.ok) throw await parseError(res, path);
  try {
    return (await res.json()) as T;
  } catch {
    throw new ApiError(0, "响应不是有效 JSON", path);
  }
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "无法连接本地服务(reno serve 未启动?)", path);
  }
  if (!res.ok) throw await parseError(res, path);
  return (await res.json()) as T;
}

// ---------- endpoint functions (single place that knows real paths) ----------

export const getVideos = (signal?: AbortSignal) =>
  apiGet<VideosResponse>("/api/videos", signal);

export const getVideoMeta = (videoId: string, signal?: AbortSignal) =>
  apiGet<VideoMeta>(`/api/videos/${encodeURIComponent(videoId)}/meta`, signal);

/** Canonicalize backend modality labels ("asr"/"ocr"/"vision", any casing). */
export function toModality(raw: string | null | undefined): Modality | null {
  const k = (raw ?? "").trim().toLowerCase();
  if (k === "asr") return "ASR";
  if (k === "ocr") return "OCR";
  if (k === "vision" || k === "vis" || k === "vlm") return "VIS";
  return null;
}

/** Events + atoms. Normalizes atom evidence modality ("asr"->"ASR" etc.). */
export async function getEvents(
  videoId: string,
  signal?: AbortSignal,
): Promise<EventsResponse> {
  const data = await apiGet<EventsResponse>(
    `/api/video/${encodeURIComponent(videoId)}/events`,
    signal,
  );
  for (const a of data.atoms as WorkbenchAtom[]) {
    for (const e of a.evidence) {
      const m = toModality(e.mod);
      if (m) e.mod = m;
    }
  }
  return data;
}

export const getConflicts = (signal?: AbortSignal) =>
  apiGet<ConflictsResponse>("/api/conflicts", signal);

export const searchAtoms = (
  q: string,
  category: string,
  space: string,
  signal?: AbortSignal,
) =>
  apiGet<SearchResponse>(
    `/api/search?q=${encodeURIComponent(q)}&category=${encodeURIComponent(category)}&space=${encodeURIComponent(space)}`,
    signal,
  );

export const getFacets = (signal?: AbortSignal) =>
  apiGet<FacetsResponse>("/api/facets", signal);

export const getReports = (signal?: AbortSignal) =>
  apiGet<ReportsResponse>("/api/reports", signal);

export const getHealth = (signal?: AbortSignal) =>
  apiGet<HealthResponse>("/api/health", signal);

export const postImportUrl = (url: string) =>
  apiPost<ImportResult>("/api/import", { url });

export const postImportBatch = (urls: string[]) =>
  apiPost<ImportBatchResult>("/api/import-batch", { urls });

export const postDecision = (payload: {
  conflict_id?: string;
  atom_id?: string;
  action: string;
  note?: string;
}) => apiPost<{ ok: boolean }>("/api/decision", payload);

// ---------- binary endpoints: URLs only, never fetched as JSON ----------

export const frameUrl = (videoId: string, ms: number) =>
  `/api/frame/${encodeURIComponent(videoId)}/${Math.round(ms)}`;

export const videoFileUrl = (videoId: string) =>
  `/api/video/${encodeURIComponent(videoId)}/file`;
