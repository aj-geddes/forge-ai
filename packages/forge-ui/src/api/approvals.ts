// Human-in-the-loop approvals (gated tool calls awaiting a human decision).
// Same BFF cookie contract as the rest of the API layer (client.ts) -- the
// session cookie authenticates, and api.post auto-attaches X-CSRF-Token; no
// bearer token is ever held or sent by the UI. Listing requires
// `config:read`; approve/reject require `agent:approve` (see useAuth.can).

import { api } from "./client";
import type { ApprovalDecisionResponse, ApprovalRequest } from "@/types/approvals";

/**
 * Lists all approval requests (pending and already-decided), as returned --
 * the backend does not paginate or envelope this endpoint. Defensive: an
 * unexpected non-array response (e.g. a stubbed/misbehaving caller) degrades
 * to an empty queue rather than throwing mid-render.
 */
export async function listApprovals(): Promise<ApprovalRequest[]> {
  const res = await api.get<ApprovalRequest[]>("/v1/admin/approvals");
  return Array.isArray(res) ? res : [];
}

/** Approves and executes the gated action once. 404 if `id` is unknown, 409
 * if it was already resolved (approved or rejected) by someone else. */
export function approveApproval(id: string): Promise<ApprovalDecisionResponse> {
  return api.post<ApprovalDecisionResponse>(`/v1/admin/approvals/${id}/approve`);
}

/** Rejects the gated action. 404 if `id` is unknown, 409 if it was already
 * approved by someone else. */
export function rejectApproval(id: string): Promise<ApprovalDecisionResponse> {
  return api.post<ApprovalDecisionResponse>(`/v1/admin/approvals/${id}/reject`);
}
