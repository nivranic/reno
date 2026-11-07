import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { uploadAttachment, personalKeys, type Attachment, type MutationEnvelope } from "@/lib/personal-api";
import { Button } from "@/components/ui/button";
import { MutationNotice, useReceiptMutation } from "@/features/project/use-receipt-mutation";
import { usePersonalProject } from "@/features/project/use-personal-project";
import { usePersonalDraft } from "@/features/project/use-personal-draft";

export function AttachmentUploader({ sourceId, projectId, epoch, disabled, onReady, onFileSelected, draftKey: editKey }: { sourceId: string; projectId: string; epoch: number; disabled?: boolean; onReady?: (attachment: Attachment) => void; onFileSelected?: (name: string | undefined) => void; draftKey?: string }) {
  const access = usePersonalProject(); const qc = useQueryClient();
  const draftKey = editKey ?? `source:${sourceId}:attachment`;
  const { draft: file, setDraft: setFile, clear, notice } = usePersonalDraft<File | undefined>({ scope: access.read?.scope ?? "", key: draftKey, label: "待上传附件", initial: () => undefined, describe: (file) => file ? `待上传文件：${file.name}\n大小：${file.size} 字节\n类型：${file.type || "未提供"}\n文件仅在内存保留，下载草稿不包含文件内容。` : "未选择文件" });
  const [preview, setPreview] = useState(""); const [validation, setValidation] = useState("");
  const upload = useReceiptMutation((body: MutationEnvelope<{ file: File }>, signal) => uploadAttachment(sourceId, body.changes.file, body.operation_id, body.primary_epoch, signal), (result) => { if (result.attachment.state !== "ready") { setValidation("附件尚未就绪，不能用于记录。请重新查询来源状态。"); return; } setFile(undefined); clear(); onFileSelected?.(undefined); void qc.invalidateQueries({ queryKey: personalKeys.sources(projectId) }); onReady?.(result.attachment); }, { enabled: access.uploadable && !disabled, owner: access.scope, draftKey });
  useEffect(() => { if (!file || !file.type.startsWith("image/")) { setPreview(""); return; } const url = URL.createObjectURL(file); setPreview(url); return () => URL.revokeObjectURL(url); }, [file]);
  const choose = (next?: File) => {
    setValidation(""); setFile(undefined); onFileSelected?.(undefined); if (!next) return;
    const pdf = next.name.toLowerCase().endsWith(".pdf"); const image = /\.(jpe?g|png)$/i.test(next.name);
    if (!pdf && !image) { setValidation("只支持 JPEG、PNG 和 PDF；HEIC 请先转换后重选。"); return; }
    if (next.size > (pdf ? 20 : 10) * 1024 * 1024) { setValidation(pdf ? "PDF 不能超过 20 MiB。" : "图片不能超过 10 MiB。"); return; }
    setFile(next); onFileSelected?.(next.name);
  };
  return <div className="space-y-2 rounded-ctl border border-line p-3"><label className="block text-[13px] font-medium">添加资料或现场照片<input aria-label="选择附件" type="file" accept=".jpg,.jpeg,.png,.pdf" disabled={!access.uploadable || disabled || upload.isPending || upload.unknown} className="mt-2 block w-full text-xs" onChange={(event) => { choose(event.target.files?.[0]); event.currentTarget.value = ""; }} /></label>
    <p className="text-xs text-muted">图片最多 10 MiB，PDF 最多 20 MiB。未上传的文件不会随刷新保留，刷新后请重新选择。</p>
    {file && <p className="text-xs text-muted">待上传：{file.name} · {(file.size / 1024).toFixed(1)} KiB</p>}{preview && <img src={preview} alt="待上传照片预览" className="max-h-48 rounded-ctl object-contain" />}
    <p className="text-xs text-muted">{notice}</p>{validation && <p role="alert" className="text-xs text-st-wait">{validation}</p>}<MutationNotice mutation={upload} />
    <Button size="sm" variant="outline" disabled={!access.uploadable || disabled || !file || upload.isPending || upload.unknown} onClick={() => upload.submit({ file: file! }, epoch)}>{upload.isPending ? "正在上传…" : "上传所选文件"}</Button>
    {upload.isSuccess && !file && !validation && <p role="status" className="text-xs text-st-ok">附件已就绪，可用于保存记录。</p>}
  </div>;
}
