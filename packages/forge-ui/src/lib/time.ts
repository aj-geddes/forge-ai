// Relative-time formatting for the activity feed and other live telemetry.

const JUST_NOW_THRESHOLD_SECONDS = 10;
const SECONDS_PER_MINUTE = 60;
const MINUTES_PER_HOUR = 60;
const HOURS_PER_DAY = 24;

/**
 * Formats an ISO8601 timestamp as a short relative time string, e.g.
 * "just now", "2m ago", "3h ago", "5d ago". A malformed timestamp is
 * returned unchanged so a bad value never throws or renders blank.
 */
export function formatRelativeTime(isoTimestamp: string): string {
  const timestampMs = new Date(isoTimestamp).getTime();
  if (Number.isNaN(timestampMs)) return isoTimestamp;

  // Clamp small negative diffs (clock skew between client/server) to zero
  // rather than leaking a raw future-looking value into the UI.
  const diffMs = Math.max(0, Date.now() - timestampMs);
  const seconds = Math.floor(diffMs / 1000);

  if (seconds < JUST_NOW_THRESHOLD_SECONDS) return "just now";
  if (seconds < SECONDS_PER_MINUTE) return `${seconds}s ago`;

  const minutes = Math.floor(seconds / SECONDS_PER_MINUTE);
  if (minutes < MINUTES_PER_HOUR) return `${minutes}m ago`;

  const hours = Math.floor(minutes / MINUTES_PER_HOUR);
  if (hours < HOURS_PER_DAY) return `${hours}h ago`;

  const days = Math.floor(hours / HOURS_PER_DAY);
  return `${days}d ago`;
}
