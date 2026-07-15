import { describe, it } from "vitest";
import { expect } from "vitest";
import { ApiError } from "@/api/client";
import type { ConfigMutationEnvelope, ConflictErrorBody } from "@/types/config";
import { deriveMutationUiState, isSuccessState } from "./mutationState";

describe("deriveMutationUiState", () => {
  describe("success cases", () => {
    it("1. envelope persisted:true, durable:true, drift_from_git:false -> kind 'success'", () => {
      const envelope: ConfigMutationEnvelope = {
        persisted: true,
        durable: true,
        drift_from_git: false,
        rev: 10,
        base_rev: "9",
        promotion_available: false,
        message: "Changes saved successfully.",
      };
      const state = deriveMutationUiState(envelope, undefined);
      expect(state).toEqual({
        kind: "success",
        message: "Changes saved successfully.",
      });
    });

    it("2. envelope persisted:true, drift_from_git:true -> kind 'success-drift'", () => {
      const envelope: ConfigMutationEnvelope = {
        persisted: true,
        durable: false,
        drift_from_git: true,
        rev: 12,
        base_rev: "11",
        promotion_available: true,
        message: "Changes saved on instance.",
      };
      const state = deriveMutationUiState(envelope, undefined);
      expect(state).toEqual({
        kind: "success-drift",
        message: "Saved — durable on this instance. Not yet in Git; Promote to make it permanent.",
        rev: 12,
      });
    });
  });

  describe("rejected cases", () => {
    it("3. envelope persisted:false -> kind 'rejected', message is envelope.message", () => {
      const envelope: ConfigMutationEnvelope = {
        persisted: false,
        durable: false,
        drift_from_git: false,
        rev: 0,
        base_rev: "0",
        promotion_available: false,
        message: "Validation failed.",
      };
      const state = deriveMutationUiState(envelope, undefined);
      expect(state).toEqual({
        kind: "rejected",
        message: "Validation failed.",
      });
    });

    it("4. envelope persisted:false with EMPTY message -> fallback message", () => {
      const envelope: ConfigMutationEnvelope = {
        persisted: false,
        durable: false,
        drift_from_git: false,
        rev: 0,
        base_rev: "0",
        promotion_available: false,
        message: "",
      };
      const state = deriveMutationUiState(envelope, undefined);
      expect(state).toEqual({
        kind: "rejected",
        message: "Save failed — the change was not persisted.",
      });
    });
  });

  describe("error cases", () => {
    it("5. 405 ApiError -> kind 'disabled'", () => {
      const error = new ApiError(405, "Method Not Allowed");
      const state = deriveMutationUiState(undefined, error);
      expect(state).toEqual({
        kind: "disabled",
        message: "Config editing is disabled (GitOps-managed) — edit via git.",
      });
    });

    it("6. 409 Conflict with current_rev, expectedRev=5 -> kind 'conflict'", () => {
      const error = new ApiError(409, "Conflict", { current_rev: 7 } as ConflictErrorBody);
      const state = deriveMutationUiState(undefined, error, 5);
      expect(state).toEqual({
        kind: "conflict",
        message: "Someone changed this (rev 5→7) — reload and reapply.",
        expectedRev: 5,
        currentRev: 7,
      });
    });

    it("7. 409 Conflict with rev (not current_rev), expectedRev=3 -> currentRev=9", () => {
      const error = new ApiError(409, "Conflict", { rev: 9 } as ConflictErrorBody);
      const state = deriveMutationUiState(undefined, error, 3);
      expect(state).toEqual({
        kind: "conflict",
        message: "Someone changed this (rev 3→9) — reload and reapply.",
        expectedRev: 3,
        currentRev: 9,
      });
    });

    it("7b. 409 Conflict with the backend's structured {detail: {message, current_rev}} body -> reads current_rev from detail, expectedRev=5", () => {
      const error = new ApiError(409, "Conflict", {
        detail: { message: "stale rev: expected 5, current rev is 8", current_rev: 8 },
      } as ConflictErrorBody);
      const state = deriveMutationUiState(undefined, error, 5);
      expect(state).toEqual({
        kind: "conflict",
        message: "Someone changed this (rev 5→8) — reload and reapply.",
        expectedRev: 5,
        currentRev: 8,
      });
    });

    it("7c. 409 Conflict with a plain-string detail (pre-structured backend) -> falls back to top-level current_rev", () => {
      const error = new ApiError(409, "Conflict", {
        detail: "stale rev: expected 2, current rev is 6",
        current_rev: 6,
      } as ConflictErrorBody);
      const state = deriveMutationUiState(undefined, error, 2);
      expect(state).toEqual({
        kind: "conflict",
        message: "Someone changed this (rev 2→6) — reload and reapply.",
        expectedRev: 2,
        currentRev: 6,
      });
    });

    it("8. 507 ApiError -> kind 'rejected'", () => {
      const error = new ApiError(507, "Insufficient Storage");
      const state = deriveMutationUiState(undefined, error);
      expect(state).toEqual({
        kind: "rejected",
        message: "Insufficient Storage",
      });
    });

    it("9. 500 ApiError -> kind 'error'", () => {
      const error = new ApiError(500, "boom");
      const state = deriveMutationUiState(undefined, error);
      expect(state).toEqual({
        kind: "error",
        message: "boom",
      });
    });

    it("10. plain Error -> kind 'error'", () => {
      const error = new Error("network down");
      const state = deriveMutationUiState(undefined, error);
      expect(state).toEqual({
        kind: "error",
        message: "network down",
      });
    });

    it("11. non-Error thrown value -> kind 'error' with 'Unknown error'", () => {
      const error = "string error";
      const state = deriveMutationUiState(undefined, error as unknown);
      expect(state).toEqual({
        kind: "error",
        message: "Unknown error",
      });
    });

    it("12. no error and envelope undefined -> kind 'error'", () => {
      const state = deriveMutationUiState(undefined, undefined);
      expect(state).toEqual({
        kind: "error",
        message: "No response from server.",
      });
    });

    it("13. error takes precedence over envelope (409 + truthy envelope)", () => {
      const envelope: ConfigMutationEnvelope = {
        persisted: true,
        durable: true,
        drift_from_git: false,
        rev: 10,
        base_rev: "9",
        promotion_available: false,
        message: "Should be ignored",
      };
      const error = new ApiError(409, "Conflict", { current_rev: 7 } as ConflictErrorBody);
      const state = deriveMutationUiState(envelope, error, 5);
      expect(state).toEqual({
        kind: "conflict",
        message: "Someone changed this (rev 5→7) — reload and reapply.",
        expectedRev: 5,
        currentRev: 7,
      });
    });
  });
});

describe("isSuccessState", () => {
  it("14. returns true only for 'success' and 'success-drift' — never show success unless persisted", () => {
    const successState = { kind: "success" as const, message: "ok" };
    const successDriftState = { kind: "success-drift" as const, message: "ok", rev: 1 };
    const rejectedState = { kind: "rejected" as const, message: "fail" };
    const conflictState = { kind: "conflict" as const, message: "conflict", expectedRev: 1, currentRev: 2 };
    const disabledState = { kind: "disabled" as const, message: "disabled" };
    const errorState = { kind: "error" as const, message: "error" };

    expect(isSuccessState(successState)).toBe(true);
    expect(isSuccessState(successDriftState)).toBe(true);
    expect(isSuccessState(rejectedState)).toBe(false);
    expect(isSuccessState(conflictState)).toBe(false);
    expect(isSuccessState(disabledState)).toBe(false);
    expect(isSuccessState(errorState)).toBe(false);
  });
});