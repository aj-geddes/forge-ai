import { useEffect } from "react";
import { useForm, Controller } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Link } from "react-router-dom";
import { ExternalLink } from "lucide-react";
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
import type { ForgeConfig, LiteLLMMode, TrustPolicy } from "@/types/config";

// --- Zod Schema ---

const litellmSchema = z.object({
  mode: z.enum(["embedded", "sidecar", "external"]),
  endpoint: z.string().optional(),
  config_path: z.string().optional(),
  port: z.coerce.number().int().positive().optional(),
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
  cors_origins: z.string().optional(),
});

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

function configToForm(config: ForgeConfig): FormValues {
  return {
    metadata: {
      name: config.metadata.name,
      version: config.metadata.version,
      description: config.metadata.description ?? "",
      environment: (config.metadata.labels?.["environment"] as
        | "development"
        | "staging"
        | "production"
        | undefined) ?? undefined,
    },
    llm: {
      model: config.llm.model,
      temperature: config.llm.temperature,
      max_tokens: config.llm.max_tokens,
      system_prompt: config.llm.litellm ? undefined : undefined,
      litellm: config.llm.litellm
        ? {
            mode: config.llm.litellm.mode,
            endpoint: config.llm.litellm.endpoint,
            config_path: config.llm.litellm.config_path,
            port: config.llm.litellm.port,
          }
        : undefined,
    },
    security: {
      agentweave_enabled: config.security?.agentweave?.enabled ?? false,
      trust_domain: config.security?.agentweave?.trust_store ?? "",
      trust_policy: config.security?.trust_policy ?? undefined,
      rate_limit_rpm: config.security?.rate_limit?.requests_per_minute,
      cors_origins: config.security?.cors_origins?.join(", ") ?? "",
    },
    agents: {
      default_agent_name: config.agents?.default_agent?.name ?? "",
    },
  };
}

