/** Personal server state stays in TanStack Query. Editor inputs and frozen
 * reading context are drafts, never a second cache of mutable server data. */
import { queryOptions } from "@tanstack/react-query";
import { apiGet, apiPatch, apiPost, apiUpload, ApiError, getToken, checkPersonalReadVersion, parseError } from "./api";

export type Conditions = Record<string, string>;
export interface ReadingContext { project_id?: string; context_snapshot: Conditions; context_revision?: number }
export interface ProjectProfile { city?: string; house_type?: string; area?: string; renovation_mode?: string; budget_range?: string; current_stage?: string; conditions?: Conditions }
export interface Project { project_id: string; name: string; profile: ProjectProfile; revision: number; created_at: string; updated_at: string; archived_at?: string | null }
export interface PersonalMirror { source_library_id: string; source_node_id: string; source_primary_id: string;
  source_primary_epoch: number; installed_revision: number; snapshot_id: string; snapshot_sha256: string; committed_at: string }
export interface PersonalCapabilities { library_id: string; primary_epoch: number; personal_read: boolean; personal_write: boolean;
  personal_upload: boolean; personal_reason?: string; field_checks?: boolean; field_checks_read?: boolean; field_checks_write?: boolean;
  node_role?: string; personal_mode?: "disabled" | "local" | "mirror"; personal_read_version?: string | null;
  personal_primary_id?: string; instance_id?: string; personal_mirror?: PersonalMirror }
export interface PersonalRead { version: string | null; scope: string; mode: "local" | "mirror" }
export interface DefaultProject { enabled: boolean; project: Project | null; reason?: string }
export type DecisionState = "considering" | "adopted" | "not_adopted" | "archived";
export interface EvidenceInput { target_kind: "atom" | "conflict" | "source" | "attachment"; target_id: string; target_fingerprint: string }
export interface EvidenceRef extends EvidenceInput { ref_id: string; snapshot: Record<string, unknown>; binding_status: "active" | "stale" | "missing" | "legacy_unbound"; current_fingerprint?: string | null; current_snapshot?: Record<string, unknown> | null }
export interface DecisionOption { option_id: string; label: string; description?: string }
export interface DecisionChanges {
  title: string; question: string; context_snapshot: Conditions; alternatives: DecisionOption[];
  space?: string | null; stage?: string | null;
  selected_option_id: string | null; decision_text: string; decision_reason: string; open_questions: string[];
  content_snapshot: { kind: "answer" | "comparison" | "atom" | "note"; text: string }; state: DecisionState;
}
export interface DecisionCard extends DecisionChanges { card_id: string; project_id: string; revision: number; evidence_refs: EvidenceRef[]; needs_recheck: boolean; created_at: string; updated_at: string }
export type DecisionShareSummary = Pick<DecisionCard,
  "title" | "decision_text" | "decision_reason" | "state" | "revision" | "updated_at" | "needs_recheck">;
export interface DecisionShareSelection { card_id: string; revision: number }
export interface DecisionSharePreviewResponse {
  snapshot: { format_version: 1; kind: "personal_decision_share_snapshot"; decisions: DecisionShareSummary[] };
  snapshot_sha256: string;
  personal_read_version: string | null;
}
export interface Revision { revision: number; snapshot: Record<string, unknown>; created_at: string; operation_id: string }
export interface MutationEnvelope<T> { operation_id: string; primary_epoch: number; base_revision?: number; changes: T }
export interface MutationReceipt<T> { operation_id: string; response: T; response_status?: number }
export type SourceType = "manual_note" | "standard_record" | "product_document" | "quote" | "site_photo";
export interface SourceChanges {
  source_type: SourceType; title: string; publisher: string; document_no: string; edition: string;
  published_on: string; valid_from: string; valid_to: string; source_url: string; locator: string; excerpt: string;
  scope: Conditions; verification_state: "unverified" | "user_checked"; validity: "unknown" | "current" | "superseded" | "withdrawn";
  checked_at: string | null; check_note: string;
}
export interface SourceRecord extends SourceChanges { source_id: string; project_id: string; revision: number; created_at: string; updated_at: string; target_fingerprint: string; attachments: Attachment[] }
export interface Attachment { attachment_id: string; source_id: string; project_id: string; sha256: string; byte_size: number; media_type: string; original_name: string; state: "staging" | "ready" | "failed"; created_at: string; target_fingerprint: string;
  readable?: boolean; read_state?: "ready" | "missing" | "not_ready"; missing?: boolean; reason?: string }
