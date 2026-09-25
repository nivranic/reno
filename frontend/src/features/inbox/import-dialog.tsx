/** Import dialog: single URL + batch (one per line). Shows per-line parse
 * results before submit, and honest outcomes after (已接受 ≠ 处理完成).
 * Final validation/dedup is always backend-side; frontend parsing is a hint.
 * Phone: fullscreen panel (ui/dialog), no auto keyboard, clipboard read only
 * behind an explicit user tap (§7.3). */
import { useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { CheckCircle2, CircleAlert, ClipboardPaste, Loader2 } from "lucide-react";
import { postImportBatch, postImportUrl } from "@/lib/api";
import { errMessage } from "@/app/query-utils";
import { Dialog, DialogContent, DialogTrigger } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/field";
import { Tabs } from "@/components/ui/tabs";
import { useToast } from "@/components/shared/toaster";

const URL_RE = /^https?:\/\/\S+$/i;

// clipboard read needs a user gesture AND secure context; the button only
// renders when the API exists, rejection falls back to system long-press
const canPaste = typeof navigator !== "undefined" && !!navigator.clipboard?.readText;

interface ParsedLine {
  raw: string;
  kind: "url" | "invalid" | "dupe";
}

function parseBatch(text: string): ParsedLine[] {
  const seen = new Set<string>();
  return text
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)
    .map((raw) => {
      if (!URL_RE.test(raw)) return { raw, kind: "invalid" as const };
      const norm = raw.replace(/[?#].*$/, "");
      if (seen.has(norm)) return { raw, kind: "dupe" as const };
      seen.add(norm);
      return { raw, kind: "url" as const };
    });
}

export function ImportDialog({
  open,
  onOpenChange,
  onImported,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onImported: () => void;
}) {
  const toast = useToast();
  const [tab, setTab] = useState<"single" | "batch">("single");
  const [url, setUrl] = useState("");
  const [batch, setBatch] = useState("");

  // §7.3: paste is an explicit user action — never read the clipboard on mount
  const pasteInto = async (apply: (text: string) => void) => {
    try {
      const text = await navigator.clipboard.readText();
      if (text.trim()) apply(text.trim());
    } catch {
      toast.error("浏览器未授权剪贴板,请长按输入框使用系统粘贴", { key: "paste" });
    }
  };

  const parsed = useMemo(() => parseBatch(batch), [batch]);
  const validCount = parsed.filter((p) => p.kind === "url").length;

  const single = useMutation({
    mutationFn: () => postImportUrl(url.trim()),
    onSuccess: (res) => {
      onImported();
      toast.success(
        res.status === "duplicate" ? "该视频已在库中,未重复导入" : `已接受导入(${res.video_id ?? ""}),已加入处理队列`,
      );
      setUrl("");
      onOpenChange(false);
    },
    onError: (e) => toast.error(`导入失败:${errMessage(e)}`, { key: "import-single" }),
  });

  const batchMut = useMutation({
    mutationFn: () =>
      postImportBatch(
        parsed.filter((p) => p.kind === "url").map((p) => p.raw),
      ),
    onSuccess: (res) => {
      onImported();
      setBatch("");
      const failNote = res.failed > 0 ? `,失败 ${res.failed}(见下方)` : "";
      toast.success(`批量提交完成:新增 ${res.imported} · 重复 ${res.duplicate}${failNote}`);
      if (res.failed === 0) onOpenChange(false);
    },
    onError: (e) => toast.error(`批量导入失败:${errMessage(e)}`, { key: "import-batch" }),
  });

  const batchResult = batchMut.data;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogTrigger asChild>
        <span className="hidden" aria-hidden />
      </DialogTrigger>
      <DialogContent
        title="导入视频"
        description="导入后自动进入处理流水线(媒体→语音→OCR→原子化),状态见收件箱轮询。"
      >
        <Tabs
          ariaLabel="导入方式"
          value={tab}
          onChange={setTab}
          className="mb-4"
          items={[
            { value: "single", label: "单条链接" },
            { value: "batch", label: "批量(一行一条)" },
          ]}
        />

        {tab === "single" ? (
          <form
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              if (url.trim() && !single.isPending) single.mutate();
            }}
          >
            <div className="flex gap-2">
              <Input
                aria-label="视频链接"
                autoFocus={typeof window !== "undefined" && window.matchMedia("(min-width: 768px)").matches}
                inputMode="url"
                placeholder="粘贴 B站 / 抖音 视频链接或本地文件路径"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
              />
              {canPaste ? (
                <Button
                  variant="outline"
                  size="icon"
                  title="从剪贴板粘贴"
                  aria-label="从剪贴板粘贴链接"
                  onClick={() => void pasteInto(setUrl)}
                >
                  <ClipboardPaste size={16} />
                </Button>
              ) : null}
            </div>
            <div className="flex items-center justify-end gap-2">
              <Button variant="ghost" size="sm" onClick={() => onOpenChange(false)}>
                取消
              </Button>
              <Button type="submit" size="sm" disabled={!url.trim() || single.isPending}>
                {single.isPending ? <Loader2 size={14} className="animate-spin" /> : null}
                {single.isPending ? "提交中…" : "导入并处理"}
              </Button>
            </div>
          </form>
        ) : (
          <form
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              if (validCount > 0 && !batchMut.isPending) batchMut.mutate();
            }}
          >
            <Textarea
              aria-label="批量链接"
              rows={6}
              placeholder={"一行一条链接(B站/抖音/本地路径可混合),最多 200 条\n例如:\nhttps://www.bilibili.com/video/BVxxxx\nhttps://v.douyin.com/xxxx/"}
              value={batch}
              onChange={(e) => setBatch(e.target.value)}
              className="font-mono text-[12.5px]"
            />
            {batch.trim() ? (
              <div className="rounded-ctl border border-line bg-surface-2/50 p-2.5 text-[12px]">
                <div className="flex gap-3 text-ink-2">
                  <span>
                    有效 <b className="font-mono">{validCount}</b>
                  </span>
                  <span className="text-muted">
                    无效 <b className="font-mono">{parsed.filter((p) => p.kind === "invalid").length}</b>
                  </span>
                  <span className="text-muted">
                    重复行 <b className="font-mono">{parsed.filter((p) => p.kind === "dupe").length}</b>
                  </span>
                </div>
                {validCount > 200 ? (
                  <p className="mt-1 text-st-bad">超过 200 条上限,后端只取前 200 条。</p>
                ) : null}
                <p className="mt-1 text-muted">无效/重复行不会提交;重复判定以服务端为准。</p>
              </div>
            ) : null}
            {batchResult && batchResult.failed > 0 ? (
              <div className="rounded-ctl border border-st-wait-line bg-st-wait-bg p-2.5 text-[12px] text-st-wait">
                <p className="flex items-center gap-1 font-medium">
                  <CircleAlert size={13} /> {batchResult.failed} 条失败(保留其余成功项)
                </p>
                <ul className="mt-1 space-y-0.5 font-mono text-[11px] opacity-80">
                  {batchResult.failed_sample.map((s, i) => (
                    <li key={i} className="truncate">
                      {s}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
            <div className="flex items-center justify-end gap-2">
              <Button variant="ghost" size="sm" onClick={() => onOpenChange(false)}>
                关闭
              </Button>
              <Button type="submit" size="sm" disabled={validCount === 0 || validCount > 200 || batchMut.isPending}>
                {batchMut.isPending ? <Loader2 size={14} className="animate-spin" /> : <CheckCircle2 size={14} />}
                {batchMut.isPending ? "提交中…" : `提交 ${validCount} 条`}
              </Button>
            </div>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
