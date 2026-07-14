import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApprovalQueue } from "./ApprovalQueue";

const PENDING_SOCIAL_APPROVAL = {
  id: "appr_1",
  tool_name: "social_publish",
  arguments: { content: "Forge AI ships human-in-the-loop approvals!", channel: "twitter" },
  argument_hash: "hash1",
  requested_by: "marketing-agent",
  run_id: "run_1",
  draft_summary: null,
  created_at: new Date().toISOString(),
  status: "pending",
};

const PENDING_GENERIC_APPROVAL = {
  id: "appr_2",
  tool_name: "send_invoice",
  arguments: { amount: 500, currency: "USD", internal_note: "***REDACTED***" },
  argument_hash: "hash2",
  requested_by: "billing-agent",
  run_id: "run_2",
  draft_summary: null,
  created_at: new Date().toISOString(),
  status: "pending",
};

function renderQueue(props: { limit?: number } = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ApprovalQueue {...props} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function stubFetch({
  permissions,
  approvals,
  onDecision,
}: {
  permissions: string[];
  approvals: unknown[];
  onDecision?: (url: string) => unknown;
}) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/v1/auth/me") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({
            kind: "user",
            sub: "u1",
            groups: [],
            roles: [],
            permissions,
          }),
        });
      }
      if (url === "/v1/admin/approvals") {
        return Promise.resolve({ ok: true, status: 200, json: async () => approvals });
      }
      if (url.includes("/v1/admin/approvals/") && init?.method === "POST") {
        const result = onDecision?.(url) ?? { id: "appr_1", status: "approved" };
        return Promise.resolve({ ok: true, status: 200, json: async () => result });
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => ({}) });
    }),
  );
}

describe("ApprovalQueue", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("renders a social_publish approval as a quoted post preview with its channel", async () => {
    stubFetch({
      permissions: ["agent:approve"],
      approvals: [PENDING_SOCIAL_APPROVAL],
    });

    renderQueue();

    await waitFor(() => {
      expect(
        screen.getByText(/Forge AI ships human-in-the-loop approvals!/),
      ).toBeInTheDocument();
    });
    expect(screen.getByText("twitter")).toBeInTheDocument();
    expect(screen.getByText("marketing-agent")).toBeInTheDocument();
  });

  it("renders a non-social_publish approval as a key/value list, skipping redacted values", async () => {
    stubFetch({
      permissions: ["agent:approve"],
      approvals: [PENDING_GENERIC_APPROVAL],
    });

    renderQueue();

    await waitFor(() => {
      expect(screen.getByText("amount")).toBeInTheDocument();
    });
    expect(screen.getByText("500")).toBeInTheDocument();
    expect(screen.getByText("currency")).toBeInTheDocument();
    expect(screen.getByText("USD")).toBeInTheDocument();
    expect(screen.queryByText("internal_note")).not.toBeInTheDocument();
    expect(screen.queryByText("***REDACTED***")).not.toBeInTheDocument();
  });

  it("calls the approve mutation with the approval's id when Approve is clicked", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
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
          json: async () => [PENDING_SOCIAL_APPROVAL],
        });
      }
      if (url.includes("/approve") && init?.method === "POST") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({ id: "appr_1", status: "approved", result: { published: true } }),
        });
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => ({}) });
    });
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    renderQueue();

    const approveButton = await screen.findByRole("button", { name: /approve/i });
    await user.click(approveButton);

    await waitFor(() => {
      const approveCall = fetchMock.mock.calls.find(
        (call) => typeof call[0] === "string" && call[0].includes("/approve"),
      );
      expect(approveCall).toBeDefined();
      expect(approveCall![0]).toBe("/v1/admin/approvals/appr_1/approve");
    });
  });

  it("hides Approve/Reject actions when the caller lacks agent:approve, showing a read-only badge instead", async () => {
    stubFetch({ permissions: [], approvals: [PENDING_SOCIAL_APPROVAL] });

    renderQueue();

    await waitFor(() => {
      expect(screen.getByText("marketing-agent")).toBeInTheDocument();
    });
    expect(screen.queryByRole("button", { name: /approve/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /reject/i })).not.toBeInTheDocument();
    expect(screen.getByText("Pending")).toBeInTheDocument();
  });

  it("renders a calm all-clear empty state when there are no pending approvals", async () => {
    stubFetch({ permissions: ["agent:approve"], approvals: [] });

    renderQueue();

    await waitFor(() => {
      expect(screen.getByText(/all clear/i)).toBeInTheDocument();
    });
    expect(screen.queryByRole("button", { name: /approve/i })).not.toBeInTheDocument();
  });

  it("shows a 'view all' link when limited and there are more pending items than the limit", async () => {
    stubFetch({
      permissions: ["agent:approve"],
      approvals: [PENDING_SOCIAL_APPROVAL, PENDING_GENERIC_APPROVAL],
    });

    renderQueue({ limit: 1 });

    await waitFor(() => {
      expect(screen.getByText("marketing-agent")).toBeInTheDocument();
    });
    expect(screen.queryByText("billing-agent")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /view all 2 pending/i })).toBeInTheDocument();
  });
});
