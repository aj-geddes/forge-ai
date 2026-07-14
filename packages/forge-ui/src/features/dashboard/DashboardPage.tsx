import { Monitor } from "lucide-react";
import { AgentHero } from "./AgentHero";
import { TryItPrompts } from "./TryItPrompts";
import { ActivityFeed } from "./ActivityFeed";
import { MiniChat } from "./MiniChat";
import { StatusStrip } from "./StatusStrip";

/**
 * The Dashboard, rebuilt around the running agent as its thesis (not a
 * grid of numbers): the agent hero, try-it prompts, the live activity
 * telemetry feed (the signature element), an embedded quick chat, and a
 * quieter status strip demoted below the fold.
 */
export function DashboardPage() {
  return (
    <div className="space-y-10">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-bold tracking-tight">
          <Monitor className="h-6 w-6" />
          Dashboard
        </h1>
        <p className="text-sm text-muted-foreground">
          Your Forge agents, what each can do, and what they've been doing.
        </p>
      </div>

      <AgentHero />
      <TryItPrompts />
      <ActivityFeed />
      <MiniChat />
      <StatusStrip />
    </div>
  );
}
