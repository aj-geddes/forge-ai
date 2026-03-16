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
import { Separator } from "@/components/ui/separator";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useToast } from "@/components/ui/toast";
import { useAddToolToConfig } from "@/api/hooks";
import { useToolStore } from "@/stores/toolStore";
import { useTools } from "@/api/hooks";
import {
  Plus,
  Trash2,
  ChevronUp,
  ChevronDown,
  GitBranch,
} from "lucide-react";

export function WorkflowComposer() {
  const {
    wizardType,
    workflowData,
    setWorkflowData,
    addWorkflowStep,
    removeWorkflowStep,
    updateWorkflowStep,
    moveWorkflowStep,
    closeWizard,
  } = useToolStore();
  const { toast } = useToast();
  const { data: tools } = useTools();

  const isOpen = wizardType === "workflow";

  const canSave =
    workflowData.name.trim().length > 0 &&
    workflowData.steps.length > 0 &&
    workflowData.steps.every((s) => s.tool.trim().length > 0);

  const addToolMutation = useAddToolToConfig();

  const handleSave = () => {
    const workflow = {
      name: workflowData.name,
      description: workflowData.description,
      steps: workflowData.steps.map((s) => ({
        tool: s.tool,
        ...(s.params && s.params !== "{}" ? { params: JSON.parse(s.params) } : {}),
        ...(s.outputAs ? { output_as: s.outputAs } : {}),
        ...(s.condition ? { condition: s.condition } : {}),
      })),
    };

    addToolMutation.mutate(
      (tools) => ({
        ...tools,
        workflows: [...(tools.workflows ?? []), workflow],
      }),
      {
        onSuccess: () => {
          toast({
            title: "Workflow saved",
            description: `Workflow "${workflowData.name}" with ${workflowData.steps.length} step(s) saved to config.`,
          });
          closeWizard();
        },
        onError: (err) => {
          toast({
            title: "Failed to save workflow",
            description: err instanceof Error ? err.message : "Unknown error",
            variant: "destructive",
          });
        },
      },
    );
  };

  return (
    <Dialog open={isOpen} onOpenChange={(open) => !open && closeWizard()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <GitBranch className="h-5 w-5" />
            Workflow Builder
          </DialogTitle>
          <DialogDescription>
            Compose a multi-step workflow by chaining tools together.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="wf-name">Workflow Name</Label>
              <Input
                id="wf-name"
                placeholder="e.g. research_pipeline"
                value={workflowData.name}
                onChange={(e) => setWorkflowData({ name: e.target.value })}
              />
            </div>
            <div className="space-y-2 col-span-2">
              <Label htmlFor="wf-desc">Description</Label>
              <Textarea
                id="wf-desc"
                placeholder="Describe what this workflow does..."
                value={workflowData.description}
                onChange={(e) =>
                  setWorkflowData({ description: e.target.value })
                }
                rows={2}
              />
            </div>
          </div>

          <Separator />

          <div className="flex items-center justify-between">
            <p className="text-sm font-medium">
              Steps ({workflowData.steps.length})
            </p>
            <Button variant="outline" size="sm" onClick={addWorkflowStep}>
              <Plus className="h-4 w-4" />
              Add Step
            </Button>
          </div>

          <ScrollArea className="max-h-[340px]">
            {workflowData.steps.length === 0 ? (
              <div className="flex h-32 items-center justify-center rounded-lg border border-dashed">
                <div className="text-center">
                  <p className="text-sm text-muted-foreground">
                    No steps yet.
                  </p>
                  <p className="text-xs text-muted-foreground mt-1">
                    Click "Add Step" to begin building your workflow.
                  </p>
                </div>
              </div>
            ) : (
              <div className="space-y-3">
                {workflowData.steps.map((step, index) => (
                  <div
                    key={step.id}
                    className="rounded-lg border p-3 space-y-3"
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span className="flex h-6 w-6 items-center justify-center rounded-full bg-primary text-xs font-medium text-primary-foreground">
                          {index + 1}
                        </span>
                        <span className="text-sm font-medium">
                          Step {index + 1}
                        </span>
                      </div>
                      <div className="flex items-center gap-1">
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7"
                          disabled={index === 0}
                          onClick={() => moveWorkflowStep(index, "up")}
                        >
                          <ChevronUp className="h-4 w-4" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7"
                          disabled={index === workflowData.steps.length - 1}
                          onClick={() => moveWorkflowStep(index, "down")}
                        >
                          <ChevronDown className="h-4 w-4" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7"
                          onClick={() => removeWorkflowStep(index)}
                        >
                          <Trash2 className="h-4 w-4 text-destructive" />
                        </Button>
                      </div>
                    </div>

                    <div className="grid grid-cols-2 gap-3">
                      <div className="space-y-1">
                        <Label className="text-xs">Tool</Label>
                        <Select
                          value={step.tool}
                          onChange={(e) =>
                            updateWorkflowStep(index, {
                              tool: e.target.value,
                            })
                          }
                          className="h-8 text-sm"
                        >
                          <option value="">Select a tool...</option>
                          {tools?.map((t) => (
                            <option key={t.name} value={t.name}>
                              {t.name}
                            </option>
                          ))}
                        </Select>
                      </div>
                      <div className="space-y-1">
                        <Label className="text-xs">Output As</Label>
                        <Input
                          placeholder="variable_name"
                          value={step.outputAs}
                          onChange={(e) =>
                            updateWorkflowStep(index, {
                              outputAs: e.target.value,
                            })
                          }
                          className="h-8 text-sm"
                        />
                      </div>
                    </div>

                    <div className="space-y-1">
                      <Label className="text-xs">Parameters (JSON)</Label>
                      <Textarea
                        placeholder='{"key": "value"}'
                        value={step.params}
                        onChange={(e) =>
                          updateWorkflowStep(index, {
                            params: e.target.value,
                          })
                        }
                        rows={2}
                        className="font-mono text-xs"
                      />
                    </div>

                    <div className="space-y-1">
                      <Label className="text-xs">Condition (optional)</Label>
                      <Input
                        placeholder='e.g. steps.step1.status == "success"'
                        value={step.condition}
                        onChange={(e) =>
                          updateWorkflowStep(index, {
                            condition: e.target.value,
                          })
                        }
                        className="h-8 text-sm"
                      />
                    </div>
                  </div>
                ))}
              </div>
            )}
          </ScrollArea>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={closeWizard}>
            Cancel
          </Button>
          <Button onClick={handleSave} disabled={!canSave}>
            Save Workflow
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
