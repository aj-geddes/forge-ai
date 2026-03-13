import { create } from "zustand";

function getInitialDarkMode(): boolean {
  if (typeof window === "undefined") return false;
  const stored = localStorage.getItem("forge-dark-mode");
  if (stored !== null) return stored === "true";
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

function getInitialSidebarCollapsed(): boolean {
  if (typeof window === "undefined") return false;
  return localStorage.getItem("forge-sidebar-collapsed") === "true";
}

interface UIState {
  sidebarCollapsed: boolean;
  darkMode: boolean;
  guideOpen: boolean;
  toggleSidebar: () => void;
  toggleDarkMode: () => void;
  toggleGuide: () => void;
}

export const useUIStore = create<UIState>((set) => ({
  sidebarCollapsed: getInitialSidebarCollapsed(),
  darkMode: getInitialDarkMode(),
  guideOpen: false,

  toggleSidebar: () =>
    set((state) => {
      const next = !state.sidebarCollapsed;
      localStorage.setItem("forge-sidebar-collapsed", String(next));
      return { sidebarCollapsed: next };
    }),

  toggleDarkMode: () =>
    set((state) => {
      const next = !state.darkMode;
      localStorage.setItem("forge-dark-mode", String(next));
      return { darkMode: next };
    }),

  toggleGuide: () =>
    set((state) => ({ guideOpen: !state.guideOpen })),
}));
