import { describe, it, expect } from "vitest";
import { configToForm, formToConfig } from "./ConfigVisualEditor";
import type { ForgeConfig } from "@/types/config";

function baseConfig(): ForgeConfig {
  return {
    metadata: {
      name: "my-forge-agent",
      version: "1.0.0",
      description: "A test agent",
      environment: "staging",
    },
    llm: {
      default_model: "gpt-4o",
      temperature: 0.5,
      max_tokens: 2048,
      litellm: { mode: "sidecar", endpoint: "http://litellm:4000" },
    },
    tools: { openapi_sources: [], manual_tools: [], workflows: [] },
    security: {
      agentweave: { enabled: true, trust_domain: "forge.local" },
      rate_limit_rpm: 120,
      allowed_origins: ["https://app.example.com"],
    },
    agents: { default: "assistant", agents: [], peers: [{ name: "p1", endpoint: "https://p1", trust_level: "high" }] },
  };
}

describe("ConfigVisualEditor field mapping (contract with ForgeConfig backend schema)", () => {
  it("reads metadata.environment directly -- the backend has no metadata.labels", () => {
    const form = configToForm(baseConfig());
    expect(form.metadata.environment).toBe("staging");
  });

  it("round-trips security fields to the backend's flat shape (rate_limit_rpm, allowed_origins, agentweave.trust_policy)", () => {
    const config = baseConfig();
    const form = configToForm(config);
    form.security.trust_policy = "strict";
    form.security.rate_limit_rpm = 30;
    form.security.cors_origins = "https://a.example.com, https://b.example.com";

    const updated = formToConfig(form, config);

    // trust_policy lives under agentweave in the backend model, not at the
    // security top level.
    expect(updated.security?.agentweave?.trust_policy).toBe("strict");
    expect(updated.security).not.toHaveProperty("trust_policy");
    // rate_limit_rpm is a flat int, not nested under rate_limit.requests_per_minute.
    expect(updated.security?.rate_limit_rpm).toBe(30);
    expect(updated.security).not.toHaveProperty("rate_limit");
    // allowed_origins, not cors_origins.
    expect(updated.security?.allowed_origins).toEqual([
      "https://a.example.com",
      "https://b.example.com",
    ]);
    expect(updated.security).not.toHaveProperty("cors_origins");
  });

  it("round-trips agents.default as a plain persona name string, not a default_agent object", () => {
    const config = baseConfig();
    const form = configToForm(config);
    form.agents.default_agent_name = "researcher";

    const updated = formToConfig(form, config);

    expect(updated.agents?.default).toBe("researcher");
    expect(updated.agents).not.toHaveProperty("default_agent");
  });

  it("does not invent llm.litellm.config_path or .port -- the backend LiteLLMConfig has no such fields", () => {
    const config = baseConfig();
    const form = configToForm(config);

    expect(form.llm.litellm).not.toHaveProperty("config_path");
    expect(form.llm.litellm).not.toHaveProperty("port");

    const updated = formToConfig(form, config);
    expect(updated.llm.litellm).not.toHaveProperty("config_path");
    expect(updated.llm.litellm).not.toHaveProperty("port");
  });
});
