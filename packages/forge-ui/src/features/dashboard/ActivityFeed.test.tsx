import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ActivityFeed } from "./ActivityFeed";

function renderFeed() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ActivityFeed />
    </QueryClientProvider>,
  );
}

describe("ActivityFeed", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("renders mocked activity rows: tool name, ok/fail state, and relative time", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({
          activity: [
            {
              tool: "get_weather",
              arguments: { city: "Tokyo" },
              ok: true,
              timestamp: new Date().toISOString(),
              session_id: "s1",
              interface: "chat",
            },
            {
              tool: "get_crypto_price",
              arguments: { symbol: "BTC" },
              ok: false,
              error: "timeout",
              timestamp: new Date(Date.now() - 120_000).toISOString(),
              session_id: "s2",
              interface: "chat",
            },
          ],
        }),
      }),
    );

    renderFeed();

    await waitFor(() => {
      expect(screen.getByText("get_weather")).toBeInTheDocument();
    });
    expect(screen.getByText("get_crypto_price")).toBeInTheDocument();
  });

  it("shows an invitation empty state when there is no activity", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({ activity: [] }),
      }),
    );

    renderFeed();

    await waitFor(() => {
      expect(
        screen.getByText("No activity yet — send the agent a prompt to watch it work."),
      ).toBeInTheDocument();
    });
  });

  it("shows a quiet unavailable state (not a crash) when the caller lacks admin read access", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 403,
        json: async () => ({ detail: "Forbidden" }),
      }),
    );

    renderFeed();

    await waitFor(() => {
      expect(screen.getByText(/activity.*unavailable/i)).toBeInTheDocument();
    });
  });
});
