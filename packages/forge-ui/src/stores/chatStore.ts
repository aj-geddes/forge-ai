import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import type { ToolCallRecord } from "@/api/chat";

/** localStorage key for the persisted chat store. Bump `CHAT_STORE_VERSION`
 * (and add a `migrate` function) if the persisted shape ever changes. */
const CHAT_STORE_KEY = "forge-chat";
const CHAT_STORE_VERSION = 1;

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  toolsUsed?: string[];
  toolCalls?: ToolCallRecord[];
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
  /** Resume a session the server knows about but that isn't in local state
   * (e.g. after a browser refresh wiped out non-persisted sessions, or on a
   * new device). Creates an empty local shell if needed and makes it active;
   * a no-op on the existing session's messages if it's already local. */
  resumeSession: (id: string) => void;
  addMessage: (sessionId: string, message: Message) => void;
  appendMessageContent: (sessionId: string, messageId: string, delta: string) => void;
  addToolCallToMessage: (
    sessionId: string,
    messageId: string,
    toolCall: ToolCallRecord,
  ) => void;
  setLoading: (loading: boolean) => void;
}

function generateId(): string {
  return `session-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

function updateMessage(
  sessions: ChatSession[],
  sessionId: string,
  messageId: string,
  updater: (message: Message) => Message,
): ChatSession[] {
  return sessions.map((s) =>
    s.id === sessionId
      ? {
          ...s,
          messages: s.messages.map((m) => (m.id === messageId ? updater(m) : m)),
        }
      : s,
  );
}

export const useChatStore = create<ChatState>()(
  persist(
    (set) => ({
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

      resumeSession: (id: string) =>
        set((state) => {
          if (state.sessions.some((s) => s.id === id)) {
            return { activeSessionId: id };
          }
          return {
            sessions: [...state.sessions, { id, messages: [] }],
            activeSessionId: id,
          };
        }),

      addMessage: (sessionId: string, message: Message) =>
        set((state) => ({
          sessions: state.sessions.map((s) =>
            s.id === sessionId
              ? { ...s, messages: [...s.messages, message] }
              : s,
          ),
        })),

      appendMessageContent: (sessionId: string, messageId: string, delta: string) =>
        set((state) => ({
          sessions: updateMessage(state.sessions, sessionId, messageId, (m) => ({
            ...m,
            content: m.content + delta,
          })),
        })),

      addToolCallToMessage: (sessionId: string, messageId: string, toolCall: ToolCallRecord) =>
        set((state) => ({
          sessions: updateMessage(state.sessions, sessionId, messageId, (m) => ({
            ...m,
            toolCalls: [...(m.toolCalls ?? []), toolCall],
          })),
        })),

      setLoading: (loading: boolean) => set({ isLoading: loading }),
    }),
    {
      name: CHAT_STORE_KEY,
      version: CHAT_STORE_VERSION,
      storage: createJSONStorage(() => localStorage),
      // Only persist conversation data, not transient UI/network state.
      partialize: (state) => ({
        sessions: state.sessions,
        activeSessionId: state.activeSessionId,
      }),
    },
  ),
);
