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
  AskHistoryTurn,
  AskResponse,
  CompareResponse,
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
  readonly code?: string;
  readonly currentRevision?: number;
  readonly changedRefIds?: string[];
  constructor(status: number, message: string, url: string, details?: {
    code?: string; currentRevision?: number; changedRefIds?: string[];
  }) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.url = url;
    this.code = details?.code;
    this.currentRevision = details?.currentRevision;
    this.changedRefIds = details?.changedRefIds;
  }
}

/** Shared access token (LAN/mobile deployments with auth_token set).
 * Stored by the AuthGate prompt; empty string = no auth configured. */
export const TOKEN_KEY = "reno-auth-token";
let volatileToken = "";
export const getToken = () => {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return volatileToken;
  }
};
export const setToken = (t: string) => {
  volatileToken = t;
  try {
    localStorage.setItem(TOKEN_KEY, t);
  } catch {
    /* private mode: token lives for the session only */
  }
  window.dispatchEvent(new CustomEvent("reno-token-changed"));
};

function tokenQuery(): string {
  const t = getToken();
  return t ? `?token=${encodeURIComponent(t)}` : "";
}

// These errors invalidate the installed mirror's authority to serve any text.
// Attachment-only failures and a missing individual record keep text readable.
const personalReadInvalidationCodes = new Set([
  "PERSONAL_MIRROR_READ_VERSION_CHANGED", "PERSONAL_MIRROR_READ_DISABLED",
  "PERSONAL_MIRROR_READ_NOT_INSTALLED", "PERSONAL_MIRROR_READ_PROOF_CHANGED",
  "PERSONAL_MIRROR_READ_UNAVAILABLE", "PERSONAL_MIRROR_READ_CONNECTION_CHANGED",
  "PERSONAL_MIRROR_READ_FACTORY_REQUIRED", "PERSONAL_MIRROR_READ_LIMIT",
]);
const personalAuthorityChangeCodes = new Set([
  "CAPABILITY_DISABLED", "PRIMARY_CHANGED", "READ_ONLY_REPLICA", "PERSONAL_PRIMARY_UNAVAILABLE",
]);
function isPersonalPath(url: string) {
  const path = url.split(/[?#]/, 1)[0];
  return /^\/api\/(?:capabilities|projects|decision-cards|sources|attachments|checklists|checklist-items|operations)(?:\/|$)/.test(path);
}

export async function parseError(res: Response, url: string, signal?: AbortSignal): Promise<ApiError> {
  let message = `HTTP ${res.status}`;
  let details: { code?: string; currentRevision?: number; changedRefIds?: string[] } = {};
  try {
    const body: unknown = await res.json();
    if (body && typeof body === "object") {
      if ("detail" in body) message = String((body as { detail: unknown }).detail ?? message);
      const info = body as Record<string, unknown>;
      details = {
        code: typeof info.code === "string" ? info.code : undefined,
        currentRevision: typeof info.current_revision === "number" ? info.current_revision : undefined,
        changedRefIds: Array.isArray(info.changed_ref_ids)
          ? info.changed_ref_ids.filter((v): v is string => typeof v === "string") : undefined,
      };
    }
  } catch {
    /* non-JSON error body: keep HTTP status text */
  }
  // An obsolete response must not invalidate the newly admitted owner.
  // Parsing the error body can itself outlive the original request scope.
  signal?.throwIfAborted();
  if (res.status === 401) {
    window.dispatchEvent(new CustomEvent("reno-unauthorized"));
  }
  if (details.code && (personalReadInvalidationCodes.has(details.code)
    || isPersonalPath(url) && personalAuthorityChangeCodes.has(details.code))) {
    window.dispatchEvent(new CustomEvent("reno-personal-version-changed"));
  }
  return new ApiError(res.status, message, url, details);
}

export function checkPersonalReadVersion(response: Response, headers?: Record<string, string>) {
  const expected = headers?.["X-Reno-Personal-Read-Version"];
  if (expected && response.headers.get("X-Reno-Personal-Read-Version") !== expected) {
    window.dispatchEvent(new CustomEvent("reno-personal-version-changed"));
    throw new ApiError(409, "个人记录的读取版本已变化，请重新读取。", response.url,
      { code: "PERSONAL_MIRROR_READ_VERSION_CHANGED" });
  }
}

export async function apiGet<T>(path: string, signal?: AbortSignal, headers?: Record<string, string>): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      signal,
      headers: { ...headers, Accept: "application/json", "X-Reno-Token": getToken() },
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new ApiError(0, "无法连接本地服务(reno serve 未启动?)", path);
  }
  signal?.throwIfAborted();
  if (!res.ok) throw await parseError(res, path, signal);
  checkPersonalReadVersion(res, headers);
  try {
    return (await res.json()) as T;
  } catch {
    throw new ApiError(0, "响应不是有效 JSON", path);
  }
}

