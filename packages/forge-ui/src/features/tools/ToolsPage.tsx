import { useState, useMemo, useEffect, useCallback } from "react";
import { Link } from "react-router-dom";
import {
  Wrench,
  Plus,
  Search,
  Globe,
  FileText,
  GitBranch,
  Loader2,
  Package,
  Info,
  Pencil,
  Trash2,
  Lock,
  ExternalLink,
} from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Select } from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { useTools, useConfigEnvelope, useUpdateTool, useDeleteTool } from "@/api/hooks";
import { deriveMutationUiState, isSuccessState } from "@/lib/mutationState";
import { useToast } from "@/components/ui/toast";
import { useToolStore } from "@/stores/toolStore";
import { cn } from "@/lib/utils";
import { OpenAPIWizard } from "./OpenAPIWizard";
import { ManualToolWizard } from "./ManualToolWizard";
import { WorkflowComposer } from "./WorkflowComposer";
import type { WizardType } from "@/stores/toolStore";
import type { ManualTool, ParamType, ParameterDef } from "@/types/config";

const DISABLED_REASON = "Config editing is disabled (GitOps-managed) — edit via git.";
const PROMOTE_REASON =
  "Base-only field -- carries a destination or credential, so it can only be changed in Git (Config > Promote).";

function ToolCardSkeleton() {
  return (
    <div className="rounded-lg border p-4 animate-pulse">
      <div className="flex items-start gap-3">
        <div className="h-10 w-10 rounded-md bg-muted" />
        <div className="flex-1 space-y-2">
          <div className="h-4 w-1/3 rounded bg-muted" />
          <div className="h-3 w-2/3 rounded bg-muted" />
        </div>
        <div className="h-5 w-16 rounded-full bg-muted" />
      </div>
    </div>
  );
}

function SourceBadge({ source }: { source: string | undefined }) {
  const label = source ?? "unknown";

  const variant = (() => {
    switch (label.toLowerCase()) {
      case "openapi":
        return "default" as const;
      case "manual":
        return "secondary" as const;
      case "workflow":
        return "outline" as const;
      default:
        return "secondary" as const;
    }
  })();

  const Icon = (() => {
    switch (label.toLowerCase()) {
      case "openapi":
        return Globe;
      case "manual":
        return FileText;
      case "workflow":
        return GitBranch;
      default:
        return Package;
    }
  })();

  return (
    <Badge variant={variant} className="gap-1">
      <Icon className="h-3 w-3" />
      {label}
    </Badge>
  );
}

function HelpText({ children }: { children: React.ReactNode }) {
  return (
    <p className="flex items-start gap-1.5 text-xs text-muted-foreground mt-1 leading-relaxed">
      <Info className="h-3 w-3 mt-0.5 shrink-0 opacity-60" />
      <span>{children}</span>
    </p>
  );
}

interface AddToolDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSelect: (type: WizardType) => void;
}

