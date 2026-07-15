import { useEffect, useRef } from "react";
import { useForm, Controller } from "react-hook-form";
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
  Lock,
} from "lucide-react";
import {
  Accordion,
  AccordionItem,
  AccordionTrigger,
  AccordionContent,
} from "@/components/ui/accordion";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { useConfigStore } from "@/stores/configStore";
import type { ForgeConfig, TrustPolicy } from "@/types/config";

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
// RHF's uncontrolled <input type="number"> / <select> fields report an
// empty string (never `undefined`) when a user hasn't touched an optional
// field. Left un-normalized, z.coerce.number() turns "" into 0 (which then
// fails a `.positive()` bound) and z.enum() rejects "" outright -- so the
// *entire* form fails validation on every keystroke whenever any unrelated
// optional numeric/enum field is unset, silently blocking every sync to the
// config store (including real edits). Preprocess "" (and null) to
// `undefined` so these fields behave as genuinely optional.
function blankToUndefined(value: unknown): unknown {
  return value === "" || value === null ? undefined : value;
}

// mode, endpoint, and model_list are BASE-ONLY (Phase-1 field split): mode
// selects the outbound LiteLLM proxy destination, endpoint IS a
// destination, and model_list carries both destinations (api_base) and
// secrets (api_key) for every routed model. None of the three is
// represented in the form schema below -- they are read-only, sourced
// directly from `draft.llm.litellm` (see ReadOnlyLiteLLMPanel), and
// formToConfig always echoes the existing value back unchanged (see the
// NOTE on formToConfig's litellm block).
const litellmSchema = z.object({
  // Comma-separated string in the form; mapped to the backend's
  // `fallback_models: string[]` field. A SELECTION over model_name
  // aliases, not a destination -- runtime-safe per the field split.
  fallback_models: z.string().optional(),
  timeout: z.preprocess(blankToUndefined, z.coerce.number().positive().optional()),
  max_retries: z.preprocess(blankToUndefined, z.coerce.number().int().nonnegative().optional()),
});

const metadataSchema = z.object({
  name: z.string().min(1, "Name is required"),
  version: z.string().min(1, "Version is required"),
  description: z.string().optional(),
  environment: z.preprocess(
    blankToUndefined,
    z.enum(["development", "staging", "production"]).optional(),
  ),
});

const llmSchema = z.object({
  model: z.string().min(1, "Model is required"),
  temperature: z.preprocess(blankToUndefined, z.coerce.number().min(0).max(2).optional()),
  max_tokens: z.preprocess(blankToUndefined, z.coerce.number().int().positive().optional()),
  system_prompt: z.string().optional(),
  litellm: litellmSchema.optional(),
});

const securitySchema = z.object({
  agentweave_enabled: z.boolean(),
  trust_domain: z.string().optional(),
  trust_policy: z.preprocess(blankToUndefined, z.enum(["strict", "permissive"]).optional()),
  rate_limit_rpm: z.preprocess(blankToUndefined, z.coerce.number().int().positive().optional()),
  // Comma-separated string in the form; mapped to the backend's flat
  // `allowed_origins: string[]` field (not `cors_origins`).
  cors_origins: z.string().optional(),
  // NOTE: `security.api_keys` (forge_config.schema.APIKeyConfig) is
  // deliberately NOT exposed here -- it is deprecated (ADR-0001 SS11),
  // emits a DeprecationWarning on construction, and is translated into a
  // synthetic service token. The form neither reads nor writes it.
});

// Full agent (persona) create/edit/delete now lives on the dedicated
// Agents page (/agents), which talks to the name-keyed overlay endpoints
// (POST/PATCH/DELETE /v1/admin/agents) directly -- not this wholesale-PUT
// visual editor. `agents.agents` is intentionally NOT represented here;
// formToConfig always passes the existing array through unchanged.
const agentsSchema = z.object({
  default_agent_name: z.string().optional(),
});

const formSchema = z.object({
  metadata: metadataSchema,
  llm: llmSchema,
  security: securitySchema,
  agents: agentsSchema,
});

type FormValues = z.infer<typeof formSchema>;

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
      // mode/endpoint/model_list deliberately excluded -- base-only, read
      // directly from `draft.llm.litellm` by ReadOnlyLiteLLMPanel instead.
      litellm: config.llm.litellm
        ? {
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
    },
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
      // A blank/untouched textarea or empty field-array round-trips back
      // through configToForm/formToConfig on every store sync (including the
      // programmatic sync that fires on mount). If we always wrote a
      // concrete `null`/`[]` here, an untouched config would come back
      // looking different (by JSON.stringify, which the store's dirty check
      // uses) from the `original` it started as -- a false "Unsaved
      // changes". Falling back to the pre-existing value when the form
      // field is empty keeps an untouched config byte-for-byte equal to
      // `original`, while a real edit (form field genuinely populated)
      // still overrides it and marks the draft dirty as expected.
      system_prompt: form.llm.system_prompt || existing.llm.system_prompt,
      // NOTE (Phase-1 field split): mode/endpoint/model_list are BASE-ONLY
      // -- mode selects the outbound LiteLLM proxy destination, endpoint IS
      // a destination, and model_list carries destinations (api_base) and
      // secrets (api_key). This form has no control that can change any of
      // the three; the `...existing.llm.litellm` spread always echoes them
      // back byte-for-byte, so a save can never repoint or introduce one.
      // Keyed off `existing.llm.litellm` (not `form.llm.litellm`) so the
      // spread always carries a complete, required `mode` -- the form
      // value alone can't prove that to the type checker.
      litellm: existing.llm.litellm
        ? {
            ...existing.llm.litellm,
            fallback_models: form.llm.litellm?.fallback_models
              ? form.llm.litellm.fallback_models
                  .split(",")
                  .map((s) => s.trim())
                  .filter(Boolean)
              : existing.llm.litellm.fallback_models,
            timeout: form.llm.litellm?.timeout,
            max_retries: form.llm.litellm?.max_retries,
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
    // Full agent (persona) CRUD now lives on the dedicated Agents page
    // (/agents), talking to the name-keyed overlay endpoints directly. This
    // wholesale visual-editor save must never touch `agents.agents` --
    // pass it through unchanged so a Config-page save can't clobber edits
    // made on /agents (or vice versa become a second, divergent write path
    // for the exact same array).
    agents: {
      ...existing.agents,
      default: form.agents.default_agent_name || existing.agents?.default,
    },
  };
}

