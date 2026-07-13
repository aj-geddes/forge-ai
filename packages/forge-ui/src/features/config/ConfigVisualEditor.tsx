import { useEffect } from "react";
import {
  useForm,
  useFieldArray,
  Controller,
  type Control,
  type UseFormRegister,
} from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Link } from "react-router-dom";
import {
  ExternalLink,
  Info,
  Cpu,
  Shield,
  Users,
  Wrench,
  Sparkles,
  Plus,
  Trash2,
} from "lucide-react";
import {
  Accordion,
  AccordionItem,
  AccordionTrigger,
  AccordionContent,
} from "@/components/ui/accordion";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { useConfigStore } from "@/stores/configStore";
import type { AgentDef, ForgeConfig, LiteLLMMode, TrustPolicy } from "@/types/config";

// --- Help text component ---

function HelpText({ children }: { children: React.ReactNode }) {
  return (
    <p className="flex items-start gap-1.5 text-xs text-muted-foreground mt-1.5 leading-relaxed">
      <Info className="h-3 w-3 mt-0.5 shrink-0 opacity-60" />
      <span>{children}</span>
    </p>
  );
}

function SectionDescription({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-sm text-muted-foreground mb-4 pb-3 border-b border-border/50">
      {children}
    </p>
  );
}

// --- Model options ---

const MODEL_OPTIONS = [
  {
    group: "OpenAI",
    models: [
      { value: "gpt-4o", label: "GPT-4o", desc: "Best overall — fast, multimodal, strong reasoning" },
      { value: "gpt-4o-mini", label: "GPT-4o Mini", desc: "Cost-effective for simpler tasks" },
      { value: "gpt-4-turbo", label: "GPT-4 Turbo", desc: "High capability, 128K context" },
      { value: "o1", label: "o1", desc: "Advanced reasoning, slower but more accurate" },
      { value: "o1-mini", label: "o1-mini", desc: "Fast reasoning for code and math" },
      { value: "o3-mini", label: "o3-mini", desc: "Latest reasoning model, cost-efficient" },
    ],
  },
  {
    group: "Anthropic",
    models: [
      { value: "claude-opus-4-6", label: "Claude Opus 4.6", desc: "Most capable, best for complex tasks" },
      { value: "claude-sonnet-4-6", label: "Claude Sonnet 4.6", desc: "Balanced speed and intelligence" },
      { value: "claude-haiku-4-5-20251001", label: "Claude Haiku 4.5", desc: "Fastest, ideal for high-volume" },
    ],
  },
  {
    group: "Google",
    models: [
      { value: "gemini/gemini-2.0-flash", label: "Gemini 2.0 Flash", desc: "Fast multimodal with tool use" },
      { value: "gemini/gemini-2.5-pro-preview", label: "Gemini 2.5 Pro", desc: "Best quality, 1M context" },
    ],
  },
  {
    group: "Local / Open Source",
    models: [
      { value: "ollama/llama3.3", label: "Llama 3.3 (Ollama)", desc: "Local, 70B parameter open model" },
      { value: "ollama/mistral", label: "Mistral (Ollama)", desc: "Local, fast 7B model" },
      { value: "ollama/deepseek-r1", label: "DeepSeek R1 (Ollama)", desc: "Local reasoning model" },
    ],
  },
] as const;

// --- Zod Schema ---

// NOTE: the backend's LiteLLMConfig (forge_config/schema.py) only has
// {mode, endpoint, model_list, fallback_models, timeout, max_retries}. There
// is no config_path or port field -- a PUT with those fields would have them
// silently dropped by pydantic. Tracked as missing backend support if a
// standalone LiteLLM proxy process needs to be launched from a config file.
//
// model_list is `list[dict[str, Any]]` on the backend -- a freeform LiteLLM
// router entry, typically `{model_name, litellm_params: {model, api_key,
// ...}}`. The form only exposes the documented subset (model_name, the
// routed model id, and the api_key env var name); any other keys on an
// existing entry's `litellm_params` (e.g. api_base) are preserved verbatim
// by formToConfig, keyed by array index.
const modelListEntrySchema = z.object({
  model_name: z.string().min(1, "Model name is required"),
  model: z.string().min(1, "Model identifier is required"),
  api_key_env: z.string().optional(),
});

const litellmSchema = z.object({
  mode: z.enum(["embedded", "sidecar", "external"]),
  endpoint: z.string().optional(),
  model_list: z.array(modelListEntrySchema).optional(),
  // Comma-separated string in the form; mapped to the backend's
  // `fallback_models: string[]` field.
  fallback_models: z.string().optional(),
  timeout: z.coerce.number().positive().optional(),
  max_retries: z.coerce.number().int().nonnegative().optional(),
});

