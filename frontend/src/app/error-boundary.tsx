/** Router error boundary: keeps the diagnostic visible and offers recovery. */
import { isRouteErrorResponse, useNavigate, useRouteError } from "react-router";
import { AlertOctagon } from "lucide-react";

export function RouteErrorBoundary() {
  const error = useRouteError();
  const navigate = useNavigate();
  let title = "页面出现问题";
  let detail = "未知错误";
  if (isRouteErrorResponse(error)) {
    title = `HTTP ${error.status}`;
    detail = error.statusText || error.data?.toString() || "";
  } else if (error instanceof Error) {
    detail = error.message;
  }
  return (
    <div className="mx-auto flex max-w-xl flex-col items-center gap-3 px-6 py-24 text-center">
      <AlertOctagon size={34} className="text-st-bad" />
      <h1 className="text-lg font-semibold text-ink">{title}</h1>
      <p className="break-all font-mono text-xs text-muted">{detail}</p>
      <div className="mt-2 flex gap-2">
        <button
          onClick={() => navigate(0)}
          className="cursor-pointer rounded-ctl bg-acc px-4 py-2 text-sm font-medium text-white"
        >
          重新加载
        </button>
        <button
          onClick={() => navigate("/")}
          className="cursor-pointer rounded-ctl border border-line-2 bg-surface px-4 py-2 text-sm font-medium text-ink-2"
        >
          回到收件箱
        </button>
      </div>
    </div>
  );
}
