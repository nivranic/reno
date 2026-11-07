/** One bounded reading session per tab and library. Requests live outside routes:
 * following an evidence link never destroys the original request or its result. */
import { useSyncExternalStore } from "react";
import { getCompare, postAsk } from "./api";
import type { AskHistoryTurn, AskResponse, CompareResponse } from "./api-types";
import type { ReadingContext } from "./personal-api";
import { newOperationId } from "./id";

export const READING_SCHEMA = 1;
export const READING_TTL = 24 * 60 * 60 * 1000;
export const READING_MAX_BYTES = 512 * 1024;
export const READING_MAX_MESSAGES = 20;
const PREFIX = `reno-reading:v${READING_SCHEMA}:`;
const uid = newOperationId;
export interface ScrollAnchor { top: number; anchor?: string; offset?: number }
export interface ReadingTurn extends AskHistoryTurn {
  id: string;
  response?: AskResponse;
  context?: ReadingContext;
}
export interface AskSession {
  session_id: string;
  generation: number;
  draft: string;
  turns: ReadingTurn[];
  expandedRefs: Record<string, boolean>;
  context?: ReadingContext;
  status: "idle" | "submitting" | "answered" | "degraded" | "failed" | "interrupted";
  request_id?: string;
  error?: string;
  notice?: string;
  scroll?: ScrollAnchor;
}
export interface CompareSession {
  session_id: string;
  generation: number;
  draft: string;
  items: string;
  expanded: Record<string, boolean>;
  perDimensionLimit: number;
  context?: ReadingContext;
  resultContext?: ReadingContext;
  status: "editing" | "loading" | "ready" | "empty" | "failed";
  result?: CompareResponse;
  request_id?: string;
  error?: string;
  notice?: string;
  scroll?: ScrollAnchor;
}
interface ReadingState { ask: AskSession; compare: CompareSession; storageNotice?: string }
const freshAsk = (generation = 0): AskSession => ({ session_id: uid(), generation, draft: "", turns: [], expandedRefs: {}, status: "idle" });
const freshCompare = (generation = 0): CompareSession => ({ session_id: uid(), generation, draft: "", items: "", expanded: {}, perDimensionLimit: 5, status: "editing" });
let state: ReadingState = { ask: freshAsk(), compare: freshCompare() };
let namespace: string | undefined;
let askController: AbortController | undefined;
let compareController: AbortController | undefined;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((listener) => listener());
const bytes = (value: unknown) => new TextEncoder().encode(JSON.stringify(value)).length;
const memoryWarning = "本标签页暂存不可用，当前仅保存在内存；刷新后可能无法恢复。";

function persist() {
  if (!namespace) return;
  try {
    for (const kind of ["ask", "compare"] as const) {
      sessionStorage.setItem(`${namespace}:${kind}`, JSON.stringify({ schema: READING_SCHEMA, updated_at: Date.now(), session: state[kind] }));
    }
  } catch {
    // Remove a stale successful snapshot so a later reload cannot resurrect
    // content the user has just cleared or replaced after hitting a quota.
    try { sessionStorage.removeItem(`${namespace}:ask`); sessionStorage.removeItem(`${namespace}:compare`); } catch { /* disabled */ }
    state = { ...state, storageNotice: memoryWarning };
  }
}

