import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PeersPage } from "./PeersPage";

function renderPeersPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <PeersPage />
    </QueryClientProvider>,
  );
}

describe("PeersPage ping status", () => {
  beforeEach(() => {
    window.HTMLDialogElement.prototype.showModal = vi.fn(function (
      this: HTMLDialogElement,
    ) {
      this.open = true;
    });
    window.HTMLDialogElement.prototype.close = vi.fn(function (
      this: HTMLDialogElement,
    ) {
      this.open = false;
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("shows a peer as reachable when the backend returns status: 'reachable' (AdminPeerStatus), not 'ok'", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, options?: RequestInit) => {
        if (options?.method === "POST" && url.includes("/ping")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: async () => ({ name: "peer-a", status: "reachable", http_status: 200 }),
          });
        }
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => [
            {
              name: "peer-a",
              endpoint: "https://peer-a.example.com",
              trust_level: "high",
              capabilities: ["search"],
              status: "unknown",
            },
          ],
        });
      }),
    );

    const user = userEvent.setup();
    renderPeersPage();

    await waitFor(() => expect(screen.getByText("peer-a")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: /ping/i }));

    await waitFor(() => {
      expect(screen.queryByText(/unreachable/i)).not.toBeInTheDocument();
    });
    expect(screen.queryByText(/status unknown/i)).not.toBeInTheDocument();
  });

  it("disables the Add Peer form and explains there is no peer-creation endpoint yet", async () => {
    // forge_gateway/routes/admin.py only exposes GET /v1/admin/peers and
    // POST /v1/admin/peers/{name}/ping -- there is no POST /v1/admin/peers.
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => [],
      }),
    );

    const user = userEvent.setup();
    renderPeersPage();

    await waitFor(() => expect(screen.getByText(/no peers configured/i)).toBeInTheDocument());

    const addPeerButtons = screen.getAllByRole("button", { name: /add peer/i });
    await user.click(addPeerButtons[0]!);

    expect(
      screen.getByText(/does not yet expose an api to create peers/i),
    ).toBeInTheDocument();

    const submitButtons = screen.getAllByRole("button", { name: "Add Peer" });
    const submitButton = submitButtons[submitButtons.length - 1];
    expect(submitButton).toBeDisabled();
  });
});
