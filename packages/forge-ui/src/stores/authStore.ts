import { create } from "zustand";
import { safeStorage } from "@/lib/storage";

interface AuthState {
  apiKey: string | null;
  isAuthenticated: boolean;
  login: (key: string) => void;
  logout: () => void;
}

const STORAGE_KEY = "forge-api-key";

function getStoredKey(): string | null {
  return safeStorage.getItem(STORAGE_KEY);
}

export const useAuthStore = create<AuthState>((set) => ({
  apiKey: getStoredKey(),
  isAuthenticated: getStoredKey() !== null,

  login: (key: string) => {
    safeStorage.setItem(STORAGE_KEY, key);
    set({ apiKey: key, isAuthenticated: true });
  },

  logout: () => {
    safeStorage.removeItem(STORAGE_KEY);
    set({ apiKey: null, isAuthenticated: false });
  },
}));
