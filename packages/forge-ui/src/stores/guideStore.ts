import { create } from "zustand";

function getInitialTourCompleted(): boolean {
  if (typeof window === "undefined") return false;
  return localStorage.getItem("forge-tour-completed") === "true";
}

interface GuideState {
  panelOpen: boolean;
  currentSection: string | null;
  tourActive: boolean;
  tourStep: number;
  tourCompleted: boolean;
  searchQuery: string;
  togglePanel: () => void;
  openPanel: (sectionId?: string) => void;
  closePanel: () => void;
  startTour: () => void;
  nextTourStep: () => void;
  prevTourStep: () => void;
  endTour: () => void;
  setSearchQuery: (query: string) => void;
}

const TOTAL_TOUR_STEPS = 5;

export const useGuideStore = create<GuideState>((set) => ({
  panelOpen: false,
  currentSection: null,
  tourActive: false,
  tourStep: 0,
  tourCompleted: getInitialTourCompleted(),
  searchQuery: "",

  togglePanel: () =>
    set((state) => ({ panelOpen: !state.panelOpen })),

  openPanel: (sectionId?: string) =>
    set({
      panelOpen: true,
      currentSection: sectionId ?? null,
    }),

  closePanel: () =>
    set({ panelOpen: false }),

  startTour: () =>
    set({ tourActive: true, tourStep: 0 }),

  nextTourStep: () =>
    set((state) => {
      const next = state.tourStep + 1;
      if (next >= TOTAL_TOUR_STEPS) {
        localStorage.setItem("forge-tour-completed", "true");
        return { tourActive: false, tourStep: 0, tourCompleted: true };
      }
      return { tourStep: next };
    }),

  prevTourStep: () =>
    set((state) => ({
      tourStep: Math.max(0, state.tourStep - 1),
    })),

  endTour: () =>
    set(() => {
      localStorage.setItem("forge-tour-completed", "true");
      return { tourActive: false, tourStep: 0, tourCompleted: true };
    }),

  setSearchQuery: (query: string) =>
    set({ searchQuery: query }),
}));
