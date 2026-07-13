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

/** Routes a stubbed fetch by method + path, matching this test file's needs. */
function stubFetch(handlers: {
  listPeers?: () => unknown[];
  createPeer?: (body: Record<string, unknown>) => { status: number; body: unknown };
  ping?: (name: string) => { status: number; body: unknown };
}) {
  const fetchMock = vi.fn().mockImplementation((url: string, options?: RequestInit) => {
    const method = (options?.method ?? "GET").toUpperCase();

    if (url === "/v1/admin/peers" && method === "GET") {
      const body = handlers.listPeers ? handlers.listPeers() : [];
      return Promise.resolve({ ok: true, status: 200, json: async () => body });
    }

    if (url === "/v1/admin/peers" && method === "POST") {
      const parsedBody = JSON.parse(options?.body as string) as Record<string, unknown>;
      const { status, body } = handlers.createPeer
        ? handlers.createPeer(parsedBody)
        : { status: 201, body: parsedBody };
      return Promise.resolve({
        ok: status >= 200 && status < 300,
        status,
        json: async () => body,
      });
    }

    if (url.startsWith("/v1/admin/peers/") && url.endsWith("/ping") && method === "POST") {
      const name = url.replace("/v1/admin/peers/", "").replace("/ping", "");
      const { status, body } = handlers.ping
        ? handlers.ping(name)
        : { status: 200, body: { name, status: "reachable" } };
      return Promise.resolve({
        ok: status >= 200 && status < 300,
        status,
        json: async () => body,
      });
    }

    throw new Error(`Unhandled fetch: ${method} ${url}`);
  });

  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
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
    stubFetch({
      listPeers: () => [
        {
          name: "peer-a",
          endpoint: "https://peer-a.example.com",
          trust_level: "high",
          capabilities: ["search"],
          status: "unknown",
        },
      ],
      ping: (name) => ({ status: 200, body: { name, status: "reachable", http_status: 200 } }),
    });

    const user = userEvent.setup();
    renderPeersPage();

    await waitFor(() => expect(screen.getByText("peer-a")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: /ping/i }));

    await waitFor(() => {
      expect(screen.queryByText(/unreachable/i)).not.toBeInTheDocument();
    });
    expect(screen.queryByText(/status unknown/i)).not.toBeInTheDocument();
  });

  it("renders the round-trip latency_ms the backend reports for a reachable ping", async () => {
    stubFetch({
      listPeers: () => [
        {
          name: "peer-a",
          endpoint: "https://peer-a.example.com",
          trust_level: "high",
          capabilities: ["search"],
          status: "unknown",
        },
      ],
      ping: (name) => ({
        status: 200,
        body: { name, status: "reachable", http_status: 200, latency_ms: 42.5 },
      }),
    });

    const user = userEvent.setup();
    renderPeersPage();

    await waitFor(() => expect(screen.getByText("peer-a")).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: /ping/i }));

    await waitFor(() => {
      expect(screen.getByText(/42\.5\s*ms/i)).toBeInTheDocument();
    });
  });

  it("does not render a latency figure when the peer is unreachable", async () => {
    stubFetch({
      listPeers: () => [
        {
          name: "peer-a",
          endpoint: "https://peer-a.example.com",
          trust_level: "high",
          capabilities: [],
          status: "unknown",
        },
      ],
      ping: (name) => ({
        status: 200,
        body: { name, status: "unreachable", error: "Connection refused" },
      }),
    });

    const user = userEvent.setup();
    renderPeersPage();

    await waitFor(() => expect(screen.getByText("peer-a")).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: /ping/i }));

    await waitFor(() => {
      expect(screen.getByText(/unreachable/i)).toBeInTheDocument();
    });
    expect(screen.queryByText(/ms/i)).not.toBeInTheDocument();
  });
});

describe("PeersPage Add Peer dialog (POST /v1/admin/peers)", () => {
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

  it("submits the form to POST /v1/admin/peers with credentials and refreshes the peer list on success", async () => {
    let peers: unknown[] = [];
    const fetchMock = stubFetch({
      listPeers: () => peers,
      createPeer: (body) => {
        const created = {
          name: body.name,
          endpoint: body.endpoint,
          trust_level: body.trust_level ?? "medium",
          capabilities: body.capabilities ?? [],
          spiffe_id: body.spiffe_id ?? null,
          status: "unknown",
        };
        peers = [...peers, created];
        return { status: 201, body: created };
      },
    });

    const user = userEvent.setup();
    renderPeersPage();

    await waitFor(() => expect(screen.getByText(/no peers configured/i)).toBeInTheDocument());

    const addPeerButtons = screen.getAllByRole("button", { name: /add peer/i });
    await user.click(addPeerButtons[0]!);

    await user.type(screen.getByLabelText(/^name$/i), "data-forge");
    await user.type(screen.getByLabelText(/endpoint url/i), "https://data-forge.example.com");
    await user.type(
      screen.getByLabelText(/spiffe/i),
      "spiffe://forge.local/peer/data-forge",
    );

    const submitButtons = screen.getAllByRole("button", { name: "Add Peer" });
    const submitButton = submitButtons[submitButtons.length - 1]!;
    expect(submitButton).not.toBeDisabled();
    await user.click(submitButton);

    await waitFor(() => {
      const postCall = fetchMock.mock.calls.find(
        (call) =>
          call[0] === "/v1/admin/peers" &&
          (call[1] as RequestInit | undefined)?.method === "POST",
      );
      expect(postCall).toBeDefined();
    });

    const postCall = fetchMock.mock.calls.find(
      (call) =>
        call[0] === "/v1/admin/peers" &&
        (call[1] as RequestInit | undefined)?.method === "POST",
    )!;
    const [, init] = postCall as [string, RequestInit];
    expect(init.credentials).toBe("include");
    const body = JSON.parse(init.body as string) as Record<string, unknown>;
    expect(body.name).toBe("data-forge");
    expect(body.endpoint).toBe("https://data-forge.example.com");
    expect(body.spiffe_id).toBe("spiffe://forge.local/peer/data-forge");

    // The peer list refreshes and shows the newly created peer.
    await waitFor(() => expect(screen.getByText("data-forge")).toBeInTheDocument());
  });

  it("surfaces the backend's error cleanly (e.g. missing spiffe_id pinning) instead of crashing", async () => {
    stubFetch({
      listPeers: () => [],
      createPeer: () => ({
        status: 400,
        body: {
          detail:
            "security.agentweave.enabled is true, but the following agents.peers have " +
            "no spiffe_id pinned: unpinned-peer.",
        },
      }),
    });

    const user = userEvent.setup();
    renderPeersPage();

    await waitFor(() => expect(screen.getByText(/no peers configured/i)).toBeInTheDocument());

    const addPeerButtons = screen.getAllByRole("button", { name: /add peer/i });
    await user.click(addPeerButtons[0]!);

    await user.type(screen.getByLabelText(/^name$/i), "unpinned-peer");
    await user.type(screen.getByLabelText(/endpoint url/i), "https://unpinned.example.com");

    const submitButtons = screen.getAllByRole("button", { name: "Add Peer" });
    await user.click(submitButtons[submitButtons.length - 1]!);

    await waitFor(() => {
      expect(screen.getByText(/no spiffe_id pinned/i)).toBeInTheDocument();
    });

    // The dialog must not crash the page -- the rest of the UI is still there.
    expect(screen.getByText(/no peers configured/i)).toBeInTheDocument();
  });
});