function AddToolDialog({ open, onOpenChange, onSelect }: AddToolDialogProps) {
  const options = [
    {
      type: "openapi" as const,
      icon: Globe,
      title: "OpenAPI Source",
      description:
        "Import tools from an OpenAPI specification URL. Best for REST APIs that already publish a spec -- the wizard auto-discovers every endpoint and lets you pick which ones to expose.",
    },
    {
      type: "manual" as const,
      icon: FileText,
      title: "Manual Tool",
      description:
        "Define a custom tool by hand with its endpoint, parameters, and response mapping. Use this when you need to integrate an API that does not have an OpenAPI spec.",
    },
    {
      type: "workflow" as const,
      icon: GitBranch,
      title: "Workflow",
      description:
        "Compose a multi-step workflow by chaining existing tools together. Ideal for pipelines where one tool's output feeds into the next (e.g., search then summarize).",
    },
  ];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add Tool</DialogTitle>
          <DialogDescription>
            Choose how you want to add a new tool. Each approach suits different integration scenarios.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3 mt-2">
          {options.map((opt) => (
            <button
              key={opt.type}
              type="button"
              onClick={() => {
                onOpenChange(false);
                onSelect(opt.type);
              }}
              className="flex items-start gap-3 rounded-lg border p-4 text-left transition-colors hover:bg-muted/50"
            >
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-primary/10">
                <opt.icon className="h-5 w-5 text-primary" />
              </div>
              <div>
                <p className="text-sm font-medium">{opt.title}</p>
                <p className="text-xs text-muted-foreground mt-0.5">
                  {opt.description}
                </p>
              </div>
            </button>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}

function RefreshingIndicator({ isLoading, hasTools }: { isLoading: boolean; hasTools: boolean }) {
  if (!isLoading || !hasTools) return null;
  return (
    <div className="flex items-center justify-center py-2">
      <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
      <span className="ml-2 text-xs text-muted-foreground">Refreshing...</span>
    </div>
  );
}

/** Read-only summary of the fields Phase-1 classifies as base-only for a
 * manual tool: outbound destination (url/base_url/endpoint), the
 * request-construction contract (method/headers/body_template/timeout),
 * the secret binding (auth), and the approval security control. None of
 * these are ever sent by the runtime overlay editor below -- they can only
 * be changed by promoting a Git-reviewed change (see PROMOTE_REASON). */
function BaseOnlyToolPanel({ tool }: { tool: ManualTool }) {
  const resolvedUrl =
    tool.api.url || (tool.api.base_url ? `${tool.api.base_url}${tool.api.endpoint ?? ""}` : "");
  const headerNames = Object.keys(tool.api.headers ?? {});

  return (
    <div className="space-y-2 rounded-lg border border-dashed border-muted-foreground/30 bg-muted/20 p-3">
      <div
        className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground"
        title={PROMOTE_REASON}
      >
        <Lock className="h-3.5 w-3.5" />
        Managed in Git -- edit via Promote/PR
      </div>
      <dl className="grid grid-cols-[auto,1fr] gap-x-3 gap-y-1 text-xs">
        <dt className="text-muted-foreground">URL</dt>
        <dd className="font-mono break-all">{resolvedUrl || "(unset)"}</dd>
        <dt className="text-muted-foreground">Method</dt>
        <dd className="font-mono">{tool.api.method}</dd>
        {tool.api.auth && tool.api.auth.type !== "none" && (
          <>
            <dt className="text-muted-foreground">Auth</dt>
            <dd className="font-mono">{tool.api.auth.type}</dd>
          </>
        )}
        {headerNames.length > 0 && (
          <>
            <dt className="text-muted-foreground">Headers</dt>
            <dd className="font-mono">{headerNames.join(", ")}</dd>
          </>
        )}
        <dt className="text-muted-foreground">Timeout</dt>
        <dd className="font-mono">{tool.api.timeout ?? 30}s</dd>
        <dt className="text-muted-foreground">Approval</dt>
        <dd className="font-mono">{tool.requires_approval ? "required" : "not required"}</dd>
      </dl>
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

interface EditableParam {
  name: string;
  type: ParamType;
  description: string;
  required: boolean;
}

function ParametersEditor({
  parameters,
  onChange,
}: {
  parameters: EditableParam[];
  onChange: (params: EditableParam[]) => void;
}) {
  const update = (index: number, patch: Partial<EditableParam>) => {
    onChange(parameters.map((p, i) => (i === index ? { ...p, ...patch } : p)));
  };
  const remove = (index: number) => {
    onChange(parameters.filter((_, i) => i !== index));
  };
  const add = () => {
    onChange([...parameters, { name: "", type: "string", description: "", required: false }]);
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <p className="text-sm font-medium">Parameters</p>
        <Button type="button" variant="outline" size="sm" onClick={add}>
          <Plus className="h-4 w-4" />
          Add Parameter
        </Button>
      </div>
      {parameters.length === 0 ? (
        <div className="flex h-14 items-center justify-center rounded-lg border border-dashed">
          <p className="text-xs text-muted-foreground">No parameters defined yet.</p>
        </div>
      ) : (
        <ScrollArea className="max-h-[220px]">
          <div className="space-y-2">
            {parameters.map((param, index) => (
              <div key={index} className="rounded-lg border p-2 space-y-2">
                <div className="flex items-start gap-2">
                  <div className="flex-1 grid gap-2 sm:grid-cols-2">
                    <Input
                      aria-label={`Parameter ${index + 1} name`}
                      placeholder="param_name"
                      value={param.name}
                      onChange={(e) => update(index, { name: e.target.value })}
                      className="h-8 text-sm"
                    />
                    <Select
                      aria-label={`Parameter ${index + 1} type`}
                      value={param.type}
                      onChange={(e) => update(index, { type: e.target.value as ParamType })}
                      className="h-8 text-sm"
                    >
                      <option value="string">string</option>
                      <option value="integer">integer</option>
                      <option value="number">number</option>
                      <option value="boolean">boolean</option>
                      <option value="array">array</option>
                      <option value="object">object</option>
                    </Select>
                  </div>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="shrink-0"
                    onClick={() => remove(index)}
                  >
                    <Trash2 className="h-4 w-4 text-destructive" />
                  </Button>
                </div>
                <Input
                  aria-label={`Parameter ${index + 1} description`}
                  placeholder="Describe this parameter..."
                  value={param.description}
                  onChange={(e) => update(index, { description: e.target.value })}
                  className="h-8 text-sm"
                />
                <div className="flex items-center gap-2">
                  <Switch
                    checked={param.required}
                    onCheckedChange={(checked) => update(index, { required: checked })}
                  />
                  <span className="text-xs">Required</span>
                </div>
              </div>
            ))}
          </div>
        </ScrollArea>
      )}
    </div>
  );
}

interface EditableFieldMapping {
  key: string;
  value: string;
}

function ResponseMappingEditor({
  resultPath,
  onResultPathChange,
  fieldMap,
  onFieldMapChange,
}: {
  resultPath: string;
  onResultPathChange: (value: string) => void;
  fieldMap: EditableFieldMapping[];
  onFieldMapChange: (mappings: EditableFieldMapping[]) => void;
}) {
  const update = (index: number, patch: Partial<EditableFieldMapping>) => {
    onFieldMapChange(fieldMap.map((m, i) => (i === index ? { ...m, ...patch } : m)));
  };
  const remove = (index: number) => {
    onFieldMapChange(fieldMap.filter((_, i) => i !== index));
  };
  const add = () => {
    onFieldMapChange([...fieldMap, { key: "", value: "" }]);
  };

  return (
    <div className="space-y-2">
      <div className="space-y-1">
        <label className="text-sm font-medium" htmlFor="tool-result-path">
          Result Path (JSONPath)
        </label>
        <Input
          id="tool-result-path"
          placeholder="e.g. $.data.results"
          value={resultPath}
          onChange={(e) => onResultPathChange(e.target.value)}
        />
      </div>
      <div className="flex items-center justify-between">
        <p className="text-sm font-medium">Field Mapping</p>
        <Button type="button" variant="outline" size="sm" onClick={add}>
          <Plus className="h-4 w-4" />
          Add Mapping
        </Button>
      </div>
      {fieldMap.length === 0 ? (
        <div className="flex h-12 items-center justify-center rounded-lg border border-dashed">
          <p className="text-xs text-muted-foreground">No field mappings defined.</p>
        </div>
      ) : (
        <div className="space-y-2">
          {fieldMap.map((mapping, index) => (
            <div key={index} className="flex items-center gap-2">
              <Input
                aria-label={`Field mapping ${index + 1} response field`}
                placeholder="Response field"
                value={mapping.key}
                onChange={(e) => update(index, { key: e.target.value })}
                className="h-8 text-sm"
              />
              <span className="shrink-0 text-muted-foreground">&rarr;</span>
              <Input
                aria-label={`Field mapping ${index + 1} output name`}
                placeholder="Output name"
                value={mapping.value}
                onChange={(e) => update(index, { value: e.target.value })}
                className="h-8 text-sm"
              />
              <Button type="button" variant="ghost" size="icon" onClick={() => remove(index)}>
                <Trash2 className="h-4 w-4 text-destructive" />
              </Button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function parametersToEditable(parameters: ParameterDef[] | undefined): EditableParam[] {
  return (parameters ?? []).map((p) => ({
    name: p.name,
    type: p.type,
    description: p.description ?? "",
    required: p.required ?? false,
  }));
}

function fieldMapToEditable(fieldMap: Record<string, string> | undefined): EditableFieldMapping[] {
  return Object.entries(fieldMap ?? {}).map(([key, value]) => ({ key, value }));
}

/**
 * Runtime tool editor. Phase-1 field split: description + parameters +
 * response_mapping are the ONLY fields this dialog ever submits via the
 * overlay (`PATCH /v1/admin/tools/{name}`). Everything that is a
 * destination (url/base_url/endpoint), a secret binding (auth/headers), a
 * request-construction pin (method/body_template/timeout), or a security
 * control (requires_approval) is rendered read-only in `BaseOnlyToolPanel`
 * with a Promote/PR affordance -- it is structurally never included in the
 * submitted payload, so this dialog cannot be used to smuggle a base-only
 * change through the runtime overlay.
 */
function EditToolDialog({
  toolName,
  rev,
  onOpenChange,
}: {
  toolName: string | null;
  rev: number | undefined;
  onOpenChange: (open: boolean) => void;
}) {
  const { data: tools } = useTools();
  const { data: envelope } = useConfigEnvelope();
  const [description, setDescription] = useState("");
  const [parameters, setParameters] = useState<EditableParam[]>([]);
  const [resultPath, setResultPath] = useState("");
  const [fieldMap, setFieldMap] = useState<EditableFieldMapping[]>([]);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const updateTool = useUpdateTool();
  const { toast } = useToast();

  const basicTool = tools?.find((t) => t.name === toolName) ?? null;
  const manualTool =
    envelope?.config.tools.manual_tools?.find((t) => t.name === toolName) ?? null;

  useEffect(() => {
    if (!toolName) return;
    setSubmitError(null);
    if (manualTool) {
      setDescription(manualTool.description);
      setParameters(parametersToEditable(manualTool.parameters));
      setResultPath(manualTool.api.response_mapping?.result_path ?? "");
      setFieldMap(fieldMapToEditable(manualTool.api.response_mapping?.field_map));
    } else if (basicTool) {
      setDescription(basicTool.description);
      setParameters([]);
      setResultPath("");
      setFieldMap([]);
    }
    // manualTool/basicTool are looked up fresh every render from query-cache
    // data, not stable references -- key the reset off the tool's identity
    // (name) so it does not re-fire (and clobber in-progress edits) every
    // render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [toolName]);

  const handleSubmit = () => {
    if (!toolName || rev === undefined) return;
    setSubmitError(null);

    // Only ever description + parameters + response_mapping -- see the
    // module doc comment above. `api` here is a partial object containing
    // ONLY response_mapping; the backend's name-keyed overlay merge is a
    // deep dict merge (forge_config.loader._merge_node), so omitted `api`
    // keys (url/base_url/endpoint/method/headers/body_template/auth/
    // timeout) are left untouched on the existing entry -- never cleared,
    // never replaced.
    const payload: Record<string, unknown> = manualTool
      ? {
          description,
          parameters: parameters
            .filter((p) => p.name.trim().length > 0)
            .map((p) => ({
              name: p.name,
              type: p.type,
              description: p.description || undefined,
              required: p.required,
            })),
          api: {
            response_mapping: {
              result_path: resultPath || undefined,
              field_map: Object.fromEntries(
                fieldMap.filter((m) => m.key.trim().length > 0).map((m) => [m.key, m.value]),
              ),
            },
          },
        }
      : { description };

    updateTool.mutate(
      { name: toolName, tool: payload, rev },
      {
        onSuccess: (result) => {
          const state = deriveMutationUiState(result, undefined);
          if (isSuccessState(state)) {
            // Match the create-wizard treatment: a drifted save is still a
            // success, but the operator needs to know it landed on the
            // overlay only, not in Git -- an unannounced dialog close would
            // hide that.
            if (state.kind === "success-drift") {
              toast({ title: "Saved — not yet in Git", description: state.message });
            }
            onOpenChange(false);
          } else {
            // persisted:false -- never close the dialog or claim success.
            setSubmitError(state.message);
          }
        },
        onError: (err) => {
          setSubmitError(deriveMutationUiState(undefined, err).message);
        },
      },
    );
  };

  return (
    <Dialog open={toolName !== null} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>Edit Tool</DialogTitle>
          <DialogDescription>
            {manualTool
              ? "Runtime-editable fields only: description, parameters, and response mapping. The endpoint, auth, and other request-construction fields are Git-managed."
              : "Update the description for this tool. Endpoint, parameters, and auth are managed elsewhere."}
          </DialogDescription>
        </DialogHeader>
        {submitError && <p className="text-xs text-destructive">{submitError}</p>}

        <div className="space-y-4">
          <div className="space-y-1">
            {/* NOT id="tool-description" -- ManualToolWizard's IdentityStep
                (also always mounted; see components/ui/dialog.tsx) already
                claims that id, and duplicate DOM ids make a <label for>
                bind to whichever element rendered first. */}
            <label className="text-sm font-medium" htmlFor="edit-tool-description">
              Description
            </label>
            <Textarea
              id="edit-tool-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={3}
              placeholder="Describe what this tool does..."
            />
          </div>

          {manualTool && (
            <>
              <ParametersEditor parameters={parameters} onChange={setParameters} />
              <ResponseMappingEditor
                resultPath={resultPath}
                onResultPathChange={setResultPath}
                fieldMap={fieldMap}
                onFieldMapChange={setFieldMap}
              />
              <BaseOnlyToolPanel tool={manualTool} />
            </>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={updateTool.isPending}>
            {updateTool.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function ToolsPage() {
  const { data: tools, isLoading, error } = useTools();
  const { data: envelope } = useConfigEnvelope();
  const deleteTool = useDeleteTool();
  const { openWizard } = useToolStore();
  const { toast } = useToast();
  const [searchQuery, setSearchQuery] = useState("");
  const [addDialogOpen, setAddDialogOpen] = useState(false);
  const [editingTool, setEditingTool] = useState<string | null>(null);
  const [pendingDeleteTool, setPendingDeleteTool] = useState<string | null>(null);
  const editingDisabled = envelope?.mutation_policy === "disabled";

  const filteredTools = useMemo(() => {
    if (!tools) return [];
    if (!searchQuery.trim()) return tools;

    const lower = searchQuery.toLowerCase();
    return tools.filter(
      (tool) =>
        tool.name.toLowerCase().includes(lower) ||
        tool.description.toLowerCase().includes(lower) ||
        (tool.source?.toLowerCase().includes(lower) ?? false),
    );
  }, [tools, searchQuery]);

  const handleSelectType = (type: WizardType) => {
    openWizard(type);
  };

  const handleConfirmDeleteTool = useCallback(() => {
    if (pendingDeleteTool === null || envelope?.rev === undefined) return;
    deleteTool.mutate(
      { name: pendingDeleteTool, rev: envelope.rev },
      {
        onSuccess: (result) => {
          // Match the create-wizard treatment: surface drift even though
          // the delete itself succeeded, instead of a silent close.
          const state = deriveMutationUiState(result, undefined);
          if (state.kind === "success-drift") {
            toast({ title: "Saved — not yet in Git", description: state.message });
          }
          setPendingDeleteTool(null);
        },
      },
    );
  }, [pendingDeleteTool, envelope?.rev, deleteTool, toast]);

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <Wrench className="h-8 w-8 text-muted-foreground" />
              <div>
                <CardTitle>Tool Workshop</CardTitle>
                <CardDescription>
                  Create, test, and manage agent tools. Tools give your agent the ability to
                  interact with external systems -- import from an{" "}
                  <span className="font-medium text-foreground/80">OpenAPI</span> spec, define a{" "}
                  <span className="font-medium text-foreground/80">Manual</span> endpoint, or
                  chain tools into a{" "}
                  <span className="font-medium text-foreground/80">Workflow</span>.
                </CardDescription>
              </div>
            </div>
            <Button onClick={() => setAddDialogOpen(true)}>
              <Plus className="h-4 w-4" />
              Add Tool
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {/* Search bar */}
          <div className="relative mb-4">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              placeholder="Filter tools by name, description, or type (openapi, manual, workflow)..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-9"
            />
          </div>

          {/* Loading state */}
          {isLoading && (
            <div className="space-y-3">
              <ToolCardSkeleton />
              <ToolCardSkeleton />
              <ToolCardSkeleton />
            </div>
          )}

          {/* Error state */}
          {error && (
            <div className="flex min-h-[200px] items-center justify-center rounded-lg border border-dashed">
              <div className="text-center">
                <p className="text-sm font-medium text-destructive">
                  Failed to load tools
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {error instanceof Error ? error.message : "Unknown error"}
                </p>
              </div>
            </div>
          )}

          {/* Empty state */}
          {!isLoading && !error && filteredTools.length === 0 && (
            <div className="flex min-h-[200px] items-center justify-center rounded-lg border border-dashed">
              <div className="text-center">
                {searchQuery.trim() ? (
                  <>
                    <Search className="mx-auto h-8 w-8 text-muted-foreground mb-2" />
                    <p className="text-sm font-medium text-muted-foreground">
                      No tools match &ldquo;{searchQuery}&rdquo;
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      Try a different name, description keyword, or tool type
                      (openapi, manual, workflow).
                    </p>
                  </>
                ) : (
                  <>
                    <Wrench className="mx-auto h-8 w-8 text-muted-foreground mb-2" />
                    <p className="text-sm font-medium text-muted-foreground">
                      No tools registered yet
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground max-w-md mx-auto">
                      Tools let your agent call external APIs, query databases, search
                      the web, and more. Without tools, the agent can only respond from
                      its built-in training knowledge.
                    </p>
                    <HelpText>
                      Start with an <strong>OpenAPI Source</strong> if you have a spec URL,
                      a <strong>Manual Tool</strong> for any HTTP endpoint, or
                      a <strong>Workflow</strong> to chain multiple tools into a pipeline.
                    </HelpText>
                    <Button
                      variant="outline"
                      size="sm"
                      className="mt-3"
                      onClick={() => setAddDialogOpen(true)}
                    >
                      <Plus className="h-4 w-4" />
                      Add your first tool
                    </Button>
                  </>
                )}
              </div>
            </div>
          )}

          {/* Tool list */}
          {!isLoading && !error && filteredTools.length > 0 && (
            <div className="space-y-2">
              <p className="text-xs text-muted-foreground mb-2">
                {filteredTools.length} tool{filteredTools.length !== 1 ? "s" : ""}
                {searchQuery.trim() ? " found" : " registered"}
              </p>
              {filteredTools.map((tool) => (
                <div
                  key={tool.name}
                  className={cn(
                    "flex items-start gap-3 rounded-lg border p-4 transition-colors hover:bg-muted/30",
                  )}
                >
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-muted">
                    <Wrench className="h-5 w-5 text-muted-foreground" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <p className="text-sm font-medium truncate">
                        {tool.name}
                      </p>
                    </div>
                    <p className="mt-0.5 text-xs text-muted-foreground line-clamp-2">
                      {tool.description}
                    </p>
                  </div>
                  <SourceBadge source={tool.source} />
                  <div className="flex shrink-0 items-center gap-1">
                    <span
                      title={editingDisabled ? DISABLED_REASON : undefined}
                      className="inline-block"
                    >
                      <Button
                        size="icon"
                        variant="ghost"
                        className="h-8 w-8 shrink-0"
                        disabled={editingDisabled}
                        onClick={() => setEditingTool(tool.name)}
                      >
                        <Pencil className="h-4 w-4" />
                      </Button>
                    </span>
                    <span
                      title={editingDisabled ? DISABLED_REASON : undefined}
                      className="inline-block"
                    >
                      <Button
                        size="icon"
                        variant="ghost"
                        className="h-8 w-8 shrink-0"
                        disabled={editingDisabled}
                        onClick={() => setPendingDeleteTool(tool.name)}
                      >
                        <Trash2 className="h-4 w-4 text-destructive" />
                      </Button>
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Refreshing indicator */}
          <RefreshingIndicator isLoading={isLoading} hasTools={Boolean(tools && tools.length > 0)} />
        </CardContent>
      </Card>

      {/* Dialogs */}
      <AddToolDialog
        open={addDialogOpen}
        onOpenChange={setAddDialogOpen}
        onSelect={handleSelectType}
      />
      <OpenAPIWizard />
      <ManualToolWizard />
      <WorkflowComposer />
      <EditToolDialog
        toolName={editingTool}
        rev={envelope?.rev}
        onOpenChange={(open) => !open && setEditingTool(null)}
      />
      <ConfirmDialog
        open={pendingDeleteTool !== null}
        onOpenChange={(open) => !open && setPendingDeleteTool(null)}
        title="Delete tool?"
        description={`This removes the "${pendingDeleteTool ?? ""}" tool from the configuration. This cannot be undone from the UI.`}
        confirmLabel="Delete Tool"
        onConfirm={handleConfirmDeleteTool}
        isPending={deleteTool.isPending}
        errorMessage={
          deleteTool.isError
            ? deleteTool.error instanceof Error
              ? deleteTool.error.message
              : "Failed to delete tool"
            : null
        }
      />
    </div>
  );
}
