import { create } from "zustand";
import type { ForgeConfig } from "@/types/config";

export interface ConfigDraftState {
  original: ForgeConfig | null;
  draft: ForgeConfig | null;
  isDirty: boolean;
  /** The config revision this client currently has loaded (mirrors the last-known server `rev`). */
  rev: number | null;
  /** The highest revision this client has confirmed the server actually persisted. */
  lastServerRev: number | null;
  /** True while a save mutation is in progress. */
  inflight: boolean;
  setOriginal: (config: ForgeConfig, rev?: number) => void;
  updateDraft: (config: ForgeConfig) => void;
  updateSection: <K extends keyof ForgeConfig>(
    section: K,
    value: ForgeConfig[K],
  ) => void;
  resetDraft: () => void;
  setInflight: (inflight: boolean) => void;
  /**
   * Optimistic-concurrency commit/rollback for the result of a save
   * mutation. A draft is only committed as the new `original` when the
   * server actually reports `persisted === true` AND the revision genuinely
   * advanced past the highest one this client has confirmed -- otherwise
   * the draft is rolled back to `original` (never silently clobbered, and
   * never treated as saved when it was not durably written).
   */
  applyMutationResult: (
    config: ForgeConfig,
    envelope: { persisted: boolean; rev: number },
  ) => void;
}

// The visual editor's form round-trip is lossless in meaning but not
// byte-for-byte: it may drop a key whose value is an unset-optional `null`
// (the backend serializes unset optionals as explicit `null`, e.g.
// `llm.litellm.endpoint`), and object spreads used while rebuilding nested
// structures (e.g. `model_list[].litellm_params`) do not preserve the
// original key insertion order. A naive `JSON.stringify` comparison is
// sensitive to both of those, producing a false "Unsaved changes" on load
// even when nothing was actually edited. `normalize` builds a canonical form
// that is insensitive to object key order and to null/undefined/absent-key
// distinctions, while leaving array element order intact -- array order is
// semantically significant (e.g. model_list routing priority) and a genuine
// change.
function normalize(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(normalize);
  }
  if (value !== null && typeof value === "object") {
    const normalized: Record<string, unknown> = {};
    for (const key of Object.keys(value as Record<string, unknown>).sort()) {
      const entryValue = (value as Record<string, unknown>)[key];
      if (entryValue === null || entryValue === undefined) continue;
      normalized[key] = normalize(entryValue);
    }
    return normalized;
  }
  return value;
}

function deepEqual(a: unknown, b: unknown): boolean {
  return JSON.stringify(normalize(a)) === JSON.stringify(normalize(b));
}

export const useConfigStore = create<ConfigDraftState>((set) => ({
  original: null,
  draft: null,
  isDirty: false,
  rev: null,
  lastServerRev: null,
  inflight: false,

  setOriginal: (config, rev) =>
    set((state) => ({
      original: structuredClone(config),
      draft: structuredClone(config),
      isDirty: false,
      rev: rev !== undefined ? rev : state.rev,
      lastServerRev: rev !== undefined ? rev : state.lastServerRev,
    })),

  updateDraft: (config) =>
    set((state) => ({
      draft: config,
      isDirty: !deepEqual(config, state.original),
    })),

  updateSection: (section, value) =>
    set((state) => {
      if (!state.draft) return state;
      const next = { ...state.draft, [section]: value };
      return {
        draft: next,
        isDirty: !deepEqual(next, state.original),
      };
    }),

  resetDraft: () =>
    set((state) => ({
      draft: state.original ? structuredClone(state.original) : null,
      isDirty: false,
      inflight: false,
    })),

  setInflight: (inflight) => set({ inflight }),

  applyMutationResult: (config, envelope) =>
    set((state) => {
      const advanced =
        state.lastServerRev === null || envelope.rev > state.lastServerRev;

      if (envelope.persisted && advanced) {
        return {
          original: structuredClone(config),
          draft: structuredClone(config),
          isDirty: false,
          rev: envelope.rev,
          lastServerRev: envelope.rev,
          inflight: false,
        };
      }

      return {
        draft: state.original ? structuredClone(state.original) : null,
        isDirty: false,
        inflight: false,
      };
    }),
}));