const metadataSchema = z.object({
  name: z.string().min(1, "Name is required"),
  version: z.string().min(1, "Version is required"),
  description: z.string().optional(),
  environment: z.enum(["development", "staging", "production"]).optional(),
});

const llmSchema = z.object({
  model: z.string().min(1, "Model is required"),
  temperature: z.coerce.number().min(0).max(2).optional(),
  max_tokens: z.coerce.number().int().positive().optional(),
  system_prompt: z.string().optional(),
  litellm: litellmSchema.optional(),
});

const securitySchema = z.object({
  agentweave_enabled: z.boolean(),
  trust_domain: z.string().optional(),
  trust_policy: z.enum(["strict", "permissive"]).optional(),
  rate_limit_rpm: z.coerce.number().int().positive().optional(),
  // Comma-separated string in the form; mapped to the backend's flat
  // `allowed_origins: string[]` field (not `cors_origins`).
  cors_origins: z.string().optional(),
  // NOTE: `security.api_keys` (forge_config.schema.APIKeyConfig) is
  // deliberately NOT exposed here -- it is deprecated (ADR-0001 SS11),
  // emits a DeprecationWarning on construction, and is translated into a
  // synthetic service token. The form neither reads nor writes it.
});

// AgentDef.tools is a `list[str]` tool-name filter on the backend; the form
// represents it as a comma-separated string, mirroring the
// fallback_models/cors_origins convention above.
const agentDefSchema = z.object({
  name: z.string().min(1, "Name is required"),
  description: z.string().optional(),
  system_prompt: z.string().optional(),
  model: z.string().optional(),
  tools: z.string().optional(),
  max_turns: z.coerce.number().int().positive().optional(),
});

const agentsSchema = z.object({
  default_agent_name: z.string().optional(),
  agents: z.array(agentDefSchema).optional(),
});

const formSchema = z.object({
  metadata: metadataSchema,
  llm: llmSchema,
  security: securitySchema,
  agents: agentsSchema,
});

type FormValues = z.infer<typeof formSchema>;
type ModelListEntryForm = z.infer<typeof modelListEntrySchema>;
type AgentDefForm = z.infer<typeof agentDefSchema>;

// --- Helpers ---
//
// These mirror the backend's ForgeConfig contract field-for-field
// (forge_config/schema.py). In particular:
//   - metadata.environment is a direct string field -- there is no
//     metadata.labels map on the backend.
//   - security.agentweave.trust_policy lives under `agentweave`, not at the
//     top level of `security`.
//   - security.rate_limit_rpm is a flat int, not nested under
//     `rate_limit.requests_per_minute`.
//   - security.allowed_origins is the CORS field name, not `cors_origins`.
//   - agents.default is a plain persona name string, not a `default_agent`
//     object.
// llm.litellm intentionally has no config_path/port fields in the form --
// the backend's LiteLLMConfig does not define them (see litellmSchema above).

function modelListEntryToForm(entry: Record<string, unknown>): ModelListEntryForm {
  const litellmParams = (entry.litellm_params ?? {}) as Record<string, unknown>;
  const apiKey = litellmParams.api_key;
  const apiKeyEnv =
    apiKey && typeof apiKey === "object" && "name" in (apiKey as Record<string, unknown>)
      ? String((apiKey as Record<string, unknown>).name)
      : "";
  return {
    model_name: typeof entry.model_name === "string" ? entry.model_name : "",
    model: typeof litellmParams.model === "string" ? litellmParams.model : "",
    api_key_env: apiKeyEnv,
  };
}

function agentDefToForm(agent: AgentDef): AgentDefForm {
  return {
    name: agent.name,
    description: agent.description ?? "",
    system_prompt: agent.system_prompt ?? "",
    model: agent.model ?? "",
    tools: (agent.tools ?? []).join(", "),
    max_turns: agent.max_turns,
  };
}

