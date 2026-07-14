import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AgentHero } from "./AgentHero";

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
              llm: { default_model: "gpt-4o", litellm: { mode: "embedded" } },
              tools: { openapi_sources: [], manual_tools: [], workflows: [] },
              agents: {
                default: "assistant",
                agents: [
                  {
                    name: "assistant",
                    description: "Helps with weather and crypto lookups.",
                    model: "gpt-4o",
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
            { name: "get_weather", description: "Gets current weather for a city" },
            { name: "get_crypto_price", description: "Gets the current price of a cryptocurrency" },
          ],
        });
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => [] });
    }),
  );
}

function renderAgentHero() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <AgentHero />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("AgentHero", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("renders the default agent's name and its plain-language one-liner", async () => {
    stubFetch();
    renderAgentHero();

    await waitFor(() => {
      expect(screen.getByText("assistant")).toBeInTheDocument();
    });
    expect(screen.getByText("Helps with weather and crypto lookups.")).toBeInTheDocument();
  });

  it("renders the model badge from config.llm.default_model", async () => {
    stubFetch();
    renderAgentHero();

    await waitFor(() => {
      expect(screen.getByText("gpt-4o")).toBeInTheDocument();
    });
  });

  it("renders a capability chip for each tool in the mocked tools list", async () => {
    stubFetch();
    renderAgentHero();

    await waitFor(() => {
      expect(screen.getByText("get_weather")).toBeInTheDocument();
    });
    expect(screen.getByText("get_crypto_price")).toBeInTheDocument();
  });

  it("shows an invitation, not an error, when the agent has no tools configured", async () => {
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
                llm: { default_model: "gpt-4o", litellm: { mode: "embedded" } },
                tools: { openapi_sources: [], manual_tools: [], workflows: [] },
                agents: { default: "assistant", agents: [], peers: [] },
              },
            }),
          });
        }
        return Promise.resolve({ ok: true, status: 200, json: async () => [] });
      }),
    );

    renderAgentHero();

    await waitFor(() => {
      expect(screen.getByText(/no tools configured/i)).toBeInTheDocument();
    });
  });
});

function stubMultiAgentConfig() {
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
              tools: { openapi_sources: [], manual_tools: [], workflows: [] },
              agents: {
                default: "assistant",
                agents: [
                  {
                    name: "researcher",
                    description: "Digs up facts on the open web.",
                    model: "gpt-4o",
                    tools: ["search_web"],
                    mode: "active",
                  },
                  {
                    name: "assistant",
                    description: "A general-purpose helper.",
                    model: "gpt-4o-mini",
                    tools: [],
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
            { name: "get_weather", description: "Gets current weather for a city" },
          ],
        });
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => [] });
    }),
  );
}

describe("AgentHero roster", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("renders a card for every configured agent", async () => {
    stubMultiAgentConfig();
    renderAgentHero();

    await waitFor(() => {
      expect(screen.getByText("researcher")).toBeInTheDocument();
    });
    expect(screen.getByText("assistant")).toBeInTheDocument();
  });

  it("marks the agent named in agents.default, and only that one", async () => {
    stubMultiAgentConfig();
    renderAgentHero();

    await waitFor(() => {
      expect(screen.getByText("assistant")).toBeInTheDocument();
    });

    const defaultBadges = screen.getAllByText(/default/i);
    expect(defaultBadges).toHaveLength(1);
  });

  it("shows a passive mode badge by default when an agent has no mode field", async () => {
    stubMultiAgentConfig();
    renderAgentHero();

    await waitFor(() => {
      expect(screen.getByText("assistant")).toBeInTheDocument();
    });

    // assistant has no `mode` field in the mocked config -- must default to passive.
    const assistantCard = screen.getByText("assistant").closest("article");
    expect(assistantCard).not.toBeNull();
    expect(within(assistantCard as HTMLElement).getByText(/passive/i)).toBeInTheDocument();
  });

  it("shows the agent's own mode badge when set (active)", async () => {
    stubMultiAgentConfig();
    renderAgentHero();

    await waitFor(() => {
      expect(screen.getByText("researcher")).toBeInTheDocument();
    });

    const researcherCard = screen.getByText("researcher").closest("article");
    expect(researcherCard).not.toBeNull();
    expect(within(researcherCard as HTMLElement).getByText(/active/i)).toBeInTheDocument();
  });

  it("scopes each agent's capabilities to its own tools[] -- researcher only sees search_web", async () => {
    stubMultiAgentConfig();
    renderAgentHero();

    await waitFor(() => {
      expect(screen.getByText("researcher")).toBeInTheDocument();
    });

    const researcherCard = screen.getByText("researcher").closest("article") as HTMLElement;
    expect(within(researcherCard).getByText("search_web")).toBeInTheDocument();
    expect(within(researcherCard).queryByText("get_weather")).not.toBeInTheDocument();
  });

  it("labels an agent with an empty tools[] as having full (all-tools) access", async () => {
    stubMultiAgentConfig();
    renderAgentHero();

    await waitFor(() => {
      expect(screen.getByText("assistant")).toBeInTheDocument();
    });

    const assistantCard = screen.getByText("assistant").closest("article") as HTMLElement;
    expect(within(assistantCard).getByText(/all tools/i)).toBeInTheDocument();
  });
});
