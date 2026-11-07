import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTrigger } from "@/components/ui/dialog";
import { usePersonalDraftArchive } from "./use-personal-draft";

export function PersonalDraftArchive() {
  const { drafts, pendingCount } = usePersonalDraftArchive(); const [open, setOpen] = useState(false);
  if (!drafts.length && !pendingCount) return null;
  const download = (label: string, text: string, pending: boolean) => {
    const body = `# ${label}\n\n本文件是本次页面暂存的填写内容，不是已保存记录。\n${pending ? "\n保存结果尚未确认；请先核实原操作结果，勿重复新建。\n" : ""}\n${text}\n`;
    const url = URL.createObjectURL(new Blob([body], { type: "text/markdown;charset=utf-8" }));
    const link = document.createElement("a"); link.href = url; link.download = "装修暂存草稿.md"; link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  return <Dialog open={open} onOpenChange={setOpen}>
    <DialogTrigger asChild><Button size="sm" variant="outline">{drafts.length ? `暂存草稿（${drafts.length}）` : `待确认保存（${pendingCount}）`}</Button></DialogTrigger>
    <DialogContent title="暂存草稿" description="这里只查看或下载原填写内容，不会自动提交到当前实例。刷新或关闭页面后暂存会丢失；附件文件不包含在下载中。">
      <div className="space-y-3"><p className="text-[13px]">本次页面保留了 {drafts.length} 份未提交草稿{pendingCount ? `，另有 ${pendingCount} 项保存结果待核实` : ""}。</p>
      {drafts.map((draft) => <details key={draft.id} className="rounded-ctl border border-line p-3"><summary className="cursor-pointer font-medium">{draft.label}{draft.pending ? " · 保存结果待核实" : ""}</summary>
        <pre className="my-3 whitespace-pre-wrap break-words font-sans text-xs">{draft.text}</pre>
        <Button size="sm" variant="outline" onClick={() => download(draft.label, draft.text, draft.pending)}>下载这份草稿</Button>
      </details>)}
      {pendingCount > 0 && <p className="text-xs text-st-wait">取消连接不代表服务端未保存。回到原编辑范围后，请先查询提交结果；切换实例或任期不会自动重发。</p>}
    </div></DialogContent>
  </Dialog>;
}
