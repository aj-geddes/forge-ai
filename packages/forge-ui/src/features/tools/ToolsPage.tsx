import { useState, useMemo } from "react";
import {
  Wrench,
  Plus,
  Search,
  Globe,
  FileText,
  GitBranch,
  Loader2,
  Package,
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
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { useTools } from "@/api/hooks";
import { useToolStore } from "@/stores/toolStore";
import { cn } from "@/lib/utils";
import { OpenAPIWizard } from "./OpenAPIWizard";
import { ManualToolWizard } from "./ManualToolWizard";
import { WorkflowComposer } from "./WorkflowComposer";
import type { WizardType } from "@/stores/toolStore";

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
      description: "Import tools from an OpenAPI specification URL.",
    },
    {
      type: "manual" as const,
      icon: FileText,
      title: "Manual Tool",
      description: "Define a custom tool with endpoint, parameters, and response mapping.",
    },
    {
      type: "workflow" as const,
      icon: GitBranch,
      title: "Workflow",
      description: "Compose a multi-step workflow by chaining tools together.",
    },
  ];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add Tool</DialogTitle>
          <DialogDescription>
            Choose how you want to add a new tool.
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

export function ToolsPage() {
  const { data: tools, isLoading, error } = useTools();
  const { openWizard } = useToolStore();
  const [searchQuery, setSearchQuery] = useState("");
  const [addDialogOpen, setAddDialogOpen] = useState(false);

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
                  Create, test, and manage agent tools
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
              placeholder="Search tools by name, description, or source..."
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
                      No tools match "{searchQuery}"
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      Try adjusting your search query.
                    </p>
                  </>
                ) : (
                  <>
                    <Wrench className="mx-auto h-8 w-8 text-muted-foreground mb-2" />
                    <p className="text-sm font-medium text-muted-foreground">
                      No tools registered
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      Get started by adding an OpenAPI source, creating a manual tool,
                      or building a workflow.
                    </p>
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
                    {tool.parameters && tool.parameters.length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-1">
                        {tool.parameters.map((param) => (
                          <Badge
                            key={param.name}
                            variant="outline"
                            className="text-xs font-normal"
                          >
                            {param.name}
                            {param.required && (
                              <span className="text-destructive ml-0.5">*</span>
                            )}
                          </Badge>
                        ))}
                      </div>
                    )}
                  </div>
                  <SourceBadge source={tool.source} />
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
    </div>
  );
}
