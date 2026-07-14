import { useState } from "react";
import { Link } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { Check, Loader2, MessageSquareQuote, ShieldCheck, X } from "lucide-react";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { useAuth } from "@/api/auth";
import { queryKeys, useApprovals, useApproveApproval, useRejectApproval } from "@/api/hooks";
import { ApiError } from "@/api/client";
import { formatRelativeTime } from "@/lib/time";
import { REDACTED_VALUE, type ApprovalRequest } from "@/types/approvals";

const SOCIAL_PUBLISH_TOOL = "social_publish";
const HTTP_CONFLICT = 409;

// ---------------------------------------------------------------------------
// Argument rendering
// ---------------------------------------------------------------------------

function isSkippableArgValue(value: unknown): boolean {
  if (value === REDACTED_VALUE) return true;
  if (value === null || value === undefined) return true;
  if (typeof value === "string" && value.trim().length === 0) return true;
  return false;
}

function formatArgValue(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value);
}

/** A clean key -> value list for any gated tool that isn't social_publish,
 * skipping redacted (caller lacks agent:approve) or empty values. */
function ArgumentList({ arguments: args }: { arguments: Record<string, unknown> }) {
  const entries = Object.entries(args).filter(([, value]) => !isSkippableArgValue(value));

  if (entries.length === 0) {
    return <p className="text-xs text-muted-foreground">No arguments to review.</p>;
  }

  return (
    <dl className="space-y-1 text-xs">
      {entries.map(([key, value]) => (
        <div key={key} className="flex gap-2">
          <dt className="shrink-0 font-medium text-muted-foreground">{key}</dt>
          <dd className="min-w-0 flex-1 truncate font-mono text-foreground">
            {formatArgValue(value)}
          </dd>
        </div>
      ))}
    </dl>
  );
}

/** social_publish's draft, shown prominently as a quoted post preview with
 * its target channel/platform -- this is the whole point of the review. */
