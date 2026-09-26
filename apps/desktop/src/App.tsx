import { useQuery } from "@tanstack/react-query";
import {
  BarChart3,
  BookOpen,
  Bot,
  ClipboardCheck,
  FileSearch,
  FileText,
  Gauge,
  Lock,
  ScrollText,
  Settings as SettingsIcon,
  SlidersHorizontal,
  Upload,
  Users,
} from "lucide-react";
import { useEffect } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { api } from "./api/client";
import { Button, cn } from "./components/ui";
import { useAuth } from "./lib/auth";
import AuditPage from "./pages/Audit";
import DashboardPage from "./pages/Dashboard";
import IdentityPage from "./pages/Identity";
import IngestPage from "./pages/Ingest";
import InvoiceDetailPage from "./pages/InvoiceDetail";
import InvoicesPage from "./pages/Invoices";
import LoginPage from "./pages/Login";
import RefDataPage from "./pages/RefData";
import ReportsPage from "./pages/Reports";
import ReviewPage from "./pages/Review";
import RulesPage from "./pages/Rules";
import SearchPage from "./pages/Search";
import SettingsPage from "./pages/Settings";

const NAV = [
  { to: "/", label: "Dashboard", icon: Gauge },
  { to: "/review", label: "Review queue", icon: ClipboardCheck },
  { to: "/ingest", label: "Ingest", icon: Upload },
  { to: "/invoices", label: "Invoices", icon: FileText },
  { to: "/search", label: "Search / Ask", icon: FileSearch },
  { to: "/identity", label: "Parties & patients", icon: Users },
  { to: "/rules", label: "Rules", icon: SlidersHorizontal },
  { to: "/refdata", label: "Reference data", icon: BookOpen },
  { to: "/reports", label: "Reports", icon: BarChart3 },
  { to: "/audit", label: "Audit log", icon: ScrollText, role: "ADMIN" as const },
  { to: "/settings", label: "Settings", icon: SettingsIcon },
];

/** Light theme only. */
function useLightTheme(): void {
  useEffect(() => {
    document.documentElement.dataset.theme = "light";
    localStorage.removeItem("theme");
  }, []);
}

function AiIndicator() {
  const { data } = useQuery({ queryKey: ["ai-status-lite"], queryFn: () => api.get("/dashboard"), refetchInterval: 5000 });
  const q = data?.ai?.queue;
  if (!data || data.ai?.tier === "OFF") return <span className="text-xs text-ink-3">AI off</span>;
  const running = q?.running?.[0];
  return (
    <span className="inline-flex items-center gap-1 text-xs text-ink-2" title="Background AI queue">
      <Bot className="size-3.5" aria-hidden /> {running ? `Running ${running.kind.replace("AI_", "").toLowerCase()}` : "Idle"} · {q?.queued ?? 0} queued
    </span>
  );
}

function Shell() {
  const { user, logout, lock, can, requireLogin } = useAuth();
  return (
    <div className="flex h-full">
      <nav className="flex w-56 shrink-0 flex-col border-r border-border bg-surface-2" aria-label="Main">
        <div className="px-4 py-4">
          <div className="text-sm font-semibold">ABM Invoice Analytics</div>
          <div className="text-xs text-ink-3">Duplicate-billing detection</div>
        </div>
        <ul className="flex-1 space-y-0.5 px-2">
          {NAV.filter((n) => !n.role || can(n.role)).map((n) => (
            <li key={n.to}>
              <NavLink
                to={n.to}
                end={n.to === "/"}
                className={({ isActive }) =>
                  cn("flex items-center gap-2 rounded-md px-2.5 py-1.5 text-sm", isActive ? "bg-surface font-medium text-ink shadow-sm" : "text-ink-2 hover:bg-surface hover:text-ink")
                }
              >
                <n.icon className="size-4" aria-hidden />
                {n.label}
              </NavLink>
            </li>
          ))}
        </ul>
        <div className="space-y-2 border-t border-border p-3 text-xs">
          <AiIndicator />
          <div className="text-ink-2">
            {user?.display_name} <span className="text-ink-3">· {user?.role.toLowerCase()}</span>
          </div>
          {requireLogin && (
            <div className="flex gap-1">
              <Button size="sm" variant="ghost" onClick={() => lock("Locked.")} aria-label="Lock">
                <Lock className="size-3.5" /> Lock
              </Button>
              <Button size="sm" variant="ghost" onClick={logout}>
                Sign out
              </Button>
            </div>
          )}
        </div>
      </nav>
      <main className="min-w-0 flex-1 overflow-auto p-6">
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/review" element={<ReviewPage />} />
          <Route path="/review/:flagId" element={<ReviewPage />} />
          <Route path="/ingest" element={<IngestPage />} />
          <Route path="/invoices" element={<InvoicesPage />} />
          <Route path="/invoices/:id" element={<InvoiceDetailPage />} />
          <Route path="/search" element={<SearchPage />} />
          <Route path="/identity" element={<IdentityPage />} />
          <Route path="/rules" element={<RulesPage />} />
          <Route path="/refdata" element={<RefDataPage />} />
          <Route path="/reports" element={<ReportsPage />} />
          <Route path="/audit" element={can("ADMIN") ? <AuditPage /> : <Navigate to="/" />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </main>
    </div>
  );
}

export default function App() {
  const { user, requireLogin } = useAuth();
  useLightTheme();
  if (user) return <Shell />;
  // No sign-in required: show a short "starting" screen while the automatic sign-in completes.
  if (requireLogin === false || requireLogin === null) return <Starting />;
  return <LoginPage />;
}

function Starting() {
  return (
    <div className="flex h-full items-center justify-center bg-surface-2 text-sm text-ink-2" role="status">
      Starting ABM Invoice Analytics…
    </div>
  );
}