export type CheckResult = "unchecked" | "pass" | "fail" | "unable_to_verify" | "not_applicable";
export type BasisKind = "standard_clause" | "manufacturer_instruction" | "personal_requirement" | "video_suggestion" | "observation_only";
export interface RequiredObservation { key: string; label: string; kind: "number" | "text"; unit?: string; method?: string }
export interface CheckCriteria { text: string; conditions_confirmed: boolean; requirement_confirmed: boolean; required_observations: RequiredObservation[]; product_model?: string; unresolved_standard_conflict?: boolean }
export interface CheckItemInput { title: string; conditions: Conditions; method_text: string; basis_kind: BasisKind; criteria_state: "known" | "needs_verification"; criteria: CheckCriteria; refs: EvidenceInput[] }
export interface Measurement { key: string; value: string | number; unit?: string; method?: string; measured_at?: string }
export interface CheckInput { record_kind: "inspection" | "remediation" | "void"; result?: Exclude<CheckResult, "unchecked">; measurements: Measurement[]; note: string; attachment_refs: string[]; checked_at?: string; supersedes_check_id?: string; criteria_snapshot_hash: string }
export interface CheckEvent extends Omit<CheckInput, "result"> { check_id: string; item_revision: number; commit_seq: number; result: CheckResult | null; created_at: string; checked_at: string; voided?: boolean; current_version?: boolean; attachment_issues?: string[] }
export interface CheckItem extends Omit<CheckItemInput, "refs"> { item_id: string; instance_id: string; revision: number; refs: EvidenceRef[]; checks: CheckEvent[]; current_result: CheckResult; current_check?: CheckEvent | null; needs_recheck: boolean; criteria_snapshot_hash: string; pass_eligibility: { allowed: boolean; reasons: string[]; label?: string } }
export interface Checklist { instance_id: string; project_id: string; title: string; space: string; stage: string; revision: number; state: "active" | "completed" | "archived"; lifecycle_state: "active" | "archived"; items: CheckItem[]; progress: { total: number; complete: number }; created_at: string; updated_at: string }
export interface ChecklistInput { title: string; space: string; stage: string; items: CheckItemInput[] }
export interface ChecklistShareSelection { instance_id: string; revision: number; items: { item_id: string; revision: number; last_commit_seq: number }[] }
export type ChecklistShareItem = Pick<CheckItem, "title" | "revision" | "current_result" | "needs_recheck" | "basis_kind">;
export interface ChecklistShareSummary extends Pick<Checklist, "title" | "state" | "lifecycle_state" | "revision" | "updated_at" | "progress"> { items: ChecklistShareItem[] }
export interface ChecklistSharePreviewResponse {
  snapshot: { format_version: 1; kind: "personal_checklist_share_snapshot"; checklists: ChecklistShareSummary[] };
  snapshot_sha256: string;
  personal_read_version: string | null;
}
/** Commit order, including voids and backdated observations, binds the current detail. */
export function checklistShareSelection(checklist: Checklist): ChecklistShareSelection[] {
  return [{ instance_id: checklist.instance_id, revision: checklist.revision,
    items: checklist.items.map((item) => ({ item_id: item.item_id, revision: item.revision,
      last_commit_seq: item.checks.reduce((latest, check) => Math.max(latest, check.commit_seq), 0) })) }];
}

