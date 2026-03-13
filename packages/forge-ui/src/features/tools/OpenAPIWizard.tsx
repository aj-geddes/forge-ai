import { useState, useMemo } from "react";
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
import { Badge } from "@/components/ui/badge";
import { Select } from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useToast } from "@/components/ui/toast";
import { useToolStore } from "@/stores/toolStore";
import { cn } from "@/lib/utils";
import {
  Loader2,
  Search,
  Check,
} from "lucide-react";
import type { AuthType } from "@/types/config";

const STEP_LABELS = ["Source", "Select", "Configure", "Preview & Add"] as const;

function StepIndicator({ currentStep }: { currentStep: number }) {
  return (
    <div className="flex items-center gap-2 mb-6">
      {STEP_LABELS.map((label, index) => {
        const stepNum = index + 1;
        const isActive = stepNum === currentStep;
        const isCompleted = stepNum < currentStep;

        return (
          <div key={label} className="flex items-center gap-2">
            {index > 0 && (
              <div
                className={cn(
                  "h-px w-6",
                  isCompleted || isActive ? "bg-primary" : "bg-border",
                )}
              />
            )}
            <div className="flex items-center gap-1.5">
              <div
                className={cn(
                  "flex h-7 w-7 items-center justify-center rounded-full text-xs font-medium",
                  isActive
                    ? "bg-primary text-primary-foreground"
                    : isCompleted
                      ? "bg-primary/20 text-primary"
                      : "bg-muted text-muted-foreground",
                )}
              >
                {isCompleted ? <Check className="h-3.5 w-3.5" /> : stepNum}
              </div>
              <span
                className={cn(
                  "text-xs hidden sm:inline",
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

function SourceStep() {
  const { openApiData, setOpenApiData } = useToolStore();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleFetch = async () => {
    if (!openApiData.specUrl.trim()) return;

    setLoading(true);
    setError(null);

    try {
      const response = await fetch(openApiData.specUrl);
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      const spec = (await response.json()) as Record<string, unknown>;
      const info = spec.info as Record<string, string> | undefined;
      const paths = spec.paths as Record<string, Record<string, unknown>> | undefined;

      const operations: Array<{
        operationId: string;
        method: string;
        path: string;
        summary: string;
        description?: string;
      }> = [];

      if (paths) {
        for (const [path, methods] of Object.entries(paths)) {
          for (const [method, details] of Object.entries(methods)) {
            if (["get", "post", "put", "patch", "delete"].includes(method)) {
              const op = details as Record<string, string> | undefined;
              const operationId =
                op?.operationId ?? `${method.toUpperCase()} ${path}`;
              operations.push({
                operationId,
                method: method.toUpperCase(),
                path,
                summary: op?.summary ?? "",
                description: op?.description,
              });
            }
          }
        }
      }

      setOpenApiData({
        specTitle: info?.title ?? "Unknown",
        specVersion: info?.version ?? "0.0.0",
        operations,
        selected: operations.map((op) => op.operationId),
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch spec");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="spec-url">OpenAPI Spec URL</Label>
        <div className="flex gap-2">
          <Input
            id="spec-url"
            placeholder="https://api.example.com/openapi.json"
            value={openApiData.specUrl}
            onChange={(e) => setOpenApiData({ specUrl: e.target.value })}
          />
          <Button
            onClick={() => void handleFetch()}
            disabled={loading || !openApiData.specUrl.trim()}
          >
            {loading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              "Fetch"
            )}
          </Button>
        </div>
        {error && (
          <p className="text-sm text-destructive">{error}</p>
        )}
      </div>

      {openApiData.specTitle && openApiData.operations.length > 0 && (
        <div className="rounded-lg border bg-muted/50 p-4 space-y-2">
          <div className="flex items-center gap-2">
            <Badge variant="secondary">{openApiData.specVersion}</Badge>
            <span className="font-medium">{openApiData.specTitle}</span>
          </div>
          <p className="text-sm text-muted-foreground">
            {openApiData.operations.length} operation
            {openApiData.operations.length !== 1 ? "s" : ""} found
          </p>
        </div>
      )}
    </div>
  );
}

function SelectStep() {
  const {
    openApiData,
    toggleOperation,
    selectAllOperations,
    deselectAllOperations,
  } = useToolStore();
  const [filter, setFilter] = useState("");

  const filteredOperations = useMemo(() => {
    if (!filter.trim()) return openApiData.operations;
    const lower = filter.toLowerCase();
    return openApiData.operations.filter(
      (op) =>
        op.operationId.toLowerCase().includes(lower) ||
        op.path.toLowerCase().includes(lower) ||
        op.summary.toLowerCase().includes(lower),
    );
  }, [openApiData.operations, filter]);

  const methodColor = (method: string) => {
    switch (method) {
      case "GET":
        return "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400";
      case "POST":
        return "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400";
      case "PUT":
        return "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400";
      case "PATCH":
        return "bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-400";
      case "DELETE":
        return "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400";
      default:
        return "bg-muted text-muted-foreground";
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Filter operations..."
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="pl-9"
          />
        </div>
        <Button variant="outline" size="sm" onClick={selectAllOperations}>
          Select All
        </Button>
        <Button variant="outline" size="sm" onClick={deselectAllOperations}>
          Deselect All
        </Button>
      </div>

      <p className="text-xs text-muted-foreground">
        {openApiData.selected.length} of {openApiData.operations.length} selected
      </p>

      <ScrollArea className="max-h-[300px]">
        <div className="space-y-1">
          {filteredOperations.map((op) => {
            const isSelected = openApiData.selected.includes(op.operationId);
            return (
              <button
                key={op.operationId}
                type="button"
                onClick={() => toggleOperation(op.operationId)}
                className={cn(
                  "flex w-full items-center gap-3 rounded-md border px-3 py-2 text-left transition-colors",
                  isSelected
                    ? "border-primary/30 bg-primary/5"
                    : "border-transparent hover:bg-muted/50",
                )}
              >
                <div
                  className={cn(
                    "flex h-5 w-5 shrink-0 items-center justify-center rounded border",
                    isSelected
                      ? "border-primary bg-primary text-primary-foreground"
                      : "border-input",
                  )}
                >
                  {isSelected && <Check className="h-3 w-3" />}
                </div>
                <span
                  className={cn(
                    "inline-flex w-16 shrink-0 items-center justify-center rounded px-1.5 py-0.5 text-xs font-bold",
                    methodColor(op.method),
                  )}
                >
                  {op.method}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{op.operationId}</p>
                  <p className="truncate text-xs text-muted-foreground">
                    {op.path}
                    {op.summary ? ` - ${op.summary}` : ""}
                  </p>
                </div>
              </button>
            );
          })}
          {filteredOperations.length === 0 && (
            <p className="py-4 text-center text-sm text-muted-foreground">
              No operations match the filter.
            </p>
          )}
        </div>
      </ScrollArea>
    </div>
  );
}

function AuthFields({
  auth,
  onAuthChange,
}: {
  auth: { type: AuthType; token: string; headerName: string; username: string; password: string };
  onAuthChange: (data: Partial<typeof auth>) => void;
}) {
  return (
    <div className="space-y-3">
      <div className="space-y-2">
        <Label>Auth Type</Label>
        <Select
          value={auth.type}
          onChange={(e) => onAuthChange({ type: e.target.value as AuthType })}
        >
          <option value="none">None</option>
          <option value="bearer">Bearer Token</option>
          <option value="api_key">API Key</option>
          <option value="basic">Basic Auth</option>
        </Select>
      </div>

      {auth.type === "bearer" && (
        <div className="space-y-2">
          <Label htmlFor="auth-token">Bearer Token</Label>
          <Input
            id="auth-token"
            type="password"
            placeholder="Enter token..."
            value={auth.token}
            onChange={(e) => onAuthChange({ token: e.target.value })}
          />
        </div>
      )}

      {auth.type === "api_key" && (
        <>
          <div className="space-y-2">
            <Label htmlFor="auth-header">Header Name</Label>
            <Input
              id="auth-header"
              placeholder="X-API-Key"
              value={auth.headerName}
              onChange={(e) => onAuthChange({ headerName: e.target.value })}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="auth-key">API Key</Label>
            <Input
              id="auth-key"
              type="password"
              placeholder="Enter API key..."
              value={auth.token}
              onChange={(e) => onAuthChange({ token: e.target.value })}
            />
          </div>
        </>
      )}

      {auth.type === "basic" && (
        <>
          <div className="space-y-2">
            <Label htmlFor="auth-username">Username</Label>
            <Input
              id="auth-username"
              placeholder="Username"
              value={auth.username}
              onChange={(e) => onAuthChange({ username: e.target.value })}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="auth-password">Password</Label>
            <Input
              id="auth-password"
              type="password"
              placeholder="Password"
              value={auth.password}
              onChange={(e) => onAuthChange({ password: e.target.value })}
            />
          </div>
        </>
      )}
    </div>
  );
}

function ConfigureStep() {
  const { openApiData, setOpenApiData, setOpenApiAuth } = useToolStore();

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="namespace">Namespace</Label>
        <Input
          id="namespace"
          placeholder="e.g. my_api"
          value={openApiData.namespace}
          onChange={(e) => setOpenApiData({ namespace: e.target.value })}
        />
        <p className="text-xs text-muted-foreground">
          Tool names will be prefixed with this namespace (e.g. my_api__listUsers)
        </p>
      </div>

      <Separator />

      <AuthFields auth={openApiData.auth} onAuthChange={setOpenApiAuth} />
    </div>
  );
}

function PreviewStep() {
  const { openApiData } = useToolStore();

  const selectedOps = openApiData.operations.filter((op) =>
    openApiData.selected.includes(op.operationId),
  );

  const prefix = openApiData.namespace
    ? `${openApiData.namespace}__`
    : "";

  return (
    <div className="space-y-4">
      <div className="rounded-lg border bg-muted/50 p-3 space-y-1">
        <p className="text-sm">
          <span className="font-medium">Source:</span> {openApiData.specTitle} v
          {openApiData.specVersion}
        </p>
        <p className="text-sm">
          <span className="font-medium">Namespace:</span>{" "}
          {openApiData.namespace || "(none)"}
        </p>
        <p className="text-sm">
          <span className="font-medium">Auth:</span> {openApiData.auth.type}
        </p>
        <p className="text-sm">
          <span className="font-medium">Tools:</span> {selectedOps.length}
        </p>
      </div>

      <ScrollArea className="max-h-[250px]">
        <div className="space-y-1">
          {selectedOps.map((op) => (
            <div
              key={op.operationId}
              className="flex items-center gap-2 rounded-md border px-3 py-2"
            >
              <Badge variant="outline" className="shrink-0 font-mono text-xs">
                {op.method}
              </Badge>
              <span className="truncate text-sm font-medium">
                {prefix}
                {op.operationId}
              </span>
              <span className="ml-auto truncate text-xs text-muted-foreground">
                {op.path}
              </span>
            </div>
          ))}
        </div>
      </ScrollArea>
    </div>
  );
}

export function OpenAPIWizard() {
  const { wizardType, step, openApiData, closeWizard, nextStep, prevStep } =
    useToolStore();
  const { toast } = useToast();

  const isOpen = wizardType === "openapi";

  const canProceed = (): boolean => {
    switch (step) {
      case 1:
        return openApiData.operations.length > 0;
      case 2:
        return openApiData.selected.length > 0;
      case 3:
        return true;
      case 4:
        return true;
      default:
        return false;
    }
  };

  const handleAdd = () => {
    toast({
      title: "OpenAPI tools added",
      description: `${openApiData.selected.length} tool(s) from ${openApiData.specTitle} added successfully.`,
    });
    closeWizard();
  };

  return (
    <Dialog open={isOpen} onOpenChange={(open) => !open && closeWizard()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Add OpenAPI Source</DialogTitle>
          <DialogDescription>
            Import tools from an OpenAPI specification.
          </DialogDescription>
        </DialogHeader>

        <StepIndicator currentStep={step} />

        <div className="min-h-[280px]">
          {step === 1 && <SourceStep />}
          {step === 2 && <SelectStep />}
          {step === 3 && <ConfigureStep />}
          {step === 4 && <PreviewStep />}
        </div>

        <DialogFooter className="gap-2 sm:gap-0">
          {step > 1 && (
            <Button variant="outline" onClick={prevStep}>
              Back
            </Button>
          )}
          {step < 4 ? (
            <Button onClick={nextStep} disabled={!canProceed()}>
              Next
            </Button>
          ) : (
            <Button onClick={handleAdd}>Add</Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
