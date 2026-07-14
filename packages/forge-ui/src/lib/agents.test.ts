import { describe, it, expect } from "vitest";
import {
  DEFAULT_AGENT_MODE,
  getAgentMode,
  isFullAccessAgent,
  resolveAgentRoster,
  resolveDefaultAgentName,
  resolveScopedToolInfos,
} from "./agents";
import type { AgentDef, ForgeConfig } from "@/types/config";
import type { ToolInfo } from "@/types/config";

function baseConfig(overrides: Partial<ForgeConfig> = {}): ForgeConfig {
  return {
    metadata: { name: "forge", version: "0.1.0" },
    llm: { default_model: "gpt-4o" },
    tools: {},
    ...overrides,
  };
}

describe("getAgentMode", () => {
  it("defaults to passive when mode is absent", () => {
    const agent: AgentDef = { name: "assistant" };
    expect(getAgentMode(agent)).toBe("passive");
    expect(getAgentMode(agent)).toBe(DEFAULT_AGENT_MODE);
  });

  it("returns the explicit mode when present", () => {
    const agent: AgentDef = { name: "assistant", mode: "active" };
    expect(getAgentMode(agent)).toBe("active");
  });
});

describe("isFullAccessAgent", () => {
  it("is true when tools is undefined", () => {
    expect(isFullAccessAgent({ tools: undefined })).toBe(true);
  });

  it("is true when tools is an empty array", () => {
    expect(isFullAccessAgent({ tools: [] })).toBe(true);
  });

  it("is false when tools has entries", () => {
    expect(isFullAccessAgent({ tools: ["get_weather"] })).toBe(false);
  });
});

describe("resolveDefaultAgentName", () => {
  it("returns config.agents.default when set", () => {
    const config = baseConfig({
      agents: { default: "researcher", agents: [{ name: "researcher" }, { name: "assistant" }] },
    });
    expect(resolveDefaultAgentName(config)).toBe("researcher");
  });

  it("falls back to the first defined agent's name when default is unset", () => {
    const config = baseConfig({
      agents: { agents: [{ name: "researcher" }, { name: "assistant" }] },
    });
    expect(resolveDefaultAgentName(config)).toBe("researcher");
  });

  it("returns undefined when there are no agents and no default", () => {
    const config = baseConfig();
    expect(resolveDefaultAgentName(config)).toBeUndefined();
  });
});

describe("resolveAgentRoster", () => {
  it("maps each configured agent, marking the default and defaulting mode to passive", () => {
    const config = baseConfig({
      agents: {
        default: "assistant",
        agents: [
          {
            name: "researcher",
            description: "Digs up facts.",
            model: "gpt-4o",
            tools: ["search_web"],
            mode: "active",
          },
          {
            name: "assistant",
            description: "General helper.",
            model: "gpt-4o-mini",
          },
        ],
      },
    });

    const roster = resolveAgentRoster(config);

    expect(roster).toHaveLength(2);
    expect(roster[0]).toMatchObject({
      name: "researcher",
      description: "Digs up facts.",
      model: "gpt-4o",
      tools: ["search_web"],
      mode: "active",
      isDefault: false,
    });
    expect(roster[1]).toMatchObject({
      name: "assistant",
      description: "General helper.",
      model: "gpt-4o-mini",
      mode: "passive",
      isDefault: true,
    });
  });

  it("falls back to the instance's own model when an agent doesn't specify one", () => {
    const config = baseConfig({
      llm: { default_model: "instance-default" },
      agents: { default: "assistant", agents: [{ name: "assistant", description: "Helper." }] },
    });

    const roster = resolveAgentRoster(config);
    expect(roster[0]!.model).toBe("instance-default");
  });

  it("synthesizes a single fallback agent from instance metadata when agents[] is empty", () => {
    const config = baseConfig({
      metadata: { name: "forge", version: "0.1.0", description: "A helpful forge instance." },
      agents: { default: "assistant", agents: [] },
    });

    const roster = resolveAgentRoster(config);

    expect(roster).toHaveLength(1);
    expect(roster[0]).toMatchObject({
      name: "assistant",
      description: "A helpful forge instance.",
      isDefault: true,
      mode: "passive",
    });
    expect(roster[0]!.tools).toBeUndefined();
  });

  it("synthesizes a fallback agent from metadata.name when there is no agents config at all", () => {
    const config = baseConfig();
    const roster = resolveAgentRoster(config);
    expect(roster).toHaveLength(1);
    expect(roster[0]!.name).toBe("forge");
  });
});

describe("resolveScopedToolInfos", () => {
  const allTools: ToolInfo[] = [
    { name: "get_weather", description: "Gets current weather for a city" },
    { name: "get_crypto_price", description: "Gets the current price of a cryptocurrency" },
  ];

  it("resolves each tool name to its full ToolInfo entry, preserving order", () => {
    const resolved = resolveScopedToolInfos(["get_crypto_price", "get_weather"], allTools);
    expect(resolved).toEqual([
      { name: "get_crypto_price", description: "Gets the current price of a cryptocurrency" },
      { name: "get_weather", description: "Gets current weather for a city" },
    ]);
  });

  it("falls back to a placeholder entry for a name not present in the full tools list", () => {
    const resolved = resolveScopedToolInfos(["unknown_tool"], allTools);
    expect(resolved).toEqual([{ name: "unknown_tool", description: "" }]);
  });

  it("returns an empty array for undefined tool names", () => {
    expect(resolveScopedToolInfos(undefined, allTools)).toEqual([]);
  });
});