const segment = encodeURIComponent;
export const personalKeys = {
  all: ["personal"] as const, capabilities: ["personal", "capabilities"] as const, project: ["personal", "default-project"] as const,
  cards: (id: string) => ["personal", "cards", id] as const, card: (id: string) => ["personal", "card", id] as const,
  revisions: (id: string) => ["personal", "revisions", id] as const, sources: (id: string) => ["personal", "sources", id] as const,
  checklists: (id: string) => ["personal", "checklists", id] as const, checklist: (id: string) => ["personal", "checklist", id] as const,
};
export const personalCapabilitiesQuery = queryOptions({ queryKey: personalKeys.capabilities, queryFn: ({ signal }) => apiGet<PersonalCapabilities>("/api/capabilities", signal), staleTime: 30_000, retry: false });
export function personalReadHeaders(read?: PersonalRead): Record<string, string> {
  if (read?.mode === "mirror" && !/^[0-9a-f]{64}$/.test(read.version ?? "")) throw new ApiError(403, "个人读取权限尚未确认。", "");
  return read?.version ? { "X-Reno-Personal-Read-Version": read.version } : {};
}
function personalQuery<T>(key: readonly unknown[], path: string, read?: PersonalRead, enabled = true) {
  return queryOptions({ queryKey: [...key, "read", read?.scope ?? "legacy-local"], meta: { personalRead: true },
    queryFn: ({ signal }) => apiGet<T>(path, signal, personalReadHeaders(read)), enabled, retry: false });
}
export const defaultProjectFor = (read?: PersonalRead) => personalQuery<DefaultProject>(personalKeys.project, "/api/projects/default", read);
export const defaultProjectQuery = defaultProjectFor(); // Standalone historical local callers only.
export const decisionCardsQuery = (projectId: string, state = "", offset = 0, filters: { space?: string; stage?: string } = {}, read?: PersonalRead) => personalQuery<{ cards: DecisionCard[]; total: number; offset: number; limit: number }>([...personalKeys.cards(projectId), state, offset, filters], `/api/projects/${segment(projectId)}/decision-cards?${new URLSearchParams({ limit: "30", offset: String(offset), ...(state ? { state } : {}), ...(filters.space ? { space: filters.space } : {}), ...(filters.stage ? { stage: filters.stage } : {}) }).toString()}`, read, !!projectId);
export const decisionCardQuery = (id: string, read?: PersonalRead) => personalQuery<{ card: DecisionCard }>(personalKeys.card(id), `/api/decision-cards/${segment(id)}`, read, !!id);
export const decisionRevisionsQuery = (id: string, read?: PersonalRead) => personalQuery<{ revisions: Revision[] }>(personalKeys.revisions(id), `/api/decision-cards/${segment(id)}/revisions`, read, !!id);
export const sourcesQuery = (id: string, read?: PersonalRead) => personalQuery<{ sources: SourceRecord[]; total: number; offset: number; limit: number }>(personalKeys.sources(id), `/api/projects/${segment(id)}/sources?limit=100`, read, !!id);
export const checklistsQuery = (id: string, offset = 0, read?: PersonalRead) => personalQuery<{ checklists: Checklist[]; total: number; offset: number; limit: number }>([...personalKeys.checklists(id), offset], `/api/projects/${segment(id)}/checklists?limit=30&offset=${offset}`, read, !!id);
export const checklistQuery = (id: string, read?: PersonalRead) => personalQuery<{ checklist: Checklist }>(personalKeys.checklist(id), `/api/checklists/${segment(id)}`, read, !!id);
export const createChecklist = (id: string, body: MutationEnvelope<ChecklistInput>, signal?: AbortSignal) => apiPost<{ checklist: Checklist; operation_id: string }>(`/api/projects/${segment(id)}/checklists`, body, signal);
export const patchChecklist = (id: string, body: MutationEnvelope<{ lifecycle_state: "active" | "archived" }>, signal?: AbortSignal) => apiPatch<{ checklist: Checklist; operation_id: string }>(`/api/checklists/${segment(id)}`, body, signal);
export const patchCheckItem = (id: string, body: MutationEnvelope<CheckItemInput>, signal?: AbortSignal) => apiPatch<{ item: CheckItem; operation_id: string }>(`/api/checklist-items/${segment(id)}`, body, signal);
export const appendCheck = (id: string, body: MutationEnvelope<CheckInput>, signal?: AbortSignal) => apiPost<{ check: CheckEvent; item: CheckItem; checklist: Checklist; operation_id: string }>(`/api/checklist-items/${segment(id)}/checks`, body, signal);
export const patchProject = (id: string, body: MutationEnvelope<{ name?: string; profile?: ProjectProfile }>, signal?: AbortSignal) => apiPatch<{ project: Project; operation_id: string }>(`/api/projects/${segment(id)}`, body, signal);
export const createDecisionCard = (projectId: string, body: MutationEnvelope<DecisionChanges & { evidence_refs: EvidenceInput[] }>, signal?: AbortSignal) => apiPost<{ card: DecisionCard; operation_id: string }>(`/api/projects/${segment(projectId)}/decision-cards`, body, signal);
export const patchDecisionCard = (id: string, body: MutationEnvelope<Partial<DecisionChanges>>, signal?: AbortSignal) => apiPatch<{ card: DecisionCard; operation_id: string }>(`/api/decision-cards/${segment(id)}`, body, signal);
export const updateDecisionEvidence = (id: string, body: MutationEnvelope<{ confirm: true; evidence_refs: EvidenceInput[] }>, signal?: AbortSignal) => apiPost<{ card: DecisionCard; operation_id: string }>(`/api/decision-cards/${segment(id)}/evidence`, body, signal);
export const getOperation = <T,>(id: string, signal?: AbortSignal) => apiGet<MutationReceipt<T>>(`/api/operations/${segment(id)}`, signal);
export const createSource = (id: string, body: MutationEnvelope<SourceChanges>, signal?: AbortSignal) => apiPost<{ source: SourceRecord; operation_id: string }>(`/api/projects/${segment(id)}/sources`, body, signal);
export const patchSource = (id: string, body: MutationEnvelope<Partial<SourceChanges>>, signal?: AbortSignal) => apiPatch<{ source: SourceRecord; operation_id: string }>(`/api/sources/${segment(id)}`, body, signal);
export const uploadAttachment = (sourceId: string, file: File, operationId: string, primaryEpoch: number, signal?: AbortSignal) => {
  const body = new FormData(); body.append("file", file);
  return apiUpload<{ attachment: Attachment; operation_id: string }>(`/api/sources/${segment(sourceId)}/attachments`, body, { operationId, primaryEpoch, signal });
};
export function attachmentReadable(attachment: Attachment, read?: PersonalRead) {
  return read?.mode === "mirror" ? attachment.readable === true :
    (attachment.readable ?? (attachment.state === "ready" && attachment.missing !== true));
}
export async function downloadAttachment(attachment: Attachment, read?: PersonalRead, signal?: AbortSignal) {
  if (!attachmentReadable(attachment, read)) throw new ApiError(403, "附件当前不可读取。", "");
  const headers = personalReadHeaders(read);
  const response = await fetch(`/api/attachments/${segment(attachment.attachment_id)}/file`, { signal, headers: { ...headers, "X-Reno-Token": getToken() } });
  signal?.throwIfAborted();
  if (!response.ok) throw await parseError(response, `/api/attachments/${segment(attachment.attachment_id)}/file`, signal);
  checkPersonalReadVersion(response, headers);
  const blob = await response.blob(); signal?.throwIfAborted();
  const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url; link.download = attachment.original_name; link.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function textRequest(path: string, body?: unknown, read?: PersonalRead, signal?: AbortSignal): Promise<string> {
  const headers = personalReadHeaders(read);
  const response = await fetch(path, { signal, method: body ? "POST" : "GET", headers: { ...headers, "X-Reno-Token": getToken(), ...(body ? { "Content-Type": "application/json" } : {}) }, body: body ? JSON.stringify(body) : undefined });
  signal?.throwIfAborted();
  if (!response.ok) throw await parseError(response, path, signal);
  checkPersonalReadVersion(response, headers);
  const text = await response.text(); signal?.throwIfAborted(); return text;
}
export const exportDecision = (id: string, read?: PersonalRead, signal?: AbortSignal) => textRequest(`/api/decision-cards/${segment(id)}/export`, undefined, read, signal);
export const exportDecisions = (projectId: string, ids: string[], read?: PersonalRead, signal?: AbortSignal) => textRequest(`/api/projects/${segment(projectId)}/decision-cards/export`, { card_ids: ids }, read, signal);
/** One explicit read, retained only by the open preview; no query cache entry. */
export async function previewDecisionShare(projectId: string, selection: readonly DecisionShareSelection[],
  read: PersonalRead, signal?: AbortSignal): Promise<DecisionSharePreviewResponse> {
  const path = `/api/projects/${segment(projectId)}/decision-cards/share-preview`;
  const headers = personalReadHeaders(read);
  signal?.throwIfAborted();
  let response: Response;
  try {
    response = await fetch(path, { method: "POST", signal,
      headers: { ...headers, "Content-Type": "application/json", Accept: "application/json", "X-Reno-Token": getToken() },
      body: JSON.stringify({ selection: selection.map(({ card_id, revision }) => ({ card_id, revision })) }) });
  } catch (error) {
    signal?.throwIfAborted();
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(0, "无法读取分享摘要，请检查服务后重试。", path);
  }
  signal?.throwIfAborted();
  if (!response.ok) throw await parseError(response, path, signal);
  checkPersonalReadVersion(response, headers);
  let value: DecisionSharePreviewResponse;
  try { value = await response.json(); }
  catch (error) {
    signal?.throwIfAborted();
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(0, "分享摘要响应无效，请重试。", path);
  }
  signal?.throwIfAborted();
  if (!value || !value.snapshot || value.snapshot.format_version !== 1
    || value.snapshot.kind !== "personal_decision_share_snapshot"
    || !Array.isArray(value.snapshot.decisions) || value.snapshot.decisions.length !== selection.length
    || !/^[0-9a-f]{64}$/.test(value.snapshot_sha256)
    || value.snapshot.decisions.some((row, index) => !row || typeof row.title !== "string"
      || typeof row.decision_text !== "string" || typeof row.decision_reason !== "string"
      || !["considering", "adopted", "not_adopted", "archived"].includes(row.state)
      || !Number.isInteger(row.revision) || row.revision < 1 || row.revision !== selection[index].revision || typeof row.updated_at !== "string"
      || typeof row.needs_recheck !== "boolean")) {
    throw new ApiError(0, "分享摘要响应无效，请重试。", path);
  }
  if (value.personal_read_version !== read.version) {
    window.dispatchEvent(new CustomEvent("reno-personal-version-changed"));
    throw new ApiError(409, "个人记录的读取版本已变化，请重新读取。", path,
      { code: "PERSONAL_MIRROR_READ_VERSION_CHANGED" });
  }
  // Retain only the server's seven summary values even if a future response
  // contains unrelated private properties. Never substitute a cached card.
  return { snapshot: { format_version: 1, kind: "personal_decision_share_snapshot",
    decisions: value.snapshot.decisions.map(({ title, decision_text, decision_reason, state, revision, updated_at, needs_recheck }) =>
      ({ title, decision_text, decision_reason, state, revision, updated_at, needs_recheck })) },
    snapshot_sha256: value.snapshot_sha256, personal_read_version: value.personal_read_version };
}
function checklistShareObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function checklistShareSummary(value: unknown, expected: ChecklistShareSelection): value is ChecklistShareSummary {
  if (!checklistShareObject(value) || typeof value.title !== "string" || typeof value.updated_at !== "string"
    || value.revision !== expected.revision || typeof value.state !== "string" || !["active", "completed", "archived"].includes(value.state)
    || typeof value.lifecycle_state !== "string" || !["active", "archived"].includes(value.lifecycle_state)
    || !Array.isArray(value.items) || value.items.length !== expected.items.length
    || !checklistShareObject(value.progress) || value.progress.total !== value.items.length
    || !Number.isSafeInteger(value.progress.complete) || Number(value.progress.complete) < 0
    || Number(value.progress.complete) > value.items.length) return false;
  if (!value.items.every((item: unknown, index: number) => checklistShareObject(item)
    && typeof item.title === "string" && item.revision === expected.items[index].revision
    && typeof item.needs_recheck === "boolean" && typeof item.current_result === "string"
    && ["unchecked", "pass", "fail", "unable_to_verify", "not_applicable"].includes(item.current_result)
    && typeof item.basis_kind === "string"
    && ["standard_clause", "manufacturer_instruction", "personal_requirement", "video_suggestion", "observation_only"].includes(item.basis_kind))) return false;
  const items = value.items as ChecklistShareItem[];
  const complete = items.filter((item) => !item.needs_recheck && ["pass", "not_applicable"].includes(item.current_result)).length;
  return value.progress.complete === complete && value.state === (value.lifecycle_state === "archived" ? "archived"
    : items.length > 0 && complete === items.length ? "completed" : "active");
}
/** Explicit, cancellable read; the returned object contains only summary fields. */
export async function previewChecklistShare(projectId: string, selection: readonly ChecklistShareSelection[],
  read: PersonalRead, signal?: AbortSignal): Promise<ChecklistSharePreviewResponse> {
  const path = `/api/projects/${segment(projectId)}/checklists/share-preview`;
  const headers = personalReadHeaders(read);
  signal?.throwIfAborted();
  if (selection.length !== 1 || selection.some((entry) => !entry || typeof entry.instance_id !== "string" || !entry.instance_id
    || !Number.isSafeInteger(entry.revision) || entry.revision < 1 || !Array.isArray(entry.items)
    || entry.items.length < 1 || entry.items.length > 50
    || entry.items.some((item) => !item || typeof item.item_id !== "string" || !item.item_id
      || !Number.isSafeInteger(item.revision) || item.revision < 1
      || !Number.isSafeInteger(item.last_commit_seq) || item.last_commit_seq < 0)
    || new Set(entry.items.map((item) => item.item_id)).size !== entry.items.length)) {
    throw new ApiError(0, "清单选集无效，请重新读取清单。", path);
  }
  // Copy the selection before awaiting: neither extra fields nor later caller
  // mutations may change the request or its response revision checks.
  const selected = selection.map(({ instance_id, revision, items }) => ({ instance_id, revision,
    items: items.map(({ item_id, revision: itemRevision, last_commit_seq }) => ({ item_id, revision: itemRevision, last_commit_seq })) }));
  let response: Response;
  try {
    response = await fetch(path, { method: "POST", signal,
      headers: { ...headers, "Content-Type": "application/json", Accept: "application/json", "X-Reno-Token": getToken() },
      body: JSON.stringify({ selection: selected }) });
  } catch (error) {
    signal?.throwIfAborted();
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(0, "无法读取清单摘要，请检查服务后重试。", path);
  }
  signal?.throwIfAborted();
  if (!response.ok) throw await parseError(response, path, signal);
  checkPersonalReadVersion(response, headers);
  let value: unknown;
  try { value = await response.json(); }
  catch (error) {
    signal?.throwIfAborted();
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(0, "清单摘要响应无效，请重试。", path);
  }
  signal?.throwIfAborted();
  if (!checklistShareObject(value) || !checklistShareObject(value.snapshot) || value.snapshot.format_version !== 1
    || value.snapshot.kind !== "personal_checklist_share_snapshot" || !Array.isArray(value.snapshot.checklists)
    || value.snapshot.checklists.length !== 1 || !checklistShareSummary(value.snapshot.checklists[0], selected[0])
    || typeof value.snapshot_sha256 !== "string" || !/^[0-9a-f]{64}$/.test(value.snapshot_sha256)
    || !(value.personal_read_version === null || typeof value.personal_read_version === "string" && /^[0-9a-f]{64}$/.test(value.personal_read_version))) {
    throw new ApiError(0, "清单摘要响应无效，请重试。", path);
  }
  if (value.personal_read_version !== read.version) {
    window.dispatchEvent(new CustomEvent("reno-personal-version-changed"));
    throw new ApiError(409, "个人记录的读取版本已变化，请重新读取。", path,
      { code: "PERSONAL_MIRROR_READ_VERSION_CHANGED" });
  }
  const { title, state, lifecycle_state, revision, updated_at, progress, items } = value.snapshot.checklists[0];
  return { snapshot: { format_version: 1, kind: "personal_checklist_share_snapshot", checklists: [{
    title, state, lifecycle_state, revision, updated_at, progress: { total: progress.total, complete: progress.complete },
    items: items.map(({ title: itemTitle, revision: itemRevision, current_result, needs_recheck, basis_kind }) =>
      ({ title: itemTitle, revision: itemRevision, current_result, needs_recheck, basis_kind })),
  }] }, snapshot_sha256: value.snapshot_sha256, personal_read_version: value.personal_read_version };
}
export function downloadMarkdown(text: string, filename: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/markdown;charset=utf-8" }));
  const link = document.createElement("a"); link.href = url; link.download = filename; link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
