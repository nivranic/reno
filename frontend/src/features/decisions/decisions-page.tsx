import { useId, useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { decisionCardsQuery, downloadMarkdown, exportDecisions, type DecisionShareSelection } from "@/lib/personal-api";
import { facetsQuery } from "@/lib/queries";
import { usePersonalProject } from "@/features/project/use-personal-project";
import { conditionsText } from "@/features/project/project-context-editor";
import { PersonalReadBanner } from "@/features/project/personal-read-banner";
import { isPersonalAbort, usePersonalTransfer } from "@/features/project/use-personal-transfer";
import { PageHeader, EmptyState, ErrorState, InlineSpinner } from "@/components/shared/states";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/field";
import { useToast } from "@/components/shared/toaster";
import { DECISION_STATES, SaveDecisionButton } from "./decision-dialog";
import { DecisionSharePreview } from "./decision-share-preview";

export default function DecisionsPage() {
  const access = usePersonalProject();
  const viewKey = JSON.stringify([access.scope, access.project.data?.project?.project_id ?? ""]);
  return <DecisionsView key={viewKey} access={access} />;
}
function DecisionsView({ access }: { access: ReturnType<typeof usePersonalProject> }) {
  const toast = useToast(); const transfer = usePersonalTransfer(access.read);
  const projectId = access.project.data?.project?.project_id ?? "";
  const [params, setParams] = useSearchParams(); const location = useLocation(); const listId = useId();
  const state = params.get("state") ?? ""; const space = params.get("space") ?? ""; const stage = params.get("stage") ?? "";
  const rawOffset = Number(params.get("offset") ?? 0); const offset = Number.isSafeInteger(rawOffset) && rawOffset >= 0 ? rawOffset : 0;
  const [selected, setSelected] = useState<DecisionShareSelection[]>([]); const [exporting, setExporting] = useState(false);
  const [selectionSearch, setSelectionSearch] = useState(location.search);
  if (selectionSearch !== location.search) {
    setSelectionSearch(location.search);
    setSelected([]);
  }
  const cards = useQuery({ ...decisionCardsQuery(projectId, state, offset, { space, stage }, access.read), enabled: access.readable && !!projectId }); const facets = useQuery(facetsQuery);
  const [previewSource, setPreviewSource] = useState({ data: cards.data, generation: 0 });
  if (previewSource.data !== cards.data) setPreviewSource({ data: cards.data, generation: previewSource.generation + 1 });
  const currentCards = new Map(cards.data?.cards.map((card) => [card.card_id, card]));
  const selectionPresent = selected.every(({ card_id }) => currentCards.has(card_id));
  const selectionComplete = selected.length > 0 && selectionPresent
    && selected.every(({ card_id, revision }) => currentCards.get(card_id)?.revision === revision);
  const previewAvailable = access.readable && cards.isSuccess && !cards.isFetching && selectionComplete;
  const selection = previewAvailable ? selected : [];
  const previewKey = JSON.stringify([projectId, location.search, selected, cards.dataUpdatedAt, previewSource.generation]);
  const updateParams = (updates: Record<string, string>, resetPage = true) => { const next = new URLSearchParams(params); for (const [key, value] of Object.entries(updates)) { if (value) next.set(key, value); else next.delete(key); } if (resetPage) next.delete("offset"); setSelected([]); setParams(next, { replace: true, state: location.state, flushSync: true }); };
  const exportSelected = async () => { setExporting(true); try { await transfer(async (signal) => { const text = await exportDecisions(projectId, selected.map(({ card_id }) => card_id), access.read, signal); signal.throwIfAborted(); downloadMarkdown(text, "装修决定.md"); }); } catch (error) { if (!isPersonalAbort(error)) toast.error(error instanceof Error ? error.message : "导出失败"); } finally { setExporting(false); } };
  return <div className="mx-auto w-full max-w-[980px] px-4 py-5 md:px-6"><PageHeader title="个人决定" desc="保留当时的选择、条件与依据，内容变化时逐项复看。" actions={<SaveDecisionButton label="记一个待决问题" seed={() => ({ title: "", content_snapshot: { kind: "note", text: "" }, evidence: [] })} />} />
    <PersonalReadBanner capabilities={access.capabilities.data} writable={access.writable} reason={access.reason} />
    <div className="mb-4 flex flex-wrap items-center gap-2"><Select aria-label="筛选个人状态" className="w-[160px]" value={state} onChange={(event) => updateParams({ state: event.target.value })}><option value="">全部状态</option>{Object.entries(DECISION_STATES).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</Select><Input aria-label="筛选空间" maxLength={80} className="w-[160px]" placeholder="全部空间（可填写）" list={`${listId}-spaces`} value={space} onChange={(event) => updateParams({ space: event.target.value })} /><datalist id={`${listId}-spaces`}>{facets.data?.spaces.map((value) => <option key={value} value={value} />)}</datalist><Input aria-label="筛选阶段" maxLength={80} className="w-[160px]" placeholder="全部阶段（可填写）" list={`${listId}-stages`} value={stage} onChange={(event) => updateParams({ stage: event.target.value })} /><datalist id={`${listId}-stages`}>{facets.data?.stages.map((value) => <option key={value} value={value} />)}</datalist>{(state || space || stage) && <Button size="sm" variant="ghost" onClick={() => updateParams({ state: "", space: "", stage: "" })}>清除筛选</Button>}<DecisionSharePreview key={previewKey} projectId={projectId} selection={selection} read={access.read} available={previewAvailable} onSelectionChanged={() => { setSelected([]); void cards.refetch(); }} /><Button variant="outline" size="sm" disabled={!access.readable || !selected.length || exporting} onClick={() => void exportSelected()}>导出所选（{selected.length}）</Button></div>
    {access.readable && cards.isSuccess && selected.length > 0 && !selectionComplete ? <p role="status" className="mb-3 text-[13px] text-st-wait">{selectionPresent ? "所选决定的版本已变化，请重新选择后预览。" : "所选决定已不在当前页，请重新选择后预览。"}</p> : null}
    {!projectId ? <EmptyState title="个人记录暂不可用" desc={access.reason} /> : cards.isError ? <ErrorState error={cards.error} onRetry={() => void cards.refetch()} context="个人决定" /> : cards.isPending ? <InlineSpinner label="读取个人决定…" /> : cards.data.cards.length === 0 ? <EmptyState title={state || space || stage ? "没有符合筛选的决定" : "还没有决定记录"} desc="可以调整筛选，或从问答、比较、知识卡保存个人决定。" /> : <div className="space-y-3">{cards.data.cards.map((card) => <article key={card.card_id} className="flex items-start gap-3 rounded-panel border border-line bg-surface p-4"><input className="mt-1" type="checkbox" aria-label={`选择${card.title}`} checked={selected.some(({ card_id }) => card_id === card.card_id)} onChange={(event) => setSelected((items) => event.target.checked ? [...items, { card_id: card.card_id, revision: card.revision }] : items.filter(({ card_id }) => card_id !== card.card_id))} /><Link className="min-w-0 flex-1" to={`/decisions/${card.card_id}`} state={{ decisionsReturnTo: `/decisions${location.search}` }}><div className="flex flex-wrap items-center gap-2"><h2 className="font-semibold text-ink">{card.title}</h2><span className="text-xs text-muted">{DECISION_STATES[card.state]}</span>{card.needs_recheck && <span className="text-xs text-st-wait">依据需复看</span>}</div><p className="mt-1 text-[13px] text-ink-2">{card.decision_text || card.question || "尚未决定"}</p><p className="mt-1 text-xs text-muted">标签：{card.space || "未标记空间"} · {card.stage || "未标记阶段"}</p><p className="mt-1 text-xs text-muted">{conditionsText(card.context_snapshot)} · 版本 {card.revision}</p></Link></article>)}</div>}
    {cards.data && <div className="mt-4 flex items-center gap-2 text-xs text-muted"><Button size="sm" variant="outline" disabled={!offset} onClick={() => updateParams({ offset: String(Math.max(0, offset - 30)) }, false)}>上一页</Button><span>共 {cards.data.total} 条</span><Button size="sm" variant="outline" disabled={offset + 30 >= cards.data.total} onClick={() => updateParams({ offset: String(offset + 30) }, false)}>下一页</Button></div>}
  </div>;
}
