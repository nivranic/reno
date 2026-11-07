import { Link, useLocation, type LinkProps } from "react-router";
import { captureReadingPositions } from "@/lib/url-state";

export interface EvidenceReturn { to: string; label: string; key: string }
/** Cold deep links fall back to the inbox, never blind history.back(). */
export function evidenceReturn(state: unknown): EvidenceReturn | undefined {
  const value = state && typeof state === "object" && "readingReturn" in state ? state.readingReturn : undefined;
  if (!value || typeof value !== "object" || !("to" in value) || !("key" in value)) return;
  if (typeof value.to !== "string" || typeof value.key !== "string" || !/^\/(ask|compare|search|reconciliation)(\?|$)/.test(value.to)) return;
  return { to: value.to, key: value.key, label: value.to.startsWith("/ask") ? "返回问答" : value.to.startsWith("/compare") ? "返回比较" : value.to.startsWith("/reconciliation") ? "返回对账" : "返回搜索" };
}
export function EvidenceLink({ onClick, state, ...props }: LinkProps) {
  const location = useLocation();
  return <Link {...props} state={{ ...state, readingReturn: { to: `${location.pathname}${location.search}`, key: location.key } }} onClick={(event) => { captureReadingPositions(); onClick?.(event); }} />;
}