function SocialPublishPreview({ arguments: args }: { arguments: Record<string, unknown> }) {
  const content = typeof args.content === "string" ? args.content : null;
  const channel =
    (typeof args.channel === "string" && args.channel) ||
    (typeof args.platform === "string" && args.platform) ||
    null;

  if (!content) {
    return <ArgumentList arguments={args} />;
  }

  return (
    <div className="space-y-1.5">
      {channel && (
        <span className="inline-flex items-center rounded-full bg-muted px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
          {channel}
        </span>
      )}
      <blockquote className="flex items-start gap-2 rounded-md border-l-2 border-primary/50 bg-muted/50 px-3 py-2 text-sm italic text-foreground">
        <MessageSquareQuote className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" aria-hidden="true" />
        <span>&ldquo;{content}&rdquo;</span>
      </blockquote>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Approval card
// ---------------------------------------------------------------------------

interface ApprovalCardProps {
  approval: ApprovalRequest;
  canApprove: boolean;
  onDecided: (id: string) => void;
  onDecisionFailed: (id: string) => void;
}

function decisionResultLabel(approval: ApprovalRequest, result: unknown): string {
  if (approval.tool_name === SOCIAL_PUBLISH_TOOL) return "Published";
  if (result != null && typeof result === "object" && "published" in (result as Record<string, unknown>)) {
    return (result as Record<string, unknown>).published ? "Published" : "Approved";
  }
  return "Approved";
}

function ApprovalCard({ approval, canApprove, onDecided, onDecisionFailed }: ApprovalCardProps) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const approveMutation = useApproveApproval();
  const rejectMutation = useRejectApproval();
  const isBusy = approveMutation.isPending || rejectMutation.isPending;
  const requester = approval.requested_by ?? "an agent";

  const handleFailure = (err: unknown) => {
    onDecisionFailed(approval.id);
    const isConflict = err instanceof ApiError && err.status === HTTP_CONFLICT;
    toast({
      title: isConflict ? "Already resolved" : "Action failed",
      description: isConflict
        ? "Someone else already decided this request."
        : "Please try again.",
      variant: "destructive",
    });
    void queryClient.invalidateQueries({ queryKey: queryKeys.approvals });
  };

  const handleApprove = () => {
    onDecided(approval.id);
    approveMutation.mutate(approval.id, {
      onSuccess: (decision) => {
        toast({
          title: decisionResultLabel(approval, decision.result),
          description: `${approval.tool_name} for ${requester}.`,
        });
      },
      onError: handleFailure,
    });
  };

  const handleReject = () => {
    onDecided(approval.id);
    rejectMutation.mutate(approval.id, {
      onSuccess: () => {
        toast({ title: "Rejected", description: `${approval.tool_name} for ${requester}.` });
      },
      onError: handleFailure,
    });
  };

  return (
    <Card className="border-l-4 border-l-primary/60">
      <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0 pb-3">
        <div className="min-w-0">
          <p className="text-sm font-semibold">
            {requester}
            <span className="mx-1.5 text-muted-foreground" aria-hidden="true">
              &rarr;
            </span>
            <span className="font-mono text-xs text-muted-foreground">{approval.tool_name}</span>
          </p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {formatRelativeTime(approval.created_at)}
          </p>
        </div>
        {!canApprove && <Badge variant="outline">Pending</Badge>}
      </CardHeader>
      <CardContent className="space-y-3 pt-0">
        {approval.tool_name === SOCIAL_PUBLISH_TOOL ? (
          <SocialPublishPreview arguments={approval.arguments} />
        ) : (
          <ArgumentList arguments={approval.arguments} />
        )}
        {canApprove && (
          <div className="flex items-center gap-2 pt-1">
            <Button
              size="sm"
              onClick={handleApprove}
              disabled={isBusy}
              className="bg-healthy text-primary-foreground hover:bg-healthy/90"
            >
              {approveMutation.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              ) : (
                <Check className="h-3.5 w-3.5" aria-hidden="true" />
              )}
              Approve
            </Button>
            <Button size="sm" variant="destructive" onClick={handleReject} disabled={isBusy}>
              {rejectMutation.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              ) : (
                <X className="h-3.5 w-3.5" aria-hidden="true" />
              )}
              Reject
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Queue
// ---------------------------------------------------------------------------

const APPROVAL_QUEUE_SKELETON_ROWS = 2;

function ApprovalQueueSkeleton() {
  return (
    <div className="space-y-3">
      {Array.from({ length: APPROVAL_QUEUE_SKELETON_ROWS }).map((_, i) => (
        <div key={i} className="h-24 animate-pulse rounded-lg border bg-muted/50" />
      ))}
    </div>
  );
}

export interface ApprovalQueueProps {
  /** Cap the number of cards rendered, with a "view all" link below (e.g.
   * the Dashboard's condensed hero). Omit for the full Approvals page. */
  limit?: number;
}

/**
 * Renders the pending human-in-the-loop approval queue as cards -- the
 * requesting agent + tool, the draft content (a quoted post preview for
 * social_publish, a clean key/value list otherwise), and Approve/Reject
 * actions gated on `agent:approve`. A decided card is optimistically
 * removed on click; a 409 (someone else already decided it) or any other
 * mutation failure refetches so the queue reflects reality.
 */
export function ApprovalQueue({ limit }: ApprovalQueueProps) {
  const { can, isLoading: authLoading } = useAuth();
  const { data: approvals, isLoading, isError } = useApprovals();
  const [resolvedIds, setResolvedIds] = useState<Set<string>>(new Set());

  const markResolved = (id: string) => {
    setResolvedIds((prev) => new Set(prev).add(id));
  };
  const unmarkResolved = (id: string) => {
    setResolvedIds((prev) => {
      const next = new Set(prev);
      next.delete(id);
      return next;
    });
  };

  if (isLoading || authLoading) {
    return <ApprovalQueueSkeleton />;
  }

  if (isError) {
    return (
      <p className="rounded-lg border border-dashed p-4 text-center text-sm text-muted-foreground">
        Approval queue unavailable.
      </p>
    );
  }

  const pending = (approvals ?? []).filter(
    (a) => a.status === "pending" && !resolvedIds.has(a.id),
  );

  if (pending.length === 0) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
        <ShieldCheck className="h-4 w-4 text-healthy" aria-hidden="true" />
        <span>All clear — nothing is waiting on your approval.</span>
      </div>
    );
  }

  const visible = typeof limit === "number" ? pending.slice(0, limit) : pending;
  const canApprove = can("agent:approve");

  return (
    <div className="space-y-3">
      {visible.map((approval) => (
        <ApprovalCard
          key={approval.id}
          approval={approval}
          canApprove={canApprove}
          onDecided={markResolved}
          onDecisionFailed={unmarkResolved}
        />
      ))}
      {typeof limit === "number" && pending.length > limit && (
        <Link
          to="/approvals"
          className="block rounded-md py-1 text-center text-xs font-medium text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
        >
          View all {pending.length} pending &rarr;
        </Link>
      )}
    </div>
  );
}
