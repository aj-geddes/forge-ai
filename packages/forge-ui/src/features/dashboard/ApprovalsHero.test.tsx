import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApprovalsHero } from "./ApprovalsHero";

function renderHero() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ApprovalsHero />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function stubFetch({ permissions, approvals }: { permissions: string[]; approvals: unknown[] }) {
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

describe("ApprovalsHero", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("shows the pending count badge and attention framing when approvals are pending", async () => {
    stubFetch({
      permissions: ["agent:approve"],
      approvals: [
        {
          id: "appr_1",
          tool_name: "social_publish",
          arguments: { content: "hi", channel: "twitter" },
          argument_hash: "h1",
          requested_by: "marketing-agent",
          run_id: "r1",
          draft_summary: null,
          created_at: new Date().toISOString(),
          status: "pending",
        },
      ],
    });

    renderHero();

    await waitFor(() => {
      expect(screen.getByText("marketing-agent")).toBeInTheDocument();
    });
    expect(screen.getByLabelText("1 pending")).toBeInTheDocument();
    expect(screen.getByText(/needs your approval/i)).toBeInTheDocument();
  });

  it("shows the calm all-clear line with no attention badge when there are no pending approvals", async () => {
    stubFetch({ permissions: ["agent:approve"], approvals: [] });

    renderHero();

    await waitFor(() => {
      expect(screen.getByText(/all clear/i)).toBeInTheDocument();
    });
    expect(screen.queryByLabelText(/pending/i)).not.toBeInTheDocument();
  });
});