// --- Read-only base-only panels ---
//
// Phase-1 field split: mode/endpoint/model_list (LiteLLM router config) and
// the full agent-definitions array are BASE-ONLY or have moved to a
// dedicated overlay-backed page respectively. Both render read-only here,
// with a "Managed in Git" affordance routing to Config > Promote -- this
// visual editor structurally cannot submit a change to either.

function ReadOnlyLiteLLMPanel({ litellm }: { litellm: ForgeConfig["llm"]["litellm"] }) {
  if (!litellm) return null;
  const modelList = litellm.model_list ?? [];

  return (
    <div className="sm:col-span-2 mt-4 space-y-3 rounded-lg border border-dashed border-muted-foreground/30 bg-muted/20 p-3">
      <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
        <Lock className="h-3.5 w-3.5" />
        Managed in Git -- edit via Promote/PR
      </div>
      <dl className="grid grid-cols-[auto,1fr] gap-x-3 gap-y-1 text-xs">
        <dt className="text-muted-foreground">Mode</dt>
        <dd className="font-mono">{litellm.mode}</dd>
        {litellm.endpoint && (
          <>
            <dt className="text-muted-foreground">Endpoint</dt>
            <dd className="font-mono break-all">{litellm.endpoint}</dd>
          </>
        )}
      </dl>
      <div>
        <p className="text-xs font-medium text-muted-foreground mb-1">
          Model List ({modelList.length})
        </p>
        {modelList.length === 0 ? (
          <p className="text-xs text-muted-foreground">No additional models configured.</p>
        ) : (
          <ul className="space-y-1">
            {modelList.map((entry, index) => {
              const modelName =
                typeof entry.model_name === "string" ? entry.model_name : `entry ${index + 1}`;
              const litellmParams = (entry.litellm_params ?? {}) as Record<string, unknown>;
              const model = typeof litellmParams.model === "string" ? litellmParams.model : "";
              return (
                <li key={`${modelName}-${index}`} className="font-mono text-xs">
                  {modelName}
                  {model ? ` -> ${model}` : ""}
                </li>
              );
            })}
          </ul>
        )}
      </div>
      <HelpText>
        Destinations (endpoint/api_base) and credentials (api_key) route requests and carry
        secrets, so LiteLLM routing is Git-reviewed rather than overlay-editable. Promote a
        change to add or repoint a model route.
      </HelpText>
      <Link
        to="/config?tab=promote"
        className="inline-flex items-center gap-1 text-xs font-medium text-primary underline-offset-4 hover:underline"
      >
        Review &amp; promote
        <ExternalLink className="h-3 w-3" />
      </Link>
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

  // `updateDraft` (below) creates a brand-new `draft` object on every form
  // change, including changes this component itself just wrote. Without
  // this flag, that store write would re-trigger the "reset form when draft
  // changes externally" effect below, which would call `reset()`, which
  // re-fires `watch()`, which calls `updateDraft` again -- an infinite
  // reset/watch feedback loop. Set the flag immediately before the
  // self-inflicted store write and consume it (without resetting) in the
  // effect that reacts to `draft` changes, so only genuinely external draft
  // changes (initial load, the YAML editor, undo/reset) trigger a form
  // reset.
  const isSelfInflictedUpdate = useRef(false);

  // Reset form when draft changes externally (e.g., from YAML editor)
  useEffect(() => {
    if (isSelfInflictedUpdate.current) {
      isSelfInflictedUpdate.current = false;
      return;
    }
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
        isSelfInflictedUpdate.current = true;
        updateDraft(formToConfig(parsed.data, draft));
      }
    });
    return () => subscription.unsubscribe();
  }, [watch, draft, updateDraft]);

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
  const agentDefsCount = draft.agents?.agents?.length ?? 0;

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

                <ReadOnlyLiteLLMPanel litellm={draft.llm.litellm} />
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
              Named agent personas with different system prompts, model selections, and tool
              access are managed on the dedicated{" "}
              <Link
                to="/agents"
                className="inline-flex items-center gap-1 text-primary underline-offset-4 hover:underline"
              >
                Agents page
                <ExternalLink className="h-3 w-3" />
              </Link>
              {" "}with full create/edit/delete via the overlay -- an agent carries no
              endpoint or credential of its own, so it is entirely runtime-editable there.
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
                  of the names defined on the Agents page.
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

              <div className="sm:col-span-2">
                <Badge variant="secondary">
                  {agentDefsCount} agent{agentDefsCount !== 1 ? "s" : ""} defined
                </Badge>
              </div>
            </div>
          </AccordionContent>
        </AccordionItem>
      </Accordion>
    </div>
  );
}
