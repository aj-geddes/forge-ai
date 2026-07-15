import { useEffect, useCallback } from "react";
import { useSearchParams } from "react-router-dom";
import { Settings, Save, RotateCcw, Loader2 } from "lucide-react";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useConfigEnvelope, useUpdateConfig } from "@/api/hooks";
import { useConfigStore } from "@/stores/configStore";
import { useToast } from "@/components/ui/toast";
import { ConfigVisualEditor } from "./ConfigVisualEditor";
import { ConfigYamlEditor } from "./ConfigYamlEditor";
import { ConfigDiffView } from "./ConfigDiffView";
import { PromotionDiffView } from "./PromotionDiffView";
import { deriveMutationUiState } from "@/lib/mutationState";

export function ConfigPage() {
  const { data: envelope, isLoading, error, refetch } = useConfigEnvelope();
  const updateConfig = useUpdateConfig();
  const { toast } = useToast();

  const { draft, isDirty, rev, inflight, setOriginal, resetDraft, setInflight, applyMutationResult } = useConfigStore();
  const [searchParams, setSearchParams] = useSearchParams();

  const activeTab = searchParams.get("tab") ?? "visual";
  const handleTabChange = (value: string) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (value === "visual") {
        next.delete("tab");
      } else {
        next.set("tab", value);
      }
      return next;
    });
  };

  useEffect(() => {
    if (envelope) {
      setOriginal(envelope.config, envelope.rev);
    }
  }, [envelope, setOriginal]);

  const handleSave = useCallback(() => {
    if (!draft || rev === null) return;
    setInflight(true);
    updateConfig.mutate(
      { config: draft, rev },
      {
        onSuccess: (envelopeResult) => {
          applyMutationResult(draft, envelopeResult);
          const uiState = deriveMutationUiState(envelopeResult, undefined);
          if (uiState.kind === "success") {
            toast({ title: "Configuration saved", description: uiState.message });
          } else if (uiState.kind === "success-drift") {
            toast({ title: "Saved — not yet in Git", description: uiState.message });
          } else if (uiState.kind === "rejected") {
            toast({ title: "Save failed", description: uiState.message, variant: "destructive" });
          }
          void refetch();
        },
        onError: (err) => {
          applyMutationResult(draft, { persisted: false, rev: rev ?? 0 });
          const uiState = deriveMutationUiState(undefined, err, rev ?? undefined);
          const titleByKind: Record<string, string> = {
            conflict: "Someone else changed this",
            disabled: "Editing disabled",
            rejected: "Save failed",
            error: "Save failed",
          };
          toast({
            title: titleByKind[uiState.kind] ?? "Save failed",
            description: uiState.message,
            variant: "destructive",
          });
        },
      },
    );
  }, [draft, rev, updateConfig, applyMutationResult, setInflight, refetch, toast]);

  const handleReload = useCallback(() => {
    resetDraft();
    void refetch();
  }, [resetDraft, refetch]);

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

  if (error) {
    return (
      <div className="flex min-h-[400px] items-center justify-center">
        <div className="text-center space-y-3">
          <p className="text-destructive font-medium">Failed to load configuration</p>
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
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Settings className="h-6 w-6 text-muted-foreground" />
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Config Builder</h1>
            <p className="text-sm text-muted-foreground">
              Visual editor for your Forge instance configuration
            </p>
          </div>
          {envelope && (
            <>
              {isDirty && <Badge variant="outline" className="ml-2">Unsaved changes</Badge>}
              <Badge variant="outline" className="ml-2">rev {envelope.rev}</Badge>
              {envelope.mutation_policy === "disabled" ? (
                <Badge variant="secondary" className="ml-2">GitOps-managed (read-only)</Badge>
              ) : (
                <Badge variant="outline" className="ml-2">overlay-editable</Badge>
              )}
            </>
          )}
        </div>

        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={handleReload} disabled={updateConfig.isPending || inflight}>
            <RotateCcw className="h-4 w-4" />
            Reload
          </Button>
          {envelope?.mutation_policy === "disabled" ? (
            <span title="Config editing is disabled (GitOps-managed) — edit via git." className="inline-block">
              <Button size="sm" disabled>
                <Save className="h-4 w-4" />
                Save
              </Button>
            </span>
          ) : (
            <Button size="sm" onClick={handleSave} disabled={!isDirty || updateConfig.isPending || inflight}>
              {updateConfig.isPending || inflight ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
              Save
            </Button>
          )}
        </div>
      </div>

      <Tabs value={activeTab} onValueChange={handleTabChange} defaultValue="visual">
        <TabsList>
          <TabsTrigger value="visual">Visual</TabsTrigger>
          <TabsTrigger value="yaml">YAML</TabsTrigger>
          <TabsTrigger value="diff">
            Diff
            {isDirty && <span className="ml-1.5 inline-block h-2 w-2 rounded-full bg-primary" />}
          </TabsTrigger>
          <TabsTrigger value="promote">
            Promote
            {envelope?.drift_from_git && <span className="ml-1.5 inline-block h-2 w-2 rounded-full bg-amber-500" />}
          </TabsTrigger>
        </TabsList>
        <TabsContent value="visual"><ConfigVisualEditor /></TabsContent>
        <TabsContent value="yaml"><ConfigYamlEditor /></TabsContent>
        <TabsContent value="diff"><ConfigDiffView /></TabsContent>
        <TabsContent value="promote"><PromotionDiffView /></TabsContent>
      </Tabs>
    </div>
  );
}