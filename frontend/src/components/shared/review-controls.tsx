import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, postDecision } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/field";
import { errMessage } from "@/app/query-utils";
import { newOperationId } from "@/lib/id";

interface ReviewEvent {
  event_id: string; decision_id: number; atom_id?: string; conflict_id?: string;
  action: string; note?: string; revised_claim?: string; decided_at?: string;
  binding_status: string; target_snapshot?: { claim?: string };
}
const ACTION: Record<string, string> = { confirm: "确认这条知识", reject: "驳回", clear_review: "清除人工复核", accept_a: "采纳 A", accept_b: "采纳 B", both: "两者各适用" };
const BINDING: Record<string, string> = { active: "内容绑定有效", stale: "内容已改变，待核对", missing: "目标已缺失，保留历史", legacy_unbound: "旧记录未绑定当时内容，待核对", invalid_time: "记录时间异常，未生效" };

export function ReviewHistory({ kind, targetId, label = "查看复核历史" }: { kind?: "atom" | "conflict"; targetId?: string; label?: string }) {
  const [open, setOpen] = useState(false);
  const reviews = useQuery({
    queryKey: ["reviews", kind ?? "all", targetId ?? "all"], enabled: open,
    queryFn: ({ signal }) => apiGet<{ reviews: ReviewEvent[] }>(`/api/reviews?${new URLSearchParams({ ...(kind ? { kind } : {}), ...(targetId ? { target_id: targetId } : {}) })}`, signal),
  });
  return <details onClick={(event) => event.stopPropagation()} onToggle={(event) => setOpen(event.currentTarget.open)} className="mt-2 rounded-ctl border border-line p-2 text-[12px]">
    <summary className="cursor-pointer py-1 text-ink-2">{label}</summary>
    {open && <div className="mt-2 space-y-2">
      {reviews.isPending ? <p className="text-muted">正在读取历史…</p> : reviews.isError ? <p role="alert">{errMessage(reviews.error)}</p> : !reviews.data?.reviews.length ? <p className="text-muted">暂无人工复核记录</p> : reviews.data.reviews.map((event) => <div key={event.event_id || event.decision_id} className="border-t border-line pt-2">
        <p className="font-medium">{ACTION[event.action] ?? event.action} · {BINDING[event.binding_status] ?? event.binding_status}</p>
        <p className="text-muted">{event.decided_at || "原记录时间未知"}</p>
        {event.target_snapshot?.claim && <p className="mt-1 break-words">当时内容：{event.target_snapshot.claim}</p>}
        {event.note && <p className="mt-1 whitespace-pre-wrap break-words">备注：{event.note}</p>}
        {event.revised_claim && <p className="mt-1 break-words">人工修订历史：{event.revised_claim}</p>}
      </div>)}
    </div>}
  </details>;
}

/** A new user action gets one event ID; an uncertain response retries that ID. */
export function AtomReviewControls({ atomId, fingerprint }: { atomId: string; fingerprint?: string }) {
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const [saved, setSaved] = useState(false);
  const request = useRef<Parameters<typeof postDecision>[0] | null>(null);
  const mutation = useMutation({ mutationFn: postDecision, onSuccess: async () => {
    request.current = null; setSaved(true);
    await Promise.all(["video", "search", "reviews", "conflicts", "report-metadata"].map((key) => client.invalidateQueries({ queryKey: [key] })));
  } });
  const submit = (action: string) => {
    const old = request.current;
    const value = old && old.action === action && old.note === note && old.target_fingerprint === fingerprint ? old : {
      atom_id: atomId, action, note, target_fingerprint: fingerprint, event_id: newOperationId(),
    };
    request.current = value; setSaved(false); mutation.mutate(value);
  };
  return <div onClick={(event) => event.stopPropagation()} className="mt-2">
    <Button size="sm" variant="outline" onClick={() => setOpen(!open)} aria-expanded={open}>人工复核</Button>
    {open && <div className="mt-2 space-y-2 rounded-ctl border border-line p-2">
      <p className="text-[12px] text-muted">复核绑定当前内容；确认不会消除来源分歧，个人采用请另存决定。</p>
      <Textarea aria-label="复核备注" maxLength={2000} rows={2} value={note} onChange={(event) => setNote(event.target.value)} />
      <div className="flex flex-wrap gap-2">{["confirm", "reject", "clear_review"].map((action) => <Button key={action} size="sm" variant="outline" disabled={!fingerprint || mutation.isPending} onClick={() => submit(action)}>{ACTION[action]}</Button>)}</div>
      {!fingerprint && <p className="text-xs text-muted">当前内容标识不可用，请刷新后复核</p>}
      {mutation.isError && <p role="alert" className="text-xs text-st-bad">{errMessage(mutation.error)}；备注仍保留。</p>}
      {saved && <p role="status" className="text-xs text-st-ok">复核已保存</p>}
      <ReviewHistory kind="atom" targetId={atomId} />
    </div>}
  </div>;
}
