import { useCallback, useEffect, useId, useMemo, useState } from "react";
import {
  Bot,
  Plus,
  Loader2,
  Info,
  AlertCircle,
  Trash2,
  Pencil,
  Sparkles,
} from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import {
  useAgents,
  useConfigEnvelope,
  useTools,
  useCreateAgent,
  useUpdateAgent,
  useDeleteAgent,
} from "@/api/hooks";
import { deriveMutationUiState, isSuccessState } from "@/lib/mutationState";
import { useToast } from "@/components/ui/toast";
import type { AgentDef, AgentMode } from "@/types/config";

const DISABLED_REASON = "Config editing is disabled (GitOps-managed) — edit via git.";

function HelpText({ children }: { children: React.ReactNode }) {
  return (
    <p className="flex items-start gap-1.5 text-xs text-muted-foreground mt-1 leading-relaxed">
      <Info className="h-3 w-3 mt-0.5 shrink-0 opacity-60" />
      <span>{children}</span>
    </p>
  );
}

/**
 * Every field an AgentDef carries is a scalar or a SELECTION (model alias,
 * tool-name filter) -- never a URL or a secret (see the Phase-1 field-split
 * design note). That is what makes agents the one entity with FULL runtime
 * create/edit/delete via the overlay, unlike tools/openapi sources/peers/
 * model_list entries, which all carry a destination and/or secret and stay
 * base-only (Git-managed).
 */
interface AgentFormState {
  name: string;
  description: string;
  system_prompt: string;
  model: string;
  tools: string[];
  max_turns: string;
  mode: AgentMode;
}

const EMPTY_FORM: AgentFormState = {
  name: "",
  description: "",
  system_prompt: "",
  model: "",
  tools: [],
  max_turns: "",
  mode: "passive",
};

function agentToFormState(agent: AgentDef): AgentFormState {
  return {
    name: agent.name,
    description: agent.description ?? "",
    system_prompt: agent.system_prompt ?? "",
    model: agent.model ?? "",
    tools: agent.tools ?? [],
    max_turns: agent.max_turns !== undefined ? String(agent.max_turns) : "",
    mode: agent.mode ?? "passive",
  };
}

/** Builds the PATCH/POST payload -- only AgentDef fields, nothing else. */
function formStateToPayload(form: AgentFormState): Record<string, unknown> {
  return {
    name: form.name.trim(),
    description: form.description.trim() || undefined,
    system_prompt: form.system_prompt.trim() || undefined,
    model: form.model || undefined,
    tools: form.tools,
    max_turns: form.max_turns.trim() ? Number(form.max_turns) : undefined,
    mode: form.mode,
  };
}

/**
 * Model choices are a SELECTION over BASE-defined destinations only --
 * every entry here is either the current default model or a `model_name`
 * alias already present in `llm.litellm.model_list`. The dropdown can never
 * carry a `base_url`/`api_key`, so choosing (or typing) a model string here
 * cannot repoint or introduce a destination (see LLMConfig.model_list is
 * BASE-ONLY in the design note; this only ever *references* it by name).
 */
function useModelOptions(): string[] {
  const { data: envelope } = useConfigEnvelope();
  return useMemo(() => {
    const config = envelope?.config;
    if (!config) return [];
    const names = new Set<string>();
    if (config.llm.default_model) names.add(config.llm.default_model);
    for (const entry of config.llm.litellm?.model_list ?? []) {
      const modelName = (entry as Record<string, unknown>).model_name;
      if (typeof modelName === "string" && modelName) names.add(modelName);
    }
    return Array.from(names);
  }, [envelope]);
}

function ToolMultiSelect({
  selected,
  onChange,
}: {
  selected: string[];
  onChange: (tools: string[]) => void;
}) {
  const { data: tools } = useTools();

  const toggle = (name: string) => {
    onChange(
      selected.includes(name) ? selected.filter((t) => t !== name) : [...selected, name],
    );
  };

  if (!tools || tools.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        No tools registered yet -- add one from the Tool Workshop first.
      </p>
    );
  }

  return (
    <ScrollArea className="max-h-40 rounded-md border p-2">
      <div className="space-y-1">
        {tools.map((tool) => (
          <label
            key={tool.name}
            className="flex cursor-pointer items-center gap-2 rounded px-1.5 py-1 text-sm hover:bg-muted/50"
          >
            <input
              type="checkbox"
              className="h-3.5 w-3.5 rounded border-input"
              checked={selected.includes(tool.name)}
              onChange={() => toggle(tool.name)}
            />
            <span className="font-mono text-xs">{tool.name}</span>
          </label>
        ))}
      </div>
    </ScrollArea>
  );
}

