import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import type * as ReactRouterDom from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AgentStrip } from "./AgentStrip";
import { useChatStore } from "@/stores/chatStore";

const navigateMock = vi.fn();

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof ReactRouterDom>("react-router-dom");
  return { ...actual, useNavigate: () => navigateMock };
});

function stubFetch() {
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
              agents: {
                default: "assistant",
                agents: [
                  { name: "assistant", description: "General helper", model: "gpt-4o" },
                  {
                    name: "researcher",
                    description: "Digs up facts",
                    model: "gpt-4o",
                    tools: ["search_web"],
                    mode: "active",
                  },
                ],
                peers: [],
              },
            },
          }),
        });
      }
      if (url.includes("/v1/admin/tools")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => [
            { name: "search_web", description: "Searches the web" },
            { name: "get_weather", description: "Gets weather" },
          ],
        });
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => [] });
    }),
  );
}

function renderStrip() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <AgentStrip />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("AgentStrip", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    navigateMock.mockClear();
  });

  it("renders a compact row per configured agent with its mode and scoped tool count", async () => {
    stubFetch();
    renderStrip();

    await waitFor(() => {
      expect(screen.getByText("researcher")).toBeInTheDocument();
    });
    expect(screen.getByText("assistant")).toBeInTheDocument();
    expect(screen.getByText("1 tool")).toBeInTheDocument(); // researcher: search_web only
    expect(screen.getByText("2 tools")).toBeInTheDocument(); // assistant: full access = all tools
  });

  it("queues the clicked agent via chatStore.pendingAgent and navigates to Chat", async () => {
    stubFetch();
    const user = userEvent.setup();
    renderStrip();

    const researcherRow = await screen.findByText("researcher");
    await user.click(researcherRow);

    expect(useChatStore.getState().pendingAgent).toBe("researcher");
    expect(navigateMock).toHaveBeenCalledWith("/chat");
  });
});
