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
    const chatCall = fetchMock.mock.calls.find(
      (call) => !(call[0] as string).includes("/v1/admin/sessions"),
    );
    expect(chatCall).toBeDefined();
    const [, init] = chatCall as [string, RequestInit];
    const body = JSON.parse(init.body as string) as Record<string, unknown>;
    expect(body.stream).toBe(true);
  });
});
