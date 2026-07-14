import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Sidebar } from "./Sidebar";

function renderSidebar() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function stubFetch({
  permissions,
  approvals,
}: {
  permissions: string[];
  approvals: unknown[];
}) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: string) => {
      if (url === "/v1/auth/me") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({ kind: "user", sub: "u1", groups: [], roles: [], permissions }),
        });
      }
      if (url === "/v1/admin/approvals") {
        return Promise.resolve({ ok: true, status: 200, json: async () => approvals });
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => ({}) });
    }),
  );
}

describe("Sidebar", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("shows an Approvals nav link for a caller with config:read", async () => {
    stubFetch({ permissions: ["config:read"], approvals: [] });

    renderSidebar();

    await waitFor(() => {
      expect(screen.getByRole("link", { name: /approvals/i })).toBeInTheDocument();
    });
  });

  it("shows a live pending-count badge on the Approvals link when approvals are pending", async () => {
    stubFetch({
      permissions: ["config:read"],
      approvals: [
        {
          id: "appr_1",
          tool_name: "social_publish",
          arguments: {},
          argument_hash: "h1",
          requested_by: "agent-a",
          run_id: "r1",
          draft_summary: null,
          created_at: new Date().toISOString(),
          status: "pending",
        },
        {
          id: "appr_2",
          tool_name: "social_publish",
          arguments: {},
          argument_hash: "h2",
          requested_by: "agent-b",
          run_id: "r2",
          draft_summary: null,
          created_at: new Date().toISOString(),
          status: "pending",
        },
      ],
    });

    renderSidebar();

    await waitFor(() => {
      expect(screen.getByLabelText(/2 pending approvals/i)).toBeInTheDocument();
    });
  });

  it("shows no badge when there are no pending approvals", async () => {
    stubFetch({ permissions: ["config:read"], approvals: [] });

    renderSidebar();

    await waitFor(() => {
      expect(screen.getByRole("link", { name: /approvals/i })).toBeInTheDocument();
    });
    expect(screen.queryByLabelText(/pending approval/i)).not.toBeInTheDocument();
  });
});
