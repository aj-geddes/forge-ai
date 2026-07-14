// Admin-only live activity feed (Dashboard's "Live Activity" telemetry log).
// Same BFF cookie contract as the rest of the API layer (client.ts) -- a
// non-admin authenticated caller gets a 403 here (not a redirect); see
// useActivity in hooks.ts for how that's surfaced quietly in the UI.

import { api } from "./client";
import type { ActivityEntry, ActivityResponse } from "@/types/activity";

const DEFAULT_ACTIVITY_LIMIT = 20;

/**
 * Fetches the most recent tool-call activity entries, newest-first.
 */
export async function getActivity(
  limit: number = DEFAULT_ACTIVITY_LIMIT,
): Promise<ActivityEntry[]> {
  const res = await api.get<ActivityResponse>(`/v1/admin/activity?limit=${limit}`);
  // Defensive: React Query requires a defined value from queryFn, and a
  // malformed/unexpected envelope should degrade to "no activity" rather
  // than throw mid-render.
  return res?.activity ?? [];
}
