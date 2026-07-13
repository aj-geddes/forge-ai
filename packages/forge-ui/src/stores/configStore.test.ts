import { describe, it, expect, afterEach } from "vitest";
import { useConfigStore } from "./configStore";
import type { ForgeConfig } from "@/types/config";

// Reproduces the live-deployed false "Unsaved changes" bug: GET /v1/admin/config
// serializes unset optionals as explicit `null` (e.g. llm.litellm.endpoint), and
// the visual editor's form round-trip (formToConfig) rebuilds nested objects
// (e.g. model_list[].litellm_params) via object spreads that do not preserve
// original key insertion order. A JSON.stringify-based deepEqual is sensitive
// to both of these -- key order and null-vs-absent -- so a semantically
// unedited config still reads as dirty.

function liveConfig(): ForgeConfig {
  return {
    metadata: {
      name: "my-forge-agent",
      version: "1.0.0",
      description: "A test agent",
      environment: "staging",
    },
    llm: {
      default_model: "gpt-4o",
      temperature: 0.7,
      max_tokens: 4096,
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

describe("configStore dirty-check semantics", () => {
  afterEach(() => {
    useConfigStore.setState({ original: null, draft: null, isDirty: false });
  });

  it("is not dirty when the draft omits a null-valued key that was present on the original", () => {
    useConfigStore.getState().setOriginal(liveConfig());

    const original = useConfigStore.getState().original!;
    const draft: ForgeConfig = structuredClone(original);
    // formToConfig behavior: an unset optional endpoint is dropped rather than
    // written back as an explicit null.
    delete (draft.llm.litellm as unknown as Record<string, unknown>).endpoint;

    useConfigStore.getState().updateDraft(draft);

    expect(useConfigStore.getState().isDirty).toBe(false);
  });

  it("is not dirty when nested object keys are reordered but values are unchanged", () => {
    useConfigStore.getState().setOriginal(liveConfig());

    const original = useConfigStore.getState().original!;
    const draft: ForgeConfig = structuredClone(original);
    const entry = draft.llm.litellm!.model_list![0] as Record<string, unknown>;
    const params = entry.litellm_params as Record<string, unknown>;
    // Same key/value pairs, deliberately different insertion order -- mirrors
    // modelListEntryFromForm's `{...existingParams, model, api_key}` spread.
    entry.litellm_params = {
      api_key: params.api_key,
      model: params.model,
      api_base: params.api_base,
    };

    useConfigStore.getState().updateDraft(draft);

    expect(useConfigStore.getState().isDirty).toBe(false);
  });

  it("is not dirty for the combined live shape: endpoint omitted AND litellm_params reordered", () => {
    useConfigStore.getState().setOriginal(liveConfig());

    const original = useConfigStore.getState().original!;
    const draft: ForgeConfig = structuredClone(original);
    delete (draft.llm.litellm as unknown as Record<string, unknown>).endpoint;
    const entry = draft.llm.litellm!.model_list![0] as Record<string, unknown>;
    const params = entry.litellm_params as Record<string, unknown>;
    entry.litellm_params = {
      api_key: params.api_key,
      model: params.model,
      api_base: params.api_base,
    };

    useConfigStore.getState().updateDraft(draft);

    expect(useConfigStore.getState().isDirty).toBe(false);
  });

  it("is dirty when a real scalar value changes (temperature)", () => {
    useConfigStore.getState().setOriginal(liveConfig());

    const original = useConfigStore.getState().original!;
    const draft: ForgeConfig = structuredClone(original);
    draft.llm.temperature = 0.5;

    useConfigStore.getState().updateDraft(draft);

    expect(useConfigStore.getState().isDirty).toBe(true);
  });

  it("is dirty when a real model_list entry is added", () => {
    useConfigStore.getState().setOriginal(liveConfig());

    const original = useConfigStore.getState().original!;
    const draft: ForgeConfig = structuredClone(original);
    draft.llm.litellm!.model_list!.push({
      model_name: "backup",
      litellm_params: { model: "openai/gpt-4o-mini" },
    });

    useConfigStore.getState().updateDraft(draft);

    expect(useConfigStore.getState().isDirty).toBe(true);
  });

  it("is dirty when array element order changes, even though the elements are unchanged", () => {
    const config = liveConfig();
    config.llm.litellm!.model_list = [
      { model_name: "primary", litellm_params: { model: "openai/gpt-4o" } },
      { model_name: "backup", litellm_params: { model: "openai/gpt-4o-mini" } },
    ];
    useConfigStore.getState().setOriginal(config);

    const original = useConfigStore.getState().original!;
    const draft: ForgeConfig = structuredClone(original);
    draft.llm.litellm!.model_list!.reverse();

    useConfigStore.getState().updateDraft(draft);

    expect(useConfigStore.getState().isDirty).toBe(true);
  });
});
