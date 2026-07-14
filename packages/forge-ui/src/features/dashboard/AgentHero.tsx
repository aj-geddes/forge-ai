import { Sparkles, Cpu } from "lucide-react";
import { useConfig, useTools } from "@/api/hooks";
import { Badge } from "@/components/ui/badge";
import { ToolChips } from "@/components/agent/ToolChips";
import {
  isFullAccessAgent,
  resolveAgentRoster,
  resolveScopedToolInfos,
  type RosterAgent,
} from "@/lib/agents";
import type { ForgeConfig, ToolInfo } from "@/types/config";
import { Eyebrow, SkeletonLine } from "./shared";

function AgentHeroSkeleton() {
  return (
    <section className="space-y-4">
      <SkeletonLine className="h-3 w-16" />
      <div className="rounded-xl border bg-card p-6">
        <SkeletonLine className="h-8 w-48" />
        <SkeletonLine className="mt-3 h-4 w-full max-w-xl" />
        <SkeletonLine className="mt-2 h-4 w-2/3 max-w-md" />
      </div>
    </section>
  );
}

function StatusPulseDot() {
  return (
    <span className="relative mt-2 flex h-3 w-3 shrink-0" aria-hidden="true">
      <span className="absolute inline-flex h-full w-full animate-molten-pulse rounded-full bg-primary" />
      <span className="relative inline-flex h-3 w-3 rounded-full bg-primary" />
    </span>
  );
}

/** In-theme mode badge -- amber for "active" (primary), neutral outline for
 * the safer "passive" default. Never blue/purple, per the Forge palette. */
function ModeBadge({ mode }: { mode: RosterAgent["mode"] }) {
  return (
    <Badge
      variant={mode === "active" ? "default" : "outline"}
      className="font-mono text-[10px] uppercase"
    >
      {mode}
    </Badge>
  );
}

function CapabilitiesSection({
  agent,
  allTools,
  toolsLoading,
}: {
  agent: RosterAgent;
  allTools: ToolInfo[] | undefined;
  toolsLoading: boolean;
}) {
  const fullAccess = isFullAccessAgent(agent);

  if (fullAccess) {
    if (toolsLoading) {
      return (
        <div className="flex flex-wrap gap-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <SkeletonLine key={i} className="h-6 w-28 rounded-full" />
          ))}
        </div>
      );
    }
    if (!allTools || allTools.length === 0) {
      return (
        <p className="text-xs text-muted-foreground">
          No tools configured yet -- this agent can still chat, just without external capabilities.
        </p>
      );
    }
    return (
      <div className="space-y-2">
        <span className="inline-flex items-center rounded-full border border-dashed px-3 py-1 text-xs text-muted-foreground">
          All tools (full access)
        </span>
        <ToolChips tools={allTools} />
      </div>
    );
  }

  const scoped = resolveScopedToolInfos(agent.tools, allTools);
  return <ToolChips tools={scoped} />;
}

function AgentCard({
  agent,
  allTools,
  toolsLoading,
}: {
  agent: RosterAgent;
  allTools: ToolInfo[] | undefined;
  toolsLoading: boolean;
}) {
  return (
    <article className="rounded-xl border bg-card p-6">
      <div className="flex items-start gap-4">
        {agent.isDefault ? <StatusPulseDot /> : null}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="font-display text-2xl font-extrabold tracking-tight sm:text-3xl">
              {agent.name}
            </h3>
            {agent.isDefault && <Badge>Default</Badge>}
            <ModeBadge mode={agent.mode} />
          </div>
          <p className="mt-2 max-w-2xl text-sm text-muted-foreground">{agent.description}</p>
          <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <Cpu className="h-3.5 w-3.5" />
            <span className="font-mono">{agent.model}</span>
          </div>
        </div>
      </div>

      <div className="mt-6 border-t pt-4">
        <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Capabilities
        </p>
        <CapabilitiesSection agent={agent} allTools={allTools} toolsLoading={toolsLoading} />
      </div>
    </article>
  );
}

/**
 * The Dashboard's thesis: the full agent roster, presented as cast
 * nameplates -- each agent's name, model, description, least-privilege
 * tool scope (governance visible), default marker, and passive/active mode
 * badge. Degenerates gracefully to a single synthesized card when the
 * instance has no explicit `agents.agents` configured.
 */
export function AgentHero() {
  const { data: config, isLoading: configLoading, isError: configError } = useConfig();
  const { data: tools, isLoading: toolsLoading } = useTools();

  if (configLoading) {
    return <AgentHeroSkeleton />;
  }

  if (configError || !config) {
    return (
      <section className="space-y-4">
        <Eyebrow icon={Sparkles}>Agents</Eyebrow>
        <div className="rounded-xl border bg-card p-6 text-sm text-destructive">
          Unable to load the agent's configuration.
        </div>
      </section>
    );
  }

  const roster = resolveAgentRoster(config as ForgeConfig);

  return (
    <section className="space-y-4">
      <Eyebrow icon={Sparkles}>Agents</Eyebrow>
      <div className="grid gap-4 sm:grid-cols-2">
        {roster.map((agent) => (
          <AgentCard key={agent.name} agent={agent} allTools={tools} toolsLoading={toolsLoading} />
        ))}
      </div>
    </section>
  );
}
