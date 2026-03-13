import { useEffect, useCallback } from "react";
import { Settings, Save, RotateCcw, Loader2 } from "lucide-react";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useConfig, useUpdateConfig } from "@/api/hooks";
import { useConfigStore } from "@/stores/configStore";
import { useToast } from "@/components/ui/toast";
import { ConfigVisualEditor } from "./ConfigVisualEditor";
import { ConfigYamlEditor } from "./ConfigYamlEditor";
import { ConfigDiffView } from "./ConfigDiffView";

export function ConfigPage() {
  const { data: config, isLoading, error, refetch } = useConfig();
  const updateConfig = useUpdateConfig();
  const { toast } = useToast();

  const { draft, isDirty, setOriginal, resetDraft } = useConfigStore();

  // Load fetched config into the store
  useEffect(() => {
    if (config) {
      setOriginal(config);
    }
  }, [config, setOriginal]);

  const handleSave = useCallback(() => {
    if (!draft) return;
    updateConfig.mutate(draft, {
      onSuccess: (saved) => {
        setOriginal(saved);
        toast({ title: "Configuration saved", description: "Changes applied successfully." });
      },
      onError: (err) => {
        toast({
          title: "Save failed",
          description: err instanceof Error ? err.message : "Unknown error",
          variant: "destructive",
        });
      },
    });
  }, [draft, updateConfig, setOriginal, toast]);

  const handleReload = useCallback(() => {
    resetDraft();
    void refetch();
  }, [resetDraft, refetch]);

  // Loading state
  if (isLoading) {
    return (
      <div className="flex min-h-[400px] items-center justify-center">
        <div className="flex items-center gap-3 text-muted-foreground">
          <Loader2 className="h-5 w-5 animate-spin" />
          <span>Loading configuration...</span>
        </div>
      </div>
    );
  }

  // Error state
  if (error) {
    return (
      <div className="flex min-h-[400px] items-center justify-center">
        <div className="text-center space-y-3">
          <p className="text-destructive font-medium">
            Failed to load configuration
          </p>
          <p className="text-sm text-muted-foreground">
            {error instanceof Error ? error.message : "Unknown error"}
          </p>
          <Button variant="outline" size="sm" onClick={() => void refetch()}>
            <RotateCcw className="h-4 w-4" />
            Retry
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Top bar */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Settings className="h-6 w-6 text-muted-foreground" />
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">
              Config Builder
            </h1>
            <p className="text-sm text-muted-foreground">
              Visual editor for your Forge instance configuration
            </p>
          </div>
          {isDirty && (
            <Badge variant="outline" className="ml-2">
              Unsaved changes
            </Badge>
          )}
        </div>

        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={handleReload}
            disabled={updateConfig.isPending}
          >
            <RotateCcw className="h-4 w-4" />
            Reload
          </Button>
          <Button
            size="sm"
            onClick={handleSave}
            disabled={!isDirty || updateConfig.isPending}
          >
            {updateConfig.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Save className="h-4 w-4" />
            )}
            Save
          </Button>
        </div>
      </div>

      {/* Tabs: Visual / YAML / Diff */}
      <Tabs defaultValue="visual">
        <TabsList>
          <TabsTrigger value="visual">Visual</TabsTrigger>
          <TabsTrigger value="yaml">YAML</TabsTrigger>
          <TabsTrigger value="diff">
            Diff
            {isDirty && (
              <span className="ml-1.5 inline-block h-2 w-2 rounded-full bg-primary" />
            )}
          </TabsTrigger>
        </TabsList>

        <TabsContent value="visual">
          <ConfigVisualEditor />
        </TabsContent>

        <TabsContent value="yaml">
          <ConfigYamlEditor />
        </TabsContent>

        <TabsContent value="diff">
          <ConfigDiffView />
        </TabsContent>
      </Tabs>
    </div>
  );
}
