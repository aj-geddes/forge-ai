import { useState } from "react";
import { Loader2, CheckCircle2, Copy, Check } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useConfigPromotionDiff } from "@/api/hooks";

/**
 * Renders GET /v1/admin/config/promotion/diff -- the unified YAML diff
 * between the running overlay and git-owned BASE, plus a copy-paste PR
 * body, so an operator can manually commit the change to
 * deploy/helm/forge and make it permanent. This frontend never writes to
 * git itself; promotion is always a human, out-of-band git commit.
 */
export function PromotionDiffView() {
  const { data, isLoading, isError, error } = useConfigPromotionDiff();
  // Hooks must run unconditionally on every render (Rules of Hooks) -- this
  // has to sit above the loading/error/no-drift early returns below.
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    if (!data?.pr_body) return;
    try {
      await navigator.clipboard.writeText(data.pr_body);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard access can be unavailable/denied -- copying is a
      // convenience, not a requirement (the text is still selectable/
      // visible below), so fail silently rather than crash the view.
    }
  };

  if (isLoading) {
    return (
      <div className="flex min-h-[400px] flex-col items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-primary" />
        <p className="mt-4 text-sm text-muted-foreground">Loading promotion diff...</p>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="flex min-h-[400px] flex-col items-center justify-center">
        <p className="text-sm text-destructive">Failed to load the promotion diff</p>
        <p className="mt-2 text-xs text-muted-foreground">
          {error instanceof Error ? error.message : "Unknown error"}
        </p>
      </div>
    );
  }

  if (!data || !data.drift_from_git) {
    return (
      <div className="flex min-h-[400px] flex-col items-center justify-center">
        <CheckCircle2 className="h-10 w-10 text-green-600 dark:text-green-500" />
        <p className="mt-4 text-sm text-muted-foreground">
          Nothing to promote — the running config matches git.
        </p>
      </div>
    );
  }

  const diffLines = data.diff.split("\n");

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-lg font-semibold">Promotion Diff</h2>
        <Badge variant="outline">
          rev {data.rev} → base {data.base_rev ? data.base_rev.slice(0, 12) : "(none yet)"}
        </Badge>
        {typeof data.changed_count === "number" && (
          <Badge variant="secondary">
            {data.changed_count} change{data.changed_count === 1 ? "" : "s"}
          </Badge>
        )}
      </div>

      {/* Unified diff */}
      <div className="max-h-[420px] overflow-auto rounded-md border border-input font-mono text-sm">
        {diffLines.map((line, idx) => {
          const isAdded = line.startsWith("+") && !line.startsWith("+++");
          const isRemoved = line.startsWith("-") && !line.startsWith("---");
          const isHunk = line.startsWith("@@");
          return (
            <div
              key={idx}
              className={cn(
                "whitespace-pre px-2 py-0.5",
                isAdded && "bg-green-500/10 text-green-700 dark:text-green-400",
                isRemoved && "bg-red-500/10 text-red-700 dark:text-red-400",
                isHunk && "bg-muted/50 font-medium text-muted-foreground",
              )}
            >
              {line}
            </div>
          );
        })}
      </div>

      {/* PR Description */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-medium">PR Description</h3>
          <Button variant="outline" size="sm" onClick={() => void handleCopy()}>
            {copied ? (
              <>
                <Check className="h-3.5 w-3.5" />
                Copied
              </>
            ) : (
              <>
                <Copy className="h-3.5 w-3.5" />
                Copy
              </>
            )}
          </Button>
        </div>
        <div className="max-h-[300px] overflow-auto whitespace-pre-wrap rounded-md border bg-muted/30 p-4 text-xs font-mono">
          {data.pr_body}
        </div>
      </div>

      {/* Audit Trail */}
      {data.audit_trail && data.audit_trail.length > 0 && (
        <div className="space-y-3">
          <h3 className="text-sm font-medium">Audit Trail</h3>
          <div className="space-y-2">
            {data.audit_trail.map((entry, idx) => (
              <div
                key={idx}
                className="flex flex-wrap items-center gap-2 rounded-md border px-3 py-2 text-xs"
              >
                <span className="font-mono">rev {entry.rev}</span>
                {entry.updated_by && (
                  <span className="text-muted-foreground">by {entry.updated_by}</span>
                )}
                {entry.updated_at && (
                  <span className="text-muted-foreground">{entry.updated_at}</span>
                )}
                {entry.summary && (
                  <span className="italic text-muted-foreground">— {entry.summary}</span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Footer note */}
      <p className="text-xs text-muted-foreground">
        This frontend never writes to git. Copy the diff/PR body above and open a pull request
        against deploy/helm/forge to make this change permanent.
      </p>
    </div>
  );
}
