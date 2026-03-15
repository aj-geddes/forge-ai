import { create } from "zustand";

interface AuthState {
  apiKey: string | null;
  isAuthenticated: boolean;
  login: (key: string) => void;
  logout: () => void;
}

const STORAGE_KEY = "forge-api-key";

function getStoredKey(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(STORAGE_KEY);
}

export const useAuthStore = create<AuthState>((set) => ({
  apiKey: getStoredKey(),
  isAuthenticated: getStoredKey() !== null,

  login: (key: string) => {
    localStorage.setItem(STORAGE_KEY, key);
    set({ apiKey: key, isAuthenticated: true });
  },

  logout: () => {
    localStorage.removeItem(STORAGE_KEY);
    set({ apiKey: null, isAuthenticated: false });
  },
}));
