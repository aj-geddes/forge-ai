import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Message } from "./chatStore";

describe("chatStore persistence", () => {
  beforeEach(() => {
    vi.resetModules();
    window.localStorage.clear();
  });

  afterEach(() => {
    window.localStorage.clear();
    vi.restoreAllMocks();
  });

  it("writes sessions and activeSessionId to localStorage under the forge-chat key", async () => {
    const { useChatStore } = await import("./chatStore");

    const sessionId = useChatStore.getState().createSession();
    const userMessage: Message = {
      id: "msg-1",
      role: "user",
      content: "hello",
      timestamp: 1,
    };
    useChatStore.getState().addMessage(sessionId, userMessage);

    const raw = window.localStorage.getItem("forge-chat");
    expect(raw).not.toBeNull();

    const parsed = JSON.parse(raw as string) as {
      state: { sessions: { id: string; messages: Message[] }[]; activeSessionId: string | null };
    };
    expect(parsed.state.activeSessionId).toBe(sessionId);
    expect(parsed.state.sessions).toHaveLength(1);
    expect(parsed.state.sessions[0]!.id).toBe(sessionId);
    expect(parsed.state.sessions[0]!.messages).toEqual([userMessage]);
  });

  it("rehydrates sessions and activeSessionId from localStorage after a simulated reload", async () => {
    const { useChatStore: firstInstance } = await import("./chatStore");

    const sessionId = firstInstance.getState().createSession();
    const assistantMessage: Message = {
      id: "msg-1",
      role: "assistant",
      content: "hi there",
      timestamp: 1,
    };
    firstInstance.getState().addMessage(sessionId, assistantMessage);

    // Simulate a browser refresh: reset the module registry so a fresh
    // store instance is created and reads back from (persisted) localStorage.
    vi.resetModules();
    const { useChatStore: secondInstance } = await import("./chatStore");

    const state = secondInstance.getState();
    expect(state.activeSessionId).toBe(sessionId);
    expect(state.sessions).toHaveLength(1);
    expect(state.sessions[0]!.id).toBe(sessionId);
    expect(state.sessions[0]!.messages).toHaveLength(1);
    expect(state.sessions[0]!.messages[0]!.content).toBe("hi there");
  });

  it("does not persist isLoading across reload (resets to false)", async () => {
    const { useChatStore: firstInstance } = await import("./chatStore");

    firstInstance.getState().setLoading(true);

    vi.resetModules();
    const { useChatStore: secondInstance } = await import("./chatStore");

    expect(secondInstance.getState().isLoading).toBe(false);
  });
});

describe("chatStore resumeSession", () => {
  beforeEach(() => {
    vi.resetModules();
    window.localStorage.clear();
  });

  afterEach(() => {
    window.localStorage.clear();
    vi.restoreAllMocks();
  });

  it("adds a local session shell for a server-only session id and makes it active", async () => {
    const { useChatStore } = await import("./chatStore");

    expect(useChatStore.getState().sessions).toHaveLength(0);

    useChatStore.getState().resumeSession("server-session-123");

    const state = useChatStore.getState();
    expect(state.activeSessionId).toBe("server-session-123");
    expect(state.sessions).toHaveLength(1);
    expect(state.sessions[0]).toEqual({ id: "server-session-123", messages: [] });
  });

  it("does not duplicate an already-local session, and just makes it active", async () => {
    const { useChatStore } = await import("./chatStore");

    const sessionId = useChatStore.getState().createSession();
    const userMessage: Message = {
      id: "msg-1",
      role: "user",
      content: "hello",
      timestamp: 1,
    };
    useChatStore.getState().addMessage(sessionId, userMessage);
    useChatStore.getState().setActiveSession("some-other-id-temporarily");

    useChatStore.getState().resumeSession(sessionId);

    const state = useChatStore.getState();
    expect(state.activeSessionId).toBe(sessionId);
    expect(state.sessions).toHaveLength(1);
    expect(state.sessions[0]!.messages).toHaveLength(1);
  });
});

describe("chatStore pendingPrompt", () => {
  beforeEach(() => {
    vi.resetModules();
    window.localStorage.clear();
  });

  afterEach(() => {
    window.localStorage.clear();
    vi.restoreAllMocks();
  });

  it("starts as null", async () => {
    const { useChatStore } = await import("./chatStore");
    expect(useChatStore.getState().pendingPrompt).toBeNull();
  });

  it("setPendingPrompt sets and clears the queued prompt", async () => {
    const { useChatStore } = await import("./chatStore");

    useChatStore.getState().setPendingPrompt("What's the weather in Tokyo?");
    expect(useChatStore.getState().pendingPrompt).toBe("What's the weather in Tokyo?");

    useChatStore.getState().setPendingPrompt(null);
    expect(useChatStore.getState().pendingPrompt).toBeNull();
  });

  it("does not persist pendingPrompt across reload (transient)", async () => {
    const { useChatStore: firstInstance } = await import("./chatStore");

    firstInstance.getState().setPendingPrompt("Define serendipity");

    vi.resetModules();
    const { useChatStore: secondInstance } = await import("./chatStore");

    expect(secondInstance.getState().pendingPrompt).toBeNull();
  });
});
