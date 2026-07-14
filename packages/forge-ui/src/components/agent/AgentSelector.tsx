import { useId } from "react";
import { isFullAccessAgent, resolveScopedToolInfos } from "@/lib/agents";
import type { AgentDef, ToolInfo } from "@/types/config";
import { Select } from "@/components/ui/select";
import { ToolChips } from "./ToolChips";

interface AgentSelectorProps {
  /** The full agent roster to choose from. Renders nothing when empty --
   * a single-persona deployment keeps today's no-selector behavior. */
  agents: AgentDef[];
  value: string;
  onChange: (name: string) => void;
  /** The instance's full tools list, used to resolve a scoped agent's tool
   * names to human labels and to render "all tools" for a full-access one. */
  tools?: ToolInfo[];
  id?: string;
  label?: string;
}

/**
 * Lets the user pick which agent persona a chat session talks to, and
 * previews that agent's least-privilege tool scope so the choice is an
 * informed one (the governance story made visible in Chat, not just the
 * Dashboard roster).
 */
export function AgentSelector({
  agents,
  value,
  onChange,
  tools,
  id,
  label = "Agent",
}: AgentSelectorProps) {
  const generatedId = useId();
  const selectId = id ?? generatedId;

  if (agents.length === 0) {
    return null;
  }

  const selectedAgent = agents.find((agent) => agent.name === value);
  const fullAccess = selectedAgent ? isFullAccessAgent(selectedAgent) : false;
  const scopedTools = selectedAgent
    ? resolveScopedToolInfos(selectedAgent.tools, tools)
    : [];

  return (
    <div className="space-y-1.5">
      <label htmlFor={selectId} className="text-xs font-medium text-muted-foreground">
        {label}
      </label>
      <Select
        id={selectId}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="h-9"
      >
        {agents.map((agent) => (
          <option key={agent.name} value={agent.name}>
            {agent.description ? `${agent.name} — ${agent.description}` : agent.name}
          </option>
        ))}
      </Select>
      {selectedAgent && (
        <div className="flex flex-wrap items-center gap-2">
          {fullAccess ? (
            <span className="text-xs text-muted-foreground">All tools (full access)</span>
          ) : (
            <ToolChips tools={scopedTools} max={6} />
          )}
        </div>
      )}
    </div>
  );
}
