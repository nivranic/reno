import { useCallback, useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ApiError } from "@/lib/api";
import { getOperation, type MutationEnvelope } from "@/lib/personal-api";
import { Button } from "@/components/ui/button";
import { newOperationId } from "@/lib/id";
import { registerPersonalMutation, usePersonalProject } from "./use-personal-project";
import { forgetPersonalRequest, readPersonalRequest, rememberPersonalRequest } from "./use-personal-draft";

type RequestOwner = { scope: string; epoch: number; owner?: string; draftKey?: string };
type OwnedRequest<C> = { envelope: MutationEnvelope<C>; owner: RequestOwner };
type Flight<C> = { request: OwnedRequest<C>; controller: AbortController;
  registration: ReturnType<typeof registerPersonalMutation> };

/** Copy editor values before assigning an operation ID. Files stay in the
 * current page's memory; no JSON conversion can recover their original bytes. */
function immutableValue<T>(value: T, parents = new Set<object>()): T {
  if (!value || typeof value !== "object" || value instanceof Blob) return value;
  if (!Array.isArray(value) && ![Object.prototype, null].includes(Object.getPrototypeOf(value))) {
    throw new TypeError("保存内容只能包含普通记录、数组或原文件");
  }
  if (parents.has(value)) throw new TypeError("保存内容不能包含循环引用");
  parents.add(value);
  try {
    const copy = Array.isArray(value) ? value.map((item) => immutableValue(item, parents)) :
      Object.fromEntries(Object.entries(value).map(([key, item]) => [key, immutableValue(item, parents)]));
    return Object.freeze(copy) as T;
  } finally { parents.delete(value); }
}

function immutableRequest<C>(envelope: MutationEnvelope<C>): MutationEnvelope<C> {
  return Object.freeze({ ...envelope, changes: immutableValue(envelope.changes) });
}

function cancelled() { return new DOMException("原个人记录权限已失效，原请求仍保留", "AbortError"); }

/** A transport failure is not proof that the write failed. Keep the original
 * immutable envelope for receipt lookup and retry; never silently change IDs. */
