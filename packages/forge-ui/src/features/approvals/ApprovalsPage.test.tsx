import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApprovalsPage } from "./ApprovalsPage";

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ApprovalsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function stubFetch(approvals: unknown[], permissions: string[] = ["agent:approve"]) {
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

describe("ApprovalsPage", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("shows pending approvals in the Pending section and decided ones in Recently decided", async () => {
    stubFetch([
      {
        id: "appr_1",
        tool_name: "social_publish",
        arguments: { content: "hello world", channel: "twitter" },
        argument_hash: "h1",
        requested_by: "marketing-agent",
        run_id: "run_1",
        draft_summary: null,
        created_at: new Date().toISOString(),
        status: "pending",
      },
      {
        id: "appr_2",
        tool_name: "send_invoice",
        arguments: { amount: 10 },
        argument_hash: "h2",
        requested_by: "billing-agent",
        run_id: "run_2",
        draft_summary: null,
        created_at: new Date().toISOString(),
        status: "approved",
      },
      {
        id: "appr_3",
        tool_name: "delete_record",
        arguments: { id: "x" },
        argument_hash: "h3",
        requested_by: "cleanup-agent",
        run_id: "run_3",
        draft_summary: null,
        created_at: new Date().toISOString(),
        status: "rejected",
      },
    ]);

    renderPage();

    await waitFor(() => {
      expect(screen.getByText("marketing-agent")).toBeInTheDocument();
    });

    expect(screen.getByText("billing-agent")).toBeInTheDocument();
    expect(screen.getByText("approved")).toBeInTheDocument();
    expect(screen.getByText("cleanup-agent")).toBeInTheDocument();
    expect(screen.getByText("rejected")).toBeInTheDocument();
  });

  it("renders the all-clear empty state and 'No decisions yet' when there is no history", async () => {
    stubFetch([]);

    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/all clear/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/no decisions yet/i)).toBeInTheDocument();
  });
});
