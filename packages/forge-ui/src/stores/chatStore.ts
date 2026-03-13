import { create } from "zustand";

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  toolsUsed?: string[];
  timestamp: number;
}

export interface ChatSession {
  id: string;
  messages: Message[];
}

interface ChatState {
  sessions: ChatSession[];
  activeSessionId: string | null;
  isLoading: boolean;
  createSession: () => string;
  setActiveSession: (id: string) => void;
  addMessage: (sessionId: string, message: Message) => void;
  setLoading: (loading: boolean) => void;
}

function generateId(): string {
  return `session-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

export const useChatStore = create<ChatState>((set) => ({
  sessions: [],
  activeSessionId: null,
  isLoading: false,

  createSession: () => {
    const id = generateId();
    set((state) => ({
      sessions: [...state.sessions, { id, messages: [] }],
      activeSessionId: id,
    }));
    return id;
  },

  setActiveSession: (id: string) => set({ activeSessionId: id }),

  addMessage: (sessionId: string, message: Message) =>
    set((state) => ({
      sessions: state.sessions.map((s) =>
        s.id === sessionId
          ? { ...s, messages: [...s.messages, message] }
          : s,
      ),
    })),

  setLoading: (loading: boolean) => set({ isLoading: loading }),
}));
