import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import {
  Flame,
  LayoutDashboard,
  Settings,
  Wrench,
  MessageSquare,
  X,
  type LucideIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { useGuideStore } from "@/stores/guideStore";
import { useToast } from "@/components/ui/toast";
import { cn } from "@/lib/utils";

interface TourStepDef {
  title: string;
  description: string;
  icon: LucideIcon;
  position: "center" | "left" | "left-nav";
  targetLabel?: string;
}

const tourSteps: TourStepDef[] = [
  {
    title: "Welcome to Forge AI Control Plane!",
    description:
      "This guided tour will walk you through the main features of the Control Plane. You can skip at any time or replay from the Guide page.",
    icon: Flame,
    position: "center",
  },
  {
    title: "Dashboard",
    description:
      "The Dashboard gives you an at-a-glance overview of your agent's health, active tools, recent activity, and system status.",
    icon: LayoutDashboard,
    position: "left-nav",
    targetLabel: "Dashboard",
  },
  {
    title: "Config Builder",
    description:
      "The Config Builder lets you visually edit your forge.yaml configuration. Switch between visual and YAML editing modes, with real-time validation.",
    icon: Settings,
    position: "left-nav",
    targetLabel: "Config",
  },
  {
    title: "Tool Workshop",
    description:
      "Import OpenAPI specs, create manual tools, and compose workflows. Test your tools interactively before deploying them.",
    icon: Wrench,
    position: "left-nav",
    targetLabel: "Tools",
  },
  {
    title: "Chat Interface",
    description:
      "Have conversations with your agent, see tool calls in real-time, and manage multiple chat sessions. This is where you interact with your configured agent.",
    icon: MessageSquare,
    position: "left-nav",
    targetLabel: "Chat",
  },
];

export function GuideTour() {
  const { tourActive, tourStep, tourCompleted, startTour, nextTourStep, prevTourStep, endTour } =
    useGuideStore();
  const navigate = useNavigate();
  const { toast } = useToast();

  // Auto-start tour on first visit
  useEffect(() => {
    if (!tourCompleted && !tourActive) {
      const timer = setTimeout(() => {
        startTour();
      }, 500);
      return () => clearTimeout(timer);
    }
    return undefined;
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  function handleNext() {
    const nextStepIndex = tourStep + 1;
    if (nextStepIndex >= tourSteps.length) {
      endTour();
      toast({
        title: "Tour Complete!",
        description:
          "You've completed the Forge AI tour. Visit the Guide page anytime to learn more.",
      });
      return;
    }

    // Navigate to relevant page for context
    if (nextStepIndex === 1) navigate("/");
    if (nextStepIndex === 2) navigate("/config");
    if (nextStepIndex === 3) navigate("/tools");
    if (nextStepIndex === 4) navigate("/chat");

    nextTourStep();
  }

  function handlePrev() {
    const prevStepIndex = tourStep - 1;
    if (prevStepIndex === 0) navigate("/");
    if (prevStepIndex === 1) navigate("/");
    if (prevStepIndex === 2) navigate("/config");
    if (prevStepIndex === 3) navigate("/tools");

    prevTourStep();
  }

  function handleSkip() {
    endTour();
  }

  if (!tourActive) return null;

  const step = tourSteps[tourStep];
  if (!step) return null;

  const Icon = step.icon;
  const isFirst = tourStep === 0;
  const isLast = tourStep === tourSteps.length - 1;

  return (
    <div className="fixed inset-0 z-[200]">
      {/* Overlay */}
      <div className="absolute inset-0 bg-black/50" />

      {/* Tooltip card */}
      <div
        className={cn(
          "absolute z-[201] w-full max-w-sm rounded-lg border bg-card p-6 shadow-2xl",
          step.position === "center" && "left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2",
          step.position === "left-nav" && "left-20 top-24",
          step.position === "left" && "left-8 top-24",
        )}
      >
        {/* Close button */}
        <button
          onClick={handleSkip}
          className="absolute right-3 top-3 rounded-md p-1 text-muted-foreground hover:text-foreground"
        >
          <X className="h-4 w-4" />
        </button>

        {/* Icon */}
        <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-primary">
          <Icon className="h-6 w-6 text-primary-foreground" />
        </div>

        {/* Content */}
        <h3 className="mb-2 text-lg font-semibold">{step.title}</h3>
        <p className="mb-6 text-sm text-muted-foreground">{step.description}</p>

        {/* Step counter */}
        <div className="mb-4 flex items-center gap-1.5">
          {tourSteps.map((_, i) => (
            <div
              key={i}
              className={cn(
                "h-1.5 w-1.5 rounded-full transition-colors",
                i === tourStep ? "bg-primary" : "bg-muted-foreground/30",
              )}
            />
          ))}
          <span className="ml-2 text-xs text-muted-foreground">
            {tourStep + 1} of {tourSteps.length}
          </span>
        </div>

        {/* Actions */}
        <div className="flex items-center justify-between">
          <Button variant="ghost" size="sm" onClick={handleSkip}>
            Skip
          </Button>
          <div className="flex gap-2">
            {!isFirst && (
              <Button variant="outline" size="sm" onClick={handlePrev}>
                Back
              </Button>
            )}
            <Button size="sm" onClick={handleNext}>
              {isLast ? "Finish" : "Next"}
            </Button>
          </div>
        </div>
      </div>

      {/* Nav highlight indicator for left-nav steps */}
      {step.position === "left-nav" && step.targetLabel && (
        <div className="absolute left-0 top-20 z-[200] h-10 w-16 rounded-r-md border-2 border-primary bg-primary/10" />
      )}
    </div>
  );
}
