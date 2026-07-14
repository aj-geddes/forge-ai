// Mirrors the gateway's admin activity feed contract:
//   GET /v1/admin/activity?limit=N -> { activity: ActivityEntry[] }
// Entries are newest-first. This is the live telemetry log of the agent's
// recent tool calls, shown on the Dashboard's Live Activity feed.

export interface ActivityEntry {
  tool: string;
  arguments: Record<string, unknown>;
  ok: boolean;
  error?: string | null;
  timestamp: string; // ISO8601 UTC
  session_id: string | null;
  interface: string;
}

export interface ActivityResponse {
  activity: ActivityEntry[];
}
