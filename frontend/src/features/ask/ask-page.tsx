/** Ask: grounded Q&A over the atom knowledge base. Stateless multi-turn —
 * each turn re-retrieves; history only gives the model follow-up context.
 * Assistant answers are Markdown with [n] citations; cited refs render as
 * chips that deep-link into the video workbench at the evidence timestamp. */
import { useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowUp, Loader2, MessagesSquare } from "lucide-react";
import type { AskRef } from "@/lib/api-types";
import { clearAskSession, patchAsk, submitAsk, useReadingSession } from "@/lib/reading-session";
import { useReadingScroll } from "@/lib/url-state";
import { EvidenceLink } from "@/components/shared/evidence-link";
import { KnowledgeDetails } from "@/components/shared/knowledge-details";
import { ProjectContextBar } from "@/components/shared/project-context-bar";
import { SaveDecisionButton } from "@/features/decisions/decision-dialog";
import { conditionsEqual, conditionsText } from "@/features/project/project-context-editor";
import { videosQuery } from "@/lib/queries";
import { PageHeader, ErrorState, EmptyState } from "@/components/shared/states";
import { MarkdownView } from "@/components/shared/markdown-view";
import { NatureBadge } from "@/components/shared/knowledge-tags";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/field";
import { cn } from "@/lib/cn";
import { RelationNotices } from "@/components/shared/derivation-notice";

const SUGGESTIONS = [
  "卫生间淋浴区防水要做多高？",
  "美缝什么时候做最好？",
  "水电验收要注意哪些硬指标？",
];