function AgentFormFields({
  form,
  setForm,
  nameEditable,
  modelOptions,
}: {
  form: AgentFormState;
  setForm: (updater: (prev: AgentFormState) => AgentFormState) => void;
  nameEditable: boolean;
  modelOptions: string[];
}) {
  const isCustomModel = form.model !== "" && !modelOptions.includes(form.model);
  // Both the Create and Edit dialogs mount their <dialog> element
  // permanently (only the native `open` state toggles visibility -- see
  // components/ui/dialog.tsx), so AgentFormFields is instantiated twice at
  // once. Static ids would collide (a <label for> would then bind to
  // whichever dialog rendered first), so every field id is namespaced with
  // a per-instance useId().
  const uid = useId();
  const nameId = `${uid}-agent-name`;
  const descriptionId = `${uid}-agent-description`;
  const systemPromptId = `${uid}-agent-system-prompt`;
  const modelId = `${uid}-agent-model`;
  const modeId = `${uid}-agent-mode`;
  const maxTurnsId = `${uid}-agent-max-turns`;

  return (
    <div className="space-y-4 py-2">
      <div className="space-y-2">
        <Label htmlFor={nameId}>Name</Label>
        <Input
          id={nameId}
          placeholder="researcher"
          value={form.name}
          disabled={!nameEditable}
          onChange={(e) => setForm((prev) => ({ ...prev, name: e.target.value }))}
        />
        {nameEditable ? (
          <HelpText>
            A unique persona name. Callers select this agent by name when invoking Forge.
          </HelpText>
        ) : (
          <HelpText>Names are immutable once created -- delete and re-create to rename.</HelpText>
        )}
      </div>

      <div className="space-y-2">
        <Label htmlFor={descriptionId}>Description</Label>
        <Input
          id={descriptionId}
          placeholder="What this persona is for"
          value={form.description}
          onChange={(e) => setForm((prev) => ({ ...prev, description: e.target.value }))}
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor={systemPromptId}>System Prompt</Label>
        <Textarea
          id={systemPromptId}
          placeholder="You are a specialist in..."
          rows={4}
          value={form.system_prompt}
          onChange={(e) => setForm((prev) => ({ ...prev, system_prompt: e.target.value }))}
        />
        <HelpText>
          Instructions shaping this persona's behavior. Content only -- always sent to the
          Git-defined LLM endpoint, never a destination itself.
        </HelpText>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor={modelId}>Model</Label>
          <Select
            id={modelId}
            value={form.model}
            onChange={(e) => setForm((prev) => ({ ...prev, model: e.target.value }))}
          >
            <option value="">(use default model)</option>
            {modelOptions.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
            {isCustomModel && <option value={form.model}>{form.model} (custom)</option>}
          </Select>
          <HelpText>
            Selects among models already defined in your LLM configuration&apos;s model list --
            it cannot introduce a new endpoint or credential.
          </HelpText>
        </div>

        <div className="space-y-2">
          <Label htmlFor={modeId}>Mode</Label>
          <Select
            id={modeId}
            value={form.mode}
            onChange={(e) => setForm((prev) => ({ ...prev, mode: e.target.value as AgentMode }))}
          >
            <option value="passive">Passive</option>
            <option value="active">Active</option>
          </Select>
          <HelpText>Governance mode (ADR-0005) -- declaration only.</HelpText>
        </div>

        <div className="space-y-2 sm:col-span-2">
          <Label htmlFor={maxTurnsId}>Max Turns</Label>
          <Input
            id={maxTurnsId}
            type="number"
            min={1}
            placeholder="10"
            value={form.max_turns}
            onChange={(e) => setForm((prev) => ({ ...prev, max_turns: e.target.value }))}
          />
        </div>
      </div>

      <div className="space-y-2">
        <Label>Tools</Label>
        <ToolMultiSelect
          selected={form.tools}
          onChange={(tools) => setForm((prev) => ({ ...prev, tools }))}
        />
        <HelpText>
          Selects a subset of already-configured tools -- unchecked means full access to every
          configured tool. Only existing tool names can be chosen here; new tools are created
          from the Tool Workshop.
        </HelpText>
      </div>
    </div>
  );
}

function CreateAgentDialog({ disabled, disabledReason }: { disabled?: boolean; disabledReason?: string }) {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<AgentFormState>(EMPTY_FORM);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const createAgent = useCreateAgent();
  const { toast } = useToast();
  const { data: envelope } = useConfigEnvelope();
  const modelOptions = useModelOptions();

  const canSubmit = form.name.trim().length > 0;

  const handleSubmit = useCallback(() => {
    if (!canSubmit) return;
    setSubmitError(null);

    createAgent.mutate(
      { agent: formStateToPayload(form), rev: envelope?.rev },
      {
        onSuccess: (result) => {
          const uiState = deriveMutationUiState(result, undefined);
          if (isSuccessState(uiState)) {
            if (uiState.kind === "success-drift") {
              toast({ title: "Saved — not yet in Git", description: uiState.message });
            } else {
              toast({ title: "Agent created", description: `"${form.name}" saved to config.` });
            }
            setOpen(false);
            setForm(EMPTY_FORM);
          } else {
            // persisted: false -- never close the dialog or claim success.
            setSubmitError(uiState.message);
          }
        },
        onError: (err) => {
          setSubmitError(deriveMutationUiState(undefined, err).message);
        },
      },
    );
  }, [canSubmit, createAgent, form, envelope?.rev, toast]);

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) {
          setForm(EMPTY_FORM);
          setSubmitError(null);
        }
      }}
    >
      {disabled ? (
        <span title={disabledReason} className="inline-block">
          <button
            type="button"
            disabled
            className="inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground opacity-50 cursor-not-allowed"
          >
            <Plus className="h-4 w-4" />
            New Agent
          </button>
        </span>
      ) : (
        <DialogTrigger className="inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90">
          <Plus className="h-4 w-4" />
          New Agent
        </DialogTrigger>
      )}
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Create Agent</DialogTitle>
          <DialogDescription>
            Define a new persona: a system prompt, model selection, and tool scope. Agents carry
            no endpoint or credential of their own, so this is fully runtime-editable.
          </DialogDescription>
        </DialogHeader>
        {submitError && (
          <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
            <AlertCircle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
            <span>{submitError}</span>
          </div>
        )}
        <AgentFormFields form={form} setForm={setForm} nameEditable modelOptions={modelOptions} />
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={!canSubmit || createAgent.isPending}>
            {createAgent.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            Create Agent
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function EditAgentDialog({
  agent,
  rev,
  onOpenChange,
}: {
  agent: AgentDef | null;
  rev: number | undefined;
  onOpenChange: (open: boolean) => void;
}) {
  const [form, setForm] = useState<AgentFormState>(EMPTY_FORM);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const updateAgent = useUpdateAgent();
  const { toast } = useToast();
  const modelOptions = useModelOptions();

  useEffect(() => {
    if (agent) {
      setForm(agentToFormState(agent));
      setSubmitError(null);
    }
  }, [agent]);

  const handleSubmit = () => {
    if (!agent || rev === undefined) return;
    setSubmitError(null);

    updateAgent.mutate(
      { name: agent.name, agent: formStateToPayload(form), rev },
      {
        onSuccess: (result) => {
          const uiState = deriveMutationUiState(result, undefined);
          if (isSuccessState(uiState)) {
            if (uiState.kind === "success-drift") {
              toast({ title: "Saved — not yet in Git", description: uiState.message });
            }
            onOpenChange(false);
          } else {
            setSubmitError(uiState.message);
          }
        },
        onError: (err) => {
          setSubmitError(deriveMutationUiState(undefined, err).message);
        },
      },
    );
  };

  return (
    <Dialog open={agent !== null} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Edit Agent</DialogTitle>
          <DialogDescription>
            Update {agent ? <strong>{agent.name}</strong> : "this agent"}&apos;s persona, model,
            and tool scope.
          </DialogDescription>
        </DialogHeader>
        {submitError && (
          <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
            <AlertCircle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
            <span>{submitError}</span>
          </div>
        )}
        <AgentFormFields
          form={form}
          setForm={setForm}
          nameEditable={false}
          modelOptions={modelOptions}
        />
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={updateAgent.isPending}>
            {updateAgent.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function AgentCard({
  agent,
  onEdit,
  onDelete,
  actionsDisabled,
  actionsDisabledReason,
}: {
  agent: AgentDef;
  onEdit: () => void;
  onDelete: () => void;
  actionsDisabled: boolean;
  actionsDisabledReason?: string;
}) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-2">
            <Bot className="h-4 w-4 text-primary" />
            <CardTitle className="text-lg">{agent.name}</CardTitle>
          </div>
          <div className="flex items-center gap-2">
            {agent.mode && (
              <Badge variant="outline" className="capitalize">
                {agent.mode}
              </Badge>
            )}
            <span
              title={actionsDisabled ? actionsDisabledReason : undefined}
              className="inline-block"
            >
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8 shrink-0"
                disabled={actionsDisabled}
                onClick={onEdit}
                aria-label="Edit agent"
                title="Edit agent"
              >
                <Pencil className="h-3.5 w-3.5" />
              </Button>
            </span>
            <span
              title={actionsDisabled ? actionsDisabledReason : undefined}
              className="inline-block"
            >
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8 shrink-0"
                disabled={actionsDisabled}
                onClick={onDelete}
                aria-label="Delete agent"
                title="Delete agent"
              >
                <Trash2 className="h-3.5 w-3.5 text-destructive" />
              </Button>
            </span>
          </div>
        </div>
        {agent.description && (
          <CardDescription className="pt-1">{agent.description}</CardDescription>
        )}
      </CardHeader>
      <CardContent className="space-y-2">
        <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
          <span className="font-medium text-foreground/80">Model:</span>
          <span className="font-mono">{agent.model || "(default)"}</span>
        </div>
        {agent.tools && agent.tools.length > 0 ? (
          <div className="flex flex-wrap gap-1.5">
            {agent.tools.map((tool) => (
              <Badge key={tool} variant="secondary" className="text-xs font-mono">
                {tool}
              </Badge>
            ))}
          </div>
        ) : (
          <p className="text-xs text-muted-foreground">Full access to all configured tools.</p>
        )}
        {agent.max_turns !== undefined && (
          <p className="text-xs text-muted-foreground">Max turns: {agent.max_turns}</p>
        )}
      </CardContent>
    </Card>
  );
}

export function AgentsPage() {
  const { data: agents, isLoading, error } = useAgents();
  const { data: envelope } = useConfigEnvelope();
  const deleteAgent = useDeleteAgent();
  const { toast } = useToast();
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  const [editingAgent, setEditingAgent] = useState<AgentDef | null>(null);

  const editingDisabled = envelope?.mutation_policy === "disabled";

  const handleConfirmDelete = useCallback(() => {
    if (pendingDelete === null || envelope?.rev === undefined) return;
    deleteAgent.mutate(
      { name: pendingDelete, rev: envelope.rev },
      {
        onSuccess: (result) => {
          const state = deriveMutationUiState(result, undefined);
          if (state.kind === "success-drift") {
            toast({ title: "Saved — not yet in Git", description: state.message });
          }
          setPendingDelete(null);
        },
      },
    );
  }, [pendingDelete, envelope?.rev, deleteAgent, toast]);

  if (isLoading) {
    return (
      <div className="flex min-h-[400px] items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (error) {
    return (
      <Card>
        <CardContent className="py-8">
          <p className="text-center text-sm text-destructive">
            Failed to load agents: {error.message}
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Bot className="h-8 w-8 text-muted-foreground" />
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Agents</h1>
            <p className="text-sm text-muted-foreground max-w-xl">
              Named personas your agent can be invoked as -- each with its own prompt, model
              selection, and tool scope. Fully editable at runtime: an agent carries no endpoint
              or credential of its own.
            </p>
          </div>
        </div>
        <CreateAgentDialog
          disabled={editingDisabled}
          disabledReason={editingDisabled ? DISABLED_REASON : undefined}
        />
      </div>

      {!agents || agents.length === 0 ? (
        <Card>
          <CardContent className="py-12">
            <div className="flex flex-col items-center gap-4 text-center">
              <Sparkles className="h-12 w-12 text-muted-foreground/50" />
              <div className="max-w-md">
                <p className="text-lg font-medium text-muted-foreground">No agents defined yet</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  Agents are named personas -- a system prompt, model choice, and tool scope
                  callers select by name. Create your first one to get started.
                </p>
              </div>
            </div>
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {agents.map((agent) => (
            <AgentCard
              key={agent.name}
              agent={agent}
              onEdit={() => setEditingAgent(agent)}
              onDelete={() => setPendingDelete(agent.name)}
              actionsDisabled={editingDisabled}
              actionsDisabledReason={editingDisabled ? DISABLED_REASON : undefined}
            />
          ))}
        </div>
      )}

      <EditAgentDialog
        agent={editingAgent}
        rev={envelope?.rev}
        onOpenChange={(open) => !open && setEditingAgent(null)}
      />

      <ConfirmDialog
        open={pendingDelete !== null}
        onOpenChange={(open) => !open && setPendingDelete(null)}
        title="Delete agent?"
        description={`This removes the "${pendingDelete ?? ""}" agent persona from the configuration. This cannot be undone from the UI.`}
        confirmLabel="Delete Agent"
        onConfirm={handleConfirmDelete}
        isPending={deleteAgent.isPending}
        errorMessage={
          deleteAgent.isError
            ? deleteAgent.error instanceof Error
              ? deleteAgent.error.message
              : "Failed to delete agent"
            : null
        }
      />
    </div>
  );
}
