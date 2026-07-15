import { useEffect, useState } from "react";
import { X, AlertTriangle } from "lucide-react";
import { Link } from "react-router-dom";
import { useConfigEnvelope, useConfigPromotionDiff } from "@/api/hooks";

const DISMISSED_REV_KEY = "forge:drift-dismissed-rev";

/**
 * Standing, per-tab-session-dismissible banner surfacing that the running
 * instance has overlay changes not yet reconciled into git (drift_from_git).
 * Dismissal is keyed to the current `rev` -- if a later mutation advances
 * `rev` while drift is still true, the banner reappears, since that is a
 * genuinely new unpromoted change the operator hasn't acknowledged yet.
 */
export function DriftBanner() {
  const { data: configData } = useConfigEnvelope();
  const [dismissedRev, setDismissedRev] = useState<string | null>(null);

  useEffect(() => {
    if (typeof window === "undefined") return;
    setDismissedRev(window.sessionStorage.getItem(DISMISSED_REV_KEY));
  }, [configData?.rev]);

  const rev = configData?.rev;
  const driftFromGit = configData?.drift_from_git ?? false;
  const isDismissedForCurrentRev = rev !== undefined && dismissedRev === String(rev);
  const shouldShow = Boolean(configData) && driftFromGit && !isDismissedForCurrentRev;

  // Only fetch the promotion diff (for the change count) once the banner is
  // actually going to render -- avoid an unnecessary background fetch.
  const { data: promotionDiff } = useConfigPromotionDiff({ enabled: shouldShow });
  const changedCount = promotionDiff?.changed_count;

  const handleDismiss = () => {
    if (typeof window === "undefined" || rev === undefined) return;
    window.sessionStorage.setItem(DISMISSED_REV_KEY, String(rev));
    setDismissedRev(String(rev));
  };

  if (!shouldShow) {
    return null;
  }

  const changeSummary =
    changedCount !== undefined
      ? `${changedCount} runtime change${changedCount === 1 ? "" : "s"} are live on this instance but not in Git`
      : "Runtime changes are live on this instance but not in Git";

  return (
    <div
      role="status"
      className="flex items-center justify-between gap-3 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-900 dark:text-amber-200"
    >
      <div className="flex flex-1 flex-wrap items-center gap-x-2 gap-y-1">
        <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" />
        <span>
          {changeSummary} — not permanent until you promote it (and lost only if the
          data volume is destroyed).
        </span>
        <Link
          to="/config?tab=promote"
          className="font-medium text-primary underline-offset-4 hover:underline"
        >
          Review &amp; promote
        </Link>
      </div>
      <button
        type="button"
        onClick={handleDismiss}
        aria-label="Dismiss"
        className="shrink-0 rounded p-1 hover:bg-amber-500/20 focus:outline-none focus:ring-2 focus:ring-amber-500/40"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  );
}
