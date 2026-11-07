import type { Conditions } from "@/lib/personal-api";
import { Field, Input } from "@/components/ui/field";

export const CONDITION_KEYS = ["空间", "部位", "基层", "材料", "规格", "环境", "工艺", "设备", "地域", "时间", "适用对象"];
export function conditionsText(value: Conditions) { return Object.entries(value).filter(([, text]) => text !== "").sort(([a], [b]) => { const ai = CONDITION_KEYS.indexOf(a); const bi = CONDITION_KEYS.indexOf(b); return (ai < 0 ? 100 : ai) - (bi < 0 ? 100 : bi) || a.localeCompare(b); }).map(([key, text]) => `${key}：${text}`).join("；") || "未设置条件"; }
export function conditionsEqual(a: Conditions = {}, b: Conditions = {}) { const normalize = (value: Conditions) => JSON.stringify(Object.entries(value).filter(([, text]) => text !== "").sort(([a], [b]) => a.localeCompare(b))); return normalize(a) === normalize(b); }
export function ProjectContextEditor({ value, onChange, disabled = false }: { value: Conditions; onChange: (value: Conditions) => void; disabled?: boolean }) {
  const fields = (keys: string[]) => keys.map((key) => <Field key={key} label={key}><Input aria-label={`条件${key}`} value={value[key] ?? ""} maxLength={300} disabled={disabled} placeholder="可留空，不猜测" onChange={(event) => {
    const next = { ...value }; if (event.target.value) next[key] = event.target.value; else delete next[key]; onChange(next);
  }} /></Field>);
  const extra = Object.keys(value).filter((key) => !CONDITION_KEYS.includes(key));
  return <div className="space-y-3"><div className="grid gap-3 sm:grid-cols-2">{fields(CONDITION_KEYS.slice(0, 4))}</div>
    <details><summary className="cursor-pointer text-[13px] text-muted">更多条件（均可选）</summary><div className="mt-3 grid gap-3 sm:grid-cols-2">{fields(CONDITION_KEYS.slice(4))}</div></details>
    {extra.length > 0 && <p className="text-[12px] text-muted">历史条件（原样保留，未自动映射）：{extra.map((key) => `${key}：${value[key]}`).join("；")}</p>}
  </div>;
}
