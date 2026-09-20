/** Search: IME-safe input, debounced as-you-type + immediate Enter, URL-held
 * filters, token highlighting via <mark> (no raw HTML), ms-precise deep links. */
import { useDeferredValue, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { Search as SearchIcon } from "lucide-react";
import { facetsQuery, searchQuery } from "@/lib/queries";
import type { SearchRow } from "@/lib/api-types";
import { fmtMs } from "@/lib/time";
import { useScrollRestore } from "@/lib/url-state";
import { PageHeader, EmptyState, ErrorState, ListSkeleton, InlineSpinner } from "@/components/shared/states";
import { PolarityBadge } from "@/components/shared/status-badge";
import { ModalityTag } from "@/components/shared/modality-tag";
import { Input, Select } from "@/components/ui/field";

export default function SearchPage() {
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const category = params.get("category") ?? "";
  const space = params.get("space") ?? "";

  // local input state; committed to URL (which drives the query) debounced or on submit
  const [input, setInput] = useState(q);
  const [composing, setComposing] = useState(false);
  const deferredInput = useDeferredValue(input);

  useEffect(() => {
    if (composing) return; // mid-IME: never commit a half-composed string
    if (deferredInput === q) return;
    const timer = window.setTimeout(() => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (deferredInput.trim()) next.set("q", deferredInput.trim());
          else next.delete("q");
          return next;
        },
        { replace: true },
      );
    }, 350);
    return () => window.clearTimeout(timer);
  }, [deferredInput, q, composing, setParams]);

  useEffect(() => setInput(q), [q]); // back/forward sync

  const facets = useQuery(facetsQuery);
  const results = useQuery(searchQuery(q, category, space));
  const scroll = useScrollRestore(`search:${q}:${category}:${space}`);
  useEffect(() => {
    scroll.restore();
    return scroll.save;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, category, space]);

  const setParam = (key: string, v: string) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (v) next.set(key, v);
        else next.delete(key);
        return next;
      },
      { replace: true },
    );

  const tokens = useMemo(
    () =>
      [q.trim(), ...q.trim().split(/\s+/).filter((t) => t.length > 1)].filter(
        Boolean,
      ),
    [q],
  );

  return (
    <div className="mx-auto w-full max-w-[880px] px-4 py-5 md:px-6">
      <PageHeader title="搜索" desc="全库知识原子检索(匹配主张与依据文本)" />

      <form
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          setParam("q", input.trim());
        }}
        className="mb-3 flex gap-2"
      >
        <div className="relative flex-1">
          <SearchIcon size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
          <Input
            aria-label="搜索知识原子"
            autoFocus
            placeholder="如:防水 高度 / 插座 / 留缝"
            value={input}
            onCompositionStart={() => setComposing(true)}
            onCompositionEnd={(e) => {
              setComposing(false);
              setInput(e.currentTarget.value);
            }}
            onChange={(e) => setInput(e.target.value)}
            className="pl-9"
          />
        </div>
        <Select
          aria-label="按工种筛选"
          value={category}
          onChange={(e) => setParam("category", e.target.value)}
          className="w-[110px]"
        >
          <option value="">全部工种</option>
          {facets.data?.categories.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </Select>
        <Select
          aria-label="按空间筛选"
          value={space}
          onChange={(e) => setParam("space", e.target.value)}
          className="w-[110px]"
        >
          <option value="">全部空间</option>
          {facets.data?.spaces.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </Select>
      </form>

      {!q.trim() ? (
        <EmptyState
          icon={<SearchIcon size={30} />}
          title="输入关键词开始搜索"
          desc="支持中文分词匹配;结果可一键跳到视频证据时间点。"
        />
      ) : results.isError ? (
        <ErrorState error={results.error} onRetry={() => void results.refetch()} context="搜索" />
      ) : results.isPending ? (
        <ListSkeleton rows={6} />
      ) : results.isFetching ? (
        <InlineSpinner label="搜索中…" />
      ) : results.data!.results.length === 0 ? (
        <EmptyState title={`没有 "${q}" 的匹配结果`} desc="尝试更短的词,或清空工种/空间筛选。" />
      ) : (
        <>
          <p className="mb-2.5 text-[12.5px] text-muted">
            {results.data!.results.length} 条结果(最多展示 80)
          </p>
          <div className="space-y-2">
            {results.data!.results.map((r) => (
              <ResultCard key={r.id} row={r} tokens={tokens} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function ResultCard({ row, tokens }: { row: SearchRow; tokens: string[] }) {
  return (
    <Link
      to={row.video ? `/videos/${row.video}?t=${row.ms}` : "#"}
      className={row.video ? "" : "pointer-events-none"}
      aria-label={`跳转到视频 ${row.video_title || row.video},时间 ${fmtMs(row.ms)}`}
    >
      <div className="rounded-panel border border-line bg-surface p-3.5 transition-colors hover:border-acc-line hover:bg-acc-soft/20">
        <div className="flex items-start gap-2">
          <PolarityBadge polarity={row.polarity} className="mt-0.5 shrink-0" />
          <p className="min-w-0 flex-1 text-[13.5px] font-medium leading-relaxed text-ink">
            <Highlight text={row.claim} tokens={tokens} />
          </p>
        </div>
        {row.evidence_text ? (
          <p className="mt-1 line-clamp-1 pl-1 text-[12px] text-muted">
            依据:{row.evidence_text}
          </p>
        ) : null}
        <div className="mt-1.5 flex flex-wrap items-center gap-x-2.5 gap-y-1 pl-1 text-[11.5px] text-muted">
          <span>{row.category ?? "—"}</span>
          <span aria-hidden>·</span>
          <span>{row.space ?? "—"}</span>
          {row.mod ? <ModalityTag mod={row.mod} size="sm" /> : null}
          <span className="font-mono tabular-nums">{fmtMs(row.ms)}</span>
          <span className="min-w-0 truncate">
            {row.video_title || row.video || "无来源"}
          </span>
        </div>
      </div>
    </Link>
  );
}

function Highlight({ text, tokens }: { text: string; tokens: string[] }) {
  if (tokens.length === 0) return <>{text}</>;
  const escaped = tokens.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const re = new RegExp(`(${escaped.join("|")})`, "gi");
  const parts = text.split(re);
  // split() with a capture group places exactly the matched tokens at odd indices
  return (
    <>
      {parts.map((p, i) =>
        i % 2 === 1 ? (
          <mark key={i} className="rounded-[3px] bg-vis-bg px-0.5 text-vis">
            {p}
          </mark>
        ) : (
          <span key={i}>{p}</span>
        ),
      )}
    </>
  );
}