export function configToForm(config: ForgeConfig): FormValues {
  return {
    metadata: {
      name: config.metadata.name,
      version: config.metadata.version,
      description: config.metadata.description ?? "",
      environment: (config.metadata.environment as
        | "development"
        | "staging"
        | "production"
        | undefined) ?? undefined,
    },
    llm: {
      model: config.llm.default_model,
      temperature: config.llm.temperature,
      max_tokens: config.llm.max_tokens,
      system_prompt: config.llm.system_prompt ?? undefined,
      litellm: config.llm.litellm
        ? {
            mode: config.llm.litellm.mode,
            endpoint: config.llm.litellm.endpoint ?? undefined,
            model_list: (config.llm.litellm.model_list ?? []).map(modelListEntryToForm),
            fallback_models: (config.llm.litellm.fallback_models ?? []).join(", "),
            timeout: config.llm.litellm.timeout,
            max_retries: config.llm.litellm.max_retries,
          }
        : undefined,
    },
    security: {
      agentweave_enabled: config.security?.agentweave?.enabled ?? false,
      trust_domain: config.security?.agentweave?.trust_domain ?? "",
      trust_policy: config.security?.agentweave?.trust_policy ?? undefined,
      rate_limit_rpm: config.security?.rate_limit_rpm,
      cors_origins: config.security?.allowed_origins?.join(", ") ?? "",
    },
    agents: {
      default_agent_name: config.agents?.default ?? "",
      agents: (config.agents?.agents ?? []).map(agentDefToForm),
    },
  };
}

function modelListEntryFromForm(
  entry: ModelListEntryForm,
  existingEntry: Record<string, unknown> | undefined,
): Record<string, unknown> {
  const existingParams = (existingEntry?.litellm_params ?? {}) as Record<string, unknown>;
  return {
    ...existingEntry,
    model_name: entry.model_name,
    litellm_params: {
      ...existingParams,
      model: entry.model,
      ...(entry.api_key_env
        ? { api_key: { source: "env", name: entry.api_key_env } }
        : {}),
    },
  };
}

function agentDefFromForm(agent: AgentDefForm): AgentDef {
  return {
    name: agent.name,
    description: agent.description || undefined,
    system_prompt: agent.system_prompt || undefined,
    model: agent.model || undefined,
    tools: agent.tools
      ? agent.tools
          .split(",")
          .map((t) => t.trim())
          .filter(Boolean)
      : [],
    max_turns: agent.max_turns,
  };
}

export function formToConfig(
  form: FormValues,
  existing: ForgeConfig,
): ForgeConfig {
  return {
    ...existing,
    metadata: {
      ...existing.metadata,
      name: form.metadata.name,
      version: form.metadata.version,
      description: form.metadata.description || undefined,
      environment: form.metadata.environment || existing.metadata.environment,
    },
    llm: {
      ...existing.llm,
      default_model: form.llm.model,
      temperature: form.llm.temperature,
      max_tokens: form.llm.max_tokens,
      system_prompt: form.llm.system_prompt || null,
      litellm: form.llm.litellm
        ? {
            ...existing.llm.litellm,
            mode: form.llm.litellm.mode as LiteLLMMode,
            endpoint: form.llm.litellm.endpoint || undefined,
            model_list: (form.llm.litellm.model_list ?? []).map((entry, index) =>
              modelListEntryFromForm(entry, existing.llm.litellm?.model_list?.[index]),
            ),
            fallback_models: form.llm.litellm.fallback_models
              ? form.llm.litellm.fallback_models
                  .split(",")
                  .map((s) => s.trim())
                  .filter(Boolean)
              : [],
            timeout: form.llm.litellm.timeout,
            max_retries: form.llm.litellm.max_retries,
          }
        : existing.llm.litellm,
    },
    security: {
      ...existing.security,
      agentweave: {
        ...(existing.security?.agentweave ?? { enabled: false }),
        enabled: form.security.agentweave_enabled,
        trust_domain: form.security.trust_domain || undefined,
        trust_policy: (form.security.trust_policy as TrustPolicy) || undefined,
      },
      rate_limit_rpm: form.security.rate_limit_rpm ?? existing.security?.rate_limit_rpm,
      allowed_origins: form.security.cors_origins
        ? form.security.cors_origins
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean)
        : existing.security?.allowed_origins,
    },
    agents: {
      ...existing.agents,
      default: form.agents.default_agent_name || existing.agents?.default,
      agents: (form.agents.agents ?? []).map(agentDefFromForm),
    },
  };
}

// --- Repeatable list editors ---

