/** Collapsible sidebar + topbar shell.
 * Desktop: persistent sidebar (collapse state persisted). Mobile: drawer. */
import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router";
import {
  BookmarkPlus,
  FileText,
  Inbox,
  Menu,
  Monitor,
  Moon,
  Search,
  ShieldAlert,
  Sun,
  X,
} from "lucide-react";
import { cn } from "@/lib/cn";
import { useTheme } from "./theme";

const NAV = [
  { to: "/", label: "收件箱", icon: Inbox, end: true },
  { to: "/search", label: "搜索", icon: Search, end: false },
  { to: "/conflicts", label: "争议复核", icon: ShieldAlert, end: false },
  { to: "/collect", label: "采集助手", icon: BookmarkPlus, end: false },
  { to: "/reports", label: "报告", icon: FileText, end: false },
];

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

  // close the mobile drawer on navigation
  useEffect(() => {
    setDrawerOpen(false);
  }, [location.pathname]);

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
            className="absolute inset-0 bg-black/40"
            onClick={() => setDrawerOpen(false)}
            aria-hidden
          />
          <aside className="absolute inset-y-0 left-0 flex w-64 flex-col border-r border-line bg-surface shadow-xl">
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
        <header className="flex h-12 shrink-0 items-center gap-2 border-b border-line bg-surface px-3 md:px-5">
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

        {/* pages control their own width: reading pages max-w, workbench full */}
        <main className="min-w-0 flex-1 overflow-y-auto overscroll-contain">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
