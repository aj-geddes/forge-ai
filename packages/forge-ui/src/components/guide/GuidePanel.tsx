import { useEffect, useMemo } from "react";
import { useLocation, useNavigate, Link } from "react-router-dom";
import {
  X,
  ExternalLink,
  Cpu,
  FileText,
  Wrench,
  Globe,
  Hammer,
  GitBranch,
  FlaskConical,
  MessageSquare,
  Radio,
  Network,
  ShieldCheck,
  Search,
  KeyRound,
  Fingerprint,
  Gauge,
  Key,
  Scale,
  ScrollText,
  HeartPulse,
  AlertTriangle,
  Unplug,
  Bot,
  Server,
  Blocks,
  Shield,
  Zap,
  RefreshCw,
  LayoutDashboard,
  Tag,
  Brain,
  type LucideIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useGuideStore } from "@/stores/guideStore";
import { sectionMap, type GuideSection } from "@/content/index";
import { cn } from "@/lib/utils";

const pathToSectionId: Record<string, string> = {
  "/": "getting-started",
  "/config": "config-reference",
  "/tools": "tools-guide",
  "/chat": "chat-guide",
  "/peers": "peers-guide",
  "/security": "security-guide",
  "/guide": "getting-started",
};

const iconMap: Record<string, LucideIcon> = {
  Cpu,
  FileText,
  Wrench,
  Globe,
  Hammer,
  GitBranch,
  FlaskConical,
  MessageSquare,
  Radio,
  Network,
  ShieldCheck,
  Search,
  KeyRound,
  Fingerprint,
  Gauge,
  Key,
  Scale,
  ScrollText,
  HeartPulse,
  AlertTriangle,
  Unplug,
  Bot,
  Server,
  Blocks,
  Shield,
  Zap,
  RefreshCw,
  LayoutDashboard,
  Tag,
  Brain,
};

function getIcon(name: string): LucideIcon {
  return iconMap[name] ?? Cpu;
}

export function GuidePanel() {
  const { panelOpen, closePanel, currentSection } = useGuideStore();
  const location = useLocation();
  const navigate = useNavigate();

  const sectionId = currentSection ?? pathToSectionId[location.pathname] ?? "getting-started";
  const section: GuideSection | undefined = sectionMap[sectionId];

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape" && panelOpen) {
        closePanel();
      }
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [panelOpen, closePanel]);

  const content = useMemo(() => {
    if (!section) return null;
    return section;
  }, [section]);

  if (!panelOpen || !content) return null;

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-40 bg-black/20"
        onClick={closePanel}
      />
      {/* Panel */}
      <div
        className={cn(
          "fixed right-0 top-0 z-50 flex h-full w-full max-w-md flex-col border-l bg-background shadow-xl",
          "animate-in slide-in-from-right duration-200",
        )}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b px-4 py-3">
          <h2 className="text-lg font-semibold">{content.title}</h2>
          <Button variant="ghost" size="icon" onClick={closePanel}>
            <X className="h-4 w-4" />
          </Button>
        </div>

        {/* Content */}
        <ScrollArea className="flex-1 p-4">
          <div className="space-y-6">
            {/* Overview */}
            <p className="text-sm text-muted-foreground">{content.overview}</p>

            {/* Concepts */}
            {content.concepts.length > 0 && (
              <div className="space-y-3">
                <h3 className="text-sm font-semibold">Key Concepts</h3>
                {content.concepts.slice(0, 4).map((concept) => {
                  const Icon = getIcon(concept.icon);
                  return (
                    <div key={concept.title} className="flex gap-3">
                      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-secondary">
                        <Icon className="h-4 w-4 text-secondary-foreground" />
                      </div>
                      <div>
                        <p className="text-sm font-medium">{concept.title}</p>
                        <p className="text-xs text-muted-foreground">
                          {concept.description}
                        </p>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}

            {/* Quick steps */}
            {content.steps.length > 0 && (
              <div className="space-y-2">
                <h3 className="text-sm font-semibold">Quick Steps</h3>
                <ol className="space-y-2 text-sm">
                  {content.steps.slice(0, 4).map((step, i) => (
                    <li key={step.title} className="flex gap-2">
                      <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-primary text-xs font-medium text-primary-foreground">
                        {i + 1}
                      </span>
                      <span className="text-muted-foreground">{step.title}</span>
                    </li>
                  ))}
                </ol>
              </div>
            )}

            {/* Try It */}
            {content.tryIt && (
              <Button
                className="w-full"
                onClick={() => {
                  navigate(content.tryIt!.path);
                  closePanel();
                }}
              >
                {content.tryIt.label}
                <ExternalLink className="ml-2 h-3 w-3" />
              </Button>
            )}
          </div>
        </ScrollArea>

        {/* Footer */}
        <div className="border-t px-4 py-3">
          <Link
            to={`/guide?section=${sectionId}`}
            onClick={closePanel}
            className="flex items-center justify-center gap-2 text-sm text-primary hover:underline"
          >
            View full guide
            <ExternalLink className="h-3 w-3" />
          </Link>
        </div>
      </div>
    </>
  );
}