/** Authenticated plain-text exports share the ordinary API error/token gate. */
export async function apiText(path: string, signal?: AbortSignal): Promise<string> {
  let res: Response;
  try {
    res = await fetch(path, {
      signal,
      headers: { Accept: "text/markdown, text/plain", "X-Reno-Token": getToken() },
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new ApiError(0, "无法连接本地服务(reno serve 未启动?)", path);
  }
  signal?.throwIfAborted();
  if (!res.ok) throw await parseError(res, path, signal);
  try { return await res.text(); }
  catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new ApiError(0, "导出内容读取失败，请重试", path);
  }
}

async function apiWrite<T>(method: "POST" | "PATCH", path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      signal,
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
        "X-Reno-Token": getToken(),
      },
      body: JSON.stringify(body),
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new ApiError(0, "无法连接本地服务(reno serve 未启动?)", path);
  }
  signal?.throwIfAborted();
  if (!res.ok) throw await parseError(res, path, signal);
  try {
    return (await res.json()) as T;
  } catch {
    throw new ApiError(0, "响应不是有效 JSON，保存结果尚未确认", path);
  }
}

export const apiPost = <T>(path: string, body: unknown, signal?: AbortSignal) =>
  apiWrite<T>("POST", path, body, signal);

export const apiPatch = <T>(path: string, body: unknown, signal?: AbortSignal) =>
  apiWrite<T>("PATCH", path, body, signal);

export async function apiUpload<T>(path: string, form: FormData, options: {
  operationId: string; primaryEpoch: number; signal?: AbortSignal;
}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      method: "POST", body: form, signal: options.signal,
      headers: { Accept: "application/json", "X-Reno-Token": getToken(),
        "X-Operation-Id": options.operationId, "X-Primary-Epoch": String(options.primaryEpoch) },
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new ApiError(0, "上传未确认，请保留照片并核对保存结果", path);
  }
  options.signal?.throwIfAborted();
  if (!res.ok) throw await parseError(res, path, options.signal);
  try { return (await res.json()) as T; }
  catch { throw new ApiError(0, "上传响应无效，保存结果尚未确认", path); }
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

export interface SearchFilters {
  category?: string;
  space?: string;
  stage?: string;
  dimension?: string;
  page?: string;
  page_size?: string;
}

export const searchAtoms = (
  q: string,
  filters: SearchFilters = {},
  signal?: AbortSignal,
) => {
  const params = new URLSearchParams({ q });
  for (const [k, v] of Object.entries(filters)) {
    if (v) params.set(k, v);
  }
  return apiGet<SearchResponse>(`/api/search?${params.toString()}`, signal);
};

export const getCompare = (items: string, signal?: AbortSignal, options?: {
  perDimensionLimit?: number;
  context?: Record<string, unknown>;
  project_id?: string;
  context_revision?: number;
}) =>
  options?.context ? apiPost<CompareResponse>("/api/compare", {
    items,
    per_dimension_limit: options.perDimensionLimit ?? 5,
    context_snapshot: options.context,
    ...(options.project_id ? { project_id: options.project_id } : {}),
    ...(options.context_revision ? { context_revision: options.context_revision } : {}),
  }, signal) : apiGet<CompareResponse>(
    `/api/compare?${new URLSearchParams({
      items,
      ...(options?.perDimensionLimit ? { per_dimension_limit: String(options.perDimensionLimit) } : {}),
    }).toString()}`,
    signal,
  );

export const getFacets = (signal?: AbortSignal) =>
  apiGet<FacetsResponse>("/api/facets", signal);

export const getReports = (signal?: AbortSignal) =>
  apiGet<ReportsResponse>("/api/reports", signal);

export const getHealth = (signal?: AbortSignal) =>
  apiGet<HealthResponse>("/api/health", signal);

export const postAsk = (question: string, history: AskHistoryTurn[], signal?: AbortSignal, options?: {
  project_id?: string; context_snapshot?: Record<string, unknown>; context_revision?: number;
}) => apiPost<AskResponse>("/api/ask", { question, history, ...options }, signal);

export const postConfigModel = (model: string) =>
  apiPost<{ ok: boolean; atomize_model: string }>("/api/config/model", { model });

export const postImportUrl = (url: string, requestId?: string) =>
  apiPost<ImportResult>("/api/import", { url, ...(requestId ? { request_id: requestId } : {}) });

export const postImportBatch = (urls: string[], requestId?: string) =>
  apiPost<ImportBatchResult>("/api/import-batch", { urls, ...(requestId ? { request_id: requestId } : {}) });

export const postDecision = (payload: {
  conflict_id?: string;
  atom_id?: string;
  action: string;
  note?: string;
  target_fingerprint?: string;
  event_id?: string;
}) => apiPost<{ ok: boolean }>("/api/decision", payload);

// ---------- binary endpoints: URLs only, never fetched as JSON ----------

export const frameUrl = (videoId: string, ms: number) =>
  `/api/frame/${encodeURIComponent(videoId)}/${Math.round(ms)}${tokenQuery()}`;

export const videoFileUrl = (videoId: string) =>
  `/api/video/${encodeURIComponent(videoId)}/file${tokenQuery()}`;
