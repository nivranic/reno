/** Atom knowledge panel: claims, params, and their evidence chips.
 * Clicking a chip selects that specific evidence + locates to its ms; card
 * body click locates to the atom's first evidence. Atoms with no evidence
 * are shown honestly and cannot fake a jump. */
import { useMemo, useState } from "react";
import { Link2Off } from "lucide-react";
import type { WorkbenchAtom } from "@/lib/api-types";
import { fmtMs } from "@/lib/time";
import { cn } from "@/lib/cn";
import { PolarityBadge, AtomStatusBadge } from "@/components/shared/status-badge";
import { ModalityTag } from "@/components/shared/modality-tag";
import { Input, Select } from "@/components/ui/field";
import { useWorkbench } from "./workbench-store";

export function AtomPanel({ atoms, className }: { atoms: WorkbenchAtom[]; className?: string }) {
  const store = useWorkbench();
  const selectedAtomId = store((s) => s.selectedAtomId);
  const [sortBy, setSortBy] = useState<"time" | "confidence">("time");
  const [polarity, setPolarity] = useState("");
  const [text, setText] = useState("");

  const sorted = useMemo(() => {
    const withMs = atoms.filter(
      (a) => !polarity || a.polarity === polarity,
    );
    const filtered = text.trim()
      ? withMs.filter((a) => a.claim.toLowerCase().includes(text.trim().toLowerCase()))
      : withMs;
    const firstMs = (a: WorkbenchAtom) => a.evidence[0]?.ms ?? Number.MAX_SAFE_INTEGER;
    return [...filtered].sort((a, b) =>
      sortBy === "time"
        ? firstMs(a) - firstMs(b)
        : (b.confidence ?? 0) - (a.confidence ?? 0) || firstMs(a) - firstMs(b),
    );
  }, [atoms, sortBy, polarity, text]);

  return (
    <div className={cn("flex min-h-0 flex-col", className)}>
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="text-[13px] font-semibold text-ink">
          知识原子 <span className="font-mono text-muted">{sorted.length}</span>
        </span>
        <div className="flex items-center gap-1.5">
          <Select
            aria-label="排序方式"
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value as "time" | "confidence")}
            className="h-7 w-[104px] text-xs"
          >
            <option value="time">按时间</option>
            <option value="confidence">按置信度</option>
          </Select>
          <Select
            aria-label="按立场筛选"
            value={polarity}
            onChange={(e) => setPolarity(e.target.value)}
            className="h-7 w-[88px] text-xs"
          >
            <option value="">全部立场</option>
            <option value="recommend">推荐</option>
            <option value="avoid">避坑</option>
            <option value="require">规范要求</option>
            <option value="neutral">中性</option>
            <option value="optional">可选</option>
          </Select>
        </div>
      </div>
      <Input
        aria-label="按内容筛选原子"
        placeholder="筛选原子内容…"
        value={text}
        onChange={(e) => setText(e.target.value)}
        className="mb-2 h-8 text-[13px]"
      />
      <div className="min-h-0 flex-1 space-y-2 overflow-y-auto overscroll-contain pr-1">
        {sorted.length === 0 ? (
          <p className="px-2 py-8 text-center text-[13px] text-muted">没有匹配的知识原子</p>
        ) : null}
        {sorted.map((a) => (
          <AtomCard
            key={a.id}
            atom={a}
            selected={a.id === selectedAtomId}
            onSelect={() => {
              if (a.evidence.length === 0) {
                store.getState().selectEvidence(a.id, null);
                return;
              }
              store.getState().locate(a.evidence[0].ms, { atomId: a.id, evId: a.evidence[0].id });
            }}
            onEvClick={(evId) => {
              const ev = a.evidence.find((e) => e.id === evId);
              if (ev) store.getState().locate(ev.ms, { atomId: a.id, evId: ev.id });
            }}
          />
        ))}
      </div>
    </div>
  );
}

function AtomCard({
  atom,
  selected,
  onSelect,
  onEvClick,
}: {
  atom: WorkbenchAtom;
  selected: boolean;
  onSelect: () => void;
  onEvClick: (evId: string) => void;
}) {
  return (
    <article
      onClick={onSelect}
      className={cn(
        "cursor-pointer rounded-panel border bg-surface p-3 transition-all duration-150",
        selected ? "border-acc shadow-sm ring-1 ring-acc-line" : "border-line hover:border-line-2",
      )}
    >
      <div className="flex items-start gap-2">
        <PolarityBadge polarity={atom.polarity} className="mt-0.5 shrink-0" />
        <p className="min-w-0 flex-1 text-[13.5px] font-medium leading-relaxed text-ink">
          {atom.claim}
        </p>
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11.5px] text-muted">
        <span>{atom.category ?? "—"}</span>
        <span aria-hidden>·</span>
        <span>{atom.space ?? "—"}</span>
        <span aria-hidden>·</span>
        <AtomStatusBadge status={atom.status} />
        {typeof atom.confidence === "number" ? (
          <span
            title={`模型置信度 ${atom.confidence}(仅供参考,不等于权威程度)`}
            className="font-mono tabular-nums"
          >
            conf {atom.confidence.toFixed(2)}
          </span>
        ) : null}
      </div>
      {atom.parameters && atom.parameters.length > 0 ? (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {atom.parameters.map((p, i) => (
            <span
              key={i}
              className={cn(
                "rounded border px-1.5 py-0.5 font-mono text-[10.5px]",
                p.param_status === "unnormalized"
                  ? "border-st-wait-line bg-st-wait-bg text-st-wait"
                  : "border-line bg-surface-2 text-ink-2",
              )}
            >
              {p.name}={p.value}
              {p.unit ?? ""}
            </span>
          ))}
        </div>
      ) : null}
      <div className="mt-2 border-t border-line/70 pt-1.5">
        {atom.evidence.length === 0 ? (
          <span className="inline-flex items-center gap-1 text-[11.5px] text-muted">
            <Link2Off size={11} /> 无证据引用
          </span>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {atom.evidence.map((e) => (
              <button
                key={e.id}
                title={e.text?.slice(0, 80) ?? ""}
                onClick={(ev) => {
                  ev.stopPropagation();
                  onEvClick(e.id);
                }}
                className="inline-flex cursor-pointer items-center gap-1 rounded border border-line bg-surface-2 px-1.5 py-0.5 text-[10.5px] text-ink-2 transition-colors hover:border-acc hover:text-acc"
              >
                <ModalityTag mod={e.mod} size="sm" />
                <span className="font-mono tabular-nums">{fmtMs(e.ms)}</span>
              </button>
            ))}
          </div>
        )}
      </div>
    </article>
  );
}