function fmtTs(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

function citedNs(answer: string): number[] {
  const ns: number[] = [];
  for (const m of answer.matchAll(/\[(\d{1,3})\]/g)) {
    const n = Number(m[1]);
    if (!ns.includes(n)) ns.push(n);
  }
  return ns;
}

function RefChips({ refs, cited, expanded, onExpand }: { refs: AskRef[]; cited: number[]; expanded: boolean; onExpand: (open: boolean) => void }) {
  const byN = new Map(refs.map((r) => [r.n, r]));
  const shown = cited.map((n) => byN.get(n)).filter(Boolean) as AskRef[];
  if (shown.length === 0) return null;
  const chip = (r: AskRef, linkable: boolean) => {
    const fullTitle = r.video_title || r.video || r.atom_id;
    const inner = (
      <>
        <span className="font-mono text-[10.5px] text-muted">[{r.n}]</span>
        <NatureBadge nature={r.evidence_nature} size="sm" />
        <span className="truncate" title={`${fullTitle}：${r.claim}`}>
          {fullTitle}
        </span>
        <span className="font-mono text-[10.5px] text-muted">{fmtTs(r.ms)}</span>
      </>
    );
    const cls =
      "inline-flex max-w-[240px] items-center gap-1 rounded-ctl border border-line-2 bg-surface-2 px-2 py-0.5 text-[11.5px] text-ink-2 transition-colors hover:border-acc hover:text-acc";
    // refs without a video render as a static chip, not a dead "#" link
    // that stays keyboard-focusable (pointer-events-none only blocks mouse)
    return linkable && r.video ? (
      <EvidenceLink key={r.n} to={`/videos/${encodeURIComponent(r.video)}?t=${r.ms}`} className={cls}>
        {inner}
      </EvidenceLink>
    ) : (
      <span key={r.n} className={cls}>
        {inner}
      </span>
    );
  };
  return (
    <div className="mt-2.5 space-y-2">
      <div className="flex flex-wrap gap-1.5">
        <span className="text-[11px] leading-6 text-muted">引用来源:</span>
        {shown.map((r) => chip(r, true))}
      </div>
      <details open={expanded} onToggle={(event) => { if (event.currentTarget.open !== expanded) onExpand(event.currentTarget.open); }}>
        <summary className="cursor-pointer text-[12px] text-muted">查看引用的适用条件与例外</summary>
        {shown.map((ref) => <section key={ref.n} className="mt-2">
          <p className="text-[12.5px] text-ink">[{ref.n}] {ref.claim}</p>
          <KnowledgeDetails knowledge={ref} compact />
        </section>)}
      </details>
    </div>
  );
}

export default function AskPage() {
  const { ask, storageNotice } = useReadingSession();
  const { turns, draft: input, error } = ask;
  const pending = ask.status === "submitting";
  const listRef = useRef<HTMLDivElement>(null);
  useReadingScroll("ask", listRef, ask.session_id);
  // shared (cached) videos query — only used for the live KB size in the
  // empty state; polling stays adaptive/off when no jobs are active
  const { data: kb } = useQuery(videosQuery);

  // jsdom (tests) lacks Element.scrollTo — optional-call keeps it quiet there.
  const scrollToEnd = () =>
    requestAnimationFrame(() => {
      const el = listRef.current;
      if (el) el.scrollTop = el.scrollHeight;
    });

  const send = (text?: string) => {
    const question = (text ?? input).trim();
    if (!question || pending) return;
    void submitAsk(question);
    scrollToEnd();
  };
  const previousStatus = useRef(ask.status);
  useEffect(() => {
    if (previousStatus.current === "submitting" && !pending) scrollToEnd();
    previousStatus.current = ask.status;
  }, [ask.status, pending]);

  return (
    <div className="mx-auto flex h-[calc(100vh-3rem)] w-full max-w-[900px] flex-col px-4 py-5 supports-[height:100svh]:h-[calc(100svh-3rem)] md:px-6">
      <PageHeader
        title="知识库问答"
        actions={<Button variant="outline" size="sm" onClick={clearAskSession}>清空会话</Button>}
        desc={
          <>
            回答基于已入库的知识原子,引用可跳转到视频对应时间点;涉及争议的主题会并列双方观点
          </>
        }
      />

      <ProjectContextBar value={ask.context} onChange={(context) => patchAsk({ context })} />

      <p className="mb-2 text-[11px] text-muted">本标签页暂存，可清空 · 最长 24 小时 / 最近 20 条消息；浏览器恢复标签页时可能保留。</p>
      {(storageNotice || ask.notice) && <p role="status" className="mb-2 text-[12px] text-st-wait">{storageNotice || ask.notice}</p>}
      <div ref={listRef} data-reading-scroll="ask" className="min-h-0 flex-1 space-y-4 overflow-y-auto overscroll-contain pb-4">
        {turns.length === 0 && !pending && (
          <EmptyState
            icon={<MessagesSquare size={30} />}
            title="向装修知识库提问"
            desc={
              <>
                回答只依据 {""}
                <span className="text-ink-2">{kb ? `${kb.stats.videos} 个视频` : "已入库视频"}</span>
                提炼的知识原子,并给出可跳转的证据来源。
              </>
            }
            action={
              <div className="flex flex-wrap justify-center gap-2">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    onClick={() => send(s)}
                    className="rounded-ctl border border-line-2 bg-surface px-3 py-1.5 text-[12.5px] text-ink-2 transition-colors hover:border-acc hover:text-acc"
                  >
                    {s}
                  </button>
                ))}
              </div>
            }
          />
        )}

        {turns.map((t) => (
          <div key={t.id} data-reading-anchor={t.id} className={cn("flex", t.role === "user" ? "justify-end" : "justify-start")}>
            {t.role === "user" ? (
              <div className="max-w-[85%] whitespace-pre-wrap rounded-panel rounded-br-sm bg-acc-soft px-3.5 py-2.5 text-[13.5px] leading-relaxed text-ink">
                {t.content}
              </div>
            ) : (
              <div className="max-w-[92%] rounded-panel rounded-bl-sm border border-line bg-surface px-4 py-3">
                {t.response?.degraded && <p className="mb-2 text-[12px] text-st-wait">本次为检索降级结果，未生成模型回答。</p>}
                {t.response?.generated_at && <p className="mb-2 text-[11px] text-muted">生成于 {t.response.generated_at} · 知识版本 {t.response.kb_revision ?? "未知"}</p>}
                <p className="mb-2 text-[11px] text-muted">本回答使用：{conditionsText(t.context?.context_snapshot ?? {})}{!conditionsEqual(t.context?.context_snapshot, ask.context?.context_snapshot) ? "（本次条件已修改，旧回答未重算）" : ""}</p>
                {t.response?.conflicts && <RelationNotices relations={t.response.conflicts.map((row) => ({ ...row, id: row.cluster_id }))} snapshotLabel="回答生成时" />}
                <MarkdownView md={t.content} className="text-[13.5px]" />
                {t.response?.refs && <RefChips refs={t.response.refs} cited={citedNs(t.content)} expanded={ask.expandedRefs[t.id] ?? false} onExpand={(open) => patchAsk({ expandedRefs: { ...ask.expandedRefs, [t.id]: open } })} />}
                <div className="mt-3"><SaveDecisionButton seed={() => ({ title: turns[Math.max(0, turns.findIndex((turn) => turn.id === t.id) - 1)]?.content.slice(0, 100) ?? "待决问题", question: turns[Math.max(0, turns.findIndex((turn) => turn.id === t.id) - 1)]?.content, context_snapshot: t.context?.context_snapshot ?? {}, content_snapshot: { kind: "answer", text: t.content }, evidence: (t.response?.refs ?? []).map((ref) => ({ target_kind: "atom", target_id: ref.atom_id, target_fingerprint: ref.target_fingerprint ?? "", label: ref.claim })) })} /></div>
              </div>
            )}
          </div>
        ))}

        {pending && (
          <div className="flex items-center gap-2 text-[12.5px] text-muted">
            <Loader2 size={14} className="animate-spin" />
            正在检索知识库并生成回答…
          </div>
        )}

        {ask.status === "interrupted" && <div role="status" className="space-y-2 text-[12.5px] text-st-wait">
          <p>上次请求已中断，未自动重新调用模型。</p>
          <Button variant="outline" size="sm" onClick={() => void submitAsk(turns.at(-1)?.content, true)}>重新发送</Button>
        </div>}

        {error && (
          <ErrorState
            error={new Error(error)}
            onRetry={() => {
              const last = [...turns].reverse().find((t) => t.role === "user");
              if (last) void submitAsk(last.content, true);
            }}
            context="问答"
          />
        )}
      </div>

      <div className="border-t border-line pt-3">
        <div className="flex items-end gap-2">
          <Textarea
            aria-label="问题输入框"
            placeholder="问点什么…(Enter 发送,Shift+Enter 换行)"
            rows={2}
            value={input}
            maxLength={12000}
            onChange={(e) => patchAsk({ draft: e.target.value })}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                send();
              }
            }}
            className="resize-none"
          />
          <Button
            size="icon"
            aria-label="发送问题"
            disabled={!input.trim() || pending}
            onClick={() => void send()}
            className="shrink-0"
          >
            {pending ? <Loader2 size={16} className="animate-spin" /> : <ArrowUp size={16} />}
          </Button>
        </div>
        <p className="mt-1.5 text-[10.5px] text-muted">
          回答由模型基于检索到的知识原子生成,开工决策请以视频原片与规范原文为准。
        </p>
      </div>
    </div>
  );
}
