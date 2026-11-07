import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { searchAtoms } from "@/lib/api";
import type { SearchRow } from "@/lib/api-types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { ErrorState, InlineSpinner } from "@/components/shared/states";

export function KnowledgeCandidatePicker({ onSelect, disabled }: { onSelect: (row: SearchRow) => void; disabled?: boolean }) {
  const [draft, setDraft] = useState(""); const [query, setQuery] = useState("");
  const results = useQuery({ queryKey: ["personal", "check-candidates", query], queryFn: ({ signal }) => searchAtoms(query, {}, signal), enabled: !!query, retry: false });
  return <details className="rounded-ctl border border-line p-3"><summary className="cursor-pointer text-[13px] font-medium">从知识库选取现场观察候选</summary><p className="my-2 text-xs text-muted">仅带入建议、条件与出处，判据仍待核实；视频建议不能直接判为符合。</p><div className="flex items-center gap-2"><Input aria-label="搜索现场候选" value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="如：卫生间防水、管根" disabled={disabled} className="max-md:h-11" onKeyDown={(event) => { if (event.key === "Enter" && !event.nativeEvent.isComposing) { event.preventDefault(); setQuery(draft.trim()); } }} /><Button disabled={disabled || !draft.trim()} onClick={() => setQuery(draft.trim())}>查找候选</Button></div>
    {query && (results.isError ? <ErrorState error={results.error} onRetry={() => void results.refetch()} context="现场候选" /> : results.isPending ? <InlineSpinner label="查找建议…" /> : <ul className="mt-3 max-h-72 space-y-2 overflow-y-auto overscroll-contain">{results.data?.results.length ? results.data.results.map((row) => <li key={row.id} className="rounded-ctl border border-line p-2 text-[12px]"><p>{row.claim}</p><p className="mt-1 text-muted">{row.video_title || "来源待核实"}</p><Button size="sm" variant="outline" className="mt-2" disabled={disabled || !row.target_fingerprint} onClick={() => onSelect(row)}>加入为观察候选</Button></li>) : <li className="text-muted">当前结果未找到候选，可调整关键词或先手工记录观察项。</li>}</ul>)}
  </details>;
}