function formToConfig(
  form: FormValues,
  existing: ForgeConfig,
): ForgeConfig {
  const labels: Record<string, string> = {
    ...(existing.metadata.labels ?? {}),
  };
  if (form.metadata.environment) {
    labels["environment"] = form.metadata.environment;
  } else {
    delete labels["environment"];
  }

  return {
    ...existing,
    metadata: {
      name: form.metadata.name,
      version: form.metadata.version,
      description: form.metadata.description || undefined,
      labels: Object.keys(labels).length > 0 ? labels : undefined,
    },
    llm: {
      ...existing.llm,
      model: form.llm.model,
      temperature: form.llm.temperature,
      max_tokens: form.llm.max_tokens,
      litellm: form.llm.litellm
        ? {
            mode: form.llm.litellm.mode as LiteLLMMode,
            endpoint: form.llm.litellm.endpoint || undefined,
            config_path: form.llm.litellm.config_path || undefined,
            port: form.llm.litellm.port,
          }
        : existing.llm.litellm,
    },
    security: {
      ...existing.security,
      agentweave: {
        ...(existing.security?.agentweave ?? { enabled: false }),
        enabled: form.security.agentweave_enabled,
        trust_store: form.security.trust_domain || undefined,
      },
      trust_policy: (form.security.trust_policy as TrustPolicy) || undefined,
      rate_limit: form.security.rate_limit_rpm
        ? {
            ...existing.security?.rate_limit,
            requests_per_minute: form.security.rate_limit_rpm,
          }
        : existing.security?.rate_limit,
      cors_origins: form.security.cors_origins
        ? form.security.cors_origins
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean)
        : existing.security?.cors_origins,
    },
    agents: {
      ...existing.agents,
      default_agent: form.agents.default_agent_name
        ? {
            ...(existing.agents?.default_agent ?? {
              name: form.agents.default_agent_name,
            }),
            name: form.agents.default_agent_name,
          }
        : existing.agents?.default_agent,
    },
  };
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

  if (!draft) {
    return (
      <div className="flex items-center justify-center py-12 text-muted-foreground">
        No configuration loaded
      </div>
    );
  }

  const openapiCount = draft.tools.openapi?.length ?? 0;
  const manualCount = draft.tools.manual?.length ?? 0;
  const workflowCount = draft.tools.workflows?.length ?? 0;
  const peersCount = draft.peers?.length ?? 0;

  return (
    <div className="space-y-2">
      <Accordion>
        {/* Metadata Section */}
        <AccordionItem open>
          <AccordionTrigger>Metadata</AccordionTrigger>
          <AccordionContent>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="metadata.name">Name</Label>
                <Input
                  id="metadata.name"
                  placeholder="my-forge-agent"
                  {...register("metadata.name")}
                />
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
                  placeholder="Describe your agent..."
                  rows={2}
                  {...register("metadata.description")}
                />
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
              </div>
            </div>
          </AccordionContent>
        </AccordionItem>

        {/* LLM Section */}
        <AccordionItem>
          <AccordionTrigger>LLM Configuration</AccordionTrigger>
          <AccordionContent>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="llm.model">Default Model</Label>
                <Input
                  id="llm.model"
                  placeholder="gpt-4o"
                  {...register("llm.model")}
                />
                {errors.llm?.model && (
                  <p className="text-xs text-destructive">
                    {errors.llm.model.message}
                  </p>
                )}
              </div>

              <div className="space-y-2">
                <Label htmlFor="llm.temperature">
                  Temperature{" "}
                  <span className="text-muted-foreground font-normal">
                    (0-2)
                  </span>
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
              </div>

              <div className="space-y-2 sm:col-span-2">
                <Label htmlFor="llm.system_prompt">System Prompt</Label>
                <Textarea
                  id="llm.system_prompt"
                  placeholder="You are a helpful assistant..."
                  rows={4}
                  {...register("llm.system_prompt")}
                />
              </div>

              {/* LiteLLM subsection */}
              <div className="sm:col-span-2">
                <h4 className="mb-3 text-sm font-medium text-muted-foreground">
                  LiteLLM Router
                </h4>
                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="space-y-2">
                    <Label htmlFor="llm.litellm.mode">Mode</Label>
                    <Select
                      id="llm.litellm.mode"
                      {...register("llm.litellm.mode")}
                    >
                      <option value="embedded">Embedded</option>
                      <option value="sidecar">Sidecar</option>
                      <option value="external">External</option>
                    </Select>
                  </div>

                  {(litellmMode === "sidecar" ||
                    litellmMode === "external") && (
                    <div className="space-y-2">
                      <Label htmlFor="llm.litellm.endpoint">Endpoint</Label>
                      <Input
                        id="llm.litellm.endpoint"
                        placeholder="http://litellm:4000"
                        {...register("llm.litellm.endpoint")}
                      />
                    </div>
                  )}
                </div>
              </div>
            </div>
          </AccordionContent>
        </AccordionItem>

        {/* Tools Section */}
        <AccordionItem>
          <AccordionTrigger>Tools</AccordionTrigger>
          <AccordionContent>
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
                Use the{" "}
                <Link
                  to="/tools"
                  className="inline-flex items-center gap-1 text-primary underline-offset-4 hover:underline"
                >
                  Tool Workshop
                  <ExternalLink className="h-3 w-3" />
                </Link>{" "}
                for detailed tool editing.
              </p>
            </div>
          </AccordionContent>
        </AccordionItem>

        {/* Security Section */}
        <AccordionItem>
          <AccordionTrigger>Security</AccordionTrigger>
          <AccordionContent>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="flex items-center gap-3 sm:col-span-2">
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
                <Label htmlFor="security.agentweave_enabled">
                  Enable AgentWeave
                </Label>
              </div>

              <div className="space-y-2">
                <Label htmlFor="security.trust_domain">Trust Domain</Label>
                <Input
                  id="security.trust_domain"
                  placeholder="/path/to/trust-store"
                  {...register("security.trust_domain")}
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="security.trust_policy">Trust Policy</Label>
                <Select
                  id="security.trust_policy"
                  {...register("security.trust_policy")}
                >
                  <option value="">Select policy...</option>
                  <option value="strict">Strict</option>
                  <option value="permissive">Permissive</option>
                </Select>
              </div>

              <div className="space-y-2">
                <Label htmlFor="security.rate_limit_rpm">
                  Rate Limit (RPM)
                </Label>
                <Input
                  id="security.rate_limit_rpm"
                  type="number"
                  min={1}
                  placeholder="60"
                  {...register("security.rate_limit_rpm")}
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="security.cors_origins">
                  Allowed Origins{" "}
                  <span className="text-muted-foreground font-normal">
                    (comma-separated)
                  </span>
                </Label>
                <Input
                  id="security.cors_origins"
                  placeholder="http://localhost:3000, https://app.example.com"
                  {...register("security.cors_origins")}
                />
              </div>
            </div>
          </AccordionContent>
        </AccordionItem>

        {/* Agents Section */}
        <AccordionItem>
          <AccordionTrigger>Agents</AccordionTrigger>
          <AccordionContent>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="agents.default_agent_name">
                  Default Agent Name
                </Label>
                <Input
                  id="agents.default_agent_name"
                  placeholder="default"
                  {...register("agents.default_agent_name")}
                />
              </div>

              <div className="flex items-end">
                <div className="space-y-1">
                  <Badge variant="secondary">
                    {peersCount} peer{peersCount !== 1 ? "s" : ""} configured
                  </Badge>
                  <p className="text-sm text-muted-foreground">
                    <Link
                      to="/peers"
                      className="inline-flex items-center gap-1 text-primary underline-offset-4 hover:underline"
                    >
                      Manage peers
                      <ExternalLink className="h-3 w-3" />
                    </Link>
                  </p>
                </div>
              </div>
            </div>
          </AccordionContent>
        </AccordionItem>
      </Accordion>
    </div>
  );
}
