import { useId } from "react";
import { useQuery } from "@tanstack/react-query";
import { facetsQuery } from "@/lib/queries";
import { Field, Input } from "@/components/ui/field";

export function DecisionTags({ space, stage, onChange, disabled }: { space?: string | null; stage?: string | null; onChange: (tags: { space: string; stage: string }) => void; disabled?: boolean }) {
  const facets = useQuery(facetsQuery);
  const id = useId();
  return <section className="space-y-2"><div className="grid gap-3 sm:grid-cols-2"><Field label="空间标签（可选）"><Input aria-label="决定空间标签" list={`${id}-spaces`} value={space ?? ""} placeholder="选择提示或自行填写" maxLength={80} disabled={disabled} onChange={(event) => onChange({ space: event.target.value, stage: stage ?? "" })} /><datalist id={`${id}-spaces`}>{facets.data?.spaces.map((value) => <option key={value} value={value} />)}</datalist></Field><Field label="阶段标签（可选）"><Input aria-label="决定阶段标签" list={`${id}-stages`} value={stage ?? ""} placeholder="选择提示或自行填写" maxLength={80} disabled={disabled} onChange={(event) => onChange({ space: space ?? "", stage: event.target.value })} /><datalist id={`${id}-stages`}>{facets.data?.stages.map((value) => <option key={value} value={value} />)}</datalist></Field></div><p className="text-xs text-muted">标签只用于整理与筛选，独立于保存时的适用条件；未知时留空。</p></section>;
}