function ModelListEditor({
  control,
  register,
}: {
  control: Control<FormValues>;
  register: UseFormRegister<FormValues>;
}) {
  const { fields, append, remove } = useFieldArray({
    control,
    name: "llm.litellm.model_list",
  });

  return (
    <div className="sm:col-span-2 mt-4 space-y-3">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm font-medium">Model List</p>
          <p className="text-xs text-muted-foreground">
            Additional model routes LiteLLM can dispatch to, beyond the default model above.
          </p>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => append({ model_name: "", model: "", api_key_env: "" })}
        >
          <Plus className="h-4 w-4" />
          Add Model
        </Button>
      </div>

      {fields.length === 0 ? (
        <div className="flex h-16 items-center justify-center rounded-lg border border-dashed">
          <p className="text-sm text-muted-foreground">No additional models configured.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {fields.map((field, index) => (
            <div key={field.id} className="rounded-lg border p-3 space-y-2">
              <div className="flex items-start gap-2">
                <div className="flex-1 grid gap-2 sm:grid-cols-3">
                  <div>
                    <Label className="text-xs">Model Name</Label>
                    <Input
                      placeholder="my-fast-model"
                      className="h-8 text-sm"
                      {...register(`llm.litellm.model_list.${index}.model_name` as const)}
                    />
                  </div>
                  <div>
                    <Label className="text-xs">Model ID</Label>
                    <Input
                      placeholder="openai/gpt-4o-mini"
                      className="h-8 text-sm"
                      {...register(`llm.litellm.model_list.${index}.model` as const)}
                    />
                  </div>
                  <div>
                    <Label className="text-xs">API Key Env Var</Label>
                    <Input
                      placeholder="OPENAI_API_KEY"
                      className="h-8 text-sm"
                      {...register(`llm.litellm.model_list.${index}.api_key_env` as const)}
                    />
                  </div>
                </div>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="mt-4 shrink-0"
                  onClick={() => remove(index)}
                >
                  <Trash2 className="h-4 w-4 text-destructive" />
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}
      <HelpText>
        Each entry routes a friendly model name to a provider-specific model ID. The API key
        env var names the environment variable Forge resolves the secret from at runtime --
        the literal key value is never stored in the config.
      </HelpText>
    </div>
  );
}

function AgentDefinitionsEditor({
  control,
  register,
}: {
  control: Control<FormValues>;
  register: UseFormRegister<FormValues>;
}) {
  const { fields, append, remove } = useFieldArray({
    control,
    name: "agents.agents",
  });

  return (
    <div className="sm:col-span-2 space-y-3">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm font-medium">Agent Definitions</p>
          <p className="text-xs text-muted-foreground">
            Named personas your agent can be invoked as, each with its own prompt, model, and
            tool filter.
          </p>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() =>
            append({
              name: "",
              description: "",
              system_prompt: "",
              model: "",
              tools: "",
              max_turns: 10,
            })
          }
        >
          <Plus className="h-4 w-4" />
          Add Agent
        </Button>
      </div>

      {fields.length === 0 ? (
        <div className="flex h-16 items-center justify-center rounded-lg border border-dashed">
          <p className="text-sm text-muted-foreground">No agent personas defined yet.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {fields.map((field, index) => (
            <div key={field.id} className="rounded-lg border p-3 space-y-2">
              <div className="flex items-start gap-2">
                <div className="flex-1 space-y-2">
                  <div className="grid gap-2 sm:grid-cols-2">
                    <div>
                      <Label className="text-xs">Name</Label>
                      <Input
                        placeholder="researcher"
                        className="h-8 text-sm"
                        {...register(`agents.agents.${index}.name` as const)}
                      />
                    </div>
                    <div>
                      <Label className="text-xs">Model Override</Label>
                      <Input
                        placeholder="(uses default model)"
                        className="h-8 text-sm"
                        {...register(`agents.agents.${index}.model` as const)}
                      />
                    </div>
                  </div>
                  <div>
                    <Label className="text-xs">Description</Label>
                    <Input
                      placeholder="What this persona is for"
                      className="h-8 text-sm"
                      {...register(`agents.agents.${index}.description` as const)}
                    />
                  </div>
                  <div>
                    <Label className="text-xs">System Prompt</Label>
                    <Textarea
                      placeholder="You are a specialist in..."
                      rows={2}
                      className="text-sm"
                      {...register(`agents.agents.${index}.system_prompt` as const)}
                    />
                  </div>
                  <div className="grid gap-2 sm:grid-cols-2">
                    <div>
                      <Label className="text-xs">Tools (comma-separated)</Label>
                      <Input
                        placeholder="web_search, summarize"
                        className="h-8 text-sm"
                        {...register(`agents.agents.${index}.tools` as const)}
                      />
                    </div>
                    <div>
                      <Label className="text-xs">Max Turns</Label>
                      <Input
                        type="number"
                        min={1}
                        placeholder="10"
                        className="h-8 text-sm"
                        {...register(`agents.agents.${index}.max_turns` as const)}
                      />
                    </div>
                  </div>
                </div>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="mt-4 shrink-0"
                  onClick={() => remove(index)}
                >
                  <Trash2 className="h-4 w-4 text-destructive" />
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// --- Component ---

export function ConfigVisualEditor() {
  const { draft, updateDraft } = useConfigStore();

  const {
    register,
    control,
    watch,
    reset,
    formState: { errors },
  } = useForm<FormValues>({
    resolver: zodResolver(formSchema),
    defaultValues: draft ? configToForm(draft) : undefined,
    mode: "onChange",
  });

  // Reset form when draft changes externally (e.g., from YAML editor)
  useEffect(() => {
    if (draft) {
      reset(configToForm(draft));
    }
  }, [draft, reset]);

  // Sync form changes back to the store
  useEffect(() => {
    const subscription = watch((values) => {
      if (!draft) return;
      const parsed = formSchema.safeParse(values);
      if (parsed.success) {
        updateDraft(formToConfig(parsed.data, draft));
      }
    });
    return () => subscription.unsubscribe();
  }, [watch, draft, updateDraft]);

  const litellmMode = watch("llm.litellm.mode");
  const currentModel = watch("llm.model");

  if (!draft) {
    return (
      <div className="flex items-center justify-center py-12 text-muted-foreground">
        No configuration loaded
      </div>
    );
  }

  const openapiCount = draft.tools.openapi_sources?.length ?? 0;
  const manualCount = draft.tools.manual_tools?.length ?? 0;
  const workflowCount = draft.tools.workflows?.length ?? 0;
  const peersCount = draft.agents?.peers?.length ?? 0;

  // Check if current model is in our preset list
  const isCustomModel = !MODEL_OPTIONS.some((g) =>
    g.models.some((m) => m.value === currentModel),
  );

  return (
    <div className="space-y-2">
      <Accordion>
        {/* Metadata Section */}
        <AccordionItem open>
          <AccordionTrigger>
            <span className="flex items-center gap-2">
              <Sparkles className="h-4 w-4 text-primary" />
              Identity
            </span>
          </AccordionTrigger>
          <AccordionContent>
            <SectionDescription>
              Identifies your Forge agent instance. The name and version appear in health checks,
              the A2A agent card, and the control plane header.
            </SectionDescription>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="metadata.name">Agent Name</Label>
                <Input
                  id="metadata.name"
                  placeholder="my-forge-agent"
                  {...register("metadata.name")}
                />
                <HelpText>
                  A unique identifier for this agent instance. Used in logging, peer
                  discovery, and the A2A agent card.
                </HelpText>
                {errors.metadata?.name && (
                  <p className="text-xs text-destructive">
                    {errors.metadata.name.message}
                  </p>
                )}
              </div>

              <div className="space-y-2">
                <Label htmlFor="metadata.version">Version</Label>
                <Input
                  id="metadata.version"
                  placeholder="1.0.0"
                  {...register("metadata.version")}
                />
                <HelpText>
                  Semantic version of your agent configuration. Useful for tracking changes
                  and displayed in health endpoints.
                </HelpText>
                {errors.metadata?.version && (
                  <p className="text-xs text-destructive">
                    {errors.metadata.version.message}
                  </p>
                )}
              </div>

              <div className="space-y-2 sm:col-span-2">
                <Label htmlFor="metadata.description">Description</Label>
                <Textarea
                  id="metadata.description"
                  placeholder="Describe what this agent does..."
                  rows={2}
                  {...register("metadata.description")}
                />
                <HelpText>
                  A human-readable summary shown in the dashboard and shared with peer agents during
                  discovery.
                </HelpText>
              </div>

              <div className="space-y-2">
                <Label htmlFor="metadata.environment">Environment</Label>
                <Select
                  id="metadata.environment"
                  {...register("metadata.environment")}
                >
                  <option value="">Select environment...</option>
                  <option value="development">Development</option>
                  <option value="staging">Staging</option>
                  <option value="production">Production</option>
                </Select>
                <HelpText>
                  Controls logging verbosity and default security posture. Production enables
                  stricter validation.
                </HelpText>
              </div>
            </div>
          </AccordionContent>
        </AccordionItem>

        {/* LLM Section */}
        <AccordionItem>
          <AccordionTrigger>
            <span className="flex items-center gap-2">
              <Cpu className="h-4 w-4 text-primary" />
              LLM Configuration
            </span>
          </AccordionTrigger>
          <AccordionContent>
            <SectionDescription>
              Controls which language model powers your agent. The model choice directly affects
              response quality, speed, cost, and which capabilities (tool calling, vision, reasoning)
              are available.
            </SectionDescription>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2 sm:col-span-2">
                <Label htmlFor="llm.model">Default Model</Label>
                <Select
                  id="llm.model"
                  {...register("llm.model")}
                >
                  {MODEL_OPTIONS.map((group) => (
                    <optgroup key={group.group} label={group.group}>
                      {group.models.map((m) => (
                        <option key={m.value} value={m.value}>
                          {m.label} — {m.desc}
                        </option>
                      ))}
                    </optgroup>
                  ))}
                  {isCustomModel && currentModel && (
                    <optgroup label="Current">
                      <option value={currentModel}>{currentModel} (custom)</option>
                    </optgroup>
                  )}
                </Select>
                <HelpText>
                  The LLM that processes all agent requests. Routed through LiteLLM, so you can use
                  any provider (OpenAI, Anthropic, Google, or local models via Ollama). More capable
                  models produce better results but cost more and respond slower.
                </HelpText>
              </div>

              <div className="space-y-2">
                <Label htmlFor="llm.temperature">
                  Temperature
                </Label>
                <Input
                  id="llm.temperature"
                  type="number"
                  min={0}
                  max={2}
                  step={0.1}
                  placeholder="0.7"
                  {...register("llm.temperature")}
                />
                <HelpText>
                  Controls randomness in responses. Lower values (0&ndash;0.3) give focused,
                  deterministic answers &mdash; good for code and factual tasks. Higher values
                  (0.7&ndash;1.5) produce more creative, varied responses. Default is 0.7.
                </HelpText>
                {errors.llm?.temperature && (
                  <p className="text-xs text-destructive">
                    {errors.llm.temperature.message}
                  </p>
                )}
              </div>

              <div className="space-y-2">
                <Label htmlFor="llm.max_tokens">Max Tokens</Label>
                <Input
                  id="llm.max_tokens"
                  type="number"
                  min={1}
                  placeholder="4096"
                  {...register("llm.max_tokens")}
                />
                <HelpText>
                  Maximum length of the model&apos;s response in tokens (~4 chars per token).
                  4096 is a good default. Increase for tasks that need long outputs (code generation,
                  detailed analysis). Higher values use more API credits.
                </HelpText>
              </div>

              <div className="space-y-2 sm:col-span-2">
                <Label htmlFor="llm.system_prompt">System Prompt</Label>
                <Textarea
                  id="llm.system_prompt"
                  placeholder="You are a helpful assistant that..."
                  rows={4}
                  {...register("llm.system_prompt")}
                />
                <HelpText>
                  Instructions that shape your agent&apos;s personality, expertise, and behavior.
                  This is prepended to every conversation. Be specific about the role, tone,
                  constraints, and what tools to prefer. Leave blank to use the per-agent
                  system prompts defined in the Agents section.
                </HelpText>
              </div>

              {/* LiteLLM subsection */}
              <div className="sm:col-span-2 rounded-lg border border-border/50 bg-muted/30 p-4">
                <h4 className="mb-1 text-sm font-semibold flex items-center gap-2">
                  LiteLLM Router
                </h4>
                <p className="mb-4 text-xs text-muted-foreground">
                  LiteLLM handles model routing, load balancing, and failover. It translates
                  all model calls into a unified interface so you can swap providers without
                  code changes.
                </p>
                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="space-y-2">
                    <Label htmlFor="llm.litellm.mode">Mode</Label>
                    <Select
                      id="llm.litellm.mode"
                      {...register("llm.litellm.mode")}
                    >
                      <option value="embedded">Embedded &mdash; runs inside the agent process</option>
                      <option value="sidecar">Sidecar &mdash; separate container in the same pod</option>
                      <option value="external">External &mdash; dedicated LiteLLM proxy service</option>
                    </Select>
                    <HelpText>
                      <strong>Embedded</strong> is simplest for development (zero extra setup).{" "}
                      <strong>Sidecar</strong> isolates LLM routing for better resource control.{" "}
                      <strong>External</strong> is best for production &mdash; a shared proxy that
                      multiple agents connect to, with its own scaling and caching.
                    </HelpText>
                  </div>

                  {(litellmMode === "sidecar" ||
                    litellmMode === "external") && (
                    <div className="space-y-2">
                      <Label htmlFor="llm.litellm.endpoint">Endpoint URL</Label>
                      <Input
                        id="llm.litellm.endpoint"
                        placeholder="http://litellm:4000"
                        {...register("llm.litellm.endpoint")}
                      />
                      <HelpText>
                        The URL where the LiteLLM proxy is reachable. For sidecar mode, this is
                        typically <code className="text-xs">http://localhost:4000</code>. For
                        external mode, use the service DNS name.
                      </HelpText>
                    </div>
                  )}

                  <div className="space-y-2">
                    <Label htmlFor="llm.litellm.timeout">Timeout (seconds)</Label>
                    <Input
                      id="llm.litellm.timeout"
                      type="number"
                      min={0}
                      step={0.5}
                      placeholder="30"
                      {...register("llm.litellm.timeout")}
                    />
                    <HelpText>
                      How long to wait for an LLM response before giving up. Default is 30
                      seconds. Increase for slower local/self-hosted models.
                    </HelpText>
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="llm.litellm.max_retries">Max Retries</Label>
                    <Input
                      id="llm.litellm.max_retries"
                      type="number"
                      min={0}
                      placeholder="3"
                      {...register("llm.litellm.max_retries")}
                    />
                    <HelpText>
                      Number of retry attempts for a failed LLM request (timeouts, rate limits,
                      transient provider errors). Default is 3.
                    </HelpText>
                  </div>

                  <div className="space-y-2 sm:col-span-2">
                    <Label htmlFor="llm.litellm.fallback_models">Fallback Models</Label>
                    <Input
                      id="llm.litellm.fallback_models"
                      placeholder="gpt-4o-mini, claude-haiku-4-5-20251001"
                      {...register("llm.litellm.fallback_models")}
                    />
                    <HelpText>
                      Comma-separated list of models to try, in order, if the default model is
                      unavailable or errors out. LiteLLM automatically fails over to the next
                      entry in this list.
                    </HelpText>
                  </div>
                </div>

                <ModelListEditor control={control} register={register} />
              </div>
            </div>
          </AccordionContent>
        </AccordionItem>

        {/* Tools Section */}
        <AccordionItem>
          <AccordionTrigger>
            <span className="flex items-center gap-2">
              <Wrench className="h-4 w-4 text-primary" />
              Tools
            </span>
          </AccordionTrigger>
          <AccordionContent>
            <SectionDescription>
              Tools give your agent the ability to take actions &mdash; call APIs, query databases,
              execute workflows. Without tools, the agent can only answer from its training data.
            </SectionDescription>
            <div className="space-y-3">
              <div className="flex flex-wrap gap-2">
                <Badge variant="secondary">
                  {openapiCount} OpenAPI source{openapiCount !== 1 ? "s" : ""}
                </Badge>
                <Badge variant="secondary">
                  {manualCount} manual tool{manualCount !== 1 ? "s" : ""}
                </Badge>
                <Badge variant="secondary">
                  {workflowCount} workflow{workflowCount !== 1 ? "s" : ""}
                </Badge>
              </div>
              <p className="text-sm text-muted-foreground">
                Tools are managed in the{" "}
                <Link
                  to="/tools"
                  className="inline-flex items-center gap-1 text-primary underline-offset-4 hover:underline"
                >
                  Tool Workshop
                  <ExternalLink className="h-3 w-3" />
                </Link>
                {" "}where you can import from OpenAPI specs, define custom tools, or compose
                multi-step workflows.
              </p>
              <HelpText>
                <strong>OpenAPI</strong> tools are auto-generated from API specs &mdash; point at a
                URL and Forge imports all operations. <strong>Manual</strong> tools let you define
                a single API call with custom parameters. <strong>Workflows</strong> chain multiple
                tools together with variable passing between steps.
              </HelpText>
            </div>
          </AccordionContent>
        </AccordionItem>

        {/* Security Section */}
        <AccordionItem>
          <AccordionTrigger>
            <span className="flex items-center gap-2">
              <Shield className="h-4 w-4 text-primary" />
              Security
            </span>
          </AccordionTrigger>
          <AccordionContent>
            <SectionDescription>
              Controls authentication, authorization, and rate limiting for your agent.
              Security settings determine who can call your agent and how requests are validated.
            </SectionDescription>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="sm:col-span-2 flex items-center justify-between rounded-lg border border-border/50 bg-muted/30 p-4">
                <div className="flex items-center gap-3">
                  <Controller
                    name="security.agentweave_enabled"
                    control={control}
                    render={({ field }) => (
                      <Switch
                        id="security.agentweave_enabled"
                        checked={field.value}
                        onCheckedChange={field.onChange}
                      />
                    )}
                  />
                  <div>
                    <Label htmlFor="security.agentweave_enabled" className="text-sm font-semibold">
                      AgentWeave Security Framework
                    </Label>
                    <p className="text-xs text-muted-foreground mt-0.5">
                      Enterprise security: identity verification (SPIFFE), message signing (JWT),
                      authorization (OPA), and audit logging for every request.
                    </p>
                  </div>
                </div>
              </div>

              <div className="space-y-2">
                <Label htmlFor="security.trust_domain">Trust Domain</Label>
                <Input
                  id="security.trust_domain"
                  placeholder="forge.local"
                  {...register("security.trust_domain")}
                />
                <HelpText>
                  The SPIFFE trust domain for identity verification. Agents within the same trust
                  domain can authenticate each other. Use your organization&apos;s domain in
                  production.
                </HelpText>
              </div>

              <div className="space-y-2">
                <Label htmlFor="security.trust_policy">Trust Policy</Label>
                <Select
                  id="security.trust_policy"
                  {...register("security.trust_policy")}
                >
                  <option value="">Select policy...</option>
                  <option value="strict">Strict &mdash; reject unverified callers</option>
                  <option value="permissive">Permissive &mdash; warn but allow</option>
                </Select>
                <HelpText>
                  <strong>Strict</strong> rejects any request that fails identity or trust checks
                  &mdash; use for production. <strong>Permissive</strong> logs warnings but allows
                  requests through &mdash; useful during development and testing.
                </HelpText>
              </div>

              <div className="space-y-2">
                <Label htmlFor="security.rate_limit_rpm">
                  Rate Limit
                  <span className="text-muted-foreground font-normal ml-1">(requests/min)</span>
                </Label>
                <Input
                  id="security.rate_limit_rpm"
                  type="number"
                  min={1}
                  placeholder="60"
                  {...register("security.rate_limit_rpm")}
                />
                <HelpText>
                  Maximum requests per minute per caller. Protects against abuse and runaway
                  automation. The default of 60 allows ~1 request/second. Set higher for
                  batch-processing agents.
                </HelpText>
              </div>

              <div className="space-y-2">
                <Label htmlFor="security.cors_origins">
                  Allowed Origins
                  <span className="text-muted-foreground font-normal ml-1">(CORS)</span>
                </Label>
                <Input
                  id="security.cors_origins"
                  placeholder="https://app.example.com, https://admin.example.com"
                  {...register("security.cors_origins")}
                />
                <HelpText>
                  Comma-separated list of domains that can make browser requests to this agent.
                  Use <code className="text-xs">*</code> for development. In production, list only
                  your actual frontend domains.
                </HelpText>
              </div>
            </div>
          </AccordionContent>
        </AccordionItem>

        {/* Agents Section */}
        <AccordionItem>
          <AccordionTrigger>
            <span className="flex items-center gap-2">
              <Users className="h-4 w-4 text-primary" />
              Agents
            </span>
          </AccordionTrigger>
          <AccordionContent>
            <SectionDescription>
              Define named agent personas with different system prompts, model overrides, and
              tool access. Each persona behaves like a specialized expert. Callers select a
              persona by name when making requests.
            </SectionDescription>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="agents.default_agent_name">
                  Default Agent
                </Label>
                <Input
                  id="agents.default_agent_name"
                  placeholder="assistant"
                  {...register("agents.default_agent_name")}
                />
                <HelpText>
                  The agent persona used when no specific agent is requested. This should match one
                  of the names defined in your agents list in the YAML config.
                </HelpText>
              </div>

              <div className="flex items-end">
                <div className="space-y-2">
                  <Badge variant="secondary">
                    {peersCount} peer{peersCount !== 1 ? "s" : ""} configured
                  </Badge>
                  <p className="text-sm text-muted-foreground">
                    <Link
                      to="/peers"
                      className="inline-flex items-center gap-1 text-primary underline-offset-4 hover:underline"
                    >
                      Manage peer agents
                      <ExternalLink className="h-3 w-3" />
                    </Link>
                    {" "}&mdash; connect to other Forge agents for cross-agent collaboration.
                  </p>
                </div>
              </div>

              <AgentDefinitionsEditor control={control} register={register} />
            </div>
          </AccordionContent>
        </AccordionItem>
      </Accordion>
    </div>
  );
}
