import { useEffect, useRef, useState } from "react";
import { Radio, CheckCircle2, XCircle } from "lucide-react";
import { useActivity } from "@/api/hooks";
import { formatRelativeTime } from "@/lib/time";
import { cn } from "@/lib/utils";
import type { ActivityEntry } from "@/types/activity";
import { Eyebrow, HelpText } from "./shared";

const ACTIVITY_FEED_LIMIT = 20;
const ARGUMENT_SUMMARY_MAX_CHARS = 60;

function summarizeArguments(args: Record<string, unknown>): string {
  const entries = Object.entries(args);
  if (entries.length === 0) return "";

  const parts = entries.map(([key, value]) => {
    const stringValue = typeof value === "string" ? value : JSON.stringify(value);
    return `${key}=${stringValue}`;
  });

  const result = parts.join(", ");
  return result.length > ARGUMENT_SUMMARY_MAX_CHARS
    ? `${result.slice(0, ARGUMENT_SUMMARY_MAX_CHARS - 1)}…`
    : result;
}

/** A stable key for an entry even though the backend doesn't assign one. */
function entryKey(entry: ActivityEntry): string {
  return `${entry.timestamp}-${entry.session_id}-${entry.tool}`;
}

function ActivityRow({ entry, isNew }: { entry: ActivityEntry; isNew: boolean }) {
  return (
    <div
      className={cn(
        "flex items-center gap-3 rounded-md px-3 py-2 font-mono text-xs",
        isNew && "animate-activity-highlight",
      )}
    >
      <span className="w-16 shrink-0 text-muted-foreground">
        {formatRelativeTime(entry.timestamp)}
      </span>
      {entry.ok ? (
        <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-healthy" aria-label="succeeded" />
      ) : (
        <XCircle className="h-3.5 w-3.5 shrink-0 text-destructive" aria-label="failed" />
      )}
      <span className="shrink-0 font-semibold text-foreground">{entry.tool}</span>
      <span className="min-w-0 flex-1 truncate text-muted-foreground">
        {summarizeArguments(entry.arguments)}
        {!entry.ok && entry.error ? ` -- ${entry.error}` : ""}
      </span>
    </div>
  );
}

function ActivityFeedSkeleton() {
  return (
    <div className="space-y-1 rounded-lg border bg-card p-3">
      {Array.from({ length: 5 }).map((_, i) => (
        <div key={i} className="h-5 animate-pulse rounded bg-muted" />
      ))}
    </div>
  );
}

/**
 * The Dashboard's signature element: a monospace telemetry log of the
 * agent's recent tool calls, polling every 4s. Newest entries get a brief
 * molten highlight (respecting prefers-reduced-motion via the CSS utility
 * itself). Unavailable (non-admin caller) and empty states are handled
 * quietly -- this never crashes the page.
 */
export function ActivityFeed() {
  const { data: entries, isLoading, isError } = useActivity(ACTIVITY_FEED_LIMIT);
  const previousTopKeyRef = useRef<string | null>(null);
  const [newestKey, setNewestKey] = useState<string | null>(null);

  useEffect(() => {
    const topEntry = entries?.[0];
    if (!topEntry) return;

    const topKey = entryKey(topEntry);
    if (previousTopKeyRef.current !== null && previousTopKeyRef.current !== topKey) {
      setNewestKey(topKey);
    }
    previousTopKeyRef.current = topKey;
  }, [entries]);

  return (
    <section className="space-y-2">
      <Eyebrow icon={Radio}>Live activity</Eyebrow>
      <HelpText>A live telemetry log of the agent's recent tool calls, updated every few seconds.</HelpText>

      {isLoading ? (
        <ActivityFeedSkeleton />
      ) : isError ? (
        <p className="rounded-lg border border-dashed p-4 text-center text-sm text-muted-foreground">
          Activity feed unavailable.
        </p>
      ) : !entries || entries.length === 0 ? (
        <p className="rounded-lg border border-dashed p-4 text-center text-sm text-muted-foreground">
          No activity yet — send the agent a prompt to watch it work.
        </p>
      ) : (
        <div className="max-h-80 space-y-1 overflow-y-auto rounded-lg border bg-card p-3">
          {entries.map((entry) => (
            <ActivityRow key={entryKey(entry)} entry={entry} isNew={entryKey(entry) === newestKey} />
          ))}
        </div>
      )}
    </section>
  );
}