function bounded(next: ReadingState): ReadingState {
  const ask = { ...next.ask, turns: [...next.ask.turns] };
  let trimmed = false;
  while (ask.turns.length > READING_MAX_MESSAGES || bytes(ask) > READING_MAX_BYTES - 512) {
    // Remove the oldest completed exchange first, preserving the pending turn.
    const end = ask.turns.findIndex((turn) => turn.role === "assistant");
    if (end < 0) break;
    ask.turns.splice(0, end + 1);
    trimmed = true;
  }
  if (bytes(ask) > READING_MAX_BYTES - 512) { ask.draft = ""; ask.turns = []; trimmed = true; }
  if (trimmed) ask.notice = "暂存已达到 20 条消息或 512 KiB 上限，较早的完整问答已移除。重要内容请另行保存。";
  ask.expandedRefs = Object.fromEntries(ask.turns.filter((turn) => ask.expandedRefs[turn.id]).map((turn) => [turn.id, true]));
  let compare = next.compare;
  if (bytes(compare) > READING_MAX_BYTES - 512) compare = { ...freshCompare(compare.generation + 1), notice: "对比结果超出 512 KiB 暂存上限，请缩小对比范围后重新提交。" };
  return { ...next, ask, compare };
}
function update(next: ReadingState) { state = bounded(next); persist(); emit(); }
const object = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
const optionalText = (value: unknown) => value == null || typeof value === "string";
const stringArray = (value: unknown) => value == null || (Array.isArray(value) && value.every((item) => typeof item === "string"));
function validKnowledge(value: Record<string, unknown>) {
  return optionalText(value.evidence_nature) && optionalText(value.review_status) && optionalText(value.source_published_at)
    && (value.condition_match == null || (object(value.condition_match) && ["match", "mismatch", "unknown"].includes(String(value.condition_match.status)) && typeof value.condition_match.reason === "string" && Array.isArray(value.condition_match.details) && value.condition_match.details.every((detail) => object(detail) && typeof detail.key === "string" && typeof detail.reason === "string")))
    && (value.conditions == null || object(value.conditions)) && stringArray(value.exceptions)
    && (value.parameters == null || (Array.isArray(value.parameters) && value.parameters.every((p) => object(p) && typeof p.name === "string" && ["string", "number"].includes(typeof p.value) && optionalText(p.unit))))
    && (value.prices == null || (Array.isArray(value.prices) && value.prices.every((p) => object(p) && typeof p.object === "string" && typeof p.amount === "number" && ["unit", "price_kind", "basis", "brand", "model", "spec", "region", "valid_at"].every((key) => optionalText(p[key])) && stringArray(p.includes))));
}

function validResponse(value: unknown): value is AskResponse {
  return object(value) && typeof value.answer === "string" && optionalText(value.generated_at) && (value.kb_revision == null || ["number", "string"].includes(typeof value.kb_revision))
    && Array.isArray(value.refs) && value.refs.every((ref) => object(ref) && typeof ref.n === "number" && typeof ref.claim === "string" && typeof ref.atom_id === "string" && optionalText(ref.video_title) && optionalText(ref.video) && validKnowledge(ref))
    && Array.isArray(value.conflicts) && value.conflicts.every((c) => object(c) && typeof c.topic === "string");
}
function validCompare(value: unknown): value is CompareResponse {
  return object(value) && Array.isArray(value.conflicts) && value.conflicts.every((conflict) => object(conflict) && optionalText(conflict.topic)) && stringArray(value.dimensions) && Array.isArray(value.sides) && value.sides.every((side) => object(side) && typeof side.item === "string" && typeof side.total === "number" && Array.isArray(side.missing_dimensions) && stringArray(side.missing_dimensions) && Array.isArray(side.groups) && side.groups.every((group) => object(group) && typeof group.dimension === "string" && Array.isArray(group.atoms) && group.atoms.every((row) => object(row) && typeof row.id === "string" && typeof row.claim === "string" && optionalText(row.video_title) && optionalText(row.video) && optionalText(row.space) && validKnowledge(row))));
}
function load(kind: "ask" | "compare"): AskSession | CompareSession | undefined {
  try {
    const raw = sessionStorage.getItem(`${namespace}:${kind}`);
    if (!raw) return;
    if (new TextEncoder().encode(raw).length > READING_MAX_BYTES) throw new Error("oversized");
    const saved: unknown = JSON.parse(raw);
    if (!object(saved) || saved.schema !== READING_SCHEMA || typeof saved.updated_at !== "number") throw new Error("schema");
    if (Date.now() - saved.updated_at > READING_TTL || saved.updated_at > Date.now() + 60_000) { sessionStorage.removeItem(`${namespace}:${kind}`); return; }
    const session = saved.session;
    if (!object(session) || typeof session.session_id !== "string" || typeof session.generation !== "number" || typeof session.draft !== "string") throw new Error("session");
    if (!optionalText(session.notice) || !optionalText(session.error) || (session.scroll != null && (!object(session.scroll) || typeof session.scroll.top !== "number" || !Number.isFinite(session.scroll.top)))) throw new Error("metadata");
    if (kind === "ask") {
      if (!Array.isArray(session.turns) || !session.turns.every((turn) => object(turn) && typeof turn.id === "string" && (turn.role === "user" || turn.role === "assistant") && typeof turn.content === "string" && (!turn.response || validResponse(turn.response)))) throw new Error("turns");
      if (!["idle", "submitting", "answered", "degraded", "failed", "interrupted"].includes(String(session.status))) throw new Error("status");
      if (session.expandedRefs != null && (!object(session.expandedRefs) || !Object.values(session.expandedRefs).every((value) => typeof value === "boolean"))) throw new Error("expanded");
      return { ...session, expandedRefs: session.expandedRefs ?? {}, status: session.status === "submitting" ? "interrupted" : session.status, request_id: undefined } as unknown as AskSession;
    }
    if (typeof session.items !== "string" || !object(session.expanded) || !Object.values(session.expanded).every((value) => typeof value === "boolean") || (session.result && !validCompare(session.result)) || !["editing", "loading", "ready", "empty", "failed"].includes(String(session.status))) throw new Error("compare");
    return { ...session, perDimensionLimit: typeof session.perDimensionLimit === "number" ? Math.min(100, Math.max(5, session.perDimensionLimit)) : 5, status: session.status === "loading" ? "editing" : session.status, request_id: undefined, notice: session.status === "loading" ? "上次对比已中断，请手动重新提交。" : session.notice } as unknown as CompareSession;
  } catch {
    try { sessionStorage.removeItem(`${namespace}:${kind}`); } catch { /* disabled */ }
    state = { ...state, storageNotice: memoryWarning };
  }
}

