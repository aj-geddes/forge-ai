import { ShieldAlert } from "lucide-react";
import { useApprovals } from "@/api/hooks";
import { Badge } from "@/components/ui/badge";
import { ApprovalQueue } from "@/features/approvals/ApprovalQueue";
import { cn } from "@/lib/utils";
import { Eyebrow, HelpText } from "./shared";

const DASHBOARD_APPROVAL_LIMIT = 3;

/**
 * The Dashboard's hero: the human-in-the-loop decision queue. When
 * approvals are pending, this is the visual focus -- a molten-amber
 * "attention" treatment (border + wash + count badge) draws the eye here
 * first, ahead of activity or system health. When the queue is empty,
 * ApprovalQueue's own calm all-clear state keeps this section quiet, and
 * the attention framing drops away entirely.
 */
export function ApprovalsHero() {
  const { data: approvals } = useApprovals();
  const pendingCount = (approvals ?? []).filter((a) => a.status === "pending").length;
  const needsAttention = pendingCount > 0;

  return (
    <section
      className={cn(
        "space-y-3 rounded-xl",
        needsAttention && "border border-primary/30 bg-primary/5 p-4",
      )}
    >
      <div className="flex items-center justify-between gap-3">
        <Eyebrow icon={ShieldAlert} className={cn(needsAttention && "text-primary")}>
          Needs your approval
        </Eyebrow>
        {needsAttention && (
          <Badge className="bg-primary text-primary-foreground" aria-label={`${pendingCount} pending`}>
            {pendingCount}
          </Badge>
        )}
      </div>
      {needsAttention && (
        <HelpText>Review and decide on gated actions your agents are waiting to take.</HelpText>
      )}
      <ApprovalQueue limit={DASHBOARD_APPROVAL_LIMIT} />
    </section>
  );
}
