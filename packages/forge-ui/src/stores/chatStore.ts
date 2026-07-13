import { create } from "zustand";
import type { ToolCallRecord } from "@/api/chat";

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
}));
