/** Collapsible sidebar + topbar shell.
 * Desktop (md+): persistent sidebar (collapse state persisted).
 * Phone (<md): bottom main nav on top-level pages; detail/reading pages
 * (workbench, ask, report reading) drop it so content owns the screen —
 * the drawer keeps every entry reachable everywhere. */
import { useEffect, useRef, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useSearchParams } from "react-router";
import {
  BookmarkPlus,
  FileText,
  Inbox,
  Menu,
  MessagesSquare,
  Monitor,
  Moon,
  Search,
  ShieldAlert,
  Sun,
  X,
} from "lucide-react";
import { cn } from "@/lib/cn";
import { setToken } from "@/lib/api";
import { useTheme } from "./theme";

const NAV = [
  { to: "/", label: "收件箱", icon: Inbox, end: true },
  { to: "/ask", label: "知识问答", icon: MessagesSquare, end: false },
  { to: "/search", label: "搜索", icon: Search, end: false },
  { to: "/conflicts", label: "争议复核", icon: ShieldAlert, end: false },
  { to: "/collect", label: "采集助手", icon: BookmarkPlus, end: false, bottom: false },
  { to: "/reports", label: "报告", icon: FileText, end: false },
];
// §4.1: the phone bottom bar carries the primary pages; 采集助手 stays in the
// drawer and the import flow (auxiliary entry), not as a fake nav page
const BOTTOM_NAV = NAV.filter((n) => n.to !== "/collect");

export const IS_MOCK = import.meta.env.MODE === "mock";

function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const next = theme === "light" ? "dark" : theme === "dark" ? "system" : "light";
  const label = theme === "light" ? "浅色" : theme === "dark" ? "深色" : "跟随系统";
  return (
    <button
      onClick={() => setTheme(next)}
      title={`主题:${label}(点击切换)`}
      aria-label={`切换主题,当前${label}`}
      className="flex h-8 w-8 cursor-pointer items-center justify-center rounded-ctl text-muted transition-colors hover:bg-surface-2 hover:text-ink"
    >
      {theme === "light" ? (
        <Sun size={16} />
      ) : theme === "dark" ? (
        <Moon size={16} />
      ) : (
        <Monitor size={16} />
      )}
    </button>
  );
}

function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <nav aria-label="主导航" className="flex flex-1 flex-col gap-1 p-3">
      {NAV.map(({ to, label, icon: Icon, end }) => (
        <NavLink
          key={to}
          to={to}
          end={end}
          onClick={onNavigate}
          className={({ isActive }) =>
            cn(
              "flex items-center gap-2.5 rounded-ctl px-3 py-2 text-sm font-medium transition-colors duration-150",
              isActive
                ? "bg-acc-soft text-acc"
                : "text-ink-2 hover:bg-surface-2 hover:text-ink",
            )
          }
        >
          <Icon size={16} className="shrink-0" />
          {label}
        </NavLink>
      ))}
    </nav>
  );
}

function SidebarInner() {
  return (
    <>
      <Link
        to="/"
        className="flex items-center gap-2.5 border-b border-line px-5 py-4"
      >
        <img src="/favicon.svg" alt="" width={26} height={26} className="rounded-[6px]" />
        <div className="leading-tight">
          <div className="text-[15px] font-bold text-ink">reno</div>
          <div className="text-[11px] text-muted">装修知识工作台</div>
        </div>
      </Link>
      <NavLinks />
      <div className="border-t border-line p-4 text-[11px] leading-relaxed text-muted">
        证据可追溯 · 决策由你做
      </div>
    </>
  );
}

