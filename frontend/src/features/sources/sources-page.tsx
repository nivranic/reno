import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { sourcesQuery, downloadAttachment, attachmentReadable, type SourceRecord } from "@/lib/personal-api";
import { usePersonalProject } from "@/features/project/use-personal-project";
import { PageHeader, EmptyState, ErrorState, InlineSpinner } from "@/components/shared/states";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/shared/toaster";
import { SOURCE_TYPES, SourceRecordDialog } from "./source-record-dialog";
import { AttachmentUploader } from "./attachment-uploader";
import { conditionsText } from "@/features/project/project-context-editor";
import { PersonalReadBanner } from "@/features/project/personal-read-banner";
import { isPersonalAbort, usePersonalTransfer } from "@/features/project/use-personal-transfer";

export default function SourcesPage() {
  const access = usePersonalProject();
  return <SourcesView key={access.scope} access={access} />;
}
function SourcesView({ access }: { access: ReturnType<typeof usePersonalProject> }) {
  const toast = useToast(); const transfer = usePersonalTransfer(access.read); const projectId = access.project.data?.project?.project_id ?? "";
  const query = useQuery({ ...sourcesQuery(projectId, access.read), enabled: access.readable && !!projectId }); const [open, setOpen] = useState(false); const [editing, setEditing] = useState<SourceRecord>(); const [formKey, setFormKey] = useState(0);
  return <div className="mx-auto w-full max-w-[900px] px-4 py-5 md:px-6"><PageHeader title="依据资料" desc="手工保存规范原文、产品说明或现场记录；文件上传成功不代表内容已经核实。" actions={<Button size="sm" disabled={!access.writable} onClick={() => { setEditing(undefined); setFormKey((key) => key + 1); setOpen(true); }}>添加依据</Button>} />
    <PersonalReadBanner capabilities={access.capabilities.data} writable={access.writable} reason={access.reason} />
    {!projectId ? <EmptyState title="个人记录暂不可用" desc={access.reason} /> : query.isError ? <ErrorState error={query.error} onRetry={() => void query.refetch()} context="依据资料" /> : query.isPending ? <InlineSpinner label="读取依据…" /> : !query.data.sources.length ? <EmptyState title="还没有人工依据" desc="先添加一份来源记录，再上传 JPEG、PNG 或 PDF。" /> : <div className="space-y-3">{query.data.sources.map((source) => <article key={source.source_id} className="space-y-3 rounded-panel border border-line bg-surface p-4"><div className="flex items-start justify-between gap-2"><div><h2 className="font-semibold text-ink">{source.title}</h2><p className="mt-1 text-xs text-muted">{SOURCE_TYPES[source.source_type]} · 版本 {source.revision} · {source.verification_state === "user_checked" ? "用户已核对原文" : "尚未核对"} · {({ unknown: "有效性未知", current: "用户记录为现行", superseded: "已被替代", withdrawn: "已撤销" })[source.validity]}</p></div>{access.writable && <Button size="sm" variant="outline" onClick={() => { setEditing(source); setFormKey((key) => key + 1); setOpen(true); }}>编辑记录</Button>}</div><p className="whitespace-pre-wrap text-[13px] text-ink-2">{source.excerpt || "暂无摘录"}</p><p className="text-xs text-muted">{[source.document_no, source.edition, source.locator].filter(Boolean).join(" · ") || "原文编号、版本或定位尚待补充"}</p>
      <details className="text-[12.5px] text-muted"><summary className="cursor-pointer">查看来源、适用范围与核对说明</summary><div className="mt-2 space-y-1"><p>发布方：{source.publisher || "未知"}</p><p>发布日期：{source.published_on || "未知"} · 生效日期：{source.valid_from || "未知"} · 失效日期：{source.valid_to || "未知"}</p><p>适用范围：{conditionsText(source.scope ?? {})}</p><p>核对日期：{source.checked_at || "尚未核对"}</p><p className="whitespace-pre-wrap">核对说明：{source.check_note || "暂无"}</p>{source.source_url && <p>原文链接：{/^https?:\/\//i.test(source.source_url) ? <a className="break-all text-acc hover:underline" href={source.source_url} target="_blank" rel="noopener noreferrer">{source.source_url}</a> : <span className="break-all">{source.source_url}</span>}</p>}</div></details>
      <div className="flex flex-wrap gap-2">{source.attachments.map((attachment) => <Button key={attachment.attachment_id} size="sm" variant="outline" disabled={!access.readable || !attachmentReadable(attachment, access.read)} onClick={() => void transfer((signal) => downloadAttachment(attachment, access.read, signal)).catch((error: unknown) => { if (!isPersonalAbort(error)) toast.error(error instanceof Error ? error.message : "附件读取失败"); })}>{attachment.original_name} · {attachmentReadable(attachment, access.read) ? "查看文件" : attachment.missing || attachment.read_state === "missing" ? "文件缺失" : "尚未就绪"}</Button>)}</div>
      {access.uploadable && <AttachmentUploader sourceId={source.source_id} projectId={projectId} epoch={access.capabilities.data!.primary_epoch} />}
    </article>)}</div>}
    {query.data && query.data.total > query.data.sources.length && <p className="mt-3 text-xs text-muted">当前展示前 {query.data.sources.length} / {query.data.total} 份来源；现场选择引用前请确认所需记录已加载。</p>}
    {access.writable && open && <SourceRecordDialog key={formKey} open onOpenChange={setOpen} source={editing} />}
  </div>;
}
