import { useState, type SetStateAction } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { BookmarkPlus } from "lucide-react";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Field, Input, Select, Textarea } from "@/components/ui/field";
import { useToast } from "@/components/shared/toaster";
import { createDecisionCard, personalKeys, sourcesQuery, type Conditions, type DecisionChanges, type DecisionOption, type EvidenceInput } from "@/lib/personal-api";
import { usePersonalProject } from "@/features/project/use-personal-project";
import { MutationNotice, useReceiptMutation } from "@/features/project/use-receipt-mutation";
import { usePersonalDraft } from "@/features/project/use-personal-draft";
import { conditionsText } from "@/features/project/project-context-editor";
import { SourceReferencePicker } from "@/features/sources/source-reference-picker";
import { DecisionTags } from "./decision-tags";

export const DECISION_STATES = { considering: "考虑中", adopted: "已采纳", not_adopted: "不采纳", archived: "已归档" };
export function describeDecision(value: DecisionChanges) {
  return [`标题：${value.title}`, `空间：${value.space ?? ""}`, `阶段：${value.stage ?? ""}`, `个人状态：${DECISION_STATES[value.state]}`, `原问题：${value.question}`, `我的决定：${value.decision_text}`, `理由与备注：${value.decision_reason}`, `待确认问题：\n${value.open_questions.join("\n")}`, `保存时的条件：${conditionsText(value.context_snapshot)}`, ...value.alternatives.map((option) => `方案${option.option_id === value.selected_option_id ? "（已选）" : ""}：${option.label}\n${option.description ?? ""}`), `保存的内容：\n${value.content_snapshot.text}`].join("\n");
}
export function decisionSeedKey(seed: DecisionSeed) {
  // The complete seed prevents drafts for different answers or references from
  // sharing a create slot. This key remains only in the in-memory registry.
  return JSON.stringify([seed.title, seed.question ?? "", seed.context_snapshot ?? {}, seed.content_snapshot, seed.alternatives ?? [], seed.evidence]);
}
export interface DecisionSeed { title: string; question?: string; context_snapshot?: Conditions; content_snapshot: DecisionChanges["content_snapshot"]; alternatives?: DecisionOption[]; evidence: (EvidenceInput & { label: string })[] }
export function SaveDecisionButton({ seed, label = "保存为个人决定" }: { seed: () => DecisionSeed; label?: string }) {
  const access = usePersonalProject();
  if (!access.writable) return <Button size="sm" variant="outline" disabled title={access.reason}><BookmarkPlus size={14} />{label}</Button>;
  return <WritableSaveDecisionButton key={access.scope} seed={seed} label={label} />;
}
function WritableSaveDecisionButton({ seed, label }: { seed: () => DecisionSeed; label: string }) {
  const [draft, setDraft] = useState<DecisionSeed | null>(null);
  const [open, setOpen] = useState(false);
  return <span className="inline-block" onClick={(event) => event.stopPropagation()}>
    <Button size="sm" variant="outline" onClick={() => { if (!draft) setDraft(seed()); setOpen(true); }}><BookmarkPlus size={14} />{label}</Button>
    {draft && <DecisionDialog seed={draft} open={open} onOpenChange={setOpen} onSaved={() => { setOpen(false); setDraft(null); }} />}
  </span>;
}

