/** Ask: grounded Q&A over the atom knowledge base. Stateless multi-turn —
 * each turn re-retrieves; history only gives the model follow-up context.
 * Assistant answers are Markdown with [n] citations; cited refs render as
 * chips that deep-link into the video workbench at the evidence timestamp. */
import { useRef, useState } from "react";
import { Link } from "react-router";
import { useMutation } from "@tanstack/react-query";
import { ArrowUp, Loader2, MessagesSquare, ShieldAlert } from "lucide-react";
import { postAsk } from "@/lib/api";
import type { AskConflictInfo, AskHistoryTurn, AskRef } from "@/lib/api-types";
import { PageHeader, ErrorState } from "@/components/shared/states";
import { MarkdownView } from "@/components/shared/markdown-view";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/field";
import { cn } from "@/lib/cn";

interface Turn extends AskHistoryTurn {
  refs?: AskRef[];
  conflicts?: AskConflictInfo[];
}

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

function RefChips({ refs, cited }: { refs: AskRef[]; cited: number[] }) {
  const byN = new Map(refs.map((r) => [r.n, r]));
  const shown = cited.map((n) => byN.get(n)).filter(Boolean) as AskRef[];
  if (shown.length === 0) return null;
  return (
    <div className="mt-2.5 flex flex-wrap gap-1.5">
      <span className="text-[11px] leading-6 text-muted">引用来源:</span>
      {shown.map((r) => (
        <Link
          key={r.n}
          to={r.video ? `/videos/${r.video}?t=${r.ms}` : "#"}
          title={r.claim}
          className="inline-flex max-w-[240px] items-center gap-1 rounded-ctl border border-line-2 bg-surface-2 px-2 py-0.5 text-[11.5px] text-ink-2 transition-colors hover:border-acc hover:text-acc"
        >
          <span className="font-mono text-[10.5px] text-muted">[{r.n}]</span>
          <span className="truncate">{r.video_title || r.video || r.atom_id}</span>
          <span className="font-mono text-[10.5px] text-muted">{fmtTs(r.ms)}</span>
        </Link>
      ))}
    </div>
  );
}

export default function AskPage() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const mut = useMutation({
    mutationFn: ({ question, history }: { question: string; history: AskHistoryTurn[] }) =>
      postAsk(question, history),
    onSuccess: (res) => {
      setTurns((t) => [
        ...t,
        { role: "assistant", content: res.answer, refs: res.refs, conflicts: res.conflicts },
      ]);
      setError(null);
      scrollToEnd();
    },
    onError: (e) => setError(e instanceof Error ? e.message : String(e)),
  });

  // jsdom (tests) lacks Element.scrollTo — optional-call keeps it quiet there.
  const scrollToEnd = () =>
    requestAnimationFrame(() => {
      const el = listRef.current;
      el?.scrollTo?.({ top: el.scrollHeight, behavior: "smooth" });
    });

  const send = (text?: string) => {
    const question = (text ?? input).trim();
    if (!question || mut.isPending) return;
    setTurns((t) => [...t, { role: "user", content: question }]);
    setInput("");
    const history: AskHistoryTurn[] = turns.slice(-4).map(({ role, content }) => ({ role, content }));
    mut.mutate({ question, history });
    scrollToEnd();
  };

  return (
    <div className="mx-auto flex h-[calc(100vh-4rem)] w-full max-w-[900px] flex-col px-4 py-5 md:px-6">
      <PageHeader
        title="知识库问答"
        desc={
          <>
            回答基于已入库的知识原子,引用可跳转到视频对应时间点;涉及争议的主题会并列双方观点
          </>
        }
      />

      <div ref={listRef} className="min-h-0 flex-1 space-y-4 overflow-y-auto pb-4">
        {turns.length === 0 && !mut.isPending && (
          <div className="mt-10 flex flex-col items-center gap-4 text-center">
            <MessagesSquare size={36} className="text-muted" />
            <p className="max-w-[420px] text-[13px] leading-relaxed text-muted">
              向装修知识库提问,回答只依据 {""}
              <span className="text-ink-2">28 个视频</span>
              提炼的知识原子,并给出可跳转的证据来源。
            </p>
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
          </div>
        )}

        {turns.map((t, i) => (
          <div key={i} className={cn("flex", t.role === "user" ? "justify-end" : "justify-start")}>
            {t.role === "user" ? (
              <div className="max-w-[85%] whitespace-pre-wrap rounded-panel rounded-br-sm bg-acc-soft px-3.5 py-2.5 text-[13.5px] leading-relaxed text-ink">
                {t.content}
              </div>
            ) : (
              <div className="max-w-[92%] rounded-panel rounded-bl-sm border border-line bg-surface px-4 py-3">
                {t.conflicts && t.conflicts.length > 0 && (
                  <div className="mb-2 flex items-center gap-1.5 rounded-ctl bg-amber-500/10 px-2.5 py-1.5 text-[11.5px] text-amber-700 dark:text-amber-400">
                    <ShieldAlert size={13} />
                    <span>
                      本回答涉及 {t.conflicts.length} 个有分歧的主题(
                      {t.conflicts.map((c) => c.topic).join("、")}),双方观点已并列 ·{" "}
                      <Link to="/conflicts" className="underline underline-offset-2">
                        去复核
                      </Link>
                    </span>
                  </div>
                )}
                <MarkdownView md={t.content} className="text-[13.5px]" />
                {t.refs && <RefChips refs={t.refs} cited={citedNs(t.content)} />}
              </div>
            )}
          </div>
        ))}

        {mut.isPending && (
          <div className="flex items-center gap-2 text-[12.5px] text-muted">
            <Loader2 size={14} className="animate-spin" />
            正在检索知识库并生成回答…
          </div>
        )}

        {error && (
          <ErrorState
            error={new Error(error)}
            onRetry={() => {
              const last = [...turns].reverse().find((t) => t.role === "user");
              if (last) {
                setTurns((t) => t.slice(0, t.findIndex((x) => x === last)));
                send(last.content);
              }
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
            disabled={mut.isPending}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                send();
              }
            }}
            className="resize-none"
          />
          <Button
            size="sm"
            aria-label="发送问题"
            disabled={!input.trim() || mut.isPending}
            onClick={() => void send()}
            className="h-9 shrink-0"
          >
            {mut.isPending ? <Loader2 size={15} className="animate-spin" /> : <ArrowUp size={15} />}
          </Button>
        </div>
        <p className="mt-1.5 text-[10.5px] text-muted">
          回答由模型基于检索到的知识原子生成,开工决策请以视频原片与规范原文为准。
        </p>
      </div>
    </div>
  );
}
