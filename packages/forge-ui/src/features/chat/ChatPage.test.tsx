import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ChatPage } from "./ChatPage";
import { useChatStore } from "@/stores/chatStore";

function renderChatPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ChatPage />
    </QueryClientProvider>,
  );
}

/** Build a mocked `fetch` Response streaming the given raw SSE data lines
 * (each already formatted as `data: {...}` or `data: [DONE]`), matching the
 * gateway's `text/event-stream` contract (docs/developer/api-reference.md). */
function sseResponse(rawDataLines: string[]): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const line of rawDataLines) {
        controller.enqueue(encoder.encode(`data: ${line}\n\n`));
      }
      controller.close();
    },
  });
  return {
    ok: true,
    status: 200,
    body,
    json: async () => ({}),
  } as unknown as Response;
}

function stubFetchForSessionsAndChat(chatEvents: string[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: string) => {
      if (url.includes("/v1/admin/sessions")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => [],
        });
      }
      // The Agent selector queries config + tools too (useConfig/useTools);
      // these tests don't configure multiple agents, so a minimal,
      // single-agent-free config is enough to keep the selector hidden
      // (see AgentSelector -- it renders nothing for an empty roster).
      if (url.includes("/v1/admin/config")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({
            path: "forge.yaml",
            config: {
              metadata: { name: "forge", version: "0.1.0" },
              llm: { default_model: "gpt-4o" },
              tools: {},
              agents: { agents: [] },
            },
          }),
        });
      }
      if (url.includes("/v1/admin/tools")) {
        return Promise.resolve({ ok: true, status: 200, json: async () => [] });
      }
      return Promise.resolve(sseResponse(chatEvents));
    }),
  );
}

function stubFetchSessionsConfigToolsAndChat({
  chatEvents,
  agentsConfig,
  tools,
}: {
  chatEvents: string[];
  agentsConfig: unknown;
  tools: unknown[];
}) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: string) => {
      if (url.includes("/v1/admin/sessions")) {
        return Promise.resolve({ ok: true, status: 200, json: async () => [] });
      }
      if (url.includes("/v1/admin/config")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({
            path: "forge.yaml",
            config: {
              metadata: { name: "forge", version: "0.1.0" },
              llm: { default_model: "gpt-4o" },
              tools: {},
              agents: agentsConfig,
            },
          }),
        });
      }
      if (url.includes("/v1/admin/tools")) {
        return Promise.resolve({ ok: true, status: 200, json: async () => tools });
      }
      return Promise.resolve(sseResponse(chatEvents));
    }),
  );
}

describe("ChatPage", () => {
  beforeEach(() => {
    window.HTMLElement.prototype.scrollIntoView = vi.fn();
    useChatStore.setState({
      sessions: [],
      activeSessionId: null,
      isLoading: false,
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("renders the agent's reply streamed via SSE `chunk` frames, concatenated in order", async () => {
    // The gateway streams incremental deltas as `data: {"chunk": "...", "session_id": "..."}`
    // frames (docs/developer/api-reference.md), terminated by `data: [DONE]`.
    stubFetchForSessionsAndChat([
      JSON.stringify({ chunk: "Hello ", session_id: "session-1" }),
      JSON.stringify({ chunk: "from the agent", session_id: "session-1" }),
      "[DONE]",
    ]);

    const user = userEvent.setup();
    renderChatPage();

    await user.click(screen.getByRole("button", { name: /new session/i }));

    const textbox = screen.getByPlaceholderText(/ask the agent anything/i);
    await user.type(textbox, "Hi there{Enter}");

    await waitFor(() => {
      expect(screen.getByText("Hello from the agent")).toBeInTheDocument();
    });
  });

  it("renders tool calls inline with their arguments and result", async () => {
    stubFetchForSessionsAndChat([
      JSON.stringify({
        tool_call: {
          name: "get_weather",
          arguments: { city: "SF" },
          result: "sunny in SF",
        },
        session_id: "session-1",
      }),
      JSON.stringify({ chunk: "It's sunny in SF.", session_id: "session-1" }),
      "[DONE]",
    ]);

    const user = userEvent.setup();
    renderChatPage();

    await user.click(screen.getByRole("button", { name: /new session/i }));

    const textbox = screen.getByPlaceholderText(/ask the agent anything/i);
    await user.type(textbox, "weather in SF?{Enter}");

    await waitFor(() => {
      expect(screen.getByText("It's sunny in SF.")).toBeInTheDocument();
    });

    // Tool call details are collapsed by default -- expand them.
    await user.click(screen.getByRole("button", { name: /get_weather/i }));

    expect(screen.getByText(/"city"/)).toBeInTheDocument();
    expect(screen.getByText(/"SF"/)).toBeInTheDocument();
    expect(screen.getByText(/"sunny in SF"/)).toBeInTheDocument();
  });

  it("sends the request with stream: true", async () => {
    stubFetchForSessionsAndChat([
      JSON.stringify({ chunk: "hi", session_id: "session-1" }),
      "[DONE]",
    ]);

    const user = userEvent.setup();
    renderChatPage();

    await user.click(screen.getByRole("button", { name: /new session/i }));
    const textbox = screen.getByPlaceholderText(/ask the agent anything/i);
    await user.type(textbox, "Hi there{Enter}");

    await waitFor(() => {
      expect(screen.getByText("hi")).toBeInTheDocument();
    });

    const fetchMock = vi.mocked(fetch);
    const chatCall = fetchMock.mock.calls.find((call) => call[0] === "/v1/chat/completions");
    expect(chatCall).toBeDefined();
    const [, init] = chatCall as [string, RequestInit];
    const body = JSON.parse(init.body as string) as Record<string, unknown>;
    expect(body.stream).toBe(true);
  });

  it("clicking a server-only session resumes it locally and makes it the active, clickable session", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.includes("/v1/admin/sessions")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: async () => [
              { session_id: "server-sess-abc123", message_count: 3 },
            ],
          });
        }
        return Promise.resolve({ ok: false, status: 404 });
      }),
    );

    const user = userEvent.setup();
    renderChatPage();

    const serverSessionButton = await screen.findByRole("button", {
      name: /server-sess-abc123/i,
    });
    await user.click(serverSessionButton);

    await waitFor(() => {
      const state = useChatStore.getState();
      expect(state.activeSessionId).toBe("server-sess-abc123");
      expect(state.sessions).toEqual([
        { id: "server-sess-abc123", messages: [] },
      ]);
    });

    expect(screen.queryByText(/no session selected/i)).not.toBeInTheDocument();
  });

  it("shows a (no response) placeholder for an assistant message that finished with empty content", async () => {
    stubFetchForSessionsAndChat([]);

    useChatStore.setState({
      sessions: [
        {
          id: "session-empty",
          messages: [
            { id: "msg-1", role: "user", content: "Hi", timestamp: 1 },
            { id: "msg-2", role: "assistant", content: "", timestamp: 2 },
          ],
        },
      ],
      activeSessionId: "session-empty",
      isLoading: false,
    });

    renderChatPage();

    expect(await screen.findByText(/\(no response\)/i)).toBeInTheDocument();

    useChatStore.setState({
      sessions: [
        {
          id: "session-empty",
          messages: [
            { id: "msg-1", role: "user", content: "Hi", timestamp: 1 },
            { id: "msg-2", role: "assistant", content: "", timestamp: 2 },
          ],
        },
        {
          id: "session-full",
          messages: [
            { id: "msg-3", role: "user", content: "Hi", timestamp: 3 },
            {
              id: "msg-4",
              role: "assistant",
              content: "Actual reply",
              timestamp: 4,
            },
          ],
        },
      ],
      activeSessionId: "session-full",
      isLoading: false,
    });

    await waitFor(() => {
      expect(screen.getByText("Actual reply")).toBeInTheDocument();
    });
    expect(screen.queryByText(/\(no response\)/i)).not.toBeInTheDocument();
  });
});

