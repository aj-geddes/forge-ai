import { useNavigate } from "react-router-dom";
import { Bot, ChevronRight, Sparkles } from "lucide-react";
import { useConfig, useTools } from "@/api/hooks";
import { useChatStore } from "@/stores/chatStore";
import { Badge } from "@/components/ui/badge";
import { isFullAccessAgent, resolveAgentRoster, type RosterAgent } from "@/lib/agents";
import type { ForgeConfig } from "@/types/config";
import { Eyebrow, SkeletonLine } from "./shared";

function scopedToolCount(agent: RosterAgent, totalToolCount: number): number {
  return isFullAccessAgent(agent) ? totalToolCount : (agent.tools?.length ?? 0);
}

function AgentStripSkeleton() {
  return (
    <div className="space-y-2">
      {Array.from({ length: 2 }).map((_, i) => (
        <SkeletonLine key={i} className="h-11 w-full rounded-lg" />
      ))}
    </div>
  );
}

function AgentRow({ agent, toolCount }: { agent: RosterAgent; toolCount: number }) {
  const navigate = useNavigate();
  const setPendingAgent = useChatStore((s) => s.setPendingAgent);

  const handleClick = () => {
    setPendingAgent(agent.name);
    navigate("/chat");
  };

  return (
    <button
      type="button"
      onClick={handleClick}
      className="flex w-full items-center gap-3 rounded-lg border bg-card px-3 py-2 text-left text-sm transition-colors hover:border-primary/40 hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
    >
      <Bot className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
      <span className="min-w-0 flex-1 truncate font-medium">{agent.name}</span>
      <Badge
        variant={agent.mode === "active" ? "default" : "outline"}
        className="shrink-0 font-mono text-[10px] uppercase"
      >
        {agent.mode}
      </Badge>
      <span className="shrink-0 text-xs text-muted-foreground">
        {toolCount} tool{toolCount !== 1 ? "s" : ""}
      </span>
      <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
    </button>
  );
}

/**
 * The Dashboard's demoted agent roster: a compact reference strip (name,
 * mode badge, scoped-tool count) rather than the full nameplate cards
 * (see AgentHero, still used standalone). Clicking a row queues that agent
 * via chatStore.pendingAgent and opens Chat pre-selected to it.
 */
export function AgentStrip() {
  const { data: config, isLoading: configLoading, isError: configError } = useConfig();
  const { data: tools, isLoading: toolsLoading } = useTools();

  return (
    <section className="space-y-2">
      <Eyebrow icon={Sparkles}>Agents</Eyebrow>
      {configLoading || toolsLoading ? (
        <AgentStripSkeleton />
      ) : configError || !config ? (
        <p className="text-sm text-destructive">Unable to load the agent roster.</p>
      ) : (
        <div className="space-y-2">
          {resolveAgentRoster(config as ForgeConfig).map((agent) => (
            <AgentRow
              key={agent.name}
              agent={agent}
              toolCount={scopedToolCount(agent, tools?.length ?? 0)}
            />
          ))}
        </div>
      )}
    </section>
  );
}
