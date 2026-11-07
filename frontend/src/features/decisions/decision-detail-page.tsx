import { useState, type SetStateAction } from "react";
import { Link, useLocation, useParams } from "react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { decisionCardQuery, decisionRevisionsQuery, downloadMarkdown, exportDecision, patchDecisionCard, personalKeys, updateDecisionEvidence, sourcesQuery, type DecisionCard, type DecisionChanges, type EvidenceInput } from "@/lib/personal-api";
import { usePersonalProject } from "@/features/project/use-personal-project";
import { conditionsText } from "@/features/project/project-context-editor";
import { MutationNotice, useReceiptMutation } from "@/features/project/use-receipt-mutation";
import { usePersonalDraft } from "@/features/project/use-personal-draft";
import { PageHeader, EmptyState, ErrorState, InlineSpinner } from "@/components/shared/states";
import { SourceReferencePicker } from "@/features/sources/source-reference-picker";
import { SourceEditorButton } from "@/features/sources/source-editor-button";
import { EvidenceSnapshot } from "@/features/sources/evidence-snapshot";
import { MarkdownView } from "@/components/shared/markdown-view";
import { Button } from "@/components/ui/button";
import { Field, Input, Select, Textarea } from "@/components/ui/field";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { useToast } from "@/components/shared/toaster";
import { DecisionTags } from "./decision-tags";
import { DECISION_STATES, describeDecision } from "./decision-dialog";
import { PersonalReadBanner } from "@/features/project/personal-read-banner";
import { isPersonalAbort, usePersonalTransfer } from "@/features/project/use-personal-transfer";

