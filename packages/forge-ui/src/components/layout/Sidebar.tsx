import { NavLink } from "react-router-dom";
import {
  LayoutDashboard,
  Settings,
  Wrench,
  MessageSquare,
  Network,
  Shield,
  BookOpen,
  ChevronLeft,
  ChevronRight,
  Flame,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useUIStore } from "@/stores/uiStore";
import { Separator } from "@/components/ui/separator";

const navItems = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard },
  { to: "/config", label: "Config", icon: Settings },
  { to: "/tools", label: "Tools", icon: Wrench },
  { to: "/chat", label: "Chat", icon: MessageSquare },
  { to: "/peers", label: "Peers", icon: Network },
  { to: "/security", label: "Security", icon: Shield },
  { to: "/guide", label: "Guide", icon: BookOpen },
] as const;

export function Sidebar() {
  const { sidebarCollapsed, toggleSidebar } = useUIStore();

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
        {navItems.map(({ to, label, icon: Icon }) => (
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
            {!sidebarCollapsed && <span>{label}</span>}
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
