import { describe, it, expect, afterEach, beforeEach, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { configToForm, formToConfig, ConfigVisualEditor } from "./ConfigVisualEditor";
import { useConfigStore } from "@/stores/configStore";
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

  it("does not add a deprecated security.api_keys control -- the field is unused by the form", () => {
    const config = baseConfig();
    const form = configToForm(config);

    expect(form.security).not.toHaveProperty("api_keys");

    const updated = formToConfig(form, config);
    expect(updated.security?.api_keys).toBeUndefined();
  });

  describe("LLM Model List / mode / endpoint are BASE-ONLY (Phase-1 field split)", () => {
    function configWithModelList(): ForgeConfig {
      const config = baseConfig();
      config.llm.litellm = {
        mode: "sidecar",
        endpoint: "http://litellm:4000",
        model_list: [
          {
            model_name: "primary",
            litellm_params: {
              model: "openai/gpt-4o",
              api_key: { source: "env", name: "OPENAI_KEY" },
              api_base: "https://api.openai.com/v1",
            },
          },
        ],
      };
      return config;
    }

    it("does not expose model_list, mode, or endpoint as form-writable fields -- they are read-only (ReadOnlyLiteLLMPanel reads them straight off the draft, not through the form)", () => {
      const form = configToForm(configWithModelList());

      expect(form.llm.litellm).not.toHaveProperty("model_list");
      expect(form.llm.litellm).not.toHaveProperty("mode");
      expect(form.llm.litellm).not.toHaveProperty("endpoint");
    });

    it("formToConfig always echoes mode/endpoint/model_list back byte-for-byte, regardless of what the form contains -- a save can never repoint or introduce a destination/secret here", () => {
      const config = configWithModelList();
      const form = configToForm(config);
      // Even a malicious/buggy caller stuffing extra keys onto the parsed
      // form object cannot move the needle: formToConfig never reads them.
      (form.llm.litellm as Record<string, unknown>).model_list = [
        { model_name: "evil", litellm_params: { api_base: "https://attacker.example.com" } },
      ];
      (form.llm.litellm as Record<string, unknown>).mode = "external";
      (form.llm.litellm as Record<string, unknown>).endpoint = "https://attacker.example.com";

      const updated = formToConfig(form, config);

      expect(updated.llm.litellm?.mode).toBe("sidecar");
      expect(updated.llm.litellm?.endpoint).toBe("http://litellm:4000");
      expect(updated.llm.litellm?.model_list).toEqual(config.llm.litellm!.model_list);
    });
  });

  describe("LLM Fallback Models, Timeout, Max Retries (LiteLLMConfig)", () => {
    it("round-trips fallback_models as a comma-separated string mapped to a string[]", () => {
      const config = baseConfig();
      config.llm.litellm = {
        mode: "embedded",
        fallback_models: ["gpt-4o-mini", "claude-haiku-4-5-20251001"],
      };
      const form = configToForm(config);

      expect(form.llm.litellm?.fallback_models).toBe("gpt-4o-mini, claude-haiku-4-5-20251001");

      form.llm.litellm!.fallback_models = "gpt-4o-mini, gemini/gemini-2.0-flash";
      const updated = formToConfig(form, config);

      expect(updated.llm.litellm?.fallback_models).toEqual([
        "gpt-4o-mini",
        "gemini/gemini-2.0-flash",
      ]);
    });

    it("round-trips timeout and max_retries as flat numeric fields, not a nested retries object", () => {
      const config = baseConfig();
      config.llm.litellm = { mode: "embedded", timeout: 30, max_retries: 3 };
      const form = configToForm(config);

      expect(form.llm.litellm?.timeout).toBe(30);
      expect(form.llm.litellm?.max_retries).toBe(3);

      form.llm.litellm!.timeout = 45;
      form.llm.litellm!.max_retries = 5;
      const updated = formToConfig(form, config);

      expect(updated.llm.litellm?.timeout).toBe(45);
      expect(updated.llm.litellm?.max_retries).toBe(5);
      expect(updated.llm.litellm).not.toHaveProperty("retries");
    });
  });

  describe("Agent Definitions (AgentsConfig.agents) -- moved off this wholesale editor onto the dedicated /agents page", () => {
    function configWithAgentDefs(): ForgeConfig {
      const config = baseConfig();
      config.agents = {
        default: "assistant",
        peers: [],
        agents: [
          {
            name: "researcher",
            description: "Researches topics",
            system_prompt: "You are a researcher.",
            model: "gpt-4o",
            tools: ["web_search", "summarize"],
            max_turns: 15,
          },
        ],
      };
      return config;
    }

    it("does not expose the agents array as a form-writable field -- full CRUD lives on /agents (overlay-backed), not this wholesale PUT", () => {
      const form = configToForm(configWithAgentDefs());

      expect(form.agents).not.toHaveProperty("agents");
    });

    it("formToConfig passes the existing agents array through unchanged, no matter what the form contains", () => {
      const config = configWithAgentDefs();
      const form = configToForm(config);
      // Even if something stuffed an edit onto the parsed form object, this
      // wholesale save must never let it reach the config.
      (form.agents as Record<string, unknown>).agents = [{ name: "evil" }];

      const updated = formToConfig(form, config);

      expect(updated.agents?.agents).toEqual(config.agents!.agents);
    });
  });
});

