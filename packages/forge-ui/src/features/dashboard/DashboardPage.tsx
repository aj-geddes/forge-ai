import { Gauge, MessageCircle } from "lucide-react";
import { ApprovalsHero } from "./ApprovalsHero";
import { ActivityFeed } from "./ActivityFeed";
import { AgentStrip } from "./AgentStrip";
import { TryItPrompts } from "./TryItPrompts";
import { MiniChat } from "./MiniChat";
import { StatusStrip } from "./StatusStrip";

/**
 * The Dashboard, rebuilt as an operator console centered on the
 * human-in-the-loop approval queue -- "what needs me, and what's
 * happening" -- rather than a static roster. Priority order:
 *   1. ApprovalsHero -- pending approvals, the visual focus when any exist.
 *   2. ActivityFeed -- the live telemetry log of recent tool calls.
 *   3. AgentStrip (+ TryItPrompts) -- the roster, demoted to reference.
 *   4. StatusStrip -- health/tools/sessions/peers, quietest of all.
 *   5. MiniChat -- secondary and collapsible, not the page's thesis anymore.
 */
export function DashboardPage() {
  return (
    <div className="space-y-10">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-bold tracking-tight">
          <Gauge className="h-6 w-6" />
          Dashboard
        </h1>
        <p className="text-sm text-muted-foreground">
          What needs your approval, what your agents have been doing, and how the instance is running.
        </p>
      </div>

      <ApprovalsHero />
      <ActivityFeed />

      <section className="space-y-4">
        <AgentStrip />
        <TryItPrompts />
      </section>

      <StatusStrip />

      <details className="group rounded-lg border bg-card/50 open:border-transparent open:bg-transparent">
        <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 font-display text-xs font-bold uppercase tracking-[0.2em] text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 group-open:px-0 group-open:py-0">
          <MessageCircle className="h-3.5 w-3.5" aria-hidden="true" />
          Quick chat
        </summary>
        <div className="p-3 pt-1 group-open:p-0 group-open:pt-2">
          <MiniChat />
        </div>
      </details>
    </div>
  );
}
