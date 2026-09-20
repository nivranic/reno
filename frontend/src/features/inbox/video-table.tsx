/** Video list: real table on md+, readable cards on phones (not a shrunken
 * table). Row click → workbench. */
import { Link } from "react-router";
import type { VideoRow } from "@/lib/api-types";
import { fmtDate, fmtDur } from "@/lib/time";
import { VideoStatusBadge } from "@/components/shared/status-badge";
import { cn } from "@/lib/cn";

export function VideoTable({ rows }: { rows: VideoRow[] }) {
  return (
    <>
      {/* desktop table */}
      <div className="hidden overflow-hidden rounded-panel border border-line md:block">
        <table className="w-full border-collapse text-[13.5px]">
          <thead>
            <tr className="border-b border-line bg-surface-2/60 text-left text-[12px] text-muted">
              <th scope="col" className="px-4 py-2 font-medium">视频</th>
              <th scope="col" className="w-[110px] px-3 py-2 font-medium">状态</th>
              <th scope="col" className="w-[70px] px-3 py-2 text-right font-medium">原子</th>
              <th scope="col" className="w-[70px] px-3 py-2 text-right font-medium">时长</th>
              <th scope="col" className="w-[150px] px-3 py-2 font-medium">UP主</th>
              <th scope="col" className="w-[110px] px-3 py-2 font-medium">导入于</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((v) => (
              <tr
                key={v.video_id}
                className="group border-b border-line/70 last:border-0 hover:bg-acc-soft/30"
              >
                <td className="max-w-0 px-4 py-2.5">
                  <Link
                    to={`/videos/${v.video_id}`}
                    className="block truncate font-medium text-ink hover:text-acc"
                    title={v.title ?? v.video_id}
                  >
                    {v.title ?? v.video_id}
                  </Link>
                  {v.status === "error" && v.error_detail ? (
                    <p className="truncate text-[11.5px] text-st-bad" title={v.error_detail}>
                      {v.error_detail}
                    </p>
                  ) : null}
                </td>
                <td className="px-3 py-2.5">
                  <VideoStatusBadge status={v.status} />
                </td>
                <td className="px-3 py-2.5 text-right font-mono tabular-nums text-ink-2">
                  {v.atoms}
                </td>
                <td className="px-3 py-2.5 text-right font-mono tabular-nums text-ink-2">
                  {fmtDur(v.duration_ms)}
                </td>
                <td className="max-w-[150px] truncate px-3 py-2.5 text-muted">{v.author ?? "—"}</td>
                <td className="px-3 py-2.5 font-mono text-[11.5px] tabular-nums text-muted">
                  {fmtDate(v.imported_at)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* phone cards */}
      <div className="space-y-2 md:hidden">
        {rows.map((v) => (
          <Link
            key={v.video_id}
            to={`/videos/${v.video_id}`}
            className={cn(
              "block rounded-panel border border-line bg-surface px-3.5 py-3 transition-colors hover:border-acc-line",
            )}
          >
            <div className="flex items-start justify-between gap-2">
              <p className="min-w-0 flex-1 text-[13.5px] font-medium leading-snug text-ink line-clamp-2">
                {v.title ?? v.video_id}
              </p>
              <VideoStatusBadge status={v.status} />
            </div>
            <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[11.5px] text-muted">
              <span>{v.author ?? "—"}</span>
              <span className="font-mono tabular-nums">{fmtDur(v.duration_ms)}</span>
              <span className="font-mono tabular-nums">{v.atoms} 原子</span>
              <span className="font-mono">{fmtDate(v.imported_at)}</span>
            </div>
            {v.status === "error" && v.error_detail ? (
              <p className="mt-1 line-clamp-2 text-[11.5px] text-st-bad">{v.error_detail}</p>
            ) : null}
          </Link>
        ))}
      </div>
    </>
  );
}
