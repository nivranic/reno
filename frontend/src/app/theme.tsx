/** Three-way theme: light / dark / system, persisted, class-driven.
 * The pre-paint script in index.html already applied the initial class. */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

export type ThemeChoice = "light" | "dark" | "system";
const KEY = "reno-theme";

interface ThemeApi {
  theme: ThemeChoice;
  setTheme: (t: ThemeChoice) => void;
  resolved: "light" | "dark";
}

const ThemeCtx = createContext<ThemeApi | null>(null);

export function useTheme(): ThemeApi {
  const api = useContext(ThemeCtx);
  if (!api) throw new Error("useTheme must be used within ThemeProvider");
  return api;
}

function applyClass(choice: ThemeChoice) {
  const dark =
    choice === "dark" ||
    (choice === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
  return dark ? "dark" : "light";
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<ThemeChoice>(() => {
    try {
      return (localStorage.getItem(KEY) as ThemeChoice) || "system";
    } catch {
      return "system";
    }
  });
  const [resolved, setResolved] = useState<"light" | "dark">(() => applyClass(theme));

  useEffect(() => {
    setResolved(applyClass(theme));
    if (theme !== "system") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => setResolved(applyClass("system"));
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [theme]);

  const setTheme = useCallback((t: ThemeChoice) => {
    try {
      localStorage.setItem(KEY, t);
    } catch {
      /* private mode: choice lives for this tab only */
    }
    setThemeState(t);
  }, []);

  const api = useMemo(() => ({ theme, setTheme, resolved }), [theme, setTheme, resolved]);
  return <ThemeCtx.Provider value={api}>{children}</ThemeCtx.Provider>;
}
