import type { PersonalCapabilities } from "@/lib/personal-api";

export function PersonalReadBanner({ capabilities, writable, reason }: { capabilities?: PersonalCapabilities; writable: boolean; reason: string }) {
  if (writable) return null;
  const mirror = capabilities?.personal_mode === "mirror" ? capabilities.personal_mirror : undefined;
  return <div role="status" className="mb-4 space-y-1 rounded-ctl border border-line bg-surface p-3 text-[13px] text-muted">
    <p>{mirror ? "云端只读镜像。修改请在来源主实例完成，再同步到这里。" : reason}</p>
    {mirror && <><p>来源主实例：{mirror.source_node_id} · 主实例代次 {mirror.source_primary_epoch}</p><p>镜像时间：{mirror.committed_at} · 镜像版本 {mirror.installed_revision}</p></>}
  </div>;
}