/** Call only with an identity obtained from the current backend. Never persist
 * anonymous reads under a shared key, and never store access tokens here. */
export function initializeReadingNamespace(libraryId: string) {
  if (!libraryId.trim()) return false;
  const next = `${PREFIX}${encodeURIComponent(window.location.origin)}:${encodeURIComponent(libraryId)}`;
  if (namespace === next) return false;
  askController?.abort(); compareController?.abort();
  const changed = namespace !== undefined;
  if (changed) {
    try { sessionStorage.removeItem(`${namespace}:ask`); sessionStorage.removeItem(`${namespace}:compare`); sessionStorage.removeItem(`${next}:ask`); sessionStorage.removeItem(`${next}:compare`); } catch { /* disabled */ }
  }
  namespace = next;
  state = { ask: freshAsk(), compare: freshCompare() };
  const ask = load("ask") as AskSession | undefined;
  const compare = load("compare") as CompareSession | undefined;
  state = bounded({ ...state, ask: ask ?? state.ask, compare: compare ?? state.compare });
  persist(); emit();
  return changed;
}
export function readingNamespaceUnavailable() { state = { ...state, storageNotice: "尚未确认知识库身份，当前仅保存在内存；刷新后可能无法恢复。" }; emit(); }

/** Auth/server changes are an identity boundary, including other-tab changes. */
export function resetReadingSessions() {
  askController?.abort(); compareController?.abort();
  try {
    for (let i = sessionStorage.length - 1; i >= 0; i--) {
      const key = sessionStorage.key(i);
      if (key?.startsWith("reno-reading:")) sessionStorage.removeItem(key);
    }
  } catch { /* private mode: memory is still reset */ }
  namespace = undefined;
  state = { ask: freshAsk(state.ask.generation + 1), compare: freshCompare(state.compare.generation + 1) };
  emit();
}
export const getReadingState = () => state;
/** Navigation can reject fetch with TypeError before the old document dies.
 * Invalidate the request synchronously before unload so that rejection cannot
 * overwrite the intended interrupted snapshot with a misleading failure. */
