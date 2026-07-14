import { NavLink } from "react-router-dom";
import {
  LayoutDashboard,
  Settings,
  Wrench,
  MessageSquare,
  Network,
  Shield,
  KeyRound,
  BookOpen,
  ChevronLeft,
  ChevronRight,
  Flame,
  ClipboardCheck,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useUIStore } from "@/stores/uiStore";
import { useAuth } from "@/api/auth";
import { useApprovals } from "@/api/hooks";
import { Separator } from "@/components/ui/separator";

// `permission: null` means always visible once signed in (e.g. the guide).
// This is cosmetic nav gating only -- the gateway independently enforces
// every request each page makes (ADR-0001 section 6).
const navItems = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, permission: "config:read" },
  { to: "/approvals", label: "Approvals", icon: ClipboardCheck, permission: "config:read" },
  { to: "/config", label: "Config", icon: Settings, permission: "config:read" },
  { to: "/tools", label: "Tools", icon: Wrench, permission: "config:read" },
  { to: "/chat", label: "Chat", icon: MessageSquare, permission: "agent:invoke" },
  { to: "/peers", label: "Peers", icon: Network, permission: "config:read" },
  { to: "/security", label: "Security", icon: Shield, permission: "config:read" },
  // Any authenticated user may mint their own API keys -- there is no
  // separate permission gate (ADR-0002 SS9).
  { to: "/api-keys", label: "API Keys", icon: KeyRound, permission: null },
  { to: "/guide", label: "Guide", icon: BookOpen, permission: null },
] as const;

/** A small molten-amber pending-count pill on the Approvals nav item -- the
 * sidebar's own "something needs you" signal. Renders nothing while
 * loading, on error, or at zero (a quiet nav the rest of the time, per the
 * Forge design's boldness budget). */
function ApprovalsBadge() {
  const { data: approvals } = useApprovals();
  const pendingCount = (approvals ?? []).filter((a) => a.status === "pending").length;

  if (pendingCount === 0) return null;

  return (
    <span
      className="ml-auto flex h-5 min-w-5 shrink-0 items-center justify-center rounded-full bg-primary px-1.5 text-[10px] font-bold text-primary-foreground"
      aria-label={`${pendingCount} pending approval${pendingCount === 1 ? "" : "s"}`}
    >
      {pendingCount}
    </span>
  );
}

export function Sidebar() {
  const { sidebarCollapsed, toggleSidebar } = useUIStore();
  const { can } = useAuth();

  const visibleItems = navItems.filter(
    (item) => item.permission === null || can(item.permission),
  );

  return (
    <aside
      className={cn(
        "flex h-screen flex-col border-r bg-sidebar text-sidebar-foreground transition-all duration-200",
        sidebarCollapsed ? "w-16" : "w-60",
      )}
    >
      {/* Logo */}
      <div className="flex h-14 items-center gap-2 border-b px-4">
        <Flame className="h-6 w-6 shrink-0 text-sidebar-primary" />
        {!sidebarCollapsed && (
          <span className="text-lg font-bold tracking-tight">Forge AI</span>
        )}
      </div>

      <Separator />

      {/* Navigation */}
      <nav className="flex-1 space-y-1 p-2">
        {visibleItems.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                isActive
                  ? "bg-sidebar-accent text-sidebar-accent-foreground"
                  : "text-sidebar-foreground/70 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground",
                sidebarCollapsed && "justify-center px-2",
              )
            }
            title={sidebarCollapsed ? label : undefined}
          >
            <Icon className="h-4 w-4 shrink-0" />
            {!sidebarCollapsed && (
              <>
                <span>{label}</span>
                {to === "/approvals" && <ApprovalsBadge />}
              </>
            )}
          </NavLink>
        ))}
      </nav>

      {/* Collapse toggle */}
      <div className="border-t p-2">
        <button
          onClick={toggleSidebar}
          className="flex w-full items-center justify-center rounded-md p-2 text-sidebar-foreground/70 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground"
          title={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          {sidebarCollapsed ? (
            <ChevronRight className="h-4 w-4" />
          ) : (
            <ChevronLeft className="h-4 w-4" />
          )}
        </button>
      </div>
    </aside>
  );
}