export function useReceiptMutation<T, C>(send: (request: MutationEnvelope<C>, signal?: AbortSignal) => Promise<T>, onSuccess: (result: T) => void, recovery?: { initialRequest?: MutationEnvelope<C> | null; onRequest?: (request: MutationEnvelope<C>) => void; onRejected?: () => void; enabled?: boolean; owner?: string; draftKey?: string }) {
  const client = useQueryClient(); const access = usePersonalProject();
  const active = useRef(true); const request = useRef<OwnedRequest<C> | null>(null);
  const initialRequest = useRef(recovery?.initialRequest ?? null);
  const initialScope = useRef(access.read?.scope ?? recovery?.owner);
  const initialConsumed = useRef(false);
  const current = useRef({ access, recovery }); current.current = { access, recovery };
  const flights = useRef(new Set<Flight<C>>());
  const running = useRef<Flight<C> | null>(null);
  const [unknown, setUnknown] = useState(!!recovery?.initialRequest);
  const [receiptMessage, setReceiptMessage] = useState("");

  const isOwner = useCallback((owned: OwnedRequest<C>) => {
    const { access: latest, recovery: limits } = current.current;
    return active.current && latest.writable && latest.read?.mode === "local"
      && limits?.enabled !== false && latest.read.scope === owned.owner.scope
      && latest.capabilities.data?.primary_epoch === owned.owner.epoch
      && limits?.owner === owned.owner.owner && limits?.draftKey === owned.owner.draftKey;
  }, []);
  const deliverable = (flight?: Flight<C>) => !!flight && request.current === flight.request
    && isOwner(flight.request) && flight.registration.isCurrent();
  const finish = useCallback((flight?: Flight<C>) => {
    if (!flight) return;
    flight.registration.release(); flights.current.delete(flight);
    if (running.current === flight) running.current = null;
  }, []);
  const forget = (owned: OwnedRequest<C>) => {
    if (owned.owner.draftKey
        && readPersonalRequest<MutationEnvelope<C>>(client, owned.owner.scope, owned.owner.draftKey)?.operation_id === owned.envelope.operation_id) {
      forgetPersonalRequest(client, owned.owner.scope, owned.owner.draftKey);
    }
    request.current = null; setUnknown(false); setReceiptMessage("");
  };

  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
      for (const flight of flights.current) { flight.controller.abort(); flight.registration.release(); }
      flights.current.clear();
    };
  }, []);

  // Restore only the exact current scope's request slot. A new scope can never
  // adopt a previous owner's pending UUID, even when it renders the same form.
  const scope = access.read?.scope;
  const confirmedEpoch = access.capabilities.data?.primary_epoch;
  const draftKey = recovery?.draftKey; const owner = recovery?.owner; const enabled = recovery?.enabled;
  useEffect(() => {
    for (const flight of flights.current) {
      if (!isOwner(flight.request) || !flight.registration.isCurrent()) {
        flight.controller.abort(); finish(flight);
      }
    }
  }, [scope, access.writable, confirmedEpoch, draftKey, owner, enabled, finish, isOwner]);
  useEffect(() => {
    if (!scope || !access.writable || enabled === false || confirmedEpoch === undefined) return;
    if (request.current && isOwner(request.current)) return;
    if (request.current?.owner.scope === scope && request.current.owner.draftKey === draftKey
        && request.current.owner.owner !== owner) {
      setUnknown(true); setReceiptMessage("原请求不属于当前编辑对象，不能转移或重发。"); return;
    }
    const epoch = confirmedEpoch;
    const saved = draftKey ? readPersonalRequest<MutationEnvelope<C>>(client, scope, draftKey) : undefined;
    const supplied = initialConsumed.current ? null : initialRequest.current;
    initialConsumed.current = true;
    const envelope = saved ?? supplied;
    request.current = envelope && (saved || initialScope.current === scope) && envelope.primary_epoch === epoch ? {
      envelope: immutableRequest(envelope), owner: { scope, epoch, owner, draftKey },
    } : null;
    if (request.current && draftKey) rememberPersonalRequest(client, scope, draftKey, request.current.envelope);
    setUnknown(!!envelope); setReceiptMessage(envelope && !request.current ? "原请求未绑定当前主实例和任期，不能重发。" : "");
  }, [client, scope, access.writable, confirmedEpoch, draftKey, owner, enabled, isOwner]);

  const begin = (owned: OwnedRequest<C>) => {
    if (!isOwner(owned) || running.current?.registration.isCurrent()) return;
    const read = current.current.access.read!;
    const controller = new AbortController(); const registration = registerPersonalMutation(client, read, controller);
    if (!registration.isCurrent()) { registration.release(); controller.abort(); return; }
    const flight = { request: owned, controller, registration };
    running.current = flight; flights.current.add(flight);
    return flight;
  };

  // Each attempt has its own identity. Reusing the same envelope after an
  // abort must not let the old attempt settle or release the newer attempt.
  const mutation = useMutation({ mutationFn: async (flight: Flight<C>) => {
    if (!deliverable(flight)) throw cancelled();
    const result = await send(flight.request.envelope, flight.controller.signal);
    if (!deliverable(flight)) throw cancelled();
    return result;
  }, retry: false, networkMode: "always",
  onSuccess: (result, flight) => {
    if (!deliverable(flight)) return;
    forget(flight.request); onSuccess(result);
  }, onError: (error, flight) => {
    if (!deliverable(flight) || error instanceof DOMException && error.name === "AbortError") return;
    const ambiguous = error instanceof ApiError ? error.status === 0 || error.status >= 500 : true;
    setUnknown(ambiguous);
    if (!ambiguous) { forget(flight.request); current.current.recovery?.onRejected?.(); }
  }, onSettled: (_result, _error, flight) => { finish(flight); } });

  const receipt = useMutation({ mutationFn: async (flight: Flight<C>) => {
    if (!deliverable(flight)) throw cancelled();
    const result = await getOperation<T>(flight.request.envelope.operation_id, flight.controller.signal);
    if (!deliverable(flight)) throw cancelled();
    return result;
  }, retry: false, networkMode: "always",
  onSuccess: (result, flight) => {
    if (!deliverable(flight)) return;
    forget(flight.request); mutation.reset(); onSuccess(result.response);
  }, onError: (error, flight) => {
    if (!deliverable(flight)) return;
    setReceiptMessage(error instanceof ApiError && error.status === 404 ? "尚未查到回执，原请求仍保留。可用同一请求重试，不会重复创建。" : "暂时无法确认是否提交，请保留本页并稍后查询。");
  }, onSettled: (_result, _error, flight) => { finish(flight); } });

  const sendOwned = (owned: OwnedRequest<C>) => {
    const flight = begin(owned); if (!flight) return;
    setUnknown(true);
    mutation.mutate(flight);
  };
  return { ...mutation, unknown, receiptMessage, checking: receipt.isPending,
    submit: (changes: C, primaryEpoch: number, baseRevision?: number) => {
      const { access: latest, recovery: limits } = current.current;
      if (!latest.read || !latest.writable || limits?.enabled === false || unknown || request.current
          || primaryEpoch !== latest.capabilities.data?.primary_epoch || running.current?.registration.isCurrent()) return;
      const retained = limits?.draftKey ? readPersonalRequest<MutationEnvelope<C>>(client, latest.read.scope, limits.draftKey) : undefined;
      if (retained) {
        request.current = retained.primary_epoch === primaryEpoch ? {
          envelope: immutableRequest(retained), owner: { scope: latest.read.scope, epoch: primaryEpoch, owner: limits?.owner, draftKey: limits?.draftKey },
        } : null;
        setUnknown(true); return;
      }
      const envelope = immutableRequest({ operation_id: newOperationId(), primary_epoch: primaryEpoch, changes,
        ...(baseRevision == null ? {} : { base_revision: baseRevision }) });
      const owned = { envelope, owner: { scope: latest.read.scope, epoch: primaryEpoch, owner: limits?.owner, draftKey: limits?.draftKey } };
      const flight = begin(owned); if (!flight) return;
      try {
        if (limits?.draftKey) rememberPersonalRequest(client, owned.owner.scope, limits.draftKey, envelope);
        request.current = owned; limits?.onRequest?.(envelope); setUnknown(true); setReceiptMessage("");
        mutation.mutate(flight);
      } catch (error) { finish(flight); throw error; }
    }, retrySame: () => { if (request.current) sendOwned(request.current); },
    checkReceipt: () => {
      if (!request.current) return;
      const flight = begin(request.current); if (!flight) return;
      receipt.mutate(flight);
    },
  };
}

export function MutationNotice({ mutation }: { mutation: { error: Error | null; unknown: boolean; receiptMessage: string; checking: boolean; isPending: boolean; checkReceipt: () => void; retrySame: () => void } }) {
  if (!mutation.error && !mutation.unknown) return null;
  const code = mutation.error instanceof ApiError ? mutation.error.code : undefined;
  return <div role="alert" className="space-y-2 rounded-ctl border border-st-wait-line bg-st-wait-bg p-3 text-[12.5px] text-st-wait">
    <p>{mutation.unknown ? "尚未收到保存回执，无法确认是否提交；草稿仍保留，请勿重复新建。" : code === "REVISION_CONFLICT" ? "记录已被修改。本次草稿仍保留，请查看最新版本后再决定如何保存。" : mutation.error?.message}</p>
    {mutation.receiptMessage && <p>{mutation.receiptMessage}</p>}
    {mutation.unknown && <div className="flex flex-wrap gap-2"><Button size="sm" variant="outline" disabled={mutation.checking || mutation.isPending} onClick={mutation.checkReceipt}>查询提交结果</Button><Button size="sm" variant="outline" disabled={mutation.isPending} onClick={mutation.retrySame}>使用同一请求重试</Button></div>}
  </div>;
}