describe("ChatPage agent selection", () => {
  beforeEach(() => {
    window.HTMLElement.prototype.scrollIntoView = vi.fn();
    useChatStore.setState({
      sessions: [],
      activeSessionId: null,
      isLoading: false,
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  const AGENTS_CONFIG = {
    default: "assistant",
    agents: [
      { name: "assistant", description: "General helper" },
      { name: "researcher", description: "Digs up facts", tools: ["search_web"] },
    ],
  };
  const TOOLS = [{ name: "search_web", description: "Searches the web" }];

  it("passes the selected agent through to the chat request after choosing it from the selector", async () => {
    stubFetchSessionsConfigToolsAndChat({
      chatEvents: [JSON.stringify({ chunk: "hi", session_id: "session-1" }), "[DONE]"],
      agentsConfig: AGENTS_CONFIG,
      tools: TOOLS,
    });

    const user = userEvent.setup();
    renderChatPage();

    await user.click(screen.getByRole("button", { name: /new session/i }));

    const select = await screen.findByRole("combobox", { name: /agent/i });
    await user.selectOptions(select, "researcher");

    const textbox = screen.getByPlaceholderText(/ask the agent anything/i);
    await user.type(textbox, "Hi there{Enter}");

    await waitFor(() => {
      expect(screen.getByText("hi")).toBeInTheDocument();
    });

    const fetchMock = vi.mocked(fetch);
    const chatCall = fetchMock.mock.calls.find((call) => call[0] === "/v1/chat/completions");
    expect(chatCall).toBeDefined();
    const [, init] = chatCall as [string, RequestInit];
    const body = JSON.parse(init.body as string) as Record<string, unknown>;
    expect(body.agent).toBe("researcher");
  });

  it("persists the session's agent selection in chatStore", async () => {
    stubFetchSessionsConfigToolsAndChat({
      chatEvents: ["[DONE]"],
      agentsConfig: AGENTS_CONFIG,
      tools: TOOLS,
    });

    const user = userEvent.setup();
    renderChatPage();

    await user.click(screen.getByRole("button", { name: /new session/i }));

    const select = await screen.findByRole("combobox", { name: /agent/i });
    await user.selectOptions(select, "researcher");

    await waitFor(() => {
      const state = useChatStore.getState();
      const active = state.sessions.find((s) => s.id === state.activeSessionId);
      expect(active?.agent).toBe("researcher");
    });
  });

  it("defaults the selector to config.agents.default without requiring a choice", async () => {
    stubFetchSessionsConfigToolsAndChat({
      chatEvents: ["[DONE]"],
      agentsConfig: AGENTS_CONFIG,
      tools: TOOLS,
    });

    const user = userEvent.setup();
    renderChatPage();

    await user.click(screen.getByRole("button", { name: /new session/i }));

    const select = await screen.findByRole("combobox", { name: /agent/i });
    await waitFor(() => {
      expect(select).toHaveValue("assistant");
    });
  });
});
