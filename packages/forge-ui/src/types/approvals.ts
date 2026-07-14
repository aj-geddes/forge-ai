// Mirrors the gateway's human-in-the-loop approval contract:
//   GET  /v1/admin/approvals                 -> ApprovalRequest[]
//   POST /v1/admin/approvals/{id}/approve     -> ApprovalDecisionResponse
//   POST /v1/admin/approvals/{id}/reject      -> ApprovalDecisionResponse
// A caller without `agent:approve` still sees pending requests (requires only
// `config:read`), but `arguments` values may arrive pre-redacted by the
// backend as the literal string "***REDACTED***" -- the UI never attempts to
// unredact these client-side.

export type ApprovalStatus = "pending" | "approved" | "rejected";

export const REDACTED_VALUE = "***REDACTED***";

export interface ApprovalRequest {
  id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  argument_hash: string;
  requested_by: string | null;
  run_id: string | null;
  draft_summary: string | null;
  created_at: string; // ISO8601
  status: ApprovalStatus;
}

export interface ApprovalDecisionResponse {
  id: string;
  status: ApprovalStatus;
  /** Only present on approve -- the result of executing the gated action. */
  result?: unknown;
}