export function interruptReadingRequests() {
  const ask = state.ask.status === "submitting" ? { ...state.ask, status: "interrupted" as const, request_id: undefined, error: undefined } : state.ask;
  const compare = state.compare.status === "loading" ? { ...state.compare, status: "editing" as const, request_id: undefined, notice: "上次对比已中断，请手动重新提交。" } : state.compare;
  if (ask === state.ask && compare === state.compare) return;
  update({ ...state, ask, compare });
  askController?.abort(); compareController?.abort();
}
export function useReadingSession() { return useSyncExternalStore((listener) => { listeners.add(listener); return () => listeners.delete(listener); }, getReadingState); }
export function patchAsk(patch: Partial<Pick<AskSession, "draft" | "scroll" | "expandedRefs" | "context">>) { update({ ...state, ask: { ...state.ask, ...patch } }); }
export function patchCompare(patch: Partial<Pick<CompareSession, "draft" | "scroll" | "expanded" | "context">>) { update({ ...state, compare: { ...state.compare, ...patch } }); }
export function clearAskSession() { askController?.abort(); update({ ...state, ask: freshAsk(state.ask.generation + 1) }); }
export function clearCompareSession() { compareController?.abort(); update({ ...state, compare: freshCompare(state.compare.generation + 1) }); }

export async function submitAsk(text?: string, retry = false) {
  const initial = state.ask;
  const question = (text ?? initial.draft).trim();
  if (!question || initial.status === "submitting") return;
  const requestId = uid();
  const controller = new AbortController(); askController = controller;
  const turns = retry && initial.turns.at(-1)?.role === "user" ? initial.turns.slice(0, -1) : initial.turns;
  const history: AskHistoryTurn[] = turns.slice(-4).map(({ role, content }) => ({ role, content }));
  const context = initial.context ? structuredClone(initial.context) : undefined;
  update({ ...state, ask: { ...initial, draft: "", error: undefined, status: "submitting", request_id: requestId, turns: [...turns, { id: uid(), role: "user", content: question }] } });
  const current = () => state.ask.session_id === initial.session_id && state.ask.generation === initial.generation && state.ask.request_id === requestId;
  try {
    const result = await postAsk(question, history, controller.signal, context);
    if (!current()) return;
    update({ ...state, ask: { ...state.ask, status: result.degraded ? "degraded" : "answered", request_id: undefined, turns: [...state.ask.turns, { id: uid(), role: "assistant", content: result.answer, response: result, context }] } });
  } catch (error) {
    if (!current()) return;
    update({ ...state, ask: { ...state.ask, status: controller.signal.aborted ? "interrupted" : "failed", request_id: undefined, error: error instanceof Error ? error.message : String(error) } });
  }
}
export async function submitCompare(text?: string, perDimensionLimit = 5, preserveResult = false) {
  const initial = state.compare;
  const parts = (text ?? initial.draft).split(/[,，/ ]+/).map((s) => s.trim()).filter(Boolean).slice(0, 3);
  if (parts.length < 2) return;
  compareController?.abort();
  const requestId = uid();
  const controller = new AbortController(); compareController = controller;
  const items = parts.join(",");
  const context = preserveResult ? initial.resultContext : initial.context ? structuredClone(initial.context) : undefined;
  update({ ...state, compare: { ...initial, items, perDimensionLimit, expanded: preserveResult ? initial.expanded : {}, result: preserveResult ? initial.result : undefined, error: undefined, status: "loading", request_id: requestId, scroll: preserveResult ? initial.scroll : undefined } });
  const current = () => state.compare.session_id === initial.session_id && state.compare.generation === initial.generation && state.compare.request_id === requestId;
  try {
    const result = await getCompare(items, controller.signal, { perDimensionLimit, context: context?.context_snapshot, project_id: context?.project_id, context_revision: context?.context_revision });
    if (current()) update({ ...state, compare: { ...state.compare, status: result.sides.some((side) => side.total > 0) ? "ready" : "empty", result, resultContext: context, request_id: undefined } });
  } catch (error) {
    if (current()) update({ ...state, compare: { ...state.compare, status: "failed", request_id: undefined, error: error instanceof Error ? error.message : String(error) } });
  }
}
export function expandCompare() { return submitCompare(state.compare.items, Math.min(100, state.compare.perDimensionLimit + 20), true); }
