// Pure helpers for resolving the multi-agent roster and each agent's
// least-privilege tool scope from `ForgeConfig.agents` (see
// src/types/config.ts -- AgentDef/AgentsConfig mirror
// forge_config.schema.AgentsConfig). Kept side-effect free and framework-free
// so both the Dashboard roster and the Chat/MiniChat agent selectors can
// share one source of truth, independently unit-testable.

import type { AgentDef, ForgeConfig, ToolInfo } from "@/types/config";

export type AgentMode = "passive" | "active";

/** The backend field may not exist yet on every deployment; absence means
 * the agent runs in the (safer, non-autonomous) passive mode. */
export const DEFAULT_AGENT_MODE: AgentMode = "passive";

export interface RosterAgent {
  name: string;
  description: string;
  model: string;
  /** Undefined or empty means full (unscoped) tool access. */
  tools?: string[];
  mode: AgentMode;
  isDefault: boolean;
}

function fallbackDescription(name: string): string {
  return `${name} is ready to help, using its configured tools to look things up and take action for you.`;
}

export function getAgentMode(agent: Pick<AgentDef, "mode">): AgentMode {
  return agent.mode ?? DEFAULT_AGENT_MODE;
}

/** An agent scopes down to `tools`; an undefined or empty list means it was
 * never scoped at all, i.e. it has full (unscoped) access to every tool. */
export function isFullAccessAgent(agent: Pick<AgentDef, "tools">): boolean {
  return !agent.tools || agent.tools.length === 0;
}

/** The configured default agent's name, falling back to the first defined
 * agent when `agents.default` is unset (a valid config, per the schema). */
export function resolveDefaultAgentName(config: ForgeConfig): string | undefined {
  return config.agents?.default ?? config.agents?.agents?.[0]?.name;
}

function toRosterAgent(agent: AgentDef, config: ForgeConfig, defaultName: string | undefined): RosterAgent {
  return {
    name: agent.name,
    description: agent.description || agent.system_prompt || fallbackDescription(agent.name),
    model: agent.model ?? config.llm.default_model,
    tools: agent.tools,
    mode: getAgentMode(agent),
    isDefault: agent.name === defaultName,
  };
}

/**
 * The full agent roster for the Dashboard/selectors. When `agents.agents`
 * is empty or missing (a minimal, single-persona Forge deployment), this
 * synthesizes one fallback "agent" from the instance's own metadata/model
 * so the UI still has exactly one persona to present -- the pre-multi-agent
 * behavior, preserved as a degenerate case of the same roster shape.
 */
export function resolveAgentRoster(config: ForgeConfig): RosterAgent[] {
  const agents = config.agents?.agents ?? [];
  const defaultName = resolveDefaultAgentName(config);

  if (agents.length > 0) {
    return agents.map((agent) => toRosterAgent(agent, config, defaultName));
  }

  const fallbackName = defaultName ?? config.metadata.name;
  return [
    {
      name: fallbackName,
      description: config.metadata.description || fallbackDescription(fallbackName),
      model: config.llm.default_model,
      tools: undefined,
      mode: DEFAULT_AGENT_MODE,
      isDefault: true,
    },
  ];
}

/**
 * Resolves an agent's scoped tool names to their full `ToolInfo` entries
 * (for descriptions/labels), preserving the given order. A name not found
 * in the full tools list (e.g. stale config) still renders as a placeholder
 * rather than silently disappearing.
 */
export function resolveScopedToolInfos(
  toolNames: string[] | undefined,
  allTools: ToolInfo[] | undefined,
): ToolInfo[] {
  if (!toolNames || toolNames.length === 0) return [];
  return toolNames.map(
    (name) => allTools?.find((tool) => tool.name === name) ?? { name, description: "" },
  );
}
