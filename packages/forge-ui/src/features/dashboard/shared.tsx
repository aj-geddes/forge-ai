import type { ComponentType, ReactNode } from "react";
import { Info } from "lucide-react";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Shared building blocks used across the Dashboard's agent-centric sections.
// ---------------------------------------------------------------------------

export function SkeletonLine({ className }: { className?: string }) {
  return <div className={cn("animate-pulse rounded bg-muted", className)} />;
}

export function HelpText({ children }: { children: ReactNode }) {
  return (
    <p className="mt-1 flex items-start gap-1.5 text-xs leading-relaxed text-muted-foreground">
      <Info className="mt-0.5 h-3 w-3 shrink-0 opacity-60" />
      <span>{children}</span>
    </p>
  );
}

/**
 * A functional (not decorative) section label -- e.g. "AGENT", "TRY IT",
 * "LIVE ACTIVITY", "STATUS". Uses the bundled display face, uppercase, with
 * tight tracking for an industrial feel, per the Forge design spec.
 */
export function Eyebrow({
  icon: Icon,
  children,
  className,
}: {
  icon: ComponentType<{ className?: string }>;
  children: ReactNode;
  className?: string;
}) {
  return (
    <h2
      className={cn(
        "flex items-center gap-2 font-display text-xs font-bold uppercase tracking-[0.2em] text-muted-foreground",
        className,
      )}
    >
      <Icon className="h-3.5 w-3.5" />
      {children}
    </h2>
  );
}
