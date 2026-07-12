import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { DashboardPage } from "./DashboardPage";

function renderDashboard() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("DashboardPage health + config contract", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("renders subsystem status from the backend's `components` map, not a nonexistent `checks` field", async () => {
    // HealthResponse (forge_gateway/models.py) is {status, version, components:
    // dict[str,str]} -- there is no `checks` field and no `uptime` field.
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url === "/health/ready") {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: async () => ({
              status: "ready",
              version: "1.0.0",
              components: { agent: "ok", litellm: "unavailable" },
            }),
          });
        }
        if (url === "/v1/admin/config") {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: async () => ({
              path: "forge.yaml",
              config: {
                metadata: { name: "forge", version: "0.1.0", environment: "development" },
                llm: { default_model: "gpt-4o", litellm: { mode: "embedded" } },
                tools: { openapi_sources: [], manual_tools: [], workflows: [] },
                agents: {
                  default: "assistant",
                  agents: [],
                  peers: [{ name: "p1", endpoint: "https://p1", trust_level: "high" }],
                },
              },
            }),
          });
        }
        return Promise.resolve({ ok: true, status: 200, json: async () => [] });
      }),
    );

    renderDashboard();

    await waitFor(() => {
      expect(screen.getByText("agent")).toBeInTheDocument();
    });
    expect(screen.getAllByText("ok").length).toBeGreaterThan(0);
    expect(screen.getByText("litellm")).toBeInTheDocument();
    expect(screen.getByText("unavailable")).toBeInTheDocument();

    // The peer count in the System Information card should come from
    // config.agents.peers, not a nonexistent top-level config.peers.
    await waitFor(() => {
      const peerBadges = screen.getAllByText("1");
      expect(peerBadges.length).toBeGreaterThan(0);
    });
  });
});