describe("ConfigVisualEditor mount dirty-state (regression: false 'Unsaved changes' on load)", () => {
  // The Visual editor now renders ConfirmDialog instances (for the model-
  // list/agent-definition remove confirmations) even while closed -- their
  // underlying native <dialog> element's mount effect calls
  // showModal()/close(), which jsdom does not implement.
  beforeEach(() => {
    window.HTMLDialogElement.prototype.showModal = vi.fn(function (
      this: HTMLDialogElement,
    ) {
      this.open = true;
    });
    window.HTMLDialogElement.prototype.close = vi.fn(function (
      this: HTMLDialogElement,
    ) {
      this.open = false;
    });
  });

  afterEach(() => {
    cleanup();
    useConfigStore.setState({ original: null, draft: null, isDirty: false });
  });

  function loadedConfig(): ForgeConfig {
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
        // Mirrors the live-deployed shape: the backend serializes an unset
        // optional endpoint as an explicit `null` (mode "embedded" doesn't
        // use one), and model_list[].litellm_params carries several keys
        // whose insertion order the form's round-trip does not preserve.
        // Regression coverage for the false "Unsaved changes" bug where a
        // JSON.stringify-based dirty check tripped on `null` vs. absent keys
        // and on object key reordering, not just literal edits.
        litellm: {
          mode: "embedded",
          endpoint: null,
          model_list: [
            {
              model_name: "nemotron",
              litellm_params: {
                model: "openai/nemotron-puzzle",
                api_base: "http://x",
                api_key: "***REDACTED***",
              },
            },
          ],
        },
      },
      tools: { openapi_sources: [], manual_tools: [], workflows: [] },
      security: {
        agentweave: { enabled: true, trust_domain: "forge.local" },
        rate_limit_rpm: 120,
        allowed_origins: ["https://app.example.com"],
      },
      agents: { default: "assistant", agents: [], peers: [] },
    };
  }

  function renderEditor() {
    useConfigStore.getState().setOriginal(loadedConfig());
    return render(
      <MemoryRouter>
        <ConfigVisualEditor />
      </MemoryRouter>,
    );
  }

  it("does not mark the draft dirty just from mounting/resetting the form with an unedited config", async () => {
    renderEditor();

    // Let the reset()/watch() effects settle.
    await waitFor(() => {
      expect(screen.getByDisplayValue("my-forge-agent")).toBeInTheDocument();
    });

    expect(useConfigStore.getState().isDirty).toBe(false);
  });

  it("marks the draft dirty on a real field edit, and clears it again when the edit is reverted", async () => {
    renderEditor();
    const user = userEvent.setup();

    const nameInput = await screen.findByDisplayValue("my-forge-agent");
    expect(useConfigStore.getState().isDirty).toBe(false);

    await user.clear(nameInput);
    await user.type(nameInput, "renamed-agent");

    await waitFor(() => {
      expect(useConfigStore.getState().isDirty).toBe(true);
    });

    await user.clear(nameInput);
    await user.type(nameInput, "my-forge-agent");

    await waitFor(() => {
      expect(useConfigStore.getState().isDirty).toBe(false);
    });
  });
});
