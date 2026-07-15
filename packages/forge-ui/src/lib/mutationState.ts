import { ApiError } from "@/api/client";
import type { ConfigMutationEnvelope, ConflictErrorBody } from "@/types/config";

// Derives what the UI is allowed to show from a config mutation's response,
// instead of a caller blindly toasting success on any 2xx. The core
// invariant: a "success" (or "success-drift") state is only reachable when
// the backend's honesty envelope says persisted === true. A 507
// (overlay write failed -- PVC full/RO), a 409 (optimistic-concurrency
// conflict), and a 405 (mutation_policy: disabled) all fail with an
// ApiError and must never be mistaken for success.

export type MutationUiState =
  | { kind: "success"; message: string }
  | { kind: "success-drift"; message: string; rev: number }
  | { kind: "rejected"; message: string }
  | {
      kind: "conflict";
      message: string;
      expectedRev?: number;
      currentRev?: number;
    }
  | { kind: "disabled"; message: string }
  | { kind: "error"; message: string };

const DISABLED_MESSAGE =
  "Config editing is disabled (GitOps-managed) — edit via git.";
const REJECTED_FALLBACK_MESSAGE =
  "Save failed — the change was not persisted.";
const DRIFT_MESSAGE =
  "Saved — durable on this instance. Not yet in Git; Promote to make it permanent.";
const DURABLE_FALLBACK_MESSAGE = "Configuration saved.";

function readConflictBody(body: unknown): ConflictErrorBody | undefined {
  if (typeof body !== "object" || body === null) return undefined;
  return body as ConflictErrorBody;
}

/**
 * The server's current revision for a 409, preferring the structured
 * `detail.current_rev` the admin API sends (`detail=str(exc)` days are
 * over -- see `ConflictErrorBody`), and falling back to a top-level
 * `current_rev`/`rev` for any response shape that predates the structured
 * detail.
 */
function extractCurrentRev(conflictBody: ConflictErrorBody | undefined): number | undefined {
  if (!conflictBody) return undefined;
  const { detail } = conflictBody;
  if (typeof detail === "object" && detail !== null && typeof detail.current_rev === "number") {
    return detail.current_rev;
  }
  return conflictBody.current_rev ?? conflictBody.rev;
}

export function deriveMutationUiState(
  envelope: ConfigMutationEnvelope | undefined,
  error: unknown,
  expectedRev?: number,
): MutationUiState {
  if (error) {
    if (error instanceof ApiError) {
      if (error.status === 405) {
        return { kind: "disabled", message: DISABLED_MESSAGE };
      }
      if (error.status === 409) {
        const conflictBody = readConflictBody(error.body);
        const currentRev = extractCurrentRev(conflictBody);
        return {
          kind: "conflict",
          message: `Someone changed this (rev ${expectedRev ?? "?"}→${currentRev ?? "?"}) — reload and reapply.`,
          expectedRev,
          currentRev,
        };
      }
      if (error.status === 507) {
        return {
          kind: "rejected",
          message: error.message || REJECTED_FALLBACK_MESSAGE,
        };
      }
      return { kind: "error", message: error.message };
    }
    return {
      kind: "error",
      message: error instanceof Error ? error.message : "Unknown error",
    };
  }

  if (!envelope) {
    return { kind: "error", message: "No response from server." };
  }

  if (!envelope.persisted) {
    return {
      kind: "rejected",
      message: envelope.message || REJECTED_FALLBACK_MESSAGE,
    };
  }

  if (envelope.drift_from_git) {
    return {
      kind: "success-drift",
      message: DRIFT_MESSAGE,
      rev: envelope.rev,
    };
  }

  return {
    kind: "success",
    message: envelope.message || DURABLE_FALLBACK_MESSAGE,
  };
}

/**
 * True only for the two states in which a success affordance may be shown.
 * Never true for "rejected" -- the never-show-success-unless-persisted
 * invariant.
 */
export function isSuccessState(state: MutationUiState): boolean {
  return state.kind === "success" || state.kind === "success-drift";
}