export function RootLayout() {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const location = useLocation();
  const [params] = useSearchParams();
  // LAN/mobile deployments with auth_token: any 401 from the API opens the
  // token gate; saving reloads so every query re-fires with the new token
  const [tokenPrompt, setTokenPrompt] = useState(false);
  const [tokenInput, setTokenInput] = useState("");
  useEffect(() => {
    const onUnauthorized = () => setTokenPrompt(true);
    window.addEventListener("reno-unauthorized", onUnauthorized);
    return () => window.removeEventListener("reno-unauthorized", onUnauthorized);
  }, []);

  // close the mobile drawer on navigation
  useEffect(() => {
    setDrawerOpen(false);
  }, [location.pathname]);

  // phone: top-level pages carry the bottom nav; detail/reading pages drop it
  // (§4.2: one primary fixed area at a time). Report reading state lives in
  // ?r= so the hide rule and deep links share one source of truth.
  const readingReport = location.pathname === "/reports" && params.has("r");
  const navHidden =
    location.pathname.startsWith("/videos/") ||
    location.pathname === "/ask" ||
    readingReport;

  if (tokenPrompt) {
    return (
      <div className="flex h-dvh flex-col items-center justify-center gap-4 px-6">
        <div className="w-full max-w-[360px] rounded-panel border border-line bg-surface p-5">
          <h1 className="mb-1 text-[17px] font-semibold text-ink">需要访问令牌</h1>
          <p className="mb-4 text-[12.5px] leading-relaxed text-muted">
            本服务已开启访问控制。输入配置文件 auth_token 中设置的访问令牌后继续。
          </p>
          <input
            aria-label="访问令牌"
            type="password"
            autoFocus
            value={tokenInput}
            onChange={(e) => setTokenInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && tokenInput.trim()) {
                setToken(tokenInput.trim());
                window.location.reload();
              }
            }}
            className="mb-3 w-full rounded-ctl border border-line-2 bg-surface px-3 py-2 text-sm text-ink focus:border-acc focus:outline-none"
            placeholder="访问令牌"
          />
          <button
            onClick={() => {
              if (!tokenInput.trim()) return;
              setToken(tokenInput.trim());
              window.location.reload();
            }}
            className="w-full cursor-pointer rounded-ctl bg-acc px-4 py-2 text-sm font-semibold text-white hover:bg-acc-strong disabled:opacity-50 dark:text-acc-contrast"
            disabled={!tokenInput.trim()}
          >
            保存并刷新
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-dvh overflow-hidden">
      {/* desktop sidebar */}
      <aside className="hidden w-56 shrink-0 flex-col border-r border-line bg-surface md:flex">
        <SidebarInner />
      </aside>

      {/* mobile drawer */}
      {drawerOpen ? (
        <div className="fixed inset-0 z-50 md:hidden">
          <div
            className="absolute inset-0 bg-black/45 backdrop-blur-[2px]"
            onClick={() => setDrawerOpen(false)}
            aria-hidden
          />
          <aside className="absolute inset-y-0 left-0 flex w-72 max-w-[85vw] flex-col border-r border-line bg-surface shadow-xl">
            <div className="flex justify-end p-2">
              <button
                aria-label="关闭导航"
                onClick={() => setDrawerOpen(false)}
                className="cursor-pointer rounded-ctl p-1.5 text-muted hover:bg-surface-2"
              >
                <X size={18} />
              </button>
            </div>
            <SidebarInner />
          </aside>
        </div>
      ) : null}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-12 shrink-0 items-center gap-2 border-b border-line bg-surface px-3 pt-[env(safe-area-inset-top)] md:px-5">
          <button
            aria-label="打开导航"
            onClick={() => setDrawerOpen(true)}
            className="cursor-pointer rounded-ctl p-1.5 text-muted hover:bg-surface-2 md:hidden"
          >
            <Menu size={18} />
          </button>
          <div className="flex-1" />
          {IS_MOCK ? (
            <span className="rounded-full border border-st-wait-line bg-st-wait-bg px-2.5 py-0.5 text-[11.5px] font-semibold text-st-wait">
              Mock 演示环境
            </span>
          ) : null}
          <ThemeToggle />
        </header>

        {/* pages control their own width: reading pages max-w, workbench full.
            bottom nav height is measured, not assumed (§15.1 text-zoom growth) */}
        <main
          className="min-w-0 flex-1 overflow-y-auto overscroll-contain pb-[var(--reno-bottom-occupy,0px)] md:pb-0"
          style={navHidden ? { paddingBottom: 0 } : undefined}
        >
          <Outlet />
        </main>

        {!navHidden ? <BottomNav /> : null}
      </div>
    </div>
  );
}

/** Phone bottom main nav (§4.1). Fixed height content area + real safe-area
 * inset; measured total height is published as --reno-bottom-occupy so main
 * content padding follows text-zoom growth instead of a magic 56px. */
function BottomNav() {
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    // published on the root element so sibling <main> can read it
    const publish = () =>
      document.documentElement.style.setProperty(
        "--reno-bottom-occupy",
        `${el.offsetHeight}px`,
      );
    publish();
    const ro = new ResizeObserver(publish);
    ro.observe(el);
    return () => {
      ro.disconnect();
      document.documentElement.style.setProperty("--reno-bottom-occupy", "0px");
    };
  }, []);
  return (
    <nav
      ref={ref}
      data-testid="bottom-nav"
      aria-label="主导航"
      className="shrink-0 border-t border-line bg-surface pb-[env(safe-area-inset-bottom)] md:hidden"
    >
      <ul className="flex items-stretch">
        {BOTTOM_NAV.map(({ to, label, icon: Icon, end }) => (
          <li key={to} className="min-w-0 flex-1">
            <NavLink
              to={to}
              end={end}
              className={({ isActive }) =>
                cn(
                  "flex min-h-[56px] flex-col items-center justify-center gap-0.5 px-1 py-1.5 text-[10.5px] leading-tight transition-colors",
                  isActive ? "text-acc" : "text-muted hover:text-ink",
                )
              }
            >
              {({ isActive }) => (
                <>
                  <Icon size={19} aria-hidden className={isActive ? "" : "opacity-80"} />
                  <span className="max-w-full truncate">{label}</span>
                </>
              )}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}
