import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { sourcesQuery, type EvidenceInput, type EvidenceRef, type SourceRecord } from "@/lib/personal-api";
import { Button } from "@/components/ui/button";
import { ErrorState, InlineSpinner } from "@/components/shared/states";
import { SourceEditorButton } from "./source-editor-button";
import { EvidenceSnapshot } from "./evidence-snapshot";
import { usePersonalProject } from "@/features/project/use-personal-project";

export function SourceReferencePicker({ projectId, value, onChange, disabled, excludeIds = [], referenceSnapshots = [] }: { projectId: string; value: EvidenceInput[]; onChange: (value: EvidenceInput[]) => void; disabled?: boolean; excludeIds?: string[]; referenceSnapshots?: EvidenceRef[] }) {
  const access = usePersonalProject();
  const query = useQuery({ ...sourcesQuery(projectId, access.read), enabled: access.readable && !!projectId }); const [notice, setNotice] = useState("");
  const [snapshots, setSnapshots] = useState<Record<string, { fingerprint: string; snapshot: Record<string, unknown> }>>(() => Object.fromEntries(referenceSnapshots.map((ref) => [ref.target_id, { fingerprint: ref.target_fingerprint, snapshot: ref.snapshot }])));
  const remember = (source: SourceRecord) => setSnapshots((current) => ({ ...current, [source.source_id]: { fingerprint: source.target_fingerprint, snapshot: structuredClone(source) as unknown as Record<string, unknown> } }));
  return <section className="space-y-3 rounded-ctl border border-line p-3"><div className="flex flex-wrap items-center justify-between gap-2"><h3 className="text-[13px] font-medium">人工依据</h3><SourceEditorButton disabled={disabled} onSaved={() => setNotice("来源已保存；请在下方明确勾选。原表单草稿与已选引用保持不变。")} /></div>
    <p className="text-xs text-muted">勾选后才引用来源；维护来源不会自动改写已选依据版本。</p>{notice && <p role="status" className="text-xs text-muted">{notice}</p>}
    {query.isError ? <ErrorState error={query.error} onRetry={() => void query.refetch()} context="人工依据" /> : query.isPending ? <InlineSpinner label="读取人工依据…" /> : query.data.sources.filter((source) => !excludeIds.includes(source.source_id)).map((source) => {
      const selected = value.find((ref) => ref.target_kind === "source" && ref.target_id === source.source_id);
      return <div key={source.source_id} className="space-y-2 rounded-ctl border border-line p-2 text-[12.5px]"><label className="flex items-start gap-2"><input type="checkbox" checked={!!selected} disabled={disabled} onChange={(event) => { if (event.target.checked) remember(source); onChange(event.target.checked ? [...value, { target_kind: "source", target_id: source.source_id, target_fingerprint: source.target_fingerprint }] : value.filter((ref) => !(ref.target_kind === "source" && ref.target_id === source.source_id))); }} /><span>{source.title} · {source.verification_state === "user_checked" ? "用户已核对" : "未核对"} · {source.validity === "current" ? "用户记录为现行" : "有效性需复看"}</span></label>
        <SourceEditorButton source={source} disabled={disabled} onSaved={() => setNotice("来源已修改。若已选择该来源，请先核对变化，再明确采用当前版本；旧记录不会自动更新。")} />
        {selected && selected.target_fingerprint !== source.target_fingerprint && <div className="space-y-2"><p className="text-xs text-st-wait">此来源在选定后已变化，当前仍保留之前选定的版本。</p><details><summary className="cursor-pointer">查看选定时与当前内容</summary><p className="mt-2 font-medium">选定时</p>{snapshots[source.source_id]?.fingerprint === selected.target_fingerprint ? <EvidenceSnapshot snapshot={snapshots[source.source_id]!.snapshot} /> : <p className="text-xs text-muted">当前表单未载入原版本内容，请先回看保存的依据。</p>}<p className="mt-2 font-medium">当前</p><EvidenceSnapshot snapshot={source as unknown as Record<string, unknown>} /></details><Button size="sm" variant="outline" disabled={disabled} onClick={() => { remember(source); onChange(value.map((ref) => ref === selected ? { ...ref, target_fingerprint: source.target_fingerprint } : ref)); }}>核对后采用当前来源版本</Button></div>}
      </div>;
    })}
    {query.data?.sources.length === 0 && <p className="text-xs text-muted">尚无人工依据，可以就地新增；未知内容无需补造。</p>}
    {query.data && query.data.total > query.data.sources.length && <p className="text-xs text-muted">当前显示前 {query.data.sources.length} 份来源，共 {query.data.total} 份。</p>}
  </section>;
}
