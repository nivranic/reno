
import { Link } from "react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { PageHeader, ErrorState, EmptyState } from "@/components/shared/states";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/field";
import { usePersonalProject } from "./use-personal-project";
import { ProjectContextEditor, conditionsText } from "./project-context-editor";
import { PersonalReadBanner } from "./personal-read-banner";
import { MutationNotice, useReceiptMutation } from "./use-receipt-mutation";
import { usePersonalDraft } from "./use-personal-draft";
import { decisionCardsQuery, defaultProjectFor, patchProject, personalKeys, type Project } from "@/lib/personal-api";

export default function ProjectPage() {
  const access = usePersonalProject();
  return <ProjectView key={access.scope} access={access} />;
}
function ProjectView({ access }: { access: ReturnType<typeof usePersonalProject> }) {
  const project = access.project.data?.project;
  const cards = useQuery({ ...decisionCardsQuery(project?.project_id ?? "", "", 0, {}, access.read), enabled: access.readable && !!project });
  if (access.capabilities.isError || access.project.isError) return <div className="p-5"><ErrorState error={access.capabilities.error ?? access.project.error!} onRetry={() => { if (!access.readable) void access.capabilities.refetch(); else void access.project.refetch(); }} context="我的装修" /></div>;
  if (!project) return <div className="mx-auto max-w-[900px] px-4 py-5"><PageHeader title="我的装修" desc="条件可以留空，随时从一个问题或决定开始。" /><EmptyState title={access.capabilities.isPending || (access.capabilities.data?.personal_read && access.project.isPending) ? "正在读取项目…" : "个人记录暂不可用"} desc={access.reason} action={<Link to="/ask" className="text-acc hover:underline">继续知识问答</Link>} /></div>;
  return <div className="mx-auto w-full max-w-[900px] px-4 py-5 md:px-6"><PageHeader title={project.name} desc="记录你家的条件、为什么这样选，以及还需要核实什么。资料均可选。" />
    <PersonalReadBanner capabilities={access.capabilities.data} writable={access.writable} reason={access.reason} />
    <div className="mb-5 grid gap-3 sm:grid-cols-3">
      <Link to="/decisions" className="rounded-panel border border-line bg-surface p-4"><h2 className="font-semibold text-ink">个人决定</h2><p className="mt-1 text-sm text-muted">{cards.data ? `${cards.data.total} 条记录` : "查看选择与待决问题"}</p></Link>
      <Link to="/sources" className="rounded-panel border border-line bg-surface p-4"><h2 className="font-semibold text-ink">依据资料</h2><p className="mt-1 text-sm text-muted">补充原文、版本与核对记录</p></Link>
      <Link to="/checks" className="rounded-panel border border-line bg-surface p-4"><h2 className="font-semibold text-ink">现场核对</h2><p className="mt-1 text-sm text-muted">逐项观察、整改与复检</p></Link>
    </div>
    {cards.data?.cards.some((card) => card.needs_recheck) && <p className="mb-4 text-[13px] text-st-wait">近期决定中有依据发生变化，请到个人决定逐项复看；原结论未自动改动。</p>}
    {access.writable ? <ProjectEditor key={project.project_id} project={project} writable epoch={access.capabilities.data!.primary_epoch} /> : <section className="space-y-3 rounded-panel border border-line bg-surface p-4"><h2 className="text-[15px] font-semibold">项目资料</h2><dl className="grid gap-3 sm:grid-cols-2">{([
      ["city", "城市"], ["house_type", "房屋类型"], ["area", "面积"], ["renovation_mode", "装修方式"], ["budget_range", "预算范围"], ["current_stage", "当前阶段"],
    ] as const).map(([key, label]) => <div key={key}><dt className="text-xs text-muted">{label}</dt><dd className="mt-1 text-[13px]">{project.profile[key] || "未定"}</dd></div>)}</dl><p className="text-[13px]">项目条件：{conditionsText(project.profile.conditions ?? {})}</p></section>}
  </div>;
}
function ProjectEditor({ project, writable, epoch }: { project: Project; writable: boolean; epoch: number }) {
  const access = usePersonalProject(); const qc = useQueryClient();
  const draftKey = `project:${project.project_id}:profile`;
  const { draft, setDraft, clear, notice } = usePersonalDraft({ scope: access.scope, key: draftKey, label: "项目资料", initial: () => ({ name: project.name, profile: project.profile, baseRevision: project.revision }),
    describe: (value) => [`项目名称：${value.name}`, ...([["city", "城市"], ["house_type", "房屋类型"], ["area", "面积"], ["renovation_mode", "装修方式"], ["budget_range", "预算范围"], ["current_stage", "当前阶段"]] as const).map(([key, label]) => `${label}：${value.profile[key] ?? ""}`), `项目条件：${conditionsText(value.profile.conditions ?? {})}`, `草稿基于版本：${value.baseRevision}`].join("\n") });
  const { name, profile, baseRevision } = draft;
  const setName = (name: string) => setDraft((value) => ({ ...value, name }));
  const setProfile = (profile: Project["profile"]) => setDraft((value) => ({ ...value, profile }));
  const setBaseRevision = (baseRevision: number) => setDraft((value) => ({ ...value, baseRevision }));
  const save = useReceiptMutation((request: Parameters<typeof patchProject>[1], signal) => patchProject(project.project_id, request, signal), (result) => { qc.setQueryData(defaultProjectFor(access.read).queryKey, { enabled: true, project: result.project }); setDraft({ name: result.project.name, profile: result.project.profile, baseRevision: result.project.revision }); clear(); }, { enabled: writable && access.writable, owner: access.scope, draftKey });
  return <form className="space-y-4 rounded-panel border border-line bg-surface p-4" onSubmit={(event) => { event.preventDefault(); save.submit({ name, profile }, epoch, baseRevision); }}>
    <h2 className="text-[15px] font-semibold">项目资料</h2><Field label="项目名称"><Input aria-label="项目名称" value={name} onChange={(event) => setName(event.target.value)} disabled={!writable || save.unknown || save.isPending} /></Field>
    <details><summary className="cursor-pointer text-[13px] text-muted">可选资料（无需填写姓名或门牌）</summary><div className="mt-3 grid gap-3 sm:grid-cols-2">{([
      ["city", "城市"], ["house_type", "房屋类型"], ["area", "面积"], ["renovation_mode", "装修方式"], ["budget_range", "预算范围"], ["current_stage", "当前阶段"],
    ] as const).map(([key, label]) => <Field key={key} label={label}><Input aria-label={label} value={profile[key] ?? ""} onChange={(event) => setProfile({ ...profile, [key]: event.target.value })} disabled={!writable || save.unknown || save.isPending} /></Field>)}</div></details>
    <h3 className="text-[14px] font-medium">已确认的项目条件</h3><ProjectContextEditor value={profile.conditions ?? {}} onChange={(conditions) => setProfile({ ...profile, conditions })} disabled={!writable || save.unknown || save.isPending} />
    <p className="text-xs text-muted">{notice}</p><MutationNotice mutation={save} />
    {save.error && !save.unknown && <div className="space-y-2"><p className="text-[12px] text-muted">当前服务器版本 {project.revision}；草稿基于 {baseRevision}。</p><Button size="sm" variant="outline" onClick={() => { setBaseRevision(project.revision); save.reset(); }}>以最新版本为基础保留草稿</Button><Button size="sm" variant="ghost" onClick={() => void qc.invalidateQueries({ queryKey: personalKeys.project })}>查看最新记录</Button></div>}
    <Button type="submit" disabled={!writable || !name.trim() || save.isPending || save.unknown}>{save.isPending ? "正在保存…" : "保存项目资料"}</Button>{save.isSuccess && <p role="status" className="text-[12px] text-st-ok">项目资料已保存。已有回答与决定仍保留原条件。</p>}
  </form>;
}
