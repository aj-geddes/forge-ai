import { CheckCircle2, ClipboardCheck, XCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { useApprovals } from "@/api/hooks";
import { formatRelativeTime } from "@/lib/time";
import { cn } from "@/lib/utils";
import type { ApprovalRequest } from "@/types/approvals";
import { ApprovalQueue } from "./ApprovalQueue";

const RECENTLY_DECIDED_LIMIT = 10;

function DecidedRow({ approval }: { approval: ApprovalRequest }) {
  const isApproved = approval.status === "approved";

  return (
    <div className="flex items-center justify-between gap-3 rounded-lg border px-4 py-3">
      <div className="flex min-w-0 items-center gap-3">
        {isApproved ? (
          <CheckCircle2 className="h-4 w-4 shrink-0 text-healthy" aria-hidden="true" />
        ) : (
          <XCircle className="h-4 w-4 shrink-0 text-destructive" aria-hidden="true" />
        )}
        <div className="min-w-0">
          <p className="truncate text-sm font-medium">
            {approval.requested_by ?? "an agent"}
            <span className="mx-1.5 text-muted-foreground" aria-hidden="true">
              &rarr;
            </span>
            <span className="font-mono text-xs text-muted-foreground">{approval.tool_name}</span>
          </p>
          <p className="text-xs text-muted-foreground">{formatRelativeTime(approval.created_at)}</p>
        </div>
      </div>
      <Badge
        variant="outline"
        className={cn("capitalize", isApproved ? "text-healthy" : "text-destructive")}
      >
        {approval.status}
      </Badge>
    </div>
  );
}

function RecentlyDecided() {
  const { data: approvals, isLoading, isError } = useApprovals();

  if (isLoading) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 2 }).map((_, i) => (
          <div key={i} className="h-14 animate-pulse rounded-lg border bg-muted/50" />
        ))}
      </div>
    );
  }

  if (isError) return null;

  const decided = (approvals ?? [])
    .filter((a) => a.status !== "pending")
    .slice(0, RECENTLY_DECIDED_LIMIT);

  if (decided.length === 0) {
    return <p className="text-sm text-muted-foreground">No decisions yet.</p>;
  }

  return (
    <div className="space-y-2">
      {decided.map((approval) => (
        <DecidedRow key={approval.id} approval={approval} />
      ))}
    </div>
  );
}

/**
 * The full approval queue: pending requests awaiting a human decision at
 * the top (via ApprovalQueue, unlimited here), and a read-only "recently
 * decided" history below for context on what was approved or rejected.
 */
export function ApprovalsPage() {
  return (
    <div className="space-y-8">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-bold tracking-tight">
          <ClipboardCheck className="h-6 w-6" />
          Approvals
        </h1>
        <p className="text-sm text-muted-foreground">
          Gated actions your agents are waiting on you to approve or reject.
        </p>
      </div>

      <section className="space-y-3">
        <h2 className="text-xs font-semibold uppercase tracking-[0.2em] text-muted-foreground">
          Pending
        </h2>
        <ApprovalQueue />
      </section>

      <section className="space-y-3 border-t pt-6">
        <h2 className="text-xs font-semibold uppercase tracking-[0.2em] text-muted-foreground">
          Recently decided
        </h2>
        <RecentlyDecided />
      </section>
    </div>
  );
}
