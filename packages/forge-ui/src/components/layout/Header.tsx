import { useLocation } from "react-router-dom";
import { HelpCircle, Moon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useUIStore } from "@/stores/uiStore";
import { useGuideStore } from "@/stores/guideStore";
import { useHealth } from "@/api/hooks";
import { cn } from "@/lib/utils";

const pageTitles: Record<string, string> = {
  "/": "Dashboard",
  "/config": "Config Builder",
  "/tools": "Tool Workshop",
  "/chat": "Chat",
  "/peers": "Peers",
  "/security": "Security",
  "/guide": "Guide",
};

export function Header() {
  const location = useLocation();
  const { darkMode, toggleDarkMode } = useUIStore();
  const { togglePanel } = useGuideStore();
  const { data: health } = useHealth();

  const title = pageTitles[location.pathname] ?? "Forge AI";
  const isHealthy = health?.status === "ok" || health?.status === "healthy";

  return (
    <header className="flex h-14 items-center justify-between border-b bg-background px-6">
      <h1 className="text-lg font-semibold">{title}</h1>

      <div className="flex items-center gap-2">
        {/* Health indicator */}
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <span
            className={cn(
              "h-2 w-2 rounded-full",
              health === undefined
                ? "bg-muted-foreground animate-pulse"
                : isHealthy
                  ? "bg-green-500"
                  : "bg-destructive",
            )}
          />
          <span className="hidden sm:inline">
            {health === undefined ? "Checking..." : isHealthy ? "Healthy" : "Unhealthy"}
          </span>
        </div>

        {/* Dark mode toggle */}
        <Button
          variant="ghost"
          size="icon"
          onClick={toggleDarkMode}
          title={darkMode ? "Switch to light mode" : "Switch to dark mode"}
        >
          {darkMode ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
        </Button>

        {/* Help button - toggles guide panel */}
        <Button
          variant="ghost"
          size="icon"
          onClick={togglePanel}
          title="Open guide"
        >
          <HelpCircle className="h-4 w-4" />
        </Button>
      </div>
    </header>
  );
}
