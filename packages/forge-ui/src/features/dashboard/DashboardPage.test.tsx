import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
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

  it("treats a 'ready' subsystem status as healthy (green), not as an error (red)", async () => {
    // The live /health/ready payload reports individual components as
    // "ready" (not just "ok"/"healthy") once they've passed readiness --
    // e.g. {"components": {"agent": "ready"}}. The Subsystems card must
    // classify that as a PASS. A genuinely unhealthy status must still
    // render as an error.
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
              components: { agent: "ready", cache: "unhealthy" },
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
                agents: { default: "assistant", agents: [], peers: [] },
              },
            }),
          });
        }
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => [],
        });
      }),
    );

    renderDashboard();

    await waitFor(() => {
      expect(screen.getByText("agent")).toBeInTheDocument();
    });

    const getSubsystemRow = (name: string): HTMLElement => {
      const label = screen.getByText(name);
      const row = label.closest("div.flex.items-center.justify-between");
      if (!row) throw new Error(`Row for subsystem "${name}" not found`);
      return row as HTMLElement;
    };

    const agentRow = getSubsystemRow("agent");
    const agentBadge = within(agentRow).getByText("ready");
    expect(agentBadge.className).not.toContain("bg-destructive");
    expect(agentRow.querySelector("svg.text-destructive")).not.toBeInTheDocument();

    const cacheRow = getSubsystemRow("cache");
    const cacheBadge = within(cacheRow).getByText("unhealthy");
    expect(cacheBadge.className).toContain("bg-destructive");
    expect(cacheRow.querySelector("svg.text-destructive")).toBeInTheDocument();
  });
});

describe("DashboardPage approvals hero", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("leads with the pending approvals queue as the hero, above Live activity", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url === "/v1/auth/me") {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: async () => ({
              kind: "user",
              sub: "u1",
              groups: [],
              roles: [],
              permissions: ["agent:approve"],
            }),
          });
        }
        if (url === "/v1/admin/approvals") {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: async () => [
              {
                id: "appr_1",
                tool_name: "social_publish",
                arguments: { content: "Big launch today!", channel: "twitter" },
                argument_hash: "h1",
                requested_by: "marketing-agent",
                run_id: "r1",
                draft_summary: null,
                created_at: new Date().toISOString(),
                status: "pending",
              },
            ],
          });
        }
        if (url === "/health/ready") {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: async () => ({ status: "ready", version: "1.0.0", components: {} }),
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
                agents: { default: "assistant", agents: [], peers: [] },
              },
            }),
          });
        }
        return Promise.resolve({ ok: true, status: 200, json: async () => [] });
      }),
    );

    renderDashboard();

    expect(screen.getByRole("heading", { level: 2, name: /needs your approval/i })).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText(/Big launch today!/)).toBeInTheDocument();
    });
    expect(screen.getByText("marketing-agent")).toBeInTheDocument();

    const headings = screen.getAllByRole("heading", { level: 2 });
    const headingTexts = headings.map((h) => h.textContent?.toLowerCase() ?? "");
    const approvalHeadingIndex = headingTexts.findIndex((t) => t.includes("needs your approval"));
    const activityHeadingIndex = headingTexts.findIndex((t) => t.includes("live activity"));
    expect(approvalHeadingIndex).toBeGreaterThanOrEqual(0);
    expect(activityHeadingIndex).toBeGreaterThan(approvalHeadingIndex);
  });
});
