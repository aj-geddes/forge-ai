import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MiniChat } from "./MiniChat";
import { streamChatCompletion } from "@/api/chat";

vi.mock("@/api/chat", () => ({
  streamChatCompletion: vi.fn(),
}));

function renderMiniChat() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MiniChat />
    </QueryClientProvider>,
  );
}

/** MiniChat also queries config + tools (for the agent selector). Defaults
 * to an empty roster (no agents configured), which keeps the selector
 * hidden -- matching pre-multi-agent behavior for these tests. */
function stubConfigAndTools(agentsConfig: unknown = { agents: [] }, tools: unknown[] = []) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: string) => {
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
      return Promise.resolve({ ok: true, status: 200, json: async () => [] });
    }),
  );
}

describe("MiniChat", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("calls streamChatCompletion with the typed message and an ephemeral session id on send", async () => {
    stubConfigAndTools();
    vi.mocked(streamChatCompletion).mockImplementation(async (_params, callbacks) => {
      callbacks.onChunk?.("Hi there!");
    });

    renderMiniChat();
    const user = userEvent.setup();

    const textbox = screen.getByRole("textbox");
    await user.type(textbox, "Hello agent");
    await user.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => {
      expect(streamChatCompletion).toHaveBeenCalledTimes(1);
    });

    const [params] = vi.mocked(streamChatCompletion).mock.calls[0]!;
    expect(params.message).toBe("Hello agent");
    expect(params.sessionId).toBeTruthy();

    await waitFor(() => {
      expect(screen.getByText("Hi there!")).toBeInTheDocument();
    });
  });

  it("shows a tools-used indicator when the streamed reply includes tool calls", async () => {
    stubConfigAndTools();
    vi.mocked(streamChatCompletion).mockImplementation(async (_params, callbacks) => {
      callbacks.onToolCall?.({ name: "get_weather", arguments: { city: "SF" }, result: "sunny" });
      callbacks.onChunk?.("It's sunny in SF.");
    });

    renderMiniChat();
    const user = userEvent.setup();

    await user.type(screen.getByRole("textbox"), "What's the weather?");
    await user.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => {
      expect(screen.getByText(/get_weather/)).toBeInTheDocument();
    });
  });
});

describe("MiniChat agent selection", () => {
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

  it("shows an agent selector once a multi-agent config loads, defaulting to config.agents.default", async () => {
    stubConfigAndTools(AGENTS_CONFIG, TOOLS);
    renderMiniChat();

    const select = await screen.findByRole("combobox", { name: /agent/i });
    await waitFor(() => {
      expect(select).toHaveValue("assistant");
    });
  });

  it("passes the selected agent to streamChatCompletion", async () => {
    stubConfigAndTools(AGENTS_CONFIG, TOOLS);
    vi.mocked(streamChatCompletion).mockImplementation(async (_params, callbacks) => {
      callbacks.onChunk?.("Findings...");
    });

    renderMiniChat();
    const user = userEvent.setup();

    const select = await screen.findByRole("combobox", { name: /agent/i });
    await user.selectOptions(select, "researcher");

    await user.type(screen.getByRole("textbox"), "Look this up");
    await user.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => {
      expect(streamChatCompletion).toHaveBeenCalledTimes(1);
    });
    const [params] = vi.mocked(streamChatCompletion).mock.calls[0]!;
    expect(params.agent).toBe("researcher");
  });
});