export function DecisionDialog({ seed, open, onOpenChange, onSaved }: { seed: DecisionSeed; open: boolean; onOpenChange: (open: boolean) => void; onSaved?: () => void }) {
  const access = usePersonalProject();
  if (!access.writable) return null;
  return <WritableDecisionDialog key={access.scope} seed={seed} open={open} onOpenChange={onOpenChange} onSaved={onSaved} />;
}
function WritableDecisionDialog({ seed, open, onOpenChange, onSaved }: { seed: DecisionSeed; open: boolean; onOpenChange: (open: boolean) => void; onSaved?: () => void }) {
  const access = usePersonalProject();
  const project = access.project.data?.project;
  const qc = useQueryClient(); const toast = useToast();
  const draftKey = `decision:${project!.project_id}:create:${decisionSeedKey(seed)}`;
  const { draft, setDraft, clear, notice } = usePersonalDraft({ scope: access.scope, key: draftKey, label: `新建决定：${seed.title}`, initial: () => ({ title: seed.title, state: "considering" as DecisionChanges["state"], tags: { space: "", stage: "" }, manualRefs: [] as EvidenceInput[], selectedOption: null as string | null, decisionText: "", reason: "", questions: "", contentText: seed.content_snapshot.text, selected: seed.evidence.filter((ref) => ref.target_fingerprint).map((ref) => ref.target_id) }), describe: (draft) => [
    describeDecision({ title: draft.title, state: draft.state, ...draft.tags, question: seed.question ?? "", context_snapshot: seed.context_snapshot ?? {}, alternatives: seed.alternatives ?? [], selected_option_id: draft.selectedOption, decision_text: draft.decisionText, decision_reason: draft.reason, open_questions: draft.questions.split("\n"), content_snapshot: { ...seed.content_snapshot, text: draft.contentText } }),
    `选定引用：${seed.evidence.filter((ref) => draft.selected.includes(ref.target_id)).map((ref) => ref.label).join("；")}`,
    `人工依据：${draft.manualRefs.map((ref) => qc.getQueryData<{ sources: { source_id: string; title: string }[] }>(sourcesQuery(project!.project_id, access.read).queryKey)?.sources.find((source) => source.source_id === ref.target_id)?.title ?? "已选来源（名称尚未载入）").join("；")}`].join("\n") });
  const { title, state, tags, manualRefs, selectedOption, decisionText, reason, questions, contentText, selected } = draft;
  const set = <K extends keyof typeof draft>(key: K, value: typeof draft[K]) => setDraft((draft) => ({ ...draft, [key]: value }));
  const setTitle = (value: string) => set("title", value);
  const setState = (value: DecisionChanges["state"]) => set("state", value);
  const setTags = (value: typeof tags) => set("tags", value);
  const setManualRefs = (value: EvidenceInput[]) => set("manualRefs", value);
  const setSelectedOption = (value: string | null) => set("selectedOption", value);
  const setDecisionText = (value: string) => set("decisionText", value);
  const setReason = (value: string) => set("reason", value);
  const setQuestions = (value: string) => set("questions", value);
  const setContentText = (value: string) => set("contentText", value);
  const setSelected = (action: SetStateAction<string[]>) => setDraft((draft) => ({ ...draft, selected: typeof action === "function" ? action(draft.selected) : action }));
  const save = useReceiptMutation((body: Parameters<typeof createDecisionCard>[1], signal) => createDecisionCard(project!.project_id, body, signal), () => {
    void qc.invalidateQueries({ queryKey: personalKeys.cards(project!.project_id) }); toast.success("个人决定已保存，知识库事实状态未改变。"); clear(); onSaved?.(); onOpenChange(false);
  }, { enabled: access.writable, owner: access.scope, draftKey });
  const locked = save.isPending || save.unknown || !access.writable;
  const invalidAdoption = state === "adopted" && !selectedOption && !decisionText.trim();
  return <Dialog open={open} onOpenChange={(next) => { if (!save.isPending) onOpenChange(next); }}><DialogContent title="保存个人决定" description="保存你在当前条件下的选择或待决问题，不把个人采纳改写为全库事实。">
    <form className="space-y-4 max-md:flex max-md:min-h-0 max-md:flex-1 max-md:flex-col" onSubmit={(event) => { event.preventDefault(); if (locked || invalidAdoption) return; save.submit({
      title: title.trim(), ...tags, question: seed.question ?? "", context_snapshot: seed.context_snapshot ?? {}, alternatives: seed.alternatives ?? [], selected_option_id: selectedOption,
      decision_text: decisionText, decision_reason: reason, open_questions: questions.split("\n").map((line) => line.trim()).filter(Boolean), content_snapshot: { ...seed.content_snapshot, text: contentText }, state,
      evidence_refs: [...seed.evidence.filter((ref) => selected.includes(ref.target_id) && ref.target_fingerprint).map(({ target_kind, target_id, target_fingerprint }) => ({ target_kind, target_id, target_fingerprint })), ...manualRefs],
    }, access.capabilities.data!.primary_epoch); }}>
      <Field label="标题"><Input aria-label="决定标题" value={title} onChange={(event) => setTitle(event.target.value)} maxLength={200} required disabled={locked} /></Field>
      <DecisionTags {...tags} onChange={setTags} disabled={locked} />
      <Field label="个人状态"><Select aria-label="个人状态" value={state} onChange={(event) => setState(event.target.value as DecisionChanges["state"])} disabled={locked}>{Object.entries(DECISION_STATES).filter(([key]) => key !== "archived").map(([key, label]) => <option key={key} value={key}>{label}</option>)}</Select></Field>
      {seed.alternatives?.length ? <Field label="选定方案"><Select aria-label="选定方案" value={selectedOption ?? ""} disabled={locked} onChange={(event) => setSelectedOption(event.target.value || null)}><option value="">尚未选择</option>{seed.alternatives.map((option) => <option key={option.option_id} value={option.option_id}>{option.label}</option>)}</Select></Field> : null}
      <Field label="我的决定" hint="考虑中可以只保存问题；采纳时至少明确选择一个方案或写下决定。"><Textarea aria-label="我的决定" value={decisionText} onChange={(event) => setDecisionText(event.target.value)} disabled={locked} rows={2} /></Field>
      <Field label="理由或备注（可留空）"><Textarea aria-label="决定理由" value={reason} onChange={(event) => setReason(event.target.value)} disabled={locked} rows={2} /></Field>
      <Field label="待确认问题（每行一项）"><Textarea aria-label="待确认问题" value={questions} onChange={(event) => setQuestions(event.target.value)} disabled={locked} rows={2} /></Field>
      <section className="text-[12.5px] text-ink-2"><h3 className="font-medium">保存时的条件</h3><p className="mt-1 text-muted">{conditionsText(seed.context_snapshot ?? {})}</p></section>
      <section className="space-y-2 text-[12.5px]"><h3 className="font-medium">选定引用（保存当时内容）</h3>{seed.evidence.length ? seed.evidence.map((ref) => <label key={`${ref.target_kind}:${ref.target_id}`} className="flex items-start gap-2 rounded-ctl border border-line p-2"><input type="checkbox" className="mt-1" checked={selected.includes(ref.target_id)} disabled={locked || !ref.target_fingerprint} onChange={(event) => setSelected((items) => event.target.checked ? [...items, ref.target_id] : items.filter((id) => id !== ref.target_id))} /><span>{ref.label}{!ref.target_fingerprint && <span className="text-st-wait">（缺少可验证指纹，请重新读取来源）</span>}</span></label>) : <p className="text-muted">暂无引用，可以先保存待决问题。</p>}</section>
      <details className="text-[12px] text-muted" open={contentText.length > 16000 || undefined}><summary className="cursor-pointer">查看或选取保存的内容片段</summary><Textarea aria-label="保存的内容片段" className="mt-2" rows={5} maxLength={16000} disabled={locked} value={contentText} onChange={(event) => setContentText(event.target.value)} />{contentText.length > 16000 && <p className="mt-1 text-st-wait">内容超过 16000 字，请明确选取要保存的片段；引用与条件另行完整保存。</p>}</details>
      {project && <SourceReferencePicker projectId={project.project_id} value={manualRefs} onChange={setManualRefs} disabled={locked} excludeIds={seed.evidence.filter((ref) => ref.target_kind === "source").map((ref) => ref.target_id)} />}
      {!access.writable && <p role="status" className="text-[12px] text-st-wait">{access.reason}</p>}<p className="text-xs text-muted">{notice}</p><MutationNotice mutation={save} />
      <div className="sticky bottom-0 -mx-5 -mb-5 flex flex-wrap gap-2 border-t border-line bg-surface p-4 max-md:-mx-4 max-md:-mb-4 max-md:mt-auto"><Button type="submit" disabled={locked || !title.trim() || invalidAdoption || contentText.length > 16000}>{save.isPending ? "正在保存…" : "保存决定"}</Button><Button variant="ghost" disabled={save.isPending} onClick={() => onOpenChange(false)}>稍后继续</Button>{invalidAdoption && <p className="w-full text-[12px] text-st-wait">采纳前请选择方案或填写明确决定。</p>}</div>
    </form>
  </DialogContent></Dialog>;
}
