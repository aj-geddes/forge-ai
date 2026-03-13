import { useMemo } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Select } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Separator } from "@/components/ui/separator";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useToast } from "@/components/ui/toast";
import { useToolStore } from "@/stores/toolStore";
import { cn } from "@/lib/utils";
import {
  FileText,
  Link,
  Settings,
  ListTree,
  GitBranch,
  FlaskConical,
  Check,
  Plus,
  Trash2,
} from "lucide-react";
import type { AuthType, HTTPMethod, ParamType } from "@/types/config";
import { stringify } from "yaml";

const STEP_LABELS = [
  "Identity",
  "Endpoint",
  "Method & Auth",
  "Parameters",
  "Response",
  "Test",
] as const;

const STEP_ICONS = [
  FileText,
  Link,
  Settings,
  ListTree,
  GitBranch,
  FlaskConical,
] as const;

function StepIndicator({ currentStep }: { currentStep: number }) {
  return (
    <div className="flex items-center gap-1 mb-6 overflow-x-auto">
      {STEP_LABELS.map((label, index) => {
        const stepNum = index + 1;
        const isActive = stepNum === currentStep;
        const isCompleted = stepNum < currentStep;
        // Keep STEP_ICONS reference to suppress unused warning
        void STEP_ICONS[index];

        return (
          <div key={label} className="flex items-center gap-1">
            {index > 0 && (
              <div
                className={cn(
                  "h-px w-4",
                  isCompleted || isActive ? "bg-primary" : "bg-border",
                )}
              />
            )}
            <div className="flex items-center gap-1">
              <div
                className={cn(
                  "flex h-6 w-6 items-center justify-center rounded-full text-xs font-medium",
                  isActive
                    ? "bg-primary text-primary-foreground"
                    : isCompleted
                      ? "bg-primary/20 text-primary"
                      : "bg-muted text-muted-foreground",
                )}
              >
                {isCompleted ? <Check className="h-3 w-3" /> : stepNum}
              </div>
              <span
                className={cn(
                  "text-xs hidden lg:inline whitespace-nowrap",
                  isActive ? "text-foreground font-medium" : "text-muted-foreground",
                )}
              >
                {label}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function IdentityStep() {
  const { manualData, setManualData } = useToolStore();

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="tool-name">Tool Name</Label>
        <Input
          id="tool-name"
          placeholder="e.g. get_weather"
          value={manualData.name}
          onChange={(e) => setManualData({ name: e.target.value })}
        />
        <p className="text-xs text-muted-foreground">
          Use snake_case. This will be the function name the agent calls.
        </p>
      </div>
      <div className="space-y-2">
        <Label htmlFor="tool-description">Description</Label>
        <Textarea
          id="tool-description"
          placeholder="Describe what this tool does..."
          value={manualData.description}
          onChange={(e) => setManualData({ description: e.target.value })}
          rows={3}
        />
        <p className="text-xs text-muted-foreground">
          Clear descriptions help the agent know when to use this tool.
        </p>
      </div>
    </div>
  );
}

function EndpointStep() {
  const { manualData, setManualData } = useToolStore();

  const resolvedUrl = useMemo(() => {
    const base = manualData.baseUrl.replace(/\/+$/, "");
    const endpoint = manualData.endpoint.startsWith("/")
      ? manualData.endpoint
      : `/${manualData.endpoint}`;
    if (!base && !manualData.endpoint) return "";
    return `${base}${endpoint}`;
  }, [manualData.baseUrl, manualData.endpoint]);

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="base-url">Base URL</Label>
        <Input
          id="base-url"
          placeholder="https://api.example.com"
          value={manualData.baseUrl}
          onChange={(e) => setManualData({ baseUrl: e.target.value })}
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor="endpoint">Endpoint</Label>
        <Input
          id="endpoint"
          placeholder="/v1/weather"
          value={manualData.endpoint}
          onChange={(e) => setManualData({ endpoint: e.target.value })}
        />
      </div>
      {resolvedUrl && (
        <div className="rounded-lg border bg-muted/50 p-3">
          <p className="text-xs text-muted-foreground mb-1">Resolved URL</p>
          <p className="text-sm font-mono break-all">{resolvedUrl}</p>
        </div>
      )}
    </div>
  );
}

function MethodAuthStep() {
  const { manualData, setManualData, setManualAuth } = useToolStore();

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label>HTTP Method</Label>
        <Select
          value={manualData.method}
          onChange={(e) => setManualData({ method: e.target.value as HTTPMethod })}
        >
          <option value="GET">GET</option>
          <option value="POST">POST</option>
          <option value="PUT">PUT</option>
          <option value="PATCH">PATCH</option>
          <option value="DELETE">DELETE</option>
        </Select>
      </div>

      <Separator />

      <div className="space-y-3">
        <div className="space-y-2">
          <Label>Auth Type</Label>
          <Select
            value={manualData.auth.type}
            onChange={(e) =>
              setManualAuth({ type: e.target.value as AuthType })
            }
          >
            <option value="none">None</option>
            <option value="bearer">Bearer Token</option>
            <option value="api_key">API Key</option>
            <option value="basic">Basic Auth</option>
          </Select>
        </div>

        {manualData.auth.type === "bearer" && (
          <div className="space-y-2">
            <Label htmlFor="m-auth-token">Bearer Token</Label>
            <Input
              id="m-auth-token"
              type="password"
              placeholder="Enter token..."
              value={manualData.auth.token}
              onChange={(e) => setManualAuth({ token: e.target.value })}
            />
          </div>
        )}

        {manualData.auth.type === "api_key" && (
          <>
            <div className="space-y-2">
              <Label htmlFor="m-auth-header">Header Name</Label>
              <Input
                id="m-auth-header"
                placeholder="X-API-Key"
                value={manualData.auth.headerName}
                onChange={(e) =>
                  setManualAuth({ headerName: e.target.value })
                }
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="m-auth-key">API Key</Label>
              <Input
                id="m-auth-key"
                type="password"
                placeholder="Enter API key..."
                value={manualData.auth.token}
                onChange={(e) => setManualAuth({ token: e.target.value })}
              />
            </div>
          </>
        )}

        {manualData.auth.type === "basic" && (
          <>
            <div className="space-y-2">
              <Label htmlFor="m-auth-user">Username</Label>
              <Input
                id="m-auth-user"
                placeholder="Username"
                value={manualData.auth.username}
                onChange={(e) =>
                  setManualAuth({ username: e.target.value })
                }
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="m-auth-pass">Password</Label>
              <Input
                id="m-auth-pass"
                type="password"
                placeholder="Password"
                value={manualData.auth.password}
                onChange={(e) =>
                  setManualAuth({ password: e.target.value })
                }
              />
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function ParametersStep() {
  const {
    manualData,
    addManualParameter,
    removeManualParameter,
    updateManualParameter,
  } = useToolStore();

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm font-medium">Parameters</p>
          <p className="text-xs text-muted-foreground">
            Define input parameters for this tool.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={addManualParameter}>
          <Plus className="h-4 w-4" />
          Add Parameter
        </Button>
      </div>

      <ScrollArea className="max-h-[300px]">
        {manualData.parameters.length === 0 ? (
          <div className="flex h-24 items-center justify-center rounded-lg border border-dashed">
            <p className="text-sm text-muted-foreground">
              No parameters defined yet.
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            {manualData.parameters.map((param, index) => (
              <div
                key={index}
                className="rounded-lg border p-3 space-y-3"
              >
                <div className="flex items-start gap-2">
                  <div className="flex-1 space-y-2">
                    <div className="flex gap-2">
                      <div className="flex-1">
                        <Label className="text-xs">Name</Label>
                        <Input
                          placeholder="param_name"
                          value={param.name}
                          onChange={(e) =>
                            updateManualParameter(index, {
                              name: e.target.value,
                            })
                          }
                          className="h-8 text-sm"
                        />
                      </div>
                      <div className="w-36">
                        <Label className="text-xs">Type</Label>
                        <Select
                          value={param.type}
                          onChange={(e) =>
                            updateManualParameter(index, {
                              type: e.target.value as ParamType,
                            })
                          }
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
                    </div>
                    <div>
                      <Label className="text-xs">Description</Label>
                      <Textarea
                        placeholder="Describe this parameter..."
                        value={param.description}
                        onChange={(e) =>
                          updateManualParameter(index, {
                            description: e.target.value,
                          })
                        }
                        rows={2}
                        className="text-sm"
                      />
                    </div>
                  </div>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="mt-4 shrink-0"
                    onClick={() => removeManualParameter(index)}
                  >
                    <Trash2 className="h-4 w-4 text-destructive" />
                  </Button>
                </div>
                <div className="flex items-center gap-2">
                  <Switch
                    checked={param.required}
                    onCheckedChange={(checked) =>
                      updateManualParameter(index, { required: checked })
                    }
                  />
                  <Label className="text-xs">Required</Label>
                </div>
              </div>
            ))}
          </div>
        )}
      </ScrollArea>
    </div>
  );
}

function ResponseStep() {
  const {
    manualData,
    setManualResponseMapping,
    addFieldMapping,
    removeFieldMapping,
    updateFieldMapping,
  } = useToolStore();

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="result-path">Result Path (JSONPath)</Label>
        <Input
          id="result-path"
          placeholder="e.g. $.data.results"
          value={manualData.responseMapping.resultPath}
          onChange={(e) =>
            setManualResponseMapping({ resultPath: e.target.value })
          }
        />
        <p className="text-xs text-muted-foreground">
          JSONPath expression to extract the result from the API response.
        </p>
      </div>

      <Separator />

      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-medium">Field Mapping</p>
            <p className="text-xs text-muted-foreground">
              Map response fields to output names.
            </p>
          </div>
          <Button variant="outline" size="sm" onClick={addFieldMapping}>
            <Plus className="h-4 w-4" />
            Add Mapping
          </Button>
        </div>

        {manualData.responseMapping.fieldMap.length === 0 ? (
          <div className="flex h-16 items-center justify-center rounded-lg border border-dashed">
            <p className="text-sm text-muted-foreground">
              No field mappings defined.
            </p>
          </div>
        ) : (
          <div className="space-y-2">
            {manualData.responseMapping.fieldMap.map((mapping, index) => (
              <div key={index} className="flex items-center gap-2">
                <Input
                  placeholder="Response field"
                  value={mapping.key}
                  onChange={(e) =>
                    updateFieldMapping(index, { key: e.target.value })
                  }
                  className="h-8 text-sm"
                />
                <span className="shrink-0 text-muted-foreground">→</span>
                <Input
                  placeholder="Output name"
                  value={mapping.value}
                  onChange={(e) =>
                    updateFieldMapping(index, { value: e.target.value })
                  }
                  className="h-8 text-sm"
                />
                <Button
                  variant="ghost"
                  size="icon"
                  className="shrink-0"
                  onClick={() => removeFieldMapping(index)}
                >
                  <Trash2 className="h-4 w-4 text-destructive" />
                </Button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function TestStep() {
  const { manualData } = useToolStore();

  const yamlPreview = useMemo(() => {
    const base = manualData.baseUrl.replace(/\/+$/, "");
    const endpoint = manualData.endpoint.startsWith("/")
      ? manualData.endpoint
      : `/${manualData.endpoint}`;
    const url = `${base}${endpoint}`;

    const tool: Record<string, unknown> = {
      name: manualData.name,
      description: manualData.description,
      parameters: manualData.parameters.map((p) => ({
        name: p.name,
        type: p.type,
        description: p.description,
        required: p.required,
      })),
      api: {
        url,
        method: manualData.method,
        ...(manualData.auth.type !== "none" && {
          auth: { type: manualData.auth.type },
        }),
        ...(manualData.responseMapping.resultPath && {
          response_mapping: {
            result_path: manualData.responseMapping.resultPath,
          },
        }),
      },
    };

    try {
      return stringify(tool, { indent: 2 });
    } catch {
      return "# Error generating YAML preview";
    }
  }, [manualData]);

  return (
    <div className="space-y-4">
      <div>
        <p className="text-sm font-medium mb-2">Tool Configuration Preview</p>
        <p className="text-xs text-muted-foreground mb-3">
          Review the YAML configuration that will be generated.
        </p>
      </div>
      <ScrollArea className="max-h-[320px]">
        <pre className="rounded-lg border bg-muted/50 p-4 text-xs font-mono whitespace-pre-wrap">
          {yamlPreview}
        </pre>
      </ScrollArea>
    </div>
  );
}

export function ManualToolWizard() {
  const { wizardType, step, manualData, closeWizard, nextStep, prevStep } =
    useToolStore();
  const { toast } = useToast();

  const isOpen = wizardType === "manual";

  const canProceed = (): boolean => {
    switch (step) {
      case 1:
        return manualData.name.trim().length > 0 && manualData.description.trim().length > 0;
      case 2:
        return manualData.baseUrl.trim().length > 0;
      case 3:
        return true;
      case 4:
        return true;
      case 5:
        return true;
      case 6:
        return true;
      default:
        return false;
    }
  };

  const handleAdd = () => {
    toast({
      title: "Manual tool added",
      description: `Tool "${manualData.name}" has been added successfully.`,
    });
    closeWizard();
  };

  return (
    <Dialog open={isOpen} onOpenChange={(open) => !open && closeWizard()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Create Manual Tool</DialogTitle>
          <DialogDescription>
            Define a custom tool with its endpoint, parameters, and response mapping.
          </DialogDescription>
        </DialogHeader>

        <StepIndicator currentStep={step} />

        <div className="min-h-[280px]">
          {step === 1 && <IdentityStep />}
          {step === 2 && <EndpointStep />}
          {step === 3 && <MethodAuthStep />}
          {step === 4 && <ParametersStep />}
          {step === 5 && <ResponseStep />}
          {step === 6 && <TestStep />}
        </div>

        <DialogFooter className="gap-2 sm:gap-0">
          {step > 1 && (
            <Button variant="outline" onClick={prevStep}>
              Back
            </Button>
          )}
          {step < 6 ? (
            <Button onClick={nextStep} disabled={!canProceed()}>
              Next
            </Button>
          ) : (
            <Button onClick={handleAdd}>Add Tool</Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
