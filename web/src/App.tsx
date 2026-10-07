import { useCallback, useEffect, useState } from "react";
import { api, type Status } from "./lib/api";
import { Icon, Toast } from "./components/ui";
import logo from "./assets/logo.svg";
import Dashboard from "./pages/Dashboard";
import Meetings from "./pages/Meetings";
import SearchPage from "./pages/Search";
import Settings from "./pages/Settings";
import SyncPage from "./pages/Sync";

const PAGES = [
  { id: "home", label: "Dashboard", icon: "dashboard" },
  { id: "sync", label: "Sync", icon: "sync" },
  { id: "meetings", label: "Meetings", icon: "event_note" },
  { id: "search", label: "Search", icon: "manage_search" },
  { id: "settings", label: "Settings", icon: "settings" },
] as const;

type PageId = (typeof PAGES)[number]["id"];

export default function App() {
  const [page, setPage] = useState<PageId>("home");
  const [status, setStatus] = useState<Status | null>(null);
  const [offline, setOffline] = useState(false);
  const [toast, setToast] = useState<{ message: string; tone: "ok" | "error" } | null>(null);
  const [theme, setTheme] = useState(
    () => document.documentElement.dataset.theme ?? "dark",
  );

  const refresh = useCallback(() => {
    api
      .status()
      .then((next) => {
        setStatus(next);
        setOffline(false);
      })
      .catch(() => setOffline(true));
  }, []);

  useEffect(() => {
    refresh();
    // Polled rather than pushed: the status payload is tiny and a socket for
    // one small object would be more moving parts than it saves. The live sync
    // log has its own event stream.
    const timer = setInterval(refresh, 5000);
    return () => clearInterval(timer);
  }, [refresh]);

  const notify = useCallback((message: string, tone: "ok" | "error" = "ok") => {
    setToast({ message, tone });
    setTimeout(() => setToast(null), 6000);
  }, []);

  function toggleTheme() {
    const next = theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem("fath.theme", next);
    } catch {
      // A blocked localStorage is not a reason to refuse to change the theme.
    }
    setTheme(next);
  }

  return (
    <div className="min-h-full">
      <header className="sticky top-0 z-30 border-b border-surface0 bg-mantle/95 backdrop-blur">
        <div className="mx-auto flex max-w-[86rem] items-center gap-4 px-4 py-2.5">
          <div className="flex items-center gap-2 font-semibold text-mauve">
            <img src={logo} alt="" className="h-6 w-6" />
            fathom-helper
          </div>

          <nav className="flex flex-1 gap-1">
            {PAGES.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => setPage(item.id)}
                className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm transition ${
                  page === item.id
                    ? "bg-surface0 text-text"
                    : "text-subtext hover:bg-surface0/50 hover:text-text"
                }`}
              >
                <Icon name={item.icon} className="text-[18px]" />
                <span className="hidden sm:inline">{item.label}</span>
              </button>
            ))}
          </nav>

          <div className="flex items-center gap-3 text-xs text-overlay">
            {offline ? (
              <span className="flex items-center gap-1 text-red">
                <Icon name="cloud_off" className="text-[16px]" />
                server stopped
              </span>
            ) : status?.running ? (
              <span className="flex items-center gap-1 text-blue">
                <Icon name="sync" className="animate-spin text-[16px]" />
                syncing
              </span>
            ) : (
              <span className="hidden md:inline">{status?.meetings ?? 0} meetings</span>
            )}
            <button
              type="button"
              onClick={toggleTheme}
              title="Switch theme"
              className="rounded-md p-1 hover:bg-surface0 hover:text-text"
            >
              <Icon name={theme === "dark" ? "light_mode" : "dark_mode"} className="text-[18px]" />
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[86rem] p-4">
        {page === "home" && (
          <Dashboard
            status={status}
            onChanged={refresh}
            notify={notify}
            goTo={(next) => setPage(next as PageId)}
          />
        )}
        {page === "sync" && (
          <SyncPage status={status} onChanged={refresh} notify={notify} />
        )}
        {page === "meetings" && <Meetings />}
        {page === "search" && <SearchPage />}
        {page === "settings" && <Settings notify={notify} onChanged={refresh} />}
      </main>

      {toast && (
        <Toast message={toast.message} tone={toast.tone} onDismiss={() => setToast(null)} />
      )}
    </div>
  );
}
