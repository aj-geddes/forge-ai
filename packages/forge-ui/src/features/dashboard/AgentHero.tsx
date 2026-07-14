import { Sparkles, Cpu } from "lucide-react";
import { useConfig, useTools } from "@/api/hooks";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { AgentDef, ForgeConfig } from "@/types/config";
import type { ToolInfo } from "@/types/config";
import { Eyebrow, SkeletonLine } from "./shared";

const MAX_VISIBLE_CAPABILITIES = 8;
const CAPABILITY_DESCRIPTION_MAX_CHARS = 48;

interface AgentPersona {
  name: string;
  oneLiner: string;
  model: string;
  litellmMode: string | null;
}

function truncate(text: string, maxLength: number): string {
  if (text.length <= maxLength) return text;
  return `${text.slice(0, maxLength).trimEnd()}…`;
}

/**
 * Resolves the "running agent" persona to headline on the Dashboard: the
 * `AgentDef` named by `agents.default`, falling back to the first defined
 * agent, and finally to the instance's own metadata when no agents are
 * configured at all (a valid, if minimal, Forge deployment).
 */
export function resolveAgentPersona(config: ForgeConfig): AgentPersona {
  const agents = config.agents?.agents ?? [];
  const defaultName = config.agents?.default;
  const persona: AgentDef | undefined =
    agents.find((a) => a.name === defaultName) ?? agents[0];

  const name = persona?.name ?? defaultName ?? config.metadata.name;
  const model = persona?.model ?? config.llm.default_model;
  const litellmMode = config.llm.litellm?.mode ?? null;

  const oneLiner =
    persona?.description ||
    persona?.system_prompt ||
    config.metadata.description ||
    `${name} is ready to help, using its configured tools to look things up and take action for you.`;

  return { name, oneLiner, model, litellmMode };
}

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

function CapabilitiesRow({ tools, loading }: { tools: ToolInfo[] | undefined; loading: boolean }) {
  if (loading) {
    return (
      <div className="flex flex-wrap gap-2">
        {Array.from({ length: 4 }).map((_, i) => (
          <SkeletonLine key={i} className="h-6 w-28 rounded-full" />
        ))}
      </div>
    );
  }

  if (!tools || tools.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        No tools configured yet -- this agent can still chat, just without external capabilities.
      </p>
    );
  }

  const visible = tools.slice(0, MAX_VISIBLE_CAPABILITIES);
  const overflow = tools.length - visible.length;

  return (
    <div className="flex flex-wrap gap-2">
      {visible.map((tool) => (
        <span
          key={tool.name}
          title={tool.description}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-full border bg-muted/50 px-3 py-1 text-xs",
          )}
        >
          <span className="font-mono font-medium">{tool.name}</span>
          {tool.description && (
            <span className="hidden text-muted-foreground sm:inline">
              &mdash; {truncate(tool.description, CAPABILITY_DESCRIPTION_MAX_CHARS)}
            </span>
          )}
        </span>
      ))}
      {overflow > 0 && (
        <span className="inline-flex items-center rounded-full border border-dashed px-3 py-1 text-xs text-muted-foreground">
          +{overflow} more
        </span>
      )}
    </div>
  );
}

/**
 * The Dashboard's thesis: the running agent, presented as a cast nameplate
 * with a live status pulse, a plain-language one-liner, and a capabilities
 * row built from the real tools list. This is what makes the agent visible.
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
        <Eyebrow icon={Sparkles}>Agent</Eyebrow>
        <div className="rounded-xl border bg-card p-6 text-sm text-destructive">
          Unable to load the agent's configuration.
        </div>
      </section>
    );
  }

  const persona = resolveAgentPersona(config);

  return (
    <section className="space-y-4">
      <Eyebrow icon={Sparkles}>Agent</Eyebrow>
      <div className="rounded-xl border bg-card p-6">
        <div className="flex items-start gap-4">
          <StatusPulseDot />
          <div className="min-w-0 flex-1">
            <h1 className="font-display text-3xl font-extrabold tracking-tight sm:text-4xl">
              {persona.name}
            </h1>
            <p className="mt-2 max-w-2xl text-sm text-muted-foreground sm:text-base">
              {persona.oneLiner}
            </p>
            <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <Cpu className="h-3.5 w-3.5" />
              <span className="font-mono">{persona.model}</span>
              {persona.litellmMode && (
                <Badge variant="secondary" className="font-mono text-[10px] uppercase">
                  {persona.litellmMode}
                </Badge>
              )}
            </div>
          </div>
        </div>

        <div className="mt-6 border-t pt-4">
          <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            Capabilities
          </p>
          <CapabilitiesRow tools={tools} loading={toolsLoading} />
        </div>
      </div>
    </section>
  );
}
