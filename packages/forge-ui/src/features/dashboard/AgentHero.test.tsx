import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
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
