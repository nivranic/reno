import { useCallback, useRef, useState, useSyncExternalStore, type SetStateAction } from "react";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";

type DraftEntry = { scope: string; key: string; label: string; text: string; value: unknown };
type DraftRegistry = { drafts: Map<string, DraftEntry>; requests: Map<string, unknown>; revision: number; listeners: Set<() => void> };
const registries = new WeakMap<QueryClient, DraftRegistry>();
const identity = (scope: string, key: string) => JSON.stringify([scope, key]);
function registryFor(client: QueryClient) {
  let registry = registries.get(client);
  if (!registry) { registry = { drafts: new Map(), requests: new Map(), revision: 0, listeners: new Set() }; registries.set(client, registry); }
  return registry;
}
function publish(registry: DraftRegistry) { registry.revision++; registry.listeners.forEach((listener) => listener()); }
function requireScope(scope: string, key: string) {
  if (!scope || scope === "unavailable" || !key) throw new Error("草稿需要原编辑范围和稳定记录标识");
}

/** Only client-entered drafts and original requests live here. This is neither
 * an authorization cache nor a replacement for Query's server records. Values,
 * including File objects, stay in this QueryClient's memory and never serialize
 * into localStorage, exports, or a different owner's request. */
export function rememberPersonalRequest<T>(client: QueryClient, scope: string, key: string, envelope: T) {
  requireScope(scope, key);
  const registry = registryFor(client); registry.requests.set(identity(scope, key), envelope); publish(registry);
}
export function readPersonalRequest<T>(client: QueryClient, scope: string, key: string): T | undefined {
  return registryFor(client).requests.get(identity(scope, key)) as T | undefined;
}
export function forgetPersonalRequest(client: QueryClient, scope: string, key: string) {
  const registry = registryFor(client); if (registry.requests.delete(identity(scope, key))) publish(registry);
}

export function usePersonalDraft<T>({ scope, key, label, initial, describe }: {
  scope: string; key: string; label: string; initial: () => T; describe: (value: T) => string;
}) {
  const client = useQueryClient(); const registry = registryFor(client); const id = identity(scope, key);
  const makeValue = () => registry.drafts.has(id) ? registry.drafts.get(id)!.value as T : initial();
  const [state, setState] = useState(() => ({ id, value: makeValue() }));
  let current = state;
  if (state.id !== id) { current = { id, value: makeValue() }; setState(current); }
  const latest = useRef(current.value); latest.current = current.value;
  const setDraft = (action: SetStateAction<T>) => {
    requireScope(scope, key);
    const next = typeof action === "function" ? (action as (previous: T) => T)(latest.current) : action;
    latest.current = next;
    registry.drafts.set(id, { scope, key, label, value: next, text: describe(next) });
    setState({ id, value: next }); publish(registry);
  };
  const clear = () => {
    // Successful saves clear just their original edit. Unknown requests remain
    // owned by the receipt hook and cannot be discarded as a side effect here.
    if (registry.drafts.delete(id)) publish(registry);
  };
  return { draft: current.value, setDraft, clear, retained: registry.drafts.has(id),
    notice: "未提交内容仅在本次页面内暂存；权限变化后可从“暂存草稿”查看。刷新或关闭页面会丢失。" };
}

export function usePersonalDraftArchive() {
  const client = useQueryClient(); const registry = registryFor(client);
  const subscribe = useCallback((listener: () => void) => { registry.listeners.add(listener); return () => { registry.listeners.delete(listener); }; }, [registry]);
  useSyncExternalStore(subscribe, () => registry.revision);
  return {
    drafts: Array.from(registry.drafts, ([id, entry]) => ({ id, label: entry.label, text: entry.text,
      pending: registry.requests.has(id) })),
    pendingCount: registry.requests.size,
  };
}
