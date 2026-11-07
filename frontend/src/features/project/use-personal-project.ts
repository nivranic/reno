import { useCallback, useEffect, useSyncExternalStore } from "react";
import { useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { defaultProjectFor, personalCapabilitiesQuery, personalKeys, type PersonalCapabilities, type PersonalRead } from "@/lib/personal-api";

type AccessSession = { scope: string; target: string; generation: number; listeners: Set<() => void>; transfers: Set<AbortController>; owners: number; everConfirmed: boolean; rechecking?: boolean; detach?: () => void };
const sessions = new WeakMap<QueryClient, AccessSession>();
function sessionFor(client: QueryClient) {
  let session = sessions.get(client);
  if (!session) { session = { scope: "", target: "", generation: 0, listeners: new Set(), transfers: new Set(), owners: 0, everConfirmed: false }; sessions.set(client, session); }
  return session;
}
function publish(session: AccessSession) { session.listeners.forEach((listener) => listener()); }
function abortTransfers(session: AccessSession) { session.transfers.forEach((controller) => controller.abort()); session.transfers.clear(); }
const personalReads = { predicate: (query: { meta?: Record<string, unknown> }) => query.meta?.personalRead === true };

/** Scope changes are coordinated per QueryClient. No new owner is admitted until
 * cancellation and removal of the previous owner's personal data has finished. */
function moveScope(client: QueryClient, session: AccessSession, target: string) {
  if (session.target === target && session.scope === target) return;
  if (session.target === target && session.scope === "") return;
  // Standalone historical home callers have no scoped capability observer. Keep
  // that first local cache admission compatible; it is never kept for a mirror
  // or after a previously confirmed owner/permission changes.
  const legacyLocal = !session.everConfirmed && !session.scope && !session.target && target.startsWith('["local",');
  const obsolete = { predicate: (query: { meta?: Record<string, unknown>; queryKey: readonly unknown[] }) => query.meta?.personalRead === true && !(legacyLocal && query.queryKey.at(-1) === "legacy-local") };
  session.target = target; session.scope = ""; const generation = ++session.generation;
  abortTransfers(session); publish(session);
  void client.cancelQueries(obsolete).then(() => {
    if (session.generation !== generation) return;
    client.removeQueries({ predicate: (query) => obsolete.predicate(query) && query.queryKey.at(-1) !== target });
    session.scope = target; if (target) session.everConfirmed = true; publish(session);
  });
}

export function registerPersonalTransfer(client: QueryClient, read: PersonalRead, controller: AbortController) {
  const session = sessionFor(client);
  if (session.rechecking || session.scope !== read.scope || confirmedScope(client) !== read.scope) { controller.abort(); return () => {}; }
  session.transfers.add(controller);
  return () => session.transfers.delete(controller);
}

/** A response belongs to the exact admission that sent it, even when a later
 * capability refresh happens to return the same scope text. Releasing a
 * completed request only unregisters its controller; it does not admit it. */
export function registerPersonalMutation(client: QueryClient, read: PersonalRead, controller: AbortController) {
  const session = sessionFor(client); const generation = session.generation;
  if (read.mode !== "local" || session.rechecking || session.scope !== read.scope || session.target !== read.scope
      || confirmedScope(client) !== read.scope) controller.abort();
  else session.transfers.add(controller);
  return {
    isCurrent: () => !controller.signal.aborted && !session.rechecking && session.generation === generation
      && session.scope === read.scope && session.target === read.scope && confirmedScope(client) === read.scope,
    release: () => { session.transfers.delete(controller); },
  };
}

function confirmedRead(capability: PersonalCapabilities): PersonalRead | undefined {
  if (!capability.personal_read || capability.personal_mode === "disabled") return;
  const mirror = capability.personal_mode === "mirror" || capability.node_role === "cloud";
  if (mirror && (capability.personal_mode !== "mirror" || !/^[0-9a-f]{64}$/.test(capability.personal_read_version ?? "") || !capability.personal_mirror)) return;
  const mode = mirror ? "mirror" : "local";
  const scope = JSON.stringify([mode, capability.library_id, capability.primary_epoch,
    capability.personal_primary_id ?? "", capability.instance_id ?? "", capability.personal_read_version ?? null,
    capability.personal_mirror ?? null, capability.personal_write, capability.personal_upload,
    capability.field_checks_read ?? capability.field_checks ?? false, capability.field_checks_write ?? capability.field_checks ?? false]);
  return { mode, version: capability.personal_read_version ?? null, scope };
}

function confirmedScope(client: QueryClient) {
  const state = client.getQueryState<PersonalCapabilities>(personalKeys.capabilities);
  return state?.status === "success" && state.data ? confirmedRead(state.data)?.scope ?? "" : "";
}

export function usePersonalProject() {
  const client = useQueryClient(); const session = sessionFor(client);
  const subscribe = useCallback((listener: () => void) => { session.listeners.add(listener); return () => { session.listeners.delete(listener); }; }, [session]);
  const settledScope = useSyncExternalStore(subscribe, () => session.scope);
  const capabilities = useQuery({ ...personalCapabilitiesQuery, refetchOnWindowFocus: "always" as const,
    refetchInterval: 30_000, refetchIntervalInBackground: false });
  // A refetch error can retain cached data. Only the query's successful state is
  // authorization; stale data from an error/401 is never a capability grant.
  const proposed = capabilities.isSuccess ? confirmedRead(capabilities.data) : undefined;
  const desiredScope = proposed?.scope ?? "";
  // Read Query's current state, not a previous render's proposed grant. Query
  // cache notifications are synchronous; React observer updates are deferred.
  useEffect(() => { moveScope(client, session, confirmedScope(client)); }, [client, session, desiredScope]);
  useEffect(() => {
    session.owners++;
    if (!session.detach) {
      const stopWatching = client.getQueryCache().subscribe((event) => {
        if ((event.type === "updated" || event.type === "removed")
            && event.query.queryKey.length === personalKeys.capabilities.length
            && event.query.queryKey.every((part: unknown, index: number) => part === personalKeys.capabilities[index])) {
          moveScope(client, session, confirmedScope(client));
        }
      });
      const recheck = () => {
        if (session.rechecking) return;
        session.rechecking = true;
        session.generation++; session.target = ""; session.scope = ""; abortTransfers(session); publish(session);
        void client.cancelQueries(personalReads).then(() => client.removeQueries({ predicate: (query) => query.meta?.personalRead === true && query.queryKey.at(-1) !== session.target }));
        // reset clears the cached grant synchronously, then refetches the active
        // capabilities query with the current token and installed mirror proof.
        void client.resetQueries({ queryKey: personalKeys.capabilities, exact: true }).finally(() => { session.rechecking = false; });
      };
      for (const name of ["reno-personal-version-changed", "reno-token-changed", "reno-unauthorized"]) window.addEventListener(name, recheck);
      session.detach = () => {
        stopWatching();
        for (const name of ["reno-personal-version-changed", "reno-token-changed", "reno-unauthorized"]) window.removeEventListener(name, recheck);
      };
    }
    return () => { if (--session.owners === 0) { session.detach?.(); session.detach = undefined; abortTransfers(session); } };
  }, [client, session]);
  const readable = !!proposed && settledScope === desiredScope;
  const read = readable ? proposed : undefined;
  const projectQuery = useQuery({ ...defaultProjectFor(read), enabled: readable });
  const project = { ...projectQuery, data: readable ? projectQuery.data : undefined };
  const local = readable && read?.mode === "local" && capabilities.data?.node_role !== "cloud";
  const writable = local && capabilities.data?.personal_write === true && !!project.data?.project;
  const fieldReadable = readable && (capabilities.data?.field_checks_read ?? capabilities.data?.field_checks) === true;
  const fieldWritable = writable && (capabilities.data?.field_checks_write ?? capabilities.data?.field_checks) === true;
  return { capabilities, project, read, readable, scope: readable ? desiredScope : "unavailable", writable, fieldReadable, fieldWritable,
    uploadable: writable && capabilities.data?.personal_upload === true,
    reason: capabilities.data?.personal_reason || project.data?.reason || "本实例暂未开放个人记录，可继续使用知识功能。" };
}
