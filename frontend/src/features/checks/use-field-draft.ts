import { useEffect, useRef, useState } from "react";
import type { CheckInput, MutationEnvelope } from "@/lib/personal-api";
import { usePersonalDraft } from "@/features/project/use-personal-draft";

export interface FieldDraft {
  baseRevision: number; criteriaHash: string; kind: "inspection" | "remediation" | "void";
  result: "pass" | "fail" | "unable_to_verify" | "not_applicable"; note: string;
  observations: Record<string, string>; checkedAt: string; attachments: string[]; sourceId: string;
  filePending?: string; voidTarget?: string; pendingRequest?: MutationEnvelope<CheckInput>;
}
export function useFieldDraft(libraryId: string, itemId: string, initial: FieldDraft, ownerEpoch = 1, ownerScope = "", describe?: (value: FieldDraft) => string) {
  const legacyKey = `reno-reading:v1:${encodeURIComponent(window.location.origin)}:${encodeURIComponent(libraryId)}:field:${itemId}`;
  const key = `${legacyKey}:owner:${encodeURIComponent(ownerScope)}`;
  const readSaved = () => {
    let warning = "";
    try {
      if (!ownerScope || ownerScope === "unavailable") return { key, draft: initial, saved: false, warning };
      let raw = sessionStorage.getItem(key);
      if (!raw) {
        const legacy = sessionStorage.getItem(ownerEpoch === 1 ? legacyKey : `${legacyKey}:primary:${ownerEpoch}`) ?? sessionStorage.getItem(legacyKey);
        if (legacy && legacy.length <= 300_000) {
          const value = JSON.parse(legacy) as { owner_scope?: string };
          // An epoch or library alone cannot identify the previous primary.
          // Keep old unscoped bytes; only explicit same-owner proof may load.
          if (value.owner_scope === ownerScope) raw = legacy;
          else warning = "旧暂存草稿缺少完整归属证明，未自动载入；原暂存内容保持，请在原实例核对。";
        }
      }
      if (!raw || raw.length > 300_000) return { key, draft: initial, saved: false, warning };
      const value = JSON.parse(raw) as { updated_at: number; owner_scope: string; cleared?: boolean; draft?: FieldDraft };
      if (value.owner_scope !== ownerScope || value.cleared) return { key, draft: initial, saved: false, warning };
      const draft = value.draft;
      if (!Number.isFinite(value.updated_at) || Date.now() - value.updated_at > 24 * 60 * 60 * 1000 || typeof draft?.note !== "string" || !Array.isArray(draft.attachments) || !draft.attachments.every((id) => typeof id === "string") || !draft.observations || typeof draft.observations !== "object" || Array.isArray(draft.observations) || !Object.values(draft.observations).every((value) => typeof value === "string")) return { key, draft: initial, saved: false, warning };
      if (!Number.isInteger(draft.baseRevision) || typeof draft.criteriaHash !== "string" || typeof draft.sourceId !== "string" || typeof draft.checkedAt !== "string" || (draft.checkedAt && !Number.isFinite(new Date(draft.checkedAt).getTime())) || !["inspection", "remediation", "void"].includes(draft.kind) || !["pass", "fail", "unable_to_verify", "not_applicable"].includes(draft.result)) return { key, draft: initial, saved: false, warning };
      if (draft.pendingRequest && (typeof draft.pendingRequest.operation_id !== "string" || !draft.pendingRequest.changes || typeof draft.pendingRequest.changes !== "object" || draft.pendingRequest.primary_epoch !== ownerEpoch)) return { key, draft: initial, saved: false, warning };
      return { key, draft: { ...initial, ...draft }, saved: true, warning };
    } catch { return { key, draft: initial, saved: false, warning }; }
  };
  const [saved, setSaved] = useState(readSaved);
  let restored = saved;
  if (saved.key !== key) { restored = readSaved(); setSaved(restored); }
  const [storageNotice, setStorageNotice] = useState("");
  const personal = usePersonalDraft({ scope: ownerScope, key: `check:${itemId}:record`, label: "现场核对记录", initial: () => restored.draft,
    describe: describe ?? ((draft) => `记录说明：${draft.note}\n观察值：${Object.values(draft.observations).join("；")}\n观察时间：${draft.checkedAt}\n附件：${draft.attachments.length} 份\n待上传文件：${draft.filePending ?? ""}`) });
  const promoted = useRef("");
  useEffect(() => {
    if (restored.saved && promoted.current !== key) { promoted.current = key; if (!personal.retained) personal.setDraft(restored.draft); }
  }, [key, restored, personal.retained, personal.setDraft]);
  const write = (next: FieldDraft) => {
    personal.setDraft(next);
    try {
      const raw = JSON.stringify({ updated_at: Date.now(), owner_scope: ownerScope, draft: next });
      if (new TextEncoder().encode(raw).length > 512 * 1024) throw new Error("large");
      sessionStorage.setItem(key, raw);
    } catch { setStorageNotice("浏览器暂存不可用，当前草稿仅在内存；刷新可能丢失，请先确认提交结果。"); }
  };
  return { draft: personal.draft, notice: storageNotice || restored.warning || personal.notice, write,
    patch: (next: Partial<FieldDraft>) => write({ ...personal.draft, ...next }),
    clear: () => {
      personal.setDraft(initial); personal.clear();
      try { sessionStorage.setItem(key, JSON.stringify({ updated_at: Date.now(), owner_scope: ownerScope, cleared: true })); }
      catch { setStorageNotice("浏览器暂存暂时无法清除；本次已收到回执，请勿重复提交。"); }
    } };
}