const BINDING = { active: "依据有效", stale: "内容已变化", missing: "原来源已缺失", legacy_unbound: "历史引用待重新绑定" };
function DecisionRevisionSnapshot({ snapshot }: { snapshot: Record<string, unknown> }) {
  const card = snapshot as unknown as Partial<DecisionCard>;
  return <div className="mt-3 space-y-2 text-[12.5px] text-ink-2"><p>{card.state ? DECISION_STATES[card.state] : "历史记录"} · {card.title}</p><p className="whitespace-pre-wrap">当时决定：{card.decision_text || card.question || "尚未决定"}</p><p className="whitespace-pre-wrap">理由与备注：{card.decision_reason || "未填写"}</p><p>当时标签：{card.space || "未标记空间"} · {card.stage || "未标记阶段"}</p><p>当时条件：{conditionsText(card.context_snapshot ?? {})}</p>{card.open_questions?.length ? <p>待确认：{card.open_questions.join("；")}</p> : null}{card.content_snapshot?.text && <details><summary className="cursor-pointer">当时的回答或比较</summary><MarkdownView md={card.content_snapshot.text} /></details>}{card.evidence_refs?.map((ref) => <div key={ref.ref_id} className="border-t border-line pt-2"><EvidenceSnapshot snapshot={ref.snapshot} /></div>)}</div>;
}
export default function DecisionDetailPage() {
  const id = useParams().cardId ?? ""; const access = usePersonalProject();
  const query = useQuery({ ...decisionCardQuery(id, access.read), enabled: access.readable && !!id });
  if (access.capabilities.isError || query.isError) return <div className="p-5"><ErrorState error={access.capabilities.error ?? query.error!} onRetry={() => { void access.capabilities.refetch(); if (access.readable) void query.refetch(); }} context="个人决定" /></div>;
  if (!access.readable) return <div className="p-5">{access.capabilities.isPending ? <InlineSpinner label="读取决定权限…" /> : <EmptyState title="个人记录暂不可用" desc={access.reason} />}</div>;
  if (!query.data) return <div className="p-5"><InlineSpinner label="读取决定…" /></div>;
  return access.writable ? <DecisionEditor key={`${access.scope}:${id}`} card={query.data.card} onReload={() => void query.refetch()} /> : <DecisionReader key={`${access.scope}:${id}`} card={query.data.card} access={access} />;
}
function DecisionEditor({ card, onReload }: { card: DecisionCard; onReload: () => void }) {
  const location = useLocation();
  const requestedReturn = (location.state as { decisionsReturnTo?: string } | null)?.decisionsReturnTo;
  const returnTo = requestedReturn && /^\/decisions(?:\?|$)/.test(requestedReturn) ? requestedReturn : "/decisions";
  const access = usePersonalProject(); const qc = useQueryClient(); const toast = useToast(); const transfer = usePersonalTransfer(access.read);
  const draftKey = `decision:${card.card_id}:edit`;
  const { draft: edit, setDraft: setEdit, clear, notice } = usePersonalDraft<{ value: DecisionChanges; baseRevision: number }>({ scope: access.scope, key: draftKey, label: `决定：${card.title}`, initial: () => ({ value: card, baseRevision: card.revision }), describe: (draft) => `${describeDecision(draft.value)}\n草稿基于版本：${draft.baseRevision}` });
  const draft = edit.value; const baseRevision = edit.baseRevision;
  const setDraft = (action: SetStateAction<DecisionChanges>) => setEdit((draft) => ({ ...draft, value: typeof action === "function" ? action(draft.value) : action }));
  const setBaseRevision = (action: SetStateAction<number>) => setEdit((draft) => ({ ...draft, baseRevision: typeof action === "function" ? action(draft.baseRevision) : action }));
  const [historyOpen, setHistoryOpen] = useState(false); const [evidenceOpen, setEvidenceOpen] = useState(false);
  const [exporting, setExporting] = useState(false);
  const history = useQuery({ ...decisionRevisionsQuery(card.card_id, access.read), enabled: access.readable && historyOpen });
  const saved = (result: { card: DecisionCard }) => {
    qc.setQueryData(decisionCardQuery(card.card_id, access.read).queryKey, result); void qc.invalidateQueries({ queryKey: personalKeys.cards(card.project_id) }); void qc.invalidateQueries({ queryKey: personalKeys.revisions(card.card_id) });
    setDraft(result.card); setBaseRevision(result.card.revision); clear(); toast.success("决定已保存，旧版本仍可回看。");
  };
  const evidenceSaved = (result: { card: DecisionCard }, evidenceBaseRevision: number) => {
    qc.setQueryData(decisionCardQuery(card.card_id, access.read).queryKey, result); void qc.invalidateQueries({ queryKey: personalKeys.cards(card.project_id) }); void qc.invalidateQueries({ queryKey: personalKeys.revisions(card.card_id) });
    setBaseRevision((current) => current === evidenceBaseRevision ? result.card.revision : current);
    toast.success("依据已更新；尚未保存的决定表单草稿仍保留。");
  };
  const save = useReceiptMutation((body: Parameters<typeof patchDecisionCard>[1], signal) => patchDecisionCard(card.card_id, body, signal), saved, { enabled: access.writable, owner: access.scope, draftKey });
  const locked = !access.writable || save.isPending || save.unknown || card.state === "archived";
  const change = <K extends keyof DecisionChanges>(key: K, value: DecisionChanges[K]) => setDraft((current) => ({ ...current, [key]: value }));
  const write = (state = draft.state) => save.submit({ title: draft.title, space: draft.space ?? "", stage: draft.stage ?? "", question: draft.question, context_snapshot: draft.context_snapshot, alternatives: draft.alternatives, selected_option_id: draft.selected_option_id, decision_text: draft.decision_text, decision_reason: draft.decision_reason, open_questions: draft.open_questions, content_snapshot: draft.content_snapshot, state }, access.capabilities.data!.primary_epoch, baseRevision);
  const exportOne = async () => { setExporting(true); try { await transfer(async (signal) => { const text = await exportDecision(card.card_id, access.read, signal); signal.throwIfAborted(); downloadMarkdown(text, `装修决定-${card.card_id}.md`); }); } catch (error) { if (!isPersonalAbort(error)) toast.error(error instanceof Error ? error.message : "导出失败"); } finally { setExporting(false); } };
  return <div className="mx-auto w-full max-w-[900px] px-4 py-5 md:px-6"><Link to={returnTo} className="mb-3 inline-block text-[13px] text-acc">← 个人决定</Link><PageHeader title={card.title} desc={`版本 ${card.revision} · ${DECISION_STATES[card.state]} · 记录于 ${card.created_at}`} actions={<Button size="sm" variant="outline" disabled={exporting} onClick={() => void exportOne()}>导出 Markdown</Button>} />
    {card.needs_recheck && <p role="status" className="mb-4 rounded-ctl border border-st-wait-line bg-st-wait-bg p-3 text-[13px] text-st-wait">依据发生变化或缺失。当前仍显示保存时的内容，个人状态未自动改变；请复看后明确更新。</p>}
    {!access.writable && <p className="mb-3 text-[13px] text-st-wait">{access.reason}</p>}
    <form className="space-y-4 rounded-panel border border-line bg-surface p-4" onSubmit={(event) => { event.preventDefault(); write(); }}>
      <Field label="标题"><Input aria-label="决定标题" value={draft.title} disabled={locked} onChange={(event) => change("title", event.target.value)} /></Field>
      <DecisionTags space={draft.space} stage={draft.stage} onChange={(tags) => setDraft((current) => ({ ...current, ...tags }))} disabled={locked} />
      <Field label="个人状态"><Select aria-label="个人状态" value={draft.state} disabled={locked} onChange={(event) => change("state", event.target.value as DecisionChanges["state"])}>{Object.entries(DECISION_STATES).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</Select></Field>
      {draft.alternatives.length > 0 && <Field label="选定方案"><Select aria-label="选定方案" value={draft.selected_option_id ?? ""} disabled={locked} onChange={(event) => change("selected_option_id", event.target.value || null)}><option value="">尚未选择</option>{draft.alternatives.map((option) => <option key={option.option_id} value={option.option_id}>{option.label}</option>)}</Select></Field>}
      <Field label="我的决定"><Textarea aria-label="我的决定" value={draft.decision_text} disabled={locked} onChange={(event) => change("decision_text", event.target.value)} rows={2} /></Field>
      <Field label="理由与补充备注"><Textarea aria-label="决定理由" value={draft.decision_reason} disabled={locked} onChange={(event) => change("decision_reason", event.target.value)} rows={3} /></Field>
      <Field label="待确认问题（每行一项）"><Textarea aria-label="待确认问题" value={draft.open_questions.join("\n")} disabled={locked} onChange={(event) => change("open_questions", event.target.value.split("\n"))} rows={2} /></Field>
      <p className="text-[12.5px] text-muted">保存时的条件：{conditionsText(card.context_snapshot)}</p><p className="text-xs text-muted">{notice}</p><MutationNotice mutation={save} />
      {save.error && !save.unknown && <div className="space-y-2"><p className="text-[12px] text-muted">本草稿基于版本 {baseRevision}，已读取的服务器版本为 {card.revision}。</p><Button size="sm" variant="outline" onClick={onReload}>查看最新记录</Button><Button size="sm" variant="outline" disabled={baseRevision === card.revision} onClick={() => { setBaseRevision(card.revision); save.reset(); }}>以最新版本为基础保留本次草稿</Button><details><summary className="cursor-pointer text-[12px]">查看服务器当前决定</summary><p className="whitespace-pre-wrap text-[12px]">{card.decision_text}\n{card.decision_reason}</p></details></div>}
      <div className="flex flex-wrap gap-2"><Button type="submit" disabled={locked || !draft.title.trim() || (draft.state === "adopted" && !draft.selected_option_id && !draft.decision_text.trim())}>保存修改</Button>{card.state === "archived" ? <Button variant="outline" disabled={!access.writable || save.isPending || save.unknown} onClick={() => write("considering")}>恢复为考虑中</Button> : <Button variant="ghost" disabled={locked} onClick={() => write("archived")}>归档决定</Button>}</div>
    </form>
    <section className="mt-4 space-y-3"><div className="flex flex-wrap items-center justify-between gap-2"><h2 className="font-semibold">保存时的依据</h2><Button size="sm" variant="outline" disabled={!access.writable || card.state === "archived"} onClick={() => setEvidenceOpen(true)}>检查并更新依据</Button></div>
      {card.evidence_refs.length ? card.evidence_refs.map((ref) => <article key={ref.ref_id} className="rounded-panel border border-line bg-surface p-3"><div className="flex flex-wrap items-center gap-2"><span className={ref.binding_status === "active" ? "text-muted" : "text-st-wait"}>{BINDING[ref.binding_status]}</span><span className="text-xs text-muted">{ref.target_kind}</span></div><EvidenceSnapshot snapshot={ref.snapshot} />{ref.binding_status === "active" && typeof ref.snapshot.video_id === "string" ? <Link className="mt-2 inline-block text-[12px] text-acc" to={`/videos/${encodeURIComponent(ref.snapshot.video_id as string)}?t=${Number((ref.snapshot.evidence_refs as { start_ms?: number }[] | undefined)?.[0]?.start_ms ?? 0)}`}>查看当前原视频</Link> : <p className="mt-2 text-xs text-muted">保留当时快照；不会自动跳转到同主题的新来源。</p>}</article>) : <p className="text-[13px] text-muted">本记录尚未选择引用。</p>}
    </section>
    <details className="mt-4 rounded-panel border border-line bg-surface p-3"><summary className="cursor-pointer text-[13px]">查看原回答或比较快照</summary><MarkdownView md={card.content_snapshot.text || "仅保存个人记录"} /></details>
    <section className="mt-4"><Button size="sm" variant="outline" onClick={() => setHistoryOpen((value) => !value)}>{historyOpen ? "收起历史" : "查看历史版本"}</Button>{historyOpen && (history.isError ? <ErrorState error={history.error} onRetry={() => void history.refetch()} context="历史版本" /> : history.isPending ? <InlineSpinner label="读取历史…" /> : <div className="mt-2 space-y-2">{history.data?.revisions.map((revision) => <details key={revision.revision} className="rounded-panel border border-line bg-surface p-3"><summary className="cursor-pointer text-[13px]">版本 {revision.revision} · {revision.created_at}</summary><DecisionRevisionSnapshot snapshot={revision.snapshot} /></details>)}</div>)}</section>
    {evidenceOpen && <UpdateEvidenceDialog card={card} open onOpenChange={setEvidenceOpen} onSaved={evidenceSaved} epoch={access.capabilities.data!.primary_epoch} />}
  </div>;
}
function UpdateEvidenceDialog({ card, open, onOpenChange, onSaved, epoch }: { card: DecisionCard; open: boolean; onOpenChange: (open: boolean) => void; onSaved: (value: { card: DecisionCard }, baseRevision: number) => void; epoch: number }) {
  const access = usePersonalProject();
  const sources = useQuery({ ...sourcesQuery(card.project_id, access.read), enabled: access.readable });
  const draftKey = `decision:${card.card_id}:evidence`;
  const { draft, setDraft, clear, notice } = usePersonalDraft({ scope: access.scope, key: draftKey, label: `决定依据：${card.title}`, initial: () => ({ baseRevision: card.revision, additionalRefs: [] as EvidenceInput[], selected: card.evidence_refs.filter((ref) => ref.current_fingerprint || ref.binding_status === "active").map((ref) => ref.ref_id) }), describe: (draft) => [
    `决定：${card.title}`, `草稿基于版本：${draft.baseRevision}`, `保留的依据：${card.evidence_refs.filter((ref) => draft.selected.includes(ref.ref_id)).map((ref) => String(ref.snapshot.claim ?? ref.snapshot.title ?? "原记录依据")).join("；")}`, `取消的依据：${card.evidence_refs.filter((ref) => !draft.selected.includes(ref.ref_id)).map((ref) => String(ref.snapshot.claim ?? ref.snapshot.title ?? "原记录依据")).join("；")}`, `新增人工依据：${draft.additionalRefs.map((ref) => sources.data?.sources.find((source) => source.source_id === ref.target_id)?.title ?? "已选来源（名称尚未载入）").join("；")}`].join("\n") });
  const { baseRevision, additionalRefs, selected } = draft;
  const setBaseRevision = (baseRevision: number) => setDraft((draft) => ({ ...draft, baseRevision }));
  const setAdditionalRefs = (additionalRefs: EvidenceInput[]) => setDraft((draft) => ({ ...draft, additionalRefs }));
  const setSelected = (action: SetStateAction<string[]>) => setDraft((draft) => ({ ...draft, selected: typeof action === "function" ? action(draft.selected) : action }));
  const [latestRead, setLatestRead] = useState(false);
  const latest = useQuery({ ...decisionCardQuery(card.card_id, access.read), enabled: false });
  const save = useReceiptMutation((request: Parameters<typeof updateDecisionEvidence>[1], signal) => updateDecisionEvidence(card.card_id, request, signal), (result) => { clear(); onSaved(result, baseRevision); onOpenChange(false); }, { enabled: access.writable, owner: access.scope, draftKey });
  const locked = !access.writable || save.isPending || save.unknown;
  return <Dialog open={open} onOpenChange={(next) => { if (!save.isPending && !save.unknown) onOpenChange(next); }}><DialogContent title="核对依据变化" description="确认后建立新版本；旧依据与旧决定会保留在历史中。缺失来源可明确取消引用。"><div className="space-y-4">{card.evidence_refs.map((ref) => <section key={ref.ref_id} className="rounded-ctl border border-line p-3 text-[12.5px]"><label className="flex gap-2"><input type="checkbox" checked={selected.includes(ref.ref_id)} disabled={locked || (!ref.current_fingerprint && ref.binding_status !== "active")} onChange={(event) => setSelected((ids) => event.target.checked ? [...ids, ref.ref_id] : ids.filter((id) => id !== ref.ref_id))} /><span>{BINDING[ref.binding_status]}：{String(ref.snapshot.claim ?? ref.snapshot.title ?? ref.target_id)}</span></label>{ref.target_kind === "source" && sources.data?.sources.find((source) => source.source_id === ref.target_id) && <div className="mt-2"><SourceEditorButton source={sources.data.sources.find((source) => source.source_id === ref.target_id)} disabled={locked} /></div>}<details className="mt-2"><summary className="cursor-pointer">查看保存版本与当前版本</summary><p className="mt-2 font-medium">保存时</p><EvidenceSnapshot snapshot={ref.snapshot} /><p className="mt-2 font-medium">当前</p>{ref.current_snapshot ? <EvidenceSnapshot snapshot={ref.current_snapshot} /> : <p className="text-xs text-muted">当前来源不可用，将取消该引用；历史快照保留。</p>}</details></section>)}<SourceReferencePicker projectId={card.project_id} value={additionalRefs} onChange={setAdditionalRefs} disabled={locked} excludeIds={card.evidence_refs.filter((ref) => ref.target_kind === "source").map((ref) => ref.target_id)} /><p className="text-xs text-muted">{notice}</p><MutationNotice mutation={save} />{save.error && !save.unknown && <section className="space-y-2 rounded-ctl border border-st-wait-line p-3 text-xs"><p>本次依据选择基于决定版本 {baseRevision}，草稿未被覆盖。</p><Button size="sm" variant="outline" disabled={latest.isFetching} onClick={() => { void latest.refetch().then((result) => setLatestRead(!!result.data && !result.isError)); }}>读取最新决定版本</Button>{latestRead && latest.data && <><p>服务器版本 {latest.data.card.revision}：{latest.data.card.title}</p><p className="whitespace-pre-wrap">当前决定：{latest.data.card.decision_text || latest.data.card.question || "尚未决定"}</p><p className="whitespace-pre-wrap">理由：{latest.data.card.decision_reason || "未填写"}</p><Button size="sm" variant="outline" disabled={baseRevision === latest.data.card.revision} onClick={() => { setBaseRevision(latest.data!.card.revision); setLatestRead(false); save.reset(); }}>核对后以最新版本保留本次依据选择</Button></>}</section>}<Button disabled={locked} onClick={() => save.submit({ confirm: true, evidence_refs: [...card.evidence_refs.filter((ref) => selected.includes(ref.ref_id)).map((ref) => ({ target_kind: ref.target_kind, target_id: ref.target_id, target_fingerprint: ref.current_fingerprint ?? ref.target_fingerprint })), ...additionalRefs] }, epoch, baseRevision)}>确认更新为新版本</Button></div></DialogContent></Dialog>;
}

function DecisionReader({ card, access }: { card: DecisionCard; access: ReturnType<typeof usePersonalProject> }) {
  const location = useLocation(); const toast = useToast(); const transfer = usePersonalTransfer(access.read);
  const requestedReturn = (location.state as { decisionsReturnTo?: string } | null)?.decisionsReturnTo;
  const returnTo = requestedReturn && /^\/decisions(?:\?|$)/.test(requestedReturn) ? requestedReturn : "/decisions";
  const [historyOpen, setHistoryOpen] = useState(false); const [exporting, setExporting] = useState(false);
  const history = useQuery({ ...decisionRevisionsQuery(card.card_id, access.read), enabled: access.readable && historyOpen });
  const exportOne = async () => { setExporting(true); try { await transfer(async (signal) => { const text = await exportDecision(card.card_id, access.read, signal); signal.throwIfAborted(); downloadMarkdown(text, `装修决定-${card.card_id}.md`); }); } catch (error) { if (!isPersonalAbort(error)) toast.error(error instanceof Error ? error.message : "导出失败"); } finally { setExporting(false); } };
  return <div className="mx-auto w-full max-w-[900px] px-4 py-5 md:px-6"><Link to={returnTo} className="mb-3 inline-block text-[13px] text-acc">← 个人决定</Link><PageHeader title={card.title} desc={`版本 ${card.revision} · ${DECISION_STATES[card.state]} · 记录于 ${card.created_at}`} actions={<Button size="sm" variant="outline" disabled={!access.readable || exporting} onClick={() => void exportOne()}>导出 Markdown</Button>} />
    <PersonalReadBanner capabilities={access.capabilities.data} writable={false} reason={access.reason} />
    {card.needs_recheck && <p role="status" className="mb-4 rounded-ctl border border-st-wait-line bg-st-wait-bg p-3 text-[13px] text-st-wait">依据发生变化或缺失。当前仍显示保存时的内容，个人状态未自动改变。</p>}
    <section className="space-y-3 rounded-panel border border-line bg-surface p-4 text-[13px]"><p>标签：{card.space || "未标记空间"} · {card.stage || "未标记阶段"}</p><p className="whitespace-pre-wrap">我的决定：{card.decision_text || card.question || "尚未决定"}</p><p className="whitespace-pre-wrap">理由与备注：{card.decision_reason || "未填写"}</p>{card.alternatives.length > 0 && <ul className="space-y-1">{card.alternatives.map((option) => <li key={option.option_id}>{option.label}{option.option_id === card.selected_option_id ? "（已选择）" : ""}{option.description ? `：${option.description}` : ""}</li>)}</ul>}{card.open_questions.length > 0 && <p>待确认：{card.open_questions.join("；")}</p>}<p className="text-muted">保存时的条件：{conditionsText(card.context_snapshot)}</p></section>
    <section className="mt-4 space-y-3"><h2 className="font-semibold">保存时的依据</h2>{card.evidence_refs.length ? card.evidence_refs.map((ref) => <article key={ref.ref_id} className="rounded-panel border border-line bg-surface p-3"><p className={ref.binding_status === "active" ? "text-muted" : "text-st-wait"}>{BINDING[ref.binding_status]}</p><EvidenceSnapshot snapshot={ref.snapshot} />{ref.binding_status === "active" && typeof ref.snapshot.video_id === "string" ? <Link className="mt-2 inline-block text-[12px] text-acc" to={`/videos/${encodeURIComponent(ref.snapshot.video_id)}?t=${Number((ref.snapshot.evidence_refs as { start_ms?: number }[] | undefined)?.[0]?.start_ms ?? 0)}`}>查看当前原视频</Link> : <p className="mt-2 text-xs text-muted">保留当时快照；不会自动跳转到同主题的新来源。</p>}</article>) : <p className="text-[13px] text-muted">本记录尚未选择引用。</p>}</section>
    <details className="mt-4 rounded-panel border border-line bg-surface p-3"><summary className="cursor-pointer text-[13px]">查看原回答或比较快照</summary><MarkdownView md={card.content_snapshot.text || "仅保存个人记录"} /></details>
    <section className="mt-4"><Button size="sm" variant="outline" disabled={!access.readable} onClick={() => setHistoryOpen((value) => !value)}>{historyOpen ? "收起历史" : "查看历史版本"}</Button>{historyOpen && (history.isError ? <ErrorState error={history.error} onRetry={() => void history.refetch()} context="历史版本" /> : history.isPending ? <InlineSpinner label="读取历史…" /> : <div className="mt-2 space-y-2">{history.data?.revisions.map((revision) => <details key={revision.revision} className="rounded-panel border border-line bg-surface p-3"><summary className="cursor-pointer text-[13px]">版本 {revision.revision} · {revision.created_at}</summary><DecisionRevisionSnapshot snapshot={revision.snapshot} /></details>)}</div>)}</section>
  </div>;
}
