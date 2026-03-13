import { create } from "zustand";
import type { ForgeConfig } from "@/types/config";

export interface ConfigDraftState {
  original: ForgeConfig | null;
  draft: ForgeConfig | null;
  isDirty: boolean;
  setOriginal: (config: ForgeConfig) => void;
  updateDraft: (config: ForgeConfig) => void;
  updateSection: <K extends keyof ForgeConfig>(
    section: K,
    value: ForgeConfig[K],
  ) => void;
  resetDraft: () => void;
}

function deepEqual(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

export const useConfigStore = create<ConfigDraftState>((set) => ({
  original: null,
  draft: null,
  isDirty: false,

  setOriginal: (config) =>
    set({
      original: structuredClone(config),
      draft: structuredClone(config),
      isDirty: false,
    }),

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
    })),
}));
